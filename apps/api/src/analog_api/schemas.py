from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from analog_api.domain.features import CONTEXT_FEATURE_BY_ID, FEATURE_BY_ID


class FeatureOut(BaseModel):
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
    missing_policy: str
    dimension_id: str
    value_kind: str
    score_role: str
    spatial_support: str
    temporal_support: str
    provider_coverage_by_body: dict[str, str]
    comparability_gate: str


class BBoxOut(BaseModel):
    west: float
    south: float
    east: float
    north: float


class CoverageOut(BaseModel):
    label: str
    indexed_candidate_count: int
    geographic_bbox: BBoxOut | None
    description: str
    data_kinds: list[str]
    origins: dict[str, int]


class ContextFeatureOut(BaseModel):
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


class ContextObservationOut(BaseModel):
    feature_id: str
    label: str
    value: float
    unit: str
    method_version: str
    source_resolution_m: float
    valid_fraction: float
    quality: str
    class_code: int
    provider: str
    score_role: Literal["context_only"] = "context_only"
    dataset_id: str | None


class CapabilitiesOut(BaseModel):
    contract_version: str
    method_version: str
    data_snapshot_id: str
    analysis: dict[str, object]
    features: list[FeatureOut]
    context_features: list[ContextFeatureOut]
    supported_bodies: list[str]
    indexed_candidate_count: int
    coverage: CoverageOut
    available_reference_ids: list[str]
    aoi_analysis: dict[str, object]
    data_version: str


class TargetSpec(BaseModel):
    value: float = Field(..., allow_inf_nan=False)
    weight: float = Field(default=1.0, ge=0, allow_inf_nan=False)


class RangeFilterIn(BaseModel):
    """Inclusive hard filter. Bounds are checked against the filtered feature's domain."""

    min: float | None = Field(default=None, allow_inf_nan=False)
    max: float | None = Field(default=None, allow_inf_nan=False)

    @model_validator(mode="after")
    def ordered(self) -> RangeFilterIn:
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError("filter min must be <= max")
        return self


class SearchRequest(BaseModel):
    body: Literal["custom", "Moon", "Mars"] = "custom"
    reference_id: str | None = None
    target: dict[str, TargetSpec] = Field(default_factory=dict)
    weights: dict[str, float] = Field(default_factory=dict)
    filters: dict[str, RangeFilterIn] = Field(default_factory=dict)
    limit: int = Field(default=10, ge=1, le=10)

    @field_validator("target")
    @classmethod
    def known_features(cls, target: dict[str, TargetSpec]) -> dict[str, TargetSpec]:
        unknown = sorted(set(target) - set(FEATURE_BY_ID))
        if unknown:
            raise ValueError(f"unsupported feature id(s): {', '.join(unknown)}")
        for feature_id, spec in target.items():
            definition = FEATURE_BY_ID[feature_id]
            if not definition.min_value <= spec.value <= definition.max_value:
                raise ValueError(
                    f"value for {feature_id} must be within "
                    f"[{definition.min_value}, {definition.max_value}] {definition.unit}"
                )
        return target

    @field_validator("weights")
    @classmethod
    def known_weights(cls, weights: dict[str, float]) -> dict[str, float]:
        unknown = sorted(set(weights) - set(FEATURE_BY_ID))
        if unknown:
            raise ValueError(f"unsupported feature id(s) in weights: {', '.join(unknown)}")
        return weights

    @field_validator("filters")
    @classmethod
    def known_filters(cls, filters: dict[str, RangeFilterIn]) -> dict[str, RangeFilterIn]:
        unknown = sorted(set(filters) - set(FEATURE_BY_ID) - set(CONTEXT_FEATURE_BY_ID))
        if unknown:
            raise ValueError(
                "unsupported filter id(s): " + ", ".join(unknown) + ". Filters apply to "
                "registered similarity features or Earth land-cover context features."
            )
        for feature_id, constraint in filters.items():
            if feature_id in FEATURE_BY_ID:
                definition = FEATURE_BY_ID[feature_id]
                low, high, unit = definition.min_value, definition.max_value, definition.unit
            else:
                low, high, unit = 0.0, 1.0, CONTEXT_FEATURE_BY_ID[feature_id].unit
            for bound in (constraint.min, constraint.max):
                if bound is not None and not low <= bound <= high:
                    raise ValueError(
                        f"filter bounds for {feature_id} must be within [{low}, {high}] {unit}"
                    )
        return filters

    @model_validator(mode="after")
    def coherent_query(self) -> SearchRequest:
        if self.reference_id is not None and self.target:
            raise ValueError(
                "Provide either reference_id or target, not both. "
                "Reference mode derives target values from the verified reference."
            )
        if self.reference_id is None:
            if self.body != "custom":
                raise ValueError(
                    "A Moon/Mars body requires a verified reference_id. "
                    "Use body='custom' for a user-supplied target vector."
                )
            if not self.target:
                raise ValueError("Provide at least one target feature.")
            if not any(spec.weight > 0 for spec in self.target.values()):
                raise ValueError("At least one target feature must have a positive weight.")
            if self.weights:
                raise ValueError("weights are only accepted in reference_id mode")
        # Reference mode: weights are partial overrides. Unspecified reference features keep
        # their default positive weight, so a zero override is not an all-zero query. The
        # merged target is validated by normalize_target after reference lookup.
        return self


