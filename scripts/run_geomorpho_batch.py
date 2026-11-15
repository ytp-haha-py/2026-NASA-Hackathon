#!/usr/bin/env python
"""Run the resumable Geomorpho250 pilot batch (Issue #32).

Thin wrapper around ``analog_pipelines.geomorpho_batch``; the command reads only
locally staged windows and never downloads the global PANGAEA raster. See
``docs/GEOMORPHO_BATCH_PILOT.md`` for the operating guide and verified results.

Examples::

    uv run python scripts/run_geomorpho_batch.py --run-id pilot-dry --dry-run
    uv run python scripts/run_geomorpho_batch.py --run-id pilot-2026 --workers 2
    uv run python scripts/run_geomorpho_batch.py --run-id pilot-2026 --resume
    uv run python scripts/run_geomorpho_batch.py --run-id pilot-2026 --verify-only
"""

from __future__ import annotations

from analog_pipelines.geomorpho_batch import main

if __name__ == "__main__":
    raise SystemExit(main())
