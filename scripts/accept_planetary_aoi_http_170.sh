#!/usr/bin/env bash
# Issue #170 Goal H (Integration & E2E QA): live-HTTP Moon/Mars AOI gate.
#
# This is the "real server" companion to the in-process pytest gate. It:
#
#   1. seeds an isolated SQLite database from the committed Earth/planetary manifests;
#   2. starts the real FastAPI app on loopback with planetary network reads disabled;
#   3. POSTs real committed Moon/Mars AOI requests over HTTP, polls the durable job,
#      checks typed persistence and the planetary overlay, and verifies the returned
#      D01 values against the committed per-cell manifest values;
#   4. exercises dispersed low/mid/high-latitude AOIs: the low-latitude committed
#      windows must produce real values; mid/high-latitude requests must produce an
#      explicit out_of_coverage failure with no fabricated values;
#   5. exercises oversized/too-wide/tiny/invalid, polar and antimeridian requests;
#   6. starts a second app over a deliberately damaged copy of the committed catalog
#      and proves source-integrity failures are explicit.
#
# Offline only: no raster download, no network, no R2, no shared ./var/analog.db.
# The Moon live bounded read is skipped with an explicit reason because
# ANALOG_PLANETARY_ALLOW_NETWORK=false. Current committed coverage is one 5 km coarse
# window per body; that is a bounded pilot, never global/exhaustive coverage.
#
# Usage:
#   bash scripts/accept_planetary_aoi_http_170.sh
#   ANALOG_PLANETARY_AOI_PORT=8147 bash scripts/accept_planetary_aoi_http_170.sh
#
# Exit status: 0 only when every check passes (or is an explicit documented skip).

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PORT="${ANALOG_PLANETARY_AOI_PORT:-8137}"
BROKEN_PORT="${ANALOG_PLANETARY_AOI_BROKEN_PORT:-$((PORT + 1))}"
BASE="http://127.0.0.1:${PORT}"
BROKEN_BASE="http://127.0.0.1:${BROKEN_PORT}"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/analog-planetary-aoi-http-170.XXXXXX")"
DB_PATH="$TMP/analog.db"
DB_URL="sqlite+pysqlite:///$DB_PATH"
BROKEN_ROOT="$TMP/broken-catalog"
MOON_BBOX_FILE="$TMP/moon-bbox.json"
SERVER_PIDS=()

