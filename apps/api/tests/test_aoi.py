from __future__ import annotations

import time
from pathlib import Path

import pytest
from analog_api.config import Settings
from analog_api.domain.aoi import AoiResult, validate_aoi_request
from analog_api.domain.entities import BBox, DatasetEntity
from analog_api.domain.errors import AppError
from analog_api.domain.features import CONTEXT_METHOD_VERSION
from analog_api.main import create_app
from fastapi.testclient import TestClient

EARTH_DATASET = DatasetEntity(
    dataset_id="test-earth-dem",
    provider="Test DEM provider",
    product_id="test-dem-v1",
    title="Test DEM",
    url="https://example.invalid/dem",
    license="Test licence",
    license_url="https://example.invalid/licence",
    access_timestamp="2026-11-14T00:00:00Z",
    source_crs="EPSG:4326",
    native_resolution_m=90.0,
    processing_version="terrain-metrics-1.0.0",
    checksum=None,
    source_kind="earth_dem",
    notes="Synthetic provider used only in tests.",
)

COVER_DATASET = DatasetEntity(
    dataset_id="test-worldcover",
    provider="Test land-cover provider",
    product_id="test-cover-v1",
    title="Test land cover",
    url="https://example.invalid/cover",
    license="Test licence",
    license_url="https://example.invalid/licence",
    access_timestamp="2026-11-14T00:00:00Z",
    source_crs="EPSG:4326",
    native_resolution_m=10.0,
    processing_version=CONTEXT_METHOD_VERSION,
    checksum=None,
    source_kind="landcover",
    notes="Context only.",
)


class FakeProcessor:
    def analyze(self, request) -> AoiResult:
        context = {
            "landcover_bare_sparse_fraction": {
                "value": 0.62,
                "unit": "fraction",
                "method_version": CONTEXT_METHOD_VERSION,
                "source_resolution_m": 10.0,
                "valid_fraction": 0.99,
                "quality": "verified",
                "class_code": 60,
                "provider": "Test land-cover provider",
                "dataset_id": COVER_DATASET.dataset_id,
            },
            "landcover_built_up_fraction": {
                "value": 0.01,
                "unit": "fraction",
                "method_version": CONTEXT_METHOD_VERSION,
                "source_resolution_m": 10.0,
                "valid_fraction": 0.99,
                "quality": "verified",
                "class_code": 50,
                "provider": "Test land-cover provider",
                "dataset_id": COVER_DATASET.dataset_id,
            },
        }
        return AoiResult(
            area_id=request.area_id,
            bbox=request.bbox,
            geometry=request.geometry,
            area_km2=request.area_km2,
            center_lon=(request.bbox.west + request.bbox.east) / 2,
            center_lat=(request.bbox.south + request.bbox.north) / 2,
            crs="EPSG:32629",
            valid_fraction=0.99,
            window_size_m=request.width_m,
            resolution_m=90.0,
            features={
                "slope_mean_deg": 4.5,
                "slope_p90_deg": 8.1,
                "terrain_relief_p95_p05_m": 72.0,
                "roughness_std_m": 18.0,
            },
            context_observations=context,
            datasets=(EARTH_DATASET, COVER_DATASET),
            provider_status={
                EARTH_DATASET.dataset_id: {"status": "verified"},
                COVER_DATASET.dataset_id: {"status": "verified"},
            },
            quality_flags=("on_demand_aoi",),
            warnings=(),
            checksum="sha256:test",
        )


def settings(tmp_path: Path, *, inline: bool = True) -> Settings:
    return Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'aoi.db'}",
        manifest_dir=str(tmp_path / "none-earth"),
        manifest_dir_planetary=str(tmp_path / "none-planetary"),
        manifest_dir_context=str(tmp_path / "none-context"),
        seed_include_synthetic=False,
        aoi_inline=inline,
    )


def bbox_payload() -> dict:
    return {
        "body": "Earth",
        "bbox": {"west": -6.617, "south": 37.682, "east": -6.600, "north": 37.699},
        "requested_features": [
            "slope_mean_deg",
            "slope_p90_deg",
            "terrain_relief_p95_p05_m",
            "roughness_std_m",
        ],
    }


