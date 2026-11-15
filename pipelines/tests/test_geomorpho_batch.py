"""Bounded/resumable Geomorpho250 batch CLI tests (Issue #32).

Every raster in this file is a clearly labelled synthetic fixture: values are
constant, so none of these rows are observations. The real-data run is documented
in docs/GEOMORPHO_BATCH_PILOT.md and never runs in CI.
"""

from __future__ import annotations

import contextlib
import io
import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
import rasterio
from analog_pipelines.batch_runner import BatchRunner
from analog_pipelines.geomorpho_batch import (
    PILOT_REGIONS,
    SYNTHETIC_COVERAGE_LABEL,
    build_report,
    main,
    make_worker,
    plan_tasks,
    select_regions,
    verify_run,
)
from analog_pipelines.geomorpho_batch import (
    verify_task_artifact as reuse_check,
)
from rasterio.transform import from_origin

RES_DEG = 7.5 / 3600
PAD_DEG = 0.05
FIXTURE_REGION = next(region for region in PILOT_REGIONS if region.region_id == "pilot-alps")
CELLS_PER_TASK = 16


def write_fixture(path: Path, region, *, value: float = 8.0, nodata: bool = False) -> None:
    """Write a constant-value synthetic slope GeoTIFF for one pilot region."""
    west = region.west - PAD_DEG
    south = region.south - PAD_DEG
    east = region.east + PAD_DEG
    north = region.north + PAD_DEG
    width = int(round((east - west) / RES_DEG))
    height = int(round((north - south) / RES_DEG))
    fill = -9999.0 if nodata else value
    data = np.full((height, width), fill, dtype="float32")
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=height,
        width=width,
        count=1,
        dtype="float32",
        crs="EPSG:4326",
        transform=from_origin(west, north, RES_DEG, RES_DEG),
        nodata=-9999.0,
    ) as destination:
        destination.write(data, 1)


def stage(tmp_path: Path, *, value: float = 8.0, nodata: bool = False) -> Path:
    cache = tmp_path / "cache"
    write_fixture(
        cache / f"{FIXTURE_REGION.region_id}.tif", FIXTURE_REGION, value=value, nodata=nodata
    )
    return cache


def cli(*argv: str) -> tuple[int, dict, str]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = main(list(argv))
    text = stdout.getvalue()
    return code, (json.loads(text) if text.strip() else {}), stderr.getvalue()


def base_args(tmp_path: Path, run_id: str, cache: Path, *, out: Path | None = None) -> list[str]:
    return [
        "--run-id",
        run_id,
        "--region",
        FIXTURE_REGION.region_id,
        "--cache",
        str(cache),
        "--out",
        str(out or tmp_path / "out"),
        "--source-mode",
        "synthetic-fixture",
        "--cells-per-task",
        str(CELLS_PER_TASK),
        "--workers",
        "2",
    ]


def test_plan_is_deterministic_and_independent_of_selection(tmp_path: Path):
    full = plan_tasks(select_regions(None), cache_dir=tmp_path / "cache", cells_per_task=8)
    again = plan_tasks(
        select_regions(list(reversed([region.region_id for region in PILOT_REGIONS]))),
        cache_dir=tmp_path / "cache",
        cells_per_task=8,
    )
    assert [task.task_id for task in full] == [task.task_id for task in again]
    assert full == again
    assert full[0].cells[0].row <= full[-1].cells[-1].row

    focused = plan_tasks(
        select_regions([FIXTURE_REGION.region_id]), cache_dir=tmp_path / "cache", cells_per_task=8
    )
    assert [task.task_id for task in focused] == [
        task.task_id for task in full if task.region.region_id == FIXTURE_REGION.region_id
    ]
    limited = focused[:1]
    assert [task.task_id for task in limited] == [focused[0].task_id]


