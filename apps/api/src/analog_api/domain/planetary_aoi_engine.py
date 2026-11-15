"""Planetary AOI Engine contract and report assembly (Issue #170, Goal D).

Additive to the Issue #132 planetary AOI contract. This module provides:
- bbox/body coordinate normalization for Earth/Moon/Mars (signed and 0-360 input,
  antimeridian crossing, polar-inclusive latitudes) in the declared body frame;
- footprint/area/pixel/quota calculations with explicit requested-vs-analysis
  reporting (never silent shrink/recenter);
- deterministic job identity helpers and provenance shaping;
- D01-D06 status resolution from real persisted analysis/evidence only
  (no zero-fill, no fabricated measurements); and
- ``build_engine_report`` used by ``/api/v1/planetary/aoi`` routes.

Heavy D01 raster computation is delegated to injected providers/analyzers (A/B/C
interfaces) through the existing ``validate``/``analyze`` processor contract.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from pyproj import Geod

from analog_api.domain.errors import AppError
from analog_api.domain.planetary import (
    COORDINATE_CONTRACT_VERSION,
    PLANETARY_BODIES,
)
from analog_api.domain.planetary import (
    get_body as get_planetary_body,
)
from analog_api.domain.planetary_aoi_geometry import (
    AOI_ENGINE_GEOMETRY_VERSION,
    BODY_DEFAULT_LIMITS,
    BODY_DEFAULT_PIXEL_SIZE_M,
    EARTH_BBOX_CRS,
    SUPPORTED_AOI_BODIES,
    AoiGeometry,
    AoiLimits,
    BodyGeometry,
    MetricEnvelope,
    body_geometry,
    validate_aoi_geometry,
)

CONTRACT_VERSION = "planetary-aoi-engine-1.0.0"
ENGINE_VERSION = "planetary-aoi-engine-170-1.0.0"

DIMENSION_CODES = ("D01", "D02", "D03", "D04", "D05", "D06")
DIMENSION_LABELS = {
    "D01": "Terrain & Landforms",
    "D02": "Geology & Composition",
    "D03": "Thermal Environment",
    "D04": "Water, Ice & Volatiles",
    "D05": "Radiation & Exposure",
    "D06": "Regolith & Surface",
}
SUPPORTED_BODIES = ("Earth", "Moon", "Mars")
BODY_FRAMES = {
    "Earth": "wgs84_geographic",
    "Moon": "lunar_planetocentric",
    "Mars": "mars_planetocentric",
}
BODY_CRS = {
    "Earth": "EPSG:4326",
    "Moon": "+proj=longlat +R=1737400.0 +no_defs",
    "Mars": "+proj=longlat +R=3396190.0 +no_defs",
}

# Conservative, explicit quotas (can be tightened by providers/analyzers; never widened silently).
DEFAULT_QUOTAS: dict[str, dict[str, float | int]] = {
    "Earth": {
        "max_side_m": 10_000.0,
        "min_side_m": 90.0,
        "max_area_km2": 25.0,
        "max_span_deg": 180.0,
        "max_pixels": 4_000_000,
    },
    "Moon": {
        "max_side_m": 25_000.0,
        "min_side_m": 250.0,
        "max_area_km2": 400.0,
        "max_span_deg": 180.0,
        "max_pixels": 4_000_000,
    },
    "Mars": {
        "max_side_m": 25_000.0,
        "min_side_m": 250.0,
        "max_area_km2": 400.0,
        "max_span_deg": 180.0,
        "max_pixels": 4_000_000,
    },
}

EXPLICIT_UNAVAILABLE = {"no_coverage", "not_indexed", "unsupported"}
EARTH_GEOD = Geod(ellps="WGS84")


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _finite(value: Any, name: str) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError) as exc:
        raise AppError("invalid_bbox", f"{name} must be a finite number.", status_code=422) from exc
    if not math.isfinite(out):
        raise AppError("invalid_bbox", f"{name} must be finite.", status_code=422)
    return out


def normalize_longitude(lon: float, convention: str = "signed") -> float:
    """Normalize a longitude to the canonical signed domain [-180, 180).

    ``convention`` describes the *input* convention:
    - ``signed``: input may be any finite longitude, wrapped to [-180, 180).
    - ``360``: input is interpreted as 0-360/east-positive style and converted to signed.
    """
    value = _finite(lon, "longitude")
    if convention == "360":
        value = value % 360.0
        if value >= 180.0:
            value -= 360.0
        return (value + 180.0) % 360.0 - 180.0
    if convention != "signed":
        raise AppError(
            "invalid_longitude_convention",
            "lon_convention must be 'signed' or '360'.",
            status_code=422,
        )
    return (value + 180.0) % 360.0 - 180.0


def body_geod(body: str) -> Geod:
    if body == "Earth":
        return EARTH_GEOD
    info = get_planetary_body(body)
    return Geod(a=float(info.radius_m), b=float(info.polar_radius_m))


def normalize_bbox(
    bbox: Mapping[str, Any],
    *,
    body: str,
    lon_convention: str = "signed",
) -> dict[str, Any]:
    """Normalize a bbox into the body's signed planetocentric contract.

    Supports 0-360 or signed input, antimeridian crossing (west > east after
    normalization), and polar latitudes within [-90, 90]. Never interprets Moon/Mars
    as Earth WGS84.
    """
    if body not in SUPPORTED_BODIES:
        raise AppError(
            "unsupported_body",
            f"body must be one of {', '.join(SUPPORTED_BODIES)}.",
            status_code=422,
        )
    if body in PLANETARY_BODIES:
        get_planetary_body(body)
    if lon_convention not in {"signed", "360"}:
        raise AppError(
            "invalid_longitude_convention",
            "lon_convention must be 'signed' or '360'.",
            status_code=422,
        )

    west = normalize_longitude(_finite(bbox.get("west"), "west"), lon_convention)
    east = normalize_longitude(_finite(bbox.get("east"), "east"), lon_convention)
    south = _finite(bbox.get("south"), "south")
    north = _finite(bbox.get("north"), "north")

    if not (-90.0 <= south <= 90.0 and -90.0 <= north <= 90.0):
        raise AppError(
            "invalid_bbox", "bbox south/north must be within [-90, 90] degrees.", status_code=422
        )
    if south >= north:
        raise AppError("invalid_bbox", "bbox south must be < north.", status_code=422)
    if west == east:
        raise AppError(
            "invalid_bbox", "bbox must define a non-zero longitude span.", status_code=422
        )

    crosses_antimeridian = west > east
    span = (180.0 - west) + (east + 180.0) if crosses_antimeridian else (east - west)
    if span > 180.0 + 1e-9:
        raise AppError(
            "bbox_too_wide",
            f"bbox longitude span {span:.6f} degrees exceeds 180 degrees.",
            status_code=422,
        )

    def r(v: float) -> float:
        return float(round(float(v), 12))

    out = {
        "west": r(west),
        "south": r(south),
        "east": r(east),
        "north": r(north),
        "body": body,
        "coordinate_frame": BODY_FRAMES[body],
        "crs": BODY_CRS[body],
        "lon_convention": "signed",
        "input_lon_convention": lon_convention,
        "crosses_antimeridian": crosses_antimeridian,
        "longitude_span_deg": r(span),
        "earth_wgs84": body == "Earth",
    }
    if crosses_antimeridian:
        out["pieces"] = [
            {"west": r(west), "south": r(south), "east": 180.0, "north": r(north)},
            {"west": -180.0, "south": r(south), "east": r(east), "north": r(north)},
        ]
    else:
        out["pieces"] = [{"west": r(west), "south": r(south), "east": r(east), "north": r(north)}]
    return out


def bbox_geometry(bbox: Mapping[str, float], crosses_antimeridian: bool) -> dict[str, Any]:
    w, s, e, n = (
        float(bbox["west"]),
        float(bbox["south"]),
        float(bbox["east"]),
        float(bbox["north"]),
    )
    if not crosses_antimeridian:
        return {"type": "Polygon", "coordinates": [[[w, s], [e, s], [e, n], [w, n], [w, s]]]}
    return {
        "type": "MultiPolygon",
        "coordinates": [
            [[[w, s], [180.0, s], [180.0, n], [w, n], [w, s]]],
            [[[-180.0, s], [e, s], [e, n], [-180.0, n], [-180.0, s]]],
        ],
    }


def bbox_area_km2(body: str, bbox: Mapping[str, float], crosses_antimeridian: bool) -> float:
    geod = body_geod(body)
    pieces = (
        [
            {"west": bbox["west"], "south": bbox["south"], "east": 180.0, "north": bbox["north"]},
            {"west": -180.0, "south": bbox["south"], "east": bbox["east"], "north": bbox["north"]},
        ]
        if crosses_antimeridian
        else [bbox]
    )
    total = 0.0
    for piece in pieces:
        lons = [piece["west"], piece["east"], piece["east"], piece["west"]]
        lats = [piece["south"], piece["south"], piece["north"], piece["north"]]
        area_m2, _ = geod.polygon_area_perimeter(lons, lats)
        total += abs(float(area_m2))
    return total / 1e6


def bbox_side_lengths_m(
    body: str, bbox: Mapping[str, float], crosses_antimeridian: bool, span_deg: float
) -> tuple[float, float]:
    geod = body_geod(body)
    mid_lat = (float(bbox["south"]) + float(bbox["north"])) / 2.0
    mid_lon = normalize_longitude(float(bbox["west"]) + span_deg / 2.0, "signed")
    east_unwrapped = float(bbox["east"]) + 360.0 if crosses_antimeridian else float(bbox["east"])
    width_m = abs(float(geod.inv(float(bbox["west"]), mid_lat, east_unwrapped, mid_lat)[2]))
    height_m = abs(float(geod.inv(mid_lon, float(bbox["south"]), mid_lon, float(bbox["north"]))[2]))
    return width_m, height_m


def quota_for_body(body: str, *, resolution_m: float | None = None) -> dict[str, Any]:
    base = dict(DEFAULT_QUOTAS.get(body, DEFAULT_QUOTAS["Moon"]))
    base["body"] = body
    base["analysis_resolution_m"] = resolution_m
    if resolution_m and resolution_m > 0:
        # Effective pixel quota is informative; actual enforcement may be tighter in providers.
        base["max_pixels"] = int(base.get("max_pixels", 4_000_000))
    return base


def enforce_quota(
    *,
    body: str,
    bbox: Mapping[str, float],
    crosses_antimeridian: bool,
    span_deg: float,
    area_km2: float,
    resolution_m: float | None = None,
) -> dict[str, Any]:
    quota = quota_for_body(body, resolution_m=resolution_m)
    width_m, height_m = bbox_side_lengths_m(body, bbox, crosses_antimeridian, span_deg)
    min_side = float(quota["min_side_m"])
    max_side = float(quota["max_side_m"])
    max_area = float(quota["max_area_km2"])
    max_span = float(quota["max_span_deg"])
    if min(width_m, height_m) < min_side:
        raise AppError(
            "aoi_too_small", f"AOI sides must be >= {min_side:.0f} m on {body}.", status_code=422
        )
    if max(width_m, height_m) > max_side:
        raise AppError(
            "aoi_too_large", f"AOI sides must be <= {max_side:.0f} m on {body}.", status_code=422
        )
    if area_km2 > max_area:
        raise AppError(
            "aoi_too_large", f"AOI area must be <= {max_area:.3f} km2 on {body}.", status_code=422
        )
    if span_deg > max_span:
        raise AppError(
            "bbox_too_wide", f"bbox longitude span exceeds {max_span} degrees.", status_code=422
        )
    pixel_estimate = None
    if resolution_m and resolution_m > 0:
        pw = max(1, int(math.ceil(width_m / resolution_m)))
        ph = max(1, int(math.ceil(height_m / resolution_m)))
        pixel_estimate = pw * ph
        if pixel_estimate > int(quota["max_pixels"]):
            raise AppError(
                "quota_exceeded",
                f"Requested AOI requires about {pixel_estimate} pixels, exceeding quota {quota['max_pixels']}.",
                status_code=422,
            )
    quota.update(
        {
            "width_m": width_m,
            "height_m": height_m,
            "area_km2": area_km2,
            "pixel_estimate": pixel_estimate,
        }
    )
    return quota


def compute_cache_key(
    *,
    body: str,
    bbox: Mapping[str, float],
    requested_features: Sequence[str] | None,
    lon_convention: str,
    endpoint: str = "planetary_aoi_engine",
    extra: Mapping[str, Any] | None = None,
) -> str:
    payload = {
        "endpoint": endpoint,
        "body": body,
        "bbox": {k: float(bbox[k]) for k in ("west", "south", "east", "north")},
        "lon_convention": lon_convention,
        "requested_features": sorted(set(requested_features or [])),
        "engine_version": ENGINE_VERSION,
        "contract_version": CONTRACT_VERSION,
        "extra": dict(extra or {}),
    }
    return hashlib.sha256(_canonical(payload).encode()).hexdigest()


def _as_float_bbox(value: Any) -> dict[str, float] | None:
    if not isinstance(value, Mapping):
        return None
    try:
        return {k: float(round(float(value[k]), 12)) for k in ("west", "south", "east", "north")}
    except Exception:
        return None


def _bboxes_equal(a: Mapping[str, float] | None, b: Mapping[str, float] | None) -> bool:
    if a is None or b is None:
        return False
    return all(
        math.isclose(float(a[k]), float(b[k]), rel_tol=0.0, abs_tol=1e-9)
        for k in ("west", "south", "east", "north")
    )


def _analysis_scope_and_full_aoi(
    *,
    job_status: str,
    requested_bbox: dict[str, float] | None,
    analysis_bbox: dict[str, float] | None,
    result: Mapping[str, Any] | None,
) -> tuple[str, bool | None, str]:
    if job_status in {"queued", "running"}:
        return "pending", None, "pending"
    if job_status == "failed" or not result:
        return "failed", None, "no_coverage" if job_status == "failed" else "unknown"
    kind = str(result.get("analysis_kind") or "").lower()
    equal = _bboxes_equal(requested_bbox, analysis_bbox)
    # Explicit provider declaration or exact bbox coverage: the requested AOI was
    # fully analyzed (including tiling over all intersecting available cells/data).
    if equal and (result.get("full_aoi") is not False):
        if "live" in kind and "bounded" in kind and result.get("full_aoi") is not True:
            return "live_bounded_read", False, "partial_aoi"
        return "full_requested_aoi", True, "full_aoi"
    # Heuristic/contracted scopes.
    if "live" in kind and "bounded" in kind:
        return "live_bounded_read", False, "partial_aoi"
    if (
        "committed" in kind
        or "union" in kind
        or "cell" in kind
        or (analysis_bbox and requested_bbox and not equal)
    ):
        return "selected_cell_union", False, "partial_aoi"
    return "partial_aoi", False, "partial_aoi"


def _dimension_entry(
    *,
    dimension: str,
    status: str,
    value_origin: str,
    reason: str | None = None,
    method_version: str | None = None,
    features: list[dict[str, Any]] | None = None,
    provenance: dict[str, Any] | None = None,
    support: dict[str, Any] | None = None,
    limitations: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "dimension_code": dimension,
        "label": DIMENSION_LABELS.get(dimension, dimension),
        "status": status,
        "value_origin": value_origin,
        "reason": reason,
        "method_version": method_version,
        "features": list(features or []),
        "provenance": dict(provenance or {}),
        "support": dict(support or {}),
        "limitations": list(limitations or []),
    }


def _declared_sources_for_dimension(dimension: str, body: str | None) -> list[dict[str, Any]]:
    # Explicit declared-source metadata basis; no fabricated measurements.
    if dimension == "D01":
        return [{"source_kind": "terrain_dem", "body": body, "status": "provider_dependent"}]
    return [
        {
            "source_kind": "declared_inventory",
            "dimension": dimension,
            "body": body,
            "note": "Declared source inventory; real on-demand measurement not available for this AOI unless explicitly observed.",
        }
    ]


def _map_provider_dim_status(raw: Any) -> tuple[str, str | None]:
    """Map provider/engine dimension status to the API engine enum."""
    if not isinstance(raw, Mapping):
        return "not_indexed", None
    status = str(raw.get("status") or "").lower()
    reason = raw.get("reason") or raw.get("reason_code")
    if status in {"present", "available", "derived", "measured"}:
        return "available", None
    if status in {"present_evidence", "evidence", "evidence_only", "stored_evidence"}:
        return "evidence", str(
            reason
        ) if reason else "Stored evidence exists but was not measured on demand for this AOI."
    if status in {"no_coverage", "absent"}:
        return "no_coverage", str(
            reason
        ) if reason else "No real coverage for this dimension in the analyzed scope."
    if status in {"unsupported", "unsupported_for_on_demand_analysis"}:
        return "unsupported", str(
            reason
        ) if reason else "Dimension is not supported for on-demand analysis in this engine."
    if status in {"not_indexed", "not_indexed_for_aoi", "unavailable"}:
        return "not_indexed", str(
            reason
        ) if reason else "No indexed real data is available for this AOI."
    return "not_indexed", str(reason) if reason else "No explicit real-data status available."


def build_engine_report(
    *,
    job_status: str,
    error_code: str | None,
    error_message: str | None,
    request: Mapping[str, Any],
    result: Mapping[str, Any] | None,
    area: Mapping[str, Any] | None,
    observations: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Assemble the additive engine response payload from persisted job artifacts.

    The report is derived only from request/job/result/typed records and explicit
    provider statuses. Missing real data is surfaced as explicit states, never
    fabricated.
    """
    body = str((result or {}).get("body") or request.get("body") or "")
    frame = (
        str(
            (result or {}).get("coordinate_frame")
            or request.get("coordinate_frame")
            or BODY_FRAMES.get(body, "")
            or ""
        )
        or None
    )
    requested_bbox = (
        _as_float_bbox(request.get("requested_bbox"))
        or _as_float_bbox(request.get("bbox"))
        or _as_float_bbox((result or {}).get("requested_bbox"))
    )
    result_bbox = _as_float_bbox((result or {}).get("bbox") or (result or {}).get("analysis_bbox"))
    if result_bbox is None and isinstance(area, Mapping):
        result_bbox = _as_float_bbox(area.get("bbox"))
    analysis_bbox = result_bbox or requested_bbox

    analysis_scope, full_aoi, coverage_status = _analysis_scope_and_full_aoi(
        job_status=job_status,
        requested_bbox=requested_bbox,
        analysis_bbox=analysis_bbox,
        result=result,
    )

    # D01 observations/features from typed records (preferred persisted truth) or result payload.
    d01_obs = [obs for obs in observations if str(obs.get("dimension_code")) == "D01"]
    d01_features: list[dict[str, Any]] = []
    if d01_obs:
        for obs in sorted(d01_obs, key=lambda o: str(o.get("feature_id") or "")):
            if obs.get("value") is None:
                continue
            d01_features.append(
                {
                    "feature_id": obs.get("feature_id"),
                    "value": obs.get("value"),
                    "unit": obs.get("unit"),
                    "method_version": obs.get("method_version"),
                    "observation_id": obs.get("observation_id"),
                    "state": obs.get("state"),
                }
            )
    elif isinstance(result, Mapping):
        for fid, val in sorted((result.get("features") or {}).items()):
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                d01_features.append({"feature_id": fid, "value": float(val)})

    dataset_ids = sorted(
        {
            ds.get("dataset_id")
            for ds in (result or {}).get("sources", [])
            if isinstance(ds, Mapping) and ds.get("dataset_id")
        }
    )
    if not dataset_ids and d01_obs:
        dataset_ids = sorted({ds for obs in d01_obs for ds in (obs.get("dataset_ids") or []) if ds})

    provider_dim_support = (
        (result or {}).get("dimension_support") if isinstance(result, Mapping) else {}
    )
    if not isinstance(provider_dim_support, Mapping):
        provider_dim_support = {}

    warnings = list((result or {}).get("warnings") or []) if isinstance(result, Mapping) else []
    method_version = (result or {}).get("method_version") if isinstance(result, Mapping) else None

    # Build D01 dimension.
    if job_status in {"queued", "running"}:
        d01 = _dimension_entry(
            dimension="D01",
            status="pending",
            value_origin="not_measured",
            reason="D01 analysis is pending for this job.",
            features=[],
            provenance={"declared_sources": _declared_sources_for_dimension("D01", body)},
            support={"kind": "pending", "analysis_bbox": analysis_bbox},
            limitations=["Result will be available after the job completes."],
        )
    elif job_status == "failed" or not d01_features:
        d01_status = (
            "no_coverage"
            if error_code == "out_of_coverage" or coverage_status == "no_coverage"
            else "unsupported"
        )
        d01 = _dimension_entry(
            dimension="D01",
            status=d01_status,
            value_origin="none",
            reason=error_message
            or (result or {}).get("coverage_note")
            or "No D01 value is available for this AOI.",
            features=[],
            provenance={
                "declared_sources": _declared_sources_for_dimension("D01", body),
                "error_code": error_code,
            },
            support={
                "kind": analysis_scope if analysis_scope != "failed" else "none",
                "analysis_bbox": analysis_bbox,
            },
            limitations=["No fabricated D01 value is returned."],
        )
    else:
        support_kind = (
            "requested_aoi"
            if full_aoi
            else (
                "selected_cell_union"
                if analysis_scope == "selected_cell_union"
                else "analysis_extent"
            )
        )
        d01_support_raw = (
            provider_dim_support.get("D01") if isinstance(provider_dim_support, Mapping) else {}
        )
        d01_support_raw = d01_support_raw if isinstance(d01_support_raw, Mapping) else {}
        d01 = _dimension_entry(
            dimension="D01",
            status="available",
            value_origin="observed_derived",
            reason=None
            if full_aoi
            else "D01 values are derived from the analyzed extent; requested and analyzed bbox may differ.",
            method_version=str(method_version)
            if method_version
            else (
                str(d01_support_raw.get("method_version"))
                if d01_support_raw.get("method_version")
                else None
            ),
            features=d01_features,
            provenance={
                "dataset_ids": dataset_ids,
                "method_version": method_version or d01_support_raw.get("method_version"),
                "source_method_version": d01_support_raw.get("source_method_version")
                or (result or {}).get("source_method_version"),
                "analysis_kind": (result or {}).get("analysis_kind"),
                "declared_sources": _declared_sources_for_dimension("D01", body),
            },
            support={
                "kind": support_kind,
                "analysis_bbox": analysis_bbox,
                "requested_bbox": requested_bbox,
                "full_aoi": bool(full_aoi),
                "cell_count": d01_support_raw.get("cell_count"),
                "feature_count": len(d01_features),
            },
            limitations=[]
            if full_aoi
            else [
                "Analysis extent may differ from the requested bbox; see analysis_bbox/analysis_scope."
            ],
        )

    dimensions: dict[str, dict[str, Any]] = {"D01": d01}
    non_d01_obs_by_dim: dict[str, list[Mapping[str, Any]]] = {
        d: [] for d in DIMENSION_CODES if d != "D01"
    }
    for obs in observations:
        dim = str(obs.get("dimension_code"))
        if dim in non_d01_obs_by_dim:
            non_d01_obs_by_dim[dim].append(obs)

    for dim in ("D02", "D03", "D04", "D05", "D06"):
        obs = non_d01_obs_by_dim[dim]
        raw = provider_dim_support.get(dim)
        mapped_status, mapped_reason = _map_provider_dim_status(raw)

        if obs:
            # Real stored evidence exists for this area/scope.
            dims_features = []
            for o in sorted(obs, key=lambda x: str(x.get("feature_id") or "")):
                if o.get("value") is None and o.get("state") != "detected":
                    continue
                dims_features.append(
                    {
                        "feature_id": o.get("feature_id"),
                        "value": o.get("value"),
                        "unit": o.get("unit"),
                        "state": o.get("state"),
                        "observation_id": o.get("observation_id"),
                    }
                )
            dimensions[dim] = _dimension_entry(
                dimension=dim,
                status="evidence",
                value_origin="stored_evidence",
                reason=mapped_reason or "Stored evidence exists for this area; not fabricated.",
                features=dims_features,
                provenance={
                    "declared_sources": _declared_sources_for_dimension(dim, body),
                    "observation_ids": [
                        o.get("observation_id") for o in obs if o.get("observation_id")
                    ],
                    "provider_status": raw if isinstance(raw, Mapping) else None,
                },
                support={"kind": "stored_typed_evidence", "analysis_bbox": analysis_bbox},
                limitations=[
                    "Evidence is reported as stored; not a newly measured on-demand value unless explicitly stated."
                ],
            )
            continue

        # No real observations: must be explicit unavailable/pending state.
        if job_status in {"queued", "running"}:
            # D02-D06 remain explicit unavailable states while queued/running.
            status = "not_indexed"
            reason = (
                mapped_reason
                or "No indexed real data is available for this dimension for the requested AOI."
            )
            value_origin = "not_measured"
        elif mapped_status in EXPLICIT_UNAVAILABLE or mapped_status == "evidence":
            status = mapped_status if mapped_status != "evidence" else "not_indexed"
            reason = mapped_reason
            value_origin = "declared_source" if status in {"no_coverage", "not_indexed"} else "none"
        else:
            status = "not_indexed"
            reason = (
                mapped_reason
                or "No real indexed data is available for this dimension in the analyzed scope."
            )
            value_origin = "declared_source"

        dimensions[dim] = _dimension_entry(
            dimension=dim,
            status=status,
            value_origin=value_origin,
            reason=reason,
            features=[],
            provenance={
                "declared_sources": _declared_sources_for_dimension(dim, body),
                "provider_status": raw if isinstance(raw, Mapping) else None,
            },
            support={"kind": "declared_source_inventory", "analysis_bbox": analysis_bbox},
            limitations=["No fabricated value is returned for this dimension."],
        )

    dimension_status = {
        k: {"status": v["status"], "reason": v.get("reason")} for k, v in dimensions.items()
    }

    # Real pixel/validity accounting from analysis (partial/invalid pixels explicit; never zero-filled).
    d01_dim_support = (
        (result or {}).get("dimension_support", {}).get("D01", {})
        if isinstance(result, Mapping)
        and isinstance((result or {}).get("dimension_support"), Mapping)
        else {}
    )
    embedded_accounting = {}
    if isinstance(d01_dim_support, Mapping):
        embedded_accounting = d01_dim_support.get("pixel_accounting") or {}
        if not isinstance(embedded_accounting, Mapping):
            embedded_accounting = {}
    cell_results = (
        list((result or {}).get("cell_results") or []) if isinstance(result, Mapping) else []
    )
    valid_pixels_total = missing_pixels_total = total_pixels = None
    if cell_results:
        valid_pixels_total = sum(
            int(cr.get("valid_slope_pixels") or 0) for cr in cell_results if isinstance(cr, Mapping)
        )
        missing_pixels_total = sum(
            int(cr.get("missing_slope_pixels") or 0)
            for cr in cell_results
            if isinstance(cr, Mapping)
        )
        total_pixels = valid_pixels_total + missing_pixels_total
    cell_count = len((result or {}).get("cells") or []) if isinstance(result, Mapping) else 0
    if not cell_count and cell_results:
        cell_count = len(cell_results)
    if not cell_count and embedded_accounting.get("cell_count"):
        cell_count = embedded_accounting.get("cell_count")
    if total_pixels is None and embedded_accounting.get("total_pixels") is not None:
        total_pixels = embedded_accounting.get("total_pixels")
    if valid_pixels_total is None and embedded_accounting.get("valid_pixels") is not None:
        valid_pixels_total = embedded_accounting.get("valid_pixels")
    if (
        missing_pixels_total is None
        and embedded_accounting.get("invalid_or_missing_pixels") is not None
    ):
        missing_pixels_total = embedded_accounting.get("invalid_or_missing_pixels")
    pixel_accounting = {
        "valid_fraction": (result or {}).get("valid_fraction")
        if isinstance(result, Mapping) and (result or {}).get("valid_fraction") is not None
        else embedded_accounting.get("valid_fraction"),
        "resolution_m": (result or {}).get("resolution_m")
        if isinstance(result, Mapping) and (result or {}).get("resolution_m") is not None
        else embedded_accounting.get("resolution_m"),
        "source_resolution_m": (result or {}).get("source_resolution_m")
        if isinstance(result, Mapping) and (result or {}).get("source_resolution_m") is not None
        else embedded_accounting.get("source_resolution_m"),
        "window_size_m": (result or {}).get("window_size_m")
        if isinstance(result, Mapping) and (result or {}).get("window_size_m") is not None
        else embedded_accounting.get("window_size_m"),
        "cell_count": cell_count or None,
        "total_pixels": total_pixels,
        "valid_pixels": valid_pixels_total,
        "invalid_or_missing_pixels": missing_pixels_total,
    }
    # Enrich D01 support with actual pixel accounting when available.
    if isinstance(dimensions.get("D01"), dict):
        d01_support = dimensions["D01"].setdefault("support", {})
        d01_support.setdefault("pixel_accounting", pixel_accounting)
        if d01_support.get("cell_count") in (None, 0) and pixel_accounting.get("cell_count"):
            d01_support["cell_count"] = pixel_accounting["cell_count"]

    provenance = {
        "contract_version": CONTRACT_VERSION,
        "engine_version": ENGINE_VERSION,
        "coordinate_contract_version": COORDINATE_CONTRACT_VERSION,
        "dataset_ids": dataset_ids,
        "method_version": method_version,
        "analysis_kind": (result or {}).get("analysis_kind")
        if isinstance(result, Mapping)
        else None,
        "coverage_note": (result or {}).get("coverage_note")
        if isinstance(result, Mapping)
        else None,
        "error_code": error_code,
        "error_message": error_message,
        "body_frame": frame,
        "pixel_accounting": pixel_accounting,
        "no_zero_fill": True,
        "no_silent_shrink": True,
    }

    aoi_geometry = (
        request.get("aoi_geometry") if isinstance(request.get("aoi_geometry"), Mapping) else {}
    )
    analysis_extent = str(request.get("analysis_extent") or analysis_scope)
    support = {
        "kind": "requested_aoi" if full_aoi else analysis_scope,
        "analysis_extent": analysis_extent,
        "full_aoi": bool(full_aoi) if full_aoi is not None else None,
        "shrink_applied": bool(request.get("shrink_applied", False)),
        "requested_bbox": requested_bbox,
        "analysis_bbox": analysis_bbox,
        "crosses_antimeridian": bool(request.get("crosses_antimeridian"))
        if request.get("crosses_antimeridian") is not None
        else (
            bool(request.get("bbox", {}).get("crosses_antimeridian"))
            if isinstance(request.get("bbox"), Mapping)
            else None
        ),
        "bbox_pieces": request.get("bbox_pieces"),
        "metric_envelope": aoi_geometry.get("metric_envelope")
        if isinstance(aoi_geometry, Mapping)
        else None,
        "limits": aoi_geometry.get("limits") if isinstance(aoi_geometry, Mapping) else None,
        "pixel_accounting": pixel_accounting,
    }

    coverage = {
        "status": coverage_status,
        "analysis_scope": analysis_scope,
        "full_aoi": full_aoi,
        "requested_bbox": requested_bbox,
        "analysis_bbox": analysis_bbox,
        "area_km2": (result or {}).get("area_km2")
        if isinstance(result, Mapping)
        else request.get("area_km2"),
        "analysis_area_km2": (result or {}).get("analysis_area_km2")
        if isinstance(result, Mapping)
        else None,
        "pixel_accounting": pixel_accounting,
    }

    return {
        "body": body or None,
        "coordinate_frame": frame,
        "requested_bbox": requested_bbox,
        "analysis_bbox": analysis_bbox,
        "full_aoi": full_aoi,
        "analysis_scope": analysis_scope,
        "coverage": coverage,
        "method_version": method_version,
        "coverage_note": (result or {}).get("coverage_note")
        if isinstance(result, Mapping)
        else None,
        "data_version": (result or {}).get("data_version") if isinstance(result, Mapping) else None,
        "dimensions": dimensions,
        "dimension_status": dimension_status,
        "provenance": provenance,
        "support": support,
        "warnings": warnings,
        "summary": {
            "analyzed_whole_bbox": bool(full_aoi) if full_aoi is not None else None,
            "d01_measured": dimensions["D01"]["status"] == "available",
            "has_unsupported_or_unindexed_dimensions": any(
                dimensions[d]["status"] in EXPLICIT_UNAVAILABLE
                for d in DIMENSION_CODES
                if d != "D01"
            ),
        },
    }


