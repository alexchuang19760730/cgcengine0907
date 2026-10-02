#!/usr/bin/env python3
"""引用閘門：用**同一場內逐 rep 的離散**決定一個數字能不能引用。

為什麼要它（2026-09-29，§54／§55）
----------------------------------
交付 cell 走了 5 場，唯一拿到 `attribution=none` 的那一場是**最慢**的一場：

    file            t/s      CV%   逐 rep samples_ts         attribution
    anchor8g.json    8.257   45.9  [10.45, 10.44,  3.88]    none   <-- 唯一乾淨
    anchor5.json    10.692    5.4  [10.05, 10.86, 11.16]    swap
    anchor4.json    10.644    9.6  [ 9.62, 10.66, 11.65]    swap
    anchor2.json     6.112   41.6  [ 8.74,  3.66,  5.94]    both (HEAVY)
    anchor_delivery  9.702   11.5  [10.18,  8.43, 10.49]    swap

⇒ `attribution` 答的是「**窗口**乾不乾淨」，答不了「這一場的三個 rep 是不是同一個
量」。`none` 的那一場第 3 個 rep 慢了 2.7 倍，`±3.79` 完全由**一個 rep** 扛。
本文件 §15 §四(3) 早就把結論寫成散文了（「`none` 不是可重現性保證…一條可以拿去
用的基線應該是重複數次的中位數與散度」）——這支工具把它變成可執行、fail-closed 的閘門。

判準（**全部沿用既有常數**，不新增品味）
----------------------------------------
一支臂的一個 row 要能被引用，四項必須同時成立：

  R1 樣本數：`len(samples_ts) >= MIN_REPS(=3)`
      —— 少於 3 就沒有中央趨勢可談（house bench 標準，已認證錨點即 3 reps）。
  R2 離散（全 rep，即 `avg_ts` 的那個向量）：`max/min <= SPREAD_LIMIT(=1.10)`
  R3 離散（kept rep，即 `platform_ts` 的那個向量）：同上
     ⚠ **R2 的拆欄例外（2026-10-01，MEASUREMENT_CONTRACT §3.3b）**：**pp-less cell**（交付 cell，
     `-p 0`）的**第 1 個 rep 天生冷**——沒有 prefill 那一列先把池餵飽（`--warm-skip` 蓋不掉），
     實測 5/6 場的第 1 個 rep 低於後兩個。合約對這種混合的指示是「**拆欄**」（cold 留診斷、
     steady 才是讀數），不是作廢 ⇒ 第 1 個 rep 標 `cold`，R2 只看 steady reps（＝ R3／kept），
     引用的值是 `quoted_ts`（steady 平均，與產物自己的 `platform_ts` 同口徑）。
     兩個限制：只有「第 1 個 rep **低於** steady 中位數」才拆（冷的是別顆 rep ⇒ 那不是格子的
     結構，是真的不穩，維持原判）；格名要查得到契約才能斷定 pp-less，查不到 ⇒ 不拆（fail-safe）。
      —— **兩個向量都查**：報出來的是 `avg_ts`，但 rep 1 天生偏冷（`--warm-skip` 與
         `platform_ts` 就是為此存在）⇒ 任一向量有 rep 主宰就不可引用。
  R4 窗口：`attribution.verdict == none`（既有合約，**仍必要、只是不再充分**）
  R5 臂身分（2026-09-29 新增）：這支臂身上**不得**掛著「原始碼自己明文說不得引用吞吐」的量具
      —— 清單見 `THROUGHPUT_VOID_INSTRUMENTS`。旗標設了 ⇒ **DIRTY**，與窗口項同級：
      R2／R3 判的是「這一場的三個 rep 是不是同一個量」，R4 判的是「窗口乾不乾淨」，
      而 R5 判的是**更前面**的一件事——「這個數字量到的還是不是交付臂」。
  R6 輸出見證（2026-09-30 新增）：這支臂的**輸出**被驗過嗎？單次提交臂（`CGC_SEG_BATCH`）跳過
      per-layer hook ⇒ 它報的 t/s 不是交付目標的那個函數；沒有 M1／answer-hash 見證 ⇒ **DIRTY**。
      為什麼 R5 不夠：旗標可以洗掉，level 洗不掉（見 `UNVERIFIED_OUTPUT_ARMS` 的實測）。
  R7 口徑（2026-09-30 新增）：這支臂的 **profile** 必須在 `QUOTABLE_PROFILES`（＝`prod-new`）內。
      operator 2026-09-28 的命令是「只接受 prod-new profile ＋ harness bench，其他測試方法的結果
      皆不接受」（立項卡 `scripts/check/charters/exp-anchor-prodnew.yaml` 檔頭記的就是這一句）。
      讀不到 profile 也**不放行**（fail-closed）⇒ 與 R5／R6 同級 **DIRTY**。
      為什麼 R1–R6 不夠：它們全部只看**這一場內部**（離散／窗口／臂上的旗標／輸出見證），
      沒有一條問「這一場是不是在認可口徑上跑的」。實測：`prod25-stream` 的 12.954
      （`anchor_repro_2026-09-28/r3_5.json`，`contract.ok=true`、cell=delivery）今天只是 `UNSTABLE`
      —— 只要它那一場的三個 rep 落在 1.10 內，R1–R6 會全部放行，而它**根本不在認可口徑上**。
  R8 格／行／聚合單元（2026-09-30 新增）：站上認證表的必須是**這一格的權威 row**，不是「同一支
      harness 跑出來、但被自訂聚合過的任何中間值」。三個子項全部 fail-closed：
      8a 權威格：讀不到格名（`cell.named_cell`／舊 schema 字串皆無）⇒ DIRTY；產物帶 `contract`
         塊而 `ok=false` ⇒ DIRTY（起跑前就沒過格契約的那一場）。
      8b 行＝格的形狀：格宣告了 (prompt, gen, depths, reps) ⇒ 這一行的 (n_prompt, n_gen) 必須
         等於那一格**兩條 row 中的一條**（prefill `(prompt, 0)`／decode `(0, gen−warm_skip)`），
         `n_depth` 必須等於宣告值，逐 rep 向量長度必須等於 `reps`；格宣告 `warm_skip>0` 而產物
         自報 `warm_skip_applied=false` ⇒ 那一行不是這一格宣告的行（槓桿是 no-op）。
      8c 聚合單元：輪級聚合（自訂 `n_rounds`／`decode_tps_median`，沒有權威格、沒有逐 rep 向量）
         **不得隱形**：`iter_rows` 把它變成 REFUSE 記錄（不是靜默跳過）——「沒被判到」與「判可
         引用」在這一支的輸出裡必須分得開。
      ⚠ 走訪範圍（2026-10-01 硬化，口徑統一）：`{plan, records:[{rows:[…]}]}` 的三層包裝也走
         （`_record_rows()`）。理由與 8c 同一條：那些 row 今天落在**靜默跳過**裡，而
         `docs/S3B_RHO_COST_2026-09-30.md §8.4` 已把它寫成 ``fail-open 角落``。臂身分以
         **檔級與 record 的合併視圖**判定（record 的 `tag`／`env` 優先，檔級 `cell` 保留）；
         這不是放寬 —— 包裝裡的 row 一樣逐條過 R1–R8（缺逐 rep 樣本 ⇒ REFUSE）。
      為什麼 R7 不夠：R7 只問「這一場是不是在認可口徑上跑的」（profile），答不了「這一場是不是
      那一格的那一行、用那一種聚合報出來的」。實測：`l2010_b1b3_2026-09-30/b1_ws256.json` 帶著
      `cell=delivery-ws256`、`contract.ok=true`（口徑與窗都過），但 `warm_skip_applied=false`
      ⇒ 它那一行不是這一格宣告的行 —— 只有 R8 擋得住。
      ⚠ 這一條是**新的嚴格度**：R8 上線後，舊產物只要缺格名／形狀不符就會由原本的 `UNSTABLE`
      變 `DIRTY`，那不是「變差」，是「以前沒被問到」。要進表就跑權威 row：
      `python3 scripts/check/cell_contract.py --cell <name>` 會印出那一條 `harness bench` 命令。
  選配（`--reference <t/s>` ⇒ `|Δ| <= --band-pct(=10%)`）

`SPREAD_LIMIT = 1.10` 與 `MIN_KEPT_MEDIAN = 3` 沿用 `http_duo.py`，那裡有它自己的
校準紀錄（2026-09-18：乾淨場 1.008–1.034、不乾淨場 1.207–1.941 ⇒ 1.10 落在缺口
中間）。本工具另外用**另一支儀器**（bench，非 HTTP）獨立佐證同一個極限：

    乾淨場（本閘門要放行）: 11.703→1.061、11.583→1.023、27.526→1.01、26.455→1.04
    不乾淨場（要擋）      :  9.311→★、8.257→2.69、6.112→2.39、5.821→1.68、9.702→1.24
    ⇒ bench 族的分界在 1.09 與 1.24 之間，1.10 仍在缺口內 ⇒ 兩支儀器挑中同一個數字。

⚠ 兩支儀器的樣本 floor **不同，而且必須講清楚**：HTTP 那支 quote 的是**中位數**，
兩人組的中位數是退化的 ⇒ 那裡要求 3 個 **kept**（`--reps 4`）；本工具 quote 的是
bench 的 `avg_ts ± stddev`，house 標準是 3 reps，且已認證的 11.703 錨點本身就是
`-r 3` ⇒ 這裡的 floor 是 3 個**樣本**。用 3 個 kept 會把 house 自己的錨點一起作廢。

讀不出 `samples_ts`、或產物內部不自洽 ⇒ **REFUSE**（不猜）。「沒有證據說它穩」不是「它穩」。

R5 為什麼必要（同一天踩到的實例）
--------------------------------
`Backup/spac_sweep_2026-09-29/k_sweep_T4_K4.json` 是**交付 cell** 上 27.338 t/s、逐 rep
[27.38, 27.24, 27.39]（max/min **1.005**）、`attribution=none` ⇒ R1–R4 全過。但它身上掛著
`CGC_MISS_MASK_DBG=1`（`llama-context.cpp:3962` 的 `MISS_MASK_DBG`：**每步多一次 synchronize**，
原始碼自己寫「DIAGNOSTIC ONLY … **Never quote throughput from an arm with this on**」）
＋ `CGC_MISS_MASK_COST=1`（同一次回讀的診斷計時）＋ `CGC_SEG_BATCH=1`（跳過 per-layer hook ⇒
餵的是另一條路徑）。⇒ 沒有 R5 時，閘門會**放行一個源碼明文禁止引用的數字**，
而 27.338 又同時 ≥20／≥25 ⇒ 只要有人把 L20／L25 的 evidence 指過去，**兩個主節點會一起變綠**。
閘門的失敗方向必須是 fail-closed：量具在臂上 ⇒ 不可引用（不是「大概偏低所以安全」）。

用法
----
    python3 scripts/check/quote_gate.py --selftest
    python3 scripts/check/quote_gate.py check Backup/anchor_clean_2026-09-29/*.json
    python3 scripts/check/quote_gate.py check --glob 'Backup/pool_window_2026-09-29/*.json'
    python3 scripts/check/quote_gate.py check --reference 11.703 Backup/.../*.json
    python3 scripts/check/quote_gate.py check --json out.json Backup/.../*.json

rc：0 = 全部可引用；1 = 有不可引用；2 = 用法錯誤；3 = 沒有可判的產物。
"""
from __future__ import annotations

