#!/usr/bin/env python3
"""expert_replay.py — ⛔ **已退役（2026-09-28）**：保留為追跡紀錄，不要把它當量測入口。

RETIRED — WHY, AND WHAT TO USE INSTEAD
--------------------------------------
這支把 `CGC-IDS:` 路由軌跡變成「排序候選專家 id」的題目，量 precision@k 與 AUC。它在
2026-09-28 做到了兩件事：把 M5 prerouter 的 0.0%（**其實是 0/0**）翻成 45.5%，以及跟引擎的
計數器對到 **0.05 個百分點**。然後**同一輪把它量的那個量證明為沒有消費者**：

  * **可避免的非駐留帳戶只有 1.00%**（179 槽/layer；其餘 3.79% 是 compulsory＝首次觸及，
    原理上任何預測器都猜不到）—— `portal/targets.json` 的 `one_step_lag_cost`，見 README §12。
  * **可支付的阻塞窗口 ≲0.5% 的 decode**：`fill_wait_us` 0.224 s / 0.071 s 對 ~42 s 的服務時間，
    而 38 s 的 `pread_us` 在背景執行緒上（`llama-expert-cache.h:595/613` 自己說這兩筆只有
    `fill_wait_us` 與 wall time 可比）。
  * 它的預測 **99.97% 已經在池裡**（`queued=15 / pred_total=59240`；`prewarm_hot` 用同一份
    ranking 先填過）⇒ 它猜的是已經在裡面的東西。

⇒ **這條路徑的值函數是「臨界路徑上的阻塞 µs」，不是 precision@k。** 一個沒有消費者的準確度
   分數不只是無用，它會自己長出專案（`ema` 贏 `freq` 20 個百分點看起來像研究題目，實際上
   是在最佳化 0.5% 帳戶裡的排序）。要對「一個策略值不值得做」定價，用**成本端**模擬：
   `Backup/thrash_sim_20260919.py` 的 residency/miss 模型（`one_step_lag_cost` 那組數字就是它
   產的）。**那支量成本、這支量排序，有消費者的是前者。**

保留下來的理由（**不是**為了繼續量排序）：README §11/§12 的所有讀數要有可重現的出處，
而這支是產生它們的那支。所以它每次執行都會把上面這段印出來，而這**是一條被自測檢查的斷言**
（`expert_replay_selftest.py` 的 `test_retired_banner_is_printed`），不是一句聲明。

教訓（可帶走的那一條）：**先指名這個數字的消費者是誰、他的值函數用什麼單位，再造尺。**
本檔的下一段當初自己寫著「順序反過來才是錯的：先落地模型、再想辦法證明它有用」——
然後作者在同一支腳本上、上一層做了一模一樣的事：先造計分板，才去問那個分數有沒有價值。

THE QUESTION IT WAS BUILT TO ASK（原文保留 2026-09-28，供追溯當時為什麼認為有用）
--------------------------------------------------------------------------------
引擎裡已經**四個**專家 id 預測器（見下面 PRIORS）。它們的成績不是「概念上好不好」，而是
被自己的計數器量過的讀數。所以任何外部模型（JEv、CLM-8B、或任何讀 stdin 寫 stdout 的東西）
進場時，要回答的第一個問題不是「它聰明嗎」，是：

    在**我們自己的**路由資料上，它的 precision@k 有沒有超過引擎既有的頻率／EMA 預測器？

這支把 `CGC-IDS:` 路由軌跡變成那道題：每一題 = 某一層某一步，狀態是**該步之前**的路由歷史，
答案 = 該步實際被選中的專家集合。它不下載權重、不啟動引擎、不呼叫模型（除非顯式給
`--scorer command`），所以「要不要為它花掉這台機器的記憶體或 API 錢」可以先被回答一次。
**順序反過來才是錯的**：先落地模型、再想辦法證明它有用。

PRIORS — 引擎自己量過的讀數（不要重新發明）
--------------------------------------------
  * `CGC-PREROUTER`（M5 prerouter，2026-09-17）：`calls=80 hit=41 (12.8%)` → `480 / 6.1%`
    → `1840 / 3.8%` → `8080 / 4.2%`（`Backup/cgc_logs/llama_server_20260917_*.log`）。
    ⚠️ 判讀那四個數字**不能拿 k/256 = 3.1% 當地板**：均句隨機排序取 top-k 時
    precision = 平均 demand / 256（**與 k 無關**）。這條軌跡的平均 demand 是 21.8/256 ⇒
    地板是 **8.5%**，所以引擎那四個讀數（含早期的 12.8%）**都貼在地板附近**。
  * `cgc_rho_prefetch`（ρ-fill，2026-09-23）：cov_uni **0.854~0.860**。
  * 池天頂（`portal/targets.json` 的 `one_step_lag_cost`）：143 槽/layer 時 6.16% 的選中項
    在步初非駐留，其中 **3.79% 是 compulsory**（首次觸及）—— 那部分**任何預測器都猜不到**。

**而這支腳本第一次跑就把那條死路翻過來了。** 同一條 prerouter 規則（`freq`，逐層路由計數
的 top-k），餵**解碼歷史**而不是前綴歷史，在 2026-09-19 的 193 步軌跡上得到
**precision@8 ≈ 0.705**，而不是 0.042。差 17 倍，而差別只有一個：`freq` 表**由誰餵**。

    引擎的 `freq` 只有兩個寫入點：`llama-context.cpp:6701`（**stock，在大型前綴分支裡**，
    `n_tokens > pmax`）與 `:~7019`（解碼/pool 路徑，**被 `LLAMA_EXPERT_CACHE_ROUTE_RECORD`
    opt-in，預設關**，且它自己的註解就寫著「stock 那個只在前綴觸發 … 這就是 freq 空掉的原因」）。

⇒ 那四個 `CGC-PREROUTER` 讀數量的是「**前綴餵的表**用在解碼上」，不是這條規則的能力。
**在那條路徑上，那四個數字不是它的成績。** 這正是為什麼要用同一把尺重跑，而不是引用一個
計數器的輸出。

TWO POOLS, AND WHY THE RAW NUMBER ALONE IS NOT INFORMATIVE
---------------------------------------------------------
  * **all 池**：正例 vs 全部 256 個負例。容易，因為歷史上**從未出現**的 id 對任何基於歷史的
    排序器都必然是低分 —— 那一大塊負例是白送的。
  * **hard 池**：正例 vs **這一層曾經被路由過、但這一步沒被選**的 id。這才是判別力所在，
    也是唯一有資格當閘門的讀數。

而 hard 池有一個**必須拔掉的捷徑**：候選分數若來自「這一層誰最常被路由」，那 hard 池的分數
就是它自己的共變數。所以每個排序器都報兩套：raw 與 `|freq`（在題內候選池上對「歷史計數」
做最小平方回歸取殘差，`action_replay.residualize` —— 同一支調整器，同一個理由）。
`freq` 排序器自己的 `|freq` 讀數**必須回到 0.500**，那正好是這個調整器自己的自檢
（與 `action_replay` 裡 `length` 對照的 `|len` 回到 0.500 是同一條斷言）。

WEAK STATISTICS, STATED UP FRONT
--------------------------------
一步的 demand 是 `ntok × top_k` 的**聯集**（這台引擎 top_k=8、MTP 驗證步 ntok=4 ⇒ 上限 32），
所以 precision@k 的分母是 k 而不是 |demand|（recall 才用 |demand|），
**而 precision@k 被 demand 封頂：|demand| < k 的步最大只能拿到 |demand|/k**（自測抓到的）。
同一條軌跡上的案例
**高度自相關**（相鄰步的需求重疊 ~70%），所以案例數不是獨立樣本數，而 `±` 那個區間是
**假設獨立算的、會低估變異** —— 它是參考值，不是「顯著與否」的開關。
**這支報的是同一條軌跡內部的排序能力，不是跨工作負載的泛化能力。** 要泛化結論，換一份
軌跡重跑，並比較兩者的 compulsory 比例。

用法::

    # 內建階梯（chance 地板 / freq=prerouter 複刻 / ema=SpAc 複刻 / lag=前一步 / static=離線表）
    python3 agent_harness/engine_loop/distill/expert_replay.py

    # 指定軌跡與 k，並列出每一層的讀數
    python3 agent_harness/engine_loop/distill/expert_replay.py \\
        --trace Backup/cgc_logs/llama_server_20260919_194311.log --k 8 --per-layer

    # JEv / CLM 進來的地方：任何讀 stdin 寫 stdout 的評分器（協定與 action_replay 相同）
    python3 agent_harness/engine_loop/distill/expert_replay.py --scorer command \\
        --scorer-command 'python3 my_jev_adapter.py' --report

    python3 agent_harness/engine_loop/distill/expert_replay_selftest.py
"""

