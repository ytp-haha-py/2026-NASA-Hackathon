# D01 Terrain coverage (Issue #135)

Status: **coverage matrix, existing verified fine/coarse artifacts, representative queries, and verification evidence delivered in this worktree. No new DEM rasters downloaded; no global/exhaustive coverage claimed.**

Scope: D01 Terrain & Landforms (five fine metrics: `slope_mean_deg`, `slope_p90_deg`, `terrain_relief_p95_p05_m`, `roughness_std_m`, `flat_terrain_fraction`) for Earth, Moon, Mars. Reuses merged #108 (Earth Geomorpho 250 m coarse index), #97 (coarse->fine persisted), #112/#116/#117, existing manifests/audits, and the fixed terrain method contract.

## Deliverables

- Machine-readable body x feature Coverage Matrix: `docs/data-sources/d01-coverage-matrix.json` (sha256 `ed89ef7649564c79d86a0a930cd2b0e11fc1199e1a0436673ec04b46c3c0d23f`, 54214 bytes).
- Existing source/access audit and checker reused: `docs/D01_COVERAGE_AUDIT.md`, `docs/data-sources/d01-terrain.json` (sha256 `c4587711c1a4e473a6d05a72b14bd1e87b8b31e8a1f8947397c528ecdaafb583`), `scripts/check_d01_sources.py`.
- D01-owned validation test: `pipelines/tests/test_d01_coverage_matrix.py`.
- Real representative queries for Earth/Moon/Mars fine terrain (all five metrics present) from committed manifests.
- Bounded expansion beyond committed fine windows: **not executed in this session** (network/quota/bounded-ingest discipline). Verified blockers and alternative sources/checkpoint plan are recorded in the coverage matrix and source audit (CP1-CP6, metadata-only checker, resumable keys, byte/window caps). This is a verified blocker/alternative path, not fabricated coverage.

## Actual indexed/queryable coverage (measured from committed artifacts)

| Body | Support | Count | Evidence |
| --- | --- | ---: | --- |
| Earth | Fine 90 m GLO-90 windows | 113 candidates | `pipelines/earth/manifests/copernicus_glo90_earth.json` (sha256 `dda333e0752816a083fae68e87ac624371cff6dddfb68b1e9b28f0b1777d55e8`) |
| Earth | Coarse 250 m Geomorpho cells (context/prefilter, not fine D01 method) | 168 cells / 3 partitions / 3 regions | `docs/global-terrain-results/coverage.json` (sha256 `134b84b7ab66d67f9e6e71c9cc0f978a46644351c348f7224821140956ea516e`) |
| Moon | Fine 90 m LOLA 5 m-site reference windows | 18 references (6 manifests) | `pipelines/planetary/manifests/lola_moon_reference.json`, `lola_site01_references.json`, `lola_site07_references.json`, `lola_site11_references.json`, `lola_site20_references.json`, `lola_site23_references.json` |
| Mars | Fine 90 m Jezero CTX reference windows | 5 references | `pipelines/planetary/manifests/ctx_mars_references.json` (sha256 `6168256e3a03502a48a0aa812ae00365337dc819cd6a161a9f4a75ee2b158153`) |

All committed windows satisfy the fixed method/support contract (1.98 km, 22x22, 90 m, projected body-fixed metric CRS for planetary bodies, Horn 3x3 slope, p95-p05 relief, plane-detrended roughness, slope < 5° flat fraction, valid-fraction rules). Missing/no_coverage/unsupported remain explicit; no zero-fill.

## Verified five terrain metrics (no curvature/geomorphons added)

Existing five metrics remain the only fine D01 similarity metrics; `terrain_research.py` remains experimental/research-only and is not registered as a production feature/classifier. Targeted verification:

- `uv run python -m pytest pipelines/tests/test_d01_coverage_matrix.py -q` -> `3 passed`
- `uv run python -m pytest pipelines/tests/test_terrain_research.py pipelines/tests/test_terrain_buffer.py -q` -> `17 passed`

## Representative real queries (all three bodies)

