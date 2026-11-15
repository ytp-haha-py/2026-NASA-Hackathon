# D02–D05 usable controls without fake similarity (#170)

Status: **implemented on `feat/170-d02-d05-controls`**. This note is the contract for the
D02–D05 controls and the #119 frontend handoff.

## Non-claims

- Similarity remains **D01 terrain + Earth D06 SoilGrids means only**. No D02, D03, D04 or
  D05 feature is promoted to `similarity`.
- A filter selects the candidate set. It never changes a candidate's weighted distance, never
  becomes a distance contribution and never makes two different physical quantities equivalent.
- D03–D05 control values are **coarse NASA POWER model/reanalysis context** at roughly
  0.5 x 0.625 degree (~50 km), not 1.98 km local measurements.
- No value is zero-filled or interpolated. A candidate without a stored value is excluded as
  `missing_filter`, with the feature id in `missing_features`.
- Moon and Mars D02–D05 records remain evidence/context only. There is no cross-body control.
- The current pilot covers **8 Earth fine candidates out of 113**. This is not global or
  exhaustive coverage; a later data expansion can raise the denominator without changing the
  contract.

## 1. What was added

| Layer | Change |
| --- | --- |
| Registry definitions | Versioned `CONTEXT_FILTER_FEATURES` (`context-filter-1.0.0`) for ten Earth scalar D03/D04/D05 fields. |
| Eligibility | Range `hard_filter` support for those ten features; `similarity` stays `false`. `category_match` for `geologic_unit`; `hard_filter` stays `false`. |
| `/search/features` | `filter_kind`, `filter_statistic`, `roles.filter` alias, `context_filter_version`, observed filter-value range and exact coverage counts. |
| `POST /search/multidimensional` | Range `filters` now work for the ten context fields. New optional `category_filters: dict[str, list[str]]` for `geologic_unit`. |
| Materialisation | Filter-only features are materialised additively; similarity materialisation is unchanged. |
| Docs/tests | `apps/api/tests/test_context_filters_170.py`; this handoff. |

`roles.filter` is an additive read-only alias for the active filter role (`hard_filter` for
range, `category_match` for the dropdown). `roles.hard_filter` remains the canonical range-filter
role, so existing clients keep working. `GET /search/features?role=filter` is valid and returns
all 35 filterable features.

## 2. Earth scalar range filters

All ten fields share the same stored source and support:

- provider: NASA POWER daily point, dataset `nasa-power-daily-point`;
- method version: `nasa-power-daily-1.0.0`;
- body: Earth only;
- source support: ~0.5 x 0.625 degree reanalysis grid point at the AOI centre;
- time support: daily `2024-01-01..2024-01-07` (7 samples);
- filter value: arithmetic mean of the seven stored finite samples (`filter_statistic =
  "period_mean"`). The value is derived at query time; no new observation is written.
- exact `area_id == candidates.location_id` join and matching recorded pilot bbox. No
  proximity join, no interpolation.
- if any sample is `None`/non-finite, `missing_count > 0`, the unit differs, or the temporal
  interval differs, the observation is rejected with an explicit reason.

| Feature id | Dim | Unit | Filter domain | UI preset (`scale`) | Observed pilot range (8 AOIs) | Current coverage |
| --- | --- | --- | --- | ---: | --- | --- |
| `surface_air_temperature_2m_c` | D03 | `degree_Celsius` | -100.0..100.0 | 10.0 | -27.9057..22.0229 | 8/113 |
| `surface_air_temperature_2m_max_c` | D03 | `degree_Celsius` | -100.0..100.0 | 10.0 | -25.4471..30.3971 | 8/113 |
| `surface_air_temperature_2m_min_c` | D03 | `degree_Celsius` | -100.0..100.0 | 10.0 | -30.7114..18.3557 | 8/113 |
| `surface_skin_temperature_c` | D03 | `degree_Celsius` | -100.0..100.0 | 10.0 | -29.1386..25.2986 | 8/113 |
| `precipitation_mm_day` | D04 | `mm day-1` | 0.0..2000.0 | 10.0 | 0.0000..1.3300 | 8/113 |
| `root_zone_soil_wetness_fraction` | D04 | `1` | 0.0..1.0 | 0.25 | 0.1400..0.9200 | 8/113 |
| `snow_depth_cm` | D04 | `cm` | 0.0..2000.0 | 50.0 | 0.0000..48.8114 | 8/113 |
| `surface_soil_wetness_fraction` | D04 | `1` | 0.0..1.0 | 0.25 | 0.1157..0.8900 | 8/113 |
| `surface_shortwave_irradiance_kwh_m2_day` | D05 | `kWh m-2 day-1` | 0.0..12.0 | 1.0 | 0.0000..9.6299 | 8/113 |
| `surface_longwave_irradiance_kwh_m2_day` | D05 | `kWh m-2 day-1` | 0.0..12.0 | 1.0 | 3.9033..8.4978 | 8/113 |

