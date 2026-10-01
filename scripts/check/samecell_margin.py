#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""samecell_margin.py — B 半（靜態寬度重算）的**同格**邊際（L20-3 的端點；2026-09-30 §70）

問題（`docs/MISSHIST_REVIVAL_GATE_2026-09-29.md` §4）：B 的重算價 6.07 ms（交付 cell 的
直方圖推出）要跟「它要取代的 fill」比，而那一欄當時寫的是 **3.955 ms ＝ (default) cell**
的儀器讀數 ⇒ 跨格（§56 不可比）。§69 把交付 cell 的 fill 價量出來之後，這一支把它變成
**機械判詞**：兩側都在同一格（交付 cell），各自有各自的窗口與散度。

左側（要付的）= 重算：`40 層 x k 列 x c_col`，`c_col` 由**同一台機器、同一個乾淨窗口**
的 kernel 探針（`shape_probe/mmid_shapes.py --op mul_mat_id`，幾何 used=8／layers=40／
tokens=1 ＝ 交付 cell 的 decode 步）量出來。
右側（要取代的）= 該步的 fill term：由**交付 cell 的成對產物**的 EBTIMER 逐 rep 中位數。

判詞（機械、fail-closed）：
  * `k*` = 讓**覆蓋率達 100%** 的最小 k（由逐層 miss 直方圖現算；靜態圖要每一步都對）。
  * 對 `k = 2..8` 逐一算 margin(k) ＝ fill − 40·k·c_col，且用**逐 rep fill x 逐趟 c_col**
    的全部組合**（3x3）檢查符號一致性：
      - 全部 > 0 ⇒ `POSITIVE`（分離，B 值得做）
      - 全部 < 0 ⇒ `EXCLUDED`（分離，B 不值得）
      - 符號混雜 ⇒ `NOT_SEPARATED`（**不可分辨**：這一格真正的答案）
  * 判詞另附 `k*` 的那一列的符號 → 決定「全覆蓋設計點」能不能分辨。

⚠ 這不是「B 真的跑了」：B 沒有實作（§51 撤回、`L20-3` 的 falsify 是「成本隨 miss 縮放 ⇒
  靜態圖論證不成立」）。兩側都是**替代路徑的定價**，不是 B 自己的量測。

自測：python3 scripts/check/samecell_margin.py --selftest
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
HIST_RE = re.compile(r"CGC-MISSMASK-HIST:\s+step=(\d+)\s+nrec=(\d+)\s+max=(\d+)\s+"
                     r"il_max=(\d+)\s+unknown=(\d+)")
DELIVERY = "delivery"
LAYERS, USED, COLUMNS = 40, 8, 40 * 8      # 交付 cell 的 decode 幾何
KS = tuple(range(2, 9))
WARM_SKIP = 64
PEAK_GIB_S = 108.8
MIB = 1024.0 ** 2


def load_ebtimer(path):
    rows = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for ln in fh:
            m = EB_RE.search(ln)
            if not m:
                continue
            seg = m.group(5)
            if seg is not None and seg != "decode":
                continue
            rows.append((int(m.group(1)), int(m.group(3))))
    return rows


