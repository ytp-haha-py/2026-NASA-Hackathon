"""G2 cross-body search integration tests (Issue #170, Goal G).

The fixture loads the committed Earth fine index plus the committed planetary reference
manifests. One test intentionally drops a value from a real Earth candidate to exercise the
missing-value path; that modification is test-only and is not a stored observation.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from analog_api.adapters.repository import SqlAlchemyRepository
from analog_api.adapters.seed import ensure_seeded, make_engine
from analog_api.adapters.typed_db import TypedObservationStore, init_typed_db
from analog_api.config import Settings
from analog_api.domain.cross_body_search import (
    CrossBodySearchError,
    TargetFeatureValue,
    build_earth_candidates,
    execute_cross_body_search,
    g1_compatible_feature_ids,
    resolve_cross_body_target,
    target_from_reference,
    target_from_values,
)
from analog_api.domain.feature_service import assemble_snapshot
from analog_api.domain.multidim import MultiDimCandidate
from analog_pipelines.coarse_features import COARSE_FEATURE_BY_ID
from analog_pipelines.planetary_aoi import analyze_planetary_aoi
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def search_ctx(tmp_path_factory: pytest.TempPathFactory):
    tmp = tmp_path_factory.mktemp("cross-body-search")
    settings = Settings(
        database_url=f"sqlite+pysqlite:///{tmp / 'cross-body-search.db'}",
        manifest_dir=str(ROOT / "pipelines/earth/manifests"),
        manifest_dir_planetary=str(ROOT / "pipelines/planetary/manifests"),
        manifest_dir_context=str(ROOT / "pipelines/worldcover/manifests"),
        seed_include_synthetic=False,
        seed_include_typed=True,
    )
    engine = make_engine(settings.database_url)
    ensure_seeded(engine, settings)
    init_typed_db(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = session_factory()
    repo = SqlAlchemyRepository(session)
    typed = TypedObservationStore(session)
    snapshot = assemble_snapshot(
        rows=typed.list_observations(),
        areas=typed.list_areas(),
        candidates=repo.list_candidates(),
        references=repo.list_references(),
    )
    yield {"session": session, "repo": repo, "snapshot": snapshot}
    session.close()


def test_g1_compatible_features_match_the_five_fine_d01_contracts(search_ctx):
    registry = search_ctx["snapshot"].registry
    for body in ("Moon", "Mars"):
        ids = g1_compatible_feature_ids(registry, source_body=body)
        assert list(ids) == [
            "slope_mean_deg",
            "slope_p90_deg",
            "terrain_relief_p95_p05_m",
            "roughness_std_m",
            "flat_terrain_fraction",
        ]


def test_real_moon_and_mars_references_rank_real_earth_candidates(search_ctx):
    snapshot = search_ctx["snapshot"]
    for reference_id in ("moon-lola-site01-roi1", "mars-jezero-ctx-window-01"):
        reference = search_ctx["repo"].get_reference(reference_id)
        assert reference is not None
        target = target_from_reference(reference)
        payload = execute_cross_body_search(
            snapshot=snapshot,
            target=target,
            limit=5,
            data_version=search_ctx["repo"].data_version(),
        )
        assert payload["source"]["body"] == reference.body
        assert payload["candidate"]["body"] == "Earth"
        assert payload["eligible_candidate_count"] == 113
        assert payload["excluded_candidate_count"] == 0
        assert len(payload["results"]) == 5
        top = payload["results"][0]
        assert {item["feature_id"] for item in top["contributions"]} == {
            "slope_mean_deg",
            "slope_p90_deg",
            "terrain_relief_p95_p05_m",
            "roughness_std_m",
            "flat_terrain_fraction",
        }
        assert sum(item["contribution_fraction"] for item in top["contributions"]) == pytest.approx(
            1.0
        )
        assert top["contributions"][0]["observation_id"].startswith("d01:")
        assert "six-dimension" in payload["limitations"][0]


def test_coarse_planetary_aoi_vector_is_refused_before_ranking(search_ctx):
    bbox = {"west": 19.85, "south": -10.15, "east": 20.15, "north": -9.85}
    aoi = analyze_planetary_aoi("Moon", bbox)
    values = {
        feature_id: TargetFeatureValue(
            value=value,
            unit=COARSE_FEATURE_BY_ID[feature_id].unit,
            method_version=COARSE_FEATURE_BY_ID[feature_id].method_version,
        )
        for feature_id, value in aoi.features.items()
    }
    target = target_from_values(
        body="Moon",
        source_id=aoi.area_id,
        values=values,
        source_kind="on_demand_planetary_aoi",
        analysis_resolution_m=250.0,
        window_size_m=5000.0,
        crs=aoi.crs,
    )
    with pytest.raises(CrossBodySearchError) as captured:
        resolve_cross_body_target(target=target, registry=search_ctx["snapshot"].registry)
    assert captured.value.code == "coarse_fine_method_not_comparable"
    assert {item["reason"] for item in captured.value.details} == {
        "coarse_fine_method_not_comparable"
    }


def test_missing_candidate_value_is_excluded_with_an_explicit_reason(search_ctx):
    snapshot = search_ctx["snapshot"]
    reference = search_ctx["repo"].get_reference("moon-lola-site01-roi1")
    assert reference is not None
    target = target_from_reference(reference)
    candidates = build_earth_candidates(
        snapshot=snapshot,
        feature_ids=[
            "slope_mean_deg",
            "slope_p90_deg",
            "terrain_relief_p95_p05_m",
            "roughness_std_m",
            "flat_terrain_fraction",
        ],
    )
    # Test-only fixture: drop one real candidate's roughness value. This does not alter the
    # stored manifest or the snapshot; it proves the missing path does not zero-fill.
    first = candidates[0]
    features = dict(first.features)
    features.pop("roughness_std_m")
    candidates[0] = MultiDimCandidate(
        location_id=first.location_id,
        body=first.body,
        features=features,
    )

    payload = execute_cross_body_search(
        snapshot=snapshot,
        target=target,
        candidates=candidates,
        limit=5,
    )
    excluded = {item["location_id"]: item for item in payload["excluded"]}
    assert first.location_id in excluded
    assert "missing_feature:roughness_std_m" in excluded[first.location_id]["reasons"]
    assert payload["eligible_candidate_count"] == 112
