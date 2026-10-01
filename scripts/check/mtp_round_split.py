#!/usr/bin/env python3
"""把一趟 MTP-on 的產物拆成「一輪裡 draft 與 verify 各多少 ms」⇒ m 與 v。

用法：
    python3 scripts/check/mtp_round_split.py <run.json> [<stderr.log>]

輸出：k_eff / E / T_draft（差分穩態）/ T_round / T_ver / m / v ＋ 五條有效性閘。

為什麼要這支程式（三個已經踩過的坑都寫在這裡，別再重踩）：
  1. `ms_per_round` 是**累計均值、未收斂**（實測末行高估穩態 20–50%）⇒ 必須**差分相鄰行**。
  2. 一行可能**重複印**（reps）⇒ 取末行前先按 `calls_draft` 去重，否則差分會算出 0。
  3. `k_eff` 只能用 `gen_tok_per_round` 讀，**不能用 `--spec-draft-n-max`**（鐵律 6）；
     而且 k_eff ≥ 2 在 16 GB 盒子上會 thrash（`on_k3` 實測 tg 0.39）⇒ 新增閘 (e)。

算式（與 `charters/e-fillbudget-m-decomp-2026-09-29.yaml` 的 `p4_preregistered.formula` 一致）：
    E = emit_tok_per_round          k_eff = gen_tok_per_round
    T_draft = 差分穩態 ms/輪        T_round = E * 1000 / tg
    T_ver   = T_round - T_draft     m = T_draft/86.13   v = T_ver/86.13
⛔ 不做「MTP 加速 X%」：ON/OFF 是不同輸出函數（M2 已 FAIL）。
"""
import argparse
import json
import os
import re
import statistics
import sys

T_PLAIN_MS = 86.13          # 錨點 11.61 t/s（MTP off，build 630）
TG_MIN = 8.0                # 閘 (b)
HIT_MIN = 90.0              # 閘 (c)
K_EFF_TOL = 0.15            # 閘 (e)：|k_eff - 1| <= 0.15

PERF_RE = re.compile(
    r"CGC-MTP-PERF type=\S+ calls_begin=(\d+) calls_draft=(\d+) calls_accept=(\d+) "
    r"gen_tokens=(\d+) acc_tokens=(\d+) t_begin_ms=([\d.]+) t_draft_ms=([\d.]+) t_accept_ms=([\d.]+) "
    r"acc_rate=([\d.]+) gen_tok_per_round=([\d.]+) acc_tok_per_round=([\d.]+) "
    r"emit_tok_per_round=([\d.]+) ms_per_round=([\d.]+)")


def parse_perf(path):
    """回傳按 calls_draft 去重後的 dict 列（升冪），每個欄位**按名字**取。

    ⚠ 2026-09-30 修正（§63）：這裡原本取 `group(8)`＝**t_accept_ms**（恆為 0.1–0.2），
    下面卻把它當 `t_draft_ms` 差分 ⇒ T_draft 恆為 0.00 ms、m 恆為 0.000。
    儀器欄位錯位，而沒有閘門消費它，所以錯了兩天沒人發現。
    欄位順序以 PERF_RE 為準：6=t_begin 7=t_draft 8=t_accept 9=acc_rate
    10=gen_tok_per_round 11=acc_tok_per_round 12=emit_tok_per_round 13=ms_per_round。
    """
    if not path or not os.path.exists(path):
        return []
    rows = {}
    for line in open(path, errors="ignore"):
        m = PERF_RE.search(line)
        if not m:
            continue
        cd = int(m.group(2))
        rows[cd] = {
            "cd": cd,
            "t_draft": float(m.group(7)),
            "t_accept": float(m.group(8)),
            "ms_per_round": float(m.group(13)),
            "gen_pr": float(m.group(10)),
            "acc_pr": float(m.group(11)),
            "emit": float(m.group(12)),
            "acc_rate": float(m.group(9)),
        }
    return [rows[k] for k in sorted(rows)]


def steady_ms_per_round(rows):
    """差分相鄰行取增量 ms/輪（末行累計均會高估 20–50%）。"""
    out = []
    for r1, r2 in zip(rows, rows[1:]):
        dc = r2["cd"] - r1["cd"]
        if dc > 0:
            out.append(((r2["t_draft"] - r1["t_draft"]) / dc, dc))
    return out


