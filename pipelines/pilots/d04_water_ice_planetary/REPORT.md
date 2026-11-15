# D04 Moon/Mars water/ice evidence pilot — report

- Release: `d04-water-ice-pilot-1.0.0`
- Method: `water-ice-evidence-0.1.0`
- Tasks attempted/succeeded/failed: 30/30/0
- Observations: 55
- Native areas: 30
- By body: {'Mars': 2, 'Moon': 53}
- By feature: {'epithermal_neutron_flux_ratio': 1, 'radar_circular_polarization_ratio': 12, 'water_equivalent_hydrogen_wt_percent': 12, 'water_ice_detection': 30}
- By state: {'detected': 25, 'no_coverage': 5, 'not_detected': 1, 'unknown': 24}
- Detection claims by state: {'no_coverage': 5, 'not_detected': 1, 'unknown': 24}
- No-coverage claims: 5
- Detected ice claims: 0

## Moon — Lunar Prospector water-equivalent hydrogen

| Native cell | Members | H (ppm wt) | WEH (wt%) | Ice claim |
|---|---|---:|---:|---|
| `moon-lp-h2-cell-s85p25-e31p75` | 1 | 145.34 | 0.1308 | `unknown` |
| `moon-lp-h2-cell-s85p75-w0p25` | 1 | 128.63 | 0.1158 | `unknown` |
| `moon-lp-h2-cell-s88p75-e123p75` | 1 | 150.40 | 0.1354 | `unknown` |
| `moon-lp-h2-cell-s88p75-w68p25` | 2 | 145.50 | 0.1310 | `unknown` |
| `moon-lp-h2-cell-s88p75-w68p75` | 2 | 145.52 | 0.1310 | `unknown` |
| `moon-lp-h2-cell-s89p25-w137p25` | 4 | 143.85 | 0.1295 | `unknown` |
| `moon-lp-h2-cell-s89p25-w137p75` | 2 | 143.85 | 0.1295 | `unknown` |
| `moon-lp-h2-cell-s89p25-w138p25` | 1 | 143.86 | 0.1295 | `unknown` |
| `moon-lp-h2-cell-s89p75-w155p75` | 1 | 147.08 | 0.1324 | `unknown` |
| `moon-lp-h2-cell-s89p75-w156p25` | 1 | 147.09 | 0.1324 | `unknown` |
| `moon-lp-h2-cell-s89p75-w156p75` | 1 | 147.09 | 0.1324 | `unknown` |
| `moon-lp-h2-cell-s89p75-w157p25` | 1 | 147.10 | 0.1324 | `unknown` |

## Moon — Mini-RF circular polarization ratio (native 236.9 m pixels)

| Native window | Members | Valid px | Min | Median | Max | CPR>=1 px | Ice claim |
|---|---|---:|---:|---:|---:|---:|---|
| `moon-minirf-cpr-l22451-s27100` | 1 | 64/64 | 0.133 | 0.469 | 1.936 | 3 | `unknown` |
| `moon-minirf-cpr-l22524-s23006` | 1 | 64/64 | 0.090 | 0.544 | 0.939 | 0 | `not_detected` |
| `moon-minirf-cpr-l22866-s14284` | 1 | 64/64 | 0.225 | 0.470 | 1.217 | 6 | `unknown` |
| `moon-minirf-cpr-l22867-s14274` | 1 | 64/64 | 0.257 | 0.502 | 1.786 | 10 | `unknown` |
| `moon-minirf-cpr-l22871-s14265` | 2 | 64/64 | 0.142 | 0.466 | 1.675 | 5 | `unknown` |
| `moon-minirf-cpr-l22884-s38850` | 1 | 64/64 | 0.207 | 0.359 | 1.123 | 3 | `unknown` |
| `moon-minirf-cpr-l22969-s05452` | 1 | 8/64 | 0.562 | 0.562 | 0.562 | 0 | `unknown` |
| `moon-minirf-cpr-l22971-s05311` | 1 | 16/64 | 0.470 | 0.638 | 0.841 | 0 | `unknown` |
| `moon-minirf-cpr-l23006-s02932` | 1 | 64/64 | 0.403 | 0.682 | 2.064 | 21 | `unknown` |
| `moon-minirf-cpr-l23007-s03004` | 1 | 64/64 | 0.541 | 0.946 | 1.972 | 28 | `unknown` |
| `moon-minirf-cpr-l23008-s03027` | 1 | 64/64 | 0.578 | 1.242 | 1.660 | 40 | `unknown` |
| `moon-minirf-cpr-l23009-s03074` | 1 | 64/64 | 0.443 | 0.697 | 1.074 | 8 | `unknown` |

## Mars — HEND normalized epithermal neutron flux

| Native cell | Members | Normalized flux | Ice claim |
|---|---|---:|---|
| `mars-hend-cell-n20p00-e78p00` | 5 | 0.7330 | `unknown` |

Every numeric observation is `evidence_only`. No ice detection, ice fraction, mineable-ice volume, or D04 similarity score is produced. The only `detected` state in this artifact is a measurement of a physical quantity at its native support; `water_ice_detection` claims are `unknown` or `no_coverage`.
