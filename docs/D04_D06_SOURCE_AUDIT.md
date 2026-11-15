# D04/D06 water-ice and regolith open-data source audit

Status: **audit complete; no application code changed; no global coverage claim.**
Branch: `audit/d-source-audit` at base commit `df6e365`.
Machine-readable catalog: [`docs/data-sources/d04-d06-water-regolith.json`](data-sources/d04-d06-water-regolith.json).
Rerunnable checker: [`scripts/check_d04_d06_sources.py`](../scripts/check_d04_d06_sources.py).
Observed on: 2026-11-15.

This audit answers three questions:

1. What D04/D06 coverage already exists in the repository?
2. Which official or confirmed public sources are actually reachable, under what licence,
   support, CRS/frame and depth/time limits?
3. What is a bounded, checkpointed way to add verified coverage without inventing data or
   claiming a global survey?

> **Coverage snapshot:** record counts below are from the audit base `df6e365` (520 typed
> observations). Current `main` includes a bounded D06 SoilGrids expansion (565+ observations);
> see [`FEATURE_ELIGIBILITY_AUDIT.md`](FEATURE_ELIGIBILITY_AUDIT.md) for the live registry counts.
> Source reachability, licences, methods and comparability findings in this audit remain valid.

## Scope and non-claims

- Dimensions: D04 Water & Ice and D06 Regolith & Surface Properties; bodies Earth, Moon, Mars.
- No observation is fabricated, zero-filled, interpolated or transferred across bodies.
- A reachable source is not an ingested observation.
- D04 water-equivalent hydrogen, neutron flux and radar CPR are evidence, not confirmed ice.
- SoilGrids values are 250 m model predictions, not local measurements.
- No Moon or Mars D06 physical-property coverage is claimed.
- Five Earth soil AOIs and a handful of planetary reference windows are **not** a global,
  representative or exhaustive survey.

## 1. Existing coverage (verified from committed releases)

### D04 — 75 observations, 0 confirmed ice detections

| Body | Observations | Composition |
|---|---:|---|
| Earth | 20 | NASA POWER context: surface soil wetness, root-zone soil wetness, snow depth, precipitation (5 AOIs each) |
| Moon | 53 | 12 water-equivalent hydrogen, 12 radar CPR, 29 water/ice detection claims |
| Mars | 2 | 1 normalized epithermal flux, 1 water/ice detection claim |
| **Total** | **75** | |

- By state: `detected=45`, `unknown=24`, `no_coverage=5`, `not_detected=1`.
- All 30 `water_ice_detection` claims are `unknown`, `no_coverage` or `not_detected`;
  **confirmed ice detections = 0**.
- Releases: `pipelines/pilots/d04_water_ice_planetary/` (55 Moon/Mars observations) and
  `pipelines/pilots/d04_earth_power/` (20 Earth context observations).
- D04 has no similarity feature. All D04 records are evidence-only or context-only.

### D06 — 300 observations, all Earth SoilGrids model predictions

| Slice | Count |
|---|---:|
| Observations | 300 |
| Requests succeeded / failed | 300 / 0 |
| QA groups complete | 100 / 100 |
| Areas | 5 (`earth-atacama-desert`, `earth-death-valley`, `earth-devon-island`, `earth-lanzarote`, `earth-sahara-r1c1`) |
| Properties | 5 (`bdod`, `clay`, `sand`, `silt`, `soc`) |
| Depths | 4 (`0-5`, `5-15`, `15-30`, `30-60` cm) |
| Statistics | 3 (`mean`, `Q0.05`, `Q0.95`) |

- 75 observations per depth; 60 per property; 100 per statistic.
- Release: `pipelines/pilots/d06_earth_soilgrids_depths/`; raw TIFF responses are not committed,
  only request/response checksums.
- Evidence class: **model prediction, not a measurement**. The AOI value is the mean of native
  250 m model cells over the envelope, not a SoilGrids product cell value at a point.
- `0-5 cm` means are the preferred gated Earth-Earth non-D01 similarity feature. `Q0.05`/`Q0.95`
  are uncertainty evidence at the same area/property/depth; they are not independent observations
  and not a confidence interval for the AOI mean.

## 2. Feature and eligibility mapping

`apps/api/src/analog_api/domain/eligibility.py` is present on `feat/116-multidim-search`
(commit `194375c`), not on this audit branch. It was read with
`git show 194375c:apps/api/src/analog_api/domain/eligibility.py`; it was not modified.

### D06 gates that matter for expansion

- The D06 feature pattern is
  `^soil_(bdod|clay|sand|silt|soc)_(0_5|5_15|15_30|30_60)cm_(mean|q05|q95)$`.
- Only `mean` is similarity-eligible; `q05`/`q95` are uncertainty evidence.
- A mean is eligible only when all of the following hold: Earth body, exact
  `area_id == candidates.location_id`, method version `soilgrids-wcs-2.0.0`, exact registered
  unit, `state=detected`, finite numeric value, and recorded AOI bbox equal to the candidate bbox
  within `1e-6` degree.
