# Dimension 02 Integration Plan

## 隔離界線

本分支僅新增 geology package、獨立資料目錄及 `pipelines/tests/test_geology*.py`。
共用 `features.py`、scoring、Catalog、Contract、Area Config、SQLite、API、前端及 lockfile
保持不變。第一維度 `feat/15-terrain-landforms` 及 R2
`snapshots/snap-0cef4d75b147866a/` 都不作寫入。本計畫不是已經完成的跨維度整合。

## 匯入第一維度新增 AOI

1. 待第一維度交付 stable Area ID、**實際 geometry**、body、原生 CRS、geometry_source，
   轉成本模組 `Area` JSON。不要只以場域名稱或中心點 join；不要將同 ID 指向另一個區域。
2. 使用 `load_areas`／`selected_areas` 驗證 fingerprint。現有 Earth AOI 直接沿用 manifest
   bbox；Shackleton 沿用 manifest 中的 source x/y centre 與 window size，不能以 WGS84 bbox
   代替。Moon/Mars 的 geodetic／planetocentric 定義及參考框架需一併移交。
3. 呼叫 `analyze --areas <external.json> --data-root <external-cache> --output-dir <new-output>`。
   先 audit，再依序處理 maps／mineral context；個別 provider 失敗不中止其他 AOI。
4. 比對 `(Area ID, body, geometry_fingerprint)` 後才可以附加 geology observations。
   若 canonical CRS／投影表示改變，先審查 footprint 的等價性，產生明確 mapping record，
   不直接忽略 fingerprint mismatch。本 fingerprint 不宣稱跨 CRS／跨 frame 等價。
5. 用新 snapshot pin 結果，驗證可搬移與離線讀取，再送資料與程式 PR review。
   不修改 Terrain 的舊 Snapshot 或把 Mars `test_aoi` 自動升為正式 reference。

## 後續共用系統修改需求

現行 `scripts/validate_data.sh` 以 `*/manifests/*.json` 尋找並強制套用 Terrain contract。
因此此模組的獨立 Manifest 存於 `pipelines/geology/records/`，其 JSON schema、snapshot hash
與語義條件由 geology tests 在現有 pytest gate 中驗證。將來若統一 data gate，需另案設計
以 schema/dimension dispatch 的驗證，不得現在把 geology 偽裝成 Terrain manifest。

| 元件 | 待整合修改 | 必須保留的科學條件 |
|---|---|---|
| Feature Registry | 新增 geology/mineral/context 的型別、角色、單位、觀測語義及明確的 matching method | 非 numeric 的地質類別不進 Terrain Euclidean；未驗證礦物不能啟用 |
| Catalog | 將獨立 dataset IDs、版本、license、CRS、scale、provider availability 匯入來源矩陣 | GLiM polygon rights 仍 `not_verified`，不借用 0.5° grid 授權 |
| Area Config | 將第一維度與本模組的 geometry/body/fingerprint 共同治理 | stable ID 不是任意 join key；不使用 Earth CRS 表示 Moon/Mars |
| Contract / OpenAPI | 設計 lithology、mineralogy、process evidence、context、provider status、uncertainty、coverage 的 versioned payload | 五種 mineral states 分開；`null` 不是 0；numeric semantics_id 必填 |
| Snapshot | 以 parent/child references 或獨立 dimensions 表連結 geology snapshot | 使用新的 aggregate Snapshot ID；保留每個 dimension 的 bytes/hash，不改舊 snapshot |
| SQLite | 新增 dataset-version、area-geometry、lithology overlaps、mineral observations、process evidence 關聯表及 native-scale metadata | 不將字串類別塞進原本 numeric terrain columns；重建索引仍由 pinned artifacts 產生 |
| Matching | 明確 dispatch category、rock/deposit、compatible mineral comparison、context | category request support count 不冒充 physical similarity；相同單位不足以證明可比較 |
| API | 新 dimension-specific request／response；以 snapshot scope 回傳實際比較項目、排除原因、證據與有效比例 | 缺資料回 unknown／provider 狀態；區分 metadata footprint 與 valid observations |
| Frontend | 可選非互斥研究需求，展示 source unit、比例尺、uncertainty、context-only 和 unknown states | 不把 band depth/index 呈現為礦物 wt%；不宣稱全球／ exhaustive Top N |
| CI / 依賴 | 若需在預設 CI 跑向量 ingestion tests，另案決定 GIS dependencies／lockfile 更新 | 目前純離線 core tests 在現有 gate 內；額外五項 GIS tests 用隔離依賴環境執行 |

## 來源解除限制的工作

- **GLiM**：確認指定鏡像與作者原始 polygons 的版本、各區域 scale、來源等同性和派生值分享權。
  取得可核實的權利文件後，另建 dataset version／catalog change，再重新處理、review。
  不手動把目前 manifest `unverified` 改成 success。
- **EMIT**：依本次使用者選擇，保持 auth_required。未來在本機配置 Earthdata 後，實作並用
  真實 V002 MIN／MINUNCERT／對應 quality product 驗證 NetCDF adapter：確認 granule identity、
  native geolocation 或官方 GLT、正確的一基 index／fill 值、quality mask、mineral library
  lookup、fit 和 band-depth uncertainty；完整 AOI 分母要包含 swath 外區域，不能只以有效點為分母。
  已有 `summarize_identification` 不代表以上 adapter 已完成。V001 不可悄悄填入 V002。
- **Lunar polar matrix**：取得作者明確 pixel registration、軸向／轉置規則、CRS／frame、單位、
  有效域、nodata、QA／uncertainty 與論文方法。不要以 RGB colors 反推出 abundance，或只因
  同為 2201×2201 就假定新版 MAT 的 grid 與舊版 RGB 一致。
- **CRISM**：保留現有 parameters 為 context；若要啟用 mineral identification，須使用可靠的
  reference/ratio spectra、品質判斷及明確方法驗證，保留 footprint／有效觀測範圍。
  同一礦物的 EMIT band depth、CRISM index、M3 model abundance 不能直接 numeric matching。
- **M³ 原始備選**：只有在研究問題、bounded footprint、演算法及資料驗證計畫已確立後才下載；
  本次沒有啟用或執行全球原始光譜下載。

## Promotion／R2

目前只產生本機、可透過 Git 分享的 `geo-snap-*`，不包含發布程式。
未來若要上傳，使用獨立 `geology/snapshots/<geo-snap-id>/`，manifest/artifact hash 驗證後
由 maintainer 執行 promotion。共用系統在批准整合前不指向這個 geology snapshot，且保留 rollback
至既有 parent snapshot 的路徑。不得覆寫第一維度 prefix，也不得將 synthetic tests 當觀測發布。
