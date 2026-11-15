"""Rebuild the SQLite index from a pinned, checksummed snapshot.

The snapshot pins manifest SHA-256 values. Verification happens before any database
connection is created, then the existing ``analog_api.adapters.seed`` machinery loads
the same manifests used by ``make seed``. No scoring or seeding logic is duplicated.

Usage::

    uv run python pipelines/build_index.py \
        --snapshot samples/snapshot/analog-demo.snapshot.json \
        --database-url sqlite+pysqlite:///./var/demo-index.db --reset
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from analog_api.adapters.db import (
    Candidate,
    ContextObservation,
    Dataset,
    FeatureObservation,
    Meta,
    PlanetaryReference,
    ReferenceFeatureObservation,
)
from analog_api.adapters.schema import SchemaVersionError
from analog_api.adapters.seed import (
    DATA_VERSION_KEY,
    ManifestError,
    compute_data_version,
    load_manifest_file,
    make_engine,
    seed_manifests,
)
from analog_api.config import Settings
from analog_pipelines.snapshot import (
    DEFAULT_SNAPSHOT_RELATIVE,
    REPO_ROOT,
    SnapshotError,
    SnapshotVerificationError,
    load_snapshot,
    resolve_snapshot_path,
    verify_snapshot,
)
from sqlalchemy import func, select
from sqlalchemy.orm import Session

DEFAULT_DATABASE_URL = "sqlite+pysqlite:///./var/demo-index.db"


def _counts(engine: Any) -> dict[str, Any]:
    with Session(engine) as session:
        data_version = session.get(Meta, DATA_VERSION_KEY)
        datasets = [
            {
                "dataset_id": row.dataset_id,
                "provider": row.provider,
                "product_id": row.product_id,
                "license": row.license,
                "native_resolution_m": row.native_resolution_m,
            }
            for row in session.scalars(select(Dataset).order_by(Dataset.dataset_id))
        ]
        return {
            "candidates": session.scalar(select(func.count()).select_from(Candidate)) or 0,
            "datasets": len(datasets),
            "references": session.scalar(select(func.count()).select_from(PlanetaryReference)) or 0,
            "features": session.scalar(select(func.count()).select_from(FeatureObservation)) or 0,
            "context_observations": (
                session.scalar(select(func.count()).select_from(ContextObservation)) or 0
            ),
            "reference_features": (
                session.scalar(select(func.count()).select_from(ReferenceFeatureObservation)) or 0
            ),
            "data_version": data_version.value if data_version is not None else None,
            "dataset_metadata": datasets,
        }


def build_index(
    snapshot_path: Path,
    database_url: str = DEFAULT_DATABASE_URL,
    *,
    reset: bool = False,
    dry_run: bool = False,
    root: Path = REPO_ROOT,
) -> dict[str, Any]:
    """Verify a snapshot, then load its pinned manifests into the SQLite index."""
    root = root.resolve()
    snapshot_path = snapshot_path.resolve()
    if not snapshot_path.is_file():
        raise SnapshotError(f"snapshot does not exist: {snapshot_path}")

    # Checksums are verified before the engine is created, so a bad snapshot can never
    # partially seed a database.
    verify_snapshot(snapshot_path, root=root, check_artifacts=False)
    snapshot = load_snapshot(snapshot_path)

    manifests = []
    for entry in snapshot["manifests"]:
        if not entry.get("seedable", True):
            continue
        manifests.append(load_manifest_file(resolve_snapshot_path(root, entry["path"])))
    if not manifests:
        raise SnapshotError("snapshot contains no candidate/reference manifests to index")

    data_version = compute_data_version(manifests)
    if data_version != snapshot["data_version"]:
        raise SnapshotVerificationError(
            f"data_version mismatch: snapshot {snapshot['data_version']!r}, "
            f"manifests compute {data_version!r}"
        )

    unique_candidates = {
        candidate.location_id: candidate
        for manifest in manifests
        for candidate in manifest.candidates
    }
    unique_references = {
        reference.reference_id: reference
        for manifest in manifests
        for reference in manifest.references
    }
    context_observations = {
        (candidate.location_id, feature_id): observation
        for manifest in manifests
        for candidate in manifest.candidates
        for feature_id, observation in candidate.context_observations.items()
    }
    preview = {
        "candidates": len(unique_candidates),
        "datasets": len(
            {dataset.dataset_id for manifest in manifests for dataset in manifest.datasets}
        ),
        "references": len(unique_references),
        "features": sum(len(candidate.features) for candidate in unique_candidates.values()),
        "context_observations": len(context_observations),
        "reference_features": sum(
            len(reference.features) for reference in unique_references.values()
        ),
        "data_version": data_version,
    }
    if dry_run:
        return {
            "dry_run": True,
            "snapshot_id": snapshot["snapshot_id"],
            "data_version": data_version,
            "database_url": None,
            "counts": preview,
        }

    settings = Settings(database_url=database_url)
    if settings.sqlite_path is not None:
        settings.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    engine = make_engine(database_url)
    seeded_version = seed_manifests(engine, manifests, reset=reset)
    counts = _counts(engine)
    if seeded_version != data_version or counts["data_version"] != data_version:
        raise SnapshotVerificationError(
            f"seeded data_version {seeded_version!r} / index {counts['data_version']!r} "
            f"does not match snapshot {data_version!r}"
        )
    return {
        "dry_run": False,
        "snapshot_id": snapshot["snapshot_id"],
        "data_version": data_version,
        "database_url": database_url,
        "counts": counts,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python pipelines/build_index.py",
        description="Rebuild the SQLite index from a pinned snapshot.",
    )
    parser.add_argument(
        "--snapshot",
        default=str(REPO_ROOT / DEFAULT_SNAPSHOT_RELATIVE),
        help="Snapshot JSON to verify and load.",
    )
    parser.add_argument("--database-url", default=DEFAULT_DATABASE_URL)
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Drop and recreate the whole index before seeding (recommended for rebuilds).",
    )
    parser.add_argument("--dry-run", action="store_true", help="Verify and count without writing.")
    parser.add_argument(
        "--root", default=str(REPO_ROOT), help="Repository root for manifest paths."
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = build_index(
            Path(args.snapshot),
            args.database_url,
            reset=args.reset,
            dry_run=args.dry_run,
            root=Path(args.root),
        )
    except (SnapshotError, ManifestError, SchemaVersionError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    counts = result["counts"]
    prefix = "dry-run" if result["dry_run"] else "rebuilt"
    print(
        f"verified snapshot={result['snapshot_id']} data_version={counts['data_version']} "
        f"{prefix} candidates={counts['candidates']} datasets={counts['datasets']} "
        f"references={counts['references']} features={counts['features']} "
        f"context_observations={counts['context_observations']}"
    )
    if not result["dry_run"]:
        print(f"database_url={result['database_url']}")
        if not args.reset:
            print("note: existing rows were kept; pass --reset for a clean rebuild")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
