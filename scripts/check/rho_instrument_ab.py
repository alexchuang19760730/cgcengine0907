#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""rho_instrument_ab.py — S3-b 端點①（ρ 儀器端點）的見證檢查器。

為什麼需要它：`docs/S3B_RHO_COST_2026-09-30.md` 的端點①是「**計數器**」（骯窗可引用，預註冊
明文），而端點②（時間／Δ）在那一趟被判「不可辨識」。若只把端點①寫成散文，下一個人無從知道
「量具到底動過沒有」——這正是 2026-09-26 那條 4.76 ms 估值變成神話的路徑（見 R5 政策）。

本檢查器把端點①機械化，判的是**三個見證的計數器**（不判時間，不判 t/s）：

  * `control`（`prod-new`，不開 `CGC_RHO_PROBE`）⇒ log 裡**不得**出現任何 `CGC-RHO` 列印；
  * `arm`（`+CGC_RHO_PROBE=1`，可多支）⇒ 每個 decode 步 `n_layer` 層 capture、`skip=0`、
    覆蓋率 ≥ 源碼自帶的**飽和門檻**（`llama-context.cpp:6726`：`> 0.70` 之後再準也沒用）；
  * 多支 `arm` 之間**必須一致**（`steps`／`layers` 全等、覆蓋在帶內）⇒「可複現」是端點的一部分，
    不是附註（本 repo 在 2026-09-30 吃過一次「單場次讀數當結論」的虧）。
  * 可選 `fill`（`+CGC_RHO_FILL=1`）⇒ 每一列 `queued == per_layer × layers`、`cum_skipped == 0`。

三態（與 `r6_witness.py` 同型，供 `decode_board_build.counter_verdict` 直接回傳）：
`INSTRUMENT_LIVE`（端點成立）／`REFUSE`（端點不成立或判不了）／判不了回 `(None, why)`。

fail-closed：檔不存在、控制臂不是控制、缺 SUM 列、步數與層數對不上、`skip>0`、
覆蓋低於門檻、跨場次不一致、fill 記帳不符 ⇒ **一律 REFUSE**，不放行任何「看起來像活著」的窗。

CLI：
    python3 scripts/check/rho_instrument_ab.py --control LOG --arm LOG [--arm LOG ...] [--fill LOG]
    python3 scripts/check/rho_instrument_ab.py --selftest
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile

# 源碼自帶的飽和門檻（llama-context.cpp:6726）：「cov_uni >= 0.398 ⇒ step −10%；>= 0.164 ⇒ −3%；
# > 0.70 之後窗口飽和，再準也沒用」。取 0.70 當**端點①的門檻**：低於它代表影子路由沒在收貨，
# 而不是「收得比較差」。
SAT_DEFAULT = 0.70
# 跨場次一致性的帶（絕對值）。三個獨立場次（2026-09-30 17:20／18:28／18:31）實測 0.8048–0.8077
# ⇒ 帶 0.02 已比觀測散布寬一個量級，只用來擋「量具回傳常數／不同 build」。
TOL_DEFAULT = 0.02


def _f(x):
    try:
        return float(x)
    except Exception:  # noqa: BLE001
        return None


def parse(path):
    """把一支臂的 stderr 解析成計數器字典。只讀列印，不碰 src／不重跑引擎。"""
    out = dict(path=path, rho_lines=0, cap=0, phase_skip=0, sums=[], fills=[],
               n_layer=None, steps=None, hard=[], exists=os.path.exists(path))
    if not out["exists"]:
        return out
    with open(path, "r", errors="replace") as f:
        for ln in f:
            if "CGC-RHO" in ln:
                out["rho_lines"] += 1
            if ln.startswith("CGC-RHO-CAP:"):
                out["cap"] += 1
            elif ln.startswith("CGC-RHO-PHASE-SKIP:"):
                out["phase_skip"] += 1
            elif ln.startswith("CGC-RHO-SUM:"):
                d = {}
                for k in ("steps", "layers", "skip", "h_layers"):
                    m = re.search(r"\b%s=(\d+)" % k, ln)
                    if m:
                        d[k] = int(m.group(1))
                for k in ("rho_tok", "cov_uni", "h_step", "h_all"):
                    m = re.search(r"\b%s=([-\d.]+)" % k, ln)
                    if m:
                        d[k] = _f(m.group(1))
                out["sums"].append(d)
            elif ln.startswith("CGC-RHO-FILL:"):
                d = {}
                for k in ("layers", "queued", "cum_skipped"):
                    m = re.search(r"\b%s=(\d+)" % k, ln)
                    if m:
                        d[k] = int(m.group(1))
                m = re.search(r"per_layer=([-\d.]+)", ln)
                if m:
                    d["per_layer"] = _f(m.group(1))
                out["fills"].append(d)
            elif ln.startswith("CGC-SHAPE "):
                m = re.search(r"\bn_layer=(\d+)", ln)
                if m:
                    out["n_layer"] = int(m.group(1))
            elif ln.startswith("CGC-RHO-MISS:") or ln.startswith("CGC-RHO-ERROR"):
                out["hard"].append(ln.strip())
    if out["sums"]:
        out["steps"] = out["sums"][-1].get("steps")
    return out


