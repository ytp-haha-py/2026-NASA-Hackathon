# NASA LOLA Moon reference — status, access log and blockers

**Status as of 2026-11-14: REAL MOON REFERENCE PRODUCED AND VERIFIED.**

One 1.98 km x 1.98 km window at the Shackleton rim (PGDA Site04, official ROI 2) was
read from a real NASA LRO-LOLA 5 m/pixel GeoTIFF with HTTP range requests, processed by
the shared `terrain-metrics-1.0.0` extractor at 90 m, validated against the JSON Schema
and the API `Manifest` model, seeded into SQLite, and returned through the live API.

This is **not** global Moon coverage, not an illumination study, and not a landing-site
recommendation. It is one local reference window. The committed manifest is
`pipelines/planetary/manifests/lola_moon_reference.json`.

## Environment used

```text
Python 3.13.18
rasterio 1.5.2 (GDAL 3.12.2)
pyproj 3.8.0
jsonschema 4.26.0
```

Repository: `/Users/pandawu/Project/2026-NASA-Hackathon` (macOS, zsh).

## Product discovery — actual pages and files inspected

### PGDA product 78

- URL: <https://pgda.gsfc.nasa.gov/products/78>
- Title: **High-Resolution LOLA Topography for Lunar South Pole Sites**
- Author: Michael K. Barker, NASA GSFC Planetary Geology, Geophysics and Geochemistry
  Laboratory (PGDA).
- Fetched successfully on 2026-11-14 (HTML, 317,341 bytes).
- Product README: <https://pgda.gsfc.nasa.gov/data/LOLA_5mpp/README>
  - Fetched with `curl -r 0-20000`; HTTP **206 Partial Content**,
    `Content-Range: bytes 0-1828/1829`, `Content-Length: 1829`,
    `Last-Modified: Tue, 07 Dec 2021 14:28:07 GMT`,
    `ETag: W/"725-17d954b6b83"`, `Accept-Ranges: bytes`,
    `Content-Type: application/octet-stream`.
  - Content (verbatim points): 5 m/pix GeoTIFFs with south polar stereographic X/Y in
    metres, MOON_ME reference frame of ephemeris DE421; GMT pixel-registered convention
    (X/Y are pixel centres); `*_surf.tif` surface height Z in metres with interpolation
    filling empty pixels; `*_ldec.tif` number of LOLA returns per pixel; `*_toterr.tif`
    total Z uncertainty (RMS error); `*_slp.tif` slope in degrees; `*_slperr.tif` slope
    uncertainty (RMS error); `Clones/` 100 LDEM clones; `*.xyzi` point clouds; `*_ROIs.tgz`
    shapefiles.
  - Sites include Site01 "Connecting ridge", Site04 "Shackleton rim", Site06 "Nobile
    rim 1", Site07 "Peak near Shackleton", Site11 "de Gerlache rim", etc.

Product 78 page statements used in the manifest caveats:

> "the fraction of interpolated 5-m pixels in these polar LDEMs is necessarily large
> (~90%) due to LOLA's cross-track and inter-spot spacing"

> "median RMS Z error ~ 0.30 - 0.50 m and a median RMS slope error ~ 1.5 - 2.5°"

> "track geolocation uncertainty is ~10 - 20 cm horizontally and ~2 - 4 cm vertically
> over each region"

> "All these files (except the XYZI points clouds and ROI shapefiles) are 5 m/pix
> GeoTiffs with south polar stereographic X/Y coords in meters, MOON_ME reference frame
> of ephemeris DE421."

> "Please cite the following reference when using any of the products described above:
> Barker, M.K., et al. (2021), Improved LOLA Elevation Maps for South Pole Landing
> Sites: Error Estimates and Their Impact on Illumination Conditions, Planetary & Space
> Science, Volume 203, 1 September 2021, 105119, doi:10.1016/j.pss.2020.105119."

