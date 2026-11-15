"""Versioned arbitrary-location D01-D06 availability/status contract (Issue #141).

This module answers: for a queried body and location/AOI, what is the actual indexed/queryable
status of each dimension/feature in this database and declared source inventory?

Statuses are explicit and never fabricated:
- available: a gated/queryable record exists for the location/support in this database
- modeled: a model-derived value exists (not a direct observation)
- derived: a derived/indexed value exists from verified processing
- evidence: evidence/context exists but is not a legal scored similarity input for this query
- no_coverage: explicitly known lack of coverage for the queried support (where represented)
- unsupported: dimension/feature/body/time/depth/query role is not supported
- not_indexed: no indexed/queryable record intersects the queried location in this database

Missing values remain missing; states do not imply scientific equivalence.
"""

from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml

from analog_api.domain.eligibility import (
    REGISTRY_VERSION,
    build_registry,
    join_audit,
    materialize,
)
from analog_api.domain.errors import AppError
from analog_api.domain.features import CONTEXT_FEATURE_BY_ID, FEATURE_BY_ID
from analog_api.domain.planetary import (
    COORDINATE_CONTRACT_VERSION,
    PLANETARY_BODIES,
    GeographicBBox,
    bbox_contains,
    bbox_intersects,
    geographic_bbox_from_center,
    get_body,
    normalize_longitude,
    projected_bbox_to_geographic,
)

CONTRACT_VERSION = "location-coverage-status-1.0.0"
EVIDENCE_QUERY_VERSION = "aoi-evidence-query-1.0.0"
RECORD_QUERY_DEFAULT_LIMIT = 200
RECORD_QUERY_MAX_LIMIT = 2000
DIMENSION_CODES = ("D01", "D02", "D03", "D04", "D05", "D06")
DIMENSION_LABELS = {
    "D01": "Terrain & Landforms",
    "D02": "Geology & Mineralogy",
    "D03": "Thermal Environment",
    "D04": "Water & Ice",
    "D05": "Solar Illumination & Radiation",
    "D06": "Regolith & Surface Properties",
}


@dataclass(frozen=True, slots=True)
class QueryLocation:
    body: str
    lat: float | None
    lon: float | None
    bbox: GeographicBBox | None


def _root() -> Path:
    # apps/api/src/analog_api/domain/coverage_status.py -> repo root
    return Path(__file__).resolve().parents[5]


def load_source_inventory() -> dict[str, Any]:
    path = _root() / "catalog" / "dimension-source-inventory.yaml"
    if not path.exists():
        return {"dimensions": [], "note": "catalog/dimension-source-inventory.yaml not found"}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def load_dimension_manifests() -> list[dict[str, Any]]:
    """Adapter view over #135-#140 and related dimension manifests/releases.

    This is intentionally read-only and manifest-driven (no per-dimension schema fork): it
    normalizes provenance/coverage metadata from typed bundles, acquisition releases and pilots.
    """
    root = _root()
    manifests: list[dict[str, Any]] = []
    candidates = [
        root / "pipelines/geology/typed/manifest.json",
        root / "pipelines/pilots/d02_spectral_detection/manifest.json",
        root / "pipelines/pilots/d03_planetary_thermal/manifest.json",
        root / "pipelines/pilots/d03_earth_power/manifest.json",
        root / "pipelines/pilots/d04_earth_power/manifest.json",
        root / "pipelines/pilots/d04_water_ice_planetary/manifest.json",
        root / "pipelines/pilots/d05_earth_power/manifest.json",
        root / "pipelines/pilots/d05_spice_illumination/manifest.json",
        root / "pipelines/pilots/d06_earth_soilgrids/manifest.json",
        root / "pipelines/pilots/d06_earth_soilgrids_all_0_5cm/manifest.json",
        root / "pipelines/pilots/d06_earth_soilgrids_depths/manifest.json",
        root / "pipelines/pilots/d06_earth_soilgrids_expansion_01/manifest.json",
    ]
    for path in candidates:
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        manifests.append(
            {
                "path": str(path.relative_to(root)),
                "manifest": payload,
                "kind": _manifest_kind(path, payload),
            }
        )
    return manifests


def _manifest_kind(path: Path, payload: dict[str, Any]) -> str:
    if (path.parent / "observations.jsonl").exists() and payload.get("release_version"):
        return "typed_acquisition_release"
    if (path.parent / "observations.jsonl").exists() and payload.get("export_version"):
        return "typed_export_bundle"
    if payload.get("schema_version") == "spectral-detection-pilot-1.0.0":
        return "spectral_detection_pilot"
    return "manifest"


def _stored_bbox_values(area: dict[str, Any]) -> tuple[float, float, float, float] | None:
    bbox = area.get("bbox")
    if not bbox:
        return None
    try:
        west, south, east, north = (
            float(bbox["west"]),
            float(bbox["south"]),
            float(bbox["east"]),
            float(bbox["north"]),
        )
    except (KeyError, TypeError, ValueError):
        return None
    if not all(math.isfinite(value) for value in (west, south, east, north)):
        return None
    return west, south, east, north


def _stored_lonlat_bbox(
    west: float, south: float, east: float, north: float, *, source: str
) -> GeographicBBox | None:
    """Interpret a stored bbox as signed lon/lat without collapsing a global envelope."""
    if not (-90.0 <= south <= 90.0 and -90.0 <= north <= 90.0 and south <= north):
        return None
    if east - west >= 360.0 - 1e-9:
        return GeographicBBox(-180.0, south, 180.0, north, source=source)
    if not (-180.0 <= west <= 180.0 and -180.0 <= east <= 180.0):
        west = normalize_longitude(west)
        east = normalize_longitude(east)
    return GeographicBBox(
        west=west,
        south=south,
        east=east,
        north=north,
        crosses_antimeridian=west > east,
        source=source,
    )


def _area_geographic_bbox(area: dict[str, Any]) -> GeographicBBox | None:
    """Return a body-frame lon/lat envelope for a stored typed area, when derivable.

    Earth areas are stored in EPSG:4326. Planetary areas may be stored in a projected
    body CRS; those envelopes are transformed with densified edges by
    ``projected_bbox_to_geographic``. Planetocentric lon/lat areas are used directly.
    A projected envelope is not a re-projection of every source pixel and is labelled as
    such in the response.
    """
    values = _stored_bbox_values(area)
    if values is None:
        return None
    west, south, east, north = values
    body = str(area.get("body"))
    if body == "Earth":
        return _stored_lonlat_bbox(
            west, south, east, north, source="stored_earth_epsg4326_envelope"
        )
    if body not in ("Moon", "Mars"):
        return None
    frame = str(area.get("coordinate_frame") or "")
    crs = str(area.get("crs") or "")
    if frame.endswith("_planetocentric"):
        return _stored_lonlat_bbox(
            west, south, east, north, source="stored_planetocentric_lonlat_envelope"
        )
    try:
        return projected_bbox_to_geographic(
            crs=crs, bbox={"west": west, "south": south, "east": east, "north": north}, body=body
        )
    except Exception:
        # A single unlocatable stored area must not abort the whole AOI query. It is
        # reported explicitly through unlocatable_typed_area_ids below.
        return None


