# Frontend handoff — Goal H (#170) integration E2E QA (strengthened)

Audience: #119/#123 frontend collaborators. **Integration/handoff only; no UI code changed.**  
Owned deliverables: `apps/api/tests/test_planetary_aoi_e2e_170.py`, `scripts/accept_planetary_aoi_170.sh`, `docs/FRONTEND_HANDOFF_170.md`.

## Scope, compatibility, and honesty limits
- **Contracts:** compatible with #132/#160 planetary/Earth AOI, jobs, typed areas, and map-features surfaces used by this integration (`/api/v1/planetary/areas/analyze`, `/api/v1/jobs/{job_id}`, `/api/v1/typed/areas/{area_id}`, `/api/v1/planetary/map-features`, `/api/v1/earth/map-features`). Source of truth: `apps/api/src/analog_api/schemas.py`, `contracts/openapi.json`, `apps/web/src/api/schema.d.ts`.
- **Evidence source:** committed manifests/fixtures and isolated temporary SQLite DBs only; offline (`ANALOG_PLANETARY_ALLOW_NETWORK=false`), no R2/shared DB/raw raster downloads.
- **Not global/exhaustive:** bounded pilot coverage only. **No claim of global or exhaustive six-dimension coverage.**
- **Planetary on-demand science scope:** only verified D01 coarse terrain recomputation (`coarse_slope_mean_deg`, `coarse_slope_p90_deg`, `coarse_flat_fraction_lt5deg`). D02–D06 are explicit `unsupported_for_on_demand_analysis` with `value: null`/reason — never measured, never zero-filled, never scored. No cross-body score.

## Committed pilot coverage and per-case feasibility proof (real offline sources)
| Body/source | Committed manifest/index region | Cells / bbox (lon,lat) | Lat band | Successful real D01 analysis possible offline |
|---|---|---|---|---|
| Moon (analysis-eligible) | `pipelines/planetary/coarse/moon_lola128_window01.json` (`moon-lola128-eqc-window-01`, `moon-lola128-pa-pixel-202405`, fixtures `samples/planetary/coarse/fixtures/moon_*.tif`) | 4 cells, union ~19.833–20.168E, -10.165 to -9.835S | **Low latitude (~10S)** | **Yes**, within committed bbox/cell union only |
| Mars (analysis-eligible, comparable CTX) | `pipelines/planetary/coarse/mars_ctx_jezero_window01.json` (`mars-ctx-jezero-window-01`, `mars2020-jezero-ctx-dem-20m`, fixtures `samples/planetary/coarse/fixtures/mars_*.tif`) | 4 cells, ~78.082–78.260E, 18.473–18.642N | **Low/mid latitude (~18.5N)** | **Yes**, within committed bbox/cell union only |
| Mars (evidence-only, not comparable) | `pipelines/planetary/coarse/mars_mola_meg128_evidence.json` (`mars-mola-meg128-evidence-01`) | 2 cells, same Jezero area (~18.47–18.64N), 463m native support | Low/mid (~18.5N) | **No** — evidence-only, excluded from on-demand comparable D01 analysis |

**Proof of unavailability:** committed `pipelines/planetary/coarse/` contains only `index.json`, `moon_lola128_window01.json`, `mars_ctx_jezero_window01.json`, and `mars_mola_meg128_evidence.json` (no other Moon/Mars coarse pilot manifests/cells/fixtures). Therefore there are no committed analysis-eligible Moon mid/high-lat, Mars low-lat-outside-pilot/high-lat, polar, dateline-crossing-in-coverage, or in-coverage nodata-only regions for successful real offline analysis. **Per-case:** the only successful real on-demand analyses possible from committed offline sources are the 6 committed-pilot AOIs (Moon: all low-lat ~10S; Mars: all low/mid-lat ~18.5N). All other required cases are mapped to **explicit, non-fabricated outcomes** (rejection or failed job/error, `result` empty/null, no fabricated values, no persisted fake typed areas). This does not claim global or exhaustive six-dimension coverage.

## Required-case evidence mapping (Goal H)

