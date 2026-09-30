#!/usr/bin/env python3
"""口徑閘門：**非統一口徑的產物不得參與量測**（operator 2026-09-30 立規）。

為什麼要它：12+ 那組讀數看起來很漂亮，但它是 **llama-server 的 completion 回合**
（`rounds=3 warmup=1 n_predict=120`、帶 answer md5），而認證表 C3／C4 是 **llama-bench 的 cell row**
（`p0/n64/d512`、逐 rep）。兩者的**入口、形狀、聚合單元**都不同 ⇒ 同一個 prod-new profile
只代表環境一致，**不代表口徑一致**。這種「環境一致、口徑不一致」最容易混進來，
因為它在畫面上完全像一次正常量測。

統一口徑的定義（本閘門判的三條，缺一即 NON_UNIFIED）：
  1. **入口**：harness bench（llama-bench 路徑），不是 server completion 的 round。
  2. **格子**：屬於測試卡 §2.5 宣告的格子（`cell_contract.cell_names()`：'(default)' ＋ 具名格）。
  3. **聚合**：逐 rep 向量（samples_ts），不是自訂 round。

判詞：UNIFIED / NON_UNIFIED（附理由）/ UNKNOWN（掃不到 ⇒ fail-closed，不解讀成「合規」）。

⚠ 本閘門只管**口徑**；口徑合了還要過 `quote_gate.py`（窗、逐 rep 離散、R5/R6）才算可引用。

用法：
    python3 scripts/check/caliber_gate.py check <path> [<path> ...]
    python3 scripts/check/caliber_gate.py --selftest
"""
import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))

# server-round 的指紋（ABBA／completion 回合會出現）
ROUND_KEYS = ("rounds", "warmup", "n_predict")
ROUND_TEXT = re.compile(r"\[round\s+\d+\]\s+decode")
MD5_TEXT = re.compile(r"\bmd5\b\s*\[?['\"]")
CELL_TEXT = re.compile(r"--cell[= ](\S+)")


def declared_cells():
    """測試卡 §2.5 宣告的格子（含 '(default)'）。讀不到 ⇒ 回傳空集合（fail-closed）。"""
    try:
        sys.path.insert(0, HERE)
        import cell_contract  # type: ignore
        return set(cell_contract.cell_names(cell_contract.load_contract()))
    except Exception:
        return set()


def _read(path):
    try:
        return open(path, encoding="utf-8", errors="ignore").read()
    except OSError:
        return None


def classify(path, cells=None):
    """回傳 (verdict, reason)。"""
    cells = cells if cells is not None else declared_cells()
    if not os.path.exists(path):
        return "UNKNOWN", "檔案不存在"
    t = _read(path)
    if t is None:
        return "UNKNOWN", "讀不到"
    # 1) server-round 指紋：有即非統一口徑（這是 12+ 那一類）
    if ROUND_TEXT.search(t) or MD5_TEXT.search(t):
        for probe in ("[round ", "md5"):
            if probe in t:
                return "NON_UNIFIED", "server completion 的 round 級讀數（非 llama-bench 的 cell row）"
    try:
        d = json.loads(t)
    except Exception:
        d = None
    if isinstance(d, dict):
        if any(k in d for k in ROUND_KEYS):
            return "NON_UNIFIED", "產物帶 round 級欄位（%s）" % "、".join(k for k in ROUND_KEYS if k in d)
    # 2) 格子：宣告過的格子才算
    cell = None
    if isinstance(d, dict) and isinstance(d.get("cell"), str):
        cell = d["cell"]
    m = CELL_TEXT.search(t)
    if m:
        cell = m.group(1)
    if cell is None:
        # `cell` 常是巢狀欄位（在 arms／resolved 底下）⇒ 只看最上層會讀不到，
        # 進而被當成 '(default)' —— 那是把別的格子認證成別的格子。
        m2 = re.search(r'"cell"\s*:\s*"([^"]+)"', t)
        # 只接受像格子名的字串：第一版把說明文件裡的模板 `"cell": "<name>",` 也吃進來，
        # 讀出一個叫 `<name>",` 的格子 ⇒ 誤報。形狀不對就當作沒看到。
        if m2 and re.fullmatch(r"[A-Za-z0-9_().\-]+", m2.group(1) or ""):
            cell = m2.group(1)
    if not cells:
        return "UNKNOWN", "測試卡 §2.5 的格子清單讀不到 ⇒ 格子這一條判不了（fail-closed）"
    if True:
        if cell is None:
            # harness bench 未指定 cell 時就是 '(default)'；但仍要有 samples_ts 佐證是 bench 路徑
            if "samples_ts" in t:
                cell = "(default)"
            else:
                return "UNKNOWN", "看不出格子，也沒有 bench 的 rep 向量"
        if cell not in cells:
            return "NON_UNIFIED", "格子 %r 不在測試卡 §2.5 的宣告裡" % cell
    # 3) 聚合：逐 rep 向量
    if "samples_ts" not in t:
        return "NON_UNIFIED", "沒有逐 rep 向量（samples_ts）⇒ 不是統一口徑的聚合方式"
    return "UNIFIED", "cell=%s，逐 rep 聚合" % (cell or "?")