def _location_intersects_bbox(query: QueryLocation, bbox: GeographicBBox | None) -> bool:
    if bbox is None:
        return False
    if query.bbox is not None:
        return bbox_intersects(query.bbox, bbox)
    if query.lat is not None and query.lon is not None:
        return bbox_contains(bbox, query.lat, query.lon)
    return False


def _location_intersects_area(query: QueryLocation, area: dict[str, Any]) -> bool:
    if str(area.get("body")) != query.body:
        return False
    return _location_intersects_bbox(query, _area_geographic_bbox(area))


def _location_intersects_candidate(query: QueryLocation, candidate: Any) -> bool:
    if query.body != "Earth":
        return False
    bbox = GeographicBBox(
        west=float(candidate.bbox.west),
        south=float(candidate.bbox.south),
        east=float(candidate.bbox.east),
        north=float(candidate.bbox.north),
    )
    return _location_intersects_bbox(query, bbox)


def _reference_geographic_bbox(reference: Any) -> GeographicBBox | None:
    bbox = getattr(reference, "bbox", None)
    if bbox is not None:
        try:
            return GeographicBBox(
                west=float(bbox.west),
                south=float(bbox.south),
                east=float(bbox.east),
                north=float(bbox.north),
                crosses_antimeridian=float(bbox.west) > float(bbox.east),
            )
        except (TypeError, ValueError):
            pass
    lat = getattr(reference, "lat", None)
    lon = getattr(reference, "lon", None)
    window_m = getattr(reference, "window_size_m", None)
    if lat is None or lon is None or not window_m:
        return None
    try:
        return geographic_bbox_from_center(
            lat=float(lat),
            lon=float(lon),
            window_m=float(window_m),
            body=str(reference.body),
            source="planetary_reference_window",
        )
    except Exception:
        return None


def _location_matches_reference(query: QueryLocation, reference: Any) -> bool:
    if query.body != reference.body:
        return False
    if reference.lat is None or reference.lon is None:
        return False
    # A reference is a bounded analysis window, not a point. Point queries are matched
    # against that window; bbox queries use its envelope. This is not a proximity join to
    # a candidate and never changes a stored area_id.
    bbox = _reference_geographic_bbox(reference)
    if bbox is None:
        if query.lat is None or query.lon is None:
            return False
        return (
            abs(float(reference.lat) - query.lat) <= 0.01
            and abs(float(reference.lon) - query.lon) <= 0.01
        )
    return _location_intersects_bbox(query, bbox)


def _bbox_value(value: Any, name: str) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise AppError(
            "invalid_bbox", f"bbox {name} must be a finite number.", status_code=422
        ) from exc
    if not math.isfinite(numeric):
        raise AppError("invalid_bbox", f"bbox {name} must be finite.", status_code=422)
    return numeric


def _normalized_query_bbox(
    *,
    bbox: dict[str, Any],
    body: str,
    bbox_lon_convention: str,
) -> GeographicBBox:
    """Validate and normalise a body-specific query bbox.

    Earth uses EPSG:4326 signed lon/lat degrees. Moon and Mars use planetocentric
    signed lon/lat degrees in their declared IAU body frame; a client may supply
    0..360 longitude by setting ``bbox_lon_convention=360``. A west > east bbox is an
    explicit antimeridian crossing and is queried as two parts. Polar latitude bands are
    valid; a near-polar window may have a global longitude envelope, which is reported
    rather than silently clamped to an Earth rectangle.
    """
    if bbox_lon_convention not in {"signed", "360"}:
        raise AppError(
            "invalid_longitude_convention",
            "bbox_lon_convention must be 'signed' or '360'.",
            status_code=422,
        )
    west = _bbox_value(bbox.get("west"), "west")
    south = _bbox_value(bbox.get("south"), "south")
    east = _bbox_value(bbox.get("east"), "east")
    north = _bbox_value(bbox.get("north"), "north")

    if not (-90.0 <= south <= 90.0 and -90.0 <= north <= 90.0):
        raise AppError(
            "invalid_bbox_latitude",
            "bbox south/north must be within [-90, 90] degrees.",
            status_code=422,
        )
    if south >= north:
        raise AppError(
            "invalid_bbox",
            "bbox south must be strictly less than north.",
            status_code=422,
        )

    if bbox_lon_convention == "360":
        raw_span = east - west
        if raw_span >= 360.0 - 1e-9:
            west, east = -180.0, 180.0
        else:
            west = normalize_longitude(west)
            east = normalize_longitude(east)
    elif not (-180.0 <= west <= 180.0 and -180.0 <= east <= 180.0):
        raise AppError(
            "invalid_bbox_longitude",
            "bbox west/east must be within [-180, 180] for signed longitude; use "
            "bbox_lon_convention=360 for 0..360 input.",
            status_code=422,
        )

    if west == east and not (west == -180.0 and east == 180.0):
        raise AppError(
            "invalid_bbox",
            "bbox west and east must differ; use a lat/lon point query for a zero-width "
            "footprint or -180/180 for a global longitude envelope.",
            status_code=422,
        )

    if body in ("Moon", "Mars"):
        get_body(body)
    return GeographicBBox(
        west=west,
        south=south,
        east=east,
        north=north,
        crosses_antimeridian=west > east,
        source="query",
    )


def _query_from_input(
    *,
    body: str,
    lat: float | None,
    lon: float | None,
    bbox: dict[str, float] | None,
    bbox_lon_convention: str,
) -> QueryLocation:
    if body not in ("Earth", "Moon", "Mars"):
        raise AppError("invalid_body", "body must be Earth, Moon or Mars.", status_code=422)
    has_point = lat is not None or lon is not None
    has_bbox = bbox is not None
    if has_point == has_bbox:
        raise AppError(
            "validation_error",
            "Provide either lat/lon or bbox, but not both.",
            status_code=422,
        )
    if has_point:
        if lat is None or lon is None:
            raise AppError(
                "validation_error", "lat and lon must both be provided.", status_code=422
            )
        if not math.isfinite(float(lat)) or not math.isfinite(float(lon)):
            raise AppError("invalid_coordinate", "lat/lon must be finite.", status_code=422)
        if not (-90.0 <= float(lat) <= 90.0):
            raise AppError("invalid_latitude", "lat must be within [-90, 90].", status_code=422)
        if bbox_lon_convention not in {"signed", "360"}:
            raise AppError(
                "invalid_longitude_convention",
                "bbox_lon_convention must be 'signed' or '360'.",
                status_code=422,
            )
        if bbox_lon_convention == "360" or body in ("Moon", "Mars"):
            lon = normalize_longitude(float(lon))
        else:
            if not (-180.0 <= float(lon) <= 180.0):
                raise AppError(
                    "invalid_longitude",
                    "lon must be within [-180, 180] for Earth EPSG:4326.",
                    status_code=422,
                )
            lon = float(lon)
        return QueryLocation(body=body, lat=float(lat), lon=float(lon), bbox=None)
    assert bbox is not None
    gb = _normalized_query_bbox(bbox=bbox, body=body, bbox_lon_convention=bbox_lon_convention)
    return QueryLocation(body=body, lat=None, lon=None, bbox=gb)


