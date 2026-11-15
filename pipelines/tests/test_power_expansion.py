from __future__ import annotations

import json
import math
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EARTH_MANIFEST = ROOT / "pipelines/earth/manifests/copernicus_glo90_earth.json"
RELEASES = {
    "D03": ROOT / "pipelines/pilots/d03_earth_power_expansion_01",
    "D04": ROOT / "pipelines/pilots/d04_earth_power_expansion_01",
    "D05": ROOT / "pipelines/pilots/d05_earth_power_expansion_01",
}
EXPECTED_COUNTS = {"D03": 12, "D04": 12, "D05": 6}
EXPECTED_FEATURES = {
    "D03": {
        "surface_air_temperature_2m_c",
        "surface_air_temperature_2m_max_c",
        "surface_air_temperature_2m_min_c",
        "surface_skin_temperature_c",
    },
    "D04": {
        "precipitation_mm_day",
        "root_zone_soil_wetness_fraction",
        "snow_depth_cm",
        "surface_soil_wetness_fraction",
    },
    "D05": {
        "surface_longwave_irradiance_kwh_m2_day",
        "surface_shortwave_irradiance_kwh_m2_day",
    },
}
EXPECTED_AREAS = {
    "earth-grand-canyon-r2c2",
    "earth-iceland-r2c2",
    "earth-namib-r2c2",
}


def _observations(release: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in (release / "observations.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_power_expansion_releases_are_real_bounded_context_ingest():
    candidates = {
        candidate["location_id"]
        for candidate in json.loads(EARTH_MANIFEST.read_text(encoding="utf-8"))["candidates"]
    }
    for dimension, release in RELEASES.items():
        manifest = json.loads((release / "manifest.json").read_text(encoding="utf-8"))
        observations = _observations(release)
        assert manifest["observation_count"] == EXPECTED_COUNTS[dimension]
        assert manifest["asset_count"] == 3
        assert len(manifest["tasks"]) == 3
        assert manifest["raw"] == []
        assert len(observations) == EXPECTED_COUNTS[dimension]
        assert {row["area_id"] for row in observations} == EXPECTED_AREAS
        assert {row["feature_id"] for row in observations} == EXPECTED_FEATURES[dimension]
        assert {row["body"] for row in observations} == {"Earth"}
        assert {row["dimension_code"] for row in observations} == {dimension}
        assert {row["state"] for row in observations} == {"detected"}
        assert {row["score_role"] for row in observations} == {"context_only"}
        assert {row["method_version"] for row in observations} == {"nasa-power-daily-1.0.0"}
        assert candidates >= EXPECTED_AREAS
        counts = Counter(row["feature_id"] for row in observations)
        assert set(counts.values()) == {len(EXPECTED_AREAS)}
        for row in observations:
            payload = row["value"]
            assert isinstance(payload, dict)
            assert payload["missing_count"] == 0
            assert payload["timestamps"] == [
                "2024-01-01",
                "2024-01-02",
                "2024-01-03",
                "2024-01-04",
                "2024-01-05",
                "2024-01-06",
                "2024-01-07",
            ]
            assert len(payload["values"]) == 7
            assert all(isinstance(value, (int, float)) for value in payload["values"])
            assert all(math.isfinite(float(value)) for value in payload["values"])
            assert row["spatial_support"]["source_resolution_m"] == 50000.0
            assert row["spatial_support"]["crs"] == "EPSG:4326"
            assert (
                "not a local" in row["spatial_support"]["notes"].lower()
                or "coarse" in row["spatial_support"]["notes"].lower()
            )


def test_power_expansion_does_not_overlap_existing_areas():
    existing = {
        row["area_id"]
        for release in (
            ROOT / "pipelines/pilots/d03_earth_power",
            ROOT / "pipelines/pilots/d04_earth_power",
            ROOT / "pipelines/pilots/d05_earth_power",
        )
        for row in _observations(release)
    }
    assert existing.isdisjoint(EXPECTED_AREAS)
