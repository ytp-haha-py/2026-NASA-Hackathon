# Raster cache, resume and resource budget (Goal C, #170)

This document describes `pipelines/src/analog_pipelines/raster_cache.py`, the C2
storage layer for bounded raster windows. The source adapter that actually reads
providers is C1's `raster_source.py`; see `docs/RASTER_SOURCE_170.md` and the
Goal C overview in `docs/RASTER_INFRA_170.md`.

The cache is transport/storage infrastructure. It stores copies of source reads
and provenance metadata; it is never an observation, never invents pixels, and
never zero-fills missing values. A miss, a corrupt payload and an incomplete
chunk set are all explicit states.

## Public API

| Symbol | Purpose |
| --- | --- |
| `RasterWindow` | Validated half-open pixel window `(col_off, row_off, width, height)`. |
| `CacheKey` | Identity of one window read; `build(...)` or `for_source(source, window=...)`. |
| `CacheEntry` | Committed entry: checksum, size, metadata path, typed `extra`. |
| `CacheProbe` | Read-only `hit` / `miss` / `partial` / `corrupt` status. |
| `RasterCache` | Atomic put/get, locking, eviction, stats, chunk sessions. |
| `CachedWindow` | Cached `RasterWindowResult` copy: values, mask, window, transform, CRS. |
| `read_window_cached(cache, source, window=...)` | Cache-through read for any `RasterSource`-compatible object. |
| `ChunkedWrite` | Resumable ordered chunks with an atomic final publish. |
| `ResourceBudget` / `BudgetLimits` | 2-4 heavy-job concurrency, RAM reservation, free-disk reserve. |

## Cache identity

`CacheKey` hashes a canonical JSON payload containing:

- `source_id` — `dataset_id` (preferred), `uri`, or `source_id`;
- `source_version` — the verified immutable version or declared checksum
  (`CacheKey.for_source` rejects an unversioned source);
- the exact pixel `window` (`col_off`, `row_off`, `width`, `height`);
- the 1-based `band`;
- JSON-serialisable reader `options` (for example padding or resampling choice).

Equal keys produce equal SHA-256 fingerprints; a version, window, band or option
change produces a different entry. `"latest"` is never an acceptable version.
Entries are stored under `entries/<ab>/<fingerprint>/`, so a version bump cannot
reuse a stale window and two sources cannot collide.

Example:

```python
from analog_pipelines.raster_cache import CacheKey, RasterCache, read_window_cached
from analog_pipelines.raster_source import RasterSource, RasterAccessKind

source = RasterSource(
    "data/raw/example.tif",
    kind=RasterAccessKind.STAGED_TILE,
    dataset_id="example-dem",
    version="sha256:...",       # required for a stable identity
)
cache = RasterCache("data/cache/raster")   # or RasterCache() / cache_from_env()
result = read_window_cached(cache, source, window=(128, 256, 512, 512))
```

On a miss `read_window_cached` calls `source.read_window(...)` once under the
per-key lock and stores the values, validity mask, window, affine coefficients,
CRS WKT/name and source identity. On a hit the source is not opened at all.

## On-disk layout

```text
<root>/.gitignore
<root>/entries/<ab>/<fingerprint>/payload.bin
<root>/entries/<ab>/<fingerprint>/meta.json
<root>/entries/<ab>/<fingerprint>/chunks/plan.json
<root>/entries/<ab>/<fingerprint>/chunks/000000.part
<root>/locks/<ab>/<fingerprint>.lock
<root>/quarantine/<fingerprint>-<utc>-<pid>/
```

`<root>` defaults to `<ANALOG_CACHE_DIR>/raster`, i.e. `data/cache/raster` in the
repository (already Git-ignored), and can be set with
`ANALOG_RASTER_CACHE_ROOT`. A self-ignoring `.gitignore` is created so a custom
root cannot accidentally commit cached rasters.

## Atomicity, checksums and corruption

- Writes use `tempfile` + `fsync` + `os.replace` in the destination directory, so a
  crash leaves either the previous committed entry or no entry.
- `meta.json` is the commit marker. It records the payload SHA-256, size, chunk
  count, creation time and provenance `extra`.
- `get()` re-hashes the payload by default. A payload with no metadata, a size
  mismatch or a digest mismatch is treated as corrupt:
  - `probe(key, verify=True)` reports `corrupt` with a reason and changes nothing;
  - `get()` quarantines the entry directory under `quarantine/` and returns a miss,
    so the pipeline recomputes instead of serving damaged pixels. The
    `corruptions`/`quarantined` counters in `stats()` record it.
- Quarantine is manual cleanup: nothing is auto-deleted from `quarantine/`.

## Locking and single-flight reads