@dataclass(frozen=True, slots=True)
class AoiRecordFilters:
    """Opt-in filters for the AOI observation-record view.

    Filters are explicit selectors, never a data-repair step: a record without declared
    time/depth support is excluded with a machine-readable reason instead of being assumed
    to match, and a filtered-out observation is never replaced by zero.
    """

    dimensions: frozenset[str] = frozenset()
    feature_ids: frozenset[str] = frozenset()
    states: frozenset[str] = frozenset()
    dataset_ids: frozenset[str] = frozenset()
    source_asset_ids: frozenset[str] = frozenset()
    time_start: date | None = None
    time_end: date | None = None
    depth_min_m: float | None = None
    depth_max_m: float | None = None

    @property
    def active(self) -> bool:
        return any(
            (
                self.dimensions,
                self.feature_ids,
                self.states,
                self.dataset_ids,
                self.source_asset_ids,
                self.time_start is not None,
                self.time_end is not None,
                self.depth_min_m is not None,
                self.depth_max_m is not None,
            )
        )

    def to_payload(self) -> dict[str, Any]:
        def values(items: frozenset[str]) -> list[str] | None:
            return sorted(items) if items else None

        return {
            "active": self.active,
            "dimension": values(self.dimensions),
            "feature_id": values(self.feature_ids),
            "state": values(self.states),
            "dataset_id": values(self.dataset_ids),
            "source_asset_id": values(self.source_asset_ids),
            "time_start": self.time_start.isoformat() if self.time_start else None,
            "time_end": self.time_end.isoformat() if self.time_end else None,
            "depth_min_m": self.depth_min_m,
            "depth_max_m": self.depth_max_m,
        }


def _split_filter(value: str | None) -> frozenset[str]:
    if not value:
        return frozenset()
    return frozenset(part.strip() for part in value.split(",") if part.strip())


def _parse_time_date(value: str | None, field: str) -> date | None:
    if value is None or not str(value).strip():
        return None
    text = str(value).strip()
    try:
        if len(text) == 10:
            return date.fromisoformat(text)
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError as exc:
        raise AppError(
            "invalid_time_window",
            f"{field} must be an ISO date (YYYY-MM-DD) or ISO datetime.",
            status_code=422,
        ) from exc


def _build_record_filters(
    *,
    dimension: str | None,
    feature_id: str | None,
    state: str | None,
    dataset_id: str | None,
    source_asset_id: str | None,
    time_start: str | None,
    time_end: str | None,
    depth_min_m: float | None,
    depth_max_m: float | None,
) -> AoiRecordFilters:
    dimensions = _split_filter(dimension)
    invalid_dimensions = sorted(dimensions - set(DIMENSION_CODES))
    if invalid_dimensions:
        raise AppError(
            "invalid_dimension",
            f"Unknown dimension filter {invalid_dimensions}; expected D01-D06.",
            status_code=422,
        )
    start = _parse_time_date(time_start, "time_start")
    end = _parse_time_date(time_end, "time_end")
    if start is not None and end is not None and start > end:
        raise AppError(
            "invalid_time_window",
            "time_start must be on or before time_end.",
            status_code=422,
        )
    for label, value in (("depth_min_m", depth_min_m), ("depth_max_m", depth_max_m)):
        if value is None:
            continue
        if not math.isfinite(float(value)) or float(value) < 0:
            raise AppError(
                "invalid_depth_window",
                f"{label} must be a finite non-negative depth in metres.",
                status_code=422,
            )
    if (
        depth_min_m is not None
        and depth_max_m is not None
        and float(depth_min_m) > float(depth_max_m)
    ):
        raise AppError(
            "invalid_depth_window",
            "depth_min_m must be on or before depth_max_m.",
            status_code=422,
        )
    return AoiRecordFilters(
        dimensions=dimensions,
        feature_ids=_split_filter(feature_id),
        states=_split_filter(state),
        dataset_ids=_split_filter(dataset_id),
        source_asset_ids=_split_filter(source_asset_id),
        time_start=start,
        time_end=end,
        depth_min_m=float(depth_min_m) if depth_min_m is not None else None,
        depth_max_m=float(depth_max_m) if depth_max_m is not None else None,
    )


def _bbox_contract_payload(body: str, query: QueryLocation) -> dict[str, Any]:
    """Document the body-specific coordinate contract actually applied to this query."""
    antimeridian = {
        "supported": True,
        "rule": (
            "west > east is an explicit antimeridian crossing; both parts are queried as "
            "west..180 and -180..east. It is never wrapped into an Earth bbox."
        ),
        "split_parts": [part.as_dict() for part in query.bbox.split()] if query.bbox else [],
    }
    polar = {
        "supported": True,
        "latitude_bounds_deg": [-90.0, 90.0],
        "rule": (
            "Polar latitude bands are valid. A near-polar window may span all longitudes; the "
            "response reports the envelope actually used rather than clamping it to a rectangle."
        ),
    }
    if body == "Earth":
        return {
            "body": "Earth",
            "coordinate_contract_version": COORDINATE_CONTRACT_VERSION,
            "coordinate_system": "EPSG:4326",
            "latitude_kind": "geodetic",
            "longitude_direction": "positive_east",
            "longitude_domain_deg": [-180.0, 180.0],
            "earth_wgs84": True,
            "antimeridian": antimeridian,
            "polar": polar,
            "note": (
                "Earth query bboxes use EPSG:4326 lon/lat degrees. They are not a planetary "
                "body frame and are not interpreted as any Moon/Mars CRS."
            ),
        }
    info = PLANETARY_BODIES[body]
    return {
        "body": body,
        "coordinate_contract_version": COORDINATE_CONTRACT_VERSION,
        "coordinate_system": info.coordinate_system,
        "frame": info.frame,
        "latitude_kind": info.latitude_kind,
        "longitude_direction": info.longitude_direction,
        "longitude_domain_deg": [-180.0, 180.0],
        "native_longitude_domain": info.native_longitude_domain,
        "geographic_epsg": info.geographic_epsg,
        "geographic_epsg_is_epsg_registry": False,
        "earth_wgs84": False,
        "antimeridian": antimeridian,
        "polar": polar,
        "projected_area_handling": (
            "Stored projected typed-area envelopes are transformed to planetocentric lon/lat "
            "envelopes with densified edges. Intersections are envelope-level, not per-pixel."
        ),
        "note": (
            "Moon/Mars coordinates are planetocentric lon/lat in the declared IAU body frame. "
            "They must never be passed to Earth WGS84/OSM tooling. Planetographic latitude is "
            "not silently substituted."
        ),
    }


