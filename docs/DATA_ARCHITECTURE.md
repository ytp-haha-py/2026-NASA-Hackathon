# Data architecture (v6)

This document describes the three storage layers added for reproducible data handoff.
It is based on the code in `pipelines/src/analog_pipelines/storage.py`,
`pipelines/src/analog_pipelines/snapshot.py`, and `pipelines/build_index.py`.

## Status at this revision

| Capability | Status | Evidence |
|---|---|---|
| Local object store | Implemented and exercised by offline tests | `LocalObjectStore` roundtrip, traversal rejection, checksum sidecars |
| Snapshot build / verify | Implemented and exercised | 3 pinned manifests, 1 JSON artifact, deterministic id |
| SQLite rebuild from snapshot | Implemented and exercised | Checksums verified before seeding; identical counts on repeat |
| S3-compatible (R2/S3) adapter | **Verified locally against a live R2 bucket** (publish + fetch, 3 objects) | `published ... objects=3`; `make data-sync SNAPSHOT_ID=snap-d3ed2ba59558e7f8` fetched and hash-matched; GitHub protected publish workflow not run |
| DVC | Not adopted | Optional migration note below |
| On-demand AOI API | Implemented (see `docs/API.md`) | `POST /areas/analyze`, `GET /jobs/{id}`, `GET /areas/{id}/features`; results persist in SQLite with provider status |

No claim in this repository should be read as "R2 is working" until a maintainer records an
actual publish/fetch run with a real bucket.

## The three layers

| Layer | Contents | Mutable? | Committed? |
|---|---|---|---|
| Git | code, contracts, small JSON manifests, snapshot JSON, `configs/areas.yaml`, `catalog/datasets.yaml`, documentation | no (reviewed commits) | yes |
| Object storage | snapshots + JSON artifacts, and later larger reproducible products (COG subsets, caches intended for sharing) | append-only in practice | no; local root is ignored |
| SQLite | `datasets`, `candidates`, `feature_observations`, `context_observations`, `planetary_references`, `analysis_jobs`, `meta` | yes; disposable | no (`*.db` ignored) |
| Runtime AOI catalog | JSON manifests per user-created AOI under `ANALOG_AOI_RUNTIME_DIR` (default `var/aoi-catalog/`) | append/update per area | no (`var/` ignored) |

Git is the source of truth for **what** was measured and which bytes are pinned. Object
storage is for **large bytes**. SQLite is a **rebuildable index** for one instance. The
API reads SQLite; it never needs object-store credentials.

## Git layer

Committed, reviewable inputs:

- `pipelines/earth/manifests/*.json` — observed Earth terrain windows.
- `pipelines/worldcover/manifests/*.json` — Earth land-cover context for the same AOIs.
- `pipelines/planetary/manifests/*.json` — verified Moon/Mars reference windows (one real LOLA ROI).
- `samples/snapshot/analog-demo.snapshot.json` — the pinned demo snapshot (small).
- `samples/snapshot/analog-demo.index-summary.json` — deterministic JSON artifact.
- `configs/areas.yaml` — known/indexed AOIs.
- `catalog/datasets.yaml` — source/verification matrix.

Manifests are validated by `analog_api.domain.manifest.Manifest` before they can seed the
index. Raw rasters, crops, and caches are not committed.

## Object storage layer

`analog_pipelines.storage` defines a narrow `ObjectStore` protocol:

```python
put_bytes(key, data, content_type=None) -> str
get_bytes(key) -> bytes
exists(key) -> bool
describe() -> dict
```

### Local backend

`LocalObjectStore(root)` is the default and the only backend exercised in CI:

- Default root: `data/object-store` (relative to the process working directory).
- Keys are normalized: absolute paths, URL schemes, backslashes, empty segments, `.` and
  `..` are rejected before any filesystem access.
- Writes are atomic (`tempfile` in the destination directory + `os.replace` + `fsync`).
- Every `put_bytes` writes a `<key>.sha256` sidecar. `checksum(key)` and `manifest(prefix)`
  expose the recorded hashes.
- The store root gets a self-ignoring `.gitignore` (`*`, `!.gitignore`) so artifacts do
  not enter Git. A repository-level ignore entry for `data/object-store/` is still
  recommended as a belt-and-braces measure.

### S3-compatible backend (R2 / S3)

`S3CompatibleObjectStore` reads configuration **only** from environment variables. It
never prompts, never logs secrets, and never hard-codes bucket, account, or endpoint
values.

