# Moon LOLA global DEM source — verified access, metadata and fallback

**Status as of 2026-11-15: source/access verified; no global raster downloaded.**
Scope: Issue #170 Goal A (Moon Global D01), source adapter slice A1. This document
records what was actually observed, not what a product page promises.

Adapter: `pipelines/planetary/lola_global_source.py`
CLI: `uv run python pipelines/planetary/lola_global_source.py ...`
Offline tests: `pipelines/planetary/tests/test_lola_global_source.py`

> **Non-claims.** This is not global D01 coverage, not a 90 m lunar DEM, and not a
> comparison of Moon and Earth terrain. No full 2.7 GB raster was downloaded. No
> value was zero-filled. The 90 m D01 analysis grid is supported only by the
> existing 5 m/20 m regional LOLA products; the global products verified here are
> **coarser than 90 m** and are coarse context only.

## 1. Decision

| Role | Product | Native support | Verdict |
| --- | --- | --- | ---: |
| Primary | NASA PGDA LOLA MOON_PA global shape model, **128 ppd** | 236.90 m at equator | Verified, range-readable, GDAL opens with correct lunar CRS |
| Primary (smaller) | NASA PGDA LOLA MOON_PA global shape model, **64 ppd** | 473.80 m at equator | Verified, same CRS/layout, 715,159,249 bytes |
| Fallback | NASA SVS CGI Moon Kit LOLA displacement **64 ppd** | 473.80 m at equator | Verified but **no CRS/geotransform in file**; documented mapping cross-checked |
| Fallback (smaller) | NASA SVS 16 ppd / 4 ppd / 64 ppd uint16 | 1,895.21 / 7,580.84 / 473.80 m | Verified headers; not suitable for 90 m metrics |

Both primary products are **global geographic grids**, not polar stereographic.
Neither can produce independent 90 m metrics: 128 ppd is 2.63x coarser than 90 m,
64 ppd is 5.26x coarser. Upsampling would fabricate detail and is not done.

## 2. Primary source — PGDA LOLA MOON_PA global GeoTIFFs

Product page: <https://pgda.gsfc.nasa.gov/products/95> — *LOLA MOON_PA gridded
dataset*, Gregory A. Neumann, NASA GSFC PGDA.

The page lists four global shape models. Only the pixel-registered versions have
GeoTIFF downloads; the gridline-registered versions are netCDF/GMT (`.grd`) only.

| Product | Raster URL | Content-Length | Last-Modified | ETag |
| --- | --- | ---: | --- | --- |
| `LDEM64_PA_pixel_202405.tif` | <https://pgda.gsfc.nasa.gov/data/LOLA_PA/LDEM64_PA_pixel_202405.tif> | 715,159,249 | Fri, 03 May 2024 14:00:40 GMT | `W/"2aa076d1-18f3ec1cf40"` |
| `LDEM128_PA_pixel_202405.tif` | <https://pgda.gsfc.nasa.gov/data/LOLA_PA/LDEM128_PA_pixel_202405.tif> | 2,705,286,892 | Fri, 03 May 2024 14:05:13 GMT | `W/"a13f66ec-18f3ec5f9a8"` |

### 2.1 HTTP evidence (observed 2026-11-15)

```bash
curl -sS -o /dev/null -D - -I \
  https://pgda.gsfc.nasa.gov/data/LOLA_PA/LDEM128_PA_pixel_202405.tif
```

Observed (essential headers):

```text
HTTP/1.1 200 OK
Accept-Ranges: bytes
Last-Modified: Fri, 03 May 2024 14:05:13 GMT
ETag: W/"a13f66ec-18f3ec5f9a8"
Content-Type: image/tiff
Content-Length: 2705286892
```

A range request returns **HTTP 206** and a real TIFF header. This is the
coordinator-verified probe, reproduced here:

```bash
curl -sS -r 0-511 \
  https://pgda.gsfc.nasa.gov/data/LOLA_PA/LDEM128_PA_pixel_202405.tif \
  -D /tmp/ldem128.hdr -o /tmp/ldem128.prefix
grep -Ei 'HTTP/|content-range|content-type' /tmp/ldem128.hdr
xxd -l 16 /tmp/ldem128.prefix
```

Observed:

```text
HTTP/1.1 206 Partial Content
Content-Type: image/tiff
Content-Range: bytes 0-511/2705286892

00000000: 4949 2b00 0800 0000 1000 0000 0000 0000  II+.............
```

