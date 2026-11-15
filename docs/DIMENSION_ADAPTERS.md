# Dimension adapters (D02–D06)

Code: `analog_pipelines.dimensions`. Typed contracts: `analog_pipelines.typed_contracts`.

These adapters are **interfaces + validators + synthetic fixtures**, not measured
coverage. No D02–D06 source raster or product has been downloaded.

## Rules

- Feature IDs are stable and method-versioned (`*-evidence-0.1.0`).
- A value kind is not interchangeable with another: detection ≠ abundance,
  confidence ≠ concentration, spectrum band depth ≠ mineral fraction.
- Five states are explicit: `detected`, `not_detected`, `no_coverage`, `unknown`,
  `not_applicable`. Missing states carry no numeric value.
- Scores are allowed only for a validated homogeneous feature with matching method
  and support. D02–D06 are `evidence_only` by default.
- The provider interface (`discover`, `fetch_window`, `extract_features`, `validate`)
  must be implemented only after a source is verified and authorized.

## D02 Geology & Mineralogy

Typed features: lithology area fractions (distribution), mineral spectral detection
(detection), mineral spectrum (spectrum), process evidence.
- Preserve map legend/code and scale; no same-code cross-body equivalence.
- Iron oxides alone do not prove aqueous alteration.
- Detection/not-detected/no-coverage are separate; never turn confidence into
  abundance.
- Sources are candidate/identified only: GLiM, EMIT, USGS Moon map, M³, Jezero map,
  CRISM.

## D03 Thermal Environment

Typed features: mean surface temperature, diurnal range, timeseries.
- K, with scale/offset, emissivity/QA, local solar time, season and n.
- No fake extrema or time series.
- Thermal inertia requires the original retrieval model and uncertainty.
- Earth: NASA POWER thermal context is `context_only`.
- Mars: the USGS THEMIS-derived quantitative thermal-inertia mosaic is verified metadata only;
  it is `evidence_only`, has no approved similarity feature ID, no per-pixel uncertainty layer,
  and no observation is persisted. See `docs/MARS_PROVIDER_GATES.md`.

## D04 Water & Ice

Typed features: water/ice detection, depth interval.
- Preserve phase, surface/subsurface, depth interval, footprint, detection limit.
- No coverage ≠ not detected ≠ absent.
- Water-equivalent hydrogen is not mineable ice; hydrated minerals are D02 evidence.

## D05 Illumination & Radiation

Typed features: solar incidence/elevation angle (degree) and absorbed dose (Gy).
- Illumination and ionizing radiation are separate subgroups; dose units cannot
  appear on illumination.
- No Gy→Sv conversion without physics; no shadow inferred from dark pixels.
- Horizon/observer height, shielding, exposure interval and uncertainty required.
- The committed `pipelines/pilots/d05_spice_illumination/` pilot is **model-derived
  solar geometry only** (NAIF SPICE, one epoch, flat ellipsoid tangent plane). It
  is not observed irradiance, a shadow fraction, or a radiation dose; see
  `docs/D05_SPICE_PILOT.md`.

## D06 Regolith & Surface Properties

Typed features: particle size distribution, bulk density, cohesion.
- Sparse evidence tables, explicit bin edges/basis/depth/sample state.
- Never derive grain size or strength from DEM roughness or bare-fraction context.
- Measured vs modeled evidence flags; experiments include gravity/moisture/load.

## Offline fixture parsing

```python
from analog_pipelines.dimensions import parse_fixture_observations

observations = parse_fixture_observations("samples/fixtures/dimensions/...json")
```

Fixtures are tiny, synthetic/manually constructed records and are not observations
of real places.

## Promotion criteria

A feature can enter the weighted score only when:

1. An authoritative source/version/licence is verified.
2. Units, CRS, support, time/depth semantics and QA are validated.
3. A comparable method exists on at least two bodies, or the feature is deliberately
   body-specific.
4. Missingness and uncertainty are represented.
5. Tests prove detection/zero/no-coverage separations.
6. A maintainer approves the schema/method version change.
