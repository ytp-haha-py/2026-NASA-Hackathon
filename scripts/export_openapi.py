#!/usr/bin/env python
"""Write the deterministic OpenAPI snapshot used for frontend type generation and drift checks."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from analog_api.config import Settings
from analog_api.main import create_app

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "contracts" / "openapi.json"


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        settings = Settings(
            database_url=f"sqlite+pysqlite:///{Path(tmp) / 'openapi.db'}",
            seed_include_synthetic=True,
            auto_seed=True,
        )
        app = create_app(settings)
        schema = app.openapi()
    OUT.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Wrote {OUT.relative_to(ROOT)} ({len(schema.get('paths', {}))} paths)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
