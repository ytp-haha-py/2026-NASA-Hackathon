# D06 SoilGrids 0-5 cm expansion

- Release version: `acquisition-release-1.0.0`
- Scope: `d06-earth-expansion-01`
- Generated at: `2026-11-15T10:21:42.466851+00:00`
- Observations: 45
- Assets: 45 (response checksums only; raw rasters not committed)
- Plan tasks: 45
- By state: {'detected': 45}
- Areas: earth-grand-canyon-r2c2, earth-iceland-r2c2, earth-namib-r2c2

## Source and licence

- Product: ISRIC SoilGrids 2.0, 250 m global model predictions.
- Service: public OGC WCS 2.0.1 at `https://maps.isric.org/mapserv`; no credentials.
- Licence: CC BY 4.0; cite ISRIC SoilGrids 2.0.
- Evidence class: **model prediction, not a measurement**.
- Model uncertainty: separate Q0.05/Q0.95 prediction-quantile records per area/property/depth; not a confidence interval for the AOI mean.

## Requests

| Status | Requests |
| --- | ---: |
| succeeded | 45 |

- Failed/unavailable: 0

## Depth coverage

| Depth | Observations |
| --- | ---: |
| 0-0.05 m | 45 |

Depths are separate observations and are never averaged or scored together.

## Failed requests

- None observed in this run.

## QA

- QA groups (area/property/depth): 15
- Complete groups: 15
- Missing groups: 0

## Reproduction

```bash
uv run python scripts/build_soilgrids_depth_pilot.py --dry-run \
  --scope d06-earth-expansion-01 --depths 0-5cm --statistics mean,Q0.05,Q0.95 \
  --area-id earth-grand-canyon-r2c2 --area-id earth-namib-r2c2 --area-id earth-iceland-r2c2 \
  --plan var/plans/d06-earth-expansion-01.json --run-dir var/runs/d06-earth-expansion-01 \
  --release pipelines/pilots/d06_earth_soilgrids_expansion_01
uv run python scripts/build_soilgrids_depth_pilot.py --scope d06-earth-expansion-01 --depths 0-5cm --statistics mean,Q0.05,Q0.95 \
  --area-id earth-grand-canyon-r2c2 --area-id earth-namib-r2c2 --area-id earth-iceland-r2c2 \
  --plan var/plans/d06-earth-expansion-01.json --run-dir var/runs/d06-earth-expansion-01 \
  --release pipelines/pilots/d06_earth_soilgrids_expansion_01 --resume --workers 2
uv run python scripts/import_typed_release.py --release pipelines/pilots/d06_earth_soilgrids_expansion_01 --database var/analog.db
```

Offline checks:

```bash
uv run pytest pipelines/tests/test_soilgrids_depths.py -q
uv run pytest pipelines/tests/test_regolith_semantics.py -q
```

## Limitations

- Three AOIs and one depth (0-5 cm) only; this is not a global or exhaustive SoilGrids survey.
- AOI values are means over the AOI envelope on the native 250 m grid, not SoilGrids product cell values at a point.
- SoilGrids bdod is fine-earth bulk density and excludes coarse fragments.
- No Moon or Mars regolith value is provided; see `contracts/d06-regolith-semantics-v1.json`.
