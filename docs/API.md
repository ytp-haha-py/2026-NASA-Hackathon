# API

Base path: `/api/v1`. Interactive docs: `http://127.0.0.1:8000/docs`. The committed machine
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
(409), `incompatible_method_version` (409), `body_mismatch` (422), `internal_error` (500). Stack
traces are never returned.

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
- Optional `filters` are **hard constraints** applied before ranking, using Earth land-cover
  context only, e.g. `{"landcover_bare_sparse_fraction":{"min":0.5},"landcover_built_up_fraction":{"max":0.1}}`.
  Missing context fails the filter (`missing_context:<id>`); an out-of-range value is excluded as
  `filter:<id>`. Filters never change the similarity formula.
- The distance is **not** a similarity percentage and not a physical-equivalence claim.

Response (abridged):

```json
{
  "contract_version": "1.0.0",
  "method_version": "terrain-metrics-1.0.0",
  "query_version": "search-1.0.0",
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

`eligible_candidate_count` counts all candidates with every requested metric.
`excluded` lists `location_id` plus `missing_metric:<feature_id>` or
`incompatible_method_version`. Results are sorted by ascending distance and then `location_id`.

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

## Typed v2 observations

`GET /typed/observations?dimension=D02&body=Earth&feature_id=...` and
`GET /typed/areas/{area_id}` expose additive typed records from the SQLite typed
tables. Current committed real data is D01 terrain + Earth context; D02–D06 fixture
rows are synthetic and clearly labelled. The v2 contracts live in
`contracts/v2/*.schema.json`; v1 manifests/API remain unchanged.

## Method versions

- Fine: `terrain-metrics-1.0.0` and `flat-terrain-fraction-1.0.0`.
- Coarse: `coarse-terrain-250m-1.0.0`, grid `coarse-grid-1.0.0`.
- Six-dimension evidence: `*-evidence-0.1.0` (not scored).

Never mix coarse and fine features in one distance.
