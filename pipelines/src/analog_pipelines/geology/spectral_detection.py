"""Bounded Mars CRISM spectral-detection pilot semantics (Issue #42 follow-up).

The previous D02 pass produced a source-status matrix and never touched a
granule. This module records the next, still conservative step: a bounded read
of one official CRISM MTRDR targeted observation over the two existing Mars
test AOIs, with explicit wavelength/band, geolocation and quality semantics.

What is validated here:

* official product identity, URLs, byte sizes and HTTP range behaviour;
* the ENVI/PDS band layout: 489 bands, 436.13-3896.76 nm, BSQ float32, three
  documented detector gaps, and the 36 m equirectangular Mars custom sphere;
* that the AOI pixel windows computed from the label projection land inside
  both test AOIs.

What is **not** validated: a mineral detector. The documented continuum-removal
formulations are implemented, but reproducing the official ``D2300`` product
from the ``IF`` cube failed the stated tolerance (observed RMSE 0.0105-0.0147
against a 0.001 requirement). Because the decoder is unvalidated, every
observation stays ``unknown``; no ``detected`` or
``not_detected_under_valid_observation`` state is emitted, and no value is an
abundance.

Band depth and spectral index are dimensionless spectral measurements. They are
never mass fraction, volume fraction or mineral abundance. Fe-oxide/mafic
indices describe mafic absorption and can never be relabelled aqueous
alteration.
"""

from __future__ import annotations

import math
from typing import Any, Literal, Protocol

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator

D02_PILOT_METHOD_VERSION = "spectral-detection-0.1.0"

PRODUCT_ID = "hrl000040ff_07_if183j_mtr3"
COLLECTION_ID = "MRO-M-CRISM-5-RDR-MPTARGETED-V1.0 (volume mrocr_4001)"
PRODUCT_DOI_URL = "https://pds-geosciences.wustl.edu/mro/mro-m-crism-5-rdr-mptargeted-v1/"
LICENSE = "PDS archived data are in the public domain (NASA PDS Geosciences Node)"
LICENSE_URL = "https://pds.nasa.gov/"

FEATURE_D2300 = "d2300_2p3um_dropoff"
FEATURE_OLINDEX3 = "olindex3_1um_olivine_index"

FEATURE_SEMANTICS: dict[str, dict[str, Any]] = {
    FEATURE_D2300: {
        "parameter_class": "continuum_removed_absorption_dropoff",
        "wavelength_nm": 2300.0,
        "quantity_class": "dimensionless_spectral_parameter",
        "meaning": (
            "Relative drop-off across 2.29-2.33 um referenced to the 1.815-2.530 um "
            "continuum; a hydroxylated Fe/Mg silicate and Mg-carbonate indicator."
        ),
        "never_means": (
            "It is not a mineral abundance, not a unique mineral identification, and "
            "not proof of aqueous alteration on its own."
        ),
        "aqueous_alteration_claim": "candidate_indicator_only",
    },
    FEATURE_OLINDEX3: {
        "parameter_class": "continuum_removed_absorption_index",
        "wavelength_nm": 1000.0,
        "quantity_class": "dimensionless_spectral_parameter",
        "meaning": "Broad 1 um absorption index; olivine-rich surfaces are strongly positive.",
        "never_means": (
            "It is not an olivine abundance, not a Fe-oxide hydration indicator, and "
            "never aqueous alteration or water evidence."
        ),
        "aqueous_alteration_claim": "forbidden",
    },
}

# Explicit pilot thresholds. They are recorded so that a later validated
# detector can use them, but they are NOT applied while ``decoder_validated``
# is false. ``threshold_status`` keeps the distinction visible in artifacts.
DETECTION_THRESHOLDS: dict[str, float] = {
    FEATURE_D2300: 0.02,
    FEATURE_OLINDEX3: 0.10,
}
THRESHOLD_STATUS = "explicit_pilot_unvalidated"

# Maximum |observed - official| or RMSE accepted before a decoder may emit
# detection states. The measured attempts exceeded this.
REPRODUCTION_TOLERANCE = 0.001

STATES: tuple[str, ...] = (
    "detected",
    "not_detected_under_valid_observation",
    "no_coverage",
    "unknown",
    "not_applicable",
)

ABUNDANCE_UNITS = {
    "wt%",
    "weight_percent",
    "mass_fraction",
    "mass_percent",
    "vol%",
    "volume_percent",
    "ppm",
}

