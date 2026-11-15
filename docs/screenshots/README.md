# Public UI screenshots

Captured 2026-11-15 against the Docker + Cloudflare Tunnel deployment using headless Chromium
(Playwright 1.65.1), viewport 1440×1100, with bounded DNS overrides for the public hosts.

| File | Page |
|---|---|
| `01-home.png` | Initial search page |
| `02-six-dimensions-earth.png` | Six-dimension panel, `earth-sahara-r1c1` (D03–D06) |
| `03-six-dimensions-mars.png` | Six-dimension panel, `mars-test-jezero-western-delta` (D02 evidence/context) |
| `04-global-discovery.png` | Global discovery, real 168-cell Geomorpho pilot index |

The screenshots are evidence of the live public UI, not a claim of global data coverage. The
six-dimension panel shows missing states rather than zero-filling, and no combined six-dimension
score.

Reproduction outline:

```bash
# in a temporary directory (not committed)
npm install playwright
npx playwright install chromium
# run a headless Chromium script against https://貓熊.台灣/ and
# click the Six dimensions / Global discovery tabs
```

The capture requires the public stack to be running; no credentials are embedded in the PNG files.
