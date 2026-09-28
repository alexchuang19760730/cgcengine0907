# 買 accept 的兩根旋鈕：一根不存在，一根沒人傳 —— 以及「髒盒子能不能判 on/off」

> ⚠ **輸出函數標籤（2026-09-28 依 `MTP_CALIBER_REDEFINE_CHARTER_2026-09-28.md` B2 重標）**：
> 本檔引用的 **MTP-on** 數字屬**另一個輸出函數**，**不可與 MTP-off 互比**
> （含任何「MTP 加速 X%」「MTP-on ≥ MTP-off」這類表述）。
> 根因：MTP on ⇒ `cparams.n_rs_seq > 0` ⇒ `delta-net-base.cpp:494` recurrent 走 K 槽位回滾，
> **計算份數本身不同**（`gdn` 30→60、`conv_input` 30→150）⇒ 見
> `docs/MTP_BITIDENT_P1_ROOTCAUSE_2026-09-28.md`。本檔既有數字**未改動**（dated 產物不回改）。

**Date** 2026-09-26 13:4x–14:3x · carrier `Nail-Qwen3.6-35B-A3B-MTP-UD-IQ3_XXS-denseIQ4X` · binary `dc397fb2f`
（`libllama.0.dylib 1dfd4394f41c58cf`）· cell `-p 2048 -n 128 -d 512 -r 3 --ctx-size 0 --warm-skip 64`。

## 0. 一句話

「MTP on 轉正」卡在 accept 那一側，而我們以為手上的兩根 accept 旋鈕，**一根在引擎裡根本不存在
（`CGC_SERVER_MTP_P_MIN`），另一根存在但沒有任何生產路徑傳過它（`--draft-p-min`）**；唯一真的被
試過的是 sampler parity，而它**輸了**（3 個配對 rep 全負）。本輪同時起跑一組**同窗交錯**的
on/off 四臂，用來回答「這個盒子現在到底能不能判 on/off」這個更前置的問題。

## 1. 審計（純程式碼閱讀，附可複製的證據命令）

| # | 命題 | 證據 | 狀態 |
|---|---|---|---|
| A1 | `CGC_SERVER_MTP_P_MIN` **不存在** | `grep -rn "MTP_P_MIN" src/llama.cpp/ --include=*.cpp --include=*.h` → **0 命中**；`run_server.sh` 也 0 | 已確認（程式碼） |
| A2 | 正規旗標是 `--draft-p-min` | `common/arg.cpp:4041` `.set_env("LLAMA_ARG_SPEC_DRAFT_P_MIN")` | 已確認 |
| A3 | 但**沒有任何生產路徑傳它** | `grep -c -- "--draft-p-min" scripts/run_server.sh` → **0**；`llama-bench.cpp` 內 `grep -n p_min` → **0**（bench 載體連解析都沒有） | 已確認 |
| A4 | M4 的 treatment 因此是 **parity-only** | `Backup/phase_decomp/m4_parity_ab_*.json` 的 `extra_env` 兩顆都在，但 P_MIN 那顆到不了引擎 ⇒ 那一臂實際只有 parity | 已確認 |
| A5 | 文件把不存在的旋鈕寫成存在 | `docs/M4_DRAFT_DISTRIBUTION_2026-09-20.md:12-13`、`docs/M4_PARITY_AB_2026-09-21.md:12-14`、`docs/REMAINING_PATHS_2026-09-21.md` 都寫 `CGC_SERVER_MTP_P_MIN`／`--spec-draft-p-min` | 待更正（見 §5） |
| A6 | `CGC_MTP_SAMPLER_PARITY` 的前提**成立** | `common/common.h:260`：`samplers` 有非空預設鏈（PENALTIES/DRY/TOP_N_SIGMA/…），所以 `!params.sampling.samplers.empty()` 為真 | **自我更正**：本輪我先推論「parity 從未咬到」，被這行推翻 |

### 1b. 我收回的那個推論（留痕，因為它差點被寫成結論）

我一度從 `common/speculative.cpp:1361` 的前提（`!params.sampling.samplers.empty()`）推論：既然
`run_server.sh` 從不傳 `--samplers`、`llama-bench` 只用手設 `temp/top_p/top_k`，那麼 parity 會在兩條
載體上都落到 legacy `{TOP_K=10}` 分支 ⇒ 「M4 的否證是無效的」。查 `common.h:260` 後**前提為真**：
`samplers` 的**預設值本身就是一條非空鏈**，`empty()` 只是「有人明確關掉鏈」的判準。
⇒ M4 的 treatment 是**真的 parity**，它的否證**有效**。這一行是本篇最重要的更正。

## 2. M4 的判決（有效，且與後來的模型一致）

| rep | off (mean_len / accept / t/s) | on (mean_len / accept / t/s) | Δ t/s |
|---:|---|---|---:|
| 0 | 2.59 / 51.49% / 13.10 | 2.72 / 57.94% / 12.19 | **−0.91** |
| 1 | 2.67 / 52.66% / 12.82 | 2.65 / 54.45% / 11.08 | **−1.74** |
| 2 | 2.83 / 60.61% / 11.91 | 2.61 / 51.43% / 9.18 | **−2.73** |

