# Terrain & Landforms expansion

This is an extension of the existing providers, registry, manifest, snapshot and SQLite
architecture. Results describe the indexed subset of 1.98 km windows, never a global
survey or a rover driving safety assessment.

## Execution plan

1. Phase 1: expand observed DEM coverage using the existing four metrics; validate
   provider crops, manifests, WorldCover context, reference search and snapshot/index.
2. Phase 2: add flat terrain fraction (<5 degrees among valid Horn slope cells),
   recompute every indexed terrain window, test known planes and missingness.
3. Phase 3: validate curvature and geomorphon definitions, neighbourhoods and buffers.
   Keep unvalidated outputs out of the registry, manifests and distance.
4. Phase 4: expose only validated additions through contracts, API and the existing UI.

Each phase requires tests before the next method change. Phase 1 keeps
`terrain-metrics-1.0.0` and the four existing metrics unchanged.

## Bounded source plan

- Earth: preserve the original five records and sample a 3 x 3 grid at each of twelve
  explicit anchors in `pipelines/earth/batch_ingest.py`. Centres are spaced 3 km in the
  anchor's local UTM frame (larger than a 1.98 km square's diagonal). The finite plan
  contains 113 windows. Anchors sample desert, volcanic, plains, mountain, impact-crater,
  polar and canyon environments; anchor names are sampling envelopes, not per-window
  landform classifications. Nine adjacent windows do not establish regional coverage.
- Copernicus: unsigned COG range reads; only small padded crops are cached. No global
  tile download. DSM includes vegetation and buildings.
- WorldCover: eleven native-10 m class fractions for each measured Earth bbox. Fractions
  use valid pixels, sum to one within rounding tolerance, and are context/filter only.
- Moon: six requested NASA PGDA surf products. Site01/04/07/11 official ROI DBF X/Y
  centres are preferred. If a DBF field overflows as `********`, use the corresponding
  official SHP polygon bbox midpoint and disclose that rule. Site20/23 have no ROI
  archive listed on product78: select the source raster bounds midpoint, require the
  whole padded footprint to lie inside the raster and >=80% valid data. Do not move a
  failed window elsewhere without documenting a new rule. ROI-centred 1.98 km squares
  often overlap: reference counts are not independent sample counts.
- Mars: the single requested Jezero CTX GeoTIFF, capped at 100 MB. Five disjoint source
  coordinate windows form a fixed diagonal transect (10 km X/Y increments). The raster
  uses Mars_2000_Sphere, radius 3396190 m; the product page lists 3396160 m. Preserve
  the actual raster CRS and disclose the discrepancy. No delta/rim/floor interpretation
  is assigned. Source values are metres, localized vertically to MOLA according to the
  product page; per-window vertical error is unverified.
- NASADEM is not downloaded. Ancillary LOLA uncertainty/LDEC products are not claimed
  as measured QA when they have not been read. The documented interpolation/error
  caveats remain; published ROI error ranges are not per-window uncertainty estimates.

## Reproducible operator commands

Set optional directories in local `.env` using `ANALOG_RAW_DIR`, `ANALOG_CACHE_DIR`,
`ANALOG_PROCESSED_DIR`, and `ANALOG_TMP_DIR`. Set `TMPDIR` and `CPL_TMPDIR` to the
same absolute temporary directory. `UV_CACHE_DIR` and `npm_config_cache` can also point
to the external cache volume. The existing dotenv loader respects exported variables;
explicit importer path arguments override defaults. Directory resolution alone never
creates files. Python/GDAL temporary files must be configured before process startup:

```bash
uv run --env-file .env python pipelines/earth/batch_ingest.py --offset 5 --limit 3
uv run --env-file .env python pipelines/worldcover/ingest_worldcover.py --only namib-r1c1,namib-r1c2,namib-r1c3 --manifest /path/to/processed/worldcover-pilot.json
# After the pilot passes; total finite plan is 113, including the original five:
uv run --env-file .env python pipelines/earth/batch_ingest.py --limit 113
uv run --env-file .env python pipelines/worldcover/ingest_worldcover.py
uv run --env-file .env python pipelines/planetary/batch_lola.py --sites 01 --limit-rois 1
uv run --env-file .env python pipelines/planetary/batch_lola.py
uv run --env-file .env python pipelines/planetary/ctx_import.py
```

Use `--dry-run` on the Earth batch to print selection coordinates and source URLs.
Earth and Moon batch runs checkpoint only validated records and retain previous
records on failure. Reruns validate/reuse cached provider crops. Reports are written to
`ANALOG_PROCESSED_DIR`; provider failures are explicit, never zero-filled. LOLA uses
serial HTTP ranges and importer-controlled retries to avoid parallel-range failures.
[GDAL documents these range settings](https://gdal.org/en/stable/user/configoptions.html).
The raw CTX object lives under `ANALOG_RAW_DIR/mars`; large download staging uses the
configured temporary directory. No TIFF, cache, SQLite file or `.env` belongs in Git.

After ingestion, build and verify the pinned snapshot, rebuild into an isolated file
under the processed directory twice, and compare counts and metadata. Refresh an
existing user index with `python -m analog_api.cli seed --keep-existing`; do not reset
an index containing user AOIs. The importer updates only supplied records and retains
unrelated AOIs. The project remains in its original location.

## Source documentation

- [Copernicus DEM AWS registry](https://registry.opendata.aws/copernicus-dem/)
- [ESA WorldCover access and CC BY 4.0](https://esa-worldcover.org/en/data-access)
- [NASA PGDA product78, ROI archives and error limitations](https://pgda.gsfc.nasa.gov/products/78)
- [USGS Jezero CTX product, public-domain use constraint](https://astrogeology.usgs.gov/search/map/mars_2020_science_investigation_ctx_dem_mosaic)

## Completed flat extension and current integration boundary

All 113 Earth, 18 Moon and 5 Mars windows were recalculated from real cached/source
DEMs before adding `flat_terrain_fraction`. The largest absolute numeric difference
in the previous four metrics before six-decimal rounding was <5e-7. Their method
version remains `terrain-metrics-1.0.0`; flat fraction uses
`flat-terrain-fraction-1.0.0`, unit fraction, range 0–1, fixed scale 0.25. Method gates
are checked per feature. Original four-feature requests and legacy manifests remain
valid. Reference-mode searches now use the five available observations; rankings can
change with the new feature. Legacy user AOIs are retained; a missing new observation
is explicitly missing, not zero. New AOI calculations can request the flat feature.

Flat fraction counts only complete Horn neighborhoods within the analysis domain:
for a fully valid 22×22 square, its denominator is 20×20 slope cells. It does not
measure drivable area or operational safety. WorldCover remains separate context.

[Curvature/Geomorphon research](TERRAIN_METHOD_RESEARCH.md) records analytic tests,
proposed scales and the observed buffer blocker; these two features are not integrated.

The production snapshot publish/fetch path now includes checksum-pinned manifests,
not just summary metadata. Fetch restores the original relative file layout as well
as the legacy artifact location, permitting an isolated index rebuild. A prefix with
different existing bytes is rejected rather than overwritten. Use a new snapshot id
for changed data. The prior Phase 1 R2 snapshot remains preserved.
