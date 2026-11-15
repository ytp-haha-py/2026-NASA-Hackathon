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


def test_committed_d05_spice_model_geometry_imports_and_queries(tmp_path: Path):
    settings = Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'd05-spice.db'}",
        manifest_dir=str(tmp_path / "none"),
        manifest_dir_planetary=str(tmp_path / "none"),
        manifest_dir_context=str(tmp_path / "none"),
        seed_include_synthetic=False,
    )
    app = create_app(settings)
    with TestClient(app) as client:
        with app.state.session_factory() as session:
            result = import_release(session, ROOT / "pipelines/pilots/d05_spice_illumination")
            session.commit()
        assert result["areas"] == 9
        assert result["observations"] == 18
        response = client.get("/api/v1/typed/observations", params={"dimension": "D05"})
        assert response.status_code == 200, response.text
        rows = response.json()
        assert len(rows) == 18
        assert {row["body"] for row in rows} == {"Moon", "Mars"}
        assert {row["feature_id"] for row in rows} == {
            "solar_incidence_deg",
            "solar_elevation_deg",
        }
        assert {row["state"] for row in rows} == {"detected"}
        assert {row["score_role"] for row in rows} == {"context_only"}
        assert {row["unit"] for row in rows} == {"degree"}
        assert not any(row["feature_id"].startswith("ionizing") for row in rows)
        assert not any(row["unit"] in {"Gy", "Sv"} for row in rows)
        assert all("flat_ellipsoid_tangent_plane" in row["quality_flags"] for row in rows)
        moon = client.get("/api/v1/typed/areas/moon-shackleton-rim-site04-roi1")
        assert moon.status_code == 200, moon.text
        assert moon.json()["coordinate_frame"] == "lunar_projected"
        assert {item["feature_id"] for item in moon.json()["observations"]} == {
            "solar_incidence_deg",
            "solar_elevation_deg",
        }
        mars = client.get("/api/v1/typed/areas/mars-jezero-ctx-window-01")
        assert mars.status_code == 200, mars.text
        assert mars.json()["coordinate_frame"] == "mars_projected"
        coverage = client.get("/api/v1/typed/coverage").json()
        assert coverage["by_dimension"]["D05"]["total"] == 18
        assert coverage["by_dimension"]["D05"]["by_body"] == {"Mars": 10, "Moon": 8}


def test_committed_planetary_thermal_bundle_imports_as_evidence_only_d03(tmp_path: Path):
    settings = Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'thermal.db'}",
        manifest_dir=str(tmp_path / "none"),
        manifest_dir_planetary=str(tmp_path / "none"),
        manifest_dir_context=str(tmp_path / "none"),
        seed_include_synthetic=False,
    )
    app = create_app(settings)
    with TestClient(app) as client:
        with app.state.session_factory() as session:
            result = import_typed_bundle(session, ROOT / "pipelines/pilots/d03_planetary_thermal")
            session.commit()
        assert result == {
            "areas": 23,
            "assets": 3,
            "observations": 41,
            "dimensions": ["D03"],
        }
        response = client.get("/api/v1/typed/observations", params={"dimension": "D03"})
        assert response.status_code == 200, response.text
        rows = response.json()
        assert len(rows) == 41
        assert {row["body"] for row in rows} == {"Moon", "Mars"}
        assert {row["score_role"] for row in rows} == {"evidence_only"}
        assert {row["state"] for row in rows} == {"detected", "unknown", "no_coverage"}
        assert {row["feature_id"] for row in rows} == {
            "surface_brightness_temperature_k",
            "thermal_inertia",
        }
        assert not any(row["feature_id"] == "surface_temperature_mean_k" for row in rows)
        thermal_inertia = [row for row in rows if row["feature_id"] == "thermal_inertia"]
        assert len(thermal_inertia) == 5
        assert {row["spatial_support"]["native_resolution_m"] for row in thermal_inertia} == {100.0}
        brightness = [
            row for row in rows if row["feature_id"] == "surface_brightness_temperature_k"
        ]
        assert len(brightness) == 36
        assert {row["spatial_support"]["native_resolution_m"] for row in brightness} == {240.0}
        assert not any(row["spatial_support"]["resolution_m"] == 90.0 for row in rows)
        coverage = client.get("/api/v1/typed/coverage")
        assert coverage.status_code == 200, coverage.text
        assert coverage.json()["by_dimension"]["D03"]["total"] == 41
        assert coverage.json()["by_dimension"]["D03"]["by_body"] == {
            "Mars": 5,
            "Moon": 36,
        }


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


