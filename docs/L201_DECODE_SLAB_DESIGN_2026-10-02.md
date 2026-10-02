# L20-1 方案 A：decode 側 per-layer 緊湊 slab staging（設計）

- 日期：2026-10-02　｜　線：A（加速交付）　｜　Charter：`scripts/check/charters/e-l201-dslab-2026-10-02.yaml`
- 狀態：**設計完成、未實作**（`llama-context.cpp` 現由線 B 活躍編輯，09:14:42 仍在寫）

---

## 1. 為什麼（三條已量的依據）

1. **decode 期間 GPU 利用率只 30–49%**（持續 37–45%；prefill 80–99%）。空轉 ~55%。
   來源：`Backup/cgc_logs/gpu_util_decode_20261002.log`（1 Hz `ioreg -r -c IOAccelerator` 的 Device Utilization %）。
2. **Metal 工作集赤字 ~10 GB**。`budget_gate.py` 口徑：
   `池子 8192（宣告）＋ Metal 峰值駐留 12260 ＋ 保留 1024 ＝ 21476 MiB ＞ 上限 11453 MiB`。
   來源：`Backup/metal_working_set.json`（2026-10-02 09:06）。
   ⇒ **池子（8192）是最大的一項超額**，且它是 decode 唯一獨佔的項。
3. **同引擎、同權重、兩種幾何**：
   - prefill（`CGC_PREFILL_STREAM=1`）→ whole-layer slab，**從不寫 pool** → util 94%。
   - decode → `wt->buffer = pool_buffer`、`ne[2] = slots_per_layer`，即**整個 8 GiB pool 進 Metal** → util 45%。

## 2. 混淆必須先說（否則結論站不住）

prefill 同時具備 (i) slab 幾何與 (ii) 2048 token 的平行度；decode 兩者皆缺。
所以「slab ⇒ 94%」**不能**直接推給幾何。本卡就是把兩者解耦的那一刀：
decode 仍是 1 token（低平行度），只換幾何。若 util 不動 ⇒ 空轉根源不是記憶體 ⇒ 當場轉方案 C。

## 3. 機制

```
現在（decode）                                  方案 A（CGC_DECODE_SLAB=1）
──────────────────────────────                 ──────────────────────────────
pool（8192 MiB, MTL0, 進工作集）                pool 留普通記憶體（不建 Metal buffer）
  │                                              │
  │ wt->buffer = pool_buffer                     │ 每層 memcpy 本步 union 的專家
  │ wt->ne[2]  = slots_per_layer                 ▼
  ▼                                            per-layer 緊湊 slab（K 個專家, MTL0）
mul_mat_id(leaf: expert→slot) ──► pool          │ wt->buffer = slab_buffer
                                                │ wt->ne[2]  = K
                                                ▼
                                              mul_mat_id(leaf: expert→slab 位置)
```

- **K** = 本步該層被路由到的專家 union 大小（decode 單 token ⇒ 8；MTP verify n_tokens=t ⇒ ≤ 8t）。
  slab 容量取 `max(K)` 並向上取整（既有 `cgc_gather_slab_cap()`，預設 64）。
- **bit-identity 的結構保證**：slab 內的位元組是**從池 memcpy 來的同一批位元組**（池又是由同一批
  GGUF 段填入），leaf 只是把「池 slot 下標」換成「slab 位置下標」；`mul_mat_id` 讀到的權重集合不變。

## 4. 插入點（已逐條核實）

