# Terrain expansion source status

Current manifests: 113 Earth Copernicus GLO-90 windows, eleven ESA WorldCover 2021
v200 context fractions per Earth window, eighteen Moon LOLA windows across all six
requested sites, and five Mars Jezero CTX DEM windows. Native resolutions are 90 m,
10 m context, 5 m and 20 m respectively; terrain analysis stays 22×22 at 90 m.
The source links, access timestamps, original CRS WKT, licences, checksums and crop
coordinates are recorded in each manifest. [TERRAIN_EXPANSION.md](TERRAIN_EXPANSION.md)
describes the bounded sampling rules and the remaining Site07 ROI 2 failure.
Mars MOLA and THEMIS thermal inertia are metadata-only interfaces in
[MARS_PROVIDER_GATES.md](MARS_PROVIDER_GATES.md); they contribute no observation.

The following access evidence documents the original five-window / one-ROI baseline.

# Data sources and provenance

This document distinguishes **downloaded and processed observations** from **candidate/future
sources**. Only the first category is allowed into the search index as `observed` data.

## Earth — Copernicus DEM GLO-90 (IMPLEMENTED, OBSERVED)

| Field | Value |
|---|---|
| Provider | European Space Agency (ESA) / Airbus Defence and Space; public AWS Open Data mirror managed by Sinergise |
| Product ID | `COP-DEM_GLO-90-DGED` (Copernicus DEM 2021 release, GLO-90) |
| Registry | <https://registry.opendata.aws/copernicus-dem/> |
| Official product/terms | <https://dataspace.copernicus.eu/explore-data/data-collections/copernicus-contributing-missions/collections-description/COP-DEM> |
| Bucket | `s3://copernicus-dem-90m/` (region `eu-central-1`, unsigned public access) |
| Format | Cloud Optimized GeoTIFF, 2048×2048 internal blocks |
| Native CRS / grid | EPSG:4326, 3 arc-second (~90 m) geographic grid |
| Vertical | EGM2008 heights in metres |
| License | Free basis for the general public under the Copernicus DEM licence terms linked above |
| Citation | "Copernicus Digital Elevation Model (DEM) was accessed on 2026-11-14 from https://registry.opendata.aws/copernicus-dem." Attribution: produced using Copernicus WorldDEM-90 © DLR e.V. 2010–2014 and © Airbus Defence and Space GmbH 2014–2018, provided under COPERNICUS by the European Union and ESA; all rights reserved. |
| Limitations | Digital **surface** model: buildings, infrastructure and vegetation are included. Ocean areas have no tiles. No nodata value is declared in the source metadata (`source_nodata_undefined` warning), so interior voids cannot be distinguished from valid zero/negative heights by metadata alone. |

### Access method

The importer (`pipelines/earth/ingest_copernicus.py`) resolves the tile from the centre coordinate,
constructs the public HTTPS COG key, opens it with GDAL/rasterio, and reads **only a small window**
around the ROI. No full tile is downloaded. The effective range-read granularity is the 2048×2048
COG block, which is why a tiny window still transfers a bounded block. Raw blocks/crops are cached
under `data/cache/` (gitignored); only the derived numeric manifest is committed.

Run:

```bash
# inspect intended tile URLs without downloading
uv run python pipelines/earth/ingest_copernicus.py --dry-run

# re-run all five windows, bounded retries, cache under data/cache
uv run python pipelines/earth/ingest_copernicus.py

# one window
uv run python pipelines/earth/ingest_copernicus.py --only earth-death-valley

# validate + seed + search
uv run python -m analog_api.cli seed
```

Committed manifest: `pipelines/earth/manifests/copernicus_glo90_earth.json`
(generated 2026-11-14T06:05:06Z, `data_kind=observed`).

### Indexed windows (actual measurements)

