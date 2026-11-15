"""Build, verify, or report the bounded multi-dimension release (Issue #48).

Usage::

    uv run python pipelines/build_release.py build
    uv run python pipelines/build_release.py verify
    uv run python pipelines/build_release.py report
"""

from __future__ import annotations

from analog_pipelines.release import main

if __name__ == "__main__":
    raise SystemExit(main())
