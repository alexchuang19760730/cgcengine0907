# 設計：把段邊界的 drain 與 hook 重疊（裝置側柵欄，部分重疊）

- 日期：2026-10-02　｜　線：A　｜　Charter：`scripts/check/charters/exp-overlap-partial-2026-10-02.yaml`
- 狀態：**設計完成、未實作**（`ggml-backend.cpp`／`llama-context.cpp` 為共用碰撞面）

---

## 1. 紅利在哪（今日成對 A/B 的分步分解）

`ab_interleave.py --arms p25-gputime,p25-submit-ahead`（build `054fb22f04a0`，3 對）。
兩臂**都帶** `CGC_DECODE_PROFILE=1;CGC_GPU_TIMING=1`，所以分解直接來自同一批 log：

| term | base（正確順序） | ahead（racy 順序） | 差額 |
|---|---|---|---|
| **total** | **73.54** | **38.10** | **−35.44 ms/步** |
| **`wait`** | **65.20** | **25.67** | **−39.53 ms/步** ← 紅利 |
| `cb` | 2.86 | 8.35 | **+5.49** |
| `submit` | 4.44 | 4.06 | −0.38 |

（ntok=1 中位；步時比 **×1.930**；成對 t/s 中位比 **×1.799**。）

**⇒ 兩件事同時成立**：
1. 紅利**全在 `wait`**——那段 65 ms 的輪詢等待裡，約 40 ms 是 GPU 在邊界空轉。
2. **當初判死的兩項（hook CPU 6.5 ms、submit 0.75 ms）都不是紅利所在**，所以那個判死是量錯量。

## 2. 為什麼不能直接換順序（race 的確切內容）

`ggml-backend.cpp` 的段迴圈（`~2759` / `~2771`）：

```cpp
for (int i = 0; i < n_segs; i++) {
    if (submit_ahead && i + 1 < n_segs) submit_seg(i + 1);   // ← racy：先提交下一段
    if (i < n_as_found) { if (!hook_seg(i)) break; }         // 等 seg i 全完 → 跑 hook → 寫 remap leaf
    if (!submit_ahead && i + 1 < n_segs) submit_seg(i + 1);  // ← 正確：hook 之後才提交
}
```

- 兩者**工作量完全相同**，只換順序。
- race 的本質：`seg[i+1]` 的 `mul_mat_id` **消費** `remap leaf`，而 host 還在寫它。
  **修改一個 in-flight command buffer 所引用的 buffer 是 UB**（Metal 的契約）。
  原註解已載：「Submitting segment[i+1] before the hook writes its remap is a CPU/GPU race …
  stale remap -> garbage / non-deterministic output」。
- ⇒ 所以「早提交」本身沒問題，問題是**沒有任何機制保證「讀」發生在「寫」之後**。

## 3. 柵欄設計（MTLSharedEvent）

```
host:  submit seg[i+1]  ──► cb 首部編碼 encodeWaitForEvent(E, v)   ← GPU 在此停住，但不佔 CPU／不 spin
       wait seg[i]（CPU 輪詢，照舊）
       hook(i)（讀 ids、ensure、寫 remap leaf）
       signalEvent(E, v)                                          ← GPU 立刻續跑
```

- **正確性**：`mul_mat_id` 讀 leaf 之前，事件一定已被 signal，而 signal 在 leaf 寫完之後
  ⇒ 讀必然在寫之後 ⇒ **結構上 bit-identical**（不需要預測、不需要回退）。
- **重疊**：`wait seg[i]` 與 `hook(i)` 期間，GPU 已有 seg[i+1] 就緒等待 ⇒ 邊界不再空轉。
- **代價**：`cb` 會上升（今日 racy 臂 +5.49 ms/步）——這是併發提交的 encoding 成本，需在 k 掃描裡量。
- **不佔 CPU、不 spin**：`encodeWaitForEvent:value:` 是 Metal 的標準 host→GPU 依賴原語。

## 4. 插入點（逐條核實）