# --- Pluggable D01 analyzer interface (A/B/C integration point) ---


@dataclass(frozen=True, slots=True)
class EngineFootprint:
    body: str
    requested_bbox: dict[str, float]
    analysis_bbox: dict[str, float]
    crosses_antimeridian: bool
    width_m: float
    height_m: float
    area_km2: float
    pixel_width: int | None = None
    pixel_height: int | None = None
    pixel_total: int | None = None
    partial_pixels: int | None = None
    invalid_pixels: int | None = None
    valid_fraction: float | None = None
    analyzed_whole_bbox: bool = False
    notes: tuple[str, ...] = ()

    def to_payload(self) -> dict[str, Any]:
        return {
            "body": self.body,
            "requested_bbox": dict(self.requested_bbox),
            "analysis_bbox": dict(self.analysis_bbox),
            "crosses_antimeridian": self.crosses_antimeridian,
            "width_m": self.width_m,
            "height_m": self.height_m,
            "area_km2": self.area_km2,
            "pixels": {
                "width": self.pixel_width,
                "height": self.pixel_height,
                "total": self.pixel_total,
                "partial": self.partial_pixels,
                "invalid": self.invalid_pixels,
                "valid_fraction": self.valid_fraction,
            },
            "analyzed_whole_bbox": self.analyzed_whole_bbox,
            "notes": list(self.notes),
        }


