from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import numpy as np
import rasterio
from analog_pipelines.features import ExtractionError, extract_features_for_bounds
from rasterio.windows import from_bounds

from analog_api.config import REPO_ROOT
from analog_api.domain.aoi import AoiResult, ValidatedAoi
from analog_api.domain.entities import BBox, DatasetEntity
from analog_api.domain.errors import AppError
from analog_api.domain.features import (
    CONTEXT_FEATURE_BY_ID,
    CONTEXT_METHOD_VERSION,
    WORLDCOVER_CLASSES,
)
from analog_api.domain.manifest import Manifest

EARTH_MANIFEST_DIR = REPO_ROOT / "pipelines" / "earth" / "manifests"
CONTEXT_MANIFEST_DIR = REPO_ROOT / "pipelines" / "worldcover" / "manifests"
COPERNICUS_BUCKET = "https://copernicus-dem-90m.s3.amazonaws.com"
WORLDCOVER_BUCKET = "https://esa-worldcover.s3.eu-central-1.amazonaws.com"
WORLDCOVER_PREFIX = "v200/2021/map"
WORLDCOVER_CLASS_CODES = {code for code, _, _ in WORLDCOVER_CLASSES}

GDAL_ENV = {
    "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
    "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif",
    "GDAL_HTTP_TIMEOUT": "60",
    "GDAL_HTTP_MAX_RETRY": "2",
    "GDAL_HTTP_RETRY_DELAY": "1",
}

BUILTIN_WORLDCOVER_DATASET = DatasetEntity(
    dataset_id="esa-worldcover-2021-v200",
    provider="European Space Agency (ESA) WorldCover / VITO",
    product_id="ESA WorldCover 2021 v200",
    title="ESA WorldCover 10 m 2021 v200 land cover",
    url="https://esa-worldcover.org/en/data-access",
    license="CC BY 4.0",
    license_url="https://creativecommons.org/licenses/by/4.0/",
    access_timestamp="2026-11-14T00:00:00Z",
    source_crs="EPSG:4326",
    native_resolution_m=10.0,
    processing_version=CONTEXT_METHOD_VERSION,
    checksum=None,
    source_kind="landcover",
    notes=(
        "Land-cover context only, not mineralogy or geology. 2021 epoch, 3x3 degree COGs, "
        "11 classes, nodata=0. Attribution: © ESA WorldCover project 2021 / Contains modified "
        "Copernicus Sentinel data (2021) processed by ESA WorldCover consortium."
    ),
)


def copernicus_tile_url(lon: float, lat: float) -> str:
    lat_floor = math.floor(lat)
    lon_floor = math.floor(lon)
    ns = "N" if lat_floor >= 0 else "S"
    ew = "E" if lon_floor >= 0 else "W"
    tile = f"Copernicus_DSM_COG_30_{ns}{abs(lat_floor):02d}_00_{ew}{abs(lon_floor):03d}_00_DEM"
    return f"{COPERNICUS_BUCKET}/{tile}/{tile}.tif"


def _load_dataset(source_kind: str, directory: Path) -> DatasetEntity | None:
    if not directory.is_dir():
        return None
    for path in sorted(directory.glob("*.json")):
        try:
            manifest = Manifest.model_validate(json.loads(path.read_text(encoding="utf-8")))
        except Exception:  # noqa: BLE001 - malformed committed data must not crash AOI
            continue
        for dataset in manifest.datasets:
            if dataset.source_kind == source_kind:
                return DatasetEntity(
                    dataset_id=dataset.dataset_id,
                    provider=dataset.provider,
                    product_id=dataset.product_id,
                    title=dataset.title,
                    url=dataset.url,
                    license=dataset.license,
                    license_url=dataset.license_url,
                    access_timestamp=dataset.access_timestamp,
                    source_crs=dataset.source_crs,
                    native_resolution_m=dataset.native_resolution_m,
                    processing_version=dataset.processing_version,
                    checksum=dataset.checksum,
                    source_kind=dataset.source_kind,
                    notes=dataset.notes,
                )
    return None


