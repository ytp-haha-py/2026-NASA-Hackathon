For the current bounded Earth/Moon/Mars batch extension and configurable local raw,
cache, processed and temporary directories, see [TERRAIN_EXPANSION.md](TERRAIN_EXPANSION.md).
Use `uv run --env-file .env` so temporary and tool cache settings apply before startup.
Do not reset the live index containing user AOIs; use `seed --keep-existing`.

# Data pipeline

This document gives the actual commands for the v6 data path. `make data-*` targets are
the intended team-facing interface and are being added by the lead. Until they land, use
the equivalent Python commands below; the existing `make data-earth` and `make data-moon`
targets already call the ingestion scripts.

## Stage map

```
discover -> subset -> clean -> validate -> extract -> cache -> publish -> sync -> rebuild -> rollback
```

| Stage | What happens | Credentials |
|---|---|---|
| discover | Resolve an official product URL/tile and print the plan | none |
| subset | Read only a bounded window/ROI | none |
| clean | Reproject to a metric CRS, handle nodata, enforce valid fraction ≥ 0.8 | none |
| validate | Validate ids, units, method version, values, and manifest provenance | none |
| extract | Compute the four `terrain-metrics-1.0.0` metrics (or context fractions) | none |
| cache | Store disposable local crops under `data/cache/` (ignored) | none |
| publish | Pin manifests in a deterministic snapshot; optionally copy to object storage | none for Git/local |
| sync | Fetch a snapshot from object storage and verify checksums | cloud only |
| rebuild | Recreate SQLite from pinned manifests | none |
| rollback | Restore an older snapshot + its manifests and rebuild | none |

## 0. Setup

```bash
uv sync
```

No cloud credentials, `boto3`, or network are needed for the offline demo/rebuild path.

## 1. Discover (no writes)

```bash
# Earth Copernicus GLO-90: intended tiles/URLs only
uv run python pipelines/earth/ingest_copernicus.py --dry-run

# Earth land-cover context: resolved WorldCover tiles only
uv run python pipelines/worldcover/ingest_worldcover.py --dry-run

# Moon LOLA: verified Site04 products, sizes, ROI centres
uv run python pipelines/planetary/lola_import.py --list-products
uv run python pipelines/planetary/lola_import.py --dry-run
```

## 2. Subset

The importers read only AOI windows via GDAL/HTTP range requests. Do not download global
tiles or full products.

```bash
# One Earth window
uv run python pipelines/earth/ingest_copernicus.py --only earth-death-valley

# All committed Earth windows
uv run python pipelines/earth/ingest_copernicus.py

# WorldCover context for one AOI or all AOIs
uv run python pipelines/worldcover/ingest_worldcover.py --only atacama-desert
uv run python pipelines/worldcover/ingest_worldcover.py
```

Existing Make targets:

```bash
make data-earth   # all Earth windows, network
make data-moon    # real LOLA reference import, network
```

WorldCover and snapshot/index targets do not exist in the Makefile yet; use the Python
commands here.

## 3. Clean and validate

Cleaning and manifest validation are part of each importer:

- Reproject/read into a metric CRS at 90 m for the 1.98 km analysis window.
- Reject windows with finite-cell fraction below 0.8.
- Validate feature ids, units, domains, and `method_version` against
  `apps/api/src/analog_api/domain/features.py`.
- Validate the manifest with `analog_api.domain.manifest.Manifest`.
- Fail closed: a failed AOI is omitted; no value is synthesized.

A manifest is the handoff artifact. Do not hand-edit measured values.

## 4. Extract and cache

- Terrain: Horn 3×3 slope, p95−p05 relief, plane-detrended roughness, all
  `terrain-metrics-1.0.0` (`pipelines/src/analog_pipelines/features.py`).
- Context: WorldCover class fractions at native 10 m, `worldcover-2021-v200-1.0.0`.
- Local caches live under `data/cache/` and are gitignored. They are disposable.

## 5. Publish the snapshot

The snapshot pins every committed manifest in:

- `pipelines/earth/manifests/`
- `pipelines/worldcover/manifests/`
- `pipelines/planetary/manifests/`

```bash
uv run python -m analog_pipelines.snapshot build

uv run python -m analog_pipelines.snapshot verify \
  --snapshot samples/snapshot/analog-demo.snapshot.json
```

`build` writes:

- `samples/snapshot/analog-demo.snapshot.json`
- `samples/snapshot/analog-demo.index-summary.json`

Both are small, deterministic JSON files. The snapshot id is content-derived; rebuilding
the same manifest bytes yields the same id and artifact hashes. `created_at` defaults to
the newest manifest `generated_at`, or `SOURCE_DATE_EPOCH` when set. `source_commit` is
`null` unless supplied with `--source-commit` (the builder runs no Git command).

Optional local object-store copy:

```bash
uv run python -m analog_pipelines.snapshot publish \
  --snapshot samples/snapshot/analog-demo.snapshot.json \
  --store-root data/object-store
```

This writes immutable keys:

