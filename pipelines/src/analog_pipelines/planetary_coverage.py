"""Committed Moon/Mars science-coverage inventory and the Mars D01 case (Issue #132).

This module is storage-independent: it reads committed manifests, tiny elevation
fixtures and typed-v2 observation files. It never talks to a database, a web
service or an API schema. Every number it reports is derived from a committed
file or computed from a committed fixture; a missing file is an explicit error or
an explicit ``missing_files`` entry, never a zero.

Two rules are load-bearing:

* The Mars MOLA 463 m cells are ``not_comparable`` support evidence. They are
  summarised separately and are never merged into the CTX comparable aggregate.
* The inventory covers a bounded committed subset. It never claims global or
  body-wide coverage, and Earth-only SoilGrids predictions are never presented as
  Moon or Mars regolith measurements.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from analog_pipelines.planetary_coarse import PlanetaryCoarseError, verify_manifest

COVERAGE_INVENTORY_VERSION = "planetary-coverage-inventory-1.0.0"
COARSE_INDEX_SCHEMA_VERSION = "planetary-coarse-index-1.0.0"
DIMENSION_CODES = ("D01", "D02", "D03", "D04", "D05", "D06")
PLANETARY_BODIES = ("Moon", "Mars")
CLAIM_SCOPE = "bounded_committed_subset_not_global"
REPRODUCE_COMMAND = (
    "uv run python -m analog_pipelines.planetary_coverage "
    "--write docs/PLANETARY_COVERAGE_INVENTORY.json"
)
SOURCE_AUDITS = (
    "docs/D01_COVERAGE_AUDIT.md",
    "docs/D02_SOURCE_AUDIT.md",
    "docs/D03_D05_SOURCE_AUDIT.md",
    "docs/D04_D06_SOURCE_AUDIT.md",
    "docs/PLANETARY_COARSE_REFERENCE.md",
    "docs/PLANETARY_MAPS_API.md",
    "docs/PLANETARY_MAP_SOURCE_AUDIT.md",
    "catalog/dimension-source-inventory.yaml",
)
DEFAULT_REPO_ROOT = Path(__file__).resolve().parents[3]

_DIMENSION_PREFIX = re.compile(r"^(d0[1-6])(?:_|$)", re.IGNORECASE)
_EARTH_CRS_MARKERS = ("EPSG:4326", "WGS 84", "WGS84", "WSG84")


class PlanetaryCoverageError(RuntimeError):
    """A coverage claim could not be derived honestly from committed files."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class _CoveragePaths:
    repo_root: Path
    coarse_root: Path
    pilots_root: Path
    geology_typed_root: Path


# ---------------------------------------------------------------------------
# Path and JSON helpers
# ---------------------------------------------------------------------------


def _find_repo_root(coarse_root: Path) -> Path:
    for candidate in (coarse_root, *coarse_root.parents):
        if (candidate / "pipelines/planetary/coarse/index.json").is_file():
            return candidate
    return DEFAULT_REPO_ROOT


def _resolve_paths(root: str | Path | None, pilots_root: str | Path | None) -> _CoveragePaths:
    if root is None:
        repo_root = DEFAULT_REPO_ROOT
        coarse_root = repo_root / "pipelines/planetary/coarse"
    else:
        candidate = Path(root).expanduser()
        if candidate.is_file() and candidate.name == "index.json":
            coarse_root = candidate.parent
            repo_root = _find_repo_root(coarse_root)
        elif (candidate / "index.json").is_file():
            coarse_root = candidate
            repo_root = _find_repo_root(coarse_root)
        elif (candidate / "pipelines/planetary/coarse/index.json").is_file():
            repo_root = candidate
            coarse_root = candidate / "pipelines/planetary/coarse"
        else:
            repo_root = candidate
            coarse_root = candidate / "pipelines/planetary/coarse"

    custom_pilots_root = pilots_root is not None
    if not custom_pilots_root:
        pilots = repo_root / "pipelines/pilots"
        if not pilots.is_dir():
            pilots = DEFAULT_REPO_ROOT / "pipelines/pilots"
    else:
        pilots = Path(pilots_root).expanduser()

    geology = pilots.parent / "geology/typed"
    if not geology.is_dir() and not custom_pilots_root:
        geology = DEFAULT_REPO_ROOT / "pipelines/geology/typed"
    return _CoveragePaths(
        repo_root=repo_root,
        coarse_root=coarse_root,
        pilots_root=pilots,
        geology_typed_root=geology,
    )