class MetricOut(BaseModel):
    feature_id: str
    unit: str
    target: float
    candidate: float
    delta: float
    scale: float
    weight: float
    normalized_delta: float
    contribution: float


class SearchResultOut(BaseModel):
    rank: int
    location_id: str
    display_name: str
    lat: float
    lon: float
    bbox: BBoxOut
    distance: float
    dimension_distances: dict[str, float]
    metrics: list[MetricOut]
    dataset_ids: list[str]
    data_kind: str
    quality_flags: list[str]
    caveats: list[str]


class ExclusionOut(BaseModel):
    location_id: str
    reason: str


class SearchResponse(BaseModel):
    contract_version: str
    method_version: str
    query_version: str
    data_version: str
    data_snapshot_id: str
    coverage: CoverageOut
    coverage_label: str
    requested_features: list[str]
    filters_applied: dict[str, object]
    indexed_candidate_count: int
    eligible_candidate_count: int
    excluded_candidate_count: int
    excluded: list[ExclusionOut]
    results: list[SearchResultOut]
    limitations: list[str]


class FeatureObservationOut(BaseModel):
    feature_id: str
    label: str
    value: float
    unit: str
    method_version: str
    extraction_scale_m: float
    valid_fraction: float
    quality: str
    scale: float
    description: str


class DatasetOut(BaseModel):
    dataset_id: str
    provider: str
    product_id: str
    title: str | None
    url: str
    license: str
    license_url: str
    access_timestamp: str
    source_crs: str | None
    native_resolution_m: float | None
    processing_version: str
    checksum: str | None
    source_kind: str
    notes: str | None


class LocationOut(BaseModel):
    location_id: str
    display_name: str
    body: Literal["Earth"]
    lat: float
    lon: float
    bbox: BBoxOut
    window_size_m: float
    analysis_resolution_m: float
    crs: str
    valid_fraction: float
    data_kind: str
    quality_flags: list[str]
    notes: str | None
    area_km2: float | None
    geometry: dict[str, Any] | None
    origin: str
    features: list[FeatureObservationOut]
    context_observations: list[ContextObservationOut]
    datasets: list[DatasetOut]
    known_limitations: list[str]
    contract_version: str
    method_version: str
    data_version: str
    data_snapshot_id: str


class ReferenceOut(BaseModel):
    reference_id: str
    body: Literal["Moon", "Mars"]
    display_name: str
    dataset_id: str
    lat: float | None
    lon: float | None
    bbox: BBoxOut | None
    window_size_m: float
    analysis_resolution_m: float
    crs: str
    valid_fraction: float
    quality_flags: list[str]
    notes: str | None
    features: list[FeatureObservationOut]


class ContextComparisonOut(BaseModel):
    feature_id: str
    unit: str
    target: float | None
    observed: float
    delta: float | None
    scale: float
    normalized_delta: float | None


