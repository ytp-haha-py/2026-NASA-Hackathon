from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RELEASE = ROOT / "pipelines/pilots/d06_moon_mars_regolith_evidence"
MATRIX = ROOT / "docs/D06_COVERAGE_MATRIX.json"

EXPECTED_FEATURES = {
    "particle_size_distribution",
    "bulk_density_kg_m3",
    "porosity_fraction",
    "cohesion_kpa",
    "friction_angle_deg",
    "bearing_strength_kpa",
}


def _load_lines(path: Path) -> list[dict]:
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def test_moon_mars_evidence_release_is_explicit_no_coverage():
    manifest = json.loads((RELEASE / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["scope"] == "d06-moon-mars-regolith-evidence-v1"
    assert manifest["observation_count"] == 12
    assert manifest["asset_count"] == 12
    assert manifest["report"]["by_body"] == {"Moon": 6, "Mars": 6}
    assert manifest["report"]["by_state"] == {"no_coverage": 12}
    assert manifest["report"]["by_feature"] == {f: 2 for f in EXPECTED_FEATURES}

    observations = _load_lines(RELEASE / "observations.jsonl")
    assert len(observations) == 12
    assert {o["body"] for o in observations} == {"Moon", "Mars"}
    assert {o["dimension_code"] for o in observations} == {"D06"}
    assert {o["state"] for o in observations} == {"no_coverage"}
    assert {o["value"] for o in observations} == {None}
    assert {o["score_role"] for o in observations} == {"evidence_only"}
    assert {o["feature_id"] for o in observations} == EXPECTED_FEATURES
    for o in observations:
        assert o["area_id"]
        assert o["dataset_ids"] and o["source_asset_ids"]
        assert "no_coverage_explicit" in o["quality_flags"]
        assert "no_dem_roughness_inference" in o["quality_flags"]
        assert "no_worldcover_inference" in o["quality_flags"]
        assert "no_earth_transfer" in o["quality_flags"]
        assert o["spatial_support"]["crs"] in {"IAU_2015:30100", "IAU_2015:49900"}
        assert not str(o["spatial_support"]["crs"]).upper().startswith("EPSG:")

    plan = json.loads((RELEASE / "plan.json").read_text(encoding="utf-8"))
    assert len(plan["tasks"]) == 12
    frames = {t["area"]["coordinate_frame"] for t in plan["tasks"]}
    assert frames == {"lunar_planetocentric", "mars_planetocentric"}


def test_d06_coverage_matrix_present_and_covers_required_bodies():
    matrix = json.loads(MATRIX.read_text(encoding="utf-8"))
    assert matrix["dimension"] == "D06"
    assert matrix["global_claim"] is False
    assert set(matrix["bodies"].keys()) == {"Earth", "Moon", "Mars"}
    earth = matrix["bodies"]["Earth"]
    assert "113/113" in earth["soil_clay_0_5cm_mean"]["actual_indexed_queryable_coverage"]
    for body in ("Moon", "Mars"):
        assert "particle_size_distribution" in matrix["bodies"][body]
        assert matrix["bodies"][body]["regolith_thickness_m"]["value_semantics"] == "unsupported"
    assert "representative_queries" in matrix
    assert "Earth" in matrix["representative_queries"]
    assert "Moon" in matrix["representative_queries"]
    assert "Mars" in matrix["representative_queries"]
