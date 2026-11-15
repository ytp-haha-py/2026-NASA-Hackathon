"""Bounded planetary D03 thermal evidence from official public products (Issue #43).

Two verified, bounded products are supported here:

* Moon: LRO Diviner Polar Cumulative Products (PCP), bolometric brightness
  temperature, 240 m bins, 0.25 h local-time bins, south polar cap 80-90 S.
* Mars: USGS THEMIS-derived global thermal inertia mosaic, quantitative 32-bit,
  100 m/pixel, ISIS3 tiled cube. Only small byte ranges/tiles are read.

Both quantities are radiometric/derived evidence, never a kinetic surface
temperature, and never a cross-body similarity score. Thermal inertia and
brightness temperature remain separate features.

The module contains no network access; callers provide already-downloaded PCP
text or decoded ISIS tile bytes so the semantics can be tested offline. Live
download is opt-in in ``scripts/build_planetary_thermal_pilot.py``.
"""

from __future__ import annotations

import math
import struct
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MOON_BODY = "Moon"
MARS_BODY = "Mars"

BRIGHTNESS_TEMPERATURE_FEATURE = "surface_brightness_temperature_k"
THERMAL_INERTIA_FEATURE = "thermal_inertia"
MOON_METHOD_VERSION = "diviner-pcp-tbol-1.0.0"
MARS_METHOD_VERSION = "themis-apparent-thermal-inertia-1.0.0"

MOON_RADIUS_M = 1_737_400.0
MOON_PCP_CRS = (
    "+proj=stere +lat_0=-90 +lat_ts=-90 +lon_0=0 +x_0=0 +y_0=0 +R=1737400 +units=m +no_defs"
)
MARS_RADIUS_M = 3_396_190.0
MARS_TI_CRS = "+proj=eqc +lat_ts=0 +lat_0=0 +lon_0=0 +x_0=0 +y_0=0 +R=3396190 +units=m +no_defs"

PCP_BUNDLE_LID = "urn:nasa:pds:lro_diviner_derived1"
PCP_COLLECTION_LID = "urn:nasa:pds:lro_diviner_derived1:data_derived_pcp"
PCP_COLLECTION_URL = (
    "https://pds-geosciences.wustl.edu/lro/urn-nasa-pds-lro_diviner_derived1/data_derived_pcp/"
)
PCP_DATASET_ID = "lro-diviner-pcp-pols-summer-240m"
PCP_PRODUCT_VERSION = "1.0"
PCP_CALIBRATION_SOFTWARE_VERSION = "4.0"
PCP_DOI = "10.1029/2019JE006028"
PCP_CITATION = (
    "Williams, J.-P., Greenhagen, B.T., Paige, D.A., Schorghofer, N., Sefton-Nash, E., "
    "Hayne, P.O., Lucey, P.G., Siegler, M.A., Aye, K.-M. (2019), Seasonal polar "
    "temperatures on the Moon, J. Geophys. Res. 124, 2505-2521, "
    "doi:10.1029/2019JE006028"
)
PCP_NATIVE_RESOLUTION_M = 240.0
PCP_LATITUDE_RANGE_DEG = (-90.0, -80.0)
PCP_LONGITUDE_RANGE_DEG = (-180.0, 180.0)
PCP_TEMPORAL_START = "2009-07-05T16:50:26.195Z"
PCP_TEMPORAL_END = "2019-02-17T00:00:00.000Z"
PCP_SEASON = "southern_summer (subsolar latitude < 0 deg)"
PCP_LOCAL_TIME_BIN_HOURS = 0.25
PCP_SPECIAL_CONSTANTS = (-9998.0, -9999.0)


