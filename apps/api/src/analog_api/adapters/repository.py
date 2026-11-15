from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from analog_api.adapters.db import (
    Candidate,
    Dataset,
    Meta,
    PlanetaryReference,
)
from analog_api.adapters.mapping import (
    candidate_entity,
    dataset_entity,
    reference_entity,
)
from analog_api.domain.entities import CandidateEntity, DatasetEntity, ReferenceEntity


class SqlAlchemyRepository:
    """SQLite-backed adapter. Swap this class for PostGIS without touching the API."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def list_candidates(self) -> Sequence[CandidateEntity]:
        rows = self.session.scalars(select(Candidate).order_by(Candidate.location_id)).all()
        return [candidate_entity(row) for row in rows]

    def get_candidate(self, location_id: str) -> CandidateEntity | None:
        row = self.session.get(Candidate, location_id)
        return candidate_entity(row) if row else None

    def candidate_count(self) -> int:
        return self.session.query(Candidate).count()

    def data_version(self) -> str:
        meta = self.session.get(Meta, "data_version")
        return meta.value if meta else "dv-empty"

    def list_datasets(self) -> Sequence[DatasetEntity]:
        rows = self.session.scalars(select(Dataset).order_by(Dataset.dataset_id)).all()
        return [dataset_entity(row) for row in rows]

    def get_dataset(self, dataset_id: str) -> DatasetEntity | None:
        row = self.session.get(Dataset, dataset_id)
        return dataset_entity(row) if row else None

    def list_references(self) -> Sequence[ReferenceEntity]:
        rows = self.session.scalars(
            select(PlanetaryReference).order_by(PlanetaryReference.reference_id)
        ).all()
        return [reference_entity(row) for row in rows]

    def get_reference(self, reference_id: str) -> ReferenceEntity | None:
        row = self.session.get(PlanetaryReference, reference_id)
        return reference_entity(row) if row else None