def test_validate_aoi_rejects_oversized_and_small_footprints():
    with pytest.raises(AppError) as error:
        validate_aoi_request(
            bbox={"west": -6.7, "south": 37.6, "east": -6.5, "north": 37.8},
            geometry=None,
            requested_features=None,
        )
    assert error.value.code == "aoi_too_large"
    with pytest.raises(AppError) as error:
        validate_aoi_request(
            bbox={"west": -6.6005, "south": 37.6995, "east": -6.600, "north": 37.700},
            geometry=None,
            requested_features=None,
        )
    assert error.value.code == "aoi_too_small"


def test_validate_aoi_rejects_contradictory_and_unknown_input():
    with pytest.raises(AppError) as error:
        validate_aoi_request(
            bbox={"west": -6.617, "south": 37.682, "east": -6.600, "north": 37.699},
            geometry={"type": "Polygon", "coordinates": []},
            requested_features=None,
        )
    assert error.value.code == "invalid_aoi"
    with pytest.raises(AppError) as error:
        validate_aoi_request(
            bbox={"west": -6.617, "south": 37.682, "east": -6.600, "north": 37.699},
            geometry=None,
            requested_features=["gravity_m_s2"],
        )
    assert error.value.code == "unknown_feature"


def test_validate_aoi_accepts_polygon_and_is_deterministic():
    polygon = {
        "type": "Polygon",
        "coordinates": [
            [
                [-6.617, 37.682],
                [-6.600, 37.682],
                [-6.600, 37.699],
                [-6.617, 37.699],
                [-6.617, 37.682],
            ]
        ],
    }
    first = validate_aoi_request(bbox=None, geometry=polygon, requested_features=["slope_mean_deg"])
    second = validate_aoi_request(
        bbox=None, geometry=polygon, requested_features=["slope_mean_deg"]
    )
    assert first.area_id == second.area_id
    assert first.cache_key == second.cache_key
    assert first.area_id.startswith("earth-aoi-")
    assert first.area_km2 > 1.0


