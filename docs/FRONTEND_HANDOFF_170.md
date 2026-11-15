# Frontend handoff — planetary AOI / D01 for #170 (Issues #119 and #123)

Audience: the #119 (six-dimension conditions) and #123 (single map workflow) frontend
collaborators. This file is a **contract handoff, not a UI implementation**. No React/Leaflet code
is changed by #170 Goal H.

Companion documents:

- [`FRONTEND_HANDOFF_132.md`](FRONTEND_HANDOFF_132.md) — the existing planetary AOI and Earth map
  contract. This document does not replace it.
- [`PLANETARY_AOI_ANALYSIS.md`](PLANETARY_AOI_ANALYSIS.md) — current backend behavior for
  `POST /api/v1/planetary/areas/analyze`.
- [`PLANETARY_MAPS_API.md`](PLANETARY_MAPS_API.md) — basemaps, coordinate contract and overlays.
- `docs/FRONTEND_HANDOFF_160.md` on the `feat/160-sixdim-aoi` branch — the #119 six-dimension base
  contract. It is **not on this branch**; #170 must merge/coordinate with it before #119 is built.
- [`MULTIDIMENSIONAL_SEARCH.md`](MULTIDIMENSIONAL_SEARCH.md) — feature registry and legal query
  roles. #119 must use the live registry, not a hard-coded D01–D06 layout.

## 0. Status: CURRENT vs TARGET

**CURRENT** means the endpoint, field or enum exists in `contracts/openapi.json` and in the
generated `apps/web/src/api/schema.d.ts` on this branch (2026-11-15, `feat/170-h-integration`).
**TARGET** means the #170 Goals A/B/C/D surface described below is **not merged into this
integration branch yet**. It is a requested contract, with exact proposed names, so the frontend
can plan against it and the coordinator can approve or rename it before implementation.

> **Do not send TARGET request fields or read TARGET response fields until they appear in
> `GET /api/v1/openapi.json`.** The planetary analyze request has `extra="forbid"`, so an unknown
> field is a 422, not a harmless extra. Do not cast a missing TARGET field to `any`; surface a
> contract mismatch.

**A/B arbitrary-AOI status (important):** Goals A (Moon global LOLA) and B (Mars global MOLA) have
their own branches (`feat/170-a-moon-d01`, `feat/170-b-mars-d01`) with source adapters/engine work,
but that arbitrary-global-AOI integration is **not merged into this H branch**. On this branch the
live behavior is still the bounded committed pilots. Mid/high-latitude Moon and Mars requests, and
any location outside the committed pilot windows, return an explicit
`out_of_coverage`/`failed` outcome with no values. Do not present them as successful global AOI
coverage, and do not build #123 against A/B fields until they land in `contracts/openapi.json`.

## 1. What #123 must be able to do

#123 replaces four top-level tabs with one map-centered workflow. The planetary part of that
workflow needs these states, all from real API responses:

| Workflow step | API surface | Status |
|---|---|---|
| Choose Earth / Moon / Mars | `GET /api/v1/planetary/bodies`, `GET /api/v1/planetary/maps?body=Moon\|Mars` | CURRENT |
| Show the body frame and safe map layer | `GET /api/v1/planetary/coordinate-contract` | CURRENT |
| Draw or pick a Moon/Mars bbox | `GET /api/v1/planetary/map-features?body=...&bbox=...` for existing areas | CURRENT |
| Ask whether this place can be analyzed | `GET /api/v1/planetary/coverage?body=...` | **TARGET** |
| Analyze the selected bbox | `POST /api/v1/planetary/areas/analyze` | CURRENT for committed pilots; **TARGET** for arbitrary global bbox |
| Show progress | `GET /api/v1/jobs/{job_id}` or `GET /api/v1/planetary/jobs/{job_id}` | CURRENT |
| Show D01 values and coverage | `result` in the analyze/job response | CURRENT fields; **TARGET** coverage/elevation fields |
| Show D02–D06 evidence | `GET /api/v1/typed/areas/{area_id}` | CURRENT; **TARGET** for real on-demand D02–D06 |
| Use the result as a six-dimension target | `GET /api/v1/search/features`, `POST /api/v1/search/multidimensional` | CURRENT contract; **TARGET** planetary D01 gate decision |
| Open the area on the map | `links.map_features` / `GET /api/v1/planetary/map-features` | CURRENT (stored AOIs appear as `typed_area`) |

The workflow must never infer “this place is covered” from basemap imagery. A visible NASA Trek
basemap is not scientific coverage, and a committed pilot cell is not a global survey.

## 2. CURRENT contract (buildable now)

### 2.1 Endpoint inventory

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/v1/planetary/bodies` | Compact Moon/Mars body metadata. |
| `GET` | `/api/v1/planetary/maps?body=Moon\|Mars` | Official basemap tiles, CRS, zoom, bounds, attribution, science-coverage summary. |
| `GET` | `/api/v1/planetary/coordinate-contract` | Planetocentric rules, antimeridian/pole behavior, Mars planetographic conversion. |
| `GET` | `/api/v1/planetary/map-features?body=...&bbox=...` | Stored reference and typed-area overlays. |
| `POST` | `/api/v1/planetary/areas/analyze` | Validate a bounded Moon/Mars bbox and enqueue/reuse a durable D01 job. |
| `GET` | `/api/v1/jobs/{job_id}` | Shared job status/result. |
| `GET` | `/api/v1/planetary/jobs/{job_id}` | Planetary-only alias of the same job. |
| `GET` | `/api/v1/planetary/areas/{area_id}` | Stored planetary typed area. |
| `GET` | `/api/v1/typed/areas/{area_id}` | Same stored area plus every typed observation. |
| `GET` | `/api/v1/typed/observations?body=Moon\|Mars&dimension=D01` | Typed evidence query by body/dimension/feature. Use the typed-area route for one exact `area_id`. |

Current response models are open (`additionalProperties: true`) for the typed-v2 routes. They are
real, but a field not in the generated types must be treated as optional and validated at runtime.

### 2.2 Exact TypeScript request/response types

These are the committed generated types. Use `components["schemas"]["..."]` or re-export from
`apps/web/src/api/client.ts`; do not hand-write a conflicting copy.

```ts
export type GeoBBoxIn = {
  west: number;
  south: number;
  east: number;
  north: number;
};

export type PlanetaryAoiAnalyzeRequest = {
  body: "Moon" | "Mars";
  bbox: GeoBBoxIn; // planetocentric lon/lat degrees in the body frame
  requested_features?: string[] | null; // currently D01 coarse ids only
  idempotency_key?: string | null; // max 128 chars; reuse on retry
};

export type AoiJobStatus = "queued" | "running" | "completed" | "partial" | "failed";

export type AoiAnalyzeResponse = {
  job_id: string;
  status: AoiJobStatus;
  area_id: string | null;
  cache_key: string;
  cached: boolean;
  provider_status?: { [key: string]: unknown };
  result?: { [key: string]: unknown } | null;
  links?: { [key: string]: string };
  body?: string | null;
  coordinate_frame?: string | null;
  method_version?: string | null;
  coverage_note?: string | null;
  dimension_support?: { [key: string]: unknown };
};

