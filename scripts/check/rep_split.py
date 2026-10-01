#!/usr/bin/env python3
"""rep_split.py — 「一個 rep 一次啟動、之間冷卻」的認證 session（一個 arm = 一個 session）。

## 為什麼需要它（量到的事實，不是偏好）

`llama-bench -r 3` 的三個 rep 在**同一個行程**裡跑，而這三個 rep **不可交換**：

- `--delay` 是**每個 test 一次**（`llama-bench.cpp:3353`，在 reps 迴圈 `:3434` **之外**）⇒ 它冷不到
  rep 之間；`cell_contract` 又**明文拒 `reps=1`** ⇒「rep 之間冷卻」在舊口徑下不可表達。
- 實測（`docs/MTP_DRAFT_FIRST_STEP_2026-09-28.md`、`docs/ARM_DRAFT_LIVENESS_2026-09-28.md`）：
  rep1 活的同時 rep2/rep3 的 draft 鏈整個死掉（`drafted=0`、`mean_len=1.0`），而那支臂仍被讀成
  「k=3」。一支 77 秒的臂能讓 swap 長 2.8 GB、free 一度只剩 16.8 MiB。
- 而 corpus 裡**唯一沒有出現過死鏈**的 k 結論（`Backup/k_abba_2026-09-23/abba/`，21 個讀數、
  `mean_len` 2.21–2.97 全活）正是採**一個 rep 一次量**的形狀。

## 這個協定強制什麼（每一條都 fail-closed）

1. **每次啟動前必須 NOMINAL**，等不到（超過 `cool_max_s`）就**拒跑**。注意這與
   `thermal_pressure.wait_nominal` 的其他 caller 不同：它們可以「照跑、但標記為不可引用」，
   這個協定直接用 `ok=False` 當**拒絕理由** —— 一個熱啟動正是它要移除的污染。
2. **每次啟動恰好 1 個 measured rep**，且這是**卡片上宣告的孿生 cell**（`*_repsplit`：
   `reps: 1` ＋ `rep_split{of, launches, cool_to, cool_max_s}`）才合法。`reps` 在權威 cell 仍是
   嚴格維度，**沒有被放寬**：`cell_contract` 會拒任何未宣告的 `reps=1`，而孿生的
   `launches × 1` 必須等於 base cell 的 `reps`（孿生不能用來量比較少的 rep）。
3. **逐 launch 驗證**：產物要有恰好 1 個 sample、`spec_draft_n_max` 讀回來要等於要求的 k、
   而那**一個** measured rep 的 draft 鏈必須活著（`draft_liveness.verdict(expect_timed=1)`）。
4. **任何一次啟動失敗 ⇒ 整個 session 拒絕**，不寫出部分 arm。理由：一個 arm 的 3 個 rep 必須
   全部通過同一組規則，否則它不是那個 arm 的 3 個樣本，而是一個混合體 —— 那正是這一輪要停止
   的東西。

## 成本（由工具自己量、印出來、寫進產物）

代價是**每個 rep 重付一次載入＋暖機＋depth prefill**（`-r 3` 只付一次），加上 N−1 次冷卻等待。
`--json` 的 `rep_split` 區塊會記 `launch_wall_s` / `cool_waited_s` / `session_wall_s`，並在 stdout
印一張表，所以「這個協定貴多少」是可引用的數字，不是感覺。

## 用法

    python3 scripts/check/rep_split.py \
      --cell delivery-repsplit \
      --arm "prod25:!CGC_PREFILL_STREAM=1;!CGC_GATHER_SLAB_CAP=256" \
      --charter scripts/check/charters/exp-k3-pair-cert.yaml \
      --shape "--prompt 0 --batch 512 --ctx-size 4096 --warm-skip 64 --fixed-fill-seed 0 \
               --spec-type draft-mtp" \
      --k 3 --out /tmp/kb/r1_b_k3 --json /tmp/kb/r1_b_k3/arm.json

    python3 scripts/check/rep_split.py --selftest
    python3 scripts/check/rep_split.py --dry-run ...        # 零 GPU：只印計畫與每次啟動的命令
"""
from __future__ import annotations

import argparse
import glob
import importlib.util
import json
import os
import shlex
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
PY = sys.executable or "python3"

EXIT_OK, EXIT_USAGE, EXIT_REFUSED = 0, 2, 3


class RepSplitError(RuntimeError):
    """A declaration/usage problem: raised so `main` can print it, and so the selftest can assert
    the refusal instead of the process exiting."""


def _say(*a, **k):
    """Every progress line is FLUSHED.

    A session is minutes long and its whole value is that a human can watch it decide: with the
    default buffering, redirecting this tool to a file ("> run.log &", which is how a long session
    has to be launched) showed NOTHING for the first minutes -- the trace appeared only when the
    4 KB block filled. Progress that is invisible until it is over is not progress.
    """
    print(*a, flush=True, **k)


def _load(name: str, fname: str):
    """Import a sibling script by path.

    It MUST be registered in `sys.modules` before `exec_module`: on Python 3.9,
    `dataclasses` resolves string annotations by looking the module up in `sys.modules`, and a
    module that is executing but not registered there dies with
    `AttributeError: 'NoneType' object has no attribute '__dict__'` -- from inside the stdlib, at
    the first `@dataclass` of the imported file, which reads like the imported file is broken.
    """
    if name in sys.modules:
        return sys.modules[name]
    path = HERE / fname
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# ───────────────────────── 啟動形狀 ─────────────────────────

