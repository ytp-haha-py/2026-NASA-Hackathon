# D04 water/ice/hydrogen/hydrated-mineral coverage matrix (Issue #138)

Status: **bounded pilot coverage with verified gaps; evidence/context only; no D04 similarity/filter score.**
Machine-readable matrix: [`docs/data-sources/d04-water-ice-coverage-matrix.json`](data-sources/d04-water-ice-coverage-matrix.json).

This document satisfies the #138 D04-owned deliverable for a body × feature coverage matrix, bounded reproducible ingest/index references, and representative real-data queries for Earth, Moon, and Mars where indexed data exists. It does **not** modify shared API schemas, OpenAPI/contracts, scoring, or other dimensions.

## Scope and non-claims

- Quantities are separated: liquid water, ice/ice phase, soil moisture, snow/precipitation context, hydrogen proxy, hydrated mineral, radar CPR, detection claims, and depth interval.
- States are preserved exactly as indexed: `detected`, `not_detected`, `unknown`, `no_coverage`, and `not_applicable` where used by source semantics. **No-detection is never converted to no-ice.**
- Hydrogen/neutron/CPR observations are evidence/proxies, not confirmed ice fraction, mineable ice, or resolved subsurface ice volume. Current indexed detection claims contain **0 confirmed ice detections**.
- Hydrated-mineral spectral evidence is D02 evidence (e.g. CRISM processing context) and is **not duplicated** as D04 scoring/filter evidence.
- Soil moisture, snow depth, and precipitation are distinct quantities and are never merged.
- Bounded pilot coverage only: 5 Earth AOIs / 7 days of NASA POWER context and 30 Moon/Mars native planetary tasks. This is **not** global, exhaustive, or representative coverage.

## Indexed releases (bounded, reproducible)

| Release | Path | Observations | SHA-256 (observations) | Notes |
|---|---|---:|---|---|
| Earth NASA POWER context | `pipelines/pilots/d04_earth_power/` | 20 | `6d42daff339896949edcab1e278d8eba6ccee1b3b1a0787bb9af67d4402a3835` | Coarse modeled/reanalysis context only (~0.5×0.625°), 5 AOIs, 2024-01-01..2024-01-07. |
| Moon/Mars water/ice evidence | `pipelines/pilots/d04_water_ice_planetary/` | 55 | `c4d5ae7bcafc098870ee638b07778e46928fc523c71960aa828bebb9a123f72e` | Evidence-only; native supports preserved; 0 detected ice claims. |

Planetary raw bytes/windows remain in gitignored cache paths when downloaded (`var/cache/water-44`); committed artifacts are typed observations, manifests, checksums, and provenance.

## Body × feature summary (indexed)

| Body | Feature / quantity | Class | Data kind | States (count) | Support highlights | Score/filter role |
|---|---|---|---|---|---|---|
| Earth | `surface_soil_wetness_fraction` | soil moisture | modeled context | detected (5) | ~50 km point, EPSG:4326, daily 7-day interval, surface | context_only, no similarity/filter |
| Earth | `root_zone_soil_wetness_fraction` | soil moisture | modeled context | detected (5) | same as above | context_only |
| Earth | `snow_depth_cm` | snow/ice proxy context | modeled context | detected (5) | same spatial/time; cm | context_only |
| Earth | `precipitation_mm_day` | liquid-water input proxy | modeled context | detected (5) | same spatial/time; mm/day | context_only |
| Moon | `water_equivalent_hydrogen_wt_percent` | hydrogen proxy | derived evidence | detected (12) | 0.5°×0.5° LP NS cells, upper ~1 m neutron sensitivity | evidence_only |
| Moon | `radar_circular_polarization_ratio` | radar CPR | measured evidence | detected (12) | ~236.9 m/pixel Mini-RF windows, near-surface scattering | evidence_only |
| Moon | `water_ice_detection` | ice detection claim | derived claim | unknown (23), no_coverage (5), not_detected (1) | native proxy supports preserved | evidence_only, 0 confirmed |
| Mars | `epithermal_neutron_flux_ratio` | hydrogen proxy | derived evidence | detected (1) | 4°×4° HEND cell, upper ~1 m proxy | evidence_only |
| Mars | `water_ice_detection` | ice detection claim | derived claim | unknown (1) | native HEND support | evidence_only, 0 confirmed |

## Explicit gaps / unsupported in D04 index

