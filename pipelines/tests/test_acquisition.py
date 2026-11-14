from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from analog_pipelines.acquisition.executor import run_plan, validate_run
from analog_pipelines.acquisition.models import (
    AcquisitionPlan,
    AcquisitionTask,
    LocalAsset,
    PlanArea,
    SourceMetadata,
    SourceQuery,
    SourceRecord,
    ValidationReport,
    write_json,
)
from analog_pipelines.acquisition.planner import build_plan, load_areas
from analog_pipelines.acquisition.providers import NasaPowerProvider
from analog_pipelines.inventory import inventory_matrix, load_inventory
from analog_pipelines.typed_contracts import (
    SpatialSupport,
    TemporalSupport,
    TypedObservation,
)


def _area(area_id: str = "earth-test", body: str = "Earth") -> PlanArea:
    return PlanArea(
        area_id=area_id,
        display_name=area_id,
        body=body,  # type: ignore[arg-type]
        bbox={"west": 10.0, "south": 45.0, "east": 10.02, "north": 45.02},
        crs="EPSG:4326",
        coordinate_frame="earth_wgs84",
    )


def test_inventory_and_plan_are_machine_readable():
    assert len(inventory_matrix(load_inventory())) == 18
    areas = load_areas(body="Earth")
    plan = build_plan(
        scope="pilot",
        areas=areas[:2],
        dimensions=["D05"],
        bodies=["Earth"],
        provider_ids=["nasa-power"],
        start="2024-01-01",
        end="2024-01-03",
    )
    assert plan.totals["tasks"] == 2
    assert all(task.dimension == "D05" for task in plan.tasks)
    assert all("ALLSKY_SFC_SW_DWN" in task.parameters["parameters"] for task in plan.tasks)
    assert len({task.task_id for task in plan.tasks}) == 2
    assert plan.notes == []


def test_nasa_power_process_preserves_timeseries_and_fill(tmp_path: Path):
    provider = NasaPowerProvider()
    area = _area()
    plan = build_plan(
        scope="pilot",
        areas=[area],
        dimensions=["D05"],
        bodies=["Earth"],
        provider_ids=["nasa-power"],
        start="2024-01-01",
        end="2024-01-03",
    )
    task = plan.tasks[0]
    payload = {
        "properties": {
            "parameter": {
                "ALLSKY_SFC_SW_DWN": {
                    "20240101": 1.5,
                    "20240102": -999.0,
                    "20240103": 2.5,
                }
            }
        }
    }
    asset_path = tmp_path / "power.json"
    asset_path.write_text(json.dumps(payload), encoding="utf-8")
    asset = LocalAsset(
        task_id=task.task_id,
        path=str(asset_path),
        sha256="0" * 64,
        bytes=asset_path.stat().st_size,
    )
    observations = provider.process(asset, task)
    assert len(observations) == 1
    observation = observations[0]
    assert observation.dimension_code == "D05"
    assert observation.state == "detected"
    assert observation.score_role == "context_only"
    assert observation.value["timestamps"] == ["2024-01-01", "2024-01-03"]
    assert observation.value["values"] == [1.5, 2.5]
    assert observation.value["missing_count"] == 1
    assert observation.temporal_support.start == "2024-01-01"
    assert observation.temporal_support.end == "2024-01-03"
    assert observation.spatial_support.kind == "point"
    assert observation.spatial_support.source_resolution_m == 50_000.0
    assert provider.validate(observations).ok


class FakeProvider:
    provider_id = "fake"

    def discover(self, query: SourceQuery) -> list[SourceRecord]:
        return []

    def inspect(self, source: SourceRecord) -> SourceMetadata:
        return SourceMetadata(source_id=source.source_id, status="accessible")

    def plan(self, query: SourceQuery, areas: list[PlanArea]) -> list[AcquisitionTask]:
        return []

    def acquire(self, task: AcquisitionTask, cache_dir: Path) -> LocalAsset:
        cache_dir.mkdir(parents=True, exist_ok=True)
        path = cache_dir / f"{task.task_id}.json"
        path.write_text('{"fake": true}\n', encoding="utf-8")
        return LocalAsset(
            task_id=task.task_id,
            path=str(path),
            sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            bytes=path.stat().st_size,
        )

    def process(self, asset: LocalAsset, task: AcquisitionTask) -> list[TypedObservation]:
        return [
            TypedObservation(
                observation_id=f"fake:{task.area.area_id}",
                area_id=task.area.area_id,
                body="Earth",
                dimension_code="D05",
                feature_id="fake_feature",
                value_kind="numeric",
                state="detected",
                value=1.0,
                unit="1",
                spatial_support=SpatialSupport(kind="unknown"),
                temporal_support=TemporalSupport(kind="unknown"),
                method_version="fake-1.0.0",
                dataset_ids=["fake-dataset"],
                source_asset_ids=[f"fake:{task.area.area_id}:{task.task_id}"],
                score_role="evidence_only",
            )
        ]

    def validate(self, observations: list[TypedObservation]) -> ValidationReport:
        return ValidationReport(ok=True, checked=len(observations))


def _fake_plan(tmp_path: Path) -> Path:
    task = AcquisitionTask(
        task_id="fake-task-1",
        provider_id="fake",
        source_id="fake-source",
        body="Earth",
        dimension="D05",
        area=_area(),
        endpoint="local://fake",
        method_version="fake-1.0.0",
        dataset_id="fake-dataset",
    )
    plan = AcquisitionPlan(scope="test", tasks=[task])
    path = tmp_path / "plan.json"
    write_json(path, plan.model_dump(mode="json"))
    return path


def test_executor_resume_checks_asset_integrity(tmp_path: Path):
    plan_path = _fake_plan(tmp_path)
    run_dir = tmp_path / "run"
    providers = {"fake": FakeProvider()}

    first = run_plan(
        plan_path,
        run_dir=run_dir,
        cache_dir=tmp_path / "cache",
        providers=providers,
        resume=True,
    )
    assert first["counts"]["succeeded"] == 1
    second = run_plan(
        plan_path,
        run_dir=run_dir,
        cache_dir=tmp_path / "cache",
        providers=providers,
        resume=True,
    )
    assert second["counts"]["skipped"] == 1
    assert validate_run(run_dir).ok

    checkpoint = json.loads((run_dir / "checkpoints/fake-task-1.json").read_text())
    Path(checkpoint["asset"]["path"]).write_text("corrupted", encoding="utf-8")
    assert not validate_run(run_dir).ok
    third = run_plan(
        plan_path,
        run_dir=run_dir,
        cache_dir=tmp_path / "cache",
        providers=providers,
        resume=True,
    )
    assert third["counts"]["succeeded"] == 1
    assert validate_run(run_dir).ok


def test_planner_rejects_unknown_provider():
    with pytest.raises(ValueError, match="unknown provider"):
        build_plan(
            scope="test",
            areas=[_area()],
            dimensions=["D05"],
            bodies=["Earth"],
            provider_ids=["missing"],
        )
