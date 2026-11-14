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
