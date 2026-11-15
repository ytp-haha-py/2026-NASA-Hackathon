# D05 NAIF SPICE solar-geometry pilot

Status: **model-derived context/evidence pilot, not an observation of surface
irradiance**. Issue #45 separates solar illumination from ionizing radiation; this
pilot addresses only the solar-geometry part for existing Moon and Mars reference
coordinates.

## What was computed

For each existing Moon/Mars reference point, the pilot computes:

- Sun **incidence angle** in degrees: angle between the local outward ellipsoid
  normal and the site-to-Sun vector.
- Sun **elevation angle** in degrees: `90 - incidence`; a negative value means the
  Sun is below the local flat tangent plane.
- Local solar time from the subsolar longitude, for provenance only.

The computation uses NASA/JPL NAIF SPICE with the pinned DE421 ephemeris and
body-fixed frames:

| Body | Frame | Frame ID | Validation |
|---|---|---:|---|
| Moon | `MOON_ME` | 31001 | pxform('MOON_ME', 'J2000') succeeded |
| Mars | `IAU_MARS` | 10014 | pxform('IAU_MARS', 'J2000') succeeded |

The documented epoch is `2026-11-15T12:00:00Z` (SPICE ET
`848016069.182739 s`). The pilot is a single instant, not a time series.

## Results

| Body | Area | Frame | Latitude (deg) | Longitude (deg) | Incidence (deg) | Elevation (deg) | Local solar time |
|---|---|---|---:|---:|---:|---:|---:|
| Moon | `moon-shackleton-rim-site04-roi1` | `MOON_ME` | -89.761966 | -157.067197 | 88.463164 | 1.536836 | 18.31 h |
| Moon | `moon-shackleton-rim-site04-roi2` | `MOON_ME` | -89.771925 | -156.502260 | 88.464607 | 1.535393 | 18.34 h |
| Moon | `moon-shackleton-rim-site04-roi3` | `MOON_ME` | -89.780083 | -156.327575 | 88.464542 | 1.535458 | 18.36 h |
| Moon | `moon-shackleton-rim-site04-roi4` | `MOON_ME` | -89.784874 | -155.959479 | 88.465472 | 1.534528 | 18.38 h |
| Mars | `mars-jezero-ctx-window-01` | `IAU_MARS` | 18.220253 | 77.370952 | 100.591418 | -10.591418 | 5.04 h |
| Mars | `mars-jezero-ctx-window-02` | `IAU_MARS` | 18.388959 | 77.548816 | 100.387748 | -10.387748 | 5.05 h |
| Mars | `mars-jezero-ctx-window-03` | `IAU_MARS` | 18.557665 | 77.726681 | 100.184311 | -10.184311 | 5.06 h |
| Mars | `mars-jezero-ctx-window-04` | `IAU_MARS` | 18.726371 | 77.904545 | 99.981111 | -9.981111 | 5.07 h |
| Mars | `mars-jezero-ctx-window-05` | `IAU_MARS` | 18.895077 | 78.082409 | 99.778150 | -9.778150 | 5.08 h |

Counts: **9/9 sites succeeded**
(**0 failed**, 0 no-coverage), **18
observations**, **9 areas**, by body `{'Mars': 10, 'Moon': 8}`, by
feature `{'solar_elevation_deg': 9, 'solar_incidence_deg': 9}`, by state `{'detected': 18}`. All observations
are `score_role = context_only` and `method_version = spice-solar-geometry-1.0.0`.

## Kernel provenance

Kernels are downloaded into the gitignored `var/cache/spice-45` cache and are
**never committed**. The builder verifies size and SHA-256 on every run.

