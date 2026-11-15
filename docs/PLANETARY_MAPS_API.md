# Planetary basemap catalog, coordinates and overlays (Issue #117)

Backend contract for Moon/Mars global basemaps and real reference/AOI overlays. The React/Leaflet
map page is intentionally out of scope; this document is the handoff for the frontend collaborator.

## What is new

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/v1/planetary/maps?body=Moon\|Mars` | Versioned official layer catalog, tile templates, CRS, zoom, bounds, attribution, license, verification, science-coverage summary. |
| `GET` | `/api/v1/planetary/map-features?body=Moon\|Mars&bbox=...` | Real reference/AOI overlays with exact-ID links, D01–D06 coverage status and availability. |
| `GET` | `/api/v1/planetary/coordinate-contract` | Planetocentric coordinate rules, antimeridian/pole behavior, longitude conversions and Mars planetographic formula. |
| `GET` | `/api/v1/planetary/bodies` | Compact body metadata. |

Catalog version: `planetary-map-catalog-2026-11-15`.
Coordinate contract: `planetary-coordinates-1.0.0`.

## Official layers

All layers are served directly by NASA Trek. The API references the official service; it does not
download, cache or redistribute global rasters.

| Body | Layer id | Type | Format | EPSG | Zoom | Tile template |
|---|---|---|---|---|---|---|
| Moon | `moon-lro-wac-global-303ppd-v02` | imagery | `image/jpeg` | 104903 | 0–8 | `https://trek.nasa.gov/tiles/Moon/EQ/LRO_WAC_Mosaic_Global_303ppd_v02/1.0.0/default/default028mm/{TileMatrix}/{TileRow}/{TileCol}.jpg` |
| Moon | `moon-lro-lola-shaded-relief-128ppd-v04` | elevation | `image/png` | 104903 | 0–5 | `https://trek.nasa.gov/tiles/Moon/EQ/LRO_LOLA_ClrShade_Global_128ppd_v04/1.0.0/default/default028mm/{TileMatrix}/{TileRow}/{TileCol}.png` |
| Mars | `mars-viking-mola-basemap-232m` | imagery | `image/jpeg` | 104905 | 0–7 | `https://trek.nasa.gov/tiles/Mars/EQ/Mars_Viking_MDIM21_ClrMosaic_global_232m/1.0.0/default/default028mm/{TileMatrix}/{TileRow}/{TileCol}.jpg` |
| Mars | `mars-mgs-mola-shaded-relief-463m` | elevation | `image/jpeg` | 104905 | 0–7 | `https://trek.nasa.gov/tiles/Mars/EQ/Mars_MGS_MOLA_ClrShade_merge_global_463m/1.0.0/default/default028mm/{TileMatrix}/{TileRow}/{TileCol}.jpg` |

All four use `default028mm`, 256 × 256 tiles and the full 180°W–180°E / 90°S–90°N bounds from the
official WMTS capabilities. The catalog includes the complete tile matrix (`matrix_width`,
`matrix_height` per zoom) so a client does not need to guess it.

Attribution / license recorded per layer:

- Moon WAC: NASA / GSFC / Arizona State University, LRO Wide Angle Camera mosaic.
- Moon LOLA: NASA / GSFC / MIT, LRO Lunar Orbiter Laser Altimeter shaded relief.
- Mars MDIM21: NASA / JPL / USGS, Mars Viking MDIM21 color mosaic.
- Mars MOLA: NASA / GSFC / MOLA Science Team, Mars Global Surveyor MOLA shaded relief.
- License: NASA public domain; NASA Media Usage Guidelines
  (`https://www.nasa.gov/nasa-brand-center/images-and-media/`).

Verification performed on 2026-11-15 from this repository:

```text
Moon imagery z0    HTTP 200 image/jpeg
Moon imagery z8    HTTP 200 image/jpeg
Moon elevation z0  HTTP 200 image/png
Moon elevation z5  HTTP 200 image/png
Mars imagery z0    HTTP 200 image/jpeg
Mars imagery z7    HTTP 200 image/jpeg
Mars elevation z0  HTTP 200 image/jpeg
Mars elevation z7  HTTP 200 image/jpeg
```

Each layer response repeats this as `verification: {status: "verified", checked_at, http_status,
content_type, sample_tile_url, capabilities_http_status}`. If a provider later becomes unavailable,
the catalog must be updated to `status: "unavailable"` with a fallback; the API never silently
substitutes an Earth basemap.

## Coordinate contract

- **Frame:** Moon `IAU_MOON` / `lunar_planetocentric`; Mars `IAU_MARS` / `mars_planetocentric`.
- **Longitude:** positive east. API query/response domain is signed `[-180, 180)`; query with
  `bbox_lon_convention=360` to pass 0–360 values.
