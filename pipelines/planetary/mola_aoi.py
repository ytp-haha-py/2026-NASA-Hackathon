"""Full-bbox D01 terrain analysis from Mars MOLA MEGDR 128 ppd tiles.

This module answers an AOI request directly from the requested longitude/latitude bbox.
It does **not** crop a fixed 5 km centre window: the bbox is projected into a local
Mars body-fixed azimuthal-equidistant metric CRS, snapped outward to whole analysis
cells, read from the bounded public PDS3/PDS4 MOLA MEGDR 128 ppd tile(s), and the five
D01 terrain operators are evaluated over every valid elevation cell in that footprint.

Scientific support statement
-----------------------------
MOLA MEGDR 128 ppd has a native angular step of 0.0078125 degrees and a nominal
0.4630576672 km/pixel map scale. A square 463.0576672 m analysis grid is used because
it is never finer than the native north-south ground spacing; at high latitude the
native east-west ground spacing is finer, so the square grid is conservative rather
than an attempt to add detail. These values are **not** the 90 m
``terrain-metrics-1.0.0`` D01 support and must not be presented as same-support D01
values or used for a cross-body similarity score. This is a bounded AOI read, never
global coverage.

The same five registry feature ids and the shared Horn/relief/roughness/flat operators
are used. Missing pixels stay missing: no value is invented, interpolated, or zero-filled
by this module beyond the source product's own documented median-bin interpolation.

Reading uses B1's ``analog_pipelines.mola_global.read_window`` adapter: the exact
PDS4/ENVI tile georeference, bounded GDAL ``/vsicurl`` range reads, and no global tile
download. GDAL's rounded PDS3 ``MAP_SCALE`` transform is never used. Remote reads are
opt-in.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
from analog_api.domain.features import FEATURE_BY_ID, MIN_VALID_FRACTION
from analog_pipelines.features import ExtractionError, horn_slope_degrees, terrain_metrics
from analog_pipelines.planetary_coarse import (
    BODY_SPHERE_RADIUS_M,
    MARS_MOLA_MEG128,
    assert_planetary_crs,
    body_geographic_crs,
)
from pyproj import CRS, Transformer
from rasterio.enums import Resampling
from rasterio.transform import from_origin
from rasterio.warp import reproject
from rasterio.windows import transform as window_transform

# The shared GEOGRAPHIC/body-fixed transform requires this override. All analysis
# operators run in the local metric Mars CRS, so the override changes no value.
os.environ.setdefault("PROJ_IGNORE_CELESTIAL_BODY", "YES")

RESULT_SCHEMA_VERSION = "mars-mola-aoi-result-1.0.0"
EVIDENCE_SCHEMA_VERSION = "mars-mola-aoi-evidence-1.0.0"
METHOD_VERSION = "mars-mola-aoi-horn-463m-1.0.0"
SOURCE_VERSION = (
    "PDS4 urn:nasa:pds:mgs_mola_topography_derived (1.0) / collection data_meg128 (2.0)"
)

MARS_RADIUS_M = BODY_SPHERE_RADIUS_M["Mars"]
MOLA_NATIVE_DEGREES = 0.0078125
MOLA_NATIVE_RESOLUTION_M = MARS_MOLA_MEG128.native_resolution_m  # 463.0576672
ANALYSIS_RESOLUTION_M = MOLA_NATIVE_RESOLUTION_M
MOLA_MAX_ABS_LATITUDE_DEG = 88.0

# A bounded AOI keeps the 463 m grid statistically meaningful: 12.5 km is ~27 native
# cells per side, while 2.5 km is ~6 native cells (a 4 x 4 slope interior).
AOI_MIN_SIDE_M = 2_500.0
AOI_MAX_SIDE_M = 12_500.0
AOI_MAX_AREA_KM2 = 160.0

D01_FEATURE_IDS: tuple[str, ...] = tuple(FEATURE_BY_ID)
_MEGDR_TILE_WIDTH_DEG = 90.0
_MEGDR_TILE_BASE = (
    "https://pds-geosciences.wustl.edu/mgs/urn-nasa-pds-mgs_mola_topography_derived/meg128"
)


class MolaAoiError(RuntimeError):
    """A MOLA AOI request could not be answered without inventing data."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class MolaTile:
    """One MOLA MEGDR 128 ppd tile covering part of the requested bbox."""

    tile_id: str
    product_id: str
    label_url: str
    west_lon: float
    east_lon: float
    south_lat: float
    north_lat: float


@dataclass(frozen=True, slots=True)
class MolaAoiRequest:
    """A validated full-bbox AOI and its exact metric analysis footprint."""

    label: str
    requested_bbox_lonlat: dict[str, float]
    center_lonlat: tuple[float, float]
    latitude_band: str
    width_m: float
    height_m: float
    area_km2: float
    crs: CRS
    crs_wkt: str
    analysis_bbox_metric: dict[str, float]
    analysis_bbox_lonlat: dict[str, float]
    grid_shape: tuple[int, int]
    resolution_m: float
    tiles: tuple[MolaTile, ...]

    @property
    def nx(self) -> int:
        return self.grid_shape[1]

    @property
    def ny(self) -> int:
        return self.grid_shape[0]