Earth fine (`pipelines/earth/manifests/copernicus_glo90_earth.json`):
```bash
python3 - <<'PY'
import json
m=json.load(open('pipelines/earth/manifests/copernicus_glo90_earth.json'))
c=m['candidates'][0]
print(c['location_id'], c['lat'], c['lon'], sorted(c['features'].keys()))
print({k:v['value'] for k,v in c['features'].items()})
PY
```
Observed: `earth-alps-r1c1 46.47351604 10.46019318 ['flat_terrain_fraction','roughness_std_m','slope_mean_deg','slope_p90_deg','terrain_relief_p95_p05_m']` with values `{'slope_mean_deg': 27.93998, 'slope_p90_deg': 38.318783, 'terrain_relief_p95_p05_m': 814.970527, 'roughness_std_m': 137.182111, 'flat_terrain_fraction': 0.005}`.

Moon fine (`pipelines/planetary/manifests/lola_site01_references.json`):
```bash
python3 - <<'PY'
import json
m=json.load(open('pipelines/planetary/manifests/lola_site01_references.json'))
r=m['references'][0]
print(r['reference_id'], r['lat'], r['lon'], r['body'], sorted(r['features'].keys()))
print({k:v['value'] for k,v in r['features'].items()})
PY
```
Observed: `moon-lola-site01-roi1 -89.43892646 -137.09597807 Moon ...` with values `{'slope_mean_deg': 8.161142, 'slope_p90_deg': 13.98682, 'terrain_relief_p95_p05_m': 162.242714, 'roughness_std_m': 48.449056, 'flat_terrain_fraction': 0.2525}`.

Mars fine (`pipelines/planetary/manifests/ctx_mars_references.json`):
```bash
python3 - <<'PY'
import json
m=json.load(open('pipelines/planetary/manifests/ctx_mars_references.json'))
r=m['references'][0]
print(r['reference_id'], r['lat'], r['lon'], r['body'], sorted(r['features'].keys()))
print({k:v['value'] for k,v in r['features'].items()})
PY
```
Observed: `mars-jezero-ctx-window-01 18.220253246764436 77.37095200216012 Mars ...` with values `{'slope_mean_deg': 11.696354, 'slope_p90_deg': 15.909283, 'terrain_relief_p95_p05_m': 356.602468, 'roughness_std_m': 21.266199, 'flat_terrain_fraction': 0.0}`.

Earth coarse context (reuse #108/#97, not fine D01 support): `python3 - <<'PY'\nimport json; m=json.load(open('docs/global-terrain-results/coverage.json')); print(m['counts']); print(m['coverage']['label'], m['coverage']['regions'])\nPY`.

## Reproducible commands and provenance

- Inspect matrix: `python3 -m json.tool docs/data-sources/d01-coverage-matrix.json | head`
- Source access audit (metadata/1 KiB range probes only, network): `python3 scripts/check_d01_sources.py`
- Recompute/verify manifests only through bounded licensed workflows: `pipelines/recompute_terrain.py` and committed manifest inputs; no bulk global raster download.
- Coarse index/rebuild reuse: `make data-demo`, `make data-rebuild-index`, `make data-validate` as applicable to #108/#97 artifacts.
- Licences/versions/CRS/QA/uncertainty/roles/gaps per body x feature are recorded in `docs/data-sources/d01-coverage-matrix.json`.

## Limitations and explicit non-claims

- Earth/Moon/Mars fine coverage is **sparse/bounded** (113/18/5 windows), not global and not exhaustive. Unindexed locations are missing/no_coverage, not zero.
- Earth coarse Geomorpho coverage is **local pilot only** (168 cells in 3 windows); its method/support differs from fine 90 m D01 and is context/prefilter evidence, not cross-body comparable ranking.
- Cross-body ranking remains disabled for coarse/planetary evidence where method/support/validation gates fail (see `docs/PLANETARY_COARSE_REFERENCE.md`).
- No curvature/geomorphons added to production scoring/features (#40 gate not passed in this change).
- No shared API schemas, OpenAPI/contracts, scoring engine, or other-dimension files were modified.
- No #118/#119/#123 UI work performed.
- Further real DEM expansion requires bounded checkpointed ingest (CP1-CP6) with provider/network/storage budget; blocked/unverified sources keep alternatives recorded and must not be substituted silently.