class ContextOut(BaseModel):
    export_version: str
    note: str
    generated_at: str
    location: LocationOut
    query: dict[str, float] | None
    source_facts: list[str]
    computed_comparisons: list[ContextComparisonOut]
    limitations: list[str]


class HealthOut(BaseModel):
    status: Literal["ok"]
    data_version: str
    data_snapshot_id: str


class ErrorBody(BaseModel):
    code: str
    message: str
    details: list[dict[str, object]] = Field(default_factory=list)


class ErrorOut(BaseModel):
    error: ErrorBody


class GeoBBoxIn(BaseModel):
    model_config = {"extra": "forbid"}
    west: float
    south: float
    east: float
    north: float


class AoiAnalyzeRequest(BaseModel):
    model_config = {"extra": "forbid"}
    body: Literal["Earth"] = "Earth"
    bbox: GeoBBoxIn | None = None
    geometry: dict[str, Any] | None = None
    requested_features: list[str] | None = None
    reference_id: str | None = None
    idempotency_key: str | None = Field(default=None, max_length=128)


class PlanetaryAoiAnalyzeRequest(BaseModel):
    """On-demand Moon/Mars AOI analysis (Issue #132).

    Coordinates are planetocentric lon/lat degrees in the declared body frame. This is
    never Earth EPSG:4326. Only verified D01 terrain sources are recomputed; other
    dimensions stay explicit unsupported evidence instead of being fabricated.
    """

    model_config = {"extra": "forbid"}
    body: Literal["Moon", "Mars"]
    bbox: GeoBBoxIn
    requested_features: list[str] | None = None
    idempotency_key: str | None = Field(default=None, max_length=128)


class AoiAnalyzeResponse(BaseModel):
    job_id: str
    status: Literal["queued", "running", "completed", "partial", "failed"]
    area_id: str | None
    cache_key: str
    cached: bool
    provider_status: dict[str, object] = Field(default_factory=dict)
    result: dict[str, object] | None = None
    links: dict[str, str] = Field(default_factory=dict)
    body: str | None = None
    coordinate_frame: str | None = None
    method_version: str | None = None
    coverage_note: str | None = None
    dimension_support: dict[str, object] = Field(default_factory=dict)


class PlanetaryAoiEngineRequest(BaseModel):
    """Create a full-AOI Moon/Mars job with explicit six-dimension statuses.

    ``bbox`` is planetocentric lon/lat in the declared body frame, never Earth
    EPSG:4326. The engine route requests the whole bounded bbox; the response reports
    whether the persisted analysis envelope actually covered it (``full_aoi``).
    D02-D06 are reported as explicit status entries, not fabricated measurements.
    """

    model_config = {"extra": "forbid"}
    body: Literal["Moon", "Mars"]
    bbox: GeoBBoxIn
    bbox_lon_convention: Literal["signed", "360"] = "signed"
    requested_features: list[str] | None = None
    idempotency_key: str | None = Field(default=None, max_length=128)


class PlanetaryAoiEngineDimensionOut(BaseModel):
    dimension_code: str
    label: str
    status: Literal[
        "available",
        "evidence",
        "no_coverage",
        "not_indexed",
        "unsupported",
        "pending",
    ]
    value_origin: Literal[
        "observed_derived",
        "stored_evidence",
        "declared_source",
        "not_measured",
        "none",
    ]
    reason: str | None = None
    method_version: str | None = None
    features: list[dict[str, Any]] = Field(default_factory=list)
    provenance: dict[str, Any] = Field(default_factory=dict)
    support: dict[str, Any] = Field(default_factory=dict)
    limitations: list[str] = Field(default_factory=list)


