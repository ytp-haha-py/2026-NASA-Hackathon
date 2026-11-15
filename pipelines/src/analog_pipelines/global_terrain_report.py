"""Reproducible source-grounded global terrain evaluation report (Issue #39).

Inputs are limited to committed artefacts:

* the verified real coarse Parquet index (manifest plus content-addressed partitions);
* the committed three-region batch evidence (report, checkpoint, per-task sidecars);
* the committed pilot source record inside ``docs/GEOMORPHO_REAL_PILOT.md``;
* the committed coarse-to-90 m refinement report when present;
* the committed DB capacity measurements in ``docs/DB_CAPACITY_REPORT.md``.

The builder never downloads, stages, or synthesises a source raster, never runs a scan,
and never turns the three-region pilot into a global or exhaustive claim. Output is
byte-deterministic so ``--check`` can detect drift from the committed report.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import statistics
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb

from analog_pipelines.coarse_features import (
    COARSE_FEATURE_BY_ID,
    COARSE_FEATURES,
    COARSE_METHOD_VERSION,
)
from analog_pipelines.coarse_index import (
    CoarseIndexRepository,
    IndexError_,
    resolve_index_root,
    verify_index,
)

REPORT_VERSION = "global-terrain-results-1.0.0"
DOCUMENT_NAME = "GLOBAL_TERRAIN_RESULTS.md"
ARTIFACT_NAMES = (
    "coverage.json",
    "top-k-queries.json",
    "regions.csv",
    "performance.json",
    "refinement.json",
    "checksums.json",
)

FEATURE_COLUMNS = {
    "coarse_slope_mean_deg": "slope_mean_deg",
    "coarse_slope_p90_deg": "slope_p90_deg",
    "coarse_flat_fraction_lt5deg": "flat_fraction",
}

#: Arithmetic denominator only. It is not a measured coverage denominator and the
#: report must never present the resulting ratio as a survey percentage.
EARTH_LAND_AREA_KM2 = 148_940_000.0
CELL_AREA_KM2 = 25.0
FINE_METHOD_VERSION = "terrain-metrics-1.0.0"

QUERY_EXAMPLES: tuple[dict[str, Any], ...] = (
    {
        "query_id": "Q1-flat-low-relief",
        "label": "Flat, low-relief target",
        "target": {
            "coarse_slope_mean_deg": 2.0,
            "coarse_slope_p90_deg": 6.0,
            "coarse_flat_fraction_lt5deg": 0.90,
        },
        "weights": {
            "coarse_slope_mean_deg": 1.0,
            "coarse_slope_p90_deg": 1.0,
            "coarse_flat_fraction_lt5deg": 1.0,
        },
    },
    {
        "query_id": "Q2-moderate-terrain",
        "label": "Moderate mixed terrain target",
        "target": {
            "coarse_slope_mean_deg": 8.0,
            "coarse_slope_p90_deg": 18.0,
            "coarse_flat_fraction_lt5deg": 0.50,
        },
        "weights": {
            "coarse_slope_mean_deg": 1.0,
            "coarse_slope_p90_deg": 1.0,
            "coarse_flat_fraction_lt5deg": 1.0,
        },
    },
    {
        "query_id": "Q3-steep-canyon",
        "label": "Steep canyon/edge target",
        "target": {
            "coarse_slope_mean_deg": 25.0,
            "coarse_slope_p90_deg": 45.0,
            "coarse_flat_fraction_lt5deg": 0.10,
        },
        "weights": {
            "coarse_slope_mean_deg": 1.0,
            "coarse_slope_p90_deg": 2.0,
            "coarse_flat_fraction_lt5deg": 1.0,
        },
    },
)


class GlobalReportError(RuntimeError):
    """A committed input is missing, inconsistent, or not reproducible."""


class GlobalClaimError(GlobalReportError):
    """A manifest would have to be described as global/exhaustive without evidence."""


@dataclass(frozen=True, slots=True)
class ReportInputs:
    repo_root: Path
    index_root: Path
    batch_dir: Path
    pilot_doc: Path
    db_report: Path
    refine_json: Path | None = None


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise GlobalReportError(f"missing committed input: {path}") from exc
    except json.JSONDecodeError as exc:
        raise GlobalReportError(f"invalid JSON in {path}: {exc}") from exc


def _f(value: Any, digits: int = 6) -> float | None:
    if value is None:
        return None
    return round(float(value), digits)


def _fixed(value: Any, digits: int = 6) -> str:
    if value is None:
        return ""
    return f"{float(value):.{digits}f}"


def _fmt(value: Any, spec: str, dash: str = "—") -> str:
    return dash if value is None else format(float(value), spec)


def _json_text(payload: Any) -> str:
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def assert_not_global_claim(manifest: dict[str, Any]) -> None:
    """Fail closed unless the manifest scope explicitly says it is not global.

    A future genuinely complete index must carry machine-checkable completeness
    evidence and change this guard in review; a pilot label alone is never enough.
    """
    coverage = manifest.get("coverage") or {}
    label = str(coverage.get("label", ""))
    if "not_global" in label or "not-global" in label:
        return
    if any(token in label for token in ("global", "worldwide", "exhaustive")):
        raise GlobalClaimError(
            f"coverage label {label!r} claims global/exhaustive scope without evidence; "
            "refusing to publish it from this builder"
        )
    raise GlobalReportError(
        f"coverage label {label!r} does not state its scope explicitly; expected a "
        "subset label such as 'local_coarse_index_not_global_exhaustive'"
    )


def _validate_versions(manifest: dict[str, Any], batch: dict[str, Any]) -> None:
    versions = batch.get("versions") or {}
    for key in ("grid_version", "index_version", "method_version", "parquet_schema_version"):
        if versions.get(key) != manifest.get(key):
            raise GlobalReportError(
                f"batch {key}={versions.get(key)!r} does not match index "
                f"{key}={manifest.get(key)!r}"
            )
    if manifest.get("method_version") != COARSE_METHOD_VERSION:
        raise GlobalReportError("index method_version is not the registered coarse method")


def load_batch_evidence(
    batch_dir: Path, manifest: dict[str, Any], *, repo_root: Path
) -> dict[str, Any]:
    """Verify the committed batch run against the committed index.

    The Parquet outputs and staged windows are not committed; their SHA-256 values are,
    so this checks the recorded provenance chain instead of inventing measurements.
    """
    report = _load_json(batch_dir / "report.json")
    checkpoint = _load_json(batch_dir / "checkpoint.json")
    task_paths = sorted((batch_dir / "tasks").glob("*/task.json"))
    if not task_paths:
        raise GlobalReportError(f"no committed task sidecars under {batch_dir / 'tasks'}")
    tasks = [_load_json(path) for path in task_paths]
    if report.get("source_mode") != "real":
        raise GlobalReportError(
            "committed batch report is not source_mode=real; synthetic fixtures cannot "
            "back this report"
        )
    run_id = report.get("run_id")
    if checkpoint.get("run_id") != run_id:
        raise GlobalReportError("batch report and checkpoint run_id differ")
    _validate_versions(manifest, report)

    artifacts = {item["task_id"]: item for item in report.get("artifacts", [])}
    checkpoint_tasks = checkpoint.get("tasks") or {}
    task_ids = {item["task"]["task_id"] for item in tasks}
    if set(artifacts) != task_ids or set(checkpoint_tasks) != task_ids:
        raise GlobalReportError("batch report, checkpoint, and task sidecars disagree on task ids")

    manifest_rows: dict[str, int] = defaultdict(int)
    for partition in manifest.get("partitions", []):
        manifest_rows[partition["region_id"]] += int(partition["rows"])

    region_rows = {item["region_id"]: int(item["rows"]) for item in report.get("regions", [])}
    if dict(region_rows) != dict(manifest_rows):
        raise GlobalReportError(
            f"batch region rows {region_rows} do not match index partition rows "
            f"{dict(manifest_rows)}"
        )

    input_regions = set((report.get("inputs") or {}).keys())
    if input_regions != set(region_rows):
        raise GlobalReportError("batch input staging regions do not match reported regions")

    durations: list[float] = []
    task_bytes: list[int] = []
    for task in tasks:
        task_id = task["task"]["task_id"]
        region_id = task["task"]["region_id"]
        artifact = artifacts[task_id]
        recorded = checkpoint_tasks[task_id]
        if task.get("outcome") != "succeeded" or recorded.get("status") != "succeeded":
            raise GlobalReportError(f"task {task_id} is not a succeeded terminal task")
        if artifact.get("status") != "succeeded":
            raise GlobalReportError(f"batch report artifact {task_id} is not succeeded")
        if task["output"]["sha256"] != artifact["output_checksum"]:
            raise GlobalReportError(f"task {task_id} output checksum differs from the batch report")
        if recorded.get("output_checksum") != artifact["output_checksum"]:
            raise GlobalReportError(f"task {task_id} checkpoint checksum differs")
        if int(task["output"]["rows"]) != int(artifact["rows"]) or int(artifact["rows"]) != int(
            task["rows"]
        ):
            raise GlobalReportError(f"task {task_id} row counts differ")
        if artifact["region_id"] != region_id:
            raise GlobalReportError(f"task {task_id} region differs from the batch report")
        counted = task.get("cell_counts") or {}
        if int(counted.get("succeeded", 0)) != int(task["rows"]):
            raise GlobalReportError(f"task {task_id} cell counts do not sum to its rows")
        if (
            int(counted.get("failed", 0))
            or int(counted.get("no_coverage", 0))
            or int(counted.get("invalid", 0))
        ):
            raise GlobalReportError(f"task {task_id} contains non-succeeded cells")
        staged = report["inputs"][region_id]
        if not staged.get("present") or not staged.get("matches_published_window"):
            raise GlobalReportError(f"staged window for {region_id} is not verified")
        if staged["sha256"] != staged["expected_published_sha256"]:
            raise GlobalReportError(f"staged window checksum for {region_id} does not match")
        if task["task"]["input"]["sha256"] != staged["sha256"]:
            raise GlobalReportError(f"task {task_id} input checksum differs from the staged window")
        if recorded.get("input_checksum") != staged["sha256"]:
            raise GlobalReportError(f"checkpoint {task_id} input checksum differs")
        durations.append(float(task["timing"]["duration_s"]))
        task_bytes.append(int(task["output"]["bytes"]))

    outcomes = report.get("outcomes") or {}
    if int(outcomes.get("rows", -1)) != int(manifest["row_count"]):
        raise GlobalReportError("batch outcome rows do not match the index row_count")
    cell_counts = outcomes.get("cell_counts") or {}
    if int(cell_counts.get("succeeded", -1)) != int(manifest["row_count"]):
        raise GlobalReportError("batch succeeded cell count does not match the index row_count")
    for key in ("failed", "invalid", "no_coverage"):
        if int(cell_counts.get(key, 0)) != 0:
            raise GlobalReportError(f"batch recorded {key} cells; the report would be misleading")
    verification = report.get("verification") or {}
    if not verification.get("ok") or int(verification.get("verified", 0)) != len(tasks):
        raise GlobalReportError("committed batch artifact verification is not clean")

    checksums = []
    for path in [batch_dir / "report.json", batch_dir / "checkpoint.json", *task_paths]:
        checksums.append(
            {
                "path": path.relative_to(repo_root).as_posix(),
                "sha256": _sha256_file(path),
            }
        )

    return {
        "run_id": run_id,
        "evidence_path": batch_dir.relative_to(repo_root).as_posix(),
        "generated_at": report.get("generated_at"),
        "source_mode": report["source_mode"],
        "workers": int(report["bounds"]["workers"]),
        "cells_per_task": int(report["bounds"]["cells_per_task"]),
        "tasks": len(tasks),
        "cells": int(manifest["row_count"]),
        "cell_counts": {key: int(cell_counts[key]) for key in sorted(cell_counts)},
        "region_rows": dict(sorted(region_rows.items())),
        "task_rows": {task["task"]["task_id"]: int(task["rows"]) for task in tasks},
        "task_duration_s": {
            "sum": round(sum(durations), 6),
            "mean": round(statistics.fmean(durations), 6),
            "median": round(statistics.median(durations), 6),
            "min": round(min(durations), 6),
            "max": round(max(durations), 6),
        },
        "parquet_bytes": {
            "total": sum(task_bytes),
            "per_cell": round(sum(task_bytes) / int(manifest["row_count"]), 6),
        },
        "analytic_peak_mb": float(report["bounds"]["estimated_peak_mb"]),
        "analytic_peak_kind": report["bounds"].get("estimate_kind"),
        "verification": {
            "ok": bool(verification["ok"]),
            "verified_tasks": int(verification["verified"]),
            "terminal_tasks": int(verification["terminal_tasks"]),
            "orphaned_artifacts": list(verification.get("orphaned_artifacts", [])),
            "problems": list(verification.get("problems", [])),
        },
        "inputs": {
            region: {
                "staged_window_sha256": item["sha256"],
                "expected_published_sha256": item["expected_published_sha256"],
                "staged_window_bytes": int(item["bytes"]),
                "matches_published_window": bool(item["matches_published_window"]),
            }
            for region, item in sorted(report["inputs"].items())
        },
        "checksums": checksums,
    }


def _coarse_distance(
    candidate: dict[str, Any], target: dict[str, float], weights: dict[str, float]
) -> float:
    total = sum(
        float(weights.get(feature_id, 1.0))
        for feature_id in target
        if float(weights.get(feature_id, 1.0)) > 0
    )
    if total <= 0:
        raise GlobalReportError("refinement selection has no positive coarse weight")
    squared = 0.0
    for feature_id, target_value in target.items():
        weight = float(weights.get(feature_id, 1.0))
        if weight <= 0:
            continue
        scale = COARSE_FEATURE_BY_ID[feature_id].scale
        value = candidate[FEATURE_COLUMNS[feature_id]]
        squared += weight * ((float(value) - float(target_value)) / scale) ** 2
    return math.sqrt(squared / total)


def load_refinement_evidence(
    path: Path,
    manifest: dict[str, Any],
    repository: CoarseIndexRepository,
    *,
    repo_root: Path,
    manifest_sha256: str,
) -> dict[str, Any]:
    """Verify the committed coarse-to-90 m refinement pilot against the same index."""
    report = _load_json(path)
    if report.get("report_version") != "coarse-refine-report-1.0.0":
        raise GlobalReportError(
            f"unsupported refinement report version {report.get('report_version')!r}"
        )
    recorded_index = report.get("input_index") or {}
    expected_index = {
        "snapshot_id": manifest["snapshot_id"],
        "method_version": manifest["method_version"],
        "grid_version": manifest["grid_version"],
        "index_version": manifest["index_version"],
        "manifest_sha256": manifest_sha256,
        "row_count": int(manifest["row_count"]),
    }
    for key, value in expected_index.items():
        if recorded_index.get(key) != value:
            raise GlobalReportError(
                f"refinement input_index {key}={recorded_index.get(key)!r} does not match "
                f"the committed index {value!r}"
            )
    expected_partitions = sorted(
        (
            {
                "path": item["path"],
                "sha256": item["sha256"],
                "rows": int(item["rows"]),
                "bytes": int(item["bytes"]),
            }
            for item in manifest["partitions"]
        ),
        key=lambda item: item["path"],
    )
    recorded_partitions = sorted(
        (
            {
                "path": item["path"],
                "sha256": item["sha256"],
                "rows": int(item["rows"]),
                "bytes": int(item["bytes"]),
            }
            for item in recorded_index.get("partitions", [])
        ),
        key=lambda item: item["path"],
    )
    if recorded_partitions != expected_partitions:
        raise GlobalReportError("refinement partitions do not match the committed index")
    if (report.get("method_versions") or {}).get("coarse") != manifest["method_version"]:
        raise GlobalReportError("refinement coarse method version differs from the index")
    versions = report.get("method_versions") or {}
    if versions.get("fine") != FINE_METHOD_VERSION:
        raise GlobalReportError(
            f"refinement fine method version {versions.get('fine')!r} is not "
            f"{FINE_METHOD_VERSION!r}"
        )
    if versions.get("link") != "coarse-refine-link-1.0.0":
        raise GlobalReportError("unexpected refinement link method version")

    selection = report.get("selection") or {}
    target = {str(key): float(value) for key, value in (selection.get("target") or {}).items()}
    weights = {str(key): float(value) for key, value in (selection.get("weights") or {}).items()}
    if set(target) - set(COARSE_FEATURE_BY_ID):
        raise GlobalReportError("refinement target contains an unknown coarse feature")
    selected_ids = list(selection.get("selected_candidate_ids") or [])

    links: list[dict[str, Any]] = []
    succeeded = failed = no_coverage = 0
    fine_observations = 0
    context_observations = 0
    for link in report.get("links", []):
        coarse = link.get("coarse") or {}
        candidate_id = coarse.get("candidate_id")
        candidate = repository.get(candidate_id) if candidate_id else None
        if candidate is None:
            raise GlobalReportError(f"refinement link candidate {candidate_id!r} is not indexed")
        if candidate["region_id"] != coarse.get("region_id"):
            raise GlobalReportError(f"refinement link {candidate_id} region differs from the index")
        for feature_id, column in FEATURE_COLUMNS.items():
            recorded = (coarse.get("features") or {}).get(feature_id)
            if recorded is None or abs(float(candidate[column]) - float(recorded)) > 1e-9:
                raise GlobalReportError(
                    f"refinement link {candidate_id} feature {feature_id} differs from the index"
                )
        distance = _coarse_distance(candidate, target, weights)
        if abs(distance - float(coarse["distance"])) > 1e-6:
            raise GlobalReportError(
                f"refinement link {candidate_id} coarse distance is not reproducible"
            )
        scores = link.get("scores") or {}
        if scores.get("coarse_and_fine_scores_interchangeable") is not False:
            raise GlobalReportError(
                "refinement report claims coarse and fine scores are interchangeable"
            )
        status = str(link.get("status"))
        if status == "succeeded":
            succeeded += 1
        elif status == "failed":
            failed += 1
        elif status == "no_coverage":
            no_coverage += 1
        else:
            raise GlobalReportError(f"refinement link {candidate_id} has status {status!r}")
        refined = link.get("refined") or {}
        features = refined.get("features") or {}
        fine: dict[str, Any] = {}
        if status == "succeeded":
            for key in (
                "slope_mean_deg",
                "slope_p90_deg",
                "terrain_relief_p95_p05_m",
                "flat_terrain_fraction",
            ):
                feature = features.get(key)
                if not feature:
                    raise GlobalReportError(
                        f"refinement link {candidate_id} is missing fine feature {key}"
                    )
                if feature.get("quality") != "verified":
                    raise GlobalReportError(
                        f"refinement link {candidate_id} fine feature {key} is not verified"
                    )
                if (
                    key != "flat_terrain_fraction"
                    and feature.get("method_version") != FINE_METHOD_VERSION
                ):
                    raise GlobalReportError(
                        f"refinement link {candidate_id} fine feature {key} method differs"
                    )
                fine[key] = _f(feature.get("value"), 6)
            fine_observations += len(features)
            context_observations += int(refined.get("context_observation_count", 0))
        links.append(
            {
                "coarse_candidate_id": candidate_id,
                "region_id": coarse["region_id"],
                "coarse_rank": int(coarse["coarse_rank"]),
                "coarse_rank_scope": coarse.get("coarse_rank_scope"),
                "coarse_distance": _f(coarse["distance"], 9),
                "coarse_slope_mean_deg": _f(coarse["features"]["coarse_slope_mean_deg"]),
                "coarse_slope_p90_deg": _f(coarse["features"]["coarse_slope_p90_deg"]),
                "coarse_flat_fraction_lt5deg": _f(
                    coarse["features"]["coarse_flat_fraction_lt5deg"]
                ),
                "status": status,
                "refined_area_id": refined.get("area_id"),
                "refined_checksum": refined.get("checksum"),
                "fine_slope_mean_deg": fine.get("slope_mean_deg"),
                "fine_slope_p90_deg": fine.get("slope_p90_deg"),
                "fine_terrain_relief_p95_p05_m": fine.get("terrain_relief_p95_p05_m"),
                "fine_flat_terrain_fraction": fine.get("flat_terrain_fraction"),
                "fine_valid_fraction": _f(refined.get("valid_fraction")),
                "error": link.get("error"),
            }
        )

    if [link["coarse_candidate_id"] for link in links] != selected_ids:
        raise GlobalReportError("refinement links do not match the selected candidate ids")
    counts = report.get("counts") or {}
    recorded_counts = {
        "coarse_indexed": int(counts.get("coarse_indexed", -1)),
        "coarse_eligible": int(counts.get("coarse_eligible", -1)),
        "coarse_selected": int(counts.get("coarse_selected", -1)),
        "links": int(counts.get("links", -1)),
        "refined_succeeded": int(counts.get("refined_succeeded", -1)),
        "refined_failed": int(counts.get("refined_failed", -1)),
        "refined_no_coverage": int(counts.get("refined_no_coverage", -1)),
        "fine_feature_observations": int(counts.get("fine_feature_observations", -1)),
        "context_observations": int(counts.get("context_observations", -1)),
        "failures": int(counts.get("failures", -1)),
    }
    actual_counts = {
        "coarse_indexed": int(manifest["row_count"]),
        "coarse_eligible": int(selection.get("eligible_candidate_count", -1)),
        "coarse_selected": len(links),
        "links": len(links),
        "refined_succeeded": succeeded,
        "refined_failed": failed,
        "refined_no_coverage": no_coverage,
        "fine_feature_observations": fine_observations,
        "context_observations": context_observations,
        "failures": len(report.get("failures") or []),
    }
    if recorded_counts != actual_counts:
        raise GlobalReportError(
            f"refinement counts {recorded_counts} do not match verified links {actual_counts}"
        )

    provider_counts = {
        name: {key: int(value) for key, value in sorted(statuses.items())}
        for name, statuses in sorted((report.get("provider_counts") or {}).items())
    }
    for name, statuses in provider_counts.items():
        total = sum(
            statuses.get(key, 0) for key in ("verified", "failed", "no_coverage", "unknown")
        )
        if total != len(links):
            raise GlobalReportError(f"provider {name} status counts do not cover every link")

    datasets = [
        {
            key: item.get(key)
            for key in (
                "dataset_id",
                "title",
                "provider",
                "product_id",
                "license",
                "native_resolution_m",
            )
        }
        for item in report.get("datasets", [])
    ]
    run_metrics = report.get("run_metrics") or {}
    return {
        "evidence": {
            "path": path.relative_to(repo_root).as_posix(),
            "sha256": _sha256_file(path),
            "report_version": report["report_version"],
            "generated_at": report.get("generated_at"),
            "generated_by": report.get("generated_by"),
        },
        "coverage": report.get("coverage") or {},
        "selection": {
            "rule": selection.get("rule"),
            "rule_description": selection.get("rule_description"),
            "rank_scope": selection.get("rank_scope"),
            "limit": selection.get("limit"),
            "target": target,
            "weights": weights,
            "selected_candidate_ids": selected_ids,
        },
        "focus": report.get("focus") or {},
        "counts": recorded_counts,
        "provider_counts": provider_counts,
        "datasets": datasets,
        "run_metrics": {
            "elapsed_s": _f(run_metrics.get("elapsed_s"), 3),
            "max_rss_mb": _f(run_metrics.get("max_rss_mb"), 3),
        },
        "recall_estimate": report.get("recall_estimate") or {},
        "links": links,
        "comparison_note": (
            "Coarse 5 km / ~250 m values and fine 1.98 km / 90 m values are shown side by side "
            "but are not interchangeable. No fine rank, recall, precision, or sensitivity "
            "estimate is computed; the coarse rank is not a fine rank."
        ),
    }


def _markdown_cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _markdown_row(text: str, label: str) -> list[str]:
    for line in text.splitlines():
        if not line.lstrip().startswith("|"):
            continue
        cells = _markdown_cells(line)
        if cells and cells[0].strip("`") == label:
            return cells
    raise GlobalReportError(f"committed report has no table row labelled {label!r}")


def _cell_number(cell: str) -> float:
    return float(cell.replace(",", "").replace("MiB", "").strip())


def _cell_numbers(cell: str) -> list[float]:
    return [float(part.strip()) for part in cell.split("/")]


def load_db_measurements(path: Path) -> dict[str, Any]:
    """Transcribe committed DB capacity measurements without recomputing them.

    ``docs/DB_CAPACITY_REPORT.md`` is a human-readable measured report, not a machine
    artifact. The values below are copied verbatim from labelled table rows so the
    evaluation report can quote them; they are explicitly *not* re-measured here.
    """
    text = path.read_text(encoding="utf-8")
    rows_100k = {
        "build_seconds": _cell_number(_markdown_row(text, "Build seconds")[1]),
        "parquet_partitions": int(_cell_number(_markdown_row(text, "Parquet partitions")[1])),
        "parquet_bytes": int(_cell_number(_markdown_row(text, "Parquet bytes")[1])),
        "parquet_bytes_per_row": _cell_number(_markdown_row(text, "Parquet bytes/row")[1]),
        "verify_seconds": _cell_number(_markdown_row(text, "Verify seconds")[1]),
        "peak_rss_mib": _cell_number(_markdown_row(text, "Peak RSS (process)")[1]),
        "topk_p50_p95_p99_ms": _cell_numbers(_markdown_row(text, "Top-K p50 / p95 / p99 (ms)")[1]),
    }
    rows_1m = {
        "build_seconds": _cell_number(_markdown_row(text, "Build seconds")[2]),
        "parquet_partitions": int(_cell_number(_markdown_row(text, "Parquet partitions")[2])),
        "parquet_bytes": int(_cell_number(_markdown_row(text, "Parquet bytes")[2])),
        "parquet_bytes_per_row": _cell_number(_markdown_row(text, "Parquet bytes/row")[2]),
        "verify_seconds": _cell_number(_markdown_row(text, "Verify seconds")[2]),
        "peak_rss_mib": _cell_number(_markdown_row(text, "Peak RSS (process)")[2]),
        "topk_p50_p95_p99_ms": _cell_numbers(_markdown_row(text, "Top-K p50 / p95 / p99 (ms)")[2]),
    }
    return {
        "measurement_kind": "documented_measurement_not_recomputed",
        "source_doc": "docs/DB_CAPACITY_REPORT.md",
        "source_sha256": _sha256_file(path),
        "sqlite": {
            "file_bytes": int(_cell_number(_markdown_row(text, "File bytes")[1])),
            "integrity_check": _markdown_row(text, "PRAGMA integrity_check")[1].strip("`"),
        },
        "synthetic_coarse_benchmark": {
            "label": "synthetic_benchmark_not_real_data",
            "rows": {"100000": rows_100k, "1000000": rows_1m},
            "note": (
                "Synthetic rows repeat values and compress unusually well; timings are a "
                "software capacity bound on one workstation, not real global coverage or a "
                "production SLA."
            ),
        },
    }


def load_pilot_source(path: Path) -> dict[str, Any]:
    """Read the committed pilot source record embedded in the pilot document."""
    text = path.read_text(encoding="utf-8")
    blocks = re.findall(r"```json\n(.*?)\n```", text, flags=re.DOTALL)
    if not blocks:
        raise GlobalReportError(f"no committed JSON source record found in {path}")
    payload = json.loads(blocks[0])
    payload["_source_doc"] = path.name
    payload["_source_sha256"] = _sha256_file(path)
    return payload


def _region_rows(parquet_paths: list[str]) -> list[dict[str, Any]]:
    escaped = ", ".join("'" + path.replace("'", "''") + "'" for path in parquet_paths)
    connection = duckdb.connect()
    try:
        connection.execute(f"CREATE VIEW coarse AS SELECT * FROM read_parquet([{escaped}])")
        rows = connection.execute(
            "SELECT region_id, count(*) AS cells, "
            "min(west), min(south), max(east), max(north), sum(area_km2), "
            "min(slope_mean_deg), max(slope_mean_deg), "
            "min(slope_p90_deg), max(slope_p90_deg), "
            "min(flat_fraction), max(flat_fraction), "
            "min(valid_fraction), max(valid_fraction), "
            "count(*) FILTER (WHERE slope_mean_deg IS NULL), "
            "count(*) FILTER (WHERE slope_p90_deg IS NULL), "
            "count(*) FILTER (WHERE flat_fraction IS NULL) "
            "FROM coarse GROUP BY region_id ORDER BY region_id"
        ).fetchall()
        duplicate_ids = connection.execute(
            "SELECT count(*) - count(DISTINCT candidate_id) FROM coarse"
        ).fetchone()[0]
    finally:
        connection.close()
    if int(duplicate_ids):
        raise GlobalReportError(f"index contains {duplicate_ids} duplicate candidate ids")
    payload = []
    for row in rows:
        payload.append(
            {
                "region_id": row[0],
                "cells": int(row[1]),
                "bbox": {
                    "west": _f(row[2], 9),
                    "south": _f(row[3], 9),
                    "east": _f(row[4], 9),
                    "north": _f(row[5], 9),
                },
                "cell_footprint_km2": _f(row[6], 3),
                "slope_mean_deg": {"min": _f(row[7]), "max": _f(row[8])},
                "slope_p90_deg": {"min": _f(row[9]), "max": _f(row[10])},
                "flat_fraction": {"min": _f(row[11]), "max": _f(row[12])},
                "valid_fraction": {"min": _f(row[13]), "max": _f(row[14])},
                "null_feature_cells": {
                    "coarse_slope_mean_deg": int(row[15]),
                    "coarse_slope_p90_deg": int(row[16]),
                    "coarse_flat_fraction_lt5deg": int(row[17]),
                },
            }
        )
    return payload


def _query_payloads(repository: CoarseIndexRepository) -> list[dict[str, Any]]:
    payload = []
    for query in QUERY_EXAMPLES:
        result = repository.search(
            target=dict(query["target"]),
            weights=dict(query["weights"]),
            limit=5,
        )
        hits = []
        for row in result["results"]:
            hits.append(
                {
                    "rank": int(row["rank"]),
                    "candidate_id": row["candidate_id"],
                    "region_id": row["region_id"],
                    "distance": _f(row["distance"], 9),
                    "coarse_slope_mean_deg": _f(row["slope_mean_deg"]),
                    "coarse_slope_p90_deg": _f(row["slope_p90_deg"]),
                    "coarse_flat_fraction_lt5deg": _f(row["flat_fraction"]),
                    "valid_fraction": _f(row["valid_fraction"]),
                    "bbox": {
                        "west": _f(row["west"], 9),
                        "south": _f(row["south"], 9),
                        "east": _f(row["east"], 9),
                        "north": _f(row["north"], 9),
                    },
                }
            )
        excluded = int(result["excluded_candidate_count"])
        payload.append(
            {
                "query_id": query["query_id"],
                "label": query["label"],
                "target": {key: float(value) for key, value in query["target"].items()},
                "weights": {key: float(value) for key, value in query["weights"].items()},
                "distance_definition": (
                    "sqrt(sum(w_i * ((value_i - target_i) / scale_i)^2) / sum(w_i)) over "
                    "positive-weight coarse features; lower is closer and the value is an "
                    "engineering shortlist score, not a physical similarity percentage"
                ),
                "indexed_candidate_count": int(result["indexed_candidate_count"]),
                "eligible_candidate_count": int(result["eligible_candidate_count"]),
                "excluded_candidate_count": excluded,
                "exclusion_reason": (
                    "missing value for a positive-weight coarse feature"
                    if excluded
                    else "none: every indexed cell has all three coarse features"
                ),
                "results": hits,
            }
        )
    return payload


def _regions_csv(regions: list[dict[str, Any]], batch: dict[str, Any], *, source_url: str) -> str:
    header = [
        "region_id",
        "cells",
        "share_of_index",
        "bbox_west",
        "bbox_south",
        "bbox_east",
        "bbox_north",
        "cell_footprint_km2",
        "slope_mean_min_deg",
        "slope_mean_max_deg",
        "slope_p90_min_deg",
        "slope_p90_max_deg",
        "flat_fraction_min",
        "flat_fraction_max",
        "valid_fraction_min",
        "staged_window_sha256",
        "source_url",
        "license",
    ]
    total = sum(region["cells"] for region in regions)
    lines = [",".join(header)]
    for region in regions:
        staged = batch["inputs"][region["region_id"]]
        cells = [
            region["region_id"],
            str(region["cells"]),
            _fixed(region["cells"] / total),
            _fixed(region["bbox"]["west"]),
            _fixed(region["bbox"]["south"]),
            _fixed(region["bbox"]["east"]),
            _fixed(region["bbox"]["north"]),
            _fixed(region["cell_footprint_km2"], 3),
            _fixed(region["slope_mean_deg"]["min"]),
            _fixed(region["slope_mean_deg"]["max"]),
            _fixed(region["slope_p90_deg"]["min"]),
            _fixed(region["slope_p90_deg"]["max"]),
            _fixed(region["flat_fraction"]["min"]),
            _fixed(region["flat_fraction"]["max"]),
            _fixed(region["valid_fraction"]["min"]),
            staged["staged_window_sha256"],
            source_url,
            "CC BY 4.0",
        ]
        if any("," in value for value in cells):
            raise GlobalReportError("CSV field would require quoting; unexpected source metadata")
        lines.append(",".join(cells))
    return "\n".join(lines) + "\n"


def build_global_terrain_report(inputs: ReportInputs) -> dict[str, str]:
    """Return ``{relative_name: text}`` for the document and its small artifacts."""
    try:
        manifest = verify_index(inputs.index_root)
        repository = CoarseIndexRepository(inputs.index_root, verify=True)
    except IndexError_ as exc:
        raise GlobalReportError(f"coarse index verification failed: {exc}") from exc
    assert_not_global_claim(manifest)
    batch = load_batch_evidence(inputs.batch_dir, manifest, repo_root=inputs.repo_root)
    pilot = load_pilot_source(inputs.pilot_doc)
    db = load_db_measurements(inputs.db_report)

    resolved_root = resolve_index_root(inputs.index_root)
    manifest_sha256 = _sha256_file(resolved_root / "manifest.json")
    refinement = None
    if inputs.refine_json is not None:
        refinement = load_refinement_evidence(
            inputs.refine_json,
            manifest,
            repository,
            repo_root=inputs.repo_root,
            manifest_sha256=manifest_sha256,
        )

    if pilot.get("snapshot") != manifest["snapshot_id"]:
        raise GlobalReportError("pilot source record and index manifest snapshot ids differ")
    if (pilot.get("grid") or {}).get("method_version") != manifest["method_version"]:
        raise GlobalReportError("pilot source record and index method versions differ")
    if (pilot.get("grid") or {}).get("grid_version") != manifest["grid_version"]:
        raise GlobalReportError("pilot source record and index grid versions differ")
    source = pilot.get("source") or {}
    if str(source.get("license")) != "CC BY 4.0":
        raise GlobalReportError("pilot source licence is not the verified CC BY 4.0 record")
    metadata = source.get("metadata") or {}
    grid_regions = set((pilot.get("grid") or {}).get("regions", []))
    if grid_regions != set(batch["inputs"]):
        raise GlobalReportError("pilot grid regions and batch staging regions differ")
    for region_id, staged in batch["inputs"].items():
        recorded = metadata.get(region_id) or {}
        if recorded.get("sha256") != staged["staged_window_sha256"]:
            raise GlobalReportError(
                f"pilot source record window checksum for {region_id} differs from batch evidence"
            )
        if recorded.get("license") != source.get("license"):
            raise GlobalReportError(f"pilot source record licence differs for {region_id}")

    regions = _region_rows(repository.parquet_paths)
    if [region["region_id"] for region in regions] != sorted(manifest["coverage"]["regions"]):
        raise GlobalReportError("index regions do not match the manifest coverage regions")
    indexed_cells = sum(region["cells"] for region in regions)
    if indexed_cells != int(manifest["row_count"]):
        raise GlobalReportError("per-region cells do not sum to the manifest row_count")
    if indexed_cells != batch["cells"]:
        raise GlobalReportError("index cells and batch cells differ")

    queries = _query_payloads(repository)
    missing = {
        feature_id: sum(region["null_feature_cells"][feature_id] for region in regions)
        for feature_id in COARSE_FEATURE_BY_ID
    }
    denominator_cells = int(EARTH_LAND_AREA_KM2 / CELL_AREA_KM2)
    coverage_payload = {
        "report_version": REPORT_VERSION,
        "scope": {
            "global_claim": False,
            "label": manifest["coverage"]["label"],
            "statement": (
                "Three 0.4-degree Earth pilot windows only. Coverage is the verified indexed "
                "candidate set; it is not global, exhaustive, or a survey percentage."
            ),
            "body": "Earth",
            "regions_indexed": len(regions),
            "cells": indexed_cells,
            "arithmetic_denominator": {
                "earth_land_area_km2": EARTH_LAND_AREA_KM2,
                "cell_area_km2": CELL_AREA_KM2,
                "possible_cells_if_complete": denominator_cells,
                "indexed_share_percent": round(indexed_cells / denominator_cells * 100, 6),
                "note": (
                    "Arithmetic denominator only. It is not a measured coverage percentage and "
                    "must not be quoted as survey completeness."
                ),
            },
        },
        "versions": {
            key: manifest[key]
            for key in (
                "snapshot_id",
                "created_at",
                "index_version",
                "grid_version",
                "method_version",
                "parquet_schema_version",
            )
        },
        "coverage": {
            "label": manifest["coverage"]["label"],
            "bbox": {key: _f(value, 9) for key, value in manifest["coverage"]["bbox"].items()},
            "bbox_note": (
                "The bbox spans the disjoint pilot windows; it is a bounding box, not a claim "
                "of contiguous coverage."
            ),
            "regions": manifest["coverage"]["regions"],
            "method_version": manifest["method_version"],
            "grid_version": manifest["grid_version"],
            "grid_window_m": int((manifest["features"] or [{}])[0].get("grid_window_m", 5000)),
            "source_resolution_m": float(
                (manifest["features"] or [{}])[0].get("source_resolution_m", 250.0)
            ),
            "source_resolution_note": (
                "7.5 arc-second (~250 m) source pixels; there is no fixed metric source grid, "
                "so this is not a 90 m terrain product."
            ),
        },
        "counts": {
            "cells": indexed_cells,
            "regions": len(regions),
            "partitions": len(manifest["partitions"]),
            "distinct_candidate_ids": indexed_cells,
            "batch_rows": batch["cells"],
        },
        "per_region": regions,
        "exclusions": {
            "missing_feature_values": missing,
            "cells_with_any_missing_feature": sum(
                1
                for region in regions
                if any(value for value in region["null_feature_cells"].values())
            ),
            "query_excluded_cells": {
                query["query_id"]: query["excluded_candidate_count"] for query in queries
            },
            "query_excluded_note": (
                "Summing per-query exclusions would double-count cells; each query reports its "
                "own eligible/excluded counts."
            ),
            "batch_task_outcomes": batch["cell_counts"],
            "declared_region_exclusions": [],
            "unindexed_scope": (
                "Every Earth land cell outside the three committed 0.4-degree pilot windows is "
                "unindexed. It is reported as missing scope, never zero-filled."
            ),
            "source_coverage_limits": (
                "Geomorpho90m covers roughly 60°S–85°N; Antarctica is excluded at source. "
                "No coarse Moon/Mars reference is validated in this phase."
            ),
        },
        "checksums": {
            "manifest_sha256": manifest_sha256,
            "partitions": [
                {
                    "region_id": partition["region_id"],
                    "path": partition["path"],
                    "sha256": partition["sha256"],
                    "bytes": int(partition["bytes"]),
                    "rows": int(partition["rows"]),
                }
                for partition in sorted(manifest["partitions"], key=lambda item: item["path"])
            ],
            "batch_evidence": batch["checksums"],
            "pilot_source_doc": {
                "path": pilot["_source_doc"],
                "sha256": pilot["_source_sha256"],
            },
        },
    }

    topk_payload = {
        "report_version": REPORT_VERSION,
        "snapshot_id": manifest["snapshot_id"],
        "method_version": manifest["method_version"],
        "grid_version": manifest["grid_version"],
        "index_version": manifest["index_version"],
        "label": manifest["coverage"]["label"],
        "global_claim": False,
        "weights_note": (
            "Weights are explicit engineering weights chosen to demonstrate the documented "
            "distance definition; they are not calibrated scientific weights."
        ),
        "queries": queries,
    }

    performance_payload = {
        "report_version": REPORT_VERSION,
        "batch": {
            "measurement_kind": "measured_committed_run",
            "source_run": batch["run_id"],
            "source_mode": batch["source_mode"],
            "generated_at": batch["generated_at"],
            "tasks": batch["tasks"],
            "cells": batch["cells"],
            "workers": batch["workers"],
            "cells_per_task": batch["cells_per_task"],
            "cell_counts": batch["cell_counts"],
            "region_rows": batch["region_rows"],
            "task_duration_s": batch["task_duration_s"],
            "parquet_bytes": batch["parquet_bytes"],
            "analytic_peak_mb": batch["analytic_peak_mb"],
            "analytic_peak_kind": batch["analytic_peak_kind"],
            "artifact_verification": batch["verification"],
            "staged_inputs": batch["inputs"],
        },
        "db": db,
    }

    refinement_payload = (
        {"report_version": REPORT_VERSION, "available": True, **refinement}
        if refinement is not None
        else {
            "report_version": REPORT_VERSION,
            "available": False,
            "reason": (
                "No committed coarse-to-90 m refinement report was configured for this "
                "checkout, so no coarse-vs-refined comparison is reported."
            ),
            "comparison": None,
        }
    )

    artifact_text = {
        "coverage.json": _json_text(coverage_payload),
        "top-k-queries.json": _json_text(topk_payload),
        "regions.csv": _regions_csv(regions, batch, source_url=str(source.get("url"))),
        "performance.json": _json_text(performance_payload),
        "refinement.json": _json_text(refinement_payload),
    }
    artifact_checksums = [
        {
            "path": name,
            "bytes": len(text.encode("utf-8")),
            "sha256": _sha256_bytes(text.encode("utf-8")),
        }
        for name, text in sorted(artifact_text.items())
    ]
    artifact_text["checksums.json"] = _json_text(
        {
            "report_version": REPORT_VERSION,
            "artifacts": artifact_checksums,
        }
    )
    document = _render_document(
        manifest=manifest,
        manifest_sha256=manifest_sha256,
        batch=batch,
        pilot=pilot,
        db=db,
        regions=regions,
        queries=queries,
        refinement=refinement,
        artifact_checksums=artifact_checksums,
    )
    return {DOCUMENT_NAME: document, **artifact_text}


def _render_document(
    *,
    manifest: dict[str, Any],
    manifest_sha256: str,
    batch: dict[str, Any],
    pilot: dict[str, Any],
    db: dict[str, Any],
    regions: list[dict[str, Any]],
    queries: list[dict[str, Any]],
    refinement: dict[str, Any] | None,
    artifact_checksums: list[dict[str, Any]],
) -> str:
    source = pilot["source"]
    feature_rows = "\n".join(
        f"| `{feature.feature_id}` | {feature.unit} | {feature.scale:g} | "
        f"`{feature.method_version}` | {feature.spatial_support} |"
        for feature in COARSE_FEATURES
    )
    region_rows = "\n".join(
        f"| `{region['region_id']}` | {region['cells']} | "
        f"{region['bbox']['west']:.6f}, {region['bbox']['south']:.6f}, "
        f"{region['bbox']['east']:.6f}, {region['bbox']['north']:.6f} | "
        f"{region['cell_footprint_km2']:.1f} | "
        f"{region['slope_mean_deg']['min']:.3f}–{region['slope_mean_deg']['max']:.3f} | "
        f"{region['flat_fraction']['min']:.3f}–{region['flat_fraction']['max']:.3f} | "
        f"{region['valid_fraction']['min']:.3f} |"
        for region in regions
    )
    query_sections = []
    for query in queries:
        target = ", ".join(
            f"`{name}`={value:g} (w={query['weights'][name]:g})"
            for name, value in sorted(query["target"].items())
        )
        hit_rows = "\n".join(
            f"| {hit['rank']} | `{hit['candidate_id']}` | `{hit['region_id']}` | "
            f"{hit['distance']:.6f} | {hit['coarse_slope_mean_deg']:.3f} | "
            f"{hit['coarse_flat_fraction_lt5deg']:.3f} | {hit['valid_fraction']:.3f} |"
            for hit in query["results"]
        )
        query_sections.append(
            f"""### {query["query_id"]} — {query["label"]}

