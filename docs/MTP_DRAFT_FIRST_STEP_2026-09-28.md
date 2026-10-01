# MTP draft 第一步為什麼在跑了若干回合之後才開始失敗（2026-09-28）

> 緣起：`docs/ARM_DRAFT_LIVENESS_2026-09-28.md` 把病灶定位在「draft 鏈在第一個 measured rep
> 之後整個死掉」，但那個 `-1` **沒有帶原因**。這一輪問的是原因，以及「要嘛不失敗、要嘛在第
> 一回合就大聲失敗」。答案是**兩個獨立的缺陷**疊在一起。

## 0. 結論（兩句話）

1. **引擎一直在說原因，是 llama-bench 把它的訊息丟掉了。** `llama-bench` 在非 verbose 時安裝
   **null log sink** ⇒ 連 `LLAMA_LOG_ERROR` 都丟。引擎對那個 `-1` 的完整解釋（含 `X`、`Y` 兩個
   數字）每一回合都印了，**268 次沒人看見**。
2. **會失敗是因為 draft 的 KV 有一條尾巴，而這條尾巴沒有任何人在清。** 新 generation 從 `N` 開
   始，但記憶體裡還留著**上一個 generation（跨 rep）**的位置 `>= N`；共用 KV 時 `draft()` 內那
   道 `!is_mem_shared` 守衛讓每回合的 `seq_rm` **完全不執行** ⇒ 該 generation 的**每一回合**都
   被 M-RoPE 拒絕 ⇒ 整個 rep 量到的是純解碼，而它仍被當成一支 k 臂。

## 1. 缺陷一：訊息被丟掉（這是「靜默」的來源）

```
tools/llama-bench/llama-bench.cpp
  if (!params.verbose && getenv("CGC_KEEP_ERRORS") == nullptr) {
      llama_log_set(llama_null_log_callback, NULL);   // ← 連 ERROR 一起丟
  }
```

逃生口**早就存在**（`CGC_KEEP_ERRORS=1`，註解自己寫著「keep ERROR-level logs without paying for
full --verbose DEBUG output」），但 bench 路徑沒有人開它。`Backup/rerun/keff_shape_ab.sh`
甚至為此存在（「is REQUIRED and is the reason this driver exists」）。

**代價**：引擎對那個 `-1` 的原文是（`Backup/cgc_logs/` 的 server log 一直看得見，因為 server
沒裝 null sink）：

```
E init: the tokens of sequence 0 in the input batch have inconsistent sequence positions:
 - the last position stored in the memory module of the context (i.e. the KV cache) for
   sequence 0 is X = 207
 - the tokens for sequence 0 in the input batch have a starting position of Y = 207
 for M-RoPE, it is required that the position satisfies: X < Y
E decode: failed to initialize batch
E llama_decode: failed to decode, ret = -1
E spec        draft: llama_decode[1] returned -1
```

**修**：預設 sink 改成**轉發 ERROR、丟掉 INFO/DEBUG**（`llama_error_only_log_callback`）。
`CGC_KEEP_ERRORS` 的語意不變（= 完全不裝 sink，INFO 以上）。理由：一個說不出理由的失敗，就是
一個穿著判詞衣服的數字；而 `draft_liveness.py` 只能分類 log **裡有的**東西，所以 log 必須有。

## 2. 缺陷二：尾巴沒人清（這是「為什麼會失敗」）

新 generation 的第一個 draft step 要把 `id_last` 解在位置 `Y = n_past`。若記憶體對該 seq 的
`pos_max = X >= Y`，`llama-batch` 就拒收整批（M-RoPE 要求 `X < Y`）。

**實測數字**（`delivery` cell、k=3、`reps 3`、一次 llama-bench 啟動、`mem_shared=1`）：

| 情境 | X | Y | 後果 |
|---|---|---|---|
| rep1 生成中 | 559 / 573 / 597 | 同值（`X == Y`） | 該回合 0 drafts（約 12% 的回合） |
| **rep2 起點** | **642**（上一個 rep 的尾巴） | **512**（本 rep 起點，`-d 512` 之後） | **整個 rep 每一回合都 0 drafts** |

第二列的原文（修前那份 log，`X = 642` vs `Y = 512` 的五行）：

