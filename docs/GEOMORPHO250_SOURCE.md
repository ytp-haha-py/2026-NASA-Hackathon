# Geomorpho90m 250 m source audit

Catalog: [`catalog/geomorpho250-sources.yaml`](../catalog/geomorpho250-sources.yaml).
Status: **documentation verified; no raster downloaded or staged.**

## Exact product / access

| Field | Value |
|---|---|
| Product | Geomorpho90m (Amatulli et al.), 7.5 arc-second (~250 m), WGS84 |
| Global layers | 26 GeoTIFFs; per-file documented size range 100 MB–6 GB |
| OpenTopography dataspace | `OTDS.012020.4326.1` |
| 250 m DOI | https://doi.org/10.1594/PANGAEA.899135 |
| 90 m OpenTopography DOI | https://doi.org/10.5069/G91R6NPX |
| Documentation | https://www.nature.com/articles/s41597-020-0479-6 |
| Licence | CC BY 4.0 |
| Coverage | ~60°S–85°N; Antarctica excluded (not "full Earth") |
| Data type | Int16/UInt16 with scale factor in GeoTIFF metadata |
| Temporal | Static MERIT-DEM derivatives (v1.0, 2018) |
| Layer naming | Documented `dtm_*_merit.dem_m_250m_s0..0cm_2018_v1.0.tif` family |

The exact object ETag/checksum, pixel scale/offset, nodata, block layout and remote
range-read support are **not verified** because no asset was fetched. They must be
inspected before ingestion.

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

The pilot plan is metadata only until a source file is authorized. It is not a
global scan and not a scientific result.

## Future acquisition (not executed)

1. Confirm exact PANGAEA/OpenTopography object, licence, bytes, checksum, scale/offset.
2. Check available disk; stage under `ANALOG_RAW_DIR` only if explicitly authorized.
3. Run the local reader metadata check and a tiny bounded read.
4. Record scale/offset/nodata/CRS/resolution/bytes/checksum in the source asset.
5. Build the deterministic 5 km grid and coarse features; validate masks.
6. Never promote coarse values to the 90 m score without refinement.