import argparse
import glob as _glob
import json
import os
import sys

# --- 判準常數（跑前寫死；改動要用新的一批場次，不能因為某個好數字被擋而放寬） ---
MIN_REPS = 3
SPREAD_LIMIT = 1.10          # 與 http_duo.py 同值（那裡的校準紀錄見檔頭說明）
#   ⚠ 它是**極值統計**（全 rep max/min ⇒ 實測隨 n↑ 變嚴：1.0866(n=3) → 1.1197(n=7)）。
#   要換成配對口徑（paired-v1）得先過預註冊卡（前瞻、0 回填）：
#   scripts/check/charters/e-quote-caliber-paired-2026-10-01.yaml ＋ docs/QUOTE_CALIBER_PAIRED_PREREG_2026-10-01.md
MIN_KEPT_MEDIAN = 3          # http_duo.py 對「中位數」的 floor（3 個 kept ⇒ --reps 4）
SPREAD_LIMIT_HTTP = SPREAD_LIMIT
CLEAN_ATTRIBUTION = ("none",)
BAND_PCT = 10.0
INTEGRITY_TOL_PCT = 0.5      # 回報的 stddev 與 samples_ts 反算值的容許差

# --- R5 臂身分：原始碼**自己**說不得引用吞吐的量具（每一條都附出處；改動要附理由）---
# 判準不是「有儀器就髒」——`CGC_GPU_TIMING`／`CGC_GPU_NODES` 這類在 Metal 上記錄
# command buffer 的 GPUStartTime/GPUEndTime、且「unset 時無熱路徑成本」的取樣不在表內
# （它們不進熱路徑的關鍵路徑）。這裡只收**明文標了 never-quote / DIAGNOSTIC ONLY** 的：
THROUGHPUT_VOID_INSTRUMENTS = {
    "CGC_MISS_MASK_DBG":
        "每步多一次 synchronize 讀回 mask（llama-context.cpp:3962「DIAGNOSTIC ONLY…"
        "Never quote throughput from an arm with this on」）",
    "CGC_MISS_MASK_COST":
        "同一次回讀的診斷計時（llama-context.cpp:3985「the mask-cost instrument」）",
    "CGC_MISS_MASK_HIST":
        "逐層 miss 直方圖，靠同一個 DBG 回讀（llama-context.cpp:3995）",
    "CGC_MMV_FUSE_DBG":
        "逐節點 mmv-fuse 對照列印（ggml-metal-ops.cpp:492）",
    "CGC_RHO_PROBE":
        "自帶每層同步讀回（claim_instruments.yaml：never quote throughput from it alone）",
    "CGC_SPAC_DBG":
        "逐層成員探針，與 SPAC 饋送共用同一個列印點（llama-expert-cache.cpp:2347）",
    "CGC_EB_NOFILL":
        "不讀位元組、直接回報填好的診斷臂（llama-expert-cache.cpp:3738「Output is garbage: "
        "this arm is TIMING ONLY, never a correctness measurement」）—— 它量到的是終點上界，不是交付臂",
}

# --- R6 臂的輸出見證：單次提交臂的 t/s 與交付目標**不是同一個函數**（2026-09-30 新增）---
# 為什麼還要一條（R5 不夠的實測）：2026-09-30 00:15 把 k_sweep_T4_K4 的三個診斷旗標全部拿掉、
# 其餘逐欄不變（`Backup/quote_hygiene_2026-09-30/k4_noflags.json`，同一 build e5d1c0f14）⇒ **26.203 t/s、
# 逐 rep [26.06, 26.31, 26.24]（1.010）**。也就是說：**旗標洗掉，level 還在**。
# 那一場的窗口是 swap（判 DIRTY，見下），但窗口乾淨與否是抽籤（§53 的四場全 swap）——
# 下一次抽到 `none` 就會出現一個「R1–R4 全過、≥25、而輸出未驗」的讀數，
# 而 26.2 ≥ 25 ⇒ 兩個主節點會一起變綠。→ 擋它的不能是旗標，只能是**輸出見證**。
# 解除條件（刻意寫死，讓它可被解除）：產物附上 M1／answer-hash 的輸出見證（走 server 端；
# llama-bench 不含答案比對）⇒ 該臂即可回到正常判準。
UNVERIFIED_OUTPUT_ARMS = {
    "CGC_SEG_BATCH":
        "單次提交臂：跳過 per-layer hook（llama-context.cpp:4059「CGC_SEG_BATCH=1 SKIPS THE HOOK」），"
        "成員維護改走回讀路徑（:4067+ cgc_rb_seg_batch）；本臂自己的 stderr 指紋是 decode 期 "
        "`gather (ensure) hits=0/0` ＋ `prefetch=0/0`（池凍結在 prefill 上），"
        "而 G3 未武裝時未駐留的選取走 `e % ns`（映射到別的真專家）⇒ 輸出未驗"
        "（§34 g4-miss／fillahead-fed-miss：「輸出仍是 garbage（B 半未做）」）。"
        "⇒ 這一格的 t/s 不是交付目標的那個函數；解除條件＝附上輸出見證（M1／answer hash）。"
        " ⚠ 2026-09-30：見證**真的跑了**，結果是紅的（不再是推定）—— 同一 build（libllama.0.dylib "
        "md5 c76358aa）上的配對 M1/M2/M3：對照臂（無此旗標）9/9 位元等同、答案「42」、覆蓋率 100%；"
        "本臂 M1 0/9、M2 2/9，**第一格（step 0, DEF）就不同**（row_fnv1a64 30f3eb92… → b322b408…、"
        "argmax 198→2047），答案由「42」變成 16 個 1 ⇒ 這支臂的輸出確實不是對照臂的輸出。"
        "⇒ 解除條件不變，但它現在是一條**引擎驗收**（讓 M1 回到 9/9，例如把回讀路的成員維護做對），"
        "不是補一份形式見證：見證只是它的量尺。實測檔案：Backup/m123_oracle_gate/summary_r6-segbatch-2026-09-30.json "
        "（對照：summary_r6ctl-nosegbatch-2026-09-30.json）",
    # 2026-09-30 補收（§81）：R6 原本只認 `CGC_SEG_BATCH`，於是**同一天**另一支「依構造 racy」的
    # 槓桿從缺。實測抓到：`Backup/stepbudget_2026-09-29/gap_sweep.json` 的 14.900 t/s 掛著
    # `CGC_SUBMIT_AHEAD=1`，而它在 R6 之後沒有任何一條理由擋得住它（只有窗口標籤=both）——
    # 也就是說，只要抽到乾淨窗口，一支**讀到 stale leaf、整張圖壞掉**的臂就會變成 QUOTABLE。
    "CGC_SUBMIT_AHEAD":
        "把 segment[i+1] 在 segment[i] 的 top-k hook「寫完 remap leaf」之前就提交，而 segment[i+1] "
        "透過 mul_mat_id 消費那個 leaf ⇒ 讀到 stale leaf ⇒ 整張圖壞掉（`ggml-backend.cpp` 的 "
        "submit_ahead 分支；§19／§25 的原話是「依構造 racy／輸出 garbage」與「它的代價不是"
        "『慢』，是**錯**」，step 75.91 → 58.97 ms（−22.3%））。"
        "⇒ 這一格的 t/s 不是交付目標的那個函數；解除條件＝附上輸出見證（M1／answer hash）。"
        "唯一合法的解除路徑是 E2（`CGC_SLOT_TABLE_GPU=1;CGC_SUBMIT_AHEAD=1`）實測 bit-identical —— 尚未跑",
}