def worldcover_tile_url(lat0: int, lon0: int) -> str:
    ns = "N" if lat0 >= 0 else "S"
    ew = "E" if lon0 >= 0 else "W"
    name = f"ESA_WorldCover_10m_2021_v200_{ns}{abs(lat0):02d}{ew}{abs(lon0):03d}_Map.tif"
    return f"{WORLDCOVER_BUCKET}/{WORLDCOVER_PREFIX}/{name}"


def read_worldcover_counts(bbox: BBox) -> dict[str, object]:
    """Count WorldCover classes over the AOI using native 10 m window reads."""
    counts = {code: 0 for code in WORLDCOVER_CLASS_CODES}
    total = 0
    total_pixels = 0
    tiles = []
    lat_start = math.floor(bbox.south / 3.0) * 3
    lon_start = math.floor(bbox.west / 3.0) * 3
    lat_values = list(np.arange(lat_start, bbox.north + 3.0, 3.0))
    lon_values = list(np.arange(lon_start, bbox.east + 3.0, 3.0))
    for lat0 in lat_values:
        for lon0 in lon_values:
            tile_bbox = BBox(
                west=max(bbox.west, lon0),
                south=max(bbox.south, lat0),
                east=min(bbox.east, lon0 + 3.0),
                north=min(bbox.north, lat0 + 3.0),
            )
            if tile_bbox.east <= tile_bbox.west or tile_bbox.north <= tile_bbox.south:
                continue
            url = worldcover_tile_url(int(lat0), int(lon0))
            tiles.append(url)
            try:
                with rasterio.Env(**GDAL_ENV), rasterio.open(url) as src:
                    window = from_bounds(
                        tile_bbox.west,
                        tile_bbox.south,
                        tile_bbox.east,
                        tile_bbox.north,
                        src.transform,
                    )
                    data = src.read(1, window=window, boundless=False, masked=True)
                    array = np.asarray(data.filled(0))
            except Exception as exc:  # noqa: BLE001 - report provider failure precisely
                raise AppError(
                    "context_provider_failed",
                    f"ESA WorldCover window read failed for {url}: {exc}",
                    status_code=503,
                ) from exc
            total_pixels += int(array.size)
            values, counts_per_class = np.unique(array, return_counts=True)
            for value, count in zip(values.tolist(), counts_per_class.tolist(), strict=True):
                value = int(value)
                if value in WORLDCOVER_CLASS_CODES:
                    counts[value] += int(count)
                    total += int(count)
    if total == 0:
        raise AppError(
            "context_out_of_coverage",
            "ESA WorldCover returned no valid land-cover pixels for this AOI.",
            status_code=422,
        )
    fractions = {code: counts[code] / total for code in counts}
    checksum = hashlib.sha256(
        json.dumps({"bbox": bbox.as_dict(), "classes": counts}, sort_keys=True).encode()
    ).hexdigest()
    return {
        "fractions": fractions,
        "valid_pixels": total,
        "total_pixels": total_pixels,
        "valid_fraction": total / total_pixels if total_pixels else 0.0,
        "tiles": tiles,
        "checksum": f"sha256:{checksum}",
    }


def _context_observations(
    counts: dict[str, object], dataset_id: str
) -> dict[str, dict[str, object]]:
    fractions = counts["fractions"]
    observations: dict[str, dict[str, object]] = {}
    for feature_id, definition in CONTEXT_FEATURE_BY_ID.items():
        observations[feature_id] = {
            "value": round(float(fractions[definition.class_code]), 6),
            "unit": "fraction",
            "method_version": CONTEXT_METHOD_VERSION,
            "source_resolution_m": 10.0,
            "valid_fraction": round(float(counts.get("valid_fraction", 1.0)), 6),
            "quality": "verified",
            "class_code": definition.class_code,
            "provider": "ESA WorldCover",
            "dataset_id": dataset_id,
        }
    return observations


