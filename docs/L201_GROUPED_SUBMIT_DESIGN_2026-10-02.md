# L20-1 分組提交（k 組）設計與量測規格 — 2026-10-02

> 立項：`scripts/check/charters/e-l201-groups-2026-10-02.yaml`（預註冊）。
> 承接：`e-l201-submit-path`（T_sub 中位 0.937 ms ⇒ 提交路徑＝紅利主體，MECHANISM）
> ＋ `e-l201-accel`（FAST 單提交＋預測＋回退，程式碼已在樹上）。
> 本檔＝機制、曲線算術、hunk 級規格、量測協議。**未實作；先拍落地路線**（見 §6）。

## 0. 一句話

把「41 段＝41 次提交」換成「k 次提交」，組內用預測寫 remap、組後回讀真值，
**失配只重跑本組**；量 Δstep(k) 曲線，再用曲線決定要不要走到 k=1（FAST 單提交）。

## 1. 機制：省什麼、付什麼

省（每個消掉的邊界一次）：L20-1 量到的每提交成本
`T_sub = (cb + submit + gap_sum)/segs`，中位 0.937 ms/提交（step≥64: 1.007）。
⇒ 41 → k 組，最多省 **（41−k）× 0.937 ms/step**（k=8 ⇒ ≈31 ms；k=16 ⇒ ≈23 ms）。

付（每個改為預測的邊界）：
- 組內邊界 g−1 個／組 ⇒ 全步 (41−k) 個邊界要靠 prev-token 預測（組**首**邊界用上一組回讀的
  真值 ⇒ 每組只冒 g−1 個預測風險）；
- 一個預測錯 ⇒ 該組輸出錯 ⇒ **重跑本組 g 段**（proven 路徑每段 ≈ 86.8/41 ≈ 2.1 ms）
  ⇒ 期望損失 (1−p^(g−1)) × g × 2.1 ms（p＝逐邊界命中率，暫當獨立）；
- 組後回讀＋比對（每組一次 `sched_synchronize`＋小張量 get；量級 ms 以下）。

## 2. 預期曲線（break-even）

每組期望淨利 ≈ `(g−1)×0.937 − (1−p^(g−1))×g×2.1`（ms）。令其 >0：

| g（組大小） | k≈41/g | 省/組 (ms) | 失配成本/組 (ms) | 需要 p > |
|---|---|---|---|---|
| 2  | 21 | 0.94 | 4.2 | 0.78 |
| 3  | 14 | 1.88 | 6.3 | 0.84 |
| 5  | 8  | 3.75 | 10.5 | 0.90 |
| 8  | 5  | 6.56 | 16.8 | 0.93 |

⇒ **曲線的可判性完全由 p 決定**；k=41（proven）是 0 點、k=1（FAST）是單提交端。
⇒ 若 p ≥0.95：k=8 級 ~ +8–25 ms/step；若 p ≈0.87：最佳只在 k≈16–20（g≈2）且 ~+8 ms/step。

**2026-10-02 00:15 實測更新（ace、本檔完成後 10 分鐘）**：`y-fast-nonmtp-long` M1/M2/M3
**222/222**、`step=200 taken=195 incomplete=5 mismatch=0 why=0`
（`Backup/m123_oracle_gate/summary_y-fast-nonmtp-long.json`、
`Backup/cgc_logs/llama_server_20261002_001438.log`）
⇒ p ≈ 1（≈195 個接戰步、0 失配）⇒ **曲線預期反轉成單調偏向 k=1**（提交數愈少愈好、沒有失配可回收）。
⚠ 保留：(i)「mismatch=0」的比對是否真的跑過只有間接證據（輸出 222/222）；
(ii) 可比性靠 `CGC_SEG_BATCH_FAST` 進 DIAGNOSTIC_KEYS（＝先假定數值中性），最乾淨是**同場成對**；
(iii) **Δstep(k=1) 仍未量** ⇒ 下一個量測＝成對（FAST off vs on，其餘逐字相同）。
⇒ 階梯從「主路」退成「退路」：只在 p 會掉的形狀（MTP-on verify 批、冷啟動、首 token）才需要 k>1。
（p 的來源：現行 FAST 臂接戰後的 `mismatch/taken` ⇒ p ≈ (1−m/t)^(1/40)；**目前 taken=0**，
見 §5。若需要更細，可在回讀迴圈加逐層計數——比整步 mismatch 多一欄，成本近零。）

## 3. 實作規格（hunk 級、錨點＝2026-10-02 00:00 樹況）

**旋鈕**：`CGC_SEG_BATCH_FAST=<k>`（值＝組數；缺席＝off；`1`＝現行 FAST；`41`＝proven 迴圈）。
解析點：`llama-context.cpp` 兩處 `getenv("CGC_SEG_BATCH_FAST")`（:4055、:4430）
＋ `ggml-backend.cpp` :1783。既有 presence-only 語意在 k 缺席時保持逐位元不變。

**(a) 分組切點**（ggml-backend.cpp，已有材料）：`as_idx[64]`／`n_segs`（:1826-1855）
⇒ 把 41 段等分成 k 組（或 g 上限 + 尾組）；組界＝`as_idx`。

**(b) 組提交**：`seg_view(s)`（:2113-2118）推廣成 `group_view(a_seg, b_seg)`
（a＝組首段起點、b＝組末段 argsort 節點；尾段特例照舊），提交/等待沿既有
`ggml_backend_graph_compute_async` ＋ `cgc_done` poll（:1857-1862）。

