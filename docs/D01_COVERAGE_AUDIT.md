# D01 Moon/Mars terrain source audit and bounded coverage plan

Status: **source/access audit complete; no new terrain observation created.**
Audit date: **2026-11-15** (Asia/Taipei).
Scope: D01 terrain sources for **Moon** and **Mars** only. Earth D01 is out of scope.
Machine-readable catalog: [`docs/data-sources/d01-terrain.json`](data-sources/d01-terrain.json).
Rerunnable checker: [`scripts/check_d01_sources.py`](../scripts/check_d01_sources.py).

> **Non-claims.** This document does not claim global D01 coverage for the Moon or Mars.
> It does not turn item counts into area coverage, does not upsample coarse global DEMs
> into the 90 m index, and does not treat a successful HTTP response as scientific
> comparability. The 18 Moon and 5 Mars fine reference windows that already exist are
> unchanged.

## 1. What already exists in the repository

| Layer | Current state | Evidence |
| --- | ---: | --- |
| Earth fine D01 candidates | 113 windows | Copernicus GLO-90 manifests under `pipelines/earth/manifests/` |
| Moon fine D01 references | 18 windows | Six LOLA 5 m site DEMs: Site01 (7), Site04 (4), Site07 (1), Site11 (4), Site20 (1), Site23 (1) |
| Mars fine D01 references | 5 windows | `pipelines/planetary/manifests/ctx_mars_references.json` (Jezero CTX DEM, 20 m) |
| Planetary coarse pilot | 10 cells | Moon LOLA128, Mars CTX and Mars MOLA cells; all `evidence_only` or `not_comparable` (`docs/PLANETARY_COARSE_REFERENCE.md`) |
| Cross-body ranking | disabled | `docs/PLANETARY_COARSE_REFERENCE.md`; no eligible same-method metric |

The existing `features.py` contract is fixed: a **1.98 km × 1.98 km** window, **22 × 22 cells
at 90 m**, bilinear resampling, **≥80% valid cells**, Horn 3×3 slope, p95−p05 relief,
plane-detrended roughness, and flat fraction below 5°. Planetary metrics must be computed
in a **projected body-fixed metric CRS**, never Earth WGS84 and never from longitude/latitude
degrees.

The existing Moon and Mars provider coverage strings remain correct:

```
Moon: verified_only_for_processed_reference_rois
Mars: verified_only_for_processed_reference_rois
```

## 2. How the audit was verified

All evidence is from live, anonymous, read-only HTTP requests made on 2026-11-15. No raw
DEM was downloaded. The checks were:

- **Metadata/API responses**: status, exact `Content-Type`, `Content-Length`, body token.
- **Raster availability**: HTTP `Range: bytes=0-1023`, checking `206 Partial Content` and
  the `Content-Range` total size. The checker reads at most 1 KiB per raster probe.
- **GeoTIFF/BigTIFF tags**: dimensions, pixel scale, GeoKeys/CRS, sample format and NoData
  were read from the first 256 KiB of each verified GeoTIFF. This is header parsing, not a
  raster download.
- **PDS3 labels**: `MAP_SCALE`, `MAP_RESOLUTION`, latitude/longitude bounds, units, scaling
  factor, offset and projection were read from the official labels.
- **STAC**: `/collections` and `/collections/{id}/items` with bbox filters; `numberMatched`
  was recorded. Item counts are inventory counts, not area coverage.
- **Trek ImageServer**: `?f=json` metadata; a **1×1** `exportImage` smoke test was executed
  once per service (Moon 66,352-byte TIFF; Mars 33,584-byte TIFF). The rerunnable checker
  does not call `exportImage`, so it never downloads even a 1-pixel DEM.

The reproducible checker is:

```bash
python3 scripts/check_d01_sources.py
```

It exits non-zero if a previously verified source changes status, content type, required
body token, `numberMatched`, or falls below its recorded byte floor. It also fails if a
required catalog field is missing.

