# Contributing

AnalogX is an open-source planetary data and Earth-analog exploration tool. Changes follow
`Issue -> branch -> Pull Request -> review -> required CI -> maintainer merge`.

## Setup

```bash
uv sync
cd apps/web && npm ci
cd ..
make test-backend
make test-frontend
make data-validate
```

Python 3.13 is pinned in `.python-version`; `uv` owns `uv.lock`. The frontend uses npm and
`apps/web/package-lock.json`. Do not mix package managers.

## Scope and style

- MVP scope and explicit non-goals: `docs/DECISIONS/0001-mvp-scope.md`, `docs/ROADMAP.md`.
- Python: Ruff (`line-length = 100`), typed domain boundaries, small modules. Run
  `uv run ruff check .` and `uv run ruff format --check .`.
- TypeScript: strict mode, generated API types from `contracts/openapi.json`, tests for user
  states. Run typecheck, lint, tests, and build from `apps/web`.
- Prefer extending the existing feature registry and repository interfaces over adding parallel
  algorithms or duplicate endpoints.

## Branching and pull requests

- Branch from up-to-date `main`: `feat/<issue-number>-short-name` or `fix/<issue-number>-short-name`.
- One logical change per PR; include tests and docs in the same PR.
- Use the PR template. Link the Issue with `Closes #...`.
- Required CI jobs: `backend-tests`, `frontend-checks`, `integration-smoke`.
- A PR needs at least one approving review from someone other than the author, resolved
  conversations, and green required checks. Authors do not merge their own unreviewed work.
- Do not force-push shared branches or push to `main`.

## Scientific data and provenance rules

- No invented measurements, placeholders presented as observations, or "global top 10" claims.
- Real observations must be reproducible from a documented official source and must include
  provider, product ID, URL, access timestamp, license, native CRS/resolution, processing version,
  checksum (source or crop), and processor method version.
- Every candidate metric must use the shared extraction method in
  `pipelines/src/analog_pipelines/features.py` (`terrain-metrics-1.0.0`). Do not mix method versions
  in one comparison.
- Synthetic fixtures are allowed only under test/demo paths, must set `data_kind=synthetic`, and
  must be labelled as DEMO / SYNTHETIC in the API and UI.
- Do not compare absolute planetary altitude with Earth sea-level altitude. Compare local
  terrain-shape metrics in a metric CRS.
- A DEM is not a mineralogical or environmental map. Disclose gravity/atmosphere/radiation and
  other non-simulated conditions separately from dataset-derived facts.
- Source licensing must permit redistribution of the committed derived values. Raw DEM tiles are
  not committed.
- On-demand AOI requests are bounded and use allowlisted providers; never accept arbitrary URLs or
  add a provider without a verified `catalog/datasets.yaml` entry and manifest.
- ESA WorldCover fractions are Earth land-cover context only. Never label them mineralogy/geology
  or add them to the weighted distance.
- Snapshot changes require rebuilding `samples/snapshot/analog-demo.snapshot.json`, recording the
  new SHA-256 values, and verifying `make data-validate` + `make data-index-dry-run`.
- Provider failures must be explicit (`unavailable`, `out_of_coverage`, `failed`) with a reason;
  unknown data is never zero-filled.

## Proposing a new data provider or feature dimension

1. Open an Issue using the feature template with source URL, license, CRS, resolution, units,
   valid domain, method, validation plan, and expected limitations.
2. Add the feature to the central registry and contract with a documented fixed scale; add a
   contract test.
3. Implement extraction in the shared pipeline with synthetic-raster tests (known slope, relief,
   nodata, CRS errors).
4. Update `GET /api/v1/capabilities`, search validation, the frontend form (driven by the API), and
   docs (`docs/DATA_SOURCES.md`, `docs/SEARCH_METHOD.md`).
5. Bump the contract/method version if the change alters comparability; reject cross-version
   comparisons explicitly.

## Review and merge rules

- Reviewers check scientific honesty, coverage labels, provenance completeness, API compatibility,
  and test evidence before style.
- CI must pass; coverage claims require observed output.
- Maintainers merge with squash merge after approval. Never bypass rules or auto-merge.

## Conduct

Be precise, cite evidence, and critique ideas rather than people. Do not pressure reviewers to
approve unverified scientific claims. Harassment or fabrication is not acceptable.
