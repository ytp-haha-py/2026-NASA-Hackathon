"""Synthetic local-GeoTIFF round-trip through the importer's crop + feature path.

TEST ONLY: this raster is generated on the fly in ``tmp_path``. It is never written
to ``pipelines/earth/manifests/`` and no value from this file appears in the committed
manifest. The point is to prove the offline code path (WGS84 crop -> shared extractor)
works without network access.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import rasterio
from analog_pipelines.features import extract_features
from copernicus_glo90 import (
    ANALYSIS_RESOLUTION_M,
    Candidate,
    bbox_contains,
    read_source_crop,
)
from rasterio.transform import from_origin

CENTER_LON = 20.0
CENTER_LAT = 10.0
CELL_DEG = 1.0 / 1200.0
SIZE = 100


def write_synthetic_dem(path):
    rows, cols = np.indices((SIZE, SIZE), dtype="float64")
    # A tilted plane plus a gentle ridge, in metres (test-only, arbitrary).
    z = 120.0 + 0.06 * cols * 90.0 + 12.0 * np.sin(rows / 9.0) * np.cos(cols / 11.0)
    west = CENTER_LON - SIZE * CELL_DEG / 2.0
    north = CENTER_LAT + SIZE * CELL_DEG / 2.0
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=SIZE,
        width=SIZE,
        count=1,
        dtype="float32",
        crs="EPSG:4326",
        transform=from_origin(west, north, CELL_DEG, CELL_DEG),
    ) as dst:
        dst.write(z.astype("float32"), 1)
    return path


def test_local_geotiff_roundtrip_through_importer(tmp_path):
    source = write_synthetic_dem(tmp_path / "synthetic-source.tif")
    crop = tmp_path / "synthetic-crop.tif"
    candidate = Candidate(
        "test-only-synthetic", "TEST ONLY synthetic terrain", CENTER_LAT, CENTER_LON
    )

    metadata = read_source_crop(source, candidate, crop, source_label="test-only")
    assert crop.is_file()
    assert metadata["tile_id"] == "Copernicus_DSM_COG_30_N10_00_E020_00_DEM"
    assert metadata["source_crs"] == "EPSG:4326"
    assert metadata["source_resolution_deg"] == pytest.approx([CELL_DEG, CELL_DEG])
    assert metadata["crop_shape"][0] < SIZE
    assert metadata["crop_shape"][1] < SIZE
    assert bbox_contains(metadata["crop_bounds_wgs84"], CENTER_LON, CENTER_LAT)
    assert crop.stat().st_size < 500_000

    result = extract_features(
        crop, center_lonlat=(CENTER_LON, CENTER_LAT), source_label="test-only"
    )
    assert result.analysis_crs == "EPSG:32634"
    assert result.window_size_m == 1980.0
    assert result.resolution_m == ANALYSIS_RESOLUTION_M
    assert result.valid_fraction == pytest.approx(1.0)
    assert set(result.features) == {
        "slope_mean_deg",
        "slope_p90_deg",
        "terrain_relief_p95_p05_m",
        "roughness_std_m",
        "flat_terrain_fraction",
    }
    assert 0.0 < result.features["slope_mean_deg"] < 45.0
    assert result.features["slope_p90_deg"] > 0.0
    assert result.features["terrain_relief_p95_p05_m"] > 0.0
    assert result.features["roughness_std_m"] > 0.0
    assert result.window_checksum.startswith("sha256:")
    assert result.bbox_wgs84 is not None
    assert bbox_contains(result.bbox_wgs84, CENTER_LON, CENTER_LAT)

    # Test-only raster: confirm the committed manifest does not reference this run.
    manifest_text = (
        Path(__file__).resolve().parents[1] / "manifests" / "copernicus_glo90_earth.json"
    ).read_text(encoding="utf-8")
    assert "test-only" not in manifest_text
    assert "earth-test-only" not in manifest_text
