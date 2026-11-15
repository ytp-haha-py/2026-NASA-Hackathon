# D02 Geology & Mineralogy — Earth/Moon/Mars Coverage Matrix (Issue #136)

- **Dimension:** D02 Geology & Mineralogy
- **Artifact (machine-readable):** `pipelines/geology/d02-coverage-matrix.json` (`artifact: d02-coverage-matrix`, `version: 1.0.0`)
- **Representative queries (machine-readable):** `pipelines/geology/representative-queries.json`
- **Scope:** bounded to the committed, indexed/queryable D02 typed export and cited audits in this repository/worktree. **Not** a global or exhaustive survey, **not** a similarity score, **not** mineral abundance.

## Non-claims and rules (enforced in artifacts)

- `global_coverage_claimed: false`, `exhaustive_survey_claimed: false`, `similarity_score_claimed: false`.
- Categorical map units are **never bilinearly interpolated**. No crosswalk/taxonomy is invented where unjustified; original source codes/descriptions are retained as evidence.
- **Detection / abundance / spectral index are separated:** `mineral_*` features are detection-state records (all `unknown` in this export; not `not_detected`, not abundance). `context_*` are dimensionless spectral parameters (context-only). No abundance/wt% is exported.
- Missing stays missing: `no_coverage`, `unsupported`, `unknown`, `not_applicable` are explicit and never zero-filled.
- All D02 records are `score_role: evidence_only`; `similarity_weighted: false`, `hard_filter: false`.

## Body × feature matrix (summary)

The full body × feature matrix with required fields (source, version, licence, product, actual indexed/queryable coverage, measured/modeled/derived/evidence/no_coverage/unsupported classification, states/counts, time/depth/spatial support, CRS/frame, QA, uncertainty, legal score/filter roles, gaps, rebuild instructions) is in `pipelines/geology/d02-coverage-matrix.json` (45 rows = 15 features × 3 bodies).

| Body | Indexed AOIs | Indexed/queryable D02 observations | Notes |
|---|---:|---:|---|
| Earth | 8 (`earth-atacama-desert`, `earth-death-valley`, `earth-devon-island`, `earth-grand-canyon-r2c2`, `earth-iceland-r2c2`, `earth-lanzarote`, `earth-namib-r2c2`, `earth-rio-tinto`) | 18 | EMIT L2B MIN V002/V001 availability records (15) plus 3 Macrostrat point-query `geologic_unit` evidence records (2026-11-15, CC-BY 4.0, no taxonomy crosswalk). GLiM mirror values withheld. No validated mineral detection/abundance. |
| Moon | 1 (`moon-shackleton-rim-site04-roi2`) | 3 | USGS Unified Geologic Map v2: 1 map unit + 1 process record (evidence). Lunar mineral/analysis availability `unknown` (M3 registration/thresholds unvalidated; research matrix unusable for AOI values). 1:5M map; local composition not resolved. |
| Mars | 2 (`mars-test-jezero-floor`, `mars-test-jezero-western-delta`, explicitly marked test AOIs) | 38 | USGS SIM 3464 Jezero/Nili (1:75,000): 10 map-unit + 4 process records. CRISM MTRDR: 14 context numeric spectral parameters (7×2 AOIs, context-only) + 10 mineral detection records all `unknown`. No validated mineral detection/abundance. Not a Mars reference, not global. |

**Feature inventory (indexed):** `geologic_unit`, `geologic_process`, `mineral_olivine`, `mineral_pyroxene`, `mineral_clay_minerals`, `mineral_hydrated_sulfates`, `mineral_carbonates`, `context_olindex3`, `context_lcpindex2`, `context_hcpindex2`, `context_d2300`, `context_bd1900_2`, `context_sindex2`, `context_cindex2`, `geology_analysis_availability` (all `score_role: evidence_only`).

## Actual indexed/queryable coverage by body × feature (condensed)