@dataclass(frozen=True, slots=True)
class PcpBin:
    """One PCP local-time bin file with verified PDS metadata."""

    product_id: str
    local_time_start_hours: float
    local_time_end_hours: float
    url: str
    source_bytes: int

    @property
    def local_time_label(self) -> str:
        start_minutes = round(self.local_time_start_hours * 60)
        end_minutes = round(self.local_time_end_hours * 60)
        return f"{start_minutes // 60:02d}:{start_minutes % 60:02d}-{end_minutes // 60:02d}:{end_minutes % 60:02d}"

    @property
    def bin_id(self) -> str:
        return self.product_id.lower().removesuffix(".tab")


def _pcp_url(filename: str) -> str:
    return (
        "https://pds-geosciences.wustl.edu/lro/urn-nasa-pds-lro_diviner_derived1/"
        f"data_derived_pcp/diurnal/ltim/pols/{filename.lower()}"
    )


PCP_BINS: tuple[PcpBin, ...] = (
    PcpBin(
        product_id="PCP_AVG_TBOL_POLS_SUM_LTIM01_240.TAB",
        local_time_start_hours=0.0,
        local_time_end_hours=0.25,
        url=_pcp_url("pcp_avg_tbol_pols_sum_ltim01_240.tab"),
        source_bytes=212_653_759,
    ),
    PcpBin(
        product_id="PCP_AVG_TBOL_POLS_SUM_LTIM49_240.TAB",
        local_time_start_hours=12.0,
        local_time_end_hours=12.25,
        url=_pcp_url("pcp_avg_tbol_pols_sum_ltim49_240.tab"),
        source_bytes=197_367_449,
    ),
)
PCP_BIN_BY_ID = {item.bin_id: item for item in PCP_BINS}

MARS_TI_DATASET_ID = "usgs-mars-themis-global-thermal-inertia-quantitative"
MARS_TI_PRODUCT_ID = "THEMIS_TI_Mosaic_Quant_00N060E_100mpp"
MARS_TI_PRODUCT_URL = (
    "https://astrogeology.usgs.gov/search/map/"
    "themis_thermal_inertia_mosaic_quantitative_00n060e_100mpp"
)
MARS_TI_S3_URL = (
    "https://asc-astropedia.s3.us-west-2.amazonaws.com/Mars/Odyssey/"
    "THEMIS-Global-Thermal-Inertia-Mosaic/Quantitative-32-Bit/"
    "THEMIS_TI_Mosaic_Quant_00N060E_100mpp.cub"
)
MARS_TI_VERSION = "edition 1 (2014-05-30)"
MARS_TI_NATIVE_RESOLUTION_M = 100.0
MARS_TI_ABSOLUTE_ACCURACY_FRACTION = 0.20
MARS_TI_RELATIVE_ACCURACY_FRACTION = 0.10
MARS_TI_CITATION = (
    "Fergason, R.L., Christensen, P.R., Kieffer, H.H. (2006), High-resolution thermal "
    "inertia derived from the Thermal Emission Imaging System (THEMIS): Thermal model "
    "and applications, J. Geophys. Res. 111, E12004, doi:10.1029/2006JE002735"
)
MARS_TI_DOI = "10.1029/2006JE002735"
MARS_TI_UNIT = "J m^-2 K^-1 s^-1/2"
MARS_TI_S3_BYTES = 2_532_510_198


