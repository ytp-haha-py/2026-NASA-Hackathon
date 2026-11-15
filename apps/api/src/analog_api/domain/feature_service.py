"""Service layer for the feature registry and multi-dimensional search (Issue #116)."""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from analog_api.domain.eligibility import (
    REGISTRY_VERSION,
    ROLE_HARD_FILTER,
    ROLE_SIMILARITY,
    FeatureCoverage,
    FeatureEligibility,
    MaterializationOutcome,
    MaterializedFeature,
    ObservedFeature,
    build_registry,
    coverage_for_registry,
    join_audit,
    materialize,
    observed_stats,
)
from analog_api.domain.entities import CandidateEntity, ReferenceEntity
from analog_api.domain.errors import AppError
from analog_api.domain.features import FEATURES
from analog_api.domain.multidim import (
    DISTANCE_SCOPE_LEGACY,
    MAX_MULTIDIM_LIMIT,
    SCORING_VERSION_DIMENSION_RMS,
    SCORING_VERSION_LEGACY,
    DimensionControl,
    MultiDimCandidate,
    MultiDimError,
    MultiDimExclusion,
    coverage_payload,
    rank_candidates,
    resolve_targets,
)
from analog_api.ports.repository import CandidateRepositoryFull

BASE_LIMITATIONS = [
    "This endpoint ranks only the features explicitly requested and registered as legal "
    "similarity inputs. It does not create a six-dimension scientific total.",
    "D06 SoilGrids values are 250 m model predictions aggregated over the AOI envelope, not local "
    "soil samples and not the same spatial support as the 90 m terrain windows.",
    "The typed join is exact area_id == fine location_id. No nearest-location or proximity merge "
    "is performed.",
    "Earth–Moon and Earth–Mars feature equivalence is not claimed. Features without a validated "
    "same-body/method/support counterpart remain evidence/context.",
    "The candidate pool is the curated fine Earth index in this database; absence from results "
    "does not mean a location is a poor analog.",
]


@dataclass(slots=True)
class RegistrySnapshot:
    rows: list[dict[str, Any]]
    registry: dict[str, FeatureEligibility]
    observed: dict[str, ObservedFeature]
    outcome: MaterializationOutcome
    coverage: dict[str, FeatureCoverage]
    join: dict[str, object]
    candidates: list[CandidateEntity]
    references: list[ReferenceEntity]
    areas: list[dict[str, Any]]


def _d01_coverage(
    candidates: Sequence[CandidateEntity],
    references: Sequence[ReferenceEntity],
) -> dict[str, FeatureCoverage]:
    coverage: dict[str, FeatureCoverage] = {}
    for feature in FEATURES:
        observed = ObservedFeature(feature_id=feature.id)
        gated: set[str] = set()
        for candidate in candidates:
            observation = candidate.features.get(feature.id)
            if observation is None:
                continue
            observed.observation_ids.append(f"d01:{candidate.location_id}:{feature.id}")
            observed.dimensions.add("D01")
            observed.bodies["Earth"] += 1
            observed.states["detected"] += 1
            observed.value_kinds.add(feature.value_kind)
            observed.units.add(observation.unit)
            observed.method_versions.add(observation.method_version)
            observed.dataset_ids.add(candidate.dataset_id)
            observed.area_ids.add(candidate.location_id)
            observed.numeric_count += 1
            observed.numeric_min = (
                observation.value
                if observed.numeric_min is None
                else min(observed.numeric_min, observation.value)
            )
            observed.numeric_max = (
                observation.value
                if observed.numeric_max is None
                else max(observed.numeric_max, observation.value)
            )
            if observation.method_version == feature.method_version and math.isfinite(
                observation.value
            ):
                gated.add(candidate.location_id)
        for reference in references:
            observation = reference.features.get(feature.id)
            if observation is None:
                continue
            observed.observation_ids.append(f"d01:{reference.reference_id}:{feature.id}")
            observed.dimensions.add("D01")
            observed.bodies[reference.body] += 1
            observed.states["detected"] += 1
            observed.value_kinds.add(feature.value_kind)
            observed.units.add(observation.unit)
            observed.method_versions.add(observation.method_version)
            observed.area_ids.add(reference.reference_id)
            observed.numeric_count += 1
            observed.numeric_min = (
                observation.value
                if observed.numeric_min is None
                else min(observed.numeric_min, observation.value)
            )
            observed.numeric_max = (
                observation.value
                if observed.numeric_max is None
                else max(observed.numeric_max, observation.value)
            )
        joined = sorted(observed.area_ids & {candidate.location_id for candidate in candidates})
        coverage[feature.id] = FeatureCoverage(
            feature_id=feature.id,
            observed=observed,
            joined_candidate_ids=tuple(joined),
            gated_candidate_ids=tuple(sorted(gated)),
            rejected_count=max(0, len(joined) - len(gated)),
        )
    return coverage


