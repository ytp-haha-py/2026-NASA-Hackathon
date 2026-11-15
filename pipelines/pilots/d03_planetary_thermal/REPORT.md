# D03 planetary thermal pilot (Moon Diviner PCP + Mars THEMIS TI)

Generated: `2026-11-15T06:27:26.029297+00:00`  
Export: `planetary-thermal-pilot-1.0.0`  
Data kind: `observed` (bounded observed product values)

## Actual content

| Item | Value |
|---|---:|
| Moon reference windows | 18 |
| Moon windows with samples (ltim01) | 18 |
| Mars reference windows | 5 |
| Mars windows with samples | 5 |
| Areas | 23 |
| Observations | 41 |
| By body | `{'Mars': 5, 'Moon': 36}` |
| By feature | `{'surface_brightness_temperature_k': 36, 'thermal_inertia': 5}` |
| By state | `{'detected': 31, 'no_coverage': 7, 'unknown': 3}` |
| By local solar time | `{'00:00-00:15': 18, '12:00-12:15': 18}` |

## Moon — Diviner PCP bolometric brightness temperature

Native 240 m bins, 1.98 km circular window, two 0.25 h local-time bins of the southern-summer composite. `n` is the number of native bins in the window; empty bins are absent from the product and are not zero-filled.

| Reference window | 00:00-00:15 mean K (n) | 12:00-12:15 mean K (n) | min–max K (midnight) |
|---|---:|---:|---:|
| `moon-shackleton-rim-site04-roi1` | 136.03 (47) | 153.13 (36) | 103.79–210.04 |
| `moon-shackleton-rim-site04-roi2` | 146.28 (46) | 149.74 (34) | 103.79–210.04 |
| `moon-shackleton-rim-site04-roi3` | 159.48 (50) | 137.76 (35) | 103.79–217.59 |
| `moon-shackleton-rim-site04-roi4` | 166.28 (49) | 131.19 (35) | 104.52–217.59 |
| `moon-lola-site01-roi1` | 120.57 (53) | 206.84 (3) | 78.54–182.53 |
| `moon-lola-site01-roi2` | 117.18 (52) | 194.50 (5) | 79.30–163.98 |
| `moon-lola-site01-roi3` | 119.83 (54) | 178.86 (7) | 79.30–182.29 |
| `moon-lola-site01-roi4` | 135.94 (54) | — (0) | 88.49–208.15 |
| `moon-lola-site01-roi5` | 152.07 (52) | 133.60 (1) | 90.52–222.45 |
| `moon-lola-site01-roi6` | 169.17 (53) | 173.59 (2) | 90.58–232.49 |
| `moon-lola-site01-roi7` | 177.93 (54) | 177.90 (11) | 90.58–243.82 |
| `moon-lola-site07-roi1` | 117.93 (9) | 175.40 (52) | 78.47–202.95 |
| `moon-lola-site11-roi1` | 107.35 (29) | — (0) | 70.21–187.56 |
| `moon-lola-site11-roi2` | 126.60 (24) | — (0) | 70.31–192.84 |
| `moon-lola-site11-roi3` | 195.27 (17) | — (0) | 136.33–222.79 |
| `moon-lola-site11-roi4` | 195.27 (17) | — (0) | 136.33–222.79 |
| `moon-lola-site20-bounds-centre` | 86.18 (53) | — (0) | 50.09–146.00 |
| `moon-lola-site23-bounds-centre` | 143.36 (50) | — (0) | 78.04–218.22 |

## Mars — THEMIS apparent thermal inertia

Native 100 m, 1.98 km circular window, one quantitative tile (`00N060E`). `n` is the number of native pixels in the window.

| Reference window | Mean TI (n) | Min–max TI | Published ±20% range |
|---|---:|---:|---:|
| `mars-jezero-ctx-window-01` | 213.63 (306) | 162.30–321.29 | 170.91–256.36 |
| `mars-jezero-ctx-window-02` | 357.58 (310) | 282.74–455.14 | 286.07–429.10 |
| `mars-jezero-ctx-window-03` | 358.77 (312) | 298.65–441.07 | 287.01–430.52 |
| `mars-jezero-ctx-window-04` | 460.19 (310) | 292.29–757.71 | 368.15–552.22 |
| `mars-jezero-ctx-window-05` | 619.65 (310) | 444.74–791.00 | 495.72–743.58 |

TI unit: `J m^-2 K^-1 s^-1/2`.

## Scientific limits and blockers

- Moon PCP is a multi-year composite at one local-time bin, not a single acquisition or same-sol time series.
- Moon PCP supplies no per-bin uncertainty; the reported value is a spatial window mean.
- Bolometric brightness temperature assumes unit emissivity and is not kinetic surface temperature.
- No diurnal range or change rate is computed because local-time bins are not same-sol samples.
- Mars THEMIS TI has no per-pixel uncertainty or QA layer; absolute accuracy is ~20%.
- Mars THEMIS TI retrieval does not correct atmospheric clouds, surface slopes or subsurface ice/bedrock.
- No cross-body D03 score; Earth NASA POWER context is unchanged.

Additional limits:

- Moon values are spatial means of 240 m bins; they are not 90 m measurements.
- Mars values are spatial means of 100 m pixels; they are not 90 m measurements.
- PCP local-time bins are different multi-year samples; a difference between the two bins is not a same-sol diurnal range and is therefore not emitted.
- Mars thermal inertia is not surface temperature and has no per-pixel QA.
- Only the existing reference windows are covered; this is not a global survey.
- No raw raster or PCP table is committed; only this derived export.

## Reproduction

```bash
uv run python scripts/build_planetary_thermal_pilot.py --download
uv run python scripts/build_planetary_thermal_pilot.py
uv run pytest pipelines/tests/test_thermal_planetary.py -q
```

The first command downloads the two PCP `.tab` files and the small 128 x 128 ISIS tile blocks into the gitignored `var/cache/thermal-43` cache. The second command rebuilds offline from that cache.
