from __future__ import annotations

from typing import Any

from analog_api.domain.eligibility import (
    REGISTRY_VERSION,
    build_registry,
    materialize,
)

BBOX = {"west": 10.0, "south": 45.0, "east": 10.01, "north": 45.01}


def _row(
    *,
    feature_id: str = "soil_clay_0_5cm_mean",
    observation_id: str = "obs-1",
    area_id: str = "earth-a",
    body: str = "Earth",
    state: str = "detected",
    value: Any = 200.0,
    unit: str = "g kg-1",
    method_version: str = "soilgrids-wcs-2.0.0",
    depth: tuple[float, float] = (0.0, 0.05),
    bbox: dict[str, float] | None = None,
    bbox_crs: str = "EPSG:4326",
    dimension_code: str = "D06",
    value_kind: str = "numeric",
    score_role: str = "evidence_only",
) -> dict[str, Any]:
    return {
        "observation_id": observation_id,
        "area_id": area_id,
        "body": body,
        "dimension_code": dimension_code,
        "feature_id": feature_id,
        "value_kind": value_kind,
        "state": state,
        "value": value,
        "unit": unit,
        "method_version": method_version,
        "dataset_ids": ["soilgrids-250m-v2"],
        "source_asset_ids": ["asset-1"],
        "quality_flags": ["model_prediction_not_measurement"],
        "score_role": score_role,
        "spatial_support": {
            "kind": "bbox",
            "crs": "EPSG:4326",
            "bbox": bbox or BBOX,
            "bbox_crs": bbox_crs,
            "resolution_m": 250.0,
            "source_resolution_m": 250.0,
        },
        "temporal_support": {"kind": "static"},
        "depth_support": {"kind": "depth_interval", "min_m": depth[0], "max_m": depth[1]},
    }


def _materialize(rows):
    registry = build_registry(rows)
    return registry, materialize(
        registry=registry, rows=rows, candidate_bboxes={"earth-a": BBOX, "earth-b": BBOX}
    )


def test_missing_value_is_not_zero_filled():
    registry, outcome = _materialize([_row(value=0.0)])
    entry = registry["soil_clay_0_5cm_mean"]
    assert entry.roles["similarity"].supported is True
    assert outcome.values[("earth-a", "soil_clay_0_5cm_mean")].value == 0.0
    assert ("earth-b", "soil_clay_0_5cm_mean") not in outcome.values
    assert entry.missing_policy.startswith("A missing or gated-out value excludes")


def test_unknown_or_no_coverage_state_is_not_scored():
    _, outcome = _materialize(
        [
            _row(observation_id="obs-unknown", state="unknown", value=None),
            _row(observation_id="obs-nocov", state="no_coverage", value=None),
        ]
    )
    assert not outcome.values
    reasons = {reason for item in outcome.rejected for reason in item.reasons}
    assert "state:unknown" in reasons
    assert "state:no_coverage" in reasons


def test_unit_mismatch_is_rejected():
    _, outcome = _materialize([_row(unit="percent")])
    assert not outcome.values
    assert "unit_mismatch" in outcome.rejected[0].reasons


def test_depth_interval_mismatch_is_rejected():
    _, outcome = _materialize([_row(feature_id="soil_clay_0_5cm_mean", depth=(0.05, 0.15))])
    assert not outcome.values
    assert "depth_interval_mismatch" in outcome.rejected[0].reasons


def test_method_version_mismatch_is_rejected():
    _, outcome = _materialize([_row(method_version="soilgrids-1.0.0")])
    assert not outcome.values
    assert "method_version_mismatch" in outcome.rejected[0].reasons


def test_body_out_of_scope_is_rejected():
    _, outcome = _materialize([_row(body="Moon")])
    assert not outcome.values
    assert "body_out_of_scope" in outcome.rejected[0].reasons


def test_spatial_bbox_mismatch_is_rejected():
    other = {"west": 20.0, "south": 45.0, "east": 20.01, "north": 45.01}
    _, outcome = _materialize([_row(bbox=other)])
    assert not outcome.values
    assert "spatial_bbox_mismatch" in outcome.rejected[0].reasons


def test_non_numeric_value_is_rejected():
    _, outcome = _materialize([_row(value={"mean": 200.0})])
    assert not outcome.values
    assert "value_not_finite_numeric" in outcome.rejected[0].reasons


def test_quantile_is_uncertainty_evidence_not_an_independent_similarity_feature():
    rows = [
        _row(observation_id="obs-mean", feature_id="soil_clay_0_5cm_mean", value=200.0),
        _row(
            observation_id="obs-q05",
            feature_id="soil_clay_0_5cm_q05",
            value=150.0,
            score_role="evidence_only",
        ),
        _row(
            observation_id="obs-q95",
            feature_id="soil_clay_0_5cm_q95",
            value=250.0,
            score_role="evidence_only",
        ),
    ]
    registry, outcome = _materialize(rows)
    mean = registry["soil_clay_0_5cm_mean"]
    q05 = registry["soil_clay_0_5cm_q05"]
    assert mean.roles["similarity"].supported is True
    assert q05.roles["similarity"].supported is False
    assert q05.roles["uncertainty"].supported is True
    assert q05.measurement_family == mean.measurement_family
    assert ("earth-a", "soil_clay_0_5cm_q05") not in outcome.values
    materialized = outcome.values[("earth-a", "soil_clay_0_5cm_mean")]
    assert materialized.uncertainty is not None
    assert materialized.uncertainty["q05"] == 150.0
    assert materialized.uncertainty["q95"] == 250.0


def test_ambiguous_duplicate_values_are_rejected():
    _, outcome = _materialize(
        [
            _row(observation_id="obs-a", value=200.0),
            _row(observation_id="obs-b", value=210.0),
        ]
    )
    assert not outcome.values
    assert any("ambiguous_duplicate_values" in item.reasons for item in outcome.rejected)


def test_identical_duplicates_select_the_lexicographically_first_id():
    _, outcome = _materialize(
        [
            _row(observation_id="obs-b", value=200.0),
            _row(observation_id="obs-a", value=200.0),
        ]
    )
    materialized = outcome.values[("earth-a", "soil_clay_0_5cm_mean")]
    assert materialized.observation_id == "obs-a"


def test_typed_evidence_only_dimensions_are_not_similarity_features():
    rows = [
        _row(
            observation_id="geo-1",
            feature_id="geologic_unit",
            dimension_code="D02",
            value_kind="category",
            value={"code": "Ec", "name": "Crater"},
            unit=None,
            method_version="geology-1.0.0",
        ),
        _row(
            observation_id="thermal-1",
            feature_id="surface_brightness_temperature_k",
            dimension_code="D03",
            body="Moon",
            value=136.0,
            unit="K",
            method_version="diviner-pcp-tbol-1.0.0",
        ),
    ]
    registry = build_registry(rows)
    assert registry["geologic_unit"].roles["similarity"].supported is False
    assert registry["geologic_unit"].roles["evidence"].supported is True
    assert "taxonomy" in (registry["geologic_unit"].unsupported_reason or "")
    assert registry["surface_brightness_temperature_k"].roles["similarity"].supported is False
    assert registry["surface_brightness_temperature_k"].body_scope == ("Moon",)
    assert registry["surface_brightness_temperature_k"].roles["evidence"].supported is True


def test_registry_version_is_exposed():
    registry = build_registry([_row()])
    assert registry["soil_clay_0_5cm_mean"].to_payload()["registry_version"] == REGISTRY_VERSION
