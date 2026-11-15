"""Export the independent Geology pilot into evidence-only typed-v2 records.

No map unit, process or mineral is converted into a Terrain similarity feature.
Mars geographic AOIs are transformed into a projected Mars analysis CRS; the
original geometry/CRS is retained in legacy provenance.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pyproj import CRS, Transformer

from analog_pipelines.geology.models import Manifest
from analog_pipelines.typed_contracts import (
    AreaV2,
    DatasetV2,
    DepthSupport,
    ProcessEvidence,
    SourceAssetV2,
    SpatialSupport,
    TemporalSupport,
    TypedObservation,
    Uncertainty,
)

Body = Literal["Earth", "Moon", "Mars"]


def _iter_positions(geometry: dict[str, Any]):
    coordinates = geometry.get("coordinates", [])

    def walk(value: Any):
        if (
            isinstance(value, (list, tuple))
            and len(value) == 2
            and all(isinstance(v, (int, float)) for v in value)
        ):
            yield float(value[0]), float(value[1])
        elif isinstance(value, (list, tuple)):
            for item in value:
                yield from walk(item)

    yield from walk(coordinates)


def _bbox(geometry: dict[str, Any]) -> dict[str, float]:
    positions = list(_iter_positions(geometry))
    if not positions:
        raise ValueError("geometry has no positions")
    xs = [x for x, _ in positions]
    ys = [y for _, y in positions]
    return {"west": min(xs), "south": min(ys), "east": max(xs), "north": max(ys)}


def _transform_geometry(geometry: dict[str, Any], src_crs: str, dst_crs: str) -> dict[str, Any]:
    transformer = Transformer.from_crs(src_crs, dst_crs, always_xy=True)
    coordinates = geometry.get("coordinates", [])

    def walk(value: Any):
        if (
            isinstance(value, (list, tuple))
            and len(value) == 2
            and all(isinstance(v, (int, float)) for v in value)
        ):
            x, y = transformer.transform(float(value[0]), float(value[1]))
            return [x, y]
        if isinstance(value, (list, tuple)):
            return [walk(item) for item in value]
        return value

    return {"type": geometry["type"], "coordinates": walk(coordinates)}


def _analysis_crs_for_area(area, analyses) -> str | None:
    if area.body == "Earth":
        return area.crs
    own = CRS.from_user_input(area.crs)
    if own.is_projected:
        return area.crs
    for analysis in analyses:
        if analysis.area_id != area.area_id or not analysis.analysis_crs:
            continue
        candidate = CRS.from_user_input(analysis.analysis_crs)
        if candidate.is_projected:
            return analysis.analysis_crs
    return None


def _frame(body: Body, crs: str) -> str:
    parsed = CRS.from_user_input(crs)
    if body == "Earth":
        return "earth_wgs84" if parsed.is_geographic else "earth_projected"
    if body == "Moon":
        return "lunar_projected"
    return "mars_projected"


def _slug(value: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in value.lower()).strip("_")


def _state(value: str) -> str:
    return {
        "not_detected_under_valid_observation": "not_detected",
    }.get(value, value)


def _asset_id(dataset_id: str, checksum: str) -> str:
    digest = checksum.removeprefix("sha256:")
    return f"geology-asset:{dataset_id}:{digest[:16]}"


def _spatial_support(area: AreaV2, analysis, bbox: dict[str, float]) -> SpatialSupport:
    return SpatialSupport(
        kind="geometry",
        crs=area.crs,
        bbox=bbox,
        bbox_crs=area.crs,
        native_resolution_m=analysis.native_resolution_m,
        valid_fraction=analysis.valid_fraction,
        notes=(
            f"Geology overlay on the Area geometry; map scale {analysis.map_scale or 'unknown'}; "
            "not a 1.98 km terrain metric."
        ),
    )


def _base_observation(
    *,
    area: AreaV2,
    analysis,
    feature_id: str,
    observation_key: str,
    value_kind: str,
    state: str,
    value: Any,
    unit: str | None,
    method_version: str,
    dataset_ids: list[str],
    asset_ids: list[str],
    bbox: dict[str, float],
    quality_flags: list[str],
    notes: str,
    evidence: ProcessEvidence | None = None,
    uncertainty: Uncertainty | None = None,
) -> TypedObservation:
    return TypedObservation(
        observation_id=(
            f"geology:{analysis.area_id}:{analysis.dataset_id}:{feature_id}:{observation_key}"
        ),
        area_id=area.area_id,
        body=area.body,
        dimension_code="D02",
        feature_id=feature_id,
        value_kind=value_kind,
        state=state,
        value=value,
        unit=unit,
        uncertainty=uncertainty,
        spatial_support=_spatial_support(area, analysis, bbox),
        temporal_support=TemporalSupport(kind="unknown"),
        depth_support=DepthSupport(kind="surface"),
        method_version=method_version,
        dataset_ids=dataset_ids,
        source_asset_ids=asset_ids,
        quality_flags=quality_flags,
        score_role="evidence_only",
        evidence=evidence,
        notes=notes,
    )


def export_geology_typed(record_path: str | Path) -> dict[str, Any]:
    manifest = Manifest.model_validate_json(Path(record_path).read_text(encoding="utf-8"))
    analyses_by_area: dict[str, list] = {}
    for analysis in manifest.analyses:
        analyses_by_area.setdefault(analysis.area_id, []).append(analysis)

    warnings: list[str] = []
    areas: list[AreaV2] = []
    area_by_id: dict[str, AreaV2] = {}
    for area in manifest.areas:
        target_crs = _analysis_crs_for_area(area, analyses_by_area.get(area.area_id, []))
        if target_crs is None:
            warnings.append(
                f"{area.area_id}: no projected planetary analysis CRS; area omitted rather than "
                "relabelling geographic coordinates"
            )
            continue
        geometry = area.geometry
        if area.crs != target_crs:
            geometry = _transform_geometry(area.geometry, area.crs, target_crs)
            warnings.append(
                f"{area.area_id}: geometry transformed {area.crs} -> {target_crs}; original "
                "geometry/CRS retained in legacy_provenance"
            )
        bbox = _bbox(geometry)
        typed_area = AreaV2(
            area_id=area.area_id,
            body=area.body,
            geometry=geometry,
            bbox=bbox,
            crs=target_crs,
            geometry_crs=target_crs,
            analysis_crs=target_crs,
            coordinate_frame=_frame(area.body, target_crs),  # type: ignore[arg-type]
            legacy_provenance={
                "schema": "geology-manifest-1.0.0",
                "geometry_fingerprint": area.geometry_fingerprint,
                "original_crs": area.crs,
                "original_geometry": area.geometry,
                "geometry_source": area.geometry_source,
                "origin": area.origin,
                "limitations": area.limitations,
            },
            notes=area.display_name,
        )
        areas.append(typed_area)
        area_by_id[area.area_id] = typed_area

    datasets: list[DatasetV2] = []
    assets: list[SourceAssetV2] = []
    asset_ids_by_dataset: dict[str, list[str]] = {}
    for dataset in manifest.datasets:
        permitted = dataset.redistribution == "permitted"
        datasets.append(
            DatasetV2(
                dataset_id=dataset.dataset_id,
                body=dataset.body,
                provider=dataset.provider,
                product_id=dataset.product,
                version=dataset.version,
                url=dataset.source_url,
                license=dataset.license,
                license_url=dataset.license_url,
                verification_status="verified" if permitted else "identified",
                native_crs=dataset.source_crs,
                native_resolution_m=dataset.native_resolution_m,
                temporal_support="unknown",
                depth_support="surface",
                redistribution_allowed="yes" if permitted else "unknown",
            )
        )
        for analysis in manifest.analyses:
            if analysis.dataset_id != dataset.dataset_id:
                continue
            for artifact in analysis.artifacts:
                asset_id = _asset_id(dataset.dataset_id, artifact.checksum)
                if any(item.asset_id == asset_id for item in assets):
                    continue
                assets.append(
                    SourceAssetV2(
                        asset_id=asset_id,
                        dataset_id=dataset.dataset_id,
                        uri=artifact.source_url,
                        checksum_algorithm="sha256",
                        checksum=artifact.checksum.removeprefix("sha256:"),
                        checksum_scope="source",
                        native_crs=dataset.source_crs,
                        bytes=artifact.size_bytes,
                        redistribution_allowed="yes" if permitted else "unknown",
                    )
                )
                asset_ids_by_dataset.setdefault(dataset.dataset_id, []).append(asset_id)

    observations: list[TypedObservation] = []
    for analysis in manifest.analyses:
        area = area_by_id.get(analysis.area_id)
        if area is None:
            continue
        asset_ids = asset_ids_by_dataset.get(analysis.dataset_id, [])
        if not asset_ids:
            warnings.append(f"{analysis.area_id}/{analysis.dataset_id}: no source artifact")
            continue
        bbox = area.bbox or {}
        quality = list(analysis.quality_flags)
        method = analysis.processing_method
        dataset_ids = [analysis.dataset_id]
        if analysis.status in {"no_coverage", "auth_required", "failed", "unverified"}:
            observations.append(
                _base_observation(
                    area=area,
                    analysis=analysis,
                    feature_id="geology_analysis_availability",
                    observation_key="availability",
                    value_kind="category",
                    state="no_coverage" if analysis.status == "no_coverage" else "unknown",
                    value=None,
                    unit=None,
                    method_version=method,
                    dataset_ids=dataset_ids,
                    asset_ids=asset_ids,
                    bbox=bbox,
                    quality_flags=quality,
                    notes=analysis.reason or f"provider status {analysis.status}",
                )
            )
            continue
        for lithology in analysis.lithology:
            observations.append(
                _base_observation(
                    area=area,
                    analysis=analysis,
                    feature_id="geologic_unit",
                    observation_key=lithology.evidence_id,
                    value_kind="category",
                    state="detected",
                    value={
                        "code": lithology.unit_id,
                        "scheme": "geology-map-unit",
                        "name": lithology.unit_name,
                        "description": lithology.official_description,
                        "unified_rocks": lithology.unified_rocks,
                        "overlap_fraction": lithology.overlap_fraction,
                        "source_feature_id": lithology.source_feature_id,
                        "original_attributes": lithology.original_attributes,
                    },
                    unit=None,
                    method_version=method,
                    dataset_ids=dataset_ids,
                    asset_ids=asset_ids,
                    bbox=bbox,
                    quality_flags=quality,
                    notes=lithology.interpretation,
                    evidence=ProcessEvidence(
                        process_code="lithology",
                        evidence_type="mapped_geology",
                        description=lithology.unit_name,
                    ),
                )
            )
        for process in analysis.geological_processes:
            observations.append(
                _base_observation(
                    area=area,
                    analysis=analysis,
                    feature_id="geologic_process",
                    observation_key=process.evidence_ids[0],
                    value_kind="process_evidence",
                    state="detected",
                    value={
                        "process_code": process.category,
                        "evidence_type": process.basis,
                        "description": process.supporting_statement,
                        "references": process.evidence_ids,
                    },
                    unit=None,
                    method_version=method,
                    dataset_ids=dataset_ids,
                    asset_ids=asset_ids,
                    bbox=bbox,
                    quality_flags=quality,
                    notes=f"{process.process} ({process.confidence})",
                )
            )
        for mineral in analysis.mineralogy:
            feature_id = f"mineral_{_slug(mineral.mineral)}"
            state = _state(mineral.state)
            if state == "detected" and mineral.value is not None and mineral.unit:
                value_kind = "numeric"
                value: Any = mineral.value
                unit = mineral.unit
            elif state == "detected":
                value_kind = "detection"
                value = {
                    "indicator": 1,
                    "mineral": mineral.mineral,
                    "measurement": mineral.measurement,
                    "semantics_id": mineral.semantics_id,
                }
                unit = None
            else:
                value_kind = "detection"
                value = None
                unit = mineral.unit
            uncertainty = None
            if mineral.uncertainty is not None and mineral.uncertainty_unit:
                uncertainty = Uncertainty(
                    kind="standard_deviation",
                    value=mineral.uncertainty,
                    unit=mineral.uncertainty_unit,
                )
            observations.append(
                _base_observation(
                    area=area,
                    analysis=analysis,
                    feature_id=feature_id,
                    observation_key=mineral.evidence_id,
                    value_kind=value_kind,
                    state=state,
                    value=value,
                    unit=unit,
                    method_version=method,
                    dataset_ids=dataset_ids,
                    asset_ids=asset_ids,
                    bbox=bbox,
                    quality_flags=[mineral.quality, *quality],
                    notes=mineral.method,
                    uncertainty=uncertainty,
                )
            )
        for context in analysis.geological_context:
            observations.append(
                _base_observation(
                    area=area,
                    analysis=analysis,
                    feature_id=f"context_{_slug(context.name)}",
                    observation_key=context.evidence_id,
                    value_kind="numeric",
                    state="detected" if context.value is not None else "unknown",
                    value=context.value,
                    unit=context.unit,
                    method_version=method,
                    dataset_ids=dataset_ids,
                    asset_ids=asset_ids,
                    bbox=bbox,
                    quality_flags=["context_only", *quality],
                    notes=f"{context.meaning}. {context.method}",
                )
            )
    return {
        "areas": areas,
        "datasets": datasets,
        "assets": assets,
        "observations": observations,
        "warnings": warnings,
        "source_record": str(record_path),
        "generated_at": datetime.now(UTC).isoformat(),
    }


def write_geology_typed_export(record_path: str | Path, output_dir: str | Path) -> dict[str, Any]:
    export = export_geology_typed(record_path)
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    payloads = {
        "areas.json": [item.model_dump(mode="json") for item in export["areas"]],
        "datasets.json": [item.model_dump(mode="json") for item in export["datasets"]],
        "assets.json": [item.model_dump(mode="json") for item in export["assets"]],
    }
    for name, payload in payloads.items():
        (target / name).write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    observations_path = target / "observations.jsonl"
    observations_path.write_text(
        "".join(
            json.dumps(observation.model_dump(mode="json"), sort_keys=True) + "\n"
            for observation in export["observations"]
        ),
        encoding="utf-8",
    )
    manifest = {
        "export_version": "geology-typed-export-1.0.0",
        "generated_at": export["generated_at"],
        "source_record": export["source_record"],
        "areas": len(export["areas"]),
        "datasets": len(export["datasets"]),
        "assets": len(export["assets"]),
        "observations": len(export["observations"]),
        "warnings": export["warnings"],
        "observations_sha256": hashlib.sha256(observations_path.read_bytes()).hexdigest(),
        "scope_note": (
            "D02 evidence-only export. Map units, processes and mineral states are not Terrain "
            "similarity features; null/unknown states are preserved."
        ),
    }
    (target / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest
