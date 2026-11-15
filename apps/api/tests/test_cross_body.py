from __future__ import annotations

import math

import pytest
from analog_api.domain.cross_body import (
    COARSE_CELL_M,
    COMMON_FEATURE_IDS,
    D01_CONTRACTS,
    D06_METHOD_VERSION,
    D06_UNITS,
    DIMENSION_D01,
    DIMENSION_D06,
    EARTH_BODY,
    FINE_ANALYSIS_RESOLUTION_M,
    FINE_SUPPORT_CLASS,
    FINE_WINDOW_M,
    FRAME_EARTH_WGS84,
    FRAME_GEOGRAPHIC,
    FRAME_PROJECTED_METRIC,
    FRAME_UNKNOWN,
    GATE_VERSION,
    REQUIRED_METHOD_BY_FEATURE,
    SUPPORT_AREA_ENVELOPE_MEAN,
    SUPPORT_COARSE_CELL,
    SUPPORT_FIXED_WINDOW,
    VALUE_ORIGIN_DEM_DERIVATIVE,
    VALUE_ORIGIN_MODEL_PREDICTION,
    CrossBodySample,
    classify_frame_kind,
    contract_for,
    evaluate_pair,
    feature_gate_payload,
    resolution_class,
)
from analog_api.domain.features import FEATURE_BY_ID, FeatureDefinition

D01_SOURCE_BY_BODY = {
    "Earth": ("copernicus-dem-glo90", "Copernicus DEM GLO-90 free basis"),
    "Moon": ("nasa-lola-south-pole", "NASA-led mission data; PGDA citation requested"),
    "Mars": ("usgs-jezero-ctx-dem", "Public domain (US Government work)"),
}
BODY_FRAME = {
    "Earth": "+proj=aeqd +R=6371000 +lat_0=37 +lon_0=-6",
    "Moon": "+proj=aeqd +R=1737400 +lat_0=-10 +lon_0=20",
    "Mars": "+proj=aeqd +R=3396190 +lat_0=18 +lon_0=78",
}


def _d01_sample(definition: FeatureDefinition, body: str, **overrides: object) -> CrossBodySample:
    source_id, license_text = D01_SOURCE_BY_BODY.get(body, ("unregistered-source", "unknown"))
    base: dict[str, object] = {
        "body": body,
        "feature_id": definition.id,
        "dimension_id": DIMENSION_D01,
        "value": (definition.min_value + definition.max_value) / 2,
        "unit": definition.unit,
        "method_version": definition.method_version,
        "analysis_resolution_m": FINE_ANALYSIS_RESOLUTION_M,
        "support_kind": SUPPORT_FIXED_WINDOW,
        "support_size_m": FINE_WINDOW_M,
        "value_origin": VALUE_ORIGIN_DEM_DERIVATIVE,
        "state": "detected",
        "frame_kind": FRAME_PROJECTED_METRIC,
        "frame": BODY_FRAME.get(body, "+proj=aeqd +R=0"),
        "source_id": source_id,
        "dataset_ids": (source_id,),
        "observation_id": f"obs-{body.lower()}-{definition.id}",
        "quality_flags": ("valid_fraction_0.95",),
        "license": license_text,
        "temporal_support": definition.temporal_support,
        "selector": {"kind": "local_window", "analysis_resolution_m": 90.0},
    }
    base.update(overrides)
    return CrossBodySample(**base)


def _d06_sample(
    body: str = "Earth",
    feature_id: str = "soil_clay_0_5cm_mean",
    **overrides: object,
) -> CrossBodySample:
    base: dict[str, object] = {
        "body": body,
        "feature_id": feature_id,
        "dimension_id": DIMENSION_D06,
        "value": 200.0,
        "unit": "g kg-1",
        "method_version": D06_METHOD_VERSION,
        "analysis_resolution_m": 250.0,
        "support_kind": SUPPORT_AREA_ENVELOPE_MEAN,
        "support_size_m": None,
        "value_origin": VALUE_ORIGIN_MODEL_PREDICTION,
        "state": "detected",
        "frame_kind": FRAME_EARTH_WGS84,
        "frame": "EPSG:4326",
        "source_id": "soilgrids-250m-v2",
        "dataset_ids": ("soilgrids-250m-v2",),
        "observation_id": f"soilgrids-{body.lower()}-{feature_id}",
        "quality_flags": ("model_prediction_not_measurement",),
        "license": "CC BY 4.0; cite ISRIC SoilGrids 2.0",
        "temporal_support": "SoilGrids 2.0 static model release",
        "selector": {"kind": "depth_interval", "depth_interval_m": [0.0, 0.05]},
    }
    base.update(overrides)
    return CrossBodySample(**base)


