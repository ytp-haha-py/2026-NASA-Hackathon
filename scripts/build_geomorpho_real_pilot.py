#!/usr/bin/env python
"""Build a bounded real Geomorpho250 coarse index for three pilot regions.

The global PANGAEA slope COG is never downloaded in full. Only padded 0.5 degree
windows are range-read through GDAL /vsicurl, converted from raw int16 (scale
0.01) to float32 slope degrees, and written to the external cache directory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import rasterio
from analog_pipelines.coarse_features import COARSE_METHOD_VERSION, extract_coarse_features
from analog_pipelines.coarse_grid import GRID_VERSION, GridRegion, generate_region_grid
from analog_pipelines.coarse_index import CoarseIndexRepository, CoarseRow, build_index
from analog_pipelines.geomorpho import read_projected_window
from rasterio.windows import from_bounds

SOURCE_URL = (
    "https://hs.pangaea.de/Maps/DEM_Geomorpho90m/dtm_slope_merit.dem_m_250m_s0..0cm_2018_v1.0.tif"
)
LICENSE = "CC BY 4.0"
PILOT_REGIONS = [
    GridRegion("pilot-sahara", "Sahara pilot", 10.0, 24.0, 10.4, 24.4),
    GridRegion("pilot-alps", "Alps pilot", 10.0, 45.0, 10.4, 45.4),
    GridRegion("pilot-grand-canyon", "Grand Canyon pilot", -112.2, 35.8, -111.8, 36.2),
]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_remote_window(
    source_url: str, region: GridRegion, destination: Path, *, pad_deg: float = 0.05
) -> dict[str, object]:
    url = "/vsicurl/" + source_url
    env = {
        "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
        "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif",
        "VSI_CACHE": "TRUE",
        "VSI_CACHE_SIZE": "1048576",
    }
    with rasterio.Env(**env), rasterio.open(url) as src:
        window = (
            from_bounds(
                region.west - pad_deg,
                region.south - pad_deg,
                region.east + pad_deg,
                region.north + pad_deg,
                src.transform,
            )
            .round_offsets()
            .round_lengths()
        )
        raw = src.read(1, window=window, masked=True)
        values = np.asarray(raw.astype("float64").filled(np.nan), dtype="float64")
        valid = np.isfinite(values) & (values != float(src.nodata))
        # PANGAEA tags Scale=0.01; rasterio does not expose it through src.scales.
        slope_deg = np.where(valid, values * 0.01, np.nan).astype("float32")
        transform = src.window_transform(window)
        metadata = {
            "source_url": source_url,
            "license": LICENSE,
            "source_crs": str(src.crs),
            "native_resolution_deg": abs(float(src.res[0])),
            "native_resolution_note": "7.5 arc-second (~250 m), not a fixed metric resolution",
            "raw_dtype": src.dtypes[0],
            "scale_tag": 0.01,
            "nodata": src.nodata,
            "window_offsets": [int(window.row_off), int(window.col_off)],
            "window_shape": [int(window.height), int(window.width)],
        }
    destination.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(
        destination,
        "w",
        driver="GTiff",
        height=slope_deg.shape[0],
        width=slope_deg.shape[1],
        count=1,
        dtype="float32",
        crs="EPSG:4326",
        transform=transform,
        nodata=-9999.0,
        compress="deflate",
    ) as dst:
        dst.write(np.where(np.isfinite(slope_deg), slope_deg, -9999.0).astype("float32"), 1)
    metadata.update(
        {
            "local_window": str(destination),
            "sha256": sha256(destination),
            "valid_fraction": float(np.isfinite(slope_deg).mean()),
            "slope_min_deg": float(np.nanmin(slope_deg)),
            "slope_max_deg": float(np.nanmax(slope_deg)),
        }
    )
    return metadata


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", default="var/cache/geomorpho-pilot")
    parser.add_argument("--index", default="pipelines/coarse/real-pilot/index")
    parser.add_argument("--report", default="docs/GEOMORPHO_REAL_PILOT.md")
    parser.add_argument("--snapshot-id", default="geomorpho250-real-pilot-20261115")
    args = parser.parse_args()

    cache = Path(args.cache)
    index_root = Path(args.index)
    source_windows: dict[str, dict[str, object]] = {}
    rows: list[CoarseRow] = []
    grid_cells = 0
    for region in PILOT_REGIONS:
        local_window = cache / f"{region.region_id}.tif"
        metadata = read_remote_window(SOURCE_URL, region, local_window)
        source_windows[region.region_id] = metadata
        cells, _coverage = generate_region_grid(region)
        grid_cells += len(cells)
        for cell in cells:
            window = read_projected_window(
                local_window,
                bbox_metric=cell.bbox_metric,
                target_crs=cell.crs,
                resolution_m=250.0,
                max_pixels=4096,
            )
            features = extract_coarse_features(window.data, window.valid_mask)
            rows.append(
                CoarseRow(
                    candidate_id=cell.candidate_id,
                    grid_id=cell.grid_version,
                    region_id=cell.region_id,
                    body="Earth",
                    west=cell.bbox_wgs84["west"],
                    south=cell.bbox_wgs84["south"],
                    east=cell.bbox_wgs84["east"],
                    north=cell.bbox_wgs84["north"],
                    crs=cell.crs,
                    area_km2=25.0,
                    slope_mean_deg=features["coarse_slope_mean_deg"],
                    slope_p90_deg=features["coarse_slope_p90_deg"],
                    flat_fraction=features["coarse_flat_fraction_lt5deg"],
                    valid_fraction=float(window.valid_mask.mean()),
                    method_version=COARSE_METHOD_VERSION,
                    grid_version=GRID_VERSION,
                    dataset_id="geomorpho250-slope-v1.0",
                    source_asset_id=f"pangaea-slope-{source_windows[region.region_id]['sha256'][:16]}",
                    qa_flags=["real_geomorpho250", "bounded_remote_window", "cc_by_4_0"],
                    window_size_m=5000.0,
                    stride_m=5000.0,
                    source_resolution_m=250.0,
                )
            )
    manifest = build_index(rows, root=index_root, snapshot_id=args.snapshot_id)
    repo = CoarseIndexRepository(index_root)
    search = repo.search(
        target={"coarse_slope_mean_deg": 8.0}, weights={"coarse_slope_mean_deg": 1.0}, limit=3
    )
    report = {
        "source": {
            "url": SOURCE_URL,
            "license": LICENSE,
            "citation": "Amatulli et al. (2019/2020), Geomorpho90m, PANGAEA 899135",
            "access": "bounded /vsicurl range reads only; no full global download",
            "metadata": source_windows,
        },
        "grid": {
            "regions": [region.region_id for region in PILOT_REGIONS],
            "cells": grid_cells,
            "rows": len(rows),
            "window_size_m": 5000,
            "source_resolution_m": 250,
            "method_version": COARSE_METHOD_VERSION,
            "grid_version": GRID_VERSION,
        },
        "snapshot": manifest["snapshot_id"],
        "index_partitions": manifest["partitions"],
        "coverage": manifest["coverage"],
        "sample_query": {
            "target": {"coarse_slope_mean_deg": 8.0},
            "eligible_candidate_count": search["eligible_candidate_count"],
            "results": [
                {
                    "candidate_id": item["candidate_id"],
                    "region_id": item["region_id"],
                    "distance": item["distance"],
                    "slope_mean_deg": item["slope_mean_deg"],
                    "valid_fraction": item["valid_fraction"],
                }
                for item in search["results"]
            ],
        },
        "limitations": [
            "Three 0.4 degree pilot regions, not global coverage.",
            "7.5 arc-second source is ~250 m only near the equator; no fixed metric source grid.",
            "Bilinear reprojection and 5 km aggregation are method-specific and not 90 m terrain metrics.",
            "Global coverage and same-support Moon/Mars coarse references remain unavailable.",
        ],
    }
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        "# Real Geomorpho250 coarse pilot\n\n```json\n"
        + json.dumps(report, indent=2, sort_keys=True)
        + "\n```\n",
        encoding="utf-8",
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
