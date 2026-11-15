"""Bounded coarse-to-90 m refinement linking (Issue #37).

This module is deliberately storage-independent and network-free. It

1. selects a deterministic, bounded set of coarse hits from a verified coarse index,
2. derives an explicit 1.98 km focus window inside each 5 km coarse candidate in the
   candidate's recorded projected CRS,
3. calls an injected 90 m refinement provider, and
4. records coarse candidate ID -> refined area ID links with separate coarse and fine
   provenance.

The real Copernicus DEM GLO-90 + ESA WorldCover provider lives in
``analog_pipelines.refine_earth`` and is injected by the CLI. Offline tests inject a
deterministic fixture provider instead.

Coarse 5 km / ~250 m features and fine 1.98 km / 90 m features are never
interchangeable. The coarse rank is carried over as coarse provenance only; this
module does not compute a fine rank and does not claim recall or global coverage.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import math
import os
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from analog_api.domain.features import (
    ANALYSIS_RESOLUTION_M,
    ANALYSIS_WINDOW_M,
    CONTEXT_METHOD_VERSION,
    FEATURE_BY_ID,
    FLAT_METHOD_VERSION,
    METHOD_VERSION,
)
from pyproj import CRS, Transformer

from analog_pipelines.coarse_features import COARSE_METHOD_VERSION
from analog_pipelines.coarse_index import CoarseIndexRepository
from analog_pipelines.paths import REPO_ROOT

LINK_METHOD_VERSION = "coarse-refine-link-1.0.0"
REPORT_VERSION = "coarse-refine-report-1.0.0"
FOCUS_WINDOW_M = float(ANALYSIS_WINDOW_M)
FOCUS_PLACEMENT_RULE = "coarse_recorded_metric_bbox_centre_v1"
SELECTION_TOP_PER_REGION = "top-per-region"
SELECTION_TOP_OVERALL = "top-overall"
SELECTION_RULES = (SELECTION_TOP_PER_REGION, SELECTION_TOP_OVERALL)
MAX_HITS = 10
DEFAULT_TARGET = {"coarse_slope_mean_deg": 8.0}
DEFAULT_WEIGHTS = {"coarse_slope_mean_deg": 1.0}
METRE_UNITS = {"m", "metre", "meter", "metres", "meters"}
FINE_METHOD_VERSIONS = {
    "slope_mean_deg": METHOD_VERSION,
    "slope_p90_deg": METHOD_VERSION,
    "terrain_relief_p95_p05_m": METHOD_VERSION,
    "roughness_std_m": METHOD_VERSION,
    "flat_terrain_fraction": FLAT_METHOD_VERSION,
}
DATA_KINDS = {"observed", "synthetic_fixture"}


class RefineError(ValueError):
    """A deterministic selection/focus/report contract violation."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class ProviderFailure(RuntimeError):
    """A provider refused or failed; a failed window is never zero-filled."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        no_coverage: bool = False,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.no_coverage = no_coverage
        self.details = dict(details or {})


@dataclass(frozen=True, slots=True)
class FocusWindow:
    """A 1.98 km metric focus window derived from one 5 km coarse candidate."""

    coarse_candidate_id: str
    coarse_region_id: str
    coarse_crs: str
    coarse_bbox_wgs84: dict[str, float]
    coarse_bbox_metric: dict[str, float]
    focus_bbox_wgs84: dict[str, float]
    focus_bbox_metric: dict[str, float]
    center_lon: float
    center_lat: float
    window_m: float
    placement_rule: str
    margins_m: dict[str, float]

    def to_payload(self) -> dict[str, Any]:
        return {
            "coarse_candidate_id": self.coarse_candidate_id,
            "coarse_region_id": self.coarse_region_id,
            "coarse_crs": self.coarse_crs,
            "coarse_bbox_wgs84": _round_bbox(self.coarse_bbox_wgs84, 8),
            "coarse_bbox_metric": _round_bbox(self.coarse_bbox_metric, 3),
            "focus_bbox_wgs84": _round_bbox(self.focus_bbox_wgs84, 8),
            "focus_bbox_metric": _round_bbox(self.focus_bbox_metric, 3),
            "center_lon": round(self.center_lon, 8),
            "center_lat": round(self.center_lat, 8),
            "window_m": round(self.window_m, 3),
            "placement_rule": self.placement_rule,
            "margins_m": {key: round(value, 3) for key, value in self.margins_m.items()},
        }


@dataclass(frozen=True, slots=True)
class RefinedMeasurement:
    """Provider-independent 90 m measurement for one focus window."""

    features: dict[str, float]
    analysis_crs: str
    valid_fraction: float
    window_size_m: float
    resolution_m: float
    checksum: str
    datasets: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    provider_status: dict[str, dict[str, Any]] = field(default_factory=dict)
    quality_flags: tuple[str, ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)
    context_observations: dict[str, dict[str, Any]] = field(default_factory=dict)
    method_versions: dict[str, str] = field(default_factory=dict)
    area_km2: float | None = None
    center_lon: float | None = None
    center_lat: float | None = None
    bbox_wgs84: dict[str, float] | None = None
    data_kind: str = "observed"


class RefinementProvider(Protocol):
    def __call__(self, focus: FocusWindow) -> RefinedMeasurement: ...


Provider = Callable[[FocusWindow], RefinedMeasurement]


@dataclass(frozen=True, slots=True)
class Selection:
    rule: str
    rule_description: str
    rank_scope: str
    target: dict[str, float]
    weights: dict[str, float]
    indexed_candidate_count: int
    eligible_candidate_count: int
    eligible_by_region: dict[str, int]
    hits: tuple[dict[str, Any], ...]


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _round_bbox(bbox: Mapping[str, float], digits: int) -> dict[str, float]:
    return {
        "west": round(float(bbox["west"]), digits),
        "south": round(float(bbox["south"]), digits),
        "east": round(float(bbox["east"]), digits),
        "north": round(float(bbox["north"]), digits),
    }


def _metric_bbox_from_wgs84(bbox: Mapping[str, float], crs: CRS) -> dict[str, float]:
    forward = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
    xs, ys = forward.transform(
        [bbox["west"], bbox["east"], bbox["west"], bbox["east"]],
        [bbox["south"], bbox["south"], bbox["north"], bbox["north"]],
    )
    return {"west": min(xs), "south": min(ys), "east": max(xs), "north": max(ys)}


def derive_focus_window(
    hit: Mapping[str, Any],
    *,
    window_m: float = FOCUS_WINDOW_M,
    placement_rule: str = FOCUS_PLACEMENT_RULE,
) -> FocusWindow:
    """Place a metric ``window_m`` square at the centre of the coarse metric window.

    The coarse index stores a WGS84 envelope and the original projected CRS. The
    envelope is reprojected back into that CRS, its metric centre is taken, and the
    focus square is centred there. This is deterministic and never assumes that a
    degree is a fixed number of metres.
    """
    if not math.isfinite(window_m) or window_m <= 0:
        raise RefineError("invalid_focus_window", "focus window must be positive and finite")
    try:
        crs = CRS.from_user_input(str(hit["crs"]))
    except Exception as exc:  # noqa: BLE001 - provider/data boundary
        raise RefineError("invalid_coarse_crs", f"cannot parse coarse CRS: {exc}") from exc
    if not crs.is_projected:
        raise RefineError("invalid_coarse_crs", f"coarse CRS must be projected, got {crs}")
    unit = crs.axis_info[0].unit_name.lower() if crs.axis_info else ""
    if unit not in METRE_UNITS:
        raise RefineError("invalid_coarse_crs", f"coarse CRS must use metres, got {unit!r}")

    coarse_bbox = {key: float(hit[key]) for key in ("west", "south", "east", "north")}
    if not (
        coarse_bbox["west"] < coarse_bbox["east"] and coarse_bbox["south"] < coarse_bbox["north"]
    ):
        raise RefineError("invalid_coarse_bbox", f"invalid coarse bbox: {coarse_bbox}")

    coarse_metric = _metric_bbox_from_wgs84(coarse_bbox, crs)
    center_x = (coarse_metric["west"] + coarse_metric["east"]) / 2.0
    center_y = (coarse_metric["south"] + coarse_metric["north"]) / 2.0
    half = window_m / 2.0
    focus_metric = {
        "west": center_x - half,
        "south": center_y - half,
        "east": center_x + half,
        "north": center_y + half,
    }

    tolerance = 1e-6
    margins = {
        "west": focus_metric["west"] - coarse_metric["west"],
        "south": focus_metric["south"] - coarse_metric["south"],
        "east": coarse_metric["east"] - focus_metric["east"],
        "north": coarse_metric["north"] - focus_metric["north"],
    }
    if any(value < -tolerance for value in margins.values()):
        raise RefineError(
            "focus_outside_coarse",
            f"focus window {window_m:.0f} m does not fit inside the coarse window; "
            f"margins={margins}",
        )

    inverse = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
    lons, lats = inverse.transform(
        [
            focus_metric["west"],
            focus_metric["east"],
            focus_metric["west"],
            focus_metric["east"],
        ],
        [
            focus_metric["south"],
            focus_metric["south"],
            focus_metric["north"],
            focus_metric["north"],
        ],
    )
    focus_bbox = {
        "west": min(lons),
        "south": min(lats),
        "east": max(lons),
        "north": max(lats),
    }
    if not (-180.0 <= focus_bbox["west"] < focus_bbox["east"] <= 180.0):
        raise RefineError("invalid_focus_bbox", f"invalid focus longitude span: {focus_bbox}")
    if not (-90.0 <= focus_bbox["south"] < focus_bbox["north"] <= 90.0):
        raise RefineError("invalid_focus_bbox", f"invalid focus latitude span: {focus_bbox}")
    if focus_bbox["south"] < -80.0 or focus_bbox["north"] > 84.0:
        raise RefineError(
            "focus_outside_provider_coverage",
            "focus window is outside the Copernicus automatic UTM range (-80..84 latitude)",
        )

    center_lon, center_lat = inverse.transform(center_x, center_y)
    return FocusWindow(
        coarse_candidate_id=str(hit["candidate_id"]),
        coarse_region_id=str(hit["region_id"]),
        coarse_crs=crs.to_string(),
        coarse_bbox_wgs84=coarse_bbox,
        coarse_bbox_metric=coarse_metric,
        focus_bbox_wgs84=focus_bbox,
        focus_bbox_metric=focus_metric,
        center_lon=float(center_lon),
        center_lat=float(center_lat),
        window_m=float(window_m),
        placement_rule=placement_rule,
        margins_m=margins,
    )


def refined_area_id(focus: FocusWindow, *, coarse_snapshot_id: str) -> str:
    """Content-derived area ID for a refined window (separate from on-demand AOIs)."""
    descriptor = {
        "link_method_version": LINK_METHOD_VERSION,
        "coarse_snapshot_id": coarse_snapshot_id,
        "coarse_candidate_id": focus.coarse_candidate_id,
        "focus_placement_rule": focus.placement_rule,
        "focus_window_m": round(focus.window_m, 3),
        "focus_bbox_metric": _round_bbox(focus.focus_bbox_metric, 3),
        "coarse_crs": focus.coarse_crs,
        "center_lonlat": [round(focus.center_lon, 8), round(focus.center_lat, 8)],
        "analysis_resolution_m": ANALYSIS_RESOLUTION_M,
        "fine_method_versions": FINE_METHOD_VERSIONS,
        "context_method_version": CONTEXT_METHOD_VERSION,
    }
    digest = hashlib.sha256(_canonical(descriptor).encode("utf-8")).hexdigest()[:16]
    return f"earth-refine-{digest}"


def select_hits(
    repository: CoarseIndexRepository,
    *,
    target: Mapping[str, float] | None = None,
    weights: Mapping[str, float] | None = None,
    limit: int = 3,
    selection: str = SELECTION_TOP_PER_REGION,
) -> Selection:
    """Select deterministic coarse hits; no random sampling and no unbounded scan."""
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= MAX_HITS:
        raise RefineError("invalid_limit", f"limit must be an integer between 1 and {MAX_HITS}")
    if selection not in SELECTION_RULES:
        raise RefineError("invalid_selection", f"unknown selection rule {selection!r}")
    target_values = {key: float(value) for key, value in (target or DEFAULT_TARGET).items()}
    if weights is None:
        weight_values = {feature_id: 1.0 for feature_id in target_values}
    else:
        weight_values = {key: float(value) for key, value in weights.items()}

    regions = tuple(sorted(repository.manifest["coverage"]["regions"]))
    if selection == SELECTION_TOP_PER_REGION:
        if limit > len(regions):
            raise RefineError(
                "insufficient_regions",
                f"top-per-region needs {limit} region(s) but the index has {len(regions)}",
            )
        chosen_regions = regions[:limit]
        hits: list[dict[str, Any]] = []
        eligible_by_region: dict[str, int] = {}
        for region_id in chosen_regions:
            result = repository.search(
                target=target_values,
                weights=weight_values,
                limit=1,
                region_id=region_id,
            )
            eligible_by_region[region_id] = int(result["eligible_candidate_count"])
            for item in result["results"]:
                payload = dict(item)
                payload["coarse_rank"] = int(item["rank"])
                payload["coarse_rank_scope"] = "within_region"
                hits.append(payload)
        indexed = int(repository.manifest["row_count"])
        eligible = sum(eligible_by_region.values())
        if len(hits) != len(chosen_regions):
            raise RefineError(
                "empty_region_selection",
                "top-per-region could not find a hit in every chosen region",
            )
        rule_description = (
            f"Top 1 hit per coarse region for the first {limit} region(s) sorted by region_id; "
            "order inside each region is coarse distance then candidate_id."
        )
        rank_scope = "within_region"
    else:
        result = repository.search(
            target=target_values,
            weights=weight_values,
            limit=limit,
        )
        hits = []
        for item in result["results"]:
            payload = dict(item)
            payload["coarse_rank"] = int(item["rank"])
            payload["coarse_rank_scope"] = "global"
            hits.append(payload)
        indexed = int(result["indexed_candidate_count"])
        eligible = int(result["eligible_candidate_count"])
        eligible_by_region = {}
        rule_description = (
            "Top K hits across the whole coarse index; order is coarse distance then candidate_id."
        )
        rank_scope = "global"

    if not hits:
        raise RefineError("empty_selection", "the coarse selection produced no hits")
    return Selection(
        rule=selection,
        rule_description=rule_description,
        rank_scope=rank_scope,
        target=target_values,
        weights=weight_values,
        indexed_candidate_count=indexed,
        eligible_candidate_count=eligible,
        eligible_by_region=eligible_by_region,
        hits=tuple(hits),
    )


def _coarse_block(
    hit: Mapping[str, Any], selection: Selection, *, coarse_snapshot_id: str
) -> dict[str, Any]:
    return {
        "candidate_id": str(hit["candidate_id"]),
        "region_id": str(hit["region_id"]),
        "coarse_rank": int(hit["coarse_rank"]),
        "coarse_rank_scope": str(hit["coarse_rank_scope"]),
        "distance": round(float(hit["distance"]), 9),
        "target": selection.target,
        "weights": selection.weights,
        "snapshot_id": coarse_snapshot_id,
        "method_version": COARSE_METHOD_VERSION,
        "grid_version": hit.get("grid_version"),
        "dataset_id": hit.get("dataset_id"),
        "source_asset_id": hit.get("source_asset_id"),
        "qa_flags": list(hit.get("qa_flags") or []),
        "valid_fraction": hit.get("valid_fraction"),
        "window_size_m": hit.get("window_size_m"),
        "source_resolution_m": hit.get("source_resolution_m"),
        "crs": hit.get("crs"),
        "bbox_wgs84": _round_bbox(
            {key: float(hit[key]) for key in ("west", "south", "east", "north")}, 8
        ),
        "features": {
            "coarse_slope_mean_deg": hit.get("slope_mean_deg"),
            "coarse_slope_p90_deg": hit.get("slope_p90_deg"),
            "coarse_flat_fraction_lt5deg": hit.get("flat_fraction"),
        },
    }


def _feature_observations(measurement: RefinedMeasurement) -> dict[str, dict[str, Any]]:
    if measurement.data_kind not in DATA_KINDS:
        raise ProviderFailure(
            "provider_invalid_result",
            f"unknown data_kind {measurement.data_kind!r}; expected one of {sorted(DATA_KINDS)}",
        )
    if measurement.data_kind == "observed" and not measurement.checksum:
        raise ProviderFailure(
            "provider_invalid_result", "observed measurement has no window checksum"
        )
    if not measurement.features:
        raise ProviderFailure("provider_invalid_result", "provider returned no fine features")
    unknown = sorted(set(measurement.features) - set(FEATURE_BY_ID))
    if unknown:
        raise ProviderFailure(
            "provider_invalid_result", f"provider returned unknown feature ids: {unknown}"
        )
    if not 0.0 <= float(measurement.valid_fraction) <= 1.0:
        raise ProviderFailure(
            "provider_invalid_result",
            f"valid_fraction {measurement.valid_fraction} is outside [0, 1]",
        )
    quality = "verified" if measurement.data_kind == "observed" else "synthetic_fixture"
    observations: dict[str, dict[str, Any]] = {}
    for feature_id, value in sorted(measurement.features.items()):
        definition = FEATURE_BY_ID[feature_id]
        numeric = float(value)
        if not math.isfinite(numeric):
            raise ProviderFailure(
                "provider_invalid_result", f"{feature_id} is not finite; refusing to record it"
            )
        if not definition.min_value <= numeric <= definition.max_value:
            raise ProviderFailure(
                "provider_invalid_result",
                f"{feature_id}={numeric} is outside [{definition.min_value}, "
                f"{definition.max_value}]",
            )
        observations[feature_id] = {
            "value": round(numeric, 6),
            "unit": definition.unit,
            "method_version": FINE_METHOD_VERSIONS[feature_id],
            "extraction_scale_m": definition.extraction_scale_m,
            "valid_fraction": round(float(measurement.valid_fraction), 6),
            "quality": quality,
        }
    return observations


def _refined_block(
    measurement: RefinedMeasurement,
    focus: FocusWindow,
    *,
    area_id: str,
    observations: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    return {
        "area_id": area_id,
        "origin": "coarse_refinement",
        "data_kind": measurement.data_kind,
        "bbox_wgs84": _round_bbox(measurement.bbox_wgs84 or focus.focus_bbox_wgs84, 8),
        "center_lon": (
            round(measurement.center_lon, 8)
            if measurement.center_lon is not None
            else round(focus.center_lon, 8)
        ),
        "center_lat": (
            round(measurement.center_lat, 8)
            if measurement.center_lat is not None
            else round(focus.center_lat, 8)
        ),
        "area_km2": (
            round(float(measurement.area_km2), 6)
            if measurement.area_km2 is not None
            else round((measurement.window_size_m / 1000.0) ** 2, 6)
        ),
        "crs": measurement.analysis_crs,
        "window_size_m": round(float(measurement.window_size_m), 3),
        "resolution_m": round(float(measurement.resolution_m), 3),
        "valid_fraction": round(float(measurement.valid_fraction), 6),
        "features": observations,
        "method_versions": measurement.method_versions or dict(FINE_METHOD_VERSIONS),
        "context_observations": measurement.context_observations,
        "context_observation_count": len(measurement.context_observations),
        "datasets": [dict(dataset) for dataset in measurement.datasets],
        "provider_status": measurement.provider_status,
        "quality_flags": list(measurement.quality_flags),
        "warnings": list(measurement.warnings),
        "checksum": measurement.checksum,
    }


def _score_separation(selection: Selection, hit: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "coarse": {
            "rank": int(hit["coarse_rank"]),
            "rank_scope": str(hit["coarse_rank_scope"]),
            "distance": round(float(hit["distance"]), 9),
            "method_version": COARSE_METHOD_VERSION,
            "spatial_support": "5 km candidate window aggregated from ~250 m source cells",
        },
        "fine": {
            "rank": None,
            "distance": None,
            "method_version": METHOD_VERSION,
            "spatial_support": "1.98 km focus window on a 22 x 22 grid at 90 m",
            "reason": (
                "No fine-support target is defined for this pilot, so the 90 m observations "
                "are reported unranked. Coarse and fine supports are not interchangeable and "
                "the coarse rank is not a fine rank."
            ),
        },
        "coarse_and_fine_scores_interchangeable": False,
    }


def refine_hits(
    selection: Selection,
    provider: Provider,
    *,
    coarse_snapshot_id: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Refine every selected hit; failures are recorded, never zero-filled."""
    links: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for hit in selection.hits:
        coarse = _coarse_block(hit, selection, coarse_snapshot_id=coarse_snapshot_id)
        try:
            focus = derive_focus_window(hit)
        except RefineError as exc:
            failures.append(
                {
                    "coarse_candidate_id": str(hit["candidate_id"]),
                    "refined_area_id": None,
                    "status": "failed",
                    "stage": "focus_derivation",
                    "coarse": coarse,
                    "focus": None,
                    "error": {"code": exc.code, "message": exc.message},
                }
            )
            continue

        area_id = refined_area_id(focus, coarse_snapshot_id=coarse_snapshot_id)
        focus_payload = focus.to_payload()
        try:
            measurement = provider(focus)
            observations = _feature_observations(measurement)
            refined = _refined_block(measurement, focus, area_id=area_id, observations=observations)
        except ProviderFailure as exc:
            failure = {
                "coarse_candidate_id": str(hit["candidate_id"]),
                "refined_area_id": area_id,
                "status": "no_coverage" if exc.no_coverage else "failed",
                "stage": "provider",
                "coarse": coarse,
                "focus": focus_payload,
                "error": {"code": exc.code, "message": exc.message},
                "details": exc.details,
            }
            failures.append(failure)
            continue
        except Exception as exc:  # noqa: BLE001 - provider boundary must stay auditable
            failures.append(
                {
                    "coarse_candidate_id": str(hit["candidate_id"]),
                    "refined_area_id": area_id,
                    "status": "failed",
                    "stage": "provider_unexpected",
                    "coarse": coarse,
                    "focus": focus_payload,
                    "error": {
                        "code": "provider_unexpected",
                        "message": f"{type(exc).__name__}: {exc}",
                    },
                }
            )
            continue

        links.append(
            {
                "link_method_version": LINK_METHOD_VERSION,
                "status": "succeeded",
                "stage": "complete",
                "coarse": coarse,
                "focus": focus_payload,
                "refined": refined,
                "scores": _score_separation(selection, hit),
                "error": None,
            }
        )
    return links, failures


