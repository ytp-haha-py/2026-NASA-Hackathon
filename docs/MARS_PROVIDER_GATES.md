# Mars provider metadata and comparability gates

Status: **metadata interfaces implemented; no Mars MOLA terrain observation exists.**
No global Mars raster was downloaded and the terrain index is unchanged. The separate Issue #43
D03 pilot now persists bounded THEMIS thermal-inertia evidence (see below); the acquisition
provider in this document still fails closed and emits no task. No Mars numeric value is
fabricated, zero-filled, or scored.

Code:

- `pipelines/src/analog_pipelines/acquisition/providers_mars.py`
- `pipelines/src/analog_pipelines/acquisition/mars_comparability.py`
- tests: `pipelines/tests/test_mars_providers.py`
- opt-in metadata test: `pipelines/planetary/tests/test_mars_metadata_online.py`

## MOLA MEGDR (terrain, D01)

Official source: NASA PDS Geosciences Node, **MGS MOLA Topography Derived** PDS4 bundle.

| Field | Verified value |
|---|---|
| Bundle logical ID / version | `urn:nasa:pds:mgs_mola_topography_derived` / `1.0` |
| Data collection version | `urn:nasa:pds:mgs_mola_topography_derived:data_meg016` / `2.0` |
| Representative product | `urn:nasa:pds:mgs_mola_topography_derived:data_meg016:megt90n000eb` / `1.1` |
| PDS3 lineage | `MGS-M-MOLA-5-MEGDR-L3-V1.0`, `PRODUCT_VERSION_ID = 2.0` |
| DOI | `10.17189/1z1b-kv84` |
| Product bytes / array | 33,177,600 bytes; 2880 lines x 5760 samples; `SignedMSB2`; metre |
| Projection / frame | Equirectangular, planetocentric, positive east, sphere R = 3396.0 km |
| Native resolution | 3.7044613374 km/pixel at 16 ppd; 0.4630576672 km/pixel at 128 ppd |
| Vertical reference | planetary radius minus the GMM3 (mgm1025) areoid |
| Licence | **not stated in the PDS4 bundle label**; recorded as `not_verified` |

The public page and PDS4 labels were inspected on 2026-11-15. A HEAD request confirmed the
MEG016 image is 33,177,600 bytes and a MEG128 tile is 129,761,280 bytes. The bytes are **not**
committed and were **not** processed.

### Gate outcome

`assess_mola_megdr_terrain(...)` returns `not_comparable` with blockers:

- native resolution is coarser than the 90 m `terrain-metrics-1.0.0` analysis support
  (16 ppd is ~41x coarser; 128 ppd is ~5.1x coarser);
- an analysis grid at 90 m would upsample the native grid;
- MEGDR is a global median topography grid with interpolated bins where no MOLA shot exists,
  not a local 1.98 km DEM;
- no validated Mars projected metric analysis CRS for the window;
- vertical/areoid and per-window uncertainty were not validated;
- no validated Mars reference manifest exists.

Consequently:

- `discover()` returns one metadata record with `comparability="not_comparable"`;
- `inspect()` returns `status="blocked"` with `metadata_verified=true` and those blockers;
- `plan()` returns no tasks;
- `acquire()` and `process()` raise `AcquisitionError`;
- `validate()` rejects any detected Mars D01 value and any `similarity` score role.

This is deliberately honest: a MEGDR-derived 90 m window would be an interpolated product, not
an observed 90 m terrain measurement.

## THEMIS thermal inertia (D03)

Official source: USGS Astrogeology Science Center, **Mars THEMIS-Derived Global Thermal
Inertia Mosaic**, quantitative 32-bit product, edition 1 (2014-05-30).

| Field | Verified value |
|---|---|
| Product page | <https://astrogeology.usgs.gov/search/map/mars-themis-derived-global-thermal-inertia-mosaic> |
| Representative tile | `THEMIS_TI_Mosaic_Quant_30N000E_100mpp.cub` |
| Native resolution | 100 m/pixel |
| Tiling / coverage | 24 tiles of 30 x 60 degrees; 60°S to 60°N |
| Format / tile size | ISIS3 `.cub`; 2,532,510,198 bytes for the representative tile |
| Access / use constraints | public domain; please cite the authors |
| Accuracy | absolute ~20%; relative within one image ~10%; no per-pixel uncertainty layer |
| Citation / DOI | Fergason et al. (2006), J. Geophys. Res. 111, E12004, `10.1029/2006JE002735` |

### Gate outcome

`assess_themis_thermal_inertia(...)` returns `evidence_only` with blockers:

- no approved D03 thermal-inertia **similarity** feature ID exists; Issue #43 adds only the
  evidence-only `thermal_inertia` feature;
- no per-pixel uncertainty layer and no verified per-pixel QA layer;
- coverage stops at 60° latitude;
- each full tile is ~2.36 GB in ISIS3 `.cub` format.

The acquisition provider still returns no tasks and fails closed, so it never invents a surface
temperature, thermal inertia, or `diurnal_temperature_range_k`.

### Bounded Issue #43 pilot (separate from this provider)

`scripts/build_planetary_thermal_pilot.py` reads only the 128 x 128 ISIS tiles touched by the
existing five Jezero CTX windows from `THEMIS_TI_Mosaic_Quant_00N060E_100mpp.cub` (tile 0-30 N,
60-120 E). The verified label gives `StartByte = 65537`, `Format = Tile`, `TileSamples = 128`,
`TileLines = 128`, `Samples = 35566`, `Lines = 17783`, float32 LSB, 100 m/pixel. HTTP range
requests read ~0.3-1.3 MB per run, never the 2.53 GB tile.

The pilot persists five `thermal_inertia` observations at native 100 m over 1.98 km windows,
with the Fergason et al. (2006) retrieval, `J m^-2 K^-1 s^-1/2`, a published ±20% absolute
uncertainty range, and no claim of per-pixel QA. Thermal inertia remains separate from surface
or brightness temperature and is never scored across bodies. See
`pipelines/pilots/d03_planetary_thermal/REPORT.md`.

## Tests

Offline:

```bash
uv run pytest pipelines/tests/test_mars_providers.py -q
```

The offline tests use only in-memory metadata and assert that published PDS4 values match the
provider constants, that no task is planned, that acquisition fails, and that negative
validation rejects fabricated values.

Opt-in online metadata-only check (no image download):

```bash
ANALOG_RUN_NETWORK_TESTS=1 uv run pytest pipelines/planetary/tests/test_mars_metadata_online.py -q -m network
```

It fetches only the small PDS4 label/bundle and USGS product page and checks that the asserted
identifiers/resolution/licence text still appear. It does not fetch a raster or write a manifest.

## What remains external

- A scientifically defensible Mars terrain reference needs a real local Mars DEM whose native
  support is compatible with the chosen method (for example a validated 20 m CTX/HiRISE DTM
  window), not an upsampled global MEGDR grid.
- MOLA redistribution terms should be confirmed by the maintainer before any derived value is
  committed.
- The evidence-only `thermal_inertia` feature now exists, but a similarity score still requires
  per-pixel QA, a validated cross-body retrieval comparison and maintainer approval. None exists.
- An AOI/ingest authorization for a multi-GB ISIS3 tile is an operator decision, not something
  this repository can infer.

`GET /references` still contains only the existing observed Moon and Mars CTX windows; MOLA is
not added because no validated manifest exists. The Issue #43 thermal pilot adds no terrain
reference and no similarity feature.
