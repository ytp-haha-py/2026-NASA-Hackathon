from __future__ import annotations

from pathlib import Path

from analog_pipelines.coarse_index import CoarseIndexRepository, verify_index

ROOT = Path(__file__).resolve().parents[2]
INDEX = ROOT / "pipelines/coarse/real-pilot/index"
SNAPSHOT = "geomorpho250-real-pilot-20261115"


def test_committed_real_geomorpho_pilot_index_is_verifiable():
    manifest = verify_index(INDEX, snapshot_id=SNAPSHOT)
    assert manifest["row_count"] == 168
    assert manifest["method_version"] == "coarse-terrain-250m-1.0.0"
    assert manifest["grid_version"] == "coarse-grid-1.0.0"
    assert manifest["coverage"]["regions"] == [
        "pilot-alps",
        "pilot-grand-canyon",
        "pilot-sahara",
    ]
    assert manifest["coverage"]["label"] == "local_coarse_index_not_global_exhaustive"
    assert len(manifest["partitions"]) == 3


def test_committed_real_coarse_index_queries_are_bounded_and_deterministic():
    repo = CoarseIndexRepository(INDEX, snapshot_id=SNAPSHOT)
    result = repo.search(
        target={"coarse_slope_mean_deg": 8.0},
        weights={"coarse_slope_mean_deg": 1.0},
        limit=5,
    )
    assert result["indexed_candidate_count"] == 168
    assert result["eligible_candidate_count"] == 168
    assert len(result["results"]) == 5
    distances = [item["distance"] for item in result["results"]]
    assert distances == sorted(distances)
    assert all(item["region_id"].startswith("pilot-") for item in result["results"])
    again = repo.search(
        target={"coarse_slope_mean_deg": 8.0},
        weights={"coarse_slope_mean_deg": 1.0},
        limit=5,
    )
    assert [item["candidate_id"] for item in result["results"]] == [
        item["candidate_id"] for item in again["results"]
    ]
