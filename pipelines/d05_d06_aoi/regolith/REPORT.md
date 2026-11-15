# D06 Moon/Mars regolith AOI release (#170 Goal F)

- Release version: `d06-regolith-aoi-release-1.0.0`
- Scope: `d06-regolith-aoi-170`
- Generated at: `2026-11-15T12:00:00+00:00`
- Observations: 12 (6 detected, 6 no_coverage)
- Global claim: **false**. Bounded measurement-site AOIs only.

## AOIs

| area_id | body | site | bbox (planetary frame) | CRS |
|---|---|---|---|---|
| `moon-apollo15-station8-regolith-170` | Moon | Apollo 15 Station 8 LSRP trench (Hadley-Apennine) | W 3.6293, S 26.1336, E 3.6303, N 26.1346 | `IAU_2015:30100` |
| `mars-insight-hp3-regolith-170` | Mars | InSight HP3/mole site, Elysium Planitia | W 135.623, S 4.5015, E 135.624, N 4.5025 | `IAU_2015:49900` |

## Returned values and explicit no-coverage

| area_id | feature_id | state | value | unit | depth | evidence class | source |
|---|---|---|---:|---|---|---|---|
| `mars-insight-hp3-regolith-170` | `bearing_strength_kpa` | no_coverage | — | kPa | unknown | `no_coverage` | `delage-2024-insight-soil` |
| `mars-insight-hp3-regolith-170` | `bulk_density_kg_m3` | detected | 1491 | kg/m^3 | 0-0.4 m | `model_derived_from_thermal_conductivity` | `delage-2024-insight-soil` |
| `mars-insight-hp3-regolith-170` | `cohesion_kpa` | detected | 5.8 | kPa | 0-0.07 m | `derived_from_in_situ_scoop_push` | `delage-2024-insight-soil` |
| `mars-insight-hp3-regolith-170` | `friction_angle_deg` | no_coverage | — | degree | unknown | `no_coverage` | `delage-2024-insight-soil` |
| `mars-insight-hp3-regolith-170` | `particle_size_distribution` | no_coverage | — | fraction | unknown | `no_coverage` | `delage-2024-insight-soil` |
| `mars-insight-hp3-regolith-170` | `porosity_fraction` | no_coverage | — | fraction | unknown | `no_coverage` | `delage-2024-insight-soil` |
| `moon-apollo15-station8-regolith-170` | `bearing_strength_kpa` | no_coverage | — | kPa | unknown | `no_coverage` | `nasa-pds-a15lsrp-soil-mechanics` |
| `moon-apollo15-station8-regolith-170` | `bulk_density_kg_m3` | detected | 1970 | kg/m^3 | 0-0.2 m | `derived_from_in_situ_penetration` | `nasa-sp-289-a15-psr-ch7` |
| `moon-apollo15-station8-regolith-170` | `cohesion_kpa` | detected | 1 | kPa | 0-0.36 m | `derived_from_in_situ_trench_failure` | `nasa-sp-289-a15-psr-ch7` |
| `moon-apollo15-station8-regolith-170` | `friction_angle_deg` | detected | 49.5 | degree | 0-0.2 m | `derived_from_in_situ_penetration` | `nasa-sp-289-a15-psr-ch7` |
| `moon-apollo15-station8-regolith-170` | `particle_size_distribution` | no_coverage | — | fraction | unknown | `no_coverage` | `nasa-pds-a15lsrp-soil-mechanics` |
| `moon-apollo15-station8-regolith-170` | `porosity_fraction` | detected | 0.365 | fraction | 0-0.2 m | `derived_from_in_situ_penetration` | `nasa-sp-289-a15-psr-ch7` |

## Source versions and QA