- **Latitude:** planetocentric. Mars is an ellipsoid `a=3396190 m`, `b=3376200 m`. Planetographic
  latitude is a different quantity:
  `tan(lat_pg) = (a²/b²) · tan(lat_pc)`. The contract endpoint returns a computed Mars example.
- **Never Earth WGS84:** the API does not return a `crs: "EPSG:4326"` geometry. Every feature carries
  the body frame; `bbox_crs` explicitly says `planetocentric longitude/latitude degrees in the
  declared body frame; never Earth EPSG:4326`.
- **Antimeridian:** `west > east` means the box crosses 180°. Both parts are searched. For example,
  `bbox=120,-90,-150,-85` returns the Moon Site07 reference at 123.55°E and Shackleton references
  near 157°W.
- **Poles:** latitude is bounded to `[-90, 90]`. Near-polar AOIs may span all longitudes; the response
  reports the actual envelope and `crosses_antimeridian` rather than clamping to a rectangle.
- **GeoJSON:** coordinates are `[longitude, latitude]` in degrees, but the CRS is the declared
  planetary body frame. A GeoJSON consumer must not assume OSM/Web-Mercator.

Example contract answers:

```text
normalize_longitude(180.0) = -180.0
to_360(-137.09597807)      = 222.90402193
Mars 18.220253° planetocentric → 18.137...° planetographic
bbox "120,-90,-150,-85"    → west=120, east=-150, longitude_span=90, crosses_antimeridian=true
```

## Overlay examples

### Mars Jezero

```bash
curl -sS 'https://api.貓熊.台灣/api/v1/planetary/map-features?body=Mars&bbox=76,17,79,20&limit=200'
```

Returns real records including:

```text
reference:mars-jezero-ctx-window-01   (D01 fine reference, 5 features)
typed:mars-jezero-ctx-window-01       (same area_id exact join)
  typed_dimensions: D02? D03/D04/D05 as stored
  D05 status: present_evidence
  D06 status: absent, record_count=0
```

The reference and typed AOI are linked by exact `area_id`, not proximity. Both are kept because they
are different products (D01 reference metrics vs typed observations).

### Moon reference

```bash
curl -sS 'https://api.貓熊.台灣/api/v1/planetary/map-features?body=Moon&bbox=122,-89,125,-88'
```

Returns `reference:moon-lola-site07-roi1`, the nearby LP H2 typed cell, the Mini-RF typed area and
the thermal typed area. The typed areas have different ids and therefore remain separate; the
response says `reference_only_no_proximity_join` rather than inventing a match.

### Empty and pagination

```bash
# Explicit empty result; no Earth records are mixed in.
curl -sS 'https://api.貓熊.台灣/api/v1/planetary/map-features?body=Mars&bbox=0,0,1,1'

# Pagination and type filter.
curl -sS 'https://api.貓熊.台灣/api/v1/planetary/map-features?body=Mars&bbox=76,17,79,20&limit=2&offset=2&feature_type=typed_area'
```

Invalid body, latitude, bbox ordering or dimension returns a structured 422 with codes such as
`unsupported_body`, `invalid_bbox_latitude`, `invalid_bbox`, `invalid_dimension`. Empty results are
`total=0` with an empty `features` array, never a placeholder.

## Basemap extent vs scientific coverage

The catalog separates two claims:

- **basemap extent:** global, because the official tile service covers the whole body;
- **scientific coverage:** curated references/AOIs and typed observations only, explicitly labelled
  `curated_subset_not_global`.

The map-features response contains a `science_coverage_summary` with real reference counts, typed
area counts, typed observation counts and per-dimension counts for that body. Each feature contains
`dimension_status` for D01–D06:

```json
{
  "D06": {
    "status": "absent",
    "record_count": 0,
    "value_origin": null,
    "comparability_gate": "No stored observation for this exact area and dimension; no zero fill."
  }
}
```

`status` is `present` for D01 references, `present_evidence` for typed records, or `absent`. A typed
record is never called a scored feature unless the #116 registry exposes a gated similarity role.

## Frontend handoff

- Read layers from `GET /planetary/maps?body=...`; never hardcode a tile provider or zoom range.
- Use the returned `tile_template`, `tile_matrix_set`, `zoom`, `tile_matrix`, `format` and `bounds`.
- Use `center`/`bbox`/`geometry` from `map-features`; do not feed them to an Earth OSM layer.
- Read `dimension_status` to show "no data" separately from "not yet scored".
- Link `typed_area_id`/`reference_id` to `/api/v1/typed/areas/{id}` or the #116 search.
- Generated TypeScript types: `apps/web/src/api/schema.d.ts`.

## Reproduce locally

```bash
make seed
uv run pytest apps/api/tests/test_planetary_api.py -q
uv run python scripts/planetary_map_acceptance.py   # optional live HTTP check (network)
bash scripts/verify.sh --ci
```