@dataclass(frozen=True, slots=True)
class IsisCubeTile:
    """Verified ISIS3 tiled-cube geometry for one quantitative mosaic tile."""

    product_id: str
    url: str
    samples: int
    lines: int
    tile_samples: int
    tile_lines: int
    start_byte: int
    ulx_m: float
    uly_m: float
    pixel_resolution_m: float
    min_latitude_deg: float
    max_latitude_deg: float
    min_longitude_deg: float
    max_longitude_deg: float
    body_radius_m: float = MARS_RADIUS_M
    dtype: str = "<f"

    @property
    def tiles_per_row(self) -> int:
        return math.ceil(self.samples / self.tile_samples)

    @property
    def tile_count(self) -> int:
        return math.ceil(self.lines / self.tile_lines) * self.tiles_per_row

    @property
    def tile_bytes(self) -> int:
        return self.tile_samples * self.tile_lines * struct.calcsize(self.dtype)

    def tile_index(self, tile_row: int, tile_col: int) -> int:
        return tile_row * self.tiles_per_row + tile_col

    def tile_byte_range(self, tile_row: int, tile_col: int) -> tuple[int, int]:
        offset = self.start_byte - 1 + self.tile_index(tile_row, tile_col) * self.tile_bytes
        return offset, offset + self.tile_bytes - 1

    def sample_line_for(self, latitude_deg: float, longitude_deg: float) -> tuple[float, float]:
        """Planetocentric simple-cylindrical pixel coordinates.

        The mosaic labels define UpperLeftCornerX/Y at the upper-left pixel edge.
        Pixel centres are therefore half a pixel inside; the half-pixel offset is
        recorded, not hidden.
        """
        x = self.body_radius_m * math.radians(longitude_deg)
        y = self.body_radius_m * math.radians(latitude_deg)
        sample = (x - self.ulx_m) / self.pixel_resolution_m - 0.5
        line = (self.uly_m - y) / self.pixel_resolution_m - 0.5
        return sample, line

    def tile_range_for_window(
        self, sample: float, line: float, radius_m: float
    ) -> tuple[range, range]:
        radius_px = radius_m / self.pixel_resolution_m
        first_col = math.floor((sample - radius_px) / self.tile_samples)
        last_col = math.floor((sample + radius_px) / self.tile_samples)
        first_row = math.floor((line - radius_px) / self.tile_lines)
        last_row = math.floor((line + radius_px) / self.tile_lines)
        max_col = math.ceil(self.samples / self.tile_samples) - 1
        max_row = math.ceil(self.lines / self.tile_lines) - 1
        return (
            range(max(0, first_row), min(max_row, last_row) + 1),
            range(max(0, first_col), min(max_col, last_col) + 1),
        )


MARS_TI_TILE = IsisCubeTile(
    product_id=MARS_TI_PRODUCT_ID,
    url=MARS_TI_S3_URL,
    samples=35_566,
    lines=17_783,
    tile_samples=128,
    tile_lines=128,
    start_byte=65_537,
    ulx_m=3_556_400.0,
    uly_m=1_778_300.0,
    pixel_resolution_m=100.0,
    min_latitude_deg=0.0,
    max_latitude_deg=30.0,
    min_longitude_deg=60.0,
    max_longitude_deg=120.0,
)


@dataclass(frozen=True, slots=True)
class WindowSamples:
    """Valid values inside a bounded window plus explicit missingness counters."""

    values: tuple[float, ...]
    rows_scanned: int
    rejected: int
    expected_bins: float | None = None

    @property
    def n(self) -> int:
        return len(self.values)


def compute_window_statistics(samples: WindowSamples) -> dict[str, Any]:
    values = samples.values
    if not values:
        return {
            "n": 0,
            "mean": None,
            "min": None,
            "max": None,
            "std": None,
            "valid_fraction": 0.0,
            "rows_scanned": samples.rows_scanned,
            "rejected": samples.rejected,
        }
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / len(values)
    valid_fraction = 0.0
    if samples.expected_bins:
        valid_fraction = min(1.0, len(values) / samples.expected_bins)
    return {
        "n": len(values),
        "mean": mean,
        "min": min(values),
        "max": max(values),
        "std": math.sqrt(variance),
        "valid_fraction": valid_fraction,
        "rows_scanned": samples.rows_scanned,
        "rejected": samples.rejected,
    }


