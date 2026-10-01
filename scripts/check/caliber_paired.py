#!/usr/bin/env python3
"""caliber_paired.py —— `paired-v1` 口徑的**唯讀**統計量、負控制與翻轉帳。

立項卡（判決規則本體）：`scripts/check/charters/e-quote-caliber-paired-2026-10-01.yaml`
預註冊全文：`docs/QUOTE_CALIBER_PAIRED_PREREG_2026-10-01.md`
本檔＝交付計畫 ①②：**統計量模組（含負控制 selftest）** ＋ **唯讀重播的翻轉帳**。

它**不做**什麼（刻意寫死，避免這支工具被當成閘門）
--------------------------------------------------
- 不改 `quote_gate.py`（R2/R3 的 `max/min` 一字不動）與 `l2010_verdict.py`（A4 照舊）。
- 不判「能不能引用」給任何人用：paired-v1 **尚未採納**（預註冊、前瞻、0 回填）；本檔只把
  帳算出來、把負控制釘住，讓 operator 採納與否有材料可看。
- 不新增任何 launch：只讀 `preregistered.audit_globs` 裡的既有產物。

判決規則（唯一法源＝立項卡；跑之前寫死，看到資料後不得由本檔改）
-------------------------------------------------------------
- 效應 ＝ **per-pair 比率**（一律讀 `second / first`，與 `rho_abba_verdict` 同向）的中位數；
  ＋精確符號檢定（`alpha`；閘門用**單尾、朝多數側** —— 立項卡轉引的 5/7 讀數 p=0.227 就是
  這個值；雙尾值同時印出，且任何「單尾過、雙尾不過」的單元都會在帳裡看得到）＋ one-sample t。
- house precision rule（`rho_abba_verdict` 的原話）：配對比率只有在**大於兩臂自身的
  launch-to-launch 散布**時才准帶號；**缺一個夥伴 ⇒ 散布是未知，不是 0**。
- `min_pairs`（登錄值 8）＝ 口徑開始說話的 floor：`n_pairs < min_pairs` ⇒ `UNRESOLVED`
  —— 不是否證、也**不得**把「沒量到」唸成「量到 0」。
- 兩趟反序：比率方向都是 `second/first` ⇒ 效應臂在 AB 讀 `effect<0`、在 BA 讀 `effect>0`
  才是**效應**；兩趟都 `<0` 是**次序漂移**；不同向 ⇒ `REFUSE`（次序簽名當成機制會被讀錯）。
- 非統計條目（R4 窗口／R5 臂身分／R6 輸出見證／R7 profile／R8 格與行）**一字不動**：
  換掉的只有「離散／解析度」這個統計量 ⇒ 只有**統計理由**（`全 rep max/min=`／
  `kept rep max/min=`）可以被 paired-v1 取代；其他任何理由都原樣擋住。

翻轉帳（本檔的主端點，成功條件＝**0**）
--------------------------------------
舊判 ∈ {REFUSE, DIRTY, UNSTABLE} 而新判 ＝ QUOTABLE ⇒ **翻轉**。重播語料＝`Backup/**/*.json`
裡**有配對鍵**的產物（其餘略過並記數）：
  (a) `run`：`ab_interleave` 產物 —— 列鍵 `<arm>#r<rep>`（臂序＝`run_arms` 或首次出現序）；
  (b) `verdict`：`pair:` ＋ `arms.*.samples` 的判詞產物 —— 以**臂序＋rep 序**位置配對；
  (c) `pairs`：`[{rep, <sideA>, <sideB>}]` 的舊格式交錯跑（m4_parity／mtp_rule_* 那一族）；
  (d) A/A：兩臂身分**逐字相同**（`_env_verbatim`：env／extra_env／tag 全等，0 不被當「沒開」）
      ⇒ 判 `AA_CLEAN`／`AA_SIGN`，永不 QUOTABLE。
「新判＝QUOTABLE」要求：非統計條目可重播且全乾淨、效應顯著（符號檢定，單尾 p ≤ alpha）、
`|effect| > 兩臂自身散布`、且 `n_pairs ≥ min_pairs`。缺非統計證據（如 `pairs` 舊格式沒有
profile／cell）⇒ fail-closed `REFUSE`，不因為統計量好看就放行。

用法
----
    python3 scripts/check/caliber_paired.py --selftest
    python3 scripts/check/caliber_paired.py                      # 掃描＋印帳（唯讀）
    python3 scripts/check/caliber_paired.py --write Backup/caliber_paired_2026-10-01/audit

rc：0 ＝ 帳乾淨（翻轉 0、A/A 0 號）；1 ＝ 出現翻轉／A/A 帶號；2 ＝ 用法錯誤；
3 ＝ 掃不到任何可判單元（「沒量到」≠「量到 0」）。
"""
from __future__ import annotations

import argparse
import glob as _glob
import json
import math
import os
import statistics as st
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
CHARTER_REL = "scripts/check/charters/e-quote-caliber-paired-2026-10-01.yaml"
PREREG_DOC = "docs/QUOTE_CALIBER_PAIRED_PREREG_2026-10-01.md"

# 立項卡 `preregistered:` 的欄位（讀不到卡時用這份；值與卡逐字相同）
PREREG_FALLBACK = {
    "caliber": "paired-v1",
    "alpha": 0.05,
    "min_pairs": 8,
    "max_flips": 0,
    "max_aa_signs": 0,
    "backfill": 0,
    "effective": "prospective",
    "audit_globs": ["Backup/**/*.json"],
}

# 負控制：L20-10 兩輪（n=7／n=3）的**凍結樣本**（判詞檔指定的 `clean_ws192_{A,B}.json`；
# n=3 那一對在 archive 裡）。斷言的是「換統計量也到不了可判定」——任一輪變可引用＝自利簽名。
NEG_CTRL = {
    "n7": dict(
        a="Backup/l2010_b1b3_2026-09-30/clean_ws192_A.json",
        b="Backup/l2010_b1b3_2026-09-30/clean_ws192_B.json",
        median=1.0114, pos=5, neg=2, t=1.23, spread_max_pct=11.11,
    ),
    "n3": dict(
        a="Backup/l2010_b1b3_2026-09-30/clean_logs/archive/Q2.20261001-071038/clean_ws192_A.json",
        b="Backup/l2010_b1b3_2026-09-30/clean_logs/archive/Q3.20261001-071453/clean_ws192_B.json",
        median=1.0422, pos=3, neg=0, spread_max_pct=8.42,
    ),
}

