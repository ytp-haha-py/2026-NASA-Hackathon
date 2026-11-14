"""Bounded, checkpointed execution of acquisition plans."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from analog_pipelines.acquisition.models import (
    AcquisitionPlan,
    AcquisitionTask,
    LocalAsset,
    TaskCheckpoint,
    ValidationIssue,
    ValidationReport,
    file_sha256,
    write_json,
)
from analog_pipelines.acquisition.providers import SourceProvider, default_providers
from analog_pipelines.typed_contracts import SourceAssetV2


def _load_plan(path: Path) -> AcquisitionPlan:
    return AcquisitionPlan.model_validate_json(path.read_text(encoding="utf-8"))


def _checkpoint_path(run_dir: Path, task: AcquisitionTask) -> Path:
    return run_dir / "checkpoints" / f"{task.task_id}.json"


def _observations_path(run_dir: Path, task: AcquisitionTask) -> Path:
    return run_dir / "observations" / f"{task.task_id}.jsonl"


def _reusable(checkpoint: TaskCheckpoint, task: AcquisitionTask, run_dir: Path) -> bool:
    if checkpoint.status != "succeeded" or checkpoint.fingerprint != task.fingerprint():
        return False
    if checkpoint.asset is None or checkpoint.observations_path is None:
        return False
    asset_path = Path(checkpoint.asset.path)
    if not asset_path.is_file() or file_sha256(asset_path) != checkpoint.asset.sha256:
        return False
    return Path(checkpoint.observations_path).is_file()


def _asset_record(task: AcquisitionTask, asset: LocalAsset) -> SourceAssetV2:
    return SourceAssetV2(
        asset_id=f"{task.provider_id}:{task.area.area_id}:{task.task_id}",
        dataset_id=task.dataset_id or task.source_id,
        uri=asset.path,
        checksum_algorithm="sha256",
        checksum=asset.sha256,
        checksum_scope="source",
        fetched_at=asset.fetched_at,
        bytes=asset.bytes,
        redistribution_allowed="unknown",
    )


def _execute_one(
    task: AcquisitionTask,
    *,
    run_dir: Path,
    cache_dir: Path,
    provider: SourceProvider,
    retry_failed: bool,
) -> TaskCheckpoint:
    cp_path = _checkpoint_path(run_dir, task)
    fingerprint = task.fingerprint()
    if cp_path.is_file():
        prior = TaskCheckpoint.model_validate_json(cp_path.read_text(encoding="utf-8"))
        if _reusable(prior, task, run_dir):
            return prior
        if prior.status == "failed" and not retry_failed:
            return prior
    checkpoint = TaskCheckpoint(
        task_id=task.task_id,
        fingerprint=fingerprint,
        status="pending",
    )
    write_json(cp_path, checkpoint.model_dump(mode="json"))
    try:
        asset = provider.acquire(task, cache_dir)
        observations = provider.process(asset, task)
        validation = provider.validate(observations)
        if not validation.ok:
            raise RuntimeError(
                "validation failed: "
                + "; ".join(
                    issue.message for issue in validation.issues if issue.severity == "error"
                )
            )
        obs_path = _observations_path(run_dir, task)
        obs_path.parent.mkdir(parents=True, exist_ok=True)
        obs_path.write_text(
            "".join(
                json.dumps(observation.model_dump(mode="json"), sort_keys=True) + "\n"
                for observation in observations
            ),
            encoding="utf-8",
        )
        asset_payload = _asset_record(task, asset).model_dump(mode="json")
        write_json(run_dir / "assets" / f"{task.task_id}.json", asset_payload)
        checkpoint.status = "succeeded"
        checkpoint.asset = asset
        checkpoint.observations_path = str(obs_path)
        checkpoint.error = None
    except Exception as exc:  # noqa: BLE001 - per-task failure is recorded, not hidden
        checkpoint.status = "failed"
        checkpoint.error = f"{type(exc).__name__}: {exc}"
    write_json(cp_path, checkpoint.model_dump(mode="json"))
    return checkpoint


def run_plan(
    plan_path: str | Path,
    *,
    run_dir: str | Path,
    cache_dir: str | Path,
    workers: int = 2,
    resume: bool = True,
    retry_failed: bool = False,
    providers: dict[str, SourceProvider] | None = None,
) -> dict[str, Any]:
    if not 1 <= workers <= 8:
        raise ValueError("workers must be within 1..8")
    plan = _load_plan(Path(plan_path))
    registry = providers or default_providers()
    unknown = sorted({task.provider_id for task in plan.tasks} - set(registry))
    if unknown:
        raise ValueError(f"plan uses unavailable provider(s): {unknown}")
    target = Path(run_dir)
    target.mkdir(parents=True, exist_ok=True)
    cache = Path(cache_dir)
    counts = {"succeeded": 0, "failed": 0, "skipped": 0, "pending": 0}
    if not resume:
        for path in (target / "checkpoints").glob("*.json"):
            path.unlink()
    pending: list[AcquisitionTask] = []
    for task in plan.tasks:
        cp_path = _checkpoint_path(target, task)
        if cp_path.is_file():
            prior = TaskCheckpoint.model_validate_json(cp_path.read_text(encoding="utf-8"))
            if _reusable(prior, task, target):
                counts["skipped"] += 1
                continue
        pending.append(task)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(
                _execute_one,
                task,
                run_dir=target,
                cache_dir=cache,
                provider=registry[task.provider_id],
                retry_failed=retry_failed,
            ): task
            for task in pending
        }
        for future in as_completed(futures):
            checkpoint = future.result()
            counts[checkpoint.status if checkpoint.status in counts else "failed"] += 1
    summary = {
        "plan": str(Path(plan_path)),
        "run_dir": str(target),
        "cache_dir": str(cache),
        "task_count": len(plan.tasks),
        "counts": counts,
        "validated": counts["failed"] == 0,
    }
    write_json(target / "summary.json", summary)
    return summary


def validate_run(run_dir: str | Path) -> ValidationReport:
    target = Path(run_dir)
    issues = []
    checked = 0
    for cp_path in sorted((target / "checkpoints").glob("*.json")):
        checkpoint = TaskCheckpoint.model_validate_json(cp_path.read_text(encoding="utf-8"))
        checked += 1
        if checkpoint.status != "succeeded":
            issues.append(
                ValidationIssue(
                    code="task_not_succeeded",
                    message=checkpoint.error or checkpoint.status,
                )
            )
            continue
        if checkpoint.asset is None or not Path(checkpoint.asset.path).is_file():
            issues.append(ValidationIssue(code="missing_asset", message=checkpoint.task_id))
            continue
        if file_sha256(Path(checkpoint.asset.path)) != checkpoint.asset.sha256:
            issues.append(
                ValidationIssue(code="asset_checksum_mismatch", message=checkpoint.task_id)
            )
        if checkpoint.observations_path is None or not Path(checkpoint.observations_path).is_file():
            issues.append(ValidationIssue(code="missing_observations", message=checkpoint.task_id))
    return ValidationReport.from_issues(checked, issues)
