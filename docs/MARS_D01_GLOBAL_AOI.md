# Mars D01 global MOLA MEGDR AOI (Goal B)

Status: **implemented** on `feat/170-b-mars-d01`. Mars arbitrary valid-bbox AOI can be
read from NASA PDS MGS MOLA MEGDR (PDS4 bundle, bounded PDS3/GDAL reads) and compute the
D01 five terrain features over the full requested bbox. This does **not** treat Jezero
CTX as all-Mars.

## Owned deliverables

- `pipelines/planetary/mola_global.py` — Goal B API/CLI surface (owned): tile/catalog
  reader helpers, `analyze_mars_bbox_d01(...)`, `--analyze-bbox` CLI; delegates bounded
  full-bbox AOI analysis to `pipelines/planetary/mola_aoi.py` shared raster/D01
  infrastructure (`analog_pipelines.features` Horn/terrain metrics,
  `analog_pipelines.planetary_coarse.read_metric_window`).
- `pipelines/planetary/tests/test_mola_global_aoi.py` — offline tests (owned): API,
  low/mid/high-lat tile selection, latitude/nodata/oversized/invalid/disconnect cases,
  full D01 feature computation, and verification of real 3-AOI evidence.
- `docs/MARS_D01_GLOBAL_AOI.md` — this document (owned).
- Evidence artifact (real runs): `pipelines/planetary/evidence/mars_mola_aoi_evidence.json`

## Source (verified, bounded)

- **Bundle:** `urn:nasa:pds:mgs_mola_topography_derived` v1.0, DOI `10.17189/1z1b-kv84`
  (PDS4), collection `data_meg128` v2.0 for 128 ppd tiles.
- **Product:** MOLA MEGDR 128 pixels/degree topography tiles (`megt*hb`, e.g.
  `megt44n000hb`, `megt88n000hb`, `megt88n270hb`), SignedMSB2, metre, nodata -32768,
  scale 1 / offset 0.
- **CRS/frame:** Mars body-fixed, planetocentric, east-positive, simple cylindrical /
  projected metric read via GDAL; analysis in local Mars azimuthal-equidistant metric CRS
  (R=3396.0 km). **Never Earth WGS84.**
- **Native resolution:** 463.0576672 m/px (128 ppd). MEGDR is a global median-binned grid
  with interpolated values where no MOLA shot falls in a bin (not raw stereo DTM).
- **Coverage:** simple-cylindrical MEG128 tiles 88°S–88°N, 0–360°E (polar caps beyond 88°
  rejected as `unsupported_latitude`).
- **Access:** bounded GDAL `/vsicurl/` HTTP range reads of PDS label/image windows only
  (no global tile/download); read upper bounds and source windows recorded per AOI.
  License not stated in PDS4 bundle/collection/product labels (recorded as-is).

## Implementation notes
- **Source:** MOLA MEGDR 128 ppd tiles (PDS4 `urn:nasa:pds:mgs_mola_topography_derived` v1.0 / data_meg128 v2.0, DOI `10.17189/1z1b-kv84`), 463.0576672 m native, planetocentric Mars (R=3396 km), nodata -32768, metre. Bounded per-window `/vsicurl/` reads only (no global download); read windows/byte upper bounds recorded.
- **Chunked/bounded reads & cache:** reads are bounded to intersecting tile windows (`source_windows` with shape/offsets/read-byte upper bounds per AOI). `mola_global.analyze_mars_bbox_d01(..., cache_dir=...)` stores a rebuildable, checksumed, versioned local AOI result cache (`AOI_CACHE_VERSION`, key by bbox/method/source/resolution/label, `cache_result_sha256`, meta sidecar, hit/miss reported). Cache is gitignored and rebuildable by deletion+rerun. Low-level chunked tile window reads can be staged to local GeoTIFF via `mola_global.read_window(..., --output)` with sidecar metadata.

## D01 computation (full requested bbox)

