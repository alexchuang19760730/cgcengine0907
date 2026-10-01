#!/usr/bin/env python3
"""前置閘門 runnable_gate：**「這一格要的判決是不是已經存在」**，在宣告可跑之前先問一次。

為什麼需要它（2026-09-30 §64，L25-6 的實例）：L25-6 的看板一直寫「✅ 現在能跑 — 前置＝加大配對 n」，
而它要的那個判決（事先登記的 N=12 配對檢定）**兩個晚上前就跑完並留在樹上**。差一點就用 ~40 趟
launch 重跑一個已經存在的答案。人工記得「先掃產物」不是流程，閘門才是流程。

語意（`precondition.rerun`，三選一，機器判）：
  needed    這一趟會產出**新資訊** ⇒ 必須證明目標判決**還不存在**（真掃描 ＋ expect=verdict_absent）。
  duplicate 這一趟只會**重印**一個已存在的判決 ⇒ 必須指名那個判決（artifact）且它必須被掃到。
  blocked   跑不了的原因**不是判決**（缺機制、缺決策、缺窗口、缺程式改動）⇒ 可以 kind=none，理由寫在 why。

判詞：EXISTS / ABSENT / CANNOT_JUDGE。掃不了 ⇒ CANNOT_JUDGE（fail-closed，板子會紅，不會靜靜放行）。

掃描器（kind）：
  verdict_file       依 glob 找檔，可再要求內容符合 contains（先正規式、再字面；見 `_contains`）。
                     回傳命中檔清單。
  quotable_reading   依 glob 找產物，篩出「指定 cell 且逐 rep 達 min_ts 的 decode 列」，
                     再用 quote_gate 當場判它們可不可引用。回傳可引用的那一列。
  none               不掃（只給 blocked／已了結的格用；needed 不准用）。

用法：
    python3 scripts/check/runnable_gate.py --yaml scripts/check/decode_board_2026-09-29.yaml
    python3 scripts/check/runnable_gate.py --selftest
"""
import argparse
import glob
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))

KINDS = ("verdict_file", "quotable_reading", "none")
RERUN = ("needed", "duplicate", "blocked")
EXISTS, ABSENT, CANNOT = "EXISTS", "ABSENT", "CANNOT_JUDGE"

# `blocked` 要說得出**卡在哪一型**（2026-09-30 §66：形別化的決策佇列）。
# 定義只在這裡一份；看板的 D13 與頁首的佇列都取用。
BLOCK_KINDS = ("decision", "code", "mechanism", "window")
BLOCK_OWNERS = ("operator", "engine", "box", "harness")
BLOCK_LABELS = {
    "decision":  "缺一個決定",
    "code":      "缺原始碼／腳本改動",
    "mechanism": "缺一個新機制",
    "window":    "缺一個可引用窗口",
}
# 佇列的排序：決定（最便宜、解鎖最多）→ 改動 → 機制 → 等盒子
BLOCK_ORDER = {k: i for i, k in enumerate(BLOCK_KINDS)}

# 卡點的**可掃描斷言**（2026-09-30 §67）：`blocked` 的敘述原本是手寫散文，會像 §65 的宣告一樣過期。
# 每一個 `block.assert` 問的是同一件事：**這個卡點現在還在嗎**。
#   present_in_file  卡點的證據字串**還在**檔案裡 ⇒ 卡點仍在（例：餵料確實還掛在那個閘下）
#   absent_in_file   卡點的解方字串**還沒出現** ⇒ 卡點仍在（例：launcher 還沒轉送那個旗標）
#   none             樹上掃不到 ⇒ 必須把理由寫在 why（頁首會把這幾格列出來，不讓它們靜靜被信任）
BLOCK_CHECKS = ("present_in_file", "absent_in_file", "none")
BLOCK_HELD, BLOCK_GONE, BLOCK_UNSCANNED = "HELD", "GONE", "UNSCANNED"
SCAN_KINDS = ("verdict_file", "quotable_reading")   # needed 只准用這兩種


def _paths(root, pattern):
    return sorted(p for p in glob.glob(os.path.join(root, pattern), recursive=True)
                  if os.path.isfile(p))


def _contains(pat, txt):
    """`contains` 的語意是**這個字串在不在**；正規式只是它的實作，不是它的意思。

    為什麼不能只有 `re.search(pat, txt)`（2026-09-30 §68，λ 的實例）：`contains` 幾乎都是**貼進來的
    程式碼片段**，而片段裡的 `(`／`)`／`[`／`+` 在正規式裡是語法 —— `if (cgc_rho_probe && … == 0)`
    這條描述的字串其實是 `ifcgc_rho_probe…`（括號被吃掉）⇒ 它對原始碼**永遠不命中**：
    `present_in_file` 會誤判「卡點已解」而紅（狼來了），`absent_in_file` 會永遠綠 ⇒ 解方落地了也
    看不出來（fail-open，這一型的月牙比狼來了貴）。這裡先正規式、再字面：白名單只會多出
    「字面在檔案裡」這種命中，不會把真命中變假；正規式自己寫壞（re.error）不當命中。
    """
    try:
        if re.search(pat, txt):
            return True
    except re.error:
        pass
    return pat in txt


