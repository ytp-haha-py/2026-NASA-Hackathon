#!/usr/bin/env bash
# Read-only public acceptance for the six-dimension demo. No credentials are used.
set -euo pipefail

API_BASE="${PUBLIC_API_BASE:-https://api.xn--2vxr09c.xn--kpry57d}"
WEB_BASE="${PUBLIC_WEB_BASE:-https://xn--2vxr09c.xn--kpry57d}"
CURL=(curl -fsS --max-time 30 --retry 3 --retry-delay 2 --retry-all-errors)

say() { printf '[acceptance] %s\n' "$*"; }

say "health"
"${CURL[@]}" "$API_BASE/api/v1/health" | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d["status"]=="ok"; print("ok", d["data_version"])'

say "capabilities + dimensions"
"${CURL[@]}" "$API_BASE/api/v1/capabilities" | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d["indexed_candidate_count"]>=113, d["indexed_candidate_count"]; assert len(d["features"])==5; print("ok features=5 indexed=", d["indexed_candidate_count"])'
"${CURL[@]}" "$API_BASE/api/v1/dimensions" | python3 -c 'import json,sys; d=json.load(sys.stdin); assert len(d["dimensions"])==2; print("ok dimensions=2")'

say "typed six-dimension coverage"
"${CURL[@]}" "$API_BASE/api/v1/typed/coverage" | python3 -c '
import json,sys
d=json.load(sys.stdin)
assert d["total"] >= 181, d["total"]
assert {"D02","D03","D04","D05","D06"} <= set(d["by_dimension"])
print("ok total=", d["total"])
'

say "real global coarse index"
"${CURL[@]}" "$API_BASE/api/v1/global/coverage" | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d["status"]=="ready"; assert d["row_count"]==168; assert d["regions"]==["pilot-alps","pilot-grand-canyon","pilot-sahara"]; print("ok rows=168 regions=3", d["label"])'
"${CURL[@]}" "$API_BASE/api/v1/global/search" -H 'content-type: application/json' -d '{"target":{"coarse_slope_mean_deg":{"value":8.0,"weight":1.0}},"limit":3}' | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d["indexed_candidate_count"]==168; assert len(d["results"])==3; print("ok top3", [r["candidate_id"] for r in d["results"]])'

say "legacy Moon reference search"
"${CURL[@]}" "$API_BASE/api/v1/search" -H 'content-type: application/json' -d '{"body":"Moon","reference_id":"moon-shackleton-rim-site04-roi2","limit":3}' | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d["coverage_label"]=="indexed_subset"; assert d["results"]; print("ok results=", len(d["results"]))'

say "feature eligibility + multi-dimensional search"
"${CURL[@]}" "$API_BASE/api/v1/search/features?dimension=D06&role=similarity" | python3 -c '
import json,sys
d=json.load(sys.stdin)
assert d["registry_version"]=="feature-eligibility-1.0.0"
features={f["feature_id"]:f for f in d["features"]}
clay=features["soil_clay_0_5cm_mean"]
assert clay["roles"]["similarity"]["supported"] is True
assert clay["coverage"]["gated_candidate_count"]==5, clay["coverage"]
assert d["join_audit"]["proximity_join_used"] is False
print("ok D06 similarity features=", len(features), "clay gated=", clay["coverage"]["gated_candidate_count"])
'
"${CURL[@]}" "$API_BASE/api/v1/search/multidimensional" -H 'content-type: application/json' -d '{"targets":{"slope_mean_deg":{"value":10,"weight":1},"soil_clay_0_5cm_mean":{"value":200,"weight":1}},"limit":5}' | python3 -c '
import json,sys
d=json.load(sys.stdin)
assert d["eligible_candidate_count"]==5, d["eligible_candidate_count"]
clay=[i for i in d["results"][0]["contributions"] if i["feature_id"]=="soil_clay_0_5cm_mean"][0]
assert clay["contribution"]>0
assert clay["observation_id"].startswith("soilgrids:")
assert clay["uncertainty"]["q05"] is not None
print("ok eligible=5 top=", d["results"][0]["location_id"], "D06 contribution=", round(clay["contribution"],12))
'
cross_code="$(curl -sS -o /tmp/public-cross-body.json -w '%{http_code}' --max-time 30 -X POST -H 'content-type: application/json' -d '{"reference_id":"moon-lola-site01-roi1","targets":{"soil_clay_0_5cm_mean":{"value":200,"weight":1}}}' "$API_BASE/api/v1/search/multidimensional")"
[ "$cross_code" = "422" ] || { echo "expected 422 cross-body, got $cross_code" >&2; exit 1; }
python3 -c 'import json; d=json.load(open("/tmp/public-cross-body.json")); assert d["error"]["code"]=="body_out_of_scope"; assert d["error"]["details"][0]["reason"]=="cross_body_incompatible"; print("ok cross-body rejected")'

say "planetary map catalog + overlays"
"${CURL[@]}" "$API_BASE/api/v1/planetary/maps?body=Moon" | python3 -c 'import json,sys; d=json.load(sys.stdin); assert len(d["layers"])==2; assert {l["layer_type"] for l in d["layers"]}=={"imagery","elevation"}; assert all(l["verification"]["http_status"]==200 for l in d["layers"]); print("ok Moon layers=2 verified")'
"${CURL[@]}" "$API_BASE/api/v1/planetary/maps?body=Mars" | python3 -c 'import json,sys; d=json.load(sys.stdin); assert len(d["layers"])==2; assert d["coverage_separation"]["basemap_extent"].startswith("global"); assert d["science_coverage"]["coverage_label"]=="curated_subset_not_global"; print("ok Mars layers=2 coverage=curated")'
"${CURL[@]}" "$API_BASE/api/v1/planetary/map-features?body=Mars&bbox=76,17,79,20&limit=200" | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d["total"]>=18, d["total"]; ids={f["feature_id"] for f in d["features"]}; assert "reference:mars-jezero-ctx-window-01" in ids; assert "typed:mars-jezero-ctx-window-01" in ids; print("ok Mars overlay total=", d["total"])'
"${CURL[@]}" "$API_BASE/api/v1/planetary/map-features?body=Moon&bbox=120,-90,-150,-85&limit=200" | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d["bbox"]["crosses_antimeridian"] is True; assert d["total"]>=20, d["total"]; print("ok Moon antimeridian total=", d["total"])'
"${CURL[@]}" "$API_BASE/api/v1/planetary/coordinate-contract" | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d["contract_version"]=="planetary-coordinates-1.0.0"; assert {b["body"] for b in d["bodies"]}=={"Moon","Mars"}; print("ok coordinate contract")'

say "web + CORS"
"${CURL[@]}" -o /dev/null -w 'web=%{http_code}\n' "$WEB_BASE/"
curl -sS -i --max-time 30 --retry 3 --retry-all-errors --resolve api.xn--2vxr09c.xn--kpry57d:443:104.21.85.194 -X OPTIONS "$API_BASE/api/v1/health" -H "Origin: https://xn--2vxr09c.xn--kpry57d" -H 'Access-Control-Request-Method: GET' | grep -qi 'access-control-allow-origin: https://xn--2vxr09c.xn--kpry57d' && echo 'cors=ok'

say "RESULT: PASS"
