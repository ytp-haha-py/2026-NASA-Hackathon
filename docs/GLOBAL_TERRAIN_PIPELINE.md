# Offline global terrain pipeline

Status: **software implemented with synthetic fixtures; no Geomorpho250 raster was
downloaded.** This is not a global index and not a scientific result.

## Components

```text
local GeoTIFF fixture
  -> analog_pipelines.geomorpho.read_window
  -> analog_pipelines.coarse_grid.generate_grid (5 km UTM windows)
  -> analog_pipelines.coarse_features.extract_coarse_features
  -> analog_pipelines.batch_runner.BatchRunner (checkpointed)
  -> analog_pipelines.coarse_index.build_index (partitioned Parquet + SHA-256)
  -> analog_pipelines.coarse_index.CoarseIndexRepository (DuckDB)
  -> /api/v1/global/{coverage,search,candidates,refine-plan}
```

## Coarse feature contract

| Feature ID | Unit | Scale | Meaning |
|---|---|---:|---|
| `coarse_slope_mean_deg` | degree | 10 | mean valid slope in a 5 km window |
| `coarse_slope_p90_deg` | degree | 15 | p90 valid slope |
| `coarse_flat_fraction_lt5deg` | fraction | 0.25 | valid slope pixels <5° |

Method version: `coarse-terrain-250m-1.0.0`.
Grid version: `coarse-grid-1.0.0`.
Source resolution: ~250 m. Window: 5 km in a projected metre frame.

These IDs are **separate** from the fine 90 m features. `assert_comparable` and
`reject_coarse_fine_comparison` fail closed.

## Fixture workflow (works offline)

```bash
# Focused tests create tiny 8x8 or 64x64 GeoTIFFs in tmp_path.
uv run pytest pipelines/tests/test_coarse_foundation.py pipelines/tests/test_parquet_batch.py -q

# API fixture index is built by apps/api/tests/test_global_api.py; no network.
```

## CLI: local source metadata only

```bash
# This only inspects a local file. It refuses http(s)/s3 URLs.
uv run python -m analog_pipelines.geomorpho path/to/authorized_slope_250m.tif --metadata
uv run python -m analog_pipelines.geomorpho path/to/authorized_slope_250m.tif \
  --bbox 8.0 45.0 8.2 45.2
```

No command in this phase downloads a source or stages a remote file.

## Operator commands for a future authorized pilot (not executed)

```bash
# 1. Stage one authorized source file under ANALOG_RAW_DIR (manual; no automated fetch).
# 2. Inspect scale/offset/nodata/CRS using the reader.
# 3. Generate the deterministic grid for three pilot regions in a small JSON plan.
# 4. Run bounded partitions; retain checkpoints and failed/no-coverage counts.
# 5. Write partitioned Parquet, verify SHA-256, and query with DuckDB.
```

## DuckDB / Parquet contract

- Twenty columns: candidate/grid/region/body/bbox/CRS/area, three coarse values,
  valid fraction, method/grid versions, dataset/asset ids, QA flags, snapshot id.
- Immutable content-addressed partitions: `partitions/<region_id>/<sha256>.parquet`.
  Snapshot manifests at `snapshots/<snapshot_id>/manifest.json` reference partitions.
  Unchanged partitions are shared by multiple snapshots; a changed partition gets a new
  content hash. Publishing the same snapshot id twice is always rejected; published
  snapshots are immutable. Old snapshots remain queryable because their manifests
  still reference the old immutable partitions.
- Snapshot membership is manifest-only: row-level `snapshot_id` is not stored in
  Parquet partitions.
- Manifest: `snapshots/<snapshot_id>/manifest.json`, SHA-256 per partition, row count,
  coverage, methods. The writer flushes partitions at a bounded row count instead of
  materializing the full input.
- Duplicate candidate IDs are checked both within a partition and across partitions
  before the manifest is published. Candidate IDs, bbox/area, valid fraction and
  coarse feature domains are validated first.
- The API caches a verified repository keyed by the resolved manifest path + mtime/size
  and invalidates when a new/updated manifest appears. It does not re-hash every
  Parquet file on every query; explicit verification occurs on publication/load/refresh.
  `ANALOG_GLOBAL_SNAPSHOT_ID` selects one snapshot when a root contains several.
- Analysis-unit IDs are content-derived from the projected footprint plus
  window/stride/source-resolution/grid versions. Region row/column labels are metadata,
  not identity. Resolution, window size, stride and the projected anchor are persisted
  separately in each row.
- Read-only query path uses an in-memory DuckDB connection over pinned Parquet;
  no shared mutable DuckDB file.
- `snapshot_id` filters results; a different snapshot cannot leak into a query.
- Missing feature values for **positive-weight** features are excluded and counted;
  zero-weight features do not require a value or contribute a distance term. Valid
  zero remains a value.
- Deterministic order: `(distance, candidate_id)`.

## Grid coverage reporting

`generate_region_grid` reports the projected envelope it actually generated, never a blanket
"exhaustive" label. `GridCoverage` separates:

- `requested_area_km2` — area of the projected envelope of the requested region;
- `generated_extent_area_km2` — extent from the first cell origin to the last cell edge;
- `covered_area_km2` — sum of generated cell footprints;
- `stride_gap_area_km2` — internal gaps when `stride_m > window_size_m`;
- `boundary_margin_area_km2` — ungenerated east/north boundary strips;
- `coverage_fraction` with `coverage_denominator=projected_region_envelope_area`;
- `uncovered_margin_m` for all four sides;
- `valid_sampled_area_km2` and `missing_source_coverage`, which remain unset/`not_evaluated`
  until source valid-data masks are processed;
- `excluded_regions` for caller-declared exclusions (for example Antarctica).

The default is `generated_grid_not_exhaustive`. A complete label is emitted only when both
internal gaps and boundary margins are zero. This describes the grid, not global source coverage.

## API behaviour

- No index configured: `GET /global/coverage` returns `status=no_index`,
  `POST /global/search` returns `409 no_indexed_data`.
- Index configured: results are explicitly `local_coarse_index_not_global_exhaustive`.
- Refine endpoint returns a plan of 1.98 km fine windows; it does not run the 90 m
  pipeline or download anything.

## Known blockers

- Real Geomorpho250 raster metadata/bytes are unverified.
- No real coarse Moon/Mars same-support reference exists.
- No real cross-body coarse comparison is claimed.
- Global candidate coverage is not exhaustive; Antarctica is excluded.

## Fixture report and benchmark

```bash
# Report only what is in a verified local fixture/coarse index.
uv run python -m analog_pipelines.reporting --index /path/to/fixture-index --output /tmp/report.json

# Opt-in synthetic benchmark (no source data; temporary directory).
uv run python scripts/benchmark_coarse_index.py --rows 100000 --json-out /tmp/coarse-100k.json
uv run python scripts/benchmark_coarse_index.py --rows 1000000 --json-out /tmp/coarse-1m.json

# Storage layers are audited separately (SQLite/Parquet/raw/cache/R2).
uv run python scripts/db_audit.py
```

The benchmark reports build seconds, Parquet bytes/row, verification time, peak RSS/CPU, and
query p50/p95/p99 for top-K, region filter, and offset pagination. Every row is labelled
`synthetic_benchmark_not_observation`. **These are software timings, not real global
coverage or a scientific result.** Observed 100k and 1M runs are recorded with the actual
commands in `docs/DB_CAPACITY_REPORT.md`.
