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

say "web + CORS"
"${CURL[@]}" -o /dev/null -w 'web=%{http_code}\n' "$WEB_BASE/"
curl -sS -i --max-time 30 --retry 3 --retry-all-errors --resolve api.xn--2vxr09c.xn--kpry57d:443:104.21.85.194 -X OPTIONS "$API_BASE/api/v1/health" -H "Origin: https://xn--2vxr09c.xn--kpry57d" -H 'Access-Control-Request-Method: GET' | grep -qi 'access-control-allow-origin: https://xn--2vxr09c.xn--kpry57d' && echo 'cors=ok'

say "RESULT: PASS"
