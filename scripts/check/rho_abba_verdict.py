#!/usr/bin/env python3
"""rho_abba_verdict.py -- turn the two-order rho-price run into a quotable verdict.

Why this exists rather than an eye on the t/s table
--------------------------------------------------
`docs/S3B_RHO_COST_2026-09-30.md` §5 pre-registered a **two-order** design because
`ab_interleave.py` always runs A before B inside every pair, and on this fan-cooled box
"the second launch of a pair is hotter" is a first-order effect of the same size as the
effect under test. The pre-registered reading was written in terms of the **paired ratio
`second / first`** -- which is literally what `ab_interleave.py` prints as `treat / base`
(`base, test = arms` in file order, so `test` is the arm listed second):

    |               | order in each pair | ratio = second/first |
    |---------------|--------------------|----------------------|
    | AB run        | off, then on       | on / off             |
    | BA run        | on,  then off      | off / on             |

    * both ratios < 1  (the second arm is always the slower one)
      ==> that is the ORDER / drift signature, whatever the arm: not rho.
    * AB < 1 and BA > 1  (on is slow whichever slot it occupies)
      ==> rho really is expensive.

Note the trap: this is *not* "on/off in both runs". Had we normalized to `on/off`, the
drift signature would read as AB<1 & BA>1 and the rho signature as "both <1" -- the two
tables are exact mirror images, and reading the wrong one signs noise as a price. The
`ratio_orientation` field in the output records which table this run was read with.

The second rule is the house precision rule, already canonical in
`decode_carrier_ab.py`: a paired ratio may only carry a sign when its magnitude exceeds
the arms' own launch-to-launch spread, and **a missing partner makes the spread unknown,
not zero**. On this box that rule is what stops a single hot/cold launch from being
quoted as a mechanism price.

Fail-closed: any disagreement (unknown spread, arm identity wrong, fewer than two pairs
per order) returns REFUSE / NOT_READY and exits non-zero. Exit 0 means the verdict is one
of `RHO_PRICE_REAL` / `RHO_ORDER_DRIFT`, i.e. the box actually answered.

Usage
    python3 scripts/check/rho_abba_verdict.py --selftest
    python3 scripts/check/rho_abba_verdict.py \
        --ab Backup/s3b_abba_2026-09-30/rho_AB.json \
        --ba Backup/s3b_abba_2026-09-30/rho_BA.json --write Backup/s3b_abba_2026-09-30/verdict
"""
from __future__ import annotations

import argparse
import json
import statistics as st
import sys
from pathlib import Path

# The step budget the doc's decision sentence was written against (88.3 ms/step = the
# 5976-slot soft pool at the 2026-09-30 profile). Kept as a named constant so a future
# profile cannot silently reprice the verdict.
STEP_MS = 88.3
WINDOW_MS = (1.30, 1.59)      # §5: Δ <= window => rho's price is affordable (net positive)
# The registry is the fail-closed part: an artifact whose arm carries an env it should not is
# refused before any arithmetic. `rho-off` is deliberately env-identical to `baseline`, which is
# what makes the A/A null (`--aa --ab <baseline,rho-off run>`) possible without a new arm.
PAIR_ARM = {"rho-on": {"CGC_RHO_PROBE": "1"}, "rho-off": {}, "baseline": {}}


def _arm_of(rows):
    """The run's arm order = order of first appearance (ab_interleave appends in run order)."""
    order = []
    for r in rows:
        a = r.get("arm")
        if a not in order:
            order.append(a)
    return order


def _pairs(rows, order):
    """[(rep, first_row, second_row)] -- the pair as *executed*, i.e. (base, test)."""
    if len(order) != 2:
        return []
    by = {}
    for r in rows:
        rep = r.get("rep")
        if rep is None:
            # ab_interleave always records rep; fall back to the #rN suffix of the key.
            key = str(r.get("key", ""))
            rep = int(key.split("#r")[-1]) if "#r" in key else None
        by[(r.get("arm"), rep)] = r
    out = []
    for rep in sorted({k[1] for k in by if k[1] is not None}):
        a, b = by.get((order[0], rep)), by.get((order[1], rep))
        if a and b:
            out.append((rep, a, b))
    return out


def _spread_pct(vals):
    """House rule: an arm seen once has UNKNOWN spread (None), never zero."""
    if len(vals) < 2:
        return None
    med = st.median(vals)
    return (max(vals) - min(vals)) / med * 100.0 if med else None


