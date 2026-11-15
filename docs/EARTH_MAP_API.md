# Earth map features API (Issue #132)

Backend contract for the Earth map bbox endpoint. It returns real persisted Earth map features —
fine 90 m candidates, on-demand AOIs already analysed, stored typed areas and the committed coarse
250 m shortlist — for a WGS84 bounding box. The React/Leaflet map page is intentionally out of
scope; see [`FRONTEND_HANDOFF_132.md`](FRONTEND_HANDOFF_132.md).

## What is new

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/v1/earth/map-features` | Earth features intersecting a WGS84 bbox, with type/level/dimension filters and pagination. |

The route is additive. Existing detail routes and `/api/v1/search`, `/api/v1/global/search` are
unchanged. Planetary overlays remain on `GET /api/v1/planetary/map-features` and are **not** mixed
into this response.

Registered OpenAPI tag: `earth-map`.

## Coordinate contract (Earth only)

- **CRS:** `EPSG:4326`, WGS84 longitude/latitude degrees.
- **Longitude:** signed `[-180, 180]`. Pass `bbox_lon_convention=360` to submit `0..360` values;
  the response is always normalised back to signed degrees.
- **Antimeridian:** `west > east` means the box crosses 180°. The server evaluates the two
  longitude intervals `[west, 180]` and `[-180, east]`; it never silently wraps the query to a
  world-spanning rectangle.
- **Latitude:** `-90 <= south < north <= 90`. A pole-spanning box is allowed; the response reports
  the actual envelope and `crosses_antimeridian`.
- **Geometry:** `geometry` is a GeoJSON `Point` and `bbox_geometry` a GeoJSON `Polygon` in
  `[longitude, latitude]` order, `EPSG:4326`.
- **Planetary frames are different.** Moon/Mars use IAU planetocentric frames
  (`lunar_planetocentric`, `mars_planetocentric`) and never `EPSG:4326`. Do not feed planetary
  coordinates to this endpoint or to an Earth OSM/Web-Mercator layer.

## Query parameters

| Parameter | Type | Default | Meaning |
|---|---|---|---|
| `bbox` | string | whole world | `west,south,east,north` in degrees. `west > east` crosses the antimeridian. Frontends should always send the current viewport; omitting it returns the bounded global result. |
| `bbox_lon_convention` | `signed` \| `360` | `signed` | Input longitude convention. Output is always signed. |
| `feature_type` | `all` \| `fine_candidate` \| `on_demand_aoi` \| `typed_area` \| `coarse_candidate` | `all` | Include only one feature family. |
| `data_level` | `all` \| `fine` \| `coarse` | `all` | `fine` covers fine candidates and on-demand AOIs; `coarse` covers the 250 m shortlist; typed areas are returned with either level because they are stored evidence, not a re-computation. |
| `dimension` | `D01` … `D06` | none | Require a dimension status on the feature. Combine with `availability`. |
| `availability` | `any` \| `present` \| `absent` | `any` | Filter by the selected (or any stored) dimension. `absent` is an explicit no-data result, not zero fill. |
| `limit` | integer | `50` | Page size, `1..200`. Values above 200 return 422. |
| `offset` | integer | `0` | Non-negative offset. Use `next_offset` for the next page. |

Invalid enums, a malformed bbox, an out-of-range latitude, `south >= north`, `west == east`, a
negative offset or `limit > 200` return the shared structured 422 error shape.

## Response contract

Illustrative search for fine + coarse features near the Grand Canyon:

```bash
curl -sS 'http://127.0.0.1:8000/api/v1/earth/map-features?bbox=-112.3,36.0,-112.0,36.3&limit=200'
```

```json
{
  "contract_version": "earth-map-features-1.0.0",
  "coordinate_contract_version": "earth-wgs84-1.0.0",
  "data_version": "dv-b46f3bad38ea",
  "snapshot_id": "dv-b46f3bad38ea",
  "body": "Earth",
  "bbox": {
    "west": -112.3,
    "south": 36.0,
    "east": -112.0,
    "north": 36.3,
    "crosses_antimeridian": false,
    "longitude_span_deg": 0.3,
    "source": "query",
    "crs": "EPSG:4326"
  },
  "bbox_crs": "EPSG:4326",
  "coordinate_system": "Earth WGS84",
  "coordinate_frame": "earth_wgs84",
  "query": {
    "bbox": "-112.3,36.0,-112.0,36.3",
    "bbox_lon_convention": "signed",
    "bbox_source": "query",
    "feature_type": "all",
    "data_level": "all",
    "dimension": null,
    "availability": "any",
    "limit": 200,
    "offset": 0
  },
  "total": 34,
  "returned": 34,
  "offset": 0,
  "limit": 200,
  "next_offset": null,
  "counts_by_type": {
    "fine_candidate": 9,
    "on_demand_aoi": 0,
    "typed_area": 9,
    "coarse_candidate": 16
  },
  "coverage": {
    "fine": {
      "candidate_count": 9,
      "with_d01_count": 9,
      "matched_count": 9,
      "label": "fine_90m_stored_subset"
    },
    "on_demand": {
      "candidate_count": 0,
      "with_d01_count": 0,
      "matched_count": 0,
      "label": "on_demand_90m_stored_subset"
    },
    "typed": {
      "area_count": 9,
      "with_observations_count": 9,
      "matched_count": 9,
      "by_dimension": {"D06": 135},
      "unlocatable_count": 0,
      "label": "stored_typed_subset"
    },
    "coarse": {
      "status": "ready",
      "snapshot_id": "geomorpho250-real-pilot-20261115",
      "method_version": "coarse-terrain-250m-1.0.0",
      "row_count": 168,
      "regions": ["pilot-alps", "pilot-grand-canyon", "pilot-sahara"],
      "bbox": {
        "west": -112.20542079733367,
        "south": 23.996959690126204,
        "east": 10.393982962376148,
        "north": 45.36007210182725
      },
      "label": "local_coarse_index_not_global_exhaustive",
      "matched_count": 16,
      "returned": 16,
      "limit": 200,
      "offset": 0,
      "truncated": false,
      "query_truncated": false
    }
  },
  "features": [
    {
      "feature_id": "fine:earth-grand-canyon-r2c2",
      "feature_type": "fine_candidate",
      "body": "Earth",
      "display_name": "grand-canyon sampling window R2 C2",
      "location_id": "earth-grand-canyon-r2c2",
      "candidate_id": "earth-grand-canyon-r2c2",
      "area_id": null,
      "data_level": "fine",
      "bbox": {
        "west": -112.21114733,
        "south": 36.1909656,
        "east": -112.18885517,
        "north": 36.20903344
      },
      "center": {"lon": -112.2, "lat": 36.2},
      "geometry": {"type": "Point", "coordinates": [-112.2, 36.2]},
      "bbox_geometry": {
        "type": "Polygon",
        "coordinates": [[
          [-112.21114733, 36.1909656],
          [-112.18885517, 36.1909656],
          [-112.18885517, 36.20903344],
          [-112.21114733, 36.20903344],
          [-112.21114733, 36.1909656]
        ]]
      },
      "crs": "EPSG:4326",
      "bbox_crs": "EPSG:4326",
      "coordinate_frame": "earth_wgs84",
      "dimension_status": {
        "D01": {
          "status": "present",
          "record_count": 4,
          "value_origin": "stored_observation",
          "reason": null
        },
        "D02": {
          "status": "absent",
          "record_count": 0,
          "value_origin": null,
          "reason": "no_stored_observation"
        },
        "D03": {"status": "absent", "value_origin": null, "reason": "no_stored_observation"},
        "D04": {"status": "absent", "value_origin": null, "reason": "no_stored_observation"},
        "D05": {"status": "absent", "value_origin": null, "reason": "no_stored_observation"},
        "D06": {
          "status": "present",
          "record_count": 5,
          "value_origin": "stored_typed_observation",
          "reason": null
        }
      },
      "available_dimensions": ["D01", "D06"],
      "missing_dimensions": ["D02", "D03", "D04", "D05"],
      "availability": {
        "d01_present": true,
        "d02_present": false,
        "d03_present": false,
        "d04_present": false,
        "d05_present": false,
        "d06_present": true,
        "any_dimension_present": true,
        "dimension_count": 2,
        "support": "fine_90m",
        "shortlist_only": false
      },
      "support": "fine_90m",
      "support_label": "fine_90m",
      "data_version": "dv-b46f3bad38ea",
      "detail_links": {
        "location": "/api/v1/locations/earth-grand-canyon-r2c2",
        "typed_area": "/api/v1/typed/areas/earth-grand-canyon-r2c2",
        "area_features": null
      }
    }
  ],
  "notes": [
    "Fine candidates are stored 90 m terrain windows; coarse rows are 250 m-derived shortlist candidates (shortlist_250m_derived_not_fine), not fine 90 m results.",
    "Only stored observations are reported. Missing D02-D06 are absent with reason no_stored_observation and are never zero-filled.",
    "This is the stored subset in this deployment, not a global or exhaustive survey.",
    "Bounding boxes and GeoJSON coordinates are Earth WGS84 (EPSG:4326) degrees with longitude before latitude.",
    "A bbox with west > east crosses the antimeridian; both sides are included."
  ]
}
```

`counts_by_type` counts all matches for the query, independent of the page. `coverage.*.matched_count`
is a coverage summary for the same query, not the number of features on this page. Clients must not
hardcode `contract_version`, counts or snapshot ids.

`coverage.coarse` currently returns **`snapshot_id` and `method_version` as the primary names**;
`snapshot`/`method` are compatibility aliases when present. Read either but always render a snapshot
and method. The same object also reports `row_count`, `returned`, `limit`, `truncated` and
`query_truncated` so the client knows the coarse viewport was bounded.

### Feature fields

| Field | Type | Notes |
|---|---|---|
| `feature_id` | string | Stable map-feature id. Namespaced by family (`fine:`, `on_demand:`, `typed:`, `coarse:`); use it as the React key. |
| `feature_type` | enum | `fine_candidate`, `on_demand_aoi`, `typed_area`, `coarse_candidate`. |
| `body` | string | Always `Earth` on this endpoint. |
| `display_name` | string | Human-readable label. |
| `location_id` | string \| null | Fine/on-demand Earth candidate id, otherwise `null`. |
| `candidate_id` | string \| null | Candidate/coarse id where applicable. |
| `area_id` | string \| null | Typed or on-demand area id, otherwise `null`. |
| `typed_area_id` | string \| null | Typed area id when the feature has stored typed evidence. |
| `data_level` | enum | `fine`, `coarse` or `typed`. |
| `bbox` | object | Earth WGS84 envelope (`west`, `south`, `east`, `north`, antimeridian flags, `crs`). |
| `center` | object | `{lon, lat}` in `EPSG:4326`. |
| `geometry` | GeoJSON | `Point` at the center. |
| `bbox_geometry` | GeoJSON | `Polygon`, or `MultiPolygon` when the feature crosses 180°. |
| `crs` | string | Map/geometry CRS, `EPSG:4326`. For coarse rows the projected analysis CRS is exposed separately as `analysis_crs`. |
| `bbox_crs` | string | Always `EPSG:4326`; use this for map rendering. |
| `coordinate_frame` | string | Always `earth_wgs84`. |
| `dimension_status` | object | Exactly `D01`…`D06`. Each entry has `status`, `value_origin` and `reason` (plus record/support detail). |
| `available_dimensions` / `missing_dimensions` | string[] | Derived from `dimension_status`. |
| `availability` | object | Flags (`d01_present`…`d06_present`, `any_dimension_present`, `dimension_count`, `support`, `shortlist_only`). The query echo keeps the filter string (`any`/`present`/`absent`). |
| `support` / `support_label` | enum | Same value under both names: `fine_90m`, `shortlist_250m_derived_not_fine` or `stored_typed_subset`. |
| `data_version` | string | Version of the loaded manifests/typed records. |
| `detail_links` | object | Detail routes; unavailable links are `null`. |
| `analysis_crs` | string \| null | Stored/projected analysis CRS (for example a UTM code) when it differs from the map CRS. |

`dimension_status[*].status` is one of the server's explicit states (`present`,
`present_evidence`, `absent`, or an unsupported state for that level). A missing value is never
reported as `0`.

### Feature families and support labels

| `feature_type` | `data_level` | `support` / `support_label` | What it is |
|---|---|---|---|
| `fine_candidate` | `fine` | `fine_90m` | Existing indexed 90 m terrain candidate. |
| `on_demand_aoi` | `fine` | `fine_90m` | Previously analysed Earth AOI persisted in the candidate index. |
| `typed_area` | `typed` | `stored_typed_subset` | Stored typed-v2 evidence; may be D02–D06 only. |
| `coarse_candidate` | `coarse` | `shortlist_250m_derived_not_fine` | Committed 250 m-derived coarse shortlist. Not a fine result. |

## Coverage separation

Three independent layers are reported and must not be conflated:

1. **Fine index** — the curated Earth candidate set loaded from manifests. `coverage.fine.label` is
   `fine_90m_stored_subset`; it is not a global survey.
2. **On-demand / typed** — user-requested AOIs already analysed and stored typed evidence. These
   are additive records, not a re-run of the provider. `coverage.on_demand.label` is
   `on_demand_90m_stored_subset`; `coverage.typed.label` is `stored_typed_subset`.
3. **Coarse index** — the local Parquet shortlist. `coverage.coarse.status` is `ready` or
   `no_index`; `snapshot_id`/`method_version`/`regions`/`bbox`/`matched_count` describe the actual
   committed index (`snapshot`/`method` are compatibility aliases). A ready coarse index is still
   `local_coarse_index_not_global_exhaustive`.

Typed evidence is stored for the area where it was observed; a typed D06 observation does not make
a fine candidate a six-dimension ranked result. There is no Earth six-dimension total score.

## Examples

Base URL below is local. For the public deployment use
`https://api.貓熊.台灣/api/v1` (`https://api.xn--2vxr09c.xn--kpry57d/api/v1`).