- Depths are never pooled. A value for one interval cannot stand in for another.
- `60-100 cm` and `100-200 cm` exist at ISRIC but are **not** in `_D06_PATTERN`/`_DEPTHS`.
  If ingested today they would fall through to generic evidence-only handling, not similarity.
  Adding them is a semantics change and a new contract version, not a data-only change.
- D06 has no `regolith_thickness_m` feature. A regolith-thickness dataset cannot be ingested as
  a D06 similarity/evidence feature until that registry gap is closed.

### D04 gates that matter for expansion

- Every observed D04 feature is evidence-only in `eligibility.py`.
- No cross-body or same-quantity mapping is registered for hydrogen proxies, radar CPR, soil
  wetness or snow depth.
- The Earth NASA POWER feature IDs (`surface_soil_wetness_fraction`,
  `root_zone_soil_wetness_fraction`, `snow_depth_cm`, `precipitation_mm_day`) are pilot-local and
  are not in `FEATURES_V2`; they remain context-only.
- `water_ice_depth_interval` is registered but has zero existing observations.

### Mapping gap between SoilGrids IDs and the canonical D06 registry

The 300 committed observations use generated evidence IDs such as
`soil_clay_0_5cm_mean` and `soil_bdod_30_60cm_q95`. The canonical D06 registry features are
`particle_size_distribution`, `bulk_density_kg_m3`, `porosity_fraction`, `cohesion_kpa`,
`friction_angle_deg` and `bearing_strength_kpa`. The pilot deliberately does **not** merge
clay/sand/silt into a particle-size distribution, and does **not** relabel fine-earth `bdod` as
total regolith bulk density. This is a correct semantic separation, but it means the canonical
D06 features still have zero observations.

## 3. Verified reachable sources

Method: each source was probed with an actual HTTP request, using byte ranges or tiny WCS
subsets where the product is large. The checker records only status, content type, bounded body
size, `Content-Range` total and marker strings. It never downloads the 4.25 GB Mini-RF image, the
14.3 MB Lunar Prospector table or a full SoilGrids coverage.

| Source | Dimension / body | Status | Verified evidence (2026-11-15) | Existing/candidate feature |
|---|---|---|---|---|
| PDS Lunar Prospector NS `hydrogenhd` | D04 / Moon | usable, existing | `206 text/plain`, 2,048 B read, total 14,256,401 B | `water_equivalent_hydrogen_wt_percent` (12 obs) |
| PDS LRO Mini-RF global CPR | D04 / Moon | usable, existing | label `206 text/plain` 2,048 B / 3,875 B; image `206 application/octet-stream` 1,024 B / 4,246,732,800 B | `radar_circular_polarization_ratio` (12 obs) |
| PDS Odyssey HEND epithermal | D04 / Mars | usable, existing | `206 text/plain`, 2,048 B / 202,500 B | `epithermal_neutron_flux_ratio` (1 obs) |
| PDS Odyssey NS epithermal 0.5° | D04 / Mars | usable, candidate | label `200 text/plain` 2,860 B; table `206 text/plain` 1,024 B / 6,739,200 B | no registered counts/s feature; cannot reuse the normalized ratio |
| PDS LRO LEND ALDS | D04 / Moon | blocked | XML `206 application/xml` 4,096 B / 19,224 B; DAT `206 application/octet` 1,024 B / 4,464,000 B | blocked: relative counts lack normalization constants |
| PDS MRO SHARAD radargrams | D04 / Mars | blocked | bundle `200 application/xml` 5,303 B; collection `200 application/xml` 7,630 B; label `200 text/plain` 2,152 B | blocked: no per-cell detection mask; clutter modelling required |
| PDS MRO CRISM TRDR | D04+D02 / Mars | blocked | bundle `200 application/xml` 5,815 B; label `200 text/plain` 9,512 B | D02 hydration, not D04 ice; atmospheric correction required |
| PDS MAVEN IUVS | D04 / Mars | not applicable | `200 text/html` 20,080 B | atmospheric D/H, not surface/subsurface ice |
| NASA POWER daily point API | D04 / Earth | usable, existing | `200 application/json` ~750 B (745–747 B observed) | 4 pilot-local context features (20 obs) |
| ISRIC SoilGrids 2.0 WCS | D06 / Earth | usable, existing | capabilities `200 text/xml` 12,503 B; DescribeCoverage 2,531/2,543/2,547 B; GetCoverage `200 image/tiff` 601/603 B | `soil_*_mean/q05/q95` (300 obs); 0-5 cm mean similarity-eligible |
| ISRIC SoilGrids REST API | D06 / Earth | unavailable | timeout after 60 s, 0 bytes (skipped by checker) | not used; WCS is the verified alternative |
| PlanetGSD 1.0 (Figshare) | D06 / Moon, Mars, Earth | usable, candidate | API `200 application/json` 5,874 B; Moon XLSX `200` 36,833 B; ESSD page `200 text/html` 340,549 B | `particle_size_distribution` candidate; blocked by missing per-sample depth |
| Zenodo lunar regolith thickness | D06 / Moon | blocked | `200 application/json` 5,089 B; 18,769,419 B ZIP (not downloaded) | no D06 thickness feature; licence review |
| USGS Astrogeology Astropedia | D06 / Moon | not applicable | product page `200 text/html` 18,873 B | boulder size-frequency context, not a D06 physical property |
| USGS Lunar Sourcebook Table 7.15 | D06 / Moon | not applicable | `200 binary/octet-stream` 6,745 B | soil chemistry, not a D06 physical property |

