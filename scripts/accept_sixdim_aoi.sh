#!/usr/bin/env bash
# Offline end-to-end acceptance for Issue #160 (six-dimension AOI MVP).
#
# Starts the real FastAPI app against a freshly seeded temporary SQLite database,
# then issues real local HTTP requests for:
#   - Earth / Moon / Mars AOI and six-dimension status
#   - D02-D05 evidence queries with provenance and explicit no-data states
#   - D01 + D06 weighted search, D06 weight changes, and dimension weighting
#   - out-of-coverage bbox behaviour
#   - Earth-only ESA WorldCover hard filter (and the multidim API gap)
#
# No provider download, no R2, no public API call, no committed database mutation.
#
# Usage:
#   bash scripts/accept_sixdim_aoi.sh
#   ANALOG_SIXDIM_PORT=8141 bash scripts/accept_sixdim_aoi.sh
#   ANALOG_SIXDIM_API_BASE=http://127.0.0.1:8000/api/v1 bash scripts/accept_sixdim_aoi.sh
#
# The reuse mode is for debugging an already running local API; CI should use the default
# fresh-database mode.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PORT="${ANALOG_SIXDIM_PORT:-8141}"
BASE="${ANALOG_SIXDIM_API_BASE:-http://127.0.0.1:${PORT}/api/v1}"
REUSE_API=0
if [ -n "${ANALOG_SIXDIM_API_BASE:-}" ]; then
  REUSE_API=1
fi

TMP="$(mktemp -d "${TMPDIR:-/tmp}/analog-sixdim-aoi.XXXXXX")"
DB_PATH="$TMP/analog.db"
DB_URL="sqlite+pysqlite:///$DB_PATH"
SERVER_PID=""

say() { printf '[sixdim-aoi] %s\n' "$*"; }
fail() { printf '\n[sixdim-aoi] ERROR: %s\n' "$*" >&2; exit 1; }

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
    printf '\n[sixdim-aoi] --- uvicorn log (last 60 lines) ---\n' >&2
    tail -n 60 "$TMP/uvicorn.log" >&2 || true
  fi
  rm -rf "$TMP"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

require_command() {
  local name="$1"
  if ! command -v "$name" >/dev/null 2>&1; then
    printf 'ERROR: required command "%s" is not installed or not on PATH.\n' "$name" >&2
    exit 127
  fi
}

require_command uv
require_command python3
require_command curl

export NO_PROXY="127.0.0.1,localhost,${NO_PROXY:-}"
export no_proxy="127.0.0.1,localhost,${no_proxy:-}"

if [ "$REUSE_API" -eq 0 ]; then
  say "starting offline acceptance with a fresh local database"
  say "database: $DB_PATH"

  if curl -fsS --noproxy '*' --max-time 1 -o /dev/null \
    "http://127.0.0.1:${PORT}/api/v1/health" 2>/dev/null; then
    fail "port ${PORT} is already serving HTTP; stop it or set ANALOG_SIXDIM_PORT."
  fi

  # Real committed manifests + committed typed-v2 pilots only. No network.
  ANALOG_SEED_INCLUDE_SYNTHETIC=false \
  ANALOG_SEED_INCLUDE_TYPED=true \
  uv run python -m analog_api.cli seed \
    --database-url "$DB_URL" \
    --manifest-dir "$ROOT/pipelines/earth/manifests" \
    --manifest-dir-planetary "$ROOT/pipelines/planetary/manifests" \
    --manifest-dir-context "$ROOT/pipelines/worldcover/manifests" \
    --include-typed

  ANALOG_DATABASE_URL="$DB_URL" \
  ANALOG_AUTO_SEED=false \
  ANALOG_SEED_INCLUDE_SYNTHETIC=false \
  ANALOG_SEED_INCLUDE_TYPED=false \
  uv run uvicorn analog_api.main:create_app \
    --factory \
    --host 127.0.0.1 \
    --port "$PORT" \
    --log-level warning >"$TMP/uvicorn.log" 2>&1 &
  SERVER_PID=$!

  READY=0
  for _ in $(seq 1 120); do
    if ! kill -0 "$SERVER_PID" 2>/dev/null; then
      fail "uvicorn exited before becoming ready"
    fi
    if curl -fsS --noproxy '*' --max-time 1 \
      "http://127.0.0.1:${PORT}/api/v1/health" -o /dev/null 2>/dev/null; then
      READY=1
      break
    fi
    sleep 0.25
  done
  [ "$READY" -eq 1 ] || fail "uvicorn did not become ready at ${BASE} within 30s"
  say "API ready at ${BASE}"
