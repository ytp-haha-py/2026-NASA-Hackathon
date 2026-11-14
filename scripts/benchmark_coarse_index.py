#!/usr/bin/env python
"""Opt-in coarse-index query benchmark; generates synthetic rows in a temp directory.

Never downloads or stages a source raster. Default row count is intentionally small.
"""

from __future__ import annotations

import argparse
import statistics
import tempfile
import time
from pathlib import Path

from analog_pipelines.coarse_features import COARSE_METHOD_VERSION
from analog_pipelines.coarse_index import (
    CoarseIndexRepository,
    CoarseRow,
    build_index,
    verify_index,
)


def synthetic_rows(count: int) -> list[CoarseRow]:
    rows = []
    for index in range(count):
        rows.append(
            CoarseRow(
                candidate_id=f"bench:{index:08d}",
                grid_id="coarse-grid-1.0.0",
                region_id=f"region-{index % 8}",
                body="Earth",
                west=-10.0 + (index % 100) * 0.06,
                south=30.0 + (index // 100) * 0.05,
                east=-10.0 + (index % 100) * 0.06 + 0.06,
                north=30.0 + (index // 100) * 0.05 + 0.05,
                crs="EPSG:32633",
                area_km2=25.0,
                slope_mean_deg=float(index % 45),
                slope_p90_deg=float(index % 60),
                flat_fraction=(index % 100) / 100,
                valid_fraction=1.0,
                method_version=COARSE_METHOD_VERSION,
                grid_version="coarse-grid-1.0.0",
                dataset_id="synthetic-benchmark",
                source_asset_id="synthetic-benchmark-asset",
                qa_flags=["synthetic_benchmark_not_observation"],
                snapshot_id="synthetic-benchmark",
            )
        )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=10_000)
    args = parser.parse_args()
    if args.rows < 1 or args.rows > 200_000:
        raise SystemExit("--rows must be within 1..200000")
    with tempfile.TemporaryDirectory(prefix="analog-benchmark-") as tmp:
        root = Path(tmp) / "index"
        started = time.perf_counter()
        build_index(synthetic_rows(args.rows), root=root, snapshot_id="synthetic-benchmark")
        build_seconds = time.perf_counter() - started
        verify_index(root)
        repo = CoarseIndexRepository(root)
        timings = []
        for _ in range(5):
            t0 = time.perf_counter()
            repo.search(
                target={
                    "coarse_slope_mean_deg": 10.0,
                    "coarse_slope_p90_deg": 20.0,
                    "coarse_flat_fraction_lt5deg": 0.5,
                },
                weights={"coarse_slope_mean_deg": 1.0, "coarse_slope_p90_deg": 0.5},
                limit=10,
            )
            timings.append(time.perf_counter() - t0)
        size = sum(path.stat().st_size for path in root.rglob("*.parquet"))
        print(
            f"rows={args.rows} build_s={build_seconds:.3f} parquet_bytes={size} "
            f"query_ms_median={statistics.median(timings) * 1000:.2f} "
            f"query_ms_max={max(timings) * 1000:.2f} "
            "note=synthetic not real observation"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
