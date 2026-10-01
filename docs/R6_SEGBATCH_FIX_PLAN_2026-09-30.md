# 引擎工作項：讓 `CGC_SEG_BATCH=1` 的輸出回到位元等同（M1 9/9）

**目標（operator 2026-09-30）**：修讀回路的成員維護，讓單段提交臂的配對 `M1/M2/M3` 回到 `9/9`。
**現狀（量到的）**：對照臂 `9/9`；本臂 `M1 0/9`，**第一格 decode 就不同**；
帶量具的變體更糟 —— 引擎自己把 dump 蓋成 **invalid**（`non-finite values: 993280 of 993280`，全 NaN）。
**這份文件的用途**：把「修哪裡、為什麼是那裡、驗收是什麼」寫死，讓下一輪不必重新推導。

> **2026-09-30 晚間更正（本版）**：§二、§三 已按當晚的源碼複核 ＋ 零 GPU 幾何複算重寫。
> 上一版寫「全駐留要 `40 × 256 × 0.43 MiB ≈ 4.4 GiB`、**帳面上放得下**」是**錯的**：0.43 MiB 不是這個
> profile 的槽費，而且 `layer_caps=40-40:256` 根本不涵蓋主幹。
> 本版把 **(A) 全駐留標成已否證**、把 **(B)／S2-c 升為主路**；**驗收判準與錨不變**。

---

## 一、機制：為什麼單段提交一定錯（三條，全部有源碼或量測依據）

1. **路由在 dispatch 之後才知道。** 第 `il` 層選哪些專家取決於第 `il-1` 層的輸出。單段提交把 41 層
   放進同一張圖 ⇒ 主機端在提交前**不可能**知道本步要哪些專家
   （這就是 hook 存在的理由：它在每層 matmul 前 `ensure` 那些專家，`llama-context.cpp:8062+`）。
2. **單段臂因此改用「合法但錯的佔位」**：`CGC_B_SCHEME=1` 在提交前把每層 remap leaf 寫成
   `slot_table[e]`（駐留）或 `e % slots`（不駐留）—— 源碼自述「legal-but-wrong placeholder」
   （`llama-context.cpp:3737-3760`；不駐留的計數在 `:3823`）⇒ 選到不駐留的專家時，`mul_mat_id`
   **讀到別的專家**。
   ⚠ 註：`e % slots` 只有在「層寬 = n_expert 且全駐留」時才退化成恆等映射 `e`。這就是 **(A) 全駐留**
   這條路的**全部**立論；§三 逐條否證它。
3. **它的成員維護只剩預測**：P1 的讀回餵料（`:4103-4135`，餵 SpAc ＋ step-union）驅動的是
   **非同步 prefetch**，預測的是**下一步**；沒有東西替**本步**補洞 ⇒ 預測錯的那幾個洞就是佔位。
   量測佐證：誠實臂在同一個 cell 上也有 **miss/step 中位 6.0（IQR 4–12）**
   （`Backup/l251_loadmode_2026-09-30/`）—— 誠實臂靠 hook 即時補，單段臂就讀錯。

⇒ **結論：只要「本步選中的專家有人不在池裡」且池子放不下全部專家，單段提交就會錯。**
這不是成員維護的 bug，是這個設計的固有代價；維護能做的是**讓「不在池裡」的集合變空**。
（下一節更正：本 profile 的「不在池裡」到底有多大、以及為什麼放大池子不是解。）

## 二、更正：`layer_caps=40-40:256` 只涵蓋 blk.40，**主幹每一層從來不是 256 槽**

### 2.1 格式與覆蓋範圍（源碼）

- `LLAMA_EXPERT_CACHE_LAYER_CAPS` 的格式是 `start-end:cap;...`：後段覆蓋前段，**未被任何段涵蓋的
  層保 `def`**（`cgc_layer_cap`，`llama-expert-cache.h:672`）。
- 本 profile 的值是 `40-40:256`（實證：`Backup/m123_oracle_gate/cap_r6ctl-nosegbatch-2026-09-30.json`
  的 `ENV.LLAMA_EXPERT_CACHE_LAYER_CAPS`）⇒ **只套 layer 40（MTP / NextN）**。
  主幹 `0..39` 全部落回 `def = n_slots`。
- 解析器是**同一個**，兩邊都吃：loader 拿它縮 `t_meta.ne[2]`（Metal pool 區寬，`llama-model-loader.cpp:1494-1510`），
  cache 拿它建 per-layer 槽向量（`llama-expert-cache.cpp:4050-4054`）。
- 附帶：MTP 層**不能**參與 `per_slot` 的計算，所以主幹的池幾何與頭怎麼存**無關**
  （loader 的 `n_decoder` 排除，`llama-model-loader.cpp:1151-1173`）；MTP 層的槽數永遠由 `LAYER_CAPS` 給。

