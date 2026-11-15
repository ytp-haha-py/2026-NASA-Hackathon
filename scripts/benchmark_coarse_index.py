#!/usr/bin/env python
"""Synthetic coarse-index capacity benchmark (Issue #70).

This measures the *software* cost of building and querying the partitioned Parquet
index. It never downloads, stages, or claims real source coverage. Every row is
labelled ``synthetic_benchmark_not_observation`` and the report says so explicitly.

What is measured:

* build seconds, Parquet bytes/file, bytes per row, peak RSS and process CPU;
* index verification seconds (SHA-256 + row/duplicate checks);
* query p50/p95/p99 for top-K, region filter, and offset pagination;
* query plans and partition counts for the chosen synthetic row count.

Run the two reference sizes separately (do not average them):

    uv run python scripts/benchmark_coarse_index.py --rows 100000 --json-out /tmp/coarse-100k.json
    uv run python scripts/benchmark_coarse_index.py --rows 1000000 --json-out /tmp/coarse-1m.json

Synthetic timings are not real global performance. Real capacity depends on the
authorized Geomorpho250 source, storage hardware, and the actual candidate count.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import platform
import resource
import sqlite3
import statistics
import tempfile
import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
from analog_pipelines.coarse_features import COARSE_METHOD_VERSION
from analog_pipelines.coarse_index import (
    CoarseIndexRepository,
    CoarseRow,
    build_index,
    verify_index,
)

BENCHMARK_VERSION = "coarse-benchmark-2.0.0"
LABEL = "synthetic_benchmark_not_real_data"
ROWS_PER_PARTITION = 10_000
REGIONS = 8
DEFAULT_ITERATIONS = 20


def synthetic_rows(count: int, *, regions: int = REGIONS) -> Iterator[CoarseRow]:
    """Yield clearly labelled synthetic rows without materialising the whole set."""
    for index in range(count):
        column = index % 360
        row = index // 360
        west = -180.0 + column * 0.1
        south = -80.0 + (row % 1600) * 0.1
        yield CoarseRow(
            candidate_id=f"bench:{index:08d}",
            grid_id="coarse-grid-1.0.0",
            region_id=f"region-{index % regions}",
            body="Earth",
            west=west,
            south=south,
            east=west + 0.1,
            north=south + 0.1,
            crs="EPSG:32633",
            area_km2=25.0,
            slope_mean_deg=(index * 7 % 4500) / 100.0,
            slope_p90_deg=(index * 11 % 6000) / 100.0,
            flat_fraction=(index % 1000) / 1000.0,
            valid_fraction=1.0,
            method_version=COARSE_METHOD_VERSION,
            grid_version="coarse-grid-1.0.0",
            dataset_id="synthetic-benchmark",
            source_asset_id="synthetic-benchmark-asset",
            qa_flags=["synthetic_benchmark_not_observation"],
        )


def _resource_snapshot() -> dict[str, float]:
    usage = resource.getrusage(resource.RUSAGE_SELF)
    return {
        "peak_rss_mb": usage.ru_maxrss / 1024.0,
        "cpu_seconds": usage.ru_utime + usage.ru_stime,
    }


def _memory_total_mb() -> float | None:
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("MemTotal:"):
                return int(line.split()[1]) / 1024.0
    except OSError:
        return None
    return None


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return float("nan")
    ordered = sorted(values)
    rank = max(1, math.ceil(percentile / 100.0 * len(ordered)))
    return ordered[min(rank - 1, len(ordered) - 1)]


def timing_stats(samples_ms: list[float]) -> dict[str, Any]:
    return {
        "iterations": len(samples_ms),
        "min_ms": round(min(samples_ms), 3),
        "p50_ms": round(_percentile(samples_ms, 50), 3),
        "p95_ms": round(_percentile(samples_ms, 95), 3),
        "p99_ms": round(_percentile(samples_ms, 99), 3),
        "max_ms": round(max(samples_ms), 3),
        "mean_ms": round(statistics.fmean(samples_ms), 3),
    }


def _measure(operation: Callable[[], Any], iterations: int) -> dict[str, Any]:
    samples: list[float] = []
    for _ in range(iterations):
        started = time.perf_counter()
        operation()
        samples.append((time.perf_counter() - started) * 1000.0)
    return timing_stats(samples)


def _environment() -> dict[str, Any]:
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "cpu_count": os.cpu_count(),
        "memory_total_mb": _memory_total_mb(),
        "duckdb": duckdb.__version__,
        "sqlite": sqlite3.sqlite_version,
    }


def _explain(repo: CoarseIndexRepository) -> list[str]:
    # Query plans are evidence only; DuckDB may choose a different plan at another scale.
    connection = repo._conn()  # noqa: SLF001 - benchmark diagnostics, read-only
    try:
        rows = connection.execute(
            "EXPLAIN SELECT candidate_id FROM coarse "
            "WHERE method_version = ? AND slope_mean_deg IS NOT NULL "
            "ORDER BY abs(slope_mean_deg - 10.0) LIMIT 10",
            [COARSE_METHOD_VERSION],
        ).fetchall()
    finally:
        connection.close()
    return [str(row[1]) for row in rows]


def run_benchmark(*, rows: int, iterations: int, rows_per_partition: int) -> dict[str, Any]:
    if rows < 1:
        raise ValueError("--rows must be positive")
    if rows_per_partition < 1:
        raise ValueError("--rows-per-partition must be positive")
    before = _resource_snapshot()
    with tempfile.TemporaryDirectory(prefix="analog-coarse-benchmark-") as tmp:
        root = Path(tmp) / "index"
        started = time.perf_counter()
        manifest = build_index(
            synthetic_rows(rows),
            root=root,
            snapshot_id="synthetic-benchmark",
            source_datasets=[
                {
                    "dataset_id": "synthetic-benchmark",
                    "label": LABEL,
                    "note": "Synthetic rows only; not an observation or global survey.",
                }
            ],
            max_rows_per_partition=rows_per_partition,
        )
        build_seconds = time.perf_counter() - started
        after_build = _resource_snapshot()

        verify_started = time.perf_counter()
        verify_index(root)
        verify_seconds = time.perf_counter() - verify_started
        verify_peak_rss_mb = _resource_snapshot()["peak_rss_mb"]
        repository_started = time.perf_counter()
        repo = CoarseIndexRepository(root, verify=True)
        repository_load_seconds = time.perf_counter() - repository_started
        partition_bytes = sum(int(item["bytes"]) for item in manifest["partitions"])
        file_bytes = sum(path.stat().st_size for path in root.rglob("*.parquet"))

        target = {
            "coarse_slope_mean_deg": 10.0,
            "coarse_slope_p90_deg": 20.0,
            "coarse_flat_fraction_lt5deg": 0.5,
        }
        weights = {"coarse_slope_mean_deg": 1.0, "coarse_slope_p90_deg": 0.5}
        offset = min(max(10, rows // 100), max(0, rows - 10))

        def top_k() -> dict[str, Any]:
            return repo.search(target=target, weights=weights, limit=10)

        def region_filter() -> dict[str, Any]:
            return repo.search(target=target, weights=weights, limit=10, region_id="region-3")

        def pagination() -> dict[str, Any]:
            return repo.search(target=target, weights=weights, limit=10, offset=offset)

        scenarios = {
            "top_k": top_k,
            "region_filter": region_filter,
            "pagination_offset": pagination,
        }
        query_cpu_start = _resource_snapshot()["cpu_seconds"]
        queries: dict[str, Any] = {}
        for name, operation in scenarios.items():
            operation()  # warm-up
            operation()
            queries[name] = _measure(operation, iterations)
        query_cpu_seconds = _resource_snapshot()["cpu_seconds"] - query_cpu_start
        after_queries = _resource_snapshot()
        plan = _explain(repo)

        return {
            "benchmark_version": BENCHMARK_VERSION,
            "generated_at": datetime.now(UTC).isoformat(),
            "label": LABEL,
            "note": (
                "SYNTHETIC rows only. These timings describe this software and machine; "
                "they are not real global coverage or a scientific result."
            ),
            "environment": _environment(),
            "rows": rows,
            "regions": REGIONS,
            "rows_per_partition": rows_per_partition,
            "build": {
                "seconds": round(build_seconds, 3),
                "partition_count": len(manifest["partitions"]),
                "parquet_bytes": partition_bytes,
                "filesystem_parquet_bytes": file_bytes,
                "bytes_per_row": round(partition_bytes / rows, 3),
                "peak_rss_mb": round(after_build["peak_rss_mb"], 2),
                "cpu_seconds": round(after_build["cpu_seconds"] - before["cpu_seconds"], 3),
            },
            "verify": {
                "seconds": round(verify_seconds, 3),
                "peak_rss_mb": round(verify_peak_rss_mb, 2),
            },
            "repository_load": {
                "seconds": round(repository_load_seconds, 3),
                "peak_rss_mb": round(_resource_snapshot()["peak_rss_mb"], 2),
            },
            "queries": queries,
            "pagination": {"offset": offset, "limit": 10},
            "query_cpu_seconds": round(query_cpu_seconds, 3),
            "total_peak_rss_mb": round(after_queries["peak_rss_mb"], 2),
            "total_cpu_seconds": round(after_queries["cpu_seconds"] - before["cpu_seconds"], 3),
            "duckdb_explain": plan,
        }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python scripts/benchmark_coarse_index.py",
        description="Synthetic coarse Parquet build/query benchmark; no downloads.",
    )
    parser.add_argument("--rows", type=int, default=10_000)
    parser.add_argument("--iterations", type=int, default=DEFAULT_ITERATIONS)
    parser.add_argument(
        "--rows-per-partition",
        type=int,
        default=ROWS_PER_PARTITION,
        help="Writer flush size, independent of the logical synthetic row count.",
    )
    parser.add_argument("--json", action="store_true", help="Print only the JSON report")
    parser.add_argument("--json-out", default=None, help="Write the JSON report to this path")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.rows < 1 or args.rows > 2_000_000:
        raise SystemExit("--rows must be within 1..2000000")
    if args.iterations < 3 or args.iterations > 500:
        raise SystemExit("--iterations must be within 3..500")
    report = run_benchmark(
        rows=args.rows,
        iterations=args.iterations,
        rows_per_partition=args.rows_per_partition,
    )
    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0
    queries = report["queries"]
    print(
        f"rows={report['rows']} label={report['label']} "
        f"build_s={report['build']['seconds']} "
        f"parquet_bytes={report['build']['parquet_bytes']} "
        f"bytes_per_row={report['build']['bytes_per_row']} "
        f"partitions={report['build']['partition_count']} "
        f"peak_rss_mb={report['total_peak_rss_mb']} "
        f"cpu_s={report['total_cpu_seconds']}"
    )
    for name, stats in queries.items():
        print(
            f"  {name:<20} p50_ms={stats['p50_ms']} p95_ms={stats['p95_ms']} "
            f"p99_ms={stats['p99_ms']} max_ms={stats['max_ms']}"
        )
    print(
        f"  verify_s={report['verify']['seconds']} "
        f"repo_load_s={report['repository_load']['seconds']} "
        f"pagination_offset={report['pagination']['offset']} "
        "note=synthetic benchmark; not real global performance"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