from __future__ import annotations

import argparse
import collections
import glob
import hashlib
import json
import os
import random
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent       # .../engine_loop/distill
ENGINE = HERE.parent                         # .../engine_loop
REPO = ENGINE.parent.parent                  # .../flashkv-devserver
for _p in (str(HERE), str(ENGINE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import action_replay as AR                   # noqa: E402  同一份 --scorer command 協定 + 同一支 residualize

OUT_ROOT = HERE / "expert_replay_out"

RETIRED_NOTE = (
    "\u26d4 已退役（2026-09-28）：這支量的是「排序準確度」，而排序準確度不是這條路徑的值函數。\n"
    "   生產路徑實測（README §12）：可避免的非駐留帳戶只有 1.00%（179 槽/layer）、可支付的\n"
    "   阻塞窗口 ≲0.5% 的 decode、而 79% 的缺口是 compulsory（首次觸及，原理上不可預測）。\n"
    "   值函數是「臨界路徑上的阻塞 µs」⇒ 本檔僅保留為追跡紀錄，不是量測入口。\n"
    "   要對策略定價，用成本端模擬（thrash_sim_20260919.py 的 residency/miss 模型）。"
)
LOG_HINT = REPO / "Backup" / "cgc_logs"
BUILTIN_SCORERS = ("random", "freq", "ema", "lag", "static")

# 引擎的預設值，不是這裡選的：top_k = n_expert_used = 8；CGC_SPAC_ALPHA 預設 0.85
# （`llama-expert-cache.h:748` 的 `cgc_spac_alpha()`）。**這兩個都不是在這條軌跡上調出來的**，
# 所以用它們當基線是合法的；任何在軌跡上調過參數的排序器都不是合法評估（見 `static` 的旗標）。
ENGINE_TOP_K = 8
ENGINE_SPAC_ALPHA = 0.85
IDS_RE = re.compile(r"^CGC-IDS:\s+ctx=(\S+)\s+pmax=(\d+)\s+il=(\d+)\s+ntok=(\d+)\s+(.*)$")


# --------------------------------------------------------------------------------------
# 資料：CGC-IDS 軌跡 -> 每一步、每一層的 demand 集合
# --------------------------------------------------------------------------------------

def sha16(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


def default_trace(min_lines: int = 200) -> Path:
    """最近一份含足夠 `CGC-IDS:` 行的 log。

    選檔用 mtime 是方便的，但要**印出來**：一份被 SIGKILL 的 log 也是一份 log，而
    「分析到一半的檔案」與「完整軌跡」在讀數上長得一樣。呼叫端會把路徑與 sha256 一起印。
    """
    cands = [p for p in glob.glob(str(LOG_HINT / "*.log")) if os.path.isfile(p)]
    # 不能拿 os.path.getmtime 當 sort key：這個目錄裡有斷掉的 symlink（`llama_server_latest.log`），
    # 它會讓整個排序在 stat 上炸掉 —— 一個壞連結不該讓工具無法啟動。
    cands.sort(key=lambda p: os.stat(p).st_mtime, reverse=True)
    best = None
    for p in cands:
        try:
            with open(p, errors="replace") as fh:
                n = sum(1 for line in fh if line.startswith("CGC-IDS:"))
        except OSError:
            continue
        if n >= min_lines:
            best = Path(p)
            break
    if best is None:
        raise SystemExit(f"no log under {LOG_HINT} has >= {min_lines} 'CGC-IDS:' lines; "
                         f"pass --trace explicitly")
    return best


def parse_trace(path: Path, base_ctx: str | None = None, top_k: int = ENGINE_TOP_K):
    """-> (steps, info)；steps[i][layer] = frozenset(expert ids)。

    步界線與截斷規則**沿用 `Backup/thrash_sim_20260919.py`**（那份腳本已經在這條軌跡上
    量過一次，兩支的口徑必須一致，否則兩個讀數不可比）：

      * 步界線 = `il` **不再遞增**（同一層的連續行屬於同一步，`il` 回到 0 = 新的一步）。
      * 一行被截斷（`len(ids) < ntok*top_k`）⇒ **整行丟棄**，不做部分計數。因為部分 union
        是**另一個** demand set，不是一個比較小的 demand set；把它算進去會讓 demand 偏小而
        讓所有排序器看起來都變好。丟了幾行要報出來（`truncated`）。
      * 同一層同一行內，`ntok` 個 token 的 top-k 是**逐 token 的扁平序列**（`ids[i + j*top_k]`）。
        **兩種用途要的東西不一樣，所以兩者都留著**：
          - **需求（truth）= 聯集**：池要服務的是這個集合（一步裡同一個專家被幾個 token 選中
            不重要，它只需要在池裡）。
          - **歷史計數 = 逐出現次數**：引擎的 `record_routes` 就是
            `for i in 0..n: freq[experts[i]]++`（`llama-expert-cache.cpp:2186`），即一個被 4 個
            token 都選中的專家**一次拿到 +4**。第一版這裡只數「每步 +1」，那是另一個排序，
            所以 `--count-mode` 預設已改成 `token`（引擎語意）。

    多個 ctx 時取行數最多的那個（＝主幹；MTP draft 是另一個 ctx、il=40、ntok=1）。
    """
    by_ctx: dict[str, list] = collections.defaultdict(list)
    n_lines = 0
    with open(path, errors="replace") as fh:
        for line in fh:
            if not line.startswith("CGC-IDS:"):
                continue
            m = IDS_RE.match(line.strip())
            if not m:
                continue
            n_lines += 1
            # ★ 尾段可能被**另一個 writer 截斷接進來**（兩個 thread / 兩個 fd 都在無緩衝 stderr 上，
            #   2026-09-28 實測：`... 60CGC-RSS: t=96.48 rss=...`）。所以逐 token 解析，遇到
            #   第一個不是整數的 token 就停 —— **不能對整行 split() 做 int()**，那會讓整支工具在真
            #   log 上 traceback（第一版就是這樣死的）。停下之後剩下的長度不足就交給下面的截斷規則丟掉。
            ids: list[int] = []
            garbled = False
            for tok in m.group(5).split():
                try:
                    ids.append(int(tok))
                except ValueError:
                    garbled = True
                    break
            by_ctx[m.group(1)].append((int(m.group(3)), int(m.group(4)), ids,
                                       int(m.group(4)) * top_k, garbled))
    if not by_ctx:
        raise SystemExit(f"no CGC-IDS lines in {path}")
    ntok_hist = collections.Counter(row[1] for rows in by_ctx.values() for row in rows)
    if base_ctx is None:
        base_ctx = max(by_ctx, key=lambda c: len(by_ctx[c]))
    if base_ctx not in by_ctx:
        raise SystemExit(f"ctx {base_ctx} not in trace (have {sorted(by_ctx)})")
    rows = by_ctx[base_ctx]

    steps: list[dict[int, list[int]]] = []
    cur: dict[int, list[int]] = {}
    prev_il = -1
    truncated = 0
    garbled = 0
    for il, ntok, ids, want, is_garbled in rows:
        if il <= prev_il and cur:
            steps.append(cur)
            cur = {}
        if len(ids) < want:
            # 兩種失敗模式分開數：「行本身短」（log writer 截斷）與「交錯寫入」（別的 writer 插進來）
            # 是不同的東西，混在一個計數裡就分不出「這份日誌少了多少」。
            if is_garbled:
                garbled += 1
            else:
                truncated += 1
            prev_il = il
            continue
        # 存**逐 token 的扁平序列**（不去重、不排序）：需求用 `set()` 得出，歷史計數用序列本身
        # （每次出現 +1，與引擎 `record_routes` 逐字相同）。
        cur.setdefault(il, []).extend(ids[:want])
        prev_il = il
    if cur:
        steps.append(cur)
    # 解析器**不**拒絕短軌跡：解析歸解析，可預測性由 `main()` 判（自測要用單步軌跡測解析本身）。

    all_ids = [i for s in steps for v in s.values() for i in v]
    layer_counts = sorted(len(s) for s in steps)
    union_sizes = sorted(len(set(v)) for s in steps for v in s.values())
    info = {
        "trace": str(path),
        "trace_sha256_16": sha16(path.read_bytes()),
        "trace_bytes": path.stat().st_size,
        "ids_lines": n_lines,
        "ctx_picked": base_ctx,
        "ctxs": {k: len(v) for k, v in by_ctx.items()},
        "ntok_hist": {str(k): v for k, v in sorted(ntok_hist.items())},
        "top_k_assumed": top_k,
        "steps": len(steps),
        "layers_per_step_median": layer_counts[len(layer_counts) // 2] if layer_counts else 0,
        "truncated_lines_dropped": truncated,
        "interleaved_lines_dropped": garbled,
        "id_min": min(all_ids) if all_ids else None,
        "id_max": max(all_ids) if all_ids else None,
        "demand_union_max": max(union_sizes) if union_sizes else 0,
        "demand_per_layer_step_median": (union_sizes[len(union_sizes) // 2]
                                         if union_sizes else 0),
    }
    return steps, info


# --------------------------------------------------------------------------------------
# 題目：每題 = (層, 步 t) 的狀態（t 之前的歷史）-> 候選 256 個 id -> 真答案
# --------------------------------------------------------------------------------------

def build_cases(steps: list[dict[int, list[int]]], history_steps: int = 4,
                min_history: int = 1, layer_filter=None,
                count_mode: str = "token") -> tuple[list[dict], dict]:
    """每一題都只使用**嚴格早於 t** 的步驟，這是這支腳本唯一的防洩漏結構。

    `state` 是給外部模型看的文字（協定與 `action_replay` 相同：query + candidates）。
    **它不含第 t 步自己的任何 id** —— 這是一條被自測檢查的斷言，不是一句聲明。
    """
    cases: list[dict] = []
    per_layer: dict[int, dict] = collections.defaultdict(
        lambda: {"cases": 0, "obs": 0, "first_touch": 0, "demand": 0})
    for t in range(1, len(steps)):
        cur = steps[t]
        for layer in sorted(cur):
            if layer_filter is not None and layer not in layer_filter:
                continue
            hist: collections.Counter = collections.Counter()
            obs = 0
            for s in steps[:t]:
                # `token` = 引擎語意（每次出現 +1，同一個專家被 4 個 token 選中 ⇒ +4）；
                # `step`  = 每步 +1（第一版，保留只是為了能重現先前發布過的讀數）。
                seq = list(s.get(layer, ()))
                if count_mode == "step":
                    seq = sorted(set(seq))
                for e in seq:
                    hist[e] += 1
                    obs += 1
            # `obs` 的單位跟著模式走（token：出現次數；step：有觀測的步數），所以這個下限
            # 在兩個模式下都是「至少這麼多歷史」。
            if not hist or obs < min_history:
                continue
            truth = frozenset(cur[layer])
            seen = frozenset(hist)
            first_touch = truth - seen
            last = [sorted(steps[k].get(layer, ())) for k in range(max(0, t - history_steps), t)]
            cases.append({
                "case_id": f"L{layer:02d}@t{t:04d}",
                "layer": layer,
                "step": t,
                "history": dict(hist),
                "last_steps": last,
                "truth": sorted(truth),
                "first_touch": sorted(first_touch),
                "n_ids": 256,
            })
            pl = per_layer[layer]
            pl["cases"] += 1
            pl["obs"] += obs
            pl["first_touch"] += len(first_touch)
            pl["demand"] += len(truth)
    total_first = sum(v["first_touch"] for v in per_layer.values())
    total_demand = sum(v["demand"] for v in per_layer.values())
    info = {
        "cases": len(cases),
        "layers": len(per_layer),
        "history_steps": history_steps,
        "count_mode": count_mode,
        # ★ 這是**不可預測**的那一塊（引擎自己的 one_step_lag_cost 說 143 槽時是 3.79%）。
        #   它是從這條軌跡算出來的，不是引用的：任何排序器的 recall 上限都是 1 - 這個比例。
        "compulsory_share": (total_first / total_demand) if total_demand else None,
        "compulsory_ids": total_first,
        "demand_ids": total_demand,
        "per_layer": {str(k): v for k, v in sorted(per_layer.items())},
    }
    return cases, info


STATE_HEAD = 24        # 狀態裡列出前 N 名計數（人可讀；外部模型要更多可以自己要求）

def render_state(case: dict) -> str:
    """狀態文字。**不含答案**（自測 `test_no_leak` 會檢查）。"""
    hist = case["history"]
    top = sorted(hist.items(), key=lambda kv: (-kv[1], kv[0]))[:STATE_HEAD]
    lines = [
        f"LAYER {case['layer']} | STEP {case['step']} | routing history up to step {case['step'] - 1}",
        f"k = {ENGINE_TOP_K} experts are selected per token; demand is the union over the step's tokens.",
        "-- per-expert route counts so far (top %d, count then id) --" % len(top),
        "  " + "  ".join(f"{e}:{c}" for e, c in top),
        f"-- distinct experts seen so far: {len(hist)} / 256 ; total observations: {sum(hist.values())} --",
        "-- most recent %d steps' selected experts (oldest first) --" % len(case["last_steps"]),
    ]
    for i, ids in enumerate(case["last_steps"]):
        lines.append(f"  step {case['step'] - len(case['last_steps']) + i}: " + " ".join(str(x) for x in ids))
    return "\n".join(lines)


def candidate_list(case: dict) -> list[dict]:
    """候選池 = 全部 256 個 id（引擎就是對全部 256 排名，再取 top-k）。

    `text` 給外部模型一個可讀的字串；`id` 是穩定的 `e<n>`，回傳時用它對齊 —— 協定與
    `action_replay.score_command` 逐欄位相同，所以一個 adapter 兩邊都能接。
    """
    hist = case["history"]
    return [{"id": f"e{e}", "text": f"expert {e} (route count so far {hist.get(e, 0)})"}
            for e in range(case["n_ids"])]


# --------------------------------------------------------------------------------------
# 排序器：score(case, corpus) -> list[float]（index = expert id）
# --------------------------------------------------------------------------------------

def score_random(case, corpus):
    rng = random.Random(f"{corpus['seed']}|{case['case_id']}")
    return [rng.random() for _ in range(case["n_ids"])]


def _from_map(mapping: dict, n: int) -> list[float]:
    """分數 = 原始量（越大越好），**不做單調變換**。

    這一點是刻意的，而且可檢查：AUC 只看排序，所以把分數換成名次不影響 AUC_all；但
    `|freq` 那一欄要在分數上對「歷史計數」做**最小平方回歸**，而名次是計數的非線性函數 ⇒
    殘差不會歸零，`freq` 的 `AUC_hard|freq` 就不會是 0.500，那個自檢會靜默失效。

    同分時誰在前面由 `evaluate()` 的 top-k 選擇決定（`key=(-score, id)`）——與 C++ 逐字相同：
    `a.first != b.first ? a.first > b.first : a.second < b.second`。AUC 則維持標準 c-index
    （同分 0.5）：把 id 的約定注入 AUC 會讓一個「全部同分」的排序器看起來像贏了。
    """
    return [float(mapping.get(e, 0)) for e in range(n)]


def score_freq(case, corpus):
    """**prerouter 複刻**（`llama_expert_cache_prerouter_predict`：逐層路由計數的 top-k）。

    唯一的差別是「表由誰餵」—— 這裡餵的是解碼歷史，引擎在量那四個讀數時餵的是前綴。
    那個差別就是這支腳本存在的理由（見 docstring 的 PRIORS）。

    分數就是計數本身（不做單調變換）⇒ `|freq` 那一欄對它必然歸零，那是自檢，不是巧合。
    """
    return _from_map(case["history"], case["n_ids"])


def score_ema(case, corpus):
    """**SpAc 複刻**（`llama_expert_cache_spac_update`）：`u[e]*=alpha` 後對被選中的 `+= (1-alpha)`。

    逐事件套用（不是逐步套用一次）—— 引擎是在 union 上逐次 bump，順序不影響結果。

    ⚠️ **只重建得出視窗內的那一段**：狀態只帶最近 `--history-steps` 步的原始集合，更早的
    事件沒有發生時間，就無法還原它們的 EMA（`u` 是逐步衰減的，不是計數的函數）。所以這個
    排序器是「純近期性」版本，比 `freq` 用的狀態**弱**；兩者的差別因此混了「衰減」與
    「可見歷史長度」兩件事。要分開它們，得把引擎的 `u` 逐層 dump 出來比對（尚未做）。
    """
    alpha = corpus["ema_alpha"]
    u = [0.0] * case["n_ids"]
    for ids in case["last_steps"]:
        for e in range(case["n_ids"]):
            u[e] *= alpha
        for e in ids:
            if e < case["n_ids"]:
                u[e] += 1.0 - alpha
    return u


def score_lag(case, corpus):
    """**前一步／上一 token／下一層家族**（draft-prefetch、`CGC_PREV_TOKEN_PREFETCH`、
    `CGC_LAYER_AHEAD_PREFETCH`）：預測 = 最近一步的 demand。"""
    if not case["last_steps"]:
        return [0.0] * case["n_ids"]
    last = set(case["last_steps"][-1])
    return [1.0 if e in last else 0.0 for e in range(case["n_ids"])]


def score_static(case, corpus):
    """**離線表**（看得到整條軌跡）：固定 top-k 表能拿到多少。

    這是「把排序結果預算成靜態表」的天花板 —— 正是 CLM 的 action embedding 快取那一類設計
    想拿的東西。⚠️ **它用了未來**，所以**不是可部署的排序器**：它報的是「一個離線擬合、
    部署時凍結的表，在這一條軌跡上最多值多少」。任何跟它比較的模型也要遵守同一條界線。
    """
    return _from_map(corpus["static"][case["layer"]], case["n_ids"])


def score_command(case, corpus):
    """外部模型（**JEv / CLM 進來的地方**）。協定**就是** `action_replay.score_command`。

    這裡不重寫一份：同一個 `--scorer command` 旗標、同一段 docstring 裡的協定、同一條
    「長度不符一律失敗，不截斷不補零」規則。一個 adapter 兩邊都能接，是這支腳本刻意維持的性質。
    """
    payload_cands = candidate_list(case)
    scores = AR.score_command(render_state(case), payload_cands, corpus)
    return [float(x) for x in scores]


SCORERS = {"random": score_random, "freq": score_freq, "ema": score_ema,
           "lag": score_lag, "static": score_static, "command": score_command}


def make_corpus(cases: list[dict], scorer: str, seed: int, command=None,
                ema_alpha: float = ENGINE_SPAC_ALPHA) -> dict:
    corpus = {"seed": seed, "command": command, "ema_alpha": ema_alpha}
    if scorer == "static":
        st: dict[int, collections.Counter] = collections.defaultdict(collections.Counter)
        for c in cases:
            for e, n in c["history"].items():
                st[c["layer"]][e] += n
        corpus["static"] = st
    return corpus


# --------------------------------------------------------------------------------------
# 指標
# --------------------------------------------------------------------------------------

def _auc(scores: list[float], pos_ids, neg_ids) -> float | None:
    """Mann-Whitney c-index，**同分算 0.5**（用平均名次實作，不需要枚舉配對）。

    256 個候選 × 7600 題 = 194 萬配對/指標，直接枚舉在 Python 裡是分鐘級；平均名次是
    O(n log n)，而且**與枚舉等價**（同分的平均名次正好給 0.5 分）。
    """
    if not pos_ids or not neg_ids:
        return None
    items = [(scores[i], 1) for i in pos_ids] + [(scores[i], 0) for i in neg_ids]
    items.sort(key=lambda x: x[0])
    n = len(items)
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and items[j + 1][0] == items[i][0]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[k] = avg
        i = j + 1
    sum_pos = sum(r for r, (_, p) in zip(ranks, items) if p)
    P, N = len(pos_ids), len(neg_ids)
    return (sum_pos - P * (P + 1) / 2.0) / (P * N)


def evaluate(cases: list[dict], scores_by_case: list[list[float]], k: int) -> dict:
    n = len(cases)
    hit = rec_pos = dem_pos = 0
    auc_all: list[float] = []
    auc_hard: list[float] = []
    auc_all_r: list[float] = []
    auc_hard_r: list[float] = []
    per_layer: dict[int, dict] = collections.defaultdict(lambda: {"cases": 0, "hit": 0, "k": 0})
    per_case = []

    for case, scores in zip(cases, scores_by_case):
        if len(scores) != case["n_ids"]:
            raise SystemExit(f"scorer returned {len(scores)} scores for {case['n_ids']} candidates "
                             f"({case['case_id']}) -- refusing to rank a misaligned answer")
        truth = set(case["truth"])
        # 「引擎會選誰」＝分數最高的 k 個（tie-break 已寫進分數）
        topk = sorted(range(case["n_ids"]), key=lambda e: (-scores[e], e))[:k]
        h = len(set(topk) & truth)
        hit += h
        dem_pos += len(truth)
        rec_pos += h
        pl = per_layer[case["layer"]]
        pl["cases"] += 1
        pl["hit"] += h
        pl["k"] += k

        pos = sorted(truth)
        neg_all = [e for e in range(case["n_ids"]) if e not in truth]
        seen = set(case["history"])
        neg_hard = [e for e in neg_all if e in seen]
        cov = [case["history"].get(e, 0) for e in range(case["n_ids"])]
        resid = AR.residualize(list(scores), cov)     # 拔掉「歷史計數」這條捷徑

        a = _auc(scores, pos, neg_all)
        ah = _auc(scores, pos, neg_hard)
        ar = _auc(resid, pos, neg_all)
        ahr = _auc(resid, pos, neg_hard)
        if a is not None:
            auc_all.append(a)
        if ah is not None:
            auc_hard.append(ah)
        if ar is not None:
            auc_all_r.append(ar)
        if ahr is not None:
            auc_hard_r.append(ahr)
        per_case.append({
            "case_id": case["case_id"], "layer": case["layer"], "step": case["step"],
            "hit_at_k": h, "demand": len(truth), "first_touch": len(case["first_touch"]),
            "auc_hard": ah, "auc_hard_freq_removed": ahr,
        })

    def _m(xs):
        return sum(xs) / len(xs) if xs else None

    p = hit / (n * k) if n else 0.0
    # ★ chance floor 是 **mean(|demand|) / 256**，不是 k/256。均勻隨機排序取 top-k 時
    #   E[hit] = k·P/N ⇒ precision = P/N，**與 k 無關**。第一次跑就把「k/256 = 3.1%」
    #   這個直覺打死了：這條軌跡的平均 demand 是 21.8/256，所以 random 拿的是 8.5%，
    #   不是 3.1%。任何拿 3.1% 當地板去讀引擎那四個 3.8%/4.2% 的人都會讀錯。
    mean_demand = (dem_pos / n) if n else 0.0
    chance = mean_demand / 256.0
    half = 1.96 * (p * (1.0 - p) / (n * k)) ** 0.5 if n else 0.0
    return {
        "n_cases": n,
        "k": k,
        "mean_demand": mean_demand,
        "chance_precision_at_k": chance,
        "precision_at_k": p,
        # 只當參考：案例高度自相關，這個區間**低估**了變異（假設獨立）。判讀一律以 chance
        # floor 與 freq 基線為準，不要拿它當「顯著與否」的開關。
        "precision_at_k_ci95_half_width_naive": half,
        "recall_of_demand": (rec_pos / dem_pos) if dem_pos else None,
        "auc_all": {"value": _m(auc_all), "n": len(auc_all)},
        "auc_hard": {"value": _m(auc_hard), "n": len(auc_hard)},
        "auc_all_freq_removed": {"value": _m(auc_all_r), "n": len(auc_all_r)},
        "auc_hard_freq_removed": {"value": _m(auc_hard_r), "n": len(auc_hard_r)},
        "per_layer": {str(kk): (vv["hit"] / (vv["cases"] * k) if vv["cases"] else None)
                      for kk, vv in sorted(per_layer.items())},
        "cases": per_case,
    }


def run(cases: list[dict], scorer: str, k: int, seed: int, command=None,
        ema_alpha: float = ENGINE_SPAC_ALPHA) -> dict:
    corpus = make_corpus(cases, scorer, seed, command, ema_alpha)
    fn = SCORERS[scorer]
    out = []
    t0 = time.time()
    for case in cases:
        out.append(fn(case, corpus))
    res = evaluate(cases, out, k)
    res["scorer"] = scorer
    res["seconds"] = round(time.time() - t0, 3)
    return res


# --------------------------------------------------------------------------------------
# 輸出
# --------------------------------------------------------------------------------------

def print_info(info: dict, cases_info: dict, out=print) -> None:
    print = out  # noqa: A001
    print(f"  軌跡        : {info['trace']}")
    print(f"                sha256:16={info['trace_sha256_16']}  {info['trace_bytes']} B  "
          f"CGC-IDS 行 {info['ids_lines']}")
    print(f"  ctx         : {info['ctx_picked']}（全部 {info['ctxs']}）"
          f"  ntok 分布 {info['ntok_hist']}  → 假設 top_k={info['top_k_assumed']}")
    print(f"  步／層      : {info['steps']} 步 × 中位 {info['layers_per_step_median']} 層"
          f"  id 範圍 [{info['id_min']},{info['id_max']}]"
          f"  demand union 中位 {info['demand_per_layer_step_median']}／最大 {info['demand_union_max']}")
    print(f"  計數口徑    : {cases_info['count_mode']}（token = 引擎 record_routes 的逐出現次數；"
          f"step = 每步 +1）")
    if info["truncated_lines_dropped"]:
        print(f"  ⚠ 截斷丟棄   : {info['truncated_lines_dropped']} 行（部分 union 是**另一個** demand set，"
              f"寧可丟棄不可部分計數 —— 沿用 thrash_sim 的規則）")
    if info.get("interleaved_lines_dropped"):
        print(f"  ⚠ 交錯丟棄   : {info['interleaved_lines_dropped']} 行（無緩衝 stderr 上另一個 writer "
              f"插進同一行；解析到第一個非整數就停，不足長度一律丟棄）")
    cs = cases_info
    print(f"  題目        : {cs['cases']} 題（{cs['layers']} 層，歷史含最近 {cs['history_steps']} 步）")
    if cs["compulsory_share"] is not None:
        print(f"  ★ 不可預測   : compulsory（該層首次出現）{cs['compulsory_ids']}/{cs['demand_ids']} "
              f"= {cs['compulsory_share']*100:.2f}%  ⇒ **任何排序器的 recall 上限是 "
              f"{100-cs['compulsory_share']*100:.2f}%**")


def _f(v, nd=3):
    return "  n/a" if v is None else f"{v:.{nd}f}"


def print_result(res: dict, out=print) -> None:
    print = out  # noqa: A001
    a, h = res["auc_all"], res["auc_hard"]
    ar, hr = res["auc_all_freq_removed"], res["auc_hard_freq_removed"]
    print(f"  {res['scorer']:>7}  precision@{res['k']}={_f(res['precision_at_k'])} "
          f"±{_f(res['precision_at_k_ci95_half_width_naive'])}  "
          f"recall={_f(res['recall_of_demand'])}  "
          f"(chance {_f(res['chance_precision_at_k'])} = mean demand "
          f"{_f(res['mean_demand'], 1)}/256)")
    print(f"           AUC_all={_f(a['value'])}  AUC_hard={_f(h['value'])}  "
          f"AUC_all|freq={_f(ar['value'])}  AUC_hard|freq={_f(hr['value'])}  "
          f"[{res['seconds']}s]")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--trace", type=Path, default=None,
                    help="CGC-IDS 軌跡所在的 log（不給 = Backup/cgc_logs 下最新且 >=200 行的那份）")
    ap.add_argument("--ctx", default=None, help="指定 ctx（預設：行數最多的那個＝主幹）")
    ap.add_argument("--k", type=int, default=ENGINE_TOP_K, help="每 token 選幾個（引擎 n_expert_used=8）")
    ap.add_argument("--history-steps", type=int, default=4, help="狀態裡附上最近幾步的原始集合")
    ap.add_argument("--min-history", type=int, default=1, help="歷史觀測數少於此的題目跳過")
    ap.add_argument("--count-mode", choices=("token", "step"), default="token",
                    help="歷史計數的單位：token=**引擎語意**（每次出現 +1，同一個專家被 4 個 token\n"
                         "選中 ⇒ +4，與 record_routes 逐字相同）；step=每步 +1（第一版，只為重現舊讀數）")
    ap.add_argument("--steps-limit", type=int, default=None, help="只取前 N 步（跑得快一點／可重現的子集）")
    ap.add_argument("--layer", type=int, action="append", default=None, help="只跑這些層（可重複）")
    ap.add_argument("--seed", type=int, default=20260928)
    ap.add_argument("--ema-alpha", type=float, default=ENGINE_SPAC_ALPHA,
                    help=f"SpAc EMA 衰減（引擎預設 {ENGINE_SPAC_ALPHA}；在軌跡上調過就不是合法基線）")
    ap.add_argument("--scorer", choices=BUILTIN_SCORERS + ("command",), default=None,
                    help="不給 = 跑內建階梯（random / freq / ema / lag / static）")
    ap.add_argument("--scorer-command", default=None,
                    help="外部評分器命令（讀 stdin 寫 stdout；協定 = action_replay.score_command）")
    ap.add_argument("--per-layer", action="store_true", help="列出每一層的 precision@k")
    ap.add_argument("--cases", action="store_true", help="印最差的 20 題")
    ap.add_argument("--json", action="store_true", help="只印 JSON（人類可讀輸出改到 stderr）")
    ap.add_argument("--report", action="store_true", help="寫 distill/expert_replay_out/<ts>/")
    ap.add_argument("--out-dir", type=Path, default=None)
    args = ap.parse_args()

    # --json 是機器模式：人類可讀的輸出走 stderr，所以 stdout 從第一行就是 JSON。
    P = (lambda *a, **kk: print(*a, file=sys.stderr, **kk)) if args.json else print

    # 退役警示每次都印（--json 時走 stderr，所以 stdout 從第一行仍是純 JSON）。
    # 理由：這支最可能的誤用就是被後來的人當成「有個分數可以追」的記分板。
    P(RETIRED_NOTE)
    P()

    trace = args.trace or default_trace()
    steps, info = parse_trace(trace, args.ctx, args.k)
    if len(steps) < 2:
        raise SystemExit(f"trace has {len(steps)} step(s) -- need >= 2 to predict anything: {trace}")
    if args.steps_limit:
        steps = steps[:args.steps_limit]
        info["steps"] = len(steps)
        info["steps_limit"] = args.steps_limit
    cases, cinfo = build_cases(steps, args.history_steps, args.min_history,
                              set(args.layer) if args.layer else None, args.count_mode)
    if not cases:
        raise SystemExit("no cases -- history too short or every layer filtered out")

    P(f"=== 專家 id 預測重放: k={args.k} / 歷史 {args.history_steps} 步 / seed={args.seed} ===")
    print_info(info, cinfo, P)
    P()

    results = []
    if args.scorer:
        if args.scorer == "command" and not args.scorer_command:
            raise SystemExit("--scorer command 需要 --scorer-command")
        results.append(run(cases, args.scorer, args.k, args.seed, args.scorer_command, args.ema_alpha))
    else:
        for s in BUILTIN_SCORERS:
            results.append(run(cases, s, args.k, args.seed, None, args.ema_alpha))
        # 內建階梯跑完才把外部模型放進去：命令的失敗不該讓內建讀數消失
        if args.scorer_command:
            results.append(run(cases, "command", args.k, args.seed, args.scorer_command, args.ema_alpha))
    for res in results:
        print_result(res, P)

    # 判讀放在數字下面，先講「哪個讀數可讀」再講「誰贏」：這一題最容易發生的錯，
    # 是把 AUC_all（白送的負例撐起來的）當成能力。
    by_name = {r["scorer"]: r for r in results}
    P()
    for res in results:
        d = res["precision_at_k"] - res["chance_precision_at_k"]
        readable = abs(d) > res["precision_at_k_ci95_half_width_naive"]
        note = []
        if res["scorer"] in ("static",):
            note.append("**離線表：用了未來，不是可部署的排序器**")
        if "freq" in by_name and res["scorer"] not in ("freq", "random"):
            bf = by_name["freq"]["auc_hard_freq_removed"]["value"]
            mv = res["auc_hard_freq_removed"]["value"]
            if bf is not None and mv is not None:
                note.append(f"AUC_hard|freq {mv:.3f} vs freq 基線 {bf:.3f} -> "
                            + ("贏過基線 +0.05" if mv > bf + 0.05 else "**沒贏過 freq 基線**"))
        if res["scorer"] == "freq":
            hr = res["auc_hard_freq_removed"]["value"]
            note.append(f"AUC_hard|freq={_f(hr)} 應為 0.500（調整器自檢：把 freq 拔掉，"
                        f"freq 排序器就沒有資訊了）")
        P(f"  判讀 {res['scorer']:>7}: precision@{res['k']} 相對 chance {d:+.3f} "
          f"({'可讀' if readable else '**在雜訊內**'})"
          + ("  |  " + "  |  ".join(note) if note else ""))

    if args.per_layer:
        for res in results:
            P(f"\n  --- {res['scorer']}: 每層 precision@{res['k']} ---")
            for lay, v in res["per_layer"].items():
                P(f"    layer {lay:>3}: {_f(v)}")

    if args.cases:
        worst = sorted(results[0]["cases"], key=lambda c: (c["hit_at_k"], c["case_id"]))[:20]
        P(f"\n  --- {results[0]['scorer']}: 最差的 20 題 ---")
        for c in worst:
            P(f"    {c['case_id']}  hit={c['hit_at_k']}/{results[0]['k']} "
              f"demand={c['demand']} 首次={c['first_touch']}  "
              f"auc_hard={_f(c['auc_hard'], 2)} auc_hard|freq={_f(c['auc_hard_freq_removed'], 2)}")

    if args.report or args.out_dir:
        stamp = time.strftime("%Y%m%d_%H%M%S")
        outp = args.out_dir or (OUT_ROOT / stamp)
        outp.mkdir(parents=True, exist_ok=True)
        manifest = {
            "trace": str(trace), "trace_sha256_16": info["trace_sha256_16"],
            "ctx": info["ctx_picked"], "k": args.k, "history_steps": args.history_steps,
            "min_history": args.min_history, "steps_limit": args.steps_limit,
            "layers": args.layer, "seed": args.seed, "ema_alpha": args.ema_alpha,
            # 命令只記 sha256、不記原文：manifest 會被 commit，而命令列可能帶著 API key
            #（沿用 closed_loop.py / action_replay.py 的同一條規則）。
            "scorer_command_sha256_16": (sha16(args.scorer_command.encode("utf-8"))
                                         if args.scorer_command else None),
            "generated_by": "agent_harness/engine_loop/distill/expert_replay.py",
        }
        (outp / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        (outp / "replay.json").write_text(
            json.dumps({"trace_info": info, "case_info": cinfo, "results": results},
                       ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        P(f"\n  寫入: {outp}/manifest.json + replay.json")

    if args.json:
        print(json.dumps({"trace_info": info, "case_info": cinfo,
                          "results": [{k: v for k, v in r.items() if k != "cases"} for r in results]},
                         ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
