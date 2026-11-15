from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from analog_pipelines.geology.gsc_polygon import (
    GscPolygonError,
    categories_for_rxtp,
    verify_artifact,
)

ROOT = Path(__file__).resolve().parents[2]
ARTIFACT = ROOT / "pipelines/geology/gsc-world/polygon"


def test_polygon_artifact_is_area_fraction_validated():
    summary = verify_artifact(ARTIFACT)
    assert summary["areas"] == 2
    assert summary["observations"] == 2
    assert summary["by_category"] == {"igneous": 1, "sedimentary": 1}
    assert summary["min_coverage"] > 0.999999
    assert summary["max_coverage"] <= 1.0
    rows = [
        json.loads(line)
        for line in (ARTIFACT / "evidence.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    by_area = {row["area_id"]: row for row in rows}
    atacama = by_area["earth-atacama-desert"]
    lanzarote = by_area["earth-lanzarote"]
    assert atacama["raw"]["runo"] == 9487
    assert atacama["raw"]["rxtps"] == ["SEDIMENT"]
    assert atacama["mapped_categories"] == ["sedimentary"]
    assert lanzarote["raw"]["runo"] == 526
    assert lanzarote["raw"]["rxtps"] == ["VOLCANIC"]
    assert lanzarote["mapped_categories"] == ["igneous"]
    for row in rows:
        assert row["fraction_basis"] == "polygon_intersection_equal_area_laea"
        assert row["coverage_fraction"] > 0.999999
        assert row["source_crs"] == "EPSG:4322"
        assert row["invalid_source_geometries"] == 0
        assert row["repaired_source_geometries"] == 0
        assert '"abundance":' not in json.dumps(row)


def test_rxtp_mapping_is_non_exclusive_and_rejects_aqueous_claims():
    assert categories_for_rxtp(["SEDIMENT"]) == ["sedimentary"]
    assert categories_for_rxtp(["VOLCANIC", "INTRUSIVE"]) == ["igneous"]
    assert categories_for_rxtp(["SEDVOL"]) == ["igneous", "sedimentary"]
    assert categories_for_rxtp(["CRYSTALLINE", "ICE"]) == []
    assert categories_for_rxtp(["UNKNOWN"]) == []


def test_tampered_polygon_artifact_fails_closed(tmp_path: Path):
    copy = tmp_path / "polygon"
    shutil.copytree(ARTIFACT, copy)
    evidence = copy / "evidence.jsonl"
    evidence.write_text(evidence.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(GscPolygonError, match="checksum"):
        verify_artifact(copy)