def _counters(row):
    """The pool's own bookkeeping -- must not move when the probe only observes."""
    return tuple(row.get(k) for k in ("requests", "hits", "misses", "miss_compulsory",
                                      "miss_capacity", "evictions", "file_reads", "io_bytes"))


def _counters_close(a, b, tol=5e-4, abs_slack=10):
    """Equal to within 0.05% OR 10 counts. Launch-to-launch the same workload re-runs read one
    miss (and so one 1.4 MiB file) differently -- measured 2026-09-30: 129318 vs 129319 hits,
    4613 vs 4608 evictions -- so exact equality reports that wobble as 'the probe changed the
    work'. The absolute slack is what covers the small-count fields (5 of 4613 evictions is
    0.11%, under one file read's worth of noise); the relative tolerance is what covers the big
    ones. Excluding the session's first launch, which is a different regime, nothing moves."""
    if len(a) != len(b):
        return False
    for x, y in zip(a, b):
        if x is None or y is None:
            if x != y:
                return False
            continue
        d = abs(x - y)
        if d > abs_slack and d > tol * max(abs(x), abs(y), 1):
            return False
    return True


def _round_excursion(row, floor_ratio=0.85):
    """Round-level heavy tail inside one launch, warm-up round excluded.

    A constant per-step instrumentation cost shifts a level; a per-layer SYNCHRONOUS readback
    in the eval callback can instead DRAIN the GPU pipeline, which shows up as whole rounds
    running 1.5-2.4x slower with the wall time fully accounted for (stall ~0) and the I/O rate
    normal. Those two signatures need different fixes, so the rounds are reported separately.
    """
    rr = [x.get("decode_tps") for x in (row.get("rounds") or []) if x.get("decode_tps")]
    if len(rr) < 3:
        return None
    rr = rr[1:]                      # round 0 is the explicit warm-up round
    med = st.median(rr)
    return {"median": med, "min": min(rr), "max": max(rr),
            "worst_ratio": (min(rr) / med) if med else None,
            "excursion": bool(med and min(rr) / med < floor_ratio)}


def _verdict(bad_env, ps, resolvable):
    if bad_env:
        return "refuse"
    if len(ps) < 2:
        return "not-ready"
    return "resolvable" if resolvable else "inconclusive"


TAIL_BRANCH_RULES = ("T=0 ⇒ RETRACT；兩臂都 ≥1（且 T≥2）⇒ PROBE_CLASS；只有一支 ≥2 而另一支 0"
                     " ⇒ INTERMITTENT；只有一支 1 而另一支 0 ⇒ UNRESOLVED（不再加 n，改判別設計）")


def tail_branch(with_exc, n_per_arm):
    """把 §7.8 預註冊的分支寫成機器（見 docs/S3B_RHO_COST_2026-09-30.md §7.8）。

    twins 的 env 逐字相同 ⇒ 任何不對稱都是儀器噪音；這支函式回答的是「接下來要動哪一個設計」，
    不是「ρ 貴不貴」。四条規則完整覆蓋所有可能計數（不留第四種結局沒人認領）。
    """
    counts = [with_exc.get(a, 0) for a in (n_per_arm or {}) or sorted(with_exc)]
    c_max = max(counts) if counts else 0
    c_min = min(counts) if counts else 0
    total = sum(counts)
    pair = " vs ".join(str(c) for c in counts)
    if total == 0:
        return "RETRACT", f"兩臂全乾淨（{pair}）⇒ 這條尾巴結案"
    if c_min >= 1 and total >= 2:
        return "PROBE_CLASS", (f"兩臂相近（{pair}）⇒ 尾巴屬「帶探針的發射」⇒ S2-c 的每步一次讀回"
                               "升級為必要前提；R5 的解除只能在權威 row 上量出價格")
    if c_min == 0 and c_max >= 2:
        return "INTERMITTENT", (f"只有一支掉格（{pair}）⇒ 間歇／窗外 ⇒ 撤回機制主張"
                                  "（lead 保留、不動作）")
    return "UNRESOLVED", (f"單一事件（{pair}）⇒ 不足以定罪也不足以撤回；要再前進得改判別設計"
                          "（不是再加 n）")


def _log_time(row):
    """Session order from the server log name (llama_server_YYYYMMDD_HHMMSS.log), not from
    which --json was passed first: the two artifacts are written by two separate invocations."""
    name = Path(str(row.get("log", ""))).name
    return name.split("_", 2)[-1] if name.startswith("llama_server_") else ""


