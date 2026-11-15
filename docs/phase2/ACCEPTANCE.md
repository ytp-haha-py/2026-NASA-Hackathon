# Phase 2 acceptance report (Issue #142)

Status: **implemented and verified on the public API at `08c944a` / data snapshot
`dv-b46f3bad38ea`.** This report closes the Phase 2 coordination scope. It does not claim global
scientific coverage and does not take over #97/#108/#132 or the frontend #118/#119/#123.

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

Public API (`https://api.貓熊.台灣/api/v1`, `dv-b46f3bad38ea`):

```text
typed observations: 2,152
D02 56, D03 61, D04 75, D05 28, D06 1,932
D06 0–5 cm gated Earth candidates: 113 / 113
D01 fine Earth candidates: 113; Moon references: 18; Mars references: 5
Earth coarse pilot: 168 cells (separate 250 m method)
```

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
