# Earth POWER 113 — D03/D04/D05 candidate coverage

- Release version: `earth-power-113-1.0.0`
- Source manifest: `pipelines/earth/manifests/copernicus_glo90_earth.json` (`sha256:dda333e0752816a083fae68e87ac624371cff6dddfb68b1e9b28f0b1777d55e8`)
- Earth candidates: 113
- Planned tasks: 339 (113 candidates x D03/D04/D05)
- Succeeded tasks: 339
- Failed tasks: 0
- Observations: 1130 (detected 1130, no_coverage 0)
- Assets: 339
- Feature slots per candidate: 10
- Distinct POWER cells (approx.): 22
- Interval: 2024-01-01 to 2024-01-07 (daily)

## Coverage by dimension

| Dimension | Features | Detected | No coverage | Not fetched |
|---|---:|---:|---:|---:|
| D03 | 4 | 452 | 0 | 0 |
| D04 | 4 | 452 | 0 | 0 |
| D05 | 2 | 226 | 0 | 0 |

## Failures

None. Every planned point/dimension produced a processed response.

## Scientific limits

- NASA POWER is a coarse reanalysis/model product (~0.5 x 0.625 degree), not a local 1.98 km measurement of temperature, water/ice or irradiance.
- The 113 candidates are the existing Earth terrain manifest, not a global or exhaustive survey. Several neighbouring 1.98 km windows fall in the same POWER cell, so candidate count is not independent spatial coverage.
- Every record is `context_only`. It must never enter Terrain similarity, a cross-body score, or a mineral/ice detection.
- Missing POWER values are counted and marked `no_coverage`; they are never replaced with zero.

## Reproduce

```bash
uv run python pipelines/d02_d04_aoi/earth_power_113/build.py plan
uv run python pipelines/d02_d04_aoi/earth_power_113/build.py fetch --workers 3
uv run python pipelines/d02_d04_aoi/earth_power_113/build.py check
bash pipelines/d02_d04_aoi/earth_power_113/rebuild.sh
```

The fetch cache defaults to `~/.cache/planetary-analog/nasa-power` (override with `ANALOG_POWER_CACHE`) and is outside the repository.