### Normal (fine candidates)

```bash
curl -sS \
  'http://127.0.0.1:8000/api/v1/earth/map-features?bbox=-112.3,36.0,-112.0,36.3&feature_type=fine_candidate&limit=10'
```

### Antimeridian crossing

`west > east` means crossing 180°. This example returns the crossing flag and an explicit empty
result because no committed Earth candidate lies in that box:

```bash
curl -sS \
  'http://127.0.0.1:8000/api/v1/earth/map-features?bbox=170,-90,-170,90'
```

The same query in 0–360 input convention:

```bash
curl -sS \
  'http://127.0.0.1:8000/api/v1/earth/map-features?bbox=170,-90,190,90&bbox_lon_convention=360'
```

Both responses report `crosses_antimeridian: true` and `longitude_span_deg: 20`. Never pass
`west > east` to an Earth tile layer without splitting the bbox yourself.

### Polar box

```bash
curl -sS \
  'http://127.0.0.1:8000/api/v1/earth/map-features?bbox=-95,75,-85,76&limit=50'
```

### Explicit empty result

```bash
curl -sS \
  'http://127.0.0.1:8000/api/v1/earth/map-features?bbox=0,0,1,1'
```

Returns HTTP 200 with `total: 0` and `features: []`. An empty result is not an error and never
contains a synthetic placeholder.

