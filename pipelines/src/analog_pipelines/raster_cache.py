"""Disk-backed raster/artifact cache with versioning, locking, and resource budgets.

This module provides reusable infrastructure for pipeline stages that fetch, stage, or
materialize raster-derived artifacts. It is intentionally storage/index focused and does
not create new observations: cached bytes are copies of upstream artifacts and metadata,
never a substitute for measured data and never zero-filled.

Key capabilities
----------------
- Configurable cache directory with sensible integration with ``analog_pipelines.paths``.
- Versioned artifact storage and retrieval with rich metadata (checksum, version,
  license, CRS, nodata, resolution, and caller-defined fields).
- Atomic writes for artifacts, cache index/state, and checkpoint state.
- Cross-process file locking (``fcntl`` on Linux) plus in-process thread locks.
- RAM/IO/download/heavy-job concurrency budgets controlled via semaphore primitives.
- Deterministic eviction policy (LRU by default) with optional size/entry limits.
- Safe checkpoint/resume persistence for partial/chunk progress.

The implementation uses the Python standard library and existing project utilities. It is
designed to be practical and robust for typical Linux pipeline usage.
"""

from __future__ import annotations

import contextlib
import errno
import hashlib
import json
import os
import shutil
import tempfile
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, BinaryIO

try:  # POSIX process locking; available on Linux where pipelines run.
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX fallback
    fcntl = None  # type: ignore[assignment]

try:
    from analog_pipelines.paths import cache_dir as _default_cache_dir
    from analog_pipelines.paths import data_dir as _default_data_dir
except Exception:  # pragma: no cover - keep module usable in isolated contexts
    _default_cache_dir = None
    _default_data_dir = None

CACHE_INDEX_VERSION = 1
CHECKPOINT_VERSION = 1
DEFAULT_HEAVY_JOBS = 3
MIN_HEAVY_JOBS = 2
MAX_DEFAULT_HEAVY_JOBS = 4


class RasterCacheError(RuntimeError):
    """Base error for raster cache operations."""


class CacheLockError(RasterCacheError):
    """Raised when a cache lock cannot be acquired."""


class CacheEntryNotFound(RasterCacheError):
    """Raised when a requested cache entry does not exist."""


class CacheCorruptionError(RasterCacheError):
    """Raised when cached bytes or metadata fail integrity checks."""


class CheckpointNotFound(RasterCacheError):
    """Raised when checkpoint state for a key is missing."""


@dataclass(frozen=True, slots=True)
class CacheEntry:
    """Metadata and location for a versioned cached artifact.

    ``checksum`` is the hex digest of the stored artifact bytes (SHA-256 by default).
    ``version`` identifies the artifact/data revision. Scientific/source metadata such as
    ``license``, ``crs``, ``nodata``, and ``resolution`` are recorded for provenance and
    safe reuse but are not observations themselves.
    """

    key: str
    version: str
    path: str
    size_bytes: int
    checksum: str
    checksum_algorithm: str = "sha256"
    license: str | None = None
    crs: str | None = None
    nodata: float | int | None = None
    resolution: float | tuple[float, float] | None = None
    content_type: str | None = None
    created_at: float = 0.0
    last_access_at: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> CacheEntry:
        return cls(
            key=str(data["key"]),
            version=str(data.get("version", "")),
            path=str(data["path"]),
            size_bytes=int(data.get("size_bytes", 0)),
            checksum=str(data.get("checksum", "")),
            checksum_algorithm=str(data.get("checksum_algorithm", "sha256")),
            license=data.get("license"),
            crs=data.get("crs"),
            nodata=data.get("nodata"),
            resolution=_coerce_resolution(data.get("resolution")),
            content_type=data.get("content_type"),
            created_at=float(data.get("created_at", 0.0)),
            last_access_at=float(data.get("last_access_at", data.get("created_at", 0.0))),
            metadata=dict(data.get("metadata") or {}),
        )


def _coerce_resolution(value: Any) -> float | tuple[float, float] | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return (float(value[0]), float(value[1]))
    return None


