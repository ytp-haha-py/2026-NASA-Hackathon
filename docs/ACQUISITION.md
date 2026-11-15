# Programmatic acquisition and ingestion

Status: **implemented for the D05 Earth NASA POWER pilot, D06 Earth SoilGrids predictions and
metadata-only Mars gates; remaining providers are explicitly unimplemented or blocked.** No large
raster download and no R2/Tunnel/deployment action is implied.

## Purpose

Repetitive source work is executed by reusable software, not by manual per-file agent commands.
The package lives at `pipelines/src/analog_pipelines/acquisition/` and uses the existing typed-v2
contracts, Areas registry, inventory and batch/checkpoint discipline.

## Interfaces

```python
class SourceProvider(Protocol):
    provider_id: str

    def discover(query: SourceQuery) -> list[SourceRecord]: ...
    def inspect(source: SourceRecord) -> SourceMetadata: ...
    def plan(query: SourceQuery, areas: list[PlanArea]) -> list[AcquisitionTask]: ...
    def acquire(task: AcquisitionTask, cache_dir: Path) -> LocalAsset: ...
    def process(asset: LocalAsset, task: AcquisitionTask) -> list[TypedObservation]: ...
    def validate(observations: list[TypedObservation]) -> ValidationReport: ...
```

Plans are JSON and deterministic. Every task has a fingerprint over source, area, endpoint,
parameters and method version. Assets are written atomically, hashed with SHA-256, and reused only
when the checkpoint fingerprint, file existence and checksum still match.

## CLI

```bash
# 18-combination source inventory
uv run python -m analog_pipelines.acquisition inventory --output var/plans/inventory.json

# bounded plan (example: five Earth AOIs for D05)
uv run python -m analog_pipelines.acquisition plan \
  --scope pilot-d05-earth --dimension D05 --body Earth --provider nasa-power \
  --area-id earth-atacama-desert --area-id earth-death-valley \
  --area-id earth-lanzarote --area-id earth-devon-island \
  --area-id earth-sahara-r1c1 \
  --start 2024-01-01 --end 2024-01-07 \
  --output var/plans/d05-earth-pilot.json

# bounded concurrent acquisition with resume
uv run python -m analog_pipelines.acquisition run \
  --plan var/plans/d05-earth-pilot.json \
  --output var/runs/d05-earth-pilot \
  --cache var/cache/acquisition --workers 3 --resume

uv run python -m analog_pipelines.acquisition validate --run-dir var/runs/d05-earth-pilot
uv run python -m analog_pipelines.acquisition report --run-dir var/runs/d05-earth-pilot

# portable, checksummed release with raw API payloads
uv run python -m analog_pipelines.acquisition release \
  --plan var/plans/d05-earth-pilot.json \
  --run-dir var/runs/d05-earth-pilot \
  --output pipelines/pilots/d05_earth_power
```

Import a release into the additive typed-v2 SQLite store:

```bash
uv run python scripts/import_typed_release.py \
  --release pipelines/pilots/d05_earth_power --database var/d05-pilot.db
```

## Implemented provider

### NASA POWER daily point (D05 Earth)

- Official endpoint: `https://power.larc.nasa.gov/api/temporal/daily/point`.
- Real pilot: 5 AOIs, 10 detected timeseries observations, 2024-01-01..2024-01-07.
- Context only; approximately 0.5 × 0.625°, never presented as a local 1.98 km measurement.
- Fill sentinels are excluded and counted; missing is never converted to zero.
- No ionizing-radiation value and no moonlight/Mars radiation is claimed.

Committed release: `pipelines/pilots/d05_earth_power/`.

## Status vocabulary

Source work must not collapse into one boolean. The inventory keeps availability separate from
data status. Ingestion checkpoints use `pending`, `succeeded`, `failed`, `unavailable`; observation
states use `detected`, `not_detected`, `no_coverage`, `unknown`, `not_applicable`.

## Remaining provider work

- D03 Earth: MODIS/other thermal granule discovery and authenticated acquisition are not implemented.
- D03 Moon/Mars: Diviner/THEMIS/TES access and calibrated processing are not implemented.
- D04 Earth/Moon/Mars: snow, soil moisture, neutron/radar/spectral evidence requires separate
  adapters and detection semantics.
