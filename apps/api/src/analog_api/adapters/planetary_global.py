"""Default global Moon/Mars D01 provider (Issue #170).

The API's original planetary provider only analysed committed coarse pilot cells.
This adapter wires the merged #170 global DEM engines into the default request path:

* Moon: NASA PGDA LOLA LDEM128 (bounded ``/vsicurl`` window; optional polar COG).
* Mars: NASA PDS MGS MOLA MEGDR 128 ppd (bounded tile/range reads).

Both engines are loaded lazily from ``pipelines/planetary`` and return *real*
bounded observations. D02-D06 are explicitly unsupported for on-demand analysis;
nothing is fabricated or zero-filled. Network use is opt-in via
``ANALOG_PLANETARY_ALLOW_NETWORK``.
"""

from __future__ import annotations

import importlib.util
import math
import sys
import threading
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from analog_api.config import resolve_repo_path
from analog_api.domain.aoi import AoiResult, ValidatedAoi
from analog_api.domain.entities import BBox, DatasetEntity
from analog_api.domain.errors import AppError

SUPPORTED_BODIES = ("Moon", "Mars")
GLOBAL_D01_FEATURES = (
    "slope_mean_deg",
    "slope_p90_deg",
    "terrain_relief_p95_p05_m",
    "roughness_std_m",
    "flat_terrain_fraction",
)
_METHOD_VERSIONS = {
    "Moon": "lola-global-d01-aoi-1.0.0",
    "Mars": "mars-mola-aoi-horn-463m-1.0.0",
}
_FRAMES = {
    "Moon": ("lunar_planetocentric", "+proj=longlat +R=1737400.0 +no_defs"),
    "Mars": ("mars_planetocentric", "+proj=longlat +R=3396190.0 +no_defs"),
}

_LOCK = threading.Lock()
_MODULES: dict[str, Any] = {}
_MODULE_ROOT: Path | None = None


def _module(name: str) -> Any:
    """Load a ``pipelines/planetary`` engine once, with its sibling modules importable."""
    global _MODULE_ROOT
    with _LOCK:
        if name in _MODULES:
            return _MODULES[name]
        root = _MODULE_ROOT or resolve_repo_path("pipelines/planetary")
        _MODULE_ROOT = root
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        path = root / f"{name}.py"
        if not path.is_file():
            raise AppError(
                "planetary_engine_missing",
                f"Global planetary engine {name} is not installed in this deployment.",
                status_code=503,
            )
        spec = importlib.util.spec_from_file_location(name, path)
        if spec is None or spec.loader is None:
            raise AppError(
                "planetary_engine_unavailable",
                f"Could not load global planetary engine {name}.",
                status_code=503,
            )
        module = importlib.util.module_from_spec(spec)
        # Register before executing so dataclasses can resolve their module.
        sys.modules[name] = module
        spec.loader.exec_module(module)
        _MODULES[name] = module
        return module


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _feature_values(payload: Mapping[str, Any]) -> dict[str, float]:
    """Normalise LOLA flat values and MOLA ``{"value": ...}`` records."""
    raw = payload.get("features") or {}
    if not isinstance(raw, Mapping):
        return {}
    features: dict[str, float] = {}
    for feature_id, value in raw.items():
        candidate = value.get("value") if isinstance(value, Mapping) else value
        number = _finite(candidate)
        if number is not None:
            features[str(feature_id)] = number
    return features


def _crs_string(body: str, payload: Mapping[str, Any]) -> str:
    analysis = payload.get("analysis_crs")
    if isinstance(analysis, str) and analysis.startswith(("+proj", "PROJCRS", "GEOGCRS", "EPSG:")):
        return analysis
    crs = payload.get("crs")
    if isinstance(crs, Mapping):
        for key in ("analysis", "wkt", "proj", "name"):
            value = crs.get(key)
            if isinstance(value, Mapping):
                for inner in ("wkt", "proj", "name"):
                    candidate = value.get(inner)
                    if isinstance(candidate, str) and candidate.strip():
                        return candidate
            if isinstance(value, str) and value.strip():
                return value
    return _FRAMES[body][1]


