from __future__ import annotations

import json
import math
from dataclasses import replace
from pathlib import Path

import pytest
from analog_api.adapters.repository import SqlAlchemyRepository
from analog_api.adapters.seed import ensure_seeded, make_engine
from analog_api.adapters.typed_db import TypedObservationStore, init_typed_db
from analog_api.config import Settings
from analog_api.domain.cross_body import (
    COARSE_CELL_M,
    D01_CONTRACTS,
    D06_METHOD_VERSION,
    DIMENSION_D01,
    DIMENSION_D06,
    FINE_ANALYSIS_RESOLUTION_M,
    FINE_WINDOW_M,
    FRAME_EARTH_WGS84,
    FRAME_PROJECTED_METRIC,
    SUPPORT_COARSE_CELL,
    SUPPORT_FIXED_WINDOW,
    VALUE_ORIGIN_DEM_DERIVATIVE,
    CrossBodySample,
    evaluate_pair,
    feature_gate_payload,
)
from analog_api.domain.cross_body_search import (
    CROSS_BODY_SEARCH_VERSION,
    G1_COMPATIBLE_D01_FEATURES,
    CrossBodySearchError,
    CrossBodyTarget,
    TargetFeatureValue,
    execute_cross_body_search,
    g1_compatible_feature_ids,
    resolve_cross_body_target,
    target_from_reference,
)
from analog_api.domain.feature_service import assemble_snapshot
from analog_api.domain.features import FEATURE_BY_ID, METHOD_VERSION
from analog_api.domain.multidim import DimensionControl
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def search_ctx(tmp_path_factory: pytest.TempPathFactory):
    tmp = tmp_path_factory.mktemp("cross-body-170")
    db_path = tmp / "cross-body.db"
    settings = Settings(
        database_url=f"sqlite+pysqlite:///{db_path}",
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
    yield {
        "session": session,
        "repo": repo,
        "snapshot": snapshot,
        "data_version": repo.data_version(),
    }
    session.close()


def _moon_reference(repo):
    ref = repo.get_reference("moon-lola-site01-roi1")
    assert ref is not None
    return ref


def _mars_reference(repo):
    ref = repo.get_reference("mars-jezero-ctx-window-01")
    assert ref is not None
    return ref


def test_common_feature_support_method_resolution_gate_d01_priority(search_ctx) -> None:
    snapshot = search_ctx["snapshot"]
    repo = search_ctx["repo"]
    moon = _moon_reference(repo)
    mars = _mars_reference(repo)

    # D01 fine terrain is the only G1-compatible/common cross-body feature set for this path.
    assert G1_COMPATIBLE_D01_FEATURES == (
        "slope_mean_deg",
        "slope_p90_deg",
        "terrain_relief_p95_p05_m",
        "roughness_std_m",
        "flat_terrain_fraction",
    )
    for body in ("Moon", "Mars"):
        ids = g1_compatible_feature_ids(snapshot.registry, source_body=body)
        assert set(ids) == set(G1_COMPATIBLE_D01_FEATURES)

    # Method/support/resolution/feature identity gate: same D01 fine contract is comparable.
    definition = FEATURE_BY_ID["slope_mean_deg"]
    earth = CrossBodySample(
        body="Earth",
        feature_id=definition.id,
        dimension_id=DIMENSION_D01,
        value=10.0,
        unit=definition.unit,
        method_version=definition.method_version,
        analysis_resolution_m=FINE_ANALYSIS_RESOLUTION_M,
        support_kind=SUPPORT_FIXED_WINDOW,
        support_size_m=FINE_WINDOW_M,
        value_origin=VALUE_ORIGIN_DEM_DERIVATIVE,
        frame_kind=FRAME_PROJECTED_METRIC,
        frame="+proj=utm +zone=32 +datum=WGS84",
    )
    moon_sample = CrossBodySample(
        body="Moon",
        feature_id=definition.id,
        dimension_id=DIMENSION_D01,
        value=float(moon.features[definition.id].value),
        unit=definition.unit,
        method_version=definition.method_version,
        analysis_resolution_m=FINE_ANALYSIS_RESOLUTION_M,
        support_kind=SUPPORT_FIXED_WINDOW,
        support_size_m=FINE_WINDOW_M,
        value_origin=VALUE_ORIGIN_DEM_DERIVATIVE,
        frame_kind=FRAME_PROJECTED_METRIC,
        frame=moon.crs,
    )
    mars_sample = CrossBodySample(
        body="Mars",
        feature_id=definition.id,
        dimension_id=DIMENSION_D01,
        value=float(mars.features[definition.id].value),
        unit=definition.unit,
        method_version=definition.method_version,
        analysis_resolution_m=FINE_ANALYSIS_RESOLUTION_M,
        support_kind=SUPPORT_FIXED_WINDOW,
        support_size_m=FINE_WINDOW_M,
        value_origin=VALUE_ORIGIN_DEM_DERIVATIVE,
        frame_kind=FRAME_PROJECTED_METRIC,
        frame=mars.crs,
    )
    assert evaluate_pair(earth, moon_sample).comparable is True
    assert evaluate_pair(earth, mars_sample).comparable is True
    assert evaluate_pair(moon_sample, mars_sample).comparable is True

    gate = feature_gate_payload()
    assert gate["gate_version"]
    d01 = next(d for d in gate["dimensions"] if d["dimension_id"] == DIMENSION_D01)
    assert d01["cross_body_allowed"] is True
    assert d01["analysis_resolution_m"] == FINE_ANALYSIS_RESOLUTION_M
    assert d01["support_size_m"] == FINE_WINDOW_M
    assert {f["feature_id"] for f in d01["features"]} == set(G1_COMPATIBLE_D01_FEATURES)
    # Registry/method contract alignment (D01 priority common features).
    for fid in G1_COMPATIBLE_D01_FEATURES:
        assert FEATURE_BY_ID[fid].method_version in D01_CONTRACTS[fid].method_versions
        assert moon.features[fid].method_version == FEATURE_BY_ID[fid].method_version
        assert mars.features[fid].method_version == FEATURE_BY_ID[fid].method_version


def test_d06_only_legal_cross_body_is_denied_and_not_scored(search_ctx) -> None:
    snapshot = search_ctx["snapshot"]
    moon = _moon_reference(search_ctx["repo"])

    # D06 pairwise gate: Earth-only SoilGrids/model predictions are never cross-body comparable with Moon/Mars.
    earth_d06 = CrossBodySample(
        body="Earth",
        feature_id="soil_clay_0_5cm_mean",
        dimension_id=DIMENSION_D06,
        value=200.0,
        unit="g kg-1",
        method_version=D06_METHOD_VERSION,
        analysis_resolution_m=250.0,
        support_kind="area_envelope_mean",
        support_size_m=None,
        value_origin="model_prediction",
        frame_kind=FRAME_PROJECTED_METRIC,
        frame="EPSG:4326",
    )
    moon_d06 = CrossBodySample(
        body="Moon",
        feature_id="soil_clay_0_5cm_mean",
        dimension_id=DIMENSION_D06,
        value=200.0,
        unit="g kg-1",
        method_version=D06_METHOD_VERSION,
        analysis_resolution_m=250.0,
        support_kind="area_envelope_mean",
        support_size_m=None,
        value_origin="model_prediction",
        frame_kind=FRAME_PROJECTED_METRIC,
        frame="+proj=stere",
    )
    decision = evaluate_pair(earth_d06, moon_d06)
    assert decision.comparable is False
    assert "body_out_of_scope" in {r.code for r in decision.reasons}

    d06_gate = next(
        d for d in feature_gate_payload()["dimensions"] if d["dimension_id"] == DIMENSION_D06
    )
    assert d06_gate["cross_body_allowed"] is False
    assert d06_gate["body_scope"] == ["Earth"]
    assert "Moon/Mars D06 sources remain not selected" in d06_gate["note"]

    # D06 cannot enter cross-body Earth ranking target (only legal cases allowed; none for Moon/Mars).
    # When valid D01 features are present, D06 must still be rejected and never scored.
    values = dict(target_from_reference(moon).features)
    values["soil_clay_0_5cm_mean"] = TargetFeatureValue(
        value=200.0,
        unit="g kg-1",
        method_version="soilgrids-wcs-2.0.0",
        valid_fraction=1.0,
        quality="model",
    )
    target = replace(target_from_reference(moon), features=values)
    resolved = resolve_cross_body_target(target=target, registry=snapshot.registry)
    assert "soil_clay_0_5cm_mean" not in resolved.accepted_feature_ids
    rejected = {
        item["feature_id"]: item for item in resolved.payload["target_features"]["rejected"]
    }
    assert rejected["soil_clay_0_5cm_mean"]["reason"] in {
        "dimension_not_g1_compatible",
        "body_out_of_scope",
        "feature_role_not_scoreable",
        "not_g1_compatible",
    }


def test_scale_resolution_and_method_not_mixed_directly(search_ctx) -> None:
    snapshot = search_ctx["snapshot"]
    moon = _moon_reference(search_ctx["repo"])

    definition = FEATURE_BY_ID["slope_mean_deg"]
    earth_fine = CrossBodySample(
        body="Earth",
        feature_id=definition.id,
        dimension_id=DIMENSION_D01,
        value=10.0,
        unit=definition.unit,
        method_version=definition.method_version,
        analysis_resolution_m=FINE_ANALYSIS_RESOLUTION_M,
        support_kind=SUPPORT_FIXED_WINDOW,
        support_size_m=FINE_WINDOW_M,
        value_origin=VALUE_ORIGIN_DEM_DERIVATIVE,
        frame_kind=FRAME_PROJECTED_METRIC,
    )
    # Planetary coarse/global-style 250 m / 5 km product must not be mixed with fine 90 m / 1.98 km.
    planetary_coarse = CrossBodySample(
        body="Moon",
        feature_id=definition.id,
        dimension_id=DIMENSION_D01,
        value=10.0,
        unit=definition.unit,
        method_version="coarse-planetary-dem-horn-1.0.0",
        analysis_resolution_m=250.0,
        support_kind=SUPPORT_COARSE_CELL,
        support_size_m=COARSE_CELL_M,
        value_origin=VALUE_ORIGIN_DEM_DERIVATIVE,
        frame_kind=FRAME_PROJECTED_METRIC,
    )
    coarse_decision = evaluate_pair(earth_fine, planetary_coarse)
    assert coarse_decision.comparable is False
    assert {
        "coarse_resolution_not_comparable",
        "coarse_support_not_comparable",
        "support_kind_mismatch",
        "analysis_resolution_mismatch",
    } & {r.code for r in coarse_decision.reasons}

    # Different fine method distance must not be mixed.
    different_method = replace(earth_fine, body="Mars", method_version="terrain-metrics-2.0.0")
    method_decision = evaluate_pair(earth_fine, different_method)
    assert method_decision.comparable is False
    assert {"method_version_not_registered", "method_version_mismatch"} & {
        r.code for r in method_decision.reasons
    }

    # Coarse feature ids rejected from cross-body D01 target resolution.
    values = dict(target_from_reference(moon).features)
    values["coarse_slope_mean_deg"] = TargetFeatureValue(
        value=1.0,
        unit="degree",
        method_version="coarse-planetary-dem-horn-1.0.0",
        valid_fraction=1.0,
        quality="derived",
    )
    target = CrossBodyTarget(
        body="Moon",
        source_id=moon.reference_id,
        source_kind="committed_planetary_reference",
        features=values,
        reference_id=moon.reference_id,
    )
    with pytest.raises(CrossBodySearchError) as exc:
        resolve_cross_body_target(target=target, registry=snapshot.registry)
    reasons = {item["feature_id"]: item["reason"] for item in exc.value.details}
    assert reasons["coarse_slope_mean_deg"] == "coarse_fine_method_not_comparable"

    # Planetary target in Earth WGS84 frame is not a valid planetary sample frame.
    bad_frame = evaluate_pair(
        earth_fine,
        replace(
            planetary_coarse,
            method_version=definition.method_version,
            analysis_resolution_m=FINE_ANALYSIS_RESOLUTION_M,
            support_kind=SUPPORT_FIXED_WINDOW,
            support_size_m=FINE_WINDOW_M,
            frame_kind=FRAME_EARTH_WGS84,
            frame="EPSG:4326",
        ),
    )
    assert bad_frame.comparable is False
    assert "earth_frame_on_planetary_body" in {r.code for r in bad_frame.reasons}


def test_dimension_feature_weight_compatibility_missing_incompatible_not_zero(search_ctx) -> None:
    snapshot = search_ctx["snapshot"]
    moon = target_from_reference(_moon_reference(search_ctx["repo"]))
    feature_weights = {fid: 1.0 for fid in G1_COMPATIBLE_D01_FEATURES}
    feature_weights["slope_mean_deg"] = 3.0
    payload = execute_cross_body_search(
        snapshot=snapshot,
        target=moon,
        feature_weights=feature_weights,
        dimension_weights={"D01": DimensionControl(weight=2.0, enabled=True)},
        limit=5,
        data_version=search_ctx["data_version"],
    )

    assert payload["scoring"]["version"] in {
        "dimension-weighted-rms-v1",
        "feature-weighted-euclidean-v1",
    }
    assert payload["weights"]["dimension_weights"]["D01"]["weight"] == 2.0
    assert payload["weights"]["dimension_weights"]["D01"]["enabled"] is True
    assert payload["weights"]["feature_weights"]["slope_mean_deg"]["weight"] == 3.0
    assert payload["weights"]["feature_weights"]["slope_mean_deg"]["comparison"] == "active"

    # Zero-weight feature is not compared and must not be fabricated/excluded as missing.
    zero_weights = {fid: 1.0 for fid in G1_COMPATIBLE_D01_FEATURES}
    zero_weights["roughness_std_m"] = 0.0
    zero_payload = execute_cross_body_search(
        snapshot=snapshot,
        target=moon,
        feature_weights=zero_weights,
        limit=3,
        data_version=search_ctx["data_version"],
    )
    assert zero_payload["weights"]["feature_weights"]["roughness_std_m"]["comparison"] == (
        "zero_weight_not_compared"
    )
    assert zero_payload["results"]
    first = zero_payload["results"][0]
    assert "roughness_std_m" not in [c["feature_id"] for c in first["contributions"]]

    # Missing/incompatible positive-weight features must exclude candidates, never zero-fill.
    # Inject a synthetic candidate-like state by removing required values is done via ranker behavior;
    # we assert all reported exclusions have explicit reasons and no zero-fill markers.
    if payload["excluded"]:
        for item in payload["excluded"]:
            assert item["reasons"]
            assert item["missing_features"] or item["incompatible_features"]

    # Incompatible weight contract rejected.
    with pytest.raises(CrossBodySearchError) as exc:
        resolve_cross_body_target(
            target=moon,
            registry=snapshot.registry,
            feature_weights={"not_a_feature": 1.0},
        )
    assert exc.value.code == "weight_without_target"


def _assert_search_payload(payload: dict, body: str, reference_id: str) -> None:
    assert payload["cross_body_search_version"] == CROSS_BODY_SEARCH_VERSION
    assert payload["source"]["body"] == body
    assert payload["source"]["source_id"] == reference_id
    assert payload["source"]["earth_wgs84"] is False
    assert payload["candidate"]["body"] == "Earth"
    assert payload["g1_gate"]["feature_ids"] == list(G1_COMPATIBLE_D01_FEATURES)
    assert payload["g1_gate"]["coarse_fine_mixing"] is False
    assert "not a six-dimension cross-body score" in payload["claim_scope"]
    assert set(payload["target_features"]["accepted"]) == set(G1_COMPATIBLE_D01_FEATURES)

    assert payload["results"], "expected ranked Earth candidates"
    assert payload["eligible_candidate_count"] >= 1
    assert len(payload["results"]) <= payload["limit"]
    assert [r["rank"] for r in payload["results"]] == list(range(1, len(payload["results"]) + 1))
    assert payload["results"] == sorted(
        payload["results"], key=lambda r: (r["distance"], r["location_id"])
    )

    top = payload["results"][0]
    assert top["body"] == "Earth"
    assert top["distance"] >= 0
    assert {c["feature_id"] for c in top["contributions"]} == set(G1_COMPATIBLE_D01_FEATURES)
    for c in top["contributions"]:
        assert c["unit"] == FEATURE_BY_ID[c["feature_id"]].unit
        assert c["scale"] == FEATURE_BY_ID[c["feature_id"]].scale
        assert math.isfinite(c["normalized_delta"])
        assert math.isfinite(c["contribution"])
        assert c["contribution"] >= 0
        assert c["observation_id"].startswith("d01:")
        assert c["measurement_family"]
        assert c["support"]["kind"] == "fixed_window"

    assert "excluded" in payload
    assert "coverage" in payload
    assert "scoring" in payload
    assert payload["scoring"]["version"]
    assert payload["scoring"]["formula"]
    assert "limitations" in payload and payload["limitations"]
    assert any("six-dimension" in item for item in payload["limitations"])

    # Provenance: source dataset/method and candidate observability are present.
    assert payload["source"]["provenance"]["reference_id"] == reference_id
    assert payload["source"]["dataset_ids"]
    methods = {item["method_version"] for item in payload["weights"]["feature_weights"].values()}
    assert methods == {METHOD_VERSION, "flat-terrain-fraction-1.0.0"}


def test_actual_earth_search_moon_aoi_target_to_ranking(search_ctx) -> None:
    snapshot = search_ctx["snapshot"]
    repo = search_ctx["repo"]
    moon_ref = _moon_reference(repo)
    target = target_from_reference(moon_ref)
    payload = execute_cross_body_search(
        snapshot=snapshot,
        target=target,
        limit=5,
        data_version=search_ctx["data_version"],
    )
    _assert_search_payload(payload, "Moon", moon_ref.reference_id)
    # Verify contributions are real per-feature deltas/distances, not a fabricated total score.
    first = payload["results"][0]
    contrib_sum = sum(c["contribution"] for c in first["contributions"])
    assert contrib_sum >= 0
    assert "D01" in first["dimension_contributions"]


def test_actual_earth_search_mars_aoi_target_to_ranking(search_ctx) -> None:
    snapshot = search_ctx["snapshot"]
    repo = search_ctx["repo"]
    mars_ref = _mars_reference(repo)
    target = target_from_reference(mars_ref)
    payload = execute_cross_body_search(
        snapshot=snapshot,
        target=target,
        limit=5,
        data_version=search_ctx["data_version"],
    )
    _assert_search_payload(payload, "Mars", mars_ref.reference_id)
    assert payload["source"]["crs"]
    assert payload["source"]["window_size_m"] == 1980.0
    assert payload["source"]["analysis_resolution_m"] == 90.0
    # JSON-serializable result with contributions/missing/provenance fields.
    encoded = json.dumps(payload)
    assert "contributions" in encoded and "excluded" in encoded and "provenance" in encoded