# 解除路徑的**力學**（2026-09-30：L20-1 機檢後補寫 —— 目的是讓下一個人不必重新發現一次）
# --------------------------------------------------------------------------------------------
# ① 產生見證的工具**已經存在**：`scripts/check/m123_oracle_gate.py`。它就是登記表要求的 server 端
#    路徑 —— 走 `run_server.sh`（同一份 profile／白名單／載入路徑），送同一個確定性探針，比對 logits
#    dump ⇒ M1（逐列 row_fnv1a64 相同 ＝ 位元相同）／M2（argmax 相同）／M3（top-N 集合相同），
#    而 `--env KEY=VAL` 就是把它轉成某一支臂。
# ② 「跨臂比對」這一步有**兩個先例**已經幫它講清楚（同目錄 `m123_oracle_gate.py` 的 DIAGNOSTIC_KEYS）：
#    `CGC_SLOT_TABLE_GPU`（09-15）與 `CGC_FILL_NOCACHE`（09-26）都是**被刻意**收進那個「不改數字」
#    集合的，理由逐字是：「那個鍵的主張就是這件事 —— 不收進去的話每一趟都會被判 INCOMPARABLE，
#    閘門就永遠說不出它存在的唯一命題；若主張為假，閘門會在 LOGITS 上失敗，而那正是它該失敗的地方。」
#    ⇒ 所以 R6 的解除是同一條路（三個步驟，都在既有的工具上）：
#      (i)  把 `CGC_SEG_BATCH` 連同它的主張收進 DIAGNOSTIC_KEYS（主張＝跳過 per-layer hook 只改
#           成員**在哪裡**被維護，不改**哪些**成員被選到 ⟹ 必須產生同一組 id 與同一份 logits）
#      (ii) 跑一趟 `m123_oracle_gate.py --env CGC_SEG_BATCH=1 --tag <...>`
#      (iii) 主張為真 ⇒ M1 ＝ M2 ＝ 1.0 ⇒ 見證成立，該臂回到正常判準
# ③ **結果（2026-09-30，已跑）**：M1 ＝ 0/9，對照臂 9/9 ⇒ **主張為假**，該臂不回到正常判準。
#    配對條件：同一 build（libllama.0.dylib md5 c76358aa）、同一 profile／同一組態戳記（config_diffs=[]）、
#    同一 prompt 指紋（50f8c5ae…）、唯一差異＝ `CGC_SEG_BATCH=1`。對照臂：9/9 位元等同、答案「42」、
#    覆蓋率 100%；本臂：第一格（step 0、DEF）就不同（row_fnv1a64 30f3eb92… → b322b408…、argmax 198→2047）。
#    產物：`Backup/m123_oracle_gate/summary_r6-segbatch-2026-09-30.json` vs `summary_r6ctl-nosegbatch-2026-09-30.json`。
#    ⇒ 這條路已經被走完一次，它不再是「待補的步驟」，而是一個**被否證的期待**；下一個動作不是再跑
#    一次見證，是讓這個旗標真的做到位元等同（M1 就是那個驗收）。
#    ⚠ 同日把「怎麼修」也縮到兩條並寫成工作項：`docs/R6_SEGBATCH_FIX_PLAN_2026-09-30.md`
#      (A) 全駐留（可能不需新機制：`prewarm_hot_capped` 在槽數 256 時就是全填，但實跑 caps=256
#          仍 evict ⇒ 先答「為何槽數不是 256」）；(B) 預測 ＋逐層重算（源碼自己寫的下一版）。
#    ⚠ 另一個新失敗模式（不影響上面的判詞，但很值得查）：帶 `CGC_MISS_MASK=1` 的變體
#      引擎吐**全 NaN**（`m123_oracle_gate.jsonl.invalid`：`non-finite values: 993280 of 993280`）
#      ⇒ 佔位讀到非有限值／未初始化槽，那是硬性正確性 bug，比 M1 差異嚴重。
# ④ 兩個代價（都已發生，所以現在可以照實寫）：(a) 它會**起一個 server**（重、要避開平行 session ——
#    本次跑前確認過 0 個殘留）；(b) 它**失敗了**，如預期。順帶一個可用的副作用：這兩趟各起停一次
#    都乾淨（0 殘留、各 62 行 launch log），所以它是這條 server 路徑**可重複執行**的證據 ——
#    只是它產出的是 M1/M2/M3 與 pool counters，**不含 t/s**（見證不是吞吐量尺）。
# ⑤ **政策（operator 2026-09-30）**：生產級一律 **prod-new profile ＋ `harness bench`**。本次見證跑在
#    `prefill250`（m123 的預設，因為參考是在那之下 dump 的）⇒ 它是**診斷級的配對**，不是可引用的生產級
#    讀數。若將來要在政策口徑上做見證，基準必須是 **prod-new 的參考**（`--write-ref`），不是 prefill250 那份。
# ⑦ **登記表外的洞：查了，而且結案（2026-09-30）**。`CGC_B_SCHEME=1` 的源碼註解自己寫著
#    「diagnostic arm: routing is wrong, output is garbage, never a deliverable」，而它**不在**上面這張
#    表（也不在 R5 那張）—— 一支帶著它的臂在閘門眼裡是乾淨的。當日把它查清楚了，兩個量測：
#      (a) 速度：五臂同場 `CGC_B_SCHEME=1;CGC_SLOT_TABLE_GPU=1` ＝ 90.9 ms/step，與控制臂 99.3 ms 同級
#          （差在窗口噪音內）⇒ **沒有紅利會被它偷走**。
#      (b) 數值：配對 M1/M2/M3 ＝ 對照臂 9/9 vs 本臂 **9/9**（答案都是「42」、覆蓋率 100%）
#          ⇒ **hook 在的時候，佔位被 hook 覆寫，routing 是對的。**
#    ⇒ 裁決：**不進 R6**（沒有理由）。源碼那句警告的適用範圍是**單段提交的組合**（hook 被跳過時），
#    不是這面旗標本身；要改那句話就把它限定成「僅在 `CGC_SEG_BATCH` 之下」—— 但那要動 `src/`，
#    依 repo 自己對引擎改動的規則（D5）得再跑一次 oracle，所以先留在這裡。
#    產物：`Backup/m123_oracle_gate/summary_r6-bscheme-2026-09-30.json`（該趟閘門標 INVALID COMPARISON＝
#    「跨組態，不得當判詞」，那正是這個測試的形式；配對結論來自「同日同 build 的對照臂 9/9」）。
#    背景：docs/S1_FLOOR_ARM_ATTRIB_2026-09-30.md（§附帶）。
# ⑥ 還缺的最後一塊（與③互不相抵，仍然缺）：**會讀見證的程式碼**。這一節底下只有兩支旗標函式（arm_*），
#    沒有任何欄位能讓一份見證清掉 R6 ⇒ 見證就算跑成綠的也沒地方放。看板 D16 的卡點斷言掃的就是這件事；
#    今天跑的是紅的，所以它同時也是「這支臂不能用」的量測依據。