def shape_args(shape: str | None) -> list[str]:
    """`--shape` 的明文參數，**拒收 `--reps`**。

    `--reps` 是這個協定的定義，不是可調參數：讓它從 shape 流進去會讓「一次啟動一個 rep」變成
    口號。擋在這裡，理由指名。
    """
    args = shlex.split(shape or "")
    bad = [a for a in args if a == "--reps" or a.startswith("--reps=")]
    if bad:
        raise RepSplitError(
            "--shape 內含 %s：`--reps` 由本協定決定（=1），不開放調整。"
            "要換 rep 數請改宣告的孿生 cell（rep_split.launches），不要繞過它。" % bad)
    return args


def profile_of(arm: str) -> str:
    return arm.split(":", 1)[0]


# ───────────────────────── 產物讀取 ─────────────────────────

def decode_row(rec_path: str | os.PathLike):
    """(row, k) from one matrix arm record. The decode row is `n_gen > 0 and n_depth > 0`.

    刻意**不套用 `>= 2` 樣本的下限**：`k_swing_decompose.matrix_arm_rep_ts` 有那個下限是因為它
    要算 within-arm 變異，而一次單 rep 啟動**合法地**只有 1 個 sample。這個函式是「一次啟動」
    的讀者，不是「一支臂」的讀者 —— 兩個問題不同，用同一個函式就會有一個被答錯。
    """
    try:
        d = json.load(open(rec_path))
    except (OSError, ValueError):
        return None, None
    if isinstance(d, list):
        d = d[0] if d else None
    if not isinstance(d, dict):
        return None, None
    k = d.get("spec_draft_n_max")
    for row in (d.get("rows") or []):
        if not isinstance(row, dict) or not row.get("n_gen") or not row.get("n_depth"):
            continue
        return row, k
    return None, k


def gate_of(rec_path) -> dict:
    """This launch's run-internal memory gate, or {} when the record carries none.

    Empty is NOT treated as "fine" here: `run_session` refuses when `harness bench` returns
    non-zero, and the matrix returns 4 exactly when this gate is not ok (or absent).
    """
    try:
        d = json.load(open(rec_path))
    except (OSError, ValueError):
        return {}
    if isinstance(d, list):
        d = d[0] if d else None
    return ((d or {}).get("memory_gate") or {}) if isinstance(d, dict) else {}


def one_sample(row) -> list[float]:
    return [float(x) for x in (row.get("samples_ts") or [])
            if isinstance(x, (int, float)) and x > 0]


# ───────────────────────── session ─────────────────────────

@dataclass
class Launch:
    index: int
    rc: int = -1
    wall_s: float = 0.0
    ldir: str = ""
    rec: str = ""
    log: str = ""
    samples: list[float] = field(default_factory=list)
    k_seen: int | None = None
    liveness_rc: int | None = None
    liveness: list[str] = field(default_factory=list)
    cool: dict = field(default_factory=dict)
    state_gate: dict = field(default_factory=dict)
    memory_gate: dict = field(default_factory=dict)
    thermal_after: dict = field(default_factory=dict)
    swap_before_mb: float | None = None
    swap_after_mb: float | None = None
    refusal: str = ""


def _plan(cell: str, contract_path: str | None):
    cc = _load("cc_plan", "cell_contract.py")
    ctr = cc.load_contract() if not contract_path else json.loads(Path(contract_path).read_text())
    names = cc.cell_names(ctr)
    plan = cc.rep_split_plan(ctr, cell)
    if plan is None:
        raise RepSplitError(
            f"cell {cell!r} 不是卡片宣告的 rep-split 孿生（已宣告：{names}）。\n"
            f"   這個協定不接受把 reps 當參數傳：孿生 cell 內就是它的定義"
            f"（reps=1 ＋ rep_split）。要新增一個，請在測試卡 §2.5 的 cells 區塊宣告它。")
    return plan


def _launch_cmd(a, ldir: str, rec: str) -> list[str]:
    cmd = [PY, str(HERE / "harness.py"), "bench",
           "--arm", a.arm, "--reps", "1",              # 1 rep = 這個協定的定義
           "--cell", a.cell,
           "--workdir", ldir, "--json", rec,
           # harness 自己的 launch 前閘照留（defense in depth），但只給它短窗：我們**已經**等過
           # NOMINAL 了，若這段期間又熱起來，寧可它現在拒跑（rc≠0 ⇒ session 拒絕），
           # 也不要它偷偷等 420 秒而讓「冷卻花多久」變成沒人量過的數字。
           "--cool-max-s", str(min(int(a.cool_max_s), 30))]
    if a.charter:
        cmd += ["--charter", a.charter]
    return cmd + a.shape