- Five D01 features: `slope_mean_deg`, `slope_p90_deg`, `terrain_relief_p95_p05_m`,
  `roughness_std_m`, `flat_terrain_fraction`.
- Horn 3×3 slope / relief / detrended roughness / flat-fraction operators evaluated over
  the **full requested bbox** on a 463.0576672 m analysis grid (never finer than native
  N-S support; conservative at high latitude). **Not comparable to 90 m
  `terrain-metrics-1.0.0`** and not for cross-body scoring (explicit in
  `method_version`, `comparability`, QA/warnings).
- Nodata/missing pixels remain missing (no zero-fill). `valid_fraction`, valid/invalid
  pixel counts, elevation stats, checksum, source version/IDs/URLs/windows, CRS and QA
  reported per result.

## Reproducible commands

Run from repo root (network required for remote PDS reads):

```bash
# CLI (Goal B owned module)
PYTHONPATH=pipelines/planetary:pipelines/src uv run python pipelines/planetary/mola_global.py \
  --analyze-bbox 77.35 18.35 77.55 18.53 --allow-remote --label "jezero-low"

# Direct API
PYTHONPATH=pipelines/planetary:pipelines/src uv run python - <<'PY'
import json
from mola_global import analyze_mars_bbox_d01
r = analyze_mars_bbox_d01({"west": 77.35, "south": 18.35, "east": 77.55, "north": 18.53}, allow_remote=True)
print(json.dumps(r, indent=2))
PY
```

Evidence regeneration (bounded, 3 AOIs): `PYTHONPATH=pipelines/planetary:pipelines/src uv run python pipelines/planetary/mola_aoi.py --evidence` (writes `pipelines/planetary/evidence/mars_mola_aoi_evidence.json`).


## Rebuildable/checksumed/versioned local cache (item 2 evidence)

`mola_global.analyze_mars_bbox_d01(..., cache_dir=...)` persists AOI results as a
versioned, checksumed, rebuildable local cache (gitignored path). Demonstration (real
bounded read, Jezero AOI):

```bash
# miss (reads bounded MOLA window, computes D01, stores cache)
PYTHONPATH=pipelines/planetary:pipelines/src uv run python pipelines/planetary/mola_global.py \
  --analyze-bbox 77.35 18.35 77.55 18.53 --allow-remote --cache-dir /tmp/mola_cache_demo --label cache-demo
# -> cache.cache_hit=false, cache.cache_version="mola-aoi-cache-1.0.0",
#    cache.cache_key="10a949191d7c203c0e4b561d",
#    cache.cache_result_sha256="sha256:1af3dca76744745ad2fe043d2854a41160e11916cda10d6b0b34e568470a976d",
#    cache.cache_path=".../10/10a949191d7c203c0e4b561d.json", rebuildable=true

# hit (no recompute; checksum-verified)
PYTHONPATH=pipelines/planetary:pipelines/src uv run python pipelines/planetary/mola_global.py \
  --analyze-bbox 77.35 18.35 77.55 18.53 --allow-remote --cache-dir /tmp/mola_cache_demo --label cache-demo
# -> cache.cache_hit=true (same key/version/sha256)

# rebuildable: delete cache and rerun -> miss/recomputed
rm -rf /tmp/mola_cache_demo
```

Stored artifacts: `<key>.json` (result payload) + `<key>.meta.json` (cache_version,
cache_key, created_at, result_sha256, result checksum, method/source/version, bbox,
rebuildable). Chunked/bounded source reads remain recorded in `source_windows`
(shape/offsets/read-byte upper bounds).

## 3 dispersed real Mars AOIs (low/mid/high latitude) — observed evidence

Source of record: `pipelines/planetary/evidence/mars_mola_aoi_evidence.json`
(`mars-mola-aoi-evidence-1.0.0`, generated 2026-11-15T12:50:19Z, PDS4
`urn:nasa:pds:mgs_mola_topography_derived` (1.0) / data_meg128 (2.0), method
`mars-mola-aoi-horn-463m-1.0.0`). All bounded `/vsicurl/` reads, `status: complete`,
`valid_fraction: 1.0`.