def _first_number(*values: Any) -> float | None:
    for value in values:
        if isinstance(value, bool) or value is None:
            continue
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(numeric):
            return numeric
    return None


def _resolution_payload(spatial: dict[str, Any] | None) -> dict[str, Any]:
    spatial = spatial or {}
    native_m = _first_number(
        spatial.get("native_resolution_m"),
        spatial.get("source_resolution_m"),
        spatial.get("resolution_m"),
        spatial.get("analysis_resolution_m"),
    )
    analysis_m = _first_number(spatial.get("analysis_resolution_m"), spatial.get("resolution_m"))
    return {
        "kind": spatial.get("kind"),
        "resolution_m": analysis_m if analysis_m is not None else native_m,
        "native_m": native_m,
        "analysis_m": analysis_m,
        "native_resolution_m": native_m,
        "source_resolution_m": native_m,
        "analysis_resolution_m": analysis_m,
        "window_size_m": _first_number(spatial.get("window_size_m")),
        "crs": spatial.get("crs") or spatial.get("bbox_crs"),
        "valid_fraction": spatial.get("valid_fraction"),
        "bbox": spatial.get("bbox"),
        "note": spatial.get("notes"),
    }


def _value_summary(value: Any, *, value_kind: str | None, unit: str | None) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "kind": value_kind,
        "unit": unit,
        "is_numeric": False,
        "sample_count": None,
        "numeric_min": None,
        "numeric_max": None,
    }
    if isinstance(value, bool):
        summary["sample_count"] = 1
        return summary
    if isinstance(value, (int, float)):
        numeric = float(value)
        summary.update(
            {
                "is_numeric": math.isfinite(numeric),
                "sample_count": 1,
                "numeric_min": numeric if math.isfinite(numeric) else None,
                "numeric_max": numeric if math.isfinite(numeric) else None,
            }
        )
        return summary
    if isinstance(value, dict):
        samples = value.get("values")
        if isinstance(samples, list):
            numeric_samples = [
                float(item)
                for item in samples
                if isinstance(item, (int, float))
                and not isinstance(item, bool)
                and math.isfinite(float(item))
            ]
            summary["sample_count"] = len(samples)
            if numeric_samples:
                summary["numeric_min"] = min(numeric_samples)
                summary["numeric_max"] = max(numeric_samples)
            summary["is_numeric"] = bool(numeric_samples) and len(numeric_samples) == len(samples)
            summary["timestamps"] = value.get("timestamps")
        else:
            summary["sample_count"] = 1
            summary["keys"] = sorted(str(key) for key in value)
        return summary
    if isinstance(value, list):
        summary["sample_count"] = len(value)
        return summary
    return summary


def _date_from_temporal(value: Any) -> date | None:
    if value is None or not str(value).strip():
        return None
    text = str(value).strip()
    try:
        if len(text) == 10:
            return date.fromisoformat(text)
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _temporal_filter_reason(
    temporal: dict[str, Any] | None, filters: AoiRecordFilters
) -> str | None:
    if filters.time_start is None and filters.time_end is None:
        return None
    temporal = temporal or {}
    start = _date_from_temporal(temporal.get("start"))
    end = _date_from_temporal(temporal.get("end"))
    if start is None and end is None:
        if str(temporal.get("kind") or "") == "static":
            return "temporal_support_static_without_declared_interval"
        return "temporal_support_unknown"
    start = start or end
    end = end or start
    assert start is not None and end is not None
    if filters.time_start is not None and end < filters.time_start:
        return "temporal_interval_before_query_window"
    if filters.time_end is not None and start > filters.time_end:
        return "temporal_interval_after_query_window"
    return None


def _depth_filter_reason(depth: dict[str, Any] | None, filters: AoiRecordFilters) -> str | None:
    if filters.depth_min_m is None and filters.depth_max_m is None:
        return None
    depth = depth or {}
    min_m = _first_number(depth.get("min_m"))
    max_m = _first_number(depth.get("max_m"))
    if min_m is None or max_m is None:
        return "depth_support_missing"
    if filters.depth_min_m is not None and max_m < filters.depth_min_m:
        return "depth_interval_shallower_than_query"
    if filters.depth_max_m is not None and min_m > filters.depth_max_m:
        return "depth_interval_deeper_than_query"
    return None


def _record_filter_reasons(record: dict[str, Any], filters: AoiRecordFilters) -> list[str]:
    reasons: list[str] = []
    if filters.dimensions and record.get("dimension_code") not in filters.dimensions:
        reasons.append("dimension_not_matched")
    if filters.feature_ids and record.get("feature_id") not in filters.feature_ids:
        reasons.append("feature_not_matched")
    if filters.states and record.get("state") not in filters.states:
        reasons.append("state_not_matched")
    if filters.dataset_ids:
        dataset_ids = {str(item) for item in record.get("dataset_ids") or []}
        if not (dataset_ids & set(filters.dataset_ids)):
            reasons.append("dataset_not_matched")
    if filters.source_asset_ids:
        source_ids = {str(item) for item in record.get("source_asset_ids") or []}
        if not (source_ids & set(filters.source_asset_ids)):
            reasons.append("source_asset_not_matched")
    time_reason = _temporal_filter_reason(record.get("temporal_support"), filters)
    if time_reason:
        reasons.append(time_reason)
    depth_reason = _depth_filter_reason(record.get("depth_support"), filters)
    if depth_reason:
        reasons.append(depth_reason)
    return reasons


def _record_roles(
    *,
    entry: Any,
    state: str | None,
    rankable: bool,
    score_role: str | None,
) -> dict[str, Any]:
    detected = state == "detected"
    similarity = bool(entry and entry.similarity_supported and detected and rankable)
    hard_filter = bool(entry and entry.hard_filter_supported and detected and rankable)
    context = bool(score_role == "context_only" or (entry and entry.score_role == "context_only"))
    uncertainty = bool(
        entry and entry.roles.get("uncertainty") and entry.roles["uncertainty"].supported
    )
    if context:
        primary = "context"
    elif similarity:
        primary = "score"
    elif hard_filter:
        primary = "filter"
    else:
        primary = "evidence"
    return {
        "query": bool(similarity or hard_filter),
        "score": similarity,
        "filter": hard_filter,
        "evidence": True,
        "context": context,
        "uncertainty": uncertainty,
        "primary": primary,
    }


