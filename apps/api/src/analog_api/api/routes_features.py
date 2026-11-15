"""Feature-discovery and multi-dimensional search endpoints (Issue #116)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from analog_api.adapters.typed_db import TypedObservationStore
from analog_api.api.routes import ERROR_RESPONSES, get_repo, get_session
from analog_api.domain.eligibility import QUERY_ROLES
from analog_api.domain.errors import AppError
from analog_api.domain.feature_service import (
    RegistrySnapshot,
    assemble_snapshot,
    execute_multidimensional_search,
    feature_detail_payload,
    registry_payload,
)
from analog_api.ports.repository import CandidateRepositoryFull
from analog_api.schemas import (
    FeatureDetailOut,
    FeatureRegistryOut,
    MultiDimSearchRequest,
    MultiDimSearchResponse,
)

router = APIRouter(prefix="/api/v1/search", tags=["six-dimension"])


def build_snapshot(session: Session, repo: CandidateRepositoryFull) -> RegistrySnapshot:
    typed = TypedObservationStore(session)
    return assemble_snapshot(
        rows=typed.list_observations(),
        areas=typed.list_areas(),
        candidates=repo.list_candidates(),
        references=repo.list_references(),
    )


@router.get("/features", response_model=FeatureRegistryOut)
def list_features(
    body: str | None = None,
    dimension: str | None = None,
    role: str | None = None,
    session: Session = Depends(get_session),
    repo: CandidateRepositoryFull = Depends(get_repo),
) -> dict[str, object]:
    if role is not None and role not in QUERY_ROLES:
        raise AppError(
            "invalid_role",
            "role must be one of: " + ", ".join(QUERY_ROLES) + ".",
            status_code=422,
        )
    snapshot = build_snapshot(session, repo)
    return registry_payload(
        snapshot,
        body=body,
        dimension=dimension,
        role=role,
        data_version=repo.data_version(),
    )


@router.get("/features/{feature_id}", response_model=FeatureDetailOut, responses=ERROR_RESPONSES)
def feature_detail(
    feature_id: str,
    session: Session = Depends(get_session),
    repo: CandidateRepositoryFull = Depends(get_repo),
) -> dict[str, object]:
    snapshot = build_snapshot(session, repo)
    return feature_detail_payload(snapshot, feature_id, data_version=repo.data_version())


@router.post(
    "/multidimensional",
    response_model=MultiDimSearchResponse,
    responses=ERROR_RESPONSES,
)
def multidimensional_search(
    request: MultiDimSearchRequest,
    session: Session = Depends(get_session),
    repo: CandidateRepositoryFull = Depends(get_repo),
) -> dict[str, Any]:
    snapshot = build_snapshot(session, repo)
    raw_targets = {
        feature_id: (spec.value, spec.weight) for feature_id, spec in request.targets.items()
    }
    filters = {
        feature_id: {"min": spec.min, "max": spec.max}
        for feature_id, spec in request.filters.items()
    }
    return execute_multidimensional_search(
        repo=repo,
        snapshot=snapshot,
        raw_targets=raw_targets,
        filters=filters,
        reference_id=request.reference_id,
        candidate_body=request.candidate_body,
        limit=request.limit,
        offset=request.offset,
        include_evidence=request.include_evidence,
        evidence_limit=request.evidence_limit,
    )