## 3. Verified source inventory

`native/90 m` is the source pixel size divided by 90 m. Values below 1 are finer than the
analysis grid; values above 1 are coarser and cannot produce independent 90 m metrics.

| Source ID | Body | Native | native/90 m | Coverage actually verified | D01 role | Licence |
| --- | --- | ---: | ---: | --- | --- | --- |
| `usgs-pds-lro-lola-sldem2015-512ppd` | Moon | 59.23 m | 0.66 | 32 tiles, 60S–60N | candidate fine | public domain (PDS/USGS); cite LOLA+Kaguya |
| `nasa-pgda-lola-ldem128-pa` | Moon | 236.90 m | 2.63 | global 128 ppd | coarse context only | not stated; citation requested |
| `nasa-pgda-lola-south-pole-sites-5m` | Moon | 5 m | 0.06 | 6 local sites | existing reference | not stated; citation requested |
| `nasa-pgda-lola-south-pole-mosaic-5m` | Moon | 5 m | 0.06 | 87–90S, 200×200 km | candidate fine | not stated; citation requested |
| `usgs-lro-wac-gld100-100m` | Moon | 100 m | 1.11 | 79S–79N, 98.2% (publisher) | candidate fine, caveat | public domain |
| `usgs-stac-kaguya-tc-dtms-v2` | Moon | 10.33 m nominal; 10–5,003 m sampled | 0.11 | 155,925 sparse items | candidate fine | CC0-1.0 |
| `usgs-stac-lola-shot-copc` | Moon | point cloud | n/a | 288 COPC tiles | not ready (gridding) | CC0-1.0 |
| `nasa-moon-trek-lola-256ppd` | Moon | 118.45 m | 1.32 | global image service | coarse context only | NASA public data |
| `pds-ode-lro-lola-gdr` | Moon | per product | n/a | metadata index, 270/7/21 products | metadata only | PDS metadata |
| `usgs-mgs-mola-dem-global-463m` | Mars | 463.09 m | 5.15 | global 46,080×23,040 | coarse context only | public domain |
| `pds4-mgs-mola-megdr` | Mars | 463.06 m (128 ppd); 3.70 km (16 ppd) | 5.15 | global tiles | coarse context only | not stated; DOI 10.17189/1z1b-kv84 |
| `usgs-mars-hrsc-mola-blend-200m` | Mars | 200 m | 2.22 | global 106,694×53,347 | coarse context only | **MOLA CC0 + HRSC CC BY-SA 3.0 IGO** |
| `usgs-stac-mro-ctx-dtms` | Mars | 20.00–35.96 m | 0.22 | 44,628 sparse items | candidate fine | CC0-1.0 |
| `usgs-stac-hirise-dtms` | Mars | 0.25–2.15 m | 0.02 | 1,262 sparse items | validation only | CC0-1.0 |
| `nasa-mars-trek-mola-128ppd` | Mars | 463.06 m | 5.15 | global image service | coarse context only | NASA public data |
| `usgs-m20-jezero-ctx-dem-20m` | Mars | 20 m | 0.22 | Jezero crater, 5 windows | existing reference | public domain; cite authors |

Two discovery services are catalogued separately: the **USGS Astrogeology STAC API**
(19 collections observed) and the **NASA PDS ODE REST** interface. Four sources are
explicitly blocked/unverified and two are rejected (see §5).

## 4. What is actually reachable, by body

### Moon

1. **SLDEM2015 512 ppd is the strongest fine global-mid-latitude source.** It is a
   LOLA + SELENE Terrain Camera merge, 59.225 m/pixel, simple cylindrical, MOON_ME/DE421,
   float32 kilometres relative to a 1,737.4 km sphere. The PDS3 label confirms 512 pixels
   per degree and coverage only from 60S to 60N. Byte-range reads work; each tile is about
   1.42 GB, so only row windows may be read. All five D01 metrics can be computed after a
   legitimate downsample to 90 m.
2. **The six LOLA 5 m site DEMs and the 87–90S 5 m mosaic are the only verified fine polar
   products.** Site04 is 3,200 × 3,200 px (16 × 16 km); Site23 is larger (70.6 MB). The
   mosaic is 40,000 × 40,000 px (200 × 200 km), COG, 4.20 GB. About 90% of 5 m pixels are
   interpolated; the mosaic's published RMS residual against the individual fields is
   30–55 cm. These are local products, not a global polar DEM.
3. **GLD100 is 100 m, not 90 m.** It covers 79S–79N (98.2% of the lunar surface per the
   USGS page). Resampling 100 m to 90 m is a slight upsample; it needs a sensitivity test
   before it can be treated as a 90 m source.
4. **Kaguya TC v2 is a large, sparse fine archive.** 155,925 items, nominal 10.33 m, but
   sampled items also had 29.97 m and 5,003 m GSD; one sample was a 9 × 2,181 px strip.
   Ten of ten sampled DTM assets returned HTTP 206. Item counts are not area coverage, and
   every item needs a GSD/shape/footprint filter.
5. **Coarser global Moon products are context only.** LDEM128 is 236.9 m; Moon Trek is
   118.45 m. Both are 1.3–2.6× coarser than 90 m. Moon Trek declares no `noDataValue`, so
   its voids cannot be distinguished reliably.
6. **LOLA shot data are not a DEM.** The 288 COPC tiles (sample: 31.4 million points,
   201.6 MB) require a documented gridding method. They must not be turned into 90 m
   metrics without validation.

### Mars

1. **No verified global Mars DEM at or finer than 90 m exists in the sources checked.**
   The best global products are the HRSC–MOLA blend at 200 m (2.22× coarser, CC BY-SA) and
   MOLA at 463.06 m (5.15× coarser, public domain/CC0 depending on distribution).
2. **Fine Mars terrain is sparse.** 44,628 CTX DTMs at 20.00–35.96 m and 1,262 HiRISE DTMs
   at 0.25–2.15 m. These are stereo footprints, not a seamless product. Ten of ten sampled
   CTX `dem` assets returned HTTP 206. HiRISE was less clean: 3 of 10 sampled advertised
   `dtm` hrefs returned HTTP 404 because the href omitted the stereo-pair folder; the
   corrected folder URL returned HTTP 206.
3. **The existing five Jezero windows cannot be expanded from the CTX STAC collection.**
   The bbox `18.0,18.3,18.6,18.7` returned **0** matching CTX DTMs. The existing windows
   use a separate USGS Mars 2020 CTX mosaic (20 m, 90.35 MB), which is local to Jezero.
4. **The HRSC–MOLA blend has a licence blocker.** The USGS page states
   “MOLA (CC0) and HRSC (CC BY-SA 3.0 IGO)”. Derived products may carry share-alike
   obligations; do not ingest without legal review. MOLA 463 m and CTX DTMs are the safer
   coarse/fine alternatives.
5. **Mars Trek is MOLA at 128 ppd (463.06 m).** It is a global visualization/export service,
   not a 90 m source; its JSON declares `noDataValue: null`.

## 5. Blockers, rejected candidates and defects

| Item | Status | Reason | Open alternative |
| --- | --- | --- | --- |
| JAXA SELENE/Kaguya TC DARTS | blocked/unverified | No anonymous, documented bulk API verified; registration/agreement may be required | USGS STAC Kaguya TC v2 DTMs (CC0) |
| ESA Mars Express HRSC PSA | blocked/unverified | Access path and product-level licences not verified in this audit | USGS HRSC–MOLA blend (access verified, CC BY-SA) |
| MGS MOLA PEDR shots | blocked/unverified | Shot archive; requires a validated gridding method | MOLA MEGDR 463 m; CTX DTMs |
| LRO LOLA RDR direct | blocked/unverified | Reduced Data Records are not a DEM | SLDEM2015; PGDA 5 m site DEMs |
| Viking MDIM 2.1 232 m | rejected | Panchromatic image mosaic, no elevation | MOLA; CTX DTMs |
| LOLA colour shaded relief 128 ppd | rejected | Rendered hillshade, no elevation values | SLDEM2015; LDEM128 |

