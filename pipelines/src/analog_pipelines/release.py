"""Bounded multi-dimension release manifest builder (Issue #48).

The release combines *only* artifacts already committed to this repository:

* the pinned fine terrain snapshot (D01, Earth/Moon/Mars);
* the real 168-cell coarse Parquet terrain index (D01, Earth pilot regions);
* the evidence-only D02 geology typed export;
* the bounded D03/D04/D05/D06 acquisition pilots;
* the machine-readable source inventory used for licence/provenance references.

Every artifact is recorded with per-file SHA-256 values, an aggregate artifact
digest, counts, schema/method versions, source and licence references. The
manifest is deterministic for the same pinned bytes: volatile build metadata is
excluded from the release id and ``generated_at`` is derived from the inputs (or
``SOURCE_DATE_EPOCH``).

Nothing here downloads data, publishes to R2, resets a live database, or turns a
bounded local subset into a global claim. ``verify`` recomputes every checksum and
semantic count; a tampered artifact therefore fails closed.

CLI::

    uv run python pipelines/build_release.py build
    uv run python pipelines/build_release.py verify
    uv run python pipelines/build_release.py report
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from itertools import combinations
from pathlib import Path
from typing import Any

import duckdb

from analog_pipelines.coarse_index import IndexError_, verify_index
from analog_pipelines.dimensions import DIMENSION_NAMES
from analog_pipelines.inventory import SourceInventory, load_inventory
from analog_pipelines.snapshot import (
    DEFAULT_SNAPSHOT_RELATIVE,
    SnapshotError,
    canonical_json_bytes,
    load_snapshot,
    pretty_json_bytes,
    resolve_snapshot_path,
    verify_snapshot,
)
from analog_pipelines.storage import sha256_file
from analog_pipelines.typed_contracts import MISSING_STATES, TypedObservation

REPO_ROOT = Path(__file__).resolve().parents[3]
RELEASE_VERSION = "multi-dimension-release-1.0.0"
EDA_VERSION = "multi-dimension-eda-1.0.0"
GENERATED_BY = "pipelines/src/analog_pipelines/release.py"
DEFAULT_MANIFEST_RELATIVE = "samples/release/analog-release.manifest.json"
DEFAULT_EDA_RELATIVE = "samples/release/analog-release.eda.json"
DEFAULT_INVENTORY_RELATIVE = "catalog/dimension-source-inventory.yaml"
COVERAGE_LABEL = "bounded_local_subset_not_global"
BODIES = ("Earth", "Moon", "Mars")
DIMENSION_CODES = tuple(DIMENSION_NAMES)
DEFAULT_NOTES = (
    "Bounded local multi-dimension release assembled from committed artifacts only. "
    "This is not global coverage; missing bodies/dimensions are listed explicitly and "
    "no value is zero-filled."
)
MISSING_STATE_VALUES = frozenset(MISSING_STATES)


class ReleaseError(RuntimeError):
    """A release could not be assembled from the approved artifacts."""


class ReleaseVerificationError(ReleaseError):
    """A release manifest or one of its artifacts failed verification."""


@dataclass(frozen=True, slots=True)
class ArtifactSpec:
    """One approved commit-time artifact and its provenance expectations."""

    artifact_id: str
    kind: str
    primary_path: str
    dimensions: tuple[str, ...]
    bodies: tuple[str, ...]
    source_ids: tuple[str, ...]
    value_origin: str
    limitations: tuple[str, ...] = field(default_factory=tuple)


ARTIFACTS: tuple[ArtifactSpec, ...] = (
    ArtifactSpec(
        artifact_id="fine-terrain-snapshot",
        kind="pinned_snapshot",
        primary_path=DEFAULT_SNAPSHOT_RELATIVE,
        dimensions=("D01",),
        bodies=BODIES,
        source_ids=(
            "copernicus-dem-glo90",
            "esa-worldcover-2021-v200",
            "nasa-lola-south-pole",
            "usgs-jezero-ctx-dem",
        ),
        value_origin="derived_from_measured_dem",
        limitations=(
            "113 Earth candidate windows and 18 Moon / 5 Mars reference windows only.",
            "WorldCover context observations are context-only and never enter the similarity score.",
        ),
    ),
    ArtifactSpec(
        artifact_id="coarse-terrain-index",
        kind="coarse_parquet_index",
        primary_path=(
            "pipelines/coarse/real-pilot/index/snapshots/"
            "geomorpho250-real-pilot-20261115/manifest.json"
        ),
        dimensions=("D01",),
        bodies=("Earth",),
        source_ids=("geomorpho250-slope",),
        value_origin="derived_from_measured_dem",
        limitations=(
            "168 cells in three bounded pilot regions; the bbox envelope is not a coverage claim.",
            "250 m-derived coarse metrics are not the fine 90 m terrain method.",
        ),
    ),
    ArtifactSpec(
        artifact_id="typed-d02-geology",
        kind="typed_bundle_export",
        primary_path="pipelines/geology/typed/manifest.json",
        dimensions=("D02",),
        bodies=BODIES,
        source_ids=(
            "glim-global-lithology",
            "nasa-emit-mineral",
            "usgs-moon-unified-geologic-map",
            "nasa-m3",
            "usgs-jezero-geologic-map",
            "nasa-crism",
        ),
        value_origin="mixed_mapped_evidence_and_spectral_context",
        limitations=(
            "Map units and spectral indices are evidence, not mineral abundance.",
            "Unknown/mineral states are preserved; no quantitative mineral is fabricated.",
        ),
    ),
    ArtifactSpec(
        artifact_id="typed-d03-thermal",
        kind="typed_bundle_acquisition",
        primary_path="pipelines/pilots/d03_earth_power/manifest.json",
        dimensions=("D03",),
        bodies=("Earth",),
        source_ids=("nasa-power-thermal-daily",),
        value_origin="model_estimate",
        limitations=(
            "NASA POWER coarse model/reanalysis context, not a local temperature measurement.",
            "No thermal inertia is derived.",
        ),
    ),
    ArtifactSpec(
        artifact_id="typed-d04-water-ice",
        kind="typed_bundle_acquisition",
        primary_path="pipelines/pilots/d04_earth_power/manifest.json",
        dimensions=("D04",),
        bodies=("Earth",),
        source_ids=("nasa-power-water-daily",),
        value_origin="model_estimate",
        limitations=("NASA POWER wetness/snow/precipitation context only; no confirmed ice.",),
    ),
    ArtifactSpec(
        artifact_id="typed-d05-illumination",
        kind="typed_bundle_acquisition",
        primary_path="pipelines/pilots/d05_earth_power/manifest.json",
        dimensions=("D05",),
        bodies=("Earth",),
        source_ids=("nasa-power-daily-point",),
        value_origin="model_estimate",
        limitations=(
            "NASA POWER coarse irradiance context, not local illuminance and not ionizing radiation.",
        ),
    ),
    ArtifactSpec(
        artifact_id="typed-d05-spice-geometry",
        kind="typed_bundle_acquisition",
        primary_path="pipelines/pilots/d05_spice_illumination/manifest.json",
        dimensions=("D05",),
        bodies=("Moon", "Mars"),
        source_ids=("naif-spice-solar-geometry-moon", "naif-spice-solar-geometry-mars"),
        value_origin="model_derived_geometry",
        limitations=(
            "NAIF SPICE model geometry (incidence/elevation), not observed irradiance or shadow.",
            "No ionizing radiation, absorbed dose (Gy) or equivalent dose (Sv) is included.",
        ),
    ),
    ArtifactSpec(
        artifact_id="typed-d06-regolith",
        kind="typed_bundle_acquisition",
        primary_path="pipelines/pilots/d06_earth_soilgrids/manifest.json",
        dimensions=("D06",),
        bodies=("Earth",),
        source_ids=("soilgrids-250m-v2",),
        value_origin="model_estimate",
        limitations=("SoilGrids 250 m model prediction at 0-5 cm, not a local sample.",),
    ),
    ArtifactSpec(
        artifact_id="source-inventory",
        kind="source_inventory",
        primary_path=DEFAULT_INVENTORY_RELATIVE,
        dimensions=DIMENSION_CODES,
        bodies=BODIES,
        source_ids=(),
        value_origin="not_applicable",
        limitations=("Inventory statuses describe sources, not observations.",),
    ),
)


def _display_path(path: Path, root: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(root.resolve()).as_posix()
    except ValueError:
        return resolved.as_posix()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as exc:
        raise ReleaseError(f"cannot read {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ReleaseError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ReleaseError(f"expected a JSON object in {path}")
    return value


def _read_json_list(path: Path) -> list[Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as exc:
        raise ReleaseError(f"cannot read {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ReleaseError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(value, list):
        raise ReleaseError(f"expected a JSON list in {path}")
    return value


def _load_observations(bundle_dir: Path) -> list[TypedObservation]:
    path = bundle_dir / "observations.jsonl"
    if not path.is_file():
        raise ReleaseError(f"missing observations: {path}")
    observations: list[TypedObservation] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            observations.append(TypedObservation.model_validate(json.loads(line)))
        except (ValueError, json.JSONDecodeError) as exc:
            raise ReleaseError(f"{path}:{number}: invalid typed observation: {exc}") from exc
    return observations


def _parse_timestamp(value: str) -> datetime | None:
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


def _timestamp_text(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _input_timestamps(root: Path) -> list[datetime]:
    moments: list[datetime] = []
    snapshot_path = root / DEFAULT_SNAPSHOT_RELATIVE
    if snapshot_path.is_file():
        snapshot = load_snapshot(snapshot_path)
        value = snapshot.get("created_at")
        if isinstance(value, str) and (moment := _parse_timestamp(value)):
            moments.append(moment)
        for entry in snapshot.get("manifests") or []:
            if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
                continue
            manifest = _read_json(resolve_snapshot_path(root, entry["path"]))
            value = manifest.get("generated_at")
            if isinstance(value, str) and (moment := _parse_timestamp(value)):
                moments.append(moment)
    for spec in ARTIFACTS:
        if spec.kind not in {
            "typed_bundle_export",
            "typed_bundle_acquisition",
            "coarse_parquet_index",
        }:
            continue
        manifest = _read_json(root / spec.primary_path)
        for key in ("generated_at", "created_at"):
            value = manifest.get(key)
            if isinstance(value, str) and (moment := _parse_timestamp(value)):
                moments.append(moment)
    return moments


def _derived_generated_at(root: Path) -> str:
    epoch = os.environ.get("SOURCE_DATE_EPOCH", "").strip()
    if epoch:
        try:
            return _timestamp_text(datetime.fromtimestamp(int(epoch), UTC))
        except ValueError as exc:
            raise ReleaseError(f"invalid SOURCE_DATE_EPOCH: {epoch!r}") from exc
    moments = _input_timestamps(root)
    if moments:
        return _timestamp_text(max(moments))
    return _timestamp_text(datetime.now(UTC))


def _manifest_body(relative: str) -> str:
    if "planetary" in relative:
        if "lola" in relative:
            return "Moon"
        if "ctx_mars" in relative or "mars" in relative:
            return "Mars"
    return "Earth"


def _count_overlaps(
    boxes: Iterable[Mapping[str, float]],
    labels: Sequence[str] | None = None,
) -> tuple[int, list[list[str]]]:
    items = [(labels[index] if labels else str(index), bbox) for index, bbox in enumerate(boxes)]
    count = 0
    examples: list[list[str]] = []
    for (left, first), (right, second) in combinations(items, 2):
        if not (
            first["east"] <= second["west"]
            or second["east"] <= first["west"]
            or first["north"] <= second["south"]
            or second["north"] <= first["south"]
        ):
            count += 1
            if len(examples) < 5:
                examples.append([left, right])
    return count, examples


def _bbox_values(value: Any) -> dict[str, float] | None:
    if not isinstance(value, dict):
        return None
    keys = ("west", "south", "east", "north")
    if not all(isinstance(value.get(key), (int, float)) for key in keys):
        return None
    return {key: float(value[key]) for key in keys}


def _source_reference(source: Any) -> dict[str, Any]:
    return {
        "source_id": source.source_id,
        "provider": source.provider,
        "product": source.product,
        "url": source.url,
        "license": source.license,
        "license_url": source.license_url,
        "availability": source.availability,
        "data_status": source.data_status,
        "comparability": source.comparability,
        "unknown_fields": list(source.unknown_fields),
    }


def _inventory_sources(root: Path) -> tuple[SourceInventory, dict[str, dict[str, Any]]]:
    path = root / DEFAULT_INVENTORY_RELATIVE
    inventory = load_inventory(path)
    by_id: dict[str, dict[str, Any]] = {}
    for dimension in inventory.dimensions:
        for body, entry in dimension.bodies.items():
            for source in entry.sources:
                record = _source_reference(source)
                record["dimension_code"] = dimension.code
                record["body"] = body
                by_id[source.source_id] = record
    return inventory, by_id


def _resolve_sources(
    source_ids: Iterable[str], sources: Mapping[str, Mapping[str, Any]]
) -> list[dict[str, Any]]:
    resolved: list[dict[str, Any]] = []
    for source_id in source_ids:
        record = sources.get(source_id)
        if record is None:
            raise ReleaseError(
                f"source {source_id!r} is not present in the source inventory; "
                "release sources must be auditable"
            )
        resolved.append(dict(record))
    return resolved


def _dedupe_sources(records: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    unique: dict[str, dict[str, Any]] = {}
    for record in records:
        key = str(record.get("source_id"))
        unique.setdefault(key, dict(record))
    return [unique[key] for key in sorted(unique)]


def _typed_origin(observation: TypedObservation) -> str:
    flags = set(observation.quality_flags)
    if any("model_or_reanalysis" in flag for flag in flags) or (
        "model_derived_soil_property" in flags
    ):
        return "model_estimate"
    if "global_model_not_local_sample" in flags:
        return "model_estimate"
    if "spectral_indices_are_not_abundances" in flags:
        return "spectral_context_not_abundance"
    if "mineral_detection_not_validated" in flags:
        return "unvalidated_spectral_detection"
    if any(flag.startswith("map_scale_") for flag in flags):
        return "mapped_evidence"
    if "unverified" in flags:
        return "unverified_context"
    if observation.dimension_code == "D02":
        return "mapped_evidence"
    return "unclassified"


def _typed_support(observations: Sequence[TypedObservation]) -> list[dict[str, Any]]:
    unique: dict[tuple[Any, ...], dict[str, Any]] = {}
    for observation in observations:
        support = observation.spatial_support
        key = (
            support.kind,
            support.crs,
            support.resolution_m,
            support.window_size_m,
            support.native_resolution_m,
            support.source_resolution_m,
            observation.temporal_support.kind,
            observation.depth_support.kind,
        )
        unique.setdefault(
            key,
            {
                "kind": support.kind,
                "crs": support.crs,
                "resolution_m": support.resolution_m,
                "window_size_m": support.window_size_m,
                "native_resolution_m": support.native_resolution_m,
                "source_resolution_m": support.source_resolution_m,
                "temporal_support": observation.temporal_support.kind,
                "depth_support": observation.depth_support.kind,
            },
        )
    return [unique[key] for key in sorted(unique, key=lambda item: canonical_json_bytes(item))]


def _typed_duplicates(
    observations: Sequence[TypedObservation],
    asset_ids: Sequence[str],
) -> dict[str, Any]:
    observation_ids = Counter(observation.observation_id for observation in observations)
    area_boxes: dict[str, dict[str, float]] = {}
    for observation in observations:
        bbox = _bbox_values(observation.spatial_support.bbox)
        if bbox is not None:
            area_boxes.setdefault(observation.area_id, bbox)
    overlaps, examples = _count_overlaps(area_boxes.values(), list(area_boxes))
    return {
        "duplicate_observation_ids": sorted(
            key for key, count in observation_ids.items() if count > 1
        ),
        "duplicate_asset_ids": sorted(
            key for key, count in Counter(asset_ids).items() if count > 1
        ),
        "overlapping_area_pairs": overlaps,
        "overlapping_area_examples": examples,
    }


def _typed_by_body(observations: Sequence[TypedObservation]) -> dict[str, Any]:
    by_body: dict[str, dict[str, Any]] = {}
    for body in BODIES:
        subset = [observation for observation in observations if observation.body == body]
        if not subset:
            continue
        states = Counter(observation.state for observation in subset)
        origins = Counter(_typed_origin(observation) for observation in subset)
        by_body[body] = {
            "observations": len(subset),
            "detected": states.get("detected", 0),
            "unavailable": sum(states.get(state, 0) for state in MISSING_STATE_VALUES),
            "by_state": dict(sorted(states.items())),
            "value_origins": dict(sorted(origins.items())),
        }
    return by_body


def _pilot_summary(
    bundle_dir: Path,
    spec: ArtifactSpec,
    sources_by_id: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    manifest = _read_json(bundle_dir / "manifest.json")
    plan = _read_json(bundle_dir / "plan.json")
    assets = _read_json_list(bundle_dir / "assets.json")
    observations = _load_observations(bundle_dir)
    if not observations:
        raise ReleaseError(f"{bundle_dir}: observations.jsonl is empty")
    if sha256_file(bundle_dir / "observations.jsonl") != manifest.get("observations_sha256"):
        raise ReleaseError(f"{bundle_dir}: observations_sha256 does not match observations.jsonl")
    if sha256_file(bundle_dir / "plan.json") != manifest.get("plan_sha256"):
        raise ReleaseError(f"{bundle_dir}: plan_sha256 does not match plan.json")
    if int(manifest.get("observation_count", -1)) != len(observations):
        raise ReleaseError(f"{bundle_dir}: manifest observation_count does not match observations")
    if int(manifest.get("asset_count", -1)) != len(assets):
        raise ReleaseError(f"{bundle_dir}: manifest asset_count does not match assets.json")
    tasks = plan.get("tasks")
    if not isinstance(tasks, list):
        raise ReleaseError(f"{bundle_dir}: plan.json has no task list")
    attempted = len(tasks)
    raw_entries = manifest.get("raw") or []
    succeeded = len({entry.get("task_id") for entry in raw_entries})
    if succeeded > attempted:
        raise ReleaseError(f"{bundle_dir}: succeeded task count exceeds attempted")
    task_failures = attempted - succeeded
    # Raw rasters are intentionally never part of the Git release. Report every
    # referenced raw path as not bundled so the manifest is identical on a clean
    # checkout and on a machine with a local cache; never hash untracked files.
    bundled_raw = 0
    missing_raw: list[str] = []
    for entry in raw_entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            raise ReleaseError(f"{bundle_dir}: raw entry is missing a path")
        missing_raw.append(entry["path"])
    by_body = _typed_by_body(observations)
    body_origins = {body: dict(values["value_origins"]) for body, values in by_body.items()}
    methods = sorted({observation.method_version for observation in observations})
    units = sorted({observation.unit for observation in observations if observation.unit})
    schema_versions = {
        "release": str(manifest.get("release_version")),
        "plan": str(plan.get("plan_version")),
        "observations": sorted({observation.schema_version for observation in observations})[0],
    }
    dataset_ids = sorted(
        {dataset_id for observation in observations for dataset_id in observation.dataset_ids}
    )
    sources = _resolve_sources(spec.source_ids, sources_by_id)
    datasets = [
        {
            "dataset_id": dataset_id,
            "license": sources[0]["license"] if sources else "unknown",
            "license_url": sources[0]["license_url"] if sources else None,
            "verification_status": sources[0]["availability"] if sources else "unknown",
        }
        for dataset_id in dataset_ids
    ]
    return {
        "counts": {
            "observations": len(observations),
            "areas": len({observation.area_id for observation in observations}),
            "assets": len(assets),
            "tasks_attempted": attempted,
            "tasks_succeeded": succeeded,
            "tasks_failed": task_failures,
            "raw_files_referenced": len(raw_entries),
            "raw_files_bundled": bundled_raw,
            "raw_files_not_bundled": len(missing_raw),
            "raw_files_not_bundled_examples": sorted(missing_raw)[:5],
            "by_dimension": dict(
                sorted(Counter(observation.dimension_code for observation in observations).items())
            ),
            "by_body": by_body,
        },
        "schema_versions": schema_versions,
        "method_versions": methods,
        "units": units,
        "support": _typed_support(observations),
        "value_origins": body_origins,
        "duplicates": _typed_duplicates(
            observations, [str(asset.get("asset_id")) for asset in assets]
        ),
        "source_references": sources,
        "datasets": datasets,
        "limitations": [
            *spec.limitations,
            *(
                [
                    f"{len(missing_raw)} source raster(s) are referenced by the release manifest "
                    "but not bundled in Git; only committed files are SHA-256 verified."
                ]
                if missing_raw
                else []
            ),
        ],
    }


def _geology_summary(
    bundle_dir: Path,
    spec: ArtifactSpec,
    sources_by_id: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    manifest = _read_json(bundle_dir / "manifest.json")
    observations = _load_observations(bundle_dir)
    assets = _read_json_list(bundle_dir / "assets.json")
    areas = _read_json_list(bundle_dir / "areas.json")
    datasets = _read_json_list(bundle_dir / "datasets.json")
    if not observations:
        raise ReleaseError(f"{bundle_dir}: observations.jsonl is empty")
    if sha256_file(bundle_dir / "observations.jsonl") != manifest.get("observations_sha256"):
        raise ReleaseError(f"{bundle_dir}: observations_sha256 does not match observations.jsonl")
    for key, actual in (
        ("observations", len(observations)),
        ("assets", len(assets)),
        ("areas", len(areas)),
        ("datasets", len(datasets)),
    ):
        if int(manifest.get(key, -1)) != actual:
            raise ReleaseError(f"{bundle_dir}: manifest {key} does not match bundle contents")
    by_body = _typed_by_body(observations)
    sources = _resolve_sources(spec.source_ids, sources_by_id)
    area_ids = [str(area.get("area_id")) for area in areas]
    area_boxes = [bbox for area in areas if (bbox := _bbox_values(area.get("bbox"))) is not None]
    area_labels = [
        str(area.get("area_id")) for area in areas if _bbox_values(area.get("bbox")) is not None
    ]
    overlaps, examples = _count_overlaps(area_boxes, area_labels)
    dataset_records = [
        {
            "dataset_id": item.get("dataset_id"),
            "provider": item.get("provider"),
            "product_id": item.get("product_id"),
            "url": item.get("url"),
            "license": item.get("license"),
            "license_url": item.get("license_url"),
            "verification_status": item.get("verification_status"),
            "redistribution_allowed": item.get("redistribution_allowed"),
            "native_resolution_m": item.get("native_resolution_m"),
            "body": item.get("body"),
        }
        for item in datasets
        if isinstance(item, dict)
    ]
    return {
        "counts": {
            "observations": len(observations),
            "areas": len(areas),
            "assets": len(assets),
            "datasets": len(datasets),
            "by_dimension": dict(
                sorted(Counter(observation.dimension_code for observation in observations).items())
            ),
            "by_body": by_body,
        },
        "schema_versions": {
            "export": str(manifest.get("export_version")),
            "observations": sorted({observation.schema_version for observation in observations})[0],
        },
        "method_versions": sorted({observation.method_version for observation in observations}),
        "units": sorted({observation.unit for observation in observations if observation.unit}),
        "support": _typed_support(observations),
        "value_origins": {body: dict(values["value_origins"]) for body, values in by_body.items()},
        "duplicates": {
            **_typed_duplicates(observations, [str(asset.get("asset_id")) for asset in assets]),
            "duplicate_area_ids": sorted(
                key for key, count in Counter(area_ids).items() if count > 1
            ),
            "overlapping_area_pairs": overlaps,
            "overlapping_area_examples": examples,
        },
        "source_references": sources,
        "datasets": dataset_records,
        "limitations": list(spec.limitations),
    }


def _snapshot_summary(
    snapshot_path: Path,
    root: Path,
    spec: ArtifactSpec,
    sources_by_id: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    verify_snapshot(snapshot_path, root=root, check_artifacts=True)
    snapshot = load_snapshot(snapshot_path)
    unique_candidates: dict[str, dict[str, Any]] = {}
    unique_references: dict[str, dict[str, Any]] = {}
    candidate_body: dict[str, str] = {}
    reference_body: dict[str, str] = {}
    candidate_records = 0
    reference_records = 0
    feature_by_body: Counter[str] = Counter()
    reference_features_by_body: Counter[str] = Counter()
    context_by_body: Counter[str] = Counter()
    candidate_boxes: list[dict[str, float]] = []
    candidate_labels: list[str] = []
    seen_context: set[tuple[str, str]] = set()
    methods: set[str] = set()
    units: set[str] = set()
    manifest_versions: set[str] = set()
    for entry in snapshot["manifests"]:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            raise ReleaseError("snapshot manifest entry is missing a path")
        relative = entry["path"]
        raw = _read_json(resolve_snapshot_path(root, relative))
        body = _manifest_body(relative)
        if isinstance(raw.get("manifest_version"), str):
            manifest_versions.add(raw["manifest_version"])
        for candidate in raw.get("candidates") or []:
            if not isinstance(candidate, dict):
                continue
            candidate_records += 1
            location_id = candidate.get("location_id")
            if not isinstance(location_id, str):
                continue
            if location_id not in unique_candidates:
                unique_candidates[location_id] = candidate
                candidate_body[location_id] = body
                features = candidate.get("features") or {}
                feature_by_body[body] += len(features)
                for observation in features.values():
                    if isinstance(observation, dict):
                        if isinstance(observation.get("method_version"), str):
                            methods.add(observation["method_version"])
                        if isinstance(observation.get("unit"), str):
                            units.add(observation["unit"])
                bbox = _bbox_values(candidate.get("bbox"))
                if bbox is not None:
                    candidate_boxes.append(bbox)
                    candidate_labels.append(location_id)
            # Context overlays repeat the same candidate id on purpose; count each
            # (candidate, context feature) pair once so the overlay is not lost.
            for feature_id, observation in (candidate.get("context_observations") or {}).items():
                context_key = (location_id, str(feature_id))
                if context_key in seen_context:
                    continue
                seen_context.add(context_key)
                context_by_body[body] += 1
                if isinstance(observation, dict):
                    if isinstance(observation.get("method_version"), str):
                        methods.add(observation["method_version"])
                    if isinstance(observation.get("unit"), str):
                        units.add(observation["unit"])
        for reference in raw.get("references") or []:
            if not isinstance(reference, dict):
                continue
            reference_records += 1
            reference_id = reference.get("reference_id")
            if not isinstance(reference_id, str) or reference_id in unique_references:
                continue
            unique_references[reference_id] = reference
            reference_body_value = reference.get("body")
            reference_body_value = reference_body_value if reference_body_value in BODIES else body
            reference_body[reference_id] = reference_body_value
            features = reference.get("features") or {}
            reference_features_by_body[reference_body_value] += len(features)
            for observation in features.values():
                if isinstance(observation, dict):
                    if isinstance(observation.get("method_version"), str):
                        methods.add(observation["method_version"])
                    if isinstance(observation.get("unit"), str):
                        units.add(observation["unit"])
    overlaps, overlap_examples = _count_overlaps(candidate_boxes, candidate_labels)
    datasets = [
        {
            "dataset_id": dataset.get("dataset_id"),
            "provider": dataset.get("provider"),
            "product_id": dataset.get("product_id"),
            "url": dataset.get("url"),
            "license": dataset.get("license"),
            "license_url": dataset.get("license_url"),
            "status": dataset.get("status"),
            "candidate_count": dataset.get("candidate_count"),
            "reference_count": dataset.get("reference_count"),
        }
        for dataset in snapshot.get("datasets") or []
        if isinstance(dataset, dict)
    ]
    sources = _resolve_sources(spec.source_ids, sources_by_id)
    by_body = {}
    for body in BODIES:
        candidates = sum(1 for value in candidate_body.values() if value == body)
        references = sum(1 for value in reference_body.values() if value == body)
        if not candidates and not references:
            continue
        by_body[body] = {
            "candidates": candidates,
            "references": references,
            "feature_observations": feature_by_body.get(body, 0),
            "reference_feature_observations": reference_features_by_body.get(body, 0),
            "context_observations": context_by_body.get(body, 0),
        }
    return {
        "counts": {
            "candidates": len(unique_candidates),
            "references": len(unique_references),
            "datasets": len(datasets),
            "manifests": len(snapshot["manifests"]),
            "feature_observations": sum(feature_by_body.values()),
            "reference_feature_observations": sum(reference_features_by_body.values()),
            "context_observations": sum(context_by_body.values()),
            "candidate_records_shared_across_manifests": candidate_records - len(unique_candidates),
            "reference_records_shared_across_manifests": (
                reference_records - len(unique_references)
            ),
            "by_body": by_body,
        },
        "schema_versions": {
            "snapshot": str(snapshot.get("snapshot_version")),
            "manifest": sorted(manifest_versions)[0] if manifest_versions else "unknown",
        },
        "method_versions": sorted(methods),
        "units": sorted(units),
        "support": [
            {
                "kind": "window",
                "crs": "EPSG:4326",
                "window_size_m": 1980.0,
                "analysis_resolution_m": 90.0,
            }
        ],
        "value_origins": {
            "Earth": {"derived_from_measured_dem": feature_by_body.get("Earth", 0)},
            "Moon": {"derived_from_measured_dem": reference_features_by_body.get("Moon", 0)},
            "Mars": {"derived_from_measured_dem": reference_features_by_body.get("Mars", 0)},
        },
        "context_value_origins": {
            body: {"mapped_classification": context_by_body.get(body, 0)}
            for body in BODIES
            if context_by_body.get(body)
        },
        "duplicates": {
            "shared_candidate_records": candidate_records - len(unique_candidates),
            "shared_reference_records": reference_records - len(unique_references),
            "note": "Context overlays repeat candidate ids on purpose; the index stores one row.",
        },
        "overlaps": {
            "candidate_window_pairs": overlaps,
            "candidate_window_examples": overlap_examples,
            "reference_window_pairs": 0,
            "note": "Reference windows have no WGS84 bbox claim; overlap is not evaluated.",
        },
        "source_references": sources,
        "datasets": datasets,
        "limitations": list(spec.limitations),
    }


def _coarse_summary(
    primary: Path,
    spec: ArtifactSpec,
    sources_by_id: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    index_root = primary.parent.parent.parent
    snapshot_id = primary.parent.name
    try:
        manifest = verify_index(index_root, snapshot_id=snapshot_id)
    except IndexError_ as exc:
        raise ReleaseError(f"coarse index verification failed: {exc}") from exc
    partition_paths = [index_root / partition["path"] for partition in manifest["partitions"]]
    if not partition_paths:
        raise ReleaseError("coarse index has no partitions")
    escaped = ", ".join("'" + str(path).replace("'", "''") + "'" for path in partition_paths)
    con = duckdb.connect()
    try:
        row_count, distinct_ids, region_count = con.execute(
            f"SELECT count(*), count(DISTINCT candidate_id), count(DISTINCT region_id) "
            f"FROM read_parquet([{escaped}])"
        ).fetchone()
        rows = con.execute(
            f"SELECT candidate_id, west, south, east, north, "
            f"slope_mean_deg, slope_p90_deg, flat_fraction "
            f"FROM read_parquet([{escaped}]) ORDER BY candidate_id"
        ).fetchall()
        duplicate_count = con.execute(
            f"SELECT count(*) FROM (SELECT candidate_id FROM read_parquet([{escaped}]) "
            "GROUP BY candidate_id HAVING count(*) > 1)"
        ).fetchone()[0]
        valid_fraction_min, valid_fraction_max = con.execute(
            f"SELECT min(valid_fraction), max(valid_fraction) FROM read_parquet([{escaped}])"
        ).fetchone()
        crs_values = [
            row[0]
            for row in con.execute(
                f"SELECT DISTINCT crs FROM read_parquet([{escaped}]) ORDER BY 1"
            ).fetchall()
        ]
    finally:
        con.close()
    feature_stats: dict[str, dict[str, Any]] = {}
    column_index = {
        "coarse_slope_mean_deg": 5,
        "coarse_slope_p90_deg": 6,
        "coarse_flat_fraction_lt5deg": 7,
    }
    for feature in manifest["features"]:
        feature_id = str(feature["feature_id"])
        index = column_index.get(feature_id)
        if index is None:
            continue
        values = [float(row[index]) for row in rows if row[index] is not None]
        nulls = len(rows) - len(values)
        feature_stats[feature_id] = {
            "non_null": len(values),
            "null": nulls,
            "min": min(values) if values else None,
            "max": max(values) if values else None,
            "mean": (math.fsum(values) / len(values)) if values else None,
            "unit": feature.get("unit"),
            "score_role": feature.get("score_role"),
            "method_version": feature.get("method_version"),
            "source_resolution_m": feature.get("source_resolution_m"),
        }
    boxes = [
        {
            "west": float(row[1]),
            "south": float(row[2]),
            "east": float(row[3]),
            "north": float(row[4]),
        }
        for row in rows
    ]
    overlaps, overlap_examples = _count_overlaps(boxes, [str(row[0]) for row in rows])
    null_cells = sum(stats["null"] for stats in feature_stats.values())
    complete = sum(
        1
        for row in rows
        if all(row[column_index[feature_id]] is not None for feature_id in feature_stats)
    )
    return {
        "counts": {
            "cells": int(row_count),
            "distinct_candidates": int(distinct_ids),
            "regions": int(region_count),
            "partitions": len(manifest["partitions"]),
            "features": len(feature_stats),
            "null_feature_values": null_cells,
            "cells_with_complete_features": complete,
            "duplicate_candidate_ids": int(duplicate_count),
            "feature_stats": feature_stats,
            "by_body": {"Earth": {"cells": int(row_count)}},
        },
        "schema_versions": {
            "index": str(manifest.get("index_version")),
            "parquet": str(manifest.get("parquet_schema_version")),
            "grid": str(manifest.get("grid_version")),
            "method": str(manifest.get("method_version")),
        },
        "method_versions": [str(manifest.get("method_version"))],
        "units": sorted(
            {str(feature.get("unit")) for feature in manifest["features"] if feature.get("unit")}
        ),
        "support": [
            {
                "kind": "window",
                "window_size_m": 5000.0,
                "stride_m": 5000.0,
                "source_resolution_m": 250.0,
                "valid_fraction_min": float(valid_fraction_min),
                "valid_fraction_max": float(valid_fraction_max),
                "crs": crs_values,
            }
        ],
        "value_origins": {"Earth": {"derived_from_measured_dem": int(row_count)}},
        "duplicates": {
            "duplicate_candidate_ids": int(duplicate_count),
            "note": "Content-addressed partitions are verified row-by-row.",
        },
        "overlaps": {
            "overlapping_window_pairs": overlaps,
            "overlapping_window_examples": overlap_examples,
            "note": (
                "Candidate ids are unique and projected stride_m == window_size_m. "
                "Adjacent WGS84 bbox envelopes can overlap by a sub-cell margin because "
                "each cell envelope is rounded independently after projection."
            ),
        },
        "coverage": {
            "label": manifest.get("coverage", {}).get("label"),
            "regions": manifest.get("coverage", {}).get("regions"),
            "bbox": manifest.get("coverage", {}).get("bbox"),
            "global_claim": False,
        },
        "source_references": _resolve_sources(spec.source_ids, sources_by_id),
        "datasets": [],
        "limitations": list(spec.limitations),
    }


def _catalog_summary(primary: Path) -> dict[str, Any]:
    inventory = load_inventory(primary)
    by_dimension: dict[str, dict[str, Any]] = {}
    source_count = 0
    for dimension in inventory.dimensions:
        body_counts: dict[str, int] = {}
        availability: Counter[str] = Counter()
        dimension_count = 0
        for body, entry in dimension.bodies.items():
            body_counts[body] = len(entry.sources)
            dimension_count += len(entry.sources)
            availability.update(source.availability for source in entry.sources)
        source_count += dimension_count
        by_dimension[dimension.code] = {
            "sources": dimension_count,
            "by_body": body_counts,
            "availability": dict(sorted(availability.items())),
        }
    return {
        "counts": {
            "dimensions": len(inventory.dimensions),
            "sources": source_count,
            "by_dimension": by_dimension,
        },
        "schema_versions": {"inventory": str(inventory.inventory_version)},
        "method_versions": [],
        "units": [],
        "support": [],
        "value_origins": {},
        "duplicates": {},
        "overlaps": {},
        "source_references": [],
        "datasets": [],
        "limitations": ["Inventory statuses describe sources, not observations."],
    }


def _collect_snapshot_files(primary: Path, root: Path) -> list[Path]:
    snapshot = load_snapshot(primary)
    paths = [primary]
    for entry in snapshot.get("artifacts") or []:
        if isinstance(entry, dict) and isinstance(entry.get("path"), str):
            paths.append(resolve_snapshot_path(root, entry["path"]))
    for entry in snapshot.get("manifests") or []:
        if isinstance(entry, dict) and isinstance(entry.get("path"), str):
            paths.append(resolve_snapshot_path(root, entry["path"]))
    return paths


def _collect_bundle_files(primary: Path) -> list[Path]:
    base = primary.parent
    return [
        path
        for path in sorted(base.rglob("*"))
        if path.is_file()
        and "__pycache__" not in path.parts
        and not {"raw", "cache"} & set(path.relative_to(base).parts)
        and path.suffix not in {".pyc", ".pyo"}
    ]


def _collect_coarse_files(primary: Path) -> list[Path]:
    manifest = _read_json(primary)
    if primary.parent.parent.name == "snapshots":
        base = primary.parent.parent.parent
    else:
        base = primary.parent
    paths = [primary]
    for partition in manifest.get("partitions") or []:
        if not isinstance(partition, dict) or not isinstance(partition.get("path"), str):
            raise ReleaseError(f"{primary}: partition entry is missing a path")
        paths.append(base / partition["path"])
    return paths


def _collect_files(spec: ArtifactSpec, root: Path) -> list[Path]:
    primary = root / spec.primary_path
    if not primary.is_file():
        raise ReleaseError(f"approved artifact is missing: {spec.primary_path}")
    if spec.kind == "pinned_snapshot":
        paths = _collect_snapshot_files(primary, root)
    elif spec.kind in {"typed_bundle_export", "typed_bundle_acquisition"}:
        paths = _collect_bundle_files(primary)
    elif spec.kind == "coarse_parquet_index":
        paths = _collect_coarse_files(primary)
    else:
        paths = [primary]
    unique: dict[str, Path] = {}
    for path in paths:
        unique[_display_path(path, root)] = path
    missing = [relative for relative, path in unique.items() if not path.is_file()]
    if missing:
        raise ReleaseError(f"{spec.artifact_id}: missing file(s): {', '.join(sorted(missing))}")
    return [unique[key] for key in sorted(unique)]


def _file_entries(paths: Sequence[Path], root: Path) -> list[dict[str, Any]]:
    entries = []
    for path in paths:
        entries.append(
            {
                "path": _display_path(path, root),
                "sha256": sha256_file(path),
                "bytes": path.stat().st_size,
            }
        )
    return entries


def _artifact_digest(files: Sequence[Mapping[str, Any]]) -> str:
    return hashlib.sha256(canonical_json_bytes(list(files))).hexdigest()


def _artifact_record(
    spec: ArtifactSpec,
    root: Path,
    sources_by_id: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    primary = root / spec.primary_path
    paths = _collect_files(spec, root)
    files = _file_entries(paths, root)
    if spec.kind == "pinned_snapshot":
        summary = _snapshot_summary(primary, root, spec, sources_by_id)
    elif spec.kind == "typed_bundle_export":
        summary = _geology_summary(primary.parent, spec, sources_by_id)
    elif spec.kind == "typed_bundle_acquisition":
        summary = _pilot_summary(primary.parent, spec, sources_by_id)
    elif spec.kind == "coarse_parquet_index":
        summary = _coarse_summary(primary, spec, sources_by_id)
    elif spec.kind == "source_inventory":
        summary = _catalog_summary(primary)
    else:  # pragma: no cover - registry is closed
        raise ReleaseError(f"unsupported artifact kind: {spec.kind}")
    primary_relative = _display_path(primary, root)
    primary_entry = next(entry for entry in files if entry["path"] == primary_relative)
    record = {
        "artifact_id": spec.artifact_id,
        "kind": spec.kind,
        "path": primary_relative,
        "sha256": _artifact_digest(files),
        "hash_method": "sha256(canonical-json(files[]))",
        "primary_sha256": primary_entry["sha256"],
        "bytes": sum(entry["bytes"] for entry in files),
        "file_count": len(files),
        "files": files,
        "dimensions": list(spec.dimensions),
        "bodies": list(spec.bodies),
        "value_origin": spec.value_origin,
        **summary,
    }
    return record


def _release_manifest_identity(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "release_version": payload.get("release_version"),
        "scope": payload.get("scope"),
        "counts": payload.get("counts"),
        "dimensions": payload.get("dimensions"),
        "absent_dimensions": payload.get("absent_dimensions"),
        "artifacts": payload.get("artifacts"),
        "source_references": payload.get("source_references"),
        "dataset_references": payload.get("dataset_references"),
    }


def _release_id(payload: Mapping[str, Any]) -> str:
    digest = hashlib.sha256(canonical_json_bytes(_release_manifest_identity(payload))).hexdigest()
    return f"rel-{digest[:16]}"


def _cell_reason(
    dimension_code: str,
    body: str,
    inventory: SourceInventory,
) -> str:
    dimension = next(item for item in inventory.dimensions if item.code == dimension_code)
    entries = dimension.bodies[body].sources
    statuses = sorted({f"{entry.availability}/{entry.data_status}" for entry in entries})
    source_ids = sorted(entry.source_id for entry in entries)
    return (
        f"no validated observation in this release; inventory sources "
        f"{', '.join(source_ids)} report {'; '.join(statuses)}"
    )


def _dimension_payload(
    artifacts: Sequence[Mapping[str, Any]],
    inventory: SourceInventory,
    sources_by_id: Mapping[str, Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    cells: dict[tuple[str, str], dict[str, Any]] = {
        (dimension, body): {
            "dimension_code": dimension,
            "body": body,
            "status": "absent",
            "artifacts": [],
            "attempted": None,
            "attempted_unit": None,
            "valid": None,
            "valid_unit": None,
            "failed": None,
            "failed_unit": None,
            "unavailable": None,
            "unavailable_unit": None,
            "observations": 0,
            "feature_observations": 0,
            "context_observations": 0,
            "coarse_cells": 0,
            "value_origins": {},
            "units": [],
            "support": [],
            "breakdown": [],
        }
        for dimension in DIMENSION_CODES
        for body in BODIES
    }
    origin_counts: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    units: dict[tuple[str, str], set[str]] = defaultdict(set)
    support: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for artifact in artifacts:
        if artifact.get("kind") == "source_inventory":
            continue
        counts = artifact.get("counts") or {}
        artifact_id = str(artifact.get("artifact_id"))
        for dimension in artifact.get("dimensions") or []:
            for body in artifact.get("bodies") or []:
                if (dimension, body) not in cells:
                    continue
                cell = cells[(dimension, body)]
                cell["status"] = "present"
                cell["artifacts"].append(artifact_id)
                body_counts = (counts.get("by_body") or {}).get(body) or {}
                if artifact.get("kind") == "pinned_snapshot":
                    cell["observations"] += int(body_counts.get("feature_observations", 0)) + int(
                        body_counts.get("reference_feature_observations", 0)
                    )
                    cell["feature_observations"] += int(body_counts.get("feature_observations", 0))
                    cell["context_observations"] += int(body_counts.get("context_observations", 0))
                    attempted = int(body_counts.get("candidates", 0)) + int(
                        body_counts.get("references", 0)
                    )
                    if attempted:
                        cell["attempted"] = attempted
                        cell["attempted_unit"] = (
                            "candidate_windows" if body == "Earth" else "reference_windows"
                        )
                    valid = int(body_counts.get("feature_observations", 0)) + int(
                        body_counts.get("reference_feature_observations", 0)
                    )
                    if valid:
                        cell["valid"] = valid
                        cell["valid_unit"] = "feature_observations"
                elif artifact.get("kind") in {
                    "typed_bundle_export",
                    "typed_bundle_acquisition",
                }:
                    cell["observations"] += int(body_counts.get("observations", 0))
                    if "tasks_attempted" in counts and body == "Earth":
                        cell["attempted"] = int(counts["tasks_attempted"])
                        cell["attempted_unit"] = "acquisition_tasks"
                    if body_counts:
                        cell["valid"] = int(body_counts.get("detected", 0))
                        cell["valid_unit"] = "detected_observations"
                        cell["unavailable"] = int(body_counts.get("unavailable", 0))
                        cell["unavailable_unit"] = "missing_state_observations"
                        if body == "Earth" and "tasks_failed" in counts:
                            cell["failed"] = int(counts["tasks_failed"])
                            cell["failed_unit"] = "acquisition_tasks"
                elif artifact.get("kind") == "coarse_parquet_index":
                    cell["coarse_cells"] += int(body_counts.get("cells", 0))
                if isinstance(artifact.get("value_origins"), dict):
                    for origin, value in (artifact["value_origins"].get(body) or {}).items():
                        origin_counts[(dimension, body)][str(origin)] += int(value)
                if isinstance(artifact.get("context_value_origins"), dict):
                    for origin, value in (
                        artifact["context_value_origins"].get(body) or {}
                    ).items():
                        origin_counts[(dimension, body)][str(origin)] += int(value)
                for unit in artifact.get("units") or []:
                    if isinstance(unit, str):
                        units[(dimension, body)].add(unit)
                for item in artifact.get("support") or []:
                    if isinstance(item, dict):
                        support[(dimension, body)].append(item)
                breakdown = {
                    "artifact_id": artifact_id,
                    "observations": int(body_counts.get("observations", 0)),
                }
                if artifact.get("kind") == "coarse_parquet_index":
                    breakdown["attempted"] = int(body_counts.get("cells", 0))
                    breakdown["attempted_unit"] = "indexed_cells"
                    breakdown["valid"] = int(counts.get("cells_with_complete_features", 0))
                    breakdown["valid_unit"] = "cells_with_complete_features"
                elif artifact.get("kind") == "pinned_snapshot":
                    breakdown["attempted"] = int(body_counts.get("candidates", 0)) + int(
                        body_counts.get("references", 0)
                    )
                    breakdown["attempted_unit"] = (
                        "candidate_windows" if body == "Earth" else "reference_windows"
                    )
                    breakdown["valid"] = int(body_counts.get("feature_observations", 0)) + int(
                        body_counts.get("reference_feature_observations", 0)
                    )
                    breakdown["valid_unit"] = "feature_observations"
                elif "tasks_attempted" in counts:
                    breakdown["attempted"] = int(counts["tasks_attempted"])
                    breakdown["attempted_unit"] = "acquisition_tasks"
                    breakdown["valid"] = int(body_counts.get("detected", 0))
                    breakdown["valid_unit"] = "detected_observations"
                    breakdown["failed"] = int(counts.get("tasks_failed", 0))
                    breakdown["failed_unit"] = "acquisition_tasks"
                cell["breakdown"].append(breakdown)
    for key, cell in cells.items():
        cell["value_origins"] = dict(sorted(origin_counts[key].items()))
        cell["units"] = sorted(units[key])
        deduped_support: dict[str, dict[str, Any]] = {}
        for item in support[key]:
            deduped_support.setdefault(canonical_json_bytes(item).decode("utf-8"), item)
        cell["support"] = [deduped_support[item] for item in sorted(deduped_support)]
    dimensions: list[dict[str, Any]] = []
    absent: list[dict[str, Any]] = []
    for dimension in DIMENSION_CODES:
        body_cells = {body: cells[(dimension, body)] for body in BODIES}
        sources = _resolve_sources(
            [source.source_id for source in _dimension_sources(inventory, dimension)],
            sources_by_id,
        )
        dimensions.append(
            {
                "dimension_code": dimension,
                "name": DIMENSION_NAMES[dimension],
                "bodies": body_cells,
                "sources": sources,
            }
        )
        for body in BODIES:
            if body_cells[body]["status"] == "absent":
                absent.append(
                    {
                        "dimension_code": dimension,
                        "body": body,
                        "reason": _cell_reason(dimension, body, inventory),
                    }
                )
    return dimensions, absent


def _dimension_sources(inventory: SourceInventory, dimension_code: str) -> list[Any]:
    dimension = next(item for item in inventory.dimensions if item.code == dimension_code)
    return [source for body in BODIES for source in dimension.bodies[body].sources]


def _build_release_payload(
    *,
    root: Path = REPO_ROOT,
    source_commit: str | None = None,
    notes: str = DEFAULT_NOTES,
) -> dict[str, Any]:
    root = root.resolve()
    inventory, sources_by_id = _inventory_sources(root)
    artifact_records = [_artifact_record(spec, root, sources_by_id) for spec in ARTIFACTS]
    dimensions, absent = _dimension_payload(artifact_records, inventory, sources_by_id)
    by_id = {artifact["artifact_id"]: artifact for artifact in artifact_records}
    typed_ids = (
        "typed-d02-geology",
        "typed-d03-thermal",
        "typed-d04-water-ice",
        "typed-d05-illumination",
        "typed-d05-spice-geometry",
        "typed-d06-regolith",
    )
    typed_by_dimension = {
        artifact_id: int(by_id[artifact_id]["counts"]["observations"]) for artifact_id in typed_ids
    }
    source_references = _dedupe_sources(
        record
        for artifact in artifact_records
        for record in artifact.get("source_references") or []
    )
    dataset_references = _dedupe_sources(
        {
            "source_id": dataset.get("dataset_id"),
            "provider": dataset.get("provider"),
            "product": dataset.get("product_id"),
            "url": dataset.get("url"),
            "license": dataset.get("license"),
            "license_url": dataset.get("license_url"),
            "availability": dataset.get("verification_status") or dataset.get("status"),
            "data_status": "referenced_by_observation",
            "comparability": None,
            "unknown_fields": [],
        }
        for artifact in artifact_records
        for dataset in artifact.get("datasets") or []
    )
    snapshot_counts = by_id["fine-terrain-snapshot"]["counts"]
    coarse_counts = by_id["coarse-terrain-index"]["counts"]
    fine_snapshot = load_snapshot(root / DEFAULT_SNAPSHOT_RELATIVE)
    counts = {
        "artifacts": len(artifact_records),
        "files": sum(artifact["file_count"] for artifact in artifact_records),
        "fine_snapshot_id": fine_snapshot["snapshot_id"],
        "fine_data_version": fine_snapshot["data_version"],
        "fine_candidates": int(snapshot_counts["candidates"]),
        "fine_references": int(snapshot_counts["references"]),
        "fine_datasets": int(snapshot_counts["datasets"]),
        "fine_feature_observations": int(snapshot_counts["feature_observations"]),
        "fine_context_observations": int(snapshot_counts["context_observations"]),
        "typed_observations": sum(typed_by_dimension.values()),
        "typed_by_dimension": typed_by_dimension,
        "coarse_cells": int(coarse_counts["cells"]),
        "coarse_regions": int(coarse_counts["regions"]),
        "coarse_partitions": int(coarse_counts["partitions"]),
        "absent_body_dimension_cells": len(absent),
        "source_references": len(source_references),
        "dataset_references": len(dataset_references),
    }
    payload: dict[str, Any] = {
        "release_version": RELEASE_VERSION,
        "release_id": "",
        "generated_at": _derived_generated_at(root),
        "generated_by": GENERATED_BY,
        "source_commit": source_commit,
        "scope": {
            "coverage_label": COVERAGE_LABEL,
            "global_claim": False,
            "bodies": list(BODIES),
            "dimensions": list(DIMENSION_CODES),
            "coarse_regions": list(by_id["coarse-terrain-index"]["coverage"]["regions"] or []),
            "notes": [
                "Only committed artifacts are combined; no download or remote write occurs.",
                "Counts describe the bounded committed subset, not global coverage.",
            ],
        },
        "counts": counts,
        "dimensions": dimensions,
        "absent_dimensions": absent,
        "artifacts": artifact_records,
        "source_references": source_references,
        "dataset_references": dataset_references,
        "notes": notes,
    }
    payload["release_id"] = _release_id(payload)
    return payload


def build_release(
    *,
    root: Path = REPO_ROOT,
    output: Path | None = None,
    source_commit: str | None = None,
    notes: str = DEFAULT_NOTES,
) -> dict[str, Any]:
    """Build and optionally write a deterministic multi-dimension release manifest."""
    root = root.resolve()
    payload = _build_release_payload(root=root, source_commit=source_commit, notes=notes)
    if output is not None:
        output = output if output.is_absolute() else root / output
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(pretty_json_bytes(payload))
    return payload


def _eda_artifact_summary(artifact: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "artifact_id": artifact.get("artifact_id"),
        "kind": artifact.get("kind"),
        "path": artifact.get("path"),
        "dimensions": artifact.get("dimensions"),
        "bodies": artifact.get("bodies"),
        "counts": artifact.get("counts"),
        "units": artifact.get("units"),
        "support": artifact.get("support"),
        "value_origins": artifact.get("value_origins"),
        "context_value_origins": artifact.get("context_value_origins"),
        "duplicates": artifact.get("duplicates"),
        "overlaps": artifact.get("overlaps"),
        "coverage": artifact.get("coverage"),
        "limitations": artifact.get("limitations"),
    }


def build_eda(
    *,
    release: Mapping[str, Any] | None = None,
    root: Path = REPO_ROOT,
) -> dict[str, Any]:
    """Build the per-dimension/source EDA and count report."""
    payload = dict(release) if release is not None else _build_release_payload(root=root)
    artifacts = payload["artifacts"]
    by_id = {artifact.get("artifact_id"): artifact for artifact in artifacts}
    dimensions = []
    for dimension in payload["dimensions"]:
        dimensions.append(
            {
                "dimension_code": dimension["dimension_code"],
                "name": dimension["name"],
                "bodies": dimension["bodies"],
                "sources": dimension["sources"],
            }
        )
    limitations = [
        "This release is a bounded local subset; it is not a global or exhaustive survey.",
        "Counts cover only committed artifacts; absent body/dimension cells are explicit.",
        "NASA POWER and SoilGrids values are model estimates, not local measurements.",
        "D02 map units/spectral indices are evidence, not mineral abundance.",
        "Coarse 250 m terrain is a separate method from the fine 90 m terrain metrics.",
        "No R2 publication, live deployment, or database reset was performed.",
    ]
    return {
        "eda_version": EDA_VERSION,
        "release_id": payload.get("release_id"),
        "generated_at": payload.get("generated_at"),
        "coverage_label": COVERAGE_LABEL,
        "global_claim": False,
        "scope": payload.get("scope"),
        "counts": payload.get("counts"),
        "dimensions": dimensions,
        "absent_dimensions": payload.get("absent_dimensions"),
        "artifacts": [_eda_artifact_summary(artifact) for artifact in artifacts],
        "quality": {
            "duplicate_observation_ids": sum(
                len((artifact.get("duplicates") or {}).get("duplicate_observation_ids") or [])
                for artifact in artifacts
            ),
            "duplicate_asset_ids": sum(
                len((artifact.get("duplicates") or {}).get("duplicate_asset_ids") or [])
                for artifact in artifacts
            ),
            "duplicate_candidate_ids": int(
                (by_id["coarse-terrain-index"].get("duplicates") or {}).get(
                    "duplicate_candidate_ids", 0
                )
            ),
            "shared_candidate_records": int(
                (by_id["fine-terrain-snapshot"].get("duplicates") or {}).get(
                    "shared_candidate_records", 0
                )
            ),
            "overlapping_fine_candidate_pairs": int(
                (by_id["fine-terrain-snapshot"].get("overlaps") or {}).get(
                    "candidate_window_pairs", 0
                )
            ),
            "overlapping_coarse_window_pairs": int(
                (by_id["coarse-terrain-index"].get("overlaps") or {}).get(
                    "overlapping_window_pairs", 0
                )
            ),
        },
        "limitations": limitations,
    }


def write_eda(
    *,
    root: Path = REPO_ROOT,
    output: Path | None = None,
    release: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    payload = build_eda(release=release, root=root)
    if output is not None:
        output = output if output.is_absolute() else root / output
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(pretty_json_bytes(payload))
    return payload


def verify_release(
    manifest_path: Path,
    *,
    root: Path = REPO_ROOT,
) -> dict[str, Any]:
    """Recompute every artifact checksum, semantic count, and the release id."""
    root = root.resolve()
    manifest_path = manifest_path.resolve()
    manifest = _read_json(manifest_path)
    if manifest.get("release_version") != RELEASE_VERSION:
        raise ReleaseVerificationError(
            f"unsupported release_version {manifest.get('release_version')!r}; "
            f"supported: {RELEASE_VERSION}"
        )
    try:
        rebuilt = _build_release_payload(
            root=root,
            source_commit=manifest.get("source_commit"),
            notes=str(manifest.get("notes") or DEFAULT_NOTES),
        )
    except ReleaseVerificationError:
        raise
    except (ReleaseError, SnapshotError) as exc:
        raise ReleaseVerificationError(f"artifact verification failed: {exc}") from exc
    expected_id = _release_id(manifest)
    if manifest.get("release_id") != expected_id:
        raise ReleaseVerificationError(
            f"release id mismatch: file says {manifest.get('release_id')!r}, "
            f"content hashes to {expected_id!r}"
        )
    if rebuilt["release_id"] != manifest.get("release_id"):
        raise ReleaseVerificationError(
            f"release content does not match the pinned inputs: "
            f"manifest {manifest.get('release_id')!r}, inputs {rebuilt['release_id']!r}"
        )
    recorded = _release_manifest_identity(manifest)
    actual = _release_manifest_identity(rebuilt)
    if canonical_json_bytes(recorded) != canonical_json_bytes(actual):
        raise ReleaseVerificationError(
            "release identity fields changed; an artifact or semantic count was tampered with"
        )
    recorded_artifacts = manifest.get("artifacts") or []
    if len(recorded_artifacts) != len(rebuilt["artifacts"]):
        raise ReleaseVerificationError("artifact count changed in the release manifest")
    for recorded_artifact, actual_artifact in zip(
        recorded_artifacts, rebuilt["artifacts"], strict=True
    ):
        if recorded_artifact.get("sha256") != actual_artifact.get("sha256"):
            raise ReleaseVerificationError(
                f"artifact checksum mismatch: {recorded_artifact.get('artifact_id')}"
            )
        if recorded_artifact.get("files") != actual_artifact.get("files"):
            raise ReleaseVerificationError(
                f"artifact file list mismatch: {recorded_artifact.get('artifact_id')}"
            )
    return {
        "release_id": manifest["release_id"],
        "generated_at": manifest.get("generated_at"),
        "artifacts_verified": len(manifest.get("artifacts") or []),
        "files_verified": sum(
            len(artifact.get("files") or []) for artifact in manifest.get("artifacts") or []
        ),
        "counts": manifest.get("counts"),
        "absent_dimensions": len(manifest.get("absent_dimensions") or []),
    }


def _human_report(payload: Mapping[str, Any]) -> str:
    counts = payload.get("counts") or {}
    lines = [
        f"Multi-dimension release {payload.get('release_id')} ({payload.get('release_version')})",
        f"generated_at={payload.get('generated_at')} coverage={COVERAGE_LABEL} global_claim=False",
        "",
        f"artifacts={counts.get('artifacts')} files={counts.get('files')} "
        f"fine_candidates={counts.get('fine_candidates')} "
        f"fine_references={counts.get('fine_references')} "
        f"typed_observations={counts.get('typed_observations')} "
        f"coarse_cells={counts.get('coarse_cells')} "
        f"absent_body_dimension_cells={counts.get('absent_body_dimension_cells')}",
        "",
        "dimension/body status:",
    ]
    for dimension in payload.get("dimensions") or []:
        statuses = ", ".join(
            f"{body}={cell['status']}" for body, cell in dimension["bodies"].items()
        )
        lines.append(f"  {dimension['dimension_code']} {dimension['name']}: {statuses}")
    if payload.get("absent_dimensions"):
        lines.extend(["", "explicitly absent:"])
        for item in payload["absent_dimensions"]:
            lines.append(f"  {item['dimension_code']} {item['body']}: {item['reason']}")
    lines.extend(["", "artifacts:"])
    for artifact in payload.get("artifacts") or []:
        lines.append(
            f"  {artifact['artifact_id']:<26} kind={artifact['kind']:<24} "
            f"files={artifact['file_count']:<3} sha256={artifact['sha256'][:16]}"
        )
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python pipelines/build_release.py",
        description="Build, verify, or report the bounded multi-dimension release manifest.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build", help="Build the release manifest and EDA report")
    build.add_argument("--root", default=str(REPO_ROOT))
    build.add_argument("--output", default=DEFAULT_MANIFEST_RELATIVE)
    build.add_argument("--eda-output", default=DEFAULT_EDA_RELATIVE)
    build.add_argument("--source-commit", default=os.environ.get("ANALOG_SOURCE_COMMIT"))
    build.add_argument("--notes", default=DEFAULT_NOTES)
    build.add_argument(
        "--no-eda", action="store_true", help="Write only the manifest, not the EDA report."
    )

    verify = sub.add_parser("verify", help="Recompute artifact checksums and semantic counts")
    verify.add_argument("--root", default=str(REPO_ROOT))
    verify.add_argument("--manifest", default=DEFAULT_MANIFEST_RELATIVE)

    report = sub.add_parser("report", help="Print a human summary of a built manifest")
    report.add_argument("--manifest", default=DEFAULT_MANIFEST_RELATIVE)
    report.add_argument("--json", action="store_true", help="Print the full JSON manifest")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "build":
            root = Path(args.root).resolve()
            manifest = build_release(
                root=root,
                output=Path(args.output),
                source_commit=args.source_commit,
                notes=args.notes,
            )
            if not args.no_eda:
                write_eda(
                    root=root,
                    output=Path(args.eda_output),
                    release=manifest,
                )
            counts = manifest["counts"]
            print(
                f"release_id={manifest['release_id']} artifacts={counts['artifacts']} "
                f"files={counts['files']} candidates={counts['fine_candidates']} "
                f"references={counts['fine_references']} "
                f"typed_observations={counts['typed_observations']} "
                f"coarse_cells={counts['coarse_cells']} "
                f"absent_dimensions={counts['absent_body_dimension_cells']}"
            )
            print(f"manifest={args.output}")
            if not args.no_eda:
                print(f"eda={args.eda_output}")
            return 0
        if args.command == "verify":
            report = verify_release(Path(args.manifest), root=Path(args.root))
            print(
                f"OK release_id={report['release_id']} "
                f"artifacts={report['artifacts_verified']} files={report['files_verified']} "
                f"absent_dimensions={report['absent_dimensions']}"
            )
            return 0
        manifest = _read_json(Path(args.manifest))
        if args.json:
            print(json.dumps(manifest, indent=2, sort_keys=True))
        else:
            print(_human_report(manifest))
        return 0
    except (ReleaseError, SnapshotError, IndexError_, duckdb.Error, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
