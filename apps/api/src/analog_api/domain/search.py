from __future__ import annotations

import json
import math
from datetime import UTC, datetime

from analog_api.domain.entities import CandidateEntity, ReferenceEntity
from analog_api.domain.errors import AppError
from analog_api.domain.features import (
    ANALYSIS_PAYLOAD,
    AOI_LIMITS,
    CONTEXT_FEATURE_BY_ID,
    CONTRACT_VERSION,
    FEATURE_BY_ID,
    FEATURES,
    METHOD_VERSION,
    QUERY_VERSION,
    context_feature_payloads,
    feature_payloads,
)
from analog_api.domain.scoring import (
    ScoringCandidate,
    ScoringError,
    normalize_target,
    rank_candidates,
)
from analog_api.ports.repository import CandidateRepositoryFull
from analog_api.schemas import SearchRequest

COVERAGE_DESCRIPTION = (
    "Search is limited to the Earth windows persisted in this index (curated seed windows plus "
    "any user-requested on-demand AOIs already analyzed). It is not a global survey, not an "
    "exhaustive top-10, and not a base-site recommendation."
)

BASE_LIMITATIONS = [
    "Only local terrain shape is compared. Gravity, atmosphere, radiation, regolith, "
    "mineralogy, temperature and geological history are not compared.",
    "Terrain-shape similarity is not physical equivalence and the distance is not a "
    "scientific similarity percentage.",
    "Indexed coverage is a small curated subset of Earth; absence from results does not "
    "mean a location is a poor analog.",
]


def base_payload(repo: CandidateRepositoryFull) -> dict[str, object]:
    return {
        "contract_version": CONTRACT_VERSION,
        "method_version": METHOD_VERSION,
        "data_version": repo.data_version(),
    }


def coverage_payload(repo: CandidateRepositoryFull, candidates: list[CandidateEntity]):
    if not candidates:
        return {
            "label": "indexed_subset",
            "indexed_candidate_count": 0,
            "geographic_bbox": None,
            "description": COVERAGE_DESCRIPTION,
            "data_kinds": [],
            "origins": {"indexed": 0, "on_demand": 0},
        }
    return {
        "label": "indexed_subset",
        "indexed_candidate_count": len(candidates),
        "geographic_bbox": {
            "west": min(c.bbox.west for c in candidates),
            "south": min(c.bbox.south for c in candidates),
            "east": max(c.bbox.east for c in candidates),
            "north": max(c.bbox.north for c in candidates),
        },
        "description": COVERAGE_DESCRIPTION,
        "data_kinds": sorted({c.data_kind for c in candidates}),
        "origins": {
            "indexed": sum(1 for c in candidates if c.origin != "on_demand"),
            "on_demand": sum(1 for c in candidates if c.origin == "on_demand"),
        },
    }


def capabilities_payload(repo: CandidateRepositoryFull) -> dict[str, object]:
    candidates = list(repo.list_candidates())
    references = list(repo.list_references())
    bodies = ["custom"] + sorted({reference.body for reference in references})
    datasets = list(repo.list_datasets())
    dataset_kinds = {(dataset.dataset_id, dataset.source_kind) for dataset in datasets}
    provider_status = {
        "copernicus-dem-glo90": {
            "status": "verified"
            if any(kind == "earth_dem" for _, kind in dataset_kinds)
            else "not_loaded",
            "role": "terrain similarity",
        },
        "esa-worldcover-2021-v200": {
            "status": "verified"
            if any(kind == "landcover" for _, kind in dataset_kinds)
            else "not_loaded",
            "role": "Earth land-cover context only (not mineralogy)",
        },
    }
    return {
        "contract_version": CONTRACT_VERSION,
        "method_version": METHOD_VERSION,
        "data_snapshot_id": repo.data_version(),
        "analysis": ANALYSIS_PAYLOAD,
        "features": feature_payloads(),
        "context_features": context_feature_payloads(),
        "supported_bodies": bodies,
        "indexed_candidate_count": len(candidates),
        "coverage": coverage_payload(repo, candidates),
        "available_reference_ids": [reference.reference_id for reference in references],
        "aoi_analysis": {**AOI_LIMITS, "provider_status": provider_status},
        "data_version": repo.data_version(),
    }


