# Earth land-cover context — ESA WorldCover 2021 v200

> **Earth land-cover context only.**
> These numbers are **not lunar mineralogy**, **not geology**, **not a terrain
> measurement**, and **not a similarity feature**. They never enter the weighted
> distance. They exist to annotate or filter the indexed Earth candidates (for
> example “show only AOIs with a bare/sparse fraction above 0.5”). Nothing here
> says anything about the Moon or Mars.

This pipeline reads one small AOI window per indexed Earth candidate from the public
ESA WorldCover 2021 v200 raster, computes all eleven class fractions at the native
10 m grid, and writes them as `context_observations` on the candidate records whose
`location_id` and `bbox` are copied unchanged from
`pipelines/earth/manifests/copernicus_glo90_earth.json`.

Committed artifact: `pipelines/worldcover/manifests/worldcover_earth_context.json`
(one `landcover` dataset, five Earth candidates, eleven context observations each).

## Source verified for this run (2026-11-14)

| Item | Verified value |
| --- | --- |
| Product page | https://esa-worldcover.org/en/data-access (HTTP 200) |
| Public bucket | `s3://esa-worldcover`, region `eu-central-1` |
| Unsigned HTTPS base | https://esa-worldcover.s3.eu-central-1.amazonaws.com/ |
| Object prefix | `v200/2021/map/` |
| Tile pattern | `ESA_WorldCover_10m_2021_v200_<N\|S>dd<W\|E>ddd_Map.tif` |
| Tile grid | 3 × 3 degrees, named by the **south-west** corner |
| CRS / grid | `EPSG:4326`, 1/12000 degree pixels (~10 m at the equator), `uint8` |
| Nodata | `0` (the only non-class value; class 0 is not a land-cover class) |
| Product id | ESA WorldCover 2021 v200 |
| DOI | https://doi.org/10.5281/zenodo.7254221 |
| Access timestamp | `2026-11-14T06:20:08Z` (latest AOI read) |
| Processing version | `worldcover-2021-v200-1.0.0` |

Every tile key below was confirmed with an HTTP `HEAD` returning `200`, and each
window was then read with GDAL `/vsicurl` range requests. No full tile was
downloaded or stored.

| Candidate | Tile id | Object bytes | ETag | Last-Modified |
| --- | --- | ---: | --- | --- |
| earth-atacama-desert | `S24W069` | 57,288,645 | `8acfd449400530bbe6adcacce58b1826-7` | Wed, 26 Oct 2022 12:48:28 GMT |
| earth-death-valley | `N36W120` | 115,482,414 | `71495d1ab2ce752b50fb08dbdf36f360-14` | Wed, 26 Oct 2022 12:54:11 GMT |
| earth-lanzarote | `N27W015` | 5,583,741 | `5f896631e0135e84dedbb4c878213b0d` | Wed, 26 Oct 2022 12:40:08 GMT |
| earth-devon-island | `N75W090` | 31,004,601 | `486b089cb2cf72a0b66c160cf9eabe0e-4` | Wed, 26 Oct 2022 13:03:04 GMT |
| earth-rio-tinto | `N36W009` | 88,848,530 | `8b1139e2040cb9f4c9b8fe1a315e2255-11` | Wed, 26 Oct 2022 12:45:48 GMT |

Example resolved URL:

```text
https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/ESA_WorldCover_10m_2021_v200_N36W120_Map.tif
```

The five HEAD `Content-Length` values sum to **298,207,931 bytes**. That is the
upper bound for the objects covered by the range reads, not bytes written to disk.
WorldCover COGs use 1024 × 1024 internal blocks, so one small window can transfer a
compressed block. Only the derived class windows are cached locally
(`data/cache/worldcover/`, gitignored; 60 KB total for all five).

## License and attribution (verified, not inferred)

The product page states, verbatim:

> The ESA WorldCover product is provided free of charge, without restriction of use.
> For the full license information see the Creative Commons Attribution 4.0
> International License.

License: **CC BY 4.0** — https://creativecommons.org/licenses/by/4.0/

Required attribution text for a published map, verbatim from the product page:

> © ESA WorldCover project 2021 / Contains modified Copernicus Sentinel data (2021)
> processed by ESA WorldCover consortium

Citation requested by the product page:

> Zanaga, D., Van De Kerchove, R., Daems, D., De Keersmaecker, W., Brockmann, C.,
> Kirches, G., Wevers, J., Cartus, O., Santoro, M., Fritz, S., Lesiv, M., Herold, M.,
> Tsendbazar, N.E., Xu, P., Ramoino, F., Arino, O., 2022. ESA WorldCover 10 m 2021
> v200. https://doi.org/10.5281/zenodo.7254221

## Class mapping

The feature ids and class codes are the ones registered in
`apps/api/src/analog_api/domain/features.py`; this pipeline imports
`WORLDCOVER_CLASSES` instead of redefining them.

