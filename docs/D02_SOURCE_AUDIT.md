# D02 Geology & Mineralogy — Open-Data Source Audit

- **Dimension:** D02 Geology & Mineralogy (`lithology`, `mineral_spectral_detection`, `geological_process`)
- **Audit date:** 2026-11-15
- **Branch:** `audit/b-source-audit` (base `df6e365`)
- **Machine-readable catalogue:** `docs/data-sources/d02-geology.json`
- **Re-runnable checker:** `scripts/check_d02_sources.py`
- **Scope:** official/public read-only APIs and metadata endpoints for Earth, Moon and Mars.
- **Non-claims:** this is **not** a global survey, **not** a similarity score, **not** a mineral
  abundance product, and **not** a replacement for the bounded D02 observations already in the
  repository. No value was zero-filled or invented.

## 1. What exists in the repository today

The pinned D02 export contains **56 typed-v2 observations across 8 bounded AOIs** (Earth 5,
Moon 1, Mars 2). All D02 records keep `score_role: "evidence_only"`. There is no D02 weighted
similarity or hard-filter role at this commit.

| Body | AOIs | Typed D02 | `geologic_unit` | `geologic_process` | `mineral_*` | `context_*` | `geology_analysis_availability` |
|---|---:|---:|---:|---:|---:|---:|---:|
| Earth | 5 | 15 | 0 | 0 | 0 | 0 | 15 |
| Moon | 1 | 3 | 1 | 1 | 0 | 0 | 1 |
| Mars | 2 | 38 | 10 | 4 | 10 (all `unknown`) | 14 | 0 |
| **Total** | **8** | **56** | **11** | **5** | **10 unknown / 0 detected** | **14** | **16** |

Details that matter for source planning:

- **Earth has no shareable map-unit value.** Five GLiM mirror overlays completed locally, but the
  mirror publishes no licence, so the values are withheld (`redistribution_not_verified`).
  The other 10 Earth records are EMIT availability records: 4 `unknown` and 1 `no_coverage` for
  each of V002 and V001. They are not mineral detections.
- **Mars mineral records are all `unknown`.** Five minerals × two test AOIs = 10 records;
  detected = 0, numeric = 0, abundance = 0. CRISM contributes 14 dimensionless context values
  (7 parameters × 2 AOIs), not detections.
- **Moon has one map unit and one process record**, both from `Ec` at one Shackleton ROI.
- Map units are bounded to one lunar ROI and two explicitly marked Jezero test AOIs. They are not
  a Mars reference and not a global lunar map.
- Pinned snapshot: `geo-snap-d1235d5f9defe6cd`. Typed observations SHA-256:
  `9f8dd0ca82b0ba4b0cb0972643fbdf8eb3c3726db3855a34c76d16e482344eef`.

### Eligibility code note

At this worktree's base commit `df6e365`, `apps/api/src/analog_api/domain/eligibility.py` does not
exist. The #116 implementation in the sibling worktree was reviewed for intent: it registers no D02
similarity or hard-filter role and falls back to evidence-only for `geologic_unit`,
`geologic_process`, every `mineral_*`, every `context_*` and `geology_analysis_availability`.
This audit does not modify API code; it records the source conditions that would have to change
before such a role could be legal.

## 2. How the audit was performed

- **stdlib-only HTTP client** (`urllib`) with a stable user agent; `GET` bodies capped at
  300,000 bytes per entry; archives and PDFs checked with `HEAD` only.
- **14 sources and 2 licence references** were checked with **34 requests/assertions**. The full
  request list, expected status/content type/body strings and observed values are in
  `docs/data-sources/d02-geology.json`.
- The checker downloaded no raster, granule, large archive or credential. One small
  **38,670-byte** GLiM 0.5° zip was fetched separately for format inspection (720 × 360, 16 codes,
  nodata `-9999`); it is not committed and not used as an observation.
- PDS Search API redirects `https://pds.nasa.gov/api/search/1/...` to
  `https://pds.mcp.nasa.gov/api/search/1/...` (HTTP 307 followed). Its query syntax is
  `field eq "value"`; a bare free-text query returns HTTP 400.
- The old `https://earth.jpl.nasa.gov/emit/data/data-products/` URL returns **HTTP 404**; the
  Earthdata catalogue and CMR endpoints are used instead. This is exactly the kind of stale URL
  the checker is meant to catch.
- The USGS Astrogeology STAC API is live but contains **18 imagery/terrain collections and no
  geologic-map collection**. It is recorded as a negative finding, not a D02 source.
- `https://www.usgs.gov/` general pages return **HTTP 403** to clients without a browser-like user
  agent; the checker sends one. Product data is served from `pubs.usgs.gov`, Astropedia and S3,
  which are reachable directly.

## 3. Reachable sources

Status meanings: `verified` = product/metadata plus an existing bounded repository use;
`accessible` = official metadata/licence/access confirmed but no repository observation yet;
`identified` = product reachable but licence/equivalence unverified; `not_applicable` = reachable
but not a D02 product.

