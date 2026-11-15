"""Full-AOI planetary job endpoints with explicit D01-D06 statuses (Issue #170, Goal D).

These routes are additive to the Issue #132 ``/planetary/areas/analyze`` contract:

* ``POST /api/v1/planetary/aoi`` creates/reuses a durable job and asks the
  configured planetary provider to analyse the whole bounded AOI.
* ``GET /api/v1/planetary/aoi/{job_id}`` reports the job lifecycle.
* ``GET /api/v1/planetary/aoi/{job_id}/result`` returns the persisted result or an
  explicit ``409`` when the job is not ready.

The provider interface is the same ``validate``/``analyze`` protocol used by #132.
A Goal A/B full-AOI engine can be injected as ``planetary_processor`` in
``create_app`` without changing this route. Until such an engine is wired, the
committed-fixture provider remains the fallback and the response reports
``full_aoi: false`` / ``analysis_scope: selected_cell_union`` instead of pretending
the requested AOI was covered.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from analog_api.adapters.db import AnalysisJob
from analog_api.adapters.jobs import JobStore
from analog_api.adapters.typed_db import TypedObservationStore
from analog_api.api.routes import ERROR_RESPONSES, get_session
from analog_api.domain.errors import AppError
from analog_api.domain.planetary_aoi_report import CONTRACT_VERSION, build_engine_report
from analog_api.schemas import PlanetaryAoiEngineRequest, PlanetaryAoiEngineResponse

router = APIRouter(prefix="/api/v1/planetary", tags=["planetary"])

_FINAL_STATUSES = {"completed", "partial", "failed"}
_COORDINATE_FRAMES = {
    "Moon": "lunar_planetocentric",
    "Mars": "mars_planetocentric",
}


def _bbox_query(bbox: dict[str, float] | None) -> str | None:
    if not bbox:
        return None
    return f"{bbox['west']},{bbox['south']},{bbox['east']},{bbox['north']}"


def _links(body: str | None, area_id: str | None, bbox: dict[str, float] | None) -> dict[str, str]:
    links: dict[str, str] = {}
    if area_id:
        links["typed_area"] = f"/api/v1/typed/areas/{area_id}"
    if body in _COORDINATE_FRAMES:
        query = _bbox_query(bbox)
        if query:
            links["map_features"] = (
                f"/api/v1/planetary/map-features?body={body}&bbox={query}&limit=200"
            )
            links["coverage"] = (
                f"/api/v1/coverage/location?body={body}&west={bbox['west']}"
                f"&south={bbox['south']}&east={bbox['east']}&north={bbox['north']}"
            )
    return links


def _job_payload(
    *,
    session: Session,
    job: AnalysisJob,
    cached: bool,
) -> dict[str, Any]:
    request_payload = job.request_json or {}
    result_payload = job.result_json if isinstance(job.result_json, dict) else None
    store = TypedObservationStore(session)
    area = store.get_area(job.area_id) if job.area_id else None
    observations = list((area or {}).get("observations") or [])
    report = build_engine_report(
        job_status=job.status,
        error_code=job.error_code,
        error_message=job.error_message,
        request=request_payload,
        result=result_payload,
        area=area,
        observations=observations,
    )
    body = str(report.get("body") or "")
    links = _links(body, job.area_id, report.get("requested_bbox"))
    links["job"] = f"/api/v1/planetary/aoi/{job.job_id}"
    links["result"] = f"/api/v1/planetary/aoi/{job.job_id}/result"
    links["shared_job"] = f"/api/v1/jobs/{job.job_id}"
    return {
        "contract_version": CONTRACT_VERSION,
        "job_id": job.job_id,
        "status": job.status,
        "area_id": job.area_id,
        "cache_key": job.cache_key,
        "cached": cached,
        **report,
        "provider_status": job.provider_status or {},
        "result": result_payload,
        "links": links,
    }


def _get_planetary_job(session: Session, job_id: str) -> AnalysisJob:
    job = session.get(AnalysisJob, job_id)
    if job is None:
        raise AppError("not_found", f"Job {job_id} was not found.", status_code=404)
    request_payload = job.request_json or {}
    if request_payload.get("body") not in _COORDINATE_FRAMES:
        raise AppError(
            "not_found",
            f"Planetary AOI job {job_id} was not found.",
            status_code=404,
        )
    return job


@router.post(
    "/aoi",
    response_model=PlanetaryAoiEngineResponse,
    status_code=202,
    responses=ERROR_RESPONSES,
)
def create_planetary_aoi_job(
    payload: PlanetaryAoiEngineRequest,
    request: Request,
    response: Response,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Create or reuse a durable full-AOI Moon/Mars analysis job."""
    provider = getattr(request.app.state, "planetary_aoi_provider", None)
    runner = getattr(request.app.state, "job_runner", None)
    if provider is None or runner is None:
        raise AppError(
            "service_unavailable",
            "The planetary AOI analysis provider is not configured.",
            status_code=503,
        )
    from analog_api.domain.planetary_aoi_engine import normalize_bbox

    raw_bbox = payload.bbox.model_dump()
    normalized = normalize_bbox(
        raw_bbox, body=payload.body, lon_convention=payload.bbox_lon_convention
    )
    bbox_for_provider = {
        "west": normalized["west"],
        "south": normalized["south"],
        "east": normalized["east"],
        "north": normalized["north"],
    }
    validated = provider.validate(
        body=payload.body,
        bbox=bbox_for_provider,
        requested_features=payload.requested_features,
        idempotency_key=payload.idempotency_key,
    )
    bbox = validated.bbox.as_dict()
    coordinate_frame = normalized.get("coordinate_frame") or _COORDINATE_FRAMES[payload.body]
    request_payload = {
        "body": validated.body,
        "bbox": bbox,
        "requested_bbox": {
            "west": normalized["west"],
            "south": normalized["south"],
            "east": normalized["east"],
            "north": normalized["north"],
        },
        "bbox_lon_convention": payload.bbox_lon_convention,
        "input_lon_convention": payload.bbox_lon_convention,
        "crosses_antimeridian": normalized["crosses_antimeridian"],
        "longitude_span_deg": normalized["longitude_span_deg"],
        "geometry": None,
        "area_km2": validated.area_km2,
        "requested_features": list(validated.requested_features),
        "reference_id": None,
        "area_id": validated.area_id,
        "cache_key": validated.cache_key,
        "width_m": validated.width_m,
        "height_m": validated.height_m,
        "coordinate_frame": coordinate_frame,
        "endpoint": "planetary_aoi_engine",
        "contract_version": CONTRACT_VERSION,
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
    assert job is not None
    response.status_code = 200 if job.status in _FINAL_STATUSES else 202
    response.headers["Cache-Control"] = "no-store"
    return _job_payload(session=session, job=job, cached=not created)


@router.get("/aoi/{job_id}", response_model=PlanetaryAoiEngineResponse, responses=ERROR_RESPONSES)
def get_planetary_aoi_job(
    job_id: str,
    response: Response,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Report the durable job lifecycle and the current D01-D06 statuses."""
    job = _get_planetary_job(session, job_id)
    response.headers["Cache-Control"] = "no-store"
    return _job_payload(session=session, job=job, cached=True)


@router.get(
    "/aoi/{job_id}/result",
    response_model=PlanetaryAoiEngineResponse,
    responses=ERROR_RESPONSES,
)
def get_planetary_aoi_result(
    job_id: str,
    response: Response,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Return the persisted engine result once the job completed or partially completed."""
    job = _get_planetary_job(session, job_id)
    if job.status in {"queued", "running"}:
        raise AppError(
            "job_not_ready",
            f"Job {job_id} is {job.status}; the result is not available yet.",
            status_code=409,
            details=[{"job_id": job.job_id, "status": job.status}],
        )
    if job.status == "failed":
        raise AppError(
            "job_failed",
            f"Job {job_id} failed; no result was persisted.",
            status_code=409,
            details=[
                {
                    "job_id": job.job_id,
                    "status": job.status,
                    "error_code": job.error_code,
                    "error_message": job.error_message,
                }
            ],
        )
    response.headers["Cache-Control"] = "no-store"
    return _job_payload(session=session, job=job, cached=True)


__all__ = ["router"]