# ENVI / PDS layout of the bounded product.
IF_SAMPLES = 455
IF_LINES = 751
IF_BANDS = 489
IF_BYTES = 668_374_980
IF_INTERLEAVE = "bsq"
IF_DATA_TYPE = "float32"
IF_IGNORE_VALUE = 65535.0
SR_BYTES = 82_009_200
IN_BYTES = 12_301_380

# Mars custom sphere from the PDS IMAGE_MAP_PROJECTION object (metres).
MARS_SPHERE_RADIUS_M = 3_394_839.813316312
PROJECTION_STANDARD_PARALLEL_DEG = 15.0
PIXEL_SIZE_M = 36.0
MAP_ORIGIN_X_M = 4_422_632.2
MAP_ORIGIN_Y_M = 1_109_935.5
LABEL_MIN_LATITUDE = 18.27674
LABEL_MAX_LATITUDE = 18.732427
LABEL_WEST_LONGITUDE = 77.27556
LABEL_EAST_LONGITUDE = 77.561134

# Wavelengths of the bands used by the two documented parameters. The values
# are the reference wavelengths in Viviano-Beck et al. (2014), Table 2, and the
# kernel width is the table's median-filter width in bands.
D2300_BANDS: dict[float, int] = {
    1815.0: 5,
    2120.0: 5,
    2170.0: 5,
    2210.0: 5,
    2290.0: 3,
    2320.0: 3,
    2330.0: 3,
    2530.0: 5,
}
OLINDEX3_BANDS: dict[float, int] = {
    1080.0: 7,
    1152.0: 7,
    1210.0: 7,
    1250.0: 7,
    1263.0: 7,
    1276.0: 7,
    1330.0: 7,
    1368.0: 7,
    1395.0: 7,
    1427.0: 7,
    1470.0: 7,
    1750.0: 7,
    2400.0: 7,
}
OLINDEX3_WEIGHTS: dict[float, float] = {
    1080.0: 0.03,
    1152.0: 0.03,
    1210.0: 0.03,
    1250.0: 0.03,
    1263.0: 0.07,
    1276.0: 0.07,
    1330.0: 0.12,
    1368.0: 0.12,
    1395.0: 0.14,
    1427.0: 0.18,
    1470.0: 0.18,
}

# AOI windows derived from the label projection. Row/column bounds are
# half-open native-pixel indices in the 455 x 751 MTRDR grid.
AOI_WINDOWS: dict[str, dict[str, Any]] = {
    "mars-test-jezero-floor": {
        "bbox_lon_lat": [77.40, 18.42, 77.44, 18.46],
        "columns": [198, 262],
        "rows": [448, 515],
        "pixel_count": 4288,
    },
    "mars-test-jezero-western-delta": {
        "bbox_lon_lat": [77.35, 18.47, 77.38, 18.50],
        "columns": [118, 167],
        "rows": [383, 433],
        "pixel_count": 2450,
    },
}

# Bounded sub-windows actually read for the reproduction attempt (16 x 16
# native pixels at the AOI centre); the full AOIs were not downloaded.
READ_SUBWINDOWS: dict[str, dict[str, int]] = {
    "mars-test-jezero-floor": {"row0": 474, "row1": 490, "col0": 222, "col1": 238},
    "mars-test-jezero-western-delta": {"row0": 399, "row1": 415, "col0": 135, "col1": 151},
}


class SpectralDetectionError(ValueError):
    """Raised when a spectral observation would overstate its evidence."""


class RangeSource(Protocol):
    """Injected HTTP/S3-style byte-range reader; tests never touch the network."""

    def __call__(self, url: str, start: int, end: int) -> bytes: ...


