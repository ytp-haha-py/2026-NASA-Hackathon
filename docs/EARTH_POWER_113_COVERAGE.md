# Earth POWER 113 coverage — D03/D04/D05 (Issue #170)

Status: bounded release committed under
`pipelines/d02_d04_aoi/earth_power_113/`. Scoring/registry code is **not** changed.

## What changed

Before this expansion, Earth D03/D04/D05 context existed for **5 pilot AOIs** (20/20/10
observations). The release now covers **all 113 Earth candidates** in
`pipelines/earth/manifests/copernicus_glo90_earth.json`:

| Dimension | Candidates with context | Observations | Failures |
|---|---:|---:|---:|
| D03 thermal | 113 / 113 | 452 | 0 |
| D04 water/ice | 113 / 113 | 452 | 0 |
| D05 illumination | 113 / 113 | 226 | 0 |
| **Total** | **113 / 113** | **1,130** | **0** |

Source: NASA POWER daily point API (`nasa-power-daily-point`,
`nasa-power-daily-1.0.0`), 2024-01-01…2024-01-07, community `RE` (D03/D05) and `AG` (D04).
339/339 planned requests succeeded; every observation is `context_only`, coarse
(~0.5 × 0.625°) and carries source, method version, resolution, time period, QA flags and
checksummed asset provenance. The 113 candidate windows collapse into **22 distinct POWER
cells**, so this is candidate coverage, not independent spatial or global coverage.

## Why this is usable but not scorable

A coarse reanalysis value cannot answer a 90 m surface-property query: it mixes neighbouring
terrain, has a different support, and (for D04) mixes physically distinct quantities. Treating
it as a similarity feature would silently degrade ranking. It is still useful as *context*:
UI/API explainability, coverage reporting, and a sanity check against a candidate's environment.

## Recommendation for the registry rule (Integration work, not implemented here)

The current eligibility registry already exposes observed Earth D03/D05 as `context` and D04 as
`evidence` only. The proposed rule is an **Earth-only scalar context filter** with explicit gates:

1. `body == "Earth"` only; Moon/Mars D03–D05 remain `evidence` (never context-equivalent).
2. `score_role == "context_only"` on the stored observation; otherwise reject.
3. Exact `area_id` match to the queried Earth candidate and exact method version/unit; no nearest
   point and no interpolation.
4. `state == "detected"` with a finite value; `no_coverage` stays missing and is never 0.
5. Point support must remain the coarse POWER grid (~50 km); the selector records the observation
   window (2024-01-01…2024-01-07) so a query cannot imply local or climatological support.
6. D04 quantities keep separate feature ids (precipitation, root-zone wetness, surface wetness,
   snow depth) and are never combined into an “ice” or “water” score.
7. The context role may be returned with the candidate payload, but must be excluded from
   `similarity`, `hard_filter`, `category_match` and uncertainty aggregation; a zero-weight context
   feature must never exclude a candidate.

Suggested follow-up in the Integration lane:

- extend the context role gate in `apps/api/src/analog_api/domain/eligibility.py` (Earth-only,
  scalar/timeseries summary, coarse-support allowlist);
- add a contract test that a D04 context value cannot be requested as a similarity target;
- keep `CONTEXT_FEATURES`/scoring untouched until that rule and its tests exist.

## Reproduce / verify

```bash
uv run python pipelines/d02_d04_aoi/earth_power_113/build.py check
bash pipelines/d02_d04_aoi/earth_power_113/rebuild.sh
uv run pytest pipelines/d02_d04_aoi/tests/test_earth_power_113.py -o addopts='' -q
```

The cache lives outside the repository (`~/.cache/planetary-analog/nasa-power` by default);
raw responses are small committed JSON files, no rasters and no global download.