@dataclass(frozen=True, slots=True)
class MolaSourceWindow:
    """One bounded source-tile read that contributed to the analysis grid."""

    tile_id: str
    product_id: str
    label_url: str
    source_window_shape: tuple[int, int]
    source_window_pixels: int
    source_window_offsets: tuple[int, int]
    read_bytes_upper_bound: int
    precise_tile_upper_left_m: tuple[float, float] | None = None
    precise_window_upper_left_m: tuple[float, float] | None = None
    gdal_upper_left_m: tuple[float, float] | None = None
    precise_tile_transform: tuple[float, ...] | None = None
    window_transform: tuple[float, ...] | None = None
    gdal_transform: tuple[float, ...] | None = None
    georeference_override: bool = False
    georeference_delta: dict[str, float] = field(default_factory=dict)
    access_timestamp: str | None = None
    source_kind: str | None = None
    source_url: str | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "tile_id": self.tile_id,
            "product_id": self.product_id,
            "label_url": self.label_url,
            "source_url": self.source_url,
            "source_kind": self.source_kind,
            "source_window_shape": list(self.source_window_shape),
            "source_window_pixels": self.source_window_pixels,
            "source_window_offsets": list(self.source_window_offsets),
            "read_bytes_upper_bound": self.read_bytes_upper_bound,
            "georeference_override": self.georeference_override,
            "georeference_delta": dict(self.georeference_delta),
            "precise_tile_upper_left_m": (
                list(self.precise_tile_upper_left_m)
                if self.precise_tile_upper_left_m is not None
                else None
            ),
            "precise_window_upper_left_m": (
                list(self.precise_window_upper_left_m)
                if self.precise_window_upper_left_m is not None
                else None
            ),
            "gdal_upper_left_m": (
                list(self.gdal_upper_left_m) if self.gdal_upper_left_m is not None else None
            ),
            "precise_tile_transform": (
                list(self.precise_tile_transform)
                if self.precise_tile_transform is not None
                else None
            ),
            "window_transform": (
                list(self.window_transform) if self.window_transform is not None else None
            ),
            "gdal_transform": list(self.gdal_transform)
            if self.gdal_transform is not None
            else None,
            "access_timestamp": self.access_timestamp,
        }


@dataclass(frozen=True, slots=True)
class MolaWindow:
    """The reprojected analysis grid plus provenance for the source reads."""

    data: np.ndarray
    sources: tuple[MolaSourceWindow, ...]
    source_crs_wkt: str
    source_crs_name: str
    source_resolution_m: float
    warnings: tuple[str, ...] = ()


def _normalize_lon(lon: float) -> float:
    value = float(lon) % 360.0
    return 0.0 if math.isclose(value, 360.0, abs_tol=1e-12) else value


def _latitude_band(lat: float) -> str:
    absolute = abs(lat)
    if absolute < 30.0:
        return "low"
    if absolute < 60.0:
        return "mid"
    return "high"


def _latitude_bands(south: float, north: float) -> tuple[tuple[float, float, int, str], ...]:
    bands: list[tuple[float, float, int, str]] = []
    if south < min(north, 44.0) and north > 0.0:
        bands.append((0.0, 44.0, 44, "n"))
    if south < 0.0 and north > max(south, -44.0):
        bands.append((-44.0, 0.0, 0, "n"))
    if south < 88.0 and north > 44.0:
        bands.append((44.0, 88.0, 88, "n"))
    if south < -44.0:
        bands.append((-88.0, -44.0, 44, "s"))
    return tuple(bands)


def tiles_for_bbox(west: float, south: float, east: float, north: float) -> tuple[MolaTile, ...]:
    """Return the MEGDR 128 ppd tiles whose footprints intersect a lon/lat bbox.

    Longitudes are east-positive degrees and must already be normalized so that
    ``west < east`` inside a single 0..360 pass.
    """
    tiles: list[MolaTile] = []
    lon_start = int(math.floor(west / _MEGDR_TILE_WIDTH_DEG))
    lon_end = int(
        math.floor(max(west, east - 1e-10) / _MEGDR_TILE_WIDTH_DEG) if east > west else lon_start
    )
    for band_south, band_north, band_code, hemisphere in _latitude_bands(south, north):
        for lon_index in range(lon_start, lon_end + 1):
            tile_west = (lon_index * _MEGDR_TILE_WIDTH_DEG) % 360.0
            tile_east = tile_west + _MEGDR_TILE_WIDTH_DEG
            tile_id = f"megt{band_code:02d}{hemisphere}{int(tile_west):03d}hb"
            tiles.append(
                MolaTile(
                    tile_id=tile_id,
                    product_id=tile_id,
                    label_url=f"{_MEGDR_TILE_BASE}/{tile_id}.lbl",
                    west_lon=tile_west,
                    east_lon=tile_east,
                    south_lat=band_south,
                    north_lat=band_north,
                )
            )
    return tuple(tiles)


def _coerce_bbox(bbox: Mapping[str, Any]) -> dict[str, float]:
    try:
        west = float(bbox["west"])
        south = float(bbox["south"])
        east = float(bbox["east"])
        north = float(bbox["north"])
    except (KeyError, TypeError, ValueError) as exc:
        raise MolaAoiError(
            "invalid_bbox", "bbox must contain numeric west, south, east and north"
        ) from exc
    if not all(math.isfinite(value) for value in (west, south, east, north)):
        raise MolaAoiError("invalid_bbox", "bbox coordinates must be finite")
    return {"west": west, "south": south, "east": east, "north": north}


def _spherical_bbox_area_km2(south: float, north: float, dlon: float) -> float:
    return (
        MARS_RADIUS_M
        * MARS_RADIUS_M
        * dlon
        * (math.sin(math.radians(north)) - math.sin(math.radians(south)))
        / 1e6
    )


