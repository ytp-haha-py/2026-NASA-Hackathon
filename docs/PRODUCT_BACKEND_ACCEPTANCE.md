# Product backend acceptance (offline handoff)

This document defines what the backend can demonstrate **today** with committed
data and synthetic fixtures. It does not claim global coverage, six-dimensional
measurement completeness, or an operational production deployment.

## Scenarios and actual state

| Scenario | Endpoint/command | Result | Evidence |
|---|---|---|---|
| Legacy indexed terrain search | `POST /api/v1/search` | Works over 113 Earth windows / 23 references (18 Moon, 5 Mars) | `apps/api/tests/test_api.py`, `test_real_manifest.py` |
| Moon/Mars reference search | `POST /api/v1/search` body `Moon|Mars` | Works; reference-mode ranking | existing API tests |
| On-demand Earth AOI | `POST /api/v1/areas/analyze` | Queued job, cache, partial/failed states | `test_aoi.py`; real online smoke opt-in only |
| Coarse global discovery (fixture) | `GET /api/v1/global/*` | Works on a verified fixture Parquet index | `test_global_api.py` |
| Coarse ranking / missingness | `CoarseIndexRepository.search` | DuckDB deterministic top-K; method/snapshot gates | `test_parquet_batch.py` |
| Refine plan | `POST /api/v1/global/refine-plan` | Plans 1.98 km fine windows; does not execute/download | `test_global_api.py` |
| Six-dimension typed fixtures | `GET /api/v1/typed/observations` | D01–D06 fixture records round-trip; legacy API intact | `test_typed_api.py`, `test_dimensions.py` |
| Snapshot/index reproducibility | `pipelines/build_index.py`, snapshot verify | Local rebuild works offline | `test_snapshot.py`, release checklist |
| Six-dimension real measurements | — | **Not implemented / not downloaded** | source inventory |

## Stable examples

```bash
# existing indexed terrain search
curl -s localhost:8000/api/v1/search -H 'content-type: application/json' -d '{
  "body":"Moon","reference_id":"moon-shackleton-rim-site04-roi2","limit":5
}'

# coarse global index (fixture or future authorized index only)
curl -s localhost:8000/api/v1/global/coverage
curl -s localhost:8000/api/v1/global/search -H 'content-type: application/json' -d '{
  "target":{"coarse_slope_mean_deg":{"value":10,"weight":1}},
  "limit":5
}'

# typed fixture/verified observations only
curl -s 'localhost:8000/api/v1/typed/observations?dimension=D02'
```

## Contract semantics

- `no_indexed_data` (409) is used when no coarse index is configured; the API never
  fabricates rows.
- Global coverage is labelled `local_coarse_index_not_global_exhaustive`.
- Coarse 250 m-derived features and fine 90 m features have different IDs, method
  versions and support; a comparison attempt is rejected rather than rescaled.
- Missing states (`not_detected`, `no_coverage`, `unknown`, `not_applicable`) carry
  no numeric value; physical zero is a valid detected value.
- Six-dimension category/detection/evidence/context features are not silently
  aggregated into a universal similarity score.
- Existing v1 endpoints, 113/18/5 observations, snapshots and user AOIs remain intact.

## Offline acceptance commands

```bash
uv run pytest apps/api/tests pipelines/tests -q
bash scripts/smoke.sh
bash scripts/validate_data.sh
bash scripts/verify.sh --backend-only
```

Do not run `scripts/online_smoke.sh` or any ingestion command as part of offline
acceptance. Real-source validation remains a separate, explicitly authorized activity.

## Frontend handoff

The existing React UI does **not** need a redesign here. New additive endpoints are
available in OpenAPI/TS types; global discovery should be introduced only after a
real or clearly labelled fixture index exists, and must show the coverage label,
method version, exclusion counts and coarse-vs-fine warning. D02–D06 must render
evidence/detection/context roles separately, never as a single six-dimensional
score.
