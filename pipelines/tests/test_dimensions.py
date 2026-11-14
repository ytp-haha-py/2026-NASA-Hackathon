from __future__ import annotations

from pathlib import Path

import pytest
from analog_pipelines.dimensions import (
    FEATURE_V2_BY_ID,
    FEATURES_V2,
    parse_fixture_observations,
    validate_dimension_observation,
)
from analog_pipelines.typed_contracts import SpatialSupport, TemporalSupport, TypedObservation
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "samples" / "fixtures" / "dimensions"


def base_payload() -> dict:
    return {
        "observation_id": "obs",
        "area_id": "area",
        "body": "Earth",
        "dimension_code": "D05",
        "feature_id": "solar_incidence_deg",
        "value_kind": "numeric",
        "state": "detected",
        "value": 10.0,
        "unit": "degree",
        "spatial_support": SpatialSupport(
            kind="window", crs="EPSG:32633", resolution_m=90, window_size_m=1980
        ),
        "temporal_support": TemporalSupport(kind="static"),
        "method_version": "illumination-radiation-0.1.0",
        "dataset_ids": ["fixture-dataset"],
        "source_asset_ids": ["fixture"],
        "score_role": "evidence_only",
    }


def test_all_six_dimension_fixtures_parse():
    files = sorted(FIXTURES.glob("*.json"))
    assert len(files) == 6
    observations = [parse_fixture_observations(path)[0] for path in files]
    assert {obs.dimension_code for obs in observations} == {
        "D01",
        "D02",
        "D03",
        "D04",
        "D05",
        "D06",
    }
    assert all(obs.score_role == "evidence_only" for obs in observations)


def test_feature_registry_has_stable_evidence_features():
    assert len(FEATURES_V2) >= 12
    assert FEATURE_V2_BY_ID["mineral_spectral_detection"].value_kind == "detection"
    assert FEATURE_V2_BY_ID["particle_size_distribution"].value_kind == "distribution"
    assert FEATURE_V2_BY_ID["solar_incidence_deg"].unit == "degree"
    assert FEATURE_V2_BY_ID["ionizing_dose_gy"].unit == "Gy"


def test_illumination_and_radiation_cannot_be_conflated():
    payload = base_payload()
    payload["unit"] = "Gy"
    observation = TypedObservation.model_validate(payload)
    errors = validate_dimension_observation(observation)
    assert any("dose" in error or "radiation" in error for error in errors)


def test_detection_and_missing_states_are_distinct():
    detection = TypedObservation.model_validate(
        {
            **base_payload(),
            "dimension_code": "D02",
            "feature_id": "mineral_spectral_detection",
            "value_kind": "detection",
            "unit": "detection",
            "value": {"indicator": 0, "confidence": 0.1},
            "method_version": "geology-evidence-0.1.0",
        }
    )
    assert validate_dimension_observation(detection) == []
    no_coverage = TypedObservation.model_validate(
        {
            **base_payload(),
            "dimension_code": "D02",
            "feature_id": "mineral_spectral_detection",
            "value_kind": "detection",
            "unit": "detection",
            "state": "no_coverage",
            "value": None,
            "method_version": "geology-evidence-0.1.0",
        }
    )
    assert no_coverage.value is None
    with pytest.raises(ValidationError):
        TypedObservation.model_validate(
            {
                **base_payload(),
                "dimension_code": "D02",
                "feature_id": "mineral_spectral_detection",
                "value_kind": "detection",
                "unit": "detection",
                "state": "no_coverage",
                "value": {"indicator": 0},
                "method_version": "geology-evidence-0.1.0",
            }
        )


def test_distribution_bins_must_match_fractions():
    payload = {
        **base_payload(),
        "dimension_code": "D06",
        "feature_id": "particle_size_distribution",
        "value_kind": "distribution",
        "unit": "fraction",
        "value": {"bins": [0, 63, 250], "fractions": [0.5, 0.3, 0.2]},
        "method_version": "regolith-evidence-0.1.0",
    }
    observation = TypedObservation.model_validate(payload)
    assert validate_dimension_observation(observation) == []
    with pytest.raises(ValidationError):
        TypedObservation.model_validate(
            {**payload, "value": {"bins": [0, 63], "fractions": [0.5, 0.3, 0.2]}}
        )


def test_unknown_dimension_feature_is_rejected():
    payload = {
        **base_payload(),
        "feature_id": "not_a_feature",
        "dimension_code": "D01",
        "method_version": "curvature-geomorphon-0.1.0",
    }
    observation = TypedObservation.model_validate(payload)
    errors = validate_dimension_observation(observation)
    assert any("unknown" in error for error in errors)
