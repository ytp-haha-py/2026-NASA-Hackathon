#!/usr/bin/env python3
"""Build bounded same-support Moon/Mars coarse reference pilots (Issue #35).

The builder reads only small windows from verified public archives:

* Moon: PGDA LOLA MOON_PA 128 ppd GeoTIFF (236.85 m native support).
* Mars: MOLA MEGDR 128 ppd tile (463.0576672 m native support; evidence-only).
* Mars: USGS Jezero CTX 20 m DTM (finer than 250 m; aggregated to 250 m).

No global raster is downloaded. Elevation fixtures are tiny, committed GeoTIFFs so
the slope and aggregation can be recomputed offline without network access.

Usage::

    uv run python pipelines/build_planetary_coarse.py --plan --allow-network
    uv run python pipelines/build_planetary_coarse.py --allow-network
    uv run python pipelines/build_planetary_coarse.py --verify
"""

from __future__ import annotations

import argparse
import json
import os
import resource
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from analog_pipelines.planetary_coarse import (
    ANALYSIS_RESOLUTION_M,
    MARS_CTX_JEZERO,
    MARS_MOLA_MEG128,
    MOON_LOLA128,
    PLANETARY_COARSE_METHOD_VERSION,
    CoarseCellResult,
    PlanetaryCoarseError,
    build_manifest,
    cell_bbox_from_center,
    coarse_cell_from_dem,
    inspect_planetary_raster,
    write_elevation_fixture,
    write_manifest,
)
from analog_pipelines.planetary_coarse import verify_manifest as verify_planetary_manifest
from pyproj import CRS, Geod, Transformer

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = REPO_ROOT / "pipelines/planetary/coarse"
DEFAULT_FIXTURE_DIR = REPO_ROOT / "samples/planetary/coarse/fixtures"
RUN_COMMAND = "uv run python pipelines/build_planetary_coarse.py --allow-network"

MOON_CRS_PROJ4 = "+proj=aeqd +lat_0=-10 +lon_0=20 +R=1737400 +units=m +no_defs"
MOON_CELL_CENTRES = (
    (0, 0, (-2500.0, -2500.0)),
    (0, 1, (2500.0, -2500.0)),
    (1, 0, (-2500.0, 2500.0)),
    (1, 1, (2500.0, 2500.0)),
)
MOON_N_PIXELS = 20
MOON_LIMITATIONS = (
    "One 10 x 10 km coordinate window at 20E, 10S; not lunar global coverage.",
    "LDEM128 is a derived global shape model (SLDEM2015 256 ppd plus polar LDEMs "
    "averaged at 128 ppd), not raw LOLA altimetry, and is partly interpolated.",
    "The value is a Horn 3x3 derivative of elevation, not the published "
    "Geomorpho250 preprocessed slope product used by the Earth coarse index.",
    "No cross-body ranking is enabled; the comparability table marks every "
    "slope-derived metric evidence_only.",
)

MARS_CTX_BLOCK = {
    "west": 4_390_000.0,
    "south": 1_095_000.0,
    "east": 4_400_000.0,
    "north": 1_105_000.0,
}
MARS_CTX_N_PIXELS = 20
MARS_CTX_LIMITATIONS = (
    "Four 5 km cells inside one 10 x 10 km coordinate window of the Jezero CTX "
    "mosaic; not Mars global coverage and not a geological unit map.",
    "The 20 m stereo DTM is aggregated to 250 m; mosaic seams and per-window "
    "vertical uncertainty are not validated.",
    "The value is a Horn 3x3 derivative of elevation, not the published "
    "Geomorpho250 preprocessed slope product used by the Earth coarse index.",
    "No cross-body ranking is enabled; every slope-derived metric is evidence_only.",
)
MARS_MOLA_LIMITATIONS = (
    "Evidence-only: MEGDR native support is 463.0576672 m/pixel, so each cell "
    "contains ~11 native pixels and a 250 m grid would upsample an interpolated "
    "product.",
    "MEGDR stores median observed topography and supplies interpolated values where "
    "no MOLA shot falls in a bin; the two evidence cells are at Jezero for a paired "
    "support comparison with CTX, not a new global grid.",
    "MOLA licence/redistribution terms are not stated in the PDS4 bundle labels.",
    "No cross-body or cross-method ranking is enabled.",
)


