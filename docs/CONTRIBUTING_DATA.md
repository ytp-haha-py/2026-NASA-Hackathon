# Contributing data

This guide covers dataset, manifest, snapshot, and index changes. General code workflow is
in `CONTRIBUTING.md`; this file focuses on provenance and scientific review.

## Workflow

`Issue -> feat/<issue>-short-name or fix/<issue>-short-name -> PR -> review -> required CI -> merge`

- Open an Issue before ingestion. Do not push to `main`.
- One dataset or one coherent dataset revision per PR.
- Include pipeline code, the regenerated manifest, snapshot/artifact updates,
  `catalog/datasets.yaml`, `configs/areas.yaml` when AOIs change, docs, and tests.
- A PR needs a reviewer other than the author; authors do not self-merge.

## Adding or changing a dataset

### 1. Intake issue

Record, with official links:

- Provider, product id, product version, and stable dataset id.
- Official product page, download/access URL, and licence URL.
- Licence terms and attribution/citation text exactly as published.
- Coverage, native CRS, resolution, vertical datum, units, and temporal epoch.
- Access method (public unsigned, authenticated, archive request) and expected volume.
- Redistribution position: may derived values be committed? May raw bytes be redistributed?
- Scientific limitations and validation plan.

If a field is unknown, write `not_verified`; do not guess.

### 2. Implement a bounded pipeline

- Follow the existing pattern: resolve the product, read only the AOI window via range
  requests, reproject to a metric CRS, enforce valid fraction ≥ 0.8, compute features with
  `terrain-metrics-1.0.0` (or the documented context method).
- Never download a global product or commit a large raster.
- Never synthesize a missing value, fill an AOI from another site, or hand-edit measured
  numbers.
- Context (for example land cover) must stay separate from terrain similarity features.
- Add offline tests with synthetic rasters for extraction/validation behavior. Network
  tests must be marked and must not be required in CI.

### 3. Regenerate the manifest

The pipeline writes the manifest under the appropriate `pipelines/*/manifests/`
directory. Validate it through the shared manifest model; do not hand-author provenance.

Typical commands:

```bash
uv run python pipelines/earth/ingest_copernicus.py --only <area>
uv run python pipelines/worldcover/ingest_worldcover.py --only <area>
uv run python pipelines/planetary/lola_import.py --dry-run
```

Then rebuild and verify the snapshot:

```bash
uv run python -m analog_pipelines.snapshot build
uv run python -m analog_pipelines.snapshot verify \
  --snapshot samples/snapshot/analog-demo.snapshot.json
```

Commit the regenerated `samples/snapshot/analog-demo.snapshot.json` and
`samples/snapshot/analog-demo.index-summary.json` with the manifest. The new
`data_version` and `snapshot_id` are expected in the diff.

### 4. Rebuild the index and inspect

```bash
uv run python pipelines/build_index.py \
  --snapshot samples/snapshot/analog-demo.snapshot.json \
  --database-url sqlite+pysqlite:///./var/demo-index.db --reset
```

Do not commit `var/*.db`. Record the observed candidate/reference/dataset/feature counts
in the PR description. Repeated rebuilds from the same snapshot must match.

### 5. Update registries

- `catalog/datasets.yaml`: copy provider/product/licence/coverage/resolution values from
  the manifest or official page. Set `status` honestly.
- `configs/areas.yaml`: add/adjust an AOI only with manifest-backed bounds. For a source
  with no valid WGS84 bbox, store `bbox: null` and document the authoritative coordinates
  (as the Moon reference does).
- `docs/DATASETS.md` and (if the access path changed) `docs/DATA_SOURCES.md`.
- `docs/DATA_ARCHITECTURE.md` only if the storage contract changed.

## Review checklist

### Provenance

- [ ] Official provider/product page and licence URL resolve and match the manifest.
- [ ] `dataset_id`, `product_id`, `provider`, `access_timestamp`, `source_crs`,
      `native_resolution_m`, `processing_version`, and `checksum` are present and consistent.