| Body | Source | Status | What it can produce | Licence | Coverage / native support | CRS / frame |
|---|---|---|---|---|---|---|
| cross-body | `pds-registry-search` | accessible | PDS4 product discovery only | PDS public domain | PDS4 catalogue | n/a |
| Moon | `usgs-moon-unified-geologic-map-v2` | verified | map-unit category, process evidence (crosswalk needed) | CC0, cite authors | global 1:5,000,000 vector | Moon 2000 sphere, Mercator, planetocentric, R=1,737,400 m |
| Mars | `usgs-mars-sim3292-global-geologic-map` | accessible | map-unit category, process evidence (crosswalk needed) | US public domain, cite authors | global 1:20,000,000 vector, 808,910,442 B zip (not ingested) | Mars 2000 sphere, Robinson, R=3,396,190 m |
| Mars | `usgs-mars-sim3464-jezero-geologic-map` | verified | map-unit category, process evidence (crosswalk needed) | US public domain, cite authors | 1° × 1° Jezero/Nili, 1:75,000 vector | Mars 2000 sphere, MTM 77.5E, R=3,396,190 m |
| Mars | `pds-crism-mtrdr` | verified | mineral detection candidate; spectral index context | PDS public domain | targeted products; example 36 m/px | Mars equirectangular, native 36 m |
| Mars | `pds-omega-global-mineral-maps` | accessible | olivine/pyroxene/hydrous mineral candidates; spectral index | PDS public domain | global, 40 px/degree ≈ 1.48 km/px | simple cylindrical, R=3,396 km, planetocentric |
| Moon | `pds-m3-l2-reflectance` | accessible | mineral detection candidate | PDS public domain | targeted swaths; example 304 × 1,182 × 85 bands | no embedded ENVI CRS; Mean Earth documented |
| Earth | `nasa-emit-l2b-mineral` | accessible | mineral identification + band depth + uncertainty candidate | NASA open data, DOI | arid dust regions, 60 m; granule access `auth_required` | per-pixel swath, non-orthocorrected |
| Earth | `pangaea-glim-0p5-grid` | accessible | 16-class lithology category (crosswalk needed) | CC BY 3.0 | global land, 0.5° ≈ 55 km, 38,670 B zip | WGS84 geographic |
| Earth | `arcgis-glim-vector-mirror` | identified | lithology polygons (crosswalk + licence needed) | **unknown** | global vector, 1:3,750,000 | EPSG:3857 |
| Earth | `gsc-of5529-world-geology` | verified | lithology/process category (crosswalk needed) | OGL-Canada 2.0 | global generalized geology, 1:35,000,000 | EPSG:4326 |
| Earth | `macrostrat-api-map-units` | accessible | point map unit + lithology (crosswalk needed) | CC BY 4.0 | partial global; source scales vary | WGS84 point query |
| cross-body | `usgs-astrogeology-stac` | not_applicable | none (imagery/terrain only) | US public domain | 18 collections, no geology | varies |
| Mars | `pds-tes-special-products` | not_applicable | none (thermal inertia/albedo only) | PDS public domain | global thermal, 8 px/degree special products | Mars simple cylindrical |

### Exact HTTP evidence

All rows below were re-run by `scripts/check_d02_sources.py` on 2026-11-15. `Bytes` is the declared
`Content-Length`; a dash means the server did not declare it. `GET` bodies were capped; the checker
did not download an archive.

