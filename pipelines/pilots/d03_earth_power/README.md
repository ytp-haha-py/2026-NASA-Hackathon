# D03 Earth — NASA POWER thermal pilot

Real NASA POWER daily-point responses for five existing Earth AOIs, fetched through the reusable
provider framework on 2026-11-15.

## Actual content

| Item | Value |
|---|---:|
| Areas | 5 |
| Tasks | 5 |
| Observations | 20 |
| Assets | 5 |
| Parameters | T2M, T2M_MAX, T2M_MIN, TS |
| Interval | 2024-01-01 through 2024-01-07 |
| State | `detected` 20 |

Areas: Atacama, Death Valley, Lanzarote, Devon Island and one Sahara cell.

## Scientific limits

- NASA POWER is a coarse reanalysis/model product (~0.5 × 0.625°), not a local 1.98 km
  temperature measurement.
- `T2M`, `T2M_MAX`, `T2M_MIN` are air temperatures at 2 m; `TS` is a skin temperature. They are not
  interchangeable and no thermal inertia, heating rate or diurnal range is derived.
- Values are context-only and never enter Terrain similarity.
- Five AOIs are not global coverage; Moon and Mars thermal observations remain unavailable.

## Files

`plan.json`, `raw/`, `observations.jsonl`, `assets.json`, `manifest.json`, `REPORT.md`.

## Reproduce

```bash
uv run python -m analog_pipelines.acquisition plan \
  --scope pilot-d03-earth --dimension D03 --body Earth --provider nasa-power \
  --area-id earth-atacama-desert --area-id earth-death-valley \
  --area-id earth-lanzarote --area-id earth-devon-island --area-id earth-sahara-r1c1 \
  --start 2024-01-01 --end 2024-01-07 --output var/plans/d03-earth-pilot.json

uv run python -m analog_pipelines.acquisition run \
  --plan var/plans/d03-earth-pilot.json --output var/runs/d03-earth-pilot \
  --cache var/cache/acquisition --workers 3 --resume

uv run python -m analog_pipelines.acquisition validate --run-dir var/runs/d03-earth-pilot
uv run python -m analog_pipelines.acquisition report --run-dir var/runs/d03-earth-pilot
uv run python -m analog_pipelines.acquisition release \
  --plan var/plans/d03-earth-pilot.json --run-dir var/runs/d03-earth-pilot \
  --output pipelines/pilots/d03_earth_power
```
