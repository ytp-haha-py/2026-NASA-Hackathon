"""Shared bounded raster source adapter for the Phase 2 raster infrastructure (Issue #170).

One small, dependency-light seam that Goals A/B/D can read through:

* a local GeoTIFF/COG,
* a locally staged tile (a bounded window previously written by the cache layer),
* a plain HTTPS URL read through GDAL ``/vsicurl`` range requests, or
* an explicit GDAL ``/vsicurl/...`` virtual path.

This module only opens, describes and reads a bounded window. It deliberately does not
own caching, staging, reprojection or terrain extraction; those belong to C2 and Goals
A/B. Nothing is downloaded wholesale: a remote read is opt-in, and the reader reports a
conservative upper bound on the bytes a window can touch.

Provenance (``dataset_id``, ``version``, ``license``, declared checksum) is supplied by
the caller from the verified source record. Missing provenance stays missing and is
reported as such; this module never invents a version, licence or checksum, and it never
fills missing pixels with zeros.
"""

from __future__ import annotations

import hashlib
import math
import os
import re
from collections.abc import Mapping, Sequence
from contextlib import ExitStack
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
from pyproj import CRS, Transformer
from rasterio.errors import RasterioError
from rasterio.transform import Affine
from rasterio.windows import Window, from_bounds
from rasterio.windows import bounds as window_bounds

RASTER_SOURCE_SCHEMA_VERSION = "raster-source-1.0.0"
DEFAULT_MAX_PIXELS = 1_000_000

# Conservative GDAL settings. /vsicurl issues HTTP Range requests for the header and
# the requested window; directory listing is disabled so an unknown URL is not probed.
_BASE_GDAL_ENV: dict[str, str] = {
    "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
}
_REMOTE_GDAL_ENV: dict[str, str] = {
    "GDAL_HTTP_TIMEOUT": "60",
    "GDAL_HTTP_MAX_RETRY": "2",
    "GDAL_HTTP_RETRY_DELAY": "1",
}
_UNSUPPORTED_SCHEMES = ("s3://", "gs://", "ftp://", "file://")
_METRE_UNITS = {"m", "metre", "meter", "metres", "meters"}
_CHECKSUM_PATTERN = re.compile(r"[0-9a-fA-F]{64}")


