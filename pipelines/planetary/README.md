Current bounded terrain expansion: see [TERRAIN_EXPANSION.md](../../docs/TERRAIN_EXPANSION.md).
The following original ingestion evidence predates the expansion; current counts and
sources are in the committed manifests and dataset catalog.

# Planetary / Moon reference — real LOLA 5 m/pix DEM

This pipeline produces **one real, observed Moon reference window** for the Planetary Analog
Explorer from the official NASA GSFC Planetary Geodesy Data Archive (PGDA) LRO-LOLA
5 m/pixel south-pole DEMs. It reads only a bounded window from the published GeoTIFF with
HTTP range requests, caches the small derived window under `data/cache/lola/` (gitignored),
and runs the shared `analog_pipelines.features` extractor with `body="Moon"`.

**Status: Moon reference enabled and verified** — the committed manifest
`pipelines/planetary/manifests/lola_moon_reference.json` was produced from a real
414 x 414 pixel source window around the official Site04 (Shackleton rim) ROI 2.
The measured analysis window is 100% valid. This is one local 1.98 km window, **not**
global lunar coverage and not a landing-site recommendation.

See `NASA_LOLA_STATUS.md` for the full access log, exact HTTP headers, byte counts,
the `PROJ_IGNORE_CELESTIAL_BODY` extractor workaround, and integration output.

## Quick start

Run from the repository root:

```bash
# 1. Print the verified product URLs/sizes and official ROI centres. No network.
uv run python pipelines/planetary/lola_import.py --list-products

# 2. Inspect the real source metadata and print the plan. No window download,
#    no manifest write. A remote uncached source uses small metadata range reads.
uv run python pipelines/planetary/lola_import.py --dry-run

# 3. Real bounded range read + extraction + manifest validation/write.
uv run python pipelines/planetary/lola_import.py

# 4. Ignore the cached window and re-read from NASA.
uv run python pipelines/planetary/lola_import.py --refresh

# 5. Offline tests (no network).
uv run pytest pipelines/planetary/tests -q
```

Useful options:

| Option | Meaning |
| --- | --- |
| `--source URL_OR_PATH` | Real LOLA surface DEM URL, `/vsicurl/` URL, `file://` URL, or local path. Default is the verified Site04 LDEM. |
| `--center-x`, `--center-y` | ROI centre in the source map CRS, metres. Both required together. |
| `--bbox XMIN YMIN XMAX YMAX` | Explicit source-CRS bbox; its centre is used (mutually exclusive with the centre options). |
| `--reference-id`, `--display-name`, `--roi-name`, `--roi-provenance` | Manifest labels. Defaults describe Site04 ROI 2. |
| `--dataset-id`, `--product-id`, `--product-title`, `--provider`, `--citation` | Dataset metadata for a different real LOLA product. Defaults are Site04 + Barker et al. (2021). |
| `--cache-dir PATH` | Default `data/cache/lola` (gitignored). |
| `--output PATH` | Default `pipelines/planetary/manifests/lola_moon_reference.json`. |
| `--max-window-mb N` | Conservative source-window size budget (default 30 MiB). The real window estimate is 5.43 MB. |
| `--timeout SECONDS` | Per-request HTTP/GDAL timeout (default 180 s). |
| `--max-retries N` | 0–2 retries per network operation (default 2). |
| `--refresh` | Ignore the verified cache and re-read from the source. |
| `--dry-run` | Inspect metadata and print the plan; no analysis window download and no manifest write. |
| `--list-products` | Offline product catalogue. |

A failed run exits `2`, prints the exact error, and writes no manifest. An existing
manifest is left untouched. No synthetic values are ever substituted.

## Source discovery (actually inspected)

Product page: <https://pgda.gsfc.nasa.gov/products/78> — *High-Resolution LOLA Topography
for Lunar South Pole Sites* (Michael K. Barker, NASA GSFC PGDA). Product README:
<https://pgda.gsfc.nasa.gov/data/LOLA_5mpp/README> (1,829 bytes).

The page documents:

- LDEM: surface-interpolated elevation Z in metres.
- LDEC: LOLA return count per pixel.
- Slope map in degrees; total Z uncertainty in metres (RMS error); slope uncertainty in
  degrees (RMS error); 100 clones; XYZI point clouds; ROI shapefiles.
- All raster products are **5 m/pixel GeoTIFFs with south polar stereographic X/Y in
  metres**, **MOON_ME reference frame of ephemeris DE421** (product documentation).
- X/Y follow the GMT pixel-registered convention (coordinates are pixel centres).
- The 5 m LDEMs are about **90% interpolated pixels** because LOLA's cross-track and
  inter-spot spacing leaves only about 10% direct returns.