export type JobOut = {
  job_id: string;
  status: AoiJobStatus;
  area_id: string | null;
  body?: string | null;
  coordinate_frame?: string | null;
  provider_status: { [key: string]: unknown };
  error: { [key: string]: string } | null;
  result: { [key: string]: unknown } | null;
  created_at: string | null;
  started_at: string | null;
  finished_at: string | null;
};
```

The current `AoiAnalyzeResponse.result` is intentionally untyped at the OpenAPI boundary. The
observed Moon result contains the fields below; treat them as optional runtime fields until the
TARGET `PlanetaryAoiResultOut` schema lands:

```ts
export type CurrentPlanetaryAoiResult = {
  area_id: string;
  location_id: string;
  body: "Moon" | "Mars";
  coordinate_frame: string;
  method_version?: string;
  analysis_kind?: "committed_reference_fixture" | "live_bounded_read" | string;
  coverage_note?: string;
  data_version?: string;
  feature_count?: number;
  context_feature_count?: number;
  features?: Record<string, number>;
  dimension_support?: Record<string, Record<string, unknown>>;
  provider_status?: Record<string, Record<string, unknown>>;
  sources?: Array<Record<string, unknown>>;
  warnings?: string[];
  area_km2?: number;
};
```

`features` currently contains only the three verified D01 coarse ids:

| `feature_id` | Unit | Meaning |
|---|---|---|
| `coarse_slope_mean_deg` | `degree` | Mean Horn slope on the 250 m analysis grid. |
| `coarse_slope_p90_deg` | `degree` | Linear 90th-percentile slope. |
| `coarse_flat_fraction_lt5deg` | `fraction` | Fraction of valid slope pixels strictly below 5°. |

D02–D06 are **not** values in `features`. They are explicit statuses in `dimension_support` and,
for stored areas, typed observations under `GET /api/v1/typed/areas/{area_id}`.

### 2.3 Observed Moon example (real local response, no network)

Request:

```http
POST /api/v1/planetary/areas/analyze
Content-Type: application/json

{
  "body": "Moon",
  "bbox": {"west": 19.85, "south": -10.15, "east": 20.15, "north": -9.85},
  "requested_features": [
    "coarse_slope_mean_deg",
    "coarse_slope_p90_deg",
    "coarse_flat_fraction_lt5deg"
  ],
  "idempotency_key": "handoff-170-probe"
}
```

Observed (trimmed; HTTP 200 because the local test runner is inline):

```json
{
  "job_id": "dc03439b6b0a44c7aac4b984e342d20c",
  "status": "completed",
  "area_id": "planet-aoi-moon-122db1830912",
  "cache_key": "48ce74cd162733ed947b31908f3c6ec850dd5a118c1a61b86bdaae4d149479eb",
  "cached": false,
  "body": "Moon",
  "coordinate_frame": "lunar_planetocentric",
  "method_version": "planetary-terrain-horn-250m-aoi-1.0.0",
  "coverage_note": "Committed reference fixture analysis over 4 Moon cell(s) ...",
  "provider_status": {
    "moon-lola128-pa-pixel-202405": {
      "status": "verified_committed_fixture",
      "network_used": false,
      "fixture_checksums_verified": true,
      "native_resolution_m": 236.90117518866782
    }
  },
  "result": {
    "area_id": "planet-aoi-moon-122db1830912",
    "location_id": "planet-aoi-moon-122db1830912",
    "body": "Moon",
    "coordinate_frame": "lunar_planetocentric",
    "analysis_kind": "committed_reference_fixture",
    "method_version": "planetary-terrain-horn-250m-aoi-1.0.0",
    "data_version": "dv-a84213037753",
    "feature_count": 3,
    "features": {
      "coarse_slope_mean_deg": 11.459459413931441,
      "coarse_slope_p90_deg": 30.035049395554832,
      "coarse_flat_fraction_lt5deg": 0.22125
    },
    "dimension_support": {
      "D01": {"status": "present", "quality": "verified_committed_fixture_recompute"},
      "D02": {"status": "unsupported_for_on_demand_analysis", "value": null},
      "D03": {"status": "unsupported_for_on_demand_analysis", "value": null},
      "D04": {"status": "unsupported_for_on_demand_analysis", "value": null},
      "D05": {"status": "unsupported_for_on_demand_analysis", "value": null},
      "D06": {"status": "unsupported_for_on_demand_analysis", "value": null}
    },
    "sources": [{"dataset_id": "nasa-pgda-lola-moon-pa-ldem128-202405", "provider": "NASA GSFC PGDA / LRO-LOLA"}],
    "warnings": ["requested_bbox and analysis_bbox differ: ...", "..."],
    "area_km2": 81.4983365073781
  },
  "links": {
    "job": "/api/v1/jobs/dc03439b6b0a44c7aac4b984e342d20c",
    "typed_area": "/api/v1/typed/areas/planet-aoi-moon-122db1830912",
    "map_features": "/api/v1/planetary/map-features?body=Moon&bbox=19.85,-10.15,20.15,-9.85"
  }
}
```

**The numbers are not constants.** They are the committed Moon LOLA128 pilot at 20°E, 10°S. A
different bbox returns different values, and an uncovered bbox returns an explicit error or a
failed job — never zeros and never a silently clipped analysis.

Polling and errors:

- `202` while `queued`/`running`; `200` when `completed`/`partial`/`failed` with an inline runner.
- Poll `links.job` with exponential backoff (0.5 s → 1 s → 2 s, capped ~5 s, bounded timeout).
  Never poll faster than about 2 requests/second.
- Generate the idempotency key once per user action (for example `crypto.randomUUID()`); reuse it
  on retry of the same click.
- `failed` jobs carry `error.code` / `error.message` on the job route and a null/empty `result`.
  `provider_status.analysis.reason` may also explain the failure (for example `out_of_coverage`).
- `partial` jobs carry real values plus at least one non-verified provider. Show the partial badge.

**`links.typed_area` is only advertised for `completed`/`partial` jobs.** A failed
out-of-coverage job may carry an `area_id` for its durable job row, but no typed area was
persisted; `GET /api/v1/typed/areas/{area_id}` would return 404. The analyze response omits
`links.typed_area` for `queued`, `running` and `failed` and keeps `links.job` plus the bbox-based
`links.map_features`. Frontends must not construct a typed-area URL from a failed job's `area_id`;
open the typed area only from a completed/partial response or after confirming the detail route
returns 200. Regression test:
`test_failed_job_does_not_advertise_an_unpersisted_typed_area`.

### 2.4 Overlays and typed observations

`GET /api/v1/planetary/map-features` current query:

```ts
type PlanetaryMapFeaturesQuery = {
  body: string;
  bbox?: string; // "west,south,east,north"; west > east crosses the antimeridian
  bbox_lon_convention?: "signed" | "360"; // default "signed"
  limit?: number; // 1..200, default 50
  offset?: number; // >= 0
  feature_type?: "all" | "reference" | "typed_area"; // default "all"
  dimension?: string; // D01..D06
};
```

Current `feature_type` values do **not** include a separate on-demand type. A stored on-demand
planetary AOI appears as a `typed_area` with `availability: "typed_area_only"` or
`"typed_area_and_exact_reference"` and `detail_links.typed_area`. Do not infer its analysis kind
from `feature_type`; read `science_coverage`/`detail_links` and then fetch the typed area.

`GET /api/v1/typed/areas/{area_id}` returns:

```ts
type TypedObservation = {
  observation_id: string;
  area_id: string;
  body: string;
  dimension_code: string | null;
  feature_id: string;
  value_kind: string;
  state: "detected" | "not_detected" | "no_coverage" | "unknown" | "not_applicable" | string;
  value: unknown;
  unit: string | null;
  method_version: string;
  dataset_ids: string[];
  source_asset_ids: string[];
  quality_flags: string[];
  score_role: string;
  spatial_support: Record<string, unknown>;
  temporal_support: Record<string, unknown>;
  depth_support: Record<string, unknown>;
  uncertainty: Record<string, unknown> | null;
  notes: string | null;
};
```

The area response is `{area_id, body, bbox, crs, coordinate_frame, area_km2, notes,
observations: TypedObservation[]}` (with open extra fields). Use it for D01–D06 exact-area
evidence. Do not copy a value from a different `area_id`, and do not treat a stored D02–D06 record
as a measurement of a new AOI.

### 2.5 Current frontend client gaps (not fixed by this handoff)

`apps/web/src/api/client.ts` currently exposes Earth AOI functions only:

- `analyzeArea()` posts to `/areas/analyze` (Earth). It must not be used for Moon/Mars.
- `getAreaFeatures()` calls `/areas/{area_id}/features`, which returns an Earth `LocationOut`.
  For a planetary area use `GET /api/v1/typed/areas/{area_id}`.
- `getJob()` is body-agnostic and works for planetary jobs.
- There are no client functions for `/planetary/bodies`, `/planetary/maps`,
  `/planetary/coordinate-contract`, `/planetary/map-features` or
  `/planetary/areas/analyze`.

Those are ordinary #123 frontend work, not API changes. The generated `schema.d.ts` already
contains the planetary path and schema types even though `client.ts` does not re-export all of
them.

## 3. TARGET #170 contract (not implemented on this branch)

Ownership: Goals A (Moon D01), B (Mars D01) and C (raster/cache) provide real global reads;
Goal D owns AOI geometry, jobs and the six-dimension API contract; Goal E/F provide real
D02–D06 data only where locatable; Goal G owns the comparison gate. Shared `apps/api` schemas are
coordinator-owned. The names below are a **requested schema change**, not a promise that D will
choose the same spelling.

### 3.1 TARGET request additions

```ts
type PlanetaryAoiAnalyzeRequestV170 = {
  body: "Moon" | "Mars";
  bbox: { west: number; south: number; east: number; north: number };

  /** NEW: 0..360 client convention. Default "signed" (-180..180). */
  bbox_lon_convention?: "signed" | "360";

  /**
   * NEW: how to satisfy the request.
   * "auto": use committed/global index first; one bounded live read only if the server allows it.
   * "committed_only": never network; return explicit no_coverage/not_indexed if absent.
   * "live_allowed": permit one bounded live read if the server policy allows it.
   * Server env policy still wins; a disallowed live request fails explicitly.
   */
  source_policy?: "auto" | "committed_only" | "live_allowed";

  /** D01..D06 ids from GET /api/v1/search/features?body=Moon|Mars. Unknown ids are 422. */
  requested_features?: string[] | null;

  idempotency_key?: string | null;
};
```

Rules:

- `bbox` remains planetocentric lon/lat degrees in the declared body frame. `west > east` means an
  antimeridian crossing; do not sort or clamp it.
- A huge bbox must return `aoi_too_large`; a tiny one `aoi_too_small`; a >180° span
  `bbox_too_wide`. Do not auto-shrink the AOI.
- `source_policy="committed_only"` is the safe frontend toggle for a reproducible exhibition/demo.
  `source_policy="live_allowed"` must still be refused server-side when the deployment disables
  network reads; surface the explicit error instead of retrying.
- Unknown `requested_features` return `unsupported_feature_on_body`. D02–D06 ids that are known
  but have no on-demand source must **not** be silently dropped; they return an explicit
  `dimension_support` status.

### 3.2 TARGET analyze/job result

Goal D should replace the untyped `result` object with a named schema so the frontend does not
guess. Proposed types:

```ts
type PlanetaryAoiCoverageStatus =
  | "complete"
  | "partial"
  | "no_coverage"
  | "not_indexed"
  | "unsupported";

