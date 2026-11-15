# Bounded multi-dimension release report (Issue #48)

> **Historical snapshot:** this report records the #48 release state. Current D02–D06
> counts (2,140 typed observations; D06 1,920) and the gated 0–5 cm query role are in
> [`SIX_DIMENSION_BACKEND_RELEASE.md`](SIX_DIMENSION_BACKEND_RELEASE.md) and
> [`FEATURE_ELIGIBILITY_AUDIT.md`](FEATURE_ELIGIBILITY_AUDIT.md).


Status: **local bounded release workflow implemented.** It combines only committed
artifacts, makes absent body × dimension cells explicit, and never claims global
coverage. No R2 upload, live database reset, deployment or automatic merge was
performed.

Machine-readable outputs:

- [`samples/release/analog-release.manifest.json`](../samples/release/analog-release.manifest.json)
- [`samples/release/analog-release.eda.json`](../samples/release/analog-release.eda.json)

## Approved inputs

The builder has a closed allowlist in
[`pipelines/src/analog_pipelines/release.py`](../pipelines/src/analog_pipelines/release.py).
A missing approved artifact fails the build; nothing is downloaded.

| Artifact | Path | Dimension / body | Counts | Artifact SHA-256 |
|---|---|---|---|---|
| `fine-terrain-snapshot` | `samples/snapshot/analog-demo.snapshot.json` | D01 Earth/Moon/Mars | 113 candidates, 23 references, 565 candidate features, 1243 context observations | `43733cf5986822ed06b416f2a1d6f10f624b35413812773c24495d560e9bf0b6` |
| `coarse-terrain-index` | `pipelines/coarse/real-pilot/index/snapshots/geomorpho250-real-pilot-20261115/manifest.json` | D01 Earth | 168 cells, 3 regions, 3 partitions | `00af31fe5b0abd434d27aba622c037c31bb85995ff704c5b242f9f5bd9bbdecd` |
| `typed-d02-geology` | `pipelines/geology/typed/manifest.json` | D02 Earth/Moon/Mars | 56 observations, 33 assets, 8 areas | `e1a87596202357f6771a742dec529a6ebf3ff52d607001045bbed2dcbc6cd1f9` |
| `typed-d03-thermal` | `pipelines/pilots/d03_earth_power/manifest.json` | D03 Earth | 20 observations, 5 tasks/assets | `6cec151a5bd3f33aea2a718fb1feca5fd08c3d9cf09ce204084b1028305e5e37` |
| `typed-d04-water-ice` | `pipelines/pilots/d04_earth_power/manifest.json` | D04 Earth | 20 observations, 5 tasks/assets | `52f4e95076d1cbfe0c3c09bba14c34eb1a1b893d8b250824681d6358fa32cede` |
| `typed-d05-illumination` | `pipelines/pilots/d05_earth_power/manifest.json` | D05 Earth | 10 observations, 5 tasks/assets | `da598016a0057c0c30c6612f24dfeebaddbea7d23a591919c14b6130be371cdf` |
| `typed-d05-spice-geometry` | `pipelines/pilots/d05_spice_illumination/manifest.json` | D05 Moon/Mars | 18 model-geometry observations, 5 kernel assets | `1c642d6c23582761fe01facebbaf5779bd7be5b1a7aef55588cf3d20d041eb45` |
| `typed-d06-regolith` | `pipelines/pilots/d06_earth_soilgrids/manifest.json` | D06 Earth | 75 observations, 75 tasks/assets | `4c982e30c2ad0a6f162bfb9bce5292c009bceaabca3263dce321bfd40ed6c469` |
| `source-inventory` | `catalog/dimension-source-inventory.yaml` | all | 27 source entries across 18 body × dimension cells | `d13fde531033940db3145feeecc4b79ef923294cc7dc8a6020c77555f5b041f2` |

Each artifact record also pins every committed file with its own SHA-256 and
size, plus schema/method versions and source/licence references.

## Aggregate counts

```text
release_id=rel-c30a25fab81d2b7f
generated_at=2026-11-15T05:19:51Z
artifacts=8 files=59
fine_snapshot_id=snap-0cef4d75b147866a data_version=dv-b46f3bad38ea
candidates=113 references=23 datasets=9
fine_feature_observations=565 context_observations=1243
typed_observations=520 (D02 56, D03 20 + 41 planetary, D04 20 + 55 planetary, D05 10 + 18 SPICE, D06 300)
coarse_cells=168 regions=3 partitions=3
explicit absent body×dimension cells=2 (D06 Moon/Mars)
source_references=15 dataset_references=18
```

Counts describe the committed subset only. `global_claim` is `false` in both the
manifest and EDA report.

## Coverage matrix

| Dimension | Earth | Moon | Mars |
|---|---|---|---|
| **D01 Terrain** | 113 fine windows / 565 features / 1243 WorldCover context; 168 coarse cells in 3 pilot regions | 18 reference windows / 90 features | 5 reference windows / 25 features |
| **D02 Geology/mineralogy** | 15 evidence records; 0 validated mineral detections; 15 `unknown`/`no_coverage` | 3 evidence records; 2 detected map/process records; 1 `unknown` | 38 evidence records; 28 detected map/process records; 10 `unknown` |
| **D03 Thermal** | 20 NASA POWER model-context observations | 36 Diviner brightness-temperature observations | 5 THEMIS thermal-inertia observations |
| **D04 Water/ice** | 20 NASA POWER model-context observations | 53 LP NS/Mini-RF evidence observations | 2 HEND epithermal-flux observations |
| **D05 Illumination/radiation** | 10 NASA POWER solar-context observations | 8 SPICE model-geometry observations | 10 SPICE model-geometry observations |
| **D06 Regolith** | 300 SoilGrids 250 m model observations (4 depths) | **absent** (`no_coverage`) | **absent** (`no_coverage`) |

