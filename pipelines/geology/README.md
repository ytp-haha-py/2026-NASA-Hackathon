# Dimension 02 — Geology & Mineralogy

獨立、可離線讀取的研究模組。程式在 `pipelines/src/analog_pipelines/geology/`，不匯入
Terrain scorer 或共用 Feature Registry。原始工作保存在 `feat/16-geology-mineralogy`，
交付分支為 `feat/41-geology-handoff`（以最新 main 為基底），沒有修改
共用 Catalog、Contract、Area Config、SQLite、API、前端、dependency lockfile 或 Terrain Snapshot。

## 本次真實資料結果

2026-11-14 的 bounded audit／分析結果如下。數量只代表列出的局部 AOI。

| 天體 | AOI | 地質圖處理成功 | 可分享的地質圖成果 | 有可靠礦物觀測的 AOI |
|---|---:|---:|---:|---:|
| Earth | 5 個既有 AOI | 5（GLiM 本機套疊） | 0（鏡像授權未確認，分析值留在外部快取） | 0 / 5 |
| Moon | 1 個既有 Shackleton ROI | 1 | 1 | 0 / 1 |
| Mars | 2 個明確標記的 Jezero **測試 AOI** | 2 | 2 | 0 / 2 |

最後一欄是「可驗證觀測的可用率」，不表示礦物含量為零或礦物不存在。各筆
`mineralogy.valid_fraction` 在沒有有效觀測時保留 `null`。Mars 尚未成為共用系統的正式 reference。

Moon ROI 的官方地圖單元為 `Ec`（Eratosthenian Crater），幾何套疊覆蓋約 100%；原圖比例尺
1:5,000,000，不能從小數位推斷定位或組成精度。原圖約 3 km 的頂點間距、約 16 km 的平滑容差
遠大於本次 1.98 km ROI，且 MOON_ME DE421 與 Moon 2000/LOLA 在精細尺度的對準未驗證。
完整 source index 有 12,247 個多邊形，其中 1 個無效幾何被排除並記錄。

Mars 使用官方 1:75,000 GIS，1304 個多邊形中 2 個無效幾何被排除。兩個測試 AOI 分別為
77.35–77.38 E／18.47–18.50 N、77.40–77.44 E／18.42–18.46 N；原始單元 symbol、名稱、
官方 map-sheet 描述及面積重疊比例均保留，包含 `Nue`、`Aeb`、`NHjf2`、`cr`、`Njf`、`Nle`。
所有天體保留其原生比例尺／像元，沒有重採樣至 90 m。

| 研究分類 | 實際支援 |
|---|---|
| Igneous | GLiM 本機資料有 broad igneous evidence；分享版尚未啟用。`vb` 僅映射為 basic volcanic rocks，絕不推成 basalt。 |
| Impact | Moon `Ec`、Mars delta 測試範圍中的 `cr` 有地圖證據；沒有宣稱已辨識 impact breccia、melt 或 shock minerals。 |
| Sedimentary | Mars `NHjf2` 官方 deltaic/lacustrine 解釋支持 sedimentary／fluvial／lacustrine deposits；保留原始地質描述。 |
| Aqueous Alteration | 尚未啟用；礦物存在或 iron oxide index 本身不證明水蝕變成因。 |

這四類非互斥。匹配結果只顯示證據支持的請求數、實際特徵、證據、重疊／有效比例及限制，
不給 Terrain Euclidean distance 或「物理相似百分比」。沒有證據的請求是 unknown。

## 各來源查核與限制