### 3.1 D04 findings

#### Lunar Prospector Neutron Spectrometer (existing, usable)

- Exact URL: <https://pds-geosciences.wustl.edu/missions/lunarp/reduced/hydrogenhd.txt>
- HTTP: `206 text/plain`, 2,048 B read, `Content-Range: bytes 0-2047/14256401`.
- Licence: NASA PDS open data; recorded as CC0-1.0 by repository precedent. The NASA licence page
  itself is reachable (`200 text/html`, 72,405 B) but does not enumerate per-product terms.
- Support: 0.5° × 0.5° planetocentric `IAU_MOON` cells, about 15.1 km; upper ~1 m neutron sensing
  depth; mission composite 1998–1999.
- Mapping: `water_equivalent_hydrogen_wt_percent`; 12 existing observations.
- Limit: 0.5° cells cannot resolve a 1.98 km window; no per-cell uncertainty; WEH is not an ice
  fraction or a detection.

#### LRO Mini-RF global CPR (existing, usable)

- Exact label URL: <https://pds-geosciences.wustl.edu/lro/lro-l-mrflro-5-global-mosaic-v1/lromrf_1001/data/128ppd/global_cpr_128ppd_simp_0c.lbl>
- Exact image URL: <https://pds-geosciences.wustl.edu/lro/lro-l-mrflro-5-global-mosaic-v1/lromrf_1001/data/128ppd/global_cpr_128ppd_simp_0c.img>
- HTTP: label `206 text/plain` 2,048 B / 3,875 B; image `206 application/octet-stream` 1,024 B /
  4,246,732,800 B. The checker reads only the first 1 KB of the image.
- Support: 128 pixels/degree, 0.23690117518867 km/pixel (about 237 m); simple cylindrical
  `IAU_MOON`; 2009-07-19 through 2010-12-26; mosaic mixes acquisitions.
- Mapping: `radar_circular_polarization_ratio`; 12 existing observations.
- Limit: CPR ≥ 1 is ice-consistent but not diagnostic; rough surfaces and rocks also raise CPR;
  no depth retrieval.

#### Odyssey HEND epithermal (existing, usable)

- Exact URL: <https://pds-geosciences.wustl.edu/ody/urn-nasa-pds-odyssey_grs_special/data/hend_epithermal_020917.tab>
- HTTP: `206 text/plain`, 2,048 B / 202,500 B.
- Support: 4° × 4° cells (about 237 km), `IAU_MARS`; upper ~1 m; first 55 days of mapping (2002),
  exact interval not stated in the label.
- Mapping: `epithermal_neutron_flux_ratio`; 1 existing observation.
- Limit: one native cell; no per-cell uncertainty; normalized flux is a hydrogen proxy, not WEH
  or ice abundance.

#### Odyssey NS epithermal 0.5° (new candidate, not ingested)

- Label: <https://pds-geosciences.wustl.edu/ody/urn-nasa-pds-odyssey_grs_special/data/ns_epithermal_020917.lbl>
- Table: <https://pds-geosciences.wustl.edu/ody/urn-nasa-pds-odyssey_grs_special/data/ns_epithermal_020917.tab>
- HTTP: label `200 text/plain` 2,860 B; table `206 text/plain`, 1,024 B / 6,739,200 B.
- Support: 0.5° × 0.5° (about 30 km), `IAU_MARS`; upper ~1 m; first 55 days of mapping.
- Why it is a candidate: the 0.5° table is eight times finer in linear cell size than HEND's
  4° cells and is a real public product, but the stored quantity is **counts/second**, not the
  normalized
  `epithermal_neutron_flux_ratio`. Reusing the ratio feature would be a unit/semantics error.
- Blocker: no registered absolute-flux feature and no documented normalization to compare with
  HEND. Even after that, 30 km is far coarser than the 1.98 km reference windows.

#### LRO LEND ALDS (blocked)

- Exact XML: <https://pds-geosciences.wustl.edu/lro/lro-l-lend-2-edr-v1/lrolen_0xxx/data/2009/20090915/lend_rdr_alds_20090915.xml>
- Exact DAT: <https://pds-geosciences.wustl.edu/lro/lro-l-lend-2-edr-v1/lrolen_0xxx/data/2009/20090915/lend_rdr_alds_20090915.dat>
- HTTP: XML `206 application/xml` 4,096 B / 19,224 B; DAT `206 application/octet` 1,024 B /
  4,464,000 B.
- Support: 0.5° polar grid below −80° latitude; 2009-09-15 through 2011-12-11; upper ~1 m.
- Blocker: the ALDS table carries relative counting rates but not the per-detector normalization
  constants needed for flux or WEH. Reinterpreting the counts as abundance would invent a
  conversion. No D04 observation is derived.

