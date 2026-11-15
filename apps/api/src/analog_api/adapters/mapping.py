"""Map persistence rows to the storage-independent domain entities."""

from __future__ import annotations

from collections.abc import Sequence

from analog_api.adapters.db import (
    Candidate,
    ContextObservation,
    Dataset,
    FeatureObservation,
    PlanetaryReference,
    ReferenceFeatureObservation,
)
from analog_api.domain.entities import (
    BBox,
    CandidateEntity,
    ContextValue,
    DatasetEntity,
    FeatureValue,
    ReferenceEntity,
)


def feature_map(
    observations: Sequence[FeatureObservation | ReferenceFeatureObservation],
) -> dict[str, FeatureValue]:
    return {
        obs.feature_id: FeatureValue(
            value=obs.value,
            unit=obs.unit,
            method_version=obs.method_version,
            extraction_scale_m=obs.extraction_scale_m,
            valid_fraction=obs.valid_fraction,
            quality=obs.quality,
        )
        for obs in observations
    }


def dataset_entity(entity: Dataset) -> DatasetEntity:
    return DatasetEntity(
        dataset_id=entity.dataset_id,
        provider=entity.provider,
        product_id=entity.product_id,
        title=entity.title,
        url=entity.url,
        license=entity.license,
        license_url=entity.license_url,
        access_timestamp=entity.access_timestamp,
        source_crs=entity.source_crs,
        native_resolution_m=entity.native_resolution_m,
        processing_version=entity.processing_version,
        checksum=entity.checksum,
        source_kind=entity.source_kind,
        notes=entity.notes,
    )


def context_map(
    observations: Sequence[ContextObservation],
) -> dict[str, ContextValue]:
    return {
        obs.feature_id: ContextValue(
            value=obs.value,
            unit=obs.unit,
            method_version=obs.method_version,
            source_resolution_m=obs.source_resolution_m,
            valid_fraction=obs.valid_fraction,
            quality=obs.quality,
            class_code=obs.class_code,
            provider=obs.provider,
            dataset_id=obs.dataset_id,
        )
        for obs in observations
    }


def candidate_entity(entity: Candidate) -> CandidateEntity:
    return CandidateEntity(
        location_id=entity.location_id,
        display_name=entity.display_name,
        lat=entity.lat,
        lon=entity.lon,
        bbox=BBox(west=entity.west, south=entity.south, east=entity.east, north=entity.north),
        dataset_id=entity.dataset_id,
        window_size_m=entity.window_size_m,
        analysis_resolution_m=entity.analysis_resolution_m,
        crs=entity.crs,
        valid_fraction=entity.valid_fraction,
        quality_flags=tuple(entity.quality_flags or []),
        notes=entity.notes,
        data_kind=entity.data_kind,
        area_km2=entity.area_km2,
        geometry=entity.geometry,
        origin=entity.origin,
        features=feature_map(entity.features),
        context_observations=context_map(entity.context_observations),
    )


def reference_entity(entity: PlanetaryReference) -> ReferenceEntity:
    bbox = None
    if None not in (entity.west, entity.south, entity.east, entity.north):
        bbox = BBox(west=entity.west, south=entity.south, east=entity.east, north=entity.north)
    return ReferenceEntity(
        reference_id=entity.reference_id,
        body=entity.body,
        display_name=entity.display_name,
        dataset_id=entity.dataset_id,
        lat=entity.lat,
        lon=entity.lon,
        bbox=bbox,
        window_size_m=entity.window_size_m,
        analysis_resolution_m=entity.analysis_resolution_m,
        crs=entity.crs,
        valid_fraction=entity.valid_fraction,
        quality_flags=tuple(entity.quality_flags or []),
        notes=entity.notes,
        features=feature_map(entity.features),
    )
