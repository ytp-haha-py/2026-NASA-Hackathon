# D05 Earth — NASA POWER pilot

This directory is a portable, checksummed release produced by
`python -m analog_pipelines.acquisition release`. It contains real NASA POWER
daily-point responses for five existing Earth AOIs, fetched on 2026-11-15.

## Actual content

| Item | Value |
|---|---:|
| Areas | 5 |
| Tasks | 5 |
| Observations | 10 |
| Assets | 5 |
| Dimensions | D05 only |
| Bodies | Earth only |
| States | `detected` 10 |
| Features | `surface_shortwave_irradiance_kwh_m2_day` (5), `surface_longwave_irradiance_kwh_m2_day` (5) |

Areas: Atacama Desert, Death Valley, Lanzarote, Devon Island and one Sahara grid cell.
The requested interval is 2024-01-01 through 2024-01-07 (seven daily values per
feature per area).

## Scientific limits

- NASA POWER is a coarse reanalysis/model product (approximately 0.5 × 0.625°).
  It is **context only**, not a local 1.98 km irradiance measurement.
- The values are not ionizing radiation, absorbed dose or equivalent dose.
- These observations are not added to the terrain Euclidean similarity score.
- Five AOIs are a bounded pilot, not global coverage.
- NASA POWER supplies no per-point uncertainty; the release keeps
  `uncertainty.kind = unknown` rather than inventing a value.
- Missing/fill sentinels are counted and excluded; they are never converted to zero.

## Files

- `plan.json` — the exact acquisition plan and source endpoints.
- `raw/` — the small NASA POWER JSON responses, content-addressed by SHA-256 in
  `manifest.json` and `assets.json`.
- `observations.jsonl` — typed-v2 observations.
- `assets.json` — source asset provenance and checksums.
- `manifest.json` — release hashes, task endpoints, counts and scope.
- `REPORT.md` — generated aggregate report.

## Reproduce

```bash
uv run python -m analog_pipelines.acquisition plan \
  --scope pilot-d05-earth --dimension D05 --body Earth --provider nasa-power \
  --area-id earth-atacama-desert --area-id earth-death-valley \
  --area-id earth-lanzarote --area-id earth-devon-island \
  --area-id earth-sahara-r1c1 \
  --start 2024-01-01 --end 2024-01-07 \
  --output var/plans/d05-earth-pilot.json

uv run python -m analog_pipelines.acquisition run \
  --plan var/plans/d05-earth-pilot.json \
  --output var/runs/d05-earth-pilot \
  --cache var/cache/acquisition --workers 3 --resume

uv run python -m analog_pipelines.acquisition validate --run-dir var/runs/d05-earth-pilot
uv run python -m analog_pipelines.acquisition report --run-dir var/runs/d05-earth-pilot
uv run python -m analog_pipelines.acquisition release \
  --plan var/plans/d05-earth-pilot.json \
  --run-dir var/runs/d05-earth-pilot \
  --output pipelines/pilots/d05_earth_power
```

The source responses are tiny API payloads, not a large scientific raster
download. Existing terrain snapshots and manifests are unchanged.