The page has a **Data Usage Policy** (citation request), not a named open-data license.
The NASA Science Data Portal's
[Science Data Licenses](https://science.data.nasa.gov/about/license) page states that
NASA-led mission data are CC0-1.0 unless marked with a restrictive notice. The PGDA page
carries no restrictive notice. The manifest records `CC0-1.0` with the NASA license URL
and the Barker et al. citation request.

### Site04 files — actual HEAD results

All URLs are under `https://pgda.gsfc.nasa.gov/data/LOLA_5mpp/Site04/`. Each returned
HTTP **200 OK**, `Accept-Ranges: bytes`, `Content-Type: image/tiff`.

| Product ID | URL suffix | HTTP | Content-Length | Last-Modified | ETag |
| --- | --- | ---: | ---: | --- | --- |
| `Site04_final_adj_5mpp_surf` | `Site04_final_adj_5mpp_surf.tif` | 200 | 40,980,806 | Fri, 05 Jun 2020 18:40:02 GMT | `W/"2715146-17285c863d0"` |
| `Site04_final_adj_5mpp_slp` | `Site04_final_adj_5mpp_slp.tif` | 200 | 40,980,772 | Mon, 08 Jun 2020 22:05:53 GMT | `W/"2715124-17295f7ede8"` |
| `Site04_final_adj_5mpp_toterr` | `Site04_final_adj_5mpp_toterr.tif` | 200 | 40,980,808 | Fri, 05 Jun 2020 18:40:15 GMT | `W/"2715148-17285c89698"` |
| `Site04_final_adj_5mpp_slperr` | `Site04_final_adj_5mpp_slperr.tif` | 200 | 40,980,806 | Fri, 05 Jun 2020 18:40:22 GMT | `W/"2715146-17285c8b1f0"` |
| `Site04_final_adj_5mpp_ldec` | `Site04_final_adj_5mpp_ldec.tif` | 200 | 40,980,740 | Fri, 05 Jun 2020 18:40:08 GMT | `W/"2715104-17285c87b40"` |
| `Site04_ROIs.tgz` | `Site04_ROIs.tgz` | 200 | 1,712 | Tue, 01 Dec 2020 13:06:48 GMT | `W/"6b0-1761e69a240"` |

Exact surface URL used:
`https://pgda.gsfc.nasa.gov/data/LOLA_5mpp/Site04/Site04_final_adj_5mpp_surf.tif`

Site01 surface LDEM was also HEAD-checked as a cross-check:
`https://pgda.gsfc.nasa.gov/data/LOLA_5mpp/Site01/Site01_final_adj_5mpp_surf.tif`
returned 200, `Content-Length: 40,980,810`,
`Last-Modified: Fri, 05 Jun 2020 18:39:35 GMT`, ETag
`W/"271514a-17285c7fa58"`. It was not used.

### Product 81 (87–90°S mosaic) — documented, not used

- URL: <https://pgda.gsfc.nasa.gov/products/81>
- "South Pole LOLA DEM Mosaic", constructed from 97 individual 20 x 20 km fields with
  2 km overlaps; typical RMS residual 30–55 cm with the individual fields.
- Same 5 m/pix, south polar stereographic metres, MOON_ME/DE421 documentation.
- Actual HEAD results:

| Product | URL | Content-Length | Last-Modified |
| --- | --- | ---: | --- |
| LDEM surface | `https://pgda.gsfc.nasa.gov/data/LOLA_5mpp/87S/ldem_87s_5mpp.tif` | 3,465,285,714 | Thu, 14 Oct 2021 12:54:49 GMT |
| Slope | `https://pgda.gsfc.nasa.gov/data/LOLA_5mpp/87S/ldsm_87s_5mpp.tif` | 5,255,076,239 | Tue, 07 Dec 2021 22:31:59 GMT |
| Return count | `https://pgda.gsfc.nasa.gov/data/LOLA_5mpp/87S/ldec_87s_5mpp.tif` | 145,895,726 | Thu, 14 Oct 2021 15:11:06 GMT |

All returned 200 and `Accept-Ranges: bytes`. The 3.47 GB LDEM is far above the 50 MB
investigation budget and was **not downloaded**. Product 78 Site04 (41 MB object) with a
5.4 MB range-read window is the smaller official alternative.

## Actual raster metadata — Site04 surface LDEM

Read with GDAL `/vsicurl/` (rasterio) on 2026-11-14:

```text
driver:        GTiff
shape:         3200 x 3200 px, 1 band
dtype:         float32
nodata:        NaN
resolution:    5.0 m x 5.0 m
bounds:        left=-9000.0, bottom=-15000.0, right=7000.0, top=1000.0 (metres)
transform:     Affine(5.0, 0.0, -9000.0, 0.0, -5.0, 1000.0)
block layout:  striped, one full row per strip: (rows, cols) = (1, 3200)
tiled:         False
compression:   none
overviews:     []
CRS:           PROJCS["unnamed", ... SPHEROID["unnamed",1737400,0] ...,
               PROJECTION["Polar_Stereographic"],
               PARAMETER["latitude_of_origin",-90],
               PARAMETER["central_meridian",0],
               PARAMETER["false_easting",0],
               PARAMETER["false_northing",0],
               UNIT["metre",1]]
proj4:         +proj=stere +lat_0=-90 +lat_ts=-90 +lon_0=0 +x_0=0 +y_0=0
               +R=1737400 +units=m +no_defs +type=crs
PROJ method:   Polar Stereographic (variant B), EPSG method 9829
ellipsoid:     sphere, semi-major 1,737,400 m
axis unit:     metre
```

The file is **not a COG**: no internal tiling and no overviews. Range reads still work
because each row is a contiguous strip. The GeoTIFF CRS is unnamed; the
MOON_ME/DE421 statement is from the product documentation, not re-derived from the WKT.

### HTTP range-read proof

- `HEAD` returned `Accept-Ranges: bytes`, `Content-Length: 40,980,806`,
  `ETag: W/"2715146-17285c863d0"`,
  `Last-Modified: Fri, 05 Jun 2020 18:40:02 GMT`.
- A 1-byte `GET` with `Range: bytes=0-0` returned **HTTP 206**.
- A 414 x 414 pixel window at `col_off=1041`, `row_off=1261` was read with
  `rasterio`/GDAL `/vsicurl/` and written to a local cache GeoTIFF.
- The window is 2070 m x 2070 m at 5 m (414 rows x 414 columns). Because the source is
  striped, the transfer touches 414 full rows:
  `414 * 3200 * 4 = 5,299,200 bytes` plus TIFF IFD/header overhead.
- The importer's conservative estimate is **5,430,272 bytes** (5,299,200 + 128 KiB).
  The derived cache file is **433,015 bytes** (deflate-compressed float32).
- The first real read (metadata probe + window) took about 42–54 s on the NASA server.
  A cache-only rerun takes under 1 s. No 40.98 MB full product was downloaded.
- The cached source window has 100% finite cells. Its elevation range is
  **1199.060546875 m to 1739.121826172 m** (product metres).

Cache artifacts (gitignored, not committed):

```text
data/cache/lola/lola-window-aee94d03b7b73be8ed41.tif   433,015 bytes
data/cache/lola/lola-window-aee94d03b7b73be8ed41.json  sidecar
```

Cache TIFF SHA-256:
`702aac2f93e0f36dd1025900d4e5021be17411ee735d93df7191cc33b65cf095`.

## Official Site04 ROI shapefile

`Site04_ROIs.tgz` (1,712 bytes) was downloaded and extracted. It contains
`Site04_ROIs.shp`, `.shx`, `.dbf`, `.prj` and a `.gmt` symlink. The DBF has four records
with fields `X` and `Y` (24-character numbers). The shapefile polygons and DBF points
agree to within about 15 m (DBF point vs polygon bbox centre). The `.prj` is a polar
stereographic WKT with standard parallel −90,
central meridian 0, false easting/northing 0, sphere radius 1,737,400 m.

| Record | DBF X (m) | DBF Y (m) | Polygon bbox (m) |
| ---: | ---: | ---: | --- |
| 1 | −2812.5 | −6647.5 | −2842.5 … −2782.5, −6802.5 … −6522.5 |
| 2 | −2757.5 | −6342.5 | −2792.5 … −2712.5, −6432.5 … −6262.5 |
| 3 | −2677.5 | −6107.5 | −2702.5 … −2642.5, −6167.5 … −6052.5 |
| 4 | −2657.5 | −5957.5 | −2687.5 … −2627.5, −6027.5 … −5902.5 |

Selected: **record 2**, X = −2757.5 m, Y = −6342.5 m. Inverse-projected with the
product CRS: **lon = −156.50226014°, lat = −89.77192511°** (east-positive degrees).
Longitude is ill-conditioned this close to the pole; the X/Y centre is authoritative.
There is no claim that ROI 2 is the best or most illuminated ROI. The product page's
illumination discussion for Site 1 RoIs 4–6 is not a Site04 ranking.

The analysis window is 1980 m x 1980 m centred on that X/Y. A 45 m pad gives a
2070 m source crop (414 x 414 px at 5 m). The full analysis window is inside the
16 x 16 km Site04 tile.

## Real run — observed command output

```bash
uv run python pipelines/planetary/lola_import.py
```

Observed (abridged):

```text
IMPORT: Moon LOLA reference
  source:          https://pgda.gsfc.nasa.gov/data/LOLA_5mpp/Site04/Site04_final_adj_5mpp_surf.tif
  source raster:   GTiff 3200x3200 px, 5.0 m/pix, float32, nodata=nan
  source layout:   striped, blocks=1x3200, compress=none, overviews=none
  source CRS:      +proj=stere +lat_0=-90 +lat_ts=-90 +lon_0=0 +x_0=0 +y_0=0 +R=1737400 +units=m +no_defs +type=crs
  access:          remote=True, range_reads=True, size=40.98 MB (40,980,806 bytes)
  remote validators: ETag=W/"2715146-17285c863d0" Last-Modified=Fri, 05 Jun 2020 18:40:02 GMT
  ROI centre:      X=-2757.5 m, Y=-6342.5 m -> lon=-156.502260, lat=-89.771925
  analysis window: 1980 m, source crop 414x414 px
  estimated range bytes: 5.43 MB (5,430,272 bytes)
  cache:           .../data/cache/lola/lola-window-aee94d03b7b73be8ed41.tif
  manifest output: .../pipelines/planetary/manifests/lola_moon_reference.json

  measured features:
    slope_mean_deg             17.895873
    slope_p90_deg              28.777052
    terrain_relief_p95_p05_m   390.805907
    roughness_std_m            102.805473
  valid fraction:  1.000000
  window checksum: sha256:0db13ffd3e0d49c04ce468ce5312f4cd46fa6b2f4f99854dc4def100d0f08050
```

The second run is a cache hit and completes in under 1 s with identical feature values.
A `--refresh` run re-reads the real source.

## Manifest validation

Validated with:

```python
import json, jsonschema
from pathlib import Path
from analog_api.domain.manifest import Manifest

m = json.loads(Path("pipelines/planetary/manifests/lola_moon_reference.json").read_text())
jsonschema.Draft202012Validator(
    json.loads(Path("contracts/pipeline-manifest.schema.json").read_text())
).validate(m)
Manifest.model_validate(m)
```

Observed: **schema + model OK**; `data_kind=observed`, 1 dataset, 1 reference,
0 candidates; all four observations `quality=verified`, `valid_fraction=1.0`,
`method_version=terrain-metrics-1.0.0`, `extraction_scale_m=90.0`.

## Extractor blocker and exact workaround

The shared extractor is imported unchanged. With `center_xy` and a lunar projected CRS,
its approximate-centre branch calls
`Transformer.from_crs(src_crs, "EPSG:4326", always_xy=True)`. PROJ rejects that
cross-celestial-body transform. Reproduced with
`PROJ_IGNORE_CELESTIAL_BODY=NO`:

```text
ProjError
Error creating Transformer from CRS.: (Internal Proj Error: proj_create_operations:
Source and target ellipsoid do not belong to the same celestial body (Moon vs Earth).)
```

This is a real blocker in the shared extractor for Moon data. The importer does **not**
modify `pipelines/src/analog_pipelines/features.py`. It sets
`PROJ_IGNORE_CELESTIAL_BODY=YES` for the process. All four metrics are computed in the
source projected CRS, so the override cannot change them. The CLI also fails early with
a clear `proj_body_override_required` error if a caller explicitly sets the variable to
`NO`.

The lead may want to move this override into the extractor's lunar branch, or make the
body explicit in the CRS transform. Until then, the importer's environment setting is
required.

The manifest `lat`/`lon` are computed by inverse-projecting the ROI X/Y directly with
the same override. The extractor's `.center_lon/.center_lat` are corner averages; at
this near-pole window they give approximately lon = −157.3275, lat = −89.7713, about
0.8° away in longitude. The X/Y centre is authoritative; the manifest notes say so.

## Integration proof — observed

The Earth pipeline and a worldcover context pipeline now exist in the repository. The
API's seed configuration also discovers `pipelines/worldcover/manifests` by default
(`ANALOG_MANIFEST_DIR_CONTEXT`), which is independent of this Moon work.

Exact command run:

```bash
ANALOG_DATABASE_URL=sqlite+pysqlite:////tmp/analog-moon.db \
ANALOG_MANIFEST_DIR=pipelines/earth/manifests \
ANALOG_MANIFEST_DIR_PLANETARY=pipelines/planetary/manifests \
uv run python -m analog_api.cli seed
```

Observed output (current API code, after the independent worldcover context change):

```text
Seeded 3 manifest(s). Index: 5 candidate(s), 3 dataset(s), 1 reference(s).
data_version=dv-19f54b4539c8
```

`uv run python -m analog_api.cli status` observed:

```text
candidates=5 datasets=3 references=1
```

The 5 persisted candidates are the five Earth Copernicus windows; the worldcover
manifest contributes context/dataset rows and shares location ids, so it does not add
new candidate rows. The Moon manifest contributes exactly one reference.

API checks with `fastapi.testclient.TestClient(create_app(Settings()))`, using the same
environment:

```text
GET /api/v1/references
200
[("moon-shackleton-rim-site04-roi2", "Moon", -89.77192511, -156.50226014, 1.0)]

POST /api/v1/search
{"body":"Moon","reference_id":"moon-shackleton-rim-site04-roi2","limit":5}
200
indexed_candidate_count=5
eligible_candidate_count=5
excluded_candidate_count=0
rank 1 earth-death-valley   distance 0.24484063803734957
rank 2 earth-lanzarote      distance 1.698952303003822
rank 3 earth-rio-tinto      distance 2.286720558558561
rank 4 earth-devon-island   distance 2.4095961441788516
rank 5 earth-atacama-desert distance 3.045256499749275
```

This is a terrain-shape ranking of the five indexed Earth windows against the real Moon
reference. It is not a global survey and not a physical-equivalence claim. The API
needed no change for the Moon reference itself; the seed discovered the planetary
manifest through the documented `ANALOG_MANIFEST_DIR_PLANETARY` setting.

## Offline checks — observed

```bash
uv run pytest pipelines/planetary/tests -q
# 37 passed
uv run ruff check pipelines/planetary
# All checks passed!
uv run ruff format --check pipelines/planetary
# (no reformat needed)
```

The tests do not access the network. The real manifest is validated against both
contracts. The synthetic lunar-plane test writes a **TEST-ONLY** GeoTIFF in pytest's
`tmp_path`, runs the full provider/cache/extractor path with a known 10% grade, and
validates a synthetic manifest; it is never written to the real manifest.

## If no real access had been possible

The fallback would have been: keep the runnable importer, tests, product discovery notes
and `NASA_LOLA_STATUS.md`; mark the Moon reference `NOT VERIFIED`; leave
`GET /api/v1/references` empty for Moon; and record the exact blocker (URL, HTTP status,
size, GDAL/pyproj error). That fallback was **not needed**: the real Site04 product was
read and the reference is verified. No values were invented, and no synthetic data was
substituted.
