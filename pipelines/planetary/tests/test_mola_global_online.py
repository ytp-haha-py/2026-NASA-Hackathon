"""Opt-in online checks for the official MOLA MEGDR source adapter.

Run explicitly::

    ANALOG_RUN_NETWORK_TESTS=1 uv run pytest \
        pipelines/planetary/tests/test_mola_global_online.py -q -m network

Only small PDS4/PDS3 label reads and bounded raster windows are performed. No
full 128 ppd tile (129,761,280 bytes) and no global mosaic is downloaded.
"""

from __future__ import annotations

import os

import numpy as np
import pytest
from mola_global import (
    MOLA_16PPD_IMAGE_BYTES,
    MOLA_128PPD_IMAGE_BYTES,
    MOLA_GLOBAL_16PPD,
    fetch_checksum_index,
    inspect_remote_product,
    probe_remote,
    published_md5,
    read_window,
    tile_for_product_id,
)

pytestmark = [
    pytest.mark.network,
    pytest.mark.skipif(
        os.getenv("ANALOG_RUN_NETWORK_TESTS") != "1",
        reason="set ANALOG_RUN_NETWORK_TESTS=1 to run opt-in online checks",
    ),
]


def test_128ppd_pds4_label_matches_pinned_adapter() -> None:
    tile = tile_for_product_id("megt44n000hb")
    meta = inspect_remote_product(tile, timeout=90)
    assert meta.logical_identifier == (
        "urn:nasa:pds:mgs_mola_topography_derived:data_meg128:megt44n000hb"
    )
    assert meta.version_id == "1.1"
    assert (meta.lines, meta.samples) == (5632, 11520)
    assert meta.data_type == "SignedMSB2"
    assert meta.unit == "meter"
    assert meta.value_offset_m == 0.0
    assert meta.pixel_resolution_x_m == pytest.approx(463.0576672, abs=1e-9)
    assert meta.upperleft_x_m == pytest.approx(-10668848.652, abs=0.001)
    assert meta.upperleft_y_m == pytest.approx(2607940.782, abs=0.001)
    assert meta.reference_frame == "IAU2000_MARS"


def test_16ppd_pds4_label_matches_pinned_adapter() -> None:
    meta = inspect_remote_product(MOLA_GLOBAL_16PPD, timeout=90)
    assert meta.logical_identifier.endswith(":data_meg016:megt90n000eb")
    assert (meta.lines, meta.samples) == (2880, 5760)
    assert meta.pixel_resolution_x_m == pytest.approx(3704.4613374, abs=1e-9)
    assert meta.upperleft_x_m == pytest.approx(-10668848.652, abs=0.001)
    assert meta.upperleft_y_m == pytest.approx(5334424.326, abs=0.001)
    assert meta.latitude_type == "Planetocentric"
    assert meta.longitude_direction == "Positive East"


def test_128ppd_tile_supports_byte_ranges() -> None:
    tile = tile_for_product_id("megt44n000hb")
    probe = probe_remote(tile, timeout=60)
    assert probe.label_status == 200
    assert probe.image_bytes == MOLA_128PPD_IMAGE_BYTES
    assert probe.accept_ranges is True
    assert probe.range_verified is True


def test_16ppd_global_tile_supports_byte_ranges() -> None:
    probe = probe_remote(MOLA_GLOBAL_16PPD, timeout=60)
    assert probe.image_bytes == MOLA_16PPD_IMAGE_BYTES
    assert probe.range_verified is True


def test_bounded_128ppd_window_at_jezero_is_real_and_small() -> None:
    window = read_window(78.05, 18.45, 78.12, 18.52, ppd=128, timeout=120)
    assert window.tile.product_id == "megt44n000hb"
    assert window.shape == (10, 10)
    assert window.valid_fraction == 1.0
    assert window.estimated_read_bytes == 10 * 11520 * 2 + 128 * 1024
    assert -3000.0 < float(np.nanmin(window.data)) < 0.0
    assert -3000.0 < float(np.nanmax(window.data)) < 0.0
    # GDAL's rounded PDS3 MAP_SCALE shifts the origin by ~866 m east, ~138 m north.
    assert abs(window.georeference_delta["origin_east_m"]) > 800.0
    assert abs(window.georeference_delta["origin_north_m"]) > 100.0
    assert window.georeference_override is True


def test_bounded_16ppd_global_window_at_the_north_pole() -> None:
    window = read_window(10.0, 80.0, 20.0, 89.0, ppd=16, timeout=120)
    assert window.tile.product_id == "megt90n000eb"
    assert window.shape == (144, 160)
    assert window.valid_fraction == 1.0
    assert window.estimated_read_bytes == 144 * 5760 * 2 + 128 * 1024
    assert float(np.nanmin(window.data)) < float(np.nanmax(window.data))


def test_observation_counts_keep_zero_as_a_real_count() -> None:
    window = read_window(78.05, 18.45, 78.12, 18.52, ppd=128, role="count", timeout=120)
    assert window.tile.product_id == "megc44n000hb"
    assert window.valid_fraction == 1.0  # finite counts, including 0
    assert float(np.nanmin(window.data)) == 0.0
    # The count array includes bins with no MOLA shot; do not claim 100% observed.
    assert float(np.mean(window.data > 0.0)) < 1.0


def test_published_md5_index_covers_the_128ppd_tile() -> None:
    tile = tile_for_product_id("megt44n000hb")
    checksums = fetch_checksum_index(timeout=90)
    digest = published_md5(tile, checksums=checksums)
    assert digest == "904CAEA41A1DB3A2450CC9497B21C3CC"