@dataclass(frozen=True, slots=True)
class EvictionPolicy:
    """Configuration for cache eviction.

    ``strategy`` currently supports ``lru`` (least-recently-used by ``last_access_at``).
    Limits are optional; when set, eviction runs after mutating operations and when
    explicitly requested.
    """

    max_bytes: int | None = None
    max_entries: int | None = None
    strategy: str = "lru"

    def __post_init__(self) -> None:
        if self.strategy != "lru":
            raise ValueError(f"unsupported eviction strategy: {self.strategy!r}")
        if self.max_bytes is not None and self.max_bytes < 0:
            raise ValueError("max_bytes must be >= 0")
        if self.max_entries is not None and self.max_entries < 0:
            raise ValueError("max_entries must be >= 0")


class HeavyJobLimiter:
    """Semaphore-based concurrency budget for heavy pipeline operations.

    This is a reusable primitive for source/download/processing stages. Limits cover
    overall heavy jobs plus optional dedicated RAM, IO, and download slots so callers can
    compose budgets conservatively (defaults target 2-4 heavy jobs).
    """

    def __init__(
        self,
        heavy_jobs: int = DEFAULT_HEAVY_JOBS,
        *,
        ram_slots: int | None = None,
        io_slots: int | None = None,
        download_slots: int | None = None,
    ) -> None:
        heavy_jobs = max(MIN_HEAVY_JOBS, min(MAX_DEFAULT_HEAVY_JOBS, int(heavy_jobs)))
        self.heavy_jobs = heavy_jobs
        self.ram_slots = int(ram_slots if ram_slots is not None else heavy_jobs)
        self.io_slots = int(io_slots if io_slots is not None else heavy_jobs)
        self.download_slots = int(download_slots if download_slots is not None else heavy_jobs)
        for name, value in (
            ("ram_slots", self.ram_slots),
            ("io_slots", self.io_slots),
            ("download_slots", self.download_slots),
        ):
            if value <= 0:
                raise ValueError(f"{name} must be positive")
        self._heavy = threading.BoundedSemaphore(self.heavy_jobs)
        self._ram = threading.BoundedSemaphore(self.ram_slots)
        self._io = threading.BoundedSemaphore(self.io_slots)
        self._download = threading.BoundedSemaphore(self.download_slots)

    @contextlib.contextmanager
    def limit(
        self,
        *,
        heavy: bool = True,
        ram: bool = False,
        io: bool = False,
        download: bool = False,
        timeout: float | None = None,
    ) -> Iterator[None]:
        acquired: list[threading.BoundedSemaphore] = []
        try:
            for needed, sem in (
                (heavy, self._heavy),
                (ram, self._ram),
                (io, self._io),
                (download, self._download),
            ):
                if not needed:
                    continue
                ok = sem.acquire() if timeout is None else sem.acquire(timeout=timeout)
                if not ok:
                    raise CacheLockError("timed out acquiring resource budget semaphore")
                acquired.append(sem)
            yield
        finally:
            for sem in reversed(acquired):
                sem.release()

    @contextlib.contextmanager
    def heavy(self, timeout: float | None = None) -> Iterator[None]:
        with self.limit(heavy=True, ram=True, io=True, timeout=timeout):
            yield

    @contextlib.contextmanager
    def download(self, timeout: float | None = None) -> Iterator[None]:
        with self.limit(heavy=True, io=True, download=True, timeout=timeout):
            yield

    @contextlib.contextmanager
    def io(self, timeout: float | None = None) -> Iterator[None]:
        with self.limit(heavy=False, io=True, timeout=timeout):
            yield


# Backward/clearer public alias requested by infrastructure consumers.
ResourceBudget = HeavyJobLimiter


def default_cache_root(provider: str | None = None) -> Path:
    """Return a sensible default cache root, preferring ``analog_pipelines.paths``."""
    if provider:
        if _default_cache_dir is not None:
            return _default_cache_dir(provider)
        if _default_data_dir is not None:
            return _default_data_dir("cache") / provider
        return Path("data/cache") / provider
    if _default_data_dir is not None:
        return _default_data_dir("cache")
    return Path("data/cache")


def compute_checksum(path: Path | str, algorithm: str = "sha256") -> str:
    """Compute a hex digest for ``path`` without loading it entirely into RAM."""
    hasher = hashlib.new(algorithm)
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _now() -> float:
    return time.time()


