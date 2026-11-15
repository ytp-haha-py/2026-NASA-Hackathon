# Planetary coarse reference pilot (Issue #35)

Status: **bounded same-support Moon and Mars coarse evidence built; cross-body ranking
disabled.** Ten 5 km/native-support cells were computed from three verified public
products. Three of the ten cells are Mars MOLA MEGDR evidence cells whose native support
is 463.0576672 m; they are explicitly `not_comparable`.

No global raster was downloaded. Raw source windows are not committed; only ~37.7 KB of
derived elevation fixtures and the JSON manifests are committed.

## Verified source facts (2026-11-15)

| Fact | Moon LOLA MOON_PA 128 ppd | Mars MOLA MEGDR 128 ppd | Mars Jezero CTX DTM |
| --- | --- | --- | --- |
| Public product | PGDA product 95, `LDEM128_PA_pixel_202405.tif` | PDS4 `urn:nasa:pds:mgs_mola_topography_derived` (1.0), collection `data_meg128` (2.0), product `megt44n000hb` (1.1) | USGS `M20_JezeroCrater_CTXDEM_20m.tif` |
| URL | <https://pgda.gsfc.nasa.gov/data/LOLA_PA/LDEM128_PA_pixel_202405.tif> | <https://pds-geosciences.wustl.edu/mgs/urn-nasa-pds-mgs_mola_topography_derived/meg128/megt44n000hb.img> (PDS3 label `.../meg128/megt44n000hb.lbl` used for GDAL I/O) | <https://planetarymaps.usgs.gov/mosaic/mars2020_trn/CTX/ScienceInvestigationMaps_JPL/M20_JezeroCrater_CTXDEM_20m.tif> |
| Byte size | 2,705,286,892 (global object; never downloaded) | 129,761,280 per 90 x 44 deg tile | 90,345,300 |
| Grid / ppd | 46,080 x 23,040; 128 ppd; 0.0078125 deg | 11,520 x 5,632; 128 ppd; pixel 0.4630576672 km | 4,456 x 5,067; 20 m |
| Native support used | **236.9012 m** at equator | **463.0576672 m** (not 250 m) | **20 m** (aggregated to 250 m) |
| CRS / frame | `GCS_Moon_2000`, `+proj=longlat +R=1737400`, MOON_PA (DE421) | Equirectangular, planetocentric, R = 3396.0 km, IAU2000 | `Jezero_Crater_Projection`, Mars_2000_Sphere R = 3396190 m, planetocentric |
| Vertical | m, relative to 1737.4 km sphere; unit inferred from pixel magnitude (see caveats) | m, radius minus GMM3 (mgm1025) areoid; PDS4 `<unit>meter</unit>` | m, stereo DTM localized to MOLA |
| Nodata / scale / offset | NaN / 1.0 / 0.0 | -32768 / 1.0 / 0.0 | -3.4028234663852886e38 / 1.0 / 0.0 |
| Licence | Not stated on the PGDA product page; public NASA archive with citation requirement (Neumann 2024, doi:10.60903/LOLA_PA) | Not stated in the PDS4 bundle/collection/product labels | Public domain (USGS product page) |

Source sizes were checked with HTTP `Content-Length`/directory listings and rasterio
metadata. The MOLA 16 ppd product is 3.7044613374 km/pixel; it was not used.

## Method

- 5 km cells in a **projected planetary body-fixed metric CRS** (lunar local azimuthal
  equidistant sphere R = 1,737,400 m; the CTX raster's Jezero equirectangular frame;
  the MOLA equirectangular frame for evidence cells). Earth WGS84/UTM is rejected by
  `assert_planetary_crs`.
- Source windows are reprojected bilinearly to a fixed **250 m** analysis grid for the
  Moon and CTX cells. Each cell is computed on a 22 x 22 padded grid, the shared Horn 3x3
  slope is applied, and the central 20 x 20 pixels are aggregated with the existing
  `coarse_features.extract_coarse_features` (mean, linear p90, fraction < 5 deg).
- Mars MOLA evidence cells use **11 native pixels** at 463.0576672 m = 5,093.6343392 m
  per side. No resampling to 250 m is performed; a 250 m grid would upsample an
  interpolated product.
- Method version `coarse-planetary-dem-horn-1.0.0` is deliberately **not**
  `coarse-terrain-250m-1.0.0`. The Earth coarse pilot aggregates a published
  preprocessed slope product (`dtm_slope_merit.dem_m_250m`, 7.5 arc-second, PANGAEA
  899135), not a DEM derivative computed in this repository.

## Results (committed manifests)

| Pilot | Cells | Source support | Valid / missing slope px | Feature range (mean slope, p90, flat <5 deg) | Decision |
| --- | ---: | --- | --- | --- | --- |
| Moon LOLA128 window 01 (20E, 10S) | 4 | 236.9012 m | 1,600 / 0 | 6.589-17.657 deg; 9.619-31.392 deg; 0.193-0.255 | `evidence_only` |
| Mars CTX Jezero window 01 | 4 | 20 m -> 250 m | 1,600 / 0 | 2.289-4.870 deg; 4.720-8.140 deg; 0.540-0.918 | `evidence_only` |
| Mars MOLA MEG128 evidence | 2 | 463.0576672 m native | 242 / 0 | 2.048-3.242 deg; 4.894-4.943 deg; 0.909-0.917 | `not_comparable` |

