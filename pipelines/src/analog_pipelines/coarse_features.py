"""Versioned 250 m-derived coarse terrain features (Issue #31).

These features are deliberately separate from the existing 90 m
`terrain-metrics-1.0.0` / `flat-terrain-fraction-1.0.0` definitions. They may be
used to generate a coarse shortlist, but are never recorded under the fine feature
IDs and are not interchangeable with the fine metrics.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np

COARSE_METHOD_VERSION = "coarse-terrain-250m-1.0.0"
COARSE_GRID_VERSION = "coarse-grid-1.0.0"
COARSE_WINDOW_M = 5000
SOURCE_RESOLUTION_M = 250.0
MIN_COARSE_VALID_FRACTION = 0.8
FLAT_THRESHOLD_DEG = 5.0
FINE_METHOD_VERSIONS = {"terrain-metrics-1.0.0", "flat-terrain-fraction-1.0.0"}


class CoarseExtractionError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class ComparabilityError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class CoarseFeatureDefinition:
    feature_id: str
    label: str
    unit: str
    method_version: str
    min_value: float
    max_value: float
    scale: float
    spatial_support: str
    description: str
    score_role: str = "similarity"

    def to_payload(self) -> dict[str, object]:
        return {
            "feature_id": self.feature_id,
            "label": self.label,
            "unit": self.unit,
            "method_version": self.method_version,
            "min_value": self.min_value,
            "max_value": self.max_value,
            "scale": self.scale,
            "spatial_support": self.spatial_support,
            "description": self.description,
            "score_role": self.score_role,
            "dimension_code": "D01",
            "source_resolution_m": SOURCE_RESOLUTION_M,
            "grid_window_m": COARSE_WINDOW_M,
        }


COARSE_FEATURES: tuple[CoarseFeatureDefinition, ...] = (
    CoarseFeatureDefinition(
        feature_id="coarse_slope_mean_deg",
        label="Coarse mean slope (250 m source)",
        unit="degree",
        method_version=COARSE_METHOD_VERSION,
        min_value=0.0,
        max_value=90.0,
        scale=10.0,
        spatial_support="5 km candidate window aggregated from 7.5 arc-second (~250 m) slope pixels",
        description="Mean of valid Geomorpho250 slope pixels within the 5 km window.",
    ),
    CoarseFeatureDefinition(
        feature_id="coarse_slope_p90_deg",
        label="Coarse 90th percentile slope",
        unit="degree",
        method_version=COARSE_METHOD_VERSION,
        min_value=0.0,
        max_value=90.0,
        scale=15.0,
        spatial_support="5 km candidate window aggregated from ~250 m slope pixels",
        description="Linear 90th percentile of valid Geomorpho250 slope pixels.",
    ),
    CoarseFeatureDefinition(
        feature_id="coarse_flat_fraction_lt5deg",
        label="Coarse flat fraction (<5°)",
        unit="fraction",
        method_version=COARSE_METHOD_VERSION,
        min_value=0.0,
        max_value=1.0,
        scale=0.25,
        spatial_support="5 km candidate window aggregated from ~250 m slope pixels",
        description="Fraction of valid slope pixels strictly below 5 degrees.",
    ),
)

COARSE_FEATURE_BY_ID = {feature.feature_id: feature for feature in COARSE_FEATURES}


def coarse_feature_payloads() -> list[dict[str, object]]:
    return [feature.to_payload() for feature in COARSE_FEATURES]


def assert_comparable(*, coarse_method_version: str, fine_method_version: str) -> None:
    """Reject coarse/fine comparisons; no implicit resampling equivalence exists."""
    if (
        fine_method_version in FINE_METHOD_VERSIONS
        or coarse_method_version == COARSE_METHOD_VERSION
    ):
        raise ComparabilityError(
            "Coarse 250 m-derived features and fine 90 m terrain features are not "
            "interchangeable; use a coarse shortlist followed by explicit 90 m refinement."
        )


def extract_coarse_features(
    slope_deg: np.ndarray,
    valid_mask: np.ndarray | None = None,
    *,
    min_valid_fraction: float = MIN_COARSE_VALID_FRACTION,
) -> dict[str, float]:
    """Aggregate a local slope raster window into coarse features.

    `slope_deg` must already be converted to degrees using verified source scale/
    offset and metadata. Missing/nodata cells must be excluded by `valid_mask` or
    be non-finite. No value is filled with zero.
    """
    values = np.asarray(slope_deg, dtype="float64")
    if values.ndim != 2 or values.size == 0:
        raise CoarseExtractionError("invalid_array", "slope array must be a non-empty 2D array")
    if valid_mask is None:
        valid = np.isfinite(values)
    else:
        valid = np.asarray(valid_mask, dtype=bool) & np.isfinite(values)
    valid_fraction = float(valid.sum()) / float(values.size)
    if valid_fraction < min_valid_fraction:
        raise CoarseExtractionError(
            "insufficient_valid_data",
            f"valid fraction {valid_fraction:.3f} < {min_valid_fraction:.3f}",
        )
    sample = values[valid]
    if np.any(sample < 0) or np.any(sample > 90):
        raise CoarseExtractionError(
            "invalid_slope_domain", "slope degrees outside [0, 90]; check source units/scale"
        )
    return {
        "coarse_slope_mean_deg": float(np.mean(sample)),
        "coarse_slope_p90_deg": float(np.percentile(sample, 90)),
        "coarse_flat_fraction_lt5deg": float(np.mean(sample < FLAT_THRESHOLD_DEG)),
    }


def coarse_similarity_breakdown(
    candidate: Mapping[str, float],
    target: Mapping[str, float],
    weights: Mapping[str, float],
) -> tuple[float, list[dict[str, float | str]]]:
    """Weighted normalized distance using only the coarse registry scales."""
    if not target:
        raise CoarseExtractionError("empty_target", "at least one coarse target is required")
    if set(target) - set(COARSE_FEATURE_BY_ID):
        raise CoarseExtractionError("unknown_feature", "target contains an unknown coarse feature")
    total = 0.0
    breakdown: list[dict[str, float | str]] = []
    for feature_id in target:
        if feature_id not in candidate:
            raise CoarseExtractionError("missing_metric", f"candidate missing {feature_id}")
        weight = float(weights.get(feature_id, 1.0))
        if not math.isfinite(weight) or weight < 0:
            raise CoarseExtractionError("invalid_weight", "weights must be finite and >= 0")
        total += weight
    if total <= 0:
        raise CoarseExtractionError("zero_total_weight", "at least one positive weight is required")
    squared = 0.0
    for feature_id, target_value in target.items():
        definition = COARSE_FEATURE_BY_ID[feature_id]
        delta = float(candidate[feature_id]) - float(target_value)
        weight = float(weights.get(feature_id, 1.0))
        normalized = delta / definition.scale
        contribution = weight * normalized**2 / total
        squared += weight * normalized**2
        breakdown.append(
            {
                "feature_id": feature_id,
                "target": float(target_value),
                "candidate": float(candidate[feature_id]),
                "delta": delta,
                "scale": definition.scale,
                "weight": weight,
                "normalized_delta": normalized,
                "contribution": contribution,
            }
        )
    return math.sqrt(squared / total), breakdown