def test_dry_run_writes_nothing_and_labels_synthetic_fixture(tmp_path: Path):
    cache = stage(tmp_path)
    code, payload, stderr = cli(*base_args(tmp_path, "dry-1", cache), "--dry-run")
    assert (code, stderr) == (0, "")
    assert payload["mode"] == "dry-run"
    assert payload["coverage"]["label"] == SYNTHETIC_COVERAGE_LABEL
    assert payload["plan"]["tasks"] == 3
    assert payload["plan"]["cells"] == 48
    assert payload["plan"]["selected_tasks"] == 3
    assert payload["inputs"][FIXTURE_REGION.region_id]["present"] is True
    assert not (tmp_path / "out").exists()

    limited_code, limited, _ = cli(
        *base_args(tmp_path, "dry-2", cache), "--dry-run", "--limit", "1"
    )
    assert limited_code == 0
    assert limited["plan"]["selected_tasks"] == 1
    assert limited["plan"]["selected"][0]["task_id"] == payload["plan"]["selected"][0]["task_id"]


def test_resume_skips_success_and_recomputes_corrupted_artifacts(tmp_path: Path):
    cache = stage(tmp_path)
    out = tmp_path / "out"
    run_root = out / "runs" / "resume-1"
    planned = plan_tasks(
        select_regions([FIXTURE_REGION.region_id]), cache_dir=cache, cells_per_task=CELLS_PER_TASK
    )
    tasks = [task.task for task in planned]
    calls: dict[str, int] = {}
    real_worker = make_worker(
        run_id="resume-1",
        planned_by_id={task.task_id: task for task in planned},
        out_dir=run_root,
        source_mode="synthetic-fixture",
    )
    interrupted = {"task_id": planned[1].task_id, "armed": True}

    def flaky_worker(task):
        calls[task.task_id] = calls.get(task.task_id, 0) + 1
        if task.task_id == interrupted["task_id"] and interrupted["armed"]:
            interrupted["armed"] = False
            raise RuntimeError("simulated interruption")
        return real_worker(task)

    def counting_worker(task):
        calls[task.task_id] = calls.get(task.task_id, 0) + 1
        return real_worker(task)

    def runner(worker, *, retry_failed: bool = False, max_workers: int = 2) -> BatchRunner:
        return BatchRunner(
            run_id="resume-1",
            checkpoint_path=run_root / "checkpoint.json",
            worker=worker,
            max_workers=max_workers,
            max_retries=0,
            max_tasks=len(planned),
            reuse_check=lambda task, record: reuse_check(task, record, out_dir=run_root),
        )

    first = runner(flaky_worker)
    counts = first.run(tasks)
    assert counts["succeeded"] == 2
    assert counts["failed"] == 1
    assert calls[planned[0].task_id] == 1

    # Resume after the simulated interruption: successful tasks are not recomputed.
    second = runner(counting_worker)
    counts = second.run(tasks, retry_failed=True)
    assert counts == {
        "succeeded": 1,
        "failed": 0,
        "skipped": 2,
        "no_coverage": 0,
        "invalid": 0,
    }
    assert calls[planned[0].task_id] == 1
    assert calls[planned[1].task_id] == 2
    assert second.report()["counts"] == {"succeeded": 3}
    assert verify_run(second.store.state.tasks, out_dir=run_root)["ok"] is True

    # A truncated Parquet artifact is detected by the checksum and recomputed.
    first_record = second.store.state.tasks[planned[0].task.task_id]
    parquet = Path(str(first_record["output_path"]))
    parquet.write_bytes(parquet.read_bytes()[:16])
    third = runner(counting_worker)
    counts = third.run(tasks)
    assert counts["succeeded"] == 1
    assert counts["skipped"] == 2
    assert calls[planned[0].task_id] == 2
    assert verify_run(third.store.state.tasks, out_dir=run_root)["ok"] is True

    # A corrupted JSON sidecar is also rejected by the resume guard.
    second_record = third.store.state.tasks[planned[1].task.task_id]
    sidecar = Path(str(second_record["output_path"])).parent / "task.json"
    sidecar.write_text("{not-json", encoding="utf-8")
    fourth = runner(counting_worker)
    counts = fourth.run(tasks)
    assert counts["succeeded"] == 1
    assert counts["skipped"] == 2
    assert calls[planned[1].task_id] == 3
    assert verify_run(fourth.store.state.tasks, out_dir=run_root)["ok"] is True


