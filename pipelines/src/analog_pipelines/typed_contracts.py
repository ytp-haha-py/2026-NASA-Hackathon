"""Additive v2 typed observation contracts (Issue #26).

This module does not replace the v1 manifest. It adds versioned Pydantic models
for dimensions, areas, assets, typed observations, processing runs and snapshots,
plus a lossless-as-possible v1 -> v2 adapter that preserves feature IDs and method
versions. Missing states are explicit; a valid physical zero is not missing.
"""

from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Body = Literal["Earth", "Moon", "Mars"]
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
ObservationState = Literal["detected", "not_detected", "no_coverage", "unknown", "not_applicable"]
ScoreRole = Literal["similarity", "context_only", "filter", "evidence_only", "not_comparable"]
VerificationStatus = Literal["verified", "accessible", "identified", "planned", "unknown"]

SCHEMA_VERSION = "2.0.0"
MISSING_STATES = {"not_detected", "no_coverage", "unknown", "not_applicable"}


class Uncertainty(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal[
        "none", "standard_deviation", "confidence_interval", "published_range", "unknown"
    ] = "unknown"
    value: float | None = Field(default=None, allow_inf_nan=False)
    lower: float | None = Field(default=None, allow_inf_nan=False)
    upper: float | None = Field(default=None, allow_inf_nan=False)
    unit: str | None = None
    notes: str | None = None


class SpatialSupport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["window", "bbox", "point", "geometry", "unknown"] = "unknown"
    crs: str = "unknown"
    resolution_m: float | None = Field(default=None, gt=0)
    window_size_m: float | None = Field(default=None, gt=0)
    bbox: dict[str, float] | None = None
    area_km2: float | None = Field(default=None, gt=0)
    native_resolution_m: float | None = Field(default=None, gt=0)
    notes: str | None = None

    @model_validator(mode="after")
    def support_is_coherent(self) -> SpatialSupport:
        if self.kind == "unknown":
            return self
        if not self.crs.strip() or self.crs == "unknown":
            raise ValueError("non-unknown spatial support requires a CRS")
        if self.kind in {"window", "bbox", "geometry"} and not self.bbox and self.kind != "window":
            raise ValueError(f"{self.kind} support requires a bbox")
        if self.bbox:
            for key in ("west", "south", "east", "north"):
                if key not in self.bbox:
                    raise ValueError(f"bbox missing {key}")
            if self.bbox["east"] <= self.bbox["west"] or self.bbox["north"] <= self.bbox["south"]:
                raise ValueError("bbox must have east>west and north>south")
        return self


class TemporalSupport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["static", "instant", "interval", "composite", "unknown"] = "unknown"
    start: str | None = None
    end: str | None = None
    local_solar_time: str | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def interval_has_endpoints(self) -> TemporalSupport:
        if self.kind in {"instant", "interval", "composite"} and not self.start:
            raise ValueError(f"{self.kind} temporal support requires start")
        return self


class DepthSupport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["surface", "depth_interval", "unknown"] = "surface"
    min_m: float | None = None
    max_m: float | None = None

    @model_validator(mode="after")
    def depth_order(self) -> DepthSupport:
        if self.kind == "depth_interval":
            if self.min_m is None or self.max_m is None:
                raise ValueError("depth_interval requires min_m and max_m")
            if self.min_m < 0 or self.max_m < self.min_m:
                raise ValueError("invalid depth interval")
        return self


class ProcessEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    process_code: str
    evidence_type: str
    description: str = ""
    references: list[str] = Field(default_factory=list)


class AreaV2(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["2.0.0"] = SCHEMA_VERSION
    area_id: str
    body: Body
    geometry: dict[str, Any] | None = None
    bbox: dict[str, float] | None = None
    crs: str
    coordinate_frame: Literal[
        "earth_wgs84", "earth_projected", "lunar_projected", "mars_projected", "unknown"
    ] = "unknown"
    area_km2: float | None = Field(default=None, gt=0)
    source_area_refs: list[str] = Field(default_factory=list)
    notes: str | None = None

    @model_validator(mode="after")
    def planetary_frame(self) -> AreaV2:
        if self.body == "Moon" and self.coordinate_frame == "earth_wgs84":
            raise ValueError("Moon area cannot use Earth WGS84 as its analysis frame")
        if self.body == "Mars" and self.coordinate_frame == "earth_wgs84":
            raise ValueError("Mars area cannot use Earth WGS84 as its analysis frame")
        if not self.crs.strip():
            raise ValueError("area requires a CRS")
        return self


class DatasetV2(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["2.0.0"] = SCHEMA_VERSION
    dataset_id: str
    body: Body
    provider: str
    product_id: str
    version: str = "unknown"
    url: str | None = None
    license: str = "unknown"
    license_url: str | None = None
    verification_status: VerificationStatus = "unknown"
    native_crs: str | None = None
    native_resolution_m: float | None = Field(default=None, gt=0)
    temporal_support: str = "unknown"
    depth_support: str = "unknown"
    redistribution_allowed: Literal["yes", "no", "unknown"] = "unknown"

    @model_validator(mode="after")
    def verified_has_provenance(self) -> DatasetV2:
        if self.verification_status == "verified":
            for field in ("url", "license", "license_url"):
                value = getattr(self, field)
                if not value or value == "unknown":
                    raise ValueError(f"verified dataset requires {field}")
        return self


class SourceAssetV2(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["2.0.0"] = SCHEMA_VERSION
    asset_id: str
    dataset_id: str
    uri: str
    checksum_algorithm: Literal["sha256", "md5", "etag", "unknown"] = "unknown"
    checksum: str | None = None
    checksum_scope: Literal["source", "derived", "window", "unknown"] = "unknown"
    native_crs: str | None = None
    native_units: str | None = None
    scale: float | None = None
    offset: float | None = None
    nodata: float | None = None
    bytes: int | None = Field(default=None, ge=0)
    fetched_at: str | None = None
    redistribution_allowed: Literal["yes", "no", "unknown"] = "unknown"

    @model_validator(mode="after")
    def checksum_pair(self) -> SourceAssetV2:
        if (self.checksum_algorithm == "unknown") != (self.checksum is None):
            raise ValueError("checksum_algorithm and checksum must be supplied together")
        return self


class TypedObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["2.0.0"] = SCHEMA_VERSION
    observation_id: str
    area_id: str
    body: Body
    dimension_code: DimensionCode | None
    feature_id: str
    value_kind: ValueKind
    state: ObservationState
    value: Any = None
    unit: str | None = None
    uncertainty: Uncertainty | None = None
    spatial_support: SpatialSupport
    temporal_support: TemporalSupport = Field(default_factory=TemporalSupport)
    depth_support: DepthSupport = Field(default_factory=DepthSupport)
    method_version: str
    source_asset_ids: list[str] = Field(min_length=1)
    quality_flags: list[str] = Field(default_factory=list)
    score_role: ScoreRole = "evidence_only"
    evidence: ProcessEvidence | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def type_and_state(self) -> TypedObservation:
        if self.state == "detected":
            if self.value is None:
                raise ValueError("detected observation requires a value (zero is valid)")
        elif self.state in MISSING_STATES:
            if self.value is not None:
                raise ValueError(f"{self.state} observation must not carry a value")
        else:  # pragma: no cover - Literal protects this
            raise ValueError(f"unknown state {self.state}")

        if not self.method_version.strip():
            raise ValueError("method_version is required")

        if self.value_kind == "numeric":
            self._validate_numeric()
        elif self.value_kind == "category":
            self._validate_category()
        elif self.value_kind == "distribution":
            self._validate_distribution()
        elif self.value_kind == "detection":
            self._validate_detection()
        elif self.value_kind == "timeseries":
            self._validate_timeseries()
        elif self.value_kind == "spectrum":
            self._validate_spectrum()
        elif self.value_kind == "process_evidence":
            self._validate_process()
        return self

    def _require_unit(self) -> str:
        if not self.unit or self.unit == "unknown":
            raise ValueError(f"{self.value_kind} observation requires a physical unit")
        return self.unit

    def _validate_numeric(self) -> None:
        if self.state != "detected":
            return
        if isinstance(self.value, bool) or not isinstance(self.value, (int, float)):
            raise ValueError("numeric observation requires a number")
        if not math.isfinite(float(self.value)):
            raise ValueError("numeric observation must be finite")
        self._require_unit()

    def _validate_category(self) -> None:
        if self.state != "detected":
            return
        if isinstance(self.value, str):
            if not self.value:
                raise ValueError("category code cannot be empty")
            return
        if not isinstance(self.value, dict):
            raise ValueError("category value must be a code string or object")
        if not {"code", "scheme"} <= set(self.value):
            raise ValueError("category object requires code and scheme")

    def _validate_distribution(self) -> None:
        if self.state != "detected":
            return
        if not isinstance(self.value, dict):
            raise ValueError("distribution value must be an object")
        bins = self.value.get("bins")
        fractions = self.value.get("fractions")
        if not isinstance(bins, list) or not isinstance(fractions, list):
            raise ValueError("distribution requires bins and fractions arrays")
        if len(bins) != len(fractions) or not bins:
            raise ValueError("distribution bins/fractions length mismatch")
        if any(
            isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x)
            for x in bins
        ):
            raise ValueError("distribution bins must be finite numbers")
        if any(
            isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x)
            for x in fractions
        ):
            raise ValueError("distribution fractions must be finite numbers")
        if any(x < 0 or x > 1 for x in fractions):
            raise ValueError("distribution fractions must be within [0, 1]")
        if not math.isclose(math.fsum(float(x) for x in fractions), 1.0, abs_tol=1e-6):
            raise ValueError("distribution fractions must sum to 1 within 1e-6")

    def _validate_detection(self) -> None:
        if self.state != "detected":
            return
        if not isinstance(self.value, dict):
            raise ValueError("detection value must be an object")
        indicator = self.value.get("indicator")
        if indicator not in (0, 1):
            raise ValueError("detection indicator must be 0 or 1")
        confidence = self.value.get("confidence")
        if confidence is not None and (
            not isinstance(confidence, (int, float))
            or isinstance(confidence, bool)
            or not 0 <= float(confidence) <= 1
        ):
            raise ValueError("detection confidence must be within [0,1]")

    def _validate_timeseries(self) -> None:
        if self.state != "detected":
            return
        if not isinstance(self.value, dict):
            raise ValueError("timeseries value must be an object")
        timestamps = self.value.get("timestamps")
        values = self.value.get("values")
        if not isinstance(timestamps, list) or not isinstance(values, list):
            raise ValueError("timeseries requires timestamps and values arrays")
        if not timestamps or len(timestamps) != len(values):
            raise ValueError("timeseries arrays must be non-empty and equal length")
        if any(
            isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
            for v in values
        ):
            raise ValueError("timeseries values must be finite numbers")
        self._require_unit()

    def _validate_spectrum(self) -> None:
        if self.state != "detected":
            return
        if not isinstance(self.value, dict):
            raise ValueError("spectrum value must be an object")
        wavelengths = self.value.get("wavelengths")
        values = self.value.get("values")
        if not isinstance(wavelengths, list) or not isinstance(values, list):
            raise ValueError("spectrum requires wavelengths and values arrays")
        if not wavelengths or len(wavelengths) != len(values):
            raise ValueError("spectrum arrays must be non-empty and equal length")
        if any(
            isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
            for v in [*wavelengths, *values]
        ):
            raise ValueError("spectrum arrays must contain finite numbers")
        if not self.value.get("wavelength_unit"):
            raise ValueError("spectrum requires a wavelength_unit")

    def _validate_process(self) -> None:
        if self.state != "detected":
            return
        if not isinstance(self.value, dict) or not {"process_code", "evidence_type"} <= set(
            self.value
        ):
            raise ValueError("process_evidence requires process_code and evidence_type")


class ProcessingRunV2(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["2.0.0"] = SCHEMA_VERSION
    run_id: str
    started_at: str
    finished_at: str | None = None
    bounded: bool = True
    limits: dict[str, int | float] = Field(default_factory=dict)
    input_asset_ids: list[str]
    input_checksums: dict[str, str] = Field(default_factory=dict)
    window_selection_rule: str
    method_versions: dict[str, str]
    output_checksum: str | None = None
    counts: dict[str, int] = Field(default_factory=dict)
    status: Literal["completed", "partial", "failed", "no_coverage", "dry_run"]
    command: str | None = None
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def bounded_run(self) -> ProcessingRunV2:
        if not self.bounded:
            raise ValueError("v2 processing runs must declare bounded=True")
        for key in ("max_regions", "max_windows", "max_pixels", "max_bytes"):
            value = self.limits.get(key)
            if value is not None and value <= 0:
                raise ValueError(f"{key} must be positive")
        if self.status == "completed" and not self.output_checksum:
            raise ValueError("completed run requires output_checksum")
        return self


class SnapshotMetadataV2(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["2.0.0"] = SCHEMA_VERSION
    snapshot_id: str
    created_at: str
    source_commit: str | None = None
    schema_versions: dict[str, str]
    manifests: list[dict[str, Any]]
    artifacts: list[dict[str, Any]]
    data_version: str
    coverage: dict[str, Any] = Field(default_factory=dict)
    notes: str = ""


class V2Migration(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ontology_version: str = "1.0.0"
    areas: list[AreaV2]
    observations: list[TypedObservation]
    warnings: list[str] = Field(default_factory=list)


def _area_from_candidate(candidate: dict[str, Any], body: Body = "Earth") -> AreaV2:
    frame = {
        "Earth": "earth_wgs84",
        "Moon": "lunar_projected",
        "Mars": "mars_projected",
    }[body]
    bbox = candidate.get("bbox")
    crs = candidate.get("crs") or "unknown"
    return AreaV2(
        area_id=candidate["location_id"] if body == "Earth" else candidate["reference_id"],
        body=body,
        geometry=candidate.get("geometry"),
        bbox=bbox,
        crs=crs,
        coordinate_frame=frame if crs != "unknown" else "unknown",
        area_km2=candidate.get("area_km2"),
        notes=candidate.get("notes"),
    )


def _observation_from_v1(
    *,
    record_id: str,
    body: Body,
    feature_id: str,
    observation: dict[str, Any],
    legacy_dimension: str,
    source_dataset_id: str,
    analysis_crs: str,
) -> TypedObservation:
    dimension = None if legacy_dimension == "land_cover_context" else "D01"
    role: ScoreRole = "context_only" if dimension is None else "similarity"
    return TypedObservation(
        observation_id=f"v1:{record_id}:{feature_id}",
        area_id=record_id,
        body=body,
        dimension_code=dimension,
        feature_id=feature_id,
        value_kind="numeric",
        state="detected",
        value=float(observation["value"]),
        unit=observation["unit"],
        spatial_support=SpatialSupport(
            kind="window",
            crs=analysis_crs,
            window_size_m=1980.0,
            resolution_m=90.0,
            notes="Migrated v1 analysis support; CRS copied from the v1 record.",
        ),
        temporal_support=TemporalSupport(kind="static"),
        method_version=observation["method_version"],
        source_asset_ids=[source_dataset_id],
        quality_flags=[observation.get("quality", "unknown")],
        score_role=role,
        notes="Migrated from a v1 manifest without changing IDs or method versions.",
    )


def migrate_v1_manifest(manifest: dict[str, Any]) -> V2Migration:
    """Convert a v1 manifest into additive typed areas/observations.

    The v1 manifest is never mutated. Context observations remain Earth-only context;
    they never become a scored dimension. Reference areas use planetary frames.
    """
    areas: list[AreaV2] = []
    observations: list[TypedObservation] = []
    warnings: list[str] = []
    for candidate in manifest.get("candidates", []):
        area = _area_from_candidate(candidate, "Earth")
        areas.append(area)
        for feature_id, observation in candidate.get("features", {}).items():
            observations.append(
                _observation_from_v1(
                    record_id=area.area_id,
                    body="Earth",
                    feature_id=feature_id,
                    observation=observation,
                    legacy_dimension="terrain_shape",
                    source_dataset_id=candidate["dataset_id"],
                    analysis_crs=candidate.get("crs") or "unknown",
                )
            )
        for feature_id, observation in candidate.get("context_observations", {}).items():
            observations.append(
                _observation_from_v1(
                    record_id=area.area_id,
                    body="Earth",
                    feature_id=feature_id,
                    observation=observation,
                    legacy_dimension="land_cover_context",
                    source_dataset_id=observation.get("dataset_id") or candidate["dataset_id"],
                    analysis_crs=candidate.get("crs") or "unknown",
                )
            )
    for reference in manifest.get("references", []):
        body = reference["body"]
        area = _area_from_candidate(reference, body)
        areas.append(area)
        for feature_id, observation in reference.get("features", {}).items():
            observations.append(
                _observation_from_v1(
                    record_id=area.area_id,
                    body=body,
                    feature_id=feature_id,
                    observation=observation,
                    legacy_dimension="terrain_shape",
                    source_dataset_id=reference["dataset_id"],
                    analysis_crs=reference.get("crs") or "unknown",
                )
            )
    return V2Migration(areas=areas, observations=observations, warnings=warnings)
