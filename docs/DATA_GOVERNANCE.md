# Data governance (v7)

This document defines how measured data moves from an intake issue to a published, indexable
snapshot, and what is actually enforced versus what still needs a repository administrator.
It complements `docs/CONTRIBUTING_DATA.md` (provenance review), `docs/DATA_ARCHITECTURE.md`
(storage layers), and `docs/R2_SETUP.md` (operator credentials).

The pinned interface is the committed snapshot manifest plus SHA-256 checksums. DVC is **not**
adopted; see [DVC status](#dvc-status-not-adopted).

## Status at this revision

| Control | Status | Evidence / limitation |
|---|---|---|
| Offline snapshot verification | Verified locally | `uv run python -m analog_pipelines.snapshot verify --snapshot samples/snapshot/analog-demo.snapshot.json` → `manifests=3 artifacts=1 datasets=3`, `data_version=dv-19f54b4539c8` |
| Offline manifest schema gate | Implemented | `bash scripts/validate_data.sh` validates every `pipelines/*/manifests/*.json` against `contracts/pipeline-manifest.schema.json` |
| Offline index dry-run | Verified locally | `pipelines/build_index.py --dry-run` → `candidates=5 datasets=3 references=1 features=20 context_observations=55` |
| Offline CI workflow | Added, not yet observed on GitHub | `.github/workflows/data-validation.yml` (read-only, no secrets) |
| Weekly/manual online smoke | Verified locally, never run in CI here | `bash scripts/online_smoke.sh`; `.github/workflows/data-smoke.yml` is opt-in and non-blocking |
| Live R2 publication | **Not verified** | No credentials were used; `.github/workflows/data-publish.yml` is an unexecuted template |
| DVC | **Not adopted** | No DVC dependency, remote, or `.dvc` files exist |
| `main` branch protection | **Not active** | Rulesets returned HTTP 403 on the current GitHub plan, and the available account lacks admin; see `docs/GITHUB_SETUP.md` |
| `data-publish` environment protection | **Not configured** | Must be created in repository settings with required reviewers before first use |

Nothing in this document should be read as evidence that live object storage or protected
environments are working. Record observed output when a maintainer performs those steps.

## Dataset promotion policy

Every dataset follows the same order. Skipping a stage requires an explicit maintainer decision
recorded in the PR.

### 1. Intake issue

Open an Issue before ingestion. Capture provider, product id and version, official product and
licence URLs, attribution text, CRS/resolution/temporal epoch, access method, expected volume,
redistribution position, and known scientific limitations. Unknown fields are `not_verified`;
never guessed. `catalog/datasets.yaml` is the source matrix that records the resulting status
(`verified`, `in_progress`, `not_verified`, `planned`).

### 2. Code/adapters PR with offline CI

One dataset or one coherent revision per PR, on
`feat/<issue>-short-name` or `fix/<issue>-short-name`:

- Bounded pipeline code and offline tests (synthetic rasters only; network tests marked
  `network` and excluded from required CI).
- The regenerated manifest under `pipelines/*/manifests/`, produced by the pipeline rather than
  hand-edited.
- A rebuilt `samples/snapshot/analog-demo.snapshot.json` and
  `samples/snapshot/analog-demo.index-summary.json`, plus `catalog/datasets.yaml` updates.
- `bash scripts/validate_data.sh` and `bash scripts/verify.sh` pass locally.
- A reviewer other than the author approves; the author does not self-merge.

The PR must prove:

```bash
bash scripts/validate_data.sh
uv run python pipelines/build_index.py \
  --snapshot samples/snapshot/analog-demo.snapshot.json \
  --database-url sqlite+pysqlite:///./var/demo-index.db --dry-run
```

`scripts/validate_data.sh` is offline and write-free (it uses a temporary directory for the
dry-run database URL). It also fails when a committed manifest is not pinned in the snapshot,
so a manifest cannot drift ahead of its snapshot activation.

### 3. Merge to `main`

Merge only after review and required checks are green. Direct pushes, force pushes, auto-merge,
and self-approval are prohibited. Because `main` protection is not currently enforceable on the
repository plan (see `docs/GITHUB_SETUP.md`), the workflow discipline is the interim control;
do not treat it as technically enforced.

### 4. Separate trusted publish from `main`

Publishing is **not** a PR step and **not** part of `data-validation`. A maintainer runs
`.github/workflows/data-publish.yml` manually from `main` after the ingestion PR merges. The
workflow:

1. Requires `github.ref == 'refs/heads/main'` and the protected `data-publish` environment.
2. Verifies the committed snapshot before any transfer.
3. Maps repository secrets `R2_ENDPOINT`, `R2_BUCKET`, `R2_REGION`, `R2_ACCESS_KEY_ID`, and
   `R2_SECRET_ACCESS_KEY` to the object-store variables. Values are never echoed.
4. Publishes under the immutable prefix `snapshots/<snapshot_id>/` and writes
   `snapshots/<snapshot_id>/checksums.json`.

PR code never receives these secrets: fork PRs cannot read repository secrets, the workflow is
`workflow_dispatch`-only, and the job-level `main` guard rejects any other ref. The publish
workflow is a template until a maintainer records a real run in `docs/R2_SETUP.md` or
`docs/DATA_ARCHITECTURE.md`.

### 5. Snapshot-activation PR

After a successful publish, open a small activation PR (or record the evidence on the ingestion
issue when no tracked file changes). It:

- Records the published `snapshot_id`, `data_version`, prefix, and verification output.
- Updates any deployment/runtime pin that is not already Git-tracked, and `catalog/datasets.yaml`
  status if the promotion changes verification status.
- Never edits the published snapshot, reuses an existing id for different bytes, or overwrites
  a prefix. Corrections produce a new snapshot id.

In this repository the committed `samples/snapshot/analog-demo.snapshot.json` is usually already
the activation pin from Stage 2, so Stage 5 may only add publish evidence to documentation and
the issue. It still must happen before anyone describes the dataset as published.

### 6. Index rebuild and rollback

Rebuild the disposable SQLite index from the verified snapshot:

```bash
uv run python pipelines/build_index.py \
  --snapshot samples/snapshot/analog-demo.snapshot.json \
  --database-url sqlite+pysqlite:///./var/analog.db --reset
# or: make data-rebuild-index
```

The builder verifies checksums and `data_version` before creating an engine, so a bad snapshot
cannot partially seed a database. Counts must match the snapshot. To roll back, point the same
command at the previous pinned snapshot (fetch it first if needed) and rebuild with `--reset`;
never mutate a published snapshot to "undo" a change.

## Workflow inventory

| Workflow | Trigger | Required for PRs? | Secrets / network |
|---|---|---|---|
| `.github/workflows/data-validation.yml` | PR/push touching `pipelines/**`, `contracts/pipeline-manifest.schema.json`, `catalog/**`, `configs/**`, `samples/snapshot/**`; manual | Candidate required check (`data-validation`); register after first observation | None; no network data |
| `.github/workflows/data-smoke.yml` | `workflow_dispatch`; weekly schedule | **No — explicitly non-blocking** | None; real Copernicus/WorldCover network calls |
| `.github/workflows/data-publish.yml` | `workflow_dispatch` from `main` only | No | R2 repository secrets; protected `data-publish` environment |

`data-smoke` must not be added to required status checks. It is an early warning for provider or
network changes; its failure does not invalidate committed data. The existing `ci.yml` jobs
(`backend-tests`, `frontend-checks`, `integration-smoke`) remain the general code gate.

## Untrusted-PR security model

- Data validation runs on `pull_request` with `permissions: contents: read`, no secrets, and no
  write-capable token. It executes only committed repository code and never downloads source
  rasters.
- Never add `pull_request_target`, secret-bearing PR workflows, or a `workflow_run` job that
  checks out and executes fork code. If a future check needs secrets, it must be a manual,
  protected-environment workflow from `main`.
- Pin third-party actions and keep the default `GITHUB_TOKEN` read-only. Do not pass repository
  secrets to PR-triggered jobs or expose them in logs, artifacts, or summaries.
- Object-store credentials live only in the `data-publish` environment; the API, web app, CI
  validation, and reviewers never need them.

## Protected environment requirement

Before the first real publish, an administrator must:

1. Create the `data-publish` environment in **Settings → Environments**.
2. Add required reviewers (at least one maintainer other than the triggering author where
   possible) and prevent self-review.
3. Restrict deployment branches to `main` only.
4. Add the five R2 secrets as environment or repository secrets; prefer environment secrets so
   PR and unrelated workflows cannot read them.
5. Confirm the workflow's `if: github.ref == 'refs/heads/main'` guard remains, then run it from
   `main` and record the observed output.

If the environment cannot be protected, **do not run the publish workflow**. It is intentionally
inert until these settings exist. Current GitHub plan/admin limitations are recorded in
`docs/GITHUB_SETUP.md`.

## Immutable object keys and checksums

- Layout: `snapshots/<snapshot_id>/<snapshot-file>.json`, pinned artifacts, and
  `snapshots/<snapshot_id>/checksums.json`.
- `snapshot_id` is derived from canonical content; it changes when pinned bytes or
  `data_version` change. It is never hand-edited.
- `snapshots/<snapshot_id>/...` is append-only. A correction publishes a new id; it does not
  overwrite existing keys.
- `checksums.json` records the snapshot id and per-file SHA-256 values. Fetch verifies those
  hashes and recomputes `snapshot_id` before writing into the destination.
- Git commits small JSON manifests, the snapshot, and the index-summary artifact; it does not
  commit raw rasters or a database. `scripts/validate_data.sh` and
  `analog_pipelines.snapshot verify` are the byte-level checks.

## Source licensing and redistribution

- The licence and licence URL are copied verbatim from the official source into each manifest
  and `catalog/datasets.yaml`; attribution text is not paraphrased.
- A dataset is `verified` only when a validated manifest exists at this revision. "Verified"
  means provenance and access were checked, not that the source is globally valid or that a
  handful of 2 km windows characterize a region.
- Committed derived values require a licence that permits redistribution with attribution.
  Current examples: Copernicus DEM GLO-90 (Copernicus Contributing Mission terms), ESA
  WorldCover 2021 v200 (CC BY 4.0), NASA LRO-LOLA Site04 (CC0-1.0 per the NASA Science Data
  Portal). Re-check the licence before adding a product; record `not_verified` when unclear.
- Do not commit credentials, signed URLs, account ids, or bucket names with access keys.
- Do not redistribute raw products from this repository. Users obtain source data from the
  official provider under that provider's terms.

## Raw versus derived data

| Class | Examples | Committed? | Policy |
|---|---|---|---|
| Raw source | Global DEM/COG tiles, full WorldCover maps, LOLA GeoTIFFs | No | Download or range-read on demand; never committed, never re-hosted |
| Derived | Feature values, valid fractions, quality flags, AOI bounds, provenance | Yes, as small JSON manifests | Reproducible from pinned method versions and documented access |
| Pinned handoff | `samples/snapshot/analog-demo.snapshot.json`, index summary | Yes, small | SHA-256 pinned; verify before any rebuild or publish |
| Disposable index | `var/*.db`, runtime caches | No | Rebuildable; ignored by Git |
| Large shared artifacts | Future COG subsets or shared caches | Object storage only | Append-only keys with checksums; never the only provenance record |

Raw rasters and local crops never enter Git. If an artifact is too large for a reviewed JSON
handoff, it belongs in object storage and must still be referenced by a checksummed snapshot.

## Runtime AOI cache versus published snapshot

These are different data products and must not be conflated:

- **On-demand AOI analysis** (`POST /areas/analyze`, `analog_api.adapters.jobs`): results are
  written to the instance-local SQLite database with `origin: on_demand`, per-provider
  `provider_status`, and `data_kind: observed` only when the providers actually validated.
  It is a local cache/queue for one deployment, not a curated dataset.
- **Published snapshot**: reviewed manifests with pinned SHA-256 values, a deterministic
  `snapshot_id`, and explicit catalogue status. It is reproducible from Git and object storage.

An AOI result never becomes a manifest or snapshot automatically. Promotion requires a pipeline
run that writes a manifest, an offline-CI PR, review, a rebuilt snapshot, and the stages above.
AOI caches are not backed up, not published, and may be discarded. Do not cite an AOI cache as
coverage, and do not treat `partial`/`failed` provider status as observed data.

## DVC status (not adopted)

DVC is not a dependency and no DVC command has been verified here. The current design already
provides content identity and reproducible transfer:

- Manifests and the snapshot are small, reviewable JSON.
- `snapshot verify` checks SHA-256 values and `data_version` offline.
- The object store uses an `ObjectStore` protocol with immutable `snapshots/<id>/` prefixes and
  checksum sidecars.

If artifact volume later justifies DVC, keep the boundary documented in
`docs/DATA_ARCHITECTURE.md`: DVC may manage large binaries only, must not replace the snapshot
contract or `catalog/datasets.yaml`, and must not manage the same artifact as the snapshot
publisher. Migration needs a chosen remote, `.dvc` files, CI support, and a decision record.

## Rollback

1. Identify the last known-good snapshot id and its `checksums.json` evidence.
2. Restore bytes:
   ```bash
   uv run --extra storage python -m analog_pipelines.snapshot fetch \
     --snapshot-id <previous-snapshot-id> --destination data/snapshots
   ```
3. Verify the restored snapshot:
   ```bash
   uv run python -m analog_pipelines.snapshot verify \
     --snapshot data/snapshots/<previous-snapshot-id>/<file>.snapshot.json
   ```
4. Rebuild the disposable index with `--reset` (see above) and confirm candidate, dataset,
   reference, feature, and context counts plus `data_version`.
5. In Git, revert the activation/pin change; do not delete or overwrite the published prefix.
   Keep the failed snapshot available for diagnosis and document the rollback reason.

Rollback is expected to be routine because the index is disposable and every input is pinned.
If verification fails, stop: do not seed from unverified bytes.

## Operator command reference

```bash
# Offline gate (this document's contract)
bash scripts/validate_data.sh

# Equivalent focused checks
make data-validate          # snapshot checksums
make data-index-dry-run     # counts without writing
make data-demo              # rebuild ./var/demo-index.db from the pinned snapshot

# Full code gate (backend + frontend + contracts)
bash scripts/verify.sh

# Opt-in network smoke (never a required PR check)
bash scripts/online_smoke.sh

# Publish / restore (requires configured object storage; see docs/R2_SETUP.md)
make data-publish
make data-sync SNAPSHOT_ID=<snapshot-id>
```

When reporting results, paste observed output and counts. "Should pass" is not evidence. Do not
claim a live R2 publication, a protected environment, or DVC support until the corresponding
step has actually run and been recorded.
