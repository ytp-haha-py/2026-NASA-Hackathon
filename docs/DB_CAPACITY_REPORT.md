# Database capacity, indexing, and backup report (Issue #70)

Status: **measured on one WSL2 workstation; synthetic rows are explicitly labelled.** This is
software/capacity evidence, not real global coverage and not a scientific result. No source
raster was downloaded and the real runtime database was never reset.

- Measurement date: 2026-11-15
- Repository branch: `feat/70-db-capacity` (based on `e907d7f`)
- Machine: AMD Ryzen 5 5600X (6C/12T), 15 GiB RAM, WSL2 Linux 6.18, 945 GiB free on the test disk
- Python 3.13.18, DuckDB 2.0.0, SQLite 3.53.1
- Runtime database inspected: a **copy** of the current `var/analog.db`; the source file was
  opened read-only by the backup API.

## 1. Actual runtime inventory (SQLite only in this checkout)

Command (copy first; never audit/reset the live file in place):

```bash
cp /path/to/repo/var/analog.db /tmp/db70-runtime/analog.db
uv run python scripts/db_audit.py --database /tmp/db70-runtime/analog.db \
  --output /tmp/db70-runtime/audit.json
```

Observed SQLite facts:

| Metric | Value |
|---|---:|
| File bytes | 1,191,936 |
| Page size × page count | 4,096 × 291 = 1,191,936 |
| Freelist pages | 0 |
| Journal mode | `delete` |
| `PRAGMA integrity_check` | `ok` |
| `data_version` | `dv-b46f3bad38ea` |
| Recorded `schema_version` | none before the additive stamp (legacy DB); `1` after startup/seed |
| WAL/SHM sidecars | 0 bytes (none present) |

Per-table/index `dbstat` attribution (bytes are page attribution, so rounded object sizes need not
sum exactly to the file size):

| Object | Rows | Bytes |
|---|---:|---:|
| `typed_observations` | 181 | 344,064 |
| `context_observations` | 1,243 | 229,376 |
| `planetary_references` | 23 | 86,016 |
| `candidates` | 113 | 81,920 |
| `feature_observations` | 565 | 61,440 |
| `typed_source_assets` | 123 | 53,248 |
| `datasets` | 9 | 36,864 |
| `typed_processing_runs` | 4 | 36,864 |
| `reference_feature_observations` | 115 | 16,384 |
| `typed_areas` | 9 | 12,288 |
| `analysis_jobs` | 0 | 4,096 |
| `meta` | 2 | 4,096 |
| `ix_typed_observations_feature_id` | — | 12,288 |
| `ix_analysis_jobs_cache_key` | — | 4,096 |
| `ix_analysis_jobs_idempotency_key` | — | 4,096 |

Separate layers in this checkout:

| Layer | Present | Files | Bytes | Note |
|---|---|---:|---:|---|
| SQLite | yes | 1 | 1,191,936 | runtime index + typed/user state |
| Parquet coarse index (`data/global-index`) | no | 0 | 0 | no real coarse index built here |
| Raw rasters (`data/raw`) | no | 0 | 0 | **not** part of DB size |
| Provider cache (`data/cache`) | no | 0 | 0 | **not** part of DB size |
| R2/S3 | not configured | unknown | unknown | no credentials; never zero-filled |

`database_bytes` in the audit is `SQLite file + sidecars + Parquet`; raw, cache, and R2 are
excluded by construction. `0` above means “directory absent in this checkout”, not a measured
zero for the real global dataset.

## 2. Backup / restore / rebuild drill

Command:

```bash
uv run python scripts/db_backup.py drill \
  --source /tmp/db70-runtime/analog.db \
  --workdir /tmp/db70-runtime/drill --keep --json \
  --output /tmp/db70-runtime/drill.json
```

Observed result (`db-backup-drill-1.0.0`, label `copy_only_drill_source_never_modified`):

| Check | Result |
|---|---|
| Online backup bytes | 1,191,936 |
| SHA-256 sidecar | recorded and verified before restore |
| Restored counts | identical to source |
| Rebuild snapshot | `snap-0cef4d75b147866a` |
| Rebuild `data_version` | `dv-b46f3bad38ea` (unchanged) |
| Protected runtime rows after rebuild | identical |
| Source file modified? | no (byte-for-byte unchanged) |

Protected counts before/after rebuild (all equal):

```json
{
  "analysis_jobs": 0,
  "on_demand_candidates": 0,
  "on_demand_context": 0,
  "on_demand_features": 0,
  "typed_areas": 9,
  "typed_observations": 181,
  "typed_processing_runs": 4,
  "typed_source_assets": 123
}
```

