# R6 引擎手術 Phase 2 設計修正（2026-10-01）

> 依據 Phase 0+1 v2 驗證結果重寫。原 charter `e-r6-engine-surgery-2026-10-01.yaml` 的 Phase 2（prev-token 預測 + 逐層重算 FFN）**已被證明是錯的槓桿**，本文件取代之。

## 1. Phase 0+1 v2 驗證結論（已實跑）

| 信號 | 觀測值 | 含義 |
|---|---|---|
| `CGC-SEG-PROBE: expert_cache_active/pool_active` | `1 / 1` | expert cache 確實 active；`ffn_moe_slots` 不存在是 SEG_BATCH graph build 路徑不同，非 cache 關閉 |
| `CGC-SEG-RECOMPUTE` 行數 | **15600** | node-walk 命中 `ffn_moe_topk-<il>`，舊版 0 行問題解除 |
| `resident=`（每層一致） | **143/256 = 55.9%** | pool 駐留率遠低於誠實臂 96.3% |
| `first=`（topk data 當 int 讀） | `-1123126721`（垃圾） | `ffn_moe_topk->data` 是 **float logits 而非 int expert index**（n_ids=16384=2048tok×8，distinct≈16382 符合連續 logits） |

## 2. M1 0/9 根因定位

**根因 = pool 沒有 re-centre，不是 FFN compute 算錯。**

`CGC_SEG_BATCH=1` 在單段提交路徑跳過 per-layer hook（`llama-context.cpp:4174-4185` 註解實錘），導致：
- `spac_update`（pool re-centre 到 decode 工作集）不跑
- `cache_step_union[il]`（prefetch feed）不跑

⇒ pool 停在 prefill 留下的低駐留狀態（**55.9%** vs 誠實臂 96.3%）。
⇒ gather 讀到近半數錯/舊 expert ⇒ logits 錯 ⇒ **M1 0/9**。

## 3. 原 Phase 2 設計為何錯

原 charter 假設「prev-token 預測 + 逐層重算 FFN」能修 M1。但重算 FFN 仍依賴 pool 提供正確 expert 權重：
**pool 不 re-centre → 重算讀到的也是錯 expert → M1 照樣救不回**。
重算是在錯誤輸入上做正確運算，無濟於事。

## 4. 修正後 Phase 2 設計

### 方案 A（優先，最低風險）—— 補回被跳過的 hook

在 SEG_BATCH 單段提交路徑中，於提交前/同步後**補呼叫**：
1. `spac_update`（pool re-centre）→ 讓駐留率回到 ~96.3%
2. `cache_step_union[il]`（prefetch feed）→ 把 decode 工作集載入 pool

預期效果：M1 自然恢復（gather 讀到正確 expert），**且不需重算 FFN**，bit-identical 護欄保住。
這才是把「27.5 t/s 紅利變可交付」的正路——單段提交省下的 GPU idle 保留，同時 pool 正確。

### 方案 B（僅當 A 不夠）—— 重算 FFN（必須疊在 A 之上）

若補 hook 後仍有 M1 偏差（例如 SEG_BATCH 的 compute 順序本身引入數值差），才做逐層重算；
但**重算前須先確保 pool 已 re-centre**，否則重算輸入錯。即 B 依賴 A 先成立。

### 讀 routed ids 的來源修正

Phase 0 的 memcpy 目標 `cache_ids_tensors[il]` 維持；但來源節點要從 `ffn_moe_topk`（float logits）
改為 **argsort/index 節點**（真正的 int expert index），否則 Phase 0 寫入的是垃圾。

## 5. 下一步（待對齊後才動 src）

1. 與用戶確認「**方案 A 優先**」。
2. 小範圍試補 `spac_update`：單層或單 hook，發車驗 `resident=` 是否回升到 ~96.3% 且 M1 恢復。
3. 若 A 成立 → 全層鋪開 → 跑 M1/M2/M3 oracle 認證。
4. 若 A 不成立 → 啟用方案 B（疊在 A 之上）。
5. 全程不動 `agent_harness/`；src 歸屬待確認後過 commit 閘。

---
*證據檔：`/tmp/harness_seg_p01v2b/result.stderr.log`（15600 行 `CGC-SEG-RECOMPUTE`，`resident=143/256` 全域一致）*