### Coarse shortlist

```bash
curl -sS \
  'http://127.0.0.1:8000/api/v1/earth/map-features?bbox=-112.3,36.0,-112.0,36.3&data_level=coarse&feature_type=coarse_candidate&limit=200'
```

Read `coverage.coarse` and `support`/`support_label: "shortlist_250m_derived_not_fine"`. To refine
a coarse candidate, call `POST /api/v1/global/refine-plan`; do not present the coarse row as a 90 m
result. The coarse feature's `crs` is the map CRS (`EPSG:4326`); its projected analysis CRS is
`analysis_crs`.

### Fine only

```bash
curl -sS \
  'http://127.0.0.1:8000/api/v1/earth/map-features?bbox=-112.3,36.0,-112.0,36.3&data_level=fine&limit=50'
```

### On-demand AOI

Analyse a bounded Earth AOI first (normally with a real provider), then read it back by bbox:

```bash
curl -sS -X POST 'http://127.0.0.1:8000/api/v1/areas/analyze' \
  -H 'Content-Type: application/json' \
  -d '{
    "body": "Earth",
    "bbox": {"west": -6.617, "south": 37.682, "east": -6.600, "north": 37.699}
  }'

curl -sS \
  'http://127.0.0.1:8000/api/v1/earth/map-features?bbox=-6.62,37.68,-6.60,37.70&feature_type=on_demand_aoi'
```

