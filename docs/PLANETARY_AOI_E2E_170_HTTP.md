# Planetary AOI end-to-end acceptance (#170 Goal H)

Status: **passing offline on the committed data snapshot** (`dv-b46f3bad38ea`).
This note documents the live-HTTP gate added for Issue #170 Goal H. It is a companion
to the in-process pytest gate and does not modify the #132/#160 analysis,
persistence or API implementation.

## Run

```bash
bash scripts/accept_planetary_aoi_http_170.sh
uv run pytest apps/api/tests/test_planetary_aoi_http_e2e_170.py -q
```

Both are offline:

- isolated temporary SQLite database, seeded from committed manifests;
- no raster download, no network, no R2, no shared `./var/analog.db`;
- no web app and no shared service;
- the Moon live bounded read is skipped because
  `ANALOG_PLANETARY_ALLOW_NETWORK=false`.

## What is real (verified end to end)

| Body | Committed source window | Cells | Method | Result |
|---|---|---|---|---|
| Moon | LOLA128 pixel, 19.832–20.168°E, 10.165–9.835°S | 4 × 250 m | `planetary-terrain-horn-250m-aoi-1.0.0` | values recomputed from checksum-verified fixtures |
| Mars | M2020 Jezero CTX DEM, 78.082–78.260°E, 18.473–18.642°N | 4 × 250 m | same shared Horn/coarse aggregation | values recomputed from checksum-verified fixtures |

The gate POSTs real AOIs to a running API, polls the durable job, then checks:

- returned D01 features equal the committed per-cell manifest values (single-cell AOIs);
- 6 distinct real covered AOIs complete: 3 Moon cells + 3 Mars cells, plus the full
  4-cell blocks for aggregation;
- typed persistence exposes only `D01`, `score_role=evidence_only`,
  `not_earth_wgs84`, and a planetary CRS — never Earth `EPSG:4326`;
- the new typed area appears in `/api/v1/planetary/map-features`;
- a repeated bbox is served from the job cache with the same job/area and values;
- provider status is `verified_committed_fixture`, `network_used=false`,
  `fixture_checksums_verified=true`, with the expected cell count.

Observed values from the passing run (mean slope°, p90 slope°, flat fraction < 5°):

```text
Moon  planet-coarse-v1:c22eb2732afdfa9a  13.620  29.807  0.193
Moon  planet-coarse-v1:d7171e6af899065b  17.657  31.392  0.230
Moon  planet-coarse-v1:655983fe3845a2e8   7.972  13.434  0.207
Moon  4-cell block                       11.459  30.035  0.221
Mars  planet-coarse-v1:8e7ce607c8c9001d   3.977   6.967  0.690
Mars  planet-coarse-v1:e81519aa779ebeca   4.870   8.140  0.540
Mars  planet-coarse-v1:2b7260352141922a   4.496   7.195  0.608
Mars  4-cell block                        3.908   7.234  0.689
```

These are bounded pilot values, not global or exhaustive coverage.

## Dispersed latitude bands: covered vs explicit no-data

The committed offline coverage is **one 5 km coarse window per body**, both at low
latitude. There is no committed analysis-eligible coarse cell at other latitudes or
longitudes yet, so the dispersed requests are real planetocentric AOIs that must fail
honestly:

| Body | AOI | Expected |
|---|---|---|
| Moon | committed window (≈10°S) | real values (covered) |
| Moon | 45°N, 30°W | `out_of_coverage`, no values |
| Moon | 82°N, 120°E | `out_of_coverage`, no values |
| Mars | committed window (≈18.6°N) | real values (covered) |
| Mars | 45°S, 150°E | `out_of_coverage`, no values |
| Mars | 78°N, 60°W | `out_of_coverage`, no values |

The API is asserted to return `status=failed`, `error.code=out_of_coverage`,
`result=null`, and no D01 feature ids. For Moon the error message must document that
the bounded live read requires `allow_remote=True`, which this offline gate does not
enable. Nothing is zero-filled, interpolated from another latitude, or copied from a
neighbouring pilot.

## Negative and failure behavior

| Case | Expected |
|---|---|
| `0°, 0°` Moon/Mars | explicit `out_of_coverage`; no result |
| oversized AOI (10° block) | HTTP 422 `aoi_too_large` |
| longitude span > 180° | HTTP 422 `bbox_too_wide` |
| sub-kilometre AOI | HTTP 422 `aoi_too_small` |
| latitude beyond the pole | HTTP 422 `invalid_bbox` |
| antimeridian bbox (179.9 → -179.9) | accepted, normalized (`west > east`); then `out_of_coverage` |
| polar bbox (89.5°N / 89.5°S) | accepted in the body frame; then `out_of_coverage` |
| deleted committed Moon fixture | HTTP 422 `fixture_missing`; no values |
| corrupted committed Mars fixture (pytest) | HTTP 422 `fixture_checksum_mismatch`; no values |

The source-failure checks copy the committed catalog to a temporary directory,
delete or corrupt one real fixture, and point a second API process at the copy. This
proves source-integrity failures are explicit rather than silently ignored.

## What is stubbed, disabled, or not yet available

| Item | Current state |
|---|---|
| Moon live bounded LOLA128 read | implemented in the engine but **not exercised offline**; requires `allow_remote=True`/network. The gate skips it with an explicit reason and asserts the no-data path documents it. |
| Mars live bounded read | **not implemented**; Mars outside committed cells stays `out_of_coverage`. |
| Mid/high-latitude committed coverage | **not available**; only the single low-latitude 5 km window per body exists. |
| Mars MOLA MEGDR 128 ppd cells | **evidence-only** (native ≈463 m support); excluded from 250 m on-demand analysis and never merged. |
| D02–D06 on-demand values | **unsupported by design**. They are returned as explicit `unsupported_for_on_demand_analysis` statuses with `value=null`; stored typed evidence is not interpolated onto a new AOI. |
| Moon/Mars D06 regolith values | explicit `no_coverage` evidence records; no numeric value is fabricated. |
| Cross-body ranking | **disabled**; no planetary coarse quantity is validated as equivalent to the Earth index. |
| Antimeridian AOI that is actually covered | no committed crossing coverage exists. Validation/normalization and the explicit no-data outcome are tested; successful persistence of a wrapping bbox is not claimed. |
| Global/exhaustive survey | **never claimed**; this is a bounded committed pilot check. |

## Files

- `scripts/accept_planetary_aoi_http_170.sh` — live-HTTP gate (server lifecycle,
  seeding, all positive/negative cases, damaged-catalog source failure).
- `apps/api/tests/test_planetary_aoi_http_e2e_170.py` — HTTP/job-boundary pytest
  coverage: exact committed values, dispersed no-data, dateline/polar, error codes,
  real damaged-fixture source failure.

The canonical in-process `scripts/accept_planetary_aoi_170.sh` and
`apps/api/tests/test_planetary_aoi_e2e_170.py` are owned by the parallel round-one
Goal H artefacts in this worktree; this gate is intentionally complementary and does
not overwrite them.