def validate_mola_aoi(bbox: Mapping[str, Any], *, label: str = "custom-mars-aoi") -> MolaAoiRequest:
    """Validate a Mars bbox and build the exact 463 m metric analysis footprint."""
    raw = _coerce_bbox(bbox)
    if raw["south"] >= raw["north"]:
        raise MolaAoiError("invalid_bbox", "south must be strictly less than north")
    if raw["south"] < -MOLA_MAX_ABS_LATITUDE_DEG or raw["north"] > MOLA_MAX_ABS_LATITUDE_DEG:
        raise MolaAoiError(
            "unsupported_latitude",
            "MOLA MEGDR 128 ppd tiles cover -88..88 degrees; the requested latitude "
            f"range {raw['south']}..{raw['north']} is outside that bounded coverage",
        )

    west = _normalize_lon(raw["west"])
    east = _normalize_lon(raw["east"])
    if raw["west"] >= 0.0 > raw["east"]:
        raise MolaAoiError(
            "antimeridian_not_supported",
            "a bbox with east-positive west >= 0 and east < 0 straddles the 0/360 "
            "antimeridian; use explicit 0..360 longitudes for a non-crossing AOI",
        )
    if west >= east:
        raise MolaAoiError(
            "antimeridian_not_supported",
            "normalized west must be strictly less than normalized east; an AOI that "
            "crosses the 0/360 antimeridian is rejected instead of being silently split",
        )

    center_lon = (west + east) / 2.0
    center_lat = (raw["south"] + raw["north"]) / 2.0
    dlon = math.radians(east - west)
    dlat = math.radians(raw["north"] - raw["south"])
    width_m = MARS_RADIUS_M * math.cos(math.radians(center_lat)) * dlon
    height_m = MARS_RADIUS_M * dlat
    area_km2 = _spherical_bbox_area_km2(raw["south"], raw["north"], dlon)

    if width_m > AOI_MAX_SIDE_M or height_m > AOI_MAX_SIDE_M:
        raise MolaAoiError(
            "aoi_too_large",
            f"AOI sides are {width_m / 1000.0:.2f} x {height_m / 1000.0:.2f} km; the "
            f"bounded limit is {AOI_MAX_SIDE_M / 1000.0:.0f} km per side at 463 m support",
        )
    if area_km2 > AOI_MAX_AREA_KM2:
        raise MolaAoiError(
            "aoi_too_large",
            f"AOI area {area_km2:.2f} km2 exceeds the {AOI_MAX_AREA_KM2:.0f} km2 limit",
        )
    if width_m < AOI_MIN_SIDE_M or height_m < AOI_MIN_SIDE_M:
        raise MolaAoiError(
            "aoi_too_small",
            f"AOI sides are {width_m:.0f} x {height_m:.0f} m; the minimum is "
            f"{AOI_MIN_SIDE_M:.0f} m so a complete Horn 3x3 interior exists at 463 m",
        )

    crs = CRS.from_proj4(
        f"+proj=aeqd +lat_0={center_lat:.12f} +lon_0={center_lon:.12f} "
        f"+R={MARS_RADIUS_M:.1f} +units=m +no_defs"
    )
    geographic = body_geographic_crs("Mars")
    to_metric = Transformer.from_crs(geographic, crs, always_xy=True)
    sample_lons = [west + (east - west) * index / 4.0 for index in range(5)]
    sample_lats = [raw["south"] + (raw["north"] - raw["south"]) * index / 4.0 for index in range(5)]
    corner_xs, corner_ys = to_metric.transform(
        [lon for lon in sample_lons for _ in sample_lats],
        [lat for _ in sample_lons for lat in sample_lats],
    )
    min_x, max_x = min(corner_xs), max(corner_xs)
    min_y, max_y = min(corner_ys), max(corner_ys)

    resolution = ANALYSIS_RESOLUTION_M
    nx = max(3, math.ceil((max_x - min_x) / resolution - 1e-12))
    ny = max(3, math.ceil((max_y - min_y) / resolution - 1e-12))
    center_x = (min_x + max_x) / 2.0
    center_y = (min_y + max_y) / 2.0
    analysis_bbox_metric = {
        "west": center_x - nx * resolution / 2.0,
        "south": center_y - ny * resolution / 2.0,
        "east": center_x + nx * resolution / 2.0,
        "north": center_y + ny * resolution / 2.0,
    }

    to_geographic = Transformer.from_crs(crs, geographic, always_xy=True)
    geo_xs, geo_ys = to_geographic.transform(
        [
            analysis_bbox_metric["west"],
            analysis_bbox_metric["east"],
            analysis_bbox_metric["west"],
            analysis_bbox_metric["east"],
        ],
        [
            analysis_bbox_metric["south"],
            analysis_bbox_metric["south"],
            analysis_bbox_metric["north"],
            analysis_bbox_metric["north"],
        ],
    )
    analysis_bbox_lonlat = {
        "west": _normalize_lon(min(geo_xs)),
        "south": min(geo_ys),
        "east": _normalize_lon(max(geo_xs)),
        "north": max(geo_ys),
    }

    return MolaAoiRequest(
        label=label,
        requested_bbox_lonlat={
            "west": west,
            "south": raw["south"],
            "east": east,
            "north": raw["north"],
        },
        center_lonlat=(center_lon, center_lat),
        latitude_band=_latitude_band(center_lat),
        width_m=width_m,
        height_m=height_m,
        area_km2=area_km2,
        crs=crs,
        crs_wkt=crs.to_wkt(),
        analysis_bbox_metric=analysis_bbox_metric,
        analysis_bbox_lonlat=analysis_bbox_lonlat,
        grid_shape=(ny, nx),
        resolution_m=resolution,
        tiles=tiles_for_bbox(
            analysis_bbox_lonlat["west"],
            analysis_bbox_lonlat["south"],
            analysis_bbox_lonlat["east"],
            analysis_bbox_lonlat["north"],
        ),
    )