Observed data/access defects that affect planning:

- **HiRISE broken hrefs (3/10 sampled).** The STAC item advertises a flat path; the actual
  object is under a stereo-pair folder. Repair or validate before reading.
- **CTX Jezero absence.** The 44,628-item CTX collection returned 0 items in the Jezero bbox.
- **Content-type inconsistency.** S3 assets switch between `image/tiff` and
  `binary/octet-stream`; the checker accepts either and validates the TIFF magic instead of
  trusting the header.
- **PGDA licences unstated.** LDEM128, the six 5 m site DEMs and the 87–90S mosaic do not
  state a licence on their product pages. The repository's CC0 inference comes from the
  NASA Science Data Portal general licence; do not describe these as explicitly CC0.
- **HRSC share-alike.** See above.
- **Trek `noDataValue` missing.** Both ImageServer JSONs omit a usable NoData value
  (Moon) or explicitly return `null` (Mars); voids cannot be classified from service
  metadata alone.

## 6. D01 contract mapping and gaps

All five D01 features are computable from an elevation grid. They are **not** computable
from a hillshade or an image mosaic, and only partially computable from a slope map.

| Source class | slope mean | slope p90 | relief p95−p05 | roughness std | flat <5° | 90 m support |
| --- | --- | --- | --- | --- | --- | --- |
| DEM/DTM at ≤90 m native (SLDEM, LOLA 5 m, Kaguya, CTX, HiRISE, Jezero) | yes | yes | yes | yes | yes | downsample to 90 m |
| GLD100 at 100 m | yes | yes | yes | yes | yes | 1.11× upsample; caveat |
| Global DEM 118–463 m (LDEM128, Moon Trek, MOLA, Mars Trek, HRSC) | yes | yes | yes | yes | yes | upsample required; **not comparable** |
| LOLA GDRDSM slope maps 120/240 m | yes | yes | no | no | yes | coarse; partial |
| LOLA COPC / MOLA PEDR points | after gridding | after gridding | after gridding | after gridding | after gridding | needs documented gridding |
| Hillshade / MDIM imagery | no | no | no | no | no | not applicable |

Gap table:

| Gap | Body | Evidence | Impact | Bounded mitigation |
| --- | --- | --- | --- | --- |
| No global Mars DEM ≤90 m | Mars | best global 200 m HRSC, 463 m MOLA | No global Mars 90 m claim | Sparse CTX/HiRISE sampling; coarse context only |
| Moon polar 90 m gap | Moon | SLDEM stops at ±60; global polar grids 118/237 m | North polar and 60–87S cannot be true 90 m | 5 m south-polar mosaic; accept missing elsewhere |
| Fine DTMs are sparse | Moon/Mars | 155,925 / 44,628 / 1,262 items | Item counts ≠ area coverage | Per-window footprint checks; checkpoint gates |
| SLDEM is a fusion product | Moon | LOLA+TC, 59.2 m | Unquantified difference vs 5 m LOLA sites | Co-located sensitivity test before mixing |
| Kaguya per-item QA | Moon | 10.33–5,003 m sampled; 9×2,181 px strip | Some items unusable | Filter gsd/shape/footprint |
| HiRISE broken hrefs | Mars | 3/10 sampled 404 | Planner reads fail | Repair folder path; validate each href |
| Jezero CTX absence | Mars | 0 items in bbox | Cannot expand existing windows | Keep 5 existing windows; use separate mosaic |
| HRSC share-alike | Mars | USGS licence field | Redistribution risk | Legal review; prefer MOLA/CTX |
| PGDA licence unstated | Moon | product pages | Redistribution uncertainty | Cite; confirm before publishing derived data |
| Point-cloud gridding | Moon | LOLA COPC | Method changes roughness | Use ready DEMs; validate any gridding |

