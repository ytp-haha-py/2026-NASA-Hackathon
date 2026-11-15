import json
from collections import Counter, defaultdict
from pathlib import Path

from analog_api.config import Settings
from analog_api.main import create_app
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[3]


def _committed_typed_counts() -> tuple[int, dict[str, dict[str, int]]]:
    """Derive expected counts from the committed releases/bundle themselves.

    This keeps the seed regression test honest as new verified pilots are added:
    it fails if seeding misses a committed observation, not merely because the
    total changed.
    """
    unique: dict[str, dict[str, object]] = {}
    files = sorted(
        path
        for path in (REPO_ROOT / "pipelines/pilots").glob("*/observations.jsonl")
        if (path.parent / "plan.json").is_file()
    )
    files.append(REPO_ROOT / "pipelines/geology/typed/observations.jsonl")
    for path in files:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            observation = json.loads(line)
            # Later releases may supersede an earlier row with the same
            # deterministic observation_id (for example the 0-5 cm SoilGrids
            # pilot replaced by the multi-depth pilot).
            unique[observation["observation_id"]] = observation
    totals: Counter[str] = Counter()
    by_body: dict[str, Counter[str]] = defaultdict(Counter)
    for observation in unique.values():
        dimension = str(observation["dimension_code"])
        totals[dimension] += 1
        by_body[dimension][str(observation["body"])] += 1
    return len(unique), {key: dict(value) for key, value in by_body.items()}


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
    expected_total, expected_by_body = _committed_typed_counts()
    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        coverage = client.get("/api/v1/typed/coverage").json()
        assert coverage["total"] == expected_total
        for dimension, body_counts in expected_by_body.items():
            assert coverage["by_dimension"][dimension]["total"] == sum(body_counts.values())
            assert coverage["by_dimension"][dimension]["by_body"] == body_counts

        # The D02 bundle and the SPICE release both land in the same typed store.
        assert client.get("/api/v1/typed/areas/moon-shackleton-rim-site04-roi1").status_code == 200
        assert client.get("/api/v1/typed/areas/earth-atacama-desert").status_code == 200

        # Legacy v1 index stays empty for this manifest-free database.
        assert client.get("/api/v1/capabilities").json()["indexed_candidate_count"] == 0


def test_seed_typed_bundles_is_idempotent(tmp_path: Path):
    expected_total, _ = _committed_typed_counts()
    settings = _settings(tmp_path)
    first = create_app(settings)
    with TestClient(first) as client:
        before = client.get("/api/v1/typed/coverage").json()

    second = create_app(settings)
    with TestClient(second) as client:
        after = client.get("/api/v1/typed/coverage").json()

    assert after == before
    assert after["total"] == expected_total