`min_value` / `max_value` are the accepted filter domain. `scale` is a UI preset width only; it
is not a similarity scale and it never enters the distance formula. The observed pilot range is
reported by `/search/features` as `coverage.filter_value_min` / `coverage.filter_value_max` so a
client can initialise a slider without hard-coding numbers. `filter_value_count` is 8 today.
`numeric_observation_count` stays 0 because the source rows are stored timeseries, not scalar
observations; the derived filter value is deliberately reported separately.

### Exact request and response

Request:

```http
POST /api/v1/search/multidimensional
Content-Type: application/json

{
  "targets": {"slope_mean_deg": {"value": 10, "weight": 1}},
  "filters": {"surface_air_temperature_2m_c": {"max": 9.85}},
  "limit": 3
}
```

Response (abridged; real values from the committed pilots):

```json
{
  "candidate_pool_size": 113,
  "filtered_candidate_count": 4,
  "eligible_candidate_count": 4,
  "excluded_candidate_count": 109,
  "filters_applied": {
    "surface_air_temperature_2m_c": {
      "kind": "range",
      "filter_kind": "range",
      "min": null,
      "max": 9.85,
      "unit": "degree_Celsius",
      "dimension_id": "D03",
      "statistic": "period_mean",
      "support": {
        "kind": "coarse_grid_point",
        "native_resolution_m": 50000.0,
        "filter_value_semantics": "period_mean_of_stored_timeseries",
        "not_a_local_measurement": true
      }
    }
  },
  "results": [
    {"rank": 1, "location_id": "earth-iceland-r2c2", "distance": 0.5031972},
    {"rank": 2, "location_id": "earth-devon-island", "distance": 0.5307616},
    {"rank": 3, "location_id": "earth-death-valley", "distance": 0.780321}
  ],
  "excluded": [
    {
      "location_id": "earth-alps-r1c1",
      "reason": "missing_filter:surface_air_temperature_2m_c",
      "reasons": ["missing_filter:surface_air_temperature_2m_c"],
      "missing_features": ["surface_air_temperature_2m_c"],
      "incompatible_features": []
    }
  ]
}
```

The `distance` values are exactly the same as the unfiltered query for those location ids. The
filter only removed rows. `excluded` contains 105 `missing_filter` rows for the other fine
candidates and 4 `filter:` rows for the covered AOIs outside the requested range.

### Validation

| Condition | Response |
| --- | --- |
| `min > max` | 422 validation error |
| bound below `min_value` or above `max_value` | 422 `filter_out_of_domain` |
| unknown feature id | 422 `unknown_feature` |
| feature without range role (for example Moon `surface_brightness_temperature_k`) | 422 `feature_role_not_filterable` |
| the same feature posted as a target | 422 `feature_role_not_scoreable` (legal roles exclude `similarity`) |

## 3. D02 category dropdown (`geologic_unit`)

The control is a **dropdown**, not a numeric slider:

- request field: `category_filters: {"geologic_unit": ["<stored label>", ...]}`;
- matching is exact string equality against labels stored in the committed Macrostrat point
  query: `value.code`, `value.name` and each `value.units[].name`;
- no casing, spelling, taxonomy or crosswalk normalisation is applied;
- a candidate matches when **any** of its stored labels is in the requested list;
- Earth only. Moon/Mars `geologic_unit` rows (USGS map units) remain evidence and are never
  matched across bodies;
- current real coverage: **3/113 candidates**, 11 distinct stored labels. The other 110
  candidates are excluded as `missing_filter:geologic_unit`, never filled with a default unit.

`GET /api/v1/search/features` exposes the exact label list and per-label candidate count:

```json
{
  "feature_id": "geologic_unit",
  "dimension_id": "D02",
  "unit": "category",
  "filter_kind": "category",
  "roles": {
    "similarity": {"supported": false, "reason": "Geologic map-unit names are dataset-specific free text. No versioned taxonomy crosswalk exists, so map units cannot enter categorical or weighted similarity."},
    "hard_filter": {"supported": false, "reason": "Numeric range filtering is undefined for a categorical map-unit label; use category_filters instead."},
    "category_match": {"supported": true, "reason": "Exact match against the stored, source-native map-unit label. This is a dropdown lookup, not a taxonomy crosswalk and not an equivalence claim."},
    "filter": {"supported": true}
  },
  "coverage": {
    "joined_candidate_count": 3,
    "gated_candidate_count": 3,
    "category_value_counts": {
      "Basalt, Holocene": 1,
      "Cambrian-Mississippian sedimentary": 1,
      "Cenozoic sedimentary rocks": 1,
      "Cenozoic volcanic rocks": 1,
      "Holocene basalt": 1,
      "Igneous: extrusive; Extrusive: mafic": 1,
      "Mississippian, Devonian, and Cambrian sedimentary rocks": 1,
      "Paleozoic sedimentary rocks": 1,
      "Quaternary volcanic: mafic rocks": 1,
      "Redwall Limestone": 1,
      "macrostrat_point_query_units": 3
    }
  }
}
```

Request:

```json
{
  "targets": {"slope_mean_deg": {"value": 10, "weight": 1}},
  "category_filters": {"geologic_unit": ["Redwall Limestone"]},
  "limit": 3
}
```

Response (abridged):