`II+\x00` is a **little-endian BigTIFF** signature, not classic TIFF. The 64 ppd
object is also BigTIFF (`Content-Range: bytes 0-511/715159249`, same `II+`
magic). GDAL/rasterio open both through `/vsicurl/`.

### 2.2 GDAL/rasterio metadata through `/vsicurl/`

```bash
uv run python pipelines/planetary/lola_global_source.py \
  --inspect pgda-lola-ldem128-pa-pixel-202405
```

Observed (abridged; full JSON is printed by the command):

| Field | Observed value |
| --- | --- |
| Driver | `GTiff` (BigTIFF, `bigtiff: true`) |
| Shape | 46,080 x 23,040 px, 1 band |
| dtype / nodata | `float32` / `NaN` |
| CRS | `GEOGCRS["GCS_Moon_2000", ... ELLIPSOID["Moon_2000_IAU_IAG",1737400,0] ... ID["ESRI",104903]]` |
| CRS name | `GCS_Moon_2000` |
| Bounds | 0.0 .. 360.0 longitude, -90.0 .. 90.0 latitude |
| Pixel size | 0.0078125° (128 ppd), square |
| Native support | 236.901 m at the equator |
| Layout | tiled, 256 x 256 blocks, deflate, no overviews |
| Size | 2,705,286,892 bytes |
| Access | `Accept-Ranges: bytes`; 206 verified; ETag/Last-Modified recorded |
| Checksum published | **no** |

So yes: **GDAL/rasterio reports the CRS, resolution and nodata correctly.** The CRS
is the lunar sphere radius 1,737,400 m (ESRI:104903), not Earth WGS84. GDAL
reports the geographic axes as latitude/longitude in the WKT but reads arrays
north-up as usual; the adapter uses the observed north-up transform and bounds.

The 64 ppd product opens identically (23,040 x 11,520, 0.015625°, 473.802 m).

The product page states the grid is in **MOON_PA (DE421)**. The GeoTIFF WKT names
`GCS_Moon_2000` and does not distinguish MOON_PA from MOON_ME. Do not compare
these values with existing MOON_ME site windows without a frame conversion: the
product page states the MOON_PA/MOON_ME rotation is about 0.0288473°, roughly
**875 m** on the surface.

### 2.3 Vertical unit and nodata

- dtype `float32`, nodata `NaN`, band tag `_FillValue=nan`.
- Pixel values are **metres** relative to the 1737.4 km reference radius. The band
  `actual_range` tag is inherited in **kilometres** (`{-9.124758720397949,
  10.76554298400879}`) from the sibling netCDF/GMT grid. The adapter therefore
  uses `scale=1`, `offset=0` for metres and records the inferred-unit caveat.
- A real 22 x 22 window at 10°E, 10°N gave elevations from **-822.295 m to
  -683.903 m**, consistent with PGDA's published global range (-9,127 to
  +10,779 m), not with the kilometre-scaled `actual_range` tag.
- No per-pixel uncertainty or interpolation mask is supplied.

### 2.4 Bounded window read (no full download)

```bash
uv run python pipelines/planetary/lola_global_source.py \
  --read pgda-lola-ldem128-pa-pixel-202405 --lon 10 --lat 10 --window-m 5000
```

Observed:

```text
window:              col_off=1269 row_off=10229 22x22 px
bounds_lonlat:       9.9140625 .. 10.0859375 (both axes)
estimated bytes:     1,114,112 (4 x 256x256 tiles + 64 KiB overhead)
valid fraction:      1.0
elevation range:     -822.2952270507812 .. -683.9031982421875 m
window sha256:       sha256:8c9e0e9e58156d9903b11a2545bc4a486583110a08f062184f33333f485504e1
access timestamp:    2026-11-15T14:07:52Z
```

The estimate is conservative: a 22-px window straddles two 256-px tile rows and
columns, so four compressed tiles may be touched (1,048,576 bytes) plus header
budget. The read is still bounded and far below the 2.7 GB object. The 5 km
window is smaller than the 90 m D01 analysis window contract; this adapter is a
source reader, not a feature extractor.

## 3. Fallback — NASA SVS CGI Moon Kit displacement TIFFs

Page: <https://svs.gsfc.nasa.gov/4720> — *CGI Moon Kit*, NASA Scientific
Visualization Studio, visualizer Ernie Wright (STC), scientist Noah Petro
(NASA/GSFC). Released 2019-09-06; page last updated 2026-01-09 (the 2025 update
concerns the colour map, not the displacement maps).

