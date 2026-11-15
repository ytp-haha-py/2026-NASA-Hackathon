# GSC Open File 5529 Earth-lithology pilot

**Acceptance status: the polygon pilot (`polygon/`) satisfies #41 for the two
bounded Earth AOIs; the WMS point-grid pilot below is retained as supplementary
evidence and is explicitly not a polygon area fraction.**

The polygon pilot downloads the official ~550 MB Open File 5529 ZIP to a
gitignored cache, clips only the two AOIs, repairs invalid geometries with
`make_valid`, intersects true polygons in a local equal-area LAEA CRS and joins
the shipped `RUNO` dBase attributes (`RUCODE`, `RXTP`, ages). Committed output:
Atacama `RUNO=9487` / `SEDIMENT` → sedimentary, Lanzarote `RUNO=526` /
`VOLCANIC` → igneous, both with coverage ≈1.0 and 0 invalid/repaired source
geometries. See `polygon/README.md` and `../gsc_polygon.py`.

Bounded ingestion of the legally usable alternative identified in
`../area-fraction/SOURCE_VERIFICATION.md`: the Geological Survey of Canada
*Generalized Geology of the World* (Chorlton, 2007, Open File 5529, about
1:35,000,000), served as a public WMS by the USGS Mineral Resources Program.

## Source and licence

- Product: **Generalized Geology of the World: Bedrock Domains and Major Faults
  in GIS Format**, Geological Survey of Canada Open File 5529 (2007).
- DOI: <https://doi.org/10.4095/223767>
- Access: public WMS `https://mrdata.usgs.gov/services/gscworld`, layer `geology`,
  no credentials.
- Licence: **Open Government Licence – Canada 2.0**
  (<https://open.canada.ca/en/open-government-licence-canada>); attribution
  required. Third-party rights are excluded by the licence.
- Native CRS: EPSG:4326; map scale about 1:35,000,000.

## What was ingested

Two existing Earth AOIs were sampled with a bounded 5×5 WMS point grid. The
script does **not** download the ~550 MB global GIS ZIP and does not query the
whole world.

| AOI | Raw `gid` | `agerxtp` | `rxtyp` | Mapped category | Fraction | Coverage |
|---|---|---|---|---:|---:|---:|
| `earth-atacama-desert` | 8686 | Cenozoic sedimentary rocks | sedimentary rocks | sedimentary | 1.0 | 1.0 (25/25) |
| `earth-lanzarote` | 86 | Cenozoic volcanic rocks | volcanic rocks | igneous | 1.0 | 1.0 (25/25) |

The raw GSC attributes (`gid`, `cover_id`, `runo_id2`, `agerxtp`, `eon`, `era`,
`rxtyp`) are preserved in `evidence.jsonl`. `registry.json` maps only documented
`rxtyp` values to broad, non-exclusive categories; no value maps to aqueous
alteration. `manifest.json` records source/licence, method, counts and SHA-256
checksums.

## Method and limits

- **Point-grid fraction, not polygon intersection.** At 1:35,000,000 the source
  polygons are far coarser than a 1.98 km AOI, so the reported fraction is the
  share of 25 bounded WMS samples assigned to a raw unit. This is flagged in
  every record (`point_grid_not_polygon_intersection`,
  `map_scale_1_35m_coarser_than_aoi`).
- **This point-grid artifact is supplementary.** The separate polygon pilot
  (`polygon/`) uses the official global ZIP and true polygon intersections; it is
  the acceptance-grade artifact. This file remains explicitly marked
  `evidence_only_insufficient_for_area_fraction_acceptance` so it cannot be
  mistaken for polygon ingestion.
- No abundance, no mineral detection, no aqueous-alteration inference, no
  geology similarity or combined six-dimension score.
- A no-result sample is a real gap and lowers coverage; it is never zero-filled.
- The USGS WMS is a rendering of the GSC data; the original GIS ZIP and the
  extended attribute database were not downloaded.

## Reproduction

```bash
# offline plan; no network
uv run python scripts/build_gsc_world_pilot.py --dry-run

# bounded live WMS build (50 point queries)
uv run python scripts/build_gsc_world_pilot.py --generated-at 2026-11-15T00:00:00Z

# verify committed checksums/counts without network
uv run python scripts/build_gsc_world_pilot.py --check

uv run pytest pipelines/tests/test_gsc_world.py -q
```

The live query is also exposed as an opt-in test:
`ANALOG_RUN_NETWORK_TESTS=1 uv run pytest pipelines/tests/test_gsc_world.py -m network`.
