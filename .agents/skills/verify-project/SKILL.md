---
name: verify-project
description: Run the Planetary Analog Explorer P0 quality gate and interpret its failures. Use after code, schema, data-manifest, CI, or frontend changes, before reporting work complete, before opening or updating a pull request, and when reproducing a local CI failure.
---

# Verify the project

The executable source of truth is `scripts/verify.sh`; this skill explains how to run and
interpret it. Do not copy the gate into ad-hoc shell pipelines, and do not weaken it to make a
change pass.

## When to run it

Run the gate before claiming a task is complete, before asking for review, and whenever a change
touches API schemas, domain scoring/features, manifests, persistence, frontend types, or CI.
`scripts/verify.sh` mirrors the checks CI runs in the `backend-tests` and `frontend-checks` jobs.

## Run the gate

From the repository root:

```bash
bash scripts/verify.sh
```

Useful modes:

```bash
bash scripts/verify.sh --backend-only   # backend + contracts only; state why the frontend was skipped
bash scripts/verify.sh --ci             # same checks, CI-friendly output
```

The full gate requires `uv` and Node.js 22+ with npm, plus `apps/web/package.json`,
`apps/web/package-lock.json`, and the `typecheck`, `lint`, `test`, `build`, and `check:api` npm
scripts. Use `--backend-only` only when the task is backend-scoped or the frontend has not landed
yet; never present a `--backend-only` result as a full-gate pass.

`scripts/verify.sh` performs, in order: `uv sync --frozen`, Ruff lint, Ruff format check, pytest
across the API suite and every pipeline suite present (`pipelines/tests`, `pipelines/earth/tests`,
and `pipelines/planetary/tests` when it exists), an OpenAPI snapshot drift check via
`scripts/export_openapi.py` (temporary restore, no dirty
tree), and the frontend install, typecheck, lint, test, build, and generated-type checks. It never
downloads source data and never starts containers.

## Interpret failures

- **Tool missing / Node older than 22**: install the tool; do not bypass the failing step.
- **`uv sync --frozen`**: `uv.lock` and `pyproject.toml` disagree or the environment is stale. Fix
  the lock through the owning workflow; do not hand-edit `uv.lock`.
- **Ruff**: fix the code or formatting. `uv run ruff format .` may rewrite files; review the diff.
- **pytest**: read the first failing test and reproduce it alone (below). A missing required test
  directory (`apps/api/tests` or `pipelines/tests`) is a gate failure, not a skip. Optional pipeline
  suites (`pipelines/earth/tests`, `pipelines/planetary/tests`) are included only when present.
- **OpenAPI snapshot**: `contracts/openapi.json` is stale or hand-edited. Regenerate it with
  `uv run python scripts/export_openapi.py`, inspect the diff, and commit it with the schema change.
- **Frontend prerequisites / `npm ci`**: a required script or lockfile is absent, or the lockfile
  is out of sync. The owning frontend agent must fix it; do not delete the lockfile or replace
  `npm ci` with `npm install` in the gate.
- **Typecheck / build**: generated API types are stale or the frontend does not compile. Run
  `npm run generate:api` only if that is the documented frontend workflow, then `npm run check:api`.
- **`npm run check:api`**: regenerate the frontend API types from `contracts/openapi.json` and commit
  them; do not edit generated files by hand.
- **`scripts/smoke.sh`**: the API is broken end-to-end, or port 8123 is occupied. The smoke uses an
  isolated temporary SQLite database and the TEST-ONLY synthetic fixture; it never touches `./var`
  and needs no network data.

If a failure is in code outside your writable ownership, stop and report it to the owning agent.
Do not disable a check, add an ignore, or edit a test to force green.

## Rerun focused checks

Fast iteration is fine, but the final claim still requires the full gate.

```bash
uv run pytest apps/api/tests -q
uv run pytest pipelines/tests -q
uv run pytest pipelines/earth/tests -q
uv run pytest pipelines/planetary/tests -q   # once the planetary pipeline lands
uv run pytest apps/api/tests/test_scoring.py -q
uv run pytest apps/api/tests pipelines/tests pipelines/earth/tests -q -k search
uv run ruff check .
uv run ruff format --check .
bash scripts/smoke.sh
cd apps/web && npm run test -- --run
cd apps/web && npm run typecheck
```

Read `docs/TESTING.md` for what each layer proves and how to run real-data checks separately.
Real raster downloads and network-marked tests are not part of this gate or CI.

## Report observed evidence

Report the exact command, the mode used, the exit status, the PASS/FAIL line, and the relevant
failure text (for example the first failing test or the OpenAPI diff). For smoke runs, include the
observed HTTP statuses and key values printed by the script. State clearly when a check was skipped
and why.

Never claim that tests, the gate, or CI passed without command output in front of you. A local pass
does not prove that a GitHub Actions run is green; say that CI status was not observed unless you
actually saw the run.
