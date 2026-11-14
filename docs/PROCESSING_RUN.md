# Processing runs and QA

Code: `analog_pipelines.qa`, `analog_pipelines.typed_contracts.ProcessingRunV2`.

A processing run is bounded and auditable. It records:

- run id, start/finish timestamps, bounded limits
- input `SourceAsset` ids and checksum scope
- window/region selection rule
- method/schema versions
- output checksum and record counts
- success / failed / no-coverage / not-applicable counts
- exact reproducibility command and warnings

## Mandatory checks

| Check | Behavior |
|---|---|
| Missing vs zero | A valid numeric `0` is `state=detected`; missing states carry **no value** |
| Nodata | Excluded from valid fraction; never filled with zero |
| CRS/units | Source CRS and physical units are declared and validated |
| Scale/offset | Applied explicitly; invalid/non-finite/zero rejected |
| Checksum | Source/derived/window scopes are distinct; completed runs require output checksum |
| Counts | `succeeded <= attempted`; `completed` cannot hide failures |
| Failure | Failed runs cannot advertise a successful output |
| Area IDs | Duplicate IDs rejected; overlapping areas flagged |
| Time/depth | Declared support is required where the feature semantics need it |
| Provenance | Every observation references at least one known source asset |

`validate_source_asset`, `validate_observation`, `validate_processing_run` and
`validate_area_overlaps` return errors/warnings; they never repair a value.

## Offline usage

```bash
uv run pytest pipelines/tests/test_foundation_contracts.py -q
```

No download, cloud credential, or R2 access is involved. A run may use only local
fixtures in tests. Real source access must wait for explicit authorization.

## Operator paths

Use `analog_pipelines.paths.data_dir("raw"|"cache"|"processed"|"tmp")` and the
`ANALOG_*_DIR` environment variables. Never hardcode a maintainer-local path.

## Future real-source checklist

1. Verify source product/version/licence/access and commit the source inventory update.
2. Stage only an authorized bounded file; record SHA-256 and bytes.
3. Run `ProcessingRunV2` with explicit limits and a dry-run plan.
4. Validate CRS/units/scale/nodata/QA before extraction.
5. Persist typed observations + manifest; do not publish to R2 without maintainer approval.
6. Record actual counts and blockers in the PR.
