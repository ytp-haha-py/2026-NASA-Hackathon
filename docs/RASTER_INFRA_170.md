# Raster Data Infrastructure (Goal C, #170)

> **Implementation note (C1, 2026-11-15):** the delivered source adapter and bounded
> window contract is documented in [`RASTER_SOURCE_170.md`](RASTER_SOURCE_170.md).
> Adapter class names below are the original planning sketch; the implemented surface is
> `RasterSource` / `RasterReader` and the cache remains in `raster_cache.py`.

## Purpose

Provide a shared, offline-first raster access layer for pipelines that need Earth (and
future planetary) raster inputs without turning caches into observations. The
infrastructure supplies **source adapters**, **metadata**, **disk cache/versioning**,
**checkpoint/resume**, and **concurrency budgets** so downstream extractors (A/B/D
consumers) can fetch bounded data reproducibly, record provenance, and fail closed
when inputs are missing or invalid.

**Hard constraints**

- **Cache is not observation.** Cached bytes/tiles are transport/storage artifacts.
  Only values extracted through a validated pipeline path, with provenance and quality
  flags, may become observations.
- **No zero-fill.** Missing tiles, nodata, failed reads, or incomplete downloads must
  remain missing/failed with explicit status; never substitute `0` or fabricated values.
- **No global/giant downloads in tests or default runs.** Work on bounded AOIs/tiles;
  tests use synthetic data or a local HTTP server.
- **Planetary correctness.** Moon/Mars coordinates stay in their planetary frames;
  never coerce to Earth WGS84.
- **Provenance over convenience.** Every materialized input must carry source identity,
  checksum/version, license, CRS, nodata, and resolution metadata.

## Planned public modules

- `pipelines/src/analog_pipelines/raster_source.py` — source adapters and metadata.
- `pipelines/src/analog_pipelines/raster_cache.py` — disk cache, versioning, locks,
  eviction, checkpoints, and concurrency budgets.

APIs below describe the intended contract for A/B/D integration; implementations may
refine names but must preserve these behaviors.

## Source adapters (`raster_source`)

### Common responsibilities

All adapters expose or produce metadata sufficient for provenance and safe reads:

| Field | Meaning | Required behavior |
| --- | --- | --- |
| `checksum` / `sha256` | Content integrity | Verify when available; mismatch is a hard failure, not a warning. |
| `version` | Dataset/product revision | Part of cache identity; a new version must not silently reuse stale bytes. |
| `license` | Usage/license terms | Recorded in manifests/provenance; never dropped. |
| `crs` / `srs` | Coordinate reference system | Explicit; never guessed. Must match the asset's actual frame. |
| `nodata` | Sentinel/mask value | Preserved and honored during extraction; nodata is not a measured value. |
| `resolution` | Native pixel/ground resolution | Recorded with units/context; resampling (if any) must be declared separately. |

Adapters should also carry source URI/id, access timestamp (where applicable), and
content type/size when known.

### Adapter types

1. **HTTP Range adapter** (`HttpRangeSource` / similar)
   - Reads byte ranges from HTTP(S) endpoints supporting `Range`/`Accept-Ranges`.
   - Used for COG/tile chunk access without downloading whole objects.
   - Must support bounded retries/timeouts and surface partial-read failures explicitly.
   - Range/chunk metadata (etag/last-modified/content-range when present) feeds
     checkpoint/resume identity.

2. **GDAL VsiCurl adapter** (`GdalVsiCurlSource` / similar)
   - Accesses remote rasters through GDAL `/vsicurl/` (and related VSI) paths.
   - Intended for formats/GDAL workflows where range logic is delegated to GDAL.
   - Must still record source URL, version/checksum when known, license, CRS, nodata,
     and resolution from dataset metadata; VSI convenience does not replace provenance.

3. **Local staged tile adapter** (`LocalStagedTileSource` / similar)
   - Reads already-staged local files/tiles (operator-managed or pipeline-produced).
   - Primary offline/test path; validates existence, readability, and checksums.
   - Must not treat a local path as authoritative unless identity metadata matches.

## Cache and versioning (`raster_cache`)

### Disk cache

- **Configurable directory:** cache root is caller/operator configurable (env/config/arg);
  tests pass an isolated temp directory.
- **Content-addressed or version-qualified keys:** cache identity includes source id/URI
  and version/checksum so revisions do not collide.
