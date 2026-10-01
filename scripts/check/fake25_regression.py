#!/usr/bin/env python3
"""假 25 回歸測試：全語料每一個「decode ≥ 20 t/s」的讀數，都必須**過不了閘門**。

為什麼要一個**永久**的回歸測試，而不是再一次一次性掃描
--------------------------------------------------------
§55–§61 的結論是：**20+／25+ 目前沒有一列是在交付目標的函數上、用活著的計數器量到的**。
那個結論靠的是一次掃描，而掃描會過期：新產物、新臂、新旗標都會生出新的 ≥20。
⇒ 把當時掃到的讀數**凍結成 fixture**（`scripts/check/fixtures/fake25_corpus_2026-09-30.json`，
   每個讀數連它那一場的 `prod` 一起存），讓「這些數字不可引用」變成每次跑 pipeline 都會被**重算**的
   斷言，而不是文件裡的一句話。判準改了、旗標表改了，這條測試會跟著判準一起動（它 import 兩個閘門，
   不重寫任何判準）。

三軸同時判（不是只判一條）
--------------------------
  ① `quote_gate`：R1–R3 逐 rep 離散／樣本數、R4 窗口、R5 臂身分、R6 輸出見證。
  ② `budget_gate`：`池子 ＋ Metal 峰值駐留 ＋ 保留 ＞ 上限` ⇒ 這一場的 t/s 是**足跡**，不是**能力**（§59）。
  ③ 臂身分（R5／R6）單獨再印一次——它已被折進 ①，但它是最容易被誤會的一條
     （「換個乾淨窗口就能引用」是被 R5／R6 封死的，不是被窗口封死的）。

    admissible（可以當交付讀數）＝ ① 判 `QUOTABLE` **且** ② 不 block。
    兩條都過才算一件事；任一條沒過，這個數字就不是 L20／L25 的讀數。

斷言
----
  A1 每一個讀數都**不 admissible**（除非列在 `ALLOWLIST`，預設空）
  A2 每一個被擋的讀數都要有**指名**的理由（不能「被擋了但不知道為什麼」）
  A3 **反空洞**：合成的「乾淨 ≥25」必須**過**——證明這條測試不是靠閘門一律說不而成立
  A4 反空洞的第二半：把那個乾淨的 ≥25 加上 `CGC_SEG_BATCH=1`（＝真實 21 個 ≥25 的形狀）
     ⇒ 必須被 **R6** 擋，而且理由裡看得到 R6

掃到新的 admissible 讀數時
--------------------------
那是**好消息**（第一個誠實的 20+／25+），但必須是刻意的動作：測試會紅，把它的產物路徑與證據
加進 `ALLOWLIST`（附一行說明、跑一次 `--build-ledger` 更新 fixture），再重跑。**刻意不自動放行**：
自動放行等於沒有這條測試。

用法
----
    python3 scripts/check/fake25_regression.py                       # 重播 fixture（快、決定性）
    python3 scripts/check/fake25_regression.py --check               # 重播 ＋ 現地掃描 Backup
    python3 scripts/check/fake25_regression.py --check --ledger-only  # 只重播（pipeline 的 check 路徑）
    python3 scripts/check/fake25_regression.py --build-ledger        # 重建 fixture（掃描現況）
    python3 scripts/check/fake25_regression.py --selftest
    python3 scripts/check/fake25_regression.py --json /tmp/fake25.json

rc：0 = 全部被擋（且反空洞成立）；1 = 有 admissible 讀數或斷言不成立；2 = 用法錯誤；3 = fixture 不在。
"""
from __future__ import annotations

import argparse
import glob as _glob
import importlib.util
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
LEDGER = os.path.join(HERE, "fixtures", "fake25_corpus_2026-09-30.json")
DEFAULT_GLOB = "Backup/**/*.json"
MIN_TPS = 20.0

