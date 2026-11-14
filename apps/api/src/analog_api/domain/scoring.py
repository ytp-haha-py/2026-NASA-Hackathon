from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from analog_api.domain.features import FEATURE_BY_ID, FeatureDefinition

MAX_LIMIT = 10


class ScoringError(ValueError):
    def __init__(self, code: str, message: str, details: list[dict[str, object]] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or []


@dataclass(frozen=True, slots=True)
class ScoringCandidate:
    location_id: str
    values: Mapping[str, float]


@dataclass(frozen=True, slots=True)
class MetricComparison:
    feature_id: str
    unit: str
    target: float
    candidate: float
    delta: float
    scale: float
    weight: float
    normalized_delta: float
    contribution: float


@dataclass(frozen=True, slots=True)
class Exclusion:
    location_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class RankedMatch:
    location_id: str
    distance: float
    metrics: tuple[MetricComparison, ...]


@dataclass(frozen=True, slots=True)
class RankingOutcome:
    matches: list[RankedMatch]
    excluded: list[Exclusion]
    eligible_count: int = field(default=0)


def normalize_target(
    target: Mapping[str, float], weights: Mapping[str, float]
) -> tuple[dict[str, float], dict[str, float], float]:
    """Validate target/weights and return finite values with a positive total weight."""
    if not target:
        raise ScoringError("empty_target", "At least one target feature is required.")

    unknown = sorted(set(target) - set(FEATURE_BY_ID))
    if unknown:
        raise ScoringError(
            "unknown_feature",
            f"Unsupported feature id(s): {', '.join(unknown)}.",
            [
                {"feature_id": feature_id, "reason": "unsupported_dimension"}
                for feature_id in unknown
            ],
        )
    unknown_weights = sorted(set(weights) - set(target))
    if unknown_weights:
        raise ScoringError(
            "unknown_weight_feature",
            "Weights were supplied for features absent from the target: "
            + ", ".join(unknown_weights)
            + ".",
        )

    clean_target: dict[str, float] = {}
    for feature_id, value in target.items():
        definition = FEATURE_BY_ID[feature_id]
        if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            raise ScoringError(
                "invalid_target_value",
                f"Target for {feature_id} must be a finite number.",
                [{"feature_id": feature_id, "reason": "not_finite"}],
            )
        numeric = float(value)
        if not (definition.min_value <= numeric <= definition.max_value):
            raise ScoringError(
                "target_out_of_domain",
                f"Target for {feature_id} must be within "
                f"[{definition.min_value}, {definition.max_value}] {definition.unit}.",
                [
                    {
                        "feature_id": feature_id,
                        "reason": "out_of_domain",
                        "min": definition.min_value,
                        "max": definition.max_value,
                    }
                ],
            )
        clean_target[feature_id] = numeric

    clean_weights: dict[str, float] = {}
    for feature_id in clean_target:
        weight = float(weights.get(feature_id, 1.0))
        if not math.isfinite(weight) or weight < 0:
            raise ScoringError(
                "invalid_weight",
                f"Weight for {feature_id} must be a finite number >= 0.",
                [{"feature_id": feature_id, "reason": "invalid_weight"}],
            )
        clean_weights[feature_id] = weight

    total = sum(clean_weights.values())
    if total <= 0:
        raise ScoringError(
            "zero_total_weight", "At least one target feature must have a positive weight."
        )
    return clean_target, clean_weights, total


def rank_candidates(
    *,
    target: Mapping[str, float],
    weights: Mapping[str, float] | None = None,
    candidates: Sequence[ScoringCandidate],
    limit: int = MAX_LIMIT,
) -> RankingOutcome:
    """Pure weighted-normalized-Euclidean ranking.

    Lower distance is more similar. All scales are fixed registry constants, never derived
    from the current candidate subset.
    """
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= MAX_LIMIT:
        raise ScoringError("invalid_limit", f"limit must be an integer between 1 and {MAX_LIMIT}.")
    clean_target, clean_weights, total_weight = normalize_target(target, weights or {})

    eligible: list[RankedMatch] = []
    excluded: list[Exclusion] = []
    for candidate in candidates:
        missing = [
            feature_id
            for feature_id in clean_target
            if clean_weights[feature_id] > 0
            and (
                feature_id not in candidate.values
                or candidate.values[feature_id] is None
                or not math.isfinite(float(candidate.values[feature_id]))
            )
        ]
        if missing:
            reasons = ",".join(f"missing_metric:{feature_id}" for feature_id in missing)
            excluded.append(Exclusion(location_id=candidate.location_id, reason=reasons))
            continue

        comparisons: list[MetricComparison] = []
        total_squared = 0.0
        for feature_id, target_value in clean_target.items():
            if (
                feature_id not in candidate.values
                or candidate.values[feature_id] is None
                or not math.isfinite(float(candidate.values[feature_id]))
            ):
                # Only zero-weight dimensions may be absent from a result's breakdown.
                continue
            definition: FeatureDefinition = FEATURE_BY_ID[feature_id]
            candidate_value = float(candidate.values[feature_id])
            delta = candidate_value - target_value
            normalized = delta / definition.scale
            contribution = clean_weights[feature_id] * normalized**2 / total_weight
            total_squared += clean_weights[feature_id] * normalized**2
            comparisons.append(
                MetricComparison(
                    feature_id=feature_id,
                    unit=definition.unit,
                    target=target_value,
                    candidate=candidate_value,
                    delta=delta,
                    scale=definition.scale,
                    weight=clean_weights[feature_id],
                    normalized_delta=normalized,
                    contribution=contribution,
                )
            )
        distance = math.sqrt(total_squared / total_weight)
        eligible.append(
            RankedMatch(
                location_id=candidate.location_id,
                distance=distance,
                metrics=tuple(comparisons),
            )
        )

    eligible.sort(key=lambda match: (match.distance, match.location_id))
    return RankingOutcome(
        matches=eligible[:limit],
        excluded=excluded,
        eligible_count=len(eligible),
    )