def scan_verdict_file(spec, root):
    """判決／分析檔是否存在（可要求內容符合 contains）。"""
    pat = spec.get("glob")
    if not pat:
        return CANNOT, "spec 缺 glob", []
    pats = [pat] if isinstance(pat, str) else list(pat)
    want = spec.get("contains")
    hits = []
    for p in pats:
        for f in _paths(root, p):
            if want:
                try:
                    with open(f, errors="ignore") as fh:
                        txt = fh.read(400000)
                except OSError:
                    continue
                if not _contains(want, txt):
                    continue
            hits.append(os.path.relpath(f, root))
    if hits:
        return EXISTS, "命中：%s" % "、".join(hits[:3]), hits
    return ABSENT, "沒有檔案符合 %s%s" % ("、".join(pats), (" 且含 %r" % want) if want else ""), []


def scan_quotable_reading(spec, root):
    """已經有一列**可引用**的讀數符合這一格的條件嗎（cell ＋ 逐 rep ≥ min_ts）？"""
    pat = spec.get("glob")
    if not pat:
        return CANNOT, "spec 缺 glob", []
    min_ts = float(spec.get("min_ts") or 0.0)
    cell = spec.get("cell")
    cands = []
    for f in _paths(root, pat):
        if not f.endswith(".json"):
            continue
        try:
            with open(f, encoding="utf-8") as fh:
                doc = json.load(fh)
        except Exception:  # noqa: BLE001
            continue
        for prod in (doc if isinstance(doc, list) else [doc]):
            if not isinstance(prod, dict):
                continue
            _c = prod.get("cell")
            named = _c.get("named_cell") if isinstance(_c, dict) else (str(_c) if isinstance(_c, str) else None)
            if cell and named != cell:
                continue
            best = None
            for row in (prod.get("rows") or []):
                if not isinstance(row, dict):
                    continue
                # 只認 decode 列（n_gen>0）：pp 列是同一張表上另一個函數（§56）
                try:
                    ngen = int(str(row.get("n_gen") or 0) or 0)
                except ValueError:
                    ngen = 0
                if ngen <= 0:
                    continue
                ts = row.get("avg_ts")
                if ts is None or float(ts) < min_ts:
                    continue
                best = max(best or 0.0, float(ts))
            if best is not None:
                cands.append((best, os.path.relpath(f, root)))
    if not cands:
        return ABSENT, "沒有 cell=%s 且 decode ≥%.4g t/s 的列" % (cell, min_ts), []
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    try:
        import quote_gate
    except Exception as exc:  # noqa: BLE001
        return CANNOT, "載不進 quote_gate：%s" % exc, []
    try:
        recs = quote_gate.scan([os.path.join(root, f) for _, f in cands])
    except Exception as exc:  # noqa: BLE001
        return CANNOT, "quote_gate 判不了：%s: %s" % (type(exc).__name__, exc), []
    hits, detail = [], []
    for r in recs:
        if r.get("verdict") != "QUOTABLE":
            continue
        m = r.get("metrics") or {}
        avg = m.get("avg")
        if avg is None or float(avg) < min_ts:
            continue
        if cell and r.get("cell") != cell:
            continue
        hits.append(os.path.relpath(r["file"], root))
        detail.append("%s（%.3f t/s、逐 rep %s）"
                      % (os.path.relpath(r["file"], root), float(avg),
                         [round(v, 2) for v in (m.get("samples") or [])]))
    if hits:
        return EXISTS, "已可引用：%s" % "；".join(detail[:3]), hits
    return ABSENT, "%d 個候選（cell=%s、≥%.4g）全部**不可引用**" % (len(cands), cell, min_ts), []


SCANS = {"verdict_file": scan_verdict_file, "quotable_reading": scan_quotable_reading}


def spec_list(pre):
    """`precondition.spec` 可為單一 dict，或**一串** dict（兩根獨立掃描用）⇒ 正規化成 list。"""
    spec = (pre or {}).get("spec")
    if spec is None:
        return []
    return list(spec) if isinstance(spec, list) else [spec]


def spec_globs(spec):
    """一個 spec 裡的 glob 樣式（`glob` 單一，或 `globs` 一串）。"""
    g = (spec or {}).get("glob", (spec or {}).get("globs"))
    if g is None:
        return []
    return [g] if isinstance(g, str) else [str(x) for x in g]


def _scan_one(kind, spec, root, cache):
    key = (kind, json.dumps(spec, sort_keys=True, ensure_ascii=False))
    if cache is not None and key in cache:
        return cache[key]
    try:
        out = SCANS[kind](spec, root)
    except Exception as exc:  # noqa: BLE001
        out = (CANNOT, "掃描器爆掉：%s: %s" % (type(exc).__name__, exc), [])
    if cache is not None:
        cache[key] = out
    return out


