from __future__ import annotations

import json
from pathlib import Path

from analog_api.config import Settings
from analog_api.main import create_app
from analog_pipelines.coarse_features import COARSE_METHOD_VERSION
from analog_pipelines.coarse_index import CoarseRow, build_index
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[3]


def _row(
    candidate_id: str, mean: float, p90: float, flat: float, region: str = "pilot"
) -> CoarseRow:
    return CoarseRow(
        candidate_id=candidate_id,
        grid_id="coarse-grid-1.0.0",
        region_id=region,
        body="Earth",
        west=10.0,
        south=45.0,
        east=10.06,
        north=45.045,
        crs="EPSG:32633",
        area_km2=25.0,
        slope_mean_deg=mean,
        slope_p90_deg=p90,
        flat_fraction=flat,
        valid_fraction=1.0,
        method_version=COARSE_METHOD_VERSION,
        grid_version="coarse-grid-1.0.0",
        dataset_id="fixture",
        source_asset_id="fixture-asset",
        qa_flags=["synthetic_fixture"],
    )


def _settings(tmp_path: Path, *, index: bool) -> Settings:
    index_dir = tmp_path / "global-index"
    if index:
        build_index(
            [_row("coarse-v1:a", 10.0, 20.0, 0.5), _row("coarse-v1:b", 12.0, 22.0, 0.6)],
            root=index_dir,
            snapshot_id="fixture-snapshot",
        )
    return Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'api.db'}",
        manifest_dir=str(tmp_path / "none"),
        manifest_dir_planetary=str(tmp_path / "none"),
        manifest_dir_context=str(tmp_path / "none"),
        global_index_dir=str(index_dir),
        seed_include_synthetic=False,
    )


