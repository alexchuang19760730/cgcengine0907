#!/usr/bin/env python3
"""p1_feed_ab.py — L20-5（P1 餵料搬家）的**端點**：餵料在沒有 debug 插樁的臂上活著。

端點是二值的，不是速度：**對照臂的 `prefetch` 是 `0/0`（池凍結在 prefill），實驗臂除了
同樣的 `prefetch>0/0` 之外，log 裡還出現 `CGC-RB-FEED:`** —— 也就是「池子會重定中心」這件事
不再綁在一個 never-quote 的診斷插樁上。所以判詞是 `FLIP`／`REFUSE` 兩態，不進引用閘門。

為什麼需要這支檢查器（而不是在 YAML 裡寫一句 why）：這張看板對「計數器端點」的規矩是
**當場重跑檢查器**（decode_board_build.D7b）。那一條的存在理由正好與本格相同——20+ 的正確臂
輸出是 garbage ⇒ 它的 t/s 不可引用，硬要 QUOTABLE 只能去捏一個時間讀數。既然端點是計數，
就讓它被機器重判：**產物不在、兩臂分不出來、對照臂不再 `0/0`、實驗臂的餵料消失、或有人把
任何一個 R5 衛星掛回那支臂（log 會出現 MISSMASK 系列列印）⇒ 一律 REFUSE（board 變紅）。**

判準（跑前寫死在 decode_board 的 L20-5 `accept`／`falsify`）：

  ① 兩臂來自**同一份產物**（同 engine_build、同 cell contract ok、同 attribution=none）。
  ② 對照臂：`extra_env` 無 `CGC_RB_FEED`；log 的 `CGC-RB-FEED:` **0 行**且所有
     `prefetch=` 都是 `0/0`。
  ③ 實驗臂：`extra_env` 有 `CGC_RB_FEED`，且**不含** `CGC_MISS_MASK_DBG`／`_COST`／`_HIST`
     （這三個是 `quote_gate.THROUGHPUT_VOID_INSTRUMENTS`，即 R5）。
  ④ 實驗臂：log 的 `CGC-RB-FEED:` **≥1 行**、出現 `prefetch=N/M` 且 `N>0`，且該 log
     **沒有任何** MISSMASK 系列列印（`MISSMASK `／`CGC-MISSMASK-STEP:`／`-COST:`／`-HIST:`）
     —— 這一條就是「它現在不是量具臂」的機器見證。
  ⑤ 註：`gather (ensure) hits=0/0` 是這條臂的**既有簽名**（單次提交臂跳過 per-layer hook），
     出現在兩臂是預期行為，不列入判準。

用法：
    python3 scripts/check/p1_feed_ab.py --artifact Backup/p1_rbfeed_2026-09-30/certified.json
    python3 scripts/check/p1_feed_ab.py --selftest
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile

FEED_RE = re.compile(r"CGC-RB-FEED:")
PREFETCH_RE = re.compile(r"prefetch=(\d+)/(\d+)")
# R5 條目（quote_gate.THROUGHPUT_VOID_INSTRUMENTS）——**臂身分**那一側
R5_ENV = ("CGC_MISS_MASK_DBG", "CGC_MISS_MASK_COST", "CGC_MISS_MASK_HIST")
# 同一批插樁的**列印**前綴。旗標可以不在 env 裡但插樁在跑（或反過來），所以兩邊都驗。
R5_PRINTS = ("MISSMASK ", "CGC-MISSMASK-STEP:", "CGC-MISSMASK-COST:", "CGC-MISSMASK-HIST:")
FEED_SRC = "CGC_RB_FEED"


def _arm_log(root: str, entry: dict) -> str:
    p = entry.get("live_log")
    if not p:
        raise KeyError("產物沒有 live_log")
    return p if os.path.isabs(p) else os.path.join(root, p)


def judge(artifact: str, logs: list[str] | None = None, root: str | None = None) -> dict:
    """回 {verdict, why, control, arm}；判不了 → verdict='REFUSE'。

    `root` 是相對路徑（產物記下的 `live_log`）的基準；預設 cwd。呼叫端（decode_board_build）
    把它傳成 repo 根，否則從別的目錄跑 build 會把相對 log 解到錯的地方（而那種失敗看起來
    就像「見證不見了」，是最難查的一種紅）。
    """
    if not os.path.exists(artifact):
        return {"verdict": "REFUSE", "why": "見證產物不存在：%s" % artifact}
    with open(artifact, encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, list) or len(data) != 2:
        return {"verdict": "REFUSE", "why": "產物不是 2 臂的成對矩陣：%s" % type(data).__name__}

    ctrl = [e for e in data if FEED_SRC not in (e.get("extra_env") or {})]
    arm = [e for e in data if FEED_SRC in (e.get("extra_env") or {})]
    if len(ctrl) != 1 or len(arm) != 1:
        return {"verdict": "REFUSE",
                "why": "兩臂分不出來（有 %s 的 %d 支、沒有的 %d 支）⇒ 這不是一組翻轉配對"
                       % (FEED_SRC, len(arm), len(ctrl))}
    ctrl, arm = ctrl[0], arm[0]

    # ① 同一份產物／同一個引擎／同一種窗口
    if ctrl.get("engine_build") != arm.get("engine_build"):
        return {"verdict": "REFUSE", "why": "兩臂的 engine_build 不同（%s vs %s）⇒ 跨 build 不可比"
                % (ctrl.get("engine_build"), arm.get("engine_build"))}
    for name, e in (("對照臂", ctrl), ("實驗臂", arm)):
        c = e.get("contract") or {}
        if not c.get("ok"):
            return {"verdict": "REFUSE", "why": "%s 的 cell contract 不過：%s"
                    % (name, c.get("mismatches"))}
        a = (e.get("attribution") or {}).get("verdict")
        if a != "none":
            return {"verdict": "REFUSE", "why": "%s 的窗口 attribution=%s ≠ none" % (name, a)}

    # ③ 實驗臂的臂身分：不得掛任何 R5 衛星
    stuck = [k for k in R5_ENV if (arm.get("extra_env") or {}).get(k) not in (None, "0", "")]
    if stuck:
        return {"verdict": "REFUSE", "why": "實驗臂仍掛著 R5 量具：%s ⇒ 它量的不是交付臂" % stuck}

    # logs：預設取各自產物記下的 live_log
    root = root or os.getcwd()
    try:
        ctrl_log, arm_log = (_arm_log(root, ctrl), _arm_log(root, arm)) if not logs \
            else (logs[0], logs[1])
    except (KeyError, IndexError) as exc:
        return {"verdict": "REFUSE", "why": "取不到成對 log：%s" % exc}
    for p in (ctrl_log, arm_log):
        if not os.path.exists(p):
            return {"verdict": "REFUSE", "why": "見證 log 不存在：%s" % p}
    ct = open(ctrl_log, encoding="utf-8", errors="replace").read()
    at = open(arm_log, encoding="utf-8", errors="replace").read()

    # ② 對照臂：餵料不存在、池是凍結的
    n_ctrl = len(FEED_RE.findall(ct))
    pf_ctrl = PREFETCH_RE.findall(ct)
    if n_ctrl != 0:
        return {"verdict": "REFUSE",
                "why": "對照臂（無 %s）也印了 %d 行 CGC-RB-FEED ⇒ 對照不成立" % (FEED_SRC, n_ctrl)}
    if pf_ctrl and any(int(n) != 0 for n, _ in pf_ctrl):
        return {"verdict": "REFUSE",
                "why": "對照臂的 prefetch 不是 0/0：%s ⇒ 池本來就有在動，翻轉無從歸因" % pf_ctrl}

    # ④ 實驗臂：餵料活著、且不是量具臂
    n_arm = len(FEED_RE.findall(at))
    if n_arm == 0:
        return {"verdict": "REFUSE", "why": "實驗臂沒有 CGC-RB-FEED ⇒ 餵料沒跑"}
    pf_arm = PREFETCH_RE.findall(at)
    if not pf_arm:
        return {"verdict": "REFUSE", "why": "實驗臂沒有 prefetch= 這一列 ⇒ 計數器無從讀"}
    if not any(int(n) > 0 for n, _ in pf_arm):
        return {"verdict": "REFUSE", "why": "實驗臂的 prefetch 仍是 0/0 ⇒ 池沒有被預取"}
    bad = [p for p in R5_PRINTS if p in at]
    if bad:
        return {"verdict": "REFUSE",
                "why": "實驗臂的 log 出現 R5 列印 %s ⇒ 它仍是量具臂（旗標可洗、插樁不能）" % bad}

    return {"verdict": "FLIP", "why": "",
            "control": {"feed_lines": n_ctrl, "prefetch": pf_ctrl},
            "arm": {"feed_lines": n_arm, "prefetch": pf_arm}}


# ── selftest：端點的每一條都要能**單獨**把它弄紅 ────────────────────────────────
def _fixture(tmp: str, ctrl_env: dict, arm_env: dict, ctrl_log: str, arm_log: str) -> tuple[str, list[str]]:
    ctrl_p = os.path.join(tmp, "ctrl.stderr.log")
    arm_p = os.path.join(tmp, "arm.stderr.log")
    open(ctrl_p, "w").write(ctrl_log)
    open(arm_p, "w").write(arm_log)
    art = os.path.join(tmp, "pair.json")
    base = {"profile": "prod-new", "engine_build": "deadbeef", "extra_env": {},
            "contract": {"ok": True, "mismatches": []}, "attribution": {"verdict": "none"}}
    a = dict(base, extra_env=ctrl_env, live_log=ctrl_p)
    b = dict(base, extra_env=arm_env, live_log=arm_p)
    json.dump([a, b], open(art, "w"))
    return art, [ctrl_p, arm_p]


def selftest() -> int:
    ok = fail = 0

    def check(name, cond):
        nonlocal ok, fail
        if cond:
            ok += 1
            print("  [PASS] %s" % name)
        else:
            fail += 1
            print("  [FAIL] %s" % name)

    with tempfile.TemporaryDirectory() as tmp:
        good_ctrl = "gather (ensure) hits=0/0\ncache: hit 100.0%\nprefetch=0/0\n"
        good_arm = ("CGC-RB-FEED: feeds=2000 il=29 n=8\nprefetch=2271/66\n"
                    "gather (ensure) hits=0/0\n")
        art, logs = _fixture(tmp, {}, {"CGC_RB_FEED": "1"}, good_ctrl, good_arm)
        r = judge(art, logs)
        check("真產物的形狀 -> FLIP", r["verdict"] == "FLIP")

        # ① 對照臂也餵了
        art2, logs2 = _fixture(tmp, {}, {"CGC_RB_FEED": "1"},
                               "prefetch=0/0\nCGC-RB-FEED: feeds=1 il=0 n=8\n", good_arm)
        check("對照臂也有餵料 -> REFUSE", judge(art2, logs2)["verdict"] == "REFUSE")

        # ② 對照臂的池本來就在動
        art3, logs3 = _fixture(tmp, {}, {"CGC_RB_FEED": "1"}, "prefetch=100/0\n", good_arm)
        check("對照臂 prefetch 非 0 -> REFUSE", judge(art3, logs3)["verdict"] == "REFUSE")

        # ③ 實驗臂沒有餵料
        art4, logs4 = _fixture(tmp, {}, {"CGC_RB_FEED": "1"}, good_ctrl, "prefetch=0/0\n")
        check("實驗臂沒有 CGC-RB-FEED -> REFUSE", judge(art4, logs4)["verdict"] == "REFUSE")

        # ④ 實驗臂的 prefetch 仍全 0
        art5, logs5 = _fixture(tmp, {}, {"CGC_RB_FEED": "1"}, good_ctrl,
                               "CGC-RB-FEED: feeds=1 il=0 n=8\nprefetch=0/0\n")
        check("實驗臂 prefetch 仍 0/0 -> REFUSE", judge(art5, logs5)["verdict"] == "REFUSE")

        # ⑤ 實驗臂偷偷掛回 R5 衛星（env）
        art6, logs6 = _fixture(tmp, {}, {"CGC_RB_FEED": "1", "CGC_MISS_MASK_DBG": "1"},
                               good_ctrl, good_arm)
        check("實驗臂掛 R5 衛星(env) -> REFUSE", judge(art6, logs6)["verdict"] == "REFUSE")

        # ⑥ 旗標洗掉但插樁照印（R5 的**列印**那一側）
        art7, logs7 = _fixture(tmp, {}, {"CGC_RB_FEED": "1"}, good_ctrl,
                               good_arm + "MISSMASK il=1 step=1 nsel=8 misses=3 exps: 5 7 9\n"
                                          "CGC-MISSMASK-STEP: step=1 misses=3 layers=1\n")
        check("旗標洗掉、插樁照印 -> REFUSE", judge(art7, logs7)["verdict"] == "REFUSE")

        # ⑦ 不是一組翻轉配對（兩支都開）
        ctrl_p = os.path.join(tmp, "c2.stderr.log")
        open(ctrl_p, "w").write(good_ctrl)
        bad = os.path.join(tmp, "both.json")
        base = {"engine_build": "deadbeef", "contract": {"ok": True}, "attribution": {"verdict": "none"},
                "live_log": ctrl_p}
        json.dump([dict(base, extra_env={"CGC_RB_FEED": "1"}),
                   dict(base, extra_env={"CGC_RB_FEED": "1"})], open(bad, "w"))
        check("兩臂都開 -> REFUSE", judge(bad)["verdict"] == "REFUSE")

        # ⑧ 跨 build
        art8, logs8 = _fixture(tmp, {}, {"CGC_RB_FEED": "1"}, good_ctrl, good_arm)
        d = json.load(open(art8))
        d[1]["engine_build"] = "cafebabe"
        json.dump(d, open(art8, "w"))
        check("跨 build -> REFUSE", judge(art8, logs8)["verdict"] == "REFUSE")

        # ⑨ 窗口髒
        art9, logs9 = _fixture(tmp, {}, {"CGC_RB_FEED": "1"}, good_ctrl, good_arm)
        d = json.load(open(art9))
        d[1]["attribution"] = {"verdict": "swap"}
        json.dump(d, open(art9, "w"))
        check("實驗臂窗口髒 -> REFUSE", judge(art9, logs9)["verdict"] == "REFUSE")

        # ⑩ 產物不存在
        check("產物不存在 -> REFUSE",
              judge(os.path.join(tmp, "nope.json"))["verdict"] == "REFUSE")

    print("selftest %d/%d" % (ok, ok + fail))
    return 0 if fail == 0 else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--artifact")
    ap.add_argument("--logs", nargs=2, metavar=("CONTROL_LOG", "ARM_LOG"))
    ap.add_argument("--json")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not a.artifact:
        ap.error("--artifact or --selftest")
    res = judge(a.artifact, a.logs)
    if a.json:
        json.dump(res, open(a.json, "w"), ensure_ascii=False, indent=2)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0 if res["verdict"] == "FLIP" else 1


if __name__ == "__main__":
    sys.exit(main())