type PlanetaryAnalysisKind =
  | "committed_reference_fixture"
  | "live_bounded_read"
  | "global_indexed_read"
  | "global_tiled_read";

type PlanetaryAoiBBox = {
  west: number;
  south: number;
  east: number;
  north: number;
  crosses_antimeridian: boolean;
  longitude_span_deg: number;
  source: string; // "query" | "analysis_union" | "source_footprint"
  crs?: "planetocentric_lon_lat_degrees";
};

type PlanetaryAoiCoverage = {
  status: PlanetaryAoiCoverageStatus;
  coverage_label: string; // server-defined, display verbatim
  global_coverage_claimed: false; // must be false for #170 pilots
  region_ids: string[];
  cell_count: number;
  valid_pixel_count: number;
  nodata_pixel_count: number;
  invalid_pixel_count: number;
  valid_fraction: number;
  requested_area_km2: number;
  analysis_area_km2: number | null;
  source_resolution_m: number | null;
  analysis_resolution_m: number;
  window_size_m: number;
  cells_truncated: boolean;
  quota: {
    max_side_m: number;
    max_area_km2: number;
    max_pixels: number;
    max_source_read_bytes: number | null;
    source_read_bytes_upper_bound: number | null;
  };
};

type PlanetaryElevationStats = {
  unit: "m";
  vertical_reference: string; // provider/areoid text, not an Earth orthometric datum
  min_m: number | null;
  max_m: number | null;
  mean_m: number | null;
  median_m: number | null;
  p05_m: number | null;
  p95_m: number | null;
  valid_count: number;
  nodata_count: number;
};

type PlanetaryDimensionSupport = {
  dimension_code: "D01" | "D02" | "D03" | "D04" | "D05" | "D06";
  status:
    | "present"
    | "present_evidence"
    | "evidence_only"
    | "no_coverage"
    | "not_indexed"
    | "unsupported_for_on_demand_analysis"
    | "not_applicable";
  reason: string | null; // required for every non-present status
  quality?: string | null;
  method_version?: string | null;
  source_method_version?: string | null;
  support?: string | null;
  feature_ids?: string[];
  observation_count?: number;
  cell_count?: number;
  value?: number | null; // null means "not measured", never 0
  stored_evidence_only?: boolean;
  stored_evidence_note?: string | null;
  comparability?: "comparable" | "evidence_only" | "not_comparable" | "unsupported";
  uncertainty?: Record<string, unknown> | null;
};

type PlanetarySource = {
  dataset_id: string;
  provider: string;
  product_id: string;
  title?: string | null;
  url: string;
  license: string;
  license_url: string;
  source_kind: string;
  source_crs: string | null;
  native_resolution_m: number | null;
  checksum?: string | null;
  version?: string | null;
  accessed_at?: string | null;
  vertical_reference?: string | null;
};

type PlanetaryProviderStatus = {
  status: string; // "ok" | "verified" | "verified_committed_fixture" | "partial" | "failed" | ...
  reason?: string | null;
  network_used?: boolean;
  provider?: string;
  source_id?: string;
  cells?: number;
  fixture_checksums_verified?: boolean;
  native_resolution_m?: number | null;
  source_read_bytes_upper_bound?: number | null;
  url?: string;
  license?: string;
  license_url?: string;
};

