# Global Earth search: bounded bbox queries and the committed refine chain (Issue #132)

This note documents the **existing** committed Earth coarse index, the new bounded
`query_bbox` contract on `CoarseIndexRepository`, how Earth map features should consume
it, and what the committed coarse-to-fine evidence proves. It is intentionally not a
claim of global or exhaustive coverage.

## Three layers, never conflated

| Layer | What it is | What it is not |
| --- | --- | --- |
| Global basemap | Earth imagery/terrain tiles rendered behind the map | Not a candidate index, not a similarity search, not a source of coarse or fine scores |
| Indexed coarse candidates | 168 committed Parquet rows in `pipelines/coarse/real-pilot/index`, each a 5 km window aggregated from ~250 m Geomorpho250 slope cells | Not global coverage and not a fine 90 m observation |
| Fine 90 m candidates | 3 refined 1.98 km focus windows recorded in `pipelines/coarse/real-pilot/refine/refinement.json` | Not indexed in the seedable snapshot by the pilot, and not interchangeable with coarse values |

A map can show all three, but the UI must label them separately. A coarse row is a
shortlist candidate; the 90 m focus window is a different support with a different
method version. No score is merged or rescaled across the boundary.

## Current real coverage

Committed snapshot: `geomorpho250-real-pilot-20261115`

| Field | Value |
| --- | --- |
| Index root | `pipelines/coarse/real-pilot/index` |
| Manifest | `snapshots/geomorpho250-real-pilot-20261115/manifest.json` |
| Manifest sha256 | `f6ac5cc33c10f57d6ea2e5016b7612dfa3702a805cc037c1d44120e16716e301` |
| Row count | 168 |
| Regions | `pilot-alps`, `pilot-grand-canyon`, `pilot-sahara` |
| Bbox (WGS84) | west `-112.20542079733367`, south `23.996959690126204`, east `10.393982962376148`, north `45.36007210182725` |
| Coarse method | `coarse-terrain-250m-1.0.0` |
| Grid version | `coarse-grid-1.0.0` |
| Coverage label | `local_coarse_index_not_global_exhaustive` |

| Region | Committed cells |
| --- | ---: |
| `pilot-alps` | 48 |
| `pilot-grand-canyon` | 56 |
| `pilot-sahara` | 64 |
| **Total** | **168** |

The bbox above is the envelope of those three local pilots. It is not a survey
footprint, not a completeness claim, and must not be rendered as a global index.

## `CoarseIndexRepository.query_bbox` contract

```python
query_bbox(
    *,
    west: float,
    south: float,
    east: float,
    north: float,
    limit: int = 50,
    offset: int = 0,
    region_id: str | None = None,
) -> dict[str, Any]
```

### Validation

- All four coordinates must be finite numbers. NaN and infinity are rejected.
- Longitudes must be within `[-180, 180]`; latitudes within `[-90, 90]`.
- `south > north` is rejected.
- Degenerate `south == north` or `west == east` is rejected with
  `CoarseExtractionError(code="invalid_bbox", ...)`.
- `limit` must be an integer in `1..200` (`invalid_limit`); `offset` must be an integer
  `>= 0` (`invalid_offset`).
- `region_id` is an optional exact match against the committed `region_id` column.

### Antimeridian behaviour

`west > east` means an antimeridian crossing and is evaluated as two longitude
intervals:

```text
[west, 180]  union  [-180, east]
```

It is **not** silently converted to `[-180, 180]`. For example
`west=179, east=-179` matches candidates near both `179..180` and `-180..-179`.
The response echoes `query.antimeridian_crossing` and
`query.longitude_intervals` so callers cannot mistake the crossing for a world query.

### Spatial predicate and ordering

A candidate matches when its committed bbox **overlaps** the query bbox (inclusive
boundaries), not merely when its centre falls inside:

```sql
candidate.west <= query.east
AND candidate.east >= query.west
AND candidate.south <= query.north
AND candidate.north >= query.south
```

The crossing form replaces the longitude test with the two intervals above. The SQL
filter is pushed down over the Parquet `west`, `south`, `east`, `north` columns and
always includes `method_version = 'coarse-terrain-250m-1.0.0'`. The current Parquet
schema has no `row`/`col` columns, so ordering is deterministic by:

```text
region_id, west, south, candidate_id
```

### Return value

```json
{
  "snapshot_id": "geomorpho250-real-pilot-20261115",
  "method_version": "coarse-terrain-250m-1.0.0",
  "coverage_label": "local_coarse_index_not_global_exhaustive",
  "query": {
    "west": 10.0,
    "south": 24.0,
    "east": 11.0,
    "north": 25.0,
    "region_id": null,
    "limit": 50,
    "offset": 0,
    "antimeridian_crossing": false,
    "longitude_intervals": [{"west": 10.0, "east": 11.0}],
    "intersection": "candidate_bbox_overlaps_query_bbox"
  },
  "total": 64,
  "returned": 50,
  "offset": 0,
  "limit": 50,
  "next_offset": 50,
  "rows": ["coarse rows with bbox and parsed qa_flags"]
}
```