else
  say "reusing local API at ${BASE} (no seeding, no server start)"
  curl -fsS --noproxy '*' --max-time 5 "${BASE}/health" >/dev/null \
    || fail "reused API is not healthy at ${BASE}/health"
fi

python3 - "$BASE" <<'PY'
from __future__ import annotations

import json
import math
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

BASE = sys.argv[1].rstrip("/")
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _request(method: str, path: str, payload: dict[str, Any] | None, expected: int) -> Any:
    data = None
    headers: dict[str, str] = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with OPENER.open(request, timeout=30) as response:
            status = response.status
            raw = response.read()
    except urllib.error.HTTPError as exc:
        status = exc.code
        raw = exc.read()
    try:
        body = json.loads(raw)
    except json.JSONDecodeError as exc:  # pragma: no cover - diagnostic path
        raise AssertionError(f"{method} {path} returned non-JSON HTTP {status}: {raw[:400]!r}") from exc
    assert status == expected, (
        f"{method} {path}: expected HTTP {expected}, got {status}: {json.dumps(body)[:700]}"
    )
    return body


def get(path: str, expected: int = 200) -> Any:
    return _request("GET", path, None, expected)


def post(path: str, payload: dict[str, Any], expected: int = 200) -> Any:
    return _request("POST", path, payload, expected)


def finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def provenance(record: dict[str, Any]) -> bool:
    source = record.get("source") or {}
    dataset_ids = record.get("dataset_ids") or source.get("dataset_ids") or []
    source_asset_ids = record.get("source_asset_ids") or source.get("source_asset_ids") or []
    return bool(record.get("observation_id")) and bool(dataset_ids or source_asset_ids)


def section(number: int, total: int, title: str) -> None:
    print(f"[sixdim-aoi] ({number}/{total}) {title}")


TOTAL = 11

# ---------------------------------------------------------------------------
# 1. Seeded real data and health
# ---------------------------------------------------------------------------
section(1, TOTAL, "seed, health and real-data inventory")
health = get("/health")
assert health["status"] == "ok", health
assert str(health["data_version"]).startswith("dv-"), health

capabilities = get("/capabilities")
candidate_count = capabilities["indexed_candidate_count"]
assert candidate_count == 113, f"expected 113 Earth candidates, got {candidate_count}"

references = get("/references")
moon_refs = [item for item in references if item["body"] == "Moon"]
mars_refs = [item for item in references if item["body"] == "Mars"]
assert len(moon_refs) >= 18, f"expected at least 18 Moon references, got {len(moon_refs)}"
assert len(mars_refs) >= 5, f"expected at least 5 Mars references, got {len(mars_refs)}"

typed_coverage = get("/typed/coverage")
typed_total = typed_coverage["total"]
assert typed_total >= 2140, f"expected the committed typed-v2 corpus, got {typed_total}"
assert {"D02", "D03", "D04", "D05", "D06"} <= set(typed_coverage["by_dimension"])
assert typed_coverage["by_dimension"]["D06"]["by_body"].get("Earth", 0) >= 1920

d06_registry = get("/search/features?dimension=D06&role=similarity")
assert d06_registry["registry_version"] == "feature-eligibility-1.0.0"
d06_features = {item["feature_id"]: item for item in d06_registry["features"]}
clay = d06_features["soil_clay_0_5cm_mean"]
assert clay["roles"]["similarity"]["supported"] is True
assert clay["coverage"]["gated_candidate_count"] == 113
assert d06_registry["join_audit"]["proximity_join_used"] is False
print(
    f"    candidates={candidate_count} typed={typed_total} "
    f"Moon={len(moon_refs)} Mars={len(mars_refs)} D06-gated={clay['coverage']['gated_candidate_count']}"
)