| Body | Feature / quantity | Status | Reason |
|---|---|---|---|
| Earth/Moon/Mars | `liquid_water` | unsupported/unindexed | No bounded D04 liquid-water product indexed; do not infer from proxies. |
| Earth | `ice_phase_resolved` | unsupported/unindexed | POWER snow depth is context, not phase-resolved ice detection/volume. |
| Moon/Mars | `ice_phase_resolved` | unsupported/unindexed | Proxies/claims exist but no confirmed phase-resolved ice; unknown/no_coverage ≠ no-ice. |
| Earth/Moon/Mars | `water_ice_depth_interval` | no_coverage_in_index | No resolved depth-interval observations; proxy sensing depths are not resolved ice intervals. |
| Mars | `hydrated_mineral_evidence` (D04 view) | handled_by_other_dimension | CRISM hydration is D02 evidence; not duplicated in D04. |
| Earth/Moon | `hydrated_mineral_evidence` (D04 view) | unsupported/unindexed | No D04-indexed hydrated-mineral observations. |

Blocked/candidate sources and alternatives are documented in [`docs/D04_D06_SOURCE_AUDIT.md`](D04_D06_SOURCE_AUDIT.md) and [`docs/data-sources/d04-d06-water-regolith.json`](data-sources/d04-d06-water-regolith.json) (e.g. LEND normalization blocker, SHARAD radargram interpretation blocker, CRISM atmospheric-correction/D02 boundary, Odyssey NS 0.5° counts normalization candidate).

## Representative real-data queries (all three bodies where data exists)

Run from repository root.

### Earth (NASA POWER context, 5 AOIs)

```bash
python3 - <<'PY'
import json
path='pipelines/pilots/d04_earth_power/observations.jsonl'
obs=[json.loads(l) for l in open(path) if l.strip()]
earth=[o for o in obs if o['body']=='Earth']
print('Earth total', len(earth))
print('features', sorted({o['feature_id'] for o in earth}))
print('areas', sorted({o['area_id'] for o in earth}))
sample=[o for o in earth if o['area_id']=='earth-atacama-desert' and o['feature_id']=='snow_depth_cm'][0]
print('sample', sample['area_id'], sample['feature_id'], sample['state'], sample['value']['values'])
PY
```

Expected evidence: 20 Earth observations, 4 features, 5 areas, and a real timeseries value list for Atacama snow depth.

### Moon (hydrogen proxy, radar CPR, detection claims)

```bash
python3 - <<'PY'
import json
path='pipelines/pilots/d04_water_ice_planetary/observations.jsonl'
obs=[json.loads(l) for l in open(path) if l.strip()]
moon=[o for o in obs if o['body']=='Moon']
print('Moon total', len(moon))
print('features', sorted({o['feature_id'] for o in moon}))
claims=[o for o in moon if o['feature_id']=='water_ice_detection']
print('claim states', {s: sum(1 for o in claims if o['state']==s) for s in sorted({o['state'] for o in claims})})
weh=[o for o in moon if o['feature_id']=='water_equivalent_hydrogen_wt_percent'][0]
print('sample WEH', weh['area_id'], weh['state'], weh['value'], weh['unit'])
PY
```

Expected evidence: 53 Moon observations across WEH, CPR, and detection claims with states including `unknown`/`no_coverage`/`not_detected` (no confirmed ice).

### Mars (epithermal hydrogen proxy and detection claim)

```bash
python3 - <<'PY'
import json
path='pipelines/pilots/d04_water_ice_planetary/observations.jsonl'
obs=[json.loads(l) for l in open(path) if l.strip()]
mars=[o for o in obs if o['body']=='Mars']
print('Mars total', len(mars))
print('features', sorted({o['feature_id'] for o in mars}))
for o in mars:
    print(o['feature_id'], o['state'], o.get('value'), o.get('unit'))
PY
```

Expected evidence: 2 Mars observations (`epithermal_neutron_flux_ratio` detected numeric, `water_ice_detection` unknown claim).

## Rebuild instructions

### Earth bounded ingest (NASA POWER, no raw raster download)

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

### Moon/Mars bounded planetary evidence pilot

```bash
uv run python scripts/build_water_ice_pilot.py --download
uv run python scripts/build_water_ice_pilot.py
uv run python scripts/build_water_ice_pilot.py --check
```

The download step fetches only bounded official products/windows into gitignored cache (small ASCII tables and bounded Mini-RF range reads as implemented by the script); it is not a global high-resolution download.

## Validation commands

```bash
uv run pytest pipelines/tests/test_water_ice_pilot.py -q
uv run pytest pipelines/tests/test_d04_coverage_matrix.py -q
python3 scripts/check_d04_d06_sources.py  # opt-in network source check; not CI
```

## Performance, storage, and limitations

- Committed D04 pilot artifacts are small JSON/JSONL/manifests/checksums; raw source bytes are not committed.
- Planetary build uses bounded reads and checksums; NASA POWER uses point API responses for 5 AOIs × 7 days.
- Coverage is intentionally bounded to committed pilot AOIs/windows; no global index, no cross-body scoring equivalence, and no confirmed ice inventory.
- Hydrated-mineral and subsurface depth-resolved products remain gaps or are owned by other dimensions to prevent semantic duplication.