三個 rep 全負 ⇒ 依當時寫死的判準是 **REFUTED**。讀法：accept 確實被抬起來了（rep0：+6.5 點），
但 t/s 反而掉——與 G6 的實測（**+11% mean_len 卻 +14% step ⇒ 淨 −0.45 t/s**）同一個機制：
**在 k=3 下，多買到的 token 付不起它帶來的 step 成長。**

## 3. 這輪的四臂（進行中）：把「髒盒子能不能判」也量進去

盒況：起跑 swap **6959 MB**（今天最乾淨的一次是 0）、thermal 起跑 HEAVY（等 150 s 降到 NOMINAL 才放行）。
**我們不需要乾淨的盒子，但需要同窗交錯**：`off → on → on → off`，兩顆 off 臂量的是**漂移**，
兩顆 on 臂量的是漂移＋設定。

### 跑前寫死的判準（不接受事後改）

| # | 判準 | 理由 |
|---|---|---|
| P1 | 兩顆 on 臂的產物必須 `spec_type == "draft-mtp"`，且 stderr 出現 `CGC-BENCH-ACCEPT` | 「武裝」是可否證的事實，不是標籤（`docs/MTP_UNARMED_VOID_2026-09-26.md` 的四條見證） |
| P2 | **漂移尺度** = 兩顆 off 臂 tg 的差距；**效果** = on 中位 − off 中位 | 髒盒子裡唯一誠實的解析度單位 |
| P3 | 判決只有三種：`on 較快`（效果 > 漂移尺度）、`on 較慢`（−效果 > 漂移尺度）、**`不可分離`** | 「不可分離」是正當結論，不是失敗 |
| P4 | 任一臂被外來 llama 行程重疊 ⇒ 整組作廢重跑 | 這輪的編排器已內建（連續 3 個安靜樣本才起跑、入侵即殺 driver 並退避） |

## 4. 這輪**不能**回答什麼（先講，避免過度引用）

- k 的最佳值：`--spec-draft-n-max 3` 在 qwen35moe 上會遇到 k_eff 的問題（`docs/KEFF_CAP_2026-09-26.md`），
  所以 E 會反映**實際**深度，不是 3。
- 任何「轉正」的門檻數字：轉正需要 **S ≥ 1 在交付 regime**，而這組四臂是 bench 載體、單一 prompt、r=3。

## 5. 待辦（本輪沒做、也不該順手做）

1. 更正 A5 那三份文件的措辭：`CGC_SERVER_MTP_P_MIN` 從未存在；要做的話得先把 `--draft-p-min` 接上
   （launcher 白名單 + bench 解析），而那是**實作**，要等 §2 的機制結論被下一輪確認再動。
2. 若要再試 accept 這一側，第一件事不是 A/B，是**先讓旋鈕存在並自我見證**（P1 的同型閘門）。

## 6. 執行結果（15:18–15:24）：**on 臂無法執行，off 臂可以 —— 4/4 對 0/4**

§3 的設計、判準、窗口都寫死了，但執行時它撞上的是另一個問題。

| 嘗試 | off 臂 | on 臂 |
|---|---|---|
| 編排器 14:57 | OK rc=0 | rc=1（`CGC-METAL-FAIL` in `test_prompt`） |
| 編排器 15:08 | OK rc=0 | rc=1（同上，1:41 即死） |
| 編排器 15:12 | OK rc=0 | rc=1（同上，1:20 即死） |
| 手動對 15:19 | **pp 265.85 / tg 11.77**（NOMINAL→HEAVY、swap 10551→10765、procs=0） | rc=-6（同上，2.5 min） |

死因逐字（on 臂 stderr；**off 臂 0 次**）：

```
ggml-metal-context.m:904: CGC-METAL-FAIL: command buffer 8 failed (status 5,
Insufficient Memory (00000008:kIOGPUCommandBufferCallbackErrorOutOfMemory))
- refusing to return stale output; lower Metal memory pressure (expert cache / -ub)
or set CGC_METAL_FAIL_STOP=0 to override
```

堆疊鏈固定：`llama-bench test_prompt → llama_context::synchronize → ggml_backend_sched_synchronize →
ggml_metal_synchronize → ggml_abort`。

**讀法**：off 臂（同 cell、同池、同 `-b/-ub`）四次全部完成，差別只有 MTP 的 draft 模組（GPU）與 verify
緩衝。8 GiB pool ＋ dense 權重已把工作集頂在 Metal 建議上限附近，MTP 的邊際足跡正好是把它推過去的那一點。

**兩個不能混為一談的結論**：

1. **速度問題今天沒有答案** —— on 臂拿不到讀數。不是「on 比較慢」，是「on 跑不完」。
2. **這是生產相關的事實**：MTP 轉正會把盒子的餘量吃掉一截；在裝載中的 16 GB 機器上，它連權威 cell 的
   prefill 都過不去（`CGC_METAL_FAIL_STOP=0` 可以讓它「跑完」，但那是回傳過期輸出，這條線不接受）。
   要量它得在剛開機（或至少餘量足夠）的盒子上跑。

**本輪認的工具缺陷**：`frag_orchestrate.py` 在作廢時 `shutil.rmtree` 掉整個嘗試目錄 ⇒ 失敗的唯一證據
是一行 console（「缺席被當成沒有」的同族）。v2（`/tmp/frag_pair.sh`）保留兩臂產物，含死掉的 on 臂。
另：kill 編排器時它的子 `llama-bench`（RSS 7.7 GB、wired 幾乎全是它）變成孤兒活著，直到自己結束
—— 這是「殺 driver 不等於停手臂」的同一族缺陷。

