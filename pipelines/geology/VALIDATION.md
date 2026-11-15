# Observed validation — 2026-11-14

以下為原始 `feat/16-geology-mineralogy` 實作階段的歷史驗證，當時尚未提交。
後續交付已將原始內容提交保存；最新 main 基底的交付驗證與 PR #54 複查結果另見
`docs/OPENCODE_HANDOFF.md`，請勿將本頁舊基底的數量當作目前共用 Snapshot。

## 最終品質 gate

實際執行（cache 路徑僅為本次執行環境設定，不在 pipeline 中硬編碼）：

```bash
UV_CACHE_DIR=/tmp/geology-uv-cache npm_config_cache=/tmp/geology-npm-cache bash scripts/verify.sh
```

在 sandbox 外執行的最終命令 exit **0**，實際結尾：

```text
[verify] RESULT: PASS - 14 steps in 70s (full gate)
```

Ruff lint／format、API／pipeline pytest、OpenAPI drift、原有 offline data gate、npm ci、
typecheck、ESLint、前端測試、build 與生成 API types 全部通過。前端實際為 3 個 test files、
63 tests passed。原有 Snapshot 仍是 `snap-d3ed2ba59558e7f8`、5 candidates、3 datasets、
1 reference、20 features、55 context observations，沒有改寫。

觀察到的既有 warnings：Starlette TestClient httpx deprecation、測試用無 CRS raster warning、
前端 bundle >500 kB；沒有為此修改共用依賴或前端。GitHub CI 尚未執行／觀察。

最初 sandbox 內 gate 在既有 `test_inline_aoi_analysis_persists_context_and_cache` 卡住，
中斷 exit 130。相同單項在 sandbox 外 exit 0、1 passed。第一次外部 full gate 發現獨立
Manifest 放在 `manifests/` 會觸發共用 Terrain data gate；改用 `records/` 並加入迴歸測試後，
才得到上述完整 PASS，未修改／忽略共用 gate 或 Contract。

## Geology 額外檢查

```bash
UV_CACHE_DIR=/tmp/geology-uv-cache uv run --offline --frozen \
  --with-requirements pipelines/geology/requirements-ingest.txt \
  pytest pipelines/tests/test_geology.py pipelines/tests/test_geology_spatial.py -o addopts='' -q
```

exit **0**，實際輸出：`51 passed in 2.36s`。其中 46 項 core 可在專案既有環境執行；
5 項向量測試使用獨立 GIS 環境。共用環境缺少選配 GIS 套件時，該向量測試 module 會
明確 skip；最終上述隔離環境已實際跑完五項。

涵蓋：五種 mineral states、null／NaN／inf、band depth 非 wt%、uncertainty、quality masks、
不相容的觀測語義、礦物不可單獨推成地質作用、廣義火山岩不推成 basalt、planetary CRS、
fingerprint／重複 ID／改區域、rings／180° seam、holes、來源 polygon 重疊的 union coverage、
無效來源幾何、無覆蓋、離線可攜性、path traversal、hash tampering、Snapshot schema，以及
避免獨立 Geology records 被誤認為 Terrain manifests。

## 真實來源與重現

實際執行過 `audit`，接著 `analyze`，以外部資料目錄保存完整 Moon／Jezero GIS、月球
MAT／RGB 與一幅 CRISM summary／DP information。`analyze --offline --refresh` 重新計算
套疊與來源格式檢查；每個 provider 的狀態獨立保留。詳細結果見 README、coverage audit、
manifest 與 pinned source inspections，沒有以 synthetic fixture 填補觀測。

在改存 `records/` 前另做過完整 cached replay assertion，Snapshot ID 保持
`geo-snap-2c198bda0cedf8d4`，印出 `DETERMINISTIC_REPLAY_PASS`。該試跑產物保留於外部
`prototype-layout-backup/`。目前正式獨立路徑的 Snapshot 為
`geo-snap-d1235d5f9defe6cd`；其內容 hash、schema、搬移後驗證及 tampering detection
均由最終 tests 與 CLI 驗證。

```bash
uv run --frozen python -m analog_pipelines.geology verify
```

exit **0**：

```text
PASS geo-snap-d1235d5f9defe6cd 8 pinned files; manifest validated; no source downloads
```

CLI `match --categories igneous impact sedimentary aqueous_alteration --rocks basalt`
亦 exit 0；五個 Earth 候選均回傳 unknown／provider status，沒有虛構 Earth geology matches。

## 礦物光譜證據 pilot（Issue #42，2026-11-15）

新增 `mineral_evidence.py`、`scripts/build_mineral_evidence.py`、
`pipelines/tests/test_mineral_evidence.py`，以及由 pinned snapshot、typed-v2 export 與
coverage audit 重建的 `pipelines/geology/mineral-evidence.json` / `MINERAL_EVIDENCE.md`。
沒有重新下載來源、沒有修改既有 pinned snapshot、typed export 或 Terrain observation。

實際執行（本 worktree，皆離線）：

