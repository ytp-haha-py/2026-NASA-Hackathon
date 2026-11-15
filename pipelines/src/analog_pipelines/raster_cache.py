"""Disk cache, resumable chunk state and resource budget for bounded raster windows.

This is the storage layer for Issue #170 Goal C. It sits between a raster source
(:mod:`analog_pipelines.raster_source`, owned separately) and the feature
pipeline: a source reads a bounded window, this module decides whether that exact
window is already on disk, and if not stores it atomically with provenance.

The cache never invents pixels. An entry becomes visible only after its payload
and checksummed metadata have both been written atomically; a payload whose
checksum no longer matches is treated as corrupt and is never returned as data.

Layout (all below one configurable root, outside Git)::

    <root>/.gitignore
    <root>/entries/<ab>/<fingerprint>/payload.bin
    <root>/entries/<ab>/<fingerprint>/meta.json
    <root>/entries/<ab>/<fingerprint>/chunks/plan.json
    <root>/entries/<ab>/<fingerprint>/chunks/000000.part
    <root>/locks/<ab>/<fingerprint>.lock
    <root>/quarantine/<fingerprint>-<utc>-<pid>/

Design rules:

* The key includes the source ID, the verified source version, the exact pixel
  window, the band, and any reader options. A version bump therefore never reuses
  a stale window.
* ``meta.json`` is the commit marker and carries the payload SHA-256. A payload
  without metadata, or with a mismatching digest, is corrupt and is quarantined
  on read.
* Writes use ``tempfile`` + ``fsync`` + ``os.replace`` in the destination
  directory, so a crash leaves either the previous entry or no entry.
* A per-key ``flock`` serialises readers/writers across threads and processes.
  :meth:`RasterCache.get_or_compute` uses it as a single-flight guard.
* Large windows can be written chunk by chunk. Completed chunks survive a crash
  and are detected again by :class:`ChunkedWrite`, so a restart resumes instead of
  re-reading the whole window.
* :class:`ResourceBudget` bounds concurrent heavy jobs (default 3, in the 2-4
  range requested for Goal C), estimated RAM, and free disk.

Everything here is offline and storage-only. It performs no network reads and
does not require rasterio; :func:`read_window_cached` accepts any object that
implements the :class:`~analog_pipelines.raster_source.RasterSource` window
contract.
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import io
import json
import os
import shutil
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from analog_pipelines.paths import data_dir
from analog_pipelines.storage import atomic_write_bytes, sha256_bytes

CACHE_FORMAT_VERSION = "raster-cache-1.0.0"
ENTRY_PAYLOAD_NAME = "payload.bin"
ENTRY_METADATA_NAME = "meta.json"
ENTRY_CHUNK_DIR = "chunks"
CHUNK_PLAN_NAME = "plan.json"

STATUS_HIT = "hit"
STATUS_MISS = "miss"
STATUS_PARTIAL = "partial"
STATUS_CORRUPT = "corrupt"
CACHE_STATUSES = (STATUS_HIT, STATUS_MISS, STATUS_PARTIAL, STATUS_CORRUPT)

DEFAULT_MAX_BYTES = 4 * 1024**3
DEFAULT_MAX_ENTRIES = 5000
DEFAULT_HEAVY_JOBS = 3
DEFAULT_MAX_RAM_BYTES = 2 * 1024**3
DEFAULT_MIN_FREE_DISK_BYTES = 0
DEFAULT_LOCK_TIMEOUT_S = 60.0
DEFAULT_HEAVY_JOB_TIMEOUT_S = 60.0

ENV_ROOT = "ANALOG_RASTER_CACHE_ROOT"
ENV_MAX_BYTES = "ANALOG_RASTER_CACHE_MAX_BYTES"
ENV_MAX_ENTRIES = "ANALOG_RASTER_CACHE_MAX_ENTRIES"
ENV_HEAVY_JOBS = "ANALOG_RASTER_HEAVY_JOBS"
ENV_MAX_RAM_BYTES = "ANALOG_RASTER_BUDGET_RAM_BYTES"
ENV_MIN_FREE_DISK_BYTES = "ANALOG_RASTER_BUDGET_MIN_FREE_DISK_BYTES"

_GITIGNORE = """# Created by RasterCache. Bounded raster windows are reproducible; do not commit them.
*
!.gitignore
"""


class RasterCacheError(RuntimeError):
    """Base class for cache and budget failures."""


class InvalidCacheKey(RasterCacheError):
    """A source ID, version, window, band or option is not cacheable."""


class CacheStateError(RasterCacheError):
    """A chunk plan or chunk set is inconsistent and cannot be resumed safely."""


class CacheLockTimeout(RasterCacheError):
    """The per-key lock could not be acquired before the deadline."""


class BudgetExceeded(RasterCacheError):
    """A heavy-job slot or the RAM budget could not be granted."""


class InsufficientDisk(BudgetExceeded):
    """Writing would leave less free disk than the configured reserve."""


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _env_int(env: Mapping[str, str], name: str, default: int) -> int:
    raw = env.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise RasterCacheError(f"{name} must be an integer, got {raw!r}") from exc


# ---------------------------------------------------------------------------
# Cache key
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RasterWindow:
    """A half-open pixel window in the source raster: ``[col_off, row_off, +w, +h)``."""

    col_off: int
    row_off: int
    width: int
    height: int

    def __post_init__(self) -> None:
        for name, value in (
            ("col_off", self.col_off),
            ("row_off", self.row_off),
            ("width", self.width),
            ("height", self.height),
        ):
            if isinstance(value, bool) or not isinstance(value, int):
                raise InvalidCacheKey(f"window {name} must be an int, got {value!r}")
        if self.col_off < 0 or self.row_off < 0:
            raise InvalidCacheKey("window offsets must be non-negative")
        if self.width < 1 or self.height < 1:
            raise InvalidCacheKey("window width and height must be positive")

    def as_tuple(self) -> tuple[int, int, int, int]:
        return (self.col_off, self.row_off, self.width, self.height)

    def to_payload(self) -> dict[str, int]:
        return {
            "col_off": self.col_off,
            "row_off": self.row_off,
            "width": self.width,
            "height": self.height,
        }


@dataclass(frozen=True, slots=True)
class CacheKey:
    """Identity of one bounded raster window read.

    ``source_version`` must be the verified immutable version (a checksum, product
    revision, or pinned snapshot ID), never "latest".
    """

    source_id: str
    source_version: str
    window: RasterWindow
    band: int = 1
    options_json: str = "{}"

    def __post_init__(self) -> None:
        for name, value in (
            ("source_id", self.source_id),
            ("source_version", self.source_version),
        ):
            if not isinstance(value, str) or not value.strip():
                raise InvalidCacheKey(f"{name} must be a non-empty string")
        if not isinstance(self.window, RasterWindow):
            raise InvalidCacheKey("window must be a RasterWindow")
        if isinstance(self.band, bool) or not isinstance(self.band, int) or self.band < 1:
            raise InvalidCacheKey("band must be a positive int (rasterio bands are 1-based)")
        try:
            parsed = json.loads(self.options_json)
        except ValueError as exc:
            raise InvalidCacheKey(f"options_json is not valid JSON: {exc}") from exc
        if not isinstance(parsed, dict):
            raise InvalidCacheKey("options_json must encode a JSON object")

    @classmethod
    def build(
        cls,
        *,
        source_id: str,
        source_version: str,
        window: RasterWindow | tuple[int, int, int, int],
        band: int = 1,
        options: Mapping[str, Any] | None = None,
    ) -> CacheKey:
        """Normalise a caller's options mapping so equal options give equal keys."""
        if not isinstance(window, RasterWindow):
            try:
                window = RasterWindow(*window)
            except TypeError as exc:
                raise InvalidCacheKey(
                    "window must be a RasterWindow or (col_off, row_off, width, height)"
                ) from exc
        payload = dict(options or {})
        try:
            encoded = _canonical(payload)
        except (TypeError, ValueError) as exc:
            raise InvalidCacheKey(f"options must be JSON-serialisable: {exc}") from exc
        return cls(
            source_id=source_id,
            source_version=source_version,
            window=window,
            band=band,
            options_json=encoded,
        )

    @classmethod
    def for_source(
        cls,
        source: Any,
        *,
        window: RasterWindow | tuple[int, int, int, int],
        band: int = 1,
        options: Mapping[str, Any] | None = None,
    ) -> CacheKey:
        """Build a key from a ``RasterSource``-like object.

        Reads ``dataset_id`` (preferred), ``uri`` or ``source_id`` plus a required
        ``version``/``declared_checksum``. An unversioned source is rejected: caching
        bytes under an implicit "latest" identity would silently mix revisions.
        """
        source_id = (
            getattr(source, "dataset_id", None)
            or getattr(source, "uri", None)
            or getattr(source, "source_id", None)
        )
        version = (
            getattr(source, "version", None)
            or getattr(source, "declared_checksum", None)
            or getattr(source, "source_version", None)
        )
        if not source_id:
            raise InvalidCacheKey("source has no dataset_id, uri or source_id")
        if not version:
            raise InvalidCacheKey(
                "source has no version or declared checksum; an unversioned cache key "
                "would mix revisions"
            )
        return cls.build(
            source_id=str(source_id),
            source_version=str(version),
            window=window,
            band=band,
            options=options,
        )

    @property
    def options(self) -> dict[str, Any]:
        return json.loads(self.options_json)

    def to_payload(self) -> dict[str, Any]:
        return {
            "cache_format": CACHE_FORMAT_VERSION,
            "source_id": self.source_id,
            "source_version": self.source_version,
            "window": self.window.to_payload(),
            "band": self.band,
            "options": self.options,
        }

    @property
    def fingerprint(self) -> str:
        return sha256_bytes(_canonical(self.to_payload()).encode("utf-8"))


