# Public UI screenshots

Captured 2026-11-15 against the live Docker + Cloudflare Tunnel deployment
(`https://貓熊.台灣/`, API `https://api.貓熊.台灣/api/v1`) with headless Chromium
(Playwright 1.65.1). Desktop viewport 1440×1100; mobile viewport 390×844 at 2× DPR.
The capture used bounded DNS overrides for the public hosts; no credentials are embedded.

| File | Flow |
|---|---|
| `01-home.png` | Initial search page |
| `02-six-dimensions-earth.png` | Six-dimension panel, `earth-sahara-r1c1` |
| `03-six-dimensions-mars.png` | D02 Mars evidence/context |
| `04-global-discovery.png` | Real 168-cell Earth coarse index |
| `05-six-dimensions-moon.png` | D02 Moon geology + D05 SPICE solar geometry |
| `06-home-desktop.png` | Desktop home with live counts (520 typed observations) |
| `07-reference-results-desktop.png` | Moon reference selected → indexed Earth candidate results |
| `08-six-dimensions-desktop.png` | Six dimensions for a real Mars reference area |
| `09-refine-plan-desktop.png` | 90 m refine plan for a real coarse candidate |
| `10-refine-executed-desktop.png` | **Executed** 90 m AOI job result with real Copernicus/WorldCover features |
| `11-home-mobile.png` | Mobile home |
| `12-six-dimensions-mobile.png` | Mobile six-dimension panel |
| `13-global-mobile.png` | Mobile global discovery |

The screenshots are evidence of the live public UI, not a claim of global data coverage.
Missing states are shown rather than zero-filled, there is no combined six-dimension score,
and 250 m coarse values are never presented as 90 m fine values.

Reproduction outline (temporary directory, not committed):

```bash
npm install playwright
npx playwright install chromium
# run a headless Chromium script against https://貓熊.台灣/ and
# click Search target / Six dimensions / Global discovery, then
# create a refine plan and execute one 1.98 km window.
```

The capture requires the public stack to be running. Executed refine results come from the
real AOI job API; the screenshot records the actual feature values and provider status.
