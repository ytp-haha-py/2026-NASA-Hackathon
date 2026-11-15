# Testing and verification

This document explains the test layers, the exact commands, the difference between synthetic and
real data, and how CI and the shared verification skill use them.

The executable source of truth is `scripts/verify.sh`. Do not copy its steps into another script
and do not weaken a check to make a change pass.

## Prerequisites

- Python 3.13 is installed and managed by `uv` (`uv sync` installs the dev group by default).
- Node.js 22 or newer with npm for the full gate (CI pins Node 22 LTS).
- `bash`, `curl`, and a POSIX shell environment for `scripts/smoke.sh`.
- No source-data download, network dataset, credential, or running container is required for any
  check described here.

## The P0 quality gate

```bash
bash scripts/verify.sh                 # full gate: backend, contracts, frontend
bash scripts/verify.sh --backend-only  # backend and contracts only
bash scripts/verify.sh --ci            # same checks, CI-friendly output
```

The full gate runs, in order:

1. Tool availability checks with actionable errors.
2. `uv sync --frozen`.
3. `uv run ruff check .`.
4. `uv run ruff format --check .`.
5. Presence check for the required backend suites; optional pipeline suites are included when their
   directories exist.
6. `uv run pytest` across the API suite and every pipeline suite that exists at invocation time:
   `apps/api/tests`, `pipelines/tests`, `pipelines/earth/tests`, `pipelines/worldcover/tests`, and
   `pipelines/planetary/tests` (optional directories are included when present).
7. OpenAPI drift check: copy `contracts/openapi.json` to a temporary directory, run
   `uv run python scripts/export_openapi.py`, diff generated against committed, restore the
   committed snapshot in all cases. A stale snapshot fails the gate without leaving a dirty tree.
8. Offline data gate: `bash scripts/validate_data.sh` verifies the pinned snapshot, manifest JSON
   Schema, and a no-write index rebuild.
9. If `apps/web/package.json` exists: verify the lockfile and required npm scripts, then
   `npm ci`, `npm run typecheck`, `npm run lint`, `npm run test -- --run`, `npm run build`, and
   `npm run check:api`.

`--backend-only` is the only supported way to skip the frontend. It is appropriate for
backend-scoped work or while `apps/web` is still being delivered; it is not a full-gate pass. If
the frontend package, lockfile, or a required npm script is absent, the full gate fails with a
clear message. Absence is never treated as success.

The gate does not download DEM tiles, start Docker, or write to `./var`. The OpenAPI check restores
the committed snapshot before exiting, so the working tree is not modified by the gate.

## Exact commands

Backend and contracts:

```bash
uv sync --frozen
uv run ruff check .
uv run ruff format --check .
uv run python scripts/export_openapi.py   # intentional regeneration only; commit the result
```

To run the backend suites manually exactly as the gate does (all existing directories, so the
command works before `pipelines/planetary/tests` exists):

```bash
pytest_dirs=(apps/api/tests pipelines/tests pipelines/earth/tests pipelines/planetary/tests)
existing_dirs=()
for directory in "${pytest_dirs[@]}"; do
  [ -d "$directory" ] && existing_dirs+=("$directory")
done
uv run pytest "${existing_dirs[@]}" -q
```

Focused reruns while developing:

```bash
uv run pytest apps/api/tests -q
uv run pytest pipelines/tests -q
uv run pytest pipelines/earth/tests -q
uv run pytest pipelines/planetary/tests -q   # once the planetary pipeline lands
uv run pytest apps/api/tests pipelines/tests pipelines/earth/tests -q -k search
uv run ruff check apps/api/src/analog_api/domain/scoring.py
```

Frontend:

```bash
cd apps/web
npm ci
npm run typecheck
npm run lint
npm run test -- --run
npm run build
npm run check:api
```

`npm run check:api` proves that generated frontend API types match `contracts/openapi.json`. When a
schema change is intentional, regenerate the contract first (`uv run python scripts/export_openapi.py`),
then regenerate the frontend types with `cd apps/web && npm run generate:api`, and commit both
results.

Headless integration smoke:

```bash
bash scripts/smoke.sh
ANALOG_SMOKE_PORT=8124 bash scripts/smoke.sh   # only if 8123 is occupied
```

The `Makefile` delegates to the same scripts: `make verify` runs `bash scripts/verify.sh` and
`make smoke` runs `bash scripts/smoke.sh`. `make test`, `make test-backend`, `make test-frontend`,
and `make lint` are convenience wrappers for the focused commands above.