Target/weights: {target}. Eligible {query["eligible_candidate_count"]} of {query["indexed_candidate_count"]} indexed cells; excluded {query["excluded_candidate_count"]} ({query["exclusion_reason"]}).

| Rank | Candidate | Region | Distance | Mean slope (°) | Flat fraction | Valid fraction |
|---:|---|---|---:|---:|---:|---:|
{hit_rows}
"""
        )
    query_text = "\n".join(query_sections)
    missing = {
        feature_id: sum(region["null_feature_cells"][feature_id] for region in regions)
        for feature_id in COARSE_FEATURE_BY_ID
    }
    nulls = ", ".join(f"`{name}`={count}" for name, count in sorted(missing.items()))
    checksum_rows = "\n".join(
        f"| `docs/global-terrain-results/{item['path']}` | {item['bytes']} | `{item['sha256']}` |"
        for item in artifact_checksums
    )
    partition_rows = "\n".join(
        f"| `{partition['region_id']}` | {partition['rows']} | {partition['bytes']} | "
        f"`{partition['sha256']}` |"
        for partition in sorted(manifest["partitions"], key=lambda item: item["region_id"])
    )
    staged_rows = "\n".join(
        f"| `{region}` | {info['staged_window_bytes']} | "
        f"`{info['staged_window_sha256']}` | "
        f"{'yes' if info['matches_published_window'] else 'no'} |"
        for region, info in batch["inputs"].items()
    )
    denominator = int(EARTH_LAND_AREA_KM2 / CELL_AREA_KM2)
    share = batch["cells"] / denominator * 100
    duration = batch["task_duration_s"]
    db_rows = db["synthetic_coarse_benchmark"]["rows"]
    partition_footprint = sum(region["cell_footprint_km2"] for region in regions)
    bbox = manifest["coverage"]["bbox"]
    refinement_section = _render_refinement_section(refinement)
    if refinement is None:
        refinement_limit = (
            "- No coarse-vs-refined overlap/sensitivity evidence exists in this checkout."
        )
        refinement_input = "| Refinement evidence | not configured | — |"
    else:
        refinement_limit = (
            "- The refinement pilot covers only "
            f"{refinement['counts']['coarse_selected']} of "
            f"{refinement['counts']['coarse_indexed']} coarse cells (region-stratified top "
            "hits) and yields no recall, precision, overlap, or sensitivity estimate."
        )
        refinement_input = (
            f"| Refinement evidence | `{refinement['evidence']['path']}` "
            f"(`{refinement['evidence']['report_version']}`) | "
            f"`{refinement['evidence']['sha256']}` |"
        )
    return f"""# Global terrain evaluation — three-region pilot evidence, not global coverage