def tail(rows_by_side):
    """Which arm owns the round-level excursions, and how unlikely that split is by chance.

    Reported, never used to sign a price: with 4 launches per arm it is a lead, not a proof.
    The A/A control (`--arms rho-off,rho-off-b`) is what decides whether the tail belongs to
    the box or to the probe -- an A/A pair has no mechanism to be asymmetric.
    """
    per = {}
    for _, rows in rows_by_side:
        for r in rows:
            e = _round_excursion(r)
            if e and e["excursion"]:
                per.setdefault(r.get("arm"), []).append({"key": r.get("key"), **e})
    n_launch = {}
    for _, rows in rows_by_side:
        for r in rows:
            n_launch[r.get("arm")] = n_launch.get(r.get("arm"), 0) + 1
    arms =    sorted(set(per) | set(n_launch))
    with_exc = {a: len(per.get(a, [])) for a in arms}
    p = None
    if len(arms) == 2:
        # One-sided Fisher over LAUNCHES: draw `n` launches for the enriched arm out of `total`;
        # what is P(its excursion count >= observed) if excursions were assigned at random?
        n_launch_arm = {a: n_launch.get(a, 0) for a in arms}
        # the arm that owns a larger SHARE of the excursions is the one being tested
        rate = {a: (with_exc[a] / n_launch_arm[a] if n_launch_arm[a] else 0.0) for a in arms}
        enriched = arms[0] if rate[arms[0]] >= rate[arms[1]] else arms[1]
        n, k, K = n_launch_arm[enriched], with_exc[enriched], sum(with_exc.values())
        total = sum(n_launch_arm.values())
        if 0 < total <= 12 and K:
            p = sum(_choose(K, i) * _choose(total - K, n - i) for i in range(k, min(n, K) + 1))
            p = round(p / _choose(total, n), 4)
    return {"excursions_per_arm": per, "launches_per_arm": n_launch,
            "launches_with_excursion": with_exc,
            "one_sided_exact_p": p,
            "why": "excursion = a measured round below 85% of that launch's own round median; "
                   "p is Fisher's one-sided exact test over launches, reported only as a lead "
                   "(n too small to sign anything)"}


def _choose(n, k):
    from math import comb
    return comb(n, k) if 0 <= k <= n else 0


def audit(ab_rows, ba_rows, sides):
    """Session-level no-op audit. Launch #1 of a session is a known different regime (its
    counters and even its answer md5 differ from every later launch, measured 2026-09-30),
    so it is reported separately -- but it is never silently dropped from the ratios."""
    seq = sorted([(r, lab) for lab, rows in ((sides[0], ab_rows), (sides[1], ba_rows))
                  for r in rows], key=lambda x: _log_time(x[0]))
    if not seq:
        return {"checked": False}
    first, rest = seq[0][0], [r for r, _ in seq[1:]]
    settled = [r for r in rest if r.get("log") != first.get("log")]
    first_md5 = (first.get("answer_md5_set") or [None])[0]
    later_md5 = {m for r in settled for m in (r.get("answer_md5_set") or [])}
    return {
        "checked": True,
        "first_launch": {"key": first.get("key"), "counters": _counters(first),
                         "answer_md5": first.get("answer_md5_set"),
                         "io_us_per_job": first.get("io_us_per_job")},
        "settled_launches": len(settled),
        "pool_counters_identical_after_first_launch":
            all(_counters_close(_counters(settled[0]), _counters(r)) for r in settled)
            if settled else None,
        "first_launch_is_the_outlier": (not any(_counters_close(_counters(first), _counters(r))
                                                for r in settled)) if settled else None,
        "first_launch_answer_differs": (first_md5 not in later_md5) if later_md5 else None,
        "why": ("launch #1 of a session runs a different state (its pool counters, its I/O rate and "
                "even its answer hash differ from every later launch); it is reported, and it is "
                "also the only reason a counter audit would fail -- the probe hypothesis over "
                "settled launches is the one that matters"),
    }