def _read_mola_window(request: MolaAoiRequest, *, allow_remote: bool) -> MolaWindow:
    """Read the full analysis footprint from every intersecting MEGDR tile.

    The requested lon/lat AOI is intersected with each verified MEGDR tile, and each
    intersection is read through B1's ``mola_global.read_window`` adapter. That adapter
    returns the exact PDS4/ENVI georeference (``tile_transform``), not GDAL's rounded
    PDS3 ``MAP_SCALE`` transform. The native window array is then reprojected to this
    module's local metric analysis grid. No shifted GDAL fallback is permitted.
    """
    if not allow_remote:
        raise MolaAoiError(
            "remote_read_disabled",
            "MOLA MEGDR reads are opt-in; pass allow_remote=True or --allow-remote to "
            "perform bounded /vsicurl range reads",
        )

    try:
        from mola_global import MolaSourceError, tile_for_product_id
        from mola_global import read_window as read_precise_window
        from mola_global import tile_transform as precise_tile_transform
    except Exception as exc:  # noqa: BLE001 - fail closed, never fall back to GDAL PDS
        raise MolaAoiError(
            "source_adapter_unavailable",
            f"cannot import B1's precise MOLA reader: {type(exc).__name__}: {exc}",
        ) from exc

    metric = request.analysis_bbox_metric
    dst_transform = from_origin(
        metric["west"], metric["north"], request.resolution_m, request.resolution_m
    )
    lonlat = request.analysis_bbox_lonlat
    merged: np.ndarray | None = None
    sources: list[MolaSourceWindow] = []
    warnings: list[str] = []
    source_crs_wkt = ""
    source_crs_name = ""
    source_resolution_m = MARS_MOLA_MEG128.native_resolution_m

    for tile in request.tiles:
        try:
            precise_tile = tile_for_product_id(tile.product_id)
        except MolaSourceError as exc:
            raise MolaAoiError("source_tile_unverified", f"{tile.product_id}: {exc}") from exc

        sub_west = max(lonlat["west"], precise_tile.west_deg)
        sub_south = max(lonlat["south"], precise_tile.south_deg)
        sub_east = min(lonlat["east"], precise_tile.east_deg)
        sub_north = min(lonlat["north"], precise_tile.north_deg)
        if sub_east <= sub_west or sub_north <= sub_south:
            warnings.append(
                f"{tile.tile_id} no longer intersects the snapped analysis bbox; skipped"
            )
            continue

        try:
            raw = read_precise_window(
                sub_west,
                sub_south,
                sub_east,
                sub_north,
                ppd=128,
                role="topography",
                allow_remote=True,
            )
        except MolaSourceError as exc:
            if exc.code == "no_valid_elevation":
                warnings.append(
                    f"{tile.tile_id}: B1 reader found no finite elevation in the tile "
                    "intersection; tile skipped, no value filled"
                )
                continue
            raise MolaAoiError("source_read_failed", f"{tile.tile_id}: {exc.code}: {exc}") from exc
        except Exception as exc:  # noqa: BLE001 - GDAL/network failures must be explicit
            raise MolaAoiError(
                "source_read_failed", f"{tile.tile_id}: {type(exc).__name__}: {exc}"
            ) from exc

        if not raw.georeference_override:
            raise MolaAoiError(
                "georeference_override_missing",
                f"{tile.tile_id}: B1 reader did not apply the precise PDS4/ENVI "
                "georeference; refusing to compute on a GDAL PDS transform",
            )
        try:
            src_crs = CRS.from_user_input(raw.crs)
            assert_planetary_crs(src_crs, body="Mars")
        except Exception as exc:  # noqa: BLE001 - wrong-body CRS must fail closed
            raise MolaAoiError(
                "source_crs_invalid", f"{tile.tile_id}: {type(exc).__name__}: {exc}"
            ) from exc

        source_data = np.asarray(raw.data, dtype="float64")
        source_data = np.where(np.isfinite(source_data), source_data, np.nan)
        full_precise_transform = precise_tile_transform(raw.tile)
        # B1 currently returns the full-tile precise affine on MolaWindow. If that
        # changes to a window-local affine, use it directly instead of double-offsetting.
        if math.isclose(
            float(raw.transform.c), float(full_precise_transform.c), abs_tol=1e-6
        ) and math.isclose(float(raw.transform.f), float(full_precise_transform.f), abs_tol=1e-6):
            src_window_transform = window_transform(raw.window, raw.transform)
        else:
            src_window_transform = raw.transform
        destination = np.full(request.grid_shape, np.nan, dtype="float64")
        reproject(
            source=source_data,
            destination=destination,
            src_transform=src_window_transform,
            src_crs=src_crs,
            src_nodata=np.nan,
            dst_transform=dst_transform,
            dst_crs=request.crs,
            dst_nodata=np.nan,
            resampling=Resampling.bilinear,
        )

        if source_crs_wkt == "":
            source_crs_wkt = src_crs.to_wkt()
            source_crs_name = src_crs.name
            source_resolution_m = float(raw.tile.pixel_resolution_m)
        if merged is None:
            merged = destination
        else:
            both_valid = np.isfinite(merged) & np.isfinite(destination)
            if both_valid.any():
                largest_difference = float(
                    np.max(np.abs(merged[both_valid] - destination[both_valid]))
                )
                if largest_difference > 1.0:
                    warnings.append(
                        f"{tile.tile_id} disagrees with an earlier tile by up to "
                        f"{largest_difference:.2f} m on overlapping valid pixels; the "
                        "first read is retained"
                    )
            merged = np.where(np.isfinite(merged), merged, destination)

        gdal_transform = tuple(float(value) for value in raw.gdal_transform)
        precise_transform = tuple(float(value) for value in tuple(full_precise_transform)[:6])
        sources.append(
            MolaSourceWindow(
                tile_id=tile.tile_id,
                product_id=raw.tile.product_id,
                label_url=tile.label_url,
                source_window_shape=(int(raw.shape[0]), int(raw.shape[1])),
                source_window_pixels=int(raw.window.width * raw.window.height),
                source_window_offsets=(
                    int(raw.window.col_off),
                    int(raw.window.row_off),
                ),
                read_bytes_upper_bound=int(raw.estimated_read_bytes),
                precise_tile_upper_left_m=(
                    float(full_precise_transform.c),
                    float(full_precise_transform.f),
                ),
                precise_window_upper_left_m=(
                    float(src_window_transform.c),
                    float(src_window_transform.f),
                ),
                gdal_upper_left_m=(gdal_transform[2], gdal_transform[5]),
                precise_tile_transform=precise_transform,
                window_transform=tuple(float(value) for value in src_window_transform[:6]),
                gdal_transform=gdal_transform,
                georeference_override=bool(raw.georeference_override),
                georeference_delta={
                    key: float(value) for key, value in raw.georeference_delta.items()
                },
                access_timestamp=raw.access_timestamp,
                source_kind=raw.source_kind,
                source_url=raw.source,
            )
        )

    if merged is None:
        merged = np.full(request.grid_shape, np.nan, dtype="float64")
        warnings.append(
            "No selected MOLA MEGDR tile returned finite elevation; the result is an "
            "explicit no_data grid, never a zero-filled surface."
        )
    if len(sources) > 1:
        warnings.append(
            f"AOI straddles MEGDR tile boundaries; merged {len(sources)} precise source "
            "windows. Only finite pixels from the listed tiles are used."
        )
    return MolaWindow(
        data=merged,
        sources=tuple(sources),
        source_crs_wkt=source_crs_wkt,
        source_crs_name=source_crs_name,
        source_resolution_m=source_resolution_m,
        warnings=tuple(warnings),
    )


