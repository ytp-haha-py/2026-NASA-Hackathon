"""Explicit cross-body comparability gate for D01 and D06 (Issue #170, Goal G).

This module answers one question only: *may these two stored observations be compared
with each other at all?* It does not rank, score, average, or aggregate anything. A
positive decision means that the quantity id, producing method version, analysis
resolution, spatial support and value origin agree. It never means environmental
equivalence, a similarity percentage, or a six-dimension total.

The gate is deliberately narrow:

* **D01 terrain-shape** features are registered for Earth, Moon and Mars with the same
  ``terrain-metrics-1.0.0`` method, a 90 m analysis grid and a fixed
  ``1.98 km x 1.98 km`` window. Those five features are the only quantities this module
  treats as cross-body comparable.
* **D06 regolith** observations stored today are Earth-only SoilGrids 2.0 model
  predictions on 250 m cells joined to an AOI envelope. They are never comparable with
  a Moon or Mars sample, and they are never interchangeable with a 90 m D01 terrain
  window. The ``eligibility.py`` registry still owns the property/depth selector rules;
  this gate does not replace it.
* **250 m / 5 km planetary coarse** pilot cells (``coarse-planetary-dem-horn-1.0.0``,
  5 km cells) are rejected against the fine 90 m / 1.98 km query. They are not
  upsampled into fine evidence. Their evidence-only handling lives in
  ``pipelines/src/analog_pipelines/cross_body_gate.py``.

Missing, ``unknown`` and ``no_coverage`` values stay missing. Nothing here zero-fills,
interpolates, resamples, converts units, or joins by proximity. Every rejection carries
a machine-readable reason and both sides keep their provenance in the payload.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from analog_api.domain.eligibility import D06_METHOD_VERSION
from analog_api.domain.features import FEATURES, FeatureDefinition

GATE_VERSION = "cross-body-comparability-1.0.0"

SUPPORTED_BODIES: tuple[str, ...] = ("Earth", "Moon", "Mars")
CROSS_BODY_DIMENSIONS: tuple[str, ...] = ("D01", "D06")

DIMENSION_D01 = "D01"
DIMENSION_D06 = "D06"

FINE_ANALYSIS_RESOLUTION_M = 90.0
FINE_WINDOW_M = 1980.0
COARSE_ANALYSIS_RESOLUTION_M = 250.0
COARSE_CELL_M = 5000.0

RESOLUTION_CLASS_FINE = "fine_90m"
RESOLUTION_CLASS_COARSE = "coarse_250m_5km"
RESOLUTION_CLASS_UNKNOWN = "unknown"

SUPPORT_FIXED_WINDOW = "fixed_window"
SUPPORT_AREA_ENVELOPE_MEAN = "area_envelope_mean"
SUPPORT_COARSE_CELL = "coarse_cell"
SUPPORT_UNKNOWN = "unknown"

VALUE_ORIGIN_DEM_DERIVATIVE = "dem_derivative"
VALUE_ORIGIN_MODEL_PREDICTION = "model_prediction"

FRAME_PROJECTED_METRIC = "projected_metric_body_fixed"
FRAME_GEOGRAPHIC = "geographic"
FRAME_EARTH_WGS84 = "earth_wgs84"
FRAME_UNKNOWN = "unknown"

D06_NATIVE_RESOLUTION_M = 250.0
D06_UNITS = frozenset({"g kg-1", "dg kg-1", "cg cm-3"})
D06_UNCERTAINTY_STATISTICS = ("_q05", "_q95")

_RESOLUTION_TOLERANCE_M = 1.0e-9
_SUPPORT_TOLERANCE_M = 1.0e-6

SCOPE_NOTE = (
    "Comparable means the same feature id, registered method version, analysis resolution, "
    "spatial support and value origin. It is not environmental equivalence, not a similarity "
    "score, and not a six-dimension cross-body total. Coverage remains limited to the "
    "processed reference regions; a body-level contract does not imply body-wide coverage."
)

_REASON_ORDER: tuple[str, ...] = (
    "unknown_body",
    "unregistered_feature",
    "dimension_mismatch",
    "feature_mismatch",
    "body_out_of_scope",
    "role_not_comparable",
    "state_not_detected",
    "missing_value",
    "value_not_finite",
    "unit_mismatch",
    "unit_not_registered",
    "method_version_missing",
    "method_version_not_registered",
    "method_version_mismatch",
    "analysis_resolution_missing",
    "analysis_resolution_mismatch",
    "coarse_resolution_not_comparable",
    "support_kind_mismatch",
    "support_size_missing",
    "support_size_mismatch",
    "coarse_support_not_comparable",
    "value_origin_mismatch",
    "frame_unknown",
    "frame_not_projected_metric",
    "frame_kind_mismatch",
    "earth_frame_on_planetary_body",
)

_REASON_MESSAGES: dict[str, str] = {
    "unknown_body": (
        "body must be Earth, Moon or Mars; no other body has a registered D01/D06 contract."
    ),
    "unregistered_feature": (
        "the feature id is not registered as a D01 terrain or D06 regolith quantity for the "
        "declared dimension; no value is compared."
    ),
    "dimension_mismatch": "the two samples declare different dimensions.",
    "feature_mismatch": (
        "the two samples are different quantities; a cross-quantity comparison is never scored."
    ),
    "body_out_of_scope": (
        "the feature is not registered for this body. Missing planetary coverage is never "
        "imputed from Earth or from another body."
    ),
    "role_not_comparable": (
        "the feature is uncertainty/evidence only (SoilGrids q05/q95); it is not a similarity "
        "quantity and is never turned into a comparable value."
    ),
    "state_not_detected": (
        "state is not 'detected'; unknown/no_coverage is missing evidence, not a zero or a "
        "negative value."
    ),
    "missing_value": (
        "no numeric value is present; missing stays missing and is never zero-filled."
    ),
    "value_not_finite": "the value is not finite; NaN/inf are missing, not zero.",
    "unit_mismatch": "the two samples use different units; this gate never converts units.",
    "unit_not_registered": ("the sample unit differs from the registered unit for this feature."),
    "method_version_missing": (
        "no method version is recorded; method equivalence is never assumed."
    ),
    "method_version_not_registered": (
        "the method version is not the registered version for this feature. A DEM derivative, "
        "a published slope product and a SoilGrids model prediction are not interchangeable."
    ),
    "method_version_mismatch": (
        "the two samples were produced by different method versions; cross-version comparison "
        "is refused."
    ),
    "analysis_resolution_missing": (
        "no analysis resolution is recorded; the analysis grid is never assumed."
    ),
    "analysis_resolution_mismatch": (
        "the analysis grids differ; this gate never resamples values onto a common grid."
    ),
    "coarse_resolution_not_comparable": (
        "a 250 m/5 km planetary coarse sample is not comparable with the 90 m fine analysis "
        "grid; coarse cells are never upsampled into fine evidence."
    ),
    "support_kind_mismatch": (
        "the spatial support kinds differ (fixed 1.98 km window vs AOI envelope vs 5 km coarse "
        "cell); a different support is not the same observation."
    ),
    "support_size_missing": "the support footprint size is missing; support is never assumed.",
    "support_size_mismatch": (
        "the support footprints differ; a coarser cell is not interchangeable with a finer window."
    ),
    "coarse_support_not_comparable": (
        "a 5 km coarse cell is not comparable with the fixed 1.98 km fine window."
    ),
    "value_origin_mismatch": (
        "the value origins differ (DEM derivative vs SoilGrids model prediction); a transferred "
        "Earth model value is not a local planetary measurement."
    ),
    "frame_unknown": (
        "the coordinate frame class is unknown; comparability requires a known body-fixed, "
        "projected metric frame."
    ),
    "frame_not_projected_metric": (
        "D01 terrain metrics require a projected metric body-fixed frame; slopes are never "
        "computed from longitude/latitude degrees."
    ),
    "frame_kind_mismatch": "the two samples use different coordinate frame classes.",
    "earth_frame_on_planetary_body": (
        "an Earth WGS84 frame was supplied for a planetary body; Moon and Mars require "
        "body-fixed planetary frames."
    ),
}

# The exact SoilGrids property/depth grammar is owned by ``eligibility.py``. This suffix
# check only decides whether a D06 id is the central estimate or a prediction quantile.
_D06_FEATURE_PREFIX = "soil_"


@dataclass(frozen=True, slots=True)
class FeatureContract:
    """What a feature id must satisfy before a pair can be compared."""

    dimension_id: str
    body_scope: tuple[str, ...]
    method_versions: tuple[str, ...]
    analysis_resolution_m: float
    support_kind: str
    support_size_m: float | None
    value_origin: str
    unit: str | None = None
    cross_body_allowed: bool = True
    feature_id: str | None = None
    note: str = ""

    def to_payload(self) -> dict[str, object]:
        return {
            "feature_id": self.feature_id,
            "dimension_id": self.dimension_id,
            "unit": self.unit,
            "body_scope": list(self.body_scope),
            "cross_body_allowed": self.cross_body_allowed,
            "method_versions": list(self.method_versions),
            "analysis_resolution_m": self.analysis_resolution_m,
            "resolution_class": resolution_class(self.analysis_resolution_m),
            "support_kind": self.support_kind,
            "support_size_m": self.support_size_m,
            "value_origin": self.value_origin,
            "note": self.note,
        }


def _d01_contract(feature: FeatureDefinition) -> FeatureContract:
    return FeatureContract(
        feature_id=feature.id,
        dimension_id=DIMENSION_D01,
        unit=feature.unit,
        body_scope=SUPPORTED_BODIES,
        cross_body_allowed=True,
        method_versions=(feature.method_version,),
        analysis_resolution_m=float(feature.extraction_scale_m),
        support_kind=SUPPORT_FIXED_WINDOW,
        support_size_m=FINE_WINDOW_M,
        value_origin=VALUE_ORIGIN_DEM_DERIVATIVE,
        note=feature.comparability_gate,
    )


D01_CONTRACTS: dict[str, FeatureContract] = {
    feature.id: _d01_contract(feature) for feature in FEATURES
}

#: The only feature ids this gate treats as cross-body comparable: the five D01
#: terrain-shape metrics at the fixed 90 m / 1.98 km analysis contract.
COMMON_FEATURE_IDS: tuple[str, ...] = tuple(feature.id for feature in FEATURES)

#: Canonical candidate/query body for a cross-body D01 search (G2 consumes this).
EARTH_BODY = "Earth"

#: Resolution-class label shared with the D01 fine-support contract.
FINE_SUPPORT_CLASS = RESOLUTION_CLASS_FINE

#: Registered method version per common feature; a pair must match exactly.
REQUIRED_METHOD_BY_FEATURE: dict[str, str] = {
    feature.id: feature.method_version for feature in FEATURES
}

D06_CONTRACT = FeatureContract(
    dimension_id=DIMENSION_D06,
    unit=None,
    body_scope=("Earth",),
    cross_body_allowed=False,
    method_versions=(D06_METHOD_VERSION,),
    analysis_resolution_m=D06_NATIVE_RESOLUTION_M,
    support_kind=SUPPORT_AREA_ENVELOPE_MEAN,
    support_size_m=None,
    value_origin=VALUE_ORIGIN_MODEL_PREDICTION,
    note=(
        "SoilGrids 2.0 model prediction on native 250 m cells aggregated to an AOI envelope. "
        "Earth-only; the AOI mean is not a local sample and has no Moon/Mars counterpart."
    ),
)


def contract_for(feature_id: str, dimension_id: str) -> FeatureContract | None:
    """Return the registered contract, or ``None`` when the id is not a D01/D06 quantity."""
    if dimension_id == DIMENSION_D01:
        return D01_CONTRACTS.get(feature_id)
    if dimension_id == DIMENSION_D06 and feature_id.startswith(_D06_FEATURE_PREFIX):
        return D06_CONTRACT
    return None


def _d06_is_uncertainty(feature_id: str) -> bool:
    return feature_id.endswith(D06_UNCERTAINTY_STATISTICS)


def resolution_class(analysis_resolution_m: float | None) -> str:
    numeric = _finite_number(analysis_resolution_m)
    if numeric is None:
        return RESOLUTION_CLASS_UNKNOWN
    if math.isclose(numeric, FINE_ANALYSIS_RESOLUTION_M, rel_tol=0.0, abs_tol=1e-9):
        return RESOLUTION_CLASS_FINE
    if numeric >= COARSE_ANALYSIS_RESOLUTION_M:
        return RESOLUTION_CLASS_COARSE
    return RESOLUTION_CLASS_UNKNOWN


def classify_frame_kind(reference: str | None) -> str:
    """Classify a stored CRS label without assuming Earth WGS84 for a planetary body."""
    upper = " ".join(str(reference or "").upper().split())
    if not upper:
        return FRAME_UNKNOWN
    if "WGS84" in upper or "WGS 84" in upper or "4326" in upper:
        return FRAME_EARTH_WGS84
    if upper.startswith(("+PROJ=LONGLAT", "+PROJ=LATLONG")):
        return FRAME_GEOGRAPHIC
    if upper.startswith("PROJCRS") or upper.startswith("PROJCS") or upper.startswith("+PROJ"):
        return FRAME_PROJECTED_METRIC
    if "GEOGCRS" in upper or "GEOGCS" in upper:
        return FRAME_GEOGRAPHIC
    return FRAME_UNKNOWN


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    numeric = float(value)
    return numeric if math.isfinite(numeric) else None


def _close(left: float, right: float, tolerance: float) -> bool:
    return math.isclose(left, right, rel_tol=0.0, abs_tol=tolerance)


@dataclass(frozen=True, slots=True)
class CrossBodySample:
    """One stored observation with the provenance the gate needs.

    ``value`` is ``None`` when the source reported no value; it is never replaced by 0.
    ``analysis_resolution_m`` and ``support_size_m`` are the *analysis* values, not the
    native source pixel size.
    """

    body: str
    feature_id: str
    dimension_id: str
    value: float | None
    unit: str
    method_version: str
    analysis_resolution_m: float | None
    support_kind: str
    value_origin: str
    support_size_m: float | None = None
    state: str = "detected"
    frame_kind: str = FRAME_UNKNOWN
    frame: str | None = None
    source_id: str | None = None
    dataset_ids: tuple[str, ...] = ()
    observation_id: str | None = None
    quality_flags: tuple[str, ...] = ()
    license: str | None = None
    temporal_support: str | None = None
    selector: Mapping[str, Any] = field(default_factory=dict)

    def to_provenance(self) -> dict[str, object]:
        return {
            "body": self.body,
            "feature_id": self.feature_id,
            "dimension_id": self.dimension_id,
            "value": self.value,
            "unit": self.unit,
            "state": self.state,
            "method_version": self.method_version,
            "analysis_resolution_m": self.analysis_resolution_m,
            "resolution_class": resolution_class(self.analysis_resolution_m),
            "support_kind": self.support_kind,
            "support_size_m": self.support_size_m,
            "value_origin": self.value_origin,
            "frame_kind": self.frame_kind,
            "frame": self.frame,
            "source_id": self.source_id,
            "dataset_ids": list(self.dataset_ids),
            "observation_id": self.observation_id,
            "quality_flags": list(self.quality_flags),
            "license": self.license,
            "temporal_support": self.temporal_support,
            "selector": dict(self.selector),
        }


@dataclass(frozen=True, slots=True)
class Reason:
    code: str
    message: str

    def to_payload(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


@dataclass(frozen=True, slots=True)
class ComparabilityDecision:
    reference: CrossBodySample
    candidate: CrossBodySample
    reasons: tuple[Reason, ...]

    @property
    def comparable(self) -> bool:
        return not self.reasons

    @property
    def cross_body(self) -> bool:
        return self.reference.body != self.candidate.body

    @property
    def same_feature(self) -> bool:
        return self.reference.feature_id == self.candidate.feature_id

    def to_payload(self) -> dict[str, object]:
        return {
            "gate_version": GATE_VERSION,
            "feature_id": self.reference.feature_id if self.same_feature else None,
            "dimension_id": (
                self.reference.dimension_id
                if self.reference.dimension_id == self.candidate.dimension_id
                else None
            ),
            "bodies": {
                "reference": self.reference.body,
                "candidate": self.candidate.body,
            },
            "cross_body": self.cross_body,
            "comparable": self.comparable,
            "reason_codes": [reason.code for reason in self.reasons],
            "reasons": [reason.to_payload() for reason in self.reasons],
            "reference": self.reference.to_provenance(),
            "candidate": self.candidate.to_provenance(),
            "comparison_scope": "single_feature_pair_no_aggregate",
            "scope_note": SCOPE_NOTE,
        }


def _add(codes: list[str], code: str) -> None:
    if code not in codes:
        codes.append(code)


def _sample_reasons(sample: CrossBodySample, contract: FeatureContract, codes: list[str]) -> None:
    if sample.body not in SUPPORTED_BODIES:
        _add(codes, "unknown_body")
    if sample.body not in contract.body_scope:
        _add(codes, "body_out_of_scope")

    if sample.state != "detected":
        _add(codes, "state_not_detected")
    numeric = _finite_number(sample.value)
    if sample.value is None:
        _add(codes, "missing_value")
    elif numeric is None:
        _add(codes, "value_not_finite")

    if contract.unit is not None and sample.unit != contract.unit:
        _add(codes, "unit_not_registered")
    if contract.dimension_id == DIMENSION_D06:
        if sample.unit not in D06_UNITS:
            _add(codes, "unit_not_registered")
        if _d06_is_uncertainty(sample.feature_id):
            _add(codes, "role_not_comparable")

    if not sample.method_version:
        _add(codes, "method_version_missing")
    elif sample.method_version not in contract.method_versions:
        _add(codes, "method_version_not_registered")

    resolution = _finite_number(sample.analysis_resolution_m)
    if resolution is None:
        _add(codes, "analysis_resolution_missing")
    elif not _close(resolution, contract.analysis_resolution_m, _RESOLUTION_TOLERANCE_M):
        _add(codes, "analysis_resolution_mismatch")
        if resolution > contract.analysis_resolution_m:
            _add(codes, "coarse_resolution_not_comparable")

    if sample.support_kind != contract.support_kind:
        _add(codes, "support_kind_mismatch")
        if sample.support_kind == SUPPORT_COARSE_CELL:
            _add(codes, "coarse_support_not_comparable")
    if contract.support_size_m is not None:
        size = _finite_number(sample.support_size_m)
        if size is None:
            _add(codes, "support_size_missing")
        elif not _close(size, contract.support_size_m, _SUPPORT_TOLERANCE_M):
            _add(codes, "support_size_mismatch")
            if size > contract.support_size_m:
                _add(codes, "coarse_support_not_comparable")

    if sample.value_origin != contract.value_origin:
        _add(codes, "value_origin_mismatch")

    if sample.frame_kind == FRAME_UNKNOWN:
        _add(codes, "frame_unknown")
    if sample.body != "Earth" and sample.frame_kind == FRAME_EARTH_WGS84:
        _add(codes, "earth_frame_on_planetary_body")
    if contract.dimension_id == DIMENSION_D01 and sample.frame_kind != FRAME_PROJECTED_METRIC:
        _add(codes, "frame_not_projected_metric")


def _pair_reasons(
    reference: CrossBodySample,
    candidate: CrossBodySample,
    ref_contract: FeatureContract,
    cand_contract: FeatureContract,
    codes: list[str],
) -> None:
    if reference.dimension_id != candidate.dimension_id:
        _add(codes, "dimension_mismatch")
    if reference.feature_id != candidate.feature_id:
        _add(codes, "feature_mismatch")
    if reference.unit != candidate.unit:
        _add(codes, "unit_mismatch")
    if reference.method_version != candidate.method_version:
        _add(codes, "method_version_mismatch")

    ref_resolution = _finite_number(reference.analysis_resolution_m)
    cand_resolution = _finite_number(candidate.analysis_resolution_m)
    if (
        ref_resolution is not None
        and cand_resolution is not None
        and not _close(ref_resolution, cand_resolution, _RESOLUTION_TOLERANCE_M)
    ):
        _add(codes, "analysis_resolution_mismatch")

    if reference.support_kind != candidate.support_kind:
        _add(codes, "support_kind_mismatch")

    ref_size = _finite_number(reference.support_size_m)
    cand_size = _finite_number(candidate.support_size_m)
    if (
        ref_size is not None
        and cand_size is not None
        and not _close(ref_size, cand_size, _SUPPORT_TOLERANCE_M)
    ):
        _add(codes, "support_size_mismatch")

    if (
        reference.frame_kind != FRAME_UNKNOWN
        and candidate.frame_kind != FRAME_UNKNOWN
        and reference.frame_kind != candidate.frame_kind
    ):
        _add(codes, "frame_kind_mismatch")

    # A pair is cross-body only when both sides are registered quantities; a D06 contract
    # is Earth-only, so one planetary side already reports body_out_of_scope above.
    if reference.body != candidate.body and (
        not ref_contract.cross_body_allowed or not cand_contract.cross_body_allowed
    ):
        _add(codes, "body_out_of_scope")


def evaluate_pair(reference: CrossBodySample, candidate: CrossBodySample) -> ComparabilityDecision:
    """Decide whether two observations may be compared. Never returns a score.

    The reference is usually the query/reference observation and the candidate is a stored
    observation. The decision is symmetric in its rules; the argument order only fixes
    which side is labelled ``reference`` in the payload.
    """
    codes: list[str] = []
    ref_contract = contract_for(reference.feature_id, reference.dimension_id)
    cand_contract = contract_for(candidate.feature_id, candidate.dimension_id)

    if ref_contract is None or cand_contract is None:
        _add(codes, "unregistered_feature")
    if ref_contract is not None:
        _sample_reasons(reference, ref_contract, codes)
    if cand_contract is not None:
        _sample_reasons(candidate, cand_contract, codes)
    if ref_contract is not None and cand_contract is not None:
        _pair_reasons(reference, candidate, ref_contract, cand_contract, codes)

    codes.sort(key=lambda code: _REASON_ORDER.index(code))
    return ComparabilityDecision(
        reference=reference,
        candidate=candidate,
        reasons=tuple(Reason(code, _REASON_MESSAGES[code]) for code in codes),
    )


def feature_gate_payload() -> dict[str, object]:
    """The registered cross-body contracts, for docs/UI. Contains no score or total."""
    return {
        "gate_version": GATE_VERSION,
        "dimensions": [
            {
                "dimension_id": DIMENSION_D01,
                "name": "Terrain & Landforms",
                "cross_body_allowed": True,
                "body_scope": list(SUPPORTED_BODIES),
                "analysis_resolution_m": FINE_ANALYSIS_RESOLUTION_M,
                "resolution_class": RESOLUTION_CLASS_FINE,
                "support_kind": SUPPORT_FIXED_WINDOW,
                "support_size_m": FINE_WINDOW_M,
                "value_origin": VALUE_ORIGIN_DEM_DERIVATIVE,
                "coarse_cell_support_m": COARSE_CELL_M,
                "features": [
                    {
                        **D01_CONTRACTS[feature.id].to_payload(),
                        "provider_coverage_by_body": dict(feature.provider_coverage_by_body),
                    }
                    for feature in FEATURES
                ],
            },
            {
                **D06_CONTRACT.to_payload(),
                "name": "Regolith & Surface Properties",
                "feature_id_pattern": "soil_<property>_<depth>cm_<statistic>",
                "uncertainty_feature_suffixes": list(D06_UNCERTAINTY_STATISTICS),
                "registered_units": sorted(D06_UNITS),
                "note": (
                    f"{D06_CONTRACT.note} Moon/Mars D06 sources remain not selected, so no "
                    "cross-body D06 value is registered."
                ),
            },
        ],
        "scope_note": SCOPE_NOTE,
    }