The returned `area_id` also opens `GET /api/v1/areas/{area_id}/features`.

### Typed areas

```bash
curl -sS \
  'http://127.0.0.1:8000/api/v1/earth/map-features?bbox=-68.5,-23.6,-68.3,-23.4&feature_type=typed_area&limit=50'
```

Typed features carry `support`/`support_label: "stored_typed_subset"` and
`detail_links.typed_area = "/api/v1/typed/areas/{area_id}"`.

### Dimension filters

Present D06 evidence anywhere in the loaded index (all committed Earth candidates currently have
D06 depth observations):

```bash
curl -sS \
  'http://127.0.0.1:8000/api/v1/earth/map-features?bbox=-180,-90,180,90&dimension=D06&availability=present&limit=10'
```

Present D01 only, fine level:

```bash
curl -sS \
  'http://127.0.0.1:8000/api/v1/earth/map-features?bbox=-180,-90,180,90&dimension=D01&availability=present&data_level=fine&limit=10'
```

Explicit absence:

```bash
curl -sS \
  'http://127.0.0.1:8000/api/v1/earth/map-features?bbox=-180,-90,180,90&dimension=D02&availability=absent&limit=10'
```

`availability=absent` returns features whose selected dimension has no stored observation. It does
not mean the value was measured as zero.

