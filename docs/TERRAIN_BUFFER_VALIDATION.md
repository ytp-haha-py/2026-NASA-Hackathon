# D01 terrain-buffer validation (research evidence)

Status: **method-sensitivity evidence, not a production feature change.** The registry
still has five terrain similarity metrics. Curvature, profile/tangential curvature and
geomorphon are not registered, scored, or compared across bodies.

This page records the bounded real-DEM experiment for issue #40. The machine-readable
outputs are [terrain-buffer-validation.json](terrain-buffer-validation.json) and
[terrain-buffer-measurements.csv](terrain-buffer-measurements.csv). No raster is
committed.

## Exact support at 90 m

The production analysis core is 22 x 22 cells = 1,980 m. A method that needs `b` cells
of context on every side needs an input of `(22 + 2b)` cells. The normal 22 x 22 crop
supports only `max(22 - 2b, 0)^2` core cells.

| Method / research field | Support | Radius | Full input | Normal crop support | Edge loss | Min gate buffer (>=80%) |
|---|---|---:|---:|---:|---:|---:|
| slope_mean_deg, slope_p90_deg, flat_terrain_fraction | Horn 3x3 | 1 cell / 90 m | 24x24 / 2,160 m | 400/484 (82.6%) | 84 | 0 cells / 0 m |
| terrain_relief_p95_p05_m, roughness_std_m | window statistic / plane | 0 | 22x22 / 1,980 m | 484/484 | 0 | 0 |
| surface_curvedness_central, mean_curvature_central, gaussian_curvature_central, profile/tangential central | central 3x3 | 1 cell / 90 m | 24x24 / 2,160 m | 400/484 (82.6%) | 84 | 0 cells / 0 m |
| surface_curvedness_quadratic_r2 | 5x5 quadratic fit | 2 cells / 180 m | 26x26 / 2,340 m | 324/484 (66.9%) | 160 | 1 cell / 90 m |
| surface_curvedness_quadratic_r3 | 7x7 quadratic fit | 3 cells / 270 m | 28x28 / 2,520 m | 256/484 (52.9%) | 228 | 2 cells / 180 m |
| surface_curvedness_smoothed, profile/tangential smoothed | Gaussian sigma=90 m, 3-sigma truncation, + 3x3 derivative | 4 cells / 360 m | 30x30 / 2,700 m | 196/484 (40.5%) | 288 | 3 cells / 270 m |
| geomorphon_10class | 8 rays, search=12, skip=0, flat=1 deg | 12 cells / 1,080 m | 46x46 / 4,140 m | 0/484 (0%) | 484 | 11 cells / 990 m |

The 80% gate is a minimum, not full support. A four-cell method passes at three cells
but still loses a one-cell rim; the experiment reports both full-support and gate
buffers. `surface_curvedness_central` passes the gate on the existing crop but loses
17.4% of the core; `surface_curvedness_smoothed` and geomorphon fail outright.

## Experiment design

- The normal 1.98 km crop is the **central core of the same buffered read**. Both fields
  use identical source pixels, an identical aligned 90 m grid and the same bilinear
  reprojection. This isolates support/edge loss and avoids source-window rounding noise.
- One bounded `/vsicurl` read per site produces a 46 x 46 / 4,140 m grid. No full tile
  is intentionally saved or downloaded. Transferred bytes are parsed from the GDAL/CURL
  `Content-Range` responses; HEAD metadata and the server's full-object `Content-Length`
  are not counted as body bytes.
- Values on common cells are identical by construction; the measured differences are
  edge loss and the summary shift caused by excluding the outer core. This is the
  scientifically relevant comparison for a local operator.
- Curvature is an experimental central-difference/quadratic/Gaussian comparison. The
  profile/tangential forms use the Mitasova/Hofierka structure with a positive-convex
  sign convention, but they are **not** cross-validated against a pinned GRASS build.

## Sources and measured access

