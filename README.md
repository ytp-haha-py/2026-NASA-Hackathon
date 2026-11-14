# Planetary Analog Explorer

[**English**](README.md) | [**繁體中文 README**](README.zh-TW.md)


**Which Earth terrain windows are the closest numerical analogs to a target terrain shape?**
Planetary Analog Explorer searches terrestrial analogs of Moon/Mars surface conditions using
reproducible, provenance-tracked data. Users enter a terrain target vector or a verified lunar
reference, and the API ranks Earth windows. Users can also request a **new bounded Earth AOI** for
on-demand analysis.

It is **not** a base-site recommender and does **not** claim that one Earth place reproduces every
planetary condition. Discovery results are the best matches **among the indexed subset**, with
indexed counts, coverage, exclusions, and limitations shown. A worldwide basemap is not worldwide
analytical coverage.

Built for the NASA Space Apps Challenge 2026 challenge
[Permanent Moon Base Locations and Mars](https://www.spaceappschallenge.org/2026/challenges/permanent-moon-base-locations-and-mars/).

## Architecture at a glance

```text
Browser (React + Leaflet)
       |
       v
FastAPI /api/v1 ------> SQLite (rebuildable search index + AOI jobs)
       |                              ^
       +-- Search / ranking ---------+
       |
       +-- On-demand Earth AOI ------+-- in-process job pool
                                         |-- Copernicus DEM
                                         +-- ESA WorldCover

Offline ingestion: NASA LOLA / Copernicus / WorldCover
   -> QC + extracted features -> manifests -> pinned snapshot
   -> verified SQLite rebuild; optional R2/S3 snapshot transfer
```

This is the implemented MVP, not a distributed-worker or global-search design.
[詳細繁體中文 README（含搜尋與資料流程的 ASCII 架構圖）](README.zh-TW.md) ·
[Traditional Chinese architecture](docs/ARCHITECTURE.zh-TW.md) ·
[Architecture SVG](docs/diagrams/architecture.en.svg).

## Architecture / 系統架構

- [Current architecture diagram (SVG)](docs/diagrams/architecture.en.svg) and [English explanation](docs/ARCHITECTURE.md)
- [目前系統架構圖（繁體中文 SVG）](docs/diagrams/architecture.zh-TW.svg) and [繁體中文完整架構與流程說明](docs/ARCHITECTURE.zh-TW.md)

These diagrams document the current MVP, including the in-process AOI job runner, offline data pipelines, rebuildable SQLite index, optional R2 snapshot transfer, and GitHub Actions. Planned integrations are labelled separately.

## Implemented and verified now

| Capability | Status | Evidence |
|---|---|---|
| Real Earth terrain index | **113 observed windows** (original five plus twelve bounded sampling envelopes) | Copernicus DEM GLO-90 window reads; `pipelines/earth/manifests/` |
| Verified lunar reference | **18 real LOLA windows** across six south-pole sites; original Site04 ROI 2 retained | NASA PGDA `Site04_final_adj_5mpp_surf`; 5 m source → 90 m analysis; `pipelines/planetary/manifests/` |
| On-demand Earth AOI | **Working**: bbox/polygon → 202 job → Copernicus + WorldCover → persisted and searchable | `POST /api/v1/areas/analyze`, `GET /api/v1/jobs/{id}`, `GET /api/v1/areas/{id}/features` |
| Earth non-terrain context | **11 ESA WorldCover 2021 v200 class fractions** for all 113 indexed Earth windows | `pipelines/worldcover/manifests/`; context-only, not mineralogy |
| Manual target search | Weighted normalized Euclidean, fixed scales, exclusions, hard context filters, tie-break, 1–10 limit | `POST /api/v1/search` |
| Typed dimension catalog | Terrain similarity + land-cover context dimensions with role/method metadata | `GET /api/v1/dimensions` |
| Reference-mode search | Rank Earth windows against verified Moon or Mars references | `{"body":"Moon","reference_id":"moon-shackleton-rim-site04-roi2"}` |
| Mars terrain reference | **5 observed Jezero CTX windows**, 20 m source → 90 m analysis | `pipelines/planetary/ctx_import.py` |
| UI | Form, ranked list, Leaflet/OSM map, details, provenance, limitations, JSON context export | React + TypeScript |
| Reproducible snapshots | Pinned manifest + SHA-256 + offline index rebuild; optional S3/R2 publish/fetch | `samples/snapshot/`, `pipelines/build_index.py`, `ObjectStore` |
| Tests / CI / Compose | Backend/pipeline tests + frontend suite; CI jobs; Compose definitions | `scripts/verify.sh`, `.github/workflows/ci.yml` |

### Explicitly not implemented / not comparable

- Global/country-scale indexing and a global top 10.
- Temperature/illumination, mineralogy, process, biology, cave, and mission-simulation features.
- AI/LLM chat in the request path (a grounded context export exists instead).
- Physical equivalence between Earth terrain and Moon/Mars conditions.

See `docs/ROADMAP.md` for extension contracts.

## Quick start

Prerequisites: `uv` (installs Python 3.13), Node 22+, npm. No credentials are required.

```bash
uv sync
cd apps/web && npm ci && cd ..
make api          # terminal 1 -> http://127.0.0.1:8000/docs
make web          # terminal 2 -> http://localhost:5173
```

The API auto-seeds from the committed Earth, WorldCover, and Moon manifests on first start. Reset
with `rm -f var/analog.db && make seed`.

Docker Compose (web on 8080, API on 8000):

```bash
docker compose up --build
```

Offline demo index from the pinned snapshot (no network, no credentials):

```bash
make data-demo          # rebuilds ./var/demo-index.db
make data-validate      # verifies manifest/artifact checksums
bash scripts/validate_data.sh  # same offline data gate used by the data-validation CI job
```

Synthetic-only demo (loudly labelled DEMO / SYNTHETIC):

```bash
ANALOG_DATABASE_URL=sqlite+pysqlite:///./var/demo.db \
ANALOG_SEED_INCLUDE_SYNTHETIC=true \
uv run python -m analog_api.cli seed
```

## Sample workflows

### 1. Search with a manual target

```bash
curl -s http://127.0.0.1:8000/api/v1/search \
  -H 'Content-Type: application/json' \
  -d '{
    "body": "custom",
    "target": {
      "slope_mean_deg": {"value": 12, "weight": 1},
      "terrain_relief_p95_p05_m": {"value": 80, "weight": 0.5},
      "roughness_std_m": {"value": 20, "weight": 0.5}
    },
    "limit": 5
  }'
```

Historical example against the original five-window index (2026-11-14), before expansion:

| Rank | Window | Distance | Mean slope | Relief p95–p05 | Roughness | Data |
|---|---|---|---:|---:|---:|---|
| 1 | Rio Tinto, Spain | 0.3783 | 6.71° | 88.07 m | 22.20 m | observed |
| 2 | Devon Island / Haughton, Canada | 0.5228 | 4.69° | 109.61 m | 18.82 m | observed |
| 3 | Lanzarote, Spain | 0.6242 | 9.21° | 192.32 m | 40.85 m | observed |
| 4 | Atacama Desert, Chile | 1.0038 | 0.01° | 0.11 m | 0.03 m | observed |
| 5 | Death Valley, USA | 2.0195 | 17.80° | 378.41 m | 93.25 m | observed |

This historical example is **Top 5 among the original 5 indexed windows**. Current searches use 113 windows; rankings change with the indexed subset. The distance is unitless, not a
"similarity percentage".

### 2. Search against the real lunar reference

```bash
curl -s http://127.0.0.1:8000/api/v1/search \
  -H 'Content-Type: application/json' \
  -d '{"body":"Moon","reference_id":"moon-shackleton-rim-site04-roi2","limit":5}'
```

Historical ranking against the original five Earth windows (all four metrics, weight 1): Death Valley 0.2450, Lanzarote 1.699,
Rio Tinto 2.287, Devon Island 2.410, Atacama 3.045. This is an engineering matching score over
terrain shape, not a physical similarity percentage.

> Lunar caveat: the LOLA 5 m source is ~90% interpolated, with median RMS Z error ~0.30–0.50 m and
> slope error ~1.5–2.5°. It is resampled to the same 90 m/1.98 km support. Do not treat 5 m texture
> as independent evidence.

### 3. Analyze a new AOI

```bash
curl -s http://127.0.0.1:8000/api/v1/areas/analyze \
  -H 'Content-Type: application/json' \
  -d '{
    "body": "Earth",
    "bbox": {"west": -6.617, "south": 37.682, "east": -6.600, "north": 37.699},
    "requested_features": [
      "slope_mean_deg", "slope_p90_deg",
      "terrain_relief_p95_p05_m", "roughness_std_m"
    ]
  }'
# -> 202 {job_id, area_id, cache_key, status, links}
# poll: GET /api/v1/jobs/{job_id}
# read: GET /api/v1/areas/{area_id}/features
```

Observed real run: completed in ~10 s, `area_id=earth-aoi-b36533b061ce`, 4 terrain metrics,
11 WorldCover context fractions, Copernicus + WorldCover provider status `verified`; repeat request
returned `cached: true` in ~0 s. Polygon input is supported (closed outer ring, 4–100 positions).
Limits: sides 600–2000 m, area ≤ 4 km², latitude −80..84, no antimeridian crossing.

For the bounded expansion plan, commands, path configuration, and failure limitations, see
[`docs/TERRAIN_EXPANSION.md`](docs/TERRAIN_EXPANSION.md).

## Data status and coverage

| Body / layer | Status | Source |
|---|---|---|
| Earth terrain | **113 observed windows** | Copernicus DEM GLO-90, `COP-DEM_GLO-90-DGED`, AWS Open Data |
| Earth land-cover context | **11 WorldCover fractions × 113 windows** | ESA WorldCover 2021 v200, CC BY 4.0 |
| Moon terrain reference | **18 observed windows**, six LOLA sites (including original Site04 ROI 2) | NASA PGDA LOLA 5 m/pix `Site04_final_adj_5mpp_surf` |
| Mars terrain reference | **5 observed Jezero CTX windows** | USGS Mars 2020 Science Investigation CTX DEM Mosaic |

Analysis support: 1.98 km × 1.98 km window, 22 × 22 cells at 90 m, projected metric CRS,
bilinear resampling, ≥80% valid data. On-demand AOIs use a 0.6–2 km footprint at 90 m and are
flagged when their support differs. Slopes come from elevation gradients, never image colors.

Exact URLs, product IDs, licences, access timestamps, CRS/resolution, crop checksums, interpolation
and error caveats: [`docs/DATA_SOURCES.md`](docs/DATA_SOURCES.md). Dataset matrix:
[`docs/DATASETS.md`](docs/DATASETS.md).

## Supported similarity dimensions

| ID | Unit | Domain | Fixed scale | Meaning |
|---|---|---|---|---|
| `slope_mean_deg` | degree | 0–90 | 10° | mean 3×3 Horn gradient angle |
| `slope_p90_deg` | degree | 0–90 | 15° | 90th percentile gradient angle |
| `terrain_relief_p95_p05_m` | meter | 0–20000 | 200 m | p95 − p05 elevation |
| `roughness_std_m` | meter | 0–5000 | 20 m | std dev after plane detrending |
| `flat_terrain_fraction` | fraction | 0–1 | 0.25 | valid Horn slope cells <5° / valid slope cells |

Earth land-cover fractions (`landcover_*_fraction`) are **context-only**; they never enter the
distance. Not compared or simulated: gravity, atmosphere, radiation, regolith, mineralogy,
temperature, wind/dust, geological history, rover physics. See
[`docs/FEATURE_REGISTRY.md`](docs/FEATURE_REGISTRY.md) and
[`docs/SEARCH_METHOD.md`](docs/SEARCH_METHOD.md).

## Team data commands

```bash
make data-demo              # offline tiny demo index from pinned snapshot
make data-ingest-earth      # online bounded Copernicus seed windows
make data-ingest-moon       # online real LOLA ROI import
make data-analyze-aoi WEST=-6.617 SOUTH=37.682 EAST=-6.600 NORTH=37.699
make data-validate          # checksums/schema/snapshot verification
make data-rebuild-index     # verify pinned snapshot, rebuild ./var/analog.db
make data-publish           # optional R2/S3 publish (fails clearly if unconfigured)
make data-sync SNAPSHOT_ID=<id>
```

### Optional Cloudflare R2 / S3-compatible storage

The app and all offline commands work without object storage. To enable team snapshot publishing,
set these **local-only** environment values (never commit them; never expose them to the browser):

```bash
ANALOG_OBJECT_STORE_BACKEND=s3
ANALOG_OBJECT_STORE_ENDPOINT=https://<ACCOUNT_ID>.r2.cloudflarestorage.com
ANALOG_OBJECT_STORE_BUCKET=<bucket>
ANALOG_OBJECT_STORE_REGION=auto
AWS_ACCESS_KEY_ID=<R2-access-key-id>
AWS_SECRET_ACCESS_KEY=<R2-secret-access-key>
```

Then `make data-publish` uploads the snapshot and pinned JSON artifacts under immutable
`snapshots/<snapshot-id>/...` keys; `make data-sync SNAPSHOT_ID=...` verifies checksums after fetch.
See [`docs/DATA_ARCHITECTURE.md`](docs/DATA_ARCHITECTURE.md).

## API

| Endpoint | Purpose |
|---|---|
| `GET /api/v1/health` | status + data version |
| `GET /api/v1/capabilities` | similarity features, context features, AOI limits, providers, coverage |
| `GET /api/v1/dimensions` | typed dimension/feature catalog with score/filter/context roles |
| `POST /api/v1/search` | ranked subset search + exclusions + caveats |
| `POST /api/v1/areas/analyze` | on-demand bounded AOI (bbox/polygon), 202 + job |
| `GET /api/v1/jobs/{job_id}` | queued/running/completed/partial/failed + provider status |
| `GET /api/v1/areas/{area_id}/features` | persisted AOI observations + provenance + context |
| `POST /api/v1/areas/{area_id}/compare` | compare an AOI with indexed candidates using the same comparator |
| `GET /api/v1/locations/{id}` | seeded/persisted details + datasets + limitations |
| `GET /api/v1/locations/{id}/context` | grounded JSON export for external AI |
| `GET /api/v1/references` | verified planetary references (verified LOLA and Jezero CTX windows) |
| `GET /api/v1/datasets` | source/processing manifests |

See [`docs/API.md`](docs/API.md). OpenAPI snapshot: `contracts/openapi.json`.

## Tests and quality gates

```bash
make test-backend      # pytest: scoring, API, AOI jobs, persistence, raster extraction, pipelines
make test-frontend     # Vitest + React Testing Library
make verify            # full local gate (scripts/verify.sh)
make smoke             # isolated offline API smoke
```

CI runs `backend-tests`, `frontend-checks`, and `integration-smoke` with lockfile installs and no
source-data downloads. `data-validation` verifies pinned snapshots/manifests offline;
`data-smoke` (network) and `data-publish` (protected environment + R2 secrets) are separate opt-in
workflows. See [`docs/DATA_GOVERNANCE.md`](docs/DATA_GOVERNANCE.md).

The canonical cross-agent skill is
[`.agents/skills/verify-project/SKILL.md`](.agents/skills/verify-project/SKILL.md); Claude Code uses
the `.claude/skills/verify-project` relative symlink. See [`docs/TESTING.md`](docs/TESTING.md).

## Stack and repository map

React 19 + TypeScript + Vite + Leaflet · FastAPI + Pydantic v2 + SQLAlchemy 2 + SQLite · Python 3.13
+ uv · rasterio/numpy/pyproj · Docker Compose · GitHub Actions.

- `apps/api/src/analog_api/` — API, feature registry, scoring, AOI validation/jobs, persistence
- `apps/web/` — React UI
- `pipelines/src/analog_pipelines/` — shared extraction, storage/snapshot interfaces
- `pipelines/earth/`, `pipelines/worldcover/`, `pipelines/planetary/` — provider importers + manifests
- `configs/areas.yaml`, `catalog/datasets.yaml` — versioned area and source matrix
- `contracts/` — contract, manifest schema, OpenAPI snapshot
- `samples/snapshot/` — pinned demo snapshot and index summary
- `docs/` — architecture, API, data sources/pipeline, feature registry, testing, deployment, roadmap

## Documentation

[Architecture](docs/ARCHITECTURE.md) ·
[API](docs/API.md) ·
[Data architecture](docs/DATA_ARCHITECTURE.md) ·
[Data governance](docs/DATA_GOVERNANCE.md) ·
[Datasets](docs/DATASETS.md) ·
[Data sources](docs/DATA_SOURCES.md) ·
[Data pipeline](docs/DATA_PIPELINE.md) ·
[Data model](docs/DATA_MODEL.md) ·
[Feature registry](docs/FEATURE_REGISTRY.md) ·
[Search method](docs/SEARCH_METHOD.md) ·
[Testing](docs/TESTING.md) ·
[Deployment](docs/DEPLOYMENT.md) ·
[Roadmap](docs/ROADMAP.md) ·
[R2 setup](docs/R2_SETUP.md) ·
[Agent guide](AGENTS.md) ·
[Contributing](CONTRIBUTING.md) ·
[Contributing data](docs/CONTRIBUTING_DATA.md)

## License and attribution

Code: MIT (`LICENSE`). Copernicus DEM: produced using Copernicus WorldDEM-90 © DLR e.V. 2010–2014
and © Airbus Defence and Space GmbH 2014–2018, provided under COPERNICUS by the European Union and
ESA; all rights reserved. ESA WorldCover: CC BY 4.0, © ESA WorldCover project 2021 / Contains
modified Copernicus Sentinel data (2021) processed by ESA WorldCover consortium. NASA LOLA: NASA-led
mission data; cite Barker et al. (2021), doi:10.1016/j.pss.2020.105119. Basemap © OpenStreetMap
contributors.

## Limitations

This is an MVP over a small curated index. It does not search all of Earth, does not provide a global
top 10, does not simulate rover physics, and does not establish environmental equivalence. The
on-demand AOI is a 2 km-class sample, not a characterization of a whole region. Synthetic fixture
values are never mixed into the observed index unless the explicit demo flag is set, and are labelled
in the API and UI.
