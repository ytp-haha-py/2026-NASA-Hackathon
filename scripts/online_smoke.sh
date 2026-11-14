#!/usr/bin/env bash
# OPT-IN online smoke: exercises real Copernicus DEM + ESA WorldCover AOI processing.
# Requires network access. This is NOT part of CI or scripts/verify.sh.
#
#   bash scripts/online_smoke.sh
#   ANALOG_ONLINE_PORT=8131 bash scripts/online_smoke.sh

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PORT="${ANALOG_ONLINE_PORT:-8131}"
BASE="http://127.0.0.1:${PORT}"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/analog-online.XXXXXX")"
DB_URL="sqlite+pysqlite:///$TMP/analog.db"
SERVER_PID=""

cleanup() {
  local status=$?
  if [ -n "$SERVER_PID" ]; then
    kill "$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true
  fi
  if [ "$status" -ne 0 ] && [ -f "$TMP/uvicorn.log" ]; then
    printf '\n[online-smoke] --- uvicorn log ---\n' >&2
    tail -n 40 "$TMP/uvicorn.log" >&2 || true
  fi
  rm -rf "$TMP"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

fail() {
  printf '\n[online-smoke] ERROR: %s\n' "$*" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || {
    printf 'ERROR: required command "%s" is missing.\n' "$1" >&2
    exit 127
  }
}

require_command uv
require_command curl

printf '[online-smoke] real provider smoke (network required)\n'
if curl -sS --max-time 1 -o /dev/null "$BASE/api/v1/health" 2>/dev/null; then
  fail "port $PORT is already serving HTTP; set ANALOG_ONLINE_PORT."
fi

export ANALOG_DATABASE_URL="$DB_URL"
export ANALOG_SEED_INCLUDE_SYNTHETIC=false

printf '[online-smoke] seeding committed real manifests (Earth + WorldCover + Moon)\n'
uv run python -m analog_api.cli seed

uv run uvicorn analog_api.main:create_app --factory \
  --host 127.0.0.1 --port "$PORT" --log-level warning >"$TMP/uvicorn.log" 2>&1 &
SERVER_PID=$!

READY=0
for _ in $(seq 1 80); do
  if ! kill -0 "$SERVER_PID" 2>/dev/null; then
    fail "uvicorn exited before becoming ready"
  fi
  if curl -fsS --max-time 1 "$BASE/api/v1/health" -o "$TMP/health.json" 2>/dev/null; then
    READY=1
    break
  fi
  sleep 0.25
done
[ "$READY" -eq 1 ] || fail "uvicorn did not become ready"

printf '[online-smoke] capabilities\n'
curl -fsS --max-time 10 "$BASE/api/v1/capabilities" -o "$TMP/caps.json"
uv run python - "$TMP/caps.json" <<'PY'
import json, sys
caps = json.load(open(sys.argv[1]))
assert caps["indexed_candidate_count"] >= 5, caps["indexed_candidate_count"]
assert caps["available_reference_ids"], "expected a verified lunar reference"
assert any(f["score_role"] == "context_only" for f in caps["context_features"])
assert caps["aoi_analysis"]["max_side_m"] == 2000
print(
    f"[online-smoke] indexed={caps['indexed_candidate_count']} "
    f"references={len(caps['available_reference_ids'])} "
    f"context_features={len(caps['context_features'])}"
)
PY

# A new AOI near Rio Tinto, not identical to a committed seed window.
BODY='{"body":"Earth","bbox":{"west":-6.63,"south":37.71,"east":-6.613,"north":37.727},"requested_features":["slope_mean_deg","slope_p90_deg","terrain_relief_p95_p05_m","roughness_std_m"]}'
printf '[online-smoke] submitting a new on-demand AOI\n'
CODE="$(curl -sS --max-time 30 -o "$TMP/analyze.json" -w '%{http_code}' \
  -X POST -H 'Content-Type: application/json' -d "$BODY" "$BASE/api/v1/areas/analyze")"
printf '[online-smoke] analyze HTTP %s\n' "$CODE"
[ "$CODE" = "202" ] || [ "$CODE" = "200" ] || fail "unexpected analyze HTTP $CODE"
JOB_ID="$(uv run python - "$TMP/analyze.json" <<'PY'
import json, sys
print(json.load(open(sys.argv[1]))["job_id"])
PY
)"
AREA_ID="$(uv run python - "$TMP/analyze.json" <<'PY'
import json, sys
print(json.load(open(sys.argv[1])).get("area_id") or "")
PY
)"

printf '[online-smoke] polling job %s\n' "$JOB_ID"
STATUS="queued"
for _ in $(seq 1 180); do
  curl -fsS --max-time 10 "$BASE/api/v1/jobs/$JOB_ID" -o "$TMP/job.json"
  STATUS="$(uv run python - "$TMP/job.json" <<'PY'
import json, sys
print(json.load(open(sys.argv[1]))["status"])
PY
)"
  case "$STATUS" in
    completed | partial | failed) break ;;
  esac
  sleep 1
done
[ "$STATUS" = "completed" ] || fail "AOI job ended with status=$STATUS"
[ -n "$AREA_ID" ] || fail "analyze response did not include area_id"

curl -fsS --max-time 20 "$BASE/api/v1/areas/$AREA_ID/features" -o "$TMP/features.json"
uv run python - "$TMP/features.json" "$TMP/job.json" <<'PY'
import json, sys
features = json.load(open(sys.argv[1]))
job = json.load(open(sys.argv[2]))
assert features["origin"] == "on_demand", features["origin"]
assert len(features["features"]) == 4, features["features"]
assert features["data_kind"] == "observed"
assert {f["feature_id"] for f in features["features"]} == {
    "slope_mean_deg", "slope_p90_deg",
    "terrain_relief_p95_p05_m", "roughness_std_m",
}
assert job["provider_status"]["copernicus-dem-glo90"]["status"] == "verified"
assert features["datasets"], "AOI must expose provenance"
print(
    f"[online-smoke] area={features['location_id']} "
    f"features={[(f['feature_id'], round(f['value'], 3)) for f in features['features']]} "
    f"context={len(features['context_observations'])} "
    f"providers={list(job['provider_status'])}"
)
PY

printf '[online-smoke] verifying idempotent cache\n'
CODE="$(curl -sS --max-time 30 -o "$TMP/analyze2.json" -w '%{http_code}' \
  -X POST -H 'Content-Type: application/json' -d "$BODY" "$BASE/api/v1/areas/analyze")"
[ "$CODE" = "200" ] || fail "expected cached 200, got $CODE"
uv run python - "$TMP/analyze2.json" "$AREA_ID" <<'PY'
import json, sys
payload = json.load(open(sys.argv[1]))
assert payload["cached"] is True, payload
assert payload["area_id"] == sys.argv[2], payload
print("[online-smoke] cache hit confirmed")
PY

printf '\n[online-smoke] RESULT: PASS - real Copernicus + WorldCover AOI processed, persisted, cached\n'