| Body | Feature | Obs. count | States | Actual coverage / class |
|---|---|---:|---|---|
| Earth | `geologic_unit`, `geologic_process` | 3, 0 | `detected` | 3 Macrostrat point-query map units (evidence-only, no crosswalk); GLiM mirror values still withheld |
| Earth | `mineral_*` (5) | 0 each | — | `unsupported_no_indexed_observation` (EMIT auth/decoder/native-swath blockers) |
| Earth | `geology_analysis_availability` | 15 | `unknown`×13, `no_coverage`×2 | indexed/queryable evidence-only availability records |
| Moon | `geologic_unit`, `geologic_process` | 1, 1 | `detected` | indexed/queryable mapped categorical evidence (1 ROI, 1:5M) |
| Moon | `mineral_*` (5) | 0 each | — | `unsupported_no_indexed_observation` |
| Moon | `geology_analysis_availability` | 1 | `unknown` | indexed/queryable evidence-only availability/blocker record |
| Mars | `geologic_unit`, `geologic_process` | 10, 4 | `detected` | indexed/queryable mapped categorical evidence (2 Jezero test AOIs) |
| Mars | `mineral_*` (5) | 2 each | `unknown`×2 | indexed/queryable detection-state records (not validated; no abundance) |
| Mars | `context_*` (7) | 2 each | `detected` | indexed/queryable dimensionless spectral context (not detection/abundance) |
| Mars | `geology_analysis_availability` | 0 | — | `unsupported_no_indexed_observation` (CRISM context/mineral records present instead) |

## Real representative queries (all three bodies where data exists)

Derived from the committed typed export (`pipelines/geology/representative-queries.json`, reproducible read of `pipelines/geology/typed/observations.jsonl`):

| Query | Body | Result count | States | What it demonstrates |
|---|---|---:|---|---|
| `Q-EARTH-availability` | Earth | 15 | `unknown`, `no_coverage` | Earth EMIT availability evidence/blockers per AOI/version (no mineral values). |
| `Q-EARTH-macrostrat-units` | Earth | 3 | `detected` | Macrostrat point-query map units for 3 Earth fine candidates; evidence-only, no crosswalk. |
| `Q-MOON-mapped-units` | Moon | 2 | `detected` | Moon mapped unit + process evidence for Shackleton ROI. |
| `Q-MOON-mineral-availability-blocker` | Moon | 1 | `unknown` | Moon mineral/analysis availability blocked/unverified (no detection claim). |
| `Q-MARS-jezero-mapped-units` | Mars | 8 | `detected` | Mars Jezero floor mapped units/processes (bounded test AOI). |
| `Q-MARS-crism-mineral-detections` | Mars | 10 | `unknown` | Mars CRISM mineral detection records all unknown (not not_detected). |
| `Q-MARS-crism-spectral-context` | Mars | 7 | `detected` | Mars CRISM spectral parameter context values for one AOI (dimensionless, context-only). |

Reproduce queries (offline): `uv run python scripts/build_d02_representative_queries.py`

## Bounded reproducible ingest/index / blockers

- **Existing bounded ingest/index artifacts (committed, reproducible):** geology snapshot/records, typed-v2 export (`pipelines/geology/typed/*`), mineral evidence (`pipelines/geology/mineral-evidence.json`), area-fraction evidence (`pipelines/geology/area-fraction/evidence.json`), and this coverage matrix/queries.
- **Verified blockers / alternative sources:** documented per source in `docs/data-sources/d02-geology.json` and per body×feature in the matrix (e.g. EMIT `emit_auth`/`emit_decoder`/`emit_native_swath`; M3 `m3_registration`/`m3_thresholds`; CRISM thresholds/continuum; GLiM mirror licence unverified with PANGAEA CC BY 3.0 0.5° coarse alternative identified; SIM 3292 identified but large/gated). No global high-resolution download performed.
- **Rebuild (offline, no source download):**
  - `uv run python -m analog_pipelines.geology verify`
  - `uv run python scripts/build_mineral_evidence.py`
  - `uv run python scripts/export_geology_typed.py --record pipelines/geology/records/geology-e920e7e3ae79e176.json --output pipelines/geology/typed`
  - `uv run python scripts/build_d02_coverage_matrix.py`
  - `uv run python scripts/build_d02_representative_queries.py`
- **Re-ingest/recompute from bounded source caches (external data root, no global download):** `export GEOLOGY_DATA_ROOT=/absolute/path/outside/repo && uv run --with-requirements pipelines/geology/requirements-ingest.txt python -m analog_pipelines.geology analyze --offline` (or audit/analyze per `pipelines/geology/README.md`).

## Provenance and inputs

Input checksums and pinned IDs are recorded in `pipelines/geology/d02-coverage-matrix.json` under `inputs` (typed manifest/observations, coverage audit, source audit, mineral evidence, area-fraction evidence). Typed export source record: `pipelines/geology/records/geology-e920e7e3ae79e176.json`; snapshot `geo-snap-d1235d5f9defe6cd`; typed observations SHA-256 `72290f85695744b75d4bba87dccbdeafb938bb840d33873a11757f22e1c362e5` (59 observations, 11 AOIs). The Macrostrat expansion is a bounded third-party point query (`scripts/ingest_d02_macrostrat.py`), not a local measurement. Subset-based, bounded pilot — do not interpret as global scientific coverage.
