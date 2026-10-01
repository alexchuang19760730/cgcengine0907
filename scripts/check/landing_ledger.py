#!/usr/bin/env python3
"""landing_ledger.py — 「每一格都落地了嗎」的機器帳（operator 2026-10-01）。

為什麼要有這一支：看板（`decode_board_build.py`）已經驗**宣告與現判是否一致**（D6／D7／D12／D13／
D14），但它不回答**下一句**：「那我現在能按哪個鍵？」。而「備好了」這種狀態最容易爛 ——
補丁的 base 會被別人改掉、腳本會被改名、判詞工具會被搬走，而卡片上的敘述仍是舊的。
這一支就答那一句，並且把「落地」變成可重跑的檢查：

  已結案（settled）
      `state` 以「結案」開頭 ∧ 端點至少一項**真的在**（`evidence.artifact`／`source`／
      `quote.artifact`／`counter_quote` 的名）∧ 引用的宣告與 `quote_gate` 當場判決一致
      （後者已由看板 D7 跑，這裡只照抄它的結論，不重寫規則）。
  卡住（blocked）
      `block{kind,owner,next}` 齊全 ∧ 卡點斷言可掃（D14）∧ **它聲稱的入口在** ——
      把 `next`／`runnable`／`action`／`now` 裡提到的路徑逐一對檔（charter／工具／補丁／文件），
      補丁另外跑 `git apply --check -p1` 與 `--3way`（「備好了、可即刻套用」這句話會被時間弄髒）。
  duplicate
      指名的 `precondition.artifact` 在。

輸出：逐格一行（LANDED／GAP ＋ 下一鍵）；`--md` 另寫 `docs/L20_L25_LANDING_<日期>.md`。
rc：0 ＝ 全部落地；1 ＝ 有缺口（印出缺口的那幾格與原因）；2 ＝ 看板載不進。

    python3 scripts/check/landing_ledger.py            # 印帳（rc=1 ＝ 有缺口）
    python3 scripts/check/landing_ledger.py --md       # 另寫 docs/L20_L25_LANDING_<日期>.md
    python3 scripts/check/landing_ledger.py --selftest
"""

import argparse
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
BOARD = os.path.join(HERE, "decode_board_2026-09-29.yaml")
DATE = time.strftime("%Y%m%d")

# 哪一些字串看起來是「入口」（要比對實檔）。刻意**不含** `src/`：原始碼路徑是卡點的成因，
# 不是要按的鍵（成因由 D14 的斷言守）。
REF_RE = re.compile(r"(?:scripts/check/[\w./-]+\.(?:py|yaml|sh|json)|Backup/[\w./{},-]+|docs/[\w./-]+\.(?:md|html))")

# 「入口」＝**現在就必須在**的東西；`Backup/**` 不在此列：那多半是**跑完才生**的產物
# （journal／log／json），把它們列成「必須在」會把「還沒跑」誤報成「沒落地」。
ENTRY_RE = re.compile(r"(?:scripts/check/[\w./-]+\.(?:py|yaml|sh)|[\w./-]+\.patch|docs/[\w./-]+\.(?:md|html))")


def _norm(p: str) -> str:
    return p.rstrip("）。，、；：")


def expand_paths(s: str) -> list:
    """把一段散文裡的路徑拆出來：支援 `{A,B}` 展開（卡片很常這樣寫一組產物）。"""
    out = []
    for m in REF_RE.findall(s or ""):
        p = _norm(m)
        mm = re.search(r"\{([^{}]*)\}", p)      # 大括号可以出現在中間：`…/{A,B}.json`
        if mm:
            head, tail = p[:mm.start()], p[mm.end():]
            for part in mm.group(1).split(","):
                out.append(head + part.strip() + tail)
        else:
            out.append(p)
    return out


def refs_of(card: dict) -> list:
    """這一格**聲稱**存在的**入口**（去重、保序）—— 只收「現在就該在」的那幾種。"""
    out, seen = [], set()
    keys = ("runnable", "action", "why", "now", "next", "accept")
    blk = (card.get("precondition") or {}).get("block") or {}
    for blob in [str(card.get(k) or "") for k in keys] + [str(blk.get("next") or "")]:
        for p in ENTRY_RE.findall(blob):
            p = _norm(p)
            if p not in seen:
                seen.add(p)
                out.append(p)
    return out