def _dataset(body: str, source: Mapping[str, Any]) -> DatasetEntity:
    now = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    if body == "Moon":
        dataset_id = str(
            source.get("source_id") or source.get("dataset_id") or "moon-lola128-pa-pixel-202405"
        )
        return DatasetEntity(
            dataset_id=dataset_id,
            provider=str(source.get("provider") or "NASA GSFC PGDA / LRO-LOLA"),
            product_id=str(source.get("product_id") or dataset_id),
            title=source.get("title") or "LOLA MOON_PA global shape model, 128 ppd",
            url=str(
                source.get("url")
                or "https://pgda.gsfc.nasa.gov/data/LOLA_PA/LDEM128_PA_pixel_202405.tif"
            ),
            license=str(
                source.get("license")
                or "not stated on the PGDA product page; public NASA archive, citation requested"
            ),
            license_url=str(source.get("license_url") or "https://pgda.gsfc.nasa.gov/products/95"),
            access_timestamp=now,
            source_crs=source.get("crs_proj4"),
            native_resolution_m=_finite(source.get("native_resolution_m")),
            processing_version=str(source.get("version") or "202405"),
            checksum=source.get("checksum"),
            source_kind="planetary_dem",
            notes=source.get("notes"),
        )
    dataset_id = str(source.get("dataset_id") or "pds-mgs-mola-megdr-meg128")
    return DatasetEntity(
        dataset_id=dataset_id,
        provider=str(source.get("provider") or "NASA PDS Geosciences / MGS MOLA"),
        product_id=str(source.get("product_id") or "MOLA MEGDR 128 ppd"),
        title=source.get("title") or "MGS MOLA MEGDR 128 ppd topography",
        url=str(source.get("url") or "https://pds-geosciences.wustl.edu/missions/mgs/megdr.html"),
        license=str(source.get("license") or "not stated in the PDS4 bundle labels"),
        license_url=str(
            source.get("license_url") or "https://pds-geosciences.wustl.edu/missions/mgs/megdr.html"
        ),
        access_timestamp=now,
        source_crs=source.get("source_crs") or "IAU2000_MARS planetocentric",
        native_resolution_m=_finite(source.get("native_resolution_m")) or 463.0576672,
        processing_version=str(source.get("version") or "PDS4 1.1"),
        checksum=source.get("checksum"),
        source_kind="planetary_dem",
        notes=source.get("notes"),
    )


