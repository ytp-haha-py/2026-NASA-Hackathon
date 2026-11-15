# D05 AOI illumination pilot — report

- Release: `d05-aoi-illumination-1.0.0`
- Illumination method: `d05-aoi-illumination-1.0.0`
- Radiation audit: `d05-aoi-ionizing-radiation-audit-1.0.0`
- Window: `2026-03-01T00:00:00Z` .. `2026-03-02T00:00:00Z`,
  60 min cadence
- Observations: 102 (illumination 100,
  radiation evidence 2)
- AOIs: 2
- By body: {'Mars': 51, 'Moon': 51}
- By feature: {'ionizing_dose_gy': 2, 'solar_elevation_deg': 50, 'solar_incidence_deg': 50}
- By state: {'detected': 100, 'no_coverage': 2}
- Radiation values produced: 0

## AOI summary

| Body | AOI | Frame | Lat (deg) | Lon (deg) | Samples | Elevation min/max (deg) | Illuminated fraction | Crossings | Radiation |
|---|---|---|---:|---:|---:|---:|---:|---:|---|
| Moon | `moon-shackleton-rim-site04-roi1` | `MOON_ME` | -89.761966 | -157.067197 | 25 | -0.324804 / -0.283213 | 0.0000 | 0 | `unsupported` |
| Mars | `mars-jezero-ctx-window-01` | `IAU_MARS` | 18.220253 | 77.370952 | 25 | -85.757606 / 51.200849 | 0.4800 | 2 | `no_coverage` |

## Geometric horizon support

- `mars-jezero-ctx-window-01` sunset at `2026-03-01T01:05:22Z` (bisection, tol 1.0 s, bracket 2026-03-01T01:00:00Z .. 2026-03-01T02:00:00Z)
- `mars-jezero-ctx-window-01` sunrise at `2026-03-01T14:23:31Z` (bisection, tol 1.0 s, bracket 2026-03-01T14:00:00Z .. 2026-03-01T15:00:00Z)

All horizon support is the flat-ellipsoid geometric horizon. No terrain horizon,
shadow mask or horizon visibility interval is computed.

## Sample values

