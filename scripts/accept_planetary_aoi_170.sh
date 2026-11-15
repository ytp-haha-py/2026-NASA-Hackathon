#!/usr/bin/env bash
# Goal H (#170) offline acceptance: Moon+Mars real committed AOIs, negative cases,
# API/OpenAPI/type compatibility checks, cache/resume and CI-relevant budgets.
#
# Offline only: isolated temporary SQLite DB, committed manifests/fixtures, no network,
# no shared ./var/analog.db, no uvicorn/server. It does not claim global/exhaustive
# six-dimension coverage.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

usage() {
  cat <<'EOF'
Usage: bash scripts/accept_planetary_aoi_170.sh [--offline|--help]

  --offline  (default) run Goal H offline E2E/contract checks in an isolated env
  --help     show this help

Checks:
  - apps/api/tests/test_planetary_aoi_e2e_170.py (Moon 3 + Mars 3 real committed AOIs,
    nodata/out-of-coverage, polar/dateline handling, oversized, source outage,
    cache/resume/idempotency, API/OpenAPI/TS type-surface compatibility)
  - contracts/openapi.json and apps/web/src/api/schema.d.ts contain planetary AOI surfaces
  - scripts/export_openapi.py --check (schema export drift guard, no files written)
  - basic resource budget: temp DB/runtime size reported (bounded, non-zero evidence only)

Environment:
  ACCEPT_TIMEOUT_S  per-command timeout seconds (default: 240)
EOF
}

MODE="offline"
case "${1:-}" in
  ""|--offline) MODE="offline" ;;
  --help|-h) usage; exit 0 ;;
  *) echo "[goal170] unknown argument: $1" >&2; usage >&2; exit 2 ;;
esac

TIMEOUT_S="${ACCEPT_TIMEOUT_S:-240}"
TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/analogx-goal170.XXXXXX")"
cleanup() { rm -rf "$TMP_DIR"; }
trap cleanup EXIT

require_file() {
  local path="$1"
  [[ -f "$path" ]] || { echo "[goal170] FAIL: required file missing: $path" >&2; exit 1; }
}

run_timed() {
  local label="$1"; shift
  echo "[goal170] >>> $label"
  if command -v timeout >/dev/null 2>&1; then
    timeout "${TIMEOUT_S}s" "$@"
  else
    "$@"
  fi
}

if [[ "$MODE" == "offline" ]]; then
  require_file apps/api/tests/test_planetary_aoi_e2e_170.py
  require_file docs/FRONTEND_HANDOFF_170.md
  require_file contracts/openapi.json
  require_file apps/web/src/api/schema.d.ts

  export ANALOG_DATABASE_URL="sqlite+pysqlite:///${TMP_DIR}/goal170.db"
  export ANALOG_AOI_RUNTIME_DIR="${TMP_DIR}/aoi-runtime"
  export ANALOG_SEED_INCLUDE_SYNTHETIC="false"
  export ANALOG_SEED_INCLUDE_TYPED="true"
  export ANALOG_AOI_INLINE="true"
  export ANALOG_PLANETARY_ALLOW_NETWORK="false"

  run_timed "pytest planetary AOI E2E (Goal H)" \
    uv run pytest apps/api/tests/test_planetary_aoi_e2e_170.py -q

  echo "[goal170] >>> OpenAPI export drift check"
  uv run python scripts/export_openapi.py --check

  echo "[goal170] >>> contract/type surface markers"
  uv run python - <<'PY'
from pathlib import Path
import json, sys
root = Path.cwd()
openapi = json.loads((root / 'contracts/openapi.json').read_text())
paths = openapi.get('paths', {})
required_paths = [
    '/api/v1/planetary/areas/analyze',
    '/api/v1/jobs/{job_id}',
    '/api/v1/typed/areas/{area_id}',
]
missing = [p for p in required_paths if p not in paths]
if missing:
    print('[goal170] FAIL: missing OpenAPI paths:', missing)
    sys.exit(1)
schemas = openapi.get('components', {}).get('schemas', {})
for name in ['PlanetaryAoiAnalyzeRequest', 'AoiAnalyzeResponse', 'JobOut']:
    if name not in schemas:
        print('[goal170] FAIL: missing OpenAPI schema:', name)
        sys.exit(1)
schema_dts = (root / 'apps/web/src/api/schema.d.ts').read_text()
for marker in ['/api/v1/planetary/areas/analyze', 'PlanetaryAoiAnalyzeRequest', 'AoiAnalyzeResponse', 'requested_features']:
    if marker not in schema_dts:
        print('[goal170] FAIL: missing TS schema marker:', marker)
        sys.exit(1)
print('[goal170] contract/type markers ok')
PY

  echo "[goal170] >>> resource budget snapshot (isolated temp artifacts)"
  du -sh "$TMP_DIR" 2>/dev/null || true
  find "$TMP_DIR" -maxdepth 2 -type f -printf '[goal170] artifact %P %s bytes\n' 2>/dev/null | sort || true

  echo "[goal170] PASS offline acceptance (bounded committed pilots; not global/exhaustive)"
fi
