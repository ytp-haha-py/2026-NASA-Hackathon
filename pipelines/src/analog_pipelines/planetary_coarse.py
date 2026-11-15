"""Bounded 5 km coarse terrain references from planetary body-fixed DEMs (Issue #35).

This module is deliberately separate from the Earth coarse pipeline:

* Earth coarse features (``coarse-terrain-250m-1.0.0``) aggregate a *published
  preprocessed slope product* at 7.5 arc-second support.
* These planetary values aggregate a Horn 3x3 slope that this repository computes
  from a planetary DEM at a 250 m analysis grid. The method version is therefore
  different (``coarse-planetary-dem-horn-1.0.0``), and the comparability gate never
  treats the two as interchangeable.

Frames are always planetary body-fixed frames. Earth WGS84/UTM CRSs are rejected, and
a source whose reference radius does not match the requested body is rejected too.
Every raster read is bounded: a local file or, only when explicitly enabled, a
``/vsicurl`` range read of a small window. A global planetary DEM is never downloaded.

No value is fabricated or zero-filled. Missing source pixels stay missing, and a cell
whose valid slope fraction is below the coarse threshold is reported as failed rather
than filled.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
from pyproj import CRS, Transformer
from rasterio.enums import Resampling
from rasterio.transform import Affine, from_origin
from rasterio.warp import reproject
from rasterio.windows import Window, from_bounds

from analog_pipelines.coarse_features import (
    COARSE_FEATURE_BY_ID,
    COARSE_METHOD_VERSION,
    CoarseExtractionError,
    extract_coarse_features,
)
from analog_pipelines.coarse_grid import GRID_VERSION, WINDOW_SIZE_M
from analog_pipelines.features import horn_slope_degrees

PLANETARY_COARSE_METHOD_VERSION = "coarse-planetary-dem-horn-1.0.0"
PLANETARY_COARSE_SCHEMA_VERSION = "planetary-coarse-reference-1.0.0"
PLANETARY_GRID_VERSION = "planet-coarse-grid-1.0.0"

EARTH_COARSE_SOURCE_NOTE = (
    "Geomorpho250 (PANGAEA 899135) dtm_slope_merit.dem_m_250m, a published "
    "7.5 arc-second preprocessed slope product; the Earth pipeline never computes a "
    "slope derivative in this repository."
)
EARTH_COARSE_SOURCE_ARCSEC = 7.5
EARTH_EQUATORIAL_METRES_PER_ARCSEC = 2.0 * math.pi * 6_378_137.0 / (360.0 * 3600.0)
EARTH_COARSE_NOMINAL_RESOLUTION_M = EARTH_EQUATORIAL_METRES_PER_ARCSEC * EARTH_COARSE_SOURCE_ARCSEC

ANALYSIS_RESOLUTION_M = 250.0
DEFAULT_MAX_PIXELS = 1_000_000
DEFAULT_PAD_PIXELS = 1
DECISION_COMPARABLE = "comparable"
DECISION_EVIDENCE_ONLY = "evidence_only"
DECISION_NOT_COMPARABLE = "not_comparable"
DECISION_NOT_SUPPORTED = "not_supported"
DECISIONS = (
    DECISION_COMPARABLE,
    DECISION_EVIDENCE_ONLY,
    DECISION_NOT_COMPARABLE,
    DECISION_NOT_SUPPORTED,
)
COARSE_FEATURE_IDS = tuple(COARSE_FEATURE_BY_ID)

# IAU 2000 / mission reference spheres used by the verified products. The Moon value
# is the PGDA LDEM reference radius; Mars keeps both the MOLA 3396.0 km sphere and the
# Mars_2000_Sphere 3396.190 km sphere used by the USGS CTX mosaic.
BODY_SPHERE_RADIUS_M: dict[str, float] = {"Moon": 1_737_400.0, "Mars": 3_396_000.0}
BODY_RADIUS_ALIASES_M: dict[str, tuple[float, ...]] = {
    "Moon": (1_737_400.0,),
    "Mars": (3_396_000.0, 3_396_190.0),
}
BODY_RADIUS_TOLERANCE_M = 5_000.0
EARTH_RADIUS_M = (6_378_137.0, 6_371_000.0, 6_371_008.8, 6_367_449.0)
EARTH_EPSG_CODES = {4326, 3857, 4269, 4258}
EARTH_UTM_RANGE = range(32601, 32761)

_METRE_UNITS = {"m", "metre", "meter", "metres", "meters"}


class PlanetaryCoarseError(RuntimeError):
    """A bounded planetary coarse operation could not be performed honestly."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class PlanetaryCrsError(PlanetaryCoarseError):
    pass


@dataclass(frozen=True, slots=True)
class PlanetaryDemSource:
    """A verified public planetary DEM and the facts needed to read it."""

    source_id: str
    body: str
    dataset_id: str
    provider: str
    product_id: str
    product_lid: str | None
    title: str
    url: str
    raster_url: str
    product_page: str
    license: str
    license_url: str | None
    citation: str
    doi: str | None
    frame: str
    coordinate_system: str
    vertical_reference: str
    native_ppd: float | None
    native_resolution_m: float
    vertical_unit: str
    vertical_unit_status: str
    vertical_scale_to_m: float
    expected_nodata: float | None
    expected_scale: float
    expected_offset: float
    same_support_as_earth: bool
    native_support_note: str
    notes: str
    quality_flags: tuple[str, ...] = ()

    def to_payload(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "body": self.body,
            "dataset_id": self.dataset_id,
            "provider": self.provider,
            "product_id": self.product_id,
            "product_lid": self.product_lid,
            "title": self.title,
            "url": self.url,
            "raster_url": self.raster_url,
            "product_page": self.product_page,
            "license": self.license,
            "license_url": self.license_url,
            "citation": self.citation,
            "doi": self.doi,
            "frame": self.frame,
            "coordinate_system": self.coordinate_system,
            "vertical_reference": self.vertical_reference,
            "native_ppd": self.native_ppd,
            "native_resolution_m": self.native_resolution_m,
            "vertical_unit": self.vertical_unit,
            "vertical_unit_status": self.vertical_unit_status,
            "vertical_scale_to_m": self.vertical_scale_to_m,
            "same_support_as_earth": self.same_support_as_earth,
            "native_support_note": self.native_support_note,
            "notes": self.notes,
            "quality_flags": list(self.quality_flags),
        }