type PlanetaryAoiResultV170 = {
  schema_version: string;
  area_id: string;
  location_id: string;
  body: "Moon" | "Mars";
  coordinate_frame: string; // "lunar_planetocentric" | "mars_planetocentric"
  requested_bbox: PlanetaryAoiBBox;
  analysis_bbox: PlanetaryAoiBBox; // actual analysed envelope, not the requested box
  analysis_geometry: Record<string, unknown>; // GeoJSON Polygon/MultiPolygon, body frame
  analysis_kind: PlanetaryAnalysisKind;
  coverage_status: PlanetaryAoiCoverageStatus;
  coverage: PlanetaryAoiCoverage;
  elevation: PlanetaryElevationStats | null;
  feature_count: number;
  context_feature_count: number;
  features: Record<string, number>; // D01 numeric map, current ids preserved
  feature_units: Record<string, string>; // "degree" | "fraction" | ...
  dimension_support: Record<string, PlanetaryDimensionSupport>;
  provider_status: Record<string, PlanetaryProviderStatus>;
  sources: PlanetarySource[];
  quality_flags: string[];
  warnings: string[];
  checksum: string;
  data_version: string;
  method_version: string;
  source_method_version: string;
  area_km2: number;
  analysis_area_km2: number | null;
};
```

Proposed named response models (coordinator-owned):

- `PlanetaryAoiAnalyzeResponse` extends the current `AoiAnalyzeResponse` with
  `result: PlanetaryAoiResultV170 | null` and
  `dimension_support: Record<string, PlanetaryDimensionSupport>`.
- `PlanetaryJobOut` extends `JobOut` with `result: PlanetaryAoiResultV170 | null`.
- The shared `JobOut` and Earth `AoiAnalyzeResponse` stay unchanged so Earth clients do not break.

Frontend behavior:

- Render `analysis_bbox`, not the requested bbox, when they differ. Explain the difference with
  `coverage_note` / `warnings`.
- Switch on `coverage_status` and `analysis_kind`; never on `feature_id` prefixes.
- `elevation` is a summary of the source pixels. Show the unit and vertical reference; do not call
  it “elevation above sea level” (there is no Earth sea level).
- `valid_fraction`/pixel counts describe the D01 computation. If `partial`, show the valid fraction
  and the missing/nodata reason.
- `checksum` is provenance, not a user-facing score.
- Do not sum D01–D06, and do not present `area_km2` as a scientific quality measure.

### 3.3 TARGET body coverage endpoint

The frontend needs to know *before* submitting whether a body/region can be analyzed and at what
resolution. Proposed:

```http
GET /api/v1/planetary/coverage?body=Moon|Mars
```

```ts
type PlanetaryCoverageRegion = {
  region_id: string;
  label: string | null;
  source_id: string;
  dataset_id: string;
  provider: string;
  analysis_eligible: boolean;
  support_status: "analysis_eligible" | "evidence_only" | "not_comparable" | string;
  bbox: PlanetaryAoiBBox;
  source_resolution_m: number | null;
  analysis_resolution_m: number | null;
  cell_count: number;
  cell_ids?: string[];
  notes?: string[];
};