def _reference_target(reference: ReferenceEntity) -> tuple[dict[str, float], dict[str, float]]:
    target: dict[str, float] = {}
    weights: dict[str, float] = {}
    for feature in FEATURES:
        observation = reference.features.get(feature.id)
        if observation and observation.method_version == feature.method_version:
            target[feature.id] = observation.value
            weights[feature.id] = 1.0
    if not target:
        raise AppError(
            "incompatible_method_version",
            f"Reference {reference.reference_id} has no observations compatible with "
            f"{METHOD_VERSION}.",
            status_code=409,
        )
    return target, weights


def _filter_value(candidate: CandidateEntity, feature_id: str) -> float | None:
    """Observed value used by a hard filter, or None when it cannot be evaluated.

    Similarity features count only with a finite value from the registry's method version, so a
    filter never mixes method versions. Missing values are never imputed.
    """
    if feature_id in FEATURE_BY_ID:
        observation = candidate.features.get(feature_id)
        if (
            observation is None
            or observation.method_version != FEATURE_BY_ID[feature_id].method_version
            or not math.isfinite(observation.value)
        ):
            return None
        return observation.value
    context = candidate.context_observations.get(feature_id)
    return None if context is None else context.value


def _caveats(candidate: CandidateEntity) -> list[str]:
    caveats = [
        "Terrain shape only; not a claim of physical or environmental equivalence.",
    ]
    if candidate.data_kind == "synthetic":
        caveats.insert(0, "DEMO / SYNTHETIC fixture values - not a real terrain observation.")
    if candidate.origin == "on_demand":
        caveats.insert(
            0,
            "User-requested on-demand AOI; terrain support differs from the fixed indexed "
            "windows when the AOI footprint is smaller than 1.98 km.",
        )
    if candidate.quality_flags:
        caveats.append("Source quality flags: " + ", ".join(candidate.quality_flags) + ".")
    if candidate.context_observations:
        caveats.append(
            "Earth land-cover context is informational only and is not lunar mineralogy, "
            "geology, or a similarity dimension."
        )
    if candidate.valid_fraction < 0.8:
        caveats.append(
            f"Only {candidate.valid_fraction:.0%} of the analysis window has valid elevation "
            "data; treat this record as lower confidence."
        )
    return caveats


def dimensions_payload() -> dict[str, object]:
    return {
        "contract_version": CONTRACT_VERSION,
        "method_version": METHOD_VERSION,
        "dimensions": [
            {
                "dimension_id": "terrain_shape",
                "label": "Terrain shape (similarity)",
                "description": (
                    "Local terrain-shape metrics extracted from metric-CRS DEM windows at a "
                    "fixed 90 m / 1.98 km support. These enter the weighted distance."
                ),
                "score_role": "similarity",
                "features": feature_payloads(),
            },
            {
                "dimension_id": "land_cover_context",
                "label": "Earth land-cover context (not scored)",
                "description": (
                    "ESA WorldCover 2021 v200 class fractions for Earth AOIs. Context/filter "
                    "only; never mineralogy, geology, or a similarity dimension."
                ),
                "score_role": "context_only",
                "features": context_feature_payloads(),
            },
        ],
        "context_features": context_feature_payloads(),
    }


