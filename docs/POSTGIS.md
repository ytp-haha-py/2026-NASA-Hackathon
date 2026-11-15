# Optional PostGIS candidate repository

Status: **adapter code and offline SQL/contract tests are implemented. No PostgreSQL/PostGIS
service was provisioned or verified for this repository.** SQLite remains the default runtime
store. A skipped live test is not deployment evidence.

Code: `apps/api/src/analog_api/adapters/postgis.py`
Tests: `apps/api/tests/test_postgis_adapter.py`

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
uv sync --extra postgis
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
```

`&&` is the index-friendly bounding-box test; `ST_Intersects` is the exact predicate. For a large
table, add the expression index (not executed automatically):

```sql
CREATE INDEX IF NOT EXISTS candidates_bbox_envelope_gix
ON candidates
USING gist (ST_MakeEnvelope(west, south, east, north, 4326));
```

This is not a substitute for a real geometry column. A future migration should add and backfill
`geometry(Polygon, 4326)` plus a GiST index, then compare plans and counts.

## Bounded regional scan

- Bbox is validated (`-180 <= west < east <= 180`, `-90 <= south < north <= 90`).
- The request is capped at 25 square degrees by default (`max_scan_area_deg2`).
- `limit` is 1..500; `offset` must be non-negative.
- Ordering is deterministic `(location_id)`.
- At boundary contact the half-open predicate excludes touching footprints.
- Antimeridian-crossing bboxes are rejected rather than silently split or mis-ordered.
- Results carry `scope="regional_bbox_subset_not_global"` and `global_coverage=False`; they must
  never be presented as a global top-N survey.

The scan is **not wired into `POST /search`** yet. There is no request field for a regional bbox,
no batch manifest, and no UI coverage label for this path. Those remain part of Issue #3.

## Tests and evidence

Default run (offline):

```bash
uv run pytest apps/api/tests/test_postgis_adapter.py -q
```

It verifies:

- identical entities from `SqlAlchemyRepository` and the adapter on the same SQLite fixture;
- deterministic bbox filtering, pagination, boundary exclusion, and size/limit validation;
- PostgreSQL SQL compilation contains `ST_MakeEnvelope`, `&&`, `ST_Intersects`, ordering and
  limits;
- the SQLite subset never emits `ST_*` functions;
- a non-PostgreSQL session requires an explicit `allow_sqlite_subset=True` test flag;
- `connect_postgis` rejects non-PostgreSQL URLs without touching the network.

Live contract test (opt-in, creates and drops its own schema):

```bash
ANALOG_TEST_POSTGIS_URL="postgresql+psycopg://user@host/disposable_db" \
  uv run pytest apps/api/tests/test_postgis_adapter.py -q -m postgis
```

If `ANALOG_TEST_POSTGIS_URL` is unset, the test is skipped. This repository has not run it and has
no result to report.

## SQLite subset caveat

`allow_sqlite_subset=True` executes a plain numeric bbox predicate with the same half-open
semantics. It is only a contract-test and local-development fallback. It is not a spatial index,
does not exercise PostGIS, and must not be cited as PostGIS verification.

## Remaining external blockers for Issue #3

- No PostgreSQL/PostGIS service, credentials, or deployment exists in this environment.
- No geometry column or spatial index; only the expression-index recommendation above.
- No resumable regional batch scan that writes per-batch manifests. The existing
  `batch_runner`, `coarse_grid`, and snapshot code can be composed for that, but the candidate
  scan is not connected to it yet.
- No API/UI exposure of batch/region coverage for this path.
- No cost/egress/storage measurement for a real regional scan.

Do not describe the adapter as deployed or the regional scan as global coverage.