## 7. 重開機後仍然 0/4，於是把它拆成「哪一個維度放不下」的判別（21:42–22:11）

重開機（`up 2 mins`、swap **0.00M**）後同一對再跑一次：

| 臂 | 結果 |
|---|---|
| off（等化四 env、權威 cell、r=3） | **OK**：`pp 299.40 / tg 12.26`（NOMINAL→NOMINAL、swap 0→3516、procs=0）——今天最好的一組 |
| on（權威 cell、r=3） | **又是同一個 OOM**，而且只撐 **13 秒** |

⇒ 不是盒況。於是「pool 從來不是問題」半被證實（使用者不同意縮 pool）——改去查**歷史上 8 GiB 是怎麼跑成功的**：
全庫 MTP-armed 且 pool 8 GiB 的成功產物（09-22～09-25）**全部是 decode-only**（`n_prompt=0`，沒有 `test_prompt`）
且 `-b/-ub 512`、`ctx 4096`。權威 cell 的 `-p 2048 × -ub 5632` 這個 prefill 形狀它們從來沒跨過。

### 判別矩陣（每一支都對標準卡、用 repo 自己的 `cell_contract.check_cell`）

| 變體 | pool | −b/−ub | ctx | 結果 | 對卡 |
|---|---|---|---|---|---|
| 權威 on 臂 | 8G | 5632 | 0 | **METAL OOM**（5/5，含剛開機） | `ok=True`，14/14 相符（只有卡自己宣告的 `fixed_fill_seed`） |
| diag6g | **6G** | 5632 | 0 | OK：`pp 291.8 / tg 8.26` | mismatch：`reps`、`expert_cache_bytes` |
| diag8g_ub512 | 8G | **512** | 0 | OK：`pp 116.6 / tg 11.18` | mismatch：`batch`、`ubatch`、`reps` |
| diag8g_ctx4096 | 8G | 5632 | **4096** | **METAL OOM** | mismatch：`reps`（ctx 是卡上的 runtime_adjustable ⇒ 合法宣告） |
| diag_stream0 | 8G | 5632 | 0 | `GGML_ASSERT(n_tokens_all <= cparams.n_batch)` | 只改 arm env `CGC_PREFILL_STREAM=0` |

四支診斷皆 `r=1`（速度用），失敗的 criteria 一律寫死：有 `CGC-METAL-FAIL` 或沒有讀數即失敗。

### 兩個結論

1. **放不下的是 `-b/-ub 5632` 這個 prefill 圖**（MTP head 在旁邊時），不是 pool、也不是 ctx：
   改 pool 或改 ub 任一即可過；只改 ctx 無效。
2. **順帶抓到一個真缺陷**：`CGC_PREFILL_STREAM=0` 時引擎把 `n_batch` 從 2560/2048 **cap 到 8**
   （`L4 pool capacity=143 -> n_batch … capped to 8 (decode graph bound: 8 tokens)`）然後在
   `llama-context.cpp:2522` 的 `GGML_ASSERT(n_tokens_all <= cparams.n_batch)` 崩掉——
   MTP armed ＋ 非 streaming prefill 這條路徑目前是壞的。

⇒ 因此 **MTP 的 on/off 在權威 cell 上量不了**（不是慢，是跑不完）；要量就得決定 MTP 臂用哪個 cell。

## 8. 修引擎的嘗試（依選擇）：三個候選、全部否證、已撤回

方向：不動 cell、不動 pool，讓 MTP-armed 的 pp 在 8G/5632 過得去。

| # | 改動 | 結果 |
|---|---|---|
| 1 | MTP draft context 的 `n_ubatch` 綁到 512（它繼承目標的 5632） | **不適用**：bench 路徑下它收到的是 2048/512（`-ub` 只進 bench 自己的 cparams，`common_params.n_ubatch` 是 0）——觀測行抓到的 |
| 2 | 同一個 cap 降到 64（draft 圖變 64-row chunk） | 仍 **METAL OOM**（見證行有印：`512 -> 64`）——⇒ draft 的圖不是決定項 |
| 3 | draft 的 `n_ctx` 從 0（→`n_ctx_train`）改成繼承目標的 n_ctx | 仍 **METAL OOM**（`n_ctx=0 -> target n_ctx=2560`） |

三個都撤回（`speculative.cpp` 回到只有他線的 k_eff hunk；dylib 重建、見證字串 0）。

### 這一輪把靶子縮到極小，而且量出了它的上界

- MTP head 在檔內 **700.8 MiB**；其中 `_exps` **278.0 MiB**（走 pool / skip-load，照 loader 的 `_exps` + `blk.` 判準，含 blk.40）、
  非 expert **422.8 MiB**——但其中 **397.9 MiB 是共用的 `output.weight`**（`decode_sweep.py:536` 記錄了
  「blk.40.nextn.{\*proj,enorm,hnorm,shared_head_norm} plus the **shared** output.weight」）⇒ 兩臂都在，**不是差額**。
