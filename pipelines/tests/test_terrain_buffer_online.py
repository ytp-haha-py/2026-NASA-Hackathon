"""Opt-in online check of a bounded real Copernicus GLO-90 buffered read.

Run explicitly with:

    RUN_ONLINE_TERRAIN_BUFFER=1 uv run pytest pipelines/tests/test_terrain_buffer_online.py -q

The default test run never touches the network. This is a method/reader check, not
an observation, not a coverage claim and not a similarity change.
"""

from __future__ import annotations

import os

import numpy as np
import pytest
from analog_pipelines.terrain_buffer import read_metric_window
from pyproj import CRS, Transformer

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_ONLINE_TERRAIN_BUFFER") != "1",
    reason="set RUN_ONLINE_TERRAIN_BUFFER=1 to allow bounded Copernicus HTTPS range reads",
)

URL = (
    "https://copernicus-dem-90m.s3.amazonaws.com/"
    "Copernicus_DSM_COG_30_N37_00_W007_00_DEM/"
    "Copernicus_DSM_COG_30_N37_00_W007_00_DEM.tif"
)


def _utm(lon: float, lat: float) -> CRS:
    zone = int((lon + 180.0) // 6.0) + 1
    return CRS.from_epsg((32600 if lat >= 0 else 32700) + zone)


def test_online_copernicus_normal_crop_is_the_buffered_core():
    lon, lat = -6.55, 37.69
    dst_crs = _utm(lon, lat)
    x, y = Transformer.from_crs("EPSG:4326", dst_crs, always_xy=True).transform(lon, lat)
    normal = read_metric_window(
        "/vsicurl/" + URL, dst_crs=dst_crs, center_xy=(x, y), buffer_cells=0
    )
    buffered = read_metric_window(
        "/vsicurl/" + URL, dst_crs=dst_crs, center_xy=(x, y), buffer_cells=12
    )
    assert normal.grid_cells == 22
    assert buffered.grid_cells == 46
    assert np.isfinite(normal.elevation).mean() >= 0.8
    np.testing.assert_allclose(normal.elevation, buffered.core(), equal_nan=True)
