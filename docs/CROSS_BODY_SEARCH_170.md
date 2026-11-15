# Cross-body D01 search: Moon/Mars target -> Earth candidates (Issue #170, Goal G / G2)

Status: **implemented as an additive query script + domain helper; D01 only; no six-dimension
cross-body score.**

- Domain helper: `apps/api/src/analog_api/domain/cross_body_search.py`
- Reproducible CLI: `scripts/cross_body_search_170.py`
- Tests: `apps/api/tests/test_cross_body_search.py`, `apps/api/tests/test_cross_body_170.py`
- G1 comparability gate consumed unchanged: `apps/api/src/analog_api/domain/cross_body.py`
  (`cross-body-comparability-1.0.0`)

This is the search/ranking layer that sits after G1. G1 answers "may these two observations be
compared at all?" This helper turns a Moon or Mars fine D01 vector into a legal Earth ranking
target, applies explicit feature/dimension weights, and ranks the **real** fine Earth candidates
already present in the database. It does not modify `scoring.py`, `features.py` or G1. The
consolidated Goal G gate description is in `docs/CROSS_BODY_170.md`; this file is the separate
search walkthrough.

## What is and is not produced

| Produced | Not produced |
|---|---|
| Per-feature normalised deltas, weights, contributions and contribution fractions | A six-dimension cross-body total |
| Per-dimension `D01` RMS contribution and distance | A similarity percentage |
| G1 gate decisions for every present candidate/feature pair | Environmental, geologic or mineralogical equivalence |
| Explicit missing/excluded reasons and provenance | A global Moon/Mars survey or global Earth claim |

The only comparable feature set is the five fine D01 terrain-shape metrics:

| Feature | Unit | Method version | Analysis grid | Support |
|---|---|---|---|---|
| `slope_mean_deg` | degree | `terrain-metrics-1.0.0` | 90 m | fixed 1.98 km × 1.98 km |
| `slope_p90_deg` | degree | `terrain-metrics-1.0.0` | 90 m | fixed 1.98 km × 1.98 km |
| `terrain_relief_p95_p05_m` | meter | `terrain-metrics-1.0.0` | 90 m | fixed 1.98 km × 1.98 km |
| `roughness_std_m` | meter | `terrain-metrics-1.0.0` | 90 m | fixed 1.98 km × 1.98 km |
| `flat_terrain_fraction` | fraction | `flat-terrain-fraction-1.0.0` | 90 m | fixed 1.98 km × 1.98 km |

D06 SoilGrids is Earth-only in the registry and G1; D02–D05 are evidence/context. Coarse
250 m / 5 km planetary AOI features are a different method and support and are refused, never
upsampled into a fine target.

## Actual Moon run

Target: committed fine reference `moon-lola-site01-roi1` (Moon, LOLA Site01 5 m source,
processed to the 90 m / 1.98 km terrain method).
Data version: `dv-b46f3bad38ea`. Earth candidate pool: 113 indexed fine windows.
Default weights: each feature 1.0; dimension `D01` 1.0; scoring `dimension-weighted-rms-v1`.

```bash
uv run python scripts/cross_body_search_170.py --body Moon --limit 5
```

| Rank | Earth candidate | Distance |
|---:|---|---:|
| 1 | `earth-devon-polar-r3c2` | 0.23761075 |
| 2 | `earth-devon-polar-r1c1` | 0.31790713 |
| 3 | `earth-namib-r2c3` | 0.32313827 |
| 4 | `earth-sahara-r1c3` | 0.33390591 |
| 5 | `earth-lanzarote` | 0.38676544 |

Top-1 contributions (target = Moon reference value):

| Feature | Target | Earth | Normalised delta | Weight | Contribution | Share |
|---|---:|---:|---:|---:|---:|---:|
| `slope_mean_deg` | 8.161142 | 10.002493 | 0.184135 | 1 | 0.000382856 | 0.1201 |
| `slope_p90_deg` | 13.98682 | 21.288951 | 0.486809 | 1 | 0.002675955 | 0.8395 |
| `terrain_relief_p95_p05_m` | 162.242714 | 178.591722 | 0.081745 | 1 | 0.000075454 | 0.0237 |
| `roughness_std_m` | 48.449056 | 47.505954 | −0.047155 | 1 | 0.000025108 | 0.0079 |
| `flat_terrain_fraction` | 0.2525 | 0.24 | −0.05 | 1 | 0.000028229 | 0.0089 |

`D01`: `q_D01 = Σ(v_f z_f²)/Σ(v_f) = 0.0564589`, `distance = sqrt(q_D01) = 0.23761075`.
The top-1 driver is the steep-tail slope metric, not the mean slope.

## Actual Mars run

Target: committed reference `mars-jezero-ctx-window-01` (Mars 2020 Jezero CTX DEM, 20 m
source, processed to the same fine D01 method). Same 113 Earth candidates and weights.

```bash
uv run python scripts/cross_body_search_170.py --body Mars --limit 5
```

| Rank | Earth candidate | Distance |
|---:|---|---:|
| 1 | `earth-namib-r1c1` | 0.62457030 |
| 2 | `earth-sahara-r3c3` | 0.65856127 |
| 3 | `earth-haughton-r3c3` | 0.69412986 |
| 4 | `earth-sahara-r1c3` | 0.73164910 |
| 5 | `earth-namib-r2c3` | 0.75532525 |

Top-1 contributions:

| Feature | Target | Earth | Normalised delta | Weight | Contribution | Share |
|---|---:|---:|---:|---:|---:|---:|
| `slope_mean_deg` | 11.696354 | 9.275477 | −0.242088 | 1 | 0.004572336 | 0.0300 |
| `slope_p90_deg` | 15.909283 | 14.967193 | −0.062806 | 1 | 0.000307748 | 0.0020 |
| `terrain_relief_p95_p05_m` | 356.602468 | 117.734733 | −1.194339 | 1 | 0.111287822 | 0.7313 |
| `roughness_std_m` | 21.266199 | 27.999530 | 0.336667 | 1 | 0.008842857 | 0.0581 |
| `flat_terrain_fraction` | 0.0 | 0.1475 | 0.59 | 1 | 0.027157931 | 0.1785 |

`D01`: `q_D01 = 0.3900881`, `distance = 0.62457030`. The Mars target's measured
`flat_terrain_fraction = 0.0` is a real zero, not a missing value; the Earth candidate's
0.1475 dominates less than the relief contrast.

## Weights

Feature weights are relative within a dimension. Dimension weights are applied after the
within-dimension feature-weighted mean:

```text
z_f     = (x_f - t_f) / s_f
q_D01   = Σ(v_f * z_f²) / Σ(v_f)          # compared positive-weight features only
distance = sqrt(Σ(w_D * q_D) / Σ(w_D))     # active dimensions only
```

Examples:

```bash
# Feature weight: mean slope counts 5x, flat fraction is not compared.
uv run python scripts/cross_body_search_170.py --body Moon \
  --weight slope_mean_deg=5 --weight flat_terrain_fraction=0 --limit 3

# Dimension weight: explicit D01 weight 2.0.
# With only D01 requested the weight cancels in the cross-dimension mean, so the
# distance is unchanged; the payload still reports the requested/active weight.
uv run python scripts/cross_body_search_170.py --body Moon \
  --dimension-weight D01=2 --limit 3

# Disabling the only active dimension is refused, not silently zero-scored.
uv run python scripts/cross_body_search_170.py --body Moon --disable-dimension D01
# -> error: zero_total_dimension_weight
```

The output `weights.feature_weights` reports requested weight, normalised weight, dimension,
comparison status and the registry scale. Zero-weight features are not compared and never
exclude a candidate. Disabled dimensions do not contribute.

## Missing, excluded and incompatible values

The payload reports `excluded[]` entries with `reasons`, `missing_features`,
`incompatible_features` and, for G1 refusals, `gate_reasons`. Missing is never zero-filled.
A positive-weight missing feature excludes the candidate with
`missing_feature:<feature_id>`. A present but incompatible pair is refused by G1 with
`g1:<reason_code>` before the shared ranker sees it.

The on-demand 250 m planetary AOI path is explicitly refused:

```bash
uv run python - <<'PY'
from analog_api.domain.cross_body_search import target_from_values, TargetFeatureValue
from analog_pipelines.planetary_aoi import analyze_planetary_aoi
from analog_pipelines.coarse_features import COARSE_FEATURE_BY_ID

aoi = analyze_planetary_aoi("Moon", {"west": 19.85, "south": -10.15, "east": 20.15, "north": -9.85})
values = {
    fid: TargetFeatureValue(value=value, unit=COARSE_FEATURE_BY_ID[fid].unit,
                            method_version=COARSE_FEATURE_BY_ID[fid].method_version)
    for fid, value in aoi.features.items()
}
target_from_values(body="Moon", source_id=aoi.area_id, values=values,
                   source_kind="on_demand_planetary_aoi")
# resolve -> CrossBodySearchError('coarse_fine_method_not_comparable')
PY
```

## Provenance

Every result carries:

- target body, source id, reference id, lat/lon, planetary CRS, window and analysis resolution;
- dataset id(s), provider/product, native resolution, processing version and licence;
- feature value, unit, method version, registry selector/support and G1 contract;
- Earth candidate id, bbox, quality flags and D01 observation id (`d01:<area>:<feature>`);
- registry version, G1 gate version, data version, scoring version/formula and candidate pool
  size.

The target's `earth_wgs84` field is always `false`; Moon/Mars coordinates remain in their
body-fixed frames.

## Verification

```bash
uv run pytest apps/api/tests/test_cross_body_search.py -q
uv run pytest apps/api/tests/test_cross_body.py apps/api/tests/test_cross_body_170.py -q
uv run pytest apps/api/tests -q
uv run ruff check apps/api/src/analog_api/domain/cross_body_search.py \
  apps/api/tests/test_cross_body_170.py apps/api/tests/test_cross_body_search.py \
  scripts/cross_body_search_170.py
uv run ruff format --check apps/api/src/analog_api/domain/cross_body_search.py \
  apps/api/tests/test_cross_body_170.py apps/api/tests/test_cross_body_search.py \
  scripts/cross_body_search_170.py
```

## Limitations

- The Earth candidate pool is 113 curated fine windows, not a global survey. Absence from the
  result is not evidence that a location is a poor analog.
- The Moon/Mars targets are processed reference ROIs, not global planetary coverage.
- Native source resolutions differ (LOLA 5 m, CTX 20 m, Copernicus GLO-90). The shared gate is
  method/analysis-grid/window, not native-pixel equivalence.
- D01 terrain shape does not imply geologic, mineralogical or environmental equivalence.
- This path deliberately emits no D02–D06 cross-body similarity and no six-dimension total.
- The shared ranker returns per-feature `contribution` values and a within-dimension
  `contribution_fraction`; the dimension-level `q_D01` is the quantity used in the distance.
  Do not present the per-feature contribution sum as a second score.
