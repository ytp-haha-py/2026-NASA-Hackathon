#!/usr/bin/env bash
# Verify the public Cloudflare Tunnel demo stack without printing secrets.
set -euo pipefail

API_BASE="${PUBLIC_API_BASE:-https://api.xn--2vxr09c.xn--kpry57d}"
WEB_BASE="${PUBLIC_WEB_BASE:-https://xn--2vxr09c.xn--kpry57d}"
CURL=(curl -fsS --max-time 20 --retry 3 --retry-delay 2 --retry-all-errors)

printf '[public-smoke] API health: '
"${CURL[@]}" "$API_BASE/api/v1/health" | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d["status"]=="ok"; print("ok", d["data_version"])'

printf '[public-smoke] typed coverage: '
"${CURL[@]}" "$API_BASE/api/v1/typed/coverage" | python3 -c '
import json,sys
d=json.load(sys.stdin)
assert d["total"] > 0
print("ok total=", d["total"], "dimensions=", ",".join(sorted(d["by_dimension"])))
'

printf '[public-smoke] web apex: '
"${CURL[@]}" -o /dev/null -w '%{http_code}\n' "$WEB_BASE/"

printf '[public-smoke] RESULT: PASS\n'