#### MRO SHARAD radargrams (blocked)

- Bundle: <https://pds-geosciences.wustl.edu/mro/mro-m-sharad-5-radargram-v2/mrosh_2101/bundle_mro_sharad_us_radargrams.xml>
- Collection: <https://pds-geosciences.wustl.edu/mro/mro-m-sharad-5-radargram-v2/mrosh_2101/data/collection_data.xml>
- Example label: <https://pds-geosciences.wustl.edu/mro/mro-m-sharad-5-radargram-v2/mrosh_2101/data/rgram/s_0016xx/s_00168901_rgram.lbl>
- HTTP: bundle `200 application/xml` 5,303 B; collection `200 application/xml` 7,630 B; label
  `200 text/plain` 2,152 B.
- Support: orbit-track radargrams with per-product geometry; sounding radar with model-dependent
  depth; example acquisition 2006-12-06T02:08:22.929–02:09:48.723.
- Blocker: SHARAD products are radargrams and clutter simulations, not a per-cell detection mask
  or abundance grid. A defensible subsurface detection needs track selection, clutter modelling
  and reflector interpretation. The Jezero CTX windows are not a SHARAD detection product.

#### MRO CRISM TRDR (blocked for D04)

- Bundle: <https://pds-geosciences.wustl.edu/mro/mro-m-crism-3-rdr-targeted-v1/mrocr_2101/bundle_crism_trdr_2101.xml>
- Example label: <https://pds-geosciences.wustl.edu/mro/mro-m-crism-3-rdr-targeted-v1/mrocr_2101/trdr/2006/2006_270/frt000027b2/frt000027b2_01_if156l_trr3.lbl>
- HTTP: bundle `200 application/xml` 5,815 B; label `200 text/plain` 9,512 B.
- Support: targeted radiance cubes; no areal ice grid; per-target acquisition time (example
  2006-09-27T17:12:00.329).
- Blocker: TRDR cubes require atmospheric correction and spectral summary computation.
  Hydration is D02 mineral evidence, not automatically D04 ice. No D04 observation is derived.

#### MAVEN IUVS (not applicable)

- Exact page: <https://pds-atmospheres.nmsu.edu/data_and_services/atmospheres_data/MAVEN/maven_iuvs.html>
- HTTP: `200 text/html`, 20,080 B.
- Support: atmospheric limb/disk/corona spectra and D/H profiles from the homopause upward; data
  through 2025-12-15.
- Why not applicable: IUVS measures atmospheric composition and escape, not surface or subsurface
  ice. Mapping atmospheric D/H onto a regolith water-ice feature would be a category error.
- Note: the PDS Geosciences MAVEN path probed during the audit returned `404`; the PDS Atmospheres
  Node page above is the reachable official source.

#### NASA POWER daily point (existing, context only)

- Exact URL (one-day probe):
  <https://power.larc.nasa.gov/api/temporal/daily/point?parameters=GWETTOP%2CGWETROOT%2CSNODP%2CPRECTOTCORR&community=AG&longitude=-68.399999&latitude=-23.500000&start=20240101&end=20240101&format=JSON>
- HTTP: `200 application/json`, ~750 B (745–747 B observed; the gate uses a broad range).
- Support: Earth WGS84 point on a POWER grid of about 0.5° × 0.625° (about 50 km); daily values;
  existing pilot covers 2024-01-01 through 2024-01-07.
- Mapping: four pilot-local context features, 5 observations each.
- Limit: reanalysis/model context, not a local water/ice measurement; soil wetness, snow depth
  and precipitation are distinct quantities and are never merged.

### 3.2 D06 findings

#### ISRIC SoilGrids 2.0 WCS (existing, usable)

- Exact capabilities URL:
  <https://maps.isric.org/mapserv?map=/map/clay.map&service=WCS&version=2.0.1&request=GetCapabilities>
- HTTP: `200 text/xml`, 12,503 B. `DescribeCoverage` for `clay_0-5cm_mean`, `clay_60-100cm_mean`
  and `clay_100-200cm_mean` returned `200 text/xml` at 2,531/2,543/2,547 B. A two-by-two cell
  `GetCoverage` returned `200 image/tiff` at 601 B (0-5 cm) and 603 B (60-100 cm).
- Native CRS/frame: `EPSG:152160` (Interrupted Goode Homolosine, metre axes), 250 m cells. The
  pilot subsets in `EPSG:4326`; that is a subsetting CRS, not the native grid.
- Depth support: six standard intervals exist at ISRIC (`0-5`, `5-15`, `15-30`, `30-60`,
  `60-100`, `100-200` cm). The repository registers only the first four.
- Licence: CC BY 4.0; the ISRIC docs page states six standard depths and 250 m
  (`200 text/html`, 34,690 B). Attribution/citation is required.
- Existing coverage: 5 AOIs, 5 properties, 4 depths, 3 statistics, 300 observations, 100 QA
  groups, 300/300 requests succeeded, 0 failed.