Status: **source-grounded pilot evidence, generated offline from committed artefacts.**
This is Issue #39's reproducible evaluation report. It is **not** a global scan, not a
complete survey, and not a validated scientific score.

- Report version: `{REPORT_VERSION}`
- Snapshot: `{manifest["snapshot_id"]}`
- Method: `{manifest["method_version"]}` · grid `{manifest["grid_version"]}` ·
  index `{manifest["index_version"]}` · Parquet `{manifest["parquet_schema_version"]}`
- Generated by: `uv run python scripts/report_global_terrain.py`
  (verify with `uv run python scripts/report_global_terrain.py --check`)

## 1. Scope refusal statement

`global_claim = false`. The committed index contains **{batch["cells"]} five-kilometre
cells in {len(regions)} pilot regions**
({", ".join("`" + region["region_id"] + "`" for region in regions)}). That is
{share:.6f}% of an *arithmetic* `{denominator:,}`-cell denominator (Earth land
{EARTH_LAND_AREA_KM2:,.0f} km² ÷ 25 km² per cell). The denominator is **not** a measured
coverage denominator, and the ratio must never be quoted as survey completeness.

Every Earth land cell outside those three 0.4° windows is unindexed. Antarctica is
excluded at source. This report refuses to label the pilot "global", "worldwide", or
"exhaustive"; `assert_not_global_claim` fails closed if a manifest label claims otherwise
without machine-checkable completeness evidence.

