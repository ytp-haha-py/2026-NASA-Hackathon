"""API adapter for the planetary on-demand AOI engine (Issue #132).

The science implementation lives in ``analog_pipelines.planetary_aoi`` and stays
storage-independent. This adapter validates API input, maps the result into the
shared job/AOI result type and never treats planetary lon/lat as Earth WGS84.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from analog_pipelines.coarse_features import COARSE_FEATURE_BY_ID
from analog_pipelines.planetary_aoi import (
    PLANETARY_AOI_METHOD_VERSION,
    PlanetaryAoiError,
    analyze_planetary_aoi,
    validate_planetary_aoi,
)

from analog_api.config import resolve_repo_path
from analog_api.domain.aoi import AoiResult, ValidatedAoi
from analog_api.domain.entities import BBox, DatasetEntity
from analog_api.domain.errors import AppError

SUPPORTED_PLANETARY_BODIES = ("Moon", "Mars")
DEFAULT_FEATURES = tuple(COARSE_FEATURE_BY_ID)
BODY_FRAME = {
    "Moon": ("lunar_planetocentric", "+proj=longlat +R=1737400.0 +no_defs"),
    "Mars": ("mars_planetocentric", "+proj=longlat +R=3396190.0 +no_defs"),
}


def _bbox_values(bbox: Any) -> dict[str, float]:
    if isinstance(bbox, dict):
        values = bbox
    else:
        values = {
            "west": bbox.west,
            "south": bbox.south,
            "east": bbox.east,
            "north": bbox.north,
        }
    try:
        return {key: float(values[key]) for key in ("west", "south", "east", "north")}
    except (KeyError, TypeError, ValueError) as exc:
        raise AppError(
            "invalid_bbox", "bbox must contain west,south,east,north numbers.", status_code=422
        ) from exc


def _source_dataset(result: Any, body: str) -> DatasetEntity:
    dataset = getattr(result, "dataset", None)
    if dataset is None and getattr(result, "datasets", None):
        dataset = result.datasets[0]
    if isinstance(dataset, DatasetEntity):
        return dataset
    if not isinstance(dataset, dict):
        raise AppError(
            "planetary_source_missing",
            "The planetary analysis did not return source provenance; the result is not "
            "persisted because it cannot be attributed.",
            status_code=503,
        )
    return DatasetEntity(
        dataset_id=str(dataset["dataset_id"]),
        provider=str(dataset.get("provider", "unknown")),
        product_id=str(dataset.get("product_id", "unknown")),
        title=dataset.get("title"),
        url=str(dataset.get("url") or dataset.get("source_url") or "unknown"),
        license=str(dataset.get("license", "unknown")),
        license_url=str(dataset.get("license_url") or "unknown"),
        access_timestamp=str(dataset.get("access_timestamp", "2026-11-15T00:00:00Z")),
        source_crs=dataset.get("source_crs"),
        native_resolution_m=dataset.get("native_resolution_m"),
        processing_version=str(dataset.get("processing_version") or PLANETARY_AOI_METHOD_VERSION),
        checksum=dataset.get("checksum"),
        source_kind=str(dataset.get("source_kind", "planetary_dem")),
        notes=dataset.get("notes"),
    )


class PlanetaryAoiProvider:
    """Validate and run a bounded Moon/Mars D01 analysis over verified sources."""

    def __init__(self, *, root: str | Path | None = None, allow_network: bool = False) -> None:
        self.root = (
            Path(root) if root is not None else resolve_repo_path("pipelines/planetary/coarse")
        )
        self.allow_network = allow_network

    def validate(
        self,
        *,
        body: str,
        bbox: dict[str, float],
        requested_features: list[str] | None,
        idempotency_key: str | None,
    ) -> ValidatedAoi:
        if body not in SUPPORTED_PLANETARY_BODIES:
            raise AppError(
                "unsupported_body",
                "Planetary AOI analysis supports body Moon or Mars only.",
                status_code=422,
            )
        features = tuple(requested_features or DEFAULT_FEATURES)
        unknown = sorted(set(features) - set(COARSE_FEATURE_BY_ID))
        if unknown:
            raise AppError(
                "unsupported_feature_on_body",
                "On-demand planetary analysis currently supports only verified D01 coarse "
                f"terrain features ({', '.join(DEFAULT_FEATURES)}); unsupported: "
                f"{', '.join(unknown)}. D02-D06 remain explicit evidence statuses, not fake "
                "measurements.",
                status_code=422,
            )
        try:
            spec = validate_planetary_aoi(body, bbox, root=self.root)
        except PlanetaryAoiError as exc:
            raise AppError(exc.code, exc.message, status_code=_status_for(exc.code)) from exc
        normalized = _bbox_values(getattr(spec, "bbox", getattr(spec, "requested_bbox", bbox)))
        return ValidatedAoi(
            body=body,
            bbox=BBox(**normalized),
            geometry=None,
            area_km2=float(spec.area_km2),
            requested_features=features,
            reference_id=None,
            idempotency_key=idempotency_key,
            area_id=str(spec.area_id),
            cache_key=str(spec.cache_key),
            width_m=float(spec.width_m),
            height_m=float(spec.height_m),
        )

    def analyze(self, request: ValidatedAoi) -> AoiResult:
        bbox = request.bbox.as_dict()
        try:
            result = analyze_planetary_aoi(
                request.body,
                bbox,
                root=self.root,
                allow_remote=self.allow_network,
                requested_features=request.requested_features,
            )
        except TypeError:
            # Older engine signature without the optional feature selector.
            try:
                result = analyze_planetary_aoi(
                    request.body, bbox, root=self.root, allow_remote=self.allow_network
                )
            except PlanetaryAoiError as exc:
                raise AppError(exc.code, exc.message, status_code=_status_for(exc.code)) from exc
        except PlanetaryAoiError as exc:
            raise AppError(exc.code, exc.message, status_code=_status_for(exc.code)) from exc

        payload = result.to_payload() if hasattr(result, "to_payload") else result
        if not isinstance(payload, dict):
            raise AppError(
                "planetary_analysis_invalid",
                "The planetary analysis returned an unsupported payload.",
                status_code=503,
            )
        body = str(payload.get("body") or request.body)
        frame, default_crs = BODY_FRAME[body]
        requested = payload.get("requested_bbox") or bbox
        analysis_bbox = payload.get("analysis_bbox") or requested
        features = {
            feature_id: float(value)
            for feature_id, value in (payload.get("features") or {}).items()
            if feature_id in request.requested_features
        }
        if not features:
            raise AppError(
                "no_supported_features",
                "The selected planetary AOI produced no requested D01 observations.",
                status_code=422,
            )
        datasets = (_source_dataset(result, body),)
        source_assets = tuple(payload.get("source_assets") or ())
        raw_crs = str(payload.get("crs") or "").strip()
        usable_crs = raw_crs.startswith(("+proj", "PROJCRS", "GEOGCRS", "EPSG:"))
        if not usable_crs or raw_crs.upper().startswith("IAU_"):
            raw_crs = default_crs
        warnings = tuple(str(item) for item in payload.get("warnings") or ())
        coverage_note = payload.get("coverage_note")
        if requested != analysis_bbox and coverage_note:
            warnings = (*warnings, str(coverage_note))

        dimension_support = dict(payload.get("dimension_support") or {})
        # Preserve real pixel/validity accounting for full-bbox/tiling evidence.
        cell_results = list(payload.get("cell_results") or [])
        valid_pixels_total = missing_pixels_total = total_pixels = None
        if cell_results:
            valid_pixels_total = sum(
                int(cr.get("valid_slope_pixels") or 0)
                for cr in cell_results
                if isinstance(cr, dict)
            )
            missing_pixels_total = sum(
                int(cr.get("missing_slope_pixels") or 0)
                for cr in cell_results
                if isinstance(cr, dict)
            )
            total_pixels = valid_pixels_total + missing_pixels_total
        pixel_accounting = {
            "valid_fraction": payload.get("valid_fraction"),
            "resolution_m": payload.get("resolution_m"),
            "source_resolution_m": payload.get("source_resolution_m"),
            "window_size_m": payload.get("window_size_m"),
            "cell_count": len(payload.get("cells") or [])
            or (len(cell_results) if cell_results else None),
            "total_pixels": total_pixels,
            "valid_pixels": valid_pixels_total,
            "invalid_or_missing_pixels": missing_pixels_total,
        }
        d01_support = dict(dimension_support.get("D01") or {})
        d01_support.setdefault("pixel_accounting", pixel_accounting)
        if analysis_bbox:
            d01_support.setdefault("analysis_bbox", analysis_bbox)
        if requested:
            d01_support.setdefault("requested_bbox", requested)
        dimension_support["D01"] = d01_support

        return AoiResult(
            area_id=request.area_id,
            bbox=BBox(
                **{key: float(analysis_bbox[key]) for key in ("west", "south", "east", "north")}
            ),
            geometry=payload.get("geometry"),
            area_km2=float(payload.get("area_km2") or request.area_km2),
            center_lon=(float(analysis_bbox["west"]) + float(analysis_bbox["east"])) / 2.0,
            center_lat=(float(analysis_bbox["south"]) + float(analysis_bbox["north"])) / 2.0,
            crs=raw_crs,
            valid_fraction=float(payload.get("valid_fraction") or 0.0),
            window_size_m=float(
                payload.get("window_size_m") or getattr(request, "width_m", 5000.0)
            ),
            resolution_m=float(payload.get("resolution_m") or 250.0),
            features=features,
            context_observations={},
            datasets=datasets,
            provider_status=dict(payload.get("provider_status") or {}),
            quality_flags=tuple(
                str(item)
                for item in (
                    payload.get("quality_flags")
                    or ("on_demand_planetary_aoi", "not_earth_wgs84", "not_a_cross_body_score")
                )
            ),
            warnings=warnings,
            checksum=str(payload.get("checksum") or "sha256:unverified"),
            body=body,
            coordinate_frame=str(payload.get("coordinate_frame") or frame),
            method_version=str(payload.get("method_version") or PLANETARY_AOI_METHOD_VERSION),
            dimension_support=dimension_support,
            source_assets=source_assets,
            coverage_note=str(coverage_note) if coverage_note else None,
            analysis_kind=payload.get("analysis_kind"),
        )


def _status_for(code: str) -> int:
    if code in {"out_of_coverage", "unsupported_body", "invalid_bbox"}:
        return 422
    if code in {"no_coverage_index", "coverage_index_invalid"}:
        return 503
    return 422


__all__ = ["PlanetaryAoiProvider", "SUPPORTED_PLANETARY_BODIES"]
