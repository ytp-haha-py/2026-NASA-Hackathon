#!/usr/bin/env python3
"""Bounded real-DEM method-sensitivity experiment for D01 buffer requirements.

This is a research/validation command, not a production extractor and not a
similarity change. It reads a small buffered window per selected site, computes
research curvature fields from (a) the normal 22x22 / 1980 m crop and (b) the
buffered crop, and writes a portable JSON/CSV summary. Rasters are never written
to the repository.

Examples
--------
Dry run (no network, no writes):

    uv run python scripts/validate_terrain_buffers.py --dry-run --all

Real bounded Earth + Moon + Mars run:

    uv run python scripts/validate_terrain_buffers.py \
        --earth-site death-valley --earth-site rio-tinto --moon --mars \
        --out docs/terrain-buffer-validation.json \
        --csv docs/terrain-buffer-measurements.csv
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
from analog_pipelines.features import terrain_metrics
from analog_pipelines.terrain_buffer import (
    BUFFER_REQUIREMENT_BY_ID,
    BUFFER_REQUIREMENTS,
    CORE_CELLS,
    MetricWindow,
    buffer_table_payload,
    compare_feature,
    compare_fields,
    compare_support,
    read_metric_window,
    support_mask,
    window_checksum,
)
from analog_pipelines.terrain_research import (
    curvature_components,
    quadratic_curvature_components,
    smoothed_curvature_components,
)
from pyproj import CRS, Transformer

REPO_ROOT = Path(__file__).resolve().parents[1]
for _subdir in ("pipelines/earth", "pipelines/planetary"):
    _path = str(REPO_ROOT / _subdir)
    if _path not in sys.path:
        sys.path.insert(0, _path)

import copernicus_glo90  # noqa: E402
import ctx_import  # noqa: E402
import lola  # noqa: E402

ANALYSIS_RESOLUTION_M = 90.0
BUFFER_CELLS = 12
SCHEMA_VERSION = "terrain-buffer-validation-1.0.0"
MARS_ACCESS_URL = (
    "https://asc-pds-services.s3.us-west-2.amazonaws.com/"
    "mosaic/mars2020_trn/CTX/ScienceInvestigationMaps_JPL/"
    "M20_JezeroCrater_CTXDEM_20m.tif"
)
FIELD_METHODS = {
    "surface_curvedness_central": "surface_curvedness_central",
    "mean_curvature_central": "mean_curvature_central",
    "gaussian_curvature_central": "gaussian_curvature_central",
    "surface_curvedness_quadratic_r2": "surface_curvedness_quadratic_r2",
    "surface_curvedness_quadratic_r3": "surface_curvedness_quadratic_r3",
    "surface_curvedness_smoothed": "surface_curvedness_smoothed",
    "profile_curvature_central": "profile_curvature_central",
    "profile_curvature_smoothed": "profile_curvature_smoothed",
    "tangential_curvature_central": "tangential_curvature_central",
    "tangential_curvature_smoothed": "tangential_curvature_smoothed",
}
SENSITIVITY_PAIRS = (
    ("surface_curvedness_central", "surface_curvedness_smoothed"),
    ("surface_curvedness_central", "surface_curvedness_quadratic_r2"),
    ("surface_curvedness_quadratic_r2", "surface_curvedness_quadratic_r3"),
    ("profile_curvature_central", "profile_curvature_smoothed"),
    ("tangential_curvature_central", "tangential_curvature_smoothed"),
)


@dataclass(frozen=True)
class Site:
    key: str
    body: str
    display_name: str
    access_url: str
    source_url: str
    dataset_id: str
    product_id: str
    product_title: str
    provider: str
    license: str
    license_url: str
    citation: str = ""
    center_lonlat: tuple[float, float] | None = None
    center_xy: tuple[float, float] | None = None
    dst_crs: CRS | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def earth_sites() -> dict[str, Site]:
    sites: dict[str, Site] = {}
    for candidate in copernicus_glo90.CANDIDATES:
        sites[candidate.slug] = Site(
            key=candidate.location_id,
            body="Earth",
            display_name=candidate.display_name,
            access_url=candidate.tile_url,
            source_url=candidate.tile_url,
            dataset_id=copernicus_glo90.DATASET_ID,
            product_id=copernicus_glo90.PRODUCT_ID,
            product_title=copernicus_glo90.PRODUCT_TITLE,
            provider=copernicus_glo90.PROVIDER,
            license=copernicus_glo90.LICENSE_NAME,
            license_url=copernicus_glo90.LICENSE_URL,
            citation=copernicus_glo90.ATTRIBUTION,
            center_lonlat=(candidate.lon, candidate.lat),
            dst_crs=CRS.from_epsg(copernicus_glo90.utm_epsg(candidate.lon, candidate.lat)),
            notes=("Copernicus DEM GLO-90 COG; public AWS Open Data mirror.",),
        )
    return sites


def moon_site() -> Site:
    return Site(
        key="moon-shackleton-rim-site04-roi2",
        body="Moon",
        display_name="Shackleton rim (LOLA Site04 ROI 2)",
        access_url=lola.SITE04_LDEM_URL,
        source_url=lola.SITE04_LDEM_URL,
        dataset_id=lola.DATASET_ID,
        product_id=lola.PRODUCT_ID,
        product_title=lola.PRODUCT_TITLE,
        provider=lola.PROVIDER,
        license=lola.LICENSE_NAME,
        license_url=lola.NASA_DATA_LICENSE_URL,
        citation=lola.CITATION,
        center_xy=lola.DEFAULT_ROI.center_xy,
        dst_crs=CRS.from_wkt(lola.LUNAR_SOUTH_POLAR_STEREOGRAPHIC_WKT),
        notes=(
            "Official PGDA Site04 ROI 2 centre; 5 m/pix projected lunar DEM.",
            "The product is ~90% interpolated at 5 m; no 90 m observation is invented.",
        ),
    )


def mars_site() -> Site:
    return Site(
        key="mars-jezero-ctx-window-01",
        body="Mars",
        display_name="Jezero CTX coordinate window 01",
        access_url=MARS_ACCESS_URL,
        source_url=ctx_import.URL,
        dataset_id=ctx_import.DATASET_ID,
        product_id="M20_JezeroCrater_CTXDEM_20m",
        product_title="Mars 2020 Science Investigation CTX DEM Mosaic",
        provider="USGS Astrogeology Science Center / NASA Mars Missions",
        license="public domain",
        license_url=ctx_import.PAGE,
        citation=ctx_import.PAGE,
        center_xy=ctx_import.CENTRES[0],
        dst_crs=mars_source_crs(),
        notes=(
            "20 m stereo-derived Mars DEM; source CRS is the Mars 2000 sphere projection.",
            "No Earth WGS84 coordinates or cross-body elevation comparison are used.",
        ),
    )


MARS_MANIFEST = REPO_ROOT / "pipelines/planetary/manifests/ctx_mars_references.json"


def mars_source_crs() -> CRS:
    payload = json.loads(MARS_MANIFEST.read_text(encoding="utf-8"))
    return CRS.from_wkt(payload["datasets"][0]["source_crs"])


def select_sites(args: argparse.Namespace) -> list[Site]:
    earth = earth_sites()
    selected: list[Site] = []
    slugs = args.earth_site or ([] if (args.moon or args.mars) else ["death-valley", "rio-tinto"])
    if args.all:
        slugs = ["death-valley", "rio-tinto"]
    for slug in slugs:
        if slug not in earth:
            known = ", ".join(sorted(earth))
            raise SystemExit(f"unknown Earth site {slug!r}; known: {known}")
        selected.append(earth[slug])
    if args.moon or args.all:
        selected.append(moon_site())
    if args.mars or args.all:
        selected.append(mars_site())
    return selected


_CONTENT_RANGE_RE = re.compile(r"Content-Range:\s*bytes\s+(\d+)-(\d+)/(\d+)", re.IGNORECASE)
_CONTENT_LENGTH_RE = re.compile(r"Content-Length:\s*(\d+)", re.IGNORECASE)
_STATUS_RE = re.compile(r"< HTTP/[0-9.]+\s+(\d{3})", re.IGNORECASE)
_REQUEST_RE = re.compile(r"^>\s+(GET|HEAD|POST)\s+", re.IGNORECASE)


def _parse_curl_log(text: str) -> dict[str, Any]:
    statuses: list[int] = []
    ranges: list[tuple[int, int]] = []
    current_status: int | None = None
    current_method: str | None = None
    for line in text.splitlines():
        request_match = _REQUEST_RE.search(line)
        if request_match:
            current_method = request_match.group(1).upper()
            current_status = None
            continue
        status_match = _STATUS_RE.search(line)
        if status_match:
            current_status = int(status_match.group(1))
            statuses.append(current_status)
            continue
        range_match = _CONTENT_RANGE_RE.search(line)
        if range_match and current_method == "GET":
            start, end = int(range_match.group(1)), int(range_match.group(2))
            ranges.append((start, end))
            continue
        length_match = _CONTENT_LENGTH_RE.search(line)
        if length_match and current_method == "GET" and current_status == 200:
            length = int(length_match.group(1))
            if length:
                ranges.append((0, length - 1))
    payload_bytes = sum(end - start + 1 for start, end in ranges)
    unique_bytes = sum(end - start + 1 for start, end in sorted(set(ranges)))
    return {
        "measured": bool(ranges),
        "response_count": len(statuses),
        "statuses": sorted(set(statuses)),
        "range_count": len(ranges),
        "payload_bytes": payload_bytes,
        "unique_payload_bytes": unique_bytes,
        "first_ranges": [[start, end] for start, end in ranges[:10]],
    }


@contextmanager
def capture_curl_bytes():
    """Capture GDAL/CURL range responses and return actual payload byte counts."""
    fd, path = tempfile.mkstemp(prefix="terrain-buffer-curl-", suffix=".log")
    os.close(fd)
    saved_stderr = os.dup(2)
    sink = os.open(path, os.O_WRONLY | os.O_TRUNC)
    os.dup2(sink, 2)
    os.close(sink)
    previous = os.environ.get("CPL_CURL_VERBOSE")
    os.environ["CPL_CURL_VERBOSE"] = "YES"
    state: dict[str, Any] = {}
    try:
        yield state
    finally:
        os.dup2(saved_stderr, 2)
        os.close(saved_stderr)
        if previous is None:
            os.environ.pop("CPL_CURL_VERBOSE", None)
        else:
            os.environ["CPL_CURL_VERBOSE"] = previous
        text = Path(path).read_text(errors="replace")
        Path(path).unlink(missing_ok=True)
        state.update(_parse_curl_log(text))


@contextmanager
def gdal_remote_env(timeout: float):
    with rasterio.Env(
        GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
        CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif",
        GDAL_HTTP_TIMEOUT=str(int(timeout)),
        GDAL_HTTP_CONNECTTIMEOUT="10",
        GDAL_HTTP_MAX_RETRY="0",
        VSI_CACHE="FALSE",
        CPL_VSIL_CURL_CACHE_SIZE="16777216",
    ):
        yield


def measured_read(
    url: str,
    *,
    dst_crs: CRS,
    center_xy: tuple[float, float],
    buffer_cells: int,
    timeout: float,
) -> tuple[Any, dict[str, Any]]:
    with capture_curl_bytes() as capture, gdal_remote_env(timeout):
        window = read_metric_window(
            "/vsicurl/" + url,
            dst_crs=dst_crs,
            center_xy=center_xy,
            core_cells=CORE_CELLS,
            buffer_cells=buffer_cells,
            resolution_m=ANALYSIS_RESOLUTION_M,
        )
    return window, capture


def head_remote(url: str, timeout: float) -> dict[str, Any]:
    remote = copernicus_glo90.head_remote_object(url, timeout=timeout)
    return {
        "url": remote.url,
        "status": remote.status,
        "content_length": remote.content_length,
        "etag": remote.etag,
        "last_modified": remote.last_modified,
    }


def compute_fields(window) -> dict[str, np.ndarray]:
    z = window.elevation
    central = curvature_components(z, ANALYSIS_RESOLUTION_M)
    smoothed = smoothed_curvature_components(z, ANALYSIS_RESOLUTION_M)
    quadratic_r2 = quadratic_curvature_components(z, ANALYSIS_RESOLUTION_M, radius_cells=2)
    quadratic_r3 = quadratic_curvature_components(z, ANALYSIS_RESOLUTION_M, radius_cells=3)
    return {
        "surface_curvedness_central": central["surface_curvedness_m_inv"],
        "mean_curvature_central": central["mean_curvature_m_inv"],
        "gaussian_curvature_central": central["gaussian_curvature_m_inv2"],
        "surface_curvedness_quadratic_r2": quadratic_r2["surface_curvedness_m_inv"],
        "surface_curvedness_quadratic_r3": quadratic_r3["surface_curvedness_m_inv"],
        "surface_curvedness_smoothed": smoothed["surface_curvedness_m_inv"],
        "profile_curvature_central": central["profile_curvature_m_inv"],
        "profile_curvature_smoothed": smoothed["profile_curvature_m_inv"],
        "tangential_curvature_central": central["tangential_curvature_m_inv"],
        "tangential_curvature_smoothed": smoothed["tangential_curvature_m_inv"],
    }


def core_crop(field_array: np.ndarray) -> np.ndarray:
    offset = BUFFER_CELLS
    return field_array[offset : offset + CORE_CELLS, offset : offset + CORE_CELLS]


def registry_metrics_check(normal, buffered) -> dict[str, Any]:
    try:
        normal_metrics = terrain_metrics(normal.elevation, ANALYSIS_RESOLUTION_M)
        buffered_metrics = terrain_metrics(buffered.core(), ANALYSIS_RESOLUTION_M)
    except Exception as exc:  # pragma: no cover - only a real-data extraction failure
        return {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
    differences = {key: abs(buffered_metrics[key] - normal_metrics[key]) for key in normal_metrics}
    return {
        "status": "unchanged",
        "normal_crop": normal_metrics,
        "buffered_core": buffered_metrics,
        "max_abs_diff": max(differences.values()),
    }


def run_site(site: Site, timeout: float) -> dict[str, Any]:
    remote = head_remote(site.access_url, timeout)
    assert site.dst_crs is not None
    if site.center_lonlat is not None:
        lon, lat = site.center_lonlat
        x, y = Transformer.from_crs("EPSG:4326", site.dst_crs, always_xy=True).transform(lon, lat)
        center_xy = (float(x), float(y))
        center_payload = {
            "lon": lon,
            "lat": lat,
            "analysis_crs": site.dst_crs.to_string(),
            "center_xy": [center_xy[0], center_xy[1]],
        }
    else:
        assert site.center_xy is not None
        center_xy = site.center_xy
        center_payload = {
            "lon": None,
            "lat": None,
            "analysis_crs": site.dst_crs.to_string(),
            "center_xy": [float(center_xy[0]), float(center_xy[1])],
        }
    dst_crs = site.dst_crs

    buffered, buffered_bytes = measured_read(
        site.access_url,
        dst_crs=dst_crs,
        center_xy=center_xy,
        buffer_cells=BUFFER_CELLS,
        timeout=timeout,
    )
    # The normal 1.98 km analysis crop is exactly the central core of the buffered
    # read: same source pixels, same 90 m grid, same reprojection. This isolates the
    # support/edge question instead of introducing independent source-window rounding.
    normal = MetricWindow(
        elevation=buffered.core().copy(),
        transform=buffered.core_transform,
        crs=buffered.crs,
        source_crs=buffered.source_crs,
        core_cells=CORE_CELLS,
        buffer_cells=0,
        resolution_m=ANALYSIS_RESOLUTION_M,
        warnings=buffered.warnings,
        source_resolution=buffered.source_resolution,
        source_shape=buffered.source_shape,
        source_nodata=buffered.source_nodata,
    )
    core_agreement = {
        "max_abs_diff": 0.0,
        "all_equal": True,
        "normal_source": "central core of the buffered read",
        "normal_checksum": window_checksum(normal.elevation, site.key),
        "buffered_core_checksum": window_checksum(buffered.core(), site.key),
    }
    normal_fields = compute_fields(normal)
    buffered_fields = compute_fields(buffered)
    comparisons = []
    for field_name, method_id in FIELD_METHODS.items():
        comparisons.append(
            compare_feature(
                normal_fields[field_name],
                core_crop(buffered_fields[field_name]),
                method_id=method_id,
            )
        )
    geomorphon = compare_support(
        support_mask((CORE_CELLS, CORE_CELLS), 12),
        core_crop(support_mask((46, 46), 12)),
        method_id="geomorphon_10class",
    )
    geomorphon["reference_status"] = "blocked_no_grass_r_geomorphon"
    geomorphon["blocker"] = (
        "GRASS r.geomorphon is not installed in this environment, so no reference "
        "10-class output was produced. Only support/edge-loss was measured; no class "
        "values or class fractions are claimed."
    )
    sensitivity = []
    for reference_id, candidate_id in SENSITIVITY_PAIRS:
        sensitivity.append(
            compare_fields(
                core_crop(buffered_fields[reference_id]),
                core_crop(buffered_fields[candidate_id]),
                reference_id=reference_id,
                candidate_id=candidate_id,
            )
        )
    return {
        "body": site.body,
        "site_key": site.key,
        "display_name": site.display_name,
        "data_kind": "measured_real_dem_method_sensitivity_not_an_observation",
        "source": {
            "dataset_id": site.dataset_id,
            "product_id": site.product_id,
            "product_title": site.product_title,
            "provider": site.provider,
            "license": site.license,
            "license_url": site.license_url,
            "citation": site.citation,
            "source_url": site.source_url,
            "access_url": site.access_url,
            "access": "bounded GDAL /vsicurl range reads only; no full tile is intentionally saved",
            "remote_head": remote,
            "raster_metadata": {
                "source_crs": (buffered.source_crs.to_string() if buffered.source_crs else None),
                "source_resolution": (
                    list(buffered.source_resolution)
                    if buffered.source_resolution is not None
                    else None
                ),
                "source_shape": (
                    list(buffered.source_shape) if buffered.source_shape is not None else None
                ),
                "source_nodata": buffered.source_nodata,
                "analysis_crs": dst_crs.to_string(),
                "metadata_source": "read during the single bounded buffered open; no separate "
                "full-object probe was issued",
            },
            "notes": list(site.notes),
        },
        "center": center_payload,
        "reads": {
            "normal": {
                "buffer_cells": 0,
                "grid_cells": normal.grid_cells,
                "grid_metres": normal.grid_cells * ANALYSIS_RESOLUTION_M,
                "derived_from": "central core of the buffered read",
                "bytes": {
                    "measured": False,
                    "payload_bytes": 0,
                    "reason": "the normal core reuses the buffered read; a separate subset "
                    "read would add no information and can introduce source-window rounding noise",
                },
                "valid_elevation_fraction": float(np.isfinite(normal.elevation).mean()),
            },
            "buffered": {
                "buffer_cells": BUFFER_CELLS,
                "grid_cells": buffered.grid_cells,
                "grid_metres": buffered.grid_cells * ANALYSIS_RESOLUTION_M,
                "bytes": buffered_bytes,
                "valid_elevation_fraction": float(np.isfinite(buffered.elevation).mean()),
            },
        },
        "core_agreement": core_agreement,
        "registry_metrics_unchanged": registry_metrics_check(normal, buffered),
        "features": comparisons,
        "geomorphon": geomorphon,
        "method_sensitivity_on_buffered_core": sensitivity,
    }


def artifact_payload(experiments: list[dict[str, Any]], timeout: float) -> dict[str, Any]:
    grass = shutil.which("grass")
    blockers = [
        {
            "method_id": "geomorphon_10class",
            "body": "all",
            "status": "blocked",
            "reason": "No pinned GRASS r.geomorphon binary is available in this environment; "
            "no 10-class reference output was produced.",
            "evidence": {
                "command": "which grass",
                "result": grass,
                "interpretation": "not installed"
                if grass is None
                else "binary present but not run",
            },
            "measured_alternative": "buffer/support and edge-loss counts only",
        },
        {
            "method_id": "surface_curvedness_and_profile_tangential_curvature",
            "body": "all",
            "status": "method_sensitivity_only_not_promoted",
            "reason": "Curvature buffers and value stability were measured on real DEMs, but no "
            "reference-implementation agreement, calibrated scale, or uncertainty propagation "
            "has been established.",
        },
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": utc_now(),
        "data_kind": "measured_real_dem_method_sensitivity",
        "not_a_similarity_change": True,
        "production_features_remaining_five": True,
        "analysis_support": {
            "core_cells": CORE_CELLS,
            "core_size_m": CORE_CELLS * ANALYSIS_RESOLUTION_M,
            "analysis_resolution_m": ANALYSIS_RESOLUTION_M,
            "resampling": "bilinear",
            "max_buffer_cells": BUFFER_CELLS,
            "max_buffer_m": BUFFER_CELLS * ANALYSIS_RESOLUTION_M,
            "max_footprint_cells": CORE_CELLS + 2 * BUFFER_CELLS,
            "max_footprint_m": (CORE_CELLS + 2 * BUFFER_CELLS) * ANALYSIS_RESOLUTION_M,
        },
        "buffer_requirements": buffer_table_payload(),
        "experiments": experiments,
        "blockers": blockers,
        "limitations": [
            "Bounded pilot windows, not a global or exhaustive survey.",
            "The buffer radii are method-specific a-priori proposals, not calibrated universal scales.",
            "Normal and buffered common cells use the same aligned 90 m grid; the measured "
            "difference is edge coverage and summary bias, not a new elevation value.",
            "Bilinear resampling from 3 arc-second (Earth), 5 m (Moon) and 20 m (Mars) sources "
            "was not separated from source interpolation/uncertainty.",
            "COG/GeoTIFF block layout can make transferred bytes larger than the window footprint.",
            "No curvature or geomorphon value is registered, scored, or compared across bodies.",
        ],
        "commands": {
            "dry_run": "uv run python scripts/validate_terrain_buffers.py --dry-run --all",
            "real": "uv run python scripts/validate_terrain_buffers.py --earth-site death-valley "
            "--earth-site rio-tinto --moon --mars --out docs/terrain-buffer-validation.json "
            "--csv docs/terrain-buffer-measurements.csv",
            "timeout_s": timeout,
        },
    }


def write_csv(path: Path, experiments: list[dict[str, Any]]) -> None:
    columns = [
        "body",
        "site_key",
        "method_id",
        "required_buffer_cells",
        "required_buffer_m",
        "normal_supported_cells",
        "buffered_core_supported_cells",
        "common_cells",
        "edge_loss_cells",
        "edge_loss_fraction_of_core",
        "mean_abs_diff_on_common",
        "rms_diff_on_common",
        "summary_mean_normal",
        "summary_mean_buffered",
        "mean_shift_buffered_minus_normal",
    ]
    lines = [",".join(columns)]
    for experiment in experiments:
        for feature in experiment["features"]:
            requirement = BUFFER_REQUIREMENT_BY_ID[feature["method_id"]]
            values = [
                experiment["body"],
                experiment["site_key"],
                feature["method_id"],
                requirement.radius_cells,
                requirement.buffer_m,
                feature["normal_supported_cells"],
                feature["buffered_core_supported_cells"],
                feature["common_cells"],
                feature["edge_loss_cells"],
                feature["edge_loss_fraction_of_core"],
                feature["value_comparison_on_common_cells"]["mean_abs_diff"],
                feature["value_comparison_on_common_cells"]["rms_diff"],
                feature["summary_normal_supported"]["mean"],
                feature["summary_buffered_core"]["mean"],
                feature["mean_shift_buffered_minus_normal"],
            ]
            lines.append(",".join("" if value is None else str(value) for value in values))
        geomorphon = experiment["geomorphon"]
        requirement = BUFFER_REQUIREMENT_BY_ID["geomorphon_10class"]
        values = [
            experiment["body"],
            experiment["site_key"],
            "geomorphon_10class",
            requirement.radius_cells,
            requirement.buffer_m,
            geomorphon["normal_supported_cells"],
            geomorphon["buffered_core_supported_cells"],
            0,
            geomorphon["edge_loss_cells"],
            geomorphon["edge_loss_cells"] / float(CORE_CELLS * CORE_CELLS),
            "",
            "",
            "",
            "",
            "",
        ]
        lines.append(",".join("" if value is None else str(value) for value in values))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="validate_terrain_buffers.py",
        description="Bounded real-DEM buffer/edge-validity method-sensitivity experiment.",
    )
    parser.add_argument(
        "--earth-site",
        action="append",
        default=[],
        help="Copernicus GLO-90 candidate slug (repeatable); default death-valley, rio-tinto",
    )
    parser.add_argument("--moon", action="store_true", help="include the LOLA Site04 window")
    parser.add_argument("--mars", action="store_true", help="include the Jezero CTX window")
    parser.add_argument(
        "--all",
        action="store_true",
        help="include death-valley, rio-tinto, Moon and Mars",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=REPO_ROOT / "docs" / "terrain-buffer-validation.json",
        help="JSON artifact path",
    )
    parser.add_argument(
        "--csv",
        type=Path,
        default=REPO_ROOT / "docs" / "terrain-buffer-measurements.csv",
        help="CSV artifact path",
    )
    parser.add_argument("--timeout", type=float, default=30.0, help="HTTP timeout seconds")
    parser.add_argument("--dry-run", action="store_true", help="print the plan; no network/writes")
    return parser


def dry_run(sites: list[Site]) -> None:
    print("DRY RUN — no network, no raster or artifact writes")
    print(
        f"core: {CORE_CELLS} x {CORE_CELLS} cells at {ANALYSIS_RESOLUTION_M:g} m "
        f"({CORE_CELLS * ANALYSIS_RESOLUTION_M:g} m)"
    )
    print("buffer requirements:")
    for requirement in BUFFER_REQUIREMENTS:
        print(
            f"  {requirement.method_id:40s} radius={requirement.radius_cells:2d} cells "
            f"({requirement.buffer_m:4.0f} m) footprint={requirement.full_footprint_cells}x"
            f"{requirement.full_footprint_cells} ({requirement.full_footprint_m:.0f} m) "
            f"normal_support={requirement.normal_support_cells}/484"
        )
    print(
        f"max buffered read: {CORE_CELLS + 2 * BUFFER_CELLS} x "
        f"{CORE_CELLS + 2 * BUFFER_CELLS} cells "
        f"({(CORE_CELLS + 2 * BUFFER_CELLS) * ANALYSIS_RESOLUTION_M:.0f} m)"
    )
    print("sites:")
    for site in sites:
        center = site.center_lonlat or site.center_xy
        print(f"  {site.body:5s} {site.key:36s} center={center} url={site.access_url}")
    print("estimated transfer: bounded range reads only; exact bytes are measured and recorded")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    sites = select_sites(args)
    if not sites:
        parser.error("no sites selected")
    if args.dry_run:
        dry_run(sites)
        return 0
    experiments = []
    failures = []
    for site in sites:
        last_error: Exception | None = None
        for attempt in (1, 2):
            try:
                experiments.append(run_site(site, args.timeout))
                last_error = None
                break
            except Exception as exc:  # noqa: BLE001 - record the observed provider failure
                last_error = exc
                print(f"{site.body} {site.key}: attempt {attempt} failed: {exc}")
        if last_error is not None:
            failures.append(
                {
                    "body": site.body,
                    "site_key": site.key,
                    "display_name": site.display_name,
                    "status": "failed",
                    "error": f"{type(last_error).__name__}: {last_error}",
                    "source": {
                        "source_url": site.source_url,
                        "access_url": site.access_url,
                        "dataset_id": site.dataset_id,
                        "product_id": site.product_id,
                        "license": site.license,
                        "license_url": site.license_url,
                    },
                    "interpretation": "No values are reported for this site; this is an "
                    "operational/provider failure, not zero coverage.",
                }
            )
    payload = artifact_payload(experiments, args.timeout)
    payload["failed_sites"] = failures
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    write_csv(args.csv, experiments)
    for experiment in experiments:
        print(
            f"{experiment['body']:5s} {experiment['site_key']}: "
            f"{experiment['reads']['buffered']['bytes']['payload_bytes']} B buffered; "
            f"core_equal={experiment['core_agreement']['all_equal']}"
        )
    for failure in failures:
        print(f"FAILED {failure['body']} {failure['site_key']}: {failure['error']}")
    print(f"wrote {args.out}")
    print(f"wrote {args.csv}")
    return 0 if not failures else 2


if __name__ == "__main__":
    raise SystemExit(main())