def parse_pcp_row(line: str | bytes) -> tuple[float, float, float, float, float]:
    """Parse one fixed-width PCP row: x, y, clon, clat, tbol.

    The PDS label fixes ROW_BYTES = 59 and five comma-separated ASCII reals.
    No value is silently replaced; invalid/unknown special constants raise.
    """
    text = line.decode("ascii") if isinstance(line, bytes) else line
    text = text.strip()
    if not text:
        raise ValueError("empty PCP row")
    parts = text.split(",")
    if len(parts) != 5:
        raise ValueError(f"PCP row has {len(parts)} columns, expected 5")
    try:
        values = tuple(float(part) for part in parts)
    except ValueError as exc:
        raise ValueError(f"PCP row is not numeric: {text!r}") from exc
    if any(not math.isfinite(value) for value in values):
        raise ValueError(f"PCP row contains a non-finite value: {text!r}")
    if any(value in PCP_SPECIAL_CONSTANTS for value in values):
        raise ValueError(f"PCP row contains an invalid/unknown special constant: {text!r}")
    return values  # type: ignore[return-value]


def parse_pcp_header(line: str | bytes) -> list[str]:
    text = line.decode("ascii") if isinstance(line, bytes) else line
    names = [name.strip() for name in text.strip().split(",")]
    if names != ["x", "y", "clon", "clat", "tbol"]:
        raise ValueError(f"unexpected PCP header: {names}")
    return names


def pcp_normalized_xy(latitude_deg: float, longitude_deg: float) -> tuple[float, float]:
    """Polar-stereographic x/y normalised by the 1737.4 km mean lunar radius.

    This reproduces the product's own convention exactly: x = r*sin(lon),
    y = r*cos(lon), r = 2*tan((90+lat)/2). It is verified against rows in the
    published south-polar file and against the existing LOLA Site04 window centre.
    """
    if not -90.0 <= latitude_deg <= -80.0:
        raise ValueError("PCP south file only covers -90 to -80 degrees latitude")
    if not -180.0 <= longitude_deg <= 180.0:
        raise ValueError("PCP longitude must be east-positive in [-180, 180]")
    radius = 2.0 * math.tan(math.radians(90.0 + latitude_deg) / 2.0)
    return (
        radius * math.sin(math.radians(longitude_deg)),
        radius * math.cos(math.radians(longitude_deg)),
    )


def pcp_center_xy_m(latitude_deg: float, longitude_deg: float) -> tuple[float, float]:
    x, y = pcp_normalized_xy(latitude_deg, longitude_deg)
    return x * MOON_RADIUS_M, y * MOON_RADIUS_M


def select_pcp_window(
    path: str | Path,
    *,
    latitude_deg: float,
    longitude_deg: float,
    radius_m: float,
    minimum_kelvin: float = 1.0,
    maximum_kelvin: float = 1_000.0,
) -> WindowSamples:
    """Read a local PCP .tab file and select rows inside a circular window.

    The file is sparse: empty bins are absent, not zero. Rejected rows are
    counted and reported. Nothing is resampled to 90 m or interpolated.
    """
    if radius_m <= 0:
        raise ValueError("radius_m must be positive")
    center_x, center_y = pcp_center_xy_m(latitude_deg, longitude_deg)
    values: list[float] = []
    rows_scanned = 0
    rejected = 0
    with Path(path).open("rb") as handle:
        header = handle.readline()
        parse_pcp_header(header)
        for raw in handle:
            rows_scanned += 1
            try:
                x, y, _clon, _clat, tbol = parse_pcp_row(raw)
            except ValueError:
                rejected += 1
                continue
            distance = math.hypot(x * MOON_RADIUS_M - center_x, y * MOON_RADIUS_M - center_y)
            if distance > radius_m:
                continue
            if not minimum_kelvin <= tbol <= maximum_kelvin:
                rejected += 1
                continue
            values.append(tbol)
    expected = math.pi * (radius_m / PCP_NATIVE_RESOLUTION_M) ** 2
    return WindowSamples(
        values=tuple(values),
        rows_scanned=rows_scanned,
        rejected=rejected,
        expected_bins=expected,
    )


