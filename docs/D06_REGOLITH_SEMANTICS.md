# D06 regolith measurement semantics, version 1.0.0

Machine-readable contract: [`contracts/d06-regolith-semantics-v1.json`](../contracts/d06-regolith-semantics-v1.json).
Method version: `regolith-evidence-0.1.0`. Status: **specification only; it does not
add observed coverage by itself.**

This specification defines what a D06 value means before any source is ingested. It
applies to particle-size distributions, bulk density, porosity, cohesion, friction
angle and bearing strength for Earth, Moon and Mars.

## Principles

1. A model prediction is not a measurement. Every observation states its evidence
   class through quality flags and notes.
2. Depth support is mandatory and per observation. Different depths are never averaged,
   pooled or scored as one feature.
3. A measured physical zero is a value. `not_detected`, `no_coverage`, `unknown` and
   `not_applicable` are separate states and never zero-filled.
4. A particle-size distribution keeps its bin edges, bin basis (mass, volume or number)
   and depth.
5. Strength properties keep gravity, moisture, load geometry, failure criterion and
   sample state.
6. Moon and Mars values are `no_coverage` until a real public sample or PDS
   measurement with official version, licence and measurement conditions is registered.
7. No regolith property is derived from DEM roughness, slope, land-cover bare fraction,
   thermal inertia or transferred Earth SoilGrids values.

## Variables and units

| Feature ID | Value kind | Unit | Required context |
|---|---|---|---|
| `particle_size_distribution` | distribution | fraction | bin edges, basis, depth |
| `bulk_density_kg_m3` | numeric | kg/m^3 | sample state (`bulk_dry`, `bulk_natural`, `fine_earth`, `bulk_total`), depth |
| `porosity_fraction` | numeric | fraction (0–1) | pore definition, sample state, depth |
| `cohesion_kpa` | numeric | kPa | gravity, moisture, normal stress, failure criterion |
| `friction_angle_deg` | numeric | degree (0–90) | gravity, moisture, normal stress, failure criterion |
| `bearing_strength_kpa` | numeric | kPa | gravity, load geometry, depth, moisture, penetration rate |

`particle_size_distribution` uses ascending **lower bin edges** in micrometres with a
terminal `+inf` bin. The default USDA texture edges are:

| Bin | Lower edge | Label |
|---|---:|---|
| 1 | 0 µm | clay |
| 2 | 2 µm | silt |
| 3 | 50 µm | sand |
| 4 | 2000 µm | terminal `+inf` (coarse fragments excluded) |

Fractions are in [0, 1] and must sum to 1 within 1e-6. Any other published scheme must
record its own edges and labels. The generic contract also rejects non-increasing bins.

## Depth policy

- `depth_support.kind` is `surface` for an interface sample with no measured thickness,
  or `depth_interval` with `min_m` and `max_m` for a measured interval.
- D06 observations in state `detected` require explicit depth support; `unknown` depth
  fails validation.
- `min_m` is inclusive and `max_m` is exclusive at the semantic level.
- Registered SoilGrids intervals: 0–0.05, 0.05–0.15, 0.15–0.3, 0.3–0.6 m.
- A value for one interval cannot stand in for another. Combining intervals requires a
  thickness-weighted, explicitly versioned derivation, which this repository does not do.

## Evidence classes and quality flags

| Class | Flag pattern | Meaning |
|---|---|---|
| Measured sample | `measured_sample` | Laboratory/sample measurement with an ID and method |
| Measured in situ | `measured_in_situ` | Instrument measurement at a known location |
| Experiment | `experiment` | Geotechnical test with stated conditions |
| Model prediction | `model_derived_soil_property`, `model_prediction_not_measurement` | Global model output, not a local measurement |
| Not observed | state `no_coverage` | No registered real source |

## Body coverage matrix

| Variable | Earth | Moon | Mars |
|---|---|---|---|
| Clay / sand / silt predictions | SoilGrids 2.0 model prediction (5 AOIs) | `no_coverage` | `no_coverage` |
| Bulk density | SoilGrids fine-earth `bdod` model prediction (5 AOIs) | `no_coverage` | `no_coverage` |
| Particle-size distribution | no distribution ingested; texture predictions stay separate | `no_coverage` | `no_coverage` |
| Porosity | `no_coverage` | `no_coverage` | `no_coverage` |
| Cohesion | `no_coverage` | `no_coverage` | `no_coverage` |
| Friction angle | `no_coverage` | `no_coverage` | `no_coverage` |
| Bearing strength | `no_coverage` | `no_coverage` | `no_coverage` |

`no_coverage` means no registered public source in this repository. It does not mean the
property is zero, absent on the body, or impossible to measure.

The Moon/Mars provider `sparse-regolith-samples` discovers the source, reports
`status=blocked` with a specific blocker, and plans zero tasks. It raises on
acquire/process rather than fabricating a value. See
`pipelines/src/analog_pipelines/acquisition/providers_regolith.py`.

## Explicitly forbidden derivations

- DEM roughness/slope → grain size, cohesion, friction angle or bearing strength.
- Land-cover bare fraction → regolith thickness or grain size.
- Thermal inertia → bulk density without a verified physical model and stated
  conductivity/heat capacity assumptions.
- `1 - bulk_density / 2650` → porosity without a stated and justified particle density.
- Earth SoilGrids texture → lunar/Martian properties.
- Rover wheel slip, trafficability score or landform class → bearing strength without
  calibrated in-situ measurements.

## Code and tests

- Feature registry and D06 validation: `pipelines/src/analog_pipelines/dimensions.py`.
- Typed observation state/value rules: `pipelines/src/analog_pipelines/typed_contracts.py`.
- Earth prediction provider: `pipelines/src/analog_pipelines/acquisition/providers_soilgrids.py`.
- Moon/Mars no-coverage provider: `pipelines/src/analog_pipelines/acquisition/providers_regolith.py`.
- Offline tests: `pipelines/tests/test_regolith_semantics.py`,
  `pipelines/tests/test_soilgrids_depths.py`.
- Earth pilot report: [`D06_SOILGRIDS_DEPTH_PILOT.md`](D06_SOILGRIDS_DEPTH_PILOT.md).

## Change policy

Changing bin edges, units, depth pooling rules, allowed derivations or body coverage is a
semantics change. It requires a new contract version, matching `dimensions.py` feature and
validator updates, and offline tests proving zero/missing separation and depth preservation.
