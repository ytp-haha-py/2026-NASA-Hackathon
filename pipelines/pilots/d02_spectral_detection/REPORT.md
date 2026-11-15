# D02 spectral-detection pilot — bounded CRISM/M3 evidence

Status: **evidence-only; decoder unvalidated; no mineral detection and no abundance**.
This is the Issue #42 follow-up to the source-status matrix. It reads part of a real
public PDS granule and records the exact band, geolocation and quality facts, but it
does **not** promote any spectral parameter to a mineral observation.

- Product: `hrl000040ff_07_if183j_mtr3` (Mars, CRISM MTRDR)
- Method version: `spectral-detection-0.1.0`
- Bounded read: 12000000 bytes; full cubes downloaded: 0
- Observations: 4 (all `unknown`); numeric values: 0; abundance values: 0

## What was verified

- Anonymous HTTPS byte-range access works on every requested CRISM asset; no credentials.
- The `IF` cube is 455 x 751 x 489 BSQ float32, 668,374,980 bytes; the label and detached
  wavelength TAB agree on 489 bands from 436.13 to 3896.76 nm in ascending order.
- Three detector gaps are present (631-710, 1010-1047, 2655-2800 nm); all bands are `bbl=1`.
- The grid is 36 m equirectangular on a 3,394,839.81 m Mars sphere (standard parallel 15 N);
  recomputing the label corners reproduces them to printed precision (<= 18 m offset).
- Both existing Mars test AOIs map to interior native windows:
  `mars-test-jezero-floor` rows 448-515 x columns 198-262;
  `mars-test-jezero-western-delta` rows 383-433 x columns 118-167.
- Moon M3 `M3G20090203T175131_V01_RFL` is public and range-readable, but its RFL/SUP
  headers expose no per-pixel latitude/longitude; geolocation needs the archive
  GEOMETRY/SPICE products. The 2.17 GB cube was intentionally not downloaded.

## Why there is no detection

The documented Viviano-Beck et al. (2014) parameters `D2300` (Table 2 #38) and
`OLINDEX3` (#14) were implemented with their published 3/5/7-band median kernels and
compared with the official refined-summary bands over 16 x 16 native-pixel sub-windows.

| AOI | D2300 RMSE | OLINDEX3 RMSE | Tolerance |
|---|---:|---:|---:|
| `mars-test-jezero-floor` | 0.01049 | 0.02365 | 0.001 |
| `mars-test-jezero-western-delta` | 0.01465 | 0.01544 | 0.001 |

All errors exceed the 0.001 acceptance tolerance and D2300 correlation is not
consistently positive, so the decoder is **not validated**. States are therefore
`unknown`, not `detected` and not `not_detected_under_valid_observation`.

Explicit pilot thresholds are recorded but not applied:

| Feature | Threshold | Status | Applied |
|---|---:|---|---|
| `d2300_2p3um_dropoff` | 0.02 | `explicit_pilot_unvalidated` | no |
| `olindex3_1um_olivine_index` | 0.1 | `explicit_pilot_unvalidated` | no |

## State semantics

| State | Meaning here |
|---|---|
| `detected` | A validated decoder and threshold identify the spectral feature in valid pixels. Never abundance. |
| `not_detected_under_valid_observation` | Valid pixels exist and a validated decoder does not identify the feature. Never a numeric zero. |
| `no_coverage` | No native pixel covers the AOI. Not evidence of absence. |
| `unknown` | Data exist but the decoder, mask or threshold is unvalidated. This pilot's only emitted state. |
| `not_applicable` | The measurement is outside the product/body scope. |

Band depth and spectral index are dimensionless. They are never wt%, vol%, mass fraction
or mineral abundance. `OLINDEX3` is a mafic 1 um index: it is never aqueous alteration,
water evidence or a Fe-oxide hydration claim. `D2300` alone is not proof of aqueous
alteration and does not uniquely identify a mineral.

## Blockers

### `crism_band_kernel_mapping`

- Exact band-index kernels for the summary parameters are unresolved
- Detail: The header wavelength vector is ascending but the TAB is detector-order (VNIR ascending, IR descending). Nearest-wavelength kernels reproduced neither D2300 nor OLINDEX3 within tolerance.
- Minimum missing validation: Map each Table 2 reference wavelength to the exact product band index used by the CRISM pipeline, then re-run the reproduction check.

### `crism_source_cube_and_flattening`

- The cube and preprocessing used for the shipped summary product are not confirmed
- Detail: The label exposes IF and Lambert-albedo products plus a destriping/flattening step described in Viviano-Beck et al. (2014) section 4.4/5; this pilot applied neither.
- Minimum missing validation: Confirm the input cube and implement the documented median/destriping step before comparing to the official summary bands.

### `crism_detection_thresholds`

- No validated mineral-identification thresholds
- Detail: Explicit pilot thresholds (D2300 > 0.02, OLINDEX3 > 0.10) are recorded but are not applied because the decoder is unvalidated and published thresholds are scene-dependent.
- Minimum missing validation: Validate a detector against labelled CRISM type spectra or published per-scene thresholds before emitting mineral states.

### `crism_dp_mask_definition`

- DP information-map bit definitions not validated
- Detail: The VNIR/IR mask read as 0 in all sampled pixels, but no SIS bit table was verified, so it cannot yet serve as a quality filter.
- Minimum missing validation: Pin the MTRDR SIS DP mask bit table and test it on a scene with known masked pixels.

## Reproduce

```bash
uv run python scripts/build_spectral_detection_pilot.py --check
uv run pytest pipelines/tests/test_spectral_detection.py -o addopts='' -q
uv run ruff check pipelines/src/analog_pipelines/geology/spectral_detection.py \
  scripts/build_spectral_detection_pilot.py pipelines/tests/test_spectral_detection.py
```

The builder is offline and deterministic. `windows.json` holds the recorded bounded-read
evidence (URLs, byte sizes, SHA-256 of metadata, observed values and the failed
reproduction); raw window bytes are not committed and live outside Git.
