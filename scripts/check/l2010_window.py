#!/usr/bin/env python3
"""L20-10 的「清窗成對」窗口（宣告在 `window_runner` 上）：Q1 窗檢 → Q2 A 臂 → Q3 B 臂 → Q4 判詞。

（2026-10-01 第二輪：兩臂換成測試卡 §2.5.4c 的 **reps 孿生** `delivery-reps7`／
`delivery-ws192-reps7` —— 只差 `reps` 3→7，其餘逐字相同。由來：第一輪的效應 +4.8% 小於
n=3 的兩臂散布（A4 ⇒ `REFUSE`）⇒ 格要更多樣本，不是門檻要放寬。命令與旗標由
`cell_contract` 帶出（`--reps 7` 跟著宣告走）；`done` 以**這一輪的格名**為準 ——
舊格（`delivery`）的可引用產物不算這一輪完成，否則整窗會 skip 成一場空。）

為什麼需要它：L20-10 的格已經可滿足（測試卡 §2.5.4b `delivery-ws192`）、成對也跑過了，
那一對在**髒窗**下判 `REFUSE`（兩臂 `attribution=swap`）。本機的阻塞是**絕對**規則
（`max_swap` > `memory_pressure.SWAP_HIGH_MB`＝6144 MiB ＋ 任何正成長 ⇒ swap），而 8 GB 存量
不是引擎的（別的 app 的駐留），且 swap 存量只累積不回收 ⇒ **清窗＝重開機**（`budget_preflight.py`
的同型註記）。重開機回來就跑這一支。

    python3 scripts/check/l2010_window.py            # 只印計畫（rc=2 ⇒ 還有 pending；不跑任何東西）
    python3 scripts/check/l2010_window.py --go       # 真的跑：窗檢 → A → B → 判詞
    python3 scripts/check/l2010_window.py --go --warmup
                                                     # 多一步 W0：同一格先跑一趟**丟棄**的，
                                                     # 讓池子的硬碟填充付在**窗外**（cold vs steady）
                                                     # 它的產物在 PRODUCT_DIR/warmup/、成果照登、
                                                     # **不當量測**（判詞只取 Q2／Q3）

收貨（跑前寫死；判詞本體在 `l2010_verdict.py`，不在這裡重寫一次）：
    Q1 窗檢：llama 行程 0 個 **且** swap 存量 ≤ 6144 MiB —— 後者正是重開機要解的那一條
             （只看存量：起跑閘是壓縮機流量，harness 自己會再擋一次）
             ⚠ 同一段輸出會多印一條**餘裕讀值**（`structural_warning`）：上限 −（上一趟峰值
             Metal 駐留 ＋ 保留）。⚠ 池子**不可以**再加一次（L4 pool 是 adopted from expert
             tensors，它的頁面就在駐留裡）—— 這是 2026-10-01 更正的記帳錯誤。只讀值、不擋人。
    Q2／Q3 ：兩臂各自的權威 row（`p0/n64`）要 `QUOTABLE`；髒了（swap／thermal／離散）不算完成 ⇒ 會重跑
    Q4     ：判詞 ∈ {`START_POINT`, `SAME_BAND`}（`REFUSE` 不算完成 ⇒ fail-fast 並印下一手）

產物：`Backup/l2010_b1b3_2026-09-30/clean_ws192_{A,B}.json` ＋ `clean_ws192_verdict.json`
＋ `docs/L2010_CLEAN_WS192_<日期>.md`（看板 L20-10 的 `needed` spec 掃的就是這幾個）。
"""

import argparse
import json
import os
import re
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import cell_contract as CC           # noqa: E402
import l2010_verdict as LV           # noqa: E402
import memory_pressure as MP         # noqa: E402
import window_runner as WR           # noqa: E402