class RasterSourceError(RuntimeError):
    """A raster source could not be opened, described or read honestly."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = dict(details or {})


class UnsupportedSourceError(RasterSourceError):
    """The scheme, source kind or declared provenance is not supported."""


class RemoteReadDisabledError(RasterSourceError):
    """A remote source was requested without explicit opt-in."""


class RasterWindowError(RasterSourceError):
    """The requested window is ambiguous, invalid, outside the source or too large."""


class RasterAccessKind(StrEnum):
    LOCAL_FILE = "local_file"
    STAGED_TILE = "staged_tile"
    HTTP_RANGE = "http_range"
    VSICURL = "vsicurl"


REMOTE_ACCESS_KINDS = frozenset({RasterAccessKind.HTTP_RANGE, RasterAccessKind.VSICURL})


@dataclass(frozen=True, slots=True)
class RasterSource:
    """A raster source plus the provenance facts needed to use it.

    ``kind`` is normally inferred from ``uri``. ``STAGED_TILE`` may be declared for a
    local path so the evidence records that the file is a bounded staged window rather
    than a complete product. Remote sources require ``allow_remote=True`` and HTTPS.
    """

    uri: str | os.PathLike[str]
    kind: RasterAccessKind | None = None
    dataset_id: str | None = None
    version: str | None = None
    license: str | None = None
    license_url: str | None = None
    declared_checksum: str | None = None
    allow_remote: bool = False
    max_pixels: int = DEFAULT_MAX_PIXELS

    def __post_init__(self) -> None:
        if not isinstance(self.uri, (str, os.PathLike)):
            raise ValueError("uri must be a path or string")
        object.__setattr__(self, "uri", os.fspath(self.uri))
        if not self.uri.strip():
            raise ValueError("uri must be a non-empty string")
        if self.max_pixels <= 0:
            raise ValueError("max_pixels must be positive")
        for name in (
            "dataset_id",
            "version",
            "license",
            "license_url",
            "declared_checksum",
        ):
            value = getattr(self, name)
            if isinstance(value, str):
                object.__setattr__(self, name, value.strip() or None)

    @property
    def is_remote(self) -> bool:
        return self.access_kind() in REMOTE_ACCESS_KINDS

    def access_kind(self) -> RasterAccessKind:
        """Classify the URI, rejecting schemes this adapter cannot read."""
        raw = self.uri.strip()
        lowered = raw.lower()
        if lowered.startswith("/vsicurl/"):
            inferred = RasterAccessKind.VSICURL
        elif lowered.startswith(("http://", "https://")):
            inferred = RasterAccessKind.HTTP_RANGE
        elif lowered.startswith(_UNSUPPORTED_SCHEMES):
            raise UnsupportedSourceError(
                "unsupported_scheme",
                f"{raw!r} uses an unsupported remote scheme; this adapter supports local "
                "files, staged tiles and HTTPS /vsicurl reads",
                details={"uri": raw},
            )
        else:
            inferred = RasterAccessKind.LOCAL_FILE

        if self.kind is None:
            return inferred
        if self.kind is inferred:
            return inferred
        if self.kind is RasterAccessKind.STAGED_TILE and inferred is RasterAccessKind.LOCAL_FILE:
            return RasterAccessKind.STAGED_TILE
        raise UnsupportedSourceError(
            "source_kind_mismatch",
            f"declared kind {self.kind.value!r} does not match URI {raw!r} "
            f"(inferred {inferred.value!r})",
            details={"uri": raw, "declared_kind": self.kind.value},
        )

    def reference(self) -> str:
        """Return the local path or GDAL virtual path used to open the source."""
        kind = self.access_kind()
        raw = self.uri.strip()
        if kind is RasterAccessKind.VSICURL:
            url = raw[len("/vsicurl/") :]
            _require_https(url)
            _require_remote_allowed(self, url)
            return "/vsicurl/" + url
        if kind is RasterAccessKind.HTTP_RANGE:
            _require_https(raw)
            _require_remote_allowed(self, raw)
            return "/vsicurl/" + raw

        path = Path(raw).expanduser()
        if not path.is_file():
            raise RasterSourceError(
                "source_not_found",
                f"raster source not found: {path}",
                details={"uri": raw, "resolved_path": str(path)},
            )
        return str(path.resolve())

    def checksum(self) -> str:
        """Compute the SHA-256 of a local source; remote bytes are never downloaded."""
        if self.is_remote:
            raise RasterSourceError(
                "checksum_unavailable_remote",
                "a remote source checksum cannot be computed without a full download; "
                "publish a declared checksum in the source record instead",
                details={"uri": self.uri},
            )
        return f"sha256:{_sha256_file(Path(self.reference()))}"

    def inspect(self, *, verify_checksum: bool = False) -> RasterSourceMetadata:
        return inspect_source(self, verify_checksum=verify_checksum)

    def open(self, *, verify_checksum: bool = False) -> RasterReader:
        return RasterReader(self, verify_checksum=verify_checksum)

    def read_window(self, **kwargs: Any) -> RasterWindowResult:
        return read_source_window(self, **kwargs)


@dataclass(frozen=True, slots=True)
class RasterSourceMetadata:
    """Observed raster facts plus declared provenance.

    ``checksum`` is the declared checksum when one was supplied. It is only an observed
    value when ``checksum_verified`` is true (local sources inspected with
    ``verify_checksum=True``). ``resolution`` is in ``resolution_unit``;
    ``resolution_m`` is populated only for a projected metre CRS.
    """

    schema_version: str
    uri: str
    reference: str
    access_kind: RasterAccessKind
    dataset_id: str | None
    version: str | None
    license: str | None
    license_url: str | None
    checksum: str | None
    checksum_verified: bool
    crs_wkt: str | None
    crs_name: str | None
    crs_epsg: int | None
    is_geographic: bool | None
    width: int
    height: int
    count: int
    dtype: str
    nodata: float | None
    resolution: tuple[float, float]
    resolution_unit: str | None
    resolution_m: tuple[float, float] | None
    bounds: tuple[float, float, float, float]
    block_shape: tuple[int, int] | None
    tiled: bool
    size_bytes: int | None
    scale: float | None
    offset: float | None

    @property
    def crs(self) -> str | None:
        """A short CRS label; the WKT keeps the full definition."""
        return self.crs_name or self.crs_wkt

    @property
    def provenance_complete(self) -> bool:
        """Version, licence and checksum must all be present; no value is inferred."""
        return bool(self.version and self.license and self.checksum)

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "uri": self.uri,
            "reference": self.reference,
            "access_kind": self.access_kind.value,
            "dataset_id": self.dataset_id,
            "version": self.version,
            "license": self.license,
            "license_url": self.license_url,
            "checksum": self.checksum,
            "checksum_verified": self.checksum_verified,
            "provenance_complete": self.provenance_complete,
            "crs": self.crs,
            "crs_wkt": self.crs_wkt,
            "crs_name": self.crs_name,
            "crs_epsg": self.crs_epsg,
            "is_geographic": self.is_geographic,
            "width": self.width,
            "height": self.height,
            "count": self.count,
            "dtype": self.dtype,
            "nodata": self.nodata,
            "resolution": list(self.resolution),
            "resolution_unit": self.resolution_unit,
            "resolution_m": list(self.resolution_m) if self.resolution_m else None,
            "bounds": list(self.bounds),
            "block_shape": list(self.block_shape) if self.block_shape else None,
            "tiled": self.tiled,
            "size_bytes": self.size_bytes,
            "scale": self.scale,
            "offset": self.offset,
        }


@dataclass(frozen=True, slots=True)
class RasterWindowResult:
    """A bounded window with source-CRS georeferencing and validity bookkeeping.

    ``data`` is float64 with scale/offset applied; missing and nodata pixels are NaN.
    ``valid_mask`` marks pixels that were actually observed. ``bounds`` stay in the
    source CRS so a planetary caller never has to pretend the frame is WGS84.
    """

    band: int
    data: np.ndarray
    valid_mask: np.ndarray
    transform: Affine
    crs_wkt: str | None
    crs_name: str | None
    window: tuple[int, int, int, int]
    requested_window: tuple[int, int, int, int] | None
    clipped: bool
    bounds: tuple[float, float, float, float]
    read_bytes_upper_bound: int
    scale: float
    offset: float
    metadata: RasterSourceMetadata

    @property
    def valid_fraction(self) -> float:
        return float(np.count_nonzero(self.valid_mask)) / float(self.valid_mask.size)


class RasterReader:
    """An open raster source that can serve multiple bounded windows."""

    def __init__(
        self,
        source: RasterSource,
        *,
        extra_gdal_env: Mapping[str, str] | None = None,
        verify_checksum: bool = False,
    ) -> None:
        if not isinstance(source, RasterSource):
            raise TypeError("source must be a RasterSource")
        self.source = source
        self.extra_gdal_env = dict(extra_gdal_env or {})
        self.verify_checksum = verify_checksum
        self._stack: ExitStack | None = None
        self._dataset: rasterio.io.DatasetReader | None = None
        self._reference: str | None = None
        self._metadata: RasterSourceMetadata | None = None

    @property
    def metadata(self) -> RasterSourceMetadata:
        if self._metadata is None:
            raise RasterSourceError(
                "source_not_open", "the raster source is not open; use a context manager"
            )
        return self._metadata

    def __enter__(self) -> RasterReader:
        if self._stack is not None:
            raise RasterSourceError("source_already_open", "RasterReader is already open")
        stack = ExitStack()
        try:
            reference = self.source.reference()
            gdal_env = dict(_BASE_GDAL_ENV)
            if self.source.is_remote:
                gdal_env.update(_REMOTE_GDAL_ENV)
            gdal_env.update(self.extra_gdal_env)
            stack.enter_context(rasterio.Env(**gdal_env))
            dataset = stack.enter_context(rasterio.open(reference))
        except RasterSourceError:
            stack.close()
            raise
        except (RasterioError, OSError, ValueError) as exc:
            stack.close()
            raise RasterSourceError(
                "open_failed",
                f"could not open raster source {self.source.uri!r}: {exc}",
                details={"uri": self.source.uri, "kind": self.source.access_kind().value},
            ) from exc
        self._stack = stack
        self._dataset = dataset
        self._reference = reference
        try:
            self._metadata = self._build_metadata()
        except BaseException:
            self.close()
            raise
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        if self._stack is not None:
            self._stack.close()
        self._stack = None
        self._dataset = None
        self._reference = None
        self._metadata = None

    def read_window(
        self,
        *,
        window: Window | Sequence[int] | None = None,
        bbox: Mapping[str, float] | Sequence[float] | None = None,
        bbox_crs: str | CRS = "EPSG:4326",
        band: int = 1,
        pad_pixels: int = 0,
        max_pixels: int | None = None,
        clip: bool = True,
    ) -> RasterWindowResult:
        """Read one bounded window by pixel window or by bbox in ``bbox_crs``.

        Exactly one of ``window`` and ``bbox`` is required. A window that crosses the
        raster edge is clipped by default and flagged; pass ``clip=False`` to reject it
        instead. The pixel cap is enforced before any read.
        """
        dataset = self._require_dataset()
        metadata = self.metadata
        if window is not None and bbox is not None:
            raise RasterWindowError(
                "ambiguous_window", "provide exactly one of window or bbox, not both"
            )
        if window is None and bbox is None:
            raise RasterWindowError("missing_window", "provide either window or bbox")
        if band < 1 or band > dataset.count:
            raise RasterWindowError("invalid_band", f"band {band} is outside 1..{dataset.count}")
        if pad_pixels < 0:
            raise RasterWindowError("invalid_pad", "pad_pixels must be non-negative")
        limit = self.source.max_pixels if max_pixels is None else max_pixels
        if limit <= 0:
            raise RasterWindowError("invalid_max_pixels", "max_pixels must be positive")

        requested = self._requested_window(
            window=window, bbox=bbox, bbox_crs=bbox_crs, pad_pixels=pad_pixels
        )
        actual, clipped = self._clamp_window(requested, clip=clip)
        pixels = int(actual.width) * int(actual.height)
        if pixels > limit:
            raise RasterWindowError(
                "window_too_large",
                f"window has {pixels} pixels; max_pixels={limit}",
                details={"window": _window_tuple(actual), "pixels": pixels},
            )

        try:
            masked = dataset.read(
                band,
                window=actual,
                masked=True,
                boundless=False,
                out_dtype="float64",
            )
        except (RasterioError, OSError, ValueError) as exc:
            raise RasterSourceError(
                "read_failed",
                f"could not read window {_window_tuple(actual)} from {self.source.uri!r}: {exc}",
                details={
                    "uri": self.source.uri,
                    "window": _window_tuple(actual),
                    "reference": self._reference,
                },
            ) from exc

        raw = np.asarray(np.ma.filled(masked, np.nan), dtype="float64")
        valid = ~np.ma.getmaskarray(masked)
        valid &= np.isfinite(raw)
        if metadata.nodata is not None and math.isfinite(metadata.nodata):
            valid &= raw != float(metadata.nodata)
        scale, offset = _scale_offset(dataset, band)
        values = raw * scale + offset
        values = np.where(valid, values, np.nan)
        window_transform = dataset.window_transform(actual)
        left, bottom, right, top = window_bounds(actual, dataset.transform)
        return RasterWindowResult(
            band=band,
            data=values,
            valid_mask=valid,
            transform=window_transform,
            crs_wkt=metadata.crs_wkt,
            crs_name=metadata.crs_name,
            window=_window_tuple(actual),
            requested_window=_window_tuple(requested),
            clipped=clipped,
            bounds=(float(left), float(bottom), float(right), float(top)),
            read_bytes_upper_bound=_read_bytes_upper_bound(dataset, actual, band),
            scale=scale,
            offset=offset,
            metadata=metadata,
        )

    def _require_dataset(self) -> rasterio.io.DatasetReader:
        if self._dataset is None:
            raise RasterSourceError(
                "source_not_open", "the raster source is not open; use a context manager"
            )
        return self._dataset

    def _build_metadata(self) -> RasterSourceMetadata:
        dataset = self._require_dataset()
        assert self._reference is not None
        source = self.source
        crs = CRS.from_user_input(dataset.crs) if dataset.crs else None
        unit = crs.axis_info[0].unit_name if crs and crs.axis_info else None
        resolution = (abs(float(dataset.res[0])), abs(float(dataset.res[1])))
        checksum, checked = _resolve_checksum(
            source,
            reference=self._reference,
            verify=self.verify_checksum,
        )
        block_shape: tuple[int, int] | None = None
        if dataset.block_shapes:
            block_shape = tuple(int(value) for value in dataset.block_shapes[0])
        scale, offset = _optional_scale_offset(dataset)
        is_local = source.access_kind() not in REMOTE_ACCESS_KINDS
        size_bytes = Path(self._reference).stat().st_size if is_local else None
        return RasterSourceMetadata(
            schema_version=RASTER_SOURCE_SCHEMA_VERSION,
            uri=source.uri,
            reference=self._reference,
            access_kind=source.access_kind(),
            dataset_id=source.dataset_id,
            version=source.version,
            license=source.license,
            license_url=source.license_url,
            checksum=checksum,
            checksum_verified=checked,
            crs_wkt=crs.to_wkt() if crs else None,
            crs_name=crs.name if crs else None,
            crs_epsg=crs.to_epsg() if crs else None,
            is_geographic=crs.is_geographic if crs else None,
            width=int(dataset.width),
            height=int(dataset.height),
            count=int(dataset.count),
            dtype=dataset.dtypes[0] if dataset.dtypes else "unknown",
            nodata=float(dataset.nodata) if dataset.nodata is not None else None,
            resolution=resolution,
            resolution_unit=unit,
            resolution_m=_resolution_metres(crs, resolution),
            bounds=tuple(float(value) for value in dataset.bounds),
            block_shape=block_shape,
            tiled=_is_tiled(dataset),
            size_bytes=size_bytes,
            scale=scale,
            offset=offset,
        )

    def _requested_window(
        self,
        *,
        window: Window | Sequence[int] | None,
        bbox: Mapping[str, float] | Sequence[float] | None,
        bbox_crs: str | CRS,
        pad_pixels: int,
    ) -> Window:
        dataset = self._require_dataset()
        if window is not None:
            if isinstance(window, Window):
                offsets = (
                    window.col_off,
                    window.row_off,
                    window.width,
                    window.height,
                )
            elif len(window) == 4:
                offsets = tuple(window)
            else:
                raise RasterWindowError(
                    "invalid_window",
                    "window must be a Window or (col_off, row_off, width, height)",
                )
            result = Window(
                int(round(float(offsets[0]))),
                int(round(float(offsets[1]))),
                int(round(float(offsets[2]))),
                int(round(float(offsets[3]))),
            )
            if result.width <= 0 or result.height <= 0:
                raise RasterWindowError(
                    "invalid_window", "window width and height must be positive"
                )
            return result

        assert bbox is not None
        west, south, east, north = _bbox_tuple(bbox)
        if not all(math.isfinite(value) for value in (west, south, east, north)):
            raise RasterWindowError("invalid_bbox", "bbox coordinates must be finite")
        if not (east > west and north > south):
            raise RasterWindowError("invalid_bbox", "bbox must have positive extent")
        if dataset.crs is None:
            raise RasterWindowError("missing_source_crs", "bbox reads require a source CRS")
        source_crs = CRS.from_user_input(dataset.crs)
        try:
            requested_crs = CRS.from_user_input(bbox_crs)
        except Exception as exc:  # noqa: BLE001 - pyproj raises several types
            raise RasterWindowError(
                "invalid_bbox_crs", f"cannot parse bbox CRS {bbox_crs!r}"
            ) from exc
        if requested_crs == source_crs:
            xs = [west, east, west, east]
            ys = [south, south, north, north]
        else:
            transformer = Transformer.from_crs(requested_crs, source_crs, always_xy=True)
            xs, ys = transformer.transform(
                [west, east, west, east],
                [south, south, north, north],
            )
        pad = pad_pixels * max(abs(dataset.transform.a), abs(dataset.transform.e))
        result = from_bounds(
            min(xs) - pad,
            min(ys) - pad,
            max(xs) + pad,
            max(ys) + pad,
            dataset.transform,
        ).round_offsets()
        result = result.round_lengths()
        if result.width <= 0 or result.height <= 0:
            raise RasterWindowError(
                "window_outside_source", "the bbox does not cover any source pixel"
            )
        return result

    def _clamp_window(self, requested: Window, *, clip: bool) -> tuple[Window, bool]:
        dataset = self._require_dataset()
        col0 = max(0, math.floor(requested.col_off))
        row0 = max(0, math.floor(requested.row_off))
        col1 = min(int(dataset.width), math.ceil(requested.col_off + requested.width))
        row1 = min(int(dataset.height), math.ceil(requested.row_off + requested.height))
        if col1 <= col0 or row1 <= row0:
            raise RasterWindowError(
                "window_outside_source",
                f"window {_window_tuple(requested)} lies outside the "
                f"{dataset.width}x{dataset.height} raster",
                details={"window": _window_tuple(requested)},
            )
        actual = Window(col0, row0, col1 - col0, row1 - row0)
        clipped = actual != requested
        if clipped and not clip:
            raise RasterWindowError(
                "window_clipped",
                f"window {_window_tuple(requested)} crosses the source edge; "
                f"clipped window would be {_window_tuple(actual)}",
                details={
                    "requested": _window_tuple(requested),
                    "clipped": _window_tuple(actual),
                },
            )
        return actual, clipped


def open_raster_source(
    source: RasterSource,
    *,
    extra_gdal_env: Mapping[str, str] | None = None,
    verify_checksum: bool = False,
) -> RasterReader:
    """Open a source for one or more bounded window reads."""
    return RasterReader(source, extra_gdal_env=extra_gdal_env, verify_checksum=verify_checksum)


def inspect_source(
    source: RasterSource,
    *,
    extra_gdal_env: Mapping[str, str] | None = None,
    verify_checksum: bool = False,
) -> RasterSourceMetadata:
    """Open, describe and close a source without reading a data window."""
    with open_raster_source(
        source,
        extra_gdal_env=extra_gdal_env,
        verify_checksum=verify_checksum,
    ) as reader:
        return reader.metadata


def read_source_window(
    source: RasterSource,
    *,
    extra_gdal_env: Mapping[str, str] | None = None,
    **window_kwargs: Any,
) -> RasterWindowResult:
    """Open, read one bounded window and close the source."""
    with open_raster_source(source, extra_gdal_env=extra_gdal_env) as reader:
        return reader.read_window(**window_kwargs)


def _require_https(url: str) -> None:
    lowered = url.strip().lower()
    if lowered.startswith("http://"):
        raise UnsupportedSourceError(
            "insecure_remote",
            "only HTTPS range reads are allowed; refuse plain HTTP",
            details={"url": url},
        )
    if not lowered.startswith("https://"):
        raise UnsupportedSourceError(
            "unsupported_scheme",
            f"{url!r} is not an HTTPS URL",
            details={"url": url},
        )


def _require_remote_allowed(source: RasterSource, url: str) -> None:
    if not source.allow_remote:
        raise RemoteReadDisabledError(
            "remote_read_disabled",
            "remote raster reads are opt-in; construct RasterSource(..., allow_remote=True) "
            "for a bounded HTTPS /vsicurl window",
            details={"uri": source.uri, "url": url},
        )


def _bbox_tuple(
    bbox: Mapping[str, float] | Sequence[float],
) -> tuple[float, float, float, float]:
    if isinstance(bbox, Mapping):
        try:
            return (
                float(bbox["west"]),
                float(bbox["south"]),
                float(bbox["east"]),
                float(bbox["north"]),
            )
        except KeyError as exc:
            raise RasterWindowError(
                "invalid_bbox", "bbox mapping needs west, south, east and north"
            ) from exc
    if len(bbox) != 4:
        raise RasterWindowError("invalid_bbox", "bbox sequence must be (west, south, east, north)")
    return (float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3]))


def _window_tuple(window: Window) -> tuple[int, int, int, int]:
    return (
        int(window.col_off),
        int(window.row_off),
        int(window.width),
        int(window.height),
    )


def _scale_offset(dataset: rasterio.io.DatasetReader, band: int) -> tuple[float, float]:
    scales = list(dataset.scales or [])
    offsets = list(dataset.offsets or [])
    scale = float(scales[band - 1]) if len(scales) >= band and scales[band - 1] is not None else 1.0
    offset = (
        float(offsets[band - 1]) if len(offsets) >= band and offsets[band - 1] is not None else 0.0
    )
    if not math.isfinite(scale) or scale == 0.0:
        raise RasterSourceError(
            "invalid_scale",
            "raster scale is missing, zero or non-finite; refusing to invent values",
        )
    if not math.isfinite(offset):
        raise RasterSourceError("invalid_offset", "raster offset is non-finite")
    return scale, offset


def _optional_scale_offset(
    dataset: rasterio.io.DatasetReader,
) -> tuple[float | None, float | None]:
    scales = list(dataset.scales or [])
    offsets = list(dataset.offsets or [])
    scale = float(scales[0]) if scales and scales[0] is not None else None
    offset = float(offsets[0]) if offsets and offsets[0] is not None else None
    return scale, offset


def _resolution_metres(
    crs: CRS | None, resolution: tuple[float, float]
) -> tuple[float, float] | None:
    if crs is None or not crs.is_projected:
        return None
    unit = crs.axis_info[0].unit_name.lower() if crs.axis_info else ""
    if unit not in _METRE_UNITS:
        return None
    return resolution


def _is_tiled(dataset: rasterio.io.DatasetReader) -> bool:
    """Whether the raster uses tiled blocks, without the deprecated ``is_tiled``."""
    value = dataset.profile.get("tiled")
    if value is not None:
        return bool(value)
    if not dataset.block_shapes:
        return False
    return dataset.block_shapes[0][1] < dataset.width


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalise_checksum(value: str) -> str:
    text = value.strip()
    if text.lower().startswith("sha256:"):
        digest = text[len("sha256:") :]
    elif ":" in text:
        raise UnsupportedSourceError(
            "unsupported_checksum",
            f"checksum {value!r} is not a SHA-256 digest",
            details={"checksum": value},
        )
    else:
        digest = text
    if not _CHECKSUM_PATTERN.fullmatch(digest):
        raise UnsupportedSourceError(
            "unsupported_checksum",
            f"checksum {value!r} is not a 64-character SHA-256 digest",
            details={"checksum": value},
        )
    return "sha256:" + digest.lower()


def _resolve_checksum(
    source: RasterSource,
    *,
    reference: str,
    verify: bool,
) -> tuple[str | None, bool]:
    declared = _normalise_checksum(source.declared_checksum) if source.declared_checksum else None
    if not verify:
        return declared, False
    if source.is_remote:
        raise RasterSourceError(
            "checksum_verification_requires_local",
            "verifying a checksum requires local bytes; stage the window first or rely on "
            "the published declared checksum",
            details={"uri": source.uri},
        )
    observed = "sha256:" + _sha256_file(Path(reference))
    if declared is not None and declared != observed:
        raise RasterSourceError(
            "checksum_mismatch",
            f"declared checksum {declared} does not match observed {observed}",
            details={"declared": declared, "observed": observed, "uri": source.uri},
        )
    return observed, True


def _tiles_touched(window: Window, block_shape: tuple[int, int]) -> tuple[int, int]:
    block_x, block_y = block_shape
    first_col = math.floor(window.col_off / block_x)
    last_col = math.floor((window.col_off + window.width - 1) / block_x)
    first_row = math.floor(window.row_off / block_y)
    last_row = math.floor((window.row_off + window.height - 1) / block_y)
    return last_col - first_col + 1, last_row - first_row + 1


def _read_bytes_upper_bound(
    dataset: rasterio.io.DatasetReader,
    window: Window,
    band: int,
) -> int:
    """Conservative bound on bytes a window read can touch.

    Compressed tiles usually transfer fewer bytes; this is an upper bound, not a
    measurement. A 64 KiB allowance covers TIFF/header and label reads.
    """
    itemsize = np.dtype(dataset.dtypes[band - 1]).itemsize
    if _is_tiled(dataset) and dataset.block_shapes:
        block_y, block_x = (int(value) for value in dataset.block_shapes[band - 1])
        cols, rows = _tiles_touched(window, (block_x, block_y))
        data_bytes = cols * rows * block_x * block_y * itemsize
    else:
        data_bytes = int(dataset.width) * int(window.height) * itemsize
    return int(data_bytes) + 64 * 1024
