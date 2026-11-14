"""Resumable bounded batch runner with durable checkpoints (Issue #32).

Local-only: no queue service, no network. A caller supplies partition tasks and a
worker. Successfully checkpointed partitions are not recomputed; failures remain
auditable and can be retried explicitly.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

CHECKPOINT_VERSION = "1.0.0"


class BatchError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class PartitionTask:
    task_id: str
    region_id: str
    window_count: int = 0


@dataclass(frozen=True, slots=True)
class WorkerResult:
    output_checksum: str
    rows: int
    warnings: tuple[str, ...] = ()


Worker = Callable[[PartitionTask], WorkerResult]


@dataclass(slots=True)
class BatchState:
    run_id: str
    checkpoint_version: str = CHECKPOINT_VERSION
    tasks: dict[str, dict[str, object]] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_payload(self) -> dict[str, object]:
        return {
            "checkpoint_version": self.checkpoint_version,
            "run_id": self.run_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "tasks": self.tasks,
        }


class CheckpointStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.state = self._load()

    def _load(self) -> BatchState:
        if not self.path.is_file():
            raise BatchError(f"checkpoint not found: {self.path}")
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        if raw.get("checkpoint_version") != CHECKPOINT_VERSION:
            raise BatchError("unsupported checkpoint version")
        return BatchState(
            run_id=raw["run_id"],
            checkpoint_version=raw["checkpoint_version"],
            tasks=raw.get("tasks", {}),
            created_at=raw.get("created_at", datetime.now(UTC).isoformat()),
            updated_at=raw.get("updated_at", datetime.now(UTC).isoformat()),
        )

    def save(self) -> None:
        self.state.updated_at = datetime.now(UTC).isoformat()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self.state.to_payload(), indent=2, sort_keys=True) + "\n"
        fd, tmp = tempfile.mkstemp(prefix=self.path.name, dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)


def _new_state(run_id: str, tasks: Iterable[PartitionTask]) -> BatchState:
    state = BatchState(run_id=run_id)
    for task in tasks:
        state.tasks[task.task_id] = {
            "status": "pending",
            "attempts": 0,
            "region_id": task.region_id,
            "window_count": task.window_count,
            "output_checksum": None,
            "rows": 0,
            "error": None,
            "updated_at": None,
        }
    return state


class BatchRunner:
    def __init__(
        self,
        *,
        run_id: str,
        checkpoint_path: str | Path,
        worker: Worker,
        max_workers: int = 2,
        max_retries: int = 1,
        max_tasks: int = 128,
    ) -> None:
        if max_workers < 1 or max_workers > 8:
            raise BatchError("max_workers must be within 1..8")
        if max_retries < 0 or max_retries > 2:
            raise BatchError("max_retries must be within 0..2")
        if max_tasks < 1 or max_tasks > 5000:
            raise BatchError("max_tasks must be within 1..5000")
        self.run_id = run_id
        self.checkpoint_path = Path(checkpoint_path)
        self.worker = worker
        self.max_workers = max_workers
        self.max_retries = max_retries
        self.max_tasks = max_tasks
        self.store = self._open_or_create()

    def _open_or_create(self) -> CheckpointStore:
        if self.checkpoint_path.is_file():
            return CheckpointStore(self.checkpoint_path)
        self.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        state = BatchState(run_id=self.run_id)
        store = CheckpointStore.__new__(CheckpointStore)
        store.path = self.checkpoint_path
        store.state = state
        store.save()
        return store

    def add_tasks(self, tasks: Iterable[PartitionTask]) -> None:
        for task in tasks:
            if task.task_id in self.store.state.tasks:
                continue
            if len(self.store.state.tasks) >= self.max_tasks:
                raise BatchError(f"task budget exceeded ({self.max_tasks})")
            self.store.state.tasks[task.task_id] = {
                "status": "pending",
                "attempts": 0,
                "region_id": task.region_id,
                "window_count": task.window_count,
                "output_checksum": None,
                "rows": 0,
                "error": None,
                "updated_at": None,
            }
        self.store.save()

    def run(self, tasks: Iterable[PartitionTask], *, retry_failed: bool = False) -> dict[str, int]:
        self.add_tasks(tasks)
        pending: list[PartitionTask] = []
        for task in tasks:
            record = self.store.state.tasks[task.task_id]
            status = record["status"]
            if status == "succeeded":
                continue
            if status == "failed" and not retry_failed:
                continue
            record["status"] = "running"
            pending.append(task)
        self.store.save()

        counts = {"succeeded": 0, "failed": 0, "skipped": 0}
        if not pending:
            counts["skipped"] = len(tasks)
            return counts

        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            futures = {pool.submit(self._run_task, task): task for task in pending}
            for future in as_completed(futures):
                task = futures[future]
                try:
                    result = future.result()
                except Exception as exc:  # noqa: BLE001 - recorded, not hidden
                    record = self.store.state.tasks[task.task_id]
                    record["status"] = "failed"
                    record["error"] = f"{type(exc).__name__}: {exc}"
                    record["updated_at"] = datetime.now(UTC).isoformat()
                    counts["failed"] += 1
                else:
                    record = self.store.state.tasks[task.task_id]
                    record["status"] = "succeeded"
                    record["output_checksum"] = result.output_checksum
                    record["rows"] = result.rows
                    record["error"] = None
                    record["updated_at"] = datetime.now(UTC).isoformat()
                    counts["succeeded"] += 1
                self.store.save()
        return counts

    def _run_task(self, task: PartitionTask) -> WorkerResult:
        record = self.store.state.tasks[task.task_id]
        attempts = 0
        last_error: Exception | None = None
        while attempts <= self.max_retries:
            attempts += 1
            record["attempts"] = attempts
            try:
                return self.worker(task)
            except Exception as exc:  # noqa: BLE001 - retry policy is explicit
                last_error = exc
        raise BatchError(f"task {task.task_id} failed after {attempts} attempts: {last_error}")

    def report(self) -> dict[str, object]:
        records = self.store.state.tasks.values()
        counts: dict[str, int] = {}
        rows = 0
        for record in records:
            counts[str(record["status"])] = counts.get(str(record["status"]), 0) + 1
            rows += int(record.get("rows", 0))
        return {
            "run_id": self.run_id,
            "checkpoint": str(self.checkpoint_path),
            "counts": counts,
            "rows": rows,
            "bounded": True,
        }