The smoke creates a temporary SQLite database outside the repository, forces
`ANALOG_SEED_INCLUDE_SYNTHETIC=true`, points both manifest directories at an empty temporary
directory, seeds only the synthetic fixture, starts the real app factory with uvicorn on
`127.0.0.1:8123`, and exercises the HTTP API. It checks `/api/v1/health`, `/api/v1/capabilities`,
`POST /api/v1/search`, `GET /api/v1/locations/{id}`, `GET /api/v1/locations/{id}/context`, and a
404 error shape. It prints observed values, then stops the server and removes the database. It
never touches `./var` and needs no network data.

## What each layer proves

| Layer | Command | Proves | Does not prove |
| --- | --- | --- | --- |
| Pipeline tests | `uv run pytest pipelines/tests pipelines/earth/tests -q` (add `pipelines/planetary/tests` when present) | Raster feature extraction math, units, valid-mask handling, method version behavior, and committed-manifest contract checks on deterministic inputs. | That real DEM tiles are available or that the values describe a specific real location. |
| API/domain tests | `uv run pytest apps/api/tests -q` | Scoring, validation, error shapes, provenance exposure, synthetic-labelling rules, and contract invariants. | Scientific equivalence between Earth and Moon/Mars terrain. |
| OpenAPI drift | `bash scripts/verify.sh` (step 7) | `contracts/openapi.json` equals the schema generated by the current app. | That the frontend has regenerated its types; `check:api` covers that. |
| Frontend static/build | `npm run typecheck`, `npm run lint`, `npm run build` | The web app compiles, passes lint, and produces a build artifact. | Runtime behavior against a live backend. |
| Frontend tests | `npm run test -- --run` | Component and formatting logic under the frontend test runner. | End-to-end browser coverage unless the frontend suite explicitly adds it. |
| Frontend API types | `npm run check:api` | Generated types match the committed OpenAPI snapshot. | Request correctness at runtime. |
| Integration smoke | `bash scripts/smoke.sh` | The app factory, isolated seeding, SQLite adapter, routing, response schemas, synthetic disclosure, and error handling work over real HTTP. | Real Copernicus/LOLA data, UI behavior, Docker images, or production configuration. |
| CI | `.github/workflows/ci.yml` | The above run in a clean Linux environment with pinned runtimes. | That a local pass means the GitHub Actions run is green; observe the run explicitly. |

No test in this repository proves that a result is a physically valid analog. Terrain-shape
similarity is subset-based and is not a global survey or a base-site recommendation.

## Fixtures and data classification

### Synthetic: TEST-ONLY

`apps/api/src/analog_api/data/synthetic.py` is the committed deterministic fixture. It is used by
unit tests, frontend development, and the smoke test.

- Every observation has `quality: "synthetic"`.
- The manifest has `data_kind: "synthetic"`, and the dataset has `source_kind: "synthetic"`.
- Candidate display names include `SYNTHETIC`, and quality flags include `synthetic_fixture` and
  `not_a_real_observation`.
- It is seeded only with `--include-synthetic` or `ANALOG_SEED_INCLUDE_SYNTHETIC=true`. The API
  caveats and location limitations label it as demo data.

Never change a synthetic record to `observed`, copy synthetic values into a real manifest, or cite
synthetic values as evidence.

### Real: pipeline-derived manifests

Real candidates are emitted as JSON handoff manifests and are not downloaded by tests or CI.

- Earth: Copernicus DEM-derived manifests in `pipelines/earth/manifests/*.json`, with
  `data_kind: "observed"` and dataset `source_kind: "earth_dem"`.
- Planetary references: NASA LOLA-derived manifests in `pipelines/planetary/manifests/*.json`, with
  dataset `source_kind: "lunar_dem"` and `body: "Moon"` (or the corresponding product for a future
  Mars reference).
- The handoff contract is `contracts/pipeline-manifest.schema.json`; the conceptual/API contract is
  `contracts/contract.json`.

The manifest directories are configuration defaults, not guaranteed contents. If they contain no
manifests, a seed without `--include-synthetic` correctly reports that no manifests were found.
Models must cite the dataset, license, access timestamp, and checksum recorded in the manifest, and
must not invent missing provenance.

Large raster binaries (`*.tif`, `*.tiff`, `*.vrt`) and pipeline caches are ignored by git. Do not
commit DEM tiles or download them in CI.

## CI workflows and job names

