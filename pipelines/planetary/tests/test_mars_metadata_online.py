"""Opt-in online metadata check for the Mars provider constants.

No raster is downloaded. Run explicitly:

    ANALOG_RUN_NETWORK_TESTS=1 uv run pytest \
        pipelines/planetary/tests/test_mars_metadata_online.py -q -m network
"""

from __future__ import annotations

import os
import urllib.request

import pytest
from analog_pipelines.acquisition.providers_mars import (
    MOLA_PRODUCT_LABEL_URL,
    MOLA_PRODUCT_LID,
    MOLA_PRODUCT_VERSION,
    THERMAL_TILE_PAGE,
)

pytestmark = [
    pytest.mark.network,
    pytest.mark.skipif(
        os.getenv("ANALOG_RUN_NETWORK_TESTS") != "1",
        reason="set ANALOG_RUN_NETWORK_TESTS=1 to run opt-in online metadata checks",
    ),
]


def _fetch(url: str) -> str:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "planetary-analog/0.1 (+metadata-check)"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        assert response.status == 200
        return response.read(2_000_000).decode("utf-8", errors="replace")


def test_mola_pds4_label_still_matches_asserted_metadata():
    label = _fetch(MOLA_PRODUCT_LABEL_URL)
    assert MOLA_PRODUCT_LID in label
    assert f"<version_id>{MOLA_PRODUCT_VERSION}</version_id>" in label
    assert "<data_type>SignedMSB2</data_type>" in label
    assert "<unit>meter</unit>" in label
    assert "3.7044613374" in label
    assert "<elements>2880</elements>" in label
    assert "<elements>5760</elements>" in label
    assert "Planetocentric" in label
    assert "Equirectangular" in label


def test_usgs_thermal_product_page_still_states_public_domain_and_100m():
    page = _fetch(THERMAL_TILE_PAGE)
    assert "Public domain" in page
    assert "100 meters/pixel" in page
    assert "60" in page
    assert "Fergason" in page