```bash
uv run --frozen pytest pipelines/tests/test_mineral_evidence.py -o addopts='' -q
# 16 passed
uv run --frozen pytest pipelines/tests/test_geology.py -o addopts='' -q
# 46 passed
uv run --frozen pytest pipelines/tests/test_geology.py pipelines/tests/test_geology_spatial.py \
  pipelines/tests/test_mineral_evidence.py -o addopts='' -q
# 62 passed, 1 skipped（共享環境缺少選配 GIS 套件，與既有行為相同）
uv run --frozen pytest apps/api/tests/test_typed_api.py -o addopts='' -q
# 7 passed
uv run ruff check .
# All checks passed!
uv run ruff format --check .
# 226 files already formatted
```

報告的實際計數：8 個 AOI、21 筆 analysis、56 筆 typed-v2 observation；礦物 observation 10 筆
全部 `unknown`，detected／numeric／abundance 皆 0；CRISM 7 個參數、14 個 context 數值；
EMIT CMR metadata 候選 V002 50／V001 88，下載 granule 0；M3 granule 0。EMIT auth、M3
registration、CRISM thresholds 等 blocker 逐條列於 `MINERAL_EVIDENCE.md`。

依 Issue 指示未執行完整 `scripts/verify.sh`；完整 gate 由整合端執行。

## 容量與隔離

目前 Snapshot 固定內容合計 **195,541 bytes**（約 191 KiB），最大單檔 **135,233 bytes**。
地質資料、Schema、Audit 與文件合計約 0.27 MB（不含本 validation 文件及後續文件文字變動）。
外部原始資料／解壓／快取約 **1.6 GiB**。主要已下載原檔：

| 檔案 | bytes |
|---|---:|
| Moon GIS v2 ZIP | 224,413,040 |
| SIM3464 GIS ZIP | 349,336,873 |
| SIM3464 map PDF | 36,406,378 |
| Lunar v4 MAT | 63,028,597 |
| Lunar v3 Oli RGB TIFF | 14,640,014 |
| 選取的 CRISM refined summary IMG | 82,009,200 |
| 對應 DP information IMG | 12,301,380 |

`git diff --exit-code` 及針對 API／web／contracts／configs／catalog／shared Snapshot／
pyproject／uv.lock／scripts 的 `git diff --exit-code` 均 exit **0**。
所有變更都是本次新增的 geology package、`pipelines/geology/` 與兩個 geology test files。
不含 raw raster、SQLite、cache、.env、token 或憑證。

## Area-fraction／evidence pilot（2026-11-15，#41）

本次只新增 D02 area-fraction pilot，不改共用 Snapshot、API、前端、catalog、contracts
或既有 terrain observations。實際執行：

```bash
uv run --frozen python -m analog_pipelines.geology.area_fraction --report-only
uv run --frozen python -m analog_pipelines.geology.area_fraction
```

`--report-only` 先驗證 registry 與 source record 並印出 summary；正式輸出寫入
`pipelines/geology/area-fraction/evidence.json`、`evidence.jsonl`、`manifest.json`。
輸出 29 筆 record、21 筆 area summary；bodies 為 Earth／Mars／Moon。`evidence.json`
128 KB、`evidence.jsonl` 88 KB，全部為可攜 JSON，不含 raw geometry 或 cache。

```bash
uv run --frozen pytest pipelines/tests/test_geology.py \
  pipelines/tests/test_geology_area_fraction.py -o addopts='' -q
```

實際輸出：`64 passed`（共用環境；GIS 模組 skip）。

```bash
uv run --frozen --with-requirements pipelines/geology/requirements-ingest.txt \
  pytest pipelines/tests/test_geology.py pipelines/tests/test_geology_spatial.py \
  pipelines/tests/test_geology_area_fraction.py \
  pipelines/tests/test_geology_area_fraction_spatial.py -o addopts='' -q
```

實際輸出：`76 passed`（含 five 舊 vector tests 與 seven 新 area-fraction spatial tests）。

```bash
uv run --frozen ruff check .
uv run --frozen ruff format --check .
```

實際輸出：`All checks passed!`、`228 files already formatted`。

覆蓋範圍：registry 版本／非互斥、交叉天體 code 不等價、unknown code 明示、
aqueous alteration 不得由 iron oxide 推論、nearest-neighbour 類別不內插、nodata 與
class `0` 區分、all-nodata=`no_coverage`（fraction 為 null）、fraction/area 加成、
`no_coverage`／`license_unverified` 不得 0 填、同 AOI 重疊不重複計 coverage、
validity 預設排除與 opt-in repair、planetary native CRS 與 Earth WGS84 拒絕、
deterministic 重跑，以及 similarity／combined score 欄位拒絕。

GLiM 授權查核結果與替代方案見 `pipelines/geology/area-fraction/SOURCE_VERIFICATION.md`
與 `earth-glim-license.json`：polygon GIS 為 `license_unverified`，不提交幾何或逐單元數值。

