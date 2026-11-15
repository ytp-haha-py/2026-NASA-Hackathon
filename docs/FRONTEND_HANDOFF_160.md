# Frontend handoff for Issue #160 / #119 — six-dimension AOI MVP

Status: **backend/contract handoff only. No React/Leaflet UI was implemented in #160.**
This document is the exact request/response/state contract for the #119 frontend work. It is based
on real committed observations and local HTTP responses from this branch, not mock data.

Companion: `docs/SIXDIM_QUERY_ROLES.md` is the live feature-role matrix. This file is the
requested exact #119 contract with line-level D01-only hardcodes, copy, and acceptance assertions;
this file and `scripts/accept_sixdim_aoi.sh` are the executable truth.

## Non-claims and data baseline

- The candidate pool is the curated fine Earth index in the seeded local database: **113 Earth
  candidates**.
- The committed typed-v2 corpus on this branch is **2,152 observations** (this is the observed
  number; the Issue #160 brief’s “2,185” does not match the committed seed and must not be shown as
  a count until the data actually changes).
- References: **18 Moon**, **5 Mars** on this branch. The brief’s “Mars 7” also does not match the
  committed manifests.
- D06 0–5 cm SoilGrids means are gated to **113 / 113** Earth candidates. D06 support is a **250 m
  model prediction aggregated over the AOI envelope**, not a local soil sample.
- Moon/Mars numeric regolith is **explicit `no_coverage`**; it is never zero-filled or inferred
  from Earth, DEM roughness, or WorldCover.
- No global coverage is claimed. Every AOI response includes
  `actual_indexed_coverage.global_coverage_claimed = false`.
- D02–D05 remain evidence/context unless the live registry explicitly says otherwise. No
  six-dimension scientific total exists.

Reproduce all of this with:

```bash
bash scripts/accept_sixdim_aoi.sh
```

The script starts the real API on a temporary, freshly seeded offline database and fails nonzero on
any contract violation.

## Endpoint map for #119

| UI need | Method / path | Notes |
|---|---|---|
| AOI-first six-dimension status + evidence | `GET /api/v1/coverage/aoi` | Primary #160 endpoint. Accepts `body` + either `lat/lon` or `west/south/east/north`. Returns six dimension statuses, intersecting records, source/support, missing reasons. |
| Status only (older contract) | `GET /api/v1/coverage/location` | Same six statuses without the flattened `intersecting_records`/`missing_reasons` block. Keep for compatibility. |
| Typed evidence query | `GET /api/v1/typed/observations` | Facets: `body`, `dimension`, `feature_id`, `area_id`, `state`, `dataset_id`, `source_asset_id`, `min_depth_m`, `max_depth_m`, `temporal_kind`, `west/south/east/north`, `lat/lon`, `envelope=true`. |
| Exact AOI provenance | `GET /api/v1/typed/areas/{area_id}` | Returns the area bbox/CRS plus every observation stored for that exact AOI. Use this for Moon/Mars exact-area evidence. |
| Feature registry / legal roles | `GET /api/v1/search/features` | Filters: `body`, `dimension`, `role`. Use `roles.*.supported` instead of hardcoding D01/D06. |
| D01 + D06 weighted search | `POST /api/v1/search/multidimensional` | The only score endpoint for #119. Returns per-feature and per-dimension contributions. |
| Earth land-cover hard filter | `POST /api/v1/search` (legacy) | ESA WorldCover filter is accepted here only. It is a hard filter, not a weight or dimension. |
| Moon/Mars overlay/AOI maps | `GET /api/v1/planetary/map-features`, `GET /api/v1/planetary/maps` | Planetocentric lon/lat. Never pass these to an Earth WGS84/OSM map without the declared body frame. |

## Exact TypeScript contract

These types mirror the current JSON. The integrator must regenerate
`apps/web/src/api/schema.d.ts` from `contracts/openapi.json`; these definitions are the target
shape for the hand-written client until generation lands.