class PlanetaryAoiEngineResponse(BaseModel):
    contract_version: str
    job_id: str
    status: Literal["queued", "running", "completed", "partial", "failed"]
    area_id: str | None
    cache_key: str
    cached: bool
    body: str | None = None
    coordinate_frame: str | None = None
    requested_bbox: dict[str, float] | None = None
    analysis_bbox: dict[str, float] | None = None
    full_aoi: bool | None = None
    analysis_scope: str
    coverage: dict[str, Any] = Field(default_factory=dict)
    method_version: str | None = None
    coverage_note: str | None = None
    data_version: str | None = None
    dimensions: dict[str, PlanetaryAoiEngineDimensionOut] = Field(default_factory=dict)
    dimension_status: dict[str, dict[str, Any]] = Field(default_factory=dict)
    provenance: dict[str, Any] = Field(default_factory=dict)
    support: dict[str, Any] = Field(default_factory=dict)
    provider_status: dict[str, object] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    result: dict[str, object] | None = None
    links: dict[str, str] = Field(default_factory=dict)


class JobOut(BaseModel):
    job_id: str
    status: Literal["queued", "running", "completed", "partial", "failed"]
    area_id: str | None
    body: str | None = None
    coordinate_frame: str | None = None
    provider_status: dict[str, object]
    error: dict[str, str] | None
    result: dict[str, object] | None
    created_at: str | None
    started_at: str | None
    finished_at: str | None


class DimensionOut(BaseModel):
    dimension_id: str
    label: str
    description: str
    score_role: str
    features: list[dict[str, Any]]


class DimensionsOut(BaseModel):
    contract_version: str
    method_version: str
    dimensions: list[DimensionOut]
    context_features: list[ContextFeatureOut]


class CoarseTargetSpec(BaseModel):
    value: float = Field(..., allow_inf_nan=False)
    weight: float = Field(default=1.0, ge=0, allow_inf_nan=False)


class GlobalCoverageOut(BaseModel):
    status: Literal["ready", "no_index"]
    snapshot_id: str | None
    label: str
    row_count: int
    regions: list[str]
    bbox: dict[str, float] | None
    method_version: str | None
    features: list[dict[str, Any]]
    note: str


class GlobalSearchRequest(BaseModel):
    model_config = {"extra": "forbid"}
    target: dict[str, CoarseTargetSpec]
    weights: dict[str, float] = Field(default_factory=dict)
    region_id: str | None = None
    limit: int = Field(default=10, ge=1, le=10)

    @field_validator("target")
    @classmethod
    def nonempty(cls, target: dict[str, CoarseTargetSpec]) -> dict[str, CoarseTargetSpec]:
        if not target:
            raise ValueError("at least one coarse target feature is required")
        return target

    @model_validator(mode="after")
    def positive_weight(self) -> GlobalSearchRequest:
        weights = {
            feature_id: self.weights.get(feature_id, spec.weight)
            for feature_id, spec in self.target.items()
        }
        if not any(weight > 0 for weight in weights.values()):
            raise ValueError("at least one coarse target weight must be positive")
        return self


class GlobalSearchResultOut(BaseModel):
    rank: int
    candidate_id: str
    grid_id: str
    region_id: str
    body: str
    bbox: dict[str, float]
    crs: str
    area_km2: float
    window_size_m: float
    stride_m: float
    source_resolution_m: float
    slope_mean_deg: float | None
    slope_p90_deg: float | None
    flat_fraction: float | None
    valid_fraction: float
    method_version: str
    distance: float
    qa_flags: list[str]


class GlobalSearchResponse(BaseModel):
    snapshot_id: str
    method_version: str
    index_version: str
    coverage_label: str
    indexed_candidate_count: int
    eligible_candidate_count: int
    excluded_candidate_count: int
    results: list[GlobalSearchResultOut]
    note: str


class CrossBodyReasonOut(BaseModel):
    code: str
    message: str


class CrossBodyMetricOut(BaseModel):
    feature_id: str
    label: str
    unit: str
    decision: str
    eligible: bool
    method_version: str
    earth_method_version: str
    support_ratio_vs_earth: float | None
    analysis_support_ratio_vs_earth: float | None
    reasons: list[CrossBodyReasonOut]


class CrossBodyBodyOut(BaseModel):
    body: str
    source_id: str
    region_id: str
    manifest: str
    method_version: str
    native_support_m: float | None
    analysis_support_m: float | None
    support_ratio_vs_earth: float | None
    analysis_support_ratio_vs_earth: float | None
    frame_kind: str
    sample_label: str
    vertical_unit_status: str
    metrics: list[CrossBodyMetricOut]
    limitations: list[str]
    quality_flags: list[str]


