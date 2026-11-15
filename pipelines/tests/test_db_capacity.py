"""Issue #70 safety tests: backup/restore/rebuild, schema gate, checksums, determinism.

All drills run on temporary copies. The real ``var/analog.db`` is never reset or
modified; the optional runtime-database test only copies it.
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path
from types import ModuleType

import pytest
from analog_api.adapters.db import (
    AnalysisJob,
    Candidate,
    ContextObservation,
    FeatureObservation,
)
from analog_api.adapters.schema import (
    SCHEMA_VERSION,
    SchemaVersionError,
    ensure_schema_compatible,
)
from analog_api.adapters.seed import make_engine, seed_manifests
from analog_api.adapters.typed_db import TypedObservationStore
from analog_pipelines.coarse_features import COARSE_METHOD_VERSION, CoarseExtractionError
from analog_pipelines.coarse_index import (
    CoarseIndexRepository,
    CoarseRow,
    IndexError_,
    build_index,
)
from analog_pipelines.typed_contracts import (
    AreaV2,
    ProcessingRunV2,
    SourceAssetV2,
    TypedObservation,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

REPO_ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT = REPO_ROOT / "samples" / "snapshot" / "analog-demo.snapshot.json"
BUILD_INDEX_PATH = REPO_ROOT / "pipelines" / "build_index.py"
BACKUP_SCRIPT = REPO_ROOT / "scripts" / "db_backup.py"
AUDIT_SCRIPT = REPO_ROOT / "scripts" / "db_audit.py"
COARSE_BENCHMARK = REPO_ROOT / "scripts" / "benchmark_coarse_index.py"
TYPED_BENCHMARK = REPO_ROOT / "scripts" / "benchmark_typed_observations.py"
RUNTIME_DATABASE = REPO_ROOT / "var" / "analog.db"


def _load_script(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def scripts() -> dict[str, ModuleType]:
    return {
        "build_index": _load_script("analog_build_index_capacity", BUILD_INDEX_PATH),
        "backup": _load_script("analog_db_backup", BACKUP_SCRIPT),
        "audit": _load_script("analog_db_audit", AUDIT_SCRIPT),
        "coarse_benchmark": _load_script("analog_coarse_benchmark", COARSE_BENCHMARK),
        "typed_benchmark": _load_script("analog_typed_benchmark", TYPED_BENCHMARK),
    }


def _build_source_database(
    path: Path, build_index_module: ModuleType, *, with_runtime: bool = True
) -> Path:
    result = build_index_module.build_index(
        SNAPSHOT, f"sqlite+pysqlite:///{path}", reset=True, root=REPO_ROOT
    )
    assert result["counts"]["candidates"] > 0
    if not with_runtime:
        return path
    payload = json.loads(
        (REPO_ROOT / "samples/fixtures/dimensions/d02_mineral_detection.json").read_text(
            encoding="utf-8"
        )
    )[0]
    observation = TypedObservation.model_validate(payload)
    engine = make_engine(f"sqlite+pysqlite:///{path}")
    with Session(engine) as session:
        dataset_id = session.scalar(select(Candidate.dataset_id))
        assert dataset_id is not None
        aoi = Candidate(
            location_id="earth-runtime-drill",
            display_name="Runtime drill AOI",
            lat=10.0,
            lon=20.0,
            west=19.99,
            south=9.99,
            east=20.01,
            north=10.01,
            dataset_id=dataset_id,
            window_size_m=1980,
            analysis_resolution_m=90,
            crs="EPSG:32634",
            valid_fraction=1.0,
            quality_flags=["on_demand_aoi"],
            notes="runtime drill row",
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
            ContextObservation(
                candidate_id=aoi.location_id,
                dataset_id=dataset_id,
                feature_id="landcover_water_fraction",
                value=0.1,
                unit="fraction",
                method_version="worldcover-2021-v200-1.0.0",
                source_resolution_m=10,
                valid_fraction=1.0,
                quality="verified",
                class_code=80,
                provider="fixture",
            )
        )
        session.add(
            AnalysisJob(
                job_id="job-runtime-drill",
                status="completed",
                cache_key="cache-runtime-drill",
                idempotency_key="idem-runtime-drill",
                area_id=aoi.location_id,
                request_json={"body": "Earth"},
                provider_status={"fixture": {"status": "verified"}},
            )
        )
        store = TypedObservationStore(session)
        store.upsert_asset(
            SourceAssetV2(
                asset_id="asset-runtime-drill",
                dataset_id=dataset_id,
                uri="local://runtime-drill",
                checksum_algorithm="sha256",
                checksum="sha256:" + "b" * 64,
                checksum_scope="derived",
                native_crs="EPSG:4326",
            )
        )
        store.upsert_area(
            AreaV2(
                area_id=observation.area_id,
                body="Earth",
                bbox={"west": 10, "south": 45, "east": 10.01, "north": 45.01},
                crs="EPSG:4326",
                coordinate_frame="earth_wgs84",
            )
        )
        store.upsert_observation(observation)
        store.upsert_run(
            ProcessingRunV2(
                run_id="run-runtime-drill",
                started_at="2026-01-01T00:00:00Z",
                finished_at="2026-01-01T00:00:01Z",
                input_asset_ids=["asset-runtime-drill"],
                input_checksums={},
                window_selection_rule="drill fixture",
                method_versions={"synthetic-typed-benchmark-1.0.0": "fixture"},
                output_checksum="sha256:" + "c" * 64,
                counts={"observations": 1},
                status="completed",
                command="test_db_capacity.py",
            )
        )
        session.commit()
    engine.dispose()
    return path


def _logical_dump(path: Path) -> dict[str, object]:
    with sqlite3.connect(path) as connection:
        meta = dict(connection.execute("SELECT key, value FROM meta").fetchall())
        tables = {
            "candidates": (
                "SELECT location_id, display_name, lat, lon, west, south, east, north, "
                "dataset_id, window_size_m, analysis_resolution_m, crs, valid_fraction, "
                "quality_flags, notes, data_kind, area_km2, geometry, origin "
                "FROM candidates ORDER BY location_id"
            ),
            "datasets": "SELECT * FROM datasets ORDER BY dataset_id",
            "feature_observations": (
                "SELECT * FROM feature_observations ORDER BY candidate_id, feature_id"
            ),
            "context_observations": (
                "SELECT * FROM context_observations ORDER BY candidate_id, feature_id"
            ),
            "planetary_references": ("SELECT * FROM planetary_references ORDER BY reference_id"),
            "reference_feature_observations": (
                "SELECT * FROM reference_feature_observations ORDER BY reference_id, feature_id"
            ),
        }
        dumps = {name: connection.execute(sql).fetchall() for name, sql in tables.items()}
    return {
        "data_version": meta.get("data_version"),
        "schema_version": meta.get("schema_version"),
        "tables": dumps,
    }


def _set_meta(path: Path, key: str, value: str) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value))
        connection.commit()


def test_backup_restore_rebuild_preserves_runtime_state(tmp_path: Path, scripts) -> None:
    backup_script = scripts["backup"]
    source = _build_source_database(tmp_path / "runtime.db", scripts["build_index"])
    before = backup_script.database_fingerprint(source)
    assert before["integrity_check"] == "ok"
    assert before["schema_version"] == str(SCHEMA_VERSION)
    assert before["counts"]["on_demand_candidates"] == 1
    assert before["counts"]["typed_observations"] >= 1
    assert before["counts"]["analysis_jobs"] == 1
    source_bytes = source.read_bytes()

    backup_path = tmp_path / "backup.db"
    metadata = backup_script.backup_database(source, backup_path)
    assert backup_path.is_file()
    assert metadata["schema_version"] == str(SCHEMA_VERSION)
    assert metadata["sha256"] == backup_script.sha256_file(backup_path)

    restored = tmp_path / "restored.db"
    restore_result = backup_script.restore_database(backup_path, restored)
    assert restore_result["sha256"] == metadata["sha256"]
    assert backup_script.database_fingerprint(restored)["counts"] == before["counts"]

    rebuild = backup_script.rebuild_from_snapshot(restored, snapshot=SNAPSHOT)
    assert rebuild["data_version"] == before["data_version"]
    after = backup_script.database_fingerprint(restored)
    for key in (
        "on_demand_candidates",
        "on_demand_features",
        "on_demand_context",
        "analysis_jobs",
        "typed_areas",
        "typed_source_assets",
        "typed_observations",
        "typed_processing_runs",
    ):
        assert after["counts"][key] == before["counts"][key], key
    assert after["counts"]["candidates"] > 0
    assert after["counts"]["datasets"] > 0
    assert source.read_bytes() == source_bytes, "the drill must never modify the source database"

    drill = backup_script.run_drill(source, snapshot=SNAPSHOT)
    assert drill["label"] == "copy_only_drill_source_never_modified"
    assert (
        drill["after_rebuild"]["counts"]["typed_observations"]
        == before["counts"]["typed_observations"]
    )


def test_runtime_database_copy_drill_is_opt_in(scripts) -> None:
    if not RUNTIME_DATABASE.is_file():
        pytest.skip("no local var/analog.db runtime database to drill")
    backup_script = scripts["backup"]
    before = backup_script.database_fingerprint(RUNTIME_DATABASE)
    drill = backup_script.run_drill(RUNTIME_DATABASE, snapshot=SNAPSHOT)
    assert drill["label"] == "copy_only_drill_source_never_modified"
    after = drill["after_rebuild"]
    for key in (
        "on_demand_candidates",
        "on_demand_features",
        "on_demand_context",
        "analysis_jobs",
        "typed_areas",
        "typed_source_assets",
        "typed_observations",
        "typed_processing_runs",
    ):
        assert after["counts"][key] == before["counts"][key], key
    # The source database is opened read-only by the backup API and never reset.
    assert backup_script.database_fingerprint(RUNTIME_DATABASE)["counts"] == before["counts"]


def test_schema_version_gate_rejects_newer_or_malformed_database(tmp_path: Path, scripts) -> None:
    from analog_api.config import Settings
    from analog_api.main import create_app

    newer = _build_source_database(tmp_path / "newer.db", scripts["build_index"])
    _set_meta(newer, "schema_version", str(SCHEMA_VERSION + 1))
    engine = make_engine(f"sqlite+pysqlite:///{newer}")
    with pytest.raises(SchemaVersionError, match="newer than this build supports"):
        ensure_schema_compatible(engine)
    with pytest.raises(SchemaVersionError):
        seed_manifests(engine, [], reset=True)
    with pytest.raises(SchemaVersionError):
        create_app(
            Settings(
                database_url=f"sqlite+pysqlite:///{newer}",
                seed_include_synthetic=False,
                manifest_dir=str(tmp_path / "none"),
                manifest_dir_planetary=str(tmp_path / "none"),
                manifest_dir_context=str(tmp_path / "none"),
            )
        )

    malformed = _build_source_database(tmp_path / "malformed.db", scripts["build_index"])
    _set_meta(malformed, "schema_version", "not-an-integer")
    malformed_engine = make_engine(f"sqlite+pysqlite:///{malformed}")
    with pytest.raises(SchemaVersionError, match="invalid schema_version"):
        ensure_schema_compatible(malformed_engine)


def test_corrupted_backup_checksum_is_rejected(tmp_path: Path, scripts) -> None:
    backup_script = scripts["backup"]
    source = _build_source_database(tmp_path / "source.db", scripts["build_index"])
    backup_path = tmp_path / "backup.db"
    metadata = backup_script.backup_database(source, backup_path)
    damaged = tmp_path / "damaged.db"
    data = bytearray(backup_path.read_bytes())
    data[-1] ^= 0xFF
    damaged.write_bytes(bytes(data))
    destination = tmp_path / "must-not-exist.db"
    with pytest.raises(backup_script.BackupError, match="checksum mismatch"):
        backup_script.restore_database(damaged, destination, expected_sha256=metadata["sha256"])
    assert not destination.exists()


def test_corrupted_parquet_partition_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "index"
    rows = [_coarse_row("coarse-checksum:a"), _coarse_row("coarse-checksum:b")]
    build_index(rows, root=root, snapshot_id="checksum-drill")
    repository = CoarseIndexRepository(root, verify=True)
    assert repository.manifest["row_count"] == 2
    partition = sorted(root.rglob("*.parquet"))[0]
    partition.write_bytes(partition.read_bytes() + b"corruption")
    with pytest.raises(IndexError_, match="checksum mismatch"):
        CoarseIndexRepository(root, verify=True)


def test_two_independent_sqlite_builds_are_deterministic(tmp_path: Path, scripts) -> None:
    first = _build_source_database(
        tmp_path / "first.db", scripts["build_index"], with_runtime=False
    )
    second = _build_source_database(
        tmp_path / "second.db", scripts["build_index"], with_runtime=False
    )
    assert _logical_dump(first) == _logical_dump(second)
    with sqlite3.connect(first) as connection:
        first_counts = {
            table: connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in (
                "candidates",
                "feature_observations",
                "context_observations",
                "planetary_references",
                "reference_feature_observations",
            )
        }
    with sqlite3.connect(second) as connection:
        second_counts = {
            table: connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in first_counts
        }
    assert first_counts == second_counts


def test_two_independent_coarse_builds_have_identical_content_addressed_partitions(
    tmp_path: Path,
) -> None:
    rows = [_coarse_row(f"coarse-deterministic:{index}") for index in range(120)]
    roots = [tmp_path / "a", tmp_path / "b"]
    manifests = [build_index(rows, root=root, snapshot_id="deterministic") for root in roots]
    digests = [sorted(item["sha256"] for item in manifest["partitions"]) for manifest in manifests]
    assert digests[0] == digests[1]
    assert [item["row_count"] for item in manifests] == [120, 120]
    file_hashes = [sorted(path.name for path in root.rglob("*.parquet")) for root in roots]
    assert file_hashes[0] == file_hashes[1]


def test_pagination_offset_is_deterministic(tmp_path: Path) -> None:
    rows = [_coarse_row(f"coarse-page:{index:03d}", index) for index in range(20)]
    root = tmp_path / "index"
    build_index(rows, root=root, snapshot_id="pagination")
    repository = CoarseIndexRepository(root, verify=True)
    target = {"coarse_slope_mean_deg": 10.0}
    weights = {"coarse_slope_mean_deg": 1.0}
    first = repository.search(target=target, weights=weights, limit=5, offset=0)
    second = repository.search(target=target, weights=weights, limit=5, offset=5)
    assert [item["candidate_id"] for item in first["results"]] == [
        "coarse-page:000",
        "coarse-page:001",
        "coarse-page:002",
        "coarse-page:003",
        "coarse-page:004",
    ]
    assert [item["candidate_id"] for item in second["results"]] == [
        "coarse-page:005",
        "coarse-page:006",
        "coarse-page:007",
        "coarse-page:008",
        "coarse-page:009",
    ]
    with pytest.raises(CoarseExtractionError, match="offset"):
        repository.search(target=target, weights=weights, limit=5, offset=-1)


def test_audit_separates_storage_layers(tmp_path: Path, scripts, monkeypatch) -> None:
    audit = scripts["audit"]
    database = _build_source_database(tmp_path / "audit.db", scripts["build_index"])
    index_root = tmp_path / "global-index"
    build_index([_coarse_row("coarse-audit:a")], root=index_root, snapshot_id="audit-snapshot")
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "source.tif").write_bytes(b"x" * 4096)
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / "provider.bin").write_bytes(b"y" * 1024)
    for name in (
        "ANALOG_OBJECT_STORE_BACKEND",
        "ANALOG_OBJECT_STORE_ENDPOINT",
        "ANALOG_OBJECT_STORE_BUCKET",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
    ):
        monkeypatch.delenv(name, raising=False)

    payload = audit.build_audit(
        database=database,
        index_dir=index_root,
        raw_dir=raw_dir,
        cache_dir=cache_dir,
        env={},
    )
    assert payload["sqlite"]["present"] is True
    assert payload["sqlite"]["file_bytes"] > 0
    assert payload["sqlite"]["page_count_bytes"] > 0
    assert payload["sqlite"]["integrity_check"] == "ok"
    assert payload["sqlite"]["meta"]["schema_version"] == str(SCHEMA_VERSION)
    table_names = {table["name"] for table in payload["sqlite"]["tables"]}
    assert {"candidates", "feature_observations", "typed_observations"} <= table_names
    index_names = {index["name"] for index in payload["sqlite"]["indexes"]}
    assert any("feature_id" in name for name in index_names)
    assert payload["parquet"]["file_count"] >= 1
    assert payload["parquet"]["bytes"] > 0
    assert payload["raw_cache"]["raw"]["bytes"] == 4096
    assert payload["raw_cache"]["cache"]["bytes"] == 1024
    assert payload["r2"]["status"] == "not_configured"
    assert payload["r2"]["bytes"] is None
    verified_payload = audit.build_audit(
        database=database,
        index_dir=index_root,
        raw_dir=raw_dir,
        cache_dir=cache_dir,
        env={},
        verify_indexes=True,
    )
    assert verified_payload["parquet"]["verification"]["ok"] is True
    totals = payload["totals"]
    assert totals["database_bytes"] == (
        totals["sqlite_bytes"] + totals["sqlite_sidecar_bytes"] + totals["parquet_bytes"]
    )
    assert totals["raw_bytes"] == 4096
    assert totals["database_bytes"] != totals["raw_bytes"]
    json.dumps(payload, sort_keys=True)
    assert "Raw rasters" in payload["scope_note"]


def test_audit_reports_missing_r2_credentials(tmp_path: Path, scripts) -> None:
    audit = scripts["audit"]
    report = audit.build_audit(
        database=tmp_path / "missing.db",
        index_dir=tmp_path / "missing-index",
        raw_dir=tmp_path / "missing-raw",
        cache_dir=tmp_path / "missing-cache",
        env={
            "ANALOG_OBJECT_STORE_BACKEND": "r2",
            "ANALOG_OBJECT_STORE_ENDPOINT": "https://example.invalid",
            "ANALOG_OBJECT_STORE_BUCKET": "bucket",
        },
    )
    assert report["sqlite"]["present"] is False
    assert report["r2"]["status"] == "unavailable"
    assert "credentials" in report["r2"]["reason"]
    assert report["r2"]["bytes"] is None


def test_synthetic_benchmark_scripts_are_labelled(scripts) -> None:
    coarse = scripts["coarse_benchmark"].run_benchmark(rows=48, iterations=3, rows_per_partition=8)
    assert coarse["label"] == "synthetic_benchmark_not_real_data"
    assert coarse["rows"] == 48
    assert coarse["build"]["partition_count"] >= 1
    assert coarse["build"]["bytes_per_row"] > 0
    for name in ("top_k", "region_filter", "pagination_offset"):
        assert {"p50_ms", "p95_ms", "p99_ms"} <= set(coarse["queries"][name])
    assert "not real global" in coarse["note"]

    typed = scripts["typed_benchmark"].run_benchmark(rows=60, iterations=3)
    assert typed["label"] == "synthetic_typed_benchmark_not_observation"
    assert typed["decision"] in {
        "defer_extra_indexes",
        "candidate_indexes_measurable_but_real_volume_decides",
    }
    assert "No dense six-dimension null rows" in typed["sparse_storage"]
    json.dumps(coarse, sort_keys=True)
    json.dumps(typed, sort_keys=True)


def _coarse_row(candidate_id: str, index: int = 0) -> CoarseRow:
    return CoarseRow(
        candidate_id=candidate_id,
        grid_id="coarse-grid-1.0.0",
        region_id="pilot",
        body="Earth",
        west=10.0 + index * 0.001,
        south=45.0,
        east=10.06 + index * 0.001,
        north=45.045,
        crs="EPSG:32633",
        area_km2=25.0,
        slope_mean_deg=10.0 + index,
        slope_p90_deg=20.0 + index,
        flat_fraction=0.5,
        valid_fraction=1.0,
        method_version=COARSE_METHOD_VERSION,
        grid_version="coarse-grid-1.0.0",
        dataset_id="synthetic-test-fixture",
        source_asset_id="synthetic-test-fixture",
        qa_flags=["synthetic_fixture"],
    )
