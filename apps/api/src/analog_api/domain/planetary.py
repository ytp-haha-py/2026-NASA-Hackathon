"""Planetary basemap catalog and coordinate contract (Issue #117).

Only official remote tile services are referenced. This module never downloads or redistributes a
global raster. A browsable global basemap is explicitly separated from the curated scientific
coverage stored in the database.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from analog_api.domain.errors import AppError

COORDINATE_CONTRACT_VERSION = "planetary-coordinates-1.0.0"
MAP_CATALOG_VERSION = "planetary-map-catalog-2026-11-15"
TREK_CAPABILITIES_ROOT = "https://trek.nasa.gov/tiles"
NASA_MEDIA_GUIDELINES = "https://www.nasa.gov/nasa-brand-center/images-and-media/"


@dataclass(frozen=True, slots=True)
class PlanetaryBody:
    body: str
    radius_m: float
    polar_radius_m: float
    geographic_epsg: int
    frame: str
    coordinate_system: str
    latitude_kind: str
    longitude_direction: str
    native_longitude_domain: str
    api_longitude_domain: str
    notes: str

    @property
    def flattening(self) -> float:
        return (self.radius_m - self.polar_radius_m) / self.radius_m

    def to_payload(self) -> dict[str, object]:
        return {
            "body": self.body,
            "mean_radius_m": self.radius_m,
            "polar_radius_m": self.polar_radius_m,
            "flattening": self.flattening,
            "frame": self.frame,
            "coordinate_system": self.coordinate_system,
            "latitude_kind": self.latitude_kind,
            "longitude_direction": self.longitude_direction,
            "native_longitude_domain": self.native_longitude_domain,
            "api_longitude_domain": self.api_longitude_domain,
            "geographic_epsg": self.geographic_epsg,
            "geographic_epsg_uri": f"urn:ogc:def:crs:EPSG::{self.geographic_epsg}",
            "geographic_epsg_authority": (
                "ESRI/IAU code advertised by NASA Trek as a URN; it is not an EPSG registry code"
            ),
            "geographic_epsg_is_epsg_registry": False,
            "earth_wgs84": False,
            "notes": self.notes,
        }


PLANETARY_BODIES: dict[str, PlanetaryBody] = {
    "Moon": PlanetaryBody(
        body="Moon",
        radius_m=1737400.0,
        polar_radius_m=1737400.0,
        geographic_epsg=104903,
        frame="lunar_planetocentric",
        coordinate_system="IAU_MOON",
        latitude_kind="planetocentric",
        longitude_direction="positive_east",
        native_longitude_domain="0..360 (Trek WMTS tile grid is addressed from -180 to 180)",
        api_longitude_domain="-180..180 (signed, half-open; 180 is normalised to -180)",
        notes=(
            "Sphere in the IAU 2015 Moon frame. No planetographic/planetocentric latitude "
            "difference because the reference shape is spherical."
        ),
    ),
    "Mars": PlanetaryBody(
        body="Mars",
        radius_m=3396190.0,
        polar_radius_m=3376200.0,
        geographic_epsg=104905,
        frame="mars_planetocentric",
        coordinate_system="IAU_MARS",
        latitude_kind="planetocentric",
        longitude_direction="positive_east",
        native_longitude_domain="0..360 (Trek WMTS tile grid is addressed from -180 to 180)",
        api_longitude_domain="-180..180 (signed, half-open; 180 is normalised to -180)",
        notes=(
            "IAU 2015 Mars ellipsoid a=3396190 m, b=3376200 m. The API and the Trek mosaic use "
            "planetocentric latitude; planetographic latitude is not silently substituted."
        ),
    ),
}


def get_body(body: str) -> PlanetaryBody:
    value = PLANETARY_BODIES.get(body)
    if value is None:
        raise AppError(
            "unsupported_body",
            "body must be Moon or Mars for planetary map endpoints.",
            status_code=422,
        )
    return value


def normalize_longitude(lon: float, *, convention: str = "signed") -> float:
    """Normalise a longitude reproducibly.

    ``signed`` is half-open [-180, 180); ``360`` is [0, 360). Inputs outside either domain wrap.
    """
    if not math.isfinite(lon):
        raise AppError("invalid_longitude", "longitude must be finite.", status_code=422)
    if convention == "360":
        return lon % 360.0
    if convention != "signed":
        raise AppError(
            "invalid_longitude_convention",
            "longitude convention must be 'signed' or '360'.",
            status_code=422,
        )
    return (lon + 180.0) % 360.0 - 180.0


def to_360(lon: float) -> float:
    return normalize_longitude(lon, convention="360")


def to_signed(lon: float) -> float:
    return normalize_longitude(lon)


def planetocentric_to_planetographic(lat_deg: float, body: str) -> float:
    """Convert planetocentric latitude to planetographic latitude for an oblate body."""
    info = get_body(body)
    if info.radius_m == info.polar_radius_m:
        return lat_deg
    ratio = (info.radius_m / info.polar_radius_m) ** 2
    return math.degrees(math.atan(ratio * math.tan(math.radians(lat_deg))))


def planetographic_to_planetocentric(lat_deg: float, body: str) -> float:
    info = get_body(body)
    if info.radius_m == info.polar_radius_m:
        return lat_deg
    ratio = (info.polar_radius_m / info.radius_m) ** 2
    return math.degrees(math.atan(ratio * math.tan(math.radians(lat_deg))))


@dataclass(frozen=True, slots=True)
class GeographicBBox:
    west: float
    south: float
    east: float
    north: float
    crosses_antimeridian: bool = False
    source: str = "query"

    def as_dict(self) -> dict[str, object]:
        return {
            "west": self.west,
            "south": self.south,
            "east": self.east,
            "north": self.north,
            "crosses_antimeridian": self.crosses_antimeridian,
            "longitude_span_deg": self.longitude_span(),
            "source": self.source,
        }

    def longitude_span(self) -> float:
        if self.crosses_antimeridian:
            return (self.east + 360.0) - self.west
        span = self.east - self.west
        return 360.0 if span >= 360.0 else max(span, 0.0)

    def is_global(self) -> bool:
        return self.longitude_span() >= 360.0 - 1e-9

    def split(self) -> list[GeographicBBox]:
        if not self.crosses_antimeridian:
            return [self]
        return [
            GeographicBBox(self.west, self.south, 180.0, self.north, source=self.source),
            GeographicBBox(-180.0, self.south, self.east, self.north, source=self.source),
        ]


def parse_bbox(raw: str, *, lon_convention: str = "signed") -> GeographicBBox:
    parts = [item.strip() for item in raw.split(",")]
    if len(parts) != 4:
        raise AppError(
            "invalid_bbox",
            "bbox must contain four comma-separated values: west,south,east,north.",
            status_code=422,
        )
    try:
        west, south, east, north = (float(item) for item in parts)
    except ValueError as exc:
        raise AppError("invalid_bbox", "bbox values must be numbers.", status_code=422) from exc
    if not all(math.isfinite(item) for item in (west, south, east, north)):
        raise AppError("invalid_bbox", "bbox values must be finite.", status_code=422)
    if lon_convention == "360":
        west = to_signed(west)
        east = to_signed(east)
    elif lon_convention != "signed":
        raise AppError(
            "invalid_longitude_convention",
            "bbox_lon_convention must be 'signed' or '360'.",
            status_code=422,
        )
    if not -90.0 <= south <= 90.0 or not -90.0 <= north <= 90.0:
        raise AppError(
            "invalid_bbox_latitude",
            "bbox south/north must be within [-90, 90] degrees planetocentric latitude.",
            status_code=422,
        )
    if south > north:
        raise AppError("invalid_bbox", "bbox south must be <= north.", status_code=422)
    if west < -180.0 or west > 180.0 or east < -180.0 or east > 180.0:
        raise AppError(
            "invalid_bbox_longitude",
            "bbox west/east must be within [-180, 180] after normalisation.",
            status_code=422,
        )
    return GeographicBBox(
        west=west,
        south=south,
        east=east,
        north=north,
        crosses_antimeridian=west > east,
        source="query",
    )


def _simple_intersects(left: GeographicBBox, right: GeographicBBox) -> bool:
    return (
        left.west <= right.east
        and right.west <= left.east
        and left.south <= right.north
        and right.south <= left.north
    )


def bbox_intersects(left: GeographicBBox, right: GeographicBBox) -> bool:
    if left.is_global() or right.is_global():
        return left.south <= right.north and right.south <= left.north
    return any(_simple_intersects(a, b) for a in left.split() for b in right.split())


def bbox_contains(bbox: GeographicBBox, lat: float, lon: float) -> bool:
    if not bbox.south <= lat <= bbox.north:
        return False
    value = normalize_longitude(lon)
    if bbox.is_global():
        return True
    if bbox.crosses_antimeridian:
        return value >= bbox.west or value <= bbox.east
    return bbox.west <= value <= bbox.east


def geographic_bbox_from_center(
    *, lat: float, lon: float, window_m: float, body: str, source: str = "derived_from_center"
) -> GeographicBBox:
    info = get_body(body)
    if not math.isfinite(lat) or not math.isfinite(lon):
        raise AppError("invalid_coordinate", "lat/lon must be finite.", status_code=422)
    if not -90.0 <= lat <= 90.0:
        raise AppError("invalid_latitude", "lat must be within [-90, 90].", status_code=422)
    if window_m <= 0 or not math.isfinite(window_m):
        raise AppError("invalid_window", "window size must be positive.", status_code=422)
    half = window_m / 2.0
    delta_lat = math.degrees(half / info.radius_m)
    south = max(-90.0, lat - delta_lat)
    north = min(90.0, lat + delta_lat)
    cos_lat = max(abs(math.cos(math.radians(lat))), 1e-9)
    delta_lon = math.degrees(half / (info.radius_m * cos_lat))
    if delta_lon >= 180.0 or north >= 90.0 or south <= -90.0:
        return GeographicBBox(-180.0, south, 180.0, north, source=source)
    west = normalize_longitude(lon - delta_lon)
    east = normalize_longitude(lon + delta_lon)
    return GeographicBBox(
        west=west,
        south=south,
        east=east,
        north=north,
        crosses_antimeridian=west > east,
        source=source,
    )


def _is_geographic_crs(crs: str) -> bool:
    upper = crs.upper()
    if upper in {"EPSG:4326", "OGC:CRS84", "CRS:84"}:
        return True
    if "+PROJ=LONGLA" in upper:
        return True
    return "GEOGCRS" in upper and "PROJCRS" not in upper


def projected_bbox_to_geographic(*, crs: str, bbox: dict[str, float], body: str) -> GeographicBBox:
    """Transform a projected AOI envelope into a planetocentric lon/lat envelope.

    The four edges are densified so a polar-stereographic rectangle does not collapse to its
    corners. The result is an envelope, not a re-projection of every source pixel.
    """
    info = get_body(body)
    west = float(bbox["west"])
    south = float(bbox["south"])
    east = float(bbox["east"])
    north = float(bbox["north"])
    if _is_geographic_crs(crs):
        return GeographicBBox(
            west=normalize_longitude(west),
            south=max(-90.0, south),
            east=normalize_longitude(east),
            north=min(90.0, north),
            crosses_antimeridian=normalize_longitude(west) > normalize_longitude(east),
            source="stored_geographic_bbox",
        )

    from pyproj import CRS, Transformer

    target = CRS.from_proj4(f"+proj=longlat +a={info.radius_m} +b={info.polar_radius_m} +no_defs")
    try:
        transformer = Transformer.from_crs(CRS.from_user_input(crs), target, always_xy=True)
    except Exception as exc:  # pragma: no cover - defensive for unexpected provider WKT
        raise AppError(
            "unsupported_source_crs",
            f"Cannot transform stored area CRS for {body}: {exc}",
            status_code=422,
        ) from exc

    steps = 8
    points: list[tuple[float, float]] = []
    for index in range(steps + 1):
        fraction = index / steps
        x = west + (east - west) * fraction
        points.append((x, south))
        points.append((x, north))
    for index in range(steps + 1):
        fraction = index / steps
        y = south + (north - south) * fraction
        points.append((west, y))
        points.append((east, y))
    lons: list[float] = []
    lats: list[float] = []
    for x, y in points:
        lon, lat = transformer.transform(x, y)
        if math.isfinite(lon) and math.isfinite(lat):
            lons.append(normalize_longitude(lon))
            lats.append(lat)
    if not lons or not lats:
        raise AppError(
            "coordinate_transform_failed",
            "The stored area bbox could not be transformed into planetocentric lon/lat.",
            status_code=422,
        )
    min_lon = min(lons)
    max_lon = max(lons)
    if max_lon - min_lon > 180.0:
        shifted = [value + 360.0 if value < 0 else value for value in lons]
        west_out = normalize_longitude(min(shifted))
        east_out = normalize_longitude(max(shifted))
        crosses = True
    else:
        west_out = min_lon
        east_out = max_lon
        crosses = False
    return GeographicBBox(
        west=west_out,
        south=max(-90.0, min(lats)),
        east=east_out,
        north=min(90.0, max(lats)),
        crosses_antimeridian=crosses,
        source="projected_bbox_transformed",
    )


def point_geometry(lon: float, lat: float) -> dict[str, object]:
    return {"type": "Point", "coordinates": [normalize_longitude(lon), lat]}


def bbox_geometry(bbox: GeographicBBox) -> dict[str, object]:
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


def coordinate_contract_payload() -> dict[str, object]:
    mars = PLANETARY_BODIES["Mars"]
    example_lat = 18.220253246764436
    return {
        "contract_version": COORDINATE_CONTRACT_VERSION,
        "bodies": [body.to_payload() for body in PLANETARY_BODIES.values()],
        "rules": [
            "All API geometry uses planetocentric longitude/latitude in ICRS body-fixed frames; "
            "never Earth WGS84/EPSG:4326 and never an OSM/Web-Mercator assumption.",
            "Longitude is positive east. API queries and responses use signed [-180, 180); "
            "clients may query with 0..360 by setting bbox_lon_convention=360.",
            "Latitude is planetocentric. Mars is an ellipsoid a=3396190 m, b=3376200 m; "
            "planetographic latitude is a different quantity and is not substituted silently.",
            "A bbox with west > east crosses the antimeridian. Both parts are included; empty "
            "results remain explicit rather than being wrapped to an Earth bbox.",
            "Poles are included as ordinary latitude bounds. A polar area may span all longitudes; "
            "the API then returns a global-longitude envelope for that latitude band.",
            "GeoJSON geometries use [longitude, latitude] in degrees, but the CRS is the planetary "
            "body frame declared on every response; it is not EPSG:4326.",
            "Basemap tile services may use their own tile matrix and longitude addressing. The "
            "catalog exposes the exact template, EPSG code, zoom range and bounds.",
        ],
        "antimeridian": {
            "query_example": "bbox=120,-90,-150,-85",
            "interpretation": "west=120, north=-85, east=-150; the box crosses 180 degrees east.",
            "split_parts": [[120.0, -90.0, 180.0, -85.0], [-180.0, -90.0, -150.0, -85.0]],
            "response_flag": "crosses_antimeridian=true",
        },
        "poles": {
            "lat_range": [-90.0, 90.0],
            "note": (
                "For a near-polar window the longitude envelope may cover all longitudes. The API "
                "reports the envelope it actually used and does not clamp a pole to a rectangle."
            ),
        },
        "longitude_conversion": {
            "formula_signed_to_360": "((lon % 360) + 360) % 360",
            "formula_360_to_signed": "((lon + 180) % 360) - 180",
            "examples": [
                {"signed": -137.09597807, "east_0_360": 222.90402193},
                {"signed": 123.54628783, "east_0_360": 123.54628783},
                {"signed": -0.23578377, "east_0_360": 359.76421623},
            ],
        },
        "planetocentric_to_planetographic": {
            "formula": "tan(lat_planetographic) = (a^2 / b^2) * tan(lat_planetocentric)",
            "mars_example": {
                "planetocentric_deg": example_lat,
                "planetographic_deg": planetocentric_to_planetographic(example_lat, "Mars"),
                "a_m": mars.radius_m,
                "b_m": mars.polar_radius_m,
            },
            "moon_example": {
                "planetocentric_deg": -89.43892646,
                "planetographic_deg": planetocentric_to_planetographic(-89.43892646, "Moon"),
                "note": "Moon is spherical in this contract, so the values are identical.",
            },
        },
        "geojson": {
            "coordinate_order": ["longitude", "latitude"],
            "unit": "degree",
            "crs": "planetocentric body frame declared per feature; not Earth EPSG:4326",
        },
    }


@dataclass(frozen=True, slots=True)
class PlanetaryMapLayer:
    layer_id: str
    body: str
    title: str
    layer_type: str
    service_type: str
    tile_template: str
    format: str
    tile_matrix_set: str
    crs: str
    epsg: int
    crs_authority: str
    coordinate_note: str
    zoom_semantics: str
    zoom_min: int
    zoom_max: int
    tile_size: int
    matrix_sizes: tuple[tuple[int, int, int], ...]
    bounds: GeographicBBox
    attribution: str
    source_url: str
    capabilities_url: str
    license: str
    license_url: str
    verification: dict[str, object]
    notes: tuple[str, ...] = ()
    fallback: str | None = None

    def to_payload(self) -> dict[str, object]:
        return {
            "layer_id": self.layer_id,
            "body": self.body,
            "title": self.title,
            "layer_type": self.layer_type,
            "service_type": self.service_type,
            "tile_template": self.tile_template,
            "format": self.format,
            "tile_matrix_set": self.tile_matrix_set,
            "crs": self.crs,
            "epsg": self.epsg,
            "crs_authority": self.crs_authority,
            "coordinate_note": self.coordinate_note,
            "zoom_semantics": self.zoom_semantics,
            "zoom": {"min": self.zoom_min, "max": self.zoom_max},
            "tile_size": self.tile_size,
            "tile_matrix": [
                {"zoom": zoom, "matrix_width": width, "matrix_height": height}
                for zoom, width, height in self.matrix_sizes
            ],
            "bounds": self.bounds.as_dict(),
            "attribution": self.attribution,
            "source_url": self.source_url,
            "capabilities_url": self.capabilities_url,
            "license": self.license,
            "license_url": self.license_url,
            "verification": self.verification,
            "notes": list(self.notes),
            "fallback": self.fallback,
        }


def _trek_layer(
    *,
    layer_id: str,
    body: str,
    title: str,
    layer_path: str,
    layer_type: str,
    format: str,
    zoom_max: int,
    attribution: str,
    sample_tile: str,
    notes: tuple[str, ...],
) -> PlanetaryMapLayer:
    info = PLANETARY_BODIES[body]
    base = f"{TREK_CAPABILITIES_ROOT}/{body}/EQ/{layer_path}/1.0.0"
    extension = "jpg" if format == "jpeg" else format
    template = f"{base}/default/default028mm/{{TileMatrix}}/{{TileRow}}/{{TileCol}}.{extension}"
    matrices = tuple((zoom, 2 * (2**zoom), 2**zoom) for zoom in range(zoom_max + 1))
    return PlanetaryMapLayer(
        layer_id=layer_id,
        body=body,
        title=title,
        layer_type=layer_type,
        service_type="OGC WMTS 1.0.0 RESTful",
        tile_template=template,
        format=f"image/{format}",
        tile_matrix_set="default028mm",
        crs=f"urn:ogc:def:crs:EPSG::{info.geographic_epsg}",
        epsg=info.geographic_epsg,
        crs_authority=(
            "ESRI/IAU GCS code advertised inside an EPSG-style URN by NASA Trek; not an EPSG "
            "registry code"
        ),
        coordinate_note=(
            "Moon is a 1,737,400 m sphere. Mars is the IAU 2000 ellipsoid a=3,396,190 m, "
            "b=3,376,200 m; the PDS MOLA source declares planetocentric latitude, while the "
            "rendered WMTS latitude convention is not separately declared by the layer metadata. "
            "Do not treat these coordinates as Earth WGS84/OSM; validate high-latitude overlays "
            "against the source product."
        ),
        zoom_semantics=(
            "WMTS TileMatrix level from this layer's explicit matrix; not Web-Mercator/OSM zoom. "
            "The provider ScaleDenominator is Earth-referenced and must not be used for planetary "
            "ground resolution."
        ),
        zoom_min=0,
        zoom_max=zoom_max,
        tile_size=256,
        matrix_sizes=matrices,
        bounds=GeographicBBox(-180.0, -90.0, 180.0, 90.0, source="wmts_layer_bounds"),
        attribution=attribution,
        source_url=f"{base}/WMTSCapabilities.xml",
        capabilities_url=f"{base}/WMTSCapabilities.xml",
        license="NASA public domain; NASA Media Usage Guidelines",
        license_url=NASA_MEDIA_GUIDELINES,
        verification={
            "status": "verified",
            "checked_at": "2026-11-15",
            "http_status": 200,
            "content_type": f"image/{format}",
            "sample_tile_url": sample_tile,
            "capabilities_http_status": 200,
        },
        notes=notes,
    )


PLANETARY_MAP_LAYERS: tuple[PlanetaryMapLayer, ...] = (
    _trek_layer(
        layer_id="moon-lro-wac-global-303ppd-v02",
        body="Moon",
        title="LRO WAC Global Mosaic (303 ppd)",
        layer_path="LRO_WAC_Mosaic_Global_303ppd_v02",
        layer_type="imagery",
        format="jpeg",
        zoom_max=8,
        attribution=(
            "NASA / GSFC / Arizona State University, LRO Wide Angle Camera mosaic; "
            "served by NASA Trek. NASA Media Usage Guidelines apply."
        ),
        sample_tile=(
            f"{TREK_CAPABILITIES_ROOT}/Moon/EQ/LRO_WAC_Mosaic_Global_303ppd_v02/1.0.0/"
            "default/default028mm/0/0/0.jpg"
        ),
        notes=(
            "Global visible-image mosaic. A browsable basemap is not a claim of global "
            "six-dimension scientific coverage.",
        ),
    ),
    _trek_layer(
        layer_id="moon-lro-lola-shaded-relief-128ppd-v04",
        body="Moon",
        title="LRO LOLA Colored Shaded Relief (128 ppd)",
        layer_path="LRO_LOLA_ClrShade_Global_128ppd_v04",
        layer_type="elevation",
        format="png",
        zoom_max=5,
        attribution=(
            "NASA / GSFC / MIT, LRO Lunar Orbiter Laser Altimeter shaded relief; "
            "served by NASA Trek. NASA Media Usage Guidelines apply."
        ),
        sample_tile=(
            f"{TREK_CAPABILITIES_ROOT}/Moon/EQ/LRO_LOLA_ClrShade_Global_128ppd_v04/1.0.0/"
            "default/default028mm/0/0/0.png"
        ),
        notes=(
            "Colored shaded relief is a visualization, not a raw elevation value tile. Use the "
            "scientific reference metrics for analysis, not this basemap.",
        ),
    ),
    _trek_layer(
        layer_id="mars-viking-mola-basemap-232m",
        body="Mars",
        title="Mars Viking MDIM21 Color Mosaic (232 m)",
        layer_path="Mars_Viking_MDIM21_ClrMosaic_global_232m",
        layer_type="imagery",
        format="jpeg",
        zoom_max=7,
        attribution=(
            "NASA / JPL / USGS, Mars Viking MDIM21 color mosaic; served by NASA Trek. "
            "NASA Media Usage Guidelines apply."
        ),
        sample_tile=(
            f"{TREK_CAPABILITIES_ROOT}/Mars/EQ/Mars_Viking_MDIM21_ClrMosaic_global_232m/1.0.0/"
            "default/default028mm/0/0/0.jpg"
        ),
        notes=(
            "Global visible-image mosaic. It does not contain rover-scale or in-situ chemistry.",
        ),
    ),
    _trek_layer(
        layer_id="mars-mgs-mola-shaded-relief-463m",
        body="Mars",
        title="Mars MGS MOLA Colored Shaded Relief (463 m)",
        layer_path="Mars_MGS_MOLA_ClrShade_merge_global_463m",
        layer_type="elevation",
        format="jpeg",
        zoom_max=7,
        attribution=(
            "NASA / GSFC / MOLA Science Team, Mars Global Surveyor MOLA shaded relief; "
            "served by NASA Trek. NASA Media Usage Guidelines apply."
        ),
        sample_tile=(
            f"{TREK_CAPABILITIES_ROOT}/Mars/EQ/Mars_MGS_MOLA_ClrShade_merge_global_463m/1.0.0/"
            "default/default028mm/0/0/0.jpg"
        ),
        notes=("Colored shaded relief is a visualization, not a raw elevation value tile.",),
    ),
)


def layers_for_body(body: str) -> list[PlanetaryMapLayer]:
    get_body(body)
    return [layer for layer in PLANETARY_MAP_LAYERS if layer.body == body]


def maps_payload(
    body: str, *, science_coverage: dict[str, object], data_version: str
) -> dict[str, object]:
    info = get_body(body)
    layers = layers_for_body(body)
    return {
        "catalog_version": MAP_CATALOG_VERSION,
        "coordinate_contract_version": COORDINATE_CONTRACT_VERSION,
        "data_version": data_version,
        "body": info.to_payload(),
        "layers": [layer.to_payload() for layer in layers],
        "science_coverage": science_coverage,
        "coverage_separation": {
            "basemap_extent": "global (the official tile service covers the whole body)",
            "scientific_coverage": (
                "curated references/AOIs and typed observations only; not a global survey"
            ),
            "statement": (
                "'The map is globally browsable' is not 'the six-dimension science index covers "
                "the globe'. The API keeps these two claims separate."
            ),
        },
        "notes": [
            "Tiles are fetched directly from the official NASA Trek service; this API neither "
            "downloads nor redistributes global rasters.",
            "Check provider availability and terms before production use. The catalog records the "
            "verification date and sample tile.",
            "Use the planetary coordinate contract; do not pass these coordinates to Earth "
            "EPSG:4326 or OSM.",
        ],
    }