class CrossBodyEarthFeatureOut(BaseModel):
    feature_id: str
    label: str
    unit: str
    scale: float
    spatial_support: str


class CrossBodyEarthBaselineOut(BaseModel):
    method_version: str
    support_m: float
    frame_kind: str
    value_source: str
    features: list[CrossBodyEarthFeatureOut]


class CrossBodyCountsOut(BaseModel):
    evidence_sources: int
    scanned_metric_observations: int
    eligible_metric_observations: int
    excluded_metric_observations: int
    unknown_metric_observations: int
    ranked_candidates: int


class CrossBodyGateOut(BaseModel):
    gate_version: str
    ranked: bool
    ranking_enabled: bool
    status: Literal["blocked", "ready", "no_evidence"]
    claim_scope: str
    eligibility_contract: list[dict[str, str]]
    earth_baseline: CrossBodyEarthBaselineOut
    bodies: list[CrossBodyBodyOut]
    comparability_decisions: list[dict[str, Any]]
    blocking_features: list[str]
    uncertainty: dict[str, Any]
    ranked_candidates: list[dict[str, Any]]
    counts: CrossBodyCountsOut
    note: str


class RefinePlanRequest(BaseModel):
    model_config = {"extra": "forbid"}
    candidate_id: str
    per_side: int = Field(default=2, ge=1, le=4)


class RefineWindowOut(BaseModel):
    fine_window_id: str
    parent_candidate_id: str
    bbox_wgs84: dict[str, float]
    window_size_m: int
    method_version: str


class RefinePlanOut(BaseModel):
    candidate_id: str
    parent_method_version: str
    fine_method_version: str
    windows: list[RefineWindowOut]
    note: str


class GlobalRefineRequest(BaseModel):
    """Execute one bounded coarse-hit refinement through the durable AOI queue."""

    model_config = {"extra": "forbid"}
    candidate_id: str
    requested_features: list[str] | None = None
    idempotency_key: str | None = Field(default=None, max_length=128)


class GlobalRefineResponse(BaseModel):
    job_id: str
    status: Literal["queued", "running", "completed", "partial", "failed"]
    area_id: str
    candidate_id: str
    coarse_candidate_id: str
    coarse_region_id: str
    coarse_method_version: str
    fine_method_version: str
    focus_window: dict[str, object]
    cached: bool
    provider_status: dict[str, object] = Field(default_factory=dict)
    result: dict[str, object] | None = None
    links: dict[str, str]
    note: str


# ---------------------------------------------------------------------------
# Issue #116: feature eligibility registry and multi-dimensional search.
# Issue #141: optional dimension weights/enabled flags and versioned scoring.
# ---------------------------------------------------------------------------


class RoleSupportOut(BaseModel):
    supported: bool
    reason: str | None
    constraints: list[str] = Field(default_factory=list)


class MultiDimTargetIn(BaseModel):
    value: float = Field(..., allow_inf_nan=False)
    weight: float = Field(default=1.0, ge=0, allow_inf_nan=False)


class MultiDimRangeFilterIn(BaseModel):
    model_config = {"extra": "forbid"}

    min: float | None = Field(default=None, allow_inf_nan=False)
    max: float | None = Field(default=None, allow_inf_nan=False)

    @model_validator(mode="after")
    def ordered(self) -> MultiDimRangeFilterIn:
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError("filter min must be <= max")
        return self


class DimensionWeightIn(BaseModel):
    model_config = {"extra": "forbid"}

    weight: float = Field(default=1.0, ge=0, allow_inf_nan=False)
    enabled: bool = True