def _real_launch(a, ldir: str, rec: str, logf: str, cmd: list[str], log=_say) -> int:
    """Run one launch, writing its full output to `logf` and a HEARTBEAT to the session log.

    Measured friction this fixes: a launch is minutes long, and with the output only going to a file
    the session log went silent for the whole of it -- so "is it stuck, or is it measuring?" was
    unanswerable, and a long run had to be watched by tailing a *different* file. The heartbeat is
    deliberately not a tee: the artifacts stay clean (one log per launch) while the session's own
    log still shows that time is passing and how much of it.
    """
    env = {**os.environ}
    with open(logf, "w") as fh:
        fh.write("$ " + " ".join(shlex.quote(c) for c in cmd) + "\n\n")
        fh.flush()
        p = subprocess.Popen(cmd, cwd=str(ROOT), stdout=fh, stderr=subprocess.STDOUT, env=env)
        t0, nxt = time.time(), 30.0
        while True:
            try:
                return p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                el = time.time() - t0
                if el >= nxt:
                    log(f"      … 啟動中（{el:.0f}s；輸出在 {os.path.basename(logf)}）")
                    nxt += 30.0


def run_session(args, launch_fn=None, wait_fn=None, sys_fn=None, log=_say) -> int:
    """The protocol. Returns an exit code; writes the merged record only when EVERY launch passed."""
    plan = _plan(args.cell, args.contract)  # RepSplitError → main
    if args.launches is not None and int(args.launches) != plan["launches"]:
        log(f"⛔ --launches {args.launches} ≠ 宣告的 {plan['launches']}（cell {plan['name']}）。"
            f"這是宣告的一部分，不從命令列改。")
        return EXIT_USAGE
    launches = plan["launches"]

    # 記憶體模組無條件載入：起跑狀態閘用它，而**即使呼叫端注入了 sys_fn**（單測就是這樣）
    # 也要判。從前它只在「需要建預設 sys_fn」時才載入，所以注入 sys_fn 的那條路會在閘門上
    # `UnboundLocalError` —— 一個只出現在測試路徑的崩潰，而那正是閘門沒被跑到的那種缺陷。
    mp = _load("mp_rs", "memory_pressure.py")
    if wait_fn is None or sys_fn is None:
        tp = _load("tp_rs", "thermal_pressure.py")
        wait_fn = wait_fn or (lambda cap: tp.wait_nominal(timeout_s=cap, log=log))

        def _default_sys():
            """One memory reading per call, shared by both fields, and it is a MEDIAN of three.

            Two separate readings would make `swap_used_mb` and `memory` two different instants --
            the state gate judges one while the log prints the other. And a single reading is not
            enough for the gate: measured 2026-09-28, `pages_free` on a quiet box read 6152 MiB and
            then 4334 MiB a minute later with nothing launched, which straddles the 5000 MiB floor.
            `steady()` costs ~1.4 s against a 41 s launch, and it carries the swing it saw.
            """
            m = mp.steady()
            return {"thermal": tp.stamp(), "memory": m,
                    "swap_used_mb": m.get("swap_used_mb")}

        sys_fn = sys_fn or _default_sys
    dl = _load("dl_rs", "draft_liveness.py")

    out = Path(args.out)
    base = Path(args.workdir or (out / "launches"))
    out.mkdir(parents=True, exist_ok=True)
    base.mkdir(parents=True, exist_ok=True)

    log(f"[rep-split] cell={plan['name']} (孿生 of {plan['of']}) launches={launches} "
        f"reps/launch=1 cool_to={plan['cool_to']} cap={plan['cool_max_s']:.0f}s")
    log(f"[rep-split] arm={args.arm}  k={args.k if args.k else '（由 llama-bench 預設 3）'}")

    t_session = time.time()
    done: list[Launch] = []

    for i in range(1, launches + 1):
        ldir = str(base / f"L{i:02d}")
        rec = str(out / f"launch{i:02d}.json")
        logf = str(out / f"launch{i:02d}.harness.log")
        Path(ldir).mkdir(parents=True, exist_ok=True)

        # (1) 冷卻：等不到 NOMINAL 就是拒絕，不是警告。
        log(f"[rep-split] launch {i}/{launches}: 等 {plan['cool_to']}（上限 "
            f"{args.cool_max_s:.0f}s）…")
        cool = wait_fn(float(args.cool_max_s))
        L = Launch(index=i, ldir=ldir, rec=rec, log=logf, cool=cool)
        if not cool.get("ok"):
            L.refusal = (f"冷卻失敗：上限 {args.cool_max_s:.0f}s 內未回到 {plan['cool_to']}"
                         f"（等了 {cool.get('waited_s', 0):.0f}s，最後 {cool.get('label')}）。"
                         f"啟動在熱狀態下量到的東西正是這個協定要移除的污染。")
            done.append(L)
            log("⛔ " + L.refusal)
            return _refuse(args, plan, done, L.refusal, log)

        before = sys_fn()
        L.swap_before_mb = before.get("swap_used_mb")
        log(f"[rep-split] launch {i}/{launches}: thermal={before.get('thermal')} "
            f"swap={L.swap_before_mb}")

        # (1.5) 起跑狀態閘（2026-09-28）：在 spawn **之前**判。這一步以前不存在，代價是「先花 44 秒
        # 跑完，再因為盒子當時太緊而把整段 session 拒掉」——而在跑之前就已经知道它緊了。
        # 為什麼不是跑後的絕對成長、以及兩個判準的校準，見 memory_pressure 的 docstring；
        # 迷宮：這裡判的是起跑讀值，所以冷卻之後、啟動之前的這一個瞬間才是它的取值點。
        sg = mp.state_gate(before.get("memory") or {},
                           waived=os.environ.get("CGC_IGNORE_STATE_BUDGET") or None)
        L.state_gate = sg
        log(f"      state gate: {'OK' if sg['ok'] else 'UNUSABLE'}  free={sg['free_mb']} MiB"
            f" (floor {sg['floor_mb']})  swap={sg['swap_used_mb']} MiB"
            + (f"  (waived: {sg['waived']})" if sg.get("waived") else ""))
        if not sg["ok"]:
            L.refusal = ("起跑狀態不合格（跑前就拒，沒有浪費一次啟動）"
                         + "；".join(sg["reasons"]))
            done.append(L)
            log("⛔ " + L.refusal)
            return _refuse(args, plan, done, L.refusal, log)

        # (2) 啟動。
        cmd = _launch_cmd(args, ldir, rec)
        t0 = time.time()
        if launch_fn is None:
            rc = _real_launch(args, ldir, rec, logf, cmd, log)
        else:
            rc = launch_fn(args, i, ldir, rec, logf)
        L.rc = int(rc)
        L.wall_s = time.time() - t0
        L.thermal_after = sys_fn().get("thermal") or {}
        L.swap_after_mb = sys_fn().get("swap_used_mb")
        log(f"[rep-split] launch {i}/{launches}: rc={L.rc} wall={L.wall_s:.1f}s "
            f"thermal_after={L.thermal_after}")

        # (3) 逐 launch 驗證：記憶體預算、樣本數、k 讀回、那一個 rep 的 liveness。
        #     記憶體先讀：rc≠0 最常見的原因就是它，而「rc=4」單獨不告訴你為什麼。矩陣在
        #     回傳非零前已經把 JSON 寫出來了，所以判詞一定在。
        L.memory_gate = gate_of(rec)
        if L.memory_gate:
            mg = L.memory_gate
            # 判準是「殘差」不是「成長的絕對值」（絕對的那根現在是選用）。舊字串把 budget 印成
            # growth_mb，在閘門換過之後會誤導讀者以為它是判準。
            log(f"      memory gate: {'OK' if mg.get('ok') else 'UNUSABLE'} "
                f"起跑 free={(mg.get('state_gate') or {}).get('free_mb')} MiB "
                f"峰值成長={mg.get('peak_growth_mb')} MiB 殘差={mg.get('residual_mb')} MiB "
                f"(上限 {(mg.get('budget') or {}).get('growth_residual_mb')}) "
                f"min_free={mg.get('min_free_mb')} MiB")
            for w in (mg.get("warnings") or []):
                log(f"      ~~ 警告（不拒跑）：{w}")
            log(f"      swap 曲線: {mg.get('sparkline')}  ({mg.get('n')} 取樣，峰值在 "
                f"{mg.get('peak_at')})")
        if L.rc != 0:
            L.refusal = f"launch {i} 的 harness bench rc={L.rc}（不是量測結果）-- 見 {logf}"
            if L.memory_gate and not L.memory_gate.get("ok"):
                L.refusal += "；記憶體預算：" + "；".join(L.memory_gate.get("reasons") or [])
            done.append(L)
            return _refuse(args, plan, done, L.refusal, log)

        row, k_seen = decode_row(rec)
        if row is None:
            L.refusal = f"launch {i} 沒有 decode cell row（{rec}）"
            done.append(L)
            return _refuse(args, plan, done, L.refusal, log)
        L.k_seen = 3 if k_seen is None else int(k_seen)
        L.samples = one_sample(row)
        if len(L.samples) != 1:
            L.refusal = (f"launch {i} 有 {len(L.samples)} 個 sample，但一次啟動應該恰好 1 個"
                         f"（這一輪的定義就是「一個 rep 一次啟動」）")
            done.append(L)
            return _refuse(args, plan, done, L.refusal, log)
        if args.k and L.k_seen != int(args.k):
            L.refusal = (f"launch {i} 的紀錄說 k={L.k_seen}，被要求 k={args.k} —— "
                         f"旗標沒送到 llama-bench（`--spec-draft-n-max` 沒有 `--spec-type` 是惰性的）")
            done.append(L)
            return _refuse(args, plan, done, L.refusal, log)

        logs = dl.find_logs([ldir])
        text = ""
        for p in logs:
            text += Path(p).read_text(errors="replace")
        L.liveness_rc, L.liveness = dl.verdict(text, expect_timed=1)
        log("      " + " | ".join(L.liveness[:3]))
        if L.liveness_rc != 0:
            L.refusal = (f"launch {i} 的那一個 measured rep 沒通過 draft liveness"
                         f"（rc={L.liveness_rc}）：{' '.join(L.liveness)}")
            done.append(L)
            return _refuse(args, plan, done, L.refusal, log)

        done.append(L)

    # 全部通過才合併。任何一次失敗都在上面 return 掉了，所以走到這裡就是「launches 個 rep 全部
    # 通過同一組規則」。
    wall = time.time() - t_session
    merged = merge(args, plan, done, wall)
    path = Path(args.json or (out / "arm.json"))
    path.write_text(json.dumps(merged, ensure_ascii=False, indent=1))
    _cost_table(done, wall, log)
    log(f"[rep-split] OK：合併紀錄 {path}（{len(done[0].samples) * len(done)} 個樣本？"
        f" 實得 {sum(len(l.samples) for l in done)} 個）")
    return EXIT_OK