The page says the displacement maps were taken directly from the spring-2019
LOLA gridded products, 4/16/64 ppd, reformatted as uncompressed TIFFs. Floating
point TIFFs are **kilometres** relative to a 1,737.4 km radius; unsigned 16-bit
TIFFs are **half-metres offset by +10,000** (10 km). The page also says the map is
centred on 0° longitude and is intended for 3D rendering; scientific use should
go to the source data.

| File | Dimensions | Bytes | Last-Modified | Vertical encoding |
| --- | ---: | ---: | --- | --- |
| `ldem_4.tif` | 1,440 x 720 | 4,153,352 | Wed, 05 Jun 2019 20:21:01 GMT | float32 km |
| `ldem_16.tif` | 5,760 x 2,880 | 66,378,634 | Wed, 05 Jun 2019 20:21:00 GMT | float32 km |
| `ldem_64.tif` | 23,040 x 11,520 | 1,061,775,722 | Wed, 05 Jun 2019 20:21:05 GMT | float32 km |
| `ldem_64_uint.tif` | 23,040 x 11,520 | 530,934,146 | Wed, 05 Jun 2019 20:21:10 GMT | uint16, metres = raw*0.5 - 10000 |

Base URL: `https://svs.gsfc.nasa.gov/vis/a000000/a004700/a004720/`.

### 3.1 HTTP evidence

```bash
curl -sS -I https://svs.gsfc.nasa.gov/vis/a000000/a004700/a004720/ldem_64.tif
```

Observed (essential):

```text
HTTP/2 200
last-modified: Wed, 05 Jun 2019 20:21:05 GMT
etag: "3f49696a-58a99563db37d"
accept-ranges: bytes
content-length: 1061775722
content-type: image/tiff
```

Range probe:

```text
HTTP/2 206
content-range: bytes 0-511/1061775722
content-type: image/tiff

00000000: 4949 2a00 0800 483f 8816 59bd 1b2f 5dbd  II*...H?..Y../].
```

`II*\x00` is a classic TIFF, unlike the PGDA BigTIFFs. The file is a bare IDL TIFF;
GDAL reads it normally once the documented mapping is supplied.

### 3.2 The important limitation: no CRS, no geotransform, no nodata

GDAL/rasterio through `/vsicurl/`:

```bash
uv run python pipelines/planetary/lola_global_source.py \
  --inspect svs-cgi-moon-kit-ldem64
```

Observed:

| Field | Observed value |
| --- | --- |
| Driver | `GTiff` (classic TIFF) |
| Shape | 23,040 x 11,520 px, 1 band |
| dtype / nodata | `float32` / **None** |
| CRS | **None** |
| Transform | identity (1, 0, 0, 0, 1, 0) — **not georeferenced** |
| Layout | striped (1 x 23,040), uncompressed, no overviews |
| Size | 1,061,775,722 bytes |
| Range | 206 verified |
| Checksum published | **no** |

Tags are only `TIFFTAG_IMAGEDESCRIPTION = "IDL TIFF file"`,
`TIFFTAG_SOFTWARE = "IDL 8.7.2"`, `TIFFTAG_DATETIME = "2019:05:20 12:47:07"`.
There is no `AREA_OR_POINT`, no GeoKeys, no nodata tag, no `scale`/`offset`.
The adapter therefore supplies the documented mapping explicitly:

```text
col = (lon + 180) / (1/ppd)      # col 0 = -180°
row = (90 - lat) / (1/ppd)       # row 0 = +90°
metres = raw * 1000              # float32 kilometre files
metres = raw * 0.5 - 10000       # uint16 file
```

Ordering and units were **verified against PGDA**, not assumed. Six sample points
compared at the same lon/lat (PGDA 64 ppd pixel value vs SVS 64 ppd pixel value):

| lon | lat | PGDA 64 ppd (m) | SVS 64 ppd (m) | SVS − PGDA (m) |
| ---: | ---: | ---: | ---: | ---: |
| 10 | 10 | -722.340 | -746.50 | -24.16 |
| -45 | -30 | -624.603 | -630.50 | -5.90 |
| 120 | 40 | -101.408 | -128.00 | -26.59 |
| -170 | 0 | 4,879.279 | 4,790.50 | -88.78 |
| 60 | -60 | -606.667 | -598.00 | +8.67 |
| 0 | 80 | -481.472 | -479.50 | +1.97 |

