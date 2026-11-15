# API

Base path: `/api/v1`. Local interactive docs: `http://127.0.0.1:8000/docs`. Intended public
deployment docs: `https://api.貓熊.台灣/docs` (`https://api.xn--2vxr09c.xn--kpry57d/docs`). The
committed machine
contract is `contracts/openapi.json`; regenerate it with `uv run python scripts/export_openapi.py`
after any schema change. The frontend types are generated from that snapshot.

All responses are JSON. Errors share one shape:

```json
{
  "error": {
    "code": "validation_error",
    "message": "unsupported feature id(s): gravity_m_s2",
    "details": [{"location": ["body", "target"], "message": "...", "type": "value_error"}]
  }
}
```

Codes: `validation_error` (422), `unknown_feature` (422), `not_found` (404), `no_indexed_data`
(409), `incompatible_method_version` (409), `body_mismatch` (422), `internal_error` (500). The
#116/#117 paths additionally use `feature_role_not_scoreable` (422), `feature_role_not_filterable`
(422), `body_out_of_scope` (422, `cross_body_incompatible`), `no_fine_candidates_for_body` (409),
`unsupported_body` (422), `invalid_bbox` / `invalid_bbox_latitude` (422), `invalid_dimension` (422)
and `invalid_longitude_convention` (422). Issue #132 adds `invalid_bbox_longitude` (422),
`invalid_lon_convention` (422), `invalid_feature_type` / `invalid_data_level` /
`invalid_availability` (422), `aoi_too_large` / `aoi_too_small` (422),
`out_of_coverage` (422), `unsupported_feature_on_body` (422), `refine_focus_invalid` (422),
`planetary_source_missing` / `planetary_analysis_invalid` (503) and `service_unavailable` (503).
Stack traces are never returned.

## GET /health

```json
{"status": "ok", "data_version": "dv-19f54b4539c8", "data_snapshot_id": "dv-19f54b4539c8"}
```

`data_version` is a deterministic hash of the loaded manifests. It changes when measurements,
provenance, or the manifest set changes.

## GET /capabilities

```json
{
  "contract_version": "1.0.0",
  "method_version": "terrain-metrics-1.0.0",
  "data_snapshot_id": "dv-19f54b4539c8",
  "analysis": {
    "window_size_m": 1980,
    "window_description": "Fixed 1.98 km x 1.98 km window (22 x 22 cells at 90 m) ...",
    "analysis_resolution_m": 90,
    "resampling": "bilinear",
    "min_valid_fraction": 0.8,
    "crs_policy": "...",
    "metric_family": "local terrain shape only"
  },
  "features": [
    {
      "id": "slope_mean_deg",
      "label": "Mean slope",
      "unit": "degree",
      "min_value": 0.0,
      "max_value": 90.0,
      "scale": 10.0,
      "method_version": "terrain-metrics-1.0.0",
      "valid_domain": "0 <= value <= 90 degrees",
      "...": "definition, method, rationale"
    }
  ],
  "supported_bodies": ["custom"],
  "indexed_candidate_count": 4,
  "coverage": {
    "label": "indexed_subset",
    "indexed_candidate_count": 4,
    "geographic_bbox": {"west": -116.9, "south": -45.0, "east": 133.5, "north": 75.4},
    "description": "Search is limited to the Earth windows persisted in this index...",
    "data_kinds": ["observed"]
  },
  "available_reference_ids": [],
  "data_version": "dv-..."
}
```

`Moon`/`Mars` appear in `supported_bodies` only when a verified reference was actually ingested.
`indexed_candidate_count: 0` and `geographic_bbox: null` are valid and expected on an empty index.

## GET /dimensions

Typed feature catalog grouped by dimension. `terrain_shape` features are `score_role=similarity`;
`land_cover_context` features are `score_role=context_only` and cannot be scored. The response also
includes `context_features` and the contract/method versions. `GET /capabilities` remains the
UI-friendly summary.

## POST /search

```json
{
  "body": "custom",
  "reference_id": null,
  "target": {
    "slope_mean_deg": {"value": 12, "weight": 1},
    "terrain_relief_p95_p05_m": {"value": 80, "weight": 0.5}
  },
  "limit": 10
}
```

- Values must be finite and within the registry domain. Weights are finite and >= 0; at least one
  weight must be positive. `limit` is 1..10.
