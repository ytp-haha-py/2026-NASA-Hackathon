# Planetary on-demand AOI analysis (Issue #132)

Backend contract for bounded on-demand Moon/Mars analysis. The endpoint recomputes verified D01
coarse terrain metrics from committed real planetary source fixtures, persists the result as a
typed-v2 area and exposes it through the existing job and typed-area routes. D02–D06 stay explicit
unsupported/evidence-only statuses; no value is fabricated and no cross-body total score is
computed.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/v1/planetary/areas/analyze` | Validate a bounded Moon/Mars bbox and enqueue/reuse a durable job. |
| `GET` | `/api/v1/jobs/{job_id}` | Shared job status/result (also `GET /api/v1/planetary/jobs/{job_id}`). |
| `GET` | `/api/v1/planetary/areas/{area_id}` | Planetary typed area detail. |
| `GET` | `/api/v1/typed/areas/{area_id}` | Same stored area in the generic typed-v2 route. |
| `GET` | `/api/v1/planetary/map-features?body=Moon\|Mars&bbox=...` | Map overlay containing the new typed area. |

Registered OpenAPI tag: `planetary`.

## Request

```json
{
  "body": "Moon",
  "bbox": {
    "west": 19.85,
    "south": -10.15,
    "east": 20.15,
    "north": -9.85
  },
  "requested_features": [
    "coarse_slope_mean_deg",
    "coarse_slope_p90_deg",
    "coarse_flat_fraction_lt5deg"
  ],
  "idempotency_key": "moon-lola-window-01-demo"
}
```

| Field | Required | Notes |
|---|---|---|
| `body` | yes | `Moon` or `Mars`. Earth uses `POST /api/v1/areas/analyze`, not this route. |
| `bbox` | yes | `west,south,east,north` as numbers, planetocentric lon/lat degrees in the body frame. Non-wrapping (`west < east`, `south < north`). |
| `requested_features` | no | Defaults to all three verified D01 coarse features. Unknown or non-D01 ids return 422 `unsupported_feature_on_body`; they are never silently dropped. |
| `idempotency_key` | no | Max 128 chars. Repeating the same key returns the existing job/result. |

The bbox must intersect at least one committed coarse cell in one of the verified pilots. It is not
a free global download request. AOI sides must be between the minimum and
`AOI_MAX_SIDE_M = 12_000 m` (a 10 km-scale safety cap); oversized boxes return explicit
`aoi_too_large`, never a silently clipped analysis.

## Coordinate contract

- Moon: `IAU_MOON`, `lunar_planetocentric`, positive-east degrees, body radius 1,737,400 m.
- Mars: `IAU_MARS`, `mars_planetocentric`, positive-east degrees, Mars 2000 sphere/ellipsoid
  (`a=3,396,190 m`, `b=3,376,200 m`).
- These coordinates are **never** Earth `EPSG:4326`. Do not pass them to an OSM/Leaflet Earth
  basemap. Use `GET /api/v1/planetary/maps?body=...` and the declared body frame.
- The response includes `coordinate_frame` (`lunar_planetocentric` or `mars_planetocentric`) and
  the stored area carries a planetary `crs` (`+proj=longlat +R=... +no_defs`), not an Earth CRS.
- `bbox` in the response is the actual analysed envelope. If a requested bbox is intersected by
  only part of a coarse cell, the engine may snap to the covered cell union and report
  `coverage_note` / warnings. Clients must display the returned bbox, not assume the request was
  processed exactly edge-for-edge.

## Response

The route returns an `AoiAnalyzeResponse`-shaped body. HTTP status is `202` while queued/running and
`200` once the job is completed, partial or failed. With `ANALOG_AOI_INLINE=true` (tests/local
inline runner), the first POST normally returns `200` with a completed result.

```json
{
  "job_id": "8f1c8b8f7e2a4a3b9f3d4c5e6a7b8c9d",
  "status": "completed",
  "area_id": "planet-aoi-moon-122db1830912",
  "cache_key": "e75fab27f0ba18ede2667afcfd65bfde7d73da3d81b83b895db8610c8ceb4401",
  "cached": false,
  "body": "Moon",
  "coordinate_frame": "lunar_planetocentric",
  "method_version": "planetary-terrain-horn-250m-aoi-1.0.0",
  "coverage_note": "Committed reference fixture analysis over 4 Moon cell(s) ...",
  "provider_status": {
    "moon-lola128-pa-pixel-202405": {
      "status": "verified_committed_fixture",
      "network_used": false,
      "fixture_checksums_verified": true
    }
  },
  "result": {
    "area_id": "planet-aoi-moon-122db1830912",
    "location_id": "planet-aoi-moon-122db1830912",
    "body": "Moon",
    "coordinate_frame": "lunar_planetocentric",
    "method_version": "planetary-terrain-horn-250m-aoi-1.0.0",
    "analysis_kind": "committed_reference_fixture",
    "data_version": "dv-...",
    "feature_count": 3,
    "context_feature_count": 0,
    "features": {
      "coarse_slope_mean_deg": 11.459459413931441,
      "coarse_slope_p90_deg": 30.035049395554832,
      "coarse_flat_fraction_lt5deg": 0.22125
    },
    "dimension_support": {
      "D01": {
        "status": "present",
        "method_version": "planetary-terrain-horn-250m-aoi-1.0.0",
        "source_method_version": "coarse-planetary-dem-horn-1.0.0",
        "support": "5 km planetary coarse cells aggregated on the fixed 250 m analysis grid",
        "cell_count": 4,
        "feature_count": 3
      },
      "D02": {"status": "unsupported_for_on_demand_analysis", "reason": "no verified on-demand mineralogy source"},
      "D03": {"status": "unsupported_for_on_demand_analysis", "reason": "no verified on-demand thermal source"},
      "D04": {"status": "unsupported_for_on_demand_analysis", "reason": "no verified on-demand water/ice source"},
      "D05": {"status": "unsupported_for_on_demand_analysis", "reason": "no verified on-demand illumination source"},
      "D06": {"status": "unsupported_for_on_demand_analysis", "reason": "no verified on-demand regolith source"}
    },
    "provider_status": {
      "moon-lola128-pa-pixel-202405": {
        "status": "verified_committed_fixture",
        "network_used": false
      }
    },
    "sources": [
      {
        "dataset_id": "nasa-pgda-lola-moon-pa-ldem128-202405",
        "provider": "NASA GSFC Planetary Geodesy Data Archive (PGDA) / LRO-LOLA",
        "product_id": "LDEM128_PA_pixel_202405",
        "url": "https://pgda.gsfc.nasa.gov/data/LOLA_PA/LDEM128_PA_pixel_202405.tif",
        "license": "not stated on the product page; public NASA archive with citation requirement",
        "license_url": "https://science.data.nasa.gov/about/license",
        "source_kind": "planetary_dem",
        "source_crs": "MOON_PA (DE421), body-fixed",
        "native_resolution_m": 236.90117518866782
      }
    ],
    "warnings": [],
    "area_km2": 99.2
  },
  "links": {
    "job": "/api/v1/jobs/8f1c8b8f7e2a4a3b9f3d4c5e6a7b8c9d",
    "typed_area": "/api/v1/typed/areas/planet-aoi-moon-122db1830912",
    "map_features": "/api/v1/planetary/map-features?body=Moon&bbox=19.85,-10.15,20.15,-9.85"
  }
}
```

**Values above are illustrative.** The three numeric feature values are real D01 outputs from the
committed Moon pilot cells but an on-demand area recomputes the union of its selected cells, so the
exact returned numbers depend on the bbox. Never hardcode them. The contract is the field names,
units, frame, method and support — not a fixed number.

`status` is one of:

| Status | HTTP | Meaning |
|---|---|---|
| `queued` | 202 | Job row committed, not started. |
| `running` | 202 | Worker is validating/reading/aggregating. |
| `completed` | 200 | All requested D01 features computed and persisted; providers verified/ok. |
| `partial` | 200 | Result persisted but at least one provider status is not `verified`/`ok`; inspect `provider_status` and `warnings`. Display the partial badge, never hide it. |
| `failed` | 200 | Job ran and failed (provider error, source missing, invalid source). `error.code` is set on the job route; `result` is null/empty. Do not render fabricated values. An out-of-coverage request may allocate an `area_id` for the durable job row, but no typed area or observation is persisted. |

Validation failures that happen before a job exists (unsupported body/feature, invalid bbox,
`aoi_too_large`) return the shared 422 error shape instead of a job. Out-of-coverage can be either a
422 error or a completed lifecycle row with `status: "failed"` and
`provider_status.analysis.reason == "out_of_coverage"`; both are explicit failures. A huge bbox that
fails the size guard returns `aoi_too_large`, which is also correct and must not be retried
unchanged.

## Status lifecycle and polling

1. `POST /planetary/areas/analyze`.
2. If response is `queued`/`running` (HTTP 202), poll `GET /api/v1/jobs/{job_id}` with exponential
   backoff (for example 0.5 s → 1 s → 2 s, capped at 5 s; stop after a bounded timeout).
3. Stop on `completed`, `partial` or `failed`. On `completed`/`partial`, open `links.typed_area`
   or `links.map_features`.
4. Never poll faster than ~2 requests/second and never retry a non-idempotent new analysis in a
   tight loop.

Example job poll:

```bash
curl -sS 'http://127.0.0.1:8000/api/v1/jobs/8f1c8b8f7e2a4a3b9f3d4c5e6a7b8c9d'
```

```json
{
  "job_id": "8f1c8b8f7e2a4a3b9f3d4c5e6a7b8c9d",
  "status": "completed",
  "area_id": "planet-aoi-moon-122db1830912",
  "body": "Moon",
  "coordinate_frame": "lunar_planetocentric",
  "provider_status": {
    "moon-lola128-pa-pixel-202405": {"status": "verified_committed_fixture"}
  },
  "error": null,
  "result": {"feature_count": 3, "features": {"coarse_slope_mean_deg": 13.62}},
  "created_at": "2026-11-15T12:00:00.000000+00:00",
  "started_at": "2026-11-15T12:00:00.100000+00:00",
  "finished_at": "2026-11-15T12:00:00.400000+00:00"
}
```

`result.features` is a summary copy; the authoritative stored observations are on the typed area.

## Retry, cache and recovery

- Jobs are durable rows in the same SQLite/Postgres database. A process restart requeues
  `running` jobs and resumes `queued` jobs during application startup (`JobRunner.recover`).
- The same bbox/body/features produces the same `cache_key`; repeating the POST returns the
  existing job with `cached: true` and the same `job_id`/`area_id`.
- An `idempotency_key` takes precedence and returns the job created for that key even if the bbox
  representation differs cosmetically.
- Repeating a previously `failed` job with the same cache key queues it again (retry) rather than
  returning a permanently poisoned cache row.
- A client retry must reuse the same `idempotency_key`. Do not create a new key for an automatic
  retry.

## Reproducible examples

Base URL below is local. Public deployment:
`https://api.貓熊.台灣/api/v1` (`https://api.xn--2vxr09c.xn--kpry57d/api/v1`).

