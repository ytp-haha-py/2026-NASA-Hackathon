"""Helpers for reading small Copernicus DEM GLO-90 windows from AWS Open Data.

The public bucket ``s3://copernicus-dem-90m`` (region ``eu-central-1``) is mirrored as
unsigned HTTPS at https://copernicus-dem-90m.s3.amazonaws.com/. GLO-90 COGs are named
``Copernicus_DSM_COG_30_<N|S>dd_00_<E|W>ddd_00_DEM`` where ``30`` is the resolution in
*arc seconds* (3.0"), not metres. Every tile is a 1 x 1 degree COG in EPSG:4326 with
1200 rows; the number of columns depends on latitude (see the bucket readme).

Only a small window around each requested centre is read over HTTPS and cached as a
tiny local GeoTIFF. Raw tiles are never stored. Feature extraction itself lives in
``analog_pipelines.features`` and is imported, never reimplemented here.
"""

from __future__ import annotations

import json
import math
import os
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
from analog_api.domain.features import METHOD_VERSION
from analog_pipelines.features import (
    ExtractionResult,
    extract_features,
    make_observations,
)
from pyproj import CRS, Transformer
from rasterio.windows import Window, from_bounds
from rasterio.windows import bounds as window_bounds

BUCKET = "copernicus-dem-90m"
BUCKET_REGION = "eu-central-1"
HTTPS_BASE = f"https://{BUCKET}.s3.amazonaws.com"
REGISTRY_URL = "https://registry.opendata.aws/copernicus-dem/"
BUCKET_README_URL = f"{HTTPS_BASE}/readme.html"
TILE_LIST_URL = f"{HTTPS_BASE}/tileList.txt"

DATASET_ID = "copernicus-dem-glo90"
PRODUCT_ID = "COP-DEM_GLO-90-DGED"
PRODUCT_TITLE = "Copernicus DEM GLO-90 Digital Surface Model (DSM), 3 arc-second COG"
PROVIDER = "European Space Agency (ESA) / Airbus Defence and Space; AWS Open Data mirror"
LICENSE_NAME = (
    "Copernicus DEM GLO-90: free basis for the general public (AWS Open Data registry), "
    "under the Copernicus Contributing Mission Data Access Licence terms"
)
LICENSE_URL = (
    "https://dataspace.copernicus.eu/explore-data/data-collections/"
    "copernicus-contributing-missions/collections-description/COP-DEM"
)
ATTRIBUTION = (
    "produced using Copernicus WorldDEM-90 © DLR e.V. 2010-2014 and "
    "© Airbus Defence and Space GmbH 2014-2018 provided under COPERNICUS by the "
    "European Union and ESA; all rights reserved"
)

SOURCE_CRS = "EPSG:4326"
NATIVE_RESOLUTION_M = 90.0
NATIVE_RESOLUTION_DEG = 1.0 / 1200.0  # 3 arc-seconds
ANALYSIS_WINDOW_M = 1980.0
ANALYSIS_RESOLUTION_M = 90.0
CROP_PAD_M = 1500.0
MAX_RETRIES = 2
DEFAULT_TIMEOUT_S = 25.0

TILE_PREFIX = "Copernicus_DSM_COG_30"
TILE_SUFFIX = "DEM"
_TILE_RE = re.compile(r"^Copernicus_DSM_COG_30_([NS])(\d{2})_00_([EW])(\d{3})_00_DEM$")
_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

DEFAULT_QUALITY_FLAGS = (
    "copernicus_dsm_may_include_vegetation_buildings",
    "glo90_native_3_arcsec_bilinear_to_90m",
)


class IngestError(RuntimeError):
    """A real ingestion problem; never silently replaced with synthetic data."""


class TileNotFoundError(IngestError):
    """The resolved S3 object does not exist (HTTP 403/404)."""


@dataclass(frozen=True, slots=True)
class Candidate:
    slug: str
    display_name: str
    lat: float
    lon: float

    @property
    def location_id(self) -> str:
        return location_id_for_slug(self.slug)

    @property
    def tile_id(self) -> str:
        return tile_id_for_lonlat(self.lon, self.lat)

    @property
    def tile_url(self) -> str:
        return tile_url(self.tile_id)


