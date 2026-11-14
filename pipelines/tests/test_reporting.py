from __future__ import annotations

from pathlib import Path

from analog_pipelines.coarse_features import COARSE_METHOD_VERSION
from analog_pipelines.coarse_index import CoarseRow, build_index
from analog_pipelines.reporting import build_coverage_report


def test_fixture_report_is_explicitly_not_global(tmp_path: Path):
    row = CoarseRow(
        candidate_id="coarse-v1:a",
        grid_id="coarse-grid-1.0.0",
        region_id="pilot",
        body="Earth",
        west=10.0,
        south=45.0,
        east=10.06,
        north=45.05,
        crs="EPSG:32633",
        area_km2=25.0,
        slope_mean_deg=10.0,
        slope_p90_deg=20.0,
        flat_fraction=0.5,
        valid_fraction=1.0,
        method_version=COARSE_METHOD_VERSION,
        grid_version="coarse-grid-1.0.0",
        dataset_id="fixture",
        source_asset_id="fixture",
        qa_flags=["synthetic_fixture"],
        snapshot_id="fixture",
    )
    build_index([row], root=tmp_path / "index", snapshot_id="fixture")
    report = build_coverage_report(tmp_path / "index")
    assert report["global_claim"] is False
    assert report["counts"]["candidates"] == 1
    assert any(
        "not global" in text or "not global-exhaustive" in text for text in report["limitations"]
    )
