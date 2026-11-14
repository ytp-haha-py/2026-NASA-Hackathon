from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from analog_api.adapters.db import (
    Candidate,
    ContextObservation,
    Dataset,
    FeatureObservation,
    PlanetaryReference,
    ReferenceFeatureObservation,
)
from analog_api.domain.entities import (
    BBox,
    CandidateEntity,
    ContextValue,
    DatasetEntity,
    FeatureValue,
    ReferenceEntity,
)


def _feature_map(observations: Sequence[FeatureObservation | ReferenceFeatureObservation]):
    return {
        obs.feature_id: FeatureValue(
            value=obs.value,
            unit=obs.unit,
            method_version=obs.method_version,
            extraction_scale_m=obs.extraction_scale_m,
            valid_fraction=obs.valid_fraction,
            quality=obs.quality,
        )
        for obs in observations
    }


def _dataset(entity: Dataset) -> DatasetEntity:
    return DatasetEntity(
        dataset_id=entity.dataset_id,
        provider=entity.provider,
        product_id=entity.product_id,
        title=entity.title,
        url=entity.url,
        license=entity.license,
        license_url=entity.license_url,
        access_timestamp=entity.access_timestamp,
        source_crs=entity.source_crs,
        native_resolution_m=entity.native_resolution_m,
        processing_version=entity.processing_version,
        checksum=entity.checksum,
        source_kind=entity.source_kind,
        notes=entity.notes,
    )


def _context_map(observations: Sequence[ContextObservation]):
    return {
        obs.feature_id: ContextValue(
            value=obs.value,
            unit=obs.unit,
            method_version=obs.method_version,
            source_resolution_m=obs.source_resolution_m,
            valid_fraction=obs.valid_fraction,
            quality=obs.quality,
            class_code=obs.class_code,
            provider=obs.provider,
            dataset_id=obs.dataset_id,
        )
        for obs in observations
    }


def _candidate(entity: Candidate) -> CandidateEntity:
    return CandidateEntity(
        location_id=entity.location_id,
        display_name=entity.display_name,
        lat=entity.lat,
        lon=entity.lon,
        bbox=BBox(west=entity.west, south=entity.south, east=entity.east, north=entity.north),
        dataset_id=entity.dataset_id,
        window_size_m=entity.window_size_m,
        analysis_resolution_m=entity.analysis_resolution_m,
        crs=entity.crs,
        valid_fraction=entity.valid_fraction,
        quality_flags=tuple(entity.quality_flags or []),
        notes=entity.notes,
        data_kind=entity.data_kind,
        area_km2=entity.area_km2,
        geometry=entity.geometry,
        origin=entity.origin,
        features=_feature_map(entity.features),
        context_observations=_context_map(entity.context_observations),
    )


def _reference(entity: PlanetaryReference) -> ReferenceEntity:
    bbox = None
    if None not in (entity.west, entity.south, entity.east, entity.north):
        bbox = BBox(west=entity.west, south=entity.south, east=entity.east, north=entity.north)
    return ReferenceEntity(
        reference_id=entity.reference_id,
        body=entity.body,
        display_name=entity.display_name,
        dataset_id=entity.dataset_id,
        lat=entity.lat,
        lon=entity.lon,
        bbox=bbox,
        window_size_m=entity.window_size_m,
        analysis_resolution_m=entity.analysis_resolution_m,
        crs=entity.crs,
        valid_fraction=entity.valid_fraction,
        quality_flags=tuple(entity.quality_flags or []),
        notes=entity.notes,
        features=_feature_map(entity.features),
    )


class SqlAlchemyRepository:
    """SQLite-backed adapter. Swap this class for PostGIS without touching the API."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def list_candidates(self) -> Sequence[CandidateEntity]:
        rows = self.session.scalars(select(Candidate).order_by(Candidate.location_id)).all()
        return [_candidate(row) for row in rows]

    def get_candidate(self, location_id: str) -> CandidateEntity | None:
        row = self.session.get(Candidate, location_id)
        return _candidate(row) if row else None

    def candidate_count(self) -> int:
        return self.session.query(Candidate).count()

    def data_version(self) -> str:
        from analog_api.adapters.db import Meta

        meta = self.session.get(Meta, "data_version")
        return meta.value if meta else "dv-empty"

    def list_datasets(self) -> Sequence[DatasetEntity]:
        rows = self.session.scalars(select(Dataset).order_by(Dataset.dataset_id)).all()
        return [_dataset(row) for row in rows]

    def get_dataset(self, dataset_id: str) -> DatasetEntity | None:
        row = self.session.get(Dataset, dataset_id)
        return _dataset(row) if row else None

    def list_references(self) -> Sequence[ReferenceEntity]:
        rows = self.session.scalars(
            select(PlanetaryReference).order_by(PlanetaryReference.reference_id)
        ).all()
        return [_reference(row) for row in rows]

    def get_reference(self, reference_id: str) -> ReferenceEntity | None:
        row = self.session.get(PlanetaryReference, reference_id)
        return _reference(row) if row else None