def assemble_snapshot(
    *,
    rows: Sequence[Mapping[str, Any]],
    areas: Sequence[Mapping[str, Any]],
    candidates: Sequence[CandidateEntity],
    references: Sequence[ReferenceEntity],
) -> RegistrySnapshot:
    rows_list = [dict(row) for row in rows]
    registry = build_registry(rows_list)
    observed = observed_stats(rows_list)
    candidate_bboxes = {candidate.location_id: candidate.bbox.as_dict() for candidate in candidates}
    outcome = materialize(
        registry=registry,
        rows=rows_list,
        candidate_bboxes=candidate_bboxes,
    )
    coverage = coverage_for_registry(
        registry=registry,
        observed=observed,
        candidate_ids=candidate_bboxes,
        outcome=outcome,
    )
    coverage.update(_d01_coverage(list(candidates), list(references)))
    join = join_audit(
        typed_area_ids=[str(area["area_id"]) for area in areas],
        candidate_ids=candidate_bboxes,
        reference_ids=[reference.reference_id for reference in references],
    )
    return RegistrySnapshot(
        rows=rows_list,
        registry=registry,
        observed=observed,
        outcome=outcome,
        coverage=coverage,
        join=join,
        candidates=list(candidates),
        references=list(references),
        areas=[dict(area) for area in areas],
    )


def _feature_payload(
    entry: FeatureEligibility, coverage: FeatureCoverage | None
) -> dict[str, object]:
    payload = entry.to_payload()
    payload["coverage"] = coverage.to_payload() if coverage else None
    return payload


def audit_summary(snapshot: RegistrySnapshot) -> dict[str, object]:
    by_dimension: dict[str, dict[str, int]] = {}
    role_counts: Counter[str] = Counter()
    for entry in snapshot.registry.values():
        stats = by_dimension.setdefault(
            entry.dimension_id,
            {
                "features": 0,
                "similarity": 0,
                "hard_filter": 0,
                "evidence": 0,
                "unsupported_for_scoring": 0,
            },
        )
        stats["features"] += 1
        if entry.roles[ROLE_SIMILARITY].supported:
            stats["similarity"] += 1
        else:
            stats["unsupported_for_scoring"] += 1
        if entry.roles[ROLE_HARD_FILTER].supported:
            stats["hard_filter"] += 1
        if entry.roles["evidence"].supported:
            stats["evidence"] += 1
        for role, support in entry.roles.items():
            if support.supported:
                role_counts[role] += 1
    scoreable = [
        entry for entry in snapshot.registry.values() if entry.roles[ROLE_SIMILARITY].supported
    ]
    gated_areas = {
        feature_id: len(coverage.gated_candidate_ids)
        for feature_id, coverage in snapshot.coverage.items()
        if coverage.gated_candidate_ids
    }
    return {
        "registry_version": REGISTRY_VERSION,
        "registered_feature_count": len(snapshot.registry),
        "observed_feature_count": len(snapshot.observed),
        "similarity_feature_count": len(scoreable),
        "similarity_feature_with_gated_values": len(gated_areas),
        "gated_area_count_by_feature": gated_areas,
        "role_support_counts": dict(sorted(role_counts.items())),
        "by_dimension": dict(sorted(by_dimension.items())),
        "rejected_observation_count": len(snapshot.outcome.rejected),
        "rejection_reason_counts": dict(
            sorted(
                Counter(
                    reason for item in snapshot.outcome.rejected for reason in item.reasons
                ).items()
            )
        ),
    }