def fill_side(ab_path, log_a, reps=None):
    """交付 cell 的 fill 側：(per-rep 被計時窗口 p50 與 miss/步) 清單。"""
    with open(ab_path, encoding="utf-8") as fh:
        data = json.load(fh)
    arm = None
    for a in (data if isinstance(data, list) else [data]):
        if "CGC_EB_NOFILL" not in (a.get("extra_env") or {}) and (a.get("extra_env") or {}).get("CGC_EB_TIMER"):
            arm = a
    if arm is None:
        return None, "成對產物裡找不到 A 臂（CGC_EB_TIMER=1 且沒有 CGC_EB_NOFILL）"
    if (arm.get("contract") or {}).get("cell") != DELIVERY:
        return None, "A 臂不是交付 cell（%r）" % (arm.get("contract") or {}).get("cell")
    if (arm.get("attribution") or {}).get("verdict") != "none":
        return None, "A 臂窗口 attribution=%r ≠ none" % (arm.get("attribution") or {}).get("verdict")
    row = None
    for r in (arm.get("rows") or []):
        if r.get("n_gen"):
            row = r
    if row is None:
        return None, "A 臂沒有 decode 列"
    nrep = int(reps or len(row.get("samples_ts") or []) or 3)
    rows = load_ebtimer(log_a)
    if len(rows) < nrep * (WARM_SKIP + 1):
        return None, "EBTIMER 行數 %d 太少（reps x 步數）" % len(rows)
    per = len(rows) // nrep
    out = []
    for i in range(nrep):
        blk = rows[i * per:(i + 1) * per] if i < nrep - 1 else rows[i * per:]
        tw = blk[WARM_SKIP:] or blk
        out.append({"fill_ms": st.median([r[0] for r in tw]) / 1000.0,
                    "miss_per_step": st.mean([r[1] for r in tw]),
                    "n": len(tw)})
    for e in out:
        e["ms_per_miss"] = e["fill_ms"] / e["miss_per_step"] if e["miss_per_step"] else None
    cache = arm.get("cache") or {}
    out_meta = {"step_ms": 1000.0 / float(row.get("avg_ts") or 0) if row.get("avg_ts") else None,
                "io_bytes": cache.get("io_bytes"), "misses": cache.get("misses"),
                "effective_mib_s": cache.get("io_effective_mib_s")}
    return (out, out_meta), ""


def probe_side(paths):
    """kernel 側：每趟的 family ms/step 與 per-column c_col（同幾何才收）。"""
    passes = []
    for p in paths:
        with open(p, encoding="utf-8") as fh:
            d = json.load(fh)
        if int(d.get("used") or 0) != USED or int(d.get("layers") or 0) != LAYERS:
            return None, "探針幾何不符（used=%s layers=%s，要 %d/%d）" % (d.get("used"), d.get("layers"), USED, LAYERS)
        toks = {v.get("tokens") for v in (d.get("rows") or {}).values() if isinstance(v, dict)}
        if toks != {1}:
            return None, "探針不是 T=1（%s）⇒ 與交付 cell 的 decode 步不同幾何" % sorted(toks)
        cls = (d.get("window") or {}).get("class")
        if cls != "clean":
            return None, "探針窗口 class=%r ≠ clean ⇒ 絕對值可能在描述鄰居" % cls
        fam = sum(float(v["ms_per_step"]) for v in d["rows"].values() if "ms_per_step" in v)
        passes.append({"path": os.path.basename(p), "family_ms": fam, "c_col": fam / COLUMNS})
    if not passes:
        return None, "沒有探針產物"
    return passes, ""


def coverage(hist_path, warm_skip=WARM_SKIP):
    """由逐層直方圖現算 steady 覆蓋率表（靜態圖要每一步都對 ⇒ 看 100% 那一欄）。"""
    steps = []
    with open(hist_path, encoding="utf-8", errors="replace") as fh:
        for ln in fh:
            m = HIST_RE.search(ln)
            if not m:
                continue
            step, nrec, mx, ilmx, unk = (int(m.group(i)) for i in range(1, 6))
            steps.append({"step": step, "nrec": nrec, "max": mx, "unknown": unk})
    steady = [s for s in steps if s["step"] > warm_skip and s["unknown"] == 0]
    if len(steady) < 100:
        return None, "steady 步數只有 %d（<100）⇒ 覆蓋率不可算" % len(steady)
    cov = {}
    for k in KS:
        cov[k] = 100.0 * sum(1 for s in steady if s["max"] <= k) / len(steady)
    return {"n_steady": len(steady), "n_all": len(steps), "coverage_pct": cov}, ""


