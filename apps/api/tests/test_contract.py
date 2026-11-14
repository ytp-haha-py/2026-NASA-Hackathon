from __future__ import annotations

import json
from pathlib import Path

from analog_api.config import Settings
from analog_api.main import create_app

ROOT = Path(__file__).resolve().parents[3]


def test_committed_openapi_snapshot_is_current(tmp_path: Path):
    settings = Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'openapi.db'}",
        seed_include_synthetic=True,
        manifest_dir=str(tmp_path / "none-earth"),
        manifest_dir_planetary=str(tmp_path / "none-planetary"),
    )
    generated = create_app(settings).openapi()
    committed = json.loads((ROOT / "contracts" / "openapi.json").read_text(encoding="utf-8"))
    assert generated == committed, (
        "contracts/openapi.json is stale. Run `uv run python scripts/export_openapi.py`."
    )


def test_contract_examples_include_required_endpoints():
    contract = json.loads((ROOT / "contracts" / "contract.json").read_text(encoding="utf-8"))
    assert contract["examples"]["search_request"]["body"] == "custom"
    assert set(contract["examples"]) >= {"capabilities", "search_request", "search_response"}
    assert "weighted normalized euclidean" in contract["distance"]["name"].lower()