def registry_payload(
    snapshot: RegistrySnapshot,
    *,
    body: str | None = None,
    dimension: str | None = None,
    role: str | None = None,
    data_version: str,
) -> dict[str, object]:
    if body is not None and body not in ("Earth", "Moon", "Mars"):
        raise AppError("invalid_body", "body must be Earth, Moon or Mars.", status_code=422)
    if dimension is not None and not dimension.upper().startswith("D"):
        raise AppError(
            "invalid_dimension", "dimension must be a code such as D01..D06.", status_code=422
        )
    features: list[dict[str, object]] = []
    for feature_id in sorted(snapshot.registry):
        entry = snapshot.registry[feature_id]
        if body is not None and body not in entry.body_scope:
            continue
        if dimension is not None and entry.dimension_id != dimension.upper():
            continue
        if role is not None:
            support = entry.roles.get(role)
            if support is None or not support.supported:
                continue
        features.append(_feature_payload(entry, snapshot.coverage.get(feature_id)))
    return {
        "registry_version": REGISTRY_VERSION,
        "data_version": data_version,
        "filters": {"body": body, "dimension": dimension, "role": role},
        "features": features,
        "join_audit": snapshot.join,
        "audit_summary": audit_summary(snapshot),
        "notes": [
            "A stored observation may keep score_role=evidence_only while the registry promotes "
            "only explicitly gated query features. Source observations are never modified.",
            "Coverage counts are exact-ID-joined, gate-passing values in this database, not "
            "six-dimension completeness or global coverage.",
            "Mean/Q05/Q95 from SoilGrids belong to one measurement family; they are not three "
            "independent observations.",
        ],
    }


def feature_detail_payload(
    snapshot: RegistrySnapshot, feature_id: str, *, data_version: str
) -> dict[str, object]:
    entry = snapshot.registry.get(feature_id)
    if entry is None:
        raise AppError("not_found", f"Feature {feature_id} is not registered.", status_code=404)
    return {
        "registry_version": REGISTRY_VERSION,
        "data_version": data_version,
        "feature": _feature_payload(entry, snapshot.coverage.get(feature_id)),
        "join_audit": snapshot.join,
    }


def _d01_materialized(
    candidate: CandidateEntity, entry: FeatureEligibility
) -> MaterializedFeature | None:
    observation = candidate.features.get(entry.feature_id)
    if observation is None:
        return None
    if observation.method_version not in entry.method_versions:
        return None
    if not math.isfinite(observation.value):
        return None
    return MaterializedFeature(
        feature_id=entry.feature_id,
        area_id=candidate.location_id,
        body="Earth",
        value=observation.value,
        unit=observation.unit,
        method_version=observation.method_version,
        observation_id=f"d01:{candidate.location_id}:{entry.feature_id}",
        dimension_id="D01",
        selector=dict(entry.selector),
        support=dict(entry.support),
        dataset_ids=(candidate.dataset_id,),
        quality_flags=tuple(candidate.quality_flags),
        uncertainty=None,
    )


def _logical_feature(
    *,
    location_id: str,
    entry: FeatureEligibility,
    materialized: MaterializedFeature | None,
    candidate: CandidateEntity,
) -> MaterializedFeature | None:
    if materialized is not None:
        return materialized
    if entry.dimension_id == "D01":
        return _d01_materialized(candidate, entry)
    return None


def _candidate_features(
    snapshot: RegistrySnapshot,
    entry_by_id: Mapping[str, FeatureEligibility],
) -> list[MultiDimCandidate]:
    features_by_area: dict[str, dict[str, MaterializedFeature]] = {}
    for (area_id, feature_id), value in snapshot.outcome.values.items():
        features_by_area.setdefault(area_id, {})[feature_id] = value
    result: list[MultiDimCandidate] = []
    for candidate in snapshot.candidates:
        values: dict[str, MaterializedFeature] = {}
        for feature_id in entry_by_id:
            values[feature_id] = _logical_feature(
                location_id=candidate.location_id,
                entry=entry_by_id[feature_id],
                materialized=features_by_area.get(candidate.location_id, {}).get(feature_id),
                candidate=candidate,
            )  # type: ignore[assignment]
        result.append(
            MultiDimCandidate(
                location_id=candidate.location_id,
                body="Earth",
                features={key: value for key, value in values.items() if value is not None},
            )
        )
    return result