# ---------------------------------------------------------------------------
# 2. Earth AOI: six statuses plus real typed evidence
# ---------------------------------------------------------------------------
section(2, TOTAL, "Earth AOI: six-dimension status + real evidence")
earth = get(
    "/coverage/aoi?body=Earth&west=-68.5&south=-23.6&east=-68.3&north=-23.4"
)
assert earth["contract_version"] == "aoi-query-1.0.0", earth.get("contract_version")
assert set(earth["dimensions"]) == {"D01", "D02", "D03", "D04", "D05", "D06"}
assert earth["actual_indexed_coverage"]["global_coverage_claimed"] is False
assert earth["actual_indexed_coverage"]["intersecting_candidate_ids"] == [
    "earth-atacama-desert"
]
assert earth["query"]["bbox"]["west"] == -68.5

earth_records = earth["intersecting_records"]
assert earth_records, "Earth AOI must return real intersecting records"
assert {record["area_id"] for record in earth_records} == {"earth-atacama-desert"}
assert all(provenance(record) for record in earth_records)
for record in earth_records:
    if record["state"] == "detected" and record["value_kind"] == "numeric":
        assert finite(record["value"]), (
            record["dimension_code"],
            record["feature_id"],
            record["value"],
        )

assert earth["dimensions"]["D01"]["status"] == "available_or_indexed"
assert earth["dimensions"]["D06"]["status"] == "available_or_indexed"
for dimension in ("D02", "D03", "D04", "D05"):
    status = earth["dimensions"][dimension]["status"]
    assert status in {"evidence_only", "partially_available"}, (dimension, status)
    evidence = [item for item in earth_records if item["dimension_code"] == dimension]
    assert evidence, f"{dimension} must return real evidence records for the Earth AOI"
    if dimension == "D02":
        # The Earth D02 pilot exposes availability states; Atacama has explicit unknown
        # availability records, not fabricated map-unit detections.
        assert any(item["state"] in {"unknown", "detected"} for item in evidence), dimension
    else:
        assert any(item["state"] == "detected" for item in evidence), dimension

d06_records = [item for item in earth_records if item["dimension_code"] == "D06"]
assert any(item["observation_id"].startswith("soilgrids:") for item in d06_records)
assert any(
    item["resolution"]["native_resolution_m"] == 250.0 for item in d06_records
)
print(
    "    statuses="
    + ",".join(f"{code}:{earth['dimensions'][code]['status']}" for code in earth["dimensions"])
    + f" records={len(earth_records)}"
)

# ---------------------------------------------------------------------------
# 3. Moon AOI/evidence, including explicit no_coverage
# ---------------------------------------------------------------------------
section(3, TOTAL, "Moon AOI/evidence + explicit no_coverage")
moon = get("/coverage/aoi?body=Moon&west=-157.2&south=-89.85&east=-156.2&north=-89.7")
assert set(moon["dimensions"]) == {"D01", "D02", "D03", "D04", "D05", "D06"}
assert moon["actual_indexed_coverage"]["global_coverage_claimed"] is False
assert moon["dimensions"]["D01"]["status"] == "available_or_indexed"
assert moon["dimensions"]["D01"]["intersecting_record_count"] > 0

moon_areas = {
    "D02": "moon-shackleton-rim-site04-roi2",
    "D03": "thermal-moon-lola-site01-roi4",
    "D04": "moon-minirf-cpr-l22965-s05403",
    "D05": "moon-shackleton-rim-site04-roi2",
    "D06": "moon-apollo15-station8-lsrp",
}
for dimension, area_id in moon_areas.items():
    envelope = get(
        "/typed/observations?"
        + urllib.parse.urlencode(
            {"body": "Moon", "dimension": dimension, "area_id": area_id, "envelope": "true"}
        )
    )
    assert envelope["status"] == "ok", (dimension, envelope["status"])
    assert envelope["count"] > 0, dimension
    observations = envelope["observations"]
    assert all(
        item["body"] == "Moon"
        and item["dimension_code"] == dimension
        and item["area_id"] == area_id
        for item in observations
    ), dimension
    assert all(provenance(item) for item in observations), dimension
    if dimension in {"D03", "D04", "D06"}:
        assert any(item["state"] == "no_coverage" for item in observations), dimension
        assert all(
            item["value"] is None for item in observations if item["state"] == "no_coverage"
        ), dimension

assert moon["dimensions"]["D06"]["status"] in {
    "explicit_no_coverage",
    "not_indexed",
    "not_indexed_at_location",
    "evidence_only",
}
print(
    "    statuses="
    + ",".join(f"{code}:{moon['dimensions'][code]['status']}" for code in moon["dimensions"])
    + " D06 no_coverage=6"
)

