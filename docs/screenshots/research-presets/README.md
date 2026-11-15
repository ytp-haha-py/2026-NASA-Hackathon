# Research Presets — local UI evidence (#130)

Captured from a production build of this branch with `origin/main` at `fb3f1ec` merged.
These are cropped comparison-board screenshots from a **local preview**, not the public
deployment. The API used a disposable `/tmp` SQLite database seeded from committed manifests
and typed bundles; no user database was rebuilt, no scientific source was fetched, and no
AOI analysis or search was triggered. External map-tile/font requests were blocked.

| File | Viewport | Selection |
|---|---|---|
| [desktop-zh-TW.png](desktop-zh-TW.png) | 1440 × 1100 | Rover Mobility, Traditional Chinese |
| [mobile-zh-TW.png](mobile-zh-TW.png) | 390 × 844 | Precision Landing, Traditional Chinese |
| [desktop-en.png](desktop-en.png) | 1440 × 1100 | Geological Fieldwork, English |

The selector sits beside the dimension heading, sharing the desktop row with Top N/actions.
Mobile controls wrap. The visible preset label and explanatory block are absent, while the
select keeps its localized accessible name. `layout.json` records measured header/control
bounds: header `scrollWidth == clientWidth` at both viewport sizes. Desktop select and Top N
share the same vertical position; mobile controls remain within the viewport.

The displayed target zeros are neutral Custom-mode defaults, **not NASA measurements or
recommended research targets**. Weights are project-defined research-informed heuristics;
scientific context and limits remain in [SEARCH_METHOD.md](../../SEARCH_METHOD.md).

## Reproduce

Install regular project dependencies, then run three terminals from the repository root:

```bash
# 1. Isolated API; choose a new disposable path for each independent reproduction.
ANALOG_DATABASE_URL=sqlite+pysqlite:////tmp/research-presets-preview.db \
ANALOG_SEED_INCLUDE_SYNTHETIC=false ANALOG_SEED_INCLUDE_TYPED=true \
uv run uvicorn analog_api.main:create_app --factory --host 127.0.0.1 --port 8137

# 2. Production preview avoids Vite's dependency cache changing during npm ci.
cd apps/web
npm run build
npm run preview -- --host 127.0.0.1 --port 5177 --strictPort

# 3. From repository root; optional tooling is isolated, not added to uv.lock.
uv run --no-project --with playwright==1.65.1 playwright install chromium
uv run --no-project --with playwright==1.65.1 \
  python docs/screenshots/research-presets/capture.py
```

The capture used Playwright 1.65.1 and an already installed Chromium (`chromium-1217`)
via `--browser <path-to-chrome>`; it did not install another browser. `--base-url`, `--api-url`
and `--output` are configurable. Browser API requests are forwarded to the isolated API by
the capture script, without changing production frontend configuration or fabricating responses.
