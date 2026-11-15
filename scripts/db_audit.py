#!/usr/bin/env python
"""Reproducible storage audit for SQLite, Parquet, raw/cache files, and R2 (Issue #70).

The report deliberately keeps storage layers separate:

* **SQLite** — the rebuildable runtime index plus typed-v2/user state, with logical
  ``page_count * page_size`` bytes and per-table/index bytes from ``dbstat``.
* **Parquet** — the coarse 5 km candidate index (content-addressed partitions and
  verified snapshot manifests), reported separately from SQLite.
* **raw/cache** — source rasters and provider caches. These are *not* part of the
  database and are never added to SQLite totals.
* **R2/S3** — configuration/credential presence only. This offline audit never makes a
  network call, so remote object bytes remain unknown when no credentials are configured.

Usage::

    uv run python scripts/db_audit.py
    uv run python scripts/db_audit.py --json --output /tmp/db-audit.json
    uv run python scripts/db_audit.py --index-dir /path/to/global-index --verify-indexes

Commands and JSON field names are stable so a capacity report can be regenerated from a
clean checkout without downloading data.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

REPO_ROOT = Path(__file__).resolve().parents[1]
AUDIT_VERSION = "db-audit-1.0.0"
DEFAULT_DATABASE = REPO_ROOT / "var" / "analog.db"
DEFAULT_INDEX_DIR = REPO_ROOT / "data" / "global-index"
DEFAULT_RAW_DIR = REPO_ROOT / "data" / "raw"
DEFAULT_CACHE_DIR = REPO_ROOT / "data" / "cache"


def _relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _readonly_connection(path: Path) -> sqlite3.Connection:
    uri = f"file:{quote(str(path.resolve()))}?mode=ro"
    return sqlite3.connect(uri, uri=True)


def _quote_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _meta_value(connection: sqlite3.Connection, key: str) -> str | None:
    exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'meta'"
    ).fetchone()
    if not exists:
        return None
    row = connection.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return None if row is None else str(row[0])


def audit_sqlite(path: Path, *, integrity: bool = True) -> dict[str, Any]:
    """Report file bytes, logical pages, and per-table/index rows and bytes."""
    if not path.is_file():
        return {
            "path": _relative(path),
            "present": False,
            "file_bytes": 0,
            "note": "database file does not exist; run the seed or a restore drill first",
        }
    with closing(_readonly_connection(path)) as connection:
        page_size = int(connection.execute("PRAGMA page_size").fetchone()[0])
        page_count = int(connection.execute("PRAGMA page_count").fetchone()[0])
        freelist_pages = int(connection.execute("PRAGMA freelist_count").fetchone()[0])
        journal_mode = str(connection.execute("PRAGMA journal_mode").fetchone()[0])
        check = "not_checked"
        if integrity:
            check = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
        objects = connection.execute(
            "SELECT name, type, tbl_name, sql FROM sqlite_master "
            "WHERE type IN ('table', 'index') AND name NOT LIKE 'sqlite_%' "
            "ORDER BY type, name"
        ).fetchall()
        index_meta: dict[str, dict[str, Any]] = {}
        for table_name, *_ in [row for row in objects if row[1] == "table"]:
            for entry in connection.execute(
                f"PRAGMA index_list({_quote_identifier(table_name)})"
            ).fetchall():
                # seq, name, unique, origin, partial
                index_meta[str(entry[1])] = {
                    "table": table_name,
                    "unique": bool(entry[2]),
                    "origin": str(entry[3]),
                }
        index_columns: dict[str, list[str]] = {}
        for name in index_meta:
            index_columns[name] = [
                str(row[2])
                for row in connection.execute(
                    f"PRAGMA index_info({_quote_identifier(name)})"
                ).fetchall()
                if row[2] is not None
            ]
        sizes: dict[str, dict[str, int]] = {}
        dbstat_available = True
        try:
            for name, pages, page_bytes, unused in connection.execute(
                "SELECT name, count(*), sum(pgsize), sum(unused) FROM dbstat GROUP BY name"
            ).fetchall():
                sizes[str(name)] = {
                    "pages": int(pages or 0),
                    "bytes": int(page_bytes or 0),
                    "unused_bytes": int(unused or 0),
                }
        except sqlite3.OperationalError:
            dbstat_available = False

        tables: list[dict[str, Any]] = []
        indexes: list[dict[str, Any]] = []
        for name, kind, table_name, _sql in objects:
            size = sizes.get(name, {"pages": 0, "bytes": 0, "unused_bytes": 0})
            if kind == "table":
                rows = int(
                    connection.execute(
                        f"SELECT count(*) FROM {_quote_identifier(name)}"
                    ).fetchone()[0]
                )
                tables.append(
                    {
                        "name": name,
                        "rows": rows,
                        "bytes": size["bytes"],
                        "pages": size["pages"],
                        "unused_bytes": size["unused_bytes"],
                    }
                )
            else:
                meta = index_meta.get(name, {})
                indexes.append(
                    {
                        "name": name,
                        "table": meta.get("table", table_name),
                        "unique": meta.get("unique"),
                        "origin": meta.get("origin"),
                        "columns": index_columns.get(name, []),
                        "bytes": size["bytes"],
                        "pages": size["pages"],
                    }
                )
        meta = {
            key: _meta_value(connection, key)
            for key in ("schema_version", "data_version", "seeded_at")
        }
    tables.sort(key=lambda item: (-item["bytes"], item["name"]))
    indexes.sort(key=lambda item: (-item["bytes"], item["name"]))
    sidecars = {
        name: (path.with_name(path.name + suffix).stat().st_size)
        for name, suffix in (("wal", "-wal"), ("shm", "-shm"), ("journal", "-journal"))
        if path.with_name(path.name + suffix).is_file()
    }
    page_bytes = page_size * page_count
    return {
        "path": _relative(path),
        "present": True,
        "file_bytes": path.stat().st_size,
        "sidecar_bytes": sidecars,
        "page_size": page_size,
        "page_count": page_count,
        "page_count_bytes": page_bytes,
        "freelist_pages": freelist_pages,
        "freelist_bytes": freelist_pages * page_size,
        "journal_mode": journal_mode,
        "integrity_check": check,
        "dbstat_available": dbstat_available,
        "meta": meta,
        "table_count": len(tables),
        "index_count": len(indexes),
        "tables": tables,
        "indexes": indexes,
        "note": (
            "file_bytes includes SQLite page overhead; page_count*page_size is the logical "
            "page total. Raw rasters/caches and Parquet partitions are excluded."
        ),
    }


def _partition_region(index_dir: Path, path: Path) -> str:
    relative = path.relative_to(index_dir).parts
    if len(relative) >= 3 and relative[0] == "partitions":
        return relative[1]
    if len(relative) > 1:
        return relative[0]
    return "(root)"


def _manifest_partition_bytes(manifest_path: Path, index_dir: Path, payload: dict[str, Any]) -> int:
    if not isinstance(payload.get("partitions"), list):
        return 0
    if manifest_path.parent.parent.name == "snapshots":
        base = manifest_path.parent.parent.parent
    else:
        base = index_dir
    total = 0
    for partition in payload["partitions"]:
        if not isinstance(partition, dict) or not isinstance(partition.get("path"), str):
            continue
        path = base / partition["path"]
        if path.is_file():
            total += path.stat().st_size
    return total


def audit_parquet(index_dir: Path, *, verify: bool = False) -> dict[str, Any]:
    """Report Parquet physical bytes, files, partition regions, and snapshot manifests."""
    if not index_dir.is_dir():
        return {
            "path": _relative(index_dir),
            "present": False,
            "file_count": 0,
            "bytes": 0,
            "snapshots": [],
            "note": "no coarse Parquet index directory is present",
        }
    files = sorted(path for path in index_dir.rglob("*.parquet") if path.is_file())
    regions: dict[str, dict[str, int]] = {}
    for path in files:
        region = _partition_region(index_dir, path)
        entry = regions.setdefault(region, {"file_count": 0, "bytes": 0})
        entry["file_count"] += 1
        entry["bytes"] += path.stat().st_size
    snapshots: list[dict[str, Any]] = []
    manifest_paths: dict[str, Path] = {}
    for manifest_path in sorted(index_dir.rglob("manifest.json")):
        if not manifest_path.is_file():
            continue
        display = _relative(manifest_path)
        manifest_paths[display] = manifest_path
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            snapshots.append(
                {
                    "manifest": display,
                    "valid": False,
                    "error": str(exc),
                }
            )
            continue
        snapshots.append(
            {
                "manifest": display,
                "valid": True,
                "snapshot_id": payload.get("snapshot_id"),
                "index_version": payload.get("index_version"),
                "row_count": payload.get("row_count"),
                "partition_count": len(payload.get("partitions") or []),
                "partition_bytes": _manifest_partition_bytes(manifest_path, index_dir, payload),
                "coverage_label": (payload.get("coverage") or {}).get("label"),
                "created_at": payload.get("created_at"),
            }
        )
    verification: dict[str, Any] = {"requested": verify}
    if verify and snapshots:
        from analog_pipelines.coarse_index import IndexError_, verify_index

        results = []
        for snapshot in snapshots:
            if not snapshot.get("valid"):
                results.append({"manifest": snapshot["manifest"], "ok": False})
                continue
            manifest = manifest_paths[snapshot["manifest"]]
            try:
                verified = verify_index(
                    manifest.parent,
                    snapshot_id=snapshot.get("snapshot_id"),
                )
            except IndexError_ as exc:
                results.append({"manifest": snapshot["manifest"], "ok": False, "error": str(exc)})
            else:
                results.append(
                    {
                        "manifest": snapshot["manifest"],
                        "ok": True,
                        "row_count": verified.get("row_count"),
                    }
                )
        verification["results"] = results
        verification["ok"] = all(item.get("ok") for item in results)
    sizes = [path.stat().st_size for path in files]
    return {
        "path": _relative(index_dir),
        "present": True,
        "file_count": len(files),
        "bytes": sum(sizes),
        "min_file_bytes": min(sizes) if sizes else 0,
        "max_file_bytes": max(sizes) if sizes else 0,
        "regions": [{"region_id": name, **values} for name, values in sorted(regions.items())],
        "snapshots": snapshots,
        "verification": verification,
        "note": (
            "Parquet partitions are content-addressed and may be shared by multiple "
            "snapshots. Snapshot row_count is logical, not a duplicate physical copy."
        ),
    }


def audit_directory(path: Path, label: str) -> dict[str, Any]:
    """Report raw/cache bytes separately from database storage."""
    if not path.is_dir():
        return {
            "label": label,
            "path": _relative(path),
            "present": False,
            "file_count": 0,
            "bytes": 0,
            "entries": [],
        }
    files = [item for item in path.rglob("*") if item.is_file()]
    entries: dict[str, dict[str, int]] = {}
    for item in files:
        try:
            relative = item.relative_to(path)
        except ValueError:
            continue
        key = relative.parts[0] if len(relative.parts) > 1 else "(root)"
        entry = entries.setdefault(key, {"file_count": 0, "bytes": 0})
        entry["file_count"] += 1
        entry["bytes"] += item.stat().st_size
    largest = sorted(files, key=lambda item: item.stat().st_size, reverse=True)[:5]
    return {
        "label": label,
        "path": _relative(path),
        "present": True,
        "file_count": len(files),
        "bytes": sum(item.stat().st_size for item in files),
        "entries": [{"name": name, **values} for name, values in sorted(entries.items())],
        "largest_files": [
            {"path": _relative(item), "bytes": item.stat().st_size} for item in largest
        ],
        "note": f"{label} bytes are source/cache storage and are excluded from database totals.",
    }


def audit_r2(env: dict[str, str] | None = None) -> dict[str, Any]:
    """Report R2/S3 configuration without making network calls."""
    values = env if env is not None else os.environ
    backend = values.get("ANALOG_OBJECT_STORE_BACKEND", "").strip().lower()
    endpoint = bool(values.get("ANALOG_OBJECT_STORE_ENDPOINT", "").strip())
    bucket = bool(values.get("ANALOG_OBJECT_STORE_BUCKET", "").strip())
    credentials = bool(
        values.get("AWS_ACCESS_KEY_ID", "").strip()
        and values.get("AWS_SECRET_ACCESS_KEY", "").strip()
    )
    s3_backends = {"s3", "r2", "s3-compatible"}
    if backend not in s3_backends:
        return {
            "backend": backend or "local",
            "status": "not_configured",
            "reason": "no R2/S3 backend selected; local object store only",
            "bucket_configured": bucket,
            "endpoint_configured": endpoint,
            "credentials_present": credentials,
            "bytes": None,
            "objects": None,
            "note": "Offline audit: no remote object inventory was requested.",
        }
    missing = []
    if not endpoint:
        missing.append("endpoint")
    if not bucket:
        missing.append("bucket")
    if not credentials:
        missing.append("credentials")
    if missing:
        return {
            "backend": backend,
            "status": "unavailable",
            "reason": "missing " + ", ".join(missing),
            "bucket_configured": bucket,
            "endpoint_configured": endpoint,
            "credentials_present": credentials,
            "bytes": None,
            "objects": None,
            "note": "R2 bytes are unknown; never zero-filled.",
        }
    return {
        "backend": backend,
        "status": "configured_not_queried",
        "reason": "credentials present; this offline audit does not list remote objects",
        "bucket_configured": bucket,
        "endpoint_configured": endpoint,
        "credentials_present": True,
        "bytes": None,
        "objects": None,
        "note": "R2 bytes are unknown; never zero-filled.",
    }


def build_audit(
    *,
    database: Path = DEFAULT_DATABASE,
    index_dir: Path = DEFAULT_INDEX_DIR,
    raw_dir: Path = DEFAULT_RAW_DIR,
    cache_dir: Path = DEFAULT_CACHE_DIR,
    integrity: bool = True,
    verify_indexes: bool = False,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    sqlite_report = audit_sqlite(database, integrity=integrity)
    parquet_report = audit_parquet(index_dir, verify=verify_indexes)
    raw_report = audit_directory(raw_dir, "raw")
    cache_report = audit_directory(cache_dir, "cache")
    r2_report = audit_r2(env)
    database_bytes = (
        (sqlite_report.get("file_bytes") or 0)
        + sum((sqlite_report.get("sidecar_bytes") or {}).values())
        + (parquet_report.get("bytes") or 0)
    )
    return {
        "audit_version": AUDIT_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "scope_note": (
            "SQLite, Parquet, raw/cache, and R2 are reported separately. Raw rasters, caches, "
            "and remote object storage are never added to SQLite file size."
        ),
        "sqlite": sqlite_report,
        "parquet": parquet_report,
        "raw_cache": {"raw": raw_report, "cache": cache_report},
        "r2": r2_report,
        "totals": {
            "sqlite_bytes": (sqlite_report.get("file_bytes") or 0),
            "sqlite_sidecar_bytes": sum((sqlite_report.get("sidecar_bytes") or {}).values()),
            "parquet_bytes": parquet_report.get("bytes") or 0,
            "database_bytes": database_bytes,
            "raw_bytes": raw_report.get("bytes") or 0,
            "cache_bytes": cache_report.get("bytes") or 0,
            "r2_bytes": None,
            "note": "database_bytes = SQLite + sidecars + Parquet; raw/cache/R2 excluded.",
        },
    }


def _human_report(payload: dict[str, Any]) -> str:
    sqlite_report = payload["sqlite"]
    parquet_report = payload["parquet"]
    raw = payload["raw_cache"]["raw"]
    cache = payload["raw_cache"]["cache"]
    r2 = payload["r2"]
    lines = [
        f"DB audit {payload['audit_version']} at {payload['generated_at']}",
        "",
        f"SQLite  path={sqlite_report['path']} present={sqlite_report['present']} "
        f"file_bytes={sqlite_report.get('file_bytes', 0)} "
        f"page_count*page_size={sqlite_report.get('page_count_bytes', 0)} "
        f"integrity={sqlite_report.get('integrity_check')} "
        f"schema={sqlite_report.get('meta', {}).get('schema_version')} "
        f"data={sqlite_report.get('meta', {}).get('data_version')}",
    ]
    for table in sqlite_report.get("tables", []):
        lines.append(f"  table {table['name']:<28} rows={table['rows']:<10} bytes={table['bytes']}")
    for index in sqlite_report.get("indexes", []):
        lines.append(
            f"  index {index['name']:<28} table={index['table']:<24} bytes={index['bytes']}"
        )
    lines.extend(
        [
            "",
            f"Parquet path={parquet_report['path']} present={parquet_report['present']} "
            f"files={parquet_report.get('file_count', 0)} "
            f"bytes={parquet_report.get('bytes', 0)} "
            f"snapshots={len(parquet_report.get('snapshots', []))}",
            f"Raw     path={raw['path']} present={raw['present']} "
            f"files={raw.get('file_count', 0)} bytes={raw.get('bytes', 0)}",
            f"Cache   path={cache['path']} present={cache['present']} "
            f"files={cache.get('file_count', 0)} bytes={cache.get('bytes', 0)}",
            f"R2      status={r2['status']} reason={r2.get('reason')} bytes={r2.get('bytes')}",
            "",
            f"database_bytes={payload['totals']['database_bytes']} "
            f"(SQLite {payload['totals']['sqlite_bytes']} + sidecars "
            f"{payload['totals']['sqlite_sidecar_bytes']} + Parquet "
            f"{payload['totals']['parquet_bytes']}); raw/cache/R2 excluded",
        ]
    )
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python scripts/db_audit.py",
        description="Audit SQLite/Parquet/raw/cache/R2 storage without downloading data.",
    )
    parser.add_argument("--database", default=str(DEFAULT_DATABASE))
    parser.add_argument("--index-dir", default=str(DEFAULT_INDEX_DIR))
    parser.add_argument("--raw-dir", default=str(DEFAULT_RAW_DIR))
    parser.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR))
    parser.add_argument(
        "--verify-indexes",
        action="store_true",
        help="Re-hash Parquet partitions (slower) and report verification status.",
    )
    parser.add_argument(
        "--no-integrity",
        action="store_true",
        help="Skip PRAGMA integrity_check (faster on very large databases).",
    )
    parser.add_argument("--json", action="store_true", help="Print only the JSON report")
    parser.add_argument("--output", default=None, help="Write the JSON report to this path")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = build_audit(
        database=Path(args.database),
        index_dir=Path(args.index_dir),
        raw_dir=Path(args.raw_dir),
        cache_dir=Path(args.cache_dir),
        integrity=not args.no_integrity,
        verify_indexes=args.verify_indexes,
    )
    if args.output:
        Path(args.output).write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(_human_report(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