def _check_arm(a, sat):
    """單支見證臂的結構檢查 ⇒ (ok, readings, why)"""
    if not a["exists"]:
        return False, a, "見證 log 不存在：%s" % a["path"]
    if a["hard"]:
        return False, a, "見證 log 出現 %s（%d 行）⇒ 影子路由有 'MISS'／'ERROR'，這次的計數不可用" % (
            a["hard"][0].split(":")[0], len(a["hard"]))
    if not a["sums"]:
        return False, a, "見證臂沒有任何 `CGC-RHO-SUM` 列印 ⇒ 收貨端沒有跑（%s）" % a["path"]
    if a["cap"] < 1:
        return False, a, "見證臂沒有任何 `CGC-RHO-CAP` ⇒ 影子張量沒被取得"
    if a["phase_skip"] < 1:
        return False, a, "見證臂缺 `CGC-RHO-PHASE-SKIP`（prefill 依設計要整段排除）⇒ 這支臂可能不是 decode 路徑"
    nl = a["n_layer"] or 40
    prev = None
    for row in a["sums"]:
        if row.get("skip"):
            return False, a, "`skip=%s` 在 steps=%s ⇒ 有層『順序不對或沒量到』（量具不可信，不是 0）" % (
                row.get("skip"), row.get("steps"))
        if prev is not None:
            d_st = row["steps"] - prev["steps"]
            d_lay = row["layers"] - prev["layers"]
            if d_st != 1 or d_lay != nl * d_st:
                return False, a, "步／層不配：steps %s→%s 而 layers %s→%s（每步應 %d 層）" % (
                    prev["steps"], row["steps"], prev["layers"], row["layers"], nl)
        prev = row
    last = a["sums"][-1]
    cov = last.get("cov_uni")
    if cov is None:
        return False, a, "最後一列沒有 `cov_uni`"
    if cov < sat:
        return False, a, "`cov_uni=%.4f` < 飽和門檻 %.2f ⇒ 影子路由沒在收貨（這不是「收得差」，是沒跑）" % (
            cov, sat)
    return True, a, ""