| Body | Site | Product / licence | Data bytes transferred | Valid |
|---|---|---|---:|---:|
| Earth | earth-atacama-desert | Copernicus GLO-90 COG, AWS Open Data; Copernicus Contributing Mission Data Access Licence | 3,119,949 | 100% |
| Earth | earth-death-valley | same | 3,835,948 | 100% |
| Earth | earth-lanzarote | same | 257,153 | 100% |
| Earth | earth-devon-island | same | 1,125,706 | 100% |
| Earth | earth-rio-tinto | same | 4,075,691 | 100% |
| Moon | moon-shackleton-rim-site04-roi2 | NASA PGDA LRO-LOLA Site04 5 m, CC0-1.0 as recorded in the manifest; Barker et al. (2021) citation requested | 21,004,288 | 100% |
| Mars | mars-jezero-ctx-window-01 | USGS/M2020 Jezero CTX DEM 20 m, public domain | 3,899,392 | 100% |

Earth tiles are the 2021 Copernicus GLO-90 release (Last-Modified 2022-05-09); Moon is
PGDA Site04 (2020-06-05); Mars is the USGS Jezero CTX mosaic (2022-11-10). Exact URLs,
ETags and timestamps are in the JSON. The Moon read succeeded once; later retries failed
at the provider with `RasterioIOError: Read failed`, so its single successful measured
record is retained and the provider instability is recorded rather than zero-filled.

## Measured edge loss

The counts are identical for every site because every site supplied a full 46 x 46 grid:

| Field | Normal supported | Buffered core supported | Edge loss |
|---|---:|---:|---:|
| central 3x3 fields | 400 | 484 | 84 (17.4%) |
| quadratic r2 | 324 | 484 | 160 (33.1%) |
| quadratic r3 | 256 | 484 | 228 (47.1%) |
| smoothed (4-cell) fields | 196 | 484 | 288 (59.5%) |
| geomorphon support | 0 | 484 | 484 (100%) |

Common-cell value differences are exactly zero. The summary shift is therefore the
edge-bias estimate. Examples (mean, m^-1):

| Site | Curvedness central normal -> buffered | Curvedness smoothed normal -> buffered | Mean curvature normal -> buffered |
|---|---|---|---|
| atacama-desert | 4.56e-6 -> 4.87e-6 | 8.77e-7 -> 9.07e-7 | -7.35e-9 -> 8.38e-8 |
| death-valley | 1.257e-3 -> 1.280e-3 | 7.54e-4 -> 8.34e-4 | 1.139e-4 -> 1.052e-4 |
| lanzarote | 9.11e-4 -> 8.68e-4 | 5.39e-4 -> 5.50e-4 | -1.284e-4 -> -1.215e-4 |
| devon-island | 4.39e-4 -> 4.47e-4 | 1.94e-4 -> 2.45e-4 | 1.17e-5 -> 3.56e-6 |
| rio-tinto | 1.025e-3 -> 1.011e-3 | 5.61e-4 -> 5.09e-4 | -3.85e-6 -> 1.81e-5 |
| Jezero CTX | 4.25e-4 -> 4.23e-4 | 2.59e-4 -> 2.26e-4 | 4.82e-7 -> 7.34e-6 |
| Shackleton rim | 4.66e-4 -> 4.50e-4 | 4.54e-4 -> 3.39e-4 | -2.45e-4 -> -2.16e-4 |

The shift can be small in absolute terms but large relative to the normal-crop mean
(e.g. mean curvature at Rio Tinto changes sign). Atacama is nearly flat and its field is
noise-dominated, so its method correlations are low and should not be over-interpreted.

## Method sensitivity on the buffered core

Pearson correlation / RMS difference between the unsmoothed central-difference
curvedness and the alternatives:

| Site | vs smoothed | vs quadratic r2 | quadratic r2 vs r3 |
|---|---|---|---|
| atacama-desert | 0.393 / 4.76e-6 | 0.178 / 4.73e-6 | 0.465 / 6.70e-7 |
| death-valley | 0.824 / 7.93e-4 | 0.838 / 7.37e-4 | 0.886 / 3.59e-4 |
| lanzarote | 0.930 / 5.17e-4 | 0.915 / 4.97e-4 | 0.971 / 2.72e-4 |
| devon-island | 0.803 / 3.30e-4 | 0.782 / 3.22e-4 | 0.812 / 1.22e-4 |
| rio-tinto | 0.821 / 6.81e-4 | 0.772 / 6.53e-4 | 0.920 / 2.87e-4 |
| Jezero CTX | 0.790 / 2.75e-4 | 0.742 / 2.77e-4 | 0.928 / 9.54e-5 |
| Shackleton rim | 0.858 / 2.25e-4 | 0.852 / 2.26e-4 | 0.978 / 7.32e-5 |