cleanup() {
  local status=$?
  local pid log
  for pid in "${SERVER_PIDS[@]:-}"; do
    [ -n "$pid" ] || continue
    kill "$pid" 2>/dev/null || true
    for _ in $(seq 1 20); do
      kill -0 "$pid" 2>/dev/null || break
      sleep 0.1
    done
    kill -9 "$pid" 2>/dev/null || true
    wait "$pid" 2>/dev/null || true
  done
  if [ "$status" -ne 0 ]; then
    for log in "$TMP/uvicorn.log" "$TMP/uvicorn-broken.log"; do
      if [ -f "$log" ]; then
        printf '\n[planetary-http-170] --- %s (last 40 lines) ---\n' "$log" >&2
        tail -n 40 "$log" >&2 || true
      fi
    done
  fi
  rm -rf "$TMP"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

fail() {
  printf '\n[planetary-http-170] ERROR: %s\n' "$*" >&2
  exit 1
}

for command in uv curl; do
  command -v "$command" >/dev/null 2>&1 || fail "required command '$command' is not on PATH"
done

for port in "$PORT" "$BROKEN_PORT"; do
  if curl -sS --max-time 1 -o /dev/null "http://127.0.0.1:${port}/api/v1/health" 2>/dev/null; then
    fail "port ${port} is already serving HTTP; stop it or set ANALOG_PLANETARY_AOI_PORT"
  fi
done

start_api() {
  local port="$1" log="$2" coarse_dir="${3:-}" pid ready=0
  (
    export ANALOG_DATABASE_URL="$DB_URL"
    export ANALOG_MANIFEST_DIR="$ROOT/pipelines/earth/manifests"
    export ANALOG_MANIFEST_DIR_PLANETARY="$ROOT/pipelines/planetary/manifests"
    export ANALOG_MANIFEST_DIR_CONTEXT="$ROOT/pipelines/worldcover/manifests"
    export ANALOG_SEED_INCLUDE_SYNTHETIC=false
    export ANALOG_SEED_INCLUDE_TYPED=false
    export ANALOG_AOI_INLINE=false
    export ANALOG_AOI_WORKERS=2
    export ANALOG_PLANETARY_ALLOW_NETWORK=false
    export ANALOG_AOI_RUNTIME_DIR="$TMP/aoi-runtime-${port}"
    if [ -n "$coarse_dir" ]; then
      export ANALOG_PLANETARY_COARSE_DIR="$coarse_dir"
    fi
    exec uv run uvicorn analog_api.main:create_app --factory \
      --host 127.0.0.1 --port "$port" --log-level warning
  ) >"$log" 2>&1 &
  pid=$!
  SERVER_PIDS+=("$pid")
  printf '[planetary-http-170] starting API on 127.0.0.1:%s (pid %s)\n' "$port" "$pid"
  for _ in $(seq 1 100); do
    if ! kill -0 "$pid" 2>/dev/null; then
      fail "API on port ${port} exited before becoming ready"
    fi
    if curl -fsS --max-time 1 "http://127.0.0.1:${port}/api/v1/health" -o /dev/null 2>/dev/null; then
      ready=1
      break
    fi
    sleep 0.25
  done
  [ "$ready" -eq 1 ] || fail "API on port ${port} did not become ready within 25s"
  printf '[planetary-http-170] API ready on http://127.0.0.1:%s\n' "$port"
}

printf '[planetary-http-170] offline live-HTTP planetary AOI gate (#170 Goal H)\n'
printf '[planetary-http-170] isolated database: %s\n' "$DB_PATH"
printf '[planetary-http-170] committed manifests only; no network, no R2, no shared DB\n'

uv run python -m analog_api.cli seed \
  --database-url "$DB_URL" \
  --manifest-dir "$ROOT/pipelines/earth/manifests" \
  --manifest-dir-planetary "$ROOT/pipelines/planetary/manifests" \
  --manifest-dir-context "$ROOT/pipelines/worldcover/manifests"

start_api "$PORT" "$TMP/uvicorn.log"

PLANETARY_AOI_ROOT="$ROOT" \
  PLANETARY_AOI_BASE="$BASE" \
  PLANETARY_AOI_MOON_BBOX_FILE="$MOON_BBOX_FILE" \
  uv run python - <<'PY'
"""HTTP end-to-end checks against the seeded local API."""

from __future__ import annotations

import json
import math
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(os.environ["PLANETARY_AOI_ROOT"])
BASE = os.environ["PLANETARY_AOI_BASE"]
MOON_BBOX_FILE = Path(os.environ["PLANETARY_AOI_MOON_BBOX_FILE"])
COARSE = ROOT / "pipelines/planetary/coarse"
FEATURE_IDS = {
    "coarse_slope_mean_deg",
    "coarse_slope_p90_deg",
    "coarse_flat_fraction_lt5deg",
}
FRAMES = {"Moon": "lunar_planetocentric", "Mars": "mars_planetocentric"}
RADII_M = {"Moon": 1_737_400.0, "Mars": 3_396_190.0}
PASSED = 0
FAILURES: list[tuple[str, str]] = []


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def http(
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    *,
    timeout: float = 30.0,
) -> tuple[int, Any]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        BASE + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = response.status
            raw = response.read()
    except urllib.error.HTTPError as exc:
        status = exc.code
        raw = exc.read()
    return status, (json.loads(raw) if raw else None)


def analysis_windows() -> dict[str, dict[str, Any]]:
    """Committed analysis-eligible 250 m windows, derived from committed manifests."""
    index = load(COARSE / "index.json")
    windows: dict[str, dict[str, Any]] = {}
    for pilot in index["pilots"]:
        manifest = load(ROOT / pilot["manifest"])
        source = manifest["source"]
        region_id = str(manifest["region"]["region_id"])
        if "evidence" in region_id.lower() or source.get("same_support_as_earth") is not True:
            continue
        cells = [
            {
                "candidate_id": str(cell["candidate_id"]),
                "bbox": {
                    key: float(cell["bbox_body_lonlat"][key])
                    for key in ("west", "south", "east", "north")
                },
                "features": {
                    feature_id: float(value)
                    for feature_id, value in (cell.get("features") or {}).items()
                },
            }
            for cell in manifest["cells"]
            if math.isclose(float(cell["analysis_resolution_m"]), 250.0)
        ]
        if not cells:
            continue
        windows[source["body"]] = {
            "bbox": {
                "west": min(cell["bbox"]["west"] for cell in cells),
                "south": min(cell["bbox"]["south"] for cell in cells),
                "east": max(cell["bbox"]["east"] for cell in cells),
                "north": max(cell["bbox"]["north"] for cell in cells),
            },
            "cells": cells,
            "source_id": source["source_id"],
            "region_id": region_id,
        }
    return windows


WINDOWS = analysis_windows()


def bbox_at(body: str, lat: float, lon: float, side_km: float = 6.0) -> dict[str, float]:
    """A planetocentric bbox of the requested metric side around a real coordinate."""
    metres_per_degree = math.pi * RADII_M[body] / 180.0
    half_lat = (side_km * 1000.0 / 2.0) / metres_per_degree
    half_lon = (side_km * 1000.0 / 2.0) / (
        metres_per_degree * max(math.cos(math.radians(lat)), 1e-6)
    )
    return {
        "west": round(lon - half_lon, 6),
        "south": round(lat - half_lat, 6),
        "east": round(lon + half_lon, 6),
        "north": round(lat + half_lat, 6),
    }


def analyze(body: str, bbox: dict[str, float]) -> tuple[int, Any]:
    return http("POST", "/api/v1/planetary/areas/analyze", {"body": body, "bbox": bbox})


def wait_job(job_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        status, job = http("GET", f"/api/v1/jobs/{job_id}")
        if status != 200:
            raise AssertionError(f"GET /jobs/{job_id} returned HTTP {status}: {job}")
        if job["status"] in {"completed", "partial", "failed"}:
            return job
        time.sleep(0.1)
    raise AssertionError(f"job {job_id} did not reach a terminal status within 30s")


def terminal(payload: dict[str, Any]) -> dict[str, Any]:
    """Return the terminal job payload (the real API answers 202 while queued)."""
    if payload.get("status") in {"completed", "partial", "failed"}:
        return payload
    return wait_job(payload["job_id"])


def assert_no_features(payload: dict[str, Any], label: str) -> None:
    if payload.get("result") is not None:
        raise AssertionError(f"{label}: failed/no-data response still has a result payload")
    text = json.dumps(payload)
    fabricated = sorted(feature_id for feature_id in FEATURE_IDS if feature_id in text)
    if fabricated:
        raise AssertionError(f"{label}: fabricated D01 feature ids in response: {fabricated}")


def assert_completed(payload: dict[str, Any], body: str, *, expected_cells: int) -> dict[str, Any]:
    if payload.get("status") != "completed":
        raise AssertionError(f"{body}: status is {payload.get('status')!r}, expected completed")
    result = payload.get("result")
    if not isinstance(result, dict):
        raise AssertionError(f"{body}: completed response has no result object")
    if result.get("body") != body or payload.get("body") != body:
        raise AssertionError(f"{body}: result/body mismatch")
    if result.get("coordinate_frame") != FRAMES[body]:
        raise AssertionError(f"{body}: coordinate_frame is {result.get('coordinate_frame')!r}")
    if result.get("analysis_kind") != "committed_reference_fixture":
        raise AssertionError(f"{body}: unexpected analysis_kind {result.get('analysis_kind')!r}")
    features = result.get("features") or {}
    if set(features) != FEATURE_IDS:
        raise AssertionError(f"{body}: feature ids are {sorted(features)}")
    for feature_id, value in features.items():
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
            raise AssertionError(f"{body}: {feature_id} is not a finite number: {value!r}")
    if not 0.0 <= float(features["coarse_slope_mean_deg"]) <= 90.0:
        raise AssertionError(f"{body}: slope mean out of physical range")
    if not 0.0 <= float(features["coarse_slope_p90_deg"]) <= 90.0:
        raise AssertionError(f"{body}: slope p90 out of physical range")
    if not 0.0 <= float(features["coarse_flat_fraction_lt5deg"]) <= 1.0:
        raise AssertionError(f"{body}: flat fraction out of [0,1]")
    support = result.get("dimension_support") or {}
    if (support.get("D01") or {}).get("status") != "present":
        raise AssertionError(f"{body}: D01 is not present")
    if set(support) != {f"D0{index}" for index in range(1, 7)}:
        raise AssertionError(f"{body}: dimension_support keys are {sorted(support)}")
    for code in ("D02", "D03", "D04", "D05", "D06"):
        entry = support[code]
        if entry.get("status") != "unsupported_for_on_demand_analysis":
            raise AssertionError(f"{body}: {code} is not explicitly unsupported: {entry}")
        if entry.get("value") is not None:
            raise AssertionError(f"{body}: {code} carries a fabricated value: {entry.get('value')!r}")
        if not entry.get("reason"):
            raise AssertionError(f"{body}: {code} is unsupported without a reason")
    providers = payload.get("provider_status") or {}
    if not providers:
        raise AssertionError(f"{body}: no provider_status")
    for source_id, status in providers.items():
        if status.get("status") != "verified_committed_fixture":
            raise AssertionError(f"{body}: {source_id} status {status.get('status')!r}")
        if status.get("network_used") is not False:
            raise AssertionError(f"{body}: {source_id} used the network")
        if status.get("fixture_checksums_verified") is not True:
            raise AssertionError(f"{body}: {source_id} checksums were not verified")
        if int(status.get("cells") or 0) != expected_cells:
            raise AssertionError(
                f"{body}: {source_id} reports {status.get('cells')} cells, expected {expected_cells}"
            )
    if "4326" in json.dumps(payload):
        raise AssertionError(f"{body}: response mentions Earth EPSG:4326")
    return result


def check(name: str, function) -> None:
    global PASSED
    try:
        detail = function()
    except Exception as exc:  # noqa: BLE001 - every violation is reported
        FAILURES.append((name, str(exc)))
        print(f"[planetary-http-170] FAIL {name}: {exc}")
    else:
        PASSED += 1
        suffix = f" ({detail})" if detail else ""
        print(f"[planetary-http-170] PASS {name}{suffix}")


def persistence_checks(body: str, area_id: str, bbox: dict[str, float]) -> None:
    typed_status, typed = http("GET", f"/api/v1/typed/areas/{area_id}")
    if typed_status != 200:
        raise AssertionError(f"{body}: typed area HTTP {typed_status}: {typed}")
    if typed.get("body") != body or typed.get("coordinate_frame") != FRAMES[body]:
        raise AssertionError(f"{body}: typed area body/frame mismatch: {typed.get('body')}")
    rows = typed.get("observations") or []
    if {row.get("feature_id") for row in rows} != FEATURE_IDS:
        raise AssertionError(f"{body}: typed feature ids are {sorted(row.get('feature_id') for row in rows)}")
    if any(row.get("dimension_code") != "D01" for row in rows):
        raise AssertionError(f"{body}: typed non-D01 observation persisted")
    if any(row.get("score_role") != "evidence_only" for row in rows):
        raise AssertionError(f"{body}: planetary observations were scored")
    if any("not_earth_wgs84" not in (row.get("quality_flags") or []) for row in rows):
        raise AssertionError(f"{body}: 'not_earth_wgs84' quality flag missing")
    if "4326" in str(typed.get("crs") or ""):
        raise AssertionError(f"{body}: typed area CRS mentions Earth EPSG:4326")

    area_status, area = http("GET", f"/api/v1/planetary/areas/{area_id}")
    if area_status != 200 or area.get("body") != body:
        raise AssertionError(f"{body}: planetary area HTTP {area_status}")

    overlay_bbox = ",".join(str(round(bbox[key], 6)) for key in ("west", "south", "east", "north"))
    overlay_status, overlay = http(
        "GET", f"/api/v1/planetary/map-features?body={body}&bbox={overlay_bbox}&limit=200"
    )
    if overlay_status != 200:
        raise AssertionError(f"{body}: overlay HTTP {overlay_status}: {overlay}")
    if not any(
        feature.get("typed_area_id") == area_id for feature in overlay.get("features") or []
    ):
        raise AssertionError(f"{body}: {area_id} missing from the planetary overlay")


def covered_cell_case(body: str, cell: dict[str, Any]) -> str:
    bbox = cell["bbox"]
    status, payload = analyze(body, bbox)
    if status not in {200, 202}:
        raise AssertionError(f"{body}: HTTP {status}: {payload}")
    if payload["cached"] is not False:
        raise AssertionError(f"{body}: first request reported cached=true")
    job = terminal(payload)
    result = assert_completed(job, body, expected_cells=1)
    for feature_id, expected in cell["features"].items():
        observed = float(result["features"][feature_id])
        if not math.isclose(observed, expected, rel_tol=0.0, abs_tol=1e-9):
            raise AssertionError(
                f"{body} {cell['candidate_id']}: {feature_id}={observed} != committed {expected}"
            )
    area_id = job["area_id"]
    if not area_id.startswith(f"planet-aoi-{body.lower()}-"):
        raise AssertionError(f"{body}: unexpected area_id {area_id}")
    persistence_checks(body, area_id, bbox)

    cached_status, cached = analyze(body, bbox)
    if cached_status != 200 or cached.get("cached") is not True:
        raise AssertionError(f"{body}: repeated bbox was not served from cache")
    if cached.get("job_id") != job["job_id"]:
        raise AssertionError(f"{body}: cached request created a new job")
    assert_completed(cached, body, expected_cells=1)
    if cached["result"]["features"] != result["features"]:
        raise AssertionError(f"{body}: cached features differ")
    values = result["features"]
    return (
        f"{cell['candidate_id']} slope_mean={values['coarse_slope_mean_deg']:.3f} "
        f"p90={values['coarse_slope_p90_deg']:.3f} "
        f"flat={values['coarse_flat_fraction_lt5deg']:.3f}"
    )


def covered_block_case(body: str, window: dict[str, Any]) -> str:
    bbox = window["bbox"]
    status, payload = analyze(body, bbox)
    if status not in {200, 202}:
        raise AssertionError(f"{body}: HTTP {status}: {payload}")
    job = terminal(payload)
    result = assert_completed(job, body, expected_cells=len(window["cells"]))
    persistence_checks(body, job["area_id"], bbox)
    values = result["features"]
    return (
        f"cells={len(window['cells'])} "
        f"slope_mean={values['coarse_slope_mean_deg']:.3f} "
        f"p90={values['coarse_slope_p90_deg']:.3f} "
        f"flat={values['coarse_flat_fraction_lt5deg']:.3f}"
    )


def no_data_case(body: str, lat: float, lon: float) -> str:
    bbox = bbox_at(body, lat, lon)
    status, payload = analyze(body, bbox)
    if status == 200:
        raise AssertionError(f"{body}@{lat},{lon}: no-data request unexpectedly returned 200")
    if status == 422:
        error = payload.get("error") or {}
        if error.get("code") != "out_of_coverage":
            raise AssertionError(f"{body}@{lat},{lon}: error is {error.get('code')!r}")
        if "No committed analysis-eligible" not in error.get("message", ""):
            raise AssertionError(f"{body}@{lat},{lon}: message lacks the committed-coverage note")
        assert_no_features(payload, f"{body}@{lat},{lon}")
        return f"{lat},{lon} -> HTTP 422 out_of_coverage"
    if status != 202:
        raise AssertionError(f"{body}@{lat},{lon}: HTTP {status}: {payload}")
    job = terminal(payload)
    if job.get("status") != "failed":
        raise AssertionError(f"{body}@{lat},{lon}: job status {job.get('status')!r}")
    if (job.get("error") or {}).get("code") != "out_of_coverage":
        raise AssertionError(f"{body}@{lat},{lon}: job error {job.get('error')!r}")
    assert_no_features(job, f"{body}@{lat},{lon} job")
    if body == "Moon" and "allow_remote=True" not in (job["error"].get("message") or ""):
        raise AssertionError("Moon no-data does not document the disabled live-read path")
    return f"{lat},{lon} -> failed out_of_coverage (no values)"


def antimeridian_case(body: str) -> str:
    bbox = {"west": 179.9, "south": 0.0, "east": -179.9, "north": 0.1}
    status, payload = analyze(body, bbox)
    if status != 202:
        raise AssertionError(f"{body}: crossing bbox was rejected: HTTP {status}: {payload}")
    if payload.get("coordinate_frame") != FRAMES[body]:
        raise AssertionError(f"{body}: crossing coordinate_frame mismatch")
    link = (payload.get("links") or {}).get("map_features", "")
    if "179.9" not in link or "-179.9" not in link:
        raise AssertionError(f"{body}: crossing bbox was not normalized: {link}")
    job = terminal(payload)
    if job.get("status") != "failed" or (job.get("error") or {}).get("code") != "out_of_coverage":
        raise AssertionError(f"{body}: crossing bbox did not fail explicitly: {job.get('status')}")
    assert_no_features(job, f"{body} antimeridian")
    return "crossing bbox accepted, normalized, then explicit no-data"


def polar_case(body: str, lat: float) -> str:
    bbox = bbox_at(body, lat, 0.0, side_km=2.0)
    status, payload = analyze(body, bbox)
    if status != 202:
        raise AssertionError(f"{body}@{lat}: polar bbox was rejected: HTTP {status}: {payload}")
    job = terminal(payload)
    if job.get("status") != "failed" or (job.get("error") or {}).get("code") != "out_of_coverage":
        raise AssertionError(f"{body}@{lat}: polar bbox did not fail explicitly")
    assert_no_features(job, f"{body}@{lat} polar")
    return f"{lat} polar bbox accepted, then explicit no-data"


def rejected_case(body: str, bbox: dict[str, float], code: str) -> str:
    status, payload = analyze(body, bbox)
    if status != 422:
        raise AssertionError(f"{body}: expected HTTP 422, got {status}: {payload}")
    if (payload.get("error") or {}).get("code") != code:
        raise AssertionError(f"{body}: error {payload.get('error')!r}, expected {code}")
    return f"{code} at HTTP 422"


NO_DATA_POINTS = {
    "Moon": ((45.0, -30.0), (82.0, 120.0)),
    "Mars": ((-45.0, 150.0), (78.0, -60.0)),
}

for body in ("Moon", "Mars"):
    window = WINDOWS.get(body)
    if window is None:
        FAILURES.append((f"{body.lower()} committed window", "no analysis-eligible cells"))
        print(f"[planetary-http-170] FAIL {body}: no committed analysis-eligible window")
        continue
    for index, cell in enumerate(window["cells"][:3], start=1):
        check(
            f"{body.lower()} committed cell {index}/{len(window['cells'])} real AOI",
            lambda body=body, cell=cell: covered_cell_case(body, cell),
        )
    check(
        f"{body.lower()} full committed block",
        lambda body=body, window=window: covered_block_case(body, window),
    )
    for lat, lon in NO_DATA_POINTS[body]:
        check(
            f"{body.lower()} dispersed no-data at {lat:+.1f},{lon:+.1f}",
            lambda body=body, lat=lat, lon=lon: no_data_case(body, lat, lon),
        )

check("moon no-data at prime meridian/equator", lambda: no_data_case("Moon", 0.0, 0.0))
check("mars no-data at prime meridian/equator", lambda: no_data_case("Mars", 0.0, 0.0))
check("moon antimeridian bbox", lambda: antimeridian_case("Moon"))
check("mars antimeridian bbox", lambda: antimeridian_case("Mars"))
check("moon north polar bbox", lambda: polar_case("Moon", 89.5))
check("mars south polar bbox", lambda: polar_case("Mars", -89.5))

check(
    "oversized AOI rejected",
    lambda: rejected_case(
        "Moon", {"west": 0.0, "south": 0.0, "east": 10.0, "north": 10.0}, "aoi_too_large"
    ),
)
check(
    "bbox wider than 180 degrees rejected",
    lambda: rejected_case(
        "Moon", {"west": -170.0, "south": 0.0, "east": 170.0, "north": 0.1}, "bbox_too_wide"
    ),
)
check(
    "sub-kilometre AOI rejected",
    lambda: rejected_case(
        "Moon", {"west": 0.0, "south": 0.0, "east": 0.001, "north": 0.001}, "aoi_too_small"
    ),
)
check(
    "latitude beyond the pole rejected",
    lambda: rejected_case(
        "Moon", {"west": 0.0, "south": 90.5, "east": 0.1, "north": 95.0}, "invalid_bbox"
    ),
)

print(
    "[planetary-http-170] SKIP moon live_bounded_read: "
    "ANALOG_PLANETARY_ALLOW_NETWORK=false (offline gate); out_of_coverage confirmed"
)

if "Moon" in WINDOWS:
    MOON_BBOX_FILE.write_text(json.dumps(WINDOWS["Moon"]["bbox"]), encoding="utf-8")

print(
    f"[planetary-http-170] primary API checks: {PASSED} passed, {len(FAILURES)} failed"
)
if FAILURES:
    print(f"[planetary-http-170] RESULT: FAIL ({len(FAILURES)} violation(s))")
    raise SystemExit(1)
PY

printf '\n[planetary-http-170] preparing damaged catalog copy for the source-failure check\n'
mkdir -p "$BROKEN_ROOT/pipelines/planetary" "$BROKEN_ROOT/samples/planetary/coarse"
cp -R "$ROOT/pipelines/planetary/coarse" "$BROKEN_ROOT/pipelines/planetary/coarse"
cp -R "$ROOT/samples/planetary/coarse/fixtures" "$BROKEN_ROOT/samples/planetary/coarse/fixtures"
BROKEN_FIXTURE="$(
  uv run python -c "import json; m=json.load(open('pipelines/planetary/coarse/moon_lola128_window01.json')); print(m['cells'][0]['fixture']['path'].rsplit('/', 1)[-1])"
)"
rm -f "$BROKEN_ROOT/samples/planetary/coarse/fixtures/$BROKEN_FIXTURE"
printf '[planetary-http-170] removed committed fixture %s from the copy\n' "$BROKEN_FIXTURE"

start_api "$BROKEN_PORT" "$TMP/uvicorn-broken.log" \
  "$BROKEN_ROOT/pipelines/planetary/coarse"

PLANETARY_AOI_BROKEN_BASE="$BROKEN_BASE" \
  PLANETARY_AOI_MOON_BBOX_FILE="$MOON_BBOX_FILE" \
  uv run python - <<'PY'
"""Source-integrity failure must be explicit and must never fabricate values."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

BASE = os.environ["PLANETARY_AOI_BROKEN_BASE"]
BBOX = json.loads(Path(os.environ["PLANETARY_AOI_MOON_BBOX_FILE"]).read_text(encoding="utf-8"))
request = urllib.request.Request(
    BASE + "/api/v1/planetary/areas/analyze",
    data=json.dumps({"body": "Moon", "bbox": BBOX}).encode("utf-8"),
    method="POST",
    headers={"Content-Type": "application/json", "Accept": "application/json"},
)
try:
    with urllib.request.urlopen(request, timeout=30) as response:
        status = response.status
        payload = json.loads(response.read())
except urllib.error.HTTPError as exc:
    status = exc.code
    payload = json.loads(exc.read())

assert status == 422, f"damaged source returned HTTP {status}: {payload}"
error = payload.get("error") or {}
assert error.get("code") in {
    "fixture_missing",
    "fixture_checksum_mismatch",
    "fixture_size_mismatch",
}, error
assert "fixture" in error.get("message", "").lower(), error
text = json.dumps(payload)
for feature_id in (
    "coarse_slope_mean_deg",
    "coarse_slope_p90_deg",
    "coarse_flat_fraction_lt5deg",
):
    assert feature_id not in text, f"source failure fabricated {feature_id}"
print(
    "[planetary-http-170] PASS source-integrity failure is explicit "
    f"({error['code']}); no values fabricated"
)
PY

printf '\n[planetary-http-170] RESULT: PASS (offline live HTTP; committed manifests only)\n'