def _utc_now() -> str:
    epoch = os.environ.get("SOURCE_DATE_EPOCH", "").strip()
    if epoch:
        return datetime.fromtimestamp(int(epoch), UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _named_crs(crs: CRS, name: str) -> CRS:
    """Give a custom planetary projected CRS a readable label without changing it."""
    wkt = crs.to_wkt()
    if f'PROJCRS["{name}"' in wkt:
        return crs
    return CRS.from_wkt(wkt.replace('PROJCRS["unknown"', f'PROJCRS["{name}"', 1))


def _moon_cells(allow_network: bool) -> tuple[list[CoarseCellResult], CRS]:
    crs = _named_crs(CRS.from_proj4(MOON_CRS_PROJ4), "Lunar_Azimuthal_Equidistant_20E_10S")
    cells: list[CoarseCellResult] = []
    for row, col, centre in MOON_CELL_CENTRES:
        cells.append(
            coarse_cell_from_dem(
                MOON_LOLA128.raster_url,
                source=MOON_LOLA128,
                region_id="moon-lola128-eqc-window-01",
                row=row,
                col=col,
                target_crs=crs,
                center_xy=centre,
                n_pixels=MOON_N_PIXELS,
                resolution_m=ANALYSIS_RESOLUTION_M,
                allow_remote=allow_network,
            )
        )
    return cells, crs


def _mars_ctx_cells(allow_network: bool) -> tuple[list[CoarseCellResult], CRS]:
    metadata = inspect_planetary_raster(
        MARS_CTX_JEZERO.raster_url, source=MARS_CTX_JEZERO, allow_remote=allow_network
    )
    crs = CRS.from_wkt(metadata.crs_wkt)
    n = MARS_CTX_N_PIXELS
    width = MARS_CTX_BLOCK["east"] - MARS_CTX_BLOCK["west"]
    height = MARS_CTX_BLOCK["north"] - MARS_CTX_BLOCK["south"]
    cols = int(round(width / (n * ANALYSIS_RESOLUTION_M)))
    rows = int(round(height / (n * ANALYSIS_RESOLUTION_M)))
    if cols < 1 or rows < 1:
        raise PlanetaryCoarseError("invalid_region", "Mars CTX block is smaller than one cell")
    cells: list[CoarseCellResult] = []
    for row in range(rows):
        for col in range(cols):
            centre = (
                MARS_CTX_BLOCK["west"] + (col + 0.5) * n * ANALYSIS_RESOLUTION_M,
                MARS_CTX_BLOCK["south"] + (row + 0.5) * n * ANALYSIS_RESOLUTION_M,
            )
            cells.append(
                coarse_cell_from_dem(
                    MARS_CTX_JEZERO.raster_url,
                    source=MARS_CTX_JEZERO,
                    region_id="mars-ctx-jezero-window-01",
                    row=row,
                    col=col,
                    target_crs=crs,
                    center_xy=centre,
                    n_pixels=n,
                    resolution_m=ANALYSIS_RESOLUTION_M,
                    allow_remote=allow_network,
                )
            )
    return cells, crs


def _mars_mola_cells(
    ctx_cells: list[CoarseCellResult], ctx_crs: CRS, allow_network: bool
) -> tuple[list[CoarseCellResult], CRS, list[dict[str, Any]]]:
    metadata = inspect_planetary_raster(
        MARS_MOLA_MEG128.url, source=MARS_MOLA_MEG128, allow_remote=allow_network
    )
    mola_crs = CRS.from_wkt(metadata.crs_wkt)
    ctx_geographic = CRS.from_proj4("+proj=longlat +R=3396190 +no_defs")
    mola_geographic = CRS.from_proj4("+proj=longlat +R=3396000 +no_defs")
    to_ctx_geo = Transformer.from_crs(ctx_crs, ctx_geographic, always_xy=True)
    from_geo_to_mola = Transformer.from_crs(mola_geographic, mola_crs, always_xy=True)
    geod = Geod(a=3_396_190.0, b=3_396_190.0)
    resolution = MARS_MOLA_MEG128.native_resolution_m
    n = max(2, round(5000.0 / resolution))
    cells: list[CoarseCellResult] = []
    pairs: list[dict[str, Any]] = []
    # Two non-adjacent cells avoid overlapping native MEGDR footprints.
    for index, (row, col) in enumerate(((0, 0), (1, 1))):
        ctx = next(cell for cell in ctx_cells if (cell.row, cell.col) == (row, col))
        centre_ctx = (
            (ctx.bbox_metric["west"] + ctx.bbox_metric["east"]) / 2,
            (ctx.bbox_metric["south"] + ctx.bbox_metric["north"]) / 2,
        )
        lon, lat = to_ctx_geo.transform(*centre_ctx)
        centre_mola = from_geo_to_mola.transform(lon, lat)
        cell = coarse_cell_from_dem(
            MARS_MOLA_MEG128.url,
            source=MARS_MOLA_MEG128,
            region_id="mars-mola-meg128-evidence-01",
            row=row,
            col=col,
            target_crs=mola_crs,
            center_xy=centre_mola,
            n_pixels=n,
            resolution_m=resolution,
            window_size_m=n * resolution,
            allow_remote=allow_network,
        )
        cells.append(cell)
        _, _, separation = geod.inv(
            lon,
            lat,
            *Transformer.from_crs(mola_crs, mola_geographic, always_xy=True).transform(
                *centre_mola
            ),
        )
        pairs.append(
            {
                "evidence_index": index + 1,
                "ctx_candidate_id": ctx.candidate_id,
                "mola_candidate_id": cell.candidate_id,
                "ctx_window_size_m": ctx.window_size_m,
                "mola_window_size_m": cell.window_size_m,
                "mola_native_pixels_per_side": n,
                "centre_separation_m": abs(float(separation)),
                "observed_delta": {
                    feature_id: float(cell.features[feature_id] - ctx.features[feature_id])
                    for feature_id in cell.features
                },
                "ctx_features": ctx.features,
                "mola_features": cell.features,
            }
        )
    return cells, mola_crs, pairs


def _write_cells(
    *,
    cells: list[CoarseCellResult],
    output_path: Path,
    fixture_dir: Path,
    source: Any,
    region: dict[str, Any],
    run: dict[str, Any],
    limitations: tuple[str, ...],
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    fixture_payloads = []
    for cell in cells:
        stem = cell.candidate_id.replace("planet-coarse-v1:", "").replace(":", "-")
        filename = f"{cell.body.lower()}_{stem}.tif"
        fixture_path = fixture_dir / filename
        label = fixture_path.relative_to(REPO_ROOT).as_posix()
        fixture_payloads.append(
            write_elevation_fixture(
                fixture_path,
                cell.fixture,
                transform=cell.fixture_transform,
                crs=cell.fixture_crs,
                path_label=label,
            )
        )
    run = dict(run)
    run["fixture_count"] = len(fixture_payloads)
    run["fixture_bytes"] = sum(int(item["bytes"]) for item in fixture_payloads)
    manifest = build_manifest(
        source=source,
        region=region,
        cells=cells,
        fixture_payloads=fixture_payloads,
        generated_at=_utc_now(),
        run=run,
        limitations=limitations,
    )
    if extra:
        manifest.update(extra)
    write_manifest(output_path, manifest)
    return manifest


def _region_payload(
    *,
    region_id: str,
    label: str,
    body: str,
    crs: CRS,
    cell_centres: list[tuple[float, float]],
    n_pixels: int,
    resolution_m: float,
    notes: str,
) -> dict[str, Any]:
    frames = [
        cell_bbox_from_center(centre, n_pixels=n_pixels, resolution_m=resolution_m)
        for centre in cell_centres
    ]
    return {
        "region_id": region_id,
        "label": label,
        "body": body,
        "crs_name": crs.name,
        "crs_wkt": crs.to_wkt(),
        "crs_frame_note": "body-fixed planetary frame; never Earth WGS84/UTM",
        "analysis_resolution_m": resolution_m,
        "n_pixels_per_side": n_pixels,
        "window_size_m": n_pixels * resolution_m,
        "cell_count": len(cell_centres),
        "bbox_metric": {
            "west": min(item["west"] for item in frames),
            "south": min(item["south"] for item in frames),
            "east": max(item["east"] for item in frames),
            "north": max(item["north"] for item in frames),
        },
        "sample_label": "bounded_pilot_not_global_exhaustive",
        "notes": notes,
    }


def _build(allow_network: bool) -> dict[str, Any]:
    started = time.perf_counter()
    output_paths: list[Path] = []

    moon_cells, moon_crs = _moon_cells(allow_network)
    moon_region = _region_payload(
        region_id="moon-lola128-eqc-window-01",
        label="Moon LOLA128 coordinate window 01 (20E, 10S)",
        body="Moon",
        crs=moon_crs,
        cell_centres=[centre for _, _, centre in MOON_CELL_CENTRES],
        n_pixels=MOON_N_PIXELS,
        resolution_m=ANALYSIS_RESOLUTION_M,
        notes=(
            "Coordinate window only; no landform or geologic interpretation. The local "
            "azimuthal-equidistant CRS is defined on the lunar 1737.4 km sphere."
        ),
    )
    moon_path = DEFAULT_OUTPUT_DIR / "moon_lola128_window01.json"
    moon_manifest = _write_cells(
        cells=moon_cells,
        output_path=moon_path,
        fixture_dir=DEFAULT_FIXTURE_DIR,
        source=MOON_LOLA128,
        region=moon_region,
        run=_run_payload("moon", moon_cells, started),
        limitations=MOON_LIMITATIONS,
    )
    output_paths.append(moon_path)

    ctx_cells, ctx_crs = _mars_ctx_cells(allow_network)
    ctx_region = _region_payload(
        region_id="mars-ctx-jezero-window-01",
        label="Mars Jezero CTX coordinate window 01",
        body="Mars",
        crs=ctx_crs,
        cell_centres=[
            (
                (cell.bbox_metric["west"] + cell.bbox_metric["east"]) / 2,
                (cell.bbox_metric["south"] + cell.bbox_metric["north"]) / 2,
            )
            for cell in ctx_cells
        ],
        n_pixels=MARS_CTX_N_PIXELS,
        resolution_m=ANALYSIS_RESOLUTION_M,
        notes=(
            "Coordinate window inside the USGS Jezero CTX mosaic; no landform or "
            "geologic interpretation. The CRS is the raster's Mars_2000_Sphere "
            "Jezero equirectangular frame, not an Earth UTM zone."
        ),
    )
    ctx_path = DEFAULT_OUTPUT_DIR / "mars_ctx_jezero_window01.json"
    ctx_manifest = _write_cells(
        cells=ctx_cells,
        output_path=ctx_path,
        fixture_dir=DEFAULT_FIXTURE_DIR,
        source=MARS_CTX_JEZERO,
        region=ctx_region,
        run=_run_payload("mars-ctx", ctx_cells, started),
        limitations=MARS_CTX_LIMITATIONS,
    )
    output_paths.append(ctx_path)

    mola_cells, mola_crs, pairs = _mars_mola_cells(ctx_cells, ctx_crs, allow_network)
    mola_region = _region_payload(
        region_id="mars-mola-meg128-evidence-01",
        label="Mars MOLA MEGDR 128 ppd evidence cells at Jezero",
        body="Mars",
        crs=mola_crs,
        cell_centres=[
            (
                (cell.bbox_metric["west"] + cell.bbox_metric["east"]) / 2,
                (cell.bbox_metric["south"] + cell.bbox_metric["north"]) / 2,
            )
            for cell in mola_cells
        ],
        n_pixels=int(round(mola_cells[0].window_size_m / MARS_MOLA_MEG128.native_resolution_m)),
        resolution_m=MARS_MOLA_MEG128.native_resolution_m,
        notes=(
            "Evidence cells use native MEGDR support (11 pixels of 463.0576672 m, "
            "5093.634 m per side); they are not 5 km same-support cells. Paired with "
            "two non-adjacent CTX 250 m cells to quantify a support/interpolation "
            "difference on the same body and region."
        ),
    )
    mola_path = DEFAULT_OUTPUT_DIR / "mars_mola_meg128_evidence.json"
    mola_manifest = _write_cells(
        cells=mola_cells,
        output_path=mola_path,
        fixture_dir=DEFAULT_FIXTURE_DIR,
        source=MARS_MOLA_MEG128,
        region=mola_region,
        run=_run_payload("mars-mola", mola_cells, started),
        limitations=MARS_MOLA_LIMITATIONS,
        extra={
            "support_comparison": {
                "label": "same-region support/interpolation comparison, not an Earth calibration",
                "pairs": pairs,
                "interpretation": (
                    "Observed deltas between a 20 m CTX DTM aggregated to 250 m and the "
                    "native 463.0576672 m MEGDR median grid. Differences combine support, "
                    "binning, interpolation, vertical registration and mosaic effects; "
                    "they must not be read as a correction factor or a physical equivalence."
                ),
            }
        },
    )
    output_paths.append(mola_path)

    summary = {
        "schema_version": "planetary-coarse-index-1.0.0",
        "generated_at": _utc_now(),
        "method_version": PLANETARY_COARSE_METHOD_VERSION,
        "command": RUN_COMMAND,
        "pilots": [
            {
                "body": manifest["source"]["body"],
                "source_id": manifest["source"]["source_id"],
                "manifest": path.relative_to(REPO_ROOT).as_posix(),
                "cells": manifest["counts"]["computed_cells"],
                "native_resolution_m": manifest["source"]["native_resolution_m"],
                "same_support_as_earth": manifest["source"]["same_support_as_earth"],
                "decisions": sorted({item["decision"] for item in manifest["comparability"]}),
                "cross_body_ranking_enabled": manifest["cross_body_ranking"]["enabled"],
            }
            for manifest, path in (
                (moon_manifest, moon_path),
                (ctx_manifest, ctx_path),
                (mola_manifest, mola_path),
            )
        ],
        "cross_body_ranking": {
            "enabled": False,
            "reason": (
                "No planetary coarse metric is validated as the same quantity, method and "
                "support as the Earth coarse index; cross-body ranking stays disabled."
            ),
        },
        "run": {
            "elapsed_s": round(time.perf_counter() - started, 2),
            "peak_rss_kib": int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss),
            "network_io_note": (
                "Only bounded /vsicurl windows were read; per-cell upper bounds are "
                "recorded in each manifest. No global raster was downloaded."
            ),
        },
    }
    summary_path = DEFAULT_OUTPUT_DIR / "index.json"
    write_manifest(summary_path, summary)
    output_paths.append(summary_path)

    for path in (moon_path, ctx_path, mola_path):
        verify_planetary_manifest(path, fixture_root=REPO_ROOT)
    return {"manifests": [str(path) for path in output_paths], "summary": summary}


def _run_payload(label: str, cells: list[CoarseCellResult], started: float) -> dict[str, Any]:
    return {
        "pilot": label,
        "command": RUN_COMMAND,
        "elapsed_s_at_write": round(time.perf_counter() - started, 2),
        "peak_rss_kib_at_write": int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss),
        "fixture_bytes": None,
        "source_read_upper_bound_bytes": sum(cell.source_read_bytes_upper_bound for cell in cells),
        "network_io_note": (
            "Per-cell upper bound includes block alignment and a 64 KiB label allowance; "
            "it is not a measured network byte count."
        ),
    }


