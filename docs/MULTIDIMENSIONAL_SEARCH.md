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
typed areas: 73
indexed fine candidates: 113 (+3 on-demand AOIs in the live demo database)
typed areas exactly matching a fine candidate: 9
  earth-atacama-desert, earth-death-valley, earth-devon-island,
  earth-grand-canyon-r2c2, earth-iceland-r2c2, earth-lanzarote,
  earth-namib-r2c2, earth-rio-tinto, earth-sahara-r1c1
typed areas exactly matching a planetary reference: 9
  mars-jezero-ctx-window-01..05, moon-shackleton-rim-site04-roi1..4
proximity_join_used: false
```

D06 values exist for eight of the nine exact-joined Earth typed areas (not Rio Tinto), so the
scored D06 subset is eight candidates. The other 105 indexed candidates (108 including the three
on-demand AOIs in the live database) are excluded only when a D06 feature has a positive weight;
with weight 0 they stay eligible. This is the required “missing ≠ zero” behavior.

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
| 1 | `earth-lanzarote` | 0.12049 | 0.00314 | 0.01138 | 230.1667 | `soilgrids:earth-lanzarote:clay:0-5cm:mean` |
| 2 | `earth-devon-island` | 0.44244 | 0.14085 | 0.05490 | 266.2689 | `soilgrids:earth-devon-island:clay:0-5cm:mean` |
| 3 | `earth-namib-r2c2` | 0.46526 | 0.02648 | 0.18999 | 76.7143 | `soilgrids:earth-namib-r2c2:clay:0-5cm:mean` |
| 4 | `earth-iceland-r2c2` | 0.47889 | 0.12660 | 0.10273 | 109.3438 | `soilgrids:earth-iceland-r2c2:clay:0-5cm:mean` |
| 5 | `earth-sahara-r1c1` | 0.58696 | 0.34217 | 0.00234 | 213.6944 | `soilgrids:earth-sahara-r1c1:clay:0-5cm:mean` |
| 6 | `earth-death-valley` | 0.65939 | 0.30445 | 0.13034 | 97.8857 | `soilgrids:earth-death-valley:clay:0-5cm:mean` |
| 7 | `earth-atacama-desert` | 0.74567 | 0.49879 | 0.05724 | 132.3333 | `soilgrids:earth-atacama-desert:clay:0-5cm:mean` |
| 8 | `earth-grand-canyon-r2c2` | 1.57575 | 2.41438 | 0.06861 | 125.9125 | `soilgrids:earth-grand-canyon-r2c2:clay:0-5cm:mean` |

The D06 contribution is recomputable from the returned fields:

```text
contribution = weight * ((candidate - target) / scale)^2 / total_weight
             = 1 * ((230.1666667 - 200) / 200)^2 / 2
             = 0.0113753472
```

D01-only comparison, restricted to the same eight candidates with a D06 hard filter:

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
| 1 | earth-lanzarote | earth-lanzarote |
| 2 | earth-namib-r2c2 | **earth-devon-island** |
| 3 | earth-iceland-r2c2 | **earth-namib-r2c2** |
| 4 | earth-devon-island | **earth-iceland-r2c2** |
| 5 | earth-death-valley | **earth-sahara-r1c1** |
| 6 | earth-sahara-r1c1 | **earth-death-valley** |
| 7 | earth-atacama-desert | earth-atacama-desert |
| 8 | earth-grand-canyon-r2c2 | earth-grand-canyon-r2c2 |

D06 flips the Death Valley/Sahara pair and also reorders Devon/Namib/Iceland. This is the required
real, non-D01 non-zero contribution; the query is not a six-dimensional total.

## Missing, zero weight and incompatible values

```bash
# Positive D06 weight: candidates without a gated value are excluded with
# missing_feature:soil_clay_0_5cm_mean.
curl ... -d '{"targets":{"slope_mean_deg":{"value":10,"weight":1},"soil_clay_0_5cm_mean":{"value":200,"weight":1}}}'
# eligible=8, excluded=108 (105 indexed-only candidates + 3 on-demand AOIs)

# Zero D06 weight: the same candidates stay eligible and D06 is not compared.
curl ... -d '{"targets":{"slope_mean_deg":{"value":10,"weight":1},"soil_clay_0_5cm_mean":{"value":200,"weight":0}}}'
# eligible=116, excluded=0
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
gated candidate count for every 0–5 cm mean is 8; deeper means are still 5 because the expansion
release acquired only 0–5 cm. See `pipelines/pilots/d06_earth_soilgrids_expansion_01/`. See
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

The 565 typed rows are read from SQLite and materialised for the exact candidate ids at request
time. This keeps the source `score_role`, observation ids, checksums and provenance untouched;
materialisation is deterministic (same rows + registry version → same values) and bounded
(113 candidates, 520 rows, no raster I/O). A future persisted index can reuse the same
`materialize()` contract without changing the API.
