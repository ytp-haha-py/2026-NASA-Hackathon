# Six-dimension ontology v1.0.0

Machine-readable source: [`contracts/ontology-v1.json`](../contracts/ontology-v1.json).
Python loader: `analog_pipelines.ontology`.

## Dimensions

| Code | Name | Research question | Subgroups |
|---|---|---|---|
| D01 | Terrain & Landforms | 地表具有什麼形狀？ | — |
| D02 | Geology & Mineralogy | 地表由什麼岩石與礦物構成？ | lithology, mineral spectral detection, geological process |
| D03 | Thermal Environment | 地表具有什麼溫度及熱物理環境？ | surface temperature, thermal inertia |
| D04 | Water & Ice | 地表／地下有哪些水與冰及觀測證據？ | surface water, subsurface ice, hydration evidence |
| D05 | Solar Illumination & Radiation | 地表受到什麼光照與輻射條件？ | **solar_illumination**, **ionizing_radiation** (separate) |
| D06 | Regolith & Surface Properties | 地表材料如何表現？ | particle size, thickness/density/porosity, strength, dust |

D02 has four **non-exclusive research views** (igneous, impact, sedimentary,
aqueous alteration). They are not a single mutually exclusive rock table.

## Legacy mapping

| Legacy dimension | Ontology mapping |
|---|---|
| `terrain_shape` | D01 |
| `land_cover_context` | Earth-only context; **no scored seventh dimension** |

The 16 existing feature IDs are unchanged:

- 5 terrain similarity features (`slope_mean_deg`, `slope_p90_deg`,
  `terrain_relief_p95_p05_m`, `roughness_std_m`, `flat_terrain_fraction`)
- 11 WorldCover context fractions (`landcover_*_fraction`)

The ontology records `score_role` and `compare_eligibility`. Context fractions can
be filters/annotations but cannot enter a weighted six-dimensional score.

## Validation rules

- Exactly D01–D06, in the formal order.
- All existing feature IDs must be mapped, with no extra/missing IDs.
- D02 keeps all four non-exclusive research classes.
- D05 keeps illumination and ionizing radiation separate.
- No context feature may claim a scored dimension.
- `GET /dimensions` and v1 contracts are not renamed by this layer.

## Future dimensions

D02–D06 feature IDs live in `analog_pipelines.dimensions` with method version
`*-evidence-0.1.0`. They are **not** in the v1 weighted score. Promotion to scoring
requires the gates in `docs/TERRAIN_METHOD_RESEARCH.md`, `docs/SIX_DIM_SOURCE_INVENTORY.md`
and the relevant Issue.
