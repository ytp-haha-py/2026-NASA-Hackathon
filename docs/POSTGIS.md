# Optional PostGIS candidate repository

Status: **adapter implemented; offline contract tests run by default; the live
PostgreSQL/PostGIS contract was executed locally on 2026-11-15 with the Docker image and
commands below.** SQLite remains the default runtime store. There is still **no hosted
PostGIS service, no production deployment, and no geometry column** — a local container run
is verification evidence, not deployment evidence.

Code: `apps/api/src/analog_api/adapters/postgis.py`
Tests: `apps/api/tests/test_postgis_adapter.py`
Regional-scan coverage: `apps/api/tests/test_global_api.py`,
`pipelines/tests/test_real_coarse_pilot.py`

## What it does

`PostgisCandidateRepository` implements the existing `CandidateRepositoryFull` Protocol:

- `list_candidates`, `get_candidate`, `candidate_count`, `data_version`
- `list_datasets`, `get_dataset`
- `list_references`, `get_reference`

It adds a bounded regional scan:

```python
from analog_api.adapters.postgis import connect_postgis
from analog_api.domain.entities import BBox

with connect_postgis("postgresql+psycopg://user@host/db") as repo:
    scan = repo.scan_candidates(
        BBox(west=8.0, south=45.0, east=10.0, north=47.0),
        limit=100,
    )
    # scan.scope == "regional_bbox_subset_not_global"
    # scan.global_coverage is False
```

`connect_postgis` checks only that the connection answers `PostGIS_Version()`; it does not
create schemas, seed data, or claim a deployment. Install the optional driver with:

```bash
uv sync --extra postgis   # psycopg[binary]
```

## Query shape

Candidate footprints are currently four WGS84 columns (`west`, `south`, `east`, `north`), not a
geometry column. The PostgreSQL predicate builds an envelope from those columns:

```sql
ST_MakeEnvelope(west, south, east, north, 4326)
  && ST_MakeEnvelope(:west, :south, :east, :north, 4326)
AND ST_Intersects(
      ST_MakeEnvelope(west, south, east, north, 4326),
      ST_MakeEnvelope(:west, :south, :east, :north, 4326)
    )
-- half-open boundary contract (see below)
AND west < :east AND east > :west AND south < :north AND north > :south
```

`&&` is the index-friendly bounding-box test; `ST_Intersects` is the exact geometric predicate.
For a large table, add the expression index (not executed automatically):

```sql
CREATE INDEX IF NOT EXISTS candidates_bbox_envelope_gix
ON candidates
USING gist (ST_MakeEnvelope(west, south, east, north, 4326));
```

### Half-open boundary fix (found by the live test)

PostGIS counts a **shared edge as intersecting**, so the original `&&` + `ST_Intersects`
predicate returned a footprint that merely touches the query box. The SQLite subset implements
the documented half-open semantics, so the two backends disagreed on the first live run. The
explicit `west/east/south/north` comparisons above restore one contract on both backends; the
PostGIS SQL now carries `ST_*` **and** the scalar half-open checks, and both are asserted.

## Bounded regional scan

- Bbox is validated (`-180 <= west < east <= 180`, `-90 <= south < north <= 90`).
- The request is capped at 25 square degrees by default (`max_scan_area_deg2`).
- `limit` is 1..500; `offset` must be non-negative.
- Ordering is deterministic `(location_id)`.
- At boundary contact the half-open predicate excludes touching footprints (verified live).
- Antimeridian-crossing bboxes are rejected rather than silently split or mis-ordered.
- Results carry `scope="regional_bbox_subset_not_global"` and `global_coverage=False`; they must
  never be presented as a global top-N survey.

The scan is **not wired into `POST /search`** yet. The fine 90 m search still reads the SQLite
candidate table; the coarse/global path is the separate `/api/v1/global/*` DuckDB index. There is
no request field for a regional bbox on `/search`, so no batch/region manifest is attached there.

## Tests and evidence

