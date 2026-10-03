# 樹式驗證（tree verification）設計 —— 把「部分接受的整輪重驗」換成一次 forward 驗多條路徑

**日期**：2026-10-02 · **狀態**：設計，**未動 src** · **立項**：`scripts/check/charters/e-spec-tree-2026-10-02.yaml`

---

## 0. 一句話

我們的 spec 是**鏈**（draft k 顆 → 一次 verify → 部分接受就 `ckpt.load_tgt()` ＋ `continue`，
把已接受的 ids 當下一輪 draft **重驗一次、零產出**）。靶＝**把那筆重驗換成樹式驗證**
（一次 target forward 驗多條候選路徑），因為：
1. 重驗是**純浪費**，且隨 rep 上升；
2. **鏈式沒有候選可選** ⇒ EcoSpec／AcceptMoE／EVICT 那一族（成本感知的 draft 選擇）**無從下手** —
   它們全部假設 draft **樹** ＋ 樹式注意力。

## 1. 為什麼（四條已量的）

| 事實 | 值 | 出處 |
|---|---|---|
| replay（部分接受 → 整輪重驗）隨 rep 上升 | k=2c **2/24 → 14/38 → 20/46** | `docs/L254_K_AXIS_ECON_2026-10-01.md:112-113` |
| 鏈式最佳點仍為負 | k=2 **−5.4%**（11.524 → 10.902，同 session、乾淨窗） | 同上 `:42-49` |
| 成本律 | `round_ms ≈ 63.6 + 35.2·T̂`（每輪固定 **0.73 步**、邊際 verify token **0.41 步**） | 同上 `:105` |
| ★ MTP-on 把**支持集推出池外**（今日新量） | distinct 中位 **139 → 205**；**超 143 槽的層數 19/40 → 41/41**；每步 union 24 槽 → 中位 **17**（`u/req` 0.750） | `docs/MTP_AXIS_REOPEN_REVIEW_2026-10-02.md` §8.3 |

⇒ 最後一條是**本設計的主要約束**：樹會把 verify batch 加寬 ⇒ **更多 cold 專家**。
所以第一版必須**又窄又淺**，而且量測**必須**同時報 pool hit／misses。

## 2. 前提已核實：**樹不需要改共享引擎**

| 需要的能力 | 現況 | 依據 |
|---|---|---|
| 一批裡放不同 `seq_id` 的 token | **支援** | `include/llama.h:262-263`（`n_seq_id`／`seq_id` 為 array-of-array） |
| 一個 KV cell 掛**多個** seq_id（讓共享祖先同時屬於兩條分支） | **支援** | `src/llama-kv-cache.cpp:1137-1138`（`seq_add`） |
| 注意力掩碼按 **seq_id 集合成員** 判定（而非純位置） | **支援** | `src/llama-kv-cache.cpp:1632`（`cells.seq_has(j, seq_id)`）＋ causal `:1647-1651`；無 cache 路徑同構 `src/llama-graph.cpp:473,478` |
| 樹形 batch 的合法性 | **允許**（僅兩條約束） | `src/llama-batch.cpp:337-386`：**同一 seq_id 內 pos 非遞減**、seq 集合不得互相衝突 |
| **現成模板** | **在樹上** | `examples/speculative/speculative.cpp`：`seq_draft` `:20-32`；分支分裂時 `llama_memory_seq_cp` `:531` ＋ 祖先追加 seq_id `:537-538`；葉節點 `common_batch_add(..., { s }, true)` `:582` |

⇒ **要做的事落在 spec 層與 bench 的 verify batch 構建器**；
`src/llama-batch.cpp`／`src/llama-graph.cpp`／`src/llama-kv-cache.cpp` **一行都不用改**
⇒ **MTP-off 的逐位元不變是構造性的**（不是「跑過 D5 才知道」）。

## 3. 設計：T1 ＝ 深度 1 分支、b=2 的**最窄樹**

```
pos   P        P+1   P+2        P+1   P+2          ← 兩個分支共用 pos（不同 seq）
             ┌── A1 ── A2                        seq 0
根 id_last ──┤                           seq {0,1}
             └── B1 ── B2                        seq 1
```

- **形狀**：根＝`id_last`（掛 `seq {0,1}`）；分支 A＝MTP draft 的第 1 顆 ＋ 續走 k−1 步；分支 B＝
  **第 1 位的第 2 候選**（`common_sampler_get_candidates` 已經在拿 top-3，見
  `common/speculative.cpp:1850`）＋ 各自續走。
- **verify batch**（k=2）：`1 + 2×2 = 5` tokens（鏈式是 3）。
- **接受**：走樹找**最長被接受路徑**（每個節點拿 target argmax 與其子節點比對）；接受後把**未選中兄弟分支**
  的 KV 用 `llama_memory_seq_rm` 清掉，並把勝出分支的 seq 併回 base。