PRODUCT_DIR = "Backup/l2010_b1b3_2026-09-30"
JOURNAL_REL = PRODUCT_DIR + "/clean_journal.jsonl"
LOG_DIR_REL = PRODUCT_DIR + "/clean_logs"
CHARTER = "scripts/check/charters/e-cell-discriminate-2026-09-29.yaml"
TITLE = "L20-10 清窗成對（delivery vs delivery-ws192）：窗檢 → A → B → 判詞"
DATE = time.strftime("%Y%m%d")
DOC_REL = "docs/L2010_CLEAN_WS192_%s.md" % DATE

# 2026-10-01 第二輪：依測試卡 §2.5.4c 改用 **reps 孿生**（只差 `reps` 3→7；`twin_of` 由
# `cell_contract.validate_reps_twins()` 驗）。維度與旗標**不手抄**：`_cmd_for` 讀的就是這兩個
# 名字 ⇒ 宣告改了，命令自動跟著（`--reps 7` 是這樣進去的）。
ARM_CELLS = {"A": "delivery-reps7", "B": "delivery-ws192-reps7"}

# 暖機（`--warmup`）：同一格、同一臂的一趟**丟棄**跑，只為了讓池子的硬碟填充
# （~4.6 GiB 的 file reads）付在**窗外**。repo 自己的框架就是這個（cold vs steady）：
# `docs/PROD_NEW_BASELINE_REPRO_2026-09-25.md`「差的**不是填充量**，是填充**落點**」——
# 兩輪的 misses／file_reads／resident 幾乎相同，可引用性卻不同。
# 它寫在**與成對不同的路徑**（不會被看板或判詞的 glob 取走）、成果照登、**不當量測**。
WARMUP_DIR = PRODUCT_DIR + "/warmup"
WARMUP_OUT = WARMUP_DIR + "/warmup_A.json"

# 上一場留下的峰值（引擎自己報的 wired 峰值）＋它當時的上限。Q1 用它來預測「這一趟就算窗乾淨，
# 也不可能可引用」——用 harness 自己的三個數字算，不新增門檻。
STRUCTURAL_CACHE = "Backup/metal_working_set.json"