`.github/workflows/ci.yml` runs on pull requests targeting `main`, pushes to any branch (including
`main` after bootstrap), and `workflow_dispatch`. It has `permissions: contents: read` and a
concurrency group that cancels superseded runs.

Required check names for branch protection:

- `backend-tests` - Python 3.13, `uv sync --frozen`, Ruff lint/format, and pytest across the API
  suite plus every pipeline suite present (`pipelines/tests`, `pipelines/earth/tests`,
  `pipelines/planetary/tests` when it exists).
- `frontend-checks` - Node.js 22, npm cache keyed on `apps/web/package-lock.json`, `npm ci`,
  typecheck, lint, test, build, and `check:api`.
- `integration-smoke` - Python 3.13, `uv sync --frozen`, `bash scripts/smoke.sh`.

`frontend-checks` requires `apps/web/package.json` and `apps/web/package-lock.json`; it fails with a
clear error until the frontend lands, then becomes green when the delivered app passes. No job uses
custom secrets, deploys anything, or downloads source data. Dependency installation from PyPI/npm
is the only network access.

`.github/workflows/publish.yml` runs on pushes to `main` and tags matching `v*`. It builds and pushes
`ghcr.io/<owner>/<repo>/api` and `ghcr.io/<owner>/<repo>/web` using `GITHUB_TOKEN` with
`contents: read, packages: write`. Tags include the branch/semver version, a short SHA, and
`latest`. It does not deploy. Dockerfiles are owned by the lead at `apps/api/Dockerfile` and
`apps/web/Dockerfile`. The API image builds with the repository root as context because the
Dockerfile copies `pyproject.toml`, `uv.lock`, `apps/api`, and `pipelines` (matching
`compose.yaml`); the web image builds with `context: ./apps/web`.

Do not describe a CI run as green unless you observed the GitHub Actions result.

## Shared verification skill

The canonical cross-agent skill is:

```text
.agents/skills/verify-project/SKILL.md
```

Its path-derived ID is `verify-project`. OpenCode discovers project skills under `.agents/skills/`
and `.claude/skills/`; Codex uses the `.agents/skills/` convention; Claude Code discovers
`.claude/skills/`. The skill describes when to run `scripts/verify.sh`, how to interpret failures,
how to rerun focused checks, and the evidence-reporting rules.

Claude Code reaches the same file through a relative symlink:

```text
.claude/skills/verify-project -> ../../.agents/skills/verify-project
```

Verify it with:

```bash
test -f .claude/skills/verify-project/SKILL.md && echo OK
```

If a platform or checkout cannot support symlinks (for example, some Windows environments), replace
the symlink with a directory copy of `.agents/skills/verify-project` as a fallback. The two copies
must remain identical; the canonical content stays in `.agents/`. Do not create a second hand-edited
copy, and do not add a root `CLAUDE.md` for this purpose.

## v6 AOI, context, and snapshot checks

Offline (part of the gate):

- `apps/api/tests/test_aoi.py` — AOI bbox/polygon validation, oversized/small/SSRF-shaped
  input rejection, injected fake processor, queued→completed job lifecycle, idempotent cache,
  context persistence, and reuse of the same search comparator.
- `pipelines/tests/test_storage.py` / `test_snapshot.py` — local object store traversal safety,
  atomic writes/checksums, deterministic snapshot id, checksum mismatch failure, and identical
  SQLite rebuild counts.
- `pipelines/worldcover/tests/` — class-fraction math, nodata, manifest/context contract.
- `pipelines/planetary/tests/` — real LOLA manifest validation and synthetic known-slope path.
- `pipelines/earth/tests/` — real observed manifest, importer helpers, synthetic GeoTIFF roundtrip.

Offline demo rebuild:

```bash
make data-validate
make data-demo
make data-index-dry-run
```

Rather than trusting a test count, observe the command output and exit status.

### Online provider smoke (opt-in, never CI)

```bash
bash scripts/online_smoke.sh       # or: make smoke-online
```

This starts the real app against an isolated temp SQLite database, seeds the committed real
manifests, submits a **new** bounded AOI near Rio Tinto, polls the job, verifies persisted terrain
features + 11 WorldCover context fractions + Copernicus/WorldCover provider status, then repeats the
request and asserts a cache hit. It requires network access and may take ~10–60 s. It is excluded
from `scripts/verify.sh` and CI. If it fails, the output identifies the failed stage; provider
failures are not converted to fake values.

A no-data AOI must fail explicitly (`source_extraction_failed`, `<80%` valid data) with a job
`failed` status and an actionable error; it must not create a candidate with zero-filled metrics.