| Source | Evidence | HTTP | Content-Type | Bytes | URL |
|---|---|---|---|---:|---|
| `pds-registry-search` | PDS search CRISM bundle query | GET 200 | `application/json` | — | <https://pds.mcp.nasa.gov/api/search/1/products?q=title%20eq%20%22CRISM%20MTRDR%22&limit=1> |
| `usgs-moon-unified-geologic-map-v2` | Astropedia landing page | GET 200 | `text/html; charset=utf-8` | — | <https://astrogeology.usgs.gov/search/map/Moon/Geology/Unified_Geologic_Map_of_the_Moon_GIS_v2/> |
| `usgs-moon-unified-geologic-map-v2` | product zip HEAD | HEAD 200 | `application/zip` | 224,413,040 | <https://asc-astropedia.s3.us-west-2.amazonaws.com/Moon/Geology/Unified_Geologic_Map_of_the_Moon_GIS_v2.zip> |
| `usgs-mars-sim3292-global-geologic-map` | USGS publication page | GET 200 | `text/html` | — | <https://pubs.usgs.gov/sim/3292/> |
| `usgs-mars-sim3292-global-geologic-map` | FGDC metadata | GET 200 | `text/plain` | — | <https://pubs.usgs.gov/sim/3292/downloads/sim3292_metadata.txt> |
| `usgs-mars-sim3292-global-geologic-map` | database zip HEAD | HEAD 200 | `application/zip` | 808,910,442 | <https://pubs.usgs.gov/sim/3292/downloads/sim3292_database.zip> |
| `usgs-mars-sim3464-jezero-geologic-map` | USGS publication index | GET 200 | `text/html;charset=ISO-8859-1` | — | <https://pubs.usgs.gov/sim/3464/> |
| `usgs-mars-sim3464-jezero-geologic-map` | FGDC metadata | GET 200 | `text/plain` | — | <https://pubs.usgs.gov/sim/3464/sim3464_metadata.txt> |
| `usgs-mars-sim3464-jezero-geologic-map` | database zip HEAD | HEAD 200 | `application/zip` | 349,336,873 | <https://pubs.usgs.gov/sim/3464/sim3464_database.zip> |
| `pds-crism-mtrdr` | ODE product query | GET 200 | `application/json; charset=utf-8` | — | <https://oderest.rsl.wustl.edu/live2/?query=product&pdsid=HRL000040FF_07_IF183J_MTR3&results=fp&output=JSON> |
| `pds-crism-mtrdr` | MTRDR PDS3 label | GET 200 | `text/plain` | 6,179 | <https://pds-geosciences.wustl.edu/mro/mro-m-crism-5-rdr-mptargeted-v1/mrocr_4001/mtrdr/2007/2007_029/hrl000040ff/hrl000040ff_07_if183j_mtr3.lbl> |
| `pds-omega-global-mineral-maps` | olivine map label | GET 200 | `text/plain` | 4,679 | <https://pds-geosciences.wustl.edu/mex/mex-m-omega-5-ddr-global-maps-v1/mexomg_2000/data/olivine/olivine_osp1_equ_map.lbl> |
| `pds-omega-global-mineral-maps` | pyroxene map label | GET 200 | `text/plain` | 4,576 | <https://pds-geosciences.wustl.edu/mex/mex-m-omega-5-ddr-global-maps-v1/mexomg_2000/data/pyroxene/pyroxene_bd2000_equ_map.lbl> |
| `pds-omega-global-mineral-maps` | hydrous sites label | GET 200 | `text/plain` | 5,378 | <https://pds-geosciences.wustl.edu/mex/mex-m-omega-5-ddr-global-maps-v1/mexomg_2000/data/hydrous/hydrous_sites_geo_map.lbl> |
| `pds-omega-global-mineral-maps` | volume readme | GET 200 | `text/plain` | 11,508 | <https://pds-geosciences.wustl.edu/mex/mex-m-omega-5-ddr-global-maps-v1/mexomg_2000/aareadme.txt> |
| `pds-m3-l2-reflectance` | M3 volume page | GET 200 | `text/html; charset=UTF-8` | 6,960 | <https://pds-imaging.jpl.nasa.gov/volumes/m3.html> |
| `pds-m3-l2-reflectance` | L2 PDS label | GET 200 | not declared | 7,315 | <https://pds-imaging.jpl.nasa.gov/data/m3/CH1M3_0004/DATA/20081118_20090214/200811/L2/M3G20081118T222604_V01_L2.LBL> |
| `pds-m3-l2-reflectance` | L2 ENVI header | GET 200 | not declared | 37,989 | <https://pds-imaging.jpl.nasa.gov/data/m3/CH1M3_0004/DATA/20081118_20090214/200811/L2/M3G20081118T222604_V01_RFL.HDR> |
| `nasa-emit-l2b-mineral` | CMR collection 002 | GET 200 | `application/json;charset=utf-8` | 9,728 | <https://cmr.earthdata.nasa.gov/search/collections.json?short_name=EMITL2BMIN&version=002&page_size=1> |
| `nasa-emit-l2b-mineral` | Earthdata catalogue page | GET 200 | `text/html; charset=UTF-8` | 237,696 | <https://www.earthdata.nasa.gov/data/catalog/lpcloud-emitl2bmin-002> |
| `pangaea-glim-0p5-grid` | PANGAEA DOI landing page | GET 200 | `text/html;charset=utf-8` | 27,662 | <https://doi.pangaea.de/10.1594/PANGAEA.788537> |
| `pangaea-glim-0p5-grid` | PANGAEA JSON-LD metadata | GET 200 | `application/ld+json; charset=UTF-8` | 5,513 | <https://doi.pangaea.de/10.1594/PANGAEA.788537?format=metadata_jsonld> |
| `pangaea-glim-0p5-grid` | grid zip HEAD | HEAD 200 | `application/zip` | 38,670 | <https://epic.awi.de/31092/1/hartmann-moosdorf_2012.zip> |
| `arcgis-glim-vector-mirror` | ArcGIS layer metadata | GET 200 | `application/json; charset=utf-8` | 30,011 | <https://services8.arcgis.com/4KhTMTZ1x0f76DSg/arcgis/rest/services/GLiM_Niveau_I/FeatureServer/1?f=pjson> |
| `gsc-of5529-world-geology` | WMS GetCapabilities | GET 200 | `text/xml; charset=UTF-8` | — | <https://mrdata.usgs.gov/services/gscworld?service=WMS&request=GetCapabilities&version=1.3.0> |
| `gsc-of5529-world-geology` | OGL Canada licence | GET 200 | `text/html; charset=utf-8` | 23,088 | <https://open.canada.ca/en/open-government-licence-canada> |
| `gsc-of5529-world-geology` | NRCan DOI landing | GET 200 | `text/html; charset=utf-8` | 119,426 | <https://doi.org/10.4095/223767> |
| `macrostrat-api-map-units` | API root | GET 200 | `application/json; charset=utf-8` | 1,687 | <https://macrostrat.org/api/v2> |
| `macrostrat-api-map-units` | Death Valley point query | GET 200 | `application/json; charset=utf-8` | 4,654 | <https://macrostrat.org/api/v2/geologic_units/map?lat=36.25&lng=-117.11> |
| `usgs-astrogeology-stac` | collections | GET 200 | `application/json; charset=utf-8` | 64,658 | <https://stac.astrogeology.usgs.gov/api/collections> |
| `pds-tes-special-products` | TES thermal-inertia readme | GET 200 | `text/plain` | 10,194 | <https://pds-geosciences.wustl.edu/mgs/mgs-m-tes-5-timap-v1/mgst_9001/aareadme.txt> |
| `pds-tes-special-products` | TES special-products listing | GET 200 | `text/html; charset=UTF-8` | 939 | <https://pds-geosciences.wustl.edu/mgs/mgs-m-tes-special-v1/> |
| `usgs-public-domain` | USGS copyright page | GET 200 | `text/html; charset=utf-8` | — | <https://www.usgs.gov/information-policies-and-instructions/copyrights-and-credits> |
| `pds-public-domain` | PDS Geosciences data-use document HEAD | HEAD 200 | `application/pdf` | 1,181,900 | <https://pds-geosciences.wustl.edu/documents/ViewingPDSImagesAndExportingToOtherFormats.pdf> |

