# D02 mineral spectral evidence — bounded pilot

Official product status, observation-state semantics and measured counts for
Earth EMIT, Moon M3 and Mars CRISM. Built from pinned local artifacts only;
no granule was downloaded for this report.

- Pinned geology snapshot: `geo-snap-d1235d5f9defe6cd`
- Typed observations SHA-256: `9f8dd0ca82b0ba4b0cb0972643fbdf8eb3c3726db3855a34c76d16e482344eef`
- Source record: `pipelines/geology/records/geology-e920e7e3ae79e176.json`

## Source status matrix

| Body | Source | Official product | Access | Verification | Detection | Abundance |
|---|---|---|---|---|---|---|
| Earth | earth-emit-mineral | EMITL2BMIN (002), EMITL2BMIN (001); example EMIT_L2B_MIN_002_20260927T195248 | auth_required | verified_metadata | disabled | disabled |
| Moon | moon-m3-reflectance | CH1-ORB-L-M3-4-L2-REFLECTANCE-V1.0 (1.0); example M3G20090203T175131_V01_RFL | open | identified_metadata | disabled | disabled |
| Mars | mars-crism-mtrdr | hrl000040ff_07_if183j_mtr3 (MTR3); example hrl000040ff_07_sr183j_mtr3 (refined summary pair) | open | verified_metadata | disabled | disabled |

Access `auth_required` means anonymous metadata was read but granule assets need an
Earthdata Login. `verification_status` describes product metadata, not observation
validity. No row enables detection or abundance in this pilot.

## Actual counts

| Item | Value |
|---|---:|
| Areas (Earth / Moon / Mars) | 5 / 1 / 2 |
| Analyses | 21 |
| Exported typed-v2 observations | 56 |
| Mineral observations | 10 |
| Mineral detections | 0 |
| Mineral numeric values | 0 |
| Mineral abundance values | 0 |
| CRISM spectral-context values | 14 |
| EMIT granules downloaded | 0 |
| M3 granules downloaded | 0 |

Provider status (analyses):

| Body | Total | Status counts |
|---|---:|---|
| Earth | 15 | auth_required=8, no_coverage=2, unverified=5 |
| Moon | 2 | success=1, unverified=1 |
| Mars | 4 | context_only=2, success=2 |

EMIT CMR metadata candidates (no granule downloaded):

| Version | AOI rows | Candidate count | AOIs with hits | AOIs without hits | Status counts |
|---|---:|---:|---:|---:|---|
| 002 | 5 | 50 | 4 | 1 | auth_required=4, no_coverage=1 |
| 001 | 5 | 88 | 4 | 1 | auth_required=4, no_coverage=1 |

CRISM context covers 2 test AOIs with 7 parameters and 14 numeric values. Selected product(s): `hrl000040ff_07_if183j_mtr3`; native resolution 36.0 m. All values are dimensionless spectral parameters, not detections or abundances.

CRISM refined-summary medians by test AOI (context only):

| Parameter | mars-test-jezero-floor | mars-test-jezero-western-delta | Unit |
|---|---:|---:|---|
| BD1900_2 | 0.00605711 | 0.0102369 | dimensionless_spectral_parameter |
| CINDEX2 | 0.0125656 | 0.0168769 | dimensionless_spectral_parameter |
| D2300 | 0.00603283 | 0.0128377 | dimensionless_spectral_parameter |
| HCPINDEX2 | -0.0108012 | -0.0115553 | dimensionless_spectral_parameter |
| LCPINDEX2 | -0.0414389 | -0.0364162 | dimensionless_spectral_parameter |
| OLINDEX3 | 0.128674 | 0.12566 | dimensionless_spectral_parameter |
| SINDEX2 | -0.00527039 | -0.0107518 | dimensionless_spectral_parameter |

## Evidence-state semantics

| State | Numeric value | Valid fraction | Meaning | Never means |
|---|---|---|---|---|
| `detected` | optional | required_positive | A validated detector identified the mineral in at least one valid AOI pixel; a numeric band depth may accompany identification. | It is not an abundance, not a mass fraction, and not proof of aqueous alteration. |
| `not_detected_under_valid_observation` | forbidden | required_positive | Valid observation covered the AOI but the validated detector did not identify the mineral; the state is categorical. | It is not zero abundance, zero concentration or a quantitative null. |
| `no_coverage` | forbidden | optional | No provider pixel/footprint intersects the AOI, or the product is absent there. | It is not non-detection and it is not zero mineral content. |
| `unknown` | forbidden | optional | The footprint exists but no valid observation was obtained: provider outage, authentication block, missing quality/uncertainty, or unvalidated detector. | It is not evidence of absence and it is not zero. |
| `not_applicable` | forbidden | optional | The requested mineral or measurement is outside the valid scope of the body, sensor or product. | It is not an outage, not a footprint miss and not a non-detection. |