**(c) 組前寫表**：現行 FAST 區塊（llama-context :4042-4125）已是「40 層預測 ensure＋寫 remap」，
**保留為步前預設值**；組界處只需用上一組回讀真值改寫「下一組首邊界」的 remap
（等效 proven 迴圈的 hook，但不需 wait→hook 的時序，因為真值已在手上）。

**(d) 組後驗證**：沿既有回讀比對邏輯（llama-context :4423-4493：`cache_ids_cont_tensors`
真值 vs `cache_remap_tensors` 已消費 slot，`slot_table_safe` 比對）⇒ **粒度改按組**。
介面二選一（實作前寫死）：
  1. 在 `ggml_backend_sched` 上加兩個可選回呼 `cgc_group_pre/post(j)`（llama-context 註冊）；
  2. 或由 llama-context 經 backend-reg proc-address 表呼叫 ggml 新原語 `cgc_submit_range(a,b)`
     ＋ `cgc_wait_group()`（同 `ggml_metal_get_cgc_done` 既有機制）。
  兩案都保持 default（未設 env）時零行為差。

**(e) 組級 fallback**：失配 ⇒ `setenv(CGC_FORCE_41SEG,"1")` ＋ 對**本組**用
`hook_seg(i)`＋`submit_seg(i)` 逐段重跑（:2144-2170 既有原語），重跑後：
  1. 重驗本組；2. **用重跑的真值改寫下一組首邊界 remap**（bit-identity 的關鍵一步，別漏）；
  3. 再續下一組。全步第一次（無 prev 預測）或某組預測缺失 ⇒ 該組直接走 proven 逐段
  （＝組級降級，不整步降級）。

**(f) 計數器**：`CGC-GROUPS: step= submits= groups= mismatch_groups= fallback_ms= why=`
（每步或每 8 步）；`m123_oracle_gate.py` 的 `DIAGNOSTIC_KEYS` 追加 `CGC_SEG_BATCH_FAST` 已存在
（ace 已加），值域變化不影響可比性判定。

## 4. 量測協議（預註冊於 charter）

- k 掃描 6 點：41／16／8／4／2／1 ＋ 誠實臂（`CGC_DECODE_PROFILE=1;CGC_GPU_TIMING=1`）。
- 同場多臂、`prod-new`＋`harness bench`；端點＝Δstep（相對 k=41 臂）＋每臂 oracle M1 9/9。
- t/s 一律不引用（承 L20-1 的 `tps_claims: 0`）；髒窗只標診斷級。
- 判詞：曲線存在 k* 使 Δstep > 兩臂散布 ⇒ 紅利可交付；全不升 ⇒ 判 DEAD。
- 決策句：「走不走 FAST（k=1）」＝ `Δstep(k=1) − Δstep(k*) > 一輪散布` 才另立交付卡。

## 5. 現況風險（實作前必讀）

1. **k=1 已接戰（00:15），但 Δstep 未量**：早期 log（`…00_012438`等）`taken=0 … why=3/2/5`；
   最新長跑 `…00_1438` 已 `taken=195/200, mismatch=0`，且長見證 222/222。
   ⇒ 卡點從「engagement」變成「**Δstep 成對**」（以及 mismatch=0 的真實性驗證）。
   k>1 的分組路徑只在 p 會掉的形狀（MTP-on verify 批、冷啟動、首 token）才有意義。
2. **已知 NaN 交互**：`CGC_SLOT_TABLE_GPU`×單段提交（`docs/L201_NAN_BISECT_2026-10-01.md`）
   ⇒ 分組臂不要帶那顆旗標；驗收一律 M1 9/9。
3. **host-leaf 真值通道**：`ffn_moe_ids_cont` 在 host-leaf 分支由
   `CGC_SEG_BATCH_FAST` gate 的捕獲張量提供（ace 已實作於 llama-graph.cpp）⇒ 回讀驗證可用。
4. **盒子與檔案都是共享資源**：分組臂的 sweep 要跟 ace 的 oracle 排隊；
   `ggml-backend.cpp`／`llama-context.cpp` 是 ace 的工作面（00:00:51 仍在改）。

## 6. 落地路線（待拍板；未拍板前不開工）

- (a) **ace 落地**（零碰撞）：把現有 FAST 一般化成 k 組；engagement 的 why= bug 本來就是
  ace 正在解的那條。
- (b) **B 線接手**：ace 停手凍結，以現況 WIP 為 base 實作（不丟棄 ace 的 hunk）。
- (c) **先量 Δstep(k=1) 成對**（零源碼、盒子現在空）：
  同場跑 `prod-new:CGC_SEG_BATCH_FAST=1` vs 誠實臂（＋答案同位檢查），
  拿到「紅利有幾 ms」與「taken/mismatch 比率」兩個數 ⇒ 再決定要蓋 k 階梯還是直接走 k=1。
  （p ≈1 已被 00:15 長跑強烈暗示，故原本的「先量 p」拆併進這一步。）

## 7. 本檔不做什麼

- 不改 default 行為、不動 k=1 的語意、不改 proven 迴圈；
- 不 commit（本樹多人共用，未獲指示）；
- 不在 ace 未停手時改 `ggml-backend.cpp`／`llama-context.cpp`。
