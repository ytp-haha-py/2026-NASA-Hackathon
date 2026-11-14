# PR #54 最新 head 複查

審查對象：[PR #54](https://github.com/ytp-haha-py/2026-NASA-Hackathon/pull/54)，
**`18951551b07322dc8e0836b0a3263f4182b1c34f`**，base `5d07072`。
複查期間 head 從 `5ab3b3e` 更新至 `92c986a`，最後至 `1895155`，均重新核對差異與測試；
不是對舊 `dfad348` 的重述。
本輪沒有修改該分支、發布 review／comment、解除 change request 或 merge。

## 結論

**#54 已於 2026-11-14 15:50:59 UTC 由 maintainer `ytp-haha-py` 合併**，
main squash commit 為 `e2a50809ad358f2ba89901ea193e120720c07328`；該 maintainer 已對
`1895155` 提交 APPROVED，先前的 change request 不再是當前 review decision。
這些是本輪工作期間發生的遠端變動，不是 Codex 執行的 merge／approval。

原來的大部分阻擋問題已實質修正。最終 head 已新增精確投影
讀取、物理支撐 ID、共享 content-addressed partitions、manifest-only membership、實際 v1
window／resolution，以及保留 runtime state 的 SQLite reset。不要把這些繼續列成「未實作」。

我的技術判定仍是下面兩組現有 contract 問題應先修正；既然已合併，請另開小型 fix PR，
在真實 canonical ingestion／宣稱 exhaustive coverage 前完成。不要重新打開已完成的投影、
ID、Snapshot 或 SQLite 實作，也不要求把全球 ingestion、R2、Tunnel、部署塞進修正。
本輪 gate／acceptance 證據見 `OPENCODE_HANDOFF.md`。沒有要求 rollback 或操作 production。

## 1. 合併前修正：v1→v2 的 bbox CRS 與資訊保留仍不正確

位置：`pipelines/src/analog_pipelines/typed_contracts.py` 的 `_area_from_candidate`、
`_observation_from_v1`、`migrate_v1_manifest`。

最新 head 已解除 1980／90 硬編碼，建立獨立 asset IDs，並保留 unknown 的 temporal／depth／
uncertainty。這些修正有效。剩餘問題：

- v1 `bbox` 是 WGS84 longitude／latitude，而 record `crs` 是 terrain analysis CRS。
  目前把原 bbox 直接複製到 `AreaV2` 與 `SpatialSupport`，同時標 `EPSG:32632`／earth_projected。
  例如 `west=10.4470654, south=46.46444404` 被標成 UTM metre coordinates。
  這會讓 canonical geometry／support 指向錯誤的位置，不是單純少一項 metadata。
- v1 feature `valid_fraction`、extraction scale、context 的 source resolution 等已知資訊
  仍沒有無損保留；context 也仍套 record 的 terrain analysis resolution。`warnings` 為空，
  呼叫者無法分辨轉換失去哪些資訊。

實際診斷先以 `Manifest.model_validate` 接受合法的 v1 複本：window=600 m、analysis／
extraction scale=30 m、valid_fraction=0.83。最新輸出確實是 600／30，但 valid_fraction
仍 `FIELD_NOT_REPRESENTED`，bbox degrees 被原樣放入 EPSG:32632 的 area／support。

最小修正：明確分離 geometry/bbox CRS 與 analysis CRS，保留原始 WGS84 bbox；如果要投影，
實際做座標轉換並記錄定義，不能只換 CRS label。保留已知的 observation quality／coverage／
source-support，無 v2 欄位時可保留明確版本化 legacy envelope／provenance 並報 warning。
也可對尚不能可靠轉換的輸入 fail closed、保留原 v1，不必在此 PR 執行 DB migration。

驗收包含：不同 window／resolution、bbox 真正座標與 CRS、context source support、
有效比例、原始 payload 不被修改。不要只檢查 IDs／method versions／重跑一致。

## 2. 合併前修正：不完整 grid coverage 仍標成 exhaustive

位置：`pipelines/src/analog_pipelines/coarse_grid.py` 的 `GridCoverage.sample_label`、
`generate_region_grid`。

目前仍固定回傳 `exhaustive_within_bbox`，但 grid 捨棄不足一個 window 的邊界，也允許
`stride > window_size` 產生內部空隙，投影 envelope 亦不等於原始 requested WGS84 region。
實際高緯／zone-edge 診斷有 east margin 4979.9058 m、north margin 416.2851 m 未分析，
卻仍標 exhaustive。這違反 repository 不把 subset 表達為完整調查的規則。

最小修正：標記為 partial/generated-grid coverage，保留未覆蓋 margin／stride gaps，
將 cell 數、分析支撐面積與 requested-region coverage 分開。不必在 #54 完成全球 tiling、
滑動窗口或所有尺度；只需目前回傳的 coverage label 誠實。

## 原 review 與五項架構疑慮核對

| 問題 | 最終 `1895155` 證據 | 原合併判定／現後續安排 |
|---|---|---|
| degrees 被標 metre／四角 bbox | `read_window` 使用 Geod pixel spans；grid 轉四角；native angular metadata 保留 | 原錯誤已修正 |
| 精確投影 footprint vs WGS84 envelope | 新 `read_projected_window` 建立原 metric bounds 的 destination raster；end-to-end test 已改走此路徑 | 不再因未實作投影 footprint 而阻擋；resampling／method 科學驗證留 ingestion 前 |
| Stable AnalysisUnit／grid IDs | ID hash 包含 projected origin、CRS、window、stride、resolution、grid version，無 region label 依賴；測試改名／移區域 | 已解決本階段 identity 問題；同 region 5／10 km ID 現在不同 |
| Snapshot manifest membership vs row snapshot_id | `partitions/<region>/<sha>.parquet` 由 manifests 重用；移除 row snapshot_id；測試同一 partition 跨 Snapshot 與舊結果可查；最終 commit 另移除 overwrite 例外 | 已實作；R2 publish、retention／GC 仍 follow-up |
| 無損、科學正確的 v1→v2 | 實際 window／resolution、獨立 asset IDs 與 unknown states 已修；bbox CRS／已知 quality support 未完整保留 | 上述第 1 項仍應阻擋 |
| SQLite rebuild 與 user AOI／jobs | `_reset_scientific_index` 不再 drop_all；保留 on_demand candidates／features／context、jobs、typed rows 及相關 datasets；新增 safety test | 不再因 drop_all 而阻擋；備份／transaction recovery／舊 typed schema migration 為 deployment 前 follow-up |
| 全量 input materialization | streaming `CoarseIndexWriter` 按 region／partition cap flush | 已修正；active regions 總 buffer cap 留 ingestion 前 |
| API 每 request 驗完整 index | `routes_global._repo` cache manifest path／mtime／size 的 verified repository | 已修正；仍依賴 immutable objects，外部竄改須明確 reload／verify |
| checkpoint run／task／output 安全 | run identity、task fingerprint、實際 output existence／SHA 驗證 | 已修正已建模條件；source／method identity 尚須在 ingestion run identity 納入 |
| zero-weight／duplicates／domain validation | fine／coarse 零權重忽略、跨 partition duplicate、值域檢查與 tests | 原主要問題已修正 |

SQLite 小型診斷：舊 `5ab3b3e` reset 後 typed area 1→0；最新 `92c986a` 1→1。
新增 `test_sqlite_safety.py` 另覆蓋 user AOI、jobs、features/context、typed observations。
未在實際使用者 DB 執行 rebuild；在部署前仍先備份，不能假設所有舊 typed table 已可自動加欄位。

投影診斷中保留舊 bbox reader，結果仍是 16.083984375°，這不是最新投影路徑失敗的證據。
最新 projected reader 得到正確 20×20 destination、250 m、metric bounds 完全相同，
synthetic edge case 的 mean 約 11.565°；它採 bilinear reprojection，與原生 polygon-masked
mean=10° 是不同估計量。這項 method choice／邊界 kernel／p90／flat fraction 的科學驗證應在
真實 ingestion 前完成，不拿 synthetic 常數測試當作已驗證科學可比性。

## 實際重現與保存

最終 `1895155` 另修正 partition flush 後 region counts、temporal interval endpoints／順序與
禁止覆寫 Snapshot。這些修正沒有改動上述 bbox CRS／資訊遺失或 grid exhaustive label。

程式：[review_pr54_probes.py](review_pr54_probes.py)。結果：
[最終 1895155](pr54-1895155-probe-results.jsonl)、[中間版 92c986a](pr54-92c986a-probe-results.jsonl)、
[歷史 5ab3b3e](pr54-probe-results.jsonl)。三版均實際 exit 0；
僅使用記憶體 manifest 複本、temporary synthetic GeoTIFF、in-memory SQLite。
診斷程式與結果都可從 Geology PR 取回，不只留在本機 `/tmp`。

```bash
cd /tmp/opencode-geology
PYTHONPATH=/tmp/opencode-pr54-latest/apps/api/src:/tmp/opencode-pr54-latest/pipelines/src /tmp/opencode-pr54-latest/.venv/bin/python docs/review_pr54_probes.py /tmp/opencode-pr54-latest
```

若 head 再更新，先核對 SHA，再重跑對應 regression／gate；本文件不是對未來 commit 的批准。
