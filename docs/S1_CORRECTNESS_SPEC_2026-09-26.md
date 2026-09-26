# S1 單段提交的正確性：障礙的**精確形狀**（2026-09-26，純靜態，0 GPU）

> 本檔是「Plan step 2」的第一段：把「單段提交為什麼 garbage」壓成一個可判定的命題。
> 尚未建置、尚未跑任何臂。所有引用都有 file:line。

---

## 1. 單段提交確切地繞過了什麼

`ggml-backend.cpp` 的 `CGC_SEG_BATCH=1` 分支是**早退**（在分段迴圈之前 return）：

```c
static const bool cgc_seg_batch = getenv("CGC_SEG_BATCH") != nullptr;
if (cgc_seg_batch) {
    enum ggml_status ec = ggml_backend_graph_compute_async(split_backend, &split->graph);
    if (ec != GGML_STATUS_SUCCESS) return ec;
    ggml_backend_synchronize(split_backend);
    return GGML_STATUS_SUCCESS;
}
```

被繞過的是整個 `for (i = 0; i < n_segs; i++)` 迴圈，其中三步全部消失：
1. **`wait`**（`cgc_done` 自旋等 segment i 跑完）
2. **`hook_seg(i)`** → `callback_eval(as_topk[i], …)` → **`llama_context::expert_cache_on_topk`**
3. **`submit_seg(i+1)`**

而 `n_segs = n_as_found + 1`（`ggml-backend.cpp:1845`），`n_as_found` 數的是
`ffn_moe_topk-<L>` 節點 ⇒ **segments ≡ 有 MoE 路由的層**。trunk 41 段（40 層 + tail）。

## 2. ★ 精確的失效鏈（這是本檔的核心）

`expert_cache_on_topk`（`llama-context.cpp:5427`）的四個區塊之一是
**`tail`＝「publish + remap leaf + union record」**（`:5448-5452` 自述）。

```
單段提交
  ⇒ 沒有 hook_seg ⇒ 沒有 expert_cache_on_topk
  ⇒ ① 沒有 ensure/demand fill      → 池不進新專家
  ⇒ ② 沒有 publish_slot_table      → slot_table 保持初始值全 -1（llama-expert-cache.cpp:3855 的 assign(...,-1)）
  ⇒ ③ 沒有寫 remap leaf            → mul_mat_id 讀到的是競技場殘留
  ⇒ B-scheme（CGC_B_SCHEME=1）把「非駐留」映射成 `e % ns`
     （llama-context.cpp:3631，程式註解自述「output is garbage, never a deliverable」）
  ⇒ 輸出 garbage，且被「合法但錯」的索引掩蓋 ⇒ 不會崩、不會報錯
```

實測對照（`Backup/phase_decomp/g2_provision_run4`，09-26）：`resident 5577/9984`、
**miss rate 42.93%** ⇒ 每步每層約 3.4/8 個選中專家走佔位。

## 3. ★★ 循環依賴：這才是真正的牆

把上面的鏈反過來問「要修什麼」：

> 要讓 `mul_mat_id` 正確，**remap leaf 必須在 submit 之前是對的**。
> 要 leaf 是對的，**必須先知道 ids**。
> 而 **ids 是圖內 `ffn_moe_topk` / argsort 節點的產物** —— 它在這張圖**算完之前不存在**。

⇒ **單段提交與「host 寫 leaf」在結構上互斥。** 這也解釋了為什麼
`gap ≡ 44.4 ms` 不是「被餓掉的空檔」，而是**fill 的窗口本身**：
CPU 之所以能在 GPU 忙的時候填專家，正是因為 GPU 被切成 41 段、每段之間回頭要 ids。
**把段拿掉＝把這個窗口拿掉。**

## 4. 所以「正確的單段」只有三條出路（互斥，且各有既有判決）

| 出路 | 內容 | 既有判決 |
|---|---|---|
| **(i) 預測 ids** | 用 ρ／prebind 預判 top-k，submit 前先寫 leaf，真 ids 出來只校驗 | ρ 與 prebind 各自**判活**，同 regime 同價 **+14.0~14.5%**（12.57 → ~14.3~14.4）；但**不是無 CPU** ⇒ 段數未必真能收成 1 |
| **(ii) leaf 裝置化** | 全鏈路無 CPU：slot 由 GPU 查表產生 | `CGC_SLOT_TABLE_GPU` 探針臂 **576/576 正確但無速度主張**；M-S2「slot_table 裝置化讓 submit-ahead 合法」判 **不可達** |
| **(iii) 保留分段、把 hook 做便宜** | 不動 leaf 機制，只砍 hook 的 CPU 成本 | hook 內部已知：每層 barrier ~12.9 ms/step ＋ 真搬資料 ~11.0 ms/step |