- Mapping: `soil_*_mean` / `_q05` / `_q95`; 0-5 cm means are the preferred gated Earth-Earth
  non-D01 similarity feature. Q05/Q95 are uncertainty evidence only.
- Limits: model prediction, not local measurement; AOI-envelope mean, not a product cell value;
  clay/sand/silt are compositional siblings; `bdod` is fine-earth bulk density and excludes
  coarse fragments; five AOIs are not global.

#### ISRIC SoilGrids REST API (unavailable)

- Probe URL:
  <https://rest.isric.org/soilgrids/v2.0/properties/query?lon=-68.4&lat=-23.5&property=clay&depth=0-5cm&value=mean>
- Observed: timeout after 60 s, 0 bytes. The checker records this as `check=false` and skips it;
  it is not counted as a pass.
- Alternative: the WCS endpoint above. A point query would not match the pilot's AOI-envelope
  mean support even if it responded.

#### PlanetGSD 1.0 (candidate, blocked by depth metadata)

- Figshare API: <https://api.figshare.com/v2/articles/32362083> (`200 application/json`, 5,874 B).
- Moon XLSX: <https://ndownloader.figshare.com/files/64804113> (`200`, 36,833 B). The Moon file
  was inspected: 8 mission sheets (Apollo 11/12/14/15/16/17, CE5C0400, CE-6).
- Paper page: <https://essd.copernicus.org/articles/18/4983/2026> (`200 text/html`, 340,549 B).
  The page string-checks for `379` lunar and `1729` Martian distributions and `CC BY`.
- Licence: CC BY 4.0 per Figshare metadata.
- What it can produce: `particle_size_distribution` evidence at sample sites, not an areal map.
- Blocker: the inspected S2 lunar table does not carry per-sample depth; the S3 Mars depth
  fields are unverified because that 3.66 MB file was not downloaded. D06 detected observations
  require explicit depth support, so ingesting the lunar rows today would fail validation or
  force an `unknown` state that adds no coverage. Bin edges and basis must also be mapped to the
  repository's USDA lower-edge convention. The Mars XLSX (3.66 MB) was not downloaded.

#### Zenodo lunar regolith thickness (blocked)

- API: <https://zenodo.org/api/records/18829373> (`200 application/json`, 5,089 B).
- Product: Jones & Rajšić (2026), *Lunar Surface Regolith Thickness Mapping*, v2.0.0, DOI
  `10.5281/zenodo.18829373`; one 18,769,419 B ZIP (not downloaded).
- Licence: GPL-3.0 in Zenodo metadata. This is a software licence; data-specific redistribution
  terms are not separately stated. Review is required.
- Blocker: D06 has no `regolith_thickness_m` feature. The archive also contains a derived global
  IDW map; that map is a model product and is not measured coverage. The sparse point
  measurements could be evidence after a registry/contract change, not before.

#### USGS Astrogeology (reachable, but no verified D04/D06 property product)

- Astropedia product page:
  <https://astrogeology.usgs.gov/search/map/moon_lro_lroc_nac_boulder_database_v1_watkins>
  (`200 text/html`, 18,873 B).
- Product: Moon LRO LROC NAC Boulder Database v1 Watkins, CSV/PDS4 tabular bundle, LROC NAC
  ~0.5–2 m/pixel, selected small craters near landing sites.
- What it is: boulder size-frequency and regolith-production context. It is **not** a soil
  particle-size distribution, bulk density, porosity, cohesion or bearing strength.
- Lunar Sourcebook Table 7.15:
  <https://lunar-consortium.astrogeology.usgs.gov/data/chem_min/tab7_15.asc>
  (`200 binary/octet-stream`, 6,745 B). It is soil chemistry, not a D06 physical property.
- Blocker: no machine-readable D04 water-ice or D06 physical-property product was verified in
  this pass. The USGS product page states `Access Scope=PDS` and `PDS Status=PDS 4 In Review` but
  does not state a licence; USGS licence pages returned `403` from this environment. Per-product
  terms need confirmation before redistribution.

## 4. Support and depth gap table

