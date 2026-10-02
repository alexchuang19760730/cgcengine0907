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