| Body | Area | Sample UTC | Incidence (deg) | Elevation (deg) | Local solar time | Geometric horizon |
|---|---|---|---:|---:|---:|---|
| Moon | `moon-shackleton-rim-site04-roi1` | 2026-03-01T00:00:00Z | 90.283213 | -0.283213 | 23.20 h | below |
| Moon | `moon-shackleton-rim-site04-roi1` | 2026-03-01T01:00:00Z | 90.285164 | -0.285164 | 23.23 h | below |
| Moon | `moon-shackleton-rim-site04-roi1` | 2026-03-01T02:00:00Z | 90.287097 | -0.287097 | 23.27 h | below |
| Moon | `moon-shackleton-rim-site04-roi1` | 2026-03-01T03:00:00Z | 90.289012 | -0.289012 | 23.30 h | below |
| Moon | `moon-shackleton-rim-site04-roi1` | 2026-03-01T04:00:00Z | 90.290907 | -0.290907 | 23.34 h | below |
| Moon | `moon-shackleton-rim-site04-roi1` | 2026-03-01T05:00:00Z | 90.292784 | -0.292784 | 23.37 h | below |
| Moon | `moon-shackleton-rim-site04-roi1` | 2026-03-01T06:00:00Z | 90.294643 | -0.294643 | 23.40 h | below |
| Moon | `moon-shackleton-rim-site04-roi1` | 2026-03-01T07:00:00Z | 90.296482 | -0.296482 | 23.44 h | below |
| Moon | `moon-shackleton-rim-site04-roi1` | 2026-03-01T08:00:00Z | 90.298302 | -0.298302 | 23.47 h | below |
| Moon | `moon-shackleton-rim-site04-roi1` | 2026-03-01T09:00:00Z | 90.300104 | -0.300104 | 23.50 h | below |
| Moon | `moon-shackleton-rim-site04-roi1` | 2026-03-01T10:00:00Z | 90.301886 | -0.301886 | 23.54 h | below |
| Moon | `moon-shackleton-rim-site04-roi1` | 2026-03-01T11:00:00Z | 90.303649 | -0.303649 | 23.57 h | below |
| Moon | `moon-shackleton-rim-site04-roi1` | 2026-03-01T12:00:00Z | 90.305393 | -0.305393 | 23.60 h | below |
| Moon | `moon-shackleton-rim-site04-roi1` | 2026-03-01T13:00:00Z | 90.307118 | -0.307118 | 23.64 h | below |
| Moon | `moon-shackleton-rim-site04-roi1` | 2026-03-01T14:00:00Z | 90.308823 | -0.308823 | 23.67 h | below |
| Moon | `moon-shackleton-rim-site04-roi1` | 2026-03-01T15:00:00Z | 90.310509 | -0.310509 | 23.71 h | below |
| Moon | `moon-shackleton-rim-site04-roi1` | 2026-03-01T16:00:00Z | 90.312176 | -0.312176 | 23.74 h | below |
| Moon | `moon-shackleton-rim-site04-roi1` | 2026-03-01T17:00:00Z | 90.313823 | -0.313823 | 23.77 h | below |
| Moon | `moon-shackleton-rim-site04-roi1` | 2026-03-01T18:00:00Z | 90.315451 | -0.315451 | 23.81 h | below |
| Moon | `moon-shackleton-rim-site04-roi1` | 2026-03-01T19:00:00Z | 90.317059 | -0.317059 | 23.84 h | below |
| Moon | `moon-shackleton-rim-site04-roi1` | 2026-03-01T20:00:00Z | 90.318648 | -0.318648 | 23.87 h | below |
| Moon | `moon-shackleton-rim-site04-roi1` | 2026-03-01T21:00:00Z | 90.320216 | -0.320216 | 23.91 h | below |
| Moon | `moon-shackleton-rim-site04-roi1` | 2026-03-01T22:00:00Z | 90.321766 | -0.321766 | 23.94 h | below |
| Moon | `moon-shackleton-rim-site04-roi1` | 2026-03-01T23:00:00Z | 90.323295 | -0.323295 | 23.98 h | below |
| Moon | `moon-shackleton-rim-site04-roi1` | 2026-03-02T00:00:00Z | 90.324804 | -0.324804 | 0.01 h | below |
| Mars | `mars-jezero-ctx-window-01` | 2026-03-01T00:00:00Z | 76.241010 | +13.758990 | 16.47 h | above |
| Mars | `mars-jezero-ctx-window-01` | 2026-03-01T01:00:00Z | 88.850019 | +1.149981 | 17.44 h | above |
| Mars | `mars-jezero-ctx-window-01` | 2026-03-01T02:00:00Z | 101.889074 | -11.889074 | 18.42 h | below |
| Mars | `mars-jezero-ctx-window-01` | 2026-03-01T03:00:00Z | 115.203682 | -25.203682 | 19.39 h | below |
| Mars | `mars-jezero-ctx-window-01` | 2026-03-01T04:00:00Z | 128.697607 | -38.697607 | 20.36 h | below |
| Mars | `mars-jezero-ctx-window-01` | 2026-03-01T05:00:00Z | 142.304954 | -52.304954 | 21.33 h | below |
| Mars | `mars-jezero-ctx-window-01` | 2026-03-01T06:00:00Z | 155.969323 | -65.969323 | 22.31 h | below |
| Mars | `mars-jezero-ctx-window-01` | 2026-03-01T07:00:00Z | 169.575896 | -79.575896 | 23.28 h | below |
| Mars | `mars-jezero-ctx-window-01` | 2026-03-01T08:00:00Z | 175.757606 | -85.757606 | 0.25 h | below |
| Mars | `mars-jezero-ctx-window-01` | 2026-03-01T09:00:00Z | 162.504495 | -72.504495 | 1.23 h | below |
| Mars | `mars-jezero-ctx-window-01` | 2026-03-01T10:00:00Z | 148.842916 | -58.842916 | 2.20 h | below |
| Mars | `mars-jezero-ctx-window-01` | 2026-03-01T11:00:00Z | 135.204284 | -45.204284 | 3.17 h | below |
| Mars | `mars-jezero-ctx-window-01` | 2026-03-01T12:00:00Z | 121.654034 | -31.654034 | 4.15 h | below |
| Mars | `mars-jezero-ctx-window-01` | 2026-03-01T13:00:00Z | 108.250932 | -18.250932 | 5.12 h | below |
| Mars | `mars-jezero-ctx-window-01` | 2026-03-01T14:00:00Z | 95.074548 | -5.074548 | 6.09 h | below |
| Mars | `mars-jezero-ctx-window-01` | 2026-03-01T15:00:00Z | 82.247710 | +7.752290 | 7.06 h | above |
| Mars | `mars-jezero-ctx-window-01` | 2026-03-01T16:00:00Z | 69.974680 | +20.025320 | 8.04 h | above |
| Mars | `mars-jezero-ctx-window-01` | 2026-03-01T17:00:00Z | 58.614706 | +31.385294 | 9.01 h | above |
| Mars | `mars-jezero-ctx-window-01` | 2026-03-01T18:00:00Z | 48.818367 | +41.181633 | 9.98 h | above |
| Mars | `mars-jezero-ctx-window-01` | 2026-03-01T19:00:00Z | 41.709530 | +48.290470 | 10.96 h | above |
| Mars | `mars-jezero-ctx-window-01` | 2026-03-01T20:00:00Z | 38.799151 | +51.200849 | 11.93 h | above |
| Mars | `mars-jezero-ctx-window-01` | 2026-03-01T21:00:00Z | 40.992769 | +49.007231 | 12.90 h | above |
| Mars | `mars-jezero-ctx-window-01` | 2026-03-01T22:00:00Z | 47.588530 | +42.411470 | 13.87 h | above |
| Mars | `mars-jezero-ctx-window-01` | 2026-03-01T23:00:00Z | 57.077262 | +32.922738 | 14.85 h | above |
| Mars | `mars-jezero-ctx-window-01` | 2026-03-02T00:00:00Z | 68.258732 | +21.741268 | 15.82 h | above |

All values are model-derived solar geometry at explicit UTC instants. No observed
irradiance, shadow fraction, ionizing dose or flux is present.
