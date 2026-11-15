# Public end-to-end acceptance

Verified 2026-11-15 against the Docker stack and Cloudflare Tunnel.

## Public endpoints

```text
https://api.貓熊.台灣/api/v1/health
https://api.貓熊.台灣/api/v1/capabilities
https://api.貓熊.台灣/api/v1/dimensions
https://api.貓熊.台灣/api/v1/typed/coverage
https://api.貓熊.台灣/api/v1/global/coverage
https://api.貓熊.台灣/docs
https://貓熊.台灣/
https://www.貓熊.台灣/
```

`bash scripts/public_acceptance.sh` observed:

```text
health: ok dv-b46f3bad38ea
capabilities: features=5 indexed=113
dimensions: 2
typed coverage: total=199, dimensions D02,D03,D04,D05,D06
global coverage: ready, rows=168, regions=pilot-alps,pilot-grand-canyon,pilot-sahara
global top-3: coarse-v1:95046a41aeb31ca9, coarse-v1:61c31ae082cf0dbe, coarse-v1:d558cc84b2550c64
legacy Moon reference search: results=3
web: HTTP 200
CORS: access-control-allow-origin = https://xn--2vxr09c.xn--kpry57d
RESULT: PASS
```

## UI screenshots

- [Home/search](screenshots/01-home.png)
- [D03–D06 Earth observations](screenshots/02-six-dimensions-earth.png)
- [D02 Mars evidence/context](screenshots/03-six-dimensions-mars.png)
- [D02 Moon geology + D05 SPICE illumination](screenshots/05-six-dimensions-moon.png)
- [Real 168-cell global discovery](screenshots/04-global-discovery.png)

The six-dimension panel is driven by `/api/v1/dimensions`, `/api/v1/typed/areas/{id}` and
`/api/v1/typed/observations`. It shows states, value kinds, score roles, units, support, time/depth,
QA, method and provenance, and never creates a six-dimension total score. The global panel is driven
by the real committed Geomorpho pilot index, not a synthetic worldwide claim.

## Actual counts

```text
D02 = 56  (Earth availability/status 15, Moon 3, Mars 38)
D03 = 20  (Earth NASA POWER thermal context)
D04 = 20  (Earth NASA POWER water context)
D05 = 28  (Earth NASA POWER 10 + Moon SPICE 8 + Mars SPICE 10)
D06 = 75  (Earth SoilGrids 0-5 cm)
typed total = 199
real global coarse = 168 cells / 3 pilot regions
legacy fine terrain = 113 Earth / 18 Moon / 5 Mars
```

## Reproduction

```bash
bash scripts/public_acceptance.sh
make public-smoke
docker compose -f compose.public.yaml ps
```

Screenshots were captured with a headless Chromium against the public tunnel; the capture script is
documented in `docs/screenshots/README.md`.

## Explicit limitations

- Moon/Mars D03–D06 observations are not available; no values are substituted.
- D02 has Moon/Mars map evidence and context, but zero verified quantitative mineral abundance.
- The real coarse index covers three 0.4° pilot regions, not global Earth.
- Coarse 250 m values are never compared to the 90 m terrain method.
- No six-dimension combined score is produced.
