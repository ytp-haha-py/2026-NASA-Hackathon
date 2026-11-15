from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from pathlib import Path

from analog_pipelines.coarse_features import (
    COARSE_FEATURES,
    CoarseExtractionError,
)
from analog_pipelines.coarse_index import (
    INDEX_VERSION,
    CoarseIndexRepository,
    IndexError_,
    resolve_index_root,
)
from analog_pipelines.coarse_reference import plan_refine_windows
from analog_pipelines.cross_body_gate import build_cross_body_gate
from analog_pipelines.refine import FOCUS_PLACEMENT_RULE, FOCUS_WINDOW_M, derive_focus_window
from analog_pipelines.refine import refined_area_id as refine_area_id
from fastapi import APIRouter, Depends, Request
from pyproj import Geod
from sqlalchemy.orm import Session

from analog_api.adapters.db import AnalysisJob
from analog_api.adapters.jobs import JobStore
from analog_api.api.routes import get_session
from analog_api.config import resolve_repo_path
from analog_api.domain.aoi import ValidatedAoi
from analog_api.domain.entities import BBox
from analog_api.domain.errors import AppError
from analog_api.domain.features import FEATURE_BY_ID, METHOD_VERSION
from analog_api.schemas import (
    CrossBodyGateOut,
    ErrorOut,
    GlobalCoverageOut,
    GlobalRefineRequest,
    GlobalRefineResponse,
    GlobalSearchRequest,
    GlobalSearchResponse,
    RefinePlanOut,
    RefinePlanRequest,
)

router = APIRouter(prefix="/api/v1/global")
ERROR_RESPONSES = {404: {"model": ErrorOut}, 409: {"model": ErrorOut}, 422: {"model": ErrorOut}}
EARTH_GEOD = Geod(ellps="WGS84")


def _index_root(request: Request) -> Path:
    return resolve_repo_path(request.app.state.settings.global_index_dir)


def _repo(request: Request) -> CoarseIndexRepository:
    root = _index_root(request)
    snapshot_id = request.app.state.settings.global_snapshot_id
    try:
        resolved = resolve_index_root(root, snapshot_id=snapshot_id)
    except IndexError_ as exc:
        raise AppError(
            "no_indexed_data",
            "No verified coarse Parquet index is configured. Global discovery is not indexed "
            "in this deployment; use the existing indexed-subset search or build an authorized "
            "offline fixture index.",
            status_code=409,
        ) from exc
    manifest = resolved / "manifest.json"
    signature = (manifest.stat().st_mtime_ns, manifest.stat().st_size)
    cache = getattr(request.app.state, "global_index_cache", None)
    if cache is None:
        cache = {}
        request.app.state.global_index_cache = cache
    cached = cache.get(str(resolved))
    if cached is not None and cached[0] == signature:
        return cached[1]
    try:
        repository = CoarseIndexRepository(resolved, verify=True)
    except IndexError_ as exc:
        raise AppError("index_invalid", str(exc), status_code=409) from exc
    cache[str(resolved)] = (signature, repository)
    return repository


@router.get("/coverage", response_model=GlobalCoverageOut)
def global_coverage(request: Request) -> dict[str, object]:
    root = _index_root(request)
    try:
        resolve_index_root(root, snapshot_id=request.app.state.settings.global_snapshot_id)
        has_index = True
    except IndexError_:
        has_index = False
    if not has_index:
        return {
            "status": "no_index",
            "snapshot_id": None,
            "label": "no_coarse_index",
            "row_count": 0,
            "regions": [],
            "bbox": None,
            "method_version": None,
            "features": [feature.to_payload() for feature in COARSE_FEATURES],
            "note": (
                "No coarse Parquet index is present. Global discovery is intentionally not "
                "claimed; the existing 113-window 90 m index remains available."
            ),
        }
    repo = _repo(request)
    coverage = repo.coverage()
    return {
        "status": "ready",
        **coverage,
        "note": (
            "Local coarse Parquet index. Coverage is the verified indexed candidate set, "
            "not an exhaustive global claim."
        ),
    }