type PlanetaryCoverageResponse = {
  contract_version: string;
  data_version: string;
  body: PlanetaryBodyOut;
  coordinate_frame: string;
  d01: {
    status: "present" | "partial" | "no_coverage" | "not_indexed" | "unsupported";
    coverage_label: string;
    global_coverage_claimed: false;
    source_ids: string[];
    default_source_resolution_m: number | null;
    analysis_resolution_m: number;
    max_side_m: number;
    max_area_km2: number;
    regions: PlanetaryCoverageRegion[];
    notes: string[];
  };
  dimensions: Record<string, PlanetaryDimensionSupport>;
};
```

Suggested UI use:

- Draw `analysis_eligible` regions as a “supported analysis” layer, and `evidence_only` regions as
  a separate evidence layer. Do not blend them.
- Show `global_coverage_claimed=false` and the actual region count; do not label the map “global
  D01”.
- If `d01.status` is `no_coverage`/`not_indexed`, the bbox action should explain that before the
  user submits (and still allow an explicit submit if the server supports a live read).
- The endpoint can be backed by the existing engine `planetary_body_coverage()` function; this is a
  read-only inventory, not a new global download.

### 3.4 TARGET overlay query and feature additions

Proposed additive query fields on `GET /api/v1/planetary/map-features`:

```ts
type PlanetaryMapFeaturesQueryV170 = {
  body: string;
  bbox?: string;
  bbox_lon_convention?: "signed" | "360";
  feature_type?: "all" | "reference" | "typed_area" | "on_demand_aoi";
  data_level?:
    | "all"
    | "reference_90m"
    | "coarse_250m"
    | "on_demand_250m"
    | "global_d01"
    | "evidence";
  availability?: "any" | "present" | "absent" | "no_coverage" | "not_indexed";
  dimension?: string;
  limit?: number;
  offset?: number;
};
```

Proposed additive feature fields (all optional):

```ts
type PlanetaryFeatureV170 = PlanetaryFeatureOut & {
  data_level?: "reference_90m" | "coarse_250m" | "on_demand_250m" | "global_d01" | "evidence";
  analysis_kind?: PlanetaryAnalysisKind;
  coverage_status?: PlanetaryAoiCoverageStatus;
  latest_job_id?: string | null;
  requested_bbox?: PlanetaryAoiBBox | null;
  analysis_bbox?: PlanetaryAoiBBox | null;
  analysis_resolution_m?: number | null;
  source_resolution_m?: number | null;
  valid_fraction?: number | null;
};
```

Current behavior to preserve: reference and typed-area records are exact-ID linked; there is no
proximity join. An empty bbox result is `total=0, features=[]`, not an error and not a placeholder.

### 3.5 TARGET six-dimension observations (D02–D06)

For #119, the authoritative per-observation shape remains the typed-v2 response in §2.4. The #170
target adds real D02–D06 records only where an official product footprint/selector is available;
it never copies an observation from another AOI and never turns a model proxy into a measurement.

Required additional fields/states for #119:

```ts
type PlanetaryObservationV170 = TypedObservation & {
  /** exact value origin: "observed" | "derived" | "model" | "evidence" | "stored_evidence" */
  value_origin?: string | null;
  /** time selector for D03/D05: ISO-8601 or local solar time; null when static */
  temporal_support: {
    kind?: string;
    start?: string | null;
    end?: string | null;
    local_solar_time?: string | null;
    solar_longitude_deg?: number | null;
    notes?: string | null;
    [key: string]: unknown;
  };
  /** depth interval for D04/D06; null means no declared depth, not zero */
  depth_support: {
    kind?: string;
    min_m?: number | null;
    max_m?: number | null;
    [key: string]: unknown;
  };
  uncertainty?: {
    kind?: string;
    value?: number | null;
    lower?: number | null;
    upper?: number | null;
    unit?: string | null;
    [key: string]: unknown;
  } | null;
  /** "similarity" | "hard_filter" | "evidence" | "uncertainty" | "context" | "not_comparable" */
  score_role: string;
  /** live registry decision; absent means not scoreable */
  legal_roles?: string[];
  comparability?: "comparable" | "evidence_only" | "not_comparable" | "unsupported";
};
```

#119 rules:

- Build controls from `GET /api/v1/search/features`, not from a hard-coded six-card layout. Only
  `roles.similarity.supported` gets a target/weight; only `roles.hard_filter.supported` gets a
  range/category control; everything else is evidence/context/unsupported with the returned reason.
- A planetary D01 AOI is evidence of terrain shape. It does not become an Earth score merely
  because the feature id is the same. Respect `comparability_gate` / `cross_body_gate`.
- D02–D06 on Moon/Mars remain evidence-only/`no_coverage` unless a real observation exists.
  `no_coverage` is not zero, and “not indexed” is not “absent”.
- ESA WorldCover remains an Earth-only hard filter / context layer, never D07 and never a
  planetary value.
- There is no cross-body six-dimension total score. Do not display one.

### 3.6 TARGET delta for the #160 `/coverage/aoi` contract

`docs/FRONTEND_HANDOFF_160.md` (branch `feat/160-sixdim-aoi`) defines the #119 AOI-first query.
When both branches merge, #170 expects these additive changes:

- `actual_indexed_coverage` gains a planetary D01 block:
  `{status, coverage_label, region_ids, analysis_eligible, global_coverage_claimed: false}`.
- `dimensions.D01` gains `analysis_capability: "on_demand" | "stored_only" | "none"`,
  `source_resolution_m`, `analysis_resolution_m`, `coverage_scope` and `region_ids`.
- `missing_reasons` accepts `no_coverage`, `not_indexed`, `unsupported`, `out_of_dem_extent` and
  `source_unavailable`; every reason has a human-readable string.
- `/coverage/aoi` must never present a basemap extent as D01 coverage. For an uncovered Moon/Mars
  bbox it returns explicit statuses, not zeros.

## 4. States and errors

### 4.1 Job and coverage states

| State | Where | UI behavior |
|---|---|---|
| `queued` | `status` | Spinner + selected bbox; map stays interactive. No metric placeholders. |
| `running` | `status` | Same; optionally show `provider_status`. |
| `completed` | `status` | Render D01 values only after checking `coverage_status`. |
| `partial` | `status` | Render real values + visible source warning; never hide. |
| `failed` | `status` | Render `error.code`/`error.message`; no values; offer retry with the same idempotency key. |
| `complete` | `coverage_status` | Normal result; show method, support, resolution, source. |
| `partial` | `coverage_status` | Show valid fraction, nodata/invalid counts and the partial reason. |
| `no_coverage` | `coverage_status` | “No verified D01 source at this location”; offer another bbox. |
| `not_indexed` | `coverage_status` | “Source exists but this location is not indexed”; do not imply no data exists anywhere. |
| `unsupported` | `coverage_status` | The body/dimension is not supported on demand; show the reason. |
| `present` | `dimension_support` | Real value/evidence for this exact AOI. |
| `present_evidence` / `evidence_only` | `dimension_support` | Evidence card; no weight/filter/score control unless the live registry says otherwise. |
| `no_coverage` | `dimension_support` | Explicit missing state; never zero. |
| `not_indexed` | `dimension_support` | Source not indexed at this location; never zero. |
| `unsupported_for_on_demand_analysis` | `dimension_support` | “Unsupported on demand — no verified source”; show `reason`/`stored_evidence_note`. |

### 4.2 Error envelope

Current shared shape:

```ts
type ApiErrorBody = {
  error: {
    code: string;
    message: string;
    details: Array<Record<string, unknown>>;
  };
};
```

Current planetary error codes and statuses (observed/mapped by the API adapter):

| Code | HTTP | Meaning / UI |
|---|---:|---|
| `validation_error` | 422 | FastAPI/Pydantic request error; details carry field locations. |
| `unsupported_body` | 422 | Body is not Moon/Mars. |
| `unsupported_feature_on_body` | 422 | Requested feature is not legal for this body. |
| `invalid_bbox` | 422 | Invalid order/values; bbox is planetocentric. |
| `invalid_bbox_latitude` / `invalid_latitude` | 422 | Latitude outside `[-90, 90]`. |
| `invalid_bbox_longitude` / `invalid_longitude` | 422 | Longitude outside the declared domain. |
| `invalid_dimension` | 422 | Dimension filter is not D01–D06. |
| `bbox_too_wide` | 422 | Longitude span >180°; split the request. |
| `aoi_too_small` | 422 | AOI sides below the minimum. |
| `aoi_too_large` | 422 | AOI sides above `AOI_MAX_SIDE_M` (currently 12,000 m). |
| `out_of_coverage` | 422 | No verified source footprint for this AOI. Can also appear as a failed job. |
| `index_missing` / `index_corrupt` | 422 currently | Server coverage index problem; #170 should map storage faults to 503. |
| `manifest_missing` / `manifest_corrupt` | 422 currently | Committed source metadata problem; #170 should map to 503. |
| `fixture_missing` / `fixture_checksum_mismatch` / `fixture_size_mismatch` | 422 currently | Committed fixture problem; #170 should map to 503, never a silent empty result. |
| `live_read_failed` | 422 currently | Bounded network read failed; show provider reason and allow retry. |
| `not_found` | 404 | Unknown area/job/reference. |
| `service_unavailable` | 503 | Provider/runner not configured. |
| `planetary_source_missing` / `planetary_analysis_invalid` | 503 | Result could not be attributed/persisted. |
| `no_supported_features` | 422 | The AOI produced none of the requested D01 features. |
| `analysis_failed` | job error | Worker fallback; inspect logs/provider status. |

TARGET codes to add/handle explicitly:

| Code | HTTP | Meaning / UI |
|---|---:|---|
| `unsupported_dimension` | 422 | Requested dimension has no on-demand contract for this body. |
| `live_read_disabled` | 422 | `source_policy` asked for a live read but the deployment disables network reads. |
| `quota_exceeded` | 422/429 | Pixel/byte/area quota exceeded; show the limit and ask for a smaller AOI. |
| `rate_limited` | 429 | Back off with `Retry-After`; do not retry in a tight loop. |
| `no_data` | job error/coverage | Source footprint exists but contains no valid pixels. Never zero-fill. |
| `source_unavailable` | 503/job error | Official provider unavailable; record per-provider status. |
| `timeout` | job error | Bounded read/job timed out; retry once with backoff, then show failure. |
| `not_indexed` | coverage/dimension | Explicit “source exists, location not indexed”. |

Frontend must never treat an unknown error code as success, and must never render `0`, `null` or an
empty string as a scientific value. Show the `error.message` plus a stable UI state keyed by
`error.code`.

## 5. UI behavior requirements for #119 / #123

1. **One workflow, separate frames.** Keep the Earth map and planetary map components separate.
   Never pass Moon/Mars lon/lat to an Earth `EPSG:4326` endpoint or basemap.
2. **Body and frame first.** When the user switches Earth/Moon/Mars, load
   `/planetary/bodies`/`/planetary/maps` and show the declared frame. The API’s planetary
   longitudes are signed `[-180, 180)`; use `bbox_lon_convention=360` only when the control stores
   0–360 values.
3. **Antimeridian.** If `west > east`, draw two rectangles. Use `crosses_antimeridian` and
   `longitude_span_deg`; do not sort or clamp.
4. **Poles.** Near-polar bboxes may span all longitudes; show the returned envelope and warnings.
   Do not assume a rectangular Earth map projection.
5. **Actual footprint.** Draw `analysis_bbox`/`analysis_geometry` from the response, not the
   requested rectangle, and explain snapping or partial coverage.
6. **No fake zeros.** `no_coverage`, `not_indexed`, `unsupported` and `failed` are distinct visual
   states. A missing D02–D06 value is never rendered as `0`.
7. **Evidence vs score.** Evidence-only/context records get an evidence card and source link, not a
   weight slider. Only live-registry scoreable features get target/weight controls.
8. **Comparability.** Show the gate label/reason when a planetary AOI is used as a search target.
   Do not display a combined Moon/Mars/Earth ranking.
9. **Loading and cancellation.** Debounce map moves (~250–400 ms), pass `AbortSignal`, and cancel
   the previous overlay/AOI request when the body or bbox changes.
10. **Accessibility and locale.** Provide keyboard/pointer equivalents for bbox drawing, visible
    focus, screen-reader labels for map/selection, and zh-TW/en copy for every state. The API’s
    scientific labels are English; translate the UI explanation, not the source values.
11. **Provenance.** Show `dataset_id`/`provider`, `method_version`, `source_method_version`,
    `native_resolution_m`, `analysis_resolution_m`, `valid_fraction`, `data_version` and the
    licence link. Do not hide a partial or live read behind a “completed” badge.

## 6. Contract maintenance and CI

The repository already has the OpenAPI → TypeScript consistency chain. Do not add a duplicate
check; use these existing commands:

```bash
# Backend schema -> committed OpenAPI snapshot
uv run python scripts/export_openapi.py
uv run pytest apps/api/tests/test_contract.py -q