| # | 檔案:行 | 現況 | 改動 |
|---|---|---|---|
| 1 | `ggml-metal-context.m`（`struct ggml_metal`，`~:74`） | 有 `n_cb` 等欄位 | 加 `id<MTLSharedEvent> cgc_fence_ev; uint64_t cgc_fence_v;` |
| 2 | `ggml-metal-context.m`（`ggml_metal_graph_compute` 的 cb 建立處，`~:564` 取 `n_bufs = n_cb + 1`） | 直接 encode | 當 `cgc_fence_v > 0` 時，對**第一個** cb 編碼 `[cb encodeWaitForEvent:ev value:v]` |
| 3 | `ggml-metal-context.m` 新增 C-API | 無 | `ggml_backend_cgc_fence_set(backend, uint64_t v)`（host 端記值）＋ `ggml_backend_cgc_fence_signal(backend, uint64_t v)` |
| 4 | `llama-context.cpp:545-566`（`CGC_N_CB` 的 proc-address 慣例） | 用 `ggml_backend_reg_get_proc_address` 取 `set_n_cb` | 照抄這條路取 `cgc_fence_*`（**不要**直接 include Metal header） |
| 5 | `ggml-backend.cpp:2759/2771` | `submit_ahead` 布林 | 新增 `CGC_OVERLAP_FENCE=<k>`：走 racy 順序，但在前 k 個邊界對 `submit_seg(i+1)` 設柵欄值 |
| 6 | `llama-context.cpp` 的 `expert_cache_on_topk`（寫 remap leaf 的尾端） | 寫完 leaf 就 return | **寫完 leaf 後**呼叫 `fence_signal(邊界序號)`。⚠ 位置必須在 `ggml_backend_sched_alloc_graph` **之後**（見 `:3916-3919` 的教訓） |
| 7 | `scripts/run_server.sh:1535` 通用轉發清單 | 已含 `CGC_SEG_BATCH*` | 加 `CGC_OVERLAP_FENCE`（同一個 inert-knob 陷阱，先加免得白跑） |
| 8 | `scripts/check/m123_oracle_gate.py` `DIAGNOSTIC_KEYS` | 已有 `CGC_SEG_BATCH_FAST` 條目 | 加 `CGC_OVERLAP_FENCE` 條目，**聲明哪些位元組不變**（提交順序變、leaf 值與權重集合不變） |

## 5. 部分重疊（k 掃描）——本卡的核心設計

全覆蓋（k=41）可能被 `cb` 的增量吃掉（racy 臂已見 +5.49 ms/步）。所以**旗標吃一個整數 k**：

- `CGC_OVERLAP_FENCE=0` ⇒ 現況（**天然控制臂**，同一支 binary）。
- `k=2, 8, 41` ⇒ 前 k 個邊界用柵欄，其餘走原順序。
- 量「收益 vs k」。**部分重疊從未被單獨試過**——三次失敗（L20-1／S3／S1SS）都在嘗試全覆蓋或以預測取代整段。

⚠ 邊界序號用**每步重置的計數器**（`i` 在 0..n_segs−1），不是全域累計——否則跨步的值會對不上。

## 6. 為什麼前三次都不適用（避免重複走）

| 嘗試 | 方法 | 撞牆處 |
|---|---|---|
| L20-1（我的 FAST） | prev-token 預測 ＋ 提交前 ensure ＋ 單提交 | 位置級全步命中率 0 ⇒ 預測結構性死 |
| S3（40 段→1） | 段數收成 1 | 10-01 被 P2 手術移除（R6-DIRTY）；且每段間的 ensure 是必需的 |
| B 線 S1SS | 設備端算 slots（S1 圖） | M1 0/8、更慢 |

共同點：**都想消掉「每段之間的 host 動作」**。本卡反過來——**保留全部 host 動作，只讓 GPU 不要等它**。

## 7. 量測前置（operator 已定）

- **先重開機**：今日 swap 峰值 6.2 GB、本體 tg 由 12.3 掉到 10.35；不重開機，k 掃描的步時差會被足跡淹沒。
- 入口：`harness bench`（k 掃描）＋ `ab_interleave.py`（成對）；壓縮機 BUSY 就輪詢等 QUIET。
- 柵欄臂的 `fence_signals>0` 是**量具活著**的判據（同 `answer_md5` 守門的作用）。

---

## 8. 後續：「leaf-only fencing」可行性檢查（2026-10-02 晚；設計完成、**未實作**）

operator 指示：×1.218 只是「encode/submit 離開關鍵路徑」那一半，要摸到 25 得拿回 racy 的全額
×2.04 ⇒ 把柵欄精細化到「只柵欄 leaf 依賴的部分」。本節把這個命題先證偽、再給可行變體。