def structural_warning(root: str) -> list:
    """本機餘裕還剩多少？回要印的行（空 list ＝數字讀不到 ⇒ 不亂說）。

    **2026-10-01 的更正（operator 指出）**：上一版把「格宣告的池子 8192」加到量到的 Metal 駐留上、
    得出「超額 8 577 MiB」—— 那是**記帳重複計算**。三條證據：
      (a) 引擎自報 `resident=6197.04 MiB`，而且是**靜態**量（143 槽 × 40 層 × 真實 blob；
          `docs/PROD_NEW_DECODE20_G4MISS_2026-09-28.md` 記著「三趟裡一模一樣」）；
      (b) L4 metal pool 這條路是「**regions adopted from expert tensors**」
          （`src/llama.cpp/src/llama-expert-cache.cpp:4152`：`capacity>0` 走 adopted 分支、
          **不另外配置**一塊 8 GiB）⇒ 池的頁面**就在** `pages_wired` 裡面；
      (c) 16 GB 的算術：峰值 wired 10 814 ＋ 若池子另外算 7 954 ＝ 18 768 MiB ＞ 16 GB
          ⇒ 那一趟不可能跑得完（實測只長 1.5 GB swap）。
    ⇒ 真正的餘裕 ＝ 上限 −（峰值 wired ＋ 保留）；而這一格的成敗取決於池子的硬碟填充
    **落在窗內還是窗外**（`docs/PROD_NEW_BASELINE_REPRO_2026-09-25.md`：“差的不是填充量，
    是填充**落點**”），不是「池子放不下」。

    這是**讀值**（用上一趟的快取），不擋人。
    """
    cache = os.path.join(root, STRUCTURAL_CACHE)
    if not os.path.exists(cache):
        return []
    try:
        with open(cache, encoding="utf-8") as fh:
            c = json.load(fh)
        peak = float(c.get("peak_wired_mb") or 0) or None
        top = float(c.get("mb") or 0) or None
    except Exception:  # noqa: BLE001
        return []
    if not (peak and top):
        return []
    try:
        reserve = float(MP.budget().get("metal_reserve_mb") or 0)
        _pool_mb = None
        try:
            _name, _cell = CC.resolve_cell(CC.load_contract(), ARM_CELLS["A"])
            _pool_mb = float(_cell.get("expert_cache_bytes") or 0) / (1024.0 * 1024.0)
        except Exception:  # noqa: BLE001
            pass
    except Exception:  # noqa: BLE001
        return []
    # 上限取同一份快取的 `mb`：那是那一趟量到的真實（引擎 stderr 的 recommendedMaxWorkingSetSize），
    # 不繞 `metal_ceiling()` 的環境／sysctl 優先序 —— 一個檔一句話，也讓 selftest 是確定性的。
    ceiling = top
    margin = ceiling - (peak + reserve)
    line = ("本機餘裕（**量到的**，不是宣告的）：上限 %.0f −（上一趟峰值 Metal 駐留 %.0f ＋ 保留 %.0f）"
            " ＝ **%+.0f MiB**" % (ceiling, peak, reserve, margin))
    out = [line]
    if _pool_mb:
        out.append("  （池子預算 %.0f MiB 是**宣告值**、不是加項：L4 pool 的區域 adopted from expert "
                   "tensors，它的頁面就在上面的駐留裡；引擎自報的 resident 是靜態量）" % _pool_mb)
    if margin < 1024:
        out.append("  ⇒ 這一趟在**邊緣**上跑（餘裕 %+.0f MiB）：池子那份硬碟填充（~4.6 GiB 的 file reads）"
                   "落在**窗內**時就長成 swap 或一個 stall 的 rep；落在**窗外**（steady）就沒事"
                   "（2026-10-01：A#1 冷啟 ⇒ +1534.81 MiB；A#2 ⇒ 一個 rep 塌到 4.03）"
                   % margin)
        out.append("  ⇒ 因此**先**把餘裕弄出來（關掉吃記憶體的程式）**再**跑，不是先決定要不要換盒子")
    return out


def _cmd_for(root: str, tag: str, out: str = None) -> list:
    """命令由 `cell_contract` 產生（不手抄維度；格改了這裡自動跟著改）。"""
    contract = CC.load_contract()
    name, cell = CC.resolve_cell(contract, ARM_CELLS[tag])
    flags, warn = CC.bench_flags(cell)
    if warn:
        raise SystemExit("cell %s 與 harness bench 不相容：%s" % (name, "；".join(warn)))
    return (["python3", "scripts/check/harness.py", "bench", "--charter", CHARTER,
             "--arm", "prod-new", "--cell", name] + flags
            + ["--json", out or os.path.join(PRODUCT_DIR, "clean_ws192_%s.json" % tag)])


def _cell_guard(got, tag: str) -> bool:
    """這份產物是不是**這一輪要的那一格**？`done` 不能只看「可引用」。

    WHY：孿生把格名換了（`delivery` → `delivery-reps7`），而舊產物還在原地、而且可引用 ——
    只看引用閘門的話，Q2／Q3 會把上一輪（n=3）的答案當成本輪已完成而 skip 掉，
    整窗就跑成一場空。格名不符 ⇒ 這一輪視為還沒跑（重跑前 `window_runner` 會先留檔）。
    """
    return got == ARM_CELLS[tag]


def _quote_ok(root: str, tag: str) -> tuple:
    spec = LV.arms_for_pair("clean")[tag]
    arm = LV.judge_arm(root, tag, spec)
    if arm.get("exists") and not _cell_guard(arm.get("cell_named"), tag):
        return False, "%s：產物是 cell=%r（這一輪要 %r）⇒ 未完成（重跑）" \
            % (tag, arm.get("cell_named"), ARM_CELLS[tag])
    return (arm["verdict"] in ("CONTROL", "DISCRIMINATED", "NOT_NEAR") and
            (arm.get("quote") or {}).get("verdict") == "QUOTABLE"), \
        "%s：%s（%s）" % (tag, arm["verdict"], "／".join(arm.get("reasons") or [])[:120])