## 4. Source notes and what each can actually support

### Moon

**USGS Unified Geologic Map of the Moon v2 (1:5M).** The Astropedia page is reachable and states
`Use Constraints CC0, but please cite authors`. The page gives the frame explicitly: Moon 2000
sphere, radius 1,737,400 m, Mercator, planetocentric latitude, east-positive longitude, map scale
1:5,000,000. The 224,413,040-byte GIS zip is reachable by `HEAD` but was not downloaded here. The
existing repository run has **one** map unit (`Ec`) and **one** process record for one Shackleton
ROI; one invalid source geometry was excluded. The 1:5M map cannot resolve local lithology or
composition, and fine-scale frame alignment is unverified. This source can support a *category*
filter only after a versioned DOMU crosswalk.

**M3 L2 reflectance.** The PDS Imaging volume page and an L2 label/ENVI header are reachable. The
example product is 304 × 1,182 × 85 bands with fill `-999.0`; the ENVI header has no CRS. No M3
granule was downloaded, and per-pixel registration, thermal correction and band alignment are
unverified. The existing `m3_registration` and `m3_thresholds` blockers remain. Evidence only.

### Mars

**USGS SIM 3292, Geologic Map of Mars (1:20M).** This is the most important newly confirmed D02
source: an official, global, public-domain vector geologic map. FGDC metadata states
`Access_Constraints: None`, `Use_Constraints: please cite authors`, Robinson projection, Mars 2000
sphere (R=3,396,190 m), global 90S–90N / 180W–180E, with `Unit` and `UnitDesc` fields. The GIS zip
is **808,910,442 bytes** and was not downloaded. Global product coverage is not local accuracy:
minimum mapped outcrop is 40 km × 100 km at 1:20M, and no SIM 3292 crosswalk exists. This is a
gated expansion candidate, not an existing observation.

**USGS SIM 3464, Jezero/Nili Planum (1:75,000).** Already used for two explicitly marked test
AOIs. The 1° × 1° product covers 76.8–77.8E / 17.7–18.7N, Mars 2000 sphere, MTM 77.5E. Six raw
codes (`cr`, `NHjf2`, `Aeb`, `Nue`, `Nle`, `Njf`) are mapped for these AOIs; two invalid source
geometries were excluded. The 349,336,873-byte zip is reachable by `HEAD`. This is not a Mars
reference and not global.

**CRISM MTRDR.** ODE returns the MTRDR product metadata for `hrl000040ff_07_if183j_mtr3`; the PDS3
label states `MRO-M-CRISM-5-RDR-MPTARGETED-V1.0`, equirectangular, `MAP_SCALE = 0.0360000` km
(36 m). The existing run reads the refined summary and DP information for two test AOIs and
exports 14 dimensionless context values. **No mineral detection is enabled**: there is no validated
threshold, DP-mask bit mapping, continuum removal or uncertainty propagation. The ODE query is
bounded, not exhaustive. Ten mineral records remain `unknown`.

**OMEGA global maps.** The PDS Geosciences volume `MEX-M-OMEGA-5-DDR-GLOBAL-MAPS-V1.0` is a real
global D02 product: olivine OSP1, pyroxene BD2000, hydrous sites, ferric oxide and albedo, at
40 pixels/degree (about 1.48 km/pixel), simple cylindrical, R=3,396 km, planetocentric, 2004–2010.
The labels are explicit that each cell stores the **highest** spectral-parameter value in a
0.025° × 0.025° area; OSP1/BD2000 mix composition, grain size and abundance. The hydrous product is
a 1,855-row CSV of site coordinates and observation IDs with **no mineral species column**. No
OMEGA product is ingested. This is a strong candidate for a bounded window read, but not for
abundance or a detection state yet.

**TES.** The TES archive listing contains global albedo and thermal-inertia products; the
thermal-inertia readme describes thermal inertia. No mineral map was found. TES is a D03 thermal
source, not a D02 mineral source. Relabelling thermal inertia as mineralogy would be wrong.

### Earth

**EMIT L2B MIN V002.** CMR returns `C4079846859-LPCLOUD`, version 002, processing level 2B. The
Earthdata catalogue page describes estimated mineral identification and band depth at **60 m**,
with a mineral ID (int16, 0–294), band depth and uncertainty. The product is spatially raw and
non-orthocorrected. Granule assets require Earthdata Login; the repository has CMR candidates only
and no granule-validated decoder, QA mask or native-pixel AOI intersection. There is no open 60 m
alternative for Earth mineral identification in this audit; GLiM/GSC/Macrostrat are lithology, not
mineral detection. `emit_auth`, `emit_decoder` and `emit_native_swath` remain blockers.

