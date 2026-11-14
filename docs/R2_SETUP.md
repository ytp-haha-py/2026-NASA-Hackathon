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
The snapshot CLI loads `.env` from the current directory/parents (without overriding already-exported
variables) for `publish`/`fetch`; API startup does not depend on it.

Credential sanity check: Cloudflare R2 **Access Key ID is 32 characters** and **Secret Access Key is
64 characters**. The 64-character token ID is not the access key. If publish fails with
`Credential access key has length 64, should be 32`, the two values are swapped.

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

## Verified status (2026-11-14)

- Live R2 publish: **verified locally** — `published snapshot_id=snap-d3ed2ba59558e7f8 prefix=snapshots/snap-d3ed2ba59558e7f8 objects=3`.
- Live R2 fetch: **verified locally** — `fetched snapshot_id=snap-d3ed2ba59558e7f8 ... artifacts=1`.
- The GitHub `data-publish` workflow has **not** been run (the protected environment/secrets are not
  configured), so it remains unverified.
- Local object-store publish/fetch, checksum verification, and snapshot/index rebuild are covered by
  offline tests.
- The operator `.env` used for this verification required swapping the two credential values to the
  correct lengths described above; no credential values were printed or committed.