### Moon — LOLA128 pilot window (4 cells)

Requires `pipelines/planetary/coarse/moon_lola128_window01.json` and the committed fixture under
`samples/planetary/coarse/fixtures/`. No network is used for an AOI inside this window.

```bash
curl -sS -X POST 'http://127.0.0.1:8000/api/v1/planetary/areas/analyze' \
  -H 'Content-Type: application/json' \
  -d '{
    "body": "Moon",
    "bbox": {"west": 19.85, "south": -10.15, "east": 20.15, "north": -9.85},
    "requested_features": [
      "coarse_slope_mean_deg",
      "coarse_slope_p90_deg",
      "coarse_flat_fraction_lt5deg"
    ],
    "idempotency_key": "moon-lola128-window01"
  }'
```

The committed four Moon cells cover mean slope 6.589–17.657°, p90 slope 9.619–31.392° and flat
fraction 0.193–0.255. Those are per-cell source values, not a promise about the aggregate.

Read it back:

```bash
curl -sS \
  'http://127.0.0.1:8000/api/v1/planetary/map-features?body=Moon&bbox=19.8,-10.2,20.2,-9.8&limit=200'
curl -sS 'http://127.0.0.1:8000/api/v1/typed/areas/<area_id>'
```

### Mars — Jezero CTX pilot window (one cell)

