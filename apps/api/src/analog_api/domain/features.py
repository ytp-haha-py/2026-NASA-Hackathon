from __future__ import annotations

from dataclasses import dataclass, field

CONTRACT_VERSION = "1.0.0"
METHOD_VERSION = "terrain-metrics-1.0.0"
FLAT_METHOD_VERSION = "flat-terrain-fraction-1.0.0"
QUERY_VERSION = "search-1.0.0"
AOI_PROCESSING_VERSION = "aoi-analysis-1.0.0"
CONTEXT_METHOD_VERSION = "worldcover-2021-v200-1.0.0"
ANALYSIS_WINDOW_M = 1980
ANALYSIS_RESOLUTION_M = 90
MIN_VALID_FRACTION = 0.8
AOI_MAX_SIDE_M = 2000.0
AOI_MIN_SIDE_M = 600.0
AOI_MAX_AREA_KM2 = 4.0
AOI_MAX_VERTICES = 100


@dataclass(frozen=True, slots=True)
class FeatureDefinition:
    id: str
    label: str
    unit: str
    description: str
    method: str
    method_version: str
    min_value: float
    max_value: float
    scale: float
    scale_rationale: str
    extraction_scale_m: int
    valid_domain: str
    missing_policy: str = (
        "Candidates missing this metric are excluded from a search that requests it."
    )
    dimension_id: str = "terrain_shape"
    value_kind: str = "numeric"
    score_role: str = "similarity"
    spatial_support: str = "fixed 1.98 km x 1.98 km window; 22 x 22 cells at 90 m"
    temporal_support: str = "static epoch of the source DEM release"
    provider_coverage_by_body: dict[str, str] = field(
        default_factory=lambda: {
            "Earth": "verified_where_indexed",
            "Moon": "verified_only_for_processed_reference_rois",
            "Mars": "verified_only_for_processed_reference_rois",
        }
    )
    comparability_gate: str = (
        "Same method_version, projected metric CRS, 90 m analysis grid and valid-data rule. "
        "Terrain-shape comparability does not imply environmental equivalence."
    )

    def to_payload(self) -> dict[str, object]:
        return {
            "id": self.id,
            "label": self.label,
            "unit": self.unit,
            "description": self.description,
            "method": self.method,
            "method_version": self.method_version,
            "min_value": self.min_value,
            "max_value": self.max_value,
            "scale": self.scale,
            "scale_rationale": self.scale_rationale,
            "extraction_scale_m": self.extraction_scale_m,
            "valid_domain": self.valid_domain,
            "missing_policy": self.missing_policy,
            "dimension_id": self.dimension_id,
            "value_kind": self.value_kind,
            "score_role": self.score_role,
            "spatial_support": self.spatial_support,
            "temporal_support": self.temporal_support,
            "provider_coverage_by_body": self.provider_coverage_by_body,
            "comparability_gate": self.comparability_gate,
        }


