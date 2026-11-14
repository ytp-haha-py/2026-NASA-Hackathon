"""Opt-in extension points.

These Protocols are intentionally narrow. They document where future work plugs in without
forcing a framework: a different ranking model, a new raster/feature extractor, or a source of
grounded limitations/evidence.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol

from analog_api.domain.entities import CandidateEntity, ReferenceEntity
from analog_api.domain.scoring import RankedMatch, ScoringCandidate


class RankingStrategy(Protocol):
    """Replace weighted normalized Euclidean without touching HTTP or storage."""

    name: str
    version: str

    def rank(
        self,
        *,
        target: Mapping[str, float],
        weights: Mapping[str, float],
        candidates: Sequence[ScoringCandidate],
        limit: int,
    ) -> Sequence[RankedMatch]: ...


class FeatureExtractor(Protocol):
    """Adapter boundary for local-raster -> persisted observation extraction."""

    method_version: str
    extraction_scale_m: float

    def extract(
        self, source_uri: str, *, center_lonlat: tuple[float, float]
    ) -> dict[str, float]: ...


class PlanetaryReferenceProvider(Protocol):
    def list_references(self) -> Sequence[ReferenceEntity]: ...

    def get_reference(self, reference_id: str) -> ReferenceEntity | None: ...


class EvidenceProvider(Protocol):
    """Return grounded, source-linked caveats; never generated narrative."""

    def caveats(self, candidate: CandidateEntity) -> Sequence[str]: ...

    def limitations(self) -> Sequence[str]: ...