Requires `pipelines/planetary/coarse/mars_ctx_jezero_window01.json` and the committed Jezero
fixture. No network is used for this window.

```bash
curl -sS -X POST 'http://127.0.0.1:8000/api/v1/planetary/areas/analyze' \
  -H 'Content-Type: application/json' \
  -d '{
    "body": "Mars",
    "bbox": {"west": 78.08, "south": 18.47, "east": 78.18, "north": 18.56},
    "requested_features": [
      "coarse_slope_mean_deg",
      "coarse_slope_p90_deg",
      "coarse_flat_fraction_lt5deg"
    ],
    "idempotency_key": "mars-jezero-ctx-window01"
  }'
```

The committed four Jezero cells cover mean slope 2.289–4.870°, p90 slope 4.720–8.140° and flat
fraction 0.540–0.918. The four-cell block is about 10.7 km wide at 18.5°N, so it fits the
`AOI_MAX_SIDE_M = 12_000 m` guard with bbox `78.08,18.47,78.27,18.65`; the one-cell bbox above is
also a deterministic acceptance case. The per-cell ranges are source values, not a promise about
any aggregate.

For a larger Mars view, query the stored overlay
(`GET /api/v1/planetary/map-features?body=Mars&bbox=76,17,79,20`) instead of requesting an
oversized analysis.