FLIP_OLD = ("REFUSE", "DIRTY", "UNSTABLE")
OLD_CLASSES = ("QUOTABLE", "UNSTABLE", "DIRTY", "THIN", "REFUSE")
SEVERITY = {"QUOTABLE": 0, "THIN": 1, "UNSTABLE": 2, "DIRTY": 3, "REFUSE": 4, "?": 5}
ARM_MARKER = "caliber_paired audit"          # 自己的帳（避免下次掃到自己）


def _qg():
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    import quote_gate
    return quote_gate


def load_prereg(root: str = ROOT) -> tuple:
    """讀立項卡的 `preregistered:` 塊（單一定義）。讀不到 ⇒ fallback，並在報告裡記明。"""
    path = os.path.join(root, CHARTER_REL)
    try:
        import yaml  # PyYAML 已在本 repo 使用（decode_board_build.py 等）
        with open(path, encoding="utf-8") as fh:
            doc = yaml.safe_load(fh) or {}
        reg = doc.get("preregistered") or {}
        out = dict(PREREG_FALLBACK)
        for k in PREREG_FALLBACK:
            if k in reg:
                out[k] = reg[k]
        return out, path
    except Exception:  # noqa: BLE001
        return dict(PREREG_FALLBACK), path


# ──────────────────────────────────────────────────────────────── 統計量
def sign_test(pos: int, neg: int) -> tuple:
    """精確符號檢定：回 (p_two, p_one)。

    p_two ＝ 雙尾精確二項（對稱 p=1/2）；p_one ＝ 朝**多數側**的單尾。
    立項卡轉引的 n=7 讀數寫的是 p=0.227 ＝ 單尾（多數側 5/7）；判決閘門用雙尾（較嚴，
    只會更緊、不會產生翻轉），兩個值都印出來，避免「口徑對不上」的誤讀。
    """
    n = pos + neg
    if n == 0:
        return None, None
    k = min(pos, neg)
    p_two = min(1.0, 2.0 * sum(math.comb(n, i) for i in range(k + 1)) / (2 ** n))
    hi = max(pos, neg)
    p_one = sum(math.comb(n, i) for i in range(hi, n + 1)) / (2 ** n)
    return p_two, p_one


def spread_pct(vals) -> float | None:
    """house rule 的散布＝(max−min)/median×100；**單一樣本 ⇒ None（未知，不是 0）**。"""
    if not vals or len(vals) < 2:
        return None
    med = st.median(vals)
    if not med:
        return None
    return (max(vals) - min(vals)) / med * 100.0


def t_stat(vals) -> float | None:
    """one-sample t（H0: ratio 的母體平均＝1）。"""
    if not vals or len(vals) < 2:
        return None
    sd = st.stdev(vals)
    if sd == 0:
        return None
    return (st.mean(vals) - 1.0) / (sd / math.sqrt(len(vals)))


def paired_stats(ratios, first_vals, second_vals, alpha=0.05, min_pairs=8) -> dict:
    """paired-v1 的單一定義：效應、顯著性、house precision rule、floor。

    - `spread_break` ＝ house precision rule（|effect| > 兩臂自身散布）—— 立項卡對「號」的
      定義（A/A 的「無號」就是查這一條）；
    - `sign_ok` ＝ 精確符號檢定（**單尾、朝多數側**）p ≤ alpha；
    - `effective` ＝ 兩者都成立（可辨識）；`quotable` ＝ effective ＋ n ≥ min_pairs。
    """
    n = len(ratios)
    out = dict(n_pairs=n, ratios=[round(r, 6) for r in ratios])
    if n == 0:
        out.update(verdict="UNRESOLVED", reasons=["沒有配對樣本"])
        return out
    med = st.median(ratios)
    pos = sum(1 for r in ratios if r > 1)
    neg = sum(1 for r in ratios if r < 1)
    ties = n - pos - neg
    p_two, p_one = sign_test(pos, neg)
    sp_first, sp_second = spread_pct(first_vals), spread_pct(second_vals)
    spreads = [s for s in (sp_first, sp_second) if s is not None]
    spread_max = max(spreads) if spreads else None
    effect = (med - 1.0) * 100.0
    reasons = []
    if n < 2:
        effective = spread_break = sign_ok = False
        reasons.append("n_pairs=%d<2（缺夥伴：散布未知，不是 0）" % n)
    elif spread_max is None:
        effective = spread_break = sign_ok = False
        reasons.append("兩臂自身散布 UNKNOWN（缺第二個夥伴；house rule：不得當 0）")
    else:
        spread_break = abs(effect) > spread_max
        sign_ok = p_one is not None and p_one <= alpha
        effective = spread_break and sign_ok
        if not spread_break:
            reasons.append("|effect|=%.2f%% ≤ 兩臂散布 %.2f%%（house precision rule）"
                           % (abs(effect), spread_max))
        if not sign_ok:
            reasons.append("符號檢定 單尾 p=%.3f > %.2f（雙尾 %.3f）"
                           % (p_one or 1.0, alpha, p_two if p_two is not None else 1.0))
    quotable = bool(effective and n >= min_pairs)
    if effective and n < min_pairs:
        reasons.append("n_pairs=%d < min_pairs=%d ⇒ UNRESOLVED（不是否證，也不得唸成 0）"
                       % (n, min_pairs))
    out.update(
        median=med, mean=st.mean(ratios), effect_pct=effect,
        pos=pos, neg=neg, ties=ties,
        sign_p_two=p_two, sign_p_one=p_one, t=t_stat(ratios),
        spread_first_pct=sp_first, spread_second_pct=sp_second, spread_max_pct=spread_max,
        spread_break=bool(spread_break), sign_ok=bool(sign_ok),
        effective=bool(effective), quotable=quotable, reasons=reasons,
    )
    return out


def two_order_reading(effect_ab_pct, effect_ba_pct) -> tuple:
    """兩趟反序（AB＝first,second；BA＝second,first 反過來）的判讀，比率一律讀 second/first。

    - AB<0 且 BA>0 ⇒ 效應臂在兩趟裡都慢（RHO_PRICE_REAL 的形狀）；
    - AB<0 且 BA<0 ⇒ 第二趟永遠較慢 ＝ **次序漂移**，不是機制；
    - 其餘（含同向但缺一階）⇒ REFUSE：不得替一個沒有兩趟反序的讀數命名機制。
    """
    if effect_ab_pct is None or effect_ba_pct is None:
        return "NOT_READY", "缺一趟反序讀數"
    if effect_ab_pct < 0 and effect_ba_pct > 0:
        return "ARM_EFFECT", ("效應臂在兩趟裡都慢（AB %+.2f%% / BA %+.2f%%）"
                              % (effect_ab_pct, effect_ba_pct))
    if effect_ab_pct < 0 and effect_ba_pct < 0:
        return "ORDER_DRIFT", ("第二趟永遠較慢（AB %+.2f%% / BA %+.2f%%）⇒ 位置效應，不是機制"
                               % (effect_ab_pct, effect_ba_pct))
    return "REFUSE", ("兩趟不同向（AB %+.2f%% / BA %+.2f%%）⇒ 次序簽名當機制會被讀錯"
                      % (effect_ab_pct, effect_ba_pct))


