# Phase 2 acceptance report (Issue #142)

Status: **implemented and verified locally at `8536805` plus the bounded D01–D05 ingest expansion
(2,185 typed observations, Mars references 7, local data version `dv-6fefaf3a7974`).** The public
API still serves the previous snapshot `dv-b46f3bad38ea` until a separate deployment is confirmed.
This report closes the Phase 2 coordination scope. It does not claim global scientific coverage and
does not take over #97/#108/#132 or the frontend #118/#119/#123.

## Workstream outcome

| Issue | Goal | Session | Phase PR | Goal | Validated | Integrated |
|---|---|---|---|---|---|---|
| #135 D01 | complete | `ses_e20fc8691ffeaMvQ1pjnNWscZp` | #147 | complete | yes (matrix/tests reviewed) | yes |
| #136 D02 | complete | `ses_e20fc69adffe9JAGca55A3aX5R` | #148 | complete | yes | yes |
| #137 D03 | complete | `ses_e20fc6930ffeoPYLni2B8ZosUx` | #146 | complete | yes | yes |
| #138 D04 | complete | `ses_e20fc68bbffe2Sar4coxcaeuS7` | #144 | complete | yes | yes |
| #139 D05 | complete | `ses_e20fc684affe5mrOOQ6dvy5Ju5` | #145 | complete | yes | yes |
| #140 D06 | complete | `ses_e20fc67c3ffeEHNB8Ra3Q5CLqP` | #150 | complete | yes | yes |
| #141 Integration | complete | `ses_e20fc6751ffeXuPBKd6WZyqGcN` | #152 | complete | yes (independent backend checks + public smoke) | yes |
| #142 Coordinator | active | `ses_e253e85e9ffeLjB1ipw0gIVen0` | #143/#154/#157 | active | this report | board/monitor merged |

Native Goal IDs and worktrees are in [`WORKSTREAM_BOARD.md`](WORKSTREAM_BOARD.md) and
[`workstreams.json`](workstreams.json). Every primary session has a synthetic
`{"source":"goal","goalID":"..."}` message produced by the native `goal` command, not a plain
prompt.

**Issue closure vs phase completion:** #140, #141 and #142 are closed. #135–#139 remain open so
additional bounded data ingest can continue under the same visible coverage matrices; their
Phase-2 Goal, validation and PR integration are complete. Closing them now would incorrectly imply
that the documented data gaps (new D01–D05 ingest, Moon/Mars numeric regolith) no longer exist.

## Coverage matrices

| Dimension | Machine-readable | Human-readable |
|---|---|---|
| D01 | `docs/data-sources/d01-coverage-matrix.json` | `docs/D01_TERRAIN_COVERAGE.md` |
| D02 | `pipelines/geology/d02-coverage-matrix.json` | `docs/D02_COVERAGE_MATRIX.md` |
| D03 | `docs/data-sources/d03-thermal-coverage-matrix.json` | `docs/D03_THERMAL_COVERAGE.md` |
| D04 | `docs/data-sources/d04-water-ice-coverage-matrix.json` | `docs/D04_COVERAGE_MATRIX.md` |
| D05 | `docs/data-sources/d05-illumination-radiation-coverage-matrix.json` | `docs/D05_COVERAGE_MATRIX.md` |
| D06 | `docs/D06_COVERAGE_MATRIX.json` | `docs/D06_COVERAGE_140.md` |
| D01–D06 integration | `docs/coverage-matrix-d01-d06.v1.json` | `docs/MULTIDIMENSIONAL_SEARCH.md` |

## Real data snapshot

Local verified snapshot (public API still on `dv-b46f3bad38ea` until deployment is confirmed):

```text
typed observations: 2,185
D02 59, D03 73, D04 87, D05 34, D06 1,932
D06 0–5 cm gated Earth candidates: 113 / 113
D01 fine Earth candidates: 113; Moon references: 18; Mars references: 7
Earth coarse pilot: 168 cells (separate 250 m method)
```

Bounded 2026-11-15 expansions (real ingest, not global coverage):

- **D01:** two additional Mars Jezero CTX 20 m windows (`mars-jezero-ctx-window-06/07`) from the
  same official USGS M20 DEM checksum; raw raster not committed. Mars fine references now 7.
- **D02:** three Macrostrat API v2 point-query `geologic_unit` records for existing Earth fine
  candidates (`earth-grand-canyon-r2c2`, `earth-namib-r2c2`, `earth-iceland-r2c2`); CC-BY 4.0,
  `evidence_only`, no taxonomy crosswalk. D02 now 59 (Earth 18).
