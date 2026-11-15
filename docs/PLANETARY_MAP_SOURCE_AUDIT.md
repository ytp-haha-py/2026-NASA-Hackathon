# Planetary basemap source audit (Issue #117)

Status: **four NASA Trek WMTS basemap layers and two USGS Astrogeology WMS alternatives
verified with live HTTP requests on 2026-11-15.** This audit is display-only. It adds no
science observation, downloads no global raster, and makes no global-coverage claim.

Machine-readable catalog: [`data-sources/planetary-map-layers.json`](data-sources/planetary-map-layers.json).
Rerunnable check: [`../scripts/check_planetary_trek_sources.py`](../scripts/check_planetary_trek_sources.py).

## Scope and method

The audit answers three questions for the Moon/Mars basemap endpoints:

1. Does each provider layer exist right now, at the exact URL, and what does it return?
2. What coordinate frame, longitude convention and latitude convention does it declare?
3. What is a concrete fallback if a provider is unavailable, and what must never be
   substituted (Earth OSM/Web Mercator) or inferred (science values from a rendered image)?

Evidence came from direct HTTPS requests: WMTS `GetCapabilities`, WMTS REST tiles, USGS WMS
`GetCapabilities`/`GetMap`, and `HEAD` on the fallback GeoTIFF objects. Tile bodies were read
in full only at the min-zoom tile and the three max-zoom edge tiles of each layer; every other
tile read, including all mid-zoom probes and every checker run, used `Range: bytes=0-1023` and
was validated by JPEG/PNG magic bytes, decoded `256x256` dimensions, content type, and CORS
header. No raster was saved to the repository.

The four layers audited:

| Catalog id | Body | Role | Product label |
|---|---|---|---|
| `moon_lro_wac_mosaic_global_303ppd_v02` | Moon | imagery basemap | `LRO_WAC_Mosaic_Global_303ppd_v02` |
| `moon_lro_lola_clrshade_global_128ppd_v04` | Moon | elevation (rendered hillshade) | `LRO_LOLA_ClrShade_Global_128ppd_v04` |
| `mars_viking_mdim21_clrmosaic_global_232m` | Mars | imagery basemap | `Mars_Viking_MDIM21_ClrMosaic_global_232m` |
| `mars_mgs_mola_clrshade_merge_global_463m` | Mars | elevation (rendered hillshade) | `Mars_MGS_MOLA_ClrShade_merge_global_463m` |

All four are OGC WMTS 1.0.0 RESTful services under
`https://trek.nasa.gov/tiles/{Body}/EQ/{product}/1.0.0/`, style `default`, tile matrix set
`default028mm`, 256x256 tiles, bounds `-180..180 / -90..90`, and CORS
`Access-Control-Allow-Origin: *`.

## Verified sources

### Capabilities endpoints

| Layer | Capabilities URL | HTTP | Content-Type | Bytes |
|---|---|---|---|---|
| Moon WAC | <https://trek.nasa.gov/tiles/Moon/EQ/LRO_WAC_Mosaic_Global_303ppd_v02/1.0.0/WMTSCapabilities.xml> | 200 | `application/xml` | 7,241 |
| Moon LOLA | <https://trek.nasa.gov/tiles/Moon/EQ/LRO_LOLA_ClrShade_Global_128ppd_v04/1.0.0/WMTSCapabilities.xml> | 200 | `application/xml` | 6,466 |
| Mars Viking | <https://trek.nasa.gov/tiles/Mars/EQ/Mars_Viking_MDIM21_ClrMosaic_global_232m/1.0.0/WMTSCapabilities.xml> | 200 | `application/xml` | 7,016 |
| Mars MOLA | <https://trek.nasa.gov/tiles/Mars/EQ/Mars_MGS_MOLA_ClrShade_merge_global_463m/1.0.0/WMTSCapabilities.xml> | 200 | `application/xml` | 7,040 |

Each document declares exactly one `Layer`, one `Style` (`default`), one `TileMatrixSet`
(`default028mm`), one `ResourceURL`, and an `ows:BoundingBox` in the planetary CRS.

### Layer contract

| Field | Moon WAC | Moon LOLA | Mars Viking | Mars MOLA |
|---|---|---|---|---|
| Format | `image/jpeg` | `image/png` | `image/jpeg` | `image/jpeg` |
| Extension | `.jpg` | `.png` | `.jpg` | `.jpg` |
| Tile matrix | `default028mm` | `default028mm` | `default028mm` | `default028mm` |
| Zoom range | 0-8 | 0-5 | 0-7 | 0-7 |
| Zoom 0 matrix | 2 x 1 | 2 x 1 | 2 x 1 | 2 x 1 |
| Max-zoom matrix | 512 x 256 | 64 x 32 | 256 x 128 | 256 x 128 |
| Tile size | 256x256 | 256x256 | 256x256 | 256x256 |
| Declared CRS | `urn:ogc:def:crs:EPSG::104903` | `urn:ogc:def:crs:EPSG::104903` | `urn:ogc:def:crs:EPSG::104905` | `urn:ogc:def:crs:EPSG::104905` |
| CRS resolved | ESRI:104903 `GCS_Moon_2000` | ESRI:104903 `GCS_Moon_2000` | ESRI:104905 `GCS_Mars_2000` | ESRI:104905 `GCS_Mars_2000` |
| Bounds | -180,-90,180,90 | -180,-90,180,90 | -180,-90,180,90 | -180,-90,180,90 |
| Top-left corner | -180, 90 | -180, 90 | -180, 90 | -180, 90 |
| Max zoom m/px (derived) | 83.29 | 666.28 | 325.61 | 325.61 |
| Native m/px | 100.08 (303 ppd) | 236.90 (128 ppd) | 231.54 (256 ppd) | 463.08 (128 ppd) |
| Max zoom vs native | 1.20x finer | 2.81x coarser | 1.41x coarser | 1.42x finer |