All windows use the shared extractor `terrain-metrics-1.0.0`: 1.98 km × 1.98 km analysis window,
22 × 22 cells at 90 m, bilinear resampling, ≥80% valid cells, Horn 3×3 slope, p95−p05 relief,
plane-detrended roughness. Each window is a 2 km sample, **not** a characterization of the entire
named region.

| Candidate ID | Display name | Centre (WGS84) | Source tile | CRS used | Valid | Mean slope° | p90 slope° | Relief m | Roughness m |
|---|---|---|---|---|---|---|---|---|---|
| `earth-atacama-desert` | Atacama Desert, Chile | −23.500, −68.400 | `Copernicus_DSM_COG_30_S24_00_W069_00_DEM` | EPSG:32719 | 1.00 | 0.012132 | 0.021067 | 0.113076 | 0.034069 |
| `earth-death-valley` | Death Valley, California, USA | 36.250, −117.100 | `Copernicus_DSM_COG_30_N36_00_W118_00_DEM` | EPSG:32611 | 1.00 | 17.803210 | 30.088898 | 378.412096 | 93.251142 |
| `earth-lanzarote` | Lanzarote volcanic terrain, Spain | 29.040, −13.630 | `Copernicus_DSM_COG_30_N29_00_W014_00_DEM` | EPSG:32628 | 1.00 | 9.207354 | 21.902200 | 192.317387 | 40.851137 |
| `earth-devon-island` | Devon Island / Haughton, Nunavut, Canada | 75.400, −89.850 | `Copernicus_DSM_COG_30_N75_00_W090_00_DEM` | EPSG:32616 | 1.00 | 4.692384 | 8.258798 | 109.605672 | 18.821312 |
| `earth-rio-tinto` | Rio Tinto, Spain | 37.690, −6.550 | `Copernicus_DSM_COG_30_N37_00_W007_00_DEM` | EPSG:32629 | 1.00 | 6.713842 | 12.825895 | 88.074492 | 22.197816 |

Per-window bbox, crop checksum (`window_checksum=sha256:...`), quality flags, and warnings are stored
in the manifest and surfaced by the API. The Atacama window is an extremely flat salt-flat surface;
it is retained as a real observation, not as a claim about the whole desert.

### Source-to-metric lineage

1. Native 3 arc-second DSM in EPSG:4326 (EGM2008 metres).
2. Reproject/read a 1.98 km window into the local UTM CRS at 90 m (bilinear).
3. Reject if finite cells < 80%.
4. `slope_mean_deg`, `slope_p90_deg` from Horn 3×3 gradients in metres/metre.
5. `terrain_relief_p95_p05_m` = p95 − p5 elevation.
6. `roughness_std_m` = population std of residuals from a least-squares plane.
7. Validate ids/units/method version, persist with dataset provenance and a deterministic
   `data_version` hash.

### Reproducibility status

- [x] Real COG windows read over HTTPS with bounded access.
- [x] Features validated against the shared registry and manifest schema.
- [x] API seed and search verified against the real manifest.
- [ ] Independent re-run on a second network/day (not required for MVP; values may change if
  upstream tiles are revised).
- [ ] Vertical accuracy validation against ground control (out of scope).

## Earth context — ESA WorldCover 2021 v200 (IMPLEMENTED, OBSERVED CONTEXT)

| Field | Value |
|---|---|
| Provider | European Space Agency (ESA) WorldCover / VITO |
| Product | ESA WorldCover 10 m 2021 v200 (11 classes) |
| Official page | <https://esa-worldcover.org/en/data-access> |
| DOI | <https://doi.org/10.5281/zenodo.7254221> |
| Bucket | `s3://esa-worldcover` (`eu-central-1`), prefix `v200/2021/map/`, unsigned HTTPS |
| Format / CRS | 3°×3° Cloud Optimized GeoTIFF, EPSG:4326, ~10 m, nodata = 0 |
| License | CC BY 4.0; attribution: "© ESA WorldCover project 2021 / Contains modified Copernicus Sentinel data (2021) processed by ESA WorldCover consortium" |
| Role | **Earth land-cover context only.** Never a similarity dimension, never mineralogy/geology. |
| Verification | 2021 v200 overall accuracy 76.7% (official validation report); static annual epoch; 10 m classes are not a mineral map |