# 可以當交付讀數的（產物相對路徑, row_index）。**預設空**：今天沒有任何一個 ≥20 過得了兩條閘門。
# 要加進來，必須附 (a) 它的引用判詞是 QUOTABLE、(b) 預算判詞不是 OVERBUDGET／REFUSE、
# (c) 一行說明它為什麼量到的是交付目標（誠實臂／輸出已驗）。加進來之前先問自己：
#     這一列給的是「這台機器能跑多快」，還是「補頁政策藏了多少」？
ALLOWLIST: tuple[tuple[str, int], ...] = ()


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, filename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def gates():
    """兩個閘門（判準的唯一來源）。不在這裡重寫任何判準。"""
    return _load("quote_gate", "quote_gate.py"), _load("budget_gate", "budget_gate.py")


def is_decode(row):
    """decode row：llama-bench 的 tg（`n_gen>0`）。prefill row（`n_gen=0`）不算——
    pp 374 t/s 不是交付目標，它只是同一張表上的另一列。"""
    return isinstance(row, dict) and (row.get("n_gen") or 0) > 0


def judge_reading(qg, bg, prod, row, ignore_budget=False):
    """判一個讀數（row）。回 dict：兩軸判詞、臂身分、admissible、指名理由。"""
    qv, qwhy, qm = qg.judge(prod, row)
    bblocked, bwhy = bg.blocks_capability(prod)
    bv, _bwhy2, bm = bg.judge(prod)
    # 臂身分**獨立算**，不看 verdict：`judge` 在 THIN／REFUSE 會提早回傳，
    # 那是它的短路（那些格連中央趨勢都沒有），但回歸測試要的是「身上有什麼」——
    # 否則「reps=1 的單次提交臂」會被記成「沒有臂旗標」，第三軸就漏了一格。
    arm = {k: "R5" for k in qg.arm_instruments(prod)}
    arm.update({k: "R6" for k in qg.unverified_output_arms(prod)})
    reasons = list(qwhy)
    if bblocked:
        reasons += ["預算：%s" % r for r in (bwhy or ["（沒有理由？）"])]
    if ignore_budget:
        # 只為了**報告**：假設無限 unified memory（預算那一軸不存在）⇒ 這一場還會不會過？
        # 這是回答「換更大台的機器是不是就有 25」的方式：若 34 個在這一軸也全倒，
        # 那麼「擴記憶體」就不是那條路。**不放行**（admissible 仍要求 budget 這條的真判詞）。
        reasons = list(qwhy)
        return dict(quote=qv, quote_reasons=qwhy, quote_metrics=qm,
                    budget=bv, budget_blocked=bool(bblocked), budget_reasons=bwhy,
                    budget_metrics=bm, arm=arm, reasons=reasons,
                    admissible=(qv == "QUOTABLE"))
    return dict(quote=qv, quote_reasons=qwhy, quote_metrics=qm,
                budget=bv, budget_blocked=bool(bblocked), budget_reasons=bwhy,
                budget_metrics=bm, arm=arm, reasons=reasons,
                admissible=(qv == "QUOTABLE" and not bblocked))


