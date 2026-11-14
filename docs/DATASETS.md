# Datasets and source verification

This is the human-readable companion to `catalog/datasets.yaml`. Existing detailed
provenance notes remain in `docs/DATA_SOURCES.md`; this document adds the v6 verification
matrix and status rules.

## Status rules

| Status | Meaning |
|---|---|
| `verified` | A validated manifest exists in this repository and its provenance fields were copied from that manifest or an official source. |
| `in_progress` | Pipeline work exists, but no validated manifest is committed at this revision. |
| `not_verified` | The source is identified, but access or validation is incomplete. |
| `planned` | Interface-only. No pipeline, manifest, or measurement exists. |

"Verified" describes the **manifest/provenance check**, not scientific universality. Five
1.98 km Earth windows are not a global survey. A Moon ROI is not the whole Moon. Planned
sources contribute no numbers to the index.

## Summary matrix

| Dataset | Body / role | Status | Native resolution | Product / version | Licence |
|---|---|---|---|---|---|
| Copernicus DEM GLO-90 | Earth terrain | **verified** | 90 m (3 arc-second) | `COP-DEM_GLO-90-DGED`, 2021 release | Copernicus DEM terms; free basis for the general public via the AWS Open Data mirror |
| ESA WorldCover 2021 v200 | Earth land-cover context | **verified** | 10 m (1/12000°) | `ESA WorldCover 2021 v200` | CC BY 4.0 |
| NASA LRO-LOLA Site04 5 m | Moon reference | **verified** | 5 m source; 90 m analysis | `Site04_final_adj_5mpp_surf` | CC0-1.0 per NASA Science Data Portal; PGDA requests citation |
| Mars MOLA MEGDR | Mars reference | **planned** | not verified | not verified | not verified |

At this revision there is no dataset in the `in_progress` state: the WorldCover context
pipeline and a validated manifest now exist. This is a snapshot of repository state, not
a promise that the manifests remain unchanged.

## Earth — Copernicus DEM GLO-90 (verified)

| Field | Value |
|---|---|
| Provider | European Space Agency (ESA) / Airbus Defence and Space; AWS Open Data mirror |
| Product | `COP-DEM_GLO-90-DGED`, Copernicus DEM 2021 release, GLO-90 |
| Official URLs | <https://registry.opendata.aws/copernicus-dem/> · <https://dataspace.copernicus.eu/explore-data/data-collections/copernicus-contributing-missions/collections-description/COP-DEM> |
| Coverage | Global land; ocean areas have no tiles. This repository indexes five 1.98 km windows. |
| Native grid | EPSG:4326, 3 arc-second (~90 m); EGM2008 heights in metres |
| Format | Cloud Optimized GeoTIFF, 2048×2048 internal blocks |
| Access | Public unsigned HTTPS COGs at `s3://copernicus-dem-90m` (eu-central-1); bounded range reads |
| Licence | "Copernicus DEM GLO-90: free basis for the general public (AWS Open Data registry), under the Copernicus Contributing Mission Data Access Licence terms" |
| Redistribution | Derived numeric values committed with attribution; raw tiles/crops not committed |
| Manifest | `pipelines/earth/manifests/copernicus_glo90_earth.json` |

Required attribution: "Produced using Copernicus WorldDEM-90 © DLR e.V. 2010–2014 and
© Airbus Defence and Space GmbH 2014–2018 provided under COPERNICUS by the European Union
and ESA; all rights reserved."

Limitations:

- Digital **surface** model: buildings, infrastructure, and vegetation are included.
- The source metadata declares no nodata value, so interior voids cannot be distinguished
  from valid zero/negative heights by metadata alone.
- Each window is a local sample at 90 m with a 1.98 km extent. It does not characterize
  the whole named region.

Five indexed windows: Atacama Desert, Death Valley, Lanzarote, Devon Island/Haughton, and
Rio Tinto. Per-window bounds, CRS, checksums, quality flags, and metric values are in the
manifest and `docs/DATA_SOURCES.md`.

## Earth — ESA WorldCover 2021 v200 (verified context)

