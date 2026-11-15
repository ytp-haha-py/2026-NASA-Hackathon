# GSC OF 5529 true polygon area fractions

Acceptance-grade companion to the WMS point-grid pilot in the parent directory.

## Source and licence

- Geological Survey of Canada, Open File 5529 (Chorlton, 2007), ~1:35,000,000.
- DOI <https://doi.org/10.4095/223767>.
- Official Open Access ZIP: 577,276,750 bytes, SHA-256
  `7ff615c7b076ac07c58e38c541cbbc2ec2353d7f889cbc000788f4c12ea4b89e`.
- Open Government Licence – Canada 2.0.
- The global ZIP is cached outside Git; only the two-AOI clipped evidence is committed.

## Method

1. Extract `roxworld.shp` (EPSG:4322, GCS WGS 1972) and the `rudbf` dBase tables.
2. Load only the polygons intersecting the two committed AOIs.
3. Repair invalid source geometry explicitly with `shapely.make_valid`; count
   invalid and repaired features (both 0 here).
4. Intersect true polygons with the AOI in a local equal-area LAEA CRS
   (`+datum=WGS84`, AOI-centred).
5. Join `RUNO` to `RUCODE`, `RXTP` and age terms from the shipped dBase tables.
6. Write one evidence record per raw unit with fraction, area, coverage, raw
   attributes, source CRS, metric CRS, licence and limitations.

## Measured result

| AOI | Raw RUNO | RUCODE | RXTP | Category | Fraction | Coverage |
|---|---:|---|---|---|---:|---:|
| `earth-atacama-desert` | 9487 | `Q` | SEDIMENT | sedimentary | 0.9999999999965 | 0.9999999999965 |
| `earth-lanzarote` | 526 | `betaN`, `betaQ/betaQ1` | VOLCANIC | igneous | 0.9999999999976 | 0.9999999999976 |

Invalid source geometries: 0; repaired: 0. Fractions are true polygon
intersections, not point-grid estimates. The map is still generalized at
~1:35,000,000, far coarser than a 1.98 km AOI; no abundance, aqueous alteration,
geology similarity or combined score is produced.

## Reproduction

```bash
# one-time global download + extract to var/cache/gsc-world (gitignored)
uv run --with-requirements pipelines/geology/requirements-ingest.txt \
  python scripts/build_gsc_polygon_pilot.py --download \
  --generated-at 2026-11-15T00:00:00Z

# or point at an existing extracted shapefile + DBF directory
uv run --with-requirements pipelines/geology/requirements-ingest.txt \
  python scripts/build_gsc_polygon_pilot.py \
  --source /path/roxworld.shp --dbf-dir /path/rudbf \
  --generated-at 2026-11-15T00:00:00Z

# offline verification
uv run python scripts/build_gsc_polygon_pilot.py --check
uv run pytest pipelines/tests/test_gsc_polygon.py -q
```