- **為什麼這麼窄**：因為 §1 最後一條。先問「**最窄的樹能不能把那筆 replay 換成有效產出**」；
  能，再談加寬／深度。

## 4. 門控與逐位元

- 新旗標 **`CGC_SPEC_TREE`（預設關）**。開＝走樹；關＝**逐位元走原鏈式**。
- 只動三處，**都不在 MTP-off 路徑上**：
  1. `common/speculative.h/.cpp` — 在 `common_speculative_draft_params`（`speculative.h:47-79`）
     增一個**預設為空**的候選欄位（例如 `alt`），MTP impl（`speculative.cpp:1730-1890`）在
     原本只取 `cur_p->data[0].id` 處**多填**第 2 候選。**關閉時欄位為空、行為不變。**
  2. `tools/llama-bench/llama-bench.cpp` — verify batch 構建器（`:3079-3084`）與接受段
     （`:3152-3205`）在 `CGC_SPEC_TREE` 開時走樹；關時走原路。
  3. （可選）`scripts/run_server.sh` — 轉發 `CGC_SPEC_TREE`（**否則靜默丟棄**，這是本 repo 的已知坑）。
- **不動**：`llama-batch.cpp`／`llama-graph.cpp`／`llama-kv-cache.cpp`／`llama-context.cpp` 的 verify 快路徑。

## 5. 預註冊（跑前寫死；完整版在 charter）

**機制端點（抗噪，優先）**
- `replay` 比例：鏈式 k=2 為 **66/208 = 31.7%**（`L254:99-100`）⇒ 目標 **<10%**。
- `emit_per_round`（真口徑）：鏈式 k=2 為 **1.625**、k=2b **1.806** ⇒ 目標 **≥1.9**。
- **必須同時報** pool `hit%` 與 `misses`（樹會推高；不報就不算完成）。
- **治療見證**：新增一個「本輪走了樹」的計數（照 `CGC-LEAFSPLIT` 的教訓 **開新 tag、不加長既有行**）。

**速度端點**
- 同 session ABBA（`tree` / `chain` / `chain` / `tree`），判據 **配對差 ≥ +5%**（k=2 的破線缺口是 +5.4%）。
- 絕對 t/s 需乾淨窗才可引用；機制端點隨時可讀。

**否證（任一成立即判「樹式在 16 GB 盒上不成立」並收束本軸）**
- 配對差 **≤0**；或
- `replay` 比例**沒降**（<20% 的相對降幅）；或
- pool `misses` 的漲幅 **>** `emit_per_round` 的漲幅（＝多驗的 token 被 IO 吃回去）。

## 6. 代價與風險（如實）

1. **池**：verify batch 3 → 5 tokens（k=2）⇒ 冷專家變多。`llama-context.cpp:8551-8600` 的
   `verify_fast` 快路徑有一道 **cold guard**（冷專家比例過高就退回 exact `ensure_batch`）
   ⇒ 樹可能**自動關掉快路徑**、成本反而上升。這是本設計最可能死的地方，**第一趟就要看它**。
2. **正確性**：樹的接受是**新寫的走訪邏輯**。緩解＝**最強見證**：
   **把樹退化成寬度 1 的樹，必須與原鏈式逐位元相同**（同一 seed、同一 prompt、token 序列逐位比對）。
   這一條**必須先過**，否則後面的數字沒有意義（同 `eng-mh-0082` 的教訓：先證明「治療真的施加」）。
3. **不承諾 t/s**；機制端點先行。整條軸的判死與否交給 §5 的否證條件。

## 7. 實施順序（每一格都是可停的）

| 格 | 內容 | 停損點 |
|---|---|---|
| **T1a** | `common/speculative.*` 暴露第 2 候選（預設空、行為不變） | 建置＋原鏈式行為不變 |
| **T1b** | `llama-bench.cpp` 樹 batch ＋ 走樹接受（`CGC_SPEC_TREE`） | 建置 |
| **T1c** | **等價性見證**：寬度 1 的樹 ≡ 鏈式（逐位元） | **不過就停，不往下** |
| **T1d** | D5（MTP-off `--tag` 需 9/9） | 不過即回退 |
| **T1e** | 機制診斷：`replay`／`emit_per_round`／`hit%`／`misses`／cold-guard 是否被觸發 | 見 §5 否證 |
| **T1f** | 同 session ABBA（tree vs chain） | 見 §5 否證 |

## 8. 出處

