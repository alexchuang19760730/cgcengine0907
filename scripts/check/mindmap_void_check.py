#!/usr/bin/env python3
"""機器檢查：mindmap 的「成績面」不得承載 **VOID 吞吐**。

WHY THIS EXISTS
---------------
2026-09-28 的一次實驗（交付步 `ntok=1` churn，`docs/PREMISE_B_CHURN_DELIVERY_2026-09-28.md`）
跑完之後，`docs/mindmap/mindmap.json` 的節點變成：

    res      = "decode 9.94 t/s（swap）"
    best     = {metric: tg, tg: 9.94, ...}
    target_gap.decode_tps = {current: 9.94, target: 20.0, gap: -10.06, pct: 49.7}

那個 9.94 是 **swap 歸因（`attribution.verdict=swap`）** 的讀數 —— 依 §5.1 不得引用。
於是**一個不可引用的吞吐數字**同時變成了節點的「成績」與「距目標 49.7%」。
`void_number_check.py` 掃不到它，因為那支只掃 `docs/*.md` 的決策面（14 檔），
而 mindmap 是**渲染面**（`index.html` ＋ 53 份 `briefs/*.md`）。

這跟本 session 抓到的其他六次是同一族病：**數字在決策面上比它的量具活得久**。
差別是這一次的載體是機器產生的成績欄，所以它可以、也應該被機械判定。

WHAT IT CHECKS
--------------
對 `entries[]` 的每個節點：

1. **有沒有吞吐主張**：`res` 裡的 `N t/s`、`best`（`metric`/`tg`/`pp`）、`target_gap.<axis>.current`。
   三者都沒有 ⇒ `N/A`（不是通過，是「這一節點不主張吞吐」）。
2. **有沒有出處可查**：有 `best` ⇒ 成績出處就是 `best.log`（寫入端只挑**乾淨**的 run 當 best；
   `runs[]` 是其餘歷史，各自在 brief 上帶判定，不是這條數字的出處）。沒有 `best`、或 `res`
   里的 t/s 與 best 的數字對不上 ⇒ 退回 `runs[].log` **全檢**（fail-closed）。
3. **那個產物是不是乾淨的**：讀出對應臂的 `attribution.verdict`（與 `thermal_worst`）。
   乾淨的定義沿用 `memory_pressure.attribute()` 的唯一乾淨標籤 **`none`**
   （`swap` / `both` / `thermal` / `contention` 都不乾淨）。
   臂的身分以 `best.arm` 的 tag 比對；只有單臂的產物可直接用；
   多臂又對不上 ⇒ `UNPROVEN`（**對不上不是乾淨**，這是 fail-closed 的那一半）。

VERDICT 階梯
------------
```
VOID-IN-RESULT   成績面上的 t/s 出自非乾淨歸因的產物（swap／both／thermal／contention）
VOID-UNBACKED    res 主張 t/s，但節點沒有任何產物可查（無 runs、無 best.log）
UNPROVEN         產物不存在／讀不到／臂身分不明 ⇒ 無法證明乾淨（fail-closed）
N/A              這個節點不主張吞吐（不是通過）
CLEAN            有主張、有出處、出處乾淨
EXEMPT           節點明文宣告 `no_throughput_claim`，或 `--allow <id>`
```

EXIT CODE
---------
`0` = 沒有未標記的 VOID；`1` = 至少一個 VOID/UNPROVEN；`2` = 用法／設定錯誤。
⚠ **今天 rc=1 是預期中的**（既有節點多數是在舊判定下寫的）。這支工具的目的跟
`claim_instrument_check.py` 一樣：讓那件事一直看得見，而不是回歸測試。
把它接到 CI 時要當成「報告」。

Usage:
    python3 scripts/check/mindmap_void_check.py
    python3 scripts/check/mindmap_void_check.py --allow exp-s-retro,exp-s2-overlap
    python3 scripts/check/mindmap_void_check.py --json /tmp/mindmap_void.json
    python3 scripts/check/mindmap_void_check.py --self-test
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MINDMAP = "docs/mindmap/mindmap.json"

# `res` 裡的吞吐主張：與 `mindmap_brief_build.big_number()` 同一個抓法（t/s 優先）。
TS_RE = re.compile(r"(\d+(?:\.\d+)?)\s*t/s")

# `memory_pressure.attribute()` 的乾淨標籤只有這一個。其他四個（swap／both／thermal／contention）
# 都要帶名字進來，因為「為什麼不乾淨」是引用時必須一起寫的那一半。
# 但**定義本身不住在這裡**：
# 寫入端 `experiment_sync._recompute_result` 用的也是 `memory_pressure.is_clean()` —— 兩邊共用一份，
# 否則「什麼叫乾淨」遲早漂移，而漂移那天就是這種病回來的時候（2026-09-28）。
CLEAN_VERDICTS = ("none",)

_CLEAN_FN = None
CLEAN_SOURCE = "未載入"


def clean_predicate():
    """(fn, source)。優先向 `memory_pressure.is_clean` 要判準；拿不到才退回本檔的等價實作，
    並把**用的是哪一份**報告出來（分岔看得見，不是靜默）。"""
    global _CLEAN_FN, CLEAN_SOURCE
    if _CLEAN_FN is not None or CLEAN_SOURCE != "未載入":
        return _CLEAN_FN, CLEAN_SOURCE
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        if here not in sys.path:
            sys.path.insert(0, here)
        import memory_pressure as _mp
        _CLEAN_FN, CLEAN_SOURCE = _mp.is_clean, "memory_pressure.is_clean()（寫入端用同一份）"
    except Exception as e:  # noqa: BLE001
        _CLEAN_FN, CLEAN_SOURCE = None, "本檔內建（memory_pressure.is_clean 不可用：%s）" % e
    return _CLEAN_FN, CLEAN_SOURCE

_INST_FN = None
INST_SOURCE = "未載入"


def instrument_predicate():
    """(fn, source)。量具綁定的判準向 `instrument_binding.is_bound` 要，**不在本檔重寫一份**，
    理由與 `clean_predicate()` 相同：兩邊各寫一遂就會漂移。

    貭不到那支工具 ⇒ 退回本檔的等價判（`BOUND`／`N/A` 通過）並把「用的是哪一份」報出來。
    """
    global _INST_FN, INST_SOURCE
    if _INST_FN is not None or INST_SOURCE != "未載入":
        return _INST_FN, INST_SOURCE
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        if here not in sys.path:
            sys.path.insert(0, here)
        import instrument_binding as _ib
        _INST_FN, INST_SOURCE = _ib.is_bound, "instrument_binding.is_bound()（寫入端用同一份）"
    except Exception as e:  # noqa: BLE001
        _INST_FN, INST_SOURCE = None, "本檔內建（instrument_binding.is_bound 不可用：%s）" % e
    return _INST_FN, INST_SOURCE


def pair_predicate():
    """回 `fn(path) -> (is_run, status)`；成對判準向 `instrument_binding.pair_status` 要，
    **不在本檔重寫一份**（否則遲早漂移）。拿不到模組 ⇒ 回 None（由呼叫端跳過這條，不静默放行也不誤殺）。
    """
    try:
        import instrument_binding as _ib

        def fn(p):
            is_run, _d, _w = _ib.load_run_artifact(p)
            return (True, _ib.pair_status(p)) if is_run else (False, {})
        return fn
    except Exception:  # noqa: BLE001
        return None


V_VOID_RESULT = "VOID-IN-RESULT"
V_VOID_UNBOUND = "VOID-INSTRUMENT-UNBOUND"
V_VOID_NO_PAIR = "VOID-NO-PAIR-LOG"
V_VOID_UNBACKED = "VOID-UNBACKED"
V_UNPROVEN = "UNPROVEN"
V_NA = "N/A"
V_CLEAN = "CLEAN"
V_EXEMPT = "EXEMPT"

# `VOID-INSTRUMENT-UNBOUND` 排在 `VOID-IN-RESULT` 後面：兩者都是「這個數字不能上成績面」，
# 但前者先說「歸因不乾淨」，因為那是引用時第一個要看的一半。
# 順序＝回報優先序。`VOID-IN-RESULT` 最先：它是從**產物自己的欄位**讀出來的（不必 log）。
# `VOID-NO-PAIR-LOG` 其次、`VOID-INSTRUMENT-UNBOUND` 最後：後者正是「拿那份 log 算的」——
# 沒有 log 時，那個問題根本無從回答。
ORDER = (V_VOID_RESULT, V_VOID_NO_PAIR, V_VOID_UNBOUND, V_VOID_UNBACKED, V_UNPROVEN,
         V_EXEMPT, V_CLEAN, V_NA)


# ───────────────────────── 純邏輯（不碰檔案之外的世界）─────────────────────────

def throughput_claims(entry: dict) -> list:
    """這個節點在哪些欄位主張了吞吐？回 [(kind, text)]。"""
    out = []
    res = entry.get("res")
    if isinstance(res, str):
        m = TS_RE.search(res)
        if m:
            out.append(("res", res.strip()))
    best = entry.get("best")
    if isinstance(best, dict):
        for key in ("tg", "pp"):
            v = best.get(key)
            if isinstance(v, (int, float)):
                # `metric` 說哪一個是它的第一指標；兩個都記，因為兩個都會被渲染出去。
                out.append(("best.%s" % key, "%s %s %s" % (key, v, best.get("arm") or "")))
    lb = entry.get("leaderboard")
    if isinstance(lb, dict):
        # 排行榜的 res 可能沒帶單位（「decode 最高 12.30」），但它的**欄位**就是一個吞吐主張。
        # 只看 res 的文字會讓這一類靜默繞過 —— 而它正是「把全語料最大值當成績」的那個載體。
        for axis in ("decode", "prefill"):
            d = lb.get(axis)
            if not isinstance(d, dict):
                continue
            for bucket in ("steady", "all"):
                lst = d.get(bucket)
                if isinstance(lst, list) and lst and isinstance(lst[0], dict) \
                        and isinstance(lst[0].get("v"), (int, float)):
                    out.append(("leaderboard.%s" % axis,
                                "%s 最高 %s（%s 榜首）" % (axis, lst[0]["v"], bucket)))
                    break
    gap = entry.get("target_gap")
    if isinstance(gap, dict):
        for axis, d in gap.items():
            if isinstance(d, dict) and isinstance(d.get("current"), (int, float)):
                cur = d["current"]
                out.append(("target_gap.%s" % axis, "%s = %s" % (axis, cur)))
    return out


def leaderboard_provenance(entry: dict) -> list:
    """排行榜節點的出處：`res` 報的是 steady 榜首（沒有 steady 才用 all），所以就是那一筆的 `src`。

    WHY：`score-leaderboard` 的 res 是一個 **t/s 主張**（「decode 最高 N」），但它既沒有
    `runs` 也沒有 `best.log`。不把這條路接上來，它就會被當成「不主張吞吐」而**靜默地**繞過檢查 ——
    而它正好是那種「把全語料的最大值寫成成績」的載體（2026-09-28 榜首曾是 prod25／MTP-on 的 13.099）。
    """
    lb = entry.get("leaderboard")
    if not isinstance(lb, dict):
        return []
    out = []
    for axis in ("decode", "prefill"):
        d = lb.get(axis)
        if not isinstance(d, dict):
            continue
        for bucket in ("steady", "all"):
            lst = d.get(bucket)
            if isinstance(lst, list) and lst and isinstance(lst[0], dict) and lst[0].get("src"):
                out.append((str(lst[0]["src"]), lst[0].get("arm")))
                break  # res 只報那一桶的榜首 ⇒ 只驗那一筆
    return out


def _best_only(entry: dict) -> bool:
    """成績面是不是「best 支撐」的形狀：有 `best.log`，且 `res` 里的 t/s（若有）與 best 的數字一致。

    寫入端 `experiment_sync._recompute_result`（2026-09-28 起）只從**乾淨**的 run 挑 best，
    再拿它重算 `res`／`target_gap` ⇒ 有 best 時，`runs[]` 是歷史、不是成績的出處。
    數字對不上（人寫的判詞帶了別的 t/s、或有人手改）⇒ 不採 best-only，退回 runs 全檢。
    """
    best = entry.get("best")
    if not (isinstance(best, dict) and best.get("log")):
        return False
    nums = [float(x) for x in TS_RE.findall(str(entry.get("res") or ""))]
    if not nums:
        return True
    bnum = best.get(best.get("metric") or "tg")
    if not isinstance(bnum, (int, float)):
        return False
    return all(abs(n - float(bnum)) <= 0.01 for n in nums)


def provenance(entry: dict) -> list:
    """回這個節點的**成績出處**（repo 相對路徑 ＋ 期望的臂 tag）。

    2026-10-01：有 best 且 res 對得上時，出處只有 `best.log`；`runs[]` 是歷史。舊語意把兩者
    混為一談 → 「有一份乾淨認證、但歷史裡有髒 run」的節點（`exp-singlesubmit-fillahead`）
    永遠判紅，而寫入端同一個節點已把 27.26 認成可引用讀數 —— 兩支工具互相矛盾。
    沒有 best（或數字對不上）⇒ 退回 runs 全檢，維持原本的 fail-closed 行為。
    """
    if _best_only(entry):
        best = entry["best"]
        return [(str(best["log"]), best.get("arm"))]
    out = []
    best = entry.get("best")
    if isinstance(best, dict) and best.get("log"):
        out.append((str(best["log"]), best.get("arm")))
    for run in entry.get("runs") or []:
        if isinstance(run, dict) and run.get("log"):
            out.append((str(run["log"]), run.get("arm")))
    if not out:
        out = leaderboard_provenance(entry)
    return out


def history_runs(entry: dict) -> list:
    """只為了「看得見」：best-only 路徑下被排除在出處之外、但仍在 runs[] 的歷史筆。"""
    if not _best_only(entry):
        return []
    return [r for r in (entry.get("runs") or []) if isinstance(r, dict) and r.get("log")]


def pick_arm(arms: list, tag) -> tuple:
    """(arm, err)。`tag` 有給就必須對上；只有單臂時可直接用；否則不明。"""
    dicts = [a for a in arms if isinstance(a, dict)]
    if not dicts:
        return None, "產物裡沒有 arm（每個元素都是非 dict）"
    if tag:
        hit = next((a for a in dicts if str(a.get("tag")) == str(tag)), None)
        if hit is None:
            return None, "產物裡沒有 tag=%r 的臂（有 %d 個臂）" % (tag, len(dicts))
        return hit, ""
    if len(dicts) == 1:
        return dicts[0], ""
    return None, "產物有 %d 個臂且沒有可比的 tag ⇒ 不知道這條成績出自哪一臂" % len(dicts)


def arm_binding(arm: dict) -> tuple:
    """(bound?, why, recorded?)。量具綁定。

    **只看產物自己記的判定**，不回頭由 env 與日誌推 —— 理由：這支工具是「成績面事後檢查器」，
    而回溯推導屬於寫入端（`experiment_sync`）。把推導放在這裡會讓同一個歷史產物在
    「檢查器眼中」和「寫入端眼中」不一樣脏（2026-09-29）。

    沒有 `instrument` 欄位（本規則之前的所有產物）⇒ `recorded=False`，不算髒：
    政策不回溯改寫語料；要清出來的是 `experiment_sync instrument-audit`（唯讀）那條路。
    """
    inst = arm.get("instrument")
    if not isinstance(inst, dict):
        return True, "", False
    fn, _src = instrument_predicate()
    if fn is not None:
        ok, why = fn(inst)
        return ok, why, True
    v = str(inst.get("verdict") or "").strip()
    if v in ("BOUND", "N/A"):
        return True, "instrument.verdict=%s" % v, True
    return False, "instrument.verdict=%s（%s）" % (v or "空", inst.get("why") or ""), True


def _window_worst(thermal_windows):
    """量測窗 worst（與 `memory_pressure.measured_worst()` 同義的本檔版，只在拿不到那支工具時用）。"""
    labels = []
    for w in thermal_windows or []:
        if not isinstance(w, dict):
            continue
        wr = w.get("worst")
        lab = (wr or {}).get("label") if isinstance(wr, dict) else None
        if lab:
            labels.append(str(lab))
    if not labels:
        return None
    order = {"NOMINAL": 0, "MODERATE": 1, "HEAVY": 2, "TRAPPING": 3, "SLEEPING": 4}
    return max(labels, key=lambda x: order.get(x, 99))


def arm_verdict(arm: dict) -> tuple:
    """(clean?, why)。缺 attribution 欄位 ⇒ 不乾淨（缺席不是乾淨）。

    判定本身走 `clean_predicate()`（= `memory_pressure.is_clean`，與寫入端同一份）；
    下面的本檔版只在那支工具拿不到時才用。

    [CGC 2026-10-03 線A · 熱口徑] 熱條件看**量測窗**（`arm['thermal_windows']`）而不是整臂 worst；
    理由見 `memory_pressure.measured_worst()`。寫入端（`experiment_sync`）與本檔必須同一口徑。
    """
    att = arm.get("attribution")
    if not isinstance(att, dict):
        return False, "產物沒有 attribution 欄位（缺席＝無法證明，不是乾淨）"
    fn, _src = clean_predicate()
    wins = arm.get("thermal_windows")
    if fn is not None:
        try:
            return fn(att, thermal_windows=wins)
        except TypeError:          # 較舊的 is_clean 沒有這個參數
            return fn(att)
    v = str(att.get("verdict") or "").strip()
    if not v:
        return False, "attribution.verdict 是空的"
    if v not in CLEAN_VERDICTS:
        why = "attribution.verdict=%s" % v
        if att.get("why"):
            why += "（%s）" % att["why"]
        return False, why
    th, src = _window_worst(wins), "量測窗"
    if th is None:
        th, src = att.get("thermal_worst"), "整臂(舊口徑)"
    if th is not None and str(th) != "NOMINAL":
        return False, "attribution.verdict=none 但 %s thermal=%s" % (src, th)
    return True, "attribution.verdict=none" + ("（%s thermal %s）" % (src, th) if th else "")


def load_arms(path: str, root: str) -> tuple:
    """(arms, err)。路徑相對 root；讀不到／不是 json ⇒ err。"""
    p = path if os.path.isabs(path) else os.path.join(root, path)
    if not os.path.exists(p):
        return None, "產物不存在：%s" % path
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
    except Exception as e:  # noqa: BLE001  讀不到不是通過
        return None, "產物讀不到／不是 json：%s（%s）" % (path, e)
    arms = d if isinstance(d, list) else [d]
    return arms, ""


def evaluate(entry: dict, root: str, allow: set) -> dict:
    """一個節點的判定。不碰全域狀態，方便 selftest 注入假樹。"""
    eid = str(entry.get("id") or "?")
    claims = throughput_claims(entry)
    rec = {"id": eid, "claims": claims, "verdict": V_NA, "why": "", "evidence": ""}

    if entry.get("no_throughput_claim"):
        rec["verdict"] = V_EXEMPT
        rec["why"] = "節點明文宣告 no_throughput_claim：%s" % entry["no_throughput_claim"]
        return rec
    if eid in allow:
        rec["verdict"] = V_EXEMPT
        rec["why"] = "以 --allow 點名豁免（會列印，不是靜默放寬）"
        return rec
    if not claims:
        rec["verdict"] = V_NA
        rec["why"] = "這個節點沒有任何吞吐主張（res 無 t/s、無 best、target_gap 無 current）"
        return rec

    prov = provenance(entry)
    if not prov:
        rec["verdict"] = V_VOID_UNBACKED
        rec["why"] = "主張了吞吐，但節點沒有任何產物可查（無 runs、無 best.log）"
        return rec

    errs = []
    seen_verdicts = []
    clean_ok = []
    unbound = []
    unpaired = []
    pair_fn = pair_predicate()
    for path, tag in prov:
        # 成對那一半（2026-09-29）：這一支產物旁邊有沒有它的 stderr log。
        # 缺 ⇒ 不可引用：沒有 log 時，「歸因乾不乾淨」「量具有沒有動」全部無從回答。
        # 只對「跑出來的產物」成立（結構判定），文件類產物不受影響。
        if pair_fn:
            fp = path if os.path.isabs(path) else os.path.join(root, path)
            try:
                is_run, st = pair_fn(fp)
            except Exception:  # noqa: BLE001
                is_run, st = False, {}
            if is_run and not st.get("paired"):
                unpaired.append((path, st.get("why") or "缺成對的 stderr log"))
        arms, err = load_arms(path, root)
        if err:
            errs.append(err)
            continue
        arm, err2 = pick_arm(arms, tag)
        if err2:
            errs.append("%s：%s" % (path, err2))
            continue
        clean, why = arm_verdict(arm)
        if clean:
            clean_ok.append("%s（%s）" % (path, why))
        else:
            seen_verdicts.append((path, why))
        bound, bwhy, recorded = arm_binding(arm)
        if recorded and not bound:
            unbound.append((path, bwhy))

    # **全部**主張的出處都必須乾淨，不是「有一個乾淨就算過」：res 可能同時報 decode 與 prefill
    # （排行榜就是這樣），一個數字乾淨不代表另一個也是。
    if not errs and not seen_verdicts and not unbound and not unpaired and clean_ok:
        rec["verdict"] = V_CLEAN
        rec["why"] = "所有出處都乾淨：%s" % "；".join(clean_ok)
        hist = history_runs(entry)
        if hist:
            rec["why"] += "；另有 %d 筆歷史 run 不列入出處（各自帶判定，見 runs）" % len(hist)
        rec["evidence"] = prov[0][0]
        return rec

    if seen_verdicts:
        rec["verdict"] = V_VOID_RESULT
        rec["why"] = "；".join("%s：%s" % (p, w) for p, w in seen_verdicts)
        rec["evidence"] = seen_verdicts[0][0]
        return rec

    # 產物旁邊沒有 log：這比「量具沒綁上」更前面 —— 沒有 log 就沒有東西可查。
    if unpaired:
        rec["verdict"] = V_VOID_NO_PAIR
        rec["why"] = "；".join("%s：%s" % (p, w) for p, w in unpaired)
        rec["evidence"] = unpaired[0][0]
        return rec

    # 歸因乾淨、但量具沒綁上：一個對的 t/s 配上一個死的量具，那個數字一樣不能被引用。
    if unbound:
        rec["verdict"] = V_VOID_UNBOUND
        rec["why"] = "；".join("%s：%s" % (p, w) for p, w in unbound)
        rec["evidence"] = unbound[0][0]
        return rec
    rec["verdict"] = V_UNPROVEN
    rec["why"] = "；".join(errs) or "沒有可判定的出處"
    return rec


def collect_entries(node, out=None) -> list:
    """`entries[]` 是節點；`subgoal_briefs` 底下也可能有，遞迴收 all dicts with an id."""
    out = [] if out is None else out
    if isinstance(node, dict):
        if node.get("id") and ("res" in node or "best" in node or "runs" in node):
            out.append(node)
        for v in node.values():
            collect_entries(v, out)
    elif isinstance(node, list):
        for v in node:
            collect_entries(v, out)
    return out


def run(mindmap_path: str, root: str, allow: set) -> tuple:
    with open(mindmap_path, encoding="utf-8") as f:
        data = json.load(f)
    recs = [evaluate(e, root, allow) for e in collect_entries(data)]
    return data.get("meta") or {}, recs


# ───────────────────────────────── selftest ─────────────────────────────────

def self_test() -> int:
    import tempfile
    import textwrap

    ok = True

    def expect(tag, got, want):
        nonlocal ok
        good = got == want
        ok &= good
        print("  [%s] %s: %r" % ("OK" if good else "FAIL", tag, got)
              + ("" if good else "   (want %r)" % (want,)))

    print("mindmap_void_check --self-test")
    d = tempfile.mkdtemp(prefix="mmvc_")
    try:
        os.makedirs(os.path.join(d, "Backup"))

        def art(name, arms):
            p = os.path.join(d, "Backup", name)
            with open(p, "w", encoding="utf-8") as f:
                json.dump(arms, f)
            return "Backup/" + name

        clean = art("clean.json", [dict(tag="prod-new", attribution=dict(
            verdict="none", why="thermal=NOMINAL", thermal_worst="NOMINAL"))])
        swp = art("swap.json", [dict(tag="prod-new", attribution=dict(
            verdict="swap", why="swap_growth=3877.69 MiB", thermal_worst="NOMINAL"))])
        both = art("both.json", [dict(tag="prod-new", attribution=dict(
            verdict="both", why="thermal=HEAVY, swap_growth=855.0 MiB",
            thermal_worst="HEAVY"))])
        conc = art("contention.json", [dict(tag="prod-new", attribution=dict(
            verdict="contention", why="llama_procs=2"))])
        noatt = art("noatt.json", [dict(tag="prod-new", rows=[])])
        two = art("two.json", [dict(tag="a", attribution=dict(verdict="none")),
                               dict(tag="b", attribution=dict(verdict="none"))])
        badjar = os.path.join(d, "Backup", "broken.json")
        with open(badjar, "w", encoding="utf-8") as f:
            f.write("{not json")
        clean_tag = art("clean_tag.json", [dict(tag="armA", attribution=dict(verdict="swap")),
                                           dict(tag="armB", attribution=dict(verdict="none"))])
        # 歸因乾淨、但量具沒綁上（2026-09-29 交付 cell 的形狀）
        unbound = art("unbound.json", [dict(tag="prod-new", attribution=dict(
            verdict="none", why="thermal=NOMINAL", thermal_worst="NOMINAL"),
            instrument=dict(verdict="UNBOUND",
                            why="武裝了 mm_pub_n_leaf，但全部是 0"))])
        # 同一個位置，但產物自己說量具綁上了 ⇒ 不得被判死（負向對照）
        bound = art("bound.json", [dict(tag="prod-new", attribution=dict(
            verdict="none", why="thermal=NOMINAL", thermal_worst="NOMINAL"),
            instrument=dict(verdict="BOUND", why="n_leaf=39、feeds=14000"))])
        # 沒武裝任何受檢量具（N/A）依然是乾淨
        na = art("na.json", [dict(tag="prod-new", attribution=dict(
            verdict="none", why="thermal=NOMINAL", thermal_worst="NOMINAL"),
            instrument=dict(verdict="N/A", why="未武裝"))])

        def entry(**kw):
            base = {"id": "n", "res": "decode 12.30 t/s"}
            base.update(kw)
            return base

        root = d
        allow = set()

        # 1) 乾淨產物 -> CLEAN
        expect("a clean artifact is CLEAN",
               evaluate(entry(runs=[dict(log=clean, arm="prod-new")]), root, allow)["verdict"], V_CLEAN)
        # 2) swap 歸因（就是 9.94 的形狀）-> VOID-IN-RESULT
        expect("a swap-attributed run is VOID-IN-RESULT",
               evaluate(entry(runs=[dict(log=swp, arm="prod-new")]), root, allow)["verdict"],
               V_VOID_RESULT)
        # 3) both/thermal 同理
        expect("a both/thermal run is VOID-IN-RESULT",
               evaluate(entry(runs=[dict(log=both, arm="prod-new")]), root, allow)["verdict"],
               V_VOID_RESULT)
        # 4) contention 不是乾淨
        expect("a contention run is VOID-IN-RESULT",
               evaluate(entry(runs=[dict(log=conc, arm="prod-new")]), root, allow)["verdict"],
               V_VOID_RESULT)
        # 5) 缺 attribution 欄位 = 缺席不是乾淨
        expect("a missing attribution field is VOID-IN-RESULT",
               evaluate(entry(runs=[dict(log=noatt, arm="prod-new")]), root, allow)["verdict"],
               V_VOID_RESULT)
        # 6) 有主張、沒有出處
        expect("a claim with no artifact is VOID-UNBACKED",
               evaluate(entry(), root, allow)["verdict"], V_VOID_UNBACKED)
        # 7) 產物不存在 -> UNPROVEN（不是 CLEAN）
        expect("a missing artifact file is UNPROVEN",
               evaluate(entry(runs=[dict(log="Backup/nope.json")]), root, allow)["verdict"], V_UNPROVEN)
        # 8) 產物不是 json -> UNPROVEN
        expect("an unreadable artifact is UNPROVEN",
               evaluate(entry(runs=[dict(log="Backup/broken.json")]), root, allow)["verdict"], V_UNPROVEN)
        # 9) 多臂又無可比 tag -> UNPROVEN（對不上不是乾淨）
        expect("an ambiguous multi-arm artifact is UNPROVEN",
               evaluate(entry(runs=[dict(log=two)]), root, allow)["verdict"], V_UNPROVEN)
        # 10) best.arm 的 tag 用來挑臂：挑到乾淨的那一臂
        expect("best.arm picks the arm by tag",
               evaluate(entry(res="", best=dict(metric="tg", tg=12.3, arm="armB", log=clean_tag)),
                        root, allow)["verdict"], V_CLEAN)
        # 11) 同一份多臂產物，tag 指到不乾淨的那一臂 -> VOID
        expect("... and picks the dirty one when the tag says so",
               evaluate(entry(res="", best=dict(metric="tg", tg=12.3, arm="armA", log=clean_tag)),
                        root, allow)["verdict"], V_VOID_RESULT)
        # 11b) 2026-10-01：成績出處＝best；runs[] 是歷史（寫入端只把乾淨的 run 放上 best）
        expect("a clean best wins over a dirty history run",
               evaluate(entry(best=dict(metric="tg", tg=12.3, log=clean),
                              runs=[dict(log=swp, arm="prod-new")]), root, allow)["verdict"], V_CLEAN)
        expect("... but with no best the dirty run still refuses the claim",
               evaluate(entry(runs=[dict(log=swp, arm="prod-new")]), root, allow)["verdict"],
               V_VOID_RESULT)
        expect("... and a res number that is not best's number falls back to the runs (fail-closed)",
               evaluate(entry(res="decode 9.94 t/s", best=dict(metric="tg", tg=12.3, log=clean),
                              runs=[dict(log=swp, arm="prod-new")]), root, allow)["verdict"],
               V_VOID_RESULT)
        expect("... and the why still shows the excluded history count",
               "歷史" in evaluate(entry(best=dict(metric="tg", tg=12.3, log=clean),
                                        runs=[dict(log=swp, arm="prod-new")]), root, allow)["why"], True)

        # 12) 沒有吞吐主張 -> N/A（不是通過）
        expect("a node with no throughput claim is N/A",
               evaluate({"id": "n", "res": "churn 3026/14976（不報吞吐）"}, root, allow)["verdict"], V_NA)
        # 13) target_gap.current 也算主張（9.94 讓 progress 變 49.7% 的那條路）
        expect("target_gap.current alone is a claim",
               evaluate({"id": "n", "res": "churn 不是 0",
                         "target_gap": {"decode_tps": {"current": 9.94, "target": 20.0}}},
                        root, allow)["verdict"], V_VOID_UNBACKED)
        # 14) 明文豁免（節點欄位）-> EXEMPT，且理由要印出來
        r = evaluate(entry(no_throughput_claim="只主張 churn 計數，不主張吞吐"), root, allow)
        expect("a declared exemption is EXEMPT (printed, not silent)",
               (r["verdict"], "no_throughput_claim" in r["why"]), (V_EXEMPT, True))
        # 15) --allow 點名豁免
        expect("--allow exempts by id",
               evaluate(entry(runs=[dict(log=swp, arm="prod-new")]), root, {"n"})["verdict"], V_EXEMPT)
        # 16) 排行榜節點：res 是 t/s 主張，出處是 leaderboard 榜首的 src（不能當成「不主張吞吐」）
        lb_node = {"id": "score-leaderboard",
                   "res": "排行榜更新（T）：decode 最高 12.30 t/s／prefill 最高 374.3 t/s",
                   "leaderboard": {"decode": {"steady": [{"v": 12.3, "arm": "prod-new", "src": clean}],
                                              "all": [{"v": 28.1, "arm": "prod-new", "src": swp}]},
                                   "prefill": {"steady": [{"v": 374.3, "arm": "prod-new", "src": swp}],
                                               "all": []}}}
        expect("a leaderboard node is judged on its headline's artifact, not skipped",
               evaluate(lb_node, root, allow)["verdict"], V_VOID_RESULT)
        lb_ok = {"id": "score-leaderboard", "res": "排行榜更新（T）：decode 最高 12.30 t/s",
                 "leaderboard": {"decode": {"steady": [{"v": 12.3, "arm": "prod-new", "src": clean}], "all": []}}}
        expect("... and passes when every headline artifact is clean",
               evaluate(lb_ok, root, allow)["verdict"], V_CLEAN)
        expect("res 沒帶 t/s、但有 leaderboard 欄位 ⇒ **仍然**是吞吐主張（不靜默繞過）",
               evaluate({"id": "score-leaderboard", "res": "排行榜更新（T）：decode 最高 12.30",
                         "leaderboard": {"decode": {"steady": [{"v": 12.3, "arm": "prod-new",
                                                                   "src": swp}], "all": []}}},
                        root, allow)["verdict"], V_VOID_RESULT)

        # 18) 量具沒綁上 → VOID-INSTRUMENT-UNBOUND（單獨一個標籤，不與 VOID-IN-RESULT 混）
        expect("an unbound instrument is VOID-INSTRUMENT-UNBOUND",
               evaluate(entry(runs=[dict(log=unbound, arm="prod-new")]), root, allow)["verdict"],
               V_VOID_UNBOUND)
        expect("... and the why names the probe that never moved",
               "mm_pub_n_leaf" in evaluate(entry(runs=[dict(log=unbound, arm="prod-new")]),
                                         root, allow)["why"], True)
        expect("a BOUND instrument stays CLEAN（負向對照）",
               evaluate(entry(runs=[dict(log=bound, arm="prod-new")]), root, allow)["verdict"], V_CLEAN)
        expect("instrument N/A（沒武裝）不算髒",
               evaluate(entry(runs=[dict(log=na, arm="prod-new")]), root, allow)["verdict"], V_CLEAN)
        expect("沒有 instrument 欄位的舊產物不得因政策而翻",
               evaluate(entry(runs=[dict(log=clean, arm="prod-new")]), root, allow)["verdict"], V_CLEAN)
        expect("歸因不乾淨優先於量具（引用時先看歸因）",
               evaluate(entry(runs=[dict(log=unbound, arm="prod-new"),
                                    dict(log=swp, arm="prod-new")]), root, allow)["verdict"],
               V_VOID_RESULT)

        # 19) 成對：跑出來的產物（有 rows）旁邊沒有 log ⇒ 不可引用。
        #     注意「跑的產物」是**結構判定**（tag ＋ rows/state_gate…），不是看檔名：
        #     上面那些 fixture（只有 tag＋attribution）不算跑的產物，所以不受這條影響。
        def _clean_arm(tag):
            return dict(tag=tag, rows=[{"n_prompt": 0, "n_gen": 64}],
                        attribution=dict(verdict="none", why="thermal=NOMINAL",
                                         thermal_worst="NOMINAL"))

        bare = art("bare.json", [_clean_arm("prod-new")])
        paired = art("paired.json", [_clean_arm("prod-new")])
        with open(os.path.join(d, "Backup", "paired.stderr.log"), "w", encoding="utf-8") as f:
            f.write("CGC-SPAC: feeds=1\n")
        expect("跑的產物缺成對 log ⇒ VOID-NO-PAIR-LOG",
               evaluate(entry(runs=[dict(log=bare, arm="prod-new")]), root, allow)["verdict"],
               V_VOID_NO_PAIR)
        expect("... 補上成對 log 後就 CLEAN（負向對照）",
               evaluate(entry(runs=[dict(log=paired, arm="prod-new")]), root, allow)["verdict"],
               V_CLEAN)
        expect("成對的 why 要說出「不可引用」而不只是「找不到檔」",
               "不可引用" in evaluate(entry(runs=[dict(log=bare, arm="prod-new")]),
                                    root, allow)["why"], True)

        # 17) 遞迴收集：entries 嵌在別處也找得到
        got = collect_entries({"entries": [{"id": "a", "res": "x"}],
                               "subgoal_briefs": {"S": [{"id": "b", "best": {"tg": 1.0}}]}})
        expect("collect_entries recurses", sorted(e["id"] for e in got), ["a", "b"])
    finally:
        import shutil
        shutil.rmtree(d, ignore_errors=True)

    print("SELFTEST %s" % ("ALL PASS" if ok else "FAILED"))
    return 0 if ok else 1


# ─────────────────────────────────── main ───────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mindmap", default=MINDMAP)
    ap.add_argument("--root", default=ROOT)
    ap.add_argument("--allow", default="",
                    help="以逗號分隔的節點 id：**點名**豁免（會列印）。用來把既有節點逐條結清，"
                         "而不是靜默放寬。")
    ap.add_argument("--json", dest="json_out")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        return self_test()

    allow = {x.strip() for x in args.allow.split(",") if x.strip()}
    path = args.mindmap if os.path.isabs(args.mindmap) else os.path.join(args.root, args.mindmap)
    if not os.path.exists(path):
        print("!! 找不到 %s" % path)
        return 2
    meta, recs = run(path, args.root, allow)

    print("mindmap 成績面：VOID 吞吐檢查")
    print("  來源：%s" % os.path.relpath(path, args.root))
    print("  規則：%s" % (meta.get("rule") or "(meta.rule 未宣告)"))
    print("  乾淨的定義：%s（標籤集 %s）" % (clean_predicate()[1], list(CLEAN_VERDICTS)))
    print("  量具綁定的定義：%s" % instrument_predicate()[1])
    if allow:
        print("  --allow（點名豁免）：%s" % ", ".join(sorted(allow)))
    print("")

    counts = {}
    for v in ORDER:
        counts[v] = 0
    rows = []
    for r in recs:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
        if r["verdict"] in (V_VOID_RESULT, V_VOID_NO_PAIR, V_VOID_UNBOUND, V_VOID_UNBACKED,
                             V_UNPROVEN, V_EXEMPT):
            rows.append(r)

    for rec in sorted(rows, key=lambda x: (ORDER.index(x["verdict"]), x["id"])):
        mark = {"VOID-IN-RESULT": "⛔", "VOID-NO-PAIR-LOG": "⛔", "VOID-INSTRUMENT-UNBOUND": "⛔",
                "VOID-UNBACKED": "⛔", "UNPROVEN": "⚠", "EXEMPT": "·"}[rec["verdict"]]
        claim = rec["claims"][0][1] if rec["claims"] else ""
        print("  %s %-16s %-26s %s" % (mark, rec["verdict"], rec["id"], (claim or "")[:48]))
        if rec["why"]:
            print("       %s" % rec["why"][:200])

    bad = (counts[V_VOID_RESULT] + counts[V_VOID_NO_PAIR] + counts[V_VOID_UNBOUND]
           + counts[V_VOID_UNBACKED] + counts[V_UNPROVEN])
    print("")
    print("  小計：" + "　".join("%s=%d" % (v, counts[v]) for v in ORDER if counts[v]))
    print("VERDICT: %d 節點不合規 / %d 已檢（CLEAN=%d、N/A=%d、EXEMPT=%d）   rc=%d"
          % (bad, len(recs), counts[V_CLEAN], counts[V_NA], counts[V_EXEMPT], 1 if bad else 0))
    if counts[V_VOID_NO_PAIR]:
        print("⚠ VOID-NO-PAIR-LOG：那幾支產物旁邊沒有它的 stderr log ⇒ 不可引用。"
              "修法：`scripts/check/instrument_binding.py --pairs 'Backup/**/*.json'`（唯讀）看哪裡有缺，"
              "再 `--pair-backfill <artifact.json> --from <那份 log>` 把**真的**那一份登記回來。")
    if bad:
        print("⚠ 這不是說那些現象不存在，是說那個**吞吐數字**不能這樣放在成績面上。"
              "要嘛補一條乾淨歸因的產物，要嘛把 res 改成不主張吞吐（節點可宣告 no_throughput_claim），"
              "要嘛 --allow 點名結清。")

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump({"counts": counts, "records": recs}, f, indent=2, ensure_ascii=False)
        print("  json -> %s" % args.json_out)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
