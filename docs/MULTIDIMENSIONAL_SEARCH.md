# Multi-dimensional search and feature eligibility (Issue #116)

This document is the backend contract for the D01–D06 query path. The UI controls are owned by the
frontend collaborators; this API is the stable source of truth for feature options, ranges, roles,
coverage and per-feature contributions.

## What is new

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/v1/search/features` | Versioned feature registry with legal roles, body scope, units, selectors, QA, actual coverage and unsupported reasons. |
| `GET` | `/api/v1/search/features/{feature_id}` | One registry entry plus the join audit. |
| `POST` | `/api/v1/search/multidimensional` | Deterministic multi-feature ranking with per-feature/per-dimension contributions. |
| `GET` | `/api/v1/search` (unchanged) | Existing D01 `POST /api/v1/search`; preserved byte-for-byte at the response-shape level. |

Registry version: `feature-eligibility-1.0.0`.

Per-feature query roles for every indexed D01–D06 feature are tabulated in
[`SIXDIM_QUERY_ROLES.md`](SIXDIM_QUERY_ROLES.md).

## Source observation vs query eligibility

Source observations keep their acquisition `score_role`. The D06 pilot records are still stored as
`evidence_only`. The registry can promote an explicitly gated subset to a **query** role without
rewriting the source row:

- `soil_<property>_<depth>cm_mean` → `similarity` + `hard_filter` (Earth only);
- `soil_<property>_<depth>cm_q05` / `q95` → `uncertainty` evidence for the matching mean;
- D02 map units / detections / spectral indices → `evidence` (map units explicitly unsupported for
  similarity until a taxonomy crosswalk exists);
- D03 / D04 planetary records → `evidence`/`context`, never a cross-body equivalence;
- D05 solar geometry/radiation → `context`;
- D01 terrain metrics → `similarity` + `hard_filter`.

`GET /api/v1/search/features` returns six named role slots for every feature:
`similarity`, `hard_filter`, `category_match`, `evidence`, `uncertainty`, `context`. Each slot has
`supported`, `reason` and `constraints`.

## Exact join audit

The only join key is:

```text
typed_areas.area_id == candidates.location_id   (exact string equality)
```

No nearest-point or proximity merge is performed. Current audit from the committed data:

```text
typed areas: 177
indexed fine candidates: 113 (+3 on-demand AOIs in the live demo database)
typed areas exactly matching a fine candidate: 113
  all 113 committed Earth fine windows (exact area_id join)
typed areas exactly matching a planetary reference: 9
  mars-jezero-ctx-window-01..05, moon-shackleton-rim-site04-roi1..4
proximity_join_used: false
```

D06 0–5 cm values now exist for all 113 committed Earth fine windows, so the scored D06 subset is
113 candidates. The three on-demand AOIs in the live database (which have no D06 release) are
excluded only when a D06 feature has a positive weight; with weight 0 they stay eligible. Deeper
depths remain limited to the five original AOIs. This is the required “missing ≠ zero” behavior.

## D06 gate

A D06 mean can contribute only when all of these hold:

1. `body == Earth` and the source area id exactly equals a fine candidate id;
2. `dimension_code == D06`, `state == detected`;
3. `value` is a finite JSON number (a true `0` is valid; `null` is missing);
4. `unit` exactly equals `g kg-1` (clay/sand/silt), `dg kg-1` (SOC) or `cg cm-3` (bulk density);
5. `method_version == soilgrids-wcs-2.0.0`;
6. `depth_support` exactly matches the requested feature's interval (0–5, 5–15, 15–30 or 30–60 cm);
7. the stored AOI bbox equals the candidate bbox within `1e-6` degree in `EPSG:4326`;
8. the observation is unique for that exact area/feature (otherwise `ambiguous_duplicate_values`).

Mean, Q05 and Q95 share a `measurement_family` such as `d06:soilgrids:clay:0_5`. They are not three
independent observations. Q05/Q95 are attached to the mean contribution as `uncertainty`; they are
never scored as separate features. Depths are never pooled.

D06 support is **250 m model prediction aggregated over the AOI envelope**, not a local soil sample
and not the same support as the 90 m terrain window. The API states this in every contribution and
in the response limitations.

## Real example: a non-D01 feature changes the ranking

Request:

```bash
curl -sS https://api.貓熊.台灣/api/v1/search/multidimensional \
  -H 'Content-Type: application/json' \
  -d '{
    "candidate_body": "Earth",
    "targets": {
      "slope_mean_deg":        {"value": 10,  "weight": 1},
      "soil_clay_0_5cm_mean":  {"value": 200, "weight": 1}
    },
    "limit": 10
  }'