The pipeline reads only each AOI window (3° tiles are tens to ~115 MB; raw tiles are never
committed). All five indexed Earth windows have 11 context fractions; values are visible in
`GET /locations/{id}` and `GET /areas/{id}/features`.

| Candidate | Tile | Bare/sparse (60) | Built-up (50) | Valid |
|---|---|---|---:|---:|---:|
| `earth-atacama-desert` | S24W069 | 0.9949 | 0.0051 | 1.0 |
| `earth-death-valley` | N36W120 | 0.0267 | 0.0003 | 1.0 |
| `earth-lanzarote` | N27W015 | 0.2609 | 0.0869 | 1.0 |
| `earth-devon-island` | N75W090 | 0.5096 | 0.0000 | 1.0 |
| `earth-rio-tinto` | N36W009 | 0.1768 | 0.2216 | 1.0 |

Manifest: `pipelines/worldcover/manifests/worldcover_earth_context.json` (35 KB, derived values only).
Methods: `worldcover-2021-v200-1.0.0`; fractions use valid (non-nodata) pixels; classes sum to ≈1.
Known limitations: 2021 epoch may not represent current land cover; built-up/water classes are
context, not habitability or resources; classification error exists (76.7% overall accuracy).

## Moon — NASA LOLA south-pole reference (IMPLEMENTED, OBSERVED)

| Field | Value |
|---|---|
| Provider / product | NASA GSFC Planetary Geodesy Data Archive (PGDA), LRO-LOLA product 78: `Site04_final_adj_5mpp_surf` |
| Product page | <https://pgda.gsfc.nasa.gov/products/78> (Site04 surface DEM, 5 m/pix) |
| File | <https://pgda.gsfc.nasa.gov/data/LOLA_5mpp/Site04/Site04_final_adj_5mpp_surf.tif> |
| Format | 3200×3200 px, float32, 5.0 m/pix, nodata=NaN, striped (1×3200 blocks), no overviews |
| Frame | LOLA/DE421 south polar stereographic, X/Y in metres; not Earth WGS84 |
| ROI | `moon-shackleton-rim-site04-roi2`: official `Site04_ROIs.shp` record 2, X=−2757.5 m, Y=−6342.5 m |
| License | NASA-led mission data; PGDA page requests citation and carries no restrictive notice (recorded as CC0-1.0 with NASA Science Data Portal link) |
| Citation | Barker, M.K., et al. (2021), *Improved LOLA Elevation Maps for South Pole Landing Sites: Error Estimates and Their Impact on Illumination Conditions*, Planetary & Space Science 203, 105119, <https://doi.org/10.1016/j.pss.2020.105119> |
| Limitations | About **90% of the 5 m pixels are interpolated** (only ~10% direct LOLA returns); median RMS Z error ~0.30–0.50 m and slope error ~1.5–2.5°; source is striped/non-COG; the window is resampled to 90 m, so 5 m texture is not independent evidence. |

Actual measured reference (shared `terrain-metrics-1.0.0`, 1.98 km window at 90 m, valid=1.0):

| `slope_mean_deg` | `slope_p90_deg` | `terrain_relief_p95_p05_m` | `roughness_std_m` |
|---:|---:|---:|---:|
| 17.8959 | 28.7771 | 390.8059 m | 102.8055 m |

Manifest: `pipelines/planetary/manifests/lola_moon_reference.json`. Import method: bounded read of a
414×414 px crop (1980 m) at 5 m, bilinear reprojection to the 22×22 / 90 m analysis grid, identical
extractor. Only this real ROI is exposed as a Moon reference; no other lunar coverage is claimed.

