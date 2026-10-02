# FAST 回讀比對的「真實性驗證」——計數器＋陰性對照（2026-10-02，B 線）

> 起因：ace 的長見證 `y-fast-nonmtp-long`（M1/M2/M3 222/222、`taken=195 mismatch=0`）
> 判 `mismatch=0`。但回讀比對的每個 `continue` 都是**靜默**的：
> id 張量 null／不在圖上／dtype 不符／`rd==nullptr` 都會讓「0 mismatch」與「根本沒比對」同形。
> 使用者指示：**加比對次數計數器，並用故意錯的預測確認 mismatch 會紅。**

## 1. 加了什麼（`src/llama.cpp/src/llama-context.cpp`）

1. **比對計數器**（回讀區塊）：
   - `cmp`＝真的做過的 `rd[i] vs slot_table_safe(true_id)` 比對次數
   - `lskip`＝整層被跳過（id 張量 null／不在圖上／非 i32）
   - `nskip`＝逐位置被跳過（負 id／`rd==nullptr`）
   - 新見證行（與 `CGC-SEGBATCH-FAST` 同節奏）：
     `CGC-FAST-VERIFY: vsteps=… cmp=… nskip=… lskip=… mismatch=… fault=…`
   - `CGC-SEGBATCH-FAST-MISMATCH` 行尾追加 `(cmp=… fault=…)`。
2. **陰性對照**：`CGC_FAST_FAULT_INJECT=1` ⇒ 提交前寫 remap leaf 時把每個預測專家 id
   位移 `+1 mod n_expert`（並 ensure 注入的那顆，讓 GPU 讀到真實 row 而非佔位），
   ⇒ 依設計 readback 必須轉紅、41 段兜底必須把 bit-identity 修回來。
   （診斷臂；依構造不可比，永不交付。）

## 2. 兩趟（同 build `libllama.0.dylib=22489dafcaee6716`；長生成 probe、`--probe-max-tokens 220`）

| arm | env | cmp | lskip/nskip | mismatch | M1 vs ref | 產物 |
|---|---|---|---|---|---|---|
| `l201-fastver-ctl` | MTP=0 + FAST=1 | **64000** | 0／0 | **0** | （自寫 ref 222 筆） | `summary_l201-fastver-ctl.json`、`ref_l201_fastver_20261002.jsonl` |
| `l201-fastver-fault` | ＋`CGC_FAST_FAULT_INJECT=1` | **64000** | 0／0 | **0** | **222/222 PASS** | `summary_l201-fastver-fault.json` |

- 64000 ＝ 320 位置/步 × 200 步（40 層 × top-8）⇒ **比對真的在跑**（不是空洞的 0）。
- `fault=1` 進入伺服器行程（見證行印出）＋該趟 probe pool `evictions` 由 **3662 → 26368**
  （注入的 ensure 真的發了 fill）⇒ 注入確實執行。
- **但 mismatch 沒有紅，輸出也沒有變** ⇒ 「故意錯的預測」對這個檢測器**不可見**。

## 3. 判讀（兩種機制，尚未分開）

1. **寫入被丟棄**：`graph_compute` 裡 pre-submit 的 leaf 寫入發生在
   `ggml_backend_sched_graph_compute_async` **之前**，而該呼叫會跑
   `ggml_backend_sched_alloc_graph`；`llama-context.cpp:4611-4616` 的舊註解已具名這個坑
   （「alloc moved remap->data」「ggml_cont copy node overwrites the hook-written ids」）
   ⇒ 寫到舊指標、GPU 與回讀看的是新 buffer（內容仍是「上一步的正確值」）。
2. **被修復**：pool fill/ensure 路徑可能重寫 leaf（`cgc_write_ids_leaf` 一類的維護；本樹已無同名函式，
   但 fill 路徑有 leaf 維護的歷史）⇒ 注入值在提交前被覆蓋回正確映射。

兩者都指向同一結論：**目前 `mismatch=0` 不能用來證明「預測品質好」**；
它也**不能用來證明「兜底不會被觸發」**——因為連故意錯的預測都進不了被比對/被消費的張量。

## 4. 下一批探針（照序，跑前寫死）

- **P1 leaf-witness**：寫 leaf 時把每層前幾個值存起來，回讀時比對「寫的值 vs 現在的值」
  ⇒ `owr>0` ⇒ 寫入被覆蓋/搬移（機制 1/2）；`owr=0` ⇒ 寫入存活但被消費的**不是這顆張量**（另一條路）。
- **P2 消費端的真身**：用既有 `CGC_MMID-ASSERT`／`CGC_SCHED`／`CGC_REMAP_POST_DBG` 之一
  確認本配置 `mul_mat_id` 讀的是 `ffn_moe_topk_remap`（leaf）還是 `ffn_moe_slot_table`（td）。
- **P3 force-red**：`CGC_FAST_FORCE_RED=1` ⇒ 每個接戰步都走兜底（不依賴比對）
  ⇒ 證兜底路徑本身 bit-identical ＋ 計數器/列印會紅（這是「安全網」的獨立驗證）。
- **P4**：P1–P3 清了之後，才跑吞吐 A/B（Δstep）；在那之前**任何 `mismatch=0` 都不引用**。

## 5. 不主張

- 本檔不含任何 t/s；兩趟都是診斷跑（fault 臂不可比）。
- 未 commit（樹共用）。