```json
{
  "filtered_candidate_count": 1,
  "eligible_candidate_count": 1,
  "excluded_candidate_count": 112,
  "filters_applied": {
    "geologic_unit": {
      "kind": "category",
      "filter_kind": "category",
      "values": ["Redwall Limestone"],
      "unit": "category",
      "dimension_id": "D02"
    }
  },
  "results": [
    {"rank": 1, "location_id": "earth-grand-canyon-r2c2", "distance": 2.1974448}
  ],
  "excluded": [
    {
      "location_id": "earth-iceland-r2c2",
      "reason": "category:geologic_unit",
      "reasons": ["category:geologic_unit"],
      "missing_features": []
    }
  ]
}
```

Validation: an empty label list or a blank label is a 422 validation error; a feature without a
`category_match` role returns 422 `feature_role_not_category_filterable`; a label that matches no
stored unit is a legal query that returns zero results.

## 4. `/search/features` fields for #119

Additions on each `features[]` item:

| Field | Meaning |
| --- | --- |
| `filter_kind` | `"range"`, `"category"` or `null`. |
| `filter_statistic` | `"period_mean"` for the ten coarse context filters; `null` otherwise. |
| `roles.filter` | `{supported, reason, constraints}` alias of the active filter role; never a similarity role. |
| `context_filter_version` | `"context-filter-1.0.0"` when the entry belongs to the context filter registry. |
| `coverage.filter_value_count` | Number of materialised filter values (8 today). |
| `coverage.filter_value_min` / `max` | Observed range of the derived filter values for this database. |
| `coverage.category_value_counts` | Exact stored label -> candidate count for the D02 dropdown. |
| `coverage.joined_candidate_count` / `gated_candidate_count` | Exact-ID coverage in this database. |
| `coverage.candidate_pool_size` / `coverage_fraction` | Explicit denominator and `gated / pool` ratio, e.g. `8/113` for a D03 field and `3/113` for the D02 dropdown. |

Top level adds `context_filter_version`. Existing `min_value`, `max_value`, `unit`,
`roles.hard_filter`, `coverage.gated_candidate_ids` keep their previous meaning. Query
`?role=filter` returns the 35 filterable features; `?role=similarity` still returns only 25
(5 D01 + 20 Earth D06).

## 5. #119 frontend handoff

1. Render **ten range sliders** for D03/D04/D05, using:
   - `min_value`/`max_value` as the legal domain;
   - `unit` as the displayed unit;
   - `coverage.filter_value_min`/`filter_value_max` as the initial data-driven range;
   - `scale` only as a step/preset hint, never as a similarity scale;
   - `coverage.gated_candidate_count`/`joined_candidate_count` as a visible "8 of 113 candidates
     have this control" note.
2. Send sliders only in `filters`, never in `targets`. A slider must not be labelled
   "similarity", "weight" or "score".
3. Render **one D02 dropdown** from `coverage.category_value_counts` and send it in
   `category_filters`. Show "3 of 113 candidates have a stored map-unit label".
4. When `coverage.gated_candidate_count == 0`, hide or disable the control and say why. Do not
   render a slider with no data and do not substitute zero.
5. Display `excluded[].missing_features` so users can see that a control reduced the pool because
   data is absent, not because the candidate scored badly.
6. Keep Moon/Mars D02–D05 as evidence/context chips. Never show them as sliders or dropdowns.
7. Show the same distance/rank as the unfiltered query for retained candidates; filters must not
   be presented as a distance contribution.

## 6. Reproduction

```bash
uv run pytest apps/api/tests/test_context_filters_170.py -q
ANALOG_SEED_INCLUDE_TYPED=true uv run python -m analog_api.cli seed
uv run python -m uvicorn analog_api.main:app --app-dir apps/api/src
curl -s 'localhost:8000/api/v1/search/features?dimension=D03&role=filter' | python -m json.tool
curl -s localhost:8000/api/v1/search/multidimensional \
  -H 'content-type: application/json' \
  -d '{"targets":{"slope_mean_deg":{"value":10,"weight":1}},"filters":{"surface_air_temperature_2m_c":{"max":9.85}},"limit":3}' \
  | python -m json.tool
```

## 7. Remaining caveats

- Only the eight committed Earth pilot AOIs (5 original + 3 `r2c2`) carry D03/D04/D05 values.
  The other 105 fine candidates and all planetary candidates have no value for these controls
  and are reported as missing.
- D02 has only three Earth Macrostrat point-query rows. They are not global geological coverage,
  not an area fraction and not a taxonomy. Eleven stored labels do not imply eleven comparable
  geological classes.
- The ten D03/D04/D05 filter values are **period means of stored daily reanalysis samples**.
  They are not local measurements, not annual climatologies, and not interchangeable with
  remote-sensing thermal/illumination records.
- No Moon or Mars D02–D05 control exists. The corresponding features remain evidence/context and
  reject both similarity and filter requests.
- Increasing coverage is a separate data-expansion task. When it lands, `filter_value_count`,
  `gated_candidate_count` and `category_value_counts` will change automatically; the contract and
  control semantics do not.
