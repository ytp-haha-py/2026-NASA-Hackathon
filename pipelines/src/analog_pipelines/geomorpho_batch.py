"""Bounded, resumable batch execution for the three-region Geomorpho250 pilot (Issue #32).

Each task covers one deterministic partition of 5 km grid cells inside one pilot region.
The task reads a locally staged 7.5 arc-second slope window (staged upstream by a bounded
`/vsicurl` range read), writes one Parquet partition plus a JSON sidecar atomically, and
is only checkpointed after its artifact checksum was verified. The global 5 GB PANGAEA
raster is never downloaded and the run needs no network access.

Real vs synthetic: the default ``--source-mode real`` produces real Geomorpho250-derived
rows labelled ``real_geomorpho250``. ``--source-mode synthetic-fixture`` is a test-only
mode that labels every row as a synthetic fixture; synthetic rows are never observations
and must never be published. This CLI covers three 0.4 degree pilot regions, never global
coverage.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from analog_pipelines.batch_runner import (
    CHECKPOINT_VERSION,
    TERMINAL_STATUSES,
    BatchError,
    BatchRunner,
    CheckpointStore,
    PartitionTask,
    Worker,
    WorkerResult,
)
from analog_pipelines.coarse_features import (
    COARSE_METHOD_VERSION,
    CoarseExtractionError,
    extract_coarse_features,
)
from analog_pipelines.coarse_grid import (
    GRID_VERSION,
    CandidateCell,
    GridRegion,
    generate_region_grid,
)
from analog_pipelines.coarse_index import (
    INDEX_VERSION,
    PARQUET_SCHEMA_VERSION,
    CoarseRow,
    write_coarse_parquet,
)
from analog_pipelines.geomorpho import RasterReadError, read_projected_window

BATCH_SCHEMA_VERSION = "geomorpho-batch-1.0.0"
SOURCE_URL = (
    "https://hs.pangaea.de/Maps/DEM_Geomorpho90m/dtm_slope_merit.dem_m_250m_s0..0cm_2018_v1.0.tif"
)
SOURCE_LICENSE = "CC BY 4.0"
SOURCE_CITATION = "Amatulli et al. (2019/2020), Geomorpho90m, PANGAEA 899135"
DATASET_ID = "geomorpho250-slope-v1.0"
PILOT_REGIONS: tuple[GridRegion, ...] = (
    GridRegion("pilot-sahara", "Sahara pilot", 10.0, 24.0, 10.4, 24.4),
    GridRegion("pilot-alps", "Alps pilot", 10.0, 45.0, 10.4, 45.4),
    GridRegion("pilot-grand-canyon", "Grand Canyon pilot", -112.2, 35.8, -111.8, 36.2),
)
# Checksums of the bounded windows published with the committed real pilot index.
# They are audit references, not a hard requirement: a re-staged window is recorded
# with its actual checksum and reported as not matching the published record.
PILOT_WINDOW_SHA256 = {
    "pilot-alps": "e1349d175135549fee6336c0aaa4d036fdaeb5d209b5a44c5865d73c17ca78fd",
    "pilot-grand-canyon": "e0c301f2dcd4891c5f55533d526857f94b844bb8c80ab6a991e45bab34556885",
    "pilot-sahara": "3c8a3f993c43ddf79929b86db7c589abc781baa030b782db894926c8acf234c9",
}
REAL_QA_FLAGS = ("real_geomorpho250", "bounded_remote_window", "cc_by_4_0", "batch_partition")
SYNTHETIC_QA_FLAGS = ("synthetic_fixture", "batch_partition", "not_an_observation")
COVERAGE_LABEL = "three_region_pilot_not_global_exhaustive"
SYNTHETIC_COVERAGE_LABEL = "synthetic_fixture_not_observation"
DEFAULT_CACHE_DIR = "var/cache/geomorpho-pilot"
DEFAULT_OUT_DIR = "var/geomorpho-batch"
DEFAULT_CELLS_PER_TASK = 16
MAX_CELLS_PER_TASK = 64
MAX_WORKERS = 4
MAX_TASKS = 2048
RESOLUTION_M = 250.0
MAX_PIXELS_PER_CELL = 4096
# Analytic lower bound for one in-flight reprojection window per worker; GDAL
# overhead, block caches and interpreter memory are not included.
ESTIMATE_BYTES_PER_CELL = MAX_PIXELS_PER_CELL * 8 * 6
DEFAULT_MEMORY_BUDGET_MB = 512
_NO_COVERAGE_READ_MARKERS = (
    "outside the raster bounds",
    "does not intersect the raster",
)
_LIMITATIONS = [
    "Three 0.4 degree pilot regions, not global coverage.",
    "7.5 arc-second source is ~250 m only near the equator; there is no fixed metric source grid.",
    "Bilinear reprojection and 5 km aggregation are method-specific, not 90 m terrain metrics.",
    "Global coverage and same-support Moon/Mars coarse references remain unavailable.",
]


class CliError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class PlannedTask:
    task: PartitionTask
    region: GridRegion
    partition_index: int
    cells: tuple[CandidateCell, ...]
    input_path: Path
    input_checksum: str | None
    input_bytes: int | None

    @property
    def task_id(self) -> str:
        return self.task.task_id

    def to_payload(self) -> dict[str, object]:
        return {
            "task_id": self.task_id,
            "fingerprint": self.task.fingerprint,
            "region_id": self.region.region_id,
            "partition_index": self.partition_index,
            "cells": [cell.candidate_id for cell in self.cells],
            "window_count": self.task.window_count,
            "input": {
                "path": str(self.input_path),
                "sha256": self.input_checksum,
                "bytes": self.input_bytes,
                "present": self.input_checksum is not None,
                "matches_published_window": (
                    self.input_checksum == PILOT_WINDOW_SHA256.get(self.region.region_id)
                ),
            },
        }


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write_json(path: str | Path, payload: Mapping[str, Any]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=destination.name, dir=destination.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, destination)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def _partition_digest(region_id: str, cells: Sequence[CandidateCell]) -> str:
    identity = "|".join(
        [
            BATCH_SCHEMA_VERSION,
            GRID_VERSION,
            COARSE_METHOD_VERSION,
            region_id,
            str(len(cells)),
            cells[0].candidate_id,
            cells[-1].candidate_id,
        ]
    )
    return hashlib.sha256(identity.encode()).hexdigest()[:16]


def select_regions(requested: Sequence[str] | None) -> list[GridRegion]:
    known = {region.region_id: region for region in PILOT_REGIONS}
    if not requested:
        return [known[key] for key in sorted(known)]
    wanted: list[str] = []
    for item in requested:
        for part in str(item).split(","):
            part = part.strip()
            if part and part not in wanted:
                wanted.append(part)
    unknown = [region_id for region_id in wanted if region_id not in known]
    if unknown:
        raise CliError(f"unknown pilot region(s) {unknown}; available: {sorted(known)}")
    if not wanted:
        raise CliError("--region did not select any region")
    return [known[region_id] for region_id in sorted(wanted)]


def plan_tasks(
    regions: Sequence[GridRegion],
    *,
    cache_dir: str | Path,
    cells_per_task: int = DEFAULT_CELLS_PER_TASK,
) -> list[PlannedTask]:
    """Build the deterministic task plan; ordering never depends on --limit."""
    if cells_per_task < 1 or cells_per_task > MAX_CELLS_PER_TASK:
        raise CliError(f"cells_per_task must be within 1..{MAX_CELLS_PER_TASK}")
    cache = Path(cache_dir)
    planned: list[PlannedTask] = []
    for region in sorted(regions, key=lambda item: item.region_id):
        cells, _coverage = generate_region_grid(region)
        ordered = sorted(cells, key=lambda cell: (cell.row, cell.col))
        input_path = cache / f"{region.region_id}.tif"
        checksum: str | None = None
        input_bytes: int | None = None
        if input_path.is_file():
            checksum = sha256_file(input_path)
            input_bytes = input_path.stat().st_size
        for index, start in enumerate(range(0, len(ordered), cells_per_task)):
            chunk = tuple(ordered[start : start + cells_per_task])
            task_id = f"geomorpho-batch-v1-{_partition_digest(region.region_id, chunk)}"
            planned.append(
                PlannedTask(
                    task=PartitionTask(
                        task_id=task_id,
                        region_id=region.region_id,
                        window_count=len(chunk),
                        input_checksum=checksum,
                    ),
                    region=region,
                    partition_index=index,
                    cells=chunk,
                    input_path=input_path,
                    input_checksum=checksum,
                    input_bytes=input_bytes,
                )
            )
    return planned


def _task_rows(
    planned: PlannedTask,
    *,
    source_mode: str,
) -> tuple[list[CoarseRow], list[str], list[str], list[dict[str, str]]]:
    rows: list[CoarseRow] = []
    no_coverage: list[str] = []
    invalid: list[str] = []
    reasons: list[dict[str, str]] = []
    checksum = planned.input_checksum or ""
    synthetic = source_mode == "synthetic-fixture"
    for cell in planned.cells:
        try:
            window = read_projected_window(
                planned.input_path,
                bbox_metric=cell.bbox_metric,
                target_crs=cell.crs,
                resolution_m=RESOLUTION_M,
                max_pixels=MAX_PIXELS_PER_CELL,
            )
            features = extract_coarse_features(window.data, window.valid_mask)
        except CoarseExtractionError as exc:
            target = no_coverage if exc.code == "insufficient_valid_data" else invalid
            target.append(cell.candidate_id)
            reasons.append(
                {"candidate_id": cell.candidate_id, "code": exc.code, "message": exc.message}
            )
            continue
        except RasterReadError as exc:
            message = str(exc)
            if any(marker in message for marker in _NO_COVERAGE_READ_MARKERS):
                no_coverage.append(cell.candidate_id)
                reasons.append(
                    {
                        "candidate_id": cell.candidate_id,
                        "code": "outside_source_coverage",
                        "message": message,
                    }
                )
            else:
                invalid.append(cell.candidate_id)
                reasons.append(
                    {
                        "candidate_id": cell.candidate_id,
                        "code": "raster_read_error",
                        "message": message,
                    }
                )
            continue
        rows.append(
            CoarseRow(
                candidate_id=cell.candidate_id,
                grid_id=cell.grid_version,
                region_id=cell.region_id,
                body="Earth",
                west=cell.bbox_wgs84["west"],
                south=cell.bbox_wgs84["south"],
                east=cell.bbox_wgs84["east"],
                north=cell.bbox_wgs84["north"],
                crs=cell.crs,
                area_km2=25.0,
                slope_mean_deg=features["coarse_slope_mean_deg"],
                slope_p90_deg=features["coarse_slope_p90_deg"],
                flat_fraction=features["coarse_flat_fraction_lt5deg"],
                valid_fraction=float(window.valid_mask.mean()),
                method_version=COARSE_METHOD_VERSION,
                grid_version=GRID_VERSION,
                dataset_id="synthetic-fixture" if synthetic else DATASET_ID,
                source_asset_id=(
                    f"synthetic-fixture-{checksum[:16]}"
                    if synthetic
                    else f"pangaea-slope-{checksum[:16]}"
                ),
                qa_flags=list(SYNTHETIC_QA_FLAGS if synthetic else REAL_QA_FLAGS),
                window_size_m=float(cell.window_size_m),
                stride_m=float(cell.stride_m),
                source_resolution_m=float(cell.source_resolution_m),
            )
        )
    return rows, no_coverage, invalid, reasons


def _execute_partition(
    planned: PlannedTask,
    *,
    run_id: str,
    out_dir: Path,
    source_mode: str,
) -> WorkerResult:
    if planned.input_checksum is None or not planned.input_path.is_file():
        raise BatchError(
            f"staged source window missing for {planned.region.region_id}: "
            f"{planned.input_path}; stage it with a bounded /vsicurl window read before retrying"
        )
    current_checksum = sha256_file(planned.input_path)
    if current_checksum != planned.input_checksum:
        raise BatchError(
            f"staged source window changed after planning for {planned.region.region_id} "
            f"({planned.input_path}); start a new run id so the checkpoint matches the input"
        )
    started_at = datetime.now(UTC).isoformat()
    started = time.perf_counter()
    rows, no_coverage, invalid, reasons = _task_rows(planned, source_mode=source_mode)
    if rows:
        outcome = "succeeded"
    elif invalid:
        outcome = "invalid"
    elif no_coverage:
        outcome = "no_coverage"
    else:  # pragma: no cover - a partition always has at least one cell
        outcome = "invalid"
    warnings: list[str] = []
    if no_coverage:
        warnings.append(f"{len(no_coverage)} cell(s) without source coverage")
    if invalid:
        warnings.append(f"{len(invalid)} invalid cell(s)")
    task_dir = out_dir / "tasks" / planned.task_id
    parquet = write_coarse_parquet(task_dir / "rows.parquet", rows)
    duration = time.perf_counter() - started
    sidecar = {
        "batch_schema_version": BATCH_SCHEMA_VERSION,
        "checkpoint_version": CHECKPOINT_VERSION,
        "run_id": run_id,
        "source_mode": source_mode,
        "task": {
            "task_id": planned.task_id,
            "fingerprint": planned.task.fingerprint,
            "region_id": planned.region.region_id,
            "partition_index": planned.partition_index,
            "window_count": planned.task.window_count,
            "cells": [cell.candidate_id for cell in planned.cells],
            "input": {
                "path": str(planned.input_path),
                "sha256": planned.input_checksum,
                "bytes": planned.input_bytes,
                "matches_published_window": (
                    planned.input_checksum == PILOT_WINDOW_SHA256.get(planned.region.region_id)
                ),
            },
        },
        "outcome": outcome,
        "rows": len(rows),
        "cell_counts": {
            "succeeded": len(rows),
            "no_coverage": len(no_coverage),
            "invalid": len(invalid),
        },
        "reasons": reasons,
        "output": {
            "file": "rows.parquet",
            "sha256": parquet["sha256"],
            "bytes": parquet["bytes"],
            "rows": parquet["rows"],
        },
        "versions": {
            "method_version": COARSE_METHOD_VERSION,
            "grid_version": GRID_VERSION,
            "index_version": INDEX_VERSION,
            "parquet_schema_version": PARQUET_SCHEMA_VERSION,
        },
        "dataset": {
            "dataset_id": "synthetic-fixture" if source_mode == "synthetic-fixture" else DATASET_ID,
            "source_url": SOURCE_URL,
            "license": SOURCE_LICENSE,
            "citation": SOURCE_CITATION,
            "qa_flags": list(
                SYNTHETIC_QA_FLAGS if source_mode == "synthetic-fixture" else REAL_QA_FLAGS
            ),
        },
        "timing": {
            "started_at": started_at,
            "finished_at": datetime.now(UTC).isoformat(),
            "duration_s": round(duration, 6),
        },
    }
    atomic_write_json(task_dir / "task.json", sidecar)
    return WorkerResult(
        output_checksum=str(parquet["sha256"]),
        rows=len(rows),
        output_path=str(parquet["path"]),
        warnings=tuple(warnings),
        status=outcome,
        metrics={
            "cells_succeeded": len(rows),
            "cells_no_coverage": len(no_coverage),
            "cells_invalid": len(invalid),
        },
    )


def make_worker(
    *,
    run_id: str,
    planned_by_id: Mapping[str, PlannedTask],
    out_dir: Path,
    source_mode: str,
) -> Worker:
    def worker(task: PartitionTask) -> WorkerResult:
        planned = planned_by_id.get(task.task_id)
        if planned is None:
            raise BatchError(f"task {task.task_id} is not part of the current plan")
        if planned.task.fingerprint != task.fingerprint:
            raise BatchError(f"task {task.task_id} does not match the planned fingerprint")
        return _execute_partition(planned, run_id=run_id, out_dir=out_dir, source_mode=source_mode)

    return worker


def _verify_terminal_record(
    task_id: str,
    record: Mapping[str, object],
    *,
    out_dir: Path,
) -> None:
    status = str(record.get("status"))
    if status not in TERMINAL_STATUSES:
        raise BatchError(f"task {task_id} is not terminal ({status})")
    checksum = record.get("output_checksum")
    path_value = record.get("output_path")
    if not checksum or not path_value:
        raise BatchError(f"task {task_id} has no recorded output artifact")
    path = Path(str(path_value))
    if not path.is_file():
        raise BatchError(f"task {task_id} output artifact is missing: {path}")
    if sha256_file(path) != str(checksum):
        raise BatchError(f"task {task_id} output artifact checksum mismatch: {path}")
    sidecar_path = path.parent / "task.json"
    if not sidecar_path.is_file():
        raise BatchError(f"task {task_id} sidecar is missing: {sidecar_path}")
    sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    if not isinstance(sidecar, dict):
        raise BatchError(f"task {task_id} sidecar is not a JSON object")
    if sidecar.get("batch_schema_version") != BATCH_SCHEMA_VERSION:
        raise BatchError(f"task {task_id} sidecar schema version is unsupported")
    task_block = sidecar.get("task") or {}
    output_block = sidecar.get("output") or {}
    cell_counts = sidecar.get("cell_counts") or {}
    if task_block.get("task_id") != task_id:
        raise BatchError(f"task {task_id} sidecar belongs to {task_block.get('task_id')!r}")
    if task_block.get("fingerprint") != record.get("fingerprint"):
        raise BatchError(f"task {task_id} sidecar fingerprint mismatch")
    sidecar_input = (task_block.get("input") or {}).get("sha256")
    if sidecar_input != record.get("input_checksum"):
        raise BatchError(f"task {task_id} sidecar input checksum mismatch")
    if sidecar.get("outcome") != status:
        raise BatchError(f"task {task_id} sidecar outcome {sidecar.get('outcome')!r} != {status!r}")
    if int(sidecar.get("rows", -1)) != int(record.get("rows", 0)):
        raise BatchError(f"task {task_id} sidecar row count mismatch")
    if str(output_block.get("sha256")) != str(checksum):
        raise BatchError(f"task {task_id} sidecar output checksum mismatch")
    cells = int(sum(int(value) for value in cell_counts.values()))
    if cells != int(record.get("window_count", 0)):
        raise BatchError(f"task {task_id} sidecar cell count mismatch")
    if int(cell_counts.get("succeeded", -1)) != int(record.get("rows", 0)):
        raise BatchError(f"task {task_id} sidecar succeeded-cell count mismatch")


def verify_task_artifact(task: PartitionTask, record: dict[str, object], *, out_dir: Path) -> bool:
    """Resume guard: only reuse a terminal task whose Parquet and sidecar still agree."""
    try:
        _verify_terminal_record(task.task_id, record, out_dir=out_dir)
    except (BatchError, OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return False
    return True


def verify_run(
    records: Mapping[str, dict[str, object]],
    *,
    out_dir: Path,
    planned_by_id: Mapping[str, PlannedTask] | None = None,
) -> dict[str, object]:
    problems: list[dict[str, str]] = []
    verified = 0
    terminal = 0
    for task_id in sorted(records):
        record = records[task_id]
        if str(record.get("status")) not in TERMINAL_STATUSES:
            continue
        terminal += 1
        try:
            _verify_terminal_record(task_id, record, out_dir=out_dir)
            planned = (planned_by_id or {}).get(task_id)
            if planned is not None:
                if planned.input_checksum is None:
                    # Inputs are not staged here, so the fingerprint cannot be
                    # recomputed; the stable partition identity is still checkable.
                    if planned.task.window_count != record.get(
                        "window_count"
                    ) or planned.region.region_id != record.get("region_id"):
                        raise BatchError(f"task {task_id} plan partition mismatch")
                else:
                    if planned.task.fingerprint != record.get("fingerprint"):
                        raise BatchError(f"task {task_id} plan fingerprint mismatch")
                    if planned.input_checksum != record.get("input_checksum"):
                        raise BatchError(f"task {task_id} plan input checksum mismatch")
        except (BatchError, OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            problems.append({"task_id": task_id, "error": str(exc)})
        else:
            verified += 1
    artifacts_root = out_dir / "tasks"
    orphaned = sorted(
        path.parent.name
        for path in artifacts_root.glob("*/task.json")
        if path.parent.name not in records
    )
    return {
        "ok": not problems,
        "terminal_tasks": terminal,
        "verified": verified,
        "problems": problems,
        "orphaned_artifacts": orphaned,
    }


def estimate_peak_mb(workers: int) -> float:
    return round(workers * ESTIMATE_BYTES_PER_CELL / (1024 * 1024), 3)


def _input_payload(planned: Sequence[PlannedTask]) -> dict[str, object]:
    inputs: dict[str, object] = {}
    for task in planned:
        region_id = task.region.region_id
        if region_id in inputs:
            continue
        inputs[region_id] = {
            "path": str(task.input_path),
            "sha256": task.input_checksum,
            "bytes": task.input_bytes,
            "present": task.input_checksum is not None,
            "expected_published_sha256": PILOT_WINDOW_SHA256.get(region_id),
            "matches_published_window": (task.input_checksum == PILOT_WINDOW_SHA256.get(region_id)),
        }
    return inputs


def _plan_payload(
    planned: Sequence[PlannedTask],
    *,
    selected: Sequence[PlannedTask] | None = None,
    limit: int | None,
) -> dict[str, object]:
    selected = list(planned) if selected is None else list(selected)
    return {
        "regions": sorted({task.region.region_id for task in planned}),
        "tasks": len(planned),
        "cells": sum(task.task.window_count for task in planned),
        "limit": limit,
        "selected_tasks": len(selected),
        "selected_cells": sum(task.task.window_count for task in selected),
        "selected": [
            {
                "task_id": task.task_id,
                "region_id": task.region.region_id,
                "partition_index": task.partition_index,
                "window_count": task.task.window_count,
            }
            for task in selected
        ],
    }


def build_report(
    *,
    run_id: str,
    out_dir: Path,
    checkpoint_path: Path,
    report_path: Path,
    planned_all: Sequence[PlannedTask],
    selected: Sequence[PlannedTask],
    records: Mapping[str, dict[str, object]],
    verification: Mapping[str, object],
    bounds: Mapping[str, object],
    source_mode: str,
    dry_run: bool,
    limit: int | None,
) -> dict[str, object]:
    status_counts: dict[str, int] = {}
    cell_metrics: dict[str, int] = {}
    rows = 0
    for record in records.values():
        status = str(record.get("status"))
        status_counts[status] = status_counts.get(status, 0) + 1
        rows += int(record.get("rows", 0) or 0)
        for key, value in dict(record.get("metrics") or {}).items():
            cell_metrics[str(key)] = cell_metrics.get(str(key), 0) + int(value)
    by_region: dict[str, dict[str, object]] = {}
    for task in planned_all:
        region_id = task.region.region_id
        entry = by_region.setdefault(
            region_id,
            {
                "region_id": region_id,
                "tasks_planned": 0,
                "cells_planned": 0,
                "tasks": {},
                "rows": 0,
                "cell_counts": {"succeeded": 0, "no_coverage": 0, "invalid": 0},
                "input": _input_payload([task])[region_id],
            },
        )
        entry["tasks_planned"] = int(entry["tasks_planned"]) + 1
        entry["cells_planned"] = int(entry["cells_planned"]) + task.task.window_count
    for record in records.values():
        region_id = str(record.get("region_id"))
        entry = by_region.setdefault(
            region_id,
            {
                "region_id": region_id,
                "tasks_planned": 0,
                "cells_planned": 0,
                "tasks": {},
                "rows": 0,
                "cell_counts": {"succeeded": 0, "no_coverage": 0, "invalid": 0},
                "input": None,
            },
        )
        tasks = dict(entry["tasks"])
        status = str(record.get("status"))
        tasks[status] = int(tasks.get(status, 0)) + 1
        entry["tasks"] = tasks
        entry["rows"] = int(entry["rows"]) + int(record.get("rows", 0) or 0)
        counts = dict(entry["cell_counts"])
        metrics = dict(record.get("metrics") or {})
        for key in ("succeeded", "no_coverage", "invalid"):
            counts[key] = int(counts[key]) + int(metrics.get(f"cells_{key}", 0))
        entry["cell_counts"] = counts
    artifacts = []
    for task_id in sorted(records):
        record = records[task_id]
        if str(record.get("status")) not in TERMINAL_STATUSES:
            continue
        path_value = record.get("output_path")
        relative = None
        if path_value:
            try:
                relative = Path(str(path_value)).relative_to(out_dir).as_posix()
            except ValueError:
                relative = str(path_value)
        artifacts.append(
            {
                "task_id": task_id,
                "region_id": record.get("region_id"),
                "status": record.get("status"),
                "rows": record.get("rows"),
                "attempts": record.get("attempts"),
                "output_checksum": record.get("output_checksum"),
                "output_path": relative,
            }
        )
    warnings: list[str] = []
    for region_id, payload in sorted(_input_payload(planned_all).items()):
        if not payload["present"]:
            warnings.append(
                f"staged source window missing for {region_id} at {payload['path']}; "
                "its tasks fail until the window is staged"
            )
        elif not payload["matches_published_window"]:
            warnings.append(
                f"staged window checksum for {region_id} does not match the published pilot "
                "record; the run records the actual checksum and is not comparable row-for-row"
            )
    if source_mode == "synthetic-fixture":
        warnings.append(
            "SYNTHETIC FIXTURE MODE: rows are not observations and must never be published."
        )
    if limit and len(selected) < len(planned_all):
        warnings.append(
            f"--limit selected {len(selected)} of {len(planned_all)} planned tasks; "
            "task identities and checkpoint records are unchanged"
        )
    non_terminal = {
        status: count
        for status, count in sorted(status_counts.items())
        if status not in TERMINAL_STATUSES
    }
    if non_terminal:
        warnings.append(f"non-terminal checkpoint tasks remain: {non_terminal}")
    return {
        "batch_schema_version": BATCH_SCHEMA_VERSION,
        "checkpoint_version": CHECKPOINT_VERSION,
        "run_id": run_id,
        "dry_run": dry_run,
        "generated_at": datetime.now(UTC).isoformat(),
        "source_mode": source_mode,
        "checkpoint": str(checkpoint_path),
        "out_dir": str(out_dir),
        "report_path": str(report_path),
        "plan": _plan_payload(planned_all, selected=selected, limit=limit),
        "inputs": _input_payload(planned_all),
        "outcomes": {
            "tasks": status_counts,
            "rows": rows,
            "cell_counts": {
                "succeeded": cell_metrics.get("cells_succeeded", 0),
                "no_coverage": cell_metrics.get("cells_no_coverage", 0),
                "invalid": cell_metrics.get("cells_invalid", 0),
            },
            "selected_tasks": len(selected),
        },
        "regions": [by_region[key] for key in sorted(by_region)],
        "artifacts": artifacts,
        "verification": dict(verification),
        "bounds": dict(bounds),
        "coverage": {
            "label": (
                SYNTHETIC_COVERAGE_LABEL if source_mode == "synthetic-fixture" else COVERAGE_LABEL
            ),
            "note": (
                "Synthetic fixtures are not observations."
                if source_mode == "synthetic-fixture"
                else "Three 0.4 degree pilot regions only; not a global or exhaustive survey."
            ),
        },
        "versions": {
            "method_version": COARSE_METHOD_VERSION,
            "grid_version": GRID_VERSION,
            "index_version": INDEX_VERSION,
            "parquet_schema_version": PARQUET_SCHEMA_VERSION,
            "checkpoint_version": CHECKPOINT_VERSION,
        },
        "limitations": list(_LIMITATIONS),
        "warnings": warnings,
    }


def _validate_run_id(run_id: str) -> None:
    if not run_id or not all(char.isalnum() or char in "-_." for char in run_id):
        raise CliError("--run-id must be a non-empty path-safe string ([A-Za-z0-9._-])")


def _dry_run_payload(
    args: argparse.Namespace,
    planned_all: Sequence[PlannedTask],
    selected: Sequence[PlannedTask],
) -> dict[str, object]:
    payload = {
        "batch_schema_version": BATCH_SCHEMA_VERSION,
        "mode": "dry-run",
        "note": "dry run: no checkpoint, artifact, or report was written",
        "run_id": args.run_id,
        "source_mode": args.source_mode,
        "checkpoint": str(Path(args.out) / "runs" / args.run_id / "checkpoint.json"),
        "report": str(
            Path(args.report)
            if args.report
            else Path(args.out) / "runs" / args.run_id / "report.json"
        ),
        "plan": _plan_payload(planned_all, selected=selected, limit=args.limit),
        "inputs": _input_payload(planned_all),
        "bounds": {
            "workers": args.workers,
            "max_retries": args.max_retries,
            "cells_per_task": args.cells_per_task,
            "memory_budget_mb": args.memory_budget_mb,
            "estimated_peak_mb": estimate_peak_mb(args.workers),
        },
        "coverage": {
            "label": (
                SYNTHETIC_COVERAGE_LABEL
                if args.source_mode == "synthetic-fixture"
                else COVERAGE_LABEL
            )
        },
    }
    payload["tasks"] = [task.to_payload() for task in selected]
    return payload


def _run(args: argparse.Namespace) -> tuple[dict[str, object], int]:
    _validate_run_id(args.run_id)
    if args.workers < 1 or args.workers > MAX_WORKERS:
        raise CliError(f"--workers must be within 1..{MAX_WORKERS}")
    if args.cells_per_task < 1 or args.cells_per_task > MAX_CELLS_PER_TASK:
        raise CliError(f"--cells-per-task must be within 1..{MAX_CELLS_PER_TASK}")
    if args.max_retries < 0 or args.max_retries > 2:
        raise CliError("--max-retries must be within 0..2")
    if args.limit is not None and args.limit < 1:
        raise CliError("--limit must be >= 1")
    if args.memory_budget_mb < 1:
        raise CliError("--memory-budget-mb must be >= 1")
    if args.dry_run and args.verify_only:
        raise CliError("--dry-run and --verify-only are mutually exclusive")
    estimate = estimate_peak_mb(args.workers)
    if estimate > args.memory_budget_mb:
        raise CliError(
            f"estimated peak {estimate} MB exceeds --memory-budget-mb {args.memory_budget_mb}; "
            f"reduce --workers (currently {args.workers})"
        )
    regions = select_regions(args.region)
    planned_all = plan_tasks(regions, cache_dir=args.cache, cells_per_task=args.cells_per_task)
    if not planned_all:
        raise CliError("no planned tasks for the selected regions")
    selected = list(planned_all[: args.limit]) if args.limit else list(planned_all)
    planned_by_id = {task.task_id: task for task in selected}
    run_root = Path(args.out) / "runs" / args.run_id
    checkpoint = run_root / "checkpoint.json"
    report_path = Path(args.report) if args.report else run_root / "report.json"
    bounds = {
        "workers": args.workers,
        "max_retries": args.max_retries,
        "cells_per_task": args.cells_per_task,
        "tasks_planned": len(planned_all),
        "tasks_selected": len(selected),
        "memory_budget_mb": args.memory_budget_mb,
        "estimated_peak_mb": estimate,
        "estimate_kind": "analytic_lower_bound_excludes_gdal_overhead",
    }

    if args.dry_run:
        return _dry_run_payload(args, planned_all, selected), 0

    if args.verify_only:
        if not checkpoint.is_file():
            raise CliError(f"--verify-only needs an existing checkpoint: {checkpoint}")
        store = CheckpointStore(checkpoint)
        if store.state.run_id != args.run_id:
            raise CliError(f"checkpoint belongs to run {store.state.run_id!r}, not {args.run_id!r}")
        records = store.state.tasks
        verification = verify_run(records, out_dir=run_root, planned_by_id=planned_by_id)
        report = build_report(
            run_id=args.run_id,
            out_dir=run_root,
            checkpoint_path=checkpoint,
            report_path=report_path,
            planned_all=planned_all,
            selected=selected,
            records=records,
            verification=verification,
            bounds=bounds,
            source_mode=args.source_mode,
            dry_run=False,
            limit=args.limit,
        )
        atomic_write_json(report_path, report)
        return report, 0 if verification["ok"] else 1

    if checkpoint.is_file() and not args.resume:
        raise CliError(
            f"checkpoint already exists at {checkpoint}; pass --resume to continue that run "
            "or choose a new --run-id (never reuse a run id for a different plan)"
        )
    if args.resume and not checkpoint.is_file():
        raise CliError(f"--resume given but no checkpoint exists at {checkpoint}")
    existing_ids: set[str] = set()
    if args.resume:
        store = CheckpointStore(checkpoint)
        if store.state.run_id != args.run_id:
            raise CliError(f"checkpoint belongs to run {store.state.run_id!r}, not {args.run_id!r}")
        existing_ids = set(store.state.tasks)
        plan_ids = {task.task_id for task in planned_all}
        if (existing_ids - plan_ids) and (plan_ids - existing_ids):
            raise CliError(
                "checkpoint tasks do not match the current plan (changed --region or "
                "--cells-per-task?); resume the original plan or choose a new --run-id"
            )
    worker = make_worker(
        run_id=args.run_id,
        planned_by_id=planned_by_id,
        out_dir=run_root,
        source_mode=args.source_mode,
    )
    runner = BatchRunner(
        run_id=args.run_id,
        checkpoint_path=checkpoint,
        worker=worker,
        max_workers=args.workers,
        max_retries=args.max_retries,
        max_tasks=min(MAX_TASKS, max(1, len(existing_ids | {t.task_id for t in planned_all}))),
        reuse_check=lambda task, record: verify_task_artifact(task, record, out_dir=run_root),
    )
    counts = runner.run([task.task for task in selected], retry_failed=args.retry_failed)
    records = runner.store.state.tasks
    verification = verify_run(records, out_dir=run_root, planned_by_id=planned_by_id)
    report = build_report(
        run_id=args.run_id,
        out_dir=run_root,
        checkpoint_path=checkpoint,
        report_path=report_path,
        planned_all=planned_all,
        selected=selected,
        records=records,
        verification=verification,
        bounds={**bounds, "last_counts": counts},
        source_mode=args.source_mode,
        dry_run=False,
        limit=args.limit,
    )
    atomic_write_json(report_path, report)
    ok = verification["ok"] and counts["failed"] == 0 and counts["invalid"] == 0
    return report, 0 if ok else 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Resumable bounded batch runner for the three-region Geomorpho250 pilot. "
            "Reads locally staged windows only; never downloads the global raster."
        )
    )
    parser.add_argument("--run-id", required=True, help="Path-safe run identity, e.g. pilot-2026")
    parser.add_argument(
        "--region",
        action="append",
        default=None,
        help="Pilot region id (repeat or comma-separate). Default: all three pilot regions.",
    )
    parser.add_argument(
        "--limit", type=int, default=None, help="Process only the first N planned tasks"
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help=f"Concurrent partitions, bounded 1..{MAX_WORKERS} (default 1)",
    )
    parser.add_argument(
        "--cells-per-task",
        type=int,
        default=DEFAULT_CELLS_PER_TASK,
        help=(
            f"Cells per deterministic partition, {1}..{MAX_CELLS_PER_TASK} "
            f"(default {DEFAULT_CELLS_PER_TASK})"
        ),
    )
    parser.add_argument(
        "--cache",
        default=DEFAULT_CACHE_DIR,
        help="Directory with staged <region_id>.tif slope windows",
    )
    parser.add_argument(
        "--out", default=DEFAULT_OUT_DIR, help="Output root (run state + artifacts)"
    )
    parser.add_argument(
        "--report", default=None, help="Report JSON path (default <out>/runs/<run-id>/report.json)"
    )
    parser.add_argument(
        "--source-mode",
        choices=("real", "synthetic-fixture"),
        default="real",
        help="real Geomorpho250 rows (default) or clearly labelled synthetic test fixtures",
    )
    parser.add_argument("--resume", action="store_true", help="Resume an existing run's checkpoint")
    parser.add_argument(
        "--retry-failed",
        action="store_true",
        help="Retry previously failed tasks (successful tasks are still not recomputed)",
    )
    parser.add_argument(
        "--max-retries", type=int, default=1, help="Per-task retries within one invocation (0..2)"
    )
    parser.add_argument(
        "--memory-budget-mb",
        type=int,
        default=DEFAULT_MEMORY_BUDGET_MB,
        help="Bounded-resource hint; the run refuses plans above the analytic estimate",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Print the plan and exit without writing files"
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="Verify checkpointed artifacts/checksums without running any task",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        payload, exit_code = _run(args)
    except (CliError, BatchError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(payload, indent=2, sort_keys=True))
    return exit_code


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