def read_bsq_window(
    source: RangeSource,
    url: str,
    *,
    samples: int,
    lines: int,
    band: int,
    row0: int,
    row1: int,
    col0: int = 0,
    col1: int | None = None,
    itemsize: int = 4,
) -> np.ndarray:
    """Read one BSQ band over a half-open row/column window with one range GET.

    BSQ stores band ``b`` as a contiguous ``lines x samples`` block, so a band
    row slice is a single byte range. The full cube is never requested.
    """
    if not (0 <= row0 < row1 <= lines) or not (0 <= band < 10_000):
        raise SpectralDetectionError("row window or band index outside the product")
    col1 = samples if col1 is None else col1
    if not (0 <= col0 < col1 <= samples):
        raise SpectralDetectionError("column window outside the product")
    band_bytes = samples * lines * itemsize
    start = band * band_bytes + row0 * samples * itemsize
    end = band * band_bytes + row1 * samples * itemsize - 1
    raw = source(url, start, end)
    expected = (row1 - row0) * samples * itemsize
    if len(raw) != expected:
        raise SpectralDetectionError(f"range read returned {len(raw)} bytes, expected {expected}")
    dtype = np.dtype("<f4" if itemsize == 4 else "<f8")
    return np.frombuffer(raw, dtype=dtype).reshape(row1 - row0, samples)[:, col0:col1]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class SpectralObservation(Model):
    schema_version: Literal["spectral-detection-observation-1.0.0"] = (
        "spectral-detection-observation-1.0.0"
    )
    observation_id: str = Field(min_length=1)
    area_id: str = Field(min_length=1)
    body: Literal["Mars"] = "Mars"
    feature_id: str
    quantity_class: Literal["dimensionless_spectral_parameter"]
    parameter: str = Field(min_length=1)
    state: Literal[
        "detected",
        "not_detected_under_valid_observation",
        "no_coverage",
        "unknown",
        "not_applicable",
    ]
    value: float | None = None
    unit: Literal["dimensionless_spectral_parameter"] = "dimensionless_spectral_parameter"
    threshold: float
    threshold_status: Literal["explicit_pilot_unvalidated"] = THRESHOLD_STATUS
    decoder_validated: bool
    valid_fraction: float | None = None
    detected_pixel_fraction: float | None = None
    source_product_id: str = PRODUCT_ID
    method_version: str = D02_PILOT_METHOD_VERSION
    meaning: str = Field(min_length=1)
    never_means: str = Field(min_length=1)
    limitations: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def state_semantics(self) -> SpectralObservation:
        if self.feature_id not in FEATURE_SEMANTICS:
            raise ValueError(f"unknown spectral feature {self.feature_id!r}")
        if self.unit in ABUNDANCE_UNITS:
            raise ValueError("band depth or index can never carry an abundance unit")
        if self.state == "detected":
            if not self.decoder_validated:
                raise ValueError("an unvalidated decoder cannot emit detected")
            if self.value is None:
                raise ValueError("detected requires a measured parameter value")
            if not self.valid_fraction or self.valid_fraction <= 0:
                raise ValueError("detected requires valid observation coverage")
        else:
            if self.value is not None:
                raise ValueError(
                    "only detected may carry a numeric value; missing coverage, "
                    "non-detection and unknowns are categorical"
                )
            if self.state == "not_detected_under_valid_observation" and not (
                self.valid_fraction and self.valid_fraction > 0
            ):
                raise ValueError("non-detection requires a valid observed fraction")
        if (
            self.feature_id == FEATURE_OLINDEX3
            and self.state == "detected"
            and FEATURE_SEMANTICS[FEATURE_OLINDEX3]["aqueous_alteration_claim"] != "forbidden"
        ):
            raise ValueError("mafic index must never become an aqueous-alteration claim")
        return self


def nearest_band_indices(wavelengths: np.ndarray, target_nm: float, width: int) -> list[int]:
    """Return the contiguous band window centred on the closest band to target."""
    if width < 1 or width % 2 == 0:
        raise SpectralDetectionError("kernel width must be a positive odd band count")
    if wavelengths.ndim != 1 or wavelengths.size == 0:
        raise SpectralDetectionError("wavelengths must be a non-empty 1-D array")
    center = int(np.argmin(np.abs(wavelengths - target_nm)))
    half = width // 2
    lo, hi = center - half, center + half + 1
    if lo < 0 or hi > wavelengths.size:
        raise SpectralDetectionError(f"kernel around {target_nm} nm leaves the band range")
    return list(range(lo, hi))


def median_band_reflectance(
    wavelengths: np.ndarray, reflectance: np.ndarray, target_nm: float, width: int
) -> np.ndarray:
    """Median reflectance over the documented band kernel; NaN stays NaN."""
    if reflectance.shape[0] != wavelengths.size:
        raise SpectralDetectionError("reflectance band axis does not match wavelengths")
    indices = nearest_band_indices(wavelengths, target_nm, width)
    return np.median(reflectance[indices], axis=0)


def linear_continuum(
    left_nm: float,
    left_reflectance: float,
    right_nm: float,
    right_reflectance: float,
    at_nm: float,
    *,
    allow_extrapolation: bool = False,
) -> float:
    """Value of the straight continuum between two shoulders at ``at_nm``.

    ``allow_extrapolation`` is required by OLINDEX3, whose 1 um bands lie below
    the 1750/2400 nm continuum anchors (Viviano-Beck et al., 2014).
    """
    if right_nm <= left_nm:
        raise SpectralDetectionError("continuum shoulders must increase in wavelength")
    if not allow_extrapolation and not (left_nm <= at_nm <= right_nm):
        raise SpectralDetectionError("continuum evaluation outside the shoulder range")
    fraction = (at_nm - left_nm) / (right_nm - left_nm)
    return left_reflectance + (right_reflectance - left_reflectance) * fraction