| Required case | Body | Reproducible evidence (test/script) | Outcome |
|---|---|---|---|
| Moon real committed AOI 1 (SW cell) | Moon | `test_moon_real_aoi_e2e_three_committed_cases[moon_committed_real_1]` | **Success** (completed/partial), real D01 features from committed fixture, low-lat pilot |
| Moon real committed AOI 2 (NE cell) | Moon | `... [moon_committed_real_2]` | **Success**, low-lat pilot |
| Moon real committed AOI 3 (full block) | Moon | `... [moon_committed_real_3]` | **Success**, low-lat pilot |
| Mars real committed AOI 1 (SW cell) | Mars | `test_mars_real_aoi_e2e_three_committed_cases[mars_committed_real_1]` | **Success**, low/mid-lat pilot |
| Mars real committed AOI 2 (NE cell) | Mars | `... [mars_committed_real_2]` | **Success**, low/mid-lat pilot |
| Mars real committed AOI 3 (full block) | Mars | `... [mars_committed_real_3]` | **Success**, low/mid-lat pilot |
| Nodata / out-of-coverage | Moon, Mars | `test_planetary_required_nodata_and_lat_band_explicit_non_fabricated[moon_nodata_out_of_coverage|mars_nodata_out_of_coverage]` | **Explicit non-fabricated failure** (422/503 or failed job with coverage reason), `result` empty/null, no persisted fake typed area. No committed in-coverage nodata-only AOI exists for successful real analysis. |
| Low/mid/high latitude coverage requests | Moon | `... [moon_midlat_outside_committed]` (45N), `... [moon_highlat_outside_committed]` (60N) — committed Moon coverage is low-lat only | **Explicit non-fabricated failure** (outside committed pilot). Successful real Moon mid/high-lat analysis not possible from committed offline sources. |
| Low/mid/high latitude coverage requests | Mars | `... [mars_lowlat_outside_committed]` (near equator, outside pilot), `... [mars_highlat_outside_committed]` (60N) — committed Mars coverage is low/mid-lat only | **Explicit non-fabricated failure** (outside committed pilot). Successful real Mars low-lat-outside-pilot/high-lat analysis not possible from committed offline sources. |
| Polar (極區) | Moon, Mars | `test_planetary_required_polar_explicit_non_fabricated[moon_polar|mars_polar]` | **Explicit non-fabricated outcome** (not silently accepted as data). No committed polar pilot coverage exists for successful real analysis. |
| Dateline/antimeridian crossing (換日線) | Moon, Mars | `test_planetary_required_dateline_crossing_explicit_non_fabricated[moon_dateline_crossing|mars_dateline_crossing]` | **Explicit non-fabricated outcome** (planetocentric crossing semantics; no silent acceptance/fabrication). No committed crossing pilot coverage exists for successful real analysis. |
| Oversized AOI (過大 AOI) | Moon, Mars | `test_planetary_required_oversized_aoi_rejected[Moon|Mars]` | **Explicit 422 `aoi_too_large`** (bounded analysis cap). |
| Source outage (來源斷線) | Moon, Mars | `test_planetary_required_source_outage_explicit[Moon|Mars]` (simulated provider boundary outage, offline; no live network disconnect) | **Explicit failed job** (`source_extraction_failed`, error/provider status), no fake success, no persisted typed area. |
| Cache/resume/idempotency | Moon | `test_planetary_cache_resume_and_idempotency` | Cached repeat by bbox/cache key and idempotency key; async runner recovery (`running -> queued`) completes |
| API/OpenAPI/TS + #132/#160 surface compatibility | - | `test_openapi_and_types_contract_compatibility_170` | Paths/schemas present in runtime+committed OpenAPI; TS markers in `schema.d.ts`; live response shape sanity |

## API request/response/types/error (frontend consumption)

### Request: `POST /api/v1/planetary/areas/analyze` (`PlanetaryAoiAnalyzeRequest`)
```ts
{
  body: "Moon" | "Mars"; // planetocentric frame, never Earth EPSG:4326
  bbox: { west: number; south: number; east: number; north: number };
  requested_features?: Array<"coarse_slope_mean_deg" | "coarse_slope_p90_deg" | "coarse_flat_fraction_lt5deg">;
  idempotency_key?: string; // max 128, reuse per user action/retry
}
```

### Response: `AoiAnalyzeResponse` (analyze) / `JobOut` (`GET /api/v1/jobs/{job_id}`)
- Analyze: `job_id`, `status` (`queued|running|completed|partial|failed`), `area_id`, `cache_key`, `cached`, `provider_status`, `result|null`, `links{job,typed_area?,map_features?}`, `body`, `coordinate_frame`, `method_version`, `coverage_note`, `dimension_support`.
- Job: `job_id`, `status`, `area_id`, `body`, `coordinate_frame`, `provider_status`, `error{code,message}|null`, `result`, timestamps.
- Successful result (D01): `features` with 3 coarse IDs (degree/fraction), `dimension_support.D01` present with method/source method lineage; `D02-D06` unsupported with reason and `value:null`; `analysis_kind` (e.g. `committed_reference_fixture`), sources/warnings/coverage note as provided.
- **Do not** treat `dimension_support` unsupported as zero/measured, do not compute cross-body totals, and keep Earth/planetary frame separation.

### Error/outcome handling
| State | Expected UI/backend behavior |
|---|---|
| `queued/running` (202 or async) | Poll `links.job`/`/api/v1/jobs/{job_id}` with backoff; no invented metrics |
| `completed/partial` | Render only returned values + method/provenance/warnings/coverage note; show partial visibly |
| `failed` | Show `error.code/message` or explicit provider reason; do not open as persisted typed area unless detail confirms; allow retry with same idempotency key |
| 422 `aoi_too_large` | Oversized/bounded-cap rejection |
| 422/503 or failed job (coverage/nodata) | Explicit out-of-coverage/nodata-equivalent; no fabricated observations, no fake persisted typed area |
| 422 `unsupported_body` / `unsupported_feature_on_body` | Invalid request selection |

Error envelope: `{ error: { code, message, details[] } }`.

## CI / perf-resource budget / cache-resume
- CI jobs (`.github/workflows/ci.yml`): backend lint/format/pytest (includes `apps/api/tests`), frontend checks (incl. `npm run check:api`), offline integration smoke. Goal H test is CI-discoverable under `apps/api/tests/`.
- Offline acceptance budget posture: isolated temp SQLite/runtime, bounded committed pilots only, no network/provider downloads, timeout-bounded command execution; script reports temp artifact sizes. This is evidence visibility, not a production/global capacity claim.
- Cache/resume: cache key/idempotency reuse durable job/result; failed cache-path retry semantics in job store; runner recovery requeues interrupted running jobs.

## Reproduction
```bash
bash scripts/accept_planetary_aoi_170.sh
uv run pytest apps/api/tests/test_planetary_aoi_e2e_170.py -q -ra
# OpenAPI drift is verified inline by the acceptance script (no file writes).
```

## Handoff notes (#119/#123)
- Follow `links`/`detail_links`; keep planetary (`lunar_planetocentric`/`mars_planetocentric`) separate from Earth flows.
- Show support/provenance labels (committed fixture, bounded pilot, method/source method) and never relabel unsupported/coarse as fine/global/measured.
- Treat missing/changed fields as contract mismatches; do not silently hide scientific/support meaning.
