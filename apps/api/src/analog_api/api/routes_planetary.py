"""Planetary map catalog, coordinate contract and overlay endpoints (Issue #117)."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from analog_api.adapters.typed_db import TypedObservationStore
from analog_api.api.routes import ERROR_RESPONSES, get_repo, get_session
from analog_api.domain.errors import AppError
from analog_api.domain.planetary import (
    COORDINATE_CONTRACT_VERSION,
    MAP_CATALOG_VERSION,
    PLANETARY_BODIES,
    GeographicBBox,
    coordinate_contract_payload,
    get_body,
    maps_payload,
    parse_bbox,
)
from analog_api.domain.planetary_service import (
    DIMENSION_CODES,
    overlay_payload,
    science_summary_payload,
)
from analog_api.ports.repository import CandidateRepositoryFull
from analog_api.schemas import (
    PlanetaryCoordinateContractOut,
    PlanetaryMapFeaturesOut,
    PlanetaryMapsOut,
)

router = APIRouter(prefix="/api/v1/planetary", tags=["planetary"])

MAX_PLANETARY_LIMIT = 200


@router.get("/maps", response_model=PlanetaryMapsOut, responses=ERROR_RESPONSES)
def planetary_maps(
    body: str,
    response: Response,
    session: Session = Depends(get_session),
    repo: CandidateRepositoryFull = Depends(get_repo),
) -> dict[str, object]:
    response.headers["Cache-Control"] = "public, max-age=300"
    response.headers["X-Planetary-Catalog-Version"] = MAP_CATALOG_VERSION
    response.headers["X-Data-Snapshot-ID"] = repo.data_version()
    info = get_body(body)
    typed = TypedObservationStore(session)
    summary = science_summary_payload(
        body=info.body,
        references=repo.list_references(),
        areas=typed.list_areas(body=info.body),
        rows=typed.list_observations(body=info.body),
    )
    return maps_payload(info.body, science_coverage=summary, data_version=repo.data_version())


@router.get("/map-features", response_model=PlanetaryMapFeaturesOut, responses=ERROR_RESPONSES)
def planetary_map_features(
    body: str,
    response: Response,
    bbox: str | None = Query(
        default=None,
        description=(
            "west,south,east,north in planetocentric lon/lat degrees. west>east crosses the "
            "antimeridian. Omit for the whole body."
        ),
    ),
    bbox_lon_convention: Literal["signed", "360"] = "signed",
    limit: int = Query(default=50, ge=1, le=MAX_PLANETARY_LIMIT),
    offset: int = Query(default=0, ge=0),
    feature_type: Literal["all", "reference", "typed_area"] = "all",
    dimension: str | None = None,
    session: Session = Depends(get_session),
    repo: CandidateRepositoryFull = Depends(get_repo),
) -> dict[str, object]:
    response.headers["Cache-Control"] = "public, max-age=60, stale-while-revalidate=300"
    response.headers["X-Planetary-Catalog-Version"] = MAP_CATALOG_VERSION
    response.headers["X-Data-Snapshot-ID"] = repo.data_version()
    info = get_body(body)
    if dimension is not None:
        dimension = dimension.upper()
        if dimension not in DIMENSION_CODES:
            raise AppError(
                "invalid_dimension",
                "dimension must be one of " + ", ".join(DIMENSION_CODES) + ".",
                status_code=422,
            )
    query_bbox = (
        parse_bbox(bbox, lon_convention=bbox_lon_convention)
        if bbox
        else GeographicBBox(-180.0, -90.0, 180.0, 90.0, source="global_default")
    )

    typed = TypedObservationStore(session)
    areas = typed.list_areas(body=info.body)
    rows = typed.list_observations(body=info.body)
    references = repo.list_references()
    result = overlay_payload(
        body=info.body,
        query_bbox=query_bbox,
        references=references,
        areas=areas,
        rows=rows,
        data_version=repo.data_version(),
        limit=limit,
        offset=offset,
        feature_type=feature_type,
        dimension=dimension,
    )
    summary = science_summary_payload(body=info.body, references=references, areas=areas, rows=rows)
    total = result.total
    next_offset = offset + len(result.features) if offset + len(result.features) < total else None
    return {
        "catalog_version": MAP_CATALOG_VERSION,
        "coordinate_contract_version": COORDINATE_CONTRACT_VERSION,
        "data_version": repo.data_version(),
        "body": info.to_payload(),
        "bbox": result.bbox.as_dict(),
        "bbox_crs": (
            "planetocentric longitude/latitude degrees in the declared body frame; "
            "never Earth EPSG:4326"
        ),
        "coordinate_system": info.coordinate_system,
        "query": {
            "bbox": bbox,
            "bbox_lon_convention": bbox_lon_convention,
            "feature_type": feature_type,
            "dimension": dimension,
            "limit": limit,
            "offset": offset,
        },
        "total": total,
        "returned": len(result.features),
        "offset": offset,
        "limit": limit,
        "next_offset": next_offset,
        "unlocatable_reference_count": result.unlocatable_reference_count,
        "unlocatable_typed_area_count": result.unlocatable_typed_area_count,
        "features": result.features,
        "science_coverage_summary": summary,
        "notes": [
            "Only Moon/Mars records are returned; Earth locations are never mixed into this "
            "endpoint.",
            "The query returns real stored references/AOIs only. An empty list is an explicit "
            "empty result, not a synthetic placeholder.",
            "Basemap availability does not imply global six-dimension scientific coverage.",
            "Bbox edges are planetocentric; do not pass them to an Earth EPSG:4326/OSM map "
            "without applying the declared body frame.",
        ],
    }


@router.get(
    "/coordinate-contract",
    response_model=PlanetaryCoordinateContractOut,
)
def planetary_coordinate_contract(response: Response) -> dict[str, object]:
    response.headers["Cache-Control"] = "public, max-age=86400"
    response.headers["X-Planetary-Catalog-Version"] = MAP_CATALOG_VERSION
    return coordinate_contract_payload()


@router.get("/bodies", response_model=list[dict[str, Any]])
def planetary_bodies(response: Response) -> list[dict[str, Any]]:
    response.headers["Cache-Control"] = "public, max-age=3600"
    return [body.to_payload() for body in PLANETARY_BODIES.values()]
