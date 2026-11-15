"""On-demand Moon/Mars AOI analysis endpoints (Issue #132)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from analog_api.adapters.db import AnalysisJob
from analog_api.adapters.jobs import JobStore
from analog_api.adapters.typed_db import TypedObservationStore
from analog_api.api.routes import ERROR_RESPONSES, get_session
from analog_api.domain.aoi import job_status_payload
from analog_api.domain.errors import AppError
from analog_api.schemas import AoiAnalyzeResponse, JobOut, PlanetaryAoiAnalyzeRequest

router = APIRouter(prefix="/api/v1/planetary", tags=["planetary"])


def _links(
    body: str, area_id: str | None, bbox: dict[str, float], *, status: str
) -> dict[str, str]:
    links = {"job": ""}
    if area_id:
        # ``typed_area`` is only valid after a completed/partial job persisted the area. A failed
        # out-of-coverage job may carry an area_id for its durable row but no typed area exists, and
        # a queued/running job has not persisted one yet. Advertising it would send the client to a
        # guaranteed 404. The map-features link is a bbox query and remains valid without an area.
        if status in {"completed", "partial"}:
            links["typed_area"] = f"/api/v1/typed/areas/{area_id}"
        links["map_features"] = (
            "/api/v1/planetary/map-features?body="
            f"{body}&bbox={bbox['west']},{bbox['south']},{bbox['east']},{bbox['north']}"
        )
    return links


def _response_payload(
    job: AnalysisJob,
    *,
    body: str,
    coordinate_frame: str,
    method_version: str,
    cache_key: str,
    cached: bool,
    bbox: dict[str, float],
) -> dict[str, Any]:
    result = job.result_json or {}
    links = _links(body, job.area_id, bbox, status=job.status)
    links["job"] = f"/api/v1/jobs/{job.job_id}"
    return {
        "job_id": job.job_id,
        "status": job.status,
        "area_id": job.area_id,
        "cache_key": cache_key,
        "cached": cached,
        "provider_status": job.provider_status or {},
        "result": job.result_json,
        "links": links,
        "body": body,
        "coordinate_frame": result.get("coordinate_frame") or coordinate_frame,
        "method_version": result.get("method_version") or method_version,
        "coverage_note": result.get("coverage_note"),
        "dimension_support": result.get("dimension_support") or {},
    }


@router.post(
    "/areas/analyze",
    response_model=AoiAnalyzeResponse,
    status_code=202,
    responses=ERROR_RESPONSES,
)
def analyze_planetary_area(
    payload: PlanetaryAoiAnalyzeRequest,
    request: Request,
    response: Response,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Analyze a bounded Moon/Mars AOI from verified scientific sources.

    The job is durable and queryable through the shared ``/jobs/{job_id}`` route.
    Coordinates are planetocentric degrees in the selected body frame; they are
    never interpreted as Earth WGS84.
    """
    provider = getattr(request.app.state, "planetary_aoi_provider", None)
    runner = getattr(request.app.state, "job_runner", None)
    if provider is None or runner is None:
        raise AppError(
            "service_unavailable",
            "The planetary AOI analysis provider is not configured.",
            status_code=503,
        )
    validated = provider.validate(
        body=payload.body,
        bbox=payload.bbox.model_dump(),
        requested_features=payload.requested_features,
        idempotency_key=payload.idempotency_key,
    )
    bbox = {
        "west": validated.bbox.west,
        "south": validated.bbox.south,
        "east": validated.bbox.east,
        "north": validated.bbox.north,
    }
    coordinate_frame = "lunar_planetocentric" if payload.body == "Moon" else "mars_planetocentric"
    request_payload = {
        "body": validated.body,
        "bbox": bbox,
        "geometry": None,
        "area_km2": validated.area_km2,
        "requested_features": list(validated.requested_features),
        "reference_id": None,
        "area_id": validated.area_id,
        "cache_key": validated.cache_key,
        "width_m": validated.width_m,
        "height_m": validated.height_m,
        "coordinate_frame": coordinate_frame,
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
    return _response_payload(
        job,
        body=validated.body,
        coordinate_frame=coordinate_frame,
        method_version="coarse-planetary-dem-horn-1.0.0",
        cache_key=validated.cache_key,
        cached=not created,
        bbox=bbox,
    )


@router.get("/areas/{area_id}", responses=ERROR_RESPONSES)
def planetary_area(area_id: str, session: Session = Depends(get_session)) -> dict[str, Any]:
    area = TypedObservationStore(session).get_area(area_id)
    if area is None or str(area.get("body")) not in {"Moon", "Mars"}:
        raise AppError("not_found", f"Planetary area {area_id} was not found.", status_code=404)
    return area


@router.get("/jobs/{job_id}", response_model=JobOut, responses=ERROR_RESPONSES)
def get_planetary_job(job_id: str, session: Session = Depends(get_session)) -> dict[str, Any]:
    job = session.get(AnalysisJob, job_id)
    if job is None:
        raise AppError("not_found", f"Job {job_id} was not found.", status_code=404)
    body = (job.request_json or {}).get("body")
    if body not in {"Moon", "Mars"}:
        raise AppError("not_found", f"Planetary job {job_id} was not found.", status_code=404)
    return job_status_payload(job)


__all__ = ["router"]