def execute_search(repo: CandidateRepositoryFull, request: SearchRequest) -> dict[str, object]:
    indexed_count = repo.candidate_count()
    if indexed_count == 0:
        raise AppError(
            "no_indexed_data",
            "The Earth candidate index is empty. Run the documented seed/ingestion steps first.",
            status_code=409,
        )

    reference: ReferenceEntity | None = None
    if request.reference_id:
        reference = repo.get_reference(request.reference_id)
        if reference is None:
            raise AppError(
                "not_found",
                f"Reference {request.reference_id} is not available in this index.",
                status_code=404,
            )
        if request.body not in ("custom", reference.body):
            raise AppError(
                "body_mismatch",
                f"Requested body {request.body} does not match reference body {reference.body}.",
                status_code=422,
            )
        target, default_weights = _reference_target(reference)
        weights = {**default_weights, **request.weights}
    else:
        target = {feature_id: spec.value for feature_id, spec in request.target.items()}
        weights = {feature_id: spec.weight for feature_id, spec in request.target.items()}

    try:
        clean_target, clean_weights, _ = normalize_target(target, weights)
    except ScoringError as exc:
        raise AppError(exc.code, exc.message, status_code=422, details=exc.details) from exc

    candidates = list(repo.list_candidates())
    scoring: list[ScoringCandidate] = []
    excluded: list[dict[str, str]] = []
    for candidate in candidates:
        filter_failures: list[str] = []
        for feature_id, constraint in request.filters.items():
            value = _filter_value(candidate, feature_id)
            if value is None:
                prefix = "missing_metric" if feature_id in FEATURE_BY_ID else "missing_context"
                filter_failures.append(f"{prefix}:{feature_id}")
                continue
            if (constraint.min is not None and value < constraint.min) or (
                constraint.max is not None and value > constraint.max
            ):
                filter_failures.append(f"filter:{feature_id}")
        if filter_failures:
            excluded.append(
                {"location_id": candidate.location_id, "reason": ",".join(filter_failures)}
            )
            continue
        values: dict[str, float] = {}
        incompatible = False
        for feature_id in clean_target:
            observation = candidate.features.get(feature_id)
            if observation is None:
                continue
            if observation.method_version != FEATURE_BY_ID[feature_id].method_version:
                incompatible = True
                break
            if not math.isfinite(observation.value):
                continue
            values[feature_id] = observation.value
        if incompatible:
            excluded.append(
                {
                    "location_id": candidate.location_id,
                    "reason": "incompatible_method_version",
                }
            )
            continue
        scoring.append(ScoringCandidate(location_id=candidate.location_id, values=values))

    try:
        outcome = rank_candidates(
            target=clean_target, weights=clean_weights, candidates=scoring, limit=request.limit
        )
    except ScoringError as exc:
        raise AppError(exc.code, exc.message, status_code=422, details=exc.details) from exc

    by_id = {candidate.location_id: candidate for candidate in candidates}
    results = []
    for rank, match in enumerate(outcome.matches, start=1):
        candidate = by_id[match.location_id]
        dimension_contributions: dict[str, float] = {}
        for metric in match.metrics:
            dimension = FEATURE_BY_ID[metric.feature_id].dimension_id
            dimension_contributions[dimension] = (
                dimension_contributions.get(dimension, 0.0) + metric.contribution
            )
        results.append(
            {
                "rank": rank,
                "location_id": candidate.location_id,
                "display_name": candidate.display_name,
                "lat": candidate.lat,
                "lon": candidate.lon,
                "bbox": candidate.bbox.as_dict(),
                "distance": match.distance,
                "dimension_distances": {
                    dimension: math.sqrt(contribution)
                    for dimension, contribution in dimension_contributions.items()
                },
                "metrics": [
                    {
                        "feature_id": metric.feature_id,
                        "unit": metric.unit,
                        "target": metric.target,
                        "candidate": metric.candidate,
                        "delta": metric.delta,
                        "scale": metric.scale,
                        "weight": metric.weight,
                        "normalized_delta": metric.normalized_delta,
                        "contribution": metric.contribution,
                    }
                    for metric in match.metrics
                ],
                "dataset_ids": [candidate.dataset_id],
                "data_kind": candidate.data_kind,
                "quality_flags": list(candidate.quality_flags),
                "caveats": _caveats(candidate),
            }
        )

    all_excluded = excluded + [
        {"location_id": exclusion.location_id, "reason": exclusion.reason}
        for exclusion in outcome.excluded
    ]
    return {
        "contract_version": CONTRACT_VERSION,
        "method_version": METHOD_VERSION,
        "query_version": QUERY_VERSION,
        "data_version": repo.data_version(),
        "data_snapshot_id": repo.data_version(),
        "coverage": coverage_payload(repo, candidates),
        "coverage_label": "indexed_subset",
        "requested_features": list(clean_target),
        "filters_applied": {
            feature_id: {"min": constraint.min, "max": constraint.max}
            for feature_id, constraint in request.filters.items()
        },
        "indexed_candidate_count": indexed_count,
        "eligible_candidate_count": outcome.eligible_count,
        "excluded_candidate_count": len(all_excluded),
        "excluded": all_excluded,
        "results": results,
        "limitations": BASE_LIMITATIONS,
    }