def _codes(decision) -> set[str]:
    return {reason.code for reason in decision.reasons}


@pytest.mark.parametrize("feature_id", sorted(D01_CONTRACTS))
@pytest.mark.parametrize(
    ("left_body", "right_body"),
    [("Earth", "Moon"), ("Earth", "Mars"), ("Moon", "Mars")],
)
def test_registered_fine_d01_pairs_are_comparable(
    feature_id: str, left_body: str, right_body: str
) -> None:
    definition = FEATURE_BY_ID[feature_id]
    decision = evaluate_pair(
        _d01_sample(definition, left_body), _d01_sample(definition, right_body)
    )

    assert decision.comparable is True
    assert decision.reasons == ()
    assert decision.cross_body is True
    payload = decision.to_payload()
    assert payload["feature_id"] == feature_id
    assert payload["dimension_id"] == DIMENSION_D01
    assert payload["reason_codes"] == []
    assert payload["comparison_scope"] == "single_feature_pair_no_aggregate"
    assert payload["gate_version"] == GATE_VERSION


def test_decision_payload_contains_no_score_or_total() -> None:
    definition = FEATURE_BY_ID["slope_mean_deg"]
    payload = evaluate_pair(
        _d01_sample(definition, "Earth"), _d01_sample(definition, "Moon")
    ).to_payload()

    assert "score" not in payload
    assert "distance" not in payload
    assert "total" not in payload
    assert "aggregate" not in payload
    assert "six-dimension cross-body total" in payload["scope_note"]


def test_planetary_coarse_250m_5km_sample_is_rejected_against_fine_earth() -> None:
    definition = FEATURE_BY_ID["slope_mean_deg"]
    earth = _d01_sample(definition, "Earth")
    moon_coarse = _d01_sample(
        definition,
        "Moon",
        method_version="coarse-planetary-dem-horn-1.0.0",
        analysis_resolution_m=250.0,
        support_kind=SUPPORT_COARSE_CELL,
        support_size_m=COARSE_CELL_M,
    )

    decision = evaluate_pair(earth, moon_coarse)

    assert decision.comparable is False
    assert {
        "method_version_not_registered",
        "method_version_mismatch",
        "analysis_resolution_mismatch",
        "coarse_resolution_not_comparable",
        "support_kind_mismatch",
        "support_size_mismatch",
        "coarse_support_not_comparable",
    } <= _codes(decision)


def test_earth_coarse_slope_product_is_rejected_against_fine_moon() -> None:
    definition = FEATURE_BY_ID["slope_mean_deg"]
    earth_coarse = _d01_sample(
        definition,
        "Earth",
        method_version="coarse-terrain-250m-1.0.0",
        analysis_resolution_m=250.0,
        support_kind=SUPPORT_COARSE_CELL,
        support_size_m=COARSE_CELL_M,
        value_origin="published_slope_product",
    )
    moon = _d01_sample(definition, "Moon")

    decision = evaluate_pair(moon, earth_coarse)

    assert decision.comparable is False
    assert {
        "coarse_resolution_not_comparable",
        "coarse_support_not_comparable",
        "value_origin_mismatch",
    } <= _codes(decision)


def test_d06_is_never_cross_body_comparable() -> None:
    earth = _d06_sample("Earth")
    moon = _d06_sample("Moon")

    decision = evaluate_pair(earth, moon)

    assert decision.cross_body is True
    assert decision.comparable is False
    assert "body_out_of_scope" in _codes(decision)
    assert "earth_frame_on_planetary_body" in _codes(decision)
    assert decision.to_payload()["candidate"]["body"] == "Moon"


