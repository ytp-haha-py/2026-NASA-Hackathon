#!/usr/bin/env python
"""Safe SQLite backup, restore, verification, and rebuild drill (Issue #70).

The runtime database mixes two kinds of state:

* rebuildable scientific index rows derived from the pinned snapshot/manifests;
* user/runtime state (on-demand AOIs, analysis jobs, typed-v2 observations, areas,
  source assets, and processing runs) that must survive a rebuild.

This script never mutates the source database: backup uses the SQLite online backup
API through a read-only connection. Restore writes to a temporary file first, verifies
the SHA-256 and ``PRAGMA integrity_check``, then atomically replaces the destination.

Commands::

    uv run python scripts/db_backup.py backup --source var/analog.db --output /tmp/analog.db
    uv run python scripts/db_backup.py restore --backup /tmp/analog.db --destination /tmp/restored.db
    uv run python scripts/db_backup.py verify --database /tmp/restored.db
    uv run python scripts/db_backup.py drill --source var/analog.db --workdir /tmp/db-drill

``drill`` performs backup -> restore -> baseline rebuild on *copies* and fails if any
protected runtime/user row or count changes. ``--reset`` is never applied to the source.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATABASE = REPO_ROOT / "var" / "analog.db"
DEFAULT_SNAPSHOT = REPO_ROOT / "samples" / "snapshot" / "analog-demo.snapshot.json"
DRILL_VERSION = "db-backup-drill-1.0.0"

PROTECTED_COUNTS = {
    "on_demand_candidates": ("SELECT count(*) FROM candidates WHERE origin = 'on_demand'"),
    "on_demand_features": (
        "SELECT count(*) FROM feature_observations f JOIN candidates c "
        "ON c.location_id = f.candidate_id WHERE c.origin = 'on_demand'"
    ),
    "on_demand_context": (
        "SELECT count(*) FROM context_observations o JOIN candidates c "
        "ON c.location_id = o.candidate_id WHERE c.origin = 'on_demand'"
    ),
    "analysis_jobs": "SELECT count(*) FROM analysis_jobs",
    "typed_areas": "SELECT count(*) FROM typed_areas",
    "typed_source_assets": "SELECT count(*) FROM typed_source_assets",
    "typed_observations": "SELECT count(*) FROM typed_observations",
    "typed_processing_runs": "SELECT count(*) FROM typed_processing_runs",
}

V1_COUNTS = {
    "candidates": "SELECT count(*) FROM candidates",
    "feature_observations": "SELECT count(*) FROM feature_observations",
    "context_observations": "SELECT count(*) FROM context_observations",
    "datasets": "SELECT count(*) FROM datasets",
    "planetary_references": "SELECT count(*) FROM planetary_references",
    "reference_feature_observations": "SELECT count(*) FROM reference_feature_observations",
}


class BackupError(RuntimeError):
    """A backup, restore, or drill invariant failed."""


def _readonly_connection(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise BackupError(f"database does not exist: {path}")
    uri = f"file:{quote(str(path.resolve()))}?mode=ro"
    return sqlite3.connect(uri, uri=True)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name = ?", (table,)
    ).fetchone()
    return row is not None


def _scalar(connection: sqlite3.Connection, sql: str) -> int:
    try:
        return int(connection.execute(sql).fetchone()[0])
    except sqlite3.OperationalError:
        return 0


def database_counts(path: Path) -> dict[str, int]:
    with closing(_readonly_connection(path)) as connection:
        counts = {
            name: _scalar(connection, sql)
            for name, sql in {**V1_COUNTS, **PROTECTED_COUNTS}.items()
        }
    return counts


def _meta_value(connection: sqlite3.Connection, key: str) -> str | None:
    if not _table_exists(connection, "meta"):
        return None
    row = connection.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return None if row is None else str(row[0])


def database_fingerprint(path: Path, *, integrity: bool = True) -> dict[str, Any]:
    if not path.is_file():
        raise BackupError(f"database does not exist: {path}")
    with closing(_readonly_connection(path)) as connection:
        check = "not_checked"
        if integrity:
            check = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
        page_size = int(connection.execute("PRAGMA page_size").fetchone()[0])
        page_count = int(connection.execute("PRAGMA page_count").fetchone()[0])
        freelist = int(connection.execute("PRAGMA freelist_count").fetchone()[0])
        journal_mode = str(connection.execute("PRAGMA journal_mode").fetchone()[0])
        schema_version = _meta_value(connection, "schema_version")
        data_version = _meta_value(connection, "data_version")
        counts = {
            name: _scalar(connection, sql)
            for name, sql in {**V1_COUNTS, **PROTECTED_COUNTS}.items()
        }
    return {
        "path": str(path),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
        "page_size": page_size,
        "page_count": page_count,
        "page_count_bytes": page_size * page_count,
        "freelist_pages": freelist,
        "journal_mode": journal_mode,
        "integrity_check": check,
        "schema_version": schema_version,
        "data_version": data_version,
        "counts": counts,
    }


def backup_database(source: Path, destination: Path, *, overwrite: bool = False) -> dict[str, Any]:
    """Create a consistent online backup plus a checksummed metadata sidecar."""
    source = source.resolve()
    destination = destination.resolve()
    if not source.is_file():
        raise BackupError(f"source database does not exist: {source}")
    if destination.exists() and not overwrite:
        raise BackupError(f"backup destination already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
    if temporary.exists():
        temporary.unlink()
    try:
        with (
            closing(_readonly_connection(source)) as src,
            closing(sqlite3.connect(temporary)) as dst,
        ):
            src.backup(dst)
            dst.commit()
        fingerprint = database_fingerprint(temporary)
        if fingerprint["integrity_check"] != "ok":
            raise BackupError(f"backup failed integrity_check: {fingerprint['integrity_check']}")
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()
    metadata = {
        "backup_version": "1.0.0",
        "created_at": datetime.now(UTC).isoformat(),
        "source": str(source),
        "sha256": sha256_file(destination),
        "bytes": destination.stat().st_size,
        "schema_version": fingerprint["schema_version"],
        "data_version": fingerprint["data_version"],
        "counts": fingerprint["counts"],
        "note": "SQLite online backup. Raw rasters/caches are not included.",
    }
    sidecar = destination.with_name(f"{destination.name}.meta.json")
    sidecar.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return metadata


def _expected_checksum(backup: Path, expected: str | None) -> str | None:
    if expected:
        return expected
    sidecar = backup.with_name(f"{backup.name}.meta.json")
    if not sidecar.is_file():
        return None
    try:
        payload = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BackupError(f"invalid backup metadata {sidecar}: {exc}") from exc
    value = payload.get("sha256")
    return value if isinstance(value, str) else None


def restore_database(
    backup: Path,
    destination: Path,
    *,
    expected_sha256: str | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Verify a backup, then atomically restore it to ``destination``."""
    backup = backup.resolve()
    destination = destination.resolve()
    if not backup.is_file():
        raise BackupError(f"backup does not exist: {backup}")
    if destination.exists() and not overwrite:
        raise BackupError(
            f"restore destination already exists: {destination}; pass --overwrite to replace it"
        )
    expected = _expected_checksum(backup, expected_sha256)
    actual = sha256_file(backup)
    if expected is not None and actual != expected:
        raise BackupError(
            f"backup checksum mismatch: expected {expected}, actual {actual}; refusing restore"
        )
    fingerprint = database_fingerprint(backup)
    if fingerprint["integrity_check"] != "ok":
        raise BackupError(f"backup failed integrity_check: {fingerprint['integrity_check']}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.restore")
    try:
        shutil.copyfile(backup, temporary)
        with temporary.open("rb") as handle:
            os.fsync(handle.fileno())
        if sha256_file(temporary) != actual:
            raise BackupError("restored copy checksum does not match the verified backup")
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()
    return {
        "backup": str(backup),
        "destination": str(destination),
        "sha256": actual,
        "counts": fingerprint["counts"],
        "schema_version": fingerprint["schema_version"],
        "data_version": fingerprint["data_version"],
    }