| Dim | Body | Quantity | Verified source | Native support | Depth / time | Existing feature | Gap |
|---|---|---|---|---|---|---|---|
| D04 | Earth | surface/root-zone wetness, snow, precipitation | NASA POWER | ~50 km point; daily | surface/root-zone; no subsurface ice | pilot-local context IDs | no 1.98 km measurement; no subsurface ice |
| D04 | Moon | water-equivalent hydrogen | Lunar Prospector NS | 0.5° (~15 km) | upper ~1 m; 1998–1999 composite | `water_equivalent_hydrogen_wt_percent` | coarse; no per-cell uncertainty; not ice fraction |
| D04 | Moon | radar CPR | LRO Mini-RF | ~237 m | no depth retrieval; 2009–2010 | `radar_circular_polarization_ratio` | not diagnostic; mixed acquisitions |
| D04 | Mars | epithermal neutron flux | Odyssey HEND | 4° (~237 km) | upper ~1 m; first 55 days 2002 | `epithermal_neutron_flux_ratio` | one cell; no WEH conversion |
| D04 | Mars | epithermal neutron flux | Odyssey NS | 0.5° (~30 km) | upper ~1 m; first 55 days 2002 | candidate counts/s feature | not a ratio; needs normalization and new method |
| D04 | Moon | polar neutron count rate | LRO LEND ALDS | 0.5° polar | upper ~1 m; 2009–2011 | blocked | missing normalization constants |
| D04 | Mars | subsurface radar reflectors | MRO SHARAD | orbit tracks | model-dependent | blocked | no per-cell mask; clutter modelling |
| D04 | Mars | surface hydration | MRO CRISM | targeted cubes | surface | D02, not D04 | hydration is not ice; atmospheric correction |
| D04 | Mars | atmospheric D/H | MAVEN IUVS | atmospheric profiles | not surface | not applicable | does not measure surface ice |
| D06 | Earth | clay/sand/silt, SOC, fine-earth bulk density | SoilGrids 2.0 WCS | 250 m; 5 AOIs | 0–60 cm in 4 intervals | `soil_*_mean/q05/q95` | model prediction; 5 AOIs; no 60–200 cm; not local samples |
| D06 | Earth | same properties deeper | SoilGrids 2.0 WCS | 250 m | 60–100, 100–200 cm reachable | not registered | eligibility regex/semantics stop at 60 cm |
| D06 | Moon | particle-size distribution | PlanetGSD 1.0 | sample curves | S2 depth missing; S3 unverified | `particle_size_distribution` (blocked) | depth, bin/basis and licence review |
| D06 | Moon | regolith thickness | Zenodo 18829373 | sparse points + derived map | thickness in metres | no feature ID | no D06 thickness feature; licence review |
| D06 | Moon | boulder size-frequency / soil chemistry | USGS Astrogeology | selected sites / site averages | surface / bulk | not applicable | no D06 physical-property product verified |
| D06 | Mars | particle-size distribution | PlanetGSD 1.0 | image-derived curves | S3 not downloaded; per-sample depth unverified | `particle_size_distribution` (blocked) | image-derived, truncated fine tail; depth unverified |
| D06 | Mars | bulk density, porosity, cohesion, friction, bearing | none verified | n/a | n/a | no coverage | no public per-cell/per-site machine-readable product verified |

## 5. Bounded, checkpointed expansion plan

The plan adds coverage in small, pre-registered steps. Every checkpoint must preserve exact
`area_id`/depth/statistic joins, report per-AOI counts, and avoid any global or representative
claim. This plan is **proposed, not executed** except where marked complete.

### Checkpoint 0 — freeze the current evidence (complete)

- Keep the committed 75 D04 and 300 D06 observations unchanged.
- Verify release integrity and manifest checksums; do not substitute a source.
- Requests: 0. Acceptance: existing counts and QA groups unchanged.

### Checkpoint 1 — more Earth AOIs at the same SoilGrids support (recommended)

- Add 5 pre-registered Earth AOIs, same 5 properties, same 4 depths, same 3 statistics.
- Requests: `5 AOIs × 5 properties × 4 depths × 3 statistics = 300` tiny WCS requests.
- Acceptance: 300/300 succeeded or every failure recorded per request; 100 new QA groups; no
  fabricated values; report explicitly says **10 AOIs**, not global.
- Gate: the AOI list must be fixed in a plan file before requests. No ad hoc coordinate
  expansion.

### Checkpoint 2 — deeper SoilGrids intervals (semantics change)

- Add `60-100 cm` and `100-200 cm` at the existing 5 AOIs.
- Requests: `5 AOIs × 5 properties × 2 depths × 3 statistics = 150` tiny WCS requests.
- Required work before ingestion: update `contracts/d06-regolith-semantics-v1.json`,
  `_D06_PATTERN`, `_DEPTHS`, eligibility tests and the API contract; bump the semantics version.
- Acceptance: 150/150 or explicit failures; depth never pooled; 0-5 cm remains the preferred
  similarity feature. Do not ingest before the version bump.

### Checkpoint 3 — Moon/Mars particle-size evidence (research gate)

- Evaluate PlanetGSD lunar and Martian distributions as **evidence-only** D06 observations.
- Downloads: Moon XLSX 36.8 KB; Mars XLSX 3.66 MB; no raster.
- Acceptance: per-sample depth, bin edges and basis documented; detected observations pass the
  explicit-depth rule; otherwise record `unknown` and do not claim coverage. Licence/attribution
  review is required.

### Checkpoint 4 — Mars 0.5° epithermal evidence (research gate)

- Evaluate the Odyssey NS 0.5° epithermal table as a D04 evidence-only Mars source.
- Download: one 6.7 MB ASCII table plus label; no raster.
- Acceptance: register an absolute-flux feature or a documented normalization; keep it
  evidence-only; do not reuse `epithermal_neutron_flux_ratio` without unit proof. Even then,
  0.5° is ~30 km and cannot resolve a 1.98 km window.

### Do not do

