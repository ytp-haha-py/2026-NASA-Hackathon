#!/usr/bin/env bash
# Deterministic P0 quality gate for the Planetary Analog Explorer.
#
# Usage:
#   bash scripts/verify.sh                 # full gate (backend + frontend + contracts)
#   bash scripts/verify.sh --backend-only  # backend/contracts only (frontend not required)
#   bash scripts/verify.sh --ci            # same checks, CI-friendly output (no color)
#
# Never downloads source data, never starts containers, and never leaves the
# OpenAPI snapshot modified.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

BACKEND_ONLY=0
CI_MODE=0
for arg in "$@"; do
  case "$arg" in
    --backend-only)
      BACKEND_ONLY=1
      ;;
    --ci)
      CI_MODE=1
      export CI=true
      ;;
    -h | --help)
      sed -n '2,10p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      printf 'ERROR: unknown argument: %s\n' "$arg" >&2
      printf 'Usage: bash scripts/verify.sh [--backend-only] [--ci]\n' >&2
      exit 2
      ;;
  esac
done

TOTAL_START=$SECONDS
STEP=0
VERIFY_TMP="$(mktemp -d "${TMPDIR:-/tmp}/analog-verify.XXXXXX")"
OPENAPI_BACKUP=""

# Required suites, plus pipeline suites that are added over time. Only existing
# directories are passed to pytest so the gate works before the planetary
# pipeline lands.
REQUIRED_TEST_DIRS=(apps/api/tests pipelines/tests)
OPTIONAL_TEST_DIRS=(pipelines/d02_d04_aoi/tests pipelines/earth/tests pipelines/worldcover/tests pipelines/planetary/tests)
TEST_DIRS=()
for test_dir in "${REQUIRED_TEST_DIRS[@]}" "${OPTIONAL_TEST_DIRS[@]}"; do
  if [ -d "$test_dir" ]; then
    TEST_DIRS+=("$test_dir")
  fi
done

cleanup() {
  local status=$?
  if [ -n "$OPENAPI_BACKUP" ] && [ -f "$OPENAPI_BACKUP" ] && [ -f "$ROOT/contracts/openapi.json" ]; then
    cp "$OPENAPI_BACKUP" "$ROOT/contracts/openapi.json"
  fi
  rm -rf "$VERIFY_TMP"
  if [ "$status" -ne 0 ]; then
    printf '\n[verify] RESULT: FAIL after %ss\n' "$((SECONDS - TOTAL_START))" >&2
  fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

fail() {
  printf '\n[verify] ERROR: %s\n' "$*" >&2
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
    npm | node)
      printf 'Install Node.js 22 LTS (includes npm): https://nodejs.org/en/download\n' >&2
      ;;
    *)
      printf 'Install "%s" and retry.\n' "$name" >&2
      ;;
  esac
  exit 127
}

run_step() {
  local label="$1"
  shift
  STEP=$((STEP + 1))
  local started=$SECONDS
  printf '\n[verify] STEP %d: %s\n' "$STEP" "$label"
  if "$@"; then
    printf '[verify] OK: %s (%ss)\n' "$label" "$((SECONDS - started))"
  else
    local status=$?
    printf '\n[verify] FAIL: %s exited %d after %ss\n' "$label" "$status" "$((SECONDS - started))" >&2
    exit "$status"
  fi
}

check_backend_tests_present() {
  local missing=0
  local directory
  for directory in "${REQUIRED_TEST_DIRS[@]}"; do
    if [ ! -d "$directory" ]; then
      printf 'ERROR: required test directory missing: %s\n' "$directory" >&2
      missing=1
    fi
  done
  for directory in "${OPTIONAL_TEST_DIRS[@]}"; do
    if [ ! -d "$directory" ]; then
      printf 'Optional test directory not present (skipped): %s\n' "$directory"
    fi
  done
  if [ "$missing" -ne 0 ]; then
    printf 'ERROR: the P0 gate requires the suites in: %s\n' "${REQUIRED_TEST_DIRS[*]}" >&2
    return 1
  fi
  find "${TEST_DIRS[@]}" -type f -name 'test_*.py' >"$VERIFY_TMP/test-files.txt"
  if [ ! -s "$VERIFY_TMP/test-files.txt" ]; then
    printf 'ERROR: no test_*.py files found under: %s\n' "${TEST_DIRS[*]}" >&2
    return 1
  fi
  printf 'Found %s backend test file(s) in: %s\n' \
    "$(wc -l <"$VERIFY_TMP/test-files.txt" | tr -d ' ')" "${TEST_DIRS[*]}"
}

