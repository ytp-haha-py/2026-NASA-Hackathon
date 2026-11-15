from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from analog_pipelines.geology.gsc_world import (
    GscWorldError,
    assert_no_forbidden_keys,
    mapped_categories,
    measure_bbox,
    parse_feature_info,
    verify_artifact,
)

ROOT = Path(__file__).resolve().parents[2]
ARTIFACT = ROOT / "pipelines/geology/gsc-world"

FEATURE_TEXT = """GetFeatureInfo results:

Layer 'geology'
  Feature 8686:
    gid = '8686'
    cover_id = '0'
    runo_id2 = '1042510'
    agerxtp = 'Cenozoic sedimentary rocks'
    eon = 'Phanerozoic'
    era = 'Cenozoic'
    rxtyp = 'sedimentary rocks'
"""


def test_parse_feature_info_preserves_raw_attributes():
    parsed = parse_feature_info(FEATURE_TEXT)
    assert parsed == {
        "gid": "8686",
        "cover_id": "0",
        "runo_id2": "1042510",
        "agerxtp": "Cenozoic sedimentary rocks",
        "eon": "Phanerozoic",
        "era": "Cenozoic",
        "rxtyp": "sedimentary rocks",
    }


def test_parse_feature_info_treats_no_result_as_gap():
    assert parse_feature_info("GetFeatureInfo results:\n\n  Search returned no results.\n") is None


def test_mapped_categories_are_non_exclusive_and_no_aqueous_claim():
    assert mapped_categories("sedimentary rocks") == ["sedimentary"]
    assert mapped_categories("volcanic rocks") == ["igneous"]
    assert mapped_categories("crystalline metamorphic rocks") == ["metamorphic"]
    assert mapped_categories("some unlisted unit") == []


def test_measure_bbox_reports_sample_fraction_and_gaps():
    feature = {"gid": "1", "cover_id": "0", "runo_id2": "10", "rxtyp": "sedimentary rocks"}
    calls = {"count": 0}

    def query(bbox, i, j):
        calls["count"] += 1
        return feature if calls["count"] <= 3 else None

    records = measure_bbox(
        area_id="earth-test",
        bbox={"west": 0, "south": 0, "east": 1, "north": 1},
        side=5,
        query=query,
    )
    assert len(records) == 1
    record = records[0]
    assert record["state"] == "detected"
    assert record["fraction"] == pytest.approx(3 / 25)
    assert record["coverage_fraction"] == pytest.approx(3 / 25)
    assert record["coverage_state"] == "partial"
    assert record["mapped_categories"] == ["sedimentary"]
    assert record["raw"]["gid"] == "1"
    assert "point_grid_not_polygon_intersection" in record["qa_flags"]


def test_measure_bbox_no_coverage_is_not_zero_filled():
    records = measure_bbox(
        area_id="earth-test",
        bbox={"west": 0, "south": 0, "east": 1, "north": 1},
        side=5,
        query=lambda bbox, i, j: None,
    )
    assert len(records) == 1
    assert records[0]["state"] == "no_coverage"
    assert records[0]["fraction"] is None
    assert records[0]["raw"] is None


def test_forbidden_score_like_keys_are_rejected():
    with pytest.raises(GscWorldError):
        assert_no_forbidden_keys({"abundance": 1})
    assert_no_forbidden_keys({"raw": {"rxtyp": "sedimentary rocks"}})


def test_committed_artifact_verifies():
    summary = verify_artifact(ARTIFACT)
    assert summary["observations"] >= 1
    assert summary["areas"] >= 1
    assert summary["acceptance_status"] == "evidence_only_insufficient_for_area_fraction_acceptance"
    rows = [
        json.loads(line)
        for line in (ARTIFACT / "evidence.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert {row["body"] for row in rows} == {"Earth"}
    assert all(row["license"].startswith("Open Government Licence") for row in rows)
    assert all(row["map_scale"] == "1:35,000,000" for row in rows)
    assert all(
        row["acceptance_status"] == "evidence_only_insufficient_for_area_fraction_acceptance"
        for row in rows
    )
    assert any(row["mapped_categories"] for row in rows)
    manifest = json.loads((ARTIFACT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["acceptance"]["polygon_extract"] == "not_obtained"
    assert all("abundance" not in row for row in rows)


@pytest.mark.network
@pytest.mark.skipif(
    os.environ.get("ANALOG_RUN_NETWORK_TESTS") != "1",
    reason="set ANALOG_RUN_NETWORK_TESTS=1 to query the live GSC WMS",
)
def test_live_wms_returns_a_real_unit():
    from analog_pipelines.geology.gsc_world import query_point

    bbox = {"west": -68.40973566, "south": -23.5089791, "east": -68.39026303, "north": -23.49102032}
    feature = query_point(bbox, 2, 2, side=5)
    assert feature is not None
    assert feature["gid"]
    assert feature["rxtyp"]