def _refuse(args, plan, done, why, log) -> int:
    log("")
    log("⛔ REP-SPLIT SESSION 拒絕 —— 不寫出部分 arm。")
    log(f"   理由：{why}")
    log(f"   已完成的 launch：{[l.index for l in done]}／{plan['launches']}")
    log("   為什麼不留半支：一個 arm 的 rep 必須全部通過同一組規則，否則它不是那個 arm 的")
    log("   三個樣本，而是一個混合體。半支臂比沒有臂更危險（它看起來能用）。")
    return EXIT_REFUSED


def merge(args, plan, done: list[Launch], session_wall_s: float) -> list[dict]:
    """把 N 次單 rep 啟動合成**既有 loader 認得的形狀**（matrix 的 `rows[]` ＋逐 rep samples）。

    不是新形狀：`k_swing_decompose.arm_rows` 與 `draft_liveness` 都讀得懂，而
    `cell_contract` 的 `rep_split` 宣告則回答了「這 3 個 sample 是怎麼來的」。
    """
    samples = [s for L in done for s in L.samples]
    row, _ = decode_row(done[0].rec)
    merged_row = dict(row or {})
    merged_row["samples_ts"] = samples
    merged_row["avg_ts"] = statistics.fmean(samples) if samples else None
    merged_row["stddev_ts"] = statistics.pstdev(samples) if len(samples) > 1 else 0.0
    return [{
        "arm": args.arm,
        "profile": profile_of(args.arm),
        "cell": plan["name"],
        "spec_draft_n_max": done[0].k_seen,
        "rows": [merged_row],
        "incomplete": False,
        "rep_split": {
            "of": plan["of"], "n_launches": plan["launches"], "reps_per_launch": 1,
            "cool_to": plan["cool_to"], "cool_max_s": plan["cool_max_s"],
            "session_wall_s": round(session_wall_s, 1),
            "measure_wall_s": round(sum(L.wall_s for L in done), 1),
            "cool_wall_s": round(sum((L.cool or {}).get("waited_s", 0.0) for L in done), 1),
            # 逐 launch 的記憶體判詞（原始曲線留在各 launch 的 json／同一 out 目錄）。
            # 起跑狀態與跑後殘差分開記：它們是同一道閘的兩半，來源不同、可修正的方式也不同。
            "memory": {
                "all_ok": all((L.memory_gate or {}).get("ok", False) for L in done),
                "state_all_ok": all((L.state_gate or {}).get("ok", False) for L in done),
                "min_launch_free_mb": min((((L.state_gate or {}).get("free_mb"))
                                           if (L.state_gate or {}).get("free_mb") is not None
                                           else 1e18) for L in done),
                "max_peak_growth_mb": max(((L.memory_gate or {}).get("peak_growth_mb") or 0)
                                          for L in done),
                "min_free_mb": min(((L.memory_gate or {}).get("min_free_mb") if
                                    (L.memory_gate or {}).get("min_free_mb") is not None else 1e18)
                                   for L in done),
                "budget": (done[0].memory_gate or {}).get("budget"),
            },
            "evidence": [{
                "index": L.index, "rc": L.rc, "wall_s": round(L.wall_s, 1),
                "samples_ts": L.samples, "k": L.k_seen,
                "cool": L.cool, "thermal_after": L.thermal_after,
                "swap_before_mb": L.swap_before_mb, "swap_after_mb": L.swap_after_mb,
                "liveness_rc": L.liveness_rc, "liveness": L.liveness,
                "state_gate": ({k: v for k, v in L.state_gate.items()
                                if k in ("ok", "reasons", "waived", "free_mb", "floor_mb", "available_mb", "available_floor_mb",
                                         "free_warn_floor_mb", "swap_used_mb", "swap_total_mb",
                                         "swap_headroom_mb", "warnings",
                                         "criteria")}
                               if L.state_gate else None),
                "memory_gate": ({k: v for k, v in L.memory_gate.items()
                                if k in ("ok", "reasons", "warnings", "waived", "growth_mb",
                                         "peak_growth_mb", "residual_mb", "growth_predicted_mb",
                                         "min_free_mb", "peak_at", "sparkline", "n",
                                         "growth_criterion", "residual_criterion", "free_criterion",
                                         "budget")}
                               if L.memory_gate else None),
                "json": os.path.basename(L.rec), "log_dir": L.ldir,
            } for L in done],
        },
    }]