**GLiM at PANGAEA (0.5°).** The PANGAEA DOI page and JSON-LD metadata confirm
**CC BY 3.0**, a 720 × 360 grid, 0.5° cells, and a 38,670-byte zip. The grid has 16 first-level
codes (`su`, `vb`, `ss`, `pb`, `sm`, `sc`, `va`, `mt`, `pa`, `vi`, `wb`, `py`, `pi`, `ev`, `nd`,
`ig`) and nodata `-9999`. A 0.5° cell is about 55 km at the equator, far coarser than a 1.98 km AOI.
It can support a *coarse* category context only, not a local measurement.

**GLiM vector mirror.** The third-party ArcGIS layer is reachable and returns polygon geometry,
`copyrightText` and a 1:3,750,000 map scale, but **no `licenseInfo` field**. The existing five
overslays are therefore withheld. The official vector distribution is separate; the open
alternative in this audit is the PANGAEA 0.5° CC BY 3.0 grid, which is much coarser. Do not publish
mirror geometry or per-unit values.

**GSC Open File 5529.** The USGS MRData WMS returns `GSC_WorldGeology` with a `geology` layer; the
OGL-Canada 2.0 page is reachable. The separate polygon pilot already processes two Earth AOIs at
1:35,000,000. Only four broad `rxtyp` values are mapped (`INTRUSIVE`, `SEDIMENT`, `SEDVOL`,
`VOLCANIC`); no code maps to aqueous alteration. This is a coarse category source, not a local
polygon measurement.

**Macrostrat API v2.** The API root states CC-BY 4.0 and the Death Valley point query returns map
units with `lith`, `source_id` and unit names. Atacama and Lanzarote also return units; an Antarctic
point returns an empty data array, confirming partial coverage. This is a useful point-map-unit
source, but source maps have different scales and attributions, and a point hit is not an AOI
fraction. No Macrostrat observation is ingested.

### Cross-body / negative findings

**PDS Registry Search API.** Reachable and useful for discovering PDS4 bundles (the CRISM MTRDR
bundle query returns `urn:nasa:pds:mro_crism_mtrdr::1.0`). It is not a geology catalogue and does
not establish local coverage.

**USGS Astrogeology STAC.** Reachable, 18 collections, all imagery/terrain (LOLA, Kaguya TC,
HiRISE, CTX, THEMIS, etc.). No geologic-map collection. This is a negative finding: STAC is not a
shortcut for D02 map units.

## 5. Crosswalk / taxonomy gap

Map units are **dataset-scoped codes or free text**. A crosswalk is not a label normalisation
exercise; it is a versioned scientific mapping with an official citation per code. The same text
code on two bodies or two maps must never be treated as equivalent.

| Dataset | Raw code system | Mapped now | Scope | Gap | What would unlock a category filter |
|---|---|---|---|---|---|
| `usgs-moon-geology-v2` | DOMU / `FIRST_Un_2` | `Ec` | one Shackleton ROI | Full lunar legend not crosswalked; one code is not a taxonomy | Versioned DOMU crosswalk citing the official CSV/description; keep unmapped codes as `unmapped_code` |
| `usgs-jezero-sim3464` | SIM3464 GeoUnits_v1 symbol | `cr`, `NHjf2`, `Aeb`, `Nue`, `Nle`, `Njf` | two Jezero test AOIs | Dataset-scoped; no other region and no global Mars legend | Expand only for codes intersecting named AOIs; cite the pamphlet for each |
| `usgs-mars-sim3292-global-geologic-map` | `Unit` / `UnitDesc` | none | none | Global 1:20M legend not crosswalked; 808 MB GIS not ingested | Gated download + checksum + per-unit crosswalk; still category only |
| `pangaea-glim-0p5-grid` | 16 first-level GLiM codes | none | none | Official legend/crosswalk missing; 0.5° is ~55 km | Crosswalk 16 codes with the official GLiM legend; coarse context only |
| `arcgis-glim-vector-mirror` | `Litho` | none | five overlays withheld | Licence unknown; upstream equivalence unverified | Obtain official vector licence or use PANGAEA 0.5° |
| `gsc-of5529-world-geology` | GSC `rxtyp` | `INTRUSIVE`, `SEDIMENT`, `SEDVOL`, `VOLCANIC` | two pilot AOIs | Only four broad values; 1:35M; no aqueous alteration | Versioned GSC registry; coarse context only |
| `macrostrat-api-map-units` | unit/lithology strings | none | point queries only | Heterogeneous regional maps; no shared crosswalk | Per-source crosswalk with attribution; point category only |

Requirements before any map-unit feature becomes a filter:

1. The crosswalk has its own version, independent of the source dataset version.
2. Every code has an official legend/description citation.
3. Raw code, original description and source dataset stay in the observation.
4. No cross-body or cross-dataset code equivalence.
5. Aqueous alteration is never inferred from iron oxide or a spectral index alone.
6. The result is `category_match`/hard filter only, **never** a Euclidean or weighted similarity.
7. The crosswalk is scoped to named AOIs; codes outside the reviewed scope stay unmapped.

## 6. Feature decision

