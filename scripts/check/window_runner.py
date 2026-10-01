#!/usr/bin/env python3
"""window_runner.py — 「窗口腳本」的**共用核心**：步驟以資料宣告，流程只有這一份。

為什麼要拆出來（operator 2026-09-30）：ρ 的 `rho_window.py` 把 S1 套用→S2 建置→S3 oracle→
S4 交付臂→S5 四條命令→S6 判詞 串成一條龍，而那個形狀**每個「卡在窗口」的子目標都一樣**：
一份補丁要進樹、一次建置、一趟收貨、一組權威 row、一個判詞。差別只在「跑什麼」與「收什麼」。
所以把**流程**放這裡（單一定義），**宣告**留在各自的窗口腳本裡。要掛一個新的子目標，
不必再寫一次 skip／fail-fast／可接續，只要寫一張 steps 表：

    import window_runner as WR

    def done_build(root, ctx):
        ...                                   # 掃產物：已存在 ⇒ (True, why) ⇒ skip，不重跑
    def receipt_build(root, ctx):
        ...                                   # 跑後驗收：由**產物**判，不由 rc 判

    STEPS = [
      dict(id="S1", name="套用補丁", commands=[[...]],
           done=lambda root, ctx: ..., receipt=lambda root, ctx: ...,
           artifacts=["Backup/.../x.patch"],
           next_on_fail=["python3 scripts/check/<tool>.py --check   # 哪個座標沒翻"]),
      dict(id="S2", name="建置", commands=[[...]], done=done_build, receipt=receipt_build,
           next_on_fail=[...]),
      ...
    ]

    WR.main(STEPS, preflight=my_preflight, journal="Backup/.../window.jsonl",
            log_dir="Backup/.../window_logs", title="...", argv=sys.argv[1:])

規矩（與本 repo 既有工具對齊；每一條都有 selftest）：
  * **預設不執行**：`--status` 只印計畫與現況（rc=2＝還有未完成）；要動手必須 `--go`。
  * **跑前先掃產物**：`done()` 說「已在」就 skip（`runnable_gate` 的 `needed／duplicate` 同規矩）；
    一步裡有好幾條命令時，每一條可以有自己的 `skip_if`（逐趟 skip，不重跑已經有的答案）。
  * **fail-fast**：`receipt()` 不過或 rc≠0 就停，並印該步的 `next_on_fail`（「下一個該看的東西」）。
  * **可接續**：每一動 append 進 journal；中斷後重跑從沒完成的那一步接下去。
  * **重跑不吃掉證據**：不通過的步再跑一次之前，它既有的 `artifacts` 先搬進
    `<log_dir>/archive/<步驟>.<時戳>/`（失敗那一趟的產物是判詞的依據，不能被覆蓋）。
  * **不偷偷發車**：`preflight()` 不過就拒跑（fail-closed）；它只擋人，不代替人決定。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

STEP_KEYS = ("id", "name", "commands", "inprocess", "done", "receipt", "next_on_fail",
             "artifacts", "log")


# ── 執行器 ──────────────────────────────────────────────────────────────────

def sh(cmd: list, cwd: str = None, log: str = None) -> tuple:
    """跑一條命令，stdout/stderr 合流即時印出，同時存進 log。回 (rc, 全文)。

    為什麼不 `capture_output=True`：那條路把子行程的輸出收在記憶體裡，中斷就什麼都不剩
    （`llama_bench_matrix` 的 docstring 記著那個坑）。這裡邊讀邊印邊落地。
    """
    with subprocess.Popen(cmd, cwd=cwd or ROOT, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, text=True, bufsize=1) as p:
        buf = []
        for line in p.stdout or []:
            buf.append(line)
            sys.stdout.write(line)
            sys.stdout.flush()
        rc = p.wait()
    txt = "".join(buf)
    if log:
        import os as _os
        _os.makedirs(_os.path.dirname(_os.path.abspath(log)) or ".", exist_ok=True)
        with open(log, "w", encoding="utf-8") as fh:
            fh.write(txt)
    return rc, txt


# ── 步驟契約 ────────────────────────────────────────────────────────────────

def _check_steps(steps: list) -> None:
    """宣告本身的體檢：id 不重複、`done` 必填（沒有它就沒有 skip 可言）。"""
    seen = set()
    for s in steps:
        for k in ("id", "name", "done"):
            if k not in s:
                raise SystemExit("步 %r 缺 %r（見 window_runner 的步驟契約）" % (s.get("id"), k))
        if s["id"] in seen:
            raise SystemExit("步 id 重複：%s" % s["id"])
        seen.add(s["id"])
        if not (s.get("commands") or s.get("inprocess")):
            raise SystemExit("步 %s 既沒有 commands 也沒有 inprocess（那一步不會做任何事）" % s["id"])
        for e in _cmd_entries(s):
            if not e["cmd"]:
                raise SystemExit("步 %s 有一條空命令（`commands` 裡的空條目）" % s["id"])
        if len(s.get("next_on_fail") or []) == 0:
            raise SystemExit("步 %s 沒有 next_on_fail —— 失敗時最貴的就是「不知道下一步看哪裡」"
                             % s["id"])


def _done_of(step: dict, root: str, ctx: dict) -> tuple:
    """跑前的「已在」掃描；沒有 receipt 的步就用它當驗收（單一實作，不會漂）。"""
    fn = step.get("done")
    return fn(root, ctx)


def _receipt_of(step: dict, root: str, ctx: dict) -> tuple:
    return step.get("receipt", step["done"])(root, ctx)


def _cmd_entries(step: dict) -> list:
    """一步裡的每一條命令。條目可以是 `["cmd", ...]`，也可以是
    `dict(cmd=[…], skip_if=callable(root, ctx)->(bool, why), log="Backup/…")` ——
    第二種讓「一個步驟裡有好幾趟」也能**逐趟** skip（例：四條 bench，某幾份產物已在）。
    """
    out = []
    for c in (step.get("commands") or []):
        if isinstance(c, dict):
            out.append(dict(cmd=list(c["cmd"]), skip_if=c.get("skip_if"), log=c.get("log")))
        else:
            out.append(dict(cmd=list(c), skip_if=None, log=None))
    return out


def plan(steps: list, root: str = ROOT, ctx: dict = None) -> list:
    """每一步現在的狀態：`done`（已在）或 `pending`，附 why／commands／artifacts。"""
    ctx = ctx or {}
    out = []
    for s in steps:
        ok, why = _done_of(s, root, ctx)
        out.append(dict(id=s["id"], name=s["name"], state="done" if ok else "pending",
                        why=why or "", commands=[e["cmd"] for e in _cmd_entries(s)],
                        artifacts=list(s.get("artifacts") or [])))
    return out


# ── 印／寫 ─────────────────────────────────────────────────────────────────

def print_plan(states: list, title: str = "", extra: list = None, notes: list = None) -> None:
    if title:
        print(title)
    for line in (extra or []):
        print("  " + line)
    for line in (notes or []):
        print("  " + line)
    for s in states:
        print("  %s %s %-30s %s" % ("✅" if s["state"] == "done" else "・",
                                    s["id"], s["name"], s["why"][:96]))
        if s["state"] == "pending":
            for c in s["commands"]:
                print("       $ %s" % " ".join(c))


def hints(step: dict) -> str:
    lines = ["", "  下一個該看的東西（%s）：" % step["id"]]
    for h in step.get("next_on_fail") or []:
        lines.append("    · %s" % h)
    return "\n".join(lines)


def append_journal(path: str, rec: dict) -> None:
    if not path:
        return
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    rec = dict(rec)
    rec["at"] = time.strftime("%F %T")
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


# ── 主流程（唯一一份）──────────────────────────────────────────────────────

def run(steps, root: str = ROOT, go: bool = False, only=None, ctx: dict = None,
        runner=sh, journal: str = None, log_dir: str = "window_logs",
        title: str = "", preflight=None, argv=None) -> int:
    """跑一個窗口。`preflight(ctx) -> dict(ok, reasons, notes)` 由宣告提供（可 None）。

    `steps` 可以是清單，也可以是 `steps(ctx)` —— 後者讓步驟本身可以是參數化的
    （例：ρ 的 tag 帶著日期、`--reuse-ctl` 會把 S3 從「發車」換成「認領那顆錨」）。
    """
    ctx = dict(ctx or {})
    ctx.setdefault("root", root)
    steps = steps(ctx) if callable(steps) else steps
    _check_steps(steps)
    only = set(only) if only else None

    pf = preflight(root, ctx) if preflight else dict(ok=True, reasons=[], notes=[])
    states = plan(steps, root=root, ctx=ctx)
    extra = list(pf.get("header") or [])
    print_plan(states, title=title, extra=extra, notes=pf.get("notes"))

    pending = [s["id"] for s in states if s["state"] == "pending"]
    if not go:
        print("\n（--status：不執行任何東西。要真的跑：--go）")
        for r in pf.get("reasons") or []:
            print("  ✗ %s" % r)
        if pf.get("reasons"):
            print(hints(steps[0]))
        return 0 if not pending else 2

    if not pf.get("ok", True):
        print("\n前置未過 ⇒ 不發車（fail-closed）：")
        for r in pf.get("reasons") or []:
            print("  ✗ %s" % r)
        print(hints(steps[0]))
        return 1

    by_id = {s["id"]: s for s in steps}
    for st in states:
        sid = st["id"]
        step = by_id[sid]
        if only and sid not in only:
            continue
        if st["state"] == "done":
            print("\n[%s] skip（已在）：%s" % (sid, st["why"][:110]))
            append_journal(journal, dict(step=sid, status="skip", why=st["why"]))
            continue

        print("\n[%s] %s" % (sid, step["name"]))
        rc, ok, why, run_log = _run_step(step, root, ctx, runner, log_dir)
        append_journal(journal, dict(step=sid, status="ok" if ok else "fail", rc=rc, why=why,
                                     log=run_log))
        if rc != 0 or not ok:
            print("\n[%s] FAIL rc=%d -- %s" % (sid, rc, why))
            print(hints(step))
            return 1
        print("[%s] OK -- %s" % (sid, why))

    states = plan(steps, root=root, ctx=ctx)
    left = [s["id"] for s in states if s["state"] == "pending"]
    print("\n窗口結束：%s" % ("全部完成" if not left else "還沒完成：%s" % "、".join(left)))
    return 0 if not left else 2


def archive_artifacts(step: dict, root: str, log_dir: str) -> list:
    """跑之前：這一步既有的產物先搬進 `<log_dir>/archive/<步驟>.<時戳>/`。回搬走的路徑。

    為什麼要有這一條：`skip_if` 說「不通過 ⇒ 重跑」時，重跑會**原地覆蓋**上一趟的產物。
    而失敗那一趟的產物（例如 `attribution=swap` 與它的 swap 曲線）正是判詞與否證的依據 ——
    覆蓋它等於把「試過、沒過、為什麼沒過」從證據裡刪掉。搬走而不是刪掉，讓重跑與留痕並存。
    """
    present = [a for a in (step.get("artifacts") or []) if os.path.exists(os.path.join(root, a))]
    if not present or not log_dir:
        return []
    dest = os.path.join(root, log_dir, "archive",
                        "%s.%s" % (step["id"], time.strftime("%Y%m%d-%H%M%S")))
    moved = []
    for rel in present:
        src = os.path.join(root, rel)
        dst = os.path.join(dest, os.path.basename(rel))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.move(src, dst)
        moved.append(dst)
    return moved


def _run_step(step: dict, root: str, ctx: dict, runner, log_dir: str) -> tuple:
    """一步：跑它的命令（或 in-process），然後用 receipt 判它過不過。回 (rc, ok, why, log)。"""
    run_log = os.path.join(log_dir or "", "%s.log" % step["id"]) if log_dir else None
    if run_log:
        run_log = os.path.join(root, run_log)
        # 這條路徑是核心自己指派的 ⇒ 目錄也要核心負責建（runner 只負責寫）。
        os.makedirs(os.path.dirname(run_log), exist_ok=True)
    txt, rc = "", 0
    if step.get("inprocess"):
        rc, txt, ok, why = step["inprocess"](root, ctx)
        if run_log:
            os.makedirs(os.path.dirname(run_log), exist_ok=True)
            with open(run_log, "w", encoding="utf-8") as fh:
                fh.write(txt or "")
        return rc, ok, why, run_log
    todo = []
    for e in _cmd_entries(step):
        if e["skip_if"]:
            skip, why = e["skip_if"](root, ctx)
            if skip:
                print("  skip（已在）：%s" % why)
                continue
        todo.append(e)
    if todo:
        moved = archive_artifacts(step, root, log_dir)
        if moved:
            print("  證據留檔（重跑前搬走 %d 份）：%s" % (len(moved), moved[0]))
    for e in todo:
        print("  $ %s" % " ".join(e["cmd"]))
        log = e["log"] and os.path.join(root, e["log"]) or run_log
        if e["log"]:
            os.makedirs(os.path.dirname(log), exist_ok=True)
        rc, txt = runner(e["cmd"], cwd=root, log=log)
        if rc != 0:
            return rc, False, "命令 rc=%d（%s）" % (rc, " ".join(e["cmd"][:6]) + " …"), log
    ok, why = _receipt_of(step, root, ctx)
    return rc, ok, why, run_log


# ── CLI（宣告只要傳 argv）───────────────────────────────────────────────────

def main(steps, preflight=None, journal: str = None, log_dir: str = "window_logs",
         title: str = "", ctx_factory=None, argv=None, root: str = ROOT) -> int:
    """共用的 CLI：`--status`／`--go`／`--only`／`--list-steps`／`--root`（宣告自己的旗標
    由 `ctx_factory(args, rest)` 解析；`rest` 是這個 CLI 不認得的其餘 argv）。"""
    ap = argparse.ArgumentParser(description=title or "窗口腳本（步驟以資料宣告）")
    ap.add_argument("--go", action="store_true", help="真的執行（預設只印計畫）")
    # `--status` 是**預設**行為，但它明列出來：文件、log 與別人的習慣都寫 `--status`，而先前它落進
    # `rest` 後沒人認得 —— 宣告方若忘了拒收就會被靜默忽略（fail-open）。明列＝說得到做得到。
    ap.add_argument("--status", action="store_true", help="只印計畫與現況（＝預設；與 --go 互斥）")
    ap.add_argument("--only", default=None, help="只做這幾步（逗號分隔）")
    ap.add_argument("--list-steps", action="store_true", help="只列步驟與它的收貨條件來源")
    ap.add_argument("--root", default=root)
    args, rest = ap.parse_known_args(argv)
    if args.status and args.go:
        raise SystemExit("--status 與 --go 不能同時：--status 是不執行。要真的跑就拿掉 --status。")
    ctx = dict(ctx_factory(args, rest)) if ctx_factory else {}
    ctx["argv_rest"] = rest
    ctx.setdefault("root", os.path.abspath(args.root))
    steps = steps(ctx) if callable(steps) else steps
    if args.list_steps:
        for s in steps:
            print("%-4s %-30s commands=%d inprocess=%s receipt=%s" % (
                s["id"], s["name"], len(_cmd_entries(s)), bool(s.get("inprocess")),
                "自訂" if "receipt" in s else "＝done()"))
        return 0
    ids = [s["id"] for s in steps]
    only = [x.strip() for x in (args.only or "").split(",") if x.strip()]
    bad = [x for x in only if x not in ids]
    if bad:
        raise SystemExit("--only 只認 %s（收到 %s）" % ("／".join(ids), "、".join(bad)))
    return run(steps, root=os.path.abspath(args.root), go=args.go, only=only, ctx=ctx,
               journal=journal, log_dir=log_dir, title=title, preflight=preflight)


# ── selftest ────────────────────────────────────────────────────────────────

def selftest() -> int:
    """核心自己的體檢：**用一個玩具宣告走完整條路**（這就是「別的子目標也能掛上」的證明）。"""
    import contextlib
    import glob
    import io
    import tempfile

    good = [0, 0]

    def case(name, cond, detail=""):
        good[1] += 1
        good[0] += 1 if cond else 0
        print("  %-58s -> %s%s" % (name, "PASS" if cond else "FAIL",
                                   "" if cond else "  " + str(detail)))

    def quiet(fn, *a, **kw):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = fn(*a, **kw)
        return rc, buf.getvalue()

    with tempfile.TemporaryDirectory() as td:
        # 一個「別的子目標」的玩具宣告：兩個檔案步驟 ＋ 一個 in-process 判詞步。
        def mk_done(root, ctx, sid):
            p = os.path.join(root, "%s.out" % sid)
            return (os.path.exists(p), ("%s 已在" % sid) if os.path.exists(p) else "%s 不在" % sid)

        calls = []
        fail_on = {"sid": "S2"}          # 用可變的盒子傳「讓哪一步失敗」，模擬外部工具的非零 rc

        def toy_runner2(cmd, cwd=None, log=None):
            """假的外部工具：命令最後一個字是 sid；真的做出產物（模擬工具自己的讀數）。"""
            calls.append(cmd)
            sid = cmd[-1]
            if sid == fail_on["sid"]:
                return 1, "boom\n"
            with open(os.path.join(cwd, "%s.out" % sid), "w", encoding="utf-8") as fh:
                fh.write("done\n")
            if log:
                with open(log, "w", encoding="utf-8") as fh:
                    fh.write("log of %s\n" % sid)
            return 0, "ok\n"

        def judge(root, ctx):
            """in-process 步的形狀：**自己做判詞、自己落地產物**（真實的 S6 寫 verdict.json）。
            它的 done() 讀的就是那個產物 ⇒ 跑完之後這一步才會自己翻 done（不是憑 rc）。"""
            ok = os.path.exists(os.path.join(root, "S2.out"))
            if ok:
                with open(os.path.join(root, "S3.out"), "w", encoding="utf-8") as fh:
                    fh.write("verdict=PASS\n")
            return 0, "", ok, "判詞：%s" % ("過" if ok else "不過（S2 沒做）")

        def toy():
            return [
                dict(id="S1", name="做 S1", commands=[["touch", "S1"]], done=lambda r, c: mk_done(r, c, "S1"),
                     receipt=lambda r, c: mk_done(r, c, "S1"), next_on_fail=["看 S1 的 log"]),
                dict(id="S2", name="做 S2", commands=[["touch", "S2"]], done=lambda r, c: mk_done(r, c, "S2"),
                     next_on_fail=["看 S2 的 log"]),
                dict(id="S3", name="判詞", inprocess=judge, done=lambda r, c: mk_done(r, c, "S3"),
                     next_on_fail=["看 S3 的 log"]),
            ]

        # ① 宣告體檢：缺 done／缺 next_on_fail／重複 id 都要當場擋。
        def _rejects(steps):
            return _raises(lambda: _check_steps(steps))

        _ok_done = lambda r, c: (False, "")  # noqa: E731
        case("契約：缺 done ⇒ 拒收", _rejects(
            [dict(id="X", name="x", commands=[["true"]], next_on_fail=["a"])]))
        case("契約：缺 next_on_fail ⇒ 拒收", _rejects(
            [dict(id="X", name="x", commands=[["true"]], done=_ok_done)]))
        case("契約：沒有 commands 也沒有 inprocess ⇒ 拒收", _rejects(
            [dict(id="X", name="x", done=_ok_done, next_on_fail=["a"])]))
        case("契約：重複 id ⇒ 拒收", _rejects([
            dict(id="X", name="x", commands=[["true"]], done=_ok_done, next_on_fail=["a"]),
            dict(id="X", name="x2", commands=[["true"]], done=_ok_done, next_on_fail=["a"])]))

        # ②b CLI：--status 明列（等於預設）；--status 與 --go 並用 ⇒ 拒收（不是靜默選一個）。
        calls.clear()
        rc_s, out_s = quiet(main, toy(), argv=["--status"], root=td,
                            journal=os.path.join(td, "j6.jsonl"))
        case("CLI：--status 明列且等於預設（不叫 runner、rc=2）", rc_s == 2 and calls == [], rc_s)

        def _cli_raises(argv):
            try:
                quiet(main, toy(), argv=argv, root=td, journal=os.path.join(td, "j7.jsonl"))
                return False
            except SystemExit:
                return True

        case("CLI：--status 與 --go 不能同時（拒收，不是靜默選一個）",
             _cli_raises(["--status", "--go"]))

        # ② 預設不執行：--status 不叫 runner，且 pending ⇒ rc=2。
        rc, out = quiet(run, toy(), root=td, go=False, runner=toy_runner2,
                        journal=os.path.join(td, "j1.jsonl"))
        case("--status 不呼叫 runner", calls == [], calls)
        case("--status：pending ⇒ rc=2", rc == 2, rc)
        case("--status：印出會跑的命令", "$ touch S1" in out, out[-200:])

        # ③ fail-fast：S2 失敗 ⇒ 停在 S2、印 hints、journal 記 fail、S3 沒被碰。
        fail_on["sid"] = "S2"
        rc, out = quiet(run, toy(), root=td, go=True, runner=toy_runner2,
                        journal=os.path.join(td, "j2.jsonl"))
        case("--go：S2 失敗 ⇒ 停（只有 S1/S2 被叫）", [c[-1] for c in calls] == ["S1", "S2"],
             [c[-1] for c in calls])
        case("--go：失敗回 1 且印『下一個該看的東西』", rc == 1 and "下一個該看的東西" in out, rc)
        recs = [json.loads(l) for l in open(os.path.join(td, "j2.jsonl"), encoding="utf-8")]
        case("--go：journal 記到 fail（可接續）",
             any(r["step"] == "S2" and r["status"] == "fail" for r in recs), recs)
        case("--go：失敗的那一步沒有產物就不會被當成 done",
             not os.path.exists(os.path.join(td, "S2.out")))

        # ④ 接續：修好後重跑 ⇒ S1 skip、S2/S3 做完、rc=0；S1 的命令沒有再被叫。
        fail_on["sid"] = ""
        calls.clear()
        rc, out = quiet(run, toy(), root=td, go=True, runner=toy_runner2,
                        journal=os.path.join(td, "j3.jsonl"))
        case("接續：重跑後 rc=0、全部完成", rc == 0 and "全部完成" in out, out[-200:])
        case("接續：已完成的 S1 被 skip（命令沒再跑）", [c[-1] for c in calls] == ["S2"], calls)
        case("in-process 步：判詞寫進自己的 log",
             os.path.exists(os.path.join(td, "window_logs/S3.log")))
        case("in-process 步：狀態由 done() 判 ⇒ 現在是 done",
             plan(toy(), root=td)[2]["state"] == "done")

        # ⑤ --only 只做指定的步（其餘連 skip 都不印）。
        for f in ("S2.out", "S3.out"):
            os.remove(os.path.join(td, f))
        calls.clear()
        rc, out = quiet(run, toy(), root=td, go=True, only=["S2"], runner=toy_runner2,
                        journal=os.path.join(td, "j4.jsonl"))
        case("--only：只跑 S2（S3 沒被碰）", [c[-1] for c in calls] == ["S2"], calls)
        case("--only：還有 pending ⇒ rc=2", rc == 2, rc)

        # ⑥ preflight：不 OK 就拒跑（fail-closed），且**不叫 runner**。
        calls.clear()
        rc, out = quiet(run, toy(), root=td, go=True, runner=toy_runner2,
                        preflight=lambda root, ctx: dict(ok=False, reasons=["補丁不在"], notes=["note"]),
                        journal=os.path.join(td, "j5.jsonl"))
        case("preflight 不過 ⇒ 拒跑且不叫 runner", rc == 1 and calls == [], (rc, calls))
        case("preflight 的理由有印出來", "補丁不在" in out, out[-200:])

        # ⑦ 逐趟 skip：一步裡有好幾條命令時，每一條可以有自己的 skip_if（不重跑已有的答案）。
        calls2 = []

        def two_runner(cmd, cwd=None, log=None):
            calls2.append(" ".join(cmd))
            return 0, "ok\n"

        def receipt_two(r, c):
            with open(os.path.join(r, "A.out"), "w", encoding="utf-8") as fh:
                fh.write("兩條都做了\n")
            return True, "兩條都做了"

        toy2 = [dict(id="A", name="兩條命令", commands=[
            ["one"],
            dict(cmd=["two"], skip_if=lambda r, c: (True, "two 已在"), log="A/two.log")],
            done=lambda r, c: (os.path.exists(os.path.join(r, "A.out")), "A.out 在/不在"),
            receipt=receipt_two, next_on_fail=["看 log"])]
        rc, out = quiet(run, toy2, root=td, go=True, runner=two_runner,
                        journal=os.path.join(td, "j6.jsonl"))
        case("逐趟 skip：有 skip_if 的那一條沒被叫", calls2 == ["one"], calls2)
        case("逐趟 skip：skip 的理由有印、步仍然走 receipt（rc=0）",
             "skip（已在）" in out and rc == 0, (rc, out[-160:]))

        # ⑧ 重跑不吃掉證據：不通過的步再跑一次時，上一趟的產物被搬進 archive（不是被覆蓋）。
        def re_runner(cmd, cwd=None, log=None):
            return 0, "ok\n"

        def receipt_art(r, c):
            with open(os.path.join(r, "re.out"), "w", encoding="utf-8") as fh:
                fh.write("第二趟\n")
            return True, "第二趟寫好了"

        with open(os.path.join(td, "re.out"), "w", encoding="utf-8") as fh:
            fh.write("第一趟（不通過那趟）\n")
        toy3 = [dict(id="P", name="會重跑的步", commands=[["again"]],
                     done=lambda r, c: (False, "產物不新鮮 ⇒ 重跑"),
                     receipt=receipt_art, artifacts=["re.out"], next_on_fail=["看 log"])]
        rc, out = quiet(run, toy3, root=td, go=True, runner=re_runner,
                        journal=os.path.join(td, "j8.jsonl"), log_dir="Plogs")
        arch = glob.glob(os.path.join(td, "Plogs", "archive", "P.*", "re.out"))
        case("重跑前留檔：舊產物被搬進 <log_dir>/archive/<步驟>.<時戳>/",
             len(arch) == 1 and "第一趟" in open(arch[0], encoding="utf-8").read(), arch)
        case("重跑後：新產物在原本的位置（兩份並存、舊的沒被覆蓋）",
             ("證據留檔" in out or rc == 0) and os.path.exists(os.path.join(td, "re.out"))
             and "第二趟" in open(os.path.join(td, "re.out"), encoding="utf-8").read())
        case("留檔的宣告：archive 步在建置前就把舊的搬走，log_dir 為空時不亂搬",
             archive_artifacts(toy3[0], td, "") == [], "log_dir='' ⇒ 不搬")

        # ⑨ CLI：--list-steps 與 --only 的驗證。
        rc, out = quiet(main, toy(), argv=["--list-steps"])
        case("--list-steps：列出步驟與收貨來源", rc == 0 and "S3" in out and "自訂" in out, out)
        case("--only 打錯 ⇒ SystemExit", _raises(lambda: main(toy(), argv=["--only", "S9"])))

    print("== selftest %d/%d ==" % (good[0], good[1]))
    return 0 if good[0] == good[1] else 1


def _raises(fn) -> bool:
    try:
        fn()
    except SystemExit:
        return True
    except Exception:  # noqa: BLE001
        return True
    return False


if __name__ == "__main__":
    sys.exit(selftest() if "--selftest" in sys.argv else
             (print(__doc__) or 0))