def judge(ab_path, log_a, probe_paths, hist_path, reps=None):
    fs, why = fill_side(ab_path, log_a, reps=reps)
    if fs is None:
        return {"verdict": "REFUSE", "why": why}
    fills, meta = fs
    ps, why = probe_side(probe_paths)
    if ps is None:
        return {"verdict": "REFUSE", "why": why}
    cov, why = coverage(hist_path)
    if cov is None:
        return {"verdict": "REFUSE", "why": why}
    k_star = next((k for k in KS if cov["coverage_pct"][k] >= 99.999), None)
    if k_star is None:
        return {"verdict": "REFUSE", "why": "沒有任何 k 達到 100% 覆蓋（表：%s）" % cov["coverage_pct"]}

    # 逐 k 的符號一致性：fill（逐 rep）x c_col（逐趟）全組合
    grid = {}
    for k in KS:
        signs, vals = set(), []
        for f in fills:
            for p in ps:
                m = f["fill_ms"] - LAYERS * k * p["c_col"]
                vals.append(m)
                signs.add(m > 0)
        grid[k] = {"margin_ms_min": min(vals), "margin_ms_max": max(vals),
                   "signs": ("+" if signs == {True} else "-" if signs == {False} else "+/-"),
                   "coverage_pct": cov["coverage_pct"][k],
                   "cost_ms": LAYERS * k * st.mean([p["c_col"] for p in ps])}
    star = grid[k_star]
    if star["signs"] == "+":
        verdict = "POSITIVE"
    elif star["signs"] == "-":
        verdict = "EXCLUDED"
    else:
        verdict = "NOT_SEPARATED"
    fill_mean = st.mean([f["fill_ms"] for f in fills])
    steady = st.mean([f["fill_ms"] for f in fills[1:]]) if len(fills) > 1 else None
    mpm = st.mean([f["ms_per_miss"] for f in fills])
    bytes_per_miss = (meta["io_bytes"] / meta["misses"]) if (meta.get("io_bytes") and meta.get("misses")) else None
    peak_ms = (bytes_per_miss / MIB / 1024 / PEAK_GIB_S * 1000.0) if bytes_per_miss else None
    res = {
        "verdict": verdict, "why": "",
        "k_star": k_star, "k_star_coverage_pct": star["coverage_pct"],
        "k_star_margin_ms": [round(star["margin_ms_min"], 2), round(star["margin_ms_max"], 2)],
        "k_star_signs": star["signs"],
        "table": {str(k): grid[k] for k in KS},
        "fill_ms_per_rep": [round(f["fill_ms"], 3) for f in fills],
        "fill_ms": round(fill_mean, 3), "fill_ms_steady": round(steady, 3) if steady else None,
        "miss_per_step": [round(f["miss_per_step"], 2) for f in fills],
        "ms_per_miss": round(mpm, 4),
        "bytes_per_miss_mib": round(bytes_per_miss / MIB, 4) if bytes_per_miss else None,
        "peak_ms_per_miss": round(peak_ms, 6) if peak_ms else None,
        "fixed_share_pct": round(100.0 * (1 - peak_ms / mpm), 2) if peak_ms else None,
        "probe": [{"path": p["path"], "family_ms": round(p["family_ms"], 3),
                   "c_col_ms": round(p["c_col"], 5)} for p in ps],
        "c_col_ms": round(st.mean([p["c_col"] for p in ps]), 5),
        "c_col_spread": round(max(p["c_col"] for p in ps) / min(p["c_col"] for p in ps), 3),
        "coverage": cov["coverage_pct"], "n_steady": cov["n_steady"],
        "step_ms": round(meta["step_ms"], 2) if meta.get("step_ms") else None,
        "io_effective_mib_s": meta.get("effective_mib_s"),
    }
    return res


