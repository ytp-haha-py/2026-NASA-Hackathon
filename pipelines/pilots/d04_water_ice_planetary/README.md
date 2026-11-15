# D04 Moon/Mars — water/ice evidence pilot

Bounded, checksummed, **evidence-only** pilot built from three official public PDS
products over the existing Moon/Mars terrain reference windows (Issue #44).

## Actual content

| Item | Value |
|---|---:|
| Tasks planned / succeeded / failed | 30 / 30 / 0 |
| Native areas | 30 |
| Observations | 55 |
| By body | {'Mars': 2, 'Moon': 53} |
| By feature | {'epithermal_neutron_flux_ratio': 1, 'radar_circular_polarization_ratio': 12, 'water_equivalent_hydrogen_wt_percent': 12, 'water_ice_detection': 30} |
| By state | {'detected': 25, 'no_coverage': 5, 'not_detected': 1, 'unknown': 24} |
| Detection claims | 30 ({'no_coverage': 5, 'not_detected': 1, 'unknown': 24}) |
| No-coverage claims | 5 |
| Detected ice claims | 0 |

## Products

| Body | Product | Native support | Quantity |
|---|---|---|---|
| Moon | Lunar Prospector NS `hydrogenhd` | 0.5° x 0.5° | H ppm by weight, converted to water-equivalent H wt% |
| Moon | Mini-RF `GLOBAL_CPR_128PPD_SIMP_0C` | 236.9 m | circular polarization ratio |
| Mars | Odyssey HEND `hend_epithermal_020917` | 4° x 4° | normalized epithermal neutron flux |

## Scientific limits

- Neutron-derived hydrogen is not ice: phase, ice fraction and mineable ice are
  never inferred. Mars HEND is a normalized flux, not water-equivalent hydrogen.
- Mini-RF CPR at or above unity is ice-consistent but not diagnostic; the mosaic
  mixes multiple acquisitions and has no depth retrieval.
- The neutron cells (0.5° Moon, 4° Mars) are far coarser than the 1.98 km reference
  windows. The pilot therefore reports `unknown` claims for neutron products and
  `no_coverage`/`not_detected`/`unknown` for radar, never an AOI-scale detection.
- Native grids are preserved; nothing is resampled to 90 m or to Earth WGS84.
- No D04 similarity feature, D04 score, or cross-body water score exists.

## Files

- `plan.json` — bounded native-read plan (30 tasks) with exact URLs/parameters.
- `observations.jsonl` — typed-v2 D04 observations (numeric evidence + claims).
- `assets.json` — source and window SHA-256 provenance; bytes are **not** committed.
- `sources.json` — body x product matrix with verified/candidate status and blockers.
- `manifest.json` — counts, checksums, limitations, non-claims, reproduction commands.
- `REPORT.md` — compact per-native-cell results.

## Reproduce

```bash
uv run python scripts/build_water_ice_pilot.py --download
uv run python scripts/build_water_ice_pilot.py
uv run python scripts/build_water_ice_pilot.py --check
```

The first command downloads the two small ASCII tables and the 17 bounded Mini-RF
windows into the gitignored `var/cache/water-44` cache. Full details are in
`docs/D04_WATER_ICE_PILOT.md`.
