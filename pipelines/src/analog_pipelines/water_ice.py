"""D04 water/ice evidence semantics and bounded Moon/Mars pilot readers (Issue #44).

This module keeps water, ice, hydrogen and hydrated-material quantities separate.
It reads only two small, official, public PDS products over bounded windows:

* Lunar Prospector Neutron Spectrometer half-degree hydrogen abundance
  (``hydrogenhd``; H ppm by weight, converted to water-equivalent H mass percent).
* LRO Mini-RF 128 ppd global circular-polarization-ratio mosaic (read through
  HTTP range requests, native 236.9 m pixels, no resampling).
* Mars Odyssey HEND 4x4 degree normalized epithermal neutron flux map.

It deliberately does **not** convert neutron-derived hydrogen into ice fraction,
mineable ice, or a scalar water score. A neutron suppression or a radar CPR above
unity is evidence, not a confirmed detection. No observation is a similarity
feature; all D04 observations are ``evidence_only``.

Parsers are pure and offline. Only the range reader touches the network, and it
is injected so tests never download.
"""

from __future__ import annotations

import hashlib
import json
import math
import struct
import urllib.request
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from analog_pipelines.dimensions import validate_dimension_observation
from analog_pipelines.typed_contracts import (
    DepthSupport,
    SpatialSupport,
    TemporalSupport,
    TypedObservation,
    Uncertainty,
)

D04_METHOD_VERSION = "water-ice-evidence-0.1.0"

FEATURE_WATER_EQUIVALENT_HYDROGEN = "water_equivalent_hydrogen_wt_percent"
FEATURE_EPITHERMAL_NEUTRON_FLUX = "epithermal_neutron_flux_ratio"
FEATURE_RADAR_CPR = "radar_circular_polarization_ratio"
FEATURE_WATER_ICE_DETECTION = "water_ice_detection"

D04_NUMERIC_FEATURES = (
    FEATURE_WATER_EQUIVALENT_HYDROGEN,
    FEATURE_EPITHERMAL_NEUTRON_FLUX,
    FEATURE_RADAR_CPR,
)

# 1 ppm H2O-equivalent = 9 x 1 ppm H by mass (H2O is 2/18 hydrogen by mass).
H_TO_WATER_EQUIVALENT = 9.0
_PPM_TO_PERCENT = 1.0 / 10_000.0


class WaterIceError(ValueError):
    """Raised when a source product or a typed observation is not usable."""


# ---------------------------------------------------------------------------
# Source descriptors
# ---------------------------------------------------------------------------

PDS_LICENSE = (
    "CC0-1.0 (NASA-led mission data archived by the NASA Planetary Data System; "
    "no restrictive notice on the product page)"
)
PDS_LICENSE_URL = "https://science.data.nasa.gov/about/license"


@dataclass(frozen=True, slots=True)
class WaterIceSource:
    """Verified metadata for one official product; never a coverage claim."""

    dataset_id: str
    body: str
    provider: str
    product_id: str
    title: str
    version: str
    url: str
    label_url: str | None
    license: str
    license_url: str
    native_resolution: str
    native_resolution_m: float
    native_crs: str
    units: str
    quantity_class: str
    surface_or_subsurface: str
    depth_sensitivity: str
    temporal_support: str
    uncertainty: str
    nodata: str
    verification_status: str
    citation: str
    notes: str
    blockers: tuple[str, ...] = ()


