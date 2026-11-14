# OpenCode 交接 — 2026-11-15（Asia/Taipei）

本交接僅收尾既有 Geology pilot、驗證及複查 PR #54。未下載新的科學資料、未操作 R2、
未設定 Tunnel、未部署、未修改 Issue／#54 分支／main，也未解除 change request 或 merge。
OpenCode desktop 接手真實資料 ingestion、六維整合、全球索引、R2、後端部署與 Tunnel。

## 1. 分支、PR 與保存點

| 用途 | 分支／版本 | 本機位置 |
|---|---|---|
| 原始 Geology，保持原工作位置 | `feat/16-geology-mineralogy`，保存 commit `7cdd9a8` | `/home/yingtung/2026-NASA-Hackathon` |
| Geology 交付，基於最新 main | `feat/41-geology-handoff`，保存 `8496025`／交接 `2affdba`，再以 `49615a1` 合入 main | `/tmp/opencode-geology` |
| 共用 main 基底 | 最終合入 `e2a50809ad358f2ba89901ea193e120720c07328`（#54 squash）；原始交付基底 `5d07072` | `origin/main`，沒有移動本機 main |
| PR #54 最終複查 | `feat/24-offline-backend-foundation`，`18951551b07322dc8e0836b0a3263f4182b1c34f` | `/tmp/opencode-pr54-latest`，detached，唯讀原始碼 |
| PR #54 歷史複查證據 | `5ab3b3ed54bcc0cb7b46e9af0e40ed2c10224ade` | `/tmp/opencode-pr54-review`，保留舊版對比，勿當最新 head |