# --- R7 口徑：可引用的 profile 白名單（問的是「認可與否」，不是「哪個 profile 比較快」）---
# 出處：operator 2026-09-28「只接受 prod-new profile ＋ harness bench，其他測試方法的結果皆不接受」；
# 把這句話留在樹上的那張卡是 `scripts/check/charters/exp-anchor-prodnew.yaml`（檔頭第一段）。
# 為什麼要 fail-closed：這一條的失敗方向若反過來（讀不到就放行），一支沒有 `profile` 欄位的舊產物
# 會直接等同 prod-new ⇒ 門檻形同虛設。白名單是**短**的，所以「讀不到」必須落在拒絕那一側。
QUOTABLE_PROFILES = ("prod-new",)

RC_OK, RC_NOT_QUOTABLE, RC_USAGE, RC_NOTHING = 0, 1, 2, 3

# `cell_contract.load_contract()` 的結果快取（R2 拆欄需要「這一格是不是 pp-less」；
# 逐 row 重讀測試卡會把掃整棵 Backup 變成 I/O 大戶）。
_CONTRACT_CACHE = None

VERDICTS = ("QUOTABLE", "UNSTABLE", "DIRTY", "THIN", "REFUSE")


def _median(vals):
    s = sorted(vals)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0


def _declared_prompt(prod, cell_name):
    """這一格宣告的 `-p`（`prompt`）：格 block 有就讀它，沒有的話查 `cell_contract` 的宣告表。

    只在 R2 拆欄那一條用（§3.3b）：need "pp-less" 這個事實，而**格的定義只有一份**——
    測試卡的 JSON block（`cell_contract.load_contract`）。讀不到 ⇒ None ⇒ 呼叫端不拆（fail-safe）。
    """
    cell = prod.get("cell")
    if isinstance(cell, dict):
        p = _as_int(cell.get("prompt"))
        if p is not None:
            return p
    if not cell_name or cell_name == "?":
        return None
    global _CONTRACT_CACHE
    if _CONTRACT_CACHE is None:
        try:
            import cell_contract as cc
            _CONTRACT_CACHE = cc.load_contract()
        except Exception:  # noqa: BLE001  閘門不因契約讀不到而改變判準方向
            _CONTRACT_CACHE = {}
    ctr = _CONTRACT_CACHE or {}
    spec = ctr.get("cell") if cell_name == "(default)" else (ctr.get("cells") or {}).get(cell_name)
    if isinstance(spec, dict):
        return _as_int(spec.get("prompt"))
    return None


def _series_stats(vals):
    """max/min 與樣本 CV%（n-1）——回 (spread, cv)。"""
    if len(vals) < 2 or min(vals) <= 0:
        return None, None
    spread = max(vals) / min(vals)
    n = len(vals)
    mean = sum(vals) / n
    var = sum((v - mean) ** 2 for v in vals) / (n - 1)
    cv = (var ** 0.5) / mean * 100.0 if mean else None
    return spread, cv


def _reported_cv(avg, sd):
    if avg in (None, 0) or sd is None:
        return None
    return float(sd) / float(avg) * 100.0


def _sub_field(container, field, default="?", raw_string_is_value=False):
    """從產物裡挖一個欄位，**舊 schema 也要讀得懂**。

    `attribution`／`cell` 在舊產物裡有時直接是一個字串（不是 mapping）。掃整棵 `Backup`
    樹時一定會踩到（`--glob 'Backup/**/*.json'` 就在這裡 AttributeError 整支掛掉），
    而「閘門自己死掉」比「判不了」嚴重得多：前者會讓呼叫它的 build／pipeline 一起掛。
    因此：mapping 就取欄位；字串就（在該欄位本來就是自由字串時）當成值本身；其餘當「?」。
    「?」不是 none ⇒ R4 不放行，這是刻意選的失敗方向。
    """
    if isinstance(container, dict):
        v = container.get(field)
        return v if v not in (None, "") else default
    if isinstance(container, str) and container and raw_string_is_value:
        return container
    return default


def _arm_env(prod):
    """把一支臂的 env 併起來看：`env` ＋ `extra_env` ＋ `tag` 的 `K=V` 尾串。

    三個地方都要看，因為三種產物各寫一處：harness 的 row 把覆寫放在 `extra_env`（`tag` 也重寫一次）、
    舊產物只有 `env`、而 `tag` 是「這一輪到底開了什麼」最不容易被漏掉的那一份。
    值為 `0`／空字串 ⇒ 當成沒開（`CGC_MISS_MASK_DBG=0` 不是武裝）。
    """
    out = {}
    for key in ("env", "extra_env"):
        d = prod.get(key)
        if isinstance(d, dict):
            for k, v in d.items():
                if v not in (None, "", "0"):
                    out[k] = v
    tag = prod.get("tag")
    if isinstance(tag, str):
        for part in tag.split(":"):
            for tok in part.split(";"):
                if "=" in tok:
                    k, _, v = tok.partition("=")
                    k, v = k.strip(), v.strip()
                    if k and v and v != "0":
                        out[k] = v
    return out


def arm_instruments(prod):
    """回 {旗標: 出處}：這支臂身上有哪些「不得引用吞吐」的量具真的開著（R5）。"""
    env = _arm_env(prod)
    return {k: why for k, why in sorted(THROUGHPUT_VOID_INSTRUMENTS.items()) if k in env}


def unverified_output_arms(prod):
    """回 {旗標: 出處}：這支臂的**輸出**有沒有見證（R6）。

    `=0`／沒開不進這條（與 R5 同一條邊界）。這一條問的不是「量到了什麼」，是
    「量到的那個東西還是不是交付目標的那個函數」。
    """
    env = _arm_env(prod)
    return {k: why for k, why in sorted(UNVERIFIED_OUTPUT_ARMS.items()) if k in env}


# ── R8：格／行／聚合單元 ────────────────────────────────────────────────────────────────
#
# 認證表的入口是**這一格的權威 row**（`llama-bench` 一次 launch 出兩行：prefill 行與 decode 行），
# 不是「同一支 harness 跑出來、但被自訂聚合過的中間值」。operator 2026-09-30：入口一致
# （prod-new ＋ harness bench）**不等於**可引用 —— 還要同格／同行／同聚合單元／同窗、且臂上無
# R5／R6。這一節把「同格／同行／同聚合」寫成機器（R5–R7 各管臂身分、輸出見證、口徑）。

ROUND_LEVEL_KEYS = ("n_rounds", "decode_tps_median", "decode_tps_min", "decode_tps_max",
                    "rounds", "rounds_median_ts")


def _as_int(v):
    """寬鬆讀數（`"512"`、`512.0`、`512` 都是 512）；讀不出來回 None ⇒ 呼叫端跳過該項比較。"""
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return int(v)
    if isinstance(v, str):
        s = v.strip()
        if s.lstrip("-").isdigit():
            return int(s)
    return None


def round_level_reason(prod):
    """R8c：這份產物是不是「自訂輪數」的輪級聚合（不是權威 row）。回 None ⇒ 不是。"""
    if not isinstance(prod, dict):
        return None
    rows = prod.get("rows")
    if isinstance(rows, list) and any(
            isinstance(r, dict) and r.get("avg_ts") is not None for r in rows):
        return None          # 有權威 row ⇒ 交給 8a／8b 問格與行
    hits = [k for k in ROUND_LEVEL_KEYS if k in prod]
    if not hits:
        return None
    n = prod.get("n_rounds")
    return ("聚合單元：輪級聚合（自訂 %s%s；沒有權威格、沒有逐 rep 向量）⇒ 不可入表。"
            "平台值只認權威 row：`python3 scripts/check/cell_contract.py --cell <name>` 印的就是"
            "那一條 `harness bench` 命令"
            % ("／".join(hits), ("，n_rounds=%s" % n) if n is not None else ""))