| AOI / latitude band | Requested bbox (W,S,E,N) | Tile(s) | Elevation mean / min..max (m), relief p95-p05 (m) | slope_mean / slope_p90 (deg) | relief (m) | roughness_std (m) | flat_fraction | checksum (result) |
|---|---|---|---|---:|---:|---:|---:|---|
| Jezero west low-latitude plain (**low**, 18.44°N) | 77.35, 18.35, 77.55, 18.53 | `megt44n000hb` (289–840 src px windows recorded) | -2541.181 / -2657.467..-2280.778, 239.410 | 1.574841 / 2.894722 | 239.409966 | 32.384649 | 0.970356 | `sha256:ff90be0b07d5a52fd3ee36e8eff546b23bd05938b3bf32a90d4d6322cb0770fd` |
| Hellas Planitia mid-latitude floor (**mid**, -42.4°S) | 70.35, -42.5, 70.55, -42.3 | `megt00n000hb` | -7432.803 / -7493.125..-7315.013, 116.855 | 1.146951 / 2.357733 | 116.854924 | 19.759685 | 0.995652 | (see evidence JSON `aois[1].checksum`) |
| Olympia Undae high-latitude dune field (**high**, 80.0°N) | 239.55, 79.92, 240.45, 80.08 | `megt88n180hb` | -4753.077 / -4852.120..-4719.133, 81.997 | 0.756747 / 1.959551 | 81.996637 | 15.584824 | 1.0 | (see evidence JSON `aois[2].checksum`) |

Each result includes per-feature `method_version` (`mars-mola-aoi-horn-463m-1.0.0`,
registry versions recorded), `extraction_scale_m` 463.0576672, `valid_fraction`, source
IDs/URLs/windows/read-byte upper bounds, CRS (source + local aeqd analysis), QA flags and
warnings (bounded read, not global, interpolated MEGDR bins, not 90 m comparable, no
zero-fill).

## Coverage Matrix fragment

| Dimension | Status | Native res. | Analysis res./support | CRS / frame | QA flags | Uncertainty / notes |
|---|---|---:|---:|---|---|---|
| D01 terrain (slope mean/p90, relief p95-p05, roughness std, flat fraction) from MOLA MEGDR 128 ppd | **measured/derived** per bounded AOI (observed MEGDR elevations) | 463.0576672 m (128 ppd) | 463.0576672 m square grid, full bbox | Mars planetocentric (IAU2000/body-fixed), R=3396 km; local aeqd metric analysis CRS | `bounded_vsicurl_window`, `full_requested_bbox`, `analysis_463m_square_grid`, `body_fixed_local_aeqd`, `native_support_463m`, `interpolated_bins_where_no_shot`, `not_90m_d01_comparable`, `no_zero_fill`, partial/no_data when applicable | Median-binned/interpolated MEGDR (not raw altimetry); nodata excluded, no zero-fill; not 90 m D01 comparable / not cross-body scoring; simple-cylindrical 88°S–88°N only; bounded per-AOI, not exhaustive global survey |

## Edge cases and tests

- **Latitudes:** low/mid/high covered (real evidence) and tile selection validated; >|88°|
  -> `unsupported_latitude`.
- **Nodata:** no-data -> `status: no_data`, features null (no zero-fill); partial nodata
  -> reported `valid_fraction` with features over valid cells.
- **Source disconnect/failure:** explicit `source_read_failed` (never fabricated).
- **Oversized AOI:** rejected (`aoi_too_large`) before computation/invalid results.
- **Invalid/antimeridian handling:** bbox validated; unsupported/crossing cases rejected
  explicitly (no silent split/fabrication).
- Offline tests: `uv run pytest pipelines/planetary/tests/test_mola_global_aoi.py pipelines/planetary/tests/test_mola_aoi_bbox.py -q`
