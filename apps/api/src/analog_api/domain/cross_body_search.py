"""Cross-body D01 target -> Earth candidate search (Issue #170, Goal G / slice G2).

This module turns a Moon or Mars fine D01 vector into a legal Earth ranking target. It does
not redefine terrain features, scales or the shared ranking formula. Instead it:

* accepts only the D01 features that the versioned eligibility registry exposes for Earth,
  Moon and Mars with the same method version, 90 m analysis grid and fixed 1.98 km window
  (the "G1-compatible" subset);
* rejects coarse 250 m planetary AOI features instead of treating them as fine D01 values;
* resolves feature and dimension weights through the shared ``multidim`` contract;
* ranks the real fine Earth candidates already present in the snapshot using the shared
  ``rank_candidates`` implementation.

The emitted distance is a D01 terrain-shape query distance. It is not a six-dimension
cross-body score, not environmental equivalence and not a global survey.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from analog_api.domain.cross_body import (
    D01_CONTRACTS,
    FINE_ANALYSIS_RESOLUTION_M,
    FINE_WINDOW_M,
    FRAME_PROJECTED_METRIC,
    FRAME_UNKNOWN,
    SUPPORT_FIXED_WINDOW,
    VALUE_ORIGIN_DEM_DERIVATIVE,
    CrossBodySample,
    classify_frame_kind,
    contract_for,
    evaluate_pair,
)
from analog_api.domain.cross_body import (
    GATE_VERSION as CROSS_BODY_GATE_VERSION,
)
from analog_api.domain.eligibility import (
    REGISTRY_VERSION,
    ROLE_SIMILARITY,
    FeatureEligibility,
    MaterializedFeature,
)
from analog_api.domain.entities import CandidateEntity, ReferenceEntity
from analog_api.domain.multidim import (
    DISTANCE_SCOPE_DIMENSION_RMS,
    SCORING_VERSION_DIMENSION_RMS,
    DimensionControl,
    MultiDimCandidate,
    MultiDimError,
    MultiDimTarget,
    coverage_payload,
    rank_candidates,
    resolve_targets,
)

CROSS_BODY_SEARCH_VERSION = "cross-body-d01-search-1.0.0"
CANDIDATE_BODY = "Earth"
SOURCE_BODIES = ("Moon", "Mars")
ANALYSIS_WINDOW_M = FINE_WINDOW_M
ANALYSIS_RESOLUTION_M = FINE_ANALYSIS_RESOLUTION_M

#: The G1 gate is the source of truth for cross-body contracts. This tuple is only the
#: registered D01 set in feature-registry order; ``g1_compatible_feature_ids`` applies the
#: body/method/support gates.
G1_COMPATIBLE_D01_FEATURES: tuple[str, ...] = tuple(D01_CONTRACTS)

CLAIM_SCOPE = (
    "Explicitly requested G1-compatible D01 terrain features only. This is a query-time "
    "D01 shape distance to curated fine Earth windows, not a six-dimension cross-body score, "
    "not a similarity percentage, not environmental equivalence and not a global survey."
)

LIMITATIONS = [
    "This ranks only the explicitly requested G1-compatible D01 terrain features. It is not a "
    "six-dimension scientific total and not a cross-body equivalence claim.",
    "The candidate pool is the curated fine Earth 90 m index in this database; absence from the "
    "results does not mean a location is a poor analog.",
    "The target is a processed Moon/Mars reference ROI or AOI vector, not global planetary "
    "coverage. Planetary provider coverage remains bounded and explicitly recorded.",
    "D01 similarity compares terrain shape under the shared method version, 90 m analysis grid "
    "and fixed 1.98 km window. It does not imply geologic, mineralogical or environmental "
    "equivalence.",
    "Native planetary DEM source resolutions differ from the Earth Copernicus GLO-90 source. "
    "The registry gates method/analysis grid/window, not native-source equivalence.",
    "Coarse 250 m planetary AOI features are never substituted for fine 90 m D01 values and "
    "never enter this distance.",
    "The output reports feature and dimension weights, per-feature contributions and every "
    "excluded candidate reason so the query is reproducible rather than a black-box score.",
]


class CrossBodySearchError(ValueError):
    """The cross-body request cannot be turned into a legal D01 Earth ranking target."""

    def __init__(
        self,
        code: str,
        message: str,
        details: list[dict[str, object]] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or []


@dataclass(frozen=True, slots=True)
class TargetFeatureValue:
    value: float
    unit: str
    method_version: str
    valid_fraction: float | None = None
    quality: str | None = None


@dataclass(frozen=True, slots=True)
class CrossBodyTarget:
    body: str
    source_id: str
    source_kind: str
    features: Mapping[str, TargetFeatureValue]
    display_name: str | None = None
    reference_id: str | None = None
    lat: float | None = None
    lon: float | None = None
    crs: str | None = None
    window_size_m: float | None = None
    analysis_resolution_m: float | None = None
    dataset_ids: tuple[str, ...] = ()
    quality_flags: tuple[str, ...] = ()
    support_kind: str = SUPPORT_FIXED_WINDOW
    value_origin: str = VALUE_ORIGIN_DEM_DERIVATIVE
    state: str = "detected"
    license: str | None = None
    provenance: Mapping[str, Any] = field(default_factory=dict)

    @property
    def frame_kind(self) -> str:
        return frame_kind_for_crs(self.crs)

    @property
    def support_size_m(self) -> float | None:
        return self.window_size_m


@dataclass(frozen=True, slots=True)
class ResolvedCrossBodyTarget:
    targets: tuple[MultiDimTarget, ...]
    payload: dict[str, object]
    accepted_feature_ids: tuple[str, ...]
    dimension_controls: dict[str, DimensionControl]
    scoring_version: str


def target_from_reference(reference: ReferenceEntity) -> CrossBodyTarget:
    """Use the committed fine D01 values of a Moon/Mars reference as the target vector."""
    if reference.body not in SOURCE_BODIES:
        raise CrossBodySearchError(
            "unsupported_source_body",
            "Cross-body search accepts a Moon or Mars source reference; "
            f"{reference.body!r} is not a cross-body target.",
            [{"reference_id": reference.reference_id, "body": reference.body}],
        )
    features = {
        feature_id: TargetFeatureValue(
            value=observation.value,
            unit=observation.unit,
            method_version=observation.method_version,
            valid_fraction=observation.valid_fraction,
            quality=observation.quality,
        )
        for feature_id, observation in reference.features.items()
    }
    if not features:
        raise CrossBodySearchError(
            "empty_target",
            f"Reference {reference.reference_id} has no D01 observations to rank.",
            [{"reference_id": reference.reference_id}],
        )
    return CrossBodyTarget(
        body=reference.body,
        source_id=reference.reference_id,
        source_kind="committed_planetary_reference",
        display_name=reference.display_name,
        reference_id=reference.reference_id,
        lat=reference.lat,
        lon=reference.lon,
        crs=reference.crs,
        window_size_m=reference.window_size_m,
        analysis_resolution_m=reference.analysis_resolution_m,
        dataset_ids=(reference.dataset_id,),
        quality_flags=tuple(reference.quality_flags),
        features=features,
        support_kind=SUPPORT_FIXED_WINDOW,
        value_origin=VALUE_ORIGIN_DEM_DERIVATIVE,
        state="detected",
        provenance={
            "origin": "committed planetary reference manifest",
            "reference_id": reference.reference_id,
            "dataset_id": reference.dataset_id,
            "lat": reference.lat,
            "lon": reference.lon,
            "crs": reference.crs,
            "window_size_m": reference.window_size_m,
            "analysis_resolution_m": reference.analysis_resolution_m,
            "valid_fraction": reference.valid_fraction,
            "quality_flags": list(reference.quality_flags),
            "notes": reference.notes,
        },
    )


def target_from_values(
    *,
    body: str,
    source_id: str,
    values: Mapping[str, TargetFeatureValue],
    source_kind: str = "explicit_d01_vector",
    display_name: str | None = None,
    lat: float | None = None,
    lon: float | None = None,
    crs: str | None = None,
    window_size_m: float | None = None,
    analysis_resolution_m: float | None = None,
    dataset_ids: Sequence[str] = (),
    quality_flags: Sequence[str] = (),
    support_kind: str = SUPPORT_FIXED_WINDOW,
    value_origin: str = VALUE_ORIGIN_DEM_DERIVATIVE,
    state: str = "detected",
    license: str | None = None,
    provenance: Mapping[str, Any] | None = None,
) -> CrossBodyTarget:
    """Build a target from an explicit vector, preserving its declared provenance.

    Values are accepted only when their unit and method version are supplied. The resolver
    then rejects coarse ids, Earth-only ids and unsupported roles with explicit reasons.
    """
    if body not in SOURCE_BODIES:
        raise CrossBodySearchError(
            "unsupported_source_body",
            "Cross-body search accepts a Moon or Mars source body only.",
            [{"body": body}],
        )
    if not values:
        raise CrossBodySearchError("empty_target", "At least one D01 target value is required.")
    return CrossBodyTarget(
        body=body,
        source_id=source_id,
        source_kind=source_kind,
        display_name=display_name,
        lat=lat,
        lon=lon,
        crs=crs,
        window_size_m=window_size_m,
        analysis_resolution_m=analysis_resolution_m,
        dataset_ids=tuple(dataset_ids),
        quality_flags=tuple(quality_flags),
        features=dict(values),
        support_kind=support_kind,
        value_origin=value_origin,
        state=state,
        license=license,
        provenance=dict(provenance or {}),
    )


def frame_kind_for_crs(crs: str | None) -> str:
    """Classify a stored Earth or planetary CRS, including EPSG UTM codes.

    G1 owns the frame vocabulary. The stored Earth candidates use projected UTM codes such as
    ``EPSG:32632`` rather than a WKT ``PROJCRS`` string, so this adapter classifies those as
    projected metric without redefining the gate.
    """
    kind = classify_frame_kind(crs)
    if kind != FRAME_UNKNOWN:
        return kind
    upper = " ".join(str(crs or "").upper().split())
    if upper.startswith("EPSG:"):
        try:
            code = int(upper.split(":", 1)[1])
        except ValueError:
            return FRAME_UNKNOWN
        if 32601 <= code <= 32760:
            return FRAME_PROJECTED_METRIC
    return FRAME_UNKNOWN


def g1_compatible_feature_ids(
    registry: Mapping[str, FeatureEligibility],
    *,
    source_body: str,
    candidate_body: str = CANDIDATE_BODY,
) -> tuple[str, ...]:
    """Return the G1-contract and registry-compatible D01 features for this body pair."""
    result: list[str] = []
    for feature_id, contract in D01_CONTRACTS.items():
        entry = registry.get(feature_id)
        if entry is None:
            continue
        if not contract.cross_body_allowed:
            continue
        if source_body not in contract.body_scope or candidate_body not in contract.body_scope:
            continue
        if contract.dimension_id != "D01":
            continue
        if contract.analysis_resolution_m != ANALYSIS_RESOLUTION_M:
            continue
        if contract.support_kind != SUPPORT_FIXED_WINDOW:
            continue
        if contract.support_size_m != ANALYSIS_WINDOW_M:
            continue
        if entry.dimension_id != "D01":
            continue
        if not entry.roles[ROLE_SIMILARITY].supported:
            continue
        if source_body not in entry.body_scope or candidate_body not in entry.body_scope:
            continue
        support = entry.support
        if support.get("kind") != SUPPORT_FIXED_WINDOW:
            continue
        if float(support.get("window_size_m", 0.0)) != ANALYSIS_WINDOW_M:
            continue
        if float(support.get("analysis_resolution_m", 0.0)) != ANALYSIS_RESOLUTION_M:
            continue
        if not entry.scale or entry.scale <= 0:
            continue
        result.append(feature_id)
    return tuple(result)


def _rejected(
    feature_id: str,
    reason: str,
    message: str,
    **extra: object,
) -> dict[str, object]:
    return {"feature_id": feature_id, "reason": reason, "message": message, **extra}


def _target_sample(
    *,
    target: CrossBodyTarget,
    feature_id: str,
    observation: TargetFeatureValue,
) -> CrossBodySample:
    return CrossBodySample(
        body=target.body,
        feature_id=feature_id,
        dimension_id="D01",
        value=observation.value,
        unit=observation.unit,
        method_version=observation.method_version,
        analysis_resolution_m=target.analysis_resolution_m,
        support_kind=target.support_kind,
        value_origin=target.value_origin,
        support_size_m=target.support_size_m,
        state=target.state,
        frame_kind=target.frame_kind,
        frame=target.crs,
        source_id=target.source_id,
        dataset_ids=tuple(target.dataset_ids),
        observation_id=target.reference_id or target.source_id,
        quality_flags=tuple(target.quality_flags),
        license=target.license,
        selector={},
    )


def _validate_target_feature(
    *,
    feature_id: str,
    observation: TargetFeatureValue,
    entry: FeatureEligibility | None,
    g1_ids: Sequence[str],
    target: CrossBodyTarget,
) -> tuple[dict[str, object] | None, dict[str, object] | None]:
    """Return ``(accepted, rejected)`` for one requested target feature."""
    if feature_id.startswith("coarse_"):
        return None, _rejected(
            feature_id,
            "coarse_fine_method_not_comparable",
            "Coarse 250 m planetary AOI features are a separate method/support and cannot be "
            "used as fine 90 m D01 target values.",
            method_version=observation.method_version,
        )
    if entry is None:
        return None, _rejected(
            feature_id,
            "unknown_feature",
            f"{feature_id} is not present in the versioned feature registry.",
        )
    if feature_id not in g1_ids:
        if entry.dimension_id != "D01":
            reason = "dimension_not_g1_compatible"
            message = (
                f"{feature_id} belongs to {entry.dimension_id}; this cross-body path ranks "
                "only the G1-compatible D01 terrain subset."
            )
        elif not entry.roles[ROLE_SIMILARITY].supported:
            reason = "feature_role_not_scoreable"
            message = (
                f"{feature_id} is not registered for weighted similarity. "
                f"{entry.roles[ROLE_SIMILARITY].reason or 'It remains evidence only.'}"
            )
        elif target.body not in entry.body_scope or CANDIDATE_BODY not in entry.body_scope:
            reason = "body_out_of_scope"
            message = (
                f"{feature_id} is registered for {', '.join(entry.body_scope)} only and is not "
                f"available for {target.body} -> {CANDIDATE_BODY}."
            )
        else:
            reason = "not_g1_compatible"
            message = (
                f"{feature_id} does not satisfy the G1 D01 gate (method, 90 m analysis grid, "
                "fixed 1.98 km window and shared body scope)."
            )
        return None, _rejected(
            feature_id,
            reason,
            message,
            dimension_id=entry.dimension_id,
            body_scope=list(entry.body_scope),
            legal_roles=[role for role, support in entry.roles.items() if support.supported],
        )
    contract = contract_for(feature_id, "D01")
    if contract is None or not contract.cross_body_allowed:
        return None, _rejected(
            feature_id,
            "not_g1_compatible",
            f"G1 has no cross-body-allowed D01 contract for {feature_id}.",
        )
    if target.body not in contract.body_scope or CANDIDATE_BODY not in contract.body_scope:
        return None, _rejected(
            feature_id,
            "body_out_of_scope",
            f"G1 contract body_scope {list(contract.body_scope)} does not cover "
            f"{target.body} -> {CANDIDATE_BODY}.",
            body_scope=list(contract.body_scope),
        )
    if observation.method_version not in entry.method_versions:
        return None, _rejected(
            feature_id,
            "method_version_mismatch",
            f"{feature_id} target method_version {observation.method_version!r} is not "
            f"registered ({', '.join(entry.method_versions)}).",
            target_method_version=observation.method_version,
            registry_method_versions=list(entry.method_versions),
        )
    if observation.unit != entry.unit:
        return None, _rejected(
            feature_id,
            "unit_mismatch",
            f"{feature_id} target unit {observation.unit!r} is not the registry unit "
            f"{entry.unit!r}; values are never silently converted.",
            target_unit=observation.unit,
            registry_unit=entry.unit,
        )
    if not math.isfinite(observation.value):
        return None, _rejected(
            feature_id,
            "value_not_finite",
            f"{feature_id} target value must be finite.",
        )
    if entry.min_value is not None and observation.value < entry.min_value:
        return None, _rejected(
            feature_id,
            "target_out_of_domain",
            f"{feature_id} target is below {entry.min_value} {entry.unit}.",
            min=entry.min_value,
        )
    if entry.max_value is not None and observation.value > entry.max_value:
        return None, _rejected(
            feature_id,
            "target_out_of_domain",
            f"{feature_id} target is above {entry.max_value} {entry.unit}.",
            max=entry.max_value,
        )
    sample = _target_sample(target=target, feature_id=feature_id, observation=observation)
    decision = evaluate_pair(sample, sample)
    if not decision.comparable:
        return None, _rejected(
            feature_id,
            "g1_gate_rejected",
            f"G1 refused this target sample for {feature_id}.",
            gate_version=CROSS_BODY_GATE_VERSION,
            reason_codes=[reason.code for reason in decision.reasons],
            gate_reasons=[reason.to_payload() for reason in decision.reasons],
        )
    return (
        {
            "feature_id": feature_id,
            "dimension_id": entry.dimension_id,
            "label": entry.label,
            "unit": entry.unit,
            "value": observation.value,
            "method_version": observation.method_version,
            "scale": entry.scale,
            "scale_rationale": entry.scale_rationale,
            "body_scope": list(entry.body_scope),
            "measurement_family": entry.measurement_family,
            "valid_fraction": observation.valid_fraction,
            "quality": observation.quality,
            "selector": entry.selector,
            "support": entry.support,
            "g1_contract": contract.to_payload(),
        },
        None,
    )


def resolve_cross_body_target(
    *,
    target: CrossBodyTarget,
    registry: Mapping[str, FeatureEligibility],
    feature_weights: Mapping[str, float] | None = None,
    dimension_weights: Mapping[str, DimensionControl] | None = None,
    scoring_version: str | None = None,
) -> ResolvedCrossBodyTarget:
    """Validate a Moon/Mars D01 vector and resolve its legal Earth ranking weights."""
    if target.body not in SOURCE_BODIES:
        raise CrossBodySearchError(
            "unsupported_source_body",
            "Cross-body search accepts a Moon or Mars source body only.",
            [{"body": target.body}],
        )

    g1_ids = g1_compatible_feature_ids(registry, source_body=target.body)
    if not g1_ids:
        raise CrossBodySearchError(
            "no_g1_compatible_features",
            f"The registry exposes no G1-compatible D01 feature for {target.body} -> Earth.",
            [{"source_body": target.body, "candidate_body": CANDIDATE_BODY}],
        )

    coarse_ids = sorted(
        feature_id for feature_id in target.features if feature_id.startswith("coarse_")
    )
    if coarse_ids:
        raise CrossBodySearchError(
            "coarse_fine_method_not_comparable",
            "A coarse 250 m planetary AOI feature cannot be part of a fine 90 m cross-body "
            "D01 target. Coarse and fine methods are never mixed.",
            [
                _rejected(
                    feature_id,
                    "coarse_fine_method_not_comparable",
                    "Coarse 250 m planetary AOI features are a separate method/support and "
                    "cannot be used as fine 90 m D01 target values.",
                    method_version=target.features[feature_id].method_version,
                )
                for feature_id in coarse_ids
            ],
        )

    requested_weights = dict(feature_weights or {})
    unknown_weights = sorted(set(requested_weights) - set(target.features))
    if unknown_weights:
        raise CrossBodySearchError(
            "weight_without_target",
            "A feature weight was supplied without a target value: "
            + ", ".join(unknown_weights)
            + ".",
            [
                {"feature_id": feature_id, "reason": "target_value_missing"}
                for feature_id in unknown_weights
            ],
        )

    accepted: list[dict[str, object]] = []
    rejected: list[dict[str, object]] = []
    raw_targets: dict[str, tuple[float, float]] = {}
    for feature_id in sorted(target.features):
        observation = target.features[feature_id]
        accepted_item, rejected_item = _validate_target_feature(
            feature_id=feature_id,
            observation=observation,
            entry=registry.get(feature_id),
            g1_ids=g1_ids,
            target=target,
        )
        if rejected_item is not None:
            rejected.append(rejected_item)
            continue
        assert accepted_item is not None
        weight = float(requested_weights.get(feature_id, 1.0))
        if not math.isfinite(weight) or weight < 0:
            rejected.append(
                _rejected(
                    feature_id,
                    "invalid_weight",
                    f"{feature_id} weight must be finite and >= 0.",
                    weight=requested_weights.get(feature_id),
                )
            )
            continue
        accepted_item["weight"] = weight
        accepted.append(accepted_item)
        raw_targets[feature_id] = (observation.value, weight)

    if not accepted:
        raise CrossBodySearchError(
            "no_eligible_cross_body_target",
            f"No requested target feature is legal for {target.body} -> Earth D01 ranking.",
            rejected,
        )

    requested_dimensions = {str(item["dimension_id"]) for item in accepted}
    controls = (
        dict(dimension_weights)
        if dimension_weights is not None
        else {
            dimension_id: DimensionControl(weight=1.0, enabled=True)
            for dimension_id in requested_dimensions
        }
    )
    effective_scoring = scoring_version or SCORING_VERSION_DIMENSION_RMS
    try:
        resolved, effective_scoring = resolve_targets(
            raw_targets=raw_targets,
            registry=registry,
            candidate_body=target.body,
            dimension_weights=controls,
            scoring_version=effective_scoring,
        )
    except MultiDimError as exc:
        raise CrossBodySearchError(exc.code, exc.message, exc.details) from exc

    total_positive = sum(item.weight for item in resolved if item.weight > 0)
    feature_payload: dict[str, dict[str, object]] = {}
    accepted_by_id = {str(item["feature_id"]): item for item in accepted}
    for item in resolved:
        definition = accepted_by_id[item.feature_id]
        control = controls.get(str(definition["dimension_id"]), DimensionControl())
        active = control.enabled and control.weight > 0 and item.weight > 0
        feature_payload[item.feature_id] = {
            **definition,
            "weight": item.weight,
            "normalized_weight": round(item.weight / total_positive, 12) if total_positive else 0.0,
            "dimension_weight": control.weight,
            "dimension_enabled": control.enabled,
            "comparison": "active" if active else "zero_weight_not_compared",
        }

    dimension_payload: dict[str, dict[str, object]] = {}
    for dimension_id in sorted(requested_dimensions):
        control = controls.get(dimension_id, DimensionControl())
        dimension_payload[dimension_id] = {
            "weight": control.weight,
            "enabled": control.enabled,
            "active": control.enabled
            and control.weight > 0
            and any(
                item["weight"] > 0
                for item in feature_payload.values()
                if item["dimension_id"] == dimension_id
            ),
        }

    payload: dict[str, object] = {
        "cross_body_search_version": CROSS_BODY_SEARCH_VERSION,
        "claim_scope": CLAIM_SCOPE,
        "registry_version": REGISTRY_VERSION,
        "source": {
            "body": target.body,
            "source_id": target.source_id,
            "source_kind": target.source_kind,
            "display_name": target.display_name,
            "reference_id": target.reference_id,
            "lat": target.lat,
            "lon": target.lon,
            "crs": target.crs,
            "coordinate_frame": (
                "planetary_body_fixed" if target.body in SOURCE_BODIES else "unknown"
            ),
            "window_size_m": target.window_size_m,
            "analysis_resolution_m": target.analysis_resolution_m,
            "support_kind": target.support_kind,
            "support_size_m": target.support_size_m,
            "value_origin": target.value_origin,
            "state": target.state,
            "frame_kind": target.frame_kind,
            "dataset_ids": list(target.dataset_ids),
            "quality_flags": list(target.quality_flags),
            "provenance": dict(target.provenance),
            "earth_wgs84": False,
        },
        "candidate": {"body": CANDIDATE_BODY},
        "g1_gate": {
            "gate_version": CROSS_BODY_GATE_VERSION,
            "gate_module": "analog_api.domain.cross_body",
            "feature_ids": list(g1_ids),
            "rule": (
                "D01 only; G1 cross_body_allowed similarity contract; source and Earth body "
                "scope; same method version; fixed 1.98 km window at 90 m analysis resolution; "
                "projected metric frame; DEM-derivative value origin."
            ),
            "coarse_fine_mixing": False,
        },
        "weights": {
            "feature_weights": feature_payload,
            "dimension_weights": dimension_payload,
            "total_positive_feature_weight": total_positive,
        },
        "target_features": {
            "requested": sorted(target.features),
            "accepted": [item.feature_id for item in resolved],
            "rejected": rejected,
        },
        "scoring_version": effective_scoring,
    }
    return ResolvedCrossBodyTarget(
        targets=tuple(resolved),
        payload=payload,
        accepted_feature_ids=tuple(item.feature_id for item in resolved),
        dimension_controls=controls,
        scoring_version=effective_scoring,
    )


def build_earth_candidates(
    *,
    snapshot: Any,
    feature_ids: Sequence[str],
) -> list[MultiDimCandidate]:
    """Materialise the requested D01 values for the real fine Earth candidates."""
    candidates: list[MultiDimCandidate] = []
    for candidate in snapshot.candidates:
        values: dict[str, MaterializedFeature] = {}
        for feature_id in feature_ids:
            observation = candidate.features.get(feature_id)
            if observation is None:
                continue
            entry = snapshot.registry[feature_id]
            values[feature_id] = MaterializedFeature(
                feature_id=feature_id,
                area_id=candidate.location_id,
                body="Earth",
                value=observation.value,
                unit=observation.unit,
                method_version=observation.method_version,
                observation_id=f"d01:{candidate.location_id}:{feature_id}",
                dimension_id=entry.dimension_id,
                selector=dict(entry.selector),
                support=dict(entry.support),
                dataset_ids=(candidate.dataset_id,),
                quality_flags=tuple(candidate.quality_flags),
                uncertainty=None,
            )
        candidates.append(
            MultiDimCandidate(
                location_id=candidate.location_id,
                body="Earth",
                features=values,
            )
        )
    return candidates


def _candidate_sample(
    *,
    candidate: CandidateEntity,
    feature_id: str,
    observation: MaterializedFeature,
) -> CrossBodySample:
    return CrossBodySample(
        body="Earth",
        feature_id=feature_id,
        dimension_id="D01",
        value=observation.value,
        unit=observation.unit,
        method_version=observation.method_version,
        analysis_resolution_m=candidate.analysis_resolution_m,
        support_kind=SUPPORT_FIXED_WINDOW,
        value_origin=VALUE_ORIGIN_DEM_DERIVATIVE,
        support_size_m=candidate.window_size_m,
        state="detected",
        frame_kind=frame_kind_for_crs(candidate.crs),
        frame=candidate.crs,
        source_id=candidate.location_id,
        dataset_ids=(candidate.dataset_id,),
        observation_id=observation.observation_id,
        quality_flags=tuple(candidate.quality_flags),
        selector={},
    )


def _gate_earth_candidates(
    *,
    target: CrossBodyTarget,
    resolved: ResolvedCrossBodyTarget,
    candidates: Sequence[MultiDimCandidate],
    by_id: Mapping[str, CandidateEntity],
) -> tuple[list[MultiDimCandidate], list[dict[str, object]]]:
    """Apply G1's pair gate to every present positive-weight target/candidate pair."""
    target_samples: dict[str, CrossBodySample] = {}
    for item in resolved.targets:
        if item.weight <= 0:
            continue
        observation = target.features[item.feature_id]
        target_samples[item.feature_id] = _target_sample(
            target=target,
            feature_id=item.feature_id,
            observation=observation,
        )

    kept: list[MultiDimCandidate] = []
    excluded: list[dict[str, object]] = []
    for candidate in candidates:
        entity = by_id[candidate.location_id]
        reasons: list[str] = []
        gate_reasons: list[dict[str, str]] = []
        incompatible: list[str] = []
        for feature_id, target_sample in sorted(target_samples.items()):
            observation = candidate.features.get(feature_id)
            if observation is None:
                # Missing values are reported by the shared ranker, which has the exact
                # ``missing_feature:<id>`` reason. The gate never invents a value.
                continue
            decision = evaluate_pair(
                target_sample,
                _candidate_sample(
                    candidate=entity,
                    feature_id=feature_id,
                    observation=observation,
                ),
            )
            if decision.comparable:
                continue
            incompatible.append(feature_id)
            for reason in decision.reasons:
                code = f"g1:{reason.code}"
                if code not in reasons:
                    reasons.append(code)
                gate_reasons.append(reason.to_payload())
        if reasons:
            excluded.append(
                {
                    "location_id": candidate.location_id,
                    "reason": ",".join(reasons),
                    "reasons": reasons,
                    "missing_features": [],
                    "incompatible_features": incompatible,
                    "gate_reasons": gate_reasons,
                }
            )
            continue
        kept.append(candidate)
    return kept, excluded


