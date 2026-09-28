# MTP ON 輸出分岔 —— 逐鍵 ablation 結案（2026-09-28 12:5x–13:0x）

## §0 結論（一句話）

**8 個 env 鍵全部排除。改變輸出的是「MTP 開著」這件事本身，不是它順帶帶進來的那包 env。**
前一輪只能說「開 MTP 那一包會改變輸出」—— 現在可以說「**開 MTP 本身改變輸出**」。

## §1 判據：為什麼不能用 gate 印的 M1/M2

gate 印的 `M1 1/14 / M2 2/14` **不可讀**，而且不是「數值不好」，是**鍵在塌縮**：

| 臂 | DEF 行數 | 唯一 `pmax` 數 |
|---|---:|---:|
| OFF | 50 | 49 |
| ON | 101 | **27** |

ON 有 101 行 DEF 但只有 27 個唯一 `pmax`（verify batch 一次解 4 個 token，多行共用同一 `pmax`），
用 `(step, token_idx, ctx_type)` 或單用 `pmax` 對齊都會把不相關內容配在一起（gate 自己印 coverage 8%）。

⇒ 本輪用兩個判據：
- **(A) `probe_answer`**（greedy、48 token、可複現：ON 連續兩次逐字相同）—— 看輸出是否相同。
- **(B) 定位用絕對位置 `pmax + token_idx` 對齊後的 `argmax_logit`** —— 看數值從哪一步開始漂。

⚠ 這也是**方法論結論**：gate 的 M1/M2 **只在兩側 dump 形狀相同時可讀**。E5（OFF+caps vs OFF）
就拿到 `common=50 / only_A=0 / only_B=0 / M1 PASS 50/50 / M2 PASS 50/50` —— **gate 是好的，
之前那個 1/14 純粹是形狀不匹配的假象，不要再去讀它。**

## §2 真正的差異只有 8 個 env（不是 30 個）

`config_diffs` 印 30 項，其中 22 項是 argv 位移的假差異（插入 `--spec-type draft-mtp
--spec-draft-n-max 3` 後整體後移），採樣參數與 `-b/-ub` 兩側相同。從 `.cap` 的 `resolved.ENV`
做全差分，真實差異就是這 8 個：

| env | OFF | ON | src 讀點 |
|---|---|---|---|
| `CGC_NO_PREFETCH` | absent | 1 | `llama-context.cpp:2154` |
| `CGC_VERIFY_DECODE` | absent | 1 | `llama-context.cpp:7174` |
| `CGC_DRAFT_DECODE` | absent | 1 | `llama-context.cpp:7177` |
| `CGC_WARM_NPAST` | absent | 0 | `llama-context.cpp:7190` |
| `CGC_MTP_NO_WARMUP` | absent | 1 | `server-context.cpp:1305` |
| `CGC_NO_SEQ_RM_PROBE` | absent | 1 | `server-context.cpp:1103` |
| `CGC_PREFIX_REUSE_CKPT` | absent | 1 | `server-context.cpp:1416` |
| `LLAMA_EXPERT_CACHE_LAYER_CAPS` | absent | `40-40:256` | loader / expert-cache |

⚠ **`CGC_MM_BITIDENT` 兩側都是 1 ⇒ 不是混淆項**（別再當成差異）。
⚠ 兩側 `MODEL` 印的是不同檔名，但 `Qwen3.6-35B-A3B-UD-IQ3_XXS.gguf` 是 **symlink →**
`Nail-Qwen3.6-35B-A3B-MTP-UD-IQ3_XXS-denseIQ4X.gguf` ⇒ **同一個物理檔，不是模型混淆**（已查證）。

## §3 六趟 ablation：全部排除

| 實驗 | 改動 | `probe_answer` | `argmax_logit @ pos=209` |
|---|---|---|---|
| OFF 基線 | — | 台北101…**擁有世界最高可運作觀景台**… | **19.22745** |
| E5 | OFF + `LAYER_CAPS=40-40:256` | **= OFF（M1/M2 50/50 PASS）** | 19.22745 |
| ON 基線 | — | 台北101…**兼具現代科技與傳統文化意象**… | **19.25835** |
| E1 | ON + `PREFIX_REUSE_CKPT=0` | = ON | 19.25835 |
| E2 | ON + `VERIFY_DECODE=0` `DRAFT_DECODE=0` `WARM_NPAST=2048` `NO_PREFETCH=0` | = ON | 19.25835 |
| E3 | ON + `NO_SEQ_RM_PROBE=0` | = ON | 19.25835 |
| E4 | ON + `MTP_NO_WARMUP=0` | = ON | 19.25835 |
| E6 | ON + `LAYER_CAPS=40-40:16` | = ON | 19.25835 |