| Kernel | SHA-256 | Bytes | Purpose |
|---|---|---:|---|
| [`naif0012.tls`](https://naif.jpl.nasa.gov/pub/naif/generic_kernels/lsk/naif0012.tls) | `678e32bdb5a744117a467cd9601cd6b373f0e9bc9bbde1371d5eee39600a039b` | 5257 | Leap-second kernel for UTC -> ephemeris time conversion. |
| [`pck00011.tpc`](https://naif.jpl.nasa.gov/pub/naif/generic_kernels/pck/pck00011.tpc) | `3dff7b1dbeceaa01f25467767d3fa25816051c85d162d1edf04acb310ee28bb1` | 131226 | Text planetary constants: body radii and IAU_MARS orientation. |
| [`de421.bsp`](https://naif.jpl.nasa.gov/pub/naif/generic_kernels/spk/planets/a_old_versions/de421.bsp) | `08b20db2ae22488650641c5a9033e5bfda4b1c4b440cfeaf20f621cfa18ecdb3` | 16790528 | JPL DE421 planetary ephemeris (1900-2050), Sun/Moon/Mars states. |
| [`moon_pa_de421_1900-2050.bpc`](https://naif.jpl.nasa.gov/pub/naif/generic_kernels/pck/moon_pa_de421_1900-2050.bpc) | `656f90616403d75a75f0cd6c8830fc5b44f8cb4facb5ccb8915e752b397520cf` | 1770496 | DE421 lunar principal-axis orientation (binary PCK). |
| [`moon_080317.tf`](https://naif.jpl.nasa.gov/pub/naif/generic_kernels/fk/satellites/moon_080317.tf) | `78732477b96f9863e7b0d65bcee3c22b8707ca5ed0db56d1173319cb2e8c7993` | 21437 | Lunar frame definitions: MOON_ME / MOON_PA realized from DE421. |

## Input manifests

| Manifest | SHA-256 | Reference count |
|---|---|---:|
| `pipelines/planetary/manifests/lola_moon_reference.json` | `80c91cb5992d395791e0835ce5952a99e51f52f6933643a414749ffdcb9eafcd` | 4 |
| `pipelines/planetary/manifests/ctx_mars_references.json` | `6168256e3a03502a48a0aa812ae00365337dc819cd6a161a9f4a75ee2b158153` | 5 |

## Exact commands

```bash
uv sync --extra spice
uv run --extra spice python scripts/build_spice_illumination_pilot.py --download
uv run --extra spice python scripts/build_spice_illumination_pilot.py
uv run --extra spice pytest pipelines/tests/test_solar_geometry.py -q
uv run --extra spice pytest apps/api/tests/test_typed_api.py -q -k d05_spice
uv run ruff check .
uv run ruff format --check .
```

The first builder invocation fetches only the five pinned public kernels. The
second is offline and re-verifies the cache. When `spiceypy` is unavailable, the
builder exits with install instructions; the committed pilot remains readable and
the pure-geometry tests do not require SPICE.

## What this is not

- No observed solar irradiance or radiance.
- No shadow fraction, shadow mask, terrain horizon, or horizon visibility interval.
- No ionizing radiation, absorbed dose (Gy), or equivalent dose (Sv).
- No atmospheric/cloud correction, no observer height, no DEM horizon raster.
- No global or exhaustive Moon/Mars coverage; only the existing reference points.
- No cross-body or Earth/Moon/Mars D05 similarity score.

## Remaining scientific limits / blockers

- Model-derived geometry only: not observed irradiance, radiance, shadow fraction, or ionizing radiation.
- Flat biaxial-ellipsoid tangent-plane normal; no DEM terrain horizon, horizon mask, observer height, or atmospheric/cloud model.
- The site is placed on the reference biaxial ellipsoid; source DEM elevations and observer heights are not added.
- Sun position is the light-time-corrected body-centre vector from SPICE; site parallax is applied by vector subtraction.
- Point sites at existing reference coordinates; not a spatial average over the existing 1.98 km DEM windows.
- Moon uses the MOON_ME body-fixed frame realized from DE421; Mars uses IAU_MARS from pck00011. Source projected-CRS coordinates are interpreted in those frames; prime-meridian/frame registration is a documented assumption, not an independent geodetic validation.
- One documented instant only; no time series, eclipse/occultation, or local-solar-time series is produced.
- DE421 covers approximately 1900-2050; kernel SHA-256 digests are pinned and any drift is rejected by the builder.
- No cross-body D05 score; illumination and ionizing radiation remain separate subgroups. No Gy/Sv value is produced.
- Kernels are cached under a gitignored var/cache directory and are not committed to Git.

## Follow-up work

1. Replace the flat tangent plane with a documented DEM horizon/shadow model before
   claiming any shadow fraction or visibility interval.
2. Validate the projected source-CRS-to-body-fixed-frame registration independently
   (especially the lunar prime-meridian and Mars 2000/IAU_MARS assumptions).
3. Add a bounded time series only when the underlying time support and local solar
   time are meaningful; do not infer illumination from dark pixels.
4. Keep ionizing-radiation detectors and dose definitions in a separate D05
   subgroup; no flux/dose-to-Sv conversion without physics.