def expected_tg_rows(r):
    """這一場**宣告**了幾個 depth ⇒ 就該有幾個 tg row（閘 (a) 的分母）。

    ⚠ 2026-09-30 修正（§63）：原本寫死 `len(tg) >= 2`，那是**預設／矩陣 cell** 的形狀。
    單深度的 cell（例如看板的交付 cell `depths: "512"`）永遠只有 1 個 tg row
    ⇒ 閘 (a) 對它**結構上不可能過**，而 L25-5 要的正是單深度那一趟。
    cell 沒宣告 depths 時保留舊行為（>=2），以免放寬既有那批矩陣產物。
    """
    d = ((r.get("cell") or {}).get("depths") or "")
    n = len([x for x in re.split(r"[;,\s]+", str(d)) if x.strip()])
    return n if n else 2


def _resolve_stderr(run_json, stderr_log=None):
    if stderr_log:
        return stderr_log
    cands = []
    base = os.path.dirname(os.path.abspath(run_json))
    for d in (base, os.path.join(base, "logs")):
        for f in (os.listdir(d) if os.path.isdir(d) else []):
            if f.endswith(".stderr.log") or ".stderr" in f:
                cands.append(os.path.join(d, f))
    return cands[0] if cands else None


def judge(run_json, stderr_log=None):
    """唯一的判定入口 ⇒ dict（board 的 counter_verdict 也走這條，見 §63／D7b）。

    verdict: "OK"（五閘全過、m 算得出來）／None（判不了或不過）。
    """
    out = {"run": run_json, "stderr": _resolve_stderr(run_json, stderr_log), "gates": {},
           "verdict": None, "why": "", "rc": 1, "m": None, "v": None, "t_draft": None,
           "t_round": None, "tg_med": None, "hit": None, "k_eff": None, "E": None,
           "acc_rate": None, "perf_n": 0, "diffs": [], "attrib": None, "cell": None,
           "depths": None, "incomplete": None}
    if not os.path.exists(run_json):
        out["why"] = "產物不存在：%s" % run_json
        return out
    try:
        j = json.load(open(run_json))
    except Exception as exc:  # noqa: BLE001
        out["why"] = "產物讀不進來：%s: %s" % (type(exc).__name__, exc)
        return out
    r = j[0] if isinstance(j, list) else j
    rows = r.get("rows") or []
    tg = [x.get("avg_ts") for x in rows if (x.get("n_gen") or 0) > 0 and x.get("avg_ts")]
    tg_med = statistics.median(tg) if tg else None
    cache = r.get("cache") or {}
    hit = cache.get("hit_rate_pct")
    mem = (r.get("memory") or {}).get("launch") or {}
    sb, sa = r.get("sys_before") or {}, r.get("sys_after") or {}
    cell = r.get("cell") or {}
    out.update({"tg_med": tg_med, "hit": hit, "wall_s": r.get("wall_s"),
                "incomplete": r.get("incomplete"), "error": r.get("error"),
                "avail_mb": mem.get("pages_available_mb"), "free_mb": mem.get("pages_free_mb"),
                "pageins_dm": ((sa.get("pageins") or 0) - (sb.get("pageins") or 0)) / 1e6,
                "attrib": ((r.get("attribution") or {}).get("verdict")),
                "cell": cell.get("named_cell"), "depths": cell.get("depths")})

    perf = parse_perf(out["stderr"])
    out["perf_n"] = len(perf)
    if not perf:
        out["gates"] = {"(d) PERF>0  ": False}
        out["why"] = "CGC-MTP-PERF 0 行 ⇒ 閘 (d) 不過"
        return out
    last = perf[-1]
    diffs = steady_ms_per_round(perf)
    t_draft = diffs[-1][0] if diffs else last["t_draft"] / max(last["cd"], 1)
    out.update({"k_eff": last["gen_pr"], "E": last["emit"], "acc_rate": last["acc_rate"],
                "acc_pr": last["acc_pr"], "diffs": diffs, "perf_last_cd": last["cd"],
                "t_draft": t_draft, "t_draft_cum": last["t_draft"] / max(last["cd"], 1)})
    exp_tg = expected_tg_rows(r)
    out["exp_tg"] = exp_tg
    gates = {
        "(a) rc/完整": (not r.get("incomplete")) and len(tg) >= exp_tg,
        "(b) tg>=8.0": tg_med is not None and tg_med >= TG_MIN,
        "(c) hit>=90": hit is not None and hit >= HIT_MIN,
        "(d) PERF>0  ": len(perf) >= 1 and last["cd"] > 0,
        "(e) k_eff≈1 ": abs(last["gen_pr"] - 1.0) <= K_EFF_TOL,
    }
    out["gates"] = gates
    if not all(gates.values()) or tg_med is None:
        out["why"] = "閘不過 ⇒ UNDECIDABLE（不硬算）"
        return out
    t_round = last["emit"] * 1000.0 / tg_med
    out.update({"t_round": t_round, "t_ver": t_round - t_draft,
                "m": t_draft / T_PLAIN_MS, "v": (t_round - t_draft) / T_PLAIN_MS,
                "verdict": "OK", "rc": 0})
    return out