- `total` is the SQL `count(*)` for the filter, not `len(rows)`.
- `returned` is the number of rows in this page.
- `next_offset` is `offset + returned` only when another page exists; otherwise `null`.
- Each row carries the same coarse columns as `search`, plus an explicit `bbox` object
  and `qa_flags` parsed as a JSON list.
- The full index is never loaded into Python; DuckDB reads only the matching row groups
  and returns one bounded page.

### Coverage aggregate

`coverage_bbox(west=..., south=..., east=..., north=..., region_id=...)` returns a
SQL-aggregated summary for map coverage:

```json
{
  "snapshot_id": "...",
  "method_version": "coarse-terrain-250m-1.0.0",
  "coverage_label": "local_coarse_index_not_global_exhaustive",
  "query": {"west": 10.0, "south": 24.0, "east": 11.0, "north": 25.0,
            "region_id": null, "antimeridian_crossing": false},
  "total": 64,
  "region_count": 1,
  "regions": ["pilot-sahara"],
  "longitude_intervals": [
    {"west": 10.0, "east": 11.0, "total": 64,
     "bbox": {"west": 9.996884649638446, "south": 23.996959690126204,
              "east": 10.393982962376148, "north": 24.36124818335005}}
  ]
}
```

For a crossing, intervals are aggregated separately and no single wrapped `bbox` is
emitted. This method uses `count`, `min`, `max` and a bounded `DISTINCT region_id`
query; it does not page the whole table into Python.

## How Earth map features use it

