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

All requested features must use method version `terrain-metrics-1.0.0`. Candidates lacking a
requested metric or carrying an incompatible method version are excluded and reported.

**Hard filters run before the distance.** Optional `filters` apply only to Earth land-cover context
(`landcover_*_fraction`, 0..1), e.g. bare/sparse ≥ 0.5 or built-up ≤ 0.1. Missing context fails a
requested filter; values are never imputed. Filters do not alter the similarity formula and are not
similarity dimensions.

## Distance

For candidate `c`, target `t`, feature `i`:

```
distance = sqrt( sum_i[ w_i * ((c_i - t_i) / scale_i)^2 ] / sum_i w_i )
```

- `scale_i` is a fixed, documented registry constant (10°, 15°, 200 m, 20 m). It is **not** computed
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

## Per-result breakdown

Each result includes, per requested metric: `target`, `candidate`, `delta`, fixed `scale`, `weight`,
`normalized_delta = delta/scale`, and `contribution = w * normalized_delta^2 / sum(weights)`.
The contributions sum to `distance^2`. This makes the ranking auditable and explains why a candidate
matches or differs.

## Missing data and exclusions

- Missing/non-finite candidate metric: excluded with `missing_metric:<feature_id>`.
- Candidate observation whose method version differs from the query: excluded with
  `incompatible_method_version`.
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

- Four terrain-shape metrics do not capture gravity, atmosphere, radiation, regolith thickness,
  mineralogy, temperature, wind, dust, or geological history.
- The Earth DEM can be a digital surface model (vegetation/buildings), which biases slope and
  roughness; this is recorded as a quality flag.
- Native resolution, resampling, interpolation, and vertical uncertainty differ by source. These
  are disclosed in dataset provenance and per-window notes.
- A 90 m analysis grid cannot resolve hazards or trafficability at rover-wheel scale.
- The index is small and curated; absence from results is not evidence that a region is a poor
  analog.