def _plan(allow_network: bool) -> None:
    for source in (MOON_LOLA128, MARS_MOLA_MEG128, MARS_CTX_JEZERO):
        metadata = inspect_planetary_raster(
            source.url if source is MARS_MOLA_MEG128 else source.raster_url,
            source=source,
            allow_remote=allow_network,
        )
        print(
            json.dumps(
                {
                    "source_id": source.source_id,
                    "body": metadata.body,
                    "crs": metadata.crs_name,
                    "native_resolution_m": round(metadata.native_resolution_m, 4),
                    "claimed_native_resolution_m": source.native_resolution_m,
                    "nodata": metadata.nodata,
                    "scale": metadata.scale,
                    "offset": metadata.offset,
                    "same_support_as_earth": source.same_support_as_earth,
                    "license": source.license,
                },
                indent=2,
            )
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--allow-network",
        action="store_true",
        help="allow bounded HTTPS /vsicurl range reads of the verified planetary DEMs",
    )
    parser.add_argument("--plan", action="store_true", help="inspect metadata only; no writes")
    parser.add_argument(
        "--verify",
        action="store_true",
        help="offline verification of the committed manifests and fixtures",
    )
    args = parser.parse_args(argv)
    if args.verify:
        manifests = sorted(DEFAULT_OUTPUT_DIR.glob("*.json"))
        for path in manifests:
            if path.name == "index.json":
                continue
            manifest = verify_planetary_manifest(path, fixture_root=REPO_ROOT)
            print(
                f"[planetary-coarse] verified {path.name}: "
                f"{manifest['counts']['computed_cells']} cells, "
                f"cross_body_ranking={manifest['cross_body_ranking']['enabled']}"
            )
        return 0
    if args.plan:
        _plan(args.allow_network)
        return 0
    if not args.allow_network:
        parser.error("real builds require --allow-network; no source is read implicitly")
    result = _build(allow_network=True)
    print(json.dumps(result["summary"], indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