### 8.1 結論（先講）
**照字面做不可行，且理由不在實作而在圖結構**：下一段沒有「可供早跑的 leaf-independent 前綴」，
因為**跨邊界殘差把整段綁在 remap leaf 上**。真正 leaf-independent 的只剩路由簿記與**共享專家**，
後者是唯一夠大、值得投的一塊（元素數為 routed 有效量的 **12.5%**；步時佔比待儀器量，見 8.4）。

### 8.2 證據一：下一段的絕大部分遞移依賴 leaf
- 邊界＝`ffn_moe_argsort-<il>`（`ggml-backend.cpp:1843-1880`）⇒ `seg il+1` =［層 il 的 MoE 餘部
  ＋ 層 il+1 的 attn／pre-FFN］。
- 跨邊界殘差（`src/llama.cpp/src/models/qwen35moe.cpp`，逐行）：
  - `:187` `inpSA = inpL`　→　`:243` `cur = build_layer_attn(...)`　→　`:252` `cur = ggml_add(cur, inpSA)`
  - `:256` `ffn_residual = cur`　→　`:279` `cur = build_layer_ffn(attn_post_norm, il)`　→　`:283` `cur = ggml_add(cur, ffn_residual)`　→　`:290` `inpL = cur`
  ⇒ 層 il+1 的 `attn_norm` 吃的是**層 il 的 post-FFN 殘差** ⇒ 依賴 `moe_out-il` ⇒ 依賴 remap leaf。
  **是遞移依賴，不是快取效應。**（原 §5 的難點 ①「消費節點太靠前 ⇒ 可早跑的部分短」被證實，且比原本想的更糟：不是短，是沒有。）
- remap leaf 的讀者＝`ffn_moe_gate_up`／`up`／`gate`／`down`（`mul_mat_id`：`llama-graph.cpp:2591`
  `mm_id_ids = remap_ids`，消費於 `:2638/2658/2671/2853`）⇒ 消費者在段的**最前面**。

### 8.3 證據二：真正 leaf-independent 的只有兩塊
1. **路由簿記**：`ffn_moe_topk-*`、`ffn_moe_weights-*`（~5 節點，太小）。
2. **共享專家**：`build_ffn(cur, ffn_up_shexp, …)`（`qwen35moe.cpp:598-620`）—— 它的輸入是
   **MoE 之前的 `cur`**（`:622` 才 `ggml_add(moe_out, ffn_shexp)`），**與 routed experts／leaf 無關**
   ⇒ 可以在 `seg il` 的 drain 與 hook 期間就跑。

### 8.4 尺寸：從 GGUF 表頭算，不需跑模型
**真正在用的模型＝`models/gguf/Nail-Qwen3.6-35B-A3B-MTP-UD-IQ3_XXS-denseIQ4X.gguf`**
（operator 2026-10-02 指正；先前本節誤引 `Qwen3.6-35B-A3B-UD-IQ3_XXS.gguf`）。
兩者表頭**幾何完全相同** ⇒ 下面的比值不受影響：
`block_count 41`、`embedding_length 2048`、`expert_count 256`、`expert_used_count 8`、
`expert_feed_forward_length 512`、`expert_shared_feed_forward_length 512`；
`blk.0.ffn_{up,gate}_shexp.weight [2048,512]`、`blk.0.ffn_down_shexp.weight [512,2048]`。

- 共享專家元素數/層 = `3·2048·512` = **3.15 M**；routed 有效 = `8·3·2048·512` = **25.17 M**
  ⇒ **共享 = routed 有效量的 12.5%**。
- ⚠⚠ **這是「元素數比」，不是「步時佔比」，而且它是上界**。本引擎的 decode 是 **IO-bound**
  （routed experts 由 expert cache pool／SSD 串流，共享專家是常駐 dense ⇒ 讀的是 RAM 不是 SSD），
  所以「12.5% 的元素」不等於「12.5% 的步時」。**步時佔比只能由儀器量出來 ＝ D1 的唯一目的。**
- 保守區間：把「MoE 相關 GEMV 佔解碼 GPU 忙碌 25–65%」（DECPROF 量過的上界帶）乘上
  「共享專家佔 MoE 元素 12.5%」⇒ 步時 **3–8%**；`14.32 × 1.03…1.08 ≈ 14.7–15.5 t/s`：
  **有機會**越過 charter 的 ≥15，但不是保證（相乘是估計，不是量測）。

