"""Versioned arbitrary-location D01-D06 availability/status contract (Issue #141).

This module answers: for a queried body and location/AOI, what is the actual indexed/queryable
status of each dimension/feature in this database and declared source inventory?

Statuses are explicit and never fabricated:
- available: a gated/queryable record exists for the location/support in this database
- modeled: a model-derived value exists (not a direct observation)
- derived: a derived/indexed value exists from verified processing
- evidence: evidence/context exists but is not a legal scored similarity input for this query
- no_coverage: explicitly known lack of coverage for the queried support (where represented)
- unsupported: dimension/feature/body/time/depth/query role is not supported
- not_indexed: no indexed/queryable record intersects the queried location in this database

Missing values remain missing; states do not imply scientific equivalence.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from analog_api.domain.eligibility import REGISTRY_VERSION, build_registry
from analog_api.domain.errors import AppError
from analog_api.domain.features import CONTEXT_FEATURE_BY_ID, FEATURE_BY_ID
from analog_api.domain.planetary import GeographicBBox, bbox_contains, bbox_intersects, get_body

CONTRACT_VERSION = "location-coverage-status-1.0.0"
DIMENSION_CODES = ("D01", "D02", "D03", "D04", "D05", "D06")
DIMENSION_LABELS = {
    "D01": "Terrain & Landforms",
    "D02": "Geology & Mineralogy",
    "D03": "Thermal Environment",
    "D04": "Water & Ice",
    "D05": "Solar Illumination & Radiation",
    "D06": "Regolith & Surface Properties",
}


@dataclass(frozen=True, slots=True)
class QueryLocation:
    body: str
    lat: float | None
    lon: float | None
    bbox: GeographicBBox | None


def _root() -> Path:
    # apps/api/src/analog_api/domain/coverage_status.py -> repo root
    return Path(__file__).resolve().parents[5]


def load_source_inventory() -> dict[str, Any]:
    path = _root() / "catalog" / "dimension-source-inventory.yaml"
    if not path.exists():
        return {"dimensions": [], "note": "catalog/dimension-source-inventory.yaml not found"}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def load_dimension_manifests() -> list[dict[str, Any]]:
    """Adapter view over #135-#140 and related dimension manifests/releases.

    This is intentionally read-only and manifest-driven (no per-dimension schema fork): it
    normalizes provenance/coverage metadata from typed bundles, acquisition releases and pilots.
    """
    root = _root()
    manifests: list[dict[str, Any]] = []
    candidates = [
        root / "pipelines/geology/typed/manifest.json",
        root / "pipelines/pilots/d02_spectral_detection/manifest.json",
        root / "pipelines/pilots/d03_planetary_thermal/manifest.json",
        root / "pipelines/pilots/d03_earth_power/manifest.json",
        root / "pipelines/pilots/d04_earth_power/manifest.json",
        root / "pipelines/pilots/d04_water_ice_planetary/manifest.json",
        root / "pipelines/pilots/d05_earth_power/manifest.json",
        root / "pipelines/pilots/d05_spice_illumination/manifest.json",
        root / "pipelines/pilots/d06_earth_soilgrids/manifest.json",
        root / "pipelines/pilots/d06_earth_soilgrids_all_0_5cm/manifest.json",
        root / "pipelines/pilots/d06_earth_soilgrids_depths/manifest.json",
        root / "pipelines/pilots/d06_earth_soilgrids_expansion_01/manifest.json",
    ]
    for path in candidates:
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        manifests.append(
            {
                "path": str(path.relative_to(root)),
                "manifest": payload,
                "kind": _manifest_kind(path, payload),
            }
        )
    return manifests


def _manifest_kind(path: Path, payload: dict[str, Any]) -> str:
    if (path.parent / "observations.jsonl").exists() and payload.get("release_version"):
        return "typed_acquisition_release"
    if (path.parent / "observations.jsonl").exists() and payload.get("export_version"):
        return "typed_export_bundle"
    if payload.get("schema_version") == "spectral-detection-pilot-1.0.0":
        return "spectral_detection_pilot"
    return "manifest"


def _earth_bbox_from_area(area: dict[str, Any]) -> GeographicBBox | None:
    bbox = area.get("bbox")
    if not bbox:
        return None
    try:
        if str(area.get("body")) == "Earth":
            # Legacy Earth candidates/typed Earth areas are stored in EPSG:4326 lon/lat degrees.
            west, south, east, north = (
                float(bbox["west"]),
                float(bbox["south"]),
                float(bbox["east"]),
                float(bbox["north"]),
            )
            return GeographicBBox(west=west, south=south, east=east, north=north)
        # Planetary typed areas: bbox is in body CRS/projected units; without reprojection we
        # cannot honestly test arbitrary lon/lat intersection except exact area id joins.
        return None
    except Exception:
        return None


def _location_intersects_area(query: QueryLocation, area: dict[str, Any]) -> bool:
    if str(area.get("body")) != query.body:
        return False
    bbox = _earth_bbox_from_area(area)
    if bbox is None:
        return False
    if query.bbox is not None:
        return bbox_intersects(query.bbox, bbox)
    if query.lat is not None and query.lon is not None:
        return bbox_contains(bbox, query.lat, query.lon)
    return False


def _location_intersects_candidate(query: QueryLocation, candidate: Any) -> bool:
    if query.body != "Earth":
        return False
    bbox = GeographicBBox(
        west=float(candidate.bbox.west),
        south=float(candidate.bbox.south),
        east=float(candidate.bbox.east),
        north=float(candidate.bbox.north),
    )
    if query.bbox is not None:
        return bbox_intersects(query.bbox, bbox)
    if query.lat is not None and query.lon is not None:
        return bbox_contains(bbox, query.lat, query.lon)
    return False


def _location_matches_reference(query: QueryLocation, reference: Any) -> bool:
    if query.body != reference.body:
        return False
    if reference.lat is None or reference.lon is None:
        return False
    # For point queries, treat reference center containment within a small tolerance only when
    # query bbox contains it; exact point equality is not assumed.
    if query.bbox is not None:
        return bbox_contains(query.bbox, float(reference.lat), float(reference.lon))
    if query.lat is not None and query.lon is not None:
        # Approximate: within 0.01 degrees for planetary centric coords is a coarse locator hint,
        # but we report it as reference_match (not area coverage) to avoid false precision.
        return (
            abs(float(reference.lat) - query.lat) <= 0.01
            and abs(float(reference.lon) - query.lon) <= 0.01
        )
    return False


def _query_from_input(
    *,
    body: str,
    lat: float | None,
    lon: float | None,
    bbox: dict[str, float] | None,
    bbox_lon_convention: str,
) -> QueryLocation:
    if body not in ("Earth", "Moon", "Mars"):
        raise AppError("invalid_body", "body must be Earth, Moon or Mars.", status_code=422)
    has_point = lat is not None or lon is not None
    has_bbox = bbox is not None
    if has_point == has_bbox:
        raise AppError(
            "validation_error",
            "Provide either lat/lon or bbox, but not both.",
            status_code=422,
        )
    if has_point:
        if lat is None or lon is None:
            raise AppError(
                "validation_error", "lat and lon must both be provided.", status_code=422
            )
        if not (-90.0 <= lat <= 90.0):
            raise AppError("invalid_latitude", "lat must be within [-90, 90].", status_code=422)
        if body in ("Moon", "Mars"):
            get_body(body)
            lon = (lon + 180.0) % 360.0 - 180.0 if bbox_lon_convention == "signed" else lon
        return QueryLocation(body=body, lat=float(lat), lon=float(lon), bbox=None)
    assert bbox is not None
    try:
        west, south, east, north = (
            float(bbox["west"]),
            float(bbox["south"]),
            float(bbox["east"]),
            float(bbox["north"]),
        )
    except Exception as exc:
        raise AppError(
            "invalid_bbox", "bbox must include west,south,east,north.", status_code=422
        ) from exc
    if body in ("Moon", "Mars"):
        get_body(body)
        if bbox_lon_convention == "360":
            west = (west + 180.0) % 360.0 - 180.0
            east = (east + 180.0) % 360.0 - 180.0
    gb = GeographicBBox(
        west=west,
        south=south,
        east=east,
        north=north,
        crosses_antimeridian=west > east,
        source="query",
    )
    return QueryLocation(body=body, lat=None, lon=None, bbox=gb)


def _inventory_index(inventory: dict[str, Any]) -> dict[tuple[str, str], list[dict[str, Any]]]:
    index: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for dimension in inventory.get("dimensions", []):
        code = str(dimension.get("code") or "")
        bodies = dimension.get("bodies") or {}
        for body, body_info in bodies.items():
            for source in body_info.get("sources") or []:
                index[(body, code)].append(source)
    return index


def build_location_coverage(
    *,
    repo: Any,
    typed_store: Any,
    body: str,
    lat: float | None = None,
    lon: float | None = None,
    bbox: dict[str, float] | None = None,
    bbox_lon_convention: str = "signed",
) -> dict[str, Any]:
    query = _query_from_input(
        body=body, lat=lat, lon=lon, bbox=bbox, bbox_lon_convention=bbox_lon_convention
    )

    candidates = list(repo.list_candidates()) if body == "Earth" else []
    references = [ref for ref in repo.list_references() if ref.body == body]
    areas = typed_store.list_areas(body=body)
    rows = typed_store.list_observations(body=body)
    registry = build_registry(rows)

    intersecting_candidates = [c for c in candidates if _location_intersects_candidate(query, c)]
    intersecting_references = [r for r in references if _location_matches_reference(query, r)]
    intersecting_areas = [a for a in areas if _location_intersects_area(query, a)]
    intersecting_area_ids = {str(a["area_id"]) for a in intersecting_areas}
    # Planetary/Moon-Mars typed areas are often only joinable by exact area/reference id;
    # include exact-id matches to references/candidates as location-relevant records.
    ref_ids = {r.reference_id for r in references}
    cand_ids = {c.location_id for c in candidates}
    for area in areas:
        area_id = str(area.get("area_id"))
        if (area_id in ref_ids or area_id in cand_ids) and (
            query.bbox is None and query.lat is not None and query.lon is not None
        ):
            # exact linked record; treat as query-relevant for id-based planetary lookups only when
            # a point query is near the linked reference center.
            ref = next((r for r in references if r.reference_id == area_id), None)
            if ref and _location_matches_reference(query, ref):
                intersecting_area_ids.add(area_id)
    location_rows = [row for row in rows if str(row.get("area_id")) in intersecting_area_ids]

    inventory = load_source_inventory()
    inv_index = _inventory_index(inventory)
    manifests = load_dimension_manifests()

    dimensions_out: dict[str, Any] = {}
    for dim in DIMENSION_CODES:
        declared_sources = inv_index.get((body, dim), [])
        dim_rows = [row for row in location_rows if row.get("dimension_code") == dim]
        states = sorted({str(row.get("state")) for row in dim_rows})
        features_out: list[dict[str, Any]] = []
        for row in sorted(
            dim_rows, key=lambda r: (str(r.get("feature_id")), str(r.get("observation_id")))
        ):
            feature_id = str(row.get("feature_id"))
            entry = registry.get(feature_id)
            if row.get("state") == "detected":
                status = "available" if (entry and entry.similarity_supported) else "evidence"
            elif row.get("state") == "no_coverage":
                status = "no_coverage"
            elif row.get("state") in {"unknown", "not_applicable", "not_detected"}:
                status = "evidence" if entry else row.get("state")
            else:
                status = "evidence"
            if str(row.get("value_kind")) in {"timeseries"} and row.get("state") == "detected":
                status = "evidence"
            features_out.append(
                {
                    "feature_id": feature_id,
                    "dimension_code": dim,
                    "status": status,
                    "state": row.get("state"),
                    "value_kind": row.get("value_kind"),
                    "score_role": row.get("score_role"),
                    "legal_roles": (
                        [role for role, s in entry.roles.items() if s.supported] if entry else []
                    ),
                    "unit": row.get("unit"),
                    "method_version": row.get("method_version"),
                    "dataset_ids": row.get("dataset_ids") or [],
                    "source_asset_ids": row.get("source_asset_ids") or [],
                    "quality_flags": row.get("quality_flags") or [],
                    "spatial_support": row.get("spatial_support"),
                    "temporal_support": row.get("temporal_support"),
                    "depth_support": row.get("depth_support"),
                    "uncertainty": row.get("uncertainty"),
                    "notes": row.get("notes"),
                    "observation_id": row.get("observation_id"),
                    "area_id": row.get("area_id"),
                }
            )

        # D01 legacy terrain presence for Earth indexed candidates intersecting location.
        d01_legacy: list[dict[str, Any]] = []
        if dim == "D01":
            for candidate in intersecting_candidates:
                for feature_id, obs in candidate.features.items():
                    definition = FEATURE_BY_ID.get(feature_id)
                    d01_legacy.append(
                        {
                            "feature_id": feature_id,
                            "dimension_code": "D01",
                            "status": "derived",
                            "state": "detected",
                            "value_kind": definition.value_kind if definition else "numeric",
                            "score_role": definition.score_role if definition else "similarity",
                            "legal_roles": ["similarity", "hard_filter", "evidence"],
                            "unit": obs.unit,
                            "method_version": obs.method_version,
                            "dataset_ids": [candidate.dataset_id],
                            "quality_flags": list(candidate.quality_flags),
                            "spatial_support": {
                                "kind": "window",
                                "bbox": candidate.bbox.as_dict(),
                                "window_size_m": candidate.window_size_m,
                                "analysis_resolution_m": candidate.analysis_resolution_m,
                                "valid_fraction": obs.valid_fraction,
                                "crs": candidate.crs,
                            },
                            "temporal_support": {"kind": "static"},
                            "depth_support": {"kind": "surface"},
                            "notes": None,
                            "observation_id": f"legacy:{candidate.location_id}:{feature_id}",
                            "area_id": candidate.location_id,
                            "origin": candidate.origin,
                        }
                    )
            for ref in intersecting_references:
                for feature_id, obs in ref.features.items():
                    definition = FEATURE_BY_ID.get(feature_id)
                    d01_legacy.append(
                        {
                            "feature_id": feature_id,
                            "dimension_code": "D01",
                            "status": "derived",
                            "state": "detected",
                            "value_kind": definition.value_kind if definition else "numeric",
                            "score_role": definition.score_role if definition else "similarity",
                            "legal_roles": ["similarity", "hard_filter", "evidence"],
                            "unit": obs.unit,
                            "method_version": obs.method_version,
                            "dataset_ids": [ref.dataset_id] if ref.dataset_id else [],
                            "quality_flags": list(ref.quality_flags),
                            "spatial_support": {
                                "kind": "reference_window",
                                "window_size_m": ref.window_size_m,
                                "analysis_resolution_m": ref.analysis_resolution_m,
                                "valid_fraction": ref.valid_fraction,
                                "crs": ref.crs,
                            },
                            "temporal_support": {"kind": "static"},
                            "depth_support": {"kind": "surface"},
                            "notes": ref.notes,
                            "observation_id": f"reference:{ref.reference_id}:{feature_id}",
                            "area_id": ref.reference_id,
                            "origin": "planetary_reference",
                        }
                    )

        all_features = d01_legacy + features_out
        if all_features:
            dim_status = "partially_available"
            if any(f["status"] in {"available", "derived", "modeled"} for f in all_features):
                dim_status = "available_or_indexed"
            elif any(f["status"] == "no_coverage" for f in all_features):
                dim_status = "explicit_no_coverage"
            else:
                dim_status = "evidence_only"
        else:
            # No intersecting records: distinguish declared sources vs unknown in this DB.
            if declared_sources:
                statuses = {s.get("data_status") for s in declared_sources}
                if statuses <= {"not_fetched", "planned", "unknown"}:
                    dim_status = "not_indexed"
                elif any(s.get("availability") == "verified" for s in declared_sources):
                    dim_status = "not_indexed_at_location"
                else:
                    dim_status = "not_indexed"
            else:
                dim_status = "unsupported"

        dimensions_out[dim] = {
            "dimension_code": dim,
            "label": DIMENSION_LABELS[dim],
            "status": dim_status,
            "intersecting_record_count": len(all_features),
            "states": states,
            "features": all_features,
            "declared_sources": [
                {
                    "source_id": s.get("source_id"),
                    "provider": s.get("provider"),
                    "product": s.get("product"),
                    "availability": s.get("availability"),
                    "data_status": s.get("data_status"),
                    "comparability": s.get("comparability"),
                    "license": s.get("license"),
                    "version": s.get("version", "unknown"),
                    "native_resolution": s.get("native_resolution"),
                    "spatial_support": s.get("spatial_support"),
                    "temporal_support": s.get("temporal_support"),
                    "depth_support": s.get("depth_support"),
                    "qa": s.get("qa"),
                    "url": s.get("url"),
                    "notes": s.get("notes"),
                    "unknown_fields": s.get("unknown_fields") or [],
                }
                for s in declared_sources
            ],
        }

    # context features (Earth land cover) as a separate non-D01-D06 context layer
    context_hits = []
    if body == "Earth":
        for candidate in intersecting_candidates:
            for fid, obs in candidate.context_observations.items():
                definition = CONTEXT_FEATURE_BY_ID.get(fid)
                context_hits.append(
                    {
                        "feature_id": fid,
                        "label": definition.label if definition else fid,
                        "status": "derived",
                        "score_role": "context_only",
                        "value": obs.value,
                        "unit": obs.unit,
                        "method_version": obs.method_version,
                        "dataset_id": obs.dataset_id,
                        "provider": obs.provider,
                        "quality": obs.quality,
                        "valid_fraction": obs.valid_fraction,
                        "spatial_support": {
                            "kind": "aoi_or_window",
                            "source_resolution_m": obs.source_resolution_m,
                        },
                    }
                )

    return {
        "contract_version": CONTRACT_VERSION,
        "registry_version": REGISTRY_VERSION,
        "data_version": repo.data_version(),
        "query": {
            "body": body,
            "lat": query.lat,
            "lon": query.lon,
            "bbox": query.bbox.as_dict() if query.bbox else None,
            "coordinate_note": (
                "Earth uses EPSG:4326 lon/lat degrees. Moon/Mars use body planetocentric "
                "lon/lat coordinates; arbitrary-point intersection for planetary projected typed "
                "areas is only reported when an exact linked reference/area match is identified."
            ),
        },
        "actual_indexed_coverage": {
            "indexed_earth_candidate_count": len(candidates),
            "intersecting_candidate_ids": [c.location_id for c in intersecting_candidates],
            "intersecting_reference_ids": [r.reference_id for r in intersecting_references],
            "intersecting_typed_area_ids": sorted(intersecting_area_ids),
            "typed_observation_count_at_location": len(location_rows),
            "global_coverage_claimed": False,
        },
        "dimensions": dimensions_out,
        "context_features": context_hits,
        "manifest_adapters": manifests,
        "limitations": [
            "This response reports actual indexed/queryable records in this database plus declared "
            "source inventory; it is not a global survey and does not fabricate missing values.",
            "no_coverage/unsupported/not_indexed are explicit states and are never converted to zero.",
            "Planetary projected typed-area point intersection is limited; exact ID-linked records "
            "and reference matches are reported where identifiable.",
            "Land-cover context is Earth-only context/filter data, never mineralogy/geology and never "
            "a similarity dimension.",
        ],
    }
