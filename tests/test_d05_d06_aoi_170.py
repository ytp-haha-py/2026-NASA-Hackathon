"""Tests for Goal F D05/D06 AOI evidence records (owned files only)."""

from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
AOI_DIR = REPO_ROOT / "pipelines" / "d05_d06_aoi"


def load_jsonl(path: Path):
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def test_bundle_files_exist():
    for name in [
        "aois.json",
        "plan.json",
        "assets.json",
        "observations.jsonl",
        "requests.jsonl",
        "qa.json",
        "manifest.json",
        "REPORT.json",
    ]:
        assert (AOI_DIR / name).exists(), f"missing {name}"


def test_two_aois_moon_and_mars():
    data = json.loads((AOI_DIR / "aois.json").read_text(encoding="utf-8"))
    aois = data["aois"]
    bodies = {a["body"] for a in aois}
    assert bodies == {"Moon", "Mars"}
    assert len(aois) == 2
    for a in aois:
        assert a["coordinate_frame"] in {"lunar_projected", "mars_projected"}
        # Planetary AOIs must not use Earth EPSG CRS identifiers as their CRS.
        assert not str(a["crs"]).upper().startswith("EPSG:")
        assert a["body"] in {"Moon", "Mars"}


def test_observations_have_real_or_explicit_no_coverage_per_aoi_dimension():
    obs = load_jsonl(AOI_DIR / "observations.jsonl")
    assert len(obs) == 18
    # per AOI + dimension: must have real detected or explicit no_coverage
    for area in ["moon-shackleton-rim-site04-roi1", "mars-jezero-ctx-window-01"]:
        for dim in ["D05", "D06"]:
            subset = [o for o in obs if o["area_id"] == area and o["dimension_code"] == dim]
            assert subset, f"no records for {area} {dim}"
            assert all(o["state"] in {"detected", "no_coverage", "not_detected", "unknown", "not_applicable"} for o in subset)
            assert any(o["state"] in {"detected", "no_coverage"} for o in subset)
    # D05 illumination real detected values for both AOIs
    illum = [o for o in obs if o["feature_id"] in {"solar_incidence_deg", "solar_elevation_deg"}]
    assert len(illum) == 4
    assert all(o["state"] == "detected" and isinstance(o["value"], (int, float)) for o in illum)
    bodies = {o["body"] for o in illum}
    assert bodies == {"Moon", "Mars"}


def test_d05_illumination_and_radiation_separated():
    obs = load_jsonl(AOI_DIR / "observations.jsonl")
    illum = [o for o in obs if o["feature_id"] in {"solar_incidence_deg", "solar_elevation_deg"}]
    rad = [o for o in obs if o.get("feature_id") == "absorbed_dose_gy"]
    assert illum and rad
    assert all("not_ionizing_radiation" in o["quality_flags"] for o in illum)
    assert all(o["state"] == "no_coverage" and o["value"] is None for o in rad)
    assert all("separated_from_solar_illumination" in o["quality_flags"] for o in rad)
    assert all(o["method_version"] != "spice-solar-geometry-1.0.0" or "ionizing" not in o["observation_id"] for o in rad)


def test_d06_no_coverage_no_proxy_flags():
    obs = load_jsonl(AOI_DIR / "observations.jsonl")
    d06 = [o for o in obs if o["dimension_code"] == "D06"]
    assert len(d06) == 12
    assert all(o["state"] == "no_coverage" and o["value"] is None for o in d06)
    for o in d06:
        q = set(o.get("quality_flags") or [])
        assert "no_dem_roughness_inference" in q
        assert "no_coverage_explicit" in q
        assert o["depth_support"]["kind"] in {"unknown", "surface", "depth_interval"}


def test_required_provenance_fields_present():
    obs = load_jsonl(AOI_DIR / "observations.jsonl")
    for o in obs:
        assert o.get("schema_version") == "2.0.0"
        assert o.get("method_version")
        assert o.get("dataset_ids")
        assert o.get("source_asset_ids")
        assert o.get("spatial_support")
        assert o.get("temporal_support")
        assert o.get("depth_support")
        assert "score_role" in o
        assert "uncertainty" in o
        assert "quality_flags" in o
        # state/value consistency
        if o["state"] == "detected":
            assert o["value"] is not None
        else:
            assert o["value"] is None


def test_manifest_and_qa_counts_match():
    obs = load_jsonl(AOI_DIR / "observations.jsonl")
    manifest = json.loads((AOI_DIR / "manifest.json").read_text(encoding="utf-8"))
    qa = json.loads((AOI_DIR / "qa.json").read_text(encoding="utf-8"))
    assert manifest["counts"]["observations"] == len(obs) == qa["counts"]["observations"]
    assert qa["counts"]["by_state"]["detected"] == 4
    assert qa["counts"]["by_state"]["no_coverage"] == 14
    assert manifest["release_version"]


def test_no_global_claims_and_doc_exists():
    doc = (REPO_ROOT / "docs" / "D05_D06_AOI_170.md").read_text(encoding="utf-8")
    assert "global" in doc.lower() or "exhaustive" in doc.lower() or "bounded" in doc.lower()
    assert "no_coverage" in doc
    assert "ionizing" in doc.lower()