### 2.2 `def`（主幹槽數 143）怎麼來

- 池預算 8 GiB。`per_slot = MAX over 主幹層的 per-expert bytes`（排除 MTP 層，同上）；
  `capacity = clamp(budget / (max_layer × per_slot), 8, 256)`（`llama-model-loader.cpp:1186`；
  日誌行在 `:1189`）。
- 零 GPU 複算（`scripts/check/gguf_pool_geometry.py`，只讀 GGUF 的 tensor table，不讀權重、不開 GPU）：

  ```
  model : Nail-Qwen3.6-35B-A3B-MTP-UD-IQ3_XXS-denseIQ4X.gguf  (12.72 GiB, 41 layers, n_expert=256)
  binding 層 = blk.39  ->  per_slot = 1,458,176 B (1.3906 MiB/expert)
  8 GiB  -> 143 slots     10 GiB -> 179     12 GiB -> 215     14 GiB -> 251     16 GiB -> 256
  ```

- 引擎自己的三行鐵證（對照臂 `Backup/cgc_logs/llama_server_20260930_165336.log`）：

  ```
  :127  CGC Soft Pool init: L0=0 L1=0 (n_slots=143, partition disabled)
  :128  llama_expert_cache: L4 metal pool: 143 slots/layer, regions adopted from expert tensors
  :129  llama_expert_cache: LAYER_CAPS per-layer caps: total 5976 slots (avg 145.8/layer, min 143/layer)
  ```

  `min 143/layer` 就是主幹；`5976` 這個總和 = `40 × 143 + 256`（主幹 40 層 × 143 ＋ MTP 層 256）。
  ⇒ **「effective 不是 256」是對的，而且每一層主幹從來不是 256。**

### 2.3 `total 5976` 是**計畫和**，不是狀態（免得下一個人拿它當指紋）

`LAYER_CAPS per-layer caps: total 5976 slots` 是 `LAYER_CAPS` 字串的**配置和**，它不查「哪些層真的進了池」。
兩臂（39 層池化 vs 40 層池化）**都印 5976** ⇒ 這行不能當狀態證據（CONVENTIONS 的 `eng-gate-0012` 已記過一次同樣的錯）。
本節引用它是為了「143 是主幹的實配」這個**層級**結論，不是為了層數。

### 2.4 evict 不是容量不足，是 **LRU 重指派**

對照臂的 miss 歸因（同一份 log）：

```
:426  llama_expert_cache: miss attribution: compulsory=798 capacity=0 (100.0% / 0.0% of 798)
        evictions=686  layers_distinct_over_slots=0  worst=layer 40 distinct=113 slots=256
:425  llama_expert_cache: slab fills: pool=6153.7 MiB disk=4940.3 MiB (non-resident share 44.5%)
```

- **全部 798 次 miss 都是 compulsory，`capacity=0`**：沒有任何一次是「這個專家以前來過、這次被擠掉了」。
- `evictions=686` 是 `pick_slot` 把**在場**專家踢掉騰位（計數在 `llama-expert-cache.cpp:774`）——
  也就是說，每個新需求的第一次觸及（compulsory）都得踢掉一個**佔位者**（slab 填了 6.0 GiB、
  非同步 prefetch 也佔位）。
- `layers_distinct_over_slots=0`：**沒有任何一層的累積 distinct 需求量超過它的槽數**；
  worst = layer 40（MTP）distinct 113 < 256。主幹各層的 distinct ≤ 143。
- 源碼自己的判詞寫在 `llama-expert-cache.cpp:3017`：
  「若某層的 distinct 需求量塞得進它的槽數，暖機後就不可能發生容量 miss ⇒ **只有 locality／routing 能幫，
  再放大池子是純 RSS 成本**。」

⇒ **更正上一版的解讀。** 上一版看到 `evictions=686` 就推「每層可用槽 < 256、有淘汰」，
把 evict 當成容量不足的證據。實情相反：**卡的是時機，不是容量** ——
工作集本來就放得下，問題是「本步需要的那些」在 dispatch 時不保證在場。

## 三、(A) 全駐留 —— **已否證**（四條，任一條成立即足夠）

### A-1（前提就錯）`40-40:256` 沒有讓主幹變 256

見 §2.1／§2.2：它只套 blk.40。要主幹全駐留得寫 `0-39:256`，那已經是**改配置**，不是「現成設定」。

### A-2（算術上放不下）全駐留 ≈ **14.3 GiB**，這台裝不下

- 全駐留 = 41 層 × 256 槽 × 1.3906 MiB/槽 ≈ **14.25 GiB（≈14.3 GiB）**。
  （交叉檢查：`pool_feasibility` 風格的 loader 反推 —— 要 capacity 真的到 256，budget 得 ≥ **16 GiB**；
  8/10/12/14 GiB 分別只到 143/179/215/251。）
