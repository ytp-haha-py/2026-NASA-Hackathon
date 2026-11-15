"""Global Moon D01 terrain features for arbitrary AOI bounding boxes.

Reads NASA LOLA DEM products for the full requested bbox (not a fixed 5 km center
window), computes the five D01 terrain-shape features on a body-fixed metric analysis
grid, and reports pixel counts / valid fraction / partial coverage / invalid regions /
source provenance.

Primary global source: NASA PGDA LRO/LOLA LDEM128 (MOON_PA, 128 ppd, pixel-registered,
global 0-360 lon geographic, lunar radius 1737400 m). Polar fallback: NASA PGDA LOLA
polar stereographic DEM COGs. Low-resolution fallback: NASA SVS global lunar DEM GeoTIFF.

No model output or committed pilot fixture is presented as global coverage. Missing or
unreadable data is reported as no_data/partial/invalid; values are never zero-filled.
Moon coordinates are planetocentric/body-fixed, never Earth WGS84/EPSG:4326.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
from pyproj import CRS, Geod, Transformer
from rasterio.enums import Resampling
from rasterio.transform import from_origin
from rasterio.warp import reproject
from rasterio.windows import Window, from_bounds

FEATURES_DIR = Path(__file__).resolve().parents[1] / "src"
if str(FEATURES_DIR) not in sys.path:
    sys.path.insert(0, str(FEATURES_DIR))
from analog_pipelines.features import (  # noqa: E402
    ANALYSIS_RESOLUTION_M,
    MIN_VALID_FRACTION,
    horn_slope_degrees,
    terrain_metrics,
)

BODY = "Moon"
LUNAR_RADIUS_M = 1_737_400.0
MOON_GEODETIC_CRS = CRS.from_proj4(f"+proj=longlat +R={LUNAR_RADIUS_M} +no_defs")
MOON_SPHERE_GEOD = Geod(a=LUNAR_RADIUS_M, b=LUNAR_RADIUS_M)

ANALYSIS_RESOLUTION_DEFAULT_M = float(ANALYSIS_RESOLUTION_M)
DEFAULT_MAX_PIXELS = 4_000_000
DEFAULT_MAX_SIDE_M = 250_000.0
DEFAULT_TIMEOUT_S = 180.0
MAX_RETRIES = 2

D01_FEATURE_IDS = (
    "slope_mean_deg",
    "slope_p90_deg",
    "terrain_relief_p95_p05_m",
    "roughness_std_m",
    "flat_terrain_fraction",
)


class LolaGlobalError(RuntimeError):
    def __init__(self, code: str, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


@dataclass(frozen=True, slots=True)
class LolaSource:
    source_id: str
    provider: str
    title: str
    url: str
    raster_url: str
    product_page: str
    license: str
    license_url: str | None
    citation: str
    version: str
    checksum: str | None
    crs_wkt: str | None
    crs_proj4: str | None
    native_resolution_m: float | None
    native_ppd: float | None
    nodata: float | None
    extent: dict[str, float]
    lon_convention: str  # "0_360" | "-180_180"
    coverage_kind: str  # "global" | "polar_north" | "polar_south" | "fallback_global"
    is_cog: bool | None
    tiled: bool | None
    size_bytes: int | None
    notes: str
    quality_flags: tuple[str, ...] = ()

    def to_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["quality_flags"] = list(self.quality_flags)
        return payload


MOON_LOLA128_GLOBAL = LolaSource(
    source_id="moon-lola128-pa-pixel-202405",
    provider="NASA GSFC Planetary Geodesy Data Archive (PGDA) / LRO-LOLA",
    title="LOLA MOON_PA global shape model, 128 pixels/degree, pixel-registered",
    url="https://pgda.gsfc.nasa.gov/data/LOLA_PA/LDEM128_PA_pixel_202405.tif",
    raster_url="https://pgda.gsfc.nasa.gov/data/LOLA_PA/LDEM128_PA_pixel_202405.tif",
    product_page="https://pgda.gsfc.nasa.gov/products/95",
    license=(
        "not stated on the PGDA product page; public NASA archive with a citation "
        "requirement (Data Usage Policy)"
    ),
    license_url="https://science.data.nasa.gov/about/license",
    citation=(
        "Neumann, G. (2024). LOLA MOON_PA gridded dataset [Data set]. NASA Goddard "
        "Space Flight Center Planetary Geodesy Data Archive. doi:10.60903/LOLA_PA"
    ),
    version="202405",
    checksum=None,  # not published/found on product page
    crs_wkt=None,
    crs_proj4=f"+proj=longlat +R={LUNAR_RADIUS_M} +no_defs",
    native_resolution_m=2.0 * math.pi * LUNAR_RADIUS_M / (360.0 * 128.0),
    native_ppd=128.0,
    nodata=float("nan"),
    extent={"west": 0.0, "south": -90.0, "east": 360.0, "north": 90.0},
    lon_convention="0_360",
    coverage_kind="global",
    is_cog=False,
    tiled=True,
    size_bytes=2_705_286_892,
    notes=(
        "Pixel-registered global GeoTIFF (46080x23040, float32, deflate, 256x256 tiles). "
        "Derived in MOON_PA (DE421). Vertical unit not explicitly tagged; treated as metres "
        "(inferred from magnitude). Bounded /vsicurl range reads recommended."
    ),
    quality_flags=(
        "global_shape_model",
        "derived_interpolated_in_part",
        "moon_pa_de421",
        "pixel_registered",
        "vertical_unit_inferred",
    ),
)

MOON_LOLA_SOUTH_80S_20MPP = LolaSource(
    source_id="moon-lola-80s-20mpp-adj",
    provider="NASA GSFC Planetary Geodesy Data Archive (PGDA) / LRO-LOLA",
    title="LOLA south polar DEM 80-90S, 20 m/px, adjusted",
    url="https://pgda.gsfc.nasa.gov/data/LOLA_20mpp/LDEM_80S_20MPP_ADJ.TIF",
    raster_url="https://pgda.gsfc.nasa.gov/data/LOLA_20mpp/LDEM_80S_20MPP_ADJ.TIF",
    product_page="https://pgda.gsfc.nasa.gov/products/90",
    license=(
        "public NASA archive; citation requested (Barker et al. 2023 PSJ 4, 183, "
        "doi:10.3847/PSJ/acf3e1; data doi:10.60903/gsfcpgda-lola-spole)"
    ),
    license_url="https://doi.org/10.60903/gsfcpgda-lola-spole",
    citation="Barker, M. K., et al. (2023). PSJ 4, 183. doi:10.3847/PSJ/acf3e1",
    version="Product 90",
    checksum=None,
    crs_wkt=None,
    crs_proj4=f"+proj=stere +lat_0=-90 +lon_0=0 +k=1 +x_0=0 +y_0=0 +R={LUNAR_RADIUS_M} +units=m +no_defs",
    native_resolution_m=20.0,
    native_ppd=None,
    nodata=None,
    extent={"west": -180.0, "south": -90.0, "east": 180.0, "north": -80.0},
    lon_convention="-180_180",
    coverage_kind="polar_south",
    is_cog=True,
    tiled=True,
    size_bytes=2_600_000_000,
    notes=(
        "South polar stereographic MOON_ME (DE421). COG stated on product page; HTTP Range "
        "supported. Covers 80-90S. Nodata/dtype/checksum verify from file."
    ),
    quality_flags=("polar_stereographic", "moon_me_de421", "cog_stated"),
)

MOON_LOLA_NORTH_80N_20MPP = LolaSource(
    source_id="moon-lola-80n-20mpp-adj",
    provider="NASA GSFC Planetary Geodesy Data Archive (PGDA) / LRO-LOLA",
    title="LOLA north polar DEM 80-90N, 20 m/px, adjusted",
    url="https://pgda.gsfc.nasa.gov/data/LOLA_20mpp_NP/LDEM_80N_20MPP_ADJ.TIF",
    raster_url="https://pgda.gsfc.nasa.gov/data/LOLA_20mpp_NP/LDEM_80N_20MPP_ADJ.TIF",
    product_page="https://pgda.gsfc.nasa.gov/products/98",
    license=(
        "public NASA archive; citation requested (Barker et al. 2025 PSJ 6, 83, "
        "doi:10.3847/PSJ/adbc9d; data doi:10.60903/gsfcpgda-lola-poles)"
    ),
    license_url="https://doi.org/10.60903/gsfcpgda-lola-poles",
    citation="Barker, M. K., et al. (2025). PSJ 6, 83. doi:10.3847/PSJ/adbc9d",
    version="Product 98",
    checksum=None,
    crs_wkt=None,
    crs_proj4=f"+proj=stere +lat_0=90 +lon_0=0 +k=1 +x_0=0 +y_0=0 +R={LUNAR_RADIUS_M} +units=m +no_defs",
    native_resolution_m=20.0,
    native_ppd=None,
    nodata=None,
    extent={"west": -180.0, "south": 80.0, "east": 180.0, "north": 90.0},
    lon_convention="-180_180",
    coverage_kind="polar_north",
    is_cog=True,
    tiled=True,
    size_bytes=2_700_000_000,
    notes=(
        "North polar stereographic MOON_ME (DE421). COG stated on product page; HTTP Range "
        "supported. Covers 80-90N. Nodata/dtype/checksum verify from file."
    ),
    quality_flags=("polar_stereographic", "moon_me_de421", "cog_stated"),
)

MOON_SVS_LDEM_16 = LolaSource(
    source_id="nasa-svs-4720-ldem-16ppd",
    provider="NASA Scientific Visualization Studio / LRO-LOLA",
    title="SVS CGI Moon Kit LOLA global displacement map, 16 pixels/degree",
    url="https://svs.gsfc.nasa.gov/4720",
    raster_url="https://svs.gsfc.nasa.gov/vis/a000000/a004700/a004720/ldem_16.tif",
    product_page="https://svs.gsfc.nasa.gov/4720",
    license="NASA SVS asset; underlying LOLA/LROC data public. Explicit SPDX license not stated.",
    license_url="https://svs.gsfc.nasa.gov/4720",
    citation="NASA SVS, CGI Moon Kit (SVS 4720), LRO/LOLA DEM.",
    version="SVS 4720 (2019-09-06, updated 2026-01-09)",
    checksum=None,
    crs_wkt=None,
    crs_proj4=f"+proj=longlat +R={LUNAR_RADIUS_M} +no_defs",
    native_resolution_m=2.0 * math.pi * LUNAR_RADIUS_M / (360.0 * 16.0),
    native_ppd=16.0,
    nodata=None,
    extent={"west": -180.0, "south": -90.0, "east": 180.0, "north": 90.0},
    lon_convention="-180_180",
    coverage_kind="fallback_global",
    is_cog=False,
    tiled=None,
    size_bytes=63_300_000,
    notes=(
        "Global cylindrical low-resolution fallback (5760x2880). Exact CRS/nodata/tiling "
        "verify from file; HTTP Range supported."
    ),
    quality_flags=("global_fallback", "low_resolution", "svs_reformat"),
)

DEFAULT_SOURCES: tuple[LolaSource, ...] = (
    MOON_LOLA_NORTH_80N_20MPP,
    MOON_LOLA_SOUTH_80S_20MPP,
    MOON_LOLA128_GLOBAL,
    MOON_SVS_LDEM_16,
)
SOURCE_BY_ID = {source.source_id: source for source in DEFAULT_SOURCES}


@dataclass(frozen=True, slots=True)
class AoiRequest:
    body: str
    requested_bbox: dict[str, float]
    normalized_bbox: dict[str, float]
    crossing_antimeridian: bool
    pieces: tuple[dict[str, float], ...]
    analysis_resolution_m: float
    max_pixels: int
    max_side_m: float


@dataclass(slots=True)
class AoiResult:
    status: str
    body: str
    requested_bbox: dict[str, float]
    normalized_bbox: dict[str, float]
    analysis_bbox: dict[str, float] | None
    source: dict[str, Any] | None
    features: dict[str, float] | None
    analysis_resolution_m: float
    analysis_crs: str | None
    pixel_count: int
    valid_pixel_count: int
    invalid_pixel_count: int
    nodata_pixel_count: int
    valid_fraction: float
    partial: bool
    invalid: bool
    no_data: bool
    warnings: list[str] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    pieces: list[dict[str, Any]] = field(default_factory=list)
    generated_at: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat(timespec="seconds")
    )

    def to_payload(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "body": self.body,
            "requested_bbox": self.requested_bbox,
            "normalized_bbox": self.normalized_bbox,
            "analysis_bbox": self.analysis_bbox,
            "source": self.source,
            "features": self.features,
            "analysis_resolution_m": self.analysis_resolution_m,
            "analysis_crs": self.analysis_crs,
            "pixel_count": self.pixel_count,
            "valid_pixel_count": self.valid_pixel_count,
            "invalid_pixel_count": self.invalid_pixel_count,
            "nodata_pixel_count": self.nodata_pixel_count,
            "valid_fraction": self.valid_fraction,
            "partial": self.partial,
            "invalid": self.invalid,
            "no_data": self.no_data,
            "warnings": self.warnings,
            "errors": self.errors,
            "pieces": self.pieces,
            "generated_at": self.generated_at,
        }


def normalize_lon(value: float) -> float:
    normalized = (float(value) + 180.0) % 360.0 - 180.0
    return 180.0 if normalized == -180.0 and value > 0 else normalized


def lon_to_0_360(lon: float) -> float:
    value = float(lon) % 360.0
    if value < 0:
        value += 360.0
    return value


def _bbox_values(bbox: Mapping[str, float] | Sequence[float]) -> dict[str, float]:
    if isinstance(bbox, Mapping):
        try:
            values = {
                "west": float(bbox["west"]),
                "south": float(bbox["south"]),
                "east": float(bbox["east"]),
                "north": float(bbox["north"]),
            }
        except (KeyError, TypeError, ValueError) as exc:
            raise LolaGlobalError("invalid_bbox", "bbox must contain west/south/east/north") from exc
    elif isinstance(bbox, Sequence) and not isinstance(bbox, (str, bytes)) and len(bbox) == 4:
        west, south, east, north = (float(v) for v in bbox)
        values = {"west": west, "south": south, "east": east, "north": north}
    else:
        raise LolaGlobalError("invalid_bbox", "bbox must be a mapping or [west,south,east,north]")
    if not all(math.isfinite(v) for v in values.values()):
        raise LolaGlobalError("invalid_bbox", "bbox values must be finite")
    return values


def validate_aoi(
    bbox: Mapping[str, float] | Sequence[float],
    *,
    analysis_resolution_m: float = ANALYSIS_RESOLUTION_DEFAULT_M,
    max_pixels: int = DEFAULT_MAX_PIXELS,
    max_side_m: float = DEFAULT_MAX_SIDE_M,
) -> AoiRequest:
    values = _bbox_values(bbox)
    west, south, east, north = values["west"], values["south"], values["east"], values["north"]
    if not (-90.0 <= south < north <= 90.0):
        raise LolaGlobalError("invalid_bbox", "bbox must satisfy -90 <= south < north <= 90")
    if not (-360.0 <= west <= 360.0 and -360.0 <= east <= 360.0):
        raise LolaGlobalError("invalid_bbox", "longitudes must be within [-360, 360]")
    if west == east:
        raise LolaGlobalError("invalid_bbox", "bbox must have non-zero longitude extent")

    w = normalize_lon(west)
    e = normalize_lon(east)
    crossing = w > e
    span = (180.0 - w) + (e + 180.0) if crossing else e - w
    if not (0.0 < span <= 180.0):
        raise LolaGlobalError(
            "bbox_too_wide",
            f"longitude span is {span:.6f} degrees; request must span <= 180 degrees",
        )

    center_lat = (south + north) / 2.0
    center_lon = normalize_lon(w + span / 2.0)
    east_unwrapped = e + 360.0 if crossing else e
    width_m = abs(
        float(MOON_SPHERE_GEOD.inv(w, center_lat, east_unwrapped, center_lat)[2])
    )
    height_m = abs(float(MOON_SPHERE_GEOD.inv(center_lon, south, center_lon, north)[2]))
    if max(width_m, height_m) > max_side_m:
        raise LolaGlobalError(
            "aoi_too_large",
            f"AOI side {max(width_m, height_m):.1f} m exceeds max_side_m={max_side_m:.1f}",
            details={"width_m": width_m, "height_m": height_m, "max_side_m": max_side_m},
        )
    if min(width_m, height_m) <= 0:
        raise LolaGlobalError("invalid_bbox", "AOI has zero metric extent")

    if crossing:
        pieces = (
            {"west": w, "south": south, "east": 180.0, "north": north},
            {"west": -180.0, "south": south, "east": e, "north": north},
        )
    else:
        pieces = ({"west": w, "south": south, "east": e, "north": north},)

    normalized_bbox = {"west": w, "south": south, "east": e, "north": north}
    requested_bbox = {"west": west, "south": south, "east": east, "north": north}
    return AoiRequest(
        body=BODY,
        requested_bbox=requested_bbox,
        normalized_bbox=normalized_bbox,
        crossing_antimeridian=crossing,
        pieces=pieces,
        analysis_resolution_m=float(analysis_resolution_m),
        max_pixels=int(max_pixels),
        max_side_m=float(max_side_m),
    )


def _analysis_crs_for_bbox(bbox: Mapping[str, float]) -> CRS:
    center_lon = (float(bbox["west"]) + float(bbox["east"])) / 2.0
    center_lat = (float(bbox["south"]) + float(bbox["north"])) / 2.0
    if center_lat >= 60.0:
        return CRS.from_proj4(
            f"+proj=stere +lat_0=90 +lat_ts=90 +lon_0={center_lon:.8f} "
            f"+R={LUNAR_RADIUS_M} +units=m +no_defs"
        )
    if center_lat <= -60.0:
        return CRS.from_proj4(
            f"+proj=stere +lat_0=-90 +lat_ts=-90 +lon_0={center_lon:.8f} "
            f"+R={LUNAR_RADIUS_M} +units=m +no_defs"
        )
    return CRS.from_proj4(
        f"+proj=aeqd +lat_0={center_lat:.8f} +lon_0={center_lon:.8f} "
        f"+R={LUNAR_RADIUS_M} +units=m +no_defs"
    )


def _bbox_to_metric_bounds(bbox: Mapping[str, float], dst_crs: CRS) -> tuple[float, float, float, float]:
    transformer = Transformer.from_crs(MOON_GEODETIC_CRS, dst_crs, always_xy=True)
    corners_lon = [bbox["west"], bbox["east"], bbox["west"], bbox["east"]]
    corners_lat = [bbox["south"], bbox["south"], bbox["north"], bbox["north"]]
    xs, ys = transformer.transform(corners_lon, corners_lat)
    return (min(xs), min(ys), max(xs), max(ys))


def _bbox_for_source(bbox: Mapping[str, float], source: LolaSource) -> dict[str, float]:
    if source.lon_convention == "0_360":
        west = lon_to_0_360(bbox["west"])
        east = lon_to_0_360(bbox["east"])
    else:
        west, east = normalize_lon(bbox["west"]), normalize_lon(bbox["east"])
    return {
        "west": float(west),
        "south": float(bbox["south"]),
        "east": float(east),
        "north": float(bbox["north"]),
    }


def _choose_source_for_bbox(bbox: Mapping[str, float]) -> LolaSource:
    south = float(bbox["south"])
    north = float(bbox["north"])
    center_lat = (south + north) / 2.0
    if north >= 80.0:
        for source in DEFAULT_SOURCES:
            if source.coverage_kind == "polar_north":
                return source
    if south <= -80.0:
        for source in DEFAULT_SOURCES:
            if source.coverage_kind == "polar_south":
                return source
    if center_lat >= 75.0:
        for source in DEFAULT_SOURCES:
            if source.coverage_kind == "polar_north":
                return source
    if center_lat <= -75.0:
        for source in DEFAULT_SOURCES:
            if source.coverage_kind == "polar_south":
                return source
    for source in DEFAULT_SOURCES:
        if source.coverage_kind == "global":
            return source
    for source in DEFAULT_SOURCES:
        if source.coverage_kind == "fallback_global":
            return source
    if DEFAULT_SOURCES:
        return DEFAULT_SOURCES[0]
    raise LolaGlobalError("no_source", "no LOLA source configured")


def _open_source_dataset(source: LolaSource, *, timeout: float = DEFAULT_TIMEOUT_S):
    os.environ.setdefault("PROJ_IGNORE_CELESTIAL_BODY", "YES")
    env = rasterio.Env(
        GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
        CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif,.tiff,.vrt",
        GDAL_HTTP_MULTIRANGE="SERIAL",
        GDAL_HTTP_MERGE_CONSECUTIVE_RANGES="YES",
        GDAL_HTTP_TIMEOUT=str(int(timeout)),
        GDAL_HTTP_MAX_RETRY=str(MAX_RETRIES),
        GDAL_HTTP_RETRY_DELAY="1",
        PROJ_IGNORE_CELESTIAL_BODY="YES",
        VSI_CACHE="TRUE",
        VSI_CACHE_SIZE="67108864",
    )
    env.__enter__()
    path = source.raster_url
    if path.startswith(("http://", "https://")):
        path = "/vsicurl/" + path
    try:
        dataset = rasterio.open(path)
    except Exception:
        env.__exit__(None, None, None)
        raise
    return env, dataset


def _read_piece(
    source: LolaSource,
    piece_bbox: Mapping[str, float],
    dst_crs: CRS,
    analysis_resolution_m: float,
    max_pixels: int,
    *,
    timeout: float = DEFAULT_TIMEOUT_S,
) -> dict[str, Any]:
    src_bbox = _bbox_for_source(piece_bbox, source)
    # If a 0-360 source bbox wraps 360, split into two continuous source reads and
    # combine on the destination grid.
    if source.lon_convention == "0_360" and src_bbox["west"] > src_bbox["east"]:
        left = dict(src_bbox, east=360.0)
        right = dict(src_bbox, west=0.0)
        res_left = _read_piece_continuous(
            source, left, piece_bbox, dst_crs, analysis_resolution_m, max_pixels, timeout=timeout
        )
        res_right = _read_piece_continuous(
            source, right, piece_bbox, dst_crs, analysis_resolution_m, max_pixels, timeout=timeout
        )
        return _merge_same_grid(res_left, res_right)
    return _read_piece_continuous(
        source, src_bbox, piece_bbox, dst_crs, analysis_resolution_m, max_pixels, timeout=timeout
    )


def _merge_same_grid(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    arr = np.array(a["array"], copy=True)
    mask = np.isfinite(arr)
    other = b["array"]
    take = ~mask & np.isfinite(other)
    arr[take] = other[take]
    valid_mask = np.isfinite(arr)
    return {
        **a,
        "array": arr,
        "valid_mask": valid_mask,
        "pixel_count": int(arr.size),
        "valid_pixel_count": int(valid_mask.sum()),
        "nodata_pixel_count": int((~valid_mask).sum()),
    }


def _read_piece_continuous(
    source: LolaSource,
    src_bbox: Mapping[str, float],
    metric_piece_bbox: Mapping[str, float],
    dst_crs: CRS,
    analysis_resolution_m: float,
    max_pixels: int,
    *,
    timeout: float = DEFAULT_TIMEOUT_S,
) -> dict[str, Any]:
    minx, miny, maxx, maxy = _bbox_to_metric_bounds(metric_piece_bbox, dst_crs)
    width_m = maxx - minx
    height_m = maxy - miny
    nx = max(3, int(math.ceil(width_m / analysis_resolution_m)))
    ny = max(3, int(math.ceil(height_m / analysis_resolution_m)))
    if nx * ny > max_pixels:
        raise LolaGlobalError(
            "aoi_too_large",
            f"analysis grid {nx}x{ny} ({nx*ny} pixels) exceeds max_pixels={max_pixels}",
            details={"nx": nx, "ny": ny, "max_pixels": max_pixels},
        )
    dst_transform = from_origin(minx, maxy, width_m / nx, height_m / ny)
    destination = np.full((ny, nx), np.nan, dtype="float64")

    env, src = _open_source_dataset(source, timeout=timeout)
    try:
        src_crs = CRS.from_user_input(src.crs) if src.crs else CRS.from_proj4(source.crs_proj4 or "")
        # Compute source window bounds by transforming analysis grid corners to source CRS.
        to_src = Transformer.from_crs(dst_crs, src_crs, always_xy=True)
        corners_x = [minx, maxx, minx, maxx]
        corners_y = [miny, miny, maxy, maxy]
        src_xs, src_ys = to_src.transform(corners_x, corners_y)
        pad = 2 * max(abs(float(src.transform.a)), abs(float(src.transform.e)), 1e-9)
        window = (
            from_bounds(
                min(src_xs) - pad,
                min(src_ys) - pad,
                max(src_xs) + pad,
                max(src_ys) + pad,
                src.transform,
            )
            .round_offsets()
            .round_lengths()
            .intersection(Window(0, 0, src.width, src.height))
        )
        if window.width <= 0 or window.height <= 0:
            # no coverage in this piece/source window
            valid_mask = np.zeros_like(destination, dtype=bool)
            return {
                "array": destination,
                "valid_mask": valid_mask,
                "bounds_metric": (minx, miny, maxx, maxy),
                "nx": nx,
                "ny": ny,
                "pixel_count": int(destination.size),
                "valid_pixel_count": 0,
                "nodata_pixel_count": int(destination.size),
                "source_bbox": dict(src_bbox),
            }

        nodata = src.nodata
        if nodata is None and source.nodata is not None:
            nodata = source.nodata
        fill = np.nan if nodata is None or (isinstance(nodata, float) and math.isnan(nodata)) else nodata
        data = src.read(
            1,
            window=window,
            boundless=False,
            masked=True,
            out_dtype="float64",
            fill_value=fill,
        )
        source_array = np.asarray(data.filled(np.nan), dtype="float64")
        if nodata is not None and not (isinstance(nodata, float) and math.isnan(nodata)):
            source_array = np.where(np.isclose(source_array, float(nodata)), np.nan, source_array)
        src_transform = src.window_transform(window)
        reproject(
            source=source_array,
            destination=destination,
            src_transform=src_transform,
            src_crs=src_crs,
            src_nodata=np.nan,
            dst_transform=dst_transform,
            dst_crs=dst_crs,
            dst_nodata=np.nan,
            resampling=Resampling.bilinear,
        )
    finally:
        src.close()
        env.__exit__(None, None, None)

    valid_mask = np.isfinite(destination)
    return {
        "array": destination,
        "valid_mask": valid_mask,
        "bounds_metric": (minx, miny, maxx, maxy),
        "nx": nx,
        "ny": ny,
        "pixel_count": int(destination.size),
        "valid_pixel_count": int(valid_mask.sum()),
        "nodata_pixel_count": int((~valid_mask).sum()),
        "source_bbox": dict(src_bbox),
    }


def _features_from_single_array(array: np.ndarray, cell_size_m: float) -> dict[str, Any]:
    z = np.asarray(array, dtype="float64")
    total = int(z.size)
    valid = np.isfinite(z)
    valid_count = int(valid.sum())
    nodata_count = total - valid_count
    valid_fraction = float(valid_count / total) if total else 0.0
    if valid_count == 0:
        return {
            "features": None,
            "valid_fraction": 0.0,
            "pixel_count": total,
            "valid_pixel_count": 0,
            "invalid_pixel_count": 0,
            "nodata_pixel_count": nodata_count,
            "no_data": True,
        }
    try:
        features = terrain_metrics(z, cell_size_m)
        slopes = horn_slope_degrees(z, cell_size_m)
        slope_valid = np.isfinite(slopes)
        invalid_pixel_count = int((~slope_valid).sum())
    except Exception as exc:  # noqa: BLE001
        return {
            "features": None,
            "valid_fraction": valid_fraction,
            "pixel_count": total,
            "valid_pixel_count": valid_count,
            "invalid_pixel_count": nodata_count,
            "nodata_pixel_count": nodata_count,
            "no_data": True,
            "error": str(exc),
        }
    return {
        "features": {k: float(v) for k, v in features.items()},
        "valid_fraction": valid_fraction,
        "pixel_count": total,
        "valid_pixel_count": valid_count,
        "invalid_pixel_count": invalid_pixel_count,
        "nodata_pixel_count": nodata_count,
        "no_data": False,
    }


def analyze_lola_aoi(
    bbox: Mapping[str, float] | Sequence[float],
    *,
    source_id: str | None = None,
    analysis_resolution_m: float = ANALYSIS_RESOLUTION_DEFAULT_M,
    max_pixels: int = DEFAULT_MAX_PIXELS,
    max_side_m: float = DEFAULT_MAX_SIDE_M,
    timeout: float = DEFAULT_TIMEOUT_S,
) -> AoiResult:
    request = validate_aoi(
        bbox,
        analysis_resolution_m=analysis_resolution_m,
        max_pixels=max_pixels,
        max_side_m=max_side_m,
    )
    source = SOURCE_BY_ID.get(source_id) if source_id else _choose_source_for_bbox(request.normalized_bbox)
    if source is None:
        raise LolaGlobalError("unknown_source", f"unknown source_id {source_id!r}")

    warnings: list[str] = []
    if request.crossing_antimeridian:
        warnings.append(
            "bbox crosses the antimeridian; request split into pieces and computed over the full selected range"
        )

    piece_results: list[dict[str, Any]] = []
    all_arrays: list[tuple[np.ndarray, CRS, dict[str, Any]]] = []
    try:
        for piece in request.pieces:
            dst_crs = _analysis_crs_for_bbox(piece)
            part = _read_piece(
                source, piece, dst_crs, request.analysis_resolution_m, max_pixels, timeout=timeout
            )
            arr = part["array"]
            piece_calc = _features_from_single_array(arr, request.analysis_resolution_m)
            piece_entry = {
                "piece_bbox": piece,
                "analysis_crs": dst_crs.to_proj4(),
                "bounds_metric": [float(v) for v in part["bounds_metric"]],
            }
            for key in (
                "valid_fraction",
                "pixel_count",
                "valid_pixel_count",
                "invalid_pixel_count",
                "nodata_pixel_count",
                "no_data",
                "error",
            ):
                if key in piece_calc:
                    piece_entry[key] = piece_calc[key]
            piece_results.append(piece_entry)
            all_arrays.append((arr, dst_crs, part))
    except Exception as exc:  # noqa: BLE001
        code = getattr(exc, "code", "source_read_failed")
        message = getattr(exc, "message", str(exc))
        details = getattr(exc, "details", None)
        return AoiResult(
            status="error",
            body=BODY,
            requested_bbox=request.requested_bbox,
            normalized_bbox=request.normalized_bbox,
            analysis_bbox=None,
            source=source.to_payload(),
            features=None,
            analysis_resolution_m=request.analysis_resolution_m,
            analysis_crs=None,
            pixel_count=0,
            valid_pixel_count=0,
            invalid_pixel_count=0,
            nodata_pixel_count=0,
            valid_fraction=0.0,
            partial=False,
            invalid=True,
            no_data=True,
            warnings=warnings,
            errors=[{"code": code, "message": message, "details": details}],
            pieces=piece_results,
        )

    combined_arrays = [a for a, _, _ in all_arrays]
    total_pixels = sum(int(a.size) for a in combined_arrays)
    total_valid = sum(int(np.isfinite(a).sum()) for a in combined_arrays)
    total_nodata = total_pixels - total_valid
    valid_fraction = float(total_valid / total_pixels) if total_pixels else 0.0

    if total_valid == 0:
        return AoiResult(
            status="no_data",
            body=BODY,
            requested_bbox=request.requested_bbox,
            normalized_bbox=request.normalized_bbox,
            analysis_bbox=request.normalized_bbox,
            source=source.to_payload(),
            features=None,
            analysis_resolution_m=request.analysis_resolution_m,
            analysis_crs=None,
            pixel_count=total_pixels,
            valid_pixel_count=0,
            invalid_pixel_count=0,
            nodata_pixel_count=total_nodata,
            valid_fraction=0.0,
            partial=False,
            invalid=True,
            no_data=True,
            warnings=warnings,
            errors=[{"code": "no_data", "message": "No valid elevation pixels in AOI"}],
            pieces=piece_results,
        )

    slope_samples = []
    elev_samples = []
    residual_samples = []
    for arr, _, _ in all_arrays:
        z = np.asarray(arr, dtype="float64")
        valid = np.isfinite(z)
        if not valid.any():
            continue
        slopes = horn_slope_degrees(z, request.analysis_resolution_m)
        slope_valid = np.isfinite(slopes)
        if slope_valid.any():
            slope_samples.append(slopes[slope_valid].ravel())
        zvals = z[valid]
        elev_samples.append(zvals.ravel())
        rows, cols = np.indices(z.shape)
        rr = rows[valid].astype("float64")
        cc = cols[valid].astype("float64")
        design = np.column_stack([rr, cc, np.ones_like(rr)])
        coeff, *_ = np.linalg.lstsq(design, zvals, rcond=None)
        residuals = zvals - design @ coeff
        residual_samples.append(residuals.ravel())

    if not slope_samples:
        return AoiResult(
            status="no_data",
            body=BODY,
            requested_bbox=request.requested_bbox,
            normalized_bbox=request.normalized_bbox,
            analysis_bbox=request.normalized_bbox,
            source=source.to_payload(),
            features=None,
            analysis_resolution_m=request.analysis_resolution_m,
            analysis_crs=None,
            pixel_count=total_pixels,
            valid_pixel_count=total_valid,
            invalid_pixel_count=total_nodata,
            nodata_pixel_count=total_nodata,
            valid_fraction=valid_fraction,
            partial=False,
            invalid=True,
            no_data=True,
            warnings=warnings,
            errors=[{"code": "no_data", "message": "No valid slope cells computed"}],
            pieces=piece_results,
        )

    slope_values = np.concatenate(slope_samples)
    z_values = np.concatenate(elev_samples)
    residual_values = np.concatenate(residual_samples)
    features = {
        "slope_mean_deg": float(np.mean(slope_values)),
        "slope_p90_deg": float(np.percentile(slope_values, 90)),
        "terrain_relief_p95_p05_m": float(np.percentile(z_values, 95) - np.percentile(z_values, 5)),
        "roughness_std_m": float(np.std(residual_values)),
        "flat_terrain_fraction": float(np.count_nonzero(slope_values < 5.0) / slope_values.size),
    }
    partial = valid_fraction < 1.0
    invalid = valid_fraction < MIN_VALID_FRACTION
    status = "ok"
    if invalid:
        status = "insufficient_valid_data"
        warnings.append(
            f"valid_fraction {valid_fraction:.4f} below MIN_VALID_FRACTION {MIN_VALID_FRACTION}"
        )
    elif partial:
        status = "partial"

    return AoiResult(
        status=status,
        body=BODY,
        requested_bbox=request.requested_bbox,
        normalized_bbox=request.normalized_bbox,
        analysis_bbox=request.normalized_bbox,
        source=source.to_payload(),
        features=features,
        analysis_resolution_m=request.analysis_resolution_m,
        analysis_crs="mixed_per_piece" if len(all_arrays) > 1 else all_arrays[0][1].to_proj4(),
        pixel_count=total_pixels,
        valid_pixel_count=total_valid,
        invalid_pixel_count=total_nodata,
        nodata_pixel_count=total_nodata,
        valid_fraction=valid_fraction,
        partial=partial,
        invalid=invalid,
        no_data=False,
        warnings=warnings,
        errors=[],
        pieces=piece_results,
    )


def _cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compute Moon D01 features for an arbitrary bbox AOI from NASA LOLA DEMs"
    )
    parser.add_argument(
        "--bbox",
        nargs=4,
        type=float,
        metavar=("WEST", "SOUTH", "EAST", "NORTH"),
        required=True,
    )
    parser.add_argument("--source-id", default=None)
    parser.add_argument("--resolution-m", type=float, default=ANALYSIS_RESOLUTION_DEFAULT_M)
    parser.add_argument("--max-pixels", type=int, default=DEFAULT_MAX_PIXELS)
    parser.add_argument("--max-side-m", type=float, default=DEFAULT_MAX_SIDE_M)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S)
    parser.add_argument("--json", action="store_true", help="Print JSON result")
    args = parser.parse_args(argv)

    try:
        result = analyze_lola_aoi(
            args.bbox,
            source_id=args.source_id,
            analysis_resolution_m=args.resolution_m,
            max_pixels=args.max_pixels,
            max_side_m=args.max_side_m,
            timeout=args.timeout,
        )
        payload = result.to_payload()
    except LolaGlobalError as exc:
        payload = {
            "status": "error",
            "error": {"code": exc.code, "message": exc.message, "details": exc.details},
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 2

    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
