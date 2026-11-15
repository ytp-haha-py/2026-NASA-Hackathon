#!/usr/bin/env bash
# Issue #132 acceptance smoke.
#
# Offline (default):
#   Builds an isolated temporary SQLite index from the committed Earth/planetary
#   manifests, runs the Earth bbox route and a Moon on-demand planetary analysis
#   inline through TestClient, and verifies the key handoff fields. No network,
#   no shared database, no running server.
#
# Public:
#   bash scripts/issue132_acceptance.sh --public
#   Calls the deployed API read-only (health, Earth map bbox, planetary overlay).
#   It never POSTs an analysis job and never touches a local service.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

usage() {
  cat <<'EOF'
Usage: bash scripts/issue132_acceptance.sh [--offline|--public]

  --offline  (default) isolated TestClient smoke against committed manifests
  --public   read-only HTTP smoke against PUBLIC_BASE (network)
  --help     show this help

Environment:
  PUBLIC_BASE  public API base. Default: https://api.xn--2vxr09c.xn--kpry57d/api/v1
               (the punycode form of https://api.貓熊.台灣/api/v1)
EOF
}

MODE="offline"
case "${1:-}" in
  ""|--offline) MODE="offline" ;;
  --public) MODE="public" ;;
  --help|-h) usage; exit 0 ;;
  *) echo "[issue132] unknown argument: $1" >&2; usage >&2; exit 2 ;;
esac

if [[ "$MODE" == "offline" ]]; then
  TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/analogx-issue132.XXXXXX")"
  trap 'rm -rf "$TMP_DIR"' EXIT
  ISSUE132_ROOT="$ROOT" ISSUE132_TMP="$TMP_DIR" uv run python - <<'PY'
