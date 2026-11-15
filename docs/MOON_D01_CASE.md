# Reproducible Moon D01 on-demand AOI case (Issue #132)

Status: **engine implemented and offline-tested; API job wiring is owned by the integration
change.** This document describes the storage-independent planetary AOI engine in
`pipelines/src/analog_pipelines/planetary_aoi.py` and the exact committed-data Moon/Mars
cases it can reproduce. It does not claim a new global lunar survey, new observations
outside the committed pilot cells, or any D02-D06 measurement.

## What is reproducible

| Case | bbox (planetocentric lon/lat) | Analysis kind | Cells |
| --- | --- | --- | ---: |
| Moon LOLA128 window 01 (primary) | `19.85, -10.15, 20.15, -9.85` | `committed_reference_fixture` | 4 |
| Mars Jezero CTX window 01 | `78.08, 18.47, 78.27, 18.65` | `committed_reference_fixture` | 4 |

Both cases read only the committed coarse manifests and the ~4 KB elevation fixtures under
`samples/planetary/coarse/fixtures/`. No raster is downloaded by default and no test touches
the network.

The Moon bbox is a user AOI inside the single committed 10 x 10 km LOLA128 coordinate
window at 20E, 10S. The four selected cells are:

- `planet-coarse-v1:c22eb2732afdfa9a`
- `planet-coarse-v1:d7171e6af899065b`
- `planet-coarse-v1:655983fe3845a2e8`
- `planet-coarse-v1:59b32ff4da5ab528`

The Mars bbox selects the four same-support CTX cells only. The two MOLA MEGDR cells at
Jezero are **not** merged into this analysis; they remain a separate evidence-only region.

## Method and provenance

- Engine method: `planetary-terrain-horn-250m-aoi-1.0.0`.
- Source coarse method: `coarse-planetary-dem-horn-1.0.0`.
- Source coarse schema: `planetary-coarse-reference-1.0.0`.
- Analysis grid: 250 m, 5 km coarse cell (20 x 20 core pixels, one padded Horn rim).
- Slope: shared `analog_pipelines.features.horn_slope_degrees` Horn 3x3 derivative.
- Aggregation: shared `analog_pipelines.coarse_features.extract_coarse_features`
  (`coarse_slope_mean_deg`, `coarse_slope_p90_deg`, `coarse_flat_fraction_lt5deg`).
- Moon source: NASA GSFC PGDA / LRO-LOLA `LDEM128_PA_pixel_202405`, 128 ppd, native support
  236.90117518866782 m, lunar sphere R = 1,737,400 m.
- Mars source: USGS Mars 2020 Science Investigation CTX DEM Mosaic, 20 m, aggregated to the
  fixed 250 m analysis grid, Mars sphere R = 3,396,000 m for the body-fixed geographic AOI
  frame. The source projected CTX cells keep their own Mars_2000_Sphere Jezero CRS.
- Moon licence: not stated on the PGDA product page; public NASA archive with a citation
  requirement (`https://science.data.nasa.gov/about/license`).
- Mars CTX licence: public domain.
- Mars MOLA evidence cells: native 463.0576672 m support with interpolated median bins; not
  comparable to a 250 m on-demand analysis and never silently promoted.

Frame rule: Moon/Mars longitudes and latitudes are body-fixed planetocentric coordinates.
`EPSG:4326`, Earth WGS84 and Earth UTM are never used. The result `crs` strings are:

- Moon: `+proj=longlat +R=1737400 +no_defs`
- Mars: `+proj=longlat +R=3396000 +no_defs`

## Moon result (committed fixtures)

`analyze_planetary_aoi("Moon", {"west": 19.85, "south": -10.15, "east": 20.15, "north": -9.85})`
returns:

| Field | Observed value |
| --- | --- |
| `area_id` | `planet-aoi-moon-122db1830912` |
| `analysis_kind` | `committed_reference_fixture` |
| `coordinate_frame` | `lunar_planetocentric` |
| `valid_fraction` | `1.0` |
| `coarse_slope_mean_deg` | `11.459459413931441` |
| `coarse_slope_p90_deg` | `30.035049395554832` |
| `coarse_flat_fraction_lt5deg` | `0.22125` |
| `requested area_km2` | `81.4983365073781` |
| `analysis (union of cells) area_km2` | `100.06378262075377` |

The analysis bbox is the union of the selected 5 km cell footprints:
`19.832481429354193 .. 20.167518570645804` longitude and
`-10.16488943108404 .. -9.835068981384325` latitude. It differs from the requested bbox by
design, and the result includes an explicit warning that the D01 values cover the cell union,
not an exact polygon intersection.

Per-cell features are reported in `cell_results` and reproduce the committed manifest values
within `1e-9` relative tolerance after reopening each fixture and recomputing Horn slope plus
the shared aggregation. `valid_fraction` is computed over all valid core slope pixels in the
union of selected cells, not by averaging per-cell percentages.

Deterministic identity:

- `area_id` = `planet-aoi-moon-` + first 12 hex characters of a SHA-256 over body, normalized
  bbox, engine method and analysis resolution.
- `cache_key` = full SHA-256 over body, bbox, engine method, index checksum, source manifest
  checksums and selected source fixture checksums.
- `checksum` = full SHA-256 of the result payload excluding the checksum itself. It changes
  only if the method, selected cells, features, source fixtures or support statements change.

For the committed request above, the observed `cache_key` is
`48ce74cd162733ed947b31908f3c6ec850dd5a118c1a61b86bdaae4d149479eb` and the observed result
`checksum` is
`sha256:7438287b550a0bcc1a5ac400292d7e097037273ffe5a8c85b364f903b6a1183a`.

## Mars result (committed fixtures)

`analyze_planetary_aoi("Mars", {"west": 78.08, "south": 18.47, "east": 78.27, "north": 18.65})`
returns:

| Field | Observed value |
| --- | --- |
| `area_id` | `planet-aoi-mars-8b6056ada3e1` |
| `analysis_kind` | `committed_reference_fixture` |
| `coordinate_frame` | `mars_planetocentric` |
| `valid_fraction` | `1.0` |
| `coarse_slope_mean_deg` | `3.9077427583168487` |
| `coarse_slope_p90_deg` | `7.234036504613219` |
| `coarse_flat_fraction_lt5deg` | `0.68875` |

The result contains only the four CTX candidate IDs
`planet-coarse-v1:8e7ce607c8c9001d`, `planet-coarse-v1:e81519aa779ebeca`,
`planet-coarse-v1:2b7260352141922a`, `planet-coarse-v1:591d325c440579f8`. The MOLA evidence
IDs `planet-coarse-v1:bfd9fa9df7f8a644` and `planet-coarse-v1:a4c4f3fd39796da8` are absent
from `cells`/`cell_results` and appear only in `planetary_body_coverage("Mars")` as
`evidence_only_cell_ids`, with their own region and bbox.

`planetary_body_coverage("Mars")` reports exactly 6 committed cells: 4 analysis-eligible CTX
cells and 2 evidence-only MOLA cells in 2 regions. `planetary_body_coverage("Moon")` reports
4 cells in 1 region.

The Mars bbox is 10.68 km wide on the Mars sphere, so the planetary engine's default maximum
side is 12 km (the minimum remains 1 km). Callers can tighten the bounds in
`validate_planetary_aoi`, never widen them.

## D01-D06 support contract

| Code | On-demand status | Meaning |
| --- | --- | --- |
| D01 | `present` | Recomputed coarse slope mean/p90/flat fraction from verified fixtures or one labelled live read. |
| D02 | `unsupported_for_on_demand_analysis` | No verified on-demand mineralogy product; stored typed records are not interpolated. |
| D03 | `unsupported_for_on_demand_analysis` | No thermal measurement for the AOI. Moon/Mars stored D03 evidence is marked `stored_evidence_only`. |
| D04 | `unsupported_for_on_demand_analysis` | No water/ice measurement; stored Moon D04 evidence is not confirmed ice for this AOI. |
| D05 | `unsupported_for_on_demand_analysis` | No observed illumination/radiation measurement; stored model geometry is noted separately. |
| D06 | `unsupported_for_on_demand_analysis` | No verified on-demand regolith/surface-property product. |