def cmd_check(paths):
    cells = declared_cells()
    if not cells:
        print("  ⚠ 讀不到測試卡 §2.5 的格子清單 ⇒ 格子這一條判不了（fail-closed）")
    bad = 0
    for p in paths:
        v, why = classify(p, cells)
        print("  %-12s %-58s %s" % (v, p, why))
        bad += 0 if v == "UNIFIED" else 1
    print("VERDICT: %d/%d 統一口徑" % (len(paths) - bad, len(paths)))
    return 0 if bad == 0 else 1


def cmd_sweep(pattern):
    """一次掃一批：把「非統一口徑不得參與量測」套到既有產物上（可重複執行）。"""
    import glob as _glob
    import collections
    cells = declared_cells()
    paths = sorted(set(_glob.glob(pattern)))
    if not paths:
        print("  沒有命中：%s" % pattern)
        return 1
    buckets = collections.defaultdict(list)
    for p in paths:
        v, why = classify(p, cells)
        buckets[v].append((p, why))
    for v in ("UNIFIED", "NON_UNIFIED", "UNKNOWN"):
        print("== %s : %d ==" % (v, len(buckets[v])))
        for p, why in buckets[v]:
            print("   %-64s %s" % (p, why))
    print("total: %d" % len(paths))
    return 0


def cmd_selftest():
    import tempfile
    ok = total = 0

    def case(name, cond, detail=""):
        nonlocal ok, total
        total += 1
        ok += 1 if cond else 0
        print("  %-52s -> %s%s" % (name, "PASS" if cond else "FAIL",
                                    "" if cond else " " + str(detail)))

    tmp = tempfile.mkdtemp()
    # round 級產物（12+ 那一類）
    p1 = os.path.join(tmp, "round.json")
    open(p1, "w").write(json.dumps({"rounds": 3, "warmup": 1, "n_predict": 120, "tg": 12.97}))
    v, _ = classify(p1, {"(default)"})
    case("round 級欄位 ⇒ NON_UNIFIED", v == "NON_UNIFIED", v)
    # round 文字指紋
    p2 = os.path.join(tmp, "round.log")
    open(p2, "w").write("[round 1] decode  12.97 t/s  prefill 19.78 t/s  md5 ['13448a8b']\n")
    v, _ = classify(p2, {"(default)"})
    case("log 的 round 指紋 ⇒ NON_UNIFIED", v == "NON_UNIFIED", v)
    # 未宣告的格子
    p3 = os.path.join(tmp, "weird.json")
    open(p3, "w").write(json.dumps({"cell": "no-such-cell", "samples_ts": [[1, 2, 3]]}))
    v, _ = classify(p3, {"(default)", "delivery"})
    case("未宣告的格子 ⇒ NON_UNIFIED", v == "NON_UNIFIED", v)
    # 統一口徑
    p4 = os.path.join(tmp, "ok.json")
    open(p4, "w").write(json.dumps({"cell": "(default)", "samples_ts": [[1, 2, 3], [11.3, 11.4, 11.2]]}))
    v, _ = classify(p4, {"(default)", "delivery"})
    case("宣告格 ＋ 逐 rep ⇒ UNIFIED", v == "UNIFIED", v)
    # 沒有 rep 向量 ⇒ 不靜默放行
    p5 = os.path.join(tmp, "norep.json")
    open(p5, "w").write(json.dumps({"cell": "(default)"}))
    v, _ = classify(p5, {"(default)"})
    case("缺 rep 向量 ⇒ NON_UNIFIED（不靜默放行）", v == "NON_UNIFIED", v)
    # 讀不到格子清單 ⇒ fail-closed
    v, why = classify(p5, set())
    case("格子清單讀不到 ⇒ UNKNOWN（不當合規）", v == "UNKNOWN", v)
    print("SELFTEST PASS（%d/%d）" % (ok, total))
    return 0 if ok == total else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="口徑閘門")
    ap.add_argument("cmd", nargs="?", choices=["check"])
    ap.add_argument("paths", nargs="*")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--sweep", metavar="GLOB",
                    help="一次掃一批產物（例：'Backup/*2026-09-30*/*.json'），只列分類與計數")
    a = ap.parse_args(argv)
    if a.selftest:
        return cmd_selftest()
    if a.sweep:
        return cmd_sweep(a.sweep)
    if a.cmd != "check" or not a.paths:
        ap.print_help()
        return 2
    return cmd_check(a.paths)


if __name__ == "__main__":
    sys.exit(main())
