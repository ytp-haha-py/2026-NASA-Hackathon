# Six-dimension backend release status

Status: **implementation in progress, not a completed six-dimension release.** This report records
what actually runs against real data, what is only a schema or inventory, and what remains blocked.
Machine-readable issue state is in [`IMPLEMENTATION_STATUS.json`](IMPLEMENTATION_STATUS.json).

## What changed in this workstream

1. v1→v2 migration now keeps the original v1 WGS84 geometry on Earth Areas, records the analysis
   CRS separately, transforms support bboxes into the analysis CRS, and preserves feature valid
   fraction, extraction scale, dataset-native resolution and context source resolution. Migration
   warnings record transformed envelopes and missing legacy fields.
2. Candidate-grid coverage now separates requested projected area, generated extent, cell area,
   internal stride gaps, boundary margins and coverage fraction. It no longer emits an
   `exhaustive_within_bbox` label.
3. A reusable acquisition framework now provides provider discovery/inspection/plan/acquire/
   process/validate, deterministic task fingerprints, bounded concurrency, atomic assets, SHA-256
   checks, resume checkpoints, validation, reporting and portable releases.
4. A real D05 Earth pilot was acquired from NASA POWER for five AOIs and 2024-01-01..07: 10 detected
   timeseries observations and 5 assets. It is context-only and explicitly not a 1.98 km
   measurement.
5. The D05 release imports into the additive typed-v2 SQLite store and is queryable through
   `/api/v1/typed/observations` and `/api/v1/typed/areas/{id}`.
6. PR #58 additionally fixes zero-weight eligibility for partial reference overrides, incompatible
   method versions and nullable coarse candidate values.

## Body × dimension status

| Body | D01 Terrain | D02 Geology/mineralogy | D03 Thermal | D04 Water/ice | D05 Illumination/radiation | D06 Regolith |
|---|---|---|---|---|---|---|
| Earth | 113 fine windows, 565 feature observations; **168 real 5 km coarse cells** in 3 pilot regions | 15 evidence-only availability/status records; 0 shareable GLiM values | **20 NASA POWER thermal context observations** (5 AOIs) | **20 NASA POWER water-context observations** (5 AOIs) | **10 NASA POWER solar context observations**; no ionizing radiation | **75 SoilGrids evidence-only observations** (5 AOIs, 5 properties, 0–5 cm) |
| Moon | 18 fine reference windows | **1 map unit + 1 process record**; 0 verified quantitative minerals | No observations | No observations | **8 SPICE model-geometry observations** | No observations |
| Mars | 5 fine reference windows | **10 map units + 4 process records + 14 CRISM context values**; 10 unknown mineral states; 0 verified quantitative minerals | No observations | No observations | **10 SPICE model-geometry observations** | No observations |

Counts are observed records in the specified subset, not global coverage. Missing sources are not
zero-filled and are not reported as negative detections.

## Comparison eligibility

| Dimension | Current rule |
|---|---|
| D01 fine terrain | Comparable only among the same 90 m method/support subset |
| D01 coarse | Real 168-cell pilot index; separate 250 m method; no global coverage or same-support Moon/Mars coarse reference |
| D02 geology | 56 evidence-only typed records; no Terrain Euclidean score; quantitative minerals unavailable |
| D03 Earth POWER | Context-only; coarse air/skin temperature, not local or thermal inertia |
| D04 Earth POWER | Context-only; soil wetness, snow depth and precipitation; no confirmed ice |
| D04 Moon/Mars | Not comparable; no validated observations |
| D06 Earth SoilGrids | Evidence-only; 250 m model predictions at four depths (300 records), not local samples |
| D05 Earth POWER | Context-only; coarse model/reanalysis, not local irradiance |
| D05 Moon/Mars SPICE | Model-only solar incidence/elevation; no observed irradiance, horizon/shadow, or radiation |
| D05 radiation | Unavailable; never converted from solar geometry |

## Verification actually run

```text
bash scripts/verify.sh --ci
→ PASS (14 steps)

bash scripts/backend_acceptance.sh
→ PASS (focused suites + legacy API smoke + pinned data validation)

D05 acquisition:
plan → 5 tasks
run → 5 succeeded, 0 failed
validate → ok, 5 checkpoints
report → 10 observations, 5 areas, 5 assets, all detected
typed import → 5 areas, 5 assets, 10 observations, run release:9568cfbce841dd0d
```

The committed release is under `pipelines/pilots/d05_earth_power/` with SHA-256 checksums in
`manifest.json`.

## Remaining blockers

- Real Geomorpho250 acquisition and a coarse Earth index; same-support Moon/Mars coarse references.
- D02 Earth GLiM redistribution rights; Earthdata authentication and EMIT adapter; lunar mineral
  registration; validated CRISM detection thresholds. Moon/Mars map evidence is now integrated as
  evidence-only typed records.
- D03 Moon/Mars thermal provider access and calibration; thermal inertia/local-solar-time products.
- D04 Moon/Mars ice, neutron/radar/spectral evidence and detection semantics.
- D05 terrain horizon/shadow, area-average illumination, and detector-based radiation.
- D06 Moon/Mars sparse sample measurement tables. Earth SoilGrids now has 0–60 cm depth
  coverage for the five pilot AOIs; 60–100 cm and 100–200 cm remain out of scope.
- R2 publication, deployment and Tunnel are out of scope for this report.

No unsupported comparison, large raster download, R2 transfer, deployment or automatic merge is
claimed.
