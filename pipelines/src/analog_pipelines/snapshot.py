"""Deterministic dataset snapshots and optional object-store transfer.

A snapshot is a small JSON document that pins the committed pipeline manifests by
SHA-256. It deliberately contains no raster data: large artifacts belong in object
storage, while Git keeps the contract, the manifests, and this snapshot.

Contract (``snapshot_version`` 1.0.0)::

    snapshot_version, snapshot_id, created_at, generated_by, data_version,
    source_commit, manifests[], artifacts[], datasets[], notes

``snapshot_id`` is derived from the canonical content of the versioned identity fields
(``snapshot_version``, ``data_version``, ``manifests``, ``artifacts``, ``datasets``).
Volatile build metadata (``created_at``, ``generated_by``, ``source_commit``, ``notes``)
is excluded, so rebuilding the same manifest bytes yields the same id and artifact hashes.

CLI::

    python -m analog_pipelines.snapshot build
    python -m analog_pipelines.snapshot verify --snapshot samples/snapshot/analog-demo.snapshot.json
    python -m analog_pipelines.snapshot publish --snapshot ... --store-root data/object-store
    python -m analog_pipelines.snapshot fetch --snapshot-id snap-... --destination ...
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from analog_pipelines.storage import (
    ENV_BACKEND,
    ENV_BUCKET,
    ENV_ENDPOINT,
    S3_BACKENDS,
    LocalObjectStore,
    ObjectStore,
    ObjectStoreError,
    ObjectStoreNotConfigured,
    atomic_write_bytes,
    sha256_file,
    store_from_env,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
SNAPSHOT_VERSION = "1.0.0"
SNAPSHOT_ID_PATTERN = re.compile(r"^snap-[0-9a-f]{16}$")
DEFAULT_SNAPSHOT_RELATIVE = "samples/snapshot/analog-demo.snapshot.json"
MANIFEST_DIRS = (
    "pipelines/earth/manifests",
    "pipelines/worldcover/manifests",
    "pipelines/planetary/manifests",
)
GENERATED_BY = "pipelines/src/analog_pipelines/snapshot.py"
DEFAULT_NOTES = (
    "Pins committed JSON manifests by SHA-256. Raw rasters and caches are not included. "
    "Indexed windows are a local subset, not a global survey."
)
SNAPSHOT_PREFIX = "snapshots"
_DATASET_FIELDS = (
    "dataset_id",
    "provider",
    "product_id",
    "title",
    "url",
    "license",
    "license_url",
    "source_crs",
    "native_resolution_m",
    "source_kind",
)


class SnapshotError(RuntimeError):
    """A snapshot could not be built, parsed, or published."""


class SnapshotVerificationError(SnapshotError):
    """A snapshot or one of its pinned files failed verification."""


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def pretty_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def _display_path(path: Path, root: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(root.resolve()).as_posix()
    except ValueError:
        return resolved.as_posix()


def resolve_snapshot_path(root: Path, value: str) -> Path:
    candidate = Path(value).expanduser()
    return candidate if candidate.is_absolute() else root / candidate


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as exc:
        raise SnapshotError(f"cannot read {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise SnapshotError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise SnapshotError(f"expected a JSON object in {path}")
    return value


def _parse_json_bytes(data: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SnapshotError(f"invalid JSON in {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise SnapshotError(f"expected a JSON object in {label}")
    return value


def _manifest_paths(root: Path) -> list[Path]:
    paths: list[Path] = []
    for relative in MANIFEST_DIRS:
        directory = root / relative
        if directory.is_dir():
            paths.extend(path for path in directory.glob("*.json") if path.is_file())
    unique: dict[str, Path] = {_display_path(path, root): path for path in paths}
    return [unique[key] for key in sorted(unique)]


def _strict_manifest(raw: Mapping[str, Any]) -> Any | None:
    from analog_api.domain.manifest import Manifest

    try:
        return Manifest.model_validate(dict(raw))
    except ValueError:
        return None


def _default_created_at(raw_manifests: Iterable[Mapping[str, Any]]) -> str:
    epoch = os.environ.get("SOURCE_DATE_EPOCH", "").strip()
    if epoch:
        try:
            moment = datetime.fromtimestamp(int(epoch), UTC)
        except ValueError as exc:
            raise SnapshotError(f"invalid SOURCE_DATE_EPOCH: {epoch!r}") from exc
        return moment.strftime("%Y-%m-%dT%H:%M:%SZ")
    generated = sorted(
        value
        for raw in raw_manifests
        if isinstance((value := raw.get("generated_at")), str) and value
    )
    if generated:
        return generated[-1]
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _artifact_path_for(output: Path) -> Path:
    name = output.name
    prefix = name[: -len(".snapshot.json")] if name.endswith(".snapshot.json") else output.stem
    return output.with_name(f"{prefix}.index-summary.json")


def _summarize_datasets(
    raw_manifests: list[tuple[str, dict[str, Any]]],
) -> list[dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    kinds: dict[str, str | None] = {}
    qualities: dict[str, set[str]] = defaultdict(set)
    candidate_counts: Counter[str] = Counter()
    reference_counts: Counter[str] = Counter()
    manifest_paths: dict[str, set[str]] = defaultdict(set)

    for relative, raw in raw_manifests:
        kind = raw.get("data_kind")
        if not isinstance(kind, str):
            kind = None
        datasets = raw.get("datasets") or []
        if not isinstance(datasets, list):
            raise SnapshotError(f"{relative}: 'datasets' must be a list")
        for dataset in datasets:
            if not isinstance(dataset, dict):
                raise SnapshotError(f"{relative}: dataset entries must be objects")
            dataset_id = dataset.get("dataset_id")
            if not isinstance(dataset_id, str) or not dataset_id:
                raise SnapshotError(f"{relative}: dataset is missing dataset_id")
            record = {field: dataset.get(field) for field in _DATASET_FIELDS}
            record["dataset_id"] = dataset_id
            if dataset_id in records and records[dataset_id] != record:
                raise SnapshotError(
                    f"conflicting metadata for dataset {dataset_id!r} across manifests"
                )
            records[dataset_id] = record
            kinds.setdefault(dataset_id, kind)
            manifest_paths[dataset_id].add(relative)

        for key, counter in (("candidates", candidate_counts), ("references", reference_counts)):
            entries = raw.get(key) or []
            if not isinstance(entries, list):
                raise SnapshotError(f"{relative}: {key!r} must be a list")
            for entry in entries:
                if not isinstance(entry, dict):
                    raise SnapshotError(f"{relative}: {key} entries must be objects")
                dataset_id = entry.get("dataset_id")
                if isinstance(dataset_id, str):
                    counter[dataset_id] += 1
                features = entry.get("features") or {}
                if isinstance(features, dict):
                    for observation in features.values():
                        if isinstance(observation, dict) and isinstance(
                            observation.get("quality"), str
                        ):
                            qualities[dataset_id].add(observation["quality"])
                context = entry.get("context_observations") or {}
                if isinstance(context, dict):
                    for observation in context.values():
                        if isinstance(observation, dict) and isinstance(
                            observation.get("quality"), str
                        ):
                            qualities[dataset_id].add(observation["quality"])

    summary: list[dict[str, Any]] = []
    for dataset_id in sorted(records):
        observed = qualities.get(dataset_id, set())
        if kinds.get(dataset_id) == "synthetic" or "synthetic" in observed:
            status = "synthetic"
        elif "suspect" in observed:
            status = "suspect"
        elif observed and observed <= {"verified"}:
            status = "verified"
        else:
            status = "in_progress"
        record = dict(records[dataset_id])
        record.update(
            {
                "status": status,
                "verified": status == "verified",
                "candidate_count": candidate_counts.get(dataset_id, 0),
                "reference_count": reference_counts.get(dataset_id, 0),
                "observation_qualities": sorted(qualities.get(dataset_id, set())),
                "manifest_paths": sorted(manifest_paths[dataset_id]),
            }
        )
        summary.append(record)
    return summary


def _candidate_entries(raw_manifests: list[tuple[str, dict[str, Any]]]) -> list[dict[str, Any]]:
    # Context overlays repeat the same location_id on purpose. The index stores one
    # candidate row, so the summary counts unique ids while preserving the first
    # terrain manifest as the candidate's owning dataset.
    unique: dict[str, dict[str, Any]] = {}
    for _, raw in raw_manifests:
        for candidate in raw.get("candidates") or []:
            if not isinstance(candidate, dict):
                continue
            location_id = candidate.get("location_id")
            if not isinstance(location_id, str):
                continue
            unique.setdefault(
                location_id,
                {
                    "location_id": location_id,
                    "display_name": candidate.get("display_name"),
                    "dataset_id": candidate.get("dataset_id"),
                    "data_kind": raw.get("data_kind"),
                },
            )
    return [unique[key] for key in sorted(unique)]


def _reference_entries(raw_manifests: list[tuple[str, dict[str, Any]]]) -> list[dict[str, Any]]:
    unique: dict[str, dict[str, Any]] = {}
    for _, raw in raw_manifests:
        for reference in raw.get("references") or []:
            if not isinstance(reference, dict):
                continue
            reference_id = reference.get("reference_id")
            if not isinstance(reference_id, str):
                continue
            unique.setdefault(
                reference_id,
                {
                    "reference_id": reference_id,
                    "body": reference.get("body"),
                    "dataset_id": reference.get("dataset_id"),
                },
            )
    return [unique[key] for key in sorted(unique)]


def _snapshot_identity(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "snapshot_version": snapshot.get("snapshot_version"),
        "data_version": snapshot.get("data_version"),
        "manifests": snapshot.get("manifests"),
        "artifacts": snapshot.get("artifacts"),
        "datasets": snapshot.get("datasets"),
    }


def _snapshot_id(snapshot: Mapping[str, Any]) -> str:
    digest = hashlib.sha256(canonical_json_bytes(_snapshot_identity(snapshot))).hexdigest()
    return f"snap-{digest[:16]}"


def build_snapshot(
    *,
    root: Path = REPO_ROOT,
    output: Path | None = None,
    artifact: Path | None = None,
    created_at: str | None = None,
    source_commit: str | None = None,
    generated_by: str = GENERATED_BY,
    notes: str = DEFAULT_NOTES,
) -> dict[str, Any]:
    """Build and write a deterministic snapshot plus its JSON index-summary artifact."""
    root = root.resolve()
    output = (output or root / DEFAULT_SNAPSHOT_RELATIVE).resolve()
    artifact = (artifact or _artifact_path_for(output)).resolve()

    manifest_paths = _manifest_paths(root)
    if not manifest_paths:
        raise SnapshotError(f"no manifests found under {root} ({', '.join(MANIFEST_DIRS)})")

    raw_manifests: list[tuple[str, dict[str, Any]]] = []
    manifest_entries: list[dict[str, Any]] = []
    seedable_manifests: list[Any] = []
    for path in manifest_paths:
        relative = _display_path(path, root)
        raw = _read_json(path)
        raw_manifests.append((relative, raw))
        strict = _strict_manifest(raw)
        entry: dict[str, Any] = {
            "path": relative,
            "sha256": sha256_file(path),
            "data_kind": raw.get("data_kind") or "unknown",
            "bytes": path.stat().st_size,
            "seedable": strict is not None,
        }
        if strict is None:
            entry["index_note"] = "not a candidate/reference manifest; not loaded into the index"
        else:
            seedable_manifests.append(strict)
        manifest_entries.append(entry)

    from analog_api.adapters.seed import compute_data_version

    data_version = compute_data_version(seedable_manifests)
    datasets = _summarize_datasets(raw_manifests)
    candidate_entries = _candidate_entries(raw_manifests)
    reference_entries = _reference_entries(raw_manifests)
    seedable_count = len(seedable_manifests)
    counts = {
        "manifests": len(manifest_entries),
        "seedable_manifests": seedable_count,
        "datasets": len(datasets),
        "candidates": len(candidate_entries),
        "references": len(reference_entries),
    }
    summary_payload = {
        "artifact_version": "1.0.0",
        "generated_by": generated_by,
        "data_version": data_version,
        "counts": counts,
        "datasets": [
            {
                "dataset_id": item["dataset_id"],
                "status": item["status"],
                "verified": item["verified"],
            }
            for item in datasets
        ],
        "candidates": candidate_entries,
        "references": reference_entries,
    }
    artifact_bytes = pretty_json_bytes(summary_payload)
    artifact_entry = {
        "path": _display_path(artifact, root),
        "sha256": hashlib.sha256(artifact_bytes).hexdigest(),
        "kind": "index_summary",
        "bytes": len(artifact_bytes),
    }

    snapshot: dict[str, Any] = {
        "snapshot_version": SNAPSHOT_VERSION,
        "snapshot_id": "",
        "created_at": created_at or _default_created_at(raw for _, raw in raw_manifests),
        "generated_by": generated_by,
        "data_version": data_version,
        "source_commit": source_commit,
        "manifests": manifest_entries,
        "artifacts": [artifact_entry],
        "datasets": datasets,
        "notes": notes,
    }
    snapshot["snapshot_id"] = _snapshot_id(snapshot)

    # Fail closed if a manifest changed between hashing and writing. The snapshot must
    # describe bytes that still exist at that path.
    for entry in manifest_entries:
        if sha256_file(resolve_snapshot_path(root, entry["path"])) != entry["sha256"]:
            raise SnapshotError(f"manifest changed during snapshot build: {entry['path']}")

    atomic_write_bytes(artifact, artifact_bytes)
    atomic_write_bytes(output, pretty_json_bytes(snapshot))
    return snapshot


def load_snapshot(path: Path) -> dict[str, Any]:
    snapshot = _read_json(path)
    required = (
        "snapshot_version",
        "snapshot_id",
        "created_at",
        "generated_by",
        "data_version",
        "source_commit",
        "manifests",
        "artifacts",
        "datasets",
        "notes",
    )
    missing = [key for key in required if key not in snapshot]
    if missing:
        raise SnapshotError(f"snapshot {path} is missing required field(s): {', '.join(missing)}")
    for key in ("manifests", "artifacts", "datasets"):
        if not isinstance(snapshot[key], list):
            raise SnapshotError(f"snapshot {path}: {key!r} must be a list")
    return snapshot


def verify_snapshot(
    snapshot_path: Path,
    *,
    root: Path = REPO_ROOT,
    check_artifacts: bool = True,
) -> dict[str, Any]:
    """Verify snapshot id, manifest SHA-256 values, data version, and artifacts."""
    root = root.resolve()
    snapshot_path = snapshot_path.resolve()
    snapshot = load_snapshot(snapshot_path)
    if snapshot["snapshot_version"] != SNAPSHOT_VERSION:
        raise SnapshotVerificationError(
            f"unsupported snapshot_version {snapshot['snapshot_version']!r}; "
            f"supported: {SNAPSHOT_VERSION}"
        )
    expected_id = _snapshot_id(snapshot)
    if snapshot["snapshot_id"] != expected_id:
        raise SnapshotVerificationError(
            f"snapshot id mismatch: file says {snapshot['snapshot_id']!r}, "
            f"content hashes to {expected_id!r}"
        )

    from analog_api.adapters.seed import compute_data_version
    from analog_api.domain.manifest import Manifest

    seedable: list[Any] = []
    manifest_verified = 0
    candidate_ids: set[str] = set()
    reference_ids: set[str] = set()
    for entry in snapshot["manifests"]:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            raise SnapshotVerificationError("manifest entry is missing a path")
        path = resolve_snapshot_path(root, entry["path"])
        if not path.is_file():
            raise SnapshotVerificationError(f"pinned manifest is missing: {entry['path']}")
        actual = sha256_file(path)
        if actual != entry.get("sha256"):
            raise SnapshotVerificationError(
                f"manifest checksum mismatch for {entry['path']}: "
                f"pinned {entry.get('sha256')}, actual {actual}"
            )
        if isinstance(entry.get("bytes"), int) and path.stat().st_size != entry["bytes"]:
            raise SnapshotVerificationError(
                f"manifest size mismatch for {entry['path']}: "
                f"pinned {entry['bytes']}, actual {path.stat().st_size}"
            )
        manifest_verified += 1
        raw = _read_json(path)
        candidate_ids.update(
            item["location_id"]
            for item in raw.get("candidates") or []
            if isinstance(item, dict) and isinstance(item.get("location_id"), str)
        )
        reference_ids.update(
            item["reference_id"]
            for item in raw.get("references") or []
            if isinstance(item, dict) and isinstance(item.get("reference_id"), str)
        )
        if entry.get("seedable", True):
            try:
                seedable.append(Manifest.model_validate(raw))
            except ValueError as exc:
                raise SnapshotVerificationError(
                    f"manifest {entry['path']} is marked seedable but does not validate: {exc}"
                ) from exc

    expected_version = compute_data_version(seedable)
    if expected_version != snapshot["data_version"]:
        raise SnapshotVerificationError(
            f"data_version mismatch: pinned {snapshot['data_version']!r}, "
            f"manifests compute {expected_version!r}"
        )

    artifact_verified = 0
    if check_artifacts:
        for entry in snapshot["artifacts"]:
            if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
                raise SnapshotVerificationError("artifact entry is missing a path")
            path = resolve_snapshot_path(root, entry["path"])
            if not path.is_file():
                raise SnapshotVerificationError(f"pinned artifact is missing: {entry['path']}")
            data = path.read_bytes()
            actual = hashlib.sha256(data).hexdigest()
            if actual != entry.get("sha256"):
                raise SnapshotVerificationError(
                    f"artifact checksum mismatch for {entry['path']}: "
                    f"pinned {entry.get('sha256')}, actual {actual}"
                )
            if isinstance(entry.get("bytes"), int) and len(data) != entry["bytes"]:
                raise SnapshotVerificationError(
                    f"artifact size mismatch for {entry['path']}: "
                    f"pinned {entry['bytes']}, actual {len(data)}"
                )
            artifact_verified += 1

    return {
        "snapshot_id": snapshot["snapshot_id"],
        "data_version": snapshot["data_version"],
        "manifests_verified": manifest_verified,
        "artifacts_verified": artifact_verified,
        "seedable_manifests": len(seedable),
        "candidate_count": len(candidate_ids),
        "reference_count": len(reference_ids),
        "datasets": [item.get("dataset_id") for item in snapshot["datasets"]],
    }


def publish_snapshot(
    store: ObjectStore,
    snapshot_path: Path,
    *,
    root: Path = REPO_ROOT,
) -> dict[str, Any]:
    """Upload a verified snapshot and its JSON artifacts under an immutable prefix."""
    root = root.resolve()
    snapshot_path = snapshot_path.resolve()
    verify_snapshot(snapshot_path, root=root, check_artifacts=True)
    snapshot = load_snapshot(snapshot_path)
    snapshot_id = snapshot["snapshot_id"]
    prefix = f"{SNAPSHOT_PREFIX}/{snapshot_id}"

    payloads = {"snapshot.json": snapshot_path.read_bytes()}
    basenames: set[str] = set()
    for entry in snapshot["artifacts"]:
        basename = PurePosixPath(entry["path"]).name
        if basename in basenames:
            raise SnapshotError(f"duplicate artifact basename in snapshot: {basename}")
        basenames.add(basename)
        data = resolve_snapshot_path(root, entry["path"]).read_bytes()
        if hashlib.sha256(data).hexdigest() != entry["sha256"]:
            raise SnapshotVerificationError(f"artifact changed during publish: {entry['path']}")
        payloads[f"artifacts/{basename}"] = data
    for entry in snapshot["manifests"]:
        data = resolve_snapshot_path(root, entry["path"]).read_bytes()
        if hashlib.sha256(data).hexdigest() != entry["sha256"]:
            raise SnapshotVerificationError(f"manifest changed during publish: {entry['path']}")
        payloads[entry["path"]] = data
    files = {key: hashlib.sha256(data).hexdigest() for key, data in payloads.items()}
    payloads["checksums.json"] = pretty_json_bytes(
        {
            "snapshot_id": snapshot_id,
            "snapshot_version": snapshot["snapshot_version"],
            "files": files,
        }
    )
    # Validate all existing objects before any mutation; never rewrite an immutable revision.
    for relative, data in payloads.items():
        key = f"{prefix}/{relative}"
        if store.exists(key) and store.get_bytes(key) != data:
            raise SnapshotVerificationError(f"immutable object differs: {key}")
    keys = []
    for relative, data in payloads.items():
        key = f"{prefix}/{relative}"
        store.put_bytes(key, data, "application/json")
        keys.append(key)
    return {"snapshot_id": snapshot_id, "prefix": prefix, "keys": keys, "checksums": files}


def fetch_snapshot(
    store: ObjectStore,
    snapshot_id: str,
    *,
    destination: Path,
    root: Path = REPO_ROOT,
) -> dict[str, Any]:
    """Download a snapshot and its artifacts, verifying every recorded SHA-256."""
    if not SNAPSHOT_ID_PATTERN.fullmatch(snapshot_id):
        raise SnapshotError(f"invalid snapshot id: {snapshot_id!r}")
    prefix = f"{SNAPSHOT_PREFIX}/{snapshot_id}"
    try:
        checksums = _parse_json_bytes(
            store.get_bytes(f"{prefix}/checksums.json"), f"{prefix}/checksums.json"
        )
        snapshot_bytes = store.get_bytes(f"{prefix}/snapshot.json")
    except ObjectStoreError as exc:
        raise SnapshotError(f"cannot fetch snapshot {snapshot_id}: {exc}") from exc
    if checksums.get("snapshot_id") != snapshot_id:
        raise SnapshotVerificationError("checksums.json does not belong to this snapshot id")
    files = checksums.get("files")
    if not isinstance(files, dict):
        raise SnapshotVerificationError("checksums.json is missing a 'files' mapping")
    snapshot_sha = hashlib.sha256(snapshot_bytes).hexdigest()
    if files.get("snapshot.json") != snapshot_sha:
        raise SnapshotVerificationError("downloaded snapshot bytes do not match checksums.json")

    snapshot = _parse_json_bytes(snapshot_bytes, f"{prefix}/snapshot.json")
    if _snapshot_id(snapshot) != snapshot_id:
        raise SnapshotVerificationError("downloaded snapshot content does not match its id")

    destination = destination.resolve()
    snapshot_path = destination / f"{snapshot_id}.snapshot.json"
    atomic_write_bytes(snapshot_path, snapshot_bytes)

    artifact_paths: list[str] = []
    for entry in snapshot.get("artifacts") or []:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            raise SnapshotVerificationError("artifact entry is missing a path")
        basename = PurePosixPath(entry["path"]).name
        remote_key = f"{prefix}/artifacts/{basename}"
        try:
            data = store.get_bytes(remote_key)
        except ObjectStoreError as exc:
            raise SnapshotError(f"cannot fetch {remote_key}: {exc}") from exc
        actual = hashlib.sha256(data).hexdigest()
        if actual != entry.get("sha256"):
            raise SnapshotVerificationError(f"artifact checksum mismatch for {basename}")
        if f"artifacts/{basename}" in files and files[f"artifacts/{basename}"] != actual:
            raise SnapshotVerificationError(f"artifact checksums.json mismatch for {basename}")
        path = destination / "artifacts" / basename
        atomic_write_bytes(path, data)
        # Retain the original layout too, so a clean download can rebuild without Git manifests.
        atomic_write_bytes(resolve_snapshot_path(destination, entry["path"]), data)
        artifact_paths.append(str(path))

    manifests_verified = 0
    manifests_downloaded = 0
    for entry in snapshot.get("manifests") or []:
        relative = entry["path"]
        if relative in files:
            data = store.get_bytes(f"{prefix}/{relative}")
            digest = hashlib.sha256(data).hexdigest()
            if digest != entry["sha256"] or digest != files[relative]:
                raise SnapshotVerificationError(
                    f"downloaded manifest checksum mismatch: {relative}"
                )
            atomic_write_bytes(resolve_snapshot_path(destination, relative), data)
            manifests_downloaded += 1
        path = resolve_snapshot_path(root, relative)
        if path.is_file():
            if sha256_file(path) != entry.get("sha256"):
                raise SnapshotVerificationError(
                    f"local manifest does not match snapshot: {entry['path']}"
                )
            manifests_verified += 1

    return {
        "snapshot_id": snapshot_id,
        "snapshot_path": str(snapshot_path),
        "artifact_paths": artifact_paths,
        "manifests_verified_locally": manifests_verified,
        "manifests_downloaded": manifests_downloaded,
    }


def _store_for_transfer(store_root: str | None) -> ObjectStore:
    if store_root:
        return LocalObjectStore(Path(store_root))
    from analog_pipelines.storage import load_local_env

    load_local_env()
    values = os.environ
    backend = values.get(ENV_BACKEND, "").strip().lower()
    if backend in S3_BACKENDS or (
        values.get(ENV_ENDPOINT, "").strip() and values.get(ENV_BUCKET, "").strip()
    ):
        return store_from_env()
    raise ObjectStoreNotConfigured(
        "No object store is configured for publish/fetch. Pass --store-root (local) or "
        f"set {ENV_BACKEND}=s3/r2 plus {ENV_ENDPOINT}, {ENV_BUCKET}, and credentials. "
        "Credentials are never prompted for."
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m analog_pipelines.snapshot",
        description="Build, verify, publish, and fetch deterministic dataset snapshots.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build", help="Build a snapshot from committed manifests")
    build.add_argument("--root", default=str(REPO_ROOT))
    build.add_argument("--output", default=None)
    build.add_argument("--artifact", default=None)
    build.add_argument("--created-at", default=None)
    build.add_argument("--source-commit", default=os.environ.get("ANALOG_SOURCE_COMMIT"))
    build.add_argument("--notes", default=DEFAULT_NOTES)

    verify = sub.add_parser("verify", help="Verify a snapshot and its pinned files")
    verify.add_argument("--snapshot", default=str(REPO_ROOT / DEFAULT_SNAPSHOT_RELATIVE))
    verify.add_argument("--root", default=str(REPO_ROOT))
    verify.add_argument(
        "--skip-artifacts",
        action="store_true",
        help="Verify snapshot identity and manifests only (used by index rebuilds).",
    )

    publish = sub.add_parser("publish", help="Publish a snapshot to an object store")
    publish.add_argument("--snapshot", default=str(REPO_ROOT / DEFAULT_SNAPSHOT_RELATIVE))
    publish.add_argument("--root", default=str(REPO_ROOT))
    publish.add_argument("--store-root", default=None)

    fetch = sub.add_parser("fetch", help="Fetch a snapshot from an object store")
    fetch.add_argument("--snapshot-id", required=True)
    fetch.add_argument("--destination", required=True)
    fetch.add_argument("--root", default=str(REPO_ROOT))
    fetch.add_argument("--store-root", default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "build":
            snapshot = build_snapshot(
                root=Path(args.root),
                output=Path(args.output) if args.output else None,
                artifact=Path(args.artifact) if args.artifact else None,
                created_at=args.created_at,
                source_commit=args.source_commit,
                notes=args.notes,
            )
            print(
                f"snapshot_id={snapshot['snapshot_id']} "
                f"data_version={snapshot['data_version']} "
                f"manifests={len(snapshot['manifests'])} "
                f"datasets={len(snapshot['datasets'])}"
            )
            return 0
        if args.command == "verify":
            report = verify_snapshot(
                Path(args.snapshot),
                root=Path(args.root),
                check_artifacts=not args.skip_artifacts,
            )
            print(
                f"OK snapshot_id={report['snapshot_id']} data_version={report['data_version']} "
                f"manifests={report['manifests_verified']} "
                f"artifacts={report['artifacts_verified']} datasets={len(report['datasets'])}"
            )
            return 0
        store = _store_for_transfer(args.store_root)
        if args.command == "publish":
            result = publish_snapshot(store, Path(args.snapshot), root=Path(args.root))
            print(
                f"published snapshot_id={result['snapshot_id']} prefix={result['prefix']} "
                f"objects={len(result['keys'])}"
            )
            return 0
        result = fetch_snapshot(
            store,
            args.snapshot_id,
            destination=Path(args.destination),
            root=Path(args.root),
        )
        print(
            f"fetched snapshot_id={result['snapshot_id']} path={result['snapshot_path']} "
            f"artifacts={len(result['artifact_paths'])}"
        )
        return 0
    except (SnapshotError, ObjectStoreError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