- 立項：`scripts/check/charters/e-spec-tree-2026-10-02.yaml`
- 既有判詞與成本律：`docs/L254_K_AXIS_ECON_2026-10-01.md`
- 今日路由實測（本設計的約束來源）：`docs/MTP_AXIS_REOPEN_REVIEW_2026-10-02.md` §8
- spec API／bench 迴圈：`src/llama.cpp/common/speculative.h:43-106`、
  `src/llama.cpp/tools/llama-bench/llama-bench.cpp:3040-3205`
- 樹式模板：`src/llama.cpp/examples/speculative/speculative.cpp:20-32, 531-538, 582`
- 掩碼機制：`src/llama.cpp/src/llama-kv-cache.cpp:1137-1138, 1632, 1647-1651`；
  `src/llama.cpp/src/llama-graph.cpp:473,478`；`src/llama.cpp/src/llama-batch.cpp:337-386`

---

## 9. 【實作回填 2026-10-02】§2「樹不需要改共享引擎」**不完整**——漏了三個 context 前提

§2 的表格查的是 **batch／mask 的 API 能力**（那些確實成立），但**沒查 context 自身的容量與能力**。
實作 T1b 時撞到三條，全部是 **context 參數**（不是引擎程式碼），但**都要花記憶體**：

| # | 前提 | 現況 | 為什麼樹需要 | 代價 |
|---|---|---|---|---|
| 1 | **seq id 容量** | bench 與 production 的 context 都來自 `llama_context_default_params()`＝**`n_seq_max=1` ＋ `kv_unified=false`** | 分支＝同一 batch 裡的**第二個 seq**，而 `seq_id=1` 在此越界：`llama-context.cpp:2593` 讓 `llama_decode` 回 -1 | 需 `kv_unified=true`（單一 KV stream，**不翻倍**）或 `n_seq_max=2`（非 unified ⇒ KV stream ×2、且 `n_ctx_seq` 折半） |
| 2 | **recurrent 狀態回滾** | 本模型是 **GDN hybrid**（arch `LLM_ARCH_QWEN35MOE`，`llama-model.cpp:2313-2324` 走 `llama_memory_hybrid`） | 走完樹要**就地裁掉敗方與未接受後綴**；recurrent 半邊**不能就地刪後綴**（`llama-memory-recurrent.cpp:170`） | 需 `n_rs_seq>0`；而 RS buffer ＝ `mem_size×(1+n_rs_seq)`（`:99`），**線性乘上記憶體**。`llm_arch_supports_rs_rollback()` 對該 arch **回 true**（`llama-arch.cpp:1002`）⇒ 可用 |
| 3 | **batch 的 seq 槽** | `llama_batch_init(n_tokens, 0, 1)` 的 seq_id 槽**每 token 只有 1 個** | 樹根**一個 token 掛多個 seq**（讓共享前綴只佔一格） | 必須按樹寬放大 `n_seq_max`，否則寫越界 |

**第 2 條的具體張力（本軸最該先回答的問題）**：`n_rs_seq` 是**線性**記憶體成本，
而這台 16 GB 盒子**本來就貼著** ~15.5/16 GB（8 GiB pool ＋ 13.6 GB 模型 ＋ ngl 99）。
2026-10-02 用 `n_rs_seq=4`（＋ 8 GiB pool）冒煙，直接把機器打進 swap 並凍住 UI
（`Writable regions 15.8 GB`、swap 6.0/7.0 GB、`WindowServer … userspace_watchdog_timeout`）。
⇒ **樹的可行性取決於「這台盒子還付得起多少新增記憶體」**，不是算術。見 charter 的 follow-up 節。

### 實作狀態（§7 的表格回填）
- **T1a** ✅ 完成（`common/speculative.*` 暴露 `alt`；另補一處 lockstep 缺口：MTP impl 在 `result` 太短而清空時**沒清 `alt`**）。
- **T1b** ✅ 實作完成、**建置通過**（`llama-bench.cpp`：樹 batch ＋ 走樹接受 ＋ `CGC_SPEC_TREE` 門控
  ＋ 見證 `CGC-SPEC-TREE`／逐位元 `CGC-SPEC-DUMP`；`run_server.sh` 白名單加 `CGC_SPEC_TREE`／`_B`／`_RS`／`LLAMA_BENCH_SPEC_DUMP`）。
- **T1c** ⏳ **仍未跑**。2026-10-02 的第一次嘗試因記憶體包絡過大而**兩臂都在 `test_gen` 的 Metal status 5 上 abort**
  （backtrace 不在 spec 路徑 ⇒ 樹代碼一次都沒跑到，該趟判據無效）。已把 `n_rs_seq` 由 4 降為 **2**（＝ k，深度 1 樹所需最小）
  並改用 **4 GiB** pool 重跑。
