from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from typing import Any, Protocol

from pyproj import Geod

from analog_api.domain.entities import BBox, CandidateEntity, DatasetEntity
from analog_api.domain.errors import AppError
from analog_api.domain.features import (
    ANALYSIS_RESOLUTION_M,
    AOI_MAX_AREA_KM2,
    AOI_MAX_SIDE_M,
    AOI_MAX_VERTICES,
    AOI_MIN_SIDE_M,
    AOI_PROCESSING_VERSION,
    CONTEXT_METHOD_VERSION,
    FEATURE_BY_ID,
    METHOD_VERSION,
    MIN_VALID_FRACTION,
)

GEOD = Geod(ellps="WGS84")


@dataclass(frozen=True, slots=True)
class ValidatedAoi:
    body: str
    bbox: BBox
    geometry: dict[str, Any] | None
    area_km2: float
    requested_features: tuple[str, ...]
    reference_id: str | None
    idempotency_key: str | None
    area_id: str
    cache_key: str
    width_m: float
    height_m: float


@dataclass(frozen=True, slots=True)
class AoiResult:
    area_id: str
    bbox: BBox
    geometry: dict[str, Any] | None
    area_km2: float
    center_lon: float
    center_lat: float
    crs: str
    valid_fraction: float
    window_size_m: float
    resolution_m: float
    features: dict[str, float]
    context_observations: dict[str, dict[str, Any]]
    datasets: tuple[DatasetEntity, ...]
    provider_status: dict[str, dict[str, Any]]
    quality_flags: tuple[str, ...]
    warnings: tuple[str, ...]
    checksum: str
    origin: str = "on_demand"

    def as_candidate(self) -> CandidateEntity:
        from analog_api.domain.entities import ContextValue, FeatureValue

        return CandidateEntity(
            location_id=self.area_id,
            display_name=f"On-demand Earth AOI {self.area_id.removeprefix('earth-aoi-')}",
            lat=self.center_lat,
            lon=self.center_lon,
            bbox=self.bbox,
            dataset_id=self.datasets[0].dataset_id,
            window_size_m=self.window_size_m,
            analysis_resolution_m=self.resolution_m,
            crs=self.crs,
            valid_fraction=self.valid_fraction,
            quality_flags=self.quality_flags,
            notes=(
                "User-requested on-demand AOI; "
                + (", ".join(self.warnings) if self.warnings else "no source warnings")
            ),
            data_kind="observed",
            area_km2=self.area_km2,
            geometry=self.geometry,
            origin="on_demand",
            features={
                feature_id: FeatureValue(
                    value=value,
                    unit="degree" if feature_id.startswith("slope_") else "meter",
                    method_version=METHOD_VERSION,
                    extraction_scale_m=self.resolution_m,
                    valid_fraction=self.valid_fraction,
                    quality="verified",
                )
                for feature_id, value in self.features.items()
            },
            context_observations={
                feature_id: ContextValue(
                    value=observation["value"],
                    unit=observation["unit"],
                    method_version=observation["method_version"],
                    source_resolution_m=observation["source_resolution_m"],
                    valid_fraction=observation["valid_fraction"],
                    quality=observation["quality"],
                    class_code=observation["class_code"],
                    provider=observation["provider"],
                    dataset_id=observation.get("dataset_id"),
                )
                for feature_id, observation in self.context_observations.items()
            },
        )


class AoiProcessor(Protocol):
    def analyze(self, request: ValidatedAoi) -> AoiResult: ...


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _validate_polygon(geometry: dict[str, Any]) -> tuple[BBox, float]:
    if geometry.get("type") != "Polygon":
        raise AppError(
            "invalid_geometry",
            "Only GeoJSON Polygon geometry is supported in the MVP.",
            status_code=422,
        )
    coordinates = geometry.get("coordinates")
    if not isinstance(coordinates, list) or not coordinates:
        raise AppError("invalid_geometry", "Polygon coordinates are missing.", status_code=422)
    if len(coordinates) > 1:
        raise AppError(
            "invalid_geometry",
            "Polygon holes are not supported in the MVP; supply the outer ring only.",
            status_code=422,
        )
    ring = coordinates[0]
    if not isinstance(ring, list) or len(ring) < 4 or len(ring) > AOI_MAX_VERTICES:
        raise AppError(
            "invalid_geometry",
            f"Polygon outer ring must have 4..{AOI_MAX_VERTICES} positions.",
            status_code=422,
        )
    points = []
    for position in ring:
        if not isinstance(position, list) or len(position) < 2:
            raise AppError("invalid_geometry", "Malformed polygon position.", status_code=422)
        lon, lat = float(position[0]), float(position[1])
        if not (math.isfinite(lon) and math.isfinite(lat)):
            raise AppError(
                "invalid_geometry", "Polygon has non-finite coordinates.", status_code=422
            )
        points.append((lon, lat))
    if points[0] != points[-1]:
        raise AppError(
            "invalid_geometry",
            "GeoJSON polygon outer ring must be closed (first position equals last).",
            status_code=422,
        )
    lons = [point[0] for point in points]
    lats = [point[1] for point in points]
    bbox = BBox(west=min(lons), south=min(lats), east=max(lons), north=max(lats))
    area_km2 = abs(GEOD.polygon_area_perimeter(lons, lats)[0]) / 1e6
    return bbox, area_km2


def _side_lengths(bbox: BBox) -> tuple[float, float]:
    lon_c = (bbox.west + bbox.east) / 2.0
    lat_c = (bbox.south + bbox.north) / 2.0
    width = abs(GEOD.inv(bbox.west, lat_c, bbox.east, lat_c)[2])
    height = abs(GEOD.inv(lon_c, bbox.south, lon_c, bbox.north)[2])
    return width, height


