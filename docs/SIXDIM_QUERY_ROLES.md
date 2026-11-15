# Six-dimension query roles (D01–D06)

Source of truth: the live registry returned by `GET /api/v1/search/features`
(`registry_version = feature-eligibility-1.0.0`), built by
`apps/api/src/analog_api/domain/eligibility.py::build_registry` from the committed fine Earth index
and the committed typed-v2 pilots/bundles. This matrix is not inferred from dimension codes: a role
is only listed when `roles.<role>.supported` is true for that exact feature id.

Reproduce the underlying counts:

```bash
ANALOG_SEED_INCLUDE_TYPED=true uv run python -m analog_api.cli seed
uv run python -m uvicorn analog_api.main:app --app-dir apps/api/src
curl -s localhost:8000/api/v1/search/features | python -m json.tool | head
```

Seeded state at the time of writing: 104 registered features, 25 scoreable (5 D01 + 20 Earth D06
means), 25 filterable, 104 evidence, 8 context, 74 uncertainty; 113 fine Earth candidates; typed
join `typed_areas.area_id == candidates.location_id` (exact string, no proximity merge).

## Legend

| Mark | Query role | Meaning |
| --- | --- | --- |
| **S** | `similarity` | May be posted as a weighted target; contributes to `distance`. |
| **F** | `hard_filter` | May be posted in `filters`; inclusive bounds inside the registry domain. |
| **E** | `evidence` | May be returned in `evidence_records`; never enters `distance`. |
| **C** | `context` | Environment/annotation context only; never enters `distance`. |
| **U** | `uncertainty` | Quantile/quality/variance evidence; never an independent observation. |
| **—** | not comparable | Similarity and hard-filter are both disabled; the feature must not be scored or filtered. |

`category_match` is disabled for every feature (no versioned taxonomy/crosswalk), so it is omitted
from the tables. Earth land-cover context (`landcover_*_fraction`) is a separate Earth-only context
namespace, not dimension D07 and never a similarity input.

## D01 — Terrain and landforms (5/5 scoreable)

Bodies: Earth, Moon, Mars. Gate: same `terrain-metrics-1.0.0` / `flat-terrain-fraction-1.0.0`
`method_version`, projected metric CRS, 90 m analysis grid, fixed 1.98 km window, finite value.
Moon/Mars scopes exist, but the fine candidate index this endpoint ranks is Earth-only.

| Feature | S | F | E | C | U | Why it may score | Source |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `slope_mean_deg` | S | F | E | — | — | Same registered terrain method, 90 m grid, finite value | Copernicus DEM GLO-90 / `copernicus-dem-glo90` |
| `slope_p90_deg` | S | F | E | — | — | Same as above; steep-tail percentile | Copernicus DEM GLO-90 / `copernicus-dem-glo90` |
| `terrain_relief_p95_p05_m` | S | F | E | — | — | Same as above; p95−p05 relief | Copernicus DEM GLO-90 / `copernicus-dem-glo90` |
| `roughness_std_m` | S | F | E | — | — | Same as above; detrended residual std dev | Copernicus DEM GLO-90 / `copernicus-dem-glo90` |
| `flat_terrain_fraction` | S | F | E | — | — | Same 90 m Horn grid; fraction is a terrain-shape metric | Copernicus DEM GLO-90 / `copernicus-dem-glo90` |

Terrain-shape similarity does not imply environmental equivalence.

## D02 — Geology and mineralogy (0/15 scoreable)

All rows are **not comparable** for score/filter: the pilots carry dataset-specific map-unit text,
single-provider spectral indices, or detection/availability states; no versioned taxonomy crosswalk
and no validated same-quantity/support counterpart exists. Stored `score_role` stays
`evidence_only`; the registry exposes E/U only.