| Field | Value |
|---|---|
| Provider | European Space Agency (ESA) WorldCover |
| Product | ESA WorldCover 10 m 2021 v200 |
| Official URL | <https://esa-worldcover.org/en/data-access> |
| DOI / citation | <https://doi.org/10.5281/zenodo.7254221> |
| Coverage | Global land, static annual 2021 map; the repository samples the same five Earth AOIs |
| Native grid | EPSG:4326, 10 m (1/12000°) |
| Access | Public unsigned HTTPS range requests from `s3://esa-worldcover` (eu-central-1), `v200/2021/map/` |
| Licence | CC BY 4.0 |
| Manifest | `pipelines/worldcover/manifests/worldcover_earth_context.json` |

The manifest stores 11 class fractions per AOI at native 10 m, with class code 0 excluded
from denominators. Valid fractions must be at least 0.8 for `verified` quality.

Required citation: Zanaga, D., Van De Kerchove, R., Daems, D., De Keersmaecker, W.,
Brockmann, C., Kirches, G., Wevers, J., Cartus, O., Santoro, M., Fritz, S., Lesiv, M.,
Herold, M., Tsendbazar, N.E., Xu, P., Ramoino, F., Arino, O., 2022. ESA WorldCover 10 m
2021 v200. <https://doi.org/10.5281/zenodo.7254221>.

Limitations:

- Context only: **not** mineralogy, geology, a terrain measurement, or a similarity
  feature.
- Static 2021 epoch; annual composite without a confirmed per-pixel date.
- Cloud/seasonal/mixed-pixel artifacts; helper classes group several surfaces (for
  example "bare / sparse vegetation").
- The published independent validation reports **76.7% overall accuracy** for 2021 v200.
- A 2 km AOI can be dominated by one class and is not a regional survey.

## Moon — NASA LRO-LOLA Site04 5 m (verified reference)

| Field | Value |
|---|---|
| Provider | NASA GSFC Planetary Geodesy Data Archive (PGDA) / LRO-LOLA |
| Product | `Site04_final_adj_5mpp_surf`, 5 m/pix south-pole surface-interpolated DEM |
| Official URLs | <https://pgda.gsfc.nasa.gov/products/78> · <https://pgda.gsfc.nasa.gov/data/LOLA_5mpp/Site04/Site04_final_adj_5mpp_surf.tif> |
| Coverage | Lunar south pole; one 1.98 km ROI (Site04 ROI 2), not the whole product or Moon |
| Native grid | 3200×3200 px, 5 m; south polar stereographic, metres; MOON_ME/DE421 per product documentation |
| Access | Public HTTPS; bounded HTTP range requests |
| Licence | CC0-1.0 per NASA Science Data Portal; the PGDA page requests citation |
| Manifest | `pipelines/planetary/manifests/lola_moon_reference.json` |

Citation: Barker, M.K., et al. (2021), Improved LOLA Elevation Maps for South Pole Landing
Sites: Error Estimates and Their Impact on Illumination Conditions, *Planetary & Space
Science* 203, 105119, <https://doi.org/10.1016/j.pss.2020.105119>.

Limitations:

- About 90% of 5 m pixels are interpolated; only about 10% are direct returns. Do not
  treat 5 m texture as an independent measurement.
- Median RMS Z error is about 0.30–0.50 m; median RMS slope error about 1.5–2.5°.
- The manifest's dataset checksum covers the bounded cached window, not the full
  40,980,806-byte product.
- The 90 m analysis window is a local reference sample.

## Mars — PDS MOLA MEGDR (planned, not verified)

| Field | Value |
|---|---|
| Provider | NASA PDS Geosciences Node / MGS MOLA |
| Product | MOLA gridded topography (MEGDR); exact product/version not selected |
| Official URL | <https://pds-geosciences.wustl.edu/missions/mgs/megdr.html> |
| Status | `planned` |
| Measurements | none |

No Mars pipeline, manifest, feature, or preset exists. Before any future ingestion, an
Issue/PR must verify and record the product version, CRS, grid resolution, vertical
datum, licence, access method, and a bounded ROI. Mars must remain absent from
`supported_bodies` and UI claims until a real reference manifest validates.

## Synthetic fixtures

Synthetic test/demo data is repository-authored under CC0-1.0, is marked
`data_kind=synthetic`, and is never presented as an observation. It does not appear in
the dataset matrix above.

## Cross-links

- Source details and access notes: `docs/DATA_SOURCES.md`.
- Storage, snapshot, and index rebuild: `docs/DATA_ARCHITECTURE.md`.
- End-to-end commands: `docs/DATA_PIPELINE.md`.
- Contribution and review rules: `docs/CONTRIBUTING_DATA.md`.
- Machine-readable registry: `catalog/datasets.yaml`.