def scan(pre, root=None, cache=None):
    """跑一個 precondition ⇒ (verdict, why, hits)。cache 是 dict（同一輪 build 內共用）。

    `spec` 可以是**一串** spec ⇒ 逐一掃，再合併：任何一個 EXISTS ⇒ EXISTS（判決已在樹上）；
    全部 ABSENT ⇒ ABSENT；有 CANNOT_JUDGE 且沒有 EXISTS ⇒ CANNOT_JUDGE（fail-closed）。

    為什麼要有「一串」（2026-09-30 §65）：L20-9 的宣告只掃了一個**手寫猜的**目錄
    （`Backup/ceiling_*/**`，不存在），於是閘門判 `needed`——而它要的判決其實早就
    寫成文件（`docs/DENSE_NSG_RESCAN_2026-09-23.md`）。單一根的猜測會生出**假的 needed**；
    兩個獨立根 ＋ 其中一個必須是 `docs/` 是機械防線。
    """
    root = root or ROOT
    kind = (pre or {}).get("kind")
    if kind == "none":
        return ABSENT, "kind=none：不掃（見 why）", []
    if kind not in SCANS:
        return CANNOT, "kind 不認得：%r（已知：%s）" % (kind, "、".join(KINDS)), []
    specs = spec_list(pre)
    if not specs or not all(isinstance(x, dict) for x in specs):
        return CANNOT, "缺 spec（%s 需要參數；可為 dict 或 dict 的清單）" % kind, []
    outs = [_scan_one(kind, sp, root, cache) for sp in specs]
    if len(outs) == 1:
        return outs[0]
    hits, seen = [], set()
    for _v, _w, hs in outs:
        for h in hs:
            if h not in seen:
                seen.add(h); hits.append(h)
    if any(v == EXISTS for v, _w, _h in outs):
        return EXISTS, "命中：%s" % "、".join(hits[:3]), hits
    if all(v == ABSENT for v, _w, _h in outs):
        return ABSENT, "全部 %d 個掃描都沒命中（%s）" % (len(outs), " ／ ".join(w for _v, w, _h in outs)), []
    return CANNOT, "有一支掃描判不了（%s）—— fail-closed" % " ／ ".join(
        w for v, w, _h in outs if v == CANNOT), []


def scan_block_assert(blk, root=None, cache=None):
    """判一個 `block.assert` ⇒ (verdict, why, hits)。verdict ∈ HELD／GONE／CANNOT_JUDGE／UNSCANNED。

    判準只在這裡一份：**HELD ＝ 卡點還在（正常）**；**GONE ＝ 卡點已經不在樹上（要紅）**；
    掃不了（glob 沒命中檔案、缺 contains）＝ CANNOT_JUDGE（fail-closed）。
    """
    root = root or ROOT
    a = (blk or {}).get("assert")
    if not isinstance(a, dict):
        return CANNOT, "缺 block.assert（卡點是不是還在，要用什麼掃）", []
    kind = a.get("kind")
    if kind not in BLOCK_CHECKS:
        return CANNOT, "block.assert.kind=%r 不認得（%s）" % (kind, "／".join(BLOCK_CHECKS)), []
    if kind == "none":
        return BLOCK_UNSCANNED, "樹上沒有可掃的斷言（理由見 block.assert.why）", []
    pat, globpat = a.get("contains"), a.get("glob")
    if not pat or not globpat:
        return CANNOT, "block.assert 需要 glob 與 contains", []
    key = ("block", kind, globpat, pat)
    if cache is not None and key in cache:
        return cache[key]
    files = _paths(root, globpat)
    if not files:
        out = (CANNOT, "glob 沒命中任何檔案：%s（檔案被搬走／改名 ⇒ 判不了）" % globpat, [])
    else:
        found = []
        for f in files:
            try:
                with open(f, errors="ignore") as fh:
                    txt = fh.read(400000)
            except OSError:
                continue
            if _contains(pat, txt):
                found.append(os.path.relpath(f, root))
        if kind == "present_in_file":
            out = ((BLOCK_HELD, "卡點還在：<code>%s</code> 命中 %s" % (pat, "、".join(found[:2])), found)
                   if found else
                   (BLOCK_GONE, "<code>%s</code> 已不再命中（卡點可能已解）" % pat, []))
        else:
            out = ((BLOCK_GONE, "<code>%s</code> 出現在 %s ⇒ 卡點已解" % (pat, "、".join(found[:2])), found)
                   if found else
                   (BLOCK_HELD, "卡點還在：<code>%s</code> 未出現於 %s" % (pat, globpat), []))
    if cache is not None:
        cache[key] = out
    return out


