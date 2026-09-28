# `rc=-6` / `CGC-METAL-FAIL compute #41` 根因盤點（2026-09-28）

> 範圍：只回答「MTP on 為什麼在權威 cell 裡 rc=-6、該從哪裡下手」。
> **本輪全部是靜態分析（讀碼＋讀既有 dated 產物），沒有跑任何量測、沒有 build、沒有改任何 shared 檔。**
> 證據分兩級，全文標注：**【碼證】**＝可由當下工作區的程式碼直接驗證；**【推論】**＝由既有讀數推出，附可證偽的預測。

## 0. 一句話

`rc=-6` 不是 kernel 崩潰、也不是 MTP 特有的 bug —— 它是 **Metal OOM（status 5）在第 41 次
`graph_compute` 被觀測到**，而超額的 ~1.28 GiB 有 ~80% 是**分配形狀**（draft ctx 用目標的
`cparams` 建出來，`n_ctx=0` 放大成 `n_ctx_train=262144`、`n_ubatch=5632`）不是真工作量。
樹上已經有兩道閘可以砍掉它，但**從來沒有被一起開過**，而且量測入口上還有第三個攔路虎（§4）。

## 1. 【碼證】`compute #41` 是**計數器位置**，不是 kernel ID

`src/llama.cpp/ggml/src/ggml-metal/ggml-metal-context.m:225` 起的 `cgc_metal_record_error()`：

```c
ctx->err_n_computes = (int64_t) atomic_load_explicit(&ctx->cgc_n_computes, memory_order_relaxed);
...
GGML_LOG_ERROR("CGC-METAL-FAIL: command buffer %d failed with status %d (compute #%lld, %s) desc=%s\n", ...)
```

- `compute #N` ＝ **第 N 次 `graph_compute`**（`cgc_n_computes` 累加器），是**時間位置**。
- `desc=` 才是原因：`Insufficient Memory (00000008:kIOGPUCommandBufferCallbackErrorOutOfMemory)`。
- 記錄是 **idempotent（只留第一次）** ⇒ 「#41」出現代表第一次失敗就在第 41 次提交，
  **不是**「第 41 號 kernel 有 bug」。

⇒ 追「compute #41 是哪個 kernel」是錯誤方向；要追的是**為什麼第 41 次提交時工作集已經撐爆**。
（同一形狀的既有紀錄：`compute #2`／`#82`／`#369` 都出現在別的 OOM 現場 —— 見
`Backup/cgc_logs/llama_server_20260915_233925.log`、`..._20260920_130827.log`、`..._20260916_133211.log`。）

## 2. OOM 的算術（`docs/ACCEPT_LEVERS_AND_DIRTY_BOX_PAIR_2026-09-26.md` §11 的 census）

| item（Metal, MiB） | off | **on** | Δ |
|---|---|---|---|
| MTL0 model buffer（weights） | 7421.91 | 7724.86 | **+302.95** |
| main-context KV | 26.56 | 26.56 | 0 |
| **draft-context KV** | — | **512.00**（262144 cells × 1 layer, f16） | **+512.00** |
| **draft-context compute buffer** | — | **493.00** | **+493.00** |
| draft CPU compute buffer | — | 264.02（host，不算 Metal） | — |
| main compute / recurrent RS / gather slabs | 2477.85 / 62.81 / 356.00 | 同 | 0 |
| `recommendedMaxWorkingSetSize` | **11453 MB** | | |

- off 的 Metal 常駐 ≈ 7421.91 + 26.56 + 2477.85 + 62.81 + 356.00 = **10345.13 MiB**。
- 可用餘額 ＝ **0.56–1.08 GiB**（11453 若為 MiB ⇒ 1108 MiB；若為 10^6 bytes ⇒ 10920 MiB ⇒ 575 MiB）。
- MTP-on 的 Metal 增量 ＝ 512.00 + 493.00 + 302.95 = **1307.95 MiB ≈ 1.28 GiB > 兩解** ⇒ 必爆。

**哪一部分是「真工作」**：只有 MTP head 的權重（檔案 50.1 MiB）。§11 明記 Metal 把它放大 ≈6×
成 +302.95 MiB，且「仍未被解釋、是直接坐在天花板上」的第三項。其餘 ≈1005 MiB 純粹是形狀。

## 3. 【推論】兩道閘各砍哪一塊，為什麼單開 `CTX_ALIGN` 救不回來

`src/llama.cpp/common/speculative.cpp`：

- `:2573` `CGC_DRAFT_CTX_ALIGN=1`（§11）→ `cparams.n_ctx = llama_n_ctx(ctx_tgt)`。
  只**換 KV 的寬度**：512 MiB → ~5 MiB。
- `:2608` `CGC_DRAFT_SMALL_BATCH=1`（§11b）→ `cparams.n_batch/n_ubatch = max(floor, n_max+1)`（預設 `n_max=3` ⇒ **4**）。
  只**換 graph reserve 的寬度**。

關鍵在 `src/llama.cpp/src/llama-context.cpp:1086`（第二處同形 `:1357`）：

```c
const uint32_t n_tokens = std::min(cparams.n_ctx, cparams.n_ubatch);
```

⇒ **單開 `CTX_ALIGN`：`n_ctx` 變小了，`n_ubatch` 還是 5632**，`n_tokens = min(n_ctx_draft, 5632)` 仍以
`n_ctx_draft` 為寬 ⇒ 那份 ~493 MiB 的 graph buffer **幾乎沒動**（寬度從 5632 降到 2560，約 −55%，
仍數百 MiB）。剩下 ≈800 MiB 增量，仍大於 575 MiB 的緊解 ⇒ 照爆。

**可證偽的預測**（下一次實測就能判）：
- 只開 `CTX_ALIGN` ⇒ 仍 rc=-6（與 baseline 的「加不加都死」一致 ✔）。
- `CTX_ALIGN + SMALL_BATCH` ⇒ `n_tokens = min(2560, 4) = 4`，draft graph 掉到幾 MiB，
  Metal 增量 ≈ **+310 MiB**（只剩 model buffer 的 +302.95）⇒ 餘額 575 MiB 下**約剩 265 MiB，應可過**。
- 若仍爆 ⇒ 表示 +302.95 MiB 那項或 host 側 264 MiB 才是真瓶頸，得改攻第三項。

## 4. ★【碼證】攔路虎：在 bench 路徑上，`CGC_SERVER_MTP=1` **是 inert 的**

`scripts/check/charters/exp-mtp-onoff-standard.yaml` 的第 2–4 臂寫的是
`prod-new:CGC_SERVER_MTP=1`（＋ `CGC_DRAFT_CTX_ALIGN` / `CGC_DRAFT_SMALL_BATCH`）。
**這三個臂開不了 MTP。** 三條獨立碼證：

1. **引擎端沒人讀它**：`grep -rn "CGC_SERVER_MTP" src/` 只剩兩處**註解**
   （`src/llama.cpp/src/llama-expert-cache.h:339`、`src/llama.cpp/src/llama-context.cpp:5391`），
   `getenv("CGC_SERVER_MTP")` 在整棵 `src/` **0 命中**。它只是 `scripts/run_server.sh:88` 的 shell 變數。
2. **`--spec-type` 不會被轉發進 llama-bench**：`run_server.sh:1369` 確實會吐 `--spec-type draft-mtp`，
   但 `scripts/check/llama_bench_matrix.py:205-214` 的 `FORWARD_VALUED`／`FORWARD_BARE`
   **兩個表都沒有 `--spec-type`** ⇒ `forward_argv()` 直接把它丟掉。
3. **llama-bench 只有 CLI 一條路**：`src/llama.cpp/tools/llama-bench/llama-bench.cpp:381-385`
   的 `spec_type` 是 CLI surface，`:660` 明寫「only 'draft-mtp' is implemented here」；
   `:2799` 甚至留了一段自述註解：「CGC_SERVER_* keys that silently never reached the engine」。

⇒ 唯一能開 MTP 的是 `scripts/check/harness.py:1079`（`if args.spec_type: cmd += ["--spec-type", ...]`），
也就是 **`--spec-type draft-mtp` 這個 CLI 引數**。

⚠️ 附帶：`harness.py:53` 的 USAGE 範例自己就寫著
`--arm "prod-new:!CGC_WAKE_POLL_US=999;CGC_SERVER_MTP=1"` —— **這行教學是錯的**，而且
`_EXPERIMENT_KNOBS`（`:808`）還把 `CGC_SERVER_MTP`／`CGC_SERVER_MTP_N_MAX` 列進白名單，
所以「設了」不會被擋、也不會被警告 ⇒ 靜默開不到。這正是 `llama-bench.cpp:2799` 在講的那類坑。