### Default (offline, CI) run

```bash
uv run pytest apps/api/tests/test_postgis_adapter.py -q
# 8 passed, 2 skipped  (the two live tests skip without ANALOG_TEST_POSTGIS_URL)
```

Offline coverage:

- one `assert_repository_parity` helper exercises **every** Protocol method plus the bounded
  scan on the SQLite subset and on the SQLite baseline, including region/bbox filtering,
  deterministic ordering, full pagination sweeps (no gaps, no repeats, stable `matched_count`),
  boundary exclusion, exact-box match, empty result, limit truncation and validation errors;
- PostgreSQL SQL compilation contains `ST_MakeEnvelope`, `&&`, `ST_Intersects`, the four
  half-open comparisons, deterministic ordering, `LIMIT`/`OFFSET`;
- the SQLite subset emits no `ST_*` functions and raises without `allow_sqlite_subset=True`;
- `connect_postgis` rejects non-PostgreSQL URLs without touching the network.

### Live (opt-in, requires a disposable PostGIS database)

```bash
ANALOG_TEST_POSTGIS_URL="postgresql+psycopg://user@host/disposable_db" \
  uv run pytest apps/api/tests/test_postgis_adapter.py -q -m postgis
# 2 passed in 1.86s
#   contract parity vs SQLite            0.37s
#   expression-index plan + boundary     1.28s
```

The live tests create a throwaway schema, seed it, and drop it (`DROP SCHEMA ... CASCADE`). They
never write to `public` beyond `CREATE EXTENSION IF NOT EXISTS postgis`, and they never print the
connection URL (SQLAlchemy renders passwords as `***`; the rendered URL is only used locally).

## Local live run (reproduction)

Image and port used on 2026-11-15:

| Item | Value |
|---|---|
| Image | `postgis/postgis:16-3.4` |
| Image digest | `postgis/postgis@sha256:44126d872ac91993766c341e369c539e8196614321765d36a6f1bab0419a5fa5` |
| PostgreSQL | 16.4 (Debian 16.4-1.pgdg110+2) |
| PostGIS | 3.4.3 (GEOS 3.9.0, PROJ 7.2.1) |
| Driver | psycopg 3.3.9 (`uv sync --extra postgis`) |
| Host port | `127.0.0.1:55432` → container `5432` (chosen to avoid local 5432) |
| Container name | `analog-postgis-3` |

```bash
docker pull postgis/postgis:16-3.4
docker run -d --name analog-postgis-3 \
  -e POSTGRES_PASSWORD="<local-throwaway>" \
  -e POSTGRES_USER=analog \
  -e POSTGRES_DB=analog_contract \
  -p 127.0.0.1:55432:5432 postgis/postgis:16-3.4
docker exec analog-postgis-3 pg_isready -U analog -d analog_contract
docker exec analog-postgis-3 psql -U analog -d analog_contract -tAc 'select postgis_full_version()'

uv sync --extra postgis
ANALOG_TEST_POSTGIS_URL="postgresql+psycopg://analog:<local-throwaway>@127.0.0.1:55432/analog_contract" \
  uv run pytest apps/api/tests/test_postgis_adapter.py -q -m postgis

docker rm -f analog-postgis-3
```

Use a local throwaway password only; do not commit it or reuse a real credential. Rootless
Docker/Podman work the same way. The tests must be pointed at a **disposable** database because
they create and drop schemas.

### Observed results

| Check | Observed |
|---|---|
| Contract fixture rows | 4 candidates + 1 dataset + 1 planetary reference, seeded identically into SQLite and PostGIS |
| Protocol parity | identical `list_*`/`get_*` entities, `candidate_count()=4`, `data_version()="dv-test-fixture"` on both backends |
| Scan parity | identical ids and `matched_count` for overlapping, empty, boundary-touching and exact-match boxes; identical pagination reassembly |
| `repository_for_session` | returns the PostGIS adapter on PostgreSQL, `SqlAlchemyRepository` on SQLite |
| `connect_postgis` | answers `PostGIS_Version()`, reads the same 4 rows through its own connection |
| Synthetic planner probe | 20,000 TEST-ONLY rows; `EXPLAIN` chooses `Bitmap Index Scan on candidates_bbox_envelope_gix` after the documented DDL, `Index Scan using candidates_pkey` (cost 256,647) before it |