The live database currently has **zero** on-demand AOIs/jobs, so the preservation path is also
covered by `pipelines/tests/test_db_capacity.py`, which injects an on-demand AOI, its
feature/context rows, an analysis job, typed assets/areas/observations/runs, then performs
backup → restore → `seed_manifests(reset=True)` on copies. It fails if any of those counts change
including `on_demand_candidates`, `on_demand_features`, `on_demand_context`, `analysis_jobs`,
`typed_areas`, `typed_source_assets`, `typed_observations`, and `typed_processing_runs`.

Safety checks encoded in tests (not narrative):

- backup SHA-256 corruption is rejected and no destination file is created;
- corrupted Parquet partition checksum is rejected by `CoarseIndexRepository(verify=True)`;
- a schema stamped newer than this build is rejected before seeding/startup;
- malformed `schema_version` is rejected;
- two independent SQLite builds from the pinned snapshot are logically identical
  (candidate/feature/context/dataset/reference rows, `data_version`, schema version);
- two independent coarse builds produce identical content-addressed partition hashes;
- audit output keeps SQLite, Parquet, raw/cache, and R2 separate and JSON-serialisable.

Focused test command:

```bash
uv run pytest pipelines/tests/test_db_capacity.py -q
```

## 3. Synthetic coarse-index benchmark (100k and 1M rows)

Every benchmark row is labelled `synthetic_benchmark_not_observation` and the report carries
`label=synthetic_benchmark_not_real_data`. The benchmark never reads or downloads a source raster.

Commands (run one size at a time so timings do not contend for CPU):

```bash
uv run python scripts/benchmark_coarse_index.py --rows 100000 --iterations 20 \
  --json-out /tmp/coarse-100k.json
uv run python scripts/benchmark_coarse_index.py --rows 1000000 --iterations 15 \
  --json-out /tmp/coarse-1m.json
```

Observed:

| Metric | 100,000 rows | 1,000,000 rows |
|---|---:|---:|
| Build seconds | 54.122 | 501.057 |
| Parquet partitions | 16 | 104 |
| Parquet bytes | 423,242 | 3,939,300 |
| Parquet bytes/row | 4.232 | 3.939 |
| Verify seconds | 0.257 | 1.722 |
| Peak RSS (process) | 190.00 MiB | 655.42 MiB |
| Process CPU (build+verify+queries) | 75.695 s | 680.938 s |
| Top-K p50 / p95 / p99 (ms) | 29.582 / 34.590 / 39.649 | 60.111 / 64.041 / 64.041 |
| Region-filter p50 / p95 / p99 (ms) | 27.469 / 28.245 / 28.355 | 43.338 / 47.142 / 47.142 |
| Pagination offset 1,000 / 10,000 p50 / p95 / p99 (ms) | 30.590 / 32.172 / 32.487 | 167.280 / 175.300 / 175.300 |

The pagination scenario uses repository `OFFSET` pagination (offset = `max(10, rows/100)`,
limit 10). The API currently exposes top-K search only; offset paging is benchmarked at the
repository level so capacity work did not change the public response schema.

Interpretation guardrails:

- 100k→1M is a **10×** row increase; do not assume linear query latency.
- Synthetic feature values repeat in cycles and compress unusually well. Real Geomorpho250
  values will not necessarily reach 4.2 bytes/row.
- This benchmark exercises Python row construction plus DuckDB `executemany`; it is not an I/O
  benchmark. A real pipeline that writes partitions incrementally will not match this wall time
  exactly.
- Build throughput was ~1,847 rows/s at 100k and ~1,996 rows/s at 1M on this machine. At that
  rate the arithmetic 5.96M-cell projection is ~50 minutes of *synthetic index writing*, excluding
  raster extraction and real source I/O. This is a planning bound, not a production promise.
- Peak RSS rose from 190 MiB (100k) to 655 MiB (1M) because finalization scans all partitions to
  detect duplicate candidate IDs. Memory is not proportional to the buffered partition size alone.
