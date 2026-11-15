# MOLA MEGDR PDS4 source and bounded window access

Status: **official source verified; bounded reader implemented; no global download.**
Issue: #170 Goal B (Mars Global D01). Adapter: `pipelines/planetary/mola_global.py`.
Verification date: 2026-11-15.

This document records what was actually fetched and read from the NASA PDS
Geosciences Node. It does **not** claim Mars global D01 coverage, a 90 m Mars DEM,
or a Mars reference manifest. MOLA MEGDR native support is 463.0576672 m at
128 pixels/degree; that is not the 90 m `terrain-metrics-1.0.0` support.

## Source identity

| Field | Verified value |
|---|---|
| Provider | NASA PDS Geosciences Node, Washington University in St. Louis |
| PDS4 bundle | `urn:nasa:pds:mgs_mola_topography_derived` version **1.0** |
| Bundle URL | <https://pds-geosciences.wustl.edu/mgs/urn-nasa-pds-mgs_mola_topography_derived/mgs_mola_topography_derived_bundle.xml> |
| Bundle title | *MGS MOLA Derived Topography - MEGDR and SHADR Dataset Bundle* |
| DOI | `10.17189/1z1b-kv84` |
| PDS3 lineage | `MGS-M-MOLA-5-MEGDR-L3-V1.0` |
| 128 ppd collection | `urn:nasa:pds:mgs_mola_topography_derived:data_meg128` version **2.0** |
| 16 ppd collection | `urn:nasa:pds:mgs_mola_topography_derived:data_meg016` version **2.0** |
| Citation | Smith, D. E., et al. (2001), *Mars Orbiter Laser Altimeter: Experiment summary after the first year of global mapping of Mars*, J. Geophys. Res. 106(E10), 23689–23722, doi:10.1029/2000JE001364 |

The bundle contains `meg004`, `meg016`, `meg032`, `meg064`, `meg128`, `polar`, `shadr`,
document collections, a PDS3 index, and a bundle-wide MD5 inventory.

## Verified MEGDR collections

Every product has four sibling files in the same directory:

| File | Role |
|---|---|
| `megt44n000hb.xml` | PDS4 product label (authoritative numeric metadata) |
| `megt44n000hb.lbl` | retained PDS3 label; GDAL's PDS driver reads this for pixel I/O |
| `megt44n000hb.hdr` | ENVI header with the precise map scale and reference pixel |
| `megt44n000hb.img` | raw BSQ image, `SignedMSB2`, one row per strip |

| Collection | Pixel step | Shape per product | Coverage | Products |
|---|---:|---:|---|---|
| `meg004` | 14,817.845349 m (4 ppd) | 720 x 1440 | global, 90°S–90°N | 1 global grid |
| `meg016` | 3,704.4613374 m (16 ppd) | 2880 x 5760 | global, 90°S–90°N | 1 global grid |
| `meg032` | 1,852.2306687 m (32 ppd) | 5760 x 11520 | global, 90°S–90°N | 1 global grid |
| `meg064` | 926.11533434 m (64 ppd) | 5760 x 11520 | 4 tiles, 180° longitude x 90° latitude | 4 topographic tiles |
| `meg128` | 463.0576672 m (128 ppd) | 5632 x 11520 | 16 tiles, 90° longitude x 44° latitude, 88°S–88°N | 16 topographic tiles |

Each collection also has observation-count (`megc...`) and radius (`megr...`) products;
`meg004`/`meg016` additionally have an areoid product (`mega...`). The 128 ppd count
product is `UnsignedByte`; the other verified arrays are `SignedMSB2`.

The 16 ppd global product is the **arbitrary global AOI fallback** (one file, full
latitude range, 33,177,600 bytes). The 128 ppd products give detail but cover only
88°S–88°N; the adapter refuses a 128 ppd request poleward of 88° rather than
upsampling or silently substituting the 16 ppd grid.

## Exact 128 ppd tile inventory

Directory: `.../urn-nasa-pds-mgs_mola_topography_derived/meg128/`.