- Median RMS Z error is about **0.30–0.50 m**; median RMS slope error about
  **1.5–2.5°**. Track geolocation uncertainty is about **10–20 cm horizontally** and
  **2–4 cm vertically**.
- The **Data Usage Policy** asks users to cite Barker et al. (2021).

The Site04 (Shackleton rim) files were HEAD-checked on 2026-11-14. All returned
HTTP 200, `Accept-Ranges: bytes`, `Content-Type: image/tiff`:

| Role | Product ID | Bytes | Last-Modified |
| --- | --- | ---: | --- |
| Surface LDEM (used) | `Site04_final_adj_5mpp_surf` | 40,980,806 | Fri, 05 Jun 2020 18:40:02 GMT |
| Slope | `Site04_final_adj_5mpp_slp` | 40,980,772 | Mon, 08 Jun 2020 22:05:53 GMT |
| Total Z uncertainty | `Site04_final_adj_5mpp_toterr` | 40,980,808 | Fri, 05 Jun 2020 18:40:15 GMT |
| Slope uncertainty | `Site04_final_adj_5mpp_slperr` | 40,980,806 | Fri, 05 Jun 2020 18:40:22 GMT |
| Return count | `Site04_final_adj_5mpp_ldec` | 40,980,740 | Fri, 05 Jun 2020 18:40:08 GMT |
| ROI shapefile | `Site04_ROIs.tgz` | 1,712 | Tue, 01 Dec 2020 13:06:48 GMT |

Exact surface URL:
<https://pgda.gsfc.nasa.gov/data/LOLA_5mpp/Site04/Site04_final_adj_5mpp_surf.tif>