### 8.5 為什麼「只柵欄 leaf 消費者」在**現行**實作下拿不到
- 柵欄是**整段粒度**：`encodeWaitForEvent` 對該 `graph_compute` 的**每一個** cb 編碼
  （`ggml-metal-context.m:1577`、註解 `:1254`）。
- cb 是**連續節點區間**，且 `n_main = MAX(64, 0.1·n_nodes)`（`:1230-1233`）⇒ 對 ~50–110 節點的段，
  前 64 個節點都落在 main cb ⇒ 共享專家（圖序在 `moe_out` 之後）與 leaf 消費者**同一個 cb**
  ⇒ 要嘛全柵欄、要嘛都不柵欄。

### 8.6 可行設計（分三步，先便宜後貴）
- **D1（先行、便宜）**：把 `ffn_shexp` 加進 node-kind 前綴白名單（`ggml-backend.cpp:2352` 附近）。
  現況 `ffn_shexp*` 落進泛用 `ffn_` 桶（`ns_fix` 有 `"ffn_"` 這個 catch-all）⇒ **量不出來**。
  加一條之後，一次 `CGC_GPU_NODES` 診斷跑就直接給出共享專家的真實 GPU 佔比，
  **用來驗收 8.4 的 7–8% 預測**（診斷讀數是比值 ⇒ 不受窗口髒不髒影響）。
- **D2（若 D1 ≥5%）**：**讓共享專家早跑**——
  (a) 段內**新增一個切點**（`ffn_shexp`），使〔`ffn_shexp`, `shared_expert_gate`, `ffn_shexp_gated`〕
      成為一個**不含 remap 讀者**的獨立段；
  (b) 排程器對「不含 remap 讀者的段」**不編柵欄**、但仍**早提交** ⇒ 它在 `seg il` 的 drain 期間就跑；
      `ffn_out`（`= add(moe_out, ffn_shexp)`）之後改由 Metal 自己的 hazard tracking 擋
      （racy 臂已證跨 cb 資源追蹤可用：它只有 remap 髒，其餘張量正確）。
  - 不需要 mid-encoder wait：cb 級柵欄已足夠。先前記憶裡「插 wait 會全 NaN」**已查明是 prefill 被柵欄**
    造成（`ggml-backend.cpp:2196-2199`：`CGC-MMID-ASSERT id_oob` ＋ all-NaN），**不是 wait 本身**。
- **D3（若 D2 達標）**：S1（裝置端算 leaf ＝ 消滅 host round-trip）。它與重疊家族**相加**，不是替代。

### 8.7 預註冊（發車前寫死；本節＝立項依據）
- 子目標節點 id：`exp-overlap-leafonly`（新立項卡；**未發車**）
- 控制臂 `prod-new`　／　處理臂 `prod-new:CGC_OVERLAP_FENCE=41:CGC_LEAF_SPLIT=1`（D2 的新旗標）
- **預期 decode 14.6–15.5 t/s**（上界＝共享專家的 12.5% 元素比；真實值待 D1 定尺寸）；
  `wait` 中位再降 0–6 ms/步；`cb` 增幅 <2 ms/步
- 驗收：M1/M2/M3 **9/9** ∧ **≥15.0 t/s** ∧ `CGC-OVERLAP-FENCE … signalled>0`
- 否證：M1 ≤2/9，**或** <14.32（連共享專家的量級都沒拿到）⇒ 判死本軸，改投 S1
- ⚠ 若 D1 量出共享專家佔步時 <2% ⇒ 先把 D2 的成本與收益並列再決定是否實作（可能不值得動 src）。

### 8.8 對「25」的路徑校正（重要）
`12.377 × 2.04 = 25.2` **不是** bit-identical 設計可及的目標：2.04 需要**沿 stale leaf 全速跑完整段**
（charter §hypothesis 自己載明 racy 臂輸出是垃圾、且提前終止）。保 bit-identity 的天花板要由
**(i) 邊界 drain 的重疊 ＋ (ii) 消滅 host round-trip（S1）** 相加得到，不是乘 2.04。
⚠ 因此「leaf-only fencing 拿回全額」的期望值應下修：能拿回的是**共享專家那一塊（~7–8%）**，
不是 racy 差額的一半。

---

## 9. D1／D1b 實測（2026-10-02 16:19–16:24）：共享專家 6.9%，邊界空轉中位 7.6 ms/步