def _cost_table(done: list[Launch], session_wall_s: float, log) -> None:
    log("")
    log("這個協定的成本（每支臂）:")
    log(f"  {'launch':>7s} {'rc':>3s} {'wall_s':>8s} {'cool_s':>8s} {'swap_before':>12s} "
        f"{'swap_after':>11s}  sample")
    for L in done:
        cool_s = (L.cool or {}).get("waited_s", 0.0)
        log(f"  {L.index:>7d} {L.rc:>3d} {L.wall_s:>8.1f} {cool_s:>8.1f} "
            f"{('%.0f' % L.swap_before_mb) if L.swap_before_mb is not None else '?':>12s} "
            f"{('%.0f' % L.swap_after_mb) if L.swap_after_mb is not None else '?':>11s}  "
            f"{L.samples}")
    m = sum(L.wall_s for L in done)
    c = sum((L.cool or {}).get("waited_s", 0.0) for L in done)
    log(f"  measure {m:.1f}s + cool {c:.1f}s = {session_wall_s:.1f}s session wall")
    log("  對照：同一個 cell 的 `-r 3` 單次啟動只付**一次**載入＋暖機＋depth prefill，"
        "所以這個協定的代價主要是（N−1）次重付的載入與 N−1 次冷卻。")


# ───────────────────────── selftest ─────────────────────────

