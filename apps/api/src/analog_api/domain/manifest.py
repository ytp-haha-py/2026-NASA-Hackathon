from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from analog_api.domain.features import (
    CONTEXT_FEATURE_BY_ID,
    CONTEXT_METHOD_VERSION,
    FEATURE_BY_ID,
    METHOD_VERSION,
)


class ObservationRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: float
    unit: str
    method_version: str
    extraction_scale_m: float = Field(gt=0)
    valid_fraction: float = Field(ge=0, le=1)
    quality: Literal["verified", "synthetic", "suspect"]

    @field_validator("value")
    @classmethod
    def finite_value(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("value must be finite")
        return value


class BBoxRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    west: float = Field(ge=-180, le=180)
    south: float = Field(ge=-90, le=90)
    east: float = Field(ge=-180, le=180)
    north: float = Field(ge=-90, le=90)

    @model_validator(mode="after")
    def ordered(self) -> BBoxRecord:
        if self.east <= self.west or self.north <= self.south:
            raise ValueError("bbox must satisfy east > west and north > south")
        return self


class DatasetRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset_id: str
    provider: str
    product_id: str
    title: str | None = None
    url: str
    license: str
    license_url: str
    access_timestamp: str
    source_crs: str | None = None
    native_resolution_m: float | None = None
    processing_version: str
    checksum: str | None = None
    source_kind: Literal["earth_dem", "lunar_dem", "martian_dem", "landcover", "synthetic"]
    notes: str | None = None


def _validate_features(features: dict[str, ObservationRecord]) -> dict[str, ObservationRecord]:
    unknown = sorted(set(features) - set(FEATURE_BY_ID))
    if unknown:
        raise ValueError(f"unsupported feature id(s) in manifest: {', '.join(unknown)}")
    for feature_id, observation in features.items():
        definition = FEATURE_BY_ID[feature_id]
        if observation.unit != definition.unit:
            raise ValueError(
                f"unit mismatch for {feature_id}: manifest uses {observation.unit!r}, "
                f"registry requires {definition.unit!r}"
            )
        if observation.method_version != METHOD_VERSION:
            raise ValueError(
                f"incompatible method_version for {feature_id}: "
                f"{observation.method_version!r} != {METHOD_VERSION!r}"
            )
        if not (definition.min_value <= observation.value <= definition.max_value):
            raise ValueError(
                f"value {observation.value} for {feature_id} is outside the valid domain "
                f"[{definition.min_value}, {definition.max_value}]"
            )
    return features


class ContextObservationRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: float = Field(ge=0, le=1)
    unit: Literal["fraction"]
    method_version: str
    source_resolution_m: float = Field(gt=0)
    valid_fraction: float = Field(ge=0, le=1)
    quality: Literal["verified", "synthetic", "suspect"]
    class_code: int
    provider: str
    dataset_id: str | None = None

    @model_validator(mode="after")
    def compatible(self) -> ContextObservationRecord:
        if self.method_version != CONTEXT_METHOD_VERSION:
            raise ValueError(
                f"incompatible context method_version {self.method_version!r}; expected "
                f"{CONTEXT_METHOD_VERSION!r}"
            )
        return self


def _validate_context(
    context: dict[str, ContextObservationRecord],
) -> dict[str, ContextObservationRecord]:
    unknown = sorted(set(context) - set(CONTEXT_FEATURE_BY_ID))
    if unknown:
        raise ValueError(f"unsupported context feature id(s): {', '.join(unknown)}")
    for feature_id, observation in context.items():
        definition = CONTEXT_FEATURE_BY_ID[feature_id]
        if observation.class_code != definition.class_code:
            raise ValueError(
                f"class_code mismatch for {feature_id}: {observation.class_code} != "
                f"{definition.class_code}"
            )
    return context


class CandidateRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    location_id: str = Field(pattern=r"^earth-[a-z0-9-]+$")
    display_name: str
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    bbox: BBoxRecord
    dataset_id: str
    window_size_m: float = Field(gt=0)
    analysis_resolution_m: float = Field(gt=0)
    crs: str
    valid_fraction: float = Field(ge=0, le=1)
    quality_flags: list[str] = Field(default_factory=list)
    notes: str | None = None
    area_km2: float | None = Field(default=None, gt=0)
    geometry: dict[str, object] | None = None
    origin: Literal["indexed", "on_demand"] = "indexed"
    features: dict[str, ObservationRecord]
    context_observations: dict[str, ContextObservationRecord] = Field(default_factory=dict)

    @field_validator("features")
    @classmethod
    def check_features(cls, value: dict[str, ObservationRecord]) -> dict[str, ObservationRecord]:
        return _validate_features(value)

    @field_validator("context_observations")
    @classmethod
    def check_context(
        cls, value: dict[str, ContextObservationRecord]
    ) -> dict[str, ContextObservationRecord]:
        return _validate_context(value)


class ReferenceRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reference_id: str
    body: Literal["Moon", "Mars"]
    display_name: str
    dataset_id: str
    lat: float | None = Field(default=None, ge=-90, le=90)
    lon: float | None = Field(default=None, ge=-180, le=180)
    bbox: BBoxRecord | None = None
    window_size_m: float = Field(gt=0)
    analysis_resolution_m: float = Field(gt=0)
    crs: str
    valid_fraction: float = Field(ge=0, le=1)
    quality_flags: list[str] = Field(default_factory=list)
    notes: str | None = None
    area_km2: float | None = Field(default=None, gt=0)
    geometry: dict[str, object] | None = None
    origin: Literal["indexed", "on_demand"] = "indexed"
    features: dict[str, ObservationRecord]
    context_observations: dict[str, ContextObservationRecord] = Field(default_factory=dict)

    @field_validator("features")
    @classmethod
    def check_features(cls, value: dict[str, ObservationRecord]) -> dict[str, ObservationRecord]:
        return _validate_features(value)

    @field_validator("context_observations")
    @classmethod
    def check_context(
        cls, value: dict[str, ContextObservationRecord]
    ) -> dict[str, ContextObservationRecord]:
        return _validate_context(value)


class Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    manifest_version: Literal["1.0.0"]
    contract_version: Literal["1.0.0"]
    generated_by: str
    generated_at: str | None = None
    data_kind: Literal["observed", "synthetic"]
    datasets: list[DatasetRecord]
    candidates: list[CandidateRecord]
    references: list[ReferenceRecord] = Field(default_factory=list)

    @model_validator(mode="after")
    def referential_integrity(self) -> Manifest:
        dataset_ids = [d.dataset_id for d in self.datasets]
        if len(dataset_ids) != len(set(dataset_ids)):
            raise ValueError("duplicate dataset_id in manifest")
        known = set(dataset_ids)
        for candidate in self.candidates:
            if candidate.dataset_id not in known:
                raise ValueError(
                    f"candidate {candidate.location_id} references unknown dataset "
                    f"{candidate.dataset_id}"
                )
        for reference in self.references:
            if reference.dataset_id not in known:
                raise ValueError(
                    f"reference {reference.reference_id} references unknown dataset "
                    f"{reference.dataset_id}"
                )
        for record in [*self.candidates, *self.references]:
            for observation in record.context_observations.values():
                if observation.dataset_id and observation.dataset_id not in known:
                    raise ValueError(
                        f"context observation references unknown dataset {observation.dataset_id}"
                    )
        if self.data_kind == "synthetic" and any(
            obs.quality != "synthetic"
            for record in [*self.candidates, *self.references]
            for obs in record.features.values()
        ):
            raise ValueError("synthetic manifests must mark every observation as 'synthetic'")
        return self
