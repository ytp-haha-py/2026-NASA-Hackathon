# D05 Illumination & Radiation — Coverage Matrix (Issue #139)

**Scope:** Phase-2 D05 Goal for AnalogX Issue #139 in worktree `phase2/139-d05` (base `ca0fc8f`).  
**Dimension ownership:** D05 data/provider/ingest/manifests/tests/docs and coverage matrix only. No shared API schemas, `contracts/openapi.json`, scoring engine, or non-D05 dimension files modified. No #118/#119/#123 UI changes.

Machine-readable matrix: [`docs/data-sources/d05-illumination-radiation-coverage-matrix.json`](data-sources/d05-illumination-radiation-coverage-matrix.json)

## 1) Body × feature coverage summary

Illumination and ionizing radiation are represented as **separate subgroups/features** and are never combined into a single value or cross-body score.

| Body | Subgroup | Feature | Committed/indexed pilot coverage | Data kind / status | Time support | Spatial support / CRS | Legal role |
|---|---|---|---:|---|---|---|---|
| Earth | Illumination | `surface_shortwave_irradiance_kwh_m2_day` | 5 areas, 5 detected obs (10 total D05 Earth obs incl. longwave), 2024-01-01..2024-01-07 daily | modeled context (NASA POWER), detected | daily interval (bounded pilot) | ~0.5×0.625° point/grid context (WGS84-like query coords) | context_only |
| Earth | Illumination | `surface_longwave_irradiance_kwh_m2_day` | 5 areas, 5 detected obs | modeled context (NASA POWER), detected | daily interval (bounded pilot) | ~0.5×0.625° point/grid context | context_only |
| Earth | Illumination | `solar_incidence_deg` / `solar_elevation_deg` | 0 committed SPICE obs | modeled/unsupported (no committed Earth SPICE ingest) | instant only when computed; epoch required | point on declared body-fixed frame (not committed) | not allowed as score/filter; context_only only if bounded+documented |
| Earth | Ionizing radiation | `absorbed_dose_gy`, `equivalent_dose_sv` | 0 committed obs | unsupported / verified blocker | exposure interval required | geometry/shielding required | unsupported |
| Moon | Illumination | `solar_incidence_deg` | 4 areas, 4 detected obs | model-derived (SPICE), detected | instant `2026-11-15T12:00:00Z` | point sites, `MOON_ME` (31001) | context_only |
| Moon | Illumination | `solar_elevation_deg` | 4 areas, 4 detected obs | model-derived (SPICE), detected | instant `2026-11-15T12:00:00Z` | point sites, `MOON_ME` (31001) | context_only |
| Moon | Ionizing radiation | flux/dose candidates | 0 committed D05 obs | unsupported/no committed ingest (CRaTER identified as source candidate only) | exposure interval required | detector/geometry dependent | unsupported/evidence_only only if future bounded ingest |
| Mars | Illumination | `solar_incidence_deg` | 5 areas, 5 detected obs | model-derived (SPICE), detected | instant `2026-11-15T12:00:00Z` | point sites, `IAU_MARS` (10014) | context_only |
| Mars | Illumination | `solar_elevation_deg` | 5 areas, 5 detected obs | model-derived (SPICE), detected | instant `2026-11-15T12:00:00Z` | point sites, `IAU_MARS` (10014) | context_only |
| Mars | Ionizing radiation | flux/dose candidates | 0 committed D05 obs | unsupported/no committed ingest (e.g. MSL RAD as alternative candidate, not ingested) | exposure interval required | geometry/shielding/time required | unsupported/evidence_only only if future bounded ingest |

**Totals (committed D05 pilot observations):** 28 (`pipelines/pilots/d05_earth_power`: 10 Earth; `pipelines/pilots/d05_spice_illumination`: 18 Moon/Mars). **Committed ionizing radiation dose/flux observations:** 0.

## 2) Time/assumption contract evidence

