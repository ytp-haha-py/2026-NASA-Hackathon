from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from analog_api.adapters.db import AnalysisJob
from analog_api.adapters.jobs import JobStore
from analog_api.adapters.repository import SqlAlchemyRepository
from analog_api.api.routes import get_session
from analog_api.domain.aoi import job_status_payload, validate_aoi_request
from analog_api.domain.errors import AppError
from analog_api.domain.features import FEATURE_BY_ID, METHOD_VERSION
from analog_api.domain.search import execute_search, location_payload
from analog_api.schemas import (
    AoiAnalyzeRequest,
    AoiAnalyzeResponse,
    ErrorOut,
    JobOut,
    LocationOut,
    SearchRequest,
    SearchResponse,
    TargetSpec,
)

router = APIRouter(prefix="/api/v1")

ERROR_RESPONSES = {
    404: {"model": ErrorOut},
    422: {"model": ErrorOut},
    503: {"model": ErrorOut},
}


def _response_payload(job: AnalysisJob, *, cache_key: str, cached: bool) -> dict[str, object]:
    links = {"job": f"/api/v1/jobs/{job.job_id}"}
    if job.area_id:
        links["area_features"] = f"/api/v1/areas/{job.area_id}/features"
    return {
        "job_id": job.job_id,
        "status": job.status,
        "area_id": job.area_id,
        "cache_key": cache_key,
        "cached": cached,
        "provider_status": job.provider_status or {},
        "result": job.result_json,
        "links": links,
    }


@router.post(
    "/areas/analyze",
    response_model=AoiAnalyzeResponse,
    status_code=202,
    responses=ERROR_RESPONSES,
)
def analyze_area(
    payload: AoiAnalyzeRequest,
    request: Request,
    response: Response,
    session: Session = Depends(get_session),
) -> dict[str, object]:
    validated = validate_aoi_request(
        bbox=payload.bbox.model_dump() if payload.bbox else None,
        geometry=payload.geometry,
        requested_features=payload.requested_features,
        reference_id=payload.reference_id,
        idempotency_key=payload.idempotency_key,
    )
    repo = SqlAlchemyRepository(session)
    if payload.reference_id and repo.get_reference(payload.reference_id) is None:
        raise AppError(
            "not_found",
            f"Reference {payload.reference_id} is not available in this index.",
            status_code=404,
        )
    runner = getattr(request.app.state, "job_runner", None)
    if runner is None:
        raise AppError(
            "service_unavailable",
            "The AOI job runner is not configured.",
            status_code=503,
        )

    request_payload = {
        "body": validated.body,
        "bbox": {
            "west": validated.bbox.west,
            "south": validated.bbox.south,
            "east": validated.bbox.east,
            "north": validated.bbox.north,
        },
        "geometry": validated.geometry,
        "area_km2": validated.area_km2,
        "requested_features": list(validated.requested_features),
        "reference_id": validated.reference_id,
        "area_id": validated.area_id,
        "cache_key": validated.cache_key,
        "width_m": validated.width_m,
        "height_m": validated.height_m,
    }
    store = JobStore(session)
    job, created = store.create_or_get(
        cache_key=validated.cache_key,
        idempotency_key=validated.idempotency_key,
        request_payload=request_payload,
    )
    if job.area_id is None:
        job.area_id = validated.area_id
        session.commit()
    if created:
        runner.submit(job.job_id)
    if getattr(runner, "inline", False):
        session.expire_all()
        job = session.get(AnalysisJob, job.job_id)

    response.status_code = 200 if job.status in {"completed", "partial", "failed"} else 202
    return _response_payload(job, cache_key=validated.cache_key, cached=not created)


@router.get("/jobs/{job_id}", response_model=JobOut, responses=ERROR_RESPONSES)
def get_job(job_id: str, session: Session = Depends(get_session)) -> dict[str, object]:
    job = session.get(AnalysisJob, job_id)
    if job is None:
        raise AppError("not_found", f"Job {job_id} was not found.", status_code=404)
    return job_status_payload(job)


@router.get("/areas/{area_id}/features", response_model=LocationOut, responses=ERROR_RESPONSES)
def area_features(area_id: str, session: Session = Depends(get_session)) -> dict[str, object]:
    repo = SqlAlchemyRepository(session)
    candidate = repo.get_candidate(area_id)
    if candidate is None or candidate.origin != "on_demand":
        raise AppError("not_found", f"On-demand area {area_id} was not found.", status_code=404)
    return location_payload(repo, area_id)


@router.post(
    "/areas/{area_id}/compare",
    response_model=SearchResponse,
    responses=ERROR_RESPONSES,
)
def compare_area(
    area_id: str,
    payload: SearchRequest | None = None,
    session: Session = Depends(get_session),
) -> dict[str, object]:
    """Compare an area against the indexed candidates using the normal comparator.

    With no body (or an empty target/reference), the area's own terrain observations become the
    target. This never introduces a second scoring algorithm.
    """
    repo = SqlAlchemyRepository(session)
    candidate = repo.get_candidate(area_id)
    if candidate is None:
        raise AppError("not_found", f"Area {area_id} was not found.", status_code=404)
    if payload is None or (not payload.target and not payload.reference_id):
        target = {
            feature_id: TargetSpec(value=observation.value, weight=1.0)
            for feature_id, observation in candidate.features.items()
            if feature_id in FEATURE_BY_ID
            and observation.method_version == FEATURE_BY_ID[feature_id].method_version
        }
        if not target:
            raise AppError(
                "no_comparable_features",
                f"Area {area_id} has no observations compatible with {METHOD_VERSION}.",
                status_code=409,
            )
        payload = SearchRequest(body="custom", target=target, limit=10)
    return execute_search(repo, payload)
