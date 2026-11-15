# D05 Moon/Mars — NAIF SPICE solar-geometry pilot

This directory is a portable, checksummed, **model-derived** pilot. It is not an
observation of surface irradiance, a shadow-fraction product, or a radiation dose.

## Actual content

| Item | Value |
|---|---:|
| Sites attempted | 9 |
| Sites succeeded | 9 |
| Sites failed | 0 |
| Areas | 9 |
| Observations | 18 |
| Bodies | Mars (10), Moon (8) |
| Features | `solar_elevation_deg` (9), `solar_incidence_deg` (9) |
| States | `detected` (18) |
| Score role | `context_only` (18) |

## Scientific limits

- The values are SPICE solar incidence/elevation angles at one documented instant
  (`2026-11-15T12:00:00Z`). They are **not** observed irradiance.
- A flat biaxial-ellipsoid tangent plane is used. There is no DEM terrain horizon,
  horizon mask, observer-height model, shadow fraction, or atmosphere/cloud model.
- Moon uses `MOON_ME` from DE421; Mars uses `IAU_MARS` from `pck00011`. Source
  projected-CRS coordinates are interpreted in those frames; registration is a
  documented assumption, not an independent geodetic validation.
- The pilot is bounded to the existing Moon/Mars reference coordinates. It is not
  global coverage and does not change existing terrain observations or scores.
- Illumination and ionizing radiation remain separate. No `Gy`/`Sv` value is
  produced.

## Files

- `plan.json` — area/task envelope for the existing reference sites.
- `observations.jsonl` — typed-v2 solar-geometry observations.
- `assets.json` — public kernel URLs and SHA-256 provenance; kernels are **not**
  committed and live under the gitignored `var/cache/spice-45` cache.
- `manifest.json` — epoch, frames, kernel checksums, counts and limitations.
- `REPORT.md` — compact site results.

## Reproduce

```bash
uv sync --extra spice
uv run --extra spice python scripts/build_spice_illumination_pilot.py --download
uv run --extra spice python scripts/build_spice_illumination_pilot.py
```

The first command downloads only the five pinned public SPICE kernels (about
18 MB total). The second run is offline and verifies the pinned digests. Full
details and computed results are in `docs/D05_SPICE_PILOT.md`.
