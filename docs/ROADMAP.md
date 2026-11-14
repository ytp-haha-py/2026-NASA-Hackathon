# Roadmap and extension contracts

Items below are deferred, except where explicitly marked implemented. Each item is sized for a
follow-up Issue and names its interface touchpoint. Do not claim an item is implemented until its
acceptance criteria pass.

## 1. Mission preset registry (P1, not implemented)

**Scope:** `rover_traversal`, `stationary_payload`, `sample_return_traverse` presets that map a
mission to supported terrain weights with rationale.
**Contract:** new `GET /api/v1/missions` (or static registry exposed in capabilities) returning
`{mission_id, display_name, rationale, weights, limitations}`. The preset MUST call
`POST /search` with the same request shape; no second ranking algorithm.
**Prerequisites:** weight rationale reviewed for each preset; tests prove the request body matches
manual mode.
**Acceptance:** selecting a preset produces the same JSON body the manual form would produce;
UI labels presets as weighting choices, not scientific mission simulation.

## 2. Verified planetary reference ROIs (Moon implemented; Mars future)

**Status:** the Moon reference is implemented: `moon-shackleton-rim-site04-roi2` from NASA LOLA
Site04 5 m/pix, resampled to the 90 m/1.98 km support. Mars remains unverified.

**Scope:** add further real LOLA ROIs and PDS MOLA (Mars) with the shared extractor.
**Contract:** existing `GET /references`, `POST /search` reference mode, and
`reference_feature_observations`.
**Prerequisites:** source URL, license, CRS, resolution, vertical uncertainty, interpolation
fraction, and ROI definition documented; identical method version; tests on a real derived manifest.
**Acceptance:** reference appears only after a real raster window was read and validated; UI enables
the body selector only then; mixed method versions are rejected.

## 3. Global/country tile indexing (not implemented)

**Scope:** batch indexing of many Copernicus tiles with resumable jobs, deduplication, and a
manifest per batch.
**Contract:** `CandidateRepository` remains unchanged; add an ingestion job runner and batch
manifests. Search still returns subset coverage with batch metadata.
**Prerequisites:** storage sizing, egress budget, tile selection policy, and a PostGIS option.
**Acceptance:** reproducible batch import; partial failures recorded; UI coverage reflects actual
indexed cells, not a country claim.

## 4. PostGIS adapter (not implemented)

**Scope:** replace SQLite for multi-user/production scale.
**Contract:** implement `ports/repository.py` Protocols; keep Pydantic/HTTP unchanged.
**Prerequisites:** Alembic migrations, spatial indexes, backup/restore runbook.
**Acceptance:** contract tests pass against both adapters; search results identical for a fixture.

## 5. Raster uncertainty and quality propagation (LOLA caveats visible; layers not imported)

**Scope:** import LOLA/SRTM uncertainty layers and DEM quality masks; propagate to observations.
**Contract:** optional `uncertainty` field on observations; result caveats and exclusion rules.
**Prerequisites:** documented uncertainty definitions and scale; no invented error bars.
**Acceptance:** UI distinguishes measured, interpolated, and uncertain cells; comparisons can
exclude high-uncertainty windows.

## 6. Better similarity models

**Scope:** evaluate alternatives (Gower distance, rank-based, learned weights) with a documented
benchmark and sensitivity analysis.
**Contract:** `RankingStrategy` protocol and `query_version` bump; old behavior remains available
for reproducibility.
**Prerequisites:** benchmark dataset, acceptance metrics, and review by a domain expert.
**Acceptance:** no false "percentage" claims; results reproducible with the same data version.

## 7. Citations and ChatGPT context connector

**Scope:** a real external AI adapter that consumes `GET /locations/{id}/context` and preserves
citations/limitations; per-result citation export.
**Contract:** context export remains the source of truth; adapter never invents facts.
**Prerequisites:** provider terms, privacy policy, prompt-injection review, and explicit user action.
**Acceptance:** generated text cites stored source facts and refuses claims not present in the
export; no API keys in the browser.

## 8. Earth candidate metadata enrichment (WorldCover context implemented)

**Scope:** link candidates to protected-area status, land cover, climate, and access constraints
from documented sources.
**Contract:** additive metadata fields on locations; `data_kind`/provenance per attribute.
**Prerequisites:** license-compatible sources and attribution requirements.
**Acceptance:** every displayed attribute has a source ID and timestamp; unknown stays unknown.

## 9. Optional 3D visualization

**Scope:** terrain preview from the same DEM window, clearly labelled as visualization, not
evidence.
**Contract:** static derived mesh/tiles generated offline; no request-path downloads.
**Prerequisites:** size budget and attribution.
**Acceptance:** visualization does not alter ranking or imply environmental equivalence.

## v6 follow-ups

- **More on-demand providers:** MOD11A2 surface temperature and Diviner thermal context only after
  a documented comparability/normalization method; do not add them to the terrain distance.
- **AOI polygon masking limits:** MVP rejects holes and antimeridian crossing; extend with explicit
  tests and geometry area validation.
- **Object storage:** exercise the S3/R2 adapter against a real bucket and record snapshot
  publish/fetch evidence. Add a scoped DVC migration Issue if the team needs large artifact sync.
- **Global scan:** candidate-generation scan over a stated region, with resumable jobs and explicit
  coverage/batch metadata; still must not claim global top 10.
- **Hard constraints:** implement pre-ranking filters (e.g. land-cover exclusions) as a distinct
  contract from weighted similarity.

## Governance follow-ups

- Enable GitHub ruleset on `main` with required checks after the first CI run (see
  `docs/GITHUB_SETUP.md`).
- Add an independent reviewer/CODEOWNERS once a real collaborator is confirmed.
- Add Alembic before the first persistent schema change.
- Add Playwright only when the browser runtime is already available; keep the fast suite offline.