def run_side(rows, label):
    order = _arm_of(rows)
    ps = _pairs(rows, order)
    if len(order) != 2 or not ps:
        return {"label": label, "verdict": "not-ready",
                "why": f"arms={order} pairs={len(ps)}", "pairs": [],
                "pool_counters_noop_per_rep": {}}
    # Instrument identity: the "on" arm must be the one carrying CGC_RHO_PROBE, and nothing
    # else may differ. A mislabelled artifact would read as a price.
    bad_env = []
    for r in rows:
        want = PAIR_ARM.get(r.get("arm"))
        if want is None:
            bad_env.append(f"{r.get('key')}: unknown arm {r.get('arm')!r}")
        elif (r.get("env") or {}) != want:
            bad_env.append(f"{r.get('key')}: env={r.get('env')} want={want}")
    ratios = [b["decode_tps_median"] / a["decode_tps_median"] for _, a, b in ps]
    first, second = order
    t_first = [r["decode_tps_median"] for r in rows if r.get("arm") == first]
    t_second = [r["decode_tps_median"] for r in rows if r.get("arm") == second]
    sp_first, sp_second = _spread_pct(t_first), _spread_pct(t_second)
    med = st.median(ratios)
    effect = (med - 1.0) * 100.0
    if sp_first is None or sp_second is None:
        resolvable, drift, why = False, None, "an arm has a single launch, so its spread is UNKNOWN, not zero"
    else:
        drift = max(sp_first, sp_second)
        resolvable = abs(effect) > drift
        why = f"|{effect:+.1f}%| vs within-arm spread {drift:.1f}%"
    # Pool-level no-op audit, per rep: the two arms of a rep must show identical bookkeeping,
    # else the probe changed the work per token and no ratio may be called a price. The audit
    # deliberately does NOT drop any row -- the caller (audit()) subtracts only the session's
    # first launch, and only after showing that it is the outlier.
    per_rep_noop = {rep: _counters_close(_counters(a), _counters(b)) for rep, a, b in ps}
    return {
        "label": label,
        "order": order,
        "ratio_orientation": f"{second} / {first}",
        "pairs": [{"rep": rep, "first": a["key"], "second": b["key"],
                   "t_first": a["decode_tps_median"], "t_second": b["decode_tps_median"],
                   "ratio": rt, "io_us_per_job": [a.get("io_us_per_job"), b.get("io_us_per_job")]}
                  for (rep, a, b), rt in zip(ps, ratios)],
        "paired_ratio_median": med,
        "effect_pct": effect,
        "within_arm_spread_pct": {first: sp_first, second: sp_second},
        "resolvable": resolvable,
        "why": why,
        "verdict": _verdict(bad_env, ps, resolvable),
        "refuse_why": "; ".join(bad_env),
        "pool_counters_noop_per_rep": per_rep_noop,
        "io_us_per_job": {r["key"]: r.get("io_us_per_job") for r in rows},
        "hit_rate_pct": {r["key"]: r.get("hit_rate_pct") for r in rows},
        "answer_md5": {r["key"]: r.get("answer_md5_set") for r in rows},
        "n_launches_per_arm": {first: len(t_first), second: len(t_second)},
    }


def decide(ab, ba):
    """The pre-registered table, in ratio = second/first orientation."""
    for s in (ab, ba):
        if s.get("verdict") == "refuse":
            return "REFUSE", f"{s['label']}: {s.get('refuse_why')}"
    if ab.get("verdict") == "not-ready" or ba.get("verdict") == "not-ready":
        return "NOT_READY", (f"ab: {ab.get('why', 'ok')} / ba: {ba.get('why', 'ok')}")
    a_e, b_e = ab["effect_pct"], ba["effect_pct"]
    if not ab["resolvable"] or not ba["resolvable"]:
        return "REFUSE", (f"the box did not resolve a sign in both orders (AB {a_e:+.1f}% "
                          f"resolvable={ab['resolvable']}, BA {b_e:+.1f}% resolvable={ba['resolvable']})"
                          f" -- an unresolved order cannot be subtracted from the other one")
    if a_e < 0 and b_e > 0:
        return "RHO_PRICE_REAL", ("the rho arm is the slow one whichever slot it takes "
                                  f"(AB second/first {a_e:+.1f}%, BA second/first {b_e:+.1f}%)")
    if a_e < 0 and b_e < 0:
        return "RHO_ORDER_DRIFT", ("the second launch is the slow one in both orders, which is a "
                                   "position effect and not an arm effect")
    return "REFUSE", (f"unexpected sign pattern (AB {a_e:+.1f}%, BA {b_e:+.1f}%) -- refusing "
                      f"rather than naming a mechanism for it")


