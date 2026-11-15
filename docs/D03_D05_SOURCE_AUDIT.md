# D03/D05 open-data source audit

Status: **bounded source audit, evidence only.** This audit verifies official D03 (thermal
environment) and D05 (solar illumination and radiation) sources with live HTTP requests, maps
them to the committed typed feature ids, and records a checkpointed path to more verified
coverage. It does **not** add observations, download rasters or kernels in full, or make a global
or cross-body claim.

- Audit date: 2026-11-15
- Base commit: `df6e365` (`audit/c-source-audit`)
- Machine-readable catalog: [`docs/data-sources/d03-d05-thermal-illumination.json`](data-sources/d03-d05-thermal-illumination.json)
- Re-runnable checker: [`scripts/check_d03_d05_sources.py`](../scripts/check_d03_d05_sources.py)
- Evidence scope: 61 committed D03 observations and 28 committed D05 observations

## Bottom line

1. The two committed planetary sources are real and reachable: NASA PDS Diviner PCP (Moon
   brightness temperature) and USGS THEMIS quantitative thermal inertia (Mars). The NAIF SPICE
   generic kernels used for D05 solar geometry are also reachable and unchanged in pinned size.
2. The other Diviner products do **not** extend the existing polar Moon AOIs: GDR L3 stops at
   80 N/S, GHRM stops at 70 N/S, RCP is Lacus Mortis, and PRP is model-derived. Diviner GCP is
   global but 0.5 degree (up to about 55 km in latitude), so it is a separate coarse context,
   not a PCP substitute.
3. TES TIMAP is a real global Mars thermal-inertia map, but at 2.96 km/pixel and from a different
   retrieval than THEMIS 100 m. It cannot be mixed with `thermal_inertia` under the THEMIS method
   version.
4. No checked source supplies observed Moon/Mars surface irradiance, a DEM horizon/shadow mask,
   or an absorbed ionizing dose. TSIS TSI is 1 AU Earth-Sun total irradiance; CRaTER is calibrated
   energy data; LEND is neutron data (D04).
5. Brightness temperature, thermal inertia, solar geometry and irradiance are **not** cross-body
   similarity features without a shared quantity, method, support and time definition. All
   committed D03/D05 rows remain `evidence_only` or `context_only`.

## Existing coverage (measured from the committed pilots)

| Dimension | Total | Areas | By body | Score roles | States | Features |
|---|---:|---:|---|---|---|---|
| D03 | 61 | 28 | Earth 20, Moon 36, Mars 5 | `evidence_only` 41, `context_only` 20 | detected 51, `no_coverage` 7, `unknown` 3 | `surface_brightness_temperature_k` 36; `thermal_inertia` 5; four Earth context fields, 5 each |
| D05 | 28 | 14 | Earth 10, Moon 8, Mars 10 | `context_only` 28 | detected 28 | `solar_incidence_deg` 9; `solar_elevation_deg` 9; shortwave 5; longwave 5 |

D03 feature detail:

| Feature id | Rows | Body | Method version | Actual support | State |
|---|---:|---|---|---|---|
| `surface_brightness_temperature_k` | 36 | Moon | `diviner-pcp-tbol-1.0.0` | 240 m native bins; 0.25 h local-time bins 00:00-00:15 and 12:00-12:15; 1.98 km window mean; multi-year southern-summer composite | 26 detected, 3 unknown, 7 `no_coverage` |
| `thermal_inertia` | 5 | Mars | `themis-apparent-thermal-inertia-1.0.0` | 100 m THEMIS pixels; 1.98 km window mean; upper few centimetres | 5 detected |
| `surface_air_temperature_2m_c` | 5 | Earth | `nasa-power-daily-1.0.0` | about 0.5 x 0.625 degree POWER point; daily 2024-01-01..07 | 5 detected |
| `surface_air_temperature_2m_max_c` | 5 | Earth | `nasa-power-daily-1.0.0` | same coarse POWER grid point | 5 detected |
| `surface_air_temperature_2m_min_c` | 5 | Earth | `nasa-power-daily-1.0.0` | same coarse POWER grid point | 5 detected |
| `surface_skin_temperature_c` | 5 | Earth | `nasa-power-daily-1.0.0` | same coarse POWER grid point | 5 detected |