**⇒ 沒有一條是「新的」。** 這是本檔最重要的負面結論：**S1 不是一個尚未開採的礦，
而是一個已經被三條路徑各自試過、且都有判決的地方。**

## 5. ★ 因此唯一值得先做的量測（可判定、便宜）

**問題**：`CGC_SEG_BATCH=1` ＋ `CGC_B_SCHEME=1` ＋ `CGC_SLOT_TABLE_GPU=1` **三者同時開**
（即「單段 ＋ 裝置查表」）到底是正確還是 garbage？

- 若**正確**（對同 build 的 control 臂逐位相同）⇒ 出路 (ii) 的「無速度主張」只是**沒量過**，
  不是不能 ⇒ 這條線立刻從「不可達」變成「只缺一次量測」。**這會是很大的翻案。**
- 若**garbage** ⇒ 三條出路都有判決，(i)/(ii) 不可達 ⇒
  **「gap 是 fill 窗口，不可分離」就是終局**，本線可以誠實結案，把力氣移到 (iii) 與 ρ/prebind。

**成本**：control 臂 dump ＋ treatment 臂 `--ref` 比對，**2 支臂 ~10 min、0 重建**。
**閘門設計**：用**同 build 的對照臂**（不是重校準 pin、不是 DEF 子集）—— 因為
`llama-context.cpp:5435-5438` 自述 draft 的專家張量也被縮到池容量、也靠同一個 hook 寫 leaf，
`ggml-backend.cpp:3032` 說 draft 也走這個分段迴圈 ⇒ **我們的工作必定碰 draft，DEF 子集不足以認**。

## 6. 本檔未回答的（明確登記，不假裝）

- 三開臂的正確性 —— **未量**（§5 就是去量它）。
- ~~`CGC_SLOT_TABLE_GPU` 單獨 576/576 的「正確」是用什麼口徑判的~~ **→ 已查（見 §7）**。
- (i) 的 ρ 預測若命中率 h 不夠，leaf 會不會「半對」（部分 token 錯）—— **未查**。

## 7. 補記：「576/576」的口徑，以及它給出的**可證偽先驗**

出處 `docs/S1_LINE_VERDICT_2026-09-25.md:65`（同一節的表格）：

| 臂 | 開關 | 讀數 | 判據 |
|---|---|---|---|
| **S1 探針臂**（09-17/18） | **`CGC_SLOT_TABLE_GPU=1` 單獨**（**不開** `CGC_SEG_BATCH`） | prefill 0–3 **48/48**、decode 全 40 層 **480/0**、prefill 4–7 48/48 ＝ **576/576**；`answer_md5` 相同 | 逐層 `fnv1a64` 指紋 SAME/DIFF |

⚠ **同一節同時寫明：「速度：無主張，而且結構上不可能有」** —— 因為 `expert_cache_on_topk`
在**兩臂都跑**（＝hook 兩臂都跑 ⇒ 分段兩臂都在）。⇒ **`CGC_SLOT_TABLE_GPU` 單獨＝正確但不快，
而且它不需要單段。**

### ★ 由口徑推出的先驗（可證偽）

按 `S1_LINE_VERDICT` 的描述，`CGC_SLOT_TABLE_GPU` 做的是
**「把 expert→slot 映射從 host 寫的 input leaf 移到 graph gather（`get_rows` over a per-layer table）」**
—— 它裝置化的是**查表**，**不是建表**。
而**表本身仍由 host 建立**：`publish_slot_table` 只在**真的 fill 時**被呼叫
（同步路徑 `llama-expert-cache.cpp:1107`／bg 迴圈 `:3441,:3493`），初值是 `assign(..., -1)`（`:3855`）。

