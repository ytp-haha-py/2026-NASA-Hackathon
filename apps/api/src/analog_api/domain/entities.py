from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class BBox:
    west: float
    south: float
    east: float
    north: float

    def as_dict(self) -> dict[str, float]:
        return {"west": self.west, "south": self.south, "east": self.east, "north": self.north}


@dataclass(frozen=True, slots=True)
class FeatureValue:
    value: float
    unit: str
    method_version: str
    extraction_scale_m: float
    valid_fraction: float
    quality: str


@dataclass(frozen=True, slots=True)
class ContextValue:
    value: float
    unit: str
    method_version: str
    source_resolution_m: float
    valid_fraction: float
    quality: str
    class_code: int
    provider: str
    dataset_id: str | None = None


@dataclass(frozen=True, slots=True)
class DatasetEntity:
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


@dataclass(frozen=True, slots=True)
class CandidateEntity:
    location_id: str
    display_name: str
    lat: float
    lon: float
    bbox: BBox
    dataset_id: str
    window_size_m: float
    analysis_resolution_m: float
    crs: str
    valid_fraction: float
    quality_flags: tuple[str, ...]
    notes: str | None
    data_kind: str
    area_km2: float | None = None
    geometry: dict[str, object] | None = None
    origin: str = "indexed"
    features: dict[str, FeatureValue] = field(default_factory=dict)
    context_observations: dict[str, ContextValue] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ReferenceEntity:
    reference_id: str
    body: str
    display_name: str
    dataset_id: str
    lat: float | None
    lon: float | None
    bbox: BBox | None
    window_size_m: float
    analysis_resolution_m: float
    crs: str
    valid_fraction: float
    quality_flags: tuple[str, ...]
    notes: str | None
    area_km2: float | None = None
    geometry: dict[str, object] | None = None
    origin: str = "indexed"
    features: dict[str, FeatureValue] = field(default_factory=dict)
    context_observations: dict[str, ContextValue] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AnalysisJobEntity:
    job_id: str
    status: str
    cache_key: str
    idempotency_key: str | None
    area_id: str | None
    request: dict[str, object]
    provider_status: dict[str, object]
    result: dict[str, object] | None
    error_code: str | None
    error_message: str | None
