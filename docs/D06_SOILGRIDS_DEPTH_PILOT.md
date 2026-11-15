# D06 SoilGrids multi-depth pilot (Issue #46)

Status: **implemented, bounded, evidence-only Earth predictions.** It is not a
measurement campaign, not global coverage, and not a similarity-scoring feature.

The earlier pilot `pipelines/pilots/d06_earth_soilgrids/` contains 75 observations at
0–5 cm. This extension adds 5–15 cm, 15–30 cm and 30–60 cm at the same five AOIs,
same five properties and same three statistics, and re-exports 0–5 cm in the same
self-contained release with the current evidence flags.

## Source and exact version

| Field | Value |
|---|---|
| Provider | ISRIC — World Soil Information |
| Product | SoilGrids 2.0, 250 m global soil property predictions |
| Dataset ID | `soilgrids-250m-v2` |
| Method version | `soilgrids-wcs-2.0.0` |
| Service | OGC WCS 2.0.1, `https://maps.isric.org/mapserv` |
| Access | Public HTTPS, no credentials |
| Licence | CC BY 4.0; cite ISRIC SoilGrids 2.0 |
| Licence URL | <https://docs.isric.org/globaldata/soilgrids/> |
| Native resolution | 250 m |
| Redistribution | Allowed under CC BY 4.0 with attribution; raw TIFF responses are not committed |
| Reference | Poggio et al. 2021, SOIL 7, 217–240, <https://doi.org/10.5194/soil-7-217-2021> |
| Evidence class | **Model prediction, not a measurement** |

### Request contract

```text
GET https://maps.isric.org/mapserv
  ?map=/map/{property}.map
  &service=WCS&version=2.0.1&request=GetCoverage
  &CoverageId={property}_{depth}_{statistic}
  &subsettingCrs=http://www.opengis.net/def/crs/EPSG/0/4326
  &subset=Long({west},{east})&subset=Lat({south},{north})
  &format=image/tiff
```

- Properties: `clay`, `sand`, `silt`, `soc`, `bdod`.
- Depth intervals: `0-5cm`, `5-15cm`, `15-30cm`, `30-60cm`.
- Statistics: `mean`, `Q0.05`, `Q0.95`.
- Requests are bounded to the AOI envelope; no full-world request or tile download.
- At most one request every 0.25 s per process; up to 4 attempts with exponential
  backoff, honouring `Retry-After`, capped at 30 s.
- Responses larger than 5 MB or non-TIFF bodies are rejected.
- Physical zero values are preserved. A tile with no valid cells becomes
  `no_coverage` with no value; it is never zero-filled.

## Committed pilot

`pipelines/pilots/d06_earth_soilgrids_depths/`

| File | Purpose |
|---|---|
| `observations.jsonl` | 300 typed observations |
| `assets.json` | 300 source response checksums (SHA-256) |
| `requests.jsonl` | Per-request status, HTTP status, checksum, bytes, endpoint, error |
| `plan.json` | Reproducible bounded plan (300 tasks) |
| `qa.json` | 100 area/property/depth groups with mean/Q0.05/Q0.95 |
| `manifest.json` | Provenance, counts, checksums, licence, rate-limit settings |
| `REPORT.md` | Human-readable counts and reproduction commands |

Raw TIFF responses are **not committed** (`*.tif` is gitignored). Their SHA-256
checksums are recorded per request and per asset.

### Observed result (2026-11-15)

| Item | Value |
|---|---:|
| Plan tasks | 300 |
| Succeeded requests | 300 |
| Failed / unavailable | 0 |
| Observations | 300 |
| Detected / no_coverage | 300 / 0 |
| QA groups complete | 100 / 100 |
| Areas | 5 |
| Observations per depth | 75 |

| Depth | Observations |
|---|---:|
| 0–0.05 m | 75 |
| 0.05–0.15 m | 75 |
| 0.15–0.3 m | 75 |
| 0.3–0.6 m | 75 |

Depth is carried per observation as `depth_support.kind=depth_interval` with
`min_m`/`max_m`. Observation and feature IDs include the depth label, so depths
cannot be silently averaged or scored together.

## Provenance and uncertainty

- Every observation has `score_role=evidence_only`.
- Quality flags include `model_derived_soil_property`,
  `model_prediction_not_measurement`, `250m_native_grid`,
  `global_model_not_local_sample`, `depth_interval_<label>` and `statistic_<code>`.
- The AOI value is the mean of native 250 m model cells over the AOI envelope. It is
  not a SoilGrids product cell value at a point.
- Model uncertainty is represented by the separate Q0.05/Q0.95 prediction-quantile
  observations at the same area/property/depth. `qa.json` records their spread.
  That spread is **not** a confidence interval for the AOI mean and not a validation
  error estimate.
- `bdod` is bulk density of the fine earth fraction (cg cm-3); it excludes coarse
  fragments and is not total regolith bulk density.
- Clay, sand and silt are separate g kg-1 predictions. This pilot keeps them as separate
  numeric observations rather than normalizing them into a particle-size distribution;
  such a conversion would be a derived product with its own bin and basis semantics.

## Reproduce

```bash
# Offline dry run: build the plan only
uv run python scripts/build_soilgrids_depth_pilot.py --dry-run

# Live bounded run; writes var/plans, var/runs, var/cache and the committed release
uv run python scripts/build_soilgrids_depth_pilot.py --resume --workers 2

# Verify and import into an additive typed-v2 SQLite database
uv run python - <<'PY'
from analog_pipelines.acquisition.release import verify_release_integrity
print(verify_release_integrity("pipelines/pilots/d06_earth_soilgrids_depths"))
PY
uv run python scripts/import_typed_release.py \
  --release pipelines/pilots/d06_earth_soilgrids_depths \
  --database var/d06-depths.db
```

The 0–5 cm rows deliberately reuse the observation IDs of the earlier pilot. Importing
both releases upserts those rows instead of double-counting them.

## Offline checks

```bash
uv run pytest pipelines/tests/test_soilgrids_depths.py -q
uv run pytest pipelines/tests/test_regolith_semantics.py -q
uv run pytest apps/api/tests/test_typed_api.py -q
```

The live endpoint check is opt-in and never part of CI:

```bash
ANALOG_LIVE_NETWORK=1 uv run pytest pipelines/tests/test_soilgrids_live.py -q -m network
```

## Limitations and blockers

- Five AOIs only; not a global, exhaustive or representative survey.
- SoilGrids itself is a model prediction trained on sparse samples; it is not a local
  soil measurement and must not be presented as `observed` in the legacy v1 sense.
- Four of the six standard SoilGrids depth intervals are registered here. 60–100 cm and
  100–200 cm are intentionally out of scope.
- No Moon or Mars regolith measurement is provided; see
  [`D06_REGOLITH_SEMANTICS.md`](D06_REGOLITH_SEMANTICS.md) and
  `contracts/d06-regolith-semantics-v1.json`.
- If the service is unavailable, the request log records the observed failure and the
  release contains no fabricated value; a partial run is reported as partial.