### Illumination geometry (SPICE, Moon/Mars committed pilot)
- **Epoch:** `2026-11-15T12:00:00Z` (ET `848016069.182739 s`) — single instant, no time series.
- **Frames:** Moon `MOON_ME` (31001, DE421 lunar PA/FK), Mars `IAU_MARS` (10014, `pck00011`).
- **Kernels (pinned, checksummed):** `naif0012.tls`, `pck00011.tpc`, `de421.bsp`, `moon_pa_de421_1900-2050.bpc`, `moon_080317.tf` (18,718,944 bytes total); provenance in `pipelines/pilots/d05_spice_illumination/manifest.json` and `assets.json`.
- **Assumptions:** point site on biaxial ellipsoid; flat tangent-plane normal; no DEM horizon/shadow mask/observer height/atmosphere/cloud; light-time-corrected Sun direction with site parallax subtraction; source projected-CRS-to-body-fixed registration is a documented assumption, not independent geodetic validation.
- **Missing time behavior:** if epoch/time assumptions are missing or unsupported, return `unsupported`/`no_coverage`; never a fixed global illumination value.
- **Evidence:** `docs/D05_SPICE_PILOT.md`, `pipelines/pilots/d05_spice_illumination/{manifest.json,assets.json,observations.jsonl,REPORT.md}`.

### Earth irradiance (NASA POWER, committed pilot)
- **Interval:** `2024-01-01..2024-01-07` daily for 5 bounded AOIs.
- **Parameters:** `ALLSKY_SFC_SW_DWN`, `ALLSKY_SFC_LW_DWN` (all-sky surface shortwave/longwave); **not** ionizing radiation, not absorbed/equivalent dose.
- **Assumptions:** coarse model/reanalysis (~0.5×0.625°), not local 1.98 km measurement; per-point uncertainty unavailable (`unknown`); fill/missing sentinels excluded, never zero-filled.
- **Missing time behavior:** missing/unsupported intervals must be reported as unsupported/no_coverage, not substituted.
- **Evidence:** `pipelines/pilots/d05_earth_power/{manifest.json,assets.json,observations.jsonl,README.md,raw/*.json}`.

### Ionizing radiation contract (separate subgroup)
- **Required before any value product:** instrument/model identity, particle species/channel/energy range, exposure interval, sensor geometry, shielding/material context, calibration and dose-model version.
- **Forbidden:** Gy↔Sv or flux↔dose conversion without explicit physics/calibration; mixing illumination geometry/irradiance with ionizing dose; fabricating or zero-filling missing values.
- **Current state:** no committed D05 ionizing-radiation observations; entries recorded as `unsupported`/`no_coverage` with verified blockers and alternative bounded sources in matrix JSON.

## 3) Bounded ingest/index and provenance