## 5. 【碼證】`CGC_DRAFT_SMALL_BATCH` 見證行**從來沒有觸發過**

`speculative.cpp:2626` 有見證行 `CGC-DRAFT-SMALL-BATCH: applied path=mtp ...`（條件在 `:2609`）。

- `grep -rl "CGC-DRAFT-SMALL-BATCH" Backup/ docs/` ⇒ **0 命中**。
- 相對地 `CGC-DRAFT-CTX-ALIGN` 只在**兩支 server log** 出現過
  （`Backup/cgc_logs/llama_server_20260927_101715.log`、`..._103405.log`），
  內容是 `n_ctx_req=8192 -> n_ctx_draft=8192 (target ctx=8192)` —— **server 路徑、ctx 8192，不是 bench cell**。

⇒ §3 預測的那個「關鍵那一刀」在這個 repo 裡**從沒被實跑過**。

⚠ **2026-09-28 補**：§6 那趟已把兩個 `CGC_DRAFT_*` 都設了、實跑 reps=3，**見證行仍然兩條都沒印** ——
原因不是開關沒傳到（同 log 裡 `CGC_PREFILL_STREAM=1` 的見證有印、且二進位
`libllama-common.0.0.630.dylib` 用 `strings` 驗過確實含這兩個字串），而是**那條程式路徑沒被走到**
（見 §8.3：draft context 全程沒建立）。

## 6. 修正後的指令（**已執行 ⇒ 仍然 rc=-6**，見 §8）

```sh
python3 scripts/check/harness.py bench \
  --arm "prod-new:CGC_DRAFT_CTX_ALIGN=1;CGC_DRAFT_SMALL_BATCH=1" \
  --spec-type draft-mtp \
  --json /tmp/mtp_on_shape_fixed.json
```

- `--spec-type draft-mtp` 是**唯一**能讓 `spec_mtp=true` 的入口（`SMALL_BATCH` 的條件是
  `small_batch && spec_mtp` ⇒ 少了它，兩個閘都不會動）。
- 兩個 `CGC_DRAFT_*` 鍵不在 `_EXPERIMENT_KNOBS` 也不在 prod-new dump ⇒ 算**新增鍵**，gate 不擋、
  進 `extra_env`；且 `run_server.sh:1531-1532` 的 env allowlist 已含這兩個鍵 ⇒ 能進到引擎。
  若 gate 抱怨，改用 `!KEY=1` 顯式宣告。
- 要同時滿足 `mtp_promotion_gate.py` v2：必須**配對設計**、off/on 各 ≥2 reps、自帶同 session off 對照。

## 7. 限度

- §2 的 `recommendedMaxWorkingSetSize` 單位在原文只寫「MB」，**兩種解都導向「必爆」，但餘額差 2×**；
  §3 的「剩 265 MiB」用緊解（575 MiB），寬解下則剩 ~800 MiB。
- §3 是**推論**，不是量測。它對「單開 CTX_ALIGN 仍爆」這件事與既有紀錄一致，但「兩個都開能過」
  還沒有任何一支實跑支撐。
- §4／§5 是碼證，但「charter 是否真有人跑過、跑出什麼」不在本輪範圍；
  `known_on_state_2026-09-27` 列的三個 artifact（`/tmp/ctxalign_harness_0927.log`、
  `/tmp/mtp_decode_ab_warm/report.json`、`/tmp/census_align/on_align.buffers.txt`）**現已全部消失**
  （`/tmp` 被清）⇒ 那個 `n_ctx_draft=2560` 的見證目前**不可重建**。

## 8. ★ 實跑結果（2026-09-28 03:31–03:42，本輪唯一一趟真跑）

### 8.1 第一次嘗試被自己的閘門擋掉

帶 `--reps 1` 的廉價探針**沒有跑**：cell contract fail-closed —

```
⛔ cell 口徑與測試卡權威 block 不一致 — 拒跑（fail-closed）：
   - reps: 實際 1 ≠ 權威 3
```

（口徑維度是 fail-closed 的，沒有「探針降價」這條路。）

### 8.2 第二次（reps=3，權威 cell）——**仍然 `rc=-6`**

