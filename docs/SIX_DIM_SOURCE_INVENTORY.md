# Six-dimension source inventory

Machine-readable source of truth: [`catalog/dimension-source-inventory.yaml`](../catalog/dimension-source-inventory.yaml).
Validator: `analog_pipelines.inventory.load_inventory()`.

The machine-readable catalog records the pre-ingest planning inventory. This page
additionally records the bounded D06 Earth SoilGrids pilot (five AOIs) without
changing that planning file or claiming global coverage.

This is an audit of **candidate and verified sources**, not a claim of coverage. The
only verified terrain observations in the repository are D01 terrain (113 Earth, 18 Moon,
5 Mars windows) and Earth WorldCover context. D02–D05 remain candidate/planned or
model-derived. D06 has Earth SoilGrids 2.0 model predictions for five AOIs; Moon and
Mars regolith remain `no_coverage`.

## Coverage matrix (18 body × dimension entries)

| Dimension | Earth | Moon | Mars |
|---|---|---|---|
| **D01 Terrain & Landforms** | Verified: Copernicus GLO-90 + WorldCover context | Verified: LOLA 5 m/six sites (18 refs) | Verified: Jezero CTX (5 refs) |
| **D02 Geology & Mineralogy** | Identified candidates: GLiM, EMIT (not fetched) | Identified: USGS unified map, M³ (not fetched) | Identified: Jezero/Nili map, CRISM (not fetched) |
| **D03 Thermal Environment** | Identified: MOD11A2 (not fetched) | Identified: Diviner (not fetched) | Planned: no product ID asserted |
| **D04 Water & Ice** | Planned: no product selected | Planned: no product selected | Planned: no product selected |
| **D05 Illumination & Radiation** | Context: NASA POWER (10 timeseries, model/reanalysis) | Model geometry: NAIF SPICE (4 sites, 8 observations) | Model geometry: NAIF SPICE (5 sites, 10 observations) |
| **D06 Regolith & Surface Properties** | Model predictions: SoilGrids 2.0, 5 AOIs × 5 properties × 4 depths (300 evidence-only observations) | No coverage: no registered public sample/PDS measurement | No coverage: no registered public sample/PDS measurement |

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
coverage is invented; entries without a verified product use `not_selected`. D06 Earth
has a verified SoilGrids 2.0 model-prediction source; D06 Moon/Mars remain
`no_coverage` until a real sample/PDS measurement is registered.

## Baseline counts

| Item | Count | Source |
|---|---:|---|
| Earth terrain windows | 113 | `pipelines/earth/manifests/` |
| Moon reference windows | 18 | `pipelines/planetary/manifests/lola_*.json` |
| Mars reference windows | 5 | `pipelines/planetary/manifests/ctx_mars_references.json` |
| Earth WorldCover context observations | 1,243 | `pipelines/worldcover/manifests/` |
| D05 Moon/Mars model-derived solar-geometry observations | 18 | `pipelines/pilots/d05_spice_illumination/` |
| D06 Earth model-prediction observations (4 depths) | 300 | `pipelines/pilots/d06_earth_soilgrids_depths/` |
| D02–D06 measured in-situ/laboratory observations | **0** | not implemented / not downloaded |

The D05 SPICE values are model-derived geometry (Sun incidence/elevation), not
observed irradiance, shadow fraction or ionizing radiation. No new source raster
was downloaded for this inventory; the SPICE kernels live in a gitignored cache.
