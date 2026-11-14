from __future__ import annotations

import json
from pathlib import Path

from analog_api.adapters.db import AnalysisJob, Candidate, ContextObservation, FeatureObservation
from analog_api.adapters.seed import make_engine, seed_manifests
from analog_api.adapters.typed_db import TypedObservationStore
from analog_api.data.synthetic import synthetic_manifest
from analog_api.domain.manifest import Manifest
from analog_pipelines.typed_contracts import (
    AreaV2,
    SourceAssetV2,
    TypedObservation,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[2]


def test_reset_preserves_aois_jobs_and_typed_observations(tmp_path: Path):
    engine = make_engine(f"sqlite+pysqlite:///{tmp_path / 'runtime.db'}")
    manifest = Manifest.model_validate(synthetic_manifest())
    seed_manifests(engine, [manifest], reset=True)

    observation = TypedObservation.model_validate(
        json.loads(
            (ROOT / "samples/fixtures/dimensions/d02_mineral_detection.json").read_text(
                encoding="utf-8"
            )
        )[0]
    )

    with Session(engine) as session:
        aoi = Candidate(
            location_id="earth-aoi-runtime-preserved",
            display_name="Runtime AOI",
            lat=10.0,
            lon=20.0,
            west=19.99,
            south=9.99,
            east=20.01,
            north=10.01,
            dataset_id="synthetic-test-fixture",
            window_size_m=1980,
            analysis_resolution_m=90,
            crs="EPSG:32634",
            valid_fraction=1.0,
            quality_flags=["on_demand_aoi"],
            notes="user runtime data",
            data_kind="observed",
            origin="on_demand",
        )
        session.add(aoi)
        session.add(
            FeatureObservation(
                candidate_id=aoi.location_id,
                feature_id="slope_mean_deg",
                value=5.0,
                unit="degree",
                method_version="terrain-metrics-1.0.0",
                extraction_scale_m=90,
                valid_fraction=1.0,
                quality="verified",
            )
        )
        session.add(
            ContextObservation(
                candidate_id=aoi.location_id,
                dataset_id="synthetic-test-fixture",
                feature_id="landcover_water_fraction",
                value=0.1,
                unit="fraction",
                method_version="worldcover-2021-v200-1.0.0",
                source_resolution_m=10,
                valid_fraction=1.0,
                quality="verified",
                class_code=80,
                provider="fixture",
            )
        )
        session.add(
            AnalysisJob(
                job_id="job-preserved",
                status="completed",
                cache_key="cache-preserved",
                idempotency_key="idem-preserved",
                area_id=aoi.location_id,
                request_json={"body": "Earth"},
                provider_status={"fixture": {"status": "verified"}},
            )
        )
        session.commit()

        store = TypedObservationStore(session)
        store.upsert_asset(
            SourceAssetV2(
                asset_id="asset-runtime",
                dataset_id="fixture-dataset",
                uri="local://runtime",
                checksum_algorithm="sha256",
                checksum="sha256:" + "a" * 64,
                checksum_scope="derived",
                native_crs="EPSG:4326",
            )
        )
        store.upsert_area(
            AreaV2(
                area_id=observation.area_id,
                body="Earth",
                bbox={"west": 10, "south": 45, "east": 10.01, "north": 45.01},
                crs="EPSG:4326",
                coordinate_frame="earth_wgs84",
            )
        )
        store.upsert_observation(observation)
        session.commit()

    # Rebuild the baseline scientific index. Runtime state must survive.
    seed_manifests(engine, [manifest], reset=True)

    with Session(engine) as session:
        candidates = list(session.scalars(select(Candidate)).all())
        assert len(candidates) == 5  # 4 baseline + 1 runtime AOI
        preserved = session.get(Candidate, "earth-aoi-runtime-preserved")
        assert preserved is not None and preserved.origin == "on_demand"
        assert session.get(AnalysisJob, "job-preserved") is not None
        assert session.scalar(
            select(FeatureObservation).where(
                FeatureObservation.candidate_id == "earth-aoi-runtime-preserved"
            )
        )
        assert session.scalar(
            select(ContextObservation).where(
                ContextObservation.candidate_id == "earth-aoi-runtime-preserved"
            )
        )
        typed = TypedObservationStore(session).list_observations(dimension="D02")
        assert typed and typed[0]["observation_id"] == observation.observation_id
