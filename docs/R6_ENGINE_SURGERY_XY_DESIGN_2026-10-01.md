# ═══════════════════════════════════════════════════════════════════════════
# R6 引擎手術 · 修法設計書（X＝池 re-centre / Y＝逐層重算）
# 只讀摸點產出，未改任何程式碼。選 X/Y（或組合）再動核心路徑。
# ═══════════════════════════════════════════════════════════════════════════
# 關聯：charter `e-r6-engine-surgery-2026-10-01.yaml`、docs/R6_A4_VERDICT_2026-10-01.md
#       工作日誌 .workbuddy/memory/2026-10-01.md（§TB-(B)）
# 全部行號以 src/llama.cpp/src/llama-context.cpp 為準，除非另標檔名。
# ───────────────────────────────────────────────────────────────────────────

## 0. 根因（a7 已活體坐實，作為兩條修法的共同前提）

單段提交臂 `CGC_SEG_BATCH=1;CGC_B_SCHEME=1;CGC_SLOT_TABLE_GPU=1` 下：
- 256 專家 / 層，池只有 143 slot ⇒ 113 非駐留專家/層。
- 提交前（`process_ubatch` 的單段路徑）用 `llama-context.cpp:3741-3882` 的 **預提交 block**
  寫每層 slot table：駐留 e → 其真 slot；否則若 ZERO-slot 已 arming → `v=zs`（0 權重，有界錯）；
  **否則 `v = e % ns`（合法但指向別人權重的錯位 slot）**。
- a7 實測 `CGC-G3-ZEROSLOT-TOTAL: zero_slot=0 placeholder=250582` ⇒ 全走 `e%ns` 占位 ⇒
  GPU gather 讀錯專家 ⇒ logits 發散 ⇒ **M1 0/9**。
- 根因層級：**per-layer hook 被 SEG_BATCH 跳過**（`expert_cache_eval_cb` / `expert_cache_on_topk`
  在 SEG_BATCH 下不觸發，見 a4 證死），所以「本步要哪些專家、池要不要 re-centre」這條資訊鏈斷了。

> 關鍵結構性約束（決定 X 與 Y 的上限）：
> 預提交 block（3741）在**提交前**就用「當前池駐留」寫 table。re-centre feed 只能餵「本步路由」，
> 而本步路由要等圖跑完才知道 ⇒ re-centre 永遠**落後一步**（prev-token 預測本質）。
> 因此純 X 無法在物理上消除第 N 步的占位，只能讓第 N+1 步少一點。

---

## 1. 兩條修法定位

| | Fix X：池 re-centre | Fix Y：逐層重算 |
|---|---|---|
| 目標 | 讓非駐留專家變駐留，使預提交 table 不再寫 `e%ns` | 提交後偵測「哪些層讀到占位」，對那些層用正確 routed ids 重跑 FFN |
| 對應 charter | 方案 A（已證死方向）+ P1 預測 | charter `e-r6-engine-surgery` 主路（prev-token + 逐層重算） |
| 消除占位 | 減少（落後一步，殘留仍在） | 完全（重算覆寫錯 activation） |
| 是交付物？ | 否（輔助，壓低 mismatched 集合） | 是（charter 主路 + D5 閘 M1≥8/9） |
| 核心路徑風險 | 中（改 feed/預取時序） | 高（圖跑完後再 splice 重算子圖） |

**charter 的真實設計 = Y 主體 + X 的 P1 預測當作「預壓縮 mismatched 集合」**。兩者不是二選一，
而是「X 讓 Y 要重算的層數變少」。本書把兩者落點都摸清楚，由你決定投資配比。

---

## 2. Fix X — 池 re-centre 的程式碼落點

### 2.1 re-centre 的消費端（已存在，正常臂在用）
- `cache_step_union[il]`（`llama-context.cpp:8415` 寫入）= 每步每層「被路由到的專家集合」。
- 消費端 `process_ubatch`（`:2217`、`:2255`）把它推進 prefetch 佇列（B-section），
  背景 `prefetch_slot` 把權重載入池 ⇒ 下一步 `ensure_batch` 命中、不堵 disk。
- 這條鏈在**正常臂**由 per-layer hook 餵（`expert_cache_on_topk`，`:4509` 註解明言寫 `cache_step_union`）。