| Feature | Body | S | F | E | C | U | Gate / why not a score | Source |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `geologic_unit` | Mars/Moon | — | — | E | — | — | Free-text map-unit names; no versioned taxonomy crosswalk | USGS Jezero SIM 3464 / USGS Moon geology v2 |
| `geologic_process` | Mars/Moon | — | — | E | — | — | Descriptive process evidence; no numeric/categorical crosswalk | USGS Jezero SIM 3464 / USGS Moon geology v2 |
| `geology_analysis_availability` | Earth/Moon | — | — | E | — | U | Coverage/availability (`unknown`/`no_coverage`), not a mineral measurement | EMIT L2B MIN, GLIM, lunar polar minerals bundle |
| `mineral_carbonates` | Mars | — | — | E | — | U | Single-provider CRISM index; support not validated against the fine pool | `crism-mtrdr-context` |
| `mineral_clay_minerals` | Mars | — | — | E | — | U | Same; spectral index support is not a mineral abundance | `crism-mtrdr-context` |
| `mineral_hydrated_sulfates` | Mars | — | — | E | — | U | Same | `crism-mtrdr-context` |
| `mineral_olivine` | Mars | — | — | E | — | U | Same | `crism-mtrdr-context` |
| `mineral_pyroxene` | Mars | — | — | E | — | U | Same | `crism-mtrdr-context` |
| `context_bd1900_2` | Mars | — | — | E | — | — | CRISM spectral parameter; not a calibrated mineral quantity | `crism-mtrdr-context` |
| `context_cindex2` | Mars | — | — | E | — | — | Same | `crism-mtrdr-context` |
| `context_d2300` | Mars | — | — | E | — | — | Same | `crism-mtrdr-context` |
| `context_hcpindex2` | Mars | — | — | E | — | — | Same | `crism-mtrdr-context` |
| `context_lcpindex2` | Mars | — | — | E | — | — | Same | `crism-mtrdr-context` |
| `context_olindex3` | Mars | — | — | E | — | — | Same | `crism-mtrdr-context` |
| `context_sindex2` | Mars | — | — | E | — | — | Same | `crism-mtrdr-context` |

## D03 — Thermal environment (0/6 scoreable)

All rows are **not comparable**: reanalysis/remote-sensing methods, native support and temporal
selectors are not shared with the 90 m terrain windows; no cross-body temperature equivalence is
registered.

| Feature | Body | S | F | E | C | U | Gate / why not a score | Source |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `surface_air_temperature_2m_c` | Earth | — | — | E | C | — | Daily reanalysis point/time selector ≠ 90 m static window | `nasa-power-daily-point` |
| `surface_air_temperature_2m_max_c` | Earth | — | — | E | C | — | Same; daily maximum | `nasa-power-daily-point` |
| `surface_air_temperature_2m_min_c` | Earth | — | — | E | C | — | Same; daily minimum | `nasa-power-daily-point` |
| `surface_skin_temperature_c` | Earth | — | — | E | C | — | Same; skin temperature, different quantity | `nasa-power-daily-point` |
| `surface_brightness_temperature_k` | Moon | — | — | E | — | U | Diviner bolometric T at 240 m bins; not Earth-comparable | `lro-diviner-pcp-pols-summer-240m` |
| `thermal_inertia` | Mars | — | — | E | — | — | THEMIS thermal inertia; different method/support | `usgs-mars-themis-global-thermal-inertia-quantitative` |

## D04 — Water and ice (0/8 scoreable)

All rows are **not comparable**: hydrogen proxies, radar CPR, reanalysis wetness and snow depth are
different physical quantities with different supports; no validated cross-body or same-quantity
mapping is registered.

| Feature | Body | S | F | E | C | U | Gate / why not a score | Source |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `water_equivalent_hydrogen_wt_percent` | Moon | — | — | E | — | — | Neutron hydrogen proxy, not water abundance | `lp-ns-hydrogenhd` |
| `radar_circular_polarization_ratio` | Moon | — | — | E | — | — | Mini-RF CPR; geometric/regolith interpretation needed | `lro-minirf-cpr-128ppd` |
| `epithermal_neutron_flux_ratio` | Mars | — | — | E | — | — | HEND neutron proxy; different body/quantity | `mro-hend-epithermal-020917` |
| `water_ice_detection` | Mars/Moon | — | — | E | — | U | Detection state, not a continuous measurement | HEND / Mini-RF / lunar hydrogen bundle |
| `precipitation_mm_day` | Earth | — | — | E | — | — | Reanalysis daily rate; time selector/support differ | `nasa-power-daily-point` |
| `surface_soil_wetness_fraction` | Earth | — | — | E | — | — | Reanalysis fraction; not a soil measurement at 90 m | `nasa-power-daily-point` |
| `root_zone_soil_wetness_fraction` | Earth | — | — | E | — | — | Same; root-zone model output | `nasa-power-daily-point` |
| `snow_depth_cm` | Earth | — | — | E | — | — | Reanalysis snow depth; dynamic, not static terrain | `nasa-power-daily-point` |

## D05 — Solar illumination and radiation (0/4 scoreable)

All rows are **not comparable**: solar geometry and irradiance depend on a time selector and
body-specific ephemerides; they are context/evidence, not surface-property similarity.