def _load_json(path: Path, *, code: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise PlanetaryCoverageError(code, f"required file is missing: {path}") from exc
    except json.JSONDecodeError as exc:
        raise PlanetaryCoverageError(
            f"{code}_invalid_json", f"invalid JSON in {path}: {exc}"
        ) from exc


def _manifest_path(paths: _CoveragePaths, reference: str) -> Path:
    raw = Path(reference)
    candidates = (
        paths.repo_root / raw,
        paths.coarse_root / raw.name,
        raw,
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise PlanetaryCoverageError(
        "coarse_manifest_missing",
        f"coarse index references a manifest that is not committed: {reference}",
    )


def _require(condition: bool, code: str, message: str) -> None:
    if not condition:
        raise PlanetaryCoverageError(code, message)


def _relative(path: Path, repo_root: Path) -> str:
    try:
        return path.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _unique(values: Iterable[Any]) -> list[Any]:
    seen: set[Any] = set()
    result: list[Any] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


# ---------------------------------------------------------------------------
# Coarse index and per-body coarse summaries
# ---------------------------------------------------------------------------


def load_planetary_coarse_index(root: str | Path | None = None) -> dict[str, Any]:
    """Load and structurally validate the committed planetary coarse index.

    ``root`` may be the repository root, ``pipelines/planetary/coarse``, or the
    ``index.json`` file itself. Fixture checksums are verified by
    :func:`coarse_case_summary` and :func:`mars_d01_case`, not here, so a caller
    can inspect the index without reading every fixture.
    """
    paths = _resolve_paths(root, None)
    index_path = paths.coarse_root / "index.json"
    if not index_path.is_file():
        raise PlanetaryCoverageError(
            "coarse_index_missing",
            f"planetary coarse index is not committed at {index_path}",
        )
    payload = _load_json(index_path, code="coarse_index")
    _require(
        isinstance(payload, dict),
        "coarse_index_invalid",
        "the planetary coarse index must be a JSON object",
    )
    _require(
        payload.get("schema_version") == COARSE_INDEX_SCHEMA_VERSION,
        "unsupported_index_schema",
        f"unsupported coarse index schema {payload.get('schema_version')!r}; expected "
        f"{COARSE_INDEX_SCHEMA_VERSION}",
    )
    pilots = payload.get("pilots")
    _require(
        isinstance(pilots, list) and bool(pilots),
        "coarse_index_invalid",
        "the planetary coarse index must contain a non-empty pilots list",
    )
    gate = payload.get("cross_body_ranking")
    _require(
        isinstance(gate, dict),
        "coarse_index_invalid",
        "the planetary coarse index must contain a cross_body_ranking object",
    )
    _require(
        gate.get("enabled") is False,
        "cross_body_ranking_enabled",
        "cross-body ranking must stay disabled; the committed index enables it",
    )
    _require(
        isinstance(payload.get("method_version"), str),
        "coarse_index_invalid",
        "the planetary coarse index must declare method_version",
    )

    seen_manifests: set[str] = set()
    for entry in pilots:
        _require(
            isinstance(entry, dict),
            "coarse_index_invalid",
            "each coarse index pilot entry must be an object",
        )
        body = entry.get("body")
        _require(
            body in PLANETARY_BODIES,
            "unsupported_body",
            f"unsupported coarse index body {body!r}",
        )
        manifest_ref = entry.get("manifest")
        _require(
            isinstance(manifest_ref, str) and bool(manifest_ref),
            "coarse_index_invalid",
            f"coarse index {body} entry has no manifest path",
        )
        _require(
            manifest_ref not in seen_manifests,
            "coarse_index_invalid",
            f"duplicate coarse manifest reference {manifest_ref}",
        )
        seen_manifests.add(manifest_ref)
        cells = entry.get("cells")
        _require(
            isinstance(cells, int) and cells > 0,
            "coarse_index_invalid",
            f"coarse index {body} entry has an invalid cell count {cells!r}",
        )
        manifest_path = _manifest_path(paths, manifest_ref)
        manifest = _load_json(manifest_path, code="coarse_manifest")
        _require(
            isinstance(manifest, dict),
            "coarse_manifest_invalid",
            f"coarse manifest {manifest_ref} must be a JSON object",
        )
        source = manifest.get("source")
        _require(
            isinstance(source, dict),
            "coarse_manifest_invalid",
            f"coarse manifest {manifest_ref} has no source object",
        )
        _require(
            source.get("body") == body,
            "manifest_body_mismatch",
            f"coarse manifest {manifest_ref} declares body {source.get('body')!r}, not {body!r}",
        )
        _require(
            source.get("source_id") == entry.get("source_id"),
            "manifest_source_mismatch",
            f"coarse manifest {manifest_ref} source_id does not match the index",
        )
        manifest_cells = manifest.get("cells")
        _require(
            isinstance(manifest_cells, list) and len(manifest_cells) == cells,
            "manifest_cell_count_mismatch",
            f"coarse manifest {manifest_ref} has {len(manifest_cells or [])} cells; "
            f"the index declares {cells}",
        )
        candidate_ids = [cell.get("candidate_id") for cell in manifest_cells]
        _require(
            all(
                isinstance(value, str) and value.startswith("planet-coarse-v1:")
                for value in candidate_ids
            ),
            "coarse_manifest_invalid",
            f"coarse manifest {manifest_ref} has an invalid candidate id",
        )
        _require(
            len(set(candidate_ids)) == len(candidate_ids),
            "coarse_manifest_invalid",
            f"coarse manifest {manifest_ref} repeats a candidate id",
        )
    return payload


def _verify_manifest(path: Path, *, fixture_root: Path) -> dict[str, Any]:
    try:
        return verify_manifest(path, fixture_root=fixture_root, recompute=True)
    except PlanetaryCoarseError as exc:
        raise PlanetaryCoverageError(exc.code, exc.message) from exc
    except OSError as exc:
        raise PlanetaryCoverageError(
            "fixture_read_failed", f"could not read a committed fixture: {exc}"
        ) from exc


def _source_summary(source: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "source_id": source.get("source_id"),
        "body": source.get("body"),
        "dataset_id": source.get("dataset_id"),
        "provider": source.get("provider"),
        "title": source.get("title"),
        "url": source.get("url"),
        "raster_url": source.get("raster_url"),
        "product_page": source.get("product_page"),
        "license": source.get("license"),
        "license_url": source.get("license_url"),
        "citation": source.get("citation"),
        "doi": source.get("doi"),
        "frame": source.get("frame"),
        "coordinate_system": source.get("coordinate_system"),
        "native_resolution_m": source.get("native_resolution_m"),
        "native_ppd": source.get("native_ppd"),
        "same_support_as_earth": source.get("same_support_as_earth"),
        "vertical_reference": source.get("vertical_reference"),
    }


def _cell_feature_aggregate(
    cells: Sequence[Mapping[str, Any]],
) -> dict[str, dict[str, float]]:
    values: dict[str, list[float]] = defaultdict(list)
    for cell in cells:
        features = cell.get("features")
        if not isinstance(features, Mapping):
            continue
        for feature_id, value in features.items():
            if isinstance(value, (int, float)) and math.isfinite(float(value)):
                values[str(feature_id)].append(float(value))
    aggregate: dict[str, dict[str, float]] = {}
    for feature_id in sorted(values):
        series = values[feature_id]
        aggregate[feature_id] = {
            "cell_count": len(series),
            "mean": float(sum(series) / len(series)),
            "min": float(min(series)),
            "max": float(max(series)),
        }
    return aggregate


def _bbox_union(bboxes: Iterable[Mapping[str, Any] | None]) -> dict[str, float] | None:
    valid = [
        bbox
        for bbox in bboxes
        if isinstance(bbox, Mapping)
        and all(
            isinstance(bbox.get(key), (int, float)) for key in ("west", "south", "east", "north")
        )
    ]
    if not valid:
        return None
    return {
        "west": min(float(bbox["west"]) for bbox in valid),
        "south": min(float(bbox["south"]) for bbox in valid),
        "east": max(float(bbox["east"]) for bbox in valid),
        "north": max(float(bbox["north"]) for bbox in valid),
    }


def _decision_summary(
    regions: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for region in regions:
        for decision in region.get("comparability", []):
            feature_id = str(decision.get("feature_id"))
            decision_value = str(decision.get("decision"))
            key = (feature_id, decision_value)
            item = grouped.setdefault(
                key,
                {
                    "feature_id": feature_id,
                    "decision": decision_value,
                    "regions": [],
                    "reason": decision.get("reason"),
                    "unit": decision.get("unit"),
                    "method_version": decision.get("method_version"),
                    "earth_method_version": decision.get("earth_method_version"),
                },
            )
            item["regions"].append(region.get("region_id"))
    for item in grouped.values():
        item["regions"] = sorted(set(item["regions"]))
    return [grouped[key] for key in sorted(grouped)]


def coarse_case_summary(body: str, root: str | Path | None = None) -> dict[str, Any]:
    """Summarise the bounded committed coarse D01 cells for one planetary body.

    Support-comparable cells (CTX 20 m and Moon LOLA128 236.9 m) and support
    evidence cells (MOLA 463.0576672 m, ``not_comparable``) are returned in
    separate lists. Their bboxes and aggregates are never merged.
    """
    if body not in PLANETARY_BODIES:
        raise PlanetaryCoverageError(
            "unsupported_body", f"coarse coverage supports Moon or Mars, not {body!r}"
        )
    paths = _resolve_paths(root, None)
    index = load_planetary_coarse_index(paths.coarse_root)
    entries = [entry for entry in index["pilots"] if entry.get("body") == body]
    if not entries:
        raise PlanetaryCoverageError(
            "no_coarse_cells", f"the committed coarse index has no {body} pilot cells"
        )

    regions: list[dict[str, Any]] = []
    comparable_cells: list[dict[str, Any]] = []
    evidence_only_cells: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    all_limitations: list[str] = []
    method_versions: set[str] = set()
    earth_method_versions: set[str] = set()
    analysis_resolutions: set[float] = set()
    native_resolutions: set[float] = set()
    gates: list[dict[str, Any]] = []

    for entry in entries:
        manifest_path = _manifest_path(paths, entry["manifest"])
        manifest = _verify_manifest(manifest_path, fixture_root=paths.repo_root)
        source = manifest["source"]
        cells = list(manifest["cells"])
        decisions = list(manifest.get("comparability", []))
        decision_values = {str(item.get("decision")) for item in decisions}
        support_comparable = bool(source.get("same_support_as_earth")) and (
            "not_comparable" not in decision_values
        )
        classification = (
            "comparable_support_evidence_only"
            if support_comparable
            else "evidence_only_not_comparable"
        )
        ranking_decision = (
            "not_comparable"
            if "not_comparable" in decision_values
            else ("evidence_only" if "evidence_only" in decision_values else "comparable")
        )
        candidate_ids = [str(cell["candidate_id"]) for cell in cells]
        target = comparable_cells if support_comparable else evidence_only_cells
        for cell in cells:
            target.append(
                {
                    "candidate_id": cell["candidate_id"],
                    "row": cell.get("row"),
                    "col": cell.get("col"),
                    "bbox_body_lonlat": cell.get("bbox_body_lonlat"),
                    "features": dict(cell.get("features") or {}),
                    "elevation_stats": dict(cell.get("elevation_stats") or {}),
                    "valid_fraction": cell.get("valid_fraction"),
                }
            )

        gate = dict(manifest.get("cross_body_ranking", {}))
        gates.append(gate)
        method = manifest.get("method", {})
        method_versions.add(str(method.get("method_version") or entry.get("method_version")))
        if method.get("earth_coarse_method_version"):
            earth_method_versions.add(str(method["earth_coarse_method_version"]))
        analysis_resolutions.add(float(method.get("analysis_resolution_m") or 0.0))
        native_resolutions.add(float(source.get("native_resolution_m") or 0.0))
        source_summary = _source_summary(source)
        source_summary["manifest"] = _relative(manifest_path, paths.repo_root)
        sources.append(source_summary)
        all_limitations.extend(str(item) for item in manifest.get("limitations", []))

        region_bbox = _bbox_union(cell.get("bbox_body_lonlat") for cell in cells)
        regions.append(
            {
                "region_id": manifest.get("region", {}).get("region_id"),
                "label": manifest.get("region", {}).get("label"),
                "manifest": _relative(manifest_path, paths.repo_root),
                "source_id": source.get("source_id"),
                "dataset_id": source.get("dataset_id"),
                "cell_count": len(cells),
                "candidate_ids": candidate_ids,
                "support_comparable_candidate_ids": candidate_ids if support_comparable else [],
                "evidence_only_candidate_ids": [] if support_comparable else candidate_ids,
                "classification": classification,
                "ranking_decision": ranking_decision,
                "bbox_body_lonlat": region_bbox,
                "bbox_metric": manifest.get("region", {}).get("bbox_metric"),
                "crs_name": manifest.get("region", {}).get("crs_name"),
                "crs_frame_note": manifest.get("region", {}).get("crs_frame_note"),
                "native_resolution_m": source.get("native_resolution_m"),
                "analysis_resolution_m": method.get("analysis_resolution_m"),
                "method_version": method.get("method_version"),
                "earth_coarse_method_version": method.get("earth_coarse_method_version"),
                "comparability": decisions,
                "cross_body_ranking": gate,
                "aggregate_features": _cell_feature_aggregate(cells),
                "cells": [
                    {
                        "candidate_id": cell["candidate_id"],
                        "row": cell.get("row"),
                        "col": cell.get("col"),
                        "bbox_body_lonlat": cell.get("bbox_body_lonlat"),
                        "features": dict(cell.get("features") or {}),
                        "elevation_stats": dict(cell.get("elevation_stats") or {}),
                        "valid_fraction": cell.get("valid_fraction"),
                    }
                    for cell in cells
                ],
                "limitations": list(manifest.get("limitations", [])),
            }
        )

    comparable_regions = [
        region
        for region in regions
        if region["classification"] == "comparable_support_evidence_only"
    ]
    evidence_regions = [
        region for region in regions if region["classification"] == "evidence_only_not_comparable"
    ]
    return {
        "body": body,
        "claim_scope": CLAIM_SCOPE,
        "cell_count": len(comparable_cells) + len(evidence_only_cells),
        "comparable_cell_count": len(comparable_cells),
        "evidence_only_cell_count": len(evidence_only_cells),
        "ranking_comparable_cell_count": sum(
            1 for region in regions if region["ranking_decision"] == "comparable"
        ),
        "support_comparable_cell_count": len(comparable_cells),
        "evidence_only_separated": True,
        "evidence_only_note": (
            "Cells whose source support/method is not comparable are kept in a separate "
            "evidence-only list. They are never merged into the comparable cell count, "
            "bbox or aggregate."
        ),
        "regions": regions,
        "comparable_region_ids": [region["region_id"] for region in comparable_regions],
        "evidence_only_region_ids": [region["region_id"] for region in evidence_regions],
        "bbox": _bbox_union(region.get("bbox_body_lonlat") for region in regions),
        "comparable_bbox": _bbox_union(
            region.get("bbox_body_lonlat") for region in comparable_regions
        ),
        "evidence_only_bbox": _bbox_union(
            region.get("bbox_body_lonlat") for region in evidence_regions
        ),
        "resolutions": {
            "analysis_resolution_m": sorted(value for value in analysis_resolutions if value),
            "native_resolution_m": sorted(value for value in native_resolutions if value),
        },
        "sources": sources,
        "source_ids": sorted({str(source["source_id"]) for source in sources}),
        "dataset_ids": sorted(
            {str(source["dataset_id"]) for source in sources if source.get("dataset_id")}
        ),
        "method_versions": sorted(value for value in method_versions if value),
        "earth_coarse_method_versions": sorted(earth_method_versions),
        "comparability_decisions": _decision_summary(regions),
        "cross_body_ranking": {
            "enabled": any(bool(gate.get("enabled")) for gate in gates),
            "all_manifests_disabled": all(not bool(gate.get("enabled")) for gate in gates),
            "blocking_features": sorted(
                {str(feature) for gate in gates for feature in gate.get("blocking_features", [])}
            ),
            "reason": (
                "No planetary coarse metric is validated as the same quantity, method and "
                "support as the Earth coarse index. Evidence-only and not_comparable metrics "
                "must not be scored against Earth candidates."
            ),
            "claim_scope": "bounded_pilot_cells_only_not_a_global_survey",
        },
        "limitations": _unique(all_limitations),
        "cell_candidate_ids": [
            str(cell["candidate_id"]) for cell in (*comparable_cells, *evidence_only_cells)
        ],
        "comparable_candidate_ids": [str(cell["candidate_id"]) for cell in comparable_cells],
        "evidence_only_candidate_ids": [str(cell["candidate_id"]) for cell in evidence_only_cells],
    }


# ---------------------------------------------------------------------------
# Typed-v2 observation counts
# ---------------------------------------------------------------------------


def _infer_dimension(pilot_dir: Path, row: Mapping[str, Any]) -> str | None:
    declared = row.get("dimension_code")
    if declared in DIMENSION_CODES:
        return str(declared)
    match = _DIMENSION_PREFIX.match(pilot_dir.name)
    if match:
        return match.group(1).upper()
    return None


def _pilot_source_dataset_ids(pilot_dir: Path, body: str) -> dict[str, list[str]]:
    """Dataset ids declared by a pilot for a body, used only when rows omit them."""
    found: dict[str, list[str]] = defaultdict(list)
    for filename in ("sources.json", "datasets.json"):
        path = pilot_dir / filename
        if not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        items = payload if isinstance(payload, list) else []
        for item in items:
            if not isinstance(item, Mapping):
                continue
            item_body = item.get("body")
            if item_body not in (None, body):
                continue
            dataset_id = item.get("dataset_id")
            if isinstance(dataset_id, str) and dataset_id:
                found[dataset_id].append(filename)
    manifest_path = pilot_dir / "manifest.json"
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            manifest = {}
        provenance = manifest.get("source_provenance") if isinstance(manifest, Mapping) else None
        if isinstance(provenance, Mapping):
            dataset_id = provenance.get("dataset_id")
            if isinstance(dataset_id, str) and dataset_id:
                found[dataset_id].append("manifest.json:source_provenance")
    return {dataset_id: sorted(set(origins)) for dataset_id, origins in found.items()}


def _empty_typed_entry() -> dict[str, Any]:
    return {
        "observation_count": 0,
        "area_count": 0,
        "dataset_ids": [],
        "dataset_id_origins": {},
        "states": {},
        "feature_counts": {},
        "roles": [],
        "role": "unspecified_in_committed_records",
        "pilots": [],
        "files": [],
    }


def _merge_typed_entry(target: dict[str, Any], source: Mapping[str, Any]) -> None:
    target["observation_count"] += int(source.get("observation_count", 0))
    target["area_count"] += int(source.get("area_count", 0))
    target["dataset_ids"] = sorted(set(target["dataset_ids"]) | set(source.get("dataset_ids", [])))
    origins = target.setdefault("dataset_id_origins", {})
    for dataset_id, origin in source.get("dataset_id_origins", {}).items():
        origins[dataset_id] = sorted(set(origins.get(dataset_id, [])) | set(origin))
    states = target.setdefault("states", {})
    for state, count in source.get("states", {}).items():
        states[state] = states.get(state, 0) + int(count)
    features = target.setdefault("feature_counts", {})
    for feature_id, count in source.get("feature_counts", {}).items():
        features[feature_id] = features.get(feature_id, 0) + int(count)
    roles = sorted(set(target.get("roles", [])) | set(source.get("roles", [])))
    target["roles"] = roles
    target["role"] = (
        roles[0]
        if len(roles) == 1
        else ("mixed:" + ",".join(roles) if roles else "unspecified_in_committed_records")
    )
    target["pilots"] = sorted(set(target.get("pilots", [])) | set(source.get("pilots", [])))
    target["files"] = sorted(set(target.get("files", [])) | set(source.get("files", [])))


def typed_dimension_counts(
    pilots_root: str | Path | None = None,
    *,
    geology_root: str | Path | None = None,
) -> dict[str, Any]:
    """Count committed typed-v2 observations by body and dimension.

    The scan reads every committed pilot ``manifest.json`` and ``observations.jsonl``
    plus the D02 geology typed bundle. Counts are per observation row, not per
    feature or per area. A missing manifest/observations file is reported in
    ``missing_files``; a malformed row is reported in ``ignored_rows``.
    """
    paths = _resolve_paths(None, pilots_root)
    root = paths.pilots_root
    if not root.is_dir():
        raise PlanetaryCoverageError(
            "pilots_root_missing", f"committed pilots directory not found: {root}"
        )
    geology = (
        Path(geology_root).expanduser() if geology_root is not None else paths.geology_typed_root
    )

    groups: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    area_sets: dict[tuple[str, str], set[str]] = defaultdict(set)
    files_scanned: list[str] = []
    missing_files: list[dict[str, str]] = []
    ignored_rows: list[dict[str, str]] = []

    def scan_bundle(bundle_dir: Path, *, label: str) -> None:
        manifest_path = bundle_dir / "manifest.json"
        observations_path = bundle_dir / "observations.jsonl"
        if not manifest_path.is_file():
            missing_files.append(
                {"path": _relative(manifest_path, paths.repo_root), "reason": "manifest missing"}
            )
        else:
            files_scanned.append(_relative(manifest_path, paths.repo_root))
        if not observations_path.is_file():
            missing_files.append(
                {
                    "path": _relative(observations_path, paths.repo_root),
                    "reason": "observations missing",
                }
            )
            return
        files_scanned.append(_relative(observations_path, paths.repo_root))
        fallback_by_body: dict[str, dict[str, list[str]]] = {}
        rows_with_dataset_ids: set[tuple[str, str]] = set()
        bundle_dimensions: set[tuple[str, str]] = set()
        for line_number, line in enumerate(
            observations_path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                ignored_rows.append(
                    {
                        "path": _relative(observations_path, paths.repo_root),
                        "line": str(line_number),
                        "reason": f"invalid JSON: {exc}",
                    }
                )
                continue
            if not isinstance(row, Mapping):
                ignored_rows.append(
                    {
                        "path": _relative(observations_path, paths.repo_root),
                        "line": str(line_number),
                        "reason": "observation row is not a JSON object",
                    }
                )
                continue
            dimension = _infer_dimension(bundle_dir, row)
            body = row.get("body")
            if dimension is None or not isinstance(body, str) or not body:
                ignored_rows.append(
                    {
                        "path": _relative(observations_path, paths.repo_root),
                        "line": str(line_number),
                        "reason": "missing dimension_code/derivable dimension or body",
                    }
                )
                continue
            bundle_dimensions.add((body, dimension))
            entry = groups[body].setdefault(dimension, _empty_typed_entry())
            entry["observation_count"] += 1
            area_id = row.get("area_id")
            if isinstance(area_id, str) and area_id:
                area_sets[(body, dimension)].add(area_id)
            state = str(row.get("state") or "unknown")
            entry["states"][state] = entry["states"].get(state, 0) + 1
            feature_id = row.get("feature_id")
            if isinstance(feature_id, str) and feature_id:
                entry["feature_counts"][feature_id] = entry["feature_counts"].get(feature_id, 0) + 1
            role = row.get("score_role")
            if isinstance(role, str) and role:
                entry["roles"] = sorted(set(entry["roles"]) | {role})
                entry["role"] = (
                    entry["roles"][0]
                    if len(entry["roles"]) == 1
                    else "mixed:" + ",".join(entry["roles"])
                )
            dataset_ids = row.get("dataset_ids")
            if isinstance(dataset_ids, list):
                for dataset_id in dataset_ids:
                    if isinstance(dataset_id, str) and dataset_id:
                        rows_with_dataset_ids.add((body, dimension))
                        origins = entry.setdefault("dataset_id_origins", {})
                        origins[dataset_id] = sorted(
                            set(origins.get(dataset_id, [])) | {"observations.jsonl"}
                        )
                        entry["dataset_ids"] = sorted(set(entry["dataset_ids"]) | {dataset_id})
            entry["pilots"] = sorted(set(entry["pilots"]) | {label})
            entry["files"] = sorted(
                set(entry["files"]) | {_relative(observations_path, paths.repo_root)}
            )
            fallback_by_body.setdefault(body, {})
            for dataset_id, origins in _pilot_source_dataset_ids(bundle_dir, body).items():
                fallback_by_body[body].setdefault(dataset_id, []).extend(origins)

        for body, dataset_origins in fallback_by_body.items():
            for dimension in sorted(dim for dim_body, dim in bundle_dimensions if dim_body == body):
                entry = groups[body][dimension]
                if (body, dimension) in rows_with_dataset_ids:
                    # The observation rows name their datasets; do not add every
                    # source inspected by the pilot as if it produced a record.
                    continue
                for dataset_id, origins in dataset_origins.items():
                    if dataset_id not in entry["dataset_ids"]:
                        entry["dataset_ids"] = sorted(set(entry["dataset_ids"]) | {dataset_id})
                        entry.setdefault("dataset_id_origins", {})[dataset_id] = sorted(
                            set(origins)
                        )

    pilot_dirs = sorted(path for path in root.iterdir() if path.is_dir())
    for pilot_dir in pilot_dirs:
        if (pilot_dir / "manifest.json").is_file() or (pilot_dir / "observations.jsonl").is_file():
            scan_bundle(pilot_dir, label=pilot_dir.name)
    if geology.is_dir():
        scan_bundle(geology, label="geology-typed-bundle")
    else:
        missing_files.append(
            {
                "path": _relative(geology, paths.repo_root),
                "reason": "D02 geology typed bundle missing",
            }
        )

    for (body, dimension), areas in area_sets.items():
        groups[body][dimension]["area_count"] = len(areas)
        groups[body][dimension]["area_ids"] = sorted(areas)
    for body in groups:
        for dimension in groups[body]:
            groups[body][dimension].setdefault("area_ids", [])

    return {
        "schema_version": "typed-dimension-counts-1.0.0",
        "count_basis": (
            "One count per committed typed-v2 observation row in pipelines/pilots/*/"
            "observations.jsonl and pipelines/geology/typed/observations.jsonl. "
            "Counts are not area coverage and are not a scientific measurement count."
        ),
        "groups": {body: dict(dimensions) for body, dimensions in sorted(groups.items())},
        "files_scanned": sorted(set(files_scanned)),
        "missing_files": missing_files,
        "ignored_rows": ignored_rows,
    }


# ---------------------------------------------------------------------------
# Source provenance registry
# ---------------------------------------------------------------------------


def _registry_add(
    registry: dict[str, dict[str, Any]],
    item: Mapping[str, Any],
    *,
    metadata_file: str,
) -> None:
    dataset_id = item.get("dataset_id")
    if not isinstance(dataset_id, str) or not dataset_id:
        return
    entry = registry.setdefault(
        dataset_id,
        {
            "dataset_id": dataset_id,
            "body": [],
            "provider": None,
            "title": None,
            "url": None,
            "license": None,
            "license_url": None,
            "metadata_files": [],
        },
    )
    body = item.get("body")
    if isinstance(body, str) and body not in entry["body"]:
        entry["body"].append(body)
    for source_key, target_key in (
        ("provider", "provider"),
        ("title", "title"),
        ("product", "title"),
        ("url", "url"),
        ("label_url", "url"),
        ("endpoint", "url"),
        ("license", "license"),
        ("license_url", "license_url"),
    ):
        value = item.get(source_key)
        if value and not entry[target_key]:
            entry[target_key] = value
    entry["metadata_files"] = sorted(set(entry["metadata_files"]) | {metadata_file})


def _typed_source_registry(paths: _CoveragePaths) -> dict[str, dict[str, Any]]:
    registry: dict[str, dict[str, Any]] = {}
    pilot_dirs: list[Path] = []
    if paths.pilots_root.is_dir():
        pilot_dirs.extend(sorted(path for path in paths.pilots_root.iterdir() if path.is_dir()))
    if paths.geology_typed_root.is_dir():
        pilot_dirs.append(paths.geology_typed_root)
    for directory in pilot_dirs:
        for filename in ("sources.json", "datasets.json"):
            path = directory / filename
            if not path.is_file():
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(payload, list):
                for item in payload:
                    if isinstance(item, Mapping):
                        _registry_add(
                            registry,
                            item,
                            metadata_file=_relative(path, paths.repo_root),
                        )
        assets_path = directory / "assets.json"
        if assets_path.is_file():
            try:
                assets = json.loads(assets_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                assets = []
            if isinstance(assets, list):
                for asset in assets:
                    if not isinstance(asset, Mapping):
                        continue
                    dataset_id = asset.get("dataset_id")
                    if not isinstance(dataset_id, str) or not dataset_id:
                        continue
                    entry = registry.setdefault(
                        dataset_id,
                        {
                            "dataset_id": dataset_id,
                            "body": [],
                            "provider": None,
                            "title": None,
                            "url": None,
                            "license": None,
                            "license_url": None,
                            "metadata_files": [],
                        },
                    )
                    if not entry["provider"]:
                        entry["provider"] = asset.get("source")
                    if not entry["url"]:
                        entry["url"] = asset.get("uri")
                    if not entry["license"]:
                        redistribution = asset.get("redistribution_allowed")
                        entry["license"] = (
                            "not stated in the committed asset records"
                            if not redistribution
                            else f"not stated in the committed asset records; "
                            f"redistribution_allowed={redistribution}"
                        )
                    entry["metadata_files"] = sorted(
                        set(entry["metadata_files"]) | {_relative(assets_path, paths.repo_root)}
                    )
        manifest_path = directory / "manifest.json"
        if manifest_path.is_file():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                manifest = {}
            provenance = (
                manifest.get("source_provenance") if isinstance(manifest, Mapping) else None
            )
            if isinstance(provenance, Mapping):
                _registry_add(
                    registry,
                    provenance,
                    metadata_file=_relative(manifest_path, paths.repo_root),
                )
    return registry


# ---------------------------------------------------------------------------
# Dimension support and the full inventory
# ---------------------------------------------------------------------------


def _zero_typed_entry() -> dict[str, Any]:
    return {
        "observation_count": 0,
        "area_count": 0,
        "dataset_ids": [],
        "states": {},
        "role": "unspecified_in_committed_records",
        "feature_counts": {},
        "pilots": [],
    }


def _typed_dimension_payload(entry: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "observation_count": int(entry.get("observation_count", 0)),
        "area_count": int(entry.get("area_count", 0)),
        "dataset_ids": sorted(entry.get("dataset_ids", [])),
        "states": dict(sorted(entry.get("states", {}).items())),
        "role": entry.get("role") or "unspecified_in_committed_records",
        "feature_counts": dict(sorted(entry.get("feature_counts", {}).items())),
        "pilots": sorted(entry.get("pilots", [])),
    }


def _dimension_support(
    *,
    body: str,
    dimension: str,
    typed_entry: Mapping[str, Any],
    coarse: Mapping[str, Any],
) -> dict[str, Any]:
    count = int(typed_entry.get("observation_count", 0))
    dataset_ids = sorted(typed_entry.get("dataset_ids", []))
    states = dict(typed_entry.get("states", {}))
    features = dict(typed_entry.get("feature_counts", {}))

    if dimension == "D01":
        coarse_sources = [source for source in coarse.get("sources", [])]
        coarse_dataset_ids = sorted(
            {str(source["dataset_id"]) for source in coarse_sources if source.get("dataset_id")}
        )
        support: dict[str, Any] = {
            "status": "present",
            "record_count": int(coarse.get("cell_count", 0)),
            "cell_count": int(coarse.get("cell_count", 0)),
            "comparable_cell_count": int(coarse.get("comparable_cell_count", 0)),
            "evidence_only_cell_count": int(coarse.get("evidence_only_cell_count", 0)),
            "typed_observation_count": count,
            "dataset_ids": coarse_dataset_ids,
            "evidence": (
                "Committed bounded coarse D01 pilot cells only: "
                f"{coarse.get('comparable_cell_count', 0)} support-comparable and "
                f"{coarse.get('evidence_only_cell_count', 0)} evidence-only. "
                "This is not global or body-wide coverage."
            ),
            "comparability": "evidence_only",
            "comparability_detail": (
                "No coarse metric is validated as the same quantity, method and support "
                "as the Earth coarse index; cross-body ranking remains disabled."
            ),
            "on_demand_analysis_supported": body == "Moon",
            "on_demand_analysis_scope": (
                "global_lunar_body_via_bounded_lola128_windows"
                if body == "Moon"
                else "jezero_ctx_mosaic_window_only"
            ),
            "on_demand_analysis_supported_within_scope": True,
            "on_demand_analysis_supported_outside_scope": body == "Moon",
            "on_demand_analysis_reason": (
                "The Moon LOLA128 product covers the whole body and the committed coarse "
                "fixtures are committed, so a bounded on-demand D01 analysis is supported."
                if body == "Moon"
                else "Mars has only the bounded Jezero CTX 20 m source. Outside the Jezero "
                "mosaic no verified Mars source at or finer than the 250 m analysis grid is "
                "committed; MOLA MEGDR 463.0576672 m is not_comparable support and must not "
                "be upsampled into an on-demand D01 answer."
            ),
            "reason": (
                "Committed coarse fixtures exist for this body; see coarse_d01 for exact "
                "region ids, feature values and limitations."
            ),
        }
        return support

    if dimension == "D06":
        if count == 0:
            return {
                "status": "absent",
                "record_count": 0,
                "observation_count": 0,
                "dataset_ids": [],
                "evidence": [],
                "evidence_note": (
                    "No committed Moon/Mars D06 observation exists. Earth SoilGrids 2.0 "
                    "250 m records are Earth-only model predictions and are explicitly "
                    "excluded from this body; see earth_only_exclusions."
                ),
                "comparability": "not_applicable",
                "on_demand_analysis_supported": False,
                "on_demand_analysis_reason": (
                    "D06 has no committed Moon/Mars source and no on-demand regolith "
                    "provider; Earth SoilGrids is never substituted for a planetary "
                    "measurement."
                ),
                "reason": (
                    "Zero committed Moon/Mars D06 typed observations. SoilGrids is a 250 m "
                    "Earth model prediction, not a lunar or Martian regolith measurement."
                ),
            }
        missing_states = {"no_coverage", "unknown", "not_detected", "not_applicable"}
        all_missing = bool(states) and set(states) <= missing_states
        return {
            "status": "evidence_no_coverage" if all_missing else "evidence_only",
            "record_count": count,
            "observation_count": count,
            "area_count": int(typed_entry.get("area_count", 0)),
            "dataset_ids": dataset_ids,
            "states": states,
            "features": features,
            "role": typed_entry.get("role"),
            "evidence": [
                f"{feature_id}: {feature_count}"
                for feature_id, feature_count in sorted(features.items())
            ],
            "evidence_note": (
                "Committed Moon/Mars D06 records are a sparse official-source audit. "
                "They carry explicit no_coverage/evidence states and no numeric regolith "
                "value; Earth SoilGrids is excluded from this body and never substituted."
            ),
            "comparability": "evidence_only",
            "on_demand_analysis_supported": False,
            "on_demand_analysis_reason": (
                "D06 has no on-demand planetary regolith provider; the committed records "
                "are a bounded evidence audit, not a measurement of a new AOI."
            ),
            "reason": (
                "Committed Moon/Mars regolith D06 evidence audit records exist, but they "
                "are no_coverage/evidence-only; SoilGrids remains a 250 m Earth model "
                "prediction and is never substituted."
            ),
        }

    if count == 0:
        return {
            "status": "absent",
            "record_count": 0,
            "observation_count": 0,
            "dataset_ids": [],
            "evidence": [],
            "comparability": "not_applicable",
            "on_demand_analysis_supported": False,
            "on_demand_analysis_reason": (
                f"No committed {dimension} on-demand provider exists for {body}; absence is "
                "reported as absent, not zero-filled."
            ),
            "reason": f"No committed {body} {dimension} typed observation exists.",
        }

    dimension_roles = {
        "D02": (
            "Geologic map-unit/process/spectral records are evidence only. Mineral states are "
            "not abundances and no D02 similarity or hard-filter role is enabled."
        ),
        "D03": (
            "Thermal records are evidence/context only. Brightness temperature and apparent "
            "thermal inertia use different instruments, methods and support; no cross-body "
            "temperature score exists."
        ),
        "D04": (
            "Water-equivalent hydrogen, neutron flux and radar CPR are evidence, not confirmed "
            "ice; no D04 similarity feature exists."
        ),
        "D05": (
            "Solar geometry is model-derived context only, not observed irradiance, shadow "
            "fraction or ionizing dose."
        ),
    }
    return {
        "status": "evidence_only",
        "record_count": count,
        "observation_count": count,
        "area_count": int(typed_entry.get("area_count", 0)),
        "dataset_ids": dataset_ids,
        "states": states,
        "features": features,
        "role": typed_entry.get("role"),
        "evidence": [
            f"{feature_id}: {feature_count}"
            for feature_id, feature_count in sorted(features.items())
        ],
        "comparability": "evidence_only",
        "reason": dimension_roles.get(
            dimension,
            "Committed typed records are evidence only; no gated similarity role exists.",
        ),
        "on_demand_analysis_supported": False,
        "on_demand_analysis_reason": (
            f"No {body} {dimension} on-demand provider is part of this issue; the committed "
            "records are a bounded evidence subset and are not a global survey."
        ),
    }


def _body_provenance(
    *,
    body: str,
    coarse: Mapping[str, Any],
    typed_entry_by_dimension: Mapping[str, Mapping[str, Any]],
    typed_registry: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    sources: list[dict[str, Any]] = []
    seen: set[str] = set()
    for source in coarse.get("sources", []):
        source_id = str(source.get("source_id"))
        if source_id in seen:
            continue
        seen.add(source_id)
        sources.append({**dict(source), "claim_role": "D01_coarse"})
    wanted_dataset_ids = sorted(
        {
            dataset_id
            for entry in typed_entry_by_dimension.values()
            for dataset_id in entry.get("dataset_ids", [])
        }
    )
    for dataset_id in wanted_dataset_ids:
        metadata = typed_registry.get(dataset_id)
        if not metadata:
            sources.append(
                {
                    "dataset_id": dataset_id,
                    "body": [body],
                    "provider": None,
                    "license": None,
                    "license_url": None,
                    "url": None,
                    "claim_role": "typed_evidence",
                    "metadata_files": [],
                    "note": "dataset id appears in committed observations but no source "
                    "metadata file was found",
                }
            )
            continue
        sources.append(
            {
                **dict(metadata),
                "claim_role": "typed_evidence",
            }
        )
    return {"body": body, "sources": sources}


def planetary_science_coverage(
    root: str | Path | None = None,
    pilots_root: str | Path | None = None,
) -> dict[str, Any]:
    """Return the deterministic, JSON-serialisable Moon/Mars science-coverage inventory."""
    paths = _resolve_paths(root, pilots_root)
    index = load_planetary_coarse_index(paths.coarse_root)
    typed = typed_dimension_counts(paths.pilots_root)
    typed_groups = typed["groups"]
    typed_registry = _typed_source_registry(paths)

    bodies: dict[str, Any] = {}
    for body in PLANETARY_BODIES:
        coarse = coarse_case_summary(body, root=paths.coarse_root)
        body_typed = typed_groups.get(body, {})
        typed_by_dimension = {
            dimension: _typed_dimension_payload(body_typed.get(dimension, _zero_typed_entry()))
            for dimension in DIMENSION_CODES
        }
        support = {
            dimension: _dimension_support(
                body=body,
                dimension=dimension,
                typed_entry=body_typed.get(dimension, _zero_typed_entry()),
                coarse=coarse,
            )
            for dimension in DIMENSION_CODES
        }
        bodies[body] = {
            "body": body,
            "coordinate_frames": (
                ["MOON_PA / lunar_planetocentric; never Earth WGS84"]
                if body == "Moon"
                else ["Mars_2000_Sphere / IAU_MARS planetocentric; never Earth WGS84"]
            ),
            "coarse_d01": {
                "status": "present",
                "claim_scope": CLAIM_SCOPE,
                "regions": coarse["regions"],
                "region_ids": [region["region_id"] for region in coarse["regions"]],
                "cell_count": coarse["cell_count"],
                "comparable_cell_count": coarse["comparable_cell_count"],
                "evidence_only_cell_count": coarse["evidence_only_cell_count"],
                "evidence_only_separated": coarse["evidence_only_separated"],
                "comparable_candidate_ids": coarse["comparable_candidate_ids"],
                "evidence_only_candidate_ids": coarse["evidence_only_candidate_ids"],
                "bbox": coarse["bbox"],
                "comparable_bbox": coarse["comparable_bbox"],
                "evidence_only_bbox": coarse["evidence_only_bbox"],
                "resolutions": coarse["resolutions"],
                "sources": coarse["sources"],
                "source_ids": coarse["source_ids"],
                "dataset_ids": coarse["dataset_ids"],
                "method_versions": coarse["method_versions"],
                "earth_coarse_method_versions": coarse["earth_coarse_method_versions"],
                "comparability_decisions": coarse["comparability_decisions"],
                "cross_body_ranking": coarse["cross_body_ranking"],
                "limitations": coarse["limitations"],
            },
            "typed_by_dimension": typed_by_dimension,
            "dimension_support": support,
            "provenance": _body_provenance(
                body=body,
                coarse=coarse,
                typed_entry_by_dimension=typed_by_dimension,
                typed_registry=typed_registry,
            ),
            "global_coverage_claimed": False,
        }

    earth_d06 = typed_groups.get("Earth", {}).get("D06", _zero_typed_entry())
    return {
        "metadata": {
            "inventory_version": COVERAGE_INVENTORY_VERSION,
            "claim_scope": CLAIM_SCOPE,
            "bodies": list(PLANETARY_BODIES),
            "dimension_codes": list(DIMENSION_CODES),
            "deterministic": True,
            "generated_at": None,
            "generated_at_note": (
                "No generation timestamp is written; the inventory is a pure function of "
                "committed files so repeated runs are byte-identical."
            ),
            "generated_from": {
                "coarse_index": _relative(paths.coarse_root / "index.json", paths.repo_root),
                "coarse_manifests": sorted(entry["manifest"] for entry in index["pilots"]),
                "typed_pilots_root": _relative(paths.pilots_root, paths.repo_root),
                "geology_typed_bundle": _relative(paths.geology_typed_root, paths.repo_root),
                "source_audits": list(SOURCE_AUDITS),
            },
            "reproduce_command": REPRODUCE_COMMAND,
            "method_version": index.get("method_version"),
            "coarse_index_schema_version": index.get("schema_version"),
            "typed_count_basis": typed["count_basis"],
            "files_scanned": typed["files_scanned"],
            "missing_files": typed["missing_files"],
            "ignored_rows": typed["ignored_rows"],
        },
        "claim_scope": CLAIM_SCOPE,
        "bodies": bodies,
        "earth_only_exclusions": {
            "D06": {
                "reason": (
                    "SoilGrids 2.0 is a 250 m Earth model prediction. It is not a Moon or "
                    "Mars regolith measurement and is never presented as one."
                ),
                "observation_count": int(earth_d06.get("observation_count", 0)),
                "dataset_ids": sorted(earth_d06.get("dataset_ids", [])),
                "states": dict(sorted(earth_d06.get("states", {}).items())),
            }
        },
        "global_claims": {
            "global": False,
            "global_coverage": False,
            "global_survey": False,
            "global_coverage_claimed": False,
            "global_survey_claimed": False,
            "body_wide_coverage_claimed": False,
            "cross_body_ranking_enabled": False,
            "earth_similarity_claimed": False,
            "statement": (
                "This inventory describes a bounded committed subset of Moon/Mars data. It "
                "is not a global or body-wide survey, does not rank bodies, and does not "
                "promote evidence-only records to similarity features."
            ),
        },
        "global_coverage_claimed": False,
        "global_survey_claimed": False,
        "body_wide_coverage_claimed": False,
        "cross_body_ranking_enabled": False,
        "provenance": {
            "source_audits": list(SOURCE_AUDITS),
            "typed_source_registry": {
                dataset_id: dict(metadata)
                for dataset_id, metadata in sorted(typed_registry.items())
            },
        },
    }


# ---------------------------------------------------------------------------
# Mars D01 reproducible case
# ---------------------------------------------------------------------------


def _iter_frame_values(value: Any, key: str | None = None) -> Iterable[tuple[str | None, Any]]:
    if isinstance(value, Mapping):
        for child_key, child_value in value.items():
            yield from _iter_frame_values(child_value, str(child_key))
    elif isinstance(value, list):
        for child in value:
            yield from _iter_frame_values(child, key)
    else:
        yield key, value


def _assert_planetary_frames(case: Mapping[str, Any]) -> None:
    """Reject an Earth WGS84 marker in an actual CRS/frame field.

    Prose that explicitly says "never Earth WGS84/UTM" is allowed; what is not
    allowed is an Earth CRS string in ``crs_wkt``, ``crs_name``, ``frame`` or a
    coordinate-system field.
    """
    frame_keys = {
        "crs_wkt",
        "crs_name",
        "crs",
        "frame",
        "coordinate_frame",
        "coordinate_system",
        "bbox_crs",
    }
    for key, value in _iter_frame_values(case):
        if key not in frame_keys or not isinstance(value, str):
            continue
        for marker in _EARTH_CRS_MARKERS:
            if marker.lower() in value.lower():
                raise PlanetaryCoverageError(
                    "earth_crs_in_planetary_case",
                    f"the planetary case field {key!r} contains the Earth CRS marker {marker!r}",
                )


def mars_d01_case(root: str | Path | None = None) -> dict[str, Any]:
    """Validate and return the reproducible Mars Jezero CTX D01 case.

    The CTX manifest and its four committed elevation fixtures are verified by
    the existing :func:`analog_pipelines.planetary_coarse.verify_manifest`, which
    re-computes every slope/feature value offline. A paired MOLA MEGDR summary is
    returned separately with ``evidence_only`` and ``not_comparable`` reasons and
    no distance or ranking.
    """
    paths = _resolve_paths(root, None)
    load_planetary_coarse_index(paths.coarse_root)
    ctx_ref = "pipelines/planetary/coarse/mars_ctx_jezero_window01.json"
    mola_ref = "pipelines/planetary/coarse/mars_mola_meg128_evidence.json"
    ctx_manifest_path = _manifest_path(paths, ctx_ref)
    mola_manifest_path = _manifest_path(paths, mola_ref)
    ctx = _verify_manifest(ctx_manifest_path, fixture_root=paths.repo_root)
    mola = _verify_manifest(mola_manifest_path, fixture_root=paths.repo_root)

    cells = list(ctx["cells"])
    if len(cells) != 4:
        raise PlanetaryCoverageError(
            "mars_case_cell_count",
            f"the committed Mars CTX case must contain 4 cells, found {len(cells)}",
        )
    bbox = _bbox_union(cell.get("bbox_body_lonlat") for cell in cells)
    region = dict(ctx["region"])
    source = dict(ctx["source"])
    method = dict(ctx["method"])
    features_by_cell = {
        str(cell["candidate_id"]): dict(cell.get("features") or {}) for cell in cells
    }
    aggregate = _cell_feature_aggregate(cells)
    fixture_checksums = [
        {
            "candidate_id": cell["candidate_id"],
            "path": cell["fixture"]["path"],
            "sha256": cell["fixture"]["sha256"],
            "bytes": cell["fixture"]["bytes"],
            "shape": cell["fixture"].get("shape"),
        }
        for cell in cells
    ]
    gate = dict(ctx["cross_body_ranking"])
    if gate.get("enabled") is not False:
        raise PlanetaryCoverageError(
            "cross_body_ranking_enabled", "the Mars CTX manifest enables cross-body ranking"
        )
    if any(decision.get("decision") == "comparable" for decision in ctx["comparability"]):
        raise PlanetaryCoverageError(
            "unexpected_comparable_decision",
            "the Mars CTX case is evidence_only; a comparable decision would be dishonest",
        )

    mola_cells = list(mola["cells"])
    mola_comparability = list(mola.get("comparability", []))
    mola_reason = next(
        (
            str(item.get("reason"))
            for item in mola_comparability
            if item.get("decision") == "not_comparable"
        ),
        None,
    )
    if not mola_reason:
        raise PlanetaryCoverageError(
            "mola_comparability_missing",
            "the MOLA evidence manifest does not state a not_comparable reason",
        )
    mola_pairs = []
    for pair in mola.get("support_comparison", {}).get("pairs", []):
        mola_pairs.append(
            {
                **dict(pair),
                "evidence_only": True,
                "not_comparable": True,
                "used_for_ranking": False,
                "distance": None,
                "score": None,
            }
        )

    case = {
        "case_id": "mars-d01-jezero-ctx-window-01",
        "inventory_version": COVERAGE_INVENTORY_VERSION,
        "body": "Mars",
        "claim_scope": CLAIM_SCOPE,
        "region": region,
        "region_id": region.get("region_id"),
        "cell_count": len(cells),
        "candidate_ids": [str(cell["candidate_id"]) for cell in cells],
        "comparable_candidate_ids": [str(cell["candidate_id"]) for cell in cells],
        "evidence_only_candidate_ids": [],
        "bbox": bbox,
        "bbox_body_lonlat": bbox,
        "bbox_metric": region.get("bbox_metric"),
        "crs_name": region.get("crs_name"),
        "crs_wkt": region.get("crs_wkt"),
        "crs_frame_note": region.get("crs_frame_note"),
        "analysis_resolution_m": method.get("analysis_resolution_m"),
        "source_resolution_m": source.get("native_resolution_m"),
        "method_version": method.get("method_version"),
        "method": method,
        "source": _source_summary(source),
        "source_url": source.get("url"),
        "source_raster_url": source.get("raster_url"),
        "license": source.get("license"),
        "license_url": source.get("license_url"),
        "frame": source.get("frame"),
        "coordinate_system": source.get("coordinate_system"),
        "cells": [
            {
                "candidate_id": cell["candidate_id"],
                "row": cell.get("row"),
                "col": cell.get("col"),
                "bbox_metric": cell.get("bbox_metric"),
                "bbox_body_lonlat": cell.get("bbox_body_lonlat"),
                "features": dict(cell.get("features") or {}),
                "elevation_stats": dict(cell.get("elevation_stats") or {}),
                "valid_fraction": cell.get("valid_fraction"),
                "valid_slope_pixels": cell.get("valid_slope_pixels"),
                "fixture": {
                    key: cell["fixture"].get(key)
                    for key in ("path", "sha256", "bytes", "shape", "transform", "pad_pixels")
                },
            }
            for cell in cells
        ],
        "features_by_cell": features_by_cell,
        "per_cell_features": features_by_cell,
        "aggregate_features": aggregate,
        "aggregate": aggregate,
        "fixture_checksums": fixture_checksums,
        "verification": {
            "verified": True,
            "verifier": "analog_pipelines.planetary_coarse.verify_manifest",
            "fixture_sha256_match": True,
            "features_recomputed": True,
            "recompute_tolerance_rel": 1e-9,
            "checked_at": None,
            "checked_at_note": "Offline fixture recomputation; no network or timestamp.",
        },
        "comparability": ctx["comparability"],
        "cross_body_ranking": gate,
        "limitations": list(ctx.get("limitations", [])),
        "claims": {
            "global_coverage_claimed": False,
            "global_survey_claimed": False,
            "body_wide_coverage_claimed": False,
            "is_mola": False,
            "mola_merged_into_comparable": False,
            "earth_wgs84_used": False,
            "earth_epsg_4326_used": False,
            "cross_body_ranking_enabled": False,
            "distance_or_score_emitted": False,
            "statement": (
                "Four bounded 5 km CTX cells inside one Jezero coordinate window. Not global, "
                "not MOLA, not Earth WGS84, and not a cross-body ranking."
            ),
        },
        "mola_evidence": {
            "evidence_only": True,
            "not_comparable": True,
            "not_comparable_reason": mola_reason,
            "merged_into_comparable": False,
            "used_for_ranking": False,
            "distance": None,
            "region_id": mola.get("region", {}).get("region_id"),
            "cell_count": len(mola_cells),
            "candidate_ids": [str(cell["candidate_id"]) for cell in mola_cells],
            "bbox_body_lonlat": _bbox_union(cell.get("bbox_body_lonlat") for cell in mola_cells),
            "source": _source_summary(mola["source"]),
            "native_resolution_m": mola["source"].get("native_resolution_m"),
            "analysis_resolution_m": mola.get("region", {}).get("analysis_resolution_m"),
            "method_version": mola.get("method", {}).get("method_version"),
            "comparability": mola_comparability,
            "cross_body_ranking": mola.get("cross_body_ranking"),
            "pairs": mola_pairs,
            "support_comparison_interpretation": mola.get("support_comparison", {}).get(
                "interpretation"
            ),
            "limitations": list(mola.get("limitations", [])),
        },
    }
    _assert_planetary_frames(case)
    return case


# ---------------------------------------------------------------------------
# Writing and CLI
# ---------------------------------------------------------------------------


def write_coverage_inventory(
    path: str | Path,
    root: str | Path | None = None,
    pilots_root: str | Path | None = None,
) -> dict[str, Any]:
    """Write the deterministic inventory JSON atomically and return its payload."""
    payload = planetary_science_coverage(root=root, pilots_root=pilots_root)
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(destination)
    return payload


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Generate the deterministic Moon/Mars science-coverage inventory or print "
            "the verified Mars Jezero CTX D01 case."
        )
    )
    parser.add_argument(
        "--write",
        metavar="PATH",
        help="write the inventory JSON to PATH (deterministic sort_keys/indent)",
    )
    parser.add_argument(
        "--mars-case",
        action="store_true",
        help="print the verified Mars Jezero CTX D01 case instead of the inventory",
    )
    parser.add_argument(
        "--root",
        help="repository root, pipelines/planetary/coarse, or index.json path",
    )
    parser.add_argument(
        "--pilots-root",
        help="override the committed pipelines/pilots directory",
    )
    args = parser.parse_args(argv)
    if args.mars_case:
        payload: Any = mars_d01_case(root=args.root)
    else:
        payload = planetary_science_coverage(root=args.root, pilots_root=args.pilots_root)
    if args.write:
        destination = Path(args.write)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    else:
        json.dump(payload, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    raise SystemExit(main())


__all__ = [
    "CLAIM_SCOPE",
    "COARSE_INDEX_SCHEMA_VERSION",
    "COVERAGE_INVENTORY_VERSION",
    "DIMENSION_CODES",
    "PLANETARY_BODIES",
    "PlanetaryCoverageError",
    "coarse_case_summary",
    "load_planetary_coarse_index",
    "main",
    "mars_d01_case",
    "planetary_science_coverage",
    "typed_dimension_counts",
    "write_coverage_inventory",
]
