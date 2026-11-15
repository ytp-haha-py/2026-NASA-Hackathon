#!/usr/bin/env bash
# Offline deterministic rebuild of the Earth POWER 113 release.
# Re-processes committed raw/ responses with the shared provider code and then
# verifies checksums, counts and invariants. No network access.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/../../.."

uv run python pipelines/d02_d04_aoi/earth_power_113/build.py rebuild
uv run python pipelines/d02_d04_aoi/earth_power_113/build.py check