Timings on the synthetic 20,000-row grid (same machine, synthetic fixture — **not** a production
benchmark, and not real terrain observations):

| Path | `scan_candidates` median / max |
|---|---|
| PostgreSQL 16.4 + PostGIS (GiST expression index) | 28.0 ms / 50.6 ms |
| PostgreSQL, select only / count only | 20.8 ms / 5.7 ms |
| SQLite subset, in-memory (brute-force predicate) | 13.8 ms / 41.1 ms |

At 20k rows the in-memory SQLite brute force is faster than a client/server round trip; the GiST
index is what changes the scaling behaviour (the pre-index plan filters 20,000 rows, the indexed
plan visits ~400).

## SQLite subset caveat

`allow_sqlite_subset=True` executes a plain numeric bbox predicate with the same half-open
semantics. It is only a contract-test and local-development fallback. It is not a spatial index,
does not exercise PostGIS, and must not be cited as PostGIS verification.

Two SQLite-specific pitfalls observed while building the live test:

- SQLite does not enforce foreign keys unless `PRAGMA foreign_keys=ON`, so child-first inserts
  passed there and failed on PostgreSQL. Test fixtures now flush parents explicitly.
- `str(sqlalchemy_url)` masks the password as `***`; passing it to a driver fails authentication.
  Use `url.render_as_string(hide_password=False)` locally and never log it.

## Cost, egress and storage estimate (estimates, not measurements)

Measured artifact sizes (bytes on disk, this repository):

| Artifact | Bytes |
|---|---|
| Committed 168-cell Parquet partitions (3 files) | 26,173 (8,151 + 8,767 + 9,255) |
| Committed index directory incl. manifest (`du -sb`) | 29,519 |
| Batch pilot Parquet, 11 tasks | 70,030 |
| Batch pilot JSON sidecars, 11 tasks | 25,028 |
| Snapshot manifest | 3,346 |

Derived density: **≈156 bytes per coarse 5 km cell** in Parquet (26,173 / 168). A resumable
batch writes a few hundred extra bytes per cell in sidecars (25,028 / 168 ≈ 149 B/cell).

Measured PostgreSQL storage (TEST-ONLY synthetic rows, real ORM schema, GiST expression index on
the bbox expression, `ANALYZE` run):

| 100,000 rows | Total | Heap | GiST | Primary key |
|---|---|---|---|---|
| Bytes | 31,490,048 | 24,829,952 | 4,366,336 | 2,260,992 |
| Per row | 314.9 | 248.3 | 43.7 | 22.6 |

Scale illustration (assumptions stated, **not** observed coverage):

| Assumption | Parquet storage | PostgreSQL storage |
|---|---|---|
| 10,000 coarse cells | ≈1.6 MB | ≈3.1 MB |
| 1,000,000 coarse cells | ≈156 MB | ≈315 MB |

Egress to the Geomorpho90m 250 m COG is the dominant cost. The archived object is
5,022,223,729 bytes, 172800 × 71698, stored as **1-row deflate strips**, so a window read pulls
whole rows even when only a few columns are needed:

- ≈70,046 bytes per row strip (5,022,223,729 / 71,698);
- a padded pilot window (240 × 240 px ≈ 240 strips) therefore moves **≈16.8 MB** of transfer for
  **115,200 bytes** of int16 pixel payload — ≈146× amplification;
- three pilot windows ⇒ **≈50 MB**; a 1° × 1° regional tile (480 rows) ⇒ **≈33.6 MB**.