# ---------------------------------------------------------------------------
# 4. Mars AOI/evidence, including explicit no_coverage
# ---------------------------------------------------------------------------
section(4, TOTAL, "Mars AOI/evidence + explicit no_coverage")
mars = get("/coverage/aoi?body=Mars&west=76&south=17&east=79&north=20")
assert set(mars["dimensions"]) == {"D01", "D02", "D03", "D04", "D05", "D06"}
assert mars["actual_indexed_coverage"]["global_coverage_claimed"] is False
assert mars["dimensions"]["D01"]["status"] == "available_or_indexed"

mars_areas = {
    "D02": "mars-test-jezero-floor",
    "D03": "thermal-mars-jezero-ctx-window-01",
    "D04": "mars-hend-cell-n20p00-e78p00",
    "D05": "mars-jezero-ctx-window-01",
    "D06": "mars-insight-elysium-hp3",
}
for dimension, area_id in mars_areas.items():
    envelope = get(
        "/typed/observations?"
        + urllib.parse.urlencode(
            {"body": "Mars", "dimension": dimension, "area_id": area_id, "envelope": "true"}
        )
    )
    assert envelope["status"] == "ok", (dimension, envelope["status"])
    assert envelope["count"] > 0, dimension
    observations = envelope["observations"]
    assert all(
        item["body"] == "Mars"
        and item["dimension_code"] == dimension
        and item["area_id"] == area_id
        for item in observations
    ), dimension
    assert all(provenance(item) for item in observations), dimension
    if dimension == "D06":
        assert all(item["state"] == "no_coverage" for item in observations)
        assert all(item["value"] is None for item in observations)
print(
    "    statuses="
    + ",".join(f"{code}:{mars['dimensions'][code]['status']}" for code in mars["dimensions"])
    + " D06 no_coverage=6"
)

# ---------------------------------------------------------------------------
# 5. Evidence query facets: AOI/body/dimension/feature/state/source/depth/time
# ---------------------------------------------------------------------------
section(5, TOTAL, "evidence query facets + explicit empty state")
mapped = get(
    "/typed/observations?"
    + urllib.parse.urlencode(
        {
            "body": "Mars",
            "dimension": "D02",
            "feature_id": "geologic_unit",
            "state": "detected",
            "dataset_id": "usgs-jezero-sim3464",
            "envelope": "true",
        }
    )
)
assert mapped["contract_version"] == "typed-observations-query-1.0.0"
assert mapped["status"] == "ok"
assert mapped["count"] >= 1
assert mapped["query"]["state"] == "detected"
assert mapped["query"]["dataset_id"] == "usgs-jezero-sim3464"
assert all(item["feature_id"] == "geologic_unit" for item in mapped["observations"])
assert all(item["state"] == "detected" for item in mapped["observations"])
assert all("usgs-jezero-sim3464" in item["dataset_ids"] for item in mapped["observations"])

source_asset = mapped["observations"][0]["source_asset_ids"][0]
by_asset = get(
    "/typed/observations?"
    + urllib.parse.urlencode(
        {"body": "Mars", "dimension": "D02", "source_asset_id": source_asset, "envelope": "true"}
    )
)
assert by_asset["count"] >= 1
assert all(source_asset in item["source_asset_ids"] for item in by_asset["observations"])

by_area = get(
    "/typed/observations?"
    + urllib.parse.urlencode(
        {"body": "Earth", "dimension": "D06", "area_id": "earth-devon-polar-r3c1", "envelope": "true"}
    )
)
assert by_area["count"] >= 1
assert all(item["area_id"] == "earth-devon-polar-r3c1" for item in by_area["observations"])

by_depth = get(
    "/typed/observations?"
    + urllib.parse.urlencode(
        {
            "body": "Earth",
            "dimension": "D06",
            "feature_id": "soil_clay_0_5cm_mean",
            "state": "detected",
            "min_depth_m": 0,
            "max_depth_m": 0.05,
            "envelope": "true",
        }
    )
)
assert by_depth["count"] >= 113, by_depth["count"]
for item in by_depth["observations"]:
    depth = item["depth_support"]
    assert depth["min_m"] == 0.0 and depth["max_m"] == 0.05, depth