def _sanitize_component(value: str) -> str:
    if not value or value in {".", ".."} or "/" in value or "\\" in value:
        raise ValueError(f"unsafe cache component: {value!r}")
    return value


def _entry_key(key: str, version: str) -> str:
    return f"{key}::{version}"


def _fsync_dir(path: Path) -> None:
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
        _fsync_dir(path.parent)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def atomic_write_json(path: Path, payload: Any) -> None:
    data = (json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode(
        "utf-8"
    )
    atomic_write_bytes(path, data)


@contextlib.contextmanager
def locked_file(path: Path, *, exclusive: bool, timeout: float | None = None) -> Iterator[None]:
    """Acquire an advisory file lock (flock/fcntl on POSIX), creating ``path`` if needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as handle:
        if fcntl is None:  # pragma: no cover
            yield
            return
        flag = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
        if timeout is None:
            fcntl.flock(handle.fileno(), flag)
        else:
            deadline = time.monotonic() + timeout
            while True:
                try:
                    fcntl.flock(handle.fileno(), flag | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise CacheLockError(f"timed out waiting for lock: {path}") from None
                    time.sleep(0.05)
                except OSError as exc:  # pragma: no cover - defensive
                    if exc.errno in {errno.EACCES, errno.EAGAIN}:
                        if time.monotonic() >= deadline:
                            raise CacheLockError(f"timed out waiting for lock: {path}") from exc
                        time.sleep(0.05)
                        continue
                    raise
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class RasterCache:
    """Versioned on-disk cache for raster artifacts and resumable checkpoints.

    The cache stores each artifact as a content-addressed-by-key/version file under
    ``artifacts/`` and maintains a JSON index under ``state/index.json``. Index and
    checkpoint mutations are protected by an advisory file lock and an in-process lock,
    while artifact writes are atomic (temp file + fsync + rename). Reads verify checksums
    before returning entries to prevent silent corruption.

    This cache tracks artifact provenance metadata only; it never fabricates or zero-fills
    missing upstream data.
    """

    def __init__(
        self,
        root: str | Path | None = None,
        *,
        provider: str | None = None,
        eviction: EvictionPolicy | None = None,
        budget: HeavyJobLimiter | ResourceBudget | None = None,
        checksum_algorithm: str = "sha256",
    ) -> None:
        if root is None:
            root = default_cache_root(provider)
        self.root = Path(root).expanduser().resolve()
        self.artifacts_dir = self.root / "artifacts"
        self.state_dir = self.root / "state"
        self.checkpoints_dir = self.state_dir / "checkpoints"
        self.tmp_dir = self.root / "tmp"
        self.index_path = self.state_dir / "index.json"
        self.lock_path = self.state_dir / "cache.lock"
        self.eviction = eviction or EvictionPolicy()
        self.budget = budget or HeavyJobLimiter()
        self.checksum_algorithm = checksum_algorithm
        self._thread_lock = threading.RLock()
        self._ensure_layout()

    def _ensure_layout(self) -> None:
        for path in (self.artifacts_dir, self.state_dir, self.checkpoints_dir, self.tmp_dir):
            path.mkdir(parents=True, exist_ok=True)

    @contextlib.contextmanager
    def _locked(self, *, exclusive: bool, timeout: float | None = None) -> Iterator[None]:
        with self._thread_lock, locked_file(self.lock_path, exclusive=exclusive, timeout=timeout):
            yield

    def _load_index_unlocked(self) -> dict[str, Any]:
        if not self.index_path.exists():
            return {"index_version": CACHE_INDEX_VERSION, "entries": {}}
        try:
            raw = json.loads(self.index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CacheCorruptionError(f"cannot read cache index: {exc}") from exc
        if not isinstance(raw, dict):
            raise CacheCorruptionError("cache index must be a JSON object")
        entries = raw.get("entries")
        if not isinstance(entries, dict):
            raw["entries"] = {}
        raw.setdefault("index_version", CACHE_INDEX_VERSION)
        return raw

    def _save_index_unlocked(self, index: Mapping[str, Any]) -> None:
        atomic_write_json(self.index_path, dict(index))

    def _artifact_path(self, key: str, version: str) -> Path:
        safe_key = hashlib.sha256(f"{key}\0{version}".encode()).hexdigest()
        # Human-readable prefix is best-effort for debugging; hash defines identity.
        prefix = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in key)[:48]
        name = f"{prefix or 'artifact'}-{version[:24]}-{safe_key}".strip("-")
        return self.artifacts_dir / name

    def artifact_path(self, key: str, version: str) -> Path:
        """Return the canonical on-disk path for a versioned artifact key."""
        return self._artifact_path(key, version)

    def has(self, key: str, version: str) -> bool:
        with self._locked(exclusive=False):
            index = self._load_index_unlocked()
            entry = index["entries"].get(_entry_key(key, version))
            if not entry:
                return False
            path = Path(entry.get("path", ""))
            return path.is_file()

    def get(
        self,
        key: str,
        version: str,
        *,
        verify_checksum: bool = True,
        touch: bool = True,
    ) -> CacheEntry | None:
        """Return a cache entry if present; optionally verify integrity and update LRU."""
        with self._locked(exclusive=touch):
            index = self._load_index_unlocked()
            raw = index["entries"].get(_entry_key(key, version))
            if raw is None:
                return None
            entry = CacheEntry.from_dict(raw)
            path = Path(entry.path)
            if not path.is_file():
                index["entries"].pop(_entry_key(key, version), None)
                self._save_index_unlocked(index)
                return None
            actual_size = path.stat().st_size
            if verify_checksum:
                actual = compute_checksum(path, entry.checksum_algorithm)
                if actual != entry.checksum:
                    raise CacheCorruptionError(
                        f"checksum mismatch for {key}@{version}: expected {entry.checksum}, got {actual}"
                    )
            if entry.size_bytes != actual_size:
                entry = CacheEntry.from_dict({**entry.to_dict(), "size_bytes": actual_size})
                index["entries"][_entry_key(key, version)] = entry.to_dict()
            if touch:
                touched = CacheEntry.from_dict({**entry.to_dict(), "last_access_at": _now()})
                index["entries"][_entry_key(key, version)] = touched.to_dict()
                self._save_index_unlocked(index)
                return touched
            if raw != index["entries"].get(_entry_key(key, version)):
                self._save_index_unlocked(index)
            return entry

    def open(self, key: str, version: str, mode: str = "rb") -> BinaryIO:
        """Open a verified cached artifact for reading."""
        entry = self.get(key, version, verify_checksum=True, touch=True)
        if entry is None:
            raise CacheEntryNotFound(f"cache miss: {key}@{version}")
        return Path(entry.path).open(mode)

    def read_bytes(self, key: str, version: str) -> bytes:
        entry = self.get(key, version, verify_checksum=True, touch=True)
        if entry is None:
            raise CacheEntryNotFound(f"cache miss: {key}@{version}")
        return Path(entry.path).read_bytes()

    def put_file(
        self,
        key: str,
        version: str,
        source_path: str | Path,
        *,
        license: str | None = None,
        crs: str | None = None,
        nodata: float | int | None = None,
        resolution: float | tuple[float, float] | None = None,
        content_type: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        move: bool = False,
    ) -> CacheEntry:
        """Store an existing file as a versioned artifact using atomic replacement."""
        source = Path(source_path)
        if not source.is_file():
            raise FileNotFoundError(source)
        _sanitize_component(version)
        destination = self._artifact_path(key, version)
        destination.parent.mkdir(parents=True, exist_ok=True)

        with self.budget.io(), self._locked(exclusive=True):
            self._atomic_install(source, destination, move=move)
            checksum = compute_checksum(destination, self.checksum_algorithm)
            stat = destination.stat()
            now = _now()
            entry = CacheEntry(
                key=key,
                version=version,
                path=str(destination),
                size_bytes=stat.st_size,
                checksum=checksum,
                checksum_algorithm=self.checksum_algorithm,
                license=license,
                crs=crs,
                nodata=nodata,
                resolution=_coerce_resolution(resolution),
                content_type=content_type,
                created_at=now,
                last_access_at=now,
                metadata=dict(metadata or {}),
            )
            index = self._load_index_unlocked()
            index["entries"][_entry_key(key, version)] = entry.to_dict()
            self._evict_unlocked(index)
            self._save_index_unlocked(index)
            return entry

    def put_bytes(
        self,
        key: str,
        version: str,
        data: bytes,
        *,
        license: str | None = None,
        crs: str | None = None,
        nodata: float | int | None = None,
        resolution: float | tuple[float, float] | None = None,
        content_type: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> CacheEntry:
        fd, tmp_name = tempfile.mkstemp(dir=self.tmp_dir, prefix=".cache-bytes-", suffix=".tmp")
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            return self.put_file(
                key,
                version,
                tmp_path,
                license=license,
                crs=crs,
                nodata=nodata,
                resolution=resolution,
                content_type=content_type,
                metadata=metadata,
                move=True,
            )
        finally:
            tmp_path.unlink(missing_ok=True)

    def write_stream(
        self,
        key: str,
        version: str,
        writer: Callable[[BinaryIO], Any],
        *,
        license: str | None = None,
        crs: str | None = None,
        nodata: float | int | None = None,
        resolution: float | tuple[float, float] | None = None,
        content_type: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> CacheEntry:
        """Atomically create an artifact by streaming bytes through ``writer(handle)``.

        Useful for download pipelines: write to a temp file, fsync, publish into the
        versioned cache, and record metadata/checksum in one step.
        """
        _sanitize_component(version)
        fd, tmp_name = tempfile.mkstemp(dir=self.tmp_dir, prefix=".cache-stream-", suffix=".part")
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as handle:
                writer(handle)
                handle.flush()
                os.fsync(handle.fileno())
            return self.put_file(
                key,
                version,
                tmp_path,
                license=license,
                crs=crs,
                nodata=nodata,
                resolution=resolution,
                content_type=content_type,
                metadata=metadata,
                move=True,
            )
        finally:
            tmp_path.unlink(missing_ok=True)

    def _atomic_install(self, source: Path, destination: Path, *, move: bool) -> None:
        fd, tmp_name = tempfile.mkstemp(
            dir=destination.parent, prefix=f".{destination.name}.", suffix=".tmp"
        )
        os.close(fd)
        tmp_path = Path(tmp_name)
        try:
            if move:
                try:
                    os.replace(source, tmp_path)
                except OSError:
                    shutil.copy2(source, tmp_path)
                    with contextlib.suppress(FileNotFoundError):
                        source.unlink()
            else:
                shutil.copy2(source, tmp_path)
            # Ensure copied bytes are durable before rename.
            with tmp_path.open("rb") as handle:
                os.fsync(handle.fileno())
            os.replace(tmp_path, destination)
            _fsync_dir(destination.parent)
        except BaseException:
            tmp_path.unlink(missing_ok=True)
            raise

    def remove(self, key: str, version: str) -> bool:
        with self._locked(exclusive=True):
            index = self._load_index_unlocked()
            raw = index["entries"].pop(_entry_key(key, version), None)
            self._save_index_unlocked(index)
            if raw is None:
                return False
            path = Path(raw.get("path", ""))
            with contextlib.suppress(FileNotFoundError):
                path.unlink()
            return True

    def list_entries(self) -> list[CacheEntry]:
        with self._locked(exclusive=False):
            index = self._load_index_unlocked()
            entries = [CacheEntry.from_dict(v) for v in index["entries"].values()]
        return sorted(entries, key=lambda e: (e.last_access_at, e.created_at, e.key, e.version))

    def stats(self) -> dict[str, Any]:
        entries = self.list_entries()
        total_bytes = sum(e.size_bytes for e in entries)
        return {
            "root": str(self.root),
            "entries": len(entries),
            "total_bytes": total_bytes,
            "eviction": asdict(self.eviction),
        }

    def evict(self) -> list[CacheEntry]:
        with self._locked(exclusive=True):
            index = self._load_index_unlocked()
            removed = self._evict_unlocked(index)
            self._save_index_unlocked(index)
            return removed

    def _evict_unlocked(self, index: dict[str, Any]) -> list[CacheEntry]:
        entries_map: dict[str, dict[str, Any]] = index.setdefault("entries", {})
        entries = [CacheEntry.from_dict(v) for v in entries_map.values()]
        if not entries:
            return []

        # Drop index records whose files disappeared (external cleanup/corruption).
        removed: list[CacheEntry] = []
        live: list[CacheEntry] = []
        for entry in entries:
            if Path(entry.path).is_file():
                live.append(entry)
            else:
                entries_map.pop(_entry_key(entry.key, entry.version), None)
                removed.append(entry)
        entries = live

        def over_limit(items: list[CacheEntry]) -> bool:
            if self.eviction.max_entries is not None and len(items) > self.eviction.max_entries:
                return True
            if self.eviction.max_bytes is not None:
                total = sum(e.size_bytes for e in items)
                if total > self.eviction.max_bytes:
                    return True
            return False

        # LRU: oldest last_access_at first; stable tie-breakers for determinism.
        entries.sort(key=lambda e: (e.last_access_at, e.created_at, e.key, e.version))
        while entries and over_limit(entries):
            victim = entries.pop(0)
            entries_map.pop(_entry_key(victim.key, victim.version), None)
            with contextlib.suppress(FileNotFoundError):
                Path(victim.path).unlink()
            removed.append(victim)
        return removed

    # ----------------------------- checkpoint/resume -----------------------------

    def _checkpoint_path(self, checkpoint_id: str) -> Path:
        safe = hashlib.sha256(checkpoint_id.encode("utf-8")).hexdigest()
        prefix = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in checkpoint_id)[
            :40
        ]
        return self.checkpoints_dir / f"{prefix or 'ckpt'}-{safe}.json"

    def save_checkpoint(
        self,
        checkpoint_id: str,
        *,
        key: str | None = None,
        version: str | None = None,
        chunks_total: int | None = None,
        chunks_completed: int | None = None,
        completed_chunks: list[int] | tuple[int, ...] | None = None,
        byte_offset: int | None = None,
        partial_path: str | Path | None = None,
        state: Mapping[str, Any] | None = None,
    ) -> Path:
        """Persist partial/chunk progress atomically for safe resume.

        The checkpoint is advisory metadata: callers remain responsible for validating that
        resumed bytes/chunks match the intended upstream version and checksum expectations.
        No missing data is synthesized.
        """
        if chunks_total is not None and chunks_total < 0:
            raise ValueError("chunks_total must be >= 0")
        if chunks_completed is not None and chunks_completed < 0:
            raise ValueError("chunks_completed must be >= 0")
        payload = {
            "checkpoint_version": CHECKPOINT_VERSION,
            "checkpoint_id": checkpoint_id,
            "key": key,
            "version": version,
            "chunks_total": chunks_total,
            "chunks_completed": chunks_completed,
            "completed_chunks": sorted(set(int(i) for i in (completed_chunks or []))),
            "byte_offset": int(byte_offset) if byte_offset is not None else None,
            "partial_path": str(partial_path) if partial_path is not None else None,
            "state": dict(state or {}),
            "updated_at": _now(),
        }
        path = self._checkpoint_path(checkpoint_id)
        with self._locked(exclusive=True):
            atomic_write_json(path, payload)
        return path

    def load_checkpoint(self, checkpoint_id: str) -> dict[str, Any]:
        path = self._checkpoint_path(checkpoint_id)
        with self._locked(exclusive=False):
            if not path.exists():
                raise CheckpointNotFound(f"checkpoint not found: {checkpoint_id}")
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise CacheCorruptionError(
                    f"cannot read checkpoint {checkpoint_id}: {exc}"
                ) from exc
        if not isinstance(data, dict):
            raise CacheCorruptionError(f"checkpoint must be a JSON object: {checkpoint_id}")
        return data

    def clear_checkpoint(self, checkpoint_id: str) -> bool:
        path = self._checkpoint_path(checkpoint_id)
        with self._locked(exclusive=True):
            try:
                path.unlink()
                return True
            except FileNotFoundError:
                return False

    def has_checkpoint(self, checkpoint_id: str) -> bool:
        return self._checkpoint_path(checkpoint_id).exists()


__all__ = [
    "CACHE_INDEX_VERSION",
    "CHECKPOINT_VERSION",
    "CacheCorruptionError",
    "CacheEntry",
    "CacheEntryNotFound",
    "CacheLockError",
    "CheckpointNotFound",
    "EvictionPolicy",
    "HeavyJobLimiter",
    "RasterCache",
    "RasterCacheError",
    "ResourceBudget",
    "atomic_write_bytes",
    "atomic_write_json",
    "compute_checksum",
    "default_cache_root",
    "locked_file",
]