def window_check(root: str, ctx: dict) -> tuple:
    """Q1：這一台現在是不是乾淨的窗？（只看存量與行程；流量由 harness 自己判）"""
    st = MP.stamp()
    procs = st.get("llama_procs") or 0
    swap = st.get("swap_used_mb")
    free = st.get("pages_free_mb")
    lines = ["窗檢 %s" % time.strftime("%F %T"),
             "  llama 行程：%s" % procs,
             "  swap 存量：%s MiB（上限 %.0f MiB；attribution 是絕對規則）"
             % (("%.0f" % swap) if swap is not None else "讀不到", MP.SWAP_HIGH_MB),
             "  pages_free：%s MiB" % (("%.0f" % free) if free is not None else "讀不到")]
    lines += ["  " + x for x in structural_warning(root)]
    why = []
    if procs:
        why.append("有 %d 個 llama 行程在跑" % procs)
    if swap is None:
        why.append("swap 讀不到（不當成 0）")
    elif swap > MP.SWAP_HIGH_MB:
        why.append("swap 存量 %.0f > %.0f MiB ⇒ 任何正成長都會被判 swap ⇒ 要重開機（或關掉重量級 app）"
                   % (swap, MP.SWAP_HIGH_MB))
    txt = "\n".join(lines) + "\n"
    if why:
        return 1, txt + "窗未開：%s\n" % "；".join(why), False, "；".join(why)
    return 0, txt, True, "窗乾淨（llama 0 個、swap %.0f ≤ %.0f MiB）" % (swap, MP.SWAP_HIGH_MB)


