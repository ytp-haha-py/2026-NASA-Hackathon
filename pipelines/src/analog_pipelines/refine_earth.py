"""Real bounded 90 m refinement provider for coarse hits (Issue #37).

The provider reads one 1.98 km Copernicus DEM GLO-90 window per coarse focus centre
through GDAL ``/vsicurl`` HTTPS range reads, reuses the shared
``analog_pipelines.features.extract_features`` extractor (the same method versions as
the original Earth pipeline), and reads ESA WorldCover 2021 v200 as context only.

Copernicus GLO-90 tiles are 1 x 1 degree COGs. A 1.98 km focus window centred at a
degree boundary can straddle two tiles; reading only one would silently halve the valid
data. This module therefore range-reads the small intersecting window from each of the
1-4 tiles and composes a tiny local mosaic before extraction. No full tile is stored or
downloaded.

The on-demand AOI path in ``analog_api.adapters.earth_aoi`` validates WGS84 AOI bboxes
against a 2000 m side cap. A 1980 m square expressed in a projected CRS can have a
slightly larger WGS84 envelope at mid-latitudes (about 2006 m at 45 degrees), so this
module reuses the provider's datasets, GDAL settings and WorldCover reader, but calls
the fixed-window extractor directly rather than routing through the user-AOI validator.
No user-created AOI record is modified.

No full tiles, no global downloads, and no synthetic fallback are used here. A failed
window raises ``ProviderFailure`` and is recorded without zero-filling.
"""

from __future__ import annotations

import math
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
from analog_api.adapters.earth_aoi import (
    GDAL_ENV,
    EarthAoiProvider,
    copernicus_tile_url,
    read_worldcover_counts,
)
from analog_api.domain.entities import BBox
from analog_api.domain.errors import AppError
from analog_api.domain.features import (
    CONTEXT_FEATURE_BY_ID,
    CONTEXT_METHOD_VERSION,
    FLAT_METHOD_VERSION,
    METHOD_VERSION,
)
from pyproj import CRS
from rasterio.transform import from_origin
from rasterio.windows import Window

from analog_pipelines.features import ExtractionError, extract_features
from analog_pipelines.refine import (
    FocusWindow,
    ProviderFailure,
    RefinedMeasurement,
    utc_now_iso,
)

EXTRACTOR = "analog_pipelines.features.extract_features"
NO_COVERAGE_EXTRACTION_CODES = {"insufficient_valid_data"}
NATIVE_RES_DEG = 1.0 / 1200.0  # Copernicus GLO-90 = 3 arc-seconds
TILE_PIXELS = 1200
MOSAIC_PAD_DEG = 0.02  # ~2 km; bounds the mosaic to a tiny window
MIN_CONTEXT_VALID_FRACTION = 0.8


@dataclass(frozen=True, slots=True)
class MosaicWindow:
    rows: int
    cols: int
    west: float
    north: float
    res_deg: float
    tiles: tuple[str, ...]
    urls: tuple[str, ...]
    window_shapes: dict[str, tuple[int, int]]
    valid_pixels: int


def _context_observations(counts: dict[str, Any], dataset_id: str) -> dict[str, dict[str, Any]]:
    fractions = counts["fractions"]
    valid_fraction = float(counts.get("valid_fraction", 1.0))
    quality = "verified" if valid_fraction >= MIN_CONTEXT_VALID_FRACTION else "suspect"
    observations: dict[str, dict[str, Any]] = {}
    for feature_id, definition in CONTEXT_FEATURE_BY_ID.items():
        observations[feature_id] = {
            "value": round(float(fractions[definition.class_code]), 6),
            "unit": "fraction",
            "method_version": CONTEXT_METHOD_VERSION,
            "source_resolution_m": 10.0,
            "valid_fraction": round(valid_fraction, 6),
            "quality": quality,
            "class_code": definition.class_code,
            "provider": "ESA WorldCover",
            "dataset_id": dataset_id,
        }
    return observations


