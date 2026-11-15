# Goal dependency DAG (2026-11-15)

Source of truth: GitHub open issues + `docs/IMPLEMENTATION_STATUS.json`. Completed
foundation work is not repeated; this DAG only covers remaining work.

## Critical path

```text
Geomorpho/PDS source authorization
  -> #28 real source asset verification
  -> #29 bounded raster reader (software done)
  -> #30 grid/coverage (software done; real plan pending)
  -> #31 coarse features (software done)
  -> #32 batch execution (software done)
  -> #33 immutable Parquet index (software done)
  -> #34 DuckDB query (software done)
  -> #38 global API (software done)
  -> #69 global frontend (ready now)
```

The longest external dependency is authorized real raster/planetary source access. The frontend
and database workstreams do not wait for it.

## Ready now (parallel)

| Workstream | Issues | Dependencies met | Output |
|---|---|---|---|
| Frontend six-dimension | #68 | #47 typed API, #49 contract | dynamic D01–D06 panel, states/roles/support/evidence |
| Frontend global | #69 | #38 global API, #30–#34 software | coverage/candidates/refine-plan UI with no_index/409 states |
| Database capacity | #70 | #33/#34/#47 | 100k/1M/per-global benchmark, size report, backup/restore tests |
| Mars/PostGIS path | #2, #3 | comparability gates/API foundation | MOLA/thermal provider gates + PostGIS adapter or explicit scope |

## Blocked / external

| Issue | Blocker |
|---|---|
| #28 real coarse source | authorized bounded Geomorpho250 asset acquisition and disk budget |
| #35/#36 same-support Moon/Mars coarse | validated same-support planetary DEM products |
| #40 curvature/geomorphon | source windows with sufficient buffers |
| #41 Earth lithology values | GLiM redistribution rights |
| #42 quantitative minerals | EMIT auth / lunar registration / CRISM thresholds |
| #43 Moon/Mars thermal | Diviner/THEMIS/PDS access and calibration |
| #44 Moon/Mars water ice | neutron/radar/spectral access and detection semantics |
| #45 Moon/Mars radiation | SPICE/detector products and frame validation |
| #46 Moon/Mars regolith | sparse sample/PDS measurements |

## Integration rule

Each workstream uses its own worktree/branch. It runs focused tests only (avoid duplicate full
`verify.sh` runs), commits, pushes and opens a PR. Coordinator merges sequentially, resolves
App/API/locale integration conflicts, then runs one `bash scripts/verify.sh --ci` and
`bash scripts/backend_acceptance.sh` on merged `main`.
