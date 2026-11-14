# Feature registry and ontology

Source of truth: `apps/api/src/analog_api/domain/features.py`. API payloads:
`GET /api/v1/dimensions` (typed catalog grouped by dimension) and `GET /api/v1/capabilities`
(UI summary). Contract mirror: `contracts/contract.json` (checked by a backend test). Feature rows
are never stored as ad-hoc SQLite columns.

## Registry rules

Every similarity feature defines: `feature_id`, `dimension_id`, `description`, `value_kind`, `unit`,
`method_version`, `spatial_support`, `temporal_support`, `provider_coverage_by_body`,
`compare_strategy`/`score_role`, `quality_rules`, fixed normalization `scale`, valid domain, and
the extraction method. A comparison requires the same `method_version`; incompatible observations
are excluded and reported, never compared silently.

The weighted distance is an **engineering matching score**, not a scientific similarity percentage.

## Similarity dimensions (Earth + verified planetary terrain ROIs)

| Feature | Dimension | Unit | Domain | Fixed scale | Role |
|---|---|---|---|---|---|
| `slope_mean_deg` | terrain_shape | degree | 0–90 | 10° | similarity |
| `slope_p90_deg` | terrain_shape | degree | 0–90 | 15° | similarity |
| `terrain_relief_p95_p05_m` | terrain_shape | meter | 0–20000 | 200 m | similarity |
| `roughness_std_m` | terrain_shape | meter | 0–5000 | 20 m | similarity |

- Support: 1.98 km × 1.98 km window (22 × 22 cells at 90 m), projected metric CRS, bilinear
  resampling, ≥80% valid cells.
- Comparability gate: same method version (`terrain-metrics-1.0.0`), same metric CRS/grid/valid
  rule. Moon ROIs use their own planetary projected frame; Moon degrees are never treated as Earth
  WGS84.
- Terrain shape is not environmental equivalence. Gravity, atmosphere, radiation, regolith,
  mineralogy, temperature, and geological history are not in the score.

## Earth land-cover context (context-only, never a similarity score)

ESA WorldCover 2021 v200 class fractions at 10 m over the AOI. `score_role: context_only`,
`compare_strategy: context_only_filter`. These are Earth land-cover annotations, **not** lunar
mineralogy, geology, or surface-material measurements, and they never enter the distance formula.

Feature ids: `landcover_tree_cover_fraction` (10), `landcover_shrubland_fraction` (20),
`landcover_grassland_fraction` (30), `landcover_cropland_fraction` (40),
`landcover_built_up_fraction` (50), `landcover_bare_sparse_fraction` (60),
`landcover_snow_ice_fraction` (70), `landcover_water_fraction` (80),
`landcover_herbaceous_wetland_fraction` (90), `landcover_mangrove_fraction` (95),
`landcover_moss_lichen_fraction` (100).

Method version: `worldcover-2021-v200-1.0.0`. Fractions use valid (non-nodata) pixels as the
denominator; classes sum to ≈1. Quality rules: valid fraction ≥0.8, 2021 epoch, 10 m source.

## NASA challenge consideration buckets

The challenge's considerations are the top-level vocabulary, not a claim of a universal ontology:

| Bucket | Status in MVP | Behavior |
|---|---|---|
| terrain / elevation / slope / roughness | **implemented** | similarity over compatible numeric features |
| geology / mineralogy / surface materials | planned / evidence-only | no fabricated numeric score; WorldCover is **not** geology |
| temperature / illumination / extremes | planned | MOD11A2/Diviner interfaces only; thermal semantics not normalized |
| water / ice / permafrost / resources | context-only (WorldCover water/snow) | Earth land-cover annotation only |
| dust / wind / erosion / volcanic or sedimentary processes | planned / evidence-only | process evidence, not a number |
| biology / extremophiles / habitability / biosignatures | not implemented | no score until authoritative data and method exist |
| caves / lava tubes / underground | planned / evidence-only | no score |
| landing / mobility / navigation / operations | planned | mission weights may reuse the same search API |

Unknown or unavailable dimensions stay `unknown`/`unavailable`; they are never zero-filled or
silently accepted. Earth context can be used as a **hard pre-ranking filter** (`min`/`max` on a
`landcover_*_fraction`); missing context fails a requested filter and never counts as a match.

## Values and missingness

`value_kind`: `numeric` (terrain), `distribution` (land-cover fractions), and reserved
`category`, `multiselect`, `boolean`, `process-evidence` for future work.

Missing behavior:

- A requested similarity feature absent from a candidate excludes that candidate with
  `missing_metric:<feature_id>`.
- A different method version excludes it with `incompatible_method_version`.
- A non-finite value is treated as missing.
- Context observations may be absent (`unknown`), marked failed in the AOI provider status, or
  marked `partial`; context absence never changes the terrain distance and is never reported as 0.
- A hard constraint (planned) filters before weighted ranking; the MVP has no hard constraints.

## Adding a dimension

Follow `docs/DATA_PIPELINE.md` and `CONTRIBUTING.md`: Issue → source/licence/method review → registry
entry with fixed scale and domain → shared extractor + synthetic known-value tests → manifest/API/UI
+ contract tests → snapshot/version bump. Bump the method/contract version if comparability changes.