# Committed OpenAPI -> generated TypeScript
cd apps/web && npm run generate:api
cd apps/web && npm run check:api
```

- `scripts/export_openapi.py` writes `contracts/openapi.json`; `test_contract.py` fails if the
  committed snapshot is stale.
- `apps/web/scripts/check-api.mjs` regenerates `src/api/schema.d.ts` in a temp directory and fails
  with the first differing line if the committed file is stale. It is wired as
  `npm run check:api` in `scripts/verify.sh` and in `.github/workflows/ci.yml`.
- Run the full gate only after the schema changes settle: `bash scripts/verify.sh --ci`.

### 6.1 Backend checklist (for Goals A/B/C/D/E/F/G + coordinator)

- [ ] Add the named Pydantic response models for `PlanetaryAoiResultV170`,
  `PlanetaryAoiAnalyzeResponse`, `PlanetaryJobOut`, `PlanetaryDimensionSupport`,
  `PlanetaryAoiCoverage`, `PlanetaryElevationStats`, `PlanetarySource`, `PlanetaryProviderStatus`.
- [ ] Add `bbox_lon_convention` and `source_policy` to the planetary analyze request, or document
  the chosen equivalents and update this file.
- [ ] Add `GET /api/v1/planetary/coverage?body=...` backed by `planetary_body_coverage()`.
- [ ] Add the target overlay query fields/feature fields, or state that the frontend must use
  typed-area detail only.
- [ ] Keep D01 numeric `features` ids backward-compatible; add D02–D06 as typed observations.
- [ ] Map storage/source faults to 503 and provider/timeout/quota failures to explicit job errors.
- [ ] Ensure `method_version` / `source_method_version` in stored typed observations are planetary
  method versions, not Earth coarse versions (see blocker in §8).
- [ ] Regenerate `contracts/openapi.json`, run `npm run generate:api`, commit both.
- [ ] Add offline tests for Moon 3 + Mars 3 dispersed AOIs, no-data, source failure, oversized AOI,
  pole and antimeridian. No network in CI.
- [ ] Do not add a global coverage claim, a cross-body total, or synthetic D02–D06 values.

### 6.2 Frontend checklist (for #119/#123)

- [ ] Use generated `components["schemas"]` types; do not hand-copy `result` as `any`.
- [ ] Add planetary client functions; keep `analyzeArea()` Earth-only.
- [ ] Implement the state matrix in §4, including `partial`, `no_coverage`, `not_indexed`,
  `unsupported`, `live_read_disabled`, `quota_exceeded` and `rate_limited`.
- [ ] Keep Earth and planetary map/detail components separate.
- [ ] Use the live feature registry for controls, not a hard-coded six-dimension form.
- [ ] Add E2E coverage for a real Moon AOI, a real Mars AOI, an uncovered bbox, an oversized bbox,
  a polar/antimeridian bbox and a provider failure.
- [ ] Capture desktop/mobile screenshots against the real API; no mock-only acceptance.

## 7. Acceptance checklist for #170 M4

- [ ] `contracts/openapi.json` and `apps/web/src/api/schema.d.ts` are regenerated and committed.
- [ ] `bash scripts/verify.sh --ci` passes (or the exact failing step is reported).
- [ ] At least 3 Moon + 3 Mars dispersed real AOIs return real elevation/D01 values with source
  checksum/version and valid fraction.
- [ ] Negative cases: uncovered AOI, nodata, source failure/timeout, oversized AOI, polar AOI,
  antimeridian bbox. Each is explicit; none is zero-filled.
- [ ] Real API response captured for the handoff; values are labelled real, not mocked.
- [ ] No global coverage claim; no cross-body total score; WorldCover remains Earth-only.
- [ ] PR opened without auto-merge or self-approval.

## 8. Known gaps and blockers observed on this branch

1. **A/B arbitrary-AOI integration is pending.** Goals A (Moon global LOLA) and B (Mars global
   MOLA) have their own branches with engine/adapter work, but that work is not merged into this
   H branch. On this branch, only the committed Moon LOLA and Mars Jezero CTX pilots are
   analysis-eligible. Mid/high-latitude and other uncovered locations return explicit
   `out_of_coverage`/failed outcomes, never fabricated values. The TARGET sections are therefore a
   request for the merged #170 surface, not a description of current global behavior.
2. **`AoiAnalyzeResponse.result` is untyped in OpenAPI** (`additionalProperties: true`). The
   frontend cannot rely on generated field names until Goal D adds a named result schema.
3. **Typed observation `method_version` inconsistency.** A stored planetary AOI currently writes
   `coarse-terrain-250m-1.0.0` (the Earth coarse method) on its D01 observations, while the analyze
   result says `planetary-terrain-horn-250m-aoi-1.0.0` and the source method is
   `coarse-planetary-dem-horn-1.0.0`. This is visible in `GET /api/v1/typed/areas/{area_id}`.
   Before #119 treats these observations as evidence, Goal D/coordinator must align the stored
   `method_version` (or explicitly document the alias). Do not silently rename it in the UI.
4. **No arbitrary global D01 yet.** Current default analysis only reads the committed Moon LOLA
   pilot and the Mars Jezero CTX cells. Mars outside Jezero and Moon outside the pilot return
   `out_of_coverage` unless the bounded live Moon path is enabled. Do not present the current
   capability as global.
5. **No body coverage endpoint.** `planetary_body_coverage()` exists in the engine but is not
   exposed; #123 cannot draw a supported-analysis layer until it is.
6. **No `bbox_lon_convention` on the planetary analyze request.** Only `/planetary/map-features`
   accepts 360° input today.
7. **Storage faults currently map to 422.** `index_missing`, `index_corrupt`, `manifest_missing`,
   `manifest_corrupt`, `fixture_missing` and checksum errors should be 503 (server/data fault) so
   the UI can distinguish “this AOI has no data” from “the server cannot read its data”.
8. **`/api/v1/coverage/aoi` is not on this branch.** It exists on `feat/160-sixdim-aoi`; #119 must
   not be built against a half-merged contract. Coordinate merge order with #160.
9. **No D02–D06 on-demand values yet.** The current engine returns explicit
   `unsupported_for_on_demand_analysis` with `value: null`. Goals E/F may add real evidence only
   where an official footprint exists; until then, the UI must show unsupported/evidence-only.
10. **D01 planetary values are evidence-only for comparison.** No same-method/support comparison
    with Earth is validated. Do not use them in Earth ranking unless Goal G’s gate returns an
    explicit eligible decision.
11. **Failed-job typed-area link (fixed in this integration).** The analyze response previously
    advertised `links.typed_area` for a failed out-of-coverage job whose area was never persisted,
    producing a guaranteed 404. The route now advertises it only for `completed`/`partial`; the
    regression test is in the HTTP gate. A client that still constructs the URL from a failed
    job's `area_id` must check the detail route instead.

## Appendix A — current planetary map-features type (trimmed)

```ts
type PlanetaryBBoxOut = {
  west: number;
  south: number;
  east: number;
  north: number;
  crosses_antimeridian: boolean;
  longitude_span_deg: number;
  source: string;
};