SOURCES: dict[str, WaterIceSource] = {
    "lp-ns-hydrogenhd": WaterIceSource(
        dataset_id="lp-ns-hydrogenhd",
        body="Moon",
        provider="NASA Lunar Prospector Neutron Spectrometer (PDS Geosciences Node special product)",
        product_id="hydrogenhd (Hydrogen_halfdeg, December 2001)",
        title="Lunar Prospector half-degree hydrogen abundance from corrected epithermal neutrons",
        version="ASCII generated 2002-01-03 by pds_idl2ascii_dec01.pro v1.1; product version 2001-07-06",
        url="https://pds-geosciences.wustl.edu/missions/lunarp/reduced/hydrogenhd.txt",
        label_url="https://pds-geosciences.wustl.edu/missions/lunarp/reduced_special.html",
        license=PDS_LICENSE,
        license_url=PDS_LICENSE_URL,
        native_resolution="0.5 x 0.5 degree",
        native_resolution_m=15_100.0,
        native_crs="IAU_MOON planetocentric longitude/latitude (simple cylindrical, 0.5 degree)",
        units="ppm H by weight (validated against the product header)",
        quantity_class="water_equivalent_hydrogen",
        surface_or_subsurface="subsurface (integrated over the neutron sensing depth)",
        depth_sensitivity=(
            "upper ~1 m of regolith (epithermal neutron escape depth; method-level "
            "approximation from Feldman et al. 2001c, not a per-pixel retrieval)"
        ),
        temporal_support="1998-01-16 through 1999-07-31 (low-altitude mission phase)",
        uncertainty=(
            "no per-cell uncertainty column; the ASCII product has five columns only. "
            "Retrieval is unreliable where thorium/rare-earth abundances are high "
            "(Maurice et al. 2001a)"
        ),
        nodata="********** in the ASCII product; the binary .dat encodes values x10 with no separate fill constant",
        verification_status="verified",
        citation=(
            "Feldman, W. C., et al. (2001), Evidence for water ice near the lunar poles, "
            "J. Geophys. Res.; product documentation: PDS Geosciences Node Lunar "
            "Prospector reduced spectrometer special products"
        ),
        notes=(
            "Special product prepared by the LP Spectrometer Team; the PDS page states it "
            "does not represent a PDS peer-reviewed archive. H ppm is converted here to "
            "water-equivalent H mass percent (x9); it is not an ice fraction."
        ),
    ),
    "lro-minirf-cpr-128ppd": WaterIceSource(
        dataset_id="lro-minirf-cpr-128ppd",
        body="Moon",
        provider="NASA LRO Mini-RF (PDS Geosciences Node global mosaic bundle)",
        product_id="GLOBAL_CPR_128PPD_SIMP_0C.IMG",
        title="Mini-RF global circular polarization ratio mosaic, 128 pixels per degree",
        version="LRO-L-MRFLRO-5-GLOBAL-MOSAIC-V1.0, product version V1.0 (ISIS 3.3.0.3646)",
        url=(
            "https://pds-geosciences.wustl.edu/lro/lro-l-mrflro-5-global-mosaic-v1/"
            "lromrf_1001/data/128ppd/global_cpr_128ppd_simp_0c.img"
        ),
        label_url=(
            "https://pds-geosciences.wustl.edu/lro/lro-l-mrflro-5-global-mosaic-v1/"
            "lromrf_1001/data/128ppd/global_cpr_128ppd_simp_0c.lbl"
        ),
        license=PDS_LICENSE,
        license_url=PDS_LICENSE_URL,
        native_resolution="128 pixels/degree, 0.23690117518867 km/pixel",
        native_resolution_m=236.90117518867,
        native_crs="IAU_MOON planetocentric longitude/latitude (simple cylindrical, 128 ppd)",
        units="dimensionless ratio (same-sense / opposite-sense circular polarization)",
        quantity_class="radar_scattering_ratio",
        surface_or_subsurface="near-surface (S-band 12.6 cm radar); no depth retrieval",
        depth_sensitivity=(
            "near-surface radar scattering; the mosaic does not report an inversion depth "
            "and S-band penetration is not asserted here"
        ),
        temporal_support="2009-07-19 through 2010-12-26 (label START_TIME/STOP_TIME)",
        uncertainty=(
            "no per-pixel calibration uncertainty in the mosaic; the observed min-max "
            "range across the bounded window is reported as a spread, not a confidence interval"
        ),
        nodata="-1.7976931E+308 (MISSING_CONSTANT); non-finite values are also excluded",
        verification_status="verified",
        citation=(
            "Mini-RF global mosaic bundle, LRO-L-MRFLRO-5-GLOBAL-MOSAIC-V1.0 (JHU/APL, "
            "PDS Geosciences Node); CPR = SC/OS as defined in the product label"
        ),
        notes=(
            "CPR above unity is ice-consistent but not diagnostic: rough surfaces and "
            "wavelength-scale rocks also raise CPR. The mosaic mixes multiple acquisitions."
        ),
    ),
    "mro-hend-epithermal-020917": WaterIceSource(
        dataset_id="mro-hend-epithermal-020917",
        body="Mars",
        provider="NASA 2001 Mars Odyssey HEND (PDS Geosciences Node GRS special products)",
        product_id="hend_epithermal_020917.tab",
        title="HEND epithermal neutron flux, first 55 days of mapping",
        version="PDS4 v1.0 (migrated 2026-01-20; original data product 2002-09-17)",
        url=(
            "https://pds-geosciences.wustl.edu/ody/urn-nasa-pds-odyssey_grs_special/"
            "data/hend_epithermal_020917.tab"
        ),
        label_url=(
            "https://pds-geosciences.wustl.edu/ody/urn-nasa-pds-odyssey_grs_special/"
            "data/hend_epithermal_020917.xml"
        ),
        license=PDS_LICENSE,
        license_url=PDS_LICENSE_URL,
        native_resolution="4 x 4 degree cells (4050 cells)",
        native_resolution_m=237_000.0,
        native_crs="IAU_MARS planetocentric longitude/latitude (4 degree cell grid)",
        units="normalized epithermal neutron flux (multiply by 0.26 for counts/second)",
        quantity_class="neutron_flux_proxy",
        surface_or_subsurface="subsurface (integrated over the neutron sensing depth)",
        depth_sensitivity=(
            "upper ~1 m of regolith (epithermal 0.4 eV - 100 keV neutron escape depth; "
            "method-level approximation, not a per-pixel retrieval)"
        ),
        temporal_support=(
            "first 55 days of Odyssey mapping (2002); the PDS label has no start/stop "
            "dates, so the exact interval is unknown"
        ),
        uncertainty=(
            "no per-cell uncertainty column in the table; the map is normalized to 1 at "
            "Solis Planum where the maximum count rate is 0.26 counts/second"
        ),
        nodata="none declared; all 4050 table rows carry a value",
        verification_status="verified",
        citation=(
            "Mitrofanov, I., et al. (2002), Maps of Subsurface Hydrogen from the High "
            "Energy Neutron Detector, Mars Odyssey, Science 297, 78-81, "
            "doi:10.1126/science.1073616"
        ),
        notes=(
            "Normalized epithermal flux is a hydrogen proxy. A low value indicates "
            "hydrogen enrichment, not ice abundance or phase."
        ),
    ),
    # Verified as accessible, but deliberately not used for a numeric observation.
    "lro-lend-alds-20090915": WaterIceSource(
        dataset_id="lro-lend-alds-20090915",
        body="Moon",
        provider="NASA LRO LEND (PDS Geosciences Node)",
        product_id="lend_rdr_alds_20090915.dat",
        title="LEND averaged south-polar neutron counting rates, 0.5 degree grid",
        version="LRO-L-LEND-4/5-RDR-V1.0, product version 1.0",
        url=(
            "https://pds-geosciences.wustl.edu/lro/lro-l-lend-2-edr-v1/lrolen_0xxx/"
            "data/2009/20090915/lend_rdr_alds_20090915.dat"
        ),
        label_url=(
            "https://pds-geosciences.wustl.edu/lro/lro-l-lend-2-edr-v1/lrolen_0xxx/"
            "data/2009/20090915/lend_rdr_alds_20090915.xml"
        ),
        license=PDS_LICENSE,
        license_url=PDS_LICENSE_URL,
        native_resolution="0.5 x 0.5 degree below -80 degrees latitude (14400 cells)",
        native_resolution_m=15_100.0,
        native_crs="IAU_MOON planetocentric longitude/latitude (0.5 degree polar grid)",
        units="unitless relative count rate (CSETN_RATE); normalization constants are not in the table",
        quantity_class="relative_neutron_count_rate",
        surface_or_subsurface="subsurface (integrated over the neutron sensing depth)",
        depth_sensitivity="upper ~1 m of regolith (epithermal neutron escape depth)",
        temporal_support="2009-09-15 through 2011-12-11 (table START/STOP)",
        uncertainty="per-cell statistical error columns (e.g. CSETN_ERR)",
        nodata="-1.0 sentinel in rate/exposure fields",
        verification_status="accessible",
        citation=(
            "LEND RDR ALD product; Sanin et al. (2017), Icarus 283, and Mitrofanov et al. "
            "(2010), Science 330, for the hydrogen interpretation"
        ),
        notes=(
            "The ALDS table carries normalized relative counting rates but not the "
            "per-detector normalization constants needed for flux or water-equivalent "
            "hydrogen. It is recorded as accessible with an explicit blocker instead of "
            "being reinterpreted as abundance."
        ),
        blockers=(
            "ALDS emits unitless relative count rates; converting them to flux or "
            "water-equivalent hydrogen requires normalization constants that are not in "
            "this table.",
            "No numeric D04 observation is emitted from this product in the pilot.",
        ),
    ),
}