| # | 檔案:行 | 現況 | 改動 |
|---|---|---|---|
| 1 | `llama-context.cpp:9483-9499`（`cgc_apply_expert_geometry` 的 decode 分支） | `wt->data = pool_data; wt->buffer = pool_buffer; wt->ne[2] = slots_per_layer` | 前置 `if (cgc_decode_slab) { 指向 slab; ne[2] = K; return; }`（照 prefill 的 early-return 慣例，:9461-9472） |
| 2 | `llama-context.cpp:3878-3912`（remap leaf 寫入）＋ `:3833-3867`（slot-table 寫入） | `td[e] = st[e]`（expert → pool slot） | 改 `td[e] = slab_pos(e)`，非 union → `zero_slot`。⚠ **位置必須在 `ggml_backend_sched_alloc_graph` 之後**（見 `:3916-3919` 註解；2026-09-09 的 bisect 就是寫在 alloc 前而白丟一輪） |
| 3 | `llama-context.cpp:8184-8206`（prefill 雙緩衝 slab 填充） | 從 disk 填 whole-layer | 新增 decode 分支：從**池**填本層 union（`llama_expert_cache_fill_layer_slab` 是「全層從 disk」，需另立變體） |
| 4 | `llama-context.h:498-539`（`cgc_gather_slab` / `cache_gather_slab` / `cgc_db_*`） | 已有雙緩衝 set0/set1 | 復用；只需加一個「decode 用」的選取旗標 |
| 5 | `llama-expert-cache.cpp:4346`（`llama_expert_cache_fill_layer_slab`） | 全層、從 disk | 新增 `fill_topk_slab_from_pool(cache, layer, kind, base, stride, ids, n_ids)` |
| 6 | `llama-expert-cache.h:942-1005` | 既有 accessor | 新增 `pool_get_expert_ro(cache, layer, kind, expert, &ptr, &bytes)`（唯讀取池段指標，省一次拷貝） |
| 7 | `scripts/run_server.sh:1535` 通用轉發清單 | 已含 `CGC_SEG_BATCH*` | 加 `CGC_DECODE_SLAB`（**同一個 inert-knob 陷阱，先加免得白跑**） |
| 8 | `scripts/check/m123_oracle_gate.py` `DIAGNOSTIC_KEYS` | 已有 `CGC_SEG_BATCH_FAST` 條目 | 加 `CGC_DECODE_SLAB` 條目，**聲明哪些位元組不變**（幾何＋ leaf 下標重編號；權重集合與 logits 不變） |

## 5. 陷阱（樹裡已有的教訓，必查）

1. **寫表位置**（`:3916-3919`）：必須在 `alloc_graph` 之後。線 B 剛抓到我在 FAST 臂上寫在 alloc 前。
2. **slab 不能從 disk 取**（`llama-expert-cache.cpp:2426-2437`）：prefill 的 slab 從 disk 讀且不寫池，
   被它服務過的請求會讓 decode 起跑時池是空的（實測 decode miss 1210、capacity=0）。
   **decode 的 slab 必須從池取**，否則 hit rate 直接崩。
3. **`ne[2]` 三處必須同源**（`:9440-9447`）：graph build / eval hook / restore 三處的
   `(data, buffer, ne[2])` 必須來自同一狀態，否則 Metal 報 `buffer is nil`。本設計多了一個 slab 態，
   必須把 slab 態也納入同一套 idempotent 推導。
4. **相位判據用共享函式**：`cgc_is_decode_graph(n_tokens, cgc_decode_max_tokens)`，
   不要自己重寫 `n_tokens > cap`（樹裡有三處踩過）。

## 6. 判決規則（預註冊）

| 情形 | 判詞 |
|---|---|
| M1/M2/M3 9/9 ∧ util ≥ 60% ∧ tg ≥ 13.5 | **成功**（池＝空轉根源；赤字 10 GB → 1–2 GB） |
| M1 ≤ 2/9 | **判死**（緊湊 slab 不保 bit-identity） |
| M1 9/9 ∧ util ≤ 50% | **方案 A 判掉**（池不是根源）⇒ 轉方案 C（平行度），回填看板「不主張」段 |
| util 樣本 < 30 或窗內有並發 llama | 作廢重跑 |

## 7. 量測口徑（照 09-30 operator 立規）

- 入口＝`harness.py bench`（唯一生產入口）；控制臂 `prod-new`、實驗臂 `prod-new:CGC_DECODE_SLAB=1`。
- util／pagein 採樣窗內**不得**有其他 llama-server（上一輪就是這樣作廢的：線 B 的 server 在 07:08 寫 log）。
- 壓縮機 BUSY ⇒ `compressor_pressure.py` 輪詢等 QUIET，**不得**用 `CGC_WINDOW_OVERRIDE`。
- 引用前跑 `quote_gate`；開著 util 儀器的臂**永不單獨引用 t/s**。
