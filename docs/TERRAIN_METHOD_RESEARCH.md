# Curvature and landform composition: research gate

Status: analytic prototypes tested, **not production features**. No curvature or
geomorphon value is emitted in observed manifests, API targets, or similarity scores.
Flat Terrain Fraction is independently implemented and recalculated on all 136
indexed terrain windows; it does not depend on these prototypes.

**2026-11-15 update:** a bounded real-DEM buffer experiment now measures the exact
edge loss and estimator sensitivity on five Earth Copernicus GLO-90 windows, one Moon
LOLA window and one Mars CTX window. See
[TERRAIN_BUFFER_VALIDATION.md](TERRAIN_BUFFER_VALIDATION.md) and the portable
`terrain-buffer-validation.json` / `terrain-buffer-measurements.csv` artifacts. The
normal 22x22 crop supports 400/484 central-curvature cells, 196/484 smoothed-curvature
cells and 0/484 geomorphon cells at search=12. Values are unchanged on common cells;
the measured difference is edge coverage and summary bias. Geomorphon remains blocked
because no GRASS `r.geomorphon` reference output is available. Promotion conditions
below are unchanged.

## Curvature candidate

A signed average can cancel hills with depressions, while mean curvature alone can
miss a saddle. The experimental `surface_curvedness` uses surface principal
curvatures: `C = sqrt((k1²+k2²)/2) = sqrt(2H²-K)` in **m⁻¹**. For a metric elevation
graph z(x,y), p=z_x, q=z_y, r=z_xx, s=z_xy, t=z_yy:

```
H = ((1+q²)r - 2pqs + (1+p²)t) / (2(1+p²+q²)^(3/2))
K = (rt-s²) / (1+p²+q²)²
```

The prototype uses central 3×3 finite differences. Incomplete neighborhoods remain
NaN, including the outer rim. Proposed regional summary: RMS(C) over supported core
cells; optionally retain p90(C) for QA. Neither sign cancellation nor absolute
planetary altitude enters this definition. Analytic plane, bowl, inverted bowl and
saddle tests verify the mathematics and units. They do not establish accuracy on
observed DEMs or justify a normalization scale for ranking.

For production validation, first compare direct derivatives against a quadratic fit
and explicit smoothing. A **candidate**, not calibrated, common scale is Gaussian
sigma=90 m, truncated at 3 sigma, plus a 90 m derivative radius: four buffer cells
(360 m) on every side, 30×30 input cells for a 22×22 core (2700 m footprint).
No smoothing across missing data, mirror/nearest padding, or slope-normalized
curvature may be silently substituted. Test sensitivity to noise, source
interpolation, resampling and slope, and pin the selected method/version before
registration. GRASS documents both
[3×3 derivative curvature](https://grass.osgeo.org/grass-stable/manuals/r.slope.aspect.html)
and [quadratic fitting at explicit scales](https://grass.osgeo.org/grass-stable/manuals/r.param.scale.html).
The curvedness choice and buffer above are this project's research proposal.

## Geomorphon candidate

Use a pinned reference GRASS `r.geomorphon` implementation, not an improvised 3×3
peak/valley heuristic. The canonical class order is Flat, Peak, Ridge, Shoulder,
Spur, Slope, Hollow, Footslope, Valley, Pit. Candidate parameters for validation:
90 m projected metric cells, search=12 cells (1080 m), skip=0, flat=1 degree,
dist=0, comparison=anglev1, no edge extension. These are proposed scales, not
scientifically validated universal thresholds. See the
[official algorithm and parameter semantics](https://grass.osgeo.org/grass-stable/manuals/r.geomorphon.html).

A full search neighborhood requires twelve buffer cells per side: **46×46** input
cells, or **4140 m**, plus native resampling support. Classify with the buffer,
then summarize only the original 1980 m core. Require >=80% valid core elevations
and >=80% core cells with complete classification support. Unsupported directions
must not be shortened at a raster edge or converted to Flat. Class fractions use
valid classified core area, sum to one, and are never fabricated for missing data.
Validate flat surfaces, isolated summits/pits, ridges, valleys and transition
classes against the pinned reference before processing observed data.

The proposed `landform_hellinger` compares the **whole ten-class composition**:
`H(a,b) = ||sqrt(a)-sqrt(b)||₂ / sqrt(2)`, range 0–1. It contributes one weighted
component to a future mixed distance; ten proportions are not ten independently
weighted scalar metrics. Tests cover identical/disjoint distributions, symmetry,
invalid dimensions, nonfinite values, negative values and missing distributions.
No production weight or normalization calibration is claimed.

## Observed buffer blocker and integration decision

All cached LOLA terrain crops inspected in this revision are **2070×2070 m**.
They contain a 1980 m analysis window with only 45 m padding per side, versus the
candidate 360 m curvature / 1080 m geomorphon buffers. They cannot provide complete
neighborhoods for all core cells. Reusing or upsampling these crops would not add
real information. The underlying products may support larger windows at some ROI
centres; this must be checked individually, and limited products/edges may remain
unavailable. No additional buffered raster has been downloaded or validated here.

Therefore both metrics remain deferred from registry/API/scoring. The experimental
module is isolated in `analog_pipelines.terrain_research`; tests are analytic
fixtures only and never become observed records. Promotion requires bounded buffered
reads across all bodies, >=80% supported statistics, reference-implementation
agreement, uncertainty/sensitivity evidence, a documented fixed comparison scale,
method versions, and a full recomputation of indexed data. Current production
terrain similarity has **five**, not seven, features.