```
python3 scripts/check/harness.py bench \
  --arm "prod-new:CGC_DRAFT_CTX_ALIGN=1;CGC_DRAFT_SMALL_BATCH=1" \
  --spec-type draft-mtp --charter waive --waive-reason "…" --json /tmp/mtp_on_shape_fixed.json
```
盒況乾淨（swap 815 MiB、compressor quiet、thermal NOMINAL），`cell 口徑校驗通過（嚴格維度 15 項一致）`，
實際命令帶 `--spec-type draft-mtp`、`arm env = {CGC_DRAFT_CTX_ALIGN:1, CGC_DRAFT_SMALL_BATCH:1}`，
build `630`。結果：

```
!! arm exited rc=-6
/…/ggml-metal-context.m:925: CGC-METAL-FAIL: command buffer 0 failed
   (status 5, Insufficient Memory (00000008:kIOGPUCommandBufferCallbackErrorOutOfMemory))
0  libggml-base  ggml_print_backtrace
1  libggml-base  ggml_abort
2  libggml-metal ggml_metal_synchronize
3  libggml-base  ggml_backend_sched_synchronize
4  libllama      llama_context::synchronize()
5  libllama-bench-impl  test_prompt(llama_context*, int, int, int)
6  libllama-bench-impl  llama_bench(int, char**)
```

### 8.3 ★ 兩個新事實（都推翻本文件原本的追查方向）

1. **崩在 prefill，不在 draft/verify。** 堆疊是 `test_prompt` → `llama_context::synchronize`
   （`:0` 沒有 decode 影子）。也就是說：炸掉的是**目標 context 自己的 prefill**。
2. **draft context 全程沒被建立。** `stderr` 裡 `expert_index built` 只出現 **1 次**、
   `ctx#` 只有 **ctx#1**（after-TG / after-PP）、沒有第二個 model load、`CGC-DRAFT-*` 見證行
   **兩條都沒印**。而 draft ctx 一旦建成，構造子（`speculative.cpp:2536` 起）必然先印見證、
   再走 `llama_model_load_from_file()` 印第二次 `expert_index built`。
   ⇒ 這趟**根本沒走到 draft ctx 那條路**。

### 8.4 因此 §3／§5 的正確讀法要改

- §3 的預測（「兩閘齊開剩 ~310 MiB，可過」）**沒有被檢驗**，不是被證偽 —— 因為那兩個閘所在的
  程式路徑（`common_speculative_init_result` 構造子）這趟沒執行到。
- 但**它也救不了這一次**：我們死在更早的地方（target prefill），跟 draft ctx 的記憶體形狀無關。
- 原本「第 41 次提交時被 draft 增量撐爆」的說法，對**這趟**不成立（缺那一大塊的前提下就已經爆）。

### 8.5 ~~一條還不能用的線索~~（已由 §9 同 build 對照**作廢**）

> ⛔ 本節寫的「OFF 5599.48 MiB vs ON 2530.72 MiB」是**不同 build 的比對**（那支 OFF 的 head 不明，
> `expert_index` 條數就不同）⇒ **不可引用**。真正的同 build 數字見 §9。

## 9. ★ 同 build 同 cell 的 OFF 控制臂（03:50–03:52，解除混淆）

`python3 scripts/check/harness.py bench --arm "prod-new"`（**不帶** `--spec-type`），
同一 build 630（dylib mtime 未變）、同一 cell、同一盒。結果：**存活**，出 JSON。

| 指標 | OFF（03:52） | ON（03:32，`--spec-type draft-mtp`） | 差 |
|---|---:|---:|---|
| `expert_index built` | **30720** | **31488** | **+768** |
| model load 次數 | 1 | 1 | 同 |
| `ctx#1 after-TG / after-PP sched` | **2530.72 MiB** | **2530.72 MiB** | **完全相同** |
| reserve 當下 rss | 7623.2 MiB | 7801.0 MiB | **+177.8 MiB** |
| `CGC-PHASE-SPLIT width` | 8 | 8 | 同 |
| `CGC-GATHER-SLAB` 行數 | 3 | 3 | 同 |
| 走到第幾層 | il=0..39（**40 層全過**） | 死在 **il=39（＝最後一層）** | — |
| max rss | 8347.9 MiB | 8388.5 MiB（abort 時） | +40.6 |
| `CGC-METAL-FAIL` | **0** | **1** | — |
| 第二個 ctx | 有（ctx#2 = 628.70 MiB，TG cell） | **沒有**（死在第一個 test） | — |