def _dimension_scores(match: Any) -> dict[str, dict[str, float]]:
    return {
        dimension: dict(values)
        for dimension, values in sorted(getattr(match, "dimension_scores", {}).items())
    }


def _result_payload(
    *,
    rank: int,
    match: Any,
    candidate: CandidateEntity,
    dimensions: Mapping[str, Mapping[str, float]],
) -> dict[str, object]:
    return {
        "rank": rank,
        "location_id": candidate.location_id,
        "display_name": candidate.display_name,
        "body": "Earth",
        "lat": candidate.lat,
        "lon": candidate.lon,
        "bbox": candidate.bbox.as_dict(),
        "distance": match.distance,
        "distance_scope": DISTANCE_SCOPE_DIMENSION_RMS,
        "dimension_contributions": {dim: dict(values) for dim, values in dimensions.items()},
        "contributions": [
            {
                "feature_id": item.feature_id,
                "dimension_id": item.dimension_id,
                "label": item.label,
                "unit": item.unit,
                "target": item.target,
                "candidate": item.candidate,
                "delta": item.delta,
                "scale": item.scale,
                "weight": item.weight,
                "normalized_delta": item.normalized_delta,
                "contribution": item.contribution,
                "contribution_fraction": item.contribution_fraction,
                "measurement_family": item.measurement_family,
                "observation_id": item.observation_id,
                "selector": item.selector,
                "support": item.support,
                "uncertainty": item.uncertainty,
            }
            for item in match.contributions
        ],
        "valid_features": [item.feature_id for item in match.contributions],
        "data_kind": candidate.data_kind,
        "quality_flags": list(candidate.quality_flags),
    }


