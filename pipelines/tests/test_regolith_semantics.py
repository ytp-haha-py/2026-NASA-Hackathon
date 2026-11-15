"""D06 measurement-semantics contract and depth/state tests (Issue #46)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from analog_pipelines.acquisition.providers_soilgrids import SOILGRIDS_DEPTHS
from analog_pipelines.dimensions import (
    D06_PARTICLE_SIZE_BINS_UM,
    D06_PARTICLE_SIZE_LABELS,
    FEATURE_V2_BY_ID,
    validate_dimension_observation,
)
from analog_pipelines.typed_contracts import (
    DepthSupport,
    SpatialSupport,
    TemporalSupport,
    TypedObservation,
)
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[2]
SPEC = ROOT / "contracts" / "d06-regolith-semantics-v1.json"


def _spec() -> dict:
    return json.loads(SPEC.read_text(encoding="utf-8"))


def _observation(**overrides) -> TypedObservation:
    payload = {
        "observation_id": "d06-test",
        "area_id": "earth-test",
        "body": "Earth",
        "dimension_code": "D06",
        "feature_id": "porosity_fraction",
        "value_kind": "numeric",
        "state": "detected",
        "value": 0.4,
        "unit": "fraction",
        "spatial_support": SpatialSupport(kind="point", crs="EPSG:4326"),
        "temporal_support": TemporalSupport(kind="static"),
        "depth_support": DepthSupport(kind="depth_interval", min_m=0.05, max_m=0.15),
        "method_version": "regolith-evidence-0.1.0",
        "dataset_ids": ["sparse-regolith-measurements"],
        "source_asset_ids": ["fixture"],
        "score_role": "evidence_only",
    }
    payload.update(overrides)
    return TypedObservation.model_validate(payload)


def test_spec_is_versioned_and_matches_the_feature_registry():
    spec = _spec()
    assert spec["spec_id"] == "d06-regolith-measurement-semantics"
    assert spec["version"] == "1.0.0"
    assert spec["status"] == "specification_only_not_observed_coverage"
    variables = {variable["feature_id"]: variable for variable in spec["variables"]}
    assert set(variables) == {
        "particle_size_distribution",
        "bulk_density_kg_m3",
        "porosity_fraction",
        "cohesion_kpa",
        "friction_angle_deg",
        "bearing_strength_kpa",
    }
    for feature_id, variable in variables.items():
        feature = FEATURE_V2_BY_ID[feature_id]
        assert variable["value_kind"] == feature.value_kind
        assert variable["unit"] == feature.unit
        assert feature.dimension_code == "D06"
        assert feature.score_role == "evidence_only"


def test_particle_size_bins_are_explicit_and_ordered():
    variable = next(
        item for item in _spec()["variables"] if item["feature_id"] == "particle_size_distribution"
    )
    bins = variable["bins"]
    edges = bins["edges_um"]
    assert bins["edge_kind"] == "lower_edge"
    assert bins["unit"] == "micrometre"
    assert edges == list(D06_PARTICLE_SIZE_BINS_UM)
    assert bins["labels"] == list(D06_PARTICLE_SIZE_LABELS)
    assert edges == sorted(edges)
    assert len(edges) == len(bins["labels"]) + 1
    assert bins["terminal_bin"] == "extends to +infinity"
    assert set(bins["basis_required"]) == {"mass", "volume", "number"}


def test_depth_intervals_match_the_provider_and_never_pool():
    policy = _spec()["depth_policy"]
    assert policy["required_for_detected_d06"] is True
    assert policy["pool_depths_in_one_value"] is False
    registered = {
        item["label"]: (item["min_m"], item["max_m"]) for item in policy["soilgrids_intervals_cm"]
    }
    assert registered == {
        label: (start / 100.0, end / 100.0) for label, (start, end) in SOILGRIDS_DEPTHS.items()
    }
    for label, (start, end) in registered.items():
        assert start < end, label


def test_moon_and_mars_are_declared_no_coverage_without_fabricated_values():
    spec = _spec()
    for body in ("Moon", "Mars"):
        coverage = spec["body_coverage"][body]
        assert coverage["status"] == "no_coverage"
        assert coverage["registered_real_measurements"] == 0
        assert "DEM" in coverage["note"]
    for variable in spec["variables"]:
        assert variable["moon_status"] == "no_coverage"
        assert variable["mars_status"] == "no_coverage"
        assert variable["forbidden_derivations"]
    assert spec["body_coverage"]["Earth"]["status"] == "model_prediction_only"
    assert spec["body_coverage"]["Earth"]["prediction_source"].startswith("SoilGrids 2.0")


def test_regolith_values_require_explicit_depth_and_in_range_units():
    unknown_depth = _observation(
        depth_support=DepthSupport(kind="unknown"),
    )
    assert any("depth" in error for error in validate_dimension_observation(unknown_depth))
    assert validate_dimension_observation(_observation()) == []
    assert any(
        "porosity" in error for error in validate_dimension_observation(_observation(value=1.5))
    )
    assert any(
        "negative" in error
        for error in validate_dimension_observation(
            _observation(
                feature_id="cohesion_kpa",
                unit="kPa",
                value=-1.0,
            )
        )
    )
    assert any(
        "90" in error
        for error in validate_dimension_observation(
            _observation(
                feature_id="friction_angle_deg",
                unit="degree",
                value=120.0,
            )
        )
    )


def test_measured_zero_is_kept_and_missing_never_carries_a_value():
    zero = _observation(
        feature_id="particle_size_distribution",
        value_kind="distribution",
        unit="fraction",
        value={"bins": [0.0, 2.0, 50.0], "fractions": [0.0, 0.4, 0.6]},
    )
    assert zero.value["fractions"][0] == 0.0
    assert validate_dimension_observation(zero) == []
    no_coverage = _observation(
        feature_id="bulk_density_kg_m3",
        unit="kg/m^3",
        state="no_coverage",
        value=None,
    )
    assert no_coverage.value is None
    assert validate_dimension_observation(no_coverage) == []
    with pytest.raises(ValidationError):
        _observation(
            feature_id="bulk_density_kg_m3",
            unit="kg/m^3",
            state="no_coverage",
            value=0.0,
        )


def test_distribution_bins_must_be_strictly_increasing():
    with pytest.raises(ValidationError):
        _observation(
            feature_id="particle_size_distribution",
            value_kind="distribution",
            unit="fraction",
            value={"bins": [0.0, 50.0, 2.0], "fractions": [0.2, 0.3, 0.5]},
        )