def price(ab, ba):
    """rho's price as Δms/step, from every pair that reads on-vs-off, both orders."""
    rs, step_ms = [], []
    for s in (ab, ba):
        for p in s.get("pairs") or []:
            if s.get("order") and s["order"][1] == "rho-on":
                rs.append(p["ratio"])                     # AB: on/off directly
            elif s.get("order") and s["order"][0] == "rho-on":
                rs.append(1.0 / p["ratio"])               # BA: off/on -> invert
            if s.get("order") and s["order"][0] == "rho-off":
                step_ms.append(1000.0 / p["t_first"])
    if not rs:
        return None
    base_step_ms = st.median(step_ms) if step_ms else STEP_MS
    ds = [base_step_ms * (1.0 / r - 1.0) for r in rs]
    lo, hi = min(ds), max(ds)
    if hi <= WINDOW_MS[0]:
        cls = "INSIDE_WINDOW"
    elif lo > WINDOW_MS[1]:
        cls = "ABOVE_WINDOW"
    else:
        cls = "UNCLEAR_VS_WINDOW"
    return {"n_pairs": len(rs), "ratio_on_off_median": st.median(rs),
            "delta_step_ms_median": st.median(ds), "delta_step_ms_range": [lo, hi],
            "window_ms": list(WINDOW_MS), "class": cls,
            "rule": "Δ = step_off_ms * (1/ratio_on_off - 1); the window is §5's 1.30-1.59 ms"}


def report(ab, ba, ab_rows=None, ba_rows=None):
    v, why = decide(ab, ba)
    return {"product": "rho price (two-order A/B)", "verdict": v, "verdict_why": why,
            "price": price(ab, ba), "noop_audit": audit(ab_rows or [], ba_rows or [], ("ab", "ba")),
            "tail": tail((("ab", ab_rows or []), ("ba", ba_rows or []))),
            "ab": ab, "ba": ba,
            "reading": "ratio = second/first inside each pair; drift => both < 1, "
                       "rho => AB < 1 and BA > 1",
            # [CGC 2026-09-30] 口徑：這一族是**輪級觀測值**（ABBA 自訂 rounds，不是權威 row）
            # ⇒ 引用閘門 quote_gate 的 R8c 判 REFUSE ⇒ **不可入認證表**，不論價格判詞是什麼。
            # 能入表的只有權威 row：`python3 scripts/check/cell_contract.py --cell <name>`。
            "quotability": "REFUSE（quote_gate R8c：輪級聚合 ⇒ 不可入認證表；本檔的判詞只回答"
                           "「價格可不可辨識」，不回答「能不能引用」）"}


# ---------------------------------------------------------------- selftest
def _row(arm, rep, tps, io=20000, counters=(1, 1, 1, 1, 0, 0, 1, 1), log="llama_server_20260930_190000.log"):
    return {"key": f"{arm}#r{rep}", "arm": arm, "rep": rep, "env": dict(PAIR_ARM[arm]),
            "log": log,
            "decode_tps_median": tps, "io_us_per_job": io, "hit_rate_pct": 96.6,
            "answer_md5_set": ["deadbeef"],
            **dict(zip(("requests", "hits", "misses", "miss_compulsory", "miss_capacity",
                        "evictions", "file_reads", "io_bytes"), counters))}


