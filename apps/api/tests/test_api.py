from __future__ import annotations

import json
from pathlib import Path

import pytest
from analog_api.adapters.seed import seed_manifests
from analog_api.config import Settings
from analog_api.data.synthetic import synthetic_manifest
from analog_api.domain.entities import BBox, CandidateEntity, FeatureValue
from analog_api.domain.manifest import Manifest
from analog_api.domain.search import execute_search
from analog_api.main import create_app
from analog_api.schemas import SearchRequest, TargetSpec
from fastapi.testclient import TestClient


def make_settings(tmp_path: Path, *, synthetic: bool) -> Settings:
    return Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'analog.db'}",
        seed_include_synthetic=synthetic,
        manifest_dir=str(tmp_path / "no-earth-manifests"),
        manifest_dir_planetary=str(tmp_path / "no-planetary-manifests"),
        manifest_dir_context=str(tmp_path / "no-context-manifests"),
        auto_seed=True,
    )


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return make_settings(tmp_path, synthetic=True)


@pytest.fixture
def client(settings: Settings):
    app = create_app(settings)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def empty_client(tmp_path: Path):
    app = create_app(make_settings(tmp_path, synthetic=False))
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def reference_manifest() -> Manifest:
    payload = synthetic_manifest()
    payload["references"] = [
        {
            "reference_id": "moon-test-reference",
            "body": "Moon",
            "display_name": "TEST-ONLY lunar reference (synthetic)",
            "dataset_id": "synthetic-test-fixture",
            "lat": None,
            "lon": None,
            "bbox": None,
            "window_size_m": 1980,
            "analysis_resolution_m": 90,
            "crs": "EPSG:3031",
            "valid_fraction": 1.0,
            "quality_flags": ["synthetic_fixture"],
            "notes": "Test-only reference, not a NASA observation.",
            "features": {
                feature_id: {
                    "value": observation["value"],
                    "unit": observation["unit"],
                    "method_version": observation["method_version"],
                    "extraction_scale_m": 90,
                    "valid_fraction": 1.0,
                    "quality": "synthetic",
                }
                for feature_id, observation in payload["candidates"][0]["features"].items()
            },
        }
    ]
    return Manifest.model_validate(payload)


@pytest.fixture
def reference_client(tmp_path: Path, reference_manifest: Manifest):
    settings = make_settings(tmp_path, synthetic=False)
    from analog_api.adapters.seed import make_engine

    engine = make_engine(settings.database_url)
    seed_manifests(engine, [reference_manifest], reset=True)
    app = create_app(settings)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def zero_weight_reference_client(tmp_path: Path):
    """Reference with all five metrics; synthetic candidates lack flat fraction."""
    payload = synthetic_manifest()
    observations = {
        "slope_mean_deg": (10.0, "degree", "terrain-metrics-1.0.0"),
        "slope_p90_deg": (20.0, "degree", "terrain-metrics-1.0.0"),
        "terrain_relief_p95_p05_m": (80.0, "meter", "terrain-metrics-1.0.0"),
        "roughness_std_m": (10.0, "meter", "terrain-metrics-1.0.0"),
        "flat_terrain_fraction": (0.4, "fraction", "flat-terrain-fraction-1.0.0"),
    }
    payload["references"] = [
        {
            "reference_id": "moon-zero-weight-reference",
            "body": "Moon",
            "display_name": "TEST-ONLY lunar reference with five metrics",
            "dataset_id": "synthetic-test-fixture",
            "lat": None,
            "lon": None,
            "bbox": None,
            "window_size_m": 1980,
            "analysis_resolution_m": 90,
            "crs": "EPSG:3031",
            "valid_fraction": 1.0,
            "quality_flags": ["synthetic_fixture"],
            "notes": "Test-only reference, not a NASA observation.",
            "features": {
                feature_id: {
                    "value": value,
                    "unit": unit,
                    "method_version": method_version,
                    "extraction_scale_m": 90,
                    "valid_fraction": 1.0,
                    "quality": "synthetic",
                }
                for feature_id, (value, unit, method_version) in observations.items()
            },
        }
    ]
    manifest = Manifest.model_validate(payload)
    settings = make_settings(tmp_path, synthetic=False)
    from analog_api.adapters.seed import make_engine

    engine = make_engine(settings.database_url)
    seed_manifests(engine, [manifest], reset=True)
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def search_payload(**overrides):
    payload = {
        "body": "custom",
        "reference_id": None,
        "target": {
            "slope_mean_deg": {"value": 12, "weight": 1},
            "terrain_relief_p95_p05_m": {"value": 80, "weight": 0.5},
        },
        "limit": 10,
    }
    payload.update(overrides)
    return payload