| Feature id | Current state | Possible future role | Required evidence | Never |
|---|---|---|---|---|
| `geologic_unit` | 11 detected, `evidence_only` | category match after crosswalk | Versioned, dataset-scoped code → category crosswalk with official citations | Similarity distance; cross-body equivalence |
| `geologic_process` | 5 detected, `evidence_only` | category match after crosswalk | Official unit description explicitly states the process | Morphology-only impact/volcanic inference; impact-melt/breccia abundance |
| `mineral_olivine` | 2 `unknown` | presence filter after method validation | Validated detector, threshold, uncertainty, QA mask and native-pixel coverage | Abundance/wt%; zero-fill; "not detected" as zero |
| `mineral_pyroxene` | 2 `unknown` | presence filter after method validation | Same as above | Same |
| `mineral_clay_minerals` | 2 `unknown` | presence filter after method validation | Same as above; hydration is not aqueous alteration by itself | Same |
| `mineral_hydrated_sulfates` | 2 `unknown` | presence filter after method validation | Same as above | Same |
| `mineral_carbonates` | 2 `unknown` | presence filter after method validation | Same as above; carbonate is not automatically aqueous alteration | Same |
| `context_olindex3` | 2 detected | context/evidence only | Keep dimensionless; document the parameter | Filter or similarity |
| `context_lcpindex2` | 2 detected | context/evidence only | Same | Same |
| `context_hcpindex2` | 2 detected | context/evidence only | Same | Same |
| `context_d2300` | 2 detected | context/evidence only | Same | Same |
| `context_bd1900_2` | 2 detected | context/evidence only | Same | Same |
| `context_sindex2` | 2 detected | context/evidence only | Same | Same |
| `context_cindex2` | 2 detected | context/evidence only | Same | Same |
| `geology_analysis_availability` | 16 records | quality/evidence only | Keep `unknown`/`no_coverage` distinct | Zero-fill or negative detection |

**No D02 feature is registered for weighted similarity.** The distinction is deliberate: a map
unit at 1:5M, a 60 m EMIT mineral ID and a 36 m CRISM summary parameter do not share a method,
support or uncertainty model.

## 7. Bounded, checkpointed expansion plan

The plan is cumulative and gated. Each checkpoint has a byte cap and an acceptance condition; a
failed checkpoint leaves the source `accessible` and does not create an observation.

| ID | Body | Action | Max download | Acceptance | Status |
|---|---|---|---:|---|---|
| D02-EXP-0 | all | Metadata, labels and licence only | 0 B | Checker passes; no observation created | complete |
| D02-EXP-1 | Earth | Ingest the 38,670-byte GLiM 0.5° grid; verify 720 × 360, 16 codes, nodata `-9999` | 100,000 B | Checksum + class counts + explicit coarse-resolution limitation; no local similarity | planned |
| D02-EXP-2 | Earth | Macrostrat point queries at the five existing Earth AOI centroids; keep raw unit/lithology/`source_id` | 300,000 B | Five bounded JSON responses; empty = `no_coverage`, never zero; no area fraction | planned |
| D02-EXP-3 | Moon | Expand the versioned crosswalk only for DOMU codes intersecting the one existing ROI | 0 B (cached) | Every code cited; unmapped codes stay `unmapped_code`; one AOI | planned |
| D02-EXP-4 | Mars | Expand the SIM3464 crosswalk only for codes intersecting the two test AOIs | 0 B (cached) | Every code cited to the pamphlet; no cross-body equivalence; two AOIs | planned |
| D02-EXP-5 | Mars | Download SIM3292 once outside the repository, checksum, clip to two test AOIs plus one named region | 808,910,442 B | Explicit budget approval, SHA-256 receipt, bounded clip, no global claim | **blocked — budget** |
| D02-EXP-6 | Mars | Range-read OMEGA olivine/pyroxene windows only for named AOIs | 2,000,000 B | Byte receipts, label scaling applied, values kept as spectral parameters | planned |
| D02-EXP-7 | Earth/Moon/Mars | Validate one CRISM/OMEGA/M3/EMIT granule and a published threshold before any `detected` state | 50,000,000 B | Documented QA mask, uncertainty, `valid_fraction`, offline fixture | **blocked — method** |

The 808,910,442-byte SIM 3292 zip is the only large archive in the plan. It is explicitly gated:
global product coverage at 1:20M is not a global survey, and a download alone would not create a
valid D02 observation.

## 8. Observed checker output

Command:

```bash
python3 scripts/check_d02_sources.py
```

Observed output on 2026-11-15 (exit code 0). `GET` rows print bytes read; `HEAD` rows print the
declared `Content-Length`.