| Tile (topography) | West–East | South–North | Image bytes | Published MD5 |
|---|---|---:|---:|---|
| `megt88n000hb` | 0–90 | 44–88 | 129,761,280 | `624D06BED9D87E9F4018580C0541441A` |
| `megt88n090hb` | 90–180 | 44–88 | 129,761,280 | `06C26DD17619099D88BB6C88CF81AD94` |
| `megt88n180hb` | 180–270 | 44–88 | 129,761,280 | `5EA3C101064099C97BAF6415D2523944` |
| `megt88n270hb` | 270–360 | 44–88 | 129,761,280 | `C5F8C7BBD423AAFAAB843E7AC1C3DAFB` |
| `megt44n000hb` | 0–90 | 0–44 | 129,761,280 | `904CAEA41A1DB3A2450CC9497B21C3CC` |
| `megt44n090hb` | 90–180 | 0–44 | 129,761,280 | `FCCF8F3707D37684261B840FFE639754` |
| `megt44n180hb` | 180–270 | 0–44 | 129,761,280 | `FDF5D1F06303ACD040B3A37C9FEEE955` |
| `megt44n270hb` | 270–360 | 0–44 | 129,761,280 | `E8E505DB406D7B96C0E690B1682152CE` |
| `megt00n000hb` | 0–90 | −44–0 | 129,761,280 | `E3DED630CDC038B11A915A1BB5AB44AA` |
| `megt00n090hb` | 90–180 | −44–0 | 129,761,280 | `1F2C70C407976DD22E79DFBB3469EBF1` |
| `megt00n180hb` | 180–270 | −44–0 | 129,761,280 | `C23EF690EC423AC06756E4F97BF68D89` |
| `megt00n270hb` | 270–360 | −44–0 | 129,761,280 | `68096468DCC074A4F61BB9A1269670D7` |
| `megt44s000hb` | 0–90 | −88–−44 | 129,761,280 | `38B8E804313B35A6D4689FEF6E46F254` |
| `megt44s090hb` | 90–180 | −88–−44 | 129,761,280 | `FF41B5A352D2ADEC28DA54FF2DE76CC8` |
| `megt44s180hb` | 180–270 | −88–−44 | 129,761,280 | `52EE54C45992FAFA309307266542D95D` |
| `megt44s270hb` | 270–360 | −88–−44 | 129,761,280 | `76CC347EEFB0DA4475C974008F6AB374` |

A full 128 ppd mosaic would be 16 x 129,761,280 = **2,076,180,480 bytes (2.08 GB)**.
No function in the adapter downloads a whole tile or the mosaic.

## PDS4 label facts (read from the actual label)

Representative product: `urn:nasa:pds:mgs_mola_topography_derived:data_meg128:megt44n000hb`,
product version **1.1**.

```xml
<data_type>SignedMSB2</data_type>
<unit>meter</unit>
<elements>5632</elements>    <!-- Line -->
<elements>11520</elements>   <!-- Sample -->
<cart:pixel_resolution_x unit="km/pixel">0.4630576672</cart:pixel_resolution_x>
<cart:pixel_resolution_y unit="km/pixel">0.4630576672</cart:pixel_resolution_y>
<cart:upperleft_corner_x unit="m">-10668848.652</cart:upperleft_corner_x>
<cart:upperleft_corner_y unit="m">2607940.782</cart:upperleft_corner_y>
<cart:west_bounding_coordinate unit="deg">0</cart:west_bounding_coordinate>
<cart:east_bounding_coordinate unit="deg">90</cart:east_bounding_coordinate>
<cart:north_bounding_coordinate unit="deg">44</cart:north_bounding_coordinate>
<cart:south_bounding_coordinate unit="deg">0</cart:south_bounding_coordinate>
<cart:latitude_type>Planetocentric</cart:latitude_type>
<cart:longitude_direction>Positive East</cart:longitude_direction>
<cart:a_axis_radius unit="km">3396.0</cart:a_axis_radius>
<geom:name>IAU2000_MARS</geom:name>
```