```
E init: the tokens of sequence 0 in the input batch have inconsistent sequence positions:
 - the last position stored in the memory module of the context (i.e. the KV cache) for
   sequence 0 is X = 642
 - the tokens for sequence 0 in the input batch have a starting position of Y = 512
 for M-RoPE, it is required that the position satisfies: X < Y
E decode: failed to initialize batch / E llama_decode: failed to decode, ret = -1
spec        draft: llama_decode[0] returned -1 on the FIRST draft step => THIS GENERATION
                     YIELDS 0 DRAFTS/ROUND ... n_past=512, drafting=1/1, mem_shared=1
```

第二列就是「跑了若干回合之後才開始失敗」的答案：不是回合數，是**跨 generation 的邊界**。
`llama-bench` 在 rep 迴圈裡清的是**目標** context 的記憶體（`llama_memory_clear(llama_get_memory(ctx), false)`），
沒有任何東西清 draft 的；而在 `is_mem_shared` 的情況，draft 的記憶體**就是**目標的記憶體，
`draft()` 內那道 `if (!is_mem_shared)` 守衛又讓每回合的 `seq_rm` 被跳過 ⇒ **這條尾巴沒有任何
人清**。

**修**：在 MTP 的 `begin()`（唯一的「新 generation」hook）裡，當 `pos_max >= N` 就
`llama_memory_seq_rm(mem, seq_id, N, -1)` —— 只丟尾巴（`llama_memory_seq_rm` 只移除位置落在
`[p0, p1)` 的 cell，`p1 < 0` 表到尾），`[0, N)` 這個前綴保留；清完仍 `>= N` 就大聲報錯（實測
從未觸發）。

### 2.1 為什麼 `N` 可以被信任（這一改動最可能變成 regression 的地方）

`begin()` 的唯一語意是「新 generation 從 `N` 開始」，所以 `[N, -1)` 的值取決於 `N` 是不是**記憶體
真正持有的長度**。兩個呼叫點都查了：

- `tools/llama-bench/llama-bench.cpp:2963`：`prompt_probe.assign(n_past, id_last)` —— 刻意的，檔內註解
  寫著「so the size handed to begin() can be honest」。
- `tools/server/server-context.cpp:4154`：`common_speculative_begin(spec.get(), slot.id, slot.prompt.tokens.get_text_tokens())`
  —— 而 `:3648` 的 `slot.prompt.tokens.keep_first(n_past)`（`n_past` 含 cache-reuse 命中，`:3645`
  記為 `slot.n_prompt_tokens_cache`）已經把它正規化成「已評估的長度」。

⇒ 兩邊都符合 `N == n_past`。也就是說 `pos_max >= N` **只會在記憶體持有超出前綴的東西時**成立
（draft 尾巴），不會誤砍 prefix-cache 的合法前綴。這是這一改動的**保全條件**；若未來有第三個
呼叫點傳進「prompt 全文」，這個推論就不成立，必須重驗。

## 3. 「要嘛不失敗、要嘛第一回合就大聲失敗」——兩個都給了

- **不失敗**：上面的尾巴清除，讓跨 generation 那一類**不再發生**（配對驗證見 §4）。
- **第一回合就大聲**：`draft()` 內 `i == 0` 的失敗現在**第一次就指名狀態**（`batch pos (Y)`、
  `ctx_dft pos_max (X)`、`n_past`、`drafting`、`mem_shared`、`ctx_dft`），並明說「這一個
  generation 每回合 0 drafts、這支臂量到的是純解碼」，之後 1/64 節流。舊的那行只重複
  `llama_decode[0] returned -1` **264 次**——那是症狀，不是大聲。

## 4. 配對驗證（同一命令、同一 cell、同一 binary，只差這個修）

