"""Deterministic multi-dimensional ranking over gated D01-D06 features (Issue #116/#141).

This is a query-time ranking, not a six-dimensional scientific total. Only features explicitly
requested by the caller and registered with a supported ``similarity`` role participate. Features
without a common body/method/selector/support remain evidence or filters and never become a
fabricated zero.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from analog_api.domain.eligibility import (
    REGISTRY_VERSION,
    ROLE_SIMILARITY,
    FeatureEligibility,
    MaterializedFeature,
)

MAX_MULTIDIM_LIMIT = 50

SCORING_VERSION_LEGACY = "feature-weighted-euclidean-v1"
SCORING_VERSION_DIMENSION_RMS = "dimension-weighted-rms-v1"

DISTANCE_SCOPE_LEGACY = (
    "Weighted-normalised query distance over the explicitly requested and gated features "
    f"({SCORING_VERSION_LEGACY}: flat feature-weighted Euclidean). It is not a six-dimension "
    "scientific total, not a similarity percentage, and not a cross-body equivalence."
)
DISTANCE_SCOPE_DIMENSION_RMS = (
    "Weighted-normalised query distance over the explicitly requested and gated features "
    f"({SCORING_VERSION_DIMENSION_RMS}: per-dimension feature-weighted mean of squared "
    "normalised deltas, then cross-dimension weighted RMS). It is not a six-dimension "
    "scientific total, not a similarity percentage, and not a cross-body equivalence."
)
# Backward-compatible name used by existing response builders/imports.
DISTANCE_SCOPE = DISTANCE_SCOPE_LEGACY


class MultiDimError(ValueError):
    def __init__(self, code: str, message: str, details: list[dict[str, object]] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or []


@dataclass(frozen=True, slots=True)
class MultiDimTarget:
    feature_id: str
    value: float
    weight: float


@dataclass(frozen=True, slots=True)
class DimensionControl:
    weight: float = 1.0
    enabled: bool = True


@dataclass(frozen=True, slots=True)
class MultiDimCandidate:
    location_id: str
    body: str
    features: Mapping[str, MaterializedFeature]


@dataclass(frozen=True, slots=True)
class Contribution:
    feature_id: str
    dimension_id: str
    label: str
    unit: str
    target: float
    candidate: float
    delta: float
    scale: float
    weight: float
    normalized_delta: float
    contribution: float
    contribution_fraction: float
    measurement_family: str | None
    observation_id: str
    selector: dict[str, Any]
    support: dict[str, Any]
    uncertainty: dict[str, Any] | None


@dataclass(frozen=True, slots=True)
class RankedMultiDimMatch:
    location_id: str
    body: str
    distance: float
    contributions: tuple[Contribution, ...]
    dimension_contributions: dict[str, float]
    dimension_scores: dict[str, dict[str, float]] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class MultiDimExclusion:
    location_id: str
    reasons: tuple[str, ...]
    missing_features: tuple[str, ...] = ()
    incompatible_features: tuple[str, ...] = ()


@dataclass(slots=True)
class MultiDimOutcome:
    matches: list[RankedMultiDimMatch] = field(default_factory=list)
    excluded: list[MultiDimExclusion] = field(default_factory=list)
    eligible_count: int = 0
    zero_weight_missing_ok: int = 0
    scoring_version: str = SCORING_VERSION_LEGACY
    distance_scope: str = DISTANCE_SCOPE_LEGACY
    active_dimensions: tuple[str, ...] = ()


def _dimension_control(
    dimension_id: str, controls: Mapping[str, DimensionControl] | None
) -> DimensionControl:
    if controls is None:
        return DimensionControl()
    control = controls.get(dimension_id)
    return control if control is not None else DimensionControl()


def resolve_targets(
    *,
    raw_targets: Mapping[str, tuple[float, float]],
    registry: Mapping[str, FeatureEligibility],
    candidate_body: str,
    dimension_weights: Mapping[str, DimensionControl] | None = None,
    scoring_version: str | None = None,
) -> tuple[list[MultiDimTarget], str]:
    """Validate requested targets against the registry, body scope and domains.

    Returns ``(resolved_targets, effective_scoring_version)``. Dimension controls are opt-in:
    absent controls preserve the legacy flat feature-weighted Euclidean distance.
    """
    if not raw_targets:
        raise MultiDimError("empty_target", "At least one target feature is required.")

    unknown = sorted(set(raw_targets) - set(registry))
    if unknown:
        raise MultiDimError(
            "unknown_feature",
            f"Unsupported feature id(s): {', '.join(unknown)}.",
            [{"feature_id": item, "reason": "not_in_registry"} for item in unknown],
        )

    if scoring_version is not None and scoring_version not in {
        SCORING_VERSION_LEGACY,
        SCORING_VERSION_DIMENSION_RMS,
    }:
        raise MultiDimError(
            "invalid_scoring_version",
            f"Unsupported scoring_version: {scoring_version}.",
            [{"scoring_version": scoring_version}],
        )

    requested_dimensions = {
        registry[feature_id].dimension_id: _dimension_control(
            registry[feature_id].dimension_id, dimension_weights
        )
        for feature_id in raw_targets
    }
    has_dimension_controls = bool(dimension_weights)
    effective_scoring = scoring_version or (
        SCORING_VERSION_DIMENSION_RMS if has_dimension_controls else SCORING_VERSION_LEGACY
    )
    if scoring_version == SCORING_VERSION_LEGACY and has_dimension_controls:
        # dimension_weights are only meaningful with the dimension-weighted formula.
        effective_scoring = SCORING_VERSION_DIMENSION_RMS

    resolved: list[MultiDimTarget] = []
    active_dimension_weights: dict[str, float] = {}
    for feature_id, (value, weight) in raw_targets.items():
        entry = registry[feature_id]
        dim_control = _dimension_control(entry.dimension_id, dimension_weights)
        dimension_enabled = dim_control.enabled and dim_control.weight > 0
        role = entry.roles[ROLE_SIMILARITY]
        if not role.supported:
            raise MultiDimError(
                "feature_role_not_scoreable",
                f"{feature_id} is not registered for weighted similarity. "
                f"{role.reason or 'It remains evidence only.'}",
                [
                    {
                        "feature_id": feature_id,
                        "dimension_id": entry.dimension_id,
                        "score_role": entry.score_role,
                        "reason": role.reason or "evidence_only",
                        "legal_roles": [
                            name for name, support in entry.roles.items() if support.supported
                        ],
                    }
                ],
            )
        if candidate_body not in entry.body_scope:
            raise MultiDimError(
                "body_out_of_scope",
                f"{feature_id} is registered for {', '.join(entry.body_scope)} only; "
                f"target/source body {candidate_body} is incompatible.",
                [
                    {
                        "feature_id": feature_id,
                        "target_body": candidate_body,
                        "body_scope": list(entry.body_scope),
                        "reason": "cross_body_incompatible",
                    }
                ],
            )
        numeric = float(value)
        numeric_weight = float(weight)
        if not math.isfinite(numeric):
            raise MultiDimError(
                "invalid_target_value",
                f"Target for {feature_id} must be finite.",
                [{"feature_id": feature_id, "reason": "not_finite"}],
            )
        if entry.min_value is not None and numeric < entry.min_value:
            raise MultiDimError(
                "target_out_of_domain",
                f"Target for {feature_id} is below {entry.min_value} {entry.unit}.",
                [{"feature_id": feature_id, "reason": "below_domain", "min": entry.min_value}],
            )
        if entry.max_value is not None and numeric > entry.max_value:
            raise MultiDimError(
                "target_out_of_domain",
                f"Target for {feature_id} is above {entry.max_value} {entry.unit}.",
                [{"feature_id": feature_id, "reason": "above_domain", "max": entry.max_value}],
            )
        if not math.isfinite(numeric_weight) or numeric_weight < 0:
            raise MultiDimError(
                "invalid_weight",
                f"Weight for {feature_id} must be finite and >= 0.",
                [{"feature_id": feature_id, "reason": "invalid_weight"}],
            )
        if numeric_weight > 0 and dimension_enabled:
            active_dimension_weights[entry.dimension_id] = dim_control.weight
        resolved.append(MultiDimTarget(feature_id, numeric, numeric_weight))

    if not any(target.weight > 0 for target in resolved):
        raise MultiDimError(
            "zero_total_weight", "At least one target feature must have a positive weight."
        )
    if effective_scoring == SCORING_VERSION_DIMENSION_RMS:
        # Every requested dimension is either disabled/weight-0 (not scored) or contributes.
        any_active = any(
            control.enabled and control.weight > 0 for control in requested_dimensions.values()
        )
        if not any_active:
            raise MultiDimError(
                "zero_total_dimension_weight",
                "At least one enabled dimension must have a positive weight.",
                [
                    {
                        "dimension_id": dimension_id,
                        "weight": control.weight,
                        "enabled": control.enabled,
                    }
                    for dimension_id, control in sorted(requested_dimensions.items())
                ],
            )
        if not active_dimension_weights:
            # All features in active dimensions have zero weight.
            raise MultiDimError(
                "zero_total_weight",
                "At least one feature in an enabled dimension must have a positive weight.",
            )
    return resolved, effective_scoring


def _active_dimension(
    entry: FeatureEligibility,
    dimension_weights: Mapping[str, DimensionControl] | None,
) -> tuple[bool, float]:
    control = _dimension_control(entry.dimension_id, dimension_weights)
    return (control.enabled and control.weight > 0), control.weight


def rank_candidates(
    *,
    targets: Sequence[MultiDimTarget],
    candidates: Sequence[MultiDimCandidate],
    registry: Mapping[str, FeatureEligibility],
    dimension_weights: Mapping[str, DimensionControl] | None = None,
    scoring_version: str = SCORING_VERSION_LEGACY,
) -> MultiDimOutcome:
    """Rank candidates using only supported, gated features.

    A positive-weight feature in an enabled/positively-weighted dimension that cannot be
    evaluated excludes the candidate with an explicit reason. A zero-weight feature and a
    feature in a disabled/zero-weight dimension are never compared and never exclude candidates.
    """
    if scoring_version not in {SCORING_VERSION_LEGACY, SCORING_VERSION_DIMENSION_RMS}:
        raise MultiDimError(
            "invalid_scoring_version",
            f"Unsupported scoring_version: {scoring_version}.",
            [{"scoring_version": scoring_version}],
        )

    outcome = MultiDimOutcome(
        scoring_version=scoring_version,
        distance_scope=(
            DISTANCE_SCOPE_DIMENSION_RMS
            if scoring_version == SCORING_VERSION_DIMENSION_RMS
            else DISTANCE_SCOPE_LEGACY
        ),
    )

    # Determine active feature set per scoring mode.
    active_targets: list[tuple[MultiDimTarget, float]] = []
    active_dimensions: set[str] = set()
    for target in targets:
        entry = registry[target.feature_id]
        if scoring_version == SCORING_VERSION_DIMENSION_RMS:
            dim_active, dim_weight = _active_dimension(entry, dimension_weights)
            if not dim_active or target.weight <= 0:
                continue
            active_targets.append((target, dim_weight))
            active_dimensions.add(entry.dimension_id)
        else:
            if target.weight <= 0:
                continue
            active_targets.append((target, 1.0))
            active_dimensions.add(entry.dimension_id)
    outcome.active_dimensions = tuple(sorted(active_dimensions))
    total_flat_weight = sum(target.weight for target, _ in active_targets)

    for candidate in candidates:
        reasons: list[str] = []
        missing: list[str] = []
        incompatible: list[str] = []
        comparisons: list[
            tuple[MultiDimTarget, FeatureEligibility, MaterializedFeature, float]
        ] = []

        for target in targets:
            entry = registry[target.feature_id]
            dim_active, dim_weight = _active_dimension(entry, dimension_weights)
            is_compared = target.weight > 0 and (
                dim_active if scoring_version == SCORING_VERSION_DIMENSION_RMS else True
            )
            materialized = candidate.features.get(target.feature_id)
            if materialized is None:
                if is_compared:
                    missing.append(target.feature_id)
                    reasons.append(f"missing_feature:{target.feature_id}")
                else:
                    outcome.zero_weight_missing_ok += 1
                continue
            if candidate.body not in entry.body_scope or materialized.body != candidate.body:
                if is_compared:
                    incompatible.append(target.feature_id)
                    reasons.append(f"body_mismatch:{target.feature_id}")
                continue
            if materialized.unit != entry.unit:
                if is_compared:
                    incompatible.append(target.feature_id)
                    reasons.append(f"unit_mismatch:{target.feature_id}")
                continue
            if materialized.method_version not in entry.method_versions:
                if is_compared:
                    incompatible.append(target.feature_id)
                    reasons.append(f"method_version_mismatch:{target.feature_id}")
                continue
            if not math.isfinite(materialized.value):
                if is_compared:
                    incompatible.append(target.feature_id)
                    reasons.append(f"value_not_finite:{target.feature_id}")
                continue
            if is_compared:
                comparisons.append((target, entry, materialized, dim_weight))

        if reasons:
            outcome.excluded.append(
                MultiDimExclusion(
                    location_id=candidate.location_id,
                    reasons=tuple(reasons),
                    missing_features=tuple(missing),
                    incompatible_features=tuple(incompatible),
                )
            )
            continue

        contributions: list[Contribution] = []
        flat_dimension_contributions: dict[str, float] = {}
        dimension_scores: dict[str, dict[str, float]] = {}
        total_squared_flat = 0.0
        invalid_scale = False

        # Build raw per-feature normalized values.
        feature_terms: list[
            tuple[MultiDimTarget, FeatureEligibility, MaterializedFeature, float, float, float]
        ] = []
        for target, entry, materialized, dim_weight in comparisons:
            scale = entry.scale
            if scale is None or scale <= 0:
                outcome.excluded.append(
                    MultiDimExclusion(
                        location_id=candidate.location_id,
                        reasons=(f"invalid_registry_scale:{target.feature_id}",),
                        incompatible_features=(target.feature_id,),
                    )
                )
                invalid_scale = True
                break
            delta = materialized.value - target.value
            normalized = delta / scale
            feature_terms.append((target, entry, materialized, dim_weight, delta, normalized))

        if invalid_scale or not feature_terms:
            continue

        if scoring_version == SCORING_VERSION_DIMENSION_RMS:
            per_dim: dict[str, dict[str, float | list]] = defaultdict(
                lambda: {"v_sum": 0.0, "q_num": 0.0, "items": []}
            )
            for target, entry, materialized, dim_weight, delta, normalized in feature_terms:
                z2 = normalized**2
                bucket = per_dim[entry.dimension_id]
                bucket["v_sum"] = float(bucket["v_sum"]) + target.weight
                bucket["q_num"] = float(bucket["q_num"]) + target.weight * z2
                bucket["items"].append(
                    (target, entry, materialized, dim_weight, delta, normalized, z2)
                )

            dim_total_weight = 0.0
            distance_num = 0.0
            for dimension_id, bucket in sorted(per_dim.items()):
                v_sum = float(bucket["v_sum"])
                q_d = (float(bucket["q_num"]) / v_sum) if v_sum > 0 else 0.0
                dim_weight = _dimension_control(dimension_id, dimension_weights).weight
                dim_contribution = dim_weight * q_d
                dim_total_weight += dim_weight
                distance_num += dim_contribution
                dimension_scores[dimension_id] = {
                    "dimension_weight": dim_weight,
                    "feature_weight_sum": v_sum,
                    "mean_squared_normalized_delta": q_d,
                    "distance": math.sqrt(q_d),
                    "contribution": dim_contribution,
                    "feature_count": float(len(bucket["items"])),
                }

            distance = math.sqrt(distance_num / dim_total_weight) if dim_total_weight > 0 else 0.0
            # per-feature contribution = dim contribution share * within-dim feature share
            for dimension_id, bucket in sorted(per_dim.items()):
                v_sum = float(bucket["v_sum"])
                q_num = float(bucket["q_num"])
                q_d = (q_num / v_sum) if v_sum > 0 else 0.0
                dim_weight = _dimension_control(dimension_id, dimension_weights).weight
                dim_contribution = dim_weight * q_d
                for target, entry, materialized, _, delta, normalized, z2 in bucket["items"]:
                    within = (target.weight * z2 / q_num) if q_num > 0 else 0.0
                    contribution = dim_contribution * within
                    contributions.append(
                        Contribution(
                            feature_id=target.feature_id,
                            dimension_id=entry.dimension_id,
                            label=entry.label,
                            unit=entry.unit,
                            target=target.value,
                            candidate=materialized.value,
                            delta=delta,
                            scale=entry.scale or 1.0,
                            weight=target.weight,
                            normalized_delta=normalized,
                            contribution=contribution,
                            contribution_fraction=0.0,
                            measurement_family=entry.measurement_family,
                            observation_id=materialized.observation_id,
                            selector=materialized.selector,
                            support=materialized.support,
                            uncertainty=materialized.uncertainty,
                        )
                    )
            dim_contrib_map = {
                dim: values["contribution"] for dim, values in dimension_scores.items()
            }
        else:
            # Legacy flat feature-weighted Euclidean, preserving prior behavior.
            for target, entry, materialized, _, delta, normalized in feature_terms:
                contribution = target.weight * normalized**2 / total_flat_weight
                total_squared_flat += target.weight * normalized**2
                flat_dimension_contributions[entry.dimension_id] = (
                    flat_dimension_contributions.get(entry.dimension_id, 0.0) + contribution
                )
                contributions.append(
                    Contribution(
                        feature_id=target.feature_id,
                        dimension_id=entry.dimension_id,
                        label=entry.label,
                        unit=entry.unit,
                        target=target.value,
                        candidate=materialized.value,
                        delta=delta,
                        scale=entry.scale or 1.0,
                        weight=target.weight,
                        normalized_delta=normalized,
                        contribution=contribution,
                        contribution_fraction=0.0,
                        measurement_family=entry.measurement_family,
                        observation_id=materialized.observation_id,
                        selector=materialized.selector,
                        support=materialized.support,
                        uncertainty=materialized.uncertainty,
                    )
                )
            distance = math.sqrt(total_squared_flat / total_flat_weight)
            dim_contrib_map = flat_dimension_contributions
            for dimension_id, contribution in flat_dimension_contributions.items():
                dim_feature_count = sum(
                    1 for item in contributions if item.dimension_id == dimension_id
                )
                dimension_scores[dimension_id] = {
                    "dimension_weight": 1.0,
                    "feature_weight_sum": sum(
                        item.weight for item in contributions if item.dimension_id == dimension_id
                    ),
                    "mean_squared_normalized_delta": 0.0,
                    "distance": math.sqrt(contribution),
                    "contribution": contribution,
                    "feature_count": float(dim_feature_count),
                }

        squared_total = sum(item.contribution for item in contributions)
        final = tuple(
            Contribution(
                feature_id=item.feature_id,
                dimension_id=item.dimension_id,
                label=item.label,
                unit=item.unit,
                target=item.target,
                candidate=item.candidate,
                delta=item.delta,
                scale=item.scale,
                weight=item.weight,
                normalized_delta=item.normalized_delta,
                contribution=item.contribution,
                contribution_fraction=(
                    item.contribution / squared_total if squared_total > 0 else 0.0
                ),
                measurement_family=item.measurement_family,
                observation_id=item.observation_id,
                selector=item.selector,
                support=item.support,
                uncertainty=item.uncertainty,
            )
            for item in contributions
        )
        outcome.matches.append(
            RankedMultiDimMatch(
                location_id=candidate.location_id,
                body=candidate.body,
                distance=distance,
                contributions=final,
                dimension_contributions=dim_contrib_map,
                dimension_scores=dimension_scores,
            )
        )

    outcome.matches.sort(key=lambda match: (match.distance, match.location_id))
    outcome.eligible_count = len(outcome.matches)
    return outcome


def coverage_payload(
    *,
    targets: Sequence[MultiDimTarget],
    registry: Mapping[str, FeatureEligibility],
    candidates: Sequence[MultiDimCandidate],
    matches: Sequence[RankedMultiDimMatch],
    dimension_weights: Mapping[str, DimensionControl] | None = None,
) -> dict[str, object]:
    """Report actual requested-feature coverage, not a six-dimension completeness claim."""
    per_feature: dict[str, dict[str, object]] = {}
    for target in targets:
        entry = registry[target.feature_id]
        control = _dimension_control(entry.dimension_id, dimension_weights)
        if target.weight <= 0:
            comparison = "zero_weight_not_compared"
        elif not control.enabled:
            comparison = "dimension_disabled_not_compared"
        else:
            comparison = "active"
        present = sum(1 for candidate in candidates if target.feature_id in candidate.features)
        scored = sum(
            1
            for match in matches
            if any(item.feature_id == target.feature_id for item in match.contributions)
        )
        per_feature[target.feature_id] = {
            "dimension_id": entry.dimension_id,
            "label": entry.label,
            "unit": entry.unit,
            "body_scope": list(entry.body_scope),
            "weight": target.weight,
            "comparison": comparison,
            "eligible_candidate_count": present,
            "candidate_pool_size": len(candidates),
            "coverage_fraction": round(present / len(candidates), 6) if candidates else 0.0,
            "scored_result_count": scored,
            "selector": entry.selector,
            "support": entry.support,
        }
    return {
        "candidate_pool_size": len(candidates),
        "requested_feature_count": len(targets),
        "features": per_feature,
        "scope_note": (
            "Coverage counts exact-ID-joined, gate-passing values in this database. They are not "
            "counts of six-dimension observations and not an assertion of global coverage."
        ),
    }


def registry_version() -> str:
    return REGISTRY_VERSION