The Earth map-features path is `GET /api/v1/earth/map-features` (API layer, Issue
#132). It:

1. Parses and validates the request `bbox` as signed WGS84 lon/lat.
2. Calls `repository.query_bbox(...)` once for the viewport and carries the returned
   rows into the `coarse_candidate` feature type.
3. Renders `rows` as **coarse candidate features** (cell bbox/polygon plus candidate id,
   region, method version, support and QA flags), visually distinct from the basemap.
4. Carries the returned `total` into `coverage.coarse.matched_count`, and marks
   `coverage.coarse.truncated` when the bounded page is smaller than the total.
5. Keeps fine 90 m windows from `/global/refine` or the committed refinement report in a
   separate `fine_candidate`/`on_demand_aoi` layer; never labels a coarse candidate as a
   90 m result.
6. Preserves `coverage_label='local_coarse_index_not_global_exhaustive'` and never
   substitutes zero rows with synthetic features.

The route's `feature_type` accepts `coarse_candidate` and `data_level` accepts `coarse`
to request only the shortlist layer. `GET /api/v1/planetary/map-features` remains
Moon/Mars-only and must not be used as the Earth WGS84 path.

### curl examples

Existing coverage and search endpoints:

```bash
curl -sS 'http://127.0.0.1:8000/api/v1/global/coverage' | jq

curl -sS -X POST 'http://127.0.0.1:8000/api/v1/global/search' \
  -H 'content-type: application/json' \
  -d '{"target":{"coarse_slope_mean_deg":{"value":8.0,"weight":1.0}},"limit":5}' | jq
```

Earth viewport map features:

```bash
# Sahara pilot: coarse matched_count=64, default viewport page is bounded by limit
curl -sS 'http://127.0.0.1:8000/api/v1/earth/map-features?bbox=10,24,11,25&limit=50' | jq

# Only the coarse shortlist layer
curl -sS 'http://127.0.0.1:8000/api/v1/earth/map-features?bbox=10,24,11,25&limit=10&feature_type=coarse_candidate&data_level=coarse' | jq

# Broad bbox: reports total=168 but returns at most `limit` rows
curl -sS 'http://127.0.0.1:8000/api/v1/earth/map-features?bbox=-180,-90,180,90&limit=10' | jq

# Antimeridian crossing: two longitude intervals, never a wrapped world bbox
curl -sS 'http://127.0.0.1:8000/api/v1/earth/map-features?bbox=179,-2,-179,0&limit=50' | jq
```

`region_id` is not exposed by the map route; use the Python contract below when an
exact coarse-region filter is required. If the route is unavailable in a deployment,
call the same contract directly:

```python
from analog_pipelines.coarse_index import CoarseIndexRepository

repo = CoarseIndexRepository("pipelines/coarse/real-pilot/index")
page = repo.query_bbox(west=10.0, south=24.0, east=11.0, north=25.0, limit=50)
print(page["total"], page["returned"], page["next_offset"])
print(repo.coverage_bbox(west=10.0, south=24.0, east=11.0, north=25.0))
```

## Performance and limit caveats

- The committed index has 168 rows across 3 small Parquet partitions; queries are
  milliseconds locally. This says nothing about global-scale performance.
- There is no spatial R-tree or tile index. DuckDB filters Parquet columns and the
  `LIMIT` bounds the returned page, but a broad bbox still performs a count over all
  matching rows.
- `limit` is capped at 200 deliberately. Use `offset`/`next_offset` for paging rather
  than requesting the whole index; the broad-query test proves a page of 25 reports
  `total=168` without returning 168 rows.
- Any API wrapper must pass a coarse `limit` of at most 200. Do not forward a composed
  `offset + limit` (for example a client page of 200 at offset 200) into
  `query_bbox`; page the assembled feature list or narrow the viewport instead, or the
  repository will reject the call with `invalid_limit`.
- Do not issue one bbox query per map tile at animation frame rates. Cache coverage and
  page results at the API layer; the repository itself is read-only and stateless.
- An empty bbox returns `total=0` and `rows=[]`. This is a real empty result for the
  local index, not evidence that no Earth terrain exists there.
- The index is local-only and never mutated by queries; no file or database write is
  performed.

## Committed coarse -> fine evidence

Verifier:

```bash
uv run python scripts/check_global_refine_chain.py
```

It verifies the committed coarse index checksums and every link in
`pipelines/coarse/real-pilot/refine/refinement.json` offline. It does not modify a
database or call a provider.

### Exact committed counts

| Item | Count |
| --- | ---: |
| Coarse cells indexed | 168 |
| Coarse cells eligible for the pilot target | 168 |
| Coarse hits selected (top 1 per region) | 3 |
| Refined windows succeeded | 3 |
| Refined windows failed | 0 |
| Refined windows with no coverage | 0 |
| Fine terrain feature observations (5 per window) | 15 |
| WorldCover context observations (11 per window) | 33 |
| Committed links | 3 |
| Recorded failures | 0 |

Provider status: `copernicus-dem-glo90` verified 3/0 failed/0 no-coverage and
`esa-worldcover-2021-v200` verified 3/0 failed/0 no-coverage.

### Links

| Coarse candidate | Region | Coarse method | Fine method | Refined area | Status |
| --- | --- | --- | --- | --- | --- |
| `coarse-v1:249d33516a874855` | `pilot-alps` | `coarse-terrain-250m-1.0.0` | `terrain-metrics-1.0.0` | `earth-refine-48a9e5a4fee2ca81` | succeeded |
| `coarse-v1:e0d4c209d928961c` | `pilot-grand-canyon` | `coarse-terrain-250m-1.0.0` | `terrain-metrics-1.0.0` | `earth-refine-432552ef1e4bb863` | succeeded |
| `coarse-v1:95046a41aeb31ca9` | `pilot-sahara` | `coarse-terrain-250m-1.0.0` | `terrain-metrics-1.0.0` | `earth-refine-01a341a9cef18971` | succeeded |

The verifier confirms that:

- every refined area is linked to a coarse candidate that exists in the committed
  Parquet index and to that candidate's region;
- the coarse bbox, feature values, snapshot and rank in the report match the committed
  coarse row;
- each focus window is a 1.98 km window placed at the recorded coarse metric centre and
  lies inside the 5 km coarse cell;
- coarse method `coarse-terrain-250m-1.0.0` / 5 km / ~250 m support and fine method
  `terrain-metrics-1.0.0` / 1.98 km / 90 m support are distinct and not interchangeable;
- the `succeeded`/`failed`/`no_coverage` statuses, link count, failure count, fine
  feature observations and context observations all agree with the exact counts above;
- the report states no fine rank, no recall estimate and no global coverage claim.

### Persistence evidence for #97

The committed evidence proves the **coarse -> fine computation and linkage**, not
database persistence:

- `refinement.json` says refined windows are not promoted into the seedable index, the
  committed snapshot or user-created AOIs by this pilot.
- The verifier finds no DB row reference in the committed report; the link exists only
  in committed JSON plus the committed coarse Parquet rows.
- The separate Issue #132 `/api/v1/global/refine` path persists one executed focus
  window through the durable AOI queue. That runtime path was executed against the
  real Sahara coarse candidate `coarse-v1:95046a41aeb31ca9` in an isolated temporary
  database: refinement completed with `copernicus-dem-glo90` and
  `esa-worldcover-2021-v200` verified, the persisted area
  `earth-refine-01a341a9cef18971` was readable via `/locations/{id}`, and a standard
  `/search` query using its own fine target values returned it at distance `0.0`.
  The full response evidence is committed in
  [`ISSUE132_REAL_REFINE_EVIDENCE.json`](ISSUE132_REAL_REFINE_EVIDENCE.json).
- That runtime result is intentionally **not** promoted into the committed pilot
  snapshot; it uses a temporary SQLite database and bounded HTTPS reads. The committed
  snapshot remains the reproducible 168-cell coarse pilot plus its 3-link report.

No synthetic row is promoted as observed, and no failure is zero-filled.

## Reproduction commands

```bash
uv run pytest pipelines/tests/test_coarse_bbox_query.py -q
uv run python scripts/check_global_refine_chain.py
uv run ruff check pipelines/src/analog_pipelines/coarse_index.py \
  pipelines/tests/test_coarse_bbox_query.py scripts/check_global_refine_chain.py
uv run ruff format --check pipelines/src/analog_pipelines/coarse_index.py \
  pipelines/tests/test_coarse_bbox_query.py scripts/check_global_refine_chain.py
```