def scan_corpus(min_tps=MIN_TPS, glob_pat=DEFAULT_GLOB, ignore_budget=False):
    """掃全語料，回（讀數清單, 統計）。讀數＝decode row 且 avg_ts ≥ min_tps。"""
    qg, bg = gates()
    files = sorted(_glob.glob(os.path.join(ROOT, glob_pat), recursive=True))
    readings, tally = [], dict(files=0, prods=0, rows=0, decode=0, fake=0, fake25=0,
                               admissible=0, unreadable=0)
    for path in files:
        rel = os.path.relpath(path, ROOT)
        if os.path.abspath(path) == os.path.abspath(LEDGER):
            continue                      # 不把自己的 fixture 當成語料
        try:
            with open(path, encoding="utf-8") as fh:
                doc = json.load(fh)
        except Exception:  # noqa: BLE001
            tally["unreadable"] += 1
            continue
        tally["files"] += 1
        for prod in (doc if isinstance(doc, list) else [doc]):
            if not isinstance(prod, dict):
                continue
            tally["prods"] += 1
            for i, row in enumerate(prod.get("rows") or []):
                if not isinstance(row, dict) or row.get("avg_ts") is None:
                    continue
                tally["rows"] += 1
                if not is_decode(row):
                    continue
                tally["decode"] += 1
                tps = float(row["avg_ts"])
                if tps < min_tps:
                    continue
                tally["fake"] += 1
                if tps >= 25.0:
                    tally["fake25"] += 1
                j = judge_reading(qg, bg, prod, row, ignore_budget=ignore_budget)
                if j["admissible"]:
                    tally["admissible"] += 1
                readings.append(dict(artifact=rel, row_index=i, avg_ts=tps,
                                     shape="p%s/n%s/d%s" % (row.get("n_prompt"), row.get("n_gen"),
                                                            row.get("n_depth")),
                                     prod=prod, row=row, judged=j, frozen=False))
    readings.sort(key=lambda r: (-r["avg_ts"], r["artifact"]))
    return readings, tally


def build_ledger(min_tps=MIN_TPS, glob_pat=DEFAULT_GLOB, path=LEDGER):
    readings, tally = scan_corpus(min_tps, glob_pat)
    doc = dict(
        fixture="fake25_corpus", built="2026-09-30",
        why="§55–§61 的結論（20+／25+ 沒有一列是交付目標的讀數）需要一個**可重算**的東西，不是一次掃描。",
        rule="decode row（n_gen>0）且 avg_ts >= threshold ⇒ 必須不 admissible"
             "（admissible = quote_gate QUOTABLE 且 budget_gate 不 block）。",
        threshold_tps=min_tps, corpus_glob=glob_pat, tally=tally,
        count=len(readings),
        note="每個讀數連它那一場的 prod 一起凍結（判準改了會跟著重判，不依賴 Backup/ 還在不在）。",
        readings=[dict(artifact=r["artifact"], row_index=r["row_index"], avg_ts=r["avg_ts"],
                       shape=r["shape"], prod=r["prod"], row=r["row"],
                       gate_when_built={k: v for k, v in r["judged"].items()
                                        if k in ("quote", "budget", "budget_blocked",
                                                 "arm", "admissible")})
                  for r in readings])
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    return doc


def replay(doc, ignore_budget=False):
    """重播 fixture：用**現在的**判準重判每一個凍結的讀數。"""
    qg, bg = gates()
    out = []
    for r in doc.get("readings") or []:
        prod = dict(r.get("prod") or {})
        row = r.get("row") or {}
        j = judge_reading(qg, bg, prod, row, ignore_budget=ignore_budget)
        j["frozen"] = r.get("gate_when_built") or {}
        out.append(dict(artifact=r.get("artifact"), row_index=r.get("row_index"),
                        avg_ts=r.get("avg_ts"), shape=r.get("shape"), judged=j))
    return out


def _short(text, n=110):
    t = " ".join(str(text).split())
    return t if len(t) <= n else t[:n - 1] + "…"


def audit(findings, allowlist=ALLOWLIST):
    """套用 A1／A2／A3（A4 在 selftest）。回 (errs, rows)。"""
    errs, rows = [], []
    for f in findings:
        j = f["judged"]
        allowed = (f["artifact"], f["row_index"]) in allowlist
        rows.append(dict(artifact=f["artifact"], row_index=f["row_index"], avg_ts=f["avg_ts"],
                         shape=f["shape"],
                         quote=j["quote"], budget=j["budget"], arm=j["arm"],
                         admissible=j["admissible"], allowlisted=allowed,
                         first_reason=_short((j["reasons"] or ["（無）"])[0])))
        if j["admissible"]:
            if allowed:
                rows[-1]["first_reason"] = "（allowlist 放行：已附證據）"
            else:
                errs.append("A1 %s row%s ＝ %.3f t/s **admissible**（quote=%s、budget=%s）"
                            "⇒ 這是第一個誠實的 20+／25+。若確實如此，把它加進 ALLOWLIST（附證據）"
                            "並跑一次 --build-ledger；否則這是假 25 的回歸。"
                            % (f["artifact"], f["row_index"], f["avg_ts"] or 0.0,
                               j["quote"], j["budget"]))
        elif not j["reasons"]:
            errs.append("A2 %s row%s 被擋了但說不出理由（判準不能只給 verdict）"
                        % (f["artifact"], f["row_index"]))
    return errs, rows