class MultiDimSearchRequest(BaseModel):
    model_config = {"extra": "forbid"}

    candidate_body: Literal["Earth", "Moon", "Mars"] = "Earth"
    reference_id: str | None = None
    targets: dict[str, MultiDimTargetIn] = Field(default_factory=dict)
    filters: dict[str, MultiDimRangeFilterIn] = Field(default_factory=dict)
    dimension_weights: dict[str, DimensionWeightIn] = Field(default_factory=dict)
    scoring_version: (
        Literal["feature-weighted-euclidean-v1", "dimension-weighted-rms-v1"] | None
    ) = None
    limit: int = Field(default=10, ge=1, le=50)
    offset: int = Field(default=0, ge=0)
    include_evidence: bool = True
    evidence_limit: int = Field(default=12, ge=1, le=50)

    @field_validator("dimension_weights")
    @classmethod
    def known_dimensions(
        cls, dimension_weights: dict[str, DimensionWeightIn]
    ) -> dict[str, DimensionWeightIn]:
        invalid = sorted(
            dimension_id
            for dimension_id in dimension_weights
            if not (
                dimension_id.startswith("D")
                and len(dimension_id) == 3
                and dimension_id[1:].isdigit()
            )
        )
        if invalid:
            raise ValueError(
                "unsupported dimension id(s) in dimension_weights: " + ", ".join(invalid)
            )
        return dimension_weights

    @model_validator(mode="after")
    def has_query(self) -> MultiDimSearchRequest:
        if not self.targets and not self.reference_id:
            raise ValueError("provide at least one target feature or a reference_id")
        if self.reference_id is not None and self.candidate_body != "Earth":
            raise ValueError(
                "the fine candidate index contains Earth windows only; candidate_body must be "
                "Earth when reference_id is used"
            )
        return self


class MultiDimContributionOut(BaseModel):
    feature_id: str
    dimension_id: str
    label: str
    unit: str
    target: float
    candidate: float
    delta: float
    scale: float
    weight: float
    normalized_delta: float
    contribution: float
    contribution_fraction: float
    measurement_family: str | None
    observation_id: str
    selector: dict[str, Any]
    support: dict[str, Any]
    uncertainty: dict[str, Any] | None


class MultiDimDimensionContributionOut(BaseModel):
    model_config = {"extra": "allow"}

    contribution: float
    distance: float
    dimension_weight: float | None = None
    feature_weight_sum: float | None = None
    mean_squared_normalized_delta: float | None = None
    feature_count: int | float | None = None


class MultiDimEvidenceRecordOut(BaseModel):
    observation_id: str
    dimension_id: str | None
    feature_id: str
    label: str
    state: str
    value_kind: str
    value: Any
    unit: str | None
    method_version: str
    stored_score_role: str
    registry_score_role: str
    legal_roles: list[str]
    support: dict[str, Any] | None
    quality_flags: list[str]
    dataset_ids: list[str]
    note: str


class MultiDimResultOut(BaseModel):
    rank: int
    location_id: str
    display_name: str
    body: str
    lat: float
    lon: float
    bbox: BBoxOut
    distance: float
    distance_scope: str
    dimension_contributions: dict[str, MultiDimDimensionContributionOut]
    contributions: list[MultiDimContributionOut]
    valid_features: list[str]
    data_kind: str
    quality_flags: list[str]
    evidence_records: list[MultiDimEvidenceRecordOut] | None = None


class MultiDimExcludedOut(BaseModel):
    location_id: str
    reason: str
    reasons: list[str]
    missing_features: list[str] = Field(default_factory=list)
    incompatible_features: list[str] = Field(default_factory=list)


class MultiDimCoverageFeatureOut(BaseModel):
    dimension_id: str
    label: str
    unit: str
    body_scope: list[str]
    weight: float
    comparison: str
    eligible_candidate_count: int
    candidate_pool_size: int
    coverage_fraction: float
    scored_result_count: int
    selector: dict[str, Any]
    support: dict[str, Any]


class MultiDimCoverageOut(BaseModel):
    candidate_pool_size: int
    requested_feature_count: int
    features: dict[str, MultiDimCoverageFeatureOut]
    scope_note: str


class MultiDimCrossBodyGateOut(BaseModel):
    reference_body: str | None
    candidate_body: str
    target_body: str
    rule: str


class MultiDimTargetResolvedOut(BaseModel):
    value: float
    weight: float
    unit: str
    dimension_id: str
    body_scope: list[str]
    measurement_family: str | None


