from __future__ import annotations

from pathlib import Path

import pytest
from analog_api.config import Settings
from analog_api.main import create_app
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def client(tmp_path_factory: pytest.TempPathFactory):
    tmp = tmp_path_factory.mktemp("issue160")
    settings = Settings(
        database_url=f"sqlite+pysqlite:///{tmp / 'issue160.db'}",
        manifest_dir=str(ROOT / "pipelines/earth/manifests"),
        manifest_dir_planetary=str(ROOT / "pipelines/planetary/manifests"),
        manifest_dir_context=str(ROOT / "pipelines/worldcover/manifests"),
        seed_include_synthetic=False,
        seed_include_typed=True,
    )
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def _multidim(client: TestClient, payload: dict) -> dict:
    response = client.post("/api/v1/search/multidimensional", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def test_unified_aoi_query_earth_bbox_reports_d01_d06_statuses_and_records(client: TestClient):
    # earth-alps-r1c1 is a known seeded Earth window with exact-ID joined D06 SoilGrids records.
    loc = client.get("/api/v1/locations/earth-alps-r1c1").json()
    bbox = loc["bbox"]
    response = client.get(
        "/api/v1/coverage/aoi",
        params={"body": "Earth", **bbox},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["contract_version"] == "aoi-query-1.0.0"
    assert payload["actual_indexed_coverage"]["global_coverage_claimed"] is False
    assert set(payload["dimensions"].keys()) >= {"D01", "D02", "D03", "D04", "D05", "D06"}
    d01 = payload["dimensions"]["D01"]
    assert d01["intersecting_record_count"] >= 1
    assert any(
        record["feature_id"] == "slope_mean_deg"
        and record["dimension_code"] == "D01"
        and "similarity" in (record.get("legal_roles") or record.get("query_roles") or [])
        and record["source"]["dataset_ids"]
        and record["resolution"]["analysis_resolution_m"] == 90.0
        for record in payload["intersecting_records"]
    )
    d06 = payload["dimensions"]["D06"]
    assert d06["intersecting_record_count"] >= 1
    assert d06["status"] in {"available_or_indexed", "partially_available", "evidence_only"}
    assert payload["handoff"]["join_policy"].startswith("exact area_id")
    assert "proximity" in payload["handoff"]["join_policy"]
    assert any(
        record["dimension_code"] == "D06" and record["area_id"] == "earth-alps-r1c1"
        for record in payload["intersecting_records"]
    )
    # Coarse/context records must carry declared support; land cover remains context only.
    assert isinstance(payload["context_features"], list)
    assert all(
        feature.get("score_role") == "context_only" for feature in payload["context_features"]
    )


def test_unified_aoi_query_moon_and_mars_cover_all_dimensions(client: TestClient):
    moon = client.get("/api/v1/coverage/aoi", params={"body": "Moon", "lat": -89.5, "lon": 0.0})
    assert moon.status_code == 200, moon.text
    moon_payload = moon.json()
    assert moon_payload["query"]["body"] == "Moon"
    assert "planetocentric" in moon_payload["query"]["coordinate_note"]
    assert set(moon_payload["dimensions"].keys()) == {"D01", "D02", "D03", "D04", "D05", "D06"}
    assert all("status" in dim for dim in moon_payload["dimensions"].values())

    mars = client.get(
        "/api/v1/coverage/aoi",
        params={"body": "Mars", "west": 76.0, "south": 17.0, "east": 79.0, "north": 20.0},
    )
    assert mars.status_code == 200, mars.text
    mars_payload = mars.json()
    assert mars_payload["query"]["body"] == "Mars"
    assert set(mars_payload["dimensions"].keys()) == {"D01", "D02", "D03", "D04", "D05", "D06"}
    assert isinstance(mars_payload["manifest_adapters"], list)


def test_d01_d06_weighted_multidim_can_toggle_dimensions_and_change_ranking(client: TestClient):
    base = {
        "targets": {
            "slope_mean_deg": {"value": 8, "weight": 1},
            "soil_clay_0_5cm_mean": {"value": 200, "weight": 1},
        },
        "dimension_weights": {
            "D01": {"weight": 1, "enabled": True},
            "D06": {"weight": 1, "enabled": True},
        },
        "scoring_version": "dimension-weighted-rms-v1",
        "limit": 10,
    }
    both = _multidim(client, base)
    d01_only = _multidim(
        client,
        {
            **base,
            "dimension_weights": {
                "D01": {"weight": 1, "enabled": True},
                "D06": {"weight": 0, "enabled": False},
            },
        },
    )
    d06_heavy = _multidim(
        client,
        {
            **base,
            "dimension_weights": {
                "D01": {"weight": 0.2, "enabled": True},
                "D06": {"weight": 3, "enabled": True},
            },
        },
    )
    assert both["scoring"]["active_dimensions"] == ["D01", "D06"]
    assert d01_only["scoring"]["active_dimensions"] == ["D01"]
    assert d01_only["eligible_candidate_count"] == 113
    assert [r["location_id"] for r in d06_heavy["results"]] != [
        r["location_id"] for r in d01_only["results"]
    ]
    first = both["results"][0]
    by_feature = {item["feature_id"]: item for item in first["contributions"]}
    assert "slope_mean_deg" in by_feature and "soil_clay_0_5cm_mean" in by_feature
    assert "dimension_contributions" in first and "excluded" in both


def test_typed_observations_support_aoi_state_depth_source_filters_and_no_data(client: TestClient):
    # Earth D06 evidence by bbox/state/dataset/source/depth.
    area_id = "earth-alps-r1c1"
    ok = client.get(
        "/api/v1/typed/observations",
        params={
            "dimension": "D06",
            "body": "Earth",
            "area_id": area_id,
            "state": "detected",
            "min_depth_m": 0.0,
            "max_depth_m": 0.05,
            "envelope": True,
        },
    )
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["status"] == "ok"
    assert body["count"] >= 1
    assert all(row["state"] == "detected" for row in body["observations"])
    assert all(row["dimension_code"] == "D06" for row in body["observations"])
    assert {row["score_role"] for row in body["observations"]} <= {"evidence_only", "context_only"}
    assert all(
        (row.get("depth_support") or {}).get("min_m") is not None for row in body["observations"]
    )
    assert "does not compute similarity scores" in " ".join(body.get("notes", [])).lower()

    dataset_id = body["observations"][0]["dataset_ids"][0]
    filtered = client.get(
        "/api/v1/typed/observations",
        params={
            "dimension": "D06",
            "body": "Earth",
            "area_id": area_id,
            "dataset_id": dataset_id,
            "envelope": True,
        },
    ).json()
    assert filtered["status"] == "ok"
    assert all(dataset_id in row["dataset_ids"] for row in filtered["observations"])

    # bbox/point spatial filter for Earth typed areas (AOI evidence query).
    loc = client.get(f"/api/v1/locations/{area_id}").json()
    spatial = client.get(
        "/api/v1/typed/observations",
        params={
            "dimension": "D06",
            "body": "Earth",
            "lat": loc["lat"],
            "lon": loc["lon"],
            "envelope": True,
        },
    ).json()
    assert spatial["status"] == "ok"
    assert any(row["area_id"] == area_id for row in spatial["observations"])

    # Explicit absent state query must not be confused with other states and must report no_data.
    none = client.get(
        "/api/v1/typed/observations",
        params={"dimension": "D02", "body": "Earth", "state": "not_detected", "envelope": True},
    )
    assert none.status_code == 200, none.text
    none_body = none.json()
    assert none_body["status"] == "no_data"
    assert none_body["count"] == 0
    assert none_body["observations"] == []


def test_worldcover_remains_earth_only_land_cover_hard_filter(client: TestClient):
    allowed = client.post(
        "/api/v1/search",
        json={
            "body": "custom",
            "target": {"slope_mean_deg": {"value": 10, "weight": 1}},
            "filters": {"landcover_bare_sparse_fraction": {"min": 0, "max": 1}},
            "limit": 1,
        },
    )
    assert allowed.status_code in {200, 409}, (
        allowed.text
    )  # 409 if no indexed data only; seeded db should allow
    rejected = client.post(
        "/api/v1/search/multidimensional",
        json={
            "targets": {"landcover_bare_sparse_fraction": {"value": 0.5, "weight": 1}},
            "limit": 1,
        },
    )
    assert rejected.status_code == 422, rejected.text
    assert rejected.json()["error"]["code"] in {"feature_role_not_scoreable", "unknown_feature"}
