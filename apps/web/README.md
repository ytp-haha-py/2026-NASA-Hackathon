# AnalogX — Web

The React frontend for AnalogX. Use it to search measured Earth terrain, explore Moon and Mars references, inspect six dimensions of observations, and analyze a bounded Earth area.

Only scientifically eligible features contribute to search scores. The map is for navigation; scientific data coverage remains limited to indexed areas.

Choose **Moon** or **Mars** to open its compact map, then select a plotted reference point for the Earth analog search. Clicking overlapping points zooms into their area. Each body remembers its latest selection while the page is open. Use **Collapse map** to hide the preview, or select the same body to reopen it.

## Requirements

- Node.js 22+ (This aligns with the repository quality gate; developed with Node 26.)
- npm (this app uses npm and commits `package-lock.json`)
- For local data, the API running at `http://127.0.0.1:8000`; otherwise development uses the public API

## Development

```bash
cd apps/web
npm ci
npm run dev
```

Alternatively, run `make web` from the repository root after installing dependencies in
`apps/web`. If port 5173 is occupied, Vite selects another port (for example 5174);
keep other development processes running.

Open the URL Vite prints (normally `http://localhost:5173`). The browser always calls
same-origin `/api/v1/...`; the Vite dev proxy decides once, at startup:

1. If `VITE_API_PROXY_TARGET` is set, use that origin and skip detection.
2. Otherwise probe `http://127.0.0.1:8000/api/v1/health` with a 750 ms timeout.
3. Use local FastAPI only when the response is HTTP 200 and matches the health schema
   (`status=ok`, non-empty `data_version` and `data_snapshot_id`).
4. Otherwise use `https://api.貓熊.台灣`.

The selection is logged in the terminal and stays fixed for the Vite process. A
connection failure or timeout never triggers a per-request switch, so one search or
AOI job cannot mix two databases.

To force a backend (for another API server, or to reproduce a failure), set an
origin-only target with no `/api/v1` path:

```bash
VITE_API_PROXY_TARGET=http://127.0.0.1:9000 npm run dev
# or put the same optional line in the repository-root .env.local
```

A target that includes a path such as `/api/v1` is rejected at startup, and TLS
certificate verification is never disabled.

`VITE_API_BASE` is production-only. The development client always uses `/api/v1`
through the proxy, so setting `VITE_API_BASE` in a local shell cannot bypass the
local-first decision. `npm run preview` and `npm run build` do not probe localhost;
they use the public target unless `VITE_API_PROXY_TARGET` is explicitly set.

Start the API from the repository root if it is not already running:

```bash
ANALOG_SEED_INCLUDE_SYNTHETIC=true \
ANALOG_DATABASE_URL=sqlite+pysqlite:////tmp/analog-webdev.db \
uv run uvicorn analog_api.main:create_app --factory --port 8000
```

## Startup troubleshooting

Run diagnostics in the same checkout and terminal used for development:

```bash
cd apps/web
node -v
npm -v
npm ls maplibre-gl @maplibre/maplibre-gl-leaflet vite
test -f node_modules/maplibre-gl/dist/maplibre-gl.css
```

If the MapLibre stylesheet cannot be resolved, stop this checkout's Vite server,
run `npm ci` in `apps/web`, and restart it. This restores the committed lockfile's
packages without changing the lockfile; do not remove the stylesheet import or
install packages globally. `npm run check:dev` checks native configuration loading,
port fallback, and development HTTP responses for Earth map modules and styles.

`ECONNREFUSED 127.0.0.1:8000` means the selected local API is not accepting
connections. Start `make api` from the repository root in another terminal and
check `curl -fsS http://127.0.0.1:8000/api/v1/health`. To use automatic selection,
stop Vite, run `unset VITE_API_PROXY_TARGET`, and remove that override from any
`.env`, `.env.local`, `.env.development`, or `.env.development.local` in both the
repository root and `apps/web`, then restart. An explicit override skips health
detection and does not start the API for you.

