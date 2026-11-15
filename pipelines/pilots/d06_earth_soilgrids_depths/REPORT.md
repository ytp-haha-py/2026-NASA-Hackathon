# D06 SoilGrids multi-depth pilot

- Release version: `acquisition-release-1.0.0`
- Scope: `pilot-d06-earth-depths`
- Generated at: `2026-11-15T06:27:06.055449+00:00`
- Observations: 300
- Assets: 300 (response checksums only; raw rasters not committed)
- Plan tasks: 300
- By state: {'detected': 300}
- Areas: earth-atacama-desert, earth-death-valley, earth-devon-island, earth-lanzarote, earth-sahara-r1c1

## Source and licence

- Product: ISRIC SoilGrids 2.0, 250 m global model predictions.
- Service: public OGC WCS 2.0.1 at `https://maps.isric.org/mapserv`; no credentials.
- Licence: CC BY 4.0; cite ISRIC SoilGrids 2.0.
- Evidence class: **model prediction, not a measurement**.
- Model uncertainty: separate Q0.05/Q0.95 prediction-quantile records per area/property/depth; not a confidence interval for the AOI mean.

## Requests

| Status | Requests |
| --- | ---: |
| succeeded | 300 |

- Failed/unavailable: 0

## Depth coverage

| Depth | Observations |
| --- | ---: |
| 0-0.05 m | 75 |
| 0.05-0.15 m | 75 |
| 0.15-0.3 m | 75 |
| 0.3-0.6 m | 75 |

Depths are separate observations and are never averaged or scored together.

## Failed requests

- None observed in this run.

## QA

- QA groups (area/property/depth): 100
- Complete groups: 100
- Missing groups: 0

## Reproduction

```bash
uv run python scripts/build_soilgrids_depth_pilot.py --dry-run
uv run python scripts/build_soilgrids_depth_pilot.py --resume --workers 2
uv run python scripts/import_typed_release.py --release pipelines/pilots/d06_earth_soilgrids_depths --database var/d06-depths.db
```

Offline checks:

```bash
uv run pytest pipelines/tests/test_soilgrids_depths.py -q
uv run pytest pipelines/tests/test_regolith_semantics.py -q
```

## Limitations

- Five AOIs only; this is not a global or exhaustive SoilGrids survey.
- AOI values are means over the AOI envelope on the native 250 m grid, not SoilGrids product cell values at a point.
- SoilGrids bdod is fine-earth bulk density and excludes coarse fragments.
- No Moon or Mars regolith value is provided; see `contracts/d06-regolith-semantics-v1.json`.