def judge(control, arms, fill=None, sat=SAT_DEFAULT, tol=TOL_DEFAULT, root=None):
    """⇒ (verdict, why, readings)。verdict ∈ {INSTRUMENT_LIVE, REFUSE}。"""
    rp = (lambda p: p if not root or os.path.isabs(p) else os.path.join(root, p))
    ctl = parse(rp(control)) if control else None
    arm_logs = [parse(rp(p)) for p in (arms or [])]
    readings = {"control": None, "arms": [], "fill": None}
    if ctl is None:
        return "REFUSE", "缺 control 臂（沒有對照臂就分不出『量具活著』與『本來就有列印』）", readings
    if not ctl["exists"]:
        return "REFUSE", "對照臂 log 不存在：%s" % ctl["path"], readings
    if ctl["rho_lines"] != 0:
        return "REFUSE", "對照臂出現 %d 行 `CGC-RHO*` ⇒ 它根本不是對照臂（沒關掉 probe？）" % ctl["rho_lines"], readings
    readings["control"] = {"path": ctl["path"], "rho_lines": 0}
    if not arm_logs:
        return "REFUSE", "缺見證臂（至少要一支 `+CGC_RHO_PROBE=1`）", readings
    for a in arm_logs:
        ok, _, why = _check_arm(a, sat)
        last = a["sums"][-1] if a["sums"] else {}
        readings["arms"].append({
            "path": a["path"], "ok": ok,
            "steps": last.get("steps"), "layers": last.get("layers"),
            "cap": a["cap"], "skip": last.get("skip"),
            "cov_uni": last.get("cov_uni"), "rho_tok": last.get("rho_tok"),
            "h_step": last.get("h_step"), "h_all": last.get("h_all"),
            "h_layers": last.get("h_layers"),
        })
        if not ok:
            return "REFUSE", why, readings
    # 跨場次一致（可複現是端點的一部分）
    base = readings["arms"][0]
    for r in readings["arms"][1:]:
        if r["steps"] != base["steps"] or r["layers"] != base["layers"]:
            return "REFUSE", "跨場次不一致：%s 的 steps/layers=%s/%s ≠ %s/%s ⇒ 量具回的不是同一個東西" % (
                os.path.basename(r["path"]), r["steps"], r["layers"], base["steps"], base["layers"]), readings
        if abs((r["cov_uni"] or 0.0) - (base["cov_uni"] or 0.0)) > tol:
            return "REFUSE", "跨場次覆蓋率差 %.4f > 帶 %.2f（%s vs %s）⇒ 不是可複現的讀數" % (
                abs((r["cov_uni"] or 0.0) - (base["cov_uni"] or 0.0)), tol,
                r["cov_uni"], base["cov_uni"]), readings
    if fill:
        fb = parse(rp(fill))
        if not fb["exists"]:
            return "REFUSE", "fill 臂 log 不存在：%s" % fb["path"], readings
        if not fb["fills"]:
            return "REFUSE", "fill 臂沒有任何 `CGC-RHO-FILL` 列印（`CGC_RHO_FILL=1` 沒生效？）", readings
        for row in fb["fills"]:
            pl, q, lay = row.get("per_layer"), row.get("queued"), row.get("layers")
            if None in (pl, q, lay) or abs(q - pl * lay) > 0.5:
                return "REFUSE", "fill 記帳不符：layers=%s per_layer=%s queued=%s（應 queued=per_layer×layers）" % (
                    lay, pl, q), readings
            if row.get("cum_skipped"):
                return "REFUSE", "fill 有 `cum_skipped=%s` ⇒ 有預測被丟（端點①要求全數入隊）" % row["cum_skipped"], readings
        last = fb["fills"][-1]
        readings["fill"] = {"path": fb["path"], "queued": last.get("queued"),
                            "per_layer": last.get("per_layer"), "layers": last.get("layers"),
                            "cum_skipped": last.get("cum_skipped"), "rows": len(fb["fills"])}
        # fill 臂本身也是一支 probe 臂（它也要過集貨／飽和／一致性）
        ok, _, why = _check_arm(fb, sat)
        if not ok:
            return "REFUSE", "fill 臂（同時是 probe 臂）不過：%s" % why, readings
        if fb["sums"][-1].get("steps") != base["steps"]:
            return "REFUSE", "fill 臂 steps=%s ≠ 見證臂 %s ⇒ 兩趟不是同長度" % (
                fb["sums"][-1].get("steps"), base["steps"]), readings
    return "INSTRUMENT_LIVE", "", readings


# ── selftest（合成 log，不碰真產物；每一條都對應上面一條 fail-closed 規則）───────────
def _mklog(n_layer=40, steps=4, cov=0.8077, skip=0, cap=2, phase_skip=1, drop_step=False,
           fill=False, fill_broken=False, hard=False):
    L = []
    if phase_skip:
        L.append("CGC-RHO-PHASE-SKIP: ntok=2048 > decode_width=8 -- shadow capture and rho fill are decode-only")
    for _ in range(cap):
        L.append("CGC-RHO-CAP: name=cgc_rho_logits-0 il=0 ne=[256,1] type=0")
    L.append("CGC-SHAPE v=1 phase=final M=8 width=8 pool_cap_slots=143 slots_layer=143 n_layer=%d" % n_layer)
    for s in range(steps + 1):
        lay = 1 + n_layer * s
        if drop_step and s == 2:
            lay = 1 + n_layer * 2 + 1        # 一步只收了一層 ⇒ 步／層不配
        L.append("CGC-RHO-SUM: steps=%d layers=%d skip=%d rho_tok=%.4f cov_uni=%.4f h_step=0.4194 h_layers=%d h_all=0.0015"
                 % (s, lay, skip, cov, cov, max(0, lay - 40)))
    if fill:
        for s in range(steps + 1):
            lay = 1 + n_layer * s
            pl = 8.0
            q = int(pl * lay) + (1 if (fill_broken and s == steps) else 0)
            L.append("CGC-RHO-FILL: layers=%d queued=%d per_layer=%.2f (cum_skipped=%d)"
                     % (lay, q, pl, 1 if fill_broken else 0))
    if hard:
        L.append("CGC-RHO-MISS: il=0 ntok=1 fresh=0 shadow_stamp=0 hook_stamp=1")
    return "\n".join(L) + "\n"