def check_target(t, root=None, cache=None):
    """判一個子目標的 precondition ⇒ dict(ok, errs, live, why, hits)。板子的 D12 與 CLI 共用。"""
    root = root or ROOT
    tid = t.get("id")
    pre = t.get("precondition")
    if not isinstance(pre, dict):
        return dict(ok=False, live=CANNOT, why="", hits=[],
                    errs=["D12 %s 缺 precondition —— 宣告可跑（或不用跑）之前，先回答"
                          "『這一格要的判決是不是已經存在』" % tid])
    errs = []
    rerun, kind = pre.get("rerun"), pre.get("kind")
    if rerun not in RERUN:
        errs.append("D12 %s 的 precondition.rerun=%r 不合法（%s）" % (tid, rerun, "／".join(RERUN)))
    if kind not in KINDS:
        errs.append("D12 %s 的 precondition.kind=%r 不認得（%s）" % (tid, kind, "／".join(KINDS)))
    if not str(pre.get("why") or "").strip():
        errs.append("D12 %s 的 precondition 缺 why（為什麼是這個 rerun）" % tid)
    live, why, hits = scan(pre, root, cache)
    expect = pre.get("expect")
    if kind in SCAN_KINDS:
        if expect not in ("verdict_exists", "verdict_absent"):
            errs.append("D12 %s 有真掃描卻缺 expect（verdict_exists／verdict_absent）" % tid)
        elif live == CANNOT:
            errs.append("D12 %s 的前置判不了（%s）—— fail-closed，不放行" % (tid, why))
        elif (live == EXISTS) != (expect == "verdict_exists"):
            errs.append("D12 %s 宣告 expect=%s，但現判 %s（%s）" % (tid, expect, live, why))
    if rerun == "needed":
        _specs = spec_list(pre)
        _globs = [g for sp in _specs for g in spec_globs(sp)]
        if len(_specs) < 2:
            errs.append("D12 %s 宣告 rerun=needed 但只掃一個根 ⇒「這個判決還不存在」必須用"
                        "**兩個獨立根**掃（其中一個必須是 docs/）：手寫的單一根猜錯目錄就會生出"
                        "假的 needed（2026-09-30 L20-9 的實例）" % tid)
        if _globs and not any(g.startswith("docs/") for g in _globs):
            errs.append("D12 %s 的 needed 掃描沒有涵蓋 docs/** ⇒ 判決常被寫成文件"
                        "（L20-9 的判決就在 docs/DENSE_NSG_RESCAN_2026-09-23.md，而宣告只掃了 Backup/）"
                        % tid)
    if rerun == "needed":
        if kind not in SCAN_KINDS:
            errs.append("D12 %s 宣告 rerun=needed（這一趟會有新資訊）卻沒有真掃描 ⇒ "
                        "沒人能證明那個判決還不存在" % tid)
        elif live != ABSENT:
            errs.append("D12 %s 宣告 rerun=needed 但目標判決**已經存在**（%s）⇒ 這一趟只會重印"
                        "（改 duplicate／blocked）" % (tid, why))
    if rerun == "blocked":
        blk = pre.get("block")
        if not isinstance(blk, dict):
            errs.append("D13 %s 宣告 rerun=blocked 卻沒說**卡在哪一型**"
                        "（precondition.block{kind,owner,next}）⇒ 決策佇列排不出來" % tid)
        else:
            bk, ow = blk.get("kind"), blk.get("owner")
            if bk not in BLOCK_KINDS:
                errs.append("D13 %s 的 block.kind=%r 不合法（%s）"
                            % (tid, bk, "／".join(BLOCK_KINDS)))
            if ow not in BLOCK_OWNERS:
                errs.append("D13 %s 的 block.owner=%r 不合法（%s）"
                            % (tid, ow, "／".join(BLOCK_OWNERS)))
            if not str(blk.get("next") or "").strip():
                errs.append("D13 %s 的 block 缺 next（下一個動作是什麼）" % tid)
            # D14（§67）：卡點的敘述必須是**可掃描的斷言**，而且**當場重掃**。
            bl, bwhy, _bhits = scan_block_assert(blk, root, cache)
            if bl == CANNOT:
                errs.append("D14 %s 的卡點判不了（%s）—— fail-closed，不放行" % (tid, bwhy))
            elif bl == BLOCK_GONE:
                errs.append("D14 %s 宣告的卡點**已經不在樹上**（%s）⇒ 這一格該改成可跑（needed）"
                            "或結案，不該還排在佇列裡" % (tid, bwhy))
            elif bl == BLOCK_UNSCANNED and not str(
                    ((blk.get("assert") or {}).get("why")) or "").strip():
                errs.append("D14 %s 的 block.assert 是 none（樹上掃不到）卻沒寫 why" % tid)
    if rerun == "duplicate":
        art = pre.get("artifact")
        if not art:
            errs.append("D12 %s 宣告 rerun=duplicate 卻沒指名 artifact（那個判決在哪）" % tid)
        elif not os.path.exists(os.path.join(root, art)):
            errs.append("D12 %s 指名的判決不存在：%s" % (tid, art))
        elif hits and all(os.path.normpath(art) != os.path.normpath(h) for h in hits):
            errs.append("D12 %s 指名的 artifact 不在掃描命中清單裡：%s" % (tid, art))
        if live != EXISTS:
            errs.append("D12 %s 宣告 rerun=duplicate 但掃不到那個判決（%s）" % (tid, why))
        st = str(t.get("state") or "")
        if not (st.startswith("結案") or t.get("settled")):
            errs.append("D12 %s 的目標判決已經存在（%s）但 state 仍不是結案 ⇒ "
                        "看板會一直叫人去跑一個已經有答案的東西" % (tid, why))
    # 看得見的標籤必須與機器狀態一致：『✅』＝這一趟是 needed
    runnable = str(t.get("runnable") or "")
    if runnable.startswith("✅") and rerun != "needed":
        errs.append("D12 %s 的 runnable 以『✅』開頭（可跑）但 rerun=%r ⇒ 標籤與機器狀態不一致"
                    % (tid, rerun))
    if rerun == "needed" and not runnable.startswith("✅"):
        errs.append("D12 %s 宣告 rerun=needed 但 runnable 沒有以『✅』開頭" % tid)
    return dict(ok=not errs, errs=errs, live=live, why=why, hits=hits)