* CRS: Mars body-fixed, planetocentric, positive-east simple cylindrical
  (equirectangular), central meridian 180°, sphere `R = 3,396,000 m`,
  `+proj=eqc +lat_ts=0 +lon_0=180 +R=3396000 +units=m`.
* Vertical reference: planetary radius minus the GMM3 (mgm1025) areoid, evaluated
  to degree and order 50.
* Topography: median observed topography in each 0.0078125° bin. Where no MOLA shot
  falls in a bin, the product supplies an **interpolated** value; the count product
  is the only way to distinguish observed from interpolated bins.
* Radius products add `value_offset = 3,396,000 m`; the count product has no unit.
* The 16 ppd global label has `pixel_resolution = 3.7044613374 km/pixel`,
  upper-left `(-10668848.652, 5334424.326)`, and covers 90°S–90°N.

## Georeferencing trap: do not use GDAL's PDS transform

The PDS3 label is retained but its `MAP_SCALE` is rounded to three decimals:
`0.463 <KM/PIXEL>` (128 ppd) and `3.705 <KM/PIXEL>` (16 ppd). GDAL's PDS driver
builds its transform from that rounded value. The PDS4 label and ENVI header carry
the exact `2*pi*R / samples` scale and recomputed upper-left corner.

Measured with rasterio 1.5.2 / GDAL 3.12.2:

| Product | GDAL PDS transform origin | Precise PDS4/ENVI origin | Origin shift |
|---|---|---|---|
| `megt44n000hb` (128 ppd) | (−10,667,983.0, 2,608,079.0) m | (−10,668,848.652, 2,607,940.782) m | **+865.652 m east, +138.218 m north** |
| `megt90n000eb` (16 ppd) | (−10,674,105.0, 5,338,905.0) m | (−10,668,848.652, 5,334,424.326) m | **−5,256.348 m east, +4,480.674 m north** |

