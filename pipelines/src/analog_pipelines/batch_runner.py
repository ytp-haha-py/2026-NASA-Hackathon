"""Resumable bounded batch runner with durable checkpoints (Issue #32).

Local-only: no queue service, no network. A caller supplies partition tasks and a
worker. Successfully checkpointed partitions are not recomputed; failures remain
auditable and can be retried explicitly.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

CHECKPOINT_VERSION = "1.0.0"
# A terminal task never recomputes on resume while its artifact still verifies.
# `no_coverage` and `invalid` are auditable outcomes, not silent successes.
TERMINAL_STATUSES = frozenset({"succeeded", "no_coverage", "invalid"})


class BatchError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class PartitionTask:
    task_id: str
    region_id: str
    window_count: int = 0
    input_checksum: str | None = None

    @property
    def fingerprint(self) -> str:
        raw = (
            f"{self.task_id}|{self.region_id}|{self.window_count}|{self.input_checksum or ''}"
        ).encode()
        return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True, slots=True)
class WorkerResult:
    output_checksum: str
    rows: int
    output_path: str | None = None
    warnings: tuple[str, ...] = ()
    status: str = "succeeded"
    metrics: dict[str, int] = field(default_factory=dict)


Worker = Callable[[PartitionTask], WorkerResult]
ReuseCheck = Callable[[PartitionTask, dict[str, object]], bool]


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


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _output_is_reusable(record: dict[str, object], task: PartitionTask) -> bool:
    if record.get("fingerprint") != task.fingerprint:
        return False
    path_value = record.get("output_path")
    checksum = record.get("output_checksum")
    if not path_value or not checksum:
        return False
    path = Path(str(path_value))
    if not path.is_file():
        return False
    return _sha256_file(path) == str(checksum)


def _new_record(task: PartitionTask) -> dict[str, object]:
    return {
        "status": "pending",
        "attempts": 0,
        "fingerprint": task.fingerprint,
        "region_id": task.region_id,
        "window_count": task.window_count,
        "input_checksum": task.input_checksum,
        "output_checksum": None,
        "output_path": None,
        "rows": 0,
        "metrics": {},
        "warnings": [],
        "error": None,
        "updated_at": None,
    }


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
        reuse_check: ReuseCheck | None = None,
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
        self.reuse_check = reuse_check
        self.store = self._open_or_create()
        if self.store.state.run_id != self.run_id:
            raise BatchError(
                f"checkpoint belongs to run {self.store.state.run_id!r}, not {self.run_id!r}"
            )

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
        added = False
        for task in tasks:
            if task.task_id in self.store.state.tasks:
                continue
            if len(self.store.state.tasks) >= self.max_tasks:
                raise BatchError(f"task budget exceeded ({self.max_tasks})")
            self.store.state.tasks[task.task_id] = _new_record(task)
            added = True
        if added:
            self.store.save()

    def _artifact_reusable(self, task: PartitionTask, record: dict[str, object]) -> bool:
        if not _output_is_reusable(record, task):
            return False
        if self.reuse_check is None:
            return True
        try:
            return bool(self.reuse_check(task, record))
        except Exception:  # noqa: BLE001 - an unreadable artifact must force a recompute
            return False

    def run(self, tasks: Iterable[PartitionTask], *, retry_failed: bool = False) -> dict[str, int]:
        tasks = list(tasks)
        self.add_tasks(tasks)
        pending: list[PartitionTask] = []
        for task in tasks:
            record = self.store.state.tasks[task.task_id]
            status = str(record["status"])
            if status in TERMINAL_STATUSES:
                if self._artifact_reusable(task, record):
                    continue
                record["status"] = "pending"
                record["error"] = "checkpoint output missing, corrupted, or task parameters changed"
            if status == "failed" and not retry_failed:
                continue
            record["status"] = "running"
            record["fingerprint"] = task.fingerprint
            record["region_id"] = task.region_id
            record["window_count"] = task.window_count
            record["input_checksum"] = task.input_checksum
            pending.append(task)
        counts = {
            "succeeded": 0,
            "failed": 0,
            "skipped": 0,
            "no_coverage": 0,
            "invalid": 0,
        }
        counts["skipped"] = len(tasks) - len(pending)
        self.store.save()

        if not pending:
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
                    output_path = Path(result.output_path) if result.output_path else None
                    if result.status not in TERMINAL_STATUSES:
                        record["status"] = "failed"
                        record["error"] = f"worker returned unsupported status {result.status!r}"
                        record["updated_at"] = datetime.now(UTC).isoformat()
                        counts["failed"] += 1
                        self.store.save()
                        continue
                    if output_path is None or not output_path.is_file():
                        record["status"] = "failed"
                        record["error"] = "worker produced no verifiable output path"
                        record["updated_at"] = datetime.now(UTC).isoformat()
                        counts["failed"] += 1
                        self.store.save()
                        continue
                    actual_checksum = _sha256_file(output_path)
                    if actual_checksum != result.output_checksum:
                        record["status"] = "failed"
                        record["error"] = "worker output checksum does not match its artifact"
                        record["updated_at"] = datetime.now(UTC).isoformat()
                        counts["failed"] += 1
                        self.store.save()
                        continue
                    record["status"] = result.status
                    record["output_checksum"] = result.output_checksum
                    record["output_path"] = result.output_path
                    record["rows"] = result.rows
                    record["metrics"] = dict(result.metrics)
                    record["warnings"] = list(result.warnings)
                    record["error"] = None
                    record["updated_at"] = datetime.now(UTC).isoformat()
                    counts[result.status] += 1
                self.store.save()
        return counts

    def _run_task(self, task: PartitionTask) -> WorkerResult:
        record = self.store.state.tasks[task.task_id]
        total_attempts = int(record.get("attempts", 0) or 0)
        attempts = 0
        last_error: Exception | None = None
        while attempts <= self.max_retries:
            attempts += 1
            total_attempts += 1
            record["attempts"] = total_attempts
            try:
                return self.worker(task)
            except Exception as exc:  # noqa: BLE001 - retry policy is explicit
                last_error = exc
        raise BatchError(
            f"task {task.task_id} failed after {attempts} attempt(s) this invocation "
            f"({total_attempts} recorded): {last_error}"
        )

    def report(self) -> dict[str, object]:
        records = self.store.state.tasks.values()
        counts: dict[str, int] = {}
        rows = 0
        metrics: dict[str, int] = {}
        for record in records:
            counts[str(record["status"])] = counts.get(str(record["status"]), 0) + 1
            rows += int(record.get("rows", 0))
            for key, value in dict(record.get("metrics") or {}).items():
                metrics[str(key)] = metrics.get(str(key), 0) + int(value)
        return {
            "run_id": self.run_id,
            "checkpoint": str(self.checkpoint_path),
            "checkpoint_version": self.store.state.checkpoint_version,
            "counts": counts,
            "rows": rows,
            "metrics": metrics,
            "bounded": True,
        }