Dependency audit is separate from startup validation. The 2026-11-15 lockfile audit
reports one production MapLibre issue and seven development-tool findings:
[MapLibre attribution sanitization](https://github.com/advisories/GHSA-jrc7-96c5-q579)
and [minimatch pattern expansion](https://github.com/advisories/GHSA-g3hv-75wg-m249).
The map consumes third-party styles, so the production finding needs a separate
MapLibre upgrade and adapter compatibility review. The reported MapLibre fix is
outside the current major version; do not use `npm audit fix --force` as a startup
repair. Development-tool findings concern processing untrusted file patterns,
not browser requests.

On macOS, npm may report blocked install scripts for optional `fsevents` (native
file watching). This does not explain a missing stylesheet or an API connection
refusal. Verify that saving a source file updates the browser; investigate watcher
installation separately if it does not. Do not enable all dependency scripts to
resolve a CSS import error.

## API types

The OpenAPI snapshot is the source of truth at `../../contracts/openapi.json`.
Generated types live in `src/api/schema.d.ts` and are committed.

```bash
npm run generate:api   # regenerate from ../../contracts/openapi.json
npm run check:api      # regenerate to a temp file and fail if committed types differ
```

Do not hand-copy response interfaces when a generated schema type exists. If the
contract changes, regenerate and review the diff.

## Checks

```bash
npm run typecheck
npm run lint
npm run test -- --run
npm run build
npm run check:dev
```

Tests use Vitest, Testing Library, and jsdom. Every network call is mocked with
`fetch`; tests are deterministic and offline. Leaflet is stubbed in tests.

## Honest UX rules

These are product requirements, not decoration:

1. **Terminology.** Distance is a weighted normalized Euclidean distance where
   lower is more similar. Never render it as a similarity percentage, and never
   describe it as physical equivalence.
2. **Coverage.** Results are subset-based. The coverage header states how many
   results were returned among how many searchable indexed regions, splits
   indexed vs user-created on-demand AOIs, and explicitly says the list is **not
   a global top 10**, not a survey, and not a base-site recommendation. Show
   eligible and excluded counts. Never present an on-demand AOI as global coverage.
3. **Exclusions.** Display the API-provided excluded candidate count and reasons
   (missing metrics, missing context, or land-cover filters). Do not hide
   exclusions.
4. **Real vs synthetic.** Every result and location carries `data_kind`. A
   `synthetic` record gets a loud `DEMO / SYNTHETIC` badge and a warning that its
   fixture values are not observations or evidence. Never relabel synthetic data
   as observed.
5. **No invented data.** Reference presets (Moon/Mars) are enabled only when
   `GET /api/v1/references` returns records with map coordinates. If references are empty or fail to
   load, those presets stay disabled with an explanation. Never invent a target
   for a body. The real LOLA Moon reference is shown with its caveat: a 5 m
   source with about 90% interpolated pixels, resampled to the 90 m analysis
   grid. Moon coordinates use a planetary frame, not Earth WGS84.
6. **Earth land-cover context is context only.** ESA WorldCover fractions are
   displayed in a separate section labelled **context only, NOT
   mineralogy/geology, excluded from the similarity score**. They are never
   mixed into terrain comparison tables or the distance. The `context_only` role
   and CC BY 4.0 attribution come from the API.
7. **On-demand AOIs.** The AOI form validates limits from
   `GET /capabilities` (`aoi_analysis`), polls durable jobs, shows
   queued/running/completed/partial/failed states, per-provider statuses,
   warnings, and server 422 codes. Completed AOIs can be compared with
   `POST /api/v1/areas/{id}/compare` (no body), used as the next search target,
   and later appear in search results with an on-demand caveat.
8. **Optional context filters.** The search form can add hard min/max filters on
   bare/sparse and built-up WorldCover fractions. They filter candidates but
   never enter the distance; applied filters are displayed from
   `filters_applied`.
9. **Context export is not a chatbot.** `GET /api/v1/locations/{id}/context` is a
   grounded structured JSON export for later external AI use. It includes
   land-cover context observations when present. The UI exposes Download/Copy
   JSON, not a chat loop, and generates no narrative.
10. **Only compared dimensions are presented as evidence.** The tool compares
    local terrain shape only. Gravity, atmosphere, radiation, regolith,
    mineralogy, temperature, and geological history are called out as **not
    simulated or compared**.
11. **Provenance.** Dataset provider, product, license link, access timestamp,
    processing version, data version, and data snapshot ID come from the API.
    Copernicus DEM and ESA WorldCover are both shown when returned. They are not
    decorative imagery.
12. **Map honesty.** Only returned results are plotted. The note says so. The
    OpenStreetMap basemap is for orientation only and is not scientific evidence.
    If tiles fail or the browser is offline, a readable status message appears;
    the ranked list and record details remain usable.
13. **Accessibility and failure states.** Loading, empty/no-data (including HTTP
    409), no-eligible-candidate, validation, AOI job failure/partial, and API
    error states are visible and announced with `role="status"` or `role="alert"`.
    The layout is usable at 375 px and desktop widths.

## Internationalization (i18n)

The UI supports **English (`en`)** and **Traditional Chinese (`zh-TW`)**. English is
the canonical catalog and the runtime fallback.

- **Switcher.** The `繁體中文 / English` buttons in the page header switch language
  immediately, without a reload. Form inputs, validation errors, search results, the
  selected result and AOI job state are kept. The choice is saved in `localStorage`
  (`planetary-analog-explorer.locale`) and `<html lang>` is updated.
- **Initial locale.** A saved choice wins. Otherwise the first supported browser
  language is used: `zh`, `zh-TW`, `zh-HK`, `zh-MO` and `zh-Hant-*` map to `zh-TW`, and
  `en-*` maps to `en`. Anything else, including Simplified Chinese (`zh-CN`, `zh-Hans`),
  falls back to `en`.
- **No dependency.** The i18n layer is small and in-house (`src/i18n/`), so it adds
  no runtime packages. Message keys are type-checked: a typo in a key, or a zh-TW key
  that does not exist in English, fails `npm run typecheck`.

### Layout

```
src/locales/en/messages.ts     canonical keys + English text + identifier label maps
src/locales/zh-TW/messages.ts  zh-TW text + zh-TW labels for stable API identifiers
src/i18n/translate.ts          locale detection, storage, interpolation, fallback, plurals
src/i18n/I18nProvider.tsx      provider, useI18n(), locale-aware formatters, <SourceText>
src/components/LanguageSwitcher.tsx
```

### What is translated and what is not

- **Translated:** every UI string (headings, forms, buttons, help text, validation,
  loading/empty/error/partial states, caveats, accessibility labels, export controls).
- **Mapped by stable identifier:** feature ids (`slope_mean_deg`, `landcover_*`),
  bodies, job/provider `status`, `quality`, `dimension_id`, and API `error_code`s get
  zh-TW display labels in `zhTWLabels`. Unknown identifiers are shown as sent.
  Raw codes stay visible next to their localized meaning.
- **Never translated:** numeric values, IDs, dataset/product names, providers, licenses,
  URLs, CRS strings, hashes, quality flags, warnings codes, versions, request payloads,
  and JSON export keys. English explanations sent by the API without a stable code
  (descriptions, caveats, limitations, notes, error messages) are shown verbatim inside
  `<SourceText>` (`lang="en"`), with a short "API original text" note in zh-TW. They are
  never machine-translated or reinterpreted.
- Scientific caveats (indexed-subset only, distance is not a percentage, WorldCover is
  not mineralogy, LOLA interpolation, planetary frame) must exist in every locale.
  `src/i18n/translate.test.ts` checks this.

Numbers, percentages and lists use `Intl` for the active locale (`useI18n().fmt`).
Distance keeps its fixed 4-decimal rendering. Timestamps are shown in UTC in the
locale's format, and the raw ISO value stays in `<time dateTime>` and the tooltip.

### Adding or changing a message

1. Add the key and English text to `src/locales/en/messages.ts`. Use `{name}`
   placeholders. Plurals use `key_one` / `key_other` and are rendered with
   `tp("key", count)`.
2. Add the zh-TW text with the **same placeholders** to
   `src/locales/zh-TW/messages.ts`. Chinese only needs `key_other`.
3. Render it with `const { t, tp, rich } = useI18n()`. Use `rich()` when a placeholder
   must be a React node (`<strong>`, `<code>`). Do not concatenate translated
   fragments.
4. Store validation errors as `{ key, vars }`, not rendered strings, so they re-render
   when the language changes.
5. Run `npm run typecheck && npm run test -- --run`. The catalog tests fail if a zh-TW
   key is missing or its placeholders differ from English. If a key is missing at
   runtime, English is shown.

For a new API identifier, add its zh-TW label to the matching map in `zhTWLabels`.
Leave the English map empty so the API stays the source of the English label.

## Project layout

```
src/
  api/client.ts          typed fetch wrapper and generated-schema aliases
  api/schema.d.ts        generated from ../../contracts/openapi.json
  components/            search, filters, AOI form/workspace, results, map, detail, sources
  hooks/useAoiAnalysis.ts AOI submit, job polling, cleanup, feature loading
  i18n/                  locale detection, translator, provider, formatters
  locales/{en,zh-TW}/    version-controlled UI message catalogs
  lib/format.ts          conservative locale-aware number/unit formatting
  lib/aoi.ts             AOI limits, bbox measurements, provider status helpers
  test/                  deterministic fetch mocks and fixtures
  styles.css             single plain CSS file
```

No API tokens, paid services, or external fonts are required. The only runtime
network dependency beyond the API is OpenStreetMap map tiles; failure is handled
without breaking the tool.
