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
    )


def _write_output(tmp_path: Path, task: PartitionTask) -> WorkerResult:
    import hashlib

    path = tmp_path / f"{task.task_id}.bin"
    path.write_bytes(f"payload:{task.task_id}:{task.window_count}".encode())
    checksum = hashlib.sha256(path.read_bytes()).hexdigest()
    return WorkerResult(output_checksum=checksum, rows=task.window_count, output_path=str(path))


def test_batch_resume_does_not_recompute_success(tmp_path: Path):
    calls: dict[str, int] = {}

    def failing_worker(task: PartitionTask) -> WorkerResult:
        calls[task.task_id] = calls.get(task.task_id, 0) + 1
        if task.task_id == "b":
            raise RuntimeError("simulated interruption")
        return _write_output(tmp_path, task)

    def good_worker(task: PartitionTask) -> WorkerResult:
        calls[task.task_id] = calls.get(task.task_id, 0) + 1
        return _write_output(tmp_path, task)

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
    worker = lambda task: _write_output(tmp_path, task)  # noqa: E731
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
    build_index(
        [row("coarse-v1:a", 10, 20, 0.5)], root=tmp_path / "index", snapshot_id="fixture-snapshot"
    )
    parquet = next((tmp_path / "index").rglob("*.parquet"))
    parquet.write_bytes(parquet.read_bytes() + b"tamper")
    with pytest.raises(Exception, match="checksum"):
        verify_index(tmp_path / "index")


def test_duplicate_candidate_ids_are_rejected(tmp_path: Path):
    with pytest.raises(Exception, match="duplicate"):
        build_index(
            [row("coarse-v1:a", 10, 20, 0.5), row("coarse-v1:a", 11, 21, 0.4)],
            root=tmp_path / "index",
            snapshot_id="fixture-snapshot",
        )


def test_corrupted_or_missing_output_forces_recompute(tmp_path: Path):
    import hashlib

    checkpoint = tmp_path / "checkpoint.json"
    task = PartitionTask("a", "r", 1)
    calls = {"n": 0}

    def worker(item: PartitionTask) -> WorkerResult:
        calls["n"] += 1
        return _write_output(tmp_path, item)

    runner = BatchRunner(run_id="r", checkpoint_path=checkpoint, worker=worker, max_workers=1)
    runner.run([task])
    output = tmp_path / "a.bin"
    output.write_bytes(b"corrupted")
    resumed = BatchRunner(run_id="r", checkpoint_path=checkpoint, worker=worker, max_workers=1)
    result = resumed.run([task])
    assert result["succeeded"] == 1
    assert calls["n"] == 2
    checksum = hashlib.sha256(output.read_bytes()).hexdigest()
    record = resumed.store.state.tasks["a"]
    assert record["output_checksum"] == checksum


def test_checkpoint_run_identity_and_task_fingerprint_are_checked(tmp_path: Path):
    checkpoint = tmp_path / "checkpoint.json"
    task = PartitionTask("a", "r", 1)
    runner = BatchRunner(
        run_id="run-a",
        checkpoint_path=checkpoint,
        worker=lambda item: _write_output(tmp_path, item),
        max_workers=1,
    )
    runner.run([task])
    with pytest.raises(BatchError, match="belongs to run"):
        BatchRunner(
            run_id="run-b",
            checkpoint_path=checkpoint,
            worker=lambda item: _write_output(tmp_path, item),
            max_workers=1,
        )
    changed = PartitionTask("a", "r", 2)
    calls = {"n": 0}

    def changed_worker(item: PartitionTask) -> WorkerResult:
        calls["n"] += 1
        return _write_output(tmp_path, item)

    resumed = BatchRunner(
        run_id="run-a", checkpoint_path=checkpoint, worker=changed_worker, max_workers=1
    )
    resumed.run([changed], retry_failed=False)
    assert calls["n"] == 1