The two grids describe the same terrain but are not identical products: PGDA is
the 2024 MOON_PA shape model; SVS is derived from 2019 LOLA PDS gridded data.
Differences of tens of metres are expected from frame/version/interpolation. This
comparison validates the SVS mapping and units; it does **not** make SVS
equivalent to the primary source. The uint16 file at the same 64 ppd grid gives
the same values after `raw*0.5 - 10000` (for example raw 18507 -> -746.50 m).

### 3.3 Fallback resolution

| ppd | Equatorial support | Verdict for 90 m D01 |
| ---: | ---: | --- |
| 64 | 473.80 m | 5.26x coarser; fallback only |
| 16 | 1,895.21 m | 21x coarser; context only |
| 4 | 7,580.84 m | 84x coarser; context only |

SVS has no global grid finer than 64 ppd. A fallback read must record 473.8 m,
not "global LOLA DEM" or an implied 90 m support.

Bounded read at 10°E, 10°N, 5 km:

```text
window:           col_off=12154 row_off=5114 12x12 px
bounds_lonlat:    9.90625 .. 10.09375
estimated bytes:  1,171,456 (12 full rows x 23,040 px x 4 B + 64 KiB)
valid fraction:   1.0
elevation range:  -804.0 .. -712.0 m
window sha256:    sha256:3337252b46726775828df6ea83d3475ad2ae7c8a41efa20c6ee32920393db473
```

The row-striped layout means the conservative estimate counts whole rows, even
though GDAL may fetch only the requested byte span in each row. The uint16
variant has the same 12x12 window and values, with a 618,496-byte estimate.

## 4. License and citation

- **PGDA**: the product page has a **Data Usage Policy** asking users to cite the
  dataset; it does not state an SPDX license. The NASA Science Data Portal states
  that NASA-led mission data are CC0-1.0 unless marked with a restrictive notice;
  the PGDA page carries no restrictive notice. Record `CC0-1.0` only with that
  provenance, and confirm redistribution terms before publishing a derived
  product.
- **SVS**: the page credits *NASA's Scientific Visualization Studio*, Ernie Wright
  (STC) and Noah Petro (NASA/GSFC), and uses NASA media guidelines. No SPDX
  license is stated; the underlying LOLA data are public NASA archive data.
- Required citation for the primary data:
  **Neumann, G. (2024). LOLA MOON_PA gridded dataset [Data set]. NASA Goddard
  Space Flight Center Planetary Geodesy Data Archive. doi:10.60903/LOLA_PA.**
- Related references (product page): Barker et al. (2016), Icarus 273, 346–355,
  doi:10.1016/j.icarus.2015.07.039; Barker et al. (2023), PSJ 4, 183,
  doi:10.3847/PSJ/acf3e1; Ermakov et al. (2018), JGR Planets 123, 2038–2064.
- Fallback credit: **NASA SVS, CGI Moon Kit, SVS 4720**, Ernie Wright (STC),
  Noah Petro (NASA/GSFC), <https://svs.gsfc.nasa.gov/4720>.

## 5. Checksums: what exists and what does not

| Product | Published SHA-256/MD5? | What is available |
| --- | --- | --- |
| PGDA LDEM64/128 | **No** | ETag and Last-Modified only; ETag is a cache validator, not a content hash |
| SVS ldem_* | **No** | ETag and Last-Modified only |

The adapter never calls an ETag a checksum. It computes a **window** SHA-256 over
the float32 metre array it actually read (`window_sha256`), labelled as a window
content checksum. It is not a checksum of the 2.7 GB source object. A future
promotion step that needs an object checksum must compute it from a complete
staged file or obtain a provider-published digest; neither was available here.

## 6. Reproducible commands

```bash
# Catalogue + resolution/role/units. No network.
uv run python pipelines/planetary/lola_global_source.py --list-sources

# HTTP + GDAL metadata for the primary 128 ppd source.
uv run python pipelines/planetary/lola_global_source.py \
  --inspect pgda-lola-ldem128-pa-pixel-202405

# Bounded primary window (22x22 px, ~1.1 MB estimate, no full download).
uv run python pipelines/planetary/lola_global_source.py \
  --read pgda-lola-ldem128-pa-pixel-202405 --lon 10 --lat 10 --window-m 5000

# Bounded 64 ppd fallback window (12x12 px).
uv run python pipelines/planetary/lola_global_source.py \
  --read svs-cgi-moon-kit-ldem64 --lon 10 --lat 10 --window-m 5000

# Plan only; no raster read.
uv run python pipelines/planetary/lola_global_source.py \
  --read pgda-lola-ldem64-pa-pixel-202405 --lon -45 --lat -30 \
  --window-m 5000 --dry-run

# Offline unit tests (synthetic lunar grids in tmp_path, no network).
uv run pytest pipelines/planetary/tests/test_lola_global_source.py -q
```