**Conclusion:** the repository has a defensible *sparse fine* Moon/Mars capability and a
*coarse global* context capability. It does **not** have global 90 m terrain coverage for
either body, and no source in this audit can supply it today.

## 7. Bounded, checkpointed expansion plan

The plan is a sample, not a survey. Every checkpoint is capped by windows, network bytes
and CPU time, and every checkpoint fails closed. “Network cap” is an upper bound, not an
expected download; no full raster is ever fetched. Raw bytes are not committed; only
derived numeric manifests are.

| Checkpoint | Source(s) | Max windows | Network cap | CPU cap | Gate | Stop rule |
| --- | --- | ---: | ---: | ---: | --- | --- |
| CP0 | Catalog/checker only | 0 | 8 MB | 2 min | 44/44 probes pass; no raster download | Any regression stops the plan |
| CP1 | SLDEM2015 512 ppd | 24 | 32 MB | 5 min | 24/24 valid; valid fraction ≥0.8; ≤256 KiB/window | Stop after 8 if >1 valid-fraction failure |
| CP2 | LOLA 5 m south pole | 12 | 64 MB | 10 min | 12/12 valid; ≥0.8; ≤2 MiB/window | Abort if >2 windows fall outside the 5 m footprint |
| CP3 | Kaguya TC v2 | 30 | 96 MB | 15 min | ≥27/30 valid; ≥0.8; rejected items recorded | Stop after 10 if >3 QA/coverage failures |
| CP4 | CTX DTMs | 30 | 96 MB | 15 min | ≥27/30 valid; ≥0.8; no Jezero request | Stop after 10 if >3 QA/coverage failures |
| CP5 | HiRISE DTMs | 5 | 64 MB | 10 min | 5/5 valid; ≥0.8; ≤16 MiB/window | Abort on 404 or footprint smaller than read bbox |
| CP6 | Coarse context only | 0 | 32 MB | 5 min | Native support only; tagged `not_comparable` | Never upsample into the 90 m index |
| **Total** | | **101** | **392 MB** | **62 min** | | |

Details and resumability:

- **CP1** samples 8 longitude sectors × 3 latitude bands (30S, 0, 30N). Each window reads a
  2.2 km box from one 59.2 m tile; the source bytes are a few kilobytes, so the 32 MB cap is
  dominated by HTTP overhead.
- **CP2** uses 8 windows from the 87–90S mosaic and 4 from site DEMs not already represented
  in the 18 references. It never falls back to 118 m upsampling.
- **CP3** filters `gsd <= 30 m`, `proj:shape >= [200, 200]`, and a footprint that fully
  contains the 2.2 km read box. A failed item is recorded as missing.
- **CP4** uses Gale and two other bboxes with at least 20 matching CTX items. It never
  requests the Jezero bbox and never substitutes MOLA for a failed 90 m window.
- **CP5** is a validation subset, not a coverage expansion: 2.0–2.15 m GSD, footprint
  ≥2.2 km in both axes, COG overviews, no 0.25 m full-resolution read.
- **CP6** is optional and produces a coarse-only report. HRSC is excluded unless legal
  review clears CC BY-SA 3.0 IGO.
- Every record is resumable by `(source_id, item_id/tile, centre_lon, centre_lat)`; a
  checkpoint writes its manifest atomically and can resume after a network failure.
- The 101-window cap is ~0.0003% of Mars’ surface by area and a tiny fraction of the
  Moon; it cannot support a global claim even if fully completed.

## 8. Reproducibility commands