# Verified on 2026-11-15 from the public product page, the GeoTIFF header and a
# bounded read. The global object is 2,705,286,892 bytes and is never downloaded.
MOON_LOLA128 = PlanetaryDemSource(
    source_id="moon-lola128-pa-pixel-202405",
    body="Moon",
    dataset_id="nasa-pgda-lola-moon-pa-ldem128-202405",
    provider="NASA GSFC Planetary Geodesy Data Archive (PGDA) / LRO-LOLA",
    product_id="LDEM128_PA_pixel_202405",
    product_lid=None,
    title="LOLA MOON_PA global shape model, 128 pixels/degree, pixel-registered",
    url="https://pgda.gsfc.nasa.gov/data/LOLA_PA/LDEM128_PA_pixel_202405.tif",
    raster_url="https://pgda.gsfc.nasa.gov/data/LOLA_PA/LDEM128_PA_pixel_202405.tif",
    product_page="https://pgda.gsfc.nasa.gov/products/95",
    license=(
        "not stated on the PGDA product page; public NASA archive with a citation "
        "requirement (Data Usage Policy)"
    ),
    license_url="https://science.data.nasa.gov/about/license",
    citation=(
        "Neumann, G. (2024). LOLA MOON_PA gridded dataset [Data set]. NASA Goddard "
        "Space Flight Center Planetary Geodesy Data Archive. doi:10.60903/LOLA_PA"
    ),
    doi="10.60903/LOLA_PA",
    frame="MOON_PA (DE421), body-fixed",
    coordinate_system="planetocentric, east-positive, geographic 0..360 longitude",
    vertical_reference="lunar surface radius relative to the 1737.4 km reference sphere",
    native_ppd=128.0,
    native_resolution_m=2.0 * math.pi * 1_737_400.0 / (360.0 * 128.0),
    vertical_unit="metre",
    vertical_unit_status=(
        "inferred from pixel magnitude; no vertical unit tag and the band actual_range "
        "tag is inherited in kilometres"
    ),
    vertical_scale_to_m=1.0,
    expected_nodata=None,
    expected_scale=1.0,
    expected_offset=0.0,
    same_support_as_earth=True,
    native_support_note=(
        "128 pixels/degree is 0.0078125 degrees, 236.90 m at the equator; the analysis "
        "grid is a fixed 250 m metric grid, so the source is ~2.1% coarser than the "
        "7.5 arc-second Earth coarse product at the equator."
    ),
    notes=(
        "Pixel-registered global GeoTIFF (46080 x 23040, float32, deflate, 256x256 tiles, "
        "nodata NaN). The product page states it was built by transforming SLDEM2015 "
        "256 ppd and the LOLA polar stereographic LDEMs to MOON_PA and averaging them at "
        "64/128 ppd; it is a derived, partly interpolated shape model, not raw altimetry. "
        "Full object size 2,705,286,892 bytes (HTTP Content-Length). The GeoTIFF has no "
        "explicit vertical unit in its TIFF tags: sampled pixel values span about "
        "-2.6e3 to +2.8e3 and match the published lunar elevation range (-9127 to "
        "+10779 m), while the band actual_range tag (-9.127 to 10.779) is inherited in "
        "kilometres from the sibling .grd. Pixels are therefore read as metres with "
        "scales=(1.0,) and offsets=(0.0,); the unit is inferred from pixel magnitude and "
        "flagged, not label-declared. During the 2026-11-15 retrieval, south-polar tiles "
        "near longitude 202-203 degrees returned TIFFReadEncodedTile errors; the pilot "
        "window at 20E, 10S is unaffected."
    ),
    quality_flags=(
        "global_shape_model",
        "derived_from_sldem2015_and_polar_ldems",
        "partly_interpolated",
        "pixel_registered_geographic",
        "moon_pa_frame",
        "vertical_unit_inferred_from_pixel_magnitude",
        "bounded_vsicurl_window",
    ),
)

# Verified on 2026-11-15 from the PDS4 bundle/collection/product labels and the PDS3
# label retained in the PDS4 bundle. Native support is 463.0576672 m/pixel, NOT 250 m.
MARS_MOLA_MEG128 = PlanetaryDemSource(
    source_id="mars-mola-megdr-meg128-t44n000",
    body="Mars",
    dataset_id="pds-mgs-mola-megdr-meg128",
    provider="NASA PDS Geosciences Node / MGS MOLA",
    product_id="megt44n000hb",
    product_lid="urn:nasa:pds:mgs_mola_topography_derived:data_meg128:megt44n000hb",
    title="MGS MOLA MEGDR median topography, 128 pixels/degree, 0-44N / 0-90E tile",
    url=(
        "https://pds-geosciences.wustl.edu/mgs/mgs-m-mola-5-megdr-l3-v1/"
        "mgsl_300x/meg128/megt44n000hb.lbl"
    ),
    raster_url=(
        "https://pds-geosciences.wustl.edu/mgs/urn-nasa-pds-mgs_mola_topography_derived/"
        "meg128/megt44n000hb.img"
    ),
    product_page=(
        "https://pds-geosciences.wustl.edu/mgs/urn-nasa-pds-mgs_mola_topography_derived/"
    ),
    license="not stated in the PDS4 bundle, collection or product labels",
    license_url=None,
    citation=(
        "Smith, D. E., et al. (2001), Mars Orbiter Laser Altimeter: Experiment summary "
        "after the first year of global mapping of Mars, J. Geophys. Res. 106(E10), "
        "23689-23722. PDS bundle urn:nasa:pds:mgs_mola_topography_derived (1.0), "
        "collection data_meg128 (2.0), DOI 10.17189/1z1b-kv84."
    ),
    doi="10.17189/1z1b-kv84",
    frame="IAU2000 Mars body-fixed, planetocentric",
    coordinate_system="equirectangular, east-positive, planetocentric latitude",
    vertical_reference=(
        "planetary radius minus the GMM3 (mgm1025) areoid, evaluated to degree and order 50"
    ),
    native_ppd=128.0,
    native_resolution_m=463.0576672,
    vertical_unit="metre",
    vertical_unit_status="label declared: PDS4 <unit>meter</unit>",
    vertical_scale_to_m=1.0,
    expected_nodata=-32768.0,
    expected_scale=1.0,
    expected_offset=0.0,
    same_support_as_earth=False,
    native_support_note=(
        "PDS4 pixel_resolution is 0.4630576672 km/pixel (128 pixel/degree), 1.85x "
        "coarser than the 250 m coarse analysis grid and 2.0x coarser than the "
        "7.5 arc-second (231.9 m) Earth coarse support at the equator."
    ),
    notes=(
        "PDS4 product label: SignedMSB2, 5632 lines x 11520 samples, unit meter, "
        "median observed topography within each 0.0078125-degree bin, with an "
        "interpolated value supplied where no MOLA observation falls in the bin. The "
        "PDS3 label retained in the bundle gives MAP_SCALE 0.463 km and A_AXIS_RADIUS "
        "3396.0 km. The PDS3 label path is used for GDAL raster I/O because GDAL has a "
        "PDS3 driver; the numeric metadata is taken from the PDS4 product label."
    ),
    quality_flags=(
        "global_median_binned_grid",
        "interpolated_bins_where_no_shot",
        "planetocentric",
        "native_support_463m",
        "not_same_support_as_earth_coarse",
        "bounded_vsicurl_window",
    ),
)