# Candidate sources verified only at the product/access level; no data extracted.
CANDIDATE_SOURCES: tuple[dict[str, Any], ...] = (
    {
        "dataset_id": "lro-lamp-far-uv",
        "body": "Moon",
        "provider": "NASA LRO LAMP",
        "product_id": "not_selected",
        "verification_status": "identified",
        "url": "http://www.boulder.swri.edu/lamp/",
        "access": "official instrument site reachable; no PDS4 gridded water-ice product ID verified",
        "blocker": (
            "LAMP is a far-UV spectrograph (stellar occultation / surface reflectance), "
            "not a gridded ice-abundance product; no bounded AOI product was located."
        ),
    },
    {
        "dataset_id": "lcross-cabeus-impact",
        "body": "Moon",
        "provider": "NASA LCROSS",
        "product_id": "not_selected",
        "verification_status": "identified",
        "url": "https://pds-geosciences.wustl.edu/missions/lcross/default.htm",
        "access": "PDS Geosciences LCROSS mission page reachable",
        "blocker": (
            "LCROSS measured ejecta from one impacted Cabeus target, not the Shackleton "
            "or LOLA reference windows; the result is a published point measurement, not "
            "an areal product for these AOIs."
        ),
    },
    {
        "dataset_id": "mro-sharad-radargrams",
        "body": "Mars",
        "provider": "NASA MRO SHARAD",
        "product_id": "urn:nasa:pds:mro_sharad_us_radargrams",
        "verification_status": "accessible",
        "url": "https://pds-geosciences.wustl.edu/mro/mro-m-sharad-5-radargram-v2/mrosh_2101/",
        "access": "PDS4 radargram and clutter-simulation bundles reachable",
        "blocker": (
            "SHARAD products are orbit-track radargrams and clutter simulations, not a "
            "per-cell detection mask or abundance grid. A defensible subsurface detection "
            "needs track selection, clutter modelling and reflector interpretation; the "
            "Jezero CTX windows are not a SHARAD detection product."
        ),
    },
    {
        "dataset_id": "mex-marsis-radargrams",
        "body": "Mars",
        "provider": "ESA Mars Express MARSIS",
        "product_id": "not_selected",
        "verification_status": "identified",
        "url": "https://pds-geosciences.wustl.edu/missions/mars_express/marsis.htm",
        "access": "mission page reachable; no bounded AOI product selected",
        "blocker": (
            "MARSIS is a sounding radar; published subsurface detections are "
            "track/interpretation results, not a per-cell product that can be joined to "
            "the 1.98 km Jezero windows."
        ),
    },
    {
        "dataset_id": "mro-crism-hydration",
        "body": "Mars",
        "provider": "NASA MRO CRISM",
        "product_id": "MROCR_3201 (MRDR multispectral), MROCR_4001 (MTRDR)",
        "verification_status": "accessible",
        "url": "https://pds-geosciences.wustl.edu/mro/mro-m-crism-3-rdr-targeted-v1/",
        "access": "PDS4 CRISM TRDR/MRDR/MTRDR volumes reachable",
        "blocker": (
            "CRISM products are radiance/I-F cubes or multispectral tiles; hydration "
            "requires summary-product computation and is evidence of hydrated minerals "
            "(D02), not automatically ice. No D04 numeric observation is derived here."
        ),
    },
)

# ---------------------------------------------------------------------------
# Offline parsers and bounded-window readers
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LatLonCell:
    """One native grid cell with an explicit lon/lat envelope."""

    west: float
    south: float
    east: float
    north: float
    latitude_deg: float
    longitude_deg: float
    value: float | None
    uncertainty: float | None = None

    @property
    def is_missing(self) -> bool:
        return self.value is None

    @property
    def bbox(self) -> dict[str, float]:
        return {
            "west": self.west,
            "south": self.south,
            "east": self.east,
            "north": self.north,
        }


@dataclass(frozen=True, slots=True)
class WindowSummary:
    """Statistics of native pixels in a bounded window; never resampled."""

    pixel_count: int
    valid_count: int
    minimum: float | None
    median: float | None
    maximum: float | None
    count_at_or_above_one: int

    @property
    def valid_fraction(self) -> float:
        if self.pixel_count <= 0:
            return 0.0
        return self.valid_count / self.pixel_count


def _parse_float(token: str, *, where: str) -> float:
    try:
        value = float(token)
    except ValueError as exc:
        raise WaterIceError(f"non-numeric value {token!r} at {where}") from exc
    if not math.isfinite(value):
        raise WaterIceError(f"non-finite value {token!r} at {where}")
    return value