# ──────────────────────────────────────────────────── 舊判重播（唯讀）
def _is_stats_reason(r: str) -> bool:
    return r.startswith("全 rep max/min=") or r.startswith("kept rep max/min=")


def classify_old(verdict: str, reasons) -> list:
    """把舊判拆成「統計理由（paired-v1 可取代）」與**擋住的其他理由**。

    只認舊閘門自己的語彙：`UNSTABLE` 可能是純統計（R2/R3）；`DIRTY/REFUSE/THIN` 一律
    整包帶走（含有臂身分／窗口／輸出見證／格行／樣本數等非統計項）。
    """
    reasons = list(reasons or [])
    if verdict == "QUOTABLE":
        return []
    if verdict == "UNSTABLE":
        return [r for r in reasons if not _is_stats_reason(r)]
    return reasons


def _tg_row(entry: dict):
    for row in entry.get("rows") or []:
        if isinstance(row, dict) and row.get("avg_ts") is not None:
            return row
    return None


def replay_entry(entry: dict) -> dict:
    """用**現行閘門**（quote_gate）重播一個產物／列的舊判——唯讀，不改它。"""
    qg = _qg()
    row = _tg_row(entry)
    if row is not None:
        v, reasons, metrics = qg.judge(entry, row)
        return dict(verdict=v, reasons=reasons, value=row.get("avg_ts"),
                    samples=list(row.get("samples_ts") or []))
    rl = qg.round_level_reason(entry)
    if rl:
        return dict(verdict="REFUSE", reasons=[rl], value=entry.get("decode_tps_median"),
                    samples=None)
    return dict(verdict="REFUSE", reasons=["讀不出可判的 row（沒有 avg_ts、也不是已登錄的輪級聚合）"],
                value=None, samples=None)


def read_arm_samples(path: str) -> list:
    """從 decode_sweep 產物取 tg 行的逐 rep 樣本（負控制用；讀不到回 []）。"""
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except Exception:  # noqa: BLE001
        return []
    for prod in (doc if isinstance(doc, list) else [doc]):
        if not isinstance(prod, dict):
            continue
        row = _tg_row(prod)
        if row is not None:
            return list(row.get("samples_ts") or [])
    return []


# ────────────────────────────────────────────────────── 單元（配對鍵的辨識）
def _launch_value(entry: dict):
    v = entry.get("decode_tps_median")
    if v is not None:
        return v
    row = _tg_row(entry)
    return row.get("avg_ts") if row else None


def _rep_of(key: str):
    if "#r" not in str(key):
        return None
    try:
        return int(str(key).split("#r")[-1])
    except ValueError:
        return None


def _arm_env(entry: dict) -> dict:
    """沿用 quote_gate 的合併規則（env＋extra_env＋tag 的 K=V）；只為了判 A/A。"""
    qg = _qg()
    fn = getattr(qg, "_arm_env", None)
    if fn:
        return fn(entry)
    return dict(entry.get("env") or {})


def _env_verbatim(entry: dict) -> dict:
    """A/A 的身份要比對**逐字**的臂身分，不是 quote_gate 的「已武裝」視圖。

    為什麼不能用 `_arm_env`：它把 `K=0` 當「沒開」（量具語意）；但 `K=0` 也可以是**機制旋鈕**
    ——實測 `ab_nospac_prodnew.json` 的 `baseline{}` vs `p25-nospac{CGC_SPAC:0}` 於是被 `_arm_env`
    看成同一支臂、誤判 A/A。空對照的定義是「兩臂身分逐字相同」，所以這裡保留 0 與所有鍵。
    """
    out = {}
    for key in ("env", "extra_env"):
        d = entry.get(key)
        if isinstance(d, dict):
            out.update({k: str(v) for k, v in d.items()})
    tag = entry.get("tag")
    if isinstance(tag, str):
        for part in tag.split(":"):
            for tok in part.split(";"):
                if "=" in tok:
                    k, _, v = tok.partition("=")
                    out[k.strip().lstrip("!")] = v.strip()
    return out


