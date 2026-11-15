# Product backend acceptance (offline handoff)

Verified 2026-11-15 against current `main` and the public deployment. This document describes
what the backend and UI can demonstrate **today** with committed real data. It does not claim
global coverage, six-dimensional measurement completeness, or a validated combined score.

## Scenarios and actual state

| Scenario | Endpoint / command | Result | Evidence |
|---|---|---|---|
| Legacy indexed terrain search | `POST /api/v1/search` | 113 Earth windows / 23 references (18 Moon, 5 Mars) | `test_api.py`, `test_real_manifest.py` |
| Moon/Mars reference → Earth candidates | `POST /api/v1/search` with `reference_id` | Works; reference target from `GET /references` | `test_api.py`, screenshot `07` |
| On-demand Earth AOI | `POST /api/v1/areas/analyze` + `GET /jobs/{id}` | Queued job, cache, partial/failed states, real Copernicus/WorldCover reads | `test_aoi.py`; live run below |
| Coarse global discovery | `GET /api/v1/global/coverage`, `POST /global/search` | Real 168 cells / 3 pilot regions; deterministic top-K, exclusions, region filter | `test_global_api.py`, `docs/GLOBAL_TERRAIN_RESULTS.md` |
| Coarse candidate detail | `GET /global/candidates/{id}` | Values, QA, source, snapshot/method/grid versions | `test_global_api.py` |
| Refine plan | `POST /global/refine-plan` | Plans 1.98 km windows; no execution by itself | `test_global_api.py` |
| **Execute refined window** | UI → `POST /areas/analyze` → `GET /jobs/{id}` → `GET /areas/{id}/features` | **Live completed run** with 5 real 90 m features | screenshot `10`, E2E doc |
| D02 evidence | `GET /typed/observations?dimension=D02` | 56 records: map units, process evidence, CRISM context; 0 abundance | `docs/MINERAL_EVIDENCE.md`, `docs/GEOLOGY_AREA_FRACTION.md` |
| D03 thermal | `GET /typed/observations?dimension=D03` | 61: Earth POWER 20, Moon Diviner 36, Mars THEMIS 5 | `docs/D03_PLANETARY_THERMAL_PILOT.md` |
| D04 water/ice | `GET /typed/observations?dimension=D04` | 75: Earth POWER 20, Moon LP NS/Mini-RF 53, Mars HEND 2 | `docs/D04_WATER_ICE_PILOT.md` |
| D05 illumination | `GET /typed/observations?dimension=D05` | 28: Earth POWER 10, Moon SPICE 8, Mars SPICE 10 | `docs/D05_SPICE_PILOT.md` |
| D06 regolith/soil | `GET /typed/observations?dimension=D06` | 1,920: SoilGrids 5 properties × 4 depths × 3 statistics × 5 AOIs plus 0–5 cm × 5 properties × 3 statistics × 108 AOIs | `docs/D06_SOILGRIDS_DEPTH_PILOT.md` |
| Planetary coarse evidence | `pipelines/planetary/coarse/index.json` | 4 Moon + 4 Mars CTX + 2 MOLA cells; cross-body ranking disabled | `docs/PLANETARY_COARSE_REFERENCE.md` |
| Aggregate release | `pipelines/build_release.py verify` | Deterministic manifest/EDA; tamper rejection | `docs/MULTIDIMENSIONAL_RELEASE.md` |
| DB capacity/backup | `scripts/db_audit.py`, `db_backup.py drill` | Measured local DB + 100k/1M synthetic benchmarks; copy-only restore | `docs/DB_CAPACITY_REPORT.md` |
| PostGIS parity | opt-in live test | SQLite and PostGIS return identical fixture results/order/pagination | `docs/POSTGIS.md` |

## Stable curl examples

