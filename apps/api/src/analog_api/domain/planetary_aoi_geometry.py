"""Body-aware AOI geometry for the on-demand planetary engine (Issue #170, Goal D).

This module is the storage-independent geometry layer between an API request and any
provider that reads an AOI. It answers one question: *what exactly was requested, in
which body frame, and is it a legal, bounded AOI?*

Coordinate contract
-------------------
* Earth uses WGS84 / ``EPSG:4326`` lon/lat in degrees with **geodetic** latitude.
* Moon and Mars use their IAU body-fixed frames with **planetocentric** latitude,
  positive-east longitude. They are never interpreted as Earth WGS84, and a
  planetographic latitude is never silently substituted.
* Longitude may be supplied as signed ``[-180, 180]`` or as ``[0, 360]``. Exact
  ``-180``/``180`` bbox edges are preserved so ``-180,180`` means the full
  longitude range rather than a zero-width box.
* ``west > east`` after normalisation means the bbox crosses the antimeridian; both
  pieces are kept.

Full-requested-AOI rule
-----------------------
The requested bbox is analysed in full. There is no centre-window substitution and
no silent clipping: :attr:`AoiGeometry.requested_bbox` keeps the submitted values,
:attr:`AoiGeometry.normalized_bbox` is the signed equivalent, and
:attr:`AoiGeometry.shrink_applied` is always ``False``. An AOI that cannot be
represented or is too large raises an explicit :class:`AppError` instead of
returning a smaller analysis extent.

Area, sides and pixels
----------------------
* ``area_km2`` is the geodesic area of the bbox polygons. Longitude chunks of at
  most 180 degrees are summed, so full-longitude bands, antimeridian pieces and
  poles are handled without a centre projection.
* The metric envelope is the dense boundary of the requested bbox projected into a
  local azimuthal-equidistant CRS centred on the AOI. For a planetary AOI the metric
  sphere is the body mean radius consistent with planetocentric latitude; Earth uses
  the WGS84 ellipsoid. If an AOI is so large that one local metric frame cannot
  represent it, the engine raises ``coordinate_transform_failed`` and asks the caller
  to split the request; it never returns a shrunken envelope.
* Limits are explicit and body-specific. Callers may pass :class:`AoiLimits` to
  change the safety caps deliberately. Defaults mirror the shared Goal D engine
  quota (Earth 10 km-class, Moon/Mars 25 km-class, 180-degree span, 4M pixels).

Poles are ordinary latitude bounds. A polar cap may span all longitudes; the engine
reports ``is_full_longitude``/``includes_*_pole`` and does not clamp a pole to a
rectangle. At the exact pole all longitudes coincide, so :meth:`AoiGeometry.contains`
treats a pole point inside the latitude range as contained regardless of longitude.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from pyproj import CRS, Geod, Transformer

from analog_api.domain.errors import AppError
from analog_api.domain.features import ANALYSIS_RESOLUTION_M as EARTH_ANALYSIS_RESOLUTION_M
from analog_api.domain.planetary import get_body

AOI_ENGINE_GEOMETRY_VERSION = "aoi-engine-geometry-1.0.0"
SUPPORTED_AOI_BODIES: tuple[str, ...] = ("Earth", "Moon", "Mars")
EARTH_BODY = "Earth"
EARTH_BBOX_CRS = "EPSG:4326"
PLANETARY_ANALYSIS_RESOLUTION_M = 250.0

_BBOX_KEYS = ("west", "south", "east", "north")
_GEOMETRIC_EPSILON = 1e-9
_METRIC_EPSILON_M = 1e-6
_MAX_DENSIFY_STEP_DEG = 0.25
_METRIC_PROJECTION = "azimuthal_equidistant"
_METRIC_SEMANTICS = "dense_boundary_envelope_in_local_azimuthal_equidistant_frame"
_EARTH_LATITUDE_KINDS = {"geodetic", "wgs84", "epsg:4326", "geographic"}
_PLANETARY_LATITUDE_KINDS = {"planetocentric"}
_FORBIDDEN_LATITUDE_KINDS = {"planetographic", "geocentric"}


@dataclass(frozen=True, slots=True)
class AoiLimits:
    """Explicit safety caps for one AOI request.

    ``min_side_m``/``max_side_m`` apply to the projected metric envelope sides;
    ``max_area_km2`` applies to the geodesic area; ``max_span_deg`` caps the
    longitude span (360 allows full-longitude polar bands); ``max_pixels`` is the
    product of the required pixel columns and rows at the requested resolution.
    """

    min_side_m: float
    max_side_m: float
    max_area_km2: float
    max_span_deg: float
    max_pixels: int

    def to_payload(self) -> dict[str, object]:
        return {
            "min_side_m": self.min_side_m,
            "max_side_m": self.max_side_m,
            "max_area_km2": self.max_area_km2,
            "max_span_deg": self.max_span_deg,
            "max_pixels": self.max_pixels,
        }


# These mirror the shared `planetary_aoi_engine` quota defaults so the geometry layer
# and the orchestrator cannot silently disagree about what "bounded" means.
BODY_DEFAULT_LIMITS: dict[str, AoiLimits] = {
    EARTH_BODY: AoiLimits(
        min_side_m=90.0,
        max_side_m=10_000.0,
        max_area_km2=25.0,
        max_span_deg=180.0,
        max_pixels=4_000_000,
    ),
    "Moon": AoiLimits(
        min_side_m=250.0,
        max_side_m=25_000.0,
        max_area_km2=400.0,
        max_span_deg=180.0,
        max_pixels=4_000_000,
    ),
    "Mars": AoiLimits(
        min_side_m=250.0,
        max_side_m=25_000.0,
        max_area_km2=400.0,
        max_span_deg=180.0,
        max_pixels=4_000_000,
    ),
}

BODY_DEFAULT_PIXEL_SIZE_M: dict[str, float] = {
    EARTH_BODY: float(EARTH_ANALYSIS_RESOLUTION_M),
    "Moon": PLANETARY_ANALYSIS_RESOLUTION_M,
    "Mars": PLANETARY_ANALYSIS_RESOLUTION_M,
}


@dataclass(frozen=True, slots=True)
class BodyGeometry:
    """The declared body frame plus the shape used for metric computations."""

    body: str
    body_kind: str
    coordinate_frame: str
    latitude_kind: str
    longitude_direction: str
    bbox_crs: str
    earth_wgs84: bool
    equatorial_radius_m: float
    polar_radius_m: float
    metric_shape: str
    metric_radius_m: float | None
    geographic_epsg_urn: str
    geographic_epsg_is_epsg_registry: bool
    notes: str

    @property
    def flattening(self) -> float:
        return (self.equatorial_radius_m - self.polar_radius_m) / self.equatorial_radius_m

    @property
    def metric_shape_note(self) -> str:
        if self.metric_shape == "ellipsoid":
            return "Local metric envelope uses the WGS84 ellipsoid with geodetic latitude."
        return (
            "Local metric envelope uses the body mean-radius sphere, consistent with "
            "planetocentric latitude. The true equatorial/polar radii are reported "
            "separately and are not silently substituted as geodetic latitude."
        )

    def to_payload(self) -> dict[str, object]:
        return {
            "body": self.body,
            "body_kind": self.body_kind,
            "coordinate_frame": self.coordinate_frame,
            "latitude_kind": self.latitude_kind,
            "longitude_direction": self.longitude_direction,
            "bbox_crs": self.bbox_crs,
            "earth_wgs84": self.earth_wgs84,
            "equatorial_radius_m": self.equatorial_radius_m,
            "polar_radius_m": self.polar_radius_m,
            "flattening": self.flattening,
            "metric_shape": self.metric_shape,
            "metric_radius_m": self.metric_radius_m,
            "metric_shape_note": self.metric_shape_note,
            "geographic_epsg_urn": self.geographic_epsg_urn,
            "geographic_epsg_is_epsg_registry": self.geographic_epsg_is_epsg_registry,
            "notes": self.notes,
        }


_EARTH_BODY_GEOMETRY = BodyGeometry(
    body=EARTH_BODY,
    body_kind="earth",
    coordinate_frame="earth_wgs84",
    latitude_kind="geodetic",
    longitude_direction="positive_east",
    bbox_crs=EARTH_BBOX_CRS,
    earth_wgs84=True,
    equatorial_radius_m=6_378_137.0,
    polar_radius_m=6_356_752.314245,
    metric_shape="ellipsoid",
    metric_radius_m=None,
    geographic_epsg_urn="urn:ogc:def:crs:EPSG::4326",
    geographic_epsg_is_epsg_registry=True,
    notes=(
        "Earth WGS84 geodetic lon/lat. This is the only body in the engine that uses "
        "EPSG:4326; Moon/Mars coordinates are never Earth coordinates."
    ),
)


@lru_cache(maxsize=8)
def body_geometry(body: str) -> BodyGeometry:
    """Return the declared frame for ``Earth``, ``Moon`` or ``Mars``."""
    if body == EARTH_BODY:
        return _EARTH_BODY_GEOMETRY
    if body not in ("Moon", "Mars"):
        raise AppError(
            "unsupported_body",
            "AOI geometry supports body Earth, Moon or Mars.",
            status_code=422,
        )
    info = get_body(body)
    # Planetocentric latitude is an angle on the reference sphere. Keeping metric
    # computation on that same sphere avoids silently treating the input as geodetic
    # latitude on the Mars ellipsoid; the true ellipsoid parameters are still exposed.
    return BodyGeometry(
        body=info.body,
        body_kind="planetary",
        coordinate_frame=info.frame,
        latitude_kind=info.latitude_kind,
        longitude_direction=info.longitude_direction,
        bbox_crs=f"+proj=longlat +R={info.radius_m:.0f} +no_defs",
        earth_wgs84=False,
        equatorial_radius_m=info.radius_m,
        polar_radius_m=info.polar_radius_m,
        metric_shape="sphere",
        metric_radius_m=info.radius_m,
        geographic_epsg_urn=f"urn:ogc:def:crs:EPSG::{info.geographic_epsg}",
        geographic_epsg_is_epsg_registry=False,
        notes=info.notes,
    )


@dataclass(frozen=True, slots=True)
class MetricEnvelope:
    """Dense boundary envelope of the requested bbox in a local metric CRS."""

    crs: str
    projection: str
    projection_center_lon: float
    projection_center_lat: float
    x_min_m: float
    y_min_m: float
    x_max_m: float
    y_max_m: float
    width_m: float
    height_m: float
    point_count: int
    semantics: str

    def to_payload(self) -> dict[str, object]:
        return {
            "crs": self.crs,
            "projection": self.projection,
            "projection_center": {
                "lon": self.projection_center_lon,
                "lat": self.projection_center_lat,
            },
            "x_min_m": self.x_min_m,
            "y_min_m": self.y_min_m,
            "x_max_m": self.x_max_m,
            "y_max_m": self.y_max_m,
            "width_m": self.width_m,
            "height_m": self.height_m,
            "point_count": self.point_count,
            "semantics": self.semantics,
        }


@dataclass(frozen=True, slots=True)
class AoiGeometry:
    """A validated AOI that keeps the full requested extent."""

    body: str
    body_info: BodyGeometry
    requested_bbox: dict[str, float]
    normalized_bbox: dict[str, float]
    bbox_0_360: dict[str, float]
    bbox_pieces: tuple[dict[str, float], ...]
    crosses_antimeridian: bool
    is_full_longitude: bool
    longitude_span_deg: float
    longitude_convention: str
    center_lon: float
    center_lat: float
    area_km2: float
    envelope_area_km2: float
    metric_envelope: MetricEnvelope
    pixel_size_m: float
    pixels_x: int
    pixels_y: int
    total_pixels: int
    limits: AoiLimits
    geometry: dict[str, Any]
    warnings: tuple[str, ...]
    includes_north_pole: bool
    includes_south_pole: bool
    analysis_extent: str = "full_requested_aoi"
    shrink_applied: bool = False

    @property
    def bbox(self) -> dict[str, float]:
        """Signed normalised bbox, convenient for existing provider adapters."""
        return dict(self.normalized_bbox)

    @property
    def crs(self) -> str:
        return self.body_info.bbox_crs

    @property
    def coordinate_frame(self) -> str:
        return self.body_info.coordinate_frame

    @property
    def latitude_kind(self) -> str:
        return self.body_info.latitude_kind

    @property
    def width_m(self) -> float:
        return self.metric_envelope.width_m

    @property
    def height_m(self) -> float:
        return self.metric_envelope.height_m

    @property
    def analysis_bbox(self) -> dict[str, float]:
        """The analysed extent; equal to the full requested AOI by construction."""
        return dict(self.normalized_bbox)

    def contains(self, lon: float, lat: float) -> bool:
        """Return whether a lon/lat point is inside the full requested AOI.

        At the exact pole all longitudes coincide, so a pole point inside the
        latitude range is contained regardless of the supplied longitude.
        """
        if not (math.isfinite(lon) and math.isfinite(lat)):
            raise AppError("invalid_coordinate", "lat/lon must be finite.", status_code=422)
        if not -90.0 <= lat <= 90.0:
            raise AppError("invalid_latitude", "lat must be within [-90, 90].", status_code=422)
        bbox = self.normalized_bbox
        if not bbox["south"] <= lat <= bbox["north"]:
            return False
        if lat in (90.0, -90.0):
            return True
        if self.is_full_longitude:
            return True
        value = _normalize_longitude(lon)
        if self.crosses_antimeridian:
            return value >= bbox["west"] or value <= bbox["east"]
        return bbox["west"] <= value <= bbox["east"]

    def as_payload(self) -> dict[str, Any]:
        notes = [
            "The requested bbox is analysed in full; no centre-window substitution is applied.",
            "West>east after normalisation crosses the antimeridian; both pieces are included.",
            (
                "Moon/Mars coordinates are planetocentric body-frame degrees, not Earth "
                "WGS84/EPSG:4326."
                if not self.body_info.earth_wgs84
                else "Earth coordinates are WGS84 geodetic degrees (EPSG:4326)."
            ),
        ]
        return {
            "contract_version": AOI_ENGINE_GEOMETRY_VERSION,
            "body": self.body,
            "body_info": self.body_info.to_payload(),
            "requested_bbox": dict(self.requested_bbox),
            "normalized_bbox": dict(self.normalized_bbox),
            "analysis_bbox": dict(self.normalized_bbox),
            "bbox_0_360": dict(self.bbox_0_360),
            "bbox_pieces": [dict(piece) for piece in self.bbox_pieces],
            "crosses_antimeridian": self.crosses_antimeridian,
            "is_full_longitude": self.is_full_longitude,
            "longitude_span_deg": self.longitude_span_deg,
            "longitude_convention": self.longitude_convention,
            "center": {"lon": self.center_lon, "lat": self.center_lat},
            "geometry": self.geometry,
            "area_km2": self.area_km2,
            "envelope_area_km2": self.envelope_area_km2,
            "width_m": self.width_m,
            "height_m": self.height_m,
            "metric_envelope": self.metric_envelope.to_payload(),
            "pixel_size_m": self.pixel_size_m,
            "pixels_x": self.pixels_x,
            "pixels_y": self.pixels_y,
            "total_pixels": self.total_pixels,
            "limits": self.limits.to_payload(),
            "includes_north_pole": self.includes_north_pole,
            "includes_south_pole": self.includes_south_pole,
            "analysis_extent": self.analysis_extent,
            "shrink_applied": self.shrink_applied,
            "warnings": list(self.warnings),
            "notes": notes,
        }


# ---------------------------------------------------------------------------
# Normalisation and parsing
# ---------------------------------------------------------------------------


def _normalize_longitude(value: float) -> float:
    """Normalise a point longitude to ``[-180, 180)``, preserving the dateline edge.

    Exact ``-180``/``180`` is preserved for bbox edges. Point containment uses the
    half-open convention, where ``180`` is equivalent to ``-180``. The result is
    rounded to 12 decimal places so identity requests keep their exact decimal values
    instead of acquiring binary floating-point drift.
    """
    if value in (-180.0, 180.0):
        return value
    return round((value + 180.0) % 360.0 - 180.0, 12)


def _longitude_from_360(value: float) -> float:
    if value in (0.0, 360.0):
        return 0.0
    if value == 180.0:
        return 180.0
    return round(value - 360.0, 12) if value > 180.0 else value


def _bbox_values(bbox: Mapping[str, float] | Sequence[float] | Any) -> dict[str, float]:
    if isinstance(bbox, Mapping):
        try:
            raw = {key: bbox[key] for key in _BBOX_KEYS}
        except (KeyError, TypeError) as exc:
            raise AppError(
                "invalid_bbox", "bbox must contain west, south, east and north.", status_code=422
            ) from exc
    elif isinstance(bbox, (str, bytes)):
        raise AppError(
            "invalid_bbox",
            "bbox must be a mapping or [west, south, east, north]; parse comma-separated "
            "strings before calling the geometry engine.",
            status_code=422,
        )
    elif isinstance(bbox, Sequence):
        if len(bbox) != 4:
            raise AppError(
                "invalid_bbox",
                "bbox sequence must contain exactly four values: west, south, east, north.",
                status_code=422,
            )
        raw = dict(zip(_BBOX_KEYS, bbox, strict=True))
    elif all(hasattr(bbox, key) for key in _BBOX_KEYS):
        raw = {key: getattr(bbox, key) for key in _BBOX_KEYS}
    else:
        raise AppError(
            "invalid_bbox",
            "bbox must be a mapping, a four-value sequence or an object with "
            "west/south/east/north attributes.",
            status_code=422,
        )
    try:
        values = {key: float(raw[key]) for key in _BBOX_KEYS}
    except (TypeError, ValueError) as exc:
        raise AppError("invalid_bbox", "bbox values must be numbers.", status_code=422) from exc
    if not all(math.isfinite(value) for value in values.values()):
        raise AppError("invalid_bbox", "bbox values must be finite.", status_code=422)
    return values


def _checked_limits(body: str, limits: AoiLimits | None) -> AoiLimits:
    resolved = limits if limits is not None else BODY_DEFAULT_LIMITS[body]
    try:
        min_side = float(resolved.min_side_m)
        max_side = float(resolved.max_side_m)
        max_area = float(resolved.max_area_km2)
        max_span = float(resolved.max_span_deg)
        max_pixels = float(resolved.max_pixels)
    except (TypeError, ValueError) as exc:
        raise AppError(
            "invalid_aoi_limits", "AOI limits must be numeric.", status_code=422
        ) from exc
    values = (min_side, max_side, max_area, max_span, max_pixels)
    if not all(math.isfinite(value) for value in values):
        raise AppError("invalid_aoi_limits", "AOI limits must be finite.", status_code=422)
    if min_side < 0:
        raise AppError("invalid_aoi_limits", "min_side_m must be >= 0.", status_code=422)
    if max_side <= min_side:
        raise AppError(
            "invalid_aoi_limits",
            "max_side_m must be greater than min_side_m.",
            status_code=422,
        )
    if max_area <= 0:
        raise AppError("invalid_aoi_limits", "max_area_km2 must be positive.", status_code=422)
    if not 0 < max_span <= 360.0:
        raise AppError(
            "invalid_aoi_limits",
            "max_span_deg must be within (0, 360].",
            status_code=422,
        )
    if max_pixels != int(max_pixels) or max_pixels < 1:
        raise AppError(
            "invalid_aoi_limits", "max_pixels must be a positive integer.", status_code=422
        )
    return AoiLimits(
        min_side_m=min_side,
        max_side_m=max_side,
        max_area_km2=max_area,
        max_span_deg=max_span,
        max_pixels=int(max_pixels),
    )


def _checked_pixel_size(body: str, pixel_size_m: float | None) -> float:
    try:
        value = BODY_DEFAULT_PIXEL_SIZE_M[body] if pixel_size_m is None else float(pixel_size_m)
    except (TypeError, ValueError) as exc:
        raise AppError(
            "invalid_analysis_resolution",
            "pixel_size_m must be a positive finite number of metres.",
            status_code=422,
        ) from exc
    if not math.isfinite(value) or value <= 0:
        raise AppError(
            "invalid_analysis_resolution",
            "pixel_size_m must be a positive finite number of metres.",
            status_code=422,
        )
    return value


def _latitude_kind(body: str, latitude_kind: str | None) -> str:
    if latitude_kind is None:
        return body_geometry(body).latitude_kind
    if not isinstance(latitude_kind, str):
        raise AppError(
            "invalid_latitude_convention",
            "latitude_kind must be a string declaration or omitted.",
            status_code=422,
        )
    normalised = latitude_kind.strip().lower().replace("-", "_")
    if body == EARTH_BODY:
        if normalised in _EARTH_LATITUDE_KINDS:
            return "geodetic"
        raise AppError(
            "invalid_latitude_convention",
            "Earth AOI latitude must be WGS84 geodetic latitude; planetocentric or "
            "geocentric latitude is not substituted silently.",
            status_code=422,
        )
    if normalised in _PLANETARY_LATITUDE_KINDS:
        return "planetocentric"
    if normalised in _FORBIDDEN_LATITUDE_KINDS:
        raise AppError(
            "invalid_latitude_convention",
            f"{body} planetographic/geocentric latitude is a different quantity and is "
            "not substituted silently; supply planetocentric latitude.",
            status_code=422,
        )
    raise AppError(
        "invalid_latitude_convention",
        f"{body} AOI latitude must be planetocentric; got {latitude_kind!r}.",
        status_code=422,
    )


def _longitude_direction(longitude_direction: str) -> str:
    if not isinstance(longitude_direction, str):
        raise AppError(
            "invalid_longitude_direction",
            "longitude_direction must be a string ('positive_east') or omitted.",
            status_code=422,
        )
    normalised = longitude_direction.strip().lower().replace("-", "_")
    if normalised != "positive_east":
        raise AppError(
            "invalid_longitude_direction",
            "AOI longitude must be positive-east; a positive-west request is not silently negated.",
            status_code=422,
        )
    return "positive_east"


def _parse_bbox(
    bbox: Mapping[str, float] | Sequence[float] | Any,
    *,
    lon_convention: str,
) -> tuple[dict[str, float], dict[str, float], bool, float, dict[str, float]]:
    values = _bbox_values(bbox)
    if not -90.0 <= values["south"] <= 90.0 or not -90.0 <= values["north"] <= 90.0:
        raise AppError(
            "invalid_bbox_latitude",
            "bbox south/north must be within [-90, 90] degrees.",
            status_code=422,
        )
    if values["south"] >= values["north"]:
        raise AppError(
            "invalid_bbox",
            "bbox south must be strictly less than north; a zero-height AOI is not valid.",
            status_code=422,
        )

    if lon_convention == "signed":
        if not all(-180.0 <= values[key] <= 180.0 for key in ("west", "east")):
            raise AppError(
                "invalid_bbox_longitude",
                "bbox west/east must be within [-180, 180] for lon_convention=signed.",
                status_code=422,
            )
        full_longitude = abs(values["east"] - values["west"]) >= 360.0 - _GEOMETRIC_EPSILON
        west = _normalize_longitude(values["west"])
        east = _normalize_longitude(values["east"])
    elif lon_convention == "360":
        if not all(0.0 <= values[key] <= 360.0 for key in ("west", "east")):
            raise AppError(
                "invalid_bbox_longitude",
                "bbox west/east must be within [0, 360] for lon_convention=360.",
                status_code=422,
            )
        full_longitude = abs(values["east"] - values["west"]) >= 360.0 - _GEOMETRIC_EPSILON
        west = _longitude_from_360(values["west"])
        east = _longitude_from_360(values["east"])
    else:
        raise AppError(
            "invalid_lon_convention",
            "lon_convention must be 'signed' or '360'.",
            status_code=422,
        )

    if full_longitude:
        west, east = -180.0, 180.0
    elif math.isclose(west, east, abs_tol=_GEOMETRIC_EPSILON):
        raise AppError(
            "invalid_bbox_longitude",
            "bbox west and east must not describe a zero-width longitude span.",
            status_code=422,
        )

    crossing = west > east
    span = (east + 360.0) - west if crossing else east - west
    requested = {key: float(values[key]) for key in _BBOX_KEYS}
    normalized = {
        "west": west,
        "south": values["south"],
        "east": east,
        "north": values["north"],
    }
    if full_longitude:
        bbox_0_360 = {
            "west": 0.0,
            "south": values["south"],
            "east": 360.0,
            "north": values["north"],
        }
    else:
        bbox_0_360 = {
            "west": west % 360.0,
            "south": values["south"],
            "east": east % 360.0,
            "north": values["north"],
        }
    return requested, normalized, crossing, span, bbox_0_360


def _bbox_pieces(normalized: dict[str, float], *, crossing: bool) -> tuple[dict[str, float], ...]:
    if not crossing:
        return (
            {
                "west": normalized["west"],
                "south": normalized["south"],
                "east": normalized["east"],
                "north": normalized["north"],
            },
        )
    pieces: list[dict[str, float]] = []
    if 180.0 - normalized["west"] > _GEOMETRIC_EPSILON:
        pieces.append(
            {
                "west": normalized["west"],
                "south": normalized["south"],
                "east": 180.0,
                "north": normalized["north"],
            }
        )
    if normalized["east"] + 180.0 > _GEOMETRIC_EPSILON:
        pieces.append(
            {
                "west": -180.0,
                "south": normalized["south"],
                "east": normalized["east"],
                "north": normalized["north"],
            }
        )
    if not pieces:
        raise AppError(
            "invalid_bbox_longitude",
            "bbox has no positive-width longitude piece.",
            status_code=422,
        )
    return tuple(pieces)


def _center(normalized: dict[str, float], *, crossing: bool, full: bool) -> tuple[float, float]:
    if full:
        lon = 0.0
    elif crossing:
        lon = _normalize_longitude((normalized["west"] + normalized["east"] + 360.0) / 2.0)
    else:
        lon = _normalize_longitude((normalized["west"] + normalized["east"]) / 2.0)
    return lon, (normalized["south"] + normalized["north"]) / 2.0


# ---------------------------------------------------------------------------
# Geodesic area and projected metric envelope
# ---------------------------------------------------------------------------


@lru_cache(maxsize=8)
def _geod_for(body: str) -> Geod:
    info = body_geometry(body)
    if info.metric_shape == "ellipsoid":
        return Geod(ellps="WGS84")
    radius = float(info.metric_radius_m or info.equatorial_radius_m)
    return Geod(a=radius, b=radius)


def _boundary_points(
    west: float, south: float, east: float, north: float
) -> tuple[list[float], list[float]]:
    """Densify a bbox boundary so projected extrema are not just the four corners."""
    lon_span = east - west
    lat_span = north - south
    lon_steps = max(2, math.ceil(lon_span / _MAX_DENSIFY_STEP_DEG - _GEOMETRIC_EPSILON))
    lat_steps = max(2, math.ceil(lat_span / _MAX_DENSIFY_STEP_DEG - _GEOMETRIC_EPSILON))
    lons: list[float] = []
    lats: list[float] = []
    for index in range(lon_steps):
        lons.append(west + lon_span * index / lon_steps)
        lats.append(south)
    for index in range(lat_steps):
        lons.append(east)
        lats.append(south + lat_span * index / lat_steps)
    for index in range(lon_steps):
        lons.append(east - lon_span * index / lon_steps)
        lats.append(north)
    for index in range(1, lat_steps):
        lons.append(west)
        lats.append(north - lat_span * index / lat_steps)
    return lons, lats


def _geodesic_area_km2(body: str, pieces: Sequence[Mapping[str, float]]) -> float:
    geod = _geod_for(body)
    total_m2 = 0.0
    for piece in pieces:
        west = piece["west"]
        east = piece["east"]
        span = east - west
        chunks = max(1, math.ceil(span / 180.0 - _GEOMETRIC_EPSILON))
        for index in range(chunks):
            start = west + span * index / chunks
            end = west + span * (index + 1) / chunks
            lons, lats = _boundary_points(start, piece["south"], end, piece["north"])
            area = geod.polygon_area_perimeter(lons, lats)[0]
            if not math.isfinite(area):
                raise AppError(
                    "invalid_bbox",
                    "The requested AOI area could not be computed on the body shape.",
                    status_code=422,
                )
            total_m2 += abs(float(area))
    if total_m2 <= 0 or not math.isfinite(total_m2):
        raise AppError(
            "invalid_bbox",
            "The requested AOI does not enclose a positive area.",
            status_code=422,
        )
    return total_m2 / 1e6


def _source_crs(body: str) -> CRS:
    info = body_geometry(body)
    if info.metric_shape == "ellipsoid":
        return CRS.from_epsg(4326)
    return CRS.from_proj4(f"+proj=longlat +R={float(info.metric_radius_m):.1f} +no_defs")


def _metric_proj_string(body: str, center_lon: float, center_lat: float) -> str:
    info = body_geometry(body)
    if info.metric_shape == "ellipsoid":
        return (
            f"+proj=aeqd +lat_0={center_lat:.12f} +lon_0={center_lon:.12f} "
            "+ellps=WGS84 +units=m +no_defs"
        )
    return (
        f"+proj=aeqd +lat_0={center_lat:.12f} +lon_0={center_lon:.12f} "
        f"+R={float(info.metric_radius_m):.1f} +units=m +no_defs"
    )


def _metric_envelope(
    body: str,
    pieces: Sequence[Mapping[str, float]],
    *,
    center_lon: float,
    center_lat: float,
) -> MetricEnvelope:
    proj_string = _metric_proj_string(body, center_lon, center_lat)
    try:
        transformer = Transformer.from_crs(
            _source_crs(body), CRS.from_proj4(proj_string), always_xy=True
        )
    except Exception as exc:  # pragma: no cover - defensive for a bad local PROJ string
        raise AppError(
            "coordinate_transform_failed",
            f"The local metric projection for this AOI could not be created: {exc}",
            status_code=422,
        ) from exc

    xs: list[float] = []
    ys: list[float] = []
    for piece in pieces:
        lons, lats = _boundary_points(piece["west"], piece["south"], piece["east"], piece["north"])
        for lon, lat in zip(lons, lats, strict=True):
            try:
                x, y = transformer.transform(lon, lat)
            except Exception as exc:
                raise AppError(
                    "coordinate_transform_failed",
                    "The requested AOI boundary could not be projected into a local metric "
                    "envelope; split the AOI instead of shrinking it.",
                    status_code=422,
                ) from exc
            if not (math.isfinite(x) and math.isfinite(y)):
                raise AppError(
                    "coordinate_transform_failed",
                    "The requested AOI is too large for one local metric envelope; split it "
                    "into smaller AOIs. It is never shrunk to a centre window.",
                    status_code=422,
                )
            xs.append(float(x))
            ys.append(float(y))

    x_min, x_max = min(xs), max(xs)
    y_min, y_max = min(ys), max(ys)
    width = x_max - x_min
    height = y_max - y_min
    if width <= _METRIC_EPSILON_M or height <= _METRIC_EPSILON_M:
        raise AppError(
            "coordinate_transform_failed",
            "The requested AOI has no positive extent in one local metric envelope; split it "
            "into smaller AOIs. It is never shrunk to a centre window.",
            status_code=422,
        )
    return MetricEnvelope(
        crs=proj_string,
        projection=_METRIC_PROJECTION,
        projection_center_lon=center_lon,
        projection_center_lat=center_lat,
        x_min_m=x_min,
        y_min_m=y_min,
        x_max_m=x_max,
        y_max_m=y_max,
        width_m=width,
        height_m=height,
        point_count=len(xs),
        semantics=_METRIC_SEMANTICS,
    )


# ---------------------------------------------------------------------------
# Geometry and limits
# ---------------------------------------------------------------------------


def _bbox_geometry(pieces: Sequence[Mapping[str, float]]) -> dict[str, Any]:
    rings = [
        [
            [piece["west"], piece["south"]],
            [piece["east"], piece["south"]],
            [piece["east"], piece["north"]],
            [piece["west"], piece["north"]],
            [piece["west"], piece["south"]],
        ]
        for piece in pieces
    ]
    if len(rings) == 1:
        return {"type": "Polygon", "coordinates": rings}
    return {"type": "MultiPolygon", "coordinates": [[ring] for ring in rings]}


def _check_side_and_pixel_limits(
    *,
    body: str,
    limits: AoiLimits,
    width_m: float,
    height_m: float,
    pixel_size_m: float,
) -> tuple[int, int, int]:
    shortest = min(width_m, height_m)
    longest = max(width_m, height_m)
    if shortest < limits.min_side_m - _GEOMETRIC_EPSILON:
        raise AppError(
            "aoi_too_small",
            f"AOI sides must be at least {limits.min_side_m:.0f} m on {body}; got "
            f"{shortest:.3f} m. The bbox is not expanded to a minimum window.",
            status_code=422,
            details=[{"field": "min_side_m", "observed": shortest, "allowed": limits.min_side_m}],
        )
    if longest > limits.max_side_m + _GEOMETRIC_EPSILON:
        raise AppError(
            "aoi_too_large",
            f"AOI sides must be at most {limits.max_side_m:.0f} m on {body}; got "
            f"{longest:.3f} m. The request is rejected, not shrunk to a centre window.",
            status_code=422,
            details=[{"field": "max_side_m", "observed": longest, "allowed": limits.max_side_m}],
        )
    pixels_x = max(1, math.ceil(width_m / pixel_size_m - _GEOMETRIC_EPSILON))
    pixels_y = max(1, math.ceil(height_m / pixel_size_m - _GEOMETRIC_EPSILON))
    total_pixels = pixels_x * pixels_y
    if total_pixels > limits.max_pixels:
        raise AppError(
            "aoi_too_many_pixels",
            f"AOI needs {total_pixels} pixels at {pixel_size_m:g} m, above the "
            f"{limits.max_pixels} pixel budget. The request is rejected, not downsampled "
            "silently.",
            status_code=422,
            details=[
                {
                    "field": "total_pixels",
                    "observed": total_pixels,
                    "allowed": limits.max_pixels,
                }
            ],
        )
    return pixels_x, pixels_y, total_pixels


def _warnings(
    *,
    body: str,
    crossing: bool,
    full_longitude: bool,
    includes_north_pole: bool,
    includes_south_pole: bool,
) -> tuple[str, ...]:
    messages: list[str] = []
    if crossing:
        messages.append(
            "The bbox crosses the antimeridian; both longitude pieces are included in full."
        )
    if full_longitude:
        messages.append(
            "The bbox spans all longitudes; it is treated as a latitude band, not a "
            "zero-width or centre window."
        )
    if includes_south_pole or includes_north_pole:
        pole = "south" if includes_south_pole else "north"
        messages.append(
            f"The bbox includes the {pole} pole; at the pole all longitudes coincide, so "
            "longitude bounds are degenerate there and the pole is not clamped."
        )
    if not body_geometry(body).earth_wgs84:
        messages.append(
            f"{body} coordinates are planetocentric body-frame degrees, never Earth WGS84."
        )
    return tuple(messages)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def validate_aoi_geometry(
    body: str,
    bbox: Mapping[str, float] | Sequence[float] | Any,
    *,
    lon_convention: str = "signed",
    latitude_kind: str | None = None,
    longitude_direction: str = "positive_east",
    limits: AoiLimits | None = None,
    pixel_size_m: float | None = None,
) -> AoiGeometry:
    """Validate a full AOI and derive its geometry, area, metric envelope and limits.

    Parameters
    ----------
    body:
        ``Earth`` (WGS84 geodetic) or ``Moon``/``Mars`` (planetocentric body frame).
    bbox:
        Mapping, four-value sequence or object with ``west``/``south``/``east``/``north``.
        The values are kept exactly as requested in ``requested_bbox``.
    lon_convention:
        ``signed`` accepts ``[-180, 180]``; ``360`` accepts ``[0, 360]``. Exact
        ``-180``/``180`` edges are preserved so a full-longitude request stays full.
    latitude_kind:
        Optional explicit declaration. Earth accepts geodetic/WGS84; Moon/Mars accept
        planetocentric only. A mismatched declaration is an explicit error, never a
        silent conversion.
    longitude_direction:
        Only ``positive_east`` is accepted; ``positive_west`` is rejected rather than
        silently negated.
    limits:
        Explicit safety caps. Defaults mirror the shared engine quota; passing limits
        changes the contract deliberately.
    pixel_size_m:
        Analysis pixel size used for the pixel budget. Defaults to 90 m on Earth and
        250 m on Moon/Mars.
    """
    if body not in SUPPORTED_AOI_BODIES:
        raise AppError(
            "unsupported_body",
            "AOI geometry supports body Earth, Moon or Mars.",
            status_code=422,
        )
    info = body_geometry(body)
    _longitude_direction(longitude_direction)
    _latitude_kind(body, latitude_kind)
    resolved_limits = _checked_limits(body, limits)
    resolved_pixel_size = _checked_pixel_size(body, pixel_size_m)

    requested, normalized, crossing, span, bbox_0_360 = _parse_bbox(
        bbox, lon_convention=lon_convention
    )
    if span > resolved_limits.max_span_deg + _GEOMETRIC_EPSILON:
        raise AppError(
            "bbox_too_wide",
            f"bbox longitude span {span:.6f} degrees exceeds the {body} limit of "
            f"{resolved_limits.max_span_deg:g} degrees. Split the AOI instead of shrinking it.",
            status_code=422,
            details=[
                {
                    "field": "longitude_span_deg",
                    "observed": span,
                    "allowed": resolved_limits.max_span_deg,
                }
            ],
        )
    full_longitude = span >= 360.0 - _GEOMETRIC_EPSILON
    pieces = _bbox_pieces(normalized, crossing=crossing)
    center_lon, center_lat = _center(normalized, crossing=crossing, full=full_longitude)
    includes_north_pole = normalized["north"] >= 90.0 - _GEOMETRIC_EPSILON
    includes_south_pole = normalized["south"] <= -90.0 + _GEOMETRIC_EPSILON

    area_km2 = _geodesic_area_km2(body, pieces)
    if area_km2 > resolved_limits.max_area_km2 + _GEOMETRIC_EPSILON:
        raise AppError(
            "aoi_too_large",
            f"AOI area {area_km2:.3f} km2 exceeds the {body} limit of "
            f"{resolved_limits.max_area_km2:.3f} km2. The request is rejected, not shrunk.",
            status_code=422,
            details=[
                {
                    "field": "area_km2",
                    "observed": area_km2,
                    "allowed": resolved_limits.max_area_km2,
                }
            ],
        )

    envelope = _metric_envelope(body, pieces, center_lon=center_lon, center_lat=center_lat)
    pixels_x, pixels_y, total_pixels = _check_side_and_pixel_limits(
        body=body,
        limits=resolved_limits,
        width_m=envelope.width_m,
        height_m=envelope.height_m,
        pixel_size_m=resolved_pixel_size,
    )

    return AoiGeometry(
        body=body,
        body_info=info,
        requested_bbox=requested,
        normalized_bbox=normalized,
        bbox_0_360=bbox_0_360,
        bbox_pieces=pieces,
        crosses_antimeridian=crossing,
        is_full_longitude=full_longitude,
        longitude_span_deg=span,
        longitude_convention=lon_convention,
        center_lon=center_lon,
        center_lat=center_lat,
        area_km2=area_km2,
        envelope_area_km2=envelope.width_m * envelope.height_m / 1e6,
        metric_envelope=envelope,
        pixel_size_m=resolved_pixel_size,
        pixels_x=pixels_x,
        pixels_y=pixels_y,
        total_pixels=total_pixels,
        limits=resolved_limits,
        geometry=_bbox_geometry(pieces),
        warnings=_warnings(
            body=body,
            crossing=crossing,
            full_longitude=full_longitude,
            includes_north_pole=includes_north_pole,
            includes_south_pole=includes_south_pole,
        ),
        includes_north_pole=includes_north_pole,
        includes_south_pole=includes_south_pole,
        analysis_extent="full_requested_aoi",
        shrink_applied=False,
    )


__all__ = [
    "AOI_ENGINE_GEOMETRY_VERSION",
    "BODY_DEFAULT_LIMITS",
    "BODY_DEFAULT_PIXEL_SIZE_M",
    "EARTH_BBOX_CRS",
    "EARTH_BODY",
    "PLANETARY_ANALYSIS_RESOLUTION_M",
    "SUPPORTED_AOI_BODIES",
    "AoiGeometry",
    "AoiLimits",
    "BodyGeometry",
    "MetricEnvelope",
    "body_geometry",
    "validate_aoi_geometry",
]
