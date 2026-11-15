# Earth POWER 113 — D03/D04/D05 candidate coverage

Bounded, real NASA POWER daily-point context for **all 113 Earth terrain candidates** in
`pipelines/earth/manifests/copernicus_glo90_earth.json`. This directory is the concrete
"more usable Earth data" expansion for Issue #170: candidate coverage, not another three AOIs.

**It is coarse reanalysis context, not a local measurement and not a global survey.**
Every value is `context_only` and must never enter Terrain similarity scoring.

## Actual content

| Item | Value |
|---|---:|
| Earth candidates | 113 |
| Dimensions | D03, D04, D05 |
| Planned tasks (point × dimension) | 339 |
| Succeeded tasks | 339 |
| Failed tasks | 0 |
| Observations | 1,130 |
| `detected` / `no_coverage` | 1,130 / 0 |
| Feature slots per candidate | 10 |
| Raw responses committed | 339 |
| Interval | 2024-01-01 … 2024-01-07 (daily) |
| Distinct POWER cells (approx.) | 22 |
| Max manifest-point vs bbox-centre offset | 0.653 m |

| Dimension | Features | Detected | No coverage |
|---|---|---:|---:|
| D03 thermal | `surface_air_temperature_2m_c`, `..._max_c`, `..._min_c`, `surface_skin_temperature_c` | 452 | 0 |
| D04 water/ice | `precipitation_mm_day`, `root_zone_soil_wetness_fraction`, `snow_depth_cm`, `surface_soil_wetness_fraction` | 452 | 0 |
| D05 illumination | `surface_shortwave_irradiance_kwh_m2_day`, `surface_longwave_irradiance_kwh_m2_day` | 226 | 0 |

Candidate clusters: Alps, Altiplano, Atacama, Death Valley, Devon Island, Grand Canyon,
Haughton, Iceland, Lanzarote, Namib, Pampas, Pingualuit, Rio Tinto, Sahara, Snake River,
Wupatki (9 × 9/10-window clusters plus four single AOIs).

## Files

| Path | Purpose |
|---|---|
| `build.py` | `plan` / `fetch` / `rebuild` / `check` stages; bounded 2–4 workers, retries, checkpoint/resume. |
| `plan.json` | 339 acquisition tasks with exact POWER request URLs and per-task method versions. |
| `fetch-index.json` | Per-task fetch status, `fetched_at`, checksum and bytes; the release timestamp anchor. |
| `raw/` | 339 committed POWER JSON responses (one per point × dimension). |
| `observations.jsonl` | 1,130 typed `context_only` observations. |
| `assets.json` | 339 source assets with checksums, endpoint and fetch time. |
| `coverage.json` | Per candidate × dimension × feature state (`detected` / `no_coverage` / `not_fetched`). |
| `failures.json` | Explicit per-task failures; empty for this build. |
| `manifest.json` | Counts, source-manifest hash, coverage summary, task list, raw checksums and file checksums. |
| `REPORT.md` | Counts, failures and scientific limits. |
| `checksums.sha256` | SHA-256 for every data/generated file (339 raw + 8 derived). |
| `rebuild.sh` | Offline deterministic rebuild + verification. |

## Provenance and QA kept on every observation

- `dataset_ids: ["nasa-power-daily-point"]` and `source_asset_ids` pointing at a checksummed
  `assets.json` entry.
- `method_version: nasa-power-daily-1.0.0` on observations; the plan task additionally records the
  dimension-specific method version (`nasa-power-thermal-daily-1.0.0`,
  `nasa-power-water-daily-1.0.0`, `nasa-power-daily-1.0.0`).
- `score_role: context_only`, body `Earth`, dimension `D03/D04/D05`, unit per feature.
- `spatial_support`: point at the AOI bbox centre, `source_resolution_m = 50000`,
  “approximately 0.5 × 0.625 degree. Not a 1.98 km local measurement.”
- `temporal_support`: 2024-01-01 … 2024-01-07 daily.
- `quality_flags`: coarse/model-or-reanalysis and “not a local … measurement”.
- POWER `-999` fill and non-finite values are counted as `missing_count`; a parameter with no
  values becomes `no_coverage` with `value: null`. Nothing is zero-filled.

Requests use community `RE` for D03/D05 and `AG` for D04, the same parameter definitions as
`pipelines/src/analog_pipelines/acquisition/providers.py`. Concurrency is capped at 3 workers with
a shared ≥0.5 s request-start interval, three retries per task and a resumable external cache.

## Scientific limits

- POWER is a coarse model/reanalysis product (~0.5 × 0.625°). Nine adjacent 1.98 km windows often
  fall in the same POWER cell: 113 candidates are **22 distinct cells**, so candidate coverage is
  not independent spatial coverage and not a global survey.
- Air temperature (`T2M`/`T2M_MAX`/`T2M_MIN`) and skin temperature (`TS`) are different quantities;
  no thermal inertia, heating rate or diurnal range is derived.
- Soil wetness, snow depth and precipitation are distinct quantities and are never merged; no
  extractable ice, subsurface ice volume or water-equivalent hydrogen is claimed.
- Shortwave/longwave irradiance is not ionizing radiation and is not surface-property similarity.
- These records answer “what is the coarse environmental context of this candidate?”, not “what is
  the temperature/water/illumination at this 90 m window?”.

## Reproduce

```bash
# 1. Rebuild the 339-task plan from the Earth candidate manifest (offline)
uv run python pipelines/d02_d04_aoi/earth_power_113/build.py plan

# 2. Fetch real POWER responses (network; resumable, external cache)
uv run python pipelines/d02_d04_aoi/earth_power_113/build.py fetch --workers 3

# 3. Verify checksums, counts and invariants (offline)
uv run python pipelines/d02_d04_aoi/earth_power_113/build.py check

# 4. Rebuild every derived file byte-identically from committed raw/ (offline)
bash pipelines/d02_d04_aoi/earth_power_113/rebuild.sh
```

The cache defaults to `~/.cache/planetary-analog/nasa-power` (override with
`ANALOG_POWER_CACHE`) and is outside the repository. It is never committed.

Focused tests (offline; the live call is opt-in):

```bash
uv run pytest pipelines/d02_d04_aoi/tests/test_earth_power_113.py -o addopts='' -q
ANALOG_RUN_NETWORK_TESTS=1 uv run pytest pipelines/d02_d04_aoi/tests/test_earth_power_113.py \
  -o addopts='' -q -m network
```

The offline suite re-materialises the release from `plan.json` + `raw/` and asserts byte-identical
`observations.jsonl`, `assets.json`, `coverage.json`, `failures.json`, `manifest.json`, `REPORT.md`
and `checksums.sha256`.

## Integration note

The scoring registry is intentionally untouched. See
`docs/EARTH_POWER_113_COVERAGE.md` for the recommended Earth-only scalar-context registry rule
(future Integration work), including the gates that must keep these values out of similarity,
hard filters and cross-body comparisons.