```ts
export type DimensionCode = "D01" | "D02" | "D03" | "D04" | "D05" | "D06";
export type Body = "Earth" | "Moon" | "Mars";
export type ObservationState =
  | "detected"
  | "not_detected"
  | "unknown"
  | "no_coverage"
  | "not_applicable";

export type RoleSupport = {
  supported: boolean;
  reason: string | null;
  constraints: string[];
};

export type FeatureRoles = {
  similarity: RoleSupport;
  hard_filter: RoleSupport;
  category_match: RoleSupport;
  evidence: RoleSupport;
  uncertainty: RoleSupport;
  context: RoleSupport;
};

export type FeatureRegistryEntry = {
  feature_id: string;
  dimension_id: DimensionCode;
  dimension_label: string;
  label: string;
  unit: string | null;
  value_kind: string;
  body_scope: Body[];
  score_role: string;
  roles: FeatureRoles;
  min_value: number | null;
  max_value: number | null;
  scale: number | null;
  selector: Record<string, unknown>;
  method_versions: string[];
  support: Record<string, unknown>;
  unsupported_reason: string | null;
  measurement_family: string | null;
  coverage: {
    observation_count: number;
    area_count: number;
    by_body: Partial<Record<Body, number>>;
    by_state: Partial<Record<ObservationState, number>>;
    gated_candidate_count?: number;
    gated_candidate_ids?: string[];
    [key: string]: unknown;
  };
};

export type FeatureRegistryResponse = {
  registry_version: string;
  data_version: string;
  features: FeatureRegistryEntry[];
  join_audit: {
    proximity_join_used: false;
    typed_area_count: number;
    typed_areas_exactly_matching_fine_candidate: number;
    typed_areas_exactly_matching_planetary_reference: number;
    [key: string]: unknown;
  };
  audit_summary: Record<string, unknown>;
  notes: string[];
};

export type CoverageRecord = {
  dimension_code: DimensionCode;
  feature_id: string;
  area_id: string;
  observation_id: string;
  status: string;
  state: ObservationState;
  score_role: string;
  legal_roles: string[];
  query_roles: string[];
  value_kind: string;
  value: unknown;
  unit: string | null;
  method_version: string;
  source: {
    dataset_ids: string[];
    source_asset_ids: string[];
    origin: string | null;
  };
  resolution: {
    resolution_m: number | null;
    native_resolution_m: number | null;
    window_size_m: number | null;
    valid_fraction: number | null;
    crs: string | null;
  };
  temporal_support: Record<string, unknown>;
  depth_support: Record<string, unknown>;
  quality_flags: string[];
  uncertainty: Record<string, unknown> | null;
  notes: string | null;
};

export type CoverageDimension = {
  dimension_code: DimensionCode;
  label: string;
  status:
    | "available_or_indexed"
    | "partially_available"
    | "evidence_only"
    | "explicit_no_coverage"
    | "not_indexed_at_location"
    | "not_indexed"
    | "unsupported";
  intersecting_record_count: number;
  states: ObservationState[];
  roles?: Record<string, RoleSupport>;
  features: CoverageRecord[];
  declared_sources: Array<Record<string, unknown>>;
};

export type AoiQueryResponse = {
  contract_version: "aoi-query-1.0.0";
  endpoint: "/api/v1/coverage/aoi";
  query: {
    body: Body;
    lat: number | null;
    lon: number | null;
    bbox: {
      west: number; south: number; east: number; north: number;
      crosses_antimeridian: boolean; longitude_span_deg: number;
      source: string;
    } | null;
    query_kind?: "bbox" | "point";
    bbox_lon_convention?: "signed" | "360";
    bbox_contract?: Record<string, unknown>;
    coordinate_note: string;
  };
  actual_indexed_coverage: {
    indexed_earth_candidate_count: number;
    intersecting_candidate_ids: string[];
    intersecting_reference_ids: string[];
    intersecting_typed_area_ids: string[];
    typed_observation_count_at_location: number;
    global_coverage_claimed: false;
  };
  dimensions: Record<DimensionCode, CoverageDimension>;
  intersecting_records: CoverageRecord[];
  missing_reasons: Record<
    DimensionCode,
    { status: string; reason: string; declared_source_count: number }
  >;
  context_features: Array<Record<string, unknown>>;
  manifest_adapters: Array<Record<string, unknown>>;
  limitations: string[];
  handoff: Record<string, unknown>;
};

export type TypedObservation = {
  observation_id: string;
  area_id: string;
  body: Body;
  dimension_code: DimensionCode | null;
  feature_id: string;
  value_kind: string;
  state: ObservationState;
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

export type TypedObservationEnvelope = {
  contract_version: "typed-observations-query-1.0.0";
  query: Record<string, unknown>;
  count: number;
  status: "ok" | "no_data";
  observations: TypedObservation[];
  notes: string[];
};

export type TypedObservationQuery = {
  dimension?: DimensionCode;
  body?: Body;
  featureId?: string;
  areaId?: string;
  state?: ObservationState;
  datasetId?: string;
  sourceAssetId?: string;
  minDepthM?: number;
  maxDepthM?: number;
  temporalKind?: string;
  west?: number; south?: number; east?: number; north?: number;
  lat?: number; lon?: number;
  envelope?: boolean;
};

export type RangeFilter = { min?: number; max?: number };

export type MultiDimSearchRequest = {
  reference_id?: string | null;
  candidate_body?: Body;
  targets: Record<string, { value: number; weight: number }>;
  filters?: Record<string, RangeFilter>;
  dimension_weights?: Record<
    DimensionCode,
    { weight: number; enabled: boolean }
  >;
  scoring_version?:
    | "feature-weighted-euclidean-v1"
    | "dimension-weighted-rms-v1";
  limit?: number;
  offset?: number;
  include_evidence?: boolean;
  evidence_limit?: number;
};

export type Contribution = {
  feature_id: string;
  dimension_id: DimensionCode;
  label: string;
  unit: string | null;
  target: number;
  candidate: number;
  delta: number;
  scale: number;
  weight: number;
  normalized_delta: number;
  contribution: number;
  contribution_fraction: number;
  measurement_family: string | null;
  observation_id: string;
  selector: Record<string, unknown>;
  support: Record<string, unknown>;
  uncertainty: { q05?: number | null; q95?: number | null; [key: string]: unknown };
};

export type DimensionContribution = {
  contribution: number;
  distance: number;
  dimension_weight: number;
  feature_weight_sum: number;
  mean_squared_normalized_delta: number;
  feature_count: number;
};

export type MultiDimResult = {
  rank: number;
  location_id: string;
  display_name: string;
  body: Body;
  lat: number;
  lon: number;
  bbox: { west: number; south: number; east: number; north: number };
  distance: number;
  distance_scope: string;
  dimension_contributions: Partial<Record<DimensionCode, DimensionContribution>>;
  contributions: Contribution[];
  valid_features: string[];
  data_kind: string;
  quality_flags: string[];
  evidence_records: Array<Record<string, unknown>>;
};

export type MultiDimSearchResponse = {
  registry_version: string;
  data_version: string;
  data_snapshot_id: string;
  scoring: {
    version: string;
    formula: string;
    dimension_controls: Record<string, { weight: number; enabled: boolean; active: boolean }>;
    active_dimensions: DimensionCode[];
    note: string;
  };
  candidate_body: Body;
  source_body: Body;
  cross_body_gate: Record<string, unknown>;
  requested_features: string[];
  requested_targets: Record<string, Record<string, unknown>>;
  filters_applied: Record<string, RangeFilter>;
  candidate_pool_size: number;
  filtered_candidate_count: number;
  eligible_candidate_count: number;
  excluded_candidate_count: number;
  total_matched: number;
  offset: number;
  limit: number;
  returned: number;
  next_offset: number | null;
  coverage: {
    candidate_pool_size: number;
    requested_feature_count: number;
    features: Record<string, Record<string, unknown>>;
  };
  excluded: Array<{ location_id: string; reasons: string[]; missing_features: string[] }>;
  results: MultiDimResult[];
  limitations: string[];
};

export type ApiErrorBody = {
  error: { code: string; message: string; details: Array<Record<string, unknown>> };
};
```

