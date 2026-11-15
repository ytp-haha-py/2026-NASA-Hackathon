# Six-dimension source inventory

Machine-readable source of truth: [`catalog/dimension-source-inventory.yaml`](../catalog/dimension-source-inventory.yaml).
Validator: `analog_pipelines.inventory.load_inventory()`.

This is an audit of **candidate and verified sources**, not a claim of coverage. The
only verified observations in the repository are D01 terrain (113 Earth, 18 Moon,
5 Mars windows) and Earth WorldCover context. D02–D06 are candidate/planned only.

## Coverage matrix (18 body × dimension entries)

| Dimension | Earth | Moon | Mars |
|---|---|---|---|
| **D01 Terrain & Landforms** | Verified: Copernicus GLO-90 + WorldCover context | Verified: LOLA 5 m/six sites (18 refs) | Verified: Jezero CTX (5 refs) |
| **D02 Geology & Mineralogy** | Identified candidates: GLiM, EMIT (not fetched) | Identified: USGS unified map, M³ (not fetched) | Identified: Jezero/Nili map, CRISM (not fetched) |
| **D03 Thermal Environment** | Identified: MOD11A2 (not fetched) | Identified: Diviner (not fetched) | Planned: no product ID asserted |
| **D04 Water & Ice** | Planned: no product selected | Planned: no product selected | Planned: no product selected |
| **D05 Illumination & Radiation** | Context: NASA POWER (10 timeseries, model/reanalysis) | Model geometry: NAIF SPICE (4 sites, 8 observations) | Model geometry: NAIF SPICE (5 sites, 10 observations) |
| **D06 Regolith & Surface Properties** | Planned: sparse evidence only | Planned: sparse evidence only | Planned: sparse evidence only |

## Status semantics

- `verified`: validated manifest/observations exist in Git.
- `accessible`: official access path/licence checked but no observation committed.
- `identified`: source/product identified, version/access not fully verified.
- `planned`: research/interface only; product may be `not_selected`.
- `unknown`: not determined.
- `not_applicable`: not scientifically applicable.

`data_status`: `existing_observed`, `existing_modeled`, `not_fetched`, or
`unknown`. `existing_modeled` means a committed model-derived value (for example
NAIF SPICE solar geometry); it is not an observation of the surface.
`comparability`: `comparable`, `context_only`, `evidence_only`, `not_comparable`, or
`unknown`.

## What remains unknown

Every candidate entry lists its `unknown_fields`. For D03–D06 no dataset ID or
coverage is invented; planned entries use `not_selected` until a maintainer verifies
an authoritative product, licence, access method and scientific semantics.

## Baseline counts

| Item | Count | Source |
|---|---:|---|
| Earth terrain windows | 113 | `pipelines/earth/manifests/` |
| Moon reference windows | 18 | `pipelines/planetary/manifests/lola_*.json` |
| Mars reference windows | 5 | `pipelines/planetary/manifests/ctx_mars_references.json` |
| Earth WorldCover context observations | 1,243 | `pipelines/worldcover/manifests/` |
| D05 Moon/Mars model-derived solar-geometry observations | 18 | `pipelines/pilots/d05_spice_illumination/` |
| D02–D06 observed values | **0** | not implemented / not downloaded |

The D05 SPICE values are model-derived geometry (Sun incidence/elevation), not
observed irradiance, shadow fraction or ionizing radiation. No new source raster
was downloaded for this inventory; the SPICE kernels live in a gitignored cache.