- **D03/D04/D05:** NASA POWER daily point context for the same three Earth AOIs: D03 +12
  (Earth 32), D04 +12 (Earth 32), D05 +6 (Earth 16). All `context_only`, coarse reanalysis,
  not local in-situ measurements and not evidence of ice/ionizing radiation.

D06 Moon/Mars additions are **12 explicit `no_coverage` evidence records** for six physical
properties; no numeric value was fabricated or inferred from DEM roughness/WorldCover. D02–D05
remain evidence/context except where the registry explicitly permits a score; there is still no
cross-body six-dimension score.

## Public contracts

```text
GET  /api/v1/coverage/location?body=Earth|Moon|Mars&lat=...&lon=...
GET  /api/v1/search/features
GET  /api/v1/search/features/{id}
POST /api/v1/search/multidimensional
GET  /api/v1/planetary/maps
GET  /api/v1/planetary/map-features
GET  /api/v1/planetary/coordinate-contract
```

- `location-coverage-status-1.0.0`, registry `feature-eligibility-1.0.0`.
- Arbitrary point/bbox returns D01–D06 status, declared sources, per-feature provenance, support,
  QA and `global_coverage_claimed=false`. Missing data remains `not_indexed`, `no_coverage` or
  `unsupported`; no synthetic values.
- Legacy `POST /api/v1/search` remains `search-1.1.0`.
- OpenAPI has 28 paths; generated TypeScript is committed.

### Three-body arbitrary-location check

```text
Earth (-23.5, -68.4):    6 dimensions, declared sources 11, D01/D06 available_or_indexed
Moon  (-89.4389, -137.0960): 6 dimensions, declared sources 10, D01 available_or_indexed
Mars  (18.2203, 77.3710): 6 dimensions, declared sources 9, D01 available, D05 evidence_only
global_coverage_claimed=false in all three
```

## Dimension × feature weighting

Opt-in `dimension_weights` + `scoring_version=dimension-weighted-rms-v1`:

```text
q_d = Σ(v_df * z_df^2) / Σ(v_df)
distance = sqrt(Σ(w_d * q_d) / Σ(w_d))
```

Public check with `slope_mean_deg=10`, `soil_clay_0_5cm_mean=200`,
`soil_sand_0_5cm_mean=300`, D01/D06 weight 1:

```text
scoring.version = dimension-weighted-rms-v1
D01 feature_count=1, D06 feature_count=2
q_D01 = 0.0029600019548100083
q_D06 = 0.031957600149219445
distance = 0.1321317564100876  (recomputed exactly)
```

A disabled/zero-weight D06 dimension returned `eligible=116`, `excluded=0`, and only D01 in
`valid_features`; the query did not exclude candidates merely because D06 was off. Default calls
without dimension controls keep the legacy feature-weighted Euclidean path.

## Verification

```text
bash scripts/verify.sh --ci                 PASS (14 steps)
bash scripts/backend_acceptance.sh          PASS (offline)
bash scripts/public_acceptance.sh           PASS
uv run pytest tools/phase2_monitor/test_core.py -q   15 passed
```

Public acceptance covers maps, D06 scoring, cross-body rejection, Earth/Moon/Mars coverage,
dimension-weighted recomputation, disabled dimensions and legacy search.

Monitoring:

```text
GET /api/event (SSE) -> server.connected + session.created captured
monitor run -> reconciled after server.connected
monitor restart -> state persisted in ~/.local/state/analogx-phase2/monitor.sqlite3
```

## Deployment safety

- Database backed up before the Phase 2 API deployment:
  `/tmp/opencode/deploy-backup/analog-before-phase2-141.db` (6,995,968 bytes).
- API image rebuilt with `catalog/` included (the missing source-inventory copy was fixed in #156).
- Public health: `200`, `dv-b46f3bad38ea`; no database reset.
- Frontend #118/#119/#123 were not modified.

## Remaining scope outside #142

- #97 and #108 are open with no active Goal/session; #135 reused their existing outputs and did not
  duplicate them.
- #132 is an active external worker; its worktree was not modified. Its PR is tracked only.
- No global scientific coverage claim. D02/D03/D04/D05 remain evidence/context outside validated
  scoring; Moon/Mars D06 physical properties remain explicit `no_coverage` until bounded numeric
  sample extraction with depth/support/assumption provenance is completed.
- Manual review/merge/deploy remains a coordinator/human decision; the monitor never auto-merges or
  auto-deploys.

## Final coordinator status (2026-11-15, after #132 integration)

### Preflight / integration base

- `origin/main` advanced to `f96fad1`; PR #151/#162/#164 (#132 Earth bbox + planetary AOI
  analysis and Docker fixture fixes) are merged. No #132 worktree was modified.
- #97 and #108 remain open with no active worktree/session. #153 is an external frontend PR (#155).
- #118/#119/#123 remain frontend-owned; no UI file was edited by this coordinator.

