from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from analog_pipelines.coarse_features import (
    COARSE_FEATURES,
    CoarseExtractionError,
)
from analog_pipelines.coarse_index import INDEX_VERSION, CoarseIndexRepository, IndexError_
from analog_pipelines.coarse_reference import plan_refine_windows
from fastapi import APIRouter, Request

from analog_api.config import resolve_repo_path
from analog_api.domain.errors import AppError
from analog_api.schemas import (
    ErrorOut,
    GlobalCoverageOut,
    GlobalSearchRequest,
    GlobalSearchResponse,
    RefinePlanOut,
    RefinePlanRequest,
)

router = APIRouter(prefix="/api/v1/global")
ERROR_RESPONSES = {404: {"model": ErrorOut}, 409: {"model": ErrorOut}, 422: {"model": ErrorOut}}


def _index_root(request: Request) -> Path:
    return resolve_repo_path(request.app.state.settings.global_index_dir)


def _repo(request: Request) -> CoarseIndexRepository:
    root = _index_root(request)
    if not (root / "manifest.json").is_file():
        raise AppError(
            "no_indexed_data",
            "No verified coarse Parquet index is configured. Global discovery is not indexed "
            "in this deployment; use the existing indexed-subset search or build an authorized "
            "offline fixture index.",
            status_code=409,
        )
    try:
        return CoarseIndexRepository(root)
    except IndexError_ as exc:
        raise AppError("index_invalid", str(exc), status_code=409) from exc


@router.get("/coverage", response_model=GlobalCoverageOut)
def global_coverage(request: Request) -> dict[str, object]:
    root = _index_root(request)
    if not (root / "manifest.json").is_file():
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