The larger 87–90°S 5 m mosaic (PGDA product 81,
<https://pgda.gsfc.nasa.gov/products/81>) is documented but **not used**: its LDEM is
3,465,285,714 bytes (3.47 GB), slope 5,255,076,239 bytes, count 145,895,726 bytes, far
above the 50 MB investigation budget. The Site04 41 MB product plus a 5.4 MB window is
the smaller official alternative.

## Raster properties (read from the actual file)

| Property | Observed value |
| --- | --- |
| Driver / layout | GTiff, striped, **not tiled**, **no overviews**, uncompressed |
| Size | 3200 x 3200 px, one band |
| Spacing | 5.0 m x 5.0 m |
| Bounds (source CRS) | X −9,000 … 7,000 m; Y −15,000 … 1,000 m (16 x 16 km) |
| Data type / nodata | float32 / NaN |
| CRS | unnamed lunar south polar stereographic, sphere R = 1,737,400 m, central meridian 0, metre axes; `+proj=stere +lat_0=-90 +lat_ts=-90 +lon_0=0 +x_0=0 +y_0=0 +R=1737400 +units=m +no_defs` |
| Projection method | Polar Stereographic (variant B), EPSG method 9829 (as reported by PROJ) |
| Block shape | one full row per strip (`1 x 3200`) |
| HTTP range read | `Accept-Ranges: bytes`; a 1-byte range request returned HTTP 206; GDAL `/vsicurl/` read the window successfully |

The GeoTIFF CRS itself is unnamed and has no lunar datum name in the WKT; the
**MOON_ME/DE421** statement comes from the PGDA product documentation. No EPSG code
exists for this lunar CRS, so the manifest stores WKT. The dataset `source_crs` and the
reference `crs` are the same WKT2 string.

The source is **not a COG**: it is an uncompressed row-striped GeoTIFF with no
overviews. Range reads still work because each requested row is a contiguous strip.
A 2 km window touches 414 full rows, so the transfer is about
**414 × 3200 × 4 B = 5,299,200 B** plus TIFF/IFD overhead. The importer's conservative
estimate is **5,430,272 bytes** (5.43 MB) including 128 KiB overhead; the derived cache
file is **433,015 bytes**. No 40.98 MB product was downloaded. The first real window read
took about 42–54 s including metadata/probe on the NASA server; cache-only reruns take
under 1 s.

### Bytes and access timestamps

- Source HEAD: `ETag: W/"2715146-17285c863d0"`,
  `Last-Modified: Fri, 05 Jun 2020 18:40:02 GMT`, `Content-Length: 40,980,806`.
- Window fetch: `2026-11-14T06:18:41Z`; cache file
  `data/cache/lola/lola-window-aee94d03b7b73be8ed41.tif`, 433,015 bytes,
  SHA-256 `702aac2f93e0f36dd1025900d4e5021be17411ee735d93df7191cc33b65cf095`.
- Manifest `generated_at` is refreshed on each successful run; the dataset
  `access_timestamp` is preserved from the original window fetch on cache hits.

## ROI selection (documented, not guessed)

The official `Site04_ROIs.tgz` was downloaded (1,712 bytes) and parsed. Its DBF has four
records with only `X` and `Y` fields (source metres):

| Record | X | Y | Polygon bbox (source metres) |
| ---: | ---: | ---: | --- |
| 1 | −2812.5 | −6647.5 | −2842.5 … −2782.5, −6802.5 … −6522.5 |
| 2 (used) | −2757.5 | −6342.5 | −2792.5 … −2712.5, −6432.5 … −6262.5 |
| 3 | −2677.5 | −6107.5 | −2702.5 … −2642.5, −6167.5 … −6052.5 |
| 4 | −2657.5 | −5957.5 | −2687.5 … −2627.5, −6027.5 … −5902.5 |

ROI 2 was chosen as a documented ROI; this is **not** a claim that it is the best or most
illuminated ROI. Its inverse-projected centre is approximately
**lon = −156.502260°, lat = −89.771925°** (east-positive degrees). Longitude is
ill-conditioned this close to the pole, so the source X/Y centre is authoritative.

The repository's default is this ROI 2 centre. `--center-x/--center-y` or `--bbox` can
select a different documented source-CRS location; the importer validates that the full
1980 m analysis window lies inside the source footprint.

## Processing

1. Compute the analysis window: 1980 m x 1980 m centred on the ROI.
2. Add 45 m of padding (about half a 90 m cell) so the extractor's bilinear neighbourhood
   is complete; compute a 2070 m x 2070 m source crop (`from_bounds` on the real
   transform). The Site04 crop is 414 x 414 px at 5 m.
3. Refuse the read if the conservative block/row estimate exceeds
   `--max-window-mb` (default 30 MiB) or if a remote server lacks byte-range support for
   an oversized object.
4. Range-read the crop with GDAL `/vsicurl/`, masked, converting nodata/NaN to NaN;
   retry at most twice with per-request timeouts; write a small deflate-compressed
   GeoTIFF cache under `data/cache/lola/` plus a JSON sidecar (source URL, ETag,
   Last-Modified, bounds, CRS, checksum, access timestamp).
5. Call the shared extractor with `body="Moon"` and `center_xy`. It resamples the crop
   bilinearly to the exact 22 x 22 grid at 90 m **in the source projected CRS** and
   computes:
   - `slope_mean_deg` — mean Horn 3x3 gradient angle (degrees).
   - `slope_p90_deg` — 90th percentile gradient angle (degrees).
   - `terrain_relief_p95_p05_m` — p95 − p05 elevation (metres).
   - `roughness_std_m` — standard deviation of residuals after a least-squares plane fit
     (metres).
6. Validate the resulting document with `contracts/pipeline-manifest.schema.json`
   (Draft 2020-12) and `analog_api.domain.manifest.Manifest`; write atomically only if
   both pass and the valid fraction is at least 0.8.

### Measured reference (2026-11-14)

`reference_id`: `moon-shackleton-rim-site04-roi2`  
`body`: `Moon`  
`dataset_id`: `nasa-lola-site04-5mpp-surf`  
`window_size_m`: 1980; `analysis_resolution_m`: 90; `valid_fraction`: 1.0  
`lat`/`lon`: −89.77192511 / −156.50226014  
`window_checksum`: `sha256:0db13ffd3e0d49c04ce468ce5312f4cd46fa6b2f4f99854dc4def100d0f08050`

| Feature | Value | Unit |
| --- | ---: | --- |
| `slope_mean_deg` | 17.895873 | degree |
| `slope_p90_deg` | 28.777052 | degree |
| `terrain_relief_p95_p05_m` | 390.805907 | meter |
| `roughness_std_m` | 102.805473 | meter |

All four observations are `quality: "verified"` with
`method_version: "terrain-metrics-1.0.0"` and `extraction_scale_m: 90.0`.
The 414 x 414 source crop's finite elevations range from **1199.06 m to 1739.12 m**
(product metres). These are real values from the actual NASA window, not synthetic data.

## Scientific caveats

- **Most 5 m pixels are interpolated.** The product page states about 90% interpolation.
  Do not interpret 5 m texture as independent LOLA measurements. The 90 m analysis scale
  is deliberate; it still inherits interpolation error but is less sensitive to 5 m
  artefacts.
- **Vertical uncertainty.** Median RMS Z error is about 0.30–0.50 m and median RMS slope
  error about 1.5–2.5° per the product page. Track geolocation uncertainty is about
  10–20 cm horizontally and 2–4 cm vertically. These are product-level error estimates,
  not a per-pixel error propagation through the four 90 m metrics.
- **Vertical datum.** The product documents Z in metres but the GeoTIFF WKT does not name
  a vertical datum. The four metrics are relative (slope, percentile relief, detrended
  residual), so a constant vertical offset does not affect them.
- **One local window only.** This is a 1.98 km window at the Shackleton rim. It is not a
  global lunar survey, not an illumination study, and not a landing-site recommendation.
  The official illumination ranking in the product page applies to other sites/ROIs and
  is not claimed here.
- **No WGS84 bbox.** `bbox` is `null` because this is a non-Earth reference; do not
  compare the lunar lat/lon with Earth WGS84 geometry. The source X/Y centre is the
  authoritative location.
- **Not a COG.** The source is a striped uncompressed GeoTIFF without overviews. A larger
  window would transfer proportionally more row-strip data. The importer's byte budget
  and window size are the safeguards.
- **Official slope map not used.** PGDA publishes a 5 m slope GeoTIFF, but the four
  registry metrics are intentionally derived from elevation by the shared extractor so
  Moon and Earth values use identical operators.
- **Product 81 not used.** The 87–90°S mosaic is 3.47 GB (LDEM), far above the budget.
- **MOON_ME/DE421 is documented, not re-derived here.** The GeoTIFF CRS is an unnamed
  lunar sphere; the frame statement comes from the PGDA product page/README.

## License and citation

- The PGDA product page has a **Data Usage Policy** requesting citation of Barker et al.
  (2021). It carries no restrictive license notice.
- The NASA Science Data Portal's [Science Data Licenses](https://science.data.nasa.gov/about/license)
  page states that data from a NASA-led mission are licensed as **CC0-1.0** unless the
  file is marked with a restrictive notice. The manifest therefore records
  `CC0-1.0` with the NASA license URL and the citation request.
- Citation: **Barker, M.K., et al. (2021)**, *Improved LOLA Elevation Maps for South Pole
  Landing Sites: Error Estimates and Their Impact on Illumination Conditions*,
  Planetary & Space Science 203, 105119,
  [doi:10.1016/j.pss.2020.105119](https://doi.org/10.1016/j.pss.2020.105119).
- Site naming follows **Mazarico, E., et al. (2011)**, *Illumination conditions of the
  lunar polar regions using LOLA topography*, Icarus 211(2), 1066–1081,
  [doi:10.1016/j.icarus.2010.10.030](https://doi.org/10.1016/j.icarus.2010.10.030).

## Sharing contract / extractor workaround (important)

The shared extractor is imported unchanged. Its `center_xy` branch transforms the lunar
projected CRS to EPSG:4326 to report an approximate centre/bbox. PROJ rejects that
cross-celestial-body transform by default:

```text
pyproj.exceptions.ProjError: Error creating Transformer from CRS.:
(Internal Proj Error: proj_create_operations: Source and target ellipsoid do not belong
to the same celestial body (Moon vs Earth).)
```

The importer sets `PROJ_IGNORE_CELESTIAL_BODY=YES` for the process. **All four metrics are
computed in the source projected CRS**, so the override does not change them. The shared
extractor source was not modified; this is a runtime workaround that the lead may want
to move into the extractor's lunar branch or make body-aware. The importer's manifest
`lat`/`lon` are computed by inverse-projecting the ROI X/Y with the same override; the
extractor's corner-average `.center_lon/.center_lat` differ by about 0.8° in longitude at
this near-pole window and are not used as the authoritative centre.

## Enable / disable the Moon reference

The API discovers planetary manifests from `ANALOG_MANIFEST_DIR_PLANETARY`, default
`pipelines/planetary/manifests`. The Moon reference is enabled because
`lola_moon_reference.json` is present there.

To disable it without touching code:

```bash
# Option A: remove/rename the committed reference (keeps the pipeline runnable)
mv pipelines/planetary/manifests/lola_moon_reference.json \
   pipelines/planetary/manifests/lola_moon_reference.json.disabled

# Option B: point the API at an empty planetary manifest directory
ANALOG_MANIFEST_DIR_PLANETARY=/tmp/no-planetary-manifests make api
```

With no planetary JSON, `GET /api/v1/references` returns no Moon reference and
`POST /api/v1/search` cannot use `reference_id: moon-...`. Earth candidates are unaffected.
The pipeline itself is not disabled by the manifest's absence; `--dry-run` and
`--list-products` still work offline.

## Tests and checks

```bash
uv run pytest pipelines/planetary/tests -q     # 37 passed, no network
uv run ruff check pipelines/planetary          # all checks passed
uv run ruff format --check pipelines/planetary
```

The tests cover:

- Manifest JSON Schema + `analog_api.domain.manifest.Manifest` validation of the real
  reference, including observed/verified invariants.
- Rejection of a modified manifest with a synthetic observation or a non-Moon body.
- URL/path parsing, `/vsicurl/` handling, explicit-centre/bbox resolution, CRS validation
  (geographic, non-metre, non-lunar), window-byte estimation for striped and tiled
  layouts, cache-key determinism, and byte formatting.
- A full local path through `prepare_window` -> cache -> shared extractor using a
  **TEST-ONLY** synthetic lunar-like GeoTIFF with a known 10% plane grade
  (`atan(0.1) = 5.7106°`). The synthetic manifest is written only under pytest's
  `tmp_path`, is marked `data_kind: "synthetic"` with `source_kind: "synthetic"`, and
  every observation is `quality: "synthetic"`. Its quality flags are
  `synthetic_test_fixture` (not the LOLA flags). It is never written to the real
  manifest.
- CLI `--dry-run` writes no manifest/cache; `--list-products` works offline.

## Future Mars adapter (interface only)

`lola.py` defines `PlanetaryReferenceProvider` and a `MolaReferenceProvider` stub. The
stub raises `mola_not_implemented` and makes **no Mars data claim**. A real MOLA adapter
must:

1. Read a real MOLA product's metadata and a bounded window (same byte/timeout/cache
   rules), with a documented Martian projected metric CRS.
2. Return a `PreparedWindow` with `body="Mars"`, `source_kind="martian_dem"`, the real
   source citation/license, and a cache checksum.
3. Use the shared extractor with `body="Mars"` and document its own vertical
   uncertainty/interpolation caveats.
4. Validate against the same manifest contract before writing, using
   `validate_manifest(..., expected_body="Mars")` and its own synthetic/observed
   quality-flag policy.

No Mars raster, URL, resolution, or value is asserted by this repository yet.

## Files

| Path | Committed? | Purpose |
| --- | --- | --- |
| `pipelines/planetary/lola.py` | yes | Source constants, provider, CRS/window/cache/manifest helpers. |
| `pipelines/planetary/lola_import.py` | yes | CLI (`uv run python pipelines/planetary/lola_import.py`). |
| `pipelines/planetary/manifests/lola_moon_reference.json` | yes | Real observed Moon reference. |
| `pipelines/planetary/tests/` | yes | Offline pytest suite. |
| `pipelines/planetary/NASA_LOLA_STATUS.md` | yes | Exact access log, headers, blocker/workaround, integration output. |
| `pipelines/planetary/README.md` | yes | This document. |
| `samples/planetary/README.md` | yes | Committed vs non-committed artifacts. |
| `data/cache/lola/*.tif`, `*.json` | no (gitignored) | Derived 414 x 414 window + sidecar. |
| Full 40.98 MB Site04 GeoTIFF / 3.47 GB product 81 | no | Never downloaded/stored. |

## Integration notes

The real Moon reference was seeded and queried with the existing API (no API edits by
this pipeline):

```bash
ANALOG_DATABASE_URL=sqlite+pysqlite:////tmp/analog-moon.db \
ANALOG_MANIFEST_DIR=pipelines/earth/manifests \
ANALOG_MANIFEST_DIR_PLANETARY=pipelines/planetary/manifests \
uv run python -m analog_api.cli seed
# Observed: Seeded 3 manifest(s). Index: 5 candidate(s), 3 dataset(s),
#           1 reference(s). data_version=dv-19f54b4539c8
# (The current API also discovers pipelines/worldcover/manifests as context by
#  default; that is an independent change from another pipeline, not this one.)

GET /api/v1/references
# 200, one reference: moon-shackleton-rim-site04-roi2 (Moon, valid_fraction 1.0)

POST /api/v1/search
{"body":"Moon","reference_id":"moon-shackleton-rim-site04-roi2","limit":5}
# 200; indexed_candidate_count=5, eligible_candidate_count=5, excluded=0
# Rank 1: earth-death-valley, distance 0.2448406380
# Rank 2: earth-lanzarote, distance 1.6989523030
# Rank 3: earth-rio-tinto, distance 2.2867205586
# Rank 4: earth-devon-island, distance 2.4095961442
# Rank 5: earth-atacama-desert, distance 3.0452564997
```

This proves the real Moon reference flows through manifest validation, seeding,
persistence, the references endpoint, and the scoring/search endpoint. The ranking is a
terrain-shape comparison against the five indexed Earth windows, not a global survey and
not a physical-equivalence claim.
