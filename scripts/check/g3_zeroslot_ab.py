#!/usr/bin/env python3
"""G3（`CGC_ZERO_SLOT`）A/B 的**機械判詞**：見證行有沒有真的翻轉。

卡：`scripts/check/charters/e-s1-g3-zeroslot-2026-09-29.yaml`（L20-2）
為什麼要這支：那張卡的 `how:` 寫的是 `grep 'CGC-G3-ZEROSLOT'` —— 也就是「人眼看」。可是判準是
**二值翻轉**（兩條互斥分支各有一個計數器），正是最不該靠人眼的一種判準：四種輸出長得都像成功。

用法
----
    python3 scripts/check/g3_zeroslot_ab.py --a <A.stderr.log|A.json> --b <B.stderr.log|B.json>
    python3 scripts/check/g3_zeroslot_ab.py --selftest

判準（來自卡上的 arms 與 subgoals）
-----------------------------------
    A 臂 prod-new:CGC_SEG_BATCH=1;CGC_SLOT_TABLE_GPU=1                    ⇒ zero_slot=0, placeholder>0
    B 臂 以上 ＋ CGC_ZERO_SLOT=1                                          ⇒ zero_slot>0, placeholder=0
四種可能結局**必須分開**（只報「有／沒有」會把無效對照當成否證）：
    FLIP        兩臂都走到該走的分支，且方向如預期            ⇒ G3 成立
    NO-OP-B     B 臂仍 0/0（或仍走 placeholder）              ⇒ G3 在這組是 no-op
    EMPTY-A     A 臂 0/0 ⇒ **對照臂根本沒走到那條路徑**（不是否證 G3，是這趟無效；
                09-29 在 prod-new 交付臂上就是這個，因為 slot table map 是空的）
    NO-WITNESS  少了一臂的見證行 ⇒ 無法判
⛔ 兩臂的 tg 一律不引用（單段提交臂輸出是 garbage、且 G3 依設計不 bit-identical）。
⛔ `arm_env_dropped` 那條子目標**由 driver 保證**：`llama_bench_matrix.py` 在 arm 的 env
   沒送達時直接 `SystemExit`（fail-closed）⇒ 「有產物」就已隱含「env 送到」。本檔只覆核一行。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

LAYER_RE = re.compile(
    r"CGC-G3-ZEROSLOT: il=(\d+) ns=(\d+) zero_slot=(-?\d+)(.*)$")
TOTAL_RE = re.compile(
    r"CGC-G3-ZEROSLOT-TOTAL: zero_slot=(\d+) placeholder=(\d+)")
ARMED = "reserved slot exists and is now zeroed"
NOT_ARMED = "NOT ARMED"

VERDICT = {
    "FLIP": "✅ 兩臂走到了預期的分支、方向如預期 ⇒ G3 成立（可翻 L20-2 的判詞）",
    "NO-OP-B": "⛔ B 臂仍沒走 zero-slot 分支 ⇒ G3 在這一組是 no-op",
    "EMPTY-A": "⚠ A 臂 0/0 ⇒ 對照臂沒走到那條路徑，這趟是**無效**（不是否證 G3）",
    "ARMED-A": "⚠ A 臂自己走了 zero-slot 分支（對照臂被武裝）⇒ 對照不成立，A 要重跑",
    "NO-WITNESS": "⚠ 少了一臂的見證行 ⇒ 無法判（先確認兩臂都真的印過 TOTAL）",
}


def resolve_log(path: str) -> str | None:
    """吃 stderr 或 run.json（json 就去找它旁邊的 live stderr）。

    ⚠ 用 `str(json) + ".logs"` **指名**該 run 的目錄，不要用 `*.logs` 撈 —— 同一個目錄底下
    常有別的 run，鬆散的 glob 會撈到隔壁那趟的 stderr（在 selftest 裡就是這樣先炸的）。
    """
    p = Path(path)
    if p.suffix != ".json":
        return str(p) if p.exists() else None
    for d in (Path(str(p) + ".logs"), p.parent):
        for pat in ("live/*.stderr.log", "*.stderr.log"):
            cands = sorted(d.glob(pat))
            if cands:
                return str(cands[0])
    return None


def parse(path: str | None) -> dict:
    out = {"path": path, "layers": [], "totals": [], "armed_lines": 0,
           "not_armed_lines": 0, "exists": bool(path and os.path.exists(path))}
    if not out["exists"]:
        return out
    for line in open(path, errors="ignore"):
        m = LAYER_RE.search(line)
        if m:
            zs_tail = m.group(4)
            rec = {"il": int(m.group(1)), "ns": int(m.group(2)), "zero_slot": int(m.group(3))}
            out["layers"].append(rec)
            if ARMED in zs_tail:
                out["armed_lines"] += 1
            if NOT_ARMED in zs_tail:
                out["not_armed_lines"] += 1
        m = TOTAL_RE.search(line)
        if m:
            out["totals"].append({"zero_slot": int(m.group(1)),
                                  "placeholder": int(m.group(2))})
    # 累計計數器 ⇒ 取最後一行（最大值）
    out["last"] = (out["totals"][-1] if out["totals"] else None)
    return out


def classify(a: dict, b: dict) -> tuple[str, list[str]]:
    """純函式 ⇒ selftest 餵合成 dict。回 (verdict, 說明行)。"""
    notes: list[str] = []
    if a.get("last") is None or b.get("last") is None:
        return "NO-WITNESS", notes
    az, ap = a["last"]["zero_slot"], a["last"]["placeholder"]
    bz, bp = b["last"]["zero_slot"], b["last"]["placeholder"]
    notes.append(f"A TOTAL zero_slot={az} placeholder={ap}"
                 f"（逐層 {len(a['layers'])} 行、armed {a['armed_lines']}／not-armed {a['not_armed_lines']}）")
    notes.append(f"B TOTAL zero_slot={bz} placeholder={bp}"
                 f"（逐層 {len(b['layers'])} 行、armed {b['armed_lines']}／not-armed {b['not_armed_lines']}）")
    # 先驗對照臂的**完整性**：A 自己走了 zero-slot 分支 ⇒ 這趟根本不是對照，先別談 B。
    if az > 0 or a["armed_lines"] > 0:
        return "ARMED-A", notes
    if az == 0 and ap == 0:
        return "EMPTY-A", notes
    if az == 0 and ap > 0:
        if bz > 0 and bp == 0:
            # 逐層不變量：保留的是**最後一槽** ⇒ zero_slot == ns-1
            bad = [r for r in b["layers"] if r["zero_slot"] != r["ns"] - 1]
            if bad:
                notes.append(f"⚠ B 的逐層行有 {len(bad)} 行不符 `zero_slot == ns-1`"
                             f"（例：{bad[:2]}）—— 翻轉成立但保留槽不是最後一槽，值得看一眼")
            else:
                notes.append("★ B 的逐層行全部符合 `zero_slot == ns-1`（保留最後一槽）")
            return "FLIP", notes
        return "NO-OP-B", notes
    return "NO-WITNESS", notes


def report(a: dict, b: dict) -> int:
    v, notes = classify(a, b)
    print("== G3（CGC_ZERO_SLOT）A/B 判詞 ==")
    print(f"A : {a.get('path')}")
    print(f"B : {b.get('path')}")
    for n in notes:
        print(f"   {n}")
    if a.get("layers") and b.get("layers"):
        ids = sorted({r["il"] for r in a["layers"]} | {r["il"] for r in b["layers"]})
        print(f"   觸及的層 il={ids[:10]}{'…' if len(ids) > 10 else ''}（共 {len(ids)} 層）")
    print(f"\nVERDICT: {v} —— {VERDICT[v]}")
    print("   ⛔ 本判詞不含任何 t/s：兩臂的 tg 都不可引用（單段提交臂輸出是 garbage、"
          "且 G3 依設計不 bit-identical）。")
    return 0 if v == "FLIP" else 1


# ── selftest：餵合成的見證文字 ────────────────────────────────────────────────
def _mk(tmp: Path, name: str, text: str) -> dict:
    p = tmp / name
    p.write_text(text, encoding="utf-8")
    return parse(str(p))


def cmd_selftest(tmp: Path) -> int:
    bad = 0

    def chk(name, cond):
        nonlocal bad
        print(f"  {'OK  ' if cond else 'FAIL'} {name}")
        if not cond:
            bad += 1

    A_TXT = ("CGC-G3-ZEROSLOT: il=9 ns=143 zero_slot=-1 (NOT ARMED -> placeholder)\n"
             "CGC-G3-ZEROSLOT-TOTAL: zero_slot=0 placeholder=12345\n"
             "CGC-G3-ZEROSLOT-TOTAL: zero_slot=0 placeholder=3398260\n")
    B_TXT = ("CGC-G3-ZEROSLOT: il=9 ns=143 zero_slot=142 (reserved slot exists and is now zeroed)\n"
             "CGC-G3-ZEROSLOT-TOTAL: zero_slot=1200 placeholder=0\n"
             "CGC-G3-ZEROSLOT-TOTAL: zero_slot=3398260 placeholder=0\n")
    a, b = _mk(tmp, "a.log", A_TXT), _mk(tmp, "b.log", B_TXT)
    chk("翻轉 ⇒ FLIP", classify(a, b)[0] == "FLIP")
    chk("取末行（累計最大）不是首行", a["last"]["placeholder"] == 3398260)
    chk("逐層 armed/not-armed 分得開",
        a["not_armed_lines"] == 1 and b["armed_lines"] == 1 and a["armed_lines"] == 0)

    # A 沒走到那條路徑（09-29 prod-new 的實況）⇒ 無效，不是否證
    a0 = _mk(tmp, "a0.log", "CGC-G3-ZEROSLOT-TOTAL: zero_slot=0 placeholder=0\n")
    chk("A 臂 0/0 ⇒ EMPTY-A（無效對照，不算否證）", classify(a0, b)[0] == "EMPTY-A")

    # B 仍是 no-op
    b0 = _mk(tmp, "b0.log", "CGC-G3-ZEROSLOT-TOTAL: zero_slot=0 placeholder=0\n")
    chk("B 臂 0/0 ⇒ NO-OP-B", classify(a, b0)[0] == "NO-OP-B")
    bph = _mk(tmp, "bph.log", "CGC-G3-ZEROSLOT-TOTAL: zero_slot=0 placeholder=999\n")
    chk("B 仍走 placeholder ⇒ NO-OP-B", classify(a, bph)[0] == "NO-OP-B")

    # 少一臂
    empty = {"path": None, "layers": [], "totals": [], "last": None,
             "armed_lines": 0, "not_armed_lines": 0, "exists": False}
    chk("缺一臂 ⇒ NO-WITNESS", classify(empty, b)[0] == "NO-WITNESS")

    # 保留槽不是最後一槽 ⇒ 仍判 FLIP 但留警告（不把它當失敗：翻轉是二值的）
    bbad = _mk(tmp, "bbad.log",
               "CGC-G3-ZEROSLOT: il=9 ns=143 zero_slot=10 (reserved slot exists and is now zeroed)\n"
               "CGC-G3-ZEROSLOT-TOTAL: zero_slot=500 placeholder=0\n")
    v, notes = classify(a, bbad)
    chk("逐層不符 ns-1 ⇒ 仍 FLIP，但有警告", v == "FLIP" and any("ns-1" in n for n in notes))

    # A 被武裝了 ⇒ 對照不成立
    aarm = _mk(tmp, "aarm.log",
               "CGC-G3-ZEROSLOT: il=9 ns=143 zero_slot=142 (reserved slot exists and is now zeroed)\n"
               "CGC-G3-ZEROSLOT-TOTAL: zero_slot=10 placeholder=200\n")
    chk("A 臂出現 armed 字樣 ⇒ 對照不成立（ARMED-A）", classify(aarm, b)[0] == "ARMED-A")

    # resolve_log：吃 json 也要能找到 live stderr
    d = tmp / "run.json.logs" / "live"
    d.mkdir(parents=True, exist_ok=True)
    (d / "x.stderr.log").write_text(B_TXT, encoding="utf-8")
    (tmp / "run.json").write_text("[]", encoding="utf-8")
    chk("resolve_log 從 json 找到 live stderr", resolve_log(str(tmp / "run.json")) == str(d / "x.stderr.log"))
    chk("resolve_log 不存在 ⇒ None", resolve_log(str(tmp / "nope.json")) is None)

    print("\nSELFTEST " + ("OK" if bad == 0 else f"FAILED（{bad}）"))
    return 1 if bad else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--a")
    ap.add_argument("--b")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--json", help="把結果也寫成 json（給卡／看板引用）")
    a = ap.parse_args()
    if a.selftest:
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            return cmd_selftest(Path(td))
    if not (a.a and a.b):
        ap.error("需要 --a 與 --b（或 --selftest）")
    ra, rb = parse(resolve_log(a.a)), parse(resolve_log(a.b))
    rc = report(ra, rb)
    if a.json:
        v, _ = classify(ra, rb)
        Path(a.json).write_text(json.dumps(
            {"verdict": v, "a": {k: ra[k] for k in ("path", "last", "armed_lines", "not_armed_lines")},
             "b": {k: rb[k] for k in ("path", "last", "armed_lines", "not_armed_lines")}},
            ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return rc


if __name__ == "__main__":
    sys.exit(main())
