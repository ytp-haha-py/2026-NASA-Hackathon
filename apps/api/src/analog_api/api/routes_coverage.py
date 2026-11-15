"""Arbitrary-location D01-D06 coverage/status contract (Issue #141, #160)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from analog_api.adapters.typed_db import TypedObservationStore
from analog_api.api.routes import get_repo, get_session
from analog_api.domain.coverage_status import build_location_coverage
from analog_api.ports.repository import CandidateRepositoryFull

router = APIRouter(prefix="/api/v1/coverage", tags=["coverage"])


def _bbox_from_params(
    west: float | None, south: float | None, east: float | None, north: float | None
) -> dict[str, float] | None:
    if any(value is not None for value in (west, south, east, north)):
        if not all(value is not None for value in (west, south, east, north)):
            from analog_api.domain.errors import AppError

            raise AppError(
                "validation_error",
                "bbox requires west, south, east and north.",
                status_code=422,
            )
        return {
            "west": float(west),
            "south": float(south),
            "east": float(east),
            "north": float(north),
        }  # type: ignore[arg-type]
    return None


@router.get("/location")
def location_coverage(
    body: str = Query(..., description="Earth, Moon or Mars"),
    lat: float | None = Query(default=None),
    lon: float | None = Query(default=None),
    west: float | None = Query(default=None),
    south: float | None = Query(default=None),
    east: float | None = Query(default=None),
    north: float | None = Query(default=None),
    bbox_lon_convention: str = Query(default="signed"),
    include_records: bool = Query(
        default=False,
        description=(
            "Opt in to the AOI observation-record view. Without it the pre-#160 response "
            "shape is preserved (plus additive metadata)."
        ),
    ),
    dimension: str | None = Query(
        default=None,
        description="Comma-separated D01-D06 record filter, e.g. D01,D06.",
    ),
    feature_id: str | None = Query(
        default=None, description="Comma-separated feature ids to include."
    ),
    state: str | None = Query(
        default=None,
        description="Comma-separated stored states, e.g. detected,no_coverage,unknown.",
    ),
    dataset_id: str | None = Query(
        default=None, description="Comma-separated dataset ids; exact membership match."
    ),
    source_asset_id: str | None = Query(
        default=None, description="Comma-separated source asset ids; exact membership match."
    ),
    time_start: str | None = Query(
        default=None,
        description="Inclusive ISO date/datetime lower bound for declared temporal support.",
    ),
    time_end: str | None = Query(
        default=None,
        description="Inclusive ISO date/datetime upper bound for declared temporal support.",
    ),
    depth_min_m: float | None = Query(
        default=None, description="Inclusive minimum depth in metres below the surface."
    ),
    depth_max_m: float | None = Query(
        default=None, description="Inclusive maximum depth in metres below the surface."
    ),
    max_records: int = Query(default=200, ge=1, le=2000),
    session: Session = Depends(get_session),
    repo: CandidateRepositoryFull = Depends(get_repo),
) -> dict[str, Any]:
    """Body-aware D01-D06 coverage/status for a point or bbox AOI.

    The endpoint accepts either ``lat``/``lon`` or ``west``/``south``/``east``/``north``.
    Earth bboxes use EPSG:4326 signed lon/lat degrees; Moon/Mars bboxes use planetocentric
    longitude/latitude in the declared IAU body frame (``bbox_lon_convention=360`` accepts
    a 0..360 client convention). ``west > east`` is an explicit antimeridian crossing.

    Set ``include_records=true`` and optional filters to receive the intersecting typed
    observation records: observation/area ids, dimension/feature, state, value summary,
    unit, dataset/source ids, resolution, temporal/depth support, score/query roles,
    exact-join rankability and explicit missing/no_coverage reasons. Records are never
    copied across AOIs and ``no_data``/``no_coverage``/``unavailable`` are never
    zero-filled. Earth land-cover remains a separate Earth-only context/filter layer, not
    a D01-D06 similarity dimension.
    """
    bbox = _bbox_from_params(west, south, east, north)
    return build_location_coverage(
        repo=repo,
        typed_store=TypedObservationStore(session),
        body=body,
        lat=lat,
        lon=lon,
        bbox=bbox,
        bbox_lon_convention=bbox_lon_convention,
        include_records=include_records,
        dimension=dimension,
        feature_id=feature_id,
        state=state,
        dataset_id=dataset_id,
        source_asset_id=source_asset_id,
        time_start=time_start,
        time_end=time_end,
        depth_min_m=depth_min_m,
        depth_max_m=depth_max_m,
        max_records=max_records,
    )


@router.get("/aoi")
def aoi_query(
    body: str = Query(..., description="Earth, Moon or Mars"),
    lat: float | None = Query(default=None),
    lon: float | None = Query(default=None),
    west: float | None = Query(default=None),
    south: float | None = Query(default=None),
    east: float | None = Query(default=None),
    north: float | None = Query(default=None),
    bbox_lon_convention: str = Query(default="signed"),
    dimension: str | None = Query(
        default=None,
        description="Comma-separated D01-D06 record filter, e.g. D01,D06.",
    ),
    feature_id: str | None = Query(
        default=None, description="Comma-separated feature ids to include."
    ),
    state: str | None = Query(
        default=None,
        description="Comma-separated stored states, e.g. detected,no_coverage,unknown.",
    ),
    dataset_id: str | None = Query(
        default=None, description="Comma-separated dataset ids; exact membership match."
    ),
    source_asset_id: str | None = Query(
        default=None, description="Comma-separated source asset ids; exact membership match."
    ),
    time_start: str | None = Query(
        default=None,
        description="Inclusive ISO date/datetime lower bound for declared temporal support.",
    ),
    time_end: str | None = Query(
        default=None,
        description="Inclusive ISO date/datetime upper bound for declared temporal support.",
    ),
    depth_min_m: float | None = Query(
        default=None, description="Inclusive minimum depth in metres below the surface."
    ),
    depth_max_m: float | None = Query(
        default=None, description="Inclusive maximum depth in metres below the surface."
    ),
    max_records: int = Query(default=200, ge=1, le=2000),
    session: Session = Depends(get_session),
    repo: CandidateRepositoryFull = Depends(get_repo),
) -> dict[str, Any]:
    """Unified AOI-first query (Issue #160): composes coverage/status and typed evidence.

    Primary input is bbox (or point). Returns six-dimension statuses, intersecting records with
    query/score/filter/evidence/context roles, source, resolution, time/depth, and missing reasons.
    Coarse/context records keep their declared spatial support and are never presented as small-AOI
    measurements. Typed joins are exact-ID only; no proximity join to fine Earth candidates.

    Optional ``dimension``/``feature_id``/``state``/``dataset_id``/``source_asset_id``/
    ``time_start``/``time_end``/``depth_min_m``/``depth_max_m`` filters select records; records
    without declared time/depth support are excluded with an explicit reason rather than assumed
    to match. ``no_data``/``no_coverage``/``unavailable`` are never zero-filled.
    """
    bbox = _bbox_from_params(west, south, east, north)
    coverage = build_location_coverage(
        repo=repo,
        typed_store=TypedObservationStore(session),
        body=body,
        lat=lat,
        lon=lon,
        bbox=bbox,
        bbox_lon_convention=bbox_lon_convention,
        include_records=True,
        dimension=dimension,
        feature_id=feature_id,
        state=state,
        dataset_id=dataset_id,
        source_asset_id=source_asset_id,
        time_start=time_start,
        time_end=time_end,
        depth_min_m=depth_min_m,
        depth_max_m=depth_max_m,
        max_records=max_records,
    )
    intersecting_records = list(coverage.get("records") or [])
    missing_reasons = {
        dim_code: {
            "status": dim.get("status"),
            "reason": (
                "no_intersecting_indexed_records"
                if not dim.get("intersecting_record_count")
                else "see_feature_states"
            ),
            "declared_source_count": len(dim.get("declared_sources") or []),
        }
        for dim_code, dim in coverage.get("dimensions", {}).items()
    }
    map_features: dict[str, Any] | None = None
    if body in {"Moon", "Mars"}:
        try:
            from analog_api.domain.planetary import GeographicBBox, get_body
            from analog_api.domain.planetary_service import overlay_payload, science_summary_payload

            info = get_body(body)
            normalized_bbox = (coverage.get("query") or {}).get("bbox")
            if normalized_bbox:
                query_bbox = GeographicBBox(
                    west=float(normalized_bbox["west"]),
                    south=float(normalized_bbox["south"]),
                    east=float(normalized_bbox["east"]),
                    north=float(normalized_bbox["north"]),
                    crosses_antimeridian=bool(normalized_bbox.get("crosses_antimeridian")),
                    source="query",
                )
            elif lat is not None and lon is not None:
                # small envelope around point for overlay lookup
                delta = 0.01
                query_bbox = GeographicBBox(
                    west=float(lon) - delta,
                    south=float(lat) - delta,
                    east=float(lon) + delta,
                    north=float(lat) + delta,
                    source="point_envelope",
                )
            else:
                query_bbox = GeographicBBox(-180.0, -90.0, 180.0, 90.0, source="global_default")
            typed = TypedObservationStore(session)
            areas = typed.list_areas(body=info.body)
            rows = typed.list_observations(body=info.body)
            references = repo.list_references()
            overlay = overlay_payload(
                body=info.body,
                query_bbox=query_bbox,
                references=references,
                areas=areas,
                rows=rows,
                data_version=repo.data_version(),
                limit=50,
                offset=0,
                feature_type="all",
                dimension=None,
            )
            map_features = {
                "total": overlay.total,
                "returned": len(overlay.features),
                "features": overlay.features,
                "science_coverage_summary": science_summary_payload(
                    body=info.body, references=references, areas=areas, rows=rows
                ),
            }
        except Exception:
            map_features = {"status": "unavailable", "features": [], "total": 0}

    response: dict[str, Any] = {
        "contract_version": "aoi-query-1.0.0",
        "endpoint": "/api/v1/coverage/aoi",
        "query": coverage.get("query"),
        "actual_indexed_coverage": coverage.get("actual_indexed_coverage"),
        "dimensions": coverage.get("dimensions"),
        "intersecting_records": intersecting_records,
        "records": intersecting_records,
        "evidence_query": coverage.get("evidence_query"),
        "missing_reasons": missing_reasons,
        "context_features": coverage.get("context_features") or [],
        "manifest_adapters": coverage.get("manifest_adapters") or [],
        "limitations": coverage.get("limitations") or [],
        "handoff": {
            "composes": [
                "/api/v1/coverage/location",
                "/api/v1/typed/observations",
                "/api/v1/typed/areas/{area_id}",
                "/api/v1/search/features",
                "/api/v1/earth/map-features",
                "/api/v1/planetary/map-features",
                "/api/v1/areas/analyze",
                "/api/v1/planetary/areas/analyze",
            ],
            "join_policy": "exact area_id/reference_id only; no proximity join to indexed Earth candidates",
            "rankability": "join.rankable/viewable_only distinguishes exact-joined query records from viewable evidence",
            "record_filters": [
                "dimension",
                "feature_id",
                "state",
                "dataset_id",
                "source_asset_id",
                "time_start",
                "time_end",
                "depth_min_m",
                "depth_max_m",
            ],
            "coarse_policy": "coarse or context-only records must keep declared spatial support and must not be presented as small-AOI measurements",
        },
    }
    if map_features is not None:
        response["map_features"] = map_features
    return response
