"""Earth viewport map-features endpoint (Issue #132).

``GET /api/v1/earth/map-features`` returns stored Earth candidates, typed areas
and coarse shortlist rows that intersect an Earth WGS84 viewport.  It never
returns planetary records and never converts a coarse shortlist row into a fine
90 m observation.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from analog_pipelines.coarse_index import (
    CoarseIndexRepository,
    IndexError_,
    resolve_index_root,
)
from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from analog_api.adapters.db import Candidate
from analog_api.adapters.typed_db import TypedObservationStore
from analog_api.api.routes import ERROR_RESPONSES, get_repo, get_session
from analog_api.config import resolve_repo_path
from analog_api.domain.earth_map import (
    DIMENSION_CODES,
    EARTH_BBOX_CRS,
    EARTH_BODY,
    EARTH_COORDINATE_CONTRACT_VERSION,
    EARTH_COORDINATE_FRAME,
    EARTH_COORDINATE_SYSTEM,
    EARTH_MAP_FEATURES_VERSION,
    EarthBBox,
    build_map_features,
    global_earth_bbox,
    parse_earth_bbox,
)
from analog_api.domain.errors import AppError
from analog_api.ports.repository import CandidateRepositoryFull

router = APIRouter(prefix="/api/v1/earth", tags=["earth-map"])

MAX_EARTH_MAP_LIMIT = 200
# The coarse bbox repository enforces this same per-query ceiling.
MAX_COARSE_QUERY_ROWS = 200
CACHE_CONTROL = "public, max-age=60, stale-while-revalidate=300"


class EarthMapBBoxOut(BaseModel):
    west: float
    south: float
    east: float
    north: float
    crosses_antimeridian: bool
    longitude_span_deg: float
    source: str
    crs: str


class EarthMapCenterOut(BaseModel):
    lon: float
    lat: float


class EarthMapQueryOut(BaseModel):
    bbox: str | None
    bbox_lon_convention: str
    bbox_source: str
    feature_type: str
    data_level: str
    dimension: str | None
    availability: str
    limit: int
    offset: int


class EarthMapFeatureOut(BaseModel):
    model_config = {"extra": "allow"}

    feature_id: str
    feature_type: Literal["fine_candidate", "on_demand_aoi", "typed_area", "coarse_candidate"]
    body: Literal["Earth"]
    display_name: str
    data_level: Literal["fine", "coarse", "typed"]
    bbox: EarthMapBBoxOut
    center: EarthMapCenterOut
    geometry: dict[str, Any]
    bbox_geometry: dict[str, Any]
    crs: str
    bbox_crs: str
    coordinate_frame: str
    support: str
    support_label: str
    dimension_status: dict[str, dict[str, Any]]
    available_dimensions: list[str]
    missing_dimensions: list[str]
    availability: dict[str, Any]
    data_version: str
    detail_links: dict[str, str | None]
    location_id: str | None = None
    candidate_id: str | None = None
    area_id: str | None = None
    typed_area_id: str | None = None
    linked_candidate_id: str | None = None
    analysis_crs: str | None = None


class EarthMapCoverageOut(BaseModel):
    model_config = {"extra": "allow"}

    fine: dict[str, Any]
    on_demand: dict[str, Any]
    typed: dict[str, Any]
    coarse: dict[str, Any]


class EarthMapFeaturesOut(BaseModel):
    contract_version: str
    coordinate_contract_version: str
    data_version: str
    snapshot_id: str
    body: Literal["Earth"]
    bbox: EarthMapBBoxOut
    bbox_crs: str
    coordinate_system: str
    coordinate_frame: str
    query: EarthMapQueryOut
    total: int
    returned: int
    offset: int
    limit: int
    next_offset: int | None
    counts_by_type: dict[str, int]
    coverage: EarthMapCoverageOut
    features: list[EarthMapFeatureOut]
    notes: list[str] = Field(default_factory=list)


def _index_root(request: Request) -> Path:
    return resolve_repo_path(request.app.state.settings.global_index_dir)


def _coarse_repository(request: Request) -> tuple[CoarseIndexRepository | None, str]:
    """Resolve the cached coarse index without failing the whole map endpoint."""
    root = _index_root(request)
    snapshot_id = request.app.state.settings.global_snapshot_id
    try:
        resolved = resolve_index_root(root, snapshot_id=snapshot_id)
    except IndexError_:
        return None, "no_index"
    manifest = resolved / "manifest.json"
    try:
        signature = (manifest.stat().st_mtime_ns, manifest.stat().st_size)
    except OSError:
        return None, "no_index"
    cache = getattr(request.app.state, "global_index_cache", None)
    if cache is None:
        cache = {}
        request.app.state.global_index_cache = cache
    cached = cache.get(str(resolved))
    if cached is not None and cached[0] == signature:
        return cached[1], "ready"
    try:
        repository = CoarseIndexRepository(resolved, verify=True)
    except IndexError_:
        return None, "index_invalid"
    cache[str(resolved)] = (signature, repository)
    return repository, "ready"


def _candidate_spatial_clause(query_bbox: EarthBBox):
    """SQL pushdown for stored candidate bboxes.

    Stored Earth candidates/AOIs are non-crossing (the AOI validator rejects
    antimeridian AOIs), so a crossing query is pushed down as its two sides.
    The exact in-memory ``bbox_intersects`` test remains authoritative.
    """
    if query_bbox.is_global():
        return None
    clauses = []
    for part in query_bbox.split():
        clauses.append(
            and_(
                Candidate.west <= part.east,
                Candidate.east >= part.west,
                Candidate.south <= part.north,
                Candidate.north >= part.south,
            )
        )
    return or_(*clauses)


def _query_candidates(session: Session, query_bbox: EarthBBox) -> list[Candidate]:
    statement = select(Candidate)
    spatial = _candidate_spatial_clause(query_bbox)
    if spatial is not None:
        statement = statement.where(spatial)
    statement = statement.order_by(Candidate.location_id)
    return list(session.scalars(statement).all())


def _coarse_payload(
    request: Request,
    query_bbox: EarthBBox,
    *,
    limit: int,
    offset: int,
    non_coarse_total: int,
) -> tuple[list[dict[str, object]], int, dict[str, object], list[str], int]:
    """Return one bounded coarse window aligned with the combined page.

    Coarse candidates are the first feature type in the deterministic map order,
    so coarse rank ``r`` is the combined rank ``r``.  The window is
    ``[offset, offset + limit)`` clipped to the coarse match count, which keeps
    pages non-overlapping while never asking the coarse repository for more than
    200 rows.
    """
    repository, status = _coarse_repository(request)
    notes: list[str] = []
    if repository is None:
        if status == "no_index":
            note = (
                "No coarse Parquet index is configured in this deployment; coarse map features "
                "are explicitly unavailable (status=no_index) rather than fabricated."
            )
        else:
            note = (
                "The configured coarse index could not be verified; coarse map features are "
                "explicitly unavailable (status=index_invalid)."
            )
        notes.append(note)
        return (
            [],
            0,
            {
                "status": status,
                "snapshot_id": None,
                "snapshot": None,
                "method_version": None,
                "method": None,
                "row_count": 0,
                "regions": [],
                "bbox": None,
                "label": "no_coarse_index_available",
                "matched_count": 0,
                "returned": 0,
                "limit": 0,
                "offset": 0,
                "truncated": False,
                "query_truncated": False,
                "note": note,
            },
            notes,
            0,
        )

    coverage = repository.coverage()
    query_bbox_method = getattr(repository, "query_bbox", None)
    if query_bbox_method is None:
        note = (
            "This coarse index build does not expose bbox queries; coarse map features are "
            "explicitly unavailable for this deployment."
        )
        notes.append(note)
        return (
            [],
            0,
            {
                "status": "coarse_query_unavailable",
                "snapshot_id": coverage.get("snapshot_id"),
                "snapshot": coverage.get("snapshot_id"),
                "method_version": coverage.get("method_version"),
                "method": coverage.get("method_version"),
                "row_count": coverage.get("row_count", 0),
                "regions": coverage.get("regions", []),
                "bbox": coverage.get("bbox"),
                "label": coverage.get("label"),
                "matched_count": 0,
                "returned": 0,
                "limit": 0,
                "offset": 0,
                "truncated": False,
                "query_truncated": False,
                "note": note,
            },
            notes,
            0,
        )

    # A small first query supplies the match count.  It is also the page itself
    # when the requested window starts at coarse rank zero and fits in 200 rows.
    first_limit = min(limit, MAX_COARSE_QUERY_ROWS)
    first = query_bbox_method(
        west=query_bbox.west,
        south=query_bbox.south,
        east=query_bbox.east,
        north=query_bbox.north,
        limit=first_limit,
        offset=0,
    )
    total = int(first.get("total", 0))
    first_rows = [dict(row) for row in first.get("rows", [])]
    # Combined order is fine/on-demand/typed first, then coarse.  Translate the
    # requested global page into the coarse rank window it overlaps.
    coarse_start = min(max(offset - non_coarse_total, 0), total)
    coarse_end = min(max(offset + limit - non_coarse_total, 0), total)
    desired = max(coarse_end - coarse_start, 0)

    if desired == 0:
        rows: list[dict[str, object]] = []
        query_offset = coarse_start
        query_limit = 0
    elif coarse_start == 0 and desired <= len(first_rows):
        rows = first_rows[:desired]
        query_offset = 0
        query_limit = first_limit
    else:
        # Enough offset to position the combined page; never exceed 200 per call.
        query_limit = min(desired, MAX_COARSE_QUERY_ROWS)
        query_offset = coarse_start
        page_result = query_bbox_method(
            west=query_bbox.west,
            south=query_bbox.south,
            east=query_bbox.east,
            north=query_bbox.north,
            limit=query_limit,
            offset=query_offset,
        )
        rows = [dict(row) for row in page_result.get("rows", [])]

    truncated = total > len(rows)
    if truncated:
        notes.append(
            "coarse_query_limited: the viewport matched more coarse shortlist rows than this "
            "bounded page returned; continue with next_offset or narrow the viewport."
        )
    snapshot_id = first.get("snapshot_id", coverage.get("snapshot_id"))
    method_version = first.get("method_version", coverage.get("method_version"))
    payload = {
        "status": "ready",
        "snapshot_id": snapshot_id,
        "snapshot": snapshot_id,
        "method_version": method_version,
        "method": method_version,
        "row_count": coverage.get("row_count", 0),
        "regions": coverage.get("regions", []),
        "bbox": coverage.get("bbox"),
        "label": coverage.get("label"),
        "matched_count": total,
        "returned": len(rows),
        "limit": query_limit,
        "offset": query_offset,
        "truncated": truncated,
        "query_truncated": truncated,
        "note": (
            "Coarse rows are 250 m-derived shortlist candidates, not the fine 90 m terrain "
            "result. Refine explicitly before comparing them with fine candidates."
        ),
    }
    return rows, total, payload, notes, query_offset


@router.get(
    "/map-features",
    response_model=EarthMapFeaturesOut,
    responses=ERROR_RESPONSES,
)
def earth_map_features(
    request: Request,
    response: Response,
    body: str = Query(
        default=EARTH_BODY,
        description="Earth only. Moon/Mars map features live under /api/v1/planetary.",
    ),
    bbox: str | None = Query(
        default=None,
        description=(
            "west,south,east,north in Earth WGS84 degrees. west>east crosses the "
            "antimeridian. Omit for the whole world."
        ),
    ),
    bbox_lon_convention: Literal["signed", "360"] = "signed",
    limit: int = Query(default=50, ge=1, le=MAX_EARTH_MAP_LIMIT),
    offset: int = Query(default=0, ge=0),
    feature_type: Literal[
        "all", "fine_candidate", "on_demand_aoi", "typed_area", "coarse_candidate"
    ] = "all",
    data_level: Literal["all", "fine", "coarse"] = "all",
    dimension: str | None = None,
    availability: Literal["any", "present", "absent"] = "any",
    session: Session = Depends(get_session),
    repo: CandidateRepositoryFull = Depends(get_repo),
) -> dict[str, object]:
    response.headers["Cache-Control"] = CACHE_CONTROL
    response.headers["X-Earth-Map-Contract-Version"] = EARTH_MAP_FEATURES_VERSION
    data_version = repo.data_version()
    response.headers["X-Data-Snapshot-ID"] = data_version

    if body.strip().lower() != EARTH_BODY.lower():
        raise AppError(
            "unsupported_body",
            "This endpoint returns Earth features only; use /api/v1/planetary for Moon/Mars.",
            status_code=422,
        )
    if dimension is not None:
        dimension = dimension.upper()
        if dimension not in DIMENSION_CODES:
            raise AppError(
                "invalid_dimension",
                "dimension must be one of " + ", ".join(DIMENSION_CODES) + ".",
                status_code=422,
            )

    bbox_source = "global_default" if bbox is None else "query"
    query_bbox = (
        global_earth_bbox(source=bbox_source)
        if bbox is None
        else parse_earth_bbox(bbox, lon_convention=bbox_lon_convention, source=bbox_source)
    )

    candidates = _query_candidates(session, query_bbox)
    typed = TypedObservationStore(session)
    areas = typed.list_areas(body=EARTH_BODY)
    observations = typed.list_observations(body=EARTH_BODY)

    # Coarse ranks follow the (small) fine/typed list.  Build that list once
    # without coarse rows so the coarse window can be translated into database
    # offset/limit without fetching earlier pages or repeating a row.
    non_coarse_only = build_map_features(
        query_bbox=query_bbox,
        fine_candidates=candidates,
        typed_areas=areas,
        typed_observations=observations,
        coarse_total=0,
        feature_type=feature_type,
        data_level=data_level,
        dimension=dimension,
        availability=availability,
        limit=limit,
        offset=offset,
        data_version=data_version,
    )
    (
        coarse_rows,
        coarse_total,
        coarse_coverage,
        coarse_notes,
        coarse_row_offset,
    ) = _coarse_payload(
        request,
        query_bbox,
        limit=limit,
        offset=offset,
        non_coarse_total=non_coarse_only.total,
    )

    result = build_map_features(
        query_bbox=query_bbox,
        fine_candidates=candidates,
        typed_areas=areas,
        typed_observations=observations,
        coarse_rows=coarse_rows,
        coarse_coverage=coarse_coverage,
        coarse_total=coarse_total,
        coarse_row_offset=coarse_row_offset,
        feature_type=feature_type,
        data_level=data_level,
        dimension=dimension,
        availability=availability,
        limit=limit,
        offset=offset,
        data_version=data_version,
    )

    notes = [
        "Fine candidates are stored 90 m terrain windows; coarse rows are 250 m-derived "
        "shortlist candidates (shortlist_250m_derived_not_fine), not fine 90 m results.",
        "Only stored observations are reported. Missing D02-D06 are absent with reason "
        "no_stored_observation and are never zero-filled.",
        "This is the stored subset in this deployment, not a global or exhaustive survey. An "
        "empty feature list is an explicit empty result for the queried viewport.",
        "Bounding boxes and GeoJSON coordinates are Earth WGS84 (EPSG:4326) degrees with "
        "longitude before latitude. Planetary body frames are never returned here.",
        "A bbox with west > east crosses the antimeridian; both sides are included.",
    ]
    notes.extend(coarse_notes)

    return {
        "contract_version": EARTH_MAP_FEATURES_VERSION,
        "coordinate_contract_version": EARTH_COORDINATE_CONTRACT_VERSION,
        "data_version": data_version,
        "snapshot_id": data_version,
        "body": EARTH_BODY,
        "bbox": query_bbox.as_dict(),
        "bbox_crs": EARTH_BBOX_CRS,
        "coordinate_system": EARTH_COORDINATE_SYSTEM,
        "coordinate_frame": EARTH_COORDINATE_FRAME,
        "query": {
            "bbox": bbox,
            "bbox_lon_convention": bbox_lon_convention,
            "bbox_source": bbox_source,
            "feature_type": feature_type,
            "data_level": data_level,
            "dimension": dimension,
            "availability": availability,
            "limit": limit,
            "offset": offset,
        },
        "total": result.total,
        "returned": len(result.features),
        "offset": offset,
        "limit": limit,
        "next_offset": result.next_offset,
        "counts_by_type": result.counts_by_type,
        "coverage": result.coverage,
        "features": result.features,
        "notes": notes,
    }


__all__ = [
    "CACHE_CONTROL",
    "MAX_COARSE_QUERY_ROWS",
    "MAX_EARTH_MAP_LIMIT",
    "EarthMapBBoxOut",
    "EarthMapCoverageOut",
    "EarthMapFeatureOut",
    "EarthMapFeaturesOut",
    "EarthMapQueryOut",
    "router",
]