## Mars — PDS4 MOLA MEGDR and THEMIS thermal inertia (metadata only)

MOLA source: <https://pds-geosciences.wustl.edu/missions/mgs/megdr.html> (MGS MOLA gridded
topography, PDS4 maintained archive). The official PDS4 bundle/product labels were inspected; the
native support is 3.704 km/pixel at 16 ppd and 0.463 km/pixel at 128 ppd, far coarser than the
90 m terrain method, with interpolated no-observation bins. No MOLA raster is ingested and no Mars
MOLA value is claimed.

Mars thermal source: <https://astrogeology.usgs.gov/search/map/mars-themis-derived-global-thermal-inertia-mosaic>
(USGS Astrogeology THEMIS-derived quantitative thermal inertia, 100 m/pixel, 24 tiles, 60°S–60°N).
It is public domain, requires citation, and is `evidence_only`. Issue #43 adds a bounded pilot
(`pipelines/pilots/d03_planetary_thermal/`) that reads small 128 x 128 ISIS tiles for the five
Jezero windows and persists five `thermal_inertia` observations at native 100 m. There is still
no per-pixel QA/uncertainty layer and no similarity feature.

Implementation: `analog_pipelines.acquisition.providers_mars` and
`mars_comparability`. Both providers fail closed; see
[`docs/MARS_PROVIDER_GATES.md`](MARS_PROVIDER_GATES.md). MOLA does not add a Mars reference or
`supported_bodies` entry until a validated manifest exists.

## Earth — ISRIC SoilGrids 2.0, 250 m (IMPLEMENTED, MODEL PREDICTION)

| Field | Value |
|---|---|
| Provider | ISRIC — World Soil Information |
| Product | SoilGrids 2.0, 250 m global soil property predictions |
| Service | Public OGC WCS 2.0.1 at `https://maps.isric.org/mapserv` |
| Access | Public HTTPS, no credentials; bounded AOI subsets only |
| Properties | clay, sand, silt, SOC, bulk density of the fine earth fraction |
| Depths | 0–5, 5–15, 15–30, 30–60 cm |
| Statistics | mean, Q0.05, Q0.95 |
| Licence | CC BY 4.0; cite ISRIC SoilGrids 2.0 |
| Evidence | **Model prediction**, not a local measurement and not `observed` |
| Pilot | 5 AOIs × 5 properties × 4 depths × 3 statistics = 300 source observations, plus 3 AOIs × 5 properties × 0–5 cm × 3 statistics = 45 expansion observations (source `evidence_only`; gated 0–5 cm means may enter query similarity) |

Raw TIFF responses are not committed; per-request SHA-256 checksums, HTTP status, exact endpoint
and parameters are recorded in `pipelines/pilots/d06_earth_soilgrids_depths/requests.jsonl`.
Model uncertainty is represented by separate Q0.05/Q0.95 prediction-quantile records and is not a
confidence interval for the AOI mean. See [`D06_SOILGRIDS_DEPTH_PILOT.md`](D06_SOILGRIDS_DEPTH_PILOT.md)
and [`D06_REGOLITH_SEMANTICS.md`](D06_REGOLITH_SEMANTICS.md). No Moon or Mars regolith measurement
is registered; those entries remain `no_coverage`.

## Challenge source

<https://www.spaceappschallenge.org/2026/challenges/permanent-moon-base-locations-and-mars/>

## Licensing and redistribution notes

- Derived numeric values from Copernicus DEM are committed with attribution and the verified license
  link. Raw DEM tiles and cached blocks are **not** committed.
- Synthetic fixtures (`apps/api/src/analog_api/data/synthetic.py`) are repository-authored test data
  under CC0-1.0 and are never presented as observations.
- Basemap tiles are © OpenStreetMap contributors, served by the public OSM tile service; the UI
  keeps attribution and degrades to a list if tiles fail.
