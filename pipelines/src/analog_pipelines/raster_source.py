"""Raster source adapters and chunked IO primitives for pipeline data infrastructure.

This module provides Goal C building blocks used by acquisition (A), feature and
extraction flows (B), and cache/checkpoint layers (D):

* source adapters: HTTP byte-range, GDAL ``/vsicurl`` (HTTPS range reads), and
  local staged tiles;
* explicit provenance metadata: checksum, version, license, CRS, nodata and
  resolution, plus source identity and observation provenance;
* chunked download primitives that expose byte offsets/ranges and can resume a
  partially staged file.

The module is intentionally independent of feature calculation. It never invents
scientific data, never zero-fills missing values, and never treats a cached or
staged tile as a new observation: metadata must mark staged/cached artifacts as
non-observational provenance unless an upstream process explicitly records a
separately verified observation event.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO, Protocol, runtime_checkable

import numpy as np
import rasterio
from rasterio.windows import Window

__all__ = [
    "ByteRange",
    "ChunkRecord",
    "ChunkedDownloadState",
    "FetchResult",
    "HttpRangeSource",
    "LocalStagedSource",
    "RasterMetadata",
    "RasterReadError",
    "RasterSource",
    "RasterSourceError",
    "ResumeNotSupportedError",
    "VsiCurlSource",
    "iter_byte_ranges",
    "open_rasterio_dataset",
    "open_source",
]

DEFAULT_CHUNK_SIZE = 1024 * 1024
DEFAULT_TIMEOUT_S = 30.0
DEFAULT_MAX_RETRIES = 2
DEFAULT_GDAL_ENV: Mapping[str, str] = {
    "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
    "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif,.tiff,.vrt,.img,.nc,.h5",
    "GDAL_HTTP_TIMEOUT": "30",
    "GDAL_HTTP_CONNECTTIMEOUT": "10",
    "GDAL_HTTP_MAX_RETRY": "0",
    "VSI_CACHE": "TRUE",
    "VSI_CACHE_SIZE": "33554432",
}


class RasterSourceError(RuntimeError):
    """Base error for raster source access and staging failures."""


class RasterReadError(RasterSourceError):
    """Raised when a raster cannot be opened or read as requested."""


class ResumeNotSupportedError(RasterSourceError):
    """Raised when a caller requests resume but the source cannot satisfy it."""


def utc_now_iso() -> str:
    """Return a UTC timestamp suitable for provenance records."""

    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class ByteRange:
    """A closed-open byte interval ``[start, end)``."""

    start: int
    end: int

    def __post_init__(self) -> None:
        if self.start < 0 or self.end < self.start:
            raise ValueError(f"invalid byte range [{self.start}, {self.end})")

    @property
    def size(self) -> int:
        return self.end - self.start

    def as_http_header(self, total_size: int | None = None) -> str:
        last = self.end - 1
        if total_size is not None and self.end >= total_size:
            return f"bytes={self.start}-"
        return f"bytes={self.start}-{last}"


def iter_byte_ranges(
    total_size: int | None,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    *,
    start: int = 0,
) -> Iterator[ByteRange]:
    """Yield deterministic byte ranges for chunked transfer.

    ``total_size`` may be ``None`` when unknown; in that case a single open-ended
    range from ``start`` is yielded and the caller is responsible for streaming
    until EOF. Offsets/ranges are always explicit in yielded values.
    """

    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    if start < 0:
        raise ValueError("start must be >= 0")
    if total_size is not None and start > total_size:
        raise ValueError(f"start {start} exceeds total_size {total_size}")
    if total_size is None:
        yield ByteRange(start, start)
        return
    offset = start
    while offset < total_size:
        end = min(offset + chunk_size, total_size)
        yield ByteRange(offset, end)
        offset = end


@dataclass(frozen=True, slots=True)
class RasterMetadata:
    """Explicit provenance and raster-description metadata.

    ``is_observation`` must remain ``False`` for cached/staged copies unless a
    separate, verified acquisition event is recorded by the owning workflow.
    """

    source_uri: str
    source_kind: str
    dataset_id: str | None = None
    version: str | None = None
    license: str | None = None
    license_url: str | None = None
    attribution: str | None = None
    checksum: str | None = None
    checksum_algorithm: str = "sha256"
    crs: str | None = None
    nodata: float | int | None = None
    resolution: tuple[float, float] | None = None
    width: int | None = None
    height: int | None = None
    count: int | None = None
    dtype: str | None = None
    transform: tuple[float, float, float, float, float, float] | None = None
    bounds: tuple[float, float, float, float] | None = None
    data_kind: str = "staged_artifact"
    is_observation: bool = False
    access_timestamp: str | None = None
    etag: str | None = None
    last_modified: str | None = None
    content_length: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.is_observation and self.data_kind == "staged_artifact":
            raise ValueError(
                "a staged artifact cannot be marked as an observation; record an "
                "explicit observed provenance event instead"
            )

    def with_updates(self, **updates: Any) -> RasterMetadata:
        return replace(self, **updates)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        # Preserve a stable, explicit statement for downstream checkpoint layers.
        data["is_observation"] = bool(self.is_observation)
        return data


@dataclass(frozen=True, slots=True)
class ChunkRecord:
    """One transferred chunk with explicit offset/range information."""

    index: int
    offset: int
    length: int
    end_offset: int
    checksum: str | None = None

    @property
    def byte_range(self) -> ByteRange:
        return ByteRange(self.offset, self.end_offset)


@dataclass(frozen=True, slots=True)
class ChunkedDownloadState:
    """State describing a partial or completed staged transfer.

    Cache/checkpoint layers can persist this structure and later resume from
    ``next_offset`` when the source supports range requests.
    """

    source_uri: str
    destination: str
    total_size: int | None
    bytes_received: int
    next_offset: int
    complete: bool
    chunk_size: int
    chunks: tuple[ChunkRecord, ...] = ()
    etag: str | None = None
    last_modified: str | None = None
    checksum: str | None = None
    checksum_algorithm: str = "sha256"
    updated_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["chunks"] = [asdict(chunk) for chunk in self.chunks]
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ChunkedDownloadState:
        chunks = tuple(ChunkRecord(**chunk) for chunk in data.get("chunks", []))
        return cls(
            source_uri=str(data["source_uri"]),
            destination=str(data["destination"]),
            total_size=(int(data["total_size"]) if data.get("total_size") is not None else None),
            bytes_received=int(data.get("bytes_received", 0)),
            next_offset=int(data.get("next_offset", data.get("bytes_received", 0))),
            complete=bool(data.get("complete", False)),
            chunk_size=int(data.get("chunk_size", DEFAULT_CHUNK_SIZE)),
            chunks=chunks,
            etag=data.get("etag"),
            last_modified=data.get("last_modified"),
            checksum=data.get("checksum"),
            checksum_algorithm=str(data.get("checksum_algorithm", "sha256")),
            updated_at=str(data.get("updated_at", utc_now_iso())),
        )


@dataclass(frozen=True, slots=True)
class FetchResult:
    """Result of a staged/chunked fetch operation."""

    path: Path
    metadata: RasterMetadata
    state: ChunkedDownloadState
    resumed: bool
    from_cache: bool = False


@runtime_checkable
class RasterSource(Protocol):
    """Common interface for raster acquisition/access adapters."""

    source_kind: str
    uri: str

    def describe(self) -> RasterMetadata:
        """Return explicit provenance and raster metadata."""

    def open_dataset(self, **open_kwargs: Any):  # pragma: no cover - protocol shape
        """Open a rasterio dataset for read access."""


def _safe_join(base: Path, relative: Path) -> Path:
    target = (base / relative).resolve()
    base_resolved = base.resolve()
    if base_resolved != target and base_resolved not in target.parents:
        raise RasterSourceError(f"refusing to write outside staging directory: {target}")
    return target


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".part")
    temp.write_text(text, encoding="utf-8")
    os.replace(temp, path)


def _hash_file(path: Path, algorithm: str = "sha256") -> str:
    digest = hashlib.new(algorithm)
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _hash_bytes(data: bytes, algorithm: str = "sha256") -> str:
    digest = hashlib.new(algorithm)
    digest.update(data)
    return digest.hexdigest()


def _metadata_from_rasterio_dataset(
    dataset: Any, *, source_uri: str, source_kind: str, base: RasterMetadata
) -> RasterMetadata:
    ds_crs = dataset.crs.to_string() if getattr(dataset, "crs", None) else base.crs
    bounds = None
    if getattr(dataset, "bounds", None) is not None:
        b = dataset.bounds
        bounds = (float(b.left), float(b.bottom), float(b.right), float(b.top))
    res = None
    if getattr(dataset, "res", None) is not None:
        try:
            res = (float(dataset.res[0]), float(dataset.res[1]))
        except Exception:  # noqa: BLE001 - defensive
            res = base.resolution
    transform = None
    if getattr(dataset, "transform", None) is not None:
        try:
            t = dataset.transform
            transform = (
                float(t.a),
                float(t.b),
                float(t.c),
                float(t.d),
                float(t.e),
                float(t.f),
            )
        except Exception:  # noqa: BLE001 - defensive
            transform = base.transform
    nodata = dataset.nodata if getattr(dataset, "nodata", None) is not None else base.nodata
    return base.with_updates(
        source_uri=source_uri,
        source_kind=source_kind,
        crs=ds_crs,
        nodata=nodata,
        resolution=res,
        width=int(dataset.width) if getattr(dataset, "width", None) is not None else None,
        height=int(dataset.height) if getattr(dataset, "height", None) is not None else None,
        count=int(dataset.count) if getattr(dataset, "count", None) is not None else None,
        dtype=str(dataset.dtypes[0]) if getattr(dataset, "dtypes", None) else base.dtype,
        transform=transform,
        bounds=bounds,
    )


@contextmanager
def open_rasterio_dataset(
    source: RasterSource | str | os.PathLike[str],
    *,
    env: Mapping[str, str] | None = None,
    **open_kwargs: Any,
) -> Iterator[Any]:
    """Open a rasterio dataset from a source adapter or path-like target.

    GDAL/VSI configuration is applied through ``rasterio.Env`` when provided or
    when the source exposes ``gdal_env``.
    """

    resolved = open_source(source) if isinstance(source, (str, os.PathLike)) else source
    gdal_env = dict(env or {})
    source_env = getattr(resolved, "gdal_env", None)
    if isinstance(source_env, Mapping):
        for key, value in source_env.items():
            gdal_env.setdefault(str(key), str(value))
    if gdal_env:
        with rasterio.Env(**gdal_env), resolved.open_dataset(**open_kwargs) as dataset:
            yield dataset
    else:
        with resolved.open_dataset(**open_kwargs) as dataset:
            yield dataset


class LocalStagedSource:
    """Adapter for a local already-staged tile or raster file.

    Local files are treated as staged artifacts, not observations, unless the
    caller supplies metadata proving otherwise.
    """

    source_kind = "local_staged"

    def __init__(
        self,
        path: str | os.PathLike[str],
        *,
        metadata: RasterMetadata | None = None,
        compute_checksum: bool = False,
        checksum_algorithm: str = "sha256",
    ) -> None:
        self.path = Path(path)
        self.uri = self.path.as_posix()
        self._metadata = metadata
        self.compute_checksum = compute_checksum
        self.checksum_algorithm = checksum_algorithm

    def describe(self) -> RasterMetadata:
        if not self.path.exists():
            raise RasterReadError(f"local staged raster does not exist: {self.path}")
        stat = self.path.stat()
        base = self._metadata or RasterMetadata(
            source_uri=self.uri,
            source_kind=self.source_kind,
            data_kind="staged_artifact",
            is_observation=False,
        )
        base = base.with_updates(
            source_uri=self.uri,
            source_kind=self.source_kind,
            content_length=stat.st_size,
            access_timestamp=base.access_timestamp or utc_now_iso(),
            checksum_algorithm=self.checksum_algorithm,
        )
        if self.compute_checksum and base.checksum is None:
            base = base.with_updates(checksum=_hash_file(self.path, self.checksum_algorithm))
        try:
            with rasterio.open(self.path) as dataset:
                base = _metadata_from_rasterio_dataset(
                    dataset, source_uri=self.uri, source_kind=self.source_kind, base=base
                )
        except Exception:  # noqa: BLE001 - metadata best effort for local files
            return base
        return base

    def open_dataset(self, **open_kwargs: Any):
        if not self.path.exists():
            raise RasterReadError(f"local staged raster does not exist: {self.path}")
        return rasterio.open(self.path, **open_kwargs)

    def read_window(
        self,
        window: Window | tuple[tuple[int, int], tuple[int, int]] | None = None,
        *,
        indexes: int | Sequence[int] = 1,
        masked: bool = True,
        out_dtype: str = "float32",
        boundless: bool = False,
        fill_value: Any = None,
        **open_kwargs: Any,
    ) -> np.ndarray:
        """Read a window without zero-filling missing data.

        ``masked=True`` by default so nodata/outside areas remain explicit masks.
        """

        with self.open_dataset(**open_kwargs) as dataset:
            data = dataset.read(
                indexes=indexes,
                window=window,
                masked=masked,
                out_dtype=out_dtype,
                boundless=boundless,
                fill_value=fill_value,
            )
            return np.ma.asarray(data) if masked else np.asarray(data)

    def iter_window_chunks(
        self,
        *,
        block_height: int = 512,
        block_width: int = 512,
        indexes: int | Sequence[int] = 1,
        masked: bool = True,
        out_dtype: str = "float32",
        **open_kwargs: Any,
    ) -> Iterator[tuple[Window, np.ndarray]]:
        """Yield raster windows in deterministic chunks for local processing."""

        if block_height <= 0 or block_width <= 0:
            raise ValueError("block_height and block_width must be positive")
        with self.open_dataset(**open_kwargs) as dataset:
            for row_off in range(0, dataset.height, block_height):
                height = min(block_height, dataset.height - row_off)
                for col_off in range(0, dataset.width, block_width):
                    width = min(block_width, dataset.width - col_off)
                    window = Window(col_off=col_off, row_off=row_off, width=width, height=height)
                    data = dataset.read(
                        indexes=indexes,
                        window=window,
                        masked=masked,
                        out_dtype=out_dtype,
                    )
                    yield window, (np.ma.asarray(data) if masked else np.asarray(data))


class VsiCurlSource:
    """GDAL ``/vsicurl`` adapter for remote rasters using HTTP range reads.

    GDAL performs remote range access internally. This adapter exposes explicit
    environment configuration, open/read primitives, and provenance metadata; it
    does not claim a byte-level resumable application download state.
    """

    source_kind = "gdal_vsicurl"

    def __init__(
        self,
        url: str,
        *,
        metadata: RasterMetadata | None = None,
        gdal_env: Mapping[str, str] | None = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        vsi_prefix: str = "/vsicurl/",
    ) -> None:
        if not url.startswith(("http://", "https://")):
            raise ValueError("VsiCurlSource requires an HTTP(S) URL")
        self.http_url = url
        self.uri = url if url.startswith("/vsi") else f"{vsi_prefix}{url}"
        self.timeout_s = float(timeout_s)
        self.gdal_env = dict(DEFAULT_GDAL_ENV)
        self.gdal_env["GDAL_HTTP_TIMEOUT"] = str(max(1, int(self.timeout_s)))
        if gdal_env:
            self.gdal_env.update({str(k): str(v) for k, v in gdal_env.items()})
        self._metadata = metadata

    def describe(self) -> RasterMetadata:
        base = self._metadata or RasterMetadata(
            source_uri=self.http_url,
            source_kind=self.source_kind,
            data_kind="remote_vsi_reference",
            is_observation=False,
        )
        base = base.with_updates(source_uri=self.http_url, source_kind=self.source_kind)
        try:
            with self.open_dataset() as dataset:
                return _metadata_from_rasterio_dataset(
                    dataset, source_uri=self.http_url, source_kind=self.source_kind, base=base
                )
        except Exception:
            return base

    def open_dataset(self, **open_kwargs: Any):
        try:
            with rasterio.Env(**self.gdal_env):
                return rasterio.open(self.uri, **open_kwargs)
        except Exception as exc:  # noqa: BLE001 - GDAL boundary
            raise RasterReadError(f"failed to open {self.uri}: {exc}") from exc

    def read_window(
        self,
        window: Window | tuple[tuple[int, int], tuple[int, int]] | None = None,
        *,
        indexes: int | Sequence[int] = 1,
        masked: bool = True,
        out_dtype: str = "float32",
        boundless: bool = False,
        fill_value: Any = None,
        **open_kwargs: Any,
    ) -> np.ndarray:
        """Read a remote window through GDAL range requests.

        No fill values are invented: callers receive masked/nan-marked data as the
        dataset provides it.
        """

        with self.open_dataset(**open_kwargs) as dataset:
            data = dataset.read(
                indexes=indexes,
                window=window,
                masked=masked,
                out_dtype=out_dtype,
                boundless=boundless,
                fill_value=fill_value,
            )
            return np.ma.asarray(data) if masked else np.asarray(data)

    def read_window_array(
        self,
        window: Window,
        *,
        indexes: int | Sequence[int] = 1,
        **open_kwargs: Any,
    ) -> np.ma.MaskedArray:
        """Alias for masked window reads used by checkpointed extraction flows."""

        data = self.read_window(window, indexes=indexes, masked=True, **open_kwargs)
        return np.ma.asarray(data)


class HttpRangeSource:
    """HTTP source adapter with explicit chunked range downloads and resume.

    This adapter is designed for staging remote rasters/tiles to local storage in
    a resumable, auditable way. It does not interpret a downloaded copy as a new
    observation.
    """

    source_kind = "http_range"

    def __init__(
        self,
        url: str,
        *,
        metadata: RasterMetadata | None = None,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        max_retries: int = DEFAULT_MAX_RETRIES,
        headers: Mapping[str, str] | None = None,
        opener: Callable[..., Any] = urllib.request.urlopen,
        sleeper: Callable[[float], None] = time.sleep,
        checksum_algorithm: str = "sha256",
    ) -> None:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("HttpRangeSource requires an HTTP(S) URL")
        self.url = url
        self.uri = url
        self.chunk_size = int(chunk_size)
        self.timeout_s = float(timeout_s)
        self.max_retries = int(max_retries)
        self.headers = {str(k): str(v) for k, v in (headers or {}).items()}
        self.opener = opener
        self.sleeper = sleeper
        self.checksum_algorithm = checksum_algorithm
        self._metadata = metadata
        if self.chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        if self.max_retries < 0:
            raise ValueError("max_retries must be >= 0")

    def describe(self) -> RasterMetadata:
        base = self._metadata or RasterMetadata(
            source_uri=self.url,
            source_kind=self.source_kind,
            data_kind="remote_http_reference",
            is_observation=False,
        )
        probe = self.probe()
        return base.with_updates(
            source_uri=self.url,
            source_kind=self.source_kind,
            content_length=probe.get("content_length", base.content_length),
            etag=probe.get("etag", base.etag),
            last_modified=probe.get("last_modified", base.last_modified),
            access_timestamp=probe.get("access_timestamp", base.access_timestamp),
            checksum=probe.get("checksum", base.checksum),
        )

    def probe(self) -> dict[str, Any]:
        """Best-effort HEAD probe for length/etag/checkpoint compatibility."""

        request = urllib.request.Request(self.url, method="HEAD", headers=self._request_headers())
        try:
            with self._open_with_retries(request) as response:
                length_text = response.headers.get("Content-Length")
                return {
                    "status": int(getattr(response, "status", 200)),
                    "content_length": int(length_text) if length_text is not None else None,
                    "etag": response.headers.get("ETag"),
                    "last_modified": response.headers.get("Last-Modified"),
                    "accept_ranges": response.headers.get("Accept-Ranges"),
                    "access_timestamp": utc_now_iso(),
                }
        except Exception as exc:  # noqa: BLE001 - probe is optional
            return {
                "status": None,
                "content_length": None,
                "etag": None,
                "last_modified": None,
                "access_timestamp": utc_now_iso(),
                "error": str(exc),
            }

    def open_dataset(self, **open_kwargs: Any):
        """Open the remote raster via GDAL VSI curl for read-only access."""

        vsi = VsiCurlSource(
            self.url,
            metadata=self._metadata,
            timeout_s=self.timeout_s,
            gdal_env=open_kwargs.pop("gdal_env", None),
        )
        return vsi.open_dataset(**open_kwargs)

    def fetch(
        self,
        destination: str | os.PathLike[str],
        *,
        resume: bool = True,
        chunk_size: int | None = None,
        expected_checksum: str | None = None,
        state_path: str | os.PathLike[str] | None = None,
        progress_callback: Callable[[ChunkedDownloadState, ChunkRecord], None] | None = None,
        staging_root: str | os.PathLike[str] | None = None,
    ) -> FetchResult:
        """Download to a local staged file using chunked HTTP ranges.

        When ``resume`` is true and the destination already exists, the transfer
        continues from the current file size if the server provides a compatible
        partial-content response. ``state_path`` (if provided) is written after
        every chunk so checkpoint layers can persist exact offsets/ranges.
        """

        chunk_size = int(chunk_size or self.chunk_size)
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        dest = Path(destination)
        if staging_root is not None:
            dest = _safe_join(
                Path(staging_root), dest if not dest.is_absolute() else Path(dest.name)
            )
        dest.parent.mkdir(parents=True, exist_ok=True)
        state_file = (
            Path(state_path)
            if state_path is not None
            else dest.with_suffix(dest.suffix + ".state.json")
        )

        probe = self.probe()
        total_size = probe.get("content_length")
        existing_state = self._load_state(state_file)
        start_offset = 0
        resumed = False
        prior_chunks: list[ChunkRecord] = []
        mode = "wb"

        if resume and dest.exists():
            start_offset = dest.stat().st_size
            if total_size is not None and start_offset > total_size:
                start_offset = 0
                dest.unlink(missing_ok=True)
            elif start_offset > 0:
                resumed = True
                mode = "r+b" if dest.exists() else "ab"
                if existing_state and existing_state.destination == str(dest):
                    prior_chunks = list(existing_state.chunks)
        elif dest.exists() and not resume:
            dest.unlink(missing_ok=True)
            if state_file.exists():
                state_file.unlink(missing_ok=True)

        if start_offset == 0:
            mode = "wb"
            prior_chunks = []

        if total_size is not None and start_offset == total_size and total_size > 0:
            state = ChunkedDownloadState(
                source_uri=self.url,
                destination=str(dest),
                total_size=total_size,
                bytes_received=total_size,
                next_offset=total_size,
                complete=True,
                chunk_size=chunk_size,
                chunks=tuple(prior_chunks),
                etag=probe.get("etag"),
                last_modified=probe.get("last_modified"),
                checksum=_hash_file(dest, self.checksum_algorithm),
                checksum_algorithm=self.checksum_algorithm,
            )
            _atomic_write_text(state_file, json.dumps(state.to_dict(), indent=2) + "\n")
            metadata = self._staged_metadata(dest, state, probe)
            return FetchResult(
                path=dest, metadata=metadata, state=state, resumed=bool(resumed), from_cache=True
            )

        bytes_received = start_offset
        chunks = list(prior_chunks)
        chunk_index = len(chunks)
        range_iter = iter_byte_ranges(total_size, chunk_size, start=start_offset)

        with dest.open(mode) as handle:
            if start_offset > 0:
                handle.seek(start_offset)
            for byte_range in range_iter:
                response, actual_offset, data = self._read_range(handle, byte_range, total_size)
                if (
                    start_offset > 0
                    and bytes_received == start_offset
                    and actual_offset == 0
                    and byte_range.start > 0
                ):
                    raise ResumeNotSupportedError(
                        f"server did not honor range resume for {self.url}; restart without resume"
                    )
                if actual_offset != bytes_received:
                    handle.seek(actual_offset)
                    bytes_received = actual_offset
                handle.write(data)
                bytes_received += len(data)
                chunk_record = ChunkRecord(
                    index=chunk_index,
                    offset=actual_offset,
                    length=len(data),
                    end_offset=bytes_received,
                    checksum=_hash_bytes(data, self.checksum_algorithm),
                )
                chunks.append(chunk_record)
                chunk_index += 1
                complete = bool(total_size is not None and bytes_received >= total_size)
                state = ChunkedDownloadState(
                    source_uri=self.url,
                    destination=str(dest),
                    total_size=total_size,
                    bytes_received=bytes_received,
                    next_offset=bytes_received,
                    complete=complete or total_size is None,
                    chunk_size=chunk_size,
                    chunks=tuple(chunks),
                    etag=response.headers.get("ETag") or probe.get("etag"),
                    last_modified=response.headers.get("Last-Modified")
                    or probe.get("last_modified"),
                    checksum=None,
                    checksum_algorithm=self.checksum_algorithm,
                )
                _atomic_write_text(state_file, json.dumps(state.to_dict(), indent=2) + "\n")
                if progress_callback is not None:
                    progress_callback(state, chunk_record)
                if total_size is None:
                    # Unknown length: stream until EOF in one request/loop pass.
                    break

        final_size = dest.stat().st_size if dest.exists() else bytes_received
        if total_size is not None and final_size != total_size:
            raise RasterSourceError(
                f"incomplete transfer for {self.url}: expected {total_size} bytes, got {final_size}"
            )
        file_checksum = _hash_file(dest, self.checksum_algorithm) if dest.exists() else None
        if expected_checksum and file_checksum and file_checksum != expected_checksum:
            raise RasterSourceError(
                f"checksum mismatch for {self.url}: expected {expected_checksum}, got {file_checksum}"
            )
        final_state = ChunkedDownloadState(
            source_uri=self.url,
            destination=str(dest),
            total_size=total_size if total_size is not None else final_size,
            bytes_received=final_size,
            next_offset=final_size,
            complete=True,
            chunk_size=chunk_size,
            chunks=tuple(chunks),
            etag=probe.get("etag"),
            last_modified=probe.get("last_modified"),
            checksum=file_checksum,
            checksum_algorithm=self.checksum_algorithm,
        )
        _atomic_write_text(state_file, json.dumps(final_state.to_dict(), indent=2) + "\n")
        metadata = self._staged_metadata(dest, final_state, probe)
        return FetchResult(path=dest, metadata=metadata, state=final_state, resumed=resumed)

    def iter_chunks(
        self,
        *,
        start: int = 0,
        total_size: int | None = None,
        chunk_size: int | None = None,
    ) -> Iterator[tuple[ByteRange, bytes, Mapping[str, str]]]:
        """Stream explicit byte ranges without writing to disk.

        Useful for cache/checkpoint implementations that manage their own storage.
        """

        chunk_size = int(chunk_size or self.chunk_size)
        probe_total = total_size if total_size is not None else self.probe().get("content_length")
        for byte_range in iter_byte_ranges(probe_total, chunk_size, start=start):
            request_headers = self._request_headers()
            if probe_total is not None or byte_range.start > 0 or byte_range.end > byte_range.start:
                request_headers["Range"] = byte_range.as_http_header(probe_total)
            request = urllib.request.Request(self.url, method="GET", headers=request_headers)
            with self._open_with_retries(request) as response:
                headers = {k: v for k, v in response.headers.items()}
                status = int(getattr(response, "status", 200))
                if byte_range.start > 0 and status == 200:
                    raise ResumeNotSupportedError("server ignored requested byte range")
                data = response.read()
                yield byte_range, data, headers
                if probe_total is None:
                    break

    def _staged_metadata(
        self, path: Path, state: ChunkedDownloadState, probe: Mapping[str, Any]
    ) -> RasterMetadata:
        base = self._metadata or RasterMetadata(
            source_uri=self.url,
            source_kind=self.source_kind,
            data_kind="staged_artifact",
            is_observation=False,
        )
        metadata = base.with_updates(
            source_uri=self.url,
            source_kind=self.source_kind,
            data_kind="staged_artifact",
            is_observation=False,
            checksum=state.checksum or base.checksum,
            checksum_algorithm=state.checksum_algorithm,
            content_length=state.total_size,
            etag=probe.get("etag") or state.etag or base.etag,
            last_modified=probe.get("last_modified") or state.last_modified or base.last_modified,
            access_timestamp=utc_now_iso(),
            extra={
                **(base.extra or {}),
                "staged_path": str(path),
                "resume_next_offset": state.next_offset,
                "complete": state.complete,
            },
        )
        try:
            with rasterio.open(path) as dataset:
                metadata = _metadata_from_rasterio_dataset(
                    dataset, source_uri=self.url, source_kind=self.source_kind, base=metadata
                )
        except Exception:
            # Not all staged HTTP payloads are locally readable rasters.
            return metadata
        return metadata

    def _request_headers(self, extra: Mapping[str, str] | None = None) -> dict[str, str]:
        headers = {"User-Agent": "planetary-analog-raster-source/1.0", **self.headers}
        if extra:
            headers.update({str(k): str(v) for k, v in extra.items()})
        return headers

    def _open_with_retries(self, request: urllib.request.Request):
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                return self.opener(request, timeout=self.timeout_s)
            except urllib.error.HTTPError as exc:
                if exc.code in {403, 404, 416}:
                    raise RasterSourceError(f"HTTP {exc.code} for {request.full_url}") from exc
                last_error = exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_error = exc
            if attempt < self.max_retries:
                self.sleeper(1.5 * (attempt + 1))
        raise RasterSourceError(
            f"request failed after {self.max_retries + 1} attempt(s): {request.full_url}: {last_error}"
        )

    def _read_range(
        self,
        handle: BinaryIO,
        byte_range: ByteRange,
        total_size: int | None,
    ) -> tuple[Any, int, bytes]:
        headers = self._request_headers()
        if total_size is not None or byte_range.start > 0 or byte_range.size > 0:
            headers["Range"] = byte_range.as_http_header(total_size)
        request = urllib.request.Request(self.url, method="GET", headers=headers)
        response = self._open_with_retries(request)
        status = int(getattr(response, "status", 200))
        try:
            data = response.read()
        except Exception:
            response.close()
            raise
        actual_offset = byte_range.start
        content_range = response.headers.get("Content-Range")
        if status == 206 and content_range:
            # Format: bytes start-end/total
            try:
                unit, _, rest = content_range.partition(" ")
                if unit == "bytes" and "-" in rest:
                    start_text = rest.split("-", 1)[0]
                    actual_offset = int(start_text)
            except Exception:  # noqa: BLE001 - keep requested offset
                actual_offset = byte_range.start
        elif status == 200 and byte_range.start > 0:
            # Caller decides whether full-body fallback is acceptable.
            actual_offset = 0
        response.close()
        return response, actual_offset, data

    @staticmethod
    def _load_state(state_path: Path) -> ChunkedDownloadState | None:
        try:
            data = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        try:
            return ChunkedDownloadState.from_dict(data)
        except Exception:  # noqa: BLE001 - corrupt state should not block staging
            return None


def open_source(
    source: str | os.PathLike[str] | RasterSource,
    *,
    metadata: RasterMetadata | None = None,
    **kwargs: Any,
) -> RasterSource:
    """Create or pass through a raster source adapter.

    Strings/paths are resolved as local staged sources unless they are explicit
    HTTP(S) URLs or GDAL VSI URLs.
    """

    if isinstance(source, (HttpRangeSource, VsiCurlSource, LocalStagedSource)):
        return source
    if isinstance(source, RasterSource):
        return source
    text = os.fspath(source)
    if text.startswith("/vsicurl/"):
        url = text[len("/vsicurl/") :]
        return VsiCurlSource(url, metadata=metadata, **kwargs)
    if text.startswith(("http://", "https://")):
        if kwargs.pop("prefer_vsicurl", False):
            return VsiCurlSource(text, metadata=metadata, **kwargs)
        return HttpRangeSource(text, metadata=metadata, **kwargs)
    return LocalStagedSource(text, metadata=metadata, **kwargs)