def patch_state(root: str, rel: str) -> tuple:
    """(ok, why)：這份補丁現在還套得上嗎？——plain 與 --3way 都問，把「備好了」的時效量出來。"""
    try:
        p = subprocess.run(["git", "apply", "--check", "-p1", rel], cwd=root,
                           capture_output=True, text=True, timeout=60)
        plain = (p.returncode == 0, (p.stderr or "").strip()[:160])
        p3 = subprocess.run(["git", "apply", "--check", "--3way", "-p1", rel], cwd=root,
                            capture_output=True, text=True, timeout=60)
        w = (p3.returncode == 0, (p3.stderr or "").strip()[:160])
    except Exception as exc:  # noqa: BLE001
        return False, "git apply 跑不動：%s" % exc
    if plain[0]:
        return True, "plain 可直上"
    if w[0]:
        return True, "plain 不行、--3way 可以（base 漂了，套用時要記在產物裡）"
    return False, "plain／--3way 都不行：%s" % (plain[1] or w[1] or "衝突")


def endpoints_of(card: dict) -> list:
    """端點＝**展開後**的具體路徑（卡片裡常是「A.json ＋ B.json ＋ 裁定：docs/…」這種散文包）。"""
    ev = card.get("evidence") or {}
    q = ev.get("quote") or {}
    cq = ev.get("counter_quote") or {}
    raw = [ev.get("artifact"), ev.get("source")] + [q.get("artifact"),
           cq.get("artifact") if isinstance(cq, dict) else None]
    out, seen = [], set()
    for x in raw:
        if not isinstance(x, str) or not x or x.startswith("<"):
            continue
        for p in expand_paths(x)[:3]:
            if p not in seen:
                seen.add(p)
                out.append(p)
    return out[:6]


def card_report(root: str, card: dict, board: dict = None) -> dict:
    """一格一結論：LANDED／GAP ＋ 缺什麼 ＋ 下一鍵。"""
    cid = card.get("id")
    pre = card.get("precondition") or {}
    blk = pre.get("block") or {}
    rerun = pre.get("rerun")
    settled = bool(card.get("settled"))
    gaps = []
    entry = []
    endpoints = endpoints_of(card)

    if settled:
        if not str(card.get("state") or "").startswith("結案"):
            gaps.append("已 settled 但 state 不以「結案」開頭（D11 的一致性）")
        present = [p for p in endpoints if os.path.exists(os.path.join(root, p))]
        if not endpoints:
            gaps.append("端點讀不出路徑（UNSCANNED）—— 結案要有指得到的端點")
        elif not present:
            gaps.append("端點一個都不在：%s" % "、".join(endpoints))
        entry = present[:2] or endpoints[:2]
        verdict = "LANDED" if not gaps else "GAP"
        return dict(id=cid, settled=True, verdict=verdict, gaps=gaps, entry=entry,
                    endpoints=endpoints, rerun=rerun, kind=pre.get("kind"),
                    block=None)

    # ── 未結案：只能這三種型 ───────────────────────────────────────────────
    if rerun not in ("blocked", "duplicate", "needed"):
        gaps.append("未結案卻沒有合法的 precondition.rerun（%r）" % rerun)
    if rerun == "blocked":
        for k in ("kind", "owner", "next"):
            if not blk.get(k):
                gaps.append("blocked 缺 block.%s（D13）" % k)
        if not (blk.get("assert") or {}).get("kind"):
            gaps.append("blocked 缺 block.assert（D14：卡點要可掃）")
    if rerun == "duplicate":
        art = pre.get("artifact")
        if not art or not os.path.exists(os.path.join(root, art)):
            gaps.append("duplicate 指名的 artifact 不在：%r" % art)
    if rerun == "needed":
        if not pre.get("spec"):
            gaps.append("needed 卻沒有 spec 掃描（D12）")

    # 入口對檔（這一支的價值就在這裡）：聲稱的 charter／工具／補丁／文件都要在
    # ⚠ 刻意**不**要求 `Backup/**`（那多半是跑完才生的產物 ⇒ 要求它會把「還沒跑」誤報成「沒落地」）。
    missing, patches = [], []
    for rel in refs_of(card):
        if not os.path.exists(os.path.join(root, rel)):
            missing.append(rel)
        elif rel.endswith(".patch"):
            patches.append(rel)
    if missing:
        gaps.append("聲稱的入口不在樹上：%s" % "、".join(missing[:4]))
    for rel in patches:
        ok, why = patch_state(root, rel)
        if not ok:
            gaps.append("補丁套不上（%s）：%s" % (rel, why))
        else:
            entry.append("%s（%s）" % (rel, why))

    entry = entry + [p for p in refs_of(card) if os.path.exists(os.path.join(root, p))
                     and p not in entry][:3]
    return dict(id=cid, settled=False, verdict="LANDED" if not gaps else "GAP", gaps=gaps,
                entry=entry[:4], endpoints=endpoints, rerun=rerun, kind=pre.get("kind"),
                block=("%s／%s" % (blk.get("kind"), blk.get("owner")) if blk else None))


