# Shared raster source adapter (Goal C, Issue #170)

Status: implemented and verified offline by subagent C1. Scope is the **source adapter
and bounded window read only**. The disk cache, versioning and resume layer is C2
(`raster_cache.py`); terrain/feature extraction belongs to Goals A/B. This module does
not download global datasets, does not write a cache, and does not compute terrain
features.

Module: `pipelines/src/analog_pipelines/raster_source.py`
Schema version: `raster-source-1.0.0`

## Why this exists

Goals A/B/D need one honest way to open a raster, read a small window, and record where
the bytes and metadata came from. Before this module each pipeline repeated its own
`rasterio.open` + masking + scale/offset code with different remote-read and provenance
rules. The adapter centralises those rules without prescribing an extraction method.

## Public interface

```python
from analog_pipelines.raster_source import (
    RasterAccessKind,
    RasterSource,
    RasterSourceError,
    RemoteReadDisabledError,
    RasterWindowError,
    UnsupportedSourceError,
    open_raster_source,
    inspect_source,
    read_source_window,
)
```

### `RasterSource`

A frozen value object: URI plus the provenance facts supplied by the caller.

| Field | Meaning |
| --- | --- |
| `uri` | Local path, HTTPS URL, or `/vsicurl/https://...` path |
| `kind` | Optional explicit `RasterAccessKind`; inferred from `uri` |
| `dataset_id` | Verified dataset identity, when known |
| `version` | Product/processing revision, when known |
| `license`, `license_url` | Usage terms, when known |
| `declared_checksum` | Published `sha256:<hex>` (or bare 64-hex) digest |
| `allow_remote` | Explicit opt-in for HTTPS range reads; default `False` |
| `max_pixels` | Per-source bounded-window cap; default `1_000_000` |

`RasterAccessKind` values:

| Kind | URI shape | Read path |
| --- | --- | --- |
| `LOCAL_FILE` | filesystem path | `rasterio.open(path)` |
| `STAGED_TILE` | local path explicitly declared staged | same local read, provenance marked staged |
| `HTTP_RANGE` | `https://...` | GDAL `/vsicurl/` HTTPS range reads |
| `VSICURL` | `/vsicurl/https://...` | explicit GDAL virtual path |

Plain `http://`, `s3://`, `gs://`, `ftp://` and `file://` are rejected. HTTP range and
VSI paths are the same GDAL mechanism; the two kinds preserve how the caller addressed
the source. Neither downloads a whole raster: GDAL requests the header and the requested
window. **No remote read happens unless `allow_remote=True`.**

### Reading

```python
with open_raster_source(source) as reader:
    meta = reader.metadata  # RasterSourceMetadata
    window = reader.read_window(bbox={"west": ..., "south": ..., "east": ..., "north": ...})
    # or: reader.read_window(window=(col_off, row_off, width, height))
    # or: reader.read_window(window=Window(...), pad_pixels=2)
```

One-shot helpers: `inspect_source(source)`, `read_source_window(source, ...)`.

`RasterReader.read_window` key behaviour:

- exactly one of `window` / `bbox` is required; both is an error;
- `bbox` is in `bbox_crs` (default `EPSG:4326`) and is transformed to the source CRS;
  the result stays in the **source CRS**, never an assumed Earth frame;
- `pad_pixels` expands the bbox envelope by native pixels for resampling guard bands;
- the pixel cap is checked **before** reading, after clipping;
- edge-crossing windows are clipped and flagged (`clipped=True`), or rejected with
  `clip=False`;
- one band per read (`band=1` default), float64 output with the band's scale/offset
  applied;
- nodata/outside pixels are `NaN` in `data` and `False` in `valid_mask`; zero is a valid
  observed value and is never confused with nodata;
- `read_bytes_upper_bound` is a conservative byte estimate (touched blocks + 64 KiB
  header allowance), not a measurement.

### Metadata

`RasterSourceMetadata` records observed facts and declared provenance:

`crs_wkt`, `crs_name`, `crs_epsg`, `is_geographic`, `resolution`, `resolution_unit`,
`resolution_m` (projected metre CRS only), `width`, `height`, `count`, `dtype`, `nodata`,
`bounds`, `block_shape`, `tiled`, `size_bytes`, `scale`, `offset`, plus `uri`,
`reference`, `access_kind`, `dataset_id`, `version`, `license`, `license_url`,
`checksum`, `checksum_verified`, `provenance_complete`.

Rules:

- A missing version/licence/checksum is reported as `None`; nothing is inferred.
- `checksum` is the declared value unless `verify_checksum=True` was requested for a
  **local** source. Remote checksums cannot be computed without a full download.