```

Observed result (data snapshot `dv-b46f3bad38ea`):

| Rank | Candidate | Combined distance | D01 contribution | D06 contribution | Soil clay (g/kg) | Source observation |
|---:|---|---:|---:|---:|---:|---|
| 1 | `earth-devon-polar-r3c1` | 0.03632 | 0.00121 | 0.00010 | 202.8939 | `soilgrids:earth-devon-polar-r3c1:clay:0-5cm:mean` |
| 2 | `earth-lanzarote` | 0.12049 | 0.00314 | 0.01138 | 230.1667 | `soilgrids:earth-lanzarote:clay:0-5cm:mean` |
| 3 | `earth-sahara-r1c3` | 0.13834 | 0.00369 | 0.01545 | 235.1528 | `soilgrids:earth-sahara-r1c3:clay:0-5cm:mean` |
| 4 | `earth-sahara-r1c2` | 0.16266 | 0.01372 | 0.01273 | 231.9167 | `soilgrids:earth-sahara-r1c2:clay:0-5cm:mean` |
| 5 | `earth-devon-polar-r1c1` | 0.17393 | 0.00148 | 0.02877 | 247.9773 | `soilgrids:earth-devon-polar-r1c1:clay:0-5cm:mean` |
| 6 | `earth-grand-canyon-r3c1` | 0.21410 | 0.04538 | 0.00047 | 193.9000 | `soilgrids:earth-grand-canyon-r3c1:clay:0-5cm:mean` |
| 7 | `earth-devon-polar-r3c3` | 0.21682 | 0.00153 | 0.04548 | 139.6818 | `soilgrids:earth-devon-polar-r3c3:clay:0-5cm:mean` |
| 8 | `earth-sahara-r2c2` | 0.22059 | 0.02522 | 0.02344 | 243.3056 | `soilgrids:earth-sahara-r2c2:clay:0-5cm:mean` |
| 9 | `earth-altiplano-r3c3` | 0.22339 | 0.04716 | 0.00275 | 185.1806 | `soilgrids:earth-altiplano-r3c3:clay:0-5cm:mean` |
| 10 | `earth-devon-polar-r3c2` | 0.22510 | 0.00000 | 0.05067 | 263.6667 | `soilgrids:earth-devon-polar-r3c2:clay:0-5cm:mean` |

The D06 contribution is recomputable from the returned fields:

```text
contribution = weight * ((candidate - target) / scale)^2 / total_weight
             = 1 * ((230.1666667 - 200) / 200)^2 / 2
             = 0.0113753472
```

D01-only comparison, restricted to the same 113 candidates with a D06 hard filter:

```bash
curl -sS https://api.貓熊.台灣/api/v1/search/multidimensional \
  -H 'Content-Type: application/json' \
  -d '{
    "targets": {"slope_mean_deg": {"value": 10, "weight": 1}},
    "filters": {"soil_clay_0_5cm_mean": {"min": 0, "max": 1000}},
    "limit": 10
  }'
```

| Rank | D01-only | D01+D06 |
|---:|---|---|
| 1 | earth-devon-polar-r3c2 | **earth-devon-polar-r3c1** |
| 2 | earth-devon-polar-r3c1 | **earth-lanzarote** |
| 3 | earth-devon-polar-r1c1 | **earth-sahara-r1c3** |
| 4 | earth-devon-polar-r3c3 | **earth-sahara-r1c2** |
| 5 | earth-iceland-r1c1 | **earth-devon-polar-r1c1** |
| 6 | earth-namib-r1c1 | **earth-grand-canyon-r3c1** |
| 7 | earth-lanzarote | **earth-devon-polar-r3c3** |
| 8 | earth-sahara-r1c3 | **earth-sahara-r2c2** |
| 9 | earth-namib-r2c3 | **earth-altiplano-r3c3** |
| 10 | earth-haughton-r3c3 | **earth-devon-polar-r3c2** |

D06 changes the top-1 and the full top-10 order. This is the required real, non-D01 non-zero
contribution; the query is not a six-dimensional total.

## Missing, zero weight and incompatible values

```bash
# Positive D06 weight: candidates without a gated value are excluded with
# missing_feature:soil_clay_0_5cm_mean.
curl ... -d '{"targets":{"slope_mean_deg":{"value":10,"weight":1},"soil_clay_0_5cm_mean":{"value":200,"weight":1}}}'
# eligible=113, excluded=3 (the three on-demand AOIs have no D06 release)

