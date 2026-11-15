"""Deterministic multi-dimensional ranking over gated D01-D06 features (Issue #116).

This is a query-time ranking, not a six-dimensional scientific total. Only features explicitly
requested by the caller and registered with a supported ``similarity`` role participate. Features
without a common body/method/selector/support remain evidence or filters and never become a
fabricated zero.
"""

from __future__ import annotations

import math
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
DISTANCE_SCOPE = (
    "Weighted-normalised query distance over the explicitly requested and gated features. "
    "It is not a six-dimension scientific total, not a similarity percentage, and not a "
    "cross-body equivalence."
)


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


def resolve_targets(
    *,
    raw_targets: Mapping[str, tuple[float, float]],
    registry: Mapping[str, FeatureEligibility],
    candidate_body: str,
) -> list[MultiDimTarget]:
    """Validate requested targets against the registry, body scope and domains."""
    if not raw_targets:
        raise MultiDimError("empty_target", "At least one target feature is required.")

    unknown = sorted(set(raw_targets) - set(registry))
    if unknown:
        raise MultiDimError(
            "unknown_feature",
            f"Unsupported feature id(s): {', '.join(unknown)}.",
            [{"feature_id": item, "reason": "not_in_registry"} for item in unknown],
        )

    resolved: list[MultiDimTarget] = []
    for feature_id, (value, weight) in raw_targets.items():
        entry = registry[feature_id]
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
        resolved.append(MultiDimTarget(feature_id, numeric, numeric_weight))

    if not any(target.weight > 0 for target in resolved):
        raise MultiDimError(
            "zero_total_weight", "At least one target feature must have a positive weight."
        )
    return resolved


def rank_candidates(
    *,
    targets: Sequence[MultiDimTarget],
    candidates: Sequence[MultiDimCandidate],
    registry: Mapping[str, FeatureEligibility],
) -> MultiDimOutcome:
    """Rank candidates using only supported, gated features.

    A positive-weight feature that cannot be evaluated excludes the candidate with an explicit
    reason. A zero-weight feature is never compared and never excludes a candidate.
    """
    outcome = MultiDimOutcome()
    total_weight = sum(target.weight for target in targets)

    for candidate in candidates:
        reasons: list[str] = []
        missing: list[str] = []
        incompatible: list[str] = []
        comparisons: list[tuple[MultiDimTarget, FeatureEligibility, MaterializedFeature]] = []

        for target in targets:
            entry = registry[target.feature_id]
            materialized = candidate.features.get(target.feature_id)
            if materialized is None:
                if target.weight > 0:
                    missing.append(target.feature_id)
                    reasons.append(f"missing_feature:{target.feature_id}")
                else:
                    outcome.zero_weight_missing_ok += 1
                continue
            if candidate.body not in entry.body_scope or materialized.body != candidate.body:
                if target.weight > 0:
                    incompatible.append(target.feature_id)
                    reasons.append(f"body_mismatch:{target.feature_id}")
                continue
            if materialized.unit != entry.unit:
                if target.weight > 0:
                    incompatible.append(target.feature_id)
                    reasons.append(f"unit_mismatch:{target.feature_id}")
                continue
            if materialized.method_version not in entry.method_versions:
                if target.weight > 0:
                    incompatible.append(target.feature_id)
                    reasons.append(f"method_version_mismatch:{target.feature_id}")
                continue
            if not math.isfinite(materialized.value):
                if target.weight > 0:
                    incompatible.append(target.feature_id)
                    reasons.append(f"value_not_finite:{target.feature_id}")
                continue
            comparisons.append((target, entry, materialized))

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
        dimension_contributions: dict[str, float] = {}
        total_squared = 0.0
        for target, entry, materialized in comparisons:
            if target.weight == 0:
                # Present but deliberately not compared. It must not appear as a contribution.
                continue
            scale = entry.scale
            if scale is None or scale <= 0:
                outcome.excluded.append(
                    MultiDimExclusion(
                        location_id=candidate.location_id,
                        reasons=(f"invalid_registry_scale:{target.feature_id}",),
                        incompatible_features=(target.feature_id,),
                    )
                )
                contributions = []
                break
            delta = materialized.value - target.value
            normalized = delta / scale
            contribution = target.weight * normalized**2 / total_weight
            total_squared += target.weight * normalized**2
            dimension_contributions[entry.dimension_id] = (
                dimension_contributions.get(entry.dimension_id, 0.0) + contribution
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
                    scale=scale,
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
        if not contributions:
            continue
        # A zero-weight-only query is rejected during validation; guard the division anyway.
        distance = math.sqrt(total_squared / total_weight)
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
                dimension_contributions=dimension_contributions,
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
) -> dict[str, object]:
    """Report actual requested-feature coverage, not a six-dimension completeness claim."""
    per_feature: dict[str, dict[str, object]] = {}
    for target in targets:
        entry = registry[target.feature_id]
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
            "comparison": "active" if target.weight > 0 else "zero_weight_not_compared",
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