## Manual acceptance checklist

Run the automated gate first, then verify the user-visible claims:

- [ ] `bash scripts/verify.sh --backend-only` passes and, once `apps/web` exists,
      `bash scripts/verify.sh` passes.
- [ ] `bash scripts/smoke.sh` passes and prints `RESULT: PASS`.
- [ ] `uv run python -m analog_api.cli seed --include-synthetic` followed by
      `uv run python -m analog_api.cli status` shows the expected candidate/dataset/reference counts.
- [ ] `uv run uvicorn analog_api.main:create_app --factory --port 8000` starts without error.
- [ ] `/api/v1/health` returns `status: "ok"` and a non-empty `data_version`.
- [ ] `/api/v1/capabilities` lists the four feature definitions, fixed scales, method version, and
      an `indexed_subset` coverage label.
- [ ] A search result shows every compared metric, the target, the candidate value, scale, weight,
      distance, dataset IDs, data kind, and caveats.
- [ ] A synthetic result is visibly labelled synthetic in both API responses and UI, and its
      limitations are visible before it can be mistaken for a real observation.
- [ ] The location and context views show provenance (dataset, license, access timestamp), units,
      method version, known limitations, and no generated narrative presented as fact.
- [ ] Unknown location IDs return the documented 404 error shape; invalid requests return 422 with
      field details.
- [ ] If the frontend exists, `npm run dev` renders the API-backed flow without console errors and
      the production `npm run build` artifact serves correctly.
- [ ] No change presents subset results as a global survey, a similarity percentage, or a base-site
      recommendation.

## Real-data integration checks (separate from CI)

Real-data verification is a local/offline-opt-in activity. It is not part of CI and must not be
required to merge ordinary code changes. The `make data-earth` and `make data-moon` targets run the
network ingestion scripts documented in the `Makefile`; they are never invoked by CI, tests, or
`scripts/smoke.sh`.

1. Produce or obtain the Copernicus/LOLA manifests through the pipeline owners. Record dataset,
   license, access timestamp, checksum, and processing version in the manifest.
2. Seed the real manifests without the synthetic flag (the default manifest directories are
   `pipelines/earth/manifests` and `pipelines/planetary/manifests`):

   ```bash
   make seed   # ANALOG_SEED_INCLUDE_SYNTHETIC=false, loads committed real manifests
   uv run python -m analog_api.cli status
   ```

   The seed resets the index by default for reproducibility; use `--keep-existing` only when that
   behavior is intended. `make seed-demo` is the clearly labelled synthetic alternative and writes
   `./var/demo.db`, not the real-data index.

3. Start the API and inspect `/api/v1/capabilities`, `/api/v1/datasets`, `/api/v1/references`, a
   search, and a location/context response. Confirm `data_kind: "observed"`, source kinds
   (`earth_dem`, `lunar_dem`), provenance fields, and non-empty limitations.
4. Run any tests marked `network` explicitly and only when the environment permits network data:
   `uv run pytest -m network`. The marker is declared in `pyproject.toml`; network tests are not
   part of the P0 gate, CI, or the smoke script. The live SoilGrids depth check additionally
   requires `ANALOG_LIVE_NETWORK=1`:
   `ANALOG_LIVE_NETWORK=1 uv run pytest pipelines/tests/test_soilgrids_live.py -q -m network`.
5. Confirm no DEM tiles or caches were added to git. Large raster binaries are gitignored by design.

If real data is unavailable, report the blocker and keep the fixture classification honest rather
than substituting synthetic values.

## Troubleshooting

| Symptom | Cause and action |
| --- | --- |
| `uv sync --frozen` fails | `pyproject.toml` and `uv.lock` disagree. Resolve through the owning dependency workflow; do not hand-edit the lock. |
| `no test_*.py files found` | The required backend suite is absent. Do not skip the gate; the owning agent must deliver tests. |
| OpenAPI drift message | Regenerate and commit `contracts/openapi.json` only for an intended schema change. The script already restored the snapshot. |
| `apps/web/package.json does not exist` | The frontend is not delivered yet. Use `--backend-only` for backend work and state that the full gate was not run. |
| Missing npm script | Add the documented script in the frontend app; do not remove it from the gate. |
| Smoke exits because port 8123 is busy | Stop the other server or use `ANALOG_SMOKE_PORT=<free port>`. |
| Smoke fails an assertion | Treat it as an API integration failure. Read the printed endpoint/file values and the uvicorn log tail; do not edit apps to weaken the assertion. |