class EarthAoiProvider:
    """Real Copernicus DEM terrain + ESA WorldCover context provider for bounded AOIs."""

    def __init__(self) -> None:
        self.earth_dataset = _load_dataset("earth_dem", EARTH_MANIFEST_DIR)
        self.worldcover_dataset = (
            _load_dataset("landcover", CONTEXT_MANIFEST_DIR) or BUILTIN_WORLDCOVER_DATASET
        )

    def analyze(self, request: ValidatedAoi) -> AoiResult:
        if self.earth_dataset is None:
            raise AppError(
                "provider_not_configured",
                "The Copernicus DEM dataset manifest is missing; no on-demand provider "
                "is configured.",
                status_code=503,
            )
        url = copernicus_tile_url(request.bbox.west, request.bbox.south)
        try:
            with rasterio.Env(**GDAL_ENV):
                extraction = extract_features_for_bounds(
                    url,
                    bbox=request.bbox.as_dict(),
                    geometry=request.geometry,
                    source_label=f"copernicus:{url}",
                )
        except ExtractionError as exc:
            raise AppError(
                "source_extraction_failed",
                f"Copernicus DEM extraction failed ({exc.code}): {exc.message}",
                status_code=422,
            ) from exc
        except Exception as exc:  # noqa: BLE001 - network/provider boundary
            raise AppError(
                "source_fetch_failed",
                "Copernicus DEM window read failed; the provider may be unavailable or the "
                "AOI may be outside tile coverage.",
                status_code=503,
            ) from exc

        features = {
            feature_id: value
            for feature_id, value in extraction.features.items()
            if feature_id in request.requested_features
        }
        provider_status: dict[str, dict[str, object]] = {
            "copernicus-dem-glo90": {
                "status": "verified",
                "source_url": url,
                "tile": url.rsplit("/", 2)[-2],
            }
        }
        context: dict[str, dict[str, object]] = {}
        warnings = list(extraction.warnings)
        try:
            counts = read_worldcover_counts(request.bbox)
            context = _context_observations(counts, self.worldcover_dataset.dataset_id)
            provider_status[self.worldcover_dataset.dataset_id] = {
                "status": "verified",
                "source_url": f"{WORLDCOVER_BUCKET}/{WORLDCOVER_PREFIX}/",
                "tiles": len(counts["tiles"]),
                "valid_pixels": counts["valid_pixels"],
            }
        except AppError as exc:
            provider_status[self.worldcover_dataset.dataset_id] = {
                "status": "failed",
                "reason": exc.code,
                "message": exc.message,
            }
            warnings.append(f"worldcover_{exc.code}")

        quality_flags = [
            "on_demand_aoi",
            "copernicus_dsm_may_include_vegetation_buildings",
            "glo90_native_3_arcsec_bilinear_to_90m",
            "worldcover_context_2021_epoch",
        ]
        datasets = (self.earth_dataset, self.worldcover_dataset)
        return AoiResult(
            area_id=request.area_id,
            bbox=request.bbox,
            geometry=request.geometry,
            area_km2=float(extraction.area_km2 or request.area_km2),
            center_lon=float(extraction.center_lon or 0.0),
            center_lat=float(extraction.center_lat or 0.0),
            crs=extraction.analysis_crs,
            valid_fraction=extraction.valid_fraction,
            window_size_m=extraction.window_size_m,
            resolution_m=round(extraction.resolution_m, 3),
            features=features,
            context_observations=context,
            datasets=datasets,
            provider_status=provider_status,
            quality_flags=tuple(quality_flags),
            warnings=tuple(dict.fromkeys(warnings)),
            checksum=extraction.window_checksum,
        )


__all__ = [
    "EarthAoiProvider",
    "copernicus_tile_url",
    "read_worldcover_counts",
    "worldcover_tile_url",
    "BUILTIN_WORLDCOVER_DATASET",
]
