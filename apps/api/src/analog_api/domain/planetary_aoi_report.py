"""Engine-shaped status/result report for planetary AOI jobs (Issue #170, Goal D).

The API layer owns the durable job (``analysis_jobs``), cache identity and typed
persistence. This module turns those persisted facts into one honest report:

* D01 is ``available`` only when the job persisted numeric D01 observations.
* D02-D06 are ``available``/``evidence`` only when exact area-linked typed records
  exist, otherwise they are explicitly ``no_coverage``, ``not_indexed`` or
  ``unsupported``. Missing values are never zero-filled and Earth observations are
  never copied onto a Moon/Mars AOI.
* ``full_aoi`` is true only when the analysed envelope equals the requested
  envelope. A committed-cell union or a central-window read is reported as such
  instead of being presented as a full-AOI analysis.

The report is storage independent in the sense that the caller passes the job,
request, result and typed observations; it never queries a database by itself.
D1's geometry module (``domain.planetary_aoi_engine``) owns bbox normalization and
quota policy. This module only reports the persisted outcome and does not re-shrink
or recentre the request.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from functools import lru_cache
from typing import Any

from analog_pipelines.coarse_features import COARSE_FEATURE_BY_ID

from analog_api.domain.coverage_status import (
    DIMENSION_CODES,
    DIMENSION_LABELS,
    load_source_inventory,
)

CONTRACT_VERSION = "planetary-aoi-engine-1.0.0"
D01 = "D01"
_UNAVAILABLE_STATUSES = {"no_coverage", "not_indexed", "unsupported"}
_BBOX_KEYS = ("west", "south", "east", "north")
_USABLE_AVAILABILITY = {"verified", "accessible"}
_USABLE_DATA_STATUS = {"existing_observed", "existing_modeled"}
_FINAL_STATUSES = {"completed", "partial", "failed"}

LIMITATIONS = (
    "D02-D06 are only reported from exact area-linked typed observations. No Earth "
    "value is copied onto a Moon/Mars AOI and a missing value is never zero-filled.",
    "A browsable planetary basemap is not evidence of global six-dimension coverage.",
    "The report does not produce a cross-body similarity score or ranking.",
)


@lru_cache(maxsize=1)
def _inventory_by_body_and_dimension() -> dict[tuple[str, str], tuple[dict[str, Any], ...]]:
    inventory = load_source_inventory()
    index: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for dimension in inventory.get("dimensions", []):
        code = str(dimension.get("code") or "")
        if not code:
            continue
        for body, body_info in (dimension.get("bodies") or {}).items():
            sources = tuple(
                source for source in (body_info.get("sources") or []) if isinstance(source, Mapping)
            )
            index[(str(body), code)] = sources
    return index


def declared_sources(body: str, dimension_code: str) -> tuple[dict[str, Any], ...]:
    """Return the declared source inventory for one body/dimension pair."""
    return _inventory_by_body_and_dimension().get((body, dimension_code), ())


def _is_usable_source(source: Mapping[str, Any]) -> bool:
    availability = str(source.get("availability") or "").lower()
    data_status = str(source.get("data_status") or "").lower()
    return availability in _USABLE_AVAILABILITY or data_status in _USABLE_DATA_STATUS


def _source_payload(source: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "source_id": source.get("source_id"),
        "provider": source.get("provider"),
        "product": source.get("product"),
        "url": source.get("url"),
        "license": source.get("license"),
        "license_url": source.get("license_url"),
        "version": source.get("version", "unknown"),
        "native_resolution": source.get("native_resolution"),
        "spatial_support": source.get("spatial_support"),
        "temporal_support": source.get("temporal_support"),
        "depth_support": source.get("depth_support"),
        "availability": source.get("availability"),
        "data_status": source.get("data_status"),
        "comparability": source.get("comparability"),
        "qa": source.get("qa"),
        "unknown_fields": source.get("unknown_fields") or [],
    }


def _bbox(value: Any) -> dict[str, float] | None:
    if not isinstance(value, Mapping):
        return None
    try:
        return {key: float(value[key]) for key in _BBOX_KEYS}
    except (KeyError, TypeError, ValueError):
        return None


def _bbox_equal(left: Mapping[str, float] | None, right: Mapping[str, float] | None) -> bool:
    if left is None or right is None:
        return False
    return all(abs(float(left[key]) - float(right[key])) <= 1e-6 for key in _BBOX_KEYS)


def _float_or_none(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _int_or_none(value: Any) -> int | None:
    number = _float_or_none(value)
    return int(number) if number is not None else None


def _observation_feature(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "feature_id": str(row.get("feature_id")),
        "value": row.get("value"),
        "unit": row.get("unit"),
        "method_version": row.get("method_version"),
        "state": row.get("state"),
        "score_role": row.get("score_role"),
        "quality_flags": list(row.get("quality_flags") or []),
        "uncertainty": row.get("uncertainty"),
        "observation_id": row.get("observation_id"),
    }


def _fallback_d01_features(
    result: Mapping[str, Any] | None, observations: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    observed = {
        str(row.get("feature_id")): row
        for row in observations
        if str(row.get("dimension_code")) == D01
    }
    features: list[dict[str, Any]] = []
    for _feature_id, row in sorted(observed.items()):
        features.append(_observation_feature(row))
    if features:
        return features
    for feature_id, value in sorted(((result or {}).get("features") or {}).items()):
        definition = COARSE_FEATURE_BY_ID.get(feature_id)
        number = _float_or_none(value)
        if definition is None or number is None:
            continue
        features.append(
            {
                "feature_id": feature_id,
                "value": number,
                "unit": definition.unit,
                "method_version": definition.method_version,
                "state": "detected",
                "score_role": "evidence_only",
                "quality_flags": ["on_demand_planetary_aoi", "not_a_cross_body_score"],
                "uncertainty": None,
                "observation_id": None,
            }
        )
    return features


def _spatial_support(observations: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    for row in observations:
        support = row.get("spatial_support")
        if isinstance(support, Mapping) and support:
            return dict(support)
    return {}


def _d01_support(
    *,
    requested_bbox: Mapping[str, float] | None,
    analysis_bbox: Mapping[str, float] | None,
    full_aoi: bool | None,
    analysis_scope: str,
    result: Mapping[str, Any] | None,
    area: Mapping[str, Any] | None,
    observations: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    support = _spatial_support(observations)
    result = result or {}
    area = area or {}
    return {
        "kind": (
            "requested_aoi"
            if full_aoi
            else "selected_cell_union"
            if analysis_bbox is not None
            else analysis_scope
        ),
        "full_aoi": full_aoi,
        "analysis_scope": analysis_scope,
        "requested_bbox": dict(requested_bbox) if requested_bbox else None,
        "analysis_bbox": dict(analysis_bbox) if analysis_bbox else None,
        "resolution_m": _float_or_none(support.get("resolution_m")),
        "source_resolution_m": _float_or_none(support.get("source_resolution_m")),
        "window_size_m": _float_or_none(support.get("window_size_m")),
        "area_km2": _float_or_none(area.get("area_km2") or result.get("area_km2")),
        "valid_fraction": _float_or_none(support.get("valid_fraction")),
        "cell_count": _int_or_none(
            ((result.get("dimension_support") or {}).get(D01) or {}).get("cell_count")
        ),
        "temporal_support": (
            dict(observations[0].get("temporal_support") or {}) if observations else {}
        ),
        "depth_support": (dict(observations[0].get("depth_support") or {}) if observations else {}),
    }


def _dimension_from_observations(
    *,
    code: str,
    rows: Sequence[Mapping[str, Any]],
    body: str,
) -> dict[str, Any]:
    detected = [row for row in rows if str(row.get("state")) == "detected"]
    if detected:
        status = "available"
        value_origin = "observed_derived"
        reason = None
    elif rows and all(str(row.get("state")) == "no_coverage" for row in rows):
        status = "no_coverage"
        value_origin = "not_measured"
        reason = "Every exact area-linked typed record for this dimension is no_coverage."
    else:
        status = "evidence"
        value_origin = "stored_evidence"
        reason = "Only evidence/unknown-state typed records are linked to this exact AOI."
    first = rows[0]
    provenance = {
        "status": "stored_typed_observation",
        "dataset_ids": sorted(
            {str(dataset_id) for row in rows for dataset_id in (row.get("dataset_ids") or [])}
        ),
        "source_asset_ids": sorted(
            {str(asset_id) for row in rows for asset_id in (row.get("source_asset_ids") or [])}
        ),
        "method_version": first.get("method_version"),
        "declared_sources": [],
        "note": "Exact area-linked typed-v2 observations in this database.",
    }
    support = {
        "kind": "stored_typed_support",
        "spatial_support": first.get("spatial_support"),
        "temporal_support": first.get("temporal_support") or {},
        "depth_support": first.get("depth_support") or {},
        "body": body,
    }
    return {
        "dimension_code": code,
        "label": DIMENSION_LABELS[code],
        "status": status,
        "value_origin": value_origin,
        "reason": reason,
        "method_version": first.get("method_version"),
        "features": [_observation_feature(row) for row in rows],
        "provenance": provenance,
        "support": support,
        "limitations": list(LIMITATIONS),
    }


def _unavailable_dimension(
    *,
    code: str,
    body: str,
    sources: Sequence[Mapping[str, Any]],
    engine_support: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    engine_support = engine_support or {}
    engine_status = str(engine_support.get("status") or "").lower()
    engine_reason = engine_support.get("reason")
    if engine_status in _UNAVAILABLE_STATUSES:
        status = engine_status
        value_origin = "not_measured"
        reason = str(engine_reason or f"The analysis engine reported {status} for {code}.")
    elif not sources:
        status = "unsupported"
        reason = (
            f"No source is declared for {code} on {body}. This AOI is not assigned a "
            "measurement from another body or dimension."
        )
        value_origin = "none"
    elif not any(_is_usable_source(source) for source in sources):
        status = "unsupported"
        reason = (
            f"Only planned/not-fetched {code} sources are declared for {body}; no usable "
            "on-demand source exists for this AOI."
        )
        value_origin = "declared_source"
    else:
        status = "not_indexed"
        reason = (
            f"A usable {code} source is declared for {body}, but no exact area-linked typed "
            "observation exists for this AOI in this database. No value is interpolated from "
            "another area."
        )
        value_origin = "declared_source"
    declared = [_source_payload(source) for source in sources]
    return {
        "dimension_code": code,
        "label": DIMENSION_LABELS[code],
        "status": status,
        "value_origin": value_origin,
        "reason": reason,
        "method_version": None,
        "features": [],
        "provenance": {
            "status": status,
            "dataset_ids": [],
            "source_asset_ids": [],
            "method_version": None,
            "declared_sources": declared,
            "note": (
                "Declared source inventory only. It records what exists for the body and "
                "what is missing for this exact AOI; it is not a measurement."
            ),
        },
        "support": {
            "kind": "declared_source_inventory",
            "spatial_support": sources[0].get("spatial_support") if sources else None,
            "temporal_support": sources[0].get("temporal_support") if sources else None,
            "depth_support": sources[0].get("depth_support") if sources else None,
            "body": body,
        },
        "limitations": list(LIMITATIONS),
    }


def _d01_dimension(
    *,
    job_status: str,
    error_code: str | None,
    error_message: str | None,
    result: Mapping[str, Any] | None,
    observations: Sequence[Mapping[str, Any]],
    body: str,
    support: Mapping[str, Any],
    sources: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    features = _fallback_d01_features(result, observations)
    result = result or {}
    d01_result = (result.get("dimension_support") or {}).get(D01) or {}
    if job_status in {"queued", "running"}:
        status = "pending"
        value_origin = "not_measured"
        reason = f"The D01 analysis is {job_status}; no value is available yet."
    elif features:
        status = "available"
        value_origin = "observed_derived"
        reason = None
    elif job_status == "failed":
        status = (
            "no_coverage"
            if error_code in {"out_of_coverage", "no_coverage"}
            else "not_indexed"
            if error_code == "not_indexed"
            else "unsupported"
        )
        value_origin = "not_measured"
        reason = error_message or error_code or "The D01 analysis failed."
    else:
        status = "not_indexed"
        value_origin = "not_measured"
        reason = "The job completed without a persisted D01 observation for this AOI."

    dataset_ids = sorted(
        {
            str(source.get("dataset_id"))
            for source in (result.get("sources") or [])
            if source.get("dataset_id")
        }
        | {str(dataset_id) for row in observations for dataset_id in (row.get("dataset_ids") or [])}
    )
    source_asset_ids = sorted(
        {str(asset_id) for row in observations for asset_id in (row.get("source_asset_ids") or [])}
    )
    provenance = {
        "status": "engine_reported" if features else status,
        "datasets": list(result.get("sources") or []),
        "dataset_ids": dataset_ids,
        "source_asset_ids": source_asset_ids,
        "method_version": result.get("method_version") or d01_result.get("method_version"),
        "source_method_version": d01_result.get("source_method_version"),
        "checksum": result.get("checksum"),
        "declared_sources": [_source_payload(source) for source in sources],
        "note": (
            "D01 provenance is the provider dataset/asset reported by the analysis engine. "
            "It is not an Earth similarity source."
        ),
    }
    return {
        "dimension_code": D01,
        "label": DIMENSION_LABELS[D01],
        "status": status,
        "value_origin": value_origin,
        "reason": reason,
        "method_version": provenance["method_version"],
        "features": features,
        "provenance": provenance,
        "support": dict(support),
        "limitations": list(LIMITATIONS),
    }


def _scope_and_full_aoi(
    *,
    job_status: str,
    requested_bbox: Mapping[str, float] | None,
    analysis_bbox: Mapping[str, float] | None,
) -> tuple[str, bool | None]:
    if job_status in {"queued", "running"}:
        return "pending", None
    if job_status == "failed":
        return "failed", None
    if requested_bbox is None or analysis_bbox is None:
        return "unknown", None
    if _bbox_equal(requested_bbox, analysis_bbox):
        return "full_requested_aoi", True
    return "selected_cell_union", False


def _coverage_payload(
    *,
    job_status: str,
    error_code: str | None,
    requested_bbox: Mapping[str, float] | None,
    analysis_bbox: Mapping[str, float] | None,
    full_aoi: bool | None,
    analysis_scope: str,
    result: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if job_status in {"queued", "running"}:
        status = "pending"
    elif job_status == "failed":
        status = "no_coverage" if error_code == "out_of_coverage" else "failed"
    elif full_aoi:
        status = "full_aoi"
    else:
        status = "partial_aoi"
    return {
        "status": status,
        "analysis_scope": analysis_scope,
        "full_aoi": full_aoi,
        "requested_bbox": dict(requested_bbox) if requested_bbox else None,
        "analysis_bbox": dict(analysis_bbox) if analysis_bbox else None,
        "note": (result or {}).get("coverage_note"),
    }


def build_engine_report(
    *,
    job_status: str,
    error_code: str | None,
    error_message: str | None,
    request: Mapping[str, Any] | None,
    result: Mapping[str, Any] | None,
    area: Mapping[str, Any] | None,
    observations: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Build the engine report shared by the create, status and result endpoints."""
    request = request or {}
    result = result or {}
    area = area or {}
    body = str(result.get("body") or area.get("body") or request.get("body") or "")
    requested_bbox = _bbox(request.get("requested_bbox")) or _bbox(request.get("bbox"))
    analysis_bbox = _bbox(area.get("bbox")) or _bbox(result.get("analysis_bbox"))
    analysis_scope, full_aoi = _scope_and_full_aoi(
        job_status=job_status,
        requested_bbox=requested_bbox,
        analysis_bbox=analysis_bbox,
    )
    d01_support = _d01_support(
        requested_bbox=requested_bbox,
        analysis_bbox=analysis_bbox,
        full_aoi=full_aoi,
        analysis_scope=analysis_scope,
        result=result,
        area=area,
        observations=observations,
    )
    dimensions: dict[str, Any] = {
        D01: _d01_dimension(
            job_status=job_status,
            error_code=error_code,
            error_message=error_message,
            result=result,
            observations=observations,
            body=body,
            support=d01_support,
            sources=declared_sources(body, D01),
        )
    }
    for code in DIMENSION_CODES:
        if code == D01:
            continue
        rows = [row for row in observations if str(row.get("dimension_code")) == code]
        if rows:
            dimensions[code] = _dimension_from_observations(code=code, rows=rows, body=body)
        else:
            dimensions[code] = _unavailable_dimension(
                code=code,
                body=body,
                sources=declared_sources(body, code),
                engine_support=(result.get("dimension_support") or {}).get(code),
            )

    dimension_status = {
        code: {
            "status": entry["status"],
            "value_origin": entry["value_origin"],
            "reason": entry["reason"],
            "feature_count": len(entry["features"]),
        }
        for code, entry in dimensions.items()
    }
    warnings = list(result.get("warnings") or [])
    if full_aoi is False:
        warnings.append(
            "The analysed envelope differs from the requested envelope; D01 covers the "
            "selected cell union, not the exact requested AOI. full_aoi is false."
        )
    if full_aoi is None and job_status in _FINAL_STATUSES:
        warnings.append(
            "No persisted analysed envelope is available, so full-AOI coverage cannot be "
            "claimed for this job."
        )
    return {
        "body": body,
        "coordinate_frame": (
            result.get("coordinate_frame")
            or area.get("coordinate_frame")
            or request.get("coordinate_frame")
        ),
        "requested_bbox": requested_bbox,
        "analysis_bbox": analysis_bbox,
        "full_aoi": full_aoi,
        "analysis_scope": analysis_scope,
        "coverage": _coverage_payload(
            job_status=job_status,
            error_code=error_code,
            requested_bbox=requested_bbox,
            analysis_bbox=analysis_bbox,
            full_aoi=full_aoi,
            analysis_scope=analysis_scope,
            result=result,
        ),
        "method_version": result.get("method_version"),
        "coverage_note": result.get("coverage_note"),
        "data_version": result.get("data_version"),
        "dimensions": dimensions,
        "dimension_status": dimension_status,
        "provenance": dimensions[D01]["provenance"],
        "support": d01_support,
        "warnings": warnings,
    }


__all__ = [
    "CONTRACT_VERSION",
    "LIMITATIONS",
    "build_engine_report",
    "declared_sources",
]
