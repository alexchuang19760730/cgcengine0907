#!/usr/bin/env python3
"""預算閘門：`池子 ＋ Metal 峰值駐留 ＋ 保留 ＞ 上限` ⇒ 這一場的 t/s 是**足跡**，不是**能力**。

為什麼要它（2026-09-30）
------------------------
`quote_gate.py` 判的是「這一場的三個 rep 是不是同一個量」（R1–R3）、「窗口乾不乾淨」（R4）、
「量到的還是不是交付臂」（R5–R6）。它**不問**一件更前面的事：**這一場在算術上有沒有可能
不換頁**。R4 答不了這個問題，因為 R4 讀的 `attribution` 是**窗口內的事件**——而池子的頁
可能在起跑前就已經被趕出去了（存量不在窗口裡變化 ⇒ R4 看到一個「乾淨」的窗口）。

實測的兩個反例（同一個交付 cell、同一個 build、同樣 `contract.ok=true`、batch 512/512）：

    file                                 t/s     swap 成長   Metal 峰值   attribution
    anchor_clean/anchor_delivery.json    9.702   +4830.8     10989.83    swap      <- 代價量在窗口裡
    quote_hygiene/k4_noflags.json       26.203   +4344.4     10273.48    swap
    spac_sweep/k_sweep_T4_K4.json       27.338      −8.0      9935.86    none      <- 一致，但帳上沒有代價

`k_sweep_T4_K4` 的逐 rep 是 [27.3815, 27.242, 27.3894]（max/min 1.005）、`attribution=none`
⇒ R1–R4 全過（R5／R6 另外擋住它）。而它的起跑 swap 存量已是 9008.69 MiB、`min_free` 55.66 MiB：
**它快是因為「該被趕的頁在起跑前就已經不在」，不是因為引擎把補頁藏起來了。**
那種讀數回答的是「補頁政策的隱藏能力」，不是「這台機器能跑多快」。

算術（全部取自產物自己帶的數字，不是我另外假設的）
--------------------------------------------------
`memory_pressure.metal_gate(phase="peak")` 已經把三項算好寫進每個 harness 產物：

    池子  ＝ `--expert-cache` 宣告值（CLI，**宣告**的，不是量到的）
    Metal 駐留 ＝ 整趟峰值 `pages_wired`（`memory.worst.max_wired_mb`）
    保留   ＝ `CGC_METAL_RESERVE_MB`（預設 1024，Metal 上限是全機的）
    上限   ＝ 引擎 stderr 的 `recommendedMaxWorkingSetSize`（本機 11453.25 MiB）

⇒ `total = pool + wired_peak + reserve`；`deficit = total − ceiling`。
`deficit > 0` 就是「池子必然有一部分不在記憶體裡」——這不是標籤，是可算的。

⚠ **這台機器的實情，寫在這裡免得每次重推**：全語料掃下來，`deficit` 在**每一場**都是正的
（wired 峰值 9.9–12.3 GiB ／ pool 8192 ／ 上限 11453.25 ⇒ 超 7.7–10.1 GiB），
**包含已認證的 11.703 錨點**（wired 12334 ⇒ 超 10097 MiB）。所以：

    `--mode strict`（預設，＝「必然 swap 就降級」）在這台機器上會**把每一場都判 OVERBUDGET**。
    這不是閘門壞了，這是先前那個結論的可執行版本：**本機不是能力量測的場地**。
    要拿它做「同一台機器上兩支補頁政策的 A/B」，用 `--mode paid`（只降級「這一場量到了代價」的）。

    2026-09-30 全語料第一次登記（`--glob 'Backup/**/*.json'`，2629 個場）：

        OVERBUDGET 322（其中 PAID 300、帳上沒有代價的 22）／REFUSE 2307／IN-BUDGET 0
        赤字 min/median/max = 7564 / 9997 / 10266 MiB
        REFUSE 裡 380 場是「預算閘未武裝」——那些是 2026-09-24 加 memory 取樣器**之前**的產物，
        它們連峰值 Metal 駐留都沒有 ⇒ **判不了**（不是放行；見 B3）。

判準
----
  B1 算術：`deficit > 0` ⇒ **OVERBUDGET**（池子有一部分在 swap，必然）
  B2 餘裕：`0 < headroom <= TIGHT_MARGIN_MIB` ⇒ **TIGHT**（放行，但沒有緩衝）
  B3 缺件：讀不到 池子／峰值駐留／上限 任一項 ⇒ **REFUSE**（不猜；「沒武裝」≠「通過」）
  B4 自洽：產物自己寫的 `total_mb` 與三項相加不符（>1 MiB）⇒ **REFUSE**

另外兩個**報告用**（不單獨當判準，但會被點名）：
  P1 **PAID**：這一場把代價量到了 —— `swap 成長 > SWAP_GROWTH_MB(=512)`，或
     `misses > 0` 且有效讀取 `<= IO_FLOOR_MIB_S`。後者的根據是**已有的校準**：
     `docs/IO_AXIS_VERDICT_2026-09-25.md` §2（`pread_cost_probe`）量到**冷讀 760 MiB/s**
     （1 緒 0.487 ms/job）／熱讀 17.6 GB/s。實測的 1.0–17.0 MiB/s 比裝置冷讀能力低
     1–3 個數量級 ⇒ 那些 miss 不是由 SSD 服務的，是 swap／壓縮。
  P2 **UNPRICED**：`deficit > 0` 而這一場**一次讀都沒有**（`file_reads == 0`）—— 沒有代價、
     也沒有帳。`k_sweep_T4_K4` 就是這一格（file_reads 1704 但 `io_bytes=0`、`misses=0`）。
     ⇒ 這比 PAID 更該被盯著看：成本可能在**這支工具量不到的地方**。

`SWAP_GROWTH_MB` 沿用 `memory_pressure.py`（校準過：曲線的膝點，見該檔 docstring），
`METAL_RESERVE_MB`／`metal_ceiling()`／`metal_gate()` 也全部從那裡 import
（**單一定義**：口徑改了只改一處）。本檔只新增兩個常數（IO 那條界線），出處就在上面。

用法
----
    python3 scripts/check/budget_gate.py --selftest
    python3 scripts/check/budget_gate.py check Backup/quote_hygiene_2026-09-30/k4_noflags.json
    python3 scripts/check/budget_gate.py check --glob 'Backup/**/*.json' --tally
    python3 scripts/check/budget_gate.py check --mode paid --json /tmp/bg.json <paths>

rc：0 = 沒有任何 OVERBUDGET（在當前 mode 下）；1 = 有 OVERBUDGET；2 = 用法錯誤；
    3 = 沒有可判的產物。

程式介面（給別的閘門用，不要重寫一遍）：
    blocks_capability(prod, mode="strict") -> (bool, [reasons])
"""
from __future__ import annotations

