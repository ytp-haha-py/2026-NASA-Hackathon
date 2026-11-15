#!/usr/bin/env python
"""Bounded 113-candidate Earth NASA POWER release for D03/D04/D05 (Issue #170).

The release answers a narrow question: can every Earth terrain candidate in
``pipelines/earth/manifests/copernicus_glo90_earth.json`` get coarse
reanalysis context for thermal (D03), water/ice (D04) and illumination (D05)?

Stages:

* ``plan``   derives 113 Earth areas from the Copernicus GLO-90 candidate
  manifest and writes a 339-task acquisition plan (113 points x 3 dimensions).
* ``fetch``  executes that plan through the shared NASA POWER provider with
  bounded concurrency (2-4 workers), retries, checkpoints and an external
  cache, then copies the raw responses into ``raw/`` and writes
  ``fetch-index.json``.
* ``rebuild`` is offline and deterministic: it re-processes the committed raw
  responses with the same provider code and rewrites every derived file.
* ``check``  verifies checksums, counts and scientific invariants offline.

The derived files are coarse reanalysis *context*. They are never a local
measurement, never a scored similarity feature, and never zero-filled: a
parameter with only POWER fill values becomes ``state=no_coverage`` with a
null value, and a failed request is recorded in ``failures.json``.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import sys
import threading
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from analog_pipelines.acquisition.executor import run_plan
from analog_pipelines.acquisition.models import (
    AcquisitionPlan,
    AcquisitionTask,
    LocalAsset,
    PlanArea,
    TaskCheckpoint,
    file_sha256,
    write_json,
)
from analog_pipelines.acquisition.planner import build_plan
from analog_pipelines.acquisition.providers import (
    NASA_POWER_METHOD_VERSION,
    NASA_POWER_PARAMETERS,
    NASA_POWER_THERMAL_METHOD_VERSION,
    NASA_POWER_THERMAL_PARAMETERS,
    NASA_POWER_WATER_METHOD_VERSION,
    NASA_POWER_WATER_PARAMETERS,
    NasaPowerProvider,
)
from analog_pipelines.typed_contracts import TypedObservation

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
EARTH_MANIFEST = ROOT / "pipelines/earth/manifests/copernicus_glo90_earth.json"
PLAN_PATH = HERE / "plan.json"
FETCH_INDEX_PATH = HERE / "fetch-index.json"
RAW_DIR = HERE / "raw"

RELEASE_VERSION = "earth-power-113-1.0.0"
SCOPE = "earth-power-113"
START = "2024-01-01"
END = "2024-01-07"
DIMENSIONS = ("D03", "D04", "D05")
BODY = "Earth"
PROVIDER_ID = "nasa-power"

# Bounded, polite access: the cache is outside the repository and is only
# written once per task. 339 requests at >=0.5 s spacing is roughly 3 minutes.
DEFAULT_WORKERS = 3
DEFAULT_MIN_INTERVAL_S = 0.5
DEFAULT_RUN_DIR = ROOT / "var/runs/earth-power-113"
DEFAULT_CACHE_DIR = Path(
    os.environ.get(
        "ANALOG_POWER_CACHE",
        str(Path.home() / ".cache/planetary-analog/nasa-power"),
    )
)

FEATURE_ORDER = {
    "D03": sorted(feature for feature, _unit in NASA_POWER_THERMAL_PARAMETERS.values()),
    "D04": sorted(feature for feature, _unit in NASA_POWER_WATER_PARAMETERS.values()),
    "D05": sorted(feature for feature, _unit in NASA_POWER_PARAMETERS.values()),
}
METHOD_VERSIONS = {
    "D03": NASA_POWER_THERMAL_METHOD_VERSION,
    "D04": NASA_POWER_WATER_METHOD_VERSION,
    "D05": NASA_POWER_METHOD_VERSION,
}
EXPECTED_OBSERVATIONS = (
    len(FEATURE_ORDER["D03"]) + len(FEATURE_ORDER["D04"]) + len(FEATURE_ORDER["D05"])
)

# Checksums cover data and generated documentation. Builder code and the
# convenience wrapper are deliberately excluded so a script edit does not
# invalidate the data release.
CHECKSUM_EXCLUDE = {"checksums.sha256", "README.md", "build.py", "rebuild.sh"}

NOTES = [
    "Bounded coverage of every Earth terrain candidate in the Copernicus GLO-90 "
    "manifest; this is not a global or exhaustive survey.",
    "NASA POWER daily values are coarse reanalysis/model context on an approximately "
    "0.5 x 0.625 degree grid, not local 1.98 km measurements.",
    "Every observation is context_only and must never enter Terrain similarity scoring.",
    "Failures are recorded per point and per source; missing values are never zero-filled.",
]


class PoliteNasaPowerProvider(NasaPowerProvider):
    """NASA POWER provider with a shared minimum request interval.

    The executor runs 2-4 worker threads. A process-wide lock keeps request
    starts at least ``min_interval_s`` apart while cache hits remain free.
    """

    def __init__(self, min_interval_s: float = DEFAULT_MIN_INTERVAL_S) -> None:
        self.min_interval_s = max(0.0, float(min_interval_s))
        self._lock = threading.Lock()
        self._next_slot = 0.0

    def _wait_for_slot(self) -> None:
        with self._lock:
            now = time.monotonic()
            slot = max(now, self._next_slot)
            self._next_slot = slot + self.min_interval_s
        delay = slot - time.monotonic()
        if delay > 0:
            time.sleep(delay)

    def acquire(self, task: AcquisitionTask, cache_dir: Path) -> LocalAsset:
        if (cache_dir / f"{task.task_id}.json").is_file():
            return super().acquire(task, cache_dir)
        self._wait_for_slot()
        return super().acquire(task, cache_dir)


def load_candidates() -> list[dict[str, Any]]:
    payload = json.loads(EARTH_MANIFEST.read_text(encoding="utf-8"))
    candidates = payload.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise ValueError(f"no candidates in {EARTH_MANIFEST}")
    seen: set[str] = set()
    for candidate in candidates:
        area_id = str(candidate["location_id"])
        if area_id in seen:
            raise ValueError(f"duplicate candidate location_id: {area_id}")
        seen.add(area_id)
    return candidates


def _bbox_center(bbox: dict[str, float]) -> tuple[float, float]:
    return (
        (float(bbox["south"]) + float(bbox["north"])) / 2,
        (float(bbox["west"]) + float(bbox["east"])) / 2,
    )


def plan_areas() -> list[PlanArea]:
    areas: list[PlanArea] = []
    for candidate in load_candidates():
        areas.append(
            PlanArea(
                area_id=str(candidate["location_id"]),
                display_name=candidate.get("display_name"),
                body=BODY,
                bbox={
                    key: float(candidate["bbox"][key]) for key in ("west", "south", "east", "north")
                },
                crs="EPSG:4326",
                coordinate_frame="earth_wgs84",
                notes=(
                    f"Earth candidate {candidate['location_id']} from "
                    "pipelines/earth/manifests/copernicus_glo90_earth.json; "
                    f"manifest point lat={candidate['lat']}, lon={candidate['lon']}; "
                    f"90 m analysis window {candidate['window_size_m']} m "
                    f"(candidate crs {candidate['crs']}). NASA POWER is sampled at the "
                    "AOI bbox centre."
                ),
            )
        )
    return areas


def build_release_plan() -> AcquisitionPlan:
    plan = build_plan(
        scope=SCOPE,
        areas=plan_areas(),
        dimensions=DIMENSIONS,
        bodies=[BODY],
        provider_ids=[PROVIDER_ID],
        start=START,
        end=END,
    )
    plan.notes = list(NOTES)
    write_json(PLAN_PATH, plan.model_dump(mode="json"))
    return plan


def _load_plan(path: Path = PLAN_PATH) -> AcquisitionPlan:
    return AcquisitionPlan.model_validate_json(path.read_text(encoding="utf-8"))


def fetch(
    *,
    run_dir: Path = DEFAULT_RUN_DIR,
    cache_dir: Path = DEFAULT_CACHE_DIR,
    workers: int = DEFAULT_WORKERS,
    min_interval_s: float = DEFAULT_MIN_INTERVAL_S,
    resume: bool = True,
    retry_failed: bool = False,
) -> dict[str, Any]:
    if not PLAN_PATH.is_file():
        build_release_plan()
    if not 2 <= workers <= 4:
        raise ValueError("workers must be within 2..4 for this bounded release")
    summary = run_plan(
        PLAN_PATH,
        run_dir=run_dir,
        cache_dir=cache_dir,
        workers=workers,
        resume=resume,
        retry_failed=retry_failed,
        providers={PROVIDER_ID: PoliteNasaPowerProvider(min_interval_s)},
    )
    return summary


def _load_checkpoint(run_dir: Path, task_id: str) -> TaskCheckpoint | None:
    path = run_dir / "checkpoints" / f"{task_id}.json"
    if not path.is_file():
        return None
    return TaskCheckpoint.model_validate_json(path.read_text(encoding="utf-8"))


def publish(
    *,
    release_dir: Path = HERE,
    run_dir: Path = DEFAULT_RUN_DIR,
    cache_dir: Path = DEFAULT_CACHE_DIR,
) -> dict[str, Any]:
    """Copy fetched raw responses and build the portable release offline."""
    plan = _load_plan(release_dir / "plan.json")
    raw_dir = release_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    tasks: dict[str, dict[str, Any]] = {}
    succeeded = 0
    failed = 0
    for task in plan.tasks:
        checkpoint = _load_checkpoint(run_dir, task.task_id)
        cached = cache_dir / f"{task.task_id}.json"
        target = raw_dir / f"{task.task_id}.json"
        if checkpoint is not None and checkpoint.status == "succeeded" and cached.is_file():
            digest = file_sha256(cached)
            if checkpoint.asset is None or digest != checkpoint.asset.sha256:
                tasks[task.task_id] = {
                    "status": "failed",
                    "error": "cached response does not match checkpoint checksum",
                }
                failed += 1
                continue
            shutil.copyfile(cached, target)
            tasks[task.task_id] = {
                "status": "succeeded",
                "fetched_at": checkpoint.asset.fetched_at,
                "sha256": digest,
                "bytes": target.stat().st_size,
            }
            succeeded += 1
            continue
        if target.is_file() and checkpoint is not None and checkpoint.status == "succeeded":
            # A resumed run may have a verified raw file but no external cache.
            digest = file_sha256(target)
            if checkpoint.asset is not None and digest == checkpoint.asset.sha256:
                tasks[task.task_id] = {
                    "status": "succeeded",
                    "fetched_at": checkpoint.asset.fetched_at,
                    "sha256": digest,
                    "bytes": target.stat().st_size,
                }
                succeeded += 1
                continue
        tasks[task.task_id] = {
            "status": checkpoint.status if checkpoint else "unavailable",
            "error": (checkpoint.error if checkpoint else None) or "no fetched response",
            "attempts": checkpoint.attempts if checkpoint else 0,
        }
        failed += 1
    completed_at = max(
        (entry["fetched_at"] for entry in tasks.values() if entry.get("fetched_at")),
        default=datetime.now(UTC).isoformat(),
    )
    index = {
        "fetch_index_version": "1.0.0",
        "release_version": RELEASE_VERSION,
        "provider_id": PROVIDER_ID,
        "start": START,
        "end": END,
        "completed_at": completed_at,
        "counts": {
            "planned": len(plan.tasks),
            "succeeded": succeeded,
            "failed": failed,
        },
        "tasks": dict(sorted(tasks.items())),
    }
    write_json(release_dir / "fetch-index.json", index)
    return materialize(release_dir)


def _load_fetch_index(release_dir: Path) -> dict[str, Any]:
    path = release_dir / "fetch-index.json"
    if not path.is_file():
        raise ValueError(f"missing fetch index: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _task_failure(task: AcquisitionTask, entry: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "task_id": task.task_id,
        "area_id": task.area.area_id,
        "dimension": task.dimension,
        "provider_id": task.provider_id,
        "source_id": task.source_id,
        "endpoint": task.endpoint,
        "status": str((entry or {}).get("status", "not_fetched")),
        "error": (entry or {}).get("error"),
    }


def _process_task(
    provider: NasaPowerProvider,
    task: AcquisitionTask,
    raw_path: Path,
) -> list[TypedObservation]:
    asset = LocalAsset(
        task_id=task.task_id,
        path=str(raw_path),
        sha256=file_sha256(raw_path),
        bytes=raw_path.stat().st_size,
        content_type="application/json",
    )
    return provider.process(asset, task)


def _observation_counts(observations: list[TypedObservation]) -> dict[str, Any]:
    return {
        "total": len(observations),
        "by_dimension": dict(Counter(item.dimension_code for item in observations)),
        "by_body": dict(Counter(item.body for item in observations)),
        "by_state": dict(Counter(item.state for item in observations)),
        "by_feature": dict(Counter(item.feature_id for item in observations)),
        "areas": sorted({item.area_id for item in observations}),
    }


def _power_cell(lat: float, lon: float) -> str:
    # Approximate 0.5 x 0.625 degree POWER grid cell label; reporting only.
    return f"{math.floor(lat / 0.5):+d}:{math.floor(lon / 0.625):+d}"


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6_371_000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = phi2 - phi1
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * radius * math.asin(min(1.0, math.sqrt(a)))


def materialize(release_dir: Path = HERE) -> dict[str, Any]:
    """Rebuild every derived release file from committed plan and raw responses."""
    release_dir = Path(release_dir)
    plan = _load_plan(release_dir / "plan.json")
    index = _load_fetch_index(release_dir)
    source_manifest = json.loads(EARTH_MANIFEST.read_text(encoding="utf-8"))
    source_candidates = {
        str(candidate["location_id"]): candidate for candidate in source_manifest["candidates"]
    }

    provider = NasaPowerProvider()
    observations: list[TypedObservation] = []
    failures: list[dict[str, Any]] = []
    tasks_by_id = {task.task_id: task for task in plan.tasks}
    for task in plan.tasks:
        entry = index.get("tasks", {}).get(task.task_id)
        raw_path = release_dir / "raw" / f"{task.task_id}.json"
        if not entry or entry.get("status") != "succeeded":
            failures.append(_task_failure(task, entry))
            continue
        if not raw_path.is_file():
            failure = _task_failure(task, entry)
            failure["status"] = "missing_raw"
            failure["error"] = "fetch index says succeeded but raw response is absent"
            failures.append(failure)
            continue
        digest = file_sha256(raw_path)
        if digest != entry.get("sha256"):
            failure = _task_failure(task, entry)
            failure["status"] = "checksum_mismatch"
            failure["error"] = "raw response checksum does not match fetch index"
            failures.append(failure)
            continue
        try:
            observations.extend(_process_task(provider, task, raw_path))
        except Exception as exc:  # noqa: BLE001 - per-task failure must be recorded
            failure = _task_failure(task, entry)
            failure["status"] = "process_failed"
            failure["error"] = f"{type(exc).__name__}: {exc}"
            failures.append(failure)

    observations.sort(key=lambda item: (item.area_id, item.dimension_code, item.feature_id))
    observations_path = release_dir / "observations.jsonl"
    observations_path.write_text(
        "".join(
            json.dumps(observation.model_dump(mode="json"), sort_keys=True) + "\n"
            for observation in observations
        ),
        encoding="utf-8",
    )

    state_by_key = {
        (item.area_id, item.dimension_code, item.feature_id): item.state for item in observations
    }
    failed_areas = {failure["area_id"] for failure in failures}
    failed_tasks = {failure["task_id"] for failure in failures}

    assets: list[dict[str, Any]] = []
    raw_entries: list[dict[str, Any]] = []
    for task_id in sorted(tasks_by_id):
        task = tasks_by_id[task_id]
        entry = index.get("tasks", {}).get(task_id) or {}
        raw_path = release_dir / "raw" / f"{task_id}.json"
        if task_id in failed_tasks or entry.get("status") != "succeeded" or not raw_path.is_file():
            continue
        digest = file_sha256(raw_path)
        assets.append(
            {
                "asset_id": f"{task.provider_id}:{task.area.area_id}:{task.task_id}",
                "dataset_id": task.dataset_id or task.source_id,
                "uri": f"raw/{raw_path.name}",
                "checksum_algorithm": "sha256",
                "checksum": digest,
                "checksum_scope": "source",
                "bytes": raw_path.stat().st_size,
                "fetched_at": entry.get("fetched_at"),
                "redistribution_allowed": "unknown",
                "source_endpoint": task.endpoint,
            }
        )
        raw_entries.append(
            {
                "task_id": task.task_id,
                "path": f"raw/{raw_path.name}",
                "sha256": digest,
                "bytes": raw_path.stat().st_size,
            }
        )
    write_json(release_dir / "assets.json", assets)

    candidates: list[dict[str, Any]] = []
    coverage_by_dimension: dict[str, Counter[str]] = {
        dimension: Counter() for dimension in DIMENSIONS
    }
    area_tasks = {task.area.area_id: task for task in plan.tasks}
    not_fetched_total = 0
    max_offset = 0.0
    cells: set[str] = set()
    for area_id in sorted(area_tasks):
        task = area_tasks[area_id]
        candidate = source_candidates.get(area_id)
        if candidate is None:
            continue
        request_lat, request_lon = _bbox_center(task.area.bbox)
        max_offset = max(
            max_offset,
            _haversine_m(
                float(candidate["lat"]),
                float(candidate["lon"]),
                request_lat,
                request_lon,
            ),
        )
        cell = _power_cell(float(candidate["lat"]), float(candidate["lon"]))
        cells.add(cell)
        dimension_states: dict[str, dict[str, str]] = {}
        for dimension in DIMENSIONS:
            feature_states: dict[str, str] = {}
            for feature in FEATURE_ORDER[dimension]:
                state = state_by_key.get((area_id, dimension, feature))
                if state is None:
                    state = "not_fetched" if area_id in failed_areas else "no_coverage"
                feature_states[feature] = state
                coverage_by_dimension[dimension][state] += 1
                if state == "not_fetched":
                    not_fetched_total += 1
            dimension_states[dimension] = feature_states
        all_states = [
            state
            for feature_states in dimension_states.values()
            for state in feature_states.values()
        ]
        candidates.append(
            {
                "area_id": area_id,
                "manifest_point": {
                    "lat": float(candidate["lat"]),
                    "lon": float(candidate["lon"]),
                },
                "request_point": {"lat": request_lat, "lon": request_lon},
                "power_cell_approx": cell,
                "dimensions": dimension_states,
                "observation_states": {
                    "detected": all_states.count("detected"),
                    "no_coverage": all_states.count("no_coverage"),
                    "not_fetched": all_states.count("not_fetched"),
                },
            }
        )

    detected_total = sum(1 for item in observations if item.state == "detected")
    no_coverage_total = sum(1 for item in observations if item.state == "no_coverage")
    coverage = {
        "candidate_count": len(candidates),
        "expected_observations": len(candidates) * EXPECTED_OBSERVATIONS,
        "observation_count": len(observations),
        "detected": detected_total,
        "no_coverage": no_coverage_total,
        "not_fetched": not_fetched_total,
        "feature_count_per_candidate": EXPECTED_OBSERVATIONS,
        "distinct_power_cells_approx": len(cells),
        "power_grid": "approximately 0.5 degree latitude x 0.625 degree longitude (POWER)",
        "request_point_max_offset_m": round(max_offset, 3),
        "by_dimension": {
            dimension: dict(sorted(coverage_by_dimension[dimension].items()))
            for dimension in DIMENSIONS
        },
        "by_state": dict(sorted(Counter(item.state for item in observations).items())),
        "candidates": candidates,
    }
    write_json(release_dir / "coverage.json", coverage)

    failure_payload = {
        "release_version": RELEASE_VERSION,
        "count": len(failures),
        "failures": failures,
        "note": (
            "A failure means no usable POWER response was processed for that point and "
            "dimension. It is not zero coverage and it is never filled with zeros."
        ),
    }
    write_json(release_dir / "failures.json", failure_payload)

    counts = _observation_counts(observations)
    source_manifest_sha = file_sha256(EARTH_MANIFEST)
    report_lines = [
        "# Earth POWER 113 — D03/D04/D05 candidate coverage",
        "",
        f"- Release version: `{RELEASE_VERSION}`",
        f"- Source manifest: `pipelines/earth/manifests/copernicus_glo90_earth.json` "
        f"(`sha256:{source_manifest_sha}`)",
        f"- Earth candidates: {len(candidates)}",
        f"- Planned tasks: {len(plan.tasks)} (113 candidates x D03/D04/D05)",
        f"- Succeeded tasks: {index['counts']['succeeded']}",
        f"- Failed tasks: {index['counts']['failed']}",
        f"- Observations: {len(observations)} "
        f"(detected {detected_total}, no_coverage {no_coverage_total})",
        f"- Assets: {len(assets)}",
        f"- Feature slots per candidate: {EXPECTED_OBSERVATIONS}",
        f"- Distinct POWER cells (approx.): {len(cells)}",
        f"- Interval: {START} to {END} (daily)",
        "",
        "## Coverage by dimension",
        "",
        "| Dimension | Features | Detected | No coverage | Not fetched |",
        "|---|---:|---:|---:|---:|",
    ]
    for dimension in DIMENSIONS:
        states = coverage_by_dimension[dimension]
        report_lines.append(
            f"| {dimension} | {len(FEATURE_ORDER[dimension])} | {states['detected']} | "
            f"{states['no_coverage']} | {states['not_fetched']} |"
        )
    report_lines.extend(
        [
            "",
            "## Failures",
            "",
            (
                "None. Every planned point/dimension produced a processed response."
                if not failures
                else "\n".join(
                    f"- `{failure['task_id']}` ({failure['status']}): {failure['error']}"
                    for failure in failures
                )
            ),
            "",
            "## Scientific limits",
            "",
            "- NASA POWER is a coarse reanalysis/model product (~0.5 x 0.625 degree), not a "
            "local 1.98 km measurement of temperature, water/ice or irradiance.",
            "- The 113 candidates are the existing Earth terrain manifest, not a global "
            "or exhaustive survey. Several neighbouring 1.98 km windows fall in the same "
            "POWER cell, so candidate count is not independent spatial coverage.",
            "- Every record is `context_only`. It must never enter Terrain similarity, "
            "a cross-body score, or a mineral/ice detection.",
            "- Missing POWER values are counted and marked `no_coverage`; they are never "
            "replaced with zero.",
            "",
            "## Reproduce",
            "",
            "```bash",
            "uv run python pipelines/d02_d04_aoi/earth_power_113/build.py plan",
            "uv run python pipelines/d02_d04_aoi/earth_power_113/build.py fetch --workers 3",
            "uv run python pipelines/d02_d04_aoi/earth_power_113/build.py check",
            "bash pipelines/d02_d04_aoi/earth_power_113/rebuild.sh",
            "```",
            "",
            "The fetch cache defaults to `~/.cache/planetary-analog/nasa-power` "
            "(override with `ANALOG_POWER_CACHE`) and is outside the repository.",
            "",
        ]
    )
    (release_dir / "REPORT.md").write_text("\n".join(report_lines), encoding="utf-8")

    files = {}
    for name in (
        "plan.json",
        "assets.json",
        "observations.jsonl",
        "coverage.json",
        "failures.json",
        "fetch-index.json",
        "REPORT.md",
    ):
        path = release_dir / name
        files[name] = {"sha256": file_sha256(path), "bytes": path.stat().st_size}

    manifest = {
        "release_version": RELEASE_VERSION,
        "scope": SCOPE,
        "generated_at": index.get("completed_at"),
        "source_manifest": {
            "path": "pipelines/earth/manifests/copernicus_glo90_earth.json",
            "sha256": source_manifest_sha,
            "candidate_count": len(source_candidates),
        },
        "plan_sha256": file_sha256(release_dir / "plan.json"),
        "observations_sha256": file_sha256(observations_path),
        "observation_count": len(observations),
        "expected_observation_count": len(candidates) * EXPECTED_OBSERVATIONS,
        "asset_count": len(assets),
        "task_count": len(plan.tasks),
        "requests": {
            "planned": index["counts"]["planned"],
            "succeeded": index["counts"]["succeeded"],
            "failed": index["counts"]["failed"],
        },
        "coverage": {
            "candidate_count": len(candidates),
            "feature_count_per_candidate": EXPECTED_OBSERVATIONS,
            "detected": detected_total,
            "no_coverage": no_coverage_total,
            "not_fetched": not_fetched_total,
            "by_dimension": coverage["by_dimension"],
            "distinct_power_cells_approx": len(cells),
            "request_point_max_offset_m": coverage["request_point_max_offset_m"],
        },
        "report": {
            "by_body": counts["by_body"],
            "by_dimension": counts["by_dimension"],
            "by_state": counts["by_state"],
            "by_feature": counts["by_feature"],
            "areas": counts["areas"],
            "total": counts["total"],
        },
        "tasks": [
            {
                "task_id": task.task_id,
                "source_id": task.source_id,
                "provider_id": task.provider_id,
                "body": task.body,
                "dimension": task.dimension,
                "area_id": task.area.area_id,
                "method_version": task.method_version,
                "dataset_id": task.dataset_id,
                "endpoint": task.endpoint,
            }
            for task in plan.tasks
        ],
        "raw": raw_entries,
        "files": files,
        "notes": NOTES,
    }
    write_json(release_dir / "manifest.json", manifest)
    _write_checksums(release_dir)
    return manifest


def _write_checksums(release_dir: Path) -> Path:
    target = release_dir / "checksums.sha256"
    lines = []
    for path in sorted(release_dir.rglob("*")):
        if not path.is_file() or path.name in CHECKSUM_EXCLUDE:
            continue
        if "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        lines.append(f"{file_sha256(path)}  {path.relative_to(release_dir).as_posix()}")
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


def _verified_checksums(release_dir: Path) -> dict[str, str]:
    target = release_dir / "checksums.sha256"
    verified: dict[str, str] = {}
    for line in target.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        digest, name = line.split("  ", 1)
        path = release_dir / name
        if not path.is_file():
            raise ValueError(f"checksums.sha256 lists a missing file: {name}")
        actual = file_sha256(path)
        if actual != digest:
            raise ValueError(f"checksum mismatch: {name}")
        verified[name] = digest
    return verified


def _expected_source_features(dimension: str) -> list[str]:
    if dimension == "D03":
        mapping = NASA_POWER_THERMAL_PARAMETERS
    elif dimension == "D04":
        mapping = NASA_POWER_WATER_PARAMETERS
    else:
        mapping = NASA_POWER_PARAMETERS
    return sorted(feature for feature, _unit in mapping.values())


def check(release_dir: Path = HERE) -> dict[str, Any]:
    release_dir = Path(release_dir)
    plan = _load_plan(release_dir / "plan.json")
    manifest = json.loads((release_dir / "manifest.json").read_text(encoding="utf-8"))
    index = _load_fetch_index(release_dir)
    observations = [
        json.loads(line)
        for line in (release_dir / "observations.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assets = json.loads((release_dir / "assets.json").read_text(encoding="utf-8"))
    failures = json.loads((release_dir / "failures.json").read_text(encoding="utf-8"))
    coverage = json.loads((release_dir / "coverage.json").read_text(encoding="utf-8"))
    source_manifest = json.loads(EARTH_MANIFEST.read_text(encoding="utf-8"))
    source_ids = {str(candidate["location_id"]) for candidate in source_manifest["candidates"]}

    problems: list[str] = []
    verified = _verified_checksums(release_dir)
    on_disk = {
        path.relative_to(release_dir).as_posix()
        for path in release_dir.rglob("*")
        if path.is_file()
        and path.name not in CHECKSUM_EXCLUDE
        and "__pycache__" not in path.parts
        and path.suffix != ".pyc"
    }
    if set(verified) != on_disk:
        problems.append(
            "checksums.sha256 does not cover exactly the release files; "
            f"unlisted={sorted(on_disk - set(verified))[:5]} missing={sorted(set(verified) - on_disk)[:5]}"
        )

    if file_sha256(release_dir / "observations.jsonl") != manifest["observations_sha256"]:
        problems.append("observations checksum does not match manifest")
    if len(observations) != manifest["observation_count"]:
        problems.append("observation count does not match manifest")
    if len(assets) != manifest["asset_count"]:
        problems.append("asset count does not match manifest")
    if len(plan.tasks) != manifest["task_count"]:
        problems.append("task count does not match manifest")

    plan_area_ids = {task.area.area_id for task in plan.tasks}
    if plan_area_ids != source_ids:
        problems.append("plan areas do not exactly match Earth manifest candidates")
    for task in plan.tasks:
        if task.method_version != METHOD_VERSIONS[task.dimension]:
            problems.append(f"unexpected planned method_version: {task.task_id}")
    if len(source_ids) != manifest["coverage"]["candidate_count"]:
        problems.append("candidate count does not match Earth manifest")
    if len(plan.tasks) != len(source_ids) * len(DIMENSIONS):
        problems.append("plan does not contain three tasks per Earth candidate")

    asset_by_id = {asset["asset_id"]: asset for asset in assets}
    raw_by_task = {entry["task_id"]: entry for entry in manifest["raw"]}
    for entry in manifest["raw"]:
        path = release_dir / entry["path"]
        if not path.is_file() or file_sha256(path) != entry["sha256"]:
            problems.append(f"raw checksum mismatch: {entry['task_id']}")
    for asset in assets:
        if asset["uri"] not in verified:
            problems.append(f"asset uri is not checksummed: {asset['uri']}")
        if asset["checksum"] != raw_by_task.get(asset["asset_id"].rsplit(":", 1)[-1], {}).get(
            "sha256"
        ):
            problems.append(f"asset checksum differs from manifest raw: {asset['asset_id']}")

    seen_keys: set[tuple[str, str, str]] = set()
    for item in observations:
        key = (item["area_id"], item["dimension_code"], item["feature_id"])
        if key in seen_keys:
            problems.append(f"duplicate observation key: {key}")
        seen_keys.add(key)
        if item["body"] != BODY:
            problems.append(f"non-Earth observation: {item['observation_id']}")
        if item["dimension_code"] not in DIMENSIONS:
            problems.append(f"unexpected dimension: {item['observation_id']}")
        if item["score_role"] != "context_only":
            problems.append(f"non-context observation: {item['observation_id']}")
        if item["feature_id"] not in _expected_source_features(item["dimension_code"]):
            problems.append(f"feature not declared for dimension: {item['observation_id']}")
        if item["method_version"] != NASA_POWER_METHOD_VERSION:
            problems.append(f"unexpected observation method_version: {item['observation_id']}")
        support = item["spatial_support"]
        if (support.get("source_resolution_m") or 0) < 40_000:
            problems.append(f"resolution is not coarse POWER context: {item['observation_id']}")
        temporal = item["temporal_support"]
        if item["state"] == "detected" and (
            temporal.get("start") != START or temporal.get("end") != END
        ):
            problems.append(f"unexpected time period: {item['observation_id']}")
        if not item["quality_flags"]:
            problems.append(f"missing quality flags: {item['observation_id']}")
        if not item["dataset_ids"]:
            problems.append(f"missing dataset provenance: {item['observation_id']}")
        if not item["source_asset_ids"]:
            problems.append(f"missing source asset provenance: {item['observation_id']}")
        for asset_id in item["source_asset_ids"]:
            if asset_id not in asset_by_id:
                problems.append(f"unresolved source asset: {asset_id}")
        value = item["value"]
        if item["state"] == "detected":
            if not isinstance(value, dict):
                problems.append(f"detected observation has no timeseries: {item['observation_id']}")
                continue
            values = value.get("values") or []
            if not values:
                problems.append(f"detected observation has no values: {item['observation_id']}")
            if len(values) + int(value.get("missing_count", 0)) != 7:
                problems.append(f"unexpected daily count: {item['observation_id']}")
            for number in values:
                if not isinstance(number, (int, float)) or not math.isfinite(number):
                    problems.append(f"non-finite value: {item['observation_id']}")
                elif number <= -900:
                    problems.append(
                        f"POWER fill sentinel was not filtered: {item['observation_id']}"
                    )
        elif item["state"] == "no_coverage":
            if value is not None:
                problems.append(
                    f"no_coverage observation carries a value: {item['observation_id']}"
                )
        else:
            problems.append(f"unexpected observation state: {item['observation_id']}")

    if failures.get("count") != len(failures.get("failures", [])):
        problems.append("failure count does not match failure list")
    if failures.get("count", 0) != manifest["requests"]["failed"]:
        problems.append("failure count does not match manifest requests")
    if coverage.get("candidate_count") != len(source_ids):
        problems.append("coverage candidate count does not match Earth manifest")
    if coverage.get("observation_count") != len(observations):
        problems.append("coverage observation count does not match observations.jsonl")
    missing_slots = 0
    for candidate in coverage["candidates"]:
        for states in candidate["dimensions"].values():
            missing_slots += sum(1 for state in states.values() if state == "not_fetched")
    if missing_slots != manifest["coverage"]["not_fetched"]:
        problems.append("coverage not_fetched count does not match manifest")
    if index.get("counts", {}).get("planned") != len(plan.tasks):
        problems.append("fetch index planned count does not match plan")

    return {
        "ok": not problems,
        "candidates": len(source_ids),
        "tasks": len(plan.tasks),
        "observations": len(observations),
        "assets": len(assets),
        "failures": failures.get("count", 0),
        "verified_files": len(verified),
        "problems": problems,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("plan", help="Write the 113-candidate, 339-task plan.json")

    fetch_cmd = sub.add_parser("fetch", help="Fetch POWER responses and publish the release")
    fetch_cmd.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    fetch_cmd.add_argument("--min-interval", type=float, default=DEFAULT_MIN_INTERVAL_S)
    fetch_cmd.add_argument("--run-dir", default=str(DEFAULT_RUN_DIR))
    fetch_cmd.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR))
    fetch_cmd.add_argument("--rerun-failed", action="store_true")

    rebuild = sub.add_parser("rebuild", help="Offline deterministic rebuild from committed raw/")
    rebuild.add_argument("--release-dir", default=str(HERE))

    check_cmd = sub.add_parser("check", help="Verify checksums, counts and invariants offline")
    check_cmd.add_argument("--release-dir", default=str(HERE))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "plan":
        plan = build_release_plan()
        print(json.dumps(plan.totals, indent=2, sort_keys=True))
        return 0
    if args.command == "fetch":
        summary = fetch(
            run_dir=Path(args.run_dir),
            cache_dir=Path(args.cache_dir),
            workers=args.workers,
            min_interval_s=args.min_interval,
            retry_failed=args.rerun_failed,
        )
        manifest = publish(
            release_dir=HERE,
            run_dir=Path(args.run_dir),
            cache_dir=Path(args.cache_dir),
        )
        print(json.dumps({"summary": summary, "manifest": manifest["coverage"]}, indent=2))
        return 0 if summary["validated"] else 1
    if args.command == "rebuild":
        manifest = materialize(Path(args.release_dir))
        print(
            json.dumps(
                {
                    "observations": manifest["observation_count"],
                    "assets": manifest["asset_count"],
                    "failures": manifest["requests"]["failed"],
                },
                indent=2,
            )
        )
        return 0
    if args.command == "check":
        result = check(Path(args.release_dir))
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result["ok"] else 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
