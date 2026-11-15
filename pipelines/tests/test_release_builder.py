"""Focused tests for the bounded multi-dimension release builder (Issue #48)."""

from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path
from types import ModuleType

import pytest
from analog_api.adapters.db import AnalysisJob, Candidate, FeatureObservation
from analog_api.adapters.seed import make_engine
from analog_pipelines.release import (
    DEFAULT_EDA_RELATIVE,
    DEFAULT_MANIFEST_RELATIVE,
    DEFAULT_SNAPSHOT_RELATIVE,
    ReleaseVerificationError,
    build_eda,
    build_release,
    verify_release,
)
from analog_pipelines.snapshot import load_snapshot, pretty_json_bytes, verify_snapshot
from analog_pipelines.storage import sha256_file
from sqlalchemy.orm import Session

REPO_ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT = REPO_ROOT / DEFAULT_SNAPSHOT_RELATIVE
D03_OBSERVATIONS = REPO_ROOT / "pipelines/pilots/d03_earth_power/observations.jsonl"
COARSE_PARTITION = (
    REPO_ROOT
    / "pipelines/coarse/real-pilot/index/partitions/pilot-alps"
    / "49a22a65f8e827a7f72a5552e224a3edac0bba8892e941896a69664f5bf87a3a.parquet"
)
COPY_PATHS = (
    "samples/snapshot",
    "pipelines/earth/manifests",
    "pipelines/planetary/manifests",
    "pipelines/worldcover/manifests",
    "pipelines/geology/typed",
    "pipelines/pilots",
    "pipelines/coarse/real-pilot",
    "catalog/dimension-source-inventory.yaml",
)


def _copy_approved_inputs(destination: Path) -> Path:
    for relative in COPY_PATHS:
        source = REPO_ROOT / relative
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(source, target, dirs_exist_ok=True)
        else:
            shutil.copy2(source, target)
    return destination


def _load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def _no_source_date_epoch(monkeypatch):
    monkeypatch.delenv("SOURCE_DATE_EPOCH", raising=False)


def test_two_clean_builds_reproduce_ids_counts_and_checksums(tmp_path: Path):
    first_path = tmp_path / "a.json"
    second_path = tmp_path / "b.json"
    first = build_release(root=REPO_ROOT, output=first_path)
    second = build_release(root=REPO_ROOT, output=second_path)

    assert first["release_id"].startswith("rel-")
    assert second["release_id"] == first["release_id"]
    assert first_path.read_bytes() == second_path.read_bytes()
    assert first["counts"] == second["counts"]
    assert [artifact["sha256"] for artifact in first["artifacts"]] == [
        artifact["sha256"] for artifact in second["artifacts"]
    ]

    counts = first["counts"]
    assert counts["fine_snapshot_id"] == "snap-0cef4d75b147866a"
    assert counts["fine_data_version"] == "dv-b46f3bad38ea"
    assert counts["fine_candidates"] == 113
    assert counts["fine_references"] == 23
    assert counts["fine_datasets"] == 9
    assert counts["fine_feature_observations"] == 565
    assert counts["fine_context_observations"] == 1243
    assert counts["typed_observations"] == 181
    assert counts["typed_by_dimension"] == {
        "typed-d02-geology": 56,
        "typed-d03-thermal": 20,
        "typed-d04-water-ice": 20,
        "typed-d05-illumination": 10,
        "typed-d06-regolith": 75,
    }
    assert counts["coarse_cells"] == 168
    assert counts["coarse_regions"] == 3
    assert counts["absent_body_dimension_cells"] == 8
    assert all(len(artifact["sha256"]) == 64 for artifact in first["artifacts"])


def test_committed_manifest_and_eda_match_a_clean_build(tmp_path: Path):
    committed_manifest = REPO_ROOT / DEFAULT_MANIFEST_RELATIVE
    committed_eda = REPO_ROOT / DEFAULT_EDA_RELATIVE
    assert committed_manifest.is_file(), "run the release build and commit its manifest"
    assert committed_eda.is_file(), "run the release build and commit its EDA report"

    manifest_path = tmp_path / "manifest.json"
    eda_path = tmp_path / "eda.json"
    manifest = build_release(root=REPO_ROOT, output=manifest_path)
    eda = build_eda(root=REPO_ROOT, release=manifest)
    eda_path.write_bytes(pretty_json_bytes(eda))
    assert manifest_path.read_bytes() == committed_manifest.read_bytes()
    assert eda_path.read_bytes() == committed_eda.read_bytes()


def test_verify_rejects_tampered_typed_observation(tmp_path: Path):
    root = _copy_approved_inputs(tmp_path / "repo")
    manifest_path = root / "release.manifest.json"
    build_release(root=root, output=manifest_path)

    observations = root / "pipelines/pilots/d03_earth_power/observations.jsonl"
    text = observations.read_text(encoding="utf-8")
    assert '"state": "detected"' in text
    observations.write_text(text.replace('"state": "detected"', '"state": "no_coverage"', 1))

    with pytest.raises(ReleaseVerificationError, match="artifact verification failed"):
        verify_release(manifest_path, root=root)


def test_verify_rejects_tampered_coarse_partition(tmp_path: Path):
    root = _copy_approved_inputs(tmp_path / "repo")
    manifest_path = root / "release.manifest.json"
    build_release(root=root, output=manifest_path)

    partition = (
        root / "pipelines/coarse/real-pilot/index/partitions/pilot-alps" / COARSE_PARTITION.name
    )
    partition.write_bytes(partition.read_bytes() + b"tampered")

    with pytest.raises(ReleaseVerificationError, match="artifact verification failed"):
        verify_release(manifest_path, root=root)