- 這台的靜態需求：模型 12.72 GiB ＋ pool ≥ 6.3 GiB（實配；見下）≈ **19 GiB > 16 GB**，
  已超 ~3 GiB；而 8 GiB 預算之下**實配只有 6.32 GiB**（`gguf_pool_geometry` 的 today 值），
  實測 `resident=6369.61 MiB`（同 log `:419`）。
- 把 pool 拉到 14–16 GiB 只會讓超額更嚴重 ⇒ **不可行**，不是「再調一下參數」。

### A-3（現成 prewarm 機制填不滿 256）

`llama_expert_cache_prewarm_hot_capped`（`llama-expert-cache.cpp:2438+`）按 prefill 路由頻率逐層取 top-`n`，
而 `n = min(slots_l(l), n_expert)`（`:2463`）= `min(143, 256)` = **143**。
⇒ 就算把 caps 給到 256，**它也只填它拿到的槽數 143**；而且它填不進剩下的 113 個主幹專家。
（呼叫點在第一個 decode 步前，`llama-context.cpp:2081-2090`，由 `CGC_NO_PREWARM` 反向控制。）
- 帳務更正：`final stats` 裡的 `prewarm req=0` **不代表 prewarm 沒跑** ——
  hot prewarm 的 `ensure` 用預設 `count=true`，計入 `runtime requests`；`n_prewarm_*` 只有
  `count=false` 的 tail-pin 路徑會動（`:2735+`）。拿 `prewarm req=0` 推「prewarm 沒執行」是誤讀。

### A-4（決定性）分歧**不在駐留**，所以「填滿池子」打錯靶

S2-b 的四趟（`docs/S2_S3_2026-09-30.md`）：

| 臂 | 池計數器 | M1 |
|---|---|---|
| 誠實臂（prefetch 開） | hit **89.2% → 96.3%**、evictions **686 → 163** | 9/9 PASS |
| 單段臂（同一組 env） | **5976 req／97.4%／miss 153 全 compulsory／evictions 41 —— 與 prefetch 關閉那兩趟逐項相同** | 0/9 |

單段臂的 hit（**97.4%**）**比誠實臂（89.2%）還高** ⇒ 原假說「單段臂因為跳 hook 掉駐留」不成立。
再配上 §2.4 的 `capacity=0`、`layers_distinct_over_slots=0`，「把池子填滿」對 M1 是**無效槓桿**。

⇒ **(A) 作廢：不要再追「為什麼槽數不是 256」，那條線是空的。**

## 四、(B) 預測 ＋ 逐層重算 —— **主路**（第一刀是 S2-c）

### 4.1 S2-c（最便宜，先做）：把 ρ capture 搬出被跳過的 hook

- 靜態根因（S2-b 坐實）：`cgc_rho_capture()`（`llama-context.cpp:4664`）**只**在
  `expert_cache_eval_cb`（`:4798`）裡被呼叫（`:4837`），而那正是 `CGC_SEG_BATCH=1`
  **跳過**的 per-layer hook（`:4103` 的註解明寫 "SKIPS THE HOOK"）
  ⇒ `g_rho_logits` 始終為空 ⇒ `cgc_rho_prefetch()`（`:4708`）在 `:4727` 的 `empty()` 守衛直接 return
  ⇒ **投遞通路被單段提交自己關掉**。這不是「沒猜中」，是通路不存在。
  （附帶辨識：單段臂 log 的 `prefetch=1528/0` 來自 DBUF/SpAc 等非 ρ 來源，不是 ρ。）
  ⚠ 更正源碼註解自己的一個數字：`:4110` 寫「measured submit-time residency 57.1% (arm) vs 96.3% (honest)」，
  但 S2-b 在**同一組 env** 下量到的單段臂是 **97.4%**（且比誠實臂 89.2% 高）⇒ 那個 57.1% 在 S2-b 的配置下
  **不成立**（它對應的是「hook 是唯一餵料者」的舊配置）。引用它之前先讀 §三 A-4。
- 動作：把 ρ capture 接到單段路徑**已經有**的讀回通道 —— 比照 P1 的
  `cgc_rb_seg_batch && (...)`（`:4122`），補一個 capture，而**不是**恢復整個 hook。
- 判別句（預註冊）：**M1 ≥ 8/9 ⇒ 20+ 臂可引用（R6 解除）**；
  ≤ 2/9 ⇒ 分歧在單段提交圖本身（slot／mask 映射，即已知的 `CGC_B_SCHEME` garbage），
  那時才升為 §4.2 的引擎手術。
- ⛔ 動工前置：`src/llama.cpp/src/llama-context.cpp` 目前在他線工作樹（117 檔未提交）⇒
  需等其落地或確認不撞同一段。

### 4.2 (B) 本體（通用解）

- 源碼原文（`llama-context.cpp:3746`）：「The deliverable version replaces the placeholder with
  **prev-token prediction ＋ per-layer recompute of mismatched layers**」。