type PlanetaryScienceCoverageOut = {
  d01_feature_count: number;
  d01_method_versions?: string[];
  d01_analysis_resolution_m?: number | null;
  d01_window_size_m?: number | null;
  typed_dimensions?: string[];
  typed_observation_count: number;
  typed_state_counts?: Record<string, number>;
  typed_feature_ids?: string[];
  value_origins?: string[];
  dimension_status: Record<string, {
    status: string;
    record_count: number;
    value_origin: string | null;
    comparability_gate: string;
  }>;
  coverage_label: string;
  [key: string]: unknown;
};

type PlanetaryFeatureOut = {
  feature_id: string;
  feature_type: "planetary_reference" | "typed_area";
  body: "Moon" | "Mars";
  display_name: string;
  reference_id: string | null;
  typed_area_id: string | null;
  linked_feature_id: string | null;
  relationship: string;
  bbox: PlanetaryBBoxOut;
  center: { lon: number; lat: number };
  geometry: Record<string, unknown>;
  bbox_geometry: Record<string, unknown>;
  science_coverage: PlanetaryScienceCoverageOut;
  availability: string;
  quality_flags: string[];
  crs: string | null;
  coordinate_frame?: string | null;
  data_version: string | null;
  detail_links: Record<string, string | null>;
};

type PlanetaryMapFeaturesOut = {
  catalog_version: string;
  coordinate_contract_version: string;
  data_version: string;
  body: PlanetaryBodyOut;
  bbox: PlanetaryBBoxOut;
  bbox_crs: string;
  coordinate_system: string;
  query: Record<string, unknown>;
  total: number;
  returned: number;
  offset: number;
  limit: number;
  next_offset: number | null;
  unlocatable_reference_count: number;
  unlocatable_typed_area_count: number;
  features: PlanetaryFeatureOut[];
  science_coverage_summary: Record<string, unknown>;
  notes: string[];
};
```

## Appendix B — commands used to verify this handoff

```bash
# No network, isolated SQLite + committed manifests.
uv run pytest apps/api/tests/test_issue132_acceptance.py -q
uv run pytest apps/api/tests/test_planetary_api.py -q
uv run pytest apps/api/tests/test_contract.py -q

# Goal H gates: canonical in-process + complementary live-HTTP
bash scripts/accept_planetary_aoi_170.sh
bash scripts/accept_planetary_aoi_http_170.sh
uv run pytest apps/api/tests/test_planetary_aoi_e2e_170.py \
  apps/api/tests/test_planetary_aoi_http_e2e_170.py -q

# OpenAPI/TypeScript consistency
uv run python scripts/export_openapi.py
cd apps/web && npm run check:api