FEATURES: tuple[FeatureDefinition, ...] = (
    FeatureDefinition(
        id="slope_mean_deg",
        label="Mean slope",
        unit="degree",
        description="Mean terrain gradient angle over the analysis window.",
        method=(
            "DEM resampled (bilinear) to 90 m in a metric CRS; per-cell gradient magnitude "
            "from a 3x3 Horn operator; arithmetic mean of valid cells."
        ),
        method_version=METHOD_VERSION,
        min_value=0.0,
        max_value=90.0,
        scale=10.0,
        scale_rationale=(
            "Fixed a-priori contrast scale: 10 degrees separates smooth plains from rugged "
            "hillslopes at 90 m sampling."
        ),
        extraction_scale_m=ANALYSIS_RESOLUTION_M,
        valid_domain="0 <= value <= 90 degrees",
    ),
    FeatureDefinition(
        id="slope_p90_deg",
        label="90th percentile slope",
        unit="degree",
        description="90th percentile of per-cell terrain gradient angle in the analysis window.",
        method=(
            "Same gradient grid as slope_mean_deg; linear-interpolated 90th percentile over "
            "valid cells."
        ),
        method_version=METHOD_VERSION,
        min_value=0.0,
        max_value=90.0,
        scale=15.0,
        scale_rationale="Fixed a-priori contrast scale of 15 degrees for steep-tail terrain.",
        extraction_scale_m=ANALYSIS_RESOLUTION_M,
        valid_domain="0 <= value <= 90 degrees",
    ),
    FeatureDefinition(
        id="terrain_relief_p95_p05_m",
        label="Terrain relief (p95-p05)",
        unit="meter",
        description=(
            "Difference between the 95th and 5th percentile elevation inside the analysis window."
        ),
        method=(
            "DEM resampled (bilinear) to 90 m; linear-interpolated percentiles over valid cells; "
            "p95 minus p05."
        ),
        method_version=METHOD_VERSION,
        min_value=0.0,
        max_value=20000.0,
        scale=200.0,
        scale_rationale=(
            "Fixed a-priori contrast scale: 200 m of relief separates subdued from strongly "
            "dissected terrain."
        ),
        extraction_scale_m=ANALYSIS_RESOLUTION_M,
        valid_domain="0 <= value <= 20000 meters",
    ),
    FeatureDefinition(
        id="roughness_std_m",
        label="Detrended roughness (std dev)",
        unit="meter",
        description=(
            "Standard deviation of elevation residuals after removing a least-squares plane "
            "fitted across the analysis window."
        ),
        method=(
            "DEM resampled (bilinear) to 90 m in a metric CRS; least-squares plane "
            "z = a*x + b*y + c fitted on valid cells; population standard deviation of residuals."
        ),
        method_version=METHOD_VERSION,
        min_value=0.0,
        max_value=5000.0,
        scale=20.0,
        scale_rationale="Fixed a-priori contrast scale of 20 m of detrended residual height.",
        extraction_scale_m=ANALYSIS_RESOLUTION_M,
        valid_domain="0 <= value <= 5000 meters",
    ),
    FeatureDefinition(
        id="flat_terrain_fraction",
        label="Flat terrain fraction",
        unit="fraction",
        description="Fraction of valid Horn slope cells strictly below 5 degrees.",
        method="Same 90 m Horn 3x3 gradient grid; count(slope < 5 degrees) / count(valid slope). Incomplete neighborhoods and outer rim excluded, never filled.",
        method_version=FLAT_METHOD_VERSION,
        min_value=0.0,
        max_value=1.0,
        scale=0.25,
        scale_rationale="Fixed engineering contrast of 25 percentage points; not calibrated to physical environmental equivalence.",
        extraction_scale_m=ANALYSIS_RESOLUTION_M,
        valid_domain="0 <= value <= 1",
    ),
)

FEATURE_BY_ID: dict[str, FeatureDefinition] = {f.id: f for f in FEATURES}

ANALYSIS_PAYLOAD: dict[str, object] = {
    "window_size_m": ANALYSIS_WINDOW_M,
    "window_description": (
        "Fixed 1.98 km x 1.98 km window (22 x 22 cells at 90 m) centred on the candidate/ROI coordinate, in a local "
        "metric CRS."
    ),
    "analysis_resolution_m": ANALYSIS_RESOLUTION_M,
    "resampling": "bilinear",
    "min_valid_fraction": MIN_VALID_FRACTION,
    "crs_policy": (
        "All metrics are computed in a projected CRS whose horizontal units are metres. "
        "Slopes are never computed from longitude/latitude degrees."
    ),
    "metric_family": "local terrain shape only",
}


def get_feature(feature_id: str) -> FeatureDefinition:
    return FEATURE_BY_ID[feature_id]


def feature_payloads() -> list[dict[str, object]]:
    return [feature.to_payload() for feature in FEATURES]


# ---------------------------------------------------------------------------
# Earth land-cover context (ESA WorldCover 2021 v200).
#
# These are NOT similarity dimensions and never enter the weighted distance.
# They are Earth-only contextual filters/annotations and must not be described
# as lunar mineralogy, geology, or a physical analog measurement.
# ---------------------------------------------------------------------------

