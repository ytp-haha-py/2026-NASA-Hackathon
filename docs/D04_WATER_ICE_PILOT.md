# D04 water & ice evidence pilot (Moon/Mars)

Status: **bounded evidence-only pilot; no ice detection and no D04 score**.
Implements the source-verification and typed-evidence part of Issue #44 on top
of the existing Moon/Mars terrain reference windows.

## Why this is not an ice inventory

| Quantity | Source in this pilot | What it is | What it is not |
|---|---|---|---|
| Water-equivalent hydrogen | Lunar Prospector NS `hydrogenhd` (Moon) | H ppm by weight from corrected epithermal neutrons, x9 for H2O equivalence | ice fraction, ice phase, mineable ice |
| Normalized epithermal flux | Odyssey HEND `hend_epithermal_020917` (Mars) | dimensionless hydrogen proxy | water-equivalent hydrogen or ice abundance |
| Circular polarization ratio | Mini-RF global CPR mosaic (Moon) | near-surface radar scattering ratio | ice detection, depth, or abundance |

## Measured result

- Tasks attempted/succeeded/failed: 30/30/0.
- Observations: 55 over 30 native areas.
- By body: `{'Mars': 2, 'Moon': 53}`.
- By feature: `{'epithermal_neutron_flux_ratio': 1, 'radar_circular_polarization_ratio': 12, 'water_equivalent_hydrogen_wt_percent': 12, 'water_ice_detection': 30}`.
- By state: `{'detected': 25, 'no_coverage': 5, 'not_detected': 1, 'unknown': 24}`.
- Detection claims: `{'no_coverage': 5, 'not_detected': 1, 'unknown': 24}`; detected ice claims: 0.
- All 23 existing reference windows are covered by at least one native source
  cell, but no neutron cell can resolve a 1.98 km window, so no AOI-scale ice
  detection is asserted.

## State semantics

| State | Meaning in this pilot |
|---|---|
| `detected` | A physical quantity was measured at the observation's own native support. |
| `not_detected` | Valid source pixels exist at adequate support and the stated criterion is not met (e.g. all CPR < 1). |
| `no_coverage` | No valid source pixel exists in the window (mosaic gap). Not evidence of absence. |
| `unknown` | Data exist but cannot answer the question: coarse cell, hydrogen proxy, or partial coverage. |
| `not_applicable` | The quantity is undefined for the body or support (e.g. snow depth on the Moon). |

Physical zero is a `detected` value of 0.0 and is never rewritten as missing;
missing cells carry no value. Tests cover both, plus all five states.

## Products and access

| Dataset | Body | Product / version | Native support | Status |
|---|---|---|---|---|
| `lp-ns-hydrogenhd` | Moon | hydrogenhd (Hydrogen_halfdeg, December 2001) | 0.5 x 0.5 degree | `verified` |
| `lro-minirf-cpr-128ppd` | Moon | GLOBAL_CPR_128PPD_SIMP_0C.IMG | 128 pixels/degree, 0.23690117518867 km/pixel | `verified` |
| `mro-hend-epithermal-020917` | Mars | hend_epithermal_020917.tab | 4 x 4 degree cells (4050 cells) | `verified` |

Candidate and blocked sources (LAMP, LCROSS, SHARAD, MARSIS, CRISM, LEND ALD)
are recorded with their exact blocker in `sources.json` and in the pilot
`REPORT.md`. LEND ALD is accessible and parses, but its relative counting rates
lack the normalization constants needed for flux or water-equivalent hydrogen.

## Commands

```bash
uv run python scripts/build_water_ice_pilot.py --download
uv run python scripts/build_water_ice_pilot.py
uv run python scripts/build_water_ice_pilot.py --check
uv run pytest pipelines/tests/test_water_ice_pilot.py -q
uv run ruff check .
uv run ruff format --check .
```

## Non-claims

- No ice fraction, subsurface ice volume, or mineable-ice estimate.
- No water-equivalent hydrogen for Mars.
- No D02 hydrated-mineral duplication; CRISM hydration stays D02 evidence.
- No global coverage and no statement that an unmeasured area is ice-free.
- No D04 similarity feature or scalar blanket water score.