# ── 自測 ─────────────────────────────────────────────────────────────────────
def self_test():
    ok = []

    def case(label, cond, extra=""):
        ok.append(bool(cond))
        print("  %s %s%s" % ("ok  " if cond else "FAIL", label, ("  [%s]" % (extra,)) if extra and not cond else ""))

    d = tempfile.mkdtemp(prefix="samecell_selftest_")
    log = os.path.join(d, "a.log")
    with open(log, "w", encoding="utf-8") as fh:
        for rep, miss in ((0, 12), (1, 7), (2, 7)):
            for i in range(128):
                us = 9000 if (rep == 0 and i >= 64) else 5200
                fh.write("CGC-EBTIMER: step_usec=%d calls=40 miss=%d n_sum=320 seg=decode n_pf=0 n_dec=40\n"
                         % (us, miss))
    hist = os.path.join(d, "hist.log")
    with open(hist, "w", encoding="utf-8") as fh:
        for s in range(1, 387):
            mx = 6 if s in (100, 200) else (3 if s % 2 else 2)
            fh.write("CGC-MISSMASK-HIST: step=%d nrec=39 max=%d il_max=39 unknown=0 per=1,2\n" % (s, mx))

    def mkprobe(name, fam, cls="clean", used=8, layers=40, tokens=1):
        p = os.path.join(d, name)
        with open(p, "w", encoding="utf-8") as fh:
            json.dump({"used": used, "layers": layers, "window": {"class": cls},
                       "rows": {"gate_exps_t1": {"tokens": tokens, "ms_per_step": fam / 2},
                                "down_exps_t1": {"tokens": tokens, "ms_per_step": fam / 2}}}, fh)
        return p

    def mkfake(path, **kw):
        a = {"tag": "prod-new:CGC_EB_TIMER=1", "extra_env": {"CGC_EB_TIMER": "1"},
             "contract": {"cell": DELIVERY}, "attribution": {"verdict": "none"},
             "cache": {"io_bytes": 4864417792, "misses": 4300, "io_effective_mib_s": 13.0},
             "rows": [{"n_gen": 64, "warm_skip": 64, "avg_ts": 10.773, "samples_ts": [1, 1, 1]}]}
        a.update(kw)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump([a], fh)
        return path

    ab = os.path.join(d, "ab.json")
    mkfake(ab)
    p_lo, p_hi = mkprobe("p_lo.json", 7.62), mkprobe("p_hi.json", 8.71)
    r = judge(ab, log, [p_lo], hist)
    case("乾淨輸入 ⇒ k*=6（100% 覆蓋要 6）", r.get("k_star") == 6, r.get("k_star"))
    case("k=6 邊際跨 0（rep1 9.0 vs 其餘 5.2）⇒ NOT_SEPARATED",
         r.get("verdict") == "NOT_SEPARATED", r.get("verdict"))
    case("k=2 三組全為正", r["table"]["2"]["signs"] == "+", r["table"]["2"])
    r2 = judge(ab, log, [p_hi], hist)
    case("探針價變高不改變 k=6 的不可分辨", r2["verdict"] == "NOT_SEPARATED", r2["verdict"])
    case("per-miss 成本被算出來（0.74 ms 量級）", 0.5 < r["ms_per_miss"] < 1.0, r["ms_per_miss"])
    case("固定開銷佔比 >98%", (r["fixed_share_pct"] or 0) > 98, r["fixed_share_pct"])
    case("覆蓋率表 k=6 是 100%", abs(r["coverage"][6] - 100.0) < 1e-9, r["coverage"][6])

    h2 = os.path.join(d, "hist2.log")
    with open(h2, "w", encoding="utf-8") as fh:
        for s in range(1, 387):
            fh.write("CGC-MISSMASK-HIST: step=%d nrec=39 max=%d il_max=39 unknown=0 per=1\n" % (s, 2))
    r3 = judge(ab, log, [p_lo], h2)
    case("若每層都 <=2 ⇒ k*=2 且分離為正", r3["k_star"] == 2 and r3["verdict"] == "POSITIVE", (r3["k_star"], r3["verdict"]))
    h3 = os.path.join(d, "hist3.log")
    with open(h3, "w", encoding="utf-8") as fh:
        for s in range(1, 387):
            fh.write("CGC-MISSMASK-HIST: step=%d nrec=39 max=%d il_max=39 unknown=0 per=1\n" % (s, 8))
    r4 = judge(ab, log, [p_lo], h3)
    case("若每層都 <=8、c_col 7.6 ⇒ k*=8 但 rep1 仍為正 ⇒ NOT_SEPARATED（不是負）",
         r4["k_star"] == 8 and r4["verdict"] == "NOT_SEPARATED", (r4["k_star"], r4["verdict"]))
    p_cost = mkprobe("p_cost.json", 12.0)          # 貴到連 rep1 的 fill 都蓋不住
    r5 = judge(ab, log, [p_cost], h3)
    case("若真的處處都貴 ⇒ k*=8 且三組全負 ⇒ EXCLUDED",
         r5["k_star"] == 8 and r5["verdict"] == "EXCLUDED", (r5["k_star"], r5["verdict"]))

    case("髒探針窗口 ⇒ REFUSE",
         judge(ab, log, [mkprobe("p_bad.json", 7.6, cls="busy-overridden")], hist)["verdict"] == "REFUSE")
    case("幾何不符（used=4）⇒ REFUSE",
         judge(ab, log, [mkprobe("p_g.json", 7.6, used=4)], hist)["verdict"] == "REFUSE")
    case("T=2 ⇒ REFUSE（不是交付 cell 的 decode 幾何）",
         judge(ab, log, [mkprobe("p_t2.json", 7.6, tokens=2)], hist)["verdict"] == "REFUSE")
    ab2 = os.path.join(d, "ab2.json")
    mkfake(ab2, contract={"cell": "(default)"})
    case("A 臂不是交付 cell ⇒ REFUSE", judge(ab2, log, [p_lo], hist)["verdict"] == "REFUSE")
    ab3 = os.path.join(d, "ab3.json")
    mkfake(ab3, attribution={"verdict": "swap"})
    case("A 臂髒窗口 ⇒ REFUSE", judge(ab3, log, [p_lo], hist)["verdict"] == "REFUSE")
    h4 = os.path.join(d, "hist4.log")
    with open(h4, "w", encoding="utf-8") as fh:
        fh.write("CGC-MISSMASK-HIST: step=1 nrec=39 max=6 il_max=39 unknown=0 per=1\n" * 10)
    case("steady 步數不足 ⇒ REFUSE", judge(ab, log, [p_lo], h4)["verdict"] == "REFUSE")
    try:
        judge(ab, log, [os.path.join(d, "nope.json")], hist)
        case("探針產物不存在 ⇒ 拋例外（fail-closed）", False)
    except Exception:
        case("探針產物不存在 ⇒ 拋例外（fail-closed）", True)
    n = sum(ok)
    print("  %d/%d" % (n, len(ok)))
    return 0 if n == len(ok) else 1