⇒ **可證偽的先驗**：`CGC_SEG_BATCH` ＋ `CGC_SLOT_TABLE_GPU` 同時開
**仍會是 garbage** —— 因為單段下沒有 hook ⇒ 沒有 publish ⇒ 表全 `-1` ⇒ 裝置查表只是
**更快地查到「全部不在池裡」**，然後被 B-scheme 映射成 `e % ns` 佔位。
換句話說：**裝置化的是「怎麼查」，不是「誰來建」；而掛掉的是「誰來建」。**

⇒ 若 arm 2 的結果**違反**這個先驗（即 M1/M2/M3 對 control 全同），
那就說明 **`CGC_B_SCHEME=1` 確實把「建表」也搬到裝置側了** —— 那是本規格最想知道的事，
且會直接改寫 §4 表格裡 (ii) 的判決（從「不可達」變成「已成立、只缺速度量測」）。

⇒ 若 arm 2 **符合**先驗（garbage），則 (ii) 的判決成立，**「gap 是 fill 窗口，不可分離」成為終局**。

---

## 8. ★★ 結案：不需要跑 —— 代碼 ＋ 既有實測就把三條出路全部封掉了

> 本節由 operator 的指令「**先看代碼，確定沒問題再跑**」觸發。結論是**不要把三開臂跑出去**。
> 全部 0 GPU、0 建置。

### 8.1 三開臂是**由設計** garbage，不是有待判決的實驗

`llama-context.cpp:3636-3645`（`CGC_B_SCHEME` 的定義處）自述：

> *"write ALL layers' remap leaves with a LEGAL placeholder mapping BEFORE the single-segment submit
> … **CGC_SEG_BATCH skips the per-layer hook**, so without this the remap stays stale and the
> single-submit arm **dies at ~8 tokens** … Purpose: measure the TRUE single-submit decode rate
> with enough tokens (**diagnostic arm: routing is wrong, output is garbage, never a deliverable**)."*

而 host-leaf 迴圈（`:3691-3695`）用的是**假的 ids**：

```cpp
for (int64_t k = 0; k < n; ++k) {
    const int32_t e = (int32_t) (k % 8);   // ← 假設「每個 token 都選了 expert 0..7」，不是 routing
    int32_t v = (st && e < n_expert && st[e] >= 0) ? st[e] : (e % ns);
    rd[k] = v;
}
```

⇒ **「違反先驗」在結構上不可能發生** —— 不是條件式的，是 `k % 8` 寫死的。
⇒ **我原本提議的三開臂實驗不該跑；這一趟是 operator 的「先看代碼」省下來的。**

### 8.2 更正 §7 的先驗：裝置路徑**確實**用真 ids，卡住的是「表不新鮮」

`:3648-3652` 說得更精確：

> *"With `CGC_SLOT_TABLE_GPU=1` the graph consumes `get_rows(slot_table, argsort_ids)` on the GPU,
> so the host only needs to publish the per-layer expert→slot table ONCE before dispatch
> (**it does not depend on routing**)."*

⇒ **gather 用的是真實 argsort ids（不是假的）**；表的內容只依賴**駐留狀態**（`st[e] >= 0`）。
⇒ 所以 (ii) **既不像我一開始猜的「把建表搬上裝置」，也不化約成 (i)**。它卡住的是別的東西：
**表要新鮮 ⇒ 要 fill ⇒ 要 ids ⇒ ids 在圖中段。**

### 8.3 ★ 而「要先知道 ids」這條路**已被實測判死**

`docs/H_MEASURED_A_VERDICT_2026-09-24.md`（2 趟獨立交付 cell、真 ids、`CGC_IDSEQ_DUMP`）：

| 預測源 | h（全中率） | 門檻 | 判 |
|---|---:|---:|---|
| `prev_union`（submit 當下唯一拿得到的） | **0.0322 / 0.0220** | 0.65（後收緊 0.75） | **FAIL，量級 20~30×** |
| top-16 拓寬（**與預測器無關的結構上界**） | ≤ 0.18~0.27 | 0.65 | FAIL |

- 覆蓋率看起來有 0.51~0.56，**全中率只有 2~3%**；白皮書 §6 的天真估計 `0.87⁸=33%` **樂觀了 10 倍**。
- 結構上界來自：每次 hook 的真實 **union 均值 19.0~19.8 > 16** ⇒ 拓寬到 16 天生不夠。
- ⇒ **「A 判死，不是還要優化，是這個方向的收益不存在。」**

### 8.4 終局

