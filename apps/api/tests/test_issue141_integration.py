from __future__ import annotations

import math
from pathlib import Path

import pytest
from analog_api.config import Settings
from analog_api.main import create_app
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def client(tmp_path_factory: pytest.TempPathFactory):
    tmp = tmp_path_factory.mktemp("issue141")
    settings = Settings(
        database_url=f"sqlite+pysqlite:///{tmp / 'issue141.db'}",
        manifest_dir=str(ROOT / "pipelines/earth/manifests"),
        manifest_dir_planetary=str(ROOT / "pipelines/planetary/manifests"),
        manifest_dir_context=str(ROOT / "pipelines/worldcover/manifests"),
        seed_include_synthetic=False,
        seed_include_typed=True,
    )
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def _post(client: TestClient, payload: dict):
    response = client.post("/api/v1/search/multidimensional", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def test_dimension_weighted_rms_is_feature_count_invariant(client: TestClient):
    # D01 has 1 feature here, D06 has 2 features. Flat legacy distance is biased by feature count;
    # dimension-weighted RMS with equal dimension weights should equal sqrt(mean(q_D01,q_D06)).
    targets = {
        "slope_mean_deg": {"value": 10, "weight": 1},
        "soil_clay_0_5cm_mean": {"value": 200, "weight": 1},
        "soil_sand_0_5cm_mean": {"value": 300, "weight": 1},
    }
    body = _post(
        client,
        {
            "targets": targets,
            "dimension_weights": {
                "D01": {"weight": 1, "enabled": True},
                "D06": {"weight": 1, "enabled": True},
            },
            "scoring_version": "dimension-weighted-rms-v1",
            "limit": 5,
        },
    )
    assert body["scoring"]["version"] == "dimension-weighted-rms-v1"
    assert "q_d = Σ(v_df * z_df^2) / Σ(v_df)" in body["scoring"]["formula"]
    assert body["scoring"]["active_dimensions"] == ["D01", "D06"]

    row = body["results"][0]
    by_feature = {item["feature_id"]: item for item in row["contributions"]}
    z_d01 = ((by_feature["slope_mean_deg"]["candidate"] - 10.0) / 10.0) ** 2
    z_clay = ((by_feature["soil_clay_0_5cm_mean"]["candidate"] - 200.0) / 200.0) ** 2
    z_sand = ((by_feature["soil_sand_0_5cm_mean"]["candidate"] - 300.0) / 200.0) ** 2
    q_d01 = z_d01
    q_d06 = (z_clay + z_sand) / 2.0
    expected = math.sqrt((q_d01 + q_d06) / 2.0)
    assert row["distance"] == pytest.approx(expected)
    dims = row["dimension_contributions"]
    assert dims["D01"]["mean_squared_normalized_delta"] == pytest.approx(q_d01)
    assert dims["D06"]["mean_squared_normalized_delta"] == pytest.approx(q_d06)
    assert dims["D01"]["feature_count"] == 1
    assert dims["D06"]["feature_count"] == 2
    # legacy flat formula would differ when feature counts differ
    legacy = _post(client, {"targets": targets, "limit": 5})
    assert legacy["scoring"]["version"] == "feature-weighted-euclidean-v1"
    assert legacy["results"][0]["distance"] != pytest.approx(expected)


def test_dimension_enabled_flags_and_zero_weights_do_not_exclude(client: TestClient):
    disabled = _post(
        client,
        {
            "targets": {
                "slope_mean_deg": {"value": 10, "weight": 1},
                "soil_clay_5_15cm_mean": {"value": 200, "weight": 1},
            },
            "dimension_weights": {"D06": {"weight": 0, "enabled": False}},
            "limit": 5,
        },
    )
    assert disabled["scoring"]["version"] == "dimension-weighted-rms-v1"
    assert disabled["eligible_candidate_count"] == 113
    assert disabled["excluded_candidate_count"] == 0
    assert all("soil_clay_5_15cm_mean" not in row["valid_features"] for row in disabled["results"])

    enabled_positive = _post(
        client,
        {
            "targets": {
                "slope_mean_deg": {"value": 10, "weight": 1},
                "soil_clay_5_15cm_mean": {"value": 200, "weight": 1},
            },
            "dimension_weights": {"D06": {"weight": 1, "enabled": True}},
            "limit": 5,
        },
    )
    assert enabled_positive["eligible_candidate_count"] == 5
    assert enabled_positive["excluded_candidate_count"] == 108


def test_real_d01_and_earth_d06_example_returns_contributions(client: TestClient):
    # Real seeded data: Earth D01 terrain + D06 SoilGrids 0-5cm mean at earth locations.
    body = _post(
        client,
        {
            "targets": {
                "slope_mean_deg": {"value": 8, "weight": 1},
                "soil_bdod_0_5cm_mean": {"value": 140, "weight": 1},
            },
            "dimension_weights": {
                "D01": {"weight": 1, "enabled": True},
                "D06": {"weight": 1, "enabled": True},
            },
            "limit": 3,
        },
    )
    assert body["eligible_candidate_count"] >= 1
    first = body["results"][0]
    by_dim = first["dimension_contributions"]
    assert "D01" in by_dim and "D06" in by_dim
    by_feature = {item["feature_id"]: item for item in first["contributions"]}
    assert by_feature["slope_mean_deg"]["dimension_id"] == "D01"
    assert by_feature["soil_bdod_0_5cm_mean"]["dimension_id"] == "D06"
    assert by_feature["soil_bdod_0_5cm_mean"]["observation_id"].startswith("soilgrids:")
    assert first["distance"] >= 0


def test_location_coverage_status_contract_earth_and_moon(client: TestClient):
    earth = client.get(
        "/api/v1/coverage/location", params={"body": "Earth", "lat": 24.47291419, "lon": 54.378419}
    )
    assert earth.status_code == 200, earth.text
    payload = earth.json()
    assert payload["contract_version"] == "location-coverage-status-1.0.0"
    assert payload["actual_indexed_coverage"]["global_coverage_claimed"] is False
    assert "D01" in payload["dimensions"]
    assert "D06" in payload["dimensions"]
    # At least declared sources/manifests are exposed for D06 Earth.
    assert payload["dimensions"]["D06"]["declared_sources"]

    moon = client.get(
        "/api/v1/coverage/location", params={"body": "Moon", "lat": -89.5, "lon": 0.0}
    )
    assert moon.status_code == 200, moon.text
    moon_payload = moon.json()
    assert moon_payload["query"]["body"] == "Moon"
    assert set(moon_payload["dimensions"].keys()) >= {"D01", "D02", "D03", "D04", "D05", "D06"}
    assert isinstance(moon_payload["manifest_adapters"], list)


def test_location_coverage_validation(client: TestClient):
    missing = client.get("/api/v1/coverage/location", params={"body": "Earth"})
    assert missing.status_code == 422
    bad = client.get(
        "/api/v1/coverage/location",
        params={"body": "Earth", "lat": 1, "lon": 2, "west": 1, "south": 1, "east": 2, "north": 2},
    )
    assert bad.status_code == 422