The two absent cells are listed with reasons in the manifest. D05 Moon/Mars geometry is model-only; ionizing radiation remains absent. D02 detected
records are map units/process evidence or unvalidated spectral context, not
mineral abundance. NASA POWER and SoilGrids values are estimates; D01 metrics are
derived from measured DEMs; WorldCover is mapped classification context only.

## EDA: attempted / valid / failed / unavailable

`null` means the artifact did not record that quantity; it is never zero-filled.

| Cell | Attempted | Valid | Failed | Unavailable | Value origin |
|---|---|---|---|---|---|
| D01 Earth fine | 113 candidate windows | 565 feature observations | not recorded | not recorded | derived from measured DEM |
| D01 Earth coarse | 168 indexed cells (breakdown) | 168 cells with complete features | not recorded | 0 null feature values | derived from measured DEM |
| D01 Moon | 18 reference windows | 90 feature observations | not recorded | not recorded | derived from measured DEM |
| D01 Mars | 5 reference windows | 25 feature observations | not recorded | not recorded | derived from measured DEM |
| D02 Earth | not recorded | 0 detected observations | not recorded | 15 missing-state observations | mapped evidence |
| D02 Moon | not recorded | 2 detected observations | not recorded | 1 missing-state observation | mapped evidence |
| D02 Mars | not recorded | 28 detected observations | not recorded | 10 missing-state observations | mapped evidence + unvalidated spectral context |
| D03 Earth | 5 acquisition tasks | 20 detected observations | 0 | 0 | model estimate |
| D04 Earth | 5 acquisition tasks | 20 detected observations | 0 | 0 | model estimate |
| D05 Earth | 5 acquisition tasks | 10 detected observations | 0 | 0 | model estimate |
| D06 Earth | 75 acquisition tasks | 75 detected observations | 0 | 0 | model estimate |

## Duplicates, overlaps and provenance gaps

- The WorldCover overlay repeats all 113 Earth candidate ids; the release
  deduplicates them to 113 and counts 113 shared records, not 226 candidates.
- Duplicate observation ids, asset ids and coarse candidate ids: **0**.
- One fine candidate window pair overlaps (`earth-devon-island`,
  `earth-haughton-r3c1`). It is reported, not removed.
- 291 adjacent coarse cells overlap slightly in their WGS84 bounding envelopes
  because each projected envelope is rounded independently. Projected
  `stride_m == window_size_m` (5000 m) and all 168 candidate ids are distinct;
  this is an envelope note, not duplicate cells.
- D06 references 75 SoilGrids source rasters that are **not bundled in Git**
  (repository policy excludes raw rasters). Their source SHA-256 values remain in
  the pilot manifest, and `raw_files_not_bundled=75` is explicit. The release
  verifies only committed bytes.

## Reproducibility evidence

```text
$ uv run python pipelines/build_release.py build \
    --output var/release-a.manifest.json --eda-output var/release-a.eda.json
release_id=rel-c30a25fab81d2b7f artifacts=8 files=59 candidates=113 references=23 \
typed_observations=520 coarse_cells=168 absent_dimensions=2

$ uv run python pipelines/build_release.py build \
    --output var/release-b.manifest.json --eda-output var/release-b.eda.json
release_id=rel-c30a25fab81d2b7f ...
$ cmp var/release-a.manifest.json var/release-b.manifest.json
$ cmp var/release-a.eda.json var/release-b.eda.json
# both files byte-identical

$ uv run python pipelines/build_release.py verify
OK release_id=rel-627b049fd4da8f6b artifacts=11 files=68 absent_dimensions=2
```

Focused tests (all exit 0):

```text
uv run pytest pipelines/tests/test_release_builder.py -q
# 8 tests: two-build determinism, committed manifest/EDA parity,
# tampered typed observation rejection, tampered Parquet rejection,
# semantic count rejection, EDA absent/origin checks,
# pinned-input immutability + old snapshot verify, copy-only SQLite drill.

uv run pytest pipelines/tests/test_release_builder.py pipelines/tests/test_snapshot.py \
  pipelines/tests/test_real_coarse_pilot.py pipelines/tests/test_sqlite_safety.py \
  pipelines/tests/test_release_reproducibility.py -q
# 23 tests pass
```

The SQLite drill runs on copies only: backup → restore → baseline rebuild keeps
the user AOI, `AnalysisJob`, and the source database SHA-256 unchanged while
`data_version` remains `dv-b46f3bad38ea`.

## Commands

```bash
uv run python pipelines/build_release.py build     # writes manifest + EDA
uv run python pipelines/build_release.py verify    # recomputes every hash/count
uv run python pipelines/build_release.py report    # human summary
```

## Not done / blockers

- No R2 publish or readback; the immutable-prefix rule still requires separate
  maintainer authorization and credentials outside Git.
- No live database reset; restore/rebuild drills are copy-only.
- No global coverage claim: the coarse index covers three pilot regions, and
  Moon/Mars D06 remain absent; D03–D05 now have bounded evidence-only pilots.
- Fine/coarse 90 m vs 250 m methods remain separate and must not be scored as one
  method.
- D02 Earth GLiM/EMIT licence and validated mineral detection remain blocked.
- D06 raw SoilGrids rasters remain uncommitted, so only their pinned source
  checksums are auditable.
