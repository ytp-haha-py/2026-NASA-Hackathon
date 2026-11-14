from __future__ import annotations

import json
from pathlib import Path

from analog_api.config import Settings
from analog_api.main import create_app
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[3]
EARTH_MANIFEST = ROOT / "pipelines" / "earth" / "manifests" / "copernicus_glo90_earth.json"


def test_committed_earth_manifest_matches_schema():
    jsonschema = __import__("jsonschema")
    schema = json.loads(
        (ROOT / "contracts" / "pipeline-manifest.schema.json").read_text(encoding="utf-8")
    )
    payload = json.loads(EARTH_MANIFEST.read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator(schema).validate(payload)
    assert payload["data_kind"] == "observed"
    assert len(payload["candidates"]) >= 3
    for candidate in payload["candidates"]:
        assert candidate["location_id"].startswith("earth-")
        assert set(candidate["features"]) == {
            "slope_mean_deg",
            "slope_p90_deg",
            "terrain_relief_p95_p05_m",
            "roughness_std_m",
        }
        assert all(
            observation["quality"] == "verified" for observation in candidate["features"].values()
        )
        assert candidate["valid_fraction"] >= 0.8


def test_real_manifest_seeds_and_returns_observed_results(tmp_path: Path):
    settings = Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'real.db'}",
        manifest_dir=str(EARTH_MANIFEST.parent),
        manifest_dir_planetary=str(tmp_path / "none"),
        manifest_dir_context=str(ROOT / "pipelines" / "worldcover" / "manifests"),
        seed_include_synthetic=False,
    )
    app = create_app(settings)
    with TestClient(app) as client:
        capabilities = client.get("/api/v1/capabilities").json()
        assert capabilities["indexed_candidate_count"] >= 3
        assert capabilities["coverage"]["data_kinds"] == ["observed"]
        assert capabilities["coverage"]["geographic_bbox"] is not None

        response = client.post(
            "/api/v1/search",
            json={
                "body": "custom",
                "target": {
                    "slope_mean_deg": {"value": 10, "weight": 1},
                    "terrain_relief_p95_p05_m": {"value": 100, "weight": 1},
                },
                "limit": 5,
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["coverage_label"] == "indexed_subset"
        assert body["results"]
        assert body["eligible_candidate_count"] >= 3
        assert all(result["data_kind"] == "observed" for result in body["results"])
        assert all(
            "SYNTHETIC" not in caveat for result in body["results"] for caveat in result["caveats"]
        )
        assert all(
            any("land-cover context" in caveat for caveat in result["caveats"])
            for result in body["results"]
        )
        distances = [result["distance"] for result in body["results"]]
        assert distances == sorted(distances)

        filtered = client.post(
            "/api/v1/search",
            json={
                "body": "custom",
                "target": {"slope_mean_deg": {"value": 5, "weight": 1}},
                "filters": {"landcover_bare_sparse_fraction": {"max": 0.5}},
                "limit": 10,
            },
        )
        assert filtered.status_code == 200, filtered.text
        filtered_body = filtered.json()
        assert filtered_body["filters_applied"] == {
            "landcover_bare_sparse_fraction": {"min": None, "max": 0.5}
        }
        assert all(
            result["location_id"] != "earth-atacama-desert" for result in filtered_body["results"]
        )
        assert any(
            exclusion["location_id"] == "earth-atacama-desert"
            and "filter:landcover_bare_sparse_fraction" in exclusion["reason"]
            for exclusion in filtered_body["excluded"]
        )

        unknown_filter = client.post(
            "/api/v1/search",
            json={
                "body": "custom",
                "target": {"slope_mean_deg": {"value": 5, "weight": 1}},
                "filters": {"mineralogy_score": {"max": 0.5}},
                "limit": 10,
            },
        )
        assert unknown_filter.status_code == 422
        assert "unsupported context filter" in unknown_filter.json()["error"]["message"]

        datasets = client.get("/api/v1/datasets").json()
        assert {dataset["source_kind"] for dataset in datasets} == {"earth_dem", "landcover"}
        for dataset in datasets:
            assert dataset["license_url"].startswith("https://")
            assert dataset["access_timestamp"]

        location = client.get(f"/api/v1/locations/{body['results'][0]['location_id']}").json()
        assert len(location["context_observations"]) == 11
        assert all(
            observation["score_role"] == "context_only"
            for observation in location["context_observations"]
        )
        assert {dataset["source_kind"] for dataset in location["datasets"]} == {
            "earth_dem",
            "landcover",
        }

        context = client.get(f"/api/v1/locations/{location['location_id']}/context").json()
        assert any("score_role=context_only" in fact for fact in context["source_facts"])
