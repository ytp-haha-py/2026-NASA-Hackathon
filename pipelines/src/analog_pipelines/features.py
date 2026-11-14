"""Shared, reproducible terrain-feature extraction for Earth and planetary DEMs.

The extractor is deliberately independent from any provider downloader: callers pass a
local path or GDAL-openable URL plus a window centre. All slope/relief/roughness metrics
come from real elevation values in a metric CRS; image colours are never used.

Method version: terrain-metrics-1.0.0
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import rasterio
from analog_api.domain.features import (
    ANALYSIS_RESOLUTION_M,
    ANALYSIS_WINDOW_M,
    FEATURE_BY_ID,
    MIN_VALID_FRACTION,
)
from pyproj import CRS, Transformer
from rasterio.enums import Resampling

GRID_SIZE = ANALYSIS_WINDOW_M // ANALYSIS_RESOLUTION_M
METRE_UNITS = {"m", "metre", "meter", "metres", "meters"}


class ExtractionError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class ExtractionResult:
    features: dict[str, float]
    analysis_crs: str
    valid_fraction: float
    center_lon: float | None
    center_lat: float | None
    bbox_wgs84: dict[str, float] | None
    window_size_m: float
    resolution_m: float
    window_checksum: str
    warnings: tuple[str, ...] = field(default_factory=tuple)
    width_m: float | None = None
    height_m: float | None = None
    area_km2: float | None = None
    geometry_wgs84: dict[str, Any] | None = None


def _utm_crs(lon: float, lat: float) -> CRS:
    if not (-80.0 <= lat <= 84.0):
        raise ExtractionError(
            "unsupported_projection",
            "Automatic UTM selection is limited to latitude -80..84; supply a projected "
            "metric source CRS instead.",
        )
    zone = int((lon + 180.0) // 6.0) + 1
    epsg = (32600 if lat >= 0 else 32700) + zone
    return CRS.from_epsg(epsg)


def _choose_analysis_crs(src_crs: CRS, lon: float, lat: float) -> CRS:
    if src_crs is None:
        raise ExtractionError("missing_crs", "The source raster has no CRS.")
    if src_crs.is_projected:
        unit = src_crs.axis_info[0].unit_name.lower() if src_crs.axis_info else ""
        if unit not in METRE_UNITS:
            raise ExtractionError(
                "non_metric_crs",
                f"Projected source CRS units are {unit!r}, expected metres.",
            )
        return src_crs
    return _utm_crs(lon, lat)


def horn_slope_degrees(
    elevation: np.ndarray, cell_size_m: float, cell_size_y_m: float | None = None
) -> np.ndarray:
    """Horn 3x3 slope in degrees; NaN where the 3x3 neighbourhood is incomplete."""
    z = np.asarray(elevation, dtype="float64")
    cell_size_y = cell_size_m if cell_size_y_m is None else cell_size_y_m
    if z.ndim != 2 or min(z.shape) < 3:
        raise ExtractionError("grid_too_small", "Slope requires at least a 3x3 grid.")
    p = np.pad(z, 1, mode="constant", constant_values=np.nan)
    z1, z2, z3 = p[0:-2, 0:-2], p[0:-2, 1:-1], p[0:-2, 2:]
    z4, z5, z6 = p[1:-1, 0:-2], p[1:-1, 1:-1], p[1:-1, 2:]
    z7, z8, z9 = p[2:, 0:-2], p[2:, 1:-1], p[2:, 2:]
    valid = np.isfinite(z1 + z2 + z3 + z4 + z5 + z6 + z7 + z8 + z9)
    dzdx = ((z3 + 2 * z6 + z9) - (z1 + 2 * z4 + z7)) / (8.0 * cell_size_m)
    dzdy = ((z7 + 2 * z8 + z9) - (z1 + 2 * z2 + z3)) / (8.0 * cell_size_y)
    slope = np.degrees(np.arctan(np.hypot(dzdx, dzdy)))
    return np.where(valid, slope, np.nan)


def terrain_metrics(
    elevation: np.ndarray,
    cell_size_m: float,
    cell_size_y_m: float | None = None,
    domain_mask: np.ndarray | None = None,
) -> dict[str, float]:
    """Compute the four registry metrics from a metric-CRS elevation window.

    Invalid cells must be NaN. `domain_mask` (True inside the AOI polygon) restricts the
    statistics and the valid-fraction denominator. Raises ExtractionError when too few cells
    are usable.
    """
    z = np.asarray(elevation, dtype="float64")
    if domain_mask is None:
        domain = np.ones(z.shape, dtype=bool)
    else:
        domain = np.asarray(domain_mask, dtype=bool)
    valid = np.isfinite(z) & domain
    denominator = int(domain.sum())
    if denominator == 0:
        raise ExtractionError("insufficient_valid_data", "AOI contains no analysis cells.")
    valid_fraction = float(valid.sum()) / float(denominator)
    if valid_fraction < MIN_VALID_FRACTION:
        raise ExtractionError(
            "insufficient_valid_data",
            f"Valid elevation fraction {valid_fraction:.2f} is below the required "
            f"{MIN_VALID_FRACTION:.2f}.",
        )

    slopes = horn_slope_degrees(z, cell_size_m, cell_size_y_m)
    slope_values = slopes[np.isfinite(slopes) & domain]
    if slope_values.size == 0:
        raise ExtractionError("insufficient_valid_data", "No valid slope cells.")

    z_values = z[valid]
    relief = float(np.percentile(z_values, 95) - np.percentile(z_values, 5))

    rows, cols = np.indices(z.shape)
    valid_rows = rows[valid].astype("float64")
    valid_cols = cols[valid].astype("float64")
    design = np.column_stack([valid_rows, valid_cols, np.ones_like(valid_rows)])
    coefficients, *_ = np.linalg.lstsq(design, z_values, rcond=None)
    residuals = z_values - design @ coefficients
    roughness = float(np.std(residuals))

    return {
        "slope_mean_deg": float(np.mean(slope_values)),
        "slope_p90_deg": float(np.percentile(slope_values, 90)),
        "terrain_relief_p95_p05_m": relief,
        "roughness_std_m": roughness,
        "flat_terrain_fraction": float(np.count_nonzero(slope_values < 5.0) / slope_values.size),
    }


def _checksum(array: np.ndarray, source_label: str) -> str:
    canonical = np.nan_to_num(array, nan=-9999.0).astype("<f8").tobytes()
    digest = hashlib.sha256(source_label.encode("utf-8") + canonical).hexdigest()
    return f"sha256:{digest}"


def _read_window(path: str | Any, dst_crs: CRS, center_xy: tuple[float, float]):
    from rasterio.transform import from_origin
    from rasterio.warp import reproject
    from rasterio.windows import from_bounds

    with rasterio.open(path) as src:
        src_crs = CRS.from_user_input(src.crs) if src.crs else None
        if src_crs is None:
            raise ExtractionError("missing_crs", "The source raster has no CRS.")
        if src_crs.is_projected:
            units = src_crs.axis_info[0].unit_name.lower() if src_crs.axis_info else ""
            if units not in METRE_UNITS:
                raise ExtractionError(
                    "non_metric_crs", f"Projected source CRS units are {units!r}, expected metres."
                )

        x, y = center_xy
        half = ANALYSIS_WINDOW_M / 2.0
        dst_transform = from_origin(
            x - half, y + half, ANALYSIS_RESOLUTION_M, ANALYSIS_RESOLUTION_M
        )

        # Requested window in destination CRS -> source CRS window.
        to_src = Transformer.from_crs(dst_crs, src_crs, always_xy=True)
        corners_x = [x - half, x + half, x - half, x + half]
        corners_y = [y - half, y - half, y + half, y + half]
        src_xs, src_ys = to_src.transform(corners_x, corners_y)
        pad = 2 * max(abs(src.transform.a), abs(src.transform.e))
        src_window = (
            from_bounds(
                min(src_xs) - pad,
                min(src_ys) - pad,
                max(src_xs) + pad,
                max(src_ys) + pad,
                src.transform,
            )
            .round_offsets()
            .round_lengths()
        )

        fill = src.nodata if src.nodata is not None else np.nan
        data = src.read(
            1,
            window=src_window,
            boundless=True,
            masked=True,
            out_dtype="float64",
            fill_value=fill,
        )
        source = np.asarray(data.filled(np.nan), dtype="float64")
        src_window_transform = src.window_transform(src_window)

        destination = np.full((GRID_SIZE, GRID_SIZE), np.nan, dtype="float64")
        reproject(
            source=source,
            destination=destination,
            src_transform=src_window_transform,
            src_crs=src_crs,
            src_nodata=np.nan,
            dst_transform=dst_transform,
            dst_crs=dst_crs,
            dst_nodata=np.nan,
            resampling=Resampling.bilinear,
        )
        warnings: list[str] = []
        if src.nodata is None:
            warnings.append("source_nodata_undefined")
        return destination, dst_transform, dst_crs.to_string(), warnings, dst_crs


def _bbox_wgs84(transform, dst_crs: CRS) -> dict[str, float]:
    xs = [transform.c, transform.c + transform.a * GRID_SIZE]
    ys = [transform.f, transform.f + transform.e * GRID_SIZE]
    transformer = Transformer.from_crs(dst_crs, "EPSG:4326", always_xy=True)
    lons, lats = transformer.transform([xs[0], xs[1], xs[0], xs[1]], [ys[0], ys[0], ys[1], ys[1]])
    return {
        "west": min(lons),
        "south": min(lats),
        "east": max(lons),
        "north": max(lats),
    }


def extract_features(
    path: str | Any,
    *,
    center_lonlat: tuple[float, float] | None = None,
    center_xy: tuple[float, float] | None = None,
    source_label: str | None = None,
    body: str = "Earth",
    warnings_extra: list[str] | None = None,
) -> ExtractionResult:
    """Extract the registry metrics for a fixed 22x22 / 90 m window.

    Provide either `center_lonlat` (WGS84 degrees) or `center_xy` in the source CRS.
    """
    if (center_lonlat is None) == (center_xy is None):
        raise ExtractionError(
            "invalid_centre", "Provide exactly one of center_lonlat or center_xy."
        )
    if body != "Earth" and center_lonlat is not None:
        raise ExtractionError(
            "planetary_center_required", "Use source projected X/Y for planetary DEMs."
        )
    label = source_label or str(path)
    with rasterio.open(path) as src:
        src_crs = CRS.from_user_input(src.crs) if src.crs else None
        if src_crs is None:
            raise ExtractionError("missing_crs", "The source raster has no CRS.")
        if center_lonlat is not None:
            lon, lat = center_lonlat
            if src_crs.is_projected:
                dst_crs = _choose_analysis_crs(src_crs, lon, lat)
                transformer = Transformer.from_crs("EPSG:4326", dst_crs, always_xy=True)
                x, y = transformer.transform(lon, lat)
            else:
                dst_crs = _utm_crs(lon, lat)
                transformer = Transformer.from_crs("EPSG:4326", dst_crs, always_xy=True)
                x, y = transformer.transform(lon, lat)
        else:
            assert center_xy is not None
            x, y = center_xy
            geographic_crs = src_crs.geodetic_crs if body != "Earth" else CRS("EPSG:4326")
            lon, lat = Transformer.from_crs(src_crs, geographic_crs, always_xy=True).transform(x, y)
            dst_crs = _choose_analysis_crs(src_crs, lon, lat)
            if body != "Earth" and not dst_crs.is_projected:
                raise ExtractionError(
                    "unsupported_projection",
                    f"{body} extraction requires a projected metric source CRS.",
                )
            lon = lat = None

    array, dst_transform, crs_text, warnings, dst_crs = _read_window(path, dst_crs, (x, y))
    features = terrain_metrics(array, ANALYSIS_RESOLUTION_M)

    if center_lonlat is not None:
        center_lon, center_lat = center_lonlat
        bbox = _bbox_wgs84(dst_transform, dst_crs)
    else:
        geographic_crs = dst_crs.geodetic_crs if body != "Earth" else CRS("EPSG:4326")
        center_lon, center_lat = Transformer.from_crs(
            dst_crs, geographic_crs, always_xy=True
        ).transform(x, y)
        bbox = _bbox_wgs84(dst_transform, dst_crs) if body == "Earth" else None

    valid_fraction = float(np.isfinite(array).sum()) / float(array.size)
    all_warnings = list(warnings) + list(warnings_extra or [])
    return ExtractionResult(
        features={key: float(value) for key, value in features.items()},
        analysis_crs=crs_text,
        valid_fraction=valid_fraction,
        center_lon=center_lon,
        center_lat=center_lat,
        bbox_wgs84=bbox,
        window_size_m=float(ANALYSIS_WINDOW_M),
        resolution_m=float(ANALYSIS_RESOLUTION_M),
        window_checksum=_checksum(array, label),
        warnings=tuple(all_warnings),
    )


def _project_geometry(geometry: dict[str, Any], dst_crs: CRS) -> dict[str, Any]:
    transformer = Transformer.from_crs("EPSG:4326", dst_crs, always_xy=True)
    rings = []
    for ring in geometry["coordinates"]:
        xs, ys = transformer.transform([point[0] for point in ring], [point[1] for point in ring])
        rings.append([[float(x), float(y)] for x, y in zip(xs, ys, strict=True)])
    return {"type": "Polygon", "coordinates": rings}


def _read_bounds_window(
    path: str | Any,
    dst_crs: CRS,
    bounds: tuple[float, float, float, float],
    grid: tuple[int, int],
    geometry: dict[str, Any] | None,
):
    from rasterio.features import geometry_mask
    from rasterio.transform import from_origin
    from rasterio.warp import reproject
    from rasterio.windows import from_bounds

    minx, miny, maxx, maxy = bounds
    nx, ny = grid
    resx = (maxx - minx) / nx
    resy = (maxy - miny) / ny
    dst_transform = from_origin(minx, maxy, resx, resy)

    with rasterio.open(path) as src:
        src_crs = CRS.from_user_input(src.crs) if src.crs else None
        if src_crs is None:
            raise ExtractionError("missing_crs", "The source raster has no CRS.")
        if src_crs.is_projected:
            units = src_crs.axis_info[0].unit_name.lower() if src_crs.axis_info else ""
            if units not in METRE_UNITS:
                raise ExtractionError(
                    "non_metric_crs", f"Projected source CRS units are {units!r}, expected metres."
                )
        to_src = Transformer.from_crs(dst_crs, src_crs, always_xy=True)
        src_xs, src_ys = to_src.transform([minx, maxx, minx, maxx], [miny, miny, maxy, maxy])
        pad = 2 * max(abs(src.transform.a), abs(src.transform.e))
        src_window = (
            from_bounds(
                min(src_xs) - pad,
                min(src_ys) - pad,
                max(src_xs) + pad,
                max(src_ys) + pad,
                src.transform,
            )
            .round_offsets()
            .round_lengths()
        )
        fill = src.nodata if src.nodata is not None else np.nan
        data = src.read(
            1,
            window=src_window,
            boundless=True,
            masked=True,
            out_dtype="float64",
            fill_value=fill,
        )
        source = np.asarray(data.filled(np.nan), dtype="float64")
        src_window_transform = src.window_transform(src_window)
        destination = np.full((ny, nx), np.nan, dtype="float64")
        reproject(
            source=source,
            destination=destination,
            src_transform=src_window_transform,
            src_crs=src_crs,
            src_nodata=np.nan,
            dst_transform=dst_transform,
            dst_crs=dst_crs,
            dst_nodata=np.nan,
            resampling=Resampling.bilinear,
        )
        warnings: list[str] = []
        if src.nodata is None:
            warnings.append("source_nodata_undefined")

    domain = np.ones((ny, nx), dtype=bool)
    if geometry is not None:
        projected = _project_geometry(geometry, dst_crs)
        domain = geometry_mask(
            [projected], out_shape=(ny, nx), transform=dst_transform, invert=True
        )
    return destination, dst_transform, warnings, domain, resx, resy


def extract_features_for_bounds(
    path: str | Any,
    *,
    bbox: dict[str, float],
    geometry: dict[str, Any] | None = None,
    source_label: str | None = None,
    body: str = "Earth",
    warnings_extra: list[str] | None = None,
) -> ExtractionResult:
    """Extract the registry metrics over an explicit AOI bbox/polygon.

    The analysis grid uses 90 m-class square cells in a projected metric CRS; the AOI is
    validated by the API before this function is called (bounded footprint). Polygon
    geometry is masked in the destination grid; statistics use the polygon domain.
    """
    from pyproj import Geod

    west = float(bbox["west"])
    south = float(bbox["south"])
    east = float(bbox["east"])
    north = float(bbox["north"])
    center_lon = (west + east) / 2.0
    center_lat = (south + north) / 2.0
    label = source_label or str(path)

    with rasterio.open(path) as src:
        src_crs = CRS.from_user_input(src.crs) if src.crs else None
        if src_crs is None:
            raise ExtractionError("missing_crs", "The source raster has no CRS.")
        dst_crs = _choose_analysis_crs(src_crs, center_lon, center_lat)

    transformer = Transformer.from_crs("EPSG:4326", dst_crs, always_xy=True)
    xs, ys = transformer.transform([west, east, west, east], [south, south, north, north])
    minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
    width = maxx - minx
    height = maxy - miny
    if width <= 0 or height <= 0:
        raise ExtractionError("invalid_aoi", "AOI has zero metric extent.")

    nx = max(5, round(width / ANALYSIS_RESOLUTION_M))
    ny = max(5, round(height / ANALYSIS_RESOLUTION_M))
    array, dst_transform, warnings, domain, resx, resy = _read_bounds_window(
        path, dst_crs, (minx, miny, maxx, maxy), (nx, ny), geometry
    )
    features = terrain_metrics(array, resx, resy, domain_mask=domain)
    valid_fraction = float(np.isfinite(array)[domain].sum()) / float(max(domain.sum(), 1))

    geod = Geod(ellps="WGS84")
    if geometry is not None:
        ring = geometry["coordinates"][0]
        lons = [float(point[0]) for point in ring]
        lats = [float(point[1]) for point in ring]
        area_km2 = abs(geod.polygon_area_perimeter(lons, lats)[0]) / 1e6
    else:
        area_km2 = (
            abs(
                geod.polygon_area_perimeter([west, east, east, west], [south, south, north, north])[
                    0
                ]
            )
            / 1e6
        )

    return ExtractionResult(
        features={key: float(value) for key, value in features.items()},
        analysis_crs=dst_crs.to_string(),
        valid_fraction=valid_fraction,
        center_lon=center_lon,
        center_lat=center_lat,
        bbox_wgs84={"west": west, "south": south, "east": east, "north": north},
        window_size_m=float(max(width, height)),
        resolution_m=float((resx + resy) / 2.0),
        window_checksum=_checksum(array, label),
        warnings=tuple(warnings) + tuple(warnings_extra or ()),
        width_m=float(width),
        height_m=float(height),
        area_km2=float(area_km2),
        geometry_wgs84=geometry,
    )


def make_observations(
    features: dict[str, float],
    *,
    valid_fraction: float,
    quality: str = "verified",
    extraction_scale_m: float = float(ANALYSIS_RESOLUTION_M),
) -> dict[str, dict[str, Any]]:
    """Shape feature values as the pipeline-manifest observation objects."""
    return {
        feature_id: {
            "value": round(float(value), 6),
            "unit": FEATURE_BY_ID[feature_id].unit,
            "method_version": FEATURE_BY_ID[feature_id].method_version,
            "extraction_scale_m": extraction_scale_m,
            "valid_fraction": round(float(valid_fraction), 6),
            "quality": quality,
        }
        for feature_id, value in features.items()
    }


def angle_from_gradient(grade: float) -> float:
    """Convenience for tests: slope angle in degrees from a dimensionless grade."""
    return math.degrees(math.atan(grade))


def flat_terrain_fraction(
    elevation: np.ndarray,
    cell_size_m: float,
    cell_size_y_m: float | None = None,
    domain_mask: np.ndarray | None = None,
) -> float:
    """Fraction of valid Horn-slope cells strictly below 5 degrees.

    The denominator excludes the outer rim and every incomplete 3x3 neighborhood.
    A nodata cell is never treated as flat. No padding/interpolation fills a slope.
    This shares the original four metrics' gradient grid and analysis support.
    """
    slopes = horn_slope_degrees(elevation, cell_size_m, cell_size_y_m)
    valid = np.isfinite(slopes)
    if domain_mask is not None:
        valid &= np.asarray(domain_mask, dtype=bool)
    if not valid.any():
        raise ExtractionError("insufficient_valid_data", "No valid slope cells for flat fraction.")
    return float(np.count_nonzero(slopes[valid] < 5.0) / np.count_nonzero(valid))