- `verify_checksum=True` streams SHA-256 over the local file, compares it with the
  declared digest, and raises `checksum_mismatch` on drift.
- `provenance_complete` requires version, licence and checksum.

`to_payload()` returns a JSON-safe dict for manifests/evidence.

## Errors

All errors derive from `RasterSourceError` and expose `.code`, `.message`, `.details`.

| Class | Codes (examples) | Meaning |
| --- | --- | --- |
| `UnsupportedSourceError` | `unsupported_scheme`, `insecure_remote`, `source_kind_mismatch`, `unsupported_checksum` | Source cannot be represented honestly |
| `RemoteReadDisabledError` | `remote_read_disabled` | HTTPS read attempted without opt-in |
| `RasterWindowError` | `ambiguous_window`, `missing_window`, `invalid_window`, `invalid_bbox`, `invalid_bbox_crs`, `window_outside_source`, `window_clipped`, `window_too_large`, `invalid_band` | Window request is invalid/unbounded |
| `RasterSourceError` | `source_not_found`, `source_not_open`, `open_failed`, `read_failed`, `checksum_mismatch`, `checksum_unavailable_remote`, `checksum_verification_requires_local` | Open/read/provenance failure |

No error path returns empty or zero-filled data.

## Verified behaviour

`uv run pytest pipelines/tests/test_raster_source.py -q` → **20 passed**.

The tests write a synthetic tiled GeoTIFF to `tmp_path` (32x16, EPSG:4326, scale 2.0,
offset 100.0, nodata -9999) and verify:

- metadata CRS/resolution/unit/nodata/dtype/scale/offset/bounds/block shape/size;
- declared provenance and the explicit "missing stays missing" behaviour;
- local SHA-256 verification and mismatch rejection;
- scale/offset application, valid-zero preservation, nodata → NaN/mask;
- bbox → source-window conversion and source-CRS bounds;
- clipping vs `clip=False` rejection and outside-source rejection;
- pixel-cap enforcement before read (source-wide and per-call);
- ambiguous/missing windows, band and bbox/CRS validation;
- multiple windows per open reader and the conservative byte bound;
- reader-before-open errors;
- staged-tile provenance;
- HTTPS opt-in, `/vsicurl/` recognition, insecure HTTP and unsupported-scheme rejection;
- kind/URI mismatch rejection;
- remote checksum refusal (no download).

No test uses the network. The fixture is labelled
`synthetic_raster_source_fixture_not_an_observation` and is never a manifest input.

Commands:

```bash
uv run pytest pipelines/tests/test_raster_source.py -q
uv run ruff check pipelines/src/analog_pipelines/raster_source.py pipelines/tests/test_raster_source.py
uv run ruff format --check pipelines/src/analog_pipelines/raster_source.py pipelines/tests/test_raster_source.py
```

## Integration notes for Goals A/B/D

```python
source = RasterSource(
    "https://example.org/cog.tif",
    dataset_id="verified-product-id",
    version="2026.1",
    license="CC BY 4.0",
    declared_checksum="sha256:...",
    allow_remote=True,
)
with open_raster_source(source) as raster:
    meta = raster.metadata
    window = raster.read_window(bbox=aoi, bbox_crs="EPSG:4326", pad_pixels=2)
# window.data / window.valid_mask / window.transform / window.bounds
```

- Planetary callers pass `extra_gdal_env={"PROJ_IGNORE_CELESTIAL_BODY": "YES"}` to
  `open_raster_source`; the adapter never assumes WGS84 for a non-Earth frame.
- C2 can stage a bounded tile, then pass its local path with `kind=STAGED_TILE`; the
  metadata will record staged provenance rather than pretending it is the product.
- C2 owns cache keys/versions; a natural key input is
  `source.reference()`, `metadata.version`, `metadata.checksum`, the window tuple and band.
- A/B own resampling and terrain math. This adapter returns source-CRS data and leaves
  the analysis grid to the caller.

## Limitations and non-goals

- No cache, staging, eviction, locking or resume (C2).
- No direct application-level HTTP range staging; `HTTP_RANGE` is served by GDAL
  `/vsicurl`. `HttpRangeSource`-style chunk staging belongs to C2.
- No reprojection/resampling; the caller decides the analysis grid.
- No global or multi-tile mosaic; one source per reader. A/B/D compose tiles explicitly.
- No checksum computation for remote sources; require a published declared checksum.
- No support for `s3://`, `gs://`, `ftp://`, `file://` or plain HTTP.
