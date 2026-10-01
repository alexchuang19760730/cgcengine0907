#!/usr/bin/env python3
"""L25-2 的 skip-probe **接收器**：閘（`--check`）／成對命令（`--plan`）／預註冊判詞（`--judge`）。

為什麼是「接收器」而不是探針本身：探針（env-gated、跳過所有輸出元素數 ≤8 的 dispatch）是**引擎**
的工作（`docs/L252_MECHANISM_NOMINATION_2026-09-30.md` §3：立項與那支 gated patch 要同一批落地），
而「量到之後怎麼判」不必等引擎 —— 把判別句先寫死，patch 一落地就是：套用 → 兩條命令 → 判詞。

跑前寫死（卡的 acceptance／falsify；§18 §七(4)）：
  S1 旗標 `CGC_SKIP_TINY_DISPATCH` 要在樹上，而且**預設關**（env-gated）；
  S2 launcher 要**轉送**它（`run_server.sh` 的 allowlist 陷阱：不轉送 ⇒ 旗標到不了引擎，
     見 docs/RHO_PRICE_AUTHROW_2026-09-30.md §6 的同型教訓）；
  S3 機制要印 **VOID 標記**（`CGC-SKIP-TINY`）——這一臂的輸出必然是錯的，收貨靠標記不靠 t/s；
  S4 判詞只取 Δstep（兩臂 decode 行的 ms/step 差）：門檻 **1.2 ms**（赤字）⇒ `LEVER`；
     < 1.2 ⇒ `DEAD`（群集 1 不是槓桿，判死附機制）；> 7.4 ms（上界）⇒ 先懷疑量具，判 `SUSPECT`；
  S5 兩臂的窗口與離散照樣要乾淨（attribution=none、全 rep max/min ≤1.10、reps≥3、profile 可引用）
     —— R6（輸出未驗）對這一臂不適用（定義上是 VOID），其餘照舊；
  S6 缺檔／讀不到 ⇒ `REFUSE`（fail-closed）。

用法：
  python3 scripts/check/skip_probe.py --check
  python3 scripts/check/skip_probe.py --plan
  python3 scripts/check/skip_probe.py --judge --a <A.json> --b <B.json> [--write]
  python3 scripts/check/skip_probe.py --selftest
"""

import argparse
import glob
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))

FLAG = "CGC_SKIP_TINY_DISPATCH"
VOID_MARKER = "CGC-SKIP-TINY"
TINY_MAX_ELEMS = 8          # 「tiny」的定義（輸出元素數 ≤8）

# 判詞門檻（跑前寫死；改這裡＝改判別句）
LEVER_MS = 1.2              # 赤字
CEILING_MS = 7.4            # 群集 1 的上界（超過 ⇒ 先懷疑量具）
SPREAD_LIMIT = 1.10         # 全 rep max/min（與 quote_gate 的 R2／R3 同一條）
MIN_REPS = 3

# 要掃的座標：機制（引擎）＋轉送（launcher）＋記帳（VOID 標記）
SITES = {
    "engine": ["src/llama.cpp/src/llama-graph.cpp", "src/llama.cpp/src/llama-context.cpp"],
    "launcher": ["scripts/run_server.sh"],
    "card": ["scripts/check/charters/e-l252-skip-probe-2026-09-30.yaml"],
}


def _qg():
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    import quote_gate
    return quote_gate


def scan(root: str = ROOT) -> dict:
    """S1–S3：旗標在不在（引擎／launcher）、有沒有 VOID 標記。不猜：逐座標掃字串。"""
    hits, marker = {}, {}
    for site, paths in SITES.items():
        hits[site], marker[site] = {}, {}
        for rel in paths:
            for p in sorted(glob.glob(os.path.join(root, rel))):
                try:
                    txt = open(p, encoding="utf-8", errors="replace").read()
                except OSError:
                    continue
                hits[site][rel] = hits[site].get(rel, 0) + txt.count(FLAG)
                marker[site][rel] = marker[site].get(rel, 0) + txt.count(VOID_MARKER)
    engine = sum(v for d in (hits["engine"],) for v in d.values())
    launcher = sum(v for d in (hits["launcher"],) for v in d.values())
    void = sum(v for d in (marker["engine"],) for v in d.values())
    missing = []
    if engine == 0:
        missing.append("引擎裡沒有 %s（src/llama.cpp/src）—— gated patch 還沒落地" % FLAG)
    if engine and launcher == 0:
        missing.append("launcher（scripts/run_server.sh）沒有轉送 %s ⇒ 旗標到不了子行程" % FLAG)
    if engine and void == 0:
        missing.append("引擎沒有印 VOID 標記 %s ⇒ 這一臂定義上是 VOID，沒有標記就分不出"
                       "「機制在跑」與「旗標被忽略」" % VOID_MARKER)
    return dict(flag=FLAG, void_marker=VOID_MARKER, tiny_max_elems=TINY_MAX_ELEMS,
                hits=hits, marker=marker, missing=missing, runnable=(not missing))


