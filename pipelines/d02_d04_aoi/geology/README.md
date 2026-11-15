# D02 AOI evidence (Issue #170 Goal E)

Bounded, evidence-only D02 discovery for one Moon AOI and two Mars test AOIs.
This directory does **not** rewrite `pipelines/geology/typed/`, does not add a
similarity score, and does not claim a mineral detection or abundance.

## Contents

| Path | Purpose |
|---|---|
| `build.py` | `discover` (network, bounded ODE queries + PDS labels) and `build` (offline evidence export). |
| `aoi.json` | Canonical copy of the three AOI geometries, source hashes and ODE query bboxes. |
| `queries/` | Exact ODE request URLs, counts, response hashes and selected product metadata. Raw raster bytes: 0. |
| `queries/labels/` | Small PDS3/PDS4 product labels used to verify product id, time, orbit and pixel scale. |
| `products.json` | Nine official product records with id, version, license, footprint, time, depth and resolution. |
| `evidence.json` | Full evidence document: datasets, products, AOI summaries, records, limitations. |
| `evidence.jsonl` | One record per line for portable ingestion. |
| `manifest.json` | Counts and SHA-256 for every committed file. |

## States

- `unknown` — an official footprint covers the AOI, but no spectral value,
  mineral identification or valid-data mask was read. `value`, `unit` and
  `valid_fraction` are `null`.
- `no_coverage` — the official query returned no product, or the AOI does not
  intersect the product footprint. Fractions and values are `null`; this is not
  zero concentration and not evidence of absence.

No record is `detected`. Footprint coverage is **not** a mineral observation.

## Reproduce

Network discovery (ODE metadata + small PDS labels only):

```bash
uv run --frozen --with shapely python pipelines/d02_d04_aoi/geology/build.py discover
```

Offline rebuild from committed query responses and labels:

```bash
uv run --frozen --with shapely python pipelines/d02_d04_aoi/geology/build.py build
uv run --frozen --with shapely python pipelines/d02_d04_aoi/geology/build.py check
```

Focused tests:

```bash
uv run --frozen --with shapely pytest pipelines/d02_d04_aoi/tests/test_d02_aoi_geology.py \
  -o addopts='' -q
```

`shapely` is only needed for the equal-area footprint/AOI intersection and the
spatial test. It is the same pin already used by
`pipelines/geology/requirements-ingest.txt`; it is not added to the shared
`pyproject.toml`.

## Boundaries

- Concurrency is 1; no raster or global data product is downloaded.
- The Moon M3 product set is a bounded four-record subset of the candidates
  returned by one ODE query; it is not an M3 survey.
- The Mars AOIs are explicitly marked `test_aoi`, not production references.
- Existing D02 typed bundles are read for context and are not modified.
