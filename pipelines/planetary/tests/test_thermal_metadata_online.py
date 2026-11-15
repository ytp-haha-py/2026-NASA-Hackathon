"""Opt-in online metadata check for the planetary D03 thermal products.

Only small labels/headers and a single 64 KiB ISIS tile are fetched. No PCP
table is downloaded and no raster is committed. Run explicitly:

    ANALOG_RUN_NETWORK_TESTS=1 uv run pytest \
        pipelines/planetary/tests/test_thermal_metadata_online.py -q -m network
"""

from __future__ import annotations

import os
import urllib.request

import pytest
from analog_pipelines.thermal_planetary import (
    MARS_TI_TILE,
    PCP_BINS,
    PCP_COLLECTION_LID,
)

pytestmark = [
    pytest.mark.network,
    pytest.mark.skipif(
        os.getenv("ANALOG_RUN_NETWORK_TESTS") != "1",
        reason="set ANALOG_RUN_NETWORK_TESTS=1 to run opt-in online metadata checks",
    ),
]


def _fetch(url: str, *, start: int | None = None, end: int | None = None) -> bytes:
    headers = {"User-Agent": "planetary-analog/0.1 (+D03-metadata-check)"}
    if start is not None and end is not None:
        headers["Range"] = f"bytes={start}-{end}"
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=60) as response:
        assert response.status in {200, 206}
        return response.read()


def test_pcp_label_still_matches_published_metadata():
    item = PCP_BINS[0]
    label_url = item.url.removesuffix(".tab") + ".lbl"
    label = _fetch(label_url).decode("ascii", errors="replace")
    assert item.product_id in label
    assert "PRODUCT_TYPE" in label and "PCP" in label
    assert PCP_COLLECTION_LID.split(":")[-1] in label or "DLRE" in label
    assert "240 <m/pix>" in label
    assert "BOLOMETRIC" in label.upper()
    assert "1737.4" in label
    assert "0.25 <hour>" in label


def test_pcp_tab_header_and_row_width_are_stable():
    item = PCP_BINS[0]
    payload = _fetch(item.url, start=0, end=117)
    header, first_row = payload.split(b"\r\n")[:2]
    assert header.strip().split(b",") == [b"x", b"y", b"clon", b"clat", b"tbol"]
    assert len(header) == 59
    assert len(first_row) == 59


def test_isis_tile_label_still_matches_verified_geometry():
    payload = _fetch(MARS_TI_TILE.url, start=0, end=4095)
    text = payload.decode("ascii", errors="replace")
    assert "Object = IsisCube" in text
    assert "Format      = Tile" in text
    assert "TileSamples = 128" in text
    assert "TileLines   = 128" in text
    assert "Type        = Real" in text
    assert "Samples = 35566" in text
    assert "Lines   = 17783" in text
    assert "StartByte   = 65537" in text
    assert "MinimumLongitude   = 60.0" in text
    assert "MaximumLongitude   = 120.0" in text
    assert "PixelResolution    = 100.0" in text


def test_isis_tile_range_is_available_and_64_kib():
    start, end = MARS_TI_TILE.tile_byte_range(0, 0)
    payload = _fetch(MARS_TI_TILE.url, start=start, end=end)
    assert len(payload) == MARS_TI_TILE.tile_bytes == 65_536
