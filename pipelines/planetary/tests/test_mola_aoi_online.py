"""Opt-in online reproduction of the committed Mars MOLA AOI evidence.

Run explicitly::

    ANALOG_RUN_NETWORK_TESTS=1 uv run pytest \
        pipelines/planetary/tests/test_mola_aoi_online.py -q -m network

The test performs the same three bounded ``/vsicurl`` reads as the committed
``pipelines/planetary/evidence/mars_mola_aoi_evidence.json`` document and compares
every scientific value. It writes no manifest and downloads no global tile.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from mola_aoi import PRESETS, analyze_mola_aoi, compare_with_evidence

pytestmark = [
    pytest.mark.network,
    pytest.mark.skipif(
        os.getenv("ANALOG_RUN_NETWORK_TESTS") != "1",
        reason="set ANALOG_RUN_NETWORK_TESTS=1 to run opt-in online checks",
    ),
]

ROOT = Path(__file__).resolve().parents[3]
EVIDENCE = ROOT / "pipelines/planetary/evidence/mars_mola_aoi_evidence.json"


def test_three_presets_reproduce_committed_evidence():
    evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    expected = {item["label"]: item for item in evidence["aois"]}
    for preset in PRESETS:
        observed = analyze_mola_aoi(preset.bbox, label=preset.label, allow_remote=True)
        assert observed["status"] == "complete"
        assert compare_with_evidence(observed, expected[preset.label])
