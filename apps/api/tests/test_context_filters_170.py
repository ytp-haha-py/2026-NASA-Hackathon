"""Issue #170: real D02-D05 controls without fake similarity.

The committed pilots are used as-is: eight Earth fine candidates carry NASA POWER D03/D04/D05
timeseries, and three Earth fine candidates carry a Macrostrat D02 map-unit label. These tests
prove that:

* the ten Earth scalar context fields are registered as range filters, never similarity features;
* a range filter changes the candidate set, reports missing candidates explicitly and never
  zero-fills them;
* the weighted distance and relative ranking are unchanged when only filters are added;
* the filter value is the documented 2024-01-01..2024-01-07 period mean of the stored samples;
* ``geologic_unit`` is an exact source-label category dropdown, not a taxonomy crosswalk;
* Moon/Mars D02-D05 features remain evidence/context only.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest
from analog_api.config import Settings
from analog_api.domain.eligibility import category_labels
from analog_api.main import create_app
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[3]
EARTH_POOL_SIZE = 113
CONTEXT_AOI_COUNT = 8
EARTH_GEOLOGY_COUNT = 3

RANGE_FEATURES: dict[str, tuple[str, str]] = {
    "surface_air_temperature_2m_c": ("D03", "degree_Celsius"),
    "surface_air_temperature_2m_max_c": ("D03", "degree_Celsius"),
    "surface_air_temperature_2m_min_c": ("D03", "degree_Celsius"),
    "surface_skin_temperature_c": ("D03", "degree_Celsius"),
    "precipitation_mm_day": ("D04", "mm day-1"),
    "root_zone_soil_wetness_fraction": ("D04", "1"),
    "snow_depth_cm": ("D04", "cm"),
    "surface_soil_wetness_fraction": ("D04", "1"),
    "surface_shortwave_irradiance_kwh_m2_day": ("D05", "kWh m-2 day-1"),
    "surface_longwave_irradiance_kwh_m2_day": ("D05", "kWh m-2 day-1"),
}
CATEGORY_FEATURE = "geologic_unit"


@pytest.fixture(scope="module")
def client(tmp_path_factory: pytest.TempPathFactory):
    tmp = tmp_path_factory.mktemp("context-filters-170")
    settings = Settings(
        database_url=f"sqlite+pysqlite:///{tmp / 'context-filters.db'}",
        manifest_dir=str(ROOT / "pipelines/earth/manifests"),
        manifest_dir_planetary=str(ROOT / "pipelines/planetary/manifests"),
        manifest_dir_context=str(ROOT / "pipelines/worldcover/manifests"),
        seed_include_synthetic=False,
        seed_include_typed=True,
    )
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def _features(client: TestClient, **params) -> dict[str, dict]:
    response = client.get("/api/v1/search/features", params=params)
    assert response.status_code == 200, response.text
    return {item["feature_id"]: item for item in response.json()["features"]}


def _post(client: TestClient, payload: dict) -> dict:
    response = client.post("/api/v1/search/multidimensional", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def _all_results(client: TestClient, payload: dict) -> list[dict]:
    rows: list[dict] = []
    offset = 0
    while True:
        body = _post(client, {**payload, "limit": 50, "offset": offset})
        rows.extend(body["results"])
        if body["next_offset"] is None:
            return rows
        offset = body["next_offset"]


def _period_means(client: TestClient, feature_id: str) -> dict[str, float]:
    response = client.get(
        "/api/v1/typed/observations",
        params={"feature_id": feature_id, "body": "Earth", "state": "detected"},
    )
    assert response.status_code == 200, response.text
    observations = response.json()["observations"]
    means: dict[str, float] = {}
    for observation in observations:
        assert observation["value_kind"] == "timeseries"
        value = observation["value"]
        assert value["missing_count"] == 0
        samples = value["values"]
        assert samples
        means[observation["area_id"]] = sum(samples) / len(samples)
    return means


def _query_targets() -> dict:
    return {
        "slope_mean_deg": {"value": 10.0, "weight": 1.0},
        "soil_clay_0_5cm_mean": {"value": 200.0, "weight": 1.0},
    }


# ---------------------------------------------------------------------------
# 1. Registry roles: real filters, still not similarity.
# ---------------------------------------------------------------------------


def test_registry_exposes_context_filters_without_similarity(client: TestClient):
    payload = client.get("/api/v1/search/features").json()
    assert payload["context_filter_version"] == "context-filter-1.0.0"
    features = {item["feature_id"]: item for item in payload["features"]}

    for feature_id, (dimension_id, unit) in RANGE_FEATURES.items():
        item = features[feature_id]
        assert item["dimension_id"] == dimension_id
        assert item["unit"] == unit
        assert item["filter_kind"] == "range"
        assert item["filter_statistic"] == "period_mean"
        assert item["roles"]["similarity"]["supported"] is False
        assert item["roles"]["hard_filter"]["supported"] is True
        assert item["roles"]["filter"]["supported"] is True
        assert item["roles"]["evidence"]["supported"] is True
        assert item["roles"]["context"]["supported"] is True
        assert "similarity" not in [
            role for role, support in item["roles"].items() if support["supported"]
        ]
        assert item["min_value"] is not None and item["max_value"] is not None
        assert item["min_value"] < item["max_value"]
        assert item["coverage"]["filter_kind"] == "range"
        assert item["coverage"]["gated_candidate_count"] == CONTEXT_AOI_COUNT
        assert item["coverage"]["joined_candidate_count"] == CONTEXT_AOI_COUNT
        assert item["coverage"]["filter_value_count"] == CONTEXT_AOI_COUNT
        assert item["coverage"]["filter_value_min"] <= item["coverage"]["filter_value_max"]
        assert item["coverage"]["candidate_pool_size"] == EARTH_POOL_SIZE
        assert item["coverage"]["coverage_fraction"] == pytest.approx(
            CONTEXT_AOI_COUNT / EARTH_POOL_SIZE, abs=1e-6
        )
        assert "not a 1.98 km" in item["support"]["spatial_support"]
        assert "2024-01-01..2024-01-07" in item["support"]["temporal_support"]

    geology = features[CATEGORY_FEATURE]
    assert geology["dimension_id"] == "D02"
    assert geology["unit"] == "category"
    assert geology["filter_kind"] == "category"
    assert geology["roles"]["similarity"]["supported"] is False
    assert geology["roles"]["hard_filter"]["supported"] is False
    assert geology["roles"]["category_match"]["supported"] is True
    assert geology["roles"]["filter"]["supported"] is True
    assert geology["coverage"]["joined_candidate_count"] == EARTH_GEOLOGY_COUNT
    assert geology["coverage"]["gated_candidate_count"] == EARTH_GEOLOGY_COUNT
    assert geology["coverage"]["candidate_pool_size"] == EARTH_POOL_SIZE
    assert geology["coverage"]["coverage_fraction"] == pytest.approx(
        EARTH_GEOLOGY_COUNT / EARTH_POOL_SIZE, abs=1e-6
    )
    counts = geology["coverage"]["category_value_counts"]
    assert counts
    assert sum(1 for value in counts.values() if value > 0) == len(counts)
    assert "macrostrat_point_query_units" in counts
    assert "no taxonomy" not in geology["roles"]["category_match"]["reason"].lower()
    assert "not a taxonomy crosswalk" in geology["roles"]["category_match"]["reason"]

    # A selective role query is usable by the frontend.
    d03_filters = _features(client, dimension="D03", role="filter")
    assert set(d03_filters) == {
        "surface_air_temperature_2m_c",
        "surface_air_temperature_2m_max_c",
        "surface_air_temperature_2m_min_c",
        "surface_skin_temperature_c",
    }
    category_features = _features(client, role="category_match")
    assert CATEGORY_FEATURE in category_features


def test_moon_and_mars_features_remain_evidence_only(client: TestClient):
    features = _features(client)
    planetary = (
        "surface_brightness_temperature_k",
        "thermal_inertia",
        "water_equivalent_hydrogen_wt_percent",
        "epithermal_neutron_flux_ratio",
        "solar_elevation_deg",
        "solar_incidence_deg",
    )
    for feature_id in planetary:
        item = features[feature_id]
        assert item["roles"]["similarity"]["supported"] is False, feature_id
        assert item["roles"]["hard_filter"]["supported"] is False, feature_id
        assert item["roles"]["category_match"]["supported"] is False, feature_id
        assert "filter" not in item["roles"], feature_id

    rejected = client.post(
        "/api/v1/search/multidimensional",
        json={
            "targets": {"slope_mean_deg": {"value": 10, "weight": 1}},
            "filters": {"surface_brightness_temperature_k": {"min": 0, "max": 500}},
        },
    )
    assert rejected.status_code == 422, rejected.text
    assert rejected.json()["error"]["code"] == "feature_role_not_filterable"


# ---------------------------------------------------------------------------
# 2. Range filters change the candidate set, never the distance.
# ---------------------------------------------------------------------------


def test_range_filter_changes_candidates_and_reports_missing(client: TestClient):
    feature_id = "surface_air_temperature_2m_c"
    means = _period_means(client, feature_id)
    assert len(means) == CONTEXT_AOI_COUNT

    baseline = _all_results(client, {"targets": _query_targets()})
    baseline_by_id = {row["location_id"]: row["distance"] for row in baseline}
    baseline_order = [row["location_id"] for row in baseline]

    ordered_means = sorted(means.values())
    cut = (ordered_means[3] + ordered_means[4]) / 2.0
    expected = {area_id for area_id, value in means.items() if value <= cut}
    assert 0 < len(expected) < CONTEXT_AOI_COUNT

    body = _post(
        client,
        {
            "targets": _query_targets(),
            "filters": {feature_id: {"max": cut}},
            "limit": 50,
        },
    )
    assert body["candidate_pool_size"] == EARTH_POOL_SIZE
    assert body["filtered_candidate_count"] == len(expected)
    assert body["eligible_candidate_count"] == len(expected)
    assert body["excluded_candidate_count"] == EARTH_POOL_SIZE - len(expected)
    assert body["filters_applied"][feature_id]["kind"] == "range"
    assert body["filters_applied"][feature_id]["statistic"] == "period_mean"
    assert body["filters_applied"][feature_id]["unit"] == "degree_Celsius"

    result_ids = {row["location_id"] for row in body["results"]}
    assert result_ids == expected
    for row in body["results"]:
        assert means[row["location_id"]] <= cut

    missing_rows = [row for row in body["excluded"] if feature_id in row["missing_features"]]
    assert len(missing_rows) == EARTH_POOL_SIZE - CONTEXT_AOI_COUNT
    assert all(f"missing_filter:{feature_id}" in row["reasons"] for row in missing_rows)
    range_rows = [row for row in body["excluded"] if f"filter:{feature_id}" in row["reasons"]]
    assert len(range_rows) == CONTEXT_AOI_COUNT - len(expected)

    # The filter selects rows only: each retained row keeps its unfiltered distance and the
    # relative order among retained candidates is unchanged.
    for row in body["results"]:
        assert row["distance"] == baseline_by_id[row["location_id"]]
    assert [row["location_id"] for row in body["results"]] == [
        location_id for location_id in baseline_order if location_id in expected
    ]


def test_period_mean_is_the_registered_derived_value(client: TestClient):
    feature_id = "precipitation_mm_day"
    means = _period_means(client, feature_id)
    area_id, mean = sorted(means.items())[0]

    body = _post(
        client,
        {
            "targets": _query_targets(),
            "filters": {feature_id: {"min": mean, "max": mean}},
            "limit": 50,
        },
    )
    assert area_id in {row["location_id"] for row in body["results"]}
    for row in body["results"]:
        assert math.isclose(means[row["location_id"]], mean, rel_tol=0.0, abs_tol=1e-12)

    registry = _features(client)[feature_id]
    assert registry["coverage"]["filter_value_min"] == min(means.values())
    assert registry["coverage"]["filter_value_max"] == max(means.values())
    # Source rows are still timeseries: the derived filter value is reported separately and no
    # fabricated scalar observation is created.
    assert registry["coverage"]["numeric_observation_count"] == 0
    assert registry["coverage"]["filter_value_count"] == CONTEXT_AOI_COUNT


def test_range_validation_and_empty_result_are_explicit(client: TestClient):
    feature_id = "snow_depth_cm"
    reversed_range = client.post(
        "/api/v1/search/multidimensional",
        json={"targets": _query_targets(), "filters": {feature_id: {"min": 5, "max": 1}}},
    )
    assert reversed_range.status_code == 422, reversed_range.text

    below_domain = client.post(
        "/api/v1/search/multidimensional",
        json={"targets": _query_targets(), "filters": {feature_id: {"min": -1}}},
    )
    assert below_domain.status_code == 422, below_domain.text
    assert below_domain.json()["error"]["code"] == "filter_out_of_domain"

    above_domain = client.post(
        "/api/v1/search/multidimensional",
        json={"targets": _query_targets(), "filters": {feature_id: {"max": 5000}}},
    )
    assert above_domain.status_code == 422, above_domain.text
    assert above_domain.json()["error"]["code"] == "filter_out_of_domain"

    unknown = client.post(
        "/api/v1/search/multidimensional",
        json={"targets": _query_targets(), "filters": {"not_a_feature": {"min": 0}}},
    )
    assert unknown.status_code == 422
    assert unknown.json()["error"]["code"] == "unknown_feature"

    # A legal range that excludes all eight covered candidates must return an empty result,
    # not a fabricated zero value.
    empty = _post(
        client,
        {
            "targets": _query_targets(),
            "filters": {feature_id: {"min": 1999.0}},
            "limit": 50,
        },
    )
    assert empty["filtered_candidate_count"] == 0
    assert empty["eligible_candidate_count"] == 0
    assert empty["results"] == []
    assert empty["excluded_candidate_count"] == EARTH_POOL_SIZE
    assert all(row["reasons"] for row in empty["excluded"])

    # The same context feature is still rejected as a similarity target.
    target = client.post(
        "/api/v1/search/multidimensional",
        json={"targets": {feature_id: {"value": 1.0, "weight": 1.0}}},
    )
    assert target.status_code == 422
    error = target.json()["error"]
    assert error["code"] == "feature_role_not_scoreable"
    assert "similarity" not in error["details"][0]["legal_roles"]
    assert "hard_filter" in error["details"][0]["legal_roles"]


# ---------------------------------------------------------------------------
# 3. D02 category dropdown: exact stored label, real coverage only.
# ---------------------------------------------------------------------------


def test_geologic_unit_category_filter_uses_stored_labels(client: TestClient):
    features = _features(client)
    geology = features[CATEGORY_FEATURE]
    counts = geology["coverage"]["category_value_counts"]
    label = min(
        (value for value in counts if value != "macrostrat_point_query_units"),
        key=lambda value: counts[value],
    )

    observations = client.get(
        "/api/v1/typed/observations",
        params={"feature_id": CATEGORY_FEATURE, "body": "Earth", "state": "detected"},
    ).json()["observations"]
    expected_areas = {
        observation["area_id"]
        for observation in observations
        if label in category_labels(observation["value"])
    }
    assert expected_areas
    assert len(observations) == EARTH_GEOLOGY_COUNT

    baseline = _all_results(client, {"targets": _query_targets()})
    baseline_by_id = {row["location_id"]: row["distance"] for row in baseline}

    body = _post(
        client,
        {
            "targets": _query_targets(),
            "category_filters": {CATEGORY_FEATURE: [label]},
            "limit": 50,
        },
    )
    assert body["filtered_candidate_count"] == len(expected_areas)
    assert body["eligible_candidate_count"] == len(expected_areas)
    assert body["excluded_candidate_count"] == EARTH_POOL_SIZE - len(expected_areas)
    assert {row["location_id"] for row in body["results"]} == expected_areas
    applied = body["filters_applied"][CATEGORY_FEATURE]
    assert applied["kind"] == "category"
    assert applied["values"] == [label]
    assert applied["unit"] == "category"
    for row in body["results"]:
        assert row["distance"] == baseline_by_id[row["location_id"]]

    missing_rows = [row for row in body["excluded"] if CATEGORY_FEATURE in row["missing_features"]]
    assert len(missing_rows) == EARTH_POOL_SIZE - EARTH_GEOLOGY_COUNT
    mismatch_rows = [
        row for row in body["excluded"] if f"category:{CATEGORY_FEATURE}" in row["reasons"]
    ]
    assert len(mismatch_rows) == EARTH_GEOLOGY_COUNT - len(expected_areas)

    # An unknown stored label is a legal query that matches nobody; it is not an error and not
    # a silent fallback.
    unknown_label = _post(
        client,
        {
            "targets": _query_targets(),
            "category_filters": {CATEGORY_FEATURE: ["Not A Stored Map Unit"]},
            "limit": 5,
        },
    )
    assert unknown_label["filtered_candidate_count"] == 0
    assert unknown_label["results"] == []
    assert any(
        f"category:{CATEGORY_FEATURE}" in row["reasons"] for row in unknown_label["excluded"]
    )


def test_category_filter_validation_and_combination(client: TestClient):
    # Category filters are only legal for a registered category feature.
    wrong_role = client.post(
        "/api/v1/search/multidimensional",
        json={
            "targets": _query_targets(),
            "category_filters": {"surface_air_temperature_2m_c": ["anything"]},
        },
    )
    assert wrong_role.status_code == 422, wrong_role.text
    assert wrong_role.json()["error"]["code"] == "feature_role_not_category_filterable"

    empty_values = client.post(
        "/api/v1/search/multidimensional",
        json={"targets": _query_targets(), "category_filters": {CATEGORY_FEATURE: []}},
    )
    assert empty_values.status_code == 422, empty_values.text

    blank_value = client.post(
        "/api/v1/search/multidimensional",
        json={"targets": _query_targets(), "category_filters": {CATEGORY_FEATURE: [" "]}},
    )
    assert blank_value.status_code == 422, blank_value.text

    # Range and category filters compose without becoming a score.
    combined = _post(
        client,
        {
            "targets": _query_targets(),
            "category_filters": {CATEGORY_FEATURE: ["macrostrat_point_query_units"]},
            "filters": {"precipitation_mm_day": {"min": 0.0, "max": 2000.0}},
            "limit": 50,
        },
    )
    assert combined["filtered_candidate_count"] == EARTH_GEOLOGY_COUNT
    assert set(combined["filters_applied"]) == {
        CATEGORY_FEATURE,
        "precipitation_mm_day",
    }
    assert all("similarity" not in row["valid_features"] for row in combined["results"])
    assert all(
        set(row["valid_features"]) <= {"slope_mean_deg", "soil_clay_0_5cm_mean"}
        for row in combined["results"]
    )