def _record_status(*, state: str | None, value: Any, roles: dict[str, Any]) -> str:
    if state == "no_coverage":
        return "no_coverage"
    if state in {"unknown", "not_detected"}:
        return "no_data"
    if state == "not_applicable":
        return "unavailable"
    if value is None:
        return "no_data"
    if roles.get("context"):
        return "context"
    if roles.get("query"):
        return "available"
    return "evidence"


def _missing_reason(state: str | None, value: Any) -> str | None:
    if state == "no_coverage":
        return "provider_declares_no_coverage"
    if state == "unknown":
        return "state_unknown_not_zero_filled"
    if state == "not_detected":
        return "state_not_detected_not_zero_filled"
    if state == "not_applicable":
        return "not_applicable_for_this_body_or_support"
    if value is None:
        return "value_missing_not_zero_filled"
    return None


def _join_kind(area_id: str, *, body: str, candidate_ids: set[str], reference_ids: set[str]) -> str:
    if body == "Earth" and area_id in candidate_ids:
        return "exact_candidate"
    if area_id in reference_ids:
        return "exact_reference"
    return "no_exact_join"


def _join_payload(
    *,
    kind: str,
    rankable: bool,
    reason: str | None,
) -> dict[str, Any]:
    return {
        "kind": kind,
        "exact_id_match": kind != "no_exact_join",
        "rankable": rankable,
        "viewable_only": not rankable,
        "proximity_join": False,
        "join_key": "typed_areas.area_id == candidates.location_id or reference_id (exact string)",
        "reason": reason,
        "note": (
            "Records without an exact candidate/reference id remain viewable evidence and are "
            "never proximity-joined or copied to another AOI."
        ),
    }


def _inventory_index(inventory: dict[str, Any]) -> dict[tuple[str, str], list[dict[str, Any]]]:
    index: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for dimension in inventory.get("dimensions", []):
        code = str(dimension.get("code") or "")
        bodies = dimension.get("bodies") or {}
        for body, body_info in bodies.items():
            for source in body_info.get("sources") or []:
                index[(body, code)].append(source)
    return index


