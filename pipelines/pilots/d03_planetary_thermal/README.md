# D03 planetary thermal pilot

Real, bounded D03 thermal evidence from two official public products:

- **Moon:** LRO Diviner Polar Cumulative Products (PCP), bolometric brightness
  temperature, 240 m native bins, 0.25 h local-time bins, southern summer
  2009-07-05 to 2019-02-17.
- **Mars:** USGS THEMIS-derived quantitative thermal-inertia mosaic, 100 m/pixel,
  ISIS3 tiled cube; only small 128 x 128 tiles around the existing Jezero
  reference windows were read by HTTP range request.

Observations: **41**. Areas:
**23**. States: `{'detected': 31, 'no_coverage': 7, 'unknown': 3}`.

## Scientific limits

- Diviner PCP is a multi-year composite at one local-time bin, not a single
  acquisition and not a same-sol time series. No diurnal range or change rate is
  computed.
- Bolometric brightness temperature assumes unit emissivity; it is **not**
  kinetic surface temperature.
- THEMIS thermal inertia is a separate feature, with the Fergason et al. (2006)
  retrieval, ~20% published absolute accuracy and no per-pixel uncertainty/QA.
- No 240 m or 100 m value is presented as 90 m. Nothing is resampled.
- No cross-body D03 score. Earth NASA POWER context is unchanged.
- Only 18 existing Moon and 5 existing Mars reference windows are covered; this
  is not global or exhaustive coverage.

## Files

`areas.json`, `datasets.json`, `assets.json`, `observations.jsonl`,
`manifest.json`, `plan.json`, `REPORT.md`.

## Reproduce

```bash
uv run python scripts/build_planetary_thermal_pilot.py --download
uv run python scripts/build_planetary_thermal_pilot.py
uv run pytest pipelines/tests/test_thermal_planetary.py -q
```

Raw PCP tables and ISIS tile blocks stay in the gitignored
`var/cache/thermal-43` directory and are never committed.
