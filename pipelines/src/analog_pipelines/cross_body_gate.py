"""Cross-body coarse-ranking eligibility gate (Issue #36).

The gate is deliberately conservative: a metric is eligible for cross-body ranking
only when the quantity, producing method, native support, unit, frame class and an
explicit ``comparable`` validation decision all agree. The current planetary pilot
contains no such metric, so the gate refuses to rank and returns a per-body/per-metric
explanation instead of a score.

The module is pure: it consumes already-loaded JSON manifests, performs no file,
network or database I/O, and never derives a score from an ineligible record.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from analog_pipelines.coarse_features import (
    COARSE_FEATURE_BY_ID,
    COARSE_FEATURES,
    COARSE_METHOD_VERSION,
)
from analog_pipelines.planetary_coarse import (
    DECISION_COMPARABLE,
    DECISION_EVIDENCE_ONLY,
    DECISION_NOT_COMPARABLE,
    DECISION_NOT_SUPPORTED,
    DECISIONS,
    EARTH_COARSE_NOMINAL_RESOLUTION_M,
    EARTH_COARSE_SOURCE_NOTE,
)

GATE_VERSION = "cross-body-coarse-gate-1.0.0"
CLAIM_SCOPE = "bounded_pilot_cells_only_not_a_global_survey"

# A support ratio inside this band is *not* treated as method equivalence. It only
# keeps the support criterion from being the blocker when the native pixel spacing is
# close enough to the Earth 7.5 arc-second product. The 463 m MOLA cells are ~2x
# coarser and still fail, and every current metric also fails the method criterion.
SUPPORT_TOLERANCE_RELATIVE = 0.05

FRAME_PROJECTED_METRIC = "projected_metric_body_fixed"
FRAME_GEOGRAPHIC = "geographic_degrees"
FRAME_EARTH_WGS84 = "earth_wgs84"
FRAME_UNKNOWN = "unknown"

_REASON_ORDER = (
    "quantity_mismatch",
    "method_missing",
    "method_mismatch",
    "support_missing",
    "support_mismatch",
    "unit_mismatch",
    "frame_mismatch",
    "earth_frame_on_planetary_body",
    "decision_missing",
    "decision_unknown",
    "decision_evidence_only",
    "decision_not_comparable",
    "decision_not_supported",
    "missing_value",
    "value_out_of_domain",
)

_REASON_MESSAGES = {
    "quantity_mismatch": (
        "the feature id is not a coarse quantity in the Earth baseline; a different "
        "quantity is never scored"
    ),
    "method_missing": "the producing method version is missing; method equivalence is not assumed",
    "method_mismatch": (
        "the producing method differs from the Earth coarse method; a DEM derivative and a "
        "published preprocessed slope product are not interchangeable"
    ),
    "support_missing": ("the native support is missing; support is never assumed or zero-filled"),
    "support_mismatch": (
        "the native support is coarser than the Earth coarse support beyond the explicit gate "
        "tolerance; a coarser source cannot be upsampled into same-support evidence"
    ),
    "unit_mismatch": "the unit differs from the Earth feature unit; values are not converted",
    "frame_mismatch": (
        "the frame class differs from the Earth baseline; geographic-degree frames are not "
        "mixed with projected metric surface frames"
    ),
    "earth_frame_on_planetary_body": (
        "an Earth WGS84 frame was supplied for a planetary body; planetary body-fixed frames "
        "are required"
    ),
    "decision_missing": "no comparability decision is recorded for this metric",
    "decision_unknown": "the comparability decision is not a recognised decision",
    "decision_evidence_only": (
        "the comparability decision is evidence_only: quantity and footprint may match, but "
        "method equivalence is not validated"
    ),
    "decision_not_comparable": (
        "the comparability decision is not_comparable: the documented method/support mismatch "
        "forbids scoring"
    ),
    "decision_not_supported": (
        "the metric is not supported by the planetary coarse pilot and has no value"
    ),
    "missing_value": "the candidate has no finite value for an eligible metric",
    "value_out_of_domain": "the candidate value is outside the coarse feature domain",
}


class CrossBodyGateError(ValueError):
    """The gate refused an operation because the eligibility contract was not met."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class EarthFeatureBaseline:
    feature_id: str
    label: str
    unit: str
    scale: float
    spatial_support: str
    method_version: str
    support_m: float