| Variable | Purpose |
|---|---|
| `ANALOG_OBJECT_STORE_BACKEND` | `s3`, `r2`, or `s3-compatible` to request cloud storage; `local` forces local |
| `ANALOG_OBJECT_STORE_ENDPOINT` | S3-compatible endpoint URL (for example an R2 endpoint) |
| `ANALOG_OBJECT_STORE_BUCKET` | Bucket name |
| `ANALOG_OBJECT_STORE_REGION` | Region string required by the client |
| `AWS_ACCESS_KEY_ID` | Access key id (environment only) |
| `AWS_SECRET_ACCESS_KEY` | Secret access key (environment only) |
| `ANALOG_OBJECT_STORE_ROOT` | Optional local root override |

`boto3` is an **optional** import. If it is missing, or any required variable is absent,
construction raises `ObjectStoreNotConfigured` with the missing variable names. The
message never includes variable values. The API, CI, snapshot build/verify, and SQLite
rebuild do not import `boto3`.

`store_from_env()` returns `LocalObjectStore` when no cloud configuration is present. In
contrast, the `publish` and `fetch` CLI subcommands require an explicit store: pass
`--store-root` for local transfer, or set the cloud variables. There is deliberately no
silent fallback to the local default for transfer, and there are no prompts.

### Key layout and naming

Snapshots are content-addressed and published under an immutable prefix:

```
snapshots/$SNAPSHOT_ID/snapshot.json
snapshots/$SNAPSHOT_ID/artifacts/<artifact-basename>.json
snapshots/$SNAPSHOT_ID/checksums.json
```

`snapshot_id` is `snap-` plus the first 16 hex characters of the SHA-256 over the
canonical identity payload. Rebuilding the same manifest bytes produces the same id.
A published prefix is never rewritten in normal operation; a correction produces a new
snapshot id. The local backend cannot enforce immutability, so the workflow and review
rules do the enforcement.

## Snapshot contract (`snapshot_version` 1.0.0)

| Field | Meaning |
|---|---|
| `snapshot_version` | Contract version string |
| `snapshot_id` | `snap-<16 hex>` content id |
| `created_at` | Build timestamp; defaults to the newest manifest `generated_at` (or `SOURCE_DATE_EPOCH`) so builds are deterministic |
| `generated_by` | Producing module/path |
| `data_version` | Same deterministic `dv-...` hash the seed computes for the same manifests |
| `source_commit` | Optional; `null` when no commit was supplied. No Git command is run by the builder |
| `manifests[]` | `path` (repo-relative), `sha256`, `data_kind`, `bytes`, and whether the manifest is seedable |
| `artifacts[]` | `path`, `sha256`, `kind`, `bytes` |
| `datasets[]` | Dataset ids, providers, product ids, licences, source resolution/CRS, status, and verification flag |
| `notes` | Scope/limitations text |

`snapshot_id` is derived from `snapshot_version`, `data_version`, `manifests`,
`artifacts`, and `datasets`. Volatile fields (`created_at`, `generated_by`,
`source_commit`, `notes`) are excluded so the same manifest bytes yield the same id.

The committed demo snapshot at this revision pins 3 manifests and 3 datasets (5 distinct
Earth candidates, 1 Moon reference) plus 1 JSON artifact. Run `snapshot verify` to print
the current `snapshot_id` and `data_version`; do not hard-code them in downstream code,
because they change when manifests change.

## SQLite layer

`pipelines/build_index.py` verifies the snapshot identity and every manifest SHA-256
**before** creating an engine or writing rows. It then calls the existing
`analog_api.adapters.seed.seed_manifests` machinery on exactly the pinned manifest files.
Scoring, manifest validation, and seed logic are not duplicated.

A rebuild from the committed snapshot produced:

```
candidates=5 datasets=3 references=1 features=20 context_observations=55
```

with the same `data_version` as the snapshot. Re-running with `--reset` produced identical
counts and dataset metadata. SQLite rows include a `seeded_at` timestamp, so the database
file itself is not byte-identical across runs; the reproducibility guarantee is about
counts, provenance, and `data_version`.

## Shared vs generated artifacts

| Artifact | Shared via Git | Generated locally | Shareable via object store |
|---|---|---|---|
| Source code / contracts | yes | — | no |
| Small manifests | yes | — | optional |
| Snapshot + index summary | yes (demo) | yes | yes |
| SQLite index (`var/*.db`) | no | yes | no (per-instance) |
| DEM/land-cover raw rasters | no | cached only | not currently |
| Local COG crops (`data/cache/`) | no | yes | not currently |

## Rebuild

