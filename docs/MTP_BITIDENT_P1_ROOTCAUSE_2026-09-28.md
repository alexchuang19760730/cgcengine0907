# MTP ON 非 bit-identical —— **P1 根因定位**（2026-09-28）

> 狀態：**根因已定位到具體 op 家族 ⇒ 判定 H8 成立（建議關閉本立項，改開「口徑重定義」）**
> 依據 `docs/MTP_BITIDENT_CHARTER_2026-09-28.md` §3／§6 · 全文補充 `MTP_BITIDENT_P0_RESULT_2026-09-28.md`
> 方法：1 次 build（新增 `CGC_GRAPH_NAMES`）＋ 2 趟 0-t/s 量測 ＋ 純讀 log。**沒有跑任何 t/s。**

---

## §1 一句話根因

**MTP on 會把 `cparams.n_rs_seq` 從 0 變成 > 0**，而 `src/models/delta-net-base.cpp:494` 的
`if (cparams.n_rs_seq == 0)` 是**兩條完全不同的 recurrent（DeltaNet／GDN conv）狀態路徑**的分岔：

| | `n_rs_seq == 0`（**OFF**） | `n_rs_seq > 0`（**ON**） |
|---|---|---|
| 分支 | 單槽位原地寫回：`s_slot = 0` | **`[TAG_RECURRENT_ROLLBACK_SPLITS]`**：`K = n_rs_seq + 1`，對 `t=1..K` 寫 **K 個回滾槽位** |
| graph 節點 | `conv_state_update=60`、`gdn=30`、`conv_input=30`、`cache_r_l=150` | `conv_state_update=**0**`、`gdn=**60**`、`conv_input=**150**`、`cache_r_l=**390**` |

⇒ 兩側的 **GDN／conv 計算本身就不一樣**（`gdn` 30→60、`conv_input` 30→150 是**計算份數**變了，
不只是寫回位置變了）⇒ 從**第一個 decode** 起數值就不同。**這就是那 0.16%。**

**為什麼 MTP on 一定要走這條**：recurrent（GDN／conv）狀態不像 KV 那樣能靠截斷回滾 ⇒ draft 被拒時
必須有**多個可回退的狀態槽位** ⇒ `n_rs_seq > 0` 是 **spec 正確性的必要條件**，不是設定偏好。

---

## §2 證據鏈（逐條可查）

### 2.1 graph pass 結構（新儀器 `CGC_GRAPH_NAMES=6`，log 逐字）

| 臂 | pass 序列（`ctx_type / ntok / n_nodes`） |
|---|---|
| **OFF** | `0/2/3847`（warmup）→ `0/182/3847` → `0/24/3847` → `0/4/3847`（prefill 分塊）→ **`0/1/3847`（第一個 decode）** → `0/1/3847` |
| **ON** | `0/2/4117`（warmup）→ `0/210/4117`（prefill）→ **`1/1/92`（draft ctx，獨立小圖）** → **`0/4/4117`（第一個 target forward＝verify 批）** → `0/3/4117` |

⇒ target 的 graph 連**節點總數都不一樣**（3847 vs **4117**，+270）。

### 2.2 ⛔ 關鍵對照：**這不是 batch 大小造成的假象**

逐 pass 統計（`ON` 在 **warmup 的 `ntok=2`** 就已經是同一套）：

| 臂 | 每一個 pass 的 recurrent 節點數（全部一致，與 `ntok` 無關） |
|---|---|
| OFF | `conv_state_update=60  cache_r_l=150  conv_input=30  gdn=30` |
| ON  | `conv_state_update=**0**  cache_r_l=**390**  conv_input=**150**  gdn=**60**` |

⇒ OFF 在 `ntok=1/2/4/24/182` 下都走原地路徑；ON 在 `ntok=2/3/4/210` 下都走回滾路徑。
⇒ **與 `n_tokens` 無關**（所以 P0-3 把 `n_max` 降到 1、`n_tokens` 4→2 卻 logit 不變，一點都不矛盾）。

### 2.3 源碼行

- `src/models/delta-net-base.cpp:494` `if (cparams.n_rs_seq == 0) {` —— 單槽位；
  `:512 else {` + `:513 [TAG_RECURRENT_ROLLBACK_SPLITS]` + `:517 K = n_rs_seq + 1` + `:519 for (t=1..K)` —— 多槽位。
- `src/llama.cpp/src/llama-context.cpp:125` `cparams.n_rs_seq = params.n_rs_seq;`
  （`:126` 會對不支援回滾的 arch clamp 回 0 —— 本模型**支援**，所以 ON 側沒被 clamp）。
- 旁證 `ggml-metal-ops.cpp` 的註釋：`delta-net-base.cpp:496` 的 `ggml_cpy(conv_state_last → conv_state_update)`
  是 recurrent 狀態的寫回點。

### 2.4 與既有 8 鍵 ablation 的一致性（全部對上）