```
                        修前（PREFIX_* log）          修後（POSTFIX_* log）
rep1 warm_skip          drafted=82  mean_len=2.02    drafted=95  mean_len=1.8444
rep1 timed              drafted=90  mean_len=2.02    drafted=82  mean_len=2.0526
rep2 warm_skip          drafted=0   mean_len=1.00    drafted=83  mean_len=2.0789   ← 活
rep2 timed              drafted=0   mean_len=1.00    drafted=83  mean_len=2.2941   ← 活
rep3 warm_skip          drafted=0   mean_len=1.00    drafted=76  mean_len=2.0811   ← 活
rep3 timed              drafted=0   mean_len=1.00    drafted=73  mean_len=2.5862   ← 活

M-RoPE 拒絕事件          268                          28        （−90%）
邊界尾巴清除（新 WRN）    —                            3
`pos_max STILL >= N`      —                            0
draft_liveness rc         3（DEAD: rep 2,3）           0（every measured rep ran the draft chain）
```

兩份 log 收在 `Backup/arm_draft_first_step_2026-09-28/`（`PREFIX_*` / `POSTFIX_*`）。

```bash
python3 scripts/check/draft_liveness.py --log-dir <dir> --expect-timed 3   # 3 = 死, 0 = 活
```

**這兩個 t/s 不是量測結論**：都是單臂、`attribution.verdict=swap`（修前 swap +4725 MiB、修後
+677 MiB），協定禁止跨 run 比較。這一輪的結果是**「3 個 rep 裡有幾個真的在 draft」從 1 變 3**，
不是「變快多少」。

## 5. 沒修的、不知道的

- **生成中的 `X == Y`（28 事件，~12%）仍在。** 它是 `docs/KEFF_CAP_2026-09-26.md` §4 已經披露
  的那一類（共用 KV 時 draft 的第一步重錨在目標已經寫過的位置）。它**不毀臂**（只損失個別回合），
  而且現在**可見、可數**。修它要動共用 KV 的重錨語意，不在這一輪的範圍。
- **為什麼 `begin()` 之外還有 `X == Y`**：位置是 `dp.n_past + i + 1`／`dp.n_past`，而共用 KV 下
  目標的 `process()` catch-up 也寫同一個位置。誰先寫、該不該跳過第一個 decode，需要另一次量測。
- **`llama-cli`/server 的 smoke 沒做成。** 一次 `llama-cli --spec-type draft-mtp` 在**模型載入**就
  abort（`common_init_from_params` → `llama_context::synchronize()` → `ggml_metal_synchronize` →
  `ggml_abort`），那是這台盒子在記憶體壓力下已記錄過的 Metal OOM 那一類，**發生在任何生成之前**
  ⇒ 既不是 regression 的證據，也不是健康的證據。同一份 `speculative.cpp` 由 `delivery` cell 的
  兩支臂實際跑過（六個 phase 全活），但 **server 路徑本輪未 smoke**。
- **16.4% 仍未認證**；本輪沒有產生任何可引用的 t/s 讀數。
- `Backup/rerun/keff_shape_ab.sh` 的註解仍寫著 `CGC_KEEP_ERRORS=1` 是 REQUIRED —— 那是存檔，
  留著；現在的預設已經會轉發 ERROR（`scripts/check/spec_cost_curve.py` 的註解已同步更正）。

## 6. 改了哪些檔

| 檔 | |
|---|---|
| `src/llama.cpp/tools/llama-bench/llama-bench.cpp` | null sink → **error-only sink**（預設不再吞 ERROR）；`CGC_KEEP_ERRORS` 語意不變 |
| `src/llama.cpp/common/speculative.cpp` | MTP `begin()`：**清 `[N, -1)` 尾巴**＋大聲報告；`draft()`：`i == 0` 失敗的第一回合指名狀態＋節流 |
| `scripts/check/spec_cost_curve.py` | 更正一條已過期的註解（它把「ERROR 被吞」當成現況） |
| `Backup/arm_draft_first_step_2026-09-28/` | 配對的兩份 log（**`Backup/` 被 `.gitignore:396` 忽略** ⇒ 這是本機證據；決定性的原文行已逐字寫在 §2） |
| `docs/ARM_DRAFT_LIVENESS_2026-09-28.md` | 補 §8：把這一輪的答案接回那條線 |
| `src/llama.cpp/build/bin/*.dylib` | 重建產物（本 repo 要求原始碼與產物一起 commit） |

原始碼改動**不動任何既有行為路徑**：`begin()` 只在 `pos_max >= N`（以前是靜默失敗的狀態）時
動作，draft 失敗訊息只在失敗時印。
