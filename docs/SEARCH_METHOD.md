# Search method

## Question answered

"Among the Earth windows indexed in this repository, which local terrain shapes are numerically
closest to the target vector?" It does **not** answer "which place on Earth is most like the Moon",
"what is globally the best analog", or "where should a base be built".

## Inputs

- 1..N supported feature ids with finite target values inside the registry domain.
- One nonnegative finite weight per requested feature; at least one weight > 0.
- A limit in 1..10.
- Optional verified planetary reference. Reference mode takes target values from the reference's
  observations and rejects mixed reference/target input.

Every requested feature with a **positive weight** must use its registry method version. Candidates
lacking a positive-weight metric or carrying an incompatible method version for it are excluded and
reported. Zero-weight metrics are not compared: their target value is still validated, but a
candidate does not need the value, contributes no distance term, and is not excluded for a missing
or incompatible zero-weight observation. This rule is the same in custom and reference mode.

**Hard filters run before the distance.** Optional inclusive `filters` (`{min?, max?}`) apply to
Earth land-cover context (`landcover_*_fraction`, 0..1), e.g. bare/sparse ≥ 0.5 or built-up ≤ 0.1,
and, since `search-1.1.0`, to registered similarity features within their registry domain, e.g.
mean slope 5–20°. A candidate missing a filtered value (or carrying another method version) fails
the filter; values are never imputed. Filters only remove candidates: they do not alter the
similarity formula, the fixed scales, or the weights, and land-cover context is never weighted.

## Distance

For candidate `c`, target `t`, feature `i`:

```
distance = sqrt( sum_i[ w_i * ((c_i - t_i) / scale_i)^2 ] / sum_i w_i )
```

- `scale_i` is a fixed, documented registry constant (10°, 15°, 200 m, 20 m, and 0.25 for flat
  terrain fraction). It is **not** computed
  from the current results or the global dataset, so adding a candidate cannot silently rescale
  every distance.
- Lower is more similar. Zero means all requested metrics match exactly.
- Sort order is `(distance asc, location_id asc)`; ties are deterministic.
- The current implementation has one scored dimension (`terrain_shape`). The API returns
  `dimension_distances` so a future multi-dimension registry aggregates within a dimension before
  combining dimension weights, preventing a dimension with many metrics from automatically
  dominating. Adding a second scored dimension requires a contract/method version bump.
- Returns `min(limit, eligible_count)` matches. Missing metrics are never imputed.

### Worked example (synthetic test fixture, not a scientific observation)

Target: mean slope 12°, relief 80 m; weights 1.0 and 0.5. Candidate "SYNTHETIC window Beta" has
slope 6° and relief 95 m.

```
slope term:   1.0 * ((6 - 12) / 10)^2       = 0.36
relief term:  0.5 * ((95 - 80) / 200)^2     = 0.0028125
distance = sqrt((0.36 + 0.0028125) / 1.5)   = 0.4918078893
```

This number is a unitless ranking aid, not "49% similar" and not a physical-equivalence measure.

## Research-informed weight presets (Custom mode)

Research Presets are analyst-designed starting weights informed by NASA research contexts.
They are **not NASA official weights**, experimentally validated optima, target measurements,
safety thresholds, or a universal ranking of research suitability. Definitions, feature IDs,
importance ratings and reference URLs live in `apps/web/src/lib/research-presets.ts`; explanatory
text uses the existing English / Traditional Chinese message catalogs.

| Feature ID | Rover Mobility | Precision Landing | Geological Fieldwork |
|---|---:|---:|---:|
| `slope_mean_deg` | 4 | 2 | 3 |
| `slope_p90_deg` | 5 | 5 | 2 |
| `terrain_relief_p95_p05_m` | 2 | 1 | 5 |
| `roughness_std_m` | 4 | 4 | 4 |
| `flat_terrain_fraction` | 2 | 5 | 1 |

These are importance ratings `r`: 5 core, 4 high, 3 medium, 2 low, 1 supporting, 0 not applicable.
Normalized weights are `r_i / sum(r_j)`. The existing 0–3 sliders use `weight_i = r_i / 2`,
producing respectively `[2, 2.5, 1, 2, 1]`, `[1, 2.5, 0.5, 2, 2.5]`, and
`[1.5, 1, 2.5, 2, 0.5]`. The common factor cancels in the existing distance denominator,
so the backend algorithm, fixed registry scales, method comparability, and missing-data rules
remain unchanged. All five currently have positive weights: a candidate missing any requested
metric is excluded under the existing rule. Higher importance means closer matching to the
analyst's target; it does not prefer larger/smaller values by itself.

NASA references support the research scenarios, **not the numerical ratings**:

- **Rover Mobility & Autonomous Navigation:** emphasize steep-tail slope, mean slope and
  roughness as coarse terrain proxies for representative mobility/navigation challenges.
  [NASA JPL ERNEST field testing](https://www.jpl.nasa.gov/news/nasa-testing-advanced-capabilities-for-moon-mars-rovers/)
  describes mobility and autonomy field work on challenging terrain and under varied lighting.
  D06 surface materials, D05 illumination and D03 thermal environment remain supporting evidence.
  A 90 m DEM cannot determine wheel-scale trafficability or rover safety.
- **Precision Landing & Hazard Avoidance:** emphasize P90 slope, flat fraction and roughness
  to match hazard-avoidance terrain characteristics.
  [NASA SPLICE](https://www.nasa.gov/safe-and-precise-landing-integrated-capabilities-evolution-splice/)
  describes high-resolution sensor-based navigation/hazard detection, including rocks and slopes.
  D05 illumination and D06 surface materials remain supporting evidence. Our 90 m DEM cannot
  identify local rocks, contiguous safe landing areas, or actual landing safety. Matching a
  challenge environment is different from recommending a safe landing site.
- **Geological Fieldwork & Sampling:** emphasize relief, roughness and mean slope as landform
  context for field investigation.
  [NASA Artemis JETT / D-RATS](https://www.nasa.gov/humans-in-space/nasa-to-practice-artemis-moonwalking-roving-operations-in-arizona-desert/)
  describes terrain, geological context, field operations and sample collection. D02 geology
  and mineralogy are core scientific requirements, while D06 surface materials are also valuable;
  neither enters this terrain-only numerical score. This is not complete geological similarity.

Presets appear only in Custom mode beside the dimension heading, sharing its wrapping header
row with the result limit and actions. The visible preset label and explanatory block are omitted;
the select retains a localized accessible name and a capability-failure tooltip. Research rationale,
NASA sources and scientific limitations remain documented here. Applying one changes weights
only, retains targets/filters/limit, and waits for the existing Search button. Initial zero
targets are not NASA recommendations: users must set targets appropriate to their question.
All five IDs must be present in API capabilities as numeric `terrain_shape` similarity features;
otherwise profiles are disabled with an explanation, and Manual remains available. No missing
feature/observation is fabricated. Additional features are not opted into scoring by these presets.

Editing a Custom weight returns the selector to Manual; editing targets/filters does not.
Selecting Manual retains current settings; Reset returns weights/targets/filters/limit to their
existing neutral defaults and resets the selector. Capability refresh returns the selector to
Manual. Reference weights remain separate. Existing AOI `SearchTargetPreset` behavior still replaces
weights/targets and submits once; it clears the Research Preset label. Selecting research weights
afterwards preserves the AOI target values and does not submit again. No preset identifier or
research-only metadata is added to the search API request, and D02–D06 stay evidence/context only.

## Per-result breakdown

Each result includes, per requested metric: `target`, `candidate`, `delta`, fixed `scale`, `weight`,
`normalized_delta = delta/scale`, and `contribution = w * normalized_delta^2 / sum(weights)`.
The contributions sum to `distance^2`. This makes the ranking auditable and explains why a candidate
matches or differs.

## Missing data and exclusions

- Missing/non-finite candidate metric with a positive weight: excluded with
  `missing_metric:<feature_id>`.
- Positive-weight candidate observation whose method version differs from the query: excluded with
  `incompatible_method_version`.
- A zero-weight metric never affects eligibility or distance. A compatible candidate value may be
  listed with zero contribution; an absent, non-finite, or incompatible value is omitted.
- Extraction rejects windows with <80% valid elevation cells; those windows should not have been
  persisted.
- Empty index: `409 no_indexed_data`.
- Non-empty index but zero eligible candidates: HTTP 200 with `results: []` and the exclusion list.

## Coverage and honesty

`coverage_label` is always `indexed_subset`. The API returns a `coverage` object with the indexed
candidate count, geographic bbox, data kinds, and indexed vs on-demand origin counts; every
response also carries a `data_snapshot_id`/`data_version`. The UI displays "Top N among M indexed
regions" and a coverage disclaimer; it must never say "worldwide top 10". Results are limited to
committed windows from documented sources; no claim is made about unindexed regions.

## Why not a similarity percentage

A percentage implies a bounded, physically meaningful similarity scale. Here the distance depends
on analyst-chosen contrast scales and a small feature set. Reporting `1/(1+d)` as "percent similar"
would fabricate precision and comparability across different target vectors. We report the raw
distance and the per-metric normalized differences instead.

## Limitations

- Five terrain-shape metrics do not capture gravity, atmosphere, radiation, regolith thickness,
  mineralogy, temperature, wind, dust, or geological history.
- The Earth DEM can be a digital surface model (vegetation/buildings), which biases slope and
  roughness; this is recorded as a quality flag.
- Native resolution, resampling, interpolation, and vertical uncertainty differ by source. These
  are disclosed in dataset provenance and per-window notes.
- A 90 m analysis grid cannot resolve hazards or trafficability at rover-wheel scale.
- The index is small and curated; absence from results is not evidence that a region is a poor
  analog.