## Concrete requests and observed responses

All examples below are real local responses from `bash scripts/accept_sixdim_aoi.sh`
(data version `dv-b46f3bad38ea`, 113 Earth candidates, 2,152 typed observations). Long arrays are
trimmed and marked `…`.

### 1. Earth AOI: six statuses + evidence

```bash
curl -sS 'http://127.0.0.1:8000/api/v1/coverage/aoi?body=Earth&west=-68.5&south=-23.6&east=-68.3&north=-23.4'
```

```json
{
  "contract_version": "aoi-query-1.0.0",
  "endpoint": "/api/v1/coverage/aoi",
  "query": {
    "body": "Earth",
    "bbox": { "west": -68.5, "south": -23.6, "east": -68.3, "north": -23.4,
      "crosses_antimeridian": false, "longitude_span_deg": 0.2, "source": "query" }
  },
  "actual_indexed_coverage": {
    "indexed_earth_candidate_count": 113,
    "intersecting_candidate_ids": ["earth-atacama-desert"],
    "intersecting_typed_area_ids": ["earth-atacama-desert"],
    "typed_observation_count_at_location": 73,
    "global_coverage_claimed": false
  },
  "dimensions": {
    "D01": { "status": "available_or_indexed", "intersecting_record_count": 5 },
    "D02": { "status": "evidence_only", "intersecting_record_count": 3 },
    "D03": { "status": "evidence_only", "intersecting_record_count": 4 },
    "D04": { "status": "evidence_only", "intersecting_record_count": 4 },
    "D05": { "status": "evidence_only", "intersecting_record_count": 2 },
    "D06": { "status": "available_or_indexed", "intersecting_record_count": 60 }
  },
  "intersecting_records": [
    {
      "dimension_code": "D06",
      "feature_id": "soil_clay_0_5cm_mean",
      "area_id": "earth-atacama-desert",
      "observation_id": "soilgrids:earth-atacama-desert:clay:0-5cm:mean",
      "status": "available",
      "state": "detected",
      "score_role": "evidence_only",
      "legal_roles": ["similarity", "hard_filter", "evidence", "uncertainty"],
      "value": 132.33333333333334,
      "unit": "g kg-1",
      "method_version": "soilgrids-wcs-2.0.0",
      "source": { "dataset_ids": ["soilgrids-250m-v2"], "source_asset_ids": ["soilgrids-wcs:…"] },
      "resolution": { "resolution_m": 250.0, "native_resolution_m": 250.0, "source_resolution_m": 250.0 },
      "depth_support": { "kind": "depth_interval", "min_m": 0.0, "max_m": 0.05 }
    }
  ],
  "missing_reasons": {
    "D02": { "status": "evidence_only", "reason": "see_feature_states", "declared_source_count": 2 }
  },
  "limitations": ["…", "Land-cover context is Earth-only context/filter data, never a similarity dimension."]
}
```

**Frontend rule:** build the six cards from `dimensions`. Do not turn `intersecting_record_count=0`
into `0` values. `not_indexed`, `unsupported`, and `no_coverage` are different explicit states.

### 2. Moon AOI and exact-area evidence

```bash
curl -sS 'http://127.0.0.1:8000/api/v1/coverage/aoi?body=Moon&west=-157.2&south=-89.85&east=-156.2&north=-89.7'
```

Observed statuses:

```json
{
  "D01": "available_or_indexed",
  "D02": "evidence_only",
  "D03": "evidence_only",
  "D04": "evidence_only",
  "D05": "evidence_only",
  "D06": "not_indexed",
  "actual_indexed_coverage": { "global_coverage_claimed": false }
}
```

The AOI endpoint now reports the lunar evidence records that actually intersect the query bbox.
D06 has no intersecting record there; use the exact area ids for the explicit `no_coverage`
audit records:

```bash
curl -sS 'http://127.0.0.1:8000/api/v1/typed/observations?body=Moon&dimension=D02&area_id=moon-shackleton-rim-site04-roi2&envelope=true'
curl -sS 'http://127.0.0.1:8000/api/v1/typed/observations?body=Moon&dimension=D06&state=no_coverage&area_id=moon-apollo15-station8-lsrp&envelope=true'
```

```json
{
  "contract_version": "typed-observations-query-1.0.0",
  "query": { "body": "Moon", "dimension": "D06", "state": "no_coverage",
    "area_id": "moon-apollo15-station8-lsrp" },
  "count": 6,
  "status": "ok",
  "observations": [
    {
      "observation_id": "d06:evidence-audit:moon:moon-apollo15-station8-lsrp:bearing_strength_kpa",
      "area_id": "moon-apollo15-station8-lsrp",
      "body": "Moon",
      "dimension_code": "D06",
      "feature_id": "bearing_strength_kpa",
      "state": "no_coverage",
      "value": null,
      "unit": "kPa",
      "method_version": "d06-regolith-evidence-audit-0.1.0",
      "dataset_ids": ["nasa-pds-apollo-lsrp-hfe-audit"],
      "source_asset_ids": ["official-source-audit:moon-lsrp-hfe"],
      "score_role": "evidence_only"
    }
  ]
}
```

**Frontend rule:** `state=no_coverage` and `value=null` must render as “no coverage”, never as `0`.
Show `dataset_ids`/`source_asset_ids` as provenance.

### 3. Mars AOI and exact-area evidence

```bash
curl -sS 'http://127.0.0.1:8000/api/v1/coverage/aoi?body=Mars&west=76&south=17&east=79&north=20'
```

Observed statuses: `D01=available_or_indexed`, `D02=evidence_only`, `D03=evidence_only`,
`D04=evidence_only`, `D05=evidence_only`, `D06=not_indexed`. Again, exact area ids carry the
D06 `no_coverage` audit evidence:

```bash
curl -sS 'http://127.0.0.1:8000/api/v1/typed/observations?body=Mars&dimension=D02&feature_id=geologic_unit&state=detected&dataset_id=usgs-jezero-sim3464&envelope=true'
curl -sS 'http://127.0.0.1:8000/api/v1/typed/observations?body=Mars&dimension=D03&area_id=thermal-mars-jezero-ctx-window-01&envelope=true'
curl -sS 'http://127.0.0.1:8000/api/v1/typed/observations?body=Mars&dimension=D04&area_id=mars-hend-cell-n20p00-e78p00&envelope=true'
curl -sS 'http://127.0.0.1:8000/api/v1/typed/observations?body=Mars&dimension=D05&area_id=mars-jezero-ctx-window-01&envelope=true'
curl -sS 'http://127.0.0.1:8000/api/v1/typed/observations?body=Mars&dimension=D06&state=no_coverage&area_id=mars-insight-elysium-hp3&envelope=true'
```

Observed D02 envelope:

```json
{
  "contract_version": "typed-observations-query-1.0.0",
  "query": { "body": "Mars", "dimension": "D02", "feature_id": "geologic_unit",
    "state": "detected", "dataset_id": "usgs-jezero-sim3464" },
  "count": 10,
  "status": "ok",
  "observations": [
    {
      "observation_id": "geology:mars-test-jezero-floor:usgs-jezero-sim3464:geologic_unit:mars-map:1000",
      "area_id": "mars-test-jezero-floor",
      "body": "Mars",
      "dimension_code": "D02",
      "feature_id": "geologic_unit",
      "value_kind": "category",
      "state": "detected",
      "value": { "code": "Aeb", "name": "Eolian bedform unit", "scheme": "geology-map-unit" },
      "method_version": "geology-1.0.0",
      "dataset_ids": ["usgs-jezero-sim3464"],
      "source_asset_ids": ["geology-asset:usgs-jezero-sim3464:…"],
      "score_role": "evidence_only"
    }
  ]
}
```

### 4. Feature registry: do not hardcode D01/D06

```bash
curl -sS 'http://127.0.0.1:8000/api/v1/search/features?role=similarity&body=Earth'
curl -sS 'http://127.0.0.1:8000/api/v1/search/features?dimension=D02&role=evidence&body=Mars'
```

Observed D01/D06 similarity entries:

```json
{
  "feature_id": "slope_mean_deg",
  "dimension_id": "D01",
  "body_scope": ["Earth", "Moon", "Mars"],
  "roles": {
    "similarity": { "supported": true, "reason": null },
    "hard_filter": { "supported": true, "reason": null },
    "evidence": { "supported": true, "reason": null }
  }
}
```

```json
{
  "feature_id": "soil_clay_0_5cm_mean",
  "dimension_id": "D06",
  "body_scope": ["Earth"],
  "unit": "g kg-1",
  "roles": {
    "similarity": { "supported": true, "reason": null },
    "hard_filter": { "supported": true, "reason": null },
    "evidence": { "supported": true, "reason": null },
    "uncertainty": { "supported": true, "reason": null }
  },
  "selector": { "kind": "depth_interval", "depth_interval_m": [0.0, 0.05], "statistic": "mean" },
  "coverage": { "gated_candidate_count": 113 }
}
```

Observed D02 evidence-only entry:

```json
{
  "feature_id": "geologic_unit",
  "dimension_id": "D02",
  "body_scope": ["Mars", "Moon"],
  "roles": {
    "similarity": { "supported": false, "reason": "Geologic map-unit names are dataset-specific free text. No versioned taxonomy crosswalk exists…" },
    "hard_filter": { "supported": false, "reason": "…" },
    "evidence": { "supported": true, "reason": "…" }
  },
  "unsupported_reason": "Geologic map-unit names are dataset-specific free text…"
}
```

### 5. D01 + D06 weighted search

```bash
curl -sS 'http://127.0.0.1:8000/api/v1/search/multidimensional' \
  -H 'Content-Type: application/json' \
  -d '{
    "targets": {
      "slope_mean_deg": {"value": 10, "weight": 1},
      "soil_clay_0_5cm_mean": {"value": 200, "weight": 1}
    },
    "limit": 10
  }'
```

Observed top result (trimmed):

```json
{
  "registry_version": "feature-eligibility-1.0.0",
  "scoring": { "version": "feature-weighted-euclidean-v1", "active_dimensions": ["D01", "D06"] },
  "candidate_body": "Earth",
  "eligible_candidate_count": 113,
  "excluded_candidate_count": 0,
  "results": [
    {
      "rank": 1,
      "location_id": "earth-devon-polar-r3c1",
      "body": "Earth",
      "distance": 0.03631569153208877,
      "dimension_contributions": {
        "D01": { "contribution": 0.001214195136845, "feature_count": 1 },
        "D06": { "contribution": 0.000104686065197, "feature_count": 1 }
      },
      "contributions": [
        {
          "feature_id": "soil_clay_0_5cm_mean",
          "dimension_id": "D06",
          "candidate": 202.8939393939394,
          "target": 200.0,
          "scale": 200.0,
          "weight": 1.0,
          "contribution": 0.00010468606519742966,
          "observation_id": "soilgrids:earth-devon-polar-r3c1:clay:0-5cm:mean",
          "support": { "native_resolution_m": 250.0, "kind": "area_envelope_mean" },
          "uncertainty": { "q05": 151.5, "q95": 255.5 }
        }
      ]
    }
  ],
  "limitations": ["This endpoint ranks only the features explicitly requested… It does not create a six-dimension scientific total."]
}
```

D01-only vs D01+D06 observed order change:

```text
D01-only top:  earth-devon-polar-r3c2
D01+D06 top:   earth-devon-polar-r3c1
```

The frontend must render `contributions[]` and `dimension_contributions`; it must not compute or
display a “total six-dimension score”.

Dimension-weighted variant:

```bash
curl -sS 'http://127.0.0.1:8000/api/v1/search/multidimensional' \
  -H 'Content-Type: application/json' \
  -d '{
    "targets": {
      "slope_mean_deg": {"value": 10, "weight": 1},
      "soil_clay_0_5cm_mean": {"value": 200, "weight": 1},
      "soil_sand_0_5cm_mean": {"value": 300, "weight": 1}
    },
    "dimension_weights": {
      "D01": {"weight": 1, "enabled": true},
      "D06": {"weight": 1, "enabled": true}
    },
    "scoring_version": "dimension-weighted-rms-v1",
    "limit": 3
  }'
```

Observed: `scoring.version=dimension-weighted-rms-v1`, `active_dimensions=["D01","D06"]`,
`D01.feature_count=1`, `D06.feature_count=2`, top distance `0.1321317564100876`.

Zero weight is not an exclusion:

```json
{
  "targets": {
    "slope_mean_deg": {"value": 10, "weight": 1},
    "soil_clay_0_5cm_mean": {"value": 200, "weight": 0}
  }
}
```

Observed: `eligible_candidate_count=113`, `excluded_candidate_count=0`,
`coverage.features.soil_clay_0_5cm_mean.comparison="zero_weight_not_compared"`, and the feature is
absent from every `valid_features`.

### 6. Earth-only WorldCover hard filter

```bash
curl -sS 'http://127.0.0.1:8000/api/v1/search' \
  -H 'Content-Type: application/json' \
  -d '{
    "body": "custom",
    "target": {"slope_mean_deg": {"value": 5, "weight": 1}},
    "filters": {"landcover_bare_sparse_fraction": {"max": 0.5}},
    "limit": 10
  }'
```

Observed:

```json
{
  "query_version": "search-1.1.0",
  "filters_applied": {
    "landcover_bare_sparse_fraction": { "min": null, "max": 0.5 }
  },
  "indexed_candidate_count": 113,
  "eligible_candidate_count": 61,
  "excluded_candidate_count": 52,
  "excluded": [
    { "location_id": "earth-atacama-desert",
      "reason": "filter:landcover_bare_sparse_fraction" }
  ]
}
```

`earth-atacama-desert` has `landcover_bare_sparse_fraction=0.994915`, so the inclusive `max=0.5`
hard filter excludes it. Filtering does not change the distance of surviving candidates.

**Documented API gap:** the multidimensional endpoint currently rejects the same filter:

```bash
curl -sS -o /tmp/wc.json -w '%{http_code}\n' \
  'http://127.0.0.1:8000/api/v1/search/multidimensional' \
  -H 'Content-Type: application/json' \
  -d '{"targets":{"slope_mean_deg":{"value":5,"weight":1}},"filters":{"landcover_bare_sparse_fraction":{"max":0.5}}}'
# 422
```

```json
{
  "error": {
    "code": "unknown_feature",
    "message": "Unsupported filter feature id: landcover_bare_sparse_fraction.",
    "details": []
  }
}
```