def test_no_coverage_is_terminal_auditable_and_not_recomputed(tmp_path: Path):
    cache = stage(tmp_path, nodata=True)
    args = base_args(tmp_path, "coverage-1", cache)
    code, payload, _ = cli(*args, "--limit", "1")
    assert code == 0
    assert payload["outcomes"]["tasks"] == {"no_coverage": 1}
    assert payload["outcomes"]["cell_counts"]["no_coverage"] == CELLS_PER_TASK
    assert payload["outcomes"]["rows"] == 0
    assert payload["coverage"]["label"] == SYNTHETIC_COVERAGE_LABEL
    assert payload["verification"]["ok"] is True
    assert payload["regions"][0]["tasks"] == {"no_coverage": 1}

    run_root = tmp_path / "out/runs/coverage-1"
    record = json.loads((run_root / "checkpoint.json").read_text(encoding="utf-8"))
    task_id = next(iter(record["tasks"]))
    sidecar = run_root / "tasks" / task_id / "task.json"
    finished_at = json.loads(sidecar.read_text(encoding="utf-8"))["timing"]["finished_at"]

    resume_code, resumed, _ = cli(*args, "--limit", "1", "--resume")
    assert resume_code == 0
    assert resumed["bounds"]["last_counts"]["skipped"] == 1
    assert json.loads(sidecar.read_text(encoding="utf-8"))["timing"]["finished_at"] == finished_at


def test_invalid_cells_are_counted_and_fail_the_run(tmp_path: Path):
    cache = stage(tmp_path, value=200.0)  # slope degrees outside [0, 90]
    code, payload, _ = cli(*base_args(tmp_path, "invalid-1", cache), "--limit", "1")
    assert code == 1
    assert payload["outcomes"]["tasks"] == {"invalid": 1}
    assert payload["outcomes"]["cell_counts"]["invalid"] == CELLS_PER_TASK
    assert payload["outcomes"]["rows"] == 0
    assert payload["verification"]["ok"] is True
    sidecar = json.loads(
        next((tmp_path / "out/runs/invalid-1/tasks").glob("*/task.json")).read_text(
            encoding="utf-8"
        )
    )
    assert sidecar["outcome"] == "invalid"
    assert {reason["code"] for reason in sidecar["reasons"]} == {"invalid_slope_domain"}


def test_missing_staged_window_is_auditable_and_retryable(tmp_path: Path):
    empty_cache = tmp_path / "cache"
    empty_cache.mkdir()
    args = base_args(tmp_path, "missing-1", empty_cache)
    code, payload, _ = cli(*args, "--limit", "1")
    assert code == 1
    assert payload["outcomes"]["tasks"] == {"failed": 1}
    assert payload["artifacts"] == []
    assert payload["inputs"][FIXTURE_REGION.region_id]["present"] is False
    assert any("staged source window missing" in item for item in payload["warnings"])
    checkpoint = json.loads(
        (tmp_path / "out/runs/missing-1/checkpoint.json").read_text(encoding="utf-8")
    )
    record = next(iter(checkpoint["tasks"].values()))
    assert record["status"] == "failed"
    assert "staged source window missing" in record["error"]
    first_attempts = record["attempts"]

    retry_code, retried, _ = cli(*args, "--limit", "1", "--resume", "--retry-failed")
    assert retry_code == 1
    checkpoint = json.loads(
        (tmp_path / "out/runs/missing-1/checkpoint.json").read_text(encoding="utf-8")
    )
    assert next(iter(checkpoint["tasks"].values()))["attempts"] > first_attempts
    assert retried["outcomes"]["tasks"] == {"failed": 1}

    # Staging the window and retrying succeeds without a new run id.
    stage(tmp_path)
    fixed_code, fixed, _ = cli(*args, "--limit", "1", "--resume", "--retry-failed")
    assert fixed_code == 0
    assert fixed["outcomes"]["tasks"] == {"succeeded": 1}
    assert fixed["outcomes"]["rows"] == CELLS_PER_TASK


