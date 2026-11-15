from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import replace
from pathlib import Path

import pytest
from analog_pipelines.global_terrain_report import (
    ARTIFACT_NAMES,
    DOCUMENT_NAME,
    GlobalClaimError,
    GlobalReportError,
    ReportInputs,
    assert_not_global_claim,
    build_global_terrain_report,
    check_global_terrain_report,
    write_global_terrain_report,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
INDEX_ROOT = REPO_ROOT / "pipelines" / "coarse" / "real-pilot" / "index"
BATCH_DIR = (
    REPO_ROOT / "pipelines" / "coarse" / "real-pilot" / "batch" / "geomorpho-pilot-real-20261115"
)
PILOT_DOC = REPO_ROOT / "docs" / "GEOMORPHO_REAL_PILOT.md"
DB_REPORT = REPO_ROOT / "docs" / "DB_CAPACITY_REPORT.md"
REFINE_JSON = REPO_ROOT / "pipelines" / "coarse" / "real-pilot" / "refine" / "refinement.json"
COMMITTED_DOC = REPO_ROOT / "docs" / DOCUMENT_NAME
COMMITTED_ARTIFACTS = REPO_ROOT / "docs" / "global-terrain-results"


def _inputs(**overrides: object) -> ReportInputs:
    values: dict[str, object] = {
        "repo_root": REPO_ROOT,
        "index_root": INDEX_ROOT,
        "batch_dir": BATCH_DIR,
        "pilot_doc": PILOT_DOC,
        "db_report": DB_REPORT,
        "refine_json": REFINE_JSON,
    }
    values.update(overrides)
    return ReportInputs(**values)  # type: ignore[arg-type]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _manifest_path() -> Path:
    return next((INDEX_ROOT / "snapshots").glob("*/manifest.json"))


def test_committed_report_matches_real_inputs() -> None:
    problems = check_global_terrain_report(
        _inputs(),
        document_path=COMMITTED_DOC,
        artifacts_dir=COMMITTED_ARTIFACTS,
    )
    assert problems == []
    assert COMMITTED_DOC.is_file()
    for name in ARTIFACT_NAMES:
        assert (COMMITTED_ARTIFACTS / name).is_file()


def test_report_is_byte_deterministic(tmp_path: Path) -> None:
    first = build_global_terrain_report(_inputs())
    second = build_global_terrain_report(_inputs())
    assert first == second

    written = write_global_terrain_report(
        _inputs(),
        document_path=tmp_path / DOCUMENT_NAME,
        artifacts_dir=tmp_path / "artifacts",
    )
    assert {path.name for path in written} == {DOCUMENT_NAME, *ARTIFACT_NAMES}
    assert (tmp_path / DOCUMENT_NAME).read_text(encoding="utf-8") == COMMITTED_DOC.read_text(
        encoding="utf-8"
    )
    for name in ARTIFACT_NAMES:
        assert (tmp_path / "artifacts" / name).read_bytes() == (
            COMMITTED_ARTIFACTS / name
        ).read_bytes()


def test_counts_and_checksums_track_committed_manifest_and_batch() -> None:
    manifest = json.loads(_manifest_path().read_text(encoding="utf-8"))
    batch = json.loads((BATCH_DIR / "report.json").read_text(encoding="utf-8"))
    rendered = build_global_terrain_report(_inputs())
    coverage = json.loads(rendered["coverage.json"])
    performance = json.loads(rendered["performance.json"])
    refinement = json.loads(rendered["refinement.json"])

    assert coverage["counts"] == {
        "cells": 168,
        "regions": 3,
        "partitions": 3,
        "distinct_candidate_ids": 168,
        "batch_rows": 168,
    }
    assert coverage["counts"]["cells"] == manifest["row_count"]
    assert sum(partition["rows"] for partition in manifest["partitions"]) == 168
    assert sum(region["cells"] for region in coverage["per_region"]) == 168
    assert coverage["scope"]["global_claim"] is False
    assert coverage["scope"]["label"] == "local_coarse_index_not_global_exhaustive"

    assert performance["batch"]["source_run"] == "geomorpho-pilot-real-20261115"
    assert performance["batch"]["source_mode"] == "real"
    assert performance["batch"]["cell_counts"] == {
        "invalid": 0,
        "no_coverage": 0,
        "succeeded": 168,
    }
    assert performance["batch"]["region_rows"] == {
        "pilot-alps": 48,
        "pilot-grand-canyon": 56,
        "pilot-sahara": 64,
    }
    assert performance["batch"]["artifact_verification"]["verified_tasks"] == 11
    assert batch["outcomes"]["rows"] == 168

    assert refinement["available"] is True
    assert refinement["evidence"]["sha256"] == _sha256(REFINE_JSON)
    assert refinement["counts"] == {
        "coarse_indexed": 168,
        "coarse_eligible": 168,
        "coarse_selected": 3,
        "links": 3,
        "refined_succeeded": 3,
        "refined_failed": 0,
        "refined_no_coverage": 0,
        "fine_feature_observations": 15,
        "context_observations": 33,
        "failures": 0,
    }
    assert refinement["provider_counts"] == {
        "copernicus-dem-glo90": {"failed": 0, "no_coverage": 0, "unknown": 0, "verified": 3},
        "esa-worldcover-2021-v200": {
            "failed": 0,
            "no_coverage": 0,
            "unknown": 0,
            "verified": 3,
        },
    }
    assert [link["coarse_candidate_id"] for link in refinement["links"]] == [
        "coarse-v1:249d33516a874855",
        "coarse-v1:e0d4c209d928961c",
        "coarse-v1:95046a41aeb31ca9",
    ]
    for link in refinement["links"]:
        assert link["status"] == "succeeded"
        assert link["refined_checksum"].startswith("sha256:")
        assert link["fine_slope_mean_deg"] is not None
        assert link["fine_valid_fraction"] == 1.0
    assert refinement["run_metrics"] == {"elapsed_s": 26.708, "max_rss_mb": 201.2}

    assert coverage["checksums"]["manifest_sha256"] == _sha256(_manifest_path())
    manifest_partitions = {item["path"]: item for item in manifest["partitions"]}
    for partition in coverage["checksums"]["partitions"]:
        path = INDEX_ROOT / partition["path"]
        assert partition["sha256"] == _sha256(path)
        assert partition["rows"] == manifest_partitions[partition["path"]]["rows"]
    for item in coverage["checksums"]["batch_evidence"]:
        assert item["sha256"] == _sha256(REPO_ROOT / item["path"])

    checksums = json.loads(rendered["checksums.json"])
    assert {item["path"] for item in checksums["artifacts"]} == {
        name for name in ARTIFACT_NAMES if name != "checksums.json"
    }
    for item in checksums["artifacts"]:
        assert item["sha256"] == hashlib.sha256(rendered[item["path"]].encode()).hexdigest()


def test_report_refuses_to_label_pilot_as_global() -> None:
    assert_not_global_claim({"coverage": {"label": "local_coarse_index_not_global_exhaustive"}})
    with pytest.raises(GlobalClaimError):
        assert_not_global_claim({"coverage": {"label": "global_complete_exhaustive"}})
    with pytest.raises(GlobalClaimError):
        assert_not_global_claim({"coverage": {"label": "worldwide_coverage"}})
    with pytest.raises(GlobalReportError):
        assert_not_global_claim({"coverage": {"label": "pilot"}})


def test_builder_refuses_tampered_global_manifest(tmp_path: Path) -> None:
    copied = tmp_path / "index"
    shutil.copytree(INDEX_ROOT, copied)
    manifest_path = next((copied / "snapshots").glob("*/manifest.json"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["coverage"]["label"] = "global_exhaustive_complete"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(GlobalClaimError):
        build_global_terrain_report(_inputs(index_root=copied))


def test_builder_refuses_synthetic_batch_evidence(tmp_path: Path) -> None:
    copied = tmp_path / "batch"
    shutil.copytree(BATCH_DIR, copied)
    report_path = copied / "report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["source_mode"] = "synthetic-fixture"
    report_path.write_text(json.dumps(report), encoding="utf-8")

    with pytest.raises(GlobalReportError, match="source_mode=real"):
        build_global_terrain_report(_inputs(batch_dir=copied))


def test_builder_refuses_mismatched_refinement_evidence(tmp_path: Path) -> None:
    copied = tmp_path / "refinement.json"
    payload = json.loads(REFINE_JSON.read_text(encoding="utf-8"))
    payload["input_index"]["manifest_sha256"] = "0" * 64
    copied.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(GlobalReportError, match="manifest_sha256"):
        build_global_terrain_report(_inputs(refine_json=copied))


def test_document_keeps_coarse_and_fine_evidence_separate() -> None:
    rendered = build_global_terrain_report(_inputs())
    document = rendered[DOCUMENT_NAME]
    coverage = json.loads(rendered["coverage.json"])
    refinement = json.loads(rendered["refinement.json"])
    assert "global_claim = false" in document
    assert "not global coverage" in document
    assert "bounded evidence, not a sensitivity study" in document
    assert "What this does not establish" in document
    assert "`coarse_and_fine_scores_interchangeable=false`" in document
    assert "No recall, precision, overlap" in document
    assert refinement["comparison_note"].startswith("Coarse 5 km")
    assert coverage["coverage"]["method_version"] == "coarse-terrain-250m-1.0.0"
    assert "coarse-terrain-250m-1.0.0" in document
    assert "terrain-metrics-1.0.0" in document
    assert "not a 90 m terrain product" in coverage["coverage"]["source_resolution_note"]


def test_document_states_unavailable_when_no_refinement_is_configured() -> None:
    rendered = build_global_terrain_report(replace(_inputs(), refine_json=None))
    document = rendered[DOCUMENT_NAME]
    refinement = json.loads(rendered["refinement.json"])
    assert "evidence unavailable" in document
    assert "**No coarse-vs-refined comparison is available, so none is reported.**" in document
    assert refinement["available"] is False
    assert refinement["comparison"] is None


def test_check_detects_stale_committed_output(tmp_path: Path) -> None:
    document = tmp_path / DOCUMENT_NAME
    artifacts = tmp_path / "artifacts"
    write_global_terrain_report(_inputs(), document_path=document, artifacts_dir=artifacts)
    assert (
        check_global_terrain_report(_inputs(), document_path=document, artifacts_dir=artifacts)
        == []
    )
    document.write_text("stale\n", encoding="utf-8")
    problems = check_global_terrain_report(
        _inputs(), document_path=document, artifacts_dir=artifacts
    )
    assert problems == [f"committed file is stale: {document}"]