def _filter_values(
    *,
    snapshot: RegistrySnapshot,
    filters: Mapping[str, Mapping[str, float | None]],
) -> tuple[list[MultiDimCandidate], list[MultiDimExclusion], dict[str, object]]:
    applied: dict[str, object] = {}
    for feature_id, constraint in filters.items():
        entry = snapshot.registry.get(feature_id)
        if entry is None:
            raise AppError(
                "unknown_feature",
                f"Unsupported filter feature id: {feature_id}.",
                status_code=422,
            )
        if not entry.roles[ROLE_HARD_FILTER].supported:
            raise AppError(
                "feature_role_not_filterable",
                f"{feature_id} is not registered as a hard filter. "
                f"{entry.roles[ROLE_HARD_FILTER].reason or 'It is evidence only.'}",
                status_code=422,
            )
        low = constraint.get("min")
        high = constraint.get("max")
        if low is not None and high is not None and low > high:
            raise AppError(
                "invalid_filter_range",
                f"filter min must be <= max for {feature_id}.",
                status_code=422,
            )
        if entry.min_value is not None and low is not None and low < entry.min_value:
            raise AppError(
                "filter_out_of_domain",
                f"filter min for {feature_id} is below {entry.min_value} {entry.unit}.",
                status_code=422,
            )
        if entry.max_value is not None and high is not None and high > entry.max_value:
            raise AppError(
                "filter_out_of_domain",
                f"filter max for {feature_id} is above {entry.max_value} {entry.unit}.",
                status_code=422,
            )
        applied[feature_id] = {
            "min": low,
            "max": high,
            "unit": entry.unit,
            "dimension_id": entry.dimension_id,
            "support": entry.support,
        }
    if not filters:
        return _candidate_features(snapshot, snapshot.registry), [], applied

    entry_by_id = {feature_id: snapshot.registry[feature_id] for feature_id in snapshot.registry}
    candidates = _candidate_features(snapshot, entry_by_id)
    kept: list[MultiDimCandidate] = []
    excluded: list[MultiDimExclusion] = []
    for candidate in candidates:
        reasons: list[str] = []
        missing: list[str] = []
        for feature_id, constraint in filters.items():
            value = candidate.features.get(feature_id)
            if value is None:
                missing.append(feature_id)
                reasons.append(f"missing_filter:{feature_id}")
                continue
            if value.unit != snapshot.registry[feature_id].unit:
                reasons.append(f"unit_mismatch:{feature_id}")
                continue
            low = constraint.get("min")
            high = constraint.get("max")
            if (low is not None and value.value < low) or (high is not None and value.value > high):
                reasons.append(f"filter:{feature_id}")
        if reasons:
            excluded.append(
                MultiDimExclusion(
                    location_id=candidate.location_id,
                    reasons=tuple(reasons),
                    missing_features=tuple(missing),
                )
            )
        else:
            kept.append(candidate)
    return kept, excluded, applied


def _reference_targets(reference: ReferenceEntity) -> dict[str, tuple[float, float]]:
    targets: dict[str, tuple[float, float]] = {}
    for feature in FEATURES:
        observation = reference.features.get(feature.id)
        if observation and observation.method_version == feature.method_version:
            targets[feature.id] = (observation.value, 1.0)
    if not targets:
        raise AppError(
            "incompatible_method_version",
            f"Reference {reference.reference_id} has no D01 observations compatible with the "
            "current registry.",
            status_code=409,
        )
    return targets


def _evidence_records(
    *,
    snapshot: RegistrySnapshot,
    area_id: str,
    limit: int,
) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for row in snapshot.rows:
        if str(row.get("area_id")) != area_id:
            continue
        entry = snapshot.registry.get(str(row.get("feature_id")))
        if entry is None:
            continue
        records.append(
            {
                "observation_id": row.get("observation_id"),
                "dimension_id": row.get("dimension_code"),
                "feature_id": row.get("feature_id"),
                "label": entry.label,
                "state": row.get("state"),
                "value_kind": row.get("value_kind"),
                "value": row.get("value"),
                "unit": row.get("unit"),
                "method_version": row.get("method_version"),
                "stored_score_role": row.get("score_role"),
                "registry_score_role": entry.score_role,
                "legal_roles": [role for role, support in entry.roles.items() if support.supported],
                "support": row.get("spatial_support"),
                "quality_flags": row.get("quality_flags") or [],
                "dataset_ids": row.get("dataset_ids") or [],
                "note": (
                    "Evidence/context record. It is not part of the weighted distance unless the "
                    "registry exposes a separate, explicitly gated query feature."
                ),
            }
        )
        if len(records) >= limit:
            break
    return records


