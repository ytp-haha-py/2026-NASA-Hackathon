"""Bounded bbox queries over the committed coarse Parquet/DuckDB index (Issue #132).

Synthetic rows exercise the boundary contract (normal, empty, antimeridian, region
filter, paging, invalid input). The committed real pilot snapshot proves the query
returns real Geomorpho250 rows with ``total`` computed by SQL, not by loading the
whole index.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest
from analog_pipelines.coarse_features import COARSE_METHOD_VERSION, CoarseExtractionError
from analog_pipelines.coarse_index import CoarseIndexRepository, CoarseRow, build_index

ROOT = Path(__file__).resolve().parents[2]
INDEX = ROOT / "pipelines/coarse/real-pilot/index"
SNAPSHOT = "geomorpho250-real-pilot-20261115"

SYNTHETIC_IDS = [
    "coarse-v1:a-sw",
    "coarse-v1:a-nw",
    "coarse-v1:a-se",
    "coarse-v1:a-ne",
    "coarse-v1:b-west-edge",
    "coarse-v1:b-west-near",
    "coarse-v1:b-interior",
    "coarse-v1:b-east-near",
    "coarse-v1:b-east-edge",
]


def _row(
    candidate_id: str,
    *,
    region_id: str,
    west: float,
    south: float,
    east: float,
    north: float,
) -> CoarseRow:
    return CoarseRow(
        candidate_id=candidate_id,
        grid_id="coarse-grid-1.0.0",
        region_id=region_id,
        body="Earth",
        west=west,
        south=south,
        east=east,
        north=north,
        crs="EPSG:32633",
        area_km2=25.0,
        slope_mean_deg=5.0,
        slope_p90_deg=10.0,
        flat_fraction=0.5,
        valid_fraction=1.0,
        method_version=COARSE_METHOD_VERSION,
        grid_version="coarse-grid-1.0.0",
        dataset_id="synthetic-fixture",
        source_asset_id="synthetic-asset",
        qa_flags=["synthetic_fixture"],
    )


@pytest.fixture(scope="module")
def synthetic_repo(tmp_path_factory: pytest.TempPathFactory) -> CoarseIndexRepository:
    root = tmp_path_factory.mktemp("bbox-index") / "index"
    rows = [
        _row("coarse-v1:a-sw", region_id="region-a", west=10.0, south=20.0, east=10.2, north=20.2),
        _row("coarse-v1:a-se", region_id="region-a", west=10.2, south=20.0, east=10.4, north=20.2),
        _row("coarse-v1:a-nw", region_id="region-a", west=10.0, south=20.2, east=10.2, north=20.4),
        _row("coarse-v1:a-ne", region_id="region-a", west=10.2, south=20.2, east=10.4, north=20.4),
        _row(
            "coarse-v1:b-west-edge",
            region_id="region-b",
            west=-180.0,
            south=-1.0,
            east=-179.5,
            north=-0.5,
        ),
        _row(
            "coarse-v1:b-west-near",
            region_id="region-b",
            west=-179.5,
            south=-1.0,
            east=-179.0,
            north=-0.5,
        ),
        _row(
            "coarse-v1:b-interior",
            region_id="region-b",
            west=100.0,
            south=-1.0,
            east=101.0,
            north=-0.5,
        ),
        _row(
            "coarse-v1:b-east-near",
            region_id="region-b",
            west=179.0,
            south=-1.0,
            east=179.5,
            north=-0.5,
        ),
        _row(
            "coarse-v1:b-east-edge",
            region_id="region-b",
            west=179.5,
            south=-1.0,
            east=180.0,
            north=-0.5,
        ),
    ]
    build_index(rows, root=root, snapshot_id="bbox-synthetic-snapshot")
    return CoarseIndexRepository(root)


@pytest.fixture(scope="module")
def real_repo() -> CoarseIndexRepository:
    return CoarseIndexRepository(INDEX, snapshot_id=SNAPSHOT)


def _overlaps(item: dict, west: float, south: float, east: float, north: float) -> bool:
    if west <= east:
        longitude_overlap = item["west"] <= east and item["east"] >= west
    else:
        longitude_overlap = item["east"] >= west or item["west"] <= east
    return longitude_overlap and item["south"] <= north and item["north"] >= south


def test_normal_bbox_returns_only_intersecting_rows(synthetic_repo):
    result = synthetic_repo.query_bbox(west=10.0, south=20.0, east=10.15, north=20.15)
    assert result["total"] == 1
    assert result["returned"] == 1
    assert [row["candidate_id"] for row in result["rows"]] == ["coarse-v1:a-sw"]
    assert all(_overlaps(row, 10.0, 20.0, 10.15, 20.15) for row in result["rows"])
    assert result["rows"][0]["bbox"] == {
        "west": 10.0,
        "south": 20.0,
        "east": 10.2,
        "north": 20.2,
    }
    assert result["rows"][0]["qa_flags"] == ["synthetic_fixture"]
    assert result["coverage_label"] == "local_coarse_index_not_global_exhaustive"
    assert result["query"]["antimeridian_crossing"] is False
    assert result["query"]["longitude_intervals"] == [{"west": 10.0, "east": 10.15}]


def test_empty_bbox_returns_zero_and_no_rows(synthetic_repo):
    result = synthetic_repo.query_bbox(west=50.0, south=50.0, east=51.0, north=51.0)
    assert result["total"] == 0
    assert result["returned"] == 0
    assert result["rows"] == []
    assert result["next_offset"] is None


def test_antimeridian_query_uses_two_longitude_intervals(synthetic_repo):
    result = synthetic_repo.query_bbox(west=179.0, south=-2.0, east=-179.0, north=0.0)
    assert result["query"]["antimeridian_crossing"] is True
    assert result["query"]["longitude_intervals"] == [
        {"west": 179.0, "east": 180.0},
        {"west": -180.0, "east": -179.0},
    ]
    assert result["total"] == 4
    assert [row["candidate_id"] for row in result["rows"]] == [
        "coarse-v1:b-west-edge",
        "coarse-v1:b-west-near",
        "coarse-v1:b-east-near",
        "coarse-v1:b-east-edge",
    ]
    assert "coarse-v1:b-interior" not in {row["candidate_id"] for row in result["rows"]}
    assert all(_overlaps(row, 179.0, -2.0, -179.0, 0.0) for row in result["rows"])


def test_region_filter_is_exact(synthetic_repo):
    result = synthetic_repo.query_bbox(
        west=-180.0,
        south=-90.0,
        east=180.0,
        north=90.0,
        region_id="region-a",
    )
    assert result["total"] == 4
    assert {row["region_id"] for row in result["rows"]} == {"region-a"}
    crossing = synthetic_repo.query_bbox(
        west=179.0,
        south=-2.0,
        east=-179.0,
        north=0.0,
        region_id="region-b",
    )
    assert crossing["total"] == 4
    missing = synthetic_repo.query_bbox(
        west=-180.0,
        south=-90.0,
        east=180.0,
        north=90.0,
        region_id="missing-region",
    )
    assert missing["total"] == 0


def test_limit_offset_and_next_offset_are_bounded(synthetic_repo):
    world = synthetic_repo.query_bbox(
        west=-180.0, south=-90.0, east=180.0, north=90.0, limit=3, offset=0
    )
    assert world["total"] == len(SYNTHETIC_IDS)
    assert world["returned"] == 3
    assert world["next_offset"] == 3
    assert [row["candidate_id"] for row in world["rows"]] == [
        "coarse-v1:a-sw",
        "coarse-v1:a-nw",
        "coarse-v1:a-se",
    ]

    second = synthetic_repo.query_bbox(
        west=-180.0, south=-90.0, east=180.0, north=90.0, limit=3, offset=3
    )
    assert second["returned"] == 3
    assert second["next_offset"] == 6

    last = synthetic_repo.query_bbox(
        west=-180.0, south=-90.0, east=180.0, north=90.0, limit=3, offset=6
    )
    assert last["returned"] == 3
    assert last["next_offset"] is None

    beyond = synthetic_repo.query_bbox(
        west=-180.0, south=-90.0, east=180.0, north=90.0, limit=3, offset=99
    )
    assert beyond["total"] == len(SYNTHETIC_IDS)
    assert beyond["returned"] == 0
    assert beyond["rows"] == []
    assert beyond["next_offset"] is None


def test_total_is_the_sql_count_not_the_page_size(synthetic_repo):
    result = synthetic_repo.query_bbox(west=-180.0, south=-90.0, east=180.0, north=90.0, limit=2)
    assert result["total"] == len(SYNTHETIC_IDS)
    assert result["returned"] == 2
    assert len(result["rows"]) == 2
    assert result["total"] > result["returned"]


def test_ordering_is_deterministic_by_region_west_south_candidate(synthetic_repo):
    first = synthetic_repo.query_bbox(west=-180.0, south=-90.0, east=180.0, north=90.0, limit=200)
    second = synthetic_repo.query_bbox(west=-180.0, south=-90.0, east=180.0, north=90.0, limit=200)
    first_ids = [row["candidate_id"] for row in first["rows"]]
    assert first_ids == [row["candidate_id"] for row in second["rows"]]
    assert first_ids == SYNTHETIC_IDS
    keys = [
        (row["region_id"], row["west"], row["south"], row["candidate_id"]) for row in first["rows"]
    ]
    assert keys == sorted(keys)


def test_coverage_bbox_uses_sql_aggregates(synthetic_repo):
    normal = synthetic_repo.coverage_bbox(west=10.0, south=20.0, east=10.4, north=20.4)
    assert normal["total"] == 4
    assert normal["region_count"] == 1
    assert normal["regions"] == ["region-a"]
    assert normal["longitude_intervals"][0]["total"] == 4
    assert normal["longitude_intervals"][0]["bbox"] == {
        "west": 10.0,
        "south": 20.0,
        "east": 10.4,
        "north": 20.4,
    }

    crossing = synthetic_repo.coverage_bbox(west=179.0, south=-2.0, east=-179.0, north=0.0)
    assert crossing["total"] == 4
    assert [interval["total"] for interval in crossing["longitude_intervals"]] == [2, 2]
    assert crossing["query"]["antimeridian_crossing"] is True


@pytest.mark.parametrize(
    "bbox",
    [
        {"west": 10.0, "south": 20.0, "east": 10.0, "north": 21.0},
        {"west": 10.0, "south": 20.0, "east": 11.0, "north": 20.0},
        {"west": 10.0, "south": 21.0, "east": 11.0, "north": 20.0},
        {"west": -181.0, "south": 20.0, "east": 11.0, "north": 21.0},
        {"west": 10.0, "south": -91.0, "east": 11.0, "north": 21.0},
        {"west": math.nan, "south": 20.0, "east": 11.0, "north": 21.0},
        {"west": 10.0, "south": 20.0, "east": math.inf, "north": 21.0},
        {"west": 10.0, "south": 20.0, "east": 11.0, "north": math.nan},
    ],
)
def test_invalid_bbox_is_rejected(synthetic_repo, bbox):
    with pytest.raises(CoarseExtractionError) as caught:
        synthetic_repo.query_bbox(**bbox)
    assert caught.value.code == "invalid_bbox"


@pytest.mark.parametrize("limit", [0, -1, 201, True, 1.5])
def test_invalid_limit_is_rejected(synthetic_repo, limit):
    with pytest.raises(CoarseExtractionError) as caught:
        synthetic_repo.query_bbox(west=-180.0, south=-90.0, east=180.0, north=90.0, limit=limit)
    assert caught.value.code == "invalid_limit"


@pytest.mark.parametrize("offset", [-1, True, 1.5])
def test_invalid_offset_is_rejected(synthetic_repo, offset):
    with pytest.raises(CoarseExtractionError) as caught:
        synthetic_repo.query_bbox(west=-180.0, south=-90.0, east=180.0, north=90.0, offset=offset)
    assert caught.value.code == "invalid_offset"


def test_real_sahara_bbox_returns_real_rows_only(real_repo):
    result = real_repo.query_bbox(west=10.0, south=24.0, east=11.0, north=25.0)
    assert result["snapshot_id"] == SNAPSHOT
    assert result["method_version"] == COARSE_METHOD_VERSION
    assert result["total"] == 64
    assert result["returned"] == 50
    assert result["next_offset"] == 50
    assert {row["region_id"] for row in result["rows"]} == {"pilot-sahara"}
    assert all(_overlaps(row, 10.0, 24.0, 11.0, 25.0) for row in result["rows"])
    assert all("real_geomorpho250" in row["qa_flags"] for row in result["rows"])
    assert all(row["source_resolution_m"] == 250.0 for row in result["rows"])
    assert all(row["window_size_m"] == 5000.0 for row in result["rows"])
    assert all(row["dataset_id"] == "geomorpho250-slope-v1.0" for row in result["rows"])

    complete = real_repo.query_bbox(west=10.0, south=24.0, east=11.0, north=25.0, limit=200)
    assert complete["total"] == complete["returned"] == len(complete["rows"]) == 64


def test_real_ocean_bbox_is_empty(real_repo):
    result = real_repo.query_bbox(west=-30.0, south=-30.0, east=-20.0, north=-20.0)
    assert result["total"] == 0
    assert result["returned"] == 0
    assert result["rows"] == []


def test_real_broad_bbox_is_limited_and_reports_the_larger_total(real_repo):
    result = real_repo.query_bbox(west=-180.0, south=-90.0, east=180.0, north=90.0, limit=25)
    assert result["total"] == 168
    assert result["returned"] == 25
    assert len(result["rows"]) == 25
    assert result["total"] > result["returned"]
    assert result["next_offset"] == 25
    again = real_repo.query_bbox(west=-180.0, south=-90.0, east=180.0, north=90.0, limit=25)
    assert [row["candidate_id"] for row in result["rows"]] == [
        row["candidate_id"] for row in again["rows"]
    ]
    keys = [
        (row["region_id"], row["west"], row["south"], row["candidate_id"]) for row in result["rows"]
    ]
    assert keys == sorted(keys)


def test_real_region_filter_is_exact(real_repo):
    result = real_repo.query_bbox(
        west=-180.0,
        south=-90.0,
        east=180.0,
        north=90.0,
        region_id="pilot-alps",
        limit=200,
    )
    assert result["total"] == 48
    assert result["returned"] == 48
    assert {row["region_id"] for row in result["rows"]} == {"pilot-alps"}