Observed test result: **10 passed** (offline). The online commands above were run
on 2026-11-15 and produced the outputs quoted in sections 2–3.

## 7. What is NOT available (blockers)

1. **No global LOLA DEM at 90 m or finer.** The best global product verified is
   128 ppd = 236.9 m at the equator. The 5 m and 20 m LOLA products are regional
   (sites, polar caps), not global. This slice does not upsample and does not
   claim global 90 m coverage.
2. **No published source checksum** for either provider. ETag is not a hash.
3. **SVS fallback is not georeferenced.** Its CRS/transform/nodata are absent;
   the adapter supplies a documented mapping. Any consumer that opens the file
   directly with GDAL and assumes a CRS is wrong.
4. **MOON_PA vs MOON_ME.** The primary global grid is MOON_PA; the existing 5 m
   site windows are documented as MOON_ME. The frames differ by roughly 875 m on
   the surface. A frame-aware transform is required before co-locating them.
5. **Vertical unit inference.** PGDA pixel values are metres, but the `actual_range`
   tag is in kilometres. Consumers must not apply the tag as a scale.
6. **Fallback is a visualisation reformat of 2019 data**, not the 2024 shape
   model; it is not a substitute for the primary grid.
7. **File-name collision in the shared worktree.** Another agent's arbitrary-AOI
   engine currently occupies `pipelines/planetary/lola_global.py` (see
   `docs/MOON_D01_GLOBAL_AOI.md`). The A1 source adapter therefore lives in
   `pipelines/planetary/lola_global_source.py`; A2's file was left untouched. The
   lead must decide whether to merge these modules or rename A2's file. Until
   then, the adapter is importable as `lola_global_source` from
   `pipelines/planetary`, and its catalogue constants are the verified source of
   truth for 64/128 ppd resolution, SVS units and no-CRS handling.

### 7.1 Integration note for the AOI engine

Commit `1725b8e` adds the arbitrary-AOI engine in `pipelines/planetary/lola_global.py`
and documents it in `docs/MOON_D01_GLOBAL_AOI.md`. Three facts from this audit must be
applied before that engine can use the SVS fallback honestly:

- SVS float TIFFs store **kilometres**, and the uint16 TIFF stores half-metres with
  a +10 km offset. A consumer that reads the fallback as metres overstates relief
  and slope by 1000x. The adapter exposes `vertical_scale_to_m` /
  `vertical_offset_m`; the AOI path should apply them.
- The SVS files have **no CRS/geotransform** and the AOI engine must inject the
  documented -180..180 mapping. It must not treat the file's identity transform as
  georeferencing.
- SVS 64 ppd (473.8 m) is the finest fallback; the AOI catalogue currently lists
  only 16 ppd (1,895 m). Resolution must be recorded in any fallback result.

This is a reconciliation task for the lead: the adapter is importable as
`lola_global_source`, and both modules currently live side by side without
importing each other, so neither silently changes the other's results.

## 8. Verification matrix

| Check | PGDA LDEM128 | PGDA LDEM64 | SVS ldem_64 |
| --- | --- | --- | --- |
| HTTPS 200 | yes | yes | yes |
| `Accept-Ranges: bytes` | yes | yes | yes |
| 512-byte range -> 206 | yes | yes | yes |
| TIFF magic | `II+` BigTIFF | `II+` BigTIFF | `II*` classic TIFF |
| GDAL `/vsicurl/` open | yes | yes | yes (warning: not georeferenced) |
| CRS in file | GCS_Moon_2000 (R=1,737,400 m) | same | **none** |
| Resolution | 0.0078125° / 236.901 m | 0.015625° / 473.802 m | 0.015625° / 473.802 m |
| Nodata | NaN | NaN | **none** |
| Tiled / compressed / overviews | 256x256 / deflate / none | same | striped / none / none |
| Bounded window read | yes, 22x22 px | yes | yes, 12x12 px |
| Full object downloaded | no | no | no |
| Published checksum | no | no | no |
