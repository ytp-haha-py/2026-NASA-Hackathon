# Deployment

## Local development

```bash
uv sync
cd apps/web && npm ci && cd ..
make api        # terminal 1: http://127.0.0.1:8000
make web        # terminal 2: http://localhost:5173 (proxies /api)
```

The API auto-seeds from committed manifests on first start (`./var/analog.db`). To reset:

```bash
rm -f var/analog.db
make seed
```

Demo-only synthetic data:

```bash
ANALOG_DATABASE_URL=sqlite+pysqlite:///./var/demo.db \
ANALOG_SEED_INCLUDE_SYNTHETIC=true \
uv run python -m analog_api.cli seed
```

The UI labels every synthetic record `DEMO / SYNTHETIC`; never ship the flag on in a real deployment.

## Cloudflare Tunnel endpoint (configured public deployment)

The configured public endpoint is:

```text
https://api.貓熊.台灣
https://api.xn--2vxr09c.xn--kpry57d   # punycode
```

Suggested routes:

| Hostname | Local service |
|---|---|
| `api.貓熊.台灣` | `http://127.0.0.1:8000` (FastAPI) |
| `貓熊.台灣`, `www.貓熊.台灣` | `http://127.0.0.1:8080` (web container) or `http://127.0.0.1:5173` (local Vite) |

The repository does **not** create the tunnel, DNS records or credentials automatically. A
Cloudflare account with zone access and either a tunnel token or `cloudflared` certificate is
required. Never commit the token, `cert.pem`, or `credentials.json`.

```bash
# One-time operator setup
cloudflared tunnel login
cloudflared tunnel create planetary-analog
cloudflared tunnel route dns planetary-analog api.貓熊.台灣
cloudflared tunnel route dns planetary-analog 貓熊.台灣
cloudflared tunnel route dns planetary-analog www.貓熊.台灣

# Example config: deploy/cloudflared/config.example.yml
cloudflared tunnel --config deploy/cloudflared/config.example.yml run
```

Frontend production builds default to `https://api.貓熊.台灣/api/v1` and can be overridden with
`VITE_API_BASE`. If the web app is on a different origin, add that origin to
`ANALOG_CORS_ORIGINS`, for example:

```dotenv
ANALOG_CORS_ORIGINS=http://localhost:5173,https://貓熊.台灣,https://www.貓熊.台灣
```

Verification, once the operator has authenticated the tunnel:

```bash
curl -fsS https://api.貓熊.台灣/api/v1/health
curl -fsS https://api.貓熊.台灣/api/v1/typed/coverage
wrangler whoami                 # requires CLOUDFLARE_API_TOKEN
cloudflared tunnel list         # requires cert.pem or TUNNEL_TOKEN
```

Verified in this WSL environment on 2026-11-15:

- `cf auth whoami`: authenticated OAuth profile with access to the `貓熊.台灣` zone.
- Tunnel `planetary-analog` (`8ac424cb-4adf-42f2-8813-35c4904184c3`): `healthy` with four
  active connections.
- DNS CNAMEs: `api`, apex and `www` all point to the tunnel.
- `https://api.貓熊.台灣/api/v1/health` and `/api/v1/typed/coverage`: HTTP 200.
- Web apex and `www`: HTTP 200.
- `cloudflared 2026.11.0` runs with `~/.cloudflared/config.yml` and a mode-600 credentials file.
  Tokens, certificates and credentials are not committed.