- D05 Moon/Mars illumination and ionizing radiation: SPICE/terrain horizon and detector-dose
  adapters are not implemented; no combined scalar is allowed.
- D06 Earth: the five-AOI SoilGrids 2.0 pilot now covers 0–5, 5–15, 15–30 and
  30–60 cm as separate evidence-only model predictions. D06 Moon/Mars: no registered
  public sample/PDS measurement exists, so `sparse-regolith-samples` reports
  `blocked` and plans zero tasks; no regolith property is inferred from terrain
  roughness or Earth values. See `docs/D06_REGOLITH_SEMANTICS.md`.
- D02: the merged independent Geology pilot still needs a v2/API integration; GLiM rights, EMIT
  authentication, lunar mineral registration and CRISM thresholds remain unresolved.
- D01: the fine 90 m index remains unchanged; a real coarse Geomorpho index and same-support
  Moon/Mars coarse references are still not acquired.

These are recorded as blockers, not fabricated observations.

## D02 geology typed export

The independent geology pilot is exported without changing its evidence semantics:

```bash
uv run python scripts/export_geology_typed.py \
  --record pipelines/geology/records/geology-e920e7e3ae79e176.json \
  --output pipelines/geology/typed

uv run python scripts/import_typed_bundle.py \
  --bundle pipelines/geology/typed --database var/geology-typed.db
```

Current bundle: 8 areas, 7 datasets, 33 assets and 56 observations. They are all `evidence_only`;
map units/processes are categories or process evidence, mineral non-observations remain
`unknown`/`no_coverage`, and CRISM parameters remain context. Mars geographic AOIs are transformed
to a projected Mars analysis CRS while the original geometry/CRS is retained in legacy provenance.

## D06 SoilGrids pilot

The base pilot covers 0–5 cm; the Issue #46 extension adds bounded 5–15, 15–30 and
30–60 cm requests for the same five AOIs and five properties, with explicit per-observation
depth intervals and no cross-depth pooling.

```bash
# Base 0–5 cm pilot (75 observations)
uv run python -m analog_pipelines.acquisition plan \
  --scope pilot-d06-earth --dimension D06 --body Earth --provider soilgrids-wcs \
  --area-id earth-atacama-desert --area-id earth-death-valley \
  --area-id earth-lanzarote --area-id earth-devon-island --area-id earth-sahara-r1c1 \
  --output var/plans/d06-earth-pilot.json
uv run python -m analog_pipelines.acquisition run \
  --plan var/plans/d06-earth-pilot.json --output var/runs/d06-earth-pilot \
  --cache var/cache/acquisition --workers 4 --resume

# Multi-depth extension (300 observations, 4 depths); dry run first
uv run python scripts/build_soilgrids_depth_pilot.py --dry-run
uv run python scripts/build_soilgrids_depth_pilot.py --resume --workers 2
```

The committed base pilot contains 75 observations: clay, sand, silt, SOC and bulk density
at 0–5 cm, with mean/Q0.05/Q0.95 quantiles. The multi-depth pilot
(`pipelines/pilots/d06_earth_soilgrids_depths/`) contains 300 observations across
0–5, 5–15, 15–30 and 30–60 cm. All are evidence-only model predictions, use 250 m metric
source resolution, preserve the depth interval and never enter Terrain similarity.
Raw TIFFs are not committed; per-request response checksums and statuses are recorded.
No Moon/Mars regolith value is claimed; see `docs/D06_REGOLITH_SEMANTICS.md` and
`contracts/d06-regolith-semantics-v1.json`.

## D03 Earth thermal pilot

The same NASA POWER provider now also plans D03 thermal tasks (`T2M`, `T2M_MAX`, `T2M_MIN`, `TS`).
The committed pilot contains 20 context-only timeseries observations for 5 AOIs over
2024-01-01..07. Air temperature and skin temperature are kept distinct; no thermal inertia, diurnal
range or cross-body thermal score is derived.

## D04 Earth water-context pilot

NASA POWER D04 tasks use `GWETTOP`, `GWETROOT`, `SNODP` and `PRECTOTCORR`. The committed pilot has
20 context-only timeseries observations over 5 AOIs. Surface soil wetness, root-zone wetness, snow
depth and precipitation remain separate features; no confirmed extractable ice is claimed.