| dataset_id | body | version | licence | footprint | depth support | QA |
|---|---|---|---|---|---|---|
| `nasa-pds-a15lsrp-soil-mechanics` | Moon | PDS4 bundle version 1.0; information model 1.23.0.0 | NASA PDS public archive; no explicit licence field in the PDS4 bundle label; cite the bundle DOI and NASA SP-289 | Apollo 15 Station 8 / ALSEP area, Hadley-Apennine, Moon (26.1341 N, 3.6298 E) | LSRP penetration and trench records to ~40 cm; PSR Table 7-IV density/porosity/friction estimates for the upper 10-20 cm | PDS4 bundle/collection labels, MD5 manifest, original microfilm-derived tables and PSR documentation |
| `nasa-sp-289-a15-psr-ch7` | Moon | NASA SP-289 (1972); PDS document a15_psr_ch7_sm.pdf | NASA public domain report; PDS public archive copy | Apollo 15 Station 8 / ALSEP area, Hadley-Apennine, Moon | Table 7-IV estimates for the upper 10-20 cm; trench exposure ~36-41 cm | Official NASA report; PDS-scanned copy SHA-256 recorded. A text-layer OCR copy is used for reading and cross-checked against the Gasteiner CSV. |
| `gasteiner-lunar-regolith-database` | Moon | git commit d01f671b6ff7403deaee010209f82f962e0c61e4; arXiv:2602.03829 | CC BY 4.0 | Multiple lunar missions/sites, including Apollo 15 Station 8 | Per-record depth column; Apollo 15 penetrometer row 0-20 cm | Peer-reviewed compilation with per-record source, year and DOI/URL; used only as a cross-check in this pass |
| `nasa-pds-insight-hp3-tem` | Mars | PDS4 bundle version 1.0; information model 1.11.0.0 | NASA PDS public archive; no explicit licence field in the PDS4 bundle label; cite the bundle DOI and HP3/RAD team | InSight lander/HP3 site, Elysium Planitia, Mars (4.502 N, 135.623 E) | HP3 TEM-A thermal-conductivity measurements after mole burial; derived average over the top ~40 cm | PDS4 bundle/collection labels, MD5 manifest, calibrated products and release notes |
| `delage-2024-insight-soil` | Mars | Soils and Rocks 47(3):e2024005023 (2024) | CC BY 4.0 | InSight lander/HP3 site, Elysium Planitia, Mars (4.502 N, 135.623 E) | Top ~40 cm for density; 0-7 cm mole-pit depth for scoop-push cohesion; 5.3 MPa penetration resistance in sand/gravel below ~31 cm | Peer-reviewed open-access synthesis with cited HP3/SEIS/IDA measurements, stated assumptions and uncertainties |
| `nasa-pds-msl-apxs-supplement` | Mars | PDS4 bundle version 1.1; information model 1.18.0.0 | NASA PDS public archive; cite the bundle DOI and Berger et al. (2022) | Gale Crater, Mars; target Gobabeb at Bagnold dunes (local site coordinates in the CSV) | Surface scoop sample; APXS samples the upper tens of micrometres | PDS4 bundle/collection labels, CSV field definitions, per-oxide uncertainties and fit type; target quality field blank for the selected row |
| `usgs-themis-quantitative-ti` | Mars | edition 1 (2014-05-30) | Public domain (USGS Astrogeology); cite Fergason et al. (2006) | Mars, quantitative mosaic; existing D03 Jezero windows are inside the 00N060E tile | Upper few centimetres; no single fixed depth interval | Existing D03 pilot verification (docs/D03_PLANETARY_THERMAL_PILOT.md); absolute accuracy ~20%, no per-pixel uncertainty layer |

## What is deliberately not done

- No DEM roughness, slope, land-cover, thermal-inertia or Earth-SoilGrids transfer.
- No penetration resistance relabeled as bearing strength.
- No density-to-porosity conversion without a stated particle density and model.
- No literature grain-size scalar converted into a particle-size distribution.
- No chemistry or thickness ingested because no registered D06 feature exists.
- No global or exhaustive coverage claim.

## Blockers

- Moon PSD: no machine-readable bin/fraction table with basis and depth for the Station 8 AOI.
- Moon bearing strength: only cone penetration resistance is reported; no bearing-capacity conversion applied.
- Mars PSD: no measured site-specific distribution; literature grain-size scalar not converted.
- Mars porosity: no direct porosity; no density-to-porosity conversion without particle density and model.
- Mars friction angle: only an assumed 30 deg model input, not a measurement.
- Mars bearing strength: only mole penetration resistance, not bearing strength.
- Regolith thickness: feature not registered in D06 FEATURES_V2; shared change via #141.
- Soil chemistry: no D06 chemistry feature; APXS values are audit-only.

## Reproduce

```bash
python3 pipelines/d05_d06_aoi/regolith/build_release.py --check
uv run pytest pipelines/tests/test_d06_aoi_170.py -q
uv run python scripts/import_typed_release.py --release pipelines/d05_d06_aoi/regolith --database var/analog.db
```

See `docs/D06_AOI_170.md` for the full scientific audit and limitations.