@router.post("/search", response_model=GlobalSearchResponse, responses=ERROR_RESPONSES)
def global_search(request: Request, payload: GlobalSearchRequest) -> dict[str, object]:
    repo = _repo(request)
    weights = {
        feature_id: payload.weights.get(feature_id, spec.weight)
        for feature_id, spec in payload.target.items()
    }
    try:
        result = repo.search(
            target={feature_id: spec.value for feature_id, spec in payload.target.items()},
            weights=weights,
            limit=payload.limit,
            region_id=payload.region_id,
        )
    except CoarseExtractionError as exc:
        raise AppError(exc.code, exc.message, status_code=422) from exc
    return {
        "snapshot_id": result["snapshot_id"],
        "method_version": result["method_version"],
        "index_version": INDEX_VERSION,
        "coverage_label": "local_coarse_index_not_global_exhaustive",
        "indexed_candidate_count": result["indexed_candidate_count"],
        "eligible_candidate_count": result["eligible_candidate_count"],
        "excluded_candidate_count": result["excluded_candidate_count"],
        "results": result["results"],
        "note": (
            "Coarse 250 m-derived candidates are a shortlist, not the fine 90 m terrain result. "
            "Refine explicitly before comparing with the existing 90 m index."
        ),
    }


@router.get("/cross-body", response_model=CrossBodyGateOut, responses=ERROR_RESPONSES)
def global_cross_body_gate(request: Request) -> dict[str, object]:
    """Report the cross-body eligibility gate without ever emitting a cross-body score."""
    directory = resolve_repo_path(request.app.state.settings.planetary_coarse_dir)
    index_path = directory / "index.json"
    if not index_path.is_file():
        return build_cross_body_gate({}, {})
    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AppError(
            "cross_body_evidence_invalid",
            f"Planetary coarse index {index_path} could not be read: {exc}",
            status_code=409,
        ) from exc
    if not isinstance(index, dict):
        raise AppError(
            "cross_body_evidence_invalid",
            "Planetary coarse index must be a JSON object.",
            status_code=409,
        )
    manifests: dict[str, dict[str, object]] = {}
    for pilot in index.get("pilots", []):
        if not isinstance(pilot, dict):
            continue
        reference = pilot.get("manifest")
        if not isinstance(reference, str) or not reference:
            continue
        manifest_path = Path(reference)
        if not manifest_path.is_absolute():
            manifest_path = resolve_repo_path(reference)
        if not manifest_path.is_file():
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise AppError(
                "cross_body_evidence_invalid",
                f"Planetary coarse manifest {reference} could not be read: {exc}",
                status_code=409,
            ) from exc
        if isinstance(manifest, dict):
            manifests[reference] = manifest
    return build_cross_body_gate(index, manifests)


@router.get("/candidates/{candidate_id}", responses=ERROR_RESPONSES)
def global_candidate(candidate_id: str, request: Request) -> dict[str, object]:
    repo = _repo(request)
    candidate = repo.get(candidate_id)
    if candidate is None:
        raise AppError(
            "not_found", f"Coarse candidate {candidate_id} was not found.", status_code=404
        )
    return candidate


@router.post("/refine-plan", response_model=RefinePlanOut, responses=ERROR_RESPONSES)
def refine_plan(payload: RefinePlanRequest, request: Request) -> dict[str, object]:
    repo = _repo(request)
    candidate = repo.get(payload.candidate_id)
    if candidate is None:
        raise AppError(
            "not_found", f"Coarse candidate {payload.candidate_id} was not found.", status_code=404
        )
    try:
        windows = plan_refine_windows(
            parent_candidate_id=payload.candidate_id,
            parent_bbox_wgs84=candidate["bbox"],
            parent_crs="EPSG:4326",
            per_side=payload.per_side,
        )
    except Exception as exc:  # noqa: BLE001 - convert comparability/geometry errors to 422
        raise AppError("refine_plan_invalid", str(exc), status_code=422) from exc
    return {
        "candidate_id": payload.candidate_id,
        "parent_method_version": candidate["method_version"],
        "fine_method_version": "terrain-metrics-1.0.0",
        "windows": [asdict(window) for window in windows],
        "note": (
            "Plan only. The existing 90 m AOI pipeline must be invoked separately with explicit "
            "authorization; coarse values are never relabeled as fine metrics."
        ),
    }