Mars support comparison on the same two centres (CTX 250 m vs MOLA 463 m):

| Pair | delta mean slope | delta p90 | delta flat fraction |
| --- | ---: | ---: | ---: |
| 1 | -0.7347 deg | -2.0728 deg | +0.2274 |
| 2 | -0.2410 deg | +0.2235 deg | -0.0084 |

These are observed differences that combine support, median binning, interpolation,
vertical registration and mosaic effects. They are **not** a correction factor, not an
Earth calibration, and not a physical equivalence.

## Comparability decision table

| Metric | Moon | Mars CTX | Mars MOLA |
| --- | --- | --- | --- |
| `coarse_slope_mean_deg` | `evidence_only` | `evidence_only` | `not_comparable` |
| `coarse_slope_p90_deg` | `evidence_only` | `evidence_only` | `not_comparable` |
| `coarse_flat_fraction_lt5deg` | `evidence_only` | `evidence_only` | `not_comparable` |
| `terrain_relief_p95_p05_m`, `roughness_std_m`, curvature, geomorphons, 90 m metrics | `not_supported` | `not_supported` | `not_supported` |

`evidence_only` means the quantity and 5 km footprint match, but the slope method does
not: Earth uses a preprocessed 7.5 arc-second slope product, while these cells use a Horn
3x3 DEM derivative. `not_comparable` for MOLA additionally means the native support is
1.85x coarser than the 250 m analysis grid and 2.0x coarser than the Earth coarse support,
with interpolated median bins.

**Cross-body ranking is disabled** in every manifest and in `index.json`. No Earth-Moon or
Earth-Mars coarse ranking is enabled, and no planetary value is written to the Earth coarse
index (`CoarseRow` rejects non-Earth bodies and non-Earth method versions).

### Why the Earth method difference is not quantified

The Earth coarse source is already a slope product. Its source MERIT DEM and preprocessing
kernel are not part of the pinned snapshot. A controlled Earth Horn-at-250 m comparison
would require downloading a global DEM, which Issue #35 forbids. Therefore the
preprocessed-product vs DEM-derivative difference against Earth is **unmeasured** and all
slope-derived planetary metrics stay `evidence_only`. The Mars CTX-vs-MOLA comparison is
the only method/support difference actually measured here.

## Cross-body coarse-ranking gate (Issue #36)

The gate is implemented in `pipelines/src/analog_pipelines/cross_body_gate.py` and exposed
read-only at `GET /api/v1/global/cross-body`. It is an eligibility gate, not a ranking: it
returns `ranked=false`, `ranking_enabled=false`, no distance/score, and a per-source,
per-metric exclusion explanation.

A metric is eligible only when all of the following hold:

| Criterion | Requirement | Current pilot result |
| --- | --- | --- |
| Quantity | Same coarse feature id as the Earth baseline | Pass for the three coarse slope metrics |
| Method | Same producing method version | Fail: `coarse-planetary-dem-horn-1.0.0` vs Earth `coarse-terrain-250m-1.0.0` |
| Support | Native support is not coarser than Earth support beyond the explicit +5% tolerance; a finer source may be aggregated to the common analysis grid, but a coarser source is never upsampled | Moon 236.9 m and CTX 20 m -> 250 m pass; MOLA 463.1 m fails |
| Units | Same unit as the Earth feature | Pass (`degree` / `fraction`) |
| Frame | Both records use a projected metric body-fixed frame class; Earth WGS84 or geographic degrees are never mixed with planetary frames | Pass for the committed projected planetary cells; an Earth/WGS84 or geographic record is rejected |
| Validation | An explicit `comparable` decision | Fail: every metric is `evidence_only` or `not_comparable` |

The support rule is deliberately asymmetric. A 20 m CTX DTM aggregated to the 250 m
analysis grid is not the same native support as the Earth product, but it is not coarser;
it is reported with both native and analysis support ratios. A 463.0576672 m MOLA grid
cannot be upsampled into same-support evidence. The +5% tolerance is an explicit gate
threshold, not a method validation or a correction factor.

Current result (committed manifests):

- 3 evidence sources, 9 metric observations, 0 eligible, 9 excluded, 0 unknown;
- every metric is excluded for `method_mismatch` plus its recorded
  `decision_evidence_only` / `decision_not_comparable` reason;
- all three MOLA metrics additionally fail `support_mismatch`;
- `blocking_features` is the full set of three coarse slope metrics;
- `ranked_candidates` is empty.

Exact blocker: **no planetary metric is produced by the Earth Geomorpho250 preprocessed
slope product, and no `comparable` validation decision exists.** The Earth-vs-planetary
method difference is unmeasured (see the previous section), and MOLA's support is ~2x
coarser. There is therefore no legitimate same-support/subset to rank. The gate refuses
rather than falling back to legacy 90 m weights.