| Code | Feature id | WorldCover label |
| ---: | --- | --- |
| 10 | `landcover_tree_cover_fraction` | Tree cover |
| 20 | `landcover_shrubland_fraction` | Shrubland |
| 30 | `landcover_grassland_fraction` | Grassland |
| 40 | `landcover_cropland_fraction` | Cropland |
| 50 | `landcover_built_up_fraction` | Built-up |
| 60 | `landcover_bare_sparse_fraction` | Bare / sparse vegetation |
| 70 | `landcover_snow_ice_fraction` | Snow and ice |
| 80 | `landcover_water_fraction` | Permanent water bodies |
| 90 | `landcover_herbaceous_wetland_fraction` | Herbaceous wetland |
| 95 | `landcover_mangrove_fraction` | Mangroves |
| 100 | `landcover_moss_lichen_fraction` | Moss and lichen |

Code 0 is nodata and is never reported as a class.

## Processing

1. Load the five candidate bboxes from the committed Earth terrain manifest. The
   bbox, centre, display name, CRS, window size and terrain features are copied
   verbatim into this manifest; the lead's loader merges `context_observations` by
   `location_id`.
2. Resolve the 3-degree WorldCover tile(s) covering each bbox. A bbox may span up to
   four tiles; the reader aggregates disjoint tile windows on the global
   `1/12000`-degree pixel grid.
3. Read only the pixel envelope of the AOI. Pixels are counted when their **centre**
   lies inside the bbox, so the AOI is the Earth window rather than a padded tile
   slice.
4. Treat value `0` as nodata. `valid_fraction = valid pixels / AOI pixels`;
   `valid_fraction ≥ 0.8` is required for `quality="verified"`, otherwise the record
   is marked `suspect`. Any non-nodata value outside the eleven classes is reported
   as unknown and also downgrades quality; it is never mapped onto a class.
5. `fraction(class) = pixels(class) / valid pixels`. Fractions are rounded to six
   decimals and must sum to 1 within `1e-4`.
6. Each AOI gets a SHA-256 `window_checksum` over the class window bytes, the tile
   URLs and the bbox. The dataset `checksum` is a composite SHA-256 over the sorted
   per-AOI window checksums; it is **not** a source-object checksum.
7. All five AOIs resolved to a single 3-degree tile. The cached windows are
   216 × 234 (Atacama), 216 × 266 (Death Valley), 218 × 248 (Lanzarote),
   224 × 885 (Devon Island) and 220 × 278 (Rio Tinto) `uint8` pixels.

## Measured context (2026-11-14 run)

Fractions of valid WorldCover pixels; every candidate had `valid_fraction = 1.0`.

| Candidate | tree | shrub | grass | crop | built | bare/sparse | snow | water | wetland | mangrove | moss/lichen |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| earth-atacama-desert | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0051 | **0.9949** | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| earth-death-valley | 0.2861 | 0.4474 | 0.2394 | 0.0000 | 0.0003 | **0.0267** | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| earth-lanzarote | 0.0034 | 0.0012 | 0.6329 | 0.0042 | 0.0869 | **0.2609** | 0.0000 | 0.0106 | 0.0000 | 0.0000 | 0.0000 |
| earth-devon-island | 0.0000 | 0.0000 | 0.0002 | 0.0000 | 0.0000 | **0.5096** | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.4902 |
| earth-rio-tinto | 0.1926 | 0.0986 | 0.3031 | 0.0040 | 0.2216 | **0.1768** | 0.0000 | 0.0033 | 0.0000 | 0.0000 | 0.0000 |

These are the product's labels for a ~2 km window, not a description of the whole
region. For example, the Death Valley window includes sloping terrain mapped as
tree/shrub/grass by WorldCover; that is a property of the 2021 product at this
location, not a claim that the park is vegetated. The Atacama window is almost
entirely `bare / sparse vegetation`, and Devon Island splits between bare/sparse and
moss/lichen. `bare / sparse vegetation` is a single WorldCover class that groups
several surfaces and is not a mineralogy indicator.

Per-AOI window checksums:

| Candidate | `window_checksum` |
| --- | --- |
| earth-atacama-desert | `sha256:cd02d32468e7bacd727286d255effdd852b6b5ade4c61d0297b4ac1f90134c1b` |
| earth-death-valley | `sha256:ccdb9e91b6d1c1380e03c366a2a20b246eeff5feabb938a5975d57e2dfb26dc7` |
| earth-lanzarote | `sha256:cf2a3498b46fa685be92629138fbd02d387c0082e8fc0f723318b3bea0c527ba` |
| earth-devon-island | `sha256:32a4d5e98a6825a3b5bd5ac1a7fa87baee0bd1aefd0e6147627b48f674a4953c` |
| earth-rio-tinto | `sha256:63860505f28436bacf71ff2b35b0af6165032cd70e0c7d70abdb700a1845509d` |

## Commands

Run from the repository root:

```bash
# Resolve AOIs, tiles and URLs. No network, no writes.
uv run python pipelines/worldcover/ingest_worldcover.py --dry-run

# Real ingestion of all five AOIs (reuses data/cache/worldcover when valid).
uv run python pipelines/worldcover/ingest_worldcover.py

# Re-read one AOI, ignoring its cache.
uv run python pipelines/worldcover/ingest_worldcover.py --only death-valley --force

# Offline test suite (no network).
uv run pytest pipelines/worldcover/tests -q

# Validate the committed manifest against both contracts.
uv run python - <<'PY'
import json, jsonschema
from pathlib import Path
from analog_api.domain.manifest import Manifest
m = json.loads(Path("pipelines/worldcover/manifests/worldcover_earth_context.json").read_text())
jsonschema.Draft202012Validator(
    json.loads(Path("contracts/pipeline-manifest.schema.json").read_text())
).validate(m)
Manifest.model_validate(m)
print("manifest OK")
PY
```

Other options: `--only atacama-desert,rio-tinto`, `--cache-dir PATH`,
`--earth-manifest PATH`, `--manifest PATH`, `--timeout 30` (seconds per attempt),
`--max-retries 2` (capped at 2 by the network policy). A failed AOI is printed and
omitted; if every AOI fails no manifest is written. Exit codes: `0` all selected
AOIs succeeded, `2` some failed but a manifest was written, `1` nothing succeeded.

Re-seed the API with both manifests (Earth first, then context):

```bash
ANALOG_SEED_INCLUDE_SYNTHETIC=false uv run python -m analog_api.cli seed
```

`analog_api.config.Settings` already includes
`pipelines/worldcover/manifests` as `manifest_dir_context`, and
`manifest_paths()` loads the Earth directory before the context directory, so the
context rows attach to the existing Earth candidates instead of replacing them.

## Validation observed

- `uv run pytest pipelines/worldcover/tests -q` → **33 passed** (offline; synthetic
  arrays only).
- `uv run ruff check pipelines/worldcover` → all checks passed.
- JSON Schema `Draft202012Validator` → 0 errors; `Manifest.model_validate` → OK,
  1 dataset, 5 candidates, 11 context observations each.
- Seed against a temporary SQLite database, observed output:

```text
Seeded 3 manifest(s). Index: 5 candidate(s), 3 dataset(s), 1 reference(s). data_version=dv-3c7193084b11
```

`data_version` is derived from the manifests and changes whenever `generated_at`
changes; the candidate/dataset/context counts are the stable part.

The other two manifests are the existing Earth terrain and planetary reference
manifests; they are unrelated to this pipeline. Database inspection after seeding
showed:

```text
dataset esa-worldcover-2021-v200  source_kind=landcover  native_resolution_m=10.0
earth-atacama-desert   dataset_id=copernicus-dem-glo90  11 context rows
earth-death-valley     dataset_id=copernicus-dem-glo90  11 context rows
earth-lanzarote        dataset_id=copernicus-dem-glo90  11 context rows
earth-devon-island     dataset_id=copernicus-dem-glo90  11 context rows
earth-rio-tinto        dataset_id=copernicus-dem-glo90  11 context rows
55 context rows, 11 distinct context feature ids
```

The terrain candidates keep their Copernicus dataset; only context rows are added.

## Failure notes

The final run had **5/5 successful AOIs and 0 failures**, so no context record was
omitted. There was no tile 404, CRS mismatch, unknown class code, or nodata failure.
All five AOIs had `valid_fraction = 1.0`, so `quality="verified"` everywhere. No
synthetic or interpolated value was substituted at any point.

An initial probe fetched a tile path without a bounded window and was interrupted
after ~55 MB; the importer itself never does that. All real reads are per-window
range requests, and only the derived class windows are cached.

## Limitations

- **Earth context only.** WorldCover is a land-cover product. It is not lunar
  mineralogy, not geology, not a spectral measurement, and not a terrain metric. Do
  not rank planetary analogs with it.
- **Static 2021 epoch.** One annual map, no per-pixel acquisition date. Seasonal
  flooding, fire scars, crops and snow cover are represented according to the 2021
  composite, not the current state.
- **10 m resolution.** Small structures, narrow roads and sub-pixel mixtures are
  generalised. The product's independent validation reports 76.7% overall accuracy
  for 2021 v200; individual classes and small AOIs can be less accurate.
- **Class semantics.** `bare / sparse vegetation` groups bare soil, rock, sand and
  sparse vegetation. `grassland` in arid regions can include sparse herbaceous
  cover. The labels are not mineral classes.
- **AOI size.** Each window is ~2 km across (the terrain analysis window), which is
  far smaller than the 3-degree product tile. Results describe these specific
  windows only and are not a regional or global survey.
- **Copied terrain fields.** Candidate records repeat the Earth terrain fields
  (including its four `features`) verbatim so the existing loader can accept the
  document and merge context by `location_id`. Those terrain values were not
  recomputed here and are not WorldCover measurements; the pipeline never changes
  them.
- **Byte accounting.** HEAD `Content-Length` is an upper bound on source bytes
  covered by the reads. Block-level COG access can transfer more than the decoded
  window; no full tile is saved.