- 形狀：提交一次 → 讀回時**偵測哪些層碰到佔位**（讀回路已經在讀 `ibuf` 並數它們，`:4111`）
  → 只對那幾層重跑 FFN ＋ 併回 activation → 才進 sampler。預測（P1 的餵料）把這個集合壓到最小。
- 這是真正的引擎工作：動 `llama-context.cpp` 的 dispatch/offload 區與 `llama-expert-cache` 的
  `ensure_slot` 路徑，且必須過 repo 對引擎改動的硬規則（自立項 ＋ D5 oracle gate）。

### 4.3 驗收（兩條路共用，不變）

**配對 `M1/M2/M3` ＝ 9/9**，錨是同 build、同 profile 的對照臂 `9/9`
（`Backup/m123_oracle_gate/summary_r6ctl-nosegbatch-2026-09-30.json`）。

### 4.4 便宜的判別實驗：駐留時機 vs 圖本身（**量具更正**，立項卡 `charters/e-r6-residency-vs-graph-2026-09-30.yaml`）

想把「駐留時機」與「單段提交圖」分開，直覺是「讓每一步需要的專家都在池裡，再看 M1」。
兩個前提要改正，否則實驗會跑不出判詞：

1. **不能用 G3 的 `placeholder` 當判準（兩種臂都失效，2026-09-30 複核後收緊）。**
   `CGC-G3-ZEROSLOT-TOTAL` 的 `placeholder` 數的是**每層 `slot_table[e] < 0`（＝不駐留）的個數**
   （`:3816-3826` 的計數迴圈，`s_g3_ph` 在 `:3823`；第二個寫入器同一個計數器另在 `:3868`），但只有**圖裡真的建了 `ffn_moe_slot_table` leaf 的層**
   才會被走過，而那只要 `CGC_SLOT_TABLE_GPU`（`llama-graph.cpp:2263`：`if (cgc_slot_table_gpu && il >= cgc_s1_min_il)`）。
   兩件事同時成立：
   * **在對照臂（`prod-new`，不設 `CGC_SLOT_TABLE_GPU`）它是真空的**：印出點在 tensor 迴圈**外面**
     （`:3828-3834`，且在 `:3868` 的第二個寫入器**之前**，所以那一槍只看得到 slot-table 這一個寫入器），map 是空的也照印 ⇒ 2026-09-30 新跑的兩支 `prod-new` 臂都印 6 次
     `placeholder=0`（`Backup/l257_s3b_2026-09-30/…stderr.log:35/182/273/371/481/653`）。
     那個 0 不是「沒有不駐留的專家」，是「沒有東西被數」。
   * **在真正需要它的臂（設了 `CGC_SLOT_TABLE_GPU`）它是地板**：既有日誌第一槍就是
     **4407 = 113 × 39**（`Backup/spac_sweep_2026-09-29/k_sweep_C.logs/…CGC_SEG_BATCH_1_CGC_B_SCHEME_1_CGC_SLOT_TABLE_GPU_1…stderr.log:43`），
     之後每步再加約 113×40 ⇒ 池只有 143 槽時要它變 0 得每層 256 槽 = **14.3 GiB**（§三 A-2）。
   ⇒ **這條判準在對照臂看到 0 是假綠、在見證臂看到紅是幾何**，兩邊都不能用。
2. **該用的是「本步被選到、而當時不在池裡」的個數**：`CGC-MISSMASK-STEP: step=.. misses=.. layers=..`
   ＋逐層 `MISSMASK il=.. nsel=.. misses=..`（`:4191`／`:4198`，需 `CGC_MISS_MASK=1;CGC_MISS_MASK_DBG=1`）。
   它**可達 0**：既有單段臂日誌 `Backup/zm_identity2/…n8…stderr.log` 的**前 5 個 decode 步就是 `misses=0`**，第 6 步起 ~130/步。
3. 順手更正一條更常被誤用的：**「union 變小」不等於「需求全在池裡」**。該日誌的 `hits=5720 = 40×143`、
   `file_reads=0`、`evictions=0` ⇒ 駐留集在 prewarm 後**固定**，所以乾淨與否只看「該步需求是否落在
   prefill 選出的那 143 個裡」；而 143/256 覆蓋率下 `top_k=8` 的期望非駐留數 = `8×(1−143/256)×39 ≈ 138/步`
   —— 觀測的 ~130 就是這個結構值。這也解釋了為什麼「池 hit 97.4%」不是駐留證據：
   **decode 階段根本沒有發出 `ensure_slot`（hook 被跳過）**，池計數器對 decode 的需求量是盲的。
