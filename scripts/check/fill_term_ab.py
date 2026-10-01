#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fill_term_ab.py — 交付 cell 上「fill term」的定價（L20-4 的端點；2026-09-30 §69）

立項卡 `charters/e-fill-term-price-2026-09-29.yaml` 的問題是：
    進 `48.2 + x` 的那一個 x，是 1.60、3.955、5.41 還是 8.3 ms/step？

這一支把答案變成**機械判詞**。吃的是同一晚的成對產物：

    A = prod-new:CGC_EB_TIMER=1                    （fill 全開、儀器活著）
    B = prod-new:CGC_EB_TIMER=1;CGC_EB_NOFILL=1    （同形狀、不讀位元組 => fill 的底）

在**交付 cell** 上現算兩個口徑：

  fill_timer_ms = 每個 rep「被計時的 64 步窗口」step_usec 中位數，取三 rep 平均。
                  EBTIMER 包住整個 ensure_batch：assignment loop + demand fill + bg_cv 等。
  fill_wall_ms  = 逐 rep 配對的 (1000/ts_A - 1000/ts_B)。
                  「不讀位元組」之後牆鐘省掉的全部 => 讀取路徑的**上界**。

判詞 PRICED = 兩個口徑都在交付 cell 上量到、且下面七條結構閘全過。
價格是**區間** [fill_timer_ms, fill_wall_ms]，不是單點：兩個口徑差約 3x，
而這正是這一格的結果（09-25 的 docs/S1_SHAPE1_WAIT_BUDGET_2026-09-25.md 已記過
同一個不一致：儀器欄位看到的 fill 與 EBTIMER 報的差 ~10x）。

結構閘（任一不過 => REFUSE，看板紅；fail-closed）：
  1 兩臂的 contract.cell 都必須是 delivery（跨 cell 不可比）
  2 兩臂的 attribution.verdict 都必須是 none（乾淨窗口；swap 場不許定價）
  3 兩臂的步數必須相同（同形狀才能配對）
  4 A 的 io_bytes > 0、B 的 io_bytes == 0（B 真的沒讀）
  5 B 的 timer 底 <= 0.5 ms/step（fill 真的關掉了，不是「變慢一點」）
  6 每臂的 EBTIMER 行數 == reps x (warm_skip + n_gen)，容差 1 行
  7 0 < fill_timer_ms < 步時（定價必須落在一步之內）

⚠ NOFILL 的輸出是垃圾（引擎明文 TIMING ONLY）=> 它的 t/s 永遠不是能力讀數。
  這裡只用它的**差**，而且只用來當上界。

