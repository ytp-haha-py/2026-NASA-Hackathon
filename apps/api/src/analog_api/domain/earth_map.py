"""Earth bbox/viewport map-feature assembly (Issue #132).

Everything here is Earth WGS84 (EPSG:4326), signed ``[-180, 180)`` longitude and
geodetic latitude in ``[-90, 90]``.  Planetary body frames are deliberately not
accepted by this module.

The module is storage independent: the route loads fine/on-demand candidates,
typed areas/observations and the coarse shortlist index, then calls
:func:`build_map_features` to filter, paginate and describe the stored subset.
Missing dimensions are reported as absent and are never zero-filled.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from analog_api.domain.errors import AppError

EARTH_MAP_FEATURES_VERSION = "earth-map-features-1.0.0"
EARTH_COORDINATE_CONTRACT_VERSION = "earth-wgs84-1.0.0"
EARTH_BODY = "Earth"
EARTH_BBOX_CRS = "EPSG:4326"
EARTH_COORDINATE_SYSTEM = "Earth WGS84"
EARTH_COORDINATE_FRAME = "earth_wgs84"

DIMENSION_CODES = ("D01", "D02", "D03", "D04", "D05", "D06")
FEATURE_TYPES = ("all", "fine_candidate", "on_demand_aoi", "typed_area", "coarse_candidate")
DATA_LEVELS = ("all", "fine", "coarse")
AVAILABILITY_VALUES = ("any", "present", "absent")

SUPPORT_FINE = "fine_90m"
SUPPORT_TYPED = "stored_typed_subset"
SUPPORT_COARSE = "shortlist_250m_derived_not_fine"

_GEOMETRIC_EPSILON = 1e-9


@dataclass(frozen=True, slots=True)
class EarthBBox:
    """Earth WGS84 bounding box with explicit antimeridian semantics.

    ``west > east`` means the box crosses 180 degrees.  A full-world query is
    represented as ``west=-180, east=180`` and reports ``is_global() == True``.
    """

    west: float
    south: float
    east: float
    north: float
    crosses_antimeridian: bool = False
    source: str = "query"

    def longitude_span(self) -> float:
        if self.is_global():
            return 360.0
        if self.crosses_antimeridian:
            return (self.east + 360.0) - self.west
        return max(self.east - self.west, 0.0)

    def is_global(self) -> bool:
        return (not self.crosses_antimeridian) and (self.east - self.west) >= (
            360.0 - _GEOMETRIC_EPSILON
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "west": self.west,
            "south": self.south,
            "east": self.east,
            "north": self.north,
            "crosses_antimeridian": self.crosses_antimeridian,
            "longitude_span_deg": self.longitude_span(),
            "source": self.source,
            "crs": EARTH_BBOX_CRS,
        }

    def split(self) -> list[EarthBBox]:
        """Split an antimeridian-crossing box into its two non-crossing parts."""
        if not self.crosses_antimeridian:
            return [self]
        return [
            EarthBBox(self.west, self.south, 180.0, self.north, source=self.source),
            EarthBBox(-180.0, self.south, self.east, self.north, source=self.source),
        ]


def _signed_longitude(value: float) -> float:
    """Normalise to half-open signed longitude while preserving the 180 edge."""
    if value == 180.0 or value == -180.0:
        return value
    return (value + 180.0) % 360.0 - 180.0


def _longitude_from_360(value: float) -> float:
    if value in (0.0, 360.0):
        return 0.0
    if value == 180.0:
        return 180.0
    return value - 360.0 if value > 180.0 else value


def parse_earth_bbox(
    raw: str,
    lon_convention: str = "signed",
    *,
    source: str = "query",
) -> EarthBBox:
    """Parse ``west,south,east,north`` in Earth WGS84 degrees.

    ``lon_convention='signed'`` accepts ``[-180, 180]``; ``'360'`` accepts
    ``[0, 360]`` and normalises the result to signed longitude.  ``west > east``
    is an antimeridian crossing.  ``-180/180`` (or ``0/360``) is the full world.
    """
    if lon_convention not in ("signed", "360"):
        raise AppError(
            "invalid_lon_convention",
            "lon_convention must be 'signed' or '360'.",
            status_code=422,
        )
    parts = [item.strip() for item in raw.split(",")]
    if len(parts) != 4:
        raise AppError(
            "invalid_bbox",
            "bbox must contain four comma-separated values: west,south,east,north.",
            status_code=422,
        )
    try:
        west_raw, south, east_raw, north = (float(item) for item in parts)
    except ValueError as exc:
        raise AppError("invalid_bbox", "bbox values must be numbers.", status_code=422) from exc
    if not all(math.isfinite(value) for value in (west_raw, south, east_raw, north)):
        raise AppError("invalid_bbox", "bbox values must be finite.", status_code=422)
    if not -90.0 <= south <= 90.0 or not -90.0 <= north <= 90.0:
        raise AppError(
            "invalid_bbox_latitude",
            "bbox south/north must be within [-90, 90] geodetic latitude.",
            status_code=422,
        )
    if south >= north:
        raise AppError(
            "invalid_bbox",
            "bbox south must be strictly less than north.",
            status_code=422,
        )

    if lon_convention == "360":
        if not all(0.0 <= value <= 360.0 for value in (west_raw, east_raw)):
            raise AppError(
                "invalid_bbox_longitude",
                "bbox west/east must be within [0, 360] for bbox_lon_convention=360.",
                status_code=422,
            )
        full_world = abs(east_raw - west_raw) >= 360.0 - _GEOMETRIC_EPSILON
        west = _longitude_from_360(west_raw)
        east = _longitude_from_360(east_raw)
    else:
        if not all(-180.0 <= value <= 180.0 for value in (west_raw, east_raw)):
            raise AppError(
                "invalid_bbox_longitude",
                "bbox west/east must be within [-180, 180] for bbox_lon_convention=signed.",
                status_code=422,
            )
        full_world = abs(east_raw - west_raw) >= 360.0 - _GEOMETRIC_EPSILON
        west = _signed_longitude(west_raw)
        east = _signed_longitude(east_raw)

    if full_world:
        return EarthBBox(-180.0, south, 180.0, north, source=source)
    if math.isclose(west, east, abs_tol=_GEOMETRIC_EPSILON):
        raise AppError(
            "invalid_bbox_longitude",
            "bbox west and east must not describe a zero-width longitude span.",
            status_code=422,
        )
    return EarthBBox(
        west=west,
        south=south,
        east=east,
        north=north,
        crosses_antimeridian=west > east,
        source=source,
    )


def global_earth_bbox(*, source: str = "global_default") -> EarthBBox:
    return EarthBBox(-180.0, -90.0, 180.0, 90.0, source=source)


def normalize_earth_longitude(lon: float) -> float:
    """Normalise a point longitude to half-open signed [-180, 180)."""
    if not math.isfinite(lon):
        raise AppError("invalid_bbox_longitude", "longitude must be finite.", status_code=422)
    return (lon + 180.0) % 360.0 - 180.0


def _simple_intersects(left: EarthBBox, right: EarthBBox) -> bool:
    return (
        left.west <= right.east
        and right.west <= left.east
        and left.south <= right.north
        and right.south <= left.north
    )


def bbox_intersects(left: EarthBBox, right: EarthBBox) -> bool:
    """Return whether two Earth WGS84 boxes intersect, including antimeridian/poles."""
    if left.south > right.north or right.south > left.north:
        return False
    if left.is_global() or right.is_global():
        return True
    return any(_simple_intersects(a, b) for a in left.split() for b in right.split())


def bbox_contains(bbox: EarthBBox, lat: float, lon: float) -> bool:
    if not math.isfinite(lat) or not math.isfinite(lon):
        raise AppError("invalid_coordinate", "lat/lon must be finite.", status_code=422)
    if not bbox.south <= lat <= bbox.north:
        return False
    value = normalize_earth_longitude(lon)
    if bbox.is_global():
        return True
    if bbox.crosses_antimeridian:
        return value >= bbox.west or value <= bbox.east
    return bbox.west <= value <= bbox.east


def earth_bbox_geometry(bbox: EarthBBox) -> dict[str, object]:
    """GeoJSON geometry for an Earth WGS84 box; MultiPolygon when it crosses 180."""
    if bbox.crosses_antimeridian:
        rings = [
            [
                [bbox.west, bbox.south],
                [180.0, bbox.south],
                [180.0, bbox.north],
                [bbox.west, bbox.north],
                [bbox.west, bbox.south],
            ],
            [
                [-180.0, bbox.south],
                [bbox.east, bbox.south],
                [bbox.east, bbox.north],
                [-180.0, bbox.north],
                [-180.0, bbox.south],
            ],
        ]
        return {"type": "MultiPolygon", "coordinates": [[ring] for ring in rings]}
    return {
        "type": "Polygon",
        "coordinates": [
            [
                [bbox.west, bbox.south],
                [bbox.east, bbox.south],
                [bbox.east, bbox.north],
                [bbox.west, bbox.north],
                [bbox.west, bbox.south],
            ]
        ],
    }


def earth_point_geometry(lon: float, lat: float) -> dict[str, object]:
    return {"type": "Point", "coordinates": [normalize_earth_longitude(lon), lat]}


def _get(source: object, key: str, default: Any = None) -> Any:
    if isinstance(source, Mapping):
        return source.get(key, default)
    return getattr(source, key, default)


def _candidate_bbox(candidate: object, *, source: str) -> EarthBBox:
    raw = _get(candidate, "bbox")
    if isinstance(raw, Mapping):
        values = raw
    elif raw is not None and hasattr(raw, "west"):
        values = {
            "west": raw.west,
            "south": raw.south,
            "east": raw.east,
            "north": raw.north,
        }
    else:
        values = {
            "west": _get(candidate, "west"),
            "south": _get(candidate, "south"),
            "east": _get(candidate, "east"),
            "north": _get(candidate, "north"),
        }
    return parse_earth_bbox(
        f"{float(values['west'])},{float(values['south'])},"
        f"{float(values['east'])},{float(values['north'])}",
        source=source,
    )


def _is_geographic_crs(crs: str) -> bool:
    upper = crs.upper()
    if upper in {"EPSG:4326", "OGC:CRS84", "CRS:84"}:
        return True
    if "+PROJ=LONGLA" in upper:
        return True
    return "GEOGCRS" in upper and "PROJCRS" not in upper


def _transform_bbox_to_wgs84(*, crs: str, bbox: Mapping[str, object]) -> EarthBBox:
    from pyproj import CRS, Transformer

    west = float(bbox["west"])
    south = float(bbox["south"])
    east = float(bbox["east"])
    north = float(bbox["north"])
    transformer = Transformer.from_crs(
        CRS.from_user_input(crs), CRS.from_epsg(4326), always_xy=True
    )
    steps = 16
    lons: list[float] = []
    lats: list[float] = []
    for index in range(steps + 1):
        fraction = index / steps
        x = west + (east - west) * fraction
        for y in (south, north):
            lon, lat = transformer.transform(x, y)
            if math.isfinite(lon) and math.isfinite(lat):
                lons.append(_signed_longitude(lon))
                lats.append(lat)
    for index in range(steps + 1):
        fraction = index / steps
        y = south + (north - south) * fraction
        for x in (west, east):
            lon, lat = transformer.transform(x, y)
            if math.isfinite(lon) and math.isfinite(lat):
                lons.append(_signed_longitude(lon))
                lats.append(lat)
    if not lons or not lats:
        raise AppError(
            "coordinate_transform_failed",
            "The stored area bbox could not be transformed to Earth WGS84.",
            status_code=422,
        )
    min_lon, max_lon = min(lons), max(lons)
    if max_lon - min_lon > 180.0:
        shifted = [value + 360.0 if value < 0 else value for value in lons]
        west_out = _signed_longitude(min(shifted))
        east_out = _signed_longitude(max(shifted))
        crosses = True
    else:
        west_out, east_out = min_lon, max_lon
        crosses = False
    south_out = max(-90.0, min(lats))
    north_out = min(90.0, max(lats))
    if south_out >= north_out:
        raise AppError(
            "invalid_bbox_latitude",
            "The stored area bbox does not span a positive latitude range.",
            status_code=422,
        )
    return EarthBBox(
        west=west_out,
        south=south_out,
        east=east_out,
        north=north_out,
        crosses_antimeridian=crosses,
        source="stored_typed_area",
    )


def _typed_area_bbox(area: Mapping[str, object], *, source: str = "stored_typed_area") -> EarthBBox:
    raw = area.get("bbox")
    if not isinstance(raw, Mapping):
        raise AppError("invalid_bbox", "Stored typed area has no bbox.", status_code=422)
    crs = str(area.get("crs") or EARTH_BBOX_CRS)
    if not _is_geographic_crs(crs):
        return _transform_bbox_to_wgs84(crs=crs, bbox=raw)
    return parse_earth_bbox(
        f"{float(raw['west'])},{float(raw['south'])},{float(raw['east'])},{float(raw['north'])}",
        source=source,
    )


def _center_from_bbox(bbox: EarthBBox) -> dict[str, float]:
    if bbox.crosses_antimeridian:
        lon = _signed_longitude((bbox.west + bbox.east + 360.0) / 2.0)
    else:
        lon = _signed_longitude((bbox.west + bbox.east) / 2.0)
    return {"lon": lon, "lat": (bbox.south + bbox.north) / 2.0}


def _candidate_feature_rows(candidate: object) -> list[dict[str, object]]:
    raw = _get(candidate, "features", {}) or {}
    rows: list[dict[str, object]] = []
    if isinstance(raw, Mapping):
        for feature_id, item in raw.items():
            rows.append(_feature_row(item, fallback_id=str(feature_id)))
    else:
        for item in raw:
            rows.append(_feature_row(item))
    return rows


def _feature_row(item: object, *, fallback_id: str | None = None) -> dict[str, object]:
    if isinstance(item, Mapping):
        return {
            "feature_id": str(item.get("feature_id") or fallback_id or ""),
            "method_version": item.get("method_version"),
            "unit": item.get("unit"),
            "quality": item.get("quality"),
        }
    return {
        "feature_id": str(_get(item, "feature_id") or fallback_id or ""),
        "method_version": _get(item, "method_version"),
        "unit": _get(item, "unit"),
        "quality": _get(item, "quality"),
    }


def _typed_rows(rows: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    return [dict(row) for row in rows if row is not None]


def _dimension_status(
    *,
    support: str,
    d01_rows: Sequence[Mapping[str, object]],
    typed_rows: Sequence[Mapping[str, object]],
) -> dict[str, dict[str, object]]:
    typed_by_dimension: dict[str, list[Mapping[str, object]]] = {}
    for row in typed_rows:
        dimension = row.get("dimension_code")
        if dimension in DIMENSION_CODES:
            typed_by_dimension.setdefault(str(dimension), []).append(row)

    statuses: dict[str, dict[str, object]] = {}
    for dimension in DIMENSION_CODES:
        if dimension == "D01":
            count = len(d01_rows)
            present = count > 0
            method_versions = sorted(
                {str(row["method_version"]) for row in d01_rows if row.get("method_version")}
            )
            statuses[dimension] = {
                "status": "present" if present else "absent",
                "record_count": count,
                "value_origin": "stored_observation" if present else None,
                "reason": None if present else "no_stored_observation",
                "support": support,
                "method_versions": method_versions,
            }
            continue
        dimension_rows = typed_by_dimension.get(dimension, [])
        present = bool(dimension_rows)
        statuses[dimension] = {
            "status": "present" if present else "absent",
            "record_count": len(dimension_rows),
            "value_origin": "stored_typed_observation" if present else None,
            "reason": None if present else "no_stored_observation",
            "support": SUPPORT_TYPED,
            "state_counts": dict(
                sorted(Counter(str(row.get("state")) for row in dimension_rows).items())
            ),
            "feature_ids": sorted({str(row.get("feature_id")) for row in dimension_rows}),
        }
    return statuses


def _availability_flags(
    statuses: Mapping[str, Mapping[str, object]], *, support: str
) -> dict[str, object]:
    available = [
        dimension for dimension in DIMENSION_CODES if statuses[dimension]["status"] == "present"
    ]
    return {
        "d01_present": "D01" in available,
        "d02_present": "D02" in available,
        "d03_present": "D03" in available,
        "d04_present": "D04" in available,
        "d05_present": "D05" in available,
        "d06_present": "D06" in available,
        "any_dimension_present": bool(available),
        "dimension_count": len(available),
        "support": support,
        "shortlist_only": support == SUPPORT_COARSE,
    }


def _feature_payload(
    *,
    feature_id: str,
    feature_type: str,
    display_name: str,
    data_level: str,
    bbox: EarthBBox,
    center: Mapping[str, float],
    crs: str,
    support: str,
    statuses: Mapping[str, Mapping[str, object]],
    data_version: str,
    detail_links: Mapping[str, str | None],
    **extra: object,
) -> dict[str, object]:
    available = [
        dimension for dimension in DIMENSION_CODES if statuses[dimension]["status"] == "present"
    ]
    missing = [dimension for dimension in DIMENSION_CODES if dimension not in available]
    payload: dict[str, object] = {
        "feature_id": feature_id,
        "feature_type": feature_type,
        "body": EARTH_BODY,
        "display_name": display_name,
        "data_level": data_level,
        "bbox": bbox.as_dict(),
        "center": {"lon": center["lon"], "lat": center["lat"]},
        "geometry": earth_point_geometry(center["lon"], center["lat"]),
        "bbox_geometry": earth_bbox_geometry(bbox),
        "crs": crs,
        "bbox_crs": EARTH_BBOX_CRS,
        "coordinate_frame": EARTH_COORDINATE_FRAME,
        "support": support,
        "support_label": support,
        "location_id": None,
        "candidate_id": None,
        "area_id": None,
        "dimension_status": dict(statuses),
        "available_dimensions": available,
        "missing_dimensions": missing,
        "availability": _availability_flags(statuses, support=support),
        "data_version": data_version,
        "detail_links": dict(detail_links),
    }
    payload.update(extra)
    return payload


def _fine_feature(
    *,
    candidate: object,
    typed_rows: Sequence[Mapping[str, object]],
    has_typed_area: bool,
    data_version: str,
) -> dict[str, object]:
    location_id = str(_get(candidate, "location_id"))
    origin = str(_get(candidate, "origin", "indexed") or "indexed")
    is_on_demand = origin == "on_demand"
    bbox = _candidate_bbox(candidate, source="stored_candidate")
    lat = _get(candidate, "lat")
    lon = _get(candidate, "lon")
    center = (
        {"lon": normalize_earth_longitude(float(lon)), "lat": float(lat)}
        if lat is not None and lon is not None
        else _center_from_bbox(bbox)
    )
    d01_rows = _candidate_feature_rows(candidate)
    statuses = _dimension_status(
        support=SUPPORT_FINE,
        d01_rows=d01_rows,
        typed_rows=typed_rows,
    )
    detail_links: dict[str, str | None] = {
        "location": f"/api/v1/locations/{location_id}",
        "typed_area": f"/api/v1/typed/areas/{location_id}" if has_typed_area else None,
    }
    if is_on_demand:
        detail_links["area_features"] = f"/api/v1/areas/{location_id}/features"
    return _feature_payload(
        feature_id=("on_demand:" if is_on_demand else "fine:") + location_id,
        feature_type="on_demand_aoi" if is_on_demand else "fine_candidate",
        display_name=str(_get(candidate, "display_name") or location_id),
        data_level="fine",
        bbox=bbox,
        center=center,
        crs=EARTH_BBOX_CRS,
        support=SUPPORT_FINE,
        statuses=statuses,
        data_version=data_version,
        detail_links=detail_links,
        location_id=location_id,
        candidate_id=location_id,
        area_id=location_id if is_on_demand else None,
        typed_area_id=location_id if has_typed_area else None,
        analysis_crs=_get(candidate, "crs"),
        dataset_id=_get(candidate, "dataset_id"),
        window_size_m=_get(candidate, "window_size_m"),
        analysis_resolution_m=_get(candidate, "analysis_resolution_m"),
        valid_fraction=_get(candidate, "valid_fraction"),
        quality_flags=list(_get(candidate, "quality_flags", []) or []),
        d01_feature_count=len(d01_rows),
        typed_observation_count=len(typed_rows),
        origin=origin,
        coordinates="earth_wgs84_geodetic",
    )


def _coarse_feature(
    *,
    row: Mapping[str, object],
    typed_rows: Sequence[Mapping[str, object]],
    has_typed_area: bool,
    data_version: str,
    snapshot_id: str | None,
    method_version: str | None,
) -> dict[str, object]:
    candidate_id = str(row.get("candidate_id"))
    bbox = parse_earth_bbox(
        f"{float(row['west'])},{float(row['south'])},{float(row['east'])},{float(row['north'])}",
        source="coarse_row_bbox_wgs84",
    )
    center = _center_from_bbox(bbox)
    row_method = str(row.get("method_version") or method_version or "")
    d01_rows = (
        [{"feature_id": "coarse_slope_mean_deg", "method_version": row_method}]
        if row_method
        else []
    )
    statuses = _dimension_status(
        support=SUPPORT_COARSE,
        d01_rows=d01_rows,
        typed_rows=typed_rows,
    )
    return _feature_payload(
        feature_id=f"coarse:{candidate_id}",
        feature_type="coarse_candidate",
        display_name=str(row.get("display_name") or candidate_id),
        data_level="coarse",
        bbox=bbox,
        center=center,
        crs=EARTH_BBOX_CRS,
        support=SUPPORT_COARSE,
        statuses=statuses,
        data_version=data_version,
        detail_links={
            "global_candidate": f"/api/v1/global/candidates/{candidate_id}",
            "typed_area": f"/api/v1/typed/areas/{candidate_id}" if has_typed_area else None,
        },
        candidate_id=candidate_id,
        region_id=row.get("region_id"),
        coarse_snapshot_id=snapshot_id,
        method_version=row_method or None,
        analysis_crs=row.get("crs"),
        dataset_id=row.get("dataset_id"),
        source_resolution_m=row.get("source_resolution_m"),
        window_size_m=row.get("window_size_m"),
        slope_mean_deg=row.get("slope_mean_deg"),
        slope_p90_deg=row.get("slope_p90_deg"),
        flat_fraction=row.get("flat_fraction"),
        valid_fraction=row.get("valid_fraction"),
        qa_flags=list(row.get("qa_flags") or []),
        shortlist_not_fine=True,
        typed_observation_count=len(typed_rows),
        coordinates="earth_wgs84_geodetic_bbox",
    )


def _typed_feature(
    *,
    area: Mapping[str, object],
    typed_rows: Sequence[Mapping[str, object]],
    linked_candidate: object | None,
    data_version: str,
) -> dict[str, object]:
    area_id = str(area["area_id"])
    bbox = _typed_area_bbox(area)
    center = _center_from_bbox(bbox)
    d01_rows = _candidate_feature_rows(linked_candidate) if linked_candidate is not None else []
    statuses = _dimension_status(
        support=SUPPORT_TYPED,
        d01_rows=d01_rows,
        typed_rows=typed_rows,
    )
    return _feature_payload(
        feature_id=f"typed:{area_id}",
        feature_type="typed_area",
        display_name=str(area.get("display_name") or area_id),
        data_level="typed",
        bbox=bbox,
        center=center,
        crs=EARTH_BBOX_CRS,
        support=SUPPORT_TYPED,
        statuses=statuses,
        data_version=data_version,
        detail_links={
            "typed_area": f"/api/v1/typed/areas/{area_id}",
            "location": (f"/api/v1/locations/{area_id}" if linked_candidate is not None else None),
        },
        area_id=area_id,
        typed_area_id=area_id,
        linked_candidate_id=area_id if linked_candidate is not None else None,
        relationship=(
            "same_area_id_exact_join" if linked_candidate is not None else "typed_area_only"
        ),
        stored_crs=area.get("crs"),
        coordinate_frame=area.get("coordinate_frame"),
        area_km2=area.get("area_km2"),
        typed_observation_count=len(typed_rows),
        d01_feature_count=len(d01_rows),
        coordinates="earth_wgs84_geodetic",
    )


@dataclass(frozen=True, slots=True)
class EarthMapFeaturesResult:
    features: list[dict[str, object]]
    total: int
    counts_by_type: dict[str, int]
    next_offset: int | None
    coverage: dict[str, object]
    unlocatable_typed_area_count: int


def _feature_passes_filters(
    feature: Mapping[str, object],
    *,
    dimension: str | None,
    availability: str,
) -> bool:
    dimensions = set(feature["available_dimensions"])
    if dimension is not None:
        present = dimension in dimensions
        if availability == "absent":
            return not present
        return present
    present = bool(dimensions)
    if availability == "present":
        return present
    if availability == "absent":
        return not present
    return True


def _included_by_level(feature_type: str, data_level: str) -> bool:
    if data_level == "all":
        return True
    if data_level == "fine":
        return feature_type in ("fine_candidate", "on_demand_aoi")
    if data_level == "coarse":
        return feature_type == "coarse_candidate"
    return False


def build_map_features(
    *,
    query_bbox: EarthBBox,
    fine_candidates: Sequence[object] = (),
    typed_areas: Sequence[Mapping[str, object]] = (),
    typed_observations: Sequence[Mapping[str, object]] = (),
    coarse_rows: Sequence[Mapping[str, object]] = (),
    coarse_coverage: Mapping[str, object] | None = None,
    coarse_total: int | None = None,
    coarse_row_offset: int = 0,
    feature_type: str = "all",
    data_level: str = "all",
    dimension: str | None = None,
    availability: str = "any",
    limit: int = 50,
    offset: int = 0,
    data_version: str = "",
) -> EarthMapFeaturesResult:
    """Filter and paginate Earth map features from stored rows.

    The caller has already loaded candidates/areas that intersect the query
    viewport.  This function applies the exact in-memory geometry test as well,
    so route-level SQL pushdown can stay conservative without changing results.
    """
    if feature_type not in FEATURE_TYPES:
        raise AppError(
            "invalid_feature_type",
            "feature_type must be one of " + ", ".join(FEATURE_TYPES) + ".",
            status_code=422,
        )
    if data_level not in DATA_LEVELS:
        raise AppError(
            "invalid_data_level",
            "data_level must be one of " + ", ".join(DATA_LEVELS) + ".",
            status_code=422,
        )
    if availability not in AVAILABILITY_VALUES:
        raise AppError(
            "invalid_availability",
            "availability must be one of " + ", ".join(AVAILABILITY_VALUES) + ".",
            status_code=422,
        )
    if dimension is not None and dimension not in DIMENSION_CODES:
        raise AppError(
            "invalid_dimension",
            "dimension must be one of " + ", ".join(DIMENSION_CODES) + ".",
            status_code=422,
        )
    if limit < 1 or offset < 0:
        raise AppError(
            "invalid_pagination",
            "limit must be >= 1 and offset must be >= 0.",
            status_code=422,
        )

    candidate_list = [candidate for candidate in fine_candidates if candidate is not None]
    candidates_by_id = {
        str(_get(candidate, "location_id")): candidate for candidate in candidate_list
    }
    rows_by_area: dict[str, list[dict[str, object]]] = {}
    for row in _typed_rows(typed_observations):
        if str(row.get("body")) != EARTH_BODY:
            continue
        rows_by_area.setdefault(str(row.get("area_id")), []).append(row)
    areas_by_id = {
        str(area["area_id"]): area for area in typed_areas if str(area.get("body")) == EARTH_BODY
    }

    fine_candidate_count = 0
    on_demand_count = 0
    with_d01_fine = 0
    with_d01_on_demand = 0
    coarse_rows_used = [row for row in coarse_rows if row.get("candidate_id")]
    coarse_snapshot_id = None
    coarse_method_version = None
    if coarse_coverage:
        coarse_snapshot_id = (
            str(coarse_coverage["snapshot_id"]) if coarse_coverage.get("snapshot_id") else None
        )
        coarse_method_version = (
            str(coarse_coverage["method_version"])
            if coarse_coverage.get("method_version")
            else None
        )

    non_coarse_features: list[dict[str, object]] = []
    coarse_features: list[dict[str, object]] = []
    unlocatable_typed = 0

    for candidate in candidate_list:
        location_id = str(_get(candidate, "location_id"))
        origin = str(_get(candidate, "origin", "indexed") or "indexed")
        try:
            bbox = _candidate_bbox(candidate, source="stored_candidate")
        except (AppError, KeyError, TypeError, ValueError):
            continue
        if not bbox_intersects(query_bbox, bbox):
            continue
        typed_for_area = rows_by_area.get(location_id, [])
        if origin == "on_demand":
            on_demand_count += 1
            if _candidate_feature_rows(candidate):
                with_d01_on_demand += 1
        else:
            fine_candidate_count += 1
            if _candidate_feature_rows(candidate):
                with_d01_fine += 1
        candidate_type = "on_demand_aoi" if origin == "on_demand" else "fine_candidate"
        if feature_type not in ("all", candidate_type):
            continue
        if not _included_by_level(candidate_type, data_level):
            continue
        non_coarse_features.append(
            _fine_feature(
                candidate=candidate,
                typed_rows=typed_for_area,
                has_typed_area=location_id in areas_by_id,
                data_version=data_version,
            )
        )

    typed_area_count = 0
    typed_with_observations = 0
    typed_by_dimension: Counter[str] = Counter()
    for area_id, area in areas_by_id.items():
        try:
            bbox = _typed_area_bbox(area)
        except (AppError, KeyError, TypeError, ValueError):
            unlocatable_typed += 1
            continue
        if not bbox_intersects(query_bbox, bbox):
            continue
        typed_area_count += 1
        area_rows = rows_by_area.get(area_id, [])
        if area_rows:
            typed_with_observations += 1
            for row in area_rows:
                if row.get("dimension_code") in DIMENSION_CODES:
                    typed_by_dimension[str(row["dimension_code"])] += 1
        if feature_type not in ("all", "typed_area"):
            continue
        if not _included_by_level("typed_area", data_level):
            continue
        non_coarse_features.append(
            _typed_feature(
                area=area,
                typed_rows=area_rows,
                linked_candidate=candidates_by_id.get(area_id),
                data_version=data_version,
            )
        )

    if feature_type in ("all", "coarse_candidate") and _included_by_level(
        "coarse_candidate", data_level
    ):
        for row in coarse_rows_used:
            candidate_id = str(row.get("candidate_id"))
            coarse_features.append(
                _coarse_feature(
                    row=row,
                    typed_rows=rows_by_area.get(candidate_id, []),
                    has_typed_area=candidate_id in areas_by_id,
                    data_version=data_version,
                    snapshot_id=coarse_snapshot_id,
                    method_version=coarse_method_version,
                )
            )

    non_coarse_features = [
        feature
        for feature in non_coarse_features
        if _feature_passes_filters(
            feature,
            dimension=dimension,
            availability=availability,
        )
    ]
    coarse_features = [
        feature
        for feature in coarse_features
        if _feature_passes_filters(
            feature,
            dimension=dimension,
            availability=availability,
        )
    ]
    # Match the coarse index's bbox ordering so an offset window is stable and
    # page boundaries cannot repeat or skip rows.
    coarse_features.sort(
        key=lambda item: (
            str(item.get("region_id") or ""),
            float(item["bbox"]["west"]),
            float(item["bbox"]["south"]),
            str(item.get("candidate_id") or ""),
        )
    )
    type_order = {
        "fine_candidate": 0,
        "on_demand_aoi": 1,
        "typed_area": 2,
        "coarse_candidate": 3,
    }
    non_coarse_features.sort(
        key=lambda item: (
            type_order[str(item["feature_type"])],
            str(item["feature_id"]),
        )
    )

    # Coarse features only ever support D01 (their method version).  A D02-D06
    # query therefore matches coarse rows only when the caller asks for absent
    # dimensions, which is the explicit inverse of "present".
    coarse_has_dimension = dimension is None or dimension == "D01"
    coarse_dimension_matches = (
        not coarse_has_dimension if availability == "absent" else coarse_has_dimension
    )
    coarse_selected = (
        feature_type in ("all", "coarse_candidate")
        and _included_by_level("coarse_candidate", data_level)
        and coarse_dimension_matches
    )
    if coarse_selected:
        coarse_matched_total = (
            int(coarse_total) if coarse_total is not None else len(coarse_features)
        )
    else:
        coarse_matched_total = 0

    counts_by_type = {name: 0 for name in FEATURE_TYPES if name != "all"}
    for feature in non_coarse_features:
        counts_by_type[str(feature["feature_type"])] += 1
    counts_by_type["coarse_candidate"] = coarse_matched_total
    total = sum(counts_by_type.values())

    # Fine/on-demand/typed features are a small in-memory list; coarse shortlist
    # rows follow as a database-paged rank window.  ``coarse_row_offset`` is the
    # global coarse rank of the first supplied coarse row.
    page_start = offset
    page_end = offset + limit
    window: list[dict[str, object]] = []
    non_coarse_count = len(non_coarse_features)
    local_start = max(0, page_start)
    local_end = min(page_end, non_coarse_count)
    if local_end > local_start:
        window.extend(non_coarse_features[local_start:local_end])
    if coarse_matched_total:
        coarse_global_start = non_coarse_count
        for index, feature in enumerate(coarse_features):
            rank = coarse_global_start + coarse_row_offset + index
            if page_start <= rank < page_end:
                window.append(feature)
    next_offset = offset + len(window) if offset + len(window) < total else None

    coverage_coarse: dict[str, object] = dict(coarse_coverage or {"status": "not_queried"})
    coverage_coarse.setdefault("status", "not_queried")
    coverage_coarse["matched_count"] = coarse_matched_total
    coverage_coarse["returned_to_assembly"] = len(coarse_rows_used)
    coverage_coarse["query_truncated"] = bool(coarse_matched_total > len(coarse_rows_used))
    coverage_coarse["truncated"] = coverage_coarse["query_truncated"]
    coverage_coarse.setdefault("snapshot", coverage_coarse.get("snapshot_id"))
    coverage_coarse.setdefault("method", coverage_coarse.get("method_version"))
    coverage = {
        "fine": {
            "candidate_count": fine_candidate_count,
            "with_d01_count": with_d01_fine,
            "matched_count": counts_by_type["fine_candidate"],
            "label": "fine_90m_stored_subset",
        },
        "on_demand": {
            "candidate_count": on_demand_count,
            "with_d01_count": with_d01_on_demand,
            "matched_count": counts_by_type["on_demand_aoi"],
            "label": "on_demand_90m_stored_subset",
        },
        "typed": {
            "area_count": typed_area_count,
            "with_observations_count": typed_with_observations,
            "matched_count": counts_by_type["typed_area"],
            "by_dimension": dict(sorted(typed_by_dimension.items())),
            "unlocatable_count": unlocatable_typed,
            "label": "stored_typed_subset",
        },
        "coarse": coverage_coarse,
    }
    return EarthMapFeaturesResult(
        features=window,
        total=total,
        counts_by_type=counts_by_type,
        next_offset=next_offset,
        coverage=coverage,
        unlocatable_typed_area_count=unlocatable_typed,
    )


__all__ = [
    "AVAILABILITY_VALUES",
    "DATA_LEVELS",
    "DIMENSION_CODES",
    "EARTH_BBOX_CRS",
    "EARTH_BODY",
    "EARTH_COORDINATE_CONTRACT_VERSION",
    "EARTH_COORDINATE_FRAME",
    "EARTH_COORDINATE_SYSTEM",
    "EARTH_MAP_FEATURES_VERSION",
    "FEATURE_TYPES",
    "SUPPORT_COARSE",
    "SUPPORT_FINE",
    "SUPPORT_TYPED",
    "EarthBBox",
    "EarthMapFeaturesResult",
    "bbox_contains",
    "bbox_intersects",
    "build_map_features",
    "earth_bbox_geometry",
    "earth_point_geometry",
    "global_earth_bbox",
    "normalize_earth_longitude",
    "parse_earth_bbox",
]
