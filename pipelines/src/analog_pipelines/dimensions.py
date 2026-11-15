"""Six-dimension typed feature catalog, provider interfaces and validators (Issues #40–#46).

Only D01 terrain participates in similarity. D04 has bounded, evidence-only
observations from public neutron and radar products (``pipelines/pilots/
d04_water_ice_planetary``); they are not similarity features and not ice abundance.
This module defines stable IDs, value kinds, units, roles, missingness/comparability
rules and offline parsers for fixture evidence. It does not invent coverage.
"""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

from analog_pipelines.typed_contracts import TypedObservation

DimensionCode = Literal["D01", "D02", "D03", "D04", "D05", "D06"]
ValueKind = Literal[
    "numeric",
    "category",
    "distribution",
    "detection",
    "timeseries",
    "spectrum",
    "process_evidence",
]
ScoreRole = Literal["similarity", "context_only", "filter", "evidence_only", "not_comparable"]
Body = Literal["Earth", "Moon", "Mars"]

DIMENSION_NAMES = {
    "D01": "Terrain & Landforms",
    "D02": "Geology & Mineralogy",
    "D03": "Thermal Environment",
    "D04": "Water & Ice",
    "D05": "Solar Illumination & Radiation",
    "D06": "Regolith & Surface Properties",
}


@dataclass(frozen=True, slots=True)
class DimensionFeatureV2:
    feature_id: str
    dimension_code: DimensionCode
    label: str
    value_kind: ValueKind
    unit: str
    score_role: ScoreRole
    method_version: str
    bodies: tuple[str, ...]
    source_requirement: str
    comparability: str
    notes: str = ""

    def to_payload(self) -> dict[str, object]:
        return {
            "feature_id": self.feature_id,
            "dimension_code": self.dimension_code,
            "label": self.label,
            "value_kind": self.value_kind,
            "unit": self.unit,
            "score_role": self.score_role,
            "method_version": self.method_version,
            "bodies": list(self.bodies),
            "source_requirement": self.source_requirement,
            "comparability": self.comparability,
            "notes": self.notes,
        }


_D01 = "curvature-geomorphon-0.1.0"
_D02 = "geology-evidence-0.1.0"
_D03 = "thermal-evidence-0.1.0"
_D04 = "water-ice-evidence-0.1.0"
_D05 = "illumination-radiation-0.1.0"
_D06 = "regolith-evidence-0.1.0"