@router.post(
    "/refine",
    response_model=GlobalRefineResponse,
    responses=ERROR_RESPONSES,
)
def refine_candidate(
    payload: GlobalRefineRequest,
    request: Request,
    session: Session = Depends(get_session),
) -> dict[str, object]:
    """Execute one centered 1.98 km refinement through the durable Earth AOI queue.

    The coarse candidate remains coarse provenance. The returned area is a real 90 m
    on-demand AOI persisted by the normal job runner, so it is immediately queryable
    through ``/locations/{area_id}`` and participates in the standard search index.
    Coarse values are never relabeled as fine metrics.
    """
    repo = _repo(request)
    candidate = repo.get(payload.candidate_id)
    if candidate is None:
        raise AppError(
            "not_found", f"Coarse candidate {payload.candidate_id} was not found.", status_code=404
        )
    runner = getattr(request.app.state, "job_runner", None)
    if runner is None:
        raise AppError(
            "service_unavailable", "The AOI job runner is not configured.", status_code=503
        )
    requested = tuple(payload.requested_features or list(FEATURE_BY_ID))
    unknown = sorted(set(requested) - set(FEATURE_BY_ID))
    if unknown:
        raise AppError(
            "unknown_feature",
            "Refinement only accepts fine similarity feature ids; unsupported: "
            + ", ".join(unknown),
            status_code=422,
        )
    try:
        focus = derive_focus_window(candidate)
    except Exception as exc:  # noqa: BLE001 - convert report/geometry errors to 422
        raise AppError("refine_focus_invalid", str(exc), status_code=422) from exc
    bbox = BBox(
        **{key: float(focus.focus_bbox_wgs84[key]) for key in ("west", "south", "east", "north")}
    )
    lons = [bbox.west, bbox.east, bbox.east, bbox.west]
    lats = [bbox.south, bbox.south, bbox.north, bbox.north]
    area_km2 = abs(EARTH_GEOD.polygon_area_perimeter(lons, lats)[0]) / 1e6
    area_id = refine_area_id(focus, coarse_snapshot_id=repo.snapshot_id)
    cache_descriptor = {
        "kind": "coarse_to_fine_refine",
        "coarse_candidate_id": payload.candidate_id,
        "coarse_snapshot_id": repo.snapshot_id,
        "coarse_method_version": candidate["method_version"],
        "focus_bbox_wgs84": focus.focus_bbox_wgs84,
        "focus_placement_rule": FOCUS_PLACEMENT_RULE,
        "focus_window_m": FOCUS_WINDOW_M,
        "requested_features": sorted(requested),
        "fine_method_version": METHOD_VERSION,
    }
    cache_key = hashlib.sha256(
        json.dumps(cache_descriptor, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    validated = ValidatedAoi(
        body="Earth",
        bbox=bbox,
        geometry=None,
        area_km2=round(area_km2, 6),
        requested_features=requested,
        reference_id=None,
        idempotency_key=payload.idempotency_key,
        area_id=area_id,
        cache_key=cache_key,
        width_m=focus.window_m,
        height_m=focus.window_m,
    )
    request_payload = {
        "body": "Earth",
        "bbox": bbox.as_dict(),
        "geometry": None,
        "area_km2": validated.area_km2,
        "requested_features": list(validated.requested_features),
        "reference_id": None,
        "area_id": area_id,
        "cache_key": cache_key,
        "width_m": validated.width_m,
        "height_m": validated.height_m,
    }
    store = JobStore(session)
    job, created = store.create_or_get(
        cache_key=cache_key,
        idempotency_key=payload.idempotency_key,
        request_payload=request_payload,
    )
    if job.area_id is None:
        job.area_id = area_id
        session.commit()
    if created:
        runner.submit(job.job_id)
    if getattr(runner, "inline", False):
        session.expire_all()
        job = session.get(AnalysisJob, job.job_id)
    return {
        "job_id": job.job_id,
        "status": job.status,
        "area_id": area_id,
        "candidate_id": area_id,
        "coarse_candidate_id": payload.candidate_id,
        "coarse_region_id": candidate["region_id"],
        "coarse_method_version": candidate["method_version"],
        "fine_method_version": METHOD_VERSION,
        "focus_window": {
            "window_m": focus.window_m,
            "placement_rule": focus.placement_rule,
            "bbox_wgs84": focus.focus_bbox_wgs84,
            "center": {"lon": focus.center_lon, "lat": focus.center_lat},
            "coarse_crs": focus.coarse_crs,
        },
        "cached": not created,
        "provider_status": job.provider_status or {},
        "result": job.result_json,
        "links": {
            "job": f"/api/v1/jobs/{job.job_id}",
            "location": f"/api/v1/locations/{area_id}",
            "features": f"/api/v1/areas/{area_id}/features",
            "compare": f"/api/v1/areas/{area_id}/compare",
        },
        "note": (
            "Executed through the bounded 90 m AOI provider. Coarse and fine values use "
            "different method/support and are never merged into one score. The persisted "
            "on-demand area participates in the normal Earth search index."
        ),
    }