def validate_aoi_request(
    *,
    bbox: dict[str, float] | None,
    geometry: dict[str, Any] | None,
    requested_features: list[str] | None,
    reference_id: str | None = None,
    idempotency_key: str | None = None,
) -> ValidatedAoi:
    if (bbox is None) == (geometry is None):
        raise AppError(
            "invalid_aoi",
            "Provide exactly one of bbox or geometry.",
            status_code=422,
        )
    if geometry is not None:
        bbox_obj, polygon_area = _validate_polygon(geometry)
    else:
        assert bbox is not None
        values = [float(bbox[key]) for key in ("west", "south", "east", "north")]
        if not all(math.isfinite(value) for value in values):
            raise AppError("invalid_aoi", "bbox values must be finite.", status_code=422)
        bbox_obj = BBox(west=values[0], south=values[1], east=values[2], north=values[3])
        polygon_area = (
            abs(
                GEOD.polygon_area_perimeter(
                    [bbox_obj.west, bbox_obj.east, bbox_obj.east, bbox_obj.west],
                    [bbox_obj.south, bbox_obj.south, bbox_obj.north, bbox_obj.north],
                )[0]
            )
            / 1e6
        )

    if not (-180 <= bbox_obj.west < bbox_obj.east <= 180):
        raise AppError(
            "invalid_aoi",
            "bbox must satisfy -180 <= west < east <= 180; antimeridian-crossing AOIs are "
            "not supported in the MVP.",
            status_code=422,
        )
    if not (-90 <= bbox_obj.south < bbox_obj.north <= 90):
        raise AppError(
            "invalid_aoi", "bbox must satisfy -90 <= south < north <= 90.", status_code=422
        )
    if bbox_obj.south < -80 or bbox_obj.north > 84:
        raise AppError(
            "out_of_provider_coverage",
            "Copernicus DEM on-demand extraction currently supports latitudes -80..84.",
            status_code=422,
        )
    if bbox_obj.east - bbox_obj.west > 180:
        raise AppError("invalid_aoi", "AOI is implausibly wide.", status_code=422)

    width_m, height_m = _side_lengths(bbox_obj)
    if min(width_m, height_m) < AOI_MIN_SIDE_M:
        raise AppError(
            "aoi_too_small",
            f"AOI sides must be at least {AOI_MIN_SIDE_M:.0f} m; got "
            f"{min(width_m, height_m):.0f} m.",
            status_code=422,
        )
    if max(width_m, height_m) > AOI_MAX_SIDE_M:
        raise AppError(
            "aoi_too_large",
            f"AOI sides must be at most {AOI_MAX_SIDE_M:.0f} m; got "
            f"{max(width_m, height_m):.0f} m.",
            status_code=422,
        )
    if polygon_area > AOI_MAX_AREA_KM2:
        raise AppError(
            "aoi_too_large",
            f"AOI area must be at most {AOI_MAX_AREA_KM2:.1f} km2; got {polygon_area:.2f} km2.",
            status_code=422,
        )

    features = tuple(requested_features or list(FEATURE_BY_ID))
    unknown = [feature for feature in features if feature not in FEATURE_BY_ID]
    if unknown:
        raise AppError(
            "unknown_feature",
            f"Unsupported feature id(s): {', '.join(unknown)}. Only similarity features can "
            "be requested for analysis.",
            status_code=422,
        )
    if not features:
        raise AppError("empty_features", "At least one feature is required.", status_code=422)

    normalized_bbox = {
        "west": round(bbox_obj.west, 6),
        "south": round(bbox_obj.south, 6),
        "east": round(bbox_obj.east, 6),
        "north": round(bbox_obj.north, 6),
    }
    canonical_geometry = geometry
    if canonical_geometry is not None:
        canonical_geometry = {
            "type": "Polygon",
            "coordinates": [
                [[round(float(x), 6), round(float(y), 6)] for x, y in ring]
                for ring in geometry["coordinates"]
            ],
        }
    aoi_descriptor = {
        "bbox": normalized_bbox,
        "geometry": canonical_geometry,
        "method_version": METHOD_VERSION,
        "resolution_m": ANALYSIS_RESOLUTION_M,
    }
    digest = hashlib.sha256(_canonical(aoi_descriptor).encode()).hexdigest()[:12]
    area_id = f"earth-aoi-{digest}"

    cache_descriptor = {
        "body": "Earth",
        "aoi": aoi_descriptor,
        "requested_features": sorted(features),
        "min_valid_fraction": MIN_VALID_FRACTION,
        "context_method_version": CONTEXT_METHOD_VERSION,
        "aoi_processing_version": AOI_PROCESSING_VERSION,
        "provider_products": [
            "COP-DEM_GLO-90-DGED",
            "ESA WorldCover 2021 v200",
        ],
    }
    cache_key = hashlib.sha256(_canonical(cache_descriptor).encode()).hexdigest()

    return ValidatedAoi(
        body="Earth",
        bbox=bbox_obj,
        geometry=canonical_geometry,
        area_km2=round(polygon_area, 6),
        requested_features=features,
        reference_id=reference_id,
        idempotency_key=idempotency_key,
        area_id=area_id,
        cache_key=cache_key,
        width_m=width_m,
        height_m=height_m,
    )


def job_status_payload(job) -> dict[str, object]:
    return {
        "job_id": job.job_id,
        "status": job.status,
        "area_id": job.area_id,
        "provider_status": job.provider_status or {},
        "error": (
            {"code": job.error_code, "message": job.error_message} if job.error_code else None
        ),
        "result": job.result_json,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
    }


__all__ = [
    "AoiProcessor",
    "AoiResult",
    "ValidatedAoi",
    "job_status_payload",
    "validate_aoi_request",
    "field",
]