def test_writer_is_partition_bounded_and_snapshots_are_immutable(tmp_path: Path):
    from analog_pipelines.coarse_index import CoarseIndexWriter

    root = tmp_path / "index"
    writer = CoarseIndexWriter(root=root, snapshot_id="snap-a", max_rows_per_partition=1)
    writer.add_row(row("coarse-v1:a", 10, 20, 0.5))
    writer.add_row(row("coarse-v1:b", 11, 21, 0.4))
    manifest = writer.finalize()
    assert len(manifest["partitions"]) == 2
    old_manifest = (root / "snapshots/snap-a/manifest.json").read_bytes()
    with pytest.raises(Exception, match="already exists"):
        build_index([row("coarse-v1:c", 12, 22, 0.3)], root=root, snapshot_id="snap-a")
    build_index([row("coarse-v1:d", 13, 23, 0.2)], root=root, snapshot_id="snap-b")
    assert (root / "snapshots/snap-a/manifest.json").read_bytes() == old_manifest


def test_duplicate_ids_across_partitions_are_rejected(tmp_path: Path):
    from analog_pipelines.coarse_index import CoarseIndexWriter

    writer = CoarseIndexWriter(
        root=tmp_path / "index", snapshot_id="snap", max_rows_per_partition=1
    )
    writer.add_row(row("coarse-v1:a", 10, 20, 0.5))
    writer.add_row(row("coarse-v1:a", 11, 21, 0.4))
    with pytest.raises(Exception, match="duplicate"):
        writer.finalize()


def test_invalid_domains_are_rejected_before_publication(tmp_path: Path):
    with pytest.raises(Exception, match="outside domain"):
        build_index([row("coarse-v1:a", 100, 20, 0.5)], root=tmp_path / "index", snapshot_id="s")


def test_zero_weight_coarse_feature_does_not_exclude_missing_value(tmp_path: Path):
    build_index([row("coarse-v1:a", 10, 20, None)], root=tmp_path / "index", snapshot_id="snap")
    repo = CoarseIndexRepository(tmp_path / "index")
    result = repo.search(
        target={"coarse_slope_mean_deg": 10.0, "coarse_flat_fraction_lt5deg": 0.5},
        weights={"coarse_slope_mean_deg": 1.0, "coarse_flat_fraction_lt5deg": 0.0},
    )
    assert result["results"][0]["candidate_id"] == "coarse-v1:a"
    assert result["results"][0]["distance"] == pytest.approx(0.0)


def test_snapshots_share_immutable_partitions_and_remain_queryable(tmp_path: Path):
    root = tmp_path / "index"
    rows = [row("coarse-v1:a", 10.0, 20.0, 0.5), row("coarse-v1:b", 12.0, 22.0, 0.6)]
    first = build_index(rows, root=root, snapshot_id="snap-a", max_rows_per_partition=1)
    second = build_index(rows, root=root, snapshot_id="snap-b", max_rows_per_partition=1)
    assert [p["path"] for p in first["partitions"]] == [p["path"] for p in second["partitions"]]
    assert len(list((root / "partitions").rglob("*.parquet"))) == 2

    repo_a = CoarseIndexRepository(root, snapshot_id="snap-a")
    repo_b = CoarseIndexRepository(root, snapshot_id="snap-b")
    assert (
        repo_a.search(
            target={"coarse_slope_mean_deg": 10.0}, weights={"coarse_slope_mean_deg": 1.0}
        )["results"][0]["candidate_id"]
        == "coarse-v1:a"
    )
    assert (
        repo_b.search(
            target={"coarse_slope_mean_deg": 10.0}, weights={"coarse_slope_mean_deg": 1.0}
        )["results"][0]["candidate_id"]
        == "coarse-v1:a"
    )

    # A changed row creates a new content-addressed partition; snap-a still resolves
    # to its original manifest/partition.
    third = build_index(
        [row("coarse-v1:a", 11.0, 20.0, 0.5), rows[1]],
        root=root,
        snapshot_id="snap-c",
        max_rows_per_partition=1,
    )
    assert third["partitions"][0]["path"] != first["partitions"][0]["path"]
    assert repo_a.get("coarse-v1:a")["slope_mean_deg"] == 10.0
    repo_c = CoarseIndexRepository(root, snapshot_id="snap-c")
    assert repo_c.get("coarse-v1:a")["slope_mean_deg"] == 11.0