4. **兩個端點必須分兩趟**（被 R5 逼的）：`_DBG` 每步多一次 `synchronize`（`run_server.sh:1706`，R5）
   且 2026-09-30 帶 `CGC_MISS_MASK=1` 的 dump **全 NaN** ⇒ 駐留見證趟只讀計數；M1 趟不帶量具。
   兩趟用同一 probe，並以 `worst distinct`／`runtime requests`／`hit_pct` 相同證明工作集一致。
5. **免費的對照（2026-09-30 已量，0 盒子成本，登記為見證產物）**：同一個 cell（`p2048_n8_d512_r3`）、
   同一個 `CGC-MISSMASK-STEP` 量具下，兩臂分得很乾淨：

   | 臂 | 每步 `misses`（30 個 decode 步） | 池 |
   |---|---|---|
   | **誠實臂** `prod-new`（`Backup/mm_identity/llama_bench_prod-new_p2048_n8_d512_r3.stderr.log`） | **全 30 步 0** | `requests=13720 hits=10947 misses=2773 (79.8%)`、`file_reads=76119` |
   | **單段臂** `CGC_SEG_BATCH=1`（`Backup/mm_identity/…CGC_SEG_BATCH_1_…_p2048_n8_d512_r3.stderr.log`） | 0×5 之後 **~130/步** | `requests=5720 = 40×143`、`hits=5720`、**`file_reads=0`** |

   ⇒ (i) 量具**會分辨**兩臂（誠實臂靠 hook 在 FFN 讀之前補進來 ⇒ 遮罩讀到 0；單段臂沒有 hook
   ⇒ 連 decode 期間都沒載過任何東西）；(ii) `misses=0` 這個前題在同一幾何下**已被證明可達**；
   (iii) 前 5 步乾淨**不是「union 小」的功勞**，是 prefill 熱集剛好蓋住（兩臂前 5 步一致）。

⇒ 判詞：前置（每個被判的 decode 步 `misses=0`，且 ≥9 步）成立後，**M1 0/9 ⇒ 分歧在圖本身**
（(A) 類作廢，直接轉 §4.2）；**M1 9/9 ⇒ 駐留時機是唯一來源**。前置不成立 ⇒ UNDECIDABLE，不得讀成任一側。

### 4.5 S2-c 的落地座標（2026-09-30 對源碼複核；這條路是「一次 build ＝ 一個答案」）

**那個洞的精確形狀**（不是「ρ 沒實作」，是「執行者只有一個，而它不在這條路上」）：

| 角色 | 座標 | 說明 |
|---|---|---|
| 唯一的執行者 | `llama-context.cpp:4798` `expert_cache_eval_cb` | 閘在 `:4801`（`ask==false` 且名字前綴 `cgc_rho_logits`）；工作內容＝`cgc_rho_capture(t)`（`:4837`）＋ `cgc_rho_prefetch(il)`（`:4844`） |
| 誰會叫它 | `src/llama.cpp/ggml/src/ggml-backend.cpp:2604` | `CGC_OA_ASYNC` 的**分段提交**逐層迴圈裡，backend 對影子節點額外轉發一次 |
| 誰不會叫它 | 單段提交（`CGC_SEG_BATCH=1`，閘在 `:4038`） | 逐層迴圈不存在 ⇒ cb 從不被呼叫 ⇒ `g_rho_logits[ul]` 永遠空 ⇒ `cgc_rho_prefetch` 在 `:4727` 的 empty 守衛直接 return（與 S2-b 的觀測一致） |

**兩條實作（擇一；兩條都只在 `cgc_rb_seg_batch` 這一支上生效 ⇒ 誠實臂 byte-identical）**：

- **(i) 直接投遞（最小）**：在單段臂**已有的讀回區塊**旁（`:4102-4135`，閘＝
  `cgc_rb_seg_batch && (cgc_miss_mask_dbg || cgc_rb_feed)`）對該層的影子張量做同一型的事
  （`ggml_backend_tensor_get`），再直接呼叫 `cgc_rho_prefetch(il)`。成本＝每層 **256×4 B ＝ 1 KiB** 的
  device→host 讀回，而且**搭在同一個 sync 上**（該區塊本來就同步讀回 routed ids ⇒ 不新增同步）。
- **(ii) 補轉發**：把 `ggml-backend.cpp:2604` 那一次 forwarding 也接到單段路徑上，讓既有的 cb
  原封不動被叫一次（改動更小，但要先確認影子張量的 buffer 在那個時點仍可讀）。

**兩條都要一起講的代價與期待**：單段臂**沒有逐層 dispatch** ⇒ 影子 logits 落地時，本步的 FFN
讀取早就過去了 ⇒ ρ 的 fill **只能預付下一步**的 union（這正是「補投遞通路」的意思，不是缺點）；
而 capture 每層一次的讀回（`:4837`）是**新增的**同步成本 ⇒ 這一趟**不得主張任何 t/s**（定價屬 S3-b）。