def cell_shape_reasons(prod, row, cell_name):
    """R8a／R8b：這一場有沒有權威格，以及這一行是不是那一格宣告的行。"""
    out = []
    if not cell_name or cell_name == "?":
        out.append("權威格：讀不到格名（`cell.named_cell`／舊 schema 字串皆無）"
                   "⇒ 這一場不在任何一格上")
    cblock = prod.get("contract")
    if isinstance(cblock, dict) and cblock.get("ok") is False:
        out.append("權威格：contract.ok=false（起跑前的格契約就不過：%s）"
                   % ("；".join(str(x) for x in (cblock.get("mismatches") or [])[:3])
                      or "見產物"))
    cell = prod.get("cell")
    if not isinstance(cell, dict):
        return out           # 舊 schema 只有格名：沒有形狀可比，8a 已經問過「有沒有格」
    prompt, gen = _as_int(cell.get("prompt")), _as_int(cell.get("gen"))
    depth, reps = _as_int(cell.get("depths")), _as_int(cell.get("reps"))
    ws = _as_int(cell.get("warm_skip")) or 0
    applied = prod.get("warm_skip_applied")
    np_, ng = _as_int(row.get("n_prompt")), _as_int(row.get("n_gen"))
    nd = _as_int(row.get("n_depth"))
    if ws and applied is False:
        out.append("行≠格：格宣告 warm_skip=%s，但產物自報 `warm_skip_applied=false`"
                   "⇒ 這一行不是這一格宣告的行（那個槓桿是 no-op）" % ws)
    if depth is not None and nd is not None and nd != depth:
        out.append("行≠格：n_depth=%s ≠ 格宣告 %s" % (nd, depth))
    if prompt is not None and gen is not None and np_ is not None and ng is not None:
        exp_gen = gen - ws if (ws and applied) else gen
        ok_prefill = (np_ == prompt and ng == 0)
        ok_decode = (np_ == 0 and ng == exp_gen)
        if not (ok_prefill or ok_decode):
            out.append("行≠格：這一行是 (n_prompt=%s, n_gen=%s)，而格宣告只有兩條行："
                       "prefill (%s, 0) 與 decode (0, %s)"
                       % (np_, ng, prompt, exp_gen))
    samples = row.get("samples_ts")
    if reps is not None and reps > 1 and not prod.get("rep_split") \
            and isinstance(samples, list) and len(samples) != reps:
        out.append("聚合單元：格宣告 reps=%d，這一行只有 %d 個逐 rep 樣本"
                   % (reps, len(samples)))
    return out


def arm_profile(prod):
    """這一支臂的 profile（R7）。

    先讀產物自己的 `profile` 欄位（harness 每支臂都寫）；讀不到才退回 `tag` 的
    `PROFILE:...` 冒號前段（舊產物只有 tag，例如 `prod25:!CGC_PREFILL_STREAM=1`）。
    **兩條都讀不到 ⇒ `None`**，呼叫端把它當「不在認可口徑上」處理 —— 不猜成 prod-new。
    """
    v = prod.get("profile")
    if isinstance(v, str) and v.strip():
        return v.strip()
    tag = prod.get("tag")
    if isinstance(tag, str) and tag.strip():
        head = tag.split(":", 1)[0].strip()
        if head:
            return head
    return None


def judge(prod, row, reference=None, band_pct=BAND_PCT):
    """判一個 row。回 (verdict, reasons, metrics)。"""
    samples = list(row.get("samples_ts") or [])
    reps = len(samples)
    avg = row.get("avg_ts")
    sd = row.get("stddev_ts")
    attrib = _sub_field(prod.get("attribution"), "verdict", raw_string_is_value=True)
    thermal = _sub_field(prod.get("attribution"), "thermal_worst")
    cell = _sub_field(prod.get("cell"), "named_cell", raw_string_is_value=True)
    profile = arm_profile(prod)

    all_spread, all_cv = _series_stats(samples)
    kept = samples[1:] if len(samples) > 1 else []
    kept_spread, kept_cv = _series_stats(kept)
    rep_cv = _reported_cv(avg, sd)
    m = dict(reps=reps, avg=avg, sd=sd, rep_cv=rep_cv, all_spread=all_spread,
             profile=profile,
             all_cv=all_cv, kept_spread=kept_spread, kept_cv=kept_cv, attrib=attrib,
             thermal=thermal, cell=cell, samples=samples, platform_ts=None,
             ref_delta=None, cold_penalty=None)
    if kept:
        m["platform_ts"] = sum(kept) / len(kept)
        if avg:
            m["cold_penalty"] = (m["platform_ts"] - avg) / avg * 100.0

    # ── R2 的拆欄例外（2026-10-01，§3.3b）：見檔頭 ──
    split = bool(kept) and samples[0] < _median(kept) and _declared_prompt(prod, cell) == 0
    m["regime_split"] = split
    m["quoted_ts"] = avg
    if split:
        m["cold_ts"] = samples[0]
        m["steady_ts"] = list(kept)
        m["quoted_ts"] = m["platform_ts"]      # steady 平均（drop rep 1，與產物 platform_ts 同口徑）
        if all_spread is not None and all_spread > SPREAD_LIMIT:
            m["cold_split"] = ("全 rep max/min=%.3f 來自 cold rep（%.3f）⇒ 已拆欄，散度只看 steady"
                               % (all_spread, samples[0]))

    if prod.get("refused_preflight") is True:
        return "REFUSE", ["refused_preflight=true（拒跑：這一輪沒有量到 t/s）"], m
    if avg is None:
        return "REFUSE", ["這個 row 沒有 avg_ts"], m
    if not samples:
        return "REFUSE", ["讀不出逐 rep 樣本（沒有 samples_ts）—— 沒有證據說它穩"], m
    if reps < MIN_REPS:
        return "THIN", ["reps=%d<%d（沒有中央趨勢可談；用 --reps %d 以上）"
                        % (reps, MIN_REPS, MIN_REPS)], m
    if all_spread is None or kept_spread is None:
        return "REFUSE", ["逐 rep 樣本讀得出但無法算離散（樣本 ≤1）"], m

    # R0：產物內部自洽（回報的 stddev 必須與 samples_ts 反算值相符）
    if rep_cv is not None and all_cv is not None and abs(rep_cv - all_cv) > INTEGRITY_TOL_PCT:
        return "REFUSE", ["產物內部不自洽（stddev 反算 CV %.1f%% vs samples CV %.1f%%）"
                          % (rep_cv, all_cv)], m

    reasons = []
    if all_spread > SPREAD_LIMIT and not split:
        reasons.append("全 rep max/min=%.3f>%.2f" % (all_spread, SPREAD_LIMIT))
    if kept_spread > SPREAD_LIMIT:
        reasons.append("kept rep max/min=%.3f>%.2f" % (kept_spread, SPREAD_LIMIT))
    if attrib not in CLEAN_ATTRIBUTION:
        reasons.append("attribution=%s≠none" % attrib)
    if reference:
        m["ref_delta"] = (float(avg) - float(reference)) / float(reference) * 100.0
        if abs(m["ref_delta"]) > band_pct:
            reasons.append("Δ vs 參考=%.1f%%（帶 ±%.0f%%）" % (m["ref_delta"], band_pct))

    # R5 臂身分（見檔頭）：這一輪量到的還是不是交付臂
    arms = arm_instruments(prod)
    m["arm_instruments"] = sorted(arms)
    arm_reasons = []
    # R7 口徑（見檔頭）：這一場是不是在認可口徑上跑的（fail-closed：讀不到也擋）
    if profile not in QUOTABLE_PROFILES:
        arm_reasons.append(
            "口徑：profile=%s ∉ %s（operator 2026-09-28：只接受 prod-new ＋ harness bench）"
            % (profile or "?", list(QUOTABLE_PROFILES)))
    arm_reasons += ["臂身分：%s=1 ⇒ %s" % (k, why) for k, why in arms.items()]
    # R6 輸出見證：臂的輸出有沒有被驗過（單次提交臂 ⇒ 目標函數不同）
    unver = unverified_output_arms(prod)
    m["unverified_output_arms"] = sorted(unver)
    if unver:
        arm_reasons += ["輸出未驗：%s ⇒ %s" % (k, why) for k, why in unver.items()]
    # R8 格／行／聚合單元（見檔頭）：認證表的入口是這一格的權威 row
    r8 = cell_shape_reasons(prod, row, cell)
    m["row_cell_reasons"] = r8
    arm_reasons += r8
    reasons += arm_reasons

    if not reasons:
        return "QUOTABLE", [], m
    if arm_reasons:
        # 與窗口同級（DIRTY）：它不是「這一場不夠穩」，是**這一場不是那個臂**。
        return "DIRTY", reasons, m
    window_only = all(("attribution=" in r) for r in reasons)
    return ("DIRTY" if window_only else "UNSTABLE"), reasons, m