def verdict_step(root: str, ctx: dict) -> tuple:
    """Q4：判詞＋落檔。REFUSE 不算完成（fail-fast），並把下一手印出來。"""
    v = LV.judge(root, LV.arms_for_pair("clean"))
    v["pair"] = "clean"
    rel = PRODUCT_DIR + "/clean_ws192_verdict.json"
    p = os.path.join(root, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    # 重跑前留檔（同 `window_runner` 的規矩，但 in-process 步不經過它）：判詞檔會被覆蓋，
    # 而它記著**上一輪判什麼**（例：n=3 那一輪的 `REFUSE` 與它的 A4 數字）⇒
    # 先搬一份進 archive 再寫，不然那一輪就只剩人寫的敘事。
    if os.path.exists(p):
        dst = os.path.join(root, LOG_DIR_REL, "archive",
                           "Q4.%s" % time.strftime("%Y%m%d-%H%M%S"), os.path.basename(rel))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.move(p, dst)
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(v, fh, ensure_ascii=False, indent=1)
    a, b = v["arms"].get("A", {}), v["arms"].get("B", {})
    txt = ["L20-10 清窗成對判詞 %s" % v["at"],
           "  A（%s）：%s t/s（%s）" % (LV.ARMS["A"]["cell"], a.get("decode_tps_median"),
                                        a.get("verdict")),
           "  B（%s）：%s t/s（%s）" % (LV.ARMS["B"]["cell"], b.get("decode_tps_median"),
                                        b.get("verdict")),
           "  Δ：%s%%" % (("%+.2f" % v["delta_pct"]) if v.get("delta_pct") is not None else "—"),
           "  VERDICT：%s -- %s" % (v["verdict"], v["why"])]
    if v.get("diagnostic"):
        d = v["diagnostic"]
        sp = {n: ("%.4f" % x) if isinstance(x, (int, float)) else "—"
              for n, x in (d.get("spreads") or {}).items()}
        txt.append("  診斷級（不可當判決）：兩臂中位數差 %+.2f%%；散布 A/B=%s／%s（reps %s）"
                   % (d["delta_pct"], sp.get("A", "—"), sp.get("B", "—"), d.get("reps")))
    ok = v["verdict"] in ("START_POINT", "SAME_BAND")
    doc = os.path.join(root, DOC_REL)
    # ⚠ 機器塊只能改**它自己那一段**：2026-10-01 這支腳本第一次真的跑到 Q4 時，把整份文件
    # 覆寫成 13 行的 stub，把兩趟嘗試的帳與更正全部蓋掉。教訓與「重跑前留檔」同一條：
    # 機器產生的是**區塊**，不是整份文件 —— 人寫的敘事（證據）不得被它踩掉。
    marker = "<!-- judge:begin -->"
    block = ("判詞：**%s**\n\n%s\n```\n%s\n```\n%s\n\n"
             % (v["verdict"], marker, "\n".join(txt), "<!-- judge:end -->"))
    head = "# L20-10 清窗成對（%s）\n\n" % v["at"]
    tail = ("判別句與門檻在 `scripts/check/l2010_verdict.py`（跑前寫死）；這一輪的產物："
            "`%s`、`%s`。\n" % (LV.PRODUCT_DIR + "/clean_ws192_A.json",
                               LV.PRODUCT_DIR + "/clean_ws192_B.json"))
    body = ""
    if os.path.exists(doc):
        with open(doc, encoding="utf-8") as fh:
            old = fh.read()
        if "<!-- judge:begin -->" in old and "<!-- judge:end -->" in old:
            pre = old.split("<!-- judge:begin -->")[0]
            post = old.split("<!-- judge:end -->", 1)[1]
            pre = re.sub(r"^# .*\n\n", head, pre, count=1)
            # 標題外的舊「判詞：**…**」行要全拿掉再放新的：block 自己帶一行，留著會越疊越多
            # （2026-10-01 07:20／07:21 實際各疊出一行；一次執行又只消一行 ⇒ 用不設 count 的清掃）。
            pre = re.sub(r"(?m)^判詞：\*\*[^\n]*\*\*\n\n?", "", pre)
            body = pre + block + post
        else:
            body = head + block + "\n" + old.lstrip("\ufeff")
    else:
        body = head + block + tail
    os.makedirs(os.path.dirname(doc), exist_ok=True)   # 這條路徑是這支指派的 ⇒ 目錄也由它負責建
    with open(doc, "w", encoding="utf-8") as fh:
        fh.write(body)
    txt.append("  判詞落檔：%s" % rel)
    txt.append("  文件：%s" % DOC_REL)
    if not ok:
        txt.append("  下一手：")
        txt += ["    · %s" % x for x in (v.get("next") or [])]
    return (0 if ok else 1), "\n".join(txt) + "\n", ok, "判詞 %s" % v["verdict"]


def _q1_done(root: str, ctx: dict) -> tuple:
    """`done`／`receipt` 的合約是 (ok, why)—— 4 元組是 `inprocess` 的合約（不要混）。"""
    rc, txt, ok, why = window_check(root, ctx)
    return ok, why


def _q4_receipt(root: str, ctx: dict) -> tuple:
    rc, txt, ok, why = verdict_step(root, ctx)
    return ok, why


def steps(ctx: dict) -> list:
    root = ctx.get("root", ROOT)
    warmup = bool(ctx.get("warmup"))
    out = [dict(id="Q1", name="窗檢（重開機過了沒）", inprocess=window_check,
                done=_q1_done, receipt=_q1_done, artifacts=[],
                next_on_fail=[
                    "sysctl vm.swapusage                      # 存量 > 6144 MiB ⇒ 重開機（swap 只累積不回收）",
                    "pgrep -fl 'llama-server|llama-bench'     # 有殘留行程就先收掉",
                    "python3 scripts/check/memory_pressure.py  # 同一支工具的存量／流量讀值",
                ])]
    if warmup:
        out.append(dict(id="W0",
                        name="暖機（fill 付在窗外；成果照登、不當量測）",
                        commands=[dict(cmd=_cmd_for(root, "A", out=WARMUP_OUT),
                                       log="%s/warmup.log" % WARMUP_DIR)],
                        done=lambda r, c: (os.path.exists(os.path.join(r, WARMUP_OUT)),
                                           "暖機產物在/不在：%s" % WARMUP_OUT),
                        receipt=lambda r, c: (os.path.exists(os.path.join(r, WARMUP_OUT)),
                                              "暖機跑過（**它的數字不是成對的答案**：判詞只取 Q2／Q3）"),
                        artifacts=[WARMUP_OUT],
                        next_on_fail=["grep -E 'attribution|swap|thermal' %s" % WARMUP_OUT,
                                      "暖機失敗就把 --warmup 拿掉，先查窗（Q1）"]))
    for tag in ("A", "B"):
        cell = ARM_CELLS[tag]
        out.append(dict(id="Q%d" % (2 if tag == "A" else 3),
                        name="%s 臂（%s）" % (tag, cell),
                        commands=[dict(cmd=_cmd_for(root, tag),
                                       skip_if=lambda r, c, t=tag: _quote_ok(r, t),
                                       log="%s/Q%s.json.log" % (LOG_DIR_REL, tag))],
                        done=lambda r, c, t=tag: _quote_ok(r, t),
                        receipt=lambda r, c, t=tag: _quote_ok(r, t),
                        artifacts=["%s/clean_ws192_%s.json" % (PRODUCT_DIR, tag)],
                        next_on_fail=[
                            "grep -E 'attribution|swap|thermal' %s/clean_ws192_%s.json"
                            % (PRODUCT_DIR, tag) + "  # 窗口項",
                            "python3 scripts/check/l2010_verdict.py --pair clean --check",
                        ]))
    out.append(dict(id="Q4", name="判詞＋落檔", commands=[], inprocess=verdict_step,
                    done=lambda r, c: (os.path.exists(os.path.join(r, DOC_REL))
                                       and (_read_json(os.path.join(r, PRODUCT_DIR,
                                                                    "clean_ws192_verdict.json"))
                                            or {}).get("verdict") in ("START_POINT", "SAME_BAND"),
                                       _q4_state(r)),
                    receipt=_q4_receipt, artifacts=[DOC_REL],
                    next_on_fail=[
                        "cat %s/clean_ws192_verdict.json     # 判詞與兩臂狀態" % PRODUCT_DIR,
                        "python3 scripts/check/l2010_verdict.py --pair clean --check",
                    ]))
    return out


def _read_json(path: str):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:  # noqa: BLE001
        return None


def _q4_state(root: str) -> str:
    """Q4 的現況字串：**看判詞本身**，不是只看文件在不在。

    2026-10-01 的教訓：第一輪的判詞檔在、文件也在，但判的是 `REFUSE` ⇒ 舊版卻顯示
    「判詞：已結」——一個判詞形狀的字給了一個沒判決的狀態（同族：檔案在 ≠ 判決在）。
    """
    vd = _read_json(os.path.join(root, PRODUCT_DIR, "clean_ws192_verdict.json")) or {}
    v = vd.get("verdict")
    if v in ("START_POINT", "SAME_BAND"):
        return "判詞：%s（已結）" % v
    return "判詞：%s（未結 —— 要 START_POINT／SAME_BAND）" % (v or "還沒寫")


def preflight(root: str, ctx: dict) -> dict:
    """跑前的誠實話：窗是不是已經開了（不 OK 就拒跑，不燒盒子）。"""
    st = MP.stamp()
    ok, why = window_check(root, ctx)[2], window_check(root, ctx)[3]
    notes_extra = (["★ 這一輪含**暖機**（W0）：同一格先跑一趟丟棄的、讓池子的 fill 付在窗外；"
                    "它的產物在 %s、**不當量測**（判詞只取 Q2／Q3）。" % WARMUP_OUT]
                   if ctx.get("warmup") else [])
    return dict(ok=ok,
                reasons=[] if ok else [why],
                notes=["（這一步只擋在**窗**上：格與命令都已現成，缺的就是乾淨窗）",
                       "llama 行程 %s 個；swap 存量 %s MiB"
                       % (st.get("llama_procs"), st.get("swap_used_mb"))]
                      + structural_warning(root) + notes_extra,
                header=["Q1 窗檢：%s" % why])


def ctx_factory(args, rest) -> dict:
    """本腳本自己認的旗標（`window_runner` 把不認得的丟進 `rest`）—— 不認得的就拒跑。"""
    known = {"--warmup"}
    unknown = [x for x in rest if x not in known]
    if unknown:
        raise SystemExit("l2010_window 不認得這些旗標：%s（我認的只有 --warmup）" % " ".join(unknown))
    return dict(warmup=("--warmup" in rest))


def selftest() -> int:
    """這一支自己的體檢：重點是 2026-10-01 那兩條（餘裕讀值、暖機那一步）。"""
    import tempfile

    good = [0, 0]

    def case(name, cond, detail=""):
        good[1] += 1
        good[0] += 1 if cond else 0
        print("  %-58s -> %s%s" % (name, "PASS" if cond else "FAIL",
                                   "" if cond else "  " + str(detail)))

    with tempfile.TemporaryDirectory() as td:
        cache = os.path.join(td, STRUCTURAL_CACHE)
        case("讀不到快取 ⇒ 不亂說（空 list）", structural_warning(td) == [], structural_warning(td))
        os.makedirs(os.path.dirname(cache), exist_ok=True)
        with open(cache, "w", encoding="utf-8") as fh:
            json.dump({"mb": 11453.25, "peak_wired_mb": 10814.0}, fh)
        lines = structural_warning(td)
        case("本機真實數字（峰值 10814／上限 11453）⇒ 印出負餘裕並點出「填充落點」",
             bool(lines) and "餘裕" in lines[0] and any("落點" in x or "落在" in x for x in lines),
             lines)
        case("更正後的記帳：池子預算是宣告值、不是加項",
             any("宣告值" in x and "adopted" in x for x in lines), lines)
        with open(cache, "w", encoding="utf-8") as fh:
            json.dump({"mb": 40000.0, "peak_wired_mb": 10814.0}, fh)
        lines2 = structural_warning(td)
        case("上限 40000（換了盒子）⇒ 餘裕正、不喊邊緣",
             bool(lines2) and "+2" in lines2[0] and not any("邊緣" in x for x in lines2), lines2)
        with open(cache, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        case("快取壞掉 ⇒ 不拋、不亂說", structural_warning(td) == [], structural_warning(td))

    # 暖機是**opt-in** 且**不能進判詞**：預設沒有 W0；要了才有，而且產物在另一個路徑。
    base = [s["id"] for s in steps(dict(root=ROOT))]
    warm = [s["id"] for s in steps(dict(root=ROOT, warmup=True))]
    case("預設沒有暖機步（W0）", "W0" not in base and base[:1] == ["Q1"], base)
    case("--warmup ⇒ 在 Q1 之後多一步 W0", warm[:2] == ["Q1", "W0"] and "Q2" in warm, warm)
    case("暖機的產物不在成對的路徑上（判詞的 glob 取不到它）",
         WARMUP_OUT not in ["%s/clean_ws192_%s.json" % (PRODUCT_DIR, t) for t in ("A", "B")]
         and "/warmup/" in WARMUP_OUT, WARMUP_OUT)
    case("不認得的旗標 ⇒ 拒跑（不靜默吞）",
         _raises(lambda: ctx_factory(None, ["--warmup", "--nope"]))
         and ctx_factory(None, ["--warmup"]) == dict(warmup=True))

    # 孿生那一輪的兩條新規矩（2026-10-01）：① 命令要**由宣告帶出**（格名與 --reps 7 都在）；
    # ②「已完成」要以**這一輪的格名**為準，不是只看可引用（舊一輪的可引用產物不算答案）。
    cmd_a = _cmd_for(ROOT, "A")
    cmd_b = _cmd_for(ROOT, "B")
    case("A 臂命令帶孿生格與 --reps 7（命令由 cell_contract 帶出）",
         cmd_a[cmd_a.index("--cell") + 1] == ARM_CELLS["A"]
         and cmd_a[cmd_a.index("--reps") + 1] == "7",
         " ".join(cmd_a))
    case("B 臂命令同上（差別只在格名：gen 256／ws 192 逐字帶上）",
         cmd_b[cmd_b.index("--cell") + 1] == ARM_CELLS["B"]
         and cmd_b[cmd_b.index("--reps") + 1] == "7"
         and cmd_b[cmd_b.index("--gen") + 1] == "256"
         and cmd_b[cmd_b.index("--warm-skip") + 1] == "192",
         " ".join(cmd_b))
    case("格守門：舊格的產物不算這一輪完成（新格才算）",
         not _cell_guard("delivery", "A") and not _cell_guard(None, "B")
         and _cell_guard(ARM_CELLS["A"], "A") and _cell_guard(ARM_CELLS["B"], "B"))

    with tempfile.TemporaryDirectory() as td2:
        import glob as _glob
        pprev = os.path.join(td2, PRODUCT_DIR, "clean_ws192_verdict.json")
        os.makedirs(os.path.dirname(pprev), exist_ok=True)
        with open(pprev, "w", encoding="utf-8") as fh:
            json.dump({"verdict": "REFUSE", "why": "old"}, fh)
        verdict_step(td2, {})
        arch = _glob.glob(os.path.join(td2, LOG_DIR_REL, "archive", "Q4.*",
                                       "clean_ws192_verdict.json"))
        case("Q4 重寫判詞前先把上一輪判詞搬進 archive（不被覆蓋）",
             bool(arch) and os.path.exists(pprev), arch)
        case("Q4 現況字讀判詞本身（REFUSE ⇒ 未結，不是「已結」）",
             "REFUSE" in _q4_state(td2) and "未結" in _q4_state(td2), _q4_state(td2))

        # 重跑時的機器塊替換：標題外的舊「判詞：**…**」不得累積，人寫的敘事不得被踩掉。
        doc_p = os.path.join(td2, DOC_REL)
        with open(doc_p, "w", encoding="utf-8") as fh:
            fh.write("# L20-10 清窗成對（舊）\n\n判詞：**REFUSE**\n\n判詞：**REFUSE**\n\n"
                     "<!-- judge:begin -->\n```\nold\n```\n<!-- judge:end -->\n\n## 人寫的敘事\n")
        verdict_step(td2, {})
        with open(doc_p, encoding="utf-8") as fh:
            txt2 = fh.read()
        case("Q4 重寫不累積判詞行（連兩行殘留也只剩一行）且敘事保留",
             txt2.count("判詞：**") == 1 and "人寫的敘事" in txt2, txt2.count("判詞：**"))

    print("== l2010_window selftest %d/%d ==" % (good[0], good[1]))
    return 0 if good[0] == good[1] else 1


def _raises(fn) -> bool:
    try:
        fn()
    except SystemExit:
        return True
    except Exception:  # noqa: BLE001
        return True
    return False


def main(argv=None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if "--selftest" in args:
        return selftest()
    return WR.main(steps, preflight=preflight, journal=JOURNAL_REL, log_dir=LOG_DIR_REL,
                   title=TITLE, argv=args, ctx_factory=ctx_factory)


if __name__ == "__main__":
    sys.exit(main())
