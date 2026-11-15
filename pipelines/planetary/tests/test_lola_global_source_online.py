"""Opt-in live checks for the global LOLA source adapter.

Run explicitly::

    ANALOG_RUN_NETWORK_TESTS=1 uv run pytest \
        pipelines/planetary/tests/test_lola_global_source_online.py -q -m network

These tests make anonymous HTTPS range reads only. They never download a global
raster and never write a file. Without ``ANALOG_RUN_NETWORK_TESTS=1`` they skip.
"""

from __future__ import annotations

import math
import os

import pytest
from lola_global_source import (
    PGDA_LDEM64,
    PGDA_LDEM128,
    SVS_LDEM_64,
    inspect_raster,
    read_bounded_window,
)

pytestmark = [
    pytest.mark.network,
    pytest.mark.skipif(
        os.getenv("ANALOG_RUN_NETWORK_TESTS") != "1",
        reason="set ANALOG_RUN_NETWORK_TESTS=1 to run opt-in online checks",
    ),
]


def test_pgda_ldem128_metadata_and_bounded_window() -> None:
    meta = inspect_raster(PGDA_LDEM128, timeout=90.0)
    assert meta.georeferenced is True
    assert meta.crs_name == "GCS_Moon_2000"
    assert meta.radius_m == pytest.approx(1_737_400.0, abs=1.0)
    assert (meta.width, meta.height) == (46080, 23040)
    assert meta.dtype == "float32"
    assert meta.nodata is not None and math.isnan(meta.nodata)
    assert meta.resolution_deg == pytest.approx(0.0078125)
    assert meta.native_resolution_m == pytest.approx(236.901, abs=0.01)
    assert meta.bigtiff is True
    assert meta.accept_ranges is True
    assert "source_coarser_than_90m" in meta.warnings
    assert "no_published_checksum_etag_is_not_a_hash" in meta.warnings

    window = read_bounded_window(
        PGDA_LDEM128,
        center_lon=10.0,
        center_lat=10.0,
        window_m=5_000.0,
        max_window_bytes=2 * 1024 * 1024,
        timeout=90.0,
    )
    assert window.array is not None
    assert window.valid_fraction == 1.0
    assert window.estimated_source_bytes <= 2 * 1024 * 1024
    assert window.window_sha256 is not None
    assert window.min_elevation_m is not None and window.max_elevation_m is not None
    assert -2000.0 < window.min_elevation_m < window.max_elevation_m < 2000.0


def test_pgda_ldem64_metadata() -> None:
    meta = inspect_raster(PGDA_LDEM64, timeout=90.0)
    assert (meta.width, meta.height) == (23040, 11520)
    assert meta.resolution_deg == pytest.approx(0.015625)
    assert meta.native_resolution_m == pytest.approx(473.802, abs=0.01)
    assert meta.bigtiff is True
    assert meta.nodata is not None and math.isnan(meta.nodata)


def test_svs_fallback_has_no_crs_and_documented_resolution() -> None:
    meta = inspect_raster(SVS_LDEM_64, timeout=90.0)
    assert meta.georeferenced is False
    assert meta.crs_wkt is None
    assert meta.nodata is None
    assert meta.origin_lon == -180.0
    assert meta.native_resolution_m == pytest.approx(473.802, abs=0.01)
    assert "no_crs_using_documented_equirectangular_mapping" in meta.warnings
    assert "fallback_source_not_primary_archive" in meta.warnings

    window = read_bounded_window(
        SVS_LDEM_64,
        center_lon=10.0,
        center_lat=10.0,
        window_m=5_000.0,
        max_window_bytes=2 * 1024 * 1024,
        timeout=90.0,
    )
    assert window.valid_fraction == 1.0
    assert window.window_sha256 is not None
    assert window.min_elevation_m is not None and window.max_elevation_m is not None
    assert -2000.0 < window.min_elevation_m < window.max_elevation_m < 2000.0
