# Dimension adapters (D02–D06)

Code: `analog_pipelines.dimensions`. Typed contracts: `analog_pipelines.typed_contracts`.

These adapters are **interfaces + validators + fixtures**, not measured coverage.
D06 Earth additionally has a bounded SoilGrids 2.0 model-prediction pilot; no D02–D06
measured in-situ/laboratory observation has been ingested.

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

Typed features: mean surface temperature, diurnal range, timeseries, plus the
evidence-only `surface_brightness_temperature_k` and `thermal_inertia` added by
Issue #43.
- K, with scale/offset, emissivity/QA, local solar time, season and n.
- No fake extrema or time series.
- Brightness temperature is a radiometric quantity under a unit-emissivity
  assumption; it is never labelled kinetic surface temperature.
- Thermal inertia requires the original retrieval model and uncertainty, and is
  a separate feature from surface/brightness temperature.
- Earth: NASA POWER thermal context is `context_only`.
- Moon: the committed `pipelines/pilots/d03_planetary_thermal/` pilot reads the
  official LRO Diviner PCP south-polar bolometric brightness-temperature product
  at native 240 m over the existing 18 LOLA reference windows (two 0.25 h
  local-time bins, southern-summer 2009-2019 composite). It is `evidence_only`;
  no diurnal range is computed from non-simultaneous local-time bins, and no
  value is presented as 90 m.
- Mars: the same pilot reads bounded 128 x 128 ISIS tiles from the USGS THEMIS
  quantitative thermal-inertia mosaic at native 100 m over the five existing
  Jezero reference windows. It is `evidence_only`: there is no per-pixel
  uncertainty/QA layer, absolute accuracy is ~20%, and the retrieval does not
  correct clouds or surface slopes. See `docs/MARS_PROVIDER_GATES.md`.


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

Typed features: particle size distribution, bulk density, porosity, cohesion,
friction angle and bearing strength.
- Sparse evidence tables, explicit bin edges/basis/depth/sample state.
- Depth support is mandatory per observation; different depths are never pooled.
- Never derive grain size or strength from DEM roughness or bare-fraction context.
- Measured vs modeled evidence flags; experiments include gravity/moisture/load.
- Earth SoilGrids 2.0 values are 250 m model predictions, never local measurements.
- Moon and Mars are `no_coverage` until a real public sample/PDS measurement exists.
- Versioned semantics: `contracts/d06-regolith-semantics-v1.json` and
  `docs/D06_REGOLITH_SEMANTICS.md`; Earth pilot
  `docs/D06_SOILGRIDS_DEPTH_PILOT.md`.

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