def test_same_body_earth_d06_mean_pair_is_comparable_but_not_cross_body() -> None:
    decision = evaluate_pair(_d06_sample("Earth"), _d06_sample("Earth"))

    assert decision.comparable is True
    assert decision.cross_body is False


def test_d06_prediction_quantile_is_uncertainty_only() -> None:
    decision = evaluate_pair(
        _d06_sample("Earth", "soil_clay_0_5cm_q05"),
        _d06_sample("Earth", "soil_clay_0_5cm_q05"),
    )

    assert decision.comparable is False
    assert "role_not_comparable" in _codes(decision)


def test_d06_250m_is_not_mixed_with_a_90m_d01_window() -> None:
    d01 = _d01_sample(FEATURE_BY_ID["slope_mean_deg"], "Earth")
    d06 = _d06_sample("Earth")

    decision = evaluate_pair(d01, d06)

    assert decision.comparable is False
    assert {
        "dimension_mismatch",
        "feature_mismatch",
        "unit_mismatch",
        "method_version_mismatch",
        "analysis_resolution_mismatch",
        "support_kind_mismatch",
        "frame_kind_mismatch",
    } <= _codes(decision)


def test_missing_value_is_reported_and_never_zero_filled() -> None:
    definition = FEATURE_BY_ID["slope_mean_deg"]
    earth = _d01_sample(definition, "Earth")
    moon = _d01_sample(definition, "Moon", value=None, state="no_coverage")

    decision = evaluate_pair(earth, moon)
    payload = decision.to_payload()

    assert decision.comparable is False
    assert {"state_not_detected", "missing_value"} <= _codes(decision)
    assert payload["candidate"]["value"] is None
    assert "score" not in payload
    assert "distance" not in payload


def test_state_detected_without_a_value_is_still_missing() -> None:
    definition = FEATURE_BY_ID["terrain_relief_p95_p05_m"]
    decision = evaluate_pair(
        _d01_sample(definition, "Earth"),
        _d01_sample(definition, "Mars", value=None),
    )

    assert "missing_value" in _codes(decision)
    assert "state_not_detected" not in _codes(decision)


def test_a_real_zero_is_preserved_as_a_value() -> None:
    definition = FEATURE_BY_ID["slope_mean_deg"]
    decision = evaluate_pair(
        _d01_sample(definition, "Earth", value=0.0),
        _d01_sample(definition, "Moon", value=0.0),
    )

    assert decision.comparable is True
    assert decision.to_payload()["candidate"]["value"] == 0.0


@pytest.mark.parametrize("bad_value", [math.nan, math.inf, -math.inf])
def test_non_finite_values_are_rejected(bad_value: float) -> None:
    definition = FEATURE_BY_ID["slope_mean_deg"]
    decision = evaluate_pair(
        _d01_sample(definition, "Earth"),
        _d01_sample(definition, "Moon", value=bad_value),
    )

    assert "value_not_finite" in _codes(decision)
    assert "missing_value" not in _codes(decision)


def test_method_version_mismatch_is_refused() -> None:
    definition = FEATURE_BY_ID["roughness_std_m"]
    decision = evaluate_pair(
        _d01_sample(definition, "Earth"),
        _d01_sample(definition, "Mars", method_version="terrain-metrics-0.9.0"),
    )

    assert {
        "method_version_not_registered",
        "method_version_mismatch",
    } <= _codes(decision)


def test_unit_mismatch_is_refused_without_conversion() -> None:
    definition = FEATURE_BY_ID["slope_mean_deg"]
    decision = evaluate_pair(
        _d01_sample(definition, "Earth"),
        _d01_sample(definition, "Moon", unit="radian"),
    )

    assert {"unit_not_registered", "unit_mismatch"} <= _codes(decision)


def test_support_kind_mismatch_is_refused() -> None:
    definition = FEATURE_BY_ID["flat_terrain_fraction"]
    decision = evaluate_pair(
        _d01_sample(definition, "Earth"),
        _d01_sample(
            definition,
            "Moon",
            support_kind=SUPPORT_AREA_ENVELOPE_MEAN,
            support_size_m=None,
        ),
    )

    assert {
        "support_kind_mismatch",
        "support_size_missing",
    } <= _codes(decision)