```bash
# 1. Full catalog/access check (metadata and 1 KiB range probes only)
python3 scripts/check_d01_sources.py

# 2. Inspect the machine-readable catalog
python3 -m json.tool docs/data-sources/d01-terrain.json | head -80

# 3. Reproduce individual source checks
curl -sS -o /dev/null -D - -r 0-1023 \
  https://pds-geosciences.wustl.edu/lro/lro-l-lola-3-rdr-v1/lrolol_1xxx/data/sldem2015/tiles/float_img/sldem2015_512_00n_30n_000_045_float.img

curl -s 'https://stac.astrogeology.usgs.gov/api/collections/mro_ctx_controlled_usgs_dtms/items?limit=1&bbox=-138,-6,-136,-4' \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["numberMatched"], d["features"][0]["properties"]["gsd"])'

curl -s 'https://trek.nasa.gov/moon/trekarcgis/rest/services/LRO_LOLA_DEM_Global_256ppd_v06/ImageServer?f=json' \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["pixelSizeX"], d["extent"])'
```

The checker is stdlib-only and has no install step. It needs outbound HTTPS to the official
endpoints listed in the catalog. A failure is a source regression or a network fault; it is
never converted into a zero or a substitute value.

## 9. Observed checker output

Command: `python3 scripts/check_d01_sources.py`
Exit status: **0**.