class MultiDimScoringOut(BaseModel):
    version: str
    formula: str
    dimension_controls: dict[str, dict[str, Any]] = Field(default_factory=dict)
    active_dimensions: list[str] = Field(default_factory=list)
    note: str


class MultiDimSearchResponse(BaseModel):
    registry_version: str
    data_version: str
    data_snapshot_id: str
    scoring: MultiDimScoringOut | None = None
    candidate_body: str
    source_body: str
    cross_body_gate: MultiDimCrossBodyGateOut
    requested_features: list[str]
    requested_targets: dict[str, MultiDimTargetResolvedOut]
    filters_applied: dict[str, Any]
    candidate_pool_size: int
    filtered_candidate_count: int
    eligible_candidate_count: int
    excluded_candidate_count: int
    total_matched: int
    offset: int
    limit: int
    returned: int
    next_offset: int | None
    coverage: MultiDimCoverageOut
    excluded: list[MultiDimExcludedOut]
    results: list[MultiDimResultOut]
    limitations: list[str]


class FeatureCoverageOut(BaseModel):
    observation_count: int
    area_count: int
    by_body: dict[str, int]
    by_state: dict[str, int]
    value_kinds: list[str]
    units: list[str]
    method_versions: list[str]
    dataset_ids: list[str]
    numeric_observation_count: int
    observed_numeric_min: float | None
    observed_numeric_max: float | None
    area_ids: list[str]
    joined_candidate_count: int
    joined_candidate_ids: list[str]
    gated_candidate_count: int
    gated_candidate_ids: list[str]
    rejected_observation_count: int


class RegisteredFeatureOut(BaseModel):
    feature_id: str
    dimension_id: str
    dimension_label: str
    label: str
    unit: str
    value_kind: str
    body_scope: list[str]
    score_role: str
    roles: dict[str, RoleSupportOut]
    min_value: float | None
    max_value: float | None
    scale: float | None
    scale_rationale: str | None
    selector: dict[str, Any]
    method_versions: list[str]
    support: dict[str, Any]
    missing_policy: str
    qa_rules: list[str]
    comparability_gates: list[str]
    source: dict[str, Any]
    unsupported_reason: str | None
    measurement_family: str | None
    uncertainty_feature_ids: list[str]
    registry_version: str
    coverage: FeatureCoverageOut | None


class JoinAuditOut(BaseModel):
    join_key: str
    proximity_join_used: bool
    typed_area_count: int
    fine_candidate_count: int
    planetary_reference_count: int
    typed_areas_exactly_matching_fine_candidate: int
    typed_areas_exactly_matching_planetary_reference: int
    matched_fine_candidate_ids: list[str]
    matched_planetary_reference_ids: list[str]
    note: str


class AuditSummaryOut(BaseModel):
    registry_version: str
    registered_feature_count: int
    observed_feature_count: int
    similarity_feature_count: int
    similarity_feature_with_gated_values: int
    gated_area_count_by_feature: dict[str, int]
    role_support_counts: dict[str, int]
    by_dimension: dict[str, dict[str, int]]
    rejected_observation_count: int
    rejection_reason_counts: dict[str, int]


class FeatureRegistryOut(BaseModel):
    registry_version: str
    data_version: str
    filters: dict[str, str | None]
    features: list[RegisteredFeatureOut]
    join_audit: JoinAuditOut
    audit_summary: AuditSummaryOut
    notes: list[str]


class FeatureDetailOut(BaseModel):
    registry_version: str
    data_version: str
    feature: RegisteredFeatureOut
    join_audit: JoinAuditOut


# ---------------------------------------------------------------------------
# Issue #117: planetary map catalog, coordinate contract and overlays.
# ---------------------------------------------------------------------------


class PlanetaryBodyOut(BaseModel):
    body: str
    mean_radius_m: float
    polar_radius_m: float
    flattening: float
    frame: str
    coordinate_system: str
    latitude_kind: str
    longitude_direction: str
    native_longitude_domain: str
    api_longitude_domain: str
    geographic_epsg: int
    geographic_epsg_uri: str
    geographic_epsg_authority: str
    geographic_epsg_is_epsg_registry: bool
    earth_wgs84: bool
    notes: str