- 因此 MTP-on 真正多的就是：blk.40 剩下的 ~25 MiB ＋ draft context 的圖（bench 下已 512/64）＋ nextn embeddings 緩衝（2048×n_embd×4 ≈ 33 MiB）。
- 這樣還 OOM ⇒ **off 臂在權威 cell 上已經貼在 Metal 工作集的天花板**；缺口量級 ≲ 100–300 MiB，而卡上沒有任何一個維度可以合法地還這麼多。

⇒ 「不動 cell、不動 pool」的修法需要先有 **Metal 工作集的逐項配置清單**（pool／權重／KV／各圖緩衝各多少），
否則就是盲射（本輪已經盲射三次）。在那之前，唯一能把 on 量出來的偏差最小的變體是 **只改 `-ub 5632 → 512`**
（pool 仍 8G、pp 仍 2048，而歷史上所有 MTP-armed 成功數字也都是 ub 512）。

## 9. Pre-registered rule: the on-arm-**alone** cell (written 22:52, before the reading)

All five on-arm OOMs so far **followed another arm within seconds**, and Metal's residency set
carries `keep_alive = 180 s`. One cell separates the two hypotheses, and its criterion is fixed
here before the number lands:

**Cell** — `armed_fc.sh on` alone at the authoritative cell (`prod-new` + `CGC_SERVER_MTP=1`,
`--spec-type draft-mtp --spec-draft-n-max 3`, `-p 2048 -n 128`, `-b/-ub 5632`, `ctx 0`, `r=3`,
warm-skip 64), preceded by **nothing**: a quiet gate of 3 consecutive samples 60 s apart with zero
llama processes (≥180 s, so any prior residency set has expired) plus zero llama processes at arm
start. Box state (uptime, swap, memory, both binary md5s) and a 15 s neighbour timeline go into the
artifact.

| Outcome | Reading | Consequence |
|---|---|---|
| **PASS** (`rc=0`, both pp and tg rows, zero `CGC-METAL-FAIL`, spec + accept witness) | the five failures were **arm order / residency**, not capacity — the only variable changed is that nothing ran before it | the pairing protocol must run the armed arm **first**, or quarantine ≥180 s with a verified quiet sample |
| **FAIL**, same Metal OOM | the ceiling is at the **cell** (pool 8 GiB ＋ `-ub 5632` ＋ MTP head), independent of order | the discriminating knob is the one that already passed once: `-ub 512` (a declared deviation), or a declared MTP variant cell |

Pre-stated caveat this cell cannot remove: the box carries **swap used ≈5.5 GiB**; a fresh-reboot
(swap 0) reading of this same cell still needs the operator's reboot. A PASS is informative in any
box state (order was the only variable); a FAIL here is **not** yet a fresh-boot FAIL and is labelled
as such.

### 9.1 Result (23:16) — **FAIL alone on a verified-quiet box**

| | |
|---|---|
| gate | 9/9 consecutive name-exact quiet samples (23:13:08→23:15:48 = 180 s) |
| box at arm start (23:15:48) | `up 1:38` (reboot was 21:37, so **not** a fresh boot), swap used **3025 MiB**, free 1686 MB, wired 1830 MB, no llama process, no wrapper |
| neighbour timeline during the arm | `procs=0` at 23:15:48 / 23:16:04 / 23:16:24 — **nobody joined** |
| arm | `armed_fc.sh on`, authoritative cell, `--spec-type draft-mtp --spec-draft-n-max 3`, r=3 |
| outcome | **rc=-6 in ~40 s** — same signature as all five previous failures: the 2048-token prefill graph is built (hook reaches il=39), then `CGC-METAL-FAIL: command buffer 8 failed (status 5, Insufficient Memory …kIOGPUCommandBufferCallbackErrorOutOfMemory)` at `test_prompt` → `ggml_abort`. No pp row, no tg row. src/llama.cpp/ggml/src/ggml-metal/ggml-metal-context.m:904 — the instrument refused to return stale output, as designed |

**Reading**: the arm-order / residency hypothesis is **rejected as the sole cause** — this arm had
nothing before it, on a box whose quiet was *verified by process name* rather than by substring.
The ceiling hypothesis is the live one: at the authoritative cell (pool 8 GiB ＋ `-ub 5632` ＋ the MTP
head) the Metal working set does not fit in the current box state, and the deficit is small enough
that two single-dimension deviations already pass (`pool 6 GiB` → pp 291.77/tg 8.26; `-ub 512` →
pp 116.6/tg 11.18) while the exact cell does not.

**Consequence** (as pre-registered): the arm-order fix ("armed arm first") **does not** buy a
quotable on reading by itself; the path to one is a **declared** MTP variant cell (`-ub 512`, the
smallest deviation that passes) **or** a fresh-boot reading of this same cell — the latter still
requires the operator's reboot, and a FAIL at 3025 MiB swap is not yet a fresh-boot FAIL.