**8 個鍵全數排除**（`LAYER_CAPS` 用雙向驗：ON 側縮到 16、OFF 側加到 256，都不改變結論）。

⛔ 其中 **`CGC_PREFIX_REUSE_CKPT` 的記載與觀測不符**：`run_server.sh:306-315` 明寫它
「MTP-on 下非 bit-neutral（移動 204 行 verify-batch 中的 7 行）」，但關掉它答案與 logit
**完全不變** ⇒ 它不是本次分岔的成因（至多是必要非充分）。

## §4 分歧長什麼樣（這是本次最有價值的新數字）

對齊後的三個位置：

| 位置 | OFF | 所有 ON 變體（7 個） |
|---|---|---|
| pos=1（prefill 尾） | 27.57889 | **27.57889（全臂 bit-identical）** |
| pos=209（**第一個 decode**） | 19.22745 | **19.25835**（差 0.0309，約 **0.16%**） |
| pos=213 起 | 4960 | 16（token 選擇也分歧） |

⇒ **prefill 是精確的**（KV 完全相同），**分歧從第一個 decode 就出現**，而且
**7 個 ON 變體給出逐位相同的 19.25835** ⇒ 單一、確定性的結構性成因，不是抽樣也不是 env。

### 旁證：不是「pool 污染」

| 臂 | hits | misses |
|---|---:|---:|
| OFF | 20460 | 2192 |
| ON | **7067** | 2432 |
| E2（fast=0） | **23284** | 2362 |

E2 把 hits 拉回 OFF 量級（23284 vs 20460），**答案與 logit 卻仍是 ON 那一套**
⇒ 白皮書 root cause A（pool 成員不同 → router near-tie 翻轉）**不足以解釋**。
也排除了 ZERO-slot：E2 已讓 `zero_slot_enabled()` 為 false（expert-cache:312）。

## §5 剩下沒驗的（都不再是 env）

8 個鍵全數排除後，剩下的結構差異只有：**draft ctx 存在**、`blk.40`（MTP block）載入並進池、
target 走 verify batch。⇒ 成因落在 **MTP 機制本身**。

要再往下挖需要動 `src/`（例如把 draft ctx 的 expert pool 與 target 隔離），或用
**cache-off oracle**（`CGC_SERVER_EXPERT_CACHE_OFF=1`）對照 —— 但 16 GB 上 cache-off
風險高（本線既有結論：16GB 唯一實測存活的組合是 `--load-mode none` + pool 8 GiB）。**本輪未做。**

## §6 對「修復」的建議

- ✅ **交付口徑維持 MTP off 就是正確的**：`prod-new` 的預設即 `CGC_SERVER_MTP=0`
  （`run_server.sh:333`）。**不需要改任何東西**。
- ⛔ **MTP on 不能當「純加速開關」**：它不只是慢（同 build OFF 11.92 → ON 7.34），
  而且**做的不是同一份工作**（第一個 decode 起 logits 就不同）。
  ⇒ 「ON 的 t/s 拿來跟 OFF 的 t/s 比」這件事**不成立**，兩個數字不在同一個座標系。
- 若要真的讓 MTP on bit-identical，屬 `src/` 改動（護欄 M1/M2/M3 是**護欄不是獎勵**，
  不該為速度換掉）⇒ 需要另外約窗口、立項。

## §7 過程中被卡住（已解）

13:0x 前後 8080 一度被**另一條線**的常駐 server 佔住（PID 86250，帶
`CGC_SLOT_TABLE_GPU/MISS_MASK/S1_DBG`，`nohup &` detach）。**沒有 kill**，
也**沒有換 port 硬跑**（對方已佔 ~13 GB，16 GB 上再起第二支會把兩邊一起拖進 swap）。
等了約 2 分鐘窗口自己開了，E3–E6 才接著跑完。