```bash
# 1. Build the deterministic snapshot and its JSON artifact
uv run python -m analog_pipelines.snapshot build

# 2. Verify identity, manifest hashes, data_version, and artifacts
uv run python -m analog_pipelines.snapshot verify \
  --snapshot samples/snapshot/analog-demo.snapshot.json

# 3. Rebuild a disposable index (checksum verification happens first)
uv run python pipelines/build_index.py \
  --snapshot samples/snapshot/analog-demo.snapshot.json \
  --database-url sqlite+pysqlite:///./var/demo-index.db --reset
```

`--dry-run` verifies and prints counts without creating the database. Omitting `--reset`
keeps existing rows, which is not a clean rebuild.

## Object-store transfer

```bash
# Local store (explicit root; no credentials)
uv run python -m analog_pipelines.snapshot publish \
  --snapshot samples/snapshot/analog-demo.snapshot.json \
  --store-root data/object-store

uv run python -m analog_pipelines.snapshot fetch \
  --snapshot-id "$SNAPSHOT_ID" \
  --destination data/snapshots/"$SNAPSHOT_ID" \
  --store-root data/object-store
```

For R2/S3, export the variables from the table above and omit `--store-root`:

```bash
export ANALOG_OBJECT_STORE_BACKEND=r2
export ANALOG_OBJECT_STORE_ENDPOINT=...   # your endpoint
export ANALOG_OBJECT_STORE_BUCKET=...
export ANALOG_OBJECT_STORE_REGION=auto
export AWS_ACCESS_KEY_ID=...
export AWS_SECRET_ACCESS_KEY=...
# boto3 must be installed only in the environment that performs transfer.
uv run python -m analog_pipelines.snapshot publish \
  --snapshot samples/snapshot/analog-demo.snapshot.json
```

`fetch_snapshot` verifies `checksums.json`, the downloaded snapshot bytes, the snapshot id,
and every artifact hash before reporting success. It also verifies manifest hashes when
the local checkout contains the pinned manifest files.

## Rollback

Object-store prefixes are append-only; roll back by selecting an older snapshot, not by
overwriting a newer prefix.

1. Restore the previous snapshot **and the manifests it pins** from the reviewed Git
   revision/tag. Restoring only the snapshot will fail checksum verification if manifests
   moved.
2. Run `snapshot verify` against the restored files.
3. Rebuild with `build_index --reset` and the same `data_version`.
4. Keep older `snapshots/<id>/...` objects; do not delete them during a rollback.

No manifest is ever "edited back" by hand. The previous commit's bytes are the rollback
artifact.

## Security

- Credentials live only in the server/CI environment, never in the browser, frontend
  bundle, SQLite, snapshots, logs, or Git.
- The API process does not need object-store credentials and does not import `boto3`.
- No command prompts for credentials. Missing configuration fails with variable names,
  not values.
- `describe()` exposes backend/root (local) or endpoint/bucket/region (S3), never keys.
- Snapshot/object keys are normalized; path traversal is rejected before I/O.
- Treat R2/S3 tokens as write secrets: publishing can overwrite mutable keys if an operator
  points at the wrong prefix. Use a dedicated bucket/prefix and least-privilege tokens.

## DVC migration (optional, not implemented)

The pinned-manifest + SHA-256 + deterministic publish/fetch design covers the current
need without a new tool or paid service. DVC is **not** a dependency and no DVC commands
have been verified here.

If artifact volume later makes DVC worthwhile, keep this boundary:

- DVC manages large binaries only (for example shared COG subsets), not manifests, the
  snapshot contract, or `catalog/`.
- Keep `ObjectStore` and the snapshot id as the portable interface; DVC remotes must not
  become the only way to verify provenance.
- Migration would need: a chosen remote, `.dvc` files for each large artifact, CI support,
  and an explicit decision record. It must not silently replace the checksummed snapshot.
- Do not use DVC and the snapshot publisher for the same artifact at the same time.

## Failure behavior

| Situation | Behavior |
|---|---|
| Manifest/artifact bytes changed | `SnapshotVerificationError`; index is not touched |
| Snapshot id does not match content | Verification fails |
| `data_version` differs from manifests | Verification fails |
| No object store for publish/fetch | Exit code 2 with an actionable message; no prompt |
| S3 requested but env incomplete | `ObjectStoreNotConfigured` listing missing variable names |
| S3 requested but `boto3` absent | `ObjectStoreNotConfigured`; install only where transfer is needed |
| Key traversal attempt | `InvalidObjectKey` before any I/O |
