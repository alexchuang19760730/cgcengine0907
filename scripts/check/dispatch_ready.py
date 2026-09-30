#!/usr/bin/env python3
"""發車閘門：**現在能不能發量測車**（窗＋熱＋沒人佔用），以及**今天是否已發過**。

為什麼要它：巡檢每 2 小時跑一次，若把「窗一空就發」寫進去而沒有這道閘，它會在盒子一空出來時
每 2 小時發一次車，而且會在他線正跑 server 時硬插進去 —— 那正是今晚 (a) 那趟被拖成
`attribution=both` 的原因（prefill 只剩 149–155，該格平時 ~290）。

三條判詞（全綠才 READY，fail-closed）：
  1. `procs`   沒有 llama-server／llama-bench／decode_sweep 在跑（他線佔用中 ⇒ 不發）
  2. `compressor` 壓縮機安靜（compressor_pressure.py：QUIET）
  3. `thermal` 熱壓力 NOMINAL（thermal_pressure；**讀不到 ⇒ UNKNOWN ⇒ 擋**，不當成 0）

外加一個 **每日一次** 的節流：`--once-per-day` 會用標記檔記住今天已發過 ⇒ 避免重複發車。
標記檔放在系統暫存（/tmp），不進 repo。

用法：
    python3 scripts/check/dispatch_ready.py --check              # 現在能不能發
    python3 scripts/check/dispatch_ready.py --check --once-per-day --claim
        # 三條全綠「且」今天還沒發過 ⇒ 回 0 並寫標記；已發過 ⇒ 回 3（不重發）
    python3 scripts/check/dispatch_ready.py --selftest
"""
import argparse
import datetime as _dt
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
PROCS = ("llama-server", "llama-bench", "run_ids_dst_capture", "decode_sweep")
MARKER_DIR = "/tmp"


def _run(cmd):
    try:
        p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=90)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except Exception as e:  # 逾時／找不到 ⇒ 讀不到
        return 97, str(e)


def reclaim_state():
    """盒子回收狀態：swap 存量與可用記憶體。

    為什麼要這一條（2026-09-30 晚上的教訓）：窗看起來全綠（無人佔用／壓縮機 QUIET／熱 NOMINAL），
    但只要 swap 存量還高，每趟 launch 就再漲 2500–3200 MiB ⇒ `attribution` 一定不是 none。
    那一晚連發三趟都是 DIRTY/UNSTABLE，等於白跑 ⇒ **「壓縮機安靜」不等於「盒子已回收」**。
    """
    try:
        out = subprocess.run(["sysctl", "-n", "vm.swapusage"], capture_output=True, text=True).stdout
        # 形如：total = 1024.00M  used = 4700.00M  free = 12.00M  (encrypted)
        m = re.search(r"used\s*=\s*([0-9.]+)([KMG])", out)
        if not m:
            return "UNKNOWN", "vm.swapusage 讀不到"
        val, unit = float(m.group(1)), m.group(2)
        swap_mb = val * {"K": 1/1024, "M": 1.0, "G": 1024.0}[unit]
    except Exception as e:
        return "UNKNOWN", "sysctl 失敗：%s" % e
    try:
        vm = subprocess.run(["vm_stat"], capture_output=True, text=True).stdout
        fm = re.search(r"Pages free:\s+(\d+)", vm)
        ps = 16384 if "16384" in vm else 4096
        free_mb = int(fm.group(1)) * ps / (1024 * 1024) if fm else None
    except Exception:
        free_mb = None
    ok = swap_mb <= 2048.0 and (free_mb is None or free_mb >= 4096.0)
    detail = "swap used %.0f MiB（門檻 ≤2048）%s" % (
        swap_mb, "；free %.0f MiB（門檻 ≥4096）" % free_mb if free_mb is not None else "")
    return ("RECLAIMED" if ok else "DIRTY_BOX"), detail


def busy_procs():
    out = subprocess.run(["pgrep", "-fl"] + ["|".join(PROCS)],
                         capture_output=True, text=True).stdout or ""
    return [l for l in out.splitlines() if l.strip()]


