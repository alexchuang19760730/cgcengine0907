#!/usr/bin/env python3
"""S1（25 唯一活路）的觀察閘門：**他線的引擎活做到哪了**，用可觀測跡證回答，不靠問人。

為什麼需要它：S1 是「修 `is_mem_shared` 的 draft 輪級掉線 ⇒ `k_eff` 1.59 → k」，那不是本線的程式，
本線也不能催；但「有沒有進展」是**可觀測**的——因為這個 bug 有兩個機器會印出來的指紋：

  1. `CGC-PHASE-SPLIT: ... -> decode graph width=N tokens`   ← 修好後 N 要能 ≥ k（今天是 1）
  2. `CGC-MTP-PERF` 的 `k_eff` / `acc_rate`                  ← 修好後 k=8 時 k_eff 要 ≥ 7

判詞（fail-closed，跡證不足就說不知道，不猜）：
  FIXED     出現 width ≥ 8 **且** 有 k_eff ≥ 7 的讀數 ⇒ S1 已修，可以接著量 25
  MOVING    只有其一（或數值有進步但未達標）
  STALLED   兩者都還是舊值（width=1／k_eff<2）⇒ 他線還沒動到這段
  UNKNOWN   掃不到任何 PHASE-SPLIT／MTP-PERF 跡證

用法：
    python3 scripts/check/s1_watch.py --check
    python3 scripts/check/s1_watch.py --selftest
"""
import argparse
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
BACKUP = os.path.join(ROOT, "Backup")

WIDTH_RE = re.compile(r"decode graph width=(\d+) tokens")
# ⚠ log 裡沒有 `k_eff` 這個欄位名：每輪實際拿到幾個 draft token 是 `gen_tok_per_round`
#   （今天診斷用的 1.59–1.72 就是它；`acc_rate` 是命中率，不是 E）。用錯欄位會讀成 0，
#   而 0 會被解讀成「沒數據」——又一個假陰性。
KEFF_RE = re.compile(r"gen_tok_per_round[=: ]+([0-9]*\.?[0-9]+)")
ACC_RE = re.compile(r"acc_rate[=: ]+([0-9]*\.?[0-9]+)")
# S1 相關的程式提交（他線動工會碰到這些）
S1_FILES = ("common/speculative.cpp", "src/llama-context.cpp", "src/llama.cpp/src/llama-context.cpp")


def _logs(limit_dirs=None):
    """Backup 下所有 .stderr.log（新的優先）。"""
    out = []
    for dirpath, _dirnames, filenames in os.walk(BACKUP):
        for fn in filenames:
            if fn.endswith(".stderr.log") or fn.endswith(".log"):
                out.append(os.path.join(dirpath, fn))
    out.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return out


def scan(logs=None):
    """回傳 (max_width, max_keff, best_acc, 命中檔數, 最近一個檔)。

    ⚠ 只看**同一族**的 log：含 `CGC-MTP-PERF`（MTP decode 跑動）或 `CGC-PHASE-SPLIT` 的檔案。
    第一版掃了 Backup 下全部 1644 份 log，把 server 啟動日誌的 `width=64` 也當成解碼寬度
    ⇒ 讀出「width 已達 64」這種假的樂觀。兩個指紋必須來自同一批跑動才有意義。
    """
    max_w, max_k, best_a, hits, newest = 0, 0.0, 0.0, 0, None
    for p in (logs if logs is not None else _logs()):
        try:
            t = open(p, encoding="utf-8", errors="ignore").read()
        except OSError:
            continue
        if "CGC-MTP-PERF" not in t:   # 只有 MTP decode 跑動才算數（server 啟動日誌也有 PHASE-SPLIT）
            continue
        w = [int(x) for x in WIDTH_RE.findall(t)]
        k = [float(x) for x in KEFF_RE.findall(t)]
        a = [float(x) for x in ACC_RE.findall(t)]
        if w or k:
            hits += 1
            if newest is None or os.path.getmtime(p) > os.path.getmtime(newest):
                newest = p
        if w:
            max_w = max(max_w, max(w))
        if k:
            max_k = max(max_k, max(k))
        if a:
            best_a = max(best_a, max(a))
    return max_w, max_k, best_a, hits, newest