def selftest():
    ok = []
    # (a) drift: the second launch is slower in both orders, at a resolvable size.
    #     AB: off 12.0 -> on 10.8 (x0.900); BA: on 10.8 -> off 9.7 (x0.898); spreads small.
    ab = [_row("rho-off", 1, 12.0), _row("rho-on", 1, 10.8), _row("rho-off", 2, 12.1), _row("rho-on", 2, 10.9)]
    ba = [_row("rho-on", 1, 10.8), _row("rho-off", 1, 9.7), _row("rho-on", 2, 10.9), _row("rho-off", 2, 9.8)]
    s_ab, s_ba = run_side(ab, "ab"), run_side(ba, "ba")
    v, _ = decide(s_ab, s_ba)
    ok.append(("drift branch", v == "RHO_ORDER_DRIFT", v))
    # (b) rho is really the slow one: on slow in its own slot both times.
    ab = [_row("rho-off", 1, 12.0), _row("rho-on", 1, 10.8), _row("rho-off", 2, 12.0), _row("rho-on", 2, 10.8)]
    ba = [_row("rho-on", 1, 10.8), _row("rho-off", 1, 13.4), _row("rho-on", 2, 10.8), _row("rho-off", 2, 13.4)]
    s_ab, s_ba = run_side(ab, "ab"), run_side(ba, "ba")
    v, _ = decide(s_ab, s_ba)
    ok.append(("price branch", v == "RHO_PRICE_REAL", v))
    p = price(s_ab, s_ba)
    ok.append(("price window read", p is not None and p["class"] in
               ("INSIDE_WINDOW", "ABOVE_WINDOW", "UNCLEAR_VS_WINDOW"), str(p and p["class"])))
    # (c) effect smaller than the arms' own spread => REFUSE, not a small price.
    ab = [_row("rho-off", 1, 12.0), _row("rho-on", 1, 11.9), _row("rho-off", 2, 12.4), _row("rho-on", 2, 11.0)]
    ba = [_row("rho-on", 1, 11.8), _row("rho-off", 1, 13.5), _row("rho-on", 2, 12.6), _row("rho-off", 2, 12.9)]
    v, _ = decide(run_side(ab, "ab"), run_side(ba, "ba"))
    ok.append(("unresolved => refuse", v == "REFUSE", v))
    # (d) one pair per order is NOT_READY, never a sign.
    ab = [_row("rho-off", 1, 12.0), _row("rho-on", 1, 10.8)]
    ba = [_row("rho-on", 1, 11.8), _row("rho-off", 1, 13.5)]
    v, why = decide(run_side(ab, "ab"), run_side(ba, "ba"))
    ok.append(("single pair => not ready", v == "NOT_READY", v))
    ok.append(("unknown spread named", "UNKNOWN" in why.upper() or "unknown" in why, why))
    # (e) a mislabelled arm is refused before any arithmetic.
    ab = [_row("rho-off", 1, 12.0), _row("rho-on", 1, 10.8), _row("rho-off", 2, 12.0), _row("rho-on", 2, 10.8)]
    ab[1]["env"] = {}
    v, why = decide(run_side(ab, "ab"), run_side([_row("rho-on", 1, 10.8), _row("rho-off", 1, 13.4)], "ba"))
    ok.append(("env identity", v == "REFUSE" and "env=" in why, v))
    # (f) a probe that moved the pool counters kills the no-op claim.
    STD = (133931, 129318, 4613, 2812, 1801, 4613, 41064, 5204402176)
    WOBBLE = (133931, 129319, 4612, 2811, 1801, 4608, 41061, 5202944000)     # the measured wobble
    FIRST = (132651, 128426, 4225, 2754, 1471, 4211, 39951, 4768874496)      # session launch #1
    ab = [_row("rho-off", 1, 12.0, counters=STD), _row("rho-on", 1, 10.8, counters=FIRST),
          _row("rho-off", 2, 12.0, counters=STD), _row("rho-on", 2, 10.8, counters=FIRST)]
    s = run_side(ab, "ab")
    ok.append(("pool no-op audit", s["pool_counters_noop_per_rep"] == {1: False, 2: False},
               str(s["pool_counters_noop_per_rep"])))
    ok.append(("counter wobble tolerated",
               _counters_close(STD, WOBBLE), str(WOBBLE)))
    ok.append(("real change still caught", not _counters_close(STD, FIRST), str(FIRST)))
    # (g) an asymmetric round tail is REPORTED and pointed at the A/A control.
    ab2 = [_row("rho-off", 1, 12.0), {**_row("rho-on", 1, 12.0),
                                        "rounds": [{"round": i, "decode_tps": t} for i, t in
                                                   enumerate((8.0, 11.8, 6.0, 11.9))]},
           _row("rho-off", 2, 12.1), _row("rho-on", 2, 12.0)]
    t = tail((("ab", ab2),))
    ok.append(("tail asymmetry reported", t["launches_with_excursion"].get("rho-on") == 1
               and t["one_sided_exact_p"] is not None, str(t["launches_with_excursion"])))
    # (h) A/A null mode reads the same artifacts without inventing a price.
    nf = null_floor((("a", [_row("baseline", 1, 12.0), _row("rho-off", 1, 11.0),
                               _row("baseline", 2, 12.1), _row("rho-off", 2, 11.1)]),))
    ok.append(("aa null floor", nf["verdict"] == "NULL_FLOOR" and nf["sides"][0]["resolvable"]
               is not None, nf["sides"][0].get("why", "")))
    # (j) the pre-registered tail branches (docs/S3B_RHO_COST_2026-09-30.md §7.8), including the
    #     case that is neither "both arms" nor "all clean" -- it must not fall through silently.
    for want, wexc, n in (("RETRACT", {"a": 0, "b": 0}, {"a": 4, "b": 4}),
                          ("PROBE_CLASS", {"a": 2, "b": 2}, {"a": 4, "b": 4}),
                          ("PROBE_CLASS", {"a": 1, "b": 1}, {"a": 4, "b": 4}),
                          ("INTERMITTENT", {"a": 2, "b": 0}, {"a": 4, "b": 4}),
                          ("UNRESOLVED", {"a": 1, "b": 0}, {"a": 4, "b": 4})):
        got = tail_branch(wexc, n)[0]
        ok.append((f"tail branch {want} {wexc}", got == want, got))
    # (k) a half-finished A/A must NOT be read as "all clean": the n=4 twin run was stopped on
    #     purpose mid-way (2026-09-30 21:25) and its 0/0 is "not finished", not `RETRACT`.
    half = [{**_row("rho-on", 1, 12.0), "run_reps": 4, "run_arms": ["rho-on", "rho-off"]},
            {**_row("rho-off", 1, 12.0), "run_reps": 4, "run_arms": ["rho-on", "rho-off"]}]
    nf_half = null_floor((("aa", half),))
    ok.append(("aa incomplete not read as clean", nf_half["tail_branch"]["branch"] == "INCOMPLETE",
               str(nf_half["tail_branch"]["branch"]) + "/" + str(nf_half["complete"])))
    done = [{**_row("rho-on", 1, 12.0), "run_reps": 2}, {**_row("rho-off", 1, 12.0), "run_reps": 2},
            {**_row("rho-on", 2, 12.1), "run_reps": 2}, {**_row("rho-off", 2, 12.0), "run_reps": 2}]
    nf_done = null_floor((("aa", done),))
    ok.append(("aa complete reaches a branch", nf_done["complete"] is True
               and nf_done["tail_branch"]["branch"] == "RETRACT",
               str(nf_done["tail_branch"]["branch"])))
    # (i) the reading is stated with its orientation (the mirror-image trap).
    ok.append(("orientation recorded", run_side([_row("rho-on", 1, 1.0), _row("rho-off", 1, 1.0), _row("rho-on", 2, 1.0), _row("rho-off", 2, 1.0)], "ba")["ratio_orientation"] == "rho-off / rho-on", "n/a"))
    bad = [t for t in ok if not t[1]]
    for name, good, got in ok:
        print(f"  [{'PASS' if good else 'FAIL'}] {name}" + ("" if good else f"  -> {got}"))
    print(f"\nselftest: {len(ok) - len(bad)}/{len(ok)} PASS")
    return 0 if not bad else 1