**Instrument defect found and fixed while setting this up** (same family as §7's): the quiet gate
used `pgrep -f "llama-bench|llama-server"`, which also matches any shell whose *command line contains
that pattern* — i.e. this driver's own `sleep …; pgrep …` monitoring shells. The gate therefore
reported `procs=1` for four minutes on a box that `ps` showed empty. Replaced with a name-exact
checker (`comm == llama-bench|llama-server`, `/tmp/quiet_check.py`), which reports 0 on that same box.

### 9.2 The fresh-boot cell is scripted, and its gate is self-tested

`Backup/rerun/on_alone_fresh/run_fresh_cell.sh` (in the repo **on purpose**: a reboot wipes `/tmp`,
and this cell is defined by the reboot). It runs the same arm as §9.1 and refuses, fail-closed,
unless three things hold, each with the reason written into the artifact:

| gate | criterion | artifact on refusal |
|---|---|---|
| fresh boot | `uptime <= 20 min` | `REFUSED_STALE_BOOT` |
| fresh regime | `swap used <= 2048 MiB` | `REFUSED_SWAP` |
| verified quiet | 180 s continuous, 20 s samples, **exact process name** | `REFUSED_BUSY` |

Self-test on the current (un-rebooted) box: `uptime=6444s (max 1200s)` → **refused, exit 3**,
`REFUSED_STALE_BOOT` written. So the cell cannot be run by accident on a used box; after a reboot the
same command runs it in ~4 minutes and records `uptime`, swap, memory and both binary md5s alongside
the rows (or the `CGC-METAL-FAIL` line that killed it).

## 10. The MTP-on cell, falsified across three builds (00:47–01:34, 09-27)

**Premise check first**: `kern.boottime = Sat Sep 26 21:37:49` and `uptime` read 3:31 at 01:13 — the
OS had **not** been rebooted, so the fresh-boot axis of §9.1/§9.2 was still closed. The fresh cell's
gate refused on exactly that ground (`uptime=9828s (max 1200s)` → `REFUSED_STALE_BOOT`).

**What was tested, and by what**:

| attempt | engine | entry | shape | outcome |
|---|---|---|---|---|
| A | live tree, built **00:43–00:44** (`libllama.0.0.627`) | `harness.py bench` (the card's only external door) | authoritative cell, cell contract **15/15 pass** | `rc=-6` in 26 s, `CGC-METAL-FAIL: command buffer 8 … Insufficient Memory`, at `test_prompt` |
| B | committed at the pre-reboot commit `dc397fb2f` (`libllama-common 87fab590…`) | same arm, run against the committed binaries | same cell + same resolved env | `rc=134` in 24 s, same line |
| C | committed at **09-23** `684d29c18` (`libllama-common 2ac1cbb9…`, the build whose commit message claims MTP-on prefill 276.4 / decode 11.35) | same arm | same cell | `rc=134` in 28 s, **same line** |

The three engines are byte-different (`83635aa5…` vs `9f23c4ed…` vs `526c196d…`) and behave
identically, so **the cell's MTP-on failure is not a build regression**. `--batch 4096` was also
tried and is **refused fail-closed** by the cell contract (`batch: 實際 4096 ≠ 權威 5632`), i.e. the
repo offers no legal in-cell deviation to route around it.

Also measured this hour: the live `build/bin` was rebuilt at **00:43–00:44 by another line**
(0.0.578 → 0.0.627), and the committed binary carries an **absolute LC_RPATH** to the repo build dir
— so a "reference build" run silently mixes versions unless `DYLD_LIBRARY_PATH` is pinned. That trap
is why attempt B first "failed" with a `libllama.0.0.627` frame inside a 0.0.578 run.

**The other half of the ask is green**: `m123_oracle_gate.py --tag boxstate-0127` →
**M1 9/9 PASS, M2 9/9 PASS, M3 9/9 PASS**, cross-tab all `num_eq_dec_eq`, probe pool hit 89.2%
(evictions 686), build digests recorded in `Backup/m123_oracle_gate/summary_boxstate-0127.json`.
So on this same box, at this same hour, **the MTP-on server path is bit-identical** — the failure is
specific to the authoritative bench cell's 2048-token prefill graph, not to MTP-on as such.

**Where that leaves the two remaining levers** (both need a decision, neither is "run it again"):

1. **A real reboot** — the only untested axis for the authoritative cell, scripted and gated
   (`Backup/rerun/on_alone_fresh/run_fresh_cell.sh`), ~4 min to a reading.
2. **Declare a variant cell** — batch 4096 is the widest width the budget block ever recorded as
   surviving (5/5 at pool 8 GiB, MTP off); using it for MTP-on means editing the card's authoritative
   block, which is a measurement-definition change, not a workaround.

## 11. The deficit has a name: it is the DRAFT CONTEXT, not the cell (01:43–01:49, 09-27)

The census came from the engine's own allocation summary (`-v` + `GGML_LOG_LEVEL=DEBUG`), run
through the same authoritative cell (`-r 1` for the census). `ggml_metal_log_allocated_size()` is
**commented out on this load path** (`ggml-metal-device.m:1693`), but the named per-buffer totals
print anyway and they are enough — off passes, on OOMs, and the two differ like this:

| item (Metal, MiB) | off | **on** | Δ |
|---|---|---|---|
| MTL0 **model buffer** (weights) | 7421.91 | **7724.86** | **+302.95** |
| main-context KV | 26.56 | 26.56 | 0 |
| **draft-context KV** | — | **512.00** (262144 cells × 1 layer, f16) | **+512.00** |
| **draft-context compute buffer** | — | **493.00** | **+493.00** |
| draft-context CPU compute buffer | — | 264.02 | +264.02 (host) |
| main compute buffer / recurrent RS / gather slabs | 2477.85 / 62.81 / 356.00 | identical | 0 |
| `recommendedMaxWorkingSetSize` | 11453 MB | | |

So MTP-on adds **≈1.0–1.3 GiB of Metal**, ~80% of it *allocation shape*, not work:
the draft context is created with the target's `cparams`, so `n_ctx = 0` resolves to
`n_ctx_train = 262144` — while the draft only ever advances the target's positions and its own
chain holds `k+1` rows. 512 MiB of KV + 493 MiB of graph, for a 4-token window.

**Fix site, named**: `src/llama.cpp/common/speculative.cpp:2584` (MTP) and `:2569` (explicit draft
model) — both `llama_init_from_model(..., cparams)` with `cparams.n_ctx = 0`. There is **no existing
env knob** for it (`DRAFT_N_CTX`/`MTP_N_CTX` do not exist). The minimal, numerics-neutral change is a
gated `cparams.n_ctx = llama_n_ctx(ctx_tgt)` before that call, with a witness line; expected KV
512 MiB → ~5 MiB. The 493 MiB compute buffer is a second, separate shape (draft graph reserved at
`n_batch = 2048`), i.e. a second fix, not the same one.

Also named and still unexplained: the **+302.95 MiB in the model buffer**, while the MTP head's
*file* bytes that the off arm ignores sum to only **50.1 MiB** (`blk.40.*`, incl. 3×16 MiB expert
blocks) — so Metal expands those tensors ≈6×. That is a third item, and it is the one that sits
directly on the ceiling.

---

## 12. MTP-off 的 box-state 基準 + 轉正前置閘（2026-09-27 10:44–10:52）

**為什麼要凍結**：本日第一次 A/B 讀數是**冷啟動單發**（off 7.53 t/s），門的 `warm-skip 64` 同形狀
讀到 **11.61 t/s** —— 差 35%，足以把「量測假象」講成「候選屬性」。所以在談任何 MTP-on 轉正之前，
先把 off 的穩定值凍結，並把它變成**前置條件**而不是事後對照。

**凍結基準**：`scripts/check/mtp_off_baseline.json`（閘門輸出會釘住它的 md5）
- 量法：`harness.py bench --arm prod-new`（MTP off，無 spec），cell = p2048 n128 d512 r3、
  `--warm-skip 64 --fixed-fill-seed 1`、b/ub 5632、ctx 0、ngl 99、t8、pool 8 GiB、q8_0 KV。
- 讀數：**pp 275.74 t/s / tg 11.61 t/s**，rc=0、base_check PASS、thermal worst NOMINAL、
  swap launch 8314.69 → worst 9290.75 MiB（growth +835）、build 630（head `e5d1c0f14`）。
- 產物：`/tmp/mtp_off_bench_0927.json`、`Backup/cgc_logs/llama_server_20260927_104402.log`。
- 重現：`python3 scripts/check/harness.py bench --arm prod-new --charter none --prompt 2048
  --gen 128 --depths 512 --reps 3 --warm-skip 64 --fixed-fill-seed 1 --json /tmp/…`
- 頻帶 ±15%（9.87–13.35）：盒況漂移是真的（swap 6–9 GiB、load 3–5）；落在頻帶外的 off 對照
  讀的是另一台盒子，拿它比較等於默默改寫對照組。

**閘門**：`scripts/check/mtp_promotion_gate.py`（已登記進 harness registry，`needs_window=False`）
判準（fail-closed，每個拒絕都帶理由）：
1. 候選 cell 15 維與基準一致；
2. 自帶**同 session** 的 off 對照，且其 tg 落在頻帶內；
3. on 必須是同形狀的**數字**（只給 `status` ＝沒分，一律拒絕）；
4. `on/off ≥ 1.0`（比自己的 off 慢者不得轉正）；
5. 權威 cell 未綠時必須有 `cell_exception` 宣告，否則拒絕（宣告會被記錄，不是默默放行）。
`--selftest` 8/8 PASS。

**本日候選已預拒**（記錄用）：`/tmp/mtp_on_candidate_0927.json` → `verdict: REFUSED`：
`ON has no measurement (status=cell_oom rc=-6 CGC-METAL-FAIL compute #41)`；
`authoritative bench cell not green (cell_ok != true) and no declared cell_exception`。
支撐數字：§11 的 align 閘已進樹（census draft KV 512.00 → 5.00 MiB，見證
`applied path=mtp n_ctx_req=0 -> n_ctx_draft=2560`，且 `run_server.sh` 已把它加進 env allowlist
—— 此前 harness 門會靜默丟掉未列的 CGC_* 鍵）；server 路徑暖機 A/B：off 14.00 vs on 8.00 t/s
（51-token 段、on/off ≈ 0.57），acceptance ≈ 0.256（draft 90/23）。

### 12.1 修正（11:05）：±15% 是「窗級錨」，3% 是「配對精度」——兩層，不可混

**質疑成立**：本 repo 現在能在**配對**下做到 3%（`SPEED_ACCEPTANCE_GATE` §2 G5：「配對門檻 3%」；
`paired_ab.py` 自述：同一引擎的六個**未配對**交錯臂跨 7.03–10.80 t/s（1.54×），但**臂內** reps
只差 0.6–2%）。所以 v1 的 ±15% 錨帶不該被當成比較精度。實測支撐：同一條門指令 10:44 讀
**11.61**、10:59 讀 **10.97**（15 分鐘 −5.5%，期間 swap +1407 MiB）⇒ 跨窗單讀本來就不是 3%。

**閘門升 v2**（`scripts/check/mtp_promotion_gate.py`，`--selftest` 12/12 PASS）：
- 候選必須是**配對設計**（ABBA／interleaved／paired）且 off／on 各 ≥2 reps；
- **3% 判在配對自己的重複上**：兩側 spread ≤3%（`null` 有給時，儀器地板也 ≤3%）；
- 決策用**配對中位數比值**：`on/off ≥ 1.0`；
- 凍結值只做**窗級錨**（±15%，回答「還是不是同一類窗口」），並在 `mtp_off_baseline.json`
  的 `band.role` 明文標注「不是比較精度」；
- 權威 cell 綠、或顯式 `cell_exception` 宣告。

今日候選在新判準下的拒絕（`/tmp/mtp_gate_verdict_v2_0927.json`）：`not a paired design`；
`off.reps missing (<2 repeats)`；`ON has no measurement (cell_oom rc=-6 compute #41)`；
`authoritative bench cell not green`。**任何 MTP-on 轉正的第一步因此變成一件事：先跑配對
（`paired_ab.py --null` 量地板，再 ABBA 量 on/off），而不是再讀一顆單發數字。**

### 12.2 走廊（19:20）：把「11.61 單點」換成 5 個同 cell 樣本的分布

**為什麼**：今天 11:15 之後的 off 讀數（11:15 **12.02**、11:46 **11.98**、12:19 **11.70**、10:59 10.97）全都比凍結點
11.61 高，也就是「最高分」這種問法會被窗內運氣直接蓋過 3.5% —— 而 3% 正是 G5 的配對門檻。單點基準的
形狀本來就撐不住這個問題。

**新工具** `scripts/check/bench_ingest.py`（`--selftest` 8/8，已登記進 harness registry，`needs_window=False`）：
把矩陣產物補成 harness 門口徑，補的欄位用閘自己的謂詞算（`import harness` → `_base_gate`／`_swap_arm_gate`／`_cell`），
而直跑裡**不存在**的東西（`box_gate`／`charter`／`sys_*`）一律標 `synthesized` 或 null + 原因，不偽造成「閘跑過」。
每個樣本判 `accepted`／`variant`／`rejected`，理由全部寫進產物（`--strict` 有任何 rejected 就非 0）。
**產物契約是「每臂都有」，不是「應該有」**：`base_check`／`cell`／`box_gate` 缺任一件＝硬缺口 ⇒ 拒收
（補的是佔位，不是讀數）；`charter`／`sys_*` 缺＝軟缺口 ⇒ 補佔位並記在 `ingest.contract_gaps`
（今天 09:46 那筆就是軟缺口 `charter`，而它同時已經被 `r_head` 擋掉）。

**走廊產物**：`Backup/mtp_off_corridor/off_corridor_20260927.json`（門形，7 臂）＋ `.summary.json`（統計）。
同 cell、同 build 630（head `e5d1c0f14`）、皆 NOMINAL，時間 10:44–12:20：

| | tg t/s | pp t/s | wall s |
|---|---|---|---|
| median | **11.696** | 292.85 | 77.8 |
| min–max | 10.973 – 12.019 | 275.74 – 304.70 | 73.4 – 85.2 |
| spread | **8.95%**（sd 0.42；跑內 sd 均值 0.165） | 9.89% | 15.2% |

5 個 accepted 樣本整段落在 ±15% 錨帶 [9.87, 13.35] 內 ⇒ **錨寬是對的，單點是錯的形狀**。

**兩個 rejected 是規則擋的，不是人判的**：
- 09:46 → `r_head: engine_build='825ec07d5' != 'e5d1c0f14'`（不同 build 的數字不是同一個盒子）；
- 13:55 `off_dense` → `r_wall: wall 996.5 s > 240`＋`r_floor: pp 47.03 / tg 3.62 < 60% of anchor`
  （窗級崩壞：wall 13×、worst swap 9212 MiB；那次 **`base_check` 仍然 PASS** ⇒ base_check 不會替你抓這種崩壞，
  所以 wall/地板規則必須獨立存在）。

**順帶一條校準**：11:17 那筆是這批裡**唯一**真的矩陣直跑（缺注入）；11:47／12:19／13:55 其實都帶
`base_check`+`box_gate`+`cell` ⇒ 它們是 harness 門產物。這件事由 `detect_runner()` 自動判，不靠記憶。

**閘的反應**：把走廊當 `off.reps` 餵進 `mtp_promotion_gate.py` → `REFUSED`，理由就是 rule 4
`off repeats disagree by 8.95% > 3%`，同時 notes 會帶出走廊摘要與 artifact 路徑（`/tmp/gate_corridor_verdict_0927.json`）。
**走廊回答「哪些單點屬於同一種窗」，3% 仍然只活在配對自己的重複上。**

**基準檔升 v2**（`scripts/check/mtp_off_baseline.json`，`schema: mtp-off-baseline/2`）：新增 `corridor`
（stats、rejected、`staleness_rule`：走廊產物釘住 baseline 的 md5，改完 baseline 要重跑 ingest）、
`band.covers_corridor: true`；`measured`／`band`／`cell` 原封不動 ⇒ 閘相容（gate `--selftest` 12/12、
`harness.py selftest` PASS）。

**重現**：`python3 scripts/check/bench_ingest.py --product /tmp/mtp_std/off.json --product … \
  --out Backup/mtp_off_corridor/off_corridor_20260927.json --summary-out …summary.json`
（完整指令在 baseline 的 `corridor.reproduce`）

### 12.3 漂移的主因與可引用的窗（19:29–19:36 補一組 null series）

**做了什麼**：在走廊裡加一組**背對背 null series**（19:29、19:30、19:32、19:34，同 arm 連跑四支，`/tmp/mtp_null_series/null_{1..4}.json`），
於是走廊共 11 支樣本（7 accepted／4 rejected）；並在 `bench_ingest.py` 加上 `health` 層與「窗」分段。

**主因：不是趨勢，也不是任何單一變數；是 rep 級雙模。**
- 同一個 arm 的三個 rep：**要嘛兩個互差 ≤1%**（12:19 = 0.94%、10:59 = 1.9%），**要嘛其中一個掉 3.1–8.2%**
  （11:15 = 4.10%、11:46 = 3.06%、19:29 = 7.33%、19:30 = 7.80%、19:32 = 7.06%、19:34 = 5.21%）。今天 11 支裡有 8 支是後者。
  ⇒ 跑出來的中位數是**混合值**，不是模態；走廊 10.84–12.02 的範圍主要由這個混合造成。
- 窗的時間尺：**19:29–19:34（5 分鐘）內兩支 accepted 的中位數只有 1.52% 差**（11.66 / 11.48）；跨 95 分鐘的走廊是 8.97%（1σ = 3.6%）。
  單讀對單讀的最小可辨差 = 2√2·σ ≈ **10%（95%）** ⇒ 10.84 與 12.02 不可稱差異。
- 對 clock 也只有 Spearman +0.10（沒有時間趨勢）。

**唯一量到的環境共因：背對背的 carry-over。**
四支連跑：pp **302.85 → 281.65 → 269.22 → 258.01（單調 −14.8%）**、tg 只 −3.0%（11.66 → 11.31）；thermal hist 第 3 支起出現
MODERATE/HEAVY（32/151、69/153），而**啟動前的 worst 標籤還是 NOMINAL** ⇒ 只讀啟動標籤會漏掉它。
推論：連跑第 3 支起不可當對照；A/B 必須交錯（ABBA），>2 支要留冷卻段；**pp 對 carry-over 比 tg 敏感得多**，pp 的比較更要用交錯。

**沒有任何被記錄的變數能追蹤 tg**（對 5 支晨間樣本）：swap 存量 ρ=0.00、run 內 swap 成長 −0.30、啟動 free +0.30、wired 成長 −0.30、per-miss 延遲 +0.40（方向相反）、
run 內 pagein 率 ≈0.00、pp +0.60。**順帶一個儀器陷阱**：11:15 那支沒有 `sys_rate`，把它當 0 代入會得到 `ρ=−0.80` 的假相關——
缺件不能當 0（這個 repo 已經踩過兩次：`growth=+0` 與 `verdict=advise`）。

**免費的機器級見證：sampler coverage**（`thermal.n × interval / wall`）：正常 **0.91–0.97**、冷窗 **0.739**（09:46，不同 build）、崩壞 **0.118**（13:55）。
監測執行緒被餓死代表整台機器在 stall，不是「這一臂慢」——這是唯一一個不用加儀器就能抓到的盒況塌陷訊號。

**自動健康判準**（進 `bench_ingest.py`，每臂 `health.checks` 帶 ok/warn/fail 與讀數，`--selftest` 12/12）：
`rep_shape`（spread ≤2.5%＝tight；單一 rep 比同伴中位低 ≥3%＝bimodal；否則 spread）、`thermal`（worst 為 NOMINAL **且** hist 無 MODERATE/HEAVY）、
`coverage ≥0.90`、`wall ≤ 1.3× 同批健康中位`、`identity`（head＋cell），並把樣本按「相鄰間隔 ≤10 分鐘」分段成窗。今天：**clean 2 支、mixed 5 支、sick 4 支**。

**可引用的穩定視窗**（寫進 `mtp_off_baseline.json` 的 `stable_window`）：
> 同 head、同 cell、`health=clean` 且與同伴間隔 ≤10 分鐘；任何 ≤3% 的宣稱還必須來自這個窗內的**配對**（ABBA／交錯、兩側各 ≥2 reps、兩側 spread ≤3%）。

操作結論三條：①**不要挑最高分**（走廊 8.97% 整段落在 ±15% 錨 [9.87, 13.35] 內，最高分是尾抽樣，不是能力值）；
②連跑上限 2 支，第 3 支起要冷卻，否則 pp 先掉；③任何「這支就是慢」的結論先看 `coverage` 與 `rep_shape`，兩者都在產物裡，不需要重跑。

**重現**：`python3 scripts/check/bench_ingest.py --product /tmp/mtp_null_series/null_1.json --product … --out Backup/mtp_off_corridor/off_corridor_20260927.json --summary-out …`
（完整清單在 baseline `corridor.reproduce`；null series 的重跑指令：`for i in 1 2 3 4; do python3 scripts/check/harness.py bench --arm prod-new --charter none --prompt 2048 --gen 128 --depths 512 --reps 3 --warm-skip 64 --fixed-fill-seed 1 --json /tmp/mtp_null_series/null_$i.json --workdir /tmp/mtp_null_series; done`）
