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
typed coverage: total=520, dimensions D02,D03,D04,D05,D06
global coverage: ready, rows=168, regions=pilot-alps,pilot-grand-canyon,pilot-sahara
global top-3: coarse-v1:95046a41aeb31ca9, coarse-v1:61c31ae082cf0dbe, coarse-v1:d558cc84b2550c64
legacy Moon reference search: results=3
web: HTTP 200
CORS: access-control-allow-origin = https://xn--2vxr09c.xn--kpry57d
RESULT: PASS
```

## Executed 90 m refinement

The UI now executes a planned 1.98 km window through the existing restricted AOI flow
(`POST /areas/analyze` → poll `GET /jobs/{id}` → `GET /areas/{id}/features`). A live run from
the public deployment completed with real Copernicus GLO-90 and ESA WorldCover reads:

```text
candidate: coarse-v1:95046a41aeb31ca9  (pilot-sahara)
window:    refine:coarse-v1:95046a41aeb31ca9:0:0  (1,980 m)
job:       completed
providers: copernicus-dem-glo90, esa-worldcover-2021-v200
features:  mean slope 10.497 deg
           p90 slope 21.2 deg
           relief p95-p05 173.691 m
           detrended roughness 36.418 m
           flat fraction 0.308
valid:     100% for all five features
warnings:  source_nodata_undefined
```

The executed values remain separate from the 250 m coarse rank and from the existing 90 m
indexed subset. The screenshot `docs/screenshots/10-refine-executed-desktop.png` records the
real result; no value is synthesized.

## UI screenshots

Desktop:

- [Home](screenshots/06-home-desktop.png)
- [Moon reference → Earth candidates](screenshots/07-reference-results-desktop.png)
- [Six dimensions, Mars](screenshots/08-six-dimensions-desktop.png)
- [Refine plan](screenshots/09-refine-plan-desktop.png)
- [Executed 90 m result](screenshots/10-refine-executed-desktop.png)

Mobile:

- [Home](screenshots/11-home-mobile.png)
- [Six dimensions](screenshots/12-six-dimensions-mobile.png)
- [Global discovery](screenshots/13-global-mobile.png)

Earlier panels: [Earth](screenshots/02-six-dimensions-earth.png),
[Mars](screenshots/03-six-dimensions-mars.png), [Moon](screenshots/05-six-dimensions-moon.png),
[global](screenshots/04-global-discovery.png).

## Actual six-dimension counts

```text
D02 = 56   Earth 15, Moon 3, Mars 38   (map units, process evidence, CRISM context; 0 abundance)
D03 = 61   Earth 20, Moon 36, Mars 5   (NASA POWER; Diviner brightness T; THEMIS inertia)
D04 = 75   Earth 20, Moon 53, Mars 2   (POWER context; LP NS WEH, Mini-RF CPR, HEND proxy)
D05 = 28   Earth 10, Moon 8, Mars 10   (POWER context; SPICE model geometry)
D06 = 300  Earth 300                   (SoilGrids 0–5/5–15/15–30/30–60 cm; 5 properties)
typed total = 520
real global coarse = 168 cells / 3 pilot regions
planetary coarse evidence = 4 Moon + 4 Mars CTX + 2 MOLA cells (not cross-body ranked)
legacy fine terrain = 113 Earth / 18 Moon / 5 Mars
```

## Reproduction

```bash
bash scripts/public_acceptance.sh
make public-smoke
docker compose -f compose.public.yaml ps
```

The screenshots were captured with headless Chromium against the public tunnel; see
`docs/screenshots/README.md`.

## Explicit limitations

- D02 has map/spectral evidence, but zero validated quantitative mineral abundance.
- D03 brightness temperature assumes unit emissivity and is not kinetic temperature; THEMIS
  inertia has no per-pixel uncertainty and stops at 60° latitude.
- D04 is evidence-only: no confirmed ice detection, no D04 score; neutron cells are coarser
  than the AOI windows.
- D05 is model geometry, not observed irradiance/shadow, and contains no ionizing radiation.
- D06 is model prediction, not a local sample; Moon/Mars regolith remains `no_coverage`.
- The real coarse index covers three 0.4° pilot regions, not global Earth. Cross-body coarse
  ranking stays disabled because method/support are not validated as equivalent.
- Executed refine covers one user-selected 1.98 km window at a time; no recall or
  global-best claim is made.
- No combined six-dimension score exists.

## Cloudflare Pages frontend

Public Pages URL: `https://2026-nasa-hackathon.貓熊.台灣/`
(`https://2026-nasa-hackathon.xn--2vxr09c.xn--kpry57d/`). The existing
`https://api.貓熊.台灣/api/v1` tunnel API, apex/www tunnel apps and SQLite/Parquet data remain unchanged.

Verified on 2026-11-15:

```text
bash scripts/pages_acceptance.sh
→ home=200 asset=/assets/index-*.js=200
→ deep-link=/global 200
→ CORS preflight + POST from the Pages origin: access-control-allow-origin matches
→ typed=520
→ global rows=168 local_coarse_index_not_global_exhaustive
→ existing-web=200 existing-api=200
→ RESULT: PASS
```

Real browser E2E on the Pages origin (Playwright, no mocks):

- six-dimension panel on `earth-sahara-r1c1`: 70 typed observations; D03/D06 values displayed;
  typed coverage totals 520.
- Moon reference → Earth candidate search.
- global coarse coverage/search, Leaflet map, candidate detail and refine plan.
- on-demand AOI job `completed` with real Copernicus GLO-90 terrain values and WorldCover context.
- desktop and mobile screenshots `docs/screenshots/14`–`22`.

The Cloudflare Pages project is `2026-nasa-hackathon`, production branch `main`, custom domain
active, HTTPS active. `.github/workflows/pages.yml` builds `apps/web` with
`VITE_API_BASE=https://api.貓熊.台灣/api/v1` and deploys on every `main` push affecting the web app
or OpenAPI contract. `ANALOG_CORS_ORIGINS` includes the new Pages origin and retains the existing
apex/www/API/local origins.

## Comparison-dimension integration (Issue #113)

Before: the search board rendered only D01 controls and five identical "Data in preparation"
placeholders. After: it renders fixed D01–D06 cards in ontology order using live
`GET /typed/coverage` and `/typed/observations` data, while D02–D06 remain explicitly
evidence/context-only and expose no weight controls.

Verified on localhost and public Pages:

```text
D01  5 similarity metrics (weighted controls)
D02  56 typed observations · Earth 15 / Moon 3 / Mars 38 · evidence only
D03  61 · Earth 20 / Moon 36 / Mars 5 · context only
D04  75 · Earth 20 / Moon 53 / Mars 2 · evidence only
D05  28 · Earth 10 / Moon 8 / Mars 10 · context only
D06 300 · Earth 300 · evidence only
```

The board exposes "View observations" / "Open six dimensions"; a selected Moon/Mars reference or
Earth candidate is carried into `SixDimensionPanel` without an area-ID prompt. Typed-data
failure shows an alert with retry. No D02–D06 weight, filter or combined score was added.

Screenshots: `docs/screenshots/14` (before), `23`–`25` (after, local), and the public Pages
captures `26`–`29`.

Public deployment evidence:

```text
Pages workflow run 43698078813: success
head commit 86e74a085fcb5e9f0373f86ebc068645acfe3eee
public board: Data in preparation = false
D02 = 56 typed observations, D06 = 300 typed observations
Moon reference selection preserved: true
browser console errors: []
```

The public board was captured at
`https://2026-nasa-hackathon.貓熊.台灣/` with the live tunnel API; the deployed commit matches
`main` after the #113 merge.
