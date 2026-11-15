# Committed batch evidence — `geomorpho-pilot-real-20261115`

Verbatim JSON evidence from the bounded, resumable three-region Geomorpho250 batch run
described in [`docs/GEOMORPHO_BATCH_PILOT.md`](../../../../../docs/GEOMORPHO_BATCH_PILOT.md)
(Issue #32). It is committed so that the global evaluation report
([`docs/GLOBAL_TERRAIN_RESULTS.md`](../../../../../docs/GLOBAL_TERRAIN_RESULTS.md), Issue #39)
can be regenerated offline from real measured inputs instead of transcribed prose.

## What is committed

| Path | Origin |
|---|---|
| `report.json` | `var/geomorpho-batch/runs/geomorpho-pilot-real-20261115/report.json` |
| `checkpoint.json` | `var/geomorpho-batch/runs/geomorpho-pilot-real-20261115/checkpoint.json` |
| `tasks/<task-id>/task.json` | `var/geomorpho-batch/runs/geomorpho-pilot-real-20261115/tasks/<task-id>/task.json` |

The Parquet outputs (`tasks/<task-id>/rows.parquet`) and the staged source windows
(`var/cache/geomorpho-pilot/*.tif`) are **not** committed: they are reproducible from the
bounded `/vsicurl` staging script and are larger than a review artifact. Their SHA-256 values
are recorded here (and in `report.json`/`task.json`) so a rerun can be compared.

## Scope and provenance

- Run: `geomorpho-pilot-real-20261115`, `source_mode=real`, 11/11 tasks succeeded,
  168 rows across 3 regions (48 alps, 56 Grand Canyon, 64 Sahara).
- Method `coarse-terrain-250m-1.0.0`, grid `coarse-grid-1.0.0`,
  Parquet `coarse-parquet-1.0.0`, checkpoint `1.0.0`.
- The 168 rows are identical to the committed real coarse index
  (`pipelines/coarse/real-pilot/index`, snapshot `geomorpho250-real-pilot-20261115`) on all
  measurement columns; the batch only adds the `batch_partition` QA flag
  (`docs/GEOMORPHO_BATCH_PILOT.md`).
- This is a **three-region pilot**, not a global or exhaustive survey. The batch stores no
  refined 90 m values and must not be compared with `terrain-metrics-1.0.0`.

## Verification

`uv run python scripts/report_global_terrain.py --check` re-verifies the index SHA-256 values,
the batch checksums, the per-region row counts, and the version contract, then compares the
generated report byte-for-byte with the committed files.
