# WorldCover samples

This directory contains **documentation only**. No WorldCover tile, AOI crop or class
raster is committed here.

The canonical artifact is the small numeric manifest at
`pipelines/worldcover/manifests/worldcover_earth_context.json`: one
`esa-worldcover-2021-v200` dataset entry and eleven WorldCover class fractions for
each of the five indexed Earth candidates, plus per-window SHA-256 checksums and the
exact source tile URLs/ETags.

> **Earth land-cover context only.** These values are not lunar mineralogy, not
> geology, and not a similarity feature.

## What is deliberately not committed

- **Raw WorldCover tiles.** Each public 3 × 3 degree GeoTIFF is roughly 5.6–115 MB
  compressed. The dataset is global, and committing tiles would bloat the repository
  and redistribute bulk source data. Repository policy forbids large raster binaries
  and global downloads.
- **AOI class windows.** The importer caches only the derived class window per AOI as
  a small `uint8` GeoTIFF under `data/cache/worldcover/`. Those five files total about
  60 KB; the directory is gitignored, and the files can be regenerated from the
  documented tile keys and bboxes.
- **Synthetic fixtures.** Offline tests build small class arrays in memory (and
  temporary GeoTIFFs when needed) to exercise nodata handling and proportions. They
  are clearly test-only and never enter a manifest or this directory.

The committed numeric manifest is the reviewable handoff artifact. It is small
enough to diff and contains no source raster data.

## Reproduce the real values

```bash
uv run python pipelines/worldcover/ingest_worldcover.py --dry-run
uv run python pipelines/worldcover/ingest_worldcover.py
uv run pytest pipelines/worldcover/tests -q
```

See `pipelines/worldcover/README.md` for the verified public access path, exact tile
names and byte counts, CC BY 4.0 licensing and attribution, the class-code table,
processing/nodata rules, measured fractions, validation output, and limitations.