```text
D02 source audit: docs/data-sources/d02-geology.json
catalogue 1.0.0 | audit 2026-11-15 | 14 source(s)

[1/14] arcgis-glim-vector-mirror (Earth, identified)
  PASS ArcGIS layer metadata: GET 200 application/json; charset=utf-8 read=30011 B
       https://services8.arcgis.com/4KhTMTZ1x0f76DSg/arcgis/rest/services/GLiM_Niveau_I/FeatureServer/1?f=pjson

[2/14] gsc-of5529-world-geology (Earth, verified)
  PASS WMS GetCapabilities: GET 200 text/xml; charset=UTF-8 read=11572 B
       https://mrdata.usgs.gov/services/gscworld?service=WMS&request=GetCapabilities&version=1.3.0
  PASS OGL Canada licence: GET 200 text/html; charset=utf-8 read=23088 B
       https://open.canada.ca/en/open-government-licence-canada
  PASS NRCan DOI landing: GET 200 text/html; charset=utf-8 read=119426 B
       https://doi.org/10.4095/223767

[3/14] macrostrat-api-map-units (Earth, accessible)
  PASS API root: GET 200 application/json; charset=utf-8 read=1687 B
       https://macrostrat.org/api/v2
  PASS Death Valley point query: GET 200 application/json; charset=utf-8 read=4654 B
       https://macrostrat.org/api/v2/geologic_units/map?lat=36.25&lng=-117.11

[4/14] nasa-emit-l2b-mineral (Earth, accessible)
  PASS CMR collection 002: GET 200 application/json;charset=utf-8 read=9728 B
       https://cmr.earthdata.nasa.gov/search/collections.json?short_name=EMITL2BMIN&version=002&page_size=1
  PASS Earthdata catalog page: GET 200 text/html; charset=UTF-8 read=237696 B
       https://www.earthdata.nasa.gov/data/catalog/lpcloud-emitl2bmin-002

[5/14] pangaea-glim-0p5-grid (Earth, accessible)
  PASS PANGAEA DOI landing page: GET 200 text/html;charset=utf-8 read=27662 B
       https://doi.pangaea.de/10.1594/PANGAEA.788537
  PASS PANGAEA JSON-LD metadata: GET 200 application/ld+json; charset=UTF-8 read=5513 B
       https://doi.pangaea.de/10.1594/PANGAEA.788537?format=metadata_jsonld
  PASS grid zip HEAD: HEAD 200 application/zip 38670 B
       https://epic.awi.de/31092/1/hartmann-moosdorf_2012.zip

[6/14] pds-crism-mtrdr (Mars, verified)
  PASS ODE product query: GET 200 application/json; charset=utf-8 read=42107 B
       https://oderest.rsl.wustl.edu/live2/?query=product&pdsid=HRL000040FF_07_IF183J_MTR3&results=fp&output=JSON
  PASS MTRDR PDS3 label: GET 200 text/plain read=6179 B
       https://pds-geosciences.wustl.edu/mro/mro-m-crism-5-rdr-mptargeted-v1/mrocr_4001/mtrdr/2007/2007_029/hrl000040ff/hrl000040ff_07_if183j_mtr3.lbl

[7/14] pds-m3-l2-reflectance (Moon, accessible)
  PASS M3 volume page: GET 200 text/html; charset=UTF-8 read=6960 B
       https://pds-imaging.jpl.nasa.gov/volumes/m3.html
  PASS L2 PDS label: GET 200 - read=7315 B
       https://pds-imaging.jpl.nasa.gov/data/m3/CH1M3_0004/DATA/20081118_20090214/200811/L2/M3G20081118T222604_V01_L2.LBL
  PASS L2 ENVI header: GET 200 - read=37989 B
       https://pds-imaging.jpl.nasa.gov/data/m3/CH1M3_0004/DATA/20081118_20090214/200811/L2/M3G20081118T222604_V01_RFL.HDR

[8/14] pds-omega-global-mineral-maps (Mars, accessible)
  PASS olivine map label: GET 200 text/plain read=4679 B
       https://pds-geosciences.wustl.edu/mex/mex-m-omega-5-ddr-global-maps-v1/mexomg_2000/data/olivine/olivine_osp1_equ_map.lbl
  PASS pyroxene map label: GET 200 text/plain read=4576 B
       https://pds-geosciences.wustl.edu/mex/mex-m-omega-5-ddr-global-maps-v1/mexomg_2000/data/pyroxene/pyroxene_bd2000_equ_map.lbl
  PASS hydrous sites label: GET 200 text/plain read=5378 B
       https://pds-geosciences.wustl.edu/mex/mex-m-omega-5-ddr-global-maps-v1/mexomg_2000/data/hydrous/hydrous_sites_geo_map.lbl
  PASS volume readme: GET 200 text/plain read=11508 B
       https://pds-geosciences.wustl.edu/mex/mex-m-omega-5-ddr-global-maps-v1/mexomg_2000/aareadme.txt

[9/14] pds-registry-search (cross-body, accessible)
  PASS PDS search CRISM bundle query: GET 200 application/json read=9755 B
       https://pds.mcp.nasa.gov/api/search/1/products?q=title%20eq%20%22CRISM%20MTRDR%22&limit=1

[10/14] pds-tes-special-products (Mars, not_applicable)
  PASS TES thermal-inertia readme: GET 200 text/plain read=10194 B
       https://pds-geosciences.wustl.edu/mgs/mgs-m-tes-5-timap-v1/mgst_9001/aareadme.txt
  PASS TES special-products listing: GET 200 text/html; charset=UTF-8 read=939 B
       https://pds-geosciences.wustl.edu/mgs/mgs-m-tes-special-v1/

[11/14] usgs-astrogeology-stac (cross-body, not_applicable)
  PASS collections: GET 200 application/json; charset=utf-8 read=64658 B
       https://stac.astrogeology.usgs.gov/api/collections

[12/14] usgs-mars-sim3292-global-geologic-map (Mars, accessible)
  PASS USGS publication page: GET 200 text/html read=11996 B
       https://pubs.usgs.gov/sim/3292/
  PASS FGDC metadata: GET 200 text/plain read=14233 B
       https://pubs.usgs.gov/sim/3292/downloads/sim3292_metadata.txt
  PASS database zip HEAD: HEAD 200 application/zip 808910442 B
       https://pubs.usgs.gov/sim/3292/downloads/sim3292_database.zip

[13/14] usgs-mars-sim3464-jezero-geologic-map (Mars, verified)
  PASS USGS publication index: GET 200 text/html;charset=ISO-8859-1 read=3879 B
       https://pubs.usgs.gov/sim/3464/
  PASS FGDC metadata: GET 200 text/plain read=20767 B
       https://pubs.usgs.gov/sim/3464/sim3464_metadata.txt
  PASS database zip HEAD: HEAD 200 application/zip 349336873 B
       https://pubs.usgs.gov/sim/3464/sim3464_database.zip

[14/14] usgs-moon-unified-geologic-map-v2 (Moon, verified)
  PASS Astropedia landing page: GET 200 text/html; charset=utf-8 read=26134 B
       https://astrogeology.usgs.gov/search/map/Moon/Geology/Unified_Geologic_Map_of_the_Moon_GIS_v2/
  PASS product zip HEAD: HEAD 200 application/zip 224413040 B
       https://asc-astropedia.s3.us-west-2.amazonaws.com/Moon/Geology/Unified_Geologic_Map_of_the_Moon_GIS_v2.zip
  PASS [licence] USGS copyright page: GET 200 text/html; charset=utf-8 read=88994 B
       https://www.usgs.gov/information-policies-and-instructions/copyrights-and-credits
  PASS [licence] PDS Geosciences data-use document HEAD: HEAD 200 application/pdf 1181900 B
       https://pds-geosciences.wustl.edu/documents/ViewingPDSImagesAndExportingToOtherFormats.pdf

14 source(s), 34 check(s): 34 passed, 0 failed
RESULT: PASS
```

