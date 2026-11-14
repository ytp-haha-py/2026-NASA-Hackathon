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
    min: float | None = Field(default=None, ge=0, le=1)
    max: float | None = Field(default=None, ge=0, le=1)

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
        unknown = sorted(set(filters) - set(CONTEXT_FEATURE_BY_ID))
        if unknown:
            raise ValueError(
                "unsupported context filter id(s): " + ", ".join(unknown) + ". Filters apply "
                "only to Earth land-cover context features; they are not similarity dimensions."
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
        else:
            if not self.weights:
                return self
            if not any(weight > 0 for weight in self.weights.values()):
                raise ValueError("At least one weight must be positive.")
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


class AoiAnalyzeResponse(BaseModel):
    job_id: str
    status: Literal["queued", "running", "completed", "partial", "failed"]
    area_id: str | None
    cache_key: str
    cached: bool
    provider_status: dict[str, object] = Field(default_factory=dict)
    result: dict[str, object] | None = None
    links: dict[str, str] = Field(default_factory=dict)


class JobOut(BaseModel):
    job_id: str
    status: Literal["queued", "running", "completed", "partial", "failed"]
    area_id: str | None
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