⛔ 不要動的：`cgc_rho_capture` 內那條**順序判據**（`:6752` 的 stamp 比較：
「hook 之前有沒有出現過一個新的影子值」）—— 它在 decode 逐步路徑上已驗過；搬位置時要保留語意，
否則會變成「數字照樣很漂亮」的靜默錯誤（`:6746` 的註解就是為此寫的）。

驗收不變（§4.3）：**M1 ≥ 8/9**（目標 9/9），對照錨 `summary_r6ctl-nosegbatch-2026-09-30.json`。

## 五、驗收怎麼跑（現成，2.5 分鐘一趟）

```bash
# 對照臂（錨，已存在）
python3 scripts/check/m123_oracle_gate.py --profile prefill250 --tag r6ctl-nosegbatch-2026-09-30
# 待驗臂（改完之後）
python3 scripts/check/m123_oracle_gate.py --profile prefill250 --tag r6-segbatch-<日期> \
    --env CGC_SEG_BATCH=1 --env CGC_SLOT_TABLE_GPU=1
```

判準：`GATE ...: PASS   M1(bit-identical)=9/9  M2(argmax)=9/9  M3(topk)=9/9`。
⚠ 三個坑，先記下來免得下一個人重踩：
1. **不要用 `CGC_MISS_MASK=1` 的變體當驗收臂** —— 2026-09-30 實測那一趟引擎吐**全 NaN**
   （`m123_oracle_gate.jsonl.invalid`：`non-finite values: 993280 of 993280`）。NaN 本身是
   **另一個 bug**（佔位讀到非有限值 / 未初始化槽），值得單獨查，但它不是 M1 判詞。
2. 想加量具就得 `--allow-incomparable`（量具不在 `m123` 的 DIAGNOSTIC_KEYS 裡），
   那一趟的判詞是「跨組態、僅供參考」，**配對結論要來自同 build 的對照臂**。
3. **前置零 GPU 檢查**：動配置（改 `CGC_SERVER_LAYER_CAPS`／預算）之前先跑
   `python3 scripts/check/gguf_pool_geometry.py --layer-caps` 與
   `python3 scripts/check/pool_feasibility.py` —— 它們只讀 GGUF 的 tensor table，
   會直接吐出「這個預算給幾槽／這個 caps 字串能否跑」，省掉一趟被 loader 拒絕的啟動。

## 六、這一輪沒有動 `src/` 的理由（更正）

- **(A) 已否證** ⇒ 不再卡在「為什麼槽數不是 256」；那條問題線關閉。
- **(B)／S2-c 都要動 `llama-context.cpp`**（ρ capture 搬家、或 dispatch/offload 區的重算），
  而 `src/` 在他線工作樹（117 檔未提交）⇒ 動它會撞車。
- repo 規矩：**引擎改動要有它自己的立項與 D5 oracle 場次**，不是在一輪長 session 的尾巴塞進去。
- 因此本輪交付的是：**更正後的診斷（含零 GPU 幾何複算與逐行 log 鐵證）、兩條路的新優先序、
  以及現成的驗收／前置指令**。

## 七、相關產物

| 內容 | 路徑 |
|---|---|
| 對照臂（錨，9/9） | `Backup/m123_oracle_gate/summary_r6ctl-nosegbatch-2026-09-30.json` |
| 對照臂 config（`LAYER_CAPS=40-40:256`、8 GiB、MTP on） | `Backup/m123_oracle_gate/cap_r6ctl-nosegbatch-2026-09-30.json` |
| 對照臂 server log（`:127/:128/:129` 槽數、`:419` final stats、`:425` slab、`:426` miss 歸因） | `Backup/cgc_logs/llama_server_20260930_165336.log` |
| 單段臂（0/9）＋ 其 log | `Backup/m123_oracle_gate/summary_r6-segbatch-2026-09-30.json`、`Backup/cgc_logs/llama_server_20260930_{170359,171121}.log` |
| 全 NaN 變體 | `Backup/r6_witness_2026-09-30/gate_missmask.log` ＋ `…jsonl.invalid` |
| S2-b／S2-c 預註冊與實測 | `docs/S2_S3_2026-09-30.md` |
| 成員維護現況（miss/step） | `Backup/l251_loadmode_2026-09-30/delivery.json` |
| 零 GPU 幾何複算（本版新增引用） | `scripts/check/gguf_pool_geometry.py`、`scripts/check/pool_feasibility.py` |
| 紅利歸因（不是 hook 的 CPU） | `docs/S1_FLOOR_ARM_ATTRIB_2026-09-30.md` |