Local web preview currently runs on port `18081` (the host's `8080` is occupied); the example
config uses `8080` for the production web container. Adjust the ingress service to the actual web
port used on the host.

## Docker Compose

```bash
docker compose up --build
# web: http://localhost:8080
# api: http://localhost:8000/docs
docker compose down
```

Compose builds:
- `apps/api/Dockerfile` — multi-stage uv build, non-root runtime, SQLite at `/data/analog.db`,
  healthcheck on `/api/v1/health`.
- `apps/web/Dockerfile` — Vite build served by nginx; `/api` proxied to the `api` service.

No credentials or external services are required. The `analog-data` named volume holds SQLite. To
persist data outside Compose, replace the volume with a host bind mount and keep it writable by UID
10001.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `ANALOG_DATABASE_URL` | `sqlite+pysqlite:///./var/analog.db` | SQLAlchemy URL |
| `ANALOG_CORS_ORIGINS` | localhost dev origins | comma-separated allowed origins |
| `ANALOG_MANIFEST_DIR` | `pipelines/earth/manifests` | Earth terrain manifests |
| `ANALOG_MANIFEST_DIR_CONTEXT` | `pipelines/worldcover/manifests` | Earth land-cover context |
| `ANALOG_MANIFEST_DIR_PLANETARY` | `pipelines/planetary/manifests` | Moon/Mars reference manifests |
| `ANALOG_SEED_INCLUDE_SYNTHETIC` | `false` | load TEST-ONLY fixtures |
| `ANALOG_AOI_WORKERS` | `2` | bounded local AOI worker pool |
| `ANALOG_AOI_RUNTIME_DIR` | `var/aoi-catalog` (Compose: `/data/aoi-catalog`) | durable runtime AOI manifests |
| `ANALOG_AOI_INLINE` | `false` | run AOI jobs synchronously (tests only) |
| `VITE_API_PROXY_TARGET` | `http://127.0.0.1:8000` | Vite dev proxy target |

Copy `.env.example` to `.env` for local overrides. Never commit `.env`.

## On-demand AOI jobs

`POST /api/v1/areas/analyze` writes a durable row to the SQLite `analysis_jobs` table and returns
`202`. A bounded in-process worker pool (2 threads by default) does raster I/O; after a restart,
jobs left `running` are marked retryable and requeued. This is intentionally not a distributed
queue. Scale-out requires replacing the job adapter, not changing the API.

Real provider access requires outbound HTTPS to the Copernicus and ESA WorldCover public buckets.
The AOI is bounded to 2 km sides / 4 km², so each job reads only a few COG windows. If a provider
fails, the job is `partial`/`failed` with per-provider status; values are never zero-filled.

## Optional object storage (Cloudflare R2 / S3-compatible)

Offline snapshots and `make data-demo` work with no credentials. To publish/fetch team artifacts:

```bash
uv sync --extra storage
export ANALOG_OBJECT_STORE_BACKEND=s3
export ANALOG_OBJECT_STORE_ENDPOINT=https://<ACCOUNT_ID>.r2.cloudflarestorage.com
export ANALOG_OBJECT_STORE_BUCKET=<bucket>
export ANALOG_OBJECT_STORE_REGION=auto
export AWS_ACCESS_KEY_ID=<R2 key id>
export AWS_SECRET_ACCESS_KEY=<R2 secret>
make data-publish
make data-sync SNAPSHOT_ID=<snapshot-id>
```

Credentials are read only from the server environment, never bundled into the web app, logged, or
committed. Without configuration, publish/fetch fail with a clear error. Live R2/S3 publication is
not verified in this repository; record an actual run in `docs/DATA_ARCHITECTURE.md` before claiming
it works. DVC is not adopted; the pinned snapshot + SHA-256 path is the current interface.

## CI (pull requests and feature branches)

`.github/workflows/ci.yml` runs `backend-tests`, `frontend-checks`, and `integration-smoke`.
Installs are lockfile-based (`uv sync --frozen`, `npm ci`); tests never download DEM tiles and need
no secrets. `scripts/smoke.sh` starts an isolated API on a free port with synthetic fixtures.

Data governance workflows are separate:
- `data-validation.yml` (read-only) verifies the pinned snapshot, manifest JSON Schema, and a
  no-write index rebuild on data-affecting PRs.
- `data-smoke.yml` is a manual/weekly real-provider smoke; non-blocking.
- `data-publish.yml` is a manual, protected-environment publish from `main` only; it requires R2
  secrets and has not been executed. See `docs/DATA_GOVERNANCE.md`.

## Image publication

`.github/workflows/publish.yml` builds and pushes version-tagged + `latest` images to GHCR after
merge to `main` or a `v*` tag:

```
ghcr.io/<owner>/<repo>/api:latest
ghcr.io/<owner>/<repo>/web:latest
```

It uses the scoped `GITHUB_TOKEN` (`packages: write`) and does **not** deploy to any host. Pull and
run with:

```bash
docker pull ghcr.io/<owner>/<repo>/api:<tag>
docker run --rm -p 8000:8000 -v analog-data:/data ghcr.io/<owner>/<repo>/api:<tag>
```

The web image expects an API reachable at the Compose service name `api`; for a custom host, adapt
`apps/web/nginx.conf` in a deployment overlay.

## Production checklist (not yet performed)

- [ ] Choose a hosting target (not specified by this repository) and document it.
- [ ] Move SQLite to a managed PostgreSQL/PostGIS instance and add Alembic migrations.
- [ ] Put TLS/reverse proxy in front of web and API; restrict CORS to the real origin.
- [ ] Add monitoring/alerting, log retention, and backups (`/data/analog.db` or PostGIS dump).
- [ ] Pin and scan container images; enable Dependabot/security updates.
- [ ] Verify GHCR image pull and smoke against the deployed environment.
- [ ] Confirm branch protection and required checks are active (see `docs/GITHUB_SETUP.md`).

Do not advertise a production deployment until the checklist is verified.

## Rollback and smoke

- Image rollback: redeploy the previous version tag; the SQLite schema is additive for the MVP,
  but take a backup before a release that changes schema.
- Backup: `docker run --rm -v analog-data:/data -v "$PWD:/backup" alpine cp /data/analog.db /backup/`
- Restore: stop the API, copy the database back into the volume with UID 10001 ownership, restart,
  then `curl -fsS http://localhost:8000/api/v1/health` and run a sample search.
- Local verification: `bash scripts/verify.sh`, `bash scripts/smoke.sh`.
- Online provider verification (network): `bash scripts/online_smoke.sh`.

## Secrets policy

No API keys are needed for the MVP. GitHub Actions uses only `GITHUB_TOKEN`. Never commit `.env`,
credentials, or private tokens. Source downloads use unsigned public endpoints.