### 2.2 SEG_BATCH 下已有的兩個 re-centre feed scaffold（**均未在 M1 臂驗證**）
1. **`CGC_RB_FEED` feed**（`:4174-4215`）：註解自稱「single-submit membership feed」。
   - 門控 `cgc_rb_seg_batch && (cgc_miss_mask_dbg || cgc_rb_feed)`。
   - 從 `ibuf`（圖回讀的 routed ids）餵 `spac_update`（`:4202`）+ `cache_step_union`（`:4208`）。
   - ⚠ 致命疑點：`ibuf` 正是 a7 裡 `CGC-SEG-RECOMPUTE: first=-1123126721` 的 garbage 來源
     （Phase 0+1 抓的是 transient `ibuf` 非真 `sbuf` routed ids）。**若 ibuf 是垃圾，這個 feed 餵的是垃圾**。
2. **`CGC_SEG_RECENTRE` feed**（`:6453-6477`）：門控 `CGC_SEG_BATCH && CGC_SEG_RECENTRE`。
   - 從 `ids`（host snapshot，比 ibuf 可靠）寫 `cache_step_union[il]`。
   - 註解明言「the deliverable SEG_BATCH arm opts in via CGC_SEG_RECENTRE=1」——**這才是 X 的正規開關**。
   - ⚠ a7 沒帶 `CGC_SEG_RECENTRE`，所以**這個 feed 從未在 M1 臂跑過**。

### 2.3 `spac_update` 的真相（a3 已證，這裡重複以定位）
- `llama-expert-cache.cpp:2319`：只做 `u[e] *= alpha; u[e] += bump`——**只改 SpAc EMA 熱度陣，
  從不把權重載入池**。它餵 `spac_prefetch`（`:2355`）間接 re-centre，但 itself 不載入。
- 結論：X 的效力**不來自** `spac_update`，而來自 `cache_step_union` → `process_ubatch` prefetch 真的載入。

