# Geomorpho250 resumable batch pilot (Issue #32)

Bounded, resumable, local-only batch execution for the **three-region Geomorpho250
pilot**. This is not a global scan: it covers three 0.4° windows (168 five-kilometre
cells) and makes no claim of global or exhaustive coverage.

- Real data ↔ synthetic fixtures are labelled everywhere. The default
  `--source-mode real` writes rows tagged `real_geomorpho250`; tests use
  `--source-mode synthetic-fixture`, whose rows are tagged `synthetic_fixture` /
  `not_an_observation` and must never be published.
- Run-time network access is **not** required. The runner reads locally staged
  240 × 240 windows; the global 5 GB PANGAEA raster is never downloaded. Staging
  itself is the already-committed bounded `/vsicurl` range read from
  `scripts/build_geomorpho_real_pilot.py`.

## Source and versions

| Item | Value |
|---|---|
| Dataset | `geomorpho250-slope-v1.0` |
| Source | PANGAEA `dtm_slope_merit.dem_m_250m_s0..0cm_2018_v1.0.tif` (Geomorpho90m) |
| Licence | CC BY 4.0 |
| Citation | Amatulli et al. (2019/2020), Geomorpho90m, PANGAEA 899135 |
| Source support | 7.5 arc-second int16 slope, scale 0.01, nodata -32768 |
| Coarse method | `coarse-terrain-250m-1.0.0` |
| Grid | `coarse-grid-1.0.0` (5 km windows, 5 km stride, UTM per region) |
| Parquet schema | `coarse-parquet-1.0.0` |
| Batch/checkpoint schema | `geomorpho-batch-1.0.0` / checkpoint `1.0.0` |

Staged input windows are real, bounded range reads (padded by 0.05°):

| Region | Staged file | SHA-256 |
|---|---|---|
| `pilot-alps` | `var/cache/geomorpho-pilot/pilot-alps.tif` | `e1349d175135549fee6336c0aaa4d036fdaeb5d209b5a44c5865d73c17ca78fd` |
| `pilot-grand-canyon` | `var/cache/geomorpho-pilot/pilot-grand-canyon.tif` | `e0c301f2dcd4891c5f55533d526857f94b844bb8c80ab6a991e45bab34556885` |
| `pilot-sahara` | `var/cache/geomorpho-pilot/pilot-sahara.tif` | `3c8a3f993c43ddf79929b86db7c589abc781baa030b782db894926c8acf234c9` |

`var/` is gitignored. To reproduce, stage the windows with the bounded pilot script
(`uv run python scripts/build_geomorpho_real_pilot.py`, network, opt-in) or copy the
same files; the runner records and compares the actual SHA-256 of each window.

## Commands

```bash
# Plan only: prints task ids, inputs, bounds; writes nothing.
uv run python scripts/run_geomorpho_batch.py \
  --run-id geomorpho-pilot-real-20261115 --dry-run

# Real three-region run (default --source-mode real), 2 workers, 16 cells per task.
uv run python scripts/run_geomorpho_batch.py \
  --run-id geomorpho-pilot-real-20261115 --workers 2 --cells-per-task 16 \
  --cache var/cache/geomorpho-pilot --out var/geomorpho-batch

# Resume after interruption; successful terminal tasks are not recomputed.
uv run python scripts/run_geomorpho_batch.py \
  --run-id geomorpho-pilot-real-20261115 --workers 2 --resume \
  --cache var/cache/geomorpho-pilot --out var/geomorpho-batch

# Retry only failed tasks (e.g. after staging a missing window).
uv run python scripts/run_geomorpho_batch.py \
  --run-id geomorpho-pilot-real-20261115 --resume --retry-failed \
  --cache var/cache/geomorpho-pilot --out var/geomorpho-batch

# Audit checkpointed artifacts and checksums without running any task.
uv run python scripts/run_geomorpho_batch.py \
  --run-id geomorpho-pilot-real-20261115 --verify-only \
  --cache var/cache/geomorpho-pilot --out var/geomorpho-batch

# Focused subset (identity is stable; --limit never renumbers tasks).
uv run python scripts/run_geomorpho_batch.py \
  --run-id alps-only --region pilot-alps --limit 1 --workers 1

make data-geomorpho-batch-dry-run BATCH_RUN_ID=demo
make data-geomorpho-batch BATCH_RUN_ID=demo
```

