# AnalogX

**探索月球與火星，尋找地球上的相似環境。**

AnalogX 是開源的行星環境探索工具。它整合 NASA、USGS、ESA 等機構的公開科學資料，讓使用者查看月球與火星的觀測資訊，並尋找在特定地形或環境條件下相近的地球地點。每項結果都能追溯資料來源及比較方法。

專案起源於 [NASA Space Apps Challenge 2026](https://www.spaceappschallenge.org/2026/challenges/permanent-moon-base-locations-and-mars/)。

[**開啟 AnalogX**](https://2026-nasa-hackathon.貓熊.台灣/) · [API 文件](https://api.貓熊.台灣/docs) · [English](README.md)

## 可以用 AnalogX 做什麼？

1. **尋找地球類比地點**：選擇月球、火星的參考地點，或自行設定坡度、起伏等地形條件，搜尋已建立索引的地球區域。
2. **查看六個面向的科學觀測**：包含地形、地質、熱環境、水冰、光照及地表材料，並了解每筆資料的來源與限制。
3. **篩選搜尋結果**：設定地形範圍，或依裸地、森林、建成區等土地覆蓋比例排除不符合需求的地點。
4. **分析新的地球區域**：選取一定範圍的地點，讓後端讀取地形資料、計算指標，並與既有候選比較。

**有觀測資料，不代表可以直接計入相似度。** 系統只會使用經過驗證、適合比較的指標進行排序。

## 六個比較維度

| 維度 | 內容 | 目前用途 |
| --- | --- | --- |
| D01 · 地形 | 坡度、起伏、粗糙度、平坦程度 | 地球類比地點排序 |
| D02 · 地質與礦物 | 地質單元、礦物證據 | 查看證據，尚無通用相似度分數 |
| D03 · 熱環境 | 溫度及熱相關觀測 | 背景資訊或觀測證據 |
| D04 · 水與冰 | 水分狀態、可能的冰相關訊號 | 觀測證據；間接訊號不等於確認有冰 |
| D05 · 光照與輻射 | 日照條件及已取得的輻射資料 | 背景資訊，尚不能比較完整輻射劑量 |
| D06 · 地表材料 | 土壤與表土物理性質 | 部分經驗證的地球 SoilGrids 資料可透過多維 API 計分 |

**土地覆蓋不是第七個維度。** ESA WorldCover 提供的是地球專用篩選條件，例如排除森林或建成區。

## 目前有哪些資料？

Repository 已提交的研究資料包括：

| 資料 | 目前涵蓋範圍 |
| --- | --- |
| 地球地形 | 113 個實際分析視窗 |
| 月球地形參考 | 18 個視窗 |
| 火星地形參考 | 5 個視窗 |
| 地球粗篩索引 | 三個試驗區域，共 168 個網格 |
| D02–D06 六維相關觀測 | 520 筆紀錄，分布於有限的地球、月球及火星區域 |

這些是**有限範圍的研究資料**，不是整顆地球、月球或火星的完整調查。即使地圖能顯示整個天體，也不代表每個位置都有可比較的科學觀測。後端已提供月球與火星全球底圖資訊，互動式前端地圖仍在協作開發中。

詳細數量、來源及品質限制請看[多維資料報告](docs/MULTIDIMENSIONAL_RELEASE.md)與[資料來源](docs/DATA_SOURCES.md)。

## 相似度怎麼計算？

使用者先選定參考地點或輸入目標數值，系統再依照各項指標及權重比較已索引的候選區域。距離越小，表示**在這次使用的指標上**越接近；距離不是相似度百分比，也不代表兩顆星球的實際環境相同。

資料缺失、測量方式不一致或解析度不相容時，系統會標示限制，不會自行補成零。多維 API 已能在符合條件時結合地球地形與部分地球土壤模型資料；目前**不能將所有六維資料直接加總成經驗證的跨天體分數**。

[搜尋方法](docs/SEARCH_METHOD.md) · [多維搜尋 API](docs/MULTIDIMENSIONAL_SEARCH.md) · [月球／火星地圖 API](docs/PLANETARY_MAPS_API.md)

## 在本機執行

需要 [uv](https://docs.astral.sh/uv/)、Node.js 22+ 與 npm。

```bash
git clone https://github.com/ytp-haha-py/2026-NASA-Hackathon.git
cd 2026-NASA-Hackathon
uv sync
make api
```

另開一個終端機：

```bash
cd apps/web
npm ci
npm run dev
```

網頁網址是 `http://localhost:5173`，本機 API 預設為 `http://127.0.0.1:8000`。目前 Vite 開發代理預設連本機 API；正式網站連公開後端。設定方式見[前端開發文件](apps/web/README.md)及[部署說明](docs/DEPLOYMENT.md)。

執行完整檢查：

```bash
bash scripts/verify.sh --ci
```

## 技術架構與協作

- **前端**：React、TypeScript、Vite、Leaflet。
- **後端與搜尋**：FastAPI、Python、SQLAlchemy、SQLite。
- **資料處理**：可重現的資料管線與來源紀錄，使用 Parquet 和 DuckDB 查詢粗篩地形索引。
- **部署**：Cloudflare Pages 提供前端網站，Cloudflare Tunnel 連接 API 主機。

透過 GitHub Issues 與 Pull Requests 協作；前後端以 API 契約分工，方便平行開發。

[系統架構](docs/ARCHITECTURE.zh-TW.md) · [資料模型](docs/DATA_MODEL.md) · [API](docs/API.md) · [開發規劃](docs/ROADMAP.md) · [參與貢獻](CONTRIBUTING.md)

## 授權與資料使用

程式碼採用 [MIT License](LICENSE)。NASA、USGS、ESA 等來源的科學資料各有引用方式、使用條件與限制，詳見[資料來源文件](docs/DATA_SOURCES.md)。AnalogX 是研究與探索工具，**不是已經過驗證的月球或火星基地選址系統**。
