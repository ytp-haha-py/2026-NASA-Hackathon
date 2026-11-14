"""Create a portable, checksummed release from a completed acquisition run."""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from analog_pipelines.acquisition.models import (
    AcquisitionPlan,
    TaskCheckpoint,
    file_sha256,
    write_json,
)
from analog_pipelines.acquisition.reporting import collect_run

RELEASE_VERSION = "acquisition-release-1.0.0"


def build_release(
    *,
    plan_path: str | Path,
    run_dir: str | Path,
    output_dir: str | Path,
    include_raw: bool = True,
) -> dict[str, Any]:
    plan = AcquisitionPlan.model_validate_json(Path(plan_path).read_text(encoding="utf-8"))
    source_run = Path(run_dir)
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    raw_dir = target / "raw"
    if include_raw:
        raw_dir.mkdir(parents=True, exist_ok=True)

    observations_lines: list[str] = []
    for path in sorted((source_run / "observations").glob("*.jsonl")):
        observations_lines.extend(
            line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
        )
    observations_path = target / "observations.jsonl"
    observations_path.write_text("\n".join(observations_lines) + "\n", encoding="utf-8")

    task_by_id = {task.task_id: task for task in plan.tasks}
    assets: list[dict[str, Any]] = []
    raw_entries: list[dict[str, Any]] = []
    for cp_path in sorted((source_run / "checkpoints").glob("*.json")):
        checkpoint = TaskCheckpoint.model_validate_json(cp_path.read_text(encoding="utf-8"))
        task = task_by_id.get(checkpoint.task_id)
        if checkpoint.status != "succeeded" or checkpoint.asset is None or task is None:
            continue
        source = Path(checkpoint.asset.path)
        rel_uri = f"source://{task.provider_id}/{task.task_id}"
        if include_raw and source.is_file():
            destination = raw_dir / f"{task.task_id}.json"
            shutil.copyfile(source, destination)
            rel_uri = f"raw/{destination.name}"
            raw_entries.append(
                {
                    "task_id": task.task_id,
                    "path": rel_uri,
                    "sha256": file_sha256(destination),
                    "bytes": destination.stat().st_size,
                }
            )
        assets.append(
            {
                "asset_id": f"{task.provider_id}:{task.area.area_id}:{task.task_id}",
                "dataset_id": task.dataset_id or task.source_id,
                "uri": rel_uri,
                "checksum_algorithm": "sha256",
                "checksum": checkpoint.asset.sha256,
                "checksum_scope": "source",
                "bytes": checkpoint.asset.bytes,
                "fetched_at": checkpoint.asset.fetched_at,
                "redistribution_allowed": "unknown",
                "source_endpoint": task.endpoint,
            }
        )
    write_json(target / "assets.json", assets)
    write_json(target / "plan.json", plan.model_dump(mode="json"))

    report = collect_run(source_run)
    manifest = {
        "release_version": RELEASE_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "scope": plan.scope,
        "plan_sha256": file_sha256(target / "plan.json"),
        "observations_sha256": file_sha256(observations_path),
        "observation_count": len(observations_lines),
        "asset_count": len(assets),
        "tasks": [
            {
                "task_id": task.task_id,
                "source_id": task.source_id,
                "provider_id": task.provider_id,
                "body": task.body,
                "dimension": task.dimension,
                "area_id": task.area.area_id,
                "endpoint": task.endpoint,
            }
            for task in plan.tasks
        ],
        "raw": raw_entries,
        "report": report["observations"],
        "notes": [
            "Portable release of a bounded pilot, not a global survey.",
            "Context-only and evidence-only observations are not scored similarity features.",
            "NASA POWER values are coarse model/reanalysis context, not local 1.98 km measurements.",
        ],
    }
    write_json(target / "manifest.json", manifest)
    (target / "REPORT.md").write_text(
        "\n".join(
            [
                "# Acquisition release",
                "",
                f"- Release version: `{RELEASE_VERSION}`",
                f"- Scope: `{plan.scope}`",
                f"- Observations: {len(observations_lines)}",
                f"- Assets: {len(assets)}",
                f"- Tasks: {len(plan.tasks)}",
                f"- By dimension: {report['observations']['by_dimension']}",
                f"- By body: {report['observations']['by_body']}",
                f"- By state: {report['observations']['by_state']}",
                f"- Areas: {', '.join(report['observations']['areas']) or 'none'}",
                "",
                report["scope_note"],
                "",
            ]
        ),
        encoding="utf-8",
    )
    return manifest
