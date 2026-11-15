# D03 planetary thermal pilot (Moon Diviner PCP, Mars THEMIS TI)

Status: **bounded observed evidence, `evidence_only`.** Issue #43 verifies two official
public products and persists real per-window values over the existing Moon and Mars
reference AOIs. No cross-body D03 score is produced and Earth NASA POWER D03 context is
unchanged.

Pilot export: `pipelines/pilots/d03_planetary_thermal/` (`areas.json`, `datasets.json`,
`assets.json`, `observations.jsonl`, `manifest.json`, `plan.json`, `REPORT.md`).

Code:

- `pipelines/src/analog_pipelines/thermal_planetary.py` (pure parsing, selection, semantics)
- `scripts/build_planetary_thermal_pilot.py` (opt-in download, export)
- offline tests: `pipelines/tests/test_thermal_planetary.py`
- opt-in metadata/raster-header test:
  `pipelines/planetary/tests/test_thermal_metadata_online.py`

## Counts actually obtained

| Item | Value |
|---|---:|
| Moon reference windows | 18 |
| Moon windows with PCP samples (00:00-00:15 bin) | 18 |
| Moon windows with PCP samples (12:00-12:15 bin) | 11 |
| Mars reference windows | 5 |
| Mars windows with THEMIS TI samples | 5 |
| Observations | 41 (`surface_brightness_temperature_k` 36, `thermal_inertia` 5) |
| States | `detected` 31, `no_coverage` 7, `unknown` 3 |

`no_coverage`/`unknown` arise only from genuinely absent or too-sparse native bins in
the published sparse PCP file. They are not zero-filled and carry no numeric value.
The 7 `no_coverage` rows are the noon PCP bin at LOLA site01-roi4, site11-roi1..4,
site20 and site23. The 3 `unknown` rows are noon bins with fewer than five native
bins at site01-roi1..3. All values are stored in `observations.jsonl`; the full
per-window table is in the pilot `REPORT.md`.

## Moon — LRO Diviner Polar Cumulative Products (PCP)

| Field | Verified value |
|---|---|
| Provider | NASA PDS Geosciences Node; UCLA Diviner Lunar Radiometer Experiment |
| Bundle / collection | `urn:nasa:pds:lro_diviner_derived1`; `urn:nasa:pds:lro_diviner_derived1:data_derived_pcp` |
| Product IDs | `PCP_AVG_TBOL_POLS_SUM_LTIM01_240.TAB`, `PCP_AVG_TBOL_POLS_SUM_LTIM49_240.TAB` |
| Product version | PDS4 collection/product `1.0`; calibration software `4.0` |
| DOI / citation | `10.1029/2019JE006028`; Williams et al. (2019), *JGR* 124, 2505-2521 |
| Access / licence | Public NASA PDS archive; the PDS4 label carries no explicit licence field; cite the authors |
| Native resolution | 240 m/pixel (`MAP_SCALE = 240 <m/pix>`) |
| Footprint | South polar cap, `-90` to `-80` deg latitude, full `-180..180` longitude |
| Projection / frame | Polar stereographic, planetocentric, positive east, sphere `R = 1737.4 km`; `x = r sin(lon)`, `y = r cos(lon)` |
| Time support | 2009-07-05T16:50:26.195Z to 2019-02-17T00:00:00.000Z; southern summer (subsolar latitude < 0) |
| Local solar time | 0.25 h bins; `ltim01` = 00:00-00:15, `ltim49` = 12:00-12:15 |
| Units / quantity | K; average **bolometric brightness temperature** (unit-emissivity assumption) |
| Uncertainty | none supplied per bin/pixel in the product; recorded as `unknown` |
| No-data | bins with no data are omitted (sparse); special constants `-9998`/`-9999` rejected |
| File size | 212,653,759 bytes (`ltim01`), 197,367,449 bytes (`ltim49`) |
| Columns | `x, y, clon, clat, tbol`, fixed 59-byte ASCII rows |

The product's own `x/y` convention was checked against the first published row and
against the existing LOLA Site04 window centre (`-2812.5, -6647.5 m`), so the 1.98 km
selection is not a re-projection guess.

### Selection and result semantics

Each reference window is a circular 1.98 km window of native 240 m bins (about 53
expected bins). The stored value is the spatial mean of the bins present in one
local-time bin. `spatial_min_k`, `spatial_max_k`, `spatial_n` and `spatial_std_k`
are recorded in `legacy_provenance`. The native resolution is 240 m; nothing is
resampled or reported as 90 m.

Example values (full table in the pilot `REPORT.md`):

| Window | 00:00-00:15 mean K (n) | 12:00-12:15 mean K (n) |
|---|---:|---:|
| `moon-shackleton-rim-site04-roi1` | 136.03 (47) | 153.13 (36) |
| `moon-lola-site01-roi1` | 120.57 (53) | 206.84 (3, `unknown`) |
| `moon-lola-site20-bounds-centre` | 86.18 (53) | — (0, `no_coverage`) |

### What is deliberately not computed

- No diurnal range or change rate. The two local-time bins are multi-year composites
  from different samples, not a same-sol pair. A builder guard refuses to emit
  `diurnal_temperature_range_k`.
- No kinetic surface temperature. Bolometric brightness temperature assumes unit
  emissivity and is a different physical quantity.
- No cloud/shadow mask is invented from cold bins; the product supplies no such flag.

## Mars — USGS THEMIS quantitative thermal inertia