class PlanetaryBBoxOut(BaseModel):
    west: float
    south: float
    east: float
    north: float
    crosses_antimeridian: bool
    longitude_span_deg: float
    source: str


class PlanetaryZoomOut(BaseModel):
    min: int
    max: int


class PlanetaryTileMatrixOut(BaseModel):
    zoom: int
    matrix_width: int
    matrix_height: int


class PlanetaryLayerVerificationOut(BaseModel):
    status: str
    checked_at: str
    http_status: int
    content_type: str
    sample_tile_url: str
    capabilities_http_status: int


class PlanetaryLayerOut(BaseModel):
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
    zoom: PlanetaryZoomOut
    tile_size: int
    tile_matrix: list[PlanetaryTileMatrixOut]
    bounds: PlanetaryBBoxOut
    attribution: str
    source_url: str
    capabilities_url: str
    license: str
    license_url: str
    verification: PlanetaryLayerVerificationOut
    notes: list[str]
    fallback: str | None


class PlanetaryCoverageSeparationOut(BaseModel):
    basemap_extent: str
    scientific_coverage: str
    statement: str


class PlanetaryMapsOut(BaseModel):
    catalog_version: str
    coordinate_contract_version: str
    data_version: str
    body: PlanetaryBodyOut
    layers: list[PlanetaryLayerOut]
    science_coverage: dict[str, Any]
    coverage_separation: PlanetaryCoverageSeparationOut
    notes: list[str]


class PlanetaryCenterOut(BaseModel):
    lon: float
    lat: float


class PlanetaryDimensionStatusOut(BaseModel):
    status: str
    record_count: int
    value_origin: str | None
    comparability_gate: str


class PlanetaryScienceCoverageOut(BaseModel):
    model_config = {"extra": "allow"}

    d01_feature_count: int
    d01_method_versions: list[str] = Field(default_factory=list)
    d01_analysis_resolution_m: float | None = None
    d01_window_size_m: float | None = None
    typed_dimensions: list[str] = Field(default_factory=list)
    typed_observation_count: int = 0
    typed_state_counts: dict[str, int] = Field(default_factory=dict)
    typed_feature_ids: list[str] = Field(default_factory=list)
    value_origins: list[str] = Field(default_factory=list)
    dimension_status: dict[str, PlanetaryDimensionStatusOut]
    coverage_label: str


class PlanetaryFeatureOut(BaseModel):
    feature_id: str
    feature_type: Literal["planetary_reference", "typed_area"]
    body: Literal["Moon", "Mars"]
    display_name: str
    reference_id: str | None
    typed_area_id: str | None
    linked_feature_id: str | None
    relationship: str
    bbox: PlanetaryBBoxOut
    center: PlanetaryCenterOut
    geometry: dict[str, Any]
    bbox_geometry: dict[str, Any]
    science_coverage: PlanetaryScienceCoverageOut
    availability: str
    quality_flags: list[str]
    crs: str | None
    coordinate_frame: str | None = None
    data_version: str | None
    detail_links: dict[str, str | None]


class PlanetaryMapFeaturesOut(BaseModel):
    catalog_version: str
    coordinate_contract_version: str
    data_version: str
    body: PlanetaryBodyOut
    bbox: PlanetaryBBoxOut
    bbox_crs: str
    coordinate_system: str
    query: dict[str, Any]
    total: int
    returned: int
    offset: int
    limit: int
    next_offset: int | None
    unlocatable_reference_count: int
    unlocatable_typed_area_count: int
    features: list[PlanetaryFeatureOut]
    science_coverage_summary: dict[str, Any]
    notes: list[str]


class PlanetaryCoordinateContractOut(BaseModel):
    contract_version: str
    bodies: list[PlanetaryBodyOut]
    rules: list[str]
    antimeridian: dict[str, Any]
    poles: dict[str, Any]
    longitude_conversion: dict[str, Any]
    planetocentric_to_planetographic: dict[str, Any]
    geojson: dict[str, Any]