# Verified on 2026-11-15 from the USGS product page and the GeoTIFF header. This is the
# same product that backs the five existing 90 m Mars references, but the coarse pilot
# reads its own bounded windows and never reuses the 90 m reference values.
MARS_CTX_JEZERO = PlanetaryDemSource(
    source_id="mars2020-jezero-ctx-dem-20m",
    body="Mars",
    dataset_id="mars2020-jezero-ctx-dem-20m",
    provider="USGS Astrogeology Science Center / NASA Mars Missions",
    product_id="M20_JezeroCrater_CTXDEM_20m",
    product_lid=None,
    title="Mars 2020 Science Investigation CTX DEM Mosaic",
    url=(
        "https://planetarymaps.usgs.gov/mosaic/mars2020_trn/CTX/"
        "ScienceInvestigationMaps_JPL/M20_JezeroCrater_CTXDEM_20m.tif"
    ),
    raster_url=(
        "https://asc-pds-services.s3.us-west-2.amazonaws.com/mosaic/mars2020_trn/CTX/"
        "ScienceInvestigationMaps_JPL/M20_JezeroCrater_CTXDEM_20m.tif"
    ),
    product_page=(
        "https://astrogeology.usgs.gov/search/map/mars_2020_science_investigation_ctx_dem_mosaic"
    ),
    license="public domain",
    license_url=(
        "https://astrogeology.usgs.gov/search/map/mars_2020_science_investigation_ctx_dem_mosaic"
    ),
    citation=(
        "USGS Astrogeology Science Center, Mars 2020 Science Investigation CTX DEM "
        "Mosaic, M20_JezeroCrater_CTXDEM_20m."
    ),
    doi=None,
    frame="Mars_2000_Sphere (IAU2000), body-fixed",
    coordinate_system="Jezero_Crater_Projection equirectangular, planetocentric, metres",
    vertical_reference="stereo-derived elevations localized vertically to MOLA",
    native_ppd=None,
    native_resolution_m=20.0,
    vertical_unit="metre",
    vertical_unit_status=(
        "declared by the existing verified USGS manifest and product documentation; "
        "no GeoTIFF unit tag"
    ),
    vertical_scale_to_m=1.0,
    expected_nodata=-3.4028234663852886e38,
    expected_scale=1.0,
    expected_offset=0.0,
    same_support_as_earth=True,
    native_support_note=(
        "20 m stereo DEM aggregated to the fixed 250 m analysis grid; the source is "
        "finer than the 7.5 arc-second (231.9 m) Earth coarse support, so support is "
        "comparable, but the 20 m texture is not an independent 250 m measurement."
    ),
    notes=(
        "90,345,300-byte striped GeoTIFF (4456 x 5067, float32, no compression), "
        "Mars_2000_Sphere radius 3396190 m, Jezero Crater equirectangular analysis CRS. "
        "The product page states public domain and DEMs localized vertically to MOLA. "
        "Per-window vertical uncertainty and mosaic seams are not validated here. The "
        "existing five 90 m Mars references are unchanged; this pilot reads separate "
        "bounded windows and uses a separate coarse method version."
    ),
    quality_flags=(
        "stereo_dem_mosaic",
        "public_domain",
        "mars_2000_sphere",
        "vertical_uncertainty_not_verified",
        "bounded_vsicurl_window",
    ),
)

PLANETARY_SOURCES: dict[str, PlanetaryDemSource] = {
    source.source_id: source for source in (MOON_LOLA128, MARS_MOLA_MEG128, MARS_CTX_JEZERO)
}


@dataclass(frozen=True, slots=True)
class PlanetaryRasterMetadata:
    path: str
    crs_wkt: str
    crs_name: str
    body: str
    width: int
    height: int
    dtype: str
    nodata: float | None
    scale: float
    offset: float
    resolution: tuple[float, float]
    resolution_unit: str
    native_resolution_m: float
    tiled: bool
    block_shape: tuple[int, int]
    size_bytes: int | None


@dataclass(frozen=True, slots=True)
class PlanetaryMetricWindow:
    data: np.ndarray
    valid_mask: np.ndarray
    transform: Affine
    crs: CRS
    bbox_metric: dict[str, float]
    bbox_body_lonlat: dict[str, float]
    source_window: Window
    source_window_shape: tuple[int, int]
    source_window_pixels: int
    source_read_bytes_upper_bound: int
    metadata: PlanetaryRasterMetadata