def ledger(root: str = ROOT, board_path: str = BOARD) -> dict:
    import yaml
    with open(board_path, encoding="utf-8") as fh:
        board = yaml.safe_load(fh)
    rows = []
    for layer in board.get("layers") or []:
        for t in layer.get("targets") or []:
            rows.append(card_report(root, t, board))
    return dict(root=root, at=time.strftime("%F %T %z"), rows=rows,
                n=len(rows), n_landed=sum(1 for r in rows if r["verdict"] == "LANDED"),
                n_settled=sum(1 for r in rows if r["settled"]))


def report(led: dict) -> None:
    print("L20／L25 落地帳 %s（%d 格）" % (led["at"], led["n"]))
    for r in led["rows"]:
        tag = "✅" if r["verdict"] == "LANDED" else "✗"
        head = ("結案" if r["settled"] else
                "%s／%s" % (r.get("rerun"), r.get("block") or r.get("kind") or "-"))
        print("  %s %-7s %-22s %s" % (tag, r["id"], head, (r["entry"][0] if r["entry"] else "-")))
        for g in r["gaps"]:
            print("      ⚠ %s" % g)
    print("  落地 %d／%d（其中結案 %d）" % (led["n_landed"], led["n"], led["n_settled"]))


def write_md(led: dict, root: str = ROOT) -> str:
    rel = "docs/L20_L25_LANDING_%s.md" % DATE
    lines = ["# L20／L25 落地帳（%s）" % led["at"],
             "",
             "由 `scripts/check/landing_ledger.py --md` 產生（**機器產生，不要手改**）。",
             "「落地」的定義與缺口判準寫在那一支的檔頭：結案＝端點在；未結案＝owner＋下一動＋可掃斷言＋"
             "**聲稱的入口都在樹上**（補丁另外當場 `git apply --check`）。",
             "",
             "| 格 | 狀態 | 卡在哪／端點 | 下一個入口 | 缺口 |",
             "|---|---|---|---|---|"]
    for r in led["rows"]:
        state = ("結案" if r["settled"] else "%s／%s" % (r.get("rerun"), r.get("block") or "-"))
        entry = (r["entry"][0] if r["entry"] else "-")
        lines.append("| `%s` | %s | %s | `%s` | %s |" % (
            r["id"], state, r["verdict"], entry, "；".join(r["gaps"]) or "—"))
    lines += ["", "**落地 %d／%d（結案 %d）**" % (led["n_landed"], led["n"], led["n_settled"]), ""]
    p = os.path.join(root, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    return rel


# ── selftest ────────────────────────────────────────────────────────────────

def selftest() -> int:
    import tempfile
    good = [0, 0]

    def case(name, cond, detail=""):
        good[1] += 1
        good[0] += 1 if cond else 0
        print("  %-58s -> %s%s" % (name, "PASS" if cond else "FAIL",
                                   "" if cond else "  " + str(detail)))

    with tempfile.TemporaryDirectory() as td:
        os.makedirs(os.path.join(td, "scripts/check"), exist_ok=True)
        with open(os.path.join(td, "scripts/check/tool.py"), "w", encoding="utf-8") as fh:
            fh.write("# toy\n")
        os.makedirs(os.path.join(td, "Backup"), exist_ok=True)

        # ① 卡住且入口都在 ⇒ LANDED
        #    ⚠ fixture 用 dict 字面值而不是 dict(...)：`assert` 是 Python 保留字，不能當關鍵字引數。
        asrt = {"kind": "none", "why": "fixture"}
        blk1 = {"kind": "code", "owner": "engine", "next": "跑 scripts/check/tool.py 就好",
                "assert": asrt}
        t1 = {"id": "T-1", "settled": False, "state": "未結案",
              "precondition": {"rerun": "blocked", "kind": "none", "block": blk1}}
        r1 = card_report(td, t1)
        case("blocked：owner＋next＋assert 齊、入口在 ⇒ LANDED", r1["verdict"] == "LANDED", r1)

        # ② 入口不在 ⇒ GAP（這一支的主要價值）
        blk2 = dict(blk1, next="跑 scripts/check/nope.py 就好")
        t2 = {"id": "T-2", "settled": False, "state": "未結案",
              "precondition": {"rerun": "blocked", "kind": "none", "block": blk2}}
        r2 = card_report(td, t2)
        case("blocked：聲稱的入口不在樹上 ⇒ GAP", r2["verdict"] == "GAP"
             and any("入口不在" in g for g in r2["gaps"]), r2["gaps"])

        # ③ 缺 assert ⇒ GAP（D14）
        t3 = {"id": "T-3", "settled": False, "state": "未結案",
              "precondition": {"rerun": "blocked", "kind": "none",
                               "block": {"kind": "code", "owner": "engine", "next": "x"}}}
        case("blocked：缺 block.assert ⇒ GAP", card_report(td, t3)["verdict"] == "GAP")

        # ④ 結案但端點不在 ⇒ GAP
        t4 = {"id": "T-4", "settled": True, "state": "結案",
              "evidence": {"artifact": "Backup/gone.json", "value": "1"}}
        r4 = card_report(td, t4)
        case("結案：端點不在 ⇒ GAP", r4["verdict"] == "GAP" and r4["gaps"], r4["gaps"])
        with open(os.path.join(td, "Backup", "ok.json"), "w", encoding="utf-8") as fh:
            fh.write("{}\n")
        t5 = {"id": "T-5", "settled": True, "state": "結案",
              "evidence": {"artifact": "Backup/ok.json", "value": "1"}}
        case("結案：端點在 ⇒ LANDED", card_report(td, t5)["verdict"] == "LANDED")
        t5b = {"id": "T-5b", "settled": True, "state": "結案",
               "evidence": {"artifact": "散文包：Backup/{ok,gone}.json ＋ 裁定：docs/OPERATOR.md", "value": "1"}}
        case("結案：散文包＋大括号裡的 {a,b} 會展開 ⇒ 抓到 ok.json ⇒ LANDED",
             card_report(td, t5b)["verdict"] == "LANDED", card_report(td, t5b))

        # ⑤ 補丁：壞掉的補丁檔 ⇒ GAP（而且不能拋）
        os.makedirs(os.path.join(td, "Backup"), exist_ok=True)
        with open(os.path.join(td, "Backup", "x.patch"), "w", encoding="utf-8") as fh:
            fh.write("--- a/nope\n+++ b/nope\n@@ -1 +1 @@\n-a\n+b\n")
        t6 = {"id": "T-6", "settled": False, "state": "未結案",
              "precondition": {"rerun": "blocked", "kind": "none",
                               "block": {"kind": "code", "owner": "engine", "next": "套 Backup/x.patch",
                                         "assert": {"kind": "none", "why": "f"}}}}
        r6 = card_report(td, t6)
        case("補丁套不上 ⇒ GAP（不會拋）", r6["verdict"] == "GAP"
             and any("補丁套不上" in g for g in r6["gaps"]), r6["gaps"])

        # ⑥ 引用抽取：只抓看起來是入口的東西
        case("refs_of：只收「現在就該在」的入口（scripts／charter／patch／docs），不收 Backup 產物與 src/",
             "src/llama.cpp/src/llama-context.cpp" not in refs_of(
                 dict(runnable="見 src/llama.cpp/src/llama-context.cpp 與 scripts/check/tool.py")))

    print("== landing_ledger selftest %d/%d ==" % (good[0], good[1]))
    return 0 if good[0] == good[1] else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="L20／L25 落地帳（每一格：端點／入口／缺口）")
    ap.add_argument("--md", action="store_true", help="另寫 docs/L20_L25_LANDING_<日期>.md")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--root", default=ROOT)
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)
    if args.selftest:
        return selftest()
    led = ledger(args.root)
    report(led)
    if args.md:
        print("  文件：%s" % write_md(led, args.root))
    return 0 if led["n_landed"] == led["n"] else 1


if __name__ == "__main__":
    sys.exit(main())