def units_of(doc) -> list:
    """把一份 JSON 拆成可判單元；沒有配對鍵 ⇒ 空（呼叫端記 UNPAIRED／SKIP）。"""
    entries = [e for e in (doc if isinstance(doc, list) else [doc]) if isinstance(e, dict)]
    if not entries:
        return []
    if any(str(e.get("product", "")).startswith(ARM_MARKER) for e in entries):
        return []                                        # 自己的帳，不當語料
    units = []

    # (b) verdict：pair ＋ arms.*.samples（臂序＋rep 序位置配對）
    for e in entries:
        arms = e.get("arms")
        pair = e.get("pair")
        if isinstance(pair, str) and isinstance(arms, dict) and len(arms) >= 2:
            items = [(k, v) for k, v in arms.items()
                     if isinstance(v, dict) and isinstance(v.get("samples"), list) and v["samples"]]
            if len(items) >= 2:
                units.append(dict(kind="verdict", pair=pair,
                                  arms=[k for k, _ in items],
                                  samples=[[float(x) for x in v["samples"]] for _, v in items],
                                  stored=[dict((v.get("quote") or {})) for _, v in items],
                                  cells=[v.get("cell") for _, v in items],
                                  paths=[v.get("path") for _, v in items],
                                  old_raw=e.get("verdict"), n_declared=len(items)))
                return units

    # (c) pairs：舊格式交錯跑（[{rep, sideA:{...}, sideB:{...}}]）
    for e in entries:
        pairs = e.get("pairs")
        if not isinstance(pairs, list) or len(pairs) < 1 or not isinstance(pairs[0], dict):
            continue
        sides = [k for k in pairs[0] if k != "rep" and isinstance(pairs[0][k], dict)]
        if len(sides) != 2:
            continue
        vals = {s: [] for s in sides}
        reps = []
        ok = True
        for p in pairs:
            if not isinstance(p, dict) or not isinstance(p.get("rep", 0), int):
                ok = False
                break
            reps.append(p["rep"])
            for s in sides:
                v = p[s].get("decode_tps", p[s].get("decode_tps_median", p[s].get("avg_ts")))
                if v is None:
                    ok = False
                    break
                vals[s].append(float(v))
            if not ok:
                break
        if ok and all(vals.values()):
            raw = e.get("verdict")
            raw = raw.get("verdict") or raw.get("call") if isinstance(raw, dict) else raw
            units.append(dict(kind="pairs", pair=", ".join(sides), arms=list(sides),
                              samples=[vals[sides[0]], vals[sides[1]]],
                              reps=reps, old_raw=raw, n_declared=len(pairs)))
            return units

    # (a) run：列鍵 <arm>#r<rep>（ab_interleave 的產物）
    rows_by_run = {}
    for e in entries:
        key = str(e.get("key") or "")
        arm = e.get("arm") or e.get("tag")
        if arm and _rep_of(key) is not None:
            rows_by_run.setdefault(tuple(e.get("run_arms") or ()), []).append(e)
    for run_key, rows in rows_by_run.items():
        arms = []
        for r in rows:
            a = r.get("arm") or r.get("tag")
            if a not in arms:
                arms.append(a)
        if len(arms) != 2:
            continue
        declared = list(run_key) if len(run_key) == 2 else arms
        if set(declared) != set(arms):
            continue
        by = {}
        for r in rows:
            a = r.get("arm") or r.get("tag")
            rep = _rep_of(r.get("key"))
            if rep is None or _launch_value(r) is None:
                continue
            by[(a, rep)] = r
        reps = sorted({rep for (a, rep) in by if (declared[0], rep) in by and (declared[1], rep) in by})
        if not reps:
            continue
        first_vals = [_launch_value(by[(declared[0], r)]) for r in reps if (declared[0], r) in by]
        second_vals = [_launch_value(by[(declared[1], r)]) for r in reps if (declared[1], r) in by]
        env_first = _env_verbatim(by[(declared[0], reps[0])]) if (declared[0], reps[0]) in by else {}
        env_second = _env_verbatim(by[(declared[1], reps[0])]) if (declared[1], reps[0]) in by else {}
        run_reps = next((r.get("run_reps") for r in rows if r.get("run_reps")), None)
        units.append(dict(kind="run", pair=None,
                          arms=declared, samples=[first_vals, second_vals], reps=reps,
                          envs=[env_first, env_second], run_reps=run_reps,
                          rows=[by[(declared[0], r)] for r in reps if (declared[0], r) in by] +
                               [by[(declared[1], r)] for r in reps if (declared[1], r) in by],
                          old_raw=None, n_declared=run_reps))
    return units


def judge_unit(unit: dict, alpha: float, min_pairs: int) -> dict:
    """一個單元的舊判 → paired-v1 新判。回紀錄（含逐項理由）。"""
    first_vals, second_vals = unit["samples"]
    ratios = [b / a for a, b in zip(first_vals, second_vals) if a]
    stats = paired_stats(ratios, first_vals, second_vals, alpha=alpha, min_pairs=min_pairs)
    aa = False
    aa_why = ""
    if unit["kind"] == "run":
        aa = unit.get("envs", [None, None])[0] == unit.get("envs", [None, None])[1]
        aa_why = "兩臂身分逐字相同（env／extra_env／tag 相等）⇒ 這是空對照（noise floor）"
    # 舊判（唯讀重播）與非統計擋點
    blockers, old_reasons, old_arm_verdicts = [], [], []
    if unit["kind"] == "run":
        for e in unit.get("rows") or []:
            r = replay_entry(e)
            old_arm_verdicts.append(r["verdict"])
            old_reasons += r["reasons"]
            blockers += classify_old(r["verdict"], r["reasons"])
        old = max(old_arm_verdicts, key=lambda v: SEVERITY.get(v, 5)) if old_arm_verdicts else "?"
    elif unit["kind"] == "verdict":
        for q in unit.get("stored") or []:
            v = (q or {}).get("verdict", "?")
            old_arm_verdicts.append(v)
            rs = list((q or {}).get("reasons") or [])
            old_reasons += rs
            blockers += classify_old(v, rs)
        old = max(old_arm_verdicts, key=lambda v: SEVERITY.get(v, 5)) if old_arm_verdicts else "?"
    else:  # pairs 舊格式：判詞不是引用閘門的語彙 ⇒ 非統計層無法重播
        old = unit.get("old_raw")
        old = old if old in OLD_CLASSES else "?"
        blockers = ["非統計條目無法重播（舊格式缺 profile／cell／attribution；fail-closed）"]
    if aa:
        # 立項卡對 A/A 的定義是 house rule：「未超過兩臂自身散布 ⇒ 不帶號」⇒ 用 spread_break。
        new = "AA_SIGN" if stats.get("spread_break") else "AA_CLEAN"
    elif blockers:
        new = "REFUSE"
    elif stats.get("quotable"):
        new = "QUOTABLE"
    else:
        new = "UNRESOLVED"
    complete = None
    if unit["kind"] == "run" and unit.get("run_reps"):
        complete = len(unit.get("reps") or []) == unit["run_reps"]
    elif unit["kind"] == "pairs" and unit.get("n_declared"):
        complete = len(ratios) == unit["n_declared"]
    return dict(
        kind=unit["kind"], file=unit.get("file"), pair=unit.get("pair"), arms=list(unit["arms"]),
        n_pairs=len(ratios), aa=aa, aa_why=aa_why, complete=complete,
        old=old, old_arm_verdicts=old_arm_verdicts,
        old_reasons=sorted(set(old_reasons)), old_raw=unit.get("old_raw"),
        new=new,
        flip=(old in FLIP_OLD and new == "QUOTABLE"),
        flip_if_floor_lowered=bool(old in FLIP_OLD and stats.get("effective") and not blockers),
        tightened=(old == "QUOTABLE" and new != "QUOTABLE"),
        blockers=sorted(set(blockers)), stats=stats,
    )