- Unknown feature ids are rejected; unsupported dimensions are never silently dropped.
- `reference_id` and `target` are mutually exclusive. `body` must be `custom` for a user target;
  `Moon`/`Mars` requires a verified reference. Reference mode uses all four reference metrics with
  weight 1 unless `weights` overrides them (response still derives targets from the reference).
- Optional `filters` are inclusive **hard constraints** `{min?, max?}` applied before ranking.
  Since `search-1.1.0` a filter id may be a registered similarity feature (terrain metric, bounds
  within that feature's domain, e.g. `{"slope_mean_deg":{"min":5,"max":20}}`) or an Earth land-cover
  context feature (bounds within 0..1, e.g. `{"landcover_bare_sparse_fraction":{"min":0.5}}`).
  A missing value fails the filter (`missing_metric:<id>` or `missing_context:<id>`); a similarity
  feature counts only with the registry's method version. An out-of-range value is excluded as
  `filter:<id>`. Filters never change the similarity formula, scales, or weights, and land cover is
  still never weighted.
- The distance is **not** a similarity percentage and not a physical-equivalence claim.

Response (abridged):

```json
{
  "contract_version": "1.0.0",
  "method_version": "terrain-metrics-1.0.0",
  "query_version": "search-1.1.0",
  "data_version": "dv-...",
  "data_snapshot_id": "dv-...",
  "coverage": {"label": "indexed_subset", "indexed_candidate_count": 4,
               "geographic_bbox": {...}, "description": "...", "data_kinds": ["observed"],
               "origins": {"indexed": 4, "on_demand": 0}},
  "coverage_label": "indexed_subset",
  "requested_features": ["slope_mean_deg", "terrain_relief_p95_p05_m"],
  "filters_applied": {},
  "indexed_candidate_count": 4,
  "eligible_candidate_count": 4,
  "excluded_candidate_count": 0,
  "excluded": [],
  "results": [
    {
      "rank": 1,
      "location_id": "earth-synthetic-beta",
      "display_name": "SYNTHETIC window Beta (not an observation)",
      "lat": -24.5,
      "lon": 133.5,
      "bbox": {"west": 133.49, "south": -24.51, "east": 133.51, "north": -24.49},
      "distance": 0.49180788932265,
      "dimension_distances": {"terrain_shape": 0.49180788932265},
      "metrics": [
        {
          "feature_id": "slope_mean_deg",
          "unit": "degree",
          "target": 12.0,
          "candidate": 6.0,
          "delta": -6.0,
          "scale": 10.0,
          "weight": 1.0,
          "normalized_delta": -0.6,
          "contribution": 0.24
        }
      ],
      "dataset_ids": ["synthetic-test-fixture"],
      "data_kind": "synthetic",
      "quality_flags": ["synthetic_fixture", "not_a_real_observation"],
      "caveats": ["DEMO / SYNTHETIC fixture values - not a real terrain observation.", "..."]
    }
  ],
  "limitations": ["Only local terrain shape is compared. ..."]
}
```

`eligible_candidate_count` counts all candidates with every requested metric that has a positive
weight. Zero-weight metrics are not compared and do not require a candidate value. `excluded` lists
`location_id` plus `missing_metric:<feature_id>` or `incompatible_method_version`. Results are sorted
by ascending distance and then `location_id`.

Empty index: `409 {"error":{"code":"no_indexed_data",...}}`. Empty eligible set with a non-empty
index: HTTP 200 with `results: []` and exclusion reasons.

## GET /locations/{location_id}

Returns the persisted candidate, `data_snapshot_id`, bbox, CRS, valid fraction, `data_kind`,
`origin`/`area_km2`/`geometry`, quality flags, all feature observations (value/unit/method version/extraction scale/valid fraction/quality), dataset provenance
(provider, product, URL, license, access timestamp, checksum, processing version), and
`known_limitations`. Dataset-derived facts and generic limitations are separate fields.

## GET /locations/{location_id}/context

Optional `target` query parameter is a URL-encoded JSON object of target values, e.g.
`?target=%7B%22slope_mean_deg%22%3A12%7D`. The response is a grounded export:

```json
{
  "export_version": "1.0.0",
  "note": "Grounded structured export for external AI assistance. ...",
  "generated_at": "2026-11-14T06:00:00+00:00",
  "location": {"...": "same shape as /locations/{id}"},
  "query": {"slope_mean_deg": 12.0},
  "source_facts": ["Window centre: ...", "Source ...: ...", "slope_mean_deg = ..."],
  "computed_comparisons": [
    {"feature_id": "slope_mean_deg", "unit": "degree", "target": 12.0,
     "observed": 12.5, "delta": 0.5, "scale": 10.0, "normalized_delta": 0.05}
  ],
  "limitations": ["..."]
}
```

Without `target`, comparisons contain the observed values against their fixed scales with
`target`/`delta`/`normalized_delta` set to `null`. This endpoint never generates narrative text and
never adds external events.

## GET /references

Array of verified/precomputed Moon/Mars references with the same feature-observation shape as a
location. Empty array when none have been ingested. Only references whose raster was actually
processed and validated are listed.

## GET /datasets

Array of dataset provenance records: `dataset_id`, provider, product ID, title, URL, license and
license URL, access timestamp, source CRS/resolution, processing version, checksum, source kind,
notes. This is the source of the "Sources and methods" panel. The response includes an
`X-Data-Snapshot-ID` header; capabilities/search/health/location bodies also carry
`data_snapshot_id`.

## On-demand Earth AOI analysis (v6)

Workflow A analyzes a **new bounded Earth AOI** that is not pre-indexed. Providers are selected by
allowlisted registry ids; the API never accepts a URL.

### POST /api/v1/areas/analyze

```json
{
  "body": "Earth",
  "bbox": {"west": -6.617, "south": 37.682, "east": -6.600, "north": 37.699},
  "requested_features": [
    "slope_mean_deg", "slope_p90_deg",
    "terrain_relief_p95_p05_m", "roughness_std_m"
  ],
  "reference_id": null,
  "idempotency_key": null
}
```

Or supply a closed GeoJSON `Polygon` (outer ring only, 4–100 positions) instead of `bbox`. Limits:
each side 600–2000 m, area ≤ 4 km², latitudes −80..84, no antimeridian crossing. Feature ids are
validated against the similarity registry.

- New work returns `202` with `job_id`, `area_id`, `cache_key`, `status: queued|running`, and links.
- Identical completed work returns `200` with `cached: true` and the same `job_id`/`area_id`.
- The request thread does not do raster I/O; a SQLite-backed durable local queue plus a bounded
  worker pool runs extraction. Jobs left `running` at restart are marked retryable and requeued.
- The worker reads only the needed Copernicus DEM and ESA WorldCover windows, validates
  CRS/nodata/valid fraction, persists observations + provenance, and recomputes `data_version`.
- Each completed AOI also writes a runtime manifest under `ANALOG_AOI_RUNTIME_DIR` (default
  `var/aoi-catalog/`) with geometry, observations, dataset provenance, provider status, and
  checksum. This is outside the Git-pinned baseline snapshot and survives a SQLite rebuild as an
  audit/reprocess record; it is not automatically part of `build_index.py` snapshots.

### GET /api/v1/jobs/{job_id}

```json
{
  "job_id": "...",
  "status": "completed",
  "area_id": "earth-aoi-...",
  "provider_status": {
    "copernicus-dem-glo90": {"status": "verified", "tile": "..."},
    "esa-worldcover-2021-v200": {"status": "verified", "tiles": 1, "valid_pixels": 41616}
  },
  "error": null,
  "result": {"feature_count": 4, "context_feature_count": 11, "data_version": "dv-..."},
  "created_at": "...", "started_at": "...", "finished_at": "..."
}
```

Statuses: `queued`, `running`, `completed`, `partial` (terrain succeeded, a context provider
failed), `failed` (terrain extraction failed; `error.code` is actionable). Provider failures are
returned per provider, never as zero values.

### GET /api/v1/areas/{area_id}/features

Returns the same shape as `GET /locations/{id}` with `origin: "on_demand"`, the requested
geometry/bbox/area, similarity `features`, and `context_observations` labelled
`score_role: "context_only"`. Only on-demand areas are served here; indexed seed windows remain
under `/locations/{id}`. A successfully analyzed AOI is also searchable by the normal
`POST /search` comparator.

### Earth land-cover context

`context_observations` contains ESA WorldCover 2021 v200 class fractions (`unit: fraction`,
`source_resolution_m: 10`, 2021 epoch). They are Earth land-cover **context only**, not mineralogy,
not geology, and never part of the similarity distance. `GET /capabilities` exposes
`context_features`, `aoi_analysis` limits, and `provider_status`.

Additional error codes: `invalid_aoi`, `aoi_too_small`, `aoi_too_large`, `invalid_geometry`,
`out_of_provider_coverage`, `source_fetch_failed`, `source_extraction_failed`,
`context_provider_failed`, `service_unavailable`.

## Versioning and compatibility

- URL version `/api/v1` is stable for the MVP.
- `contract_version` covers request/response shape; `method_version` covers extraction compatibility;
  `query_version` covers ranking semantics; `data_version` identifies the loaded index.
- Additive optional fields are non-breaking. Removing/renaming fields, changing units, changing a
  scale, or changing missing-data behavior requires a contract/method version bump and a new
  contract test.
- The comparison rejects observations from an incompatible `method_version` instead of comparing
  silently.

## Adding a feature dimension

1. Add a `FeatureDefinition` with unit, domain, method, version, extraction scale, and fixed scale
   rationale in `domain/features.py`.
2. Update `contracts/contract.json`, regenerate OpenAPI, and update the manifest schema.
3. Implement extraction in `pipelines/src/analog_pipelines/features.py` and add synthetic-raster
   tests.
4. Re-ingest all datasets with the same method version; mixed versions are excluded.
5. The frontend form and result table are registry-driven, but add UI/tests for any new unit or
   formatting rule.

## Frontend types

`apps/web` generates TypeScript types from `contracts/openapi.json` with `openapi-typescript`.
Run `npm run generate:api` after regenerating the snapshot; `npm run check:api` detects drift in CI.

## Coarse global discovery (offline-first)

These endpoints are additive. They require a **verified local Parquet index** built by
`pipelines/src/analog_pipelines/coarse_index.py`. If no index is configured they do not
fabricate data.

### GET /global/coverage

`status=no_index` with `row_count=0` when no index exists. With an index:

```json
{
  "status": "ready",
  "snapshot_id": "fixture-snapshot",
  "label": "local_coarse_index_not_global_exhaustive",
  "row_count": 3,
  "regions": ["pilot"],
  "bbox": {"west": 10.0, "south": 45.0, "east": 10.06, "north": 45.045},
  "method_version": "coarse-terrain-250m-1.0.0",
  "features": ["coarse feature definitions ..."],
  "note": "Local coarse Parquet index; not an exhaustive global claim."
}
```

### POST /global/search

```json
{
  "target": {"coarse_slope_mean_deg": {"value": 10, "weight": 1}},
  "limit": 5
}
```

Returns deterministic top-K over the pinned snapshot, eligible/excluded counts and
the coverage label. Coarse feature IDs are separate from the 90 m IDs. Missing
metrics are excluded, never zero-filled. No index -> `409 no_indexed_data`.

`region_id` restricts ranking to one indexed region (for example `pilot-alps`).
`indexed_candidate_count` still reports the whole snapshot, while
`eligible_candidate_count` becomes that region's row count and
`excluded_candidate_count` grows by the other regions — an unknown `region_id`
returns zero results with every indexed row counted as excluded, never a silent
fallback to all regions. The response carries `snapshot_id`, `method_version`,
`index_version` and `coverage_label`; the pilot label is
`local_coarse_index_not_global_exhaustive` and must never be rendered as global.
The bounded bbox query (`query_bbox`) used by `/earth/map-features` accepts
`west > east` for antimeridian queries, returns SQL `total` independently of
`limit`/`offset`, and never materialises the whole index. See
[`GLOBAL_SEARCH_132.md`](GLOBAL_SEARCH_132.md).

### GET /global/candidates/{id}

Returns the coarse candidate record, including bbox, CRS, method version, source
ids and QA flags. Missing -> 404.

### POST /global/refine-plan

```json
{"candidate_id": "coarse-v1:...", "per_side": 2}
```

Returns planned 1.98 km fine windows. It does **not** run the 90 m AOI pipeline or
download a DEM. Coarse and fine methods are explicitly different; a comparison is
rejected rather than silently rescaled.

### POST /global/refine (Issue #132)

```json
{
  "candidate_id": "coarse-v1:...",
  "requested_features": ["slope_mean_deg", "slope_p90_deg"],
  "idempotency_key": "optional-client-key"
}
```

Executes one centered 1.98 km focus window for a committed coarse candidate through
the normal durable Earth AOI queue. The focus placement is
`coarse_recorded_metric_bbox_centre_v1`; the fine area id is deterministic and starts
with `earth-refine-`. On completion the real 90 m observations are persisted as an
on-demand candidate, so `GET /locations/{area_id}` works and the area participates in
the standard `/search` index. A repeated request returns `cached=true`; an unknown
candidate returns 404. The response carries the coarse candidate/region, both method
versions, the focus window, `job_id`, provider status and links. Coarse and fine
values are never merged or rescaled into one score. This endpoint performs a bounded
network read through the configured provider; offline tests inject a fixture
processor.

### GET /global/cross-body

Read-only cross-body coarse-ranking gate (Issue #36). It returns `ranked=false` and
`ranking_enabled=false` while no metric satisfies the same
quantity/method/support/unit/frame/validation contract:

```json
{
  "ranked": false,
  "ranking_enabled": false,
  "status": "blocked",
  "claim_scope": "bounded_pilot_cells_only_not_a_global_survey",
  "counts": {
    "evidence_sources": 3,
    "scanned_metric_observations": 9,
    "eligible_metric_observations": 0,
    "excluded_metric_observations": 9,
    "unknown_metric_observations": 0,
    "ranked_candidates": 0
  },
  "bodies": ["per-source metric decisions with exclusion reasons"],
  "comparability_decisions": ["the committed planetary decisions"],
  "blocking_features": [
    "coarse_flat_fraction_lt5deg",
    "coarse_slope_mean_deg",
    "coarse_slope_p90_deg"
  ],
  "ranked_candidates": []
}
```

The endpoint does not require an Earth coarse index and never returns a distance,
score or percentage for an ineligible metric. A missing index returns
`status=no_evidence`; a corrupt index returns `409 cross_body_evidence_invalid`.
The committed body × D01–D06 availability, real record counts, source URLs and
on-demand analysis support are published in
[`PLANETARY_COVERAGE_INVENTORY.json`](PLANETARY_COVERAGE_INVENTORY.json); the
narrative is in [`SIX_DIM_SOURCE_INVENTORY.md`](SIX_DIM_SOURCE_INVENTORY.md).

## Typed v2 observations

`GET /typed/observations?dimension=D02&body=Earth&feature_id=...` and
`GET /typed/areas/{area_id}` expose additive typed records from the SQLite typed
tables. Current committed real data is D01 terrain + Earth context, plus D06 Earth
SoilGrids model-prediction releases (base 0–5 cm and the 300-observation multi-depth
pilot). D02–D05 fixture rows are synthetic and clearly labelled. The v2 contracts live in
`contracts/v2/*.schema.json`; v1 manifests/API remain unchanged.

## Method versions

- Fine: `terrain-metrics-1.0.0` and `flat-terrain-fraction-1.0.0`.
- Coarse: `coarse-terrain-250m-1.0.0`, grid `coarse-grid-1.0.0`.
- Six-dimension evidence: `*-evidence-0.1.0` (not scored).

Never mix coarse and fine features in one distance.

## Feature eligibility registry (Issue #116)

### GET /search/features

Versioned query capability discovery, for example
`/api/v1/search/features?role=similarity&body=Earth&dimension=D06`.

Each feature reports:

- `dimension_id`, `body_scope`, `unit`, `value_kind`;
- six role slots (`similarity`, `hard_filter`, `category_match`, `evidence`, `uncertainty`,
  `context`) with `supported`, `reason` and `constraints`;
- `min_value`, `max_value`, `scale`, `scale_rationale`;
- `selector` (depth/statistic/window), `method_versions`, `support`, `qa_rules`;
- `comparability_gates`, `source`, `unsupported_reason`, `measurement_family`;
- `coverage` with actual observation/area counts, exact-ID-joined candidate counts and gated
  candidate ids.

The response also carries `join_audit` (`proximity_join_used=false`) and `audit_summary`.
`GET /search/features/{feature_id}` returns one entry or `404`.

### POST /search/multidimensional

```json
{
  "candidate_body": "Earth",
  "reference_id": null,
  "targets": {
    "slope_mean_deg": {"value": 10, "weight": 1},
    "soil_clay_0_5cm_mean": {"value": 200, "weight": 1}
  },
  "filters": {
    "soil_clay_0_5cm_mean": {"min": 100, "max": 300},
    "surface_air_temperature_2m_c": {"max": 10}
  },
  "category_filters": {"geologic_unit": ["Redwall Limestone"]},
  "limit": 10,
  "offset": 0,
  "include_evidence": true,
  "evidence_limit": 12
}
```

- Only registry features with `roles.similarity.supported=true` may be targets. Evidence-only
  targets return `422 feature_role_not_scoreable` with legal roles and a reason.
- Only features with `roles.hard_filter.supported=true` may be range `filters`. Issue #170 adds ten
  Earth-only coarse D03/D04/D05 context fields whose filter value is the stored 2024-01-01..07
  period mean; they are never similarity targets.
- Only features with `roles.category_match.supported=true` may be sent in `category_filters`. Today
  this is Earth `geologic_unit`, matching a stored source label exactly (3/113 coverage). It is a
  dropdown, not a taxonomy crosswalk.
- A positive-weight missing/gated feature excludes a candidate with explicit reasons. A missing
  filter value excludes it as `missing_filter:<id>`. Neither is ever filled with zero. A zero-weight
  feature is never compared and never excludes.
- Filters only select the candidate set: a retained candidate's `distance` and relative rank are
  unchanged from the unfiltered query.
- The response returns `distance`, `distance_scope`, `dimension_contributions`,
  per-feature `contributions` (target, candidate, delta, scale, weight, normalized delta,
  contribution, contribution fraction, observation id, selector, support, uncertainty), `coverage`,
  `excluded`, pagination fields and limitations.
- `distance` is a weighted query distance, **not** a six-dimension scientific total and not a
  cross-body equivalence.
- Moon/Mars references can use D01, but Earth-only D06 targets are rejected with
  `422 body_out_of_scope` (`reason: cross_body_incompatible`).

See [`MULTIDIMENSIONAL_SEARCH.md`](MULTIDIMENSIONAL_SEARCH.md),
[`D02_D05_CONTROLS_170.md`](D02_D05_CONTROLS_170.md) and
[`FEATURE_ELIGIBILITY_AUDIT.md`](FEATURE_ELIGIBILITY_AUDIT.md).

## Planetary maps (Issue #117)

### GET /planetary/maps?body=Moon|Mars

Returns the versioned official NASA Trek WMTS catalog: tile template, format, tile matrix set,
EPSG/body frame, zoom range, complete tile matrix, bounds, attribution, license, verification
status/date and sample tile. It also returns a curated `science_coverage` summary and a
`coverage_separation` block stating that a globally browsable basemap is not a global scientific
survey.

### GET /planetary/map-features?body=Moon|Mars&bbox=west,south,east,north

Returns real planetary references and typed AOIs intersecting the bbox. `bbox` uses planetocentric
lon/lat degrees; `west > east` crosses the antimeridian; `bbox_lon_convention=360` accepts 0–360
longitudes. `feature_type` is `all|reference|typed_area`; `dimension=D01..D06` filters by stored
coverage; `limit` is 1–200 with `offset`/`next_offset`. Each feature carries `center`, `bbox`,
`geometry`, `bbox_geometry`, exact-ID `linked_feature_id`, `relationship`,
`science_coverage.dimension_status` (`present`, `present_evidence`, `absent`), value origin,
availability and detail links.

Empty results are `total=0`; invalid body/bbox/dimension returns a structured 422. Earth records are
never mixed in.

### GET /planetary/coordinate-contract

Returns Moon/Mars radii, IAU frames, planetocentric/planetographic rules, positive-east longitude
conventions, `[-180,180)` vs `0..360` conversion formulas and examples, antimeridian split rules,
pole behavior and GeoJSON metadata. Coordinates are never Earth EPSG:4326/OSM.

See [`PLANETARY_MAPS_API.md`](PLANETARY_MAPS_API.md).

## Earth map viewport (Issue #132)

### GET /earth/map-features?bbox=west,south,east,north

Returns stored Earth fine candidates, on-demand AOIs, typed Earth areas and coarse shortlist rows
that intersect an Earth WGS84 viewport. This is the Earth counterpart of
`/planetary/map-features` and is designed for viewport queries rather than loading every candidate
in the browser.

- `bbox` is `west,south,east,north` in **Earth WGS84/EPSG:4326**; `west > east` crosses the
  antimeridian; `bbox_lon_convention=360` accepts `0..360`; omitted bbox means the whole world.
- `feature_type=all|fine_candidate|on_demand_aoi|typed_area|coarse_candidate`,
  `data_level=all|fine|coarse`, `dimension=D01..D06`, `availability=any|present|absent`,
  `limit` 1..200 and `offset`.
- Each feature reports `bbox`/`center`/`geometry`/`bbox_geometry` in Earth WGS84, `crs=EPSG:4326`,
  `coordinate_frame=earth_wgs84`, `support`/`support_label` (`fine_90m`,
  `shortlist_250m_derived_not_fine`, `stored_typed_subset`), `dimension_status` for D01-D06,
  `available_dimensions`, `missing_dimensions`, `data_version` and `detail_links` to
  `/locations/{id}`, `/global/candidates/{id}`, `/typed/areas/{id}` and `/areas/{id}/features`.
- `coverage` separates fine stored candidates, on-demand AOIs, typed Earth areas and the coarse
  index (`status`, `snapshot_id`/`snapshot`, `method_version`, `regions`, `bbox`, `matched_count`,
  `truncated`). The coarse index is queried with SQL bbox pushdown; it is never fully returned.
- Empty result is `total=0` with an explicit note. Missing dimensions are `absent` and never
  zero-filled. Coarse rows are a shortlist, not a fine 90 m result. There is no global/exhaustive
  coverage claim.
- Errors: malformed bbox (`invalid_bbox`, `invalid_bbox_latitude`, `invalid_bbox_longitude`),
  unsupported `body`/dimension/filter values return the shared structured 422 shape.

See [`EARTH_MAP_API.md`](EARTH_MAP_API.md) for curl examples, pagination, antimeridian/pole cases
and performance limits.

## On-demand planetary AOI analysis (Issue #132)

### POST /planetary/areas/analyze

```json
{
  "body": "Moon",
  "bbox": {"west": 19.85, "south": -10.15, "east": 20.15, "north": -9.85},
  "requested_features": [
    "coarse_slope_mean_deg",
    "coarse_slope_p90_deg",
    "coarse_flat_fraction_lt5deg"
  ]
}
```

Validates a bounded planetocentric AOI and queues a durable analysis job. Coordinates are in the
declared body frame (`lunar_planetocentric` / `mars_planetocentric`), never Earth EPSG:4326. The
response has the shared `job_id`/`status`/`area_id`/`cache_key`/`cached`/`provider_status`/`result`/
`links` fields plus `body`, `coordinate_frame`, `method_version`, `coverage_note` and
`dimension_support`. Poll `GET /jobs/{job_id}`; terminal states are `queued`, `running`,
`completed`, `partial`, `failed`. A repeated request is cached.

The reproducible committed cases are Moon around `19.85,-10.15,20.15,-9.85` (LOLA128 pilot, 4
cells) and Mars around `78.08,18.47,78.27,18.65` (Jezero CTX pilot, 4 cells; a single-cell
`78.08,18.47,78.18,18.56` case also works). AOI sides must stay within the 12,000 m guard. The
engine recomputes D01 coarse slope metrics from checksum-verified
committed fixtures. D02-D06 return explicit `unsupported_for_on_demand_analysis` reasons or stored
evidence notes; no value is fabricated. Outside committed coverage returns an explicit
`out_of_coverage`/`aoi_too_large` error or a failed job with a coverage reason. Moon may optionally
use a bounded live LOLA read when `ANALOG_PLANETARY_ALLOW_NETWORK=true`; Mars live analysis outside
Jezero is not claimed.

The persisted area is queryable through `GET /typed/areas/{id}`, `GET /planetary/areas/{id}` and
the planetary `map-features` endpoint. Planetary D01 values are evidence-only and never enter the
Earth similarity distance or a cross-body score. See
[`PLANETARY_AOI_ANALYSIS.md`](PLANETARY_AOI_ANALYSIS.md) and
[`FRONTEND_HANDOFF_132.md`](FRONTEND_HANDOFF_132.md).