"""Offline Issue #132 route smoke.

Uses an isolated temporary SQLite DB and the committed manifests/fixtures. Never
starts uvicorn and never reads/writes ./var/analog.db or a shared service.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from analog_api.config import Settings
from analog_api.main import create_app
from fastapi.testclient import TestClient

ROOT = Path(os.environ["ISSUE132_ROOT"])
TMP = Path(os.environ["ISSUE132_TMP"])
os.environ["ANALOG_AOI_RUNTIME_DIR"] = str(TMP / "aoi-runtime")

SETTINGS = Settings(
    database_url=f"sqlite+pysqlite:///{TMP / 'issue132-acceptance.db'}",
    manifest_dir=str(ROOT / "pipelines/earth/manifests"),
    manifest_dir_planetary=str(ROOT / "pipelines/planetary/manifests"),
    manifest_dir_context=str(ROOT / "pipelines/worldcover/manifests"),
    seed_include_synthetic=False,
    seed_include_typed=True,
    aoi_inline=True,
    global_index_dir=str(ROOT / "pipelines/coarse/real-pilot/index"),
    planetary_allow_network=False,
)


def fail(message: str) -> None:
    raise SystemExit(f"[issue132] FAIL: {message}")


def check_json(response, path: str, *, statuses: tuple[int, ...] = (200,)):
    if response.status_code not in statuses:
        fail(
            f"{path} returned HTTP {response.status_code} (expected {statuses}).\n"
            f"{response.text[:1500]}"
        )
    try:
        return response.json()
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        fail(f"{path} returned non-JSON: {exc}: {response.text[:500]}")


print("[issue132] offline: isolated SQLite + committed manifests (no network)")
with TestClient(create_app(SETTINGS)) as client:
    health = check_json(client.get("/api/v1/health"), "/api/v1/health")
    if health.get("status") != "ok":
        fail(f"health is not ok: {health}")
    print(f"[issue132] health ok data_version={health.get('data_version')}")

    earth = check_json(
        client.get(
            "/api/v1/earth/map-features",
            params={"bbox": "-112.3,36.0,-112.0,36.3", "limit": 10},
        ),
        "/api/v1/earth/map-features",
    )
    if earth.get("body") != "Earth" or earth.get("bbox_crs") != "EPSG:4326":
        fail(f"Earth map response has the wrong body/CRS: {earth.get('body')} / {earth.get('bbox_crs')}")
    if earth.get("total", 0) <= 0:
        fail("Earth map response is empty for the committed Grand Canyon bbox")
    if not earth.get("features"):
        fail("Earth map response has total>0 but no features")
    first_feature = earth["features"][0]
    if not (first_feature.get("support_label") or first_feature.get("support")):
        fail(f"Earth map feature has neither support_label nor support: {sorted(first_feature)}")
    missing = sorted(
        {"dimension_status", "available_dimensions", "missing_dimensions", "detail_links"}
        - set(first_feature)
    )
    if missing:
        fail(f"Earth map feature is missing contract fields {missing}: {sorted(first_feature)}")
    if first_feature.get("bbox_crs") != "EPSG:4326":
        fail(f"Earth map feature bbox_crs is not EPSG:4326: {first_feature.get('bbox_crs')}")
    coarse = earth.get("coverage", {}).get("coarse", {})
    if coarse.get("status") != "ready":
        fail(f"coverage.coarse must be ready for the configured real-pilot index: {coarse}")
    print(
        "[issue132] Earth map ok total={} returned={} coarse_matched={} next_offset={}".format(
            earth.get("total"), earth.get("returned"), coarse.get("matched_count"), earth.get("next_offset")
        )
    )

    crossing = check_json(
        client.get("/api/v1/earth/map-features", params={"bbox": "170,-90,-170,90"}),
        "/api/v1/earth/map-features?bbox=antimeridian",
    )
    if crossing.get("bbox", {}).get("crosses_antimeridian") is not True:
        fail(f"antimeridian query did not report crosses_antimeridian=true: {crossing.get('bbox')}")
    print("[issue132] Earth antimeridian ok (explicit empty result allowed)")

    mars_overlay = check_json(
        client.get(
            "/api/v1/planetary/map-features",
            params={"body": "Mars", "bbox": "76,17,79,20", "limit": 10},
        ),
        "/api/v1/planetary/map-features?body=Mars",
    )
    if mars_overlay.get("body", {}).get("body") != "Mars":
        fail(f"planetary overlay body mismatch: {mars_overlay.get('body')}")
    print(f"[issue132] planetary Mars overlay ok total={mars_overlay.get('total')}")

    creates = check_json(
        client.post(
            "/api/v1/planetary/areas/analyze",
            json={
                "body": "Moon",
                "bbox": {"west": 19.85, "south": -10.15, "east": 20.15, "north": -9.85},
                "requested_features": [
                    "coarse_slope_mean_deg",
                    "coarse_slope_p90_deg",
                    "coarse_flat_fraction_lt5deg",
                ],
                "idempotency_key": "issue132-offline-smoke-moon",
            },
        ),
        "/api/v1/planetary/areas/analyze",
        statuses=(200, 202),
    )
    job_id = creates.get("job_id")
    if not job_id:
        fail(f"planetary analyze returned no job_id: {creates}")
    for _ in range(50):
        if creates.get("status") in {"completed", "partial", "failed"}:
            break
        time.sleep(0.2)
        creates = check_json(
            client.get(f"/api/v1/jobs/{job_id}"),
            f"/api/v1/jobs/{job_id}",
        )
    if creates.get("status") not in {"completed", "partial"}:
        fail(f"Moon on-demand analysis did not complete: {creates}")
    if creates.get("coordinate_frame") != "lunar_planetocentric":
        fail(f"Moon coordinate_frame is wrong: {creates.get('coordinate_frame')}")
    if "EPSG:4326" in json.dumps(creates):
        fail("Moon analysis response contains Earth EPSG:4326")
    features = (creates.get("result") or {}).get("features") or {}
    missing = {
        "coarse_slope_mean_deg",
        "coarse_slope_p90_deg",
        "coarse_flat_fraction_lt5deg",
    } - set(features)
    if missing:
        fail(f"Moon analysis is missing D01 features {sorted(missing)}: {features}")
    area_id = creates.get("area_id")
    if not area_id:
        fail(f"Moon analysis returned no area_id: {creates}")

    typed = check_json(
        client.get(f"/api/v1/typed/areas/{area_id}"),
        f"/api/v1/typed/areas/{area_id}",
    )
    if typed.get("body") != "Moon":
        fail(f"typed area body mismatch: {typed.get('body')}")
    if not any(o.get("dimension_code") == "D01" for o in typed.get("observations") or []):
        fail("typed Moon area has no D01 observations")

    overlay = check_json(
        client.get(
            "/api/v1/planetary/map-features",
            params={"body": "Moon", "bbox": "19.8,-10.2,20.2,-9.8", "limit": 200},
        ),
        "/api/v1/planetary/map-features?body=Moon",
    )
    if not any(
        feature.get("typed_area_id") == area_id
        or str(feature.get("feature_id", "")).endswith(area_id)
        for feature in overlay.get("features", [])
    ):
        fail(f"new Moon typed area {area_id} is not in the planetary overlay")
    print(
        "[issue132] Moon on-demand ok area_id={} status={} frame={}".format(
            area_id, creates.get("status"), creates.get("coordinate_frame")
        )
    )

    out_of_coverage = client.post(
        "/api/v1/planetary/areas/analyze",
        json={"body": "Mars", "bbox": {"west": 0, "south": 0, "east": 1, "north": 1}},
    )
    if out_of_coverage.status_code in {200, 202}:
        body = out_of_coverage.json()
        if body.get("status") != "failed" or body.get("area_id") is not None:
            fail(f"out-of-coverage did not fail explicitly: {out_of_coverage.text[:800]}")
    elif out_of_coverage.status_code != 422:
        fail(
            "out-of-coverage must be an explicit 422 or failed job; got "
            f"HTTP {out_of_coverage.status_code}: {out_of_coverage.text[:800]}"
        )
    print("[issue132] out-of-coverage is explicit (no fake feature)")

    global_search = check_json(
        client.post(
            "/api/v1/global/search",
            json={"target": {"coarse_slope_mean_deg": {"value": 10.0}}, "limit": 3},
        ),
        "/api/v1/global/search",
    )
    if not global_search.get("results"):
        fail(f"global search regressed: {global_search}")
    print("[issue132] global search still returns real coarse results")

print("[issue132] RESULT: PASS (offline; isolated temp DB, no network, no shared services)")
PY
  exit 0
fi

# ---------------------------------------------------------------------------
# Public read-only smoke.
# ---------------------------------------------------------------------------
PUBLIC_BASE="${PUBLIC_BASE:-https://api.xn--2vxr09c.xn--kpry57d/api/v1}"
PUBLIC_BASE="${PUBLIC_BASE%/}"
TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/analogx-issue132-public.XXXXXX")"
trap 'rm -rf "$TMP_DIR"' EXIT

echo "[issue132] public: read-only checks against ${PUBLIC_BASE}"
echo "[issue132] public: no POST, no local service, no shared DB"

fetch() {
  local name="$1"
  local url="$2"
  local out="$TMP_DIR/$name.json"
  local status
  status="$(curl -sS --max-time 25 -H 'Accept: application/json' -o "$out" -w '%{http_code}' "$url" || true)"
  if [[ "$status" != "200" ]]; then
    echo "[issue132] FAIL: ${name} ${url} -> HTTP ${status}" >&2
    head -c 800 "$out" >&2 || true
    echo >&2
    exit 1
  fi
  echo "[issue132] ${name}: HTTP ${status} ($(wc -c <"$out" | tr -d ' ') bytes)"
}

fetch health "${PUBLIC_BASE}/health"
fetch earth_map "${PUBLIC_BASE}/earth/map-features?bbox=-112.3,36.0,-112.0,36.3&limit=5"
fetch mars_overlay "${PUBLIC_BASE}/planetary/map-features?body=Mars&bbox=76,17,79,20&limit=5"

PUBLIC_TMP="$TMP_DIR" uv run python - <<'PY'
import json
import os
from pathlib import Path

root = Path(os.environ["PUBLIC_TMP"])


def load(name: str) -> dict:
    try:
        return json.loads((root / f"{name}.json").read_text(encoding="utf-8"))
    except Exception as exc:
        raise SystemExit(f"[issue132] FAIL: cannot parse {name}: {exc}")


health = load("health")
if health.get("status") != "ok":
    raise SystemExit(f"[issue132] FAIL: health status is {health.get('status')!r}")

earth = load("earth_map")
if earth.get("body") != "Earth":
    raise SystemExit(f"[issue132] FAIL: Earth map body is {earth.get('body')!r}")
if earth.get("bbox_crs") != "EPSG:4326":
    raise SystemExit(f"[issue132] FAIL: Earth map bbox_crs is {earth.get('bbox_crs')!r}")
if not isinstance(earth.get("features"), list):
    raise SystemExit("[issue132] FAIL: Earth map response has no features list")

mars = load("mars_overlay")
body = mars.get("body") or {}
if body.get("body") != "Mars":
    raise SystemExit(f"[issue132] FAIL: planetary overlay body is {body.get('body')!r}")
if not isinstance(mars.get("features"), list):
    raise SystemExit("[issue132] FAIL: planetary overlay has no features list")

print(
    "[issue132] public summary: health data_version={}, earth total={} returned={}, "
    "Mars overlay total={} returned={}".format(
        health.get("data_version"),
        earth.get("total"),
        earth.get("returned"),
        mars.get("total"),
        mars.get("returned"),
    )
)
print("[issue132] RESULT: PASS (public read-only; no POST)")
PY