OFF 讀數：`pp 276.70 ± 11.55`、`tg 11.89 ± 0.25`（`base_check: PASS`），
但 `attribution.verdict = "swap"`（`swap_growth +3003 MiB`、`max_swap 8143.81`）⇒ **不可當乾淨基線引用**。

### 9.1 三個由此確定的結論

1. **target ctx 的 graph 預留一模一樣（2530.72 MiB）** ⇒ MTP 沒有讓 target ctx 多留任何 graph buffer。
   ⇒ 「草稿 ctx 形狀把 n_ubatch=5632 放大成巨大 buffer」這條軸**在 bench 上不成立**
   （更何況 draft ctx 壓根沒建立，見 §8.3）。§3 那兩個 `CGC_DRAFT_*` 閘 = **題外槓桿**。
2. **多出來的成本在「模型載入時」就付了**：只載一次模型，`expert_index` 卻從 30720 → 31488
   （**+768**），同一 reserve 點的 rss **+177.8 MiB**。⇒ MTP block 的張量在 load 階段就進了
   model／expert-cache index（expert matches），而這發生在 draft ctx 之前
   ⇒ 解釋了「為什麼形狀閘救不了」（它們作用在後面）以及「為什麼 IL=39 才炸」。
3. **盒況不是藉口**：ON 那趟**起跑時 swap 只有 815 MiB**（乾淨）卻死；
   OFF 這趟**起跑 swap 4835 MiB、worst 8143**（髒）卻活。方向相反 ⇒ 死因在 MTP 本身，不在機箱。

### 9.2 由此改寫的機制假說（可證偽）

> **PP 的工作集沿著 40 層逐層累積，在第 39 層（最後一層）越過 `recommendedMaxWorkingSetSize`；
> MTP-enabled 時因為 model 多帶了 768 個 expert index 條目／+177.8 MiB RSS，把餘額墊到 <0。**

可證偽的兩個推論：
- (a) 若把 prompt 縮小（`-p 512`）——只影響 cell、不改任何 MTP 形狀——ON 臂就該活；
   ⇒ 證它是「逐層累積的工作集」，不是某個固定配置。（⚠ 這趟**不可引用為成績**，只回答存活。）
- (b) 若能讓 MTP block 不進 expert index / 不載入 Metal buffer，(+)177.8 MiB 消失 ⇒ 應可過。

### 9.3 下一步（待裁定，我沒跑）

1. **(a) 便宜的機制驗證**：`-p 512` 的 ON 臂跑一次，看是否存活（不要引用 t/s）。
2. **查那 768 從哪來**：`llama-model.cpp` 建 `expert_index`（或 `CGC Soft Pool init` 那條）
   在 model load 時如何處理 MTP block 的 experts —— 這是唯一真正能砍掉那 +177.8 MiB 的地方。
3. promotion gate 的 OFF 半趟現在有了，但 **ON 半趟仍沒有 ≥2 reps 的存活樣本**
   ⇒ 「MTP on 的增益」**依舊不可判定**（連 `-6` 都還沒跨過）。

### 9.4 artifact

- JSON：`/tmp/mtp_off_pair_control.json`（log：`/tmp/mtp_off_pair_ctrl.log`）
- stderr：`/tmp/harness_bench/llama_bench_prod-new_p2048_n128_d512_r3.stderr.log`（03:52，同 build）


## 10. ★ B 軸結案：`+177.8 MiB` 的完整因果鏈（2026-09-28 05:0x，靜態分析＋GGUF 普查）

> 證據分級：【碼證】工作區程式碼可驗證；【檔證】模型檔／ GC 普查可重現；【推算】公式推導，非量測。

### 10.1【檔證】先看數字怎麼閉合

工具：`scripts/check/gguf_tensor_census.py`（自寫，不依賴 numpy；用 `LLAMA_EXPERT_CACHE` 同名規則
`_exps` + `blk.` 去掃）。重現：

```
python3 scripts/check/gguf_tensor_census.py models/gguf/Nail-Qwen3.6-35B-A3B-MTP-UD-IQ3_XXS-denseIQ4X.gguf
```

