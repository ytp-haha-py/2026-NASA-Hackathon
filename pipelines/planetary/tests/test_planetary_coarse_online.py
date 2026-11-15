"""Opt-in metadata/bounded-window check for the planetary coarse pilot.

Run explicitly::

    ANALOG_RUN_NETWORK_TESTS=1 uv run pytest \
        pipelines/planetary/tests/test_planetary_coarse_online.py -q -m network

The test does not write a manifest. It re-reads the same small windows and checks the
verified native support, nodata, planetary CRS and body. No global raster is downloaded.
"""

from __future__ import annotations

import os

import numpy as np
import pytest
from analog_pipelines.planetary_coarse import (
    ANALYSIS_RESOLUTION_M,
    MARS_CTX_JEZERO,
    MARS_MOLA_MEG128,
    MOON_LOLA128,
    inspect_planetary_raster,
    read_metric_window,
)

pytestmark = [
    pytest.mark.network,
    pytest.mark.skipif(
        os.getenv("ANALOG_RUN_NETWORK_TESTS") != "1",
        reason="set ANALOG_RUN_NETWORK_TESTS=1 to run opt-in online checks",
    ),
]


def test_moon_lola128_metadata_and_bounded_window():
    metadata = inspect_planetary_raster(
        MOON_LOLA128.raster_url, source=MOON_LOLA128, allow_remote=True
    )
    assert metadata.body == "Moon"
    assert metadata.native_resolution_m == pytest.approx(236.9012, abs=0.01)
    assert metadata.crs_name == "GCS_Moon_2000"
    assert np.isnan(metadata.nodata) if metadata.nodata is not None else True
    assert metadata.size_bytes is None  # remote object; Content-Length is not stat() size


def test_mars_mola_meg128_metadata_is_planetocentric_463m():
    metadata = inspect_planetary_raster(
        MARS_MOLA_MEG128.url, source=MARS_MOLA_MEG128, allow_remote=True
    )
    assert metadata.body == "Mars"
    assert metadata.native_resolution_m == pytest.approx(463.0, abs=0.1)
    assert metadata.nodata == -32768.0
    assert metadata.scale == 1.0
    assert metadata.offset == 0.0


def test_mars_ctx_window_is_read_at_bounded_size():
    metadata = inspect_planetary_raster(
        MARS_CTX_JEZERO.raster_url, source=MARS_CTX_JEZERO, allow_remote=True
    )
    assert metadata.body == "Mars"
    window = read_metric_window(
        MARS_CTX_JEZERO.raster_url,
        source=MARS_CTX_JEZERO,
        bbox_metric={
            "west": 4_392_500.0 - 2500,
            "south": 1_097_500.0 - 2500,
            "east": 4_392_500.0 + 2500,
            "north": 1_097_500.0 + 2500,
        },
        target_crs=metadata.crs_wkt,
        resolution_m=ANALYSIS_RESOLUTION_M,
        pad_pixels=1,
        allow_remote=True,
    )
    assert window.data.shape == (20, 20)
    assert window.source_window_pixels <= 1_000_000