**源碼座標（本版逐一複核）**：`cgc_layer_cap` = `llama-expert-cache.h:672`；
槽數推導 = `llama-model-loader.cpp:1186`（log `:1189`）；MTP 排除 `per_slot` = `:1151-1173`；
`ne[2]` 縮放 = `:1494-1510`；cache 端槽向量 = `llama-expert-cache.cpp:4010`／`:4050-4054`（普查行 `:4190`）；
prewarm cap = `:2438+`（`n = min(slots_l, n_expert)` = `:2463`）；evict 計數 = `:774`；
miss 歸因註解 = `:3017`（印出 `:3033`）；tail-pin `count=false` = `:2735+`；
placeholder = `llama-context.cpp:3816-3826`（計數迴圈）／`:3828-3834`（印出點，在 tensor 迴圈外）；leaf 閘 = `llama-graph.cpp:2263`；SEG_BATCH = `:4038`；
讀回餵料 = `:4103-4135`；prewarm 呼叫點 = `:2081-2090`；
ρ capture/prefetch = `:4664`／`:4708`（empty 守衛 `:4727`），呼叫點 `:4837`。

### 4.6 §4.5(i) 的補丁：已交付（**不套用**、可即刻 `git apply`），2026-09-30

| 產物 | 位置 |
|---|---|
| 補丁 | `Backup/s2c_2026-09-30/s2c_rho_capture.patch`（md5 `104e6d90…`、5948 B、2 hunks） |
| 基線 | `src/llama.cpp/src/llama-context.cpp @ md5 bc65fbb4c8e1f5f2c8c1d97c282334bd`（10287 行） |
| 逐項證據 | `Backup/s2c_2026-09-30/delivery.json` |
| 立項卡 | `scripts/check/charters/e-s2c-rho-capture-2026-09-30.yaml`（新增 `delivery:` 段；立項閘複驗 PASS） |
| 見證行 | `CGC-RHO-S2C: steps=.. layers=.. bytes=.. preskip=..`（檔內新行號 :4337） |
| 量具註冊 | `instrument_binding.PROBES['rho_s2c']` ＋ `PROBE_REQUIRES=['CGC_RHO_PROBE','CGC_SEG_BATCH']`（`--self-test` ALL PASS） |

**這個補丁做了什麼**：在 `graph_compute` 的 ids 讀回區塊**之後**（:4269 起）加一個新區塊，閘＝
`CGC_SEG_BATCH=1 && CGC_RHO_PROBE=1`。它掃這張圖的節點、挑出 `cgc_rho_logits-<il>`（與 cb 同一個前綴判據，
`qwen35moe.cpp` 的 `cb(...)` 命名），套**同一個相位閘** `cgc_is_decode_graph`，然後
`cgc_rho_capture(t)` ＋ `cgc_rho_prefetch(il)`（後者自己含 `CGC_RHO_FILL` 與層容量閘）。
同步沿用上面的 drain；只有在 ids 區塊沒跑時才自己補一次（`s2c_synced`）。

**為什麼落點在區塊之後、而不是鑽進去的**：原區塊的閘是 `cgc_miss_mask_dbg || cgc_rb_feed`（一個 debug 量具
或餵料開關）。把 ρ 的投遞塞進去，等於「ρ 只能在帶著 R5 量具的臂上跑」——那正是 P1 已經拆掉一次的錯
（`:4022` 的註解就是為此寫的）。新區塊讓 ρ 的投遞只依賴它自己的兩個開關，代價是形狀與 §4.5「搭在同一個
sync 上」的敘述差一行註解（`s2c_synced`）。

**驗過三件（不套用也驗得到）**：
1. `git apply --check -p1 …` 對**未修改的實檔** ⇒ PASS（不是只對 scratch 樹放行）；
2. `patch -p1` 進 scratch 樹後與交付內容 `cmp` **位元相同** ⇒ 補丁重現得了它宣稱的東西；
3. **`-fsyntax-only`**（直接取專案 build 的 `compile_commands.json` 那一條命令）⇒ **rc=0、0 error、
   兩個 hunk 內 0 warning**；其餘 7 條 warning 皆為既有行（595／627／5821／6687／6691／6754／6759）。

**跑法與判詞（一次 build ＝ 一個答案）**：見立項卡的 `delivery.apply_and_run`。摘要：
`git apply --3way` → build → 對照臂（可重用錨 `summary_r6ctl-nosegbatch-2026-09-30.json`）＋
見證臂（`--env CGC_SEG_BATCH=1 --env CGC_SLOT_TABLE_GPU=1 --env CGC_RHO_PROBE=1 --env CGC_RHO_FILL=1
--env CGC_SERVER_NO_PREFETCH=0`）→ `r6_witness.py` 期望 `WITNESS_GREEN`，並以
`grep -c 'CGC-RHO-S2C:'` ≥1 且 `layers/step == 40` 證明投遞真的發生。

**兩條必須一起看的口徑**：
* **R5 不動**：這支臂帶 `CGC_RHO_PROBE`（`quote_gate` 的 R5 條目）⇒ 即使 M1 回到 9/9、R6 解除，
  **它的 t/s 仍不可引用**。R5 解除要等 L20-7 的價格 ≤ 視窗，而 2026-09-30 的兩趟反序判 **REFUSE**
  （`docs/S3B_RHO_COST_2026-09-30.md` §7.6）⇒ 本卡能主張的是「R6 解除」，不是「20+ 可引用」。
