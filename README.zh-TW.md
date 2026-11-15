# Planetary Analog Explorer

[English](README.md) | **繁體中文**

以可追溯、可重現的公開地理資料，尋找**在局部地形形狀上接近指定目標**的地球類比區域（Planetary Analogs）。

專案參與 NASA Space Apps Challenge 2026 的 [Permanent Moon Base Locations and Mars](https://www.spaceappschallenge.org/2026/challenges/permanent-moon-base-locations-and-mars/) 題目。目前的最小可行產品（MVP）回答的是：

> 在**已建立索引的地球小區域**中，哪些區域的地形特徵最接近指定條件，或一個已驗證的月球地形參考區域？

這不是月球／火星基地選址系統，也不宣稱地球地點與其他星球在重力、溫度、地質或物理環境上相同。地圖底圖的世界涵蓋範圍**不代表搜尋索引涵蓋全球**。

## 公開 API 端點

Cloudflare Tunnel 公開 API 端點：

```text
https://api.貓熊.台灣
https://api.xn--2vxr09c.xn--kpry57d   # ASCII／punycode 形式
```

常用路徑：

```text
https://api.貓熊.台灣/api/v1/health
https://api.貓熊.台灣/api/v1/capabilities
https://api.貓熊.台灣/api/v1/typed/coverage
https://api.貓熊.台灣/docs
```

正式前端建置預設使用 `https://api.貓熊.台灣/api/v1`，可用 `VITE_API_BASE` 覆寫。本機開發仍是
`http://127.0.0.1:8000`，透過 Vite `/api` proxy 使用相對路徑 `/api/v1`。已於 2026-11-15 驗證：`api` health 與 typed coverage 回傳 HTTP 200，web routes 也回傳 HTTP 200。

公開 demo 使用 Docker（`api`、`web`、`cloudflared`），含 restart policy、healthcheck 與持久化
SQLite。一個指令啟動：

```bash
make public-up      # docker compose -f compose.public.yaml up -d --build
make public-smoke   # 公開 health/coverage/web 檢查
make public-down    # 停止
```

Tunnel 憑證與 routes 見 [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md)。

## 目前功能與資料

以下數量對應 Repository 已提交的 [固定資料快照](samples/snapshot/analog-demo.index-summary.json)，不是所有使用者執行時可能另外建立的 AOI 總數。

| 項目 | 目前狀態 |
| --- | --- |
| 地球地形候選 | 113 個真實分析視窗：保留原五處，加上十二個有限取樣範圍 |
| 月球參考 | 18 個真實 NASA LOLA 視窗，涵蓋六個站點並保留 Site04 ROI 2 |
| 地球地形資料 | Copernicus DEM GLO-90；從有限區域讀取高程 |
| 地球地表覆蓋 | ESA WorldCover 2021 v200；11 類比例，僅用於背景資訊／部分必要條件篩選 |
| 地形相似度 | 5 項特徵：平均坡度、坡度第 90 百分位、地形起伏、去趨勢粗糙度、平坦地形比例 |
| 火星參考 | 5 個 Jezero CTX 真實視窗，使用火星來源座標系統 |
| 搜尋 | 自訂目標數值與權重，或選擇已驗證的月球參考；僅排序已索引的地球區域 |
| 新地球 AOI | 支援矩形或 Polygon 送出分析工作、查詢狀態，並將有效結果加入索引 |
| 介面 | React、TypeScript、Leaflet 地圖、排名、詳細資料、資料來源和限制、JSON 證據匯出 |

**尚未完成：** 經驗證的大鄰域曲率與 Geomorphons 整合、全球候選搜尋、溫度／礦物／生物等額外維度的數值比較、基地適宜性推薦、AI 即時對話分析。詳見 [Roadmap](docs/ROADMAP.md)。

### 目前的分析方式

目前以五個地形指標比較目標與候選，採用**加權、固定尺度正規化的歐氏距離**。距離越小，代表在這些指標上越接近；**距離不是相似度百分比**。若資料缺失或處理方法版本不相容，系統會排除候選並回報原因，不會用虛構或補零的值計算。

月球 LOLA 參考區域會重採樣到共同的地形分析尺度，但來源約 90% 的 5 m 像素是插值結果；不能把 5 m 影像紋理視為獨立量測。地球的 WorldCover 是土地覆蓋分類，**不是月球岩性或礦物資料**。

## 系統架構

以下以純文字圖呈現目前實際運作方式；更完整的圖可見 [繁體中文 SVG](docs/diagrams/architecture.zh-TW.svg) 與 [繁體中文架構說明](docs/ARCHITECTURE.zh-TW.md)。

### 使用者搜尋與按需分析（執行時）

```text
使用者瀏覽器
    |
    v
React / TypeScript + Leaflet (apps/web)
    |
    | HTTP /api/v1
    v
FastAPI (apps/api)
    |
    +-- 搜尋 / 比較 (domain/search.py, scoring.py)
    |      |
    |      +-- 讀取已索引的候選地點與特徵 -----+
    |                                         |
    +-- 新增地球 AOI (POST /areas/analyze)     |
           |                                  |
           +-- SQLite: 建立 Job               |
           |                                  |
           +-- 程序內 ThreadPoolExecutor      |
                   |                          |
                   +-- Copernicus DEM         |
                   +-- ESA WorldCover         |
                   +-- 計算 / 驗證 / 儲存 ------+
                                              |
                                              v
                                  SQLite (可重建搜尋索引)
```

背景工作目前在 **API 的同一個程序內**執行，不是獨立部署的 Worker、Celery 或分散式佇列。提交新 AOI 時可取得 HTTP 202 與 Job ID；用 `GET /api/v1/jobs/{id}` 查詢進度。

### 資料匯入、驗證與版本化（離線）

```text
公開資料來源
  |-- Copernicus DEM (地球高程)
  |-- ESA WorldCover (地球地表覆蓋背景)
  +-- NASA LOLA (月球參考高程)
                 |
                 v
         pipelines/ 資料處理腳本
         區域讀取 -> 轉換 -> 品質檢查 -> 特徵萃取
                 |
                 v
         JSON Manifest (來源、方法、範圍、品質)
                 |
                 v
        固定 Snapshot + SHA-256 驗證
                 |
                 +------------------------+
                 |                        |
                 v                        v
        重建 SQLite 搜尋索引       選用 R2 / S3 快照儲存
                                          |
                                   團隊發布 / 取得

Git：程式、設定、Manifest、小型固定快照與文件
GitHub Actions：測試 / 資料驗證；通過後可發布 GHCR 容器映像
```

這裡的 R2 是**選用**的快照分享機制，正常 API 與離線測試不需要其憑證；目前**未採用 DVC**。GitHub Actions 發布容器映像並不代表已將網站部署到線上。資料的版本、內容驗證與授權限制詳見 [資料架構](docs/DATA_ARCHITECTURE.md) 與 [資料來源](docs/DATA_SOURCES.md)。

## 快速開始

需求：`uv`（安裝專案指定的 Python 3.13）、Node.js 22+、npm。基本啟動不需要外部服務憑證。

```bash
uv sync
cd apps/web && npm ci && cd ..

# 終端機 1
make api

# 終端機 2
make web
```

- 網頁：`http://localhost:5173`
- API 文件：`http://127.0.0.1:8000/docs`

API 第一次啟動會從已提交的 Manifest 建立 SQLite 索引。

也可以用 Docker Compose：

```bash
docker compose up --build
# 網頁 http://localhost:8080
# API  http://localhost:8000/docs
```

離線驗證固定快照，不下載大型來源影像：

```bash
make data-demo       # 重建 var/demo-index.db
make data-validate   # Manifest、快照與雜湊驗證
make verify          # 專案完整檢查
```

### 用 API 搜尋地球類比區域

以下是**自訂目標地形**，而不是搜尋整個地球：

```bash
curl -s http://127.0.0.1:8000/api/v1/search \
  -H 'Content-Type: application/json' \
  -d '{
    "body": "custom",
    "target": {
      "slope_mean_deg": {"value": 12, "weight": 1},
      "terrain_relief_p95_p05_m": {"value": 80, "weight": 0.5}
    },
    "limit": 5
  }'
```

使用真實月球參考區域的搜尋：

```bash
curl -s http://127.0.0.1:8000/api/v1/search \
  -H 'Content-Type: application/json' \
  -d '{"body":"Moon","reference_id":"moon-shackleton-rim-site04-roi2","limit":5}'
```

新增地球 AOI（Area of Interest，研究區域）：

```bash
curl -s http://127.0.0.1:8000/api/v1/areas/analyze \
  -H 'Content-Type: application/json' \
  -d '{
    "body": "Earth",
    "bbox": {"west": -6.617, "south": 37.682, "east": -6.600, "north": 37.699},
    "requested_features": ["slope_mean_deg", "slope_p90_deg",
                           "terrain_relief_p95_p05_m", "roughness_std_m"]
  }'
```

可透過 `GET /api/v1/jobs/{id}` 與 `GET /api/v1/areas/{id}/features` 取得工作狀態與特徵。新 AOI 的大小、緯度與幾何限制以 `GET /api/v1/capabilities` 為準；完整 API 欄位見 [API 文件](docs/API.md)。

## 專案目錄

| 路徑 | 說明 |
| --- | --- |
| `apps/web/` | 前端介面、地圖、使用者互動與測試 |
| `apps/api/` | FastAPI、地形搜尋、AOI 任務、SQLite |
| `pipelines/earth/` | Copernicus DEM 匯入 |
| `pipelines/worldcover/` | ESA WorldCover 地表覆蓋特徵 |
| `pipelines/planetary/` | NASA LOLA 月球參考 |
| `pipelines/src/analog_pipelines/` | 共用特徵計算、快照及物件儲存介面 |
| `catalog/`、`configs/`、`contracts/` | 資料來源目錄、AOI 定義、API 和 Manifest 契約 |
| `samples/snapshot/` | 可重現的固定小型資料快照 |
| `docs/` | 系統架構、資料來源、演算法、開發與測試文件 |

## 團隊協作與驗證

團隊工作流程：Issue → 功能分支 → Pull Request（PR）→ 審查與持續整合（CI）→ 合併。

```bash
make test-backend
make test-frontend
make smoke
make verify
```

主要 CI 包含後端測試、前端檢查與 API 整合冒煙測試。資料相關變更另有離線 Manifest／Snapshot 驗證；線上來源測試與 R2 資料發布不應成為一般 PR 必須依賴的外部服務。

## 進一步閱讀

- [系統架構（中文）](docs/ARCHITECTURE.zh-TW.md) · [英文](docs/ARCHITECTURE.md)
- [架構圖（中文 SVG）](docs/diagrams/architecture.zh-TW.svg) · [英文 SVG](docs/diagrams/architecture.en.svg)
- [API](docs/API.md) · [資料來源與科學限制](docs/DATA_SOURCES.md) · [特徵定義](docs/FEATURE_REGISTRY.md) · [搜尋演算法](docs/SEARCH_METHOD.md)
- [資料架構](docs/DATA_ARCHITECTURE.md) · [資料治理](docs/DATA_GOVERNANCE.md) · [部署](docs/DEPLOYMENT.md) · [路線圖](docs/ROADMAP.md)
- [協作方式](CONTRIBUTING.md) · [測試](docs/TESTING.md) · [英文完整 README](README.md)

## 授權與限制

程式碼使用 [MIT License](LICENSE)。Copernicus DEM、ESA WorldCover、NASA LOLA 和 OpenStreetMap 各有來源、引用及使用條款，請參閱 [原始資料說明](docs/DATA_SOURCES.md) 與英文 README 的授權段落。

這是一個小範圍候選索引的 MVP：不能宣稱全球 Top 10、實際行星環境等價，或直接推薦基地位置。合成測試資料僅供測試／示範，不可當作真實觀測證據。

本次資料擴充的有限下載計畫、方法版本、D 槽路徑設定與重算指令，見
[Terrain 擴充紀錄](docs/TERRAIN_EXPANSION.md)。曲率及 Geomorphons 目前只有
研究原型和解析測試；現有月球裁切不足以支援擬議 buffer，因此不會產生觀測分類或進入搜尋分數。
