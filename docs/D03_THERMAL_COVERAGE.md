# D03 Thermal Coverage (Issue #137)

Status: **bounded, evidence-only/context-only coverage; not a global or exhaustive survey.**
Machine-readable matrix: [`docs/data-sources/d03-thermal-coverage-matrix.json`](data-sources/d03-thermal-coverage-matrix.json).
Time-selector query evidence: `python3 scripts/d03_thermal_query_evidence.py`.

## Scope and semantics

- Dimension: `D03` Thermal Environment, Dimension -> Feature architecture preserved.
- Bodies: Earth, Moon, Mars represented with real committed pilot observations where data exists.
- Score roles: all D03 observations are `evidence_only` or `context_only`; **no cross-body D03 similarity score** is produced.
- No fabricated values, no zero-filled missing values, no invented time series from a single image.
- Model/reanalysis context (Earth NASA POWER) is kept distinct from measured/derived remote-sensing evidence (Moon Diviner PCP brightness temperature, Mars THEMIS apparent thermal inertia).

## Actual indexed/queryable coverage (committed)

| Body | Feature | Obs | Areas | Measured/modeled/derived | Time/LST/season/day-night support | Footprint | Uncertainty | States |
|---|---|---:|---:|---|---|---|---|---|
| Earth | `surface_air_temperature_2m_c`, `*_max_c`, `*_min_c`, `surface_skin_temperature_c` | 20 | 5 | modeled/reanalysis context (NASA POWER daily) | daily interval 2024-01-01..2024-01-07 (7 samples); no sub-daily LST; single 7-day window | ~0.5x0.625° grid point (coarse; not 1.98 km) | unknown per-value (model context) | detected 20 |
| Moon | `surface_brightness_temperature_k` | 36 | 18 | measured radiometric composite (Diviner PCP bolometric brightness temperature, unit emissivity) | multi-year southern-summer composite 2009-07-05..2019-02-17; LST bins 00:00-00:15 and 12:00-12:15; night/day bins present; no same-sol pair, no diurnal range computed | circular 1.98 km window, native 240 m bins, lunar projected frame | no per-bin product uncertainty; spatial n/min/max/std and valid_fraction recorded | detected 26, unknown 3, no_coverage 7 |
| Mars | `thermal_inertia` | 5 | 5 | derived retrieval evidence (THEMIS apparent thermal inertia) | static/time-independent retrieval; per-pixel acquisition time/season/LST unsupported by mosaic | circular 1.98 km window, native 100 m pixels, Mars projected frame | published ~20% absolute / ~10% relative; no per-pixel uncertainty/QA | detected 5 |

Totals: **61 observations** across **28 areas** (Earth 5, Moon 18, Mars 5), **D03 only**.

## Time-selector query evidence

Representative queries over committed pilot exports (`pipelines/pilots/d03_earth_power/observations.jsonl`, `pipelines/pilots/d03_planetary_thermal/observations.jsonl`):

```bash
python3 scripts/d03_thermal_query_evidence.py
```

Observed output demonstrates:
- **Earth:** temporal window selector `2024-01-01..2024-01-07` matches 20 observations across 5 areas.
- **Moon:** local-solar-time selectors `00:00-00:15` (night) and `12:00-12:15` (day) each match 18 observations with explicit states; season recorded as southern-summer composite; no diurnal range is invented.
- **Mars:** temporal-support selector `kind=static` matches 5 observations; LST/acquisition time not present in product and not fabricated.
- **Footprint/uncertainty:** representative native resolution, window/footprint kind and uncertainty semantics reported for all bodies.

## Sources, licences, CRS/frame, and provenance

| Body | Source | Version/product | Licence | CRS/frame | Resolution | Time/depth support |
|---|---|---|---|---|---|---|
| Earth | NASA POWER daily point API | thermal parameters T2M/T2M_MAX/T2M_MIN/TS, method `nasa-power-daily-1.0.0` | NASA POWER free/open (cite NASA POWER) | EPSG:4326 point query; ~0.5x0.625° native grid | coarse model grid | daily 2024-01-01..2024-01-07; 2 m air / skin |
| Moon | LRO Diviner PCP (PDS) | `PCP_AVG_TBOL_POLS_SUM_LTIM01_240.TAB`, `PCP_AVG_TBOL_POLS_SUM_LTIM49_240.TAB`, PDS4 1.0/cal 4.0 | PDS public archive, cite Williams et al. 2019 (10.1029/2019JE006028) | lunar polar stereographic R=1737.4 km (lunar_projected) | 240 m native, 1.98 km window mean | multi-year southern-summer composite; 0.25 h LST bins; surface brightness T |
| Mars | USGS THEMIS quantitative TI mosaic | `THEMIS_TI_Mosaic_Quant_00N060E_100mpp.cub`, edition 1 (2014-05-30) | Public domain (USGS), cite Fergason et al. 2006 (10.1029/2006JE002735) | Mars equirectangular R=3396.19 km (mars_projected) | 100 m native, 1.98 km window mean | static/time-independent retrieval; upper few cm apparent TI |

Checksums and source-asset provenance are recorded in:
- `pipelines/pilots/d03_earth_power/manifest.json`, `assets.json`
- `pipelines/pilots/d03_planetary_thermal/manifest.json`, `assets.json`

## Gaps, blockers, and non-claims

- **Moon:** only 2 of 96 official PCP local-time bins committed; 7 noon windows `no_coverage` and 3 `unknown` due to genuinely absent/sparse native bins (not zero-filled). Expanding to additional LST bins requires opt-in download/cache of large official PCP tables and bounded processing.
- **Mars:** mosaic has no per-pixel acquisition time/season/LST; time-series expansion from this product is impossible and not invented. Retrieval caveats (clouds/slopes/subsurface not corrected) remain.
- **Earth:** coarse modeled/reanalysis context only; one 7-day committed window; no sub-daily LST, no local in-situ measurement, no thermal inertia.
- **Cross-body:** brightness temperature, air/skin temperature, and apparent thermal inertia are not comparable quantities/methods/supports; all rows remain evidence_only/context_only.
- **Shared API/schema/scoring:** time-selector API/query contract changes are out of D03 file ownership (send to #141). Evidence is provided from committed typed exports and scripts.
- **Coverage:** bounded to listed areas/windows only; **not global and not exhaustive** for any body.

## Rebuild instructions

```bash
# Planetary thermal pilot (Moon Diviner PCP, Mars THEMIS TI)
uv run python scripts/build_planetary_thermal_pilot.py --download  # opt-in, network; caches under var/cache/thermal-43/
uv run python scripts/build_planetary_thermal_pilot.py             # offline rebuild from cache

# Existing tests
uv run pytest pipelines/tests/test_thermal_planetary.py -q
uv run pytest pipelines/tests/test_d03_thermal_coverage.py -q

# Time-selector query evidence (offline, committed exports)
python3 scripts/d03_thermal_query_evidence.py

# Earth D03 POWER pilot is committed under pipelines/pilots/d03_earth_power/.
# Re-acquisition/rebuild uses the acquisition pipeline (nasa-power provider, D03 Earth, bounded areas/dates; network opt-in).
```