### Out of coverage

A small, valid-size box away from the pilots (so it reaches coverage validation rather than the
size guard):

```bash
curl -sS -X POST 'http://127.0.0.1:8000/api/v1/planetary/areas/analyze' \
  -H 'Content-Type: application/json' \
  -d '{
    "body": "Moon",
    "bbox": {"west": 0, "south": -5, "east": 0.05, "north": -4.95}
  }'
```

Expected explicit failure, not a synthetic row. The inline route currently returns a durable job
row with `status: "failed"` (HTTP 200) and no result:

```json
{
  "job_id": "0dda3a6ec72b45589b49b26d42d4ebbc",
  "status": "failed",
  "area_id": "planet-aoi-moon-697a381c97d6",
  "cache_key": "12b825f6fd3637100ce6046431cf823b2a0fb1bf9932b6dad00cdf2f212c9ac6",
  "cached": false,
  "body": "Moon",
  "coordinate_frame": "lunar_planetocentric",
  "provider_status": {
    "analysis": {"status": "failed", "reason": "out_of_coverage"}
  },
  "result": null,
  "links": {"job": "/api/v1/jobs/0dda3a6ec72b45589b49b26d42d4ebbc"}
}
```

A pre-job validation failure can instead return the shared 422 shape with `out_of_coverage` or a
related explicit coverage/provider code. The exact code may vary, but it is always explicit and
never `200 completed` with fake zeros. An oversized box returns `aoi_too_large` (also explicit).
Check `GET /planetary/map-features?body=Moon&bbox=0,-5.1,0.1,-4.9`; the empty result must stay
empty, and `GET /typed/areas/{area_id}` for the failed allocation must be 404 because no observations
were persisted.

### Unsupported dimension/feature

```bash
curl -sS -X POST 'http://127.0.0.1:8000/api/v1/planetary/areas/analyze' \
  -H 'Content-Type: application/json' \
  -d '{
    "body": "Moon",
    "bbox": {"west": 19.85, "south": -10.15, "east": 20.15, "north": -9.85},
    "requested_features": ["soil_organic_carbon_0_5cm"]
  }'
```

Returns 422 `unsupported_feature_on_body`. Only the three verified coarse D01 features are
available on demand; D02–D06 are not silently approximated.

## Source provenance, units, support and quality

| Fact | Moon | Mars |
|---|---|---|
| Public source | NASA GSFC PGDA, LOLA MOON_PA global shape model, product 95, `LDEM128_PA_pixel_202405.tif` | USGS Astrogeology, Mars 2020 Science Investigation CTX DEM Mosaic, `M20_JezeroCrater_CTXDEM_20m.tif` |
| DOI / citation | `10.60903/LOLA_PA`, Neumann (2024) | USGS Astrogeology Science Center; public domain product page |
| Native support | 128 ppd = 236.9012 m at the equator | 20 m stereo DTM |
| Analysis support | fixed 250 m grid, 5 km cell, 22 × 22 padded grid, central 20 × 20 aggregated | 20 m aggregated to the same 250 m grid/5 km cell |
| Frame | `IAU_MOON` / `lunar_planetocentric`, body-fixed, positive east | `IAU_MARS` / `mars_planetocentric`, `Mars_2000_Sphere` R=3,396,190 m |
| Vertical | metres relative to the 1,737.4 km reference sphere; unit inferred from pixel magnitude and flagged | metres, stereo DTM localized vertically to MOLA |
| Source cell method | `coarse-planetary-dem-horn-1.0.0` (Horn 3×3 DEM derivative) | same source method |
| AOI recomputation method | `planetary-terrain-horn-250m-aoi-1.0.0` (cell union aggregated on the fixed 250 m grid) | same AOI method |
| Features | `coarse_slope_mean_deg` (degree), `coarse_slope_p90_deg` (degree), `coarse_flat_fraction_lt5deg` (fraction) | same |
| Decision | `evidence_only` | `evidence_only` |

