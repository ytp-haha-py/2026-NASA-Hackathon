# Frontend handoff — Earth bbox map and planetary on-demand AOI (Issue #132)

Audience: the #118/#119/#123 frontend collaborators. This file is a contract summary, not a UI
implementation. It covers the two new backend surfaces and the existing detail routes the map will
open. `contracts/openapi.json` and the generated TypeScript types
(`apps/web/src/api/schema.d.ts`) are main-agent deliverables; the snippets below are the intended
shape and can be used against mocks until regeneration.

Backend references:

- [`EARTH_MAP_API.md`](EARTH_MAP_API.md) — full Earth endpoint contract.
- [`PLANETARY_AOI_ANALYSIS.md`](PLANETARY_AOI_ANALYSIS.md) — full planetary analyze contract.
- [`PLANETARY_MAPS_API.md`](PLANETARY_MAPS_API.md) — existing basemap/overlay contract (#117).

## 1. Earth bbox endpoint

```http
GET /api/v1/earth/map-features?bbox=west,south,east,north
```

Query shape:

```ts
type EarthMapQuery = {
  bbox: string; // "west,south,east,north" in EPSG:4326 degrees
  bbox_lon_convention?: "signed" | "360";
  feature_type?: "all" | "fine_candidate" | "on_demand_aoi" | "typed_area" | "coarse_candidate";
  data_level?: "all" | "fine" | "coarse";
  dimension?: "D01" | "D02" | "D03" | "D04" | "D05" | "D06";
  availability?: "any" | "present" | "absent";
  limit?: number; // 1..200, default 50
  offset?: number; // >= 0
};
```

Call example:

```ts
async function loadEarthFeatures(bbox: string, signal?: AbortSignal) {
  const params = new URLSearchParams({
    bbox,
    feature_type: "all",
    data_level: "fine", // start with fine; coarse is a separate shortlist layer
    limit: "200",
    offset: "0",
  });
  const response = await fetch(`/api/v1/earth/map-features?${params}`, { signal });
  if (!response.ok) throw await response.json();
  return (await response.json()) as EarthMapFeaturesResponse;
}
```

`bbox` from a Leaflet/MapLibre Earth map:

```ts
// Leaflet: do not let west/east wrap silently.
const b = map.getBounds();
const bbox = [
  b.getWest(),
  b.getSouth(),
  b.getEast(),
  b.getNorth(),
].join(",");
```

If `west > east`, keep it. The backend interprets that as an antimeridian crossing. Do not sort the
four numbers or clamp them to `[-180, 180]`; that destroys the crossing meaning. If the UI stores
0–360 longitudes (for example a planetary-oriented control), send
`bbox_lon_convention=360`.

### Response types

```ts
export type DimensionCode = "D01" | "D02" | "D03" | "D04" | "D05" | "D06";
export type EarthFeatureType =
  | "fine_candidate"
  | "on_demand_aoi"
  | "typed_area"
  | "coarse_candidate";
export type EarthDataLevel = "fine" | "coarse" | "typed";
export type EarthSupportLabel =
  | "fine_90m"
  | "shortlist_250m_derived_not_fine"
  | "stored_typed_subset";

export type DimensionStatus = {
  status: string; // "present" | "present_evidence" | "absent" | server-defined
  value_origin: string | null;
  reason: string | null;
};

export type EarthBBox = {
  west: number;
  south: number;
  east: number;
  north: number;
  crosses_antimeridian?: boolean;
  longitude_span_deg?: number;
  source?: string;
  crs?: "EPSG:4326";
};

export type EarthAvailabilityFlags = {
  d01_present: boolean;
  d02_present: boolean;
  d03_present: boolean;
  d04_present: boolean;
  d05_present: boolean;
  d06_present: boolean;
  any_dimension_present: boolean;
  dimension_count: number;
  support: EarthSupportLabel;
  shortlist_only: boolean;
};

export type EarthMapFeature = {
  feature_id: string; // "fine:", "on_demand:", "typed:" or "coarse:" prefix
  feature_type: EarthFeatureType;
  body: "Earth";
  display_name: string;
  location_id: string | null;
  candidate_id: string | null;
  area_id: string | null;
  typed_area_id?: string | null;
  data_level: EarthDataLevel;
  bbox: EarthBBox;
  center: { lon: number; lat: number };
  geometry: { type: "Point"; coordinates: [number, number] };
  bbox_geometry: GeoJSON.Polygon | GeoJSON.MultiPolygon; // EPSG:4326
  crs: string; // map CRS: EPSG:4326
  bbox_crs: "EPSG:4326"; // render with this
  coordinate_frame: "earth_wgs84";
  analysis_crs?: string | null; // projected analysis CRS for coarse rows
  dimension_status: Record<DimensionCode, DimensionStatus>;
  available_dimensions: DimensionCode[];
  missing_dimensions: DimensionCode[];
  availability: EarthAvailabilityFlags;
  support: EarthSupportLabel;
  support_label?: EarthSupportLabel; // alias of support
  data_version: string;
  detail_links: {
    location?: string | null;
    global_candidate?: string | null;
    typed_area?: string | null;
    area_features?: string | null;
  };
};

export type EarthMapFeaturesResponse = {
  contract_version: string;
  coordinate_contract_version?: string;
  data_version: string;
  snapshot_id: string; // same data snapshot header
  body: "Earth";
  bbox: EarthBBox;
  bbox_crs: "EPSG:4326";
  coordinate_system: string;
  coordinate_frame: "earth_wgs84";
  query: Record<string, unknown>;
  total: number;
  returned: number;
  offset: number;
  limit: number;
  next_offset: number | null;
  counts_by_type: Partial<Record<EarthFeatureType, number>>;
  coverage: {
    fine: { matched_count: number; label: string };
    on_demand: { matched_count: number; label: string };
    typed: { matched_count: number; label: string };
    coarse: {
      status: "ready" | "no_index" | "index_invalid" | string;
      snapshot_id: string | null;
      method_version: string | null;
      // Compatibility aliases may also be present.
      snapshot?: string | null;
      method?: string | null;
      row_count?: number;
      regions: string[];
      bbox: EarthBBox | null;
      matched_count: number;
      returned?: number;
      limit?: number;
      truncated?: boolean;
      query_truncated?: boolean;
    };
  };
  features: EarthMapFeature[];
  notes: string[];
};
```

Do not rely on `feature_id` prefixes for logic. Switch on `feature_type` and
`support` (or its `support_label` alias), and follow `detail_links`. Render map geometry from
`bbox`/`bbox_crs`/`coordinate_frame`; read `crs` only as the map CRS and `analysis_crs` when you
need the stored projected analysis CRS.

### Filters and layers

- `feature_type=fine_candidate`: indexed 90 m terrain windows.
- `feature_type=on_demand_aoi`: Earth AOIs previously analyzed through
  `POST /api/v1/areas/analyze`; same fine support, different origin.
- `feature_type=typed_area`: stored D02–D06 evidence; may have no D01 fine metric.
- `feature_type=coarse_candidate`: the 250 m shortlist. Always show
  `support`/`support_label: "shortlist_250m_derived_not_fine"` so users do not read it as a 90 m result.
  `coverage.coarse` reports `snapshot_id`/`method_version`; accept `snapshot`/`method` aliases.
- `data_level=fine` hides coarse; use this as the default map layer to avoid coarse density.
- `dimension=D06&availability=present` shows areas with stored D06 evidence. `availability=absent`
  means "no stored observation", never zero.
- Pass an `AbortSignal` and cancel the previous request when the bbox changes. Debounce map moves
  (~250–400 ms) and request a slightly padded bbox.

### Pagination and density

- Hard cap: `limit <= 200`. Default 50. `next_offset` is the only correct next-page cursor; stop
  when it is `null`.
- `total`, `counts_by_type` and `coverage.*.matched_count` include all matches, not just the page.
  Badge them as query-level counts.
- The current coarse index has only 168 cells across three small pilots but they overlap densely
  at low zoom. Do not draw every coarse polygon at world zoom. Either use `data_level=fine` for the
  overview, or aggregate/limit coarse rendering and show the "shortlist not global" label.
- The endpoint returns 200 with `features: []` for an empty box. Render "No stored features in this
  area", not an error or a fake marker.

### Earth coordinate pitfalls

- Everything here is WGS84 `EPSG:4326`. If the map library uses a Web-Mercator tile layer, pass
  `[lat, lon]`/`[lon, lat]` in the library's expected order; the API always uses GeoJSON
  `[lon, lat]`.
- A bbox crossing the antimeridian must be drawn as two rectangles. `crosses_antimeridian: true`
  and `longitude_span_deg` tell you when to split.
- Never send Moon/Mars coordinates to this endpoint or draw them on the Earth layer. Use the
  planetary endpoint and the body frame from `GET /api/v1/planetary/maps?body=...`.

## 2. Planetary on-demand analysis

```http
POST /api/v1/planetary/areas/analyze
Content-Type: application/json
```

```ts
type PlanetaryAoiRequest = {
  body: "Moon" | "Mars";
  bbox: { west: number; south: number; east: number; north: number }; // body lon/lat degrees
  requested_features?: Array<
    "coarse_slope_mean_deg" | "coarse_slope_p90_deg" | "coarse_flat_fraction_lt5deg"
  >;
  idempotency_key?: string; // reuse on every retry of the same click
};

// AOI sides must fit the bounded analysis guard (AOI_MAX_SIDE_M = 12_000 m,
// a 10 km-scale cap). The reproducible four-cell Mars Jezero block is
// 78.08,18.47,78.27,18.65; a single-cell box such as
// 78.08,18.47,78.18,18.56 also works. A huge box returns the explicit 422
// code aoi_too_large -- it is not clipped silently.

type PlanetaryAoiResponse = {
  job_id: string;
  status: "queued" | "running" | "completed" | "partial" | "failed";
  area_id: string | null;
  cache_key: string;
  cached: boolean;
  body: "Moon" | "Mars";
  coordinate_frame: "lunar_planetocentric" | "mars_planetocentric" | string;
  method_version?: "planetary-terrain-horn-250m-aoi-1.0.0" | string | null;
  coverage_note?: string | null;
  provider_status: Record<string, { status?: string; reason?: string; network_used?: boolean }>;
  result: null | {
    area_id: string;
    location_id?: string;
    body: string;
    coordinate_frame?: string;
    method_version?: "planetary-terrain-horn-250m-aoi-1.0.0" | string;
    analysis_kind?: "committed_reference_fixture" | "live_bounded_read" | string;
    data_version?: string;
    feature_count?: number;
    features?: Partial<Record<
      "coarse_slope_mean_deg" | "coarse_slope_p90_deg" | "coarse_flat_fraction_lt5deg",
      number
    >>;
    dimension_support?: Partial<Record<DimensionCode, {
      status: string;
      reason?: string;
      method_version?: string;
      source_method_version?: "coarse-planetary-dem-horn-1.0.0" | string;
    }>>;
    provider_status?: Record<string, unknown>;
    sources?: Array<Record<string, unknown>>;
    warnings?: string[];
  };
  links: {
    job: string;
    typed_area?: string;
    map_features?: string;
  };
};

type PlanetaryJob = {
  job_id: string;
  status: "queued" | "running" | "completed" | "partial" | "failed";
  area_id: string | null;
  body?: "Moon" | "Mars";
  coordinate_frame?: string;
  provider_status: Record<string, unknown>;
  error: { code: string; message: string } | null;
  result: PlanetaryAoiResponse["result"];
  created_at: string | null;
  started_at: string | null;
  finished_at: string | null;
};
```

Call and poll:

```ts
async function analyzePlanetary(
  request: PlanetaryAoiRequest,
  onUpdate: (value: PlanetaryAoiResponse | PlanetaryJob) => void,
  signal?: AbortSignal,
) {
  const created = await fetch("/api/v1/planetary/areas/analyze", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
    signal,
  });
  const first = (await created.json()) as PlanetaryAoiResponse;
  onUpdate(first);
  if (first.status === "completed" || first.status === "partial" || first.status === "failed") {
    return first;
  }

  let delay = 500;
  for (let attempt = 0; attempt < 60; attempt += 1) {
    await new Promise((resolve) => setTimeout(resolve, delay));
    const response = await fetch(first.links.job, { signal });
    if (!response.ok) throw await response.json();
    const job = (await response.json()) as PlanetaryJob;
    onUpdate(job);
    if (job.status === "completed" || job.status === "partial" || job.status === "failed") {
      return job;
    }
    delay = Math.min(delay * 1.6, 5000);
  }
  throw new Error("Planetary AOI analysis timed out");
}
```

Generate the idempotency key once per user action (for example
`crypto.randomUUID()`), not per poll/retry. Reuse it if the user clicks "retry".

### Loading, error and partial states

- `queued`/`running`: show a spinner and the selected bbox; keep the map interactive. Do not render
  fake metric values while waiting.
- `completed`: render the three D01 values with units (`degree`, `degree`, `fraction`) and the
  `method_version` (the AOI recomputation method, `planetary-terrain-horn-250m-aoi-1.0.0`) plus
  `dimension_support.D01.source_method_version` (`coarse-planetary-dem-horn-1.0.0`) as the source
  method label.
- `partial`: render the values plus a visible "partial source status" warning from
  `provider_status`/`warnings`. Never hide a partial.
- `failed`: show `error.code`/`error.message` from the job route, or
  `provider_status.analysis.reason` (for example `out_of_coverage`). A failed job may carry an
  `area_id` for the durable row; it is not a persisted area and must not be opened as one. Offer
  retry with the same idempotency key. Do not show zeros.
- 422 `unsupported_body`/`out_of_coverage`/`aoi_too_large`/`unsupported_feature_on_body`: a normal
  user-facing "this area is not covered / too large" state, not a crash.
- Show D01 only. D02–D06 must render as "Unsupported on demand — no verified source" / "Evidence
  only" from `dimension_support`; never as zero and never as an Earth comparison.
- There is no cross-body total score. Do not display a combined Moon/Mars/Earth ranking.

### Planetary coordinate pitfalls

- `body` and its frame decide the map layer. Moon `lunar_planetocentric`; Mars
  `mars_planetocentric`. Neither is Earth `EPSG:4326`.
- Do not pass a planetary bbox to the Earth endpoint, an OSM URL, or a Web-Mercator projection
  without the declared body frame.
- The response bbox is the actual analysed envelope. If `coverage_note`/`warnings` mention snapping
  to covered cells, draw the returned bbox and explain it.
- Use `GET /api/v1/planetary/maps?body=Moon|Mars` for tile templates, zooms and body metadata. Do
  not hardcode NASA Trek URLs or an EPSG code.
- Planetographic vs planetocentric latitude matters on Mars. The API uses planetocentric. If a
  future control asks for planetographic, convert explicitly with the `coordinate-contract`
  formula; never relabel one as the other.

## 3. Detail routes to open

| Feature | Route |
|---|---|
| Earth fine candidate | `GET /api/v1/locations/{location_id}` |
| Earth on-demand AOI | `GET /api/v1/areas/{area_id}/features` |
| Earth typed evidence | `GET /api/v1/typed/areas/{area_id}` |
| Earth coarse candidate | `GET /api/v1/global/candidates/{candidate_id}` |
| Planetary reference/typed overlay | `GET /api/v1/planetary/map-features?body=...&bbox=...` |
| Planetary typed area | `GET /api/v1/typed/areas/{area_id}` or `GET /api/v1/planetary/areas/{area_id}` |
| Job status | `GET /api/v1/jobs/{job_id}` |

Prefer the URLs in `detail_links`/`links`; they already encode the route. Keep the Earth detail
drawer and planetary detail drawer separate so a planetary frame never enters an Earth component.

## 4. Contract maintenance

After the main integration agent changes schemas/routes:

```bash
uv run python scripts/export_openapi.py
```

Then regenerate the frontend types from `contracts/openapi.json` as the repository normally does.
Until that regeneration lands, use the types above against mocks and check
`GET /api/v1/openapi.json` for the live shape. If a field is missing, report it as a contract
mismatch instead of casting it to `any` silently.

Offline acceptance:

```bash
uv run pytest apps/api/tests/test_issue132_acceptance.py -q
bash scripts/issue132_acceptance.sh
```
