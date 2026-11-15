from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MATRIX = ROOT / "docs/data-sources/d03-thermal-coverage-matrix.json"
DOC = ROOT / "docs/D03_THERMAL_COVERAGE.md"
SCRIPT = ROOT / "scripts/d03_thermal_query_evidence.py"
EARTH_PILOT = ROOT / "pipelines/pilots/d03_earth_power/observations.jsonl"
PLANETARY_PILOT = ROOT / "pipelines/pilots/d03_planetary_thermal/observations.jsonl"


def load_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def test_d03_coverage_matrix_exists_and_is_valid() -> None:
    assert MATRIX.exists()
    matrix = json.loads(MATRIX.read_text(encoding="utf-8"))
    assert matrix["dimension_code"] == "D03"
    assert matrix["survey_claim"] == "not_global_not_exhaustive"
    bodies = matrix["bodies"]
    assert set(bodies) == {"Earth", "Moon", "Mars"}
    assert bodies["Earth"]["actual_indexed_queryable_coverage"]["observations"] == 20
    assert bodies["Moon"]["actual_indexed_queryable_coverage"]["observations"] == 36
    assert bodies["Mars"]["actual_indexed_queryable_coverage"]["observations"] == 5
    assert (
        "time-selector query evidence" in " ".join(matrix["rebuild_instructions"]).lower()
        or SCRIPT.exists()
    )


def test_d03_coverage_matrix_matches_committed_pilots() -> None:
    matrix = json.loads(MATRIX.read_text(encoding="utf-8"))
    earth = load_jsonl(EARTH_PILOT)
    planetary = load_jsonl(PLANETARY_PILOT)
    moon = [o for o in planetary if o.get("body") == "Moon"]
    mars = [o for o in planetary if o.get("body") == "Mars"]
    assert (
        len(earth) == matrix["bodies"]["Earth"]["actual_indexed_queryable_coverage"]["observations"]
    )
    assert (
        len(moon) == matrix["bodies"]["Moon"]["actual_indexed_queryable_coverage"]["observations"]
    )
    assert (
        len(mars) == matrix["bodies"]["Mars"]["actual_indexed_queryable_coverage"]["observations"]
    )
    assert {o["score_role"] for o in earth} == {"context_only"}
    assert {o["score_role"] for o in planetary} == {"evidence_only"}
    assert all(o.get("dimension_code") == "D03" for o in earth + planetary)


def test_d03_time_and_semantic_fields_present() -> None:
    planetary = load_jsonl(PLANETARY_PILOT)
    moon = [o for o in planetary if o.get("body") == "Moon"]
    mars = [o for o in planetary if o.get("body") == "Mars"]
    # Moon: LST bins, season/composite semantics, footprint and uncertainty
    lsts = {(o.get("temporal_support") or {}).get("local_solar_time") for o in moon}
    assert lsts == {"00:00-00:15", "12:00-12:15"}
    assert all(
        "southern-summer" in (o.get("temporal_support") or {}).get("notes", "") for o in moon
    )
    assert all((o.get("spatial_support") or {}).get("native_resolution_m") == 240.0 for o in moon)
    assert all((o.get("spatial_support") or {}).get("window_size_m") == 1980.0 for o in moon)
    assert all(o.get("uncertainty") is not None for o in moon)
    # Mars: static/time-independent, no invented LST, footprint and uncertainty
    assert all((o.get("temporal_support") or {}).get("kind") == "static" for o in mars)
    assert all((o.get("temporal_support") or {}).get("local_solar_time") is None for o in mars)
    assert all((o.get("spatial_support") or {}).get("native_resolution_m") == 100.0 for o in mars)
    assert all(
        o.get("uncertainty", {}).get("kind") == "published_range"
        for o in mars
        if o.get("state") == "detected"
    )
    # No fabricated diurnal range
    assert not any(
        o.get("feature_id") in {"diurnal_temperature_range_k", "diurnal_range_k"} for o in planetary
    )


def test_d03_query_evidence_script_passes() -> None:
    assert DOC.exists()
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "RESULT: PASS" in result.stdout
    assert "Earth" in result.stdout and "Moon" in result.stdout and "Mars" in result.stdout
    assert "00:00-00:15" in result.stdout and "12:00-12:15" in result.stdout
    assert "kind=static" in result.stdout