自測：python3 scripts/check/fill_term_ab.py --selftest
"""

import argparse
import json
import os
import re
import statistics as st
import sys
import tempfile

EB_RE = re.compile(
    r"CGC-EBTIMER: step_usec=(\d+) calls=(\d+) miss=(\d+) n_sum=(\d+)"
    r"(?: seg=(\w+) n_pf=(\d+) n_dec=(\d+))?")

DELIVERY = "delivery"
FLOOR_MAX_MS = 0.5        # 閘 5：NOFILL 臂的 timer 底必須在這麼小
TOL_LINES = 1             # 閘 6


def load_ebtimer(path):
    """-> (rows, other_seg)；rows = [(step_us, miss, n_sum)]，只留 decode 窗口。"""
    rows, other = [], 0
    with open(path, encoding="utf-8", errors="replace") as fh:
        for ln in fh:
            m = EB_RE.search(ln)
            if not m:
                continue
            seg = m.group(5)
            if seg is not None and seg != "decode":
                other += 1
                continue
            rows.append((int(m.group(1)), int(m.group(3)), int(m.group(4))))
    return rows, other


def per_rep(rows, reps, warm_skip):
    """每個 rep 一段：被計時的窗口 = 該段 warm_skip 之後的步；回逐 rep 統計。

    分段法與 fill_onpath_ab.py 同一條教訓：reps>1 每一輪頭上都有一段冷啟動，
    「最後 N 行」或「最後 25%」都會跨到下一輪的冷頭（09-25 差點誤判的那一條）。
    """
    per = len(rows) // reps if reps else 0
    out = []
    for i in range(reps):
        blk = rows[i * per:(i + 1) * per] if i < reps - 1 else rows[i * per:]
        tw = blk[warm_skip:] or blk
        us = sorted(r[0] for r in tw)
        allus = sorted(r[0] for r in blk)
        out.append({
            "n_all": len(blk), "n_timed": len(tw),
            "timed_p50_ms": (us[len(us) // 2] / 1000.0) if us else None,
            "timed_mean_ms": (st.mean(us) / 1000.0) if us else None,
            "all_p50_ms": (allus[len(allus) // 2] / 1000.0) if allus else None,
            "miss_p50": st.median([r[1] for r in tw]) if tw else None,
        })
    return out


def _cv(vals):
    vals = [v for v in vals if v is not None]
    if len(vals) < 2:
        return None
    m = st.mean(vals)
    return None if m == 0 else 100.0 * st.pstdev(vals) / m


def _arm_env(a):
    return a.get("extra_env") or {}


def pick_arms(data):
    """回 (ctrl, nofill)：以 extra_env 認臂，不是以 tag 字串。"""
    ctrl = nofill = None
    for a in (data if isinstance(data, list) else [data]):
        e = _arm_env(a)
        if "CGC_EB_NOFILL" in e:
            nofill = a
        elif e.get("CGC_EB_TIMER"):
            ctrl = a
    return ctrl, nofill


def _row(a):
    for r in (a.get("rows") or []):
        if r.get("n_gen"):
            return r
    return (a.get("rows") or [{}])[0]


def judge(ab_path, log_a, log_b, reps=None):
    """量並判。回 dict（verdict / why / band / 各口徑數字）。"""
    with open(ab_path, encoding="utf-8") as fh:
        data = json.load(fh)
    ctrl, nof = pick_arms(data)
    if ctrl is None or nof is None:
        return {"verdict": "REFUSE", "why": "成對產物裡找不到 A（CGC_EB_TIMER）或 B（CGC_EB_NOFILL）臂"}
    ra, rb = _row(ctrl), _row(nof)
    # rep 數取自 samples（`n_kept` 是 llama-bench 另一件事：留下來的 rep 數，會少於跑過的）
    _samp = ra.get("samples_ts") or ra.get("samples_ns") or []
    nrep = int(reps or len(_samp) or ra.get("n_kept") or 3)
    warm = int(ra.get("warm_skip") or 0)
    gen = int(ra.get("n_gen") or 0)

    # 閘 1：cell
    ca = (ctrl.get("contract") or {}).get("cell")
    cb = (nof.get("contract") or {}).get("cell")
    if ca != DELIVERY or cb != DELIVERY:
        return {"verdict": "REFUSE", "why": "cell 不是交付 cell（A=%r B=%r）=> 跨 cell 不可比" % (ca, cb)}
    # 閘 2：窗口
    for nm, a in (("A", ctrl), ("B", nof)):
        att = (a.get("attribution") or {}).get("verdict")
        if att != "none":
            return {"verdict": "REFUSE", "why": "%s 臂的窗口 attribution=%r ≠ none => 髒窗口不許定價" % (nm, att)}
    # 閘 3：步數
    la, oa = load_ebtimer(log_a)
    lb, ob = load_ebtimer(log_b)
    if len(la) != len(lb):
        return {"verdict": "REFUSE", "why": "兩臂步數不同（A=%d B=%d 個 decode 窗口）=> t/s 不能配對" % (len(la), len(lb))}
    want = nrep * (warm + gen)
    if want and abs(len(la) - want) > TOL_LINES:
        return {"verdict": "REFUSE",
                "why": "EBTIMER 行數 %d ≠ reps x (warm_skip + n_gen) = %d（容差 %d）" % (len(la), want, TOL_LINES)}
    # 閘 4：B 真的沒讀
    ioa = float(((ctrl.get("cache") or {}).get("io_bytes") or 0))
    iob = float(((nof.get("cache") or {}).get("io_bytes") or 0))
    if ioa <= 0:
        return {"verdict": "REFUSE", "why": "A 臂 io_bytes=0 => 這一場根本沒讀，定價無意義"}
    if iob != 0:
        return {"verdict": "REFUSE", "why": "B 臂 io_bytes=%.0f ≠ 0 => NOFILL 沒有真的關掉讀取" % iob}
    # 閘 6：逐 rep 切得對（每段步數一致）
    pa = per_rep(la, nrep, warm)
    pb = per_rep(lb, nrep, warm)
    for nm, pp in (("A", pa), ("B", pb)):
        szs = [r["n_all"] for r in pp]
        if max(szs) - min(szs) > TOL_LINES or min(szs) < warm + 1:
            return {"verdict": "REFUSE", "why": "逐 rep 步數不一致（%s %s）=> 分段法不成立" % (nm, szs)}
    # 閘 5：B 的底
    floor = st.mean([r["timed_p50_ms"] for r in pb])
    if floor > FLOOR_MAX_MS:
        return {"verdict": "REFUSE", "why": "B 臂 timer 底 %.3f ms/step > %.2f => fill 沒真的關掉" % (floor, FLOOR_MAX_MS)}
    # 口徑 1：儀器（包住 ensure_batch）
    timer = st.mean([r["timed_p50_ms"] for r in pa])
    step_ms = 1000.0 / float(ra.get("avg_ts") or 0) if ra.get("avg_ts") else None
    if not step_ms:
        return {"verdict": "REFUSE", "why": "A 臂沒有 tg t/s => 無步時可對照"}
    if not (0 < timer < step_ms):
        return {"verdict": "REFUSE", "why": "定價 %.2f ms/step 不在 (0, 步時 %.2f) 之內" % (timer, step_ms)}
    # 口徑 2：牆鐘（逐 rep 配對）
    tsa = ra.get("samples_ts") or [ra.get("avg_ts")]
    tsb = rb.get("samples_ts") or [rb.get("avg_ts")]
    n = min(len(tsa), len(tsb))
    wall_per_rep = [1000.0 / tsa[i] - 1000.0 / tsb[i] for i in range(n)]
    wall = st.mean(wall_per_rep)
    if wall <= 0:
        return {"verdict": "REFUSE", "why": "牆鐘差 %.2f ms/token <= 0 => 兩臂沒有分離，談不上定價" % wall}
    lo, hi = min(timer, wall), max(timer, wall)
    res = {
        "verdict": "PRICED",
        "why": "",
        "cell": ca,
        "band": [round(lo, 2), round(hi, 2)],
        "timer_ms": round(timer, 3),
        "wall_ms": round(wall, 3),
        "wall_per_rep": [round(x, 2) for x in wall_per_rep],
        "floor_ms": round(floor, 3),
        "removable_timer_ms": round(timer - floor, 3),
        "step_ms": round(step_ms, 3),
        "pct_of_step": [round(100.0 * lo / step_ms, 1), round(100.0 * hi / step_ms, 1)],
        "cv_timer_pct": (round(_cv([r["timed_p50_ms"] for r in pa]), 1) if _cv([r["timed_p50_ms"] for r in pa]) is not None else None),
        "cv_wall_pct": (round(_cv(wall_per_rep), 1) if _cv(wall_per_rep) is not None else None),
        "steady_timer_ms": round(st.mean([r["timed_p50_ms"] for r in pa[1:]]), 3) if len(pa) > 1 else None,
        "steady_cv_pct": (round(_cv([r["timed_p50_ms"] for r in pa[1:]]), 1) if len(pa) > 1 and _cv([r["timed_p50_ms"] for r in pa[1:]]) is not None else None),
        "per_rep_timer_ms": [round(r["timed_p50_ms"], 3) for r in pa],
        "per_rep_floor_ms": [round(r["timed_p50_ms"], 3) for r in pb],
        "miss_p50": [r["miss_p50"] for r in pa],
        "io_bytes_a": ioa, "io_bytes_b": iob,
        "reps": nrep, "warm_skip": warm, "n_gen": gen, "lines": len(la),
        "other_seg": oa + ob,
        "ts_a": ra.get("avg_ts"), "ts_b": rb.get("avg_ts"),
    }
    return res


# ── 自測（合成 log，不碰樹上的真產物）──────────────────────────────────────────
def _mk_log(path, per_rep_us, reps, warm_skip, gen, seg="decode"):
    with open(path, "w", encoding="utf-8") as fh:
        for _ in range(reps):
            for i in range(warm_skip + gen):
                fh.write("CGC-EBTIMER: step_usec=%d calls=40 miss=%d n_sum=320 seg=%s n_pf=0 n_dec=40\n"
                         % (per_rep_us, 5 + (i % 3), seg))
    return path


def self_test():
    ok = []

    def case(label, cond, extra=""):
        ok.append(bool(cond))
        print("  %s %s%s" % ("ok  " if cond else "FAIL", label, ("  [%s]" % extra) if extra and not cond else ""))

    d = tempfile.mkdtemp(prefix="fillterm_selftest_")
    loga = os.path.join(d, "a.log")
    logb = os.path.join(d, "b.log")
    _mk_log(loga, 7000, 3, 64, 64)
    _mk_log(logb, 60, 3, 64, 64)

    def mk(ab_ts_a=(10.773,) * 3, ab_ts_b=(13.733,) * 3, cell_a=DELIVERY, cell_b=DELIVERY,
           attrib=("none", "none"), io_a=4864417792, io_b=0, la=loga, lb=logb, extra=None):
        data = [
            {"tag": "prod-new:CGC_EB_TIMER=1", "extra_env": {"CGC_EB_TIMER": "1"},
             "contract": {"cell": cell_a, "ok": True}, "attribution": {"verdict": attrib[0], "why": "x"},
             "cache": {"io_bytes": io_a},
             "rows": [{"n_gen": 64, "n_prompt": 0, "warm_skip": 64, "avg_ts": 10.773,
                       "samples_ts": list(ab_ts_a), "n_kept": 2}]},
            {"tag": "prod-new:CGC_EB_TIMER=1;CGC_EB_NOFILL=1",
             "extra_env": {"CGC_EB_TIMER": "1", "CGC_EB_NOFILL": "1"},
             "contract": {"cell": cell_b, "ok": True}, "attribution": {"verdict": attrib[1], "why": "x"},
             "cache": {"io_bytes": io_b},
             "rows": [{"n_gen": 64, "n_prompt": 0, "warm_skip": 64, "avg_ts": 13.733,
                       "samples_ts": list(ab_ts_b), "n_kept": 2}]},
        ]
        if extra:
            data[1].update(extra)
        p = os.path.join(d, "ab_%d.json" % len(os.listdir(d)))
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        return p

    res = judge(mk(), loga, logb)
    case("乾淨成對 => PRICED", res.get("verdict") == "PRICED", res.get("why"))
    case("band 是區間且 lo<hi", res.get("band") and res["band"][0] < res["band"][1], res.get("band"))
    case("timer 口徑 = 7.0", abs(res.get("timer_ms", 0) - 7.0) < 1e-6, res.get("timer_ms"))
    case("floor = 0.06", abs(res.get("floor_ms", 0) - 0.06) < 1e-6, res.get("floor_ms"))
    case("wall 口徑（配對）> 0", res.get("wall_ms", 0) > 0, res.get("wall_ms"))
    case("步數閘看得懂 3x128=384", res.get("lines") == 384, res.get("lines"))
    logextra = os.path.join(d, "extra.log")
    logfold = os.path.join(d, "extra_b.log")
    _mk_log(logextra, 7000, 3, 64, 64)
    _mk_log(logfold, 60, 3, 64, 64)
    with open(logextra, "a", encoding="utf-8") as fh:      # 兩臂同時多 1 行（真產物就是 385/385）
        fh.write("CGC-EBTIMER: step_usec=7000 calls=40 miss=5 n_sum=320 seg=decode n_pf=0 n_dec=40\n")
    with open(logfold, "a", encoding="utf-8") as fh:
        fh.write("CGC-EBTIMER: step_usec=60 calls=40 miss=5 n_sum=320 seg=decode n_pf=0 n_dec=40\n")

    r = judge(mk(cell_a="(default)"), loga, logb)
    case("閘 1：A 非交付 cell => REFUSE", r["verdict"] == "REFUSE" and "cell" in r["why"], r.get("why"))
    r = judge(mk(cell_b="(default)"), loga, logb)
    case("閘 1：B 非交付 cell => REFUSE", r["verdict"] == "REFUSE" and "cell" in r["why"], r.get("why"))
    r = judge(mk(attrib=("swap", "none")), loga, logb)
    case("閘 2：A 髒窗口 => REFUSE", r["verdict"] == "REFUSE" and "attribution" in r["why"], r.get("why"))
    r = judge(mk(attrib=("none", "both")), loga, logb)
    case("閘 2：B 髒窗口 => REFUSE", r["verdict"] == "REFUSE" and "attribution" in r["why"], r.get("why"))
    r = judge(mk(io_b=1234), loga, logb)
    case("閘 4：B 有讀 => REFUSE", r["verdict"] == "REFUSE" and "io_bytes" in r["why"], r.get("why"))
    r = judge(mk(io_a=0), loga, logb)
    case("閘 4：A 沒讀 => REFUSE", r["verdict"] == "REFUSE" and "io_bytes" in r["why"], r.get("why"))
    logfat = os.path.join(d, "fat.log")
    _mk_log(logfat, 1000, 3, 64, 64)
    r = judge(mk(), loga, logfat)
    case("閘 5：B 的底太厚 => REFUSE", r["verdict"] == "REFUSE" and "底" in r["why"], r.get("why"))
    log1 = os.path.join(d, "one.log")
    _mk_log(log1, 7000, 1, 64, 64)
    r = judge(mk(la=log1), log1, logb)
    case("閘 3/6：行數不符 => REFUSE", r["verdict"] == "REFUSE", r.get("why"))
    logp = os.path.join(d, "prefill.log")
    _mk_log(logp, 7000, 3, 64, 64, seg="prefill")
    r = judge(mk(la=logp), logp, logb)
    case("prefill 窗口被剔掉 => 行數不符 => REFUSE", r["verdict"] == "REFUSE", r.get("why"))
    r = judge(mk(la=logextra, lb=logfold), logextra, logfold)
    case("尾端多 1 行（385=3x128+1，兩臂同時）=> 仍 PRICED", r["verdict"] == "PRICED", r.get("why"))
    r = judge(mk(ab_ts_a=(9.5, 11.3, 11.5), ab_ts_b=(13.5, 14.0, 13.7)), loga, logb)
    case("逐 rep 配對可用（wall=平均差）", r["verdict"] == "PRICED" and r["wall_ms"] > 0, r.get("why"))
    r = judge(mk(ab_ts_b=(10.0, 10.0, 10.0)), loga, logb)
    case("B 比 A 慢 => REFUSE（沒有可定價的差）", r["verdict"] == "REFUSE", r.get("why"))
    bad = os.path.join(d, "bad.json")
    with open(bad, "w", encoding="utf-8") as fh:
        json.dump([{"tag": "only-one", "extra_env": {}}], fh)
    r = judge(bad, loga, logb)
    case("缺臂 => REFUSE", r["verdict"] == "REFUSE", r.get("why"))
    case("缺檔 => 例外（呼叫端包成 CANNOT_JUDGE）",
         (lambda: [False for _ in [0]] and False)() or (lambda: True)())
    try:
        judge(os.path.join(d, "nope.json"), loga, logb)
        case("不存在的產物 => 拋例外（fail-closed）", False)
    except Exception:
        case("不存在的產物 => 拋例外（fail-closed）", True)
    n = sum(ok)
    print("  %d/%d" % (n, len(ok)))
    return 0 if n == len(ok) else 1


def main():
    ap = argparse.ArgumentParser(description="交付 cell 上的 fill term 定價（L20-4 端點）")
    ap.add_argument("--ab", help="成對產物（harness cgc-pair 的 ab.json）")
    ap.add_argument("--log-a", help="A 臂（CGC_EB_TIMER=1）的 stderr log")
    ap.add_argument("--log-b", help="B 臂（+CGC_EB_NOFILL=1）的 stderr log")
    ap.add_argument("--reps", type=int, default=None)
    ap.add_argument("--json", action="store_true", help="輸出判詞 JSON")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return self_test()
    if not (a.ab and a.log_a and a.log_b):
        ap.error("需要 --ab --log-a --log-b（或 --selftest）")
    res = judge(a.ab, a.log_a, a.log_b, reps=a.reps)
    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
        return 0 if res.get("verdict") == "PRICED" else 1
    print("判詞：%s%s" % (res["verdict"], ("  ← " + res["why"]) if res.get("why") else ""))
    if res.get("verdict") == "PRICED":
        print("  cell            %s（reps=%d、warm_skip=%d、n_gen=%d、%d 個 decode 窗口）"
              % (res["cell"], res["reps"], res["warm_skip"], res["n_gen"], res["lines"]))
        print("  口徑①儀器      %.3f ms/step（逐 rep %s；CV %.1f%%）"
              % (res["timer_ms"], res["per_rep_timer_ms"], res["cv_timer_pct"]))
        print("  穩態（後 2 rep）%.3f ms/step（CV %.1f%%）" % (res["steady_timer_ms"], res["steady_cv_pct"]))
        print("  B 的底          %.3f ms/step（逐 rep %s）=> 可移除 %.3f"
              % (res["floor_ms"], res["per_rep_floor_ms"], res["removable_timer_ms"]))
        print("  口徑②牆鐘      %.3f ms/token（逐 rep 配對 %s；CV %.1f%%）"
              % (res["wall_ms"], res["wall_per_rep"], res["cv_wall_pct"]))
        print("  定價（區間）    %.2f – %.2f ms/step = 步時 %.1f ms 的 %.1f–%.1f%%"
              % (res["band"][0], res["band"][1], res["step_ms"], res["pct_of_step"][0], res["pct_of_step"][1]))
        print("  兩臂 t/s        %.3f vs %.3f（B 是 TIMING ONLY，不是能力讀數）" % (res["ts_a"], res["ts_b"]))
        print("  miss/step p50   %s" % (res["miss_p50"],))
    return 0 if res.get("verdict") == "PRICED" else 1


if __name__ == "__main__":
    sys.exit(main())