import argparse
import glob as _glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import memory_pressure as mempress  # noqa: E402  （上限／保留／swap 成長的單一定義在那裡）

MIB = 1048576.0

# --- 沿用既有常數（不新增品味）--------------------------------------------------------
RESERVE_MIB = float(mempress.METAL_RESERVE_MB)          # 1024.0
SWAP_GROWTH_MIB = float(mempress.SWAP_GROWTH_MB)        # 512.0（曲線膝點，見 memory_pressure）
# 本檔新增的兩個常數（出處：docs/IO_AXIS_VERDICT_2026-09-25.md §2 pread_cost_probe）
IO_COLD_MIB_S = 760.0     # 冷讀（1 緒，0.487 ms/job ⇒ 760 MiB/s）；熱讀 17.6 GB/s
IO_FLOOR_PCT = 10.0       # ≤ 裝置冷讀能力的 1/10 ⇒ 這條路徑不是在讀 SSD
IO_FLOOR_MIB_S = IO_COLD_MIB_S * IO_FLOOR_PCT / 100.0
# 餘裕小於這一格 = TIGHT（沿用 swap 成長的同一格，理由相同：512 以下分不出東西）
TIGHT_MARGIN_MIB = SWAP_GROWTH_MIB
TOTAL_TOL_MIB = 1.0       # 產物自寫的 total 與三項相加的容許差（B4）

VERDICTS = ("IN-BUDGET", "TIGHT", "OVERBUDGET", "REFUSE")
RC_OK, RC_OVER, RC_USAGE, RC_NOTHING = 0, 1, 2, 3

# 池子的宣告可能只出現在 env／tag（`--expert-cache` 走 CLI，harness 會寫進 env）。
POOL_ENV_KEYS = ("CGC_EXPERT_CACHE_BYTES", "CGC_SERVER_EXPERT_CACHE_BYTES",
                 "CGC_EXPERT_CACHE_MB", "CGC_SERVER_EXPERT_CACHE_MB")
RESERVE_ENV_KEYS = ("CGC_METAL_RESERVE_MB",)
CEILING_ENV_KEYS = ("CGC_METAL_WORKING_SET_MB",)


