# D06 SoilGrids 0-5 cm all-Earth-candidate expansion

- Release version: `acquisition-release-1.0.0`
- Scope: `d06-earth-all-0-5cm`
- Generated at: `2026-11-15T10:49:15.810150+00:00`
- Observations: 1575
- Assets: 1575 (response checksums only; raw rasters not committed)
- Plan tasks: 1575
- By state: {'detected': 1575}
- Areas: earth-alps-r1c1, earth-alps-r1c2, earth-alps-r1c3, earth-alps-r2c1, earth-alps-r2c2, earth-alps-r2c3, earth-alps-r3c1, earth-alps-r3c2, earth-alps-r3c3, earth-altiplano-r1c1, earth-altiplano-r1c2, earth-altiplano-r1c3, earth-altiplano-r2c1, earth-altiplano-r2c2, earth-altiplano-r2c3, earth-altiplano-r3c1, earth-altiplano-r3c2, earth-altiplano-r3c3, earth-devon-polar-r1c1, earth-devon-polar-r1c2, earth-devon-polar-r1c3, earth-devon-polar-r2c1, earth-devon-polar-r2c2, earth-devon-polar-r2c3, earth-devon-polar-r3c1, earth-devon-polar-r3c2, earth-devon-polar-r3c3, earth-grand-canyon-r1c1, earth-grand-canyon-r1c2, earth-grand-canyon-r1c3, earth-grand-canyon-r2c1, earth-grand-canyon-r2c3, earth-grand-canyon-r3c1, earth-grand-canyon-r3c2, earth-grand-canyon-r3c3, earth-haughton-r1c1, earth-haughton-r1c2, earth-haughton-r1c3, earth-haughton-r2c1, earth-haughton-r2c2, earth-haughton-r2c3, earth-haughton-r3c1, earth-haughton-r3c2, earth-haughton-r3c3, earth-iceland-r1c1, earth-iceland-r1c2, earth-iceland-r1c3, earth-iceland-r2c1, earth-iceland-r2c3, earth-iceland-r3c1, earth-iceland-r3c2, earth-iceland-r3c3, earth-namib-r1c1, earth-namib-r1c2, earth-namib-r1c3, earth-namib-r2c1, earth-namib-r2c3, earth-namib-r3c1, earth-namib-r3c2, earth-namib-r3c3, earth-pampas-r1c1, earth-pampas-r1c2, earth-pampas-r1c3, earth-pampas-r2c1, earth-pampas-r2c2, earth-pampas-r2c3, earth-pampas-r3c1, earth-pampas-r3c2, earth-pampas-r3c3, earth-pingualuit-r1c1, earth-pingualuit-r1c2, earth-pingualuit-r1c3, earth-pingualuit-r2c1, earth-pingualuit-r2c2, earth-pingualuit-r2c3, earth-pingualuit-r3c1, earth-pingualuit-r3c2, earth-pingualuit-r3c3, earth-rio-tinto, earth-sahara-r1c2, earth-sahara-r1c3, earth-sahara-r2c1, earth-sahara-r2c2, earth-sahara-r2c3, earth-sahara-r3c1, earth-sahara-r3c2, earth-sahara-r3c3, earth-snake-river-r1c1, earth-snake-river-r1c2, earth-snake-river-r1c3, earth-snake-river-r2c1, earth-snake-river-r2c2, earth-snake-river-r2c3, earth-snake-river-r3c1, earth-snake-river-r3c2, earth-snake-river-r3c3, earth-wupatki-r1c1, earth-wupatki-r1c2, earth-wupatki-r1c3, earth-wupatki-r2c1, earth-wupatki-r2c2, earth-wupatki-r2c3, earth-wupatki-r3c1, earth-wupatki-r3c2, earth-wupatki-r3c3

## Source and licence

- Product: ISRIC SoilGrids 2.0, 250 m global model predictions.
- Service: public OGC WCS 2.0.1 at `https://maps.isric.org/mapserv`; no credentials.
- Licence: CC BY 4.0; cite ISRIC SoilGrids 2.0.
- Evidence class: **model prediction, not a measurement**.
- Model uncertainty: separate Q0.05/Q0.95 prediction-quantile records per area/property/depth; not a confidence interval for the AOI mean.

## Requests

| Status | Requests |
| --- | ---: |
| succeeded | 1575 |

- Failed/unavailable: 0

## Depth coverage

| Depth | Observations |
| --- | ---: |
| 0-0.05 m | 1575 |

Depths are separate observations and are never averaged or scored together.

## Failed requests

- None observed in this run.

## QA

- QA groups (area/property/depth): 525
- Complete groups: 525
- Missing groups: 0

## Reproduction

```bash
# See the committed plan.json for the exact 105 --area-id arguments.
uv run python scripts/build_soilgrids_depth_pilot.py --dry-run \
  --scope d06-earth-all-0-5cm --depths 0-5cm --statistics mean,Q0.05,Q0.95 \
  --plan var/plans/d06-earth-all-0-5cm.json --run-dir var/runs/d06-earth-all-0-5cm \
  --release pipelines/pilots/d06_earth_soilgrids_all_0_5cm
uv run python scripts/import_typed_release.py --release pipelines/pilots/d06_earth_soilgrids_all_0_5cm --database var/analog.db
```

Offline checks:

```bash
uv run pytest pipelines/tests/test_soilgrids_depths.py -q
uv run pytest pipelines/tests/test_regolith_semantics.py -q
```

## Limitations

- 105 AOIs and one depth (0-5 cm) only; this covers the committed Earth fine index but is not a global or exhaustive SoilGrids survey.
- AOI values are means over the AOI envelope on the native 250 m grid, not SoilGrids product cell values at a point.
- SoilGrids bdod is fine-earth bulk density and excludes coarse fragments.
- No Moon or Mars regolith value is provided; see `contracts/d06-regolith-semantics-v1.json`.