def test_health_and_capabilities(client: TestClient):
    health = client.get("/api/v1/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert health.json()["data_version"].startswith("dv-")

    response = client.get("/api/v1/capabilities")
    assert response.status_code == 200
    body = response.json()
    assert body["contract_version"] == "1.0.0"
    assert [feature["id"] for feature in body["features"]] == [
        "slope_mean_deg",
        "slope_p90_deg",
        "terrain_relief_p95_p05_m",
        "roughness_std_m",
        "flat_terrain_fraction",
    ]
    assert body["supported_bodies"] == ["custom"]
    assert body["indexed_candidate_count"] == 4
    assert body["coverage"]["label"] == "indexed_subset"
    assert body["coverage"]["data_kinds"] == ["synthetic"]
    assert body["coverage"]["origins"] == {"indexed": 4, "on_demand": 0}
    assert len(body["context_features"]) == 11
    assert body["context_features"][0]["score_role"] == "context_only"
    assert body["aoi_analysis"]["max_side_m"] == 2000.0
    assert body["available_reference_ids"] == []
    assert body["features"][0]["scale"] == 10.0
    assert body["data_snapshot_id"] == body["data_version"]


def test_dimensions_catalog(client: TestClient):
    response = client.get("/api/v1/dimensions")
    assert response.status_code == 200
    body = response.json()
    assert [dimension["dimension_id"] for dimension in body["dimensions"]] == [
        "terrain_shape",
        "land_cover_context",
    ]
    assert len(body["dimensions"][0]["features"]) == 5
    assert len(body["context_features"]) == 11
    assert body["dimensions"][1]["score_role"] == "context_only"


def test_search_is_ranked_and_reports_coverage(client: TestClient):
    response = client.post("/api/v1/search", json=search_payload())
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["coverage_label"] == "indexed_subset"
    assert body["indexed_candidate_count"] == 4
    assert body["eligible_candidate_count"] == 4
    assert body["excluded_candidate_count"] == 0
    assert len(body["results"]) == 4
    assert body["results"][0]["location_id"] == "earth-synthetic-beta"
    assert body["results"][0]["rank"] == 1
    assert body["results"][0]["data_kind"] == "synthetic"
    assert any("SYNTHETIC" in caveat for caveat in body["results"][0]["caveats"])
    # Hand-computed weighted normalized Euclidean distance.
    distances = [result["distance"] for result in body["results"]]
    assert distances == sorted(distances)
    assert body["results"][0]["distance"] == pytest.approx(0.4918078893, rel=1e-6)
    assert len(body["results"][0]["metrics"]) == 2
    metric = body["results"][0]["metrics"][0]
    assert metric["feature_id"] == "slope_mean_deg"
    assert metric["delta"] == pytest.approx(-6.0)
    assert metric["scale"] == 10.0
    assert metric["normalized_delta"] == pytest.approx(-0.6)
    assert body["limitations"]
    assert body["data_snapshot_id"] == body["data_version"]
    assert body["coverage"]["origins"] == {"indexed": 4, "on_demand": 0}
    assert body["requested_features"] == [
        "slope_mean_deg",
        "terrain_relief_p95_p05_m",
    ]
    assert body["results"][0]["dimension_distances"]["terrain_shape"] == pytest.approx(
        body["results"][0]["distance"]
    )


def test_search_limit_and_eligible_counts(client: TestClient):
    response = client.post("/api/v1/search", json=search_payload(limit=2))
    body = response.json()
    assert len(body["results"]) == 2
    assert body["eligible_candidate_count"] == 4


def test_custom_zero_weight_missing_metric_stays_eligible(client: TestClient):
    target = {
        "slope_mean_deg": {"value": 12, "weight": 1},
        "slope_p90_deg": {"value": 20, "weight": 1},
        "terrain_relief_p95_p05_m": {"value": 80, "weight": 1},
        "roughness_std_m": {"value": 10, "weight": 1},
        "flat_terrain_fraction": {"value": 0.4, "weight": 0},
    }
    response = client.post("/api/v1/search", json=search_payload(target=target))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["eligible_candidate_count"] == 4
    assert body["excluded"] == []
    for result in body["results"]:
        assert "flat_terrain_fraction" not in {metric["feature_id"] for metric in result["metrics"]}


def test_zero_weight_incompatible_method_version_stays_eligible():
    class Repo:
        def __init__(self, candidates: list[CandidateEntity]) -> None:
            self.candidates = candidates

        def candidate_count(self) -> int:
            return len(self.candidates)

        def list_candidates(self) -> list[CandidateEntity]:
            return self.candidates

        def data_version(self) -> str:
            return "dv-test"

    candidate = CandidateEntity(
        location_id="earth-test",
        display_name="TEST-ONLY candidate",
        lat=0.0,
        lon=0.0,
        bbox=BBox(-0.01, -0.01, 0.01, 0.01),
        dataset_id="synthetic-test-fixture",
        window_size_m=1980,
        analysis_resolution_m=90,
        crs="EPSG:3857",
        valid_fraction=1.0,
        quality_flags=(),
        notes=None,
        data_kind="synthetic",
        features={
            "slope_mean_deg": FeatureValue(
                value=10.0,
                unit="degree",
                method_version="terrain-metrics-1.0.0",
                extraction_scale_m=90,
                valid_fraction=1.0,
                quality="synthetic",
            ),
            "slope_p90_deg": FeatureValue(
                value=20.0,
                unit="degree",
                method_version="terrain-metrics-0.9.0",
                extraction_scale_m=90,
                valid_fraction=1.0,
                quality="synthetic",
            ),
        },
    )
    request = SearchRequest(
        body="custom",
        target={
            "slope_mean_deg": TargetSpec(value=10.0, weight=1.0),
            "slope_p90_deg": TargetSpec(value=20.0, weight=0.0),
        },
    )
    body = execute_search(Repo([candidate]), request)
    assert body["eligible_candidate_count"] == 1
    assert body["excluded"] == []
    assert [metric["feature_id"] for metric in body["results"][0]["metrics"]] == ["slope_mean_deg"]


def test_reference_mode_zero_weight_missing_metric_stays_eligible(
    zero_weight_reference_client: TestClient,
):
    response = zero_weight_reference_client.post(
        "/api/v1/search",
        json={
            "body": "Moon",
            "reference_id": "moon-zero-weight-reference",
            "weights": {"flat_terrain_fraction": 0.0},
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["eligible_candidate_count"] == 4
    assert body["excluded"] == []
    assert len(body["results"]) == 4
    for result in body["results"]:
        assert "flat_terrain_fraction" not in {metric["feature_id"] for metric in result["metrics"]}

    # The same reference with the default positive weight still requires the metric.
    response = zero_weight_reference_client.post(
        "/api/v1/search",
        json={"body": "Moon", "reference_id": "moon-zero-weight-reference"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["eligible_candidate_count"] == 0
    assert body["results"] == []
    assert all(
        "missing_metric:flat_terrain_fraction" in item["reason"] for item in body["excluded"]
    )


def test_search_rejects_reference_and_target_together(client: TestClient):
    response = client.post(
        "/api/v1/search", json=search_payload(reference_id="moon-test-reference")
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_search_rejects_non_custom_body_without_reference(client: TestClient):
    response = client.post("/api/v1/search", json=search_payload(body="Mars"))
    assert response.status_code == 422
    assert "verified reference" in response.json()["error"]["message"]


def test_search_unknown_feature(client: TestClient):
    response = client.post(
        "/api/v1/search",
        json={
            "body": "custom",
            "target": {"gravity_m_s2": {"value": 3.7, "weight": 1}},
            "limit": 5,
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert "unsupported feature" in response.json()["error"]["message"]


def test_search_rejects_zero_weights(client: TestClient):
    response = client.post(
        "/api/v1/search",
        json={
            "body": "custom",
            "target": {"slope_mean_deg": {"value": 10, "weight": 0}},
            "limit": 5,
        },
    )
    assert response.status_code == 422
    assert "positive weight" in response.json()["error"]["message"]


def test_search_rejects_out_of_domain_target(client: TestClient):
    response = client.post(
        "/api/v1/search",
        json={
            "body": "custom",
            "target": {"slope_mean_deg": {"value": 120, "weight": 1}},
            "limit": 5,
        },
    )
    assert response.status_code == 422


def test_empty_index_returns_explicit_conflict(empty_client: TestClient):
    capabilities = empty_client.get("/api/v1/capabilities").json()
    assert capabilities["indexed_candidate_count"] == 0
    assert capabilities["coverage"]["geographic_bbox"] is None
    response = empty_client.post("/api/v1/search", json=search_payload())
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "no_indexed_data"


def test_location_detail_and_context(client: TestClient):
    detail = client.get("/api/v1/locations/earth-synthetic-gamma")
    assert detail.status_code == 200
    body = detail.json()
    assert body["body"] == "Earth"
    assert body["data_kind"] == "synthetic"
    assert len(body["features"]) == 4
    assert body["datasets"][0]["license"] == "CC0-1.0"
    assert any("SYNTHETIC" in limitation for limitation in body["known_limitations"])

    target = json.dumps({"slope_mean_deg": 12})
    context = client.get(
        "/api/v1/locations/earth-synthetic-gamma/context",
        params={"target": target},
    )
    assert context.status_code == 200
    payload = context.json()
    assert payload["export_version"] == "1.0.0"
    assert payload["location"]["location_id"] == "earth-synthetic-gamma"
    assert payload["query"] == {"slope_mean_deg": 12.0}
    comparison = next(
        item for item in payload["computed_comparisons"] if item["feature_id"] == "slope_mean_deg"
    )
    assert comparison["target"] == 12.0
    assert comparison["delta"] == pytest.approx(0.5)
    assert any("Source synthetic-test-fixture" in fact for fact in payload["source_facts"])
    assert payload["limitations"]

    context_without_target = client.get("/api/v1/locations/earth-synthetic-gamma/context").json()
    assert context_without_target["query"] is None
    assert context_without_target["computed_comparisons"][0]["target"] is None


def test_location_not_found(client: TestClient):
    response = client.get("/api/v1/locations/earth-does-not-exist")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_datasets_expose_provenance(client: TestClient):
    response = client.get("/api/v1/datasets")
    assert response.status_code == 200
    datasets = response.json()
    assert len(datasets) == 1
    assert datasets[0]["dataset_id"] == "synthetic-test-fixture"
    assert datasets[0]["source_kind"] == "synthetic"


def test_unknown_route_uses_consistent_error_shape(client: TestClient):
    response = client.get("/api/v1/nope")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_internal_errors_are_masked(tmp_path: Path):
    app = create_app(make_settings(tmp_path, synthetic=True))

    @app.get("/boom")
    def boom():
        raise RuntimeError("secret stack trace")

    with TestClient(app, raise_server_exceptions=False) as test_client:
        response = test_client.get("/boom")
    assert response.status_code == 500
    body = response.json()
    assert body["error"]["code"] == "internal_error"
    assert "secret" not in body["error"]["message"]
    assert "Traceback" not in response.text


def test_reference_mode_uses_verified_reference(reference_client: TestClient):
    capabilities = reference_client.get("/api/v1/capabilities").json()
    assert capabilities["available_reference_ids"] == ["moon-test-reference"]
    assert capabilities["supported_bodies"] == ["custom", "Moon"]

    references = reference_client.get("/api/v1/references").json()
    assert references[0]["reference_id"] == "moon-test-reference"
    assert references[0]["body"] == "Moon"

    response = reference_client.post(
        "/api/v1/search",
        json={"body": "Moon", "reference_id": "moon-test-reference", "limit": 3},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["results"]) == 3
    assert body["coverage_label"] == "indexed_subset"


def test_reference_body_mismatch(reference_client: TestClient):
    response = reference_client.post(
        "/api/v1/search",
        json={"body": "Mars", "reference_id": "moon-test-reference", "limit": 3},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "body_mismatch"


def test_unknown_reference_is_not_found(reference_client: TestClient):
    response = reference_client.post(
        "/api/v1/search",
        json={"body": "Moon", "reference_id": "moon-missing", "limit": 3},
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_search_terrain_range_filter_excludes_out_of_range(client: TestClient):
    unfiltered = client.post("/api/v1/search", json=search_payload()).json()
    response = client.post(
        "/api/v1/search",
        json=search_payload(filters={"slope_mean_deg": {"min": 5, "max": 15}}),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert {result["location_id"] for result in body["results"]} == {
        "earth-synthetic-beta",
        "earth-synthetic-gamma",
    }
    reasons = {item["location_id"]: item["reason"] for item in body["excluded"]}
    assert reasons == {
        "earth-synthetic-alpha": "filter:slope_mean_deg",
        "earth-synthetic-delta": "filter:slope_mean_deg",
    }
    assert body["filters_applied"] == {"slope_mean_deg": {"min": 5.0, "max": 15.0}}
    # A hard filter removes candidates but never changes the distance of the ones that remain.
    before = {result["location_id"]: result["distance"] for result in unfiltered["results"]}
    for result in body["results"]:
        assert result["distance"] == pytest.approx(before[result["location_id"]])


def test_search_terrain_filter_bounds_are_inclusive(client: TestClient):
    body = client.post(
        "/api/v1/search",
        json=search_payload(filters={"slope_mean_deg": {"min": 6, "max": 6}}),
    ).json()
    assert [result["location_id"] for result in body["results"]] == ["earth-synthetic-beta"]


def test_search_terrain_filter_on_missing_metric_excludes_without_imputing(client: TestClient):
    body = client.post(
        "/api/v1/search",
        json=search_payload(filters={"flat_terrain_fraction": {"min": 0}}),
    ).json()
    assert body["results"] == []
    assert {item["reason"] for item in body["excluded"]} == {"missing_metric:flat_terrain_fraction"}


def test_search_terrain_filter_rejects_out_of_domain_bounds(client: TestClient):
    response = client.post(
        "/api/v1/search",
        json=search_payload(filters={"slope_mean_deg": {"min": -1}}),
    )
    assert response.status_code == 422
    assert "filter bounds for slope_mean_deg" in response.json()["error"]["message"]


def test_search_context_filter_bounds_stay_fractions(client: TestClient):
    response = client.post(
        "/api/v1/search",
        json=search_payload(filters={"landcover_bare_sparse_fraction": {"max": 1.5}}),
    )
    assert response.status_code == 422


def test_search_filter_rejects_inverted_range(client: TestClient):
    response = client.post(
        "/api/v1/search",
        json=search_payload(filters={"slope_mean_deg": {"min": 10, "max": 5}}),
    )
    assert response.status_code == 422