def _print_rows(rows, prefix="  "):
    for r in rows:
        art = r["artifact"]
        art = art if len(art) <= 44 else "…" + art[-43:]
        arm = "、".join("%s=%s" % kv for kv in sorted(r["arm"].items())) or "—"
        print("%s%-44s %8.3f %-9s %-11s %-16s %s"
              % (prefix, art, r["avg_ts"] or 0.0, r["quote"], r["budget"], arm, r["first_reason"]))


def report(rows, errs, title, live_rows=None, ignore_budget=False):
    print("假 25 回歸測試 fake25_regression%s：%s" % ("（含現地掃描）" if live_rows is not None else "", title))
    print("  A1 每一個 decode ≥ 20 的讀數都不 admissible（quote QUOTABLE 且 budget 不 block）；"
          "A2 每個被擋的都要說得出理由")
    print("  %-44s %8s %-9s %-11s %-16s %s" % ("產物（截短）", "t/s", "引用", "預算", "臂身分", "第一條理由"))
    _print_rows(rows)
    adm = [r for r in rows if r["admissible"] and not r["allowlisted"]]
    print("\n  讀數 %d 個：admissible %d 個（allowlist 放行 %d）"
          % (len(rows), len(adm), sum(1 for r in rows if r["allowlisted"])))
    if live_rows is not None:
        frozen = {(r["artifact"], r["row_index"]) for r in rows}
        # 現地只看「fixture 裡沒有的」與「判詞變了的」——其餘 34 列重印只是噪音
        fresh = [r for r in live_rows
                 if (r["artifact"], r["row_index"]) not in frozen]
        print("  現地：與 fixture 相同 %d 列；新出現 %d 列（新產物或門檻以下變上來）"
              % (len(live_rows) - len(fresh), len(fresh)))
        if fresh:
            print("  %-44s %8s %-9s %-11s %-16s %s"
                  % ("（現地新增）", "t/s", "引用", "預算", "臂身分", "第一條理由"))
            _print_rows(fresh, prefix="  現地 ")
        else:
            print("  ⇒ 今天沒有任何新的 ≥%.0f 讀數；已凍結的 34 列全部仍不可引用。" % MIN_TPS)
    for e in errs:
        print("  ✗ " + e)
    print("VERDICT: %s（%d 個問題）%s"
          % ("PASS" if not errs else "FAIL", len(errs),
             "｜**--ignore-budget 是報告模式**：admissible 只算引用軸，這一軸的判詞不當放行" 
             if ignore_budget else ""))
    return 0 if not errs else 1