- Query latency includes a fresh DuckDB connection + Parquet view per call (the API's read path),
  not a warmed shared cache.

## 4. Typed observations: sparse storage and index decision

`typed_observations` contains one row per present observation. There is no dense
area × dimension × feature table and no null-filled six-dimension cross-product. Missing sources
stay missing and are reported as `unknown`/provider status; they are never zero-filled or turned
into synthetic “observed” rows.

Current real data: 181 typed observations, 9 areas, 123 source assets, 4 processing runs;
`typed_observations` occupies 344,064 bytes (JSON support/evidence columns dominate).

Existing indexes: primary key + `feature_id`. The API also filters by `area_id`,
`body`/`dimension_code`, and groups by `(dimension_code, body, state)` for coverage.

Measurement command:

```bash
uv run python scripts/benchmark_typed_observations.py --rows 1000000 --iterations 10 \
  --json-out /tmp/typed-1m.json
```

Observed on the synthetic 1,000,000-row temporary database (insert 12.555 s, database
584,953,856 bytes before candidate indexes):

| Query | Current p50/p95 (ms) | With candidate indexes p50/p95 (ms) | p95 speed-up |
|---|---:|---:|---:|
| `area_id` exact lookup | 180.189 / 214.634 | 1.900 / 2.522 | 85.1× |
| `dimension_code + body` filter | 199.121 / 228.524 | 0.008 / 0.046 | 4967.9× |
| `feature_id` (existing index) | 285.876 / 405.045 | 251.833 / 257.907 | 1.6× |
| coverage `GROUP BY dimension/body/state` | 603.783 / 653.995 | 66.973 / 69.392 | 9.4× |

Candidate indexes cost 61,726,720 bytes (~10.6% of the 585 MB synthetic database) before
`VACUUM`. The measurement therefore shows a **future** need at synthetic 1M scale, but not a
current need: the real table is 181 rows and these queries complete in well under a millisecond.
Adding indexes to an existing SQLite file also needs an explicit additive migration because
`Base.metadata.create_all` does not add new indexes to an existing table. We keep the current
schema and use the benchmark as the trigger.

Decision for this change: **no new typed-observation index is added yet.** The real table is ~181
rows and all common queries are far below the 50 ms p95 threshold used by the script; an index
would add write and disk cost without measured need. Re-run the command when the typed store
approaches 100k present observations (sparse rows only), then add the smallest index supported by
the plan: `(area_id)` for AOI lookups and `(body, dimension_code)` for dimension filtering;
coverage grouping may prefer `(body, dimension_code, state)`.

## 5. Capacity projection (assumptions explicit)

Measured Parquet bytes/row are the only scale inputs used here:

- 100k: 4.232 bytes/row (synthetic, highly compressible).
- 1M: 3.939 bytes/row (use this value first when planning).
- 5 km × 5 km cell = 25 km²; Earth land area ≈ 148.9 M km² ⇒ ≈ 5.96 M cells if the grid were
  complete and every cell were valid. This is a denominator, not a coverage claim.
- Projected Parquet at 5.96 M cells ≈ `5.96M × 3.939 ≈ 23.5 MB`, plus partition
  count and manifest overhead. Antarctica/excluded regions and missing-source gaps reduce it.
- This projection says nothing about the raw Geomorpho250 source size or R2 egress; those remain
  unknown without an authorized source and credentials.
- SQLite is **not** projected to hold global coarse candidates. For reference, the current
  1.19 MB database holds 113 candidate rows, 565 feature observations, 1,243 context observations,
  and 181 typed observations. Loading millions of coarse rows into SQLite would be a different
  architecture and is not planned.

Uncertainty: real value entropy/compression, duplicate candidate density, longer strings/QA flags,
partition count, snapshot sharing, WAL/journal settings, and filesystem block size all change the
result. Treat the synthetic numbers as a lower-bound software benchmark.

## 6. PostGIS decision (#3)

No migration in #70. Evidence:

- The real SQLite file is 1.19 MB / 291 pages with `integrity_check=ok`; no measured SQLite query
  bottleneck was found.
- Coarse candidate search is already Parquet + DuckDB, not SQLite, so PostGIS would not accelerate
  the current global query path.
- SQLite stores bbox as four floats; the API does not issue spatial joins/unions. The measured
  runtime database has zero on-demand AOIs and zero analysis jobs, so there is no measured
  spatial-query pressure.
- Revisit only if measurements show one of: sustained bbox/geometry joins over ≥10⁶ fine
  candidates, concurrent multi-writer requirements, or server-side spatial predicates that SQLite
  cannot satisfy at the documented latency target. A future adapter must preserve the existing
  `ports/repository.py` Protocols; do not rewrite scoring or feature semantics.

## 7. Remaining blockers / limits

- No real coarse Parquet index, raw raster, or cache exists in this checkout; the only real
  database is SQLite. Real global Parquet size remains unmeasured.
- R2 is not configured; remote bytes/object counts are unknown and intentionally not zero-filled.
- The live DB predates the schema stamp; it receives `schema_version=1` additively on the next
  startup or seed. The drill/stamp never runs `--reset` against the live file.
- Benchmarks ran on one WSL2 workstation; they are not production SLAs or cross-machine claims.
- `uv run pytest apps/api/tests pipelines/tests -q` (253 tests) and the offline
  `bash scripts/backend_acceptance.sh` gate passed on this branch.
- The full `bash scripts/verify.sh` gate is left to the integration coordinator. This workstream
  ran focused tests, Ruff, and the benchmark commands above.

## 8. Reproduction checklist

```bash
uv run pytest pipelines/tests/test_db_capacity.py -q
uv run ruff check .
uv run ruff format --check .
uv run python scripts/db_audit.py --json --output /tmp/db-audit.json
uv run python scripts/db_backup.py drill --source var/analog.db --json
uv run python scripts/benchmark_coarse_index.py --rows 100000 --iterations 20
uv run python scripts/benchmark_coarse_index.py --rows 1000000 --iterations 15
uv run python scripts/benchmark_typed_observations.py --rows 1000000 --iterations 10
```