Offline coverage:

```bash
uv run pytest pipelines/tests/test_cross_body_gate.py -q
uv run pytest apps/api/tests/test_global_api.py -q
```

The tests cover refusal, missing support (never zero-filled), Earth-frame rejection for a
planetary body, support-tolerance boundaries, deterministic tie ordering for a synthetic
same-method eligible subset, and refusal to emit any score when no metric is comparable.

## Reproducibility, I/O and cost

Commands:

```bash
uv run python pipelines/build_planetary_coarse.py --plan --allow-network  # metadata only
uv run python pipelines/build_planetary_coarse.py --allow-network        # bounded windows + manifests
uv run python pipelines/build_planetary_coarse.py --verify               # offline fixture/feature check
uv run pytest pipelines/tests/test_planetary_coarse.py -q
ANALOG_RUN_NETWORK_TESTS=1 uv run pytest pipelines/planetary/tests/test_planetary_coarse_online.py -q -m network
```

Observed real run (2026-11-15T06:28Z): 45.75 s wall, peak RSS 135,680 KiB (~132.5 MiB),
10/10 cells computed, 0 failed, 0 missing slope pixels. Committed fixtures total 37,700
bytes. Sum of per-cell source-read upper bounds = 25,568,608 bytes (~24.4 MiB); this is a
conservative bound (block alignment + 64 KiB label allowance), not a measured byte count.
No source object larger than one PDS tile window was requested.

Every committed fixture is a float64 GeoTIFF of the padded reprojected elevation window,
with the planetary CRS embedded. `verify` re-opens it, recomputes Horn slope and the three
coarse features, and requires exact equality with the committed values. Fixture SHA-256,
size and shape are recorded per cell; the manifest also records the candidate ID, projected
and body-fixed bbox, valid/missing counts, source window shape, and upper read bound.

## Preserved legacy references

The existing **18 Moon + 5 Mars** 90 m references are unchanged:

- manifests under `pipelines/planetary/manifests/` still contain 23 references;
- no coarse candidate ID is used by a 90 m reference;
- no coarse manifest is under `pipelines/planetary/manifests/`, so snapshot/index
  discovery and the API reference count are unaffected;
- the coarse method version is distinct and `CoarseRow` rejects non-Earth bodies.

## Known blockers and caveats

1. **MOLA is not 250 m.** MEGDR 128 ppd is 463.0576672 m/pixel; 16 ppd is 3.7044613374
   km/pixel. MEGDR also supplies interpolated median values where no shot falls in a bin.
   Same-support comparison is impossible without a finer local Mars DEM (for example a
   validated CTX/HiRISE DTM), so MOLA stays evidence-only.
2. **Moon vertical unit is inferred, not label-declared.** The GeoTIFF has no vertical
   unit tag; sampled values (~1.2e3 m at the south pole, -2.2e3 m at 20S) and the published
   lunar range (-9127 to +10779 m) identify metres, while the band `actual_range` tag is
   inherited in kilometres. The pilot applies no vertical scale and flags
   `vertical_unit_inferred_from_pixel_magnitude`. Reconfirm before production use.
3. **Moon source is a derived shape model.** LDEM128 is built from SLDEM2015 256 ppd plus
   polar LDEMs averaged at 128 ppd, not raw altimetry; it is partly interpolated.
4. **Unreadable south-polar tile observed.** On 2026-11-15, bounded reads of LDEM128 tiles
   near longitude 202-203 deg at the south pole returned `TIFFReadEncodedTile` errors. The
   pilot window at 20E, 10S is unaffected; the source access issue is recorded rather than
   patched.
5. **Licences.** The MOLA PDS4 labels state no licence/rights text, and the PGDA page
   states a citation requirement but no licence. Redistribution terms must be confirmed by
   the maintainer before any derived MOLA/LOLA value is promoted beyond this evidence pilot.
6. **Mars CTX vertical uncertainty.** Per-window vertical uncertainty, mosaic seams and
   stereo artifacts are not validated; the 20 m texture is not an independent 250 m
   measurement.
7. **No cross-body ranking.** No metric is `comparable`; the gate fails closed.

## Files

- `pipelines/src/analog_pipelines/planetary_coarse.py` - CRS/body gates, bounded reads,
  reprojection, Horn slope, coarse aggregation, comparability table, manifest verify.
- `pipelines/build_planetary_coarse.py` - plan/build/verify CLI.
- `pipelines/planetary/coarse/*.json` - three cell manifests and `index.json`.
- `samples/planetary/coarse/fixtures/*.tif` - 10 tiny derived elevation fixtures.
- `pipelines/tests/test_planetary_coarse.py` - offline grid/valid-mask/nodata/scale-offset/
  frame/comparability/fixture tests.
- `pipelines/planetary/tests/test_planetary_coarse_online.py` - opt-in metadata/bounded
  window check (never run in CI).