def _load_build_index():
    path = REPO_ROOT / "pipelines" / "build_index.py"
    spec = importlib.util.spec_from_file_location("analog_build_index_db70", path)
    if spec is None or spec.loader is None:
        raise BackupError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def rebuild_from_snapshot(
    database: Path, *, snapshot: Path, root: Path = REPO_ROOT
) -> dict[str, Any]:
    """Rebuild the scientific index of ``database`` from a verified pinned snapshot."""
    build_index = _load_build_index()
    result = build_index.build_index(
        snapshot,
        f"sqlite+pysqlite:///{database}",
        reset=True,
        root=root,
    )
    return {
        "snapshot_id": result["snapshot_id"],
        "data_version": result["data_version"],
        "counts": result["counts"],
    }


def run_drill(
    source: Path,
    *,
    snapshot: Path = DEFAULT_SNAPSHOT,
    workdir: Path | None = None,
    keep: bool = False,
) -> dict[str, Any]:
    """Backup -> restore -> rebuild on copies; assert protected runtime state survives."""
    source = source.resolve()
    snapshot = snapshot.resolve()
    if not snapshot.is_file():
        raise BackupError(f"snapshot does not exist: {snapshot}")
    owns_workdir = workdir is None
    workdir = Path(workdir or tempfile.mkdtemp(prefix="analog-db-drill-")).resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    backup_path = workdir / "backup.db"
    restored_path = workdir / "restored.db"
    try:
        before = database_fingerprint(source)
        backup_meta = backup_database(source, backup_path)
        restored = restore_database(backup_path, restored_path)
        after_restore = database_fingerprint(restored_path)
        if after_restore["counts"] != before["counts"]:
            raise BackupError("restored database counts differ from the source")
        if after_restore["data_version"] != before["data_version"]:
            raise BackupError("restored database data_version differs from the source")

        rebuild = rebuild_from_snapshot(restored_path, snapshot=snapshot)
        after_rebuild = database_fingerprint(restored_path)
        mismatches = {
            name: {"before": before["counts"].get(name), "after": after_rebuild["counts"].get(name)}
            for name in PROTECTED_COUNTS
            if before["counts"].get(name) != after_rebuild["counts"].get(name)
        }
        if mismatches:
            raise BackupError(f"protected runtime counts changed after rebuild: {mismatches}")
        required = ("candidates", "datasets", "feature_observations", "context_observations")
        empty = [name for name in required if after_rebuild["counts"].get(name, 0) == 0]
        if empty:
            raise BackupError(f"baseline rebuild produced empty scientific tables: {empty}")
        return {
            "drill_version": DRILL_VERSION,
            "source": str(source),
            "snapshot": str(snapshot),
            "workdir": str(workdir),
            "backup": backup_meta,
            "restore": restored,
            "rebuild": rebuild,
            "before": before,
            "after_restore": after_restore,
            "after_rebuild": after_rebuild,
            "protected_counts_preserved": sorted(PROTECTED_COUNTS),
            "label": "copy_only_drill_source_never_modified",
        }
    finally:
        if owns_workdir and not keep:
            shutil.rmtree(workdir, ignore_errors=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python scripts/db_backup.py",
        description="Backup, restore, verify, and drill the runtime SQLite database.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    backup = sub.add_parser("backup", help="Create a verified online backup")
    backup.add_argument("--source", default=str(DEFAULT_DATABASE))
    backup.add_argument("--output", required=True)
    backup.add_argument("--overwrite", action="store_true")

    restore = sub.add_parser("restore", help="Verify and atomically restore a backup")
    restore.add_argument("--backup", required=True)
    restore.add_argument("--destination", required=True)
    restore.add_argument("--expected-sha256", default=None)
    restore.add_argument("--overwrite", action="store_true")

    verify = sub.add_parser("verify", help="Report integrity, schema/data version, and counts")
    verify.add_argument("--database", default=str(DEFAULT_DATABASE))
    verify.add_argument("--expected-sha256", default=None)

    drill = sub.add_parser(
        "drill", help="Backup -> restore -> baseline rebuild on copies; never touches the source"
    )
    drill.add_argument("--source", default=str(DEFAULT_DATABASE))
    drill.add_argument("--snapshot", default=str(DEFAULT_SNAPSHOT))
    drill.add_argument("--workdir", default=None)
    drill.add_argument("--keep", action="store_true", help="Keep the temporary work directory")
    drill.add_argument("--json", action="store_true", help="Print the full JSON report")
    drill.add_argument("--output", default=None, help="Write the JSON report to this path")

    parser.add_argument("--json", action="store_true", help=argparse.SUPPRESS)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "backup":
            payload = backup_database(
                Path(args.source), Path(args.output), overwrite=args.overwrite
            )
            print(
                f"backed_up source={args.source} output={args.output} "
                f"bytes={payload['bytes']} sha256={payload['sha256']} "
                f"counts={json.dumps(payload['counts'], sort_keys=True)}"
            )
            return 0
        if args.command == "restore":
            payload = restore_database(
                Path(args.backup),
                Path(args.destination),
                expected_sha256=args.expected_sha256,
                overwrite=args.overwrite,
            )
            print(
                f"restored backup={payload['backup']} destination={payload['destination']} "
                f"sha256={payload['sha256']} counts={json.dumps(payload['counts'], sort_keys=True)}"
            )
            return 0
        if args.command == "verify":
            expected = args.expected_sha256
            payload = database_fingerprint(Path(args.database))
            if expected is not None and payload["sha256"] != expected:
                raise BackupError(
                    f"checksum mismatch: expected {expected}, actual {payload['sha256']}"
                )
            if payload["integrity_check"] != "ok":
                raise BackupError(f"integrity_check failed: {payload['integrity_check']}")
            print(json.dumps(payload, indent=2, sort_keys=True))
            return 0
        payload = run_drill(
            Path(args.source),
            snapshot=Path(args.snapshot),
            workdir=Path(args.workdir) if args.workdir else None,
            keep=args.keep,
        )
        if args.output:
            Path(args.output).write_text(
                json.dumps(payload, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        if args.json:
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            print(
                f"drill OK source={args.source} backup_bytes={payload['backup']['bytes']} "
                f"data_version={payload['after_rebuild']['data_version']} "
                f"protected_counts={','.join(sorted(PROTECTED_COUNTS))} "
                f"label={payload['label']}"
            )
            print(
                "counts before/after_rebuild: "
                + json.dumps(
                    {
                        name: {
                            "before": payload["before"]["counts"].get(name),
                            "after": payload["after_rebuild"]["counts"].get(name),
                        }
                        for name in PROTECTED_COUNTS
                    },
                    sort_keys=True,
                )
            )
        return 0
    except (BackupError, OSError, sqlite3.Error) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
