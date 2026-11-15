from __future__ import annotations

from pathlib import Path

import pytest
from analog_api.config import Settings
from analog_api.main import create_app
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def client(tmp_path_factory: pytest.TempPathFactory):
    tmp = tmp_path_factory.mktemp("multidim")
    settings = Settings(
        database_url=f"sqlite+pysqlite:///{tmp / 'multidim.db'}",
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
    return {feature["feature_id"]: feature for feature in response.json()["features"]}


def test_registry_distinguishes_score_filters_evidence_and_uncertainty(client: TestClient):
    response = client.get("/api/v1/search/features")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["registry_version"] == "feature-eligibility-1.0.0"
    assert payload["join_audit"]["proximity_join_used"] is False
    assert payload["join_audit"]["typed_areas_exactly_matching_fine_candidate"] >= 100
    features = {feature["feature_id"]: feature for feature in payload["features"]}

    mean = features["soil_clay_0_5cm_mean"]
    assert mean["roles"]["similarity"]["supported"] is True
    assert mean["roles"]["hard_filter"]["supported"] is True
    assert mean["body_scope"] == ["Earth"]
    assert mean["selector"]["depth_interval_m"] == [0.0, 0.05]
    assert mean["selector"]["statistic"] == "mean"
    assert mean["coverage"]["gated_candidate_count"] == 113
    assert "earth-alps-r1c1" in mean["coverage"]["gated_candidate_ids"]
    assert "earth-lanzarote" in mean["coverage"]["gated_candidate_ids"]

    q05 = features["soil_clay_0_5cm_q05"]
    assert q05["roles"]["similarity"]["supported"] is False
    assert q05["roles"]["uncertainty"]["supported"] is True
    assert q05["measurement_family"] == mean["measurement_family"]

    geology = features["geologic_unit"]
    assert geology["roles"]["similarity"]["supported"] is False
    assert geology["roles"]["evidence"]["supported"] is True
    assert "taxonomy" in geology["unsupported_reason"]

    thermal = features["surface_brightness_temperature_k"]
    assert thermal["roles"]["similarity"]["supported"] is False
    assert thermal["body_scope"] == ["Moon"]
    assert thermal["roles"]["evidence"]["supported"] is True

    assert features["slope_mean_deg"]["roles"]["similarity"]["supported"] is True


def test_registry_audit_reports_role_counts_and_join_gaps(client: TestClient):
    payload = client.get("/api/v1/search/features").json()
    summary = payload["audit_summary"]
    assert summary["registered_feature_count"] >= 80
    assert summary["similarity_feature_count"] >= 25
    assert summary["by_dimension"]["D06"]["similarity"] == 20
    assert summary["by_dimension"]["D02"]["unsupported_for_scoring"] == 15
    assert (
        payload["join_audit"]["typed_area_count"]
        > payload["join_audit"]["typed_areas_exactly_matching_fine_candidate"]
    )


def test_d06_feature_changes_a_real_ranking(client: TestClient):
    d01_only = client.post(
        "/api/v1/search/multidimensional",
        json={
            "targets": {"slope_mean_deg": {"value": 10, "weight": 1}},
            "filters": {"soil_clay_0_5cm_mean": {"min": 0, "max": 1000}},
            "limit": 10,
        },
    )
    assert d01_only.status_code == 200, d01_only.text
    d01_order = [row["location_id"] for row in d01_only.json()["results"]]
    assert len(d01_order) == 10

    combined = client.post(
        "/api/v1/search/multidimensional",
        json={
            "targets": {
                "slope_mean_deg": {"value": 10, "weight": 1},
                "soil_clay_0_5cm_mean": {"value": 200, "weight": 1},
            },
            "limit": 10,
        },
    )
    assert combined.status_code == 200, combined.text
    body = combined.json()
    assert body["eligible_candidate_count"] == 113
    assert body["excluded_candidate_count"] == 0
    combined_order = [row["location_id"] for row in body["results"]]
    # D06 changes the ranking; the D01-only order is not simply reused.
    assert combined_order != d01_order
    assert combined_order[0] != d01_order[0]

    first = body["results"][0]
    by_feature = {item["feature_id"]: item for item in first["contributions"]}
    clay = by_feature["soil_clay_0_5cm_mean"]
    assert clay["contribution"] > 0
    assert clay["observation_id"].startswith("soilgrids:")
    assert clay["observation_id"].endswith("clay:0-5cm:mean")
    expected = ((clay["candidate"] - 200.0) / 200.0) ** 2 / 2.0
    assert clay["contribution"] == pytest.approx(expected)
    assert clay["uncertainty"]["q05"] is not None
    assert clay["uncertainty"]["q95"] is not None
    assert clay["support"]["native_resolution_m"] == 250.0
    assert body["coverage"]["features"]["soil_clay_0_5cm_mean"]["scored_result_count"] == 113
    assert "six-dimension scientific total" in body["limitations"][0]


def test_zero_weight_does_not_exclude_but_positive_weight_does(client: TestClient):
    zero = client.post(
        "/api/v1/search/multidimensional",
        json={
            "targets": {
                "slope_mean_deg": {"value": 10, "weight": 1},
                "soil_clay_5_15cm_mean": {"value": 200, "weight": 0},
            },
            "limit": 3,
        },
    )
    assert zero.status_code == 200, zero.text
    zero_body = zero.json()
    assert zero_body["eligible_candidate_count"] == 113
    assert zero_body["excluded_candidate_count"] == 0
    assert zero_body["coverage"]["features"]["soil_clay_5_15cm_mean"]["comparison"] == (
        "zero_weight_not_compared"
    )
    assert all("soil_clay_5_15cm_mean" not in row["valid_features"] for row in zero_body["results"])

    positive = client.post(
        "/api/v1/search/multidimensional",
        json={
            "targets": {
                "slope_mean_deg": {"value": 10, "weight": 1},
                "soil_clay_5_15cm_mean": {"value": 200, "weight": 1},
            },
            "limit": 10,
        },
    ).json()
    assert positive["eligible_candidate_count"] == 5
    assert positive["excluded_candidate_count"] == 108
    alps = [row for row in positive["excluded"] if row["location_id"] == "earth-alps-r1c1"][0]
    assert alps["missing_features"] == ["soil_clay_5_15cm_mean"]
    assert "missing_feature:soil_clay_5_15cm_mean" in alps["reasons"]


def test_depth_selectors_are_not_pooled(client: TestClient):
    features = _features(client, dimension="D06")
    shallow = features["soil_clay_0_5cm_mean"]
    deeper = features["soil_clay_5_15cm_mean"]
    assert shallow["selector"]["depth_interval_m"] == [0.0, 0.05]
    assert deeper["selector"]["depth_interval_m"] == [0.05, 0.15]
    assert shallow["coverage"]["gated_candidate_count"] == 113
    assert deeper["coverage"]["gated_candidate_count"] == 5

    response = client.post(
        "/api/v1/search/multidimensional",
        json={
            "targets": {
                "soil_clay_0_5cm_mean": {"value": 200, "weight": 1},
                "soil_clay_5_15cm_mean": {"value": 200, "weight": 1},
            },
            "limit": 5,
        },
    )
    assert response.status_code == 200, response.text
    rows = response.json()["results"]
    assert rows
    for row in rows:
        by_feature = {item["feature_id"]: item for item in row["contributions"]}
        assert (
            by_feature["soil_clay_0_5cm_mean"]["candidate"]
            != by_feature["soil_clay_5_15cm_mean"]["candidate"]
        )
        assert by_feature["soil_clay_0_5cm_mean"]["selector"]["depth_interval_m"] == [
            0.0,
            0.05,
        ]
        assert by_feature["soil_clay_5_15cm_mean"]["selector"]["depth_interval_m"] == [
            0.05,
            0.15,
        ]


def test_unsupported_and_cross_body_targets_are_rejected_with_reasons(client: TestClient):
    evidence = client.post(
        "/api/v1/search/multidimensional",
        json={"targets": {"geologic_unit": {"value": 1, "weight": 1}}},
    )
    assert evidence.status_code == 422
    error = evidence.json()["error"]
    assert error["code"] == "feature_role_not_scoreable"
    assert "taxonomy" in error["message"]
    assert "evidence" in error["details"][0]["legal_roles"]
    assert "similarity" not in error["details"][0]["legal_roles"]

    moon_cross_body = client.post(
        "/api/v1/search/multidimensional",
        json={
            "reference_id": "moon-lola-site01-roi1",
            "targets": {"soil_clay_0_5cm_mean": {"value": 200, "weight": 1}},
            "limit": 5,
        },
    )
    assert moon_cross_body.status_code == 422
    error = moon_cross_body.json()["error"]
    assert error["code"] == "body_out_of_scope"
    assert error["details"][0]["reason"] == "cross_body_incompatible"
    assert error["details"][0]["body_scope"] == ["Earth"]

    moon_candidate = client.post(
        "/api/v1/search/multidimensional",
        json={
            "candidate_body": "Moon",
            "targets": {"slope_mean_deg": {"value": 10, "weight": 1}},
        },
    )
    assert moon_candidate.status_code == 409
    assert moon_candidate.json()["error"]["code"] == "no_fine_candidates_for_body"


def test_unknown_and_out_of_domain_features_are_rejected(client: TestClient):
    unknown = client.post(
        "/api/v1/search/multidimensional",
        json={"targets": {"not_a_feature": {"value": 1, "weight": 1}}},
    )
    assert unknown.status_code == 422
    assert unknown.json()["error"]["code"] == "unknown_feature"

    out_of_domain = client.post(
        "/api/v1/search/multidimensional",
        json={"targets": {"soil_clay_0_5cm_mean": {"value": 1001, "weight": 1}}},
    )
    assert out_of_domain.status_code == 422
    assert out_of_domain.json()["error"]["code"] == "target_out_of_domain"

    no_positive = client.post(
        "/api/v1/search/multidimensional",
        json={
            "targets": {
                "slope_mean_deg": {"value": 10, "weight": 0},
                "soil_clay_0_5cm_mean": {"value": 200, "weight": 0},
            }
        },
    )
    assert no_positive.status_code == 422


def test_filters_pagination_and_evidence_records(client: TestClient):
    response = client.post(
        "/api/v1/search/multidimensional",
        json={
            "targets": {"soil_clay_0_5cm_mean": {"value": 200, "weight": 1}},
            "filters": {"soil_clay_0_5cm_mean": {"min": 150, "max": 240}},
            "limit": 2,
            "offset": 1,
            "include_evidence": True,
            "evidence_limit": 4,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["filtered_candidate_count"] == body["total_matched"]
    assert body["total_matched"] > 2
    assert body["offset"] == 1
    assert body["limit"] == 2
    assert body["returned"] == 2
    assert body["next_offset"] == 3
    assert all(150 <= row["contributions"][0]["candidate"] <= 240 for row in body["results"])
    assert all(len(row["evidence_records"]) <= 4 for row in body["results"])
    assert all(
        record["stored_score_role"] in {"evidence_only", "context_only"}
        for row in body["results"]
        for record in row["evidence_records"]
    )

    bad_filter = client.post(
        "/api/v1/search/multidimensional",
        json={
            "targets": {"slope_mean_deg": {"value": 10, "weight": 1}},
            "filters": {"geologic_unit": {"min": 0, "max": 1}},
        },
    )
    assert bad_filter.status_code == 422
    assert bad_filter.json()["error"]["code"] == "feature_role_not_filterable"


def test_existing_search_global_and_typed_endpoints_are_unchanged(client: TestClient):
    preserved = client.post(
        "/api/v1/search",
        json={"body": "custom", "target": {"slope_mean_deg": {"value": 10, "weight": 1}}},
    )
    assert preserved.status_code == 200, preserved.text
    assert "dimension_distances" in preserved.json()["results"][0]
    assert client.get("/api/v1/global/coverage").status_code == 200
    typed = client.get("/api/v1/typed/coverage").json()
    assert typed["total"] == 2185
    assert typed["by_dimension"]["D06"]["total"] == 1932


def test_every_observed_typed_feature_has_a_discoverable_legal_role(client: TestClient):
    observations = client.get("/api/v1/typed/observations").json()
    observed_ids = {row["feature_id"] for row in observations}
    registry = {
        feature["feature_id"]: feature
        for feature in client.get("/api/v1/search/features").json()["features"]
    }
    assert observed_ids <= set(registry)
    for feature_id in observed_ids:
        feature = registry[feature_id]
        supported = [role for role, support in feature["roles"].items() if support["supported"]]
        assert supported, feature_id
        assert feature["coverage"]["observation_count"] > 0
        if not feature["roles"]["similarity"]["supported"]:
            assert feature["roles"]["similarity"]["reason"], feature_id
    assert len(observed_ids) >= 90
    assert len(registry) >= len(observed_ids)


def test_time_series_context_feature_is_not_a_similarity_target(client: TestClient):
    response = client.post(
        "/api/v1/search/multidimensional",
        json={"targets": {"surface_air_temperature_2m_c": {"value": 10, "weight": 1}}},
    )
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "feature_role_not_scoreable"
    # Issue #170 adds a coarse range-filter role, never a similarity role.
    assert error["details"][0]["legal_roles"] == ["hard_filter", "evidence", "context"]
    assert "similarity" not in error["details"][0]["legal_roles"]


def test_multidimensional_query_is_bounded_and_paginated(client: TestClient):
    import time

    started = time.perf_counter()
    response = client.post(
        "/api/v1/search/multidimensional",
        json={
            "targets": {
                "slope_mean_deg": {"value": 10, "weight": 1},
                "soil_clay_0_5cm_mean": {"value": 200, "weight": 1},
            },
            "limit": 50,
            "include_evidence": False,
        },
    )
    elapsed = time.perf_counter() - started
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["limit"] == 50
    assert body["returned"] == 50
    assert body["next_offset"] == 50
    assert elapsed < 5.0, f"113-candidate query took {elapsed:.2f}s"