def select_pcp_windows(
    path: str | Path,
    *,
    centers: Mapping[str, tuple[float, float]],
    radius_m: float,
    minimum_kelvin: float = 1.0,
    maximum_kelvin: float = 1_000.0,
) -> dict[str, WindowSamples]:
    """Scan one PCP file once and collect several circular windows.

    ``centers`` maps a key to ``(latitude_deg, longitude_deg)``. Scanning once is
    a performance choice only; each window is still an independent selection and
    overlapping windows are not independent samples.
    """
    if radius_m <= 0:
        raise ValueError("radius_m must be positive")
    projected = {key: pcp_center_xy_m(lat, lon) for key, (lat, lon) in centers.items()}
    collected: dict[str, list[float]] = {key: [] for key in centers}
    rejected: dict[str, int] = {key: 0 for key in centers}
    rows_scanned = 0
    with Path(path).open("rb") as handle:
        parse_pcp_header(handle.readline())
        for raw in handle:
            rows_scanned += 1
            try:
                x, y, _clon, _clat, tbol = parse_pcp_row(raw)
            except ValueError:
                for key in centers:
                    rejected[key] += 1
                continue
            x_m, y_m = x * MOON_RADIUS_M, y * MOON_RADIUS_M
            for key, (center_x, center_y) in projected.items():
                if math.hypot(x_m - center_x, y_m - center_y) > radius_m:
                    continue
                if not minimum_kelvin <= tbol <= maximum_kelvin:
                    rejected[key] += 1
                    continue
                collected[key].append(tbol)
    expected = math.pi * (radius_m / PCP_NATIVE_RESOLUTION_M) ** 2
    return {
        key: WindowSamples(
            values=tuple(values),
            rows_scanned=rows_scanned,
            rejected=rejected[key],
            expected_bins=expected,
        )
        for key, values in collected.items()
    }


def decode_isis_tile(data: bytes, tile: IsisCubeTile) -> tuple[float, ...]:
    expected_bytes = tile.tile_bytes
    if len(data) != expected_bytes:
        raise ValueError(f"tile payload is {len(data)} bytes, expected {expected_bytes}")
    return struct.unpack(f"<{tile.tile_samples * tile.tile_lines}f", data)


def collect_ti_window(
    *,
    tile: IsisCubeTile,
    sample: float,
    line: float,
    radius_m: float,
    decoded_tiles: Mapping[tuple[int, int], Sequence[float]],
    minimum_ti: float = 1.0,
    maximum_ti: float = 10_000.0,
) -> WindowSamples:
    """Collect 100 m thermal-inertia pixels inside a circular window.

    The ISIS label carries no explicit no-data constant. Non-finite, non-positive
    and implausibly large values are rejected and counted rather than zero-filled.
    No interpolation or resampling to another grid is performed.
    """
    radius_px = radius_m / tile.pixel_resolution_m
    rows, cols = tile.tile_range_for_window(sample, line, radius_m)
    values: list[float] = []
    rejected = 0
    for tile_row in rows:
        for tile_col in cols:
            key = (tile_row, tile_col)
            if key not in decoded_tiles:
                raise ValueError(f"missing decoded ISIS tile {key}")
            pixels = decoded_tiles[key]
            if len(pixels) != tile.tile_samples * tile.tile_lines:
                raise ValueError(f"decoded ISIS tile {key} has the wrong number of pixels")
            for local_line in range(tile.tile_lines):
                global_line = tile_row * tile.tile_lines + local_line
                if global_line >= tile.lines:
                    continue
                for local_sample in range(tile.tile_samples):
                    global_sample = tile_col * tile.tile_samples + local_sample
                    if global_sample >= tile.samples:
                        continue
                    if math.hypot(global_sample - sample, global_line - line) > radius_px:
                        continue
                    value = pixels[local_line * tile.tile_samples + local_sample]
                    if math.isfinite(value) and minimum_ti <= value <= maximum_ti:
                        values.append(value)
                    else:
                        rejected += 1
    expected = math.pi * (radius_m / tile.pixel_resolution_m) ** 2
    return WindowSamples(
        values=tuple(values),
        rows_scanned=tile.tile_samples * tile.tile_lines * len(rows) * len(cols),
        rejected=rejected,
        expected_bins=expected,
    )