def selftest():
    """fixture：反空洞（乾淨的要過）＋ 真實形狀（SEG_BATCH 的要被 R6 擋）。"""
    qg, bg = gates()
    ok = 0
    total = 0

    def case(name, cond, detail=""):
        nonlocal ok, total
        total += 1
        ok += 1 if cond else 0
        print("  %-52s -> %s%s" % (name, "PASS" if cond else "FAIL", "" if cond else " " + str(detail)))

    def clean_prod(extra_env=None, pool=2048.0, wired=6000.0, reserve=1024.0, ceil=20000.0,
                   attrib="none"):
        env = dict(extra_env or {})
        return dict(env=env, tag="prod-new", cell={"named_cell": "delivery"},
                    attribution={"verdict": attrib, "thermal_worst": "NOMINAL"},
                    metal_gate_peak=dict(items=[{"name": "pool_mb", "mb": pool},
                                                {"name": "wired_mb", "mb": wired},
                                                {"name": "reserve_mb", "mb": reserve}],
                                         total_mb=pool + wired + reserve,
                                         ceiling_mb=ceil, ceiling_source="fixture"),
                    cache={"misses": 0, "file_reads": 0, "io_bytes": 0, "io_effective_mib_s": 0.0})

    clean_row = {"n_prompt": 0, "n_gen": 64, "n_depth": 512, "avg_ts": 25.5,
                 "stddev_ts": 0.15, "samples_ts": [25.4, 25.5, 25.6]}

    j = judge_reading(qg, bg, clean_prod(), clean_row)
    case("A3 反空洞：乾淨的 ≥25 必須**過**（quote QUOTABLE ＋ budget 不 block）",
         j["quote"] == "QUOTABLE" and not j["budget_blocked"] and j["admissible"],
         (j["quote"], j["budget"], j["reasons"]))
    j = judge_reading(qg, bg, clean_prod({"CGC_SEG_BATCH": "1"}), clean_row)
    case("A4 真實形狀：同一場 ＋ CGC_SEG_BATCH ⇒ R6 擋",
         (not j["admissible"]) and j["quote"] == "DIRTY"
         and any("輸出未驗" in r or "R6" in r for r in j["reasons"]), j["reasons"])
    j = judge_reading(qg, bg, clean_prod({"CGC_MISS_MASK_DBG": "1", "CGC_MISS_MASK_COST": "1"}),
                      clean_row)
    case("R5：診斷量具（DBG／COST）⇒ DIRTY",
         j["quote"] == "DIRTY" and set(j["arm"]) >= {"CGC_MISS_MASK_DBG", "CGC_MISS_MASK_COST"})
    j = judge_reading(qg, bg, clean_prod(attrib="swap"), clean_row)
    case("R4：attribution=swap ⇒ DIRTY", j["quote"] == "DIRTY")
    # ⚠ 離散的 fixture 必須讓 `stddev_ts` 與 `samples_ts` **自洽**（R0），否則先判 REFUSE
    #    —— 這正是「產物內部不自洽」那條在做事：想用「樣本很亂、卻回報很穩」來造一個穩的讀數，會被擋。
    _s = [25.4, 25.5, 30.0]
    _mean = sum(_s) / len(_s)
    _sd = (sum((x - _mean) ** 2 for x in _s) / (len(_s) - 1)) ** 0.5   # n-1，與 _series_stats 同口徑
    j = judge_reading(qg, bg, clean_prod(),
                      dict(clean_row, samples_ts=_s, avg_ts=_mean, stddev_ts=_sd))
    case("R2：逐 rep 離散 > 1.10 ⇒ UNSTABLE", j["quote"] == "UNSTABLE", (j["quote"], j["reasons"]))
    j = judge_reading(qg, bg, clean_prod(), dict(clean_row, samples_ts=[25.4, 25.5, 30.0]))
    case("R0：樣本很亂卻回報很穩 ⇒ REFUSE（不自洽）", j["quote"] == "REFUSE", j["quote"])
    j = judge_reading(qg, bg, clean_prod(pool=8192.0, wired=9936.0, ceil=11453.25), clean_row)
    case("B1：必然 swap（8192＋9936＋1024 ＞ 11453）⇒ budget 擋",
         j["budget_blocked"] and j["budget"] == "OVERBUDGET" and not j["admissible"])
    j = judge_reading(qg, bg, clean_prod(pool=8192.0, wired=9936.0, ceil=11453.25), clean_row)
    case("B1 的理由指名「必然 swap」並報出超額",
         any("必然" in r for r in j["reasons"]), j["reasons"])
    # A2：擋要擋得出理由
    errs, _rows = audit([dict(artifact="x", row_index=0, avg_ts=27.3, shape="p0/n64/d512",
                              judged=judge_reading(qg, bg, clean_prod({"CGC_SEG_BATCH": "1"}),
                                                   clean_row))])
    case("A2 被擋的讀數說得出理由（SEG_BATCH 那筆）", not errs, errs)
    errs, _rows = audit([dict(artifact="y", row_index=0, avg_ts=25.5, shape="p0/n64/d512",
                              judged=judge_reading(qg, bg, clean_prod(), clean_row))])
    case("A1 乾淨的 ≥25 會讓測試紅（＝測試不是空的）", bool(errs), errs)
    errs, _rows = audit([dict(artifact="y", row_index=0, avg_ts=25.5, shape="p0/n64/d512",
                              judged=judge_reading(qg, bg, clean_prod(), clean_row))],
                        allowlist=(("y", 0),))
    case("ALLOWLIST 放行（刻意動作才放行）", not errs, errs)
    print("SELFTEST %s（%d/%d）" % ("PASS" if ok == total else "FAIL", ok, total))
    return 0 if ok == total else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="假 25 回歸測試（全語料 decode ≥20 都必須過不了閘門）")
    ap.add_argument("--build-ledger", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--ledger-only", action="store_true", help="不掃現地語料，只重播 fixture")
    ap.add_argument("--ignore-budget", action="store_true",
                    help="只報告：假設無限 unified memory（預算那一軸不存在），還有幾個讀數過得了引用軸？")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--min-tps", type=float, default=MIN_TPS)
    ap.add_argument("--glob", default=DEFAULT_GLOB)
    ap.add_argument("--json")
    a = ap.parse_args(argv)

    if a.selftest:
        rc = selftest()
        return rc

    if a.build_ledger:
        doc = build_ledger(a.min_tps, a.glob)
        print("fixture 已重建：%s（%d 個讀數；decode≥%.0f 中 ≥25 有 %d 個）"
              % (os.path.relpath(LEDGER, ROOT), doc["count"], a.min_tps,
                 doc["tally"]["fake25"]))
        return 0

    if not os.path.exists(LEDGER):
        print("fixture 不存在：%s（先跑 --build-ledger）" % os.path.relpath(LEDGER, ROOT), file=sys.stderr)
        return 3

    with open(LEDGER, encoding="utf-8") as fh:
        doc = json.load(fh)
    findings = replay(doc, ignore_budget=a.ignore_budget)
    errs, rows = audit(findings)
    print("fixture：%s（建於 %s；門檻 %.0f t/s；凍結 %d 個讀數、其中 ≥25 有 %d 個；"
          "掃描時 corpus = %s）"
          % (os.path.relpath(LEDGER, ROOT), doc.get("built"), doc.get("threshold_tps", MIN_TPS),
             doc.get("count"), (doc.get("tally") or {}).get("fake25"), doc.get("corpus_glob")))
    live = None
    if a.check and not a.ledger_only:
        live, tally = scan_corpus(a.min_tps, a.glob, ignore_budget=a.ignore_budget)
        lerrs, lrows = audit(live)
        print("現地掃描：%s（檔案 %d／產物 %d／row %d；decode %d；decode≥%.0f ＝ %d（其中 ≥25 %d）；"
              "admissible %d）"
              % (a.glob, tally["files"], tally["prods"], tally["rows"], tally["decode"],
                 a.min_tps, tally["fake"], tally["fake25"], tally["admissible"]))
        errs = errs + ["現地：" + e for e in lerrs]
        rc = report(rows, errs, "fixture 重播 ＋ 現地掃描", live_rows=lrows,
                    ignore_budget=a.ignore_budget)
    else:
        rc = report(rows, errs, "fixture 重播", ignore_budget=a.ignore_budget)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as fh:
            json.dump(dict(fixture=os.path.relpath(LEDGER, ROOT), rows=rows, errors=errs),
                      fh, ensure_ascii=False, indent=1)
        print("json：%s" % a.json)
    return rc


if __name__ == "__main__":
    sys.exit(main())
