#!/usr/bin/env bash
# Headless integration smoke test.
#
# Starts the real FastAPI app against an isolated temporary SQLite database,
# seeded only with the committed TEST-ONLY synthetic fixture. No network data is
# downloaded, ./var is never touched, and no external service is required.
#
# Usage:
#   bash scripts/smoke.sh
#   ANALOG_SMOKE_PORT=8124 bash scripts/smoke.sh   # if 8123 is busy

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PORT="${ANALOG_SMOKE_PORT:-8123}"
BASE="http://127.0.0.1:${PORT}"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/analog-smoke.XXXXXX")"
DB_PATH="$TMP/analog.db"
DB_URL="sqlite+pysqlite:///$DB_PATH"
EMPTY_MANIFESTS="$TMP/no-manifests"
SERVER_PID=""

cleanup() {
  local status=$?
  if [ -n "$SERVER_PID" ]; then
    kill "$SERVER_PID" 2>/dev/null || true
    for _ in $(seq 1 20); do
      if ! kill -0 "$SERVER_PID" 2>/dev/null; then
        break
      fi
      sleep 0.1
    done
    kill -9 "$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true
  fi
  if [ "$status" -ne 0 ] && [ -f "$TMP/uvicorn.log" ]; then
    printf '\n[smoke] --- uvicorn log (last 40 lines) ---\n' >&2
    tail -n 40 "$TMP/uvicorn.log" >&2 || true
  fi
  rm -rf "$TMP"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

fail() {
  printf '\n[smoke] ERROR: %s\n' "$*" >&2
  exit 1
}

require_command() {
  local name="$1"
  if command -v "$name" >/dev/null 2>&1; then
    return 0
  fi
  printf 'ERROR: required command "%s" is not installed or not on PATH.\n' "$name" >&2
  case "$name" in
    uv)
      printf 'Install uv: https://docs.astral.sh/uv/getting-started/installation/\n' >&2
      ;;
    curl)
      printf 'Install curl with your system package manager.\n' >&2
      ;;
    *)
      printf 'Install "%s" and retry.\n' "$name" >&2
      ;;
  esac
  exit 127
}

fetch() {
  local name="$1"
  local expected="$2"
  shift 2
  local code
  code="$(curl -sS --max-time 10 -o "$TMP/$name.json" -w '%{http_code}' "$@")" || {
    printf 'ERROR: curl failed for %s\n' "$name" >&2
    return 1
  }
  printf '[smoke] %-18s HTTP %s\n' "$name" "$code"
  if [ "$code" != "$expected" ]; then
    printf 'ERROR: expected HTTP %s for %s, got %s\n' "$expected" "$name" "$code" >&2
    cat "$TMP/$name.json" >&2 || true
    return 1
  fi
}

printf '[smoke] headless integration smoke\n'
printf '[smoke] repo: %s\n' "$ROOT"
require_command uv
require_command curl
printf '[smoke] %s\n' "$(uv --version)"

if curl -sS --max-time 1 -o /dev/null "$BASE/api/v1/health" 2>/dev/null; then
  fail "port $PORT is already serving HTTP. Stop that server or set ANALOG_SMOKE_PORT."
fi

mkdir -p "$EMPTY_MANIFESTS"
export ANALOG_DATABASE_URL="$DB_URL"
export ANALOG_SEED_INCLUDE_SYNTHETIC=true
export ANALOG_MANIFEST_DIR="$EMPTY_MANIFESTS"
export ANALOG_MANIFEST_DIR_CONTEXT="$EMPTY_MANIFESTS"
export ANALOG_MANIFEST_DIR_PLANETARY="$EMPTY_MANIFESTS"
export ANALOG_AUTO_SEED=false

printf '[smoke] isolated database: %s\n' "$DB_PATH"
printf '[smoke] seeding synthetic fixture only (no real manifests, no network)\n'
uv run python -m analog_api.cli seed \
  --database-url "$DB_URL" \
  --manifest-dir "$EMPTY_MANIFESTS" \
  --manifest-dir-context "$EMPTY_MANIFESTS" \
  --manifest-dir-planetary "$EMPTY_MANIFESTS" \
  --include-synthetic

printf '[smoke] index status:\n'
uv run python -m analog_api.cli status --database-url "$DB_URL"

printf '[smoke] starting uvicorn on %s\n' "$BASE"
uv run uvicorn analog_api.main:create_app \
  --factory \
  --host 127.0.0.1 \
  --port "$PORT" \
  --log-level warning >"$TMP/uvicorn.log" 2>&1 &
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
if [ "$READY" -ne 1 ]; then
  fail "uvicorn did not become ready at $BASE within 20s"
fi
printf '[smoke] server ready (pid %s)\n' "$SERVER_PID"

fetch health 200 "$BASE/api/v1/health"
fetch capabilities 200 "$BASE/api/v1/capabilities"
fetch search 200 \
  -X POST \
  -H 'Content-Type: application/json' \
  --data '{"body":"custom","target":{"slope_mean_deg":{"value":6.0,"weight":1.0},"terrain_relief_p95_p05_m":{"value":90.0,"weight":0.5}},"limit":3}' \
  "$BASE/api/v1/search"