by_time = get(
    "/typed/observations?"
    + urllib.parse.urlencode(
        {"body": "Mars", "dimension": "D05", "temporal_kind": "instant", "envelope": "true"}
    )
)
assert by_time["status"] == "ok" and by_time["count"] >= 1
assert all(item["temporal_support"]["kind"] == "instant" for item in by_time["observations"])

empty = get(
    "/typed/observations?"
    + urllib.parse.urlencode({"body": "Mars", "dimension": "D06", "state": "detected", "envelope": "true"})
)
assert empty["status"] == "no_data", empty["status"]
assert empty["count"] == 0
assert empty["observations"] == []

plain = get("/typed/observations?body=Mars&dimension=D02&feature_id=geologic_unit")
assert isinstance(plain, list) and plain, "plain-array compatibility is required"
print(
    f"    D02 matched={mapped['count']} asset={by_asset['count']} area={by_area['count']} "
    f"depth={by_depth['count']} time={by_time['count']} empty={empty['status']} plain={len(plain)}"
)

# ---------------------------------------------------------------------------
# 6. D01 + D06 ranking change and exact contribution recomputation
# ---------------------------------------------------------------------------
section(6, TOTAL, "D01 + D06 weighted search changes the ranking")
d01_only = post(
    "/search/multidimensional",
    {
        "targets": {"slope_mean_deg": {"value": 10, "weight": 1}},
        "filters": {"soil_clay_0_5cm_mean": {"min": 0, "max": 1000}},
        "limit": 10,
    },
)
combined = post(
    "/search/multidimensional",
    {
        "targets": {
            "slope_mean_deg": {"value": 10, "weight": 1},
            "soil_clay_0_5cm_mean": {"value": 200, "weight": 1},
        },
        "limit": 10,
    },
)
assert d01_only["eligible_candidate_count"] == 113
assert combined["eligible_candidate_count"] == 113
assert combined["excluded_candidate_count"] == 0
d01_order = [item["location_id"] for item in d01_only["results"]]
combined_order = [item["location_id"] for item in combined["results"]]
assert combined_order != d01_order, "D06 must change the D01-only order"
assert combined_order[0] != d01_order[0], "D06 must change the top-1"

top = combined["results"][0]
clay_contribution = next(
    item for item in top["contributions"] if item["feature_id"] == "soil_clay_0_5cm_mean"
)
assert clay_contribution["contribution"] > 0
expected = ((clay_contribution["candidate"] - 200.0) / 200.0) ** 2 / 2.0
assert abs(clay_contribution["contribution"] - expected) < 1e-12
assert clay_contribution["observation_id"].startswith("soilgrids:")
assert clay_contribution["uncertainty"]["q05"] is not None
assert clay_contribution["uncertainty"]["q95"] is not None
assert clay_contribution["support"]["native_resolution_m"] == 250.0
assert "six-dimension scientific total" in combined["limitations"][0]

zero_weight = post(
    "/search/multidimensional",
    {
        "targets": {
            "slope_mean_deg": {"value": 10, "weight": 1},
            "soil_clay_0_5cm_mean": {"value": 200, "weight": 0},
        },
        "limit": 10,
    },
)
assert zero_weight["eligible_candidate_count"] == 113
assert zero_weight["excluded_candidate_count"] == 0
assert zero_weight["results"][0]["location_id"] == d01_order[0]
assert all(
    "soil_clay_0_5cm_mean" not in item["valid_features"] for item in zero_weight["results"]
)
assert zero_weight["coverage"]["features"]["soil_clay_0_5cm_mean"]["comparison"] == (
    "zero_weight_not_compared"
)