def test_committed_d06_soilgrids_depth_release_preserves_depths(tmp_path: Path):
    settings = Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'd06-depths.db'}",
        manifest_dir=str(tmp_path / "none"),
        manifest_dir_planetary=str(tmp_path / "none"),
        manifest_dir_context=str(tmp_path / "none"),
        seed_include_synthetic=False,
    )
    app = create_app(settings)
    with TestClient(app) as client:
        with app.state.session_factory() as session:
            result = import_release(session, ROOT / "pipelines/pilots/d06_earth_soilgrids_depths")
            session.commit()
        assert result["observations"] == 300
        assert result["integrity"]["observations"] == 300
        response = client.get(
            "/api/v1/typed/observations", params={"dimension": "D06", "body": "Earth"}
        )
        assert response.status_code == 200, response.text
        rows = response.json()
        assert len(rows) == 300
        assert {row["state"] for row in rows} == {"detected"}
        assert {row["score_role"] for row in rows} == {"evidence_only"}
        depth_counts: dict[tuple[float, float], int] = {}
        for row in rows:
            key = (
                row["depth_support"]["min_m"],
                row["depth_support"]["max_m"],
            )
            depth_counts[key] = depth_counts.get(key, 0) + 1
        assert depth_counts == {
            (0.0, 0.05): 75,
            (0.05, 0.15): 75,
            (0.15, 0.3): 75,
            (0.3, 0.6): 75,
        }
        assert all("model_prediction_not_measurement" in row["quality_flags"] for row in rows)
        coverage = client.get("/api/v1/typed/coverage").json()
        assert coverage["by_dimension"]["D06"]["total"] == 300


def test_committed_d03_power_thermal_release_imports_and_queries(tmp_path: Path):
    settings = Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'd03.db'}",
        manifest_dir=str(tmp_path / "none"),
        manifest_dir_planetary=str(tmp_path / "none"),
        manifest_dir_context=str(tmp_path / "none"),
        seed_include_synthetic=False,
    )
    app = create_app(settings)
    with TestClient(app) as client:
        with app.state.session_factory() as session:
            result = import_release(session, ROOT / "pipelines/pilots/d03_earth_power")
            session.commit()
        assert result["observations"] == 20
        response = client.get(
            "/api/v1/typed/observations", params={"dimension": "D03", "body": "Earth"}
        )
        assert response.status_code == 200, response.text
        rows = response.json()
        assert len(rows) == 20
        assert {row["state"] for row in rows} == {"detected"}
        assert {row["score_role"] for row in rows} == {"context_only"}
        assert {row["unit"] for row in rows} == {"degree_Celsius"}
        assert {row["spatial_support"]["source_resolution_m"] for row in rows} == {50000.0}
        coverage = client.get("/api/v1/typed/coverage").json()
        assert coverage["by_dimension"]["D03"]["total"] == 20


def test_committed_d04_power_water_context_imports_and_queries(tmp_path: Path):
    settings = Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'd04.db'}",
        manifest_dir=str(tmp_path / "none"),
        manifest_dir_planetary=str(tmp_path / "none"),
        manifest_dir_context=str(tmp_path / "none"),
        seed_include_synthetic=False,
    )
    app = create_app(settings)
    with TestClient(app) as client:
        with app.state.session_factory() as session:
            result = import_release(session, ROOT / "pipelines/pilots/d04_earth_power")
            session.commit()
        assert result["observations"] == 20
        rows = client.get(
            "/api/v1/typed/observations", params={"dimension": "D04", "body": "Earth"}
        ).json()
        assert len(rows) == 20
        assert {row["state"] for row in rows} == {"detected"}
        assert {row["score_role"] for row in rows} == {"context_only"}
        assert {row["feature_id"] for row in rows} == {
            "surface_soil_wetness_fraction",
            "root_zone_soil_wetness_fraction",
            "snow_depth_cm",
            "precipitation_mm_day",
        }
        assert any("not_confirmed_subsurface_ice" in row["quality_flags"] for row in rows)