LOCATION_ID="$(uv run python - "$TMP/search.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    payload = json.load(handle)
print(payload["results"][0]["location_id"])
PY
)"
if [ -z "$LOCATION_ID" ]; then
  fail "search returned no usable location_id"
fi
printf '[smoke] top search result: %s\n' "$LOCATION_ID"

fetch location 200 "$BASE/api/v1/locations/$LOCATION_ID"
fetch context 200 \
  --get \
  --data-urlencode 'target={"slope_mean_deg": 6.0}' \
  "$BASE/api/v1/locations/$LOCATION_ID/context"
fetch missing_location 404 "$BASE/api/v1/locations/earth-does-not-exist"

uv run python - "$TMP" "$LOCATION_ID" <<'PY'
from __future__ import annotations

import json
import math
import sys
from datetime import datetime
from pathlib import Path

tmp = Path(sys.argv[1])
location_id = sys.argv[2]


def load(name: str) -> dict:
    with (tmp / f"{name}.json").open(encoding="utf-8") as handle:
        return json.load(handle)


def finite_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


health = load("health")
assert health["status"] == "ok", health
assert health["data_version"].startswith("dv-"), health

capabilities = load("capabilities")
expected_features = {
    "slope_mean_deg",
    "slope_p90_deg",
    "terrain_relief_p95_p05_m",
    "roughness_std_m",
}
assert capabilities["contract_version"] == "1.0.0"
assert capabilities["method_version"] == "terrain-metrics-1.0.0"
assert {feature["id"] for feature in capabilities["features"]} == expected_features
assert capabilities["indexed_candidate_count"] == 4, capabilities["indexed_candidate_count"]
assert capabilities["coverage"]["label"] == "indexed_subset"
assert capabilities["coverage"]["data_kinds"] == ["synthetic"], capabilities["coverage"]
assert "custom" in capabilities["supported_bodies"]

search = load("search")
assert search["contract_version"] == "1.0.0"
assert search["query_version"] == "search-1.0.0"
assert search["coverage_label"] == "indexed_subset"
assert search["indexed_candidate_count"] == 4
assert search["eligible_candidate_count"] == 4, search["eligible_candidate_count"]
assert len(search["results"]) == 3, len(search["results"])
assert search["results"][0]["location_id"] == location_id
for expected_rank, result in enumerate(search["results"], start=1):
    assert result["rank"] == expected_rank
    assert result["data_kind"] == "synthetic", result["data_kind"]
    assert result["location_id"].startswith("earth-synthetic-")
    assert finite_number(result["distance"]) and result["distance"] >= 0
    assert result["metrics"], result["location_id"]
    assert result["caveats"]
    assert any("SYNTHETIC" in caveat for caveat in result["caveats"])

location = load("location")
assert location["location_id"] == location_id
assert location["body"] == "Earth"
assert location["data_kind"] == "synthetic"
assert location["contract_version"] == "1.0.0"
assert {feature["feature_id"] for feature in location["features"]} == expected_features
assert location["datasets"], "location must expose its dataset provenance"
assert location["datasets"][0]["source_kind"] == "synthetic"
assert location["known_limitations"]
assert any("SYNTHETIC" in text for text in location["known_limitations"])

context = load("context")
assert context["export_version"] == "1.0.0"
assert context["location"]["location_id"] == location_id
assert context["query"] == {"slope_mean_deg": 6.0}, context["query"]
assert context["source_facts"]
assert context["limitations"]
assert context["computed_comparisons"]
slope_comparison = next(
    item for item in context["computed_comparisons"] if item["feature_id"] == "slope_mean_deg"
)
assert slope_comparison["target"] == 6.0
assert finite_number(slope_comparison["observed"])
assert finite_number(slope_comparison["delta"])
datetime.fromisoformat(context["generated_at"])

missing = load("missing_location")
assert missing["error"]["code"] == "not_found", missing

print("\n[smoke] observed values")
print(f"  health.status              = {health['status']}")
print(f"  health.data_version        = {health['data_version']}")
print(f"  capabilities.contract      = {capabilities['contract_version']}")
print(f"  capabilities.feature_count = {len(capabilities['features'])}")
print(f"  capabilities.indexed       = {capabilities['indexed_candidate_count']}")
print(f"  search.results             = {len(search['results'])}")
print(f"  search.top.location_id     = {search['results'][0]['location_id']}")
print(f"  search.top.distance        = {search['results'][0]['distance']:.6f}")
print(f"  search.top.data_kind       = {search['results'][0]['data_kind']}")
print(f"  location.body              = {location['body']}")
print(f"  location.features          = {len(location['features'])}")
print(f"  location.datasets          = {len(location['datasets'])}")
print(f"  context.comparisons        = {len(context['computed_comparisons'])}")
print(f"  missing.http_error         = {missing['error']['code']}")
print("\n[smoke] RESULT: PASS - all endpoint assertions satisfied")
PY

printf '[smoke] server shutdown and temporary database cleanup follow\n'
