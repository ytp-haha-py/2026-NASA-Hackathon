from __future__ import annotations

import json
from pathlib import Path

import pytest
from analog_api.adapters.db import Candidate, Dataset, FeatureObservation, Meta
from analog_api.adapters.seed import (
    compute_data_version,
    discover_manifests,
    make_engine,
    seed_manifests,
)
from analog_api.data.synthetic import synthetic_manifest
from analog_api.domain.manifest import Manifest
from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[3]


def test_synthetic_manifest_validates_and_is_explicitly_synthetic():
    manifest = Manifest.model_validate(synthetic_manifest())
    assert manifest.data_kind == "synthetic"
    assert len(manifest.candidates) == 4
    for candidate in manifest.candidates:
        assert candidate.features["slope_mean_deg"].quality == "synthetic"
        assert "SYNTHETIC" in candidate.display_name


def test_manifest_rejects_incompatible_method_version():
    payload = synthetic_manifest()
    payload["candidates"][0]["features"]["slope_mean_deg"]["method_version"] = "terrain-v0"
    with pytest.raises(ValueError, match="incompatible method_version"):
        Manifest.model_validate(payload)


def test_manifest_rejects_unknown_feature():
    payload = synthetic_manifest()
    payload["candidates"][0]["features"]["gravity_m_s2"] = {
        "value": 3.7,
        "unit": "m/s^2",
        "method_version": "terrain-metrics-1.0.0",
        "extraction_scale_m": 90,
        "valid_fraction": 1.0,
        "quality": "synthetic",
    }
    with pytest.raises(ValueError, match="unsupported feature"):
        Manifest.model_validate(payload)


def test_manifest_rejects_unit_mismatch():
    payload = synthetic_manifest()
    payload["candidates"][0]["features"]["slope_mean_deg"]["unit"] = "radian"
    with pytest.raises(ValueError, match="unit mismatch"):
        Manifest.model_validate(payload)


def test_manifest_rejects_unknown_dataset_reference():
    payload = synthetic_manifest()
    payload["candidates"][0]["dataset_id"] = "missing-dataset"
    with pytest.raises(ValueError, match="unknown dataset"):
        Manifest.model_validate(payload)


def test_seed_persists_provenance_features_and_data_version(tmp_path: Path):
    manifest = Manifest.model_validate(synthetic_manifest())
    engine = make_engine(f"sqlite+pysqlite:///{tmp_path / 'seed.db'}")
    version = seed_manifests(engine, [manifest], reset=True)

    assert version.startswith("dv-")
    assert version == compute_data_version([manifest])

    with engine.connect() as connection:
        candidates = connection.execute(select(Candidate)).mappings().all()
        datasets = connection.execute(select(Dataset)).mappings().all()
        observations = connection.execute(select(FeatureObservation)).mappings().all()
        meta = {row.key: row.value for row in connection.execute(select(Meta)).mappings().all()}
    assert len(candidates) == 4
    assert len(datasets) == 1
    assert datasets[0]["license"] == "CC0-1.0"
    assert datasets[0]["source_kind"] == "synthetic"
    assert len(observations) == 16
    assert candidates[0]["data_kind"] == "synthetic"
    assert meta["data_version"] == version


def test_seed_is_idempotent_without_reset(tmp_path: Path):
    manifest = Manifest.model_validate(synthetic_manifest())
    engine = make_engine(f"sqlite+pysqlite:///{tmp_path / 'idempotent.db'}")
    seed_manifests(engine, [manifest], reset=True)
    seed_manifests(engine, [manifest], reset=False)
    with engine.connect() as connection:
        count = connection.execute(select(Candidate)).mappings().all()
        observations = connection.execute(select(FeatureObservation)).mappings().all()
    assert len(count) == 4
    assert len(observations) == 16


def test_data_version_changes_when_measurement_changes():
    first = Manifest.model_validate(synthetic_manifest())
    payload = synthetic_manifest()
    payload["candidates"][0]["features"]["slope_mean_deg"]["value"] += 0.001
    second = Manifest.model_validate(payload)
    assert compute_data_version([first]) != compute_data_version([second])


def test_empty_manifests_have_explicit_data_version():
    assert compute_data_version([]) == "dv-empty"


def test_discover_manifests_reads_directory(tmp_path: Path, monkeypatch):
    from analog_api.config import Settings

    manifest_dir = tmp_path / "manifests"
    manifest_dir.mkdir()
    (manifest_dir / "fixture.json").write_text(json.dumps(synthetic_manifest()), encoding="utf-8")
    settings = Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'x.db'}",
        manifest_dir=str(manifest_dir),
        manifest_dir_planetary=str(tmp_path / "none"),
        manifest_dir_context=str(tmp_path / "none-context"),
        seed_include_synthetic=False,
    )
    manifests = discover_manifests(settings)
    assert len(manifests) == 1
    assert manifests[0].data_kind == "synthetic"


def test_contract_file_matches_registry_and_analysis():
    from analog_api.domain.features import (
        ANALYSIS_PAYLOAD,
        AOI_LIMITS,
        CONTRACT_VERSION,
        context_feature_payloads,
        feature_payloads,
    )

    contract = json.loads((ROOT / "contracts" / "contract.json").read_text(encoding="utf-8"))
    assert contract["contract_version"] == CONTRACT_VERSION
    assert contract["features"] == feature_payloads()
    assert contract["context_features"] == context_feature_payloads()
    assert contract["aoi_analysis"] == AOI_LIMITS
    for key, value in ANALYSIS_PAYLOAD.items():
        assert contract["analysis"][key] == value


def test_reseed_refreshes_imported_values_and_preserves_unrelated_aoi(tmp_path: Path):
    from sqlalchemy.orm import Session

    payload = synthetic_manifest()
    engine = make_engine(f"sqlite+pysqlite:///{tmp_path / 'refresh.db'}")
    original = Manifest.model_validate(payload)
    seed_manifests(engine, [original], reset=True)
    untouched_id = original.candidates[-1].location_id
    with Session(engine) as session:
        session.get(Candidate, untouched_id).origin = "on_demand"
        session.commit()
    payload["candidates"] = payload["candidates"][:1]
    payload["candidates"][0]["features"]["slope_mean_deg"]["value"] = 8.25
    payload["datasets"][0]["notes"] = "TEST ONLY updated provenance"
    revised = Manifest.model_validate(payload)
    seed_manifests(engine, [revised], reset=False)
    with Session(engine) as session:
        row = session.get(Candidate, revised.candidates[0].location_id)
        assert next(f.value for f in row.features if f.feature_id == "slope_mean_deg") == 8.25
        assert (
            session.get(Dataset, revised.datasets[0].dataset_id).notes
            == "TEST ONLY updated provenance"
        )
        assert session.get(Candidate, untouched_id).origin == "on_demand"
        assert len(session.get(Candidate, untouched_id).features) == 4