For #119 this means: keep the land-cover control on the legacy `/search` request (or split the UI
request), and do not send it to `/search/multidimensional` until the backend combines the paths.

### 7. Out-of-coverage bbox

```bash
curl -sS 'http://127.0.0.1:8000/api/v1/coverage/aoi?body=Earth&west=-140.1&south=-0.1&east=-139.9&north=0.1'
```

Observed:

```json
{
  "dimensions": {
    "D01": { "status": "not_indexed_at_location", "features": [] },
    "D02": { "status": "not_indexed", "features": [] },
    "D03": { "status": "not_indexed", "features": [] },
    "D04": { "status": "not_indexed", "features": [] },
    "D05": { "status": "not_indexed", "features": [] },
    "D06": { "status": "not_indexed", "features": [] }
  },
  "actual_indexed_coverage": {
    "intersecting_candidate_ids": [],
    "intersecting_typed_area_ids": [],
    "global_coverage_claimed": false
  },
  "intersecting_records": [],
  "missing_reasons": {
    "D01": { "reason": "no_intersecting_indexed_records", "declared_source_count": 3 }
  }
}
```

Render “no indexed record at this AOI”, not zero values and not “no data on Earth”.

### 8. Error contract

All API errors use:

```json
{ "error": { "code": "…", "message": "…", "details": [ … ] } }
```

| Status | Code | Meaning / UI action |
|---:|---|---|
| 422 | `body_out_of_scope` | Earth-only feature requested for Moon/Mars. Show the reason and body scope; do not disable the whole form. |
| 422 | `feature_role_not_scoreable` | Evidence/context feature used as a similarity target. Show `details[0].legal_roles`; keep it in the evidence panel. |
| 422 | `unknown_feature` | Unknown target/filter id, including land-cover sent to `/search/multidimensional`. |
| 422 | `validation_error` | Invalid bbox/lat/lon/limit. Show field-level details. |
| 404 | `not_found` | Unknown location/area/reference id. |
| 409 | `no_indexed_data` | Empty Earth candidate index; do not show an empty ranking as success. |

Observed cross-body error:

```json
{
  "error": {
    "code": "body_out_of_scope",
    "message": "soil_clay_0_5cm_mean is registered for Earth only; target/source body Moon is incompatible.",
    "details": [{
      "feature_id": "soil_clay_0_5cm_mean",
      "target_body": "Moon",
      "body_scope": ["Earth"],
      "reason": "cross_body_incompatible"
    }]
  }
}
```

Observed role error:

```json
{
  "error": {
    "code": "feature_role_not_scoreable",
    "message": "geologic_unit is not registered for weighted similarity…",
    "details": [{
      "feature_id": "geologic_unit",
      "dimension_id": "D02",
      "score_role": "evidence",
      "legal_roles": ["evidence"]
    }]
  }
}
```

## Current D01-only hardcodes and exact replacements

Line numbers are from this branch at the time of writing; re-check after any frontend edit.