- Do not treat SoilGrids model predictions as measured samples.
- Do not map D04 atmospheric D/H, neutron flux or CPR directly to ice abundance.
- Do not pool SoilGrids depths or substitute a point query for an AOI mean.
- Do not ingest PlanetGSD or Zenodo data without depth/licence review.
- Do not call any checkpoint global, exhaustive or representative.

## 6. Checker output (observed)

Command:

```bash
python3 scripts/check_d04_d06_sources.py
```

Observed output (exit code 0):

```text
Catalog: docs/data-sources/d04-d06-water-regolith.json
Checks: 40 | timeout: 30s | jobs: 3
----------------------------------------------------------------------------------------------------
PASS  pds-lp-ns-hydrogenhd:primary                         status=206 type='text/plain' bytes=2048
PASS  pds-lp-ns-hydrogenhd:license                         status=200 type='text/html; charset=UTF-8' bytes=72405
PASS  pds-lro-minirf-cpr-128ppd:primary                    status=206 type='text/plain' bytes=2048
PASS  pds-lro-minirf-cpr-128ppd:extra0                     status=206 type='application/octet-stream' bytes=1024
PASS  pds-lro-minirf-cpr-128ppd:license                    status=200 type='text/html; charset=UTF-8' bytes=72405
PASS  pds-odyssey-hend-epithermal:primary                  status=206 type='text/plain' bytes=2048
PASS  pds-odyssey-hend-epithermal:license                  status=200 type='text/html; charset=UTF-8' bytes=72405
PASS  pds-odyssey-ns-epithermal-0p5deg:primary             status=200 type='text/plain' bytes=2860
PASS  pds-odyssey-ns-epithermal-0p5deg:extra0              status=206 type='text/plain' bytes=1024
PASS  pds-odyssey-ns-epithermal-0p5deg:license             status=200 type='text/html; charset=UTF-8' bytes=72405
PASS  pds-lro-lend-alds:primary                            status=206 type='application/xml' bytes=4096
PASS  pds-lro-lend-alds:extra0                             status=206 type='application/octet' bytes=1024
PASS  pds-lro-lend-alds:license                            status=200 type='text/html; charset=UTF-8' bytes=72405
PASS  pds-mro-sharad-radargrams:primary                    status=200 type='application/xml' bytes=5303
PASS  pds-mro-sharad-radargrams:extra0                     status=200 type='application/xml' bytes=7630
PASS  pds-mro-sharad-radargrams:extra1                     status=200 type='text/plain' bytes=2152
PASS  pds-mro-sharad-radargrams:license                    status=200 type='text/html; charset=UTF-8' bytes=72405
PASS  pds-mro-crism-trdr:primary                           status=200 type='application/xml' bytes=5815
PASS  pds-mro-crism-trdr:extra0                            status=200 type='text/plain' bytes=9512
PASS  pds-mro-crism-trdr:license                           status=200 type='text/html; charset=UTF-8' bytes=72405
PASS  pds-maven-iuvs:primary                               status=200 type='text/html; charset=UTF-8' bytes=20080
PASS  pds-maven-iuvs:license                               status=200 type='text/html; charset=UTF-8' bytes=72405
PASS  nasa-power-daily-point:primary                       status=200 type='application/json' bytes=745
PASS  nasa-power-daily-point:license                       status=200 type='text/html' bytes=21050
PASS  isric-soilgrids-2-wcs:primary                        status=200 type='text/xml; charset=UTF-8' bytes=2531
PASS  isric-soilgrids-2-wcs:extra1                         status=200 type='text/xml; charset=UTF-8' bytes=2543
PASS  isric-soilgrids-2-wcs:extra2                         status=200 type='text/xml; charset=UTF-8' bytes=2547
PASS  isric-soilgrids-2-wcs:extra3                         status=200 type='image/tiff' bytes=601
PASS  isric-soilgrids-2-wcs:license                        status=200 type='text/html' bytes=34690
PASS  isric-soilgrids-rest-api:license                     status=200 type='text/html' bytes=34690
PASS  planetgsd-1-figshare:primary                         status=200 type='application/json' bytes=5874
PASS  planetgsd-1-figshare:extra0                          status=200 type='application/octet-stream' bytes=36833
PASS  planetgsd-1-figshare:extra1                          status=200 type='text/html' bytes=340549
PASS  planetgsd-1-figshare:license                         status=200 type='application/json' bytes=5874
PASS  zenodo-lunar-regolith-thickness:primary              status=200 type='application/json' bytes=5089
PASS  zenodo-lunar-regolith-thickness:license              status=200 type='application/json' bytes=5089
PASS  usgs-astrogeology-astropedia:primary                 status=200 type='text/html; charset=utf-8' bytes=18873
PASS  usgs-astrogeology-astropedia:license                 status=200 type='text/html; charset=utf-8' bytes=18873
PASS  usgs-lunar-sourcebook-tables:primary                 status=200 type='binary/octet-stream' bytes=6745
PASS  usgs-lunar-sourcebook-tables:license                 status=200 type='text/html; charset=utf-8' bytes=18873
SKIP  isric-soilgrids-2-wcs:extra0                         Large discovery document. It intermittently exceeds the 30 s request timeout, so it is recorded but not part of the regression gate; DescribeCoverage/GetCoverage are the gated checks.
SKIP  isric-soilgrids-2-wcs:extra4                         Optional deeper-depth probe; DescribeCoverage 60-100 is the gated availability check. The 603-byte observed response is recorded, not gated, because this endpoint intermittently exceeds the timeout.
SKIP  isric-soilgrids-rest-api:primary                     Observed timeout after 60 s with 0 bytes on 2026-11-15. Excluded from the regression check; the WCS endpoint is the verified alternative.
----------------------------------------------------------------------------------------------------
SUMMARY pass=40 fail=0 skipped=3
Skipped checks are recorded as unavailable/optional in the catalog and are not regressions.
```

