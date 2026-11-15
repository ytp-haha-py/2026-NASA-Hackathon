"""Bounded D03 thermal and D04 water/ice AOI evidence for Issue #170 Goal E.

This module adds *coarse context* and *modeled* evidence around the existing
Moon/Mars reference windows.  It deliberately does not replace or rewrite the
committed #43/#44 pilots:

* D03 Moon coarse context: LRO Diviner Global Cumulative Product (GCP), 0.5
  degree, bolometric brightness temperature at two local-time bins.
* D03 Mars coarse context: MGS TES TIMAP day/night apparent thermal inertia,
  20 pixels/degree (2.96 km), with the published interpolation mask.
* D03/D04 Moon modeled evidence: LRO Diviner Polar Resource Product (PRP),
  annual mean temperature at 2 cm, annual maximum surface temperature and the
  modeled depth to water-ice stability on a ~350 m triangle mesh.
* D04 Mars measured proxy: Odyssey Neutron Spectrometer epithermal counts per
  second on the native 0.5 degree grid.  Counts are **not** the normalized
  HEND flux ratio and are never mixed with it.

All functions are pure and offline; the builder supplies bytes/text from
bounded HTTP range reads or a cached official product.  No value is fabricated,
interpolated or zero-filled.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

MOON = "Moon"
MARS = "Mars"

D03 = "D03"
D04 = "D04"

MOON_RADIUS_KM = 1737.4
MARS_RADIUS_KM = 3396.19
TES_BODY_RADIUS_KM = 3396.0
MOON_GEOGRAPHIC_CRS = "+proj=longlat +R=1737400 +no_defs"
MARS_GEOGRAPHIC_CRS = "+proj=longlat +R=3396190 +no_defs"
MOON_POLAR_CRS = (
    "+proj=stere +lat_0=-90 +lat_ts=-90 +lon_0=0 +x_0=0 +y_0=0 +R=1737400 +units=m +no_defs"
)
MARS_EQCR_CRS = "+proj=eqc +lat_ts=0 +lat_0=0 +lon_0=0 +x_0=0 +y_0=0 +R=3396190 +units=m +no_defs"
TES_EQCR_CRS = "+proj=eqc +lat_ts=0 +lat_0=0 +lon_0=0 +x_0=0 +y_0=0 +R=3396000 +units=m +no_defs"

# ---------------------------------------------------------------------------
# Verified source metadata
# ---------------------------------------------------------------------------

DIVINER_GCP_DATASET_ID = "lro-diviner-gcp-pols-0p5deg-2009-2015"
DIVINER_GCP_PRODUCT_ID = "GLOBAL_CUMUL_AVG_CYL_90S80S_002.TAB"
DIVINER_GCP_URL = (
    "https://pds-geosciences.wustl.edu/lro/urn-nasa-pds-lro_diviner_derived1/"
    "data_derived_gcp/global_cumul_avg_cyl_90s80s_002.tab"
)
DIVINER_GCP_LABEL_URL = (
    "https://pds-geosciences.wustl.edu/lro/urn-nasa-pds-lro_diviner_derived1/"
    "data_derived_gcp/global_cumul_avg_cyl_90s80s_002.lbl"
)
DIVINER_GCP_DOI = "10.1016/j.icarus.2016.08.012"
DIVINER_GCP_CITATION = (
    "Williams, J.-P., Paige, D.A., Greenhagen, B.T., Sefton-Nash, E. (2017), "
    "The global surface temperatures of the Moon as measured by the Diviner Lunar "
    "Radiometer Experiment, Icarus 283, 300-325, doi:10.1016/j.icarus.2016.08.012"
)
DIVINER_GCP_START = "2009-07-05T16:50:26.195Z"
DIVINER_GCP_END = "2015-04-01T00:00:00.000Z"
DIVINER_GCP_ROW_BYTES = 113
DIVINER_GCP_LAT_BINS = 20
DIVINER_GCP_LON_BINS = 720
DIVINER_GCP_LTIM_BINS = 96
DIVINER_GCP_PIXEL_DEG = 0.5
DIVINER_GCP_NATIVE_RESOLUTION_M = 15_100.0
DIVINER_GCP_FILL_K = -9999.0
DIVINER_GCP_METHOD_VERSION = "diviner-gcp-tbol-0.5deg-1.0.0"

TES_DATASET_ID = "mgs-tes-timap-20ppd"
TES_DAY_PRODUCT_ID = "GLOBAL_TI_DAY_2007"
TES_NIGHT_PRODUCT_ID = "GLOBAL_TI_NIGHT_2007"
TES_DAY_URL = (
    "https://pds-geosciences.wustl.edu/mgs/mgs-m-tes-5-timap-v1/mgst_9001/data/"
    "global_ti_day_2007.img"
)
TES_NIGHT_URL = (
    "https://pds-geosciences.wustl.edu/mgs/mgs-m-tes-5-timap-v1/mgst_9001/data/"
    "global_ti_night_2007.img"
)
TES_DAY_MASK_URL = (
    "https://pds-geosciences.wustl.edu/mgs/mgs-m-tes-5-timap-v1/mgst_9001/data/"
    "global_ti_msk_day_2007.img"
)
TES_NIGHT_MASK_URL = (
    "https://pds-geosciences.wustl.edu/mgs/mgs-m-tes-5-timap-v1/mgst_9001/data/"
    "global_ti_msk_night_2007.img"
)
TES_DAY_LABEL_URL = (
    "https://pds-geosciences.wustl.edu/mgs/mgs-m-tes-5-timap-v1/mgst_9001/data/"
    "global_ti_day_2007.lbl"
)
TES_CITATION = (
    "Putzig, N.E., Mellon, M.T., Heet, T.L., Ward, J.G. (2009), MGS Mars TES "
    "Derived Thermal Inertia Maps V1.0, MGS-M-TES-5-TIMAP-V1.0, NASA PDS; "
    "Putzig and Mellon (2007), Icarus 191, 68-94, doi:10.1016/j.icarus.2007.05.013"
)
TES_DOI = "10.1016/j.icarus.2007.05.013"
TES_PIXELS_PER_DEGREE = 20.0
TES_PIXEL_RESOLUTION_M = 2960.0
TES_LINES = 3600
TES_SAMPLES = 7200
TES_UNIT = "J m^-2 K^-1 s^-1/2"
TES_NATIVE_CRS = TES_EQCR_CRS
TES_METHOD_DAY = "tes-timap-day-apparent-thermal-inertia-1.0.0"
TES_METHOD_NIGHT = "tes-timap-night-apparent-thermal-inertia-1.0.0"

PRP_DATASET_ID = "lro-diviner-prp-south-v2"
PRP_PRODUCT_ID = "DLRE_PRP_SOUTH.TAB"
PRP_URL = (
    "https://pds-geosciences.wustl.edu/lro/urn-nasa-pds-lro_diviner_derived1/"
    "data_derived_prp/dlre_prp_south.tab"
)
PRP_XML_URL = (
    "https://pds-geosciences.wustl.edu/lro/urn-nasa-pds-lro_diviner_derived1/"
    "data_derived_prp/dlre_prp_south.xml"
)
PRP_CITATION = (
    "Paige, D.A., et al. (2010), Diviner Lunar Radiometer Observations of Cold Traps "
    "in the Moon's South Polar Region, Science 330, 479-482, doi:10.1126/science.1187726; "
    "LRO Diviner Polar Resource Product V2.0, PDS4 label dlre_prp_south.xml"
)
PRP_DOI = "10.1126/science.1187726"
PRP_ROW_BYTES = 210
PRP_ROWS = 2_880_000
PRP_INVALID_DEPTH_M = -999.0
PRP_MAX_DEPTH_M = 2.8738
PRP_METHOD_TEMPERATURE = "diviner-prp-annual-temperature-1.0.0"
PRP_METHOD_ICE_DEPTH = "diviner-prp-ice-stability-depth-1.0.0"
PRP_TRIANGLE_RESOLUTION_M = 350.0
PRP_NATIVE_RESOLUTION_NOTE = (
    "2,880,000-triangle mesh covering a square centred on the south pole to 80 S; "
    "triangle edge length is approximately 0.3-0.4 km. The product does not publish "
    "a single nominal resolution."
)

ODYSSEY_NS_DATASET_ID = "ody-ns-epithermal-0p5deg-2002"
ODYSSEY_NS_PRODUCT_ID = "NS_EPITHERMAL_020917.TAB"
ODYSSEY_NS_URL = (
    "https://pds-geosciences.wustl.edu/ody/urn-nasa-pds-odyssey_grs_special/data/"
    "ns_epithermal_020917.tab"
)
ODYSSEY_NS_LABEL_URL = (
    "https://pds-geosciences.wustl.edu/ody/urn-nasa-pds-odyssey_grs_special/data/"
    "ns_epithermal_020917.lbl"
)
ODYSSEY_NS_CITATION = (
    "Feldman, W.C., et al. (2002), Global Distribution of Neutrons from Mars: Results "
    "from Mars Odyssey, Science 297, 75-78, doi:10.1126/science.1073541; PDS Geosciences "
    "Node Odyssey GRS special product ns_epithermal_020917"
)
ODYSSEY_NS_DOI = "10.1126/science.1073541"
ODYSSEY_NS_ROW_BYTES = 26
ODYSSEY_NS_LAT_BINS = 360
ODYSSEY_NS_LON_BINS = 720
ODYSSEY_NS_PIXEL_DEG = 0.5
ODYSSEY_NS_NATIVE_RESOLUTION_M = 29_600.0
ODYSSEY_NS_METHOD_VERSION = "odyssey-ns-epithermal-counts-0.5deg-1.0.0"
ODYSSEY_NS_QUANTITY_ID = "epithermal_neutron_counts_per_second"

PDS_LICENSE = (
    "CC0-1.0 (NASA-led mission data archived by the NASA Planetary Data System; "
    "no restrictive notice on the product page)"
)
PDS_LICENSE_URL = "https://science.data.nasa.gov/about/license"

SOURCE_STATUS: dict[str, dict[str, Any]] = {
    "diviner-gcp": {
        "dataset_id": DIVINER_GCP_DATASET_ID,
        "body": MOON,
        "dimension_code": D03,
        "provider": "NASA PDS Geosciences Node / UCLA Diviner Lunar Radiometer Experiment",
        "product_id": DIVINER_GCP_PRODUCT_ID,
        "version": "PDS3, calibration software 4.0, product version 1",
        "url": DIVINER_GCP_URL,
        "label_url": DIVINER_GCP_LABEL_URL,
        "doi": DIVINER_GCP_DOI,
        "citation": DIVINER_GCP_CITATION,
        "license": PDS_LICENSE,
        "license_url": PDS_LICENSE_URL,
        "evidence_class": "measured_radiometric_composite",
        "native_resolution": "0.5 x 0.5 degree (up to ~55 km in latitude near the pole)",
        "native_resolution_m": DIVINER_GCP_NATIVE_RESOLUTION_M,
        "native_crs": "IAU_MOON planetocentric longitude/latitude (simple cylindrical, 0.5 degree)",
        "temporal_support": f"{DIVINER_GCP_START} to {DIVINER_GCP_END}; all nadir observations",
        "local_time_support": "0.25 h bins; 00:00-00:15 and 12:00-12:15 selected",
        "depth_support": "surface radiometric brightness temperature",
        "uncertainty": (
            "no per-cell uncertainty column; the table reports brightness temperatures "
            "and bolometric brightness temperatures from cumulative nadir observations"
        ),
        "nodata": "-9999.0 (documented no-observation constant); negative radiance values also occur",
        "qa": "fill constant and physical range checked per row; no interpolation",
        "score_role": "context_only",
        "notes": (
            "Coarse context only. This is not the 240 m PCP product and is never mixed "
            "with the committed PCP window values."
        ),
    },
    "tes-timap": {
        "dataset_id": TES_DATASET_ID,
        "body": MARS,
        "dimension_code": D03,
        "provider": "NASA PDS Geosciences Node / MGS TES / Southwest Research Institute",
        "product_id": f"{TES_DAY_PRODUCT_ID} + {TES_NIGHT_PRODUCT_ID}",
        "version": "MGS-M-TES-5-TIMAP-V1.0 (2009)",
        "url": TES_DAY_URL,
        "label_url": TES_DAY_LABEL_URL,
        "doi": TES_DOI,
        "citation": TES_CITATION,
        "license": PDS_LICENSE,
        "license_url": PDS_LICENSE_URL,
        "evidence_class": "derived_retrieval",
        "native_resolution": "20 pixels/degree (2.96 km/pixel)",
        "native_resolution_m": TES_PIXEL_RESOLUTION_M,
        "native_crs": "Mars planetocentric simple cylindrical, R=3396.0 km, positive east",
        "temporal_support": (
            "TES observations Feb 1999-Apr 2004, three Mars years; day and night maps; "
            "no per-pixel acquisition time is carried by the map"
        ),
        "local_time_support": "dayside and nightside maps, no single local solar time",
        "depth_support": "upper few centimetres (apparent thermal inertia retrieval)",
        "uncertainty": (
            "no per-pixel uncertainty layer; the data set cites Putzig and Mellon (2007) "
            "for retrieval accuracy and provides a separate interpolation mask"
        ),
        "nodata": "interpolation mask 0 marks infilled pixels; values outside 5-5000 rejected",
        "qa": "mask applied per pixel; only non-interpolated native pixels are reported",
        "score_role": "context_only",
        "notes": (
            "Coarse context only. TES TIMAP uses a different retrieval, support and "
            "method version from the committed THEMIS 100 m product; the two are never mixed."
        ),
    },
    "diviner-prp": {
        "dataset_id": PRP_DATASET_ID,
        "body": MOON,
        "dimension_code": f"{D03}+{D04}",
        "provider": "NASA PDS Geosciences Node / UCLA Diviner Lunar Radiometer Experiment",
        "product_id": PRP_PRODUCT_ID,
        "version": "LRO-L-DLRE-5-PRP-V2.0, PDS4 product version 1.0, calibration software 4.0",
        "url": PRP_URL,
        "label_url": PRP_XML_URL,
        "doi": PRP_DOI,
        "citation": PRP_CITATION,
        "license": PDS_LICENSE,
        "license_url": PDS_LICENSE_URL,
        "evidence_class": "modeled",
        "native_resolution": PRP_NATIVE_RESOLUTION_NOTE,
        "native_resolution_m": PRP_TRIANGLE_RESOLUTION_M,
        "native_crs": "Moon-centered body-fixed x/y/z (km), planetocentric longitude/latitude",
        "temporal_support": (
            "no observation start/stop in the label; annual quantities derived from "
            "Diviner climatology and a Kaguya DEM"
        ),
        "local_time_support": "annual average and annual maximum, not a local-time sample",
        "depth_support": (
            "temp_avg at 2 cm below the surface; temp_max at the surface; ice_depth is a "
            "modeled sublimation-stability depth"
        ),
        "uncertainty": (
            "no per-triangle uncertainty in the product; modeled quantities, not measurements"
        ),
        "nodata": "ice_depth -999.0 means the modeled depth exceeds 2.8738 m",
        "qa": "temperature range and depth range checked per triangle; physical zero retained",
        "score_role": "evidence_only",
        "notes": (
            "Model-derived. ice_depth is the depth at which water ice would sublimate at "
            "1 kg m-2 per billion years; 0 means stable at the surface. It is not an "
            "observed ice detection, ice fraction or mineable-ice estimate."
        ),
    },
    "odyssey-ns": {
        "dataset_id": ODYSSEY_NS_DATASET_ID,
        "body": MARS,
        "dimension_code": D04,
        "provider": "NASA PDS Geosciences Node / 2001 Mars Odyssey Gamma Ray Spectrometer",
        "product_id": ODYSSEY_NS_PRODUCT_ID,
        "version": "PDS3 special product, created 2002-09-17",
        "url": ODYSSEY_NS_URL,
        "label_url": ODYSSEY_NS_LABEL_URL,
        "doi": ODYSSEY_NS_DOI,
        "citation": ODYSSEY_NS_CITATION,
        "license": PDS_LICENSE,
        "license_url": PDS_LICENSE_URL,
        "evidence_class": "measured_proxy",
        "native_resolution": "0.5 x 0.5 degree (about 29.6 km at the equator)",
        "native_resolution_m": ODYSSEY_NS_NATIVE_RESOLUTION_M,
        "native_crs": "IAU_MARS planetocentric longitude/latitude (simple cylindrical, 0.5 degree)",
        "temporal_support": (
            "first 25.7 days of Odyssey mapping (2002); exact start/stop not stated in the label"
        ),
        "local_time_support": "not carried by the product",
        "depth_support": "upper ~1 m neutron sensing depth (method-level)",
        "uncertainty": "no per-cell uncertainty column in the ASCII table",
        "nodata": (
            "no fill constant is documented; non-positive cells are treated as no_coverage "
            "and are never interpreted as physical zero"
        ),
        "qa": "finite and positive check; no interpolation",
        "score_role": "evidence_only",
        "notes": (
            "Counts/second, not the normalized HEND epithermal flux ratio. The two "
            "products use different instruments and normalization and are not comparable."
        ),
    },
}


# ---------------------------------------------------------------------------
# Small geometry helpers
# ---------------------------------------------------------------------------


def angular_distance_m(
    lat1_deg: float,
    lon1_deg: float,
    lat2_deg: float,
    lon2_deg: float,
    radius_km: float,
) -> float:
    """Great-circle distance on a sphere in metres."""
    phi1 = math.radians(lat1_deg)
    phi2 = math.radians(lat2_deg)
    dphi = phi2 - phi1
    dlambda = math.radians(lon2_deg - lon1_deg)
    a = math.sin(dphi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2.0) ** 2
    return 2.0 * radius_km * 1000.0 * math.asin(min(1.0, math.sqrt(a)))


def xyz_from_lonlat_km(
    lat_deg: float, lon_deg: float, radius_km: float
) -> tuple[float, float, float]:
    lat = math.radians(lat_deg)
    lon = math.radians(lon_deg)
    return (
        radius_km * math.cos(lat) * math.cos(lon),
        radius_km * math.cos(lat) * math.sin(lon),
        radius_km * math.sin(lat),
    )


def triangle_area_km2(v1: Sequence[float], v2: Sequence[float], v3: Sequence[float]) -> float:
    """Planar triangle area from Moon-centred km vertices (small-triangle approximation)."""
    ax, ay, az = (v2[0] - v1[0], v2[1] - v1[1], v2[2] - v1[2])
    bx, by, bz = (v3[0] - v1[0], v3[1] - v1[1], v3[2] - v1[2])
    cx = ay * bz - az * by
    cy = az * bx - ax * bz
    cz = ax * by - ay * bx
    return 0.5 * math.sqrt(cx * cx + cy * cy + cz * cz)


def moon_polar_xy_m(lat_deg: float, lon_deg: float) -> tuple[float, float]:
    """Diviner polar-stereographic x/y in metres (positive east, R=1737.4 km)."""
    r = MOON_RADIUS_KM * 1000.0 * math.radians(90.0 + lat_deg)
    return r * math.sin(math.radians(lon_deg)), r * math.cos(math.radians(lon_deg))


# ---------------------------------------------------------------------------
# Diviner GCP (0.5 degree Moon coarse context)
# ---------------------------------------------------------------------------


def gcp_cell_indices(lon_deg: float, lat_deg: float) -> tuple[int, int]:
    """Return (lon_index, lat_index) for the GCP 0.5 degree cell containing a point."""
    if not (-90.0 <= lat_deg <= -80.0):
        raise ValueError("GCP covers only 90S-80S latitude")
    if not (-180.0 <= lon_deg <= 180.0):
        raise ValueError("GCP longitude must be within -180..180")
    lon_index = int(math.floor((lon_deg + 180.0) / DIVINER_GCP_PIXEL_DEG))
    lat_index = int(math.floor((lat_deg + 90.0) / DIVINER_GCP_PIXEL_DEG))
    lon_index = min(max(lon_index, 0), DIVINER_GCP_LON_BINS - 1)
    lat_index = min(max(lat_index, 0), DIVINER_GCP_LAT_BINS - 1)
    return lon_index, lat_index


def gcp_cell_center(lon_index: int, lat_index: int) -> tuple[float, float]:
    lon = -180.0 + (lon_index + 0.5) * DIVINER_GCP_PIXEL_DEG
    lat = -90.0 + (lat_index + 0.5) * DIVINER_GCP_PIXEL_DEG
    return lon, lat


def gcp_row_index(lon_index: int, lat_index: int, ltim_index: int) -> int:
    if not 0 <= ltim_index < DIVINER_GCP_LTIM_BINS:
        raise ValueError("GCP local-time index out of range")
    return (lon_index * DIVINER_GCP_LAT_BINS + lat_index) * DIVINER_GCP_LTIM_BINS + ltim_index


def gcp_row_byte_range(row_index: int) -> tuple[int, int]:
    start = row_index * DIVINER_GCP_ROW_BYTES
    return start, start + DIVINER_GCP_ROW_BYTES - 1


def parse_gcp_row(text: str) -> dict[str, float]:
    """Parse one fixed-width GCP data row (not the header)."""
    clean = text.replace("\r", "").replace("\n", "").strip()
    parts = [part.strip() for part in clean.split(",")]
    if len(parts) != 11:
        raise ValueError(f"GCP row has {len(parts)} columns, expected 11")
    values = [float(part) for part in parts]
    return {
        "clon": values[0],
        "clat": values[1],
        "ltim": values[2],
        "t3": values[3],
        "t4": values[4],
        "t5": values[5],
        "t6": values[6],
        "t7": values[7],
        "t8": values[8],
        "t9": values[9],
        "tbol": values[10],
    }


def gcp_brightness_state(tbol_k: float) -> tuple[str, float | None, str | None]:
    """Return (state, value, reason). Fill and negative-radiance values are never zero-filled."""
    if not math.isfinite(tbol_k):
        return "unknown", None, "non_finite_value"
    if tbol_k == DIVINER_GCP_FILL_K:
        return "no_coverage", None, "gcp_no_observation_constant"
    if tbol_k < 0.0:
        return "unknown", None, "negative_radiance_below_channel_sensitivity"
    if tbol_k > 1000.0:
        return "unknown", None, "implausible_brightness_temperature"
    return "detected", tbol_k, None


def gcp_spatial_support(lon_index: int, lat_index: int) -> dict[str, Any]:
    lon, lat = gcp_cell_center(lon_index, lat_index)
    half = DIVINER_GCP_PIXEL_DEG / 2.0
    return {
        "kind": "bbox",
        "crs": MOON_GEOGRAPHIC_CRS,
        "bbox": {
            "west": lon - half,
            "south": lat - half,
            "east": lon + half,
            "north": lat + half,
        },
        "bbox_crs": MOON_GEOGRAPHIC_CRS,
        "native_resolution_m": DIVINER_GCP_NATIVE_RESOLUTION_M,
        "source_resolution_m": DIVINER_GCP_NATIVE_RESOLUTION_M,
        "notes": (
            "Native 0.5 x 0.5 degree GCP cell containing the reference-window centre; "
            "the cell is much coarser than the 1.98 km window and is context only."
        ),
    }


def gcp_observation_payload(
    *,
    reference_id: str,
    display_name: str,
    lat_deg: float,
    lon_deg: float,
    lon_index: int,
    lat_index: int,
    ltim_index: int,
    ltim_label: str,
    row: Mapping[str, float],
    asset_id: str,
    source_manifest: str,
) -> dict[str, Any]:
    state, value, reason = gcp_brightness_state(float(row["tbol"]))
    cell_lon, cell_lat = gcp_cell_center(lon_index, lat_index)
    return {
        "schema_version": "2.0.0",
        "observation_id": (
            f"diviner-gcp:{reference_id}:surface_brightness_temperature_k:ltim{ltim_index:02d}"
        ),
        "area_id": f"moon-gcp-cell-s{abs(cell_lat):06.2f}-e{cell_lon:07.2f}",
        "body": MOON,
        "dimension_code": D03,
        "feature_id": "surface_brightness_temperature_k",
        "value_kind": "numeric",
        "state": state,
        "value": value,
        "unit": "K",
        "uncertainty": {
            "kind": "unknown",
            "value": None,
            "lower": None,
            "upper": None,
            "unit": "K",
            "notes": "No per-cell uncertainty is supplied by the GCP table.",
        },
        "spatial_support": gcp_spatial_support(lon_index, lat_index),
        "temporal_support": {
            "kind": "composite",
            "start": DIVINER_GCP_START,
            "end": DIVINER_GCP_END,
            "local_solar_time": ltim_label,
            "notes": (
                "Cumulative multi-year GCP composite, not a single acquisition and not a "
                "same-sol pair. No diurnal range is computed."
            ),
        },
        "depth_support": {"kind": "surface", "min_m": None, "max_m": None},
        "method_version": DIVINER_GCP_METHOD_VERSION,
        "dataset_ids": [DIVINER_GCP_DATASET_ID],
        "source_asset_ids": [asset_id],
        "quality_flags": [
            "coarse_context_0p5deg",
            "cumulative_composite",
            "not_same_sol",
            "no_diurnal_range",
            "planetary_planetocentric_lonlat",
        ],
        "score_role": "context_only",
        "legacy_provenance": {
            "reference_id": reference_id,
            "display_name": display_name,
            "reference_latitude_deg": lat_deg,
            "reference_longitude_deg": lon_deg,
            "source_manifest": source_manifest,
            "gcp_cell_center_lon_deg": cell_lon,
            "gcp_cell_center_lat_deg": cell_lat,
            "gcp_row_index": gcp_row_index(lon_index, lat_index, ltim_index),
            "gcp_local_time_midpoint_hours": row["ltim"],
            "gcp_tbol_k": row["tbol"],
            "reason_code": reason,
            "native_resolution_m": DIVINER_GCP_NATIVE_RESOLUTION_M,
            "doi": DIVINER_GCP_DOI,
            "citation": DIVINER_GCP_CITATION,
        },
        "notes": (
            "0.5 degree Diviner GCP bolometric brightness temperature at one local-time "
            "bin. Context only; not the 240 m PCP product and not kinetic temperature."
        ),
    }


# ---------------------------------------------------------------------------
# TES TIMAP (2.96 km Mars coarse context)
# ---------------------------------------------------------------------------


def tes_pixel_index(lat_deg: float, lon_deg: float) -> tuple[int, int]:
    """Return (line0, sample0) for the TES 20 ppd simple-cylindrical pixel containing a point."""
    if not (-90.0 <= lat_deg <= 90.0):
        raise ValueError("TES latitude out of range")
    line = int(math.floor((90.0 - lat_deg) * TES_PIXELS_PER_DEGREE))
    sample = int(math.floor((lon_deg + 180.0) * TES_PIXELS_PER_DEGREE))
    return min(max(line, 0), TES_LINES - 1), min(max(sample, 0), TES_SAMPLES - 1)


def tes_pixel_center(line0: int, sample0: int) -> tuple[float, float]:
    lat = 90.0 - (line0 + 0.5) / TES_PIXELS_PER_DEGREE
    lon = -180.0 + (sample0 + 0.5) / TES_PIXELS_PER_DEGREE
    return lat, lon


def tes_value_byte_range(line0: int, sample0: int) -> tuple[int, int]:
    offset = (line0 * TES_SAMPLES + sample0) * 2
    return offset, offset + 1


def tes_mask_byte_range(line0: int, sample0: int) -> tuple[int, int]:
    offset = line0 * TES_SAMPLES + sample0
    return offset, offset


def decode_tes_uint16_msb(payload: bytes) -> int:
    if len(payload) != 2:
        raise ValueError("TES value payload must be exactly 2 bytes")
    return int.from_bytes(payload, "big", signed=False)


def decode_tes_mask_uint8(payload: bytes) -> int:
    if len(payload) != 1:
        raise ValueError("TES mask payload must be exactly 1 byte")
    return payload[0]


def tes_pixel_state(value: int, mask: int) -> tuple[str, float | None, str | None]:
    """Return (state, value, reason) for one TES pixel."""
    if mask == 0:
        return "no_coverage", None, "interpolated_pixel_excluded_by_mask"
    if not 5 <= value <= 5000:
        return "unknown", None, "value_outside_published_5_5000_range"
    return "detected", float(value), None


def tes_spatial_support(line0: int, sample0: int) -> dict[str, Any]:
    lat, lon = tes_pixel_center(line0, sample0)
    x = TES_BODY_RADIUS_KM * 1000.0 * math.radians(lon)
    y = TES_BODY_RADIUS_KM * 1000.0 * math.radians(lat)
    half = TES_PIXEL_RESOLUTION_M / 2.0
    return {
        "kind": "bbox",
        "crs": TES_NATIVE_CRS,
        "bbox": {"west": x - half, "south": y - half, "east": x + half, "north": y + half},
        "bbox_crs": TES_NATIVE_CRS,
        "native_resolution_m": TES_PIXEL_RESOLUTION_M,
        "source_resolution_m": TES_PIXEL_RESOLUTION_M,
        "notes": (
            "Nearest native 2.96 km TES TIMAP pixel to the reference-window centre. "
            "The pixel is larger than the 1.98 km window; this is coarse context, "
            "not a window mean."
        ),
    }


def tes_observation_payload(
    *,
    reference_id: str,
    display_name: str,
    lat_deg: float,
    lon_deg: float,
    line0: int,
    sample0: int,
    phase: str,
    raw_value: int,
    mask: int,
    asset_id: str,
    source_manifest: str,
) -> dict[str, Any]:
    state, value, reason = tes_pixel_state(raw_value, mask)
    method = TES_METHOD_DAY if phase == "day" else TES_METHOD_NIGHT
    product_id = TES_DAY_PRODUCT_ID if phase == "day" else TES_NIGHT_PRODUCT_ID
    return {
        "schema_version": "2.0.0",
        "observation_id": f"tes-timap:{reference_id}:thermal_inertia:{phase}",
        "area_id": f"mars-tes-pixel-l{line0 + 1:04d}-s{sample0 + 1:04d}",
        "body": MARS,
        "dimension_code": D03,
        "feature_id": "thermal_inertia",
        "value_kind": "numeric",
        "state": state,
        "value": value,
        "unit": TES_UNIT,
        "uncertainty": {
            "kind": "published_range",
            "value": None,
            "lower": None,
            "upper": None,
            "unit": TES_UNIT,
            "notes": (
                "No per-pixel uncertainty layer is supplied; the data set refers to "
                "Putzig and Mellon (2007). The published derived range is 5-5000 "
                "J m^-2 K^-1 s^-1/2."
            ),
        },
        "spatial_support": tes_spatial_support(line0, sample0),
        "temporal_support": {
            "kind": "static",
            "start": None,
            "end": None,
            "local_solar_time": "dayside" if phase == "day" else "nightside",
            "notes": (
                "Three Mars years of TES observations (1999-2004) combined into a "
                "day/night map; per-pixel acquisition time is not carried."
            ),
        },
        "depth_support": {"kind": "unknown", "min_m": None, "max_m": None},
        "method_version": method,
        "dataset_ids": [TES_DATASET_ID],
        "source_asset_ids": [asset_id],
        "quality_flags": [
            "coarse_context_2p96km",
            "interpolation_mask_applied",
            f"retrieval_phase_{phase}",
            "not_themis_100m",
            "not_cross_body_comparable",
        ],
        "score_role": "context_only",
        "legacy_provenance": {
            "reference_id": reference_id,
            "display_name": display_name,
            "reference_latitude_deg": lat_deg,
            "reference_longitude_deg": lon_deg,
            "source_manifest": source_manifest,
            "product_id": product_id,
            "retrieval_phase": phase,
            "line": line0 + 1,
            "sample": sample0 + 1,
            "mask_value": mask,
            "raw_uint16": raw_value,
            "reason_code": reason,
            "doi": TES_DOI,
        },
        "notes": (
            "MGS TES TIMAP apparent thermal inertia (day or night retrieval) at the "
            "nearest native 2.96 km pixel. Different retrieval and support from the "
            "committed THEMIS 100 m product; never mixed with it."
        ),
    }


# ---------------------------------------------------------------------------
# Odyssey NS (0.5 degree Mars measured proxy)
# ---------------------------------------------------------------------------


def odyssey_ns_cell_indices(lon_deg: float, lat_deg: float) -> tuple[int, int]:
    if not (-90.0 <= lat_deg <= 90.0):
        raise ValueError("Odyssey NS latitude out of range")
    lon360 = lon_deg % 360.0
    lat_index = int(math.floor((lat_deg + 90.0) / ODYSSEY_NS_PIXEL_DEG))
    lon_index = int(math.floor(lon360 / ODYSSEY_NS_PIXEL_DEG))
    return min(max(lon_index, 0), ODYSSEY_NS_LON_BINS - 1), min(
        max(lat_index, 0), ODYSSEY_NS_LAT_BINS - 1
    )


def odyssey_ns_cell_center(lon_index: int, lat_index: int) -> tuple[float, float]:
    lon360 = (lon_index + 0.5) * ODYSSEY_NS_PIXEL_DEG
    lon = lon360 if lon360 <= 180.0 else lon360 - 360.0
    lat = -90.0 + (lat_index + 0.5) * ODYSSEY_NS_PIXEL_DEG
    return lon, lat


def odyssey_ns_row_index(lon_index: int, lat_index: int) -> int:
    return lat_index * ODYSSEY_NS_LON_BINS + lon_index


def odyssey_ns_row_byte_range(row_index: int) -> tuple[int, int]:
    start = row_index * ODYSSEY_NS_ROW_BYTES
    return start, start + ODYSSEY_NS_ROW_BYTES - 1


def parse_odyssey_ns_row(text: str) -> dict[str, float]:
    clean = text.replace("\r", "").replace("\n", "").strip()
    parts = [part.strip() for part in clean.split(",")]
    if len(parts) != 3:
        raise ValueError(f"Odyssey NS row has {len(parts)} columns, expected 3")
    return {
        "longitude": float(parts[0]),
        "latitude": float(parts[1]),
        "counts_per_second": float(parts[2]),
    }


def odyssey_ns_state(counts_per_second: float) -> tuple[str, float | None, str | None]:
    if not math.isfinite(counts_per_second):
        return "unknown", None, "non_finite_value"
    if counts_per_second <= 0.0:
        return "no_coverage", None, "non_positive_value_no_documented_fill_constant"
    return "detected", counts_per_second, None


def odyssey_ns_spatial_support(lon_index: int, lat_index: int) -> dict[str, Any]:
    lon, lat = odyssey_ns_cell_center(lon_index, lat_index)
    half = ODYSSEY_NS_PIXEL_DEG / 2.0
    return {
        "kind": "bbox",
        "crs": MARS_GEOGRAPHIC_CRS,
        "bbox": {
            "west": lon - half,
            "south": lat - half,
            "east": lon + half,
            "north": lat + half,
        },
        "bbox_crs": MARS_GEOGRAPHIC_CRS,
        "native_resolution_m": ODYSSEY_NS_NATIVE_RESOLUTION_M,
        "source_resolution_m": ODYSSEY_NS_NATIVE_RESOLUTION_M,
        "notes": (
            "Native 0.5 x 0.5 degree Odyssey NS cell containing the reference-window "
            "centre; much coarser than the 1.98 km window and not an AOI mean."
        ),
    }


def odyssey_ns_evidence_payload(
    *,
    reference_id: str,
    display_name: str,
    lat_deg: float,
    lon_deg: float,
    lon_index: int,
    lat_index: int,
    row: Mapping[str, float],
    asset_id: str,
    source_manifest: str,
) -> dict[str, Any]:
    state, value, reason = odyssey_ns_state(float(row["counts_per_second"]))
    cell_lon, cell_lat = odyssey_ns_cell_center(lon_index, lat_index)
    return {
        "evidence_id": f"odyssey-ns:{reference_id}:{ODYSSEY_NS_QUANTITY_ID}",
        "area_id": f"mars-ns-cell-s{cell_lat:06.2f}-e{cell_lon:07.2f}",
        "body": MARS,
        "dimension_code": D04,
        "quantity_id": ODYSSEY_NS_QUANTITY_ID,
        "value_kind": "numeric",
        "state": state,
        "value": value,
        "unit": "counts/second",
        "evidence_class": "measured_proxy",
        "spatial_support": odyssey_ns_spatial_support(lon_index, lat_index),
        "temporal_support": {
            "kind": "unknown",
            "start": None,
            "end": None,
            "local_solar_time": None,
            "notes": "First 25.7 days of Odyssey mapping (2002); exact interval not in the label.",
        },
        "depth_support": {"kind": "depth_interval", "min_m": 0.0, "max_m": 1.0},
        "method_version": ODYSSEY_NS_METHOD_VERSION,
        "source": {
            "dataset_id": ODYSSEY_NS_DATASET_ID,
            "product_id": ODYSSEY_NS_PRODUCT_ID,
            "url": ODYSSEY_NS_URL,
            "doi": ODYSSEY_NS_DOI,
            "citation": ODYSSEY_NS_CITATION,
            "license": PDS_LICENSE,
            "license_url": PDS_LICENSE_URL,
            "source_asset_id": asset_id,
        },
        "quality_flags": [
            "measured_neutron_proxy",
            "counts_per_second_not_normalized_flux",
            "not_comparable_to_hend_flux_ratio",
            "no_documented_fill_constant",
            "planetary_planetocentric_lonlat",
            "not_an_ice_detection",
        ],
        "uncertainty": {
            "kind": "unknown",
            "value": None,
            "lower": None,
            "upper": None,
            "unit": "counts/second",
            "notes": "No per-cell uncertainty column.",
        },
        "legacy_provenance": {
            "reference_id": reference_id,
            "display_name": display_name,
            "reference_latitude_deg": lat_deg,
            "reference_longitude_deg": lon_deg,
            "source_manifest": source_manifest,
            "ns_cell_center_lon_deg": cell_lon,
            "ns_cell_center_lat_deg": cell_lat,
            "ns_row_index": odyssey_ns_row_index(lon_index, lat_index),
            "reason_code": reason,
            "not_comparable_to": "epithermal_neutron_flux_ratio (HEND, 4 degree, different normalization)",
        },
        "notes": (
            "Odyssey NS epithermal counts/second at the native 0.5 degree cell. "
            "It is a hydrogen proxy, not the normalized HEND flux ratio, not an "
            "ice abundance and not an ice detection."
        ),
    }


# ---------------------------------------------------------------------------
# Diviner PRP (modeled Moon annual temperature and ice stability depth)
# ---------------------------------------------------------------------------


def parse_prp_row(text: str) -> dict[str, Any]:
    clean = text.replace("\r", "").replace("\n", "")
    parts = [part.strip() for part in clean.split(",")]
    if len(parts) != 15:
        raise ValueError(f"PRP row has {len(parts)} columns, expected 15")
    values = [float(part) for part in parts]
    return {
        "vertices": (
            (values[0], values[1], values[2]),
            (values[3], values[4], values[5]),
            (values[6], values[7], values[8]),
        ),
        "clon": values[9],
        "clat": values[10],
        "calt": values[11],
        "temp_avg": values[12],
        "temp_max": values[13],
        "ice_depth": values[14],
    }


def prp_triangle_area_km2(row: Mapping[str, Any]) -> float:
    v1, v2, v3 = row["vertices"]
    return triangle_area_km2(v1, v2, v3)


def prp_valid_temperature(value: float) -> bool:
    return math.isfinite(value) and 0.0 < value < 500.0


def prp_valid_ice_depth(value: float) -> bool:
    return math.isfinite(value) and 0.0 <= value <= PRP_MAX_DEPTH_M


def select_prp_triangles(
    rows: Iterable[Mapping[str, Any]], lat_deg: float, lon_deg: float, radius_m: float
) -> list[dict[str, Any]]:
    """Select PRP triangles whose published centroid lies inside the circular window."""
    selected: list[dict[str, Any]] = []
    for row in rows:
        distance = angular_distance_m(
            lat_deg, lon_deg, float(row["clat"]), float(row["clon"]), MOON_RADIUS_KM
        )
        if distance <= radius_m:
            item = dict(row)
            item["centroid_distance_m"] = distance
            item["area_km2"] = prp_triangle_area_km2(row)
            selected.append(item)
    return selected


def _weighted_mean(values: Sequence[float], weights: Sequence[float]) -> float:
    total = math.fsum(weights)
    if total <= 0:
        raise ValueError("weights must sum to a positive value")
    return math.fsum(value * weight for value, weight in zip(values, weights, strict=True)) / total


def prp_aggregate(
    triangles: Sequence[Mapping[str, Any]], *, window_size_m: float
) -> dict[str, Any]:
    """Aggregate selected PRP triangles to one AOI-scale modeled evidence record."""
    if not triangles:
        return {
            "state": "no_coverage",
            "reason": "no_prp_triangle_centroid_inside_window",
            "n_triangles": 0,
            "temperature": None,
            "ice_depth": None,
            "coverage": {"window_area_km2": math.pi * (window_size_m / 2000.0) ** 2},
        }

    weights = [float(row["area_km2"]) for row in triangles]
    coverage = {
        "n_triangles": len(triangles),
        "triangle_area_sum_km2": math.fsum(weights),
        "window_area_km2": math.pi * (window_size_m / 2000.0) ** 2,
        "selection": "published triangle centroid inside circular window",
    }
    coverage["triangle_area_ratio_of_window"] = min(
        1.0, coverage["triangle_area_sum_km2"] / coverage["window_area_km2"]
    )

    avg_pairs = [
        (float(row["temp_avg"]), weight)
        for row, weight in zip(triangles, weights, strict=True)
        if prp_valid_temperature(float(row["temp_avg"]))
    ]
    max_pairs = [
        (float(row["temp_max"]), weight)
        for row, weight in zip(triangles, weights, strict=True)
        if prp_valid_temperature(float(row["temp_max"]))
    ]
    temperature: dict[str, Any] | None = None
    if avg_pairs and max_pairs:
        avg_values = [value for value, _ in avg_pairs]
        avg_weights = [weight for _, weight in avg_pairs]
        max_values = [value for value, _ in max_pairs]
        max_weights = [weight for _, weight in max_pairs]
        temperature = {
            "annual_mean_2cm_k": _weighted_mean(avg_values, avg_weights),
            "annual_mean_2cm_min_k": min(avg_values),
            "annual_mean_2cm_max_k": max(avg_values),
            "annual_mean_2cm_std_k": _weighted_std(avg_values, avg_weights),
            "annual_max_surface_mean_k": _weighted_mean(max_values, max_weights),
            "annual_max_surface_max_k": max(max_values),
            "valid_triangle_count": len(avg_values),
            "invalid_triangle_count": len(triangles) - len(avg_values),
        }

    depth_pairs = [
        (float(row["ice_depth"]), weight)
        for row, weight in zip(triangles, weights, strict=True)
        if prp_valid_ice_depth(float(row["ice_depth"]))
    ]
    excluded = sum(1 for row in triangles if float(row["ice_depth"]) == PRP_INVALID_DEPTH_M)
    invalid = len(triangles) - len(depth_pairs) - excluded
    total_area = math.fsum(weights)
    ice_depth: dict[str, Any] | None = None
    if depth_pairs:
        depth_values = [value for value, _ in depth_pairs]
        depth_weights = [weight for _, weight in depth_pairs]
        valid_area = math.fsum(depth_weights)
        excluded_area = math.fsum(
            weight
            for row, weight in zip(triangles, weights, strict=True)
            if float(row["ice_depth"]) == PRP_INVALID_DEPTH_M
        )
        ice_depth = {
            "mean_stability_depth_m": _weighted_mean(depth_values, depth_weights),
            "min_stability_depth_m": min(depth_values),
            "max_stability_depth_m": max(depth_values),
            "surface_stable_triangle_count": sum(1 for value in depth_values if value == 0.0),
            "valid_triangle_count": len(depth_values),
            "excluded_gt_2_8738m_count": excluded,
            "invalid_triangle_count": invalid,
            "valid_area_fraction": valid_area / total_area if total_area else 0.0,
            "excluded_area_fraction": excluded_area / total_area if total_area else 0.0,
        }

    state = "detected" if temperature is not None or ice_depth is not None else "unknown"
    reason = None
    if state != "detected":
        reason = "all_prp_triangles_invalid_or_no_temperature"
    return {
        "state": state,
        "reason": reason,
        "n_triangles": len(triangles),
        "temperature": temperature,
        "ice_depth": ice_depth,
        "coverage": coverage,
    }


def _weighted_std(values: Sequence[float], weights: Sequence[float]) -> float:
    mean = _weighted_mean(values, weights)
    total = math.fsum(weights)
    variance = (
        math.fsum(
            weight * (value - mean) ** 2 for value, weight in zip(values, weights, strict=True)
        )
        / total
    )
    return math.sqrt(max(0.0, variance))


# ---------------------------------------------------------------------------
# Existing #43/#44 observations -> AOI index
# ---------------------------------------------------------------------------


def index_existing_observations(
    observations: Iterable[Mapping[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Group existing D03/D04 observations by reference id without altering them."""
    index: dict[str, list[dict[str, Any]]] = {}
    for observation in observations:
        if observation.get("dimension_code") not in {D03, D04}:
            continue
        legacy = observation.get("legacy_provenance") or {}
        reference_ids: list[str] = []
        if legacy.get("reference_id"):
            reference_ids.append(str(legacy["reference_id"]))
        for member in legacy.get("member_reference_ids") or []:
            if member not in reference_ids:
                reference_ids.append(str(member))
        for reference_id in reference_ids:
            index.setdefault(reference_id, []).append(dict(observation))
    return index


def summarize_existing(
    observations: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Compact, value-preserving summary of existing observations for an AOI."""
    rows: list[dict[str, Any]] = []
    for observation in sorted(
        observations,
        key=lambda item: (
            str(item.get("dimension_code")),
            str(item.get("feature_id")),
            str(item.get("observation_id")),
        ),
    ):
        legacy = observation.get("legacy_provenance") or {}
        rows.append(
            {
                "observation_id": observation["observation_id"],
                "dimension_code": observation.get("dimension_code"),
                "feature_id": observation.get("feature_id"),
                "state": observation.get("state"),
                "value": observation.get("value"),
                "unit": observation.get("unit"),
                "method_version": observation.get("method_version"),
                "local_time_bin_hours": legacy.get("local_time_bin_hours"),
                "native_resolution_m": legacy.get("native_resolution_m"),
                "reused_from": "committed #43/#44 pilot",
            }
        )
    return rows
