from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from analog_api.adapters.typed_db import TypedObservationStore
from analog_api.api.routes import get_session
from analog_api.domain.errors import AppError
from analog_api.domain.planetary import GeographicBBox, bbox_contains, bbox_intersects

router = APIRouter(prefix="/api/v1/typed")


@router.get("/coverage")
def typed_coverage(session: Session = Depends(get_session)) -> dict[str, Any]:
    return TypedObservationStore(session).coverage()


def _earth_bbox_from_area(area: dict[str, Any]) -> GeographicBBox | None:
    bbox = area.get("bbox")
    if not bbox or str(area.get("body")) != "Earth":
        return None
    try:
        return GeographicBBox(
            west=float(bbox["west"]),
            south=float(bbox["south"]),
            east=float(bbox["east"]),
            north=float(bbox["north"]),
        )
    except Exception:
        return None


@router.get("/observations")
def list_typed_observations(
    dimension: str | None = None,
    body: str | None = None,
    feature_id: str | None = None,
    area_id: str | None = None,
    state: str | None = Query(
        default=None,
        description="detected|not_detected|unknown|no_coverage|not_applicable",
    ),
    dataset_id: str | None = None,
    source_asset_id: str | None = None,
    min_depth_m: float | None = None,
    max_depth_m: float | None = None,
    temporal_kind: str | None = None,
    west: float | None = None,
    south: float | None = None,
    east: float | None = None,
    north: float | None = None,
    lat: float | None = None,
    lon: float | None = None,
    envelope: bool = Query(
        default=False,
        description="Return an explicit query envelope with status/no_data metadata instead of a plain array.",
    ),
    session: Session = Depends(get_session),
) -> Any:
    store = TypedObservationStore(session)
    bbox_values = [west, south, east, north]
    has_bbox = any(value is not None for value in bbox_values)
    if has_bbox and not all(value is not None for value in bbox_values):
        raise AppError(
            "validation_error",
            "bbox requires west, south, east and north.",
            status_code=422,
        )
    has_point = lat is not None or lon is not None
    if has_point and (lat is None or lon is None):
        raise AppError("validation_error", "lat and lon must both be provided.", status_code=422)

    if feature_id is not None:
        # Invalid feature ids must fail explicitly; they must never be treated as zero/missing evidence.
        exists = store.session.execute(
            __import__("sqlalchemy")
            .select(
                __import__(
                    "analog_api.adapters.typed_db", fromlist=["TypedObservationRow"]
                ).TypedObservationRow.observation_id
            )
            .where(
                __import__(
                    "analog_api.adapters.typed_db", fromlist=["TypedObservationRow"]
                ).TypedObservationRow.feature_id
                == feature_id
            )
            .limit(1)
        ).first()
        if exists is None:
            raise AppError(
                "unknown_feature",
                f"Feature {feature_id} is not present in typed observations/evidence records.",
                status_code=422,
                details=[{"feature_id": feature_id, "reason": "invalid_or_unavailable_feature"}],
            )

    observations = store.list_observations(
        dimension=dimension,
        body=body,
        feature_id=feature_id,
        area_id=area_id,
        state=state,
        dataset_id=dataset_id,
        source_asset_id=source_asset_id,
        min_depth_m=min_depth_m,
        max_depth_m=max_depth_m,
        temporal_kind=temporal_kind,
    )
    query_meta: dict[str, Any] = {
        "dimension": dimension,
        "body": body,
        "feature_id": feature_id,
        "area_id": area_id,
        "state": state,
        "dataset_id": dataset_id,
        "source_asset_id": source_asset_id,
        "min_depth_m": min_depth_m,
        "max_depth_m": max_depth_m,
        "temporal_kind": temporal_kind,
    }
    spatial_requested = has_bbox or has_point
    if spatial_requested:
        query_bbox = (
            GeographicBBox(west=west, south=south, east=east, north=north) if has_bbox else None
        )
        area_ids = {row["area_id"] for row in observations}
        areas = {
            item["area_id"]: item
            for item in store.list_areas(body=body)
            if item["area_id"] in area_ids
        }
        matched_area_ids: set[str] = set()
        unsupported_area_ids: set[str] = set()
        for aid, area in areas.items():
            area_bbox = _earth_bbox_from_area(area)
            if area_bbox is None:
                # Planetary typed areas are stored in projected/body CRS; arbitrary bbox intersection
                # cannot be computed honestly here. Keep exact-id usage; report as unsupported spatial filter.
                if str(area.get("body")) in {"Moon", "Mars"}:
                    unsupported_area_ids.add(aid)
                continue
            if (query_bbox is not None and bbox_intersects(query_bbox, area_bbox)) or (
                lat is not None and lon is not None and bbox_contains(area_bbox, lat, lon)
            ):
                matched_area_ids.add(aid)
        observations = [row for row in observations if row["area_id"] in matched_area_ids]
        query_meta["bbox"] = (
            {"west": west, "south": south, "east": east, "north": north} if has_bbox else None
        )
        query_meta["lat"] = lat
        query_meta["lon"] = lon
        query_meta["unsupported_spatial_area_ids"] = sorted(unsupported_area_ids)
        if unsupported_area_ids:
            query_meta["spatial_note"] = (
                "Planetary projected typed-area bbox/point intersection is not computed here to avoid "
                "false proximity joins; use exact area_id or /coverage/location + /planetary/map-features."
            )

    extended = any(
        value is not None
        for value in (
            area_id,
            state,
            dataset_id,
            source_asset_id,
            min_depth_m,
            max_depth_m,
            temporal_kind,
        )
    )
    if not envelope and not spatial_requested and not extended:
        return observations

    return {
        "contract_version": "typed-observations-query-1.0.0",
        "query": query_meta,
        "count": len(observations),
        "status": "ok" if observations else "no_data",
        "observations": observations,
        "notes": [
            "detected/not_detected/unknown/no_coverage states are preserved as stored; missing data is never zero-filled.",
            "Records are evidence/context or typed observations per feature score_role/legal role; this endpoint does not compute similarity scores.",
            "Invalid feature/state filters must be provided explicitly by the client; absent values are not treated as zero.",
        ],
    }


@router.get("/areas/{area_id}")
def typed_area(area_id: str, session: Session = Depends(get_session)) -> dict[str, Any]:
    store = TypedObservationStore(session)
    area = store.get_area(area_id)
    if area is None:
        raise AppError("not_found", f"Typed area {area_id} was not found.", status_code=404)
    return area