`conv_input` 30→**150**＝ 5 倍 ⇒ `K = 5` ⇒ `n_rs_seq = 4`（≈ `n_max 3 + 1`）。
而 **P0-3（`n_max=1`）的 logit 仍然是 19.258354**（槽位數 `K` 應該隨 `n_max` 變成 3）⇒
⇒ **決定數值的不是槽位數量 `K`，而是「走了 else 分支」這個二值選擇**。
⇒ 這解釋了為什麼 pool 4/8 GiB、`draft-ngl 0`、`layer-caps 16/256`、8 個 env 鍵**全部無效** ——
   沒有一個碰得到 `n_rs_seq`。

---

## §3 它同時解釋了之前所有「奇怪」的觀察

| 既有觀察 | 現在的解釋 |
|---|---|
| `pos=1`（prefill 尾）全臂 bit-identical | prefill 階段 recurrent 狀態是**初始化**，兩條路徑尚未分岔出可見差異 |
| `pos=209`（**第一個 decode**）就分歧 | 第一次 recurrent 狀態**更新**：ON 走多槽位計算、OFF 走原地寫回 |
| 分歧出現在**任何 draft forward 之前** | 路徑是在 **ctx 建立時**（`n_rs_seq` 決定）就選好的，與 draft 跑沒跑無關 |
| pool／`draft-ngl`／`layer-caps`／8 個 env 鍵全無效 | 沒有一個改變 `n_rs_seq` |
| `CGC_MM_BITIDENT=1` 兩側都開卻仍差 0.16% | 它保證的是 **mul_mat 家族**；這裡分岔的是 **gdn／conv recurrent**，不在其覆蓋範圍 |

---

## §4 判定：**H8 成立**（依立項卡 §3／§6）

- ⛔ **不能靠改設定修**：`n_rs_seq > 0` 是 spec 正確性的必要條件（draft 被拒要能回滾 recurrent 狀態）。
  強迫 `n_rs_seq = 0` 會讓**被拒的 draft 留下錯的 recurrent 狀態** ⇒ 那是用正確性換 bit-identical，
  違反「M1/M2/M3 是護欄不是獎勵」。
- ⚠ **能修，但屬 compute 層改造**：要讓「多槽位 batched recurrent」與「單槽位原地 recurrent」在
  同一 token 上**逐位相同**（相當於把 `CGC_MM_BITIDENT` 那套保證延伸到 gdn／conv）。
  這是引擎核心改動、**可能拖慢 spec** ⇒ 收益（讓 ON/OFF 的 t/s 可比）< 成本。
- ⇒ 依立項卡 §6「失敗定義」與 §3 H8：**關閉本立項**，另開「**口徑重定義**」立項
  （把 MTP on 明確定位為**另一個輸出函數**，不再當純加速開關）。

**⛔ 不要為了達標去放寬 A1–A3**（換 prompt、換 cell、只比前 N token 都不行）。

---

## §5 衝擊（既有結論的確認與擴大）

- **交付口徑維持 MTP off**（`prod-new` 預設 `CGC_SERVER_MTP=0`），**不改任何東西**。
- ⛔ **ON/OFF 的 t/s 不可互比** —— 現在不只是「觀測到答案不同」，而是**知道它們連 recurrent 計算都不一樣**。
- ⛔ 任何「MTP 加速 X%」的數字**全部不可引用**（含 `12.57`、m1 排行榜 13.099 等）。
- ★ 附帶收穫：`n_rs_seq>0` 也意味著 **ON 的 recurrent 狀態記憶體與計算量都比較大**
  ⇒ 之前「ON 比較慢（11.92 → 7.34）」除了 pool／draft ctx 之外，**recurrent 多槽位計算本身也是一項成本**。

---

## §6 本次產物與改動

- **新增儀器**（1 次 build）：
  - `src/llama.cpp/src/llama-context.cpp`（`process_ubatch`，`build_graph` 之後）：`CGC_GRAPH_NAMES=<n>`
    印前 n 張 graph 的節點名。**不讀張量、不釘 buffer** ⇒ 不像 `CGC_TENSOR_CAPTURE` 那樣擾動排程；
    **預設 0 = 關閉** ⇒ 不改任何既有臂的行為。
  - `scripts/run_server.sh`：allowlist 加 `CGC_GRAPH_NAMES`（未列的 `CGC_*` 會被**靜默丟棄**）。
    ⚠ 該檔有 **2 行非本線的 modified**，本輪只在別處新增、沒碰它們。
- **輔助腳本**：`Backup/m123_oracle_gate/p1_graph_names.py`（解析 pass／節點名）。
- **量測**：`dump_p1names_{on,off}.jsonl` ＋ log `llama_server_20260928_135531.log`（ON）／
  `135635.log`（OFF）。sanity：新 build（`libllama.0.dylib=6375e39296c349e3`）下
  ON `pos=209` 仍 19.258354、OFF 仍 19.227451，且 OFF 對 OFF 參考 **M1/M2 50/50 PASS**
  ⇒ 新儀器**沒有改變任何既有行為**。
- ⛔ **P0-5 不成立**：`CGC_SERVER_MTP_N_MAX=0` 觸發 `GGML_ASSERT`（`llama-context.cpp:3057`）。