def selftest():
    ok_n = 0
    tmp = tempfile.mkdtemp(prefix="rho_inst_")
    def W(name, txt):
        p = os.path.join(tmp, name)
        with open(p, "w") as f:
            f.write(txt)
        return p
    ctl = W("ctl.log", "CGC-SHAPE v=1 phase=final n_layer=40\nllama_expert_cache: final stats: runtime requests=1 hits=1\n")
    a1 = W("a1.log", _mklog())
    a2 = W("a2.log", _mklog(cov=0.8048))
    fl = W("fl.log", _mklog(fill=True))
    cases = [
        ("S1 好的一組（控制＋2 見證＋fill）", (ctl, [a1, a2], fl), "INSTRUMENT_LIVE"),
        ("S2 對照臂也印了 CGC-RHO*", (W("c2.log", _mklog(cap=1)), [a1], None), "REFUSE"),
        ("S3 見證臂 skip>0", (ctl, [W("a3.log", _mklog(skip=1))], None), "REFUSE"),
        ("S4 覆蓋低於飽和門檻", (ctl, [W("a4.log", _mklog(cov=0.42))], None), "REFUSE"),
        ("S5 跨場次不一致（覆蓋差 > 帶）", (ctl, [a1, W("a5.log", _mklog(cov=0.76))], None), "REFUSE"),
        ("S6 步／層不配", (ctl, [W("a6.log", _mklog(drop_step=True))], None), "REFUSE"),
        ("S7 fill 記帳不符", (ctl, [a1], W("f7.log", _mklog(fill=True, fill_broken=True))), "REFUSE"),
        ("S8 見證臂有 MISS 行", (ctl, [W("a8.log", _mklog(hard=True))], None), "REFUSE"),
        ("S9 log 不存在", (ctl, [os.path.join(tmp, "nope.log")], None), "REFUSE"),
    ]
    for name, (c, ar, f), want in cases:
        got, why, _ = judge(c, ar, f)
        good = got == want
        print("  %-34s -> %-16s %s%s" % (name, got, "PASS" if good else "FAIL", "" if good else "  (%s)" % why))
        ok_n += 1 if good else 0
    print("SELFTEST %s（%d/%d）" % ("PASS" if ok_n == len(cases) else "FAIL", ok_n, len(cases)))
    return 0 if ok_n == len(cases) else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="S3-b 端點①（ρ 儀器）見證檢查器")
    ap.add_argument("--control", help="對照臂 stderr（prod-new，不開 CGC_RHO_PROBE）")
    ap.add_argument("--arm", action="append", default=[], help="見證臂 stderr（可多次）")
    ap.add_argument("--fill", default=None, help="fill 臂 stderr（CGC_RHO_FILL=1）")
    ap.add_argument("--sat", type=float, default=SAT_DEFAULT)
    ap.add_argument("--tol", type=float, default=TOL_DEFAULT)
    ap.add_argument("--root", default=None)
    ap.add_argument("--json", dest="json_path", default=None)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    if not (a.control and a.arm):
        ap.error("需要 --control 與至少一個 --arm（或用 --selftest）")
    v, why, readings = judge(a.control, a.arm, a.fill, a.sat, a.tol, root=a.root)
    out = {"verdict": v, "why": why, "readings": readings}
    print("VERDICT: %s%s" % (v, ("  -- %s" % why) if why else ""))
    for r in readings.get("arms") or []:
        print("  見證 %s: steps=%s layers=%s cap=%s skip=%s cov_uni=%s h_step=%s"
              % (os.path.basename(r["path"]), r["steps"], r["layers"], r["cap"], r["skip"],
                 r["cov_uni"], r["h_step"]))
    if readings.get("fill"):
        f = readings["fill"]
        print("  fill %s: queued=%s per_layer=%s layers=%s cum_skipped=%s (%d 列)"
              % (os.path.basename(f["path"]), f["queued"], f["per_layer"], f["layers"],
                 f["cum_skipped"], f["rows"]))
    if a.json_path:
        with open(a.json_path, "w") as fh:
            json.dump(out, fh, ensure_ascii=False, indent=2)
    return 0 if v == "INSTRUMENT_LIVE" else 1


if __name__ == "__main__":
    sys.exit(main())