def plan(root: str = ROOT) -> list:
    """S4：成對的兩條命令（交付 cell；格維度由 `cell_contract --cell delivery` 印出）。"""
    base = ["python3", "scripts/check/harness.py", "bench",
            "--charter", "scripts/check/charters/e-l252-skip-probe-2026-09-30.yaml"]
    out = []
    try:
        if HERE not in sys.path:
            sys.path.insert(0, HERE)
        import cell_contract as CC
        # API 對齊（2026-10-01）：resolve_cell 吃 (contract, name)，bench_flags 回 (flags, warn)。
        # 舊寫法在這一版是 TypeError ⇒ --plan 會退化成一條「讀不到 cell_contract」的註解（那會讓
        # 這一趟的「成對」靜默消失，正是 receiver 要防的那種漂移）。
        _cell_name, cell = CC.resolve_cell(CC.load_contract(), "delivery")
        flags, _warn = CC.bench_flags(cell)
        # `--cell <name>` 不可省：harness 用它去對測試卡 §2.5 的**權威** block（少了它會拿
        # `(default)` 的權威值來拒跑 —— 這一步 2026-10-01 就是這樣被擋下來的）。
        base += ["--cell", _cell_name] + list(flags)
    except Exception as exc:  # noqa: BLE001
        base += ["# ⚠ 讀不到 cell_contract（%s）：先跑 cell_contract.py --cell delivery 把格維度貼上" % exc]
    for arm, tag in (("prod-new", "A"), ("prod-new:" + FLAG + "=1", "B")):
        out.append(dict(arm=tag, arm_spec=arm,
                        cmd=base + ["--arm", arm, "--json",
                                    "Backup/l252_skipprobe_%s/order%s.json" % (_today(), tag)]))
    return out


def _today() -> str:
    return time.strftime("%Y%m%d")


def _ms_of(rec: dict):
    """decode 行的 ms/step（權威 row ⇒ p0）。回 (ms, why)。"""
    m = rec.get("metrics") or {}
    avg = m.get("avg")
    if not avg:
        return None, "沒有 avg_ts"
    return 1000.0 / float(avg), "ms/step = 1000 / %.3f t/s" % avg


def _row_ok(rec: dict) -> tuple:
    """S5：窗口與離散（R6 除外 —— 這一臂定義上是 VOID）。"""
    m = rec.get("metrics") or {}
    why = []
    if (m.get("attrib") or "?") not in ("none",):
        why.append("attribution=%s≠none" % m.get("attrib"))
    for k in ("all_spread", "kept_spread"):
        v = m.get(k)
        if v is None or v > SPREAD_LIMIT:
            why.append("%s=%s>%.2f" % (k, v, SPREAD_LIMIT))
    if (m.get("reps") or 0) < MIN_REPS:
        why.append("reps=%s<%d" % (m.get("reps"), MIN_REPS))
    for r in rec.get("reasons") or []:
        if r.startswith("輸出未驗"):      # R6 對 VOID 臂不適用（定義上）
            continue
        why.append(r)
    return (not why), why


def _decode(path: str):
    recs = _qg().scan([path])
    dec = [r for r in recs if str(r.get("shape", "")).startswith("p0/n")]
    return (dec[0] if len(dec) == 1 else None), ("decode 行不唯一/沒有（%d 條）" % len(dec))