D05 feature detail:

| Feature id | Rows | Body | Method version | Actual support | State |
|---|---:|---|---|---|---|
| `solar_incidence_deg` | 9 | Moon 4, Mars 5 | `spice-solar-geometry-1.0.0` | body-fixed point site; one instant `2026-11-15T12:00:00Z`; flat ellipsoid; no DEM horizon | 9 detected |
| `solar_elevation_deg` | 9 | Moon 4, Mars 5 | `spice-solar-geometry-1.0.0` | derived as `90 - incidence`; same point/instant support | 9 detected |
| `surface_shortwave_irradiance_kwh_m2_day` | 5 | Earth | `nasa-power-daily-1.0.0` | about 0.5 x 0.625 degree POWER point; daily all-sky surface shortwave | 5 detected |
| `surface_longwave_irradiance_kwh_m2_day` | 5 | Earth | `nasa-power-daily-1.0.0` | about 0.5 x 0.625 degree POWER point; daily all-sky surface longwave | 5 detected |

The following ids are defined in the six-dimension registry but have **zero** committed
observations: `surface_temperature_mean_k`, `diurnal_temperature_range_k`, `ionizing_dose_gy`.
The Earth irradiance ids above are typed pilot observations but are not in the
`FEATURES_V2` registry; that mismatch is recorded as a gap, not silently repaired.

The committed D03 planetary count is 31 detected (26 Moon brightness rows plus 5 Mars TI rows).
`catalog/dimension-source-inventory.yaml` still says "26 detected" and lists an older Diviner
URL; this audit uses the committed observations and the verified PDS4 path. The discrepancy is
documented in the JSON gaps and should be fixed in a separate documentation PR.

## Reachable sources and HTTP evidence

All requests below were made on 2026-11-15. `Status` is the observed HTTP status, and the size is
the full object size when the server supplied `Content-Range` or `Content-Length`. Directory
pages, labels and small XML/JSON were fetched whole (all under 256 KiB); data tables, rasters and
kernels were read with HTTP range requests (at most 4 KiB). No source required credentials.

### D03 — thermal environment

