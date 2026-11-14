from __future__ import annotations

import hashlib
import json
from pathlib import Path

from sqlalchemy import create_engine, delete, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from analog_api.adapters.db import (
    Base,
    Candidate,
    ContextObservation,
    Dataset,
    FeatureObservation,
    Meta,
    PlanetaryReference,
    ReferenceFeatureObservation,
    utcnow,
)
from analog_api.config import Settings, resolve_repo_path
from analog_api.data.synthetic import synthetic_manifest
from analog_api.domain.manifest import Manifest

DATA_VERSION_KEY = "data_version"


class ManifestError(RuntimeError):
    pass


def load_manifest_file(path: Path) -> Manifest:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestError(f"cannot read manifest {path}: {exc}") from exc
    try:
        return Manifest.model_validate(raw)
    except ValueError as exc:
        raise ManifestError(f"invalid manifest {path}: {exc}") from exc


def manifest_paths(settings: Settings) -> list[Path]:
    paths: list[Path] = []
    for directory in (
        settings.manifest_dir,
        settings.manifest_dir_context,
        settings.manifest_dir_planetary,
    ):
        resolved = resolve_repo_path(directory)
        if resolved.is_dir():
            paths.extend(sorted(resolved.glob("*.json")))
    return paths


def discover_manifests(settings: Settings) -> list[Manifest]:
    manifests = [load_manifest_file(path) for path in manifest_paths(settings)]
    if settings.seed_include_synthetic:
        manifests.append(Manifest.model_validate(synthetic_manifest()))
    return manifests


def compute_data_version(manifests: list[Manifest]) -> str:
    total_candidates = sum(len(m.candidates) for m in manifests)
    if total_candidates == 0:
        return "dv-empty"
    canonical = json.dumps(
        [m.model_dump(mode="json") for m in sorted(manifests, key=lambda m: m.generated_by)],
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]
    return f"dv-{digest}"


def init_db(engine: Engine) -> None:
    Base.metadata.create_all(engine)


def seed_manifests(engine: Engine, manifests: list[Manifest], *, reset: bool = False) -> str:
    """Persist manifests idempotently. `reset=True` replaces the whole index."""
    if reset:
        Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)

    data_version = compute_data_version(manifests)
    with Session(engine) as session:
        if reset:
            session.execute(delete(ReferenceFeatureObservation))
            session.execute(delete(FeatureObservation))
            session.execute(delete(PlanetaryReference))
            session.execute(delete(Candidate))
            session.execute(delete(Dataset))
            session.execute(delete(Meta))
            session.commit()

        existing_datasets = set(session.scalars(select(Dataset.dataset_id)).all())
        existing_candidates = set(session.scalars(select(Candidate.location_id)).all())
        existing_references = set(session.scalars(select(PlanetaryReference.reference_id)).all())

        for manifest in manifests:
            landcover_dataset_id = next(
                (
                    dataset.dataset_id
                    for dataset in manifest.datasets
                    if dataset.source_kind == "landcover"
                ),
                None,
            )
            for dataset in manifest.datasets:
                if dataset.dataset_id in existing_datasets:
                    continue
                session.add(
                    Dataset(
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
                )
                existing_datasets.add(dataset.dataset_id)

            for candidate in manifest.candidates:
                if candidate.location_id in existing_candidates:
                    continue
                session.add(
                    Candidate(
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
                        quality_flags=candidate.quality_flags,
                        notes=candidate.notes,
                        data_kind=manifest.data_kind,
                        area_km2=candidate.area_km2,
                        geometry=candidate.geometry,
                        origin=candidate.origin,
                        features=[
                            FeatureObservation(
                                feature_id=feature_id,
                                value=observation.value,
                                unit=observation.unit,
                                method_version=observation.method_version,
                                extraction_scale_m=observation.extraction_scale_m,
                                valid_fraction=observation.valid_fraction,
                                quality=observation.quality,
                            )
                            for feature_id, observation in candidate.features.items()
                        ],
                        context_observations=[
                            ContextObservation(
                                dataset_id=observation.dataset_id or landcover_dataset_id,
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
                            for feature_id, observation in candidate.context_observations.items()
                        ],
                    )
                )
                existing_candidates.add(candidate.location_id)

            if existing_candidates and manifest.data_kind == "observed":
                for candidate in manifest.candidates:
                    if not candidate.context_observations:
                        continue
                    row = session.get(Candidate, candidate.location_id)
                    if row is None:
                        continue
                    if row.area_km2 is None and candidate.area_km2 is not None:
                        row.area_km2 = candidate.area_km2
                    if row.geometry is None and candidate.geometry is not None:
                        row.geometry = candidate.geometry
                    session.query(ContextObservation).filter(
                        ContextObservation.candidate_id == candidate.location_id,
                        ContextObservation.feature_id.in_(list(candidate.context_observations)),
                    ).delete(synchronize_session=False)
                    for feature_id, observation in candidate.context_observations.items():
                        session.add(
                            ContextObservation(
                                candidate_id=candidate.location_id,
                                dataset_id=observation.dataset_id or landcover_dataset_id,
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

            for reference in manifest.references:
                if reference.reference_id in existing_references:
                    continue
                session.add(
                    PlanetaryReference(
                        reference_id=reference.reference_id,
                        body=reference.body,
                        display_name=reference.display_name,
                        dataset_id=reference.dataset_id,
                        lat=reference.lat,
                        lon=reference.lon,
                        west=reference.bbox.west if reference.bbox else None,
                        south=reference.bbox.south if reference.bbox else None,
                        east=reference.bbox.east if reference.bbox else None,
                        north=reference.bbox.north if reference.bbox else None,
                        window_size_m=reference.window_size_m,
                        analysis_resolution_m=reference.analysis_resolution_m,
                        crs=reference.crs,
                        valid_fraction=reference.valid_fraction,
                        quality_flags=reference.quality_flags,
                        notes=reference.notes,
                        features=[
                            ReferenceFeatureObservation(
                                feature_id=feature_id,
                                value=observation.value,
                                unit=observation.unit,
                                method_version=observation.method_version,
                                extraction_scale_m=observation.extraction_scale_m,
                                valid_fraction=observation.valid_fraction,
                                quality=observation.quality,
                            )
                            for feature_id, observation in reference.features.items()
                        ],
                    )
                )
                existing_references.add(reference.reference_id)

        session.merge(Meta(key=DATA_VERSION_KEY, value=data_version))
        session.merge(Meta(key="seeded_at", value=utcnow().isoformat()))
        session.commit()
    return data_version


def ensure_seeded(engine: Engine, settings: Settings) -> None:
    init_db(engine)
    with Session(engine) as session:
        count = session.query(Candidate).count()
    if count == 0:
        seed_manifests(engine, discover_manifests(settings), reset=False)


def make_engine(database_url: str) -> Engine:
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    return create_engine(database_url, connect_args=connect_args, future=True)
