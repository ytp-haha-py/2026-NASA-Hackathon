from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from analog_api.adapters.postgis import repository_for_session
from analog_api.domain.features import FEATURES
from analog_api.domain.search import (
    capabilities_payload,
    context_payload,
    dimensions_payload,
    execute_search,
    location_payload,
)
from analog_api.ports.repository import CandidateRepositoryFull
from analog_api.schemas import (
    CapabilitiesOut,
    ContextOut,
    DatasetOut,
    DimensionsOut,
    ErrorOut,
    HealthOut,
    LocationOut,
    ReferenceOut,
    SearchRequest,
    SearchResponse,
)

router = APIRouter(prefix="/api/v1")

ERROR_RESPONSES = {
    404: {"model": ErrorOut},
    409: {"model": ErrorOut},
    422: {"model": ErrorOut},
}


def get_session(request: Request):
    factory = request.app.state.session_factory
    session: Session = factory()
    try:
        yield session
    finally:
        session.close()


def get_repo(session: Session = Depends(get_session)) -> CandidateRepositoryFull:
    return repository_for_session(session)


def _reference_payload(reference) -> dict[str, object]:
    features = []
    for feature in FEATURES:
        observation = reference.features.get(feature.id)
        if observation is None:
            continue
        features.append(
            {
                "feature_id": feature.id,
                "label": feature.label,
                "value": observation.value,
                "unit": observation.unit,
                "method_version": observation.method_version,
                "extraction_scale_m": observation.extraction_scale_m,
                "valid_fraction": observation.valid_fraction,
                "quality": observation.quality,
                "scale": feature.scale,
                "description": feature.description,
            }
        )
    return {
        "reference_id": reference.reference_id,
        "body": reference.body,
        "display_name": reference.display_name,
        "dataset_id": reference.dataset_id,
        "lat": reference.lat,
        "lon": reference.lon,
        "bbox": reference.bbox.as_dict() if reference.bbox else None,
        "window_size_m": reference.window_size_m,
        "analysis_resolution_m": reference.analysis_resolution_m,
        "crs": reference.crs,
        "valid_fraction": reference.valid_fraction,
        "quality_flags": list(reference.quality_flags),
        "notes": reference.notes,
        "features": features,
    }


@router.get("/health", response_model=HealthOut)
def health(repo: CandidateRepositoryFull = Depends(get_repo)) -> dict[str, str]:
    version = repo.data_version()
    return {
        "status": "ok",
        "data_version": version,
        "data_snapshot_id": version,
    }


@router.get("/dimensions", response_model=DimensionsOut)
def dimensions() -> dict[str, object]:
    return dimensions_payload()


@router.get("/capabilities", response_model=CapabilitiesOut)
def capabilities(repo: CandidateRepositoryFull = Depends(get_repo)) -> dict[str, object]:
    return capabilities_payload(repo)


@router.get("/datasets", response_model=list[DatasetOut])
def datasets(
    response: Response, repo: CandidateRepositoryFull = Depends(get_repo)
) -> list[dict[str, object]]:
    response.headers["X-Data-Snapshot-ID"] = repo.data_version()
    return [
        {
            "dataset_id": dataset.dataset_id,
            "provider": dataset.provider,
            "product_id": dataset.product_id,
            "title": dataset.title,
            "url": dataset.url,
            "license": dataset.license,
            "license_url": dataset.license_url,
            "access_timestamp": dataset.access_timestamp,
            "source_crs": dataset.source_crs,
            "native_resolution_m": dataset.native_resolution_m,
            "processing_version": dataset.processing_version,
            "checksum": dataset.checksum,
            "source_kind": dataset.source_kind,
            "notes": dataset.notes,
        }
        for dataset in repo.list_datasets()
    ]


@router.get("/references", response_model=list[ReferenceOut])
def references(repo: CandidateRepositoryFull = Depends(get_repo)) -> list[dict[str, object]]:
    return [_reference_payload(reference) for reference in repo.list_references()]


@router.post("/search", response_model=SearchResponse, responses=ERROR_RESPONSES)
def search(
    request: SearchRequest, repo: CandidateRepositoryFull = Depends(get_repo)
) -> dict[str, object]:
    return execute_search(repo, request)


@router.get("/locations/{location_id}", response_model=LocationOut, responses=ERROR_RESPONSES)
def location(
    location_id: str, repo: CandidateRepositoryFull = Depends(get_repo)
) -> dict[str, object]:
    return location_payload(repo, location_id)


@router.get(
    "/locations/{location_id}/context", response_model=ContextOut, responses=ERROR_RESPONSES
)
def location_context(
    location_id: str,
    target: str | None = None,
    repo: CandidateRepositoryFull = Depends(get_repo),
) -> dict[str, object]:
    return context_payload(repo, location_id, target)