WORLDCOVER_CLASSES: tuple[tuple[int, str, str], ...] = (
    (10, "tree_cover", "Tree cover"),
    (20, "shrubland", "Shrubland"),
    (30, "grassland", "Grassland"),
    (40, "cropland", "Cropland"),
    (50, "built_up", "Built-up"),
    (60, "bare_sparse", "Bare / sparse vegetation"),
    (70, "snow_ice", "Snow and ice"),
    (80, "water", "Permanent water bodies"),
    (90, "herbaceous_wetland", "Herbaceous wetland"),
    (95, "mangrove", "Mangroves"),
    (100, "moss_lichen", "Moss and lichen"),
)


@dataclass(frozen=True, slots=True)
class ContextFeatureDefinition:
    feature_id: str
    label: str
    class_code: int
    dimension_id: str
    description: str
    value_kind: str
    unit: str
    method_version: str
    spatial_support: str
    temporal_support: str
    provider_coverage_by_body: dict[str, str]
    compare_strategy: str
    score_role: str
    quality_rules: str
    reference: str

    def to_payload(self) -> dict[str, object]:
        return {
            "feature_id": self.feature_id,
            "label": self.label,
            "class_code": self.class_code,
            "dimension_id": self.dimension_id,
            "description": self.description,
            "value_kind": self.value_kind,
            "unit": self.unit,
            "method_version": self.method_version,
            "spatial_support": self.spatial_support,
            "temporal_support": self.temporal_support,
            "provider_coverage_by_body": self.provider_coverage_by_body,
            "compare_strategy": self.compare_strategy,
            "score_role": self.score_role,
            "quality_rules": self.quality_rules,
            "reference": self.reference,
        }


CONTEXT_FEATURES: tuple[ContextFeatureDefinition, ...] = tuple(
    ContextFeatureDefinition(
        feature_id=f"landcover_{slug}_fraction",
        label=f"Land cover: {label} fraction",
        class_code=code,
        dimension_id="land_cover_context",
        description=(
            f"Fraction of valid ESA WorldCover pixels in the AOI mapped to class {code} "
            f"({label}). Earth land-cover context only."
        ),
        value_kind="distribution",
        unit="fraction",
        method_version=CONTEXT_METHOD_VERSION,
        spatial_support="AOI footprint at native 10 m WorldCover pixels; fractions over valid pixels",
        temporal_support="ESA WorldCover 2021 v200 static annual epoch",
        provider_coverage_by_body={
            "Earth": "verified_where_indexed",
            "Moon": "not_applicable",
            "Mars": "not_applicable",
        },
        compare_strategy="context_only_filter",
        score_role="context_only",
        quality_rules="valid_fraction >= 0.8; class fractions sum to 1 within tolerance; unknown stays unknown",
        reference="https://esa-worldcover.org/en/data-access",
    )
    for code, slug, label in WORLDCOVER_CLASSES
)

CONTEXT_FEATURE_BY_ID: dict[str, ContextFeatureDefinition] = {
    feature.feature_id: feature for feature in CONTEXT_FEATURES
}

AOI_LIMITS: dict[str, object] = {
    "max_side_m": AOI_MAX_SIDE_M,
    "min_side_m": AOI_MIN_SIDE_M,
    "max_area_km2": AOI_MAX_AREA_KM2,
    "max_polygon_vertices": AOI_MAX_VERTICES,
    "analysis_resolution_m": ANALYSIS_RESOLUTION_M,
    "requested_features": [feature.id for feature in FEATURES],
    "supported_body": "Earth",
    "note": (
        "On-demand AOIs are bounded to a 2 km-class footprint so terrain support remains "
        "comparable to the indexed windows. Polygon holes are not supported in the MVP; the "
        "outer ring is validated and used."
    ),
}


def context_feature_payloads() -> list[dict[str, object]]:
    return [feature.to_payload() for feature in CONTEXT_FEATURES]