Flags: `--run-id` (required, path-safe), `--region` (repeat/comma; default all three),
`--limit`, `--workers` (bounded 1–4), `--cells-per-task` (1–64, default 16),
`--cache`, `--out`, `--report`, `--source-mode {real,synthetic-fixture}`, `--resume`,
`--retry-failed`, `--max-retries` (0–2), `--memory-budget-mb` (analytic bound check),
`--dry-run`, `--verify-only`.

Exit codes: `0` success (including auditable `no_coverage`), `1` failed/invalid tasks or
failed artifact verification, `2` invalid configuration or unsafe resume.

## Checkpoint, artifacts, and outcomes

```
<out>/runs/<run-id>/
  checkpoint.json                 # per-task status, fingerprint, attempts, checksums
  report.json                     # bounded run report (atomic write)
  tasks/<task-id>/
    rows.parquet                  # atomic, checksum-verified Parquet partition
    task.json                     # atomic sidecar: versions, input/output hashes, counts
```

- Task identity is content-derived from batch/grid/method versions, region id, and the
  first/last candidate id of the partition. It does not depend on `--limit`, `--region`
  selection order, or worker count.
- The fingerprint includes the staged input SHA-256, so a changed window forces a
  recompute instead of silently reusing stale rows.
- A task is checkpointed only after its Parquet checksum matches the worker result. On
  resume, the Parquet checksum **and** the JSON sidecar are re-verified; missing,
  truncated, or tampered artifacts are recomputed.
- Resuming a checkpoint with a changed partition plan (different `--cells-per-task` or
  region set) is refused instead of silently mixing schemes; a wider `--limit` on the same
  plan is allowed and only adds the remaining tasks.
- Task outcomes are `succeeded`, `no_coverage` (insufficient valid source pixels),
  `invalid` (domain/read failure), `failed` (operational error, retryable), plus
  `pending`/`running`. Cell counts and reasons are stored per task and aggregated in the
  report; missing staging is a `failed` task, never zero-filled coverage.
- At most `--workers` partitions run concurrently. Tasks stream one partition at a time;
  rows are never accumulated for all cells. The report records the worker/task bounds
  and an analytic peak-memory lower bound.

## Verified real run (2026-11-15)

Environment: Linux, Python 3.13, `uv`; network not used by the batch itself.
Command: `uv run python scripts/run_geomorpho_batch.py --run-id
geomorpho-pilot-real-20261115 --workers 2 --cells-per-task 16 --cache
var/cache/geomorpho-pilot --out var/geomorpho-batch`.

| Metric | Observed |
|---|---|
| Planned/selected tasks | 11 / 11 (168 cells: 48 alps, 56 Grand Canyon, 64 Sahara) |
| Task outcomes | `succeeded` 11; `failed` 0; `no_coverage` 0; `invalid` 0 |
| Rows written | 168 (one per 5 km cell), no duplicate `candidate_id` |
| Artifact verification | 11/11 Parquet + sidecar verified, 0 orphaned |
| Wall time / peak RSS | ~1.0–1.1 s / ~167–169 MiB (two clean reruns) |
| Disk | 70,030 B Parquet + 25,028 B sidecars; checkpoint 9,072 B; report 10,859 B |
| Per-task duration | 0.088–0.187 s (sum 1.54 s across two workers) |
| Warnings | none (all three staged window hashes match the published pilot record) |

Per-task Parquet SHA-256 (also in `report.json`):