| Feature | Body | S | F | E | C | U | Gate / why not a score | Source |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `solar_elevation_deg` | Mars/Moon | — | — | E | C | — | SPICE geometry at a chosen epoch; time-dependent | `naif-spice-generic-kernels` |
| `solar_incidence_deg` | Mars/Moon | — | — | E | C | — | Same | `naif-spice-generic-kernels` |
| `surface_longwave_irradiance_kwh_m2_day` | Earth | — | — | E | C | — | Daily reanalysis irradiance; time selector differs | `nasa-power-daily-point` |
| `surface_shortwave_irradiance_kwh_m2_day` | Earth | — | — | E | C | — | Same | `nasa-power-daily-point` |

## D06 — Regolith and surface properties (20/66 scoreable)

Only Earth SoilGrids **mean** predictions are scoreable, and only through the exact-ID gate
(`typed_areas.area_id == candidates.location_id`, Earth body, `method_version
soilgrids-wcs-2.0.0`, exact unit, exact depth interval and `statistic=mean`, `state=detected`,
finite value, recorded bbox equal to the candidate bbox within `1e-6` degree). Values are 250 m
model predictions aggregated over the AOI envelope, not local soil samples and not the same
spatial support as the 90 m terrain window.

| Feature group (exact ids) | Body | S | F | E | C | U | Gate / why not a score | Source |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `soil_{bdod,clay,sand,silt,soc}_0_5cm_mean` (5 ids) | Earth | S | F | E | — | U | Exact-ID 0–5 cm mean; 113 gated candidates | ISRIC SoilGrids 2.0 / `soilgrids-250m-v2` |
| `soil_{bdod,clay,sand,silt,soc}_{5_15,15_30,30_60}cm_mean` (15 ids) | Earth | S | F | E | — | U | Same gate, but only 5 candidates have these depths in the committed pilots; a positive weight excludes the other 108 as `missing_feature` | ISRIC SoilGrids 2.0 / `soilgrids-250m-v2` |
| `soil_{bdod,clay,sand,silt,soc}_{0_5,5_15,15_30,30_60}cm_{q05,q95}` (40 ids) | Earth | — | — | E | — | U | Prediction quantile of the same model prediction; uncertainty evidence for the matching mean, never an independent observation, score or filter | ISRIC SoilGrids 2.0 / `soilgrids-250m-v2` |
| `bearing_strength_kpa`, `bulk_density_kg_m3`, `cohesion_kpa`, `friction_angle_deg`, `particle_size_distribution`, `porosity_fraction` (6 ids) | Mars/Moon | — | — | E | — | U | No registered eligibility rule for these planetary regolith observations; evidence/uncertainty only, no cross-body equivalence | PDS Apollo LSRP/HFE and InSight HP3/RAD audits |

Notes:

- Sand, silt and clay are compositional fractions of one SoilGrids prediction and are not
  independent measurements. Requesting several of them double-weights the same texture
  information; the registry exposes `compositional_group` so callers can see this.
- `mean`, `q05` and `q95` at one depth are one measurement family; quantiles must not be posted as
  three independent targets.
- Depth intervals are never pooled. The requested feature id fixes one interval.

## Query rules enforced by the API

1. A target in any role other than `similarity` is rejected with `feature_role_not_scoreable` and
   the legal roles in `error.details`; it is never scored as zero.
2. A filter in any role other than `hard_filter` is rejected with `feature_role_not_filterable`.
3. A positive-weight score feature that is missing, QA-gated, unit/body/method-incompatible or
   non-finite excludes the candidate with `missing_feature:<id>` / `incompatible_features`; a
   zero-weight feature is reported as `zero_weight_not_compared` and never excludes. Missing is
   never imputed as zero.
4. Every requested feature must be registered for the target/source body. A Moon or Mars reference
   cannot borrow Earth-only D06 (`body_out_of_scope`, `cross_body_incompatible`).
5. `distance` is a weighted normalized query distance over explicitly requested, gated features.
   It is not a six-dimension scientific total, not a similarity percentage and not a cross-body
   equivalence.
6. `evidence_records` may include stored `evidence_only`/`context_only` rows, but they never enter
   `distance` and their `legal_roles` do not include `similarity` unless the registry separately
   promotes a gated query feature.

Regression tests: `apps/api/tests/test_sixdim_weighted_search.py` asserts this matrix against the
live registry and exercises D01-only, D06-only, combined D01+D06, feature/dimension weights, the
missing/zero-weight policy, and the evidence-only roles.