def analyze_global_planetary_aoi(request: ValidatedAoi) -> AoiResult:
    """Run a real bounded global D01 analysis for a Moon/Mars AOI."""
    body = request.body
    if body not in SUPPORTED_BODIES:
        raise AppError(
            "unsupported_body",
            "Global D01 analysis supports Moon or Mars only.",
            status_code=422,
        )
    bbox = request.bbox.as_dict()
    try:
        if body == "Moon":
            payload = _module("lola_global").analyze_lola_aoi(bbox).to_payload()
        else:
            payload = _module("mola_aoi").analyze_mola_aoi(bbox, allow_remote=True)
    except AppError:
        raise
    except Exception as exc:  # noqa: BLE001 - source failures must stay explicit
        raise AppError(
            "source_read_failed",
            f"{body} global DEM read failed: {type(exc).__name__}: {exc}",
            status_code=503,
            details=[{"body": body, "bbox": bbox, "source_status": "failed"}],
        ) from exc

    if not isinstance(payload, Mapping):
        raise AppError(
            "planetary_analysis_invalid",
            "The global planetary engine returned an unsupported payload.",
            status_code=503,
        )
    status = str(payload.get("status") or "")
    features = _feature_values(payload)
    if status in {"error", "no_data"} or not features:
        details = payload.get("errors") or payload.get("warnings") or []
        raise AppError(
            "no_data",
            f"The requested {body} AOI produced no valid D01 observations "
            f"(status={status or 'unknown'}).",
            status_code=422,
            details=[{"body": body, "bbox": bbox, "status": status, "details": details}],
        )

    source = payload.get("source") or {}
    if not isinstance(source, Mapping):
        source = {}
    dataset = _dataset(body, source)
    analysis_bbox = payload.get("analysis_bbox") or payload.get("analysis_bbox_lonlat") or bbox
    analysis_bbox = {key: float(analysis_bbox[key]) for key in ("west", "south", "east", "north")}
    analysis_resolution = (
        _finite(payload.get("analysis_resolution_m") or payload.get("resolution_m")) or 90.0
    )
    source_resolution = _finite(payload.get("source_resolution_m")) or _finite(
        source.get("native_resolution_m")
    )
    resolution_class = str(
        payload.get("resolution_class")
        or (
            "upsampled_from_coarse_source"
            if source_resolution and source_resolution > analysis_resolution
            else "native_or_downsampled"
        )
    )
    frame, fallback_crs = _FRAMES[body]
    crs = _crs_string(body, payload)
    if not crs:
        crs = fallback_crs
    features = {
        feature_id: value
        for feature_id, value in features.items()
        if feature_id in (request.requested_features or features)
    }
    if not features:
        raise AppError(
            "no_supported_features",
            "The global planetary AOI produced no requested D01 features.",
            status_code=422,
        )
    warnings = [str(item) for item in payload.get("warnings") or ()]
    warnings.append(
        "Global DEM bounded read over the requested bbox; this is not a global/exhaustive survey."
    )
    if resolution_class == "upsampled_from_coarse_source":
        warnings.append(
            "The global source is coarser than the 90 m analysis grid; values are not "
            "native 90 m terrain and are not comparable to Earth 90 m/1.98 km D01."
        )
    if body == "Mars":
        warnings.append(
            "MOLA MEGDR contains interpolated median bins where no shot falls in a bin; "
            "it is not raw altimetry and is not 90 m comparable."
        )
    warnings.append(
        "D02-D06 are unsupported for on-demand analysis at this AOI; they are not zero."
    )
    quality_flags = (
        "global_dem_arbitrary_aoi",
        "bounded_window_read",
        "not_earth_wgs84",
        "not_cross_body_scored",
        f"resolution_class:{resolution_class}",
    )
    method_version = _METHOD_VERSIONS[body]
    dimension_support: dict[str, Any] = {
        "D01": {
            "dimension_code": "D01",
            "status": "present",
            "quality": "derived_from_global_official_dem",
            "method_version": method_version,
            "source_resolution_m": source_resolution,
            "analysis_resolution_m": analysis_resolution,
            "resolution_class": resolution_class,
            "feature_ids": sorted(features),
            "feature_count": len(features),
            "observation_count": len(features),
            "analysis_kind": "global_dem_aoi",
        }
    }
    for code in ("D02", "D03", "D04", "D05", "D06"):
        dimension_support[code] = {
            "dimension_code": code,
            "status": "unsupported_for_on_demand_analysis",
            "reason": (
                "No verified on-demand source is wired for this dimension at this AOI; "
                "no value is fabricated. Use the committed evidence/coverage endpoints."
            ),
            "value": None,
        }
    width = _finite(payload.get("width_m")) or request.width_m
    height = _finite(payload.get("height_m")) or request.height_m
    return AoiResult(
        area_id=request.area_id,
        bbox=BBox(**analysis_bbox),
        geometry=None,
        area_km2=float(payload.get("area_km2") or request.area_km2),
        center_lon=(analysis_bbox["west"] + analysis_bbox["east"]) / 2.0,
        center_lat=(analysis_bbox["south"] + analysis_bbox["north"]) / 2.0,
        crs=crs,
        valid_fraction=float(payload.get("valid_fraction") or 0.0),
        window_size_m=float(max(width, height)),
        resolution_m=float(analysis_resolution),
        features=features,
        context_observations={},
        datasets=(dataset,),
        provider_status={dataset.dataset_id: dict(source)},
        quality_flags=quality_flags,
        warnings=tuple(warnings),
        checksum=str(payload.get("checksum") or "sha256:unverified"),
        body=body,
        coordinate_frame=frame,
        method_version=method_version,
        dimension_support=dimension_support,
        source_assets=(dict(source),) if source else (),
        coverage_note=(
            f"Global official {body} DEM bounded read over the requested bbox. "
            "Coverage is the source product footprint, not an exhaustive survey. "
            "D02-D06 are explicit unsupported on-demand statuses."
        ),
        analysis_kind="global_dem_aoi",
    )


__all__ = ["GLOBAL_D01_FEATURES", "SUPPORTED_BODIES", "analyze_global_planetary_aoi"]
