#!/usr/bin/env bash
# Deterministic offline data gate for the Planetary Analog Explorer.
#
# Usage:
#   bash scripts/validate_data.sh
#   ANALOG_SNAPSHOT=samples/snapshot/analog-demo.snapshot.json bash scripts/validate_data.sh
#
# Checks, in order:
#   1. Snapshot identity, pinned manifest SHA-256 values, and artifact checksums.
#   2. Every committed pipelines/*/manifests/*.json against
#      contracts/pipeline-manifest.schema.json (Draft 2020-12), and that every
#      committed manifest is pinned by the snapshot.
#   3. A no-write index rebuild (pipelines/build_index.py --dry-run) with counts.
#
# No network access, no credentials, and no writes inside the repository. The
# dry-run database URL points at a temporary directory only.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

SNAPSHOT="${ANALOG_SNAPSHOT:-samples/snapshot/analog-demo.snapshot.json}"
SCHEMA="contracts/pipeline-manifest.schema.json"
TOTAL_START=$SECONDS
TMP="$(mktemp -d "${TMPDIR:-/tmp}/analog-data-validate.XXXXXX")"

cleanup() {
  local status=$?
  rm -rf "$TMP"
  if [ "$status" -ne 0 ]; then
    printf '\n[data-validate] RESULT: FAIL after %ss\n' "$((SECONDS - TOTAL_START))" >&2
  fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

fail() {
  printf '\n[data-validate] ERROR: %s\n' "$*" >&2
  exit 1
}

require_command() {
  if command -v "$1" >/dev/null 2>&1; then
    return 0
  fi
  printf 'ERROR: required command "%s" is not installed or not on PATH.\n' "$1" >&2
  case "$1" in
    uv)
      printf 'Install uv: https://docs.astral.sh/uv/getting-started/installation/\n' >&2
      ;;
  esac
  exit 127
}

STEP=0

run_step() {
  local label="$1"
  shift
  STEP=$((STEP + 1))
  printf '\n[data-validate] STEP %d: %s\n' "$STEP" "$label"
  local status=0
  "$@" || status=$?
  if [ "$status" -eq 0 ]; then
    return 0
  fi
  printf '\n[data-validate] FAIL: %s exited %d\n' "$label" "$status" >&2
  exit "$status"
}

printf '[data-validate] Planetary Analog Explorer offline data gate\n'
printf '[data-validate] repo: %s\n' "$ROOT"
printf '[data-validate] snapshot: %s\n' "$SNAPSHOT"

require_command uv
printf '[data-validate] %s\n' "$(uv --version)"

[ -f "$SNAPSHOT" ] || fail "snapshot not found: $SNAPSHOT"
[ -f "$SCHEMA" ] || fail "manifest schema not found: $SCHEMA"

verify_snapshot() {
  uv run python -m analog_pipelines.snapshot verify --snapshot "$SNAPSHOT"
}

run_step "snapshot verify (pinned manifest + artifact SHA-256)" verify_snapshot

MANIFEST_LIST="$TMP/manifests.txt"
find pipelines -type f -path '*/manifests/*.json' -print | LC_ALL=C sort >"$MANIFEST_LIST"
if [ ! -s "$MANIFEST_LIST" ]; then
  fail "no manifests found under pipelines/*/manifests/; the data gate requires at least one"
fi
MANIFEST_COUNT="$(wc -l <"$MANIFEST_LIST" | tr -d ' ')"
printf '\n[data-validate] committed manifests: %s\n' "$MANIFEST_COUNT"
sed 's/^/[data-validate]   /' "$MANIFEST_LIST"

check_manifests() {
  uv run python - "$SCHEMA" "$SNAPSHOT" "$MANIFEST_LIST" <<'PY'
import json
import sys
from pathlib import Path

from jsonschema import Draft202012Validator

schema_path, snapshot_path, list_path = sys.argv[1:4]
schema = json.loads(Path(schema_path).read_text(encoding="utf-8"))
snapshot = json.loads(Path(snapshot_path).read_text(encoding="utf-8"))
paths = [line.strip() for line in Path(list_path).read_text(encoding="utf-8").splitlines() if line.strip()]

validator = Draft202012Validator(schema)
errors: list[str] = []
for path in paths:
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        errors.append(f"{path}: invalid JSON: {exc}")
        continue
    for error in sorted(validator.iter_errors(document), key=lambda item: list(item.absolute_path)):
        location = "/".join(str(part) for part in error.absolute_path) or "<root>"
        errors.append(f"{path}: {location}: {error.message}")

pinned = {entry["path"] for entry in snapshot.get("manifests", [])}
committed = set(paths)
unpinned = sorted(committed - pinned)
if unpinned:
    errors.append(
        "manifest(s) committed but not pinned by the snapshot: "
        + ", ".join(unpinned)
        + ". Rebuild the snapshot and commit it in the same change."
    )

if errors:
    for message in errors:
        print(f"[data-validate] ERROR: {message}", file=sys.stderr)
    sys.exit(1)

print(
    f"[data-validate] OK manifests={len(paths)} pinned={len(pinned)} "
    f"schema={schema_path}"
)
PY
}

run_step "JSON Schema validation of committed manifests" check_manifests

DRY_RUN_DB="sqlite+pysqlite:///$TMP/index-dry-run.db"
dry_run_index() {
  uv run python pipelines/build_index.py \
    --snapshot "$SNAPSHOT" \
    --database-url "$DRY_RUN_DB" \
    --dry-run | tee "$TMP/index.txt"
}

run_step "build_index.py --dry-run (no database writes)" dry_run_index

COUNTS="$(sed -n 's/.*dry-run //p' "$TMP/index.txt" | tail -n 1)"
[ -n "$COUNTS" ] || fail "could not parse index counts from the dry-run output"

printf '\n[data-validate] SUMMARY %s\n' "$COUNTS"
printf '[data-validate] RESULT: PASS - %d steps in %ss (offline, no credentials)\n' \
  "$STEP" "$((SECONDS - TOTAL_START))"
