#!/usr/bin/env bash
# Goal H (#170) offline acceptance: Moon+Mars real committed AOIs, required negative cases,
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

Required case mapping (reproducible, committed real sources only):
  - Moon 3 real committed AOIs (successful D01): moon-lola128-eqc-window-01 cells/block
    (low latitude ~10S, bounded pilot).
  - Mars 3 real committed AOIs (successful D01): mars-ctx-jezero-window-01 cells/block
    (low/mid latitude ~18.5N, bounded pilot).
  - Nodata/out-of-coverage (Moon+Mars): explicit non-fabricated failure, no persisted fake typed area.
  - Low/mid/high latitude coverage requests (Moon+Mars): where committed pilot coverage is absent,
    explicit non-fabricated outcomes are asserted (not a claim of global successful coverage).
  - Polar (Moon+Mars): explicit non-fabricated outcome.
  - Dateline/antimeridian crossing (Moon+Mars): explicit non-fabricated outcome.
  - Oversized AOI (Moon+Mars): explicit 422 aoi_too_large.
  - Source outage (Moon+Mars): simulated provider outage -> explicit failed job, no fake success.
  - Cache/resume/idempotency, API/OpenAPI/TS compatibility (#132/#160 surfaces), resource snapshot.

Checks:
  - apps/api/tests/test_planetary_aoi_e2e_170.py
  - contracts/openapi.json and apps/web/src/api/schema.d.ts contract/type markers
  - scripts/export_openapi.py --check (schema export drift guard)

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

  echo "[goal170] committed coverage context (bounded, not global/exhaustive) — per-source proof"
  echo "[goal170]   Moon analysis-eligible: pipelines/planetary/coarse/moon_lola128_window01.json (moon-lola128-eqc-window-01, 4 cells ~19.833-20.168E,-10.165..-9.835S, low lat ~10S)"
  echo "[goal170]   Mars analysis-eligible: pipelines/planetary/coarse/mars_ctx_jezero_window01.json (mars-ctx-jezero-window-01, 4 cells ~78.082-78.260E,18.473-18.642N, low/mid lat ~18.5N)"
  echo "[goal170]   Mars evidence-only (not comparable): pipelines/planetary/coarse/mars_mola_meg128_evidence.json"
  echo "[goal170]   No other committed Moon/Mars coarse pilot manifests/cells/fixtures exist; no committed mid/high-lat Moon, low-outside/high-lat Mars, polar, dateline-crossing-in-coverage, or in-coverage nodata-only regions for successful real offline analysis."
  echo "[goal170]   Only 6 committed-pilot AOIs can succeed (Moon 3 low-lat, Mars 3 low/mid-lat). All other required cases map to explicit non-fabricated outcomes."

  export ANALOG_DATABASE_URL="sqlite+pysqlite:///${TMP_DIR}/goal170.db"
  export ANALOG_AOI_RUNTIME_DIR="${TMP_DIR}/aoi-runtime"
  export ANALOG_SEED_INCLUDE_SYNTHETIC="false"
  export ANALOG_SEED_INCLUDE_TYPED="true"
  export ANALOG_AOI_INLINE="true"
  export ANALOG_PLANETARY_ALLOW_NETWORK="false"

  run_timed "pytest planetary AOI E2E (Goal H, required-case mapping)" \
    uv run pytest apps/api/tests/test_planetary_aoi_e2e_170.py -q -ra

  echo "[goal170] >>> OpenAPI export drift check (inline, no file writes)"
  uv run python - <<'PY'
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from analog_api.config import Settings
from analog_api.main import create_app

root = Path.cwd()
out = root / "contracts/openapi.json"
if not out.exists():
    raise SystemExit("[goal170] FAIL: contracts/openapi.json missing")
with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
    settings = Settings(
        database_url=f"sqlite+pysqlite:///{Path(tmp) / 'openapi.db'}",
        seed_include_synthetic=True,
        auto_seed=True,
    )
    schema = create_app(settings).openapi()
rendered = json.dumps(schema, indent=2, sort_keys=True) + "\n"
committed = out.read_bytes().decode("utf-8")
if committed != rendered:
    for i, (a, b) in enumerate(zip(committed.splitlines(), rendered.splitlines()), start=1):
        if a != b:
            print(f"[goal170] FAIL: OpenAPI snapshot drift at line {i}")
            print("-", a)
            print("+", b)
            raise SystemExit(1)
    raise SystemExit("[goal170] FAIL: OpenAPI snapshot drift")
print(f"[goal170] contracts/openapi.json is current ({len(schema.get('paths', {}))} paths)")
PY

  echo "[goal170] >>> contract/type surface markers (#132/#160 surfaces)"
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
    '/api/v1/planetary/map-features',
    '/api/v1/earth/map-features',
]
missing = [p for p in required_paths if p not in paths]
if missing:
    print('[goal170] FAIL: missing OpenAPI paths:', missing)
    sys.exit(1)
schemas = openapi.get('components', {}).get('schemas', {})
for name in ['PlanetaryAoiAnalyzeRequest', 'AoiAnalyzeResponse', 'JobOut', 'ErrorOut']:
    if name not in schemas:
        print('[goal170] FAIL: missing OpenAPI schema:', name)
        sys.exit(1)
schema_dts = (root / 'apps/web/src/api/schema.d.ts').read_text()
for marker in ['/api/v1/planetary/areas/analyze', 'PlanetaryAoiAnalyzeRequest', 'AoiAnalyzeResponse', 'requested_features', 'JobOut']:
    if marker not in schema_dts:
        print('[goal170] FAIL: missing TS schema marker:', marker)
        sys.exit(1)
print('[goal170] contract/type markers ok')
PY

  echo "[goal170] >>> required-case evidence summary (from tests)"
  cat <<'EOF'
[goal170]   Moon real committed AOIs (3, successful D01): moon_committed_real_1..3 (SW/NE/full low-lat pilot)
[goal170]   Mars real committed AOIs (3, successful D01): mars_committed_real_1..3 (SW/NE/full low-mid-lat pilot)
[goal170]   Nodata/out-of-coverage: moon_nodata_out_of_coverage, mars_nodata_out_of_coverage (explicit non-fabricated)
[goal170]   Low/mid/high-lat required requests: moon_midlat_outside_committed, moon_highlat_outside_committed, mars_lowlat_outside_committed, mars_highlat_outside_committed (outside committed pilots -> explicit non-fabricated)
[goal170]   Polar: moon_polar, mars_polar (explicit non-fabricated)
[goal170]   Dateline/antimeridian: moon_dateline_crossing, mars_dateline_crossing (explicit non-fabricated)
[goal170]   Oversized: Moon+Mars -> 422 aoi_too_large
[goal170]   Source outage: Moon+Mars simulated provider outage -> failed job, no fake success/persisted typed area
[goal170]   Cache/resume/idempotency + OpenAPI/TS compatibility: dedicated tests passed
EOF

  echo "[goal170] >>> resource budget snapshot (isolated temp artifacts)"
  du -sh "$TMP_DIR" 2>/dev/null || true
  find "$TMP_DIR" -maxdepth 2 -type f -printf '[goal170] artifact %P %s bytes\n' 2>/dev/null | sort || true

  echo "[goal170] PASS offline acceptance (bounded committed pilots; explicit non-fabricated outcomes for uncovered required cases; not global/exhaustive)"
fi
