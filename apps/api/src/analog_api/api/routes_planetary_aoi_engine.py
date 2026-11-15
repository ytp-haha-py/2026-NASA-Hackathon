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

from collections.abc import Mapping, Sequence
from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from analog_api.adapters.db import AnalysisJob
from analog_api.adapters.jobs import JobStore
from analog_api.adapters.typed_db import TypedObservationStore
from analog_api.api.routes import ERROR_RESPONSES, get_session
from analog_api.domain.errors import AppError
from analog_api.domain.planetary_aoi_engine import CONTRACT_VERSION, build_engine_report
from analog_api.domain.planetary_aoi_geometry import AOI_ENGINE_GEOMETRY_VERSION
from analog_api.domain.planetary_aoi_limits import validate_provider_geometry
from analog_api.schemas import PlanetaryAoiEngineRequest, PlanetaryAoiEngineResponse

router = APIRouter(prefix="/api/v1/planetary", tags=["planetary"])

_FINAL_STATUSES = {"completed", "partial", "failed"}
ANALYSIS_WINDOW_PIXELS = 20  # fixed 20 x 20 core pixels per committed 5 km cell
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


def _geometry_support_payload(
    geometry: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Additive geometry fields nested in the existing dict-valued ``support``."""
    if not isinstance(geometry, Mapping):
        return {}
    return {
        "geometry_version": geometry.get("geometry_version"),
        "normalized_bbox": geometry.get("normalized_bbox"),
        "bbox_pieces": geometry.get("bbox_pieces") or [],
        "metric_envelope": geometry.get("metric_envelope"),
        "requested_area_km2": geometry.get("area_km2"),
        "envelope_area_km2": geometry.get("envelope_area_km2"),
        "pixel_size_m": geometry.get("pixel_size_m"),
        "pixels_x": geometry.get("pixels_x"),
        "pixels_y": geometry.get("pixels_y"),
        "total_pixels": geometry.get("total_pixels"),
        "limits": geometry.get("limits"),
        "crosses_antimeridian": geometry.get("crosses_antimeridian"),
        "is_full_longitude": geometry.get("is_full_longitude"),
        "includes_north_pole": geometry.get("includes_north_pole"),
        "includes_south_pole": geometry.get("includes_south_pole"),
        "longitude_span_deg": geometry.get("longitude_span_deg"),
        "latitude_kind": geometry.get("latitude_kind"),
        "analysis_extent": geometry.get("analysis_extent"),
        "shrink_applied": geometry.get("shrink_applied"),
    }


def _pixel_accounting(
    *,
    report: Mapping[str, Any],
    result: Mapping[str, Any] | None,
    observations: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Return real D01 pixel accounting, deriving cell/window counts when needed."""
    result = result or {}
    d01_result = (result.get("dimension_support") or {}).get("D01") or {}
    existing = report.get("support", {}).get("pixel_accounting")
    existing = existing if isinstance(existing, Mapping) else {}
    existing_d01 = (
        (report.get("dimensions") or {}).get("D01", {}).get("support", {}).get("pixel_accounting")
    )
    if isinstance(existing_d01, Mapping) and existing_d01.get("total_pixels") is not None:
        return dict(existing_d01)
    if existing.get("total_pixels") is not None:
        return dict(existing)

    valid_fraction = None
    resolution_m = existing.get("resolution_m")
    source_resolution_m = existing.get("source_resolution_m")
    window_size_m = existing.get("window_size_m")
    for row in observations:
        if str(row.get("dimension_code")) != "D01":
            continue
        support = row.get("spatial_support") or {}
        if isinstance(support, Mapping):
            if valid_fraction is None and support.get("valid_fraction") is not None:
                valid_fraction = float(support["valid_fraction"])
            if resolution_m is None and support.get("resolution_m") is not None:
                resolution_m = float(support["resolution_m"])
            if source_resolution_m is None and support.get("source_resolution_m") is not None:
                source_resolution_m = float(support["source_resolution_m"])
            if window_size_m is None and support.get("window_size_m") is not None:
                window_size_m = float(support["window_size_m"])
        if valid_fraction is not None and resolution_m is not None:
            break
    if valid_fraction is None:
        valid_fraction = existing.get("valid_fraction")
    cell_count = d01_result.get("cell_count")
    if cell_count is None:
        cell_count = existing.get("cell_count")
    cell_count = int(cell_count) if cell_count is not None else None
    total_pixels = (
        cell_count * ANALYSIS_WINDOW_PIXELS * ANALYSIS_WINDOW_PIXELS
        if cell_count is not None
        else None
    )
    valid_pixels = (
        int(round(total_pixels * float(valid_fraction)))
        if total_pixels is not None and valid_fraction is not None
        else None
    )
    missing_pixels = (
        total_pixels - valid_pixels
        if total_pixels is not None and valid_pixels is not None
        else None
    )
    return {
        **dict(existing),
        "valid_fraction": valid_fraction,
        "resolution_m": resolution_m,
        "source_resolution_m": source_resolution_m,
        "window_size_m": window_size_m,
        "cell_count": cell_count,
        "total_pixels": total_pixels,
        "valid_pixels": valid_pixels,
        "invalid_or_missing_pixels": missing_pixels,
    }


def _augment_engine_report(
    report: dict[str, Any],
    *,
    request_payload: Mapping[str, Any],
    result_payload: Mapping[str, Any] | None,
    observations: Sequence[Mapping[str, Any]],
) -> None:
    geometry = request_payload.get("aoi_geometry")
    pixel_accounting = _pixel_accounting(
        report=report, result=result_payload, observations=observations
    )
    support = report.setdefault("support", {})
    support.update(_geometry_support_payload(geometry))
    support["pixel_accounting"] = pixel_accounting
    dimensions = report.setdefault("dimensions", {})
    d01 = dimensions.get("D01")
    if isinstance(d01, dict):
        d01_support = d01.setdefault("support", {})
        d01_support.update(_geometry_support_payload(geometry))
        d01_support["pixel_accounting"] = pixel_accounting
    coverage = report.setdefault("coverage", {})
    coverage["pixel_accounting"] = pixel_accounting
    coverage["bbox_pieces"] = (geometry or {}).get("bbox_pieces") or []
    coverage["crosses_antimeridian"] = (geometry or {}).get("crosses_antimeridian")
    coverage["includes_north_pole"] = (geometry or {}).get("includes_north_pole")
    coverage["includes_south_pole"] = (geometry or {}).get("includes_south_pole")
    coverage["analysis_extent"] = (geometry or {}).get("analysis_extent")
    coverage["shrink_applied"] = (geometry or {}).get("shrink_applied")


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
    _augment_engine_report(
        report,
        request_payload=request_payload,
        result_payload=result_payload,
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
    geometry = validate_provider_geometry(
        payload.body,
        payload.bbox.model_dump(),
        lon_convention=payload.bbox_lon_convention,
        latitude_kind=payload.latitude_kind,
    )
    bbox_for_provider = geometry.normalized_bbox
    validated = provider.validate(
        body=payload.body,
        bbox=bbox_for_provider,
        requested_features=payload.requested_features,
        idempotency_key=payload.idempotency_key,
    )
    bbox = validated.bbox.as_dict()
    coordinate_frame = geometry.coordinate_frame or _COORDINATE_FRAMES[payload.body]
    aoi_geometry = {
        "geometry_version": AOI_ENGINE_GEOMETRY_VERSION,
        "requested_bbox": dict(geometry.requested_bbox),
        "normalized_bbox": dict(geometry.normalized_bbox),
        "bbox_pieces": [dict(piece) for piece in geometry.bbox_pieces],
        "metric_envelope": geometry.metric_envelope.to_payload(),
        "area_km2": geometry.area_km2,
        "envelope_area_km2": geometry.envelope_area_km2,
        "pixel_size_m": geometry.pixel_size_m,
        "pixels_x": geometry.pixels_x,
        "pixels_y": geometry.pixels_y,
        "total_pixels": geometry.total_pixels,
        "limits": geometry.limits.to_payload(),
        "crosses_antimeridian": geometry.crosses_antimeridian,
        "is_full_longitude": geometry.is_full_longitude,
        "includes_north_pole": geometry.includes_north_pole,
        "includes_south_pole": geometry.includes_south_pole,
        "longitude_span_deg": geometry.longitude_span_deg,
        "longitude_convention": geometry.longitude_convention,
        "latitude_kind": geometry.latitude_kind,
        "analysis_extent": geometry.analysis_extent,
        "shrink_applied": geometry.shrink_applied,
        "crs": geometry.crs,
        "coordinate_frame": geometry.coordinate_frame,
    }
    request_payload = {
        "body": validated.body,
        "bbox": bbox,
        "requested_bbox": dict(geometry.requested_bbox),
        "normalized_bbox": dict(geometry.normalized_bbox),
        "bbox_pieces": [dict(piece) for piece in geometry.bbox_pieces],
        "bbox_lon_convention": payload.bbox_lon_convention,
        "input_lon_convention": payload.bbox_lon_convention,
        "crosses_antimeridian": geometry.crosses_antimeridian,
        "is_full_longitude": geometry.is_full_longitude,
        "includes_north_pole": geometry.includes_north_pole,
        "includes_south_pole": geometry.includes_south_pole,
        "longitude_span_deg": geometry.longitude_span_deg,
        "analysis_extent": geometry.analysis_extent,
        "shrink_applied": geometry.shrink_applied,
        "aoi_geometry": aoi_geometry,
        "geometry": geometry.geometry,
        "area_km2": validated.area_km2,
        "requested_area_km2": geometry.area_km2,
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