# Full gate after the schema changes settle
bash scripts/verify.sh --ci
```

If a command is unavailable in the environment, report that exact command and error; do not claim
the contract was verified.

## Appendix C — Goal H integration QA summary (H1, integrated)

This appendix preserves the H1 per-case feasibility proof and the canonical/HTTP gate split.
It is consistent with the CURRENT/TARGET contract above; where it is shorter, the sections
above are authoritative for #119/#123.


Audience: #119/#123 frontend collaborators. **Integration/handoff only; no UI code changed.**  
Owned deliverables: `apps/api/tests/test_planetary_aoi_e2e_170.py`, `scripts/accept_planetary_aoi_170.sh`, `docs/FRONTEND_HANDOFF_170.md`.

### Scope, compatibility, and honesty limits
- **Contracts:** compatible with #132/#160 planetary/Earth AOI, jobs, typed areas, and map-features surfaces used by this integration (`/api/v1/planetary/areas/analyze`, `/api/v1/jobs/{job_id}`, `/api/v1/typed/areas/{area_id}`, `/api/v1/planetary/map-features`, `/api/v1/earth/map-features`). Source of truth: `apps/api/src/analog_api/schemas.py`, `contracts/openapi.json`, `apps/web/src/api/schema.d.ts`.
- **Evidence source:** committed manifests/fixtures and isolated temporary SQLite DBs only; offline (`ANALOG_PLANETARY_ALLOW_NETWORK=false`), no R2/shared DB/raw raster downloads.
- **Not global/exhaustive:** bounded pilot coverage only. **No claim of global or exhaustive six-dimension coverage.**
- **Planetary on-demand science scope:** only verified D01 coarse terrain recomputation (`coarse_slope_mean_deg`, `coarse_slope_p90_deg`, `coarse_flat_fraction_lt5deg`). D02–D06 are explicit `unsupported_for_on_demand_analysis` with `value: null`/reason — never measured, never zero-filled, never scored. No cross-body score.

### Committed pilot coverage and per-case feasibility proof (real offline sources)
| Body/source | Committed manifest/index region | Cells / bbox (lon,lat) | Lat band | Successful real D01 analysis possible offline |
|---|---|---|---|---|
| Moon (analysis-eligible) | `pipelines/planetary/coarse/moon_lola128_window01.json` (`moon-lola128-eqc-window-01`, `moon-lola128-pa-pixel-202405`, fixtures `samples/planetary/coarse/fixtures/moon_*.tif`) | 4 cells, union ~19.833–20.168E, -10.165 to -9.835S | **Low latitude (~10S)** | **Yes**, within committed bbox/cell union only |
| Mars (analysis-eligible, comparable CTX) | `pipelines/planetary/coarse/mars_ctx_jezero_window01.json` (`mars-ctx-jezero-window-01`, `mars2020-jezero-ctx-dem-20m`, fixtures `samples/planetary/coarse/fixtures/mars_*.tif`) | 4 cells, ~78.082–78.260E, 18.473–18.642N | **Low/mid latitude (~18.5N)** | **Yes**, within committed bbox/cell union only |
| Mars (evidence-only, not comparable) | `pipelines/planetary/coarse/mars_mola_meg128_evidence.json` (`mars-mola-meg128-evidence-01`) | 2 cells, same Jezero area (~18.47–18.64N), 463m native support | Low/mid (~18.5N) | **No** — evidence-only, excluded from on-demand comparable D01 analysis |

**Proof of unavailability:** committed `pipelines/planetary/coarse/` contains only `index.json`, `moon_lola128_window01.json`, `mars_ctx_jezero_window01.json`, and `mars_mola_meg128_evidence.json` (no other Moon/Mars coarse pilot manifests/cells/fixtures). Therefore there are no committed analysis-eligible Moon mid/high-lat, Mars low-lat-outside-pilot/high-lat, polar, dateline-crossing-in-coverage, or in-coverage nodata-only regions for successful real offline analysis. **Per-case:** the only successful real on-demand analyses possible from committed offline sources are the 6 committed-pilot AOIs (Moon: all low-lat ~10S; Mars: all low/mid-lat ~18.5N). All other required cases are mapped to **explicit, non-fabricated outcomes** (rejection or failed job/error, `result` empty/null, no fabricated values, no persisted fake typed areas). This does not claim global or exhaustive six-dimension coverage.

### Required-case evidence mapping (Goal H)

| Required case | Body | Reproducible evidence (test/script) | Outcome |
|---|---|---|---|
| Moon real committed AOI 1 (SW cell) | Moon | `test_moon_real_aoi_e2e_three_committed_cases[moon_committed_real_1]` | **Success** (completed/partial), real D01 features from committed fixture, low-lat pilot |
| Moon real committed AOI 2 (NE cell) | Moon | `... [moon_committed_real_2]` | **Success**, low-lat pilot |
| Moon real committed AOI 3 (full block) | Moon | `... [moon_committed_real_3]` | **Success**, low-lat pilot |
| Mars real committed AOI 1 (SW cell) | Mars | `test_mars_real_aoi_e2e_three_committed_cases[mars_committed_real_1]` | **Success**, low/mid-lat pilot |
| Mars real committed AOI 2 (NE cell) | Mars | `... [mars_committed_real_2]` | **Success**, low/mid-lat pilot |
| Mars real committed AOI 3 (full block) | Mars | `... [mars_committed_real_3]` | **Success**, low/mid-lat pilot |
| Nodata / out-of-coverage | Moon, Mars | `test_planetary_required_nodata_and_lat_band_explicit_non_fabricated[moon_nodata_out_of_coverage|mars_nodata_out_of_coverage]` | **Explicit non-fabricated failure** (422/503 or failed job with coverage reason), `result` empty/null, no persisted fake typed area. No committed in-coverage nodata-only AOI exists for successful real analysis. |
| Low/mid/high latitude coverage requests | Moon | `... [moon_midlat_outside_committed]` (45N), `... [moon_highlat_outside_committed]` (60N) — committed Moon coverage is low-lat only | **Explicit non-fabricated failure** (outside committed pilot). Successful real Moon mid/high-lat analysis not possible from committed offline sources. |
| Low/mid/high latitude coverage requests | Mars | `... [mars_lowlat_outside_committed]` (near equator, outside pilot), `... [mars_highlat_outside_committed]` (60N) — committed Mars coverage is low/mid-lat only | **Explicit non-fabricated failure** (outside committed pilot). Successful real Mars low-lat-outside-pilot/high-lat analysis not possible from committed offline sources. |
| Polar (極區) | Moon, Mars | `test_planetary_required_polar_explicit_non_fabricated[moon_polar|mars_polar]` | **Explicit non-fabricated outcome** (not silently accepted as data). No committed polar pilot coverage exists for successful real analysis. |
| Dateline/antimeridian crossing (換日線) | Moon, Mars | `test_planetary_required_dateline_crossing_explicit_non_fabricated[moon_dateline_crossing|mars_dateline_crossing]` | **Explicit non-fabricated outcome** (planetocentric crossing semantics; no silent acceptance/fabrication). No committed crossing pilot coverage exists for successful real analysis. |
| Oversized AOI (過大 AOI) | Moon, Mars | `test_planetary_required_oversized_aoi_rejected[Moon|Mars]` | **Explicit 422 `aoi_too_large`** (bounded analysis cap). |
| Source outage (來源斷線) | Moon, Mars | `test_planetary_required_source_outage_explicit[Moon|Mars]` (simulated provider boundary outage, offline; no live network disconnect) | **Explicit failed job** (`source_extraction_failed`, error/provider status), no fake success, no persisted typed area. |
| Cache/resume/idempotency | Moon | `test_planetary_cache_resume_and_idempotency` | Cached repeat by bbox/cache key and idempotency key; async runner recovery (`running -> queued`) completes |
| API/OpenAPI/TS + #132/#160 surface compatibility | - | `test_openapi_and_types_contract_compatibility_170` | Paths/schemas present in runtime+committed OpenAPI; TS markers in `schema.d.ts`; live response shape sanity |

### API request/response/types/error (frontend consumption)

### Request: `POST /api/v1/planetary/areas/analyze` (`PlanetaryAoiAnalyzeRequest`)
```ts
{
  body: "Moon" | "Mars"; // planetocentric frame, never Earth EPSG:4326
  bbox: { west: number; south: number; east: number; north: number };
  requested_features?: Array<"coarse_slope_mean_deg" | "coarse_slope_p90_deg" | "coarse_flat_fraction_lt5deg">;
  idempotency_key?: string; // max 128, reuse per user action/retry
}
```

### Response: `AoiAnalyzeResponse` (analyze) / `JobOut` (`GET /api/v1/jobs/{job_id}`)
- Analyze: `job_id`, `status` (`queued|running|completed|partial|failed`), `area_id`, `cache_key`, `cached`, `provider_status`, `result|null`, `links{job,typed_area?,map_features?}`, `body`, `coordinate_frame`, `method_version`, `coverage_note`, `dimension_support`.
- Job: `job_id`, `status`, `area_id`, `body`, `coordinate_frame`, `provider_status`, `error{code,message}|null`, `result`, timestamps.
- Successful result (D01): `features` with 3 coarse IDs (degree/fraction), `dimension_support.D01` present with method/source method lineage; `D02-D06` unsupported with reason and `value:null`; `analysis_kind` (e.g. `committed_reference_fixture`), sources/warnings/coverage note as provided.
- **Do not** treat `dimension_support` unsupported as zero/measured, do not compute cross-body totals, and keep Earth/planetary frame separation.

### Error/outcome handling
| State | Expected UI/backend behavior |
|---|---|
| `queued/running` (202 or async) | Poll `links.job`/`/api/v1/jobs/{job_id}` with backoff; no invented metrics |
| `completed/partial` | Render only returned values + method/provenance/warnings/coverage note; show partial visibly |
| `failed` | Show `error.code/message` or explicit provider reason; do not open as persisted typed area unless detail confirms; allow retry with same idempotency key |
| 422 `aoi_too_large` | Oversized/bounded-cap rejection |
| 422/503 or failed job (coverage/nodata) | Explicit out-of-coverage/nodata-equivalent; no fabricated observations, no fake persisted typed area |
| 422 `unsupported_body` / `unsupported_feature_on_body` | Invalid request selection |

Error envelope: `{ error: { code, message, details[] } }`.

### CI / perf-resource budget / cache-resume
- CI jobs (`.github/workflows/ci.yml`): backend lint/format/pytest (includes `apps/api/tests`), frontend checks (incl. `npm run check:api`), offline integration smoke. Goal H test is CI-discoverable under `apps/api/tests/`.
- Offline acceptance budget posture: isolated temp SQLite/runtime, bounded committed pilots only, no network/provider downloads, timeout-bounded command execution; script reports temp artifact sizes. This is evidence visibility, not a production/global capacity claim.
- Cache/resume: cache key/idempotency reuse durable job/result; failed cache-path retry semantics in job store; runner recovery requeues interrupted running jobs.

### Reproduction
```bash
bash scripts/accept_planetary_aoi_170.sh
uv run pytest apps/api/tests/test_planetary_aoi_e2e_170.py -q -ra
# OpenAPI drift is verified inline by the acceptance script (no file writes).
```

### Handoff notes (#119/#123)
- Follow `links`/`detail_links`; keep planetary (`lunar_planetocentric`/`mars_planetocentric`) separate from Earth flows.
- Show support/provenance labels (committed fixture, bounded pilot, method/source method) and never relabel unsupported/coarse as fine/global/measured.
- Treat missing/changed fields as contract mismatches; do not silently hide scientific/support meaning.