def selftest() -> int:
    import tempfile
    checks: list[tuple[str, bool]] = []

    def c(name, ok):
        checks.append((name, bool(ok)))

    NOM = {"ok": True, "waited_s": 12.0, "level": 0, "label": "NOMINAL", "timed_out": False}
    HOT = {"ok": False, "waited_s": 60.0, "level": 2, "label": "HEAVY", "timed_out": True}

    def mk_log(path: Path, mean_len: float = 2.0, k: int = 3):
        path.write_text(
            f"draft n_max={k}\n"
            f"CGC-BENCH-ACCEPT phase=warm_skip rounds=35 drafted=80 acc_drafts=40 "
            f"mean_len=2.1000 draft_ratio=0.5 gen_tokens=64 n_gen=64\n"
            f"CGC-BENCH-ACCEPT phase=timed rounds=38 drafted=82 acc_drafts=40 "
            f"mean_len={mean_len:.4f} draft_ratio=0.5 gen_tokens=64 n_gen=64\n")

    def mk_rec(path: Path, ts: list[float], k: int | None = 3):
        path.write_text(json.dumps([{
            "arm": "prod25:!A=1", "spec_draft_n_max": k,
            "rows": [{"n_prompt": 0, "n_gen": 128, "n_depth": 512, "samples_ts": ts,
                      "avg_ts": statistics.fmean(ts)}],

        }]))

    def fake_launcher(args, i, ldir, rec, logf):
        Path(ldir).mkdir(parents=True, exist_ok=True)
        mk_rec(Path(rec), [10.0 + i])
        mk_log(Path(ldir) / f"llama_bench_x_p0_n128_d512_r1.stderr.log", 2.0 + 0.1 * i)
        Path(logf).write_text("fake\n")
        return 0

    def ns(tmp, **over):
        a = argparse.Namespace(
            cell="delivery-repsplit", arm="prod25:!CGC_PREFILL_STREAM=1;!CGC_GATHER_SLAB_CAP=256",
            charter=None, shape="--prompt 0 --batch 512 --ctx-size 4096 --warm-skip 64",
            k=None, out=str(tmp), workdir=None, json=None, contract=None,
            cool_max_s=420.0, launches=None)
        for kk, vv in over.items():
            setattr(a, kk, vv)
        return a

    quiet = lambda *a, **k: None  # noqa: E731

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        a = ns(tmp)
        a.shape = shape_args(a.shape)  # noqa: F841  (the real path parses it in main)
        rc = run_session(a, launch_fn=fake_launcher, wait_fn=lambda cap: dict(NOM),
                         sys_fn=lambda: {"thermal": {"level": 0, "label": "NOMINAL"},
                                         "swap_used_mb": 3000.0,
                                         "memory": {"pages_free_mb": 8000.0, "pages_available_mb": 9000.0,
                                                    "swap_used_mb": 3000.0,
                                                    "swap_total_mb": 4096.0}}, log=quiet)
        c("happy path：3 次啟動 -> rc 0", rc == EXIT_OK)
        merged = json.loads((tmp / "arm.json").read_text())
        ts = merged[0]["rows"][0]["samples_ts"]
        c("合併紀錄有 3 個樣本且順序＝啟動順序", ts == [11.0, 12.0, 13.0])
        c("合併紀錄帶 rep_split 證據（3 筆，各 1 sample）",
          merged[0]["rep_split"]["n_launches"] == 3
          and [e["samples_ts"] for e in merged[0]["rep_split"]["evidence"]] == [[11.0], [12.0], [13.0]])
        c("合併紀錄記了冷卻與量測時間",
          merged[0]["rep_split"]["cool_wall_s"] == 36.0
          and merged[0]["rep_split"]["measure_wall_s"] >= 0.0)

        # 合併紀錄必須真的被既有 loader 讀得動 —— 這是「不是新形狀」的證明，不是聲明。
        kw = _load("kw_rs", "k_swing_decompose.py")
        p = tmp / "arm.json"
        c("既有 loader（k_swing_decompose.arm_rows）讀得懂合併紀錄",
          kw.arm_rows(str(p)) == [11.0, 12.0, 13.0])

    def run_case(label, launcher=None, wait=None, **over):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            a = ns(tmp, **over)
            a.shape = shape_args(a.shape)
            rf = launcher or fake_launcher
            try:
                rc = run_session(a, launch_fn=rf, wait_fn=wait or (lambda cap: dict(NOM)),
                                 sys_fn=lambda: {"thermal": {}, "swap_used_mb": 3000.0,
                                                 "memory": {"pages_free_mb": 8000.0,
                                                            "swap_used_mb": 3000.0,
                                                            "swap_total_mb": 4096.0}}, log=quiet)
            except RepSplitError:
                rc = EXIT_USAGE
            wrote = (tmp / "arm.json").exists()
            return rc, wrote, tmp

    # 熱啟動：拒，而且不留半支臂
    rc, wrote, _ = run_case("hot", wait=lambda cap: dict(HOT))
    c("冷卻失敗（仍 HEAVY）-> rc 3 且不寫出 arm", rc == EXIT_REFUSED and not wrote)

    # 第 2 次啟動死鏈：整段拒絕
    def dead_launcher(args, i, ldir, rec, logf):
        Path(ldir).mkdir(parents=True, exist_ok=True)
        mk_rec(Path(rec), [10.0 + i])
        Path(logf).write_text("fake\n")
        mk_log(Path(ldir) / "llama_bench_x.stderr.log", 1.0 if i == 2 else 2.0)
        return 0
    rc, wrote, _ = run_case("dead", launcher=dead_launcher)
    c("第 2 次啟動的 rep 死鏈 -> rc 3 且不寫出 arm", rc == EXIT_REFUSED and not wrote)

    # launch rc≠0
    def bad_rc(args, i, ldir, rec, logf):
        fake_launcher(args, i, ldir, rec, logf)
        return 7 if i == 1 else 0
    rc, wrote, _ = run_case("rc", launcher=bad_rc)
    c("任一次啟動 rc≠0 -> rc 3 且不寫出 arm", rc == EXIT_REFUSED and not wrote)

    # 樣本數不是 1
    def two_samples(args, i, ldir, rec, logf):
        Path(ldir).mkdir(parents=True, exist_ok=True)
        mk_rec(Path(rec), [10.0, 11.0])
        mk_log(Path(ldir) / "l.stderr.log", 2.0)
        Path(logf).write_text("x\n")
        return 0
    rc, wrote, _ = run_case("two", launcher=two_samples)
    c("一次啟動交出 2 個 sample -> 拒（定義被破壞）", rc == EXIT_REFUSED and not wrote)

    # k 讀回不符
    def wrong_k(args, i, ldir, rec, logf):
        Path(ldir).mkdir(parents=True, exist_ok=True)
        mk_rec(Path(rec), [10.0 + i], k=2)
        mk_log(Path(ldir) / "l.stderr.log", 2.0, k=2)
        Path(logf).write_text("x\n")
        return 0
    rc, wrote, _ = run_case("k", launcher=wrong_k, k=3)
    c("k 讀回（2）≠ 要求（3）-> 拒", rc == EXIT_REFUSED and not wrote)

    # launches 與宣告不符
    rc, wrote, _ = run_case("launches", launches=1)
    c("--launches 與宣告不符 -> rc 2（用法錯，不跑）", rc == EXIT_USAGE and not wrote)

    # --shape 內含 --reps
    try:
        shape_args("--batch 512 --reps 3")
        refused = False
    except RepSplitError:
        refused = True
    c("--shape 帶 --reps -> 拒收", refused)

    # 非 rep-split cell
    rc, wrote, _ = run_case("cell", cell="delivery")
    c("非宣告的 rep-split cell（delivery）-> rc≠0", rc != EXIT_OK and not wrote)

    # UNREADABLE：log 沒有 ACCEPT 行
    def no_accept(args, i, ldir, rec, logf):
        Path(ldir).mkdir(parents=True, exist_ok=True)
        mk_rec(Path(rec), [10.0 + i])
        (Path(ldir) / "l.stderr.log").write_text("nothing here\n")
        Path(logf).write_text("x\n")
        return 0
    rc, wrote, _ = run_case("unreadable", launcher=no_accept)
    c("log 無 ACCEPT 行 -> 拒（UNREADABLE 不是通過）", rc == EXIT_REFUSED and not wrote)

    # 起跑狀態閘：緊盒子必須在 spawn **之前**就被拒（不放行），而且 waiver 只降 ok、理由留在產物裡。
    calls = {"n": 0}

    def counting_launcher(args, i, ldir, rec, logf):
        calls["n"] += 1
        return fake_launcher(args, i, ldir, rec, logf)

    def tight_sys():
        return {"thermal": {}, "swap_used_mb": 6100.0,
                "memory": {"pages_free_mb": 1200.0, "pages_available_mb": 1300.0,
                           "swap_used_mb": 6100.0,
                           "swap_total_mb": 8192.0}}

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        a = ns(tmp)
        a.shape = shape_args(a.shape)
        rc = run_session(a, launch_fn=counting_launcher, wait_fn=lambda cap: dict(NOM),
                         sys_fn=tight_sys, log=quiet)
        c("起跑太緊 -> rc 3，且**一次啟動都沒發生**（跑前就拒）",
          rc == EXIT_REFUSED and calls["n"] == 0 and not (tmp / "arm.json").exists())
        os.environ["CGC_IGNORE_STATE_BUDGET"] = "使用者決定在緊盒子上量"
        try:
            rc2 = run_session(a, launch_fn=counting_launcher, wait_fn=lambda cap: dict(NOM),
                              sys_fn=tight_sys, log=quiet)
        finally:
            os.environ.pop("CGC_IGNORE_STATE_BUDGET", None)
        c("CGC_IGNORE_STATE_BUDGET -> 放行，且三次啟動都跑了",
          rc2 == EXIT_OK and calls["n"] == 3)
        ev = json.loads((tmp / "arm.json").read_text())[0]["rep_split"]["evidence"][0]
        c("產物同時留下起跑判詞與 waiver 理由（waiver 不抹證據）",
          ev["state_gate"]["ok"] is True and bool(ev["state_gate"]["waived"])
          and bool(ev["state_gate"]["reasons"]))

    # 跑後殘差那條還在：矩陣回 rc=4，session 必須拒且指名理由
    def over_budget(args, i, ldir, rec, logf):
        Path(ldir).mkdir(parents=True, exist_ok=True)
        Path(rec).write_text(json.dumps([{
            "arm": "x", "spec_draft_n_max": 3,
            "rows": [{"n_prompt": 0, "n_gen": 128, "n_depth": 512, "samples_ts": [9.0]}],
            "state_gate": {"ok": False, "reasons": ["起跑 pages_free 900 MiB < 地板 5000 MiB"]},
            "memory_gate": {"ok": False, "peak_growth_mb": 4200.0, "min_free_mb": 14.0,
                            "reasons": ["成長殘差 +3100 MiB > 250"]},
        }]))
        mk_log(Path(ldir) / "l.stderr.log", 2.0)
        Path(logf).write_text("x\n")
        return 4

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        a = ns(tmp)
        a.shape = shape_args(a.shape)
        lines: list[str] = []
        try:
            rc = run_session(a, launch_fn=over_budget, wait_fn=lambda cap: dict(NOM),
                             sys_fn=lambda: {"thermal": {}, "swap_used_mb": 3000.0,
                                             "memory": {"pages_free_mb": 9000.0}},
                             log=lines.append)
        except RepSplitError:
            rc = EXIT_USAGE
        text = "\n".join(lines)
        c("跑後判詞超標（rc=4）-> 拒，且拒絕訊息指名理由",
          rc == EXIT_REFUSED and not (tmp / "arm.json").exists() and "殘差" in text)

    bad = [n for n, ok in checks if not ok]
    for n, ok in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {n}")
    print(f"selftest {len(checks) - len(bad)}/{len(checks)}")
    return 1 if bad else 0