def _canonical(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _checksum(payload: Mapping[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def _tile_source_id(tile_id: str) -> str:
    """Map a MEGDR product id (``megt44n000hb``) to a tile-specific source id."""
    core = tile_id.removeprefix("meg").removesuffix("hb")
    return f"mars-mola-megdr-meg128-{core}"


def _source_payload(request: MolaAoiRequest, sources: Sequence[MolaSourceWindow]) -> dict[str, Any]:
    source = MARS_MOLA_MEG128
    source_ids = [_tile_source_id(tile.tile_id) for tile in request.tiles]
    return {
        "source_id": (source_ids[0] if len(source_ids) == 1 else "mars-mola-megdr-meg128-mosaic"),
        "source_ids": source_ids,
        "dataset_id": source.dataset_id,
        "provider": source.provider,
        "source_version": SOURCE_VERSION,
        "product_ids": [tile.product_id for tile in request.tiles],
        "read_product_ids": [item.product_id for item in sources],
        "label_urls": [tile.label_url for tile in request.tiles],
        "license": source.license,
        "license_url": source.license_url,
        "citation": source.citation,
        "doi": source.doi,
        "frame": source.frame,
        "coordinate_system": source.coordinate_system,
        "vertical_reference": source.vertical_reference,
        "native_resolution_m": source.native_resolution_m,
        "native_ppd": source.native_ppd,
        "quality_flags": list(source.quality_flags),
    }


def _georeference_payload(window: MolaWindow) -> dict[str, Any]:
    entries = []
    for source in window.sources:
        entries.append(
            {
                "tile_id": source.tile_id,
                "product_id": source.product_id,
                "georeference_override": source.georeference_override,
                "precise_tile_upper_left_m": (
                    list(source.precise_tile_upper_left_m)
                    if source.precise_tile_upper_left_m is not None
                    else None
                ),
                "precise_window_upper_left_m": (
                    list(source.precise_window_upper_left_m)
                    if source.precise_window_upper_left_m is not None
                    else None
                ),
                "gdal_upper_left_m": (
                    list(source.gdal_upper_left_m) if source.gdal_upper_left_m is not None else None
                ),
                "georeference_delta": dict(source.georeference_delta),
                "window_transform": (
                    list(source.window_transform) if source.window_transform is not None else None
                ),
                "gdal_transform": (
                    list(source.gdal_transform) if source.gdal_transform is not None else None
                ),
                "access_timestamp": source.access_timestamp,
            }
        )
    return {
        "override": bool(window.sources)
        and all(source.georeference_override for source in window.sources),
        "method": (
            "PDS4 cart:Geo_Transformation / exact 2*pi*R/samples tile origin; "
            "GDAL's rounded PDS3 MAP_SCALE transform is not used"
        ),
        "delta_convention": (
            "gdal_minus_precise_m; positive means GDAL's origin is east/north of the "
            "precise PDS4/ENVI origin"
        ),
        "sources": entries,
    }


def _crs_payload(request: MolaAoiRequest, window: MolaWindow) -> dict[str, Any]:
    return {
        "analysis": {
            "name": request.crs.name,
            "wkt": request.crs_wkt,
            "proj": request.crs.to_string(),
            "is_projected": bool(request.crs.is_projected),
            "unit": (request.crs.axis_info[0].unit_name if request.crs.axis_info else "unknown"),
            "frame": "Mars body-fixed, planetocentric, local azimuthal equidistant",
        },
        "source": {
            "name": window.source_crs_name,
            "wkt": window.source_crs_wkt,
            "native_resolution_m": window.source_resolution_m,
            "verified_product_resolution_m": MARS_MOLA_MEG128.native_resolution_m,
            "georeference_override": bool(window.sources)
            and all(source.georeference_override for source in window.sources),
        },
    }


def _resolution_payload(request: MolaAoiRequest) -> dict[str, float | str]:
    center_lat = request.center_lonlat[1]
    east_west = (
        MARS_RADIUS_M * math.cos(math.radians(center_lat)) * math.radians(MOLA_NATIVE_DEGREES)
    )
    north_south = MARS_RADIUS_M * math.radians(MOLA_NATIVE_DEGREES)
    return {
        "analysis_resolution_m": request.resolution_m,
        "source_nominal_resolution_m": MOLA_NATIVE_RESOLUTION_M,
        "source_ground_east_west_m": east_west,
        "source_ground_north_south_m": north_south,
        "support_note": (
            "The 463.0576672 m square analysis grid is never finer than the native "
            "north-south ground spacing. At this latitude the native east-west spacing "
            "is finer, so the square grid is conservative and does not add detail. "
            "These are not 90 m terrain-metrics-1.0.0 D01 values."
        ),
    }


def _feature_payload(
    features: Mapping[str, float], *, valid_fraction: float, resolution_m: float
) -> dict[str, dict[str, Any]]:
    payload: dict[str, dict[str, Any]] = {}
    for feature_id in D01_FEATURE_IDS:
        definition = FEATURE_BY_ID[feature_id]
        payload[feature_id] = {
            "value": round(float(features[feature_id]), 6),
            "unit": definition.unit,
            "method_version": METHOD_VERSION,
            "registry_feature_method_version": definition.method_version,
            "extraction_scale_m": round(float(resolution_m), 7),
            "valid_fraction": round(float(valid_fraction), 6),
            "quality": "observed",
            "comparability": "not_comparable_to_90m_d01",
        }
    return payload


def _elevation_stats(data: np.ndarray) -> dict[str, float] | None:
    values = data[np.isfinite(data)]
    if values.size == 0:
        return None
    return {
        "min_m": round(float(np.min(values)), 6),
        "max_m": round(float(np.max(values)), 6),
        "mean_m": round(float(np.mean(values)), 6),
        "relief_p95_p05_m": round(float(np.percentile(values, 95) - np.percentile(values, 5)), 6),
    }


def analyze_mola_aoi(
    bbox: Mapping[str, Any],
    *,
    label: str = "custom-mars-aoi",
    allow_remote: bool = False,
    window_reader: Any | None = None,
) -> dict[str, Any]:
    """Analyze a full Mars bbox and return a JSON-safe result payload.

    ``window_reader`` is an optional test/extension seam with the signature
    ``reader(request, allow_remote=...) -> MolaWindow``. The production reader performs
    bounded rasterio/GDAL reads; no synthetic values are created here.
    """
    request = validate_mola_aoi(bbox, label=label)
    reader = _read_mola_window if window_reader is None else window_reader
    try:
        window = reader(request, allow_remote=allow_remote)
    except MolaAoiError:
        raise
    except Exception as exc:  # noqa: BLE001 - source failures must stay explicit
        raise MolaAoiError("source_read_failed", f"{type(exc).__name__}: {exc}") from exc

    data = np.asarray(window.data, dtype="float64")
    if data.shape != request.grid_shape:
        raise MolaAoiError(
            "reader_shape_mismatch",
            f"reader returned {data.shape}, expected {request.grid_shape}",
        )
    data = np.where(np.isfinite(data), data, np.nan)

    total_pixels = int(data.size)
    valid_values = np.isfinite(data)
    valid_pixels = int(valid_values.sum())
    invalid_pixels = int(total_pixels - valid_pixels)
    valid_fraction = float(valid_pixels / total_pixels)

    slope_grid_pixels = max(0, (request.nx - 2) * (request.ny - 2))
    slopes = horn_slope_degrees(data, request.resolution_m)
    valid_slope_pixels = int(np.isfinite(slopes).sum())
    invalid_slope_pixels = int(slope_grid_pixels - valid_slope_pixels)
    valid_slope_fraction = (
        float(valid_slope_pixels / slope_grid_pixels) if slope_grid_pixels > 0 else 0.0
    )

    warnings: list[str] = [
        "Bounded full-bbox read over MOLA MEGDR 128 ppd; this is not global Mars "
        "coverage and not a Jezero-only case.",
        "MEGDR is a global median-binned grid with interpolated values where no MOLA "
        "shot falls in a bin; it is not raw high-resolution altimetry.",
        "The five D01 registry operators are evaluated at 463.0576672 m support. These "
        "values are not interchangeable with the 90 m terrain-metrics-1.0.0 D01 "
        "references and are not used for cross-body scoring.",
        "The 463 m analysis grid is snapped outward to whole cells around the requested "
        "bbox so the requested footprint is never shrunk.",
    ]
    warnings.extend(window.warnings)

    features: dict[str, dict[str, Any]] | None = None
    elevation_stats: dict[str, float] | None = None
    partial = False
    if valid_fraction < MIN_VALID_FRACTION or valid_slope_pixels == 0:
        status = "no_data"
        warnings.append(
            f"Only {valid_pixels}/{total_pixels} analysis cells have valid elevation "
            f"(valid fraction {valid_fraction:.3f}, required {MIN_VALID_FRACTION:.2f}); "
            "D01 features are explicitly null instead of being filled."
        )
    else:
        partial = invalid_pixels > 0
        status = "partial" if partial else "complete"
        if partial:
            warnings.append(
                f"{invalid_pixels}/{total_pixels} analysis cells are nodata; the five "
                "features use only valid cells and are marked partial."
            )
        try:
            values = terrain_metrics(data, request.resolution_m)
        except ExtractionError as exc:
            status = "no_data"
            features = None
            warnings.append(f"Shared D01 operators rejected the window ({exc.code}): {exc.message}")
        else:
            features = _feature_payload(
                values,
                valid_fraction=valid_fraction,
                resolution_m=request.resolution_m,
            )
            elevation_stats = _elevation_stats(data)

    if abs(request.center_lonlat[1]) >= 60.0:
        warnings.append(
            "At this high latitude the native east-west ground spacing is finer than "
            "the 463 m analysis cell; the square grid is deliberately conservative."
        )

    qa_flags = list(MARS_MOLA_MEG128.quality_flags)
    qa_flags.extend(
        (
            "full_requested_bbox",
            "analysis_463m_square_grid",
            "body_fixed_local_aeqd",
            "not_90m_d01_comparable",
            "no_zero_fill",
            "bounded_vsicurl_window",
        )
    )
    if partial:
        qa_flags.append("partial_valid_fraction")
    if status == "no_data":
        qa_flags.append("no_data")

    payload: dict[str, Any] = {
        "schema_version": RESULT_SCHEMA_VERSION,
        "body": "Mars",
        "data_kind": "observed",
        "synthetic": False,
        "scope": "bounded_full_bbox_aoi",
        "coordinate_frame": "mars_planetocentric",
        "dimension": "D01",
        "method_version": METHOD_VERSION,
        "source_version": SOURCE_VERSION,
        "status": status,
        "label": request.label,
        "requested_bbox_lonlat": dict(request.requested_bbox_lonlat),
        "center_lonlat": list(request.center_lonlat),
        "latitude_band": request.latitude_band,
        "width_m": round(request.width_m, 6),
        "height_m": round(request.height_m, 6),
        "area_km2": round(request.area_km2, 6),
        "analysis_bbox_metric": dict(request.analysis_bbox_metric),
        "analysis_bbox_lonlat": dict(request.analysis_bbox_lonlat),
        "grid_shape": list(request.grid_shape),
        "resolution_m": round(request.resolution_m, 7),
        "resolution": _resolution_payload(request),
        "pixel_count": total_pixels,
        "valid_pixels": valid_pixels,
        "invalid_pixels": invalid_pixels,
        "valid_fraction": round(valid_fraction, 6),
        "partial": partial,
        "slope_pixel_count": slope_grid_pixels,
        "slope_outer_rim_pixels": total_pixels - slope_grid_pixels,
        "valid_slope_pixels": valid_slope_pixels,
        "invalid_slope_pixels": invalid_slope_pixels,
        "valid_slope_fraction": round(valid_slope_fraction, 6),
        "features": features,
        "elevation_stats": elevation_stats,
        "support_quality": {
            "grid": "MOLA MEGDR median binned, 128 pixels/degree",
            "interpolated_bins_present": True,
            "raw_altimetry": False,
            "per_pixel_uncertainty": None,
            "d01_90m_comparable": False,
        },
        "source": _source_payload(request, window.sources),
        "crs": _crs_payload(request, window),
        "georeference_override": bool(window.sources)
        and all(source.georeference_override for source in window.sources),
        "georeference": _georeference_payload(window),
        "source_windows": [source.to_payload() for source in window.sources],
        "read_bytes_upper_bound": int(
            sum(source.read_bytes_upper_bound for source in window.sources)
        ),
        "warnings": warnings,
        "qa_flags": qa_flags,
    }
    payload["checksum"] = _checksum(payload)
    return payload


def _without_access_timestamps(items: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        {key: value for key, value in item.items() if key != "access_timestamp"} for item in items
    ]


def compare_with_evidence(result: Mapping[str, Any], evidence: Mapping[str, Any]) -> bool:
    """Return True when two results agree on every scientific value.

    Used by the opt-in online test. Provenance timestamps, read byte bounds and
    checksums are ignored because they legitimately differ between runs.
    """
    keys = (
        "status",
        "valid_pixels",
        "invalid_pixels",
        "valid_fraction",
        "valid_slope_pixels",
        "analysis_bbox_metric",
        "features",
        "elevation_stats",
        "georeference_override",
    )
    if not all(result.get(key) == evidence.get(key) for key in keys):
        return False
    result_georef = dict(result.get("georeference") or {})
    evidence_georef = dict(evidence.get("georeference") or {})
    result_georef["sources"] = _without_access_timestamps(result_georef.get("sources", []))
    evidence_georef["sources"] = _without_access_timestamps(evidence_georef.get("sources", []))
    if result_georef != evidence_georef:
        return False
    return _without_access_timestamps(
        result.get("source_windows", [])
    ) == _without_access_timestamps(evidence.get("source_windows", []))


@dataclass(frozen=True, slots=True)
class MolaPreset:
    """A reproducible real-Mars AOI used for the committed evidence document."""

    preset_id: str
    label: str
    latitude_band: str
    bbox: dict[str, float]
    note: str

    def to_payload(self) -> dict[str, Any]:
        return {
            "preset_id": self.preset_id,
            "label": self.label,
            "latitude_band": self.latitude_band,
            "bbox": dict(self.bbox),
            "note": self.note,
        }


PRESETS: tuple[MolaPreset, ...] = (
    MolaPreset(
        preset_id="jezero-west-low-lat",
        label="Jezero west low-latitude plain",
        latitude_band="low",
        bbox={"west": 77.35, "south": 18.35, "east": 77.55, "north": 18.53},
        note="Low-latitude western Jezero terrain; 18.44 N, 77.45 E.",
    ),
    MolaPreset(
        preset_id="hellas-mid-lat",
        label="Hellas Planitia mid-latitude floor",
        latitude_band="mid",
        bbox={"west": 70.35, "south": -42.50, "east": 70.55, "north": -42.30},
        note="Mid-latitude southern Hellas basin floor; 42.4 S, 70.5 E.",
    ),
    MolaPreset(
        preset_id="olympia-undae-high-lat",
        label="Olympia Undae high-latitude dune field",
        latitude_band="high",
        bbox={"west": 239.55, "south": 79.92, "east": 240.45, "north": 80.08},
        note="High-latitude northern circumpolar dune terrain; 80.0 N, 240.0 E.",
    ),
)


def _preset_by_id(preset_id: str) -> MolaPreset:
    for preset in PRESETS:
        if preset.preset_id == preset_id:
            return preset
    raise MolaAoiError("unknown_preset", f"unknown MOLA AOI preset {preset_id!r}")


def build_evidence(
    results: Sequence[Mapping[str, Any]],
    *,
    command: str,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Wrap one or more real AOI results with explicit scope and provenance."""
    return {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "body": "Mars",
        "data_kind": "observed",
        "synthetic": False,
        "dimension": "D01",
        "method_version": METHOD_VERSION,
        "source_version": SOURCE_VERSION,
        "generated_at": generated_at or datetime.now(UTC).replace(microsecond=0).isoformat(),
        "command": command,
        "georeference": {
            "override": all(
                result.get("georeference_override")
                for result in results
                if result.get("status") != "error"
            ),
            "method": (
                "PDS4 cart:Geo_Transformation / exact 2*pi*R/samples tile origin; "
                "GDAL's rounded PDS3 MAP_SCALE transform is not used"
            ),
            "delta_convention": (
                "gdal_minus_precise_m; positive means GDAL's origin is east/north of "
                "the precise PDS4/ENVI origin"
            ),
            "per_aoi": [
                {
                    "label": result.get("label"),
                    "status": result.get("status"),
                    "sources": (result.get("georeference") or {}).get("sources", []),
                }
                for result in results
            ],
        },
        "limits": {
            "min_side_m": AOI_MIN_SIDE_M,
            "max_side_m": AOI_MAX_SIDE_M,
            "max_area_km2": AOI_MAX_AREA_KM2,
            "max_abs_latitude_deg": MOLA_MAX_ABS_LATITUDE_DEG,
            "analysis_resolution_m": ANALYSIS_RESOLUTION_M,
        },
        "scope": (
            "Three dispersed bounded Mars AOIs (low, mid and high latitude) computed from "
            "the real public MOLA MEGDR 128 ppd tiles. This is not global coverage, not a "
            "Jezero-only claim, and not a 90 m same-support D01 reference."
        ),
        "d01_feature_ids": list(D01_FEATURE_IDS),
        "presets": [preset.to_payload() for preset in PRESETS],
        "aois": list(results),
        "limitations": [
            "MEGDR native support is 0.4630576672 km/pixel; a 90 m D01 comparison is invalid.",
            "MEGDR is a median-binned grid with interpolated bins where no MOLA shot exists.",
            "No MOLA raw altimetry, no per-pixel uncertainty layer and no 90 m texture is claimed.",
            "Only the requested bounding boxes are read; no global tile was downloaded.",
            "The AOIs are terrain examples, not landing-site or resource recommendations.",
        ],
    }


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument(
        "--bbox",
        nargs=4,
        type=float,
        metavar=("WEST", "SOUTH", "EAST", "NORTH"),
        help="east-positive Mars longitude/latitude bbox in degrees",
    )
    choice.add_argument(
        "--preset",
        choices=[preset.preset_id for preset in PRESETS],
        help="run one committed reproducible AOI preset",
    )
    choice.add_argument(
        "--all-presets",
        action="store_true",
        help="run all three dispersed presets and write an evidence document",
    )
    parser.add_argument("--label", default="custom-mars-aoi")
    parser.add_argument(
        "--allow-remote",
        action="store_true",
        help="perform bounded /vsicurl MOLA reads (required for real data)",
    )
    parser.add_argument("--output", type=Path, help="write JSON here instead of stdout")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.all_presets:
        jobs = [(preset.label, preset.bbox) for preset in PRESETS]
    elif args.preset:
        preset = _preset_by_id(args.preset)
        jobs = [(preset.label, preset.bbox)]
    else:
        jobs = [(args.label, dict(zip(("west", "south", "east", "north"), args.bbox, strict=True)))]

    results: list[dict[str, Any]] = []
    errors = 0
    for label, bbox in jobs:
        try:
            results.append(analyze_mola_aoi(bbox, label=label, allow_remote=args.allow_remote))
        except MolaAoiError as exc:
            errors += 1
            results.append(
                {
                    "schema_version": RESULT_SCHEMA_VERSION,
                    "label": label,
                    "status": "error",
                    "error": {"code": exc.code, "message": exc.message},
                }
            )

    command = "uv run python pipelines/planetary/mola_aoi.py"
    command += (
        " --all-presets"
        if args.all_presets
        else (f" --preset {args.preset}" if args.preset else " --bbox ...")
    )
    command += " --allow-remote"
    if args.output:
        command += f" --output {args.output}"

    if args.all_presets or len(results) > 1:
        payload: dict[str, Any] = build_evidence(results, command=command)
    else:
        payload = results[0]

    if args.output:
        _write_json(args.output, payload)
        print(f"wrote {args.output} ({len(results)} AOI result(s))")
    else:
        print(json.dumps(payload, indent=2, sort_keys=True, allow_nan=False))
    return 2 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