| Location | Current hardcode | Exact replacement |
|---|---|---|
| `apps/web/src/App.tsx:154` | `searchCandidates()` always posts to legacy `/search`. | For D01+D06 controls, call `searchMultidimensional()` (`POST /api/v1/search/multidimensional`). Keep legacy `/search` only while a WorldCover filter is active, or until the backend combines the paths. |
| `apps/web/src/App.tsx:196` | `body: "Earth"` on every selected result. | Use the API value: `body: selectedResult.body ?? searchResponse.candidate_body`. Multidim results now include `body`; legacy results are Earth-only but should not be hardcoded for future bodies. |
| `apps/web/src/App.tsx:189-213` | Six-dimension targets are assembled only from the legacy selected result + selected reference. | Preserve this shape, but source the scoreable feature set from `GET /search/features` and the body from the result/reference. Use `fineValuesFromSearchResult` only for D01 legacy metrics; D06 values come from `contributions[]`/evidence records. |
| `apps/web/src/lib/dimensions.ts:136-143` | `DIMENSION_ROLE` fixes D01=similarity, D02/D04/D06=evidence_only, D03/D05=context_only. D06 is now scoreable. | Derive the role from the registry: similarity if any feature in the dimension has `roles.similarity.supported`; context if only `roles.context.supported`; otherwise evidence. Do not keep a per-dimension literal map. |
| `apps/web/src/lib/dimensions.ts:157` | Comment/assumption “Similarity features are always D01”. | Replace with “Similarity features are those with `roles.similarity.supported`; currently D01 and Earth D06.” |
| `apps/web/src/lib/dimensions.ts:178` | `features: code === "D01" ? features : []` hides D06 score controls. | Group registry features by `dimension_id` and pass every feature whose `roles.similarity.supported` is true (D01 + D06) to the score controls. |
| `apps/web/src/lib/dimensions.ts:179` | `total` fallback only counts D01. | Use the typed coverage summary or the registry `coverage.observation_count` for every dimension; keep explicit `0` only when the API says zero records. |
| `apps/web/src/api/client.ts:275-288` | Only `searchCandidates()` for legacy `/search`. | Add `searchMultidimensional(body: MultiDimSearchRequest)` and `getFeatureRegistry(params)`. |
| `apps/web/src/api/client.ts:367-371` | `TypedObservationQuery` only has `dimension`, `body`, `featureId`. | Add `areaId`, `state`, `datasetId`, `sourceAssetId`, `minDepthM`, `maxDepthM`, `temporalKind`, `west/south/east/north`, `lat/lon`, and `envelope`. |
| `apps/web/src/api/client.ts:373-393` | Always returns `TypedObservationOut[]`; no `no_data` envelope. | Add `getTypedObservationEnvelope()` returning `TypedObservationEnvelope`; keep the array function for compatibility. |
| `apps/web/src/components/SearchForm.tsx:151` | `useState("D01")` forces D01 as the active dimension. | Default to the first registry dimension with a similarity feature (D01), but allow D06; do not hardcode. |
| `apps/web/src/components/SearchForm.tsx:239` | `getTypedObservations({})` downloads all 2,152 typed rows. | Query by `body` + `dimension` + `state`/`feature_id`/`area_id`; use `envelope=true` and show `status=no_data` explicitly. |
| `apps/web/src/components/SearchForm.tsx:249-251` | `dimensionCards(features, typedCoverage, typedObservations)` only has legacy D01 features + typed counts. | Pass the feature registry; let `dimensionCards` group legal similarity/evidence/context features by dimension. |
| `apps/web/src/components/SearchForm.tsx:1050` | CSS class `card.code === "D01" ? " is-similarity"` assumes only D01 can score. | Use `card.role === "similarity"`. |
| `apps/web/src/components/SearchForm.tsx:1059` | Header tag/active count only for D01. | Show the registry role for every dimension; for D06 show similarity + hard-filter tags. |
| `apps/web/src/components/SearchForm.tsx:1075` | Only `card.code === "D01"` renders `renderMetric` (target/range/weight sliders). | Render `renderMetric` for every feature with `roles.similarity.supported` (D01 and D06). Render an evidence-only panel for D02–D05; never pass D02–D05 features to `renderMetric`. |
| `apps/web/src/components/SearchForm.tsx:563-711` | `renderMetric` always renders target + range + weight controls. | Keep it for similarity/hard-filter features only. Add a separate `EvidencePanel` for `roles.evidence.supported` / `roles.context.supported`. |
| `apps/web/src/components/SearchForm.tsx:265-286` | `buildFilters` sends every feature/context range as a filter. | Send filters only for ids with `roles.hard_filter.supported`; send WorldCover only through the legacy `/search` request. |
| `apps/web/src/components/SearchForm.tsx:737-741` | Static body options `Moon`, `custom`, `Mars`. | This is a body preset, not a D01 hardcode. Keep it, but label Earth as `custom` (the current UI convention) and never treat Moon/Mars as Earth-coordinate AOIs. |

## Land-cover control copy and semantics

Title (exact copy):

```text
zh-TW: 土地覆蓋條件（僅地球）
en:    Land-cover filter (Earth only)
```

Hint:

```text
zh-TW: 僅篩選地球候選；不是 D07，不加入相似度權重。
en:    Filters Earth candidates only. It is not D07 and adds no similarity weight.
```

Badge:

```text
zh-TW: 硬性篩選 · 不計分
en:    Hard filter · not scored
```

Rules:

1. This is **not a dimension**. Do not render a seventh dimension card, do not put it in
   `dimension_weights`, and do not call it D07.
2. It is an inclusive `min`/`max` **hard filter** on Earth candidates:
   `filters.landcover_<class>_fraction = { min?, max? }` on legacy `POST /api/v1/search`.
3. It never changes the similarity distance. A surviving candidate has exactly the same `distance`
   with or without the filter.
4. Missing land-cover context is **missing**, never `0`. A candidate with no stored context is
   excluded with `missing_context:<feature_id>`; display that reason.
5. Moon/Mars are not offered this control. `provider_coverage_by_body` is `not_applicable` for both.
6. The current multidimensional endpoint rejects this filter with `unknown_feature`. Until the
   backend combines paths, the #119 control must use the legacy search request or be clearly
   disabled with the gap message.
7. Empty result copy: “No Earth candidate has a stored land-cover value in this range. Missing
   context is not zero.”

Recommended i18n keys:

```ts
// en
"search.landcover.title": "Land-cover filter (Earth only)"
"search.landcover.hint": "Filters Earth candidates only. It is not D07 and adds no similarity weight."
"search.landcover.badge": "Hard filter · not scored"
"search.landcover.empty": "No Earth candidate has a stored land-cover value in this range. Missing context is not zero."
"search.landcover.unavailableBody": "Land-cover context is Earth-only, so it is not offered for Moon or Mars."

// zh-TW
"search.landcover.title": "土地覆蓋條件（僅地球）"
"search.landcover.hint": "僅篩選地球候選；不是 D07，不加入相似度權重。"
"search.landcover.badge": "硬性篩選 · 不計分"
"search.landcover.empty": "沒有地球候選在此範圍內有已儲存的土地覆蓋值；缺少 context 不會當成 0。"
"search.landcover.unavailableBody": "土地覆蓋 context 僅適用於地球，因此月球與火星不提供此篩選。"
```

## No-invalid-sliders rule for D02–D05

**Never render a target slider, range slider, or weight slider for D02–D05.**

Reason: the registry does not support similarity for those features. Rendering a weight slider
would imply they can enter the weighted distance, which is false. The script asserts:

- `POST /search/multidimensional` with `geologic_unit` (D02) returns 422
  `feature_role_not_scoreable`, `legal_roles=["evidence"]`.
- `GET /search/features?role=similarity&body=Earth` contains no `landcover_*` and no D02–D05
  evidence-only feature.
- D02–D05 observations are `evidence_only`/`context_only`, not score contributions.

Instead, render an **evidence/query panel** with these controls:

| Control | API facet |
|---|---|
| Body | `body=Earth|Moon|Mars` |
| Dimension | `dimension=D02|D03|D04|D05` |
| Feature | `feature_id=…` from `GET /search/features?dimension=…&role=evidence` |
| State | `state=detected|not_detected|unknown|no_coverage|not_applicable` |
| Source | `dataset_id=…` or `source_asset_id=…` |
| Exact AOI | `area_id=…` |
| Depth | `min_depth_m` / `max_depth_m` |
| Time support | `temporal_kind=instant|…` |
| Spatial | bbox or point (Earth only for honest intersection) |
| Envelope | `envelope=true` to get `status=ok|no_data` |

For each feature, display the legal-role badge and the API reason:

```tsx
const similarity = feature.roles.similarity;
const reason = similarity.supported
  ? null
  : feature.unsupported_reason ?? similarity.reason;
```

Dimension role matrix observed on this branch:

| Dimension | Observed legal role(s) | UI control |
|---|---|---|
| D01 | similarity + hard_filter | score sliders + filters |
| D02 | evidence (map units/processes; no similarity) | evidence panel only |
| D03 | evidence/context (instrument-specific thermal) | evidence panel only |
| D04 | evidence (water/ice/radar) | evidence panel only |
| D05 | context (solar geometry) | evidence/context panel only |
| D06 | similarity + hard_filter (Earth only, 250 m) | score sliders + filters + 250 m caveat |

Panel states:

- `idle`: “Choose a body, dimension and feature to query evidence.”
- `loading`: request in flight; cancel on body/dimension change.
- `ok`: render records with state, value/unit, method, dataset/source asset, support, uncertainty.
- `no_data`: “No stored record matches these filters.” This is explicit, not an error and not zero.
- `unsupported`: `roles.*.supported=false`; show `reason`/`unsupported_reason`, no controls that
  imply scoring.
- `error`: `ApiError` code/message/details; never replace with a fabricated empty table.

## Suggested client functions

```ts
export function getCoverageAoi(params: {
  body: Body;
  west?: number; south?: number; east?: number; north?: number;
  lat?: number; lon?: number;
  bboxLonConvention?: "signed" | "360";
}): Promise<AoiQueryResponse>;

export function getFeatureRegistry(params: {
  body?: Body; dimension?: DimensionCode; role?: string;
}): Promise<FeatureRegistryResponse>;

export function getTypedObservationEnvelope(
  query: TypedObservationQuery,
  signal?: AbortSignal,
): Promise<TypedObservationEnvelope>;

export function searchMultidimensional(
  body: MultiDimSearchRequest,
  signal?: AbortSignal,
): Promise<MultiDimSearchResponse>;
```

Implementation notes:

- Use `URLSearchParams` and include `envelope=true` whenever the UI needs `no_data` vs `ok`.
- Abort in-flight evidence requests on body/dimension/filter changes (`AbortController`).
- Do not request all typed observations at mount. Query only the visible dimension/AOI.
- Keep `ApiError` handling; preserve `code`, `message`, `status`, `details`.

## Known gaps / blockers for #119

1. **WorldCover is legacy-only.** `/search/multidimensional` rejects
   `landcover_bare_sparse_fraction` with `unknown_feature`. #119 cannot send a single combined
   D01+D06+land-cover request yet. Use legacy `/search` for the land-cover filter or request a
   backend follow-up.
2. **Planetary D06 audit areas are not at every AOI.** `/coverage/aoi` transforms stored projected
   typed-area envelopes to planetocentric lon/lat and reports intersecting evidence. The six D06
   `no_coverage` audit records are stored on specific areas (`moon-apollo15-station8-lsrp`,
   `mars-insight-elysium-hp3`), so a polar Shackleton or Jezero bbox correctly reports
   `D06=not_indexed`. Use `area_id` for those exact audit records and do not present `not_indexed`
   as “no data anywhere on the body”.
3. **Counts differ from the brief.** 2,152 typed observations and 5 Mars references are what the
   committed seed actually contains; the brief’s 2,185 / 7 Mars references are not reproducible on
   this branch. Do not display the brief’s numbers as observed counts.
4. **Legacy result body.** Legacy `POST /search` results do not include `body`; multidim results do.
   Do not hardcode `body: "Earth"` in shared code. If a legacy result is reused for the
   six-dimension panel, derive Earth from the endpoint contract, not a literal.
5. **D06 support.** Always display “250 m model prediction over the AOI envelope, not a local soil
   sample”, and do not compare it as if it had 90 m terrain support.

## Verification

```bash
bash scripts/accept_sixdim_aoi.sh
uv run pytest apps/api/tests/test_worldcover_filter_160.py -q
```

`scripts/accept_sixdim_aoi.sh` asserts the Earth/Moon/Mars statuses, real evidence/no_coverage
records, facet filters, D01+D06 ranking change, zero-weight non-exclusion, dimension-weighted
recomputation, out-of-coverage status, and the Earth-only WorldCover hard filter. It never calls a
provider or the public API.