def build_location_coverage(
    *,
    repo: Any,
    typed_store: Any,
    body: str,
    lat: float | None = None,
    lon: float | None = None,
    bbox: dict[str, float] | None = None,
    bbox_lon_convention: str = "signed",
    include_records: bool = False,
    dimension: str | None = None,
    feature_id: str | None = None,
    state: str | None = None,
    dataset_id: str | None = None,
    source_asset_id: str | None = None,
    time_start: str | None = None,
    time_end: str | None = None,
    depth_min_m: float | None = None,
    depth_max_m: float | None = None,
    max_records: int = RECORD_QUERY_DEFAULT_LIMIT,
) -> dict[str, Any]:
    """Body-aware D01-D06 coverage plus an opt-in AOI observation-record view.

    ``records`` is only populated when ``include_records`` is true. The record view is a
    read-only projection of intersecting typed observations and indexed D01 values; it
    never copies observations from another AOI, never proximity-joins an area id, and
    never zero-fills a missing value. ``include_records=false`` keeps the pre-#160
    response shape (plus additive metadata) for existing callers.
    """
    if not (1 <= int(max_records) <= RECORD_QUERY_MAX_LIMIT):
        raise AppError(
            "invalid_record_limit",
            f"max_records must be between 1 and {RECORD_QUERY_MAX_LIMIT}.",
            status_code=422,
        )
    filters = _build_record_filters(
        dimension=dimension,
        feature_id=feature_id,
        state=state,
        dataset_id=dataset_id,
        source_asset_id=source_asset_id,
        time_start=time_start,
        time_end=time_end,
        depth_min_m=depth_min_m,
        depth_max_m=depth_max_m,
    )
    query = _query_from_input(
        body=body, lat=lat, lon=lon, bbox=bbox, bbox_lon_convention=bbox_lon_convention
    )

    candidates = list(repo.list_candidates()) if body == "Earth" else []
    references = [ref for ref in repo.list_references() if ref.body == body]
    areas = typed_store.list_areas(body=body)
    rows = typed_store.list_observations(body=body)
    registry = build_registry(rows)
    candidate_ids = {str(c.location_id) for c in candidates}
    reference_ids = {str(r.reference_id) for r in references}
    candidate_by_id = {str(c.location_id): c for c in candidates}
    reference_by_id = {str(r.reference_id): r for r in references}
    candidate_bboxes = {cid: candidate.bbox.as_dict() for cid, candidate in candidate_by_id.items()}
    materialization = materialize(
        registry=registry,
        rows=rows,
        candidate_bboxes=candidate_bboxes,
    )
    rankable_keys = set(materialization.values)
    rejected_reasons: dict[tuple[str, str], list[str]] = defaultdict(list)
    for rejection in materialization.rejected:
        rejected_reasons[(str(rejection.area_id), str(rejection.feature_id))].extend(
            rejection.reasons
        )

    area_bboxes = {str(area.get("area_id")): _area_geographic_bbox(area) for area in areas}
    intersecting_candidates = [c for c in candidates if _location_intersects_candidate(query, c)]
    intersecting_references = [r for r in references if _location_matches_reference(query, r)]
    intersecting_areas = [
        area
        for area in areas
        if str(area.get("body")) == query.body
        and _location_intersects_bbox(query, area_bboxes.get(str(area.get("area_id"))))
    ]
    intersecting_area_ids = {str(a["area_id"]) for a in intersecting_areas}
    unlocatable_area_ids = sorted(
        area_id for area_id, area_bbox in area_bboxes.items() if area_bbox is None
    )
    # Planetary projected typed areas may only be joinable by exact id. If an exact-linked
    # reference/candidate is in this AOI, its known window supplies the location; this is an
    # exact string-id link, never a nearest-location merge.
    for area in areas:
        area_id = str(area.get("area_id"))
        if area_id in intersecting_area_ids:
            continue
        reference = reference_by_id.get(area_id)
        if reference is not None and _location_matches_reference(query, reference):
            intersecting_area_ids.add(area_id)
            continue
        candidate = candidate_by_id.get(area_id)
        if candidate is not None and _location_intersects_candidate(query, candidate):
            intersecting_area_ids.add(area_id)
    location_rows = [row for row in rows if str(row.get("area_id")) in intersecting_area_ids]

    inventory = load_source_inventory()
    inv_index = _inventory_index(inventory)
    manifests = load_dimension_manifests()

    dimensions_out: dict[str, Any] = {}
    for dim in DIMENSION_CODES:
        declared_sources = inv_index.get((body, dim), [])
        dim_rows = [row for row in location_rows if row.get("dimension_code") == dim]
        states = sorted({str(row.get("state")) for row in dim_rows})
        features_out: list[dict[str, Any]] = []
        for row in sorted(
            dim_rows, key=lambda r: (str(r.get("feature_id")), str(r.get("observation_id")))
        ):
            feature_id = str(row.get("feature_id"))
            entry = registry.get(feature_id)
            row_area_id = str(row.get("area_id"))
            key = (row_area_id, feature_id)
            is_rankable = key in rankable_keys
            join_status = _join_kind(
                row_area_id,
                body=body,
                candidate_ids=candidate_ids,
                reference_ids=reference_ids,
            )
            if is_rankable:
                rankability_reason = None
            else:
                reasons = rejected_reasons.get(key, [])
                if reasons:
                    rankability_reason = reasons[0]
                elif join_status == "no_exact_join":
                    rankability_reason = "no_exact_candidate_or_reference_id"
                elif join_status == "exact_reference":
                    rankability_reason = (
                        "typed_evidence_viewable_only_reference_rank_uses_indexed_d01"
                    )
                elif entry and entry.similarity_supported:
                    rankability_reason = "not_materialized_for_query"
                else:
                    rankability_reason = "not_registered_for_similarity_or_filter"
            if row.get("state") == "detected":
                status = (
                    "available"
                    if (is_rankable and entry and entry.similarity_supported)
                    else "evidence"
                )
            elif row.get("state") == "no_coverage":
                status = "no_coverage"
            elif row.get("state") in {"unknown", "not_applicable", "not_detected"}:
                status = "evidence" if entry else row.get("state")
            else:
                status = "evidence"
            if str(row.get("value_kind")) in {"timeseries"} and row.get("state") == "detected":
                status = "evidence"
            roles = _record_roles(
                entry=entry,
                state=row.get("state"),
                rankable=is_rankable,
                score_role=row.get("score_role"),
            )
            features_out.append(
                {
                    "feature_id": feature_id,
                    "dimension_code": dim,
                    "status": status,
                    "state": row.get("state"),
                    "value_kind": row.get("value_kind"),
                    "value": row.get("value"),
                    "value_summary": _value_summary(
                        row.get("value"),
                        value_kind=row.get("value_kind"),
                        unit=row.get("unit"),
                    ),
                    "score_role": row.get("score_role"),
                    "roles": roles,
                    "legal_roles": (
                        [role for role, s in entry.roles.items() if s.supported] if entry else []
                    ),
                    "unit": row.get("unit"),
                    "method_version": row.get("method_version"),
                    "dataset_ids": row.get("dataset_ids") or [],
                    "source_asset_ids": row.get("source_asset_ids") or [],
                    "quality_flags": row.get("quality_flags") or [],
                    "spatial_support": row.get("spatial_support"),
                    "resolution": _resolution_payload(row.get("spatial_support")),
                    "temporal_support": row.get("temporal_support"),
                    "depth_support": row.get("depth_support"),
                    "uncertainty": row.get("uncertainty"),
                    "notes": row.get("notes"),
                    "observation_id": row.get("observation_id"),
                    "area_id": row.get("area_id"),
                    "rankable": is_rankable,
                    "join_status": join_status,
                    "rankability_reason": rankability_reason,
                    "missing_reason": _missing_reason(row.get("state"), row.get("value")),
                }
            )

        # D01 legacy terrain presence for Earth indexed candidates intersecting location.
        d01_legacy: list[dict[str, Any]] = []
        if dim == "D01":
            for candidate in intersecting_candidates:
                for feature_id, obs in candidate.features.items():
                    definition = FEATURE_BY_ID.get(feature_id)
                    d01_legacy.append(
                        {
                            "feature_id": feature_id,
                            "dimension_code": "D01",
                            "status": "derived",
                            "state": "detected",
                            "value_kind": definition.value_kind if definition else "numeric",
                            "score_role": definition.score_role if definition else "similarity",
                            "roles": _record_roles(
                                entry=registry.get(feature_id),
                                state="detected",
                                rankable=True,
                                score_role=(definition.score_role if definition else "similarity"),
                            ),
                            "legal_roles": ["similarity", "hard_filter", "evidence"],
                            "unit": obs.unit,
                            "method_version": obs.method_version,
                            "dataset_ids": [candidate.dataset_id],
                            "source_asset_ids": [],
                            "quality_flags": list(candidate.quality_flags),
                            "spatial_support": {
                                "kind": "window",
                                "bbox": candidate.bbox.as_dict(),
                                "window_size_m": candidate.window_size_m,
                                "analysis_resolution_m": candidate.analysis_resolution_m,
                                "valid_fraction": obs.valid_fraction,
                                "crs": candidate.crs,
                            },
                            "resolution": _resolution_payload(
                                {
                                    "kind": "window",
                                    "analysis_resolution_m": candidate.analysis_resolution_m,
                                    "window_size_m": candidate.window_size_m,
                                    "valid_fraction": obs.valid_fraction,
                                    "crs": candidate.crs,
                                    "bbox": candidate.bbox.as_dict(),
                                }
                            ),
                            "temporal_support": {"kind": "static"},
                            "depth_support": {"kind": "surface"},
                            "uncertainty": None,
                            "notes": None,
                            "observation_id": f"legacy:{candidate.location_id}:{feature_id}",
                            "area_id": candidate.location_id,
                            "origin": candidate.origin,
                            "value": obs.value,
                            "value_summary": _value_summary(
                                obs.value,
                                value_kind=(definition.value_kind if definition else "numeric"),
                                unit=obs.unit,
                            ),
                            "rankable": True,
                            "join_status": "exact_candidate",
                            "rankability_reason": None,
                            "missing_reason": None,
                        }
                    )
            for ref in intersecting_references:
                for feature_id, obs in ref.features.items():
                    definition = FEATURE_BY_ID.get(feature_id)
                    d01_legacy.append(
                        {
                            "feature_id": feature_id,
                            "dimension_code": "D01",
                            "status": "derived",
                            "state": "detected",
                            "value_kind": definition.value_kind if definition else "numeric",
                            "score_role": definition.score_role if definition else "similarity",
                            "roles": _record_roles(
                                entry=registry.get(feature_id),
                                state="detected",
                                rankable=True,
                                score_role=(definition.score_role if definition else "similarity"),
                            ),
                            "legal_roles": ["similarity", "hard_filter", "evidence"],
                            "unit": obs.unit,
                            "method_version": obs.method_version,
                            "dataset_ids": [ref.dataset_id] if ref.dataset_id else [],
                            "source_asset_ids": [],
                            "quality_flags": list(ref.quality_flags),
                            "spatial_support": {
                                "kind": "reference_window",
                                "window_size_m": ref.window_size_m,
                                "analysis_resolution_m": ref.analysis_resolution_m,
                                "valid_fraction": ref.valid_fraction,
                                "crs": ref.crs,
                            },
                            "resolution": _resolution_payload(
                                {
                                    "kind": "reference_window",
                                    "analysis_resolution_m": ref.analysis_resolution_m,
                                    "window_size_m": ref.window_size_m,
                                    "valid_fraction": ref.valid_fraction,
                                    "crs": ref.crs,
                                }
                            ),
                            "temporal_support": {"kind": "static"},
                            "depth_support": {"kind": "surface"},
                            "uncertainty": None,
                            "notes": ref.notes,
                            "observation_id": f"reference:{ref.reference_id}:{feature_id}",
                            "area_id": ref.reference_id,
                            "origin": "planetary_reference",
                            "value": obs.value,
                            "value_summary": _value_summary(
                                obs.value,
                                value_kind=(definition.value_kind if definition else "numeric"),
                                unit=obs.unit,
                            ),
                            "rankable": True,
                            "join_status": "exact_reference",
                            "rankability_reason": None,
                            "missing_reason": None,
                        }
                    )

        all_features = d01_legacy + features_out
        role_score = any(
            f.get("rankable", True)
            and "similarity" in (f.get("legal_roles") or [])
            and f.get("state") == "detected"
            for f in all_features
        )
        role_filter = any(
            f.get("rankable", True)
            and "hard_filter" in (f.get("legal_roles") or [])
            and f.get("state") == "detected"
            for f in all_features
        )
        role_context = any(
            f.get("score_role") == "context_only" or "context" in (f.get("legal_roles") or [])
            for f in all_features
        )
        role_uncertainty = any("uncertainty" in (f.get("legal_roles") or []) for f in all_features)
        if role_context:
            primary_role = "context"
        elif role_score:
            primary_role = "score"
        elif role_filter:
            primary_role = "filter"
        elif all_features:
            primary_role = "evidence"
        else:
            primary_role = "none"
        dimension_roles = {
            "query": bool(role_score or role_filter),
            "score": role_score,
            "filter": role_filter,
            "evidence": bool(all_features),
            "context": role_context,
            "uncertainty": role_uncertainty,
            "primary": primary_role,
        }
        if all_features:
            dim_status = "partially_available"
            if any(f["status"] in {"available", "derived", "modeled"} for f in all_features):
                dim_status = "available_or_indexed"
            elif any(f["status"] == "no_coverage" for f in all_features):
                dim_status = "explicit_no_coverage"
            else:
                dim_status = "evidence_only"
        else:
            # No intersecting records: distinguish declared sources vs unknown in this DB.
            if declared_sources:
                statuses = {s.get("data_status") for s in declared_sources}
                if statuses <= {"not_fetched", "planned", "unknown"}:
                    dim_status = "not_indexed"
                elif any(s.get("availability") == "verified" for s in declared_sources):
                    dim_status = "not_indexed_at_location"
                else:
                    dim_status = "not_indexed"
            else:
                dim_status = "unsupported"

        dimensions_out[dim] = {
            "dimension_code": dim,
            "label": DIMENSION_LABELS[dim],
            "status": dim_status,
            "intersecting_record_count": len(all_features),
            "states": states,
            "roles": dimension_roles,
            "features": all_features,
            "declared_sources": [
                {
                    "source_id": s.get("source_id"),
                    "provider": s.get("provider"),
                    "product": s.get("product"),
                    "availability": s.get("availability"),
                    "data_status": s.get("data_status"),
                    "comparability": s.get("comparability"),
                    "license": s.get("license"),
                    "version": s.get("version", "unknown"),
                    "native_resolution": s.get("native_resolution"),
                    "spatial_support": s.get("spatial_support"),
                    "temporal_support": s.get("temporal_support"),
                    "depth_support": s.get("depth_support"),
                    "qa": s.get("qa"),
                    "url": s.get("url"),
                    "notes": s.get("notes"),
                    "unknown_fields": s.get("unknown_fields") or [],
                }
                for s in declared_sources
            ],
        }

    # context features (Earth land cover) as a separate non-D01-D06 context layer
    context_hits = []
    if body == "Earth":
        for candidate in intersecting_candidates:
            for fid, obs in candidate.context_observations.items():
                definition = CONTEXT_FEATURE_BY_ID.get(fid)
                context_hits.append(
                    {
                        "feature_id": fid,
                        "label": definition.label if definition else fid,
                        "status": "derived",
                        "score_role": "context_only",
                        "value": obs.value,
                        "unit": obs.unit,
                        "method_version": obs.method_version,
                        "dataset_id": obs.dataset_id,
                        "provider": obs.provider,
                        "quality": obs.quality,
                        "valid_fraction": obs.valid_fraction,
                        "spatial_support": {
                            "kind": "aoi_or_window",
                            "source_resolution_m": obs.source_resolution_m,
                        },
                    }
                )

    # Opt-in observation-record view. The dimension feature lists above are the same
    # records, so the view can never assert a different value, state, or join than the
    # per-dimension contract. No record is copied from another AOI.
    all_records: list[dict[str, Any]] = []
    for dim in DIMENSION_CODES:
        dimension_payload = dimensions_out.get(dim) or {}
        for feature in dimension_payload.get("features") or []:
            feature_id_value = str(feature.get("feature_id"))
            entry = registry.get(feature_id_value)
            state_value = feature.get("state")
            value = feature.get("value")
            roles = feature.get("roles") or _record_roles(
                entry=entry,
                state=state_value,
                rankable=bool(feature.get("rankable")),
                score_role=feature.get("score_role"),
            )
            join_status = str(feature.get("join_status") or "no_exact_join")
            rankable = bool(feature.get("rankable"))
            record_status = _record_status(state=state_value, value=value, roles=roles)
            legal_roles = feature.get("legal_roles") or []
            dataset_ids = feature.get("dataset_ids") or []
            source_asset_ids = feature.get("source_asset_ids") or []
            all_records.append(
                {
                    "observation_id": feature.get("observation_id"),
                    "area_id": feature.get("area_id"),
                    "body": body,
                    "dimension_code": dim,
                    "dimension_label": DIMENSION_LABELS.get(dim, dim),
                    "feature_id": feature_id_value,
                    "feature_label": entry.label if entry else feature_id_value,
                    "state": state_value,
                    "status": record_status,
                    "record_status": record_status,
                    "value": value,
                    "value_summary": feature.get("value_summary")
                    or _value_summary(
                        value,
                        value_kind=feature.get("value_kind"),
                        unit=feature.get("unit"),
                    ),
                    "unit": feature.get("unit"),
                    "value_kind": feature.get("value_kind"),
                    "score_role": feature.get("score_role"),
                    "roles": roles,
                    "legal_roles": legal_roles,
                    "query_roles": legal_roles,
                    "dataset_ids": dataset_ids,
                    "source_asset_ids": source_asset_ids,
                    "source": {
                        "dataset_ids": dataset_ids,
                        "source_asset_ids": source_asset_ids,
                        "origin": feature.get("origin") or "typed_observation",
                    },
                    "method_version": feature.get("method_version"),
                    "resolution": feature.get("resolution")
                    or _resolution_payload(feature.get("spatial_support")),
                    "spatial_support": feature.get("spatial_support"),
                    "temporal_support": feature.get("temporal_support"),
                    "depth_support": feature.get("depth_support"),
                    "quality_flags": feature.get("quality_flags") or [],
                    "uncertainty": feature.get("uncertainty"),
                    "notes": feature.get("notes"),
                    "join": _join_payload(
                        kind=join_status,
                        rankable=rankable,
                        reason=feature.get("rankability_reason"),
                    ),
                    "missing_reason": feature.get("missing_reason")
                    or _missing_reason(state_value, value),
                    "record_source": (
                        "indexed_d01_observation" if feature.get("origin") else "typed_observation"
                    ),
                }
            )
    all_records.sort(
        key=lambda record: (
            str(record.get("dimension_code") or ""),
            str(record.get("feature_id") or ""),
            str(record.get("observation_id") or ""),
        )
    )

    filter_matches: list[tuple[dict[str, Any], list[str]]] = [
        (record, _record_filter_reasons(record, filters)) for record in all_records
    ]
    matched_records = [record for record, reasons in filter_matches if not reasons]
    excluded = [
        {
            "observation_id": record.get("observation_id"),
            "area_id": record.get("area_id"),
            "dimension_code": record.get("dimension_code"),
            "feature_id": record.get("feature_id"),
            "reasons": reasons,
        }
        for record, reasons in filter_matches
        if reasons
    ]
    reason_counts = Counter(reason for _, reasons in filter_matches for reason in reasons)
    truncated = include_records and len(matched_records) > int(max_records)
    returned_records = matched_records[: int(max_records)] if include_records else []
    matched_by_dimension: dict[str, int] = {
        dim: sum(1 for record in matched_records if record.get("dimension_code") == dim)
        for dim in DIMENSION_CODES
    }
    evidence_query = {
        "contract_version": EVIDENCE_QUERY_VERSION,
        "include_records": include_records,
        "body": body,
        "bbox_contract": _bbox_contract_payload(body, query),
        "filters": filters.to_payload(),
        "max_records": int(max_records),
        "intersecting_observation_count": len(all_records),
        "matched_observation_count": len(matched_records),
        "returned_observation_count": len(returned_records),
        "matched_by_dimension": matched_by_dimension,
        "truncated": truncated,
        "truncation_note": (
            "Returned records are capped by max_records; the matched count and per-dimension "
            "counts are complete for the intersecting AOI."
            if truncated
            else None
        ),
        "filter_audit": {
            "excluded_observation_count": len(excluded),
            "by_reason": dict(sorted(reason_counts.items())),
            "excluded_examples": excluded[:25],
            "note": (
                "A record without declared time/depth support is excluded when that filter is "
                "active with an explicit reason. Filtered values are never filled with zero."
            ),
        },
        "status_semantics": {
            "available": "an exact-joined queryable value is present and rankable",
            "evidence": "a real intersecting record exists but is not rankable for this query",
            "context": (
                "context/filter evidence (for example Earth-only land cover or body-specific "
                "illumination); never a similarity dimension"
            ),
            "no_data": "state is unknown/not_detected or the value is missing; never zero-filled",
            "no_coverage": "the provider explicitly declares no coverage for this support",
            "unavailable": "not applicable/not supported for this body or support",
            "missing_policy": (
                "Missing is reported as missing. no_data/no_coverage/unavailable are never "
                "converted to zero or copied from another AOI."
            ),
        },
        "join_policy": {
            "typed_area_to_candidate": (
                "exact typed_areas.area_id == candidates.location_id string equality only; "
                "no proximity/nearest-location join"
            ),
            "typed_area_to_reference": (
                "exact typed_areas.area_id == planetary reference_id string equality only"
            ),
            "rankable": (
                "rankable records are exact-joined and pass the versioned "
                "eligibility/materialization gates; all other intersecting records are "
                "viewable_only evidence"
            ),
            "proximity_join_used": False,
        },
        "record_fields": [
            "observation_id",
            "area_id",
            "body",
            "dimension_code",
            "feature_id",
            "state",
            "record_status",
            "value",
            "value_summary",
            "unit",
            "dataset_ids",
            "source_asset_ids",
            "resolution",
            "temporal_support",
            "depth_support",
            "score_role",
            "roles",
            "join",
            "missing_reason",
        ],
    }

    return {
        "contract_version": CONTRACT_VERSION,
        "registry_version": REGISTRY_VERSION,
        "data_version": repo.data_version(),
        "query": {
            "body": body,
            "lat": query.lat,
            "lon": query.lon,
            "bbox": query.bbox.as_dict() if query.bbox else None,
            "query_kind": "bbox" if query.bbox is not None else "point",
            "bbox_lon_convention": bbox_lon_convention,
            "bbox_contract": _bbox_contract_payload(body, query),
            "coordinate_note": (
                "Earth uses EPSG:4326 lon/lat degrees. Moon/Mars use body planetocentric "
                "lon/lat coordinates in their declared IAU frame. Stored planetary projected "
                "typed-area envelopes are transformed into lon/lat envelopes for AOI "
                "intersection; exact-id links and reference windows are also reported."
            ),
        },
        "actual_indexed_coverage": {
            "indexed_earth_candidate_count": len(candidates),
            "planetary_reference_count": len(references),
            "typed_area_count": len(areas),
            "intersecting_candidate_ids": [c.location_id for c in intersecting_candidates],
            "intersecting_reference_ids": [r.reference_id for r in intersecting_references],
            "intersecting_typed_area_ids": sorted(intersecting_area_ids),
            "typed_observation_count_at_location": len(location_rows),
            "unlocatable_typed_area_ids": unlocatable_area_ids,
            "join_audit": join_audit(
                typed_area_ids=[str(area.get("area_id")) for area in areas],
                candidate_ids=candidate_ids,
                reference_ids=reference_ids,
            ),
            "global_coverage_claimed": False,
        },
        "dimensions": dimensions_out,
        "evidence_query": evidence_query,
        "records": returned_records,
        "context_features": context_hits,
        "manifest_adapters": manifests,
        "limitations": [
            "This response reports actual indexed/queryable records in this database plus declared "
            "source inventory; it is not a global survey and does not fabricate missing values.",
            "no_data/no_coverage/unavailable/unsupported/not_indexed are explicit states and are "
            "never converted to zero.",
            "Records are returned only for typed areas intersecting this AOI and indexed D01 "
            "windows; no observation is copied from another AOI. Rankability requires an exact "
            "candidate/reference id; there is no proximity join.",
            "Planetary projected typed-area intersection uses transformed lon/lat envelopes, not "
            "a per-pixel footprint; planetocentric latitude is never Earth WGS84.",
            "A time/depth filter excludes records without declared support and reports the reason; "
            "it never assumes a match.",
            "Land-cover context is Earth-only context/filter data, never mineralogy/geology and never "
            "a similarity dimension.",
        ],
    }
