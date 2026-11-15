# Goal G — Cross-Body Similarity (Issue #170)

Scope: Moon/Mars fine AOI/reference targets to Earth candidate search, with explicit comparability gates. This document describes the Goal G-owned implementation in this worktree (`apps/api/src/analog_api/domain/cross_body.py`, `apps/api/src/analog_api/domain/cross_body_search.py`, `apps/api/tests/test_cross_body_170.py`, `scripts/cross_body_search_170.py`, `docs/CROSS_BODY_170.md`). Shared scientific/scoring sources of truth were not modified.

## 1. Common feature / support / method / resolution gate (D01 priority; D06 only if legal)

- **D01 (priority, comparable fine set):** the common cross-body feature set is the five fine terrain-shape metrics registered in `apps/api/src/analog_api/domain/features.py`: `slope_mean_deg`, `slope_p90_deg`, `terrain_relief_p95_p05_m`, `roughness_std_m`, `flat_terrain_fraction`.
- **Method:** same registered method versions per feature (`terrain-metrics-1.0.0` for slope/relief/roughness, `flat-terrain-fraction-1.0.0` for flat fraction). Different method versions/distances are never mixed.
- **Support/resolution:** fixed fine support only — `analysis_resolution_m == 90`, `window_size_m == 1980` (1.98 km, 22×22 at 90 m), fixed-window contract. Native source resolutions may differ by provider/body, but analysis support/method must satisfy the gate.
- **Frame/CRS policy:** projected metric body-fixed frame required for D01 terrain metrics; Earth WGS84 is rejected for planetary Moon/Mars samples/targets. Moon coordinates remain planetary frames, never Earth WGS84.
- **D06:** only legal when a feature is registered, body-legal, and comparable by the same quantity/method/support/unit/value-origin contract. In current data, Moon/Mars have no registered real D06 regolith measurements and Earth SoilGrids means are Earth-only model predictions on different support/method/value origin; therefore D06 is excluded from cross-body Moon/Mars→Earth similarity and never scored. Missing/no-coverage/unsupported D06 evidence remains explicit and is never zero-filled or transferred.

Implementation: pairwise/feature contract gate in `domain/cross_body.py` (`evaluate_pair`, `feature_gate_payload`, D01/D06 contracts); search-target G1-compatible subset in `domain/cross_body_search.py` (`g1_compatible_feature_ids`, `resolve_cross_body_target`).

## 2. Scale / resolution / normalization-comparability gate (no fine/coarse mixing)

- Earth fine 90 m / 1.98 km and planetary global/coarse products are not directly mixed or normalized into one comparable distance.
- Coarse 250 m / 5 km products (`coarse_*`, `coarse-planetary-dem-horn-1.0.0`, 250 m analysis / 5 km cell support) are rejected against fine 90 m / 1.98 km targets/candidates with explicit reasons (`coarse_resolution_not_comparable`, `coarse_support_not_comparable`, `support_kind_mismatch`, `analysis_resolution_mismatch`, `coarse_fine_method_not_comparable`).
- No cross-scale normalization/correction factor is invented. A comparison is legal only when the same quantity, method version, analysis resolution, spatial support, unit/value origin, and frame class agree for the compared pair/feature.

## 3. Dimension / feature weight compatibility (#141/#160-aligned contracts)

- Weight/eligibility handling follows the existing registry/multidim contracts: only registry-legal, G1-compatible D01 similarity features may be requested as comparable targets.
- Feature weights must be finite and >= 0; unknown/targetless weights are rejected. Dimension controls (`DimensionControl`) and scoring version are resolved through `analog_api.domain.multidim.resolve_targets` / `rank_candidates` semantics.
- **Missing/incompatible values are never treated as zero.** A positive-weight compared feature that is missing or incompatible causes the candidate to be excluded with explicit reasons (`missing_feature:*`, method/unit/support/value mismatches, etc.). Zero-weight/not-compared features do not contribute and do not exclude.
- Distances/contributions are weighted normalized query distances over explicitly gated features only (registry fixed scales). This is not a cross-body six-dimension total score, not a similarity percentage, and not physical/environmental equivalence.

## 4. Earth candidate search test (Moon/Mars AOI target -> Earth ranking)

- Targets are Moon/Mars fine D01 vectors from committed planetary references/manifests (e.g. `moon-lola-site01-roi1`, `mars-jezero-ctx-window-01`) with preserved provenance (body, reference/source id, dataset ids, lat/lon, CRS, window/resolution, quality flags, method versions).
- Candidate pool is the real curated fine Earth 90 m index from `pipelines/earth/manifests/copernicus_glo90_earth.json` (seeded into a temporary offline SQLite DB for tests/scripts).
- Output includes: ranked Earth candidates with `distance`, per-feature `contributions` (target/candidate/delta/scale/weight/normalized delta/contribution/fraction, observation/provenance support), `dimension_contributions`, `weights`, `scoring`, `coverage`, `excluded` candidates with machine-readable missing/incompatible reasons, and target/candidate provenance + limitations/scope notes.
- Reproducible script: `uv run python scripts/cross_body_search_170.py --body Moon --limit 5` or `--body Mars ...` (offline, committed manifests/fixtures only).

## 5. Verification

- `apps/api/tests/test_cross_body.py` — pairwise cross-body comparability gate (D01 comparable pairs, coarse/fine rejection, D06 cross-body denial/legal Earth-only cases, provenance/reasons).
- `apps/api/tests/test_cross_body_170.py` — Goal G gates, D06 exclusion, no coarse/fine/method mixing, weight/missing/incompatible behavior, and actual Moon/Mars target → Earth ranking with contributions/missing/provenance.
- Example focused run:

```bash
PYTHONPATH=apps/api/src uv run pytest apps/api/tests/test_cross_body.py apps/api/tests/test_cross_body_170.py -q
```

## Limitations / non-claims

- Bounded/curated reference and candidate coverage only; not global, not exhaustive.
- D01 terrain-shape comparison only under shared fine method/support; no geologic/mineralogical/environmental equivalence claim.
- No cross-body six-dimension total score is produced.
- No coarse/fine mixing, no cross-method mixing, no zero-fill for missing/incompatible values, no fabricated Moon/Mars D06 measurements.
