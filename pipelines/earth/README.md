Current bounded terrain expansion: see [TERRAIN_EXPANSION.md](../../docs/TERRAIN_EXPANSION.md).
The following original ingestion evidence predates the expansion; current counts and
sources are in the committed manifests and dataset catalog.

# Earth ingestion — Copernicus DEM GLO-90

Real, observed terrain windows for the Planetary Analog Explorer, read on demand from the
public Copernicus DEM GLO-90 Cloud Optimized GeoTIFFs on AWS Open Data. Every committed
number in `manifests/copernicus_glo90_earth.json` was measured from those rasters with the
shared extractor (`analog_pipelines.features`, method `terrain-metrics-1.0.0`). Nothing is
synthetic or interpolated by this pipeline beyond the documented 90 m bilinear resampling.

## Quick start

Run from the repository root:

```bash
# 1. See resolved tiles/URLs/cache paths. No network, no writes.
uv run python pipelines/earth/ingest_copernicus.py --dry-run

# 2. Real ingestion of all five candidate windows.
uv run python pipelines/earth/ingest_copernicus.py

# 3. Re-read one window and ignore its cache.
uv run python pipelines/earth/ingest_copernicus.py --only death-valley --force

# 4. Validate the manifest with both contracts.
uv run python - <<'PY'
import json, jsonschema
from pathlib import Path
from analog_api.domain.manifest import Manifest
m = json.loads(Path("pipelines/earth/manifests/copernicus_glo90_earth.json").read_text())
jsonschema.Draft202012Validator(
    json.loads(Path("contracts/pipeline-manifest.schema.json").read_text())
).validate(m)
Manifest.model_validate(m)
print("manifest OK")
PY

# 5. Offline test suite (no network).
uv run pytest pipelines/earth/tests -q
```

Other options: `--only atacama-desert,rio-tinto` (repeatable or comma separated),
`--cache-dir PATH`, `--manifest PATH`, `--timeout 25` (seconds per attempt),
`--max-retries 2` (capped at 2 by the network policy). A failed window is printed and
omitted; if every window fails no manifest is written. Exit codes: `0` all selected
windows succeeded, `2` some failed but a manifest was written, `1` nothing succeeded.

## Source verified for this run

| Item | Value |
| --- | --- |
| AWS Open Data registry | https://registry.opendata.aws/copernicus-dem/ |
| S3 bucket / region | `s3://copernicus-dem-90m` / `eu-central-1` |
| Unsigned HTTPS base | https://copernicus-dem-90m.s3.amazonaws.com/ |
| Bucket readme | https://copernicus-dem-90m.s3.amazonaws.com/readme.html |
| Tile list | https://copernicus-dem-90m.s3.amazonaws.com/tileList.txt |
| Product ID | `COP-DEM_GLO-90-DGED` (source product; COG mirror derived by Sinergise) |
| Data release | Copernicus DEM 2021 release, GLO-90 |
| Object key pattern | `<tile_id>/<tile_id>.tif` |
| Tile id pattern | `Copernicus_DSM_COG_30_<N|S>dd_00_<E|W>ddd_00_DEM` |

`30` in the file name is **3.0 arc-seconds**, not 30 metres. Longitudes in tile ids are
always three digits (`W014`, `W069`), latitudes two (`S24`, `N75`). The tile is named by
the floor of its south-west integer degree. All five keys below were confirmed against
`tileList.txt` and an HTTP `HEAD` returning `200`:

| Candidate | Centre (lon, lat) | Tile id | Object bytes |
| --- | --- | --- | ---: |
| earth-atacama-desert | -68.40, -23.50 | `Copernicus_DSM_COG_30_S24_00_W069_00_DEM` | 4,168,525 |
| earth-death-valley | -117.10, 36.25 | `Copernicus_DSM_COG_30_N36_00_W118_00_DEM` | 5,130,284 |
| earth-lanzarote | -13.63, 29.04 | `Copernicus_DSM_COG_30_N29_00_W014_00_DEM` | 355,457 |
| earth-devon-island | -89.85, 75.40 | `Copernicus_DSM_COG_30_N75_00_W090_00_DEM` | 1,502,538 |
| earth-rio-tinto | -6.55, 37.69 | `Copernicus_DSM_COG_30_N37_00_W007_00_DEM` | 5,402,795 |

An initial probe used two-digit longitudes (`W14`, `W90`, `W07`); those objects genuinely
return HTTP 404. The actual bucket keys are zero-padded to three digits, as verified in
`tileList.txt` and by successful `HEAD`/read requests. The importer now builds keys from
the pattern after verifying them; it fails loudly rather than guessing.

### Access timestamp