| Source | Exact product / URL | HTTP evidence | Native support / frame | Time support | Existing feature mapping | Verdict |
|---|---|---|---|---|---|---|
| PDS Diviner PCP | [PCP collection](https://pds-geosciences.wustl.edu/lro/urn-nasa-pds-lro_diviner_derived1/data_derived_pcp/); `pcp_avg_tbol_pols_sum_ltim01_240.tab`, `..._ltim49_240.tab` | collection 200 `text/html` 957 B; label 200 `text/plain` 3,637 B; ltim01 206 `text/plain` 212,653,759 B; ltim49 206 `text/plain` 197,367,449 B | 240 m; south polar stereographic, planetocentric, positive east, R=1737.4 km; 1.98 km window mean | 2009-07-05T16:50:26.195Z to 2019-02-17; 0.25 h local-time bins; southern-summer composite | `surface_brightness_temperature_k` exact (`diviner-pcp-tbol-1.0.0`) | **verified existing source** |
| PDS Diviner GCP | [GCP 90S-80S label](https://pds-geosciences.wustl.edu/lro/urn-nasa-pds-lro_diviner_derived1/data_derived_gcp/global_cumul_avg_cyl_90s80s_002.lbl), [table](https://pds-geosciences.wustl.edu/lro/urn-nasa-pds-lro_diviner_derived1/data_derived_gcp/global_cumul_avg_cyl_90s80s_002.tab) | label 200 `text/plain` 3,413 B; table 206 `text/plain` 156,211,313 B | 0.5 degree bins (up to about 55 km in latitude); simple cylindrical, planetocentric | 2009-07-05 to 2015-04-01; 0.25 h local-time bins | `surface_brightness_temperature_k` but **method/support mismatch** | verified coarse candidate; do not mix with PCP |
| PDS Diviner GDR L3 | [regolith-temperature label](https://pds-geosciences.wustl.edu/lro/urn-nasa-pds-lro_diviner_derived1/data_derived_gdr_l3/cylindrical/img/dgdr_st_avg_cyl_128_img.lbl) | 200 `text/plain` 4,700 B | 128 ppd = 236.901 m; 80 N to 80 S; simple cylindrical, DE421 mean Earth/polar axis | 2009-07-05 to 2016-10-10; nighttime 19:30-05:30 | registry `surface_temperature_mean_k`, but model-adjusted nighttime temperature | verified; excludes every existing polar AOI |
| PDS Diviner GHRM | [midnight bolometric XML](https://pds-geosciences.wustl.edu/lro/urn-nasa-pds-lro_diviner_derived1/data_derived_ghrm/geotiff/dghrm_tbol_m_70s70n_tif.xml) | 200 `application/xml` 8,338 B | 236.901 m; 70 N to 70 S; simple cylindrical | 2009-07-05 to 2022-08-01; midnight mosaic | `surface_brightness_temperature_k` but **method/support mismatch** | verified; excludes polar AOIs |
| PDS Diviner RCP | [Lacus Mortis XML](https://pds-geosciences.wustl.edu/lro/urn-nasa-pds-lro_diviner_derived1/data_derived_rcp/region_lacus_mortis_cumul_avg_cyl_42n023e_128.xml) | 200 `application/xml` 14,463 B | 128 ppd = 236.901 m; Lacus Mortis about 42 N, 23 E | 2009-07-05 to 2021-03-01 | `surface_brightness_temperature_k` but different regional composite | verified; out of scope for current AOIs |
| PDS Diviner PRP | [south polar label](https://pds-geosciences.wustl.edu/lro/urn-nasa-pds-lro_diviner_derived1/data_derived_prp/dlre_prp_south.lbl) | 200 `text/plain` 2,167 B | polar model grid, Kaguya DEM; annual average/max temperature and ice depth | model fit; no observational time support | registry `surface_temperature_mean_k`; D04 ice-depth candidate | verified model product; not observed evidence |
| USGS THEMIS TI | [product page](https://astrogeology.usgs.gov/search/map/themis_thermal_inertia_mosaic_quantitative_00n060e_100mpp); [ISIS cube](https://asc-astropedia.s3.us-west-2.amazonaws.com/Mars/Odyssey/THEMIS-Global-Thermal-Inertia-Mosaic/Quantitative-32-Bit/THEMIS_TI_Mosaic_Quant_00N060E_100mpp.cub) | page 200 `text/html` 27,670 B; cube 206 `binary/octet-stream` 2,532,510,192 B | 100 m; equirectangular R=3396190 m; upper few centimetres | time-independent retrieval; mosaic does not carry per-pixel acquisition time | `thermal_inertia` exact (`themis-apparent-thermal-inertia-1.0.0`) | **verified existing source** |
| PDS TES TIMAP | [day-map label](https://pds-geosciences.wustl.edu/mgs/mgs-m-tes-5-timap-v1/mgst_9001/data/global_ti_day_2007.lbl) | 200 `text/plain` 2,408 B | 20 ppd = 2.96 km; simple cylindrical, planetocentric, R=3396 km; 16-bit | 1999-02 to 2004-04; separate day/night maps | `thermal_inertia` but **method/support mismatch** | verified coarse candidate; do not mix with THEMIS |
| NASA POWER | [daily point API](https://power.larc.nasa.gov/api/temporal/daily/point?parameters=ALLSKY_SFC_SW_DWN,ALLSKY_SFC_LW_DWN,T2M&community=RE&longitude=-68.4&latitude=-23.5&start=20240101&end=20240102&format=JSON) | 200 `application/json` 831 B | about 0.5 x 0.625 degree Earth grid point | daily 2024-01-01..07 in the pilot | Earth D03 context fields exact | **verified existing context source** |

### D05 — solar illumination and radiation

| Source | Exact product / URL | HTTP evidence | Native support / frame | Time support | Existing feature mapping | Verdict |
|---|---|---|---|---|---|---|
| NAIF SPICE generic kernels | [LSK](https://naif.jpl.nasa.gov/pub/naif/generic_kernels/lsk/naif0012.tls), [PCK](https://naif.jpl.nasa.gov/pub/naif/generic_kernels/pck/pck00011.tpc), [lunar PA](https://naif.jpl.nasa.gov/pub/naif/generic_kernels/pck/moon_pa_de421_1900-2050.bpc), [lunar FK](https://naif.jpl.nasa.gov/pub/naif/generic_kernels/fk/satellites/moon_080317.tf), [DE421](https://naif.jpl.nasa.gov/pub/naif/generic_kernels/spk/planets/a_old_versions/de421.bsp) | 206 `text/plain` 5,257 B; 206 `text/plain` 131,226 B; 206 `<absent>` 1,770,496 B; 206 `text/plain` 21,437 B; 206 `model/vnd.valve.source.compiled-map` 16,790,528 B | point site; `MOON_ME` 31001 and `IAU_MARS` 10014; DE421 1900-2050 | pilot instant `2026-11-15T12:00:00Z`; no time series | `solar_incidence_deg`, `solar_elevation_deg` exact (`spice-solar-geometry-1.0.0`) | **verified existing source** |
| LASP TSIS-1 TIM | [24 h TSI latest](https://lasp.colorado.edu/data/tsis/tsi_data/tsis_tsi_L3_c24h_latest.txt) | 206 `text/plain` 2,048 B read; 488,641 B total at check time (latest file is dynamic) | disk-integrated TSI at 1 AU; no spatial footprint | 24 h cadence; 2018-01-10 to 2026-11-10 in the checked file | no existing feature (`total_solar_irradiance_w_m2` absent) | verified; not mappable without a new feature |
| PDS LRO CRaTER | [dataset catalog](https://pds-ppi.igpp.ucla.edu/data/LRO-L-CRAT-3-CDR-CALIBRATED-V1.0/CATALOG/LRO_CRAT_L1_DS.CAT) | 200 `application/vnd.ms-pki.seccat` 39,895 B | detector energy deposits; spacecraft/detector frame, not a surface grid | 2009-06-29 to 2026-06-30 | registry `ionizing_dose_gy`; no direct Gy product | verified; dose feature blocked |
| PDS LRO LEND | [RDR catalog](https://pds-geosciences.wustl.edu/lro/lro-l-lend-2-edr-v1/lrolen_0xxx/catalog/rdr_ds.cat) | 200 `text/plain` 22,880 B | neutron counts/spectra; polar orbit | mission time series to 2026 | D04 only; no D03/D05 feature | verified; out of scope (D04) |

Services checked:

| Service | URL | HTTP evidence | Use |
|---|---|---|---|
| PDS Geosciences Node | <https://pds-geosciences.wustl.edu/> | 200 `text/html` 23,177 B | archive entry point for Diviner, LEND and TES |
| PDS Geosciences ODE REST | <https://oderest.rsl.wustl.edu/live2/?query=product&ihid=LRO&iid=DLRE&pt=RDR&results=json&limit=2> | 200 `text/xml` 442 B | discovery of LRO/DLRE product ids; not observation evidence |

Licence/attribution summary: NASA-led PDS and NAIF data are recorded as CC0-1.0 under the
[NASA Science Data Portal licence policy](https://science.data.nasa.gov/about/license), with
citation requested; the Diviner bundle DOI is `10.17189/wj0s-w188`. USGS THEMIS is public domain
under the product page's Access Constraints. NASA POWER is free and open with citation requested.
TSIS has no explicit licence statement on the checked page, so redistribution is recorded as
unknown rather than assumed. LEND and CRaTER inherit the NASA-led PDS policy.

## Feature mapping and the method/support gap

| Existing feature | Committed value / method | Actual source and support | What it measures | Why it cannot be a cross-body similarity feature |
|---|---|---|---|---|
| `surface_brightness_temperature_k` | 36 Moon rows, `diviner-pcp-tbol-1.0.0` | Diviner PCP, 240 m, 0.25 h local-time bins, multi-year composite | Bolometric brightness temperature with unit-emissivity assumption | Depends on band/emissivity and local time/season; not kinetic temperature; no Earth/Mars product with the same method and support |
| `thermal_inertia` | 5 Mars rows, `themis-apparent-thermal-inertia-1.0.0` | THEMIS quantitative mosaic, 100 m, upper few cm | Apparent thermal inertia from a thermal retrieval | Retrieval model, instrument, season and support differ from TES and from any Earth thermal-inertia derivation; no shared method |
| `surface_temperature_mean_k` | 0 committed rows; registry only | GDR L3 is a nighttime model-adjusted regolith temperature; PRP is an annual model fit | Not a mean of matched surface-temperature samples | No source supplies a mean over matched time/emissivity support; model outputs are not observations |
| `diurnal_temperature_range_k` | 0 committed rows; registry only | PCP bins are multi-year composites; GCP is 0.5 degree and 2009-2015 | Not a same-sol day/night pair | The two PCP bins are different samples, not a same-sol pair; differencing them is forbidden by the builder and this audit |
| Earth D03 fields (`surface_air_temperature_2m_c`, max/min, skin) | 20 rows, context only | POWER daily reanalysis, about 0.5 x 0.625 degree | 2 m air temperature or skin temperature | Different quantity and 50 km support; not a local surface measurement and not comparable to Diviner/THEMIS |
| `solar_incidence_deg` / `solar_elevation_deg` | 18 rows, context only | SPICE geometry, body-fixed point, one instant | Geometric Sun angle on a flat ellipsoid | Geometry is not irradiance, shadow fraction or radiance; no DEM horizon or observer height; one instant is not a time series |
| Earth D05 fields (shortwave/longwave) | 10 rows, context only | POWER daily reanalysis, about 0.5 x 0.625 degree | All-sky surface broadband irradiance | Daily, 50 km, atmospheric model; no Moon/Mars counterpart and not comparable to a geometric Sun angle |
| `ionizing_dose_gy` | 0 committed rows; registry only | CRaTER calibrated energy; LEND neutrons | Not absorbed dose in Gy | No direct dose product; unfolding and shielding physics are missing; LEND is D04 neutron data |

### Why brightness temperature, thermal inertia, solar geometry and irradiance are not cross-body similarity features

- **Quantity.** `surface_brightness_temperature_k` is a radiometric brightness temperature under
  a unit-emissivity assumption. It is not kinetic surface temperature, not Earth 2 m air
  temperature, and not the thermal-inertia retrieval. `thermal_inertia` is an apparent retrieval,
  not a temperature. Solar incidence/elevation are angles, not fluxes. Irradiance is a flux with a
  spectral and atmospheric definition.
- **Method.** Diviner PCP and GCP use different cumulative products and epochs. THEMIS and TES use
  different instruments, thermal models and resolutions. SPICE is a geometry model, POWER is a
  reanalysis, and TSIS is a 1 AU total-irradiance measurement. No pair shares a method version.
- **Support.** The committed supports are 240 m PCP bins, 100 m THEMIS pixels, 0.5 degree GCP,
  2.96 km TES, about 50 km POWER, a point for SPICE and 1 AU for TSIS. A coarser source cannot be
  upsampled into same-support evidence, and a point cannot be treated as a 1.98 km window mean.
- **Time.** PCP is a multi-year composite at one local-time bin; THEMIS TI is treated as
  time-independent; POWER is daily; SPICE is one instant; TSIS is daily at 1 AU. There is no
  matched same-sol or same-observation-time pair.
- **Role enforcement.** `domain/scoring.py` accepts only `FEATURE_BY_ID` from
  `domain/features.py`, which contains terrain features. All D03/D05 rows are `evidence_only` or
  `context_only`; `cross_body_gate.py` refuses to score without an explicit `comparable` decision
  and matching quantity/method/support/unit/frame. No such decision exists for D03/D05.

## Eligibility implementation note

The audit brief referenced `apps/api/src/analog_api/domain/eligibility.py`. At base commit
`df6e365` that file does **not** exist (`git ls-tree -r HEAD` and a repository-wide `find` return
no match). The active rules live in:

- `pipelines/src/analog_pipelines/dimensions.py` — D03/D05 feature ids, units, `score_role`,
  `value_kind`, method versions, body scope and source requirements.
- `pipelines/src/analog_pipelines/ontology.py` — `compare_eligibility` vocabulary:
  `same_method_and_support`, `context_only`, `evidence_only`, `not_comparable`.
- `pipelines/src/analog_pipelines/cross_body_gate.py` — quantity, method, support, unit, frame and
  explicit decision checks; it refuses to rank when any is missing or mismatched.
- `apps/api/src/analog_api/domain/scoring.py` — production similarity scoring over terrain
  `FEATURE_BY_ID`; D03/D05 ids are not admitted.

This is recorded as a documentation gap so a future refactor can add the path deliberately; this
audit does not invent an API or create a second eligibility implementation.

## Bounded, checkpointed expansion plan

Every phase below is opt-in, bounded and stops at a recorded checkpoint. No phase permits a
global survey, a cross-body score or a zero-filled value.

| Phase | Action | Bounded request | Checkpoint before continuing | Status |
|---|---|---|---|---|
| P0 | Freeze current evidence; re-run the 24-probe checker | about 120 kB of metadata/headers | 24/24 pass; JSON valid; no full raster/kernel | **done in this audit** |
| P1 | Add Diviner GCP 0.5 degree as separate coarse Moon context | one 156,211,313 B ASCII table, opt-in | cell overlap >=1; 2009-2015; separate method version; never mixed with PCP | proposed |
| P2 | Add TES TIMAP 2.96 km as separate coarse Mars context | 51,840,000 B day + 25,920,000 B night + masks | interpolation mask applied; method version distinct from THEMIS; no 100 m mixing | proposed |
| P3 | Extend THEMIS 100 m to at most 3 new Mars windows inside 60 N/S | about 0.3-1.3 MB of ISIS tile blocks per window | >=1 native 100 m pixel per window; tile checksum and byte ranges recorded | proposed after review |
| P4 | Compute a bounded SPICE time series at the existing 9 sites | reuse the 18,718,944 B pinned kernel cache; no new download if valid | 24-25 hourly samples per site; flat-ellipsoid/no-horizon caveats retained; no irradiance | proposed, lowest risk |
| P5 | Add Earth-only TSIS TSI context | about 0.5 MB latest text file, or a range for metadata | new `total_solar_irradiance_w_m2` feature approved; never mapped to surface shortwave | deferred (schema change) |
| P6 | Attempt a CRaTER dose product | metadata only until a documented dose product exists | blocked: no direct Gy; no Sv conversion without physics | blocked |
| P7 | Use GDR/GHRM/RCP for new lower-latitude Moon AOIs | bounded image/tile reads; no full mosaic | new AOI/science question approved; method version separate from PCP | deferred (new scope) |
| P8 | Use ODE only for discovery | small XML responses | every ODE hit re-verified against its PDS label and HTTP evidence | available |

Stop rules: if a source does not overlap the AOI, stop; if the native support is coarser than
1.98 km, store it as a separate coarse context and do not mix it; if the licence is not explicit,
record it as unverified and do not publish it as open; if a value would require a physical model
that is not documented, leave it missing rather than zero-filling.

## Reproduction and observed checker output

```bash
python3 scripts/check_d03_d05_sources.py
python3 scripts/check_d03_d05_sources.py --json
```

Observed on 2026-11-15 (exit code 0):

```text
D03/D05 open-data source checker
bounded requests only: full bodies <=256 KiB; ranges <=4 KiB

PASS  pds-diviner-pcp-collection  status=200 type=text/html read=957 total=957
      GET https://pds-geosciences.wustl.edu/lro/urn-nasa-pds-lro_diviner_derived1/data_derived_pcp/
PASS  pds-diviner-pcp-ltim01-label  status=200 type=text/plain read=3637 total=3637
      GET https://pds-geosciences.wustl.edu/lro/urn-nasa-pds-lro_diviner_derived1/data_derived_pcp/diurnal/ltim/pols/pcp_avg_tbol_pols_sum_ltim01_240.lbl
PASS  pds-diviner-pcp-ltim01-data  status=206 type=text/plain read=118 total=212653759 range=bytes=0-117
      GET https://pds-geosciences.wustl.edu/lro/urn-nasa-pds-lro_diviner_derived1/data_derived_pcp/diurnal/ltim/pols/pcp_avg_tbol_pols_sum_ltim01_240.tab
PASS  pds-diviner-pcp-ltim49-data  status=206 type=text/plain read=118 total=197367449 range=bytes=0-117
      GET https://pds-geosciences.wustl.edu/lro/urn-nasa-pds-lro_diviner_derived1/data_derived_pcp/diurnal/ltim/pols/pcp_avg_tbol_pols_sum_ltim49_240.tab
PASS  pds-diviner-gcp-90s80s-label  status=200 type=text/plain read=3413 total=3413
      GET https://pds-geosciences.wustl.edu/lro/urn-nasa-pds-lro_diviner_derived1/data_derived_gcp/global_cumul_avg_cyl_90s80s_002.lbl
PASS  pds-diviner-gcp-90s80s-data  status=206 type=text/plain read=226 total=156211313 range=bytes=0-225
      GET https://pds-geosciences.wustl.edu/lro/urn-nasa-pds-lro_diviner_derived1/data_derived_gcp/global_cumul_avg_cyl_90s80s_002.tab
PASS  pds-diviner-gdr-l3-st-label  status=200 type=text/plain read=4700 total=4700
      GET https://pds-geosciences.wustl.edu/lro/urn-nasa-pds-lro_diviner_derived1/data_derived_gdr_l3/cylindrical/img/dgdr_st_avg_cyl_128_img.lbl
PASS  pds-diviner-ghrm-tbol-xml  status=200 type=application/xml read=8338 total=8338
      GET https://pds-geosciences.wustl.edu/lro/urn-nasa-pds-lro_diviner_derived1/data_derived_ghrm/geotiff/dghrm_tbol_m_70s70n_tif.xml
PASS  pds-diviner-rcp-lacus-mortis-xml  status=200 type=application/xml read=14463 total=14463
      GET https://pds-geosciences.wustl.edu/lro/urn-nasa-pds-lro_diviner_derived1/data_derived_rcp/region_lacus_mortis_cumul_avg_cyl_42n023e_128.xml
PASS  pds-diviner-prp-south-label  status=200 type=text/plain read=2167 total=2167
      GET https://pds-geosciences.wustl.edu/lro/urn-nasa-pds-lro_diviner_derived1/data_derived_prp/dlre_prp_south.lbl
PASS  usgs-themis-ti-product-page  status=200 type=text/html read=27670
      GET https://astrogeology.usgs.gov/search/map/themis_thermal_inertia_mosaic_quantitative_00n060e_100mpp
PASS  usgs-themis-ti-cub  status=206 type=binary/octet-stream read=1024 total=2532510192 range=bytes=0-1023
      GET https://asc-astropedia.s3.us-west-2.amazonaws.com/Mars/Odyssey/THEMIS-Global-Thermal-Inertia-Mosaic/Quantitative-32-Bit/THEMIS_TI_Mosaic_Quant_00N060E_100mpp.cub
PASS  pds-tes-timap-label  status=200 type=text/plain read=2408 total=2408
      GET https://pds-geosciences.wustl.edu/mgs/mgs-m-tes-5-timap-v1/mgst_9001/data/global_ti_day_2007.lbl
PASS  naif-lsk  status=206 type=text/plain read=512 total=5257 range=bytes=0-511
      GET https://naif.jpl.nasa.gov/pub/naif/generic_kernels/lsk/naif0012.tls
PASS  naif-pck  status=206 type=text/plain read=512 total=131226 range=bytes=0-511
      GET https://naif.jpl.nasa.gov/pub/naif/generic_kernels/pck/pck00011.tpc
PASS  naif-moon-pa  status=206 type=<absent> read=512 total=1770496 range=bytes=0-511
      GET https://naif.jpl.nasa.gov/pub/naif/generic_kernels/pck/moon_pa_de421_1900-2050.bpc
PASS  naif-moon-fk  status=206 type=text/plain read=512 total=21437 range=bytes=0-511
      GET https://naif.jpl.nasa.gov/pub/naif/generic_kernels/fk/satellites/moon_080317.tf
PASS  naif-de421  status=206 type=model/vnd.valve.source.compiled-map read=512 total=16790528 range=bytes=0-511
      GET https://naif.jpl.nasa.gov/pub/naif/generic_kernels/spk/planets/a_old_versions/de421.bsp
PASS  nasa-power-daily-point  status=200 type=application/json read=831 total=831
      GET https://power.larc.nasa.gov/api/temporal/daily/point?parameters=ALLSKY_SFC_SW_DWN,ALLSKY_SFC_LW_DWN,T2M&community=RE&longitude=-68.4&latitude=-23.5&start=20240101&end=20240102&format=JSON
PASS  lasp-tsis-tsi-24h  status=206 type=text/plain read=2048 total=488641 range=bytes=0-2047
      GET https://lasp.colorado.edu/data/tsis/tsi_data/tsis_tsi_L3_c24h_latest.txt
PASS  pds-lro-crater-catalog  status=200 type=application/vnd.ms-pki.seccat read=39895 total=39895
      GET https://pds-ppi.igpp.ucla.edu/data/LRO-L-CRAT-3-CDR-CALIBRATED-V1.0/CATALOG/LRO_CRAT_L1_DS.CAT
PASS  pds-lro-lend-rdr-catalog  status=200 type=text/plain read=22880 total=22880
      GET https://pds-geosciences.wustl.edu/lro/lro-l-lend-2-edr-v1/lrolen_0xxx/catalog/rdr_ds.cat
PASS  pds-ode-product-query  status=200 type=text/xml read=442
      GET https://oderest.rsl.wustl.edu/live2/?query=product&ihid=LRO&iid=DLRE&pt=RDR&results=json&limit=2
PASS  pds-geosciences-node  status=200 type=text/html read=23177 total=23177
      GET https://pds-geosciences.wustl.edu/

summary: 24 passed, 0 failed, 24 probes
scope: metadata/header evidence only; no raster or kernel was downloaded in full
```

The checker exits non-zero if a status, content-type, pinned size or body marker regresses. The
`<absent>` content type for the binary lunar PA kernel is the server's actual header behaviour, not
a missing check: the 206 status, 1,770,496-byte total and `DAF/PCK` marker are verified.

## Limits and non-claims

- This is a **source audit plus bounded pilot coverage**, not a global thermal or illumination
  survey. It covers the 18 Moon reference windows, 5 Mars windows, 4 Moon point sites and 5 Mars
  point sites that already exist in the repository.
- The Moon PCP values are multi-year composites at two local-time bins; they are not a same-sol
  diurnal pair, not kinetic temperature, and carry no per-bin uncertainty.
- The Mars THEMIS values are apparent thermal inertia with about 20% absolute accuracy and no
  per-pixel QA; the product stops at 60 N/S.
- D05 SPICE values are model-derived geometry at one instant on a flat ellipsoid. They are not
  observed irradiance, shadow fraction, horizon visibility or ionizing radiation.
- No source checked supports a cross-body D03 or D05 similarity score. All such values remain
  evidence/context only; `scoring.py` continues to score terrain features only.
- No raw raster, PCP table, THEMIS tile, TES image or SPICE kernel is committed. The checker and
  this audit use only metadata and bounded HTTP ranges.
