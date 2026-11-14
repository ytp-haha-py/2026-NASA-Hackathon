# samples/planetary — committed vs non-committed artifacts

This directory is **documentation-only**. No raster binary or synthetic fixture is
committed here. The real Moon reference is the validated numeric manifest at
`pipelines/planetary/manifests/lola_moon_reference.json`; the derived source window is a
runtime cache under `data/cache/lola/` and is gitignored.

## Committed

| Path | What it is |
| --- | --- |
| `pipelines/planetary/manifests/lola_moon_reference.json` | Real, observed Moon reference measured from the official NASA PGDA Site04 (Shackleton rim) LOLA 5 m/pix DEM, 1980 m / 90 m window, ROI 2. Four verified observations. |
| `pipelines/planetary/lola.py` | Provider, source constants, CRS/window/cache/manifest helpers. |
| `pipelines/planetary/lola_import.py` | CLI importer (`uv run python pipelines/planetary/lola_import.py`). |
| `pipelines/planetary/tests/` | Offline pytest suite (37 tests; no network). Includes a TEST-ONLY synthetic lunar plane generated in `tmp_path`; it is never committed and never written into the real manifest. |
| `pipelines/planetary/README.md` | Source URIs, CRS/resolution, access method/bytes, processing, caveats, enable/disable, MOLA extension point. |
| `pipelines/planetary/NASA_LOLA_STATUS.md` | Exact HTTP statuses, sizes, headers, byte budget, extractor blocker/workaround, integration output. |
| `samples/planetary/README.md` | This document. |

## Not committed (runtime or external)

| Path / object | Why not committed |
| --- | --- |
| `data/cache/lola/lola-window-aee94d03b7b73be8ed41.tif` | Derived 414 x 414 px, 2070 m source window cache, 433,015 bytes. Gitignored via `data/cache/`; reproducible with the importer. |
| `data/cache/lola/lola-window-aee94d03b7b73be8ed41.json` | Cache sidecar: source URL, ETag, Last-Modified, bounds, CRS, SHA-256, access timestamp. Gitignored. |
| Full `Site04_final_adj_5mpp_surf.tif` | 40,980,806 bytes (41 MB) official NASA product. It is read by HTTP range requests; the full object is never stored. |
| Product 81 87–90°S mosaic (`ldem_87s_5mpp.tif`, 3,465,285,714 bytes; slope, 5,255,076,239 bytes; count, 145,895,726 bytes) | Far above the 50 MB investigation budget; documented but not downloaded or used. |
| `TEST_ONLY_synthetic_lunar_plane_5m.tif` | Created only inside pytest's `tmp_path`; marked synthetic and never copied into `samples/` or the real manifest. |

## Why there is no committed sample raster

- The scientific artifact is the **validated manifest**, not a raster thumbnail. The
  manifest contains the exact source URL, ETag, CRS, window bounds, checksum, software
  versions and the four measured values.
- The full NASA products are large and are updated/validated by HTTP headers. Committing
  a copy would duplicate an external dataset and could drift from the product's ETag.
- The derived cache is only 433 KB, but it is a runtime derivative of the 5 m source and
  is intentionally excluded by `.gitignore` (`data/cache/`).
- Any synthetic TIFF in this directory would risk being mistaken for real lunar data.
  The only synthetic raster used by the tests is generated at test time in `tmp_path`,
  is named `TEST_ONLY_synthetic_lunar_plane_5m.tif`, and is validated with
  `data_kind: "synthetic"` / `quality: "synthetic"` in a temporary manifest.

## Reproduce the real reference

```bash
uv run python pipelines/planetary/lola_import.py --dry-run   # metadata + plan, no window/manifest
uv run python pipelines/planetary/lola_import.py             # real range read + manifest
uv run pytest pipelines/planetary/tests -q                   # offline checks
```

Observed real values in the committed manifest:

| Feature | Value | Unit |
| --- | ---: | --- |
| `slope_mean_deg` | 17.895873 | degree |
| `slope_p90_deg` | 28.777052 | degree |
| `terrain_relief_p95_p05_m` | 390.805907 | meter |
| `roughness_std_m` | 102.805473 | meter |

`valid_fraction` = 1.0; source window checksum
`sha256:0db13ffd3e0d49c04ce468ce5312f4cd46fa6b2f4f99854dc4def100d0f08050`;
cache SHA-256 `702aac2f93e0f36dd1025900d4e5021be17411ee735d93df7191cc33b65cf095`.

This is one 1.98 km window at the Shackleton rim. It is not global Moon coverage, not an
illumination ranking, and not a landing-site recommendation. See
`pipelines/planetary/README.md` and `NASA_LOLA_STATUS.md` for the caveats and provenance.
