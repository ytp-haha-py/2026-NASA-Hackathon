"""Create a portable, checksummed release from a completed acquisition run."""

from __future__ import annotations

import json
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


class ReleaseIntegrityError(ValueError):
    """A committed or generated release does not match its own checksums."""


def verify_release_integrity(release_dir: str | Path) -> dict[str, Any]:
    """Verify a release's observation checksum, counts and copied raw assets.

    This is intentionally offline and read-only. Missing checksums are accepted
    only for older manifests that did not record them; a recorded checksum that
    does not match is always an error.
    """
    root = Path(release_dir)
    manifest_path = root / "manifest.json"
    observations_path = root / "observations.jsonl"
    if not manifest_path.is_file():
        raise ReleaseIntegrityError(f"release has no manifest.json: {root}")
    if not observations_path.is_file():
        raise ReleaseIntegrityError(f"release has no observations.jsonl: {root}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    computed = file_sha256(observations_path)
    expected = manifest.get("observations_sha256")
    if expected and computed != expected:
        raise ReleaseIntegrityError(
            f"observations checksum mismatch: manifest={expected} actual={computed}"
        )
    line_count = sum(
        1 for line in observations_path.read_text(encoding="utf-8").splitlines() if line.strip()
    )
    expected_count = manifest.get("observation_count")
    if expected_count is not None and line_count != int(expected_count):
        raise ReleaseIntegrityError(
            f"observation count mismatch: manifest={expected_count} actual={line_count}"
        )
    verified_raw = 0
    missing_raw: list[str] = []
    for entry in manifest.get("raw", []):
        path = root / str(entry.get("path", ""))
        if not path.is_file():
            # Raster binaries are gitignored by repository policy and may be absent from a
            # committed release. Their recorded checksum is verified whenever bytes exist.
            missing_raw.append(str(entry.get("path")))
            continue
        if file_sha256(path) != entry.get("sha256"):
            raise ReleaseIntegrityError(
                f"manifest raw asset checksum mismatch: {entry.get('path')}"
            )
        verified_raw += 1
    verified_assets = 0
    assets_path = root / "assets.json"
    if assets_path.is_file():
        for asset in json.loads(assets_path.read_text(encoding="utf-8")):
            uri = str(asset.get("uri", ""))
            if "://" in uri or not uri:
                continue
            path = root / uri
            if not path.is_file():
                continue
            checksum = asset.get("checksum")
            if asset.get("checksum_algorithm") == "sha256" and checksum:
                if file_sha256(path) != checksum:
                    raise ReleaseIntegrityError(f"asset checksum mismatch: {uri}")
                verified_assets += 1
    verified_files: list[str] = []
    for name, metadata in manifest.get("files", {}).items():
        path = root / str(name)
        if not path.is_file():
            raise ReleaseIntegrityError(f"declared release file is missing: {name}")
        expected_file_checksum = metadata.get("sha256") if isinstance(metadata, dict) else None
        if expected_file_checksum and file_sha256(path) != expected_file_checksum:
            raise ReleaseIntegrityError(f"release file checksum mismatch: {name}")
        verified_files.append(str(name))
    return {
        "observations_sha256": computed,
        "observations": line_count,
        "verified_raw": verified_raw,
        "missing_raw": missing_raw,
        "verified_assets": verified_assets,
        "verified_files": sorted(verified_files),
    }


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
            destination = raw_dir / f"{task.task_id}{task.output_suffix}"
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