@dataclass(frozen=True, slots=True)
class D01Analysis:
    features: dict[str, float]
    analysis_bbox: dict[str, float]
    area_km2: float
    method_version: str | None = None
    provider_status: dict[str, Any] = field(default_factory=dict)
    sources: tuple[dict[str, Any], ...] = ()
    warnings: tuple[str, ...] = ()
    valid_fraction: float | None = None
    pixel_counts: dict[str, int | None] = field(default_factory=dict)
    analyzed_whole_bbox: bool = False
    analyzer_id: str | None = None


class D01Analyzer(Protocol):
    """Pluggable D01 engine interface (engines A/B/C or future backends)."""

    analyzer_id: str

    def supports(self, *, body: str, requested_bbox: Mapping[str, float]) -> bool: ...

    def analyze(
        self,
        *,
        body: str,
        requested_bbox: Mapping[str, float],
        requested_features: Sequence[str] | None = None,
    ) -> D01Analysis: ...


class CommittedCoarseD01Engine:
    """Concrete D01 engine backed by committed coarse planetary analysis.

    Integrates with Goal D via both the runtime AOI processor contract
    (``validate``/``analyze(ValidatedAoi) -> AoiResult`` used by
    ``JobRunner``/``create_app(planetary_processor=...)`` and the additive engine
    routes) and the domain ``D01Analyzer`` protocol. D01 terrain features are
    computed from real committed/eligible coarse data selected by intersecting the
    full requested bbox (tiling/union over available cells); no raster-calculation
    kernels are modified here and no values are fabricated or zero-filled. Requests
    outside available committed coverage remain explicit coverage failures/partials.
    """

    analyzer_id = "committed_coarse_d01_engine"

    def __init__(self, *, root: str | None = None, allow_network: bool = False) -> None:
        self.root = root
        self.allow_network = allow_network
        self._provider: Any | None = None

    def _get_provider(self) -> Any:
        if self._provider is None:
            from analog_api.adapters.planetary_aoi import PlanetaryAoiProvider

            self._provider = PlanetaryAoiProvider(root=self.root, allow_network=self.allow_network)
        return self._provider

    # --- runtime AOI processor contract (validate/analyze) ---

    def validate(
        self,
        *,
        body: str,
        bbox: Mapping[str, float],
        requested_features: Sequence[str] | None,
        idempotency_key: str | None,
    ) -> Any:
        return self._get_provider().validate(
            body=body,
            bbox=dict(bbox),
            requested_features=list(requested_features) if requested_features is not None else None,
            idempotency_key=idempotency_key,
        )

    def analyze(
        self,
        request: Any = None,
        *,
        body: str | None = None,
        requested_bbox: Mapping[str, float] | None = None,
        requested_features: Sequence[str] | None = None,
    ) -> Any:
        """Dispatch: processor ``analyze(ValidatedAoi)`` or D01Analyzer protocol call."""
        if request is not None and body is None and requested_bbox is None:
            return self._get_provider().analyze(request)
        if body is None or requested_bbox is None:
            raise TypeError(
                "analyze expects a ValidatedAoi request or body/requested_bbox keywords"
            )
        validated = self.validate(
            body=body,
            bbox=requested_bbox,
            requested_features=requested_features,
            idempotency_key=None,
        )
        result = self._get_provider().analyze(validated)
        analysis_bbox = None
        if getattr(result, "bbox", None) is not None and hasattr(result.bbox, "as_dict"):
            analysis_bbox = result.bbox.as_dict()
        provider_status = dict(getattr(result, "provider_status", {}) or {})
        source_payloads = []
        for ds in getattr(result, "datasets", ()) or ():
            if hasattr(ds, "model_dump"):
                source_payloads.append(ds.model_dump(mode="json"))
            elif hasattr(ds, "as_dict"):
                source_payloads.append(ds.as_dict())
            else:
                try:
                    from dataclasses import asdict, is_dataclass

                    if is_dataclass(ds):
                        source_payloads.append(asdict(ds))
                        continue
                except Exception:
                    pass
                if isinstance(ds, Mapping):
                    source_payloads.append(dict(ds))
                else:
                    source_payloads.append({"value": str(ds)})
        sources = tuple(source_payloads)
        d01_support = (getattr(result, "dimension_support", {}) or {}).get("D01") or {}
        pixel_counts = {}
        pa = d01_support.get("pixel_accounting") if isinstance(d01_support, Mapping) else None
        if isinstance(pa, Mapping):
            pixel_counts = {
                "total": pa.get("total_pixels"),
                "valid": pa.get("valid_pixels"),
                "invalid_or_missing": pa.get("invalid_or_missing_pixels"),
            }
        analyzed_whole_bbox = False
        req = {k: float(requested_bbox[k]) for k in ("west", "south", "east", "north")}
        if isinstance(analysis_bbox, Mapping):
            analyzed_whole_bbox = _bboxes_equal(req, _as_float_bbox(analysis_bbox))
        return D01Analysis(
            features=dict(getattr(result, "features", {}) or {}),
            analysis_bbox=_as_float_bbox(analysis_bbox) or req,
            area_km2=float(getattr(result, "area_km2", 0.0) or 0.0),
            method_version=getattr(result, "method_version", None),
            provider_status=provider_status,
            sources=sources,
            warnings=tuple(getattr(result, "warnings", ()) or ()),
            valid_fraction=getattr(result, "valid_fraction", None),
            pixel_counts=pixel_counts,
            analyzed_whole_bbox=analyzed_whole_bbox,
            analyzer_id=self.analyzer_id,
        )

    # --- D01Analyzer protocol helpers ---

    def supports(self, *, body: str, requested_bbox: Mapping[str, float]) -> bool:
        return body in {"Moon", "Mars"}


__all__ = [
    "AOI_ENGINE_GEOMETRY_VERSION",
    "BODY_DEFAULT_LIMITS",
    "BODY_DEFAULT_PIXEL_SIZE_M",
    "CONTRACT_VERSION",
    "DIMENSION_CODES",
    "DIMENSION_LABELS",
    "ENGINE_VERSION",
    "EARTH_BBOX_CRS",
    "SUPPORTED_AOI_BODIES",
    "SUPPORTED_BODIES",
    "BODY_FRAMES",
    "BODY_CRS",
    "AoiGeometry",
    "AoiLimits",
    "BodyGeometry",
    "CommittedCoarseD01Engine",
    "EngineFootprint",
    "MetricEnvelope",
    "D01Analysis",
    "D01Analyzer",
    "bbox_area_km2",
    "bbox_geometry",
    "bbox_side_lengths_m",
    "body_geod",
    "body_geometry",
    "build_engine_report",
    "compute_cache_key",
    "enforce_quota",
    "normalize_bbox",
    "normalize_longitude",
    "quota_for_body",
    "validate_aoi_geometry",
]