GDAL also reports 463.0 m and 3705.0 m spacing instead of 463.0576672 m and
3704.4613374 m. The PDS Geosciences ENVI guide documents exactly this problem
(<https://pds-geosciences.wustl.edu/mgs/urn-nasa-pds-mgs_mola_topography_derived/document/mola_envi.htm>):

> "when GDAL-translated MEGDR maps are compared to other Mars map data ... offsets
> and scale differences are observed ... The value of the MAP_SCALE keyword in the
> MOLA MEGDR labels is not precise enough."

`mola_global.py` therefore opens the `.lbl` for pixel I/O but **overrides the
transform** with

```text
x = (lon - 180°) * pi * R / 180
y =  lat        * pi * R / 180
pixel = 2 * pi * R / (360 * ppd)
```

which matches the PDS4 `upperleft_corner_x/y` to 0.001 m. The remaining GDAL delta
is recorded in every window result (`georeference_delta`) instead of being hidden.
Do not georeference MOLA by opening the `.lbl` (or a GDAL-translated copy) without
this override.

The PDS4 label's upper-left corner is the tile corner, not a pixel centre; pixel
rows run north-to-south and columns west-to-east.

### Integration note for the AOI slice

`pipelines/planetary/mola_aoi.py` (owned by the AOI slice, not this one) currently
calls `analog_pipelines.planetary_coarse.read_metric_window`, which georeferences
from GDAL's PDS transform. A Jezero AOI smoke run at 78.05–78.12°E, 18.45–18.52°N
reported `crs.source.map_scale_resolution_m = 463.0`, i.e. the rounded PDS3 scale,
not the verified 463.0576672 m. Any AOI value that must be tied to precise
lon/lat should either read through `mola_global.read_window` or apply
`mola_global.tile_transform` before reprojection. This is a source-layer finding,
not a claim that the AOI numbers are wrong in magnitude; the window is shifted by
hundreds of metres relative to the PDS4/ENVI georeference.

## Nodata and validity

* No `MISSING_CONSTANT`/missing-value element exists in the PDS3 or PDS4 labels.
* GDAL's PDS driver reports `-32768` for signed arrays and `0` for the 128 ppd
  `UnsignedByte` observation-count array. These are driver defaults, not declared
  product sentinels.
* `0` is a valid MOLA elevation (areoid level) and a valid count (no MOLA shot in
  the bin). The ENVI header's `data ignore value = 0` must **not** be applied.
  Real 0 m cells were observed in `megt44s270hb` rows 0–1.
* The adapter masks only `-32768` for elevation products. It masks nothing for
  counts, so `count = 0` remains a meaningful "no observation" bin.
* Sampled full rows in `megt88n000hb`, `megt44s270hb`, and `megt90n000eb` contained
  no `-32768`; every sampled elevation pixel was finite.
* The count product is the honest observed/interpolated guide. At the Jezero
  test window, counts ranged 0–4 and only about 45% of bins had at least one shot,
  while topography was 100% finite because the product interpolates empty bins.
  Never report the interpolated topography as 100% observed.

## Access: byte ranges work; no staging is required

HEAD requests on the actual objects (2026-11-15):

```text
$ curl -sSI .../meg128/megt44n000hb.img
HTTP/2 200
content-length: 129761280
content-type: application/octet-stream
accept-ranges: bytes
last-modified: Mon, 31 Mar 2003 23:45:04 GMT
etag: "004a8ddff7c21:0"

$ curl -sSI .../meg016/megt90n000eb.img
HTTP/2 200
content-length: 33177600
accept-ranges: bytes
```

A 1-byte range request returns `206`. A 32-row source range at the Jezero tile
(rows 3264–3295, columns are the full 11,520-sample row) was fetched explicitly:

```text
$ curl -r 75202560-75939839 .../meg128/megt44n000hb.img
HTTP/2 206
accept-ranges: bytes
content-length: 737280
content-range: bytes 75202560-75939839/129761280
```

737,280 bytes = 32 rows x 11,520 samples x 2 bytes, exactly the touched-row bound.
Decoding that byte range as big-endian `int16` gave the same 32 x 32 Jezero subset
as the GDAL window read (min −2330 m, max −1032 m).

GDAL `/vsicurl/` reads a bounded window through the **PDS3 `.lbl`** path
(`/vsicurl/https://.../megt44n000hb.lbl`); GDAL resolves the sibling `.img`. Opening
the remote `.img` directly is not supported because GDAL does not auto-discover the
remote ENVI header, and the adapter intentionally uses the PDS3 label anyway.

Because the PDS3 image is row-striped (one row per block), a window read transfers
whole rows:

```text
10 x 10 pixel AOI  -> 10 rows x 23,040 bytes = 230,400 B
                    + 128 KiB label/header allowance
                    -> 361,472 B upper bound
```

The adapter's default budget is 8 MB and 512 x 512 pixels; a full 129.7 MB tile is
refused. `estimated_read_bytes` is a conservative bound, not a measured HTTP byte
count, because GDAL issues its own merged range requests.

A local staged tile is optional for offline/reproducible reruns. This module does
**not** implement a tile downloader: an operator may stage
`<product>.lbl`/`.img`/`.hdr` manually, and `--local-tile-dir` verifies the size
(and MD5 with `verify_staged_tile`). This keeps an accidental 129.7 MB download out
of normal use.

## Checksums

Bundle MD5 inventory (407 entries):
<https://pds-geosciences.wustl.edu/mgs/urn-nasa-pds-mgs_mola_topography_derived/urn-nasa-pds-mgs_mola_topography_derived.md5>

```text
904CAEA41A1DB3A2450CC9497B21C3CC  meg128\megt44n000hb.img
357C759AB342140D2D008443E0A68015  meg016\megt90n000eb.img
```

The adapter can fetch and match this index (`--checksums`, `published_md5`). Because
no full tile was downloaded, **no full-tile MD5 was recomputed here**; the published
values are recorded as source provenance, not as a local verification claim.

## Licence

`MOLA_LICENSE_STATUS = "not_verified"`.

The PDS4 bundle label, both collection labels, the product labels, `readme.txt`, and
the ENVI guide state no licence, copyright, or redistribution terms. The archive is
publicly accessible PDS data, but this repository does not infer CC0 or any other
licence from that. The maintainer must confirm redistribution terms before derived
values are committed.

## Adapter API and commands

`pipelines/planetary/mola_global.py` provides:

* `tiles(ppd, role)` / `tile_for_lonlat(lon, lat)` / `tiles_for_lonlat_bbox(...)`
  for the verified 4/16/32/64/128 ppd inventories.
* `tile_transform(tile)` for the precise PDS4/ENVI georeference.
* `inspect_source(tile)` and `read_window(west, south, east, north, ...)` for a
  bounded, single-tile read with byte and pixel budgets.
* `parse_pds4_label(...)`, `inspect_remote_product(...)`, and
  `verify_pds4_metadata(...)` to fail closed if a label changes.
* `probe_remote(...)`, `fetch_checksum_index()`, `published_md5(...)`, and
  `verify_staged_tile(...)`.
* `write_window_geotiff(...)` for a small local window plus JSON sidecar.

CLI examples (all network output below is real):

```bash
# Offline inventory
uv run python pipelines/planetary/mola_global.py --list-collections
uv run python pipelines/planetary/mola_global.py --list-tiles --ppd 128

# Bounded access probe and PDS4 label verification
uv run python pipelines/planetary/mola_global.py --probe --metadata megt44n000hb
uv run python pipelines/planetary/mola_global.py --verify --metadata megt44n000hb

# Read a 10 x 10 native window at Jezero and write a small GeoTIFF
uv run python pipelines/planetary/mola_global.py \
  --window 78.05 18.45 78.12 18.52 --ppd 128 \
  --output /tmp/mola-jezero-128.tif

# Global arbitrary-AOI fallback at 16 ppd (coarse; 3.7 km native)
uv run python pipelines/planetary/mola_global.py --window 10 80 20 89 --ppd 16
```

Observed `--probe`:

```json
{
  "product_id": "megt44n000hb",
  "label_status": 200,
  "image_status": 206,
  "image_bytes": 129761280,
  "accept_ranges": true,
  "range_verified": true,
  "etag": "\"004a8ddff7c21:0\"",
  "last_modified": "Mon, 31 Mar 2003 23:45:04 GMT"
}
```

Observed `--verify`:

```json
{
  "verified": true,
  "product_id": "megt44n000hb",
  "logical_identifier": "urn:nasa:pds:mgs_mola_topography_derived:data_meg128:megt44n000hb",
  "version_id": "1.1",
  "shape": [5632, 11520],
  "data_type": "SignedMSB2",
  "unit": "meter",
  "value_offset_m": 0.0,
  "pixel_resolution_m": 463.0576672,
  "upperleft_m": [-10668848.652, 2607940.782],
  "bounds_lonlat": [0.0, 0.0, 90.0, 44.0],
  "a_axis_radius_km": 3396.0,
  "latitude_type": "Planetocentric",
  "longitude_direction": "Positive East",
  "reference_frame": "IAU2000_MARS"
}
```

Observed bounded window at Jezero (10 x 10 native pixels):

```json
{
  "product_id": "megt44n000hb",
  "window": [9990, 3261, 10, 10],
  "shape": [10, 10],
  "lon_lat_bounds": [78.046875, 18.4453125, 78.125, 18.5234375],
  "valid_fraction": 1.0,
  "value_min": -2441.0,
  "value_max": -2145.0,
  "estimated_read_bytes": 361472,
  "georeference_override": true,
  "georeference_delta": {
    "origin_east_m": 865.6515909,
    "origin_north_m": 138.2185,
    "resolution_east_m": -0.0576672,
    "resolution_north_m": -0.0576672
  }
}
```

Observed 16 ppd fallback window (10° x 9°, 144 x 160 native pixels):

```json
{
  "product_id": "megt90n000eb",
  "shape": [144, 160],
  "valid_fraction": 1.0,
  "estimated_read_bytes": 1789952,
  "georeference_delta": {
    "origin_east_m": -5256.348,
    "origin_north_m": 4480.674,
    "resolution_east_m": 0.5387,
    "resolution_north_m": 0.5387
  }
}
```

Observed count role at the same Jezero window:

```json
{
  "product_id": "megc44n000hb",
  "valid_fraction": 1.0,
  "value_min": 0.0,
  "value_max": 4.0,
  "value_mean": 0.86,
  "nodata_policy": "counts: 0 is a valid count (no MOLA shot); no value masked"
}
```

## Tests

```bash
uv run pytest pipelines/planetary/tests/test_mola_global.py -q
# 23 passed

ANALOG_RUN_NETWORK_TESTS=1 uv run pytest \
  pipelines/planetary/tests/test_mola_global_online.py -q -m network
# 8 passed

uv run ruff check pipelines/planetary/mola_global.py \
  pipelines/planetary/tests/test_mola_global.py \
  pipelines/planetary/tests/test_mola_global_online.py
# All checks passed
```

The offline tests pin the collection shapes/resolutions, tile selection at Jezero,
Olympus and the southern highlands, the ±88° limit, the precise vs GDAL transform
delta, the 10 x 10 Jezero pixel window, the striped-row byte bound, cross-tile
failure, PDS4 parsing/verification (including a changed-resolution rejection), and
the nodata policy (0 retained, `-32768` masked for elevation, nothing masked for
counts). The online tests re-verify the real PDS4 labels, range support, a bounded
128 ppd Jezero window, a 16 ppd polar window, the count-vs-topography distinction,
and the published MD5 index.

## Verified vs unavailable

Verified here:

* PDS4 bundle/collection/product URLs, versions, shapes, `SignedMSB2`/`UnsignedByte`,
  metre units, `value_offset`, planetocentric frame, 3396.0 km sphere, and the exact
  463.0576672 m / 3704.4613374 m resolutions.
* HTTP 200 + `Accept-Ranges: bytes`, a verified 206 one-byte range, and a 737,280-byte
  bounded row range with the exact `Content-Range`.
* Bounded `/vsicurl/` reads through the PDS3 label, including Jezero, a 16 ppd polar
  window, radius with `+3,396,000 m`, and observation counts.
* Precise georeferencing from PDS4/ENVI and the measured GDAL origin/scale delta.
* Published MD5 values for the 128 ppd and 16 ppd products.

Not verified / unavailable:

* **Licence and redistribution terms**: not stated anywhere in the bundle.
* **Local full-tile checksum**: no tile was downloaded, so the published MD5 is not a
  local digest verification.
* **Per-pixel elevation uncertainty / interpolation mask**: the product supplies no
  per-pixel error layer; observation counts indicate only how many shots fell in a
  bin, not the error of the interpolated median.
* **90 m Mars D01**: MEGDR 128 ppd is 463 m native. It cannot answer a 90 m
  `terrain-metrics-1.0.0` request without upsampling, which this repository does not
  do. The adapter is a source/access layer, not a 90 m Mars reference.
* **Global 128 ppd coverage beyond ±88°**: the 16 tiles stop at 88°S/N. The 16 ppd
  global grid covers the poles but is 3.7 km native; it is not a substitute at 128 ppd.
* **Jezero CTX as global Mars**: the Mars 2020 CTX mosaic is a local 20 m product.
  It is not used here and does not make MOLA global.

## Note on the PDS MEGDR landing page

The PDS Geosciences page <https://pds-geosciences.wustl.edu/missions/mgs/megdr.html>
is the PDS3-era entry point. The PDS4 bundle inventory above is authoritative for
this work. In particular:

* `meg016/megr90n000eb.img` is the **16 ppd global** radius product, not 128 ppd.
* `meg064/megr90n000gb.img` and `megr90n180gb.img` are two of four 64 ppd tiles
  (0–90°N and 0–90°S, each 180° of longitude), not a simple "north/south" pair.
* The 128 ppd tiles are the 16 `meg128/megt{88n,44n,00n,44s}{000,090,180,270}hb.img`
  products listed above.

## Non-claims

This is one verified source adapter and a bounded reader. It does not add a Mars
manifest, does not change the similarity index, does not score across bodies, and
does not turn a 463 m median/interpolated product into an observed 90 m terrain
measurement.