def attach_cross_order(units: list) -> None:
    """同一組臂名、兩趟反序的單元 ⇒ 附上兩趟讀法（沒有兩趟就標 single-order）。"""
    groups = {}
    for u in units:
        if u["kind"] == "run":
            groups.setdefault(frozenset(u["arms"]), []).append(u)
    for arms, us in groups.items():
        if len(us) != 2:
            continue
        a, b = us
        ea, eb = a["stats"].get("effect_pct"), b["stats"].get("effect_pct")
        if not ea or not eb:
            continue
        # AB／BA＝兩份產物裡 arms 的**執行序**相反
        if list(a["arms"]) == list(reversed(b["arms"])):
            reading, why = two_order_reading(ea, eb)
            a["cross_order"] = dict(with_file=b["file"], reading=reading, why=why)
            b["cross_order"] = dict(with_file=a["file"], reading=reading, why=why)


# ───────────────────────────────────────────────────────────── 掃描（唯讀）
def has_samples(doc) -> bool:
    for e in (doc if isinstance(doc, list) else [doc]):
        if not isinstance(e, dict):
            continue
        if isinstance(e.get("samples_ts"), list) and e["samples_ts"]:
            return True
        for row in e.get("rows") or []:
            if isinstance(row, dict) and row.get("samples_ts"):
                return True
        arms = e.get("arms")
        if isinstance(arms, dict) and any(isinstance(v, dict) and v.get("samples")
                                          for v in arms.values()):
            return True
    return False


def scan(globs, alpha: float, min_pairs: int, root: str = ROOT) -> dict:
    files = sorted({os.path.normpath(p) for g in globs
                    for p in _glob.glob(os.path.join(root, g), recursive=True)})
    acc = dict(files=len(files), unreadable=[], skipped=0, unpaired=[], units=[])
    for path in files:
        rel = os.path.relpath(path, root)
        try:
            with open(path, encoding="utf-8") as fh:
                doc = json.load(fh)
        except Exception:  # noqa: BLE001
            acc["unreadable"].append(rel)
            continue
        units = units_of(doc)
        if not units:
            if has_samples(doc):
                acc["unpaired"].append(rel)
            else:
                acc["skipped"] += 1
            continue
        for u in units:
            u["file"] = rel
            acc["units"].append(judge_unit(u, alpha, min_pairs))
    attach_cross_order(acc["units"])
    return acc


def summarize(acc: dict, prereg: dict) -> dict:
    units = acc["units"]
    flips = [u for u in units if u["flip"]]
    aa = [u for u in units if u["aa"]]
    aa_signs = [u for u in aa if u["new"] == "AA_SIGN"]          # 立項卡的「號」＝超出散布
    aa_effective = [u for u in aa if u["stats"].get("effective")]  # 完整可辨識（⊆ 前者）
    out = dict(
        flips=len(flips), flip_files=[u["file"] for u in flips],
        flips_if_floor_lowered=sum(1 for u in units if u["flip_if_floor_lowered"]),
        aa_units=len(aa), aa_signs=len(aa_signs), aa_effective=len(aa_effective),
        aa_certified=sum(1 for u in aa if u.get("complete") and u["n_pairs"] >= prereg["min_pairs"]),
        aa_below_floor=sum(1 for u in aa if u["n_pairs"] < prereg["min_pairs"]),
        tightened=sum(1 for u in units if u["tightened"]),
        paired_units=len(units),
    )
    out["verdict"] = ("CALIBRATION_CLEAN"
                      if out["flips"] <= prereg["max_flips"]
                      and out["aa_signs"] <= prereg["max_aa_signs"]
                      else "CALIBRATION_VIOLATION")
    return out