# ───────────────────────── CLI ─────────────────────────

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="「一個 rep 一次啟動、之間冷卻」的認證 session（見檔頭）。",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--cell", default="delivery-repsplit", help="卡片宣告的 rep-split 孿生 cell")
    ap.add_argument("--arm", help="llama_bench_matrix 臂字串（PROFILE[:!KEY=VAL;...]）")
    ap.add_argument("--charter", help="--charter 轉給 harness bench")
    ap.add_argument("--shape", default="",
                    help="轉給 harness bench 的形狀參數（**不含** --reps，那由協定決定）")
    ap.add_argument("--k", type=int, help="要求的 MTP draft k；會從每次啟動的紀錄讀回來驗")
    ap.add_argument("--out", help="session 輸出目錄（預設 ./rep_split_out）")
    ap.add_argument("--workdir", help="每次啟動的 --workdir 父目錄（預設 <out>/launches）")
    ap.add_argument("--json", help="合併紀錄路徑（預設 <out>/arm.json）")
    ap.add_argument("--contract", help="改用別的合約 JSON（預設讀測試卡 §2.5）")
    ap.add_argument("--launches", type=int, help="必須等於宣告值；不同就是用法錯誤")
    ap.add_argument("--cool-max-s", type=float, default=None,
                    help="冷卻上限秒數（預設取宣告值；**調低只會更容易拒跑**，不會產生熱啟動）")
    ap.add_argument("--dry-run", action="store_true", help="只印計畫與命令，不跑（零 GPU）")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()
    if not args.arm:
        ap.error("--arm 是必要的（或加 --selftest）")

    try:
        plan = _plan(args.cell, args.contract)
    except RepSplitError as e:
        print(f"⛔ {e}")
        return EXIT_USAGE
    if args.cool_max_s is None:
        args.cool_max_s = plan["cool_max_s"]
    args.out = args.out or str(ROOT / "rep_split_out")
    args.shape = shape_args(args.shape)

    if args.dry_run:
        _say(f"[dry-run] cell={plan['name']}（孿生 of {plan['of']}）launches={plan['launches']} "
             f"cool_to={plan['cool_to']} cap={args.cool_max_s:.0f}s")
        for i in range(1, plan["launches"] + 1):
            ldir = str(Path(args.workdir or Path(args.out) / "launches") / f"L{i:02d}")
            rec = str(Path(args.out) / f"launch{i:02d}.json")
            _say(f"  launch {i}: " + " ".join(shlex.quote(x) for x in _launch_cmd(args, ldir, rec)))
        _say("  （每行之前都會先等到 NOMINAL；等不到就整段拒絕）")
        return EXIT_OK

    return run_session(args)


if __name__ == "__main__":
    sys.exit(main())