def check_board(yaml_path, root=None):
    """掃整張看板（逐子目標）⇒ (rows, errs)。rows 給報表用，errs 給 D12 用。"""
    import yaml
    root = root or ROOT
    with open(yaml_path, encoding="utf-8") as fh:
        board = yaml.safe_load(fh)
    rows, errs, cache = [], [], {}
    for layer in (board.get("layers") or []):
        for t in (layer.get("targets") or []):
            res = check_target(t, root, cache)
            pre = t.get("precondition") or {}
            rows.append(dict(id=t.get("id"), layer=layer.get("id"), rerun=pre.get("rerun"),
                             kind=pre.get("kind"), expect=pre.get("expect"), live=res["live"],
                             ok=res["ok"], why=res["why"], hits=res["hits"],
                             artifact=pre.get("artifact")))
            errs.extend(res["errs"])
    return rows, errs


def report(rows, errs):
    print("前置閘門 runnable_gate：**這一格要的判決是不是已經存在**（宣告可跑之前先問）")
    print("  rerun=needed（會有新資訊）｜duplicate（只會重印已存在的判決）｜blocked（跑不了的原因不是判決）")
    print()
    print("  %-7s %-10s %-16s %-15s %-12s %s" % ("id", "rerun", "kind", "expect", "現判", "ok"))
    for r in rows:
        print("  %-7s %-10s %-16s %-15s %-12s %s"
              % (r["id"], r["rerun"], r["kind"], r["expect"], r["live"], "✅" if r["ok"] else "⛔"))
    print()
    n_needed = sum(1 for r in rows if r["rerun"] == "needed")
    n_dup = sum(1 for r in rows if r["rerun"] == "duplicate")
    n_blk = sum(1 for r in rows if r["rerun"] == "blocked")
    print("  口徑：needed %d／duplicate %d／blocked %d（共 %d 格）" % (n_needed, n_dup, n_blk, len(rows)))
    for r in rows:
        if r["rerun"] == "duplicate" and r["live"] == EXISTS:
            print("  · %s：判決已存在（%s）⇒ 不必再跑" % (r["id"], r["artifact"]))
        if r["rerun"] == "needed" and r["live"] == ABSENT:
            print("  · %s：判決不存在（%s）⇒ 這一趟有價值" % (r["id"], r["why"][:110]))
    print()
    print("VERDICT: %s（%d 個問題）" % ("PASS" if not errs else "FAIL", len(errs)))
    for e in errs:
        print("  ✗ %s" % e)
    return 0 if not errs else 1