def read_lp_hydrogen_ascii(path: str | Path) -> list[LatLonCell]:
    """Parse the Lunar Prospector ``hydrogenhd.txt`` table.

    The product has five comma-separated columns: lat min, lat max, lon min,
    lon max and H ppm by weight. ``**********`` is the documented ASCII fill and
    becomes ``None``; a numeric zero stays a physical zero.
    """
    cells: list[LatLonCell] = []
    for number, raw in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = [part.strip() for part in line.split(",")]
        if len(parts) != 5:
            raise WaterIceError(f"{path}:{number}: expected 5 columns, found {len(parts)}")
        south = _parse_float(parts[0], where=f"{path}:{number}")
        north = _parse_float(parts[1], where=f"{path}:{number}")
        west = _parse_float(parts[2], where=f"{path}:{number}")
        east = _parse_float(parts[3], where=f"{path}:{number}")
        if not (south < north and west < east):
            raise WaterIceError(f"{path}:{number}: invalid cell envelope")
        if parts[4] in {
            "*",
            "**",
            "***",
            "****",
            "*****",
            "******",
            "*******",
            "********",
            "*********",
            "**********",
            "NaN",
            "nan",
        }:
            value: float | None = None
        else:
            value = _parse_float(parts[4], where=f"{path}:{number}")
        cells.append(
            LatLonCell(
                west=west,
                south=south,
                east=east,
                north=north,
                latitude_deg=(south + north) / 2.0,
                longitude_deg=(west + east) / 2.0,
                value=value,
            )
        )
    if not cells:
        raise WaterIceError(f"{path}: no data rows")
    return cells


def read_hend_epithermal_ascii(path: str | Path) -> list[LatLonCell]:
    """Parse the PDS HEND ``hend_epithermal_020917.tab`` table.

    Columns are longitude centre, latitude centre and normalized epithermal flux.
    The label declares 4050 records on a 4 x 4 degree grid; count and coordinates
    are validated rather than trusted.
    """
    cells: list[LatLonCell] = []
    for number, raw in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = [part.strip() for part in line.split(",")]
        if len(parts) != 3:
            raise WaterIceError(f"{path}:{number}: expected 3 columns, found {len(parts)}")
        lon = _parse_float(parts[0], where=f"{path}:{number}")
        lat = _parse_float(parts[1], where=f"{path}:{number}")
        value = _parse_float(parts[2], where=f"{path}:{number}")
        if not -90.0 <= lat <= 90.0 or not -180.0 <= lon <= 180.0:
            raise WaterIceError(f"{path}:{number}: cell centre outside lon/lat range")
        cells.append(
            LatLonCell(
                west=lon - 2.0,
                south=lat - 2.0,
                east=lon + 2.0,
                north=lat + 2.0,
                latitude_deg=lat,
                longitude_deg=lon,
                value=value,
            )
        )
    if len(cells) != 4050:
        raise WaterIceError(f"{path}: expected 4050 records, found {len(cells)}")
    return cells


LEND_ALD_RECORD_BYTES = 310
LEND_ALD_FIELDS = {
    "start_utc": (0, 23),
    "stop_utc": (23, 23),
    "latitude_deg": (46, 4),
    "longitude_deg": (50, 4),
    "stn1_exposure_s": (58, 4),
    "stn1_rate": (62, 4),
    "stn1_error": (66, 4),
    "setn_exposure_s": (70, 4),
    "setn_rate": (74, 4),
    "setn_error": (78, 4),
    "stn2_exposure_s": (82, 4),
    "stn2_rate": (86, 4),
    "stn2_error": (90, 4),
    "stn3_exposure_s": (94, 4),
    "stn3_rate": (98, 4),
    "stn3_error": (102, 4),
    "csetn_exposure_s": (106, 4),
    "csetn_rate": (110, 4),
    "csetn_error": (114, 4),
}


@dataclass(frozen=True, slots=True)
class LendAldRecord:
    """One LEND ALD 0.5 degree cell; rates are unitless relative counting rates."""

    latitude_deg: float
    longitude_deg: float
    csetn_rate: float | None
    csetn_error: float | None
    csetn_exposure_s: float | None
    setn_rate: float | None
    start_utc: str
    stop_utc: str


def read_lend_ald_binary(
    data: bytes, *, record_bytes: int = LEND_ALD_RECORD_BYTES
) -> list[LendAldRecord]:
    """Parse the LEND ALD fixed-length binary table (PDS4 label offsets)."""
    if record_bytes <= 0:
        raise WaterIceError("record_bytes must be positive")
    if len(data) % record_bytes:
        raise WaterIceError("LEND ALD byte length is not a multiple of the record length")
    records: list[LendAldRecord] = []
    for offset in range(0, len(data), record_bytes):
        record = data[offset : offset + record_bytes]
        start = record[0:23].decode("ascii", errors="replace").strip()
        stop = record[23:46].decode("ascii", errors="replace").strip()
        lat = struct.unpack_from(">f", record, LEND_ALD_FIELDS["latitude_deg"][0])[0]
        lon = struct.unpack_from(">f", record, LEND_ALD_FIELDS["longitude_deg"][0])[0]

        def field(name: str, record: bytes = record) -> float | None:
            value = struct.unpack_from(">f", record, LEND_ALD_FIELDS[name][0])[0]
            if not math.isfinite(value) or value <= -0.5:
                return None
            return float(value)

        records.append(
            LendAldRecord(
                latitude_deg=float(lat),
                longitude_deg=float(lon),
                csetn_rate=field("csetn_rate"),
                csetn_error=field("csetn_error"),
                csetn_exposure_s=field("csetn_exposure_s"),
                setn_rate=field("setn_rate"),
                start_utc=start,
                stop_utc=stop,
            )
        )
    if not records:
        raise WaterIceError("LEND ALD table is empty")
    return records


@dataclass(frozen=True, slots=True)
class MiniRfWindow:
    """Native pixel window in the Mini-RF simple-cylindrical mosaic."""

    line_first: int
    sample_first: int
    lines: int
    samples: int
    lines_total: int
    samples_total: int
    row_bytes: int
    pixel_bytes: int = 4

    @property
    def byte_offset(self) -> int:
        return (self.line_first - 1) * self.row_bytes + (self.sample_first - 1) * self.pixel_bytes

    @property
    def byte_length(self) -> int:
        return self.lines * self.row_bytes

    def window_bbox(self) -> dict[str, float]:
        """Envelope of the pixel block in planetocentric lon/lat degrees."""
        lat_top = 90.0 - (self.line_first - 1) * 180.0 / self.lines_total
        lat_bottom = 90.0 - (self.line_first - 1 + self.lines) * 180.0 / self.lines_total
        lon_left = -180.0 + (self.sample_first - 1) * 360.0 / self.samples_total
        lon_right = -180.0 + (self.sample_first - 1 + self.samples) * 360.0 / self.samples_total
        return {"west": lon_left, "south": lat_bottom, "east": lon_right, "north": lat_top}