FEATURES_V2: tuple[DimensionFeatureV2, ...] = (
    DimensionFeatureV2(
        "curvature_curvedness_m1",
        "D01",
        "Curvature curvedness",
        "numeric",
        "m^-1",
        "evidence_only",
        _D01,
        ("Earth", "Moon", "Mars"),
        "Buffered metric DEM; valid 3x3/5x5 neighborhood; verified vertical units",
        "not_comparable until buffer/method validation",
        "Proposal only; not in the production score.",
    ),
    DimensionFeatureV2(
        "geomorphon_composition",
        "D01",
        "Geomorphon 10-class composition",
        "distribution",
        "fraction",
        "evidence_only",
        _D01,
        ("Earth", "Moon", "Mars"),
        "Verified GRASS r.geomorphon or equivalent with declared search radius and buffers",
        "not_comparable until real agreement study",
        "Ten fixed classes: flat, peak, ridge, shoulder, spur, slope, hollow, footslope, valley, pit.",
    ),
    DimensionFeatureV2(
        "lithology_area_fraction",
        "D02",
        "Lithology area fractions",
        "distribution",
        "fraction",
        "evidence_only",
        _D02,
        ("Earth", "Moon", "Mars"),
        "Verified polygon/raster geology map with preserved legend/code and map scale",
        "not_comparable without a shared mapping/ontology",
        "Non-exclusive research classes are views, not a global rock classification.",
    ),
    DimensionFeatureV2(
        "mineral_spectral_detection",
        "D02",
        "Mineral spectral detection",
        "detection",
        "detection",
        "evidence_only",
        _D02,
        ("Earth", "Moon", "Mars"),
        "Verified spectral product with band semantics, QA, confidence threshold and coverage",
        "evidence_only; detection is not abundance",
        "Preserve detected / not_detected / no_coverage / unknown / not_applicable.",
    ),
    DimensionFeatureV2(
        "mineral_spectrum",
        "D02",
        "Mineral spectrum",
        "spectrum",
        "reflectance",
        "evidence_only",
        _D02,
        ("Earth", "Moon", "Mars"),
        "Verified wavelengths/calibration/QA; no color-to-abundance conversion",
        "evidence_only",
    ),
    DimensionFeatureV2(
        "surface_temperature_mean_k",
        "D03",
        "Mean surface temperature",
        "numeric",
        "K",
        "evidence_only",
        _D03,
        ("Earth", "Moon", "Mars"),
        "Verified surface-temperature product with QA, local solar time, season and sample count",
        "not_comparable without matching time/emissivity support",
        "Do not infer from illumination or dark pixels.",
    ),
    DimensionFeatureV2(
        "diurnal_temperature_range_k",
        "D03",
        "Diurnal temperature range",
        "numeric",
        "K",
        "evidence_only",
        _D03,
        ("Earth", "Moon", "Mars"),
        "Same-location same-definition day/night samples; n and time support required",
        "not_comparable without matched sampling",
        "Only calculate when actual samples support the definition.",
    ),
    DimensionFeatureV2(
        "water_ice_detection",
        "D04",
        "Water/ice evidence detection",
        "detection",
        "detection",
        "evidence_only",
        _D04,
        ("Earth", "Moon", "Mars"),
        "Verified product with phase/depth/detection limit/model semantics",
        "evidence_only; no mineable-ice inference",
        "No coverage, not detected and absent are separate states.",
    ),
    DimensionFeatureV2(
        "water_ice_depth_interval",
        "D04",
        "Water/ice depth interval",
        "numeric",
        "meter",
        "evidence_only",
        _D04,
        ("Earth", "Moon", "Mars"),
        "Verified retrieval with depth interval, footprint and uncertainty",
        "not_comparable across bodies without method validation",
    ),
    DimensionFeatureV2(
        "water_equivalent_hydrogen_wt_percent",
        "D04",
        "Water-equivalent hydrogen mass percent",
        "numeric",
        "percent",
        "evidence_only",
        _D04,
        ("Moon", "Mars"),
        "Verified neutron-spectrometer product with documented H-to-H2O conversion, sensing depth and retrieval caveats",
        "not_comparable to ice fraction, radar CPR or hydrated-mineral abundance",
        "H ppm by weight x 9. Mars products are not converted to WEH in this repository.",
    ),
    DimensionFeatureV2(
        "epithermal_neutron_flux_ratio",
        "D04",
        "Normalized epithermal neutron flux",
        "numeric",
        "1",
        "evidence_only",
        _D04,
        ("Moon", "Mars"),
        "Verified neutron map with declared normalization, native cell size and no invented per-cell uncertainty",
        "not_comparable across instruments without a shared normalization",
        "Low flux is a hydrogen proxy; it is not ice abundance and does not resolve a 1.98 km window.",
    ),
    DimensionFeatureV2(
        "radar_circular_polarization_ratio",
        "D04",
        "Radar circular polarization ratio",
        "numeric",
        "1",
        "evidence_only",
        _D04,
        ("Moon", "Mars"),
        "Verified radar mosaic with native pixel size, wavelength, calibration/QA and coverage mask",
        "not_comparable to neutron or spectral products",
        "CPR at or above unity is ice-consistent but not diagnostic; no depth retrieval.",
    ),
    DimensionFeatureV2(
        "solar_incidence_deg",
        "D05",
        "Solar incidence angle",
        "numeric",
        "degree",
        "evidence_only",
        _D05,
        ("Earth", "Moon", "Mars"),
        "Solar geometry model with observer height, horizon assumptions and time support",
        "not_comparable to ionizing radiation",
        "Subgroup solar_illumination only.",
    ),
    DimensionFeatureV2(
        "solar_elevation_deg",
        "D05",
        "Solar elevation angle",
        "numeric",
        "degree",
        "evidence_only",
        _D05,
        ("Earth", "Moon", "Mars"),
        "Solar geometry model with observer height, horizon assumptions and time support",
        "not_comparable to ionizing radiation",
        "Derived as 90 - solar_incidence_deg; subgroup solar_illumination only.",
    ),
    DimensionFeatureV2(
        "ionizing_dose_gy",
        "D05",
        "Absorbed ionizing dose",
        "numeric",
        "Gy",
        "evidence_only",
        _D05,
        ("Earth", "Moon", "Mars"),
        "Detector/spectrum model with shielding, exposure interval and uncertainty",
        "not_comparable to sunlight; no Sv conversion without physics",
        "Subgroup ionizing_radiation only.",
    ),
    DimensionFeatureV2(
        "particle_size_distribution",
        "D06",
        "Particle size distribution",
        "distribution",
        "fraction",
        "evidence_only",
        _D06,
        ("Earth", "Moon", "Mars"),
        "Sparse sample/measurement table with explicit bin edges, basis and depth",
        "not comparable to DEM roughness",
        "Never derive grain size from terrain roughness.",
    ),
    DimensionFeatureV2(
        "bulk_density_kg_m3",
        "D06",
        "Bulk density",
        "numeric",
        "kg/m^3",
        "evidence_only",
        _D06,
        ("Earth", "Moon", "Mars"),
        "Sparse measurement with sample state, depth, method and uncertainty",
        "not comparable without sample-state matching",
    ),
    DimensionFeatureV2(
        "cohesion_kpa",
        "D06",
        "Cohesion",
        "numeric",
        "kPa",
        "evidence_only",
        _D06,
        ("Earth", "Moon", "Mars"),
        "Geotechnical experiment with gravity/moisture/load conditions",
        "not comparable without experiment conditions",
    ),
    DimensionFeatureV2(
        "porosity_fraction",
        "D06",
        "Porosity",
        "numeric",
        "fraction",
        "evidence_only",
        _D06,
        ("Earth", "Moon", "Mars"),
        "Sample/experiment measurement with depth, sample state, pore definition and uncertainty",
        "not comparable across bodies without matching sample state and measurement method",
        "A fraction in [0, 1]; never inferred from bulk density without a stated grain density.",
    ),
    DimensionFeatureV2(
        "friction_angle_deg",
        "D06",
        "Friction angle",
        "numeric",
        "degree",
        "evidence_only",
        _D06,
        ("Earth", "Moon", "Mars"),
        "Geotechnical experiment with gravity/moisture/load conditions and failure criterion",
        "not comparable without experiment conditions",
        "Internal friction angle from a stated test; not a slope angle from a DEM.",
    ),
    DimensionFeatureV2(
        "bearing_strength_kpa",
        "D06",
        "Bearing strength",
        "numeric",
        "kPa",
        "evidence_only",
        _D06,
        ("Earth", "Moon", "Mars"),
        "Penetrometer/plate test with gravity, load geometry, depth and moisture state",
        "not comparable without experiment conditions",
        "Bearing capacity or penetration resistance as measured; never a DEM-derived proxy.",
    ),
)

