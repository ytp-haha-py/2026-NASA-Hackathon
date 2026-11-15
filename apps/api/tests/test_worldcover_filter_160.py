"""Regression coverage for the Earth-only ESA WorldCover hard filter (Issue #160).

WorldCover is Earth land-cover context/filter data:
- it is not D07 and never enters the similarity distance;
- it is not registered as a similarity feature or a multidimensional dimension;
- the legacy /search hard filter applies to Earth candidates only and never zero-fills missing
  context.

The committed real manifests are used; no synthetic fixture or network access is involved.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from analog_api.config import Settings
from analog_api.main import create_app
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[3]
EARTH_MANIFEST_DIR = ROOT / "pipelines" / "earth" / "manifests"
PLANETARY_MANIFEST_DIR = ROOT / "pipelines" / "planetary" / "manifests"
CONTEXT_MANIFEST_DIR = ROOT / "pipelines" / "worldcover" / "manifests"


@pytest.fixture(scope="module")
def client(tmp_path_factory: pytest.TempPathFactory):
    tmp = tmp_path_factory.mktemp("worldcover-160")
    settings = Settings(
        database_url=f"sqlite+pysqlite:///{tmp / 'worldcover.db'}",
        manifest_dir=str(EARTH_MANIFEST_DIR),
        manifest_dir_planetary=str(PLANETARY_MANIFEST_DIR),
        manifest_dir_context=str(CONTEXT_MANIFEST_DIR),
        seed_include_synthetic=False,
        seed_include_typed=False,
    )
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def _search(client: TestClient, **payload) -> dict:
    response = client.post("/api/v1/search", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def test_worldcover_is_earth_only_context_filter_not_a_dimension(client: TestClient):
    dimensions = client.get("/api/v1/dimensions").json()
    roles = {item["dimension_id"]: item for item in dimensions["dimensions"]}
    assert roles["terrain_shape"]["score_role"] == "similarity"
    assert roles["land_cover_context"]["score_role"] == "context_only"
    assert roles["land_cover_context"]["label"].startswith("Earth land-cover context")
    assert roles["land_cover_context"]["features"]
    assert all(
        feature["score_role"] == "context_only"
        for feature in roles["land_cover_context"]["features"]
    )

    capabilities = client.get("/api/v1/capabilities").json()
    context_features = capabilities["context_features"]
    assert context_features
    assert all(item["feature_id"].startswith("landcover_") for item in context_features)
    assert all(item["score_role"] == "context_only" for item in context_features)
    assert all(
        item["provider_coverage_by_body"]
        == {
            "Earth": "verified_where_indexed",
            "Moon": "not_applicable",
            "Mars": "not_applicable",
        }
        for item in context_features
    )

    similarity_registry = client.get(
        "/api/v1/search/features", params={"role": "similarity", "body": "Earth"}
    )
    assert similarity_registry.status_code == 200, similarity_registry.text
    assert all(
        "landcover" not in feature["feature_id"]
        for feature in similarity_registry.json()["features"]
    )

    unfiltered = _search(
        client,
        body="custom",
        target={"slope_mean_deg": {"value": 5, "weight": 1}},
        limit=10,
    )
    filtered = _search(
        client,
        body="custom",
        target={"slope_mean_deg": {"value": 5, "weight": 1}},
        filters={"landcover_bare_sparse_fraction": {"max": 0.5}},
        limit=10,
    )

    assert filtered["query_version"] == "search-1.1.0"
    assert filtered["filters_applied"] == {
        "landcover_bare_sparse_fraction": {"min": None, "max": 0.5}
    }
    assert filtered["eligible_candidate_count"] < unfiltered["eligible_candidate_count"]
    assert any(
        item["location_id"] == "earth-atacama-desert"
        and item["reason"] == "filter:landcover_bare_sparse_fraction"
        for item in filtered["excluded"]
    )

    unfiltered_distances = {item["location_id"]: item["distance"] for item in unfiltered["results"]}
    for result in filtered["results"]:
        location = client.get(f"/api/v1/locations/{result['location_id']}").json()
        context = {
            item["feature_id"]: item
            for item in location["context_observations"]
            if item["feature_id"] == "landcover_bare_sparse_fraction"
        }
        assert context
        observation = context["landcover_bare_sparse_fraction"]
        assert observation["score_role"] == "context_only"
        assert observation["value"] <= 0.5
        # A hard filter selects rows; it never changes the similarity distance.
        if result["location_id"] in unfiltered_distances:
            assert result["distance"] == unfiltered_distances[result["location_id"]]


def test_worldcover_filter_is_not_accepted_by_multidimensional_search(client: TestClient):
    """The current multidimensional path has no WorldCover contract; keep the gap explicit."""

    response = client.post(
        "/api/v1/search/multidimensional",
        json={
            "targets": {"slope_mean_deg": {"value": 5, "weight": 1}},
            "filters": {"landcover_bare_sparse_fraction": {"max": 0.5}},
            "limit": 3,
        },
    )
    assert response.status_code == 422, response.text
    body = response.json()
    assert body["error"]["code"] == "unknown_feature"
    assert "landcover_bare_sparse_fraction" in body["error"]["message"]

    target_response = client.post(
        "/api/v1/search/multidimensional",
        json={
            "targets": {"landcover_bare_sparse_fraction": {"value": 0.2, "weight": 1}},
            "limit": 3,
        },
    )
    assert target_response.status_code == 422, target_response.text
    assert target_response.json()["error"]["code"] == "unknown_feature"
