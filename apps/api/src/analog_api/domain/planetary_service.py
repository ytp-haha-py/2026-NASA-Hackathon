"""Overlay assembly for the planetary map-features API (Issue #117)."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from analog_api.domain.eligibility import build_registry
from analog_api.domain.entities import ReferenceEntity
from analog_api.domain.errors import AppError
from analog_api.domain.planetary import (
    GeographicBBox,
    bbox_geometry,
    bbox_intersects,
    geographic_bbox_from_center,
    point_geometry,
    projected_bbox_to_geographic,
)

DIMENSION_CODES = ("D01", "D02", "D03", "D04", "D05", "D06")


@dataclass(frozen=True, slots=True)
class OverlayResult:
    features: list[dict[str, object]]
    total: int
    unlocatable_reference_count: int
    unlocatable_typed_area_count: int
    bbox: GeographicBBox


def _dimension_status(
    *,
    dimension: str,
    reference_features: int,
    typed_by_dimension: Mapping[str, int],
) -> dict[str, object]:
    if dimension == "D01":
        if reference_features:
            return {
                "status": "present",
                "record_count": reference_features,
                "value_origin": "processed_reference_metric",
                "comparability_gate": "D01 fine reference method and 90 m support",
            }
        typed_d01 = typed_by_dimension.get(dimension, 0)
        if typed_d01:
            return {
                "status": "present_evidence",
                "record_count": typed_d01,
                "value_origin": "stored_typing_record",
                "comparability_gate": (
                    "Stored D01 evidence (for example an on-demand coarse-window analysis). "
                    "Its method and support differ from the fine 90 m reference metric and are "
                    "never substituted for it."
                ),
            }
        return {
            "status": "absent",
            "record_count": 0,
            "value_origin": None,
            "comparability_gate": "No D01 reference metric for this exact id; not zero-filled.",
        }
    count = typed_by_dimension.get(dimension, 0)
    if count:
        return {
            "status": "present_evidence",
            "record_count": count,
            "value_origin": "stored_typing_record",
            "comparability_gate": (
                "Evidence/context unless the versioned feature registry exposes a gated "
                "similarity feature for this exact body/method/depth/support."
            ),
        }
    return {
        "status": "absent",
        "record_count": 0,
        "value_origin": None,
        "comparability_gate": "No stored observation for this exact area and dimension; no zero fill.",
    }


def _reference_feature(
    *,
    reference: ReferenceEntity,
    typed_area: Mapping[str, Any] | None,
    typed_rows: Sequence[Mapping[str, Any]],
    registry: Mapping[str, Any],
) -> tuple[dict[str, object], int]:
    if reference.lat is None or reference.lon is None:
        return {}, 1
    bbox = geographic_bbox_from_center(
        lat=reference.lat,
        lon=reference.lon,
        window_m=reference.window_size_m,
        body=reference.body,
        source="derived_from_reference_center_and_window",
    )
    typed_by_dimension = Counter(
        str(row.get("dimension_code")) for row in typed_rows if row.get("dimension_code")
    )
    statuses = {
        dimension: _dimension_status(
            dimension=dimension,
            reference_features=len(reference.features),
            typed_by_dimension=typed_by_dimension,
        )
        for dimension in DIMENSION_CODES
    }
    typed_dimensions = sorted(typed_by_dimension)
    feature: dict[str, object] = {
        "feature_id": f"reference:{reference.reference_id}",
        "feature_type": "planetary_reference",
        "body": reference.body,
        "display_name": reference.display_name,
        "reference_id": reference.reference_id,
        "typed_area_id": typed_area.get("area_id") if typed_area else None,
        "linked_feature_id": (f"typed:{typed_area['area_id']}" if typed_area else None),
        "relationship": (
            "same_area_id_exact_join" if typed_area else "reference_only_no_proximity_join"
        ),
        "bbox": bbox.as_dict(),
        "center": {"lon": reference.lon, "lat": reference.lat},
        "geometry": point_geometry(reference.lon, reference.lat),
        "bbox_geometry": bbox_geometry(bbox),
        "science_coverage": {
            "d01_feature_count": len(reference.features),
            "d01_method_versions": sorted(
                {observation.method_version for observation in reference.features.values()}
            ),
            "d01_analysis_resolution_m": reference.analysis_resolution_m,
            "d01_window_size_m": reference.window_size_m,
            "typed_dimensions": typed_dimensions,
            "typed_observation_count": len(typed_rows),
            "dimension_status": statuses,
            "coverage_label": "curated_reference_subset_not_global",
        },
        "availability": (
            "fine_reference_and_exact_typed_area" if typed_area else "fine_reference_only"
        ),
        "quality_flags": list(reference.quality_flags),
        "crs": reference.crs,
        "data_version": None,
        "detail_links": {
            "references": "/api/v1/references",
            "typed_area": (f"/api/v1/typed/areas/{typed_area['area_id']}" if typed_area else None),
            "search": "/api/v1/search/multidimensional",
        },
    }
    return feature, 0


def _typed_feature(
    *,
    area: Mapping[str, Any],
    reference: ReferenceEntity | None,
    typed_rows: Sequence[Mapping[str, Any]],
    registry: Mapping[str, Any],
) -> dict[str, object]:
    body = str(area["body"])
    stored_bbox = area.get("bbox") or {}
    bbox = projected_bbox_to_geographic(
        crs=str(area["crs"]),
        bbox={key: float(stored_bbox[key]) for key in ("west", "south", "east", "north")},
        body=body,
    )
    center_lon = (bbox.west + bbox.east) / 2.0
    if bbox.crosses_antimeridian:
        center_lon = (bbox.west + bbox.east + 360.0) / 2.0
    center_lat = (bbox.south + bbox.north) / 2.0
    typed_by_dimension = Counter(
        str(row.get("dimension_code")) for row in typed_rows if row.get("dimension_code")
    )
    statuses = {
        dimension: _dimension_status(
            dimension=dimension,
            reference_features=len(reference.features) if reference else 0,
            typed_by_dimension=typed_by_dimension,
        )
        for dimension in DIMENSION_CODES
    }
    origins: list[str] = []
    for row in typed_rows:
        entry = registry.get(str(row.get("feature_id")))
        if entry is not None:
            origin = entry.source.get("value_origin")
            if origin and origin not in origins:
                origins.append(str(origin))
    return {
        "feature_id": f"typed:{area['area_id']}",
        "feature_type": "typed_area",
        "body": body,
        "display_name": str(area["area_id"]),
        "reference_id": reference.reference_id if reference else None,
        "typed_area_id": area["area_id"],
        "linked_feature_id": (f"reference:{reference.reference_id}" if reference else None),
        "relationship": (
            "same_area_id_exact_join" if reference else "typed_area_only_no_proximity_join"
        ),
        "bbox": bbox.as_dict(),
        "center": {"lon": center_lon, "lat": center_lat},
        "geometry": point_geometry(center_lon, center_lat),
        "bbox_geometry": bbox_geometry(bbox),
        "science_coverage": {
            "d01_feature_count": len(reference.features) if reference else 0,
            "typed_dimensions": sorted(typed_by_dimension),
            "typed_observation_count": len(typed_rows),
            "typed_state_counts": dict(
                sorted(Counter(str(row.get("state")) for row in typed_rows).items())
            ),
            "typed_feature_ids": sorted({str(row.get("feature_id")) for row in typed_rows}),
            "value_origins": origins,
            "dimension_status": statuses,
            "coverage_label": "curated_typed_subset_not_global",
        },
        "availability": ("typed_area_and_exact_reference" if reference else "typed_area_only"),
        "quality_flags": [],
        "crs": area.get("crs"),
        "coordinate_frame": area.get("coordinate_frame"),
        "data_version": None,
        "detail_links": {
            "typed_area": f"/api/v1/typed/areas/{area['area_id']}",
            "references": "/api/v1/references",
            "search": "/api/v1/search/multidimensional",
        },
    }


def overlay_payload(
    *,
    body: str,
    query_bbox: GeographicBBox,
    references: Sequence[ReferenceEntity],
    areas: Sequence[Mapping[str, Any]],
    rows: Sequence[Mapping[str, Any]],
    data_version: str,
    limit: int,
    offset: int,
    feature_type: str,
    dimension: str | None,
) -> OverlayResult:
    registry = build_registry([dict(row) for row in rows])
    rows_by_area: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        rows_by_area.setdefault(str(row.get("area_id")), []).append(dict(row))
    areas_by_id = {str(area["area_id"]): area for area in areas}
    references_by_id = {
        reference.reference_id: reference for reference in references if reference.body == body
    }

    candidates: list[dict[str, object]] = []
    unlocatable = 0
    unlocatable_typed = 0
    if feature_type in ("all", "reference"):
        for reference in references:
            if reference.body != body:
                continue
            typed_area = areas_by_id.get(reference.reference_id)
            feature, missing = _reference_feature(
                reference=reference,
                typed_area=typed_area,
                typed_rows=rows_by_area.get(reference.reference_id, []),
                registry=registry,
            )
            unlocatable += missing
            if not feature:
                continue
            feature_bbox = GeographicBBox(
                float(feature["bbox"]["west"]),
                float(feature["bbox"]["south"]),
                float(feature["bbox"]["east"]),
                float(feature["bbox"]["north"]),
                bool(feature["bbox"]["crosses_antimeridian"]),
            )
            if not bbox_intersects(query_bbox, feature_bbox):
                continue
            if dimension and dimension not in feature["science_coverage"]["dimension_status"]:
                continue
            if (
                dimension
                and feature["science_coverage"]["dimension_status"][dimension]["status"] == "absent"
            ):
                continue
            candidates.append(feature)

    if feature_type in ("all", "typed_area"):
        for area in areas:
            if str(area["body"]) != body:
                continue
            area_id = str(area["area_id"])
            reference = references_by_id.get(area_id)
            try:
                feature = _typed_feature(
                    area=area,
                    reference=reference,
                    typed_rows=rows_by_area.get(area_id, []),
                    registry=registry,
                )
            except (AppError, KeyError, TypeError, ValueError):
                unlocatable_typed += 1
                continue
            feature_bbox = GeographicBBox(
                float(feature["bbox"]["west"]),
                float(feature["bbox"]["south"]),
                float(feature["bbox"]["east"]),
                float(feature["bbox"]["north"]),
                bool(feature["bbox"]["crosses_antimeridian"]),
            )
            if not bbox_intersects(query_bbox, feature_bbox):
                continue
            if (
                dimension
                and feature["science_coverage"]["dimension_status"][dimension]["status"] == "absent"
            ):
                continue
            candidates.append(feature)

    candidates.sort(key=lambda item: (str(item["feature_type"]), str(item["feature_id"])))
    for feature in candidates:
        feature["data_version"] = data_version
    total = len(candidates)
    window = candidates[offset : offset + limit]
    return OverlayResult(
        features=window,
        total=total,
        unlocatable_reference_count=unlocatable,
        unlocatable_typed_area_count=unlocatable_typed,
        bbox=query_bbox,
    )


def science_summary_payload(
    *,
    body: str,
    references: Sequence[ReferenceEntity],
    areas: Sequence[Mapping[str, Any]],
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, object]:
    body_refs = [reference for reference in references if reference.body == body]
    body_areas = [area for area in areas if str(area["body"]) == body]
    area_ids = {str(area["area_id"]) for area in body_areas}
    body_rows = [row for row in rows if str(row.get("area_id")) in area_ids]
    by_dimension = Counter(
        str(row.get("dimension_code")) for row in body_rows if row.get("dimension_code")
    )
    return {
        "body": body,
        "reference_count": len(body_refs),
        "reference_with_d01_feature_count": sum(1 for reference in body_refs if reference.features),
        "typed_area_count": len(body_areas),
        "typed_area_ids": sorted(area_ids),
        "typed_observation_count": len(body_rows),
        "typed_by_dimension": dict(sorted(by_dimension.items())),
        "coverage_label": "curated_subset_not_global",
        "note": (
            "These are the Moon/Mars references and typed areas stored in this database. They "
            "are not a global scientific survey, even though the basemap is globally browsable."
        ),
    }
