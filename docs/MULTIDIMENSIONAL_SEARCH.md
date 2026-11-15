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
typed areas: 70
fine candidates: 113
typed areas exactly matching a fine candidate: 6
  earth-atacama-desert, earth-death-valley, earth-devon-island,
  earth-lanzarote, earth-rio-tinto, earth-sahara-r1c1
typed areas exactly matching a planetary reference: 9
  mars-jezero-ctx-window-01..05, moon-shackleton-rim-site04-roi1..4
proximity_join_used: false
```

D06 values exist for five of the six Earth typed areas (not Rio Tinto), so the scored D06 subset is
five candidates. The other 108 fine candidates are excluded only when a D06 feature has a positive
weight; with weight 0 they stay eligible. This is the required “missing ≠ zero” behavior.

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
    "limit": 5
  }'
```

Observed result (data snapshot `dv-b46f3bad38ea`):

| Rank | Candidate | Combined distance | D01 contribution | D06 contribution | Soil clay (g/kg) | Source observation |
|---:|---|---:|---:|---:|---:|---|
| 1 | `earth-lanzarote` | 0.12049 | 0.00314 | 0.01138 | 230.1667 | `soilgrids:earth-lanzarote:clay:0-5cm:mean` |
| 2 | `earth-devon-island` | 0.44244 | 0.14125 | 0.05458 | 266.2689 | `soilgrids:earth-devon-island:clay:0-5cm:mean` |
| 3 | `earth-sahara-r1c1` | 0.58704 | 0.34222 | 0.00247 | 213.6944 | `soilgrids:earth-sahara-r1c1:clay:0-5cm:mean` |
| 4 | `earth-death-valley` | 0.65939 | 0.30411 | 0.13035 | 97.8857 | `soilgrids:earth-death-valley:clay:0-5cm:mean` |
| 5 | `earth-atacama-desert` | 0.74572 | 0.49885 | 0.05723 | 132.3333 | `soilgrids:earth-atacama-desert:clay:0-5cm:mean` |

The D06 contribution is recomputable from the returned fields:

```text
contribution = weight * ((candidate - target) / scale)^2 / total_weight
             = 1 * ((230.1666667 - 200) / 200)^2 / 2
             = 0.0113753472
```

D01-only comparison, restricted to the same five candidates with a D06 hard filter:

```bash
curl -sS https://api.貓熊.台灣/api/v1/search/multidimensional \
  -H 'Content-Type: application/json' \
  -d '{
    "targets": {"slope_mean_deg": {"value": 10, "weight": 1}},
    "filters": {"soil_clay_0_5cm_mean": {"min": 0, "max": 1000}},
    "limit": 5
  }'
```

| Rank | D01-only | D01+D06 |
|---:|---|---|
| 1 | earth-lanzarote | earth-lanzarote |
| 2 | earth-devon-island | earth-devon-island |
| 3 | earth-death-valley | **earth-sahara-r1c1** |
| 4 | earth-sahara-r1c1 | **earth-death-valley** |
| 5 | earth-atacama-desert | earth-atacama-desert |

D06 flips the Sahara/Death Valley pair. This is the required real, non-D01 non-zero contribution; the
query is not a six-dimensional total.

## Missing, zero weight and incompatible values

```bash
# Positive D06 weight: candidates without a gated value are excluded with
# missing_feature:soil_clay_0_5cm_mean.
curl ... -d '{"targets":{"slope_mean_deg":{"value":10,"weight":1},"soil_clay_0_5cm_mean":{"value":200,"weight":1}}}'
# eligible=5, excluded=108

# Zero D06 weight: the same candidates stay eligible and D06 is not compared.
curl ... -d '{"targets":{"slope_mean_deg":{"value":10,"weight":1},"soil_clay_0_5cm_mean":{"value":200,"weight":0}}}'
# eligible=113, excluded=0
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
gated candidate count for every 0–5 cm mean is 5; deeper means are also 5. See
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
