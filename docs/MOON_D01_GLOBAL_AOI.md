# Moon D01 Global AOI (LOLA) — 任意 bbox 實際讀取與計算

> 範圍：Goal A（Moon Global D01）。本文件記錄可用於「Moon 任意有效 bbox AOI」之 NASA LOLA 官方 DEM 來源、bbox 視窗讀取/計算實作、實測結果與限制。**不**將模型、pilot fixture 或 basemap 視為全球觀測覆蓋；缺資料/不可讀區域明確標示 `no_data` / `partial` / `invalid`，不 zero-fill。座標為 Moon body-fixed planetocentric（lunar radius 1737.4 km），不使用 Earth WGS84/EPSG:4326。

## 1) 官方來源驗證（URL / license / version / checksum / CRS / resolution / nodata）

| 來源 ID | 產品 / URL | License / citation | Version / date | Checksum | CRS / frame | Native resolution | Nodata | Extent / lon convention | Size / format | 備註 |
|---|---|---|---|---|---|---|---|---|---|---|
| `pgda-lola-ldem128-pa-pixel-202405` | NASA GSFC PGDA LRO/LOLA **LDEM128_PA_pixel_202405** global GeoTIFF. Product: https://pgda.gsfc.nasa.gov/products/95 ; Raster: https://pgda.gsfc.nasa.gov/data/LOLA_PA/LDEM128_PA_pixel_202405.tif | PGDA Data Usage Policy: public NASA archive, citation requested. Neumann, G. (2024). LOLA MOON_PA gridded dataset [Data set]. NASA GSFC PGDA. doi:10.60903/LOLA_PA (https://doi.org/10.60903/LOLA_PA). Restrictive redistribution terms not explicitly stated on page. | Filename tag `202405` (product page describes improved MOON_PA model; no separate formal version string stated). | Not published/found on PGDA product/data page. | MOON_PA (DE421), body-fixed, planetocentric geographic on sphere R=1737400 m. GeoTIFF CRS/proj tags not fully stated on page; implementation treats/reads as `+proj=longlat +R=1737400 +no_defs` and verifies from file when opened. | 128 ppd = 2π·1737400/(360·128) ≈ **236.901 m** (equatorial). | `NaN` (float32; verify from file). | Global cylindrical 0..360° longitude, -90..90° latitude (PGDA page states global cylindrical arrays; lon domain verified against product conventions/file). | 46080×23040, float32, deflate, 256×256 tiles, ~2,705,286,892 bytes (Content-Length). Not advertised as COG profile but internally tiled; **HTTP Range supported** (206 observed). | Derived shape model (SLDEM2015 256 ppd + polar LDEMs averaged in MOON_PA), partly interpolated. Vertical unit not explicitly tagged; pixel magnitude consistent with metres relative to 1737.4 km sphere (inferred). Bounded `/vsicurl` window reads recommended. |
| `moon-lola-80s-20mpp-adj` | NASA GSFC PGDA Product 90 south polar LOLA DEM. Product: https://pgda.gsfc.nasa.gov/products/90 ; Raster: https://pgda.gsfc.nasa.gov/data/LOLA_20mpp/LDEM_80S_20MPP_ADJ.TIF | Public NASA archive; citation requested. Barker et al. 2023 PSJ 4, 183 doi:10.3847/PSJ/acf3e1; data doi:10.60903/gsfcpgda-lola-spole. | Product 90 (date not separately stated on page). | Not published/found. | South polar stereographic X/Y in metres, **MOON_ME (DE421)**, R=1737400 m. | 20 m/px. | Not published on page; read from file at runtime. | 80°S–90°S (polar cap band). | ~2.6 GB GeoTIFF; page states **cloud-optimized GeoTIFF (COG)**; HTTP Range supported (206 observed for polar COG family). | Used as polar fallback for high southern latitude AOIs (`lat <= -80°` preferred). Exact dtype/scale/offset verified from file. |
| `moon-lola-80n-20mpp-adj` | NASA GSFC PGDA Product 98 north polar LOLA DEM. Product: https://pgda.gsfc.nasa.gov/products/98 ; Raster: https://pgda.gsfc.nasa.gov/data/LOLA_20mpp_NP/LDEM_80N_20MPP_ADJ.TIF | Public NASA archive; citation requested. Barker et al. 2025 PSJ 6, 83 doi:10.3847/PSJ/adbc9d; data doi:10.60903/gsfcpgda-lola-poles. | Product 98 (date not separately stated on page). | Not published/found. | North polar stereographic X/Y in metres, **MOON_ME (DE421)**, R=1737400 m. | 20 m/px. | Not published on page; read from file at runtime. | 80°N–90°N. | ~2.7 GB GeoTIFF; stated COG; HTTP Range supported. | Used as polar fallback for high northern latitude AOIs (`lat >= 80°` preferred). |
| `svs-cgi-moon-kit-ldem16` | NASA SVS CGI Moon Kit (#4720) global LOLA-derived displacement map. Page: https://svs.gsfc.nasa.gov/4720 ; Raster: https://svs.gsfc.nasa.gov/vis/a000000/a004700/a004720/ldem_16.tif | NASA SVS visualization asset; underlying data from public LOLA/LROC archives. Explicit redistribution license not stated as SPDX on page. | SVS 4720 released 2019-09-06, updated 2026-01-09. | Not published/found. | Global cylindrical (exact CRS/lon-domain tags verify from file). Reformat of LOLA gridded data; centered on 0° lon per page. | 16 ppd (5760×2880), ~1895 m equatorial. | Not stated on page; verify from file. | Global -180..180 (stated/verify). | ~63.3 MB float TIFF; HTTP Range supported (206 observed). | **Catalog only；目前不啟用**（bare array 無 in-file CRS/geotransform/nodata，且值為 km；需明確 -180..180 映射與 km→m 轉換端到端驗證）。不作為高解析觀測替代。 |

來源選擇策略（實作於 `pipelines/planetary/lola_global.py`，global PGDA 事實來自 A1 `lola_global_source.py`）：高緯優先使用對應極區 polar COG（native stereographic）；其餘使用 PGDA LDEM128 global。SVS 64/16/4 ppd 僅列於 catalog，**目前不啟用**，因其 bare array 需要明確 -180..180 映射與 km→m 轉換；未端到端驗證前不得作為 fallback。若來源不可讀/無覆蓋，回傳明確 `no_data`/錯誤與來源狀態，不以其他來源或模型冒充。

## 2) 實作摘要（分塊/視窗讀取，整個 bbox）

- 模組：`pipelines/planetary/lola_global.py`（CLI: `uv run python pipelines/planetary/lola_global.py --bbox W S E N --json`）
- 輸入：Moon planetocentric lon/lat bbox（支援 -180..180 與 0..360 正規化），可選 `--source-id` / `--resolution-m` / `--max-pixels` / `--max-side-m`。
- 讀取：依 bbox 選擇來源；以 GDAL/rasterio 對遠端 HTTPS 使用 `/vsicurl` 或本機路徑快取讀取**所需視窗**（`Window` + bounds 轉換），再重投影至 body-fixed metric analysis CRS（中低緯 local azimuthal equidistant；高緯 polar stereographic）之 90 m（預設）**計算網格**。90 m 是 analysis grid，不是來源 native support；LOLA128 為 236.901 m，因此標記 `upsampled_from_coarse_source`。跨換日線 bbox 會分片計算並彙整整個選取範圍。
- 計算：對整個 bbox 有效像元計算 D01 五項 — `slope_mean_deg`, `slope_p90_deg`, `terrain_relief_p95_p05_m`, `roughness_std_m`, `flat_terrain_fraction`（Horn 3x3 slope，與 `analog_pipelines.features.terrain_metrics` 語義一致，metric CRS）。
- 記錄：`pixel_count`, `valid_pixel_count`, `invalid_pixel_count`/`nodata_pixel_count`, `valid_fraction`, `partial`, `invalid`, `no_data`, `source` provenance, `analysis_crs`, `analysis_bbox`, warnings/errors。過大 AOI（`max_pixels`/`max_side_m`）明確拒絕；來源斷線/讀取失敗回傳明確錯誤狀態與 `features=null`。
- 限制：非 pilot/非模型；未宣稱全球 90 m 連續觀測；部分區域（依來源品質/讀取狀態）可能 partial/no_data。

### 2.1 解析度與跨天體可比性（每一筆結果都必須記錄）

分析網格 90 m 是**計算網格**，不是每個來源的 native support。每一筆 `AoiResult`/JSON 都包含：

| 欄位 | 意義 |
|---|---|
| `source_resolution_m` | 來源 native support（LOLA128 = 236.901 m；polar COG = 20.0 m；SVS 16 ppd = 1895.2 m；SVS 64 ppd = 473.802 m）。 |
| `analysis_resolution_m` | 實際計算網格（預設 90 m；不是來源 native）。 |
| `resolution_class` | `upsampled_from_coarse_source`（來源比網格粗，數值含插值，不代表 native 90 m 細節）或 `native_or_downsampled`（來源等於/細於網格，降採樣）。 |
| `cross_body_comparable` | 本 planetary global path 一律 `false`：方法/支援與 Earth D01 90 m/1.98 km gate 不一致；不得直接跨天體排名。 |
| `comparability_reason` | 人類可讀原因，含 `upsampled_from_coarse_source` 與 frame 注意事項。 |

- LOLA128 236.901 m → 90 m 網格是 **2.63× 插值/升採樣**，只能標為 coarse-source fallback，不能說成 native 90 m lunar data。
- 20 m polar COG → 90 m 網格是 downsampling，`resolution_class=native_or_downsampled`，但仍不是 Earth 90 m/1.98 km 的同支援比較，所以 `cross_body_comparable=false`。
- MOON_PA（LDEM128）與 MOON_ME（5 m site / polar COG）frame 相差約 875 m；`comparability_reason` 會明確記錄，與 5 m site window 混用前必須做 frame conversion。
- SVS fallback（64/16/4 ppd）在檔案內**沒有 CRS、geotransform、nodata**，且值為公里。A1 的 `lola_global_source.py` 已記錄其 -180..180 映射與 km→m（raw × 1000）轉換；本 AOI 引擎目前**不啟用 SVS fallback**，避免在未端到端驗證前把 km 當 m。可用來源為 PGDA128 與 polar COG。

## 3) 真實 Moon AOI 計算（可重現 command 與實際數值）

> 以下為 2026-11-15 於本 worktree 以官方 LOLA 來源實際執行（bounded `/vsicurl` 視窗讀取，非 pilot fixture、非模型）的 bbox 計算結果。每筆皆回報整個框選範圍之 pixel/valid 統計與來源。若環境無法連線來源，CLI 會回傳明確 `no_data`/錯誤而非偽造數值。

### 3.1 低緯（low latitude, ~10°S）

```bash
uv run python pipelines/planetary/lola_global.py --bbox 20.0 -10.2 20.2 -10.0 --json
```

- status: `ok`, source: `pgda-lola-ldem128-pa-pixel-202405` (PGDA LDEM128 global, 128 ppd, MOON_PA)
- **coarse-source fallback**: `source_resolution_m`: 236.901, `analysis_resolution_m`: 90.0, `resolution_class`: `upsampled_from_coarse_source`, `cross_body_comparable`: false
- analysis CRS: local AEQD `+proj=aeqd +lat_0=-10.1 +lon_0=20.1 ... +R=1737400`
- pixel_count: 4556, valid_pixel_count: 4556, nodata_pixel_count: 0, valid_fraction: 1.0, partial: false
- features:
  - `slope_mean_deg`: 19.808971257216918
  - `slope_p90_deg`: 30.825464059896643
  - `terrain_relief_p95_p05_m`: 1892.7785546209848
  - `roughness_std_m`: 263.4328873301446
  - `flat_terrain_fraction`: 0.16526806526806526

### 3.2 中緯（mid latitude, ~30°N）

```bash
uv run python pipelines/planetary/lola_global.py --bbox 30.0 30.0 30.2 30.2 --json
```

- status: `ok`, source: `pgda-lola-ldem128-pa-pixel-202405` (PGDA LDEM128 global)
- **coarse-source fallback**: `source_resolution_m`: 236.901, `analysis_resolution_m`: 90.0, `resolution_class`: `upsampled_from_coarse_source`, `cross_body_comparable`: false
- analysis CRS: local AEQD `+proj=aeqd +lat_0=30.1 +lon_0=30.1 ... +R=1737400`
- pixel_count: 4012, valid_pixel_count: 4012, nodata_pixel_count: 0, valid_fraction: 1.0, partial: false
- features:
  - `slope_mean_deg`: 10.58805826593364
  - `slope_p90_deg`: 15.824575528470584
  - `terrain_relief_p95_p05_m`: 924.7385978145519
  - `roughness_std_m`: 63.134439751901546
  - `flat_terrain_fraction`: 0.11935140882509304

### 3.3 高緯（high latitude / polar south, 85°S–85.5°S，使用 PGDA polar COG）

```bash
uv run python pipelines/planetary/lola_global.py --bbox 0.0 -85.5 2.0 -85.0 --json
```

- status: `ok`, source: `moon-lola-80s-20mpp-adj` (PGDA Product 90, south polar COG 80–90°S, 20 m/px, MOON_ME polar stereographic)
- **higher-resolution polar source**: `source_resolution_m`: 20.0, `analysis_resolution_m`: 90.0, `resolution_class`: `native_or_downsampled`, `cross_body_comparable`: false
- analysis CRS: south polar stereographic `+proj=stere +lat_0=-90 +lat_ts=-90 +lon_0=1 ... +R=1737400`
- pixel_count: 9971, valid_pixel_count: 9971, nodata_pixel_count: 0, valid_fraction: 1.0, partial: false
- features:
  - `slope_mean_deg`: 10.588056520771183
  - `slope_p90_deg`: 14.839683174315857
  - `terrain_relief_p95_p05_m`: 1582.444757390533
  - `roughness_std_m`: 219.80908968224497
  - `flat_terrain_fraction`: 0.052736631999159574

> 極區 bbox 由來源選擇邏輯自動選用 polar COG（native stereographic）並對整個框選範圍計算；數值來自實際 DEM 視窗讀取。

## 4) 邊界與錯誤案例（測試覆蓋）

- 極區 bbox：選擇 polar source / polar stereographic analysis CRS（`test_lola_global_aoi.py`）。
- 跨換日線：`west > east` 正規化與分片（`validate_aoi_antimeridian_splits`）。
- 過大 AOI：`aoi_too_large`（side 或 pixel budget）明確拒絕。
- 來源斷線/不存在：`analyze_lola_aoi` 回傳 `no_data=true`, `features=null`, 明確 error code/message（不可 zero-fill）。
- 全 nodata 區域：`_features_from_single_array` / analyze 回傳 `no_data`。

## 5) Coverage Matrix 片段（D01 Moon global AOI path）

| Body | Dimension | Feature(s) | Source (official) | Coverage (actual, this path) | Class | Time support | Depth/vertical | Spatial support / resolution | CRS / frame | QA / uncertainty | Notes |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Moon | D01 Terrain shape | `slope_mean_deg`, `slope_p90_deg`, `terrain_relief_p95_p05_m`, `roughness_std_m`, `flat_terrain_fraction` | NASA PGDA LRO/LOLA LDEM128 MOON_PA global (`pgda-lola-ldem128-pa-pixel-202405`, 128 ppd, doi:10.60903/LOLA_PA) | **On-demand bbox window reads** over requested AOI (not a fixed 5 km center, not limited to committed pilot). Global product extent; per-request `valid_fraction`/`partial`/`no_data` reported. Not claimed as complete/exhaustive survey. | measured/derived from observed DEM (derived shape model, partly interpolated per source) | Static DEM epoch/version `202405` (source release). | Surface elevation (radius relative to 1737.4 km sphere); vertical unit inferred metres. | Analysis grid default 90 m in body-fixed metric CRS over entire bbox; native source ~237 m (global). | Moon body-fixed planetocentric, R=1737400 m; never Earth WGS84. Analysis CRS: local AEQD or polar stereographic (metric). | `pixel_count`, valid/nodata counts, `valid_fraction`, `partial/invalid/no_data`, source provenance, warnings. No checksum published by source; file metadata read at open. Interpolation/derivation uncertainty inherent to source. | Values computed only from valid pixels; missing/unreadable -> no_data/partial, never zero-fill. |
| Moon | D01 Terrain shape | same D01 set | NASA PGDA Product 90 south polar COG `LDEM_80S_20MPP_ADJ.TIF` (`moon-lola-80s-20mpp-adj`, 20 m, MOON_ME polar stereo) | On-demand for high-lat south AOIs (80-90°S preferred). | measured/derived from observed DEM | Source product release (see product page). | Elevation in source DEM units/metres (verify file). | Native 20 m; analysis resampled to requested analysis grid (default 90 m). | Polar stereographic MOON_ME (DE421), R=1737400 m. | Same QA fields + explicit source selection; nodata/dtype/checksum verified from file when available. | Preferred for polar bboxes to avoid global equirectangular distortion. |
| Moon | D01 Terrain shape | same D01 set | NASA PGDA Product 98 north polar COG `LDEM_80N_20MPP_ADJ.TIF` (`moon-lola-80n-20mpp-adj`, 20 m) | On-demand for high-lat north AOIs (80-90°N preferred). | measured/derived from observed DEM | Source product release. | Elevation in source DEM units/metres (verify file). | Native 20 m; analysis default 90 m grid. | Polar stereographic MOON_ME (DE421). | Same QA fields. | Preferred for polar bboxes. |
| Moon | D01 Terrain shape | same D01 set | NASA SVS 4720 `ldem_16.tif` (`svs-cgi-moon-kit-ldem16`, 16 ppd) | **Catalogued but not enabled** in this AOI engine until the bare-array -180..180 mapping and km→m conversion are exercised end to end. | modeled/reformatted visualization-derived from LOLA gridded data | SVS release 2019/updated 2026-01-09. | km (raw × 1000 → m per A1 adapter); no in-file CRS/geotransform/nodata. | Native ~1.9 km eq. If enabled: `upsampled_from_coarse_source`; otherwise not used. | Global cylindrical -180..180, explicit mapping only. | Lower confidence; `cross_body_comparable=false`; never a substitute for high-res LOLA DEM. | Not a substitute; must never be read as metres without km→m conversion. |

**Non-claims:** no global/exhaustive D01 survey claim; no cross-body scoring claim from this module; no fabricated values; source license/redistribution terms follow original providers.

## 6) 檔案與測試

- 實作：`pipelines/planetary/lola_global.py`
- 測試：`pipelines/planetary/tests/test_lola_global_aoi.py`（bbox/antimeridian/polar CRS/過大 AOI/nodata/來源斷線；離線 synthetic rasters，無網路）
- 文件：`docs/MOON_D01_GLOBAL_AOI.md`

執行測試：

```bash
uv run pytest pipelines/planetary/tests/test_lola_global_aoi.py -q
```