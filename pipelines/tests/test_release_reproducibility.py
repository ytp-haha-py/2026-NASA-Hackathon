from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from analog_api.adapters.db import Candidate, ContextObservation, Dataset, PlanetaryReference
from analog_api.adapters.seed import make_engine
from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT = ROOT / "samples" / "snapshot" / "analog-demo.snapshot.json"


def _rebuild(tmp_path: Path, name: str) -> tuple[int, int, int, int, str]:
    db_url = f"sqlite+pysqlite:///{tmp_path / name}"
    result = subprocess.run(
        [
            sys.executable,
            "pipelines/build_index.py",
            "--snapshot",
            str(SNAPSHOT),
            "--database-url",
            db_url,
            "--reset",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    engine = make_engine(db_url)
    with engine.connect() as connection:
        candidates = len(connection.execute(select(Candidate)).all())
        datasets = len(connection.execute(select(Dataset)).all())
        references = len(connection.execute(select(PlanetaryReference)).all())
        context = len(connection.execute(select(ContextObservation)).all())
    return candidates, datasets, references, context, result.stdout.strip()


def test_two_clean_rebuilds_are_identical(tmp_path: Path):
    first = _rebuild(tmp_path, "a.db")
    second = _rebuild(tmp_path, "b.db")
    assert first[:4] == second[:4]
    assert first[0] == 113
    assert first[1] == 9
    assert first[2] == 25
    assert first[3] == 1243
    assert "verified snapshot=" in first[4]
    assert "verified snapshot=" in second[4]
