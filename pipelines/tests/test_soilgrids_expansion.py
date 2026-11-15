from __future__ import annotations

import json
import math
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RELEASE = ROOT / "pipelines/pilots/d06_earth_soilgrids_expansion_01"
EARTH_MANIFEST = ROOT / "pipelines/earth/manifests/copernicus_glo90_earth.json"

EXPECTED_AREAS = {
    "earth-grand-canyon-r2c2",
    "earth-iceland-r2c2",
    "earth-namib-r2c2",
}
EXPECTED_UNITS = {
    "bdod": "cg cm-3",
    "clay": "g kg-1",
    "sand": "g kg-1",
    "silt": "g kg-1",
    "soc": "dg kg-1",
}


def _observations() -> list[dict]:
    return [
        json.loads(line)
        for line in (RELEASE / "observations.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_expansion_release_is_bounded_complete_and_detected():
    manifest = json.loads((RELEASE / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["scope"] == "d06-earth-expansion-01"
    assert manifest["observation_count"] == 45
    assert manifest["request_count"] == 45
    assert manifest["asset_count"] == 45
    assert manifest["failed_requests"] == []
    assert manifest["observation_depths"] == [[0.0, 0.05]]
    assert manifest["qa"]["groups"] == 15
    assert manifest["qa"]["missing_groups"] == 0
    assert set(manifest["report"]["areas"]) == EXPECTED_AREAS
    assert manifest["request_summary"] == {"succeeded": 45}

    observations = _observations()
    assert len(observations) == 45
    assert {row["area_id"] for row in observations} == EXPECTED_AREAS
    assert {row["body"] for row in observations} == {"Earth"}
    assert {row["dimension_code"] for row in observations} == {"D06"}
    assert {row["state"] for row in observations} == {"detected"}
    assert {row["method_version"] for row in observations} == {"soilgrids-wcs-2.0.0"}
    assert len({row["observation_id"] for row in observations}) == 45

    feature_counts = Counter(row["feature_id"] for row in observations)
    assert len(feature_counts) == 15
    assert set(feature_counts.values()) == {3}

    candidate_ids = {
        candidate["location_id"]
        for candidate in json.loads(EARTH_MANIFEST.read_text(encoding="utf-8"))["candidates"]
    }
    assert EXPECTED_AREAS <= candidate_ids

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


def test_expansion_quantiles_are_attached_to_the_same_area_property_depth():
    observations = _observations()
    groups: dict[tuple[str, str, str], dict[str, float]] = {}
    for row in observations:
        area, property_id, depth, statistic = row["observation_id"].split(":")[1:]
        groups.setdefault((area, property_id, depth), {})[statistic] = row["value"]
    assert len(groups) == 15
    for key, values in groups.items():
        assert set(values) == {"mean", "q05", "q95"}, key
        assert values["q05"] <= values["q95"], key