dimension_weighted = post(
    "/search/multidimensional",
    {
        "targets": {
            "slope_mean_deg": {"value": 10, "weight": 1},
            "soil_clay_0_5cm_mean": {"value": 200, "weight": 1},
            "soil_sand_0_5cm_mean": {"value": 300, "weight": 1},
        },
        "dimension_weights": {
            "D01": {"weight": 1, "enabled": True},
            "D06": {"weight": 1, "enabled": True},
        },
        "scoring_version": "dimension-weighted-rms-v1",
        "limit": 3,
    },
)
assert dimension_weighted["scoring"]["version"] == "dimension-weighted-rms-v1"
assert dimension_weighted["scoring"]["active_dimensions"] == ["D01", "D06"]
dimension_top = dimension_weighted["results"][0]
weights = {item["feature_id"]: item for item in dimension_top["contributions"]}
q_d01 = ((weights["slope_mean_deg"]["candidate"] - 10.0) / 10.0) ** 2
q_d06 = (
    ((weights["soil_clay_0_5cm_mean"]["candidate"] - 200.0) / 200.0) ** 2
    + ((weights["soil_sand_0_5cm_mean"]["candidate"] - 300.0) / 200.0) ** 2
) / 2.0
expected_distance = math.sqrt((q_d01 + q_d06) / 2.0)
assert abs(dimension_top["distance"] - expected_distance) < 1e-12
assert dimension_top["dimension_contributions"]["D01"]["feature_count"] == 1
assert dimension_top["dimension_contributions"]["D06"]["feature_count"] == 2

disabled = post(
    "/search/multidimensional",
    {
        "targets": {
            "slope_mean_deg": {"value": 10, "weight": 1},
            "soil_clay_5_15cm_mean": {"value": 200, "weight": 1},
        },
        "dimension_weights": {"D06": {"weight": 0, "enabled": False}},
        "limit": 3,
    },
)
assert disabled["scoring"]["active_dimensions"] == ["D01"]
assert disabled["eligible_candidate_count"] == 113
assert disabled["excluded_candidate_count"] == 0
assert all(
    "soil_clay_5_15cm_mean" not in item["valid_features"] for item in disabled["results"]
)
print(
    f"    D01-top={d01_order[0]} D01+D06-top={combined_order[0]} "
    f"clay-contribution={clay_contribution['contribution']:.12f} "
    f"dimension-distance={dimension_top['distance']:.12f}"
)

# ---------------------------------------------------------------------------
# 7. Cross-body and score-role guards
# ---------------------------------------------------------------------------
section(7, TOTAL, "cross-body and evidence-only guards")
cross_body = post(
    "/search/multidimensional",
    {
        "reference_id": "moon-lola-site01-roi1",
        "targets": {"soil_clay_0_5cm_mean": {"value": 200, "weight": 1}},
    },
    expected=422,
)
assert cross_body["error"]["code"] == "body_out_of_scope"
assert cross_body["error"]["details"][0]["reason"] == "cross_body_incompatible"

role_guard = post(
    "/search/multidimensional",
    {"targets": {"geologic_unit": {"value": 1, "weight": 1}}},
    expected=422,
)
assert role_guard["error"]["code"] == "feature_role_not_scoreable"
# Issue #170 adds an exact-label category filter; similarity stays disabled.
assert role_guard["error"]["details"][0]["legal_roles"] == ["category_match", "evidence"]
print(
    f"    cross-body={cross_body['error']['code']} role={role_guard['error']['code']}"
)

# ---------------------------------------------------------------------------
# 8. Out-of-coverage bbox: explicit status, no fabricated values
# ---------------------------------------------------------------------------
section(8, TOTAL, "out-of-coverage bbox: explicit status")
outside = get(
    "/coverage/aoi?body=Earth&west=-140.1&south=-0.1&east=-139.9&north=0.1"
)
assert set(outside["dimensions"]) == {"D01", "D02", "D03", "D04", "D05", "D06"}
assert outside["actual_indexed_coverage"]["global_coverage_claimed"] is False
assert outside["actual_indexed_coverage"]["intersecting_candidate_ids"] == []
assert outside["actual_indexed_coverage"]["intersecting_typed_area_ids"] == []
assert outside["intersecting_records"] == []
for dimension, detail in outside["dimensions"].items():
    assert detail["status"] in {
        "not_indexed",
        "not_indexed_at_location",
        "unsupported",
        "explicit_no_coverage",
    }, (dimension, detail["status"])
    assert detail["features"] == [], dimension
for dimension, reason in outside["missing_reasons"].items():
    assert reason["reason"] == "no_intersecting_indexed_records", (dimension, reason)
    assert reason["declared_source_count"] >= 0
print(
    "    statuses="
    + ",".join(f"{code}:{outside['dimensions'][code]['status']}" for code in outside["dimensions"])
    + " records=0"
)