MINIMUM_WINDOW_BINS = 5


def observed_state(n: int) -> str:
    """Evidence state from actual sample support; never zero-fill missing bins."""
    if n == 0:
        return "no_coverage"
    if n < MINIMUM_WINDOW_BINS:
        return "unknown"
    return "detected"


def brightness_observation_payload(
    *,
    area_id: str,
    reference_id: str,
    display_name: str,
    latitude_deg: float,
    longitude_deg: float,
    bin_metadata: PcpBin,
    stats: dict[str, Any],
    window_size_m: float,
    source_manifest: str,
    dataset_id: str,
    asset_id: str,
) -> dict[str, Any]:
    """Build a typed-v2-compatible brightness-temperature observation payload.

    The value is the spatial mean of 240 m PCP bins inside the window at one
    local-time bin. It is a bolometric brightness temperature (unit-emissivity
    assumption), not a kinetic surface temperature, and not a diurnal range.
    """
    state = observed_state(int(stats["n"]))
    center_x, center_y = pcp_center_xy_m(latitude_deg, longitude_deg)
    half = window_size_m / 2.0
    bbox = {
        "west": center_x - half,
        "south": center_y - half,
        "east": center_x + half,
        "north": center_y + half,
    }
    value = None if state != "detected" else stats["mean"]
    return {
        "schema_version": "2.0.0",
        "observation_id": (
            f"diviner-pcp:{area_id}:{BRIGHTNESS_TEMPERATURE_FEATURE}:{bin_metadata.bin_id}"
        ),
        "area_id": area_id,
        "body": MOON_BODY,
        "dimension_code": "D03",
        "feature_id": BRIGHTNESS_TEMPERATURE_FEATURE,
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
            "notes": (
                "No per-bin uncertainty is supplied by the PCP product. "
                "The published paper describes the retrieval; no per-pixel "
                "uncertainty was inferred here."
            ),
        },
        "spatial_support": {
            "kind": "window",
            "crs": MOON_PCP_CRS,
            "resolution_m": PCP_NATIVE_RESOLUTION_M,
            "window_size_m": window_size_m,
            "bbox": bbox,
            "bbox_crs": MOON_PCP_CRS,
            "source_resolution_m": PCP_NATIVE_RESOLUTION_M,
            "native_resolution_m": PCP_NATIVE_RESOLUTION_M,
            "valid_fraction": stats["valid_fraction"],
            "notes": (
                "Circular 1.98 km window of native 240 m PCP bins; no resampling "
                "to 90 m and no interpolation of empty bins."
            ),
        },
        "temporal_support": {
            "kind": "composite",
            "start": PCP_TEMPORAL_START,
            "end": PCP_TEMPORAL_END,
            "local_solar_time": bin_metadata.local_time_label,
            "notes": (
                "Multi-year southern-summer composite (subsolar latitude < 0 deg) "
                "at one 0.25 h local-time bin; not a single acquisition and not a "
                "same-sol time series."
            ),
        },
        "depth_support": {
            "kind": "surface",
            "min_m": None,
            "max_m": None,
        },
        "method_version": MOON_METHOD_VERSION,
        "dataset_ids": [dataset_id],
        "source_asset_ids": [asset_id],
        "quality_flags": [
            "official_pds4_product",
            "diviner_pcp_composite",
            "bolometric_brightness_temperature",
            "unit_emissivity_assumption",
            "not_kinetic_surface_temperature",
            "native_240m",
            "not_resampled_90m",
            "sparse_bins_empty_omitted",
            "no_per_bin_uncertainty",
            "multi_year_composite_no_single_acquisition",
            "no_diurnal_range_computed",
            "south_polar_cap",
            "polar_longitude_ill_conditioned",
        ],
        "score_role": "evidence_only",
        "evidence": None,
        "legacy_provenance": {
            "reference_id": reference_id,
            "display_name": display_name,
            "source_manifest": source_manifest,
            "reference_latitude_deg": latitude_deg,
            "reference_longitude_deg": longitude_deg,
            "pcp_product_id": bin_metadata.product_id,
            "pcp_product_version": PCP_PRODUCT_VERSION,
            "pcp_calibration_software_version": PCP_CALIBRATION_SOFTWARE_VERSION,
            "pcp_collection_lid": PCP_COLLECTION_LID,
            "pcp_doi": PCP_DOI,
            "pcp_citation": PCP_CITATION,
            "physical_quantity": "bolometric_brightness_temperature",
            "emissivity_assumption": "unit emissivity (brightness temperature, not kinetic)",
            "season": PCP_SEASON,
            "local_time_bin_hours": [
                bin_metadata.local_time_start_hours,
                bin_metadata.local_time_end_hours,
            ],
            "native_resolution_m": PCP_NATIVE_RESOLUTION_M,
            "window_center_map_m": [center_x, center_y],
            "window_radius_m": window_size_m / 2.0,
            "spatial_n": stats["n"],
            "spatial_mean_k": stats["mean"],
            "spatial_min_k": stats["min"],
            "spatial_max_k": stats["max"],
            "spatial_std_k": stats["std"],
            "rows_scanned": stats["rows_scanned"],
            "rejected_rows_in_window": stats["rejected"],
            "expected_bins_in_window": round(
                math.pi * (window_size_m / 2.0 / PCP_NATIVE_RESOLUTION_M) ** 2, 3
            ),
            "diurnal_range_computed": False,
        },
        "notes": (
            "Observed Diviner PCP bolometric brightness temperature (unit emissivity), "
            "native 240 m, one 0.25 h local-time bin over a multi-year southern-summer "
            "composite. Not kinetic surface temperature, not a same-sol diurnal range, "
            "and not comparable to Earth 2 m air or skin temperature."
        ),
    }