### 9.1 D1：共享專家佔解碼 GPU 忙碌 ≈ **6.9%**
儀器修正：`ggml-backend.cpp` 的 kind 白名單加 `"ffn_shexp"`（原本落進 catch-all `ffn_`，與
`ffn_norm`/`ffn_out` 混在一起 ⇒ 量不出來）。D5：**M1/M2/M3 9/9、full_fnv1a64 9/9**（純列印改動）。

⚠ **命名陷阱（先踩到才發現）**：`build_ffn`（`llama-graph.cpp:1757/1905`）把共享專家的三條 GEMM 命名成
**`ffn_up`／`ffn_gate`／`ffn_down`**，只有**返回張量**被外層 `cb(ffn_shexp,"ffn_shexp",il)` 改名
⇒ `ffn_shexp` 這個桶**只含 down 那一條**，只讀它會低估到 1/3。加總（穩態表，`wcntw`＝work-weighted）：

| bucket | wcntw | cntw |
|---|---|---|
| `ffn_up` | 1.0% | 1.0% |
| `ffn_gate` | 1.0% | 1.0% |
| `ffn_shexp`（＝down） | 2.8% | 1.8% |
| `shared_expert_gate`（含 sigmoid） | 2.1% | 1.9% |
| **合計** | **≈6.9%** | **≈5.7%** |

產物 `Backup/l201_accel/leafonly_d1/{d1.json,d1.stderr.log}`（`attribution=swap` ⇒ **t/s 不可引用**）。
與 §8.4 推算自洽：元素比 12.5% × MoE 佔 busy ~50% ≈ 6.3% ⇒ **IO-bound 的警告成立（常駐權重比 SSD 串流便宜），
但量級對**。D1 判別句（≥5% ⇒ 進 D2）**成立**。

### 9.2 D1b：邊界 GPU 空轉 **中位 7.6／均值 10.4 ms/步**，且 `gap ≈ hook`
產物 `Backup/l201_accel/leafonly_d1b/{d1b.json,d1b.stderr.log}`（同為 `attribution=swap`，不引用 t/s）。
`CGC-GPUTIME`（ntok=1）11 筆 `gap`：**7.57 / 2.12 / 3.51 / 2.78 / 44.36 / 7.37 / 16.48 / 9.15 / 0.00 / 9.05 / 12.30 ms**
（已剔除 warmup 的 542.96）⇒ **中位 7.57、均值 10.4 ms/步**。

★ **機制**：`gap` 與 `bdhook`（hook 的 CPU 時間）同步 —— 44.36↔41.52、16.48↔16.67、12.30↔16.67；
而 `bdsub`（提交）穩定在 **3.1 ms/步**。⇒ **空轉就是「seg[i+1] 等事件、而事件要等 hook 寫完 leaf」的那段**，
不是隨機噪聲。同批 `bdcpu`（邊界 CPU 包絡）＝ 7–20 ms/步，**大於或等於 gap** ⇒ CPU 側的邊界工作已被 GPU
執行蓋掉大部分 —— **柵欄設計其實已經把非 compute 時間疊進了 compute 的絕大部分**。

### 9.3 判詞（對照 §8.7 預註冊）
- 預註冊兩分支：gap **≥8 ms/步 ⇒ D2**；**<3 ⇒ 改投 S1**。實測 **均值 10.4 ≥ 8（中位 7.57 貼線）**，
  且機制判準（`gap ≈ hook`，落點就是 hook 那段）**支持 D2** ⇒ 判 **D2 開工**。
- **預期上修到 15.0–15.5 t/s**（共享專家 5.5 ms/步 ≤ gap 10.4 ms/步 ⇒ 落點足夠），
  但要扣 `cb` 增量（設計上限 +2 ms/步）⇒ 淨值 **14.9–15.4**。
- 驗收維持 **M1/M2/M3 9/9 ∧ decode ≥15.0 t/s**；**否證**：M1 ≤2/9，或 <14.32（連落點都沒吃到）。
- ★ 對「把所有非 compute 疊掉」的總結：**能疊的兩塊＝(i) 邊界空轉（中位 7.6 ms/步，柵欄已吃掉一部分）
  ＋ (ii) 與 leaf 無關的段內工作（唯一夠大的是共享專家 6.9%）**；其餘非 compute 是**代價**（`cb`，
  重疊越多漲越多）或**真依賴**（host round-trip，41 次/步）。⇒ 把 (i)+(ii) 吃滿仍是**單步幾毫秒**的量級，
  **不可能靠重疊到 25**；要 25 只能靠 S1 把 round-trip **消滅**。