def print_result(res):
    print(f"run            : {res['run']}")
    print(f"stderr         : {res['stderr']}")
    print(f"cell           : {res.get('cell')}   depths={res.get('depths')}   "
          f"exp_tg={res.get('exp_tg')}   attrib={res.get('attrib')}")
    print(f"wall_s         : {res.get('wall_s')}   incomplete={res.get('incomplete')}   error={res.get('error')}")
    print(f"launch avail MB: {res.get('avail_mb')}   pages_free={res.get('free_mb')}")
    print(f"pageins Δ      : {res.get('pageins_dm'):.2f} M" if res.get("pageins_dm") is not None else "pageins Δ      : None")
    print(f"hit%           : {res.get('hit')}")
    print(f"tg (median)    : {res.get('tg_med')}")
    if not res.get("perf_n"):
        print("CGC-MTP-PERF   : ⛔ 0 行 ⇒ 閘 (d) 不過")
        return
    print(f"MTP-PERF 行數  : {res['perf_n']}（去重後）  末行 calls_draft={res.get('perf_last_cd')}")
    print(f"k_eff          : {res['k_eff']:.3f}    E(emit) = {res['E']:.3f}    acc_rate = {res['acc_rate']:.4f}")
    print("T_draft 差分   : " + (" | ".join(f"Δ{dc}輪 {v:.2f}" for v, dc in res["diffs"]) if res["diffs"] else "無"))
    print(f"T_draft 穩態   : {res['t_draft']:.2f} ms/輪（末行累計均 {res['t_draft_cum']:.2f}）")
    for k, v in res["gates"].items():
        print(f"gate {k}: {'✅' if v else '⛔'}")
    if res["verdict"] != "OK":
        print(f"\n== 閘 不過 ⇒ UNDECIDABLE（不硬算）：{res['why']} ==")
        return
    print(f"T_round        : {res['t_round']:.2f} ms/輪  （E·1000/tg）")
    print(f"T_ver          : {res['t_ver']:.2f} ms      （T_round − T_draft）")
    print(f"m = T_draft/{T_PLAIN_MS} = {res['m']:.3f}")
    print(f"v = T_ver  /{T_PLAIN_MS} = {res['v']:.3f}")


# ── 自測：釘住**文件已記載**的 m ≈ 0.10（09-24 k=1 的真實 PERF 行）──────────────
# 這是這支程式最重要的測試：錯位那版會算出 m = 0.000（t_accept 恆為 0.2），
# 而 09-24 那一趟的 m 早在 09-29 就由 docs/MISSHIST_REVIVAL_GATE_2026-09-29.md §9
# 記成 ≈0.10（T_draft 8.41–9.45 ms/輪）。用真實行釘住它 ⇒ 改壞了立刻紅。
PERF_0924_K1 = (
    "CGC-MTP-PERF type=draft-mtp calls_begin=5 calls_draft=160 calls_accept=160 gen_tokens=160 "
    "acc_tokens=160 t_begin_ms=0.0 t_draft_ms=1374.8 t_accept_ms=0.2 acc_rate=1.0000 "
    "gen_tok_per_round=1.000 acc_tok_per_round=1.000 emit_tok_per_round=2.000 ms_per_round=8.592\n"
    "CGC-MTP-PERF type=draft-mtp calls_begin=6 calls_draft=192 calls_accept=192 gen_tokens=192 "
    "acc_tokens=192 t_begin_ms=0.0 t_draft_ms=1647.5 t_accept_ms=0.2 acc_rate=1.0000 "
    "gen_tok_per_round=1.000 acc_tok_per_round=1.000 emit_tok_per_round=2.000 ms_per_round=8.581\n"
)


