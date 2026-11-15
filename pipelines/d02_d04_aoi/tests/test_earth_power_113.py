"""Offline checks for the bounded 113-candidate Earth POWER D03/D04/D05 release.

Everything here reads committed JSON/JSONL files and re-runs the offline
rebuild path. The single optional network test is gated by
``ANALOG_RUN_NETWORK_TESTS=1`` and never runs in CI.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import os
import shutil
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
RELEASE = ROOT / "pipelines/d02_d04_aoi/earth_power_113"
EARTH_MANIFEST = ROOT / "pipelines/earth/manifests/copernicus_glo90_earth.json"
START = "2024-01-01"
END = "2024-01-07"
DIMENSIONS = ("D03", "D04", "D05")
EXPECTED_FEATURES = {
    "D03": {
        "surface_air_temperature_2m_c",
        "surface_air_temperature_2m_max_c",
        "surface_air_temperature_2m_min_c",
        "surface_skin_temperature_c",
    },
    "D04": {
        "precipitation_mm_day",
        "root_zone_soil_wetness_fraction",
        "snow_depth_cm",
        "surface_soil_wetness_fraction",
    },
    "D05": {
        "surface_shortwave_irradiance_kwh_m2_day",
        "surface_longwave_irradiance_kwh_m2_day",
    },
}
EXPECTED_OBSERVATIONS = 113 * 10


def _read(name: str) -> dict:
    return json.loads((RELEASE / name).read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _builder():
    spec = importlib.util.spec_from_file_location("earth_power_113_build", RELEASE / "build.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def plan() -> dict:
    return _read("plan.json")


@pytest.fixture(scope="module")
def manifest() -> dict:
    return _read("manifest.json")


@pytest.fixture(scope="module")
def coverage() -> dict:
    return _read("coverage.json")


@pytest.fixture(scope="module")
def failures() -> dict:
    return _read("failures.json")


@pytest.fixture(scope="module")
def assets() -> list[dict]:
    return _read("assets.json")


@pytest.fixture(scope="module")
def observations() -> list[dict]:
    return [
        json.loads(line)
        for line in (RELEASE / "observations.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_plan_targets_every_earth_candidate_in_every_dimension(plan: dict) -> None:
    source = json.loads(EARTH_MANIFEST.read_text(encoding="utf-8"))
    wanted = {item["location_id"] for item in source["candidates"]}
    assert len(wanted) == 113
    by_dimension = Counter(task["dimension"] for task in plan["tasks"])
    assert by_dimension == {"D03": 113, "D04": 113, "D05": 113}
    assert {task["area"]["area_id"] for task in plan["tasks"]} == wanted
    assert plan["scope"] == "earth-power-113"
    assert plan["totals"]["tasks"] == 339
    for task in plan["tasks"]:
        parameters = task["parameters"]
        assert parameters["start"] == "20240101"
        assert parameters["end"] == "20240107"
        assert task["endpoint"].startswith("https://power.larc.nasa.gov/api/temporal/daily/point?")
        if task["dimension"] == "D03":
            assert parameters["parameters"] == "T2M,T2M_MAX,T2M_MIN,TS"
            assert parameters["community"] == "RE"
        elif task["dimension"] == "D04":
            assert parameters["parameters"] == "GWETTOP,GWETROOT,SNODP,PRECTOTCORR"
            assert parameters["community"] == "AG"
        else:
            assert parameters["parameters"] == "ALLSKY_SFC_SW_DWN,ALLSKY_SFC_LW_DWN"
            assert parameters["community"] == "RE"


def test_release_is_complete_for_all_113_candidates(
    manifest: dict, coverage: dict, failures: dict, assets: list[dict]
) -> None:
    assert manifest["source_manifest"]["candidate_count"] == 113
    assert manifest["coverage"]["candidate_count"] == 113
    assert manifest["task_count"] == 339
    assert manifest["requests"] == {"planned": 339, "succeeded": 339, "failed": 0}
    assert manifest["asset_count"] == len(assets) == 339
    assert failures["count"] == 0 and failures["failures"] == []
    assert coverage["candidate_count"] == 113
    assert coverage["not_fetched"] == 0
    for candidate in coverage["candidates"]:
        assert set(candidate["dimensions"]) == set(DIMENSIONS)
        for dimension, states in candidate["dimensions"].items():
            assert set(states) == EXPECTED_FEATURES[dimension]
            assert set(states.values()) <= {"detected", "no_coverage"}


def test_observation_count_matches_expected_113x10(
    manifest: dict, observations: list[dict]
) -> None:
    assert manifest["expected_observation_count"] == EXPECTED_OBSERVATIONS
    assert manifest["observation_count"] == len(observations) == EXPECTED_OBSERVATIONS
    counts = Counter(item["dimension_code"] for item in observations)
    assert counts == {"D03": 452, "D04": 452, "D05": 226}
    keys = {(item["area_id"], item["dimension_code"], item["feature_id"]) for item in observations}
    assert len(keys) == len(observations)
    assert manifest["coverage"]["detected"] == EXPECTED_OBSERVATIONS
    assert manifest["coverage"]["no_coverage"] == 0


def test_every_observation_keeps_context_provenance_and_support(
    observations: list[dict], assets: list[dict]
) -> None:
    asset_ids = {asset["asset_id"] for asset in assets}
    tasks = {task["task_id"]: task for task in _read("plan.json")["tasks"]}
    for item in observations:
        assert item["body"] == "Earth"
        assert item["dimension_code"] in DIMENSIONS
        assert item["feature_id"] in EXPECTED_FEATURES[item["dimension_code"]]
        assert item["state"] == "detected"
        assert item["score_role"] == "context_only"
        assert item["method_version"] == "nasa-power-daily-1.0.0"
        assert item["dataset_ids"] == ["nasa-power-daily-point"]
        assert item["spatial_support"]["kind"] == "point"
        assert item["spatial_support"]["source_resolution_m"] == 50_000.0
        assert "0.5 x 0.625 degree" in item["spatial_support"]["notes"]
        assert item["temporal_support"]["start"] == START
        assert item["temporal_support"]["end"] == END
        assert item["quality_flags"]
        assert "coarse_resolution" in item["quality_flags"]
        assert item["source_asset_ids"] and set(item["source_asset_ids"]) <= asset_ids
        task = tasks[item["source_asset_ids"][0].rsplit(":", 1)[-1]]
        assert task["dimension"] == item["dimension_code"]
        assert task["area"]["area_id"] == item["area_id"]


def test_no_zero_fill_or_fill_sentinel(observations: list[dict]) -> None:
    for item in observations:
        value = item["value"]
        if item["state"] == "no_coverage":
            assert value is None
            continue
        assert isinstance(value, dict)
        values = value["values"]
        assert len(values) + int(value["missing_count"]) == 7
        for number in values:
            assert isinstance(number, (int, float))
            assert math.isfinite(number)
            assert number > -900, "POWER -999 fill sentinel must never be published"
        assert value["timestamps"][0] == START
        assert value["timestamps"][-1] == END


def test_checksums_cover_every_data_file(manifest: dict) -> None:
    checksums = {}
    for line in (RELEASE / "checksums.sha256").read_text(encoding="utf-8").splitlines():
        digest, name = line.split("  ", 1)
        checksums[name] = digest
    assert (
        len(checksums) == 347
    )  # 339 raw + plan/assets/observations/coverage/failures/index/REPORT
    assert checksums["observations.jsonl"] == manifest["observations_sha256"]
    assert checksums["plan.json"] == manifest["plan_sha256"]
    for name, digest in checksums.items():
        assert _sha256(RELEASE / name) == digest, name
    for entry in manifest["raw"]:
        assert checksums[entry["path"]] == entry["sha256"]
        assert (RELEASE / entry["path"]).stat().st_size == entry["bytes"]


def test_offline_rebuild_is_byte_identical(tmp_path: Path) -> None:
    builder = _builder()
    work = tmp_path / "release"
    (work / "raw").mkdir(parents=True)
    for name in ("plan.json", "fetch-index.json"):
        shutil.copyfile(RELEASE / name, work / name)
    for source in sorted((RELEASE / "raw").glob("*.json")):
        shutil.copyfile(source, work / "raw" / source.name)
    builder.materialize(work)
    for name in (
        "observations.jsonl",
        "assets.json",
        "coverage.json",
        "failures.json",
        "manifest.json",
        "REPORT.md",
        "checksums.sha256",
    ):
        assert (work / name).read_bytes() == (RELEASE / name).read_bytes(), name
    result = builder.check(work)
    assert result["ok"] is True, result["problems"]


def test_coverage_is_bounded_and_reports_grid_cell_density(coverage: dict, manifest: dict) -> None:
    assert (
        coverage["distinct_power_cells_approx"]
        == (manifest["coverage"]["distinct_power_cells_approx"])
    )
    # 113 1.98 km windows fall into far fewer ~0.5 degree POWER cells.
    assert 0 < coverage["distinct_power_cells_approx"] < 113
    assert coverage["request_point_max_offset_m"] < 1.0
    assert coverage["power_grid"].startswith("approximately 0.5 degree")
    for candidate in coverage["candidates"]:
        assert abs(candidate["request_point"]["lat"] - candidate["manifest_point"]["lat"]) < 1e-3
        assert abs(candidate["request_point"]["lon"] - candidate["manifest_point"]["lon"]) < 1e-3
        states = [state for values in candidate["dimensions"].values() for state in values.values()]
        assert len(states) == 10


@pytest.mark.network
@pytest.mark.skipif(
    os.environ.get("ANALOG_RUN_NETWORK_TESTS") != "1",
    reason="set ANALOG_RUN_NETWORK_TESTS=1 to hit the NASA POWER API",
)
def test_live_nasa_power_point_matches_committed_response(tmp_path: Path) -> None:
    """One bounded live request; verifies the provider path, not the whole release."""
    from analog_pipelines.acquisition.models import AcquisitionTask
    from analog_pipelines.acquisition.providers import NasaPowerProvider

    plan = _read("plan.json")
    raw_task = next(task for task in plan["tasks"] if task["dimension"] == "D05")
    task = AcquisitionTask.model_validate(raw_task)
    provider = NasaPowerProvider()
    asset = provider.acquire(task, tmp_path)
    observations = provider.process(asset, task)
    assert {item.feature_id for item in observations} == EXPECTED_FEATURES["D05"]
    for item in observations:
        assert item.state == "detected"
        assert item.score_role == "context_only"
        assert len(item.value["timestamps"]) == 7
        assert all(math.isfinite(value) for value in item.value["values"])
    assert Path(asset.path).is_file()
