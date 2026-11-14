#!/usr/bin/env python3
"""Import five fixed, disjoint Jezero CTX windows in the raster's Mars frame.

The only downloadable object is the requested ~90 MB mosaic (100 MB hard cap).
Names are coordinate windows, not unverified geological interpretations.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

import rasterio
from analog_api.domain.manifest import Manifest
from analog_pipelines.features import extract_features, make_observations
from analog_pipelines.paths import data_dir
from pyproj import CRS

URL = "https://planetarymaps.usgs.gov/mosaic/mars2020_trn/CTX/ScienceInvestigationMaps_JPL/M20_JezeroCrater_CTXDEM_20m.tif"
PAGE = "https://astrogeology.usgs.gov/search/map/mars_2020_science_investigation_ctx_dem_mosaic"
DATASET_ID = "mars2020-jezero-ctx-dem-20m"
MAX_BYTES = 100_000_000
# Metric coordinates in Jezero_Crater_Projection; 10+ km separation.
CENTRES = (
    (4350000.0, 1080000.0),
    (4360000.0, 1090000.0),
    (4370000.0, 1100000.0),
    (4380000.0, 1110000.0),
    (4390000.0, 1120000.0),
)


def validate_source(src):
    if not src.crs:
        raise ValueError("Mars source missing CRS")
    crs = CRS(src.crs)
    if (
        not crs.is_projected
        or not crs.axis_info
        or crs.axis_info[0].unit_name.lower() not in {"metre", "meter"}
    ):
        raise ValueError("Mars source must have projected metre coordinates")
    if not 3_390_000 <= crs.ellipsoid.semi_major_metre <= 3_400_000:
        raise ValueError("Source is not a Mars CRS")
    if any(abs(r - 20) > 0.01 for r in src.res):
        raise ValueError("Expected 20 m CTX source")
    if src.scales != (1.0,) or src.offsets != (0.0,):
        raise ValueError("Unverified vertical scale/offset")
    return crs


def download(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = data_dir("tmp")
    staging.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(staging).free < MAX_BYTES * 2:
        raise ValueError("Insufficient free space for bounded CTX download")
    fd, temporary_name = tempfile.mkstemp(prefix="jezero-", suffix=".tif.part", dir=staging)
    import os

    os.close(fd)
    temporary = Path(temporary_name)
    # Only promote a complete successful transfer; no partial file is a cache hit.
    with urllib.request.urlopen(URL, timeout=40) as response, temporary.open("wb") as dst:
        total = 0
        while block := response.read(1024 * 1024):
            total += len(block)
            if total > MAX_BYTES:
                raise ValueError("CTX object exceeds 100 MB budget")
            dst.write(block)
    shutil.move(str(temporary), path)


def build(source: Path, centres=CENTRES):
    with rasterio.open(source) as src:
        crs = validate_source(src)
    with source.open("rb") as stream:
        checksum = "sha256:" + hashlib.file_digest(stream, "sha256").hexdigest()
    now = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    dataset = dict(
        dataset_id=DATASET_ID,
        provider="USGS Astrogeology Science Center / NASA Mars Missions",
        product_id="M20_JezeroCrater_CTXDEM_20m",
        title="Mars 2020 Science Investigation CTX DEM Mosaic",
        url=URL,
        license="public domain",
        license_url=PAGE,
        access_timestamp=now,
        source_crs=crs.to_wkt(),
        native_resolution_m=20.0,
        processing_version="jezero-ctx-import-1.0.0",
        checksum=checksum,
        source_kind="martian_dem",
        notes="20 m stereo-derived mosaic; official product page: DEMs localized vertically to MOLA. "
        "Source GeoTIFF uses Mars_2000_Sphere radius 3396190 m; catalog page lists 3396160 m. "
        "Retain authoritative raster CRS without substituting Earth WGS84. Elevation in metres; "
        "no absolute elevations compared across bodies. Horizontal accuracy: Accurate to Control Net; "
        "per-window vertical uncertainty not verified. Mosaic seams and stereo artifacts may affect metrics. "
        "Dataset checksum covers full source GeoTIFF. Source redirects to USGS public S3. "
        "Citation/product documentation: " + PAGE,
    )
    refs = []
    for i, center in enumerate(centres, 1):
        result = extract_features(str(source), center_xy=center, body="Mars", source_label=URL)
        refs.append(
            dict(
                reference_id=f"mars-jezero-ctx-window-{i:02d}",
                body="Mars",
                display_name=f"Jezero CTX coordinate window {i:02d}",
                dataset_id=DATASET_ID,
                lat=result.center_lat,
                lon=result.center_lon,
                bbox=None,
                window_size_m=1980.0,
                analysis_resolution_m=90.0,
                crs=crs.to_wkt(),
                valid_fraction=result.valid_fraction,
                quality_flags=["stereo_dem_mosaic", "vertical_uncertainty_not_verified"],
                notes=f"Fixed source-CRS centre X={center[0]} m Y={center[1]} m; "
                "diagonal sampling transect at 10 km X/Y increments; no landform label inferred. "
                "22x22 bilinear 90 m grid; Horn slope uses complete 3x3 neighborhoods only. "
                f"window_checksum={result.window_checksum}.",
                features=make_observations(result.features, valid_fraction=result.valid_fraction),
            )
        )
    doc = dict(
        manifest_version="1.0.0",
        contract_version="1.0.0",
        generated_by="pipelines/planetary/ctx_import.py",
        generated_at=now,
        data_kind="observed",
        datasets=[dataset],
        candidates=[],
        references=refs,
    )
    Manifest.model_validate(doc)
    return doc


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=None)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("pipelines/planetary/manifests/ctx_mars_references.json"),
    )
    args = parser.parse_args()
    args.source = args.source or data_dir("raw") / "mars" / "M20_JezeroCrater_CTXDEM_20m.tif"
    try:
        if not args.source.exists():
            download(args.source)
        doc = build(args.source)
        import jsonschema

        jsonschema.validate(
            doc, json.loads(Path("contracts/pipeline-manifest.schema.json").read_text())
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(doc, indent=2) + "\n")
        print(
            f"Wrote {len(doc['references'])} observed Mars windows; no failed window substituted."
        )
    except Exception as exc:
        print(f"FAILED USGS CTX: {exc}; no manifest written", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