---

## 10. D2 量測（2026-10-02 16:37–16:49）：**UNDECIDABLE** —— 本窗口的雜訊大於效應

### 10.1 兩批量測
**第一批（設計失誤：只有控制與 D2）** `Backup/l201_accel/leafonly_d2/`

| 趟 | 臂 | tg | 逐 rep | attribution |
|---|---|---|---|---|
| 0 | 控制 | 11.435 | [11.4544, 11.5353, 11.3168] | swap |
| 1 | D2 | 14.323 | [14.1524, 14.2114, 14.6065] | thermal |
| 2 | D2 | 13.375 | [12.5176, 14.0211, 13.5857] | thermal |
| 3 | 控制 | 11.441 | [11.2895, 11.6711, 11.3638] | both |

⇒ 這一場**沒有「只開柵欄」臂**，所以唯一要回答的「D2 相對柵欄的增量」**在結構上答不了**。中途讀到的
+4.5% 在四臂齊了之後反轉 —— 這正是**單對會被臂序與漂移騙**的實例。落 lesson `eng-mh-0078`。

**第二批（ABBA：fence / fence+D2 / fence+D2 / fence）** `Backup/l201_accel/leafonly_d2x/d2x.json`

| 趟 | 臂 | tg | 逐 rep | 臂內 spread |
|---|---|---|---|---|
| 0 | fence | 13.3727 | [14.2357, 13.2278, 12.6546] | 1.125 |
| 1 | fence+D2 | 13.2936 | [13.9371, 13.8687, 12.0751] | 1.154 |
| 2 | fence+D2 | 13.9758 | [14.7466, 14.2813, 12.8997] | 1.143 |
| 3 | fence | 14.4699 | [14.8057, 14.0505, 14.5535] | 1.054 |

- **配對**：A(fence) = {13.3727, 14.4699} → 13.921；B(fence+D2) = {13.2936, 13.9758} → 13.635。
  配對差 **−0.287 t/s**（兩對各 −0.079／−0.494；sd 0.293、t=1.38）⇒ **遠在臂內散布（±0.48–0.78）之內**。
- **臂序漂移可見且不小**：四臂均值 **13.37 → 13.29 → 13.98 → 14.47**（單調爬升約 +0.9 t/s）
  ⇒ 任何單對比較都會被漂移主導。
- **4 趟全部 `attribution=both`（thermal ＋ swap）** ⇒ 絕對值一律 **DIRTY**、不可引用。

### 10.2 判詞
對照 §8.7 預註冊（M1/M2/M3 9/9 ∧ ≥15.0；否證 <14.32）：
- **M1/M2/M3 9/9 成立**：`leafonly-d2-ctl` 與 `leafonly-d2-split` 兩趟皆 PASS、`config_diffs=[]`
  ⇒ 拆分路徑逐位元等同（這是本節唯一「已證」的一條）。
- **≥15.0 不成立**；但**否證條件也不乾淨成立** —— 因為柵欄臂自己在這個窗口就已從乾淨窗的 14.32
  掉到 13.4–14.5，所以量到的「<14.32」是**窗口惡化**，不是 D2 的成本。
- ⇒ **判 UNDECIDABLE（本窗口證據不足）**：效應量（若 6.9% 全落地 ≈ +0.9 t/s）小於本窗口的
  臂內／臂間散布。**既不是「D2 無效」，也不是「D2 有效」**——是這台盒子當下的雜訊不允許回答。
- **後續（唯一正路）**：在**重開機後的乾淨窗**重做同一組 ABBA（fence vs fence+D2），並同時開
  `CGC_GPU_TIMING=1` 確認 `gap` 是否真的被填掉 —— **機制層的獨立證據比 t/s 抗噪**（若 gap 沒降，
  就是拆分的 B 段根本沒有早跑，那答案與 t/s 無關）。在那之前 `CGC_LEAF_SPLIT` **維持預設關閉**，
  且**不得**在板上寫任何增量數字。

---

## 11. D2 機制見證（2026-10-02 16:5x）：**治療確實施加了，但代價大於收益 ⇒ D2 判不划算**