- **Atomic writes:** write to a temp file in the target namespace then rename/fsync as
  appropriate; readers must never observe partial `.tmp`/`.part` files.
- **Integrity:** store/verify checksums; corrupted or mismatched entries are treated as
  misses/failures, not served as valid data.
- **Descriptive state:** expose stats (bytes/entries/hits/misses/evictions) where
  practical for observability and tests.

### Locking

- Protect concurrent writes to the same key (process/thread-safe file or in-process
  lock, depending scope). Parallel writers must yield one complete artifact, never a
  torn file. Independent keys should not serialize unnecessarily.

### Eviction

- Enforce configurable limits (max bytes and/or max entries). Eviction policy should be
  deterministic enough to reason about (e.g. LRU/size-aware) and must never delete
  in-flight atomic temp files outside its ownership rules. Eviction removes cached
  copies only; it does not invalidate upstream source provenance records.

## Checkpoint / resume

- Long or chunked transfers record progress (offset/ranges, etag/version/checkpoint id,
  destination, bytes written) so interrupted work can resume instead of restarting from
  zero.
- Resume must validate that the remote/source identity still matches the checkpoint
  (etag/version/checksum/range semantics). A mismatched source invalidates the
  checkpoint and restarts safely (or fails closed).
- Checkpoints are persisted atomically and cleaned up on verified completion.
- Suitable for local HTTP range simulation in tests and for real chunked providers in
  production paths; no giant-download assumption.

## Concurrency budget

- Heavy raster jobs are bounded by a shared semaphore/budget.
- **Default:** 2–4 concurrent heavy jobs (conservative for memory/IO-bound raster work);
  configurable per environment/pipeline.
- Budget acquisition must release on success, failure, and cancellation/exception.
- Intended to cap parallel decode/fetch/extract pressure, not to hide correctness bugs.
  Callers should still bound AOI/window sizes explicitly.

## Public interfaces for A/B/D consumers (integration guidance)

- **Goal A (acquisition/planning):** request sources by dataset/AOI, inspect metadata and
  availability, plan bounded tasks with explicit provider/source ids and versions.
- **Goal B (extraction/processing):** obtain a verified local/cache path or ranged reader,
  read with CRS/nodata/resolution honored, emit observations/features with provenance and
  `valid_fraction`/quality flags; never read through stale/unverified cache entries.
- **Goal D (serving/indexing/reporting):** consume manifests/provenance and cache stats
  for reproducibility and diagnostics; never present cache presence as data coverage.

Integrators should depend on behavior contracts (metadata present, atomic/cache-safe
reads, bounded concurrency, resumability) and treat absent/failed inputs as explicit
no-data states.

## Performance and resource test guidance

Tests live alongside pipeline tests and must remain **fast, offline, and synthetic**.

- **Location (target):** `pipelines/tests/test_raster_perf.py` (this task stages an
  adaptable draft at `/tmp/opencode/170_test_raster_perf.py`).
- **Data:** tiny synthetic byte payloads or small generated rasters; local files and/or
  `localhost` HTTP server for range/chunk behavior. No external network, no real R2/GCS,
  no global DEM/imagery downloads.
- **Concurrency:** assert configured caps are respected (max observed parallelism <=
  budget) and permits release on exceptions.
- **Cache:** configurable dir, atomic writes (no `.tmp`/`.part` leaks), version/key
  separation, integrity/checksum behavior, and eviction within configured bounds.
- **Checkpoint/resume:** simulate partial transfer/interruption and verify resume reaches
  byte-identical completion without restarting unnecessarily when API supports it.
- **Perf sanity:** generous wall-clock bounds for small operations to catch accidental
  quadratic behavior, sleeps, or unbounded work; not microbenchmarks.
- **Adaptability:** tests may probe alternative class/function names and `pytest.skip`
  only when a required behavior/API is genuinely not exposed; they must fail when the
  module exists but violates the contract (e.g. torn writes, zero-fill, ignored budget).

## Non-goals / reminders

- Raster cache entries are **not** scientific observations and must not be indexed as such.
- Do not “repair” missing data by filling nodata with zeros/defaults.
- Do not broaden tests to require credentials, large fixtures, or network access.
- Keep CRS/frame handling explicit; planetary products are not Earth products.
