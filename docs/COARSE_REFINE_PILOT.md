# Coarse-to-90 m refinement pilot (Issue #37)

Generated: `2026-11-15T06:07:19Z`  
Provider: `analog_pipelines.refine_earth.RefineEarthProvider`  
Report schema: `coarse-refine-report-1.0.0`

## Bounded scope

- Input coarse index: `pipelines/coarse/real-pilot/index` snapshot `geomorpho250-real-pilot-20261115` (manifest `sha256:f6ac5cc33c10f57d6ea2e5016b7612dfa3702a805cc037c1d44120e16716e301`).
- Coarse method: `coarse-terrain-250m-1.0.0`; grid: `coarse-grid-1.0.0`.
- Selection: `top-per-region` — Top 1 hit per coarse region for the first 3 region(s) sorted by region_id; order inside each region is coarse distance then candidate_id.
- Target: `{"coarse_slope_mean_deg": 8.0}` with weights `{"coarse_slope_mean_deg": 1.0}`.
- Focus rule: `coarse_recorded_metric_bbox_centre_v1`; 1980 m metric window inside each 5 km coarse cell.
- Fine analysis: 90 m cells; method `terrain-metrics-1.0.0`.

## Actual counts

| Item | Count |
| --- | ---: |
| Coarse cells indexed | 168 |
| Coarse cells eligible for the target | 168 |
| Coarse hits selected | 3 |
| Refined windows succeeded | 3 |
| Refined windows failed | 0 |
| Refined windows no coverage | 0 |
| Fine terrain observations | 15 |
| WorldCover context observations | 33 |

## Coarse -> refined links

| Coarse candidate | Region | Coarse rank (within region) | Coarse distance | Refined area | Status | Fine mean slope (deg) | Fine p90 slope (deg) | Relief p95-p05 (m) | Valid fraction |
| --- | --- | ---: | ---: | --- | --- | ---: | ---: | ---: | ---: |
| `coarse-v1:249d33516a874855` | pilot-alps | 1 | 0.727555 | `earth-refine-48a9e5a4fee2ca81` | succeeded | 0.7848 | 1.7907 | 17.07 | 1.0000 |
| `coarse-v1:e0d4c209d928961c` | pilot-grand-canyon | 1 | 0.095805 | `earth-refine-432552ef1e4bb863` | succeeded | 2.2488 | 3.4729 | 50.39 | 1.0000 |
| `coarse-v1:95046a41aeb31ca9` | pilot-sahara | 1 | 0.013549 | `earth-refine-01a341a9cef18971` | succeeded | 12.4560 | 22.9691 | 154.65 | 1.0000 |

Coarse and fine values are shown side by side but are not comparable: the coarse rank uses a 5 km / ~250 m support and the fine observations use a 1.98 km / 90 m support. No fine rank is computed.

## Provider status

| Provider dataset | verified | failed | no coverage |
| --- | ---: | ---: | ---: |
| `copernicus-dem-glo90` | 3 | 0 | 0 |
| `esa-worldcover-2021-v200` | 3 | 0 | 0 |

## Sources and licenses

| Dataset | Provider | Product | License |
| --- | --- | --- | --- |
| `copernicus-dem-glo90` | European Space Agency (ESA) / Airbus Defence and Space; AWS Open Data mirror | COP-DEM_GLO-90-DGED | Copernicus DEM GLO-90: free basis for the general public (AWS Open Data registry), under the Copernicus Contributing Mission Data Access Licence terms |
| `esa-worldcover-2021-v200` | European Space Agency (ESA) WorldCover | ESA WorldCover 2021 v200 | CC BY 4.0 (Creative Commons Attribution 4.0 International) |

Per-window source URLs, Copernicus tile lists, checksums and actual access timestamps are recorded in the JSON `provider_status` fields.

## Failures

- None.

## Recall and coverage claims

- Recall estimate: none. No held-out labelled pilot is available. This run measures bounded refinement behaviour and provider support, not coarse-shortlist recall.
- Coverage: Only the selected coarse cells and their focus windows were processed. No global or exhaustive best-match claim is made.

## Limitations

- This is a bounded local coarse pilot, not global coverage and not an exhaustive scan.
- The coarse index contains 168 cells; only 3 selected hit(s) were refined. No recall or precision is estimated because no held-out labelled pilot is available.
- Coarse 5 km / ~250 m features and fine 1.98 km / 90 m features have different support and method versions. The coarse rank is not a fine rank and the two are never merged into one score.
- The 1.98 km focus window is placed at the centre of each coarse metric window. This fixed placement does not search for the most similar sub-window inside the 5 km candidate, so a coarse hit may contain a different fine-scale signature.
- Copernicus GLO-90 is a DSM and may include vegetation, buildings and infrastructure; it is not a bare-earth terrain model.
- ESA WorldCover is 2021 land-cover context only. It is not mineralogy, not geology, and does not enter the terrain similarity distance.
- Provider status is per request. Failed or no-coverage windows are reported and omitted; no missing value is replaced with zero or a synthetic observation.
- Refined windows are not promoted into the seedable index, the committed snapshot, or user-created AOIs by this pilot.
- Region-stratified selection takes the top hit in each committed pilot region, not the global top three; it is not a ranking of regions.

## Run metrics

- Elapsed: 26.71 s
- Max RSS: 201.2 MiB
- Report JSON bytes: 56393
