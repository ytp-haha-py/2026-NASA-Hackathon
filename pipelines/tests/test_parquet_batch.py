from __future__ import annotations

from pathlib import Path

import pytest
from analog_pipelines.batch_runner import (
    BatchError,
    BatchRunner,
    PartitionTask,
    WorkerResult,
)
from analog_pipelines.coarse_features import COARSE_METHOD_VERSION
from analog_pipelines.coarse_index import (
    CoarseIndexRepository,
    CoarseRow,
    build_index,
    verify_index,
)


def row(
    candidate_id: str, mean: float, p90: float, flat: float, region: str = "pilot"
) -> CoarseRow:
    return CoarseRow(
        candidate_id=candidate_id,
        grid_id="coarse-grid-1.0.0",
        region_id=region,
        body="Earth",
        west=10.0,
        south=45.0,
        east=10.06,
        north=45.045,
        crs="EPSG:32633",
        area_km2=25.0,
        slope_mean_deg=mean,
        slope_p90_deg=p90,
        flat_fraction=flat,
        valid_fraction=1.0,
        method_version=COARSE_METHOD_VERSION,
        grid_version="coarse-grid-1.0.0",
        dataset_id="fixture",
        source_asset_id="fixture-asset",
        qa_flags=["synthetic_fixture"],
        snapshot_id="fixture-snapshot",
    )


def test_batch_resume_does_not_recompute_success(tmp_path: Path):
    calls: dict[str, int] = {}

    def failing_worker(task: PartitionTask) -> WorkerResult:
        calls[task.task_id] = calls.get(task.task_id, 0) + 1
        if task.task_id == "b":
            raise RuntimeError("simulated interruption")
        return WorkerResult(output_checksum=f"sha256:{task.task_id}", rows=task.window_count)

    def good_worker(task: PartitionTask) -> WorkerResult:
        calls[task.task_id] = calls.get(task.task_id, 0) + 1
        return WorkerResult(output_checksum=f"sha256:{task.task_id}", rows=task.window_count)

    tasks = [PartitionTask("a", "r1", 2), PartitionTask("b", "r1", 3), PartitionTask("c", "r2", 1)]
    checkpoint = tmp_path / "checkpoint.json"
    runner = BatchRunner(
        run_id="run-1", checkpoint_path=checkpoint, worker=failing_worker, max_workers=1
    )
    first = runner.run(tasks)
    assert first == {"succeeded": 2, "failed": 1, "skipped": 0}
    assert calls == {"a": 1, "b": 2, "c": 1}  # b retried once by policy

    calls.clear()
    resumed = BatchRunner(
        run_id="run-1", checkpoint_path=checkpoint, worker=good_worker, max_workers=1
    )
    second = resumed.run(tasks, retry_failed=True)
    assert second == {"succeeded": 1, "failed": 0, "skipped": 0}
    assert calls == {"b": 1}
    assert resumed.report()["counts"] == {"succeeded": 3}


def test_batch_restart_and_bounds(tmp_path: Path):
    checkpoint = tmp_path / "checkpoint.json"
    tasks = [PartitionTask(f"t{i}", "r", 1) for i in range(3)]
    worker = lambda task: WorkerResult("sha256:x", 1)  # noqa: E731
    runner = BatchRunner(run_id="r", checkpoint_path=checkpoint, worker=worker, max_workers=2)
    runner.run(tasks)
    restarted = BatchRunner(run_id="r", checkpoint_path=checkpoint, worker=worker, max_workers=2)
    result = restarted.run(tasks)
    assert result["skipped"] == 3
    with pytest.raises(BatchError):
        BatchRunner(
            run_id="bad", checkpoint_path=tmp_path / "new.json", worker=worker, max_workers=0
        )


def test_parquet_index_roundtrip_and_duckdb_ranking(tmp_path: Path):
    rows = [
        row("coarse-v1:a", 10.0, 20.0, 0.5),
        row("coarse-v1:b", 12.0, 22.0, 0.6),
        row("coarse-v1:c", 30.0, 40.0, 0.1),
    ]
    manifest = build_index(rows, root=tmp_path / "index", snapshot_id="fixture-snapshot")
    assert manifest["row_count"] == 3
    verified = verify_index(tmp_path / "index")
    assert verified["snapshot_id"] == "fixture-snapshot"

    repo = CoarseIndexRepository(tmp_path / "index")
    result = repo.search(
        target={"coarse_slope_mean_deg": 10.0, "coarse_flat_fraction_lt5deg": 0.5},
        weights={"coarse_slope_mean_deg": 1.0, "coarse_flat_fraction_lt5deg": 0.5},
        limit=2,
    )
    assert result["indexed_candidate_count"] == 3
    assert [item["candidate_id"] for item in result["results"]] == ["coarse-v1:a", "coarse-v1:b"]
    assert result["results"][0]["distance"] == pytest.approx(0.0)
    assert repo.get("coarse-v1:c")["slope_mean_deg"] == 30.0
    assert repo.get("missing") is None


def test_parquet_manifest_tamper_is_rejected(tmp_path: Path):
    build_index([row("coarse-v1:a", 10, 20, 0.5)], root=tmp_path / "index", snapshot_id="s")
    parquet = next((tmp_path / "index" / "regions").rglob("*.parquet"))
    parquet.write_bytes(parquet.read_bytes() + b"tamper")
    with pytest.raises(Exception, match="checksum"):
        verify_index(tmp_path / "index")


def test_duplicate_candidate_ids_are_rejected(tmp_path: Path):
    with pytest.raises(Exception, match="duplicate"):
        build_index(
            [row("coarse-v1:a", 10, 20, 0.5), row("coarse-v1:a", 11, 21, 0.4)],
            root=tmp_path / "index",
            snapshot_id="s",
        )