def location_payload(repo: CandidateRepositoryFull, location_id: str) -> dict[str, object]:
    candidate = repo.get_candidate(location_id)
    if candidate is None:
        raise AppError("not_found", f"Location {location_id} was not found.", status_code=404)
    dataset_ids = [candidate.dataset_id]
    for observation in candidate.context_observations.values():
        if observation.dataset_id and observation.dataset_id not in dataset_ids:
            dataset_ids.append(observation.dataset_id)
    datasets = []
    for dataset_id in dataset_ids:
        dataset = repo.get_dataset(dataset_id)
        if dataset is None:
            continue
        datasets.append(
            {
                "dataset_id": dataset.dataset_id,
                "provider": dataset.provider,
                "product_id": dataset.product_id,
                "title": dataset.title,
                "url": dataset.url,
                "license": dataset.license,
                "license_url": dataset.license_url,
                "access_timestamp": dataset.access_timestamp,
                "source_crs": dataset.source_crs,
                "native_resolution_m": dataset.native_resolution_m,
                "processing_version": dataset.processing_version,
                "checksum": dataset.checksum,
                "source_kind": dataset.source_kind,
                "notes": dataset.notes,
            }
        )

    context_observations = []
    for feature_id, observation in candidate.context_observations.items():
        definition = CONTEXT_FEATURE_BY_ID.get(feature_id)
        if definition is None:
            continue
        context_observations.append(
            {
                "feature_id": feature_id,
                "label": definition.label,
                "value": observation.value,
                "unit": observation.unit,
                "method_version": observation.method_version,
                "source_resolution_m": observation.source_resolution_m,
                "valid_fraction": observation.valid_fraction,
                "quality": observation.quality,
                "class_code": observation.class_code,
                "provider": observation.provider,
                "score_role": "context_only",
                "dataset_id": observation.dataset_id,
            }
        )

    limitations = [
        "Metrics describe terrain shape at a 2 km / 90 m analysis scale derived from the "
        "cited elevation source; they are not a full environmental characterization.",
        "The source DEM is a digital surface model where vegetation or buildings may be "
        "present; this can bias slope and roughness.",
    ]
    if candidate.data_kind == "synthetic":
        limitations.insert(0, "DEMO / SYNTHETIC fixture: values are not observations.")
    if candidate.notes:
        limitations.append(f"Source note: {candidate.notes}")
    if context_observations:
        limitations.append(
            "ESA WorldCover class fractions are Earth land-cover context at a 2021 epoch. "
            "They are not mineral composition, not geology, and not comparable to lunar "
            "surface materials."
        )

    features = []
    for feature in FEATURES:
        observation = candidate.features.get(feature.id)
        if observation is None:
            continue
        features.append(
            {
                "feature_id": feature.id,
                "label": feature.label,
                "value": observation.value,
                "unit": observation.unit,
                "method_version": observation.method_version,
                "extraction_scale_m": observation.extraction_scale_m,
                "valid_fraction": observation.valid_fraction,
                "quality": observation.quality,
                "scale": feature.scale,
                "description": feature.description,
            }
        )

    return {
        "location_id": candidate.location_id,
        "display_name": candidate.display_name,
        "body": "Earth",
        "lat": candidate.lat,
        "lon": candidate.lon,
        "bbox": candidate.bbox.as_dict(),
        "window_size_m": candidate.window_size_m,
        "analysis_resolution_m": candidate.analysis_resolution_m,
        "crs": candidate.crs,
        "valid_fraction": candidate.valid_fraction,
        "data_kind": candidate.data_kind,
        "quality_flags": list(candidate.quality_flags),
        "notes": candidate.notes,
        "area_km2": candidate.area_km2,
        "geometry": candidate.geometry,
        "origin": candidate.origin,
        "features": features,
        "context_observations": context_observations,
        "datasets": datasets,
        "known_limitations": limitations,
        "contract_version": CONTRACT_VERSION,
        "method_version": METHOD_VERSION,
        "data_version": repo.data_version(),
        "data_snapshot_id": repo.data_version(),
    }