FEATURE_V2_BY_ID = {feature.feature_id: feature for feature in FEATURES_V2}

# SoilGrids texture predictions use the USDA particle-size classes. These edges
# are lower bin edges in micrometres for a distribution with a terminal +inf bin.
D06_PARTICLE_SIZE_BINS_UM: tuple[float, ...] = (0.0, 2.0, 50.0, 2000.0)
D06_PARTICLE_SIZE_LABELS: tuple[str, ...] = ("clay", "silt", "sand")
D06_DEPTH_EXPLICIT_FEATURES: frozenset[str] = frozenset(
    {
        "particle_size_distribution",
        "bulk_density_kg_m3",
        "porosity_fraction",
        "cohesion_kpa",
        "friction_angle_deg",
        "bearing_strength_kpa",
    }
)


class DimensionProvider(Protocol):
    """Provider adapter boundary; real methods require explicit source authorization."""

    dimension_code: DimensionCode
    provider_id: str

    def discover(self, *, body: str, area: dict[str, Any]) -> Sequence[dict[str, Any]]: ...

    def fetch_window(self, asset: dict[str, Any], area: dict[str, Any]) -> dict[str, Any]: ...

    def extract_features(self, window: dict[str, Any]) -> Sequence[TypedObservation]: ...

    def validate(self, observation: TypedObservation) -> Sequence[str]: ...