## 2. Inputs, versions, and checksums

| Input | Path | SHA-256 |
|---|---|---|
| Index manifest | `{manifest.get("snapshot_root", "pipelines/coarse/real-pilot/index/snapshots/" + manifest["snapshot_id"])}/manifest.json` | `{manifest_sha256}` |
| Batch evidence | `{batch["evidence_path"]}` report + checkpoint + {batch["tasks"]} task sidecars | each file pinned in `coverage.json` → `checksums.batch_evidence` |
| Pilot source record | `{pilot["_source_doc"]}` embedded JSON block | `{pilot["_source_sha256"]}` |
| DB capacity report | `{db["source_doc"]}` | `{db["source_sha256"]}` |
{refinement_input}

Verified partition checksums (re-hashed by `verify_index` before this report renders):

| Region | Rows | Bytes | Parquet SHA-256 |
|---|---:|---:|---|
{partition_rows}

Committed batch evidence for the same rows (Parquet outputs are not committed; their
recorded hashes are):

| Region | Staged window bytes | Staged window SHA-256 | Matches published pilot record |
|---|---:|---|---|
{staged_rows}

The batch (`{batch["run_id"]}`, `source_mode=real`, {batch["tasks"]}/{batch["tasks"]} tasks succeeded, verified {batch["verification"]["verified_tasks"]}/{batch["verification"]["terminal_tasks"]}) produced {batch["cells"]} rows with per-region counts matching the committed index. Per-task row counts and checksums agree across `report.json`, `checkpoint.json`, and each `task.json`. The batch Parquet bytes differ from the index partition bytes only because the batch adds a `batch_partition` QA flag; the measurement columns are unchanged (Issue #32 record).

## 3. Coverage counts and regions

| Region | Cells | WGS84 bbox (W, S, E, N) | Cell footprint km² | Mean slope range (°) | Flat fraction range | Min valid fraction |
|---|---:|---|---:|---|---|---:|
{region_rows}
| **Total** | **{batch["cells"]}** | bbox union `{bbox["west"]:.6f}, {bbox["south"]:.6f}, {bbox["east"]:.6f}, {bbox["north"]:.6f}` | **{partition_footprint:.1f}** | — | — | — |

The bbox union spans disjoint windows; it is a bounding box, not contiguous coverage.
Cells are 5 km × 5 km projected windows (UTM per region) aggregating 7.5 arc-second
(~250 m) slope pixels. Candidate identity is content-derived; region labels are metadata.

## 4. Missing values, exclusions, and no-coverage

- Missing coarse feature values (all cells): {nulls}.
- Cells excluded from a query due to a missing positive-weight feature: see each query
  section. A valid zero stays a value; only null/non-finite values are excluded.
- Batch outcomes: `{json.dumps(batch["cell_counts"], sort_keys=True)}` — no failed,
  invalid, or `no_coverage` cells. Missing staged input would fail a task rather than be
  reported as coverage.
- Declared region exclusions in the index manifest: none. Antarctica is outside the source
  product's coverage (~60°S–85°N) and outside this index.
- `no_coverage` here means "not indexed by this pilot", never "measured as zero".

## 5. Method contract (coarse 250 m only)

| Feature | Unit | Scale | Method version | Spatial support |
|---|---|---:|---|---|
{feature_rows}

All three features carry `{COARSE_METHOD_VERSION}`. They are coarse shortlist features and
are never recorded under the fine 90 m `{FINE_METHOD_VERSION}` IDs.

## 6. Top-K query examples (with targets and weights)

These queries run against the committed index through the same DuckDB search path as
`POST /api/v1/global/search`. Distances are engineering shortlist scores, not physical
similarity percentages. Weights are explicit demonstration weights, not calibrated science.

{query_text}
## 7. Measured batch performance

Source: committed run `{batch["run_id"]}` (`report.json`, `checkpoint.json`, task sidecars).

| Metric | Observed |
|---|---:|
| Tasks / cells | {batch["tasks"]} / {batch["cells"]} |
| Region rows | `{json.dumps(batch["region_rows"], sort_keys=True)}` |
| Workers / cells per task | {batch["workers"]} / {batch["cells_per_task"]} |
| Task duration sum / mean / median / max (s) | {duration["sum"]} / {duration["mean"]} / {duration["median"]} / {duration["max"]} |
| Parquet bytes total / per cell | {batch["parquet_bytes"]["total"]:,} / {batch["parquet_bytes"]["per_cell"]} |
| Analytic peak memory bound (MB, excludes GDAL) | {batch["analytic_peak_mb"]} |
| Artifact verification | `ok={batch["verification"]["ok"]}`, verified {batch["verification"]["verified_tasks"]}/{batch["verification"]["terminal_tasks"]}, orphaned {len(batch["verification"]["orphaned_artifacts"])} |

The committed human summary in `docs/GEOMORPHO_BATCH_PILOT.md` also records wall time and
peak RSS; those values are quoted there, not re-measured by this report.

## 8. Measured DB / index capacity

Source: `{db["source_doc"]}` ({db["measurement_kind"]}). These are **synthetic** software
measurements (`{db["synthetic_coarse_benchmark"]["label"]}`), not real coverage and not a
production SLA. SQLite file bytes: {db["sqlite"]["file_bytes"]:,};
`PRAGMA integrity_check={db["sqlite"]["integrity_check"]}`.

| Metric | 100,000 synthetic rows | 1,000,000 synthetic rows |
|---|---:|---:|
| Build seconds | {db_rows["100000"]["build_seconds"]} | {db_rows["1000000"]["build_seconds"]} |
| Parquet partitions | {db_rows["100000"]["parquet_partitions"]} | {db_rows["1000000"]["parquet_partitions"]} |
| Parquet bytes | {db_rows["100000"]["parquet_bytes"]:,} | {db_rows["1000000"]["parquet_bytes"]:,} |
| Parquet bytes/row | {db_rows["100000"]["parquet_bytes_per_row"]} | {db_rows["1000000"]["parquet_bytes_per_row"]} |
| Verify seconds | {db_rows["100000"]["verify_seconds"]} | {db_rows["1000000"]["verify_seconds"]} |
| Peak RSS (MiB) | {db_rows["100000"]["peak_rss_mib"]:.2f} | {db_rows["1000000"]["peak_rss_mib"]:.2f} |
| Top-K p50 / p95 / p99 (ms) | {" / ".join(f"{value:.3f}" for value in db_rows["100000"]["topk_p50_p95_p99_ms"])} | {" / ".join(f"{value:.3f}" for value in db_rows["1000000"]["topk_p50_p95_p99_ms"])} |

Synthetic values repeat and compress unusually well; real Geomorpho250 entropy will differ.
The 168-cell pilot has no per-1000-window production timing.

{refinement_section}
## 10. Source, licence, and attribution

- Dataset: Geomorpho90m slope, 7.5 arc-second (~250 m), PANGAEA object
  `dtm_slope_merit.dem_m_250m_s0..0cm_2018_v1.0.tif`.
- Citation: {source["citation"]}.
- Licence: {source["license"]}.
- Source URL: {source["url"]}.
- Access: {source["access"]}.
- Native resolution: 0.002083333333° (~250 m at the equator), EPSG:4326, int16 with
  `Scale=0.01`, `Offset=0`, nodata `-32768`.
- Attribution is required under CC BY 4.0; re-distribution of the source raster is not part
  of this repository (only bounded pilot windows were range-read and their hashes are
  recorded).

## 11. Reproduction

All commands are offline; they read committed files only. No network, no source download,
no provider call, and no synthetic global claim. The committed refinement report is an input
as well; it is not re-run here.

```bash
uv sync
uv run python scripts/report_global_terrain.py --check   # verify committed report
uv run python scripts/report_global_terrain.py           # regenerate document + artifacts
uv run pytest pipelines/tests/test_global_terrain_report.py -q
```

Optional batch re-execution (needs staged windows, therefore network opt-in; not part of
CI and not required to reproduce this report):

```bash
uv run python scripts/build_geomorpho_real_pilot.py
uv run python scripts/run_geomorpho_batch.py \\
  --run-id geomorpho-pilot-real-20261115 --workers 2 --cells-per-task 16 \\
  --cache var/cache/geomorpho-pilot --out var/geomorpho-batch
```

Regenerating the batch changes wall-clock timings; the committed batch evidence is the
pinned input for this report. `--check` compares the rendered document and artifacts
byte-for-byte with the committed files and exits non-zero on any drift.

Committed artifact digests:

| Artifact | Bytes | SHA-256 |
|---|---:|---|
{checksum_rows}

## 12. Limitations and remaining blockers

- Three 0.4° pilot regions, {batch["cells"]} cells; not global coverage, not an exhaustive
  survey, and not a base-site recommendation.
- 7.5 arc-second source pixels are ~250 m only near the equator; there is no fixed metric
  source grid.
- 5 km aggregation and bilinear reprojection are method-specific, not 90 m terrain metrics.
{refinement_limit}
- No real coarse Moon/Mars reference is validated; any refined windows are not promoted into
  the seedable index, snapshot, or user AOIs.
- DB capacity numbers are synthetic and measured on one workstation; the pilot itself has
  no production-scale timing.
- R2/remote object bytes remain unknown without credentials and are never zero-filled.
"""


def _render_refinement_section(refinement: dict[str, Any] | None) -> str:
    if refinement is None:
        return f"""## 9. Coarse 250 m vs refined 90 m — evidence unavailable

**No coarse-vs-refined comparison is available, so none is reported.** No committed
coarse-to-90 m refinement report was configured. The committed coarse pilot contains only
`{COARSE_METHOD_VERSION}` rows for ~250 m-derived 5 km windows; there are no paired refined
rows produced with `{FINE_METHOD_VERSION}`, no refined similarity values, and no committed
overlap/sensitivity measurement between the two supports.

- Coarse and fine methods are deliberately disjoint; `assert_comparable` and
  `reject_coarse_fine_comparison` fail closed.
- `POST /api/v1/global/refine-plan` can only *plan* 1.98 km fine windows; it does not
  fetch a DEM or measure them.
- Any coarse/fine difference table must wait for an explicit, bounded 90 m refinement run.
"""
    evidence = refinement["evidence"]
    counts = refinement["counts"]
    metrics = refinement["run_metrics"]
    focus = refinement["focus"]
    selection = refinement["selection"]
    link_rows = "\n".join(
        f"| `{link['coarse_candidate_id']}` | `{link['region_id']}` | {link['coarse_rank']} | "
        f"{link['coarse_distance']:.6f} | {link['coarse_slope_mean_deg']:.3f} | "
        f"`{link['refined_area_id']}` | {link['status']} | "
        f"{_fmt(link['fine_slope_mean_deg'], '.3f')} | "
        f"{_fmt(link['fine_slope_p90_deg'], '.3f')} | "
        f"{_fmt(link['fine_terrain_relief_p95_p05_m'], '.2f')} | "
        f"{_fmt(link['fine_valid_fraction'], '.3f')} |"
        for link in refinement["links"]
    )
    provider_text = "; ".join(
        f"`{name}` verified {statuses.get('verified', 0)}, failed {statuses.get('failed', 0)}, "
        f"no coverage {statuses.get('no_coverage', 0)}"
        for name, statuses in refinement["provider_counts"].items()
    )
    dataset_text = "\n".join(
        f"- `{item['dataset_id']}` — {item.get('provider')}, {item.get('product_id')}; "
        f"licence: {item.get('license')}"
        for item in refinement["datasets"]
    )
    return f"""## 9. Coarse 250 m vs refined 90 m — bounded evidence, not a sensitivity study

Evidence: `{evidence["path"]}` (`{evidence["report_version"]}`, SHA-256
`{evidence["sha256"]}`), generated {evidence["generated_at"]} by
`{evidence["generated_by"]}`; human-readable report: `docs/COARSE_REFINE_PILOT.md`. The
refinement was verified against the same snapshot, manifest digest, partition checksums,
candidate ids, coarse values, and coarse distances as this report; it is not a separate or
synthetic index.

Selection: {selection["rule_description"]}

Target `{json.dumps(selection["target"], sort_keys=True)}`, weights
`{json.dumps(selection["weights"], sort_keys=True)}`; focus window {focus.get("window_m")} m,
placement `{focus.get("placement_rule")}`, fine analysis {focus.get("analysis_resolution_m")} m.

| Coarse candidate | Region | Coarse rank | Coarse distance | Coarse mean slope (°) | Refined area | Status | Fine mean slope (°) | Fine p90 (°) | Fine relief (m) | Fine valid fraction |
|---|---|---:|---:|---:|---|---|---:|---:|---:|---:|
{link_rows}

Counts: {counts["coarse_selected"]} coarse hits selected from {counts["coarse_indexed"]}
indexed cells; {counts["refined_succeeded"]} refined, {counts["refined_failed"]} failed,
{counts["refined_no_coverage"]} no coverage; {counts["fine_feature_observations"]} fine
terrain observations and {counts["context_observations"]} WorldCover context observations.
Provider status: {provider_text}.

Measured run: {metrics["elapsed_s"]} s elapsed, {metrics["max_rss_mb"]} MiB max RSS (from
the committed refinement report, not re-run by this report).

{dataset_text}

**What this does not establish.** The two blocks are shown side by side but are *not*
interchangeable: coarse uses a 5 km window over ~250 m source pixels, fine uses a 1.98 km
window over 90 m cells, and `coarse_and_fine_scores_interchangeable=false`. There is no fine
target, so the fine block is unranked. Only {counts["coarse_selected"]} of
{counts["coarse_indexed"]} cells were refined, selected per region (not the global top 3),
and the focus window is placed at the coarse window centre. No recall, precision, overlap,
or method-sensitivity estimate is available, and no result here is a global or exhaustive
claim. Copernicus GLO-90 is a DSM that may include vegetation/buildings; ESA WorldCover is
context only and never enters the similarity distance.
"""


def write_global_terrain_report(
    inputs: ReportInputs,
    *,
    document_path: Path,
    artifacts_dir: Path,
) -> list[Path]:
    rendered = build_global_terrain_report(inputs)
    written: list[Path] = []
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    for name in ARTIFACT_NAMES:
        path = artifacts_dir / name
        path.write_text(rendered[name], encoding="utf-8")
        written.append(path)
    document_path.parent.mkdir(parents=True, exist_ok=True)
    document_path.write_text(rendered[DOCUMENT_NAME], encoding="utf-8")
    written.append(document_path)
    return written


def check_global_terrain_report(
    inputs: ReportInputs,
    *,
    document_path: Path,
    artifacts_dir: Path,
) -> list[str]:
    rendered = build_global_terrain_report(inputs)
    problems: list[str] = []
    expected = {document_path: rendered[DOCUMENT_NAME]}
    for name in ARTIFACT_NAMES:
        expected[artifacts_dir / name] = rendered[name]
    for path, text in expected.items():
        if not path.is_file():
            problems.append(f"missing committed file: {path}")
            continue
        actual = path.read_text(encoding="utf-8")
        if actual != text:
            problems.append(f"committed file is stale: {path}")
    return problems
