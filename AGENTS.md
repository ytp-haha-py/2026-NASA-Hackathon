# AGENTS.md

Short operating guide for coding agents working in this repository.

## Source of truth

- Feature definitions, units, scales, and analysis constants: `apps/api/src/analog_api/domain/features.py`.
- API request/response schema: `apps/api/src/analog_api/schemas.py`; committed OpenAPI: `contracts/openapi.json`.
- Ranking algorithm: `apps/api/src/analog_api/domain/scoring.py` (pure, storage-independent).
- Raster extraction: `pipelines/src/analog_pipelines/features.py`.
- Persistence + seeding: `apps/api/src/analog_api/adapters/`.
- Pipeline handoff format: `contracts/pipeline-manifest.schema.json`; concept description in `contracts/contract.json`.
- On-demand AOI validation/jobs: `apps/api/src/analog_api/domain/aoi.py`, `adapters/jobs.py`, `adapters/earth_aoi.py`.
- Land-cover context is context-only: `CONTEXT_FEATURES` in the registry; never add it to `scoring.py`.
- Snapshot/index rebuild: `pipelines/src/analog_pipelines/snapshot.py`, `pipelines/build_index.py`.
- Data promotion, trusted publish, and rollback: `docs/DATA_GOVERNANCE.md`; offline gate `bash scripts/validate_data.sh`.

Never invent scientific data, never turn synthetic fixtures into "observed" records, and never
present subset-based results as a global or exhaustive survey. If a data source is unavailable,
mark it unverified/failed with per-provider status; never zero-fill. ESA WorldCover is Earth
land-cover context only, not mineralogy/geology, and never enters the similarity distance. Moon
coordinates use a planetary frame, never Earth WGS84. Raw rasters/caches are not committed; use the
pinned snapshot for reproducibility.

## Commands

```bash
uv sync                    # backend + pipelines
cd apps/web && npm ci      # frontend
make api                   # http://127.0.0.1:8000
make web                   # http://localhost:5173
make seed                  # real manifests -> ./var/analog.db
make test-backend
make test-frontend
make verify                # full gate (delegates to scripts/verify.sh)
make smoke                 # offline API integration smoke
make smoke-online          # real AOI provider smoke (network; never CI)
make data-demo             # offline demo index from the pinned snapshot
make data-validate         # snapshot/manifest checksums
make data-rebuild-index    # verify snapshot + rebuild ./var/analog.db
make data-analyze-aoi WEST=-6.617 SOUTH=37.682 EAST=-6.600 NORTH=37.699
```

Focused checks: `uv run pytest apps/api/tests/test_scoring.py -q`,
`uv run pytest pipelines/tests -q`, `cd apps/web && npm run test -- --run`.
Run `uv run ruff check .` and `uv run ruff format --check .` before pushing.
After changing API schemas, regenerate the contract: `uv run python scripts/export_openapi.py`.

## Cross-agent verification skill

The canonical skill is `.agents/skills/verify-project/SKILL.md`. It delegates all enforcement to
`scripts/verify.sh`. OpenCode and Codex discover it under `.agents/skills/`; Claude Code reaches
the same files through `.claude/skills/verify-project` (relative symlink). Do not create a second
copy of the instructions.

Read the skill, run `bash scripts/verify.sh`, and report observed evidence. Never claim tests pass
without command output.

## Workflow

`Issue -> feat/<issue>-short-name or fix/<issue>-short-name -> PR -> review -> required CI -> merge`

- Do not push to `main`; the initial repository bootstrap was a one-time exception authorized by
  the maintainer. All later changes use a feature branch and PR.
- Do not auto-merge or self-approve.
- Keep scientific provenance requirements in `CONTRIBUTING.md`.
- Do not add paid services, credentials, large raster binaries, or global downloads.
