#!/usr/bin/env python
"""Build the Issue #170 Goal E D03/D04 AOI evidence package.

New bounded artifacts only.  Existing #43/#44 pilots are read and linked, never
rewritten.  Raw source bytes stay in the gitignored cache; only derived JSON,
JSONL and Markdown are committed.

Usage:
    uv run python pipelines/d02_d04_aoi/thermal_water/build.py --download
    uv run python pipelines/d02_d04_aoi/thermal_water/build.py
    uv run python pipelines/d02_d04_aoi/thermal_water/build.py --check
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
import sys
import urllib.error
import urllib.request
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from analog_pipelines.aoi_thermal_water import (
    D03,
    D04,
    DIVINER_GCP_DATASET_ID,
    DIVINER_GCP_URL,
    MARS,
    MOON,
    MOON_POLAR_CRS,
    ODYSSEY_NS_DATASET_ID,
    ODYSSEY_NS_URL,
    PRP_DATASET_ID,
    PRP_MAX_DEPTH_M,
    PRP_METHOD_ICE_DEPTH,
    PRP_METHOD_TEMPERATURE,
    PRP_URL,
    SOURCE_STATUS,
    TES_DATASET_ID,
    TES_DAY_MASK_URL,
    TES_DAY_URL,
    TES_NIGHT_MASK_URL,
    TES_NIGHT_URL,
    decode_tes_mask_uint8,
    decode_tes_uint16_msb,
    gcp_cell_indices,
    gcp_observation_payload,
    gcp_row_byte_range,
    gcp_row_index,
    index_existing_observations,
    moon_polar_xy_m,
    odyssey_ns_cell_indices,
    odyssey_ns_evidence_payload,
    odyssey_ns_row_byte_range,
    odyssey_ns_row_index,
    parse_gcp_row,
    parse_odyssey_ns_row,
    parse_prp_row,
    prp_aggregate,
    summarize_existing,
    tes_mask_byte_range,
    tes_observation_payload,
    tes_pixel_index,
    tes_value_byte_range,
)
from analog_pipelines.typed_contracts import AreaV2, DatasetV2, SourceAssetV2, TypedObservation

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUTPUT = "pipelines/d02_d04_aoi/thermal_water"
DEFAULT_CACHE = "var/cache/d03-d04-aoi-170"
WINDOW_SIZE_M = 1980.0
WINDOW_RADIUS_M = WINDOW_SIZE_M / 2.0
USER_AGENT = "planetary-analog/0.1 (+D03-D04-AOI-170)"
EXPORT_VERSION = "d03-d04-aoi-evidence-1.0.0"

GCP_PRODUCT_BYTES = 156_211_313
TES_DAY_BYTES = 51_840_000
TES_NIGHT_BYTES = 51_840_000
TES_MASK_BYTES = 25_920_000
PRP_BYTES = 604_800_210
ODYSSEY_NS_BYTES = 6_739_200


@dataclass(frozen=True, slots=True)
class Reference:
    reference_id: str
    display_name: str
    body: str
    latitude_deg: float
    longitude_deg: float
    dataset_id: str
    source_manifest: str


def repo_relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_references() -> list[Reference]:
    references: list[Reference] = []
    manifest_dir = ROOT / "pipelines" / "planetary" / "manifests"
    for path in sorted(manifest_dir.glob("lola_*_reference*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        for reference in raw.get("references", []):
            if reference.get("body") != MOON:
                continue
            references.append(
                Reference(
                    reference_id=reference["reference_id"],
                    display_name=reference.get("display_name") or reference["reference_id"],
                    body=MOON,
                    latitude_deg=float(reference["lat"]),
                    longitude_deg=float(reference["lon"]),
                    dataset_id=reference["dataset_id"],
                    source_manifest=repo_relative(path),
                )
            )
    mars_path = manifest_dir / "ctx_mars_references.json"
    raw = json.loads(mars_path.read_text(encoding="utf-8"))
    for reference in raw.get("references", []):
        if reference.get("body") != MARS:
            continue
        references.append(
            Reference(
                reference_id=reference["reference_id"],
                display_name=reference.get("display_name") or reference["reference_id"],
                body=MARS,
                latitude_deg=float(reference["lat"]),
                longitude_deg=float(reference["lon"]),
                dataset_id=reference["dataset_id"],
                source_manifest=repo_relative(mars_path),
            )
        )
    if not references:
        raise SystemExit("no Moon/Mars reference windows found")
    return references


def load_existing_observations() -> list[dict[str, Any]]:
    paths = [
        ROOT / "pipelines" / "pilots" / "d03_planetary_thermal" / "observations.jsonl",
        ROOT / "pipelines" / "pilots" / "d04_water_ice_planetary" / "observations.jsonl",
    ]
    observations: list[dict[str, Any]] = []
    for path in paths:
        if not path.exists():
            raise SystemExit(f"missing committed pilot observation file: {repo_relative(path)}")
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                observations.append(json.loads(line))
    return observations


class Fetcher:
    """Bounded HTTP range/full fetcher with a deterministic on-disk cache."""

    def __init__(self, cache_dir: Path, *, allow_download: bool) -> None:
        self.cache_dir = cache_dir
        self.allow_download = allow_download
        self.fetched: list[dict[str, Any]] = []

    def _request(self, url: str, start: int, end: int) -> bytes:
        request = urllib.request.Request(
            url,
            headers={"User-Agent": USER_AGENT, "Range": f"bytes={start}-{end}"},
        )
        with urllib.request.urlopen(request, timeout=120) as response:
            payload = response.read()
        expected = end - start + 1
        if len(payload) != expected:
            raise RuntimeError(
                f"range {start}-{end} returned {len(payload)} bytes, expected {expected}"
            )
        return payload

    def range_bytes(self, url: str, start: int, end: int, cache_name: str) -> bytes:
        path = self.cache_dir / cache_name
        expected = end - start + 1
        if path.exists() and path.stat().st_size == expected:
            return path.read_bytes()
        if not self.allow_download:
            raise SystemExit(
                f"missing cache {repo_relative(path)}; rerun with --download "
                f"(range {start}-{end} of {url})"
            )
        payload = self._request(url, start, end)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        self.fetched.append(
            {
                "url": url,
                "range": [start, end],
                "bytes": len(payload),
                "sha256": sha256_bytes(payload),
            }
        )
        return payload

    def full_file(self, url: str, cache_name: str, expected_bytes: int) -> Path:
        path = self.cache_dir / cache_name
        if path.exists() and path.stat().st_size == expected_bytes:
            return path
        if not self.allow_download:
            raise SystemExit(
                f"missing cache {repo_relative(path)}; rerun with --download "
                f"(expected {expected_bytes} bytes from {url})"
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with (
                urllib.request.urlopen(request, timeout=600) as response,
                path.open("wb") as handle,
            ):
                shutil.copyfileobj(response, handle, length=1024 * 1024)
        except urllib.error.HTTPError as exc:
            raise SystemExit(f"download failed for {url}: HTTP {exc.code}") from exc
        size = path.stat().st_size
        if size != expected_bytes:
            raise SystemExit(
                f"downloaded {repo_relative(path)} is {size} bytes, expected {expected_bytes}"
            )
        return path


def _future_source_result(future: Any, source: str, payload_key: str) -> dict[str, Any]:
    """Isolate one source failure so the other sources still produce artifacts."""
    try:
        return future.result()
    except Exception as exc:  # noqa: BLE001 - per-source isolation is intentional
        return {
            payload_key: [],
            "observations": [],
            "status": {"source": source, "state": "failed", "errors": [str(exc)]},
        }


def build_gcp(references: Sequence[Reference], fetcher: Fetcher) -> dict[str, Any]:
    observations: list[dict[str, Any]] = []
    status = {"source": "diviner-gcp", "state": "ok", "errors": [], "rows": 0}
    bins = (("00:00-00:15", 0), ("12:00-12:15", 48))
    for reference in references:
        try:
            lon_index, lat_index = gcp_cell_indices(reference.longitude_deg, reference.latitude_deg)
            for label, ltim_index in bins:
                row_index = gcp_row_index(lon_index, lat_index, ltim_index)
                start, end = gcp_row_byte_range(row_index)
                payload = fetcher.range_bytes(
                    DIVINER_GCP_URL, start, end, f"gcp/row_{row_index:07d}.bin"
                )
                row = parse_gcp_row(payload.decode("ascii"))
                observations.append(
                    gcp_observation_payload(
                        reference_id=reference.reference_id,
                        display_name=reference.display_name,
                        lat_deg=reference.latitude_deg,
                        lon_deg=reference.longitude_deg,
                        lon_index=lon_index,
                        lat_index=lat_index,
                        ltim_index=ltim_index,
                        ltim_label=label,
                        row=row,
                        asset_id="diviner-gcp:rows",
                        source_manifest=reference.source_manifest,
                    )
                )
                status["rows"] += 1
        except Exception as exc:  # noqa: BLE001 - per-source failure must not stop others
            status["state"] = "partial"
            status["errors"].append(f"{reference.reference_id}: {exc}")
    return {"observations": observations, "status": status}


def build_tes(references: Sequence[Reference], fetcher: Fetcher) -> dict[str, Any]:
    observations: list[dict[str, Any]] = []
    status = {"source": "tes-timap", "state": "ok", "errors": [], "pixels": 0}
    phases = (
        ("day", TES_DAY_URL, TES_DAY_MASK_URL, "tes/day_value", "tes/day_mask"),
        ("night", TES_NIGHT_URL, TES_NIGHT_MASK_URL, "tes/night_value", "tes/night_mask"),
    )
    for reference in references:
        try:
            line0, sample0 = tes_pixel_index(reference.latitude_deg, reference.longitude_deg)
            for phase, url, mask_url, value_prefix, mask_prefix in phases:
                vstart, vend = tes_value_byte_range(line0, sample0)
                raw = fetcher.range_bytes(
                    url, vstart, vend, f"{value_prefix}_l{line0 + 1:04d}_s{sample0 + 1:04d}.bin"
                )
                mstart, mend = tes_mask_byte_range(line0, sample0)
                mask_payload = fetcher.range_bytes(
                    mask_url, mstart, mend, f"{mask_prefix}_l{line0 + 1:04d}_s{sample0 + 1:04d}.bin"
                )
                observations.append(
                    tes_observation_payload(
                        reference_id=reference.reference_id,
                        display_name=reference.display_name,
                        lat_deg=reference.latitude_deg,
                        lon_deg=reference.longitude_deg,
                        line0=line0,
                        sample0=sample0,
                        phase=phase,
                        raw_value=decode_tes_uint16_msb(raw),
                        mask=decode_tes_mask_uint8(mask_payload),
                        asset_id="tes-timap:rows",
                        source_manifest=reference.source_manifest,
                    )
                )
                status["pixels"] += 1
        except Exception as exc:  # noqa: BLE001
            status["state"] = "partial"
            status["errors"].append(f"{reference.reference_id}: {exc}")
    return {"observations": observations, "status": status}


def build_odyssey_ns(references: Sequence[Reference], fetcher: Fetcher) -> dict[str, Any]:
    modeled: list[dict[str, Any]] = []
    status = {"source": "odyssey-ns", "state": "ok", "errors": [], "cells": 0}
    for reference in references:
        try:
            lon_index, lat_index = odyssey_ns_cell_indices(
                reference.longitude_deg, reference.latitude_deg
            )
            row_index = odyssey_ns_row_index(lon_index, lat_index)
            start, end = odyssey_ns_row_byte_range(row_index)
            payload = fetcher.range_bytes(
                ODYSSEY_NS_URL, start, end, f"odyssey_ns/row_{row_index:06d}.bin"
            )
            row = parse_odyssey_ns_row(payload.decode("ascii"))
            modeled.append(
                odyssey_ns_evidence_payload(
                    reference_id=reference.reference_id,
                    display_name=reference.display_name,
                    lat_deg=reference.latitude_deg,
                    lon_deg=reference.longitude_deg,
                    lon_index=lon_index,
                    lat_index=lat_index,
                    row=row,
                    asset_id="odyssey-ns:rows",
                    source_manifest=reference.source_manifest,
                )
            )
            status["cells"] += 1
        except Exception as exc:  # noqa: BLE001
            status["state"] = "partial"
            status["errors"].append(f"{reference.reference_id}: {exc}")
    return {"modeled": modeled, "status": status}


def _prp_candidate_rows(
    path: Path, references: Sequence[Reference]
) -> dict[str, list[dict[str, Any]]]:
    """One streaming pass over PRP; keep only triangles inside each AOI window."""
    lat_keys: dict[int, list[Reference]] = {}
    for reference in references:
        key = int(round(reference.latitude_deg * 10))
        lat_keys.setdefault(key, []).append(reference)
    selected: dict[str, list[dict[str, Any]]] = {ref.reference_id: [] for ref in references}
    with path.open("r", encoding="ascii", errors="strict") as handle:
        handle.readline()  # header
        for line in handle:
            if not line.strip():
                continue
            parts = line.split(",")
            if len(parts) != 15:
                continue
            try:
                clat = float(parts[10])
                clon = float(parts[9])
            except ValueError:
                continue
            base_key = int(round(clat * 10))
            candidates: list[Reference] = []
            for delta in (-2, -1, 0, 1, 2):
                candidates.extend(lat_keys.get(base_key + delta, ()))
            if not candidates:
                continue
            row = parse_prp_row(line)
            for reference in candidates:
                distance = _angular_distance_m(
                    reference.latitude_deg,
                    reference.longitude_deg,
                    clat,
                    clon,
                )
                if distance <= WINDOW_RADIUS_M:
                    item = dict(row)
                    item["centroid_distance_m"] = distance
                    item["area_km2"] = _triangle_area(row)
                    selected[reference.reference_id].append(item)
    return selected


def _angular_distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = phi2 - phi1
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2.0) ** 2
    return 2.0 * 1737.4 * 1000.0 * math.asin(min(1.0, math.sqrt(a)))


def _triangle_area(row: dict[str, Any]) -> float:
    v1, v2, v3 = row["vertices"]
    ax, ay, az = (v2[0] - v1[0], v2[1] - v1[1], v2[2] - v1[2])
    bx, by, bz = (v3[0] - v1[0], v3[1] - v1[1], v3[2] - v1[2])
    cx = ay * bz - az * by
    cy = az * bx - ax * bz
    cz = ax * by - ay * bx
    return 0.5 * math.sqrt(cx * cx + cy * cy + cz * cz)


def build_prp(references: Sequence[Reference], fetcher: Fetcher) -> dict[str, Any]:
    status = {"source": "diviner-prp", "state": "ok", "errors": [], "triangles": 0}
    observations: list[dict[str, Any]] = []
    modeled: list[dict[str, Any]] = []
    try:
        path = fetcher.full_file(PRP_URL, "dlre_prp_south.tab", PRP_BYTES)
        selected = _prp_candidate_rows(path, references)
    except Exception as exc:  # noqa: BLE001 - do not stop the other sources
        return {
            "observations": [],
            "modeled": [],
            "status": {
                "source": "diviner-prp",
                "state": "failed",
                "errors": [str(exc)],
                "triangles": 0,
            },
        }
    for reference in references:
        triangles = selected.get(reference.reference_id, [])
        status["triangles"] += len(triangles)
        aggregate = prp_aggregate(triangles, window_size_m=WINDOW_SIZE_M)
        center_x, center_y = moon_polar_xy_m(reference.latitude_deg, reference.longitude_deg)
        half = WINDOW_SIZE_M / 2.0
        bbox = {
            "west": center_x - half,
            "south": center_y - half,
            "east": center_x + half,
            "north": center_y + half,
        }
        if aggregate["temperature"] is not None:
            temperature = aggregate["temperature"]
            observations.append(
                {
                    "schema_version": "2.0.0",
                    "observation_id": f"diviner-prp:{reference.reference_id}:surface_temperature_mean_k",
                    "area_id": f"moon-prp-window-{reference.reference_id}",
                    "body": MOON,
                    "dimension_code": D03,
                    "feature_id": "surface_temperature_mean_k",
                    "value_kind": "numeric",
                    "state": "detected",
                    "value": temperature["annual_mean_2cm_k"],
                    "unit": "K",
                    "uncertainty": {
                        "kind": "unknown",
                        "value": None,
                        "lower": None,
                        "upper": None,
                        "unit": "K",
                        "notes": "Model-derived; no per-triangle uncertainty in the PRP product.",
                    },
                    "spatial_support": {
                        "kind": "window",
                        "crs": MOON_POLAR_CRS,
                        "window_size_m": WINDOW_SIZE_M,
                        "bbox": bbox,
                        "bbox_crs": MOON_POLAR_CRS,
                        "native_resolution_m": 350.0,
                        "source_resolution_m": 350.0,
                        "valid_fraction": aggregate["coverage"]["triangle_area_ratio_of_window"],
                        "notes": (
                            "Area-weighted mean of PRP triangle centroids inside the 1.98 km "
                            "window; native mesh ~0.3-0.4 km, no resampling to 90 m."
                        ),
                    },
                    "temporal_support": {
                        "kind": "static",
                        "start": None,
                        "end": None,
                        "local_solar_time": None,
                        "notes": (
                            "Annual average at 2 cm below the surface, derived from Diviner "
                            "climatology and a Kaguya DEM; not a measured time series."
                        ),
                    },
                    "depth_support": {"kind": "depth_interval", "min_m": 0.0, "max_m": 0.02},
                    "method_version": PRP_METHOD_TEMPERATURE,
                    "dataset_ids": [PRP_DATASET_ID],
                    "source_asset_ids": ["diviner-prp:source"],
                    "quality_flags": [
                        "modeled_annual_temperature",
                        "annual_mean_at_2cm",
                        "not_a_measured_time_series",
                        "native_mesh_no_resampling",
                        "planetary_projected_frame",
                    ],
                    "score_role": "evidence_only",
                    "legacy_provenance": {
                        "reference_id": reference.reference_id,
                        "display_name": reference.display_name,
                        "reference_latitude_deg": reference.latitude_deg,
                        "reference_longitude_deg": reference.longitude_deg,
                        "source_manifest": reference.source_manifest,
                        "n_triangles": aggregate["n_triangles"],
                        "coverage": aggregate["coverage"],
                        "annual_mean_2cm_min_k": temperature["annual_mean_2cm_min_k"],
                        "annual_mean_2cm_max_k": temperature["annual_mean_2cm_max_k"],
                        "annual_mean_2cm_std_k": temperature["annual_mean_2cm_std_k"],
                    },
                    "notes": (
                        "PRP modeled annual average temperature at 2 cm depth. "
                        "Not a measured surface temperature and not a local-time sample."
                    ),
                }
            )
        if aggregate["temperature"] is not None:
            temperature = aggregate["temperature"]
            modeled.append(
                {
                    "evidence_id": f"diviner-prp:{reference.reference_id}:annual_max_surface_temperature",
                    "area_id": f"moon-prp-window-{reference.reference_id}",
                    "body": MOON,
                    "dimension_code": D03,
                    "quantity_id": "diviner_prp_annual_max_surface_temperature_k",
                    "value_kind": "numeric",
                    "state": "detected",
                    "value": temperature["annual_max_surface_mean_k"],
                    "unit": "K",
                    "evidence_class": "modeled",
                    "spatial_support": {
                        "kind": "window",
                        "crs": MOON_POLAR_CRS,
                        "window_size_m": WINDOW_SIZE_M,
                        "bbox": bbox,
                        "bbox_crs": MOON_POLAR_CRS,
                        "native_resolution_m": 350.0,
                        "source_resolution_m": 350.0,
                        "valid_fraction": aggregate["coverage"]["triangle_area_ratio_of_window"],
                    },
                    "temporal_support": {
                        "kind": "static",
                        "start": None,
                        "end": None,
                        "local_solar_time": None,
                        "notes": "Annual maximum at the surface, derived from Diviner climatology.",
                    },
                    "depth_support": {"kind": "surface", "min_m": None, "max_m": None},
                    "method_version": PRP_METHOD_TEMPERATURE,
                    "source": _source_ref("diviner-prp", "diviner-prp:source"),
                    "quality_flags": [
                        "modeled_annual_maximum",
                        "no_registered_max_temperature_feature",
                        "not_a_measured_time_series",
                    ],
                    "uncertainty": {
                        "kind": "unknown",
                        "value": None,
                        "lower": None,
                        "upper": None,
                        "unit": "K",
                        "notes": "No per-triangle uncertainty in the product.",
                    },
                    "legacy_provenance": {
                        "reference_id": reference.reference_id,
                        "display_name": reference.display_name,
                        "annual_max_surface_max_k": temperature["annual_max_surface_max_k"],
                        "n_triangles": aggregate["n_triangles"],
                    },
                    "notes": (
                        "Model-derived annual maximum surface temperature. Stored as "
                        "pilot-local modeled evidence because no registered D03 maximum "
                        "temperature feature exists."
                    ),
                }
            )
        ice = aggregate["ice_depth"]
        valid_fraction = ice["valid_area_fraction"] if ice else 0.0
        if ice is not None and valid_fraction >= 0.5:
            state = "detected"
            value = ice["mean_stability_depth_m"]
            reason = None
        else:
            state = "unknown"
            value = None
            reason = (
                "less_than_half_of_window_area_has_a_valid_shallow_stability_depth"
                if ice is not None
                else "all_triangles_excluded_gt_2_8738m_or_invalid"
            )
        modeled.append(
            {
                "evidence_id": f"diviner-prp:{reference.reference_id}:water_ice_stability_depth",
                "area_id": f"moon-prp-window-{reference.reference_id}",
                "body": MOON,
                "dimension_code": D04,
                "quantity_id": "diviner_prp_water_ice_stability_depth_m",
                "value_kind": "numeric",
                "state": state,
                "value": value,
                "unit": "meter",
                "evidence_class": "modeled",
                "spatial_support": {
                    "kind": "window",
                    "crs": MOON_POLAR_CRS,
                    "window_size_m": WINDOW_SIZE_M,
                    "bbox": bbox,
                    "bbox_crs": MOON_POLAR_CRS,
                    "native_resolution_m": 350.0,
                    "source_resolution_m": 350.0,
                    "valid_fraction": valid_fraction,
                },
                "temporal_support": {
                    "kind": "static",
                    "start": None,
                    "end": None,
                    "local_solar_time": None,
                    "notes": (
                        "Modeled sublimation-stability depth from annual Diviner "
                        "temperatures and a Kaguya DEM; not an observed ice depth."
                    ),
                },
                "depth_support": {
                    "kind": "depth_interval",
                    "min_m": 0.0,
                    "max_m": PRP_MAX_DEPTH_M,
                },
                "method_version": PRP_METHOD_ICE_DEPTH,
                "source": _source_ref("diviner-prp", "diviner-prp:source"),
                "quality_flags": [
                    "modeled_ice_stability",
                    "not_an_ice_detection",
                    "not_ice_fraction",
                    "zero_means_surface_stable",
                    "minus_999_excluded_not_zero_filled",
                ],
                "uncertainty": {
                    "kind": "unknown",
                    "value": None,
                    "lower": None,
                    "upper": None,
                    "unit": "meter",
                    "notes": (
                        "No per-triangle uncertainty. The product excludes calculated "
                        "depths greater than 2.8738 m with -999.0."
                    ),
                },
                "legacy_provenance": {
                    "reference_id": reference.reference_id,
                    "display_name": reference.display_name,
                    "n_triangles": aggregate["n_triangles"],
                    "coverage": aggregate["coverage"],
                    "mean_stability_depth_m": ice["mean_stability_depth_m"] if ice else None,
                    "min_stability_depth_m": ice["min_stability_depth_m"] if ice else None,
                    "max_stability_depth_m": ice["max_stability_depth_m"] if ice else None,
                    "surface_stable_triangle_count": (
                        ice["surface_stable_triangle_count"] if ice else 0
                    ),
                    "excluded_gt_2_8738m_count": (
                        ice["excluded_gt_2_8738m_count"] if ice else aggregate["n_triangles"]
                    ),
                    "reason_code": reason,
                },
                "notes": (
                    "PRP modeled depth at which water ice would sublimate at "
                    "1 kg m-2 per billion years. 0 means stable at the surface; -999 "
                    "means the modeled depth exceeds 2.8738 m and is excluded, never "
                    "zero-filled. This is not an observed ice detection or abundance."
                ),
            }
        )
        modeled.append(
            {
                "evidence_id": (
                    f"diviner-prp:{reference.reference_id}:shallow_stability_area_fraction"
                ),
                "area_id": f"moon-prp-window-{reference.reference_id}",
                "body": MOON,
                "dimension_code": D04,
                "quantity_id": "diviner_prp_shallow_stability_area_fraction",
                "value_kind": "numeric",
                "state": "detected",
                "value": valid_fraction,
                "unit": "1",
                "evidence_class": "modeled",
                "spatial_support": {
                    "kind": "window",
                    "crs": MOON_POLAR_CRS,
                    "window_size_m": WINDOW_SIZE_M,
                    "bbox": bbox,
                    "bbox_crs": MOON_POLAR_CRS,
                    "native_resolution_m": 350.0,
                    "source_resolution_m": 350.0,
                    "valid_fraction": aggregate["coverage"]["triangle_area_ratio_of_window"],
                },
                "temporal_support": {
                    "kind": "static",
                    "start": None,
                    "end": None,
                    "local_solar_time": None,
                    "notes": "Modeled annual climatology; not a measured time series.",
                },
                "depth_support": {
                    "kind": "depth_interval",
                    "min_m": 0.0,
                    "max_m": PRP_MAX_DEPTH_M,
                },
                "method_version": PRP_METHOD_ICE_DEPTH,
                "source": _source_ref("diviner-prp", "diviner-prp:source"),
                "quality_flags": [
                    "modeled_ice_stability",
                    "fraction_of_window_area_not_ice_fraction",
                    "not_an_ice_detection",
                    "minus_999_excluded_not_zero_filled",
                ],
                "uncertainty": {
                    "kind": "unknown",
                    "value": None,
                    "lower": None,
                    "upper": None,
                    "unit": "1",
                    "notes": "No per-triangle uncertainty in the product.",
                },
                "legacy_provenance": {
                    "reference_id": reference.reference_id,
                    "display_name": reference.display_name,
                    "n_triangles": aggregate["n_triangles"],
                    "coverage": aggregate["coverage"],
                    "valid_area_fraction": valid_fraction,
                    "excluded_area_fraction": ice["excluded_area_fraction"] if ice else 1.0,
                    "definition": (
                        "area fraction of selected PRP triangles with modeled ice-stability "
                        "depth <= 2.8738 m; -999 (>2.8738 m) is excluded, never zero-filled"
                    ),
                },
                "notes": (
                    "Modeled fraction of the 1.98 km window with a shallow (<=2.8738 m) "
                    "water-ice stability depth. This is an area fraction, not an ice "
                    "fraction, abundance or detection."
                ),
            }
        )
    return {"observations": observations, "modeled": modeled, "status": status}


def _source_ref(source_key: str, asset_id: str) -> dict[str, Any]:
    status = SOURCE_STATUS[source_key]
    return {
        "dataset_id": status["dataset_id"],
        "product_id": status["product_id"],
        "url": status["url"],
        "doi": status["doi"],
        "citation": status["citation"],
        "license": status["license"],
        "license_url": status["license_url"],
        "source_asset_id": asset_id,
    }


def build_areas(
    observations: Sequence[dict[str, Any]], modeled: Sequence[dict[str, Any]]
) -> list[dict[str, Any]]:
    areas: dict[str, dict[str, Any]] = {}

    def add(area_id: str, body: str, spatial: dict[str, Any], dimension: str) -> None:
        if area_id in areas:
            return
        frame = {
            "Moon": "lunar_planetocentric"
            if spatial["crs"].startswith("+proj=longlat")
            else "lunar_projected",
            "Mars": "mars_planetocentric"
            if spatial["crs"].startswith("+proj=longlat")
            else "mars_projected",
        }[body]
        areas[area_id] = {
            "schema_version": "2.0.0",
            "area_id": area_id,
            "body": body,
            "bbox": spatial.get("bbox"),
            "crs": spatial["crs"],
            "analysis_crs": spatial["crs"],
            "coordinate_frame": frame,
            "source_area_refs": [],
            "notes": f"New #170 {dimension} AOI evidence area; native source support recorded per observation.",
        }

    for observation in observations:
        add(
            observation["area_id"],
            observation["body"],
            observation["spatial_support"],
            observation["dimension_code"],
        )
    for evidence in modeled:
        add(
            evidence["area_id"],
            evidence["body"],
            evidence["spatial_support"],
            evidence["dimension_code"],
        )
    return [areas[key] for key in sorted(areas)]


def build_datasets() -> list[dict[str, Any]]:
    datasets: list[dict[str, Any]] = []
    for key in ("diviner-gcp", "tes-timap", "diviner-prp", "odyssey-ns"):
        status = SOURCE_STATUS[key]
        datasets.append(
            {
                "schema_version": "2.0.0",
                "dataset_id": status["dataset_id"],
                "body": status["body"],
                "provider": status["provider"],
                "product_id": status["product_id"],
                "version": status["version"],
                "url": status["url"],
                "license": status["license"],
                "license_url": status["license_url"],
                "verification_status": "verified",
                "native_crs": status["native_crs"],
                "native_resolution_m": status["native_resolution_m"],
                "temporal_support": status["temporal_support"],
                "depth_support": status["depth_support"],
                "redistribution_allowed": "yes",
            }
        )
    return datasets


def _cache_fetched_at(paths: Sequence[Path]) -> str:
    newest = max(path.stat().st_mtime for path in paths)
    return (
        datetime.fromtimestamp(newest, UTC)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def build_assets(fetcher: Fetcher, *, allow_download: bool) -> list[dict[str, Any]]:
    assets: list[dict[str, Any]] = []
    # Hash the cached range payloads per source so the committed provenance is reproducible.
    for source_key, dataset_id, cache_glob, scope in (
        ("diviner-gcp", DIVINER_GCP_DATASET_ID, "gcp/*.bin", "window"),
        ("tes-timap", TES_DATASET_ID, "tes/*.bin", "window"),
        ("odyssey-ns", ODYSSEY_NS_DATASET_ID, "odyssey_ns/*.bin", "window"),
    ):
        paths = sorted(fetcher.cache_dir.glob(cache_glob))
        if not paths:
            continue
        digest = hashlib.sha256()
        total = 0
        for path in paths:
            payload = path.read_bytes()
            digest.update(payload)
            total += len(payload)
        assets.append(
            {
                "schema_version": "2.0.0",
                "asset_id": f"{source_key}:rows",
                "dataset_id": dataset_id,
                "uri": SOURCE_STATUS[source_key]["url"],
                "checksum_algorithm": "sha256",
                "checksum": digest.hexdigest(),
                "checksum_scope": scope,
                "native_crs": SOURCE_STATUS[source_key]["native_crs"],
                "native_units": "mixed",
                "bytes": total,
                "fetched_at": _cache_fetched_at(paths),
                "redistribution_allowed": "yes",
            }
        )
    prp_path = fetcher.cache_dir / "dlre_prp_south.tab"
    if prp_path.exists():
        assets.append(
            {
                "schema_version": "2.0.0",
                "asset_id": "diviner-prp:source",
                "dataset_id": PRP_DATASET_ID,
                "uri": PRP_URL,
                "checksum_algorithm": "sha256",
                "checksum": sha256_file(prp_path),
                "checksum_scope": "source",
                "native_crs": SOURCE_STATUS["diviner-prp"]["native_crs"],
                "native_units": "K, m",
                "bytes": prp_path.stat().st_size,
                "fetched_at": _cache_fetched_at([prp_path]),
                "redistribution_allowed": "yes",
            }
        )
    return assets


def build_aoi_evidence(
    references: Sequence[Reference],
    existing: Mapping[str, list[dict[str, Any]]],
    observations: Sequence[dict[str, Any]],
    modeled: Sequence[dict[str, Any]],
    statuses: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    new_by_reference: dict[str, list[dict[str, Any]]] = {}
    for row in observations:
        legacy = row.get("legacy_provenance") or {}
        reference_id = legacy.get("reference_id")
        if reference_id:
            new_by_reference.setdefault(reference_id, []).append(
                {
                    "kind": "typed_observation",
                    "observation_id": row["observation_id"],
                    "dimension_code": row["dimension_code"],
                    "feature_id": row["feature_id"],
                    "state": row["state"],
                    "value": row["value"],
                    "unit": row["unit"],
                    "method_version": row["method_version"],
                }
            )
    for row in modeled:
        legacy = row.get("legacy_provenance") or {}
        reference_id = legacy.get("reference_id")
        if reference_id:
            new_by_reference.setdefault(reference_id, []).append(
                {
                    "kind": "modeled_evidence",
                    "evidence_id": row["evidence_id"],
                    "dimension_code": row["dimension_code"],
                    "quantity_id": row["quantity_id"],
                    "state": row["state"],
                    "value": row["value"],
                    "unit": row["unit"],
                    "method_version": row["method_version"],
                }
            )
    status_by_source = {status["source"]: status for status in statuses}
    aois: list[dict[str, Any]] = []
    for reference in references:
        existing_rows = summarize_existing(existing.get(reference.reference_id, []))
        new_rows = sorted(
            new_by_reference.get(reference.reference_id, []),
            key=lambda row: (
                row["dimension_code"],
                row["kind"],
                row.get("feature_id") or row.get("quantity_id") or "",
            ),
        )
        d03 = [row for row in new_rows if row["dimension_code"] == D03]
        d04 = [row for row in new_rows if row["dimension_code"] == D04]
        aois.append(
            {
                "reference_id": reference.reference_id,
                "display_name": reference.display_name,
                "body": reference.body,
                "latitude_deg": reference.latitude_deg,
                "longitude_deg": reference.longitude_deg,
                "window_size_m": WINDOW_SIZE_M,
                "source_manifest": reference.source_manifest,
                "d03": {
                    "existing_observations": [
                        row for row in existing_rows if row["dimension_code"] == D03
                    ],
                    "new_evidence": d03,
                    "counts": _state_counts(
                        [row for row in existing_rows if row["dimension_code"] == D03] + d03
                    ),
                },
                "d04": {
                    "existing_observations": [
                        row for row in existing_rows if row["dimension_code"] == D04
                    ],
                    "new_evidence": d04,
                    "counts": _state_counts(
                        [row for row in existing_rows if row["dimension_code"] == D04] + d04
                    ),
                },
            }
        )
    return {
        "schema_version": EXPORT_VERSION,
        "window_size_m": WINDOW_SIZE_M,
        "source_status": status_by_source,
        "non_claims": [
            "No D03 or D04 similarity score or cross-body ranking.",
            "GCP and TES are coarse context, not 240 m/100 m AOI measurements.",
            "PRP values are model-derived, not observed ice detections or ice abundance.",
            "Odyssey NS counts/second are not the normalized HEND flux ratio.",
            "No value is zero-filled; missing states carry no numeric value.",
            "Coverage is limited to the 23 committed Moon/Mars reference windows.",
        ],
        "aois": aois,
    }


def _state_counts(rows: Sequence[dict[str, Any]]) -> dict[str, int]:
    counts = Counter(str(row["state"]) for row in rows)
    return {key: counts[key] for key in sorted(counts)}


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")


def build(output: Path, cache: Path, *, allow_download: bool) -> dict[str, Any]:
    references = load_references()
    existing_observations = load_existing_observations()
    existing_index = index_existing_observations(existing_observations)
    fetcher = Fetcher(cache, allow_download=allow_download)

    moon_refs = [reference for reference in references if reference.body == MOON]
    mars_refs = [reference for reference in references if reference.body == MARS]

    # Two concurrent workers at most. PRP is a single sequential download.
    with ThreadPoolExecutor(max_workers=2) as pool:
        gcp_future = pool.submit(build_gcp, moon_refs, fetcher)
        tes_future = pool.submit(build_tes, mars_refs, fetcher)
        ns_future = pool.submit(build_odyssey_ns, mars_refs, fetcher)
        gcp = _future_source_result(gcp_future, "diviner-gcp", "observations")
        tes = _future_source_result(tes_future, "tes-timap", "observations")
        ns = _future_source_result(ns_future, "odyssey-ns", "modeled")
    prp = build_prp(moon_refs, fetcher)

    observations = [*gcp["observations"], *tes["observations"], *prp["observations"]]
    modeled = [*prp["modeled"], *ns["modeled"]]
    statuses = [gcp["status"], tes["status"], prp["status"], ns["status"]]

    typed = []
    for payload in observations:
        typed.append(TypedObservation.model_validate(payload).model_dump(mode="json"))
    areas = [
        AreaV2.model_validate(payload).model_dump(mode="json")
        for payload in build_areas(typed, modeled)
    ]
    datasets = [
        DatasetV2.model_validate(payload).model_dump(mode="json") for payload in build_datasets()
    ]
    assets = [
        SourceAssetV2.model_validate(payload).model_dump(mode="json")
        for payload in build_assets(fetcher, allow_download=allow_download)
    ]
    aoi_evidence = build_aoi_evidence(references, existing_index, typed, modeled, statuses)

    output.mkdir(parents=True, exist_ok=True)
    write_json(
        output / "sources.json",
        {
            "schema_version": EXPORT_VERSION,
            "new_sources": SOURCE_STATUS,
            "reused_committed_pilots": [
                "pipelines/pilots/d03_planetary_thermal/observations.jsonl",
                "pipelines/pilots/d04_water_ice_planetary/observations.jsonl",
            ],
            "notes": (
                "Full source metadata for the new sources. Reused #43/#44 observations are "
                "linked by observation_id in aoi_evidence.json and are not rewritten."
            ),
        },
    )
    write_json(output / "datasets.json", datasets)
    write_json(output / "areas.json", areas)
    write_json(output / "assets.json", assets)
    write_jsonl(output / "observations.jsonl", typed)
    write_jsonl(output / "modeled_evidence.jsonl", modeled)
    write_json(output / "aoi_evidence.json", aoi_evidence)

    checksums = {
        name: sha256_file(output / name)
        for name in (
            "sources.json",
            "datasets.json",
            "areas.json",
            "assets.json",
            "observations.jsonl",
            "modeled_evidence.jsonl",
            "aoi_evidence.json",
        )
    }
    source_counts = Counter(row["state"] for row in modeled)
    manifest = {
        "schema_version": EXPORT_VERSION,
        "generated_at": utc_now(),
        "issue": 170,
        "goal": "E",
        "scope": "D03 thermal + D04 water/ice AOI evidence for committed Moon/Mars reference windows",
        "window_size_m": WINDOW_SIZE_M,
        "reference_count": len(references),
        "moon_reference_count": len(moon_refs),
        "mars_reference_count": len(mars_refs),
        "observation_count": len(typed),
        "modeled_evidence_count": len(modeled),
        "area_count": len(areas),
        "dataset_count": len(datasets),
        "asset_count": len(assets),
        "observation_states": dict(sorted(Counter(row["state"] for row in typed).items())),
        "modeled_evidence_states": dict(sorted(source_counts.items())),
        "source_status": {status["source"]: status for status in statuses},
        "checksums": checksums,
        "raw_cache": repo_relative(cache),
        "non_claims": aoi_evidence["non_claims"],
    }
    write_json(output / "manifest.json", manifest)
    return manifest


def check(output: Path) -> list[str]:
    errors: list[str] = []
    manifest_path = output / "manifest.json"
    if not manifest_path.exists():
        return [f"missing {repo_relative(manifest_path)}"]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for name, expected in manifest.get("checksums", {}).items():
        path = output / name
        if not path.exists():
            errors.append(f"missing {name}")
            continue
        actual = sha256_file(path)
        if actual != expected:
            errors.append(f"checksum mismatch for {name}")
    observations = [
        json.loads(line)
        for line in (output / "observations.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    modeled = [
        json.loads(line)
        for line in (output / "modeled_evidence.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    for row in observations:
        if row["state"] == "detected" and row.get("value") is None:
            errors.append(f"detected observation without value: {row['observation_id']}")
        if row["state"] != "detected" and row.get("value") is not None:
            errors.append(f"missing-state observation carries a value: {row['observation_id']}")
    for row in modeled:
        if row["state"] == "detected" and row.get("value") is None:
            errors.append(f"detected modeled evidence without value: {row['evidence_id']}")
        if row["state"] != "detected" and row.get("value") is not None:
            errors.append(f"missing-state modeled evidence carries a value: {row['evidence_id']}")
    aoi = json.loads((output / "aoi_evidence.json").read_text(encoding="utf-8"))
    if len(aoi.get("aois", [])) != manifest.get("reference_count"):
        errors.append("AOI count does not match manifest reference_count")
    return errors


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--download", action="store_true", help="fetch missing bounded source bytes"
    )
    parser.add_argument("--check", action="store_true", help="validate committed artifacts offline")
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--cache", default=DEFAULT_CACHE)
    args = parser.parse_args(argv)

    output = ROOT / args.output
    if args.check:
        errors = check(output)
        if errors:
            for error in errors:
                print(f"ERROR: {error}", file=sys.stderr)
            return 1
        print(f"OK: {repo_relative(output)} artifacts are internally consistent")
        return 0

    manifest = build(output, ROOT / args.cache, allow_download=args.download)
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
