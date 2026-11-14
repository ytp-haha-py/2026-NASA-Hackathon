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
    # ignore_cleanup_errors: on Windows SQLite may still hold openapi.db when the directory is
    # removed (WinError 32). The schema is already built by then, so cleanup is best-effort.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        settings = Settings(
            database_url=f"sqlite+pysqlite:///{Path(tmp) / 'openapi.db'}",
            seed_include_synthetic=True,
            auto_seed=True,
        )
        app = create_app(settings)
        schema = app.openapi()
    # newline="\n" keeps LF on every platform (write_text would emit CRLF on Windows).
    with OUT.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(schema, indent=2, sort_keys=True) + "\n")
    print(f"Wrote {OUT.relative_to(ROOT)} ({len(schema.get('paths', {}))} paths)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