def execute_cross_body_search(
    *,
    snapshot: Any,
    target: CrossBodyTarget,
    feature_weights: Mapping[str, float] | None = None,
    dimension_weights: Mapping[str, DimensionControl] | None = None,
    scoring_version: str | None = None,
    limit: int = 10,
    offset: int = 0,
    data_version: str | None = None,
    candidates: Sequence[MultiDimCandidate] | None = None,
) -> dict[str, object]:
    """Rank real Earth candidates against a legal Moon/Mars D01 target vector."""
    if limit < 1 or limit > 50:
        raise CrossBodySearchError("invalid_limit", "limit must be between 1 and 50.")
    if offset < 0:
        raise CrossBodySearchError("invalid_offset", "offset must be >= 0.")

    resolved = resolve_cross_body_target(
        target=target,
        registry=snapshot.registry,
        feature_weights=feature_weights,
        dimension_weights=dimension_weights,
        scoring_version=scoring_version,
    )
    earth_candidates = (
        list(candidates)
        if candidates is not None
        else build_earth_candidates(snapshot=snapshot, feature_ids=resolved.accepted_feature_ids)
    )
    by_id = {candidate.location_id: candidate for candidate in snapshot.candidates}
    gated_candidates, gate_excluded = _gate_earth_candidates(
        target=target,
        resolved=resolved,
        candidates=earth_candidates,
        by_id=by_id,
    )
    outcome = rank_candidates(
        targets=resolved.targets,
        candidates=gated_candidates,
        registry=snapshot.registry,
        dimension_weights=resolved.dimension_controls,
        scoring_version=resolved.scoring_version,
    )

    total_eligible = len(outcome.matches)
    window = outcome.matches[offset : offset + limit]
    results: list[dict[str, object]] = []
    for rank, match in enumerate(window, start=offset + 1):
        results.append(
            _result_payload(
                rank=rank,
                match=match,
                candidate=by_id[match.location_id],
                dimensions=_dimension_scores(match),
            )
        )

    excluded = gate_excluded + [
        {
            "location_id": item.location_id,
            "reason": ",".join(item.reasons),
            "reasons": list(item.reasons),
            "missing_features": list(item.missing_features),
            "incompatible_features": list(item.incompatible_features),
        }
        for item in outcome.excluded
    ]
    payload = dict(resolved.payload)
    payload.update(
        {
            "data_version": data_version,
            "candidate": {
                "body": CANDIDATE_BODY,
                "pool_size": len(earth_candidates),
                "g1_gate_passed": len(gated_candidates),
                "g1_gate_excluded": len(gate_excluded),
                "eligible_count": total_eligible,
                "excluded_count": len(excluded),
            },
            "eligible_candidate_count": total_eligible,
            "excluded_candidate_count": len(excluded),
            "total_matched": total_eligible,
            "offset": offset,
            "limit": limit,
            "returned": len(results),
            "next_offset": offset + len(results)
            if offset + len(results) < total_eligible
            else None,
            "scoring": {
                "version": resolved.scoring_version,
                "formula": (
                    "For each compared feature f in D01: z_f = (x_f - t_f) / s_f. "
                    "q_D01 = Σ(v_f * z_f^2) / Σ(v_f). distance = sqrt(Σ(w_D * q_D) / Σ(w_D)) "
                    "over active dimensions."
                ),
                "dimension_controls": resolved.payload["weights"]["dimension_weights"],
                "active_dimensions": list(getattr(outcome, "active_dimensions", ())),
                "note": (
                    "Feature weights are relative within the dimension; dimension weights are "
                    "applied after the within-dimension feature-weighted mean."
                ),
            },
            "coverage": coverage_payload(
                targets=resolved.targets,
                registry=snapshot.registry,
                candidates=earth_candidates,
                matches=outcome.matches,
            ),
            "excluded": excluded,
            "results": results,
            "limitations": LIMITATIONS,
        }
    )
    return payload


__all__ = [
    "CANDIDATE_BODY",
    "CLAIM_SCOPE",
    "CROSS_BODY_SEARCH_VERSION",
    "G1_COMPATIBLE_D01_FEATURES",
    "CrossBodySearchError",
    "CrossBodyTarget",
    "ResolvedCrossBodyTarget",
    "TargetFeatureValue",
    "build_earth_candidates",
    "execute_cross_body_search",
    "g1_compatible_feature_ids",
    "resolve_cross_body_target",
    "target_from_reference",
    "target_from_values",
]
