# Data release checklist (offline / local first)

This checklist covers the current pinned-snapshot release path. Real-source
downloads and live R2 publication remain deferred until explicitly authorized.

## Versions and inputs

- [ ] `contracts/contract.json` / `contracts/openapi.json` regenerated.
- [ ] v1 manifests unchanged except intended data change; `Manifest.model_validate` passes.
- [ ] v2 typed contracts/schemas regenerated (`scripts/export_v2_schemas.py`).
- [ ] Feature/method versions and source inventory statuses are recorded.
- [ ] No raw raster, cache, SQLite DB, `.env`, or credentials are staged.

## Offline validation

```bash
bash scripts/validate_data.sh
uv run pytest apps/api/tests pipelines/tests -q
bash scripts/verify.sh --backend-only
bash scripts/verify.sh
```

Expected current baseline (2026-11-14, before any new real ingestion):

```text
snapshot_id=snap-0cef4d75b147866a
data_version=dv-b46f3bad38ea
manifests=9 datasets=9
candidates=113 datasets=9 references=23 features=565 context_observations=1243
```

These counts come from committed manifests, not new downloads. Do not claim
coverage beyond the frozen snapshot.

## Multi-dimension release manifest (Issue #48)

The aggregate release combines the pinned fine snapshot, the 168-cell real coarse
Parquet index, the D02 typed export, the D03–D06 acquisition pilots, and the
source inventory. Absent body × dimension cells are explicit; nothing is
downloaded or zero-filled.

```bash
uv run python pipelines/build_release.py build    # samples/release/*.json
uv run python pipelines/build_release.py verify   # re-hash every artifact + count
uv run python pipelines/build_release.py report   # human summary
uv run pytest pipelines/tests/test_release_builder.py -q
```

Expected committed baseline (2026-11-15 inputs):

```text
release_id=rel-c30a25fab81d2b7f
artifacts=8 files=59
fine snapshot snap-0cef4d75b147866a data_version dv-b46f3bad38ea
candidates=113 references=23 feature_observations=565 context_observations=1243
typed_observations=181 (D02 56, D03 20, D04 20, D05 10, D06 75)
coarse_cells=168 regions=3
absent body×dimension cells=8 (D03–D06 × Moon/Mars)
```

- [ ] Two clean builds are byte-identical (manifest and EDA) and keep the same
      `release_id`.
- [ ] `verify` rejects a tampered typed observation, Parquet partition, or
      aggregate count.
- [ ] Pinned snapshot/manifest SHA-256 values are unchanged by the build and the
      old snapshot still verifies.
- [ ] SQLite AOI/job preservation is tested on copies only (`db_backup.py drill`).
- [ ] D06 raw SoilGrids rasters remain uncommitted; the report states
      `raw_files_not_bundled=75` instead of implying they were verified.
- [ ] No R2 publish, live reset, or global claim is made.

See [`MULTIDIMENSIONAL_RELEASE.md`](MULTIDIMENSIONAL_RELEASE.md) for the observed
EDA and evidence.


## Local two-build / restore check

```bash
uv run python pipelines/build_index.py \
  --snapshot samples/snapshot/analog-demo.snapshot.json \
  --database-url sqlite+pysqlite:///./var/release-a.db --reset
uv run python pipelines/build_index.py \
  --snapshot samples/snapshot/analog-demo.snapshot.json \
  --database-url sqlite+pysqlite:///./var/release-b.db --reset
uv run python -m analog_api.cli status --database-url sqlite+pysqlite:///./var/release-a.db
uv run python -m analog_api.cli status --database-url sqlite+pysqlite:///./var/release-b.db
```

Compare counts and data version. Test tampered checksum rejection and preservation
of user-created AOIs (separate runtime catalog).

## Snapshot/release artifacts

- [ ] Immutable prefix is NEW; never overwrite a prior snapshot.
- [ ] Snapshot/manifest/artifact SHA-256 values recorded.
- [ ] Clean destination fetch verifies checksums.
- [ ] EDA report distinguishes attempted/valid/failed/no-coverage and measured vs
      estimated; no global claim from a local subset.
- [ ] R2 publish only after separate maintainer authorization and protected environment.

## Rollback

Select a previous immutable snapshot and rebuild SQLite from it; runtime AOI
manifests under `ANALOG_AOI_RUNTIME_DIR` are not part of the baseline snapshot and
must be reprocessed explicitly.