def judge(a_path: str, b_path: str) -> dict:
    out = dict(product="L25-2 skip-probe（群集 1 的 Δstep）", at=time.strftime("%F %T %z"),
               flag=FLAG, void_marker=VOID_MARKER,
               thresholds=dict(lever_ms=LEVER_MS, ceiling_ms=CEILING_MS, spread_limit=SPREAD_LIMIT))
    ms = {}
    for tag, path in (("A", a_path), ("B", b_path)):
        if not path or not os.path.exists(path):
            out.update(verdict="REFUSE", why="%s 臂的產物不存在（%s）" % (tag, path))
            return out
        rec, why = _decode(path)
        if rec is None:
            out.update(verdict="REFUSE", why="%s：%s" % (tag, why))
            return out
        ms[tag], why_ms = _ms_of(rec)
        if ms[tag] is None:
            out.update(verdict="REFUSE", why="%s：%s" % (tag, why_ms))
            return out
        ok, reasons = _row_ok(rec)
        out["arm_%s" % tag] = dict(path=path, ms_per_step=ms[tag], row_ok=ok, reasons=reasons,
                                   tps=(rec.get("metrics") or {}).get("avg"),
                                   spread=(rec.get("metrics") or {}).get("all_spread"))
        if not ok:
            out.update(verdict="REFUSE",
                       why="%s 臂的窗口／離散不乾淨 ⇒ 不判 LEVER／DEAD：%s" % (tag, "；".join(reasons)))
            return out
    delta = ms["A"] - ms["B"]        # 正 = probe-on 比較快（省下來的 ms/step）
    spread_ms = max(abs((out["arm_A"]["spread"] or 1.0) - 1.0),
                    abs((out["arm_B"]["spread"] or 1.0) - 1.0)) * max(ms["A"], ms["B"])
    out.update(delta_step_ms=delta, spread_ms=spread_ms)
    if abs(delta) <= spread_ms:
        out.update(verdict="REFUSE", why="|Δstep|=%.3f ms ≤ 兩臂散布 %.3f ms ⇒ 不可辨識"
                   % (abs(delta), spread_ms))
    elif delta > CEILING_MS:
        out.update(verdict="SUSPECT", why="Δstep=%.3f ms > 上界 %.1f ms ⇒ 先懷疑量具（或這一臂不只"
                                          "跳過題目允許的東西）" % (delta, CEILING_MS))
    elif delta >= LEVER_MS:
        out.update(verdict="LEVER", why="Δstep=%.3f ms ≥ %.1f ms（赤字）⇒ 值得寫 ffn_moe_weighted"
                   % (delta, LEVER_MS))
    else:
        out.update(verdict="DEAD", why="Δstep=%.3f ms < %.1f ms ⇒ 群集 1 不是槓桿：L25-2 與 "
                                       "sg-kernel-busy 判死（附機制）" % (delta, LEVER_MS))
    out["next"] = (["照卡新增工作項：寫 ffn_moe_weighted（承載算子）；先立項、再動 src/"]
                   if out["verdict"] == "LEVER" else
                   ["把判詞寫回看板 L25-2（%s）" % out["verdict"]])
    return out


def report_scan(s: dict) -> None:
    print("L25-2 skip-probe 接收器：旗標 %s（env-gated、預設關）＋ VOID 標記 %s（輸出必然錯）"
          % (s["flag"], s["void_marker"]))
    for site, d in s["hits"].items():
        for rel, n in d.items():
            print("  %-9s %-52s 旗標×%d 標記×%d" % (site, rel, n, s["marker"][site].get(rel, 0)))
    if s["runnable"]:
        print("VERDICT: RUNNABLE -- 閘與標記都在 ⇒ 照 `--plan` 跑成對，再 `--judge`")
    else:
        print("VERDICT: BLOCKED -- %d 個座標還沒好在：" % len(s["missing"]))
        for m in s["missing"]:
            print("    · %s" % m)
        print("  ⛔ patch 落地前不發車（卡的規矩：立項與 gated patch 同批）")


