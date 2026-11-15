from pathlib import Path

from analog_api.config import Settings
from analog_api.main import create_app
from fastapi.testclient import TestClient


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'typed-seed.db'}",
        manifest_dir=str(tmp_path / "none"),
        manifest_dir_planetary=str(tmp_path / "none"),
        manifest_dir_context=str(tmp_path / "none"),
        seed_include_synthetic=False,
        seed_include_typed=True,
    )


def test_seed_imports_committed_typed_releases_and_bundle(tmp_path: Path):
    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        coverage = client.get("/api/v1/typed/coverage").json()
        assert coverage["total"] == 199
        assert coverage["by_dimension"]["D02"]["total"] == 56
        assert coverage["by_dimension"]["D03"]["total"] == 20
        assert coverage["by_dimension"]["D04"]["total"] == 20
        assert coverage["by_dimension"]["D05"]["total"] == 28
        assert coverage["by_dimension"]["D05"]["by_body"] == {
            "Earth": 10,
            "Mars": 10,
            "Moon": 8,
        }
        assert coverage["by_dimension"]["D06"]["total"] == 75

        # The D02 bundle and the SPICE release both land in the same typed store.
        assert client.get("/api/v1/typed/areas/moon-shackleton-rim-site04-roi1").status_code == 200
        assert client.get("/api/v1/typed/areas/earth-atacama-desert").status_code == 200

        # Legacy v1 index stays empty for this manifest-free database.
        assert client.get("/api/v1/capabilities").json()["indexed_candidate_count"] == 0


def test_seed_typed_bundles_is_idempotent(tmp_path: Path):
    settings = _settings(tmp_path)
    first = create_app(settings)
    with TestClient(first) as client:
        before = client.get("/api/v1/typed/coverage").json()

    second = create_app(settings)
    with TestClient(second) as client:
        after = client.get("/api/v1/typed/coverage").json()

    assert after == before
    assert after["total"] == 199
