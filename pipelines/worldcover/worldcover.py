"""ESA WorldCover 2021 v200 land-cover context for the indexed Earth AOIs.

This module reads only the small AOI windows of the public ESA WorldCover 2021 v200
raster over unsigned HTTPS, computes per-class pixel fractions, and shapes them as
``context_observations`` on the existing Earth candidate records.

The values are **Earth land-cover context only**. They are not lunar mineralogy, not
geology, not a terrain measurement, and never enter the similarity distance. The
authoritative feature ids/class codes live in
``analog_api.domain.features.WORLDCOVER_CLASSES`` and are imported, not redefined.

Public access verified 2026-11-14:

- Product page: https://esa-worldcover.org/en/data-access
- Bucket: ``s3://esa-worldcover`` (``eu-central-1``), unsigned HTTPS
- Key pattern: ``v200/2021/map/ESA_WorldCover_10m_2021_v200_<N|S>dd<W|E>ddd_Map.tif``
- Tiles are 3 x 3 degrees, named by their south-west corner, EPSG:4326, uint8,
  1/12000 degree pixels (~10 m at the equator), nodata 0.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
from analog_api.domain.features import CONTEXT_METHOD_VERSION, WORLDCOVER_CLASSES
from pyproj import CRS
from rasterio.transform import Affine
from rasterio.windows import Window

BUCKET = "esa-worldcover"
BUCKET_REGION = "eu-central-1"
HTTPS_BASE = f"https://{BUCKET}.s3.{BUCKET_REGION}.amazonaws.com"
DATA_ACCESS_URL = "https://esa-worldcover.org/en/data-access"
DATASET_DOI = "https://doi.org/10.5281/zenodo.7254221"

DATASET_ID = "esa-worldcover-2021-v200"
PROVIDER = "European Space Agency (ESA) WorldCover"
PRODUCT_ID = "ESA WorldCover 2021 v200"
PRODUCT_TITLE = "ESA WorldCover 10 m 2021 v200 land cover"
LICENSE_NAME = "CC BY 4.0 (Creative Commons Attribution 4.0 International)"
LICENSE_URL = "https://creativecommons.org/licenses/by/4.0/"
ATTRIBUTION = (
    "© ESA WorldCover project 2021 / Contains modified Copernicus Sentinel data (2021) "
    "processed by ESA WorldCover consortium"
)
CITATION = (
    "Zanaga, D., Van De Kerchove, R., Daems, D., De Keersmaecker, W., Brockmann, C., "
    "Kirches, G., Wevers, J., Cartus, O., Santoro, M., Fritz, S., Lesiv, M., Herold, M., "
    "Tsendbazar, N.E., Xu, P., Ramoino, F., Arino, O., 2022. ESA WorldCover 10 m 2021 "
    "v200. https://doi.org/10.5281/zenodo.7254221"
)

SOURCE_CRS = "EPSG:4326"
NATIVE_RESOLUTION_M = 10.0
PIXELS_PER_DEGREE = 12000
DEGREES_PER_PIXEL = 1.0 / PIXELS_PER_DEGREE
TILE_SIDE_DEG = 3.0
TILE_SIDE_PX = int(round(TILE_SIDE_DEG * PIXELS_PER_DEGREE))  # 36000
NODATA = 0
MIN_VALID_FRACTION = 0.8
FRACTION_SUM_TOLERANCE = 1e-4
MAX_RETRIES = 2
DEFAULT_TIMEOUT_S = 30.0

TILE_KEY_TEMPLATE = "v200/2021/map/ESA_WorldCover_10m_2021_v200_{tile_id}_Map.tif"
_TILE_RE = re.compile(r"^([NS])(\d{2})([EW])(\d{3})$")

CLASS_CODES: tuple[int, ...] = tuple(code for code, _slug, _label in WORLDCOVER_CLASSES)
FEATURE_ID_BY_CODE: dict[int, str] = {
    code: f"landcover_{slug}_fraction" for code, slug, _label in WORLDCOVER_CLASSES
}
CLASS_LABEL_BY_CODE: dict[int, str] = {code: label for code, _slug, label in WORLDCOVER_CLASSES}
BARE_SPARSE_CODE = 60
BARE_SPARSE_FEATURE_ID = FEATURE_ID_BY_CODE[BARE_SPARSE_CODE]


class IngestError(RuntimeError):
    """A real ingestion problem; never silently replaced with synthetic data."""


class TileNotFoundError(IngestError):
    """The resolved WorldCover object does not exist (HTTP 403/404)."""


@dataclass(frozen=True, slots=True)
class Aoi:
    """One indexed Earth candidate and its exact manifest record."""

    location_id: str
    display_name: str
    lat: float
    lon: float
    bbox: dict[str, float]
    earth_record: dict[str, Any] = field(repr=False, compare=False)

    @property
    def slug(self) -> str:
        return self.location_id.removeprefix("earth-")

    @property
    def tile_ids(self) -> tuple[str, ...]:
        return tiles_for_bbox(self.bbox)


@dataclass(frozen=True, slots=True)
class RemoteObject:
    url: str
    status: int
    content_length: int | None
    etag: str | None
    last_modified: str | None


@dataclass(frozen=True, slots=True)
class ClassFractions:
    counts: dict[int, int]
    valid_pixels: int
    total_pixels: int
    valid_fraction: float
    unknown_codes: tuple[int, ...]
    quality: str

    @property
    def fractions(self) -> dict[int, float]:
        if self.valid_pixels == 0:
            return {code: 0.0 for code in CLASS_CODES}
        return {code: self.counts.get(code, 0) / self.valid_pixels for code in CLASS_CODES}

    @property
    def fraction_sum(self) -> float:
        return sum(self.counts.get(code, 0) for code in CLASS_CODES) / self.valid_pixels


@dataclass(frozen=True, slots=True)
class AoiObservation:
    aoi: Aoi
    path: Path
    metadata_path: Path
    tile_ids: tuple[str, ...]
    tile_urls: tuple[str, ...]
    access_timestamp: str
    fractions: ClassFractions
    checksum: str
    remote_bytes: int | None
    from_cache: bool


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def tile_id_for_lonlat(lon: float, lat: float) -> str:
    """Return the 3-degree WorldCover tile containing ``(lon, lat)``."""
    if not (-180.0 <= lon <= 180.0):
        raise IngestError(f"longitude {lon} outside [-180, 180]")
    if not (-90.0 <= lat <= 90.0):
        raise IngestError(f"latitude {lat} outside [-90, 90]")
    lat_origin = math.floor(lat / TILE_SIDE_DEG) * TILE_SIDE_DEG
    lon_origin = math.floor(lon / TILE_SIDE_DEG) * TILE_SIDE_DEG
    lat_origin = min(max(lat_origin, -90.0), 87.0)
    lon_origin = min(max(lon_origin, -180.0), 177.0)
    ns = "N" if lat_origin >= 0 else "S"
    ew = "E" if lon_origin >= 0 else "W"
    return f"{ns}{abs(int(lat_origin)):02d}{ew}{abs(int(lon_origin)):03d}"


def validate_tile_id(tile_id: str) -> str:
    if not _TILE_RE.fullmatch(tile_id):
        raise IngestError(f"invalid WorldCover tile id: {tile_id!r}")
    return tile_id


def tile_bounds_from_id(tile_id: str) -> dict[str, float]:
    match = _TILE_RE.fullmatch(validate_tile_id(tile_id))
    assert match is not None
    ns, lat_text, ew, lon_text = match.groups()
    lat = int(lat_text) * (1 if ns == "N" else -1)
    lon = int(lon_text) * (1 if ew == "E" else -1)
    return {
        "west": float(lon),
        "south": float(lat),
        "east": float(lon) + TILE_SIDE_DEG,
        "north": float(lat) + TILE_SIDE_DEG,
    }


def tile_key(tile_id: str) -> str:
    return TILE_KEY_TEMPLATE.format(tile_id=validate_tile_id(tile_id))


def tile_url(tile_id: str, bucket: str = BUCKET) -> str:
    return f"https://{bucket}.s3.{BUCKET_REGION}.amazonaws.com/{tile_key(tile_id)}"


def tiles_for_bbox(bbox: dict[str, float]) -> tuple[str, ...]:
    """All 3-degree tiles intersecting an axis-aligned WGS84 bbox (1-4 tiles)."""
    west, south, east, north = (float(bbox[key]) for key in ("west", "south", "east", "north"))
    if not (west < east and south < north):
        raise IngestError(f"invalid bbox: {bbox}")
    lon_start = math.floor(west / TILE_SIDE_DEG) * TILE_SIDE_DEG
    lon_end = math.floor((east - 1e-12) / TILE_SIDE_DEG) * TILE_SIDE_DEG
    lat_start = math.floor(south / TILE_SIDE_DEG) * TILE_SIDE_DEG
    lat_end = math.floor((north - 1e-12) / TILE_SIDE_DEG) * TILE_SIDE_DEG
    tile_ids = []
    lat = lat_start
    while lat <= lat_end + 1e-9:
        lon = lon_start
        while lon <= lon_end + 1e-9:
            ns = "N" if lat >= 0 else "S"
            ew = "E" if lon >= 0 else "W"
            tile_ids.append(f"{ns}{abs(int(lat)):02d}{ew}{abs(int(lon)):03d}")
            lon += TILE_SIDE_DEG
        lat += TILE_SIDE_DEG
    return tuple(tile_ids)


def envelope_pixel_grid(bbox: dict[str, float]) -> tuple[int, int, int, int]:
    """Global WorldCover pixel envelope (col0, row0, width, height) for a bbox."""
    col0 = math.floor((float(bbox["west"]) + 180.0) * PIXELS_PER_DEGREE)
    col1 = math.ceil((float(bbox["east"]) + 180.0) * PIXELS_PER_DEGREE)
    row0 = math.floor((90.0 - float(bbox["north"])) * PIXELS_PER_DEGREE)
    row1 = math.ceil((90.0 - float(bbox["south"])) * PIXELS_PER_DEGREE)
    if col1 <= col0 or row1 <= row0:
        raise IngestError(f"bbox produces an empty WorldCover pixel envelope: {bbox}")
    return col0, row0, col1 - col0, row1 - row0


def envelope_transform(col0: int, row0: int) -> Affine:
    west = -180.0 + col0 * DEGREES_PER_PIXEL
    north = 90.0 - row0 * DEGREES_PER_PIXEL
    return Affine(DEGREES_PER_PIXEL, 0.0, west, 0.0, -DEGREES_PER_PIXEL, north)


def compute_class_fractions(
    array: np.ndarray,
    transform: Affine,
    bbox: dict[str, float],
) -> ClassFractions:
    """Class proportions over WorldCover pixels whose centres fall inside ``bbox``.

    ``0`` is the WorldCover nodata code and is excluded from the denominator. Any
    other value outside the eleven documented classes stays in the denominator but
    is reported as ``unknown_codes`` and downgrades the quality to ``suspect``; it is
    never mapped onto a real class.
    """
    data = np.asarray(array)
    if data.ndim != 2:
        raise IngestError(f"expected a 2-D class raster, got shape {data.shape}")

    rows, cols = np.mgrid[0 : data.shape[0], 0 : data.shape[1]]
    lon = transform.c + (cols + 0.5) * transform.a
    lat = transform.f + (rows + 0.5) * transform.e
    inside = (
        (lon >= float(bbox["west"]))
        & (lon <= float(bbox["east"]))
        & (lat >= float(bbox["south"]))
        & (lat <= float(bbox["north"]))
    )
    total_pixels = int(inside.sum())
    if total_pixels == 0:
        raise IngestError("AOI bbox contains no WorldCover pixel centres")

    values = data[inside].astype("int64", copy=False)
    valid = values != NODATA
    valid_pixels = int(valid.sum())
    valid_values = values[valid]
    unique, counts = np.unique(valid_values, return_counts=True)
    count_by_code = {int(code): int(count) for code, count in zip(unique, counts, strict=True)}
    unknown = tuple(sorted(code for code in count_by_code if code not in CLASS_CODES))
    valid_fraction = valid_pixels / total_pixels
    known_pixels = sum(count_by_code.get(code, 0) for code in CLASS_CODES)
    fraction_sum = known_pixels / valid_pixels if valid_pixels else 0.0
    quality = "verified"
    if valid_pixels == 0 or valid_fraction < MIN_VALID_FRACTION:
        quality = "suspect"
    if unknown or abs(fraction_sum - 1.0) > FRACTION_SUM_TOLERANCE:
        quality = "suspect"
    return ClassFractions(
        counts={code: count_by_code.get(code, 0) for code in CLASS_CODES},
        valid_pixels=valid_pixels,
        total_pixels=total_pixels,
        valid_fraction=valid_fraction,
        unknown_codes=unknown,
        quality=quality,
    )


def window_checksum(array: np.ndarray, tile_urls: tuple[str, ...], bbox: dict[str, float]) -> str:
    """SHA-256 fingerprint of the AOI class window and its provenance labels."""
    digest = hashlib.sha256()
    for url in tile_urls:
        digest.update(url.encode("utf-8"))
        digest.update(b"\n")
    descriptor = ",".join(f"{bbox[key]:.8f}" for key in ("west", "south", "east", "north"))
    digest.update(descriptor.encode("utf-8"))
    digest.update(b"\n")
    digest.update(np.ascontiguousarray(array, dtype=np.uint8).tobytes())
    return "sha256:" + digest.hexdigest()


def head_remote_object(
    url: str,
    *,
    timeout: float = DEFAULT_TIMEOUT_S,
    max_retries: int = MAX_RETRIES,
    opener: Callable[..., Any] = urllib.request.urlopen,
    sleeper: Callable[[float], None] = time.sleep,
) -> RemoteObject:
    """HEAD a public object with bounded retries (fake-injectable for offline tests)."""
    last_error: Exception | None = None
    for attempt in range(max_retries + 1):
        request = urllib.request.Request(
            url, method="HEAD", headers={"User-Agent": "planetary-analog-worldcover/0.1"}
        )
        try:
            with opener(request, timeout=timeout) as response:
                length_text = response.headers.get("Content-Length")
                return RemoteObject(
                    url=url,
                    status=int(getattr(response, "status", 200)),
                    content_length=int(length_text) if length_text else None,
                    etag=response.headers.get("ETag"),
                    last_modified=response.headers.get("Last-Modified"),
                )
        except urllib.error.HTTPError as exc:
            if exc.code in (403, 404):
                raise TileNotFoundError(
                    f"WorldCover object not found at {url} (HTTP {exc.code}); "
                    "verify the 3-degree tile id against the published naming pattern."
                ) from exc
            last_error = exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = exc
        if attempt < max_retries:
            sleeper(1.5 * (attempt + 1))
    raise IngestError(f"cannot reach {url} after {max_retries + 1} attempt(s): {last_error}")


def rasterio_remote_env(timeout: float = DEFAULT_TIMEOUT_S) -> rasterio.Env:
    """GDAL settings for bounded unsigned HTTPS range reads."""
    return rasterio.Env(
        GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
        CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif",
        GDAL_HTTP_TIMEOUT=str(int(timeout)),
        GDAL_HTTP_CONNECTTIMEOUT="10",
        GDAL_HTTP_MAX_RETRY="0",
        VSI_CACHE="TRUE",
        VSI_CACHE_SIZE="33554432",
    )


def _validate_source(src: rasterio.DatasetReader, label: str) -> None:
    if src.crs is None or CRS.from_user_input(src.crs) != CRS.from_epsg(4326):
        raise IngestError(f"{label}: expected EPSG:4326 WorldCover, found {src.crs}")
    if not (
        math.isclose(abs(float(src.res[0])), DEGREES_PER_PIXEL, rel_tol=0.0, abs_tol=1e-12)
        and math.isclose(abs(float(src.res[1])), DEGREES_PER_PIXEL, rel_tol=0.0, abs_tol=1e-12)
    ):
        raise IngestError(f"{label}: expected 1/12000 degree pixels, found res={src.res}")
    if src.count != 1 or src.dtypes[0] != "uint8":
        raise IngestError(
            f"{label}: expected one uint8 band, found count={src.count} dtypes={src.dtypes}"
        )
    if src.nodata not in (None, 0, 0.0):
        raise IngestError(f"{label}: expected nodata 0 (or undefined), found {src.nodata}")


def read_envelope_from_remote(
    aoi: Aoi,
    tile_ids: tuple[str, ...],
    *,
    timeout: float = DEFAULT_TIMEOUT_S,
) -> np.ndarray:
    """Read the AOI class envelope from WorldCover COGs via /vsicurl range reads."""
    col0, row0, width, height = envelope_pixel_grid(aoi.bbox)
    envelope = np.zeros((height, width), dtype=np.uint8)
    with rasterio_remote_env(timeout):
        for tile_id in tile_ids:
            url = tile_url(tile_id)
            bounds = tile_bounds_from_id(tile_id)
            tile_col0 = int(round((bounds["west"] + 180.0) * PIXELS_PER_DEGREE))
            tile_row0 = int(round((90.0 - bounds["north"]) * PIXELS_PER_DEGREE))
            gx0 = max(col0, tile_col0)
            gx1 = min(col0 + width, tile_col0 + TILE_SIDE_PX)
            gy0 = max(row0, tile_row0)
            gy1 = min(row0 + height, tile_row0 + TILE_SIDE_PX)
            if gx1 <= gx0 or gy1 <= gy0:
                continue
            window = Window(
                col_off=gx0 - tile_col0,
                row_off=gy0 - tile_row0,
                width=gx1 - gx0,
                height=gy1 - gy0,
            )
            with rasterio.open("/vsicurl/" + url) as src:
                _validate_source(src, url)
                data = src.read(1, window=window)
            if data.shape != (gy1 - gy0, gx1 - gx0):
                raise IngestError(
                    f"{url}: unexpected window shape {data.shape} for AOI {aoi.location_id}"
                )
            envelope[gy0 - row0 : gy1 - row0, gx0 - col0 : gx1 - col0] = data
    return envelope


def _read_cached_array(path: Path) -> np.ndarray | None:
    try:
        with rasterio.open(path) as src:
            if src.crs is None or CRS.from_user_input(src.crs) != CRS.from_epsg(4326):
                return None
            return src.read(1)
    except (rasterio.errors.RasterioError, OSError, ValueError):
        return None


def _metadata_matches(metadata: dict[str, Any], aoi: Aoi, tile_ids: tuple[str, ...]) -> bool:
    if metadata.get("location_id") != aoi.location_id:
        return False
    if tuple(metadata.get("tile_ids", ())) != tile_ids:
        return False
    cached_bbox = metadata.get("bbox") or {}
    try:
        return all(
            math.isclose(float(cached_bbox[key]), float(aoi.bbox[key]), rel_tol=0.0, abs_tol=1e-9)
            for key in ("west", "south", "east", "north")
        )
    except (KeyError, TypeError, ValueError):
        return False


def _build_result(
    aoi: Aoi,
    path: Path,
    metadata_path: Path,
    array: np.ndarray,
    metadata: dict[str, Any],
    *,
    remote_bytes: int | None,
    from_cache: bool,
) -> AoiObservation:
    col0, row0, width, height = envelope_pixel_grid(aoi.bbox)
    if array.shape != (height, width):
        raise IngestError(
            f"{aoi.location_id}: cached class window shape {array.shape} != {(height, width)}"
        )
    fractions = compute_class_fractions(array, envelope_transform(col0, row0), aoi.bbox)
    checksum = window_checksum(array, tuple(metadata["tile_urls"]), aoi.bbox)
    if metadata.get("window_checksum") and metadata["window_checksum"] != checksum:
        raise IngestError(f"{aoi.location_id}: cached window checksum does not match its metadata")
    return AoiObservation(
        aoi=aoi,
        path=path,
        metadata_path=metadata_path,
        tile_ids=tuple(metadata["tile_ids"]),
        tile_urls=tuple(metadata["tile_urls"]),
        access_timestamp=metadata["access_timestamp"],
        fractions=fractions,
        checksum=checksum,
        remote_bytes=remote_bytes,
        from_cache=from_cache,
    )


def _load_cached(
    aoi: Aoi, crop_path: Path, metadata_path: Path, tile_ids: tuple[str, ...]
) -> AoiObservation | None:
    if not crop_path.is_file() or not metadata_path.is_file():
        return None
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not _metadata_matches(metadata, aoi, tile_ids):
        return None
    array = _read_cached_array(crop_path)
    if array is None:
        return None
    try:
        return _build_result(
            aoi,
            crop_path,
            metadata_path,
            array,
            metadata,
            remote_bytes=metadata.get("remote_bytes"),
            from_cache=True,
        )
    except (IngestError, KeyError):
        return None


def _write_cached(
    array: np.ndarray,
    aoi: Aoi,
    crop_path: Path,
    metadata_path: Path,
    metadata: dict[str, Any],
) -> None:
    col0, row0, _width, _height = envelope_pixel_grid(aoi.bbox)
    profile = {
        "driver": "GTiff",
        "height": array.shape[0],
        "width": array.shape[1],
        "count": 1,
        "dtype": "uint8",
        "crs": SOURCE_CRS,
        "transform": envelope_transform(col0, row0),
        "nodata": NODATA,
        "compress": "deflate",
        "tiled": False,
    }
    crop_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = crop_path.with_name(crop_path.name + ".part")
    try:
        with rasterio.open(temporary, "w", **profile) as dst:
            dst.write(array, 1)
        os.replace(temporary, crop_path)
    finally:
        temporary.unlink(missing_ok=True)

    temporary_metadata = metadata_path.with_name(metadata_path.name + ".part")
    temporary_metadata.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary_metadata, metadata_path)


def fetch_aoi_window(
    aoi: Aoi,
    cache_dir: str | Path,
    *,
    force: bool = False,
    timeout: float = DEFAULT_TIMEOUT_S,
    max_retries: int = MAX_RETRIES,
    sleeper: Callable[[float], None] = time.sleep,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> AoiObservation:
    """Return a cached AOI window or range-read it from the public WorldCover bucket."""
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    crop_path = cache_dir / f"{aoi.slug}.tif"
    metadata_path = cache_dir / f"{aoi.slug}.json"
    tile_ids = aoi.tile_ids

    if not force:
        cached = _load_cached(aoi, crop_path, metadata_path, tile_ids)
        if cached is not None:
            return cached

    remotes = [
        head_remote_object(
            tile_url(tile_id),
            timeout=timeout,
            max_retries=max_retries,
            opener=opener,
            sleeper=sleeper,
        )
        for tile_id in tile_ids
    ]
    remote_bytes = sum(remote.content_length or 0 for remote in remotes) or None
    tile_urls = tuple(remote.url for remote in remotes)

    last_error: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            array = read_envelope_from_remote(aoi, tile_ids, timeout=timeout)
            checksum = window_checksum(array, tile_urls, aoi.bbox)
            metadata = {
                "schema": "worldcover-aoi-window/1",
                "location_id": aoi.location_id,
                "tile_ids": list(tile_ids),
                "tile_urls": list(tile_urls),
                "bbox": {key: float(aoi.bbox[key]) for key in ("west", "south", "east", "north")},
                "pixel_grid": list(envelope_pixel_grid(aoi.bbox)),
                "source_crs": SOURCE_CRS,
                "source_resolution_m": NATIVE_RESOLUTION_M,
                "native_resolution_deg": DEGREES_PER_PIXEL,
                "source_nodata": NODATA,
                "window_checksum": checksum,
                "access_timestamp": utc_now_iso(),
                "remote_bytes": remote_bytes,
                "remote_objects": [
                    {
                        "url": remote.url,
                        "content_length": remote.content_length,
                        "etag": remote.etag,
                        "last_modified": remote.last_modified,
                    }
                    for remote in remotes
                ],
                "read_mode": "GDAL /vsicurl HTTPS range read; local cache is a small AOI window",
                "note": "small derived class window; no full WorldCover tile was stored",
            }
            _write_cached(array, aoi, crop_path, metadata_path, metadata)
            return _build_result(
                aoi,
                crop_path,
                metadata_path,
                array,
                metadata,
                remote_bytes=remote_bytes,
                from_cache=False,
            )
        except (rasterio.errors.RasterioError, IngestError, OSError) as exc:
            last_error = exc
            crop_path.unlink(missing_ok=True)
            metadata_path.unlink(missing_ok=True)
            if attempt < max_retries:
                sleeper(1.5 * (attempt + 1))
    raise IngestError(
        f"{aoi.location_id}: WorldCover range read failed after {max_retries + 1} "
        f"attempt(s) for tile(s) {', '.join(tile_ids)}: {last_error}"
    )


def build_context_observations(fractions: ClassFractions) -> dict[str, dict[str, Any]]:
    """Frozen ``context_observations`` shape, one entry per WorldCover class."""
    values = fractions.fractions
    observations: dict[str, dict[str, Any]] = {}
    for code, _slug, _label in WORLDCOVER_CLASSES:
        observations[FEATURE_ID_BY_CODE[code]] = {
            "value": round(values[code], 6),
            "unit": "fraction",
            "method_version": CONTEXT_METHOD_VERSION,
            "source_resolution_m": NATIVE_RESOLUTION_M,
            "valid_fraction": round(fractions.valid_fraction, 6),
            "quality": fractions.quality,
            "class_code": code,
            "provider": PROVIDER,
        }
    return observations


def _class_table() -> str:
    return "; ".join(f"{code} {label}" for code, _slug, label in WORLDCOVER_CLASSES)


def _dataset_notes() -> str:
    return (
        "ESA WorldCover 2021 v200 static annual land-cover map at 10 m (1/12000 degree) "
        "in EPSG:4326. Class code 0 is nodata and is excluded from fraction denominators. "
        f"Class codes: {_class_table()}. "
        "The AOI windows were read as bounded unsigned HTTPS range requests from the public "
        f"AWS Open Data bucket s3://{BUCKET} ({BUCKET_REGION}), prefix v200/2021/map/, with "
        "3-degree tiles named ESA_WorldCover_10m_2021_v200_<N|S>dd<W|E>ddd_Map.tif. "
        "Fractions use valid (non-nodata) pixels as the denominator and sum to 1 within "
        f"{FRACTION_SUM_TOLERANCE}; valid_fraction is the share of AOI pixels with data and "
        f"must be >= {MIN_VALID_FRACTION} for quality 'verified'. Body: Earth. "
        "This is Earth land-cover context only: it is not lunar mineralogy, not geology, "
        "not a terrain measurement, and not a similarity feature. Limitations: 10 m static "
        "2021 epoch, annual composite without a per-pixel date, cloud/seasonal and "
        "mixed-pixel artifacts, and helper-class semantics (for example 'bare / sparse "
        "vegetation' groups several surfaces). The published independent validation reports "
        "76.7% overall accuracy for 2021 v200. Small 2 km AOIs can be dominated by one "
        f"class and are not a regional or global survey. Citation: {CITATION}."
    )


def build_manifest(
    observations: list[AoiObservation],
    earth_document: dict[str, Any],
    *,
    generated_at: str | None = None,
    generated_by: str = "pipelines/worldcover/ingest_worldcover.py",
) -> dict[str, Any]:
    """Shape real WorldCover measurements as a pipeline-manifest 1.0.0 document."""
    if not observations:
        raise IngestError("refusing to write a manifest with zero successful AOIs")

    earth_by_id = {
        candidate["location_id"]: candidate for candidate in earth_document["candidates"]
    }
    access_timestamp = max(observation.access_timestamp for observation in observations)
    candidates: list[dict[str, Any]] = []

    for observation in observations:
        aoi = observation.aoi
        earth_candidate = earth_by_id.get(aoi.location_id)
        if earth_candidate is None:
            raise IngestError(
                f"{aoi.location_id}: no matching Earth candidate in the terrain manifest"
            )
        if any(
            not math.isclose(
                float(earth_candidate["bbox"][key]), float(aoi.bbox[key]), abs_tol=1e-9
            )
            for key in ("west", "south", "east", "north")
        ):
            raise IngestError(f"{aoi.location_id}: AOI bbox differs from the Earth manifest")
        context = build_context_observations(observation.fractions)
        if set(context) != set(FEATURE_ID_BY_CODE.values()):
            raise IngestError(f"{aoi.location_id}: incomplete WorldCover context coverage")
        fraction_sum = sum(item["value"] for item in context.values())
        if abs(fraction_sum - 1.0) > FRACTION_SUM_TOLERANCE:
            raise IngestError(
                f"{aoi.location_id}: context fractions sum to {fraction_sum:.6f}, expected 1.0"
            )

        earth_notes = str(earth_candidate.get("notes") or "").strip()
        context_note = (
            "context: ESA WorldCover 2021 v200 class fractions over the same AOI bbox at "
            f"native 10 m; tile(s) {', '.join(observation.tile_ids)}; "
            f"window_checksum={observation.checksum}; valid_fraction="
            f"{observation.fractions.valid_fraction:.6f}; "
            "terrain features above are copied verbatim from "
            "pipelines/earth/manifests/copernicus_glo90_earth.json for loader compatibility "
            "and were not recomputed or altered by this pipeline; WorldCover values are Earth "
            "land-cover context only, not mineralogy or geology."
        )
        notes = f"{earth_notes}; {context_note}".strip()

        candidates.append(
            {
                "location_id": earth_candidate["location_id"],
                "display_name": earth_candidate["display_name"],
                "lat": earth_candidate["lat"],
                "lon": earth_candidate["lon"],
                "bbox": copy.deepcopy(earth_candidate["bbox"]),
                "dataset_id": DATASET_ID,
                "window_size_m": earth_candidate["window_size_m"],
                "analysis_resolution_m": earth_candidate["analysis_resolution_m"],
                "crs": earth_candidate["crs"],
                "valid_fraction": earth_candidate["valid_fraction"],
                "quality_flags": list(earth_candidate.get("quality_flags", [])),
                "notes": notes,
                "features": copy.deepcopy(earth_candidate["features"]),
                "context_observations": context,
            }
        )

    composite = hashlib.sha256(
        "\n".join(sorted(observation.checksum for observation in observations)).encode("utf-8")
    ).hexdigest()

    return {
        "manifest_version": "1.0.0",
        "contract_version": "1.0.0",
        "generated_by": generated_by,
        "generated_at": generated_at or utc_now_iso(),
        "data_kind": "observed",
        "datasets": [
            {
                "dataset_id": DATASET_ID,
                "provider": PROVIDER,
                "product_id": PRODUCT_ID,
                "title": PRODUCT_TITLE,
                "url": DATA_ACCESS_URL,
                "license": LICENSE_NAME,
                "license_url": LICENSE_URL,
                "access_timestamp": access_timestamp,
                "source_crs": SOURCE_CRS,
                "native_resolution_m": NATIVE_RESOLUTION_M,
                "processing_version": CONTEXT_METHOD_VERSION,
                "checksum": "sha256:" + composite,
                "source_kind": "landcover",
                "notes": _dataset_notes(),
            }
        ],
        "candidates": candidates,
        "references": [],
    }


def load_earth_aois(manifest_path: str | Path) -> list[Aoi]:
    """Load the five Earth candidate bboxes from the committed terrain manifest."""
    manifest_path = Path(manifest_path)
    try:
        document = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise IngestError(f"Earth terrain manifest not found: {manifest_path}") from exc
    if document.get("data_kind") != "observed":
        raise IngestError(f"{manifest_path}: expected data_kind='observed'")
    aois: list[Aoi] = []
    for candidate in document.get("candidates", []):
        bbox = candidate.get("bbox")
        if not bbox:
            raise IngestError(f"{manifest_path}: candidate without bbox")
        aois.append(
            Aoi(
                location_id=str(candidate["location_id"]),
                display_name=str(candidate["display_name"]),
                lat=float(candidate["lat"]),
                lon=float(candidate["lon"]),
                bbox={key: float(bbox[key]) for key in ("west", "south", "east", "north")},
                earth_record=candidate,
            )
        )
    if not aois:
        raise IngestError(f"{manifest_path}: no Earth candidates found")
    return aois


def find_aoi(aois: list[Aoi], value: str) -> Aoi:
    wanted = value.strip().lower()
    if wanted.startswith("earth-"):
        wanted = wanted[len("earth-") :]
    matches = [aoi for aoi in aois if aoi.slug == wanted or aoi.location_id == value.strip()]
    if not matches:
        known = ", ".join(aoi.slug for aoi in aois)
        raise IngestError(f"unknown AOI {value!r}; known candidates: {known}")
    return matches[0]


def validate_manifest(document: dict[str, Any], schema_path: str | Path) -> None:
    """Validate with the JSON Schema contract and the API Pydantic model."""
    import jsonschema
    from analog_api.domain.manifest import Manifest

    schema_path = Path(schema_path)
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(document), key=lambda error: list(error.path))
    if errors:
        details = "; ".join(
            f"{'/'.join(str(part) for part in error.path) or '<root>'}: {error.message}"
            for error in errors[:10]
        )
        raise IngestError(f"manifest fails {schema_path.name}: {details}")

    Manifest.model_validate(document)


def write_manifest(document: dict[str, Any], path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".part")
    temporary.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)
    return path


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def default_cache_dir() -> Path:
    from analog_pipelines.paths import cache_dir

    return cache_dir("worldcover")


def default_earth_manifest_path() -> Path:
    return repo_root() / "pipelines" / "earth" / "manifests" / "copernicus_glo90_earth.json"


def default_manifest_path() -> Path:
    return repo_root() / "pipelines" / "worldcover" / "manifests" / "worldcover_earth_context.json"


def default_schema_path() -> Path:
    return repo_root() / "contracts" / "pipeline-manifest.schema.json"