def test_verify_only_reports_tampering_and_bounds_are_enforced(tmp_path: Path):
    cache = stage(tmp_path)
    args = base_args(tmp_path, "verify-1", cache)
    code, payload, _ = cli(*args, "--limit", "1")
    assert code == 0

    parquet = Path(payload["out_dir"]) / payload["artifacts"][0]["output_path"]
    parquet.write_bytes(parquet.read_bytes() + b"tamper")
    verify_code, verified, _ = cli(*args, "--limit", "1", "--verify-only")
    assert verify_code == 1
    assert verified["verification"]["ok"] is False
    assert verified["verification"]["problems"][0]["task_id"] == payload["artifacts"][0]["task_id"]

    for argv in (
        ["--workers", "5"],
        ["--workers", "0"],
        ["--cells-per-task", "0"],
        ["--limit", "0"],
        ["--memory-budget-mb", "0"],
        ["--region", "pilot-nowhere"],
        ["--run-id", "../escape"],
    ):
        code, _, stderr = cli(*args, *argv, "--dry-run")
        assert code == 2
        assert stderr.startswith("error:")

    unknown_verify = cli(*base_args(tmp_path, "absent-run", cache), "--verify-only")
    assert unknown_verify[0] == 2
    assert "needs an existing checkpoint" in unknown_verify[2]

    reused = cli(*args, "--limit", "1")
    assert reused[0] == 2
    assert "checkpoint already exists" in reused[2]


@pytest.mark.parametrize("workers", [1, 2, 3, 4])
def test_worker_bound_is_respected(tmp_path: Path, workers: int):
    cache = stage(tmp_path)
    args = base_args(tmp_path, f"workers-{workers}", cache)
    args[args.index("--workers") + 1] = str(workers)
    code, payload, _ = cli(*args, "--limit", "1")
    assert code == 0
    assert payload["bounds"]["workers"] == workers


def test_verify_only_does_not_require_staged_inputs(tmp_path: Path):
    cache = stage(tmp_path)
    args = base_args(tmp_path, "verify-offline", cache)
    assert cli(*args, "--limit", "1")[0] == 0
    cache.rename(tmp_path / "cache-moved")
    code, payload, _ = cli(*args, "--limit", "1", "--verify-only")
    assert code == 0
    assert payload["verification"]["ok"] is True
    assert any("staged source window missing" in item for item in payload["warnings"])


def test_report_warns_on_missing_or_unpublished_staged_inputs(tmp_path: Path):
    planned = plan_tasks(
        select_regions([FIXTURE_REGION.region_id]),
        cache_dir=tmp_path / "cache",
        cells_per_task=CELLS_PER_TASK,
    )
    base = {
        "run_id": "report-only",
        "out_dir": tmp_path / "out",
        "checkpoint_path": tmp_path / "out/checkpoint.json",
        "report_path": tmp_path / "out/report.json",
        "selected": planned,
        "records": {},
        "verification": {"ok": True},
        "bounds": {},
        "source_mode": "real",
        "dry_run": False,
        "limit": None,
    }
    missing = build_report(planned_all=planned, **base)
    assert any("staged source window missing" in item for item in missing["warnings"])

    mismatched = [replace(task, input_checksum="0" * 64, input_bytes=1) for task in planned]
    unpublished = build_report(planned_all=mismatched, **{**base, "selected": mismatched})
    assert any(
        "does not match the published pilot record" in item for item in unpublished["warnings"]
    )


def test_resume_rejects_changed_partition_plan_and_allows_wider_selection(tmp_path: Path):
    cache = stage(tmp_path)
    args = base_args(tmp_path, "plan-change", cache)
    assert cli(*args, "--limit", "1")[0] == 0

    changed = list(args)
    changed[changed.index("--cells-per-task") + 1] = "8"
    code, _, stderr = cli(*changed, "--limit", "1", "--resume")
    assert code == 2
    assert "do not match the current plan" in stderr

    # A wider selection of the same plan adds the remaining tasks without recomputing.
    code, payload, _ = cli(*args, "--resume")
    assert code == 0
    assert payload["bounds"]["last_counts"] == {
        "succeeded": 2,
        "failed": 0,
        "skipped": 1,
        "no_coverage": 0,
        "invalid": 0,
    }
