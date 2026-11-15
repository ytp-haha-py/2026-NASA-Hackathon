from __future__ import annotations

import json
from pathlib import Path

from analog_api.adapters.release_import import import_release
from analog_api.adapters.typed_bundle import import_typed_bundle
from analog_api.adapters.typed_db import TypedObservationStore
from analog_api.config import Settings
from analog_api.main import create_app
from analog_pipelines.typed_contracts import (
    AreaV2,
    SourceAssetV2,
    TypedObservation,
)
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[3]


def test_typed_observations_are_additive_and_queryable(tmp_path: Path):
    settings = Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'typed.db'}",
        manifest_dir=str(tmp_path / "none"),
        manifest_dir_planetary=str(tmp_path / "none"),
        manifest_dir_context=str(tmp_path / "none"),
        seed_include_synthetic=False,
    )
    app = create_app(settings)
    with TestClient(app) as client:
        observation = TypedObservation.model_validate(
            json.loads(
                (ROOT / "samples/fixtures/dimensions/d02_mineral_detection.json").read_text(
                    encoding="utf-8"
                )
            )[0]
        )
        area = AreaV2(
            area_id=observation.area_id,
            body="Earth",
            bbox={"west": 10.0, "south": 45.0, "east": 10.01, "north": 45.01},
            crs="EPSG:4326",
            coordinate_frame="earth_wgs84",
        )
        asset = SourceAssetV2(
            asset_id=observation.source_asset_ids[0],
            dataset_id="fixture",
            uri="local://fixture",
            checksum_algorithm="sha256",
            checksum="sha256:" + "a" * 64,
            checksum_scope="derived",
            native_crs="EPSG:4326",
        )
        with app.state.session_factory() as session:
            store = TypedObservationStore(session)
            store.upsert_asset(asset)
            store.upsert_area(area)
            store.upsert_observation(observation)
            session.commit()

        response = client.get("/api/v1/typed/observations", params={"dimension": "D02"})
        assert response.status_code == 200
        body = response.json()
        assert len(body) == 1
        assert body[0]["state"] == "detected"
        assert body[0]["value"]["indicator"] == 1
        assert body[0]["value_kind"] == "detection"

        area_response = client.get(f"/api/v1/typed/areas/{area.area_id}")
        assert area_response.status_code == 200
        assert area_response.json()["observations"][0]["feature_id"] == "mineral_spectral_detection"

        # Existing legacy endpoints remain intact with an empty v1 index.
        assert client.get("/api/v1/health").status_code == 200
        capabilities = client.get("/api/v1/capabilities").json()
        assert capabilities["indexed_candidate_count"] == 0


def test_committed_d05_power_release_imports_and_queries(tmp_path: Path):
    settings = Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'd05.db'}",
        manifest_dir=str(tmp_path / "none"),
        manifest_dir_planetary=str(tmp_path / "none"),
        manifest_dir_context=str(tmp_path / "none"),
        seed_include_synthetic=False,
    )
    app = create_app(settings)
    with TestClient(app) as client:
        with app.state.session_factory() as session:
            result = import_release(session, ROOT / "pipelines/pilots/d05_earth_power")
            session.commit()
        assert result["observations"] == 10
        response = client.get(
            "/api/v1/typed/observations", params={"dimension": "D05", "body": "Earth"}
        )
        assert response.status_code == 200, response.text
        rows = response.json()
        assert len(rows) == 10
        assert {row["state"] for row in rows} == {"detected"}
        assert {row["score_role"] for row in rows} == {"context_only"}
        assert {row["spatial_support"]["source_resolution_m"] for row in rows} == {50000.0}
        area = client.get("/api/v1/typed/areas/earth-atacama-desert")
        assert area.status_code == 200, area.text
        assert len(area.json()["observations"]) == 2


def test_committed_geology_bundle_imports_as_evidence_only_d02(tmp_path: Path):
    settings = Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'geology.db'}",
        manifest_dir=str(tmp_path / "none"),
        manifest_dir_planetary=str(tmp_path / "none"),
        manifest_dir_context=str(tmp_path / "none"),
        seed_include_synthetic=False,
    )
    app = create_app(settings)
    with TestClient(app) as client:
        with app.state.session_factory() as session:
            result = import_typed_bundle(session, ROOT / "pipelines/geology/typed")
            session.commit()
        assert result == {
            "areas": 8,
            "assets": 33,
            "observations": 56,
            "dimensions": ["D02"],
        }
        response = client.get("/api/v1/typed/observations", params={"dimension": "D02"})
        assert response.status_code == 200, response.text
        rows = response.json()
        assert len(rows) == 56
        assert {row["body"] for row in rows} == {"Earth", "Moon", "Mars"}
        assert {row["score_role"] for row in rows} == {"evidence_only"}
        assert {row["state"] for row in rows} >= {"detected", "unknown", "no_coverage"}
        assert not any(row["feature_id"].startswith("coarse_") for row in rows)
        coverage = client.get("/api/v1/typed/coverage")
        assert coverage.status_code == 200, coverage.text
        assert coverage.json()["by_dimension"]["D02"]["total"] == 56
        assert coverage.json()["by_dimension"]["D02"]["by_body"] == {
            "Earth": 15,
            "Mars": 38,
            "Moon": 3,
        }
        moon = client.get("/api/v1/typed/areas/moon-shackleton-rim-site04-roi2")
        assert moon.status_code == 200, moon.text
        assert {item["feature_id"] for item in moon.json()["observations"]} >= {
            "geologic_unit",
            "geologic_process",
        }
        mars = client.get("/api/v1/typed/areas/mars-test-jezero-western-delta")
        assert mars.status_code == 200, mars.text
        assert mars.json()["crs"].startswith("+proj=laea")
        assert mars.json()["coordinate_frame"] == "mars_projected"


def test_committed_d06_soilgrids_release_imports_and_queries(tmp_path: Path):
    settings = Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'd06.db'}",
        manifest_dir=str(tmp_path / "none"),
        manifest_dir_planetary=str(tmp_path / "none"),
        manifest_dir_context=str(tmp_path / "none"),
        seed_include_synthetic=False,
    )
    app = create_app(settings)
    with TestClient(app) as client:
        with app.state.session_factory() as session:
            result = import_release(session, ROOT / "pipelines/pilots/d06_earth_soilgrids")
            session.commit()
        assert result["observations"] == 75
        response = client.get(
            "/api/v1/typed/observations", params={"dimension": "D06", "body": "Earth"}
        )
        assert response.status_code == 200, response.text
        rows = response.json()
        assert len(rows) == 75
        assert {row["state"] for row in rows} == {"detected"}
        assert {row["score_role"] for row in rows} == {"evidence_only"}
        assert {row["spatial_support"]["resolution_m"] for row in rows} == {250.0}
        assert {row["spatial_support"]["native_resolution_m"] for row in rows} == {250.0}
        assert any(row["depth_support"]["max_m"] == 0.05 for row in rows)
        coverage = client.get("/api/v1/typed/coverage").json()
        assert coverage["by_dimension"]["D06"]["total"] == 75