## 9. Reproduce

```bash
# full audit (network; metadata only, no large archives)
python3 scripts/check_d02_sources.py

# one source
python3 scripts/check_d02_sources.py --source pds-omega-global-mineral-maps

# list source ids without network
python3 scripts/check_d02_sources.py --list

# machine-readable result
python3 scripts/check_d02_sources.py --json
```

The checker is stdlib-only, sends a stable user agent, caps every GET body, uses `HEAD` for
archives/PDFs and exits non-zero on any status, content-type, body-text, JSON/body or length
regression. It does not write to the repository and does not use credentials.

## 10. Limitations and open blockers

1. **No global survey.** Product coverage (global Moon/Mars maps, global GLiM grid) is not local
   accuracy and not a global survey of analogs. The repository has 8 bounded AOIs.
2. **No mineral abundance.** CRISM/OMEGA/M3 values are spectral parameters or reflectance; EMIT
   band depth is not a mass fraction. No wt% conversion is registered.
3. **No D02 similarity.** Map units are categorical and dataset-scoped. A crosswalk can support a
   category filter, not a distance.
4. **EMIT is gated.** `emit_auth` (Earthdata Login), `emit_decoder` (NetCDF/QA/fill values) and
   `emit_native_swath` (per-pixel geolocation) remain open. No open 60 m Earth mineral-ID
   alternative was found.
5. **M3 is gated.** No granule downloaded; registration, thermal correction, band alignment and
   detection thresholds are unvalidated.
6. **CRISM thresholds/DP mask.** No published threshold or DP-mask bit mapping is registered here;
   the bounded ODE query is not exhaustive.
7. **OMEGA semantics.** The global maps store the highest value in a 0.025° cell, not an AOI mean;
   OSP1/BD2000 mix composition, grain size and abundance; the hydrous CSV has no species column.
8. **GLiM vector licence.** The ArcGIS mirror has no `licenseInfo`; do not publish its geometry or
   values. The open PANGAEA 0.5° grid is CC BY 3.0 but ~55 km per cell.
9. **Scale mismatch.** SIM 3292 is 1:20M (minimum outcrop 40 × 100 km), Moon v2 is 1:5M, GSC is
   1:35M, GLiM is 0.5°. None can resolve a 1.98 km AOI as a local measurement.
10. **Stale URLs exist.** `earth.jpl.nasa.gov/emit/data/data-products/` returned 404 during this
    audit; the checker pins the working Earthdata/CMR endpoints instead. PDS Search redirects to
    `pds.mcp.nasa.gov`.
11. **USGS main site bot protection.** `www.usgs.gov` returns 403 without a browser-like user agent;
    product pages on `pubs.usgs.gov`, Astropedia and S3 are the canonical checked endpoints.
12. **No credentials were used or stored.** If a future step needs Earthdata Login, it must remain
    opt-in and outside CI.

## 11. Deliverables

| File | Purpose |
|---|---|
| `docs/data-sources/d02-geology.json` | machine-readable source catalogue: status, access, licence, coverage, CRS, resolution, D02 outputs, feature mapping, limitations, blockers and exact HTTP evidence |
| `docs/D02_SOURCE_AUDIT.md` | this audit: existing coverage, reachable sources, crosswalk gap, feature decision, checkpointed plan, observed checker output |
| `scripts/check_d02_sources.py` | stdlib-only re-runnable checker; exits non-zero on regression; no large downloads or credentials |
