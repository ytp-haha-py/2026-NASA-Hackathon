from __future__ import annotations

from pathlib import Path

from analog_api.adapters.planetary_store import persist_planetary_aoi_result
from analog_api.adapters.seed import make_engine
from analog_api.adapters.typed_db import TypedObservationStore, init_typed_db
from analog_api.domain.aoi import AoiResult
from analog_api.domain.entities import BBox, DatasetEntity
from sqlalchemy.orm import Session


def _dataset() -> DatasetEntity:
    return DatasetEntity(
        dataset_id="test-planetary-dem",
        provider="NASA test",
        product_id="LOLA-test",
        title="Test planetary DEM",
        url="https://example.invalid/dem.tif",
        license="public domain",
        license_url="https://example.invalid/license",
        access_timestamp="2026-11-15T00:00:00Z",
        source_crs="+proj=longlat +R=1737400",
        native_resolution_m=236.9,
        processing_version="coarse-planetary-dem-horn-1.0.0",
        checksum=None,
        source_kind="planetary_dem",
        notes="Test only.",
    )


def test_planetary_aoi_persists_as_typed_evidence_not_earth_candidate(tmp_path: Path):
    engine = make_engine(f"sqlite+pysqlite:///{tmp_path / 'planetary.db'}")
    init_typed_db(engine)
    result = AoiResult(
        area_id="planet-aoi-moon-test",
        bbox=BBox(19.9, -10.1, 20.1, -9.9),
        geometry=None,
        area_km2=25.0,
        center_lon=20.0,
        center_lat=-10.0,
        crs="+proj=longlat +R=1737400.0 +no_defs",
        valid_fraction=1.0,
        window_size_m=5000.0,
        resolution_m=250.0,
        features={
            "coarse_slope_mean_deg": 7.5,
            "coarse_slope_p90_deg": 12.0,
            "coarse_flat_fraction_lt5deg": 0.25,
        },
        context_observations={},
        datasets=(_dataset(),),
        provider_status={"nasa-pgda-lola": {"status": "verified"}},
        quality_flags=("on_demand_planetary_aoi",),
        warnings=(),
        checksum="sha256:test",
        body="Moon",
        coordinate_frame="lunar_planetocentric",
        method_version="coarse-planetary-dem-horn-1.0.0",
        dimension_support={
            "D01": {"status": "present"},
            "D02": {"status": "unsupported_for_on_demand_analysis"},
        },
        source_assets=(
            {
                "uri": "samples/planetary/coarse/fixtures/moon_test.tif",
                "sha256": "a" * 64,
            },
        ),
        coverage_note="Bounded pilot fixture.",
        analysis_kind="committed_reference_fixture",
    )
    with Session(engine) as session:
        data_version = persist_planetary_aoi_result(session, result)
        assert data_version.startswith("dv-")
        store = TypedObservationStore(session)
        area = store.get_area("planet-aoi-moon-test")
        assert area is not None
        assert area["body"] == "Moon"
        assert area["coordinate_frame"] == "lunar_planetocentric"
        assert not area["crs"].upper().startswith("EPSG:4326")
        dimensions = {row["dimension_code"] for row in area["observations"]}
        assert dimensions == {"D01"}
        assert all(row["score_role"] == "evidence_only" for row in area["observations"])
        assert all("not_earth_wgs84" in row["quality_flags"] for row in area["observations"])
        assert {row["feature_id"] for row in area["observations"]} == {
            "coarse_slope_mean_deg",
            "coarse_slope_p90_deg",
            "coarse_flat_fraction_lt5deg",
        }


def test_planetary_persistence_is_idempotent(tmp_path: Path):
    engine = make_engine(f"sqlite+pysqlite:///{tmp_path / 'planetary-two.db'}")
    init_typed_db(engine)
    dataset = _dataset()
    result = AoiResult(
        area_id="planet-aoi-mars-test",
        bbox=BBox(78.1, 18.5, 78.2, 18.6),
        geometry=None,
        area_km2=25.0,
        center_lon=78.15,
        center_lat=18.55,
        crs="+proj=longlat +R=3396190.0 +no_defs",
        valid_fraction=1.0,
        window_size_m=5000.0,
        resolution_m=250.0,
        features={"coarse_slope_mean_deg": 4.0},
        context_observations={},
        datasets=(dataset,),
        provider_status={},
        quality_flags=(),
        warnings=(),
        checksum="sha256:test",
        body="Mars",
        coordinate_frame="mars_planetocentric",
        method_version="coarse-planetary-dem-horn-1.0.0",
    )
    with Session(engine) as session:
        persist_planetary_aoi_result(session, result)
        persist_planetary_aoi_result(session, result)
        observations = TypedObservationStore(session).list_observations(
            area_id="planet-aoi-mars-test"
        )
        assert len(observations) == 1
        assert observations[0]["body"] == "Mars"