@dataclass(frozen=True, slots=True)
class CacheEntry:
    """A committed cache entry; ``payload_path`` is safe to open read-only."""

    key: CacheKey
    directory: Path
    payload_path: Path
    metadata_path: Path
    checksum: str
    size_bytes: int
    chunk_count: int
    created_at: str
    content_type: str
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def last_used_at(self) -> float:
        try:
            return self.payload_path.stat().st_mtime
        except FileNotFoundError:
            return 0.0

    def read_bytes(self) -> bytes:
        return self.payload_path.read_bytes()

    def read_array(self) -> np.ndarray:
        return np.load(self.payload_path, allow_pickle=False)

    def to_payload(self) -> dict[str, Any]:
        return {
            "cache_key": self.key.fingerprint,
            "source_id": self.key.source_id,
            "source_version": self.key.source_version,
            "window": self.key.window.to_payload(),
            "band": self.key.band,
            "checksum": self.checksum,
            "size_bytes": self.size_bytes,
            "chunk_count": self.chunk_count,
            "created_at": self.created_at,
            "content_type": self.content_type,
        }


@dataclass(frozen=True, slots=True)
class CacheProbe:
    """Read-only status of one key, useful before an expensive read."""

    status: str
    key: CacheKey
    entry: CacheEntry | None = None
    completed_chunks: tuple[int, ...] = ()
    detail: str = ""

    def to_payload(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "cache_key": self.key.fingerprint,
            "completed_chunks": list(self.completed_chunks),
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class CachedWindow:
    """A cached bounded window with source-CRS georeferencing.

    ``transform`` holds the rasterio ``Affine`` coefficients as a JSON-safe tuple;
    use :meth:`to_affine` in code paths that need the rasterio object. The cached
    values are copies of the source read, not new observations.
    """

    key: CacheKey
    data: np.ndarray
    valid_mask: np.ndarray
    window: tuple[int, int, int, int]
    transform: tuple[float, float, float, float, float, float]
    crs_wkt: str | None
    crs_name: str | None
    band: int
    checksum: str
    size_bytes: int

    @property
    def valid_fraction(self) -> float:
        return float(np.count_nonzero(self.valid_mask)) / float(self.valid_mask.size)

    def to_affine(self) -> Any:
        from rasterio.transform import Affine

        return Affine(*self.transform)

    def to_payload(self) -> dict[str, Any]:
        return {
            "cache_key": self.key.fingerprint,
            "source_id": self.key.source_id,
            "source_version": self.key.source_version,
            "window": list(self.window),
            "band": self.band,
            "checksum": self.checksum,
            "size_bytes": self.size_bytes,
            "valid_fraction": self.valid_fraction,
        }


@dataclass(frozen=True, slots=True)
class EvictionReport:
    removed: tuple[str, ...]
    removed_bytes: int
    remaining_entries: int
    remaining_bytes: int
    dry_run: bool

    def to_payload(self) -> dict[str, Any]:
        return {
            "removed": list(self.removed),
            "removed_bytes": self.removed_bytes,
            "remaining_entries": self.remaining_entries,
            "remaining_bytes": self.remaining_bytes,
            "dry_run": self.dry_run,
        }


# ---------------------------------------------------------------------------
# Resource budget
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BudgetLimits:
    """Hard limits for concurrent heavy jobs, estimated RAM and free disk."""

    max_heavy_jobs: int = DEFAULT_HEAVY_JOBS
    max_ram_bytes: int = DEFAULT_MAX_RAM_BYTES
    min_free_disk_bytes: int = DEFAULT_MIN_FREE_DISK_BYTES

    def __post_init__(self) -> None:
        for name, value in (
            ("max_heavy_jobs", self.max_heavy_jobs),
            ("max_ram_bytes", self.max_ram_bytes),
            ("min_free_disk_bytes", self.min_free_disk_bytes),
        ):
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{name} must be an int, got {value!r}")
        if self.max_heavy_jobs < 1:
            raise ValueError("max_heavy_jobs must be at least 1")
        if self.max_ram_bytes < 0 or self.min_free_disk_bytes < 0:
            raise ValueError("RAM and disk limits must be non-negative")

    def to_payload(self) -> dict[str, int]:
        return {
            "max_heavy_jobs": self.max_heavy_jobs,
            "max_ram_bytes": self.max_ram_bytes,
            "min_free_disk_bytes": self.min_free_disk_bytes,
        }


def budget_limits_from_env(env: Mapping[str, str] | None = None) -> BudgetLimits:
    values = dict(os.environ if env is None else env)
    return BudgetLimits(
        max_heavy_jobs=_env_int(values, ENV_HEAVY_JOBS, DEFAULT_HEAVY_JOBS),
        max_ram_bytes=_env_int(values, ENV_MAX_RAM_BYTES, DEFAULT_MAX_RAM_BYTES),
        min_free_disk_bytes=_env_int(values, ENV_MIN_FREE_DISK_BYTES, DEFAULT_MIN_FREE_DISK_BYTES),
    )


def _existing_ancestor(path: Path) -> Path:
    candidate = path.resolve()
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    return candidate


class ResourceBudget:
    """Bounded heavy-job slots plus RAM reservation and a free-disk guard.

    ``heavy_job`` blocks until both a slot and the estimated RAM are available, so
    a caller with 8 queued windows runs at most ``max_heavy_jobs`` at a time and
    never claims more RAM than ``max_ram_bytes``. ``check_disk`` raises instead of
    letting a cache root fill the volume.
    """

    def __init__(self, limits: BudgetLimits | None = None) -> None:
        self.limits = limits or BudgetLimits()
        self._semaphore = threading.BoundedSemaphore(self.limits.max_heavy_jobs)
        self._condition = threading.Condition()
        self._active = 0
        self._peak_active = 0
        self._ram_in_use = 0
        self._peak_ram = 0

    def check_disk(self, path: Path | str, required_bytes: int = 0) -> None:
        if required_bytes < 0:
            raise ValueError("required_bytes must be non-negative")
        usage = shutil.disk_usage(_existing_ancestor(Path(path)))
        if usage.free < required_bytes + self.limits.min_free_disk_bytes:
            raise InsufficientDisk(
                f"insufficient disk at {path}: {usage.free} bytes free, "
                f"{required_bytes} requested, {self.limits.min_free_disk_bytes} reserved"
            )

    @contextlib.contextmanager
    def heavy_job(
        self,
        ram_bytes: int = 0,
        *,
        timeout: float | None = DEFAULT_HEAVY_JOB_TIMEOUT_S,
    ) -> Iterator[ResourceBudget]:
        if isinstance(ram_bytes, bool) or not isinstance(ram_bytes, int) or ram_bytes < 0:
            raise ValueError("ram_bytes must be a non-negative int")
        if ram_bytes > self.limits.max_ram_bytes:
            raise BudgetExceeded(
                f"requested RAM {ram_bytes} exceeds the budget of {self.limits.max_ram_bytes}"
            )
        deadline = None if timeout is None else time.monotonic() + timeout
        wait = -1 if deadline is None else max(0.0, deadline - time.monotonic())
        if not self._semaphore.acquire(timeout=wait):
            raise BudgetExceeded(
                f"no heavy job slot free within {timeout}s "
                f"(max_heavy_jobs={self.limits.max_heavy_jobs})"
            )
        reserved = False
        try:
            with self._condition:
                while self._ram_in_use + ram_bytes > self.limits.max_ram_bytes:
                    remaining = None if deadline is None else deadline - time.monotonic()
                    if remaining is not None and remaining <= 0:
                        raise BudgetExceeded(
                            f"RAM budget {self.limits.max_ram_bytes} exhausted within "
                            f"{timeout}s; {self._ram_in_use} bytes in use"
                        )
                    self._condition.wait(remaining)
                self._active += 1
                self._ram_in_use += ram_bytes
                self._peak_active = max(self._peak_active, self._active)
                self._peak_ram = max(self._peak_ram, self._ram_in_use)
                reserved = True
            yield self
        finally:
            if reserved:
                with self._condition:
                    self._active -= 1
                    self._ram_in_use -= ram_bytes
                    self._condition.notify_all()
            self._semaphore.release()

    def snapshot(self) -> dict[str, Any]:
        with self._condition:
            return {
                "limits": self.limits.to_payload(),
                "active_heavy_jobs": self._active,
                "peak_heavy_jobs": self._peak_active,
                "ram_in_use_bytes": self._ram_in_use,
                "peak_ram_bytes": self._peak_ram,
            }


# ---------------------------------------------------------------------------
# Chunked, resumable writes
# ---------------------------------------------------------------------------


class ChunkedWrite:
    """Write one payload as ordered chunks so a crash resumes where it stopped.

    Each chunk is a separate atomic file under ``chunks/``. A restart constructs a
    new session for the same key and sees the completed chunk indices; missing
    chunks are the caller's remaining work. :meth:`finalize` refuses to commit
    unless every chunk is present, then assembles the payload atomically, writes
    the checksummed metadata, and discards the chunk directory.
    """

    def __init__(self, cache: RasterCache, key: CacheKey, chunk_count: int) -> None:
        if isinstance(chunk_count, bool) or not isinstance(chunk_count, int) or chunk_count < 1:
            raise ValueError("chunk_count must be a positive int")
        self.cache = cache
        self.key = key
        self.chunk_count = chunk_count
        self.directory = cache._chunk_dir(key)  # noqa: SLF001 - same module
        self.directory.mkdir(parents=True, exist_ok=True)
        self._plan_path = self.directory / CHUNK_PLAN_NAME
        plan = {
            "cache_format": CACHE_FORMAT_VERSION,
            "cache_key": key.fingerprint,
            "chunk_count": chunk_count,
        }
        if self._plan_path.is_file():
            try:
                existing = json.loads(self._plan_path.read_text(encoding="utf-8"))
            except ValueError as exc:
                raise CacheStateError(
                    f"unreadable chunk plan for {key.fingerprint}: {exc}"
                ) from exc
            if existing.get("cache_key") != key.fingerprint:
                raise CacheStateError("chunk plan belongs to a different cache key")
            if existing.get("chunk_count") != chunk_count:
                raise CacheStateError(
                    "chunk plan has chunk_count="
                    f"{existing.get('chunk_count')}, requested {chunk_count}"
                )
        else:
            atomic_write_bytes(self._plan_path, (json.dumps(plan, sort_keys=True) + "\n").encode())
        self._closed = False

    def _chunk_path(self, index: int) -> Path:
        return self.directory / f"{index:06d}.part"

    @property
    def completed_chunks(self) -> tuple[int, ...]:
        completed: list[int] = []
        for path in self.directory.glob("*.part"):
            try:
                index = int(path.stem)
            except ValueError:
                continue
            if 0 <= index < self.chunk_count:
                completed.append(index)
        return tuple(sorted(completed))

    @property
    def missing_chunks(self) -> tuple[int, ...]:
        done = set(self.completed_chunks)
        return tuple(index for index in range(self.chunk_count) if index not in done)

    def write_chunk(self, index: int, data: bytes) -> None:
        if not 0 <= index < self.chunk_count:
            raise ValueError(f"chunk index {index} outside 0..{self.chunk_count - 1}")
        if self._closed:
            raise CacheStateError("chunk session is already closed")
        atomic_write_bytes(self._chunk_path(index), bytes(data))

    def finalize(
        self,
        *,
        content_type: str = "application/octet-stream",
        extra: Mapping[str, Any] | None = None,
    ) -> CacheEntry:
        missing = self.missing_chunks
        if missing:
            raise CacheStateError(
                f"{len(missing)} chunk(s) missing before finalize: {list(missing[:8])}"
            )
        directory = self.cache._entry_dir(self.key)  # noqa: SLF001 - same module
        directory.mkdir(parents=True, exist_ok=True)
        payload_path = directory / ENTRY_PAYLOAD_NAME
        temporary = payload_path.with_name(f".{payload_path.name}.assemble.tmp")
        digest = hashlib.sha256()
        size = 0
        try:
            with temporary.open("wb") as handle:
                for index in range(self.chunk_count):
                    with self._chunk_path(index).open("rb") as source:
                        for block in iter(lambda: source.read(1024 * 1024), b""):
                            digest.update(block)
                            handle.write(block)
                            size += len(block)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, payload_path)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
        entry = self.cache._commit(  # noqa: SLF001 - same module
            self.key,
            payload_path=payload_path,
            checksum=digest.hexdigest(),
            size_bytes=size,
            chunk_count=self.chunk_count,
            content_type=content_type,
            extra=extra,
        )
        self.discard()
        return entry

    def discard(self) -> None:
        shutil.rmtree(self.directory, ignore_errors=True)
        self._closed = True

    def __enter__(self) -> ChunkedWrite:
        return self

    def __exit__(self, *exc_info: object) -> None:
        if self._closed:
            return
        if exc_info and exc_info[0] is not None:
            self.discard()
        else:
            # A normal exit keeps completed chunks so a later session can resume.
            self._closed = True


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