| Field | Verified value |
|---|---|
| Provider | USGS Astrogeology Science Center / ASU Mars Space Flight Facility / NASA MDAP |
| Product | THEMIS-derived global thermal inertia mosaic, quantitative 32-bit, edition 1 (2014-05-30) |
| Product page | <https://astrogeology.usgs.gov/search/map/mars-themis-derived-global-thermal-inertia-mosaic> |
| Tile used | `THEMIS_TI_Mosaic_Quant_00N060E_100mpp.cub` (0-30 N, 60-120 E) |
| DOI / citation | `10.1029/2006JE002735`; Fergason et al. (2006), *JGR* 111, E12004 |
| Access / licence | Public domain; cite the authors |
| Native resolution | 100 m/pixel |
| Format | ISIS3 tiled cube, `StartByte = 65537`, `Format = Tile`, 128 x 128 tiles, float32 LSB, `Samples = 35566`, `Lines = 17783` |
| Units / quantity | `J m^-2 K^-1 s^-1/2`; **apparent thermal inertia** |
| Uncertainty | absolute ~20%, relative within one image ~10%; no per-pixel layer |
| Time support | retrieval treats apparent TI as time-independent; per-pixel acquisition time/season is not carried by the mosaic |
| Depth | upper few centimetres; no single fixed depth interval |
| No-data | no explicit constant in the ISIS label; non-finite, non-positive and >10000 values rejected and counted |
| Full tile size | 2,532,510,198 bytes; only ~0.3-1.3 MB of 128 x 128 tile blocks read per run |

### Selection and result semantics

Only the 128 x 128 ISIS tiles touched by the five existing Jezero 1.98 km windows were
read by HTTP range request. Each value is the spatial mean of native 100 m pixels in
a circular 1.98 km window; `n`, min, max and std are recorded. Nothing is resampled to
90 m.

| Window | Mean TI (n) | Min-max TI | Published ±20% range |
|---|---:|---:|---:|
| `mars-jezero-ctx-window-01` | 213.63 (306) | 162.30-321.29 | 170.91-256.36 |
| `mars-jezero-ctx-window-02` | 357.58 (310) | 282.74-455.14 | 286.07-429.10 |
| `mars-jezero-ctx-window-03` | 358.77 (312) | 298.65-441.07 | 287.01-430.52 |
| `mars-jezero-ctx-window-04` | 460.19 (310) | 292.29-757.71 | 368.15-552.22 |
| `mars-jezero-ctx-window-05` | 619.65 (310) | 444.74-791.00 | 495.72-743.58 |

Thermal inertia is kept as a separate feature from
`surface_brightness_temperature_k`. It is never labelled a surface temperature and
never scored across bodies.

## Candidates checked and rejected or gated

| Candidate | Decision | Reason |
|---|---|---|
| Diviner PCP (used) | verified | bounded ASCII, 240 m, local-time bins, overlaps all 18 Moon AOIs |
| Diviner RCP Lacus Mortis | not used | 1 deg tiles are ~150 MB each and the region does not overlap the Moon AOIs |
| Diviner GDR/GHRM global mosaics | not used | global mosaics; GHRM stops at 70 S and misses the polar AOIs; no bounded per-AOI tile |
| Diviner PRP | not used | model-derived annual temperature/ice depth, not observed brightness temperature at the AOIs |
| THEMIS quantitative TI (used) | verified | bounded ISIS tile reads, 100 m, quantitative |
| THEMIS IR Day/Night global mosaic v12 | rejected | 8-bit stretched/blended "visual representation", not quantitative temperature |
| THEMIS qualitative 8-bit TI | rejected | normalized qualitative product, not a quantitative retrieval |
| TES global thermal inertia (~3 km) | not used | global mosaic and coarse relative to the reference windows |

## Blockers and limits (exact)

- Moon PCP is a multi-year composite at one 0.25 h local-time bin; it is not a single
  acquisition, not a same-sol time series and not a diurnal range.
- Moon PCP supplies no per-bin uncertainty; the value is a spatial window mean.
- Bolometric brightness temperature assumes unit emissivity; it is not kinetic
  surface temperature and not comparable to Earth 2 m air/skin temperature.
- Seven Moon noon-bin windows have no native bins and three have fewer than five;
  they are recorded as `no_coverage`/`unknown`, not zero-filled.
- Mars THEMIS TI has no per-pixel uncertainty or QA layer; absolute accuracy ~20%.
- The retrieval does not correct atmospheric water-ice clouds, surface slopes, or
  subsurface ice/bedrock. Image-to-image calibration errors remain.
- Mars coverage of this product stops at 60 deg latitude; the five Jezero windows
  are inside that footprint.
- No cross-body D03 score and no approved thermal-inertia similarity feature.
- Only the 18 Moon and 5 Mars reference windows are covered; this is not a global or
  exhaustive survey. No raw raster or PCP table is committed.

## Reproduction

```bash
# Opt-in first run: fetch two PCP tables and the small ISIS tile blocks into
# the gitignored var/cache/thermal-43 cache.
uv run python scripts/build_planetary_thermal_pilot.py --download

# Offline rebuild from the cache.
uv run python scripts/build_planetary_thermal_pilot.py

# Offline tests (semantics, state handling, import, pilot consistency).
uv run pytest pipelines/tests/test_thermal_planetary.py -q
uv run pytest apps/api/tests/test_typed_api.py -q -k planetary_thermal

# Opt-in metadata/header check (no PCP table download, one 64 KiB tile).
ANALOG_RUN_NETWORK_TESTS=1 uv run pytest \
  pipelines/planetary/tests/test_thermal_metadata_online.py -q -m network
```

Raw sources stay in `var/cache/thermal-43/` (gitignored). The committed export contains
only derived JSON/JSONL/Markdown plus SHA-256 checksums of the derived files and scope
metadata for the source ranges.