def null_floor(rows_by_side):
    """The A/A null: two arms with IDENTICAL env (`baseline` vs `rho-off`) run as a pair.

    Whatever asymmetry they show is the floor this box+design can resolve -- it is the number
    the rho tail has to beat before 'the probe did it' becomes a statement rather than a lead.
    """
    sides = [run_side(rows, f"{lab}") for lab, rows in rows_by_side if rows]
    t = tail(tuple(rows_by_side))
    # 完整性（2026-09-30）：`ab_interleave` 每列蓋 `run_arms`／`run_reps`（設計戳記）⇒ 被中途
    # 停掉的半份資料在這裡就擋下：它的 0/0 不是「全乾淨」，是「還沒跑完」。舊產物沒有戳記
    # ⇒ `complete=None`，行為與以前相同（不倒退）。
    all_rows = [r for _, rs in rows_by_side for r in rs]
    want = {r.get("run_reps") for r in all_rows if r.get("run_reps")}
    per_arm = {}
    for r in all_rows:
        per_arm[r.get("arm")] = per_arm.get(r.get("arm"), 0) + 1
    complete = None
    if len(want) == 1 and len(per_arm) == 2:
        w = next(iter(want))
        complete = all(n == w for n in per_arm.values())
    if complete is False:
        tb = {"branch": "INCOMPLETE",
              "why": ("這一場是中途停掉的：設計 %d 趟/臂，實際 %s ⇒ 不判任何分支"
                      "（半份資料的 0/0 不是「全乾淨」）" % (next(iter(want)), per_arm)),
              "rule": TAIL_BRANCH_RULES, "complete": False}
    else:
        br, br_why = tail_branch(t["launches_with_excursion"], t["launches_per_arm"])
        tb = {"branch": br, "why": br_why, "rule": TAIL_BRANCH_RULES, "complete": complete}
    return {"sides": sides, "tail": t, "complete": complete, "tail_branch": tb,
            "why": "both arms carry the same env, so every ratio/spread/tail here is instrument "
                   "noise by construction -- it is a FLOOR, never a price",
            "verdict": "NULL_FLOOR",
            "quotability": "REFUSE（quote_gate R8c：輪級聚合 ⇒ 不可入認證表）"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--ab", help="rho_AB.json (arms rho-off,rho-on)")
    ap.add_argument("--ba", help="rho_BA.json (arms rho-on,rho-off)")
    ap.add_argument("--aa", action="store_true",
                    help="null mode: the artifacts are an A/A run (two env-identical arms); "
                         "report the noise floor instead of a price")
    ap.add_argument("--write", default="", help="write <path>.json + <path>.md")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    if args.aa:
        if not args.ab:
            print("need --ab <A/A artifact> for --aa", file=sys.stderr)
            return 2
        # An A/A run is ONE invocation (`--arms baseline,rho-off`), so one artifact holds both arms.
        r = null_floor((("aa", json.load(open(args.ab))),))
        print(json.dumps(r, indent=1, ensure_ascii=False))
        t = r["tail"]
        for s in r["sides"]:
            print(f"  {s['label']}: median x{s.get('paired_ratio_median', float('nan')):.3f} "
                  f"({s.get('effect_pct', 0):+.1f}%) resolvable={s.get('resolvable')}")
        print(f"  tail: launches with an excursion = {t['launches_with_excursion']} of "
              f"{t['launches_per_arm']} (one-sided exact p={t['one_sided_exact_p']})")
        print(f"  tail_branch: {r['tail_branch']['branch']} -- {r['tail_branch']['why']}")
        print(f"\nVERDICT: NULL_FLOOR -- {r['why']}")
        print(f"  quotability: {r['quotability']}")
        if args.write:
            Path(args.write).with_suffix(".json").write_text(
                json.dumps(r, indent=1, ensure_ascii=False) + "\n")
            print(f"\nwrote {Path(args.write).with_suffix('.json')}")
        return 0
    if not (args.ab and args.ba):
        print("need --ab and --ba (or --selftest / --aa)", file=sys.stderr)
        return 2
    ab_rows, ba_rows = json.load(open(args.ab)), json.load(open(args.ba))
    ab = run_side(ab_rows, "ab (off,on)")
    ba = run_side(ba_rows, "ba (on,off)")
    rep = report(ab, ba, ab_rows, ba_rows)
    print(json.dumps(rep, indent=1, ensure_ascii=False))
    print(f"\nVERDICT: {rep['verdict']} -- {rep['verdict_why']}")
    print(f"  quotability: {rep['quotability']}")
    if rep["price"]:
        p = rep["price"]
        print(f"price: rho/off ratio x{p['ratio_on_off_median']:.3f} -> "
              f"dStep {p['delta_step_ms_median']:+.2f} ms [range {p['delta_step_ms_range'][0]:+.2f}, "
              f"{p['delta_step_ms_range'][1]:+.2f}] vs window {p['window_ms'][0]}-{p['window_ms'][1]} ms "
              f"=> {p['class']}")
    for s in (ab, ba):
        print(f"  {s['label']}: median x{s.get('paired_ratio_median', float('nan')):.3f} "
              f"({s.get('effect_pct', 0):+.1f}%) resolvable={s.get('resolvable')} "
              f"pool-noop-per-rep={s.get('pool_counters_noop_per_rep')}")
    t = rep["tail"]
    print(f"  round excursions: launches with a round < 85% of its own median = "
          f"{t['launches_with_excursion']} of {t['launches_per_arm']}"
          f" (one-sided exact p={t['one_sided_exact_p']})")
    a = rep["noop_audit"]
    if a.get("checked"):
        print(f"  session audit: settled {a['settled_launches']} launches, counters identical "
              f"after launch #1 = {a['pool_counters_identical_after_first_launch']}; "
              f"first launch is the outlier = {a['first_launch_is_the_outlier']} "
              f"(its md5 differs = {a['first_launch_answer_differs']})")
    if args.write:
        out = Path(args.write)
        out.with_suffix(".json").write_text(json.dumps(rep, indent=1, ensure_ascii=False) + "\n")
        print(f"\nwrote {out.with_suffix('.json')}")
    return 0 if rep["verdict"] in ("RHO_PRICE_REAL", "RHO_ORDER_DRIFT") else (
        1 if rep["verdict"] == "NOT_READY" else 2)


if __name__ == "__main__":
    sys.exit(main())