MINI_RF_LINES = 23040
MINI_RF_SAMPLES = 46080
MINI_RF_ROW_BYTES = MINI_RF_SAMPLES * 4
MINI_RF_PIXEL_M = 236.90117518867
MINI_RF_MISSING = -1.7976931e308


def mini_rf_window(
    latitude_deg: float,
    longitude_deg: float,
    *,
    pixels: int = 8,
    lines: int = MINI_RF_LINES,
    samples: int = MINI_RF_SAMPLES,
    row_bytes: int = MINI_RF_ROW_BYTES,
) -> MiniRfWindow:
    """Locate a centred native-pixel window; never resample to another grid."""
    if pixels <= 0 or pixels > min(lines, samples):
        raise WaterIceError("pixels must be a positive window size")
    if not -90.0 <= latitude_deg <= 90.0 or not -180.0 <= longitude_deg <= 180.0:
        raise WaterIceError("latitude/longitude outside planetocentric range")
    line = int((90.0 - latitude_deg) / 180.0 * lines) + 1
    sample = int((longitude_deg + 180.0) / 360.0 * samples) + 1
    first_line = min(max(1, line - pixels // 2), lines - pixels + 1)
    first_sample = min(max(1, sample - pixels // 2), samples - pixels + 1)
    return MiniRfWindow(
        line_first=first_line,
        sample_first=first_sample,
        lines=pixels,
        samples=pixels,
        lines_total=lines,
        samples_total=samples,
        row_bytes=row_bytes,
    )


def decode_mini_rf_window(data: bytes, window: MiniRfWindow) -> list[float | None]:
    """Decode little-endian float32 pixels; missing/fill values become ``None``."""
    expected = window.byte_length
    if len(data) != expected:
        raise WaterIceError(f"Mini-RF window has {len(data)} bytes, expected {expected}")
    values: list[float | None] = []
    row_stride = window.row_bytes
    for row in range(window.lines):
        base = row * row_stride
        for column in range(window.samples):
            value = struct.unpack_from("<f", data, base + column * 4)[0]
            if not math.isfinite(value) or value == MINI_RF_MISSING or value <= -1.0e30:
                values.append(None)
            else:
                values.append(float(value))
    return values


def summarize_window(values: Iterable[float | None]) -> WindowSummary:
    """Summarize native pixels; ``None`` is missing, zero is a physical zero."""
    pixels = list(values)
    valid = [value for value in pixels if value is not None]
    if not valid:
        return WindowSummary(
            pixel_count=len(pixels),
            valid_count=0,
            minimum=None,
            median=None,
            maximum=None,
            count_at_or_above_one=0,
        )
    ordered = sorted(valid)
    middle = len(ordered) // 2
    median = ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2.0
    return WindowSummary(
        pixel_count=len(pixels),
        valid_count=len(valid),
        minimum=ordered[0],
        median=median,
        maximum=ordered[-1],
        count_at_or_above_one=sum(1 for value in valid if value >= 1.0),
    )


def find_cell(cells: Sequence[LatLonCell], latitude_deg: float, longitude_deg: float) -> LatLonCell:
    """Return the native cell containing a point; never interpolate."""
    lon = longitude_deg % 360.0
    if lon > 180.0:
        lon -= 360.0
    for cell in cells:
        if cell.south <= latitude_deg < cell.north and cell.west <= lon < cell.east:
            return cell
    raise WaterIceError(f"no native cell contains ({latitude_deg}, {longitude_deg})")


# ---------------------------------------------------------------------------
# HTTP range access (injectable; tests use fakes)
# ---------------------------------------------------------------------------


class ByteRangeReader(Protocol):
    def read(self, start: int, end_inclusive: int) -> bytes: ...


class HttpRangeReader:
    """Minimal HTTP byte-range reader with a bounded response check."""

    def __init__(self, url: str, *, timeout: float = 90.0, max_bytes: int = 8_000_000) -> None:
        self.url = url
        self.timeout = timeout
        self.max_bytes = max_bytes

    def read(self, start: int, end_inclusive: int) -> bytes:
        if start < 0 or end_inclusive < start:
            raise WaterIceError("invalid byte range")
        if end_inclusive - start + 1 > self.max_bytes:
            raise WaterIceError("requested range exceeds the bounded read limit")
        request = urllib.request.Request(
            self.url,
            headers={
                "Range": f"bytes={start}-{end_inclusive}",
                "User-Agent": "planetary-analog/0.1 (+d04-water-ice-evidence)",
            },
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            status = getattr(response, "status", None)
            data = response.read()
        if status != 206:
            raise WaterIceError(f"server did not honour the byte range (status {status})")
        if len(data) != end_inclusive - start + 1:
            raise WaterIceError(
                f"range returned {len(data)} bytes, expected {end_inclusive - start + 1}"
            )
        return data


class RangeCache:
    """Content-addressed cache for small bounded windows."""

    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)

    def fetch(
        self,
        reader: ByteRangeReader,
        *,
        key: str,
        start: int,
        end_inclusive: int,
    ) -> tuple[bytes, bool]:
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / f"{key}.bin"
        if path.is_file():
            data = path.read_bytes()
            expected = end_inclusive - start + 1
            if len(data) != expected:
                raise WaterIceError(f"cached window {path} has an unexpected length")
            return data, True
        data = reader.read(start, end_inclusive)
        path.write_bytes(data)
        return data, False


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# Depth / phase / detection semantics
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DetectionClaim:
    """A per-source ice claim; states are never collapsed into one another."""

    state: str
    reason_code: str
    notes: str
    confidence: float | None = None


def radar_ice_claim(summary: WindowSummary, *, min_valid_fraction: float = 0.5) -> DetectionClaim:
    """Map native-window CPR statistics to an explicit detection state.

    * no valid pixels -> ``no_coverage`` (mosaic gap, not "no ice");
    * valid pixels but a gap larger than half the window -> ``unknown``;
    * any CPR >= 1 -> ``unknown`` (ice-consistent, not diagnostic);
    * otherwise -> ``not_detected`` for this sensor at this support.
    """
    if summary.valid_count == 0:
        return DetectionClaim(
            state="no_coverage",
            reason_code="no_valid_minirf_pixels",
            notes=(
                "The 8x8 native Mini-RF window contains no valid CPR pixels. This is a "
                "mosaic coverage gap; it is not evidence that ice is absent."
            ),
        )
    if summary.valid_fraction < min_valid_fraction:
        return DetectionClaim(
            state="unknown",
            reason_code="partial_minirf_coverage",
            notes=(
                f"Only {summary.valid_count}/{summary.pixel_count} native pixels are valid "
                "in this window; too few for a window-level radar statement."
            ),
        )
    if summary.maximum is not None and summary.maximum >= 1.0:
        return DetectionClaim(
            state="unknown",
            reason_code="cpr_at_or_above_unity_not_diagnostic",
            notes=(
                f"Maximum CPR {summary.maximum:.3f} is at or above unity, which is "
                "ice-consistent but not diagnostic of ice; rough surfaces and "
                "wavelength-scale rocks also raise CPR. Phase is not determined."
            ),
        )
    return DetectionClaim(
        state="not_detected",
        reason_code="no_cpr_at_or_above_unity",
        notes=(
            f"All {summary.valid_count} valid native CPR values are below unity "
            f"(max {summary.maximum:.3f}). This is a negative radar result at 236.9 m "
            "support; it does not exclude buried ice or ice at another depth."
        ),
    )


def hydrogen_ice_claim(source: WaterIceSource) -> DetectionClaim:
    """Neutron-derived hydrogen is never promoted to an ice detection."""
    if source.quantity_class == "water_equivalent_hydrogen":
        detail = "water-equivalent hydrogen mass fraction"
    else:
        detail = "normalized epithermal neutron flux (hydrogen proxy)"
    return DetectionClaim(
        state="unknown",
        reason_code="neutron_hydrogen_is_not_ice",
        notes=(
            f"The product reports {detail}, which cannot determine ice phase, ice "
            "fraction, or mineable ice. Hydrogen enrichment and ice detection are "
            "different quantities; no ice detection is claimed."
        ),
    )


def detection_state_counts(claims: Iterable[DetectionClaim]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for claim in claims:
        counts[claim.state] = counts.get(claim.state, 0) + 1
    return dict(sorted(counts.items()))


# ---------------------------------------------------------------------------
# Observation builders
# ---------------------------------------------------------------------------


def _latlon_spatial_support(
    *,
    bbox: Mapping[str, float],
    crs: str,
    native_resolution_m: float,
    window_size_m: float | None,
    valid_fraction: float | None,
    notes: str,
) -> SpatialSupport:
    return SpatialSupport(
        kind="window",
        crs=crs,
        bbox=dict(bbox),
        bbox_crs=crs,
        native_resolution_m=native_resolution_m,
        source_resolution_m=native_resolution_m,
        window_size_m=window_size_m,
        valid_fraction=valid_fraction,
        notes=notes,
    )


def _depth_for_source(source: WaterIceSource) -> DepthSupport:
    if source.surface_or_subsurface.startswith("subsurface"):
        return DepthSupport(kind="depth_interval", min_m=0.0, max_m=1.0)
    return DepthSupport(kind="surface")


def _temporal_for_source(source: WaterIceSource) -> TemporalSupport:
    if source.dataset_id == "lp-ns-hydrogenhd":
        return TemporalSupport(
            kind="interval",
            start="1998-01-16T00:00:00Z",
            end="1999-07-31T23:59:59Z",
            notes=(
                "Low-altitude portion of the Lunar Prospector mission; the map integrates "
                "that interval rather than one instant."
            ),
        )
    if source.dataset_id == "lro-minirf-cpr-128ppd":
        return TemporalSupport(
            kind="interval",
            start="2009-07-19T12:27:42Z",
            end="2010-12-26T09:17:27Z",
            notes="Label START_TIME/STOP_TIME for the multi-acquisition mosaic.",
        )
    return TemporalSupport(
        kind="unknown",
        notes=(
            "The PDS label has no start/stop dates; the product is documented as the "
            "first 55 days of Odyssey mapping (2002)."
        ),
    )


def build_water_equivalent_hydrogen_observation(
    *,
    area_id: str,
    cell: LatLonCell,
    source: WaterIceSource,
    source_asset_id: str,
    member_reference_ids: Sequence[str],
    source_manifest: str,
) -> TypedObservation:
    """Build a Moon WEH observation from one native 0.5 degree cell."""
    if cell.value is None:
        raise WaterIceError("cannot build a detected observation from a missing cell")
    hydrogen_ppm = float(cell.value)
    if hydrogen_ppm < 0.0:
        raise WaterIceError(
            "negative hydrogen abundance is a retrieval artifact and is not emitted as a "
            "physical abundance"
        )
    weh_percent = hydrogen_ppm * H_TO_WATER_EQUIVALENT * _PPM_TO_PERCENT
    return TypedObservation(
        observation_id=f"d04:lp-ns-hydrogenhd:{area_id}",
        area_id=area_id,
        body="Moon",
        dimension_code="D04",
        feature_id=FEATURE_WATER_EQUIVALENT_HYDROGEN,
        value_kind="numeric",
        state="detected",
        value=weh_percent,
        unit="percent",
        uncertainty=Uncertainty(
            kind="unknown",
            notes=(
                "The ASCII product has no per-cell uncertainty column. Retrieval is "
                "unreliable where thorium/rare-earth abundances are high."
            ),
        ),
        spatial_support=_latlon_spatial_support(
            bbox=cell.bbox,
            crs=source.native_crs,
            native_resolution_m=source.native_resolution_m,
            window_size_m=None,
            valid_fraction=1.0,
            notes=(
                "One native 0.5 x 0.5 degree cell (~15.1 km N-S; E-W width is "
                "15.1 km x cos(latitude), about 3.8 km at 89.75 S). The cell contains the "
                "1.98 km reference window; no resampling to 90 m and no claim of "
                "1.98 km support."
            ),
        ),
        temporal_support=_temporal_for_source(source),
        depth_support=_depth_for_source(source),
        method_version=D04_METHOD_VERSION,
        dataset_ids=[source.dataset_id],
        source_asset_ids=[source_asset_id],
        quality_flags=[
            "observed_neutron_spectroscopy",
            "water_equivalent_hydrogen_not_ice_fraction",
            "phase_not_determined",
            "not_confirmed_ice",
            "coarse_native_grid",
            "no_resampling_to_90m",
            "planetary_planetocentric_lonlat",
            "subsurface_integrated_sensing_depth",
            "negative_values_not_emitted",
        ],
        score_role="evidence_only",
        legacy_provenance={
            "source_manifest": source_manifest,
            "member_reference_ids": list(member_reference_ids),
            "hydrogen_ppm_by_weight": hydrogen_ppm,
            "h_to_water_equivalent_factor": H_TO_WATER_EQUIVALENT,
            "cell_bbox": cell.bbox,
            "native_resolution_deg": 0.5,
        },
        notes=(
            "Water-equivalent hydrogen (H x 9) from Lunar Prospector corrected epithermal "
            "neutrons. This is not an ice fraction, not a hydrate abundance, and not "
            "mineable ice."
        ),
    )


def build_neutron_flux_observation(
    *,
    area_id: str,
    cell: LatLonCell,
    source: WaterIceSource,
    source_asset_id: str,
    member_reference_ids: Sequence[str],
    source_manifest: str,
) -> TypedObservation:
    """Build a Mars HEND normalized epithermal flux observation."""
    if cell.value is None:
        raise WaterIceError("cannot build a detected observation from a missing cell")
    return TypedObservation(
        observation_id=f"d04:mro-hend-epithermal:{area_id}",
        area_id=area_id,
        body="Mars",
        dimension_code="D04",
        feature_id=FEATURE_EPITHERMAL_NEUTRON_FLUX,
        value_kind="numeric",
        state="detected",
        value=float(cell.value),
        unit="1",
        uncertainty=Uncertainty(
            kind="unknown",
            notes=(
                "The 4 x 4 degree map has no per-cell uncertainty column; it is "
                "normalized to 1 at Solis Planum where the maximum count rate is "
                "0.26 counts/second."
            ),
        ),
        spatial_support=_latlon_spatial_support(
            bbox=cell.bbox,
            crs=source.native_crs,
            native_resolution_m=source.native_resolution_m,
            window_size_m=None,
            valid_fraction=1.0,
            notes=(
                "One native 4 x 4 degree HEND cell (~237 km N-S at the equator; E-W "
                "width shrinks with cos(latitude)). The cell contains all five 1.98 km "
                "Jezero CTX windows; it cannot resolve them."
            ),
        ),
        temporal_support=_temporal_for_source(source),
        depth_support=_depth_for_source(source),
        method_version=D04_METHOD_VERSION,
        dataset_ids=[source.dataset_id],
        source_asset_ids=[source_asset_id],
        quality_flags=[
            "observed_neutron_spectroscopy",
            "normalized_epithermal_flux",
            "hydrogen_proxy_not_ice_fraction",
            "phase_not_determined",
            "not_confirmed_ice",
            "very_coarse_native_grid",
            "no_resampling_to_90m",
            "planetary_planetocentric_lonlat",
            "subsurface_integrated_sensing_depth",
        ],
        score_role="evidence_only",
        legacy_provenance={
            "source_manifest": source_manifest,
            "member_reference_ids": list(member_reference_ids),
            "normalized_flux": float(cell.value),
            "counts_per_second_scaling_factor": 0.26,
            "cell_bbox": cell.bbox,
            "native_resolution_deg": 4.0,
        },
        notes=(
            "Normalized HEND epithermal neutron flux. A low value indicates hydrogen "
            "enrichment, not ice abundance or phase; no water-equivalent hydrogen or ice "
            "fraction is derived from this Mars product."
        ),
    )


def build_radar_cpr_observation(
    *,
    area_id: str,
    window: MiniRfWindow,
    summary: WindowSummary,
    source: WaterIceSource,
    source_asset_id: str,
    member_reference_ids: Sequence[str],
    source_manifest: str,
) -> TypedObservation:
    """Build a Moon Mini-RF CPR observation from native pixels (median + range)."""
    if summary.valid_count == 0 or summary.median is None:
        raise WaterIceError("cannot build a detected radar observation without valid pixels")
    window_size_m = window.lines * MINI_RF_PIXEL_M
    bbox = window.window_bbox()
    return TypedObservation(
        observation_id=f"d04:lro-minirf-cpr:{area_id}",
        area_id=area_id,
        body="Moon",
        dimension_code="D04",
        feature_id=FEATURE_RADAR_CPR,
        value_kind="numeric",
        state="detected",
        value=float(summary.median),
        unit="1",
        uncertainty=Uncertainty(
            kind="published_range",
            lower=summary.minimum,
            upper=summary.maximum,
            unit="1",
            notes=(
                "Spread across the native pixels in the window, not a confidence "
                "interval; the mosaic has no per-pixel calibration uncertainty."
            ),
        ),
        spatial_support=_latlon_spatial_support(
            bbox=bbox,
            crs=source.native_crs,
            native_resolution_m=MINI_RF_PIXEL_M,
            window_size_m=window_size_m,
            valid_fraction=summary.valid_fraction,
            notes=(
                f"{window.lines} x {window.samples} native 236.9 m pixels "
                f"(~{window_size_m:.0f} m), read from the global mosaic; no resampling. "
                f"Line {window.line_first}, sample {window.sample_first}."
            ),
        ),
        temporal_support=_temporal_for_source(source),
        depth_support=_depth_for_source(source),
        method_version=D04_METHOD_VERSION,
        dataset_ids=[source.dataset_id],
        source_asset_ids=[source_asset_id],
        quality_flags=[
            "observed_radar_backscatter",
            "circular_polarization_ratio",
            "cpr_alone_not_ice",
            "phase_not_determined",
            "not_confirmed_ice",
            "native_236m_pixels",
            "no_resampling_to_90m",
            "planetary_planetocentric_lonlat",
            "surface_scattering_no_depth_retrieval",
        ],
        score_role="evidence_only",
        legacy_provenance={
            "source_manifest": source_manifest,
            "member_reference_ids": list(member_reference_ids),
            "line_first": window.line_first,
            "sample_first": window.sample_first,
            "pixels": window.lines * window.samples,
            "valid_pixels": summary.valid_count,
            "minimum": summary.minimum,
            "median": summary.median,
            "maximum": summary.maximum,
            "count_at_or_above_one": summary.count_at_or_above_one,
            "byte_offset": window.byte_offset,
            "byte_length": window.byte_length,
        },
        notes=(
            "Window median circular polarization ratio at native 236.9 m support. CPR "
            "above unity is ice-consistent but not diagnostic; no ice detection is "
            "claimed from CPR alone."
        ),
    )


def build_detection_claim_observation(
    *,
    area_id: str,
    body: str,
    claim: DetectionClaim,
    source: WaterIceSource,
    source_asset_id: str,
    member_reference_ids: Sequence[str],
    source_manifest: str,
    bbox: Mapping[str, float],
    valid_fraction: float | None,
    support_notes: str,
    extra_provenance: Mapping[str, Any] | None = None,
) -> TypedObservation:
    """Build an explicit claim observation; missing states carry no value."""
    provenance: dict[str, Any] = {
        "source_manifest": source_manifest,
        "member_reference_ids": list(member_reference_ids),
        "reason_code": claim.reason_code,
        "cell_bbox": dict(bbox),
    }
    if extra_provenance:
        provenance.update(dict(extra_provenance))
    return TypedObservation(
        observation_id=f"d04:claim:{source.dataset_id}:{area_id}",
        area_id=area_id,
        body=body,  # type: ignore[arg-type]
        dimension_code="D04",
        feature_id=FEATURE_WATER_ICE_DETECTION,
        value_kind="detection",
        state=claim.state,  # type: ignore[arg-type]
        value=None,
        unit="detection",
        uncertainty=Uncertainty(kind="unknown", notes=claim.notes),
        spatial_support=_latlon_spatial_support(
            bbox=bbox,
            crs=source.native_crs,
            native_resolution_m=source.native_resolution_m,
            window_size_m=None,
            valid_fraction=valid_fraction,
            notes=support_notes,
        ),
        temporal_support=_temporal_for_source(source),
        depth_support=_depth_for_source(source),
        method_version=D04_METHOD_VERSION,
        dataset_ids=[source.dataset_id],
        source_asset_ids=[source_asset_id],
        quality_flags=[
            f"claim_state_{claim.state}",
            f"reason_{claim.reason_code}",
            "phase_not_determined",
            "not_confirmed_ice",
            "no_mineable_ice_inference",
            "planetary_planetocentric_lonlat",
        ],
        score_role="evidence_only",
        legacy_provenance=provenance,
        notes=claim.notes,
    )


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

_EARTH_CRS_MARKERS = ("epsg:4326", "earth_wgs84", "wgs 84", "wgs84")


def validate_water_ice_observation(observation: TypedObservation) -> list[str]:
    """Return explicit D04 errors; never coerce or upgrade a claim.

    The typed-v2 contract already enforces the five states and that missing states
    carry no value. This adds the D04 quantity, depth, phase and frame separation.
    """
    errors = list(validate_dimension_observation(observation))
    if observation.dimension_code != "D04":
        errors.append("water/ice validator only accepts D04 observations")
    if observation.score_role != "evidence_only":
        errors.append("D04 pilot observations must be evidence_only, never similarity or filter")
    if observation.body in {"Moon", "Mars"}:
        crs = observation.spatial_support.crs.lower()
        if any(marker in crs for marker in _EARTH_CRS_MARKERS):
            errors.append(f"{observation.body} observation must not use an Earth CRS")
    if observation.feature_id in D04_NUMERIC_FEATURES:
        if "not_confirmed_ice" not in observation.quality_flags:
            errors.append("numeric D04 evidence must carry the not_confirmed_ice flag")
        if "phase_not_determined" not in observation.quality_flags:
            errors.append("numeric D04 evidence must carry the phase_not_determined flag")
        if observation.feature_id in {
            FEATURE_WATER_EQUIVALENT_HYDROGEN,
            FEATURE_EPITHERMAL_NEUTRON_FLUX,
        }:
            if observation.depth_support.kind != "depth_interval":
                errors.append("neutron-derived evidence requires a declared depth interval")
            elif observation.depth_support.max_m is None or observation.depth_support.max_m > 5.0:
                errors.append("declared neutron sensing depth must be bounded and explicit")
        if observation.feature_id == FEATURE_RADAR_CPR:
            if observation.depth_support.kind != "surface":
                errors.append("radar CPR is a near-surface scattering ratio, not a depth retrieval")
            if observation.unit != "1":
                errors.append("radar CPR must be a dimensionless ratio")
    if observation.feature_id == FEATURE_WATER_ICE_DETECTION:
        if observation.state == "detected":
            errors.append(
                "this pilot has no product that supports an AOI-scale ice detection; a "
                "detected claim would need a matching-support product"
            )
        if observation.unit != "detection":
            errors.append("detection claims must use the detection unit")
        if observation.value is not None:
            errors.append("detection claims must not carry an abundance value")
    if (
        observation.feature_id == FEATURE_WATER_EQUIVALENT_HYDROGEN
        and observation.unit != "percent"
    ):
        errors.append("water-equivalent hydrogen must be reported as mass percent")
    return errors


def observation_to_jsonl_line(observation: TypedObservation) -> str:
    return json.dumps(observation.model_dump(mode="json"), sort_keys=True)


def write_observations_jsonl(path: str | Path, observations: Sequence[TypedObservation]) -> str:
    ordered = sorted(
        observations,
        key=lambda item: (
            item.body,
            item.feature_id,
            item.area_id,
            item.observation_id,
        ),
    )
    text = "\n".join(observation_to_jsonl_line(item) for item in ordered) + "\n"
    Path(path).write_text(text, encoding="utf-8")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_observations_jsonl(path: str | Path) -> list[TypedObservation]:
    observations: list[TypedObservation] = []
    for number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        observations.append(TypedObservation.model_validate(json.loads(line)))
        if not observations:
            raise WaterIceError(f"{path}:{number}: empty observation")
    return observations