def _fixture(d, cell_depths="512", tg_rows=1, incomplete=False):
    """造一個最小但欄位齊的產物 ＋ 它的 stderr（不自帶任何外部依賴）。"""
    rows = [{"n_prompt": 2048, "n_gen": 0, "avg_ts": 305.4}]
    for _ in range(tg_rows):
        rows.append({"n_prompt": 0, "n_gen": 128, "avg_ts": 9.9})
    obj = {"rows": rows, "cache": {"hit_rate_pct": 91.6}, "wall_s": 82.5,
           "incomplete": incomplete, "error": None,
           "cell": {"named_cell": "(default)", "depths": cell_depths},
           "attribution": {"verdict": "none"}, "memory": {"launch": {"pages_available_mb": 10978.0}},
           "sys_before": {"pageins": 0}, "sys_after": {"pageins": 1370000}}
    rj = os.path.join(d, "run.json")
    lg = os.path.join(d, "run.stderr.log")
    with open(rj, "w") as fh:
        json.dump([obj], fh)
    with open(lg, "w") as fh:
        fh.write(PERF_0924_K1)
    return rj, lg


def selftest():
    import tempfile
    ok = [0, 0]

    def chk(label, cond):
        ok[1] += 1
        ok[0] += 1 if cond else 0
        print(("  ✅ " if cond else "  ⛔ ") + label)

    print("mtp_round_split --selftest")
    with tempfile.TemporaryDirectory() as d:
        rj, lg = _fixture(d)
        rows = parse_perf(lg)
        chk("parse_perf 取的是 t_draft_ms（不是 t_accept_ms）", rows and rows[-1]["t_draft"] == 1647.5)
        chk("同一行的 t_accept_ms 是常數 0.2 ⇒ 舊欄位就是它", rows and rows[-1]["t_accept"] == 0.2)
        diffs = steady_ms_per_round(rows)
        chk("T_draft 差分 = (1647.5-1374.8)/32 = 8.52 ms/輪（舊版會是 0.00）",
            len(diffs) == 1 and abs(diffs[0][0] - 8.521875) < 1e-6)
        res = judge(rj, lg)
        chk("五閘全過（交付/預設 cell 單深度，閘 (a) 不再要求 ≥2 row）", res["verdict"] == "OK")
        chk("m ≈ 0.099（＝文件已記載的 09-24 值 0.098–0.110）", res["m"] is not None and 0.098 <= res["m"] <= 0.101)
        chk("m ≠ 0（舊版永遠是 0.000）", res["m"] > 0.05)
        chk("k_eff 讀 gen_tok_per_round ＝ 1.000", res["k_eff"] == 1.0)
        chk("E 讀 emit_tok_per_round ＝ 2.000", res["E"] == 2.0)
        rj2, lg2 = _fixture(d, incomplete=True)
        chk("incomplete ⇒ 閘 (a) ⛔ ⇒ 不硬算", judge(rj2, lg2)["verdict"] is None)
        rj3, lg3 = _fixture(d, cell_depths="512,1024", tg_rows=1)
        chk("宣告兩個 depth 卻只有一個 tg row ⇒ 閘 (a) ⛔（舊行為保留）",
            judge(rj3, lg3)["verdict"] is None)
        rj4, lg4 = _fixture(d, tg_rows=2)
        chk("宣告一個 depth 卻有兩個 tg row ⇒ 閘 (a) ✅（多不算少）", judge(rj4, lg4)["verdict"] == "OK")
        chk("expected_tg_rows：單深度 ⇒ 1", expected_tg_rows({"cell": {"depths": "512"}}) == 1)
        chk("expected_tg_rows：沒宣告 ⇒ 2（舊行為）", expected_tg_rows({}) == 2)
    chk("缺產物 ⇒ 判不了，不當過", judge("/nonexistent/run.json")["verdict"] is None)
    print(f"== selftest {ok[0]}/{ok[1]} ==")
    return 0 if ok[0] == ok[1] else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run_json", nargs="?")
    ap.add_argument("stderr_log", nargs="?")
    ap.add_argument("--json", action="store_true", help="印 JSON（給閘門吃）")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not a.run_json:
        ap.error("需要 run_json（或用 --selftest）")
    res = judge(a.run_json, a.stderr_log)
    if a.json:
        d = {k: v for k, v in res.items() if k != "diffs"}
        d["diffs"] = [[v, dc] for v, dc in res["diffs"]]
        print(json.dumps(d, ensure_ascii=False))
    else:
        print_result(res)
    return res["rc"]


if __name__ == "__main__":
    sys.exit(main())