def git_activity(since="2026-09-30"):
    """他線有沒有動到 S1 會碰的檔案（只看已提交；工作樹的髒不算，因為那可能是別的東西）。"""
    try:
        out = subprocess.run(["git", "log", "--oneline", "--since", since, "--name-only"],
                             cwd=ROOT, capture_output=True, text=True).stdout
    except OSError:
        return []
    files = set()
    for l in out.splitlines():
        l = l.strip()
        for f in S1_FILES:
            if l.endswith(f) or l == f:
                files.add(l)
    return sorted(files)


def verdict(width, keff):
    """只有兩個指紋都達標才算 FIXED；其一進步算 MOVING；都停在舊值算 STALLED。"""
    if width >= 8 and keff >= 7.0:
        return "FIXED"
    if width >= 8 or keff >= 2.0:
        return "MOVING"
    if width or keff:
        return "STALLED"
    return "UNKNOWN"


def cmd_check():
    w, k, a, hits, newest = scan()
    st = verdict(w, k)
    act = git_activity()
    print("S1 觀察閘門（25 唯一活路：is_mem_shared 的 draft 輪級掉線 ⇒ k_eff 1.59 → k）")
    print("  指紋① decode graph width : 最大 %d（修好需 ≥ 8；今天是 1 是真兇：width=1 讓大 k 構上量不到）" % w)
    print("  指紋② gen_tok_per_round   : 最大 %.3f（＝每輪實拿 draft 數，即 k_eff；判別句：k=8 時需 ≥ 7）" % k)
    print("  參考   acc_rate          : 最大 %.3f（門檻 a ≥ 0.484 ⇒ k=8 ≈ 25.2 t/s）" % a)
    print("  跡證   : %d 份 log 含指紋；最近＝%s" % (hits, newest or "—"))
    print("  他線提交（S1 會碰的檔）: %s" % ("、".join(act) or "無（今日尚未提交到這幾支）"))
    print("VERDICT: %s" % st)
    if st == "FIXED":
        print("  ⇒ 可以接著量 25：先帶足跡閘（MTP-on 存活 ≥7703 MB），再跑 k=8。")
    elif st == "UNKNOWN":
        print("  ⇒ 掃不到指紋（fail-closed）：不要解讀成「沒進展」，只是沒有跡證。")
    return 0


def cmd_selftest():
    ok = total = 0

    def case(name, cond, detail=""):
        nonlocal ok, total
        total += 1
        ok += 1 if cond else 0
        print("  %-46s -> %s%s" % (name, "PASS" if cond else "FAIL",
                                    "" if cond else " " + str(detail)))

    case("width=1,k_eff=1.59 ⇒ STALLED", verdict(1, 1.59) == "STALLED")
    case("width=8,k_eff=1.9 ⇒ MOVING（只解一半）", verdict(8, 1.9) == "MOVING")
    case("width=8,k_eff=7 ⇒ FIXED", verdict(8, 7.0) == "FIXED")
    case("都沒有 ⇒ UNKNOWN（不猜）", verdict(0, 0.0) == "UNKNOWN")
    # 掃描器對空輸入不炸
    w, k, a, hits, _ = scan(logs=[])
    case("空輸入 ⇒ 全 0（不炸）", (w, k, a, hits) == (0, 0.0, 0.0, 0))
    # 欄位名對不對：真的 MTP-PERF 行要讀得出 gen_tok_per_round
    sample = ("CGC-MTP-PERF type=draft-mtp calls_begin=1 calls_draft=43 calls_accept=43 "
              "gen_tokens=70 acc_tokens=24 t_begin_ms=0.0 t_draft_ms=587.1 acc_rate=0.3429 "
              "gen_tok_per_round=1.628 acc_tok_per_round=0.558 ms_per_round=13.653")
    case("真的 MTP-PERF 行讀得出 k_eff", KEFF_RE.findall(sample) == ["1.628"], KEFF_RE.findall(sample))
    case("同一行讀得出 acc_rate", ACC_RE.findall(sample) == ["0.3429"], ACC_RE.findall(sample))
    print("SELFTEST PASS（%d/%d）" % (ok, total))
    return 0 if ok == total else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="S1 觀察閘門")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return cmd_selftest()
    return cmd_check()


if __name__ == "__main__":
    sys.exit(main())