def _arm_env(prod):
    """把一支臂的 env 併起來看：`env` ＋ `extra_env` ＋ `tag` 的 `K=V` 尾串。

    與 `quote_gate._arm_env` 同一件事。**刻意不互相 import**：R7 若被併進 quote_gate，
    方向是 quote_gate → budget_gate，反過來成環。三處都看是因為三種產物各寫一處。
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


def _fnum(env, keys):
    """env／tag 裡的數值（MiB）。讀不出就 None——不猜。"""
    for k in keys:
        v = env.get(k)
        if v in (None, ""):
            continue
        try:
            return float(v)
        except (TypeError, ValueError):
            continue
    return None


def _pool_from_scalars(prod):
    """harness 把 `--expert-cache` 也記在 `scalars.BUDGET`（bytes）—— 舊產物沒有 env 時靠這條。"""
    sc = prod.get("scalars")
    if not isinstance(sc, dict):
        return None
    raw = sc.get("BUDGET")
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return None
    return v / MIB


def _pool_from_env(env):
    """`--expert-cache` 有兩種寫法：bytes（`CGC_EXPERT_CACHE_BYTES`）與 MiB（`..._MB`）。"""
    for k in POOL_ENV_KEYS:
        raw = env.get(k)
        if raw in (None, ""):
            continue
        try:
            v = float(raw)
        except (TypeError, ValueError):
            continue
        return v / MIB if k.endswith("BYTES") else v
    return None


def _worst_mib(prod, key):
    worst = ((prod.get("memory") or {}).get("worst") or {}) if isinstance(prod.get("memory"), dict) else {}
    v = worst.get(key)
    return float(v) if isinstance(v, (int, float)) else None


def _swap_series(prod):
    mem = prod.get("memory")
    if not isinstance(mem, dict):
        return None, None
    samples = mem.get("samples") or []
    vals = [s.get("swap_used_mb") for s in samples
            if isinstance(s, dict) and isinstance(s.get("swap_used_mb"), (int, float))]
    launch = (mem.get("launch") or {}).get("swap_used_mb") if isinstance(mem.get("launch"), dict) else None
    end = (mem.get("end") or {}).get("swap_used_mb") if isinstance(mem.get("end"), dict) else None
    if len(vals) >= 2:
        return vals[-1] - vals[0], max(vals)
    if isinstance(launch, (int, float)) and isinstance(end, (int, float)):
        return end - launch, None
    return None, (max(vals) if vals else None)


def _peak_block(prod):
    """回 (鍵, 區塊)。峰值那一塊才是判準：起跑那道閘取在 spawn 之前，看不到引擎自己的那一塊。"""
    for key in ("metal_gate_peak", "metal_gate"):
        b = prod.get(key)
        if isinstance(b, dict) and b:
            return key, b
    return None, None


def _items_mib(block):
    out = {}
    for it in (block.get("items") or []):
        if isinstance(it, dict) and it.get("name") and isinstance(it.get("mb"), (int, float)):
            out[str(it["name"])] = float(it["mb"])
    return out


def judge(prod, mode="strict", ceiling_mib=None, ceiling_cache=None):
    """判一個產物（**一場**，不是一個 row）。回 (verdict, reasons, metrics)。

    缺件一律 REFUSE（B3）：沒有證據說它在預算內。舊產物沒有 `metal_gate_*` 時，
    用 `memory` ＋ 宣告的池子**重算一次**（走 `memory_pressure.metal_gate`，同一口徑）。
    """
    env = _arm_env(prod)
    key, block = _peak_block(prod)
    items = _items_mib(block) if block else {}
    declared_total = block.get("total_mb") if block else None

    pool = items.get("pool_mb")
    wired = items.get("wired_mb")
    reserve = items.get("reserve_mb")
    ceil = block.get("ceiling_mb") if block else None
    why_ceiling = block.get("ceiling_source") if block else None
    recomputed = False

    # 池子／保留：產物沒寫就從 env／tag 抽（`--expert-cache` 與 CGC_METAL_RESERVE_MB）
    if pool is None:
        pool = _pool_from_env(env)
    if pool is None:
        pool = _pool_from_scalars(prod)
    if reserve is None:
        reserve = _fnum(env, RESERVE_ENV_KEYS)
        if reserve is None:
            reserve = RESERVE_MIB
    if wired is None:
        wired = _worst_mib(prod, "max_wired_mb")
    if ceil is None:
        env_ceil = _fnum(env, CEILING_ENV_KEYS)
        if env_ceil is not None:
            ceil, why_ceiling = env_ceil, "CGC_METAL_WORKING_SET_MB（產物自帶）"
        elif ceiling_mib is not None:
            ceil, why_ceiling = float(ceiling_mib), "--ceiling-mib（人指定）"
        else:
            got, src = mempress.metal_ceiling(cache_path=ceiling_cache)
            if got is not None:
                ceil, why_ceiling = float(got), "%s（**快取**，不是這一場引擎的 stderr）" % src
        recomputed = True

    metrics = dict(
        block=(key or "—"), recomputed=recomputed, mode=mode,
        pool_mib=pool, wired_mib=wired, reserve_mib=reserve, ceiling_mib=ceil,
        ceiling_source=why_ceiling, total_mib=None, deficit_mib=None, headroom_mib=None,
        wireable_mib=None, unbacked_mib=None, unbacked_pct=None, biggest=None,
        declared_total_mib=declared_total, swap_growth_mib=None, max_swap_mib=None,
        misses=None, file_reads=None, io_bytes=None,
        io_effective_mib_s=None, io_floor_mib_s=IO_FLOOR_MIB_S,
        paid=False, unpriced=False, rows=[], tps=[])

    if prod.get("refused_preflight") is True:
        return "REFUSE", ["refused_preflight=true（這一輪沒有量到 t/s）"], metrics
    rows = [r for r in (prod.get("rows") or []) if isinstance(r, dict)]
    metrics["rows"] = [(r.get("n_prompt"), r.get("n_gen"), r.get("avg_ts")) for r in rows]
    dec = [r.get("avg_ts") for r in rows
           if r.get("n_prompt") == 0 and r.get("avg_ts") is not None]
    metrics["tps"] = dec or [r.get("avg_ts") for r in rows if r.get("avg_ts") is not None]

    missing = [n for n, v in (("上限", ceil), ("池子", pool), ("峰值 Metal 駐留", wired))
               if v is None]
    if missing:
        return "REFUSE", ["預算閘未武裝：讀不到 %s ⇒ 這一場**沒有**這根桿子（不等於通過）"
                          % "、".join(missing)], metrics

    total = float(pool) + float(wired) + float(reserve)
    deficit = total - float(ceil)
    metrics.update(total_mib=total, deficit_mib=deficit, headroom_mib=-deficit,
                   wireable_mib=float(ceil) - float(wired) - float(reserve))
    metrics["unbacked_mib"] = max(0.0, float(pool) - max(0.0, metrics["wireable_mib"]))
    metrics["unbacked_pct"] = (metrics["unbacked_mib"] / float(pool) * 100.0) if pool else None
    metrics["biggest"] = "wired_mb" if float(wired) > float(pool) else "pool_mb"

    if declared_total is not None and isinstance(declared_total, (int, float)) and \
            abs(float(declared_total) - total) > TOTAL_TOL_MIB:
        return "REFUSE", ["產物內部不自洽（自寫 total %.0f vs 三項相加 %.0f，差 %.1f MiB）"
                          % (float(declared_total), total, abs(float(declared_total) - total))], metrics

    growth, max_swap = _swap_series(prod)
    cache = prod.get("cache") if isinstance(prod.get("cache"), dict) else {}
    metrics.update(swap_growth_mib=growth, max_swap_mib=max_swap,
                   misses=cache.get("misses"), file_reads=cache.get("file_reads"),
                   io_bytes=cache.get("io_bytes"),
                   io_effective_mib_s=cache.get("io_effective_mib_s"))
    slow_reads = (isinstance(cache.get("misses"), (int, float)) and cache.get("misses")
                  and isinstance(cache.get("io_effective_mib_s"), (int, float))
                  and cache.get("io_effective_mib_s") <= IO_FLOOR_MIB_S)
    metrics["paid"] = bool((isinstance(growth, (int, float)) and growth > SWAP_GROWTH_MIB) or slow_reads)
    metrics["unpriced"] = bool(deficit > 0 and not metrics["paid"])

    reasons = []
    if deficit > 0:
        reasons.append(
            "池子 %.0f ＋ Metal 峰值 %.0f ＋ 保留 %.0f ＝ %.0f MiB ＞ 上限 %.0f MiB（%s）"
            "⇒ **必然**有一部分池子在 swap 上：超 %.0f MiB、宣告的池子有 %.0f MiB（%.0f%%）沒有 backing；"
            "最大項＝%s"
            % (pool, wired, reserve, total, ceil, why_ceiling, deficit,
               metrics["unbacked_mib"], metrics["unbacked_pct"] or 0.0,
               "Metal 峰值駐留（模型側）" if metrics["biggest"] == "wired_mb" else "宣告的池子"))
        if metrics["paid"]:
            reasons.append("這一場把代價量到了：swap 成長 %s MiB、misses %s、有效讀取 %s MiB/s"
                           % (_fmt(growth), cache.get("misses"), _fmt(cache.get("io_effective_mib_s"))))
        elif metrics["unpriced"]:
            reasons.append("**帳上沒有代價**：這一場一次代價都沒量到（swap 成長 %s MiB、misses=%s、"
                           "file_reads=%s（io_bytes=%s）、有效讀取 %s MiB/s）⇒ 超額是算出來的，"
                           "而成本若存在，不在此工具看得見的地方"
                           % (_fmt(growth), cache.get("misses"), cache.get("file_reads"),
                              cache.get("io_bytes"), _fmt(cache.get("io_effective_mib_s"))))
        return "OVERBUDGET", reasons, metrics
    if -deficit <= TIGHT_MARGIN_MIB:
        reasons.append("在預算內但沒有緩衝：餘裕只有 %.0f MiB（<= %.0f）"
                       % (-deficit, TIGHT_MARGIN_MIB))
        return "TIGHT", reasons, metrics
    return "IN-BUDGET", [], metrics


def _fmt(v):
    if v is None:
        return "—"
    if isinstance(v, float):
        return "%.1f" % v
    return str(v)


def blocks_capability(prod, mode="strict", **kw):
    """這一場能不能當**能力**數（＝拿去看板的綠燈／對外宣稱）。回 (bool, [reasons])。

    `mode="strict"`：算術上必然 swap ⇒ 不能（使用者要的那一條）。
    `mode="paid"`  ：只有「這一場把代價量到了」才不能 —— 給同一台機器上的 A/B 用：
                     兩支臂都在赤字下，但**只有付了代價的那一支**其 t/s 含未計價的置換成本。
    """
    v, reasons, _m = judge(prod, mode=mode, **kw)
    if v == "REFUSE":
        return True, ["REFUSE：%s" % "；".join(reasons)]
    if v != "OVERBUDGET":
        return False, []
    if mode == "paid":
        paid = any("把代價量到了" in r for r in reasons)
        return (bool(paid), reasons if paid else [])
    return True, reasons


def iter_prods(paths):
    """一個產物檔可能是一場，也可能是一個 list（一檔多場）。壞檔不該讓閘門掛掉。"""
    for p in paths:
        try:
            with open(p, encoding="utf-8") as fh:
                doc = json.load(fh)
        except Exception as exc:  # noqa: BLE001
            yield p, None, "REFUSE", ["讀不出產物：%s" % exc]
            continue
        # 自己的帳本會被 `--glob 'Backup/**/*.json'` 掃到自己（實測：REFUSE 因此 +1）。
        # 掃描器不該把自己算成一個判不了的場。
        if isinstance(doc, dict) and "records" in doc and "counts" in doc and "mode" in doc:
            continue
        found = False
        for prod in (doc if isinstance(doc, list) else [doc]):
            if isinstance(prod, dict) and (prod.get("rows") or prod.get("metal_gate_peak")
                                          or prod.get("metal_gate") or prod.get("memory")):
                found = True
                yield p, prod, None, None
        if not found:
            yield p, None, "REFUSE", ["這個檔裡沒有任何可以判預算的產物"]


def scan(paths, mode="strict", ceiling_mib=None, ceiling_cache=None):
    out = []
    for p, prod, pre_v, pre_why in iter_prods(paths):
        if pre_v:
            out.append(dict(file=p, verdict=pre_v, reasons=pre_why, metrics={}))
            continue
        v, why, m = judge(prod, mode=mode, ceiling_mib=ceiling_mib, ceiling_cache=ceiling_cache)
        out.append(dict(file=p, verdict=v, reasons=why, metrics=m))
    return out


def tally(records):
    """回 (counts, deficits, flagged)：`deficits` 只收判得出來的場。"""
    counts, deficits, flagged = {}, [], []
    for r in records:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
        m = r["metrics"] or {}
        if isinstance(m.get("deficit_mib"), (int, float)):
            deficits.append(m["deficit_mib"])
        if r["verdict"] == "OVERBUDGET":
            flagged.append(r)
    return counts, deficits, flagged


def report(records, mode, reference=None, only_flagged=False):
    counts, deficits, flagged = tally(records)
    print("預算閘門 budget_gate：`池子 ＋ Metal 峰值 ＋ 保留 ＞ 上限` ⇒ 這一場的讀數是足跡，不是能力")
    print("  mode=%s ｜ 上限＝引擎 recommendedMaxWorkingSetSize ｜ 保留＝CGC_METAL_RESERVE_MB(%.0f)"
          " ｜ swap 成長門檻＝%.0f MiB ｜ 讀取地板＝%.0f MiB/s（冷讀校準 %.0f 的 %.0f%%）"
          % (mode, RESERVE_MIB, SWAP_GROWTH_MIB, IO_FLOOR_MIB_S, IO_COLD_MIB_S, IO_FLOOR_PCT))
    print()
    if deficits:
        print("  赤字（total−上限）: %d 場判得出來，min %.0f ／ median %.0f ／ max %.0f MiB"
              % (len(deficits), min(deficits), sorted(deficits)[len(deficits) // 2], max(deficits)))
    print("  判定: %s" % "，".join("%s=%d" % (k, counts[k]) for k in VERDICTS if counts.get(k)))
    print()
    print("  %-34s %-11s %-8s %-9s %-9s %-8s %-7s %s"
          % ("file", "verdict", "t/s", "deficit", "swap成長", "讀取", "讀取率", "備註"))
    for r in records:
        if only_flagged and r["verdict"] != "OVERBUDGET":
            continue
        m = r["metrics"] or {}
        tps = [t for t in (m.get("tps") or []) if t]
        note = "PAID" if m.get("paid") else ("UNPRICED" if m.get("unpriced") else "")
        if m.get("biggest") == "wired_mb":
            note = (note + " 最大項=模型側").strip()
        print("  %-34s %-11s %-8s %-9s %-9s %-8s %-7s %s"
              % (os.path.basename(r["file"])[:34], r["verdict"],
                 ("%.3f" % max(tps)) if tps else "—",
                 _fmt(m.get("deficit_mib")), _fmt(m.get("swap_growth_mib")),
                 _fmt(m.get("file_reads")), _fmt(m.get("io_effective_mib_s")), note))
    print()
    if flagged:
        over = [r for r in flagged if not (r["metrics"] or {}).get("paid")]
        print("  超預算 %d 場（其中帳上沒有代價的 %d 場）" % (len(flagged), len(over)))
        if mode == "strict":
            print("  ⚠ mode=strict：以上**全部**不可當能力數（含任何已認證的錨點，若它也在名單裡）。")
        else:
            print("  ⚠ mode=paid：只有代價量到的那些不可當能力數；其餘僅供同機 A/B。")
    n_bad = sum(1 for r in records if r["verdict"] in ("OVERBUDGET", "REFUSE"))
    print("VERDICT: %d/%d 在預算內（mode=%s）" % (len(records) - n_bad, len(records), mode))
    for r in records:
        if r["verdict"] in ("OVERBUDGET", "REFUSE"):
            print("  · %s → %s：%s" % (os.path.basename(r["file"]), r["verdict"],
                                       "；".join(r["reasons"])[:400]))
    return n_bad, len(records)


def _mk(pool, wired, reserve=1024.0, ceiling=11453.25, swap_growth=None, misses=None,
        file_reads=None, io=None, declared_total=True, block_key="metal_gate_peak",
        tps=(11.0,), with_memory=True, note_total=None):
    """造一個與真產物同形的場（`metal_gate(phase='peak')` 的輸出 ＋ `memory` ＋ `cache`）。"""
    items = [{"name": "pool_mb", "label": "池子", "mb": pool},
             {"name": "wired_mb", "label": "Metal 駐留", "mb": wired},
             {"name": "reserve_mb", "label": "保留", "mb": reserve}]
    block = {"ok": False, "armed": True, "phase": "peak", "items": items,
             "total_mb": (pool + wired + reserve) if declared_total else None,
             "ceiling_mb": ceiling, "ceiling_source": "引擎 stderr 本次的 recommendedMaxWorkingSetSize",
             "reserve_mb": reserve}
    if note_total is not None:
        block["total_mb"] = note_total
    prod = {block_key: block,
            "rows": [{"n_prompt": 0, "n_gen": 64, "avg_ts": t, "samples_ts": [t, t, t]}
                     for t in tps],
            "cache": {"misses": misses, "file_reads": file_reads, "io_effective_mib_s": io}}
    if with_memory:
        s = [{"t": "00:00:00", "swap_used_mb": 9000.0},
             {"t": "00:00:01", "swap_used_mb": 9000.0 + (swap_growth or 0.0)}]
        prod["memory"] = {"samples": s, "worst": {"max_wired_mb": wired}}
    return prod


def selftest():
    """fixture 一律取**真產物**的三項數字（2026-09-30 掃描時的實際值）。"""
    cases = [
        # 交付 cell 的慢臂：赤字 8752.58、swap 真的長了 4830.8、讀取 7.0 MiB/s
        ("anchor_delivery 9.702（代價量到）",
         _mk(8192.0, 10989.828125, swap_growth=4830.82, misses=4329, file_reads=26736, io=7.0,
             tps=(9.702022,)), "OVERBUDGET", True),
        # 快臂：赤字 7698.61，但整趟 swap 成長 −8.0、沒有讀取 ⇒ 代價不在帳上
        ("k_sweep_T4_K4 27.338（帳上沒代價）",
         _mk(8192.0, 9935.859375, swap_growth=-8.0, misses=0, file_reads=1704, io=0.0,
             tps=(27.337646,)), "OVERBUDGET", True),
        # 洗掉診斷旗標之後：赤字 8036.23，swap 成長 4344.43 ⇒ 代價量到（另一條路）
        ("k4_noflags 26.203（代價量到）",
         _mk(8192.0, 10273.484375, swap_growth=4344.43, misses=0, file_reads=0, io=None,
             tps=(26.202926,)), "OVERBUDGET", True),
        # ★ 已認證的 11.703 錨點也在同一個桶子裡（赤字 10097，讀取 1.0 MiB/s）
        ("已認證錨點 11.703（也在桶子裡）",
         _mk(8192.0, 12334.0, swap_growth=-46.7, misses=4784, file_reads=82146, io=1.0,
             tps=(11.70307,)), "OVERBUDGET", True),
        # 換一台機器／縮了模型時的樣子：真的在預算內 ⇒ 放行
        ("合成：在預算內",
         _mk(2048.0, 4000.0, swap_growth=0.0, misses=120, file_reads=200, io=800.0), "IN-BUDGET", False),
        # 在預算內但餘裕 108 MiB（< 512）⇒ TIGHT
        ("合成：TIGHT（餘裕 108 MiB）",
         _mk(6000.0, 4321.25, swap_growth=0.0, misses=0, file_reads=0, io=None), "TIGHT", False),
        # 舊產物（2026-09-28 的錨點就是這個形狀）：沒有 metal_gate_*，池子只在 env 裡
        # ⇒ 用 memory.worst.max_wired_mb ＋ env 的池子重算（同一口徑：metal_gate 用的就是這兩個）
        ("舊產物：靠 memory ＋ env 重算",
         {**_mk(8192.0, 11000.0, swap_growth=300.0, misses=10, file_reads=40, io=900.0),
          "metal_gate_peak": None, "env": {"CGC_EXPERT_CACHE_BYTES": "8589934592"}},
         "OVERBUDGET", True),
        # 缺件（沒有上限可讀、也沒有 memory）⇒ REFUSE，不是放行
        ("缺乏記憶體讀數 ⇒ REFUSE",
         _mk(8192.0, 9000.0, with_memory=False, block_key="none"), "REFUSE", True),
        # 產物內部不自洽：自寫 total 與三項相加差 900 MiB
        ("產物內部不自洽 ⇒ REFUSE",
         _mk(8192.0, 9000.0, note_total=8192.0 + 9000.0 + 1024.0 + 900.0), "REFUSE", True),
    ]
    ok = 0
    for name, prod, want, want_block in cases:
        v, why, m = judge(prod)
        blocked, _ = blocks_capability(prod, mode="strict")
        good = (v == want and blocked == want_block)
        ok += 1 if good else 0
        print("  %-34s → %-11s 期待 %-11s strict擋=%s 期待=%s %s"
              % (name, v, want, blocked, want_block, "✓" if good else "✗"))
    # mode=paid 只在「代價量到」時擋：同一批 fixture 換 mode 要有不同的答案
    paid_cases = [
        (_mk(8192.0, 9935.859375, swap_growth=-8.0, misses=0, file_reads=1704, io=0.0), False),
        (_mk(8192.0, 10989.828125, swap_growth=4830.82, misses=4329, file_reads=26736, io=7.0), True),
        (_mk(8192.0, 12334.0, swap_growth=-46.7, misses=4784, file_reads=82146, io=1.0), True),
        (_mk(2048.0, 4000.0, swap_growth=0.0, misses=120, file_reads=200, io=800.0), False),
    ]
    t4 = _mk(8192.0, 9935.859375, swap_growth=-8.0, misses=0, file_reads=1704, io=0.0)
    _v, _why, m4 = judge(t4)
    good = bool(m4["unpriced"]) and not m4["paid"]
    ok += 1 if good else 0
    print("  %-34s %-14s 期待 %-11s %s" % ("k_sweep_T4_K4 是 UNPRICED", "UNPRICED" if m4["unpriced"] else "—",
                                           "UNPRICED", "✓" if good else "✗"))
    for prod, want in paid_cases:
        got = blocks_capability(prod, mode="paid")[0]
        good = (got == want)
        ok += 1 if good else 0
        print("  %-34s %-14s 期待 %-11s %s"
              % ("mode=paid（同批 fixture）", "擋" if got else "放行",
                 "擋" if want else "放行", "✓" if good else "✗"))
    # scan 這一層要擋得住不是 llama-bench 的東西（整棵 Backup 樹混著字串／空 row／缺欄位）
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        junk = os.path.join(td, "junk.json")
        with open(junk, "w", encoding="utf-8") as fh:
            json.dump(["not-a-record", 42, {"rows": [{"avg_ts": None}]},
                       {"metal_gate_peak": {"items": [], "ceiling_mb": None}}], fh)
        recs = scan([junk])
        good = len(recs) == 2 and all(r["verdict"] == "REFUSE" for r in recs)
        ok += 1 if good else 0
        print("  %-34s → %-11s 期待 %-11s %s （混雜產物不該讓閘門掛掉）"
              % ("scan：混雜／缺件", recs[0]["verdict"] if recs else "—", "REFUSE", "✓" if good else "✗"))
    total = len(cases) + len(paid_cases) + 2
    print("SELFTEST %s (%d/%d)" % ("PASS" if ok == total else "FAIL", ok, total))
    return RC_OK if ok == total else RC_OVER


def main(argv=None):
    ap = argparse.ArgumentParser(description="預算閘門：必然 swap 的跑法，其讀數不是能力數")
    ap.add_argument("cmd", nargs="?", default="check", choices=["check"])
    ap.add_argument("paths", nargs="*")
    ap.add_argument("--glob", dest="glob_pat", default=None)
    ap.add_argument("--mode", choices=["strict", "paid"], default="strict",
                    help="strict（預設）：算術上必然 swap ⇒ 降級；paid：只有代價量到的才降級")
    ap.add_argument("--ceiling-mib", type=float, default=None,
                    help="上限（MiB）；預設讀產物，其次讀 Backup/metal_working_set.json")
    ap.add_argument("--ceiling-cache", default=None)
    ap.add_argument("--tally", action="store_true", help="只印總表，不逐場列（給 pipeline 用）")
    ap.add_argument("--json", dest="json_out", default=None)
    ap.add_argument("--selftest", action="store_true")
    if hasattr(ap, "parse_intermixed_args"):
        args = ap.parse_intermixed_args(argv)
    else:  # pragma: no cover
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

    records = scan(paths, mode=args.mode, ceiling_mib=args.ceiling_mib,
                   ceiling_cache=args.ceiling_cache)
    if not records:
        print("這些產物裡沒有任何可判預算的場", file=sys.stderr)
        return RC_NOTHING
    counts, deficits, flagged = tally(records)
    if args.tally:
        print("budget_gate mode=%s：%s ｜ 赤字 min/median/max = %s"
              % (args.mode, "，".join("%s=%d" % (k, counts[k]) for k in VERDICTS if counts.get(k)),
                 ("%.0f/%.0f/%.0f MiB" % (min(deficits), sorted(deficits)[len(deficits) // 2],
                                          max(deficits))) if deficits else "—"))
    else:
        report(records, args.mode)
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump({"mode": args.mode, "reserve_mib": RESERVE_MIB,
                       "swap_growth_mib": SWAP_GROWTH_MIB, "io_floor_mib_s": IO_FLOOR_MIB_S,
                       "counts": counts, "n_over": len(flagged), "records": records},
                      fh, ensure_ascii=False, indent=1)
        print("wrote %s" % args.json_out)
    return RC_OK if not flagged else RC_OVER


if __name__ == "__main__":
    sys.exit(main())