```bash
# Moon reference → Earth candidate terrain search
curl -s localhost:8000/api/v1/search -H 'content-type: application/json' -d '{
  "body":"Moon","reference_id":"moon-shackleton-rim-site04-roi2","limit":5
}'

# real coarse index coverage and top candidates
curl -s localhost:8000/api/v1/global/coverage
curl -s localhost:8000/api/v1/global/search -H 'content-type: application/json' -d '{
  "target":{"coarse_slope_mean_deg":{"value":8,"weight":1}},
  "limit":5
}'

# refine plan for a real candidate
curl -s localhost:8000/api/v1/global/refine-plan -H 'content-type: application/json' -d '{
  "candidate_id":"coarse-v1:95046a41aeb31ca9","per_side":1
}'

# execute one bounded 1.98 km window through the restricted AOI job API
curl -s localhost:8000/api/v1/areas/analyze -H 'content-type: application/json' -d '{
  "body":"Earth",
  "bbox":{"west":10.071,"south":24.0942,"east":10.0907,"north":24.1119}
}'
curl -s localhost:8000/api/v1/jobs/<job_id>
curl -s localhost:8000/api/v1/areas/<area_id>/features

# typed six-dimension coverage and records
curl -s localhost:8000/api/v1/typed/coverage
curl -s 'localhost:8000/api/v1/typed/observations?dimension=D03&body=Moon'
```

## Contract semantics

- `no_indexed_data` (409) when no coarse index is configured; `not_found` (404) for unknown
  references/areas/jobs; `422` for invalid coarse targets or geometry.
- Global coverage is labelled `local_coarse_index_not_global_exhaustive`; the 168-cell pilot is
  never presented as a global survey.
- Coarse 250 m preprocessed slope and fine 90 m terrain features have different IDs, method
  versions and support. A cross-method comparison is rejected rather than rescaled.
- Missing states (`not_detected`, `no_coverage`, `unknown`, `not_applicable`) carry no numeric
  value; physical zero is a valid detected value.
- D02–D06 observations expose `state`, `value_kind`, `score_role`, units, native support,
  time/depth, QA, method and provenance. No universal six-dimension score is produced.
- Executed 90 m results are AOI-job outputs, not new coarse ranks; the UI labels this.
- Existing v1 endpoints, 113/18/5 terrain observations, snapshots and user AOIs remain intact.

## Frontend handoff

| UI surface | API contract | Required display |
|---|---|---|
| Search target / results | `GET /references`, `POST /search` | body/reference, candidate rank, distance, origin, coverage |
| Six dimensions | `GET /dimensions`, `GET /typed/areas/{id}`, `GET /typed/observations` | state, role, value kind, units, support, time/depth, QA, limitations |
| Global discovery | `GET /global/coverage`, `POST /global/search`, `GET /global/candidates/{id}`, `POST /global/refine-plan` | coverage label, regions, snapshot/method/grid, exclusions, coarse values |
| Execute refine | `POST /areas/analyze`, `GET /jobs/{id}`, `GET /areas/{id}/features` | job status, area, provider status, executed features, warnings |
| Errors | `ErrorOut` | code, message, retry; never substitute fixtures |
| Empty/missing | typed states, `no_index` | explicit “no reliable data” copy, no zero-fill |

The React UI already implements these views; see `apps/web/src/components/{SearchForm,ResultsPanel,
SixDimensionPanel,GlobalPanel}.tsx` and the public screenshots.

## Offline acceptance commands

```bash
uv run pytest apps/api/tests pipelines/tests -q
bash scripts/backend_acceptance.sh
bash scripts/validate_data.sh
bash scripts/verify.sh --ci
```

Do not run online ingestion or `scripts/online_smoke.sh` as part of offline acceptance.

## Known limitations

- No global coarse index; three 0.4° pilot regions only.
- Planetary coarse methods are `evidence_only`; MOLA is `not_comparable`; cross-body ranking is disabled.
- D02 has no validated quantitative mineral abundance; EMIT/M3/CRISM still need granule-level work.
- D03 brightness temperature is not kinetic temperature; Mars inertia has no per-pixel uncertainty.
- D04 has no confirmed ice detection; neutron/radar cells are coarser than the AOI windows.
- D05 has no observed irradiance/shadow and no ionizing radiation/dose.
- D06 is model prediction; Moon/Mars regolith remains `no_coverage`.
- Executed refine is per-window and bounded; no recall/global-best claim.
