#!/usr/bin/env bash
# Read-only acceptance for the Cloudflare Pages frontend + tunnel API.
set -euo pipefail

WEB="${PAGES_WEB_BASE:-https://2026-nasa-hackathon.xn--2vxr09c.xn--kpry57d}"
API="${PUBLIC_API_BASE:-https://api.xn--2vxr09c.xn--kpry57d}"
ORIGIN="${PAGES_ORIGIN:-https://2026-nasa-hackathon.xn--2vxr09c.xn--kpry57d}"
CURL=(curl -fsS --max-time 30 --retry 3 --retry-delay 2 --retry-all-errors)
say() { printf '[pages] %s\n' "$*"; }

say "home and static asset"
home="$("${CURL[@]}" "$WEB/")"
asset="$(printf '%s' "$home" | grep -oE '/assets/[^" ]+\.(js|css)' | head -1)"
test -n "$asset"
"${CURL[@]}" -o /dev/null "$WEB$asset"
printf 'home=200 asset=%s=200\n' "$asset"

say "SPA deep link"
"${CURL[@]}" -o /dev/null "$WEB/global"
echo "deep-link=/global 200"

say "CORS preflight and POST from Pages origin"
"${CURL[@]}" -i -X OPTIONS "$API/api/v1/global/search" \
  -H "Origin: $ORIGIN" -H 'Access-Control-Request-Method: POST' \
  -H 'Access-Control-Request-Headers: content-type' \
  | grep -qi "access-control-allow-origin: $ORIGIN"
"${CURL[@]}" -o /dev/null -D /tmp/pages-cors.headers -X POST "$API/api/v1/global/search" \
  -H "Origin: $ORIGIN" -H 'content-type: application/json' \
  -d '{"target":{"coarse_slope_mean_deg":{"value":8,"weight":1}},"limit":2}'
grep -qi "access-control-allow-origin: $ORIGIN" /tmp/pages-cors.headers
echo "cors=$ORIGIN"

say "typed and global API"
"${CURL[@]}" "$API/api/v1/typed/coverage" | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d["total"]>=181, d["total"]; print("typed=", d["total"])'
"${CURL[@]}" "$API/api/v1/global/coverage" | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d["status"]=="ready"; assert d["row_count"]>=168; print("global rows=", d["row_count"], d["label"])'

say "existing web and API endpoints"
"${CURL[@]}" -o /dev/null "https://xn--2vxr09c.xn--kpry57d/"
"${CURL[@]}" -o /dev/null "$API/api/v1/health"
echo "existing-web=200 existing-api=200"

say "RESULT: PASS"
