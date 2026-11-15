# Geomorpho90m 250 m source audit

Catalog: [`catalog/geomorpho250-sources.yaml`](../catalog/geomorpho250-sources.yaml).
Status: **source metadata verified from the live object; only bounded pilot windows were range-read, never the full 4.8 GB raster.**

## Exact product / access

| Field | Value |
|---|---|
| Product | Geomorpho90m (Amatulli et al.), 7.5 arc-second (~250 m), WGS84 |
| Global layers | Verified slope object: 5,022,223,729 bytes (~4.8 GB); 172800 x 71698 pixels |
| OpenTopography dataspace | `OTDS.012020.4326.1` |
| 250 m DOI | https://doi.org/10.1594/PANGAEA.899135 |
| 90 m OpenTopography DOI | https://doi.org/10.5069/G91R6NPX |
| Documentation | https://www.nature.com/articles/s41597-020-0479-6 |
| Licence | CC BY 4.0 |
| Coverage | ~60°S–85°N; Antarctica excluded (not "full Earth") |
| Data type | Int16; GeoTIFF `Scale=0.01`, `Offset=0`; nodata -32768; striped 1-row deflate blocks |
| Temporal | Static MERIT-DEM derivatives (v1.0, 2018) |
| Layer naming | Verified `dtm_slope_merit.dem_m_250m_s0..0cm_2018_v1.0.tif`; family documented |

The live object was verified by bounded HTTP range reads:

- URL: `https://hs.pangaea.de/Maps/DEM_Geomorpho90m/dtm_slope_merit.dem_m_250m_s0..0cm_2018_v1.0.tif`
- CRS EPSG:4326, pixel size 0.00208333°, Int16, scale 0.01, offset 0, nodata -32768.
- 172800 x 71698, striped (1 x 172800) deflate blocks; range reads of 240 x 240 windows
  succeeded, so a bounded pilot does not require the full file.
- The archive does not publish an ETag/checksum; SHA-256 of each local pilot window is in
  [`GEOMORPHO_REAL_PILOT.md`](GEOMORPHO_REAL_PILOT.md).

## Go / no-go

**Go** for:
- deterministic grid and feature code using synthetic fixtures;
- a bounded pilot after the maintainer explicitly authorizes a local staged file;
- separate coarse method/support and coarse feature IDs.

**No-go** for:
- treating 250 m-derived slope/relief as the existing 90 m `terrain-metrics-1.0.0`;
- claiming global coverage (Antarctica excluded, MERIT-DEM derivative);
- assuming every archive supports free remote range reads;
- copying the global 26-layer archive into Git or R2.

## Three-region pilot matrix

| Region | WGS84 bbox | Purpose |
|---|---|---|
| `sahara-tanezrouft` | −2,22 to 2,26 | low-relief / arid |
| `alps` | 8,45 to 12,48 | high-relief / steep |
| `grand-canyon` | −113,35 to −111,37 | canyon / edge-sensitive |

The real pilot used three smaller 0.4° windows around Sahara (10–10.4E), Alps
(10–10.4E) and Grand Canyon (-112.2–-111.8E), producing 168 real 5 km cells. See
[`GEOMORPHO_REAL_PILOT.md`](GEOMORPHO_REAL_PILOT.md). This is still not a global scan or a
scientific global result. The resumable batch runner over the same staged windows is
documented in [`GEOMORPHO_BATCH_PILOT.md`](GEOMORPHO_BATCH_PILOT.md).

## Pilot acquisition actually executed

1. Verified the exact PANGAEA object, licence, bytes, scale/offset, nodata and block layout.
2. Used `/vsicurl` range reads for three 240 x 240 padded windows; no full download.
3. Converted raw Int16 × 0.01 to float32 slope degrees and recorded SHA-256 per window.
4. Built the deterministic 5 km grid and coarse features, then an immutable Parquet index.
5. Verified DuckDB top-K and the FastAPI `/api/v1/global/*` contract on the real index.
6. Never promote coarse values to the 90 m score without refinement.

## Online smoke (opt-in)

`uv run python scripts/geomorpho_online_smoke.py` performs a 10x10 `/vsicurl` range read and prints
the verified CRS, dimensions, pixel size, dtype, nodata, block layout, scale tag and raw/slope
ranges. It never downloads the full global object and is not part of CI.