Correlations of 0.74-0.93 on non-flat terrain show that the choice of estimator is not a
cosmetic detail. The quadratic r2/r3 pair is more stable than central-vs-smoothed, but
none of these comparisons establishes a calibrated ranking scale.

## Geomorphon reference validation

GRASS `r.geomorphon` is still unavailable without a passwordless install, but an
independent reference implementation was used: **WhiteboxTools v2.4.0 `Geomorphons`**
(Jasiewicz & Stepinski 2013 algorithm, search=12, flat threshold 1 degree, 10 classes).
A synthetic 80 x 80 / 90 m DEM with a pit, peak, ridge, valley, slope and flat was
classified on the full DEM, the normal 22 x 22 crop, and a 46 x 46 crop with the required
12-cell halo. Evidence: `docs/terrain-geomorphon-reference.json`.

| Input | Valid cells | Core cells compared | 10-class agreement | Ternary agreement |
|---|---:|---:|---:|---:|
| Normal 22 x 22 | 400 / 484 | 400 | 1.0000 | **0.9550** |
| Buffered 46 x 46 | 1,936 / 2,116 | 484 | **1.0000** | **1.0000** |

The buffered core reproduces the full-DEM reference exactly on all 484 core cells for both
10-class forms and ternary codes. The normal crop loses 84 core cells and disagrees on
18/400 ternary codes (4.5%), confirming that edge-truncated rays change geomorphon
patterns. This validates the buffer requirement with an independent implementation; it
does **not** add a production geomorphon feature, assert class-name equivalence, or
validate real Earth/Moon/Mars class composition. Those remain open.

## Decision

- Keep the production terrain similarity at **five** metrics. No API, schema, manifest,
  scoring weight or seeded observation is changed.
- `surface_curvedness_central` is not safe on the existing crop despite passing the 80%
  gate: it drops 17.4% of core cells, and the summary shift is terrain-dependent.
- If curvature is ever promoted, require at least the full-support buffer (1 cell for the
  central prototype, 4 cells for the proposed smoothed method) and an independently
  calibrated scale plus reference agreement. If geomorphon is promoted, require 12 cells
  (46 x 46 / 4,140 m), the committed WhiteboxTools reference artifact, a pinned GRASS
  version when available, and class-agreement tests.
- The existing five registry metrics are numerically unchanged; the experiment recomputes
  them on the normal core and the buffered core and gets a maximum absolute difference of
  exactly 0.

## Reproduction

```bash
# No network, no writes
uv run python scripts/validate_terrain_buffers.py --dry-run --all

# Bounded real reads; writes only the JSON/CSV artifacts
uv run python scripts/validate_terrain_buffers.py \
  --earth-site atacama-desert --earth-site death-valley --earth-site lanzarote \
  --earth-site devon-island --earth-site rio-tinto --moon --mars --timeout 60 \
  --out docs/terrain-buffer-validation.json \
  --csv docs/terrain-buffer-measurements.csv

# Offline tests and lint
uv run pytest pipelines/tests/test_terrain_buffer.py pipelines/tests/test_terrain_research.py -q
uv run ruff check .
uv run ruff format --check .
```

The online reader test is opt-in only:

```bash
RUN_ONLINE_TERRAIN_BUFFER=1 uv run pytest pipelines/tests/test_terrain_buffer_online.py -q
```

## Limitations

- Seven bounded windows, not a global or exhaustive survey.
- Buffer radii are method-specific a-priori proposals, not calibrated universal scales.
- Bilinear resampling from 3 arc-second (Earth), 5 m (Moon) and 20 m (Mars) sources was
  not separated from source interpolation and vertical uncertainty.
- The Moon source is documented as ~90% interpolated at 5 m; the 90 m analysis grid does
  not create new information.
- COG/GeoTIFF block layout can make transferred bytes larger than the window footprint;
  the JSON records the actual ranges.
- No cross-body elevation or curvature scale is claimed.