def _record_rows(prod):
    """三層包裝（`{plan, records:[{rows:[…]}]}`）的走訪（2026-10-01 硬化；見檔頭 R8）。

    回 (merged_prod, row)——`merged` 以**檔級欄位為底、record 覆蓋**：record 帶著這一支臂的
    `tag`／`env`／`cell`，檔級帶著 `profile`／`attribution`／`warm_skip_applied`。
    兩邊都不改一個字（這裡只是把判準看得見的範圍補齊），row 一樣逐條過 R1–R8。
    """
    recs = prod.get("records")
    if not isinstance(recs, list):
        return
    ctx = {k: v for k, v in prod.items() if k != "records"}
    for rec in recs:
        if not isinstance(rec, dict):
            continue
        rows = rec.get("rows")
        if not isinstance(rows, list):
            continue
        merged = dict(ctx)
        merged.update(rec)
        for row in rows:
            if isinstance(row, dict) and row.get("avg_ts") is not None:
                yield merged, row


def iter_rows(paths):
    for p in paths:
        try:
            with open(p, encoding="utf-8") as fh:
                doc = json.load(fh)
        except Exception as exc:  # noqa: BLE001
            yield p, None, None, "REFUSE", ["讀不出產物：%s" % exc]
            continue
        for prod in (doc if isinstance(doc, list) else [doc]):
            if not isinstance(prod, dict):
                continue
            rows = prod.get("rows")
            saw = False
            if isinstance(rows, list):
                for row in rows:
                    if isinstance(row, dict) and row.get("avg_ts") is not None:
                        saw = True
                        yield p, prod, row, None, None
            if not saw:
                # 三層包裝：row 不在最上層（檔頭 R8 的硬化）。
                for merged, row in _record_rows(prod):
                    saw = True
                    yield p, merged, row, None, None
            # R8c：輪級聚合不得**隱形** —— 沒有權威 row 的產物也要留下一條 REFUSE 記錄
            # （「靜默跳過」與「判可引用」在輸出裡必須分得開；見檔頭的 R8）。
            if not saw:
                rl = round_level_reason(prod)
                if rl:
                    yield p, prod, None, "REFUSE", [rl]


def scan(paths, reference=None, band_pct=BAND_PCT):
    out = []
    for p, prod, row, pre_v, pre_why in iter_rows(paths):
        if pre_v:
            out.append(dict(file=p, cell="?", shape="?", verdict=pre_v, reasons=pre_why, metrics={}))
            continue
        v, why, m = judge(prod, row, reference, band_pct)
        shape = "p%s/n%s/d%s" % (row.get("n_prompt"), row.get("n_gen"), row.get("n_depth"))
        out.append(dict(file=p, cell=m["cell"], shape=shape, verdict=v, reasons=why, metrics=m))
    return out


def report(records, reference, band_pct):
    print("引用閘門 quote_gate：以**逐 rep 離散**（不是 attribution 標籤）決定可不可引用")
    print("  R1 reps>=%d ｜ R2 全 rep max/min<=%.2f ｜ R3 kept rep max/min<=%.2f ｜ R4 attribution∈%s"
          " ｜ R5 臂上無不得引用吞吐的量具（%d 條） ｜ R6 臂的輸出有見證（%d 條）"
          " ｜ R7 profile∈%s ｜ R8 同格／同行／同聚合單元（fail-closed）%s"
          % (MIN_REPS, SPREAD_LIMIT, SPREAD_LIMIT, list(CLEAN_ATTRIBUTION),
             len(THROUGHPUT_VOID_INSTRUMENTS), len(UNVERIFIED_OUTPUT_ARMS),
             list(QUOTABLE_PROFILES),
             (" ｜ |Δ|<=%.0f%%" % band_pct) if reference else ""))
    print()
    print("  %-24s %-9s %-11s %-9s %-8s %-8s %-9s %s"
          % ("file", "cell", "shape", "t/s", "all", "kept", "attrib", "verdict"))
    for r in records:
        m = r["metrics"] or {}
        fmt = (lambda v, f: ("%" + f) % v) if False else None
        print("  %-24s %-9s %-11s %-9s %-8s %-8s %-9s %s"
              % (os.path.basename(r["file"])[:24], r["cell"], r["shape"],
                 ("%.3f" % m["avg"]) if m.get("avg") else "—",
                 ("%.3f" % m["all_spread"]) if m.get("all_spread") else "—",
                 ("%.3f" % m["kept_spread"]) if m.get("kept_spread") else "—",
                 m.get("attrib") or "—", r["verdict"]))
    n_q = sum(1 for r in records if r["verdict"] == "QUOTABLE")
    print()
    print("VERDICT: %d/%d 可引用" % (n_q, len(records)))
    for r in records:
        if r["verdict"] != "QUOTABLE":
            print("  · %s %s → %s：%s" % (os.path.basename(r["file"]), r["shape"],
                                          r["verdict"], "；".join(r["reasons"])))
    for r in records:
        m = r["metrics"] or {}
        if m.get("samples"):
            print("  · %s %s 逐 rep %s  kept %s  platform_ts %s  cold-rep 影響 %s%s"
                  % (os.path.basename(r["file"]), r["shape"],
                     [round(v, 2) for v in m["samples"]],
                     ([round(v, 2) for v in m["samples"][1:]] if m["samples"] else "—"),
                     ("%.3f" % m["platform_ts"]) if m.get("platform_ts") else "—",
                     ("%+.1f%%" % m["cold_penalty"]) if m.get("cold_penalty") is not None else "—",
                     ("  ← 已拆欄（§3.3b）：引用值 %.3f（steady；cold %.2f 留診斷）"
                      % (m["quoted_ts"], m["cold_ts"])) if m.get("regime_split") else ""))
    return n_q, len(records)


def _legacy(prod, cell=None, attrib=None):
    """把 fixture 改寫成舊 schema（整塊直接是字串）——用來釘住上面的相容路徑。"""
    if cell is not None:
        prod["cell"] = cell
    if attrib is not None:
        prod["attribution"] = attrib
    return prod


def _mk(avg, sd, samples, attrib="none", thermal="NOMINAL", refused=False,
        cell="delivery", prompt=0, gen=64, dep=512, extra_env=None,
        profile="prod-new", tag=None, cell_block=None, ws_applied=None):
    prod = {"rows": [{"n_prompt": prompt, "n_gen": gen, "n_depth": dep, "avg_ts": avg,
                       "stddev_ts": sd, "samples_ts": samples}],
            "attribution": {"verdict": attrib, "thermal_worst": thermal},
            "refused_preflight": refused}
    # R8b 才需要「形狀可比」的 cell block（`cell_block`）；否則只寫格名（舊 fixture 不變）。
    if cell_block is None:
        prod["cell"] = {"named_cell": cell}
    else:
        c = dict(cell_block)
        if cell is not None and not c.get("named_cell"):
            c["named_cell"] = cell
        prod["cell"] = c
    if ws_applied is not None:
        prod["warm_skip_applied"] = ws_applied
    if extra_env:
        prod["extra_env"] = dict(extra_env)
    # R7：`profile` 是預設值（真產物每一支臂都有），要測「讀不到」就顯式傳 None
    if profile is not None:
        prod["profile"] = profile
    if tag is not None:
        prod["tag"] = tag
    return prod