`RasterCache.lock(key, timeout=60)` takes an advisory `flock` on
`locks/<ab>/<fingerprint>.lock`; threads and processes contend for the same file.
`get_or_compute(key, compute, ram_bytes=...)` uses it as a double-checked
single-flight guard: concurrent callers for one window run `compute` exactly once.
Locks are per key, so unrelated windows proceed in parallel, and a timeout raises
`CacheLockTimeout` instead of silently duplicating work.

## Chunked writes and crash resume

For large windows or staged downloads:

```python
with cache.chunked_write(key, chunk_count=4) as chunks:
    for index in pending_chunks:
        chunks.write_chunk(index, block)
    entry = chunks.finalize(extra={"provider": "..."})
```

- Each `NNNNNN.part` is written atomically. `completed_chunks`, `missing_chunks`
  and `probe(...).status == "partial"` describe the resumable state.
- A restart creates a new session for the same key and sees the chunks already on
  disk; only missing chunks need work. The plan file pins the chunk count and key,
  so a mismatched resume raises `CacheStateError`.
- `finalize` refuses to publish unless every chunk is present, streams the chunks
  into the payload while hashing, then commits metadata and discards the chunk
  directory. A normal context-manager exit keeps chunks for a later resume; an
  exception discards the partial session.

## Eviction

Eviction is LRU by payload `mtime`, with limits applied after each write
(`auto_evict=True`, the default) and on demand:

```python
cache.evict(max_entries=500, max_bytes=8 * 1024**3)
cache.evict(dry_run=True)      # report only
```

`stats()`, `EvictionReport` and `probe()` expose entry counts, bytes, hits/misses,
evictions, corruptions and quarantine counts. Defaults are 5000 entries and 4 GiB;
`ANALOG_RASTER_CACHE_MAX_BYTES` / `ANALOG_RASTER_CACHE_MAX_ENTRIES` override them.
Because order is `mtime`, a read updates recency; do not rely on directory order.

## Resource budget

`ResourceBudget` is independent of the cache so non-caching callers can bound work
too:

- `max_heavy_jobs` defaults to **3** (within the required 2-4 range) and bounds
  concurrent heavy jobs across threads.
- `heavy_job(ram_bytes=...)` additionally reserves estimated RAM and blocks until
  the reservation fits; a request larger than the whole RAM budget raises
  `BudgetExceeded` immediately, and a timeout raises instead of overcommitting.
- `check_disk(path, required_bytes)` enforces free space plus
  `min_free_disk_bytes` (default 0 = disabled). Operators should set
  `ANALOG_RASTER_BUDGET_MIN_FREE_DISK_BYTES` (for example 2 GiB) on shared volumes;
  `get_or_compute(..., ram_bytes=...)` and `put` honour the guard.
- `snapshot()` reports active/peak jobs and RAM for logs and tests.

Environment overrides:

| Variable | Default |
| --- | --- |
| `ANALOG_RASTER_CACHE_ROOT` | `<ANALOG_CACHE_DIR>/raster` |
| `ANALOG_RASTER_CACHE_MAX_BYTES` | `4294967296` (4 GiB) |
| `ANALOG_RASTER_CACHE_MAX_ENTRIES` | `5000` |
| `ANALOG_RASTER_HEAVY_JOBS` | `3` |
| `ANALOG_RASTER_BUDGET_RAM_BYTES` | `2147483648` (2 GiB) |
| `ANALOG_RASTER_BUDGET_MIN_FREE_DISK_BYTES` | `0` (disabled) |

`cache_from_env()` and `budget_limits_from_env()` read these; explicit constructor
arguments win. Invalid integer values fail loudly rather than silently defaulting.

## Deliberate limitations

- Cached bytes are copies, not new measurements; extraction and QA still belong
  to the feature pipeline.
- The cache verifies its own payload checksum. Provider checksums are the caller's
  responsibility: pass a content-derived `version` (for example `sha256:...`) when
  upstream can change. Raster files are not content-addressed by digest.
- LRU ordering uses `mtime`; there is no distributed eviction coordinator beyond
  per-key locking, and quarantine is not auto-pruned.
- `.npz` window payloads are not byte-identical across writes (zip timestamps), so
  cache identity comes from the key, not from the payload hash.
- Remote reads, `max_pixels` limits, nodata handling and planetary CRS rules live in
  `raster_source.py`; this module performs no network I/O and no reprojection.

## Verification

```bash
uv run pytest pipelines/tests/test_raster_source.py pipelines/tests/test_raster_cache.py pipelines/tests/test_raster_perf.py -q
uv run pytest pipelines/tests -q
uv run ruff check pipelines/src/analog_pipelines/raster_cache.py pipelines/tests/test_raster_cache.py pipelines/tests/test_raster_perf.py
```

The cache tests are offline: temporary roots, in-memory bytes and a 32x32 synthetic
GeoTIFF read through `RasterSource`. No provider calls and no committed rasters.
