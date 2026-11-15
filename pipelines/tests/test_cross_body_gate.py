"""Offline tests for the cross-body coarse-ranking gate (Issue #36).

Every test uses either the committed bounded pilot manifests or an explicitly labelled
synthetic fixture. No test invents observed data, and no test asserts a cross-body score
for the current planetary pilots because none is eligible.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from analog_pipelines.coarse_features import COARSE_FEATURE_BY_ID, COARSE_METHOD_VERSION
from analog_pipelines.cross_body_gate import (
    FRAME_EARTH_WGS84,
    FRAME_GEOGRAPHIC,
    FRAME_PROJECTED_METRIC,
    SUPPORT_TOLERANCE_RELATIVE,
    BodyEvidence,
    CrossBodyGateError,
    CrossBodyMetricObservation,
    EarthBaseline,
    EarthFeatureBaseline,
    RankableCandidate,
    build_cross_body_gate,
    earth_coarse_baseline,
    evaluate_cross_body_gate,
    rank_comparable_candidates,
)

ROOT = Path(__file__).resolve().parents[2]
INDEX = ROOT / "pipelines/planetary/coarse/index.json"
TEST_METHOD = "test-coarse-method-1.0.0"
TEST_SUPPORT_M = 240.0


def _committed() -> tuple[dict, dict[str, dict]]:
    index = json.loads(INDEX.read_text(encoding="utf-8"))
    manifests = {
        pilot["manifest"]: json.loads((ROOT / pilot["manifest"]).read_text(encoding="utf-8"))
        for pilot in index["pilots"]
    }
    return index, manifests


def _baseline(
    *,
    method_version: str = TEST_METHOD,
    support_m: float = TEST_SUPPORT_M,
) -> EarthBaseline:
    return EarthBaseline(
        method_version=method_version,
        support_m=support_m,
        frame_kind=FRAME_PROJECTED_METRIC,
        value_source="synthetic_test_fixture_only",
        features=(
            EarthFeatureBaseline(
                feature_id="coarse_slope_mean_deg",
                label="Coarse mean slope",
                unit="degree",
                scale=10.0,
                spatial_support="synthetic test fixture",
                method_version=method_version,
                support_m=support_m,
            ),
        ),
    )


def _observation(
    *,
    body: str = "Moon",
    method_version: str = TEST_METHOD,
    support_m: float | None = TEST_SUPPORT_M,
    unit: str = "degree",
    frame_kind: str = FRAME_PROJECTED_METRIC,
    decision: str = "comparable",
    feature_id: str = "coarse_slope_mean_deg",
) -> CrossBodyMetricObservation:
    return CrossBodyMetricObservation(
        body=body,
        source_id="synthetic-test-source",
        region_id="synthetic-test-region",
        feature_id=feature_id,
        decision=decision,
        method_version=method_version,
        support_m=support_m,
        unit=unit,
        frame_kind=frame_kind,
        analysis_support_m=250.0,
        quality_flags=("synthetic_test_fixture",),
    )


def _body(
    observations: tuple[CrossBodyMetricObservation, ...] = (),
    *,
    body: str = "Moon",
    method_version: str = TEST_METHOD,
    support_m: float | None = TEST_SUPPORT_M,
    frame_kind: str = FRAME_PROJECTED_METRIC,
) -> BodyEvidence:
    return BodyEvidence(
        body=body,
        source_id="synthetic-test-source",
        region_id="synthetic-test-region",
        manifest="synthetic-test-manifest.json",
        method_version=method_version,
        native_support_m=support_m,
        analysis_support_m=250.0,
        frame_kind=frame_kind,
        sample_label="synthetic_test_fixture_only",
        vertical_unit_status="synthetic_test_fixture_only",
        quality_flags=("synthetic_test_fixture",),
        limitations=("synthetic test fixture; not observed data",),
        comparability=(),
        observations=observations,
    )


def test_current_committed_pilots_are_blocked_and_every_metric_is_explained():
    index, manifests = _committed()
    report = build_cross_body_gate(index, manifests)

    assert report["ranked"] is False
    assert report["ranking_enabled"] is False
    assert report["status"] == "blocked"
    assert report["ranked_candidates"] == []
    assert report["blocking_features"] == sorted(COARSE_FEATURE_BY_ID)
    assert report["counts"] == {
        "evidence_sources": 3,
        "scanned_metric_observations": 9,
        "eligible_metric_observations": 0,
        "excluded_metric_observations": 9,
        "unknown_metric_observations": 0,
        "ranked_candidates": 0,
    }
    assert "not a global or exhaustive survey" in report["note"]

    for body in report["bodies"]:
        assert body["metrics"], body["source_id"]
        for metric in body["metrics"]:
            assert metric["eligible"] is False
            assert metric["reasons"], (body["source_id"], metric["feature_id"])
            codes = {reason["code"] for reason in metric["reasons"]}
            assert "method_mismatch" in codes
            assert "decision_evidence_only" in codes or "decision_not_comparable" in codes
            assert "distance" not in metric
            assert "score" not in metric

    mola = next(
        body for body in report["bodies"] if body["source_id"] == "mars-mola-megdr-meg128-t44n000"
    )
    assert mola["support_ratio_vs_earth"] == pytest.approx(1.9966645433975558)
    for metric in mola["metrics"]:
        codes = {reason["code"] for reason in metric["reasons"]}
        assert "support_mismatch" in codes
        assert "decision_not_comparable" in codes

    assert len(report["comparability_decisions"]) == 9
    assert all(
        decision["decision"] != "comparable" for decision in report["comparability_decisions"]
    )
    assert report["earth_baseline"]["method_version"] == COARSE_METHOD_VERSION


def test_missing_support_is_excluded_and_never_zero_filled():
    report = evaluate_cross_body_gate(
        earth=_baseline(),
        bodies=(_body((_observation(support_m=None),)),),
    )
    assert report["ranking_enabled"] is False
    metric = report["bodies"][0]["metrics"][0]
    assert metric["eligible"] is False
    assert metric["support_ratio_vs_earth"] is None
    assert [reason["code"] for reason in metric["reasons"]] == ["support_missing"]
    assert report["counts"]["eligible_metric_observations"] == 0
    assert report["uncertainty"]["earth_support_m"] == TEST_SUPPORT_M


def test_cross_body_frame_rejection_even_with_matching_method_and_support():
    earth_wgs84 = evaluate_cross_body_gate(
        earth=_baseline(),
        bodies=(_body((_observation(frame_kind=FRAME_EARTH_WGS84),)),),
    )
    reasons = {reason["code"] for reason in earth_wgs84["bodies"][0]["metrics"][0]["reasons"]}
    assert earth_wgs84["bodies"][0]["metrics"][0]["eligible"] is False
    assert "earth_frame_on_planetary_body" in reasons
    assert "frame_mismatch" in reasons

    geographic = evaluate_cross_body_gate(
        earth=_baseline(),
        bodies=(_body((_observation(frame_kind=FRAME_GEOGRAPHIC),)),),
    )
    reasons = {reason["code"] for reason in geographic["bodies"][0]["metrics"][0]["reasons"]}
    assert reasons == {"frame_mismatch"}


def test_support_tolerance_is_asymmetric_and_documented():
    # A finer source is accepted because it can be aggregated to the analysis grid; it
    # still has to pass the method and validation criteria.
    finer = evaluate_cross_body_gate(
        earth=_baseline(),
        bodies=(_body((_observation(support_m=20.0),)),),
    )
    assert finer["bodies"][0]["metrics"][0]["eligible"] is True
    assert finer["ranking_enabled"] is True

    at_limit = TEST_SUPPORT_M * (1.0 + SUPPORT_TOLERANCE_RELATIVE)
    within = evaluate_cross_body_gate(
        earth=_baseline(),
        bodies=(_body((_observation(support_m=at_limit),)),),
    )
    assert within["bodies"][0]["metrics"][0]["eligible"] is True

    coarser = evaluate_cross_body_gate(
        earth=_baseline(),
        bodies=(_body((_observation(support_m=463.0576672),)),),
    )
    assert coarser["bodies"][0]["metrics"][0]["eligible"] is False
    assert [reason["code"] for reason in coarser["bodies"][0]["metrics"][0]["reasons"]] == [
        "support_mismatch"
    ]


def test_eligible_same_method_subset_ranks_deterministically_and_excludes_mismatches():
    earth = _baseline()
    gate = evaluate_cross_body_gate(
        earth=earth,
        bodies=(_body((_observation(),)),),
    )
    assert gate["ranking_enabled"] is True
    assert gate["status"] == "ready"
    assert gate["ranked"] is False  # no target was supplied; the gate itself never ranks
    assert gate["counts"]["eligible_metric_observations"] == 1

    target = {"coarse_slope_mean_deg": 10.0}
    candidates = (
        RankableCandidate(
            candidate_id="test:b",
            body="Mars",
            values={"coarse_slope_mean_deg": 10.0},
            method_version=TEST_METHOD,
            support_m=TEST_SUPPORT_M,
            frame_kind=FRAME_PROJECTED_METRIC,
        ),
        RankableCandidate(
            candidate_id="test:a",
            body="Mars",
            values={"coarse_slope_mean_deg": 10.0},
            method_version=TEST_METHOD,
            support_m=TEST_SUPPORT_M,
            frame_kind=FRAME_PROJECTED_METRIC,
        ),
        RankableCandidate(
            candidate_id="test:c",
            body="Moon",
            values={"coarse_slope_mean_deg": 20.0},
            method_version=TEST_METHOD,
            support_m=TEST_SUPPORT_M,
            frame_kind=FRAME_PROJECTED_METRIC,
        ),
        RankableCandidate(
            candidate_id="test:incompatible",
            body="Moon",
            values={"coarse_slope_mean_deg": 10.0},
            method_version="coarse-planetary-dem-horn-1.0.0",
            support_m=TEST_SUPPORT_M,
            frame_kind=FRAME_PROJECTED_METRIC,
        ),
    )
    first = rank_comparable_candidates(
        earth=earth,
        eligible_features=["coarse_slope_mean_deg"],
        target=target,
        weights={"coarse_slope_mean_deg": 1.0},
        candidates=candidates,
        limit=10,
    )
    second = rank_comparable_candidates(
        earth=earth,
        eligible_features=["coarse_slope_mean_deg"],
        target=target,
        weights={"coarse_slope_mean_deg": 1.0},
        candidates=candidates,
        limit=10,
    )
    assert first["ranked"] is True
    assert [item["candidate_id"] for item in first["results"]] == [
        "test:a",
        "test:b",
        "test:c",
    ]
    assert [item["candidate_id"] for item in first["results"]] == [
        item["candidate_id"] for item in second["results"]
    ]
    assert [item["distance"] for item in first["results"]] == sorted(
        item["distance"] for item in first["results"]
    )
    assert [item["candidate_id"] for item in first["excluded"]] == ["test:incompatible"]
    assert first["excluded"][0]["reasons"][0]["code"] == "method_mismatch"


def test_no_score_when_no_metric_is_comparable():
    index, manifests = _committed()
    report = build_cross_body_gate(index, manifests)
    eligible = [
        metric["feature_id"]
        for body in report["bodies"]
        for metric in body["metrics"]
        if metric["eligible"]
    ]
    assert eligible == []
    assert report["ranked"] is False
    assert report["ranked_candidates"] == []

    with pytest.raises(CrossBodyGateError) as excinfo:
        rank_comparable_candidates(
            earth=earth_coarse_baseline(),
            eligible_features=eligible,
            target={"coarse_slope_mean_deg": 10.0},
            weights={"coarse_slope_mean_deg": 1.0},
            candidates=(),
        )
    assert excinfo.value.code == "no_eligible_metric"


def test_target_outside_the_eligible_subset_is_refused():
    earth = _baseline()
    with pytest.raises(CrossBodyGateError) as excinfo:
        rank_comparable_candidates(
            earth=earth,
            eligible_features=["coarse_slope_mean_deg"],
            target={"coarse_slope_p90_deg": 10.0},
            weights={"coarse_slope_p90_deg": 1.0},
            candidates=(),
        )
    assert excinfo.value.code == "ineligible_target"


def test_missing_manifest_is_visible_and_not_scored():
    report = build_cross_body_gate(
        {
            "pilots": [
                {
                    "body": "Moon",
                    "source_id": "missing-source",
                    "manifest": "does/not/exist.json",
                    "native_resolution_m": 236.9,
                }
            ]
        },
        {},
    )
    assert report["status"] == "no_evidence"
    assert report["ranked"] is False
    assert report["bodies"][0]["sample_label"] == "unavailable"
    assert report["bodies"][0]["metrics"] == []
    assert report["uncertainty"]["unavailable_manifests"] == ["does/not/exist.json"]
    assert "manifest unavailable" in report["bodies"][0]["limitations"][0]
