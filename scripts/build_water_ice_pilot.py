#!/usr/bin/env python
"""Build the bounded D04 Moon/Mars water/ice evidence pilot (Issue #44).

The pilot reads three official public PDS products over bounded windows around
the existing Moon and Mars terrain reference areas:

* Lunar Prospector half-degree hydrogen abundance (Moon, water-equivalent H).
* LRO Mini-RF 128 ppd global CPR mosaic (Moon, native 236.9 m pixels).
* Mars Odyssey HEND 4x4 degree normalized epithermal neutron flux (Mars).

No ice fraction, mineable-ice volume, or D04 similarity score is produced. Raw
products and window bytes stay in the gitignored ``var/cache/water-44`` cache; the
committed artifact contains typed observations, checksums and measured counts.

Usage::

    uv run python scripts/build_water_ice_pilot.py --download
    uv run python scripts/build_water_ice_pilot.py
    uv run python scripts/build_water_ice_pilot.py --check
"""

from __future__ import annotations

import argparse
import glob
import json
import sys
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from analog_pipelines.acquisition.models import (
    AcquisitionPlan,
    AcquisitionTask,
    PlanArea,
    file_sha256,
    write_json,
)
from analog_pipelines.typed_contracts import TypedObservation
from analog_pipelines.water_ice import (
    CANDIDATE_SOURCES,
    FEATURE_EPITHERMAL_NEUTRON_FLUX,
    FEATURE_RADAR_CPR,
    FEATURE_WATER_EQUIVALENT_HYDROGEN,
    FEATURE_WATER_ICE_DETECTION,
    SOURCES,
    HttpRangeReader,
    LatLonCell,
    MiniRfWindow,
    RangeCache,
    WaterIceError,
    build_detection_claim_observation,
    build_neutron_flux_observation,
    build_radar_cpr_observation,
    build_water_equivalent_hydrogen_observation,
    decode_mini_rf_window,
    find_cell,
    hydrogen_ice_claim,
    mini_rf_window,
    radar_ice_claim,
    read_hend_epithermal_ascii,
    read_lp_hydrogen_ascii,
    sha256_bytes,
    summarize_window,
    validate_water_ice_observation,
    write_observations_jsonl,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = "pipelines/pilots/d04_water_ice_planetary"
DEFAULT_DOC = "docs/D04_WATER_ICE_PILOT.md"
DEFAULT_CACHE = "var/cache/water-44"
RELEASE_VERSION = "d04-water-ice-pilot-1.0.0"
SCOPE = "pilot-d04-water-ice-planetary"
MOON_MANIFEST_GLOB = "pipelines/planetary/manifests/lola_*reference*.json"
MARS_MANIFEST = "pipelines/planetary/manifests/ctx_mars_references.json"
LP_FILENAME = "hydrogenhd.txt"
HEND_FILENAME = "hend_epithermal_020917.tab"
LP_SOURCE = SOURCES["lp-ns-hydrogenhd"]
MINIRF_SOURCE = SOURCES["lro-minirf-cpr-128ppd"]
HEND_SOURCE = SOURCES["mro-hend-epithermal-020917"]
LEND_PROBE = SOURCES["lro-lend-alds-20090915"]


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def repo_relative(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(ROOT).as_posix()
    except ValueError:
        return resolved.as_posix()


def coord_token(value: float) -> str:
    sign = "n" if value >= 0 else "s"
    fixed = f"{abs(value):.2f}".replace(".", "p")
    return f"{sign}{fixed}"


def lon_token(value: float) -> str:
    sign = "e" if value >= 0 else "w"
    fixed = f"{abs(value):.2f}".replace(".", "p")
    return f"{sign}{fixed}"


def ensure_file(url: str, path: Path, *, download: bool, expected_bytes: int | None = None) -> Path:
    if path.is_file():
        if expected_bytes is not None and path.stat().st_size != expected_bytes:
            raise WaterIceError(
                f"{path}: size {path.stat().st_size} does not match expected {expected_bytes}"
            )
        return path
    if not download:
        raise WaterIceError(
            f"missing cached source {path}. Re-run with --download (network) first."
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    # These tables are bounded (0.2-14 MB); a full read is capped below.
    request_data = _read_full(url)
    path.write_bytes(request_data)
    if expected_bytes is not None and len(request_data) != expected_bytes:
        raise WaterIceError(
            f"{url}: downloaded {len(request_data)} bytes, expected {expected_bytes}"
        )
    return path


def _read_full(url: str) -> bytes:
    """Read one bounded tabular product; the 20 MB cap is deliberate."""
    import urllib.request

    request = urllib.request.Request(
        url, headers={"User-Agent": "planetary-analog/0.1 (+d04-water-ice-evidence)"}
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        data = response.read()
    if len(data) > 20_000_000:
        raise WaterIceError("source file exceeds the bounded size limit")
    return data


def load_moon_references() -> list[dict[str, Any]]:
    references: list[dict[str, Any]] = []
    for path_text in sorted(glob.glob(str(ROOT / MOON_MANIFEST_GLOB))):
        path = Path(path_text)
        raw = json.loads(path.read_text(encoding="utf-8"))
        for reference in raw.get("references", []):
            if reference.get("body") != "Moon":
                continue
            references.append(
                {
                    "reference_id": reference["reference_id"],
                    "display_name": reference.get("display_name") or reference["reference_id"],
                    "latitude_deg": float(reference["lat"]),
                    "longitude_deg": float(reference["lon"]),
                    "dataset_id": reference["dataset_id"],
                    "manifest": repo_relative(path),
                }
            )
    if not references:
        raise WaterIceError("no Moon reference windows found")
    return references


def load_mars_references() -> list[dict[str, Any]]:
    path = ROOT / MARS_MANIFEST
    raw = json.loads(path.read_text(encoding="utf-8"))
    references: list[dict[str, Any]] = []
    for reference in raw.get("references", []):
        if reference.get("body") != "Mars":
            continue
        references.append(
            {
                "reference_id": reference["reference_id"],
                "display_name": reference.get("display_name") or reference["reference_id"],
                "latitude_deg": float(reference["lat"]),
                "longitude_deg": float(reference["lon"]),
                "dataset_id": reference["dataset_id"],
                "manifest": repo_relative(path),
            }
        )
    if not references:
        raise WaterIceError("no Mars reference windows found")
    return references


# ---------------------------------------------------------------------------
# Native-cell selection
# ---------------------------------------------------------------------------


def group_by_cell(
    references: Sequence[dict[str, Any]],
    cells: Sequence[LatLonCell],
) -> list[dict[str, Any]]:
    """Group reference windows by containing native cell; never duplicate a cell."""
    grouped: dict[tuple[float, float], dict[str, Any]] = {}
    for reference in references:
        cell = find_cell(cells, reference["latitude_deg"], reference["longitude_deg"])
        key = (cell.latitude_deg, cell.longitude_deg)
        entry = grouped.setdefault(
            key,
            {
                "cell": cell,
                "members": [],
            },
        )
        entry["members"].append(reference)
    return [
        grouped[key] for key in sorted(grouped, key=lambda item: (item[0], item[1]), reverse=True)
    ]


def unique_windows(
    references: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    grouped: dict[tuple[int, int], dict[str, Any]] = {}
    for reference in references:
        window = mini_rf_window(reference["latitude_deg"], reference["longitude_deg"])
        key = (window.line_first, window.sample_first)
        entry = grouped.setdefault(key, {"window": window, "members": []})
        entry["members"].append(reference)
    return [
        grouped[key] for key in sorted(grouped, key=lambda item: (item[0], item[1]), reverse=True)
    ]


# ---------------------------------------------------------------------------
# Artifact construction
# ---------------------------------------------------------------------------


def build_plan(
    *,
    lp_cells: Sequence[dict[str, Any]],
    radar_windows: Sequence[dict[str, Any]],
    hend_cell: dict[str, Any],
    lp_bytes: int,
    hend_bytes: int,
    generated_at: str,
) -> AcquisitionPlan:
    tasks: list[AcquisitionTask] = []
    for entry in lp_cells:
        cell = entry["cell"]
        area_id = lp_area_id(cell)
        tasks.append(
            AcquisitionTask(
                task_id=f"lp-h2-{area_id}",
                provider_id="pds-geosciences",
                source_id=LP_SOURCE.dataset_id,
                body="Moon",
                dimension="D04",
                area=PlanArea(
                    area_id=area_id,
                    display_name="LP NS hydrogen cell",
                    body="Moon",
                    bbox=cell.bbox,
                    crs=LP_SOURCE.native_crs,
                    coordinate_frame="lunar_planetocentric",
                    notes=(
                        "Native 0.5 x 0.5 degree Lunar Prospector hydrogen cell; "
                        "no resampling and no Earth frame."
                    ),
                ),
                endpoint=LP_SOURCE.url,
                parameters={
                    "cell_latitude_deg": f"{cell.latitude_deg:.2f}",
                    "cell_longitude_deg": f"{cell.longitude_deg:.2f}",
                    "native_resolution_deg": "0.5",
                },
                method_version="lp-ns-hydrogenhd-1.0.0",
                dataset_id=LP_SOURCE.dataset_id,
                expected_bytes=lp_bytes,
                output_suffix=".txt",
            )
        )
    for entry in radar_windows:
        window: MiniRfWindow = entry["window"]
        area_id = radar_area_id(window)
        tasks.append(
            AcquisitionTask(
                task_id=f"minirf-{area_id}",
                provider_id="pds-geosciences",
                source_id=MINIRF_SOURCE.dataset_id,
                body="Moon",
                dimension="D04",
                area=PlanArea(
                    area_id=area_id,
                    display_name="Mini-RF CPR native window",
                    body="Moon",
                    bbox=window.window_bbox(),
                    crs=MINIRF_SOURCE.native_crs,
                    coordinate_frame="lunar_planetocentric",
                    notes=(
                        "8 x 8 native 236.9 m pixels read from the global mosaic by "
                        "HTTP byte range; no resampling."
                    ),
                ),
                endpoint=MINIRF_SOURCE.url,
                parameters={
                    "line_first": str(window.line_first),
                    "sample_first": str(window.sample_first),
                    "pixels": str(window.lines),
                    "byte_offset": str(window.byte_offset),
                    "byte_length": str(window.byte_length),
                },
                method_version="minirf-cpr-window-1.0.0",
                dataset_id=MINIRF_SOURCE.dataset_id,
                expected_bytes=window.byte_length,
                output_suffix=".bin",
            )
        )
    cell = hend_cell["cell"]
    tasks.append(
        AcquisitionTask(
            task_id=f"hend-{hend_area_id(cell)}",
            provider_id="pds-geosciences",
            source_id=HEND_SOURCE.dataset_id,
            body="Mars",
            dimension="D04",
            area=PlanArea(
                area_id=hend_area_id(cell),
                display_name="HEND 4 degree cell",
                body="Mars",
                bbox=cell.bbox,
                crs=HEND_SOURCE.native_crs,
                coordinate_frame="mars_planetocentric",
                notes=(
                    "Native 4 x 4 degree HEND cell containing the five Jezero CTX "
                    "reference windows; no Earth frame."
                ),
            ),
            endpoint=HEND_SOURCE.url,
            parameters={
                "cell_latitude_deg": f"{cell.latitude_deg:.2f}",
                "cell_longitude_deg": f"{cell.longitude_deg:.2f}",
                "native_resolution_deg": "4",
            },
            method_version="hend-epithermal-1.0.0",
            dataset_id=HEND_SOURCE.dataset_id,
            expected_bytes=hend_bytes,
            output_suffix=".tab",
        )
    )
    return AcquisitionPlan(
        scope=SCOPE,
        generated_at=generated_at,
        tasks=tasks,
        totals={
            "tasks": len(tasks),
            "by_body": dict(sorted(Counter(task.body for task in tasks).items())),
            "by_dimension": {"D04": len(tasks)},
            "expected_bytes": sum(task.expected_bytes or 0 for task in tasks),
        },
        notes=[
            "Bounded pilot at the existing Moon/Mars terrain reference windows only.",
            "Native source grids are preserved; nothing is resampled to 90 m.",
            "Neutron-derived hydrogen and radar CPR are evidence, not ice detections.",
            "Raw products and window bytes are not committed.",
        ],
    )


def lp_area_id(cell: LatLonCell) -> str:
    return f"moon-lp-h2-cell-{coord_token(cell.latitude_deg)}-{lon_token(cell.longitude_deg)}"


def radar_area_id(window: MiniRfWindow) -> str:
    return f"moon-minirf-cpr-l{window.line_first:05d}-s{window.sample_first:05d}"


def hend_area_id(cell: LatLonCell) -> str:
    return f"mars-hend-cell-{coord_token(cell.latitude_deg)}-{lon_token(cell.longitude_deg)}"


def build_observations(
    *,
    lp_cells: Sequence[dict[str, Any]],
    radar_windows: Sequence[dict[str, Any]],
    hend_cell: dict[str, Any],
    radar_payloads: dict[tuple[int, int], tuple[bytes, bool]],
    lp_asset_id: str,
    hend_asset_id: str,
    radar_asset_ids: dict[tuple[int, int], str],
) -> list[TypedObservation]:
    observations: list[TypedObservation] = []
    for entry in lp_cells:
        cell: LatLonCell = entry["cell"]
        members = entry["members"]
        area_id = lp_area_id(cell)
        manifest = members[0]["manifest"]
        member_ids = [member["reference_id"] for member in members]
        if cell.value is None:
            continue
        observations.append(
            build_water_equivalent_hydrogen_observation(
                area_id=area_id,
                cell=cell,
                source=LP_SOURCE,
                source_asset_id=lp_asset_id,
                member_reference_ids=member_ids,
                source_manifest=manifest,
            )
        )
        claim = hydrogen_ice_claim(LP_SOURCE)
        observations.append(
            build_detection_claim_observation(
                area_id=area_id,
                body="Moon",
                claim=claim,
                source=LP_SOURCE,
                source_asset_id=lp_asset_id,
                member_reference_ids=member_ids,
                source_manifest=manifest,
                bbox=cell.bbox,
                valid_fraction=1.0,
                support_notes=(
                    "Claim is evaluated at the native 0.5 degree cell, which contains "
                    "each member 1.98 km window but cannot resolve it."
                ),
                extra_provenance={"hydrogen_ppm_by_weight": cell.value},
            )
        )
    for entry in radar_windows:
        window: MiniRfWindow = entry["window"]
        members = entry["members"]
        area_id = radar_area_id(window)
        manifest = members[0]["manifest"]
        member_ids = [member["reference_id"] for member in members]
        data, _cached = radar_payloads[(window.line_first, window.sample_first)]
        values = decode_mini_rf_window(data, window)
        summary = summarize_window(values)
        claim = radar_ice_claim(summary)
        if summary.valid_count:
            observations.append(
                build_radar_cpr_observation(
                    area_id=area_id,
                    window=window,
                    summary=summary,
                    source=MINIRF_SOURCE,
                    source_asset_id=radar_asset_ids[(window.line_first, window.sample_first)],
                    member_reference_ids=member_ids,
                    source_manifest=manifest,
                )
            )
        observations.append(
            build_detection_claim_observation(
                area_id=area_id,
                body="Moon",
                claim=claim,
                source=MINIRF_SOURCE,
                source_asset_id=radar_asset_ids[(window.line_first, window.sample_first)],
                member_reference_ids=member_ids,
                source_manifest=manifest,
                bbox=window.window_bbox(),
                valid_fraction=summary.valid_fraction,
                support_notes=(
                    f"Claim is evaluated over {window.lines} x {window.samples} native "
                    f"236.9 m pixels; valid fraction {summary.valid_fraction:.3f}."
                ),
                extra_provenance={
                    "valid_pixels": summary.valid_count,
                    "pixel_count": summary.pixel_count,
                    "maximum_cpr": summary.maximum,
                },
            )
        )
    cell = hend_cell["cell"]
    members = hend_cell["members"]
    area_id = hend_area_id(cell)
    manifest = members[0]["manifest"]
    member_ids = [member["reference_id"] for member in members]
    observations.append(
        build_neutron_flux_observation(
            area_id=area_id,
            cell=cell,
            source=HEND_SOURCE,
            source_asset_id=hend_asset_id,
            member_reference_ids=member_ids,
            source_manifest=manifest,
        )
    )
    observations.append(
        build_detection_claim_observation(
            area_id=area_id,
            body="Mars",
            claim=hydrogen_ice_claim(HEND_SOURCE),
            source=HEND_SOURCE,
            source_asset_id=hend_asset_id,
            member_reference_ids=member_ids,
            source_manifest=manifest,
            bbox=cell.bbox,
            valid_fraction=1.0,
            support_notes=(
                "Claim is evaluated at the native 4 x 4 degree cell, which contains "
                "all five 1.98 km Jezero windows but cannot resolve them."
            ),
            extra_provenance={"normalized_flux": cell.value},
        )
    )
    return observations


def build_sources_matrix() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for source in SOURCES.values():
        rows.append(
            {
                "dataset_id": source.dataset_id,
                "body": source.body,
                "provider": source.provider,
                "product_id": source.product_id,
                "title": source.title,
                "version": source.version,
                "url": source.url,
                "label_url": source.label_url,
                "license": source.license,
                "license_url": source.license_url,
                "verification_status": source.verification_status,
                "quantity_class": source.quantity_class,
                "units": source.units,
                "native_resolution": source.native_resolution,
                "native_resolution_m": source.native_resolution_m,
                "native_crs": source.native_crs,
                "surface_or_subsurface": source.surface_or_subsurface,
                "depth_sensitivity": source.depth_sensitivity,
                "temporal_support": source.temporal_support,
                "uncertainty": source.uncertainty,
                "nodata": source.nodata,
                "citation": source.citation,
                "notes": source.notes,
                "blockers": list(source.blockers),
                "used_in_artifact": source.dataset_id
                in {
                    LP_SOURCE.dataset_id,
                    MINIRF_SOURCE.dataset_id,
                    HEND_SOURCE.dataset_id,
                },
            }
        )
    for candidate in CANDIDATE_SOURCES:
        rows.append(
            {
                "dataset_id": candidate["dataset_id"],
                "body": candidate["body"],
                "provider": candidate["provider"],
                "product_id": candidate["product_id"],
                "title": "",
                "version": "unknown",
                "url": candidate["url"],
                "label_url": None,
                "license": "unknown",
                "license_url": None,
                "verification_status": candidate["verification_status"],
                "quantity_class": "candidate",
                "units": "unknown",
                "native_resolution": "unknown",
                "native_resolution_m": None,
                "native_crs": "unknown",
                "surface_or_subsurface": "unknown",
                "depth_sensitivity": "unknown",
                "temporal_support": "unknown",
                "uncertainty": "unknown",
                "nodata": "unknown",
                "citation": "",
                "notes": candidate["access"],
                "blockers": [candidate["blocker"]],
                "used_in_artifact": False,
            }
        )
    return rows


def counts_for(observations: Sequence[TypedObservation], plan: AcquisitionPlan) -> dict[str, Any]:
    by_body = Counter(observation.body for observation in observations)
    by_feature = Counter(observation.feature_id for observation in observations)
    by_state = Counter(observation.state for observation in observations)
    claims = [
        observation
        for observation in observations
        if observation.feature_id == FEATURE_WATER_ICE_DETECTION
    ]
    return {
        "tasks_planned": len(plan.tasks),
        "tasks_succeeded": len(plan.tasks),
        "tasks_failed": 0,
        "observations": len(observations),
        "areas": len({observation.area_id for observation in observations}),
        "by_body": dict(sorted(by_body.items())),
        "by_feature": dict(sorted(by_feature.items())),
        "by_state": dict(sorted(by_state.items())),
        "claims": len(claims),
        "claims_by_state": dict(sorted(Counter(item.state for item in claims).items())),
        "no_coverage_claims": sum(1 for item in claims if item.state == "no_coverage"),
        "detected_claims": sum(1 for item in claims if item.state == "detected"),
    }


def build_assets(
    *,
    lp_path: Path,
    hend_path: Path,
    radar_payloads: dict[tuple[int, int], tuple[bytes, bool]],
    radar_windows: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    assets: list[dict[str, Any]] = [
        {
            "asset_id": f"{LP_SOURCE.dataset_id}:source",
            "dataset_id": LP_SOURCE.dataset_id,
            "uri": LP_SOURCE.url,
            "checksum_algorithm": "sha256",
            "checksum": file_sha256(lp_path),
            "checksum_scope": "source",
            "bytes": lp_path.stat().st_size,
            "fetched_at": datetime.fromtimestamp(lp_path.stat().st_mtime, UTC).isoformat(),
            "redistribution_allowed": "yes",
            "native_crs": LP_SOURCE.native_crs,
            "native_units": "ppm H by weight",
            "nodata": None,
            "purpose": "Lunar Prospector half-degree hydrogen abundance table.",
            "local_cache_path": repo_relative(lp_path),
        },
        {
            "asset_id": f"{HEND_SOURCE.dataset_id}:source",
            "dataset_id": HEND_SOURCE.dataset_id,
            "uri": HEND_SOURCE.url,
            "checksum_algorithm": "sha256",
            "checksum": file_sha256(hend_path),
            "checksum_scope": "source",
            "bytes": hend_path.stat().st_size,
            "fetched_at": datetime.fromtimestamp(hend_path.stat().st_mtime, UTC).isoformat(),
            "redistribution_allowed": "yes",
            "native_crs": HEND_SOURCE.native_crs,
            "native_units": "normalized epithermal neutron flux",
            "nodata": None,
            "purpose": "Mars Odyssey HEND 4 x 4 degree epithermal neutron flux table.",
            "local_cache_path": repo_relative(hend_path),
        },
    ]
    for entry in radar_windows:
        window: MiniRfWindow = entry["window"]
        data, _cached = radar_payloads[(window.line_first, window.sample_first)]
        assets.append(
            {
                "asset_id": radar_area_id(window),
                "dataset_id": MINIRF_SOURCE.dataset_id,
                "uri": MINIRF_SOURCE.url,
                "checksum_algorithm": "sha256",
                "checksum": sha256_bytes(data),
                "checksum_scope": "window",
                "bytes": len(data),
                "byte_offset": window.byte_offset,
                "byte_length": window.byte_length,
                "native_crs": MINIRF_SOURCE.native_crs,
                "native_units": "circular polarization ratio",
                "scale": 1.0,
                "offset": 0.0,
                "nodata": -1.7976931e308,
                "redistribution_allowed": "yes",
                "purpose": (
                    f"8x8 native 236.9 m CPR window at line {window.line_first}, "
                    f"sample {window.sample_first}; read by HTTP byte range."
                ),
                "local_cache_path": repo_relative(
                    ROOT
                    / DEFAULT_CACHE
                    / f"minirf-cpr-{window.line_first:05d}-{window.sample_first:05d}.bin"
                ),
            }
        )
    return assets


def build_manifest(
    *,
    generated_at: str,
    plan: AcquisitionPlan,
    counts: dict[str, Any],
    plan_sha256: str,
    observations_sha256: str,
    assets_sha256: str,
    input_manifests: Sequence[dict[str, Any]],
    access_tests: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "release_version": RELEASE_VERSION,
        "generated_at": generated_at,
        "scope": SCOPE,
        "data_kind": "observed",
        "method_version": "water-ice-evidence-0.1.0",
        "counts": counts,
        "plan_sha256": plan_sha256,
        "observations_sha256": observations_sha256,
        "assets_sha256": assets_sha256,
        "input_manifests": list(input_manifests),
        "products": [
            {
                "dataset_id": source.dataset_id,
                "product_id": source.product_id,
                "url": source.url,
                "license": source.license,
                "license_url": source.license_url,
                "verification_status": source.verification_status,
            }
            for source in (LP_SOURCE, MINIRF_SOURCE, HEND_SOURCE)
        ],
        "access_tests": list(access_tests),
        "limitations": [
            "Two neutron products and one radar mosaic only; this is not global or exhaustive Moon/Mars coverage.",
            "Native source grids are preserved: 0.5 degree (Moon hydrogen), 236.9 m (Mini-RF), 4 degree (Mars neutron). Nothing is resampled to 90 m.",
            "The Lunar Prospector product is a PDS special product that the PDS page describes as not a peer-reviewed archive; it is bounded to the 1998-1999 low-altitude mission phase.",
            "The Mars HEND label has no start/stop dates; the 55-day mapping interval is documented in the product description, not the label.",
            "Neutron-derived water-equivalent hydrogen and normalized epithermal flux are hydrogen evidence, not ice fraction, ice phase, or mineable ice.",
            "Mini-RF CPR at or above unity is ice-consistent but not diagnostic; roughness and wavelength-scale rocks also raise CPR.",
            "All 23 existing reference windows are coarser than the native source cells for the neutron products, so no AOI-scale ice detection is claimed.",
            "No D04 similarity feature, D04 score, or cross-body water score is produced.",
            "Raw products and window bytes remain in the gitignored var/cache/water-44 cache and are not committed.",
        ],
        "non_claims": [
            "No ice fraction, ice volume, or mineable-ice estimate.",
            "No water-equivalent hydrogen for Mars (the HEND map is a normalized flux, not WEH).",
            "No detection claim at 1.98 km support from 0.5 or 4 degree neutron cells.",
            "No hydrate or mineral abundance (D02 owns hydrated-mineral evidence).",
            "No liquid-water or permafrost observation.",
            "No global coverage and no claim that an unmeasured area is ice-free.",
        ],
        "reproduce_commands": [
            "uv run python scripts/build_water_ice_pilot.py --download",
            "uv run python scripts/build_water_ice_pilot.py",
            "uv run python scripts/build_water_ice_pilot.py --check",
            "uv run pytest pipelines/tests/test_water_ice_pilot.py -q",
            "uv run ruff check .",
            "uv run ruff format --check .",
        ],
        "notes": [
            "Counts are measured from the committed observations.jsonl, not estimated.",
            "Every native read is recorded in plan.json and every window checksum in assets.json.",
            "Source and candidate statuses are recorded in sources.json.",
        ],
    }


def access_tests() -> list[dict[str, Any]]:
    """Static record of the access/format tests performed while verifying sources."""
    return [
        {
            "dataset_id": LP_SOURCE.dataset_id,
            "test": "HTTPS GET of hydrogenhd.txt and header/column inspection",
            "result": "accessible",
            "observed": "14,256,401 bytes; 259,200 comma-separated rows; one ********** missing cell",
            "blocker": "",
        },
        {
            "dataset_id": MINIRF_SOURCE.dataset_id,
            "test": "HTTPS HEAD + HTTP Range GET of the 4.25 GB global CPR mosaic",
            "result": "accessible",
            "observed": "accept-ranges: bytes; HTTP 206; 8x8 native windows decoded as little-endian float32",
            "blocker": "",
        },
        {
            "dataset_id": HEND_SOURCE.dataset_id,
            "test": "HTTPS GET of hend_epithermal_020917.tab and PDS4 label",
            "result": "accessible",
            "observed": "202,500 bytes; 4,050 rows on a 4x4 degree grid; normalized flux 0.158515-1.0",
            "blocker": "",
        },
        {
            "dataset_id": LEND_PROBE.dataset_id,
            "test": "HTTPS GET and PDS4 label/offset inspection of the LEND ALDS table",
            "result": "accessible_but_not_used",
            "observed": "4,464,000 bytes; 14,400 records of 310 bytes; CSETN rates parse",
            "blocker": LEND_PROBE.blockers[0],
        },
        {
            "dataset_id": "lro-lamp-far-uv",
            "test": "official instrument site reachability",
            "result": "identified",
            "observed": "instrument site reachable; no gridded PDS water-ice product ID verified",
            "blocker": CANDIDATE_SOURCES[0]["blocker"],
        },
        {
            "dataset_id": "lcross-cabeus-impact",
            "test": "PDS LCROSS mission page reachability",
            "result": "identified",
            "observed": "page reachable; single-impact published result at Cabeus, not an areal product",
            "blocker": CANDIDATE_SOURCES[1]["blocker"],
        },
        {
            "dataset_id": "mro-sharad-radargrams",
            "test": "PDS4 SHARAD radargram and clutter-simulation bundle listing",
            "result": "accessible",
            "observed": "urn:nasa:pds:mro_sharad_us_radargrams and mro_sharad_simulations bundles reachable",
            "blocker": CANDIDATE_SOURCES[2]["blocker"],
        },
        {
            "dataset_id": "mex-marsis-radargrams",
            "test": "PDS Mars Express MARSIS mission page reachability",
            "result": "identified",
            "observed": "page reachable; no bounded per-cell product selected",
            "blocker": CANDIDATE_SOURCES[3]["blocker"],
        },
        {
            "dataset_id": "mro-crism-hydration",
            "test": "PDS4 CRISM TRDR/MRDR/MTRDR volume listing",
            "result": "accessible",
            "observed": "MROCR_3201 / MROCR_4001 volumes reachable; cubes and multispectral tiles, not a hydration mask",
            "blocker": CANDIDATE_SOURCES[4]["blocker"],
        },
    ]


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------


def write_report(
    output: Path, manifest: dict[str, Any], observations: Sequence[TypedObservation]
) -> None:
    counts = manifest["counts"]
    lp_rows = [
        observation
        for observation in observations
        if observation.feature_id == FEATURE_WATER_EQUIVALENT_HYDROGEN
    ]
    radar_rows = [
        observation for observation in observations if observation.feature_id == FEATURE_RADAR_CPR
    ]
    hend_rows = [
        observation
        for observation in observations
        if observation.feature_id == FEATURE_EPITHERMAL_NEUTRON_FLUX
    ]
    claim_by_area = {
        observation.area_id: observation
        for observation in observations
        if observation.feature_id == FEATURE_WATER_ICE_DETECTION
    }

    def claim_state(area_id: str) -> str:
        claim = claim_by_area.get(area_id)
        return claim.state if claim is not None else "none"

    lines = [
        "# D04 Moon/Mars water/ice evidence pilot — report",
        "",
        f"- Release: `{RELEASE_VERSION}`",
        "- Method: `water-ice-evidence-0.1.0`",
        f"- Tasks attempted/succeeded/failed: {counts['tasks_planned']}/"
        f"{counts['tasks_succeeded']}/{counts['tasks_failed']}",
        f"- Observations: {counts['observations']}",
        f"- Native areas: {counts['areas']}",
        f"- By body: {counts['by_body']}",
        f"- By feature: {counts['by_feature']}",
        f"- By state: {counts['by_state']}",
        f"- Detection claims by state: {counts['claims_by_state']}",
        f"- No-coverage claims: {counts['no_coverage_claims']}",
        f"- Detected ice claims: {counts['detected_claims']}",
        "",
        "## Moon — Lunar Prospector water-equivalent hydrogen",
        "",
        "| Native cell | Members | H (ppm wt) | WEH (wt%) | Ice claim |",
        "|---|---|---:|---:|---|",
    ]
    for observation in sorted(lp_rows, key=lambda item: item.area_id):
        provenance = observation.legacy_provenance or {}
        hydrogen_ppm = float(provenance.get("hydrogen_ppm_by_weight", float("nan")))
        members = provenance.get("member_reference_ids", [])
        lines.append(
            f"| `{observation.area_id}` | {len(members)} | {hydrogen_ppm:.2f} | "
            f"{float(observation.value):.4f} | `{claim_state(observation.area_id)}` |"
        )
    lines += [
        "",
        "## Moon — Mini-RF circular polarization ratio (native 236.9 m pixels)",
        "",
        "| Native window | Members | Valid px | Min | Median | Max | CPR>=1 px | Ice claim |",
        "|---|---|---:|---:|---:|---:|---:|---|",
    ]
    for observation in sorted(radar_rows, key=lambda item: item.area_id):
        provenance = observation.legacy_provenance or {}
        uncertainty = observation.uncertainty
        lines.append(
            f"| `{observation.area_id}` | {len(provenance.get('member_reference_ids', []))} | "
            f"{provenance.get('valid_pixels')}/{provenance.get('pixels')} | "
            f"{float(uncertainty.lower):.3f} | {float(observation.value):.3f} | "
            f"{float(uncertainty.upper):.3f} | {provenance.get('count_at_or_above_one')} | "
            f"`{claim_state(observation.area_id)}` |"
        )
    lines += [
        "",
        "## Mars — HEND normalized epithermal neutron flux",
        "",
        "| Native cell | Members | Normalized flux | Ice claim |",
        "|---|---|---:|---|",
    ]
    for observation in sorted(hend_rows, key=lambda item: item.area_id):
        members = (observation.legacy_provenance or {}).get("member_reference_ids", [])
        lines.append(
            f"| `{observation.area_id}` | {len(members)} | {float(observation.value):.4f} | "
            f"`{claim_state(observation.area_id)}` |"
        )
    lines += [
        "",
        "Every numeric observation is `evidence_only`. No ice detection, ice fraction, "
        "mineable-ice volume, or D04 similarity score is produced. The only `detected` "
        "state in this artifact is a measurement of a physical quantity at its native "
        "support; `water_ice_detection` claims are `unknown` or `no_coverage`.",
        "",
    ]
    (output / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")


def write_readme(output: Path, manifest: dict[str, Any]) -> None:
    counts = manifest["counts"]
    text = f"""# D04 Moon/Mars — water/ice evidence pilot

Bounded, checksummed, **evidence-only** pilot built from three official public PDS
products over the existing Moon/Mars terrain reference windows (Issue #44).

## Actual content

| Item | Value |
|---|---:|
| Tasks planned / succeeded / failed | {counts["tasks_planned"]} / {counts["tasks_succeeded"]} / {counts["tasks_failed"]} |
| Native areas | {counts["areas"]} |
| Observations | {counts["observations"]} |
| By body | {counts["by_body"]} |
| By feature | {counts["by_feature"]} |
| By state | {counts["by_state"]} |
| Detection claims | {counts["claims"]} ({counts["claims_by_state"]}) |
| No-coverage claims | {counts["no_coverage_claims"]} |
| Detected ice claims | {counts["detected_claims"]} |

## Products

| Body | Product | Native support | Quantity |
|---|---|---|---|
| Moon | Lunar Prospector NS `hydrogenhd` | 0.5° x 0.5° | H ppm by weight, converted to water-equivalent H wt% |
| Moon | Mini-RF `GLOBAL_CPR_128PPD_SIMP_0C` | 236.9 m | circular polarization ratio |
| Mars | Odyssey HEND `hend_epithermal_020917` | 4° x 4° | normalized epithermal neutron flux |

## Scientific limits

- Neutron-derived hydrogen is not ice: phase, ice fraction and mineable ice are
  never inferred. Mars HEND is a normalized flux, not water-equivalent hydrogen.
- Mini-RF CPR at or above unity is ice-consistent but not diagnostic; the mosaic
  mixes multiple acquisitions and has no depth retrieval.
- The neutron cells (0.5° Moon, 4° Mars) are far coarser than the 1.98 km reference
  windows. The pilot therefore reports `unknown` claims for neutron products and
  `no_coverage`/`not_detected`/`unknown` for radar, never an AOI-scale detection.
- Native grids are preserved; nothing is resampled to 90 m or to Earth WGS84.
- No D04 similarity feature, D04 score, or cross-body water score exists.

## Files

- `plan.json` — bounded native-read plan (30 tasks) with exact URLs/parameters.
- `observations.jsonl` — typed-v2 D04 observations (numeric evidence + claims).
- `assets.json` — source and window SHA-256 provenance; bytes are **not** committed.
- `sources.json` — body x product matrix with verified/candidate status and blockers.
- `manifest.json` — counts, checksums, limitations, non-claims, reproduction commands.
- `REPORT.md` — compact per-native-cell results.

## Reproduce

```bash
uv run python scripts/build_water_ice_pilot.py --download
uv run python scripts/build_water_ice_pilot.py
uv run python scripts/build_water_ice_pilot.py --check
```

The first command downloads the two small ASCII tables and the 17 bounded Mini-RF
windows into the gitignored `var/cache/water-44` cache. Full details are in
`docs/D04_WATER_ICE_PILOT.md`.
"""
    (output / "README.md").write_text(text, encoding="utf-8")


def write_doc(
    doc: Path,
    *,
    manifest: dict[str, Any],
    observations: Sequence[TypedObservation],
    sources: Sequence[dict[str, Any]],
) -> None:
    counts = manifest["counts"]
    lines = [
        "# D04 water & ice evidence pilot (Moon/Mars)",
        "",
        "Status: **bounded evidence-only pilot; no ice detection and no D04 score**.",
        "Implements the source-verification and typed-evidence part of Issue #44 on top",
        "of the existing Moon/Mars terrain reference windows.",
        "",
        "## Why this is not an ice inventory",
        "",
        "| Quantity | Source in this pilot | What it is | What it is not |",
        "|---|---|---|---|",
        "| Water-equivalent hydrogen | Lunar Prospector NS `hydrogenhd` (Moon) | H ppm by weight from corrected epithermal neutrons, x9 for H2O equivalence | ice fraction, ice phase, mineable ice |",
        "| Normalized epithermal flux | Odyssey HEND `hend_epithermal_020917` (Mars) | dimensionless hydrogen proxy | water-equivalent hydrogen or ice abundance |",
        "| Circular polarization ratio | Mini-RF global CPR mosaic (Moon) | near-surface radar scattering ratio | ice detection, depth, or abundance |",
        "",
        "## Measured result",
        "",
        f"- Tasks attempted/succeeded/failed: {counts['tasks_planned']}/{counts['tasks_succeeded']}/{counts['tasks_failed']}.",
        f"- Observations: {counts['observations']} over {counts['areas']} native areas.",
        f"- By body: `{counts['by_body']}`.",
        f"- By feature: `{counts['by_feature']}`.",
        f"- By state: `{counts['by_state']}`.",
        f"- Detection claims: `{counts['claims_by_state']}`; detected ice claims: {counts['detected_claims']}.",
        "- All 23 existing reference windows are covered by at least one native source",
        "  cell, but no neutron cell can resolve a 1.98 km window, so no AOI-scale ice",
        "  detection is asserted.",
        "",
        "## State semantics",
        "",
        "| State | Meaning in this pilot |",
        "|---|---|",
        "| `detected` | A physical quantity was measured at the observation's own native support. |",
        "| `not_detected` | Valid source pixels exist at adequate support and the stated criterion is not met (e.g. all CPR < 1). |",
        "| `no_coverage` | No valid source pixel exists in the window (mosaic gap). Not evidence of absence. |",
        "| `unknown` | Data exist but cannot answer the question: coarse cell, hydrogen proxy, or partial coverage. |",
        "| `not_applicable` | The quantity is undefined for the body or support (e.g. snow depth on the Moon). |",
        "",
        "Physical zero is a `detected` value of 0.0 and is never rewritten as missing;",
        "missing cells carry no value. Tests cover both, plus all five states.",
        "",
        "## Products and access",
        "",
        "| Dataset | Body | Product / version | Native support | Status |",
        "|---|---|---|---|---|",
    ]
    for row in sources:
        if row["used_in_artifact"]:
            lines.append(
                f"| `{row['dataset_id']}` | {row['body']} | {row['product_id']} | "
                f"{row['native_resolution']} | `{row['verification_status']}` |"
            )
    lines += [
        "",
        "Candidate and blocked sources (LAMP, LCROSS, SHARAD, MARSIS, CRISM, LEND ALD)",
        "are recorded with their exact blocker in `sources.json` and in the pilot",
        "`REPORT.md`. LEND ALD is accessible and parses, but its relative counting rates",
        "lack the normalization constants needed for flux or water-equivalent hydrogen.",
        "",
        "## Commands",
        "",
        "```bash",
        "uv run python scripts/build_water_ice_pilot.py --download",
        "uv run python scripts/build_water_ice_pilot.py",
        "uv run python scripts/build_water_ice_pilot.py --check",
        "uv run pytest pipelines/tests/test_water_ice_pilot.py -q",
        "uv run ruff check .",
        "uv run ruff format --check .",
        "```",
        "",
        "## Non-claims",
        "",
        "- No ice fraction, subsurface ice volume, or mineable-ice estimate.",
        "- No water-equivalent hydrogen for Mars.",
        "- No D02 hydrated-mineral duplication; CRISM hydration stays D02 evidence.",
        "- No global coverage and no statement that an unmeasured area is ice-free.",
        "- No D04 similarity feature or scalar blanket water score.",
        "",
    ]
    doc.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def build(
    *,
    download: bool,
    cache_dir: Path,
    output: Path,
    doc: Path,
    generated_at: str,
) -> dict[str, Any]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    doc.parent.mkdir(parents=True, exist_ok=True)

    moon_refs = load_moon_references()
    mars_refs = load_mars_references()

    lp_path = ensure_file(LP_SOURCE.url, cache_dir / LP_FILENAME, download=download)
    hend_path = ensure_file(HEND_SOURCE.url, cache_dir / HEND_FILENAME, download=download)
    lp_cells = read_lp_hydrogen_ascii(lp_path)
    hend_cells = read_hend_epithermal_ascii(hend_path)

    lp_groups = group_by_cell(moon_refs, lp_cells)
    radar_window_groups = unique_windows(moon_refs)
    hend_group = group_by_cell(mars_refs, hend_cells)[0]

    radar_cache = RangeCache(cache_dir)
    range_reader = HttpRangeReader(MINIRF_SOURCE.url)
    radar_payloads: dict[tuple[int, int], tuple[bytes, bool]] = {}
    for entry in radar_window_groups:
        window: MiniRfWindow = entry["window"]
        key = f"minirf-cpr-{window.line_first:05d}-{window.sample_first:05d}"
        start = window.byte_offset
        end = start + window.byte_length - 1
        if not download and not (cache_dir / f"{key}.bin").is_file():
            raise WaterIceError(
                f"missing cached window {key}. Re-run with --download (network) first."
            )
        data, cached = radar_cache.fetch(range_reader, key=key, start=start, end_inclusive=end)
        radar_payloads[(window.line_first, window.sample_first)] = (data, cached)

    plan = build_plan(
        lp_cells=lp_groups,
        radar_windows=radar_window_groups,
        hend_cell=hend_group,
        lp_bytes=lp_path.stat().st_size,
        hend_bytes=hend_path.stat().st_size,
        generated_at=generated_at,
    )
    write_json(output / "plan.json", plan.model_dump(mode="json"))
    plan_sha256 = file_sha256(output / "plan.json")

    radar_asset_ids = {
        (entry["window"].line_first, entry["window"].sample_first): radar_area_id(entry["window"])
        for entry in radar_window_groups
    }
    observations = build_observations(
        lp_cells=lp_groups,
        radar_windows=radar_window_groups,
        hend_cell=hend_group,
        radar_payloads=radar_payloads,
        lp_asset_id=f"{LP_SOURCE.dataset_id}:source",
        hend_asset_id=f"{HEND_SOURCE.dataset_id}:source",
        radar_asset_ids=radar_asset_ids,
    )
    errors: list[str] = []
    for observation in observations:
        errors.extend(
            f"{observation.observation_id}: {error}"
            for error in validate_water_ice_observation(observation)
        )
    if errors:
        raise WaterIceError("invalid D04 observations:\n" + "\n".join(errors))

    observations_sha256 = write_observations_jsonl(output / "observations.jsonl", observations)

    assets = build_assets(
        lp_path=lp_path,
        hend_path=hend_path,
        radar_payloads=radar_payloads,
        radar_windows=radar_window_groups,
    )
    write_json(output / "assets.json", assets)
    assets_sha256 = file_sha256(output / "assets.json")

    sources = build_sources_matrix()
    write_json(output / "sources.json", sources)

    input_manifests = [
        {
            "path": item["manifest"],
            "sha256": file_sha256(ROOT / item["manifest"]),
        }
        for item in sorted(
            {reference["manifest"]: reference for reference in moon_refs + mars_refs}.values(),
            key=lambda reference: reference["manifest"],
        )
    ]
    counts = counts_for(observations, plan)
    manifest = build_manifest(
        generated_at=generated_at,
        plan=plan,
        counts=counts,
        plan_sha256=plan_sha256,
        observations_sha256=observations_sha256,
        assets_sha256=assets_sha256,
        input_manifests=input_manifests,
        access_tests=access_tests(),
    )
    write_json(output / "manifest.json", manifest)
    write_report(output, manifest, observations)
    write_readme(output, manifest)
    write_doc(doc, manifest=manifest, observations=observations, sources=sources)
    return manifest


def check(output: Path) -> int:
    manifest_path = output / "manifest.json"
    observations_path = output / "observations.jsonl"
    if not manifest_path.is_file() or not observations_path.is_file():
        print(f"missing artifact files under {output}", file=sys.stderr)
        return 2
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    actual = file_sha256(observations_path)
    expected = manifest.get("observations_sha256")
    if actual != expected:
        print(
            f"observations.jsonl checksum mismatch: {actual} != {expected}",
            file=sys.stderr,
        )
        return 1
    print(f"artifact verified: {observations_path} sha256={actual}")
    return 0


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download", action="store_true", help="fetch missing bounded sources")
    parser.add_argument("--cache", default=DEFAULT_CACHE)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--doc", default=DEFAULT_DOC)
    parser.add_argument(
        "--check", action="store_true", help="verify the committed artifact checksum"
    )
    parser.add_argument("--generated-at", default=None)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    output = ROOT / args.output
    if args.check:
        return check(output)
    generated_at = args.generated_at or datetime.now(UTC).isoformat()
    manifest = build(
        download=args.download,
        cache_dir=ROOT / args.cache,
        output=output,
        doc=ROOT / args.doc,
        generated_at=generated_at,
    )
    counts = manifest["counts"]
    print(
        f"wrote {counts['observations']} observations over {counts['areas']} native areas "
        f"to {repo_relative(output)}"
    )
    print(f"claims by state: {counts['claims_by_state']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