```text
D01 Moon/Mars terrain source checker
catalog: /tmp/opencode/wt-agent-a/docs/data-sources/d01-terrain.json
sources: 16 | metadata_services: 2 | blocked/unverified: 4 | rejected: 2 | probes: 44

PASS  usgs-pds-lro-lola-sldem2015-512ppd           200  text/plain                                 body 4,692 B
PASS  usgs-pds-lro-lola-sldem2015-512ppd           206  application/octet-stream                   object 1,415,577,600 B, read 1,024 B
PASS  usgs-pds-lro-lola-sldem2015-512ppd           200  text/html; charset=UTF-8                   body 22,916 B
PASS  nasa-pgda-lola-ldem128-pa                    200  text/html; charset=utf-8                   body 13,073 B
PASS  nasa-pgda-lola-ldem128-pa                    206  image/tiff                                 object 2,705,286,892 B, read 1,024 B
PASS  nasa-pgda-lola-south-pole-sites-5m           200  text/html; charset=utf-8                   body 317,341 B
PASS  nasa-pgda-lola-south-pole-sites-5m           206  image/tiff                                 object 40,980,806 B, read 1,024 B
PASS  nasa-pgda-lola-south-pole-sites-5m           206  image/tiff                                 object 40,980,810 B, read 1,024 B
PASS  nasa-pgda-lola-south-pole-sites-5m           206  image/tiff                                 object 40,980,808 B, read 1,024 B
PASS  nasa-pgda-lola-south-pole-sites-5m           206  image/tiff                                 object 40,980,806 B, read 1,024 B
PASS  nasa-pgda-lola-south-pole-sites-5m           206  image/tiff                                 object 40,980,802 B, read 1,024 B
PASS  nasa-pgda-lola-south-pole-sites-5m           206  image/tiff                                 object 70,586,804 B, read 1,024 B
PASS  nasa-pgda-lola-south-pole-mosaic-5m          200  text/plain; charset=utf-8                  body 1,611 B
PASS  nasa-pgda-lola-south-pole-mosaic-5m          206  image/tiff                                 object 4,200,862,545 B, read 1,024 B
PASS  nasa-pgda-lola-south-pole-mosaic-5m          200  text/html; charset=utf-8                   body 7,230 B
PASS  usgs-lro-wac-gld100-100m                     206  image/tiff                                 object 10,461,394,351 B, read 1,024 B
PASS  usgs-lro-wac-gld100-100m                     200  text/html; charset=utf-8                   body 29,264 B
PASS  usgs-stac-kaguya-tc-dtms-v2                  200  application/json; charset=utf-8            body 3,518 B
PASS  usgs-stac-kaguya-tc-dtms-v2                  200  application/geo+json; charset=utf-8        body 10,351 B
PASS  usgs-stac-kaguya-tc-dtms-v2                  206  image/tiff                                 object 4,225,791 B, read 1,024 B
PASS  usgs-stac-lola-shot-copc                     200  application/json; charset=utf-8            body 2,704 B
PASS  usgs-stac-lola-shot-copc                     200  application/geo+json; charset=utf-8        body 6,246 B
PASS  usgs-stac-lola-shot-copc                     206  binary/octet-stream                        object 201,616,541 B, read 1,024 B
PASS  nasa-moon-trek-lola-256ppd                   200  application/json;charset=UTF-8             body 2,306 B
PASS  nasa-moon-trek-lola-256ppd                   200  application/json;charset=UTF-8             body 3,368 B
PASS  pds-ode-lro-lola-gdr                         200  text/xml; charset=utf-8                    body 356 B
PASS  pds-ode-lro-lola-gdr                         200  text/xml; charset=utf-8                    body 354 B
PASS  usgs-mgs-mola-dem-global-463m                206  image/tiff                                 object 2,125,771,142 B, read 1,024 B
PASS  usgs-mgs-mola-dem-global-463m                200  text/html; charset=utf-8                   body 32,570 B
PASS  pds4-mgs-mola-megdr                          200  application/xml                            body 6,765 B
PASS  pds4-mgs-mola-megdr                          200  text/plain                                 body 4,820 B
PASS  pds4-mgs-mola-megdr                          206  application/octet-stream                   object 129,761,280 B, read 1,024 B
PASS  usgs-mars-hrsc-mola-blend-200m               206  image/tiff                                 object 11,384,463,908 B, read 1,024 B
PASS  usgs-mars-hrsc-mola-blend-200m               200  text/html; charset=utf-8                   body 36,482 B
PASS  usgs-stac-mro-ctx-dtms                       200  application/json; charset=utf-8            body 4,529 B
PASS  usgs-stac-mro-ctx-dtms                       200  application/geo+json; charset=utf-8        body 9,536 B
PASS  usgs-stac-mro-ctx-dtms                       206  binary/octet-stream                        object 635,969 B, read 1,024 B
PASS  usgs-stac-hirise-dtms                        200  application/json; charset=utf-8            body 3,471 B
PASS  usgs-stac-hirise-dtms                        200  application/geo+json; charset=utf-8        body 7,960 B
PASS  usgs-stac-hirise-dtms                        206  image/tiff                                 object 30,171,054 B, read 1,024 B
PASS  nasa-mars-trek-mola-128ppd                   200  application/json;charset=UTF-8             body 1,989 B
PASS  usgs-m20-jezero-ctx-dem-20m                  206  image/tiff                                 object 90,345,300 B, read 1,024 B
PASS  usgs-astrogeology-stac                       200  application/json; charset=utf-8            body 2,987 B
PASS  pds-ode                                      200  text/html                                  body 19,785 B

summary: 44 passed, 0 failed, 44 probes
result: OK (all previously verified sources still respond as recorded)
```

The `object … read 1,024 B` lines mean the source object’s total size was read from
`Content-Range`; only 1 KiB was transferred. No DEM was downloaded.

## 10. Decision and next action

The D01 Moon/Mars baseline should remain **sparse fine references + coarse context**. The
next legitimate step is **CP1 (SLDEM2015, 24 windows)** followed by **CP2 (5 m south pole,
12 windows)**, because those are the only sources that can produce true 90 m metrics with a
clear public-domain path. CP3/CP4 should start only after CP1/CP2 pass their gates. CP5 is
validation only. CP6 is context only and must never enter the 90 m index.

If any checkpoint fails, the correct outcome is a recorded missing window, not a zero, an
upsample, or an unverified substitute.