def compressor_state():
    rc, out = _run(["/opt/homebrew/bin/python3", os.path.join(HERE, "compressor_pressure.py")])
    if rc == 97:
        return "UNKNOWN", "compressor_pressure 讀不到"
    q = "QUIET" in out.upper()
    return ("QUIET" if q else "BUSY"), (out.strip().splitlines() or [""])[-1][:90]


def thermal_state():
    """熱壓力：讀得到才算數；讀不到 ⇒ UNKNOWN（不是 0，也不是 NOMINAL）。"""
    sys.path.insert(0, HERE)
    try:
        import thermal_pressure  # type: ignore
    except Exception as e:
        return "UNKNOWN", "thermal_pressure 匯入失敗：%s" % e
    try:
        lvl = thermal_pressure.level() if hasattr(thermal_pressure, "level") else None
        if lvl is None:
            return "UNKNOWN", "thermal_pressure.level() 回 None"
        # 0/1 = NOMINAL/FAIR 視為可發；2/3 = HEAVY/CRITICAL 擋
        return ("NOMINAL" if int(lvl) <= 1 else "HOT"), "level=%s" % lvl
    except Exception as e:
        return "UNKNOWN", "讀取失敗：%s" % e


def marker_path():
    return os.path.join(MARKER_DIR, "cgc_dispatch_%s" % _dt.date.today().isoformat())


def claimed_today():
    return os.path.exists(marker_path())


def claim():
    try:
        open(marker_path(), "w").write(_dt.datetime.now().isoformat() + "\n")
        return True
    except OSError:
        return False


def check():
    """回傳 (ok, rows)。rows = [(name, state, detail)]。"""
    rows = []
    p = busy_procs()
    rows.append(("procs", "FREE" if not p else "BUSY", "、".join(p) or "沒有 llama 行程在跑"))
    c, cd = compressor_state()
    rows.append(("compressor", c, cd))
    t, td = thermal_state()
    rows.append(("thermal", t, td))
    r, rd = reclaim_state()
    rows.append(("reclaim", r, rd))
    ok = (not p) and c == "QUIET" and t == "NOMINAL" and r == "RECLAIMED"
    return ok, rows


def main(argv=None):
    ap = argparse.ArgumentParser(description="發車閘門（窗／熱／佔用／每日一次）")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--once-per-day", action="store_true", help="今天已發過就不再發")
    ap.add_argument("--claim", action="store_true", help="READY 時寫下今天的標記")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)

    if a.selftest:
        ok = total = 0

        def case(n, cond, detail=""):
            nonlocal ok, total
            total += 1
            ok += 1 if cond else 0
            print("  %-46s -> %s%s" % (n, "PASS" if cond else "FAIL",
                                        "" if cond else " " + str(detail)))

        # 結構：永遠三條，且熱讀不到時不得當成可發
        okk, rows = check()
        case("check() 回傳四條", len(rows) == 4, rows)
        case("任一條 UNKNOWN ⇒ 不 READY",
             (okk is False) if any(r[1] == "UNKNOWN" for r in rows) else True, rows)
        case("四條都綠才 READY",
             okk == all(r[1] in ("FREE", "QUIET", "NOMINAL", "RECLAIMED") for r in rows))
        # 盒子沒回收（swap 存量高）⇒ 不發車（這是今晚三趟白跑的原因）
        case("reclaim 不是 RECLAIMED ⇒ 不 READY",
             (okk is False) if any(r[0] == "reclaim" and r[1] != "RECLAIMED" for r in rows) else True,
             [r for r in rows if r[0] == "reclaim"])
        case("標記檔路徑在系統暫存（不進 repo）", marker_path().startswith("/tmp"))
        print("SELFTEST PASS（%d/%d）" % (ok, total))
        return 0 if ok == total else 1

    if not a.check:
        ap.print_help()
        return 2

    ok, rows = check()
    for n, s, d in rows:
        print("  %-11s %-9s %s" % (n, s, d))
    if a.once_per_day and claimed_today():
        print("VERDICT: ALREADY_CLAIMED（今天已發過 ⇒ 不重發）")
        return 3
    if not ok:
        print("VERDICT: BLOCKED（fail-closed：任一條不確定就不發車）")
        return 1
    print("VERDICT: READY")
    if a.claim:
        print("  標記：%s" % ("已寫" if claim() else "寫入失敗"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
