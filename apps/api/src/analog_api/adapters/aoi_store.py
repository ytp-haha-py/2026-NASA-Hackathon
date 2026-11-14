from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.orm import Session

from analog_api.adapters.db import (
    Candidate,
    ContextObservation,
    Dataset,
    FeatureObservation,
    Meta,
)
from analog_api.domain.aoi import AoiResult

logger = logging.getLogger("analog_api.aoi_store")


def _dataset_row(dataset) -> Dataset:
    return Dataset(
        dataset_id=dataset.dataset_id,
        provider=dataset.provider,
        product_id=dataset.product_id,
        title=dataset.title,
        url=dataset.url,
        license=dataset.license,
        license_url=dataset.license_url,
        access_timestamp=dataset.access_timestamp,
        source_crs=dataset.source_crs,
        native_resolution_m=dataset.native_resolution_m,
        processing_version=dataset.processing_version,
        checksum=dataset.checksum,
        source_kind=dataset.source_kind,
        notes=dataset.notes,
    )


def _runtime_manifest_path(area_id: str) -> Path:
    root = Path(os.environ.get("ANALOG_AOI_RUNTIME_DIR", "var/aoi-catalog"))
    return root / f"{area_id}.json"


def persist_runtime_manifest(result: AoiResult, data_version: str) -> Path:
    """Write a durable runtime AOI manifest outside the rebuildable seed index.

    These manifests are not part of the Git-pinned baseline snapshot; they let an operator
    inspect/reprocess user-created AOIs after a SQLite rebuild.
    """
    path = _runtime_manifest_path(result.area_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "manifest_version": "1.0.0",
        "kind": "on_demand_aoi_runtime",
        "area_id": result.area_id,
        "generated_at": datetime.now(UTC).isoformat(),
        "data_snapshot_id": data_version,
        "bbox": result.bbox.as_dict(),
        "geometry": result.geometry,
        "area_km2": result.area_km2,
        "crs": result.crs,
        "valid_fraction": result.valid_fraction,
        "window_size_m": result.window_size_m,
        "resolution_m": result.resolution_m,
        "checksum": result.checksum,
        "features": result.features,
        "context_observations": result.context_observations,
        "datasets": [
            {
                "dataset_id": dataset.dataset_id,
                "provider": dataset.provider,
                "product_id": dataset.product_id,
                "url": dataset.url,
                "license": dataset.license,
                "license_url": dataset.license_url,
                "access_timestamp": dataset.access_timestamp,
                "processing_version": dataset.processing_version,
                "source_kind": dataset.source_kind,
            }
            for dataset in result.datasets
        ],
        "provider_status": result.provider_status,
        "warnings": list(result.warnings),
    }
    temp = path.with_suffix(".json.tmp")
    temp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temp.replace(path)
    return path


def persist_aoi_result(session: Session, result: AoiResult) -> str:
    """Upsert the AOI observation set and return the new data version.

    Idempotent per area_id: feature/context rows are replaced, not duplicated.
    """
    for dataset in result.datasets:
        existing = session.get(Dataset, dataset.dataset_id)
        if existing is None:
            session.add(_dataset_row(dataset))
        else:
            for key, value in _dataset_row(dataset).__dict__.items():
                if key != "_sa_instance_state":
                    setattr(existing, key, value)

    candidate = result.as_candidate()
    row = session.get(Candidate, candidate.location_id)
    if row is None:
        row = Candidate(
            location_id=candidate.location_id,
            display_name=candidate.display_name,
            lat=candidate.lat,
            lon=candidate.lon,
            west=candidate.bbox.west,
            south=candidate.bbox.south,
            east=candidate.bbox.east,
            north=candidate.bbox.north,
            dataset_id=candidate.dataset_id,
            window_size_m=candidate.window_size_m,
            analysis_resolution_m=candidate.analysis_resolution_m,
            crs=candidate.crs,
            valid_fraction=candidate.valid_fraction,
            quality_flags=list(candidate.quality_flags),
            notes=candidate.notes,
            data_kind=candidate.data_kind,
            area_km2=candidate.area_km2,
            geometry=candidate.geometry,
            origin=candidate.origin,
        )
        session.add(row)
    else:
        row.display_name = candidate.display_name
        row.lat = candidate.lat
        row.lon = candidate.lon
        row.west = candidate.bbox.west
        row.south = candidate.bbox.south
        row.east = candidate.bbox.east
        row.north = candidate.bbox.north
        row.dataset_id = candidate.dataset_id
        row.window_size_m = candidate.window_size_m
        row.analysis_resolution_m = candidate.analysis_resolution_m
        row.crs = candidate.crs
        row.valid_fraction = candidate.valid_fraction
        row.quality_flags = list(candidate.quality_flags)
        row.notes = candidate.notes
        row.area_km2 = candidate.area_km2
        row.geometry = candidate.geometry
        row.origin = candidate.origin

    session.query(FeatureObservation).filter(
        FeatureObservation.candidate_id == candidate.location_id
    ).delete(synchronize_session=False)
    session.query(ContextObservation).filter(
        ContextObservation.candidate_id == candidate.location_id
    ).delete(synchronize_session=False)
    for feature_id, observation in candidate.features.items():
        session.add(
            FeatureObservation(
                candidate_id=candidate.location_id,
                feature_id=feature_id,
                value=observation.value,
                unit=observation.unit,
                method_version=observation.method_version,
                extraction_scale_m=observation.extraction_scale_m,
                valid_fraction=observation.valid_fraction,
                quality=observation.quality,
            )
        )
    for feature_id, observation in candidate.context_observations.items():
        session.add(
            ContextObservation(
                candidate_id=candidate.location_id,
                dataset_id=observation.dataset_id,
                feature_id=feature_id,
                value=observation.value,
                unit=observation.unit,
                method_version=observation.method_version,
                source_resolution_m=observation.source_resolution_m,
                valid_fraction=observation.valid_fraction,
                quality=observation.quality,
                class_code=observation.class_code,
                provider=observation.provider,
            )
        )

    previous = session.get(Meta, "data_version")
    payload = {
        "area_id": candidate.location_id,
        "features": {key: observation.value for key, observation in candidate.features.items()},
        "context": {
            key: observation.value for key, observation in candidate.context_observations.items()
        },
    }
    digest = hashlib.sha256(
        (
            (previous.value if previous else "dv-empty") + json.dumps(payload, sort_keys=True)
        ).encode()
    ).hexdigest()[:12]
    data_version = f"dv-{digest}"
    session.merge(Meta(key="data_version", value=data_version))
    session.commit()
    try:
        persist_runtime_manifest(result, data_version)
    except OSError as exc:
        # The runtime catalog is auxiliary; a read-only container mount must not lose the
        # already-persisted index result. The warning is visible in server logs.
        logger.warning("Could not write runtime AOI manifest: %s", exc)
    return data_version


def get_aoi(session: Session, area_id: str) -> Candidate | None:
    candidate = session.get(Candidate, area_id)
    if candidate is not None and candidate.origin != "on_demand":
        return None
    return candidate


__all__ = ["persist_aoi_result", "persist_runtime_manifest", "get_aoi"]
