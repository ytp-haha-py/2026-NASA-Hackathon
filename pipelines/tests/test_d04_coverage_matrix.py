"""Offline checks for the D04 coverage matrix and representative body queries."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MATRIX = ROOT / "docs" / "data-sources" / "d04-water-ice-coverage-matrix.json"
EARTH_OBS = ROOT / "pipelines" / "pilots" / "d04_earth_power" / "observations.jsonl"
PLANET_OBS = ROOT / "pipelines" / "pilots" / "d04_water_ice_planetary" / "observations.jsonl"


def load_jsonl(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def test_d04_coverage_matrix_matches_indexed_releases() -> None:
    matrix = json.loads(MATRIX.read_text(encoding="utf-8"))
    assert matrix["dimension"] == "D04"
    assert matrix["generated_for_issue"] == 138
    assert matrix["totals"]["indexed_observations"] == 75
    assert matrix["totals"]["by_body"] == {"Earth": 20, "Mars": 2, "Moon": 53}

    earth = load_jsonl(EARTH_OBS)
    planet = load_jsonl(PLANET_OBS)
    all_obs = earth + planet
    assert len(all_obs) == 75
    assert Counter(o["body"] for o in all_obs) == Counter({"Earth": 20, "Moon": 53, "Mars": 2})

    rows = matrix["rows"]
    # every indexed body/feature count in matrix matches actual observations
    actual = Counter((o["body"], o["feature_id"]) for o in all_obs)
    for row in rows:
        key = (row["body"], row["feature_id"])
        claimed = row["actual_counts"]["observations"]
        assert claimed == actual.get(key, 0), (key, claimed, actual.get(key, 0))
        for field in (
            "source",
            "version",
            "licence",
            "product",
            "indexed_queryable_coverage",
            "spatial_support",
            "time_support",
            "depth_support",
            "crs",
            "qa",
            "uncertainty",
            "legal_score_filter_roles",
            "gaps",
            "rebuild_instructions",
            "quantity_class",
            "data_kind",
        ):
            assert field in row, (key, field)

    # required separated quantity classes are present
    classes = {r["quantity_class"] for r in rows}
    for required in {
        "liquid_water",
        "ice",
        "soil_moisture",
        "hydrogen_proxy",
        "hydrated_mineral",
        "radar_cpr",
        "ice_detection_claim",
        "subsurface_depth",
    }:
        assert required in classes, required

    # no detection claim is presented as confirmed ice in indexed claims
    claims = [o for o in planet if o["feature_id"] == "water_ice_detection"]
    assert claims
    assert all(o["state"] != "detected" for o in claims)

    roles = [r["legal_score_filter_roles"] for r in rows if r["actual_counts"]["observations"] > 0]
    assert roles and all(not role.get("similarity_eligible") for role in roles)
    assert all(not role.get("filter_eligible") for role in roles)


def test_representative_queries_cover_all_three_bodies() -> None:
    earth = load_jsonl(EARTH_OBS)
    planet = load_jsonl(PLANET_OBS)
    moon = [o for o in planet if o["body"] == "Moon"]
    mars = [o for o in planet if o["body"] == "Mars"]

    assert {o["feature_id"] for o in earth} == {
        "precipitation_mm_day",
        "root_zone_soil_wetness_fraction",
        "snow_depth_cm",
        "surface_soil_wetness_fraction",
    }
    assert len({o["area_id"] for o in earth}) == 5
    atacama_snow = [
        o
        for o in earth
        if o["area_id"] == "earth-atacama-desert" and o["feature_id"] == "snow_depth_cm"
    ][0]
    assert atacama_snow["state"] == "detected"
    assert isinstance(atacama_snow["value"]["values"], list) and atacama_snow["value"]["values"]

    assert "water_equivalent_hydrogen_wt_percent" in {o["feature_id"] for o in moon}
    assert "radar_circular_polarization_ratio" in {o["feature_id"] for o in moon}
    claim_states = Counter(o["state"] for o in moon if o["feature_id"] == "water_ice_detection")
    assert claim_states["unknown"] >= 1
    assert claim_states["detected"] == 0

    assert Counter(o["feature_id"] for o in mars) == Counter(
        {"epithermal_neutron_flux_ratio": 1, "water_ice_detection": 1}
    )
    assert [o for o in mars if o["feature_id"] == "water_ice_detection"][0]["state"] == "unknown"
