from __future__ import annotations

from pathlib import Path

import pytest
from analog_api.config import Settings
from analog_api.main import create_app
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[3]

EARTH_BBOX = {
    "west": 10.4470654,
    "south": 46.46444404,
    "east": 10.47332516,
    "north": 46.48258667,
}
MOON_BBOX = {"west": -157.3, "south": -89.82, "east": -156.8, "north": -89.70}
MARS_BBOX = {"west": 77.35, "south": 18.20, "east": 77.39, "north": 18.24}
ATACAMA_BBOX = {
    "west": -68.40973566,
    "south": -23.5089791,
    "east": -68.39026303,
    "north": -23.49102032,
}


@pytest.fixture(scope="module")
def client(tmp_path_factory: pytest.TempPathFactory):
    tmp = tmp_path_factory.mktemp("aoi-evidence")
    settings = Settings(
        database_url=f"sqlite+pysqlite:///{tmp / 'aoi-evidence.db'}",
        manifest_dir=str(ROOT / "pipelines/earth/manifests"),
        manifest_dir_planetary=str(ROOT / "pipelines/planetary/manifests"),
        manifest_dir_context=str(ROOT / "pipelines/worldcover/manifests"),
        seed_include_synthetic=False,
        seed_include_typed=True,
    )
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def _coverage(client: TestClient, **params) -> dict:
    response = client.get("/api/v1/coverage/location", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def _records(payload: dict) -> list[dict]:
    return payload["records"]


def test_earth_bbox_returns_exact_joined_rankable_d01_and_d06_records(client: TestClient):
    payload = _coverage(client, body="Earth", include_records="true", **EARTH_BBOX)
    assert payload["contract_version"] == "location-coverage-status-1.0.0"
    assert payload["evidence_query"]["contract_version"] == "aoi-evidence-query-1.0.0"
    assert payload["evidence_query"]["include_records"] is True
    assert payload["actual_indexed_coverage"]["global_coverage_claimed"] is False
    assert payload["actual_indexed_coverage"]["join_audit"]["proximity_join_used"] is False
    assert (
        payload["actual_indexed_coverage"]["join_audit"][
            "typed_areas_exactly_matching_fine_candidate"
        ]
        == 113
    )
    assert payload["query"]["bbox_contract"]["coordinate_system"] == "EPSG:4326"
    assert payload["query"]["bbox_contract"]["earth_wgs84"] is True

    assert set(payload["dimensions"]) == {"D01", "D02", "D03", "D04", "D05", "D06"}
    assert payload["dimensions"]["D01"]["roles"]["score"] is True
    assert payload["dimensions"]["D06"]["roles"]["score"] is True

    records = _records(payload)
    assert records
    d01 = next(
        record
        for record in records
        if record["observation_id"] == "legacy:earth-alps-r1c1:slope_mean_deg"
    )
    assert d01["area_id"] == "earth-alps-r1c1"
    assert d01["body"] == "Earth"
    assert d01["dimension_code"] == "D01"
    assert d01["feature_id"] == "slope_mean_deg"
    assert d01["state"] == "detected"
    assert d01["record_status"] == "available"
    assert d01["roles"]["score"] is True
    assert d01["roles"]["filter"] is True
    assert d01["join"]["kind"] == "exact_candidate"
    assert d01["join"]["rankable"] is True
    assert d01["join"]["proximity_join"] is False
    assert d01["dataset_ids"] == ["copernicus-dem-glo90"]
    assert d01["resolution"]["analysis_resolution_m"] == 90.0
    assert d01["value"] is not None
    assert d01["missing_reason"] is None

    d06 = next(
        record
        for record in records
        if record["dimension_code"] == "D06" and record["feature_id"] == "soil_clay_0_5cm_mean"
    )
    assert d06["area_id"] == "earth-alps-r1c1"
    assert d06["state"] == "detected"
    assert d06["record_status"] == "available"
    assert d06["score_role"] == "evidence_only"  # stored source role is preserved
    assert d06["roles"]["score"] is True  # gated query role is explicit and distinct
    assert d06["join"]["rankable"] is True
    assert d06["dataset_ids"] == ["soilgrids-250m-v2"]
    assert d06["resolution"]["native_resolution_m"] == 250.0
    assert d06["depth_support"]["min_m"] == 0.0
    assert d06["depth_support"]["max_m"] == 0.05
    assert isinstance(d06["value"], float)
    assert d06["missing_reason"] is None

    # No typed observation is copied from another AOI, and land cover is not a D01-D06 row.
    typed_areas = {
        record["area_id"] for record in records if record["record_source"] == "typed_observation"
    }
    assert typed_areas == {"earth-alps-r1c1"}
    assert not any((record["feature_id"] or "").startswith("landcover_") for record in records)
    assert payload["context_features"]
    assert all(feature["score_role"] == "context_only" for feature in payload["context_features"])
    assert all(
        feature["feature_id"].startswith("landcover_") for feature in payload["context_features"]
    )


def test_moon_bbox_uses_planetocentric_contract_and_exact_reference_roles(client: TestClient):
    payload = _coverage(client, body="Moon", include_records="true", **MOON_BBOX)
    contract = payload["query"]["bbox_contract"]
    assert contract["coordinate_system"] == "IAU_MOON"
    assert contract["frame"] == "lunar_planetocentric"
    assert contract["latitude_kind"] == "planetocentric"
    assert contract["earth_wgs84"] is False
    assert contract["geographic_epsg"] == 104903
    assert contract["geographic_epsg_is_epsg_registry"] is False
    assert payload["actual_indexed_coverage"]["unlocatable_typed_area_ids"] == []

    records = _records(payload)
    assert records
    assert {record["body"] for record in records} == {"Moon"}

    d01 = next(
        record
        for record in records
        if record["dimension_code"] == "D01"
        and record["area_id"] == "moon-shackleton-rim-site04-roi1"
    )
    assert d01["join"]["kind"] == "exact_reference"
    assert d01["join"]["rankable"] is True
    assert d01["record_status"] == "available"

    d05 = next(
        record
        for record in records
        if record["dimension_code"] == "D05"
        and record["area_id"] == "moon-shackleton-rim-site04-roi1"
    )
    assert d05["feature_id"] in {"solar_incidence_deg", "solar_elevation_deg"}
    assert d05["state"] == "detected"
    assert d05["score_role"] == "context_only"
    assert d05["roles"]["context"] is True
    assert d05["roles"]["score"] is False
    assert d05["record_status"] == "evidence" or d05["record_status"] == "context"
    assert d05["join"]["kind"] == "exact_reference"
    assert d05["join"]["rankable"] is False
    assert d05["join"]["viewable_only"] is True

    # Projected thermal/D04 envelopes are transformed, not silently dropped or proxied.
    assert any(record["dimension_code"] == "D03" for record in records)
    assert any(record["dimension_code"] == "D04" for record in records)
    no_exact = [
        record
        for record in records
        if record["dimension_code"] == "D03" and record["join"]["kind"] == "no_exact_join"
    ]
    assert no_exact
    assert all(record["join"]["viewable_only"] is True for record in no_exact)
    assert all(record["join"]["proximity_join"] is False for record in no_exact)


def test_mars_bbox_uses_planetocentric_contract_and_exact_reference_roles(client: TestClient):
    payload = _coverage(client, body="Mars", include_records="true", **MARS_BBOX)
    contract = payload["query"]["bbox_contract"]
    assert contract["coordinate_system"] == "IAU_MARS"
    assert contract["frame"] == "mars_planetocentric"
    assert contract["latitude_kind"] == "planetocentric"
    assert contract["earth_wgs84"] is False
    assert contract["geographic_epsg"] == 104905
    assert payload["actual_indexed_coverage"]["unlocatable_typed_area_ids"] == []

    records = _records(payload)
    assert records
    assert {record["body"] for record in records} == {"Mars"}

    d01 = next(
        record
        for record in records
        if record["dimension_code"] == "D01" and record["area_id"] == "mars-jezero-ctx-window-01"
    )
    assert d01["join"]["kind"] == "exact_reference"
    assert d01["join"]["rankable"] is True

    d05 = next(
        record
        for record in records
        if record["dimension_code"] == "D05" and record["area_id"] == "mars-jezero-ctx-window-01"
    )
    assert d05["join"]["kind"] == "exact_reference"
    assert d05["join"]["rankable"] is False
    assert d05["score_role"] == "context_only"

    thermal = next(
        record
        for record in records
        if record["dimension_code"] == "D03"
        and record["area_id"] == "thermal-mars-jezero-ctx-window-01"
    )
    assert thermal["feature_id"] == "thermal_inertia"
    assert thermal["join"]["kind"] == "no_exact_join"
    assert thermal["join"]["viewable_only"] is True
    assert thermal["record_status"] == "evidence"


def test_record_filters_are_explicit_and_never_assume_time_or_depth(client: TestClient):
    d06 = _coverage(
        client,
        body="Earth",
        include_records="true",
        dimension="D06",
        feature_id="soil_clay_0_5cm_mean",
        state="detected",
        dataset_id="soilgrids-250m-v2",
        depth_max_m=0.05,
        **EARTH_BBOX,
    )
    assert d06["evidence_query"]["matched_observation_count"] == 1
    assert len(d06["records"]) == 1
    record = d06["records"][0]
    assert record["feature_id"] == "soil_clay_0_5cm_mean"
    assert record["join"]["rankable"] is True
    assert record["depth_support"]["max_m"] == 0.05

    time_window = _coverage(
        client,
        body="Earth",
        include_records="true",
        dimension="D03",
        time_start="2024-01-02",
        time_end="2024-01-03",
        **ATACAMA_BBOX,
    )
    assert time_window["evidence_query"]["matched_observation_count"] == 4
    assert all(
        record["temporal_support"]["start"] == "2024-01-01"
        and record["temporal_support"]["end"] == "2024-01-07"
        for record in time_window["records"]
    )
    assert time_window["evidence_query"]["filter_audit"]["excluded_observation_count"] > 0

    outside = _coverage(
        client,
        body="Earth",
        include_records="true",
        dimension="D03",
        time_start="2025-01-01",
        time_end="2025-01-02",
        **ATACAMA_BBOX,
    )
    assert outside["records"] == []
    assert outside["evidence_query"]["matched_observation_count"] == 0
    assert (
        "temporal_interval_before_query_window"
        in outside["evidence_query"]["filter_audit"]["by_reason"]
    )

    deep = _coverage(
        client,
        body="Earth",
        include_records="true",
        dimension="D06",
        depth_min_m=0.3001,
        depth_max_m=0.60,
        **ATACAMA_BBOX,
    )
    assert deep["records"]
    assert all(
        record["depth_support"]["min_m"] == 0.30 and record["depth_support"]["max_m"] == 0.60
        for record in deep["records"]
    )

    # D06 has no declared time selector; a time filter must not invent one.
    d06_time = _coverage(
        client,
        body="Earth",
        include_records="true",
        dimension="D06",
        time_start="2024-01-01",
        time_end="2024-01-02",
        **ATACAMA_BBOX,
    )
    assert d06_time["records"] == []
    assert "temporal_support_unknown" in d06_time["evidence_query"]["filter_audit"]["by_reason"]


def test_no_coverage_and_no_data_are_explicit_never_zero_filled(client: TestClient):
    no_coverage = _coverage(
        client,
        body="Moon",
        include_records="true",
        dimension="D03",
        state="no_coverage",
        west=-70.5,
        south=-88.80,
        east=-66.4,
        north=-88.60,
    )
    assert no_coverage["evidence_query"]["matched_observation_count"] >= 1
    assert no_coverage["records"]
    for record in no_coverage["records"]:
        assert record["state"] == "no_coverage"
        assert record["record_status"] == "no_coverage"
        assert record["value"] is None
        assert record["missing_reason"] == "provider_declares_no_coverage"
        assert record["join"]["viewable_only"] is True

    unknown = _coverage(
        client,
        body="Moon",
        include_records="true",
        dimension="D04",
        state="unknown",
        west=31.4,
        south=-85.6,
        east=32.1,
        north=-84.9,
    )
    assert unknown["records"]
    for record in unknown["records"]:
        assert record["state"] == "unknown"
        assert record["record_status"] == "no_data"
        assert record["value"] is None
        assert record["missing_reason"] == "state_unknown_not_zero_filled"


def test_bbox_contract_rejects_invalid_and_reports_crossing_polar_queries(client: TestClient):
    invalid_cases = [
        (
            {"body": "Earth", "west": 0, "south": -91, "east": 1, "north": 1},
            "invalid_bbox_latitude",
        ),
        ({"body": "Earth", "west": 0, "south": 2, "east": 1, "north": 1}, "invalid_bbox"),
        (
            {"body": "Earth", "west": 200, "south": 0, "east": 201, "north": 1},
            "invalid_bbox_longitude",
        ),
        ({"body": "Earth", "west": 1, "south": 0, "east": 1, "north": 1}, "invalid_bbox"),
        (
            {"body": "Earth", "west": 0, "south": 0, "east": 1, "north": 1, "dimension": "D99"},
            "invalid_dimension",
        ),
        (
            {
                "body": "Earth",
                "west": 0,
                "south": 0,
                "east": 1,
                "north": 1,
                "time_start": "2024-01-02",
                "time_end": "2024-01-01",
            },
            "invalid_time_window",
        ),
        (
            {
                "body": "Earth",
                "west": 0,
                "south": 0,
                "east": 1,
                "north": 1,
                "depth_min_m": 2,
                "depth_max_m": 1,
            },
            "invalid_depth_window",
        ),
    ]
    for params, code in invalid_cases:
        response = client.get("/api/v1/coverage/location", params=params)
        assert response.status_code == 422, (params, response.text)
        assert response.json()["error"]["code"] == code

    missing_component = client.get(
        "/api/v1/coverage/location",
        params={"body": "Earth", "west": 0, "south": 0, "east": 1},
    )
    assert missing_component.status_code == 422
    assert missing_component.json()["error"]["code"] == "validation_error"

    # 0..360 client convention is normalised to signed planetocentric coordinates.
    moon_360 = _coverage(
        client,
        body="Moon",
        west=200,
        south=-89.82,
        east=203,
        north=-89.70,
        bbox_lon_convention="360",
        include_records="true",
    )
    assert moon_360["query"]["bbox"]["west"] == -160.0
    assert moon_360["query"]["bbox"]["east"] == -157.0
    assert moon_360["records"]

    dateline = _coverage(
        client,
        body="Moon",
        west=170,
        south=-90,
        east=-170,
        north=-89.9,
        include_records="true",
    )
    assert dateline["query"]["bbox"]["crosses_antimeridian"] is True
    assert dateline["query"]["bbox_contract"]["antimeridian"]["supported"] is True
    assert len(dateline["query"]["bbox_contract"]["antimeridian"]["split_parts"]) == 2

    polar = _coverage(
        client,
        body="Moon",
        west=-157.3,
        south=-89.82,
        east=-156.8,
        north=-89.70,
        include_records="true",
    )
    assert polar["query"]["bbox_contract"]["polar"]["supported"] is True
    assert polar["query"]["bbox_contract"]["polar"]["latitude_bounds_deg"] == [-90.0, 90.0]


def test_default_response_remains_backward_compatible(client: TestClient):
    payload = _coverage(client, body="Earth", lat=24.47291419, lon=54.378419)
    assert payload["contract_version"] == "location-coverage-status-1.0.0"
    assert set(payload) >= {
        "contract_version",
        "registry_version",
        "data_version",
        "query",
        "actual_indexed_coverage",
        "dimensions",
        "context_features",
        "manifest_adapters",
        "limitations",
    }
    assert payload["records"] == []
    assert payload["evidence_query"]["include_records"] is False
    assert payload["evidence_query"]["returned_observation_count"] == 0
    assert set(payload["dimensions"]) == {"D01", "D02", "D03", "D04", "D05", "D06"}
    assert payload["actual_indexed_coverage"]["global_coverage_claimed"] is False