These egress figures are **derived from the documented block layout**, not measured transfers. A
live re-measurement on 2026-11-15 did not complete (a 1 KiB range probe returned HTTP 206, but a
16 KiB range read timed out after 30 s against `hs.pangaea.de`), so no observed byte counter is
reported. Any future regional scan should either stage once and reuse, or publish a tiled/COG
with internal tiling so range reads stop pulling full rows.

Cost: **no paid service, credential, or invoice is involved** in anything documented here — the
PostGIS container is local, and the pilot used bounded public range reads. The following is
list-price arithmetic only, for planning: at USD 0.09/GB egress, one 16.8 MB window ≈ USD 0.0015
and a 1° tile ≈ USD 0.003; Parquet storage of 10k cells (1.6 MB) at USD 0.015/GB-month is
negligible (< USD 0.001/month). These are illustrations of scale, not quotations.

## Licensing review

Source facts are recorded in `catalog/datasets.yaml`, `catalog/geomorpho250-sources.yaml`,
`docs/DATASETS.md`, and `docs/GEOMORPHO250_SOURCE.md`; this is a summary, not legal advice.

| Source | Role | Licence | Obligations for this project |
|---|---|---|---|
| Copernicus DEM GLO-90 (`COP-DEM_GLO-90-DGED`, 2021) | 90 m terrain metrics | "Free basis for the general public" via the AWS Open Data mirror, under the Copernicus Contributing Mission Data Access Licence terms | Keep the required attribution "Produced using Copernicus WorldDEM-90 © DLR e.V. 2010–2014 and © Airbus Defence and Space GmbH 2014–2018, provided under COPERNICUS by the EU and ESA; all rights reserved"; publish derived values and provenance, not raw tiles |
| Geomorpho90m 250 m slope (PANGAEA 899135) | coarse `coarse-terrain-250m-1.0.0` | CC BY 4.0 | Attribute Amatulli et al. (2019/2020), Geomorpho90m, PANGAEA 899135 and state CC BY 4.0; the committed coarse rows carry `cc_by_4_0` in `qa_flags` |
| ESA WorldCover 10 m 2021 v200 | **context only** land cover | CC BY 4.0 | Attribute "© ESA WorldCover project 2021 / Contains modified Copernicus Sentinel data (2021) processed by ESA WorldCover consortium"; never enters the similarity distance and is never presented as mineralogy/geology |

Repository rules that follow from the above: no raw raster or COG is committed, only derived
numeric values plus checksums; every coarse row keeps `dataset_id`, `source_asset_id`,
`method_version` and QA flags; Antarctica is excluded from the Geomorpho coverage and is never
described as covered.

## Remaining external blockers for Issue #3

- No hosted PostgreSQL/PostGIS service, credentials, or deployment exists. Verification is a
  local disposable container only; nothing is migrated or operated.
- No geometry column or geometry-native GiST index: the adapter uses an expression index over
  four WGS84 columns. A real spatial migration (add/backfill `geometry(Polygon, 4326)`, compare
  plans and counts) is still open.
- The bounded regional scan is not wired into `POST /search`; no request field, no batch
  manifest attachment, and no UI coverage label exists for the fine-search path.
- Per-region row counts and per-batch manifest linkage are not exposed by the API/search
  contract (regions, total rows, bbox, label, snapshot and method versions are). Changing the
  response shape would require regenerating `contracts/openapi.json` **and**
  `apps/web/src/api/schema.d.ts`, which this workstream is not allowed to edit.
- The UI renders region scope, coverage label, snapshot/method/index versions and the
  indexed-vs-on-demand origin split (`apps/web/src/components/GlobalPanel.tsx`,
  `ResultsPanel.tsx`), but no UI test asserts the region list or the origin split directly; that
  assertion belongs to the frontend owner.
- No live egress measurement for the source COG (see above); the estimate is derived from the
  documented strip layout.
- No global coverage: three 0.4° pilot windows, 168 cells. Never describe this pilot as global,
  exhaustive, or as a global top-10.

Do not describe the adapter as deployed or the regional scan as global coverage.