def test_verify_rejects_semantic_count_tampering(tmp_path: Path):
    manifest_path = tmp_path / "manifest.json"
    manifest = build_release(root=REPO_ROOT, output=manifest_path)
    manifest["counts"]["fine_candidates"] += 1
    manifest_path.write_bytes(
        (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")
    )
    with pytest.raises(ReleaseVerificationError):
        verify_release(manifest_path, root=REPO_ROOT)


def test_eda_distinguishes_origins_and_marks_absent_cells():
    eda = build_eda(root=REPO_ROOT)
    assert eda["global_claim"] is False
    assert eda["coverage_label"] == "bounded_local_subset_not_global"

    absent = {(item["dimension_code"], item["body"]) for item in eda["absent_dimensions"]}
    assert absent == {
        ("D03", "Moon"),
        ("D03", "Mars"),
        ("D04", "Moon"),
        ("D04", "Mars"),
        ("D05", "Moon"),
        ("D05", "Mars"),
        ("D06", "Moon"),
        ("D06", "Mars"),
    }
    assert all("no validated observation" in item["reason"] for item in eda["absent_dimensions"])

    dimensions = {item["dimension_code"]: item for item in eda["dimensions"]}
    d01_earth = dimensions["D01"]["bodies"]["Earth"]
    assert d01_earth["value_origins"]["derived_from_measured_dem"] == 565 + 168
    assert d01_earth["value_origins"]["mapped_classification"] == 1243
    assert d01_earth["coarse_cells"] == 168

    d03_earth = dimensions["D03"]["bodies"]["Earth"]
    assert d03_earth["attempted"] == 5
    assert d03_earth["valid"] == 20
    assert d03_earth["failed"] == 0
    assert d03_earth["value_origins"] == {"model_estimate": 20}

    d02_mars = dimensions["D02"]["bodies"]["Mars"]
    assert d02_mars["value_origins"] == {
        "mapped_evidence": 14,
        "spectral_context_not_abundance": 24,
    }

    quality = eda["quality"]
    assert quality["duplicate_observation_ids"] == 0
    assert quality["duplicate_asset_ids"] == 0
    assert quality["duplicate_candidate_ids"] == 0
    assert quality["shared_candidate_records"] == 113
    assert quality["overlapping_fine_candidate_pairs"] == 1
    assert quality["overlapping_coarse_window_pairs"] == 291


def test_build_does_not_mutate_pinned_inputs_and_old_snapshot_still_verifies(tmp_path: Path):
    snapshot = load_snapshot(SNAPSHOT)
    pinned = [SNAPSHOT, *[REPO_ROOT / entry["path"] for entry in snapshot["manifests"]]]
    before = {path: sha256_file(path) for path in pinned}

    build_release(root=REPO_ROOT, output=tmp_path / "manifest.json")

    after = {path: sha256_file(path) for path in pinned}
    assert after == before
    report = verify_snapshot(SNAPSHOT, root=REPO_ROOT)
    assert report["snapshot_id"] == "snap-0cef4d75b147866a"
    assert report["data_version"] == "dv-b46f3bad38ea"
    assert report["candidate_count"] == 113
    assert report["reference_count"] == 23


def test_sqlite_aoi_and_jobs_survive_a_copy_only_rebuild_drill(tmp_path: Path):
    build_index = _load_module(
        "analog_build_index_release_test", REPO_ROOT / "pipelines/build_index.py"
    )
    db_backup = _load_module("analog_db_backup_release_test", REPO_ROOT / "scripts/db_backup.py")

    database = tmp_path / "runtime.db"
    url = f"sqlite+pysqlite:///{database}"
    build_index.build_index(SNAPSHOT, url, reset=True)
    engine = make_engine(url)
    with Session(engine) as session:
        aoi = Candidate(
            location_id="earth-aoi-release-copy",
            display_name="Release copy AOI",
            lat=10.0,
            lon=20.0,
            west=19.99,
            south=9.99,
            east=20.01,
            north=10.01,
            dataset_id="copernicus-dem-glo90",
            window_size_m=1980,
            analysis_resolution_m=90,
            crs="EPSG:32634",
            valid_fraction=1.0,
            quality_flags=["on_demand_aoi"],
            notes="runtime data that must survive a baseline rebuild",
            data_kind="observed",
            origin="on_demand",
        )
        session.add(aoi)
        session.add(
            FeatureObservation(
                candidate_id=aoi.location_id,
                feature_id="slope_mean_deg",
                value=5.0,
                unit="degree",
                method_version="terrain-metrics-1.0.0",
                extraction_scale_m=90,
                valid_fraction=1.0,
                quality="verified",
            )
        )
        session.add(
            AnalysisJob(
                job_id="job-release-copy",
                status="completed",
                cache_key="cache-release-copy",
                idempotency_key="idem-release-copy",
                area_id=aoi.location_id,
                request_json={"body": "Earth"},
                provider_status={"fixture": {"status": "verified"}},
            )
        )
        session.commit()

    before = db_backup.database_fingerprint(database)
    result = db_backup.run_drill(database, snapshot=SNAPSHOT, workdir=tmp_path / "drill")
    after = db_backup.database_fingerprint(database)

    assert before["sha256"] == after["sha256"], "the source database must never be mutated"
    assert result["label"] == "copy_only_drill_source_never_modified"
    assert result["after_rebuild"]["data_version"] == "dv-b46f3bad38ea"
    for name, counts in result["before"]["counts"].items():
        if name in result["protected_counts_preserved"]:
            assert result["after_rebuild"]["counts"][name] == counts
    assert result["after_rebuild"]["counts"]["on_demand_candidates"] == 1
    assert result["after_rebuild"]["counts"]["analysis_jobs"] == 1
