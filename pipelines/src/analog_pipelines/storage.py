"""Object storage adapters for reproducible, large pipeline artifacts.

The repository keeps three logical layers separate:

* Git: source code, small JSON manifests, snapshots, and documentation.
* Object storage (local directory, or S3/R2): large, content-addressed artifacts.
* SQLite: a rebuildable, per-instance index.

This module deliberately exposes a very small interface. Callers do not need credentials:
``store_from_env()`` returns a local filesystem store unless an S3-compatible backend is
explicitly requested. Cloud credentials are read from environment variables only and are
never prompted for, logged, or embedded in ``describe()``.

Optional dependency: ``boto3`` is imported lazily/optionally. The API, CI, and the local
demo paths never require it.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

DEFAULT_LOCAL_ROOT = "data/object-store"
_DOTENV_LOADED = False
ENV_BACKEND = "ANALOG_OBJECT_STORE_BACKEND"
ENV_ENDPOINT = "ANALOG_OBJECT_STORE_ENDPOINT"
ENV_BUCKET = "ANALOG_OBJECT_STORE_BUCKET"
ENV_REGION = "ANALOG_OBJECT_STORE_REGION"
ENV_ROOT = "ANALOG_OBJECT_STORE_ROOT"
ENV_ACCESS_KEY = "AWS_ACCESS_KEY_ID"
ENV_SECRET_KEY = "AWS_SECRET_ACCESS_KEY"

S3_BACKENDS = frozenset({"s3", "r2", "s3-compatible"})
LOCAL_BACKENDS = frozenset({"", "local", "filesystem", "fs"})

_GITIGNORE = """# Created by LocalObjectStore. Artifacts are reproducible; do not commit them.
*
!.gitignore
"""


class ObjectStoreError(RuntimeError):
    """Base class for storage failures."""


class ObjectStoreNotConfigured(ObjectStoreError):
    """The requested backend is incomplete or its optional dependency is missing."""


class InvalidObjectKey(ObjectStoreError):
    """A key is absolute, contains traversal segments, or is otherwise unsafe."""


class ObjectNotFound(ObjectStoreError):
    """The requested key does not exist."""


def normalize_key(key: str) -> str:
    """Return a safe POSIX-style key or raise :class:`InvalidObjectKey`.

    Keys are relative, forward-slash separated, and may not contain empty, ``.`` or
    ``..`` segments. Absolute paths, URL schemes, backslashes, and trailing slashes are
    rejected so a key can never escape the configured store root.
    """
    if not isinstance(key, str) or not key:
        raise InvalidObjectKey("object key must be a non-empty string")
    if key.startswith("/") or key.startswith("\\") or "\\" in key:
        raise InvalidObjectKey(f"object key must use relative POSIX separators: {key!r}")
    if "://" in key:
        raise InvalidObjectKey(f"object key must not be a URL: {key!r}")
    if PurePosixPath(key).is_absolute():
        raise InvalidObjectKey(f"object key must be relative: {key!r}")
    parts = key.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise InvalidObjectKey(f"object key contains an empty/dot/dotdot segment: {key!r}")
    return "/".join(parts)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class ObjectStore(Protocol):
    """Narrow storage interface shared by local and S3-compatible implementations."""

    def put_bytes(self, key: str, data: bytes, content_type: str | None = None) -> str: ...

    def get_bytes(self, key: str) -> bytes: ...

    def exists(self, key: str) -> bool: ...

    def describe(self) -> dict[str, Any]: ...


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    temporary_path = Path(temporary)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


class LocalObjectStore:
    """Filesystem implementation with atomic writes and SHA-256 sidecars.

    The default root is ``data/object-store`` relative to the current working directory.
    A self-ignoring ``.gitignore`` is created inside the root so artifacts never enter
    Git even when the repository-level ignore file has not been updated.
    """

    def __init__(self, root: Path | str = Path(DEFAULT_LOCAL_ROOT)) -> None:
        self.root = Path(root).expanduser()

    def _path(self, key: str) -> Path:
        safe = normalize_key(key)
        path = (self.root / safe).resolve()
        root = self.root.resolve()
        if path != root and root not in path.parents:
            raise InvalidObjectKey(f"object key escapes the store root: {key!r}")
        return path

    def _sidecar(self, key: str) -> Path:
        return self._path(key).with_name(self._path(key).name + ".sha256")

    def _ensure_root(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        ignore = self.root / ".gitignore"
        if not ignore.exists():
            atomic_write_bytes(ignore, _GITIGNORE.encode("utf-8"))

    def put_bytes(self, key: str, data: bytes, content_type: str | None = None) -> str:
        del content_type  # filesystem storage has no content-type metadata
        path = self._path(key)
        self._ensure_root()
        atomic_write_bytes(path, data)
        atomic_write_bytes(self._sidecar(key), f"{sha256_bytes(data)}\n".encode())
        return normalize_key(key)

    def get_bytes(self, key: str) -> bytes:
        path = self._path(key)
        try:
            return path.read_bytes()
        except FileNotFoundError as exc:
            raise ObjectNotFound(f"object not found: {key}") from exc

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def checksum(self, key: str) -> str | None:
        """Return the recorded SHA-256 for ``key``, or ``None`` without a sidecar."""
        path = self._sidecar(key)
        try:
            text = path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            return None
        digest = text.split()[0] if text else ""
        return digest if len(digest) == 64 else None

    def manifest(self, prefix: str = "") -> dict[str, Any]:
        """Return a sorted checksum manifest of stored keys (sidecars excluded)."""
        if not self.root.is_dir():
            return {"backend": "local", "root": str(self.root), "files": []}
        base = self.root
        if prefix:
            base = self._path(prefix)
        files: list[dict[str, Any]] = []
        for path in sorted(base.rglob("*")):
            if not path.is_file() or path.name == ".gitignore" or path.name.endswith(".sha256"):
                continue
            relative = path.relative_to(self.root).as_posix()
            files.append(
                {
                    "key": relative,
                    "sha256": self.checksum(relative) or sha256_file(path),
                    "bytes": path.stat().st_size,
                }
            )
        return {"backend": "local", "root": str(self.root), "files": files}

    def describe(self) -> dict[str, Any]:
        return {"backend": "local", "root": str(self.root), "atomic_writes": True}


class S3CompatibleObjectStore:
    """S3-compatible backend for Cloudflare R2 or AWS S3.

    All configuration comes from environment variables. Credentials are stored only on
    the instance, never printed by ``describe()``. No bucket, account, or endpoint value
    is hard-coded.
    """

    def __init__(self, env: Mapping[str, str] | None = None) -> None:
        values = dict(os.environ if env is None else env)
        required = (ENV_ENDPOINT, ENV_BUCKET, ENV_REGION, ENV_ACCESS_KEY, ENV_SECRET_KEY)
        missing = [name for name in required if not values.get(name, "").strip()]
        if missing:
            raise ObjectStoreNotConfigured(
                "S3-compatible object store is not configured; missing environment "
                f"variable(s): {', '.join(missing)}. Set {ENV_ENDPOINT}, {ENV_BUCKET}, "
                f"{ENV_REGION}, {ENV_ACCESS_KEY}, {ENV_SECRET_KEY}. Credentials are never "
                "prompted for."
            )
        self.endpoint = values[ENV_ENDPOINT].strip()
        self.bucket = values[ENV_BUCKET].strip()
        self.region = values[ENV_REGION].strip()
        self._access_key = values[ENV_ACCESS_KEY].strip()
        self._secret_key = values[ENV_SECRET_KEY].strip()
        try:
            import boto3  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - depends on optional install
            raise ObjectStoreNotConfigured(
                "S3-compatible object store requested but the optional 'boto3' package is "
                "not installed. Install it only where cloud sync is needed: "
                "`uv add boto3`."
            ) from exc
        self._boto3 = boto3
        self._client: Any = None

    def _s3(self) -> Any:
        if self._client is None:
            self._client = self._boto3.client(
                "s3",
                endpoint_url=self.endpoint,
                region_name=self.region,
                aws_access_key_id=self._access_key,
                aws_secret_access_key=self._secret_key,
            )
        return self._client

    def put_bytes(self, key: str, data: bytes, content_type: str | None = None) -> str:
        safe = normalize_key(key)
        kwargs: dict[str, Any] = {"Bucket": self.bucket, "Key": safe, "Body": data}
        if content_type:
            kwargs["ContentType"] = content_type
        try:
            self._s3().put_object(**kwargs)
        except Exception as exc:
            raise ObjectStoreError(f"failed to upload {safe}: {exc}") from exc
        return safe

    def get_bytes(self, key: str) -> bytes:
        safe = normalize_key(key)
        try:
            response = self._s3().get_object(Bucket=self.bucket, Key=safe)
            return response["Body"].read()
        except Exception as exc:
            if _is_not_found(exc):
                raise ObjectNotFound(f"object not found: {safe}") from exc
            raise ObjectStoreError(f"failed to download {safe}: {exc}") from exc

    def exists(self, key: str) -> bool:
        safe = normalize_key(key)
        try:
            self._s3().head_object(Bucket=self.bucket, Key=safe)
            return True
        except Exception as exc:
            if _is_not_found(exc):
                return False
            raise ObjectStoreError(f"failed to check {safe}: {exc}") from exc

    def describe(self) -> dict[str, Any]:
        return {
            "backend": "s3",
            "endpoint": self.endpoint,
            "bucket": self.bucket,
            "region": self.region,
            "credentials": "environment",
        }


def _is_not_found(exc: Exception) -> bool:
    response = getattr(exc, "response", None)
    if not isinstance(response, dict):
        return False
    code = str(response.get("Error", {}).get("Code", ""))
    return code in {"404", "NoSuchKey", "NotFound"}


def load_local_env(path: str | Path | None = None) -> None:
    """Load a local, gitignored .env for operator commands without overriding real env vars.

    Used by operator storage/pipeline paths; the API does not need this and tests run
    with an injected environment. Missing python-dotenv or .env is not an error.
    """
    global _DOTENV_LOADED
    if _DOTENV_LOADED or os.environ.get("ANALOG_SKIP_DOTENV") == "1":
        return
    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover - optional in minimal installs
        return
    load_dotenv(dotenv_path=path, override=False)
    _DOTENV_LOADED = True


def store_from_env(env: Mapping[str, str] | None = None) -> ObjectStore:
    """Return an S3-compatible store only when explicitly configured, else local.

    Explicit configuration means ``ANALOG_OBJECT_STORE_BACKEND`` is one of
    ``s3``/``r2``/``s3-compatible``, or an endpoint and bucket are both present. An
    explicitly requested but incomplete S3 configuration raises
    :class:`ObjectStoreNotConfigured` instead of silently falling back to local storage.
    A local .env is loaded for operator convenience unless an explicit env mapping is passed.
    """
    if env is None:
        load_local_env()
    values = dict(os.environ if env is None else env)
    backend = values.get(ENV_BACKEND, "").strip().lower()
    endpoint = values.get(ENV_ENDPOINT, "").strip()
    bucket = values.get(ENV_BUCKET, "").strip()

    if backend in S3_BACKENDS or (not backend and endpoint and bucket):
        return S3CompatibleObjectStore(values)
    if backend in LOCAL_BACKENDS:
        return LocalObjectStore(values.get(ENV_ROOT, DEFAULT_LOCAL_ROOT))
    raise ObjectStoreNotConfigured(
        f"unknown {ENV_BACKEND}={backend!r}; use 'local', 's3', 'r2', or 's3-compatible'"
    )