- [ ] Attribution/citation text is exact; no paraphrased licence claims.
- [ ] The manifest says `data_kind=observed`, never synthetic, for real measurements.

### Rights and redistribution

- [ ] Licence permits committed derived values with attribution.
- [ ] Raw rasters/crops are not committed.
- [ ] No credentials, tokens, bucket account ids, or signed URLs enter Git, snapshots,
      docs, logs, or the frontend.
- [ ] No paid or authenticated service is required for the offline/CI path.

### CRS, resolution, and methods

- [ ] Earth uses a metric analysis CRS; no mixing of degrees with metres.
- [ ] Native and analysis resolutions are distinct and documented.
- [ ] Vertical datum/units are stated (or explicitly `not_verified`).
- [ ] Feature ids/units/domains and `method_version` match the central registry.
- [ ] Context methods and versions are separate from terrain metrics.

### Scientific QA

- [ ] AOI bounds are real and reproducible from documented source coordinates.
- [ ] Bounded window/range read only; no global download.
- [ ] Valid-fraction threshold and nodata handling are tested.
- [ ] The 1.98 km window is labelled a local sample, not a regional/global claim.
- [ ] Source limitations are disclosed (DSM, interpolation, static epoch, classification
      accuracy, polar longitude conditioning, etc.).
- [ ] Moon/Mars references exist only when a real raster ROI validated; otherwise the body
      stays unavailable.
- [ ] No absolute planetary altitude is compared with Earth sea level.

### Dataset diff and reproducibility

- [ ] Every changed manifest is pinned in the new snapshot with its SHA-256.
- [ ] `snapshot verify` passes and `data_version` matches the rebuilt index.
- [ ] Two rebuilds with `--reset` produce identical counts and dataset metadata.
- [ ] The PR lists old vs new dataset ids, candidate/reference counts, and the reason for
      changed values.
- [ ] Tests are offline and pass; network-only checks are labelled.
- [ ] `catalog/datasets.yaml`, `configs/areas.yaml`, and docs agree with the manifest.

### Snapshot and version rules

- [ ] `snapshot_version` changes only for a contract-shape change.
- [ ] `snapshot_id` is regenerated, never edited.
- [ ] An existing object-store prefix is not overwritten for a correction; publish a new
      snapshot id.
- [ ] `method_version` changes are explicit and exclude cross-version comparisons.
- [ ] If a manifest is removed, the PR explains why and updates catalog/areas/docs.

## Status and version semantics

- `data_version` is derived from the canonical validated manifests. It changes when
  provenance or observations change.
- `snapshot_id` is derived from the pinned manifest hashes, dataset summary, artifact
  hashes, and `data_version`. It is stable for unchanged bytes.
- A dataset may be `verified` for provenance while still being scientifically limited.
  State the limitation; do not upgrade "verified" to "universally valid".
- WorldCover is verified **context**, not a similarity feature.
- MOLA is `planned`; no value or preset may be added before a real manifest validates.

## Commands before requesting review

```bash
uv run pytest pipelines/tests/test_storage.py pipelines/tests/test_snapshot.py -q
uv run pytest pipelines/tests pipelines/earth/tests -q
uv run ruff check .
uv run ruff format --check .
uv run python -m analog_pipelines.snapshot verify \
  --snapshot samples/snapshot/analog-demo.snapshot.json
make verify  # full gate when backend + frontend are in scope
```

Report observed output. "Tests should pass" is not evidence.

## Prohibited

- Fabricated coordinates, metrics, licences, citations, or access timestamps.
- Synthetic or placeholder data presented as observed.
- Copying values from another dataset or AOI to fill a gap.
- Committing raw global rasters, large crops, database files, or credentials.
- Claiming global coverage from a handful of 2 km windows.
- Claiming Moon/Mars support without a validated reference manifest.
- Editing a published snapshot id or overwriting its object-store prefix.
