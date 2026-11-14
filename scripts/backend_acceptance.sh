#!/usr/bin/env bash
# Offline backend acceptance for the six-dimension / global-discovery foundation.
# Never downloads scientific data and never calls R2.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

printf '[backend-acceptance] focused offline suites\n'
uv run pytest \
  apps/api/tests \
  pipelines/tests \
  pipelines/earth/tests \
  pipelines/worldcover/tests \
  pipelines/planetary/tests -q

printf '\n[backend-acceptance] legacy API smoke\n'
bash scripts/smoke.sh

printf '\n[backend-acceptance] pinned data validation\n'
bash scripts/validate_data.sh

printf '\n[backend-acceptance] RESULT: PASS (offline; no source download, no R2)\n'