def test_global_coverage_and_search_with_fixture_index(tmp_path: Path):
    app = create_app(_settings(tmp_path, index=True))
    with TestClient(app) as client:
        coverage = client.get("/api/v1/global/coverage")
        assert coverage.status_code == 200
        first_cache = dict(app.state.global_index_cache)
        client.get("/api/v1/global/coverage")
        second_cache = dict(app.state.global_index_cache)
        assert first_cache.keys() == second_cache.keys()
        assert first_cache == second_cache
        assert coverage.json()["status"] == "ready"
        assert coverage.json()["row_count"] == 2
        assert coverage.json()["label"] == "local_coarse_index_not_global_exhaustive"

        response = client.post(
            "/api/v1/global/search",
            json={
                "target": {"coarse_slope_mean_deg": {"value": 10, "weight": 1}},
                "limit": 2,
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["method_version"] == COARSE_METHOD_VERSION
        assert body["results"][0]["candidate_id"] == "coarse-v1:a"
        assert body["results"][0]["bbox"]["west"] == 10.0
        assert body["indexed_candidate_count"] == 2

        candidate = client.get("/api/v1/global/candidates/coarse-v1:a")
        assert candidate.status_code == 200
        assert candidate.json()["slope_mean_deg"] == 10.0

        refine = client.post(
            "/api/v1/global/refine-plan",
            json={"candidate_id": "coarse-v1:a", "per_side": 2},
        )
        assert refine.status_code == 200, refine.text
        assert len(refine.json()["windows"]) == 4
        assert all(
            window["method_version"] == "terrain-metrics-1.0.0"
            for window in refine.json()["windows"]
        )


def test_global_endpoints_without_index_are_explicit(tmp_path: Path):
    app = create_app(_settings(tmp_path, index=False))
    with TestClient(app) as client:
        coverage = client.get("/api/v1/global/coverage").json()
        assert coverage["status"] == "no_index"
        assert coverage["row_count"] == 0
        response = client.post(
            "/api/v1/global/search",
            json={"target": {"coarse_slope_mean_deg": {"value": 10, "weight": 1}}},
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "no_indexed_data"


def test_global_search_validation_is_clear(tmp_path: Path):
    app = create_app(_settings(tmp_path, index=True))
    with TestClient(app) as client:
        unknown = client.post(
            "/api/v1/global/search",
            json={"target": {"not_a_feature": {"value": 1, "weight": 1}}},
        )
        assert unknown.status_code == 422
        no_weight = client.post(
            "/api/v1/global/search",
            json={"target": {"coarse_slope_mean_deg": {"value": 10, "weight": 0}}},
        )
        assert no_weight.status_code == 422


def test_api_can_select_an_explicit_immutable_snapshot(tmp_path: Path):
    index_dir = tmp_path / "global-index"
    build_index(
        [_row("coarse-v1:a", 10.0, 20.0, 0.5)],
        root=index_dir,
        snapshot_id="snap-a",
    )
    build_index(
        [_row("coarse-v1:a", 18.0, 28.0, 0.2)],
        root=index_dir,
        snapshot_id="snap-b",
    )
    settings = _settings(tmp_path, index=False).model_copy(
        update={
            "global_index_dir": str(index_dir),
            "global_snapshot_id": "snap-a",
        }
    )
    with TestClient(create_app(settings)) as client:
        coverage = client.get("/api/v1/global/coverage").json()
        assert coverage["snapshot_id"] == "snap-a"
        candidate = client.get("/api/v1/global/candidates/coarse-v1:a").json()
        assert candidate["slope_mean_deg"] == 10.0


def test_coverage_reports_multiple_regions(tmp_path: Path):
    index_dir = tmp_path / "global-index"
    build_index(
        [
            _row("coarse-v1:a", 10.0, 20.0, 0.5, region="region-a"),
            _row("coarse-v1:b", 12.0, 22.0, 0.6, region="region-b"),
        ],
        root=index_dir,
        snapshot_id="multi",
    )
    settings = _settings(tmp_path, index=False).model_copy(
        update={"global_index_dir": str(index_dir)}
    )
    with TestClient(create_app(settings)) as client:
        coverage = client.get("/api/v1/global/coverage").json()
        assert coverage["regions"] == ["region-a", "region-b"]
        assert coverage["row_count"] == 2


def test_real_geomorpho_pilot_index_is_served_by_global_api(tmp_path: Path):
    settings = Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'real-coarse.db'}",
        manifest_dir=str(tmp_path / "none"),
        manifest_dir_planetary=str(tmp_path / "none"),
        manifest_dir_context=str(tmp_path / "none"),
        global_index_dir=str(ROOT / "pipelines/coarse/real-pilot/index"),
        seed_include_synthetic=False,
    )
    app = create_app(settings)
    with TestClient(app) as client:
        coverage = client.get("/api/v1/global/coverage")
        assert coverage.status_code == 200, coverage.text
        body = coverage.json()
        assert body["status"] == "ready"
        assert body["row_count"] == 168
        assert body["regions"] == ["pilot-alps", "pilot-grand-canyon", "pilot-sahara"]
        assert body["label"] == "local_coarse_index_not_global_exhaustive"

        search = client.post(
            "/api/v1/global/search",
            json={
                "target": {"coarse_slope_mean_deg": {"value": 8.0, "weight": 1.0}},
                "limit": 3,
            },
        )
        assert search.status_code == 200, search.text
        result = search.json()
        assert result["indexed_candidate_count"] == 168
        assert result["eligible_candidate_count"] == 168
        assert len(result["results"]) == 3
        assert result["coverage_label"] == "local_coarse_index_not_global_exhaustive"


def test_real_pilot_search_exposes_region_scope_versions_and_never_claims_global(tmp_path: Path):
    """The committed 168-cell pilot must publish its own scope, versions and regions.

    This is the API-level counterpart of ``pipelines/tests/test_real_coarse_pilot.py``:
    nothing here may be relabelled global, exhaustive, or fine (90 m) support.
    """
    index_root = ROOT / "pipelines/coarse/real-pilot/index"
    snapshot_id = "geomorpho250-real-pilot-20261115"
    manifest = json.loads(
        (index_root / "snapshots" / snapshot_id / "manifest.json").read_text(encoding="utf-8")
    )
    region_rows = {
        partition["region_id"]: partition["rows"] for partition in manifest["partitions"]
    }
    settings = Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'real-coarse-scope.db'}",
        manifest_dir=str(tmp_path / "none"),
        manifest_dir_planetary=str(tmp_path / "none"),
        manifest_dir_context=str(tmp_path / "none"),
        global_index_dir=str(index_root),
        seed_include_synthetic=False,
    )
    target = {"coarse_slope_mean_deg": {"value": 8.0, "weight": 1.0}}
    with TestClient(create_app(settings)) as client:
        coverage = client.get("/api/v1/global/coverage")
        assert coverage.status_code == 200, coverage.text
        body = coverage.json()
        assert body["snapshot_id"] == snapshot_id
        assert body["method_version"] == COARSE_METHOD_VERSION
        assert body["label"] == "local_coarse_index_not_global_exhaustive"
        assert "global" not in body["label"].split("_")[0]
        assert "not_global" in body["label"]
        assert body["row_count"] == manifest["row_count"] == 168
        assert body["regions"] == manifest["coverage"]["regions"]
        assert sorted(region_rows) == body["regions"]
        assert sum(region_rows.values()) == body["row_count"]
        # Coverage bbox is the union of the three pilot windows, not a world extent.
        assert body["bbox"] == manifest["coverage"]["bbox"]
        assert body["bbox"]["west"] > -180 and body["bbox"]["east"] < 180
        assert body["bbox"]["east"] - body["bbox"]["west"] < 180
        assert "not an exhaustive global claim" in body["note"]

        search = client.post("/api/v1/global/search", json={"target": target, "limit": 10})
        assert search.status_code == 200, search.text
        first = search.json()
        assert first["snapshot_id"] == snapshot_id
        assert first["method_version"] == COARSE_METHOD_VERSION
        assert first["index_version"] == "coarse-index-1.0.0"
        assert first["coverage_label"] == body["label"]
        assert first["indexed_candidate_count"] == 168
        assert first["eligible_candidate_count"] == 168
        assert first["excluded_candidate_count"] == 0
        assert "not the fine 90 m terrain result" in first["note"]
        assert len(first["results"]) == 10
        assert {item["region_id"] for item in first["results"]} <= set(body["regions"])
        distances = [item["distance"] for item in first["results"]]
        assert distances == sorted(distances)
        for item in first["results"]:
            assert item["method_version"] == COARSE_METHOD_VERSION
            assert item["source_resolution_m"] == 250.0
            assert item["window_size_m"] == 5000.0
            assert "real_geomorpho250" in item["qa_flags"]
            assert "synthetic_fixture" not in item["qa_flags"]
            assert item["body"] == "Earth"
        # Grid/dataset provenance is exposed by the candidate detail endpoint (the
        # search list intentionally carries only the fields the UI needs).
        detail = client.get(f"/api/v1/global/candidates/{first['results'][0]['candidate_id']}")
        assert detail.status_code == 200, detail.text
        detail_body = detail.json()
        assert detail_body["grid_version"] == "coarse-grid-1.0.0"
        assert detail_body["method_version"] == COARSE_METHOD_VERSION
        assert detail_body["dataset_id"] == "geomorpho250-slope-v1.0"
        assert detail_body["region_id"] in body["regions"]
        assert detail_body["source_resolution_m"] == 250.0
        # Same request, same rows, same order: no reliance on index scan order.
        again = client.post("/api/v1/global/search", json={"target": target, "limit": 10})
        assert [item["candidate_id"] for item in again.json()["results"]] == [
            item["candidate_id"] for item in first["results"]
        ]

        # A region filter reports that region's coverage, not the pilot total.
        for region_id, rows in sorted(region_rows.items()):
            scoped = client.post(
                "/api/v1/global/search",
                json={"target": target, "limit": 10, "region_id": region_id},
            )
            assert scoped.status_code == 200, scoped.text
            scoped_body = scoped.json()
            assert {item["region_id"] for item in scoped_body["results"]} == {region_id}
            assert scoped_body["indexed_candidate_count"] == 168
            assert scoped_body["eligible_candidate_count"] == rows
            assert scoped_body["coverage_label"] == body["label"]

        unknown = client.post(
            "/api/v1/global/search",
            json={"target": target, "limit": 10, "region_id": "not-a-pilot-region"},
        )
        assert unknown.status_code == 200, unknown.text
        assert unknown.json()["results"] == []
        assert unknown.json()["eligible_candidate_count"] == 0
        assert unknown.json()["excluded_candidate_count"] == 168
        assert unknown.json()["coverage_label"] == body["label"]