先證明「治療真的施加了」（charter §2 記過 `F2／exp-s2-overlap` 是 treatment-application null，所以
在讀任何 gap／t-s 之前必須先有見證）。改動是**純列印**：`ggml-backend.cpp` 加 `static int64_t cgc_split_n`，
`do_split` 時自增，並在既有 DECPROF 行加 `splits=%lld`（累計）。

臂：`…CGC_GPU_TIMING=1;CGC_DECODE_PROFILE=1`（fence）vs 同＋`CGC_LEAF_SPLIT=1`（fence+D2），
**同一批、同 reps=3**。產物 `Backup/l201_accel/leafonly_d2w/`。

### 11.1 見證與代價（同 step 對照）

| 量 | fence-only | fence+D2 |
|---|---|---|
| `splits=`（DECPROF，累計） | **0** | **13845 → 14781（遞增）** |
| `submit` | 3.1–3.7 ms/步 | **9.0–23.9 ms/步** |
| `cb` | 2.3–8.9 ms/步 | 10.2–24.7 ms/步 |
| `total` | 56.5–63.0 ms/步 | 76.2–84.5 ms/步 |

- ⇒ **治療確實施加了**（`splits` 遞增；且 fence-only 臂為 0 ⇒ 這個欄位是真的在區分兩臂，不是擺設）。
- ⇒ **代價明確**：拆分讓 41 個柵欄邊界各多一次 `graph_compute`，`submit` 淨增 **約 5.5–20 ms/步**
  （每邊界 ≈ +130 µs）。**這已經超過共享專家的 6.9%（≈5.5 ms/步）。**
- ⇒ 因此 ABBA 的 −0.287 t/s 不是「窗口雜訊」而已：**機制層上它就是淨負或最多持平。**

### 11.2 更重要的：D2 的機制前提本身是錯的
`gap`（§9.2）是在**未開柵欄**的臂上量的（`prod-new:CGC_GPU_NODES=1;CGC_DECODE_PROFILE=1;
CGC_GPU_TIMING=1;CGC_CB_N_MAIN=1`）。**而柵欄的工作就是填掉那段邊界空轉** —— 那正是它 ×1.218 的來源。
⇒ 對一個**已經開柵欄**的段再拆一次，**沒有落點可吃**；剩下的序列化是 host round-trip（真依賴），
不是閒置。D2 的 `+6.9%` 樂觀預期建立在「落點在柵欄之後仍然存在」這個未經量測的假設上。

### 11.3 ★ 順手發現：`CGC_GPU_TIMING` 的 `gap` 在**開柵欄時失效**
見證臂（fence on）的 GPUTIME 行是 `segs=1 bufs=9 skipped=351 skip_nc=351`，
而 §9.2（fence off）是 `segs=40 bufs=355 skipped=5`。
根因：該儀表在 hook 裡讀 `ctx->cmd_bufs[]`，其註解自己寫著前提是
**「segment i+1 has not been submitted yet, so nothing else can be in flight」** ——
**柵欄恰恰把這個前提破壞**（i+1 早在 hook 之前就提交了）⇒ 它讀到的是在途緩衝 ⇒ `skip_nc` 爆表、
`gap` 讀成 0.00。**⇒ 開柵欄後不得再引用 `gap`／`gpu_busy_sum`**（`gpu_union`／`layer gpu_sum` 亦同）。
這與 `CGC_LEAF_SPLIT` 無關，是**柵欄本身的既有代價**，本日才被顯露。

### 11.4 判詞
- **D2：判不划算（淨負）**。保留 `CGC_LEAF_SPLIT` 於**預設關閉**、並在樹上留作**已文獻化的否定**
  （同 `CGC_SUBMIT_AHEAD` 的處置）；`seg_cb_end[]` 這個 cb 計數泛化**保留**（D5 兩趟已證對舊路徑零影響，
  且任何未來的拆分都需要它）。
- **這一軸（重疊家族）到此收在**：柵欄 ×1.218（乾淨窗、`attribution=swap` 不可引用）是**已量到的上限**；
  leaf-only 精細化**無效**（機制已定位）。要再往上只能走 **S1（裝置端算 leaf）把 host round-trip 消滅** ——
  那是「消滅」不是「重疊」，且與本軸可相加。
- ⚠ 這一條也**改寫了 §8.7 的預註冊結論**：D2 的否證條件（<14.32）之所以看起來不成立，是因為
  它量的是窗口惡化；**真正的否證來自機制（代價 > 收益）**。