def test_earth_wgs84_on_a_planetary_body_is_refused() -> None:
    definition = FEATURE_BY_ID["slope_mean_deg"]
    decision = evaluate_pair(
        _d01_sample(definition, "Earth"),
        _d01_sample(definition, "Moon", frame_kind=FRAME_EARTH_WGS84, frame="EPSG:4326"),
    )

    assert {
        "earth_frame_on_planetary_body",
        "frame_not_projected_metric",
        "frame_kind_mismatch",
    } <= _codes(decision)


def test_unknown_frame_and_unknown_body_are_refused() -> None:
    definition = FEATURE_BY_ID["slope_mean_deg"]
    decision = evaluate_pair(
        _d01_sample(definition, "Earth", frame_kind=FRAME_UNKNOWN),
        _d01_sample(definition, "Europa"),
    )

    assert {"frame_unknown", "unknown_body", "body_out_of_scope"} <= _codes(decision)


@pytest.mark.parametrize(
    ("feature_id", "dimension_id"),
    [
        ("landcover_tree_cover_fraction", DIMENSION_D01),
        ("geologic_unit", DIMENSION_D01),
        ("slope_mean_deg", DIMENSION_D06),
        ("mineral_hematite", "D02"),
    ],
)
def test_unregistered_or_mislabelled_features_are_refused(
    feature_id: str, dimension_id: str
) -> None:
    decision = evaluate_pair(
        CrossBodySample(
            body="Earth",
            feature_id=feature_id,
            dimension_id=dimension_id,
            value=1.0,
            unit="fraction",
            method_version="unknown-1.0.0",
            analysis_resolution_m=90.0,
            support_kind=SUPPORT_FIXED_WINDOW,
            support_size_m=FINE_WINDOW_M,
            value_origin=VALUE_ORIGIN_DEM_DERIVATIVE,
            frame_kind=FRAME_PROJECTED_METRIC,
        ),
        CrossBodySample(
            body="Moon",
            feature_id=feature_id,
            dimension_id=dimension_id,
            value=1.0,
            unit="fraction",
            method_version="unknown-1.0.0",
            analysis_resolution_m=90.0,
            support_kind=SUPPORT_FIXED_WINDOW,
            support_size_m=FINE_WINDOW_M,
            value_origin=VALUE_ORIGIN_DEM_DERIVATIVE,
            frame_kind=FRAME_PROJECTED_METRIC,
        ),
    )

    assert decision.comparable is False
    assert "unregistered_feature" in _codes(decision)
    assert contract_for(feature_id, dimension_id) is None


def test_provenance_is_preserved_on_both_sides() -> None:
    definition = FEATURE_BY_ID["terrain_relief_p95_p05_m"]
    payload = evaluate_pair(
        _d01_sample(definition, "Earth"), _d01_sample(definition, "Moon")
    ).to_payload()

    for side in ("reference", "candidate"):
        provenance = payload[side]
        assert provenance["feature_id"] == definition.id
        assert provenance["method_version"] == definition.method_version
        assert provenance["analysis_resolution_m"] == FINE_ANALYSIS_RESOLUTION_M
        assert provenance["support_kind"] == SUPPORT_FIXED_WINDOW
        assert provenance["support_size_m"] == FINE_WINDOW_M
        assert provenance["value_origin"] == VALUE_ORIGIN_DEM_DERIVATIVE
        assert provenance["source_id"]
        assert provenance["dataset_ids"]
        assert provenance["observation_id"]
        assert provenance["license"]
        assert provenance["frame_kind"] == FRAME_PROJECTED_METRIC
        assert provenance["selector"]["kind"] == "local_window"

    assert payload["reference"]["body"] == "Earth"
    assert payload["candidate"]["body"] == "Moon"