- **GLiM**：指定 ArcGIS layer `1` 可用，鏡像描述為 v1.1；原生 EPSG:3857，平均來源比例尺約
  1:3,750,000，各區域來源尺度不一。五個既有 Earth AOI 都完成 bounded polygon query 與面積
  套疊。鏡像 item 的 `licenseInfo` 為空，且上游等同性未驗證，因此分享 manifest 為
  `unverified`；`glim-local-<area-id>.json` 與 GeoJSON 僅留在外部資料路徑。
  [PANGAEA.788537](https://doi.pangaea.de/10.1594/PANGAEA.788537) 的 CC BY 3.0 是另一份
  **0.5° gridded** 產品，不將該授權或解析度套到此 polygon mirror。
- **EMIT**：由 [CMR collections](https://cmr.earthdata.nasa.gov/search/collections.json?short_name=EMITL2BMIN&page_size=30)
  驗證正式 V002（`C4079846859-LPCLOUD`），並獨立查核 V001（`C2408034484-LPCLOUD`）歷史覆蓋。
  Atacama、Death Valley、Lanzarote、Rio Tinto 的 V002 bbox 候選數分別為 16、20、4、10；
  V001 為 26、29、5、28；Devon Island 兩版均為 0。只下載 metadata sample，未下載有效
  granules。依使用者選擇，保留 `auth_required`，不要求帳密、不讀取或記錄憑證。
  尚未交付經真實 granule 驗證的 NetCDF ingestion；`summarize_identification` 僅是已測試的
  原生像元／品質／uncertainty 聚合核心，不能當成完整 EMIT adapter。
- **Moon geology**：完整下載 USGS GIS v2。此 GDAL build 無法開啟套件中的 compressed
  FileGDB，改用**同一完整套件附帶的 GeoUnits shapefile**及官方 DOMU CSV，建立 STRtree。
  [官方 metadata](https://astrogeology.usgs.gov/search/map/Moon/Geology/Unified_Geologic_Map_of_the_Moon_GIS_v2/)
  標示 CC0、edition 2.0，保留作者引用。
- **Lunar polar minerals**：Zenodo [15549419](https://zenodo.org/records/15549419) 的 TIFF 是
  uint8 RGB；[20725024](https://zenodo.org/records/20725024) v4 是 MATLAB v7.3/HDF5，
  `AbcubeNew` 在 HDF5 中為 `(5,2201,2201)`，對應 MATLAB `(2201,2201,5)`，記錄的順序是
  Oli、Plag、Opx、Cpx、SmFe。API 確認 CC BY 4.0，且 v4 明確引用
  [Du et al., doi:10.1029/2024JE008842](https://doi.org/10.1029/2024JE008842)。論文 abstract
  為南極 83–90 S／約 193 m 的研究衍生產品；全文在本次環境回傳 HTTP 403。
  MAT 沒有內嵌 CRS、affine、單位、有效域、QA 或 uncertainty；數值也含負值、>100、非有限值。
  因此不假定單位、不借用不同版本 RGB 的座標來抽取、不把色彩當含量，也不硬剪裁成 0–100。
  原檔 MD5 均已核對，格式／範圍檢查存於 pinned `source-inspections` artifact。
- **Mars geology**：完整下載 [USGS SIM3464](https://pubs.usgs.gov/publication/sim3464) GIS，
  使用 `Nili.gdb/GeoUnits_v1`。XML 中的單元 description 為空，故用官方 map PDF 補足正式描述，
  不自行填寫岩性。USGS-authored GIS 為 public domain；map pamphlet 的第三方素材例外仍保留。
- **CRISM**：bounded ODE 查到 4 幅候選 MTRDR；選取其中較小的
  `hrl000040ff_07_if183j_mtr3` 對應 refined summary 與 DP information，而非完整光譜 cube。
  60-band float32 summary 為 36 m，原生 nodata=65535，保留 custom Mars sphere CRS。
  兩個測試 AOI 的七個 summary parameters 都有 100% **finite/non-fill 像元覆蓋**；這不是
  通過礦物辨識 QA 的覆蓋率。OLINDEX3、LCPINDEX2、HCPINDEX2、D2300、BD1900_2、SINDEX2、
  CINDEX2 的 AOI median 僅列於 `geological_context`，不解讀 DP mask 未文件化的 threshold，
  不啟用礦物偵測、不換算 wt%。
  [PDS Geosciences 的資料使用說明](https://pds-geosciences.wustl.edu/documents/ViewingPDSImagesAndExportingToOtherFormats.pdf)
  說明 PDS archived datasets 為 public domain。
- **M³ 備選**：沒有下載全球原始反射光譜。研究衍生數值缺少 registration／units 的問題尚未
  解決；回退至全球原始光譜不會自動產生可靠 mineral abundance，故本次不啟用。

精確來源 URL、版本、存取時間、CRS、map scale／native resolution、checksum、處理方法、
quality flags 與 limitations 由 manifest 的 area、dataset、analysis 三層以驗證過的 ID 連結。

## 礦物光譜證據 pilot（Issue #42）

`pipelines/geology/mineral-evidence.json` 與 `MINERAL_EVIDENCE.md` 是可從 pinned snapshot、
typed-v2 export 與 coverage audit 離線重建的 D02 證據報告，不新增任何礦物偵測或 abundance。
三個官方來源與存取狀態如下，完整 product ID、DOI、授權、URL 與 blocker 見報告：

| 天體 | 來源 | 官方產品 | 存取 | 本次狀態 |
|---|---|---|---|---|
| Earth | NASA JPL / LP DAAC EMIT | `EMITL2BMIN` V002（CMR `C4079846859-LPCLOUD`）與 V001（`C2408034484-LPCLOUD`） | `auth_required` | 只查匿名 CMR metadata；未下載 granule |
| Moon | NASA PDS / Chandrayaan-1 M3 | L2 reflectance `CH1-ORB-L-M3-4-L2-REFLECTANCE-V1.0`（PDS4 `urn:nasa:pds:ch1_m3:data::1.0`，DOI `10.17189/f8xf-6a29`） | `open` | 已定位官方 archive；未下載、registration 未驗證 |
| Mars | NASA PDS Geosciences / MRO CRISM | MTRDR `hrl000040ff_07_if183j_mtr3`（MTR3） | `open` | 只讀 refined summary 與 DP information；spectral parameter 僅 context |

實際計數（pinned `geo-snap-d1235d5f9defe6cd`）：8 個 AOI、21 筆 analysis、56 筆 typed-v2
observation；礦物 observation 10 筆且全部 `unknown`，detected 0、numeric 0、abundance 0；
CRISM 7 個 spectral parameter、14 個 context 數值，全部是 `dimensionless_spectral_parameter`；
EMIT CMR metadata 候選 V002 50、V001 88，下載 granule 0；M3 granule 0。這些數字只代表列出的
bounded AOI，不是全球涵蓋率。

五種 state 的語意由 `mineral_evidence.py` 與測試固定：`detected` 可帶 band depth；
`not_detected_under_valid_observation` 是 categorical，typed-v2 映射為 `not_detected`，兩者都
不是 0；`no_coverage`、`unknown`、`not_applicable` 各自保留 null，分別代表無足跡、有足跡但
無有效觀測、超出產品適用範圍。`summarize_identification` 只回傳 spectral band depth，沒有
abundance 欄位或 wt% 轉換。

```bash
uv run python scripts/build_mineral_evidence.py
uv run pytest pipelines/tests/test_mineral_evidence.py -o addopts='' -q
```

builder 會先驗證 pinned snapshot 與 typed observations checksum，無網路存取，只寫上述兩個檔案；
不修改既有 snapshot、typed export 或 Terrain observation。

## Area-fraction／process evidence pilot（#41）

`pipelines/geology/area-fraction/` 是獨立的 bounded pilot，只使用已驗證的 Moon
Unified Geologic Map v2 與 Mars SIM3464 Jezero/Nili 圖幅，以及已提交的 AOI 與
source-derived overlay record。它**不**新增地質相似度、排名或六維合併分數。

- `registry.json`：`geology-legend-registry-1.0.0`，非互斥的 raw code → Igneous／
  Impact／Sedimentary／Aqueous-alteration process evidence 對照。Mapping 以 dataset
  為界，Moon `Ec` 與 Mars `cr` 不會互相等價；未審查的 code 保持 `unmapped_code`。
  `aqueous_alteration` 必須有正面陳述的 alteration 證據，iron oxide／spectral
  index 本身不合格。
- `evidence.json` / `evidence.jsonl` / `manifest.json`：每個單元的原始 code、
  fraction、area、source product/version/license、map scale、native/analysis CRS、
  uncertainty／QA 與非互斥 process labels。`no_coverage`／`unknown` 的 fraction 與
  area 為 `null`，不是 0。
- `earth-glim-license.json`：GLiM polygon GIS 的授權查核結果為
  `license_unverified`，不得提交或發布幾何與逐單元數值。
- `../gsc-world/`：已實際匯入 Geological Survey of Canada Open File 5529
  （Open Government Licence – Canada 2.0）的 bounded 5×5 WMS point-grid pilot。
  Atacama 為 sedimentary、Lanzarote 為 igneous，原始 GSC 屬性與 fraction
  已提交；比例尺約 1:35,000,000，因此這是點取樣 fraction，不是 polygon
  intersection，也沒有 abundance／aqueous alteration／similarity。
- `SOURCE_VERIFICATION.md`：來源版本／授權、實測 fraction、方法與 blocker。

Area 量測使用各天體正確 radius 的 equal-area LAEA；class raster 僅允許
nearest-neighbour（超出來源範圍必須明示 nodata）；invalid geometry 預設排除並計數，
`make_valid` 修復須明確開啟。實際結果與可重現指令見該目錄 README 與
`SOURCE_VERIFICATION.md`。

## Clone 後直接離線讀取

只需要原專案依賴，不必安裝 GIS 額外套件、不必重新下載原始資料，也不必登入 Earthdata。

```bash
uv sync --frozen
uv run --frozen python -m analog_pipelines.geology verify
uv run --frozen python -m analog_pipelines.geology report
uv run --frozen python -m analog_pipelines.geology match --categories igneous impact sedimentary aqueous_alteration --rocks basalt
uv run --frozen pytest pipelines/tests/test_geology.py -o addopts='' -q
```

分享版的五個 Earth 候選目前全部缺少可分享的地質證據，`match` 會誠實列出 unknown 與 provider
status，沒有可宣稱的 Earth analog 排名。`compare_minerals` 只有在 mineral、measurement、unit、
method、semantics_id 都相容且兩邊有有效觀測時才比較；本次實際資料尚沒有這樣的成對觀測。

## 重新執行 Pipeline

設定 repository **外部**的資料路徑；程式會拒絕把原始來源放在 repository 裡。
可設定任意磁碟路徑，不受特定分區限制。

```bash
export GEOLOGY_DATA_ROOT=/absolute/path/outside/repository/geology

# 同步現有場域；附加明確標記的 Mars 測試 AOI，完全不修改 configs/areas.yaml
uv run --frozen python -m analog_pipelines.geology export-areas --with-test-aois --output pipelines/geology/areas.json

# 有限 metadata audit，接著執行地質圖 -> mineral/context -> snapshot
uv run --frozen --with-requirements pipelines/geology/requirements-ingest.txt python -m analog_pipelines.geology audit
uv run --frozen --with-requirements pipelines/geology/requirements-ingest.txt python -m analog_pipelines.geology analyze

# 快取齊全時不連網；保留成功與不可用紀錄，避免反覆下載
uv run --frozen --with-requirements pipelines/geology/requirements-ingest.txt python -m analog_pipelines.geology analyze --offline

# 只重試不可用的分析；既有 URL metadata cache 不會自動 refresh
uv run --frozen --with-requirements pipelines/geology/requirements-ingest.txt python -m analog_pipelines.geology analyze --retry-unavailable
```

GIS ingestion 另需系統 `curl`、`pdftotext`。額外 Python 套件精確列於獨立
`requirements-ingest.txt`，由 `uv --with-requirements` 使用隔離環境，未修改共用 pyproject／lockfile。
下載使用 timeout、兩次 retry、`.part` 續傳；來源錯誤獨立記錄，其他 provider 繼續。
`--offline` 不會要求下載；`--refresh` 重新計算 provider 分析但仍重用既有原檔。
遠端 metadata 或 source 更新應使用**新的外部 cache 目錄**做新 audit，保留舊版本供重現。
沒有刪除 cache 的指令，沒有 R2 publish／production pointer 寫入功能。

### 新 AOI 與第一維度後續場域

```bash
uv run --frozen --with-requirements pipelines/geology/requirements-ingest.txt python -m analog_pipelines.geology analyze \
  --areas /absolute/path/new-areas.json \
  --output-dir /absolute/path/geology-run-output
uv run --frozen python -m analog_pipelines.geology verify --root /absolute/path/geology-run-output
```

外部 JSON 格式為 `{"areas": [...]}`，每個 Area 符合 `schemas/area.schema.json`：
`area_id, display_name, body, crs, geometry, origin, geometry_source`；geometry 是 Polygon 或
MultiPolygon。`geometry_fingerprint` 可省略，由程式計算。使用 x/y 軸序，地理座標為
east-positive −180..180 longitude、planetocentric Moon/Mars latitude；跨 180° 的多邊形需先拆分。
Earth 可用 EPSG:4326；Moon/Mars 必須提供正確天體的 CRS／radius。Mars ODE adapter 目前只
接受 0..180 E 的已拆分 AOI，符合 SIM3464 地區。

程式驗證天體與 ellipsoid、關閉的有限座標 rings、geometry fingerprint、重複 ID、原有 ID
是否換了區域；GIS 步驟另驗證拓樸。fingerprint 忽略 ring 起點與方向，不做座標四捨五入。
不同 CRS 表示法／不同投影的等價 footprint 不保證有同一 fingerprint，整合時應先選定 canonical
geometry／CRS。非地球 CRS 不能以 Earth WGS84 暫代。

## 資料可攜性與檔案

- `catalog.json`, `taxonomy.json`, `areas.json`, `schemas/`：獨立來源與 AOI／資料規格。
- `coverage-audit.json`：provider、授權、metadata 覆蓋、下載量預估、AOI identities。
- `records/`：content-addressed Geology manifests；使用此路徑避免被共用 Terrain
  `*/manifests/*.json` data gate 誤認為 Terrain contract，獨立 schema 由 geology tests 驗證。
  沒有 raw raster、cache 或 SQLite。
- `artifacts/`：Snapshot 固定的 Catalog、Area、Schema、Taxonomy、Audit、Summary、來源檢查。
- `snapshots/geology.snapshot.json`：此維度目前的本機入口；同目錄 `geo-snap-*.json` 為不可變
  版本紀錄。改用獨立 `records/` 格式之前的試跑產物保存於外部 cache 的
  `prototype-layout-backup/`，沒有刪除或發布到 Git／R2。
- `INTEGRATION_PLAN.md`：跨維度整合所需修改；`VALIDATION.md`：實際命令與測試證據。

目前原始資料／解壓與本機結果約 1.6 GiB，全部位於執行時指定的 repository 外部目錄；
來源檔大小由 manifest artifact receipts 記錄。分享資料為 JSON，無需重建資料庫。
Snapshot ID 根據排序後的檔案 hash、size、relative path 產生，搬移路徑後仍可驗證；
重用相同來源與 checkpoint 的分析不會因生成時間改變 ID。

若稍後決定用 R2，只規劃 `geology/snapshots/geo-snap-<content-hash>/`。
不使用 `snapshots/snap-0cef4d75b147866a/`，不改任何 production pointer。本次未上傳。

## GitHub 提交範圍與指令

只提交以下新增目錄／檔案；不提交外部 raw/cache、`.env`、憑證或可重建的資料庫。
原始實作已提交保存；交付分支另含最新 main 的相容性檢查及 OpenCode 交接文件。
本次沒有 merge 或操作 R2。完整交接見 `docs/OPENCODE_HANDOFF.md`。

```bash
git branch --show-current  # 交付工作目錄應為 feat/41-geology-handoff
git add pipelines/src/analog_pipelines/geology pipelines/geology pipelines/tests/test_geology.py pipelines/tests/test_geology_spatial.py
git diff --cached --stat
git commit -m "feat(geology): add independent evidence-based geology dimension"
git push -u origin feat/41-geology-handoff
```

推送後建立 PR，交由其他 reviewer，通過 required CI 再由 maintainer 決定 merge；
不要自行 merge 或 self-approve。