check_openapi_snapshot() {
  local snapshot="$ROOT/contracts/openapi.json"
  if [ ! -f "$snapshot" ]; then
    printf 'ERROR: contracts/openapi.json is missing. Generate it with scripts/export_openapi.py.\n' >&2
    return 1
  fi
  OPENAPI_BACKUP="$VERIFY_TMP/openapi.committed.json"
  cp "$snapshot" "$OPENAPI_BACKUP"

  printf 'Regenerating OpenAPI schema via scripts/export_openapi.py (snapshot is restored afterwards).\n'
  if ! uv run python scripts/export_openapi.py; then
    cp "$OPENAPI_BACKUP" "$snapshot"
    printf 'ERROR: OpenAPI generation failed; contracts/openapi.json was restored.\n' >&2
    return 1
  fi
  if ! diff -u "$OPENAPI_BACKUP" "$snapshot"; then
    cp "$OPENAPI_BACKUP" "$snapshot"
    printf '\nERROR: contracts/openapi.json is stale or hand-edited.\n' >&2
    printf 'Regenerate intentionally with: uv run python scripts/export_openapi.py\n' >&2
    printf 'Then commit the updated snapshot. The working copy has been restored.\n' >&2
    return 1
  fi
  cp "$OPENAPI_BACKUP" "$snapshot"
  OPENAPI_BACKUP=""
  printf 'OpenAPI snapshot matches the generated schema.\n'
}

frontend_prereqs() {
  local package_json="apps/web/package.json"
  if [ -f "$package_json" ]; then :; else
    printf 'ERROR: %s does not exist.\n' "$package_json" >&2
    printf 'The full gate requires the delivered frontend. Use --backend-only only for\n' >&2
    printf 'backend/contract checks while apps/web is being built.\n' >&2
    return 1
  fi
  if [ ! -f apps/web/package-lock.json ]; then
    printf 'ERROR: apps/web/package-lock.json is missing; npm ci requires a committed lockfile.\n' >&2
    return 1
  fi
  local script
  for script in typecheck lint test build check:api check:dev; do
    if ! node -e '
      const fs = require("fs");
      const pkg = JSON.parse(fs.readFileSync(process.argv[1], "utf8"));
      const name = process.argv[2];
      if (!pkg.scripts || !pkg.scripts[name]) {
        console.error("ERROR: apps/web/package.json is missing required script: " + name);
        process.exit(1);
      }
    ' "$package_json" "$script"; then
      printf 'ERROR: the full gate requires npm script "%s" in apps/web/package.json.\n' "$script" >&2
      return 1
    fi
  done
  printf 'Frontend prerequisites present (package.json, package-lock.json, required scripts).\n'
}

frontend_ci() {
  (cd apps/web && npm ci --no-audit --no-fund)
}

frontend_script() {
  local script="$1"
  shift
  (cd apps/web && npm run "$script" -- "$@")
}

frontend_test() {
  (cd apps/web && npm run test -- --run)
}

printf '[verify] Planetary Analog Explorer quality gate\n'
printf '[verify] repo: %s\n' "$ROOT"
printf '[verify] mode: %s\n' "$([ "$BACKEND_ONLY" -eq 1 ] && printf 'backend-only' || printf 'full')"
printf '[verify] output: %s\n' "$([ "$CI_MODE" -eq 1 ] && printf 'ci' || printf 'local')"

require_command uv
printf '[verify] %s\n' "$(uv --version)"

if [ "$BACKEND_ONLY" -eq 0 ]; then
  require_command npm
  require_command node
  node_major="$(node -p 'process.versions.node.split(".")[0]')"
  if [ "$node_major" -lt 22 ] 2>/dev/null; then
    fail "Node.js 22+ is required by apps/web (found $(node --version))."
  fi
  printf '[verify] node: %s, npm: %s\n' "$(node --version)" "$(npm --version)"
fi

run_step "uv sync --frozen" uv sync --frozen
run_step "ruff check" uv run ruff check .
run_step "ruff format --check" uv run ruff format --check .
run_step "backend tests present" check_backend_tests_present
run_step "pytest ${TEST_DIRS[*]}" uv run pytest "${TEST_DIRS[@]}" -q
run_step "OpenAPI snapshot is current" check_openapi_snapshot
run_step "offline data snapshot + manifest validation" bash scripts/validate_data.sh

if [ "$BACKEND_ONLY" -eq 1 ]; then
  printf '\n[verify] Frontend checks skipped (--backend-only).\n'
else
  run_step "frontend prerequisites" frontend_prereqs
  run_step "frontend: npm ci" frontend_ci
  run_step "frontend: npm run typecheck" frontend_script typecheck
  run_step "frontend: npm run lint" frontend_script lint
  run_step "frontend: npm run test -- --run" frontend_test
  run_step "frontend: npm run build" frontend_script build
  run_step "frontend: npm run check:dev" frontend_script check:dev
  run_step "frontend: npm run check:api" frontend_script check:api
fi

printf '\n[verify] RESULT: PASS - %d steps in %ss (%s)\n' \
  "$STEP" "$((SECONDS - TOTAL_START))" \
  "$([ "$BACKEND_ONLY" -eq 1 ] && printf 'backend-only' || printf 'full gate')"