class RasterCache:
    """Content-addressed disk cache for bounded raster windows."""

    def __init__(
        self,
        root: Path | str | None = None,
        *,
        max_bytes: int | None = None,
        max_entries: int | None = None,
        budget: ResourceBudget | None = None,
        auto_evict: bool = True,
        env: Mapping[str, str] | None = None,
    ) -> None:
        values = dict(os.environ if env is None else env)
        self.root = Path(root if root is not None else default_cache_root(values)).expanduser()
        self.max_bytes = (
            _env_int(values, ENV_MAX_BYTES, DEFAULT_MAX_BYTES) if max_bytes is None else max_bytes
        )
        self.max_entries = (
            _env_int(values, ENV_MAX_ENTRIES, DEFAULT_MAX_ENTRIES)
            if max_entries is None
            else max_entries
        )
        if self.max_bytes < 0 or self.max_entries < 0:
            raise ValueError("cache limits must be non-negative")
        self.budget = budget or ResourceBudget(budget_limits_from_env(values))
        self.auto_evict = auto_evict
        self._stats_lock = threading.Lock()
        self._stats: dict[str, int] = {
            "hits": 0,
            "misses": 0,
            "puts": 0,
            "evictions": 0,
            "evicted_bytes": 0,
            "corruptions": 0,
            "quarantined": 0,
            "lock_timeouts": 0,
        }

    # -- paths ---------------------------------------------------------

    def _entry_dir(self, key: CacheKey) -> Path:
        fingerprint = key.fingerprint
        return self.root / "entries" / fingerprint[:2] / fingerprint

    def _chunk_dir(self, key: CacheKey) -> Path:
        return self._entry_dir(key) / ENTRY_CHUNK_DIR

    def _lock_path(self, key: CacheKey) -> Path:
        fingerprint = key.fingerprint
        return self.root / "locks" / fingerprint[:2] / f"{fingerprint}.lock"

    def _ensure_root(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        ignore = self.root / ".gitignore"
        if not ignore.exists():
            atomic_write_bytes(ignore, _GITIGNORE.encode("utf-8"))

    # -- stats ---------------------------------------------------------

    def _bump(self, name: str, amount: int = 1) -> None:
        with self._stats_lock:
            self._stats[name] = self._stats.get(name, 0) + amount

    def reset_stats(self) -> None:
        with self._stats_lock:
            for name in self._stats:
                self._stats[name] = 0

    def stats(self) -> dict[str, Any]:
        entries = list(self.iter_entries())
        with self._stats_lock:
            counters = dict(self._stats)
        return {
            "root": str(self.root),
            "cache_format": CACHE_FORMAT_VERSION,
            "entries": len(entries),
            "bytes": sum(entry.size_bytes for entry in entries),
            "max_bytes": self.max_bytes,
            "max_entries": self.max_entries,
            "counters": counters,
            "budget": self.budget.snapshot(),
            "free_disk_bytes": shutil.disk_usage(_existing_ancestor(self.root)).free,
        }

    # -- locking -------------------------------------------------------

    @contextlib.contextmanager
    def lock(self, key: CacheKey, *, timeout: float = DEFAULT_LOCK_TIMEOUT_S) -> Iterator[None]:
        """Exclusive per-key lock shared by threads and processes (POSIX ``flock``)."""
        if timeout < 0:
            raise ValueError("lock timeout must be non-negative")
        path = self._lock_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = path.open("a+b")
        deadline = time.monotonic() + timeout
        try:
            while True:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        self._bump("lock_timeouts")
                        raise CacheLockTimeout(
                            f"cache key {key.fingerprint} stayed locked for {timeout}s"
                        ) from None
                    time.sleep(0.01)
            yield
        finally:
            with contextlib.suppress(OSError):
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()

    # -- reads ---------------------------------------------------------

    def _load_entry(self, key: CacheKey, *, verify: bool) -> tuple[CacheEntry | None, str]:
        directory = self._entry_dir(key)
        metadata_path = directory / ENTRY_METADATA_NAME
        payload_path = directory / ENTRY_PAYLOAD_NAME
        if not metadata_path.is_file():
            if payload_path.exists():
                return None, "payload exists without checksummed metadata"
            return None, ""
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            return None, f"unreadable metadata: {type(exc).__name__}"
        if not isinstance(metadata, dict):
            return None, "metadata is not a JSON object"
        if metadata.get("cache_format") != CACHE_FORMAT_VERSION:
            return None, f"unsupported cache format {metadata.get('cache_format')!r}"
        if metadata.get("cache_key") != key.fingerprint:
            return None, "metadata cache_key does not match the requested key"
        checksum = metadata.get("sha256")
        size_bytes = metadata.get("size_bytes")
        if not isinstance(checksum, str) or len(checksum) != 64:
            return None, "metadata has no valid sha256"
        if not isinstance(size_bytes, int) or size_bytes < 0:
            return None, "metadata has no valid size_bytes"
        if not payload_path.is_file():
            return None, "metadata exists without a payload"
        actual_size = payload_path.stat().st_size
        if actual_size != size_bytes:
            return None, f"payload size {actual_size} != metadata {size_bytes}"
        if verify and sha256_file(payload_path) != checksum:
            return None, "payload sha256 mismatch"
        entry = CacheEntry(
            key=key,
            directory=directory,
            payload_path=payload_path,
            metadata_path=metadata_path,
            checksum=checksum,
            size_bytes=size_bytes,
            chunk_count=int(metadata.get("chunk_count", 1) or 1),
            created_at=str(metadata.get("created_at", "")),
            content_type=str(metadata.get("content_type", "application/octet-stream")),
            extra=dict(metadata.get("extra") or {}),
        )
        return entry, ""

    def _quarantine(self, key: CacheKey) -> Path | None:
        directory = self._entry_dir(key)
        if not directory.exists():
            return None
        target = (
            self.root
            / "quarantine"
            / f"{key.fingerprint}-{utc_now_iso().replace(':', '')}-{os.getpid()}"
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.move(str(directory), str(target))
        except OSError:
            shutil.rmtree(directory, ignore_errors=True)
            return None
        self._bump("quarantined")
        return target

    def entry(
        self, key: CacheKey, *, verify: bool = True, quarantine: bool = True
    ) -> CacheEntry | None:
        """Return the committed entry, or ``None`` for a miss/corrupt/partial entry."""
        entry, detail = self._load_entry(key, verify=verify)
        if entry is not None:
            return entry
        if detail:
            self._bump("corruptions")
            if quarantine:
                self._quarantine(key)
        return None

    def probe(self, key: CacheKey, *, verify: bool = False) -> CacheProbe:
        """Report hit/miss/partial/corrupt without mutating the cache."""
        entry, detail = self._load_entry(key, verify=verify)
        if entry is not None:
            return CacheProbe(status=STATUS_HIT, key=key, entry=entry)
        if detail:
            return CacheProbe(status=STATUS_CORRUPT, key=key, detail=detail)
        chunks = self._chunk_dir(key)
        if chunks.is_dir():
            completed: list[int] = []
            for path in chunks.glob("*.part"):
                try:
                    completed.append(int(path.stem))
                except ValueError:
                    continue
            return CacheProbe(
                status=STATUS_PARTIAL,
                key=key,
                completed_chunks=tuple(sorted(completed)),
                detail="chunked write in progress or interrupted",
            )
        return CacheProbe(status=STATUS_MISS, key=key)

    def _touch(self, entry: CacheEntry) -> None:
        with contextlib.suppress(OSError):
            os.utime(entry.payload_path, None)

    def get(self, key: CacheKey, *, touch: bool = True) -> bytes | None:
        """Return verified payload bytes, or ``None`` on a miss.

        A corrupt entry is quarantined and counted, then reported as a miss: the
        pipeline recomputes it instead of trusting damaged pixels.
        """
        entry = self.entry(key, verify=True, quarantine=True)
        if entry is None:
            self._bump("misses")
            return None
        try:
            data = entry.read_bytes()
        except FileNotFoundError:
            self._bump("misses")
            return None
        self._bump("hits")
        if touch:
            self._touch(entry)
        return data

    def get_array(self, key: CacheKey, *, touch: bool = True) -> np.ndarray | None:
        """Return a NumPy payload written with :meth:`put_array`, or ``None``.

        Raises :class:`CacheStateError` when the stored payload is not a readable
        ``.npy`` array, so a bytes payload is never silently misread as pixels.
        """
        data = self.get(key, touch=touch)
        if data is None:
            return None
        try:
            return np.load(io.BytesIO(data), allow_pickle=False)
        except (ValueError, OSError) as exc:
            raise CacheStateError(f"payload for {key.fingerprint} is not an npy array") from exc

    def path(self, key: CacheKey, *, touch: bool = True) -> Path | None:
        """Return the payload path after a fast size check (no full-checksum read).

        Use this when a reader such as rasterio opens the file itself. Call
        :meth:`get` when the caller needs checksum verification in-process.
        """
        entry = self.entry(key, verify=False, quarantine=True)
        if entry is None:
            self._bump("misses")
            return None
        self._bump("hits")
        if touch:
            self._touch(entry)
        return entry.payload_path

    # -- writes --------------------------------------------------------

    def _commit(
        self,
        key: CacheKey,
        *,
        payload_path: Path,
        checksum: str,
        size_bytes: int,
        chunk_count: int,
        content_type: str,
        extra: Mapping[str, Any] | None,
    ) -> CacheEntry:
        metadata = {
            **key.to_payload(),
            "cache_key": key.fingerprint,
            "sha256": checksum,
            "size_bytes": size_bytes,
            "chunk_count": chunk_count,
            "content_type": content_type,
            "created_at": utc_now_iso(),
            "extra": dict(extra or {}),
        }
        metadata_path = payload_path.with_name(ENTRY_METADATA_NAME)
        atomic_write_bytes(
            metadata_path, (json.dumps(metadata, sort_keys=True, indent=2) + "\n").encode("utf-8")
        )
        shutil.rmtree(payload_path.with_name(ENTRY_CHUNK_DIR), ignore_errors=True)
        self._bump("puts")
        entry = CacheEntry(
            key=key,
            directory=payload_path.parent,
            payload_path=payload_path,
            metadata_path=metadata_path,
            checksum=checksum,
            size_bytes=size_bytes,
            chunk_count=chunk_count,
            created_at=str(metadata["created_at"]),
            content_type=content_type,
            extra=dict(extra or {}),
        )
        if self.auto_evict:
            self.evict()
        return entry

    def put(
        self,
        key: CacheKey,
        data: bytes,
        *,
        content_type: str = "application/octet-stream",
        extra: Mapping[str, Any] | None = None,
    ) -> CacheEntry:
        """Atomically store a complete payload; metadata is the commit marker."""
        payload = bytes(data)
        self._ensure_root()
        self.budget.check_disk(self.root, len(payload))
        directory = self._entry_dir(key)
        with self.lock(key):
            directory.mkdir(parents=True, exist_ok=True)
            payload_path = directory / ENTRY_PAYLOAD_NAME
            atomic_write_bytes(payload_path, payload)
            return self._commit(
                key,
                payload_path=payload_path,
                checksum=sha256_bytes(payload),
                size_bytes=len(payload),
                chunk_count=1,
                content_type=content_type,
                extra=extra,
            )

    def put_array(
        self,
        key: CacheKey,
        array: np.ndarray,
        *,
        extra: Mapping[str, Any] | None = None,
    ) -> CacheEntry:
        """Store a NumPy array with ``allow_pickle=False`` (no executable payloads)."""
        buffer = io.BytesIO()
        np.save(buffer, np.asarray(array), allow_pickle=False)
        return self.put(key, buffer.getvalue(), content_type="application/x-npy", extra=extra)

    def _put_window(self, key: CacheKey, result: Any) -> CachedWindow:
        data = np.asarray(result.data, dtype="float64")
        valid = np.asarray(result.valid_mask, dtype=bool)
        if data.shape != valid.shape:
            raise CacheStateError(
                f"window data shape {data.shape} does not match mask {valid.shape}"
            )
        transform = tuple(float(value) for value in tuple(result.transform)[:6])
        extra = {
            "window": list(result.window),
            "transform": list(transform),
            "crs_wkt": result.crs_wkt,
            "crs_name": result.crs_name,
            "band": int(result.band),
        }
        buffer = io.BytesIO()
        np.savez(buffer, data=data, valid_mask=valid)
        payload = buffer.getvalue()
        self._ensure_root()
        self.budget.check_disk(self.root, len(payload))
        directory = self._entry_dir(key)
        directory.mkdir(parents=True, exist_ok=True)
        payload_path = directory / ENTRY_PAYLOAD_NAME
        atomic_write_bytes(payload_path, payload)
        entry = self._commit(
            key,
            payload_path=payload_path,
            checksum=sha256_bytes(payload),
            size_bytes=len(payload),
            chunk_count=1,
            content_type="application/x-npz",
            extra=extra,
        )
        return CachedWindow(
            key=key,
            data=data,
            valid_mask=valid,
            window=tuple(int(value) for value in result.window),
            transform=transform,  # type: ignore[arg-type]
            crs_wkt=result.crs_wkt,
            crs_name=result.crs_name,
            band=int(result.band),
            checksum=entry.checksum,
            size_bytes=entry.size_bytes,
        )

    def put_window(self, key: CacheKey, result: Any) -> CachedWindow:
        """Store one ``RasterWindowResult``-like object (data, mask, georeferencing)."""
        with self.lock(key):
            return self._put_window(key, result)

    def get_window(self, key: CacheKey, *, touch: bool = True) -> CachedWindow | None:
        """Return a cached window payload, or ``None`` on miss/corruption."""
        data = self.get(key, touch=touch)
        if data is None:
            return None
        entry = self.entry(key, verify=False)
        assert entry is not None  # get() just verified it
        try:
            with np.load(io.BytesIO(data), allow_pickle=False) as payload:
                values = np.asarray(payload["data"], dtype="float64")
                valid = np.asarray(payload["valid_mask"], dtype=bool)
        except (KeyError, ValueError, OSError) as exc:
            raise CacheStateError(
                f"payload for {key.fingerprint} is not a cached window bundle"
            ) from exc
        extra = entry.extra
        try:
            window = tuple(int(value) for value in extra["window"])
            transform = tuple(float(value) for value in extra["transform"])
        except (KeyError, TypeError, ValueError) as exc:
            raise CacheStateError(f"window metadata for {key.fingerprint} is incomplete") from exc
        return CachedWindow(
            key=key,
            data=values,
            valid_mask=valid,
            window=window,  # type: ignore[arg-type]
            transform=transform,  # type: ignore[arg-type]
            crs_wkt=extra.get("crs_wkt"),
            crs_name=extra.get("crs_name"),
            band=int(extra.get("band", key.band)),
            checksum=entry.checksum,
            size_bytes=entry.size_bytes,
        )

    def chunked_write(self, key: CacheKey, chunk_count: int) -> ChunkedWrite:
        """Open (or resume) a chunked write for ``key``."""
        self._ensure_root()
        self.budget.check_disk(self.root, 0)
        return ChunkedWrite(self, key, chunk_count)

    def get_or_compute(
        self,
        key: CacheKey,
        compute: Callable[[], bytes],
        *,
        ram_bytes: int | None = None,
        content_type: str = "application/octet-stream",
        extra: Mapping[str, Any] | None = None,
        lock_timeout: float = DEFAULT_LOCK_TIMEOUT_S,
    ) -> bytes:
        """Return cached bytes or compute them exactly once under the key lock.

        With ``ram_bytes`` the computation also holds one heavy-job slot and that
        much RAM reservation for its duration. The lock makes the read-check and
        the write one critical section, so concurrent callers do not duplicate a
        provider read.
        """
        cached = self.get(key)
        if cached is not None:
            return cached
        with self.lock(key, timeout=lock_timeout):
            cached = self.get(key)
            if cached is not None:
                return cached
            if ram_bytes is None:
                data = compute()
            else:
                with self.budget.heavy_job(ram_bytes):
                    data = compute()
            payload = bytes(data)
            self.budget.check_disk(self.root, len(payload))
            directory = self._entry_dir(key)
            directory.mkdir(parents=True, exist_ok=True)
            payload_path = directory / ENTRY_PAYLOAD_NAME
            atomic_write_bytes(payload_path, payload)
            self._commit(
                key,
                payload_path=payload_path,
                checksum=sha256_bytes(payload),
                size_bytes=len(payload),
                chunk_count=1,
                content_type=content_type,
                extra=extra,
            )
            return payload

    # -- eviction and maintenance --------------------------------------

    def iter_entries(self) -> Iterator[CacheEntry]:
        """Yield committed entries without verifying payload checksums."""
        base = self.root / "entries"
        if not base.is_dir():
            return
        for metadata_path in sorted(base.glob("*/*/" + ENTRY_METADATA_NAME)):
            payload_path = metadata_path.with_name(ENTRY_PAYLOAD_NAME)
            if not payload_path.is_file():
                continue
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            directory = metadata_path.parent
            try:
                key = CacheKey(
                    source_id=metadata.get("source_id"),  # type: ignore[arg-type]
                    source_version=metadata.get("source_version"),  # type: ignore[arg-type]
                    window=RasterWindow(**metadata["window"]),
                    band=metadata["band"],
                    options_json=_canonical(metadata.get("options") or {}),
                )
            except (TypeError, KeyError, InvalidCacheKey):
                continue
            yield CacheEntry(
                key=key,
                directory=directory,
                payload_path=payload_path,
                metadata_path=metadata_path,
                checksum=str(metadata.get("sha256", "")),
                size_bytes=int(metadata.get("size_bytes", payload_path.stat().st_size)),
                chunk_count=int(metadata.get("chunk_count", 1) or 1),
                created_at=str(metadata.get("created_at", "")),
                content_type=str(metadata.get("content_type", "application/octet-stream")),
                extra=dict(metadata.get("extra") or {}),
            )

    def evict(
        self,
        *,
        max_bytes: int | None = None,
        max_entries: int | None = None,
        dry_run: bool = False,
    ) -> EvictionReport:
        """Remove least-recently-used entries until both limits are met."""
        byte_limit = self.max_bytes if max_bytes is None else max_bytes
        entry_limit = self.max_entries if max_entries is None else max_entries
        entries = sorted(self.iter_entries(), key=lambda item: item.last_used_at)
        total_bytes = sum(entry.size_bytes for entry in entries)
        total_entries = len(entries)
        removed: list[str] = []
        removed_bytes = 0
        for entry in entries:
            over_bytes = total_bytes > byte_limit
            over_entries = total_entries > entry_limit
            if not (over_bytes or over_entries):
                break
            if not dry_run:
                shutil.rmtree(entry.directory, ignore_errors=True)
            removed.append(entry.key.fingerprint)
            removed_bytes += entry.size_bytes
            total_bytes -= entry.size_bytes
            total_entries -= 1
        if removed and not dry_run:
            self._bump("evictions", len(removed))
            self._bump("evicted_bytes", removed_bytes)
        if dry_run:
            remaining_entries = total_entries
            remaining_bytes = total_bytes
        else:
            remaining = list(self.iter_entries())
            remaining_entries = len(remaining)
            remaining_bytes = sum(entry.size_bytes for entry in remaining)
        return EvictionReport(
            removed=tuple(removed),
            removed_bytes=removed_bytes,
            remaining_entries=remaining_entries,
            remaining_bytes=remaining_bytes,
            dry_run=dry_run,
        )

    def delete(self, key: CacheKey) -> bool:
        directory = self._entry_dir(key)
        if not directory.exists():
            return False
        shutil.rmtree(directory, ignore_errors=True)
        return True

    def clear(self, *, quarantine: bool = True) -> int:
        """Remove all entries (and quarantine) below the root; returns entries removed."""
        removed = 0
        for entry in list(self.iter_entries()):
            if self.delete(entry.key):
                removed += 1
        shutil.rmtree(self.root / "entries", ignore_errors=True)
        if quarantine:
            shutil.rmtree(self.root / "quarantine", ignore_errors=True)
        return removed


def default_cache_root(env: Mapping[str, str] | None = None) -> Path:
    """``ANALOG_RASTER_CACHE_ROOT`` or ``<cache>/raster``; never creates directories."""
    values = dict(os.environ if env is None else env)
    raw = values.get(ENV_ROOT, "").strip()
    if raw:
        return Path(raw).expanduser()
    return data_dir("cache") / "raster"


def cache_from_env(
    env: Mapping[str, str] | None = None,
    *,
    root: Path | str | None = None,
) -> RasterCache:
    """Build a cache from environment values; root defaults to the operator cache dir."""
    values = dict(os.environ if env is None else env)
    return RasterCache(root if root is not None else default_cache_root(values), env=values)


def read_window_cached(
    cache: RasterCache,
    source: Any,
    *,
    window: RasterWindow | tuple[int, int, int, int],
    band: int = 1,
    options: Mapping[str, Any] | None = None,
) -> CachedWindow:
    """Read one bounded window through ``cache`` and the source's own reader.

    ``source`` is a ``RasterSource`` (or a compatible object) with
    ``dataset_id``/``uri``, a required ``version`` or ``declared_checksum``, and a
    ``read_window(window=..., band=...)`` method returning ``data``, ``valid_mask``,
    ``window``, ``transform``, ``crs_wkt`` and ``crs_name``. A cached window is
    returned without opening the source; a miss reads once under the per-key lock.
    The cache stores a copy of the read; it never fabricates or zero-fills values.
    """
    key = CacheKey.for_source(source, window=window, band=band, options=options)
    cached = cache.get_window(key)
    if cached is not None:
        return cached
    with cache.lock(key):
        cached = cache.get_window(key)
        if cached is not None:
            return cached
        result = source.read_window(window=key.window.as_tuple(), band=band)
        return cache._put_window(key, result)  # noqa: SLF001 - same module


__all__ = [
    "CACHE_FORMAT_VERSION",
    "CACHE_STATUSES",
    "DEFAULT_HEAVY_JOBS",
    "ENV_HEAVY_JOBS",
    "ENV_MAX_BYTES",
    "ENV_MIN_FREE_DISK_BYTES",
    "ENV_ROOT",
    "STATUS_CORRUPT",
    "STATUS_HIT",
    "STATUS_MISS",
    "STATUS_PARTIAL",
    "BudgetExceeded",
    "BudgetLimits",
    "CacheEntry",
    "CacheKey",
    "CacheLockTimeout",
    "CacheProbe",
    "CacheStateError",
    "CachedWindow",
    "ChunkedWrite",
    "EvictionReport",
    "InsufficientDisk",
    "InvalidCacheKey",
    "RasterCache",
    "RasterCacheError",
    "RasterWindow",
    "ResourceBudget",
    "budget_limits_from_env",
    "cache_from_env",
    "default_cache_root",
    "read_window_cached",
    "sha256_file",
    "utc_now_iso",
]