Every D02-D06 entry includes a reason and `value: None`. No zero, empty string or synthetic
value is returned as a measurement.

## Public engine surface for API integration

The API job layer should import only these names from
`analog_pipelines.planetary_aoi`:

```python
from analog_pipelines.planetary_aoi import (
    PLANETARY_AOI_METHOD_VERSION,
    PLANETARY_AOI_SCHEMA_VERSION,
    PlanetaryAoiError,
    PlanetaryAoiRequest,
    PlanetaryAoiResult,
    PlanetaryBodyCoverage,
    PlanetaryCellCoverage,
    analyze_planetary_aoi,
    planetary_body_coverage,
    validate_planetary_aoi,
)
```

Expected use:

```python
request = validate_planetary_aoi("Moon", bbox)  # area_id, cache_key, geometry, crs
result = analyze_planetary_aoi("Moon", bbox)  # offline by default
payload = result.to_payload()  # JSON-safe versioned payload
```

`analyze_planetary_aoi(..., allow_remote=True)` enables only the **Moon** bounded live path
when no committed cell covers the AOI. It performs one 5 km window read from the verified
NASA PGDA LOLA128 URL in a local lunar azimuthal-equidistant CRS and labels the result
`analysis_kind='live_bounded_read'`, `provider_status[...].network_used=True`, with an explicit
"new bounded read, not a committed fixture" warning. It never runs in tests or by default.

Errors are `PlanetaryAoiError` with `.code` and `.message`. Important codes for the job layer:
`unsupported_body`, `invalid_bbox`, `bbox_too_wide`, `aoi_too_small`, `aoi_too_large`,
`out_of_coverage`, `index_missing`, `index_corrupt`, `manifest_missing`, `manifest_corrupt`,
`fixture_missing`, `fixture_checksum_mismatch`, `fixture_metadata_mismatch`,
`fixture_recompute_mismatch`, `live_read_failed`. Out of coverage is always an explicit error,
never an empty successful result.

## What the engine does not do

- It does not use the Moon/Mars basemap image extent as scientific coverage.
- It does not create a new global grid or a new global survey claim.
- It does not merge evidence-only MOLA cells into comparable coverage.
- It does not fabricate D02-D06 values from stored typed evidence for other areas.
- It does not score Moon/Mars against Earth; every slope-derived planetary metric remains
  `evidence_only` (or `not_comparable` for 463 m MOLA) in the coarse comparability gate.
- It does not download a global DEM; the live Moon branch is one bounded 5 km window and is
  opt-in.

## Reproduction commands

```bash
uv run pytest pipelines/planetary/tests/test_planetary_aoi.py -q
uv run ruff check pipelines/src/analog_pipelines/planetary_aoi.py \
  pipelines/planetary/tests/test_planetary_aoi.py
uv run ruff format --check pipelines/src/analog_pipelines/planetary_aoi.py \
  pipelines/planetary/tests/test_planetary_aoi.py
uv run python pipelines/build_planetary_coarse.py --verify
```

The offline tests cover:

- the Moon reproducible bbox, 4 committed cells, aggregate features, `valid_fraction`,
  deterministic `area_id`/`cache_key`, Moon CRS/frame and result payload serialization;
- fixture checksums and source provenance (PGDA LOLA URL, licence, source resolution);
- D02-D06 unsupported entries with no fabricated values;
- the Moon out-of-coverage error and the monkeypatched live-read labelling path;
- the Mars CTX reproducible bbox, MOLA evidence separation and body isolation;
- antimeridian splitting, polar validation, too-wide/too-large/too-small AOIs, invalid body,
  invalid latitude and reversed bounds;
- missing/corrupt index, corrupt manifest, missing fixture and tampered fixture errors.