### Open PRs from this phase

| PR | Scope | Local verification | Merge |
|---|---|---|---|
| #161 | D01–D05 bounded real ingest expansion (#135–#139) | `bash scripts/verify.sh --ci` PASS; `backend_acceptance.sh` PASS | open, not merged |
| #166 | #160 P0 AOI-first six-dimension query, D01+D06 search, evidence filters | `bash scripts/verify.sh --ci` PASS; `accept_sixdim_aoi.sh` PASS | open, not merged |

Both PRs were updated by merging `origin/main` (`f96fad1`) and re-running the full gate. GitHub
reports them mergeable; required review/merge is intentionally left to the maintainer.

### Native Goal verification (observed via `opencode session export`)

| Issue | Session | Native goal | Outcome |
|---|---|---|---|
| #135 D01 | `ses_e20fc8691ffeaMvQ1pjnNWscZp` | `0199a330-c186-4293-acd6-71762469219d` | succeeded |
| #136 D02 | `ses_e20fc69adffe9JAGca55A3aX5R` | `8004c26a-8e34-464d-9bfb-8dc31680d2fe` | succeeded |
| #137 D03 | `ses_e20fc6930ffeoPYLni2B8ZosUx` | `0ceab842-708d-4164-81e2-96c4aeb23ca4` | succeeded |
| #138 D04 | `ses_e20fc68bbffe2Sar4coxcaeuS7` | `642a1a88-25bc-4515-a3bb-2d153bad8bb1` | succeeded |
| #139 D05 | `ses_e20fc684affe5mrOOQ6dvy5Ju5` | `993b3503-c9c2-4a47-a256-76f6e2f35ed9` | succeeded |
| #140 D06 | `ses_e20fc67c3ffeEHNB8Ra3Q5CLqP` | `96555f26-c85b-448d-8ed1-6c55880da63c` | succeeded |
| #141 Integration | `ses_e20fc6751ffeXuPBKd6WZyqGcN` | `2f312370-3a07-400f-b15a-95cd4cc8d251` | succeeded |
| #142 Coordinator | `ses_e253e85e9ffeLjB1ipw0gIVen0` | `a1caf80d-100c-4491-a301-cdf5020f0a69` | active |
| #160 P0 | `ses_e20b4eb76ffekA5MUFIVhI4mSc` | `051525ff-6215-470c-847a-a570d6b1aea1` | active |

Each session contains a synthetic message with `metadata.source="goal"`; the message-list endpoint
is paginated, so verification uses `opencode session export`.

### Three-body acceptance (observed)

- `bash scripts/backend_acceptance.sh` in the #161 branch: **PASS** (offline focused suites, legacy
  smoke, pinned snapshot `snap-0faad9cee4386729` / `dv-6fefaf3a7974`, 25 references).
- `bash scripts/public_acceptance.sh` against the deployed API: **PASS** (health, capabilities,
  typed coverage, global coarse index, Moon legacy search, D06 eligibility, multidimensional search,
  planetary map catalog, arbitrary-location D01–D06, dimension×feature weighting, legacy search,
  web/CORS).
- `bash scripts/accept_sixdim_aoi.sh` in the #160 branch: **PASS** (11/11 sections: Earth/Moon/Mars
  statuses, evidence facets, D01+D06 rank change, cross-body rejection, out-of-coverage, WorldCover).

### Public deployment status — not claimed as this branch

- Observed public API: `dv-72be27e95dba`, 117 indexed candidates, 2,158 typed observations,
  5 D01 features, D06 1,932, D06 gated 113. Public acceptance passed read-only.
- Local #161 branch: `dv-6fefaf3a7974`, 113 Earth candidates, 2,185 typed observations, 7 Mars
  references. Local #166 branch is based on `f96fad1` and seeds the committed 2,152/5 Mars data.
- The public data version therefore does **not** match PR #161 or PR #166. No deployment, database
  overwrite or production reset was performed. Formal deployment and rollback remain subject to
  explicit maintainer confirmation.

### Remaining gaps (explicit, not zero-filled)

- D02–D05 remain evidence/context outside validated scoring; no cross-body six-dimension score.
- Moon/Mars numeric regolith D06 properties remain `no_coverage` (12 explicit evidence records).
- WorldCover remains an Earth-only land-cover hard filter; `/search/multidimensional` does not yet
  accept `landcover_*` filters (`422 unknown_feature`). #119 must keep that control on legacy
  `/search` until the combined path lands (documented in `docs/FRONTEND_HANDOFF_160.md`).
- Coverage endpoint response bodies remain generic objects in OpenAPI; record fields are documented
  but not yet Pydantic response models.