def band_depth(
    wavelengths: np.ndarray,
    reflectance: np.ndarray,
    center_nm: float,
    left_nm: float,
    right_nm: float,
    kernel: int = 1,
) -> float:
    """Continuum-removed band depth (Clark & Roush, 1984, eq. 1-3)."""
    center = float(median_band_reflectance(wavelengths, reflectance, center_nm, kernel))
    left = float(median_band_reflectance(wavelengths, reflectance, left_nm, kernel))
    right = float(median_band_reflectance(wavelengths, reflectance, right_nm, kernel))
    continuum = linear_continuum(left_nm, left, right_nm, right, center_nm)
    if continuum <= 0:
        return math.nan
    return 1.0 - center / continuum


def d2300(wavelengths: np.ndarray, reflectance: np.ndarray) -> float:
    """Viviano-Beck et al. (2014) Table 2 parameter 38 (2.3 um drop-off).

    RC is the 1.815-2.530 um continuum. Numerator bands use a 3-band median,
    denominator bands a 5-band median, matching the table kernel widths.
    """
    continuum = {}
    for target in (1815.0, 2530.0):
        continuum[target] = float(
            median_band_reflectance(wavelengths, reflectance, target, D2300_BANDS[target])
        )

    def rc(at_nm: float) -> float:
        return linear_continuum(1815.0, continuum[1815.0], 2530.0, continuum[2530.0], at_nm)

    numerator = 0.0
    for target in (2290.0, 2320.0, 2330.0):
        band = float(median_band_reflectance(wavelengths, reflectance, target, D2300_BANDS[target]))
        denominator = rc(target)
        if denominator <= 0:
            return math.nan
        numerator += band / denominator
    divisor = 0.0
    for target in (2120.0, 2170.0, 2210.0):
        band = float(median_band_reflectance(wavelengths, reflectance, target, D2300_BANDS[target]))
        denominator = rc(target)
        if denominator <= 0:
            return math.nan
        divisor += band / denominator
    if divisor == 0:
        return math.nan
    return 1.0 - (numerator / divisor)


def olindex3(wavelengths: np.ndarray, reflectance: np.ndarray) -> float:
    """Viviano-Beck et al. (2014) Table 2 parameter 14 (OLINDEX3)."""
    a1750 = float(median_band_reflectance(wavelengths, reflectance, 1750.0, 7))
    a2400 = float(median_band_reflectance(wavelengths, reflectance, 2400.0, 7))
    total = 0.0
    for target, weight in OLINDEX3_WEIGHTS.items():
        band = float(
            median_band_reflectance(wavelengths, reflectance, target, OLINDEX3_BANDS[target])
        )
        rc = linear_continuum(1750.0, a1750, 2400.0, a2400, target, allow_extrapolation=True)
        if rc <= 0:
            return math.nan
        total += weight * (rc - band) / rc
    return total


def classify(
    feature_id: str,
    decoder_validated: bool,
    *,
    valid_pixels: int | None,
    total_pixels: int,
    value: float | None,
    threshold: float | None = None,
) -> dict[str, Any]:
    """Map coverage, decoder validation and an explicit threshold to a state.

    Order matters: no coverage is not non-detection, and an unvalidated decoder
    is unknown even when a parameter value exists. A validated decoder may only
    emit ``detected``/``not_detected_under_valid_observation``. ``valid_pixels``
    of ``None`` means coverage exists but the valid count was not measured; the
    state is still unknown and no fraction is asserted.
    """
    if feature_id not in FEATURE_SEMANTICS:
        raise SpectralDetectionError(f"unknown spectral feature {feature_id!r}")
    limit = threshold if threshold is not None else DETECTION_THRESHOLDS[feature_id]
    if total_pixels <= 0:
        return {"state": "no_coverage", "value": None, "valid_fraction": 0.0}
    if valid_pixels is None:
        return {"state": "unknown", "value": None, "valid_fraction": None}
    valid_fraction = valid_pixels / total_pixels
    if valid_pixels <= 0:
        return {"state": "unknown", "value": None, "valid_fraction": 0.0}
    if not decoder_validated:
        return {"state": "unknown", "value": None, "valid_fraction": valid_fraction}
    if value is None or not math.isfinite(value):
        return {"state": "unknown", "value": None, "valid_fraction": valid_fraction}
    if value > limit:
        return {"state": "detected", "value": float(value), "valid_fraction": valid_fraction}
    return {
        "state": "not_detected_under_valid_observation",
        "value": None,
        "valid_fraction": valid_fraction,
    }