- [PR #54](https://github.com/ytp-haha-py/2026-NASA-Hackathon/pull/54)：已由 maintainer `ytp-haha-py` 於 15:50:59 UTC 合併，最終 head 有該 reviewer 的 APPROVED；Codex 未執行此 merge／approval。本次複查見 [PR54_REVIEW.md](PR54_REVIEW.md)。
- **Geology [PR #57](https://github.com/ytp-haha-py/2026-NASA-Hackathon/pull/57)**：`feat/41-geology-handoff` 對 main，僅關聯 [#41](https://github.com/ytp-haha-py/2026-NASA-Hackathon/issues/41)／[#42](https://github.com/ytp-haha-py/2026-NASA-Hackathon/issues/42)，不宣告關閉兩個 Issue。
- 第一維度 `feat/15-terrain-landforms` 與 `snapshots/snap-0cef4d75b147866a/` 沒有改寫。
- 起始工作樹無 tracked diff，32 個 Geology 新檔全部保存；沒有 reset、clean 或捨棄原始內容。
- 原始備份 `/tmp/opencode-handoff-20261114T152017Z/original-geology.tar.gz`，SHA-256：
  `051572c5fe8f2d3da1f23dc83d2f5f1cfeee9e5b089540e47018b59d8148bd7a`。
  同目錄有逐檔 `original-sha256.json` 及原始空的 `original-tracked.diff`。

`/tmp` 不是長期備份。程式與小型成果以 Git commit／PR 為準；原始資料與備份請由 OpenCode
另存至外部持久磁碟，搬移前核對 checksum，不刪除現存副本。原分支 commit 已保存全部原始檔案，
交付分支只有文件與「讀取目前全部 Earth IDs」測試相容性調整，既有科學產物 bytes 未改。

## 2. 已完成的最小 milestone

- `pipelines/src/analog_pipelines/geology/`：獨立 typed records、Area fingerprint、來源狀態、
  地質 polygon 套疊、原始分類與非互斥 taxonomy、礦物觀測語義檢查、類別／礦物 matching、CLI、
  cache／retry／timeout／斷點續傳及 checksummed Snapshot。
- `pipelines/geology/`：獨立 Catalog、Schema、taxonomy、coverage audit、small processed records、
  provenance、source inspections、README 與 Integration Plan。資料本體位於 `records/`，
  **不要改名為 `manifests/`**，否則現有 Terrain gate 會誤套共用 v1 contract。
- `pipelines/tests/test_geology.py`、`test_geology_spatial.py`：離線科學狀態／語義與空間／可攜性測試。
- 分享版 Snapshot `geo-snap-d1235d5f9defe6cd`：8 個 pinned files，195,541 bytes，
  最大檔案 135,233 bytes。全部為小型 JSON；不需下載 GIS／raster 即可讀取與測試。
- 未修改共用 Feature Registry、Catalog、Contract、Area Config、SQLite、API、前端或 lockfile。
  PR #54 的同名概念是另一套新 v2 foundation，尚未匯入這個 Geology package。

| 天體 | 本次分析範圍 | 可分享地質成果 | 可靠礦物觀測 |
|---|---|---:|---:|
| Earth | 原有 5 AOI；GLiM 本機套疊成功 | 0，鏡像授權／等同性未確認，值留外部 cache | 0 / 5 |
| Moon | 原有 Shackleton rim ROI2 | 1，官方地圖單元 Ec | 0 / 1 |
| Mars | 2 個明確標記的 Jezero 測試 AOI | 2，官方單元及描述 | 0 / 2 |

Impact 與 Sedimentary 有可分享地圖證據；Igneous 僅有未啟用的 Earth 本機證據；
Aqueous Alteration 尚未啟用。這不是新增的全球 Earth analog 排名。
「零筆可驗證觀測」代表沒有可用的可靠測量，**不代表礦物不存在**；缺失值是 null，不能補零。

目前 main 另有 113 Earth candidates、18 Moon／5 Mars references、9 datasets、565 Earth
terrain observations 與 1,243 land-cover context observations。這些是共用 Terrain 子集，
不能與上述 Geology pilot 數量混用，也不是全球索引。

## 3. 環境與資料路徑

本次環境：Linux x86_64、Python 3.13.13、uv 0.11.7、Node v24.22.2、npm 11.19.1。
每個 worktree 使用自己的 `.venv`；不要把舊 worktree 的 editable install 當新分支環境。
軟體 cache：`/tmp/geology-uv-cache`、`/tmp/geology-npm-cache`。

- 已有原始／解壓／cache：`/tmp/geology-audit`，約 1.6 GiB。此輪未新增科學下載。
- Moon GIS：`/tmp/geology-audit/moon/Unified_Geologic_Map_of_the_Moon_GIS/`。
- Mars GIS：`/tmp/geology-audit/mars/sim3464_JezeroNili_FINAL/`。
- 其他 lunar MAT／RGB、CRISM summary／DP、GLiM 本機結果及 receipts 均在該外部資料根目錄，
  精確 URL／檔案 checksum 見 committed records 與 source-inspections；不要重抓已成功來源。
- 舊版試跑成果：`/tmp/geology-audit/prototype-layout-backup/`，保留作追溯，並非目前 Snapshot。
- ingestion 額外套件：`pipelines/geology/requirements-ingest.txt`，
  shapely 2.2.0、fiona 1.10.1、h5py 3.16.0；另需 `curl`、`pdftotext`。
- pipeline 只接受 repository 外的 raw/cache，使用 `GEOLOGY_DATA_ROOT` 或 `--data-root`，
  沒有硬編碼上述本機資料路徑。
- 共用 SQLite 預設 `./var/analog.db`；沒有讀取／重建／遷移使用者實際資料庫。
  **不得對含 user AOI／jobs 的 DB 執行 `--reset`／data-rebuild-index。**
- 本機有 Docker CLI；未檢查 daemon、未啟動容器。`cloudflared` 不在目前 PATH。
  未檢查或列印任何憑證值。

## 4. 驗證證據與離線命令

本輪已觀察到的結果（全部 exit 0）：

| 對象 | 命令／結果 |
|---|---|
| Geology + 合併後 main `e2a5080` | `bash scripts/verify.sh`：最終 `PASS - 14 steps in 88s (full gate)`，352 項 backend tests、70 項 frontend tests |
| PR #54 最終 `1895155` | 同一完整 gate：`PASS - 14 steps in 86s (full gate)`，306 項 backend tests、70 項 frontend tests |
| Geology + 舊 main `5d07072` | 先前 gate `PASS - 14 steps in 71s (full gate)`，298 backend／70 frontend；首次含 dependency 安裝為 407s |
| PR #54 中間版 `92c986a` | 同一完整 gate：`PASS - 14 steps in 102s (full gate)`，303 項 backend tests、70 項 frontend tests |
| PR #54 歷史 `5ab3b3e` | 同一完整 gate：`PASS - 14 steps in 468s (full gate)`，296 項 backend tests、70 項 frontend tests |
| PR #54 acceptance | `bash scripts/backend_acceptance.sh`：`RESULT: PASS (offline; no source download, no R2)` |
| Geology + GIS 額外套件 | 合入最新 main 後兩個 geology suites：`51 passed in 1.90s`；預設環境未安裝 GIS 的 module 另在此實際跑完 |
| Geology Snapshot | `PASS geo-snap-d1235d5f9defe6cd 8 pinned files; manifest validated; no source downloads` |
| PR #54 小型診斷 | `docs/review_pr54_probes.py` 對兩個 head 均 exit 0；分別保存於兩份帶版本的 probe results，見 review 文件 |

backend test counts 亦以 `pytest --collect-only -q -o addopts=''` 核對。Gate 預設兩次 quiet
不印數量摘要；以上沒有將可選 GIS skip 冒充已執行。API smoke 實際有 health／capabilities／
search／location／context HTTP 200、missing location HTTP 404，所有 assertions 通過。
共用 data gate 保持 snapshot `snap-0cef4d75b147866a`、data_version `dv-b46f3bad38ea`。
原分支收存前的 51 項亦 exit 0；歷史舊基底完整驗證另見 `pipelines/geology/VALIDATION.md`。

以下命令不下載科學資料；首次安裝可能需下載軟體 dependencies：

```bash
cd /tmp/opencode-geology
uv sync --frozen
uv run --frozen python -m analog_pipelines.geology verify
uv run --frozen python -m analog_pipelines.geology report
uv run --frozen python -m analog_pipelines.geology match --categories igneous impact sedimentary aqueous_alteration --rocks basalt
uv run --offline --frozen --with-requirements pipelines/geology/requirements-ingest.txt pytest pipelines/tests/test_geology.py pipelines/tests/test_geology_spatial.py -o addopts='' -q
UV_CACHE_DIR=/tmp/geology-uv-cache npm_config_cache=/tmp/geology-npm-cache bash scripts/verify.sh
```

本輪 logs：`/tmp/opencode-geology-verify.log`、`/tmp/opencode-pr54-verify.log`、
`/tmp/opencode-pr54-acceptance.log`、`/tmp/opencode-geology-focused.log`、
`/tmp/opencode-pr54-latest-verify.log`、`/tmp/opencode-pr54-latest-acceptance.log`、
`/tmp/opencode-geology-release-verify.log`（舊 main）、
`/tmp/opencode-geology-main-final-verify.log`（最終合入 main 的版本）、
`/tmp/opencode-pr54-merged-verify.log`、`/tmp/opencode-pr54-merged-acceptance.log`、
`/tmp/opencode-geology-main-focused.log`。
PR #54 診斷程式與輸出已一併提交到 docs；診斷使用小型 synthetic raster／in-memory
SQLite，只能證明軟體行為，不能證明真實來源可比性。

## 5. OpenCode 接手順序

1. 先 `git status --short --branch`、`git worktree list`，讀本交接與 #54 複查。
   保留原分支保存點與外部 cache；不要把兩個 PR 的檔案混在同一提交。
2. #54 已由原 reviewer 批准並合併；勿等待另一輪 merge。剩餘 bbox CRS／資訊保留與 grid
   truthful coverage 問題請另開小型 fix PR，在真實 ingestion 前完成，見 review 文件。
   Codex 本次沒有 dismiss、resolve review threads、approve 或 merge #54。
3. 審查獨立 Geology PR：來源與科學限制明確保留，#41／#42 仍 open，避免把 pilot 當完整驗收。
   本交付已將 #54 合併後的 main 合入自己的 Geology 分支並重跑 gate；勿重做此步，
   也勿直接覆寫／重新產生 pinned records。
4. 最新 #54 已實作投影 support、物理 grid identity、共享 partitions 與 SQLite 保護，勿重做。
   先完成 review 所列剩餘 bbox CRS／資訊保留與 truthful coverage 修正；真實 ingestion 前
   固定方法版本與科學可比性 gate，部署前確認 SQLite 備份／復原與既有 typed schema 相容。
5. 依 `pipelines/geology/INTEGRATION_PLAN.md` 建立 Geology→v2 的明確轉換。
   保留 geometry fingerprint、map scale、分類 evidence、單位、QA 與 source status；
   matching 不接 Terrain Euclidean scorer，不把不可比資料變成綜合分數。
6. 先小 AOI 真實驗證再擴大。補 EMIT 登入後的 QA/geolocation adapter、lunar 數值 registration／
   units／有效域，或維持 blocked；CRISM 無可靠辨識方法時繼續 context only。
7. 再做真實全球索引、既有 shared Parquet 的 R2 發布／retention 與 cache／query strategy，
   對應 #28–#39、#47–#49；不因 #54 合併就宣稱已完成真實全球 ingestion。
8. 最後部署 backend 與 Tunnel，驗證健康檢查、CORS、持久化 SQLite、job recovery、
   secrets isolation、備份與 rollback。部署操作由 OpenCode 執行。

新增 AOI：目前 importer 可唯讀匯出 main 全部 Earth AOI，但只特別解析既有 Shackleton ROI2。
其他 Moon／Mars reference 請由權威 geometry 匯出符合獨立 Area Schema 的外部清單；
不可僅從 display name／bbox 推測 planetary frame。原有 Area ID 改 geometry 會拒絕。

以下供 OpenCode **之後**在資料準備好時使用，本輪未執行 ingestion：

```bash
uv run --frozen python -m analog_pipelines.geology export-areas --with-test-aois --output /tmp/geology-areas.json
export GEOLOGY_DATA_ROOT=/absolute/path/outside/repository/geology
uv run --frozen --with-requirements pipelines/geology/requirements-ingest.txt python -m analog_pipelines.geology analyze --areas /absolute/path/areas.json --output-dir /absolute/path/geology-output --offline
uv run --frozen python -m analog_pipelines.geology verify --root /absolute/path/geology-output
```

`--offline` 只用既有 cache；缺資料會記錄不可用，不會自動補下載。新來源版本使用新的 cache
目錄。audit／analyze 是後續 ingestion 工作，先 review bounded AOI、來源與容量再執行。

## 6. 科學與資料限制

- Earthdata：使用者明確選擇保留 `auth_required`；不要再次要求在對話提供帳密。
  EMIT V002／V001 CMR granule bbox 候選只代表潛在覆蓋，不是有效像元或礦物偵測。
- GLiM polygon mirror 的 license／上游等同性不明，不能借用另一份 gridded 產品授權；
  不把 broad volcanic 轉成 basalt。
- Moon USGS map 1:5,000,000 是地質背景；1.98 km ROI 套疊比例不是精細組成精度。
  Moon frame alignment 未在精細尺度驗證。
- Mars Jezero map 1:75,000；兩個 Geology AOI 是 test AOI，尚未成為主系統 reference。
- Lunar MAT 的 CRS／affine／單位／有效域／QA 未解決；RGB 不能當 mineral abundance。
- CRISM 七項 summary medians 只作 context；finite/non-fill coverage 100% 不是可靠礦物覆蓋。
- 五種狀態須保留：detected、not detected under valid observation、no coverage、unknown、
  not applicable。缺資料／auth failure 不等於 not detected。
- 地質圖 unit code 跨天體不直接等價；process 必須有地質證據，iron oxides 不能獨自證明水蝕變。

## 7. Cloudflare 憑證與基礎設施（只列需求）

先讀 `docs/R2_SETUP.md`、`docs/DATA_GOVERNANCE.md`，不要照舊文件直接覆寫既有 prefix。
本輪未驗證線上 R2 狀態；舊文件的成功記錄不是此次執行證據。

- R2 account ID、bucket、endpoint `https://<ACCOUNT_ID>.r2.cloudflarestorage.com`。
- runtime／query 用 bucket-scoped read-only credentials；operator publish 另用受限 write credentials。
  現有 operator 設定名：`ANALOG_OBJECT_STORE_BACKEND`、`ANALOG_OBJECT_STORE_ENDPOINT`、
  `ANALOG_OBJECT_STORE_BUCKET`、`ANALOG_OBJECT_STORE_REGION`、`AWS_ACCESS_KEY_ID`、
  `AWS_SECRET_ACCESS_KEY`。可選 storage extra，不改寫共享 lockfile 來塞秘密。
- GitHub trusted publish workflow 對應 secrets：`R2_ENDPOINT`、`R2_BUCKET`、`R2_REGION`、
  `R2_ACCESS_KEY_ID`、`R2_SECRET_ACCESS_KEY`，另需 protected environment／maintainer 審核。
- Geology 暫只保留獨立規劃 prefix `geology/snapshots/geo-snap-<content-hash>/`；
  不上傳本輪成果、不操作 `current.json`／production pointer。
- Tunnel：Cloudflare account／zone 管理權、已託管的 hostname、named tunnel 與 DNS route；
  安裝 `cloudflared`、將 tunnel token／credentials JSON 置於 secret store 或受權限保護路徑，
  設定 origin `http://127.0.0.1:8000` 及 fallback，使用 systemd／容器服務保活。
  不把 account certificate／token／credentials JSON 加入 Git。
- backend 主機需 outbound 網路、TLS hostname、持久化 SQLite／job cache volume、
  CORS frontend origin、restart／health／logging／backup／rollback 設定。
  管理端與科學資料 publish credentials 不應提供給 React frontend。

## 8. 完整檔案清冊與遠端保存邊界

逐檔清冊：[geology-artifact-inventory.json](geology-artifact-inventory.json)，包括全部原始 Geology
source／records／processed artifacts 的 path、size、SHA-256，以及外部目錄 **543 files、
1,704,517,290 bytes**（約 1.59 GiB）的逐檔 path、size、SHA-256、用途與遠端狀態。
可攜成果的來源／版本／checksum／processing method 由 pinned records、Catalog 與 artifact
receipts 連結；Git 分支是這些已分享成果的 storage location。

外部每一檔均明確標成 `not_backed_up_by_this_handoff`：包括 raw ZIP／GIS 解壓檔／MAT／TIFF／
CRISM IMG、metadata cache、GLiM 授權未確認的 local-derived values、checkpoint、舊試跑副本。
它們沒有因為 commit source 而變成已備份。所有可分享且已驗證的本次地質套疊／context 成果
已在 Git 的 small records 中，沒有另留唯一的大型 scientific output。

因此本次沒有需額外 R2 發布的大型合格成果，也沒有嘗試 R2 認證／publish；不是把未成功的
upload 當成功。原始資料不因新授權就自動上傳，GLiM 授權不明的派生資料也不發布。
若 OpenCode 要保留原始資料的長期副本，先以清冊核對後複製到外部持久磁碟；若之後確認
有必要發布大型合法派生物，再用既有 publishing workflow 與獨立 immutable prefix。

```bash
# 唯讀盤點現有資料；不下載／刪除／上傳，不讀取 .env 或 credentials：
python pipelines/geology/inventory_local.py --data-root /tmp/geology-audit --output /tmp/geology-inventory.json
```

## 9. Git 操作與驗收邊界

交付已提交的檔案只有 Geology source／small records／schemas／tests／docs。
大型 raw/cache、`.env`、憑證、SQLite、node_modules、`.venv` 均不在 PR。

```bash
git worktree list
git -C /tmp/opencode-geology status --short --branch
git -C /tmp/opencode-geology diff origin/main...HEAD --stat
# 後續更新仍推同一功能分支，不推 main：
git -C /tmp/opencode-geology push -u origin feat/41-geology-handoff
```

其他成員只需 checkout 交付 PR／分支即可讀取既有 Geology 成果；不必取得 1.6 GiB 原始資料。
六維 production readiness、全域檢索、R2 partition reuse 與部署仍是 OpenCode 後續工作。

## 10. 從遠端取回成果

遠端位置是 [PR #57](https://github.com/ytp-haha-py/2026-NASA-Hackathon/pull/57) 的
`feat/41-geology-handoff` 分支；資料不只存在 `/tmp` worktree。
本輪收尾另由 GitHub API 讀回 branch／PR 與 Snapshot，核對遠端 Git tree 和本機已提交 blobs；
具體結果亦在交接完成回覆中報告。沒有聲稱任何 R2 objects 已上傳。

```bash
# 在已有 clone 中，選擇尚未存在的 worktree 路徑：
git fetch origin feat/41-geology-handoff
git worktree add --detach /tmp/geology-retrieval origin/feat/41-geology-handoff
cd /tmp/geology-retrieval
uv sync --frozen
uv run --frozen python -m analog_pipelines.geology verify
uv run --frozen python -m analog_pipelines.geology report
```

以上會取回程式、Schemas、manifests、全部可分享 processed observations／artifacts、Snapshot、
清冊、診斷程式／結果與本交接文件。只需安裝軟體 dependencies，不必從 NASA／USGS 重下 raw。
若要重跑原始 GIS 分析，再由 OpenCode 依清冊保留的 source／version／checksum／方法及 cache
處理；那是後續 ingestion 工作，不是讀取本次完成成果的必要條件。
