"""Machine-readable six-dimension source inventory (Issue #24).

The inventory is deliberately separate from observed manifests. It records what is
verified, what is merely a candidate, and what is unknown; it never turns a planned
source into observations.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_INVENTORY = REPO_ROOT / "catalog" / "dimension-source-inventory.yaml"

DIMENSION_NAMES = {
    "D01": "Terrain & Landforms",
    "D02": "Geology & Mineralogy",
    "D03": "Thermal Environment",
    "D04": "Water & Ice",
    "D05": "Solar Illumination & Radiation",
    "D06": "Regolith & Surface Properties",
}
BODIES = ("Earth", "Moon", "Mars")
D02_CLASSES = {
    "igneous_geology",
    "impact_geology",
    "sedimentary_geology",
    "aqueous_alteration",
}
D05_SUBGROUPS = {"solar_illumination", "ionizing_radiation"}

Availability = Literal[
    "verified", "accessible", "identified", "planned", "unknown", "not_applicable"
]
DataStatus = Literal["existing_observed", "existing_modeled", "not_fetched", "unknown"]
Comparability = Literal["comparable", "context_only", "evidence_only", "not_comparable", "unknown"]


class SourceEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str
    provider: str
    product: str
    url: str
    license: str
    license_url: str
    native_resolution: str
    spatial_support: str
    temporal_support: str
    depth_support: str
    observation_kinds: list[str] = Field(min_length=1)
    qa: str
    availability: Availability
    data_status: DataStatus
    comparability: Comparability
    notes: str = ""
    unknown_fields: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def verified_requires_provenance(self) -> SourceEntry:
        if self.availability in {"verified", "accessible"}:
            for field in ("url", "license", "license_url"):
                if getattr(self, field).strip().lower() in {"", "not_verified", "unknown"}:
                    raise ValueError(
                        f"{self.source_id}: {field} must be verified before availability="
                        f"{self.availability}"
                    )
            if self.unknown_fields:
                raise ValueError(
                    f"{self.source_id}: verified/accessible source still lists unknown fields "
                    f"{self.unknown_fields}"
                )
        if (
            self.availability == "planned"
            and self.product.strip() == "not_selected"
            and "product" not in self.unknown_fields
        ):
            raise ValueError(f"{self.source_id}: planned source must mark product unknown")
        return self


class BodyEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sources: list[SourceEntry] = Field(min_length=1)


class DimensionInventory(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    name: str
    research_question: str
    subgroups: list[str] = Field(default_factory=list)
    research_classes: list[str] = Field(default_factory=list)
    baseline: str
    bodies: dict[str, BodyEntry]

    @model_validator(mode="after")
    def exact_dimension_shape(self) -> DimensionInventory:
        if self.code not in DIMENSION_NAMES:
            raise ValueError(f"unknown dimension code {self.code}")
        if self.name != DIMENSION_NAMES[self.code]:
            raise ValueError(f"{self.code}: name must be {DIMENSION_NAMES[self.code]!r}")
        if set(self.bodies) != set(BODIES):
            raise ValueError(f"{self.code}: bodies must be exactly {BODIES}")
        if self.code == "D02" and set(self.research_classes) != D02_CLASSES:
            raise ValueError(f"D02 research classes must be {sorted(D02_CLASSES)}")
        if self.code == "D05" and set(self.subgroups) != D05_SUBGROUPS:
            raise ValueError("D05 must separate solar illumination from ionizing radiation")
        return self


class SourceInventory(BaseModel):
    model_config = ConfigDict(extra="forbid")

    inventory_version: str
    description: str
    status_legend: dict[str, object]
    dimensions: list[DimensionInventory]

    @model_validator(mode="after")
    def complete_inventory(self) -> SourceInventory:
        codes = [dimension.code for dimension in self.dimensions]
        if codes != list(DIMENSION_NAMES):
            raise ValueError(f"dimension order/content must be {list(DIMENSION_NAMES)}")
        return self


def load_inventory(path: str | Path = DEFAULT_INVENTORY) -> SourceInventory:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return SourceInventory.model_validate(raw)


def inventory_matrix(inventory: SourceInventory) -> list[dict[str, object]]:
    """Return a compact 18-row summary suitable for docs or an API."""
    rows: list[dict[str, object]] = []
    for dimension in inventory.dimensions:
        for body in BODIES:
            sources = dimension.bodies[body].sources
            rows.append(
                {
                    "dimension": dimension.code,
                    "name": dimension.name,
                    "body": body,
                    "sources": [source.source_id for source in sources],
                    "availability": sorted({source.availability for source in sources}),
                    "data_status": sorted({source.data_status for source in sources}),
                    "comparability": sorted({source.comparability for source in sources}),
                    "unknown_fields": sorted(
                        {field for source in sources for field in source.unknown_fields}
                    ),
                }
            )
    return rows
