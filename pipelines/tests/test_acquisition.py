from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pytest
import rasterio
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
from analog_pipelines.acquisition.providers_soilgrids import SoilGridsProvider
from analog_pipelines.inventory import inventory_matrix, load_inventory
from analog_pipelines.typed_contracts import (
    SpatialSupport,
    TemporalSupport,
    TypedObservation,
)
from rasterio.transform import from_origin


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


def test_soilgrids_plan_and_process_preserve_depth_and_metric_resolution(tmp_path: Path):
    provider = SoilGridsProvider()
    area = _area()
    plan = build_plan(
        scope="pilot",
        areas=[area],
        dimensions=["D06"],
        bodies=["Earth"],
        provider_ids=["soilgrids-wcs"],
        parameters={"properties": "clay", "depths": "0-5cm", "statistics": "mean,Q0.05,Q0.95"},
    )
    assert len(plan.tasks) == 3
    assert all("CoverageId=clay_0-5cm_" in task.endpoint for task in plan.tasks)
    task = next(task for task in plan.tasks if task.parameters["statistic"] == "mean")
    path = tmp_path / "clay.tif"
    array = np.array([[131, 132], [133, 134]], dtype="int16")
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=2,
        width=2,
        count=1,
        dtype="int16",
        crs="EPSG:4326",
        transform=from_origin(10.0, 45.02, 0.01, 0.01),
    ) as dst:
        dst.write(array, 1)
    asset = LocalAsset(
        task_id=task.task_id,
        path=str(path),
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        bytes=path.stat().st_size,
        content_type="image/tiff",
    )
    observations = provider.process(asset, task)
    assert len(observations) == 1
    observation = observations[0]
    assert observation.dimension_code == "D06"
    assert observation.value == pytest.approx(132.5)
    assert observation.unit == "g kg-1"
    assert observation.spatial_support.resolution_m == 250.0
    assert observation.spatial_support.source_resolution_m == 250.0
    assert observation.depth_support.kind == "depth_interval"
    assert observation.depth_support.min_m == 0.0
    assert observation.depth_support.max_m == 0.05
    assert observation.score_role == "evidence_only"
    assert provider.validate(observations).ok


def test_nasa_power_thermal_plan_and_process_are_d03_context(tmp_path: Path):
    provider = NasaPowerProvider()
    plan = build_plan(
        scope="pilot",
        areas=[_area()],
        dimensions=["D03"],
        bodies=["Earth"],
        provider_ids=["nasa-power"],
        start="2024-01-01",
        end="2024-01-02",
    )
    assert len(plan.tasks) == 1
    task = plan.tasks[0]
    assert task.dimension == "D03"
    assert "T2M" in task.parameters["parameters"]
    payload = {
        "properties": {
            "parameter": {
                "T2M": {"20240101": 10.0, "20240102": 12.0},
                "T2M_MAX": {"20240101": 15.0, "20240102": 17.0},
            }
        }
    }
    path = tmp_path / "thermal.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    asset = LocalAsset(
        task_id=task.task_id,
        path=str(path),
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        bytes=path.stat().st_size,
    )
    observations = provider.process(asset, task)
    assert {item.dimension_code for item in observations} == {"D03"}
    assert {item.score_role for item in observations} == {"context_only"}
    assert {item.unit for item in observations} == {"degree_Celsius"}
    assert all(item.value["values"] for item in observations)
    assert provider.validate(observations).ok


def test_nasa_power_water_plan_and_process_are_d04_context(tmp_path: Path):
    provider = NasaPowerProvider()
    plan = build_plan(
        scope="pilot",
        areas=[_area()],
        dimensions=["D04"],
        bodies=["Earth"],
        provider_ids=["nasa-power"],
        start="2024-01-01",
        end="2024-01-02",
    )
    assert len(plan.tasks) == 1
    task = plan.tasks[0]
    assert task.dimension == "D04"
    assert task.parameters["community"] == "AG"
    assert "SNODP" in task.parameters["parameters"]
    payload = {
        "properties": {
            "parameter": {
                "GWETTOP": {"20240101": 0.4, "20240102": 0.5},
                "SNODP": {"20240101": 0.0, "20240102": 2.5},
            }
        }
    }
    path = tmp_path / "water.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    asset = LocalAsset(
        task_id=task.task_id,
        path=str(path),
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        bytes=path.stat().st_size,
    )
    observations = provider.process(asset, task)
    assert {item.dimension_code for item in observations} == {"D04"}
    assert {item.score_role for item in observations} == {"context_only"}
    assert {item.feature_id for item in observations} == {
        "surface_soil_wetness_fraction",
        "snow_depth_cm",
    }
    snow = next(item for item in observations if item.feature_id == "snow_depth_cm")
    assert snow.value["values"] == [0.0, 2.5]
    assert any("not_confirmed_subsurface_ice" in item.quality_flags for item in observations)
    assert provider.validate(observations).ok