| 項目 | 值 |
|---|---|
| `general.architecture` | `qwen35moe` |
| `qwen35moe.block_count` | **41** |
| `qwen35moe.expert_count` | **256** |
| `qwen35moe.nextn_predict_layers` | **1** |
| 符合 loader pattern 的 expert tensor | **123 = 41 層 × 3 kind**（down / gate / up） |
| 所有 expert tensor 的 `ne[2]` | **256**（單一值，無例外） |
| ⇒ 檔案全量可被 index 的條目 | **41 × 3 × 256 = 31488** |

**31488 = ON 實測值（一分不差）；30720 = ON − 768 = 40 × 3 × 256。**
⇒ OFF 少的，**剛好是整整一個 block 的量**。

### 10.2【碼證】因果鏈：誰決定那一塊在不在

1. `llama-bench.cpp:1584`
   `mparams.load_mtp = std::find(spec_types…, COMMON_SPECULATIVE_TYPE_DRAFT_MTP) != spec_types.end();`
   ⇠ 唯一開關，純由 CLI `--spec-type draft-mtp` 推導（註解 `:1576-1580` 自己指到 `models/qwen35moe.cpp:45`）。
   （server 路徑同構：`common.cpp:1635`。）
2. `llama.cpp:312` → `llama-model-loader.cpp:829` → `this->load_mtp`
3. **`models/qwen35moe.cpp:45`** `int mtp_flags = !ml.load_mtp ? TENSOR_SKIP : 0;`
4. `models/qwen35moe.cpp:112-136` `load_block_mtp()` 用 `mtp_flags` 建 **blk.40**，
   含 `ffn_down_exps` / `create_tensor_gate_up_exps`（`ffn_gate_exps` + `ffn_up_exps`）。
5. `llama-model-loader.cpp:1262-1267`：`(flags & TENSOR_SKIP)` ⇒ **不建立張量**（`size_data -= nbytes`，回 nullptr）。
6. `llama-model-loader.cpp:1847-1878`：index 只對「存在於 ctx」的張量計數 ⇒ OFF 少 768 條。

### ★★ 10.3 因果方向與原本的假說相反

不是「MTP 在載入階段多塞了東西進 index」——而是：

> **OFF 那一趟本來就把 blk.40 整塊 `TENSOR_SKIP` 掉了；`--spec-type draft-mtp` 只是把它加回來。**

這同時把 §9.1(2) 講的「成本高發生在 draft ctx 之前」講得更死：它發生在** model load**，
比 draft context 的建立還早兩個階段，所以任何作用在 draft ctx 上的開關
（`CGC_DRAFT_CTX_ALIGN` / `CGC_DRAFT_SMALL_BATCH`）必然救不了 —— 與 §8.3 實跑一致。

⛔ 附帶更正：本趟 OFF log 裡 `'unused tensor'` = 0 行**不能拿來反駁 TENSOR_SKIP**，
因為 `llama-bench.cpp:3251` 有 `llama_log_set(llama_null_log_callback, NULL)` —— logger 全關，
那條 `LLAMA_LOG_WARN` 根本不會印。

### 10.4 index 本身不花錢（更正 §9.1.2）

`llama_expert_index_entry`（`llama.h:308`）約 40 B × 768 條 ≈ **30 KB**。
**index 是指標，不是成本**；真正花錢的是 entry 指到的那些 weights。

### 10.5 那 177.8 MiB 是哪些 bytes

`models/gguf/…mtphead.json`（schema `cgc.mtp_head_identity/1`）：`head_layer=40`、
`head_bytes=700.80 MiB`、21 個張量。

| | MiB |
|---|---:|
| `output.weight`（載不載 MTP 都載 ⇒ **不計入差值**） | 397.85 |
| **blk.40 其餘 20 個張量** | **302.95** |
| ├ `ffn_down_exps` Q3_K（256 experts） | 110.00 |
| ├ `ffn_gate_exps` Q2_K | 84.00 |
| ├ `ffn_up_exps` Q2_K | 84.00 |
| └ 其他（attn / eh_proj / shexp / norms） | 24.95 |

本趟 cell **沒有設** `LLAMA_EXPERT_CACHE_LAYER_CAPS`（`extra_env={}`）⇒ uniform cap = **143**
（log：`CGC Soft Pool init: L0=0 L1=0 (n_slots=143, partition disabled)`）。