def selftest():
    """fixture 全部取自真產物（今天的 5 場 ＋ 歷史 4 支）。"""
    cases = [
        # 已認證錨點：Delivery cell, -r 3, samples [11.65, 11.38, 12.07] -> 1.061
        ("錨點 11.703（已認證）", _mk(11.703, 0.348, [11.65, 11.38, 12.07]), "QUOTABLE"),
        # 今天唯一 attribution=none 的那一場：spread 2.69、kept 2.69
        ("今日 none 但離散", _mk(8.257, 3.788, [10.45, 10.44, 3.88]), "UNSTABLE"),
        # 06:43：離散很好但換頁
        ("06:43 穩但 swap", _mk(11.583, 0.138, [11.74, 11.48, 11.53], attrib="swap"), "DIRTY"),
        # 02:47：both/HEAVY 且離散大
        ("02:47 HEAVY 離散", _mk(5.821, 1.505, [7.40, 5.66, 4.40], attrib="both",
                                thermal="HEAVY"), "UNSTABLE"),
        # 今天的 anchor5：全 rep 1.11 剛好越線、kept 1.03。
        # ⚠ 2026-10-01（§3.3b 拆欄）：第 1 個 rep 低於 steady 中位數 ⇒ 已拆欄、散度只看 kept
        #   ⇒ 只剩 attribution=swap 那條 ⇒ **DIRTY**（不再是 UNSTABLE）。兩個判詞都不可引用，
        #   差的是「為什麼」：這個數字死在窗口，不是死在統計。
        ("anchor5 全 rep 越線（已拆欄）", _mk(10.692, 0.572, [10.05, 10.86, 11.16], attrib="swap"), "DIRTY"),
        # 今天的 anchor4：全 rep 1.21、kept 1.09 ⇒ 同上：拆欄後只剩窗口那條。
        ("anchor4 kept 過但全 rep 不過（已拆欄）", _mk(10.644, 1.021, [9.62, 10.66, 11.65], attrib="swap"), "DIRTY"),
        ("reps=1（不是平台值）", _mk(17.669, 0.0, [17.669], attrib="none"), "THIN"),
        ("無 samples_ts", _mk(11.703, 0.348, [], attrib="none"), "REFUSE"),
        ("拒跑", _mk(0.0, 0.0, [], refused=True), "REFUSE"),
        # 內部不自洽：samples 說 1.00 但回報 stddev 讓 CV=20%
        ("內部不自洽", _mk(11.703, 2.34, [11.70, 11.70, 11.71]), "REFUSE"),
        ("偏離參考", _mk(9.00, 0.02, [9.02, 8.98, 9.00]), "UNSTABLE"),
        # 舊 schema：`cell` 直接是名字、`attribution` 直接是判詞（整棵 Backup 掃得到的形狀）
        ("舊 schema：cell 是字串", _legacy(_mk(11.703, 0.348, [11.65, 11.38, 12.07]),
                                          cell="delivery"), "QUOTABLE"),
        ("舊 schema：attrib 是字串", _legacy(_mk(11.583, 0.138, [11.74, 11.48, 11.53]),
                                            attrib="swap"), "DIRTY"),
        # R5（2026-09-29）：真產物的形狀 —— 27.338 t/s／逐 rep 1.005／none，四條全過，
        # 只有臂身分擋得住（`spac_sweep_2026-09-29/k_sweep_T4_K4.json`）。
        ("R5 診斷臂（四條全過）",
         _mk(27.338, 0.083, [27.3815, 27.242, 27.3894],
             extra_env={"CGC_MISS_MASK_DBG": "1", "CGC_MISS_MASK_COST": "1"}), "DIRTY"),
        # R5 只在**真的開著**時擋：`=0`／沒開一律不進這條（否則等於所有產物永久紅）。
        ("R5 未武裝不擋",
         _mk(9.377, 0.475, [8.83, 9.59, 9.70],
             extra_env={"CGC_MISS_MASK_DBG": "0", "CGC_GPU_TIMING": "1"}), "QUOTABLE"),
        # R6（2026-09-30）：真產物的形狀 —— 旗標洗掉之後的那一場（`k4_noflags`，26.203／逐 rep 1.010）。
        # **假設它窗口乾淨**（這裡把 attribution 設成 none）⇒ R1–R5 全過，只有 R6 擋得住。
        ("R6 單次提交（假設窗口乾淨）",
         _mk(26.203, 0.132, [26.0558, 26.3118, 26.2412],
             extra_env={"CGC_SEG_BATCH": "1"}), "DIRTY"),
        ("R6 誠實臂不擋",
         _mk(9.377, 0.475, [8.83, 9.59, 9.70],
             extra_env={"CGC_SEG_BATCH": "0"}), "QUOTABLE"),
        # R6 補收（2026-09-30）：真產物的形狀 —— `stepbudget_2026-09-29/gap_sweep.json` 的
        # submit-ahead 臂（14.900／逐 rep 1.022）。**假設窗口乾淨** ⇒ R1–R5 全過，只有 R6 擋得住。
        ("R6 submit-ahead（假設窗口乾淨）",
         _mk(14.900, 0.1701, [15.0899, 14.8425, 14.7682],
             extra_env={"CGC_SUBMIT_AHEAD": "1"}), "DIRTY"),
        ("R6 submit-ahead 未開不擋（同一組數 ⇒ 只剩那支旗標是差的）",
         _mk(14.900, 0.1701, [15.0899, 14.8425, 14.7682],
             extra_env={"CGC_SUBMIT_AHEAD": "0"}), "QUOTABLE"),
        # R5 補收（2026-09-30）：真產物的形狀 —— 交付 cell、逐 rep 1.027、attribution none、
        # 13.750 t/s（`fill_split_delivery_2026-09-30/ab.json` 的 NOFILL 臂）。
        # 它是「完全不讀位元組」的診斷臂 ⇒ 那 13.75 是終點上界，不是交付讀數。
        ("R5 NOFILL 診斷臂（窗口乾淨、散度極小）",
         _mk(13.750, 0.190, [13.7459, 13.9375, 13.568], cell="delivery",
             extra_env={"CGC_EB_TIMER": "1", "CGC_EB_NOFILL": "1"}), "DIRTY"),
        ("R5 NOFILL 未開不擋",
         _mk(10.923, 0.512, [10.34, 11.34, 11.08], cell="delivery",
             extra_env={"CGC_EB_TIMER": "1"}), "QUOTABLE"),
        # R7（2026-09-30）：口徑。真形狀＝`anchor_repro_2026-09-28/r3_5.json` 的 arm
        # （profile prod25、tag `prod25:!CGC_PREFILL_STREAM=1`、cell=delivery、spec draft-mtp）。
        # 刻意給它**穩到不能再穩**的逐 rep（1.002）⇒ R1–R6 全過，只有 R7 擋得住。
        ("R7 prod25 平穩臂（R1–R6 全過）",
         _mk(12.954, 0.026, [12.954, 12.94, 12.967], cell="delivery",
             profile="prod25", tag="prod25:!CGC_PREFILL_STREAM=1"), "DIRTY"),
        ("R7 沒有 profile 欄位（只有 tag）",
         _mk(12.954, 0.026, [12.954, 12.94, 12.967], cell="delivery",
             profile=None, tag="prod25:!CGC_PREFILL_STREAM=1"), "DIRTY"),
        ("R7 profile 完全讀不到（fail-closed）",
         _mk(11.700, 0.010, [11.70, 11.70, 11.70], cell="delivery",
             profile=None), "DIRTY"),
        ("R7 prod-new 是認可口徑", _mk(10.923, 0.512, [10.34, 11.34, 11.08],
                                     cell="delivery", profile="prod-new"), "QUOTABLE"),
        # R8（2026-09-30）：格／行／聚合單元。「入口一致」（prod-new＋harness bench）不等於
        # 「可引用」。下面四支的 R1–R7 全過（窗口乾淨、profile prod-new、臂上乾淨），只有 R8 擋。
        # 8a：真產物的形狀 —— 沒有權威格（`Backup/mm_dbg/res.json` 那一族：p0/n64/d512、prod-new）。
        ("R8a 沒有權威格", _mk(11.562, 0.010, [11.56, 11.57, 11.56], cell=None), "DIRTY"),
        # 8b：ABBA 的輪級形狀（n≈98）被 repack 成 row —— 格宣告 (prompt 0, gen 128, ws 64)
        # ⇒ decode 行必須是 (0, 64)；98 是另一種形狀（§56 跨格不可比）。
        ("R8b 行≠格（n_gen=98）",
         _mk(11.700, 0.010, [11.70, 11.70, 11.70], cell="delivery", prompt=0, gen=98, dep=512,
             cell_block={"prompt": 0, "gen": 128, "depths": 512, "reps": 3, "warm_skip": 64},
             ws_applied=True), "DIRTY"),
        # 8b：「B1 那一場」的真形狀 —— 格宣告 ws=256、產物自報 `warm_skip_applied=false`
        # ⇒ 那一行不是這一格宣告的行（槓桿 no-op）；口徑與窗全過也一樣。
        ("R8b warm_skip 未套用",
         _mk(10.2339, 1.205, [8.8446, 10.8629, 10.9942], cell="delivery-ws256", prompt=0, gen=128, dep=512,
             cell_block={"prompt": 0, "gen": 128, "depths": 512, "reps": 3, "warm_skip": 256},
             ws_applied=False), "DIRTY"),
        # 回歸釘子：同一組格、warm_skip 已套用 ⇒ decode 行是 (0, 64) ⇒ 放行（R8b 不擋真行）。
        ("R8b 真行（ws 已套用）",
         _mk(11.703, 0.348, [11.65, 11.38, 12.07], cell="(default)", prompt=0, gen=64, dep=512,
             cell_block={"prompt": 2048, "gen": 128, "depths": 512, "reps": 3,
                         "warm_skip": 64}, ws_applied=True), "QUOTABLE"),
    ]
    ok = 0
    for name, prod, want in cases:
        row = prod["rows"][0]
        ref = 11.703 if name == "偏離參考" else None
        v, why, _m = judge(prod, row, ref, BAND_PCT)
        good = (v == want)
        ok += 1 if good else 0
        print("  %-26s → %-9s 期待 %-9s %s %s"
              % (name, v, want, "✓" if good else "✗", ("（%s）" % "；".join(why)) if why else ""))
    # ── 2026-10-01（§3.3b）：pp-less 的 cold/steady 拆欄 ──────────────────────────────
    # 真產物形狀：`Backup/delivery_anchor_rerun_2026-09-30/launch1.json`（逐 rep [10.0305, 11.1652,
    # 11.1215]、全 rep 1.113、kept 1.004）—— 舊判 UNSTABLE，拆欄後可引用、cold 留診斷。
    _p = _mk(10.77243, 0.642862, [10.0305, 11.1652, 11.1215], cell="delivery")
    _v, _why, _m = judge(_p, _p["rows"][0])
    _split_ok = (_v == "QUOTABLE" and _m.get("regime_split") is True
                 and abs((_m.get("quoted_ts") or 0) - 11.14335) < 0.01
                 and abs((_m.get("cold_ts") or 0) - 10.0305) < 0.001)
    ok += 1 if _split_ok else 0
    print("  %-26s → %-9s 期待 %-9s %s （cold=%.2f 留診斷、引用值 %.3f=steady）"
          % ("launch1 拆欄", _v, "QUOTABLE", "✓" if _split_ok else "✗",
             _m.get("cold_ts") or 0, _m.get("quoted_ts") or 0))
    # 反向：冷的是**最後**一顆 rep（第 1 顆最高）⇒ 那不是格子的結構（是真的不穩）⇒ 不拆、照 R2 擋。
    _p2 = _mk(8.257, 3.788, [10.45, 10.44, 3.88], cell="delivery")
    _v2, _r2, _m2 = judge(_p2, _p2["rows"][0])
    _anti_ok = (_v2 == "UNSTABLE" and not _m2.get("regime_split"))
    ok += 1 if _anti_ok else 0
    print("  %-26s → %-9s 期待 %-9s %s" % ("冷的是最後一顆 rep（不拆）", _v2, "UNSTABLE",
                                            "✓" if _anti_ok else "✗"))

    # scan 這一層也要擋得住不是 llama-bench 的東西：整棵樹裡混著字串、空 row、缺欄位。
    # （實測 `--glob 'Backup/**/*.json'` 曾讓閘門自己 AttributeError 掛掉 ⇒ 這條是回歸釘子。）
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        junk = os.path.join(td, "junk.json")
        with open(junk, "w", encoding="utf-8") as fh:
            json.dump(["not-a-record", 42, {"rows": [{"avg_ts": None}]},
                       {"cell": "delivery", "attribution": {"verdict": "none"},
                        "profile": "prod-new", "tag": "prod-new",
                        "rows": [{"n_prompt": 0, "n_gen": 64, "n_depth": 512, "avg_ts": 11.703,
                                  "stddev_ts": 0.348, "samples_ts": [11.65, 11.38, 12.07]}]}], fh)
        # R8c：輪級聚合不得**隱形**（`decode_bench`／ABBA 的產物形狀：沒有 rows，只有輪序列）
        rounds = os.path.join(td, "rounds.json")
        with open(rounds, "w", encoding="utf-8") as fh:
            json.dump({"tag": "prod-new", "n_rounds": 98, "decode_tps_median": 12.97,
                       "rounds": [{"round": 1, "decode_tps": 12.97}],
                       "env": {"CGC_RHO_PROBE": "1"}}, fh)
        recs = scan([junk, rounds])
        good = (len(recs) == 2 and recs[0]["verdict"] == "QUOTABLE"
                and recs[1]["verdict"] == "REFUSE"
                and "聚合單元" in "；".join(recs[1]["reasons"]))
        ok += 1 if good else 0
        print("  %-26s → %-9s 期待 %-9s %s （混雜產物不該讓閘門掛掉；輪級聚合不得隱形）"
              % ("scan：混雜／舊 schema＋輪級", (recs[0]["verdict"] if recs else "—"), "QUOTABLE",
                 "✓" if good else "✗"))
        # 2026-10-01 硬化（口徑統一）：三層包裝 `{plan, records:[{rows:[…]}]}` 也走訪。
        # 兩張 fixture：① 缺逐 rep 樣本 ⇒ REFUSE（以前是**靜默跳過**）；② 完整的權威 row ⇒ QUOTABLE
        # （證明包裝不是「免死金牌」：它一樣逐條過 R1–R8）。
        wrap_na = os.path.join(td, "wrapper_na.json")
        with open(wrap_na, "w", encoding="utf-8") as fh:
            json.dump({"plan": {"arms": ["prod-new"]},
                       "cell": {"named_cell": "delivery"},
                       "records": [{"arm": "prod-new", "tag": "prod-new",
                                    "rows": [{"n_prompt": 0, "n_gen": 64, "n_depth": 512,
                                              "avg_ts": 20.73, "stddev_ts": 0.0,
                                              "samples_ts": None}]}]}, fh)
        wrap_ok = os.path.join(td, "wrapper_ok.json")
        with open(wrap_ok, "w", encoding="utf-8") as fh:
            json.dump({"plan": {"arms": ["prod-new"]},
                       "cell": {"prompt": 0, "gen": 128, "depths": 512, "reps": 3,
                                "warm_skip": 64, "named_cell": "delivery"},
                       "attribution": {"verdict": "none", "thermal_worst": "NOMINAL"},
                       "warm_skip_applied": True,
                       "profile": "prod-new",
                       "records": [{"arm": "prod-new", "tag": "prod-new",
                                    "rows": [{"n_prompt": 0, "n_gen": 64, "n_depth": 512,
                                              "avg_ts": 11.703, "stddev_ts": 0.348,
                                              "samples_ts": [11.65, 11.38, 12.07]}]}]}, fh)
        wrecs = scan([wrap_na, wrap_ok])
        good2 = (len(wrecs) == 2 and wrecs[0]["verdict"] == "REFUSE"
                 and "逐 rep" in "；".join(wrecs[0]["reasons"])
                 and wrecs[1]["verdict"] == "QUOTABLE")
        ok += 1 if good2 else 0
        print("  %-26s → %-9s 期待 %-9s %s （缺逐 rep ⇒ REFUSE、完整 row ⇒ QUOTABLE；不得靜默跳過）"
              % ("scan：三層包裝 records[].rows",
                 "/".join(r["verdict"] for r in wrecs) if wrecs else "—", "REFUSE/QUOTABLE",
                 "✓" if good2 else "✗"))
    cases = cases + ["scan-junk", "scan-records-wrapper", "split-launch1", "split-antipattern"]
    print("SELFTEST %s (%d/%d)" % ("PASS" if ok == len(cases) else "FAIL", ok, len(cases)))
    return RC_OK if ok == len(cases) else RC_NOT_QUOTABLE