def thermal_inertia_observation_payload(
    *,
    area_id: str,
    reference_id: str,
    display_name: str,
    latitude_deg: float,
    longitude_deg: float,
    stats: dict[str, Any],
    window_size_m: float,
    source_manifest: str,
    dataset_id: str,
    asset_id: str,
    tile: IsisCubeTile,
) -> dict[str, Any]:
    """Build a typed-v2-compatible apparent thermal-inertia observation payload."""
    state = observed_state(int(stats["n"]))
    sample, line = tile.sample_line_for(latitude_deg, longitude_deg)
    x = tile.body_radius_m * math.radians(longitude_deg)
    y = tile.body_radius_m * math.radians(latitude_deg)
    half = window_size_m / 2.0
    bbox = {
        "west": x - half,
        "south": y - half,
        "east": x + half,
        "north": y + half,
    }
    value = None if state != "detected" else stats["mean"]
    uncertainty = None
    if value is not None:
        uncertainty = {
            "kind": "published_range",
            "value": None,
            "lower": value * (1.0 - MARS_TI_ABSOLUTE_ACCURACY_FRACTION),
            "upper": value * (1.0 + MARS_TI_ABSOLUTE_ACCURACY_FRACTION),
            "unit": MARS_TI_UNIT,
            "notes": (
                "Published absolute accuracy ~20% (Fergason et al., 2006); relative "
                "accuracy within one image ~10%. No per-pixel uncertainty layer exists."
            ),
        }
    return {
        "schema_version": "2.0.0",
        "observation_id": f"themis-ti:{area_id}:{THERMAL_INERTIA_FEATURE}",
        "area_id": area_id,
        "body": MARS_BODY,
        "dimension_code": "D03",
        "feature_id": THERMAL_INERTIA_FEATURE,
        "value_kind": "numeric",
        "state": state,
        "value": value,
        "unit": MARS_TI_UNIT,
        "uncertainty": uncertainty,
        "spatial_support": {
            "kind": "window",
            "crs": MARS_TI_CRS,
            "resolution_m": tile.pixel_resolution_m,
            "window_size_m": window_size_m,
            "bbox": bbox,
            "bbox_crs": MARS_TI_CRS,
            "source_resolution_m": tile.pixel_resolution_m,
            "native_resolution_m": tile.pixel_resolution_m,
            "valid_fraction": stats["valid_fraction"],
            "notes": (
                "Circular 1.98 km window of native 100 m THEMIS TI pixels; no "
                "resampling to 90 m and no interpolation of missing pixels."
            ),
        },
        "temporal_support": {
            "kind": "static",
            "start": None,
            "end": None,
            "local_solar_time": None,
            "notes": (
                "Apparent thermal inertia is treated as time-independent by the "
                "retrieval; per-pixel acquisition time/season is not carried by the "
                "mosaic, so no single observation time is claimed."
            ),
        },
        "depth_support": {
            "kind": "unknown",
            "min_m": None,
            "max_m": None,
        },
        "method_version": MARS_METHOD_VERSION,
        "dataset_ids": [dataset_id],
        "source_asset_ids": [asset_id],
        "quality_flags": [
            "official_usgs_public_domain",
            "themis_quantitative_32bit",
            "apparent_thermal_inertia",
            "retrieval_model_fergason_2006",
            "native_100m",
            "not_resampled_90m",
            "no_per_pixel_uncertainty",
            "no_per_pixel_qa_layer",
            "published_absolute_accuracy_20pct",
            "atmospheric_clouds_not_corrected",
            "surface_slopes_not_in_retrieval",
            "subsurface_ice_or_bedrock_not_modeled",
            "not_surface_temperature",
        ],
        "score_role": "evidence_only",
        "evidence": None,
        "legacy_provenance": {
            "reference_id": reference_id,
            "display_name": display_name,
            "source_manifest": source_manifest,
            "reference_latitude_deg": latitude_deg,
            "reference_longitude_deg": longitude_deg,
            "product_id": tile.product_id,
            "product_version": MARS_TI_VERSION,
            "product_url": MARS_TI_PRODUCT_URL,
            "tile_url": tile.url,
            "doi": MARS_TI_DOI,
            "citation": MARS_TI_CITATION,
            "physical_quantity": "apparent_thermal_inertia",
            "retrieval_model": "Fergason et al. (2006) 1-D apparent thermal inertia",
            "native_resolution_m": tile.pixel_resolution_m,
            "pixel_sample_line": [sample, line],
            "spatial_n": stats["n"],
            "spatial_mean_ti": stats["mean"],
            "spatial_min_ti": stats["min"],
            "spatial_max_ti": stats["max"],
            "spatial_std_ti": stats["std"],
            "rows_scanned": stats["rows_scanned"],
            "rejected_pixels_in_window": stats["rejected"],
            "published_absolute_accuracy_fraction": MARS_TI_ABSOLUTE_ACCURACY_FRACTION,
            "published_relative_accuracy_fraction": MARS_TI_RELATIVE_ACCURACY_FRACTION,
        },
        "notes": (
            "Observed USGS THEMIS apparent thermal inertia, native 100 m, bounded "
            "1.98 km window. This is not a surface temperature, has no per-pixel "
            "uncertainty or QA layer, and is not scored across bodies."
        ),
    }


def assert_no_diurnal_range(payloads: Sequence[dict[str, Any]]) -> None:
    """Fail closed if a builder tries to emit a diurnal range from PCP bins."""
    forbidden = {
        "diurnal_temperature_range_k",
        "diurnal_range_k",
        "temperature_change_rate_k_per_hour",
    }
    for payload in payloads:
        if payload.get("feature_id") in forbidden:
            raise ValueError(
                "PCP local-time bins are multi-year composites and cannot support a "
                "diurnal range or change rate"
            )
