from __future__ import annotations

from pathlib import Path

from analog_api.config import Settings
from analog_api.domain.aoi import AoiResult
from analog_api.domain.entities import DatasetEntity
from analog_api.main import create_app
from analog_pipelines.coarse_features import COARSE_METHOD_VERSION
from analog_pipelines.coarse_index import CoarseRow, build_index
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[3]

DATASET = DatasetEntity(
    dataset_id="test-refine-dem",
    provider="Test DEM provider",
    product_id="test-dem-v1",
    title="Test DEM",
    url="https://example.invalid/dem",
    license="Test licence",
    license_url="https://example.invalid/licence",
    access_timestamp="2026-11-15T00:00:00Z",
    source_crs="EPSG:4326",
    native_resolution_m=90.0,
    processing_version="terrain-metrics-1.0.0",
    checksum=None,
    source_kind="earth_dem",
    notes="Synthetic provider used only in tests.",
)


class FakeProcessor:
    def analyze(self, request) -> AoiResult:
        return AoiResult(
            area_id=request.area_id,
            bbox=request.bbox,
            geometry=None,
            area_km2=request.area_km2,
            center_lon=(request.bbox.west + request.bbox.east) / 2.0,
            center_lat=(request.bbox.south + request.bbox.north) / 2.0,
            crs="EPSG:32633",
            valid_fraction=1.0,
            window_size_m=request.width_m,
            resolution_m=90.0,
            features={feature_id: 5.0 for feature_id in request.requested_features},
            context_observations={},
            datasets=(DATASET,),
            provider_status={DATASET.dataset_id: {"status": "verified"}},
            quality_flags=("on_demand_aoi",),
            warnings=(),
            checksum="sha256:test-refine",
        )


def _row() -> CoarseRow:
    return CoarseRow(
        candidate_id="coarse-v1:refine-a",
        grid_id="coarse-grid-1.0.0",
        region_id="pilot-test",
        body="Earth",
        west=10.0,
        south=45.0,
        east=10.06,
        north=45.045,
        crs="EPSG:32633",
        area_km2=25.0,
        slope_mean_deg=10.0,
        slope_p90_deg=20.0,
        flat_fraction=0.5,
        valid_fraction=1.0,
        method_version=COARSE_METHOD_VERSION,
        grid_version="coarse-grid-1.0.0",
        dataset_id="fixture",
        source_asset_id="fixture-asset",
        qa_flags=["synthetic_fixture"],
    )


def _settings(tmp_path: Path) -> Settings:
    index_dir = tmp_path / "global-index"
    build_index([_row()], root=index_dir, snapshot_id="refine-fixture")
    return Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'refine.db'}",
        manifest_dir=str(tmp_path / "none"),
        manifest_dir_planetary=str(tmp_path / "none"),
        manifest_dir_context=str(tmp_path / "none"),
        global_index_dir=str(index_dir),
        seed_include_synthetic=False,
        aoi_inline=True,
    )


def test_global_refine_executes_persists_and_becomes_searchable(tmp_path: Path):
    app = create_app(_settings(tmp_path), aoi_processor=FakeProcessor())
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/global/refine",
            json={"candidate_id": "coarse-v1:refine-a"},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "completed"
        assert body["area_id"].startswith("earth-refine-")
        assert body["coarse_method_version"] == COARSE_METHOD_VERSION
        assert body["fine_method_version"] == "terrain-metrics-1.0.0"
        assert body["focus_window"]["window_m"] == 1980.0
        assert body["focus_window"]["placement_rule"].startswith("coarse_recorded_metric")
        area_id = body["area_id"]

        location = client.get(f"/api/v1/locations/{area_id}")
        assert location.status_code == 200, location.text
        detail = location.json()
        assert detail["origin"] == "on_demand"
        assert detail["features"]

        # The refined area participates in the normal Earth search index.
        search = client.post(
            "/api/v1/search",
            json={
                "body": "custom",
                "target": {"slope_mean_deg": {"value": 5.0, "weight": 1.0}},
                "limit": 10,
            },
        )
        assert search.status_code == 200, search.text
        ids = {result["location_id"] for result in search.json()["results"]}
        assert area_id in ids

        again = client.post(
            "/api/v1/global/refine",
            json={"candidate_id": "coarse-v1:refine-a"},
        )
        assert again.status_code == 200, again.text
        assert again.json()["cached"] is True
        assert again.json()["job_id"] == body["job_id"]

        missing = client.post(
            "/api/v1/global/refine",
            json={"candidate_id": "coarse-v1:missing"},
        )
        assert missing.status_code == 404


def test_global_refine_keeps_coarse_and_fine_methods_separate(tmp_path: Path):
    app = create_app(_settings(tmp_path), aoi_processor=FakeProcessor())
    with TestClient(app) as client:
        body = client.post(
            "/api/v1/global/refine",
            json={"candidate_id": "coarse-v1:refine-a"},
        ).json()
        assert "not" in body["note"].lower() or "never" in body["note"].lower()
        assert body["coarse_method_version"] != body["fine_method_version"]
        detail = client.get(f"/api/v1/locations/{body['area_id']}").json()
        methods = {observation["method_version"] for observation in detail["features"]}
        assert methods <= {"terrain-metrics-1.0.0", "flat-terrain-fraction-1.0.0"}
        assert COARSE_METHOD_VERSION not in methods