# Priority order requested by the project brief. Coordinates are approximate
# WGS84 centres used to select the 1.98 km analysis window; measured values and
# measured bboxes are written to the manifest.
CANDIDATES: tuple[Candidate, ...] = (
    Candidate("atacama-desert", "Atacama Desert, Chile", -23.50, -68.40),
    Candidate("death-valley", "Death Valley, California, USA", 36.25, -117.10),
    Candidate("lanzarote", "Lanzarote volcanic terrain, Spain", 29.04, -13.63),
    Candidate("devon-island", "Devon Island / Haughton, Nunavut, Canada", 75.40, -89.85),
    Candidate("rio-tinto", "Rio Tinto, Spain", 37.69, -6.55),
)


@dataclass(frozen=True, slots=True)
class RemoteObject:
    url: str
    status: int
    content_length: int | None
    etag: str | None
    last_modified: str | None


@dataclass(frozen=True, slots=True)
class CropResult:
    path: Path
    metadata_path: Path
    tile_id: str
    tile_url: str
    access_timestamp: str
    source_crs: str
    source_resolution_deg: tuple[float, float]
    source_shape: tuple[int, int]
    crop_shape: tuple[int, int]
    crop_bounds_wgs84: dict[str, float]
    remote_content_length: int | None
    remote_etag: str | None
    from_cache: bool


@dataclass(frozen=True, slots=True)
class CandidateMeasurement:
    candidate: Candidate
    crop: CropResult
    result: ExtractionResult


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def normalise_slug(text: str) -> str:
    """Lower-case ASCII slug used for location ids."""
    slug = re.sub(r"[^a-z0-9]+", "-", text.strip().lower()).strip("-")
    if not slug:
        raise IngestError(f"cannot derive a slug from {text!r}")
    return slug


def location_id_for_slug(slug: str) -> str:
    if not _SLUG_RE.fullmatch(slug):
        raise IngestError(
            f"invalid candidate slug {slug!r}; expected lowercase letters, digits and '-'"
        )
    return f"earth-{slug}"


def find_candidate(slug: str) -> Candidate:
    """Resolve ``atacama-desert`` or ``earth-atacama-desert`` to a known candidate."""
    wanted = slug.strip().lower()
    if wanted.startswith("earth-"):
        wanted = wanted[len("earth-") :]
    matches = [c for c in CANDIDATES if c.slug == wanted]
    if not matches:
        known = ", ".join(c.slug for c in CANDIDATES)
        raise IngestError(f"unknown candidate {slug!r}; known candidates: {known}")
    return matches[0]


def validate_tile_id(tile_id: str) -> str:
    if not _TILE_RE.fullmatch(tile_id):
        raise IngestError(f"invalid Copernicus GLO-90 tile id: {tile_id!r}")
    return tile_id


def tile_id_for_lonlat(lon: float, lat: float) -> str:
    """Return the 1-degree tile containing ``(lon, lat)``.

    Copernicus names tiles by the integer degree of their south-west corner. The
    published COG grid removes the shared south/east rows and columns, but the tile
    identifier still uses the floor of the coordinate. Longitudes are always three
    digits (``W069``) and latitudes two (``S24``).
    """
    if not (-180.0 <= lon < 180.0):
        raise IngestError(f"longitude {lon} outside [-180, 180)")
    if not (-90.0 < lat < 90.0):
        raise IngestError(f"latitude {lat} outside (-90, 90)")
    lat_floor = math.floor(lat)
    lon_floor = math.floor(lon)
    ns = "N" if lat_floor >= 0 else "S"
    ew = "E" if lon_floor >= 0 else "W"
    return f"{TILE_PREFIX}_{ns}{abs(lat_floor):02d}_00_{ew}{abs(lon_floor):03d}_00_{TILE_SUFFIX}"


def tile_bounds_from_id(tile_id: str) -> dict[str, float]:
    """Nominal 1-degree WGS84 bounds encoded by a tile id (before COG edge trimming)."""
    match = _TILE_RE.fullmatch(validate_tile_id(tile_id))
    assert match is not None
    ns, lat_text, ew, lon_text = match.groups()
    lat = int(lat_text) * (1 if ns == "N" else -1)
    lon = int(lon_text) * (1 if ew == "E" else -1)
    return {
        "west": float(lon),
        "south": float(lat),
        "east": float(lon + 1),
        "north": float(lat + 1),
    }