def test_inline_aoi_analysis_persists_context_and_cache(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ANALOG_AOI_RUNTIME_DIR", str(tmp_path / "aoi-catalog"))
    app = create_app(settings(tmp_path, inline=True), aoi_processor=FakeProcessor())
    with TestClient(app) as client:
        response = client.post("/api/v1/areas/analyze", json=bbox_payload())
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "completed"
        assert body["cached"] is False
        area_id = body["area_id"]
        assert area_id.startswith("earth-aoi-")

        features = client.get(f"/api/v1/areas/{area_id}/features")
        assert features.status_code == 200
        detail = features.json()
        assert detail["origin"] == "on_demand"
        assert detail["data_kind"] == "observed"
        assert len(detail["features"]) == 4
        assert len(detail["context_observations"]) == 2
        assert detail["context_observations"][0]["score_role"] == "context_only"
        assert any(
            "not mineral composition" in limitation for limitation in detail["known_limitations"]
        )
        assert {dataset["dataset_id"] for dataset in detail["datasets"]} == {
            "test-earth-dem",
            "test-worldcover",
        }
        runtime_manifest = tmp_path / "aoi-catalog" / f"{area_id}.json"
        assert runtime_manifest.is_file()
        assert '"kind": "on_demand_aoi_runtime"' in runtime_manifest.read_text()

        capabilities = client.get("/api/v1/capabilities").json()
        assert capabilities["indexed_candidate_count"] == 1
        assert capabilities["context_features"]
        assert "aoi_analysis" in capabilities

        # The new AOI participates in the same indexed search comparator.
        search = client.post(
            "/api/v1/search",
            json={
                "body": "custom",
                "target": {"slope_mean_deg": {"value": 4.5, "weight": 1}},
                "limit": 5,
            },
        )
        assert search.status_code == 200
        assert search.json()["results"][0]["location_id"] == area_id

        repeat = client.post("/api/v1/areas/analyze", json=bbox_payload())
        assert repeat.status_code == 200
        assert repeat.json()["cached"] is True
        assert repeat.json()["area_id"] == area_id
        assert repeat.json()["job_id"] == body["job_id"]

        compare = client.post(f"/api/v1/areas/{area_id}/compare")
        assert compare.status_code == 200, compare.text
        comparison = compare.json()
        assert comparison["results"][0]["location_id"] == area_id
        assert comparison["results"][0]["distance"] == pytest.approx(0.0)
        assert comparison["coverage"]["origins"]["on_demand"] == 1


def test_async_aoi_job_runs_and_is_queryable(tmp_path: Path):
    app = create_app(settings(tmp_path, inline=False), aoi_processor=FakeProcessor())
    with TestClient(app) as client:
        response = client.post("/api/v1/areas/analyze", json=bbox_payload())
        assert response.status_code in {200, 202}
        job_id = response.json()["job_id"]
        deadline = time.monotonic() + 10
        body = response.json()
        while body["status"] in {"queued", "running"} and time.monotonic() < deadline:
            time.sleep(0.05)
            body = client.get(f"/api/v1/jobs/{job_id}").json()
        assert body["status"] == "completed", body
        assert client.get("/api/v1/jobs/does-not-exist").status_code == 404


def test_aoi_requests_are_validated_by_the_api(tmp_path: Path):
    app = create_app(settings(tmp_path, inline=True), aoi_processor=FakeProcessor())
    with TestClient(app) as client:
        oversized = bbox_payload()
        oversized["bbox"] = {"west": -6.7, "south": 37.6, "east": -6.5, "north": 37.8}
        response = client.post("/api/v1/areas/analyze", json=oversized)
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "aoi_too_large"

        unknown_reference = bbox_payload()
        unknown_reference["reference_id"] = "moon-missing"
        response = client.post("/api/v1/areas/analyze", json=unknown_reference)
        assert response.status_code == 404

        url_attack = bbox_payload()
        url_attack["source_url"] = "https://evil.invalid/data.tif"
        response = client.post("/api/v1/areas/analyze", json=url_attack)
        assert response.status_code == 422


def test_aoi_preserves_bbox_geometry_fields():
    polygon = {
        "type": "Polygon",
        "coordinates": [
            [
                [-6.617, 37.682],
                [-6.600, 37.682],
                [-6.600, 37.699],
                [-6.617, 37.699],
                [-6.617, 37.682],
            ]
        ],
    }
    request = validate_aoi_request(bbox=None, geometry=polygon, requested_features=None)
    assert request.geometry == polygon
    assert request.bbox == BBox(west=-6.617, south=37.682, east=-6.6, north=37.699)


class FailingProcessor:
    def analyze(self, request):
        raise AppError(
            "source_extraction_failed",
            "Valid elevation fraction 0.40 is below the required 0.80.",
            status_code=422,
        )


class PartialProcessor(FakeProcessor):
    def analyze(self, request):
        base = super().analyze(request)
        return AoiResult(
            area_id=base.area_id,
            bbox=base.bbox,
            geometry=base.geometry,
            area_km2=base.area_km2,
            center_lon=base.center_lon,
            center_lat=base.center_lat,
            crs=base.crs,
            valid_fraction=base.valid_fraction,
            window_size_m=base.window_size_m,
            resolution_m=base.resolution_m,
            features=base.features,
            context_observations={},
            datasets=(EARTH_DATASET,),
            provider_status={
                EARTH_DATASET.dataset_id: {"status": "verified"},
                COVER_DATASET.dataset_id: {"status": "failed", "reason": "context_provider_failed"},
            },
            quality_flags=base.quality_flags,
            warnings=("worldcover_context_provider_failed",),
            checksum=base.checksum,
        )


def test_failed_aoi_is_explicit_and_creates_no_candidate(tmp_path: Path):
    app = create_app(settings(tmp_path), aoi_processor=FailingProcessor())
    with TestClient(app) as client:
        response = client.post("/api/v1/areas/analyze", json=bbox_payload())
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "failed"
        job = client.get(f"/api/v1/jobs/{body['job_id']}").json()
        assert job["status"] == "failed"
        assert job["error"]["code"] == "source_extraction_failed"
        assert client.get(f"/api/v1/areas/{body['area_id']}/features").status_code == 404
        assert client.get("/api/v1/capabilities").json()["indexed_candidate_count"] == 0


def test_partial_aoi_keeps_terrain_and_reports_context_failure(tmp_path: Path):
    app = create_app(settings(tmp_path), aoi_processor=PartialProcessor())
    with TestClient(app) as client:
        response = client.post("/api/v1/areas/analyze", json=bbox_payload())
        body = response.json()
        assert response.status_code == 200
        assert body["status"] == "partial"
        assert body["provider_status"]["test-worldcover"]["status"] == "failed"
        features = client.get(f"/api/v1/areas/{body['area_id']}/features")
        assert features.status_code == 200
        assert features.json()["context_observations"] == []