def parse_context_target(raw: str | None) -> dict[str, float] | None:
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AppError(
            "validation_error", f"target query parameter is not valid JSON: {exc}", status_code=422
        ) from exc
    if not isinstance(payload, dict):
        raise AppError(
            "validation_error", "target query parameter must be a JSON object.", status_code=422
        )
    target: dict[str, float] = {}
    for feature_id, value in payload.items():
        definition = FEATURE_BY_ID.get(feature_id)
        if definition is None:
            raise AppError(
                "unknown_feature",
                f"Unsupported feature id in target: {feature_id}",
                status_code=422,
            )
        if isinstance(value, dict):
            value = value.get("value")
        if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            raise AppError(
                "invalid_target_value",
                f"Target for {feature_id} must be a finite number.",
                status_code=422,
            )
        if not definition.min_value <= float(value) <= definition.max_value:
            raise AppError(
                "target_out_of_domain",
                f"Target for {feature_id} is outside its valid domain.",
                status_code=422,
            )
        target[feature_id] = float(value)
    return target or None


def context_payload(
    repo: CandidateRepositoryFull, location_id: str, raw_target: str | None
) -> dict[str, object]:
    location = location_payload(repo, location_id)
    target = parse_context_target(raw_target)
    comparisons = []
    for feature in location["features"]:
        feature_target = target.get(feature["feature_id"]) if target else None
        delta = feature["value"] - feature_target if feature_target is not None else None
        comparisons.append(
            {
                "feature_id": feature["feature_id"],
                "unit": feature["unit"],
                "target": feature_target,
                "observed": feature["value"],
                "delta": delta,
                "scale": feature["scale"],
                "normalized_delta": (delta / feature["scale"] if delta is not None else None),
            }
        )

    source_facts = [
        f"Window centre: {location['lat']:.4f}, {location['lon']:.4f} (WGS84).",
        f"Analysis window {location['window_size_m']:.0f} m; analysis resolution "
        f"{location['analysis_resolution_m']:.0f} m; projected CRS {location['crs']}.",
    ]
    for dataset in location["datasets"]:
        source_facts.append(
            f"Source {dataset['dataset_id']}: {dataset['provider']} / "
            f"{dataset['product_id']} ({dataset['url']}); license {dataset['license']}; "
            f"accessed {dataset['access_timestamp']}."
        )
    for feature in location["features"]:
        source_facts.append(
            f"{feature['feature_id']} = {feature['value']} {feature['unit']} "
            f"(quality={feature['quality']}, method={feature['method_version']}, "
            f"scale={feature['extraction_scale_m']} m)."
        )
    for context in location["context_observations"]:
        source_facts.append(
            f"{context['feature_id']} = {context['value']} {context['unit']} "
            f"(score_role=context_only, provider={context['provider']}, "
            f"method={context['method_version']}, source_resolution="
            f"{context['source_resolution_m']} m, class_code={context['class_code']})."
        )

    limitations = list(location["known_limitations"]) + BASE_LIMITATIONS
    return {
        "export_version": "1.0.0",
        "note": (
            "Grounded structured export for external AI assistance. It contains stored source "
            "metadata and deterministic comparisons only; no narrative or external events."
        ),
        "generated_at": datetime.now(UTC).isoformat(),
        "location": location,
        "query": target,
        "source_facts": source_facts,
        "computed_comparisons": comparisons,
        "limitations": limitations,
    }