Files were fetched on **2026-11-14**, between `06:11:06Z` and `06:11:16Z`. The manifest's
dataset `access_timestamp` is the latest of those reads (`2026-11-14T06:11:16Z`); each
crop sidecar in `data/cache/copernicus/*.json` records its own timestamp and remote ETag.
All five objects carry `Last-Modified: Mon, 09 May 2022`.

## Licensing and attribution

The AWS registry states (verbatim):

> GLO-30 Public and GLO-90 are available on a free basis for the general public under the
> terms and conditions of the Licence found [here](https://dataspace.copernicus.eu/explore-data/data-collections/copernicus-contributing-missions/collections-description/COP-DEM).

The Copernicus Data Space Ecosystem page for COP-DEM states (verbatim):

> The GLO-30 and GLO-90 datasets are available worldwide with a free license.

The licence document linked from that page is titled **“COPERNICUS CONTRIBUTING MISSION
DATA ACCESS LICENCE BETWEEN ESA AND COPERNICUS USER FOR THE USE OF DATA SUBJECT TO
RESTRICTIVE LICENSING TERMS”** (issue date 21/02/2025, PDF:
<https://dataspace.copernicus.eu/sites/default/files/media/files/2025-06/copernicus_contributing_mission_data_access_v2_cop_dem_licenses.pdf>).
The same page separately confirms free access to GLO-90 for the general public. The
manifest records the short form of this provenance in `license` / `license_url`; consult
the CDSE page for the authoritative terms.

Required attribution for adapted Copernicus DEM data, copied into the dataset notes:

> produced using Copernicus WorldDEM-90 © DLR e.V. 2010-2014 and © Airbus Defence and
> Space GmbH 2014-2018 provided under COPERNICUS by the European Union and ESA; all rights
> reserved

## Raster properties and processing

- Source CRS: `EPSG:4326` (WGS84-G1150), geographic; vertical datum EGM2008, metres.
- Native spacing: 3.0 arc-seconds (0.000833333…°). GLO-90 COGs have 1200 rows; the number
  of columns depends on latitude (1200 at 0–50°, 400 at 70–80°). The Devon Island tile is
  therefore `1200 x 400` with longitude spacing 0.0025°; all other selected tiles are
  `1200 x 1200` at 0.000833333°.
- Data type: `float32`; DEFLATE with floating-point predictor; internal TIFF blocks
  `2048 x 2048`; overviews at 2× and 4×. The COGs declare **no nodata value** (ocean areas
  have no tiles). The extractor records `source_nodata_undefined` as a warning; all five
  selected land windows were 100% valid.
- Read path: HTTP `HEAD` for existence/size/ETag, then GDAL `/vsicurl/` with
  `GDAL_DISABLE_READDIR_ON_OPEN=EMPTY_DIR`, `CPL_VSIL_CURL_ALLOWED_EXTENSIONS=.tif`,
  25 s timeout, and at most 2 retries handled in Python.
- Cropping: the importer computes the WGS84 bounds of a 1.98 km analysis window plus
  1,500 m of margin, intersects it with the COG bounds, and writes a 54–56 × 59–74 pixel
  `float32` GeoTIFF to `data/cache/copernicus/<slug>.tif`. Five crops total 59,431 bytes.
  Full tiles are never written to disk.
- Feature extraction: shared `extract_features(..., center_lonlat=(lon, lat))`. The window
  is reprojected to the local UTM zone (EPSG:32719, 32611, 32628, 32616, 32629 for the five
  candidates, north/south as appropriate), resampled bilinearly to 22 × 22 cells at 90 m,
  and reduced to the four registry metrics. Valid fraction must be ≥ 0.8 (all were 1.0).

### What was downloaded vs range-read

Only bounded COG range reads were issued; there was no global listing download and no bulk
tile download. `tileList.txt` was read once during verification, not by the importer. The
five S3 objects total **16,559,599 bytes** (0.36–5.40 MB each); this is the HEAD
`Content-Length` upper bound for the bytes covered by the reads. Because every GLO-90 COG
uses 2048 × 2048 internal blocks and these tiles are only 1200 rows tall, the block
containing a crop can span the whole compressed tile: the HTTP transfer for one window may
therefore be close to that tile's full size even though only a ~3 km window is decoded.
That is a property of the published COG layout, not a bulk download. The committed artifact
is the numeric manifest, not any raster.

## Measured results (2026-11-14 run)

All values come from the committed manifest; `valid_fraction` is the fraction of the
22 × 22 analysis grid with data after resampling.

| Candidate | slope_mean (deg) | slope_p90 (deg) | relief p95–p05 (m) | roughness std (m) | valid |
| --- | ---: | ---: | ---: | ---: | ---: |
| earth-atacama-desert | 0.012132 | 0.021067 | 0.113076 | 0.034069 | 1.0 |
| earth-death-valley | 17.803210 | 30.088898 | 378.412096 | 93.251142 | 1.0 |
| earth-lanzarote | 9.207354 | 21.902200 | 192.317387 | 40.851137 | 1.0 |
| earth-devon-island | 4.692384 | 8.258798 | 109.605672 | 18.821312 | 1.0 |
| earth-rio-tinto | 6.713842 | 12.825895 | 88.074492 | 22.197816 | 1.0 |

The Atacama centre sits on the Salar de Atacama, one of the flattest land surfaces on
Earth (crop elevation 2300.5–2302.4 m). Its near-zero metrics are real observations, not a
failure: the mandated coordinate is simply not rugged. It is retained because the brief
requested it and because it usefully calibrates how flat “flat” can be.

A second full `--force` run against the same S3 objects reproduced every value and window
checksum exactly (same ETags); only `generated_at` and access timestamps changed.

## Checksums

Each candidate's `notes` field contains
`window_checksum=sha256:<64 hex>`. The shared extractor computes it over the final 22 × 22
`float64` array (NaN mapped to −9999) concatenated with the source tile URL. It is a
reproducible fingerprint of the resampled analysis window, not of the raw tile. Dataset
`checksum` is `null` because a single value cannot represent five distinct windows.
Because the checksum depends on the source label, changing the label changes the hash even
when the elevation values are identical.

## Reprocessing and verification

- The manifest is regenerated atomically by the CLI. `generated_at` and dataset
  `access_timestamp` are refreshed only when a crop is re-read from S3; a cache-only run
  reuses the original per-crop access timestamps.
- `--force` deletes the local crop and metadata and re-reads from S3. To fully reset:
  `rm -rf data/cache/copernicus` (the directory is gitignored).
- Expected local checks:
  - `uv run pytest pipelines/earth/tests -q` → 53 passed (offline, no network).
  - `uv run ruff check pipelines/earth` → all checks passed.
  - JSON Schema + `Manifest.model_validate` → OK, 5 candidates, 1 dataset, `observed`.
- API integration, observed output:

```bash
ANALOG_DATABASE_URL=sqlite+pysqlite:////tmp/analog-earth.db \
  uv run python -m analog_api.cli seed
# Seeded 5 candidate(s) and 0 reference(s) from 1 manifest(s). data_version=dv-3e2f2c6e67ef
```

A `POST /api/v1/search` with target
`{slope_mean_deg: 10, slope_p90_deg: 20, terrain_relief_p95_p05_m: 150, roughness_std_m: 30}`
returned HTTP 200, `indexed_candidate_count=5`, `eligible_candidate_count=5`, and ranked
`earth-lanzarote` first (distance 0.300625), followed by `earth-rio-tinto` (0.382357).
This proves the manifest flows through seeding, persistence, scoring and the HTTP contract
without API-side changes.

## Known limitations

- **DSM, not DTM.** Copernicus DEM includes buildings, infrastructure and vegetation. All
  candidates carry `copernicus_dsm_may_include_vegetation_buildings`. Roughness and relief
  can therefore include canopy or structures, especially at Rio Tinto, Lanzarote and
  Death Valley.
- **Resolution.** Native 3 arc-second (~90 m at the equator) data resampled bilinearly to
  90 m. Slopes at 90 m cannot resolve cliffs, gullies, lava tubes or metre-scale features.
- **Window size.** 1.98 km × 1.98 km (22 × 22 cells). Results describe local shape only,
  not regional context, and are not a global survey.
- **Projection.** UTM scale distortion grows toward the zone edges and at high latitude;
  the Devon Island window is near 75.4°N where longitude spacing in the source is 0.0025°.
  All metrics are computed in metres, never in degrees.
- **Vertical datum.** EGM2008 geoid heights, not ellipsoidal heights. The four metrics are
  relative (slope, percentile relief, detrended residual), so a constant datum offset does
  not affect them.
- **Nodata.** GLO-90 COGs define no nodata value; the extractor emits
  `source_nodata_undefined`. Valid fraction is still computed from finite resampled cells.
  Ocean-only windows would have no tile at all and are rejected.
- **COG block layout.** As noted above, range-reading a small window can transfer a whole
  compressed tile block (~0.36–5.4 MB). This is bounded and per-window, but it is not
  byte-minimal.
- **Candidate selection.** Five hand-picked, geologically distinct windows, not a
  statistically representative sample. “Best match” means best among these indexed
  windows.

## Failure notes

The final ingestion run had **5/5 successful windows and 0 failures**, so the committed
manifest contains every attempted candidate and no failed window. Earlier verification
probes that returned HTTP 404 were due to two-digit longitude ids (`W14`, `W90`, `W07`);
the real keys use three digits (`W014`, `W090`, `W007`) and were confirmed in
`tileList.txt` and by `HEAD 200`. No network, CRS, or nodata failure occurred during the
final run, and no synthetic values were substituted at any point.