【推算】若 residency 隨 per-layer cap 線性縮放：
`278.00 × 143/256 + 24.95 = ` **180.27 MiB** vs 實測 **177.8 MiB** ⇒ 差 **1.4%**。
（與 `ACCEPT_LEVERS` §11 census 那個「302.95 MiB model buffer」是同一批 bytes，只是那邊假設全駐留。）

### 10.6 ⚠ 能砍，但那一刀**已經被研究過，而且刻意付岀**了

唯一存在的可調旋钮＝ **per-layer residency cap**
（`llama-model-loader.cpp:1496` + `llama-expert-cache.cpp:3939`，讀 `LLAMA_EXPERT_CACHE_LAYER_CAPS`，
語法 `start-end:cap;…`）。而 **`debug-layer-caps-ab.md` 說的正是 layer 40**：

- H1 **Confirmed**：生產預設 `40-40:256` 明顯優於關掉（warmed 20.65 vs 15.60 t/s）
- H3 **Confirmed**：已吃掉幾乎全部好處，**往回退只會侵蝕 draft-layer 優勢**
- 原始 ABBA：misses **11574 → 2622**、+0.8 t/s

⇒ **layer 40 就是這個 MTP block**，`40-40:256` 就是「讓 blk.40 的 experts 全駐留」。
也就是說：**那 177.8 MiB（production 設定下是 302.95 MiB）不是浪費，是刻意付岀的購買成本。**

### 10.7 所以「唯一能砍的地方」要這樣改寫

真正**不用拿命中率換**的那一刀不是降 cap，而是：

> **target prefill 期間，blk.40 的 weights 是完全用不到的**（target ctx 只建 il=0..39，§9 已證
> target ctx reserve OFF/ON 完全相同 = 2530.72 MiB）。它現在在 PP 的尖峯時刻白白佔著 residency。

也就是說真正該炸的不是 entry 數，而是「**模型載入時就直接配置，沒有延後到 draft 才用**」。
那一刀是 code change（延後配置 / buft override），**不是現有 env knob**，本輪沒做、也不在本線範圍（src 共享）。

現有手段對照：

| 手段 | 砍多少 | 代價 | 現況 |
|---|---|---|---|
| `LAYER_CAPS` `40-40:N`（N < 143） | `278×(143−N)/256` MiB（N=64 ⇒ −85.8；N=32 ⇒ −120.6） | **掉 draft 層命中**（既有研究：往回退就掉） | 現有 knob，可按 cell 試（但也就自己砍自己） |
| blk.40 experts 改放 CPU buft | ≈ 278 MiB（target PP 期間） | 未驗證；pool 有可能拒絕收養（loader 註解假設 Metal buft 當 zero-copy pool region） | llama-bench 有 `tensor_buft_overrides` 欄位但未接 CLI |
| 降 `-p`（原 option A） | 不砍這個，但降 PP 峰值 | cell 不再可比 | 已提，未跑 |
| 什麼都不動 | — | MTP 在權威 cell 仍 `rc=-6` | 現狀 |

### 10.8 狀態與限度

- **B 軸的答案是「找到一份刻意付岀的帳單」，不是「找到一個能免費清掉的 waste」。**
- MTP ON 在權威 cell **仍無存活樣本** ⇒ 「MTP on 的增益」**依舊不可判定**（哪一條都沒跨過 `-6`）。
- 【推算】§10.5 的線性縮放只有一個資料點（cap=143、177.8 MiB）撐著，且 `CGC-GATHER-SLAB` 印的是
  `cap=256 / size=110.00 MiB`（與 per-layer cap 是不同口徑）⇒ **residency 是否真的隨 cap 線性，還沒量過**。
  要證它的話，下一步是跑兩個不同 `40-40:N` 的 OFF/ON 對照，看 RSS 差是否走 `278×N/256 + 24.95`。
- 【未跑】本節沒有執行任何量測、沒有 build、`src/` 一行未改。

### 10.9 artifact

- `scripts/check/gguf_tensor_census.py`（本輪新增，可重現）+ 輸出 `/tmp/cgc_gguf_census.out`
- `models/gguf/Nail-Qwen3.6-35B-A3B-MTP-UD-IQ3_XXS-denseIQ4X.mtphead.json`
- stderr / log 同 §8.7、§9.4
