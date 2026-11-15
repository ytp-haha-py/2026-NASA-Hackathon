import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _load_json(rel: str):
    return json.loads((REPO / rel).read_text())


def test_d02_coverage_matrix_artifact_complete():
    matrix = _load_json("pipelines/geology/d02-coverage-matrix.json")
    assert matrix["artifact"] == "d02-coverage-matrix"
    assert matrix["dimension"] == "D02"
    scope = matrix["scope_and_non_claims"]
    assert scope["global_coverage_claimed"] is False
    assert scope["exhaustive_survey_claimed"] is False
    assert scope["similarity_score_claimed"] is False

    rows = matrix["rows"]
    assert len(rows) == 45  # 15 features x 3 bodies
    bodies = {r["body"] for r in rows}
    assert bodies == {"Earth", "Moon", "Mars"}
    features = {r["feature_id"] for r in rows}
    assert "geologic_unit" in features
    assert "mineral_olivine" in features
    assert "context_olindex3" in features
    assert "geology_analysis_availability" in features

    # all evidence-only, no scoring/filter roles
    for r in rows:
        role = r["legal_score_filter_roles"]
        assert role["score_role"] == "evidence_only"
        assert role["similarity_weighted"] is False
        assert role["hard_filter"] is False
        # required descriptive fields present
        for key in [
            "sources",
            "observation_class",
            "actual_indexed_queryable_coverage",
            "states_present",
            "spatial_support_summary",
            "temporal_support_summary",
            "depth_support_summary",
            "qa_flags",
            "gaps",
            "rebuild_instructions",
        ]:
            assert key in r

    # key bounded realities: Mars context indexed, Moon map indexed, Earth map unsupported
    def row(body, fid):
        return next(r for r in rows if r["body"] == body and r["feature_id"] == fid)

    assert row("Mars", "context_olindex3")["indexed_observation_count"] == 2
    assert row("Moon", "geologic_unit")["indexed_observation_count"] == 1
    assert row("Earth", "geologic_unit")["indexed_observation_count"] == 0
    assert row("Earth", "geologic_unit")["observation_class"] == "unsupported"
    assert row("Earth", "geology_analysis_availability")["indexed_observation_count"] == 15
    # mineral detections unknown, not detected/abundance
    mars_ol = row("Mars", "mineral_olivine")
    assert mars_ol["state_counts"] == {"unknown": 2}


def test_d02_representative_queries_cover_all_bodies():
    q = _load_json("pipelines/geology/representative-queries.json")
    assert q["dimension"] == "D02"
    assert q["global_claim"] is False
    queries = q["queries"]
    by_body = {}
    for item in queries:
        by_body.setdefault(item["body"], 0)
        by_body[item["body"]] += item["result_count"]
        assert item["result_count"] > 0
        for r in item["results"]:
            assert r["score_role"] == "evidence_only"
            assert r["body"] == item["body"]
    assert set(by_body.keys()) == {"Earth", "Moon", "Mars"}

    ids = {item["query_id"] for item in queries}
    assert "Q-EARTH-availability" in ids
    assert "Q-MOON-mapped-units" in ids
    assert "Q-MARS-crism-mineral-detections" in ids


def test_d02_representative_queries_match_typed_export_counts():
    typed = [
        json.loads(line)
        for line in (REPO / "pipelines/geology/typed/observations.jsonl").read_text().splitlines()
        if line.strip()
    ]
    q = _load_json("pipelines/geology/representative-queries.json")

    # selected queries should be subsets consistent with typed export
    def count(body=None, fid=None, prefix=None, area=None):
        n = 0
        for o in typed:
            if body and o["body"] != body:
                continue
            if fid and o["feature_id"] != fid:
                continue
            if prefix and not o["feature_id"].startswith(prefix):
                continue
            if area and o["area_id"] != area:
                continue
            n += 1
        return n

    by_id = {item["query_id"]: item for item in q["queries"]}
    assert by_id["Q-EARTH-availability"]["result_count"] == count(
        body="Earth", fid="geology_analysis_availability"
    )
    assert by_id["Q-MOON-mapped-units"]["result_count"] == count(
        body="Moon", fid="geologic_unit"
    ) + count(body="Moon", fid="geologic_process")
    assert by_id["Q-MARS-crism-mineral-detections"]["result_count"] == count(
        body="Mars", prefix="mineral_"
    )