- **Earth illumination ingest/index (bounded):** `pipelines/pilots/d05_earth_power/` — 5 NASA POWER point tasks, 5 raw JSON assets (checksummed), 10 typed observations, manifest/report/reproduce instructions. No global download.
- **Moon/Mars illumination geometry ingest/index (bounded):** `pipelines/pilots/d05_spice_illumination/` — 9 existing reference sites, pinned public kernel provenance (bytes cached under gitignored `var/cache/spice-45`, not committed), 18 typed observations, manifest/assets/checksums.
- **Ionizing radiation:** verified blocker for committed dose/flux products in this bounded scope; alternative official sources identified (e.g. CRaTER, MSL RAD) but not ingested without explicit bounded product selection, time/geometry/shielding metadata, and shared-contract coordination (#141 where required). No fabricated values.

## 4) Real representative queries (all three bodies where data exists)

Offline, reproducible queries against committed pilot artifacts:

```bash
# Earth (illumination irradiance context exists)
python3 -c "import json; obs=[json.loads(l) for l in open('pipelines/pilots/d05_earth_power/observations.jsonl') if l.strip()]; print({'body':'Earth','count':len(obs),'areas':sorted({o['area_id'] for o in obs}),'features':sorted({o['feature_id'] for o in obs}),'states':sorted({o['state'] for o in obs})})"

# Moon (illumination geometry exists)
python3 -c "import json; obs=[json.loads(l) for l in open('pipelines/pilots/d05_spice_illumination/observations.jsonl') if l.strip() and json.loads(l)['body']=='Moon']; print({'body':'Moon','count':len(obs),'areas':sorted({o['area_id'] for o in obs}),'features':sorted({o['feature_id'] for o in obs}),'epoch':obs[0]['temporal_support']['start']})"

# Mars (illumination geometry exists)
python3 -c "import json; obs=[json.loads(l) for l in open('pipelines/pilots/d05_spice_illumination/observations.jsonl') if l.strip() and json.loads(l)['body']=='Mars']; print({'body':'Mars','count':len(obs),'areas':sorted({o['area_id'] for o in obs}),'features':sorted({o['feature_id'] for o in obs}),'epoch':obs[0]['temporal_support']['start']})"

# Ionizing radiation: explicit absence (no Gy/Sv/ionizing feature observations committed)
python3 -c "import json; paths=['pipelines/pilots/d05_earth_power/observations.jsonl','pipelines/pilots/d05_spice_illumination/observations.jsonl']; ion=[];\nfor p in paths:\n  for l in open(p):\n    o=json.loads(l)\n    if 'ionizing' in o.get('feature_id','') or o.get('unit') in ('Gy','Sv'): ion.append((p,o['feature_id']))\nprint({'committed_ionizing_observations': len(ion), 'examples': ion[:5], 'status':'unsupported/no_coverage in committed D05 pilots'})"
```

Observed results from committed artifacts (2026-11-15 worktree state): Earth count 10 / 5 areas / 2 irradiance features; Moon count 8 / 4 areas; Mars count 10 / 5 areas; committed ionizing observations 0.

## 5) Rebuild instructions

```bash
# Earth NASA POWER D05 pilot (bounded, network calls to NASA POWER API when run)
uv run python -m analog_pipelines.acquisition plan --scope pilot-d05-earth --dimension D05 --body Earth --provider nasa-power --area-id earth-atacama-desert --area-id earth-death-valley --area-id earth-lanzarote --area-id earth-devon-island --area-id earth-sahara-r1c1 --start 2024-01-01 --end 2024-01-07 --output var/plans/d05-earth-pilot.json
uv run python -m analog_pipelines.acquisition run --plan var/plans/d05-earth-pilot.json --output var/runs/d05-earth-pilot --cache var/cache/acquisition --workers 3 --resume
uv run python -m analog_pipelines.acquisition validate --run-dir var/runs/d05-earth-pilot
uv run python -m analog_pipelines.acquisition report --run-dir var/runs/d05-earth-pilot
uv run python -m analog_pipelines.acquisition release --plan var/plans/d05-earth-pilot.json --run-dir var/runs/d05-earth-pilot --output pipelines/pilots/d05_earth_power

# Moon/Mars SPICE illumination pilot (bounded; kernels downloaded once, cached gitignored)
uv sync --extra spice
uv run --extra spice python scripts/build_spice_illumination_pilot.py --download
uv run --extra spice python scripts/build_spice_illumination_pilot.py

# Source reachability audit (bounded HTTP metadata/range checks only)
python3 scripts/check_d03_d05_sources.py
python3 scripts/check_d03_d05_sources.py --json
```

## 6) Gaps, blockers, and limitations

- **No global/exhaustive coverage.** All committed products are bounded pilots/reference-site sets.
- **No local horizon/shadow/atmospheric irradiance model** in current SPICE pilot; geometry is flat-ellipsoid, single-instant, model-derived.
- **No committed ionizing-radiation dose/flux products** (Gy/Sv/particle flux with required metadata). Blockers are explicit; alternatives require bounded ingest + provenance + no-physics-free conversions.
- **Earth SPICE illumination observations not committed** in current pilot artifacts (matrix records computability/requirements and missing committed rows).
- **Shared contract/API/scoring changes** (if needed for location+time query schema or new typed fields) are routed to #141; this issue provides D05-owned matrix/docs/tests/manifests only.
- **Storage/performance:** committed D05 pilot payloads are small JSON/JSONL/raw API responses and manifest metadata; SPICE kernels (~18 MB) are cached locally (gitignored), not stored in Git. NASA POWER rebuild performs 5 bounded point API requests; SPICE rebuild reuses pinned cached kernels after first download.

## 7) Data sources, licenses, and checksums (evidence)

- NASA POWER daily point API: open/citation requested — raw checksums in `pipelines/pilots/d05_earth_power/manifest.json` and `assets.json`.
- NAIF SPICE generic kernels: public NAIF products with pinned URLs/SHA-256/sizes in `pipelines/pilots/d05_spice_illumination/manifest.json` and `assets.json`.
- Audit evidence: `docs/data-sources/d03-d05-thermal-illumination.json`, `docs/D03_D05_SOURCE_AUDIT.md`, `scripts/check_d03_d05_sources.py` (24/24 probes passed on 2026-11-15 as recorded in audit doc).
- Existing pilot observation/plan hashes: see each pilot `manifest.json` (`observations_sha256`, `plan_sha256`, raw asset hashes).
