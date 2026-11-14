from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path
from types import ModuleType

import pytest
from analog_pipelines.snapshot import (
    SnapshotVerificationError,
    build_snapshot,
    fetch_snapshot,
    publish_snapshot,
    verify_snapshot,
)
from analog_pipelines.snapshot import (
    main as snapshot_main,
)
from analog_pipelines.storage import LocalObjectStore

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATTERNS = (
    "pipelines/earth/manifests/*.json",
    "pipelines/planetary/manifests/*.json",
    "pipelines/worldcover/manifests/*.json",
)
BUILD_INDEX_PATH = REPO_ROOT / "pipelines" / "build_index.py"


@pytest.fixture()
def demo_root(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    copied = 0
    for pattern in MANIFEST_PATTERNS:
        for source in sorted(REPO_ROOT.glob(pattern)):
            destination = root / source.relative_to(REPO_ROOT)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            copied += 1
    assert copied > 0, "no committed manifests found to build the demo snapshot"
    return root


@pytest.fixture()
def snapshot_path(demo_root: Path) -> Path:
    build_snapshot(root=demo_root)
    return demo_root / "samples/snapshot/analog-demo.snapshot.json"


@pytest.fixture(scope="module")
def build_index_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("analog_build_index", BUILD_INDEX_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_snapshot_is_deterministic_and_checksummed(demo_root: Path) -> None:
    first = build_snapshot(root=demo_root)
    output = demo_root / "samples/snapshot/analog-demo.snapshot.json"
    artifact = demo_root / "samples/snapshot/analog-demo.index-summary.json"
    first_bytes = output.read_bytes()
    first_artifact = artifact.read_bytes()
    first_id = first["snapshot_id"]

    second = build_snapshot(root=demo_root)
    assert second["snapshot_id"] == first_id
    assert second["snapshot_id"].startswith("snap-")
    assert len(second["snapshot_id"]) == 21
    assert output.read_bytes() == first_bytes
    assert artifact.read_bytes() == first_artifact
    assert second["artifacts"][0]["sha256"] == first["artifacts"][0]["sha256"]

    report = verify_snapshot(output, root=demo_root)
    assert report["snapshot_id"] == first_id
    assert report["manifests_verified"] == len(first["manifests"])
    assert report["artifacts_verified"] == 1
    assert report["seedable_manifests"] == len(first["manifests"])


def test_snapshot_contains_provenance_not_raster_data(snapshot_path: Path) -> None:
    snapshot = _load(snapshot_path)
    assert snapshot["snapshot_version"] == "1.0.0"
    assert snapshot["data_version"].startswith("dv-")
    assert snapshot["source_commit"] is None
    assert len(snapshot["manifests"]) >= 1
    for entry in snapshot["manifests"]:
        assert entry["path"].endswith(".json")
        assert len(entry["sha256"]) == 64
        assert entry["data_kind"] == "observed"
    for dataset in snapshot["datasets"]:
        assert dataset["provider"]
        assert dataset["license"]
        assert dataset["status"] in {"verified", "synthetic", "suspect", "in_progress"}
    assert not list(snapshot_path.parent.glob("*.tif"))


def test_manifest_tampering_fails_verification(snapshot_path: Path, demo_root: Path) -> None:
    snapshot = _load(snapshot_path)
    manifest = demo_root / snapshot["manifests"][0]["path"]
    original = manifest.read_bytes()
    manifest.write_bytes(original + b"\n")
    with pytest.raises(SnapshotVerificationError, match="checksum mismatch"):
        verify_snapshot(snapshot_path, root=demo_root)
    manifest.write_bytes(original)
    verify_snapshot(snapshot_path, root=demo_root)


def test_artifact_tampering_fails_verification(snapshot_path: Path, demo_root: Path) -> None:
    snapshot = _load(snapshot_path)
    artifact = demo_root / snapshot["artifacts"][0]["path"]
    original = artifact.read_bytes()
    artifact.write_bytes(b"{}\n")
    with pytest.raises(SnapshotVerificationError, match="artifact checksum mismatch"):
        verify_snapshot(snapshot_path, root=demo_root)
    artifact.write_bytes(original)


def test_build_index_rebuilds_identical_counts(
    snapshot_path: Path,
    demo_root: Path,
    tmp_path: Path,
    build_index_module: ModuleType,
) -> None:
    snapshot = _load(snapshot_path)
    summary = _load(demo_root / snapshot["artifacts"][0]["path"])
    database = tmp_path / "index.db"
    database_url = f"sqlite+pysqlite:///{database}"

    first = build_index_module.build_index(snapshot_path, database_url, reset=True, root=demo_root)
    second = build_index_module.build_index(snapshot_path, database_url, reset=True, root=demo_root)

    assert first["snapshot_id"] == snapshot["snapshot_id"]
    assert first["data_version"] == snapshot["data_version"]
    assert first["counts"] == second["counts"]
    assert first["counts"]["data_version"] == snapshot["data_version"]
    assert first["counts"]["candidates"] == summary["counts"]["candidates"]
    assert first["counts"]["references"] == summary["counts"]["references"]
    assert first["counts"]["candidates"] > 0
    assert first["counts"]["datasets"] >= 1
    assert first["counts"]["features"] > 0
    assert first["counts"]["context_observations"] > 0
    assert first["counts"]["dataset_metadata"] == second["counts"]["dataset_metadata"]
    assert database.is_file()


def test_build_index_verifies_before_seeding(
    snapshot_path: Path,
    demo_root: Path,
    tmp_path: Path,
    build_index_module: ModuleType,
) -> None:
    snapshot = _load(snapshot_path)
    manifest = demo_root / snapshot["manifests"][0]["path"]
    manifest.write_bytes(manifest.read_bytes() + b"\n")
    database = tmp_path / "never-created.db"
    with pytest.raises(SnapshotVerificationError):
        build_index_module.build_index(
            snapshot_path,
            f"sqlite+pysqlite:///{database}",
            reset=True,
            root=demo_root,
        )
    assert not database.exists()


def test_dry_run_does_not_create_database(
    snapshot_path: Path,
    demo_root: Path,
    tmp_path: Path,
    build_index_module: ModuleType,
) -> None:
    database = tmp_path / "dry-run.db"
    result = build_index_module.build_index(
        snapshot_path,
        f"sqlite+pysqlite:///{database}",
        dry_run=True,
        root=demo_root,
    )
    assert result["dry_run"] is True
    assert result["counts"]["candidates"] > 0
    assert result["counts"]["context_observations"] > 0
    assert not database.exists()


def test_local_publish_and_fetch_roundtrip(
    snapshot_path: Path, demo_root: Path, tmp_path: Path
) -> None:
    store = LocalObjectStore(tmp_path / "object-store")
    published = publish_snapshot(store, snapshot_path, root=demo_root)
    snapshot_id = published["snapshot_id"]
    assert published["prefix"] == f"snapshots/{snapshot_id}"
    assert f"snapshots/{snapshot_id}/snapshot.json" in published["keys"]
    assert f"snapshots/{snapshot_id}/checksums.json" in published["keys"]

    destination = tmp_path / "fetched"
    result = fetch_snapshot(store, snapshot_id, destination=destination, root=demo_root)
    fetched_snapshot = Path(result["snapshot_path"])
    assert fetched_snapshot.read_bytes() == snapshot_path.read_bytes()
    assert result["manifests_verified_locally"] == len(_load(snapshot_path)["manifests"])
    artifact_paths = [Path(path) for path in result["artifact_paths"]]
    assert artifact_paths and artifact_paths[0].is_file()
    assert (
        artifact_paths[0].read_bytes()
        == (demo_root / _load(snapshot_path)["artifacts"][0]["path"]).read_bytes()
    )


def test_publish_and_fetch_without_store_fail_clearly(
    snapshot_path: Path,
    demo_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    for name in (
        "ANALOG_OBJECT_STORE_BACKEND",
        "ANALOG_OBJECT_STORE_ENDPOINT",
        "ANALOG_OBJECT_STORE_BUCKET",
        "ANALOG_OBJECT_STORE_REGION",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
    ):
        monkeypatch.delenv(name, raising=False)

    assert (
        snapshot_main(
            [
                "publish",
                "--snapshot",
                str(snapshot_path),
                "--root",
                str(demo_root),
            ]
        )
        == 2
    )
    assert "No object store is configured" in capsys.readouterr().err
    assert (
        snapshot_main(
            [
                "fetch",
                "--snapshot-id",
                "snap-0000000000000000",
                "--destination",
                str(tmp_path / "unused"),
                "--root",
                str(demo_root),
            ]
        )
        == 2
    )
    assert "No object store is configured" in capsys.readouterr().err


def test_remote_snapshot_restores_manifests_without_original_checkout(
    snapshot_path, demo_root, tmp_path
):
    store = LocalObjectStore(tmp_path / "store")
    published = publish_snapshot(store, snapshot_path, root=demo_root)
    empty_checkout = tmp_path / "empty-checkout"
    empty_checkout.mkdir()
    destination = tmp_path / "restore"
    fetched = fetch_snapshot(
        store, published["snapshot_id"], destination=destination, root=empty_checkout
    )
    assert fetched["manifests_verified_locally"] == 0
    assert fetched["manifests_downloaded"] == len(_load(snapshot_path)["manifests"])
    report = verify_snapshot(Path(fetched["snapshot_path"]), root=destination)
    assert report["snapshot_id"] == published["snapshot_id"]


def test_publish_rejects_changed_immutable_object(snapshot_path, demo_root, tmp_path):
    store = LocalObjectStore(tmp_path / "store")
    first = publish_snapshot(store, snapshot_path, root=demo_root)
    key = first["prefix"] + "/snapshot.json"
    store.put_bytes(key, b'{"tampered":true}')
    with pytest.raises(SnapshotVerificationError, match="immutable"):
        publish_snapshot(store, snapshot_path, root=demo_root)