Published tile template (verbatim, note the provider's double slash after `1.0.0`):

```text
https://trek.nasa.gov/tiles/Moon/EQ/LRO_WAC_Mosaic_Global_303ppd_v02/1.0.0//{Style}/{TileMatrixSet}/{TileMatrix}/{TileRow}/{TileCol}.jpg
https://trek.nasa.gov/tiles/Moon/EQ/LRO_LOLA_ClrShade_Global_128ppd_v04/1.0.0//{Style}/{TileMatrixSet}/{TileMatrix}/{TileRow}/{TileCol}.png
https://trek.nasa.gov/tiles/Mars/EQ/Mars_Viking_MDIM21_ClrMosaic_global_232m/1.0.0//{Style}/{TileMatrixSet}/{TileMatrix}/{TileRow}/{TileCol}.jpg
https://trek.nasa.gov/tiles/Mars/EQ/Mars_MGS_MOLA_ClrShade_merge_global_463m/1.0.0//{Style}/{TileMatrixSet}/{TileMatrix}/{TileRow}/{TileCol}.jpg
```

The `//` collapses on the wire; both the double-slash template and the single-slash form
returned HTTP 200. The catalog stores the normalized single-slash form for clients and keeps
the verbatim template as evidence.

### Tile evidence

Concrete normalized URLs and responses:

| Layer | Tile | URL path after `/1.0.0/` | HTTP | Type | Bytes |
|---|---|---|---|---|---|
| Moon WAC | min zoom | `default/default028mm/0/0/0.jpg` | 200 | `image/jpeg` | 49,475 |
| Moon WAC | mid zoom | `default/default028mm/4/8/16.jpg` | 206 | `image/jpeg` | 44,266 total |
| Moon WAC | max NW | `default/default028mm/8/0/0.jpg` | 200 | `image/jpeg` | 13,749 |
| Moon WAC | max SE | `default/default028mm/8/255/511.jpg` | 200 | `image/jpeg` | 15,467 |
| Moon WAC | max antimeridian | `default/default028mm/8/0/511.jpg` | 200 | `image/jpeg` | 10,412 |
| Moon WAC | beyond max | `default/default028mm/9/0/0.jpg` | 404 | `text/html` | 196 |
| Moon LOLA | min zoom | `default/default028mm/0/0/0.png` | 200 | `image/png` | 152,697 |
| Moon LOLA | mid zoom | `default/default028mm/2/2/4.png` | 206 | `image/png` | 171,502 total |
| Moon LOLA | max NW | `default/default028mm/5/0/0.png` | 200 | `image/png` | 82,650 |
| Moon LOLA | max SE | `default/default028mm/5/31/63.png` | 200 | `image/png` | 85,208 |
| Moon LOLA | max antimeridian | `default/default028mm/5/0/63.png` | 200 | `image/png` | 83,095 |
| Moon LOLA | beyond max | `default/default028mm/6/0/0.png` | 404 | `text/html` | 196 |
| Mars Viking | min zoom | `default/default028mm/0/0/0.jpg` | 200 | `image/jpeg` | 52,978 |
| Mars Viking | mid zoom | `default/default028mm/3/4/8.jpg` | 206 | `image/jpeg` | 59,207 total |
| Mars Viking | max NW | `default/default028mm/7/0/0.jpg` | 200 | `image/jpeg` | 11,299 |
| Mars Viking | max SE | `default/default028mm/7/127/255.jpg` | 200 | `image/jpeg` | 17,612 |
| Mars Viking | max antimeridian | `default/default028mm/7/0/255.jpg` | 200 | `image/jpeg` | 10,688 |
| Mars Viking | beyond max | `default/default028mm/8/0/0.jpg` | 404 | `text/html` | 196 |
| Mars MOLA | min zoom | `default/default028mm/0/0/0.jpg` | 200 | `image/jpeg` | 62,976 |
| Mars MOLA | mid zoom | `default/default028mm/3/4/8.jpg` | 206 | `image/jpeg` | 76,017 total |
| Mars MOLA | max NW | `default/default028mm/7/0/0.jpg` | 200 | `image/jpeg` | 20,006 |
| Mars MOLA | max SE | `default/default028mm/7/127/255.jpg` | 200 | `image/jpeg` | 13,652 |
| Mars MOLA | max antimeridian | `default/default028mm/7/0/255.jpg` | 200 | `image/jpeg` | 16,999 |
| Mars MOLA | beyond max | `default/default028mm/8/0/0.jpg` | 404 | `text/html` | 196 |

The checker decodes the first 1 KiB of every `200`/`206` tile to `256x256`, matching
`TileWidth`/`TileHeight`. The `206` rows above report the provider's total object size from
`Content-Range`. The `404` beyond the published maximum is intentional evidence that the zoom
range is still 0-8 / 0-5 / 0-7, not a larger matrix that the catalog missed.

### Availability

Verified live on 2026-11-15: all four capability documents and all sampled tiles responded.
This is a point-in-time check, not an SLA. The rerunnable checker re-tests capabilities, three
zoom levels, both max-zoom corners, the antimeridian column, CORS, and the `max+1` 404.

## Attribution and license

| Layer | Attribution | License status |
|---|---|---|
| Moon WAC | NASA / LRO / LROC WAC (Arizona State University); mosaic served by NASA Moon Trek | not stated per layer |
| Moon LOLA | NASA / LRO / LOLA (NASA GSFC, MIT); color hillshade served by NASA Moon Trek | not stated per layer |
| Mars Viking | NASA / Viking Orbiter Imaging; USGS MDIM 2.1; served by NASA Mars Trek | not stated per layer |
| Mars MOLA | NASA / MGS / MOLA (NASA GSFC); served by NASA Mars Trek | not stated per layer |

The WMTS documents and the Trek FGDC records contain no licence field. The only usage
reference found is the NASA media guideline
(<https://www.nasa.gov/nasa-brand-center/images-and-media/>), which states that NASA content
is generally not subject to copyright in the United States and that the NASA insignia,
logotype and identifiers are not public domain. This audit therefore records the licence as
`not_stated_per_layer` and does not claim a redistribution licence for the imagery.

The Mars Trek FGDC record for the Viking layer is a placeholder: `Originator`, `Title`,
`Abstract` and `Use_Constraints` are `TBD`, and `Access_Constraints` is `PRIVATE`. It is not
used as provenance. Authoritative Mars coordinates come from PDS labels (below).

## Alternative official sources

### NASA Trek WMTS API documentation

Verified HTTP 200:

- <https://trek.nasa.gov/tiles/apidoc>
- <https://trek.nasa.gov/tiles/apidoc/trekAPI.html?body=moon>
- <https://trek.nasa.gov/tiles/apidoc/trekAPI.html?body=mars>
- <https://trek.nasa.gov/tiles/apidoc/config/config.json> (10,233 bytes)

The API document confirms the REST template, that zoom 0 has two columns and one row and
each level doubles, and that a missing tile means "no data coverage in that area". The config
declares Moon equirectangular as `urn:ogc:def:crs:EPSG::104903` and Mars equirectangular as
`urn:ogc:def:crs:IAU2000::104905`, and its zoom-0 samples point at the same tiles audited
here. Note the authority-string inconsistency with the WMTS documents, which use
`urn:ogc:def:crs:EPSG::104905` for Mars.

### USGS Astrogeology WMS (Moon)

- Capabilities: <https://planetarymaps.usgs.gov/cgi-bin/mapserv?map=/maps/earth/moon_simp_cyl.map&SERVICE=WMS&REQUEST=GetCapabilities&VERSION=1.1.1>
  → HTTP 200, `application/vnd.ogc.wms_xml`, 18,184 bytes.
- Layers include `LROC_WAC`, `LOLA_color`, `LOLA_bw`, `LOLA_Kaguya_Shade`.
- CRS: `IAU_2015:30100` (plus an `EPSG:4326` compatibility alias).
- GetMap probe (`LROC_WAC`, 64x32, `IAU_2015:30100`, bbox `-180,-90,180,90`)
  → HTTP 200, `image/png`, 119 bytes, decoded 64x32.
- Layer bbox is advertised as `-180,-89.999974,359.999921,90`, i.e. a 0..360 layout,
  while GetMap accepts `-180..180`. The longitude direction is not declared.

A request with an unspecified WMS version or `VERSION=1.3.0` returned HTTP 502 from this host
during the audit; `VERSION=1.1.1` returned 200 and is what the checker uses.

### USGS Astrogeology WMS (Mars)

- Capabilities: <https://planetarymaps.usgs.gov/cgi-bin/mapserv?map=/maps/mars/mars_simp_cyl.map&SERVICE=WMS&REQUEST=GetCapabilities&VERSION=1.1.1>
  → HTTP 200, `application/vnd.ogc.wms_xml`, 20,698 bytes.
- Layers include `MDIM21`, `MOLA_color`, `MOLA_bw`, `THEMIS`, `HRSC_MOLA_Blend_Hillshade`.
- CRS: `IAU_2015:49900` (plus an `EPSG:4326` compatibility alias).
- GetMap probe (`MDIM21`, 64x32, `IAU_2015:49900`, bbox `-180,-90,180,90`)
  → HTTP 200, `image/jpeg`, 295 bytes, decoded 64x32.
- `MDIM21` bbox is advertised as `-180,-90,360,90`; longitude direction is not declared.
- The WMS `EPSG:4326` entry is a MapServer compatibility alias. It does not make the layer
  Earth WGS84 and must not be used to derive distances or areas.

USGS licence text could not be captured: `usgs.gov` policy pages returned HTTP 403 from this
environment, and the WMS capabilities/ISO metadata contain no use constraint. The catalog
records the USGS layers as `not_stated` rather than assuming public domain.

## Coordinate contract

### Frames and CRS declarations

| Body | NAIF | Frame | Reference radius | Declared CRS |
|---|---|---|---|---|
| Moon | 301 | MOON_ME (mean Earth/polar axis, DE421) | 1,737,400 m, sphere | `urn:ogc:def:crs:EPSG::104903` |
| Mars | 499 | IAU2000 Mars body-fixed rotating | 3,396,190 m equatorial / 3,376,200 m polar | `urn:ogc:def:crs:EPSG::104905` |

Verified from PDS labels:

- Moon SLDEM2015 label: `A_AXIS_RADIUS = 1737.4 <km>`,
  `POSITIVE_LONGITUDE_DIRECTION = "EAST"`,
  `COORDINATE_SYSTEM_NAME = "MEAN EARTH/POLAR AXIS OF DE421"`.
  Source: <https://pds-geosciences.wustl.edu/lro/lro-l-lola-3-rdr-v1/lrolol_1xxx/data/sldem2015/global/float_img/sldem2015_128_60s_60n_000_360_float.lbl>.
- Mars MOLA MEGDR label: `A_AXIS_RADIUS = 3396.0 <KM>`,
  `POSITIVE_LONGITUDE_DIRECTION = "EAST"`,
  `COORDINATE_SYSTEM_TYPE = "BODY-FIXED ROTATING"`,
  `COORDINATE_SYSTEM_NAME = "PLANETOCENTRIC"`.
  Source: <https://pds-geosciences.wustl.edu/mgs/urn-nasa-pds-mgs_mola_topography_derived/meg128/megt44n000hb.lbl>.

The MOLA product label uses a **3,396.0 km sphere** for its map projection, while the
declared CRS (`ESRI:104905`) is an ellipsoid with a=3,396.19 km and b=3,376.2 km. Do not
substitute one radius for the other in distance or reprojection math; record which radius a
derived value used.

### The `EPSG::104903/104905` token is an ESRI code

`104903` and `104905` are ESRI authority codes (`GCS_Moon_2000`, `GCS_Mars_2000`), not codes
in the EPSG dataset. The WMTS documents place them inside `urn:ogc:def:crs:EPSG::...`, and the
Trek API config uses `urn:ogc:def:crs:IAU2000::104905` for Mars. Three authority strings, one
pair of CRS definitions. Clients must resolve by code plus body and must not treat the
authority token as proof of an EPSG registration.

Resolved definitions (from the ESRI/epsg.io JSON):

- `104903`: geographic, `Moon_2000_IAU_IAG`, sphere radius 1,737,400 m, longitude axis east.
- `104905`: geographic, `Mars_2000_IAU_IAG`, ellipsoid a=3,396,190 m, 1/f=169.894447,
  longitude axis east.

### Latitude: planetocentric vs planetographic

- Moon: the body is a sphere (flattening 0), so planetocentric and planetographic latitude
  are identical for basemap purposes. No measurable ambiguity.
- Mars: PDS MOLA declares `PLANETOCENTRIC`. `ESRI:104905` is a geodetic CRS on the IAU
  ellipsoid, whose geodetic latitude is planetographic. The difference is small but not
  negligible:

| Planetocentric latitude | Planetographic latitude |
|---:|---:|
| 0 deg | 0.0000 deg |
| 30 deg | 30.2938 deg |
| 45 deg | 45.3382 deg |
| 60 deg | 60.2921 deg |
| 75 deg | 75.1683 deg |
| 89 deg | 89.0117 deg |

At 45 deg the difference is 0.3382 deg, about 20 km on the surface. The WMTS capabilities do
not declare which convention the rendered tile grid carries. The catalog therefore records
the Mars latitude convention as **unverified** and forbids relabeling between conventions
without an explicit transform.

### Longitude direction and range

- Verified: the PDS Moon and Mars products used here declare positive longitude **east**.
- The WMTS grid is **-180..180** with column 0 starting at -180 and the last column ending at
  +180.
- The provider-native products store **0..360**: the Moon SLDEM label has
  `WESTERNMOST_LONGITUDE = 0`, `EASTERNMOST_LONGITUDE = 360, CENTER_LONGITUDE = 180`; the Mars
  MOLA tile label has 0..90 per tile with `CENTER_LONGITUDE = 180`; the USGS WMS advertises a
  0..360 bbox. The tile grid is a re-wrapped copy, not a statement that the archive stores
  negative longitudes.
- Planetographic longitude on Mars is measured **positive west** under the IAU convention,
  the opposite axis direction from planetocentric east longitude. That is a sign convention,
  not a formatting choice. A product that declares planetographic west must be sign-flipped
  explicitly and must record which convention it used.
- The WMTS `ows:WGS84BoundingBox` element (`crs="urn:ogc:def:crs:OGC:2:84"`) is a WMTS
  convenience label. Its values are planetary degrees, not Earth WGS84.

### Tile grid, seam and poles

- Zoom 0 is 2 columns x 1 row of 256x256 tiles: a 512x256 canvas for 360x180 degrees. Each
  zoom doubles both dimensions.
- Rows increase southward (row 0 is the north edge). Columns increase eastward (column 0 is
  the -180 edge).
- The seam is at **+/-180**, not at 0. Column 0 covers `[-180, -180+step)` and the last column
  covers `[180-step, 180]`. Both max-zoom antimeridian tiles returned HTTP 200.
- The poles are included by stretching the top and bottom equirectangular rows to +/-90.
  These four layers publish **only** the equirectangular `default028mm` matrix set. There is
  no polar-stereographic matrix set and no polar cap tile. Equirectangular distortion is
  1/cos(latitude): 2.0x at 60 deg, 11.5x at 85 deg, 57.3x at 89 deg.
- `ScaleDenominator` is **Earth-referenced**. At zoom 0, `2.7922763629807472e8` times the OGC
  0.28 mm pixel size is 78,183.7 m/px, which implies a sphere of R=6,370,984 m, i.e. Earth.
  A client that maps standard Web-Mercator zoom levels to planetary ground resolution will be
  wrong by the body/Earth radius ratio (3.67x for the Moon, 1.88x for Mars).

### Why EPSG:4326, EPSG:3857 and OSM are invalid

| Assumption | Earth value | Moon | Mars |
|---|---|---|---|
| Reference radius | 6,378,137 m (WGS84 a) | 1,737,400 m | 3,396,190 m |
| Flattening | 1/298.257223563 | 0 | 1/169.894447 |
| One degree of latitude | ~111.1 km | 30.32 km | 59.27 km |
| Vertical datum | WGS84 ellipsoid / geoid | 1,737.4 km sphere | IAU ellipsoid / areoid |

- `EPSG:4326` declares an Earth geodetic datum, an Earth ellipsoid and ellipsoidal/orthometric
  heights. The same numeric degree denotes a ground distance 3.67x larger than on the Moon and
  1.88x larger than on Mars, and the vertical datum does not exist on either body.
- `EPSG:3857` (Web Mercator) hard-codes the Earth radius, clamps latitude to +/-85.0511 deg,
  and defines z/x/y tiles with 2^z columns for Earth. For a planetary layer it misplaces
  coordinates by thousands of kilometres and hides the pole rows.
- OSM tiles are Earth imagery on the Earth tiling scheme. They are never a fallback for a
  Moon or Mars basemap, including during an outage.
- The WMS `EPSG:4326` alias seen at USGS is a compatibility label only; it does not make the
  layer Earth WGS84 and must not be used for distance, area or reprojection.

## Basemap vs science coverage

These layers are **rendered pictures**. They are safe for display, navigation context and AOI
selection. They are not measurements.

| Use | Allowed | Reason |
|---|---|---|
| Show a Moon/Mars basemap behind an AOI | yes | WMTS tiles verified, CORS enabled |
| Label the visible mosaic as provider-advertised global extent | yes, with the provider name | capabilities advertise -180..180 / -90..90 |
| Read elevation from LOLA/MOLA color hillshade | no | JPEG/PNG rendering has no values, units or uncertainty |
| Feed a basemap pixel into similarity / terrain metrics | no | not the shared 90 m analysis support, not a DEM |
| Claim global science coverage or a global survey | no | visual mosaic only; source resolution and coverage vary |
| Substitute OSM/Earth imagery when Trek is down | no | wrong body, wrong datum, wrong tiling |

Concrete resolution limits (derived from the verified matrix dimensions and the IAU radius):

- Moon WAC max zoom 8 is ~83.3 m/px at the equator, finer than the 303 ppd (~100.1 m/px)
  source, so the deepest zoom is interpolated.
- Moon LOLA max zoom 5 is ~666.3 m/px, 2.81x coarser than the 128 ppd (~236.9 m/px) source;
  the tile set never exposes native DEM resolution.
- Mars Viking max zoom 7 is ~325.6 m/px, 1.41x coarser than the ~231.5 m/px source.
- Mars MOLA max zoom 7 is ~325.6 m/px, 1.42x finer than the ~463.1 m/px source.

## Availability and fallbacks

| Trigger | Fallback | Constraint |
|---|---|---|
| Trek WMTS capabilities or tiles not 200 | USGS Astrogeology WMS of the same body/role: `LROC_WAC`, `LOLA_color`, `MDIM21`, `MOLA_color` | visual only; no zoom matrix; do not infer coordinates from the WMS bbox |
| Both Trek and USGS WMS unavailable | bounded GDAL window read from the USGS GeoTIFF objects | objects are 3-13 GB; never download whole for basemap display |
| No planetary provider reachable | return explicit `unavailable`, render without imagery | never substitute Earth OSM/WGS84/Web-Mercator; never zero-fill |

Fallback GeoTIFF objects verified with `HEAD` (302 to the USGS S3 origin, final HTTP 200,
`image/tiff`):

| Body | Object | Bytes |
|---|---|---|
| Moon | `Lunar_LRO_LROC-WAC_Mosaic_global_100m_June2013.tif` | 5,959,263,751 |
| Mars | `Mars_Viking_MDIM21_ClrMosaic_global_232m.tif` | 12,742,411,029 |
| Mars | `Mars_MGS_MOLA_ClrShade_merge_global_463m.tif` | 3,185,603,207 |

Base URL: `https://planetarymaps.usgs.gov/mosaic/`, redirecting to
`https://asc-pds-services.s3.us-west-2.amazonaws.com/mosaic/`.

A missing tile inside a published matrix is `out_of_coverage`, not a zero value. A non-200
capability document is `unavailable`, not an empty layer. The API response should carry the
active provider id and an explicit availability state so the UI can show which source is
being displayed.

## Reproducing this audit

```bash
python3 scripts/check_planetary_trek_sources.py
python3 scripts/check_planetary_trek_sources.py --json
python3 scripts/check_planetary_trek_sources.py --skip-alternatives
```

The checker reads the catalog, not a second copy of the metadata, so a catalog edit and a
provider change are both detected. It exits non-zero on any capability, metadata, tile,
dimension, CORS, zoom-range or `max+1` regression.

### Observed checkpoint output (2026-11-15)

Command: `python3 scripts/check_planetary_trek_sources.py`

```text
PASS  moon_lro_wac_mosaic_global_303ppd_v02: capabilities HTTP 200
PASS  moon_lro_wac_mosaic_global_303ppd_v02: capabilities XML content type
PASS  moon_lro_wac_mosaic_global_303ppd_v02: capabilities CORS wildcard
PASS  moon_lro_wac_mosaic_global_303ppd_v02: layer identifier present once
PASS  moon_lro_wac_mosaic_global_303ppd_v02: advertised format
PASS  moon_lro_wac_mosaic_global_303ppd_v02: default style
PASS  moon_lro_wac_mosaic_global_303ppd_v02: tile matrix set link
PASS  moon_lro_wac_mosaic_global_303ppd_v02: bbox is -180,-90,180,90 (+/-0.01 deg)
PASS  moon_lro_wac_mosaic_global_303ppd_v02: REST tile template
PASS  moon_lro_wac_mosaic_global_303ppd_v02: tile matrix set exists
PASS  moon_lro_wac_mosaic_global_303ppd_v02: supported CRS
PASS  moon_lro_wac_mosaic_global_303ppd_v02: zoom range 0-8
PASS  moon_lro_wac_mosaic_global_303ppd_v02: zoom 0 matrix is 2x1
PASS  moon_lro_wac_mosaic_global_303ppd_v02: top-left corner is -180,90 at every zoom
PASS  moon_lro_wac_mosaic_global_303ppd_v02: 256x256 tiles at every zoom
PASS  moon_lro_wac_mosaic_global_303ppd_v02: matrix dimensions double per zoom
PASS  moon_lro_wac_mosaic_global_303ppd_v02: min zoom z0/0/0 HTTP 200/206
PASS  moon_lro_wac_mosaic_global_303ppd_v02: min zoom z0/0/0 content type image/jpeg
PASS  moon_lro_wac_mosaic_global_303ppd_v02: min zoom z0/0/0 decodes as 256x256
PASS  moon_lro_wac_mosaic_global_303ppd_v02: min zoom z0/0/0 CORS wildcard
PASS  moon_lro_wac_mosaic_global_303ppd_v02: mid zoom z4/8/16 HTTP 200/206
PASS  moon_lro_wac_mosaic_global_303ppd_v02: mid zoom z4/8/16 content type image/jpeg
PASS  moon_lro_wac_mosaic_global_303ppd_v02: mid zoom z4/8/16 decodes as 256x256
PASS  moon_lro_wac_mosaic_global_303ppd_v02: mid zoom z4/8/16 CORS wildcard
PASS  moon_lro_wac_mosaic_global_303ppd_v02: max zoom north-west z8/0/0 HTTP 200/206
PASS  moon_lro_wac_mosaic_global_303ppd_v02: max zoom north-west z8/0/0 content type image/jpeg
PASS  moon_lro_wac_mosaic_global_303ppd_v02: max zoom north-west z8/0/0 decodes as 256x256
PASS  moon_lro_wac_mosaic_global_303ppd_v02: max zoom north-west z8/0/0 CORS wildcard
PASS  moon_lro_wac_mosaic_global_303ppd_v02: max zoom south-east z8/255/511 HTTP 200
PASS  moon_lro_wac_mosaic_global_303ppd_v02: max zoom south-east z8/255/511 image content type
PASS  moon_lro_wac_mosaic_global_303ppd_v02: max zoom south-east z8/255/511 non-empty tile
PASS  moon_lro_wac_mosaic_global_303ppd_v02: max zoom antimeridian z8/0/511 HTTP 200
PASS  moon_lro_wac_mosaic_global_303ppd_v02: max zoom antimeridian z8/0/511 image content type
PASS  moon_lro_wac_mosaic_global_303ppd_v02: max zoom antimeridian z8/0/511 non-empty tile
PASS  moon_lro_wac_mosaic_global_303ppd_v02: zoom 9 is not published
PASS  moon_lro_lola_clrshade_global_128ppd_v04: capabilities HTTP 200
PASS  moon_lro_lola_clrshade_global_128ppd_v04: capabilities XML content type
PASS  moon_lro_lola_clrshade_global_128ppd_v04: capabilities CORS wildcard
PASS  moon_lro_lola_clrshade_global_128ppd_v04: layer identifier present once
PASS  moon_lro_lola_clrshade_global_128ppd_v04: advertised format
PASS  moon_lro_lola_clrshade_global_128ppd_v04: default style
PASS  moon_lro_lola_clrshade_global_128ppd_v04: tile matrix set link
PASS  moon_lro_lola_clrshade_global_128ppd_v04: bbox is -180,-90,180,90 (+/-0.01 deg)
PASS  moon_lro_lola_clrshade_global_128ppd_v04: REST tile template
PASS  moon_lro_lola_clrshade_global_128ppd_v04: tile matrix set exists
PASS  moon_lro_lola_clrshade_global_128ppd_v04: supported CRS
PASS  moon_lro_lola_clrshade_global_128ppd_v04: zoom range 0-5
PASS  moon_lro_lola_clrshade_global_128ppd_v04: zoom 0 matrix is 2x1
PASS  moon_lro_lola_clrshade_global_128ppd_v04: top-left corner is -180,90 at every zoom
PASS  moon_lro_lola_clrshade_global_128ppd_v04: 256x256 tiles at every zoom
PASS  moon_lro_lola_clrshade_global_128ppd_v04: matrix dimensions double per zoom
PASS  moon_lro_lola_clrshade_global_128ppd_v04: min zoom z0/0/0 HTTP 200/206
PASS  moon_lro_lola_clrshade_global_128ppd_v04: min zoom z0/0/0 content type image/png
PASS  moon_lro_lola_clrshade_global_128ppd_v04: min zoom z0/0/0 decodes as 256x256
PASS  moon_lro_lola_clrshade_global_128ppd_v04: min zoom z0/0/0 CORS wildcard
PASS  moon_lro_lola_clrshade_global_128ppd_v04: mid zoom z2/2/4 HTTP 200/206
PASS  moon_lro_lola_clrshade_global_128ppd_v04: mid zoom z2/2/4 content type image/png
PASS  moon_lro_lola_clrshade_global_128ppd_v04: mid zoom z2/2/4 decodes as 256x256
PASS  moon_lro_lola_clrshade_global_128ppd_v04: mid zoom z2/2/4 CORS wildcard
PASS  moon_lro_lola_clrshade_global_128ppd_v04: max zoom north-west z5/0/0 HTTP 200/206
PASS  moon_lro_lola_clrshade_global_128ppd_v04: max zoom north-west z5/0/0 content type image/png
PASS  moon_lro_lola_clrshade_global_128ppd_v04: max zoom north-west z5/0/0 decodes as 256x256
PASS  moon_lro_lola_clrshade_global_128ppd_v04: max zoom north-west z5/0/0 CORS wildcard
PASS  moon_lro_lola_clrshade_global_128ppd_v04: max zoom south-east z5/31/63 HTTP 200
PASS  moon_lro_lola_clrshade_global_128ppd_v04: max zoom south-east z5/31/63 image content type
PASS  moon_lro_lola_clrshade_global_128ppd_v04: max zoom south-east z5/31/63 non-empty tile
PASS  moon_lro_lola_clrshade_global_128ppd_v04: max zoom antimeridian z5/0/63 HTTP 200
PASS  moon_lro_lola_clrshade_global_128ppd_v04: max zoom antimeridian z5/0/63 image content type
PASS  moon_lro_lola_clrshade_global_128ppd_v04: max zoom antimeridian z5/0/63 non-empty tile
PASS  moon_lro_lola_clrshade_global_128ppd_v04: zoom 6 is not published
PASS  mars_viking_mdim21_clrmosaic_global_232m: capabilities HTTP 200
PASS  mars_viking_mdim21_clrmosaic_global_232m: capabilities XML content type
PASS  mars_viking_mdim21_clrmosaic_global_232m: capabilities CORS wildcard
PASS  mars_viking_mdim21_clrmosaic_global_232m: layer identifier present once
PASS  mars_viking_mdim21_clrmosaic_global_232m: advertised format
PASS  mars_viking_mdim21_clrmosaic_global_232m: default style
PASS  mars_viking_mdim21_clrmosaic_global_232m: tile matrix set link
PASS  mars_viking_mdim21_clrmosaic_global_232m: bbox is -180,-90,180,90 (+/-0.01 deg)
PASS  mars_viking_mdim21_clrmosaic_global_232m: REST tile template
PASS  mars_viking_mdim21_clrmosaic_global_232m: tile matrix set exists
PASS  mars_viking_mdim21_clrmosaic_global_232m: supported CRS
PASS  mars_viking_mdim21_clrmosaic_global_232m: zoom range 0-7
PASS  mars_viking_mdim21_clrmosaic_global_232m: zoom 0 matrix is 2x1
PASS  mars_viking_mdim21_clrmosaic_global_232m: top-left corner is -180,90 at every zoom
PASS  mars_viking_mdim21_clrmosaic_global_232m: 256x256 tiles at every zoom
PASS  mars_viking_mdim21_clrmosaic_global_232m: matrix dimensions double per zoom
PASS  mars_viking_mdim21_clrmosaic_global_232m: min zoom z0/0/0 HTTP 200/206
PASS  mars_viking_mdim21_clrmosaic_global_232m: min zoom z0/0/0 content type image/jpeg
PASS  mars_viking_mdim21_clrmosaic_global_232m: min zoom z0/0/0 decodes as 256x256
PASS  mars_viking_mdim21_clrmosaic_global_232m: min zoom z0/0/0 CORS wildcard
PASS  mars_viking_mdim21_clrmosaic_global_232m: mid zoom z3/4/8 HTTP 200/206
PASS  mars_viking_mdim21_clrmosaic_global_232m: mid zoom z3/4/8 content type image/jpeg
PASS  mars_viking_mdim21_clrmosaic_global_232m: mid zoom z3/4/8 decodes as 256x256
PASS  mars_viking_mdim21_clrmosaic_global_232m: mid zoom z3/4/8 CORS wildcard
PASS  mars_viking_mdim21_clrmosaic_global_232m: max zoom north-west z7/0/0 HTTP 200/206
PASS  mars_viking_mdim21_clrmosaic_global_232m: max zoom north-west z7/0/0 content type image/jpeg
PASS  mars_viking_mdim21_clrmosaic_global_232m: max zoom north-west z7/0/0 decodes as 256x256
PASS  mars_viking_mdim21_clrmosaic_global_232m: max zoom north-west z7/0/0 CORS wildcard
PASS  mars_viking_mdim21_clrmosaic_global_232m: max zoom south-east z7/127/255 HTTP 200
PASS  mars_viking_mdim21_clrmosaic_global_232m: max zoom south-east z7/127/255 image content type
PASS  mars_viking_mdim21_clrmosaic_global_232m: max zoom south-east z7/127/255 non-empty tile
PASS  mars_viking_mdim21_clrmosaic_global_232m: max zoom antimeridian z7/0/255 HTTP 200
PASS  mars_viking_mdim21_clrmosaic_global_232m: max zoom antimeridian z7/0/255 image content type
PASS  mars_viking_mdim21_clrmosaic_global_232m: max zoom antimeridian z7/0/255 non-empty tile
PASS  mars_viking_mdim21_clrmosaic_global_232m: zoom 8 is not published
PASS  mars_mgs_mola_clrshade_merge_global_463m: capabilities HTTP 200
PASS  mars_mgs_mola_clrshade_merge_global_463m: capabilities XML content type
PASS  mars_mgs_mola_clrshade_merge_global_463m: capabilities CORS wildcard
PASS  mars_mgs_mola_clrshade_merge_global_463m: layer identifier present once
PASS  mars_mgs_mola_clrshade_merge_global_463m: advertised format
PASS  mars_mgs_mola_clrshade_merge_global_463m: default style
PASS  mars_mgs_mola_clrshade_merge_global_463m: tile matrix set link
PASS  mars_mgs_mola_clrshade_merge_global_463m: bbox is -180,-90,180,90 (+/-0.01 deg)
PASS  mars_mgs_mola_clrshade_merge_global_463m: REST tile template
PASS  mars_mgs_mola_clrshade_merge_global_463m: tile matrix set exists
PASS  mars_mgs_mola_clrshade_merge_global_463m: supported CRS
PASS  mars_mgs_mola_clrshade_merge_global_463m: zoom range 0-7
PASS  mars_mgs_mola_clrshade_merge_global_463m: zoom 0 matrix is 2x1
PASS  mars_mgs_mola_clrshade_merge_global_463m: top-left corner is -180,90 at every zoom
PASS  mars_mgs_mola_clrshade_merge_global_463m: 256x256 tiles at every zoom
PASS  mars_mgs_mola_clrshade_merge_global_463m: matrix dimensions double per zoom
PASS  mars_mgs_mola_clrshade_merge_global_463m: min zoom z0/0/0 HTTP 200/206
PASS  mars_mgs_mola_clrshade_merge_global_463m: min zoom z0/0/0 content type image/jpeg
PASS  mars_mgs_mola_clrshade_merge_global_463m: min zoom z0/0/0 decodes as 256x256
PASS  mars_mgs_mola_clrshade_merge_global_463m: min zoom z0/0/0 CORS wildcard
PASS  mars_mgs_mola_clrshade_merge_global_463m: mid zoom z3/4/8 HTTP 200/206
PASS  mars_mgs_mola_clrshade_merge_global_463m: mid zoom z3/4/8 content type image/jpeg
PASS  mars_mgs_mola_clrshade_merge_global_463m: mid zoom z3/4/8 decodes as 256x256
PASS  mars_mgs_mola_clrshade_merge_global_463m: mid zoom z3/4/8 CORS wildcard
PASS  mars_mgs_mola_clrshade_merge_global_463m: max zoom north-west z7/0/0 HTTP 200/206
PASS  mars_mgs_mola_clrshade_merge_global_463m: max zoom north-west z7/0/0 content type image/jpeg
PASS  mars_mgs_mola_clrshade_merge_global_463m: max zoom north-west z7/0/0 decodes as 256x256
PASS  mars_mgs_mola_clrshade_merge_global_463m: max zoom north-west z7/0/0 CORS wildcard
PASS  mars_mgs_mola_clrshade_merge_global_463m: max zoom south-east z7/127/255 HTTP 200
PASS  mars_mgs_mola_clrshade_merge_global_463m: max zoom south-east z7/127/255 image content type
PASS  mars_mgs_mola_clrshade_merge_global_463m: max zoom south-east z7/127/255 non-empty tile
PASS  mars_mgs_mola_clrshade_merge_global_463m: max zoom antimeridian z7/0/255 HTTP 200
PASS  mars_mgs_mola_clrshade_merge_global_463m: max zoom antimeridian z7/0/255 image content type
PASS  mars_mgs_mola_clrshade_merge_global_463m: max zoom antimeridian z7/0/255 non-empty tile
PASS  mars_mgs_mola_clrshade_merge_global_463m: zoom 8 is not published
PASS  trek_wmts_api_documentation: config HTTP 200
PASS  trek_wmts_api_documentation: Moon equirectangular projection
PASS  trek_wmts_api_documentation: Mars equirectangular projection
PASS  trek_wmts_api_documentation: zoom-0 sample tile
PASS  usgs_astrogeology_wms_moon: WMS capabilities HTTP 200
PASS  usgs_astrogeology_wms_moon: advertises IAU_2015:30100
PASS  usgs_astrogeology_wms_moon: layer LROC_WAC present
PASS  usgs_astrogeology_wms_moon: GetMap HTTP 200
PASS  usgs_astrogeology_wms_moon: GetMap image content type
PASS  usgs_astrogeology_wms_moon: GetMap decodes as 64x32
PASS  usgs_astrogeology_wms_mars: WMS capabilities HTTP 200
PASS  usgs_astrogeology_wms_mars: advertises IAU_2015:49900
PASS  usgs_astrogeology_wms_mars: layer MDIM21 present
PASS  usgs_astrogeology_wms_mars: GetMap HTTP 200
PASS  usgs_astrogeology_wms_mars: GetMap image content type
PASS  usgs_astrogeology_wms_mars: GetMap decodes as 64x32

156 checks, 0 failed
```

## Limitations and non-claims

- Availability is a point-in-time observation; NASA and USGS publish no SLA.
- No per-layer licence text exists in the WMTS or Trek FGDC metadata. The licence field is
  `not_stated_per_layer`, not an assertion of public domain.
- The Mars Trek FGDC record is a placeholder (`TBD`, `Access_Constraints: PRIVATE`) and is
  not used as provenance.
- The Mars latitude convention of the rendered WMTS grid is not declared. PDS MOLA is
  planetocentric; `ESRI:104905` geodetic latitude is planetographic.
- The IAU planetographic-west statement is convention-level context. The IAU WGCCRE report
  landing page was retrieved, but its full text was not extracted in this session.
- The checker verifies structure, availability, content type and decoded tile dimensions. It
  does not verify radiometric content, image freshness or visual correctness.
- Provider bounding boxes are nominal: Mars MOLA advertises
  `-180,-89.9991635,179.9983271,90` and Moon LOLA prints extra decimals. The effective grid
  edges are +/-180/+/-90 but not bit-identical.
- No global raster was downloaded, no raster is committed, and no science value is derived
  from these layers.