Both method versions are explicit: `result.method_version` and
`dimension_support.D01.method_version` are the AOI recomputation method, while
`dimension_support.D01.source_method_version` is the committed coarse-cell method. Neither is
treated as the Earth coarse method (`coarse-terrain-250m-1.0.0`).

Important caveats, repeated in `provider_status`, `quality_flags`, `warnings` and the typed
observations:

- The Moon product is a derived, partly interpolated shape model, not raw laser altimetry. The
  GeoTIFF does not label a vertical unit; the pilot infers metres and records that as a flag.
- The Mars CTX product is a stereo DEM mosaic. Per-window vertical uncertainty and mosaic seams are
  not independently validated here.
- Both methods differ from the Earth coarse method (`coarse-terrain-250m-1.0.0` is a published
  preprocessed slope product; the planetary values are a Horn DEM derivative). The difference is
  unmeasured.
- Therefore these values are D01 terrain **evidence**. They are not an Earth analog score and not
  a cross-body similarity distance.
- There is no cross-body total score, ranking or percentile. `POST /api/v1/global/search` rejects
  non-Earth bodies and `/api/v1/global/cross-body` reports `ranked=false`.

## D02–D06 behavior

On-demand planetary analysis supports **D01 only**. The response exposes the other dimensions as
explicit status entries:

```json
{
  "dimension_support": {
    "D01": {
      "status": "present",
      "method_version": "planetary-terrain-horn-250m-aoi-1.0.0",
      "source_method_version": "coarse-planetary-dem-horn-1.0.0",
      "support": "5 km planetary coarse cells aggregated on the fixed 250 m analysis grid"
    },
    "D02": {"status": "unsupported_for_on_demand_analysis", "reason": "no verified on-demand mineralogy source"},
    "D03": {"status": "unsupported_for_on_demand_analysis", "reason": "no verified on-demand thermal source"},
    "D04": {"status": "unsupported_for_on_demand_analysis", "reason": "no verified on-demand water/ice source"},
    "D05": {"status": "unsupported_for_on_demand_analysis", "reason": "no verified on-demand illumination source"},
    "D06": {"status": "unsupported_for_on_demand_analysis", "reason": "no verified on-demand regolith source"}
  }
}
```

The exact status vocabulary can include `evidence_only` where a stored typed pilot has real D02–D06
evidence (for example the committed thermal/water-ice typed areas). What is forbidden is:

- zero-filling a missing dimension;
- copying an Earth D02–D06 value onto a Moon/Mars AOI;
- labelling a stored evidence record as a scored similarity feature;
- returning an Earth `EPSG:4326` geometry for a planetary bbox.

## Interaction with the planetary map

After the job completes, the area is persisted in the typed-v2 tables and appears in
`GET /api/v1/planetary/map-features` for that body/bbox as a `typed_area` feature
(`feature_id` `typed:<area_id>`). It is never inserted into the Earth fine candidate index or the
Earth coarse index.

Use `links.map_features` from the analyze response, or query:

```bash
curl -sS \
  'http://127.0.0.1:8000/api/v1/planetary/map-features?body=Moon&bbox=19.8,-10.2,20.2,-9.8&limit=200'
```

The new overlay follows the existing #117 contract: planetocentric `bbox`, `center`, `geometry`,
`dimension_status`, `support_label`/availability and `detail_links`. See
[`PLANETARY_MAPS_API.md`](PLANETARY_MAPS_API.md).

## Reproduce locally

```bash
make seed
uv run pytest apps/api/tests/test_issue132_acceptance.py -q
bash scripts/issue132_acceptance.sh            # offline route smoke
bash scripts/issue132_acceptance.sh --public   # live public API check (network)
```

Offline tests use the committed manifests and fixtures with `ANALOG_AOI_INLINE=true`; they never
download a global raster. `ANALOG_PLANETARY_ALLOW_NETWORK` is `false` by default, so an AOI outside
a committed pilot returns `out_of_coverage` instead of reaching the network.