The typed-v2 export collapses `not_detected_under_valid_observation` to
`not_detected`; neither is a numeric zero. `no_coverage`, `unknown` and
`not_applicable` remain distinct and always carry a null value.

## Blockers

### `emit_auth` (all)

- **Summary**: Earthdata Login required for granule assets
- **Detail**: CMR metadata is anonymous, but every LP DAAC asset URL observed is lp-prod-protected. No credentials were requested, read or stored.
- **Resolution required**: An authorized operator must download one bounded granule and record its checksum; access remains opt-in and outside CI.

### `emit_decoder` (detection)

- **Summary**: NetCDF mineral-ID/quality/uncertainty decoding not validated
- **Detail**: The repository has a tested native-pixel aggregation core (summarize_identification) but no granule-validated EMIT reader, mineral-group lookup or quality-bit mapping.
- **Resolution required**: Validate the real group/band names, fill values, QA mask and per-pixel uncertainty against one pinned granule before enabling state.

### `emit_native_swath` (detection)

- **Summary**: Native swath geolocation not validated for the AOIs
- **Detail**: The counts in this report are CMR metadata candidates only. AOI intersection requires per-pixel geolocation, not a granule bounding box.
- **Resolution required**: Reproject/intersect native pixel centers and retain valid_fraction per AOI.

### `m3_registration` (detection)

- **Summary**: M3 registration/geolocation not validated in this repository
- **Detail**: The official L2 product is documented as pixel-located in the Mean Earth frame, but no M3 granule was downloaded and no per-pixel latitude/longitude, thermal correction or band alignment was checked against the lunar ROI.
- **Resolution required**: Download one bounded L2 granule, validate its detached label geolocation and band binning, and record pixel-center coverage.

### `m3_thresholds` (detection)

- **Summary**: No validated M3 mineral detection thresholds
- **Detail**: Continuum removal, band-depth placement and a detection threshold must be specified and tested before any detected/non-detected state.
- **Resolution required**: Pin an estimator and threshold with a citation and offline fixtures; keep reflectance and band depth separate from abundance.

### `m3_research_product` (all)

- **Summary**: Research-derived polar matrix cannot substitute for M3
- **Detail**: The pinned Zenodo v4 matrix has no CRS, transform, units, valid range, QA or uncertainty, and its registration is unverified.
- **Resolution required**: Do not extract AOI values from it; treat it as format-inspected metadata until an authoritative registration exists.

### `crism_thresholds` (detection)

- **Summary**: CRISM summary-parameter detection thresholds undocumented here
- **Detail**: OLINDEX3/LCPINDEX2/HCPINDEX2/D2300/BD1900_2/SINDEX2/CINDEX2 values are retained as medians, but no published threshold or DP-mask bit mapping was validated for mineral identification.
- **Resolution required**: Pin the CRISM ATBD/DP mask definition and validate thresholds on labelled spectra before emitting detected/non-detected states.

### `crism_continuum` (detection)

- **Summary**: No continuum removal or uncertainty propagation
- **Detail**: The summary parameters are published indices; no independent band-depth estimator, atmospheric correction or uncertainty model was applied.
- **Resolution required**: Define measurement semantics and uncertainty before any numeric mineral observation is exported.

### `crism_bounded_query` (all)

- **Summary**: Bounded product selection is not exhaustive coverage
- **Detail**: The ODE query is bounded to two test AOIs and the first five products, then one smallest refined-summary cube is selected.
- **Resolution required**: Any wider claim needs a new bounded audit with explicit product counts.

## Reproduce

```bash
uv run python scripts/build_mineral_evidence.py
uv run pytest pipelines/tests/test_mineral_evidence.py -o addopts='' -q
```

The builder verifies the pinned geology snapshot and the typed-v2 observations
checksum before writing. It performs no network access and writes only
`pipelines/geology/mineral-evidence.json` and `pipelines/geology/MINERAL_EVIDENCE.md`.