### Pagination

```bash
curl -sS \
  'http://127.0.0.1:8000/api/v1/earth/map-features?bbox=-180,-90,180,90&limit=25&offset=0'
curl -sS \
  'http://127.0.0.1:8000/api/v1/earth/map-features?bbox=-180,-90,180,90&limit=25&offset=25'
```

Stop when `next_offset` is `null`. Do not compute the next page from `total` alone; the server is
authoritative.

### Error cases

```bash
# Malformed bbox -> 422 invalid_bbox
curl -sS 'http://127.0.0.1:8000/api/v1/earth/map-features?bbox=0,0,1'

# Latitude out of range -> 422 invalid_bbox_latitude
curl -sS 'http://127.0.0.1:8000/api/v1/earth/map-features?bbox=0,0,1,91'

# South > north or west == east -> 422 invalid_bbox
curl -sS 'http://127.0.0.1:8000/api/v1/earth/map-features?bbox=0,10,1,-10'
curl -sS 'http://127.0.0.1:8000/api/v1/earth/map-features?bbox=10,0,10,1'

# Limit above the hard cap -> 422 validation_error
curl -sS 'http://127.0.0.1:8000/api/v1/earth/map-features?bbox=-180,-90,180,90&limit=201'

# Unknown dimension or enum -> 422
curl -sS 'http://127.0.0.1:8000/api/v1/earth/map-features?bbox=-180,-90,180,90&dimension=D07'
curl -sS 'http://127.0.0.1:8000/api/v1/earth/map-features?bbox=-180,-90,180,90&feature_type=planet'
```

All errors use the shared shape:

```json
{
  "error": {
    "code": "invalid_bbox",
    "message": "south must be <= north",
    "details": []
  }
}
```

## Performance and limits

- `limit` is hard-capped at **200**; there is no unbounded "whole world" response. The default is
  50. Request pages with `limit`/`offset` and render only `features`.
- Coarse filtering is pushed down to the DuckDB/Parquet index with an overlap predicate and
  `LIMIT`/`OFFSET`; the full index is never materialised in Python. Antimeridian queries are
  evaluated as two longitude intervals.
- Fine and typed filtering query the local SQLite/PostGIS-backed tables for the bbox; only 113 fine
  Earth candidates are committed today.
- `total`, `counts_by_type` and `coverage.*.matched_count` are query-level counts, not page counts.
- Coarse density is high (168 committed 5 km cells in the current pilot index); at low zoom, filter
  by `data_level=fine` or `feature_type` and increase the client-side bbox rather than requesting
  thousands of overlapping coarse cells.
- The endpoint has no network side effects. It does not download rasters, call providers, or
  refresh the coarse index.
- `coverage.coarse.status == "no_index"` is a valid deployment state. Fine/typed results are still
  returned; no fake coarse rows are inserted.
- Responses carry the data snapshot header used elsewhere (`X-Data-Snapshot-ID`) and a short public
  cache lifetime; a changed `data_version` invalidates downstream caches.

## Interaction with existing detail routes

| Feature | Open |
|---|---|
| `fine_candidate` | `GET /api/v1/locations/{location_id}` |
| `on_demand_aoi` | `GET /api/v1/areas/{area_id}/features` (and `POST /api/v1/areas/{area_id}/compare`) |
| `typed_area` | `GET /api/v1/typed/areas/{area_id}` |
| `coarse_candidate` | `GET /api/v1/global/candidates/{candidate_id}` |

`detail_links` already contains these URLs where applicable (null otherwise), so the map should
follow the links instead of constructing paths itself. The map endpoint is a discovery surface: it
does not replace the detail payloads or the similarity search.

## Reproduce locally

```bash
make seed
uv run pytest apps/api/tests/test_issue132_acceptance.py -q
bash scripts/issue132_acceptance.sh            # offline route smoke
bash scripts/issue132_acceptance.sh --public   # live public API check (network)
```

Regenerate the committed OpenAPI snapshot after any schema change:

```bash
uv run python scripts/export_openapi.py
```

`contracts/openapi.json` and the generated TypeScript types are maintained by the main integration
agent; this document describes the intended contract so the frontend can start against mocks.