* **重尾未判**：同一輪量到 on 臂的輪級掉格 3/4 趟 vs off 0/4（Fisher p=0.071，最壞 187 ms/step 而
  `wall≈pred` ⇒ 不是 I/O 等待）。若 A/A 空對照證明那是**每層同步讀回**的代價，這個補丁的形狀就要先改成
  **每步一次**讀回（或把 top-k 留在裝置端），否則會把 40 次管線排空搬進單段路徑。
* **免費的第二槓桿（若 M1 仍 ≤2/9）**：同一個區塊已經握有本步 routed ids（`ibuf`）且已餵進
  `cache_step_union` ⇒ 可以直接用它對**下一步**的 union 發 fill：完美預測、零新增讀回、不帶 R5 旋鈕。
  它也不動 M1 才轉 §4.2 的引擎手術。

## 4.7 S2-c 的 GPU 驗證（2026-10-01）：**未過** —— fill 開啟時 logits 全 NaN；patch 未套用（已反轉）

S2-c 首次落地成 build：`git apply -p1`（乾淨檔，**不用** `--3way` —— 髒檔加 `--3way` 會以 "does not match
index" 拒絕）、`cmake --build … -j8` 增量 5.5 s、binary 裡 `CGC-RHO-S2C`×2。
⭕ 注意：`r6_witness` 的判準②要求兩份 `engine_digest` 的每個 artifact md5 完全相同 ⇒ **既有錨（09-30 build）
不可重用**；控制臂已用新 build 重跑（`summary_r6ctl-s2c-20261001.json`，M1/M2/M3 **9/9**，22 s）。
見證臂的 env 矩陣（同一 build；都含 `CGC_SEG_BATCH=1`）：

| # | build | 加的旗標 | dump | 判詞 |
|---|---|---|---|---|
| A | S2-c | （都不加） | 有效 | M1 0/9／M2 2/9（重現 09-30；pool 5976／97.4%／41） |
| C | S2-c | `CGC_RHO_PROBE` | 有效 | M1 0/9（courier 交棒、fill 關） |
| D | S2-c | `CGC_RHO_PROBE`＋`CGC_RHO_FILL` | **全 NaN** | 不能判（引擎自己蓋 invalid：993280/993280） |
| a | 無 S2-c | `CGC_RHO_PROBE`＋`CGC_RHO_FILL` | 有效 | M1 0/9（這兩面旗在單段臂上本來是惰性的） |
| b | 無 S2-c | `CGC_SLOT_TABLE_GPU` | **全 NaN** | 不能判（**與 RHO 無關**） |

立項卡的見證臂命令**還帶 `CGC_SLOT_TABLE_GPU=1`**（＋`CGC_SERVER_NO_PREFETCH=0`）⇒ 全組在兩種 build 上都 NaN
（同一根因 b）。⇒ **兩個獨立的 NaN**：

1. **S2-c 的 fill**：capture＋sync 安全（C 有效），一旦 `cgc_rho_prefetch` 真的發 IO 就破壞數值（D）⇒
   這版**不能落地**；要先在引擎側查出「單段臂上做 host 側 pread 進 expert cache」與誰衝突。
2. **`CGC_SLOT_TABLE_GPU`×單段臂**：**先於 S2-c 就存在**（b，不帶任何 RHO 旗標）——它會擋掉任何
   帶 slot-table leaf 的單段臂；也解釋了 L20-1 卡上那條「帶量具的變體吐全 NaN」的家族。

見證行本身是好的：`CGC-RHO-S2C: steps=1 layers=40 bytes=81920 preskip=0（layers/step=40.0）`，
`CGC-RHO-S2C-SKIP` 也在 ntok=207 的批次圖上正確擋下（decode-only 守衛有效）。

**處置**：patch 已反轉（`llama-context.cpp` md5 回到 `54b5bc040189a69bb271583d15dfb9e5`、重建後
`CGC-RHO-S2C`×0），未套用、未落地。帳：`Backup/s2c_2026-09-30/verify_2026-10-01.json`（含 summaries
與 md5）；負控制檢查 `git apply --check` 與 syntax 仍成立。
**下一步（引擎側，依序）**：(i) 修 fill 在單段臂的數值破壞；(ii) 隔離 `SLOT_TABLE_GPU` 的 NaN（它現在擋著
見證設計）；(iii) 見證臂 env 改成上表 **A／C／D 的最小集**（不加 `SLOT_TABLE_GPU`）後重跑 —— 判別句不變
（M1 ≥8/9 ⇒ R6 解除；M1 ≤2/9 且見證行在 ⇒ 轉 §4.2）。