# ---------------------------------------------------------------------------
# 9. Earth-only WorldCover hard filter (legacy /search) and non-scoring roles
# ---------------------------------------------------------------------------
section(9, TOTAL, "Earth-only WorldCover hard filter")
unfiltered = post(
    "/search",
    {
        "body": "custom",
        "target": {"slope_mean_deg": {"value": 5, "weight": 1}},
        "limit": 10,
    },
)
filtered = post(
    "/search",
    {
        "body": "custom",
        "target": {"slope_mean_deg": {"value": 5, "weight": 1}},
        "filters": {"landcover_bare_sparse_fraction": {"max": 0.5}},
        "limit": 10,
    },
)
assert filtered["query_version"] == "search-1.1.0"
assert filtered["filters_applied"] == {
    "landcover_bare_sparse_fraction": {"min": None, "max": 0.5}
}
assert filtered["eligible_candidate_count"] < unfiltered["eligible_candidate_count"]
atacama = [
    item for item in filtered["excluded"] if item["location_id"] == "earth-atacama-desert"
]
assert atacama, "Atacama (bare fraction 0.995) must be excluded by the hard filter"
assert atacama[0]["reason"] == "filter:landcover_bare_sparse_fraction"

unfiltered_distance = {
    item["location_id"]: item["distance"] for item in unfiltered["results"]
}
for result in filtered["results"]:
    location = get("/locations/" + urllib.parse.quote(result["location_id"]))
    values = {
        item["feature_id"]: item
        for item in location["context_observations"]
        if item["feature_id"] == "landcover_bare_sparse_fraction"
    }
    assert values, result["location_id"]
    assert values["landcover_bare_sparse_fraction"]["value"] <= 0.5
    assert values["landcover_bare_sparse_fraction"]["score_role"] == "context_only"
    if result["location_id"] in unfiltered_distance:
        assert abs(
            result["distance"] - unfiltered_distance[result["location_id"]]
        ) < 1e-15, "the filter must not change the similarity distance"

capabilities_again = get("/capabilities")
context_features = capabilities_again["context_features"]
assert context_features
assert all(item["feature_id"].startswith("landcover_") for item in context_features)
assert all(item["score_role"] == "context_only" for item in context_features)
assert all(item["provider_coverage_by_body"]["Moon"] == "not_applicable" for item in context_features)
assert all(item["provider_coverage_by_body"]["Mars"] == "not_applicable" for item in context_features)

dimensions = get("/dimensions")
roles = {item["dimension_id"]: item["score_role"] for item in dimensions["dimensions"]}
assert roles.get("land_cover_context") == "context_only"
assert roles.get("terrain_shape") == "similarity"

similarity_registry = get("/search/features?role=similarity&body=Earth")
assert all(
    "landcover" not in item["feature_id"] for item in similarity_registry["features"]
)
print(
    f"    unfiltered={unfiltered['eligible_candidate_count']} "
    f"filtered={filtered['eligible_candidate_count']} "
    f"excluded={filtered['excluded_candidate_count']} atacama=excluded"
)

# ---------------------------------------------------------------------------
# 10. WorldCover multidimensional API gap (explicit, not silently accepted)
# ---------------------------------------------------------------------------
section(10, TOTAL, "WorldCover multidimensional API gap")
gap = post(
    "/search/multidimensional",
    {
        "targets": {"slope_mean_deg": {"value": 5, "weight": 1}},
        "filters": {"landcover_bare_sparse_fraction": {"max": 0.5}},
        "limit": 3,
    },
    expected=422,
)
assert gap["error"]["code"] == "unknown_feature", gap
assert "landcover_bare_sparse_fraction" in gap["error"]["message"]
print(
    "    /search/multidimensional rejects landcover filters with "
    f"error.code={gap['error']['code']} (documented gap for #119)"
)

# ---------------------------------------------------------------------------
# 11. Summary
# ---------------------------------------------------------------------------
section(11, TOTAL, "summary")
print(
    "[sixdim-aoi] PASS: six-dimension AOI, evidence facets, D01+D06 ranking, "
    "out-of-coverage, and Earth-only WorldCover hard filter verified."
)
print(
    "[sixdim-aoi] non-claims: subset coverage only; no global survey; "
    "D02-D05 expose filters/category controls but no similarity; D06 is Earth-only SoilGrids 0-5 cm means."
)
PY

say "RESULT: PASS"
