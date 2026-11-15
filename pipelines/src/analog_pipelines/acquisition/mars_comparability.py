"""Mars terrain/thermal comparability gates (Issues #2/#3).

The gates are deliberately fail-closed. They encode the difference between a
validated same-support terrain comparison and metadata-only thermal evidence.
They do not contain or infer Mars observations.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

GateStatus = Literal["comparable", "evidence_only", "not_comparable", "unavailable"]

TERRAIN_METHOD_VERSION = "terrain-metrics-1.0.0"
THERMAL_METHOD_VERSION = "themis-thermal-inertia-1.0.0"

MOLA_MEGDR_16PPD_RESOLUTION_M = 3704.4613374
MOLA_MEGDR_128PPD_RESOLUTION_M = 463.0576672
FINE_ANALYSIS_RESOLUTION_M = 90.0


class MarsComparabilityError(ValueError):
    """Raised when a Mars product is used outside its documented comparison role."""


@dataclass(frozen=True, slots=True)
class ComparabilityDecision:
    status: GateStatus
    reasons: tuple[str, ...]
    blockers: tuple[str, ...] = ()

    @property
    def allows_similarity_score(self) -> bool:
        return self.status == "comparable" and not self.blockers

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "reasons": list(self.reasons),
            "blockers": list(self.blockers),
            "allows_similarity_score": self.allows_similarity_score,
        }


def assess_mola_megdr_terrain(
    *,
    native_resolution_m: float,
    analysis_resolution_m: float = FINE_ANALYSIS_RESOLUTION_M,
    method_version: str = TERRAIN_METHOD_VERSION,
    projected_metric_crs: bool = False,
    vertical_units_verified: bool = False,
    per_window_uncertainty_verified: bool = False,
    local_dem: bool = False,
) -> ComparabilityDecision:
    """Assess whether a MOLA MEGDR window can enter the terrain similarity score.

    MEGDR is a global median topography grid with interpolated no-observation
    bins. At 16 and 128 pixels/degree its native cells are about 3.70 km and
    0.463 km, far coarser than the 90 m fine analysis support.
    """
    reasons: list[str] = [
        "MOLA MEGDR is a global median topographic grid, not a local 1.98 km DEM",
    ]
    blockers: list[str] = []
    if native_resolution_m <= 0:
        raise MarsComparabilityError("native_resolution_m must be positive")
    if method_version != TERRAIN_METHOD_VERSION:
        blockers.append(f"method_version_{method_version}_is_not_{TERRAIN_METHOD_VERSION}")
    if native_resolution_m > FINE_ANALYSIS_RESOLUTION_M:
        blockers.append("native_resolution_coarser_than_90m_analysis_support")
    if analysis_resolution_m < native_resolution_m:
        blockers.append("analysis_grid_would_upsample_the_native_grid")
    if not projected_metric_crs:
        blockers.append("no_validated_mars_projected_metric_analysis_crs")
    if not vertical_units_verified:
        blockers.append("vertical_units_and_areoid_reference_not_verified_for_the_window")
    if not per_window_uncertainty_verified:
        blockers.append("no_per_window_vertical_uncertainty_verified")
    if not local_dem:
        blockers.append("source_is_not_a_local_dem_and_contains_interpolated_bins")
    return ComparabilityDecision(
        status="not_comparable" if blockers else "comparable",
        reasons=tuple(reasons),
        blockers=tuple(blockers),
    )


def assess_themis_thermal_inertia(
    *,
    native_resolution_m: float,
    has_per_pixel_uncertainty: bool = False,
    has_qa_layer: bool = False,
    has_local_time_or_season_support: bool = True,
    proposed_score_role: str = "evidence_only",
    coverage_note: str = "quantitative mosaic covers 60S to 60N only",
) -> ComparabilityDecision:
    """Assess the THEMIS-derived Mars thermal-inertia product.

    Apparent thermal inertia is D03 evidence. It is not surface temperature, it
    has no approved similarity feature ID, and it must not enter the terrain
    similarity distance.
    """
    if native_resolution_m <= 0:
        raise MarsComparabilityError("native_resolution_m must be positive")
    reasons = [
        "apparent thermal inertia is evidence about the upper few centimetres",
        coverage_note,
    ]
    blockers = ["no_approved_d03_thermal_inertia_similarity_feature"]
    if proposed_score_role != "evidence_only":
        blockers.append(f"proposed_score_role_{proposed_score_role}_is_not_evidence_only")
    if not has_per_pixel_uncertainty:
        blockers.append("no_per_pixel_uncertainty_layer")
    if not has_qa_layer:
        blockers.append("no_verified_per_pixel_qa_layer")
    if not has_local_time_or_season_support:
        blockers.append("retrieval_time_and_season_support_not_verified")
    status: GateStatus = (
        "not_comparable" if proposed_score_role != "evidence_only" else ("evidence_only")
    )
    return ComparabilityDecision(status=status, reasons=tuple(reasons), blockers=tuple(blockers))


def require_evidence_only(decision: ComparabilityDecision, score_role: str) -> None:
    """Fail closed if a caller tries to score a non-comparable Mars product."""
    if score_role == "similarity" and not decision.allows_similarity_score:
        raise MarsComparabilityError(
            "Mars product is not comparable for similarity: " + ", ".join(decision.blockers)
        )
    if decision.status == "evidence_only" and score_role not in {
        "evidence_only",
        "context_only",
    }:
        raise MarsComparabilityError(
            f"Mars evidence product requires evidence_only/context_only, got {score_role!r}"
        )