The catalog contains 43 check definitions: 40 gated and 3 non-gated. The three `SKIP` entries
are non-gated catalog checks: the large SoilGrids
`GetCapabilities` discovery document, the optional deeper `60-100 cm` `GetCoverage` probe, and
the SoilGrids REST API timeout. They are recorded with their observed responses but do not fail
the gate. The gated SoilGrids checks are `DescribeCoverage` for `0-5/60-100/100-200 cm` and the
tiny `0-5 cm` `GetCoverage`.

The checker also exits non-zero on a deliberate marker regression. Observed with a temporary
single-check catalog:

```text
FAIL  regression-test:primary                              status=206 type='text/plain' bytes=64
      - missing marker 'THIS_MARKER_MUST_NOT_EXIST'
      url: https://pds-geosciences.wustl.edu/missions/lunarp/reduced/hydrogenhd.txt
SUMMARY pass=0 fail=1 skipped=0
EXIT=1
```

## 7. Reproduction

```bash
# Full source audit (stdlib only; small range/metadata/WCS requests)
python3 scripts/check_d04_d06_sources.py

# Machine-readable catalog
python3 -m json.tool docs/data-sources/d04-d06-water-regolith.json > /dev/null

# Existing D06 release integrity and focused offline tests
uv run pytest pipelines/tests/test_soilgrids_depths.py -q
uv run pytest pipelines/tests/test_regolith_semantics.py -q

# Existing D04 pilot tests
uv run pytest pipelines/tests/test_water_ice_pilot.py -q

# Import the committed SoilGrids depth release into an additive typed-v2 database
uv run python scripts/import_typed_release.py \
  --release pipelines/pilots/d06_earth_soilgrids_depths \
  --database var/d06-depths.db
```

Observed focused offline checks (2026-11-15):

```text
$ uv run pytest pipelines/tests/test_soilgrids_depths.py \
    pipelines/tests/test_regolith_semantics.py \
    pipelines/tests/test_water_ice_pilot.py --tb=short
32 passed, 1 skipped in 0.86s

$ uv run ruff check scripts/check_d04_d06_sources.py
All checks passed!

$ uv run ruff format --check scripts/check_d04_d06_sources.py
1 file already formatted
```

The checker is designed for manual/opt-in use because it reaches public internet endpoints; it
is not part of the default CI gate. It does not download the 4.25 GB Mini-RF image, the 14.3 MB
LP table, the 6.7 MB NS table, the 18.8 MB Zenodo ZIP or a full SoilGrids coverage.

## 8. Limitations and open blockers

- **D04 cannot be promoted to similarity.** The best available Mars product at 0.5° is still
  ~30 km and stores counts/second, not the normalized ratio. LEND, SHARAD and CRISM remain
  blocked for different, documented reasons. MAVEN is atmospheric, not surface ice.
- **D06 Moon/Mars still has no verified physical-property coverage.** PlanetGSD supplies
  particle-size curves, but the inspected S2 lunar table lacks per-sample depth and the S3 Mars
  depth fields are unverified; Zenodo supplies regolith thickness but D06 has no thickness
  feature. Neither is ingested.
- **SoilGrids is model prediction, not measurement.** The five AOIs are not a global survey.
  The 250 m AOI-envelope mean is not a local sample and not a product cell value.
- **Deeper SoilGrids intervals are reachable but not registered.** `60-100` and `100-200 cm`
  exist at ISRIC and respond to `DescribeCoverage`/`GetCoverage`, but the eligibility regex stops
  at `30-60 cm`. Adding them requires a semantics/contract change.
- **USGS licence confirmation is incomplete.** The Astropedia product page does not state a
  licence, and USGS licence pages returned `403` from this environment. Treat USGS products as
  public-domain candidates pending per-product confirmation.
- **NASA PDS licence wording is generic.** NASA's science-data licence page is reachable but does
  not enumerate per-product terms. The repository records NASA-led PDS products as CC0-1.0 by
  precedent; per-product notices should still be checked before redistribution.
- **The SoilGrids WCS metadata does not carry a property-specific physical unit in the
  coverage description.** Unit conversion and scaling must follow the repository's recorded
  method and ISRIC documentation; do not treat raw GeoTIFF values as already-normalized
  physical quantities.
