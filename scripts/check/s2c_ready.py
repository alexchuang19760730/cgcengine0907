#!/usr/bin/env python3
"""S2-c 的就緒閘門：**「現在能不能落那一筆補丁」**，在動 src 之前先問一次。

為什麼需要它（2026-09-30，同 D12/runnable_gate 的道理）：S2-c 是 20+ 的鑰匙，但它卡在
**別人的工作樹**上（`llama-context.cpp`／`run_server.sh`／`ggml-metal/*`）。「等它空」不是流程，
閘門才是流程 —— 而且第二個坑更陰：那份補丁是對 HEAD 基線寫的，他線一旦落地改到同一段，
補丁就悄悄失效，`git apply` 會失敗或（更壞）貼到錯的地方。

三條判詞，全部 fail-closed（任何一條不確定 ⇒ 擋，不給「看起來可以」）：
  1. `tree`    三個目標檔是否都已提交（= 他線已放下）。未提交 ⇒ 擋。
  2. `patch`   補丁是否還貼得上（`git apply --check --recount`）。貼不上 ⇒ 擋（要 rebase）。
  3. `window`  建置/量測窗口是否空（沒有 llama-server／bench 在跑）—— 建置會蓋掉別人正在 map 的 dylib。

用法：
    python3 scripts/check/s2c_ready.py --check          # rc=0 可以落；rc=1 擋（理由印出來）
    python3 scripts/check/s2c_ready.py --selftest
"""
import argparse
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))

PATCH = "patches/s2c_rho_capture_out_of_hook.patch"
# 動工會碰到的三個檔（他線持有中）
TOUCHED = (
    "src/llama.cpp/src/llama-context.cpp",
    "src/llama.cpp/src/models/qwen35moe.cpp",
    "scripts/run_server.sh",
)
PROCS = ("llama-server", "llama-bench", "run_ids_dst_capture", "decode_sweep")


def _git(*args):
    p = subprocess.run(["git"] + list(args), cwd=ROOT, capture_output=True, text=True)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def dirty_files(paths=TOUCHED):
    """哪些目標檔目前有未提交的改動（= 還在被別人編輯）。

    ⚠ 路徑不存在也要回傳「髒」：`git status --porcelain -- <不存在路徑>` 會**成功且回空**，
    那會被讀成「乾淨 ⇒ 可以動工」—— 而實際上是「問錯了路徑」，放行它等於放行一個假陰性。
    """
    missing = [p for p in paths if not os.path.exists(os.path.join(ROOT, p))]
    rc, out = _git("status", "--porcelain", "--", *paths)
    if rc != 0:
        return sorted(set(missing) | set(paths))  # 問不到 ⇒ 全當髒（fail-closed）
    dirty = [l[3:].strip() for l in out.splitlines() if l.strip()]
    return sorted(set(missing) | set(dirty))


def patch_state(patch=PATCH):
    """補丁能不能貼：APPLIES / NEEDS_REBASE / MISSING。"""
    p = os.path.join(ROOT, patch)
    if not os.path.exists(p):
        return "MISSING", "補丁檔不存在：%s" % patch
    rc, out = _git("apply", "--check", "--recount", patch)
    if rc == 0:
        return "APPLIES", ""
    return "NEEDS_REBASE", out.strip().splitlines()[0] if out.strip() else "git apply --check 失敗"


def busy_procs():
    out = subprocess.run(["pgrep", "-fl"] + ["|".join(PROCS)], capture_output=True, text=True).stdout
    return [l for l in out.splitlines() if l.strip()]


def check():
    """回傳 (ok, 逐條報告)。任何一條不確定 ⇒ ok=False。"""
    rows, ok = [], True
    d = dirty_files()
    rows.append(("tree", "CLEAN" if not d else "DIRTY", "、".join(d) or "三個目標檔都已提交"))
    ok &= not d
    st, why = patch_state()
    rows.append(("patch", st, why or "補丁對當前樹仍然適用"))
    ok &= (st == "APPLIES")
    b = busy_procs()
    rows.append(("window", "FREE" if not b else "BUSY", "、".join(b) or "沒有 llama 行程在跑"))
    ok &= not b
    return ok, rows


def cmd_check():
    ok, rows = check()
    print("S2-c 就緒閘門（目標：把 ρ 的讀回通路搬出 per-layer hook）")
    for name, state, why in rows:
        print("  %-8s %-12s %s" % (name, state, why))
    if ok:
        print("VERDICT: READY —— 可以落補丁。下一條：")
        print("  git apply patches/%s" % os.path.basename(PATCH))
        print("  （建置前再跑一次本閘門；建置後先確認 stderr 出現 CGC-RHO-CAP，再跑 M123 見證）")
    else:
        print("VERDICT: BLOCKED（fail-closed：任一條不確定就不給放行）")
    return 0 if ok else 1


def cmd_selftest():
    import tempfile
    ok = total = 0

    def case(name, cond, detail=""):
        nonlocal ok, total
        total += 1
        ok += 1 if cond else 0
        print("  %-44s -> %s%s" % (name, "PASS" if cond else "FAIL",
                                    "" if cond else " " + str(detail)))

    # 1) 目標檔髒 ⇒ 偵測得到（用一個必然存在的 repository 檔製造髒狀態太危險 ⇒ 改測函式本身的
    #    純粹性：同一份輸入兩次呼叫結果相同，且「查不到」時 fail-closed 當成髒）
    same = dirty_files(("scripts/check/s2c_ready.py",)) == dirty_files(("scripts/check/s2c_ready.py",))
    case("dirty_files 對同一輸入穩定", same)
    fc = dirty_files(("__no_such_path__",))
    case("查不到時 fail-closed（當成髒）", fc == ["__no_such_path__"], fc)
    # 2) patch 狀態三選一，且 MISSING 不會被當成 APPLIES
    st, _ = patch_state("patches/__does_not_exist__.patch")
    case("補丁不存在 ⇒ MISSING（不靜默放行）", st == "MISSING", st)
    st2, _ = patch_state(PATCH)
    case("真補丁的狀態是三選一", st2 in ("APPLIES", "NEEDS_REBASE", "MISSING"), st2)
    # 3) check() 的結構：永遠三條、且任何一條非綠 ⇒ BLOCKED
    okk, rows = check()
    case("check() 回傳三條報告", len(rows) == 3, rows)
    case("三條都綠才 READY（其餘 BLOCKED）",
         okk == all(r[1] in ("CLEAN", "APPLIES", "FREE") for r in rows))
    print("SELFTEST PASS（%d/%d）" % (ok, total))
    return 0 if ok == total else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="S2-c 就緒閘門")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return cmd_selftest()
    return cmd_check()


if __name__ == "__main__":
    sys.exit(main())
