"""Area-fraction and categorical evidence export for mapped geology.

Two independent paths share one evidence schema:

* live polygon overlay through :class:`analog_pipelines.geology.spatial.VectorIndex`
  (native CRS -> equal-area CRS), and
* offline export from an already committed, source-derived overlay record.

Categorical rasters are only ever resampled with nearest-neighbour. Missing
coverage stays ``no_coverage`` and never becomes a fabricated zero or false.

No geology similarity value and no combined six-dimension score is produced.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from pyproj import CRS, Transformer

from analog_pipelines.geology.legend import (
    PROCESS_CATEGORIES,
    UnitMapping,
    documented_aqueous_evidence,
    load_registry,
    resolve_unit,
)
from analog_pipelines.geology.models import Manifest, validate_body_crs

EVIDENCE_SCHEMA = "geology-area-fraction-evidence-1.0.0"
EXPORT_VERSION = "geology-area-fraction-1.0.0"
FRACTION_TOLERANCE = 1e-6
REPO = Path(__file__).resolve().parents[4]
UNAVAILABLE_STATUSES = {
    "no_coverage",
    "auth_required",
    "failed",
    "unverified",
    "context_only",
}

COVERAGE_STATE_DEFINITION = {
    "complete": "covered fraction is 1 within 1e-6",
    "partial": "0 < covered fraction < 1 - 1e-6; uncovered_fraction is reported, not zero-filled",
    "no_coverage": "the source has no polygon/raster coverage of this AOI; fractions are null",
    "unknown": "coverage could not be exported (for example unverified redistribution rights)",
}


def coverage_state(valid_fraction: float | None) -> str:
    if valid_fraction is None:
        return "unknown"
    if valid_fraction <= 0:
        return "no_coverage"
    if valid_fraction >= 1 - FRACTION_TOLERANCE:
        return "complete"
    return "partial"


# ---------------------------------------------------------------------------
# Equal-area measurement without optional GIS imports
# ---------------------------------------------------------------------------


def densify_ring(ring: list[list[float]] | list[tuple[float, float]], step: float) -> list[tuple]:
    """Densify a closed ring in its own CRS before projection, as shapely does."""
    points = [(float(x), float(y)) for x, y in ring]
    if len(points) < 4 or points[0] != points[-1]:
        raise ValueError("rings must be closed and contain at least four positions")
    output: list[tuple[float, float]] = []
    for (x1, y1), (x2, y2) in zip(points, points[1:], strict=False):
        distance = math.hypot(x2 - x1, y2 - y1)
        divisions = max(1, math.ceil(distance / step))
        for index in range(divisions):
            ratio = index / divisions
            output.append((x1 + (x2 - x1) * ratio, y1 + (y2 - y1) * ratio))
    output.append(points[-1])
    return output


def _ring_area_m2(ring: list, source_crs: str, metric_crs: str, step: float) -> float:
    transformer = Transformer.from_crs(source_crs, metric_crs, always_xy=True)
    dense = densify_ring(ring, step)
    xs, ys = transformer.transform([p[0] for p in dense], [p[1] for p in dense])
    twice_area = sum(
        x1 * y2 - x2 * y1 for x1, y1, x2, y2 in zip(xs, ys, xs[1:], ys[1:], strict=False)
    )
    return abs(twice_area) / 2


def geometry_area_m2(geometry: dict[str, Any], source_crs: str, metric_crs: str) -> float:
    """Planar area of a Polygon/MultiPolygon measured in an equal-area CRS."""

    source = CRS.from_user_input(source_crs)
    metric = CRS.from_user_input(metric_crs)
    if not metric.is_projected:
        raise ValueError("area measurement requires a projected equal-area CRS")
    if source.is_geographic and metric.is_geographic:
        raise ValueError("geographic coordinates cannot be measured as square metres")
    step = 0.001 if source.is_geographic else 100.0
    kind = geometry.get("type")
    if kind not in {"Polygon", "MultiPolygon"}:
        raise ValueError("AOI must be Polygon or MultiPolygon")
    polygons = [geometry["coordinates"]] if kind == "Polygon" else geometry["coordinates"]
    if not polygons:
        raise ValueError("empty AOI geometry")
    total = 0.0
    for polygon in polygons:
        for index, ring in enumerate(polygon):
            area = _ring_area_m2(ring, source_crs, metric_crs, step)
            total += area if index == 0 else -area
    if total <= 0:
        raise ValueError("AOI area must be positive")
    return total


# ---------------------------------------------------------------------------
# Nearest-neighbour categorical resampling (no bilinear interpolation)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ClassResample:
    classes: np.ndarray
    valid: np.ndarray
    nodata: int | float | None = None
    resampling: str = "nearest_neighbour"


@dataclass(frozen=True)
class ClassFractions:
    state: str
    valid_pixel_count: int
    total_pixel_count: int
    valid_fraction: float
    fractions: dict[int | float, float] | None
    pixel_counts: dict[int | float, int] | None
    nodata_pixel_count: int
    unknown_codes: tuple[int | float, ...] = ()


def nearest_neighbour_resample(
    classes: Any,
    source_transform: tuple[float, float, float, float, float, float],
    target_transform: tuple[float, float, float, float, float, float],
    target_shape: tuple[int, int],
    nodata: int | float | None = None,
) -> ClassResample:
    """Sample a categorical grid by nearest neighbour only.

    ``source_transform``/``target_transform`` use the GDAL six-tuple:
    ``x = a*col + b*row + c`` and ``y = d*col + e*row + f``. Pixel centres are
    sampled. Class values are copied exactly; they are never averaged. Pixels
    outside the source grid stay nodata, and an explicit ``nodata`` is required
    if any target pixel falls outside the source extent.
    """

    array = np.asarray(classes)
    if array.ndim != 2 or array.size == 0:
        raise ValueError("class raster must be a non-empty 2-D array")
    if not np.issubdtype(array.dtype, np.integer) and not np.issubdtype(array.dtype, np.floating):
        raise ValueError("class raster must contain numeric class codes")
    rows, cols = int(target_shape[0]), int(target_shape[1])
    if rows <= 0 or cols <= 0:
        raise ValueError("target shape must be positive")

    a, b, c, d, e, f = source_transform
    determinant = a * e - b * d
    if abs(determinant) < 1e-18:
        raise ValueError("source transform is not invertible")

    target_rows, target_cols = np.indices((rows, cols))
    xs = (
        target_transform[0] * (target_cols + 0.5)
        + target_transform[1] * (target_rows + 0.5)
        + target_transform[2]
    )
    ys = (
        target_transform[3] * (target_cols + 0.5)
        + target_transform[4] * (target_rows + 0.5)
        + target_transform[5]
    )
    dx, dy = xs - c, ys - f
    source_cols = np.floor((e * dx - b * dy) / determinant).astype(np.int64)
    source_rows = np.floor((-d * dx + a * dy) / determinant).astype(np.int64)

    height, width = array.shape
    inside = (
        (source_rows >= 0) & (source_rows < height) & (source_cols >= 0) & (source_cols < width)
    )
    if not inside.all() and nodata is None:
        raise ValueError(
            "target pixels fall outside the source extent; supply an explicit nodata value "
            "instead of fabricating a class"
        )

    if nodata is None:
        output = np.zeros((rows, cols), dtype=array.dtype)
    else:
        output = np.full((rows, cols), nodata, dtype=array.dtype)
    output[inside] = array[source_rows[inside], source_cols[inside]]
    valid = inside.copy()
    if nodata is not None:
        valid &= output != nodata
    return ClassResample(classes=output, valid=valid, nodata=nodata)


def class_fractions(
    classes: Any,
    valid: Any | None = None,
    nodata: int | float | None = None,
    allowed_codes: tuple[int | float, ...] | None = None,
) -> ClassFractions:
    """Fraction of valid pixels per class; all-nodata support is ``no_coverage``."""

    array = np.asarray(classes)
    if array.ndim != 2 or array.size == 0:
        raise ValueError("class raster must be a non-empty 2-D array")
    mask = np.ones(array.shape, dtype=bool) if valid is None else np.asarray(valid, dtype=bool)
    if mask.shape != array.shape:
        raise ValueError("valid mask must match the class raster shape")
    if nodata is not None:
        mask = mask & (array != nodata)
    total = int(array.size)
    nodata_count = int(np.count_nonzero(~mask))
    valid_count = int(np.count_nonzero(mask))
    if valid_count == 0:
        return ClassFractions(
            state="no_coverage",
            valid_pixel_count=0,
            total_pixel_count=total,
            valid_fraction=0.0,
            fractions=None,
            pixel_counts=None,
            nodata_pixel_count=nodata_count,
        )
    values, counts = np.unique(array[mask], return_counts=True)
    pixel_counts = {value.item(): int(count) for value, count in zip(values, counts, strict=True)}
    fractions = {code: count / valid_count for code, count in pixel_counts.items()}
    unknown = ()
    if allowed_codes is not None:
        allowed = set(allowed_codes)
        unknown = tuple(sorted(code for code in pixel_counts if code not in allowed))
    return ClassFractions(
        state="detected",
        valid_pixel_count=valid_count,
        total_pixel_count=total,
        valid_fraction=valid_count / total,
        fractions=fractions,
        pixel_counts=pixel_counts,
        nodata_pixel_count=nodata_count,
        unknown_codes=unknown,
    )


# ---------------------------------------------------------------------------
# Portable evidence records
# ---------------------------------------------------------------------------


def _quality_count(flags: list[str], prefix: str) -> int | None:
    for flag in flags:
        if flag.startswith(prefix + ":"):
            try:
                return int(flag.split(":", 1)[1])
            except ValueError:
                return None
    return None


def _dataset_view(dataset, analysis, registry_dataset: dict[str, Any]) -> dict[str, Any]:
    return {
        "dataset_id": dataset.dataset_id,
        "provider": dataset.provider,
        "product": dataset.product,
        "version": dataset.version,
        "citation": dataset.citation,
        "license": dataset.license,
        "license_url": dataset.license_url,
        "redistribution": dataset.redistribution,
        "map_scale": dataset.map_scale,
        "native_resolution_m": dataset.native_resolution_m,
        "source_crs": analysis.source_crs or dataset.source_crs,
        "analysis_crs": analysis.analysis_crs,
        "native_frame": registry_dataset.get("native_frame"),
        "legend_source": registry_dataset.get("legend_source"),
        "legend_version": registry_dataset.get("legend_version"),
    }


def _label_payload(mapping: UnitMapping) -> list[dict[str, Any]]:
    return [
        {
            "category": label.category,
            "process": label.process,
            "evidence_basis": label.evidence_basis,
            "confidence": label.confidence,
            "supporting_statement": label.supporting_statement,
            "source_document": label.source_document,
        }
        for label in mapping.process_labels
    ]


def _record_id(area_id: str, dataset_id: str, source_feature_id: str) -> str:
    return f"area-fraction:{area_id}:{dataset_id}:{source_feature_id}"


def _display_path(path: str | Path) -> str:
    resolved = Path(path).resolve()
    try:
        return str(resolved.relative_to(REPO))
    except ValueError:
        return str(resolved)


def _qa(
    analysis,
    *,
    derived_from_committed_overlay: bool,
    mapping_status: str,
    geometry_repair: str,
) -> dict[str, Any]:
    return {
        "map_scale": analysis.map_scale,
        "map_scale_note": (
            "Map-scale geological context only; overlay fraction does not improve source accuracy."
            if analysis.map_scale
            else "Map scale not available."
        ),
        "fraction_tolerance": FRACTION_TOLERANCE,
        "invalid_source_geometries_excluded": _quality_count(
            analysis.quality_flags, "excluded_invalid_source_geometries"
        ),
        "geometry_repair": geometry_repair,
        "mapping_status": mapping_status,
        "derived_from_committed_overlay": derived_from_committed_overlay,
        "quality_flags": list(analysis.quality_flags),
    }


def build_evidence(
    record_path: str | Path,
    registry_path: str | Path,
    *,
    derived_from_committed_overlay: bool = True,
    geometry_repair: str = "invalid source polygons excluded and counted, never silently repaired",
) -> dict[str, Any]:
    manifest = Manifest.model_validate_json(Path(record_path).read_text(encoding="utf-8"))
    registry = load_registry(registry_path)
    areas = {area.area_id: area for area in manifest.areas}
    datasets = {dataset.dataset_id: dataset for dataset in manifest.datasets}
    records: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    warnings: list[str] = []

    for analysis in manifest.analyses:
        area = areas[analysis.area_id]
        dataset = datasets[analysis.dataset_id]
        if analysis.body != area.body or analysis.body != dataset.body:
            raise ValueError(f"{analysis.area_id}/{analysis.dataset_id}: body mismatch")
        if dataset.dataset_id not in registry["datasets"]:
            warnings.append(
                f"{analysis.area_id}/{dataset.dataset_id}: no registered legend; "
                "availability recorded without process labels"
            )
            registry_dataset: dict[str, Any] = {}
        else:
            registry_dataset = registry["datasets"][dataset.dataset_id]
        dataset_view = _dataset_view(dataset, analysis, registry_dataset)
        available = (
            dataset.redistribution == "permitted" and analysis.status not in UNAVAILABLE_STATUSES
        )

        if not available:
            availability = (
                "license_unverified" if dataset.redistribution != "permitted" else analysis.status
            )
            state = "no_coverage" if analysis.status == "no_coverage" else "unknown"
            record = {
                "record_id": _record_id(area.area_id, dataset.dataset_id, "availability"),
                "area_id": area.area_id,
                "body": analysis.body,
                "dataset_id": dataset.dataset_id,
                "availability_status": availability,
                "source": dataset_view,
                "area": {
                    "crs": area.crs,
                    "geometry_source": area.geometry_source,
                    "geometry_fingerprint": area.geometry_fingerprint,
                    "area_m2": None,
                    "coverage_fraction": None,
                    "coverage_state": state,
                    "uncovered_fraction": None,
                },
                "unit": {
                    "raw_code": None,
                    "unit_name": None,
                    "official_family": None,
                    "mapping_status": "not_exported",
                    "source_feature_id": None,
                    "source_attributes": {},
                },
                "fraction_of_aoi": None,
                "area_m2": None,
                "process_labels": [],
                "supported_categories": [],
                "unknown_categories": list(PROCESS_CATEGORIES),
                "uncertainty": {
                    "kind": "not_quantified",
                    "notes": "No fraction or area is exported for this availability state.",
                },
                "qa": _qa(
                    analysis,
                    derived_from_committed_overlay=derived_from_committed_overlay,
                    mapping_status="not_exported",
                    geometry_repair=geometry_repair,
                ),
                "score_role": "evidence_only",
                "notes": analysis.reason or f"provider status {analysis.status}",
            }
            records.append(record)
            summaries.append(
                {
                    "area_id": area.area_id,
                    "body": analysis.body,
                    "dataset_id": dataset.dataset_id,
                    "availability_status": availability,
                    "coverage_fraction": None,
                    "coverage_state": state,
                    "uncovered_fraction": None,
                    "aoi_area_m2": None,
                    "covered_area_m2": None,
                    "fraction_sum": None,
                    "units": [],
                    "category_fractions": {},
                    "supported_categories": [],
                    "unknown_categories": list(PROCESS_CATEGORIES),
                    "notes": "No mapped-unit fraction is exported; this is not zero coverage.",
                }
            )
            continue

        metric_crs = analysis.analysis_crs
        if not metric_crs:
            raise ValueError(f"{analysis.area_id}/{dataset.dataset_id}: missing equal-area CRS")
        validate_body_crs(analysis.body, area.crs)
        validate_body_crs(analysis.body, metric_crs)
        aoi_area = geometry_area_m2(area.geometry, area.crs, metric_crs)
        coverage = analysis.valid_fraction
        if coverage is None:
            raise ValueError(
                f"{analysis.area_id}/{dataset.dataset_id}: available status lacks coverage"
            )
        state = coverage_state(coverage)
        uncovered = max(0.0, 1.0 - coverage)

        unit_rows = []
        category_fractions: dict[str, dict[str, Any]] = {}
        for lithology in analysis.lithology:
            mapping = resolve_unit(registry, dataset.dataset_id, lithology.unit_id)
            if mapping.body != analysis.body:
                raise ValueError(
                    f"{area.area_id}: raw code {lithology.unit_id!r} resolved for {mapping.body} "
                    f"but the area is {analysis.body}"
                )
            fraction = lithology.overlap_fraction
            unit_area = fraction * aoi_area
            labels = _label_payload(mapping)
            unknown = list(mapping.unknown_categories)
            quality = list(analysis.quality_flags)
            if mapping.status == "unmapped_code":
                quality.append(f"unmapped_code:{lithology.unit_id}")
            record = {
                "record_id": _record_id(
                    area.area_id, dataset.dataset_id, lithology.source_feature_id
                ),
                "area_id": area.area_id,
                "body": analysis.body,
                "dataset_id": dataset.dataset_id,
                "availability_status": "available",
                "source": dataset_view,
                "area": {
                    "crs": area.crs,
                    "geometry_source": area.geometry_source,
                    "geometry_fingerprint": area.geometry_fingerprint,
                    "area_m2": aoi_area,
                    "coverage_fraction": coverage,
                    "coverage_state": state,
                    "uncovered_fraction": uncovered,
                },
                "unit": {
                    "raw_code": lithology.unit_id,
                    "unit_name": lithology.unit_name,
                    "official_family": mapping.official_family,
                    "mapping_status": mapping.status,
                    "source_feature_id": lithology.source_feature_id,
                    "source_attributes": lithology.original_attributes,
                    "registry_version": mapping.registry_version,
                },
                "fraction_of_aoi": fraction,
                "area_m2": unit_area,
                "process_labels": labels,
                "supported_categories": list(mapping.categories),
                "unknown_categories": unknown,
                "uncertainty": {
                    "kind": "not_quantified",
                    "notes": (
                        "Fraction is a geometric intersection at source map scale; it is not a "
                        "composition, mineral abundance or positional uncertainty estimate."
                    ),
                },
                "qa": _qa(
                    analysis,
                    derived_from_committed_overlay=derived_from_committed_overlay,
                    mapping_status=mapping.status,
                    geometry_repair=geometry_repair,
                ),
                "score_role": "evidence_only",
                "notes": " ".join(
                    part for part in (mapping.notes, lithology.interpretation) if part
                ).strip(),
            }
            records.append(record)
            unit_rows.append(
                {
                    "raw_code": lithology.unit_id,
                    "unit_name": lithology.unit_name,
                    "mapping_status": mapping.status,
                    "source_feature_id": lithology.source_feature_id,
                    "fraction_of_aoi": fraction,
                    "area_m2": unit_area,
                    "supported_categories": list(mapping.categories),
                    "unknown_categories": unknown,
                }
            )
            for category in mapping.categories:
                entry = category_fractions.setdefault(
                    category,
                    {
                        "category": category,
                        "fraction_of_aoi": 0.0,
                        "area_m2": 0.0,
                        "unit_codes": [],
                    },
                )
                entry["fraction_of_aoi"] += fraction
                entry["area_m2"] += unit_area
                if lithology.unit_id not in entry["unit_codes"]:
                    entry["unit_codes"].append(lithology.unit_id)
        if not analysis.lithology:
            records.append(
                {
                    "record_id": _record_id(area.area_id, dataset.dataset_id, "availability"),
                    "area_id": area.area_id,
                    "body": analysis.body,
                    "dataset_id": dataset.dataset_id,
                    "availability_status": "no_mapped_units",
                    "source": dataset_view,
                    "area": {
                        "crs": area.crs,
                        "geometry_source": area.geometry_source,
                        "geometry_fingerprint": area.geometry_fingerprint,
                        "area_m2": aoi_area,
                        "coverage_fraction": coverage,
                        "coverage_state": state,
                        "uncovered_fraction": uncovered,
                    },
                    "unit": {
                        "raw_code": None,
                        "unit_name": None,
                        "official_family": None,
                        "mapping_status": "no_mapped_units",
                        "source_feature_id": None,
                        "source_attributes": {},
                    },
                    "fraction_of_aoi": None,
                    "area_m2": None,
                    "process_labels": [],
                    "supported_categories": [],
                    "unknown_categories": list(PROCESS_CATEGORIES),
                    "uncertainty": {
                        "kind": "not_quantified",
                        "notes": "No mapped unit intersected this AOI; coverage is reported separately.",
                    },
                    "qa": _qa(
                        analysis,
                        derived_from_committed_overlay=derived_from_committed_overlay,
                        mapping_status="no_mapped_units",
                        geometry_repair=geometry_repair,
                    ),
                    "score_role": "evidence_only",
                    "notes": "Availability is not zero geology; no category is asserted.",
                }
            )
        supported = sorted(category_fractions)
        fraction_sum = sum(row["fraction_of_aoi"] for row in unit_rows)
        summaries.append(
            {
                "area_id": area.area_id,
                "body": analysis.body,
                "dataset_id": dataset.dataset_id,
                "availability_status": "available",
                "coverage_fraction": coverage,
                "coverage_state": state,
                "uncovered_fraction": uncovered,
                "aoi_area_m2": aoi_area,
                "covered_area_m2": coverage * aoi_area,
                "fraction_sum": fraction_sum,
                "units": unit_rows,
                "category_fractions": [category_fractions[c] for c in sorted(category_fractions)],
                "supported_categories": supported,
                "unknown_categories": [c for c in PROCESS_CATEGORIES if c not in set(supported)],
                "nonexclusive": True,
                "notes": (
                    "Process categories are non-exclusive; category fractions can overlap and "
                    "must not be summed into a similarity or combined score. Units without a "
                    "reviewed process label remain explicit in unknown_categories."
                ),
            }
        )

    document = {
        "schema": EVIDENCE_SCHEMA,
        "export_version": EXPORT_VERSION,
        "registry_version": registry["registry_version"],
        "registry_sha256": hashlib.sha256(Path(registry_path).read_bytes()).hexdigest(),
        "nonexclusive": True,
        "score_role": "evidence_only",
        "source_record": _display_path(record_path),
        "source_record_generated_at": manifest.generated_at.isoformat(),
        "source_record_sha256": hashlib.sha256(Path(record_path).read_bytes()).hexdigest(),
        "scope": manifest.scope,
        "coverage_state_definition": COVERAGE_STATE_DEFINITION,
        "records": records,
        "area_summaries": summaries,
        "warnings": warnings,
        "limitations": [
            "Area fractions measure polygon intersection at source map scale, not composition.",
            "Missing coverage is no_coverage/unknown with null fractions; it is never zero-filled.",
            "Raw codes are dataset-scoped and are not equivalent across Moon and Mars.",
            "No geology similarity, combined six-dimension score or ranking is produced.",
        ],
    }
    validate_evidence(document)
    return document


def validate_evidence(document: dict[str, Any]) -> dict[str, Any]:
    if document.get("schema") != EVIDENCE_SCHEMA:
        raise ValueError("unexpected area-fraction evidence schema")
    if document.get("nonexclusive") is not True:
        raise ValueError("evidence must declare non-exclusive categories")
    if document.get("score_role") != "evidence_only":
        raise ValueError("area-fraction evidence is evidence only")
    records = document.get("records")
    if not isinstance(records, list) or not records:
        raise ValueError("evidence requires at least one record")
    identifiers = set()
    grouped: dict[tuple[str, str], dict[str, float]] = {}
    for record in records:
        identifier = record.get("record_id")
        if not isinstance(identifier, str) or not identifier:
            raise ValueError("record_id is required")
        if identifier in identifiers:
            raise ValueError(f"duplicate record_id {identifier}")
        identifiers.add(identifier)
        if record.get("score_role") != "evidence_only":
            raise ValueError(f"{identifier}: score_role must be evidence_only")
        body = record.get("body")
        if body not in {"Earth", "Moon", "Mars"}:
            raise ValueError(f"{identifier}: invalid body")
        area = record.get("area", {})
        if area.get("coverage_state") not in COVERAGE_STATE_DEFINITION:
            raise ValueError(f"{identifier}: invalid coverage_state")
        fraction = record.get("fraction_of_aoi")
        area_m2 = record.get("area_m2")
        labels = record.get("process_labels", [])
        availability = record.get("availability_status")
        if availability == "available":
            if fraction is None or area_m2 is None:
                raise ValueError(f"{identifier}: available unit requires fraction and area")
            if not 0 < fraction <= 1 + FRACTION_TOLERANCE:
                raise ValueError(f"{identifier}: fraction must be in (0, 1]")
            if area_m2 <= 0:
                raise ValueError(f"{identifier}: unit area must be positive")
            coverage = area.get("coverage_fraction")
            if coverage is None or not 0 < coverage <= 1 + FRACTION_TOLERANCE:
                raise ValueError(f"{identifier}: available unit requires positive coverage")
            if fraction > coverage + FRACTION_TOLERANCE:
                raise ValueError(f"{identifier}: unit fraction exceeds covered fraction")
            bucket = grouped.setdefault((record["area_id"], record["dataset_id"]), {})
            if record["unit"]["source_feature_id"] in bucket:
                raise ValueError(f"{identifier}: duplicate source feature")
            bucket[record["unit"]["source_feature_id"]] = fraction
        else:
            if fraction is not None or area_m2 is not None:
                raise ValueError(f"{identifier}: unavailable coverage must not carry a fraction")
            if labels:
                raise ValueError(
                    f"{identifier}: unavailable coverage must not carry process labels"
                )
            if area.get("coverage_state") not in {"no_coverage", "unknown"}:
                raise ValueError(f"{identifier}: unavailable coverage needs no_coverage/unknown")
        supported = record.get("supported_categories", [])
        unknown = record.get("unknown_categories", [])
        if set(supported) | set(unknown) != set(PROCESS_CATEGORIES):
            raise ValueError(f"{identifier}: supported/unknown categories must cover all four")
        if set(supported) & set(unknown):
            raise ValueError(f"{identifier}: categories cannot be both supported and unknown")
        if supported != sorted({label.get("category") for label in labels}):
            raise ValueError(f"{identifier}: supported categories must match process labels")
        for label in labels:
            if label.get("category") not in PROCESS_CATEGORIES:
                raise ValueError(f"{identifier}: invalid process category")
            if label.get("category") == "aqueous_alteration":
                text = " ".join(
                    [
                        str(label.get("supporting_statement", "")),
                        str(label.get("source_document", "")),
                    ]
                )
                if not documented_aqueous_evidence(text):
                    raise ValueError(
                        f"{identifier}: aqueous alteration needs positive documented evidence"
                    )
    for (area_id, dataset_id), bucket in grouped.items():
        summary = next(
            (
                item
                for item in document.get("area_summaries", [])
                if item["area_id"] == area_id and item["dataset_id"] == dataset_id
            ),
            None,
        )
        if summary is None:
            raise ValueError(f"{area_id}/{dataset_id}: missing area summary")
        coverage = summary["coverage_fraction"]
        fraction_sum = math.fsum(bucket.values())
        if fraction_sum < coverage - FRACTION_TOLERANCE:
            raise ValueError(
                f"{area_id}/{dataset_id}: unit fractions do not reach the covered fraction"
            )
        if not math.isclose(fraction_sum, summary["fraction_sum"], abs_tol=FRACTION_TOLERANCE):
            raise ValueError(f"{area_id}/{dataset_id}: summary fraction sum mismatch")
        if summary["coverage_state"] != coverage_state(coverage):
            raise ValueError(f"{area_id}/{dataset_id}: coverage state mismatch")
        for entry in summary["category_fractions"]:
            if not 0 < entry["fraction_of_aoi"] <= 1 + FRACTION_TOLERANCE:
                raise ValueError(f"{area_id}/{dataset_id}: invalid category fraction")
    _reject_similarity_keys(document)
    return document


def _reject_similarity_keys(value: Any, path: str = "") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            lowered = str(key).lower()
            if any(token in lowered for token in ("similarity", "combined_score", "geology_score")):
                raise ValueError(f"area-fraction evidence must not contain {path}/{key}")
            _reject_similarity_keys(item, f"{path}/{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_similarity_keys(item, f"{path}[{index}]")


def write_evidence(document: dict[str, Any], output_dir: str | Path) -> dict[str, Any]:
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    evidence_json = json.dumps(document, indent=2, sort_keys=True, allow_nan=False) + "\n"
    (target / "evidence.json").write_text(evidence_json, encoding="utf-8")
    lines = "".join(
        json.dumps(record, sort_keys=True, allow_nan=False) + "\n" for record in document["records"]
    )
    (target / "evidence.jsonl").write_text(lines, encoding="utf-8")
    manifest = {
        "schema": "geology-area-fraction-manifest-1.0.0",
        "export_version": EXPORT_VERSION,
        "registry_version": document["registry_version"],
        "registry_sha256": document["registry_sha256"],
        "source_record": document["source_record"],
        "source_record_sha256": document["source_record_sha256"],
        "records": len(document["records"]),
        "area_summaries": len(document["area_summaries"]),
        "bodies": sorted({record["body"] for record in document["records"]}),
        "evidence_json_sha256": hashlib.sha256(evidence_json.encode()).hexdigest(),
        "evidence_jsonl_sha256": hashlib.sha256(lines.encode()).hexdigest(),
        "scope_note": (
            "D02 evidence-only area fractions and non-exclusive process labels. "
            "No similarity or combined score; no_coverage is not zero."
        ),
    }
    (target / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--record",
        default="pipelines/geology/records/geology-e920e7e3ae79e176.json",
    )
    parser.add_argument(
        "--registry",
        default="pipelines/geology/area-fraction/registry.json",
    )
    parser.add_argument("--output", default="pipelines/geology/area-fraction")
    parser.add_argument(
        "--report-only",
        action="store_true",
        help="print the validated summary without writing evidence files",
    )
    args = parser.parse_args(argv)
    document = build_evidence(args.record, args.registry)
    if args.report_only:
        print(
            json.dumps(
                {
                    "registry_version": document["registry_version"],
                    "records": len(document["records"]),
                    "area_summaries": document["area_summaries"],
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    print(json.dumps(write_evidence(document, args.output), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
