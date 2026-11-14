# Optional Cloudflare R2 / S3-compatible setup

The application, offline tests, demo index rebuild, and CI do **not** need object storage. Follow
this only if the team wants to publish/fetch pinned snapshots to a shared bucket.

## What to create (Cloudflare dashboard)

1. An R2 bucket, e.g. `planetary-analog-artifacts` (any private bucket name).
2. An R2 API token with **Object Read & Write** for that bucket.
   - Access Key ID and Secret Access Key are shown once.
3. Your account ID from the R2 overview page; the S3 endpoint is
   `https://<ACCOUNT_ID>.r2.cloudflarestorage.com`.

Do not paste keys into chat, Git, issues, or frontend code.

## Fill locally (never commit)

Create `.env` (already gitignored) or export in your shell:

```bash
ANALOG_OBJECT_STORE_BACKEND=s3
ANALOG_OBJECT_STORE_ENDPOINT=https://<ACCOUNT_ID>.r2.cloudflarestorage.com
ANALOG_OBJECT_STORE_BUCKET=<bucket-name>
ANALOG_OBJECT_STORE_REGION=auto
AWS_ACCESS_KEY_ID=<R2-access-key-id>
AWS_SECRET_ACCESS_KEY=<R2-secret-access-key>
```

Install the optional client once:

```bash
uv sync --extra storage
```

The API container/web app never receive these values. Publishing is a local/operator command.

## Verify

```bash
# 1. Validate the pinned snapshot and its manifests/artifacts locally
make data-validate

# 2. Publish immutable objects under snapshots/<snapshot-id>/...
make data-publish

# 3. Fetch the snapshot back to a clean directory and verify checksums
make data-sync SNAPSHOT_ID=<snapshot-id>
```

Expected publish output includes the snapshot id and the destination prefix. A misconfigured or
missing credential fails immediately with a clear error and never prompts.

## Restore/index

```bash
uv run python pipelines/build_index.py \
  --snapshot data/snapshots/<snapshot-id>/<snapshot-file>.json \
  --database-url sqlite+pysqlite:///./var/restored.db --reset
```

Verify candidate/dataset/reference/context counts and `data_version` match the snapshot.

## Honest status

The S3-compatible adapter is implemented but **has not been exercised against a live R2 bucket in
this repository** (no credentials were available). Local object-store publish/fetch and snapshot
verification/rebuild are tested. After your first real publish/fetch, record the observed output in
`docs/DATA_ARCHITECTURE.md` before claiming R2 works in project documentation.