def test_reasons_carry_machine_codes_and_messages() -> None:
    definition = FEATURE_BY_ID["slope_mean_deg"]
    payload = evaluate_pair(
        _d01_sample(definition, "Earth"),
        _d01_sample(definition, "Moon", value=None, state="unknown"),
    ).to_payload()

    assert payload["reason_codes"] == ["state_not_detected", "missing_value"]
    for reason in payload["reasons"]:
        assert reason["code"]
        assert reason["message"]


def test_evaluation_is_deterministic() -> None:
    definition = FEATURE_BY_ID["slope_mean_deg"]
    earth = _d01_sample(definition, "Earth")
    moon = _d01_sample(definition, "Moon", analysis_resolution_m=250.0)
    first = evaluate_pair(earth, moon).to_payload()
    second = evaluate_pair(earth, moon).to_payload()
    assert first == second


def test_feature_gate_payload_lists_d01_cross_body_and_d06_earth_only() -> None:
    payload = feature_gate_payload()
    dimensions = {item["dimension_id"]: item for item in payload["dimensions"]}

    d01 = dimensions[DIMENSION_D01]
    assert d01["cross_body_allowed"] is True
    assert d01["body_scope"] == ["Earth", "Moon", "Mars"]
    assert d01["analysis_resolution_m"] == FINE_ANALYSIS_RESOLUTION_M
    assert d01["support_size_m"] == FINE_WINDOW_M
    assert d01["support_kind"] == SUPPORT_FIXED_WINDOW
    assert d01["coarse_cell_support_m"] == COARSE_CELL_M
    assert {feature["feature_id"] for feature in d01["features"]} == set(D01_CONTRACTS)
    assert all(feature["cross_body_allowed"] for feature in d01["features"])
    assert all(
        set(feature["provider_coverage_by_body"]) == {"Earth", "Moon", "Mars"}
        for feature in d01["features"]
    )

    d06 = dimensions[DIMENSION_D06]
    assert d06["cross_body_allowed"] is False
    assert d06["body_scope"] == ["Earth"]
    assert d06["analysis_resolution_m"] == 250.0
    assert d06["support_kind"] == SUPPORT_AREA_ENVELOPE_MEAN
    assert d06["value_origin"] == VALUE_ORIGIN_MODEL_PREDICTION
    assert set(d06["registered_units"]) == set(D06_UNITS)

    assert payload["gate_version"] == GATE_VERSION
    assert "not a six-dimension" in payload["scope_note"]
    assert "score" not in payload
    assert "total" not in payload


def test_registered_scope_constants_match_the_feature_registry() -> None:
    assert tuple(FEATURE_BY_ID) == COMMON_FEATURE_IDS
    assert COMMON_FEATURE_IDS == (
        "slope_mean_deg",
        "slope_p90_deg",
        "terrain_relief_p95_p05_m",
        "roughness_std_m",
        "flat_terrain_fraction",
    )
    assert EARTH_BODY == "Earth"
    assert FINE_SUPPORT_CLASS == "fine_90m"
    for feature_id in COMMON_FEATURE_IDS:
        assert REQUIRED_METHOD_BY_FEATURE[feature_id] == FEATURE_BY_ID[feature_id].method_version
        assert feature_id not in {"soil_clay_0_5cm_mean", "landcover_tree_cover_fraction"}


@pytest.mark.parametrize(
    ("reference", "expected"),
    [
        ('PROJCRS["local"]', FRAME_PROJECTED_METRIC),
        ("+proj=aeqd +R=1737400", FRAME_PROJECTED_METRIC),
        ("+proj=longlat +R=1737400", FRAME_GEOGRAPHIC),
        ('GEOGCRS["Mars 2000"]', FRAME_GEOGRAPHIC),
        ("EPSG:4326", FRAME_EARTH_WGS84),
        ("", FRAME_UNKNOWN),
    ],
)
def test_frame_classification_never_assumes_earth_wgs84(reference: str, expected: str) -> None:
    assert classify_frame_kind(reference) == expected


def test_resolution_class_separates_fine_from_planetary_coarse() -> None:
    assert resolution_class(90.0) == "fine_90m"
    assert resolution_class(250.0) == "coarse_250m_5km"
    assert resolution_class(5000.0) == "coarse_250m_5km"
    assert resolution_class(None) == "unknown"
