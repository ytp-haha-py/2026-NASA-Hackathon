from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MATRIX = ROOT / "docs" / "data-sources" / "d05-illumination-radiation-coverage-matrix.json"
DOC = ROOT / "docs" / "D05_COVERAGE_MATRIX.md"
EARTH_OBS = ROOT / "pipelines" / "pilots" / "d05_earth_power" / "observations.jsonl"
SPICE_OBS = ROOT / "pipelines" / "pilots" / "d05_spice_illumination" / "observations.jsonl"


def _load_jsonl(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def test_d05_coverage_matrix_exists_and_has_required_shape():
    assert MATRIX.exists(), "machine-readable D05 coverage matrix must exist"
    assert DOC.exists(), "D05 coverage matrix documentation must exist"
    data = json.loads(MATRIX.read_text(encoding="utf-8"))
    assert data["schema_version"] == "1.0.0"
    assert data["dimension"]["code"] == "D05"
    assert set(data["dimension"]["subgroups"]) == {"illumination", "ionizing_radiation"}
    assert data["entries"], "body x feature entries are required"
    assert data["time_assumption_contract"], "time/assumption contract evidence is required"
    assert data["representative_queries"]["commands"], "representative queries are required"

    required_entry_keys = {
        "body",
        "subgroup",
        "feature_id",
        "source",
        "version",
        "licence",
        "product",
        "actual_indexed_queryable_coverage",
        "data_kind",
        "status_support",
        "time_support",
        "depth_support",
        "spatial_support",
        "crs",
        "qa",
        "uncertainty",
        "legal_score_filter_roles",
        "gaps",
        "rebuild_instructions",
    }
    bodies = {e["body"] for e in data["entries"]}
    assert bodies == {"Earth", "Moon", "Mars"}
    subgroups = {e["subgroup"] for e in data["entries"]}
    assert subgroups == {"illumination", "ionizing_radiation"}
    for entry in data["entries"]:
        missing = required_entry_keys - set(entry)
        assert not missing, f"entry {entry.get('body')}:{entry.get('feature_id')} missing {sorted(missing)}"
        assert isinstance(entry["gaps"], list)
        assert isinstance(entry["status_support"], list) and entry["status_support"]
        assert entry["rebuild_instructions"]


def test_d05_matrix_separates_illumination_and_radiation_and_no_fabricated_dose():
    data = json.loads(MATRIX.read_text(encoding="utf-8"))
    radiation = [e for e in data["entries"] if e["subgroup"] == "ionizing_radiation"]
    illumination = [e for e in data["entries"] if e["subgroup"] == "illumination"]
    assert radiation, "ionizing radiation subgroup must be represented explicitly"
    assert illumination, "illumination subgroup must be represented explicitly"

    rad_by_body = {e["body"] for e in radiation}
    assert rad_by_body == {"Earth", "Moon", "Mars"}
    for entry in radiation:
        text = json.dumps(entry).lower()
        assert "unsupported" in entry["data_kind"] or "no_committed" in entry["data_kind"] or "evidence" in entry["data_kind"]
        assert "0 indexed/queryable" in entry["actual_indexed_queryable_coverage"] or "0 committed" in entry["actual_indexed_queryable_coverage"]
        assert "gy<->sv" in text or "no gy" in text or "no sv" in text or "without physics" in text or "conversion" in text

    # Committed pilot observations must not contain ionizing dose/flux products.
    for path in (EARTH_OBS, SPICE_OBS):
        for obs in _load_jsonl(path):
            fid = obs.get("feature_id", "")
            unit = obs.get("unit")
            assert "ionizing" not in fid
            assert fid not in {"absorbed_dose_gy", "equivalent_dose_sv"}
            assert unit not in {"Gy", "Sv"}


def test_d05_matrix_matches_committed_pilot_counts_and_bodies():
    data = json.loads(MATRIX.read_text(encoding="utf-8"))
    earth = _load_jsonl(EARTH_OBS)
    spice = _load_jsonl(SPICE_OBS)
    moon = [o for o in spice if o["body"] == "Moon"]
    mars = [o for o in spice if o["body"] == "Mars"]

    assert len(earth) == 10
    assert len(moon) == 8
    assert len(mars) == 10
    assert {o["feature_id"] for o in earth} == {
        "surface_shortwave_irradiance_kwh_m2_day",
        "surface_longwave_irradiance_kwh_m2_day",
    }
    assert {o["feature_id"] for o in spice} == {"solar_incidence_deg", "solar_elevation_deg"}

    # Matrix must report these bounded committed coverages.
    coverage_text = " ".join(e["actual_indexed_queryable_coverage"] for e in data["entries"])
    assert "5 Earth areas" in coverage_text or "5 areas" in coverage_text
    assert "4 Moon areas" in coverage_text
    assert "5 Mars areas" in coverage_text

    doc_text = DOC.read_text(encoding="utf-8")
    assert "28" in doc_text
    assert "committed ionizing radiation dose/flux observations:** 0" in doc_text.lower() or "committed ionizing observations 0" in doc_text.lower()
    assert "illumination and ionizing radiation" in doc_text.lower()


def test_d05_representative_queries_cover_all_bodies():
    data = json.loads(MATRIX.read_text(encoding="utf-8"))
    cmds = data["representative_queries"]["commands"]
    bodies = {c.get("body") for c in cmds}
    assert "Earth" in bodies
    assert "Moon" in bodies
    assert "Mars" in bodies
    assert any("ionizing" in c.get("purpose", "").lower() or "ionizing" in c.get("command", "") for c in cmds)
    for cmd in cmds:
        assert "command" in cmd and cmd["command"].strip()
        assert "purpose" in cmd and cmd["purpose"].strip()