def _tile_origins(focus: FocusWindow, pad_deg: float = MOSAIC_PAD_DEG) -> tuple[int, int, int, int]:
    west = focus.focus_bbox_wgs84["west"] - pad_deg
    south = focus.focus_bbox_wgs84["south"] - pad_deg
    east = focus.focus_bbox_wgs84["east"] + pad_deg
    north = focus.focus_bbox_wgs84["north"] + pad_deg
    if west < -180.0 or east > 180.0 or south < -90.0 or north > 90.0:
        raise ProviderFailure(
            "provider_out_of_bounds",
            f"focus window plus pad crosses the WGS84 domain: {west},{south},{east},{north}",
        )
    return (
        math.floor(south + 1e-9),
        math.floor(north - 1e-9),
        math.floor(west + 1e-9),
        math.floor(east - 1e-9),
    )


def _mosaic_origin(col0: int, row0: int) -> tuple[float, float]:
    """Outer-edge origin of a Copernicus pixel-centre grid cell.

    Copernicus GLO-90 puts the first pixel *centre* on the tile's integer degree
    origin, so the outer edge is half a pixel outside it. Using the integer degree
    directly would shift a copied window by ~46 m.
    """
    return (
        -180.0 - NATIVE_RES_DEG / 2.0 + col0 * NATIVE_RES_DEG,
        90.0 + NATIVE_RES_DEG / 2.0 - row0 * NATIVE_RES_DEG,
    )


def _read_copernicus_mosaic(
    focus: FocusWindow, env: dict[str, str]
) -> tuple[np.ndarray, MosaicWindow]:
    """Range-read only the intersecting windows and compose a tiny local array."""
    lat_start, lat_end, lon_start, lon_end = _tile_origins(focus)
    west = focus.focus_bbox_wgs84["west"] - MOSAIC_PAD_DEG
    south = focus.focus_bbox_wgs84["south"] - MOSAIC_PAD_DEG
    east = focus.focus_bbox_wgs84["east"] + MOSAIC_PAD_DEG
    north = focus.focus_bbox_wgs84["north"] + MOSAIC_PAD_DEG

    col0 = math.floor((west + 180.0) / NATIVE_RES_DEG)
    col1 = math.ceil((east + 180.0) / NATIVE_RES_DEG)
    row0 = math.floor((90.0 - north) / NATIVE_RES_DEG)
    row1 = math.ceil((90.0 - south) / NATIVE_RES_DEG)
    width = col1 - col0
    height = row1 - row0
    if width <= 0 or height <= 0 or width > 4096 or height > 4096:
        raise ProviderFailure(
            "provider_invalid_window",
            f"refusing non-bounded mosaic window {width}x{height}",
        )

    mosaic = np.full((height, width), np.nan, dtype="float64")
    tiles: list[str] = []
    urls: list[str] = []
    shapes: dict[str, tuple[int, int]] = {}
    for lat0 in range(lat_start, lat_end + 1):
        for lon0 in range(lon_start, lon_end + 1):
            tile_col0 = int(round((lon0 + 180.0) * TILE_PIXELS))
            tile_row0 = int(round((90.0 - (lat0 + 1.0)) * TILE_PIXELS))
            gx0 = max(col0, tile_col0)
            gx1 = min(col1, tile_col0 + TILE_PIXELS)
            gy0 = max(row0, tile_row0)
            gy1 = min(row1, tile_row0 + TILE_PIXELS)
            if gx1 <= gx0 or gy1 <= gy0:
                continue
            url = copernicus_tile_url(lon0 + 0.5, lat0 + 0.5)
            tile_id = url.rsplit("/", 2)[-2]
            try:
                with rasterio.Env(**env), rasterio.open(url) as src:
                    src_crs = CRS.from_user_input(src.crs) if src.crs else None
                    if src_crs != CRS.from_epsg(4326):
                        raise ProviderFailure(
                            "provider_unexpected_source",
                            f"{url}: expected EPSG:4326, found {src.crs}",
                        )
                    if not math.isclose(
                        abs(float(src.res[1])), NATIVE_RES_DEG, abs_tol=1e-12, rel_tol=0.0
                    ):
                        raise ProviderFailure(
                            "provider_unexpected_source",
                            f"{url}: expected 3 arc-second spacing, found {src.res[1]}",
                        )
                    window = Window(
                        col_off=gx0 - tile_col0,
                        row_off=gy0 - tile_row0,
                        width=gx1 - gx0,
                        height=gy1 - gy0,
                    )
                    data = src.read(1, window=window, masked=True, out_dtype="float64")
                    array = np.asarray(data.filled(np.nan), dtype="float64")
                    if src.nodata is not None:
                        array = np.where(array == float(src.nodata), np.nan, array)
            except ProviderFailure:
                raise
            except Exception as exc:  # noqa: BLE001 - network/provider boundary
                raise ProviderFailure(
                    "source_fetch_failed",
                    f"Copernicus tile range read failed for {url}: {exc}",
                    details={"source_url": url, "tiles_read": tiles},
                ) from exc
            expected_shape = (gy1 - gy0, gx1 - gx0)
            if array.shape != expected_shape:
                raise ProviderFailure(
                    "provider_grid_mismatch",
                    f"{url}: window shape {array.shape} != expected {expected_shape}",
                )
            mosaic[
                gy0 - row0 : gy1 - row0,
                gx0 - col0 : gx1 - col0,
            ] = array
            tiles.append(tile_id)
            urls.append(url)
            shapes[tile_id] = expected_shape

    valid_pixels = int(np.isfinite(mosaic).sum())
    if valid_pixels == 0:
        raise ProviderFailure(
            "no_coverage",
            "Copernicus GLO-90 returned no valid elevation pixels for the focus window",
            no_coverage=True,
            details={"tiles_read": tiles, "source_urls": urls},
        )
    # Copernicus GLO-90 is pixel-centre registered on integer degrees: the first
    # pixel centre is at the tile's integer origin and the outer edge is half a
    # pixel outside. The mosaic transform must use the same convention.
    west, north = _mosaic_origin(col0, row0)
    return mosaic, MosaicWindow(
        rows=height,
        cols=width,
        west=west,
        north=north,
        res_deg=NATIVE_RES_DEG,
        tiles=tuple(tiles),
        urls=tuple(urls),
        window_shapes=shapes,
        valid_pixels=valid_pixels,
    )