def main(argv=None):
    ap = argparse.ArgumentParser(description="引用閘門：逐 rep 離散（非 attribution 標籤）")
    ap.add_argument("cmd", nargs="?", default="check", choices=["check"])
    ap.add_argument("paths", nargs="*")
    ap.add_argument("--glob", dest="glob_pat", default=None)
    ap.add_argument("--reference", type=float, default=None)
    ap.add_argument("--band-pct", type=float, default=BAND_PCT)
    ap.add_argument("--json", dest="json_out", default=None)
    ap.add_argument("--selftest", action="store_true")
    # 位置參數（paths）與選項可以交錯：`nargs="*"` 的 positionals 在 argparse 裡
    # 一旦遇到選項就停止收集，所以 `--json X a.json b.json` 這種常見寫法會失敗。
    if hasattr(ap, "parse_intermixed_args"):
        args = ap.parse_intermixed_args(argv)
    else:  # pragma: no cover - py3.6 以前
        args = ap.parse_args(argv)

    if args.selftest:
        return selftest()

    paths = list(args.paths)
    if args.glob_pat:
        paths += sorted(_glob.glob(args.glob_pat, recursive=True))
    paths = [p for p in paths if os.path.isfile(p)]
    if not paths:
        print("沒有可判的產物（用法見 --help）", file=sys.stderr)
        return RC_NOTHING

    records = scan(paths, args.reference, args.band_pct)
    if not records:
        print("這些產物裡沒有任何 llama-bench row 可判", file=sys.stderr)
        return RC_NOTHING
    n_q, n_all = report(records, args.reference, args.band_pct)
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump({"min_reps": MIN_REPS, "spread_limit": SPREAD_LIMIT,
                       "clean_attribution": list(CLEAN_ATTRIBUTION),
                       "reference": args.reference, "n_quotable": n_q, "n_rows": n_all,
                       "records": records}, fh, ensure_ascii=False, indent=1)
        print("wrote %s" % args.json_out)
    return RC_OK if n_q == n_all else RC_NOT_QUOTABLE


if __name__ == "__main__":
    sys.exit(main())