def _dataset_table(links: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    seen: dict[str, dict[str, Any]] = {}
    for link in links:
        refined = link.get("refined") or {}
        for dataset in refined.get("datasets") or []:
            dataset_id = str(dataset.get("dataset_id"))
            if dataset_id not in seen:
                seen[dataset_id] = {
                    "dataset_id": dataset_id,
                    "provider": dataset.get("provider"),
                    "product_id": dataset.get("product_id"),
                    "title": dataset.get("title"),
                    "license": dataset.get("license"),
                    "license_url": dataset.get("license_url"),
                    "source_kind": dataset.get("source_kind"),
                    "native_resolution_m": dataset.get("native_resolution_m"),
                    "processing_version": dataset.get("processing_version"),
                    "access_timestamp": dataset.get("access_timestamp"),
                }
    return [seen[key] for key in sorted(seen)]


def _provider_counts(links: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = {}
    for link in links:
        refined = link.get("refined") or {}
        for dataset_id, payload in (refined.get("provider_status") or {}).items():
            bucket = counts.setdefault(
                str(dataset_id),
                {"verified": 0, "failed": 0, "no_coverage": 0, "unknown": 0},
            )
            status = str((payload or {}).get("status"))
            bucket[status if status in bucket else "unknown"] += 1
    return counts


def build_report(
    *,
    selection: Selection,
    links: Sequence[dict[str, Any]],
    failures: Sequence[dict[str, Any]],
    repository: CoarseIndexRepository,
    index_path: str | Path,
    run_metrics: Mapping[str, Any] | None = None,
    provider_label: str = "injected",
    generated_by: str = "pipelines/refine_coarse_hits.py",
    generated_at: str | None = None,
) -> dict[str, Any]:
    succeeded = sum(1 for link in links if link["status"] == "succeeded")
    failed = sum(1 for failure in failures if failure["status"] == "failed")
    no_coverage = sum(1 for failure in failures if failure["status"] == "no_coverage")
    total = len(selection.hits)
    if succeeded + failed + no_coverage != total:
        raise RefineError(
            "count_mismatch",
            f"selected={total} does not equal succeeded+failed+no_coverage="
            f"{succeeded + failed + no_coverage}",
        )

    index_root = Path(index_path)
    manifest_path = Path(repository.root) / "manifest.json"
    resolved_index = index_root
    with contextlib.suppress(ValueError):
        resolved_index = index_root.resolve().relative_to(REPO_ROOT)

    feature_observations = sum(len(link["refined"]["features"]) for link in links)
    context_observations = sum(len(link["refined"]["context_observations"]) for link in links)
    limitations = [
        "This is a bounded local coarse pilot, not global coverage and not an exhaustive scan.",
        (
            f"The coarse index contains {selection.indexed_candidate_count} cells; only "
            f"{total} selected hit(s) were refined. No recall or precision is estimated "
            "because no held-out labelled pilot is available."
        ),
        (
            "Coarse 5 km / ~250 m features and fine 1.98 km / 90 m features have different "
            "support and method versions. The coarse rank is not a fine rank and the two are "
            "never merged into one score."
        ),
        (
            "The 1.98 km focus window is placed at the centre of each coarse metric window. "
            "This fixed placement does not search for the most similar sub-window inside the "
            "5 km candidate, so a coarse hit may contain a different fine-scale signature."
        ),
        (
            "Copernicus GLO-90 is a DSM and may include vegetation, buildings and "
            "infrastructure; it is not a bare-earth terrain model."
        ),
        (
            "ESA WorldCover is 2021 land-cover context only. It is not mineralogy, not "
            "geology, and does not enter the terrain similarity distance."
        ),
        (
            "Provider status is per request. Failed or no-coverage windows are reported and "
            "omitted; no missing value is replaced with zero or a synthetic observation."
        ),
        (
            "Refined windows are not promoted into the seedable index, the committed "
            "snapshot, or user-created AOIs by this pilot."
        ),
    ]
    if selection.rule == SELECTION_TOP_PER_REGION:
        limitations.append(
            "Region-stratified selection takes the top hit in each committed pilot region, "
            "not the global top three; it is not a ranking of regions."
        )

    return {
        "report_version": REPORT_VERSION,
        "link_method_version": LINK_METHOD_VERSION,
        "generated_by": generated_by,
        "generated_at": generated_at or utc_now_iso(),
        "provider": {
            "implementation": provider_label,
            "access": (
                "bounded GDAL /vsicurl HTTPS range reads for 1.98 km windows; "
                "no full tile or global download"
            ),
            "analysis_window_m": FOCUS_WINDOW_M,
            "analysis_resolution_m": ANALYSIS_RESOLUTION_M,
            "network": provider_label != "injected",
        },
        "input_index": {
            "path": str(resolved_index),
            "snapshot_id": repository.snapshot_id,
            "manifest_sha256": _sha256_file(manifest_path),
            "index_version": repository.manifest.get("index_version"),
            "grid_version": repository.manifest.get("grid_version"),
            "method_version": repository.manifest.get("method_version"),
            "row_count": repository.manifest.get("row_count"),
            "coverage_label": repository.manifest["coverage"].get("label"),
            "regions": repository.manifest["coverage"].get("regions"),
            "partitions": [
                {
                    "path": partition.get("path"),
                    "sha256": partition.get("sha256"),
                    "rows": partition.get("rows"),
                    "bytes": partition.get("bytes"),
                }
                for partition in repository.manifest.get("partitions", [])
            ],
        },
        "selection": {
            "rule": selection.rule,
            "rule_description": selection.rule_description,
            "rank_scope": selection.rank_scope,
            "target": selection.target,
            "weights": selection.weights,
            "limit": total,
            "indexed_candidate_count": selection.indexed_candidate_count,
            "eligible_candidate_count": selection.eligible_candidate_count,
            "eligible_by_region": selection.eligible_by_region,
            "selected_candidate_ids": [hit["candidate_id"] for hit in selection.hits],
        },
        "focus": {
            "window_m": FOCUS_WINDOW_M,
            "placement_rule": FOCUS_PLACEMENT_RULE,
            "inside_coarse_window": True,
            "selection_is_deterministic": True,
            "analysis_resolution_m": ANALYSIS_RESOLUTION_M,
        },
        "method_versions": {
            "coarse": COARSE_METHOD_VERSION,
            "fine": METHOD_VERSION,
            "flat": FLAT_METHOD_VERSION,
            "context": CONTEXT_METHOD_VERSION,
            "link": LINK_METHOD_VERSION,
            "report": REPORT_VERSION,
        },
        "counts": {
            "coarse_indexed": selection.indexed_candidate_count,
            "coarse_eligible": selection.eligible_candidate_count,
            "coarse_selected": total,
            "refined_succeeded": succeeded,
            "refined_failed": failed,
            "refined_no_coverage": no_coverage,
            "fine_feature_observations": feature_observations,
            "context_observations": context_observations,
            "links": len(links),
            "failures": len(failures),
        },
        "provider_counts": _provider_counts(links),
        "links": list(links),
        "failures": list(failures),
        "datasets": _dataset_table(links),
        "recall_estimate": {
            "value": None,
            "reason": (
                "No held-out labelled pilot is available. This run measures bounded "
                "refinement behaviour and provider support, not coarse-shortlist recall."
            ),
        },
        "coverage": {
            "scope": "local_coarse_index_not_global_exhaustive",
            "regions": repository.manifest["coverage"].get("regions"),
            "claim": (
                "Only the selected coarse cells and their focus windows were processed. "
                "No global or exhaustive best-match claim is made."
            ),
        },
        "run_metrics": dict(run_metrics or {}),
        "limitations": limitations,
    }


def write_report_json(report: Mapping[str, Any], path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(report, indent=2, sort_keys=True) + "\n"
    fd, tmp = tempfile.mkstemp(prefix=path.name, dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return path


def _value(value: Any, digits: int = 4) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def render_markdown(report: Mapping[str, Any]) -> str:
    counts = report["counts"]
    index = report["input_index"]
    selection = report["selection"]
    rank_header = (
        "Coarse rank (within region)"
        if selection["rule"] == "top-per-region"
        else "Coarse rank (global)"
    )
    lines = [
        "# Coarse-to-90 m refinement pilot (Issue #37)",
        "",
        f"Generated: `{report['generated_at']}`  ",
        f"Provider: `{report['provider']['implementation']}`  ",
        f"Report schema: `{report['report_version']}`",
        "",
        "## Bounded scope",
        "",
        f"- Input coarse index: `{index['path']}` snapshot `{index['snapshot_id']}` "
        f"(manifest `sha256:{index['manifest_sha256']}`).",
        f"- Coarse method: `{index['method_version']}`; grid: `{index['grid_version']}`.",
        f"- Selection: `{selection['rule']}` — {selection['rule_description']}",
        f"- Target: `{json.dumps(selection['target'], sort_keys=True)}` with weights "
        f"`{json.dumps(selection['weights'], sort_keys=True)}`.",
        f"- Focus rule: `{report['focus']['placement_rule']}`; "
        f"{report['focus']['window_m']:.0f} m metric window inside each 5 km coarse cell.",
        f"- Fine analysis: {report['focus']['analysis_resolution_m']:.0f} m cells; "
        f"method `{report['method_versions']['fine']}`.",
        "",
        "## Actual counts",
        "",
        "| Item | Count |",
        "| --- | ---: |",
        f"| Coarse cells indexed | {counts['coarse_indexed']} |",
        f"| Coarse cells eligible for the target | {counts['coarse_eligible']} |",
        f"| Coarse hits selected | {counts['coarse_selected']} |",
        f"| Refined windows succeeded | {counts['refined_succeeded']} |",
        f"| Refined windows failed | {counts['refined_failed']} |",
        f"| Refined windows no coverage | {counts['refined_no_coverage']} |",
        f"| Fine terrain observations | {counts['fine_feature_observations']} |",
        f"| WorldCover context observations | {counts['context_observations']} |",
        "",
        "## Coarse -> refined links",
        "",
        "| Coarse candidate | Region | "
        + rank_header
        + " | Coarse distance | Refined area | Status | "
        "Fine mean slope (deg) | Fine p90 slope (deg) | Relief p95-p05 (m) | Valid fraction |",
        "| --- | --- | ---: | ---: | --- | --- | ---: | ---: | ---: | ---: |",
    ]
    for link in report["links"]:
        coarse = link["coarse"]
        refined = link["refined"]
        features = refined["features"]
        lines.append(
            "| "
            + " | ".join(
                [
                    f"`{coarse['candidate_id']}`",
                    coarse["region_id"],
                    str(coarse["coarse_rank"]),
                    _value(coarse["distance"], 6),
                    f"`{refined['area_id']}`",
                    link["status"],
                    _value(features.get("slope_mean_deg", {}).get("value")),
                    _value(features.get("slope_p90_deg", {}).get("value")),
                    _value(features.get("terrain_relief_p95_p05_m", {}).get("value"), 2),
                    _value(refined["valid_fraction"], 4),
                ]
            )
            + " |"
        )
    for failure in report["failures"]:
        lines.append(
            "| "
            + " | ".join(
                [
                    f"`{failure['coarse_candidate_id']}`",
                    failure["coarse"]["region_id"],
                    str(failure["coarse"]["coarse_rank"]),
                    _value(failure["coarse"]["distance"], 6),
                    f"`{failure['refined_area_id'] or 'n/a'}`",
                    f"{failure['status']} ({failure['stage']})",
                    "n/a",
                    "n/a",
                    "n/a",
                    "n/a",
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "Coarse and fine values are shown side by side but are not comparable: the coarse "
            "rank uses a 5 km / ~250 m support and the fine observations use a 1.98 km / 90 m "
            "support. No fine rank is computed.",
            "",
            "## Provider status",
            "",
            "| Provider dataset | verified | failed | no coverage |",
            "| --- | ---: | ---: | ---: |",
        ]
    )
    for dataset_id, bucket in sorted(report["provider_counts"].items()):
        lines.append(
            f"| `{dataset_id}` | {bucket['verified']} | {bucket['failed']} | "
            f"{bucket['no_coverage']} |"
        )
    if not report["provider_counts"]:
        lines.append("| n/a | 0 | 0 | 0 |")
    lines.extend(
        [
            "",
            "## Sources and licenses",
            "",
            "| Dataset | Provider | Product | License |",
            "| --- | --- | --- | --- |",
        ]
    )
    for dataset in report["datasets"]:
        lines.append(
            f"| `{dataset['dataset_id']}` | {dataset['provider']} | "
            f"{dataset['product_id']} | {dataset['license']} |"
        )
    if not report["datasets"]:
        lines.append("| n/a | n/a | n/a | n/a |")
    lines.append(
        "\nPer-window source URLs, Copernicus tile lists, checksums and actual access "
        "timestamps are recorded in the JSON `provider_status` fields."
    )
    lines.extend(["", "## Failures", ""])
    if report["failures"]:
        for failure in report["failures"]:
            lines.append(
                f"- `{failure['coarse_candidate_id']}` -> "
                f"`{failure['refined_area_id'] or 'n/a'}`: {failure['status']} at "
                f"`{failure['stage']}` — `{failure['error']['code']}`: "
                f"{failure['error']['message']}"
            )
    else:
        lines.append("- None.")
    lines.extend(["", "## Recall and coverage claims", ""])
    lines.append(f"- Recall estimate: none. {report['recall_estimate']['reason']}")
    lines.append(f"- Coverage: {report['coverage']['claim']}")
    lines.extend(["", "## Limitations", ""])
    for limitation in report["limitations"]:
        lines.append(f"- {limitation}")
    metrics = report.get("run_metrics") or {}
    if metrics:
        lines.extend(
            [
                "",
                "## Run metrics",
                "",
                f"- Elapsed: {_value(metrics.get('elapsed_s'), 2)} s",
                f"- Max RSS: {_value(metrics.get('max_rss_mb'), 1)} MiB",
                f"- Report JSON bytes: {_value(metrics.get('json_bytes'))}",
            ]
        )
    lines.append("")
    return "\n".join(lines)


def write_report_markdown(report: Mapping[str, Any], path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = render_markdown(report)
    fd, tmp = tempfile.mkstemp(prefix=path.name, dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return path


__all__ = [
    "DEFAULT_TARGET",
    "DEFAULT_WEIGHTS",
    "FOCUS_PLACEMENT_RULE",
    "FOCUS_WINDOW_M",
    "LINK_METHOD_VERSION",
    "MAX_HITS",
    "REPORT_VERSION",
    "SELECTION_RULES",
    "FocusWindow",
    "ProviderFailure",
    "RefinedMeasurement",
    "RefinementProvider",
    "RefineError",
    "Selection",
    "build_report",
    "derive_focus_window",
    "refine_hits",
    "refined_area_id",
    "render_markdown",
    "select_hits",
    "utc_now_iso",
    "write_report_json",
    "write_report_markdown",
]