def selftest():
    import shutil
    import tempfile
    ok = [0, 0]

    def case(name, cond, detail=""):
        ok[1] += 1
        ok[0] += 1 if cond else 0
        print("  %-52s -> %s%s" % (name, "PASS" if cond else "FAIL", "" if cond else " " + str(detail)))

    print("runnable_gate --selftest")
    d = tempfile.mkdtemp()
    try:
        # --- 掃描器 ---
        os.makedirs(os.path.join(d, "Backup", "pair_2026-09-28"))
        vf = os.path.join(d, "Backup", "pair_2026-09-28", "verdict_fcrit_2.22.txt")
        with open(vf, "w") as fh:
            fh.write("n=12 pairs  mean diff +0.54 t/s  VERDICT: NOT SEPARATED\n")
        v, why, hits = scan({"kind": "verdict_file",
                             "spec": {"glob": "Backup/pair_*/verdict_*.txt", "contains": "VERDICT"}}, d)
        case("verdict_file：命中（含 contains）⇒ EXISTS", v == EXISTS, why)
        case("verdict_file：命中清單是**相對路徑**", hits == ["Backup/pair_2026-09-28/verdict_fcrit_2.22.txt"], hits)
        v, why, _ = scan({"kind": "verdict_file",
                          "spec": {"glob": "Backup/pair_*/verdict_*.txt", "contains": "CERTIFIED"}}, d)
        case("verdict_file：contains 不符 ⇒ ABSENT", v == ABSENT, why)
        v, why, _ = scan({"kind": "verdict_file", "spec": {"glob": "Backup/nope_*/x.txt"}}, d)
        case("verdict_file：glob 沒命中 ⇒ ABSENT", v == ABSENT, why)
        # contains＝「字串在不在」：片段含 `(`／`)` 等語法字元時，re.search 會把它們當語法
        # （`if (x)` 描述的是 `ifx`）⇒ 永遠不命中。走字面要照樣命中。
        os.makedirs(os.path.join(d, "Backup", "rho_snippet"))
        with open(os.path.join(d, "Backup", "rho_snippet", "gate.txt"), "w") as fh:
            fh.write('    if (cgc_rho_probe && !ask && strncmp(t->name, "cgc_rho_logits", 14) == 0) {\n')
        SNIP = 'if (cgc_rho_probe && !ask && strncmp(t->name, "cgc_rho_logits", 14) == 0)'
        v, why, _ = scan({"kind": "verdict_file",
                          "spec": {"glob": "Backup/rho_snippet/*.txt", "contains": SNIP}}, d)
        case("verdict_file：含括號的片段 ⇒ 字面仍命中", v == EXISTS, why)
        q1 = os.path.join(d, "Backup", "anchor_q.json")
        # R7（2026-09-30）：真產物的錨點臂一定帶 `profile` —— fixture 少了它就會被口徑規則擋掉
        # （這正是這一條 fixture 要反映的真實形狀：10.923 是 `prod-new`）。
        with open(q1, "w") as fh:
            json.dump([{"rows": [{"n_prompt": 0, "n_gen": 64, "n_depth": 512,
                                  "avg_ts": 10.923036, "stddev_ts": 0.518813,
                                  "samples_ts": [10.3426, 11.3416, 11.0849]}],
                        "attribution": {"verdict": "none", "thermal_worst": "NOMINAL"},
                        "profile": "prod-new", "tag": "prod-new",
                        "cell": {"named_cell": "delivery"}}], fh)
        q2 = os.path.join(d, "Backup", "anchor_bad.json")
        with open(q2, "w") as fh:
            json.dump([{"rows": [{"n_prompt": 0, "n_gen": 64, "n_depth": 512, "avg_ts": 30.0,
                                  "stddev_ts": 9.0, "samples_ts": [30.0, 5.0, 30.0]}],
                        "attribution": {"verdict": "none", "thermal_worst": "NOMINAL"},
                        "cell": {"named_cell": "delivery"}}], fh)
        spec = {"kind": "quotable_reading",
                "spec": {"glob": "Backup/anchor_q.json", "cell": "delivery", "min_ts": 10.0}}
        v, why, _ = scan(spec, d)
        case("quotable_reading：有一列可引用的 ≥10 ⇒ EXISTS", v == EXISTS, why)
        # R7（2026-09-30）：同一列、同一 cell、同樣穩，只是**不在認可口徑上**（`prod25-stream`
        # 的 12.954 形狀）⇒ 這一格的「已經有一列可引用的讀數」必須判 ABSENT，否則
        # `needed` 會被一個引用閘門不放行的數字結掉（那正是 L20／L25 變綠的假路徑）。
        q3 = os.path.join(d, "Backup", "anchor_q_prod25.json")
        with open(q3, "w") as fh:
            json.dump([{"rows": [{"n_prompt": 0, "n_gen": 64, "n_depth": 512,
                                  "avg_ts": 12.954, "stddev_ts": 0.026,
                                  "samples_ts": [12.954, 12.94, 12.967]}],
                        "attribution": {"verdict": "none", "thermal_worst": "NOMINAL"},
                        "profile": "prod25", "tag": "prod25:!CGC_PREFILL_STREAM=1",
                        "cell": {"named_cell": "delivery"}}], fh)
        v, why, _ = scan({"kind": "quotable_reading",
                          "spec": {"glob": "Backup/anchor_q_prod25.json",
                                   "cell": "delivery", "min_ts": 10.0}}, d)
        case("quotable_reading：R7 非認可口徑 ⇒ ABSENT（不得結掉 needed）", v == ABSENT, why)
        v, why, _ = scan({"kind": "quotable_reading",
                          "spec": {"glob": "Backup/anchor_bad.json", "cell": "delivery",
                                   "min_ts": 10.0}}, d)
        case("quotable_reading：30 t/s 但離散 6× ⇒ **不可引用** ⇒ ABSENT", v == ABSENT, why)
        v, why, _ = scan({"kind": "quotable_reading",
                          "spec": {"glob": "Backup/anchor_q.json", "cell": "delivery",
                                   "min_ts": 19.2}}, d)
        case("quotable_reading：門檻拉高到 19.2 ⇒ ABSENT", v == ABSENT, why)
        v, why, _ = scan({"kind": "no_such_kind", "spec": {}}, d)
        case("未知 kind ⇒ CANNOT_JUDGE（fail-closed）", v == CANNOT, why)
        case("kind=none ⇒ 不掃，ABSENT", scan({"kind": "none"}, d)[0] == ABSENT)

        # --- 規則 ---
        def T(**kw):
            base = dict(id="X-1", state="未結案（fixture）", runnable="⛔ 現在不能跑 — fixture")
            base.update(kw)
            return base

        def errs_of(t, **kw):
            return check_target(t, d)["errs"]

        r = errs_of(T(runnable="✅ 現在能跑 — fixture",
                      precondition={"rerun": "needed", "kind": "verdict_file",
                                    "spec": [{"glob": "Backup/nope_*/x.txt"},
                                             {"glob": "docs/fixture_none_*.md"}],
                                    "expect": "verdict_absent", "why": "fixture"}))
        case("needed ＋ **兩根**掃描（含 docs/）＋ 全 ABSENT ⇒ 無錯", r == [], r)
        r = errs_of(T(runnable="✅ 現在能跑 — fixture",
                      precondition={"rerun": "needed", "kind": "verdict_file",
                                    "spec": {"glob": "Backup/nope_*/x.txt"},
                                    "expect": "verdict_absent", "why": "fixture"}))
        case("needed 只掃一個根 ⇒ 紅（L20-9 的形狀）",
             any("兩個獨立根" in e for e in r), r)
        r = errs_of(T(runnable="✅ 現在能跑 — fixture",
                      precondition={"rerun": "needed", "kind": "verdict_file",
                                    "spec": [{"glob": "Backup/nope_*/x.txt"},
                                             {"glob": "Backup/also_nope_*/y.txt"}],
                                    "expect": "verdict_absent", "why": "fixture"}))
        case("needed 兩根但都沒涵蓋 docs/ ⇒ 紅",
             any("docs/**" in e for e in r), r)
        r = errs_of(T(runnable="✅ 現在能跑 — fixture",
                      precondition={"rerun": "needed", "kind": "verdict_file",
                                    "spec": [{"glob": "docs/fixture_none_*.md"},
                                             {"glob": "Backup/pair_*/verdict_*.txt"}],
                                    "expect": "verdict_absent", "why": "fixture"}))
        case("needed 兩根、其中一根命中 ⇒ 紅（判決已在樹上）",
             any("已經存在" in e for e in r), r)
        r = errs_of(T(runnable="✅ 現在能跑 — fixture",
                      precondition={"rerun": "needed", "kind": "verdict_file",
                                    "spec": {"glob": "Backup/pair_*/verdict_*.txt"},
                                    "expect": "verdict_absent", "why": "fixture"}))
        case("needed 但判決已存在 ⇒ 紅（L25-6 的形狀）",
             any("已經存在" in e for e in r), r)
        r = errs_of(T(runnable="✅ 現在能跑 — fixture",
                      precondition={"rerun": "needed", "kind": "none",
                                    "expect": "verdict_absent", "why": "fixture"}))
        case("needed 但 kind=none ⇒ 紅（沒人能證明判決不存在）",
             any("沒有真掃描" in e for e in r), r)
        r = errs_of(T(precondition={"rerun": "duplicate", "kind": "verdict_file",
                                    "spec": {"glob": "Backup/pair_*/verdict_*.txt"},
                                    "expect": "verdict_exists", "why": "fixture"}))
        case("duplicate 沒指名 artifact ⇒ 紅", any("artifact" in e for e in r), r)
        r = errs_of(T(precondition={"rerun": "duplicate", "kind": "verdict_file",
                                    "spec": {"glob": "Backup/pair_*/verdict_*.txt"},
                                    "expect": "verdict_exists",
                                    "artifact": "Backup/pair_2026-09-28/verdict_fcrit_2.22.txt",
                                    "why": "fixture"}))
        case("duplicate ＋ 指名判決 ＋ 未結案 ⇒ 紅（該結案了）", any("未結案" in e or "已經有答案" in e for e in r), r)
        r = errs_of(T(state="結案（fixture）",
                      precondition={"rerun": "duplicate", "kind": "verdict_file",
                                    "spec": {"glob": "Backup/pair_*/verdict_*.txt"},
                                    "expect": "verdict_exists",
                                    "artifact": "Backup/pair_2026-09-28/verdict_fcrit_2.22.txt",
                                    "why": "fixture"}))
        case("duplicate ＋ 指名判決 ＋ 已結案 ⇒ 無錯", r == [], r)
        r = errs_of(T(runnable="✅ 現在能跑 — fixture",
                      precondition={"rerun": "blocked", "kind": "none", "why": "fixture"}))
        case("『✅』標籤 ＋ blocked ⇒ 紅（標籤與機器狀態不一致）", bool(r), r)
        r = errs_of(T(precondition={"rerun": "blocked", "kind": "none", "why": "fixture"}))
        case("blocked 沒說是哪一型 ⇒ 紅（佇列排不出來）",
             any("卡在哪一型" in e for e in r), r)
        r = errs_of(T(precondition={"rerun": "blocked", "kind": "none", "why": "fixture",
                                    "block": {"kind": "nope", "owner": "operator", "next": "x"}}))
        case("blocked 的 kind 不合法 ⇒ 紅", any("不合法" in e for e in r), r)
        r = errs_of(T(precondition={"rerun": "blocked", "kind": "none", "why": "fixture",
                                    "block": {"kind": "decision", "owner": "nobody", "next": "x"}}))
        case("blocked 的 owner 不合法 ⇒ 紅", any("owner" in e for e in r), r)
        r = errs_of(T(precondition={"rerun": "blocked", "kind": "none", "why": "fixture",
                                    "block": {"kind": "decision", "owner": "operator", "next": "  "}}))
        case("blocked 缺 next ⇒ 紅", any("next" in e for e in r), r)
        r = errs_of(T(precondition={"rerun": "blocked", "kind": "none", "why": "fixture",
                                    "block": {"kind": "decision", "owner": "operator",
                                              "next": "去裁",
                                              "assert": {"kind": "none", "why": "樹上掃不到"}}}))
        case("blocked ＋ 型別 ＋ owner ＋ next ＋ assert(none,有理由) ⇒ 無錯", r == [], r)
        # --- D14：卡點斷言（§67）---
        def _blkassert(assertion):
            return errs_of(T(precondition={"rerun": "blocked", "kind": "none", "why": "fixture",
                                           "block": {"kind": "code", "owner": "engine",
                                                     "next": "去改", "assert": assertion}}))

        r = _blkassert({"kind": "none"})
        case("D14 assert(none) 沒寫理由 ⇒ 紅", any("沒寫 why" in e for e in r), r)
        r = _blkassert({"kind": "nope", "glob": "x", "contains": "y"})
        case("D14 assert.kind 不認得 ⇒ 紅", any("不認得" in e for e in r), r)
        r = _blkassert({"kind": "present_in_file", "glob": "no_such_fixture_*/x.txt",
                        "contains": "y"})
        case("D14 glob 沒命中檔案 ⇒ 判不了 ⇒ 紅（fail-closed）",
             any("判不了" in e for e in r), r)
        r = _blkassert({"kind": "present_in_file", "glob": "Backup/pair_*/verdict_*.txt",
                        "contains": "NOT SEPARATED"})
        case("D14 present_in_file 命中 ⇒ 卡點還在 ⇒ 無錯", r == [], r)
        r = _blkassert({"kind": "present_in_file", "glob": "Backup/rho_snippet/*.txt",
                        "contains": SNIP})
        case("D14 present_in_file：括號片段命中 ⇒ 卡點還在（不再誤判已解）", r == [], r)
        r = _blkassert({"kind": "present_in_file", "glob": "Backup/pair_*/verdict_*.txt",
                        "contains": "CERTIFIED"})
        case("D14 present_in_file 不再命中 ⇒ 卡點已解 ⇒ 紅", any("不在樹上" in e for e in r), r)
        r = _blkassert({"kind": "absent_in_file", "glob": "Backup/pair_*/verdict_*.txt",
                        "contains": "CERTIFIED"})
        case("D14 absent_in_file 仍缺席 ⇒ 卡點還在 ⇒ 無錯", r == [], r)
        r = _blkassert({"kind": "absent_in_file", "glob": "Backup/pair_*/verdict_*.txt",
                        "contains": "NOT SEPARATED"})
        case("D14 absent_in_file 出現了 ⇒ 卡點已解 ⇒ 紅", any("不在樹上" in e for e in r), r)
        r = errs_of(T())
        case("缺 precondition ⇒ 紅", any("缺 precondition" in e for e in r), r)
        r = errs_of(T(precondition={"rerun": "blocked", "kind": "none"}))
        case("缺 why ⇒ 紅", any("why" in e for e in r), r)
        r = errs_of(T(precondition={"rerun": "blocked", "kind": "verdict_file",
                                    "spec": {"glob": "Backup/nope_*"},
                                    "expect": "verdict_exists", "why": "fixture"}))
        case("宣告 verdict_exists 但現判 ABSENT ⇒ 紅", any("expect" in e for e in r), r)
    finally:
        shutil.rmtree(d, ignore_errors=True)
    print("== selftest %d/%d ==" % (ok[0], ok[1]))
    return 0 if ok[0] == ok[1] else 1


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--yaml", default=os.path.join(HERE, "decode_board_2026-09-29.yaml"))
    ap.add_argument("--root", default=None)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    rows, errs = check_board(a.yaml, a.root)
    if a.json:
        print(json.dumps(dict(rows=rows, errs=errs), ensure_ascii=False))
        return 0 if not errs else 1
    return report(rows, errs)


if __name__ == "__main__":
    sys.exit(main())