### 2.4 Fix X 落地要改/驗的點
| 動作 | 落點 | 風險 |
|---|---|---|
| 在 M1 臂開 `CGC_SEG_RECENTRE=1` 跑一次，看 `CGC-SEG-UNION`（`:6471）與 `placeholder` 是否下降 | 實驗，`scripts/check/llama_bench_matrix.py --arms 'prod-new:CGC_SEG_BATCH=1;CGC_B_SCHEME=1;CGC_SLOT_TABLE_GPU=1;CGC_SEG_RECENTRE=1;CGC_S1_DBG=1'` | 低（只開關，不動邏輯） |
| 確認 prefetch 時序：第 N 步 union 是否在第 N+1 步預提交前完成載入 | `process_ubatch` B-section（`:2213`）與預提交 block（`:3741`）的相對順序/非同步視窗 | 中（可能的 1-step 落後導致仍有殘留占位） |
| 修 `CGC_RB_FEED` 的 `ibuf` garbage（若要用它） | `:4197-4200` 的 `routed` 來源 | 中（改錯來源會餵垃圾 re-centre） |
| 決定 X 是否足以單獨交付 | 看上一步 `placeholder` 是否降到 0 | — |

### 2.5 Fix X 的硬上限（必須誠實告知）
即便 X 完美，單段提交「先寫 table 再提交」的結構決定 re-centre **落後一步**；
compulsory miss（本步首次出現、上一步沒預測到的專家）仍會走 `e%ns`。
⇒ **X 單獨無法達 M1 9/9**，只能壓低 Y 要重算的層數。這也是 charter 把 X 當 P1 預測而非主路的原因。

---

## 3. Fix Y — 逐層重算的程式碼落點

### 3.1 偵測（哪些層讀到占位）—— 基礎已存在，SEG_BATCH 下要修
- **正常臂的 sel_wrong 差分儀**：`cgc_s1_equiv_dbg`（`:5812`）在 per-layer hook 內（`:8063`）
  呼叫，對每個 selected expert 比「table 實際寫的 slot」vs `slot_table_safe`（非駐留回 -1）。
  `tb[e] != want` ⇒ `n_mismatch++`，印 `CGC-S1: EQUIV-... mismatch=N`。
  ⚠ 這儀器在 SEG_BATCH 下**不會跑**（它在被跳過的 hook 內）；且它早退於 `tbl->data==nullptr`
  （`:5816`，GPU table 在 CPU 側不可見 ⇒ a7 裡 EQUIV 一行都沒出，是「量不到」非「無錯位」）。
- **SEG_BATCH 下的偵測 scaffold**：Phase 0+1 node-walk（`:4419-4490`，`CGC-SEG-RECOMPUTE` 見證行）。
  它 node-walk `gf` 找 `ffn_moe_topk-<il>` 想還原 routed ids，但 `:4442` 註解承認
  「ffn_moe_topk turned out to be float scores, not indices」⇒ **抓到的不是真 routed ids**（a7 `first=-1123126721` 實錘）。
  ⇒ SEG_BATCH 偵測目前**壞的**，要先修「從哪抓真 routed ids」。

### 3.2 重算（對 mismatched 層重跑 FFN）—— **目前完全未實作（stub-only）**
- `CGC_SEG_RECOMPUTE` 全碼只有 `:4433` 的見證 print，**沒有任何 recompute handler**（grep 全檔 0 命中）。
- charter `sg-eng-recompute`（`:94-97`）明寫「呼叫 ffn_moe 逐層 forward（待確認進入點）」——進入點至今未定。
- MoE forward 建圖在 `src/llama.cpp/src/llama-graph.cpp`（`ggml_mul_mat_id` 在 `:1572` 起；
  `ffn_moe_topk_remap` 葉建構在 `:2490-2575`；`mm_id_ids = remap_ids` 在 `:2576`）。
- **重算要解決的三個硬問題（設計書必列）**：
  1. **正確 routed ids 來源**：單段圖跑完後，從哪抓每層本步真 routed ids？
     `ffn_moe_topk-<il>` 是 float score 不是 index（`:4442`）；真 index 在 `ffn_moe_argsort-<il>` 或 gate 輸出。
     需確認後把正確 ids 讀回 host（`ggml_backend_tensor_get`）。
  2. **權重可見性**：重算層的專家必須在池裡（或從 disk 取）。若仍非駐留，重算讀到的還是錯權重 ⇒
     重算須先 `ensure`（`:8423` `llama_expert_cache_ensure`）把該層專家載入，再跑。
  3. **activation splice**：重算輸出要**寫回主圖該層 FFN 的 output tensor**（在 sampler 之前），
     否則重算白做。要定位該 tensor 在主圖 `gf` 中的指標並 `ggml_backend_tensor_set` 覆寫。

### 3.3 Fix Y 落地要改/驗的點（分三階段，對齊 charter Phase 0/1/2）
| 階段 | 落點 | 產出 |
|---|---|---|
| P0 修偵測 | `:4419` node-walk 改用正確 routed-id 來源（argsort/gate 輸出）；或把 `cgc_s1_equiv_dbg`（`:5812`）改成可在 GPU-table 路徑讀回 | `CGC-SEG-RECOMPUTE` 列出**真** mismatched 層（非 garbage） |
| P1 見證 | 現有 `:4433` 見證行擴成印 per-layer `mismatch` 集合 | 確認「重算真的有對象」 |
| P2 重算（高風險） | 在 `process_ubatch` 單段提交後、`synchronize` 之後插入：對每 mismatched 層 ensure+建 MoE 子圖+跑+寫回 activation | `CGC-SEG-RECOMPUTE` 見證行 ≥1 且 M1≥8/9 |

---

## 4. 關鍵風險 / 未決問題（選 X/Y 前必讀）

1. **`ibuf` garbage（影響 X 的 `CGC_RB_FEED` 與 Y 的 P0 偵測）**：a7 的 `first=-1123126721` 證實
   SEG_BATCH 下從 `ibuf` 抓的 routed ids 是 transient 垃圾。Y 的偵測若要正確，必須先解這題
   （改用 `ffn_moe_argsort-<il>` 或 gate 輸出的真 index）。
2. **1-step 落後（影響 X 上限）**：見 §0 結構約束，X 單獨到不了 M1 9/9。
3. **re-centre 時序（影響 X 效力）**：`cache_step_union`→`process_ubatch` prefetch 是否在第 N+1 步
   預提交（`:3741`）前完成載入，決定 X 降多少占位。
4. **重算 activation splice（影響 Y 成敗）**：寫回錯 tensor 就無效；這是 charter 留白、全碼未做的部分。
5. **D5 閘（硬規則）**：引擎改動必自立項＋`m123_oracle_gate --profile prefill250`，
   M1/M2/M3 9/9（≥8/9 鬆綁 R6-DIRTY）、`config_diffs==[]` 才可交。兩條修法都過這閘。

---

## 5. 推薦路徑（僅建議，不代你決定）

- **Y 是 charter 主路、也是唯一能達 M1 9/9 的路**；但 P2 重算是高風險核心改動，須先過 P0/P1 把偵測做對。
- **X 不該單獨投**，但它（`CGC_SEG_RECENTRE`）是 charter 的 P1 預測，能壓低 Y 的 mismatched 層數、
  間接降重算成本。**建議組合：先開 X 的 `CGC_SEG_RECENTRE` 量一次（零邏輯風險），
  再把 Y 的 P0 偵測修對，最後動 P2 重算。**
- 若你只選一個先投：**選 Y 的 P0/P1（偵測修對）**——它風險低（只讀/印），卻能證明「占位偵測可行」，
  是 Y 全部價值的前置；X 的測量可與之並行。

---

## 6. 驗證矩陣（兩條都先只開關、不動邏輯來量）

| 實驗 | 臂 | 看什麼 | 判讀 |
|---|---|---|---|
| X 測量 | `prod-new:CGC_SEG_BATCH=1;CGC_B_SCHEME=1;CGC_SLOT_TABLE_GPU=1;CGC_SEG_RECENTRE=1;CGC_S1_DBG=1` | `CGC-SEG-UNION`（`:6471`）有無、`CGC-G3-ZEROSLOT-TOTAL` 的 `placeholder` 是否下降 | 下降⇒X feed 有接上；仍 >0⇒殘留（預期，因 1-step 落後） |
| Y 偵測修對前 | 同上臂 + 修 `:4419` 用正確 routed-id 來源 | `CGC-SEG-RECOMPUTE` 的 `mismatch` 是否變成合理值（非 -1123126721 類垃圾） | 合理⇒偵測可做；否則先解 §4.1 |
| Y 重算（P2，高風險） | 同上臂 + `CGC_SEG_RECOMPUTE=1` + P2 實作 | `CGC-SEG-RECOMPUTE` ≥1 行且 `m123` M1≥8/9 | charter success |

> 所有實驗都用 `scripts/check/llama_bench_matrix.py`（t/s 口徑不引用）或 `m123_oracle_gate --profile prefill250`（位元口徑）。
> 模型 13G+池 8G > 16G 實體，需 `BUDGET_GATE=off CGC_INTERNAL_CALL=1` 關整體超訂閘。

---

## 7. 實測修正（a8，2026-10-01）—— §5/§6 的「X feed 雙臂都觸發」假設推翻

- **a8 結果**：臂 `CGC_SEG_BATCH=1;CGC_B_SCHEME=1;CGC_SLOT_TABLE_GPU=1;CGC_SEG_RECENTRE=1;CGC_S1_DBG=1` 跑完，`CGC-SEG-UNION` = **0 行**，`placeholder=250582` 與 a7（不帶 RECENTRE）**完全相同** ⇒ X 開關在交付臂零影響。
- **根因**：`CGC_SEG_RECENTRE` feed 在 `expert_cache_on_topk`（llama-context.cpp:6453-6477），而 SEG_BATCH 單段提交**不呼叫此 hook**（ggml-backend.cpp:1773「No hook is fired」）。鐵證：同臂 a7 的 `CGC-SEG-RECOMPUTE`（:4528，主計算函式）打 15600 行，但本 feed（:6472，hook 內）0 行。
- **§5「X feed fires in BOTH arms（含 SEG_BATCH）」之說法錯誤**（源自 :6447 註釋誤導）；X feed 在 SEG_BATCH 下是死代碼。
- **修正後落點（不動邏輯、只 relocate）**：X feed 與 Y P0 偵測都須從 hook 搬到 **pre-submit 函式（:3741-3882 區）**，該區在 SEG_BATCH 下會跑，且同時持有 `st`（真 slot 表 :3809）與 `td`（發布表 :3823）：
  - Y P0：寫表後比對 `td[e] vs st[e]` 數 `sel_wrong`（現有 `cgc_s1_equiv_dbg` 邏輯正確但也在錯的 hook，:5812）；
  - X：寫表前用 prev-step routed ids 觸發 `cache_step_union`→prefetch→載入專家，使 `st[e]>=0` 不落入 `e%ns`。
- **`CGC-SEG-NODE`（1240 行）全是 float 當 int 讀的垃圾**（如 `1068274974`=float≈1.0 位元；`ffn_moe_topk-0` 的 `distinct=16382` 是 garbage 上算的假值）⇒ §6「Y 偵測修對前」想靠 node-walk 找真 routed ids **行不通**，必須改用 `st/td` 比對法。
- 下一步：relocate X feed + Y P0 到 pre-submit 區（核心路徑改動，需先用戶拍板），再發 a9 驗 `sel_wrong>0` 與 `placeholder` 降幅。
