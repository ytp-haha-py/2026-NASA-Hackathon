"""Versioned, non-exclusive geology legend/category registry.

Raw source codes are dataset-scoped. A code is resolved only against its own
dataset entry; there is no global code table and no cross-body equivalence.
Unknown codes stay explicit instead of being guessed from a similar code.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

PROCESS_CATEGORIES = ("igneous", "impact", "sedimentary", "aqueous_alteration")
EVIDENCE_BASES = {"mapped_geology", "documented_interpretation"}
CONFIDENCES = {"mapped", "interpreted"}

MappingStatus = Literal[
    "mapped_with_process_evidence",
    "mapped_without_process_evidence",
    "unmapped_code",
]

# Aqueous alteration requires explicit documented alteration evidence. Iron
# oxides or spectral indicators on their own are never enough.
POSITIVE_AQUEOUS_PATTERNS = (
    "aqueous alteration",
    "aqueous weathering",
    "aqueous process",
    "aqueous mineral",
    "hydrated sulfate",
    "hydrated mineral",
    "hydrated phase",
    "hydrous mineral",
    "clay mineral",
    "clay-bearing",
    "clay bearing",
    "phyllosilicate",
    "alteration assemblage",
    "alteration mineral",
    "hydration",
)
NEGATION_MARKERS = (
    "no ",
    "not ",
    "without ",
    "insufficient",
    "lacks ",
    "absent ",
    "never ",
    "nor ",
    "cannot ",
    "does not ",
)
REGISTRY_VERSION = re.compile(r"^geology-legend-registry-\d+\.\d+\.\d+$")


def documented_aqueous_evidence(text: str) -> bool:
    """True only for a positive, documented aqueous-alteration statement.

    Negated mentions ("no aqueous alteration") and spectral-only indicators do
    not count as documented alteration evidence.
    """

    lowered = text.lower()
    for pattern in POSITIVE_AQUEOUS_PATTERNS:
        start = 0
        while (index := lowered.find(pattern, start)) != -1:
            prefix = lowered[max(0, index - 24) : index]
            if not any(marker in prefix for marker in NEGATION_MARKERS):
                return True
            start = index + 1
    return False


class LegendRegistryError(ValueError):
    """Raised when the committed legend registry is inconsistent or unsafe."""


@dataclass(frozen=True)
class ProcessLabel:
    category: str
    process: str
    evidence_basis: str
    confidence: str
    supporting_statement: str
    source_document: str


@dataclass(frozen=True)
class UnitMapping:
    dataset_id: str
    body: str
    raw_code: str
    status: MappingStatus
    unit_name: str | None
    official_family: str | None
    process_labels: tuple[ProcessLabel, ...]
    rock_labels: tuple[str, ...] = ()
    notes: str = ""
    registry_version: str = ""

    @property
    def categories(self) -> tuple[str, ...]:
        return tuple(sorted({label.category for label in self.process_labels}))

    @property
    def unknown_categories(self) -> tuple[str, ...]:
        supported = set(self.categories)
        return tuple(c for c in PROCESS_CATEGORIES if c not in supported)


def load_registry(path: str | Path) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return validate_registry(data)


def validate_registry(data: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise LegendRegistryError("registry must be a JSON object")
    version = data.get("registry_version")
    if not isinstance(version, str) or not REGISTRY_VERSION.match(version):
        raise LegendRegistryError("registry_version must be geology-legend-registry-X.Y.Z")
    if data.get("nonexclusive") is not True:
        raise LegendRegistryError("registry must declare nonexclusive: true")
    categories = data.get("categories")
    if not isinstance(categories, dict) or set(categories) != set(PROCESS_CATEGORIES):
        raise LegendRegistryError("registry must define exactly the four process categories")
    datasets = data.get("datasets")
    if not isinstance(datasets, dict) or not datasets:
        raise LegendRegistryError("registry requires at least one dataset legend")
    for dataset_id, dataset in datasets.items():
        if not isinstance(dataset, dict):
            raise LegendRegistryError(f"{dataset_id}: dataset entry must be an object")
        body = dataset.get("body")
        if body not in {"Earth", "Moon", "Mars"}:
            raise LegendRegistryError(f"{dataset_id}: invalid body {body!r}")
        units = dataset.get("units")
        if not isinstance(units, dict) or not units:
            raise LegendRegistryError(f"{dataset_id}: units must be a non-empty object")
        for code, unit in units.items():
            if not isinstance(code, str) or not code.strip():
                raise LegendRegistryError(f"{dataset_id}: empty raw unit code")
            if not isinstance(unit, dict):
                raise LegendRegistryError(f"{dataset_id}/{code}: unit must be an object")
            labels = unit.get("process_labels")
            if not isinstance(labels, list):
                raise LegendRegistryError(f"{dataset_id}/{code}: process_labels must be a list")
            seen = set()
            for label in labels:
                _validate_label(dataset_id, code, unit, label)
                key = (label["category"], label["process"])
                if key in seen:
                    raise LegendRegistryError(f"{dataset_id}/{code}: duplicate process label {key}")
                seen.add(key)
            rocks = unit.get("rock_labels", [])
            if not isinstance(rocks, list) or any(not isinstance(r, str) or not r for r in rocks):
                raise LegendRegistryError(f"{dataset_id}/{code}: rock_labels must be strings")
    return data


def _validate_label(dataset_id: str, code: str, unit: dict, label: Any) -> None:
    where = f"{dataset_id}/{code}"
    if not isinstance(label, dict):
        raise LegendRegistryError(f"{where}: process label must be an object")
    category = label.get("category")
    if category not in PROCESS_CATEGORIES:
        raise LegendRegistryError(f"{where}: unknown process category {category!r}")
    for key in ("process", "supporting_statement", "source_document"):
        value = label.get(key)
        if not isinstance(value, str) or not value.strip():
            raise LegendRegistryError(f"{where}: process label requires non-empty {key}")
    if label.get("evidence_basis") not in EVIDENCE_BASES:
        raise LegendRegistryError(f"{where}: invalid evidence_basis")
    if label.get("confidence") not in CONFIDENCES:
        raise LegendRegistryError(f"{where}: invalid confidence")
    if category == "aqueous_alteration":
        text = " ".join(
            [
                label["supporting_statement"],
                str(unit.get("unit_name", "")),
                str(unit.get("notes", "")),
            ]
        )
        if not documented_aqueous_evidence(text):
            raise LegendRegistryError(
                f"{where}: aqueous_alteration requires a positive, documented alteration "
                "statement; iron oxides or spectral indicators alone are insufficient"
            )


def resolve_unit(registry: dict[str, Any], dataset_id: str, raw_code: str) -> UnitMapping:
    """Resolve one raw code inside its own dataset legend; never across bodies."""

    if dataset_id not in registry.get("datasets", {}):
        raise LegendRegistryError(f"no legend registered for dataset {dataset_id!r}")
    dataset = registry["datasets"][dataset_id]
    body = dataset["body"]
    unit = dataset["units"].get(raw_code)
    version = registry["registry_version"]
    if unit is None:
        return UnitMapping(
            dataset_id=dataset_id,
            body=body,
            raw_code=raw_code,
            status="unmapped_code",
            unit_name=None,
            official_family=None,
            process_labels=(),
            notes=(
                f"Raw code {raw_code!r} is not mapped for {dataset_id}; it is not guessed from "
                "another dataset or body."
            ),
            registry_version=version,
        )
    labels = tuple(
        ProcessLabel(
            category=label["category"],
            process=label["process"],
            evidence_basis=label["evidence_basis"],
            confidence=label["confidence"],
            supporting_statement=label["supporting_statement"],
            source_document=label["source_document"],
        )
        for label in unit["process_labels"]
    )
    status: MappingStatus = (
        "mapped_with_process_evidence" if labels else "mapped_without_process_evidence"
    )
    return UnitMapping(
        dataset_id=dataset_id,
        body=body,
        raw_code=raw_code,
        status=status,
        unit_name=unit.get("unit_name"),
        official_family=unit.get("official_family"),
        process_labels=labels,
        rock_labels=tuple(unit.get("rock_labels", [])),
        notes=unit.get("notes", ""),
        registry_version=version,
    )


def registry_dataset_ids(registry: dict[str, Any]) -> tuple[str, ...]:
    return tuple(sorted(registry.get("datasets", {})))


def unit_codes(registry: dict[str, Any], dataset_id: str) -> tuple[str, ...]:
    return tuple(sorted(registry["datasets"][dataset_id]["units"]))


__all__ = [
    "LegendRegistryError",
    "PROCESS_CATEGORIES",
    "ProcessLabel",
    "UnitMapping",
    "documented_aqueous_evidence",
    "load_registry",
    "registry_dataset_ids",
    "resolve_unit",
    "unit_codes",
    "validate_registry",
]