def validate_dimension_observation(observation: TypedObservation) -> list[str]:
    """Return explicit validation errors; never coerce an unsupported value."""
    errors: list[str] = []
    feature = FEATURE_V2_BY_ID.get(observation.feature_id)
    if feature is None:
        return [f"unknown v2 dimension feature: {observation.feature_id}"]
    if observation.dimension_code != feature.dimension_code:
        errors.append(
            f"dimension mismatch: {feature.feature_id} is {feature.dimension_code}, "
            f"observation says {observation.dimension_code}"
        )
    if observation.value_kind != feature.value_kind:
        errors.append(f"value_kind mismatch: {feature.feature_id} requires {feature.value_kind}")
    if observation.unit and observation.unit != feature.unit:
        errors.append(f"unit mismatch: {feature.feature_id} requires {feature.unit}")
    if observation.body not in feature.bodies:
        errors.append(f"{feature.feature_id} is not defined for {observation.body}")
    if feature.dimension_code == "D05":
        if observation.feature_id in {
            "solar_incidence_deg",
            "solar_elevation_deg",
        } and observation.unit not in {"degree", None}:
            errors.append("illumination must not use radiation dose units")
        if observation.feature_id.startswith("ionizing") and observation.unit == "degree":
            errors.append("ionizing radiation cannot use angular units")
    if (
        feature.dimension_code == "D06"
        and observation.feature_id == "particle_size_distribution"
        and observation.value_kind != "distribution"
    ):
        errors.append("particle size must be a distribution with bin edges")
    if feature.dimension_code == "D06":
        if observation.state == "detected" and observation.depth_support.kind == "unknown":
            errors.append("D06 regolith observations require an explicit depth support")
        if observation.state == "detected":
            value = observation.value
            if (
                observation.feature_id == "porosity_fraction"
                and isinstance(value, (int, float))
                and not 0 <= float(value) <= 1
            ):
                errors.append("porosity_fraction must be within [0, 1]")
            if (
                observation.feature_id
                in {"cohesion_kpa", "friction_angle_deg", "bearing_strength_kpa"}
                and isinstance(value, (int, float))
                and float(value) < 0
            ):
                errors.append(f"{observation.feature_id} cannot be negative")
            if (
                observation.feature_id == "friction_angle_deg"
                and isinstance(value, (int, float))
                and not 0 <= float(value) <= 90
            ):
                errors.append("friction_angle_deg must be within [0, 90] degrees")
    return errors


def parse_fixture_observations(path: str | Path) -> list[TypedObservation]:
    """Parse a tiny synthetic/manual fixture list; no source ingestion."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("dimension fixture must be a JSON array")
    observations = [TypedObservation.model_validate(item) for item in raw]
    for observation in observations:
        errors = validate_dimension_observation(observation)
        if errors:
            raise ValueError(f"{observation.observation_id}: {errors}")
    return observations


def distribution_from_fractions(
    *,
    bins: list[float],
    fractions: list[float],
    labels: list[str] | None = None,
) -> dict[str, Any]:
    if len(bins) != len(fractions):
        raise ValueError("bins and fractions must have equal length")
    if labels is not None and len(labels) != len(fractions):
        raise ValueError("labels and fractions must have equal length")
    if any(not math.isfinite(x) for x in [*bins, *fractions]):
        raise ValueError("distribution values must be finite")
    payload = {"bins": bins, "fractions": fractions}
    if labels is not None:
        payload["labels"] = labels
    return payload
