# Mars D01 reproducible case: Jezero CTX window 01

Status: **verified offline from committed manifest and fixtures; bounded pilot only.**
Inventory version: `planetary-coverage-inventory-1.0.0`.
Case id: `mars-d01-jezero-ctx-window-01`.

This document is the human-readable companion to the machine-checked case emitted by
`analog_pipelines.planetary_coverage.mars_d01_case()`. It is a real-data case, not a
fixture promoted to an observation and not a global survey.

## Non-claims

- **Not global.** Four 5 km cells inside one 10 x 10 km coordinate window of the Jezero CTX
  mosaic. No Mars global or body-wide D01 coverage is claimed.
- **Not a geologic map.** The case contains coordinate windows and terrain derivatives only;
  it makes no landform, unit or mineral interpretation.
- **Not MOLA.** The comparable aggregate uses the 20 m CTX DTM. MOLA MEGDR 128 ppd
  (463.0576672 m) appears only in a separate evidence block.
- **No silent MOLA merge.** The two MOLA cells are `not_comparable` support evidence and are
  excluded from the CTX comparable count, bbox and aggregate.
- **Not Earth WGS84.** The source and analysis frames are Mars body-fixed
  (`Jezero_Crater_Projection`, `Mars_2000_Sphere`, R = 3,396,190 m). `EPSG:4326` is rejected.
- **No cross-body ranking or score.** The gate is disabled and the case emits no distance.

## Source and method

| Field | Committed value |
|---|---|
| Source id | `mars2020-jezero-ctx-dem-20m` |
| Dataset | Mars 2020 Science Investigation CTX DEM Mosaic (`M20_JezeroCrater_CTXDEM_20m`) |
| Provider | USGS Astrogeology Science Center / NASA Mars Missions |
| Product page | <https://astrogeology.usgs.gov/search/map/mars_2020_science_investigation_ctx_dem_mosaic> |
| Raster URL | <https://planetarymaps.usgs.gov/mosaic/mars2020_trn/CTX/ScienceInvestigationMaps_JPL/M20_JezeroCrater_CTXDEM_20m.tif> |
| Licence | `public domain` (USGS product page) |
| Vertical reference | stereo-derived elevations localized vertically to MOLA |
| Frame | `Mars_2000_Sphere` (IAU2000), body-fixed; planetocentric |
| Native / analysis resolution | 20 m source; fixed 250 m analysis grid |
| Method version | `coarse-planetary-dem-horn-1.0.0` |
| Earth coarse method | `coarse-terrain-250m-1.0.0` (different; never treated as equivalent) |
| Window | 4 cells x 5 km; region `mars-ctx-jezero-window-01` |
| Body lon/lat bbox | west 78.08240903206506, south 18.473312319636165, east 78.26027328954129, north 18.642018368217318 |
| Metric bbox | west 4,390,000 m, south 1,095,000 m, east 4,400,000 m, north 1,105,000 m |

The slope is the shared Horn 3x3 implementation on the reprojected elevation grid; the
central 20 x 20 pixels of each 22 x 22 padded cell are aggregated with
`coarse_features.extract_coarse_features` (mean slope, linear p90 slope, fraction strictly
below 5 degrees). The Earth coarse index instead consumes a published 7.5 arc-second
Geomorpho250 slope product, so every slope-derived planetary metric is `evidence_only`.

## Four CTX cells and committed values

| Candidate id | Row, col | Mean slope (deg) | P90 slope (deg) | Flat <5 deg | Fixture SHA-256 |
|---|---:|---:|---:|---:|---|
| `planet-coarse-v1:8e7ce607c8c9001d` | 0, 0 | 3.9766174118611417 | 6.966672456377621 | 0.69 | `510b60bc70ad09de985b3a05e5f0c16d2bc04190f79d3db3ca33de62664e7018` |
| `planet-coarse-v1:e81519aa779ebeca` | 0, 1 | 4.869835139787125 | 8.139509735536356 | 0.54 | `b6d18bd4bc4fc2490cc3b53006e314d26d502d43759b9f8b4dc133ace0fdeee6` |
| `planet-coarse-v1:2b7260352141922a` | 1, 0 | 4.495656328366134 | 7.194686209293172 | 0.6075 | `f703c61163f7dcebeaedb4d8fa07327a658df194319344b5eea659f800d975e7` |
| `planet-coarse-v1:591d325c440579f8` | 1, 1 | 2.288862153252994 | 4.719774101307491 | 0.9175 | `e3e2ce647fcc53368d6b8806e03d4ada4e3254f714e9e34e5f5145cc4e8790d9` |