def render_md(rep: dict) -> str:
    c, s = rep["counts"], rep["summary"]
    lines = [
        "# paired-v1 翻轉帳（唯讀重播；`caliber_paired.py`）",
        "",
        "> 本帳是**材料**，不是採納：paired-v1 預註冊、前瞻、**0 回填**（%s 為單一定義的預註冊全文）。" % PREREG_DOC,
        "> 判詞只寫進本報告，**不改任何既有檔案**；閘門（`quote_gate` R2/R3、`l2010_verdict` A4）行為 0 改動。",
        "",
        "產物：`%s`；時戳 %s；root `%s`" % (ARM_MARKER, rep["at"], rep["root"]),
        "立項卡：`%s`（preregistered: alpha=%s、min_pairs=%s、max_flips=%s、backfill=%s）"
        % (rep["charter"], rep["prereg"]["alpha"], rep["prereg"]["min_pairs"],
           rep["prereg"]["max_flips"], rep["prereg"]["backfill"]),
        "",
        "## 帳",
        "",
        "| 項 | 值 |",
        "|---|---|",
        "| 掃描檔數（%s） | %d |" % ("＋".join(rep["globs"]), c["files"]),
        "| 讀不出 | %d |" % len(c["unreadable"]),
        "| 無逐 rep 樣本且無配對鍵（略過） | %d |" % c["skipped"],
        "| 有樣本但無配對鍵（UNPAIRED，單獨列帳） | %d |" % c["unpaired"],
        "| 可判配對單元 | %d |" % c["paired_units"],
        "| **翻轉（REFUSE/DIRTY/UNSTABLE → QUOTABLE）** | **%d** |" % s["flips"],
        "| 其中 A/A 空對照 | %d（帶號 %d、其中完整可辨識 %d；達 min_pairs 認證 %d；低於 floor %d） |"
        % (s["aa_units"], s["aa_signs"], s["aa_effective"], s["aa_certified"], s["aa_below_floor"]),
        "| 若把 floor 降到 2 對的翻轉（反事實，僅供參考） | %d |" % s["flips_if_floor_lowered"],
        "| 舊判 QUOTABLE → 新判不引用（收緊） | %d |" % s["tightened"],
        "",
        "判決語（以預註冊門檻為準）：**%s**" % s["verdict"],
        "",
        "## 可判單元（逐筆）",
        "",
        "| 檔案 | 型 | 臂 | n | 舊判 | 新判 | 中位比率 | 效應% | 兩臂散布% | 符號p(雙/單) | 旗標 |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for u in rep["units"]:
        st_ = u["stats"]
        flags = []
        if u["flip"]:
            flags.append("**翻轉**")
        if u["aa"]:
            flags.append("A/A")
        if u.get("complete") is False:
            flags.append("未完成")
        if u.get("cross_order"):
            flags.append("兩趟:%s" % u["cross_order"]["reading"])
        p2 = "%.3f" % st_["sign_p_two"] if st_.get("sign_p_two") is not None else "—"
        p1 = "%.3f" % st_["sign_p_one"] if st_.get("sign_p_one") is not None else "—"
        lines.append("| %s | %s | %s | %d | %s | %s | %s | %s | %s | %s/%s | %s |" % (
            u["file"], u["kind"], "+".join(u["arms"]), u["n_pairs"], u["old"], u["new"],
            ("%.4f" % st_["median"]) if st_.get("median") else "—",
            ("%+.2f" % st_["effect_pct"]) if st_.get("effect_pct") is not None else "—",
            ("%.2f" % st_["spread_max_pct"]) if st_.get("spread_max_pct") is not None else "UNKNOWN",
            p2, p1, "；".join(flags) or "—"))
    if rep["unpaired_sample"]:
        lines += ["", "## UNPAIRED 樣本（前 %d 筆，共 %d）" % (len(rep["unpaired_sample"]), c["unpaired"]), ""]
        lines += ["- `%s`" % p for p in rep["unpaired_sample"]]
    if c["unreadable"]:
        lines += ["", "## 讀不出（%d）" % len(c["unreadable"]), ""]
        lines += ["- `%s`" % p for p in c["unreadable"][:50]]
    lines += ["", "## 處置（UNPAIRED 與略過的分開）", "",
              "- UNPAIRED＝有逐 rep 樣本但沒有配對鍵（臂序／rep 序／`<arm>#r<rep>`）：**不得**當獨立樣本",
              "  混進配對統計（預註冊 §5）；要補就補一趟宣告 caliber 的 `ab_interleave` 交錯跑再重播。",
              "- 略過＝連逐 rep 樣本都沒有（與本端點無關的產物）。「沒量到」≠「量到 0」。"]

    return "\n".join(lines) + "\n"


# ────────────────────────────────────────────────────────────── selftest
def selftest() -> int:
    ok = []

    def case(name, cond, detail=""):
        ok.append((name, bool(cond), detail))

    # 1) 符號檢定：5/7 的兩個讀數（立項卡轉引 p=0.227 ＝ 單尾）
    p2, p1 = sign_test(5, 2)
    case("sign test 5/7 雙尾≈0.453", abs(p2 - 0.453125) < 1e-4, p2)
    case("sign test 5/7 單尾≈0.227（立項卡轉引的那個）", abs(p1 - 0.2265625) < 1e-4, p1)
    case("sign test 7/7 雙尾<alpha、6/7 雙尾>alpha", sign_test(7, 0)[0] < 0.05 < sign_test(6, 1)[0],
         (sign_test(7, 0)[0], sign_test(6, 1)[0]))
    case("閘門用單尾（註冊值 0.227）：7/8 單尾 0.035≤α 而雙尾 0.070>α —— 兩個都印",
         sign_test(7, 1)[1] <= 0.05 < sign_test(7, 1)[0], (sign_test(7, 1)[1], sign_test(7, 1)[0]))

    # 2) house rule：單一樣本 ⇒ UNKNOWN，不是 0
    case("單一樣本 ⇒ 散布 UNKNOWN（不是 0）", spread_pct([12.0]) is None and spread_pct([]) is None)

    # 3) 合成 null：同分布、12 對 ⇒ 不帶號
    import random
    rnd = random.Random(7)
    # 單尾過、雙尾不過的合成單元（7/8 同向；門檻用單尾，兩值都印）
    f8 = [12.0 + rnd.uniform(-0.02, 0.02) for _ in range(8)]
    g8 = [v * 1.50 for v in f8]               # 效應 50%（要讓 7/8 同向時第二臂自身散布仍小於它）
    g8[3] = f8[3] * 0.99                     # 第 4 對反向 ⇒ 7/8 同向
    f = [12.0 + rnd.uniform(-0.3, 0.3) for _ in range(12)]
    g = [12.0 + rnd.uniform(-0.3, 0.3) for _ in range(12)]
    null = paired_stats([b / a for a, b in zip(f, g)], f, g)
    case("合成 null 無號（house rule＋符號檢定）", null["effective"] is False and null["quotable"] is False,
         (null["effect_pct"], null["sign_p_two"]))

    # 4) 合成效應：第二臂穩定快 20% ⇒ 顯著且可引用（證明尺能讀）
    f = [12.0 + rnd.uniform(-0.05, 0.05) for _ in range(12)]
    g = [v * 0.8 for v in f]
    eff = paired_stats([b / a for a, b in zip(f, g)], f, g)
    case("合成 20% 效應 ⇒ effective 且 quotable（n=12≥8）",
         eff["effective"] and eff["quotable"] and eff["effect_pct"] < -10, eff["effect_pct"])
    # 單尾／雙尾分歧確實會發生：7/8 同向、效應遠超散布 ⇒ effective（單尾）且標出雙尾值
    odd = paired_stats([b / a for a, b in zip(f8, g8)], f8, g8)
    case("7/8 同向：單尾過⇒effective，雙尾 0.070 也印在帳裡",
         odd["effective"] and odd["sign_ok"] and odd["sign_p_one"] <= 0.05 < odd["sign_p_two"],
         (odd["sign_p_one"], odd["sign_p_two"], odd["sign_p_two"]))

    # 5) 精度隨 n_pairs ≈1/√n 縮（容差 1.5×）
    def disp(n_pairs, k=300):
        rnd2 = random.Random(99 + n_pairs)
        meds = []
        for _ in range(k):
            a = [12.0 + rnd2.uniform(-0.3, 0.3) for _ in range(n_pairs)]
            b = [12.0 + rnd2.uniform(-0.3, 0.3) for _ in range(n_pairs)]
            meds.append(st.median([y / x for x, y in zip(a, b)]))
        return st.stdev(meds)
    ratio = disp(9) / disp(36)
    case("精度 ≈1/√n（n=9 vs n=36 的散布比 ≈2，容差 1.5×）", 2 / 1.5 <= ratio <= 2 * 1.5, ratio)

    # 6) 負控制：L20-10 n=7（凍結樣本；判「不成立」）
    nc = NEG_CTRL["n7"]
    sa, sb = read_arm_samples(nc["a"]), read_arm_samples(nc["b"])
    case("n=7 凍結樣本讀得到（%d/%d）" % (len(sa), len(sb)), len(sa) == len(sb) == 7,
         (nc["a"], nc["b"]))
    if len(sa) == len(sb) == 7:
        st7 = paired_stats([b / a for a, b in zip(sa, sb)], sa, sb)
        case("n=7 中位比率≈1.0114（立項卡轉引）", abs(st7["median"] - nc["median"]) < 5e-4, st7["median"])
        case("n=7 方向 5/7、單尾 p≈0.227、t≈1.23",
             (st7["pos"], st7["neg"]) == (nc["pos"], nc["neg"])
             and abs(st7["sign_p_one"] - 0.2266) < 5e-4 and abs(st7["t"] - nc["t"]) < 0.02,
             (st7["pos"], st7["neg"], st7["sign_p_one"], st7["t"]))
        case("n=7 兩臂散布≈11.11%（A 臂）", abs(st7["spread_first_pct"] - nc["spread_max_pct"]) < 0.1,
             st7["spread_first_pct"])
        case("n=7 負控制：不成立（spread_break False、effective False、quotable False）",
             st7["effective"] is False and st7["quotable"] is False
             and st7["spread_break"] is False and st7["sign_p_one"] > 0.05)
    else:
        case("n=7 凍結樣本讀得到", False, "樣本不在樹上（fail-closed）")

    # 7) 負控制：L20-10 n=3
    nc3 = NEG_CTRL["n3"]
    sa3, sb3 = read_arm_samples(nc3["a"]), read_arm_samples(nc3["b"])
    if len(sa3) == len(sb3) == 3:
        st3 = paired_stats([b / a for a, b in zip(sa3, sb3)], sa3, sb3)
        case("n=3 負控制：中位比率≈1.0422、不成立",
             abs(st3["median"] - nc3["median"]) < 1e-3 and st3["effective"] is False
             and st3["quotable"] is False, (st3["median"], st3["effect_pct"], st3["spread_max_pct"]))
    else:
        case("n=3 負控制樣本讀得到", False, "樣本不在樹上（fail-closed）")

    # 8) 舊判拆解：UNSTABLE 只有統計理由 ⇒ 無擋點；帶窗口理由 ⇒ 擋點
    case("classify_old：UNSTABLE 純統計 ⇒ 無擋點",
         classify_old("UNSTABLE", ["全 rep max/min=1.120>1.10"]) == [])
    case("classify_old：UNSTABLE 帶窗口理由 ⇒ 擋點保留",
         classify_old("UNSTABLE", ["全 rep max/min=1.12>1.10", "attribution=swap≠none"]) != [])
    case("classify_old：DIRTY／REFUSE 整包帶走", classify_old("DIRTY", ["attribution=swap≠none"]) != []
         and classify_old("REFUSE", ["聚合單元：輪級聚合"]) != [])

    # 9) 翻轉偵測器本身會響（否則 0 翻轉是空的）
    def _mk_unit(kind, arms, samples, old, stored=None, envs=None, reps=None):
        u = dict(kind=kind, pair="t", arms=arms, samples=samples, old_raw=None,
                 n_declared=reps)
        if kind == "verdict":
            u["stored"] = stored
        else:
            u["rows"] = []
            u["envs"] = envs or [None, None]
            u["run_reps"] = reps
        return u
    f = [12.0 + rnd.uniform(-0.05, 0.05) for _ in range(12)]
    g = [v * 0.75 for v in f]
    u = _mk_unit("verdict", ["A", "B"], [f, g],
                 old=None, stored=[{"verdict": "UNSTABLE", "reasons": ["全 rep max/min=1.15>1.10"]},
                                   {"verdict": "QUOTABLE", "reasons": []}])
    j = judge_unit(u, 0.05, 8)
    case("翻轉偵測器：舊 UNSTABLE（純統計）→ 新 QUOTABLE ⇒ flip=True",
         j["flip"] is True and j["new"] == "QUOTABLE", (j["old"], j["new"]))
    case("翻轉偵測器：舊 QUOTABLE → 新不引用 ⇒ tightened、不是 flip",
         judge_unit(_mk_unit("verdict", ["A", "B"], [f, f],
                             None, stored=[{"verdict": "QUOTABLE", "reasons": []},
                                           {"verdict": "QUOTABLE", "reasons": []}]),
                    0.05, 8)["tightened"] is True)
    # n<min_pairs 的有效效應 ⇒ 不叫 QUOTABLE，但反事實帳要記到
    # （n=4 時雙尾符號檢定最低只能到 0.125 ⇒ 用 n=6：6/6 的雙尾 p=0.031 ≤ 0.05）
    f6 = [12.0 + rnd.uniform(-0.05, 0.05) for _ in range(6)]
    g6 = [v * 0.75 for v in f6]
    u6 = _mk_unit("verdict", ["A", "B"], [f6, g6],
                  None, stored=[{"verdict": "UNSTABLE", "reasons": ["全 rep max/min=1.11>1.10"]},
                                {"verdict": "QUOTABLE", "reasons": []}])
    j6 = judge_unit(u6, 0.05, 8)
    case("n=6 有效效應：registered 不翻轉，反事實帳記 flip_if_floor_lowered",
         j6["flip"] is False and j6["flip_if_floor_lowered"] is True and j6["new"] == "UNRESOLVED",
         (j6["new"], j6["flip_if_floor_lowered"]))

    # 10) A/A 判別與「未完成」標記（8 對、1.5 倍的假效應 ⇒ 符號檢定也會響）
    ff = [12.0 + rnd.uniform(-0.05, 0.05) for _ in range(8)]
    ua = _mk_unit("run", ["rho-on", "rho-on-b"], [ff, [v * 1.5 for v in ff]],
                  None, envs=[{"CGC_RHO_PROBE": "1"}, {"CGC_RHO_PROBE": "1"}], reps=10)
    ua["rows"] = [dict(key="rho-on#r%d" % i, arm="rho-on", decode_tps_median=ff[i])
                   for i in range(8)] + \
                 [dict(key="rho-on-b#r%d" % i, arm="rho-on-b", decode_tps_median=ff[i] * 1.5)
                  for i in range(8)]
    ua["reps"] = list(range(8))
    ja = judge_unit(ua, 0.05, 8)
    case("A/A 帶號 ⇒ AA_SIGN（永不 QUOTABLE）；run_reps=10、實際 8 ⇒ 標未完成",
         ja["aa"] and ja["new"] == "AA_SIGN" and ja["flip"] is False and ja["complete"] is False,
         (ja["aa"], ja["new"], ja["complete"]))
    ub = dict(ua)
    ub["envs"] = [{"CGC_RHO_PROBE": "1"}, {"X": "1"}]
    case("A/A 判別靠身分逐字相同（env 不同 ⇒ 不是 A/A）",
         judge_unit(ub, 0.05, 8)["aa"] is False)

    # 11) 兩趟反序讀法（mirror-image 陷阱：AB<0 & BA<0 是漂移，不是效應）
    case("兩趟：AB<0 & BA>0 ⇒ ARM_EFFECT", two_order_reading(-3.0, 4.0)[0] == "ARM_EFFECT")
    case("兩趟：都 <0 ⇒ ORDER_DRIFT（位置效應，不是機制）",
         two_order_reading(-3.0, -2.0)[0] == "ORDER_DRIFT")
    case("兩趟：不同向 ⇒ REFUSE（不得命名機制）", two_order_reading(2.0, -1.0)[0] == "REFUSE")

    # 12) 單元辨識：列鍵／verdict／pairs 三格式
    doc = [dict(key="a#r1", arm="a", decode_tps_median=1.0, env={}),
           dict(key="b#r1", arm="b", decode_tps_median=1.1, env={"K": "1"})]
    case("units_of：扁平列（列鍵＋臂）⇒ 1 個 run 單元", len(units_of(doc)) == 1)
    case("units_of：缺配對鍵（無 #rN）⇒ 0 單元", units_of([dict(tag="solo", rows=[])]) == [])
    case("units_of：pair＋arms.samples ⇒ 1 個 verdict 單元",
         len(units_of(dict(pair="clean", arms={"A": {"samples": [1.0, 2.0]},
                                               "B": {"samples": [1.1, 2.1]}}))) == 1)
    case("units_of：pairs 舊格式 ⇒ 1 個 pairs 單元",
         len(units_of(dict(pairs=[dict(rep=i, off={"decode_tps": 12.0 + i},
                                       on={"decode_tps": 11.0 + i}) for i in range(3)]))) == 1)
    case("units_of：自己的帳（product 標記）⇒ 略過",
         units_of([dict(product=ARM_MARKER + " (read-only)")]) == [])

    bad = [t for t in ok if not t[1]]
    for name, good, got in ok:
        print("  [%s] %s%s" % ("PASS" if good else "FAIL", name,
                               "" if good else "  -> %r" % (got,)))
    print("\nselftest: %d/%d PASS" % (len(ok) - len(bad), len(ok)))
    return 0 if not bad else 1


# ────────────────────────────────────────────────────────────────── main
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="paired-v1 唯讀統計量／負控制／翻轉帳")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--glob", action="append", default=None, help="覆寫 audit_globs（可多個）")
    ap.add_argument("--write", default="", help="寫 <path>.json ＋ <path>.md（唯讀輸入）")
    ap.add_argument("--root", default=ROOT)
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    prereg, charter_path = load_prereg(args.root)
    globs = args.glob or list(prereg["audit_globs"])
    t0 = time.time()
    acc = scan(globs, float(prereg["alpha"]), int(prereg["min_pairs"]), root=args.root)
    summary = summarize(acc, prereg)
    rep = dict(product=ARM_MARKER + " (read-only)", at=time.strftime("%F %T %z"),
               root=args.root, charter=os.path.relpath(charter_path, args.root),
               prereg={k: prereg[k] for k in ("caliber", "alpha", "min_pairs", "max_flips",
                                              "max_aa_signs", "backfill", "effective")},
               globs=globs,
               counts=dict(files=acc["files"], unreadable=acc["unreadable"],
                           skipped=acc["skipped"], unpaired=len(acc["unpaired"]),
                           paired_units=len(acc["units"])),
               summary=summary, units=acc["units"],
               unpaired_sample=acc["unpaired"][:40],
               unpaired_disposition=("有樣本、無配對鍵 ⇒ UNPAIRED，不混進配對統計；"
                                     "要補就補一趟宣告 caliber 的 ab_interleave 交錯跑再重播"),
               elapsed_s=round(time.time() - t0, 1))
    print("paired-v1 翻轉帳（唯讀；法源 %s）" % CHARTER_REL)
    print("  globs: %s" % " ".join(globs))
    print("  檔數 %d ｜ 讀不出 %d ｜ 略過 %d ｜ UNPAIRED %d ｜ 可判單元 %d（%.1fs）"
          % (acc["files"], len(acc["unreadable"]), acc["skipped"], len(acc["unpaired"]),
             len(acc["units"]), rep["elapsed_s"]))
    for u in acc["units"]:
        st_ = u["stats"]
        tags = []
        if u["flip"]:
            tags.append("FLIP")
        if u["aa"]:
            tags.append("A/A")
        if u.get("complete") is False:
            tags.append("未完成")
        if u.get("cross_order"):
            tags.append("兩趟=%s" % u["cross_order"]["reading"])
        print("  %-72s %-8s n=%-2d %-10s -> %-11s med=%s eff=%s sp=%s %s"
              % (u["file"], u["kind"], u["n_pairs"], u["old"], u["new"],
                 ("%.4f" % st_["median"]) if st_.get("median") else "—",
                 ("%+.2f%%" % st_["effect_pct"]) if st_.get("effect_pct") is not None else "—",
                 ("%.2f%%" % st_["spread_max_pct"]) if st_.get("spread_max_pct") is not None else "UNKNOWN",
                 " ".join(tags)))
        if u["new"] not in ("QUOTABLE", "AA_CLEAN") and st_.get("reasons"):
            print("        ↳ %s" % "；".join(st_["reasons"]))
    print("\n  翻轉（REFUSE/DIRTY/UNSTABLE → QUOTABLE）：%d（門檻 %d）｜ A/A 帶號：%d（門檻 %d；"
          "其中完整可辨識 %d）"
          % (summary["flips"], prereg["max_flips"], summary["aa_signs"], prereg["max_aa_signs"],
             summary["aa_effective"]))
    print("  A/A：%d 單元（達 min_pairs 認證 %d；低於 floor %d）｜ 反事實（floor→2）翻轉 %d"
          % (summary["aa_units"], summary["aa_certified"], summary["aa_below_floor"],
             summary["flips_if_floor_lowered"]))
    print("  收緊（舊 QUOTABLE→新不引用）：%d" % summary["tightened"])
    print("\n判決語：%s" % summary["verdict"])
    if args.write:
        base = os.path.join(args.root, args.write)
        os.makedirs(os.path.dirname(base), exist_ok=True)
        with open(base + ".json", "w", encoding="utf-8") as fh:
            json.dump(rep, fh, indent=1, ensure_ascii=False)
            fh.write("\n")
        with open(base + ".md", "w", encoding="utf-8") as fh:
            fh.write(render_md(rep))
        print("\n寫出 %s.json ＋ %s.md" % (base, base))
    if not acc["units"] and not acc["unpaired"]:
        return 3
    return 0 if summary["verdict"] == "CALIBRATION_CLEAN" else 1


if __name__ == "__main__":
    sys.exit(main())