def selftest() -> int:
    import tempfile

    ok = [0, 0]

    def case(name, cond, detail=""):
        ok[1] += 1
        ok[0] += 1 if cond else 0
        print("  %-62s -> %s%s" % (name, "PASS" if cond else "FAIL", "" if cond else "  " + str(detail)))

    def _mk(path, tps, samples=None, attrib="none", cell="delivery"):
        import statistics
        samples = samples or [tps] * 3
        prod = dict(tag="prod-new", profile="prod-new", warm_skip=64, warm_skip_applied=True,
                    cell=dict(named_cell=cell, prompt=0, gen=128, depths=512, reps=3, warm_skip=64),
                    attribution=dict(verdict=attrib, thermal_worst="NOMINAL"),
                    contract=dict(ok=True, cell=cell),
                    rows=[dict(n_prompt=0, n_gen=64, avg_ts=tps, stddev_ts=statistics.pstdev(samples),
                               samples_ts=samples)])
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump([prod], fh)
        return path

    with tempfile.TemporaryDirectory() as td:
        # A：87.0 ms/step，逐 rep 很緊（range 0.15 ms）—— 否則「不可辨識」會先擋下一切。
        a = _mk(os.path.join(td, "A.json"), 11.50, [11.49, 11.50, 11.51])
        b = _mk(os.path.join(td, "B.json"), 12.60, [12.59, 12.60, 12.61])   # 79.4 ms ⇒ Δ≈7.7 ms
        v = judge(a, b)
        case("Δ=7.7 ms > 上界 7.4 ⇒ SUSPECT（先懷疑量具）", v["verdict"] == "SUSPECT", v["verdict"])
        b = _mk(os.path.join(td, "B.json"), 11.68, [11.66, 11.68, 11.70])   # 85.6 ms ⇒ Δ≈1.4 ms
        v = judge(a, b)
        case("Δ≈1.4 ms ≥ 1.2 且 > 兩臂散布 ⇒ LEVER（值得寫承載算子）",
             v["verdict"] == "LEVER", (v["verdict"], v.get("delta_step_ms"), v.get("spread_ms")))
        case("LEVER 的 next 指向 ffn_moe_weighted", any("ffn_moe_weighted" in x for x in v.get("next", [])),
             v.get("next"))
        # 注意：avg 必須等於 samples 的平均（R0 產物自洽）；Δ 要 > 散布且 < 1.2 ⇒ DEAD
        b = _mk(os.path.join(td, "B.json"), 11.58, [11.57, 11.58, 11.59])   # 86.4 ms ⇒ Δ≈0.6 ms
        v = judge(a, b)
        case("Δ≈0.2 ms < 1.2 ⇒ DEAD（判死附機制）", v["verdict"] == "DEAD", (v["verdict"], v.get("delta_step_ms")))
        # 效應小於散布 ⇒ 不可辨識（不是 LEVER 也不是 DEAD）
        a2 = _mk(os.path.join(td, "A2.json"), 11.50, [11.30, 11.50, 11.70])
        case("|Δ| ≤ 兩臂散布（ms）⇒ REFUSE（不可辨識）",
             judge(a2, b)["verdict"] == "REFUSE", judge(a2, b)["why"])
        case("B 臂窗口不乾淨（thermal）⇒ REFUSE，不判 LEVER／DEAD",
             judge(a, _mk(os.path.join(td, "B2.json"), 12.6, attrib="thermal"))["verdict"] == "REFUSE")
        case("缺檔 ⇒ REFUSE（fail-closed）", judge(a, os.path.join(td, "nope.json"))["verdict"] == "REFUSE")
        # R6（輸出未驗）不適用於 VOID 臂 ⇒ 不得因此擋下判詞
        rec = _qg().scan([a])[0] if _qg().scan([a]) else None
        case("S5：R6 的『輸出未驗』被排除（VOID 臂定義上必錯）",
             all(not r.startswith("輸出未驗") for r in (rec.get("reasons") or [])) if rec else True,
             rec.get("reasons") if rec else None)

        # 閘的掃描：fixture 樹（旗標只在引擎、launcher 沒轉送、沒有 VOID 標記）
        tree = os.path.join(td, "tree")
        os.makedirs(os.path.join(tree, "src/llama.cpp/src"), exist_ok=True)
        os.makedirs(os.path.join(tree, "scripts"), exist_ok=True)
        os.makedirs(os.path.join(tree, "scripts/check/charters"), exist_ok=True)
        with open(os.path.join(tree, "src/llama.cpp/src/llama-graph.cpp"), "w") as fh:
            fh.write("static bool skip = getenv(\"%s\") != nullptr;\n" % FLAG)
        s = scan(tree)
        case("只落了引擎的一半 ⇒ BLOCKED 且點名 launcher 沒轉送／沒有 VOID 標記",
             s["runnable"] is False and any("launcher" in m for m in s["missing"])
             and any("VOID" in m for m in s["missing"]), s["missing"])
        with open(os.path.join(tree, "scripts/run_server.sh"), "w") as fh:
            fh.write("export %s=1\n" % FLAG)
        with open(os.path.join(tree, "src/llama.cpp/src/llama-graph.cpp"), "a") as fh:
            fh.write("fprintf(stderr, \"%s: VOID\\n\");\n" % VOID_MARKER)
        s = scan(tree)
        case("引擎＋launcher＋VOID 標記到齊 ⇒ RUNNABLE", s["runnable"] is True, s["missing"])

    print("== selftest %d/%d ==" % (ok[0], ok[1]))
    return 0 if ok[0] == ok[1] else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="L25-2 skip-probe 接收器（閘／命令／預註冊判詞）")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--plan", action="store_true")
    ap.add_argument("--judge", action="store_true")
    ap.add_argument("--a", default=None, help="A（對照）產物")
    ap.add_argument("--b", default=None, help="B（probe-on）產物")
    ap.add_argument("--write", action="store_true", help="把判詞寫進 B 產物旁的 verdict.json")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--root", default=ROOT)
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    if args.plan:
        for x in plan(args.root):
            print("# %s（%s）" % (x["arm"], x["arm_spec"]))
            print(" ".join(x["cmd"]))
        return 0
    if args.judge:
        v = judge(args.a, args.b)
        print(json.dumps(v, ensure_ascii=False, indent=1))
        if args.write and args.b:
            p = os.path.join(os.path.dirname(args.b), "verdict.json")
            with open(p, "w", encoding="utf-8") as fh:
                json.dump(v, fh, ensure_ascii=False, indent=1)
            print("判詞落檔：%s" % p)
        return 0 if v["verdict"] in ("LEVER", "DEAD") else 2
    report_scan(scan(args.root))
    return 0


if __name__ == "__main__":
    sys.exit(main())