def main():
    ap = argparse.ArgumentParser(description="B 半的同格邊際（L20-3 端點）")
    ap.add_argument("--ab", help="交付 cell 成對產物（A 臂要有 CGC_EB_TIMER）")
    ap.add_argument("--log-a", help="A 臂 stderr（EBTIMER）")
    ap.add_argument("--probe", action="append", default=[], help="kernel 探針 JSON（可重複）")
    ap.add_argument("--hist", help="逐層 miss 直方圖的原始 stderr")
    ap.add_argument("--reps", type=int, default=None)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return self_test()
    if not (a.ab and a.log_a and a.probe and a.hist):
        ap.error("需要 --ab --log-a --probe --hist（或 --selftest）")
    res = judge(a.ab, a.log_a, a.probe, a.hist, reps=a.reps)
    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
        return 0 if res.get("verdict") in ("POSITIVE", "EXCLUDED", "NOT_SEPARATED") else 1
    print("判詞：%s%s" % (res["verdict"], ("  ← " + res["why"]) if res.get("why") else ""))
    if res.get("verdict") != "REFUSE":
        print("  fill（交付 cell，逐 rep p50）%s ms/step；穩態 %s；miss/步 %s ⇒ %.3f ms/miss"
              % (res["fill_ms_per_rep"], res["fill_ms_steady"], res["miss_per_step"], res["ms_per_miss"]))
        print("  每 miss 的 bytes %.4f MiB ⇒ 峰值只要 %.5f ms ⇒ **固定開銷 %.2f%%**"
              % (res["bytes_per_miss_mib"], res["peak_ms_per_miss"], res["fixed_share_pct"]))
        print("  kernel c_col %s ms/列（%d 趟，spread %.3f）" % (res["c_col_ms"], len(res["probe"]), res["c_col_spread"]))
        print("  k  重算 ms  覆蓋%%   margin（fill−重算，min~max）  符號")
        for k in KS:
            g = res["table"][str(k)]
            print("  %d  %6.2f  %5.1f   %+6.2f ~ %+6.2f          %s"
                  % (k, g["cost_ms"], g["coverage_pct"], g["margin_ms_min"], g["margin_ms_max"], g["signs"]))
        print("  ⇒ 100%% 覆蓋要 k=%d（覆蓋 %.1f%%）⇒ margin %s ms ⇒ **%s**"
              % (res["k_star"], res["k_star_coverage_pct"], res["k_star_margin_ms"], res["verdict"]))
    return 0 if res.get("verdict") in ("POSITIVE", "EXCLUDED", "NOT_SEPARATED") else 1


if __name__ == "__main__":
    sys.exit(main())