Each fixture is a committed 22 x 22 float64 padded elevation GeoTIFF. `verify_manifest`
checks the SHA-256, reopens the fixture, asserts the planetary CRS, recomputes the Horn slope
and every committed feature at `rel_tol=1e-9`, and checks the valid-pixel count. A tampered
copy fails with `fixture_checksum_mismatch` or `feature_mismatch`.

## Aggregate over the four CTX cells

These are arithmetic summaries of the four committed cell values, not a new measurement and
not an error-weighted estimate.

| Feature | Cells | Mean of cell values | Min | Max |
|---|---:|---:|---:|---:|
| `coarse_slope_mean_deg` | 4 | 3.9077427583168487 | 2.288862153252994 | 4.869835139787125 |
| `coarse_slope_p90_deg` | 4 | 6.75516062562866 | 4.719774101307491 | 8.139509735536356 |
| `coarse_flat_fraction_lt5deg` | 4 | 0.68875 | 0.54 | 0.9175 |

## Paired MOLA evidence, kept separate

The MOLA MEGDR 128 ppd manifest (`mars-mola-meg128-evidence-01`) is verified in the same
offline run, but it is not part of the comparable aggregate.

- Cells: `planet-coarse-v1:bfd9fa9df7f8a644`, `planet-coarse-v1:a4c4f3fd39796da8` (2).
- Native and analysis support: 463.0576672 m; each cell has 11 native pixels, 5,093.6343392 m
  per side. It is not a 5 km same-support cell.
- Decision: `not_comparable` for every slope metric.
- Reason: MEGDR native support is 463.0576672 m/pixel (128 ppd), 1.85x coarser than the
  250 m analysis grid and 2.0x coarser than the 7.5 arc-second Earth coarse support; each
  5 km cell contains only about 11 native pixels, and MEGDR supplies interpolated median
  values where no MOLA shot falls in a bin. A 250 m grid would upsample an interpolated
  product, not measure the same quantity at the same support.
- The case reports `evidence_only: true`, `not_comparable: true`,
  `merged_into_comparable: false`, `used_for_ranking: false`, `distance: null`, and
  `score: null`.

The paired observed deltas (CTX minus MOLA) are recorded in the MOLA manifest and repeated
in the case only as a support/interpolation comparison:

| Pair | Delta mean slope (deg) | Delta p90 (deg) | Delta flat fraction |
|---|---:|---:|---:|
| `8e7ce607c8c9001d` / `bfd9fa9df7f8a644` | -0.7347485634580511 | -2.072774890966982 | +0.22735537190082655 |
| `591d325c440579f8` / `a4c4f3fd39796da8` | -0.2410163236505518 | +0.22347668225792905 | -0.008409090909090922 |

They combine support, median binning, interpolation, vertical registration and mosaic
effects. They are **not** a correction factor, an Earth calibration or a physical
equivalence.

## Cross-body gate

`cross_body_ranking.enabled` is `false`; the blocking features are
`coarse_flat_fraction_lt5deg`, `coarse_slope_mean_deg` and `coarse_slope_p90_deg`.
No Earth-Moon or Earth-Mars ranking is emitted. The case also asserts
`claims.distance_or_score_emitted = false`.

## Reproduce

```bash
# Human-readable case JSON, verified offline (no network, no timestamp)
uv run python -m analog_pipelines.planetary_coverage --mars-case

# Regenerate the committed body x D01-D06 inventory
uv run python -m analog_pipelines.planetary_coverage \
  --write docs/PLANETARY_COVERAGE_INVENTORY.json

# Focused tests, including fixture tamper and missing-index failures
uv run pytest pipelines/tests/test_planetary_coverage.py -q
uv run ruff check pipelines/src/analog_pipelines/planetary_coverage.py \
  pipelines/tests/test_planetary_coverage.py
```

## Limitations

- Four 5 km cells inside one 10 x 10 km coordinate window of the Jezero CTX mosaic; not Mars
  global coverage and not a geological unit map.
- The 20 m stereo DTM is aggregated to 250 m; mosaic seams and per-window vertical
  uncertainty are not validated.
- The value is a Horn 3x3 derivative of elevation, not the published Geomorpho250
  preprocessed slope product used by the Earth coarse index.
- No cross-body ranking is enabled; every slope-derived metric is `evidence_only`.
- On-demand D01 analysis is scoped: the Moon has a bounded global LOLA128 source, while Mars
  is supported only inside the Jezero CTX mosaic. MOLA 463 m is never upsampled as an answer
  for Mars outside Jezero.