def execute_multidimensional_search(
    *,
    repo: CandidateRepositoryFull,
    snapshot: RegistrySnapshot,
    raw_targets: Mapping[str, tuple[float, float]],
    filters: Mapping[str, Mapping[str, float | None]],
    reference_id: str | None,
    candidate_body: str,
    dimension_weights: Mapping[str, tuple[float, bool]] | None = None,
    scoring_version: str | None = None,
    limit: int,
    offset: int,
    include_evidence: bool,
    evidence_limit: int,
) -> dict[str, object]:
    if limit < 1 or limit > MAX_MULTIDIM_LIMIT:
        raise AppError(
            "invalid_limit",
            f"limit must be between 1 and {MAX_MULTIDIM_LIMIT}.",
            status_code=422,
        )
    if offset < 0:
        raise AppError("invalid_offset", "offset must be >= 0.", status_code=422)
    if evidence_limit < 1 or evidence_limit > 50:
        raise AppError(
            "invalid_evidence_limit",
            "evidence_limit must be between 1 and 50.",
            status_code=422,
        )
    if candidate_body not in ("Earth", "Moon", "Mars"):
        raise AppError(
            "invalid_body", "candidate_body must be Earth, Moon or Mars.", status_code=422
        )
    if candidate_body != "Earth":
        raise AppError(
            "no_fine_candidates_for_body",
            f"The fine candidate index contains Earth windows only; {candidate_body} has "
            "planetary references and typed evidence but no fine similarity candidates.",
            status_code=409,
        )

    reference: ReferenceEntity | None = None
    merged: dict[str, tuple[float, float]] = dict(raw_targets)
    if reference_id:
        reference = repo.get_reference(reference_id)
        if reference is None:
            raise AppError(
                "not_found",
                f"Reference {reference_id} is not available in this index.",
                status_code=404,
            )
        merged = {**_reference_targets(reference), **merged}
    if not merged:
        raise AppError(
            "empty_target",
            "Provide at least one target feature or a reference_id.",
            status_code=422,
        )

    source_body = reference.body if reference is not None else candidate_body
    dim_controls = (
        {
            dimension_id: DimensionControl(weight=weight, enabled=enabled)
            for dimension_id, (weight, enabled) in dimension_weights.items()
        }
        if dimension_weights
        else None
    )
    try:
        targets, effective_scoring = resolve_targets(
            raw_targets=merged,
            registry=snapshot.registry,
            candidate_body=source_body,
            dimension_weights=dim_controls,
            scoring_version=scoring_version,
        )
    except MultiDimError as exc:
        raise AppError(exc.code, exc.message, status_code=422, details=exc.details) from exc

    candidates, filter_excluded, filters_applied = _filter_values(
        snapshot=snapshot, filters=filters
    )
    outcome = rank_candidates(
        targets=targets,
        candidates=candidates,
        registry=snapshot.registry,
        dimension_weights=dim_controls,
        scoring_version=effective_scoring,
    )
    total_eligible = len(outcome.matches)
    window = outcome.matches[offset : offset + limit]
    by_id = {candidate.location_id: candidate for candidate in snapshot.candidates}

    results: list[dict[str, object]] = []
    for rank, match in enumerate(window, start=offset + 1):
        candidate = by_id[match.location_id]
        dimension_contributions = {
            dimension: {
                **{
                    "contribution": values.get("contribution", contribution)
                    if isinstance(values, dict)
                    else contribution,
                    "distance": values.get("distance", math.sqrt(contribution))
                    if isinstance(values, dict)
                    else math.sqrt(contribution),
                }
            }
            for dimension, values, contribution in (
                (
                    dimension,
                    getattr(match, "dimension_scores", {}).get(dimension, {}),
                    contribution,
                )
                for dimension, contribution in sorted(match.dimension_contributions.items())
            )
        }
        # prefer rich dimension_scores when present
        if getattr(match, "dimension_scores", None):
            dimension_contributions = {
                dimension: dict(values)
                for dimension, values in sorted(match.dimension_scores.items())
            }
        result: dict[str, object] = {
            "rank": rank,
            "location_id": candidate.location_id,
            "display_name": candidate.display_name,
            "body": "Earth",
            "lat": candidate.lat,
            "lon": candidate.lon,
            "bbox": candidate.bbox.as_dict(),
            "distance": match.distance,
            "distance_scope": getattr(outcome, "distance_scope", DISTANCE_SCOPE_LEGACY),
            "dimension_contributions": dimension_contributions,
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
        if include_evidence:
            result["evidence_records"] = _evidence_records(
                snapshot=snapshot, area_id=candidate.location_id, limit=evidence_limit
            )
        results.append(result)

    all_excluded: list[dict[str, object]] = [
        {
            "location_id": item.location_id,
            "reason": ",".join(item.reasons),
            "reasons": list(item.reasons),
            "missing_features": list(item.missing_features),
            "incompatible_features": list(item.incompatible_features),
        }
        for item in filter_excluded
    ]
    all_excluded.extend(
        {
            "location_id": item.location_id,
            "reason": ",".join(item.reasons),
            "reasons": list(item.reasons),
            "missing_features": list(item.missing_features),
            "incompatible_features": list(item.incompatible_features),
        }
        for item in outcome.excluded
    )

    scoring_version_out = getattr(outcome, "scoring_version", SCORING_VERSION_LEGACY)
    dim_controls_out: dict[str, dict[str, object]] = {}
    requested_dims = sorted({snapshot.registry[t.feature_id].dimension_id for t in targets})
    for dimension_id in requested_dims:
        if dim_controls and dimension_id in dim_controls:
            control = dim_controls[dimension_id]
            dim_controls_out[dimension_id] = {
                "weight": control.weight,
                "enabled": control.enabled,
                "active": control.enabled and control.weight > 0,
            }
        else:
            dim_controls_out[dimension_id] = {
                "weight": 1.0,
                "enabled": True,
                "active": True,
                "implicit": True,
            }
    if scoring_version_out == SCORING_VERSION_DIMENSION_RMS:
        formula = (
            "For each compared feature f in dimension d: z_df = (x_df - t_df) / s_df. "
            "q_d = Σ(v_df * z_df^2) / Σ(v_df) over compared positive-weight features in d. "
            "distance = sqrt(Σ(w_d * q_d) / Σ(w_d)) over enabled dimensions with positive w_d."
        )
        note = (
            "Feature-count invariant at the dimension level: each active dimension contributes by "
            "its feature-weighted mean squared normalised delta, then dimensions are combined by "
            "user dimension weights. Disabled/zero-weight dimensions and zero-weight features do "
            "not contribute and do not exclude candidates."
        )
    else:
        formula = (
            "distance = sqrt(Σ(v_f * ((x_f - t_f) / s_f)^2) / Σ(v_f)) over compared "
            "positive-weight features (legacy flat feature-weighted Euclidean)."
        )
        note = (
            "Legacy scoring preserved for backward compatibility. Provide dimension_weights or "
            "scoring_version='dimension-weighted-rms-v1' for dimension-level feature-count "
            "invariant aggregation."
        )

    return {
        "registry_version": REGISTRY_VERSION,
        "data_version": repo.data_version(),
        "data_snapshot_id": repo.data_version(),
        "scoring": {
            "version": scoring_version_out,
            "formula": formula,
            "dimension_controls": dim_controls_out,
            "active_dimensions": list(getattr(outcome, "active_dimensions", requested_dims)),
            "note": note,
        },
        "candidate_body": candidate_body,
        "source_body": source_body,
        "cross_body_gate": {
            "reference_body": reference.body if reference is not None else None,
            "candidate_body": candidate_body,
            "target_body": source_body,
            "rule": (
                "Every requested feature must be registered for the target/source body. A Moon or "
                "Mars reference cannot borrow Earth-only D06 targets; no cross-body equivalence "
                "is created."
            ),
        },
        "requested_features": [target.feature_id for target in targets],
        "requested_targets": {
            target.feature_id: {
                "value": target.value,
                "weight": target.weight,
                "unit": snapshot.registry[target.feature_id].unit,
                "dimension_id": snapshot.registry[target.feature_id].dimension_id,
                "body_scope": list(snapshot.registry[target.feature_id].body_scope),
                "measurement_family": snapshot.registry[target.feature_id].measurement_family,
            }
            for target in targets
        },
        "filters_applied": filters_applied,
        "candidate_pool_size": len(snapshot.candidates),
        "filtered_candidate_count": len(candidates),
        "eligible_candidate_count": total_eligible,
        "excluded_candidate_count": len(all_excluded),
        "total_matched": total_eligible,
        "offset": offset,
        "limit": limit,
        "returned": len(results),
        "next_offset": offset + len(results) if offset + len(results) < total_eligible else None,
        "coverage": coverage_payload(
            targets=targets,
            registry=snapshot.registry,
            candidates=candidates,
            matches=outcome.matches,
            dimension_weights=dim_controls,
        ),
        "excluded": all_excluded,
        "results": results,
        "limitations": BASE_LIMITATIONS,
    }


__all__ = [
    "RegistrySnapshot",
    "assemble_snapshot",
    "audit_summary",
    "execute_multidimensional_search",
    "feature_detail_payload",
    "registry_payload",
]