| 出路 | 需要的東西 | 判 |
|---|---|---|
| **(i) 預測 ids** | h ≥ 0.65 | ⛔ **已量測判死（h = 0.022~0.032；top-16 的結構上界 18~27%）** |
| **(ii) leaf 裝置化** | 表新鮮 ⇒ fill ⇒ ids | ⛔ **化約回 (i)**：表不依賴 routing，但**更新表**要靠 ids |
| **(iii) 保留分段、hook 做便宜** | — | ⚠ 唯一活著，但只是常數優化：barrier **12.9 / 158 ≈ 8%** |

**⇒「`gap ≡ 44.4 ms` 是 fill 的窗口，不可分離」是終局**，且現在有三條獨立機制級證據：
① `drain_layer` 殺掉任何 prefetch（沒有重疊窗口）；
② h = 0.022~0.032（預測性預填不可能）；
③ 補算路線（B-scheme 的 deliverable 形式＝prev-token 預測 ＋ 逐層重算）要的正是 ② 的那個 h。

**⇒ 本線該以「負結果」結案，不是繼續找槓桿。**

### 8.5 ★★ 從**現行代碼**重推（不看判決書）：三個預測機制共用同一個來源，且代碼假設與交付形狀直接衝突

> operator 指令：「**不要看判決書，看最新的代碼**」。以下全部引現樹 `src/llama.cpp/src/llama-context.cpp`。
> 先確認代碼基準沒動：**09-24 18:00 之後動到 `src/` 的只有 4 個 commit**
> （`65c76b8c7` KV 共享、`553424ec1` miss mask 重建、`8afb56af4` expand 修正、`828f4d1c2` leaf-blind 修正），
> **沒有一個碰預測／預填／S1 leaf**。現樹有 110 個 `CGC_*` 開關，與預測有關的是這三個：

| 機制 | 位置 | 預測源 | 觸發 | 預設 |
|---|---|---|---|---|
| `CGC_PREV_TOKEN_PREFETCH` | `:5881-5918` 收集、`:6149-6214` 觸發 | **上一 token 的 per-layer ids** | **`il == 1` 一次性**，40 層全部 | OFF |
| `CGC_LAYER_AHEAD_PREFETCH` | `:5888-5894` | **同上（共用收集）** | 每層 hook 排 `il+1` | OFF |
| `CGC_PREROUTER` | 09-17 引入 | 影子 router | decode、L+1、8 顆 | OFF |

**三個機制的預測源是同一個東西**（`:5890` 自述「Same prediction source」）——
所以「換一個預測器」不是三張牌，是**一張牌**。

而 `:5884` 與 `:6150-6151` 寫著這個機制的地基假設：

> *"predicting the current token's experts from the previous token (**adjacent tokens have ~70-90%
> routing overlap**)"*、*"Adjacent tokens have strong routing correlation (**~70-90% overlap**)"*

**⇒ 與交付形狀的實測直接衝突**：同一來源實測（ntok=4）覆蓋率只有 **0.51~0.56**、全中率 **0.022~0.032**
（§8.3）。代碼的 70~90% 是**單 token** 的直覺；交付 cell 是 MTP verify ⇒ **ntok=4（32 個 ids）**，
覆蓋率就掉到一半。

**三個獨立缺陷疊加，任何一個都足以判死這條路：**
1. **預測本身弱一半**：交付形狀覆蓋 51~56%，不是代碼寫的 70~90%。
2. **ASYNC 形式被 `drain_layer` 吃掉**：在 `il==1` 排入 40 層，但每層 L 的 hook 都會
   `drain_layer(L)` 清掉 L 的 `slot_queued`（`llama-expert-cache.cpp:1555`）——
   L 的預填只有 `il` 從 1 走到 L 這段時間能「開始」（`slot_loading` 後就安全），**尾段的會被清掉**。
3. **SYNC 形式只是搬家**：`CGC_PREV_TOKEN_PREFETCH_SYNC=1` 走 `ensure_slot`（`:6181`，**阻塞 decode**）
   ⇒ 同樣的 IO 總量、同樣落在關鍵路徑上，只差在「一次大批」vs「40 次小批」的 barrier 數。
   **不省時間，只換形狀。**

**⇒ 這與 §8.4 的終局是同一句話**，但現在是從**現行代碼**推出來的：
「gap 44.4 ms 是 fill 的窗口」，而填窗口所需的 ids 只在圖中段存在，三個預測機制共用一個弱來源。