# Zero D06 weight: the same candidates stay eligible and D06 is not compared.
curl ... -d '{"targets":{"slope_mean_deg":{"value":10,"weight":1},"soil_clay_0_5cm_mean":{"value":200,"weight":0}}}'
# eligible=116, excluded=0 (all candidates remain when the feature has weight 0)
```

Other hard failures are explicit: `unit_mismatch`, `method_version_mismatch`,
`depth_interval_mismatch`, `spatial_bbox_mismatch`, `state:unknown`, `state:no_coverage`,
`value_not_finite_numeric`, `ambiguous_duplicate_values`,
`no_exact_candidate_location_id`.

## Cross-body rejection

A Moon/Mars reference may use D01 terrain metrics, but it cannot borrow Earth-only D06 soil targets:

```bash
curl -sS https://api.貓熊.台灣/api/v1/search/multidimensional \
  -H 'Content-Type: application/json' \
  -d '{
    "reference_id": "moon-lola-site01-roi1",
    "targets": {"soil_clay_0_5cm_mean": {"value": 200, "weight": 1}}
  }'
```

```json
{
  "error": {
    "code": "body_out_of_scope",
    "message": "soil_clay_0_5cm_mean is registered for Earth only; candidate body Moon is incompatible.",
    "details": [{
      "feature_id": "soil_clay_0_5cm_mean",
      "candidate_body": "Moon",
      "body_scope": ["Earth"],
      "reason": "cross_body_incompatible"
    }]
  }
}
```

Evidence-only targets are rejected with `feature_role_not_scoreable` and the legal roles:

```json
{
  "error": {
    "code": "feature_role_not_scoreable",
    "message": "geologic_unit is not registered for weighted similarity. Geologic map-unit names are dataset-specific free text. No versioned taxonomy crosswalk exists, so map units cannot enter categorical or weighted similarity.",
    "details": [{"legal_roles": ["evidence"]}]
  }
}
```

## Coverage, pagination and ranking scope

`POST /api/v1/search/multidimensional` accepts `limit` (1–50) and `offset`. The response includes
`eligible_candidate_count`, `total_matched`, `offset`, `limit`, `returned`, `next_offset` and a
coverage block per requested feature with `eligible_candidate_count`, `candidate_pool_size`,
`coverage_fraction` and `scored_result_count`. Coverage counts gated values, not typed-record counts.

`distance` is a weighted normalized query distance. `distance_scope` and `limitations` state that it
is **not** a six-dimension scientific total, not a similarity percentage and not a cross-body
equivalence. Per-dimension contributions and per-feature contribution fractions are returned so the
frontend can display the actual drivers instead of a fabricated total.

## D06 feature inventory

The registry exposes 20 scored D06 means and 40 Q05/Q95 uncertainty records:

```text
properties: bdod, clay, sand, silt, soc
depths:     0–5, 5–15, 15–30, 30–60 cm
means:      20 similarity + hard_filter features
quantiles:  40 uncertainty/evidence records (same measurement family as the mean)
```

All 60 D06 feature ids are present in the registry, but only the 20 means are scoreable. The actual
gated candidate count for every 0–5 cm mean is 113; deeper means are still 5 because the expansion
releases acquired only 0–5 cm. See `pipelines/pilots/d06_earth_soilgrids_expansion_01/`. See
[`FEATURE_ELIGIBILITY_AUDIT.md`](FEATURE_ELIGIBILITY_AUDIT.md) for the full table.

## Frontend handoff

- Use `GET /api/v1/search/features?role=similarity&body=Earth` to build controls; do not hardcode
  metric names, units, ranges or scales.
- Read `roles[...]` to decide whether a feature belongs in similarity, a hard filter, an evidence
  panel or an uncertainty display.
- Read `selector` and `support` to label depth/support correctly.
- `POST` the same feature ids to `/api/v1/search/multidimensional`; render `contributions[]`,
  `dimension_contributions` and `coverage` directly.
- Generated TypeScript types: `apps/web/src/api/schema.d.ts` from `contracts/openapi.json`.

## Reproduce locally

```bash
make seed
uv run pytest apps/api/tests/test_feature_eligibility.py apps/api/tests/test_multidim_api.py -q
uv run python scripts/audit_feature_eligibility.py --output docs/FEATURE_ELIGIBILITY_AUDIT.md
bash scripts/verify.sh --ci
```

## Semantic and join audit (same name ≠ same quantity)

| Feature / group | Bodies | Stored support | Query role | Why it is not silently joined |
|---|---|---|---|---|
| D01 slope/relief/roughness/flat fraction | Earth, Moon, Mars | 90 m projected windows, fixed 1.98 km | similarity/filter | Comparable only within the registered terrain method; planetary references are processed ROIs, not global coverage. |
| D02 `geologic_unit` | Moon, Mars (and Earth availability records) | provider map-unit geometry, free-text code/name | evidence | No versioned taxonomy/crosswalk; `Ec` on the Moon is not an Earth ecological class. |
| D02 mineral detections / spectral indices | Mars, Moon | single-provider detections/indices, mixed states | evidence/uncertainty | Detection state and index support are not validated against fine 90 m Earth candidates. |
| D03 `surface_brightness_temperature_k` | Moon (Diviner), Mars (THEMIS/HEND bundles) | 240 m / 100 m windows, instrument-specific methods | evidence | Different instruments, wavelengths, time-of-day and native support; no shared Earth method. |
| D03 Earth reanalysis time series | Earth | 50 km point support, daily timestamps | context | Temporal selector and spatial support are not the 90 m candidate window; time-series aggregation is not versioned for ranking. |
| D04 water/ice detections, hydrogen, CPR | Moon, Mars | body-specific remote sensing windows | evidence | Hydrogen equivalent water, radar CPR, neutron flux and Earth soil wetness are different physical quantities. |
| D04 Earth precipitation/wetness/snow | Earth | reanalysis point/time series | context | Different quantity and support from D06 soil predictions; not weighted. |
| D05 solar geometry/irradiance | Earth, Moon, Mars | SPICE geometry or power time series | context | Illumination depends on a time selector and body ephemerides; it is not a surface-property similarity. |
| D06 SoilGrids means | Earth | 250 m model cells, AOI envelope mean, 4 depths | similarity/filter | Only exact area-ID joins, same provider/method/depth/unit/bbox; 250 m model support is not 90 m terrain support. |
| D06 SoilGrids Q05/Q95 | Earth | same model family, same depth | uncertainty only | Prediction quantiles, not independent observations; attached to the matching mean. |
| ESA WorldCover fractions | Earth | 10 m land-cover context | context/filter (existing `/search` only) | Earth-only hard filter; never D07 and never part of the weighted distance. |

### Materialisation is query-time, not a source rewrite

The 2,140 typed rows are read from SQLite and materialised for the exact candidate ids at request
time. This keeps the source `score_role`, observation ids, checksums and provenance untouched;
materialisation is deterministic (same rows + registry version → same values) and bounded
(113 candidates, 2,140 rows, no raster I/O). A future persisted index can reuse the same
`materialize()` contract without changing the API.


## Issue #141 scoring extension (backward compatible)

`POST /api/v1/search/multidimensional` accepts optional:

- `dimension_weights`: `{ "D01": {"weight": 1.0, "enabled": true}, ... }`
- `scoring_version`: `"feature-weighted-euclidean-v1"` (legacy, default when no dimension controls) or `"dimension-weighted-rms-v1"`

When dimension controls are provided, scoring defaults to `dimension-weighted-rms-v1`:

- per-feature normalized delta: `z_df = (x_df - t_df) / s_df`
- per-dimension feature-weighted mean: `q_d = Σ(v_df * z_df^2) / Σ(v_df)` over compared positive-weight features in `d`
- cross-dimension weighted RMS: `distance = sqrt(Σ(w_d * q_d) / Σ(w_d))` over enabled dimensions with `w_d > 0`

Properties:
- no hidden feature-count bias (each active dimension contributes by its mean, not by raw feature count)
- `enabled=false` or `weight=0` dimensions and `weight=0` features do not contribute and do not exclude candidates
- positive-weight missing/incompatible/QA-gated features in active dimensions still exclude with explicit reasons
- responses include `scoring` metadata (version, formula, dimension controls, active dimensions) and rich per-dimension/per-feature contributions

Legacy `/api/v1/search` and default `/api/v1/search/multidimensional` behavior remain unchanged.