def build_observation(feature_id: str, area_id: str, **kwargs: Any) -> SpectralObservation:
    """Construct a validated observation, keeping detection semantics explicit."""
    if feature_id not in FEATURE_SEMANTICS:
        raise SpectralDetectionError(f"unknown spectral feature {feature_id!r}")
    semantics = FEATURE_SEMANTICS[feature_id]
    state = kwargs["state"]
    return SpectralObservation(
        observation_id=f"spectral:{area_id}:{PRODUCT_ID}:{feature_id}:{state}",
        area_id=area_id,
        feature_id=feature_id,
        quantity_class=semantics["quantity_class"],
        parameter=semantics["parameter_class"],
        threshold=kwargs.get("threshold", DETECTION_THRESHOLDS[feature_id]),
        decoder_validated=kwargs["decoder_validated"],
        state=state,
        value=kwargs.get("value"),
        valid_fraction=kwargs.get("valid_fraction"),
        detected_pixel_fraction=kwargs.get("detected_pixel_fraction"),
        meaning=semantics["meaning"],
        never_means=semantics["never_means"],
        limitations=kwargs.get(
            "limitations",
            [
                "Band depth and spectral indices are not mineral abundance.",
                "One bounded MTRDR granule over two test AOIs; not a Mars survey.",
            ],
        ),
    )


def lonlat_to_pixel(lon_deg: float, lat_deg: float) -> tuple[float, float]:
    """Label-projection pixel coordinates (column, row) of a lon/lat point.

    The MTRDR grid is an equidistant-cylindrical projection on the label's
    custom Mars sphere (standard parallel 15 deg). ``x_map`` is the centre of
    the upper-left pixel, matching ENVI ``map info`` semantics.
    """
    standard_parallel = math.radians(PROJECTION_STANDARD_PARALLEL_DEG)
    x = MARS_SPHERE_RADIUS_M * math.radians(lon_deg) * math.cos(standard_parallel)
    y = MARS_SPHERE_RADIUS_M * math.radians(lat_deg)
    return (x - MAP_ORIGIN_X_M) / PIXEL_SIZE_M, (MAP_ORIGIN_Y_M - y) / PIXEL_SIZE_M


def pixel_to_lonlat(column: float, row: float) -> tuple[float, float]:
    """Inverse of :func:`lonlat_to_pixel` for pixel-centre coordinates."""
    standard_parallel = math.radians(PROJECTION_STANDARD_PARALLEL_DEG)
    x = MAP_ORIGIN_X_M + PIXEL_SIZE_M * column
    y = MAP_ORIGIN_Y_M - PIXEL_SIZE_M * row
    lon = math.degrees(x / (MARS_SPHERE_RADIUS_M * math.cos(standard_parallel)))
    lat = math.degrees(y / MARS_SPHERE_RADIUS_M)
    return lon, lat


def aoi_pixel_window(bbox: list[float]) -> dict[str, Any]:
    """Half-open native pixel window covering a lon/lat bbox inside the product."""
    west, south, east, north = bbox
    west_col, north_row = lonlat_to_pixel(west, north)
    east_col, south_row = lonlat_to_pixel(east, south)
    col0, col1 = sorted((int(math.floor(west_col)), int(math.ceil(east_col))))
    row0, row1 = sorted((int(math.floor(north_row)), int(math.ceil(south_row))))
    col0, col1 = max(col0, 0), min(col1, IF_SAMPLES)
    row0, row1 = max(row0, 0), min(row1, IF_LINES)
    if col1 <= col0 or row1 <= row0:
        return {"columns": [col0, col1], "rows": [row0, row1], "pixel_count": 0}
    return {
        "columns": [col0, col1],
        "rows": [row0, row1],
        "pixel_count": (col1 - col0) * (row1 - row0),
    }


def fraction_above(values: np.ndarray, threshold: float) -> float:
    """Fraction of finite values above a threshold; NaN is never a detection."""
    finite = np.isfinite(values)
    total = int(np.count_nonzero(finite))
    if not total:
        return 0.0
    return float(np.count_nonzero(values[finite] > threshold) / total)


def summary_stats(values: np.ndarray) -> dict[str, float | None]:
    finite = values[np.isfinite(values)]
    if not finite.size:
        return {"count": 0, "median": None, "min": None, "max": None}
    return {
        "count": int(finite.size),
        "median": float(np.median(finite)),
        "min": float(finite.min()),
        "max": float(finite.max()),
    }