| Region | Task | Rows | Parquet SHA-256 |
|---|---|---|---|
| pilot-alps | `geomorpho-batch-v1-b12288bbb5059082` | 16 | `77b9e62556608e69a3000dedd8e53679b54c7635948f385759f5f3f88957c91e` |
| pilot-alps | `geomorpho-batch-v1-c7bc681722713d78` | 16 | `65a4ad66287718ab4e50478013e84f31b15909c97362c2a51ef046b455f868ac` |
| pilot-alps | `geomorpho-batch-v1-f3a7c68709553e27` | 16 | `02cc462542ed53c97fc145426b085ca3afd966d79711c226a96627814e246c34` |
| pilot-grand-canyon | `geomorpho-batch-v1-415bceb2741c6f50` | 8 | `5c751f0b3b5e5bbe60eeecf71f6526171c77281abf3a719142cb528b014063b4` |
| pilot-grand-canyon | `geomorpho-batch-v1-65128d5a946443ce` | 16 | `53f58f01aca3a0c69c85f5a09633f2674ce2da34ebe4f3c5f22374db3cef3ae4` |
| pilot-grand-canyon | `geomorpho-batch-v1-aeedaabab626da85` | 16 | `6771b01a76e41089563a6f0f442ac9c0575463677cb587ff761cc0cff584d3b5` |
| pilot-grand-canyon | `geomorpho-batch-v1-df8e4318e3b07efc` | 16 | `df3d63726dbaa3b72166373a2b4f88f12d69f538d81f72d5dc8df684fcfcdf1f` |
| pilot-sahara | `geomorpho-batch-v1-714e5c81d3777321` | 16 | `4800369870db55b73ed12cddb8343f02cb08836e4f82e806bc66d392fcf762fb` |
| pilot-sahara | `geomorpho-batch-v1-9cccaaa3f9cc0ad4` | 16 | `e7a77882714f24854c8a4497acc016ee052e2051982a4c692ffdea390db057f3` |
| pilot-sahara | `geomorpho-batch-v1-c95ea5ef1ec5c77b` | 16 | `70cc1f1295f8973af229d021db55cb27000cd74a721d40523a1cd34b02d50318` |
| pilot-sahara | `geomorpho-batch-v1-e46226ce9068c778` | 16 | `85e3f1e5c0704354c7d725d55265da155c57610cfac3daf82ec7cd04215a0d42` |

Reproducibility check against the committed real pilot index
(`pipelines/coarse/real-pilot/index`, snapshot `geomorpho250-real-pilot-20261115`):
the batch's 168 rows are identical to the committed 168 rows on
`candidate_id, region_id, west, south, east, north, slope_mean_deg, slope_p90_deg,
flat_fraction, valid_fraction, method_version, grid_version` (0 mismatches, 0
duplicates). Only `qa_flags` differs, because the batch adds `batch_partition` to make
the execution provenance explicit.

Resume and audit on the same run:

```
--resume        -> counts {succeeded: 0, skipped: 11, failed: 0, no_coverage: 0, invalid: 0}
--verify-only   -> {"ok": true, "terminal_tasks": 11, "verified": 11, "problems": []}
```

## Interruption and corrupted-output recovery

Automated coverage: `pipelines/tests/test_geomorpho_batch.py` injects a simulated
interruption, resumes, then truncates a Parquet file and corrupts a JSON sidecar; it
asserts the successful tasks are not recomputed and only damaged artifacts are rebuilt.

Real-data demonstration (run id `geomorpho-pilot-recovery-demo`, same staged windows):

```
step 1  --limit 3                         -> succeeded 3
        truncate one rows.parquet, corrupt one task.json
step 2  --limit 3 --resume                -> skipped 1, succeeded 2 (the two damaged tasks)
        verification ok = true
```

## Focused checks run for this change

```bash
uv run pytest pipelines/tests/test_geomorpho_batch.py \
  pipelines/tests/test_parquet_batch.py pipelines/tests/test_real_coarse_pilot.py
# 29 passed

uv run pytest pipelines/tests
# 191 passed, 2 skipped (after merging origin/main)

uv run ruff check .              # All checks passed!
uv run ruff format --check .     # 216 files already formatted
```

`make verify` is run by the coordinator; this workstream did not run the full gate.

## Limitations

- Three 0.4° pilot regions only; not global coverage and not an exhaustive survey.
- 7.5 arc-second source is ~250 m only near the equator; there is no fixed metric source
  grid.
- Bilinear reprojection and 5 km aggregation are method-specific, not 90 m terrain
  metrics; coarse rows must not be compared or merged with `terrain-metrics-1.0.0`.
- `no_coverage`/`invalid` cells are reported, never filled; a missing staged window fails
  the task instead of being reported as coverage.
- Local filesystem checkpoints only: no Redis/Celery/Kubernetes dependency, and no
  cross-host coordination.
