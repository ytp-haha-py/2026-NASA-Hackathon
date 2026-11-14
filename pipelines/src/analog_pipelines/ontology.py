"""Versioned six-dimension ontology and legacy feature mapping (Issue #25).

The formal dimension names/research questions come from Issue #20. Existing
feature IDs and method versions are not changed; this layer only classifies them.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from analog_api.domain.features import CONTEXT_FEATURES, FEATURES
from pydantic import BaseModel, ConfigDict, Field, model_validator

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_ONTOLOGY = REPO_ROOT / "contracts" / "ontology-v1.json"

DIMENSION_NAMES = {
    "D01": "Terrain & Landforms",
    "D02": "Geology & Mineralogy",
    "D03": "Thermal Environment",
    "D04": "Water & Ice",
    "D05": "Solar Illumination & Radiation",
    "D06": "Regolith & Surface Properties",
}
D02_CLASSES = {
    "igneous_geology",
    "impact_geology",
    "sedimentary_geology",
    "aqueous_alteration",
}
D05_SUBGROUPS = {"solar_illumination", "ionizing_radiation"}

ScoreRole = Literal["similarity", "context_only", "filter", "evidence_only", "not_comparable"]
Eligibility = Literal["same_method_and_support", "context_only", "evidence_only", "not_comparable"]


class DimensionDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str
    name: str
    research_question: str
    subgroups: list[str] = Field(default_factory=list)
    research_classes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def identity(self) -> DimensionDefinition:
        if self.code not in DIMENSION_NAMES:
            raise ValueError(f"unknown dimension code {self.code}")
        if self.name != DIMENSION_NAMES[self.code]:
            raise ValueError(f"{self.code}: name must be {DIMENSION_NAMES[self.code]!r}")
        return self


class FeatureMapping(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dimension_code: str | None
    legacy_dimension: str
    score_role: ScoreRole
    compare_eligibility: Eligibility
    value_kind: str
    earth_only: bool = False

    @model_validator(mode="after")
    def role_is_consistent(self) -> FeatureMapping:
        if (
            self.score_role == "similarity"
            and self.compare_eligibility != "same_method_and_support"
        ):
            raise ValueError("similarity features require same_method_and_support eligibility")
        if self.score_role == "context_only" and self.compare_eligibility != "context_only":
            raise ValueError("context features must not claim scored comparability")
        if self.score_role == "context_only" and self.dimension_code is not None:
            raise ValueError("land-cover context must not become a scored seventh dimension")
        return self


class Ontology(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ontology_version: str
    dimensions: list[DimensionDefinition]
    legacy_dimension_mapping: dict[str, str | None]
    feature_mappings: dict[str, FeatureMapping]
    notes: str

    @model_validator(mode="after")
    def complete(self) -> Ontology:
        codes = [dimension.code for dimension in self.dimensions]
        if codes != list(DIMENSION_NAMES):
            raise ValueError(f"dimensions must be {list(DIMENSION_NAMES)}")
        by_code = {dimension.code: dimension for dimension in self.dimensions}
        if set(by_code["D02"].research_classes) != D02_CLASSES:
            raise ValueError("D02 must retain the four non-exclusive research classes")
        if set(by_code["D05"].subgroups) != D05_SUBGROUPS:
            raise ValueError("D05 must separate illumination and ionizing radiation")
        expected = {feature.id for feature in FEATURES} | {
            feature.feature_id for feature in CONTEXT_FEATURES
        }
        if set(self.feature_mappings) != expected:
            missing = expected - set(self.feature_mappings)
            extra = set(self.feature_mappings) - expected
            raise ValueError(
                f"feature mapping mismatch; missing={sorted(missing)} extra={sorted(extra)}"
            )
        for feature_id, mapping in self.feature_mappings.items():
            if mapping.dimension_code is not None and mapping.dimension_code not in DIMENSION_NAMES:
                raise ValueError(f"{feature_id}: unknown dimension {mapping.dimension_code}")
            if (
                mapping.legacy_dimension == "land_cover_context"
                and mapping.dimension_code is not None
            ):
                raise ValueError(f"{feature_id}: land-cover context cannot be a scored dimension")
        return self


def load_ontology(path: str | Path = DEFAULT_ONTOLOGY) -> Ontology:
    return Ontology.model_validate(json.loads(Path(path).read_text(encoding="utf-8")))


def feature_dimension(ontology: Ontology, feature_id: str) -> DimensionDefinition | None:
    mapping = ontology.feature_mappings.get(feature_id)
    if mapping is None or mapping.dimension_code is None:
        return None
    return next(d for d in ontology.dimensions if d.code == mapping.dimension_code)


def legacy_dimension_for(feature_id: str, ontology: Ontology | None = None) -> str:
    ontology = ontology or load_ontology()
    return ontology.feature_mappings[feature_id].legacy_dimension


def scored_features(ontology: Ontology | None = None) -> list[str]:
    ontology = ontology or load_ontology()
    return [
        feature_id
        for feature_id, mapping in ontology.feature_mappings.items()
        if mapping.score_role == "similarity"
    ]
