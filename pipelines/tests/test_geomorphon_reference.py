from __future__ import annotations

import json
from pathlib import Path

ARTIFACT = Path(__file__).resolve().parents[2] / "docs/terrain-geomorphon-reference.json"


def _payload() -> dict:
    return json.loads(ARTIFACT.read_text(encoding="utf-8"))


def test_buffered_geomorphon_reproduces_full_reference_core():
    payload = _payload()
    buffered = payload["results"]["buffered_46x46"]
    assert buffered["window_size"] == 46
    assert buffered["valid_cells"] == 44 * 44  # one-cell Whitebox edge band
    assert buffered["forms_comparable"] == 22 * 22
    assert buffered["ternary_comparable"] == 22 * 22
    assert buffered["forms_agreement"] == 1.0
    assert buffered["ternary_agreement"] == 1.0


def test_normal_window_loses_edge_support_and_ternary_agreement():
    payload = _payload()
    normal = payload["results"]["normal_22x22"]
    assert normal["valid_cells"] == 400
    assert normal["valid_cells"] < 22 * 22
    assert normal["ternary_agreement"] < 1.0
    assert normal["ternary_equal"] < normal["ternary_comparable"]


def test_reference_is_independent_and_not_a_production_claim():
    payload = _payload()
    assert "WhiteboxTools" in payload["reference"]["implementation"]
    assert payload["reference"]["parameters"]["search"] == 12
    assert payload["reference"]["parameters"]["threshold_degrees"] == 1.0
    assert payload["dem"]["synthetic"] is True
    limitations = " ".join(payload["limitations"]).lower()
    assert "synthetic" in limitations
    assert "no geomorphon feature" in limitations