@dataclass(frozen=True, slots=True)
class EarthBaseline:
    method_version: str
    support_m: float
    frame_kind: str
    value_source: str
    features: tuple[EarthFeatureBaseline, ...]

    def feature(self, feature_id: str) -> EarthFeatureBaseline | None:
        for feature in self.features:
            if feature.feature_id == feature_id:
                return feature
        return None


@dataclass(frozen=True, slots=True)
class CrossBodyMetricObservation:
    body: str
    source_id: str
    region_id: str
    feature_id: str
    decision: str
    method_version: str
    support_m: float | None
    unit: str
    frame_kind: str
    analysis_support_m: float | None = None
    quality_flags: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class BodyEvidence:
    body: str
    source_id: str
    region_id: str
    manifest: str
    method_version: str
    native_support_m: float | None
    analysis_support_m: float | None
    frame_kind: str
    sample_label: str
    vertical_unit_status: str
    quality_flags: tuple[str, ...]
    limitations: tuple[str, ...]
    comparability: tuple[Mapping[str, Any], ...]
    observations: tuple[CrossBodyMetricObservation, ...]
    support_comparison: Mapping[str, Any] | None = None
    method_calibration: Mapping[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class RankableCandidate:
    candidate_id: str
    body: str
    values: Mapping[str, float | None]
    method_version: str
    support_m: float | None
    frame_kind: str


def earth_coarse_baseline() -> EarthBaseline:
    """The Earth coarse contract every cross-body observation is measured against."""
    features = tuple(
        EarthFeatureBaseline(
            feature_id=feature.feature_id,
            label=feature.label,
            unit=feature.unit,
            scale=feature.scale,
            spatial_support=feature.spatial_support,
            method_version=feature.method_version,
            support_m=EARTH_COARSE_NOMINAL_RESOLUTION_M,
        )
        for feature in COARSE_FEATURES
    )
    return EarthBaseline(
        method_version=COARSE_METHOD_VERSION,
        support_m=EARTH_COARSE_NOMINAL_RESOLUTION_M,
        frame_kind=FRAME_PROJECTED_METRIC,
        value_source=EARTH_COARSE_SOURCE_NOTE,
        features=features,
    )


def frame_kind_from_crs_wkt(wkt: str) -> str:
    """Classify a stored CRS WKT without reopening the raster or assuming WGS84."""
    upper = " ".join(str(wkt).upper().split())
    if not upper:
        return FRAME_UNKNOWN
    if upper.startswith("PROJCRS") or upper.startswith("PROJCS"):
        return FRAME_PROJECTED_METRIC
    if "WGS 84" in upper and ("GEOGCRS" in upper or "GEOGCS" in upper):
        return FRAME_EARTH_WGS84
    if "GEOGCRS" in upper or "GEOGCS" in upper:
        return FRAME_GEOGRAPHIC
    return FRAME_UNKNOWN


def _finite_or_none(value: Any) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if math.isfinite(numeric) else None


def _reason(code: str, detail: str | None = None) -> dict[str, str]:
    message = _REASON_MESSAGES.get(code, code)
    if detail:
        message = f"{message}: {detail}"
    return {"code": code, "message": message}


def _reason_sort_key(reason: Mapping[str, str]) -> tuple[int, str]:
    code = str(reason.get("code", ""))
    try:
        return (_REASON_ORDER.index(code), code)
    except ValueError:
        return (len(_REASON_ORDER), code)


def _decision_reason(decision: str) -> dict[str, str]:
    if not decision:
        return _reason("decision_missing")
    if decision == DECISION_EVIDENCE_ONLY:
        return _reason("decision_evidence_only")
    if decision == DECISION_NOT_COMPARABLE:
        return _reason("decision_not_comparable")
    if decision == DECISION_NOT_SUPPORTED:
        return _reason("decision_not_supported")
    if decision not in DECISIONS:
        return _reason("decision_unknown", repr(decision))
    return _reason("decision_unknown", repr(decision))


def _metric_eligibility(
    observation: CrossBodyMetricObservation,
    earth: EarthBaseline,
) -> dict[str, Any]:
    reasons: list[dict[str, str]] = []
    baseline = earth.feature(observation.feature_id)
    if baseline is None:
        reasons.append(_reason("quantity_mismatch", repr(observation.feature_id)))

    if not observation.method_version:
        reasons.append(_reason("method_missing"))
    elif observation.method_version != earth.method_version:
        reasons.append(
            _reason(
                "method_mismatch",
                f"{observation.method_version!r} != {earth.method_version!r}",
            )
        )

    support_ratio: float | None = None
    support_m = _finite_or_none(observation.support_m)
    if support_m is None or support_m <= 0:
        reasons.append(_reason("support_missing"))
    else:
        support_ratio = support_m / earth.support_m
        # A finer source may be aggregated to the common analysis grid. A coarser source
        # cannot be upsampled into same-support evidence.
        if support_ratio > 1.0 + SUPPORT_TOLERANCE_RELATIVE:
            reasons.append(
                _reason(
                    "support_mismatch",
                    f"native {support_m:.3f} m vs Earth {earth.support_m:.3f} m "
                    f"(ratio {support_ratio:.3f}, coarser than the "
                    f"+{SUPPORT_TOLERANCE_RELATIVE:.0%} gate tolerance)",
                )
            )

    if baseline is not None and observation.unit != baseline.unit:
        reasons.append(_reason("unit_mismatch", f"{observation.unit!r} != {baseline.unit!r}"))

    if observation.body != "Earth" and observation.frame_kind == FRAME_EARTH_WGS84:
        reasons.append(_reason("earth_frame_on_planetary_body", observation.body))
    if observation.frame_kind != earth.frame_kind:
        reasons.append(
            _reason(
                "frame_mismatch",
                f"{observation.frame_kind!r} != {earth.frame_kind!r}",
            )
        )

    if observation.decision != DECISION_COMPARABLE:
        reasons.append(_decision_reason(observation.decision))

    reasons.sort(key=_reason_sort_key)
    return {
        "feature_id": observation.feature_id,
        "label": baseline.label if baseline is not None else observation.feature_id,
        "unit": baseline.unit if baseline is not None else observation.unit,
        "decision": observation.decision,
        "eligible": not reasons,
        "method_version": observation.method_version,
        "earth_method_version": earth.method_version,
        "support_ratio_vs_earth": support_ratio,
        "analysis_support_ratio_vs_earth": (
            observation.analysis_support_m / earth.support_m
            if observation.analysis_support_m is not None
            else None
        ),
        "reasons": reasons,
    }


def bodies_from_planetary_index(
    index: Mapping[str, Any],
    manifests: Mapping[str, Mapping[str, Any]],
) -> tuple[BodyEvidence, ...]:
    """Turn committed pilot manifests into gate inputs. Missing manifests stay visible."""
    bodies: list[BodyEvidence] = []
    for pilot in index.get("pilots", []):
        if not isinstance(pilot, Mapping):
            continue
        manifest_ref = str(pilot.get("manifest", ""))
        manifest = manifests.get(manifest_ref)
        if manifest is None:
            bodies.append(
                BodyEvidence(
                    body=str(pilot.get("body", "unknown")),
                    source_id=str(pilot.get("source_id", "unknown")),
                    region_id="",
                    manifest=manifest_ref,
                    method_version=str(index.get("method_version", "")),
                    native_support_m=_finite_or_none(pilot.get("native_resolution_m")),
                    analysis_support_m=None,
                    frame_kind=FRAME_UNKNOWN,
                    sample_label="unavailable",
                    vertical_unit_status="unknown",
                    quality_flags=(),
                    limitations=(
                        "manifest unavailable; no metric was evaluated and no value is reported",
                    ),
                    comparability=(),
                    observations=(),
                )
            )
            continue

        source = manifest.get("source", {})
        method = manifest.get("method", {})
        region = manifest.get("region", {})
        cells = manifest.get("cells", [])
        frame_kind = FRAME_UNKNOWN
        analysis_support_m = _finite_or_none(method.get("analysis_resolution_m"))
        if isinstance(cells, Sequence) and cells:
            first = cells[0]
            if isinstance(first, Mapping):
                frame_kind = frame_kind_from_crs_wkt(str(first.get("crs_wkt", "")))
                if first.get("analysis_resolution_m") is not None:
                    analysis_support_m = _finite_or_none(first.get("analysis_resolution_m"))

        comparability = tuple(
            item for item in manifest.get("comparability", []) if isinstance(item, Mapping)
        )
        observations = tuple(
            CrossBodyMetricObservation(
                body=str(source.get("body", pilot.get("body", "unknown"))),
                source_id=str(source.get("source_id", pilot.get("source_id", "unknown"))),
                region_id=str(region.get("region_id", "")),
                feature_id=str(decision.get("feature_id", "")),
                decision=str(decision.get("decision", "")),
                method_version=str(
                    method.get("method_version", decision.get("method_version", ""))
                ),
                support_m=_finite_or_none(
                    source.get("native_resolution_m", pilot.get("native_resolution_m"))
                ),
                unit=str(decision.get("unit", "")),
                frame_kind=frame_kind,
                analysis_support_m=analysis_support_m,
                quality_flags=tuple(str(flag) for flag in source.get("quality_flags", [])),
            )
            for decision in comparability
        )
        limitations = tuple(str(item) for item in manifest.get("limitations", []))
        if not observations:
            limitations = limitations + (
                "comparability table unavailable; no metric was eligible for evaluation",
            )
        bodies.append(
            BodyEvidence(
                body=str(source.get("body", pilot.get("body", "unknown"))),
                source_id=str(source.get("source_id", pilot.get("source_id", "unknown"))),
                region_id=str(region.get("region_id", "")),
                manifest=manifest_ref,
                method_version=str(method.get("method_version", "")),
                native_support_m=_finite_or_none(source.get("native_resolution_m")),
                analysis_support_m=analysis_support_m,
                frame_kind=frame_kind,
                sample_label=str(region.get("sample_label", "")),
                vertical_unit_status=str(source.get("vertical_unit_status", "unknown")),
                quality_flags=tuple(str(flag) for flag in source.get("quality_flags", [])),
                limitations=limitations,
                comparability=comparability,
                observations=observations,
                support_comparison=(
                    manifest.get("support_comparison")
                    if isinstance(manifest.get("support_comparison"), Mapping)
                    else None
                ),
                method_calibration=(
                    manifest.get("method_calibration")
                    if isinstance(manifest.get("method_calibration"), Mapping)
                    else None
                ),
            )
        )
    return tuple(bodies)


def _body_sort_key(body: BodyEvidence) -> tuple[str, str, str]:
    return (body.body, body.source_id, body.region_id)


def evaluate_cross_body_gate(
    *,
    earth: EarthBaseline,
    bodies: Sequence[BodyEvidence],
) -> dict[str, Any]:
    """Evaluate every body/metric and refuse ranking unless an explicit subset is eligible."""
    ordered_bodies = sorted(bodies, key=_body_sort_key)
    body_payloads: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    blocking_features: set[str] = set()
    scanned = eligible_count = excluded = unknown = 0
    unmeasured: list[str] = []
    measured_comparisons: list[dict[str, Any]] = []
    unavailable: list[str] = []

    for body in ordered_bodies:
        if not body.observations:
            unavailable.append(body.manifest)
        metrics: list[dict[str, Any]] = []
        for observation in sorted(body.observations, key=lambda item: item.feature_id):
            scanned += 1
            metric = _metric_eligibility(observation, earth)
            if metric["eligible"]:
                eligible_count += 1
            elif observation.decision in DECISIONS:
                excluded += 1
            else:
                unknown += 1
            if not metric["eligible"]:
                blocking_features.add(observation.feature_id)
            metrics.append(metric)

        for decision in sorted(
            body.comparability, key=lambda item: str(item.get("feature_id", ""))
        ):
            payload = dict(decision)
            payload["body"] = body.body
            payload["source_id"] = body.source_id
            decisions.append(payload)

        body_payloads.append(
            {
                "body": body.body,
                "source_id": body.source_id,
                "region_id": body.region_id,
                "manifest": body.manifest,
                "method_version": body.method_version,
                "native_support_m": body.native_support_m,
                "analysis_support_m": body.analysis_support_m,
                "support_ratio_vs_earth": (
                    body.native_support_m / earth.support_m
                    if body.native_support_m is not None
                    else None
                ),
                "analysis_support_ratio_vs_earth": (
                    body.analysis_support_m / earth.support_m
                    if body.analysis_support_m is not None
                    else None
                ),
                "frame_kind": body.frame_kind,
                "sample_label": body.sample_label,
                "vertical_unit_status": body.vertical_unit_status,
                "metrics": metrics,
                "limitations": list(body.limitations),
                "quality_flags": list(body.quality_flags),
            }
        )

        support_comparison = body.support_comparison
        if support_comparison is not None:
            measured_comparisons.append(
                {
                    "body": body.body,
                    "source_id": body.source_id,
                    "label": support_comparison.get("label"),
                    "interpretation": support_comparison.get("interpretation"),
                    "pair_count": len(support_comparison.get("pairs", [])),
                }
            )
        if body.method_calibration is not None:
            statement = body.method_calibration.get("not_measured_against_earth")
            if statement and statement not in unmeasured:
                unmeasured.append(str(statement))

    ranking_enabled = eligible_count > 0
    status = "no_evidence" if scanned == 0 else ("blocked" if not ranking_enabled else "ready")
    blocking = sorted(blocking_features)
    return {
        "gate_version": GATE_VERSION,
        "ranked": False,
        "ranking_enabled": ranking_enabled,
        "status": status,
        "claim_scope": CLAIM_SCOPE,
        "eligibility_contract": list(ELIGIBILITY_CONTRACT),
        "earth_baseline": {
            "method_version": earth.method_version,
            "support_m": earth.support_m,
            "frame_kind": earth.frame_kind,
            "value_source": earth.value_source,
            "features": [
                {
                    "feature_id": feature.feature_id,
                    "label": feature.label,
                    "unit": feature.unit,
                    "scale": feature.scale,
                    "spatial_support": feature.spatial_support,
                }
                for feature in earth.features
            ],
        },
        "bodies": body_payloads,
        "comparability_decisions": decisions,
        "blocking_features": blocking,
        "uncertainty": {
            "support_tolerance_relative": SUPPORT_TOLERANCE_RELATIVE,
            "earth_support_m": earth.support_m,
            "method_equivalence": (
                "not_established" if not ranking_enabled else "established_by_explicit_comparable"
            ),
            "unmeasured_method_differences": unmeasured,
            "measured_support_comparisons": measured_comparisons,
            "unavailable_manifests": unavailable,
            "notes": [
                "A support ratio inside the gate tolerance is not a validated method "
                "equivalence and is never converted into a correction factor.",
                "No percentage similarity, physical equivalence or global coverage claim "
                "is produced from the pilot cells.",
                "Missing support or missing values are excluded; they are never zero-filled.",
            ],
        },
        "ranked_candidates": [],
        "counts": {
            "evidence_sources": len(body_payloads),
            "scanned_metric_observations": scanned,
            "eligible_metric_observations": eligible_count,
            "excluded_metric_observations": excluded,
            "unknown_metric_observations": unknown,
            "ranked_candidates": 0,
        },
        "note": (
            "Cross-body coarse ranking is refused. No metric currently satisfies the same "
            "quantity/method/support/unit/frame/validation contract, so no cross-body score "
            "is emitted. The response reports bounded pilot evidence only and is not a "
            "global or exhaustive survey."
        ),
    }


def build_cross_body_gate(
    index: Mapping[str, Any],
    manifests: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Convenience entry point for already-loaded committed manifests."""
    return evaluate_cross_body_gate(
        earth=earth_coarse_baseline(),
        bodies=bodies_from_planetary_index(index, manifests),
    )


ELIGIBILITY_CONTRACT: tuple[dict[str, str], ...] = (
    {
        "criterion": "quantity",
        "requirement": (
            "the feature id must be the same coarse quantity present in the Earth baseline"
        ),
        "failure_code": "quantity_mismatch",
    },
    {
        "criterion": "method",
        "requirement": (
            "the producing method version must equal the Earth coarse method version; a DEM "
            "derivative is not interchangeable with a published preprocessed slope product"
        ),
        "failure_code": "method_mismatch",
    },
    {
        "criterion": "support",
        "requirement": (
            "the native support must not be coarser than the Earth coarse support beyond the "
            "explicit tolerance; a finer source may be aggregated to the common analysis grid, "
            "but a coarser source is never upsampled into same-support evidence"
        ),
        "failure_code": "support_mismatch",
    },
    {
        "criterion": "units",
        "requirement": "the unit must equal the Earth feature unit; no silent conversion",
        "failure_code": "unit_mismatch",
    },
    {
        "criterion": "frame",
        "requirement": (
            "both records must use a projected metric body-fixed frame class; Earth WGS84 or "
            "geographic-degree frames are never mixed with planetary body-fixed frames"
        ),
        "failure_code": "frame_mismatch",
    },
    {
        "criterion": "validation",
        "requirement": (
            "an explicit comparable decision is required; evidence_only, not_comparable and "
            "not_supported never rank"
        ),
        "failure_code": "decision_evidence_only",
    },
)


def _candidate_reasons(
    candidate: RankableCandidate,
    earth: EarthBaseline,
    eligible_features: Sequence[str],
    weights: Mapping[str, float],
) -> list[dict[str, str]]:
    reasons: list[dict[str, str]] = []
    if candidate.method_version != earth.method_version:
        reasons.append(
            _reason(
                "method_mismatch",
                f"{candidate.method_version!r} != {earth.method_version!r}",
            )
        )
    support_m = _finite_or_none(candidate.support_m)
    if support_m is None or support_m <= 0:
        reasons.append(_reason("support_missing"))
    elif support_m > earth.support_m * (1.0 + SUPPORT_TOLERANCE_RELATIVE):
        reasons.append(
            _reason(
                "support_mismatch",
                f"native {support_m:.3f} m vs Earth {earth.support_m:.3f} m",
            )
        )
    if candidate.frame_kind != earth.frame_kind:
        reasons.append(
            _reason("frame_mismatch", f"{candidate.frame_kind!r} != {earth.frame_kind!r}")
        )
    for feature_id in eligible_features:
        if weights.get(feature_id, 1.0) <= 0:
            continue
        value = _finite_or_none(candidate.values.get(feature_id))
        if value is None:
            reasons.append(_reason("missing_value", feature_id))
            continue
        feature = COARSE_FEATURE_BY_ID[feature_id]
        if not feature.min_value <= value <= feature.max_value:
            reasons.append(_reason("value_out_of_domain", feature_id))
    reasons.sort(key=_reason_sort_key)
    return reasons


def rank_comparable_candidates(
    *,
    earth: EarthBaseline,
    eligible_features: Sequence[str],
    target: Mapping[str, float],
    weights: Mapping[str, float],
    candidates: Sequence[RankableCandidate],
    limit: int = 10,
) -> dict[str, Any]:
    """Rank only an explicitly eligible metric subset; refuse everything else.

    This helper is tested with synthetic same-method fixtures but is not wired to the API,
    because the committed planetary pilots contain no eligible metric. It never accepts an
    Earth preprocessed-slope feature and a DEM-derived planetary feature in the same call:
    ``eligible_features`` must come from a gate evaluation that established method equality.
    """
    eligible = sorted(set(eligible_features))
    if not eligible:
        raise CrossBodyGateError(
            "no_eligible_metric",
            "cross-body ranking refused: no metric passed the eligibility gate",
        )
    unknown_eligible = sorted(set(eligible) - set(COARSE_FEATURE_BY_ID))
    if unknown_eligible:
        raise CrossBodyGateError(
            "unknown_feature", f"unknown eligible features: {unknown_eligible}"
        )
    if not target:
        raise CrossBodyGateError("empty_target", "at least one target feature is required")
    ineligible_target = sorted(set(target) - set(eligible))
    if ineligible_target:
        raise CrossBodyGateError(
            "ineligible_target",
            f"target features are not eligible for cross-body ranking: {ineligible_target}",
        )
    if not 1 <= limit <= 10:
        raise CrossBodyGateError("invalid_limit", "limit must be within 1..10")

    positive: dict[str, float] = {}
    for feature_id in target:
        weight = float(weights.get(feature_id, 1.0))
        if not math.isfinite(weight) or weight < 0:
            raise CrossBodyGateError("invalid_weight", "weights must be finite and >= 0")
        if weight > 0:
            positive[feature_id] = weight
    if not positive:
        raise CrossBodyGateError("zero_total_weight", "at least one positive weight is required")
    total_weight = sum(positive.values())

    results: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for candidate in sorted(candidates, key=lambda item: (item.body, item.candidate_id)):
        reasons = _candidate_reasons(candidate, earth, eligible, positive)
        if reasons:
            excluded.append(
                {
                    "candidate_id": candidate.candidate_id,
                    "body": candidate.body,
                    "reasons": reasons,
                }
            )
            continue
        squared = 0.0
        for feature_id, weight in positive.items():
            feature = COARSE_FEATURE_BY_ID[feature_id]
            value = float(candidate.values[feature_id])  # type: ignore[arg-type]
            squared += weight * ((value - float(target[feature_id])) / feature.scale) ** 2
        results.append(
            {
                "candidate_id": candidate.candidate_id,
                "body": candidate.body,
                "distance": math.sqrt(squared / total_weight),
            }
        )

    results.sort(key=lambda item: (item["distance"], item["body"], item["candidate_id"]))
    for rank, item in enumerate(results, start=1):
        item["rank"] = rank
    return {
        "ranked": bool(results),
        "eligible_features": eligible,
        "results": results[:limit],
        "excluded": sorted(excluded, key=lambda item: item["candidate_id"]),
        "counts": {
            "scanned_candidates": len(candidates),
            "eligible_candidates": len(results),
            "excluded_candidates": len(excluded),
            "returned_candidates": min(len(results), limit),
        },
        "note": (
            "Ranking is limited to the explicitly eligible feature subset. Unsupported "
            "metrics and incompatible methods are excluded, never scored."
        ),
    }