```
snapshots/$SNAPSHOT_ID/snapshot.json
snapshots/$SNAPSHOT_ID/artifacts/analog-demo.index-summary.json
snapshots/$SNAPSHOT_ID/checksums.json
```

Cloud transfer is the same command without `--store-root`, after exporting the variables
in `docs/DATA_ARCHITECTURE.md`. `boto3` is optional and is not required for local work.

## 6. Sync / fetch

```bash
uv run python -m analog_pipelines.snapshot fetch \
  --snapshot-id "$SNAPSHOT_ID" \
  --destination data/snapshots/"$SNAPSHOT_ID" \
  --store-root data/object-store
```

Publish/fetch fail with exit code 2 when no store is configured. They never prompt for
credentials. Fetch verifies the snapshot checksum sidecar, the snapshot id, artifact
hashes, and any pinned manifests present in the local checkout.

## 7. Rebuild the SQLite index

Verification happens before any database connection:

```bash
# Verify and count only; does not create the database
uv run python pipelines/build_index.py \
  --snapshot samples/snapshot/analog-demo.snapshot.json \
  --database-url sqlite+pysqlite:///./var/demo-index.db \
  --dry-run

# Clean rebuild
uv run python pipelines/build_index.py \
  --snapshot samples/snapshot/analog-demo.snapshot.json \
  --database-url sqlite+pysqlite:///./var/demo-index.db \
  --reset

# Inspect the rebuilt index
uv run python -m analog_api.cli status \
  --database-url sqlite+pysqlite:///./var/demo-index.db
```

The builder uses the existing seed machinery; it does not duplicate scoring or seed
logic. At the time of writing, the committed snapshot pinned 3 manifests and a clean
rebuild produced:

```
candidates=5 datasets=3 references=1 features=20 context_observations=55
```

with a `data_version` identical to the snapshot's. Re-running with `--reset` produced
identical counts and dataset metadata. The database file itself differs because
`seeded_at` changes. Always use the counts and version printed by the current commands,
not copied strings.

## 8. Offline demo (no network)

```bash
uv run python -m analog_pipelines.snapshot build
uv run python -m analog_pipelines.snapshot verify \
  --snapshot samples/snapshot/analog-demo.snapshot.json
uv run python pipelines/build_index.py \
  --snapshot samples/snapshot/analog-demo.snapshot.json \
  --database-url sqlite+pysqlite:///./var/demo-index.db --reset
```

This path reads committed manifests and writes only the snapshot, its artifact, and a
local SQLite file. It works without `boto3`, AWS variables, or internet access.

## 9. Rollback

1. Select the previous reviewed snapshot id and restore **both** the snapshot and the
   manifest files it pins from the previous Git revision/tag.
2. Verify:

   ```bash
   uv run python -m analog_pipelines.snapshot verify \
     --snapshot samples/snapshot/analog-demo.snapshot.json
   ```

3. Rebuild into a fresh/disposable index with `--reset`.
4. Keep older `snapshots/<id>/...` object prefixes; do not overwrite them.

If only the snapshot file is restored while manifests have moved, verification fails by
design. A rollback is a return to pinned bytes, not an edit.

## 10. Quality gate

```bash
# Focused v6 tests (offline)
uv run pytest pipelines/tests/test_storage.py pipelines/tests/test_snapshot.py -q

# Lint/format
uv run ruff check .
uv run ruff format --check .

# Existing pipeline and contract tests
uv run pytest pipelines/tests pipelines/earth/tests -q

# Full repository gate (backend + frontend + contracts)
make verify
```

Never report a passing gate without command output. Never present a subset result as a
global survey.

## Make interface (proposed names for the lead)

These targets are implemented in the repository Makefile. Publishing/fetching require an explicit local store root or configured S3/R2 environment; they never prompt.

| Proposed target | Equivalent command |
|---|---|
| `make data-earth-dry-run` | `uv run python pipelines/earth/ingest_copernicus.py --dry-run` |
| `make data-earth` | `uv run python pipelines/earth/ingest_copernicus.py` (already exists) |
| `make data-worldcover` | `uv run python pipelines/worldcover/ingest_worldcover.py` |
| `make data-moon` | `uv run python pipelines/planetary/lola_import.py` (already exists) |
| `make data-snapshot` | `uv run python -m analog_pipelines.snapshot build` |
| `make data-verify` | `uv run python -m analog_pipelines.snapshot verify --snapshot samples/snapshot/analog-demo.snapshot.json` |
| `make data-publish` | `uv run --extra storage python -m analog_pipelines.snapshot publish --snapshot samples/snapshot/analog-demo.snapshot.json` (uses configured env) |
| `make data-fetch SNAPSHOT_ID=<id>` | `uv run --extra storage python -m analog_pipelines.snapshot fetch --snapshot-id <id> --destination data/snapshots` |
| `make data-index` | `uv run python pipelines/build_index.py --snapshot samples/snapshot/analog-demo.snapshot.json --database-url sqlite+pysqlite:///./var/demo-index.db --reset` |
| `make data-index-dry-run` | same with `--dry-run` |

No target above should hide credential prompts, downloads, or destructive resets. `make
data-index` should use a disposable demo database, not the API's production path.
