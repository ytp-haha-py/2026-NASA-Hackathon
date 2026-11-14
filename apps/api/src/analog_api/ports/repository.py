"""Narrow, typed interfaces between the API/domain and storage or ingestion concerns."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from analog_api.domain.entities import CandidateEntity, DatasetEntity, ReferenceEntity


class CandidateRepository(Protocol):
    def list_candidates(self) -> Sequence[CandidateEntity]: ...

    def get_candidate(self, location_id: str) -> CandidateEntity | None: ...

    def candidate_count(self) -> int: ...

    def data_version(self) -> str: ...


class DatasetRepository(Protocol):
    def list_datasets(self) -> Sequence[DatasetEntity]: ...

    def get_dataset(self, dataset_id: str) -> DatasetEntity | None: ...


class PlanetaryReferenceProvider(Protocol):
    def list_references(self) -> Sequence[ReferenceEntity]: ...

    def get_reference(self, reference_id: str) -> ReferenceEntity | None: ...


class CandidateRepositoryFull(
    CandidateRepository, DatasetRepository, PlanetaryReferenceProvider, Protocol
):
    """Composite used by the API; storage technology remains swappable."""
