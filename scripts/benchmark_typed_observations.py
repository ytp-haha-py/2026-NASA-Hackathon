#!/usr/bin/env python
"""Measure typed-observation query plans/index value (Issue #70).

Typed-v2 rows are **sparse**: one row exists only when an observation is present. A
missing source is ``unknown``/absent, never a dense six-dimension matrix of null rows.
This script measures whether extra SQLite indexes are justified at a given scale.

It creates a temporary database with synthetic typed rows (loudly labelled), runs the
API's common queries with the current schema, then compares against candidate indexes in
the same temporary database. It never touches a real runtime database and never inserts
synthetic rows into one.

Usage::

    uv run python scripts/benchmark_typed_observations.py --rows 100000
    uv run python scripts/benchmark_typed_observations.py --rows 1000000 --json
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import statistics
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from analog_api.adapters.seed import make_engine
from analog_api.adapters.typed_db import init_typed_db

LABEL = "synthetic_typed_benchmark_not_observation"
BENCHMARK_VERSION = "typed-observation-benchmark-1.0.0"
CANDIDATE_INDEXES = {
    "area_id": "CREATE INDEX ix_bench_typed_area ON typed_observations(area_id)",
    "body_dimension": (
        "CREATE INDEX ix_bench_typed_body_dimension ON typed_observations(body, dimension_code)"
    ),
    "coverage": (
        "CREATE INDEX ix_bench_typed_coverage ON typed_observations(body, dimension_code, state)"
    ),
}
QUERIES = {
    "area_lookup": (
        "SELECT observation_id FROM typed_observations WHERE area_id = ? ORDER BY observation_id"
    ),
    "dimension_filter": (
        "SELECT observation_id FROM typed_observations "
        "WHERE dimension_code = ? AND body = ? ORDER BY observation_id"
    ),
    "feature_lookup": (
        "SELECT observation_id FROM typed_observations WHERE feature_id = ? ORDER BY observation_id"
    ),
    "coverage_group": (
        "SELECT dimension_code, body, state, count(*) FROM typed_observations "
        "GROUP BY dimension_code, body, state"
    ),
}
QUERY_PARAMS = {
    "area_lookup": ("area-0007",),
    "dimension_filter": ("D03", "Earth"),
    "feature_lookup": ("soil_ph_h2o",),
    "coverage_group": (),
}


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    rank = max(1, math.ceil(percentile / 100.0 * len(ordered)))
    return ordered[min(rank - 1, len(ordered) - 1)]


def _timing(samples: list[float]) -> dict[str, Any]:
    return {
        "iterations": len(samples),
        "p50_ms": round(_percentile(samples, 50), 3),
        "p95_ms": round(_percentile(samples, 95), 3),
        "p99_ms": round(_percentile(samples, 99), 3),
        "mean_ms": round(statistics.fmean(samples), 3),
    }


def _measure(connection: sqlite3.Connection, sql: str, params: tuple[Any, ...], n: int):
    samples: list[float] = []
    for _ in range(n):
        started = time.perf_counter()
        connection.execute(sql, params).fetchall()
        samples.append((time.perf_counter() - started) * 1000.0)
    return _timing(samples)


def _plan(connection: sqlite3.Connection, sql: str, params: tuple[Any, ...]) -> list[str]:
    return [
        " ".join(str(part) for part in row)
        for row in connection.execute("EXPLAIN QUERY PLAN " + sql, params).fetchall()
    ]


def _insert_rows(connection: sqlite3.Connection, rows: int) -> None:
    bodies = ("Earth", "Moon", "Mars")
    dimensions = ("D01", "D02", "D03", "D04", "D05", "D06")
    features = ("slope_mean_deg", "thermal_inertia", "soil_ph_h2o", "mineral_spectral_detection")
    sql = (
        "INSERT INTO typed_observations (observation_id, area_id, body, dimension_code, "
        "feature_id, value_kind, state, value, unit, uncertainty, spatial_support, "
        "temporal_support, depth_support, method_version, dataset_ids, source_asset_ids, "
        "quality_flags, score_role, evidence, notes, schema_version, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
    )
    now = datetime.now(UTC).isoformat()
    batch: list[tuple[Any, ...]] = []
    for index in range(rows):
        body = bodies[index % len(bodies)]
        batch.append(
            (
                f"synthetic:typed:{index:09d}",
                f"area-{index % 1000:04d}",
                body,
                dimensions[index % len(dimensions)],
                features[index % len(features)],
                "scalar",
                "verified" if index % 7 else "unknown",
                json.dumps({"scalar": (index % 100) / 10.0}),
                "fraction",
                None,
                json.dumps({"support": "synthetic", "resolution_m": 1000}),
                json.dumps({"support": "unknown"}),
                json.dumps({"support": "unknown"}),
                "synthetic-typed-benchmark-1.0.0",
                json.dumps(["synthetic-benchmark"]),
                json.dumps(["synthetic-benchmark-asset"]),
                json.dumps([LABEL]),
                "context",
                None,
                "SYNTHETIC benchmark row; not an observation",
                "2.0.0",
                now,
            )
        )
        if len(batch) >= 10_000:
            connection.executemany(sql, batch)
            batch.clear()
    if batch:
        connection.executemany(sql, batch)


def run_benchmark(*, rows: int, iterations: int) -> dict[str, Any]:
    if rows < 1 or rows > 1_000_000:
        raise ValueError("--rows must be within 1..1000000")
    with tempfile.TemporaryDirectory(prefix="analog-typed-benchmark-") as tmp:
        database = Path(tmp) / "typed.db"
        engine = make_engine(f"sqlite+pysqlite:///{database}")
        init_typed_db(engine)
        engine.dispose()
        with sqlite3.connect(database) as connection:
            insert_started = time.perf_counter()
            _insert_rows(connection, rows)
            connection.commit()
            insert_seconds = time.perf_counter() - insert_started
            existing_indexes = [
                {
                    "name": row[1],
                    "unique": bool(row[2]),
                    "origin": row[3],
                }
                for row in connection.execute("PRAGMA index_list('typed_observations')")
            ]
            before_plans = {
                name: _plan(connection, sql, QUERY_PARAMS[name]) for name, sql in QUERIES.items()
            }
            before = {
                name: _measure(connection, sql, QUERY_PARAMS[name], iterations)
                for name, sql in QUERIES.items()
            }
            for statement in CANDIDATE_INDEXES.values():
                connection.execute(statement)
            connection.commit()
            after_plans = {
                name: _plan(connection, sql, QUERY_PARAMS[name]) for name, sql in QUERIES.items()
            }
            after = {
                name: _measure(connection, sql, QUERY_PARAMS[name], iterations)
                for name, sql in QUERIES.items()
            }
            bytes_before = sum(
                page[0] for page in connection.execute("PRAGMA page_count").fetchall()
            ) * int(connection.execute("PRAGMA page_size").fetchone()[0])
            connection.execute("VACUUM")
            bytes_with_indexes = int(connection.execute("PRAGMA page_count").fetchone()[0]) * int(
                connection.execute("PRAGMA page_size").fetchone()[0]
            )
            index_bytes = _index_bytes(connection)
        speedups = {
            name: {
                "p50_speedup": round(before[name]["p50_ms"] / max(after[name]["p50_ms"], 0.001), 2),
                "p95_speedup": round(before[name]["p95_ms"] / max(after[name]["p95_ms"], 0.001), 2),
            }
            for name in QUERIES
        }
        worst_before = max(item["p95_ms"] for item in before.values())
        decision = (
            "defer_extra_indexes"
            if worst_before < 50.0
            else "candidate_indexes_measurable_but_real_volume_decides"
        )
        return {
            "benchmark_version": BENCHMARK_VERSION,
            "generated_at": datetime.now(UTC).isoformat(),
            "label": LABEL,
            "note": (
                "SYNTHETIC temporary database. Real typed-v2 coverage is sparse and much "
                "smaller; this is an index-decision measurement, not a coverage claim."
            ),
            "rows": rows,
            "iterations": iterations,
            "insert_seconds": round(insert_seconds, 3),
            "database_bytes_before": bytes_before,
            "database_bytes_with_candidate_indexes": bytes_with_indexes,
            "index_bytes_with_candidate_indexes": index_bytes,
            "existing_indexes": existing_indexes,
            "candidate_indexes": sorted(CANDIDATE_INDEXES),
            "before": before,
            "after": after,
            "speedups": speedups,
            "plans": {"before": before_plans, "after": after_plans},
            "decision": decision,
            "sparse_storage": (
                "One row per present observation. No dense six-dimension null rows; absent "
                "sources are represented by the dataset/provider status, not fabricated rows."
            ),
        }


def _index_bytes(connection: sqlite3.Connection) -> int:
    try:
        row = connection.execute(
            "SELECT sum(pgsize) FROM dbstat WHERE name LIKE 'ix_bench_typed_%'"
        ).fetchone()
        return int(row[0] or 0)
    except sqlite3.OperationalError:
        return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python scripts/benchmark_typed_observations.py",
        description="Compare typed-observation query indexes on synthetic temporary data.",
    )
    parser.add_argument("--rows", type=int, default=100_000)
    parser.add_argument("--iterations", type=int, default=10)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--json-out", default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = run_benchmark(rows=args.rows, iterations=max(3, args.iterations))
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0
    print(
        f"rows={report['rows']} label={report['label']} insert_s={report['insert_seconds']} "
        f"db_bytes={report['database_bytes_before']} "
        f"candidate_index_bytes={report['index_bytes_with_candidate_indexes']} "
        f"decision={report['decision']}"
    )
    for name in QUERIES:
        before = report["before"][name]
        after = report["after"][name]
        print(
            f"  {name:<18} before_p50_ms={before['p50_ms']} after_p50_ms={after['p50_ms']} "
            f"before_p95_ms={before['p95_ms']} after_p95_ms={after['p95_ms']} "
            f"p95_speedup={report['speedups'][name]['p95_speedup']}x"
        )
    print(f"  existing_indexes={report['existing_indexes']}")
    print("  note=synthetic index measurement; not real observation coverage")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