def _write_mosaic(array: np.ndarray, mosaic: MosaicWindow, path: Path) -> None:
    profile = {
        "driver": "GTiff",
        "height": mosaic.rows,
        "width": mosaic.cols,
        "count": 1,
        "dtype": "float64",
        "crs": "EPSG:4326",
        "transform": from_origin(mosaic.west, mosaic.north, mosaic.res_deg, mosaic.res_deg),
        "nodata": np.nan,
        "compress": "deflate",
    }
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(array, 1)


class RefineEarthProvider:
    """Bounded Copernicus GLO-90 terrain + ESA WorldCover context provider."""

    def __init__(self, *, timeout: float = 25.0) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        base = EarthAoiProvider()
        if base.earth_dataset is None:
            raise RuntimeError(
                "the Copernicus DEM dataset manifest is missing; cannot configure refinement"
            )
        self.earth_dataset = base.earth_dataset
        self.worldcover_dataset = base.worldcover_dataset
        self.timeout = float(timeout)

    def __call__(self, focus: FocusWindow) -> RefinedMeasurement:
        env = dict(GDAL_ENV)
        env["GDAL_HTTP_TIMEOUT"] = str(max(1, int(self.timeout)))
        try:
            array, mosaic = _read_copernicus_mosaic(focus, env)
            with tempfile.TemporaryDirectory(prefix="analog-refine-") as directory:
                local_window = Path(directory) / "copernicus-focus.tif"
                _write_mosaic(array, mosaic, local_window)
                extraction = extract_features(
                    str(local_window),
                    center_lonlat=(focus.center_lon, focus.center_lat),
                    source_label="copernicus-mosaic:" + ",".join(mosaic.tiles),
                    body="Earth",
                    warnings_extra=["bounded_gdal_vsicurl_range_read", "bounded_tile_mosaic"],
                )
        except ProviderFailure:
            raise
        except ExtractionError as exc:
            raise ProviderFailure(
                exc.code,
                exc.message,
                no_coverage=exc.code in NO_COVERAGE_EXTRACTION_CODES,
                details={"source_tiles": [], "coarse_candidate_id": focus.coarse_candidate_id},
            ) from exc
        except Exception as exc:  # noqa: BLE001 - network/provider boundary
            raise ProviderFailure(
                "source_fetch_failed",
                f"Copernicus DEM window read failed: {exc}",
                details={"coarse_candidate_id": focus.coarse_candidate_id},
            ) from exc

        if extraction.bbox_wgs84 is None:
            raise ProviderFailure(
                "provider_invalid_result",
                "extractor returned no measured WGS84 bbox",
                details={"source_tiles": list(mosaic.tiles)},
            )

        warnings = list(extraction.warnings)
        context: dict[str, dict[str, Any]] = {}
        context_bbox = BBox(**extraction.bbox_wgs84)
        try:
            counts = read_worldcover_counts(context_bbox)
            context = _context_observations(counts, self.worldcover_dataset.dataset_id)
            worldcover_status: dict[str, Any] = {
                "status": "verified",
                "quality": (
                    "verified"
                    if float(counts.get("valid_fraction", 1.0)) >= MIN_CONTEXT_VALID_FRACTION
                    else "suspect"
                ),
                "source_url": "https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/",
                "tiles": len(counts["tiles"]),
                "valid_pixels": counts["valid_pixels"],
                "valid_fraction": counts.get("valid_fraction"),
                "checksum": counts["checksum"],
                "access_timestamp": utc_now_iso(),
            }
        except AppError as exc:
            worldcover_status = {
                "status": "no_coverage" if exc.code == "context_out_of_coverage" else "failed",
                "reason": exc.code,
                "message": exc.message,
            }
            warnings.append(f"worldcover_{exc.code}")
        except Exception as exc:  # noqa: BLE001 - provider boundary
            worldcover_status = {
                "status": "failed",
                "reason": "context_provider_failed",
                "message": str(exc),
            }
            warnings.append("worldcover_context_provider_failed")

        datasets = tuple(
            asdict(dataset) for dataset in (self.earth_dataset, self.worldcover_dataset)
        )
        return RefinedMeasurement(
            features=dict(extraction.features),
            analysis_crs=extraction.analysis_crs,
            valid_fraction=float(extraction.valid_fraction),
            window_size_m=float(extraction.window_size_m),
            resolution_m=float(extraction.resolution_m),
            checksum=extraction.window_checksum,
            datasets=datasets,
            provider_status={
                "copernicus-dem-glo90": {
                    "status": "verified",
                    "source_tiles": list(mosaic.tiles),
                    "source_urls": list(mosaic.urls),
                    "read_mode": "bounded_gdal_vsicurl_range_read_bounded_mosaic",
                    "window_shapes": {
                        tile_id: list(shape) for tile_id, shape in mosaic.window_shapes.items()
                    },
                    "valid_pixels": mosaic.valid_pixels,
                    "window_checksum": extraction.window_checksum,
                    "access_timestamp": utc_now_iso(),
                },
                self.worldcover_dataset.dataset_id: worldcover_status,
            },
            quality_flags=(
                "coarse_refined_window",
                "focus_center_placement",
                "copernicus_dsm_may_include_vegetation_buildings",
                "glo90_native_3_arcsec_bilinear_to_90m",
                "worldcover_context_2021_epoch",
            ),
            warnings=tuple(dict.fromkeys(warnings)),
            context_observations=context,
            method_versions={
                "extractor": EXTRACTOR,
                "terrain": METHOD_VERSION,
                "flat": FLAT_METHOD_VERSION,
                "context": CONTEXT_METHOD_VERSION,
            },
            area_km2=float(extraction.window_size_m**2 / 1e6),
            center_lon=extraction.center_lon,
            center_lat=extraction.center_lat,
            bbox_wgs84=dict(extraction.bbox_wgs84),
            data_kind="observed",
        )


__all__ = ["EXTRACTOR", "MosaicWindow", "RefineEarthProvider"]