@dataclass(frozen=True, slots=True)
class CoarseCellResult:
    candidate_id: str
    body: str
    source_id: str
    region_id: str
    row: int
    col: int
    crs_wkt: str
    crs_name: str
    bbox_metric: dict[str, float]
    bbox_body_lonlat: dict[str, float]
    analysis_resolution_m: float
    source_resolution_m: float
    window_size_m: float
    grid_shape: tuple[int, int]
    valid_slope_pixels: int
    missing_slope_pixels: int
    valid_fraction: float
    features: dict[str, float]
    elevation_stats: dict[str, float]
    fixture: np.ndarray
    fixture_transform: Affine
    fixture_crs: CRS
    fixture_bbox_metric: dict[str, float]
    pad_pixels: int
    source_window_shape: tuple[int, int]
    source_window_pixels: int
    source_read_bytes_upper_bound: int
    qa_flags: tuple[str, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# CRS and body validation
# ---------------------------------------------------------------------------


def _ellipsoid_radius(crs: CRS) -> float | None:
    try:
        radius = crs.ellipsoid.semi_major_metre
    except Exception:  # noqa: BLE001 - pyproj raises several types for unnamed CRSs
        return None
    if radius is None or not math.isfinite(radius) or radius <= 0:
        return None
    return float(radius)


def classify_body_from_crs(crs: CRS) -> str | None:
    """Classify a CRS by its reference radius; Earth is always classified first."""
    radius = _ellipsoid_radius(crs)
    if radius is None:
        return None
    if any(abs(radius - earth) <= 100.0 for earth in EARTH_RADIUS_M):
        return "Earth"
    for body, aliases in BODY_RADIUS_ALIASES_M.items():
        if any(abs(radius - alias) <= BODY_RADIUS_TOLERANCE_M for alias in aliases):
            return body
    return None


def assert_planetary_crs(crs: CRS, *, body: str) -> None:
    """Reject Earth CRSs, unknown bodies, and a CRS for the wrong body."""
    if body not in BODY_SPHERE_RADIUS_M:
        raise PlanetaryCrsError("unsupported_body", f"unsupported body {body!r}")
    if crs.to_epsg() in EARTH_EPSG_CODES or crs.to_epsg() in EARTH_UTM_RANGE:
        raise PlanetaryCrsError(
            "earth_crs_not_allowed",
            f"{crs.to_string()} is an Earth CRS; planetary work must use a body-fixed frame",
        )
    detected = classify_body_from_crs(crs)
    if detected == "Earth":
        raise PlanetaryCrsError(
            "earth_crs_not_allowed",
            f"{crs.to_string()} uses an Earth reference radius; WGS84/UTM is never valid here",
        )
    if detected is None:
        raise PlanetaryCrsError(
            "unrecognized_planetary_crs",
            f"{crs.to_string()} has no reference radius matching Moon or Mars",
        )
    if detected != body:
        raise PlanetaryCrsError(
            "body_mismatch", f"CRS belongs to {detected}, not the requested {body}"
        )


def assert_metric_target_crs(crs: CRS, *, body: str) -> None:
    assert_planetary_crs(crs, body=body)
    if not crs.is_projected:
        raise PlanetaryCrsError(
            "target_crs_not_projected",
            "the 5 km analysis grid needs a projected body-fixed metric CRS",
        )
    unit = crs.axis_info[0].unit_name.lower() if crs.axis_info else ""
    if unit not in _METRE_UNITS:
        raise PlanetaryCrsError("target_crs_not_metric", f"target CRS unit is {unit!r}")


def body_geographic_crs(body: str, *, radius_m: float | None = None) -> CRS:
    if body not in BODY_SPHERE_RADIUS_M:
        raise PlanetaryCrsError("unsupported_body", f"unsupported body {body!r}")
    radius = BODY_SPHERE_RADIUS_M[body] if radius_m is None else float(radius_m)
    return CRS.from_proj4(f"+proj=longlat +R={radius} +no_defs")


@contextmanager
def _planetary_gdal_env() -> Iterator[None]:
    """Allow pyproj/GDAL to work with non-Earth bodies and bounded remote reads."""
    os.environ.setdefault("PROJ_IGNORE_CELESTIAL_BODY", "YES")
    with rasterio.Env(
        GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
        PROJ_IGNORE_CELESTIAL_BODY="YES",
        GDAL_HTTP_MAX_RETRY="2",
        GDAL_HTTP_RETRY_DELAY="1",
    ):
        yield


def _source_reference(path: str | Path, *, allow_remote: bool) -> str:
    raw = str(path)
    lowered = raw.lower()
    if lowered.startswith(("http://", "https://")):
        if not allow_remote:
            raise PlanetaryCoarseError(
                "remote_read_disabled",
                "remote planetary DEM reads are opt-in; pass allow_remote=True for a "
                "bounded /vsicurl window",
            )
        if lowered.startswith("http://"):
            raise PlanetaryCoarseError("insecure_remote_read", "only HTTPS range reads are allowed")
        return "/vsicurl/" + raw
    if lowered.startswith(("s3://", "gs://", "ftp://")):
        raise PlanetaryCoarseError(
            "unsupported_remote_scheme", f"unsupported source scheme in {raw!r}"
        )
    resolved = Path(path).expanduser()
    if not resolved.is_file():
        raise PlanetaryCoarseError("source_not_found", f"DEM file not found: {resolved}")
    return str(resolved)


def _resolution_in_metres(crs: CRS, resolution: tuple[float, float]) -> float:
    xres = abs(float(resolution[0]))
    yres = abs(float(resolution[1]))
    radius = _ellipsoid_radius(crs)
    if radius is None:
        raise PlanetaryCrsError("unrecognized_planetary_crs", "CRS has no reference radius")
    if crs.is_geographic:
        x_m = math.radians(xres) * radius
        y_m = math.radians(yres) * radius
    else:
        unit = crs.axis_info[0].unit_name.lower() if crs.axis_info else ""
        if unit not in _METRE_UNITS:
            raise PlanetaryCrsError(
                "source_not_metric", f"projected source CRS unit is {unit!r}, expected metres"
            )
        x_m, y_m = xres, yres
    if not (math.isclose(x_m, y_m, rel_tol=1e-6)):
        raise PlanetaryCoarseError(
            "anisotropic_source",
            f"source pixels are anisotropic ({x_m:.4f} m x {y_m:.4f} m); a single "
            "metric Horn cell size would be untruthful",
        )
    return x_m


def _read_scale_offset(src: rasterio.io.DatasetReader) -> tuple[float, float]:
    scales = list(src.scales or [])
    offsets = list(src.offsets or [])
    scale = float(scales[0]) if scales and scales[0] is not None else 1.0
    offset = float(offsets[0]) if offsets and offsets[0] is not None else 0.0
    if not math.isfinite(scale) or scale == 0:
        raise PlanetaryCoarseError(
            "invalid_scale", "raster scale is missing/zero; refusing to invent elevations"
        )
    if not math.isfinite(offset):
        raise PlanetaryCoarseError("invalid_offset", "raster offset is not finite")
    return scale, offset


def _nodata_matches(observed: float | None, expected: float | None) -> bool:
    if expected is None:
        return observed is None or (observed is not None and not math.isfinite(observed))
    if observed is None or not math.isfinite(observed):
        return False
    return math.isclose(observed, expected, rel_tol=1e-6)


def _metadata_from_open_source(
    src: rasterio.io.DatasetReader,
    *,
    reference: str,
    source: PlanetaryDemSource | None,
) -> PlanetaryRasterMetadata:
    if src.crs is None:
        raise PlanetaryCrsError("missing_crs", "planetary DEM has no CRS")
    crs = CRS.from_user_input(src.crs)
    body = source.body if source else classify_body_from_crs(crs)
    if body is None:
        raise PlanetaryCrsError(
            "unrecognized_planetary_crs", "cannot infer Moon/Mars from the CRS radius"
        )
    assert_planetary_crs(crs, body=body)
    scale, offset = _read_scale_offset(src)
    observed_nodata = float(src.nodata) if src.nodata is not None else None
    native_m = _resolution_in_metres(crs, (src.res[0], src.res[1]))
    if source is not None:
        if not _nodata_matches(observed_nodata, source.expected_nodata):
            raise PlanetaryCoarseError(
                "nodata_mismatch",
                f"observed nodata {observed_nodata!r} does not match the verified "
                f"value {source.expected_nodata!r}",
            )
        if not math.isclose(scale, source.expected_scale, rel_tol=1e-9) or not (
            math.isclose(offset, source.expected_offset, abs_tol=1e-9)
        ):
            raise PlanetaryCoarseError(
                "scale_offset_mismatch",
                f"observed scale/offset {scale}/{offset} does not match the verified "
                f"{source.expected_scale}/{source.expected_offset}",
            )
        if not math.isclose(native_m, source.native_resolution_m, rel_tol=0.015):
            raise PlanetaryCoarseError(
                "resolution_mismatch",
                f"native support {native_m:.4f} m does not match the verified "
                f"{source.native_resolution_m:.4f} m",
            )
    local = Path(reference)
    return PlanetaryRasterMetadata(
        path=reference,
        crs_wkt=crs.to_wkt(),
        crs_name=crs.name,
        body=body,
        width=src.width,
        height=src.height,
        dtype=src.dtypes[0],
        nodata=observed_nodata,
        scale=scale,
        offset=offset,
        resolution=(float(abs(src.res[0])), float(abs(src.res[1]))),
        resolution_unit=(crs.axis_info[0].unit_name if crs.axis_info else "unknown"),
        native_resolution_m=native_m,
        tiled=bool(src.is_tiled),
        block_shape=tuple(int(v) for v in src.block_shapes[0]),
        size_bytes=(local.stat().st_size if local.is_file() else None),
    )


def inspect_planetary_raster(
    path: str | Path,
    *,
    source: PlanetaryDemSource | None = None,
    allow_remote: bool = False,
) -> PlanetaryRasterMetadata:
    """Inspect a DEM and verify its body, native support, nodata, and scale/offset."""
    reference = _source_reference(path, allow_remote=allow_remote)
    with _planetary_gdal_env(), rasterio.open(reference) as src:
        return _metadata_from_open_source(src, reference=reference, source=source)


# ---------------------------------------------------------------------------
# Bounded window reads and reprojection
# ---------------------------------------------------------------------------


def _tiles_touched(window: Window, block_shape: tuple[int, int]) -> tuple[int, int]:
    block_x, block_y = block_shape
    first_col = int(math.floor(window.col_off / block_x))
    last_col = int(math.floor((window.col_off + window.width - 1) / block_x))
    first_row = int(math.floor(window.row_off / block_y))
    last_row = int(math.floor((window.row_off + window.height - 1) / block_y))
    return last_col - first_col + 1, last_row - first_row + 1


def _source_read_upper_bound(src: rasterio.io.DatasetReader, window: Window, itemsize: int) -> int:
    """Conservative bound on raster bytes touched by a window read.

    This is an upper bound, not a measurement: compressed tiles usually transfer
    fewer bytes. The PDS3 label/header read is budgeted separately as 64 KiB.
    """
    if src.is_tiled:
        block_y, block_x = int(src.block_shapes[0][0]), int(src.block_shapes[0][1])
        cols, rows = _tiles_touched(window, (block_x, block_y))
        data_bytes = cols * rows * block_x * block_y * itemsize
    else:
        data_bytes = int(src.width) * int(window.height) * itemsize
    return int(data_bytes) + 64 * 1024


def _source_window_for_bbox(
    src: rasterio.io.DatasetReader,
    bbox: Mapping[str, float],
    *,
    pad_pixels: int,
) -> Window:
    transform = src.transform
    corners = [
        (bbox["west"], bbox["south"]),
        (bbox["east"], bbox["south"]),
        (bbox["west"], bbox["north"]),
        (bbox["east"], bbox["north"]),
    ]
    xs = [point[0] for point in corners]
    ys = [point[1] for point in corners]
    xpad = pad_pixels * abs(float(transform.a))
    ypad = pad_pixels * abs(float(transform.e))
    window = (
        from_bounds(min(xs) - xpad, min(ys) - ypad, max(xs) + xpad, max(ys) + ypad, transform)
        .round_offsets()
        .round_lengths()
    )
    if window.width <= 0 or window.height <= 0:
        raise PlanetaryCoarseError(
            "window_outside_source", "requested cell does not intersect the source DEM"
        )
    if (
        window.col_off >= src.width
        or window.row_off >= src.height
        or window.col_off + window.width <= 0
        or window.row_off + window.height <= 0
    ):
        raise PlanetaryCoarseError(
            "window_outside_source", "requested cell lies outside the source DEM"
        )
    return window


def _clamp_window(window: Window, src: rasterio.io.DatasetReader) -> Window:
    col_off = max(0, int(window.col_off))
    row_off = max(0, int(window.row_off))
    width = min(int(window.width), src.width - col_off)
    height = min(int(window.height), src.height - row_off)
    if width <= 0 or height <= 0:
        raise PlanetaryCoarseError(
            "window_outside_source", "requested cell window has no source pixels"
        )
    return Window(col_off, row_off, width, height)


def read_metric_window(
    path: str | Path,
    *,
    source: PlanetaryDemSource,
    bbox_metric: Mapping[str, float],
    target_crs: CRS | str,
    resolution_m: float = ANALYSIS_RESOLUTION_M,
    pad_pixels: int = DEFAULT_PAD_PIXELS,
    max_pixels: int = DEFAULT_MAX_PIXELS,
    allow_remote: bool = False,
) -> PlanetaryMetricWindow:
    """Read a bounded window, retrying transient remote range-read failures.

    A retry reopens the dataset and repeats exactly the same bounded window request; it
    never enlarges the window and never fills missing data. Local reads get one attempt.
    """
    attempts = 3 if allow_remote else 1
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            return _read_metric_window_once(
                path,
                source=source,
                bbox_metric=bbox_metric,
                target_crs=target_crs,
                resolution_m=resolution_m,
                pad_pixels=pad_pixels,
                max_pixels=max_pixels,
                allow_remote=allow_remote,
            )
        except rasterio.errors.RasterioIOError as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(1.0 * (attempt + 1))
    raise PlanetaryCoarseError(
        "source_read_failed",
        f"bounded planetary DEM read failed after {attempts} attempt(s): {last_error}",
    ) from last_error


def _read_metric_window_once(
    path: str | Path,
    *,
    source: PlanetaryDemSource,
    bbox_metric: Mapping[str, float],
    target_crs: CRS | str,
    resolution_m: float = ANALYSIS_RESOLUTION_M,
    pad_pixels: int = DEFAULT_PAD_PIXELS,
    max_pixels: int = DEFAULT_MAX_PIXELS,
    allow_remote: bool = False,
) -> PlanetaryMetricWindow:
    """Read a bounded planetary DEM window and reproject it to a metric analysis grid.

    ``bbox_metric`` is the exact analysis footprint in ``target_crs``. ``pad_pixels``
    only adds source pixels to the I/O envelope for stable edge interpolation; the
    returned grid still covers exactly ``bbox_metric``. Callers that need a Horn stencil
    around every reported pixel must expand ``bbox_metric`` (as ``coarse_cell_from_dem``
    does). The function never wraps a whole global DEM.
    """
    dst_crs = CRS.from_user_input(target_crs)
    assert_metric_target_crs(dst_crs, body=source.body)
    if not math.isfinite(resolution_m) or resolution_m <= 0:
        raise PlanetaryCoarseError("invalid_resolution", "resolution_m must be positive")
    west, south = float(bbox_metric["west"]), float(bbox_metric["south"])
    east, north = float(bbox_metric["east"]), float(bbox_metric["north"])
    if not (east > west and north > south):
        raise PlanetaryCoarseError("invalid_bbox", "metric bbox must have positive extent")
    cell_x = (east - west) / max(1, round((east - west) / resolution_m))
    cell_y = (north - south) / max(1, round((north - south) / resolution_m))
    if not (
        math.isclose(cell_x, resolution_m, rel_tol=1e-9)
        and math.isclose(cell_y, resolution_m, rel_tol=1e-9)
    ):
        raise PlanetaryCoarseError(
            "grid_mismatch",
            "metric bbox is not an integer multiple of the requested analysis resolution",
        )
    nx = max(1, round((east - west) / resolution_m))
    ny = max(1, round((north - south) / resolution_m))
    if nx * ny > max_pixels:
        raise PlanetaryCoarseError(
            "window_too_large", f"analysis window has {nx * ny} pixels; max_pixels={max_pixels}"
        )
    dst_transform = from_origin(west, north, (east - west) / nx, (north - south) / ny)

    reference = _source_reference(path, allow_remote=allow_remote)
    with _planetary_gdal_env(), rasterio.open(reference) as src:
        if src.crs is None:
            raise PlanetaryCrsError("missing_crs", "planetary DEM has no CRS")
        src_crs = CRS.from_user_input(src.crs)
        assert_planetary_crs(src_crs, body=source.body)
        scale, offset = _read_scale_offset(src)
        if not math.isclose(scale, source.expected_scale, rel_tol=1e-9) or not (
            math.isclose(offset, source.expected_offset, abs_tol=1e-9)
        ):
            raise PlanetaryCoarseError(
                "scale_offset_mismatch",
                "source scale/offset changed; re-verify the product before reading",
            )
        to_source = Transformer.from_crs(dst_crs, src_crs, always_xy=True)
        corners = [
            to_source.transform(west, south),
            to_source.transform(east, south),
            to_source.transform(west, north),
            to_source.transform(east, north),
        ]
        source_bbox = {
            "west": min(point[0] for point in corners),
            "south": min(point[1] for point in corners),
            "east": max(point[0] for point in corners),
            "north": max(point[1] for point in corners),
        }
        window = _clamp_window(_source_window_for_bbox(src, source_bbox, pad_pixels=2), src)
        if window.width * window.height > max_pixels:
            raise PlanetaryCoarseError(
                "source_window_too_large",
                f"source window has {int(window.width * window.height)} pixels; "
                f"max_pixels={max_pixels}",
            )
        itemsize = np.dtype(src.dtypes[0]).itemsize
        read_bound = _source_read_upper_bound(src, window, itemsize)
        nodata = src.nodata
        masked = src.read(
            1,
            window=window,
            masked=True,
            boundless=False,
            out_dtype="float64",
        )
        array = np.asarray(masked.filled(np.nan), dtype="float64")
        valid = np.isfinite(array)
        if nodata is not None:
            valid &= array != float(nodata)
        array = array * scale + offset
        array = array * source.vertical_scale_to_m
        array = np.where(valid, array, np.nan)
        if not np.isfinite(array[valid]).all():
            raise PlanetaryCoarseError(
                "nonfinite_elevation", "source produced non-finite elevations"
            )
        destination = np.full((ny, nx), np.nan, dtype="float64")
        reproject(
            source=array,
            destination=destination,
            src_transform=src.window_transform(window),
            src_crs=src_crs,
            src_nodata=np.nan,
            dst_transform=dst_transform,
            dst_crs=dst_crs,
            dst_nodata=np.nan,
            resampling=Resampling.bilinear,
        )
        to_geographic = Transformer.from_crs(dst_crs, body_geographic_crs(source.body))
        geo_corners = [
            to_geographic.transform(west, south),
            to_geographic.transform(east, south),
            to_geographic.transform(west, north),
            to_geographic.transform(east, north),
        ]
        lons = [point[0] for point in geo_corners]
        lats = [point[1] for point in geo_corners]
        metadata = _metadata_from_open_source(src, reference=reference, source=source)
        return PlanetaryMetricWindow(
            data=destination,
            valid_mask=np.isfinite(destination),
            transform=dst_transform,
            crs=dst_crs,
            bbox_metric={"west": west, "south": south, "east": east, "north": north},
            bbox_body_lonlat={
                "west": min(lons),
                "south": min(lats),
                "east": max(lons),
                "north": max(lats),
            },
            source_window=window,
            source_window_shape=(int(window.height), int(window.width)),
            source_window_pixels=int(window.width * window.height),
            source_read_bytes_upper_bound=read_bound,
            metadata=metadata,
        )


# ---------------------------------------------------------------------------
# Coarse cell computation and comparability
# ---------------------------------------------------------------------------


def cell_bbox_from_center(
    center_xy: tuple[float, float], *, n_pixels: int, resolution_m: float
) -> dict[str, float]:
    if n_pixels < 2:
        raise PlanetaryCoarseError("invalid_grid", "a coarse cell needs at least 2 pixels")
    half = n_pixels * resolution_m / 2.0
    return {
        "west": float(center_xy[0]) - half,
        "south": float(center_xy[1]) - half,
        "east": float(center_xy[0]) + half,
        "north": float(center_xy[1]) + half,
    }


def _candidate_id(
    *, source_id: str, crs_wkt: str, bbox_metric: Mapping[str, float], window_size_m: float
) -> str:
    identity = (
        f"{PLANETARY_GRID_VERSION}|{source_id}|{crs_wkt}|"
        f"{bbox_metric['west']:.6f}|{bbox_metric['south']:.6f}|{window_size_m:.6f}"
    )
    return "planet-coarse-v1:" + hashlib.sha256(identity.encode()).hexdigest()[:16]


def coarse_cell_from_dem(
    path: str | Path,
    *,
    source: PlanetaryDemSource,
    region_id: str,
    row: int,
    col: int,
    target_crs: CRS | str,
    center_xy: tuple[float, float],
    n_pixels: int,
    resolution_m: float = ANALYSIS_RESOLUTION_M,
    window_size_m: float | None = None,
    pad_pixels: int = DEFAULT_PAD_PIXELS,
    allow_remote: bool = False,
) -> CoarseCellResult:
    """Compute one 5 km coarse cell from a bounded planetary DEM window.

    The slope is the shared Horn 3x3 implementation on the reprojected elevation grid.
    The central ``n_pixels`` square is aggregated with the shared coarse aggregator;
    the padded rim exists only to complete the Horn stencil.
    """
    if pad_pixels < 1:
        raise PlanetaryCoarseError(
            "invalid_padding", "at least one padded pixel is required for a complete stencil"
        )
    dst_crs = CRS.from_user_input(target_crs)
    assert_metric_target_crs(dst_crs, body=source.body)
    window_m = n_pixels * resolution_m if window_size_m is None else float(window_size_m)
    if not math.isclose(window_m, n_pixels * resolution_m, rel_tol=1e-9):
        raise PlanetaryCoarseError(
            "invalid_window", "window_size_m must equal n_pixels * resolution_m"
        )
    core_bbox = cell_bbox_from_center(center_xy, n_pixels=n_pixels, resolution_m=resolution_m)
    padded = pad_pixels * resolution_m
    padded_bbox = {
        "west": core_bbox["west"] - padded,
        "south": core_bbox["south"] - padded,
        "east": core_bbox["east"] + padded,
        "north": core_bbox["north"] + padded,
    }
    window = read_metric_window(
        path,
        source=source,
        bbox_metric=padded_bbox,
        target_crs=dst_crs,
        resolution_m=resolution_m,
        pad_pixels=0,
        allow_remote=allow_remote,
    )
    if np.isfinite(window.data).sum() == 0:
        raise PlanetaryCoarseError("no_valid_data", "the padded cell window has no valid data")
    slope = horn_slope_degrees(window.data, resolution_m)
    core_slice = (slice(pad_pixels, -pad_pixels), slice(pad_pixels, -pad_pixels))
    core_slope = slope[core_slice]
    core_elevation = window.data[core_slice]
    if core_slope.shape != (n_pixels, n_pixels):
        raise PlanetaryCoarseError(
            "grid_mismatch",
            f"core slope shape {core_slope.shape} does not match {(n_pixels, n_pixels)}",
        )
    valid = np.isfinite(core_slope)
    valid_pixels = int(valid.sum())
    missing_pixels = int(core_slope.size - valid_pixels)
    try:
        features = extract_coarse_features(core_slope, valid)
    except CoarseExtractionError as exc:
        raise PlanetaryCoarseError(exc.code, exc.message) from exc
    elevation_valid = np.isfinite(core_elevation)
    if not elevation_valid.any():
        raise PlanetaryCoarseError("no_valid_data", "the cell core has no valid elevation")
    elevation_values = core_elevation[elevation_valid]
    elevation_stats = {
        "elevation_min_m": float(np.min(elevation_values)),
        "elevation_max_m": float(np.max(elevation_values)),
        "elevation_mean_m": float(np.mean(elevation_values)),
        "relief_p95_p05_m": float(
            np.percentile(elevation_values, 95) - np.percentile(elevation_values, 5)
        ),
        "valid_elevation_pixels": int(elevation_valid.sum()),
    }
    return CoarseCellResult(
        candidate_id=_candidate_id(
            source_id=source.source_id,
            crs_wkt=dst_crs.to_wkt(),
            bbox_metric=core_bbox,
            window_size_m=window_m,
        ),
        body=source.body,
        source_id=source.source_id,
        region_id=region_id,
        row=row,
        col=col,
        crs_wkt=dst_crs.to_wkt(),
        crs_name=dst_crs.name,
        bbox_metric=core_bbox,
        bbox_body_lonlat=_bbox_from_window(window, core_bbox, body=source.body),
        analysis_resolution_m=float(resolution_m),
        source_resolution_m=float(source.native_resolution_m),
        window_size_m=window_m,
        grid_shape=(n_pixels, n_pixels),
        valid_slope_pixels=valid_pixels,
        missing_slope_pixels=missing_pixels,
        valid_fraction=float(valid_pixels / core_slope.size),
        features=features,
        elevation_stats=elevation_stats,
        fixture=window.data,
        fixture_transform=window.transform,
        fixture_crs=window.crs,
        fixture_bbox_metric=padded_bbox,
        pad_pixels=pad_pixels,
        source_window_shape=window.source_window_shape,
        source_window_pixels=window.source_window_pixels,
        source_read_bytes_upper_bound=window.source_read_bytes_upper_bound,
        qa_flags=source.quality_flags,
    )


def _bbox_from_window(
    window: PlanetaryMetricWindow, bbox_metric: Mapping[str, float], *, body: str
) -> dict[str, float]:
    """Inverse-project the core bbox corners into the body-fixed geographic frame."""
    to_geographic = Transformer.from_crs(window.crs, body_geographic_crs(body))
    corners = [
        to_geographic.transform(bbox_metric["west"], bbox_metric["south"]),
        to_geographic.transform(bbox_metric["east"], bbox_metric["south"]),
        to_geographic.transform(bbox_metric["west"], bbox_metric["north"]),
        to_geographic.transform(bbox_metric["east"], bbox_metric["north"]),
    ]
    return {
        "west": min(point[0] for point in corners),
        "south": min(point[1] for point in corners),
        "east": max(point[0] for point in corners),
        "north": max(point[1] for point in corners),
    }


def support_ratio(source: PlanetaryDemSource) -> float:
    return source.native_resolution_m / EARTH_COARSE_NOMINAL_RESOLUTION_M


def comparability_decisions(source: PlanetaryDemSource) -> list[dict[str, Any]]:
    """Per-metric decision table. Never promotes a method/support difference silently."""
    decisions: list[dict[str, Any]] = []
    ratio = support_ratio(source)
    for feature_id in COARSE_FEATURE_IDS:
        definition = COARSE_FEATURE_BY_ID[feature_id]
        if source.source_id == MARS_MOLA_MEG128.source_id:
            decision = DECISION_NOT_COMPARABLE
            reason = (
                "MEGDR native support is 463.0576672 m/pixel (128 ppd), 1.85x coarser "
                "than the 250 m coarse analysis grid and 2.0x coarser than the 7.5 "
                "arc-second Earth coarse support; each 5 km cell contains only ~11 native "
                "pixels, and MEGDR supplies interpolated median values where no MOLA shot "
                "falls in a bin. A 250 m grid would upsample an interpolated product, not "
                "measure the same quantity at the same support."
            )
        elif source.same_support_as_earth:
            decision = DECISION_EVIDENCE_ONLY
            reason = (
                f"{feature_id} has the same nominal quantity and a 5 km footprint, but "
                "the Earth value comes from the published Geomorpho250 preprocessed slope "
                "product at 7.5 arc-second support, while this value comes from a Horn 3x3 "
                "derivative of a planetary DEM on a fixed 250 m grid. The product's "
                "preprocessing algorithm is not replicated, so method equivalence is not "
                f"established. Native support ratio vs Earth coarse = {ratio:.3f}."
            )
        else:
            decision = DECISION_NOT_COMPARABLE
            reason = (
                f"{feature_id} is not supported at the Earth coarse support; native "
                f"resolution is {source.native_resolution_m:.3f} m "
                f"(ratio {ratio:.3f} vs 7.5 arc-second Earth)."
            )
        decisions.append(
            {
                "feature_id": feature_id,
                "label": definition.label,
                "unit": definition.unit,
                "decision": decision,
                "reason": reason,
                "earth_value_source": "preprocessed Geomorpho250 slope product",
                "planetary_value_source": "Horn 3x3 derivative of a planetary DEM",
                "earth_support": "5 km window, 7.5 arc-second (~231.9 m at the equator)",
                "planetary_support": (
                    f"5 km window, {source.native_resolution_m:.3f} m native source, "
                    "fixed 250 m analysis grid"
                ),
                "support_ratio_vs_earth": ratio,
                "method_version": PLANETARY_COARSE_METHOD_VERSION,
                "earth_method_version": COARSE_METHOD_VERSION,
            }
        )
    return decisions


def cross_body_ranking_gate(decisions: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    blocking = sorted(
        {str(item["feature_id"]) for item in decisions if item["decision"] != DECISION_COMPARABLE}
    )
    enabled = not blocking
    return {
        "enabled": enabled,
        "blocking_features": blocking,
        "reason": (
            "Cross-body coarse ranking is disabled: no coarse metric is validated as the "
            "same quantity, method and support as the Earth coarse index. Evidence-only and "
            "not_comparable metrics must not be scored against Earth candidates."
            if not enabled
            else "All coarse metrics are validated as comparable."
        ),
        "claim_scope": "bounded_pilot_cells_only_not_a_global_survey",
    }


def method_calibration_note() -> dict[str, Any]:
    """State exactly which method difference was measured and which was not."""
    return {
        "earth_coarse_method_version": COARSE_METHOD_VERSION,
        "planetary_coarse_method_version": PLANETARY_COARSE_METHOD_VERSION,
        "earth_source": EARTH_COARSE_SOURCE_NOTE,
        "measured_on_mars": (
            "The Mars pilot computes the same Horn 3x3 + 5 km aggregation twice: once from "
            "the 20 m CTX DTM reprojected to a 250 m grid (comparable support) and once from "
            "the native 463.0576672 m MOLA MEGDR grid. The observed per-cell deltas are in "
            "the MOLA evidence manifest and quantify a support/interpolation effect, not an "
            "Earth calibration."
        ),
        "not_measured_against_earth": (
            "The Earth coarse pilot consumes a published slope product (PANGAEA 899135, "
            "7.5 arc-second). The source MERIT DEM and the product's preprocessing kernel "
            "are not part of the pinned snapshot, so computing an Earth Horn-at-250 m control "
            "would require downloading a global DEM, which this issue forbids. The "
            "preprocessed-product vs DEM-derivative difference against Earth is therefore "
            "unmeasured, and every slope-derived planetary metric is marked evidence_only."
        ),
        "unsupported_metrics": [
            "terrain_relief_p95_p05_m",
            "roughness_std_m",
            "curvature",
            "geomorphon_composition",
            "any 90 m fine metric",
        ],
        "unsupported_reason": (
            "The Earth coarse pilot defined only slope mean, slope p90, and flat fraction <5 "
            "degrees. Relief/roughness/curvature/geomorphon are not part of that coarse "
            "feature set, so no planetary coarse value is emitted for them."
        ),
    }


# ---------------------------------------------------------------------------
# Manifest writing and offline verification
# ---------------------------------------------------------------------------


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_elevation_fixture(
    path: str | Path,
    array: np.ndarray,
    *,
    transform: Affine,
    crs: CRS,
    path_label: str | None = None,
) -> dict[str, Any]:
    """Write a small compressed elevation fixture with the planetary CRS embedded.

    float64 preserves the exact reprojected values so the committed features can be
    recomputed offline without a float32 rounding difference.
    """
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    data = np.asarray(array, dtype="float64")
    with rasterio.open(
        destination,
        "w",
        driver="GTiff",
        height=data.shape[0],
        width=data.shape[1],
        count=1,
        dtype="float64",
        crs=crs,
        transform=transform,
        nodata=float("nan"),
        compress="deflate",
        tiled=False,
    ) as dst:
        dst.write(data, 1)
    return {
        "path": path_label if path_label is not None else destination.as_posix(),
        "sha256": _sha256_file(destination),
        "bytes": destination.stat().st_size,
        "shape": [int(data.shape[0]), int(data.shape[1])],
    }


def cell_payload(result: CoarseCellResult, fixture: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "candidate_id": result.candidate_id,
        "body": result.body,
        "source_id": result.source_id,
        "region_id": result.region_id,
        "row": result.row,
        "col": result.col,
        "bbox_metric": result.bbox_metric,
        "bbox_body_lonlat": result.bbox_body_lonlat,
        "crs_wkt": result.crs_wkt,
        "crs_name": result.crs_name,
        "analysis_resolution_m": result.analysis_resolution_m,
        "source_resolution_m": result.source_resolution_m,
        "window_size_m": result.window_size_m,
        "grid_shape": list(result.grid_shape),
        "valid_slope_pixels": result.valid_slope_pixels,
        "missing_slope_pixels": result.missing_slope_pixels,
        "valid_fraction": result.valid_fraction,
        "features": result.features,
        "elevation_stats": result.elevation_stats,
        "fixture": {
            **fixture,
            "crs_wkt": result.fixture_crs.to_wkt(),
            "transform": list(tuple(result.fixture_transform)[:6]),
            "bbox_metric": result.fixture_bbox_metric,
            "pad_pixels": result.pad_pixels,
        },
        "source_read": {
            "source_window_shape": list(result.source_window_shape),
            "source_window_pixels": result.source_window_pixels,
            "upper_bound_bytes": result.source_read_bytes_upper_bound,
            "note": (
                "Upper bound on raster bytes touched, including tiled/striped block "
                "alignment and a 64 KiB label/header allowance; not a network measurement."
            ),
        },
        "qa_flags": list(result.qa_flags),
    }


def build_manifest(
    *,
    source: PlanetaryDemSource,
    region: Mapping[str, Any],
    cells: Sequence[CoarseCellResult],
    fixture_payloads: Sequence[Mapping[str, Any]],
    generated_at: str,
    run: Mapping[str, Any] | None = None,
    limitations: Sequence[str] = (),
) -> dict[str, Any]:
    if len(cells) != len(fixture_payloads):
        raise PlanetaryCoarseError(
            "fixture_count_mismatch", "each cell needs exactly one elevation fixture"
        )
    payload_cells = [
        cell_payload(cell, fixture) for cell, fixture in zip(cells, fixture_payloads, strict=True)
    ]
    decisions = comparability_decisions(source)
    valid_pixels = sum(cell.valid_slope_pixels for cell in cells)
    missing_pixels = sum(cell.missing_slope_pixels for cell in cells)
    manifest = {
        "schema_version": PLANETARY_COARSE_SCHEMA_VERSION,
        "generated_at": generated_at,
        "data_kind": "observed_derived",
        "method": {
            "method_version": PLANETARY_COARSE_METHOD_VERSION,
            "grid_version": PLANETARY_GRID_VERSION,
            "earth_coarse_method_version": COARSE_METHOD_VERSION,
            "earth_coarse_grid_version": GRID_VERSION,
            "window_size_m": WINDOW_SIZE_M,
            "analysis_resolution_m": ANALYSIS_RESOLUTION_M,
            "slope_algorithm": "Horn 3x3 on the reprojected elevation grid",
            "aggregation": (
                "mean and linear p90 of valid Horn slope pixels, and fraction strictly "
                "below 5 degrees, over the central 5 km cell"
            ),
            "min_valid_fraction": 0.8,
            "vertical_scale_to_m": source.vertical_scale_to_m,
            "elevation_fixture": (
                "padded reprojected elevation in metres, committed so the slope and "
                "aggregation can be recomputed offline"
            ),
        },
        "source": source.to_payload(),
        "region": dict(region),
        "cells": payload_cells,
        "counts": {
            "requested_cells": len(cells),
            "computed_cells": len(cells),
            "failed_cells": 0,
            "valid_slope_pixels": valid_pixels,
            "missing_slope_pixels": missing_pixels,
            "valid_fraction": (
                float(valid_pixels / (valid_pixels + missing_pixels))
                if valid_pixels + missing_pixels
                else 0.0
            ),
        },
        "comparability": decisions,
        "cross_body_ranking": cross_body_ranking_gate(decisions),
        "method_calibration": method_calibration_note(),
        "run": dict(run or {}),
        "limitations": list(limitations),
    }
    return manifest


def write_manifest(path: str | Path, payload: Mapping[str, Any]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, destination)


def load_manifest(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def verify_manifest(
    path: str | Path,
    *,
    fixture_root: str | Path | None = None,
    recompute: bool = True,
) -> dict[str, Any]:
    """Verify a committed planetary coarse manifest and its small fixtures offline."""
    manifest_path = Path(path)
    manifest = load_manifest(manifest_path)
    if manifest.get("schema_version") != PLANETARY_COARSE_SCHEMA_VERSION:
        raise PlanetaryCoarseError("unsupported_schema", "unsupported manifest schema version")
    if manifest.get("method", {}).get("method_version") != PLANETARY_COARSE_METHOD_VERSION:
        raise PlanetaryCoarseError("unsupported_method", "unexpected planetary method version")
    root = Path(fixture_root) if fixture_root is not None else manifest_path.parent
    source = PLANETARY_SOURCES[manifest["source"]["source_id"]]
    decisions = manifest.get("comparability", [])
    gate = manifest.get("cross_body_ranking", {})
    expected_gate = cross_body_ranking_gate(decisions)
    if gate.get("enabled") != expected_gate["enabled"]:
        raise PlanetaryCoarseError("gate_mismatch", "cross-body gate is inconsistent")
    for decision in decisions:
        if decision.get("decision") not in DECISIONS:
            raise PlanetaryCoarseError(
                "invalid_decision", f"unknown comparability decision {decision.get('decision')!r}"
            )
    checks = 0
    for cell in manifest.get("cells", []):
        fixture = cell["fixture"]
        fixture_path = root / fixture["path"]
        if not fixture_path.is_file():
            raise PlanetaryCoarseError("missing_fixture", f"missing fixture {fixture_path}")
        if _sha256_file(fixture_path) != fixture["sha256"]:
            raise PlanetaryCoarseError(
                "fixture_checksum_mismatch", f"fixture checksum mismatch: {fixture_path}"
            )
        if recompute:
            with _planetary_gdal_env(), rasterio.open(fixture_path) as src:
                crs = CRS.from_user_input(src.crs)
                assert_planetary_crs(crs, body=source.body)
                array = np.asarray(src.read(1, masked=True).filled(np.nan), dtype="float64")
            pad = int(fixture["pad_pixels"])
            slope = horn_slope_degrees(array, float(cell["analysis_resolution_m"]))
            core = slope[slice(pad, -pad), slice(pad, -pad)]
            valid = np.isfinite(core)
            features = extract_coarse_features(core, valid)
            for feature_id, value in cell["features"].items():
                if not math.isclose(float(value), float(features[feature_id]), rel_tol=1e-9):
                    raise PlanetaryCoarseError(
                        "feature_mismatch",
                        f"{cell['candidate_id']} {feature_id}: committed {value} != "
                        f"recomputed {features[feature_id]}",
                    )
            if int(valid.sum()) != int(cell["valid_slope_pixels"]):
                raise PlanetaryCoarseError(
                    "count_mismatch", f"valid pixel count changed for {cell['candidate_id']}"
                )
        checks += 1
    if checks != int(manifest["counts"]["computed_cells"]):
        raise PlanetaryCoarseError("cell_count_mismatch", "manifest cell count is inconsistent")
    return manifest
