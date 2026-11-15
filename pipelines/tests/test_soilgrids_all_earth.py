from __future__ import annotations

import json
import math
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RELEASE = ROOT / "pipelines/pilots/d06_earth_soilgrids_all_0_5cm"
EARTH_MANIFEST = ROOT / "pipelines/earth/manifests/copernicus_glo90_earth.json"
EXPECTED_UNITS = {
    "bdod": "cg cm-3",
    "clay": "g kg-1",
    "sand": "g kg-1",
    "silt": "g kg-1",
    "soc": "dg kg-1",
}


def _observations(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in (path / "observations.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_all_earth_expansion_is_complete_and_exactly_joined():
    manifest = json.loads((RELEASE / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["scope"] == "d06-earth-all-0-5cm"
    assert manifest["observation_count"] == 1575
    assert manifest["request_count"] == 1575
    assert manifest["asset_count"] == 1575
    assert manifest["failed_requests"] == []
    assert manifest["observation_depths"] == [[0.0, 0.05]]
    assert manifest["qa"] == {
        "qa_version": "d06-soilgrids-depth-qa-1.0.0",
        "groups": 525,
        "missing_groups": 0,
    }
    assert manifest["request_summary"] == {"succeeded": 1575}

    observations = _observations(RELEASE)
    assert len(observations) == 1575
    areas = {row["area_id"] for row in observations}
    assert len(areas) == 105
    assert {row["body"] for row in observations} == {"Earth"}
    assert {row["dimension_code"] for row in observations} == {"D06"}
    assert {row["state"] for row in observations} == {"detected"}
    assert {row["method_version"] for row in observations} == {"soilgrids-wcs-2.0.0"}
    assert len({row["observation_id"] for row in observations}) == 1575

    feature_counts = Counter(row["feature_id"] for row in observations)
    assert len(feature_counts) == 15
    assert set(feature_counts.values()) == {105}

    candidates = {
        candidate["location_id"]
        for candidate in json.loads(EARTH_MANIFEST.read_text(encoding="utf-8"))["candidates"]
    }
    assert candidates >= areas

    original_areas = {
        row["area_id"]
        for row in _observations(ROOT / "pipelines/pilots/d06_earth_soilgrids_depths")
    }
    expansion_areas = {
        row["area_id"]
        for row in _observations(ROOT / "pipelines/pilots/d06_earth_soilgrids_expansion_01")
    }
    assert areas.isdisjoint(original_areas | expansion_areas)

    for row in observations:
        property_id = row["feature_id"].split("_")[1]
        assert row["unit"] == EXPECTED_UNITS[property_id]
        assert row["value_kind"] == "numeric"
        assert isinstance(row["value"], (int, float)) and not isinstance(row["value"], bool)
        assert math.isfinite(float(row["value"]))
        assert row["depth_support"]["min_m"] == 0.0
        assert row["depth_support"]["max_m"] == 0.05
        assert row["spatial_support"]["bbox_crs"] == "EPSG:4326"
        assert row["spatial_support"]["native_resolution_m"] == 250.0
        assert row["score_role"] == "evidence_only"
        assert "model_prediction_not_measurement" in row["quality_flags"]


def test_all_earth_expansion_covers_the_remaining_indexed_candidates():
    all_earth = {
        candidate["location_id"]
        for candidate in json.loads(EARTH_MANIFEST.read_text(encoding="utf-8"))["candidates"]
    }
    original = {
        row["area_id"]
        for row in _observations(ROOT / "pipelines/pilots/d06_earth_soilgrids_depths")
    }
    expansion = {
        row["area_id"]
        for row in _observations(ROOT / "pipelines/pilots/d06_earth_soilgrids_expansion_01")
    }
    new = {row["area_id"] for row in _observations(RELEASE)}
    assert original | expansion | new == all_earth
    assert len(all_earth) == 113
