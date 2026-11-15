# Issue #160 — P0 six-dimension AOI MVP checklist

Status: **in progress**. This is an execution checklist, not an acceptance claim. Update it only
with observed command output and real API responses.

- Worktree: `/home/panda/.local/share/opencode/worktree/204e7a/phase2-160-sixdim`
- Branch: `feat/160-sixdim-aoi`
- Session: `ses_e20b4eb76ffekA5MUFIVhI4mSc`
- Native Goal: `051525ff-6215-470c-847a-a570d6b1aea1` (verified by synthetic `metadata.source=goal`)
- Scope: backend/contracts/tests/docs; **no** React/Leaflet UI takeover (#119/#123/#118)

## Acceptance checklist

| # | Criterion | Owner | Evidence required | Status |
|---|---|---|---|---|
| 1 | Earth/Moon/Mars bbox AOI returns D01–D06 status, roles, sources, support, missing reasons | Subagent A | API request/response + test | **done** (`/coverage/aoi`, `/coverage/location?include_records=true`; `test_aoi_evidence_query.py` 7 tests) |
| 2 | Records are filtered by AOI/body/dimension/feature/state/time/depth/source; no cross-AOI copying | Subagent A | test + seeded DB result | **done** (exact-ID join; `proximity_join=false`; explicit filters) |
| 3 | D01 + Earth D06 weighted search; enable/disable and feature/dimension weights respected | Subagent B | reproducible request + rank change | **done** (`test_sixdim_weighted_search.py`; 40 passed incl. D06 leader flip) |
| 4 | Missing/incompatible features are excluded/missing, never zero-scored | Subagent B | test assertion | **done** (`test_sixdim_weighted_search.py`) |
| 5 | D02–D05 evidence queries use real pilots and return provenance or explicit no_data | Subagent A/B | per-dimension request/response | **done** (Earth/Moon/Mars real records + `no_data`/`no_coverage`) |
| 6 | D02–D05 evidence-only features never expose similarity scores | Subagent B | role matrix + test | **done** (`docs/SIXDIM_QUERY_ROLES.md`; 33 D02–D05 features asserted non-scoring) |
| 7 | WorldCover remains Earth-only land-cover hard filter (not a dimension, no weight) | Subagent C | regression test or explicit gap | **done** (`test_worldcover_filter_160.py`; legacy `/search` filter works, multidimensional gap documented) |
| 8 | Out-of-coverage bbox returns explicit status, not fabricated values | Subagent C | E2E script assertion | **done** (`accept_sixdim_aoi.sh` section 8) |
| 9 | Frontend handoff lists exact D01-only hardcodes and replacement APIs | Subagent C | `docs/FRONTEND_HANDOFF_160.md` | **done** (executable contract; duplicate summary removed) |
| 10 | OpenAPI + TypeScript types regenerated | Integrator | diff + `bash scripts/verify.sh --ci` | **done** (29 paths; `npm run generate:api`) |
| 11 | Full CI passes | Integrator | verify output | **done** (`bash scripts/verify.sh --ci` PASS, 14 steps, 117s) |
| 12 | PR opened with real requests/responses and gaps; not self-merged | Integrator | PR URL | **done**: https://github.com/ytp-haha-py/2026-NASA-Hackathon/pull/166 |

## Shared-scoring fix (Integration-owned)

Subagent B found that `dimension-weighted-rms-v1` per-feature contributions were squared twice
(`within` divided by `Σv` instead of the dimension's `Σ(v·z²)` numerator). Coordinator fixed
`apps/api/src/analog_api/domain/multidim.py` so feature contributions sum exactly to the dimension
contribution; `coverage_payload` now labels disabled dimensions
`dimension_disabled_not_compared`. Regression assertions are in
`apps/api/tests/test_sixdim_weighted_search.py`; focused run: **9 passed**.

## Reproduction baseline (run in this worktree)

```bash
uv sync
ANALOG_SEED_INCLUDE_TYPED=true uv run python -m analog_api.cli seed
uv run pytest apps/api/tests -q
bash scripts/verify.sh --ci
```

## Known non-claims

- D02–D05 are evidence/context unless the live registry explicitly allows scoring.
- D06 Earth uses SoilGrids 0–5 cm means; Moon/Mars numeric regolith remains `no_coverage`.
- Mars D01 covers seven Jezero windows, not Mars global coverage.
- The public API still serves the previous snapshot until a deployment is confirmed separately.
