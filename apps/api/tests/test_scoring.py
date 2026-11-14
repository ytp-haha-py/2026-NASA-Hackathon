from __future__ import annotations

import math

import pytest
from analog_api.domain.features import FEATURE_BY_ID
from analog_api.domain.scoring import (
    ScoringCandidate,
    ScoringError,
    normalize_target,
    rank_candidates,
)


def candidate(location_id: str, **values: float) -> ScoringCandidate:
    return ScoringCandidate(location_id=location_id, values=values)


def test_weighted_normalized_distance_matches_hand_calculation():
    outcome = rank_candidates(
        target={"slope_mean_deg": 12.0, "terrain_relief_p95_p05_m": 80.0},
        weights={"slope_mean_deg": 1.0, "terrain_relief_p95_p05_m": 0.5},
        candidates=[
            candidate(
                "earth-a",
                slope_mean_deg=18.0,
                terrain_relief_p95_p05_m=120.0,
            )
        ],
    )
    # sqrt((1*(6/10)^2 + 0.5*(40/200)^2) / 1.5)
    expected = math.sqrt((36 / 100 + 0.5 * (40 / 200) ** 2) / 1.5)
    assert outcome.matches[0].distance == pytest.approx(expected)
    assert outcome.matches[0].metrics[0].contribution == pytest.approx((36 / 100) / 1.5)
    assert outcome.matches[0].metrics[1].contribution == pytest.approx(
        (0.5 * (40 / 200) ** 2) / 1.5
    )


def test_zero_weight_dimension_does_not_contribute():
    outcome = rank_candidates(
        target={"slope_mean_deg": 10.0, "slope_p90_deg": 20.0},
        weights={"slope_mean_deg": 1.0, "slope_p90_deg": 0.0},
        candidates=[candidate("earth-a", slope_mean_deg=10.0, slope_p90_deg=80.0)],
    )
    assert outcome.matches[0].distance == pytest.approx(0.0)
    assert outcome.matches[0].metrics[1].contribution == 0.0


def test_registry_scale_is_used_not_result_subset_range():
    # The two candidates span a wide relief range; that must not change the scale.
    outcome = rank_candidates(
        target={"terrain_relief_p95_p05_m": 100.0},
        candidates=[
            candidate("earth-a", terrain_relief_p95_p05_m=100.0),
            candidate("earth-b", terrain_relief_p95_p05_m=1000.0),
        ],
    )
    assert outcome.matches[0].distance == 0.0
    assert outcome.matches[0].metrics[0].scale == FEATURE_BY_ID["terrain_relief_p95_p05_m"].scale


def test_sort_is_deterministic_with_location_id_tiebreak():
    outcome = rank_candidates(
        target={"slope_mean_deg": 10.0},
        candidates=[
            candidate("earth-zulu", slope_mean_deg=11.0),
            candidate("earth-alpha", slope_mean_deg=11.0),
            candidate("earth-mike", slope_mean_deg=12.0),
        ],
    )
    assert [match.location_id for match in outcome.matches] == [
        "earth-alpha",
        "earth-zulu",
        "earth-mike",
    ]


def test_limit_truncates_but_eligible_count_is_full():
    outcome = rank_candidates(
        target={"slope_mean_deg": 0.0},
        candidates=[candidate(f"earth-{i}", slope_mean_deg=float(i)) for i in range(5)],
        limit=2,
    )
    assert len(outcome.matches) == 2
    assert outcome.eligible_count == 5


def test_missing_metric_excludes_with_reason():
    outcome = rank_candidates(
        target={"slope_mean_deg": 10.0, "slope_p90_deg": 20.0},
        candidates=[candidate("earth-a", slope_mean_deg=10.0)],
    )
    assert outcome.matches == []
    assert outcome.excluded[0].location_id == "earth-a"
    assert "missing_metric:slope_p90_deg" in outcome.excluded[0].reason


@pytest.mark.parametrize(
    ("target", "weights", "code"),
    [
        ({}, {}, "empty_target"),
        ({"unknown_feature": 1.0}, {}, "unknown_feature"),
        ({"slope_mean_deg": float("nan")}, {}, "invalid_target_value"),
        ({"slope_mean_deg": float("inf")}, {}, "invalid_target_value"),
        ({"slope_mean_deg": -1.0}, {}, "target_out_of_domain"),
        ({"slope_mean_deg": 91.0}, {}, "target_out_of_domain"),
        ({"slope_mean_deg": 10.0}, {"slope_mean_deg": -1.0}, "invalid_weight"),
        ({"slope_mean_deg": 10.0}, {"slope_mean_deg": float("nan")}, "invalid_weight"),
        ({"slope_mean_deg": 10.0}, {"slope_mean_deg": 0.0}, "zero_total_weight"),
        ({"slope_mean_deg": 10.0}, {"slope_p90_deg": 1.0}, "unknown_weight_feature"),
    ],
)
def test_invalid_input_is_rejected(target, weights, code):
    with pytest.raises(ScoringError) as error:
        normalize_target(target, weights)
    assert error.value.code == code


@pytest.mark.parametrize("limit", [0, 11, -1, 1.5, True])
def test_invalid_limit_is_rejected(limit):
    with pytest.raises(ScoringError) as error:
        rank_candidates(
            target={"slope_mean_deg": 10.0},
            candidates=[candidate("earth-a", slope_mean_deg=10.0)],
            limit=limit,
        )
    assert error.value.code == "invalid_limit"


def test_candidate_with_non_finite_value_is_excluded():
    outcome = rank_candidates(
        target={"slope_mean_deg": 10.0},
        candidates=[candidate("earth-a", slope_mean_deg=float("nan"))],
    )
    assert outcome.matches == []
    assert "missing_metric:slope_mean_deg" in outcome.excluded[0].reason


def test_metric_comparison_contains_units_and_delta():
    outcome = rank_candidates(
        target={"roughness_std_m": 10.0},
        candidates=[candidate("earth-a", roughness_std_m=15.0)],
    )
    metric = outcome.matches[0].metrics[0]
    assert metric.unit == "meter"
    assert metric.delta == 5.0
    assert metric.normalized_delta == 0.25
