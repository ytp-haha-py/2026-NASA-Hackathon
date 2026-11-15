# D04 Earth — NASA POWER water-context pilot

Real NASA POWER daily-point responses for five Earth AOIs, fetched through the reusable provider
framework on 2026-11-15.

## Actual content

| Item | Value |
|---|---:|
| Areas | 5 |
| Tasks | 5 |
| Observations | 20 |
| Assets | 5 |
| Parameters | GWETTOP, GWETROOT, SNODP, PRECTOTCORR |
| Interval | 2024-01-01 through 2024-01-07 |
| State | `detected` 20 |

Features: surface soil wetness fraction, root-zone soil wetness fraction, snow depth (cm),
precipitation (mm/day). Areas: Atacama, Death Valley, Lanzarote, Devon Island and one Sahara cell.

## Scientific limits

- Coarse reanalysis/model context (~0.5 × 0.625°), not a local 1.98 km water/ice measurement.
- Soil wetness, snow depth and precipitation are distinct quantities and are never merged.
- No confirmed extractable ice, subsurface ice volume or water-equivalent hydrogen is claimed.
- Context-only; never enters Terrain similarity or any cross-body water/ice score.
- Five AOIs are not global coverage; Moon/Mars water-ice observations remain unavailable.

## Reproduce

```bash
uv run python -m analog_pipelines.acquisition plan \
  --scope pilot-d04-earth --dimension D04 --body Earth --provider nasa-power \
  --area-id earth-atacama-desert --area-id earth-death-valley \
  --area-id earth-lanzarote --area-id earth-devon-island --area-id earth-sahara-r1c1 \
  --start 2024-01-01 --end 2024-01-07 --output var/plans/d04-earth-pilot.json

uv run python -m analog_pipelines.acquisition run \
  --plan var/plans/d04-earth-pilot.json --output var/runs/d04-earth-pilot \
  --cache var/cache/acquisition --workers 3 --resume

uv run python -m analog_pipelines.acquisition validate --run-dir var/runs/d04-earth-pilot
uv run python -m analog_pipelines.acquisition report --run-dir var/runs/d04-earth-pilot
uv run python -m analog_pipelines.acquisition release \
  --plan var/plans/d04-earth-pilot.json --run-dir var/runs/d04-earth-pilot \
  --output pipelines/pilots/d04_earth_power
```