def tile_key(tile_id: str) -> str:
    tile_id = validate_tile_id(tile_id)
    return f"{tile_id}/{tile_id}.tif"


def tile_url(tile_id: str, bucket: str = BUCKET) -> str:
    return f"https://{bucket}.s3.amazonaws.com/{tile_key(tile_id)}"


def tile_id_from_key(key: str) -> str:
    """Parse ``<tile>/<tile>.tif`` (or a bare tile id) back to a validated tile id."""
    base = key[:-4] if key.endswith(".tif") else key
    parts = base.split("/")
    if len(parts) == 1:
        return validate_tile_id(parts[0])
    if len(parts) == 2 and parts[0] == parts[1]:
        return validate_tile_id(parts[0])
    raise IngestError(f"unexpected Copernicus object key: {key!r}")


def utm_epsg(lon: float, lat: float) -> int:
    """UTM zone selection identical to the shared extractor's geographic branch."""
    if not (-80.0 <= lat <= 84.0):
        raise IngestError(
            f"latitude {lat} outside the automatic UTM range [-80, 84]; "
            "the shared extractor cannot analyse this window"
        )
    if not (-180.0 <= lon < 180.0):
        raise IngestError(f"longitude {lon} outside [-180, 180)")
    zone = int((lon + 180.0) // 6.0) + 1
    return (32600 if lat >= 0 else 32700) + zone


def source_window_bounds(lon: float, lat: float, pad_m: float = CROP_PAD_M) -> dict[str, float]:
    """WGS84 bounds needed to cover a 1.98 km analysis window plus ``pad_m`` metres.

    The analysis window is metric (UTM); converting its corners back to WGS84 avoids
    under-reading longitude at high latitudes.
    """
    analysis_crs = CRS.from_epsg(utm_epsg(lon, lat))
    to_metric = Transformer.from_crs(SOURCE_CRS, analysis_crs, always_xy=True)
    to_wgs84 = Transformer.from_crs(analysis_crs, SOURCE_CRS, always_xy=True)
    x, y = to_metric.transform(lon, lat)
    half = ANALYSIS_WINDOW_M / 2.0 + pad_m
    xs = [x - half, x + half, x - half, x + half]
    ys = [y - half, y - half, y + half, y + half]
    lons, lats = to_wgs84.transform(xs, ys)
    return {
        "west": min([lon, *lons]),
        "south": min([lat, *lats]),
        "east": max([lon, *lons]),
        "north": max([lat, *lats]),
    }


def round_bbox(bbox: dict[str, float], digits: int = 8) -> dict[str, float]:
    return {key: round(float(bbox[key]), digits) for key in ("west", "south", "east", "north")}


def bbox_contains(bbox: dict[str, float], lon: float, lat: float, tolerance: float = 0.0) -> bool:
    return (
        bbox["west"] - tolerance <= lon <= bbox["east"] + tolerance
        and bbox["south"] - tolerance <= lat <= bbox["north"] + tolerance
    )


def head_remote_object(
    url: str,
    *,
    timeout: float = DEFAULT_TIMEOUT_S,
    max_retries: int = MAX_RETRIES,
    opener: Callable[..., Any] = urllib.request.urlopen,
    sleeper: Callable[[float], None] = time.sleep,
) -> RemoteObject:
    """HEAD the public object with bounded retries.

    The opener/sleeper arguments exist only so offline unit tests can inject fakes.
    """
    last_error: Exception | None = None
    for attempt in range(max_retries + 1):
        request = urllib.request.Request(
            url, method="HEAD", headers={"User-Agent": "planetary-analog-earth/0.1"}
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
                    f"Copernicus tile not found at {url} (HTTP {exc.code}). "
                    "The tile id/URL was resolved from the real bucket key pattern; "
                    "verify the coordinate against tileList.txt."
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


def read_source_crop(
    source: str | Path,
    candidate: Candidate,
    out_path: str | Path,
    *,
    source_label: str | None = None,
) -> dict[str, Any]:
    """Copy a padded WGS84 window around ``candidate`` into a small local GeoTIFF.

    ``source`` may be a local path or a GDAL URL such as ``/vsicurl/https://...``.
    Returns provenance metadata. Raises IngestError on CRS, resolution or coverage
    problems rather than guessing.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    bounds = source_window_bounds(candidate.lon, candidate.lat)

    with rasterio.open(source) as src:
        src_crs = CRS.from_user_input(src.crs) if src.crs else None
        if src_crs is None:
            raise IngestError(f"{source_label or source}: source raster has no CRS")
        if src_crs != CRS.from_epsg(4326):
            raise IngestError(
                f"{source_label or source}: expected EPSG:4326 Copernicus GLO-90, "
                f"found {src_crs.to_string()}"
            )
        if src.count < 1:
            raise IngestError(f"{source_label or source}: source raster has no bands")
        y_res = abs(float(src.res[1]))
        if not math.isclose(y_res, NATIVE_RESOLUTION_DEG, rel_tol=0.0, abs_tol=1e-9):
            raise IngestError(
                f"{source_label or source}: expected 3 arc-second latitude spacing "
                f"({NATIVE_RESOLUTION_DEG:.10f} deg), found {y_res:.10f} deg"
            )

        ds_bounds = src.bounds
        if not (
            ds_bounds.left <= candidate.lon <= ds_bounds.right
            and ds_bounds.bottom <= candidate.lat <= ds_bounds.top
        ):
            raise IngestError(
                f"{source_label or source}: candidate centre "
                f"({candidate.lon}, {candidate.lat}) lies outside tile bounds {ds_bounds}"
            )

        left = max(bounds["west"], ds_bounds.left)
        bottom = max(bounds["south"], ds_bounds.bottom)
        right = min(bounds["east"], ds_bounds.right)
        top = min(bounds["north"], ds_bounds.top)
        window = (
            from_bounds(left, bottom, right, top, src.transform).round_offsets().round_lengths()
        )
        col_off = int(max(0, min(window.col_off, src.width - 1)))
        row_off = int(max(0, min(window.row_off, src.height - 1)))
        width = int(min(window.width, src.width - col_off))
        height = int(min(window.height, src.height - row_off))
        if width < 3 or height < 3:
            raise IngestError(
                f"{source_label or source}: padded crop would be {width}x{height} pixels, too small"
            )
        window = Window(col_off, row_off, width, height)
        data = src.read(1, window=window, masked=True, out_dtype="float32")
        array = data.filled(np.nan) if np.ma.isMaskedArray(data) else np.asarray(data)
        array = np.asarray(array, dtype="float32")
        if np.isfinite(array).sum() == 0:
            raise IngestError(f"{source_label or source}: crop contains no valid elevation cells")

        crop_transform = src.window_transform(window)
        crop_left, crop_bottom, crop_right, crop_top = window_bounds(window, src.transform)
        profile = {
            "driver": "GTiff",
            "height": height,
            "width": width,
            "count": 1,
            "dtype": "float32",
            "crs": src.crs,
            "transform": crop_transform,
            "compress": "deflate",
            "tiled": False,
            "BIGTIFF": "IF_SAFER",
        }
        if src.nodata is not None:
            profile["nodata"] = src.nodata

        temporary = out_path.with_name(out_path.name + ".part")
        try:
            with rasterio.open(temporary, "w", **profile) as dst:
                dst.write(array, 1)
            os.replace(temporary, out_path)
        finally:
            temporary.unlink(missing_ok=True)

        metadata = {
            "schema": "earth-copernicus-crop/1",
            "location_id": candidate.location_id,
            "centre_lonlat": [candidate.lon, candidate.lat],
            "tile_id": candidate.tile_id,
            "tile_url": candidate.tile_url,
            "source_crs": src_crs.to_string(),
            "source_resolution_deg": [float(src.res[0]), float(src.res[1])],
            "source_shape": [int(src.height), int(src.width)],
            "source_nodata": src.nodata,
            "crop_shape": [height, width],
            "crop_bounds_wgs84": round_bbox(
                {
                    "west": crop_left,
                    "south": crop_bottom,
                    "east": crop_right,
                    "north": crop_top,
                }
            ),
            "crop_transform": list(crop_transform)[:6],
            "note": "small derived crop for feature extraction; raw COG was not stored",
        }
        return metadata


def _read_cache_metadata(path: Path, candidate: Candidate) -> dict[str, Any] | None:
    try:
        metadata = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if metadata.get("location_id") != candidate.location_id:
        return None
    if metadata.get("tile_id") != candidate.tile_id:
        return None
    return metadata


def _cached_crop_is_usable(path: Path, candidate: Candidate) -> bool:
    """Cheap integrity check so a truncated/corrupt crop is re-read, not trusted."""
    try:
        with rasterio.open(path) as src:
            if src.crs is None or CRS.from_user_input(src.crs) != CRS.from_epsg(4326):
                return False
            if src.width < 3 or src.height < 3:
                return False
            bounds = src.bounds
            return (
                bounds.left <= candidate.lon <= bounds.right
                and bounds.bottom <= candidate.lat <= bounds.top
            )
    except (rasterio.errors.RasterioError, OSError, ValueError):
        return False


def fetch_crop(
    candidate: Candidate,
    cache_dir: str | Path,
    *,
    force: bool = False,
    timeout: float = DEFAULT_TIMEOUT_S,
    max_retries: int = MAX_RETRIES,
    sleeper: Callable[[float], None] = time.sleep,
) -> CropResult:
    """Return a cached crop, or range-read one from the public S3 COG."""
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    crop_path = cache_dir / f"{candidate.slug}.tif"
    metadata_path = cache_dir / f"{candidate.slug}.json"

    if not force and crop_path.is_file():
        cached = _read_cache_metadata(metadata_path, candidate)
        if cached is not None and _cached_crop_is_usable(crop_path, candidate):
            return CropResult(
                path=crop_path,
                metadata_path=metadata_path,
                tile_id=cached["tile_id"],
                tile_url=cached["tile_url"],
                access_timestamp=cached["access_timestamp"],
                source_crs=cached["source_crs"],
                source_resolution_deg=tuple(cached["source_resolution_deg"]),  # type: ignore[arg-type]
                source_shape=tuple(cached["source_shape"]),  # type: ignore[arg-type]
                crop_shape=tuple(cached["crop_shape"]),  # type: ignore[arg-type]
                crop_bounds_wgs84=cached["crop_bounds_wgs84"],
                remote_content_length=cached.get("remote_content_length"),
                remote_etag=cached.get("remote_etag"),
                from_cache=True,
            )

    url = candidate.tile_url
    remote = head_remote_object(url, timeout=timeout, max_retries=max_retries, sleeper=sleeper)
    last_error: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            with rasterio_remote_env(timeout):
                metadata = read_source_crop(
                    "/vsicurl/" + url,
                    candidate,
                    crop_path,
                    source_label=url,
                )
            metadata.update(
                {
                    "access_timestamp": utc_now_iso(),
                    "remote_content_length": remote.content_length,
                    "remote_etag": remote.etag,
                    "remote_last_modified": remote.last_modified,
                    "read_mode": "gdal /vsicurl HTTPS range read; local cache is a small crop",
                }
            )
            temporary = metadata_path.with_name(metadata_path.name + ".part")
            temporary.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
            os.replace(temporary, metadata_path)
            return CropResult(
                path=crop_path,
                metadata_path=metadata_path,
                tile_id=candidate.tile_id,
                tile_url=url,
                access_timestamp=metadata["access_timestamp"],
                source_crs=metadata["source_crs"],
                source_resolution_deg=tuple(metadata["source_resolution_deg"]),  # type: ignore[arg-type]
                source_shape=tuple(metadata["source_shape"]),  # type: ignore[arg-type]
                crop_shape=tuple(metadata["crop_shape"]),  # type: ignore[arg-type]
                crop_bounds_wgs84=metadata["crop_bounds_wgs84"],
                remote_content_length=remote.content_length,
                remote_etag=remote.etag,
                from_cache=False,
            )
        except (rasterio.errors.RasterioError, IngestError, OSError) as exc:
            last_error = exc
            crop_path.unlink(missing_ok=True)
            if attempt < max_retries:
                sleeper(1.5 * (attempt + 1))
    raise IngestError(
        f"{candidate.location_id}: remote COG read failed after {max_retries + 1} "
        f"attempt(s) for {url}: {last_error}"
    )


def extract_candidate(candidate: Candidate, crop: CropResult) -> ExtractionResult:
    """Run the shared terrain-metrics extractor on the cached crop."""
    return extract_features(
        str(crop.path),
        center_lonlat=(candidate.lon, candidate.lat),
        source_label=crop.tile_url,
        body="Earth",
    )


def build_manifest(
    measurements: list[CandidateMeasurement],
    *,
    generated_at: str | None = None,
    generated_by: str = "pipelines/earth/ingest_copernicus.py",
) -> dict[str, Any]:
    """Shape real measurements as a pipeline-manifest 1.0.0 document."""
    if not measurements:
        raise IngestError("refusing to write a manifest with zero successful windows")
    access_timestamp = max(m.crop.access_timestamp for m in measurements)
    candidates: list[dict[str, Any]] = []
    for measurement in measurements:
        candidate = measurement.candidate
        result = measurement.result
        if result.bbox_wgs84 is None:
            raise IngestError(f"{candidate.location_id}: extractor returned no WGS84 bbox")
        bbox = round_bbox(result.bbox_wgs84)
        lon = round(float(result.center_lon if result.center_lon is not None else candidate.lon), 8)
        lat = round(float(result.center_lat if result.center_lat is not None else candidate.lat), 8)
        if not bbox_contains(bbox, lon, lat, tolerance=1e-9):
            raise IngestError(
                f"{candidate.location_id}: measured centre ({lon}, {lat}) is outside "
                f"measured bbox {bbox}"
            )
        warnings = ", ".join(result.warnings) if result.warnings else "none"
        notes = (
            f"Copernicus DEM GLO-90 tile {measurement.crop.tile_id}; "
            f"source COG read via HTTPS range request and cropped locally; "
            f"window_checksum={result.window_checksum}; warnings={warnings}"
        )
        candidates.append(
            {
                "location_id": candidate.location_id,
                "display_name": candidate.display_name,
                "lat": lat,
                "lon": lon,
                "bbox": bbox,
                "dataset_id": DATASET_ID,
                "window_size_m": float(result.window_size_m),
                "analysis_resolution_m": float(result.resolution_m),
                "crs": result.analysis_crs,
                "valid_fraction": round(float(result.valid_fraction), 6),
                "quality_flags": list(DEFAULT_QUALITY_FLAGS),
                "notes": notes,
                "features": make_observations(
                    result.features,
                    valid_fraction=result.valid_fraction,
                    quality="verified",
                ),
            }
        )

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
                "url": REGISTRY_URL,
                "license": LICENSE_NAME,
                "license_url": LICENSE_URL,
                "access_timestamp": access_timestamp,
                "source_crs": SOURCE_CRS,
                "native_resolution_m": NATIVE_RESOLUTION_M,
                "processing_version": METHOD_VERSION,
                "checksum": None,
                "source_kind": "earth_dem",
                "notes": (
                    "GLO-90 DSM (COP-DEM_GLO-90-DGED source) from the 2021 Copernicus DEM "
                    "release, mirrored as public COGs in s3://copernicus-dem-90m "
                    f"({BUCKET_REGION}). 3 arc-second native grid, EPSG:4326, EGM2008 "
                    "heights in metres. DSM includes buildings, infrastructure and "
                    "vegetation; ocean areas have no tiles. Each COG uses 2048x2048 "
                    "internal blocks, so a window range-read may transfer the containing "
                    f"block. Attribution: {ATTRIBUTION}."
                ),
            }
        ],
        "candidates": candidates,
        "references": [],
    }


def validate_manifest(document: dict[str, Any], schema_path: str | Path) -> None:
    """Validate with both the JSON Schema contract and the API Pydantic model."""
    import jsonschema

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

    from analog_api.domain.manifest import Manifest

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
    return repo_root() / "data" / "cache" / "copernicus"


def default_manifest_path() -> Path:
    return repo_root() / "pipelines" / "earth" / "manifests" / "copernicus_glo90_earth.json"


def default_schema_path() -> Path:
    return repo_root() / "contracts" / "pipeline-manifest.schema.json"
