# D05 AOI/time illumination + ionizing-radiation evidence (#170)

Bounded, model-derived, offline-reproducible pilot for **one Moon** and **one Mars**
AOI. Illumination geometry and ionizing radiation are separate D05 subgroups and are
never combined into one value.

## Actual content

| Item | Value |
|---|---|
| Method | `d05-aoi-illumination-1.0.0` |
| Radiation audit | `d05-aoi-ionizing-radiation-audit-1.0.0` |
| Window | `2026-03-01T00:00:00Z` .. `2026-03-02T00:00:00Z` (60 min cadence) |
| AOIs | 2 |
| Observations | 102 (illumination 100, radiation evidence 2) |
| Bodies | {'Mars': 51, 'Moon': 51} |
| States | {'detected': 100, 'no_coverage': 2} |
| Score roles | {'context_only': 100, 'evidence_only': 2} |

| Body | AOI | Illuminated sample fraction | Horizon crossings | Radiation |
|---|---|---|---:|---:|---|
| Moon | `moon-shackleton-rim-site04-roi1` | 0.0000 | 0 | `unsupported` |
| Mars | `mars-jezero-ctx-window-01` | 0.4800 | 2 | `no_coverage` |

## What the values are

- Sun incidence/elevation angles at explicit UTC instants, from pinned NAIF SPICE
  kernels and documented body-fixed frames.
- A **geometric flat-ellipsoid horizon**: Sun above/below the local tangent plane,
  with bracketed and bisection-refined zero crossings.
- **Not** observed irradiance, **not** a shadow fraction, **not** a terrain horizon,
  and **not** ionizing radiation.

## Ionizing radiation

The audit found real, accessible official products (MSL RAD RDR/EDR, LRO CRaTER
CDR/EDR/DDR). None is an AOI-attributable surface absorbed-dose product for these
requests, so each AOI gets an explicit missing-value record:

- Moon: `unsupported` — CRaTER products are orbital detector energy/LET, not surface
  absorbed dose.
- Mars: `no_coverage` — MSL RAD is in situ at Gale Crater (~3,752 km away), not at the
  Jezero AOI.

No dose/flux value is ingested, converted, interpolated or zero-filled.

## Files

- `requests.jsonl` — explicit AOI/time requests (illumination and radiation).
- `observations.jsonl` — typed-v2 observations (numeric angles + `no_coverage` evidence).
- `horizon.json` — geometric horizon runs and refined crossings per AOI.
- `radiation_sources.json` — official-source audit, probes, blockers and decisions.
- `plan.json` — acquisition-plan envelope; `assets.json` — kernels + audited metadata.
- `manifest.json` — hashes, counts, frames, kernels, limitations and non-claims.
- `REPORT.md` — compact per-sample results.

## Reproduce

```bash
uv sync --extra spice
uv run --extra spice python scripts/build_d05_aoi_illumination.py --download
uv run --extra spice python scripts/build_d05_aoi_illumination.py
uv run --extra spice python scripts/build_d05_aoi_illumination.py --probe-radiation --download
```

Full detail: [`docs/D05_AOI_170.md`](../../docs/D05_AOI_170.md).
