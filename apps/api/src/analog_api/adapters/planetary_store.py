"""Persistence for on-demand Moon/Mars AOI analysis (Issue #132).

Planetary areas are stored in the additive typed-v2 tables. They never enter the
Earth candidate index, the Earth similarity distance, or a cross-body score. The
legacy ``analysis_jobs`` table records the job lifecycle; this module persists the
area, its D01 observations and the source assets used to compute them.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from analog_pipelines.coarse_features import COARSE_FEATURE_BY_ID
from analog_pipelines.typed_contracts import (
    AreaV2,
    DepthSupport,
    SourceAssetV2,
    SpatialSupport,
    TemporalSupport,
    TypedObservation,
)
from sqlalchemy.orm import Session

from analog_api.adapters.db import Meta
from analog_api.adapters.typed_db import TypedObservationStore
from analog_api.domain.aoi import AoiResult
from analog_api.domain.features import FEATURES

logger = logging.getLogger("analog_api.planetary_store")

PLANETARY_METHOD_VERSION = "coarse-planetary-dem-horn-1.0.0"
PLANETARY_DIMENSION = "D01"
FINE_D01_BY_ID = {feature.id: feature for feature in FEATURES}

_BODY_RADIUS_M = {"Moon": 1_737_400.0, "Mars": 3_396_190.0}
_BODY_FRAME = {
    "Moon": ("lunar_planetocentric", "IAU_MOON"),
    "Mars": ("mars_planetocentric", "IAU_MARS"),
}


def _planetary_crs(body: str) -> str:
    radius = _BODY_RADIUS_M.get(body)
    if radius is None:
        raise ValueError(f"unsupported planetary body {body!r}")
    return f"+proj=longlat +R={radius:.1f} +no_defs"


def _runtime_manifest_path(area_id: str) -> Path:
    root = Path(os.environ.get("ANALOG_AOI_RUNTIME_DIR", "var/aoi-catalog"))
    return root / f"{area_id}.json"


def _write_runtime_manifest(result: AoiResult, data_version: str) -> None:
    path = _runtime_manifest_path(result.area_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "manifest_version": "1.0.0",
        "kind": "on_demand_planetary_aoi_runtime",
        "area_id": result.area_id,
        "body": result.body,
        "generated_at": datetime.now(UTC).isoformat(),
        "data_snapshot_id": data_version,
        "bbox": result.bbox.as_dict(),
        "geometry": result.geometry,
        "area_km2": result.area_km2,
        "crs": result.crs,
        "coordinate_frame": result.coordinate_frame,
        "method_version": result.method_version,
        "valid_fraction": result.valid_fraction,
        "window_size_m": result.window_size_m,
        "resolution_m": result.resolution_m,
        "checksum": result.checksum,
        "features": result.features,
        "dimension_support": result.dimension_support,
        "coverage_note": result.coverage_note,
        "analysis_kind": result.analysis_kind,
        "provider_status": result.provider_status,
        "source_assets": list(result.source_assets),
        "warnings": list(result.warnings),
    }
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def _source_asset_payload(result: AoiResult, body: str) -> dict[str, Any]:
    dataset = result.datasets[0]
    asset = result.source_assets[0] if result.source_assets else {}
    uri = str(asset.get("uri") or dataset.url)
    checksum = asset.get("sha256") or asset.get("checksum")
    asset_id = (
        "planetary-aoi-asset-"
        + hashlib.sha256(f"{result.area_id}|{uri}|{checksum or ''}".encode()).hexdigest()[:16]
    )
    return {
        "asset_id": asset_id,
        "dataset_id": dataset.dataset_id,
        "uri": uri,
        "checksum_algorithm": "sha256" if checksum else "unknown",
        "checksum": str(checksum) if checksum else None,
        "checksum_scope": "derived" if checksum else "unknown",
        "native_crs": dataset.source_crs,
        "native_units": "metre",
        "scale": None,
        "offset": None,
        "nodata": None,
        "bytes": asset.get("bytes"),
        "fetched_at": dataset.access_timestamp,
        "redistribution_allowed": "unknown",
    }


def persist_planetary_aoi_result(session: Session, result: AoiResult) -> str:
    """Upsert a planetary AOI and its D01 observations; return a new data version."""
    body = result.body
    if body not in _BODY_RADIUS_M:
        raise ValueError(f"planetary persistence only supports Moon/Mars, got {body!r}")
    frame, _ = _BODY_FRAME[body]
    store = TypedObservationStore(session)
    dataset = result.datasets[0]
    source_asset = _source_asset_payload(result, body)
    store.upsert_asset(SourceAssetV2(**source_asset))

    bbox = result.bbox.as_dict()
    if bbox["east"] <= bbox["west"] or bbox["north"] <= bbox["south"]:
        raise ValueError("planetary AOI persistence requires a non-wrapping east>west bbox")
    area = AreaV2(
        area_id=result.area_id,
        body=body,  # type: ignore[arg-type]
        geometry=result.geometry,
        bbox=bbox,
        crs=result.crs or _planetary_crs(body),
        geometry_crs=result.crs or _planetary_crs(body),
        analysis_crs=result.crs or _planetary_crs(body),
        coordinate_frame=frame,  # type: ignore[arg-type]
        area_km2=result.area_km2,
        source_area_refs=[],
        legacy_provenance={
            "source": "on_demand_planetary_aoi",
            "method_version": result.method_version or PLANETARY_METHOD_VERSION,
            "analysis_kind": result.analysis_kind,
            "coverage_note": result.coverage_note,
        },
        notes=(
            "User-requested on-demand planetary AOI; D01 coarse metrics recomputed from "
            "the verified source fixture. Not a cross-body similarity score."
        ),
    )
    store.upsert_area(area)

    crs = area.crs
    for feature_id, value in result.features.items():
        definition = COARSE_FEATURE_BY_ID.get(feature_id)
        fine_definition = FINE_D01_BY_ID.get(feature_id) if definition is None else None
        if definition is None and fine_definition is None:
            raise ValueError(f"unsupported planetary AOI feature {feature_id!r}")
        stored_feature_id = feature_id if definition is not None else f"planetary_{feature_id}"
        unit = definition.unit if definition is not None else fine_definition.unit
        method_version = (
            definition.method_version
            if definition is not None
            else (result.method_version or fine_definition.method_version)
        )
        quality_flags = (
            [
                "on_demand_planetary_aoi",
                "d01_coarse_250m_support",
                "not_earth_wgs84",
                "not_a_cross_body_score",
            ]
            if definition is not None
            else [
                "on_demand_planetary_aoi",
                "global_dem_aoi",
                "not_earth_wgs84",
                "not_a_cross_body_score",
                f"resolution_class:{'upsampled_from_coarse_source' if (dataset.native_resolution_m or 0) > result.resolution_m else 'native_or_downsampled'}",
            ]
        )
        observation = TypedObservation(
            observation_id=f"{result.area_id}:{stored_feature_id}",
            area_id=result.area_id,
            body=body,  # type: ignore[arg-type]
            dimension_code=PLANETARY_DIMENSION,  # type: ignore[arg-type]
            feature_id=stored_feature_id,
            value_kind="numeric",
            state="detected",
            value=float(value),
            unit=unit,
            spatial_support=SpatialSupport(
                kind="window",
                crs=crs,
                resolution_m=result.resolution_m,
                window_size_m=result.window_size_m,
                bbox=bbox,
                bbox_crs="planetocentric_lon_lat_degrees",
                area_km2=result.area_km2,
                source_resolution_m=dataset.native_resolution_m,
                native_resolution_m=dataset.native_resolution_m,
                valid_fraction=result.valid_fraction,
                notes=(
                    "Bounded planetary AOI window recomputed from the verified source; "
                    "support is the requested window, not an Earth WGS84 or cross-body score."
                ),
            ),
            temporal_support=TemporalSupport(
                kind="static",
                notes="Static planetary topography source release.",
            ),
            depth_support=DepthSupport(kind="surface"),
            method_version=method_version,
            dataset_ids=[dataset.dataset_id],
            source_asset_ids=[source_asset["asset_id"]],
            quality_flags=quality_flags,
            score_role="evidence_only",
            notes=(
                "Recomputed from a verified planetary DEM source; evidence for D01 terrain "
                "context, not an Earth-analog similarity value."
            ),
        )
        store.upsert_observation(observation)

    previous = session.get(Meta, "data_version")
    payload = {
        "area_id": result.area_id,
        "body": body,
        "features": {key: float(value) for key, value in result.features.items()},
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
        _write_runtime_manifest(result, data_version)
    except OSError as exc:
        logger.warning("Could not write planetary runtime AOI manifest: %s", exc)
    return data_version


__all__ = ["persist_planetary_aoi_result"]
