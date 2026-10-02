#!/usr/bin/env python3
"""唯一 gate 規格的漂移探測器（MEASUREMENT_CONTRACT §3）。

WHY THIS EXISTS
---------------
`docs/MEASUREMENT_CONTRACT_2026-09-25.md` §3 定義了「唯一的 gate 規格」。
但規格寫得再清楚，只要**沒有一支程式去比對實作**，它就會漂移 ——
2026-09-25 的實際狀態就是活證：同一個 commit 內三處 swap 門檻各不相同
（`arm_two_pass` 起跑 1024 / `lane_watchdog` 止血 3072 / `budget_gate` 超訂即拒），
而 `--reps` 預設是 1（讓散度判據永不觸發）。

⇒ 這支程式把「規格」變成「可機檢的期望」：
   **現在是紅的**（§3 尚未接線）正是它該有的樣子 —— 接線後自動轉綠。

2026-10-01：§3.4.1「所有會起 llama-bench 的 runner 一律接 budget_gate.sh」不再靠點名
（舊版只驗 llama_bench_matrix.py 一個名字）—— 改成掃描 launcher_evidence() 判定的**直接啟動者**，
逐檔判紅；只 delegate（叫 llama_bench_matrix／harness.py bench）的不算，閘門在被叫的那支裡。

它只讀檔、不改檔，所以可以安全地在任何時候跑。
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

FILES = {
    "two_pass": "scripts/check/arm_two_pass.py",
    "watchdog": "scripts/check/lane_watchdog.py",
    "server_window": "scripts/check/server_window.py",
    "harness": "scripts/check/harness.py",
    "matrix": "scripts/check/llama_bench_matrix.py",
    "commit_bench": "scripts/check/commit_bench.py",
}

# §3 的 canonical 值（改這裡＝改規格，必須同時改 docs/MEASUREMENT_CONTRACT §3）
CANON_REPS = 3
CANON_LAUNCH_SWAP_MB = 2048.0
CANON_POOL_BYTES = 8589934592
# 只比對「旗標名」，不比對值 —— 各檔把它寫成 `"--prompt", prompt` 或 `--prompt 2048` 都算合格。
CANON_FLAGS = ("--prompt", "--gen", "--depths", "--warm-skip", "--ctx-size")

# ── §3.4.1 launcher 掃描（2026-10-01）──────────────────────────────────────────
# 「會起 llama-bench 的 runner」= binary 路徑先被綁成常數/變數、之後被拿去起子行程；
# 兩段都成立才算（保守、可解釋）：
#   shell ：`NAME=…llama-bench`（值結尾就是 binary）＋ `"$NAME" -x` 這種執行；
#   python：`NAME = …"llama-bench"`（大寫常數）＋ `[NAME`／`str(NAME)` 進指令列。
# 這種檔案必須引用 `budget_gate.sh`（或它的 Python 封裝 budget_gate_preflight）⇒ 否則逐檔判紅。
# 只 delegate（叫 llama_bench_matrix.py／harness.py bench）的不算 —— 閘門在被叫的那支裡。
# 已停用／一次性者在這裡登錄（值＝理由）；掃描只驗「檔案還在」，免得豁免變成孤兒。
LAUNCHER_EXEMPT: dict[str, str] = {
    "scripts/check/pin_abba.sh":
        "一次性 pin-profile A/B（2026-09-24）；形狀與 env 已凍結進 masscov_decode_shape.sh 的註解，"
        "無現行呼叫者。要重跑請先接 budget_gate.sh。",
    "scripts/check/pool_sweet_spot.sh":
        "一次性 pool 甜點掃描（2026-09-23）；它的環境已固化進 route_overlap_3prompt.sh／"
        "masscov_decode_shape.sh（兩者都已接閘），自己沒有現行呼叫者。",
    "scripts/check/sweet_abba.sh":
        "零引用的歷史 ABBA 工具（sweet-spot 一輪用完即棄）；要重跑請先接 budget_gate.sh。",
}
GATE_REF_RE = re.compile(r"budget_gate\.sh|budget_gate_preflight")
BIND_SHELL_RE = re.compile(r"^\s*([A-Za-z_]\w*)=\s*[\"']?[^\"'\s]*llama-bench[\"']?\s*$")
BIND_PY_RE = re.compile(r"^\s*([A-Z_]\w*)\s*=\s*[^#]*[\"'][^\"']*llama-bench[\"']\s*\)?\s*$")


def launcher_evidence(path: Path, text: str) -> str:
    """回傳「這個檔案會起 llama-bench」的證據（綁定行＋執行行）；空字串＝不是 launcher。"""
    lines = text.splitlines()
    if path.suffix == ".sh":
        for i, ln in enumerate(lines, 1):
            m = BIND_SHELL_RE.match(ln)
            if not m:
                continue
            use = re.search(r"\"?\$\{?" + re.escape(m.group(1)) + r"\}?\"?\s+-", text)
            if use:
                j = text[:use.start()].count("\n")
                return f"{ln.strip()}（L{i}）⇒ 執行 {lines[j].strip()[:70]}（L{j + 1}）"
        return ""
    if path.suffix == ".py":
        for i, ln in enumerate(lines, 1):
            m = BIND_PY_RE.match(ln)
            if not m:
                continue
            use = re.search(r"(?:subprocess\.\w+\([^\n]*|\[\s*(?:str\(\s*)?)"
                            + re.escape(m.group(1)) + r"\b", text)
            if use:
                j = text[:use.start()].count("\n")
                return f"{ln.strip()}（L{i}）⇒ 指令列 {lines[j].strip()[:70]}（L{j + 1}）"
        return ""
    return ""


def scan_launchers(root: Path) -> dict:
    """掃 <root>/scripts/**/*.sh|*.py；回傳 gated／ungated／stale_exempt 三份清單。"""
    out: dict = {"gated": [], "ungated": [], "stale_exempt": []}
    for p in sorted((root / "scripts").rglob("*")):
        if p.suffix not in (".sh", ".py") or "__pycache__" in str(p):
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        ev = launcher_evidence(p, text)
        if not ev:
            continue
        rel = str(p.relative_to(root))
        if rel in LAUNCHER_EXEMPT:
            continue
        if GATE_REF_RE.search(text):
            out["gated"].append(rel)
        else:
            out["ungated"].append((rel, ev))
    out["stale_exempt"] = [rel for rel in LAUNCHER_EXEMPT if not (root / rel).exists()]
    return out


def _search(text: str, pattern: str, cast=float):
    m = re.search(pattern, text)
    return cast(m.group(1)) if m else None


def check_all(files: dict[str, str], launchers: dict | None = None) -> list[tuple[str, bool, str]]:
    out: list[tuple[str, bool, str]] = []

    def add(name: str, ok: bool, detail: str):
        out.append((name, bool(ok), detail))

    tp = files.get("two_pass", "")
    wd = files.get("watchdog", "")
    hs = files.get("harness", "")

    # 1) 起跑閘 = 壓縮機安靜度（2026-09-26 取代 swap 存量）。三支閘都必須走共享模組，而不是各自
    #    再寫一條門檻 —— 「三處 swap 門檻各不相同」就是漂移的成因，只是這次換成了 flow。
    sw = files.get("server_window", "")
    into = {"arm_two_pass": "compressor_pressure" in tp,
            "lane_watchdog": "compressor_pressure" in wd,
            "server_window": "compressor_pressure" in sw}
    add("起跑閘 = 壓縮機安靜度（三支閘均走 compressor_pressure）",
        all(into.values()), f"{into}")

    # 1b) 回歸防線：swap 存量不得再當 decider（把 stock 當 flow 就紅）。
    tp_stock = '"swap_level"' not in tp
    wd_stock = "> args.max_swap_mb" not in wd
    add("swap 存量不得再當起跑閘（stock ≠ flow）",
        tp_stock and wd_stock,
        f"arm_two_pass 舊 swap_level 殘留={not tp_stock} / "
        f"lane_watchdog 舊 > max_swap_mb 比較殘留={not wd_stock}")

    # 2) reps 預設 = 3（否則 judge_artifact 的散度判據永不觸發）
    tp_reps = _search(tp, r'"--reps"[^)]*?default=([0-9]+)', int)
    hs_reps = _search(hs, r"_BENCH_DEFAULTS\s*=\s*dict\([^)]*?reps=([0-9]+)", int)
    add(f"reps 預設 = {CANON_REPS}（arm_two_pass 與 harness）",
        tp_reps == hs_reps == CANON_REPS,
        f"arm_two_pass={tp_reps} / harness={hs_reps}")

    # 3) G1 的看門狗偵測必須包含現行名字
    names = [n for n in ("auto_bench_watchdog", "watchdog_daemon", "lane_watchdog") if n in tp]
    has_daemon = "watchdog_daemon" in tp or "lane_watchdog" in tp
    add("G1 看門狗偵測含現行名字（watchdog_daemon / lane_watchdog）",
        has_daemon,
        f"偵測名單＝{'、'.join(names) if names else '（空）'}")

    # 4) §3.4.1：**掃描**所有會起 llama-bench 的 runner（不再只點名 llama_bench_matrix）
    lz = launchers or {}
    for rel, ev in lz.get("ungated", []):
        add(f"launcher 已接 budget_gate.sh（{rel}）", False, f"未接；證據：{ev}")
    for rel in lz.get("stale_exempt", []):
        add(f"launcher 豁免仍有效（{rel}）", False, "檔案不存在 ⇒ 從 LAUNCHER_EXEMPT 移除")
    gated = lz.get("gated", [])
    if lz.get("ungated") or lz.get("stale_exempt"):
        detail = f"未接 {len(lz.get('ungated', []))} 個（逐檔見上列）"
    else:
        detail = "已接 %d 個：%s" % (len(gated), "、".join(Path(g).name for g in gated) or "—")
        if LAUNCHER_EXEMPT:
            detail += "；豁免 %d 個：%s" % (len(LAUNCHER_EXEMPT),
                                          "、".join(Path(g).name for g in LAUNCHER_EXEMPT))
    add("launcher 掃描（§3.4.1：所有會起 llama-bench 的 runner）",
        not lz.get("ungated") and not lz.get("stale_exempt"), detail)

    # 5) pool = 8 GiB（cell 的一部分，不得為通過閘門而縮）
    tp_pool = _search(tp, r'CGC_EXPERT_CACHE_BYTES"\s*:\s*"([0-9]+)"', int)
    add(f"pool = {CANON_POOL_BYTES}（8 GiB）", tp_pool == CANON_POOL_BYTES, f"arm_two_pass PIN={tp_pool}")

    # 6) canonical cell 形狀必須在兩支 runner 裡都出現
    for key, label in (("commit_bench", "commit_bench.py"), ("two_pass", "arm_two_pass.py")):
        txt = files.get(key, "")
        missing = [f for f in CANON_FLAGS if f not in txt]
        add(f"canonical cell 旗標齊全於 {label}", not missing, f"缺 {missing}" if missing else "齊全")

    # 7) MTP 必須 ABSENT（= off）
    add("MTP 必須 ABSENT（PIN_ABSENT 含 CGC_SERVER_MTP）",
        "PIN_ABSENT" in tp and "CGC_SERVER_MTP" in tp,
        "PIN_ABSENT 缺 CGC_SERVER_MTP" if "PIN_ABSENT" not in tp else "ok")

    return out


def run(root: Path) -> int:
    files = {}
    for key, rel in FILES.items():
        p = root / rel
        files[key] = p.read_text(encoding="utf-8") if p.exists() else ""
    rows = check_all(files, scan_launchers(root))
    width = max(len(n) for n, _, _ in rows)
    for name, ok, detail in rows:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name:<{width}}  {detail}")
    bad = [n for n, ok, _ in rows if not ok]
    print()
    if bad:
        print(f"§3 尚未接線：{len(bad)}/{len(rows)} 項未達標（這正是 2026-09-25 的現狀）")
        print("⇒ 接線後本檢查自動轉綠；規格見 docs/MEASUREMENT_CONTRACT_2026-09-25.md §3")
        if any("launcher" in n for n in bad):
            print("⇒ launcher 類：把該 runner 接上 budget_gate.sh（或改叫已接閘門的 runner）；"
                  "已停用者請在 LAUNCHER_EXEMPT 附理由登錄（掃描仍會驗它沒被刪）。")
        return 1
    print(f"PASS — {len(rows)} 項全部與 §3 規格一致")
    return 0


def selftest() -> int:
    ok: list[tuple[str, bool]] = []

    def chk(n, c):
        ok.append((n, bool(c)))

    good_two_pass = (
        'ap.add_argument("--max-swap-mb", type=float, default=2048, help="x")\n'
        'ap.add_argument("--reps", type=int, default=3)\n'
        '"CGC_EXPERT_CACHE_BYTES": "8589934592",\n'
        'PIN_ABSENT = ["CGC_SERVER_MTP"]\n'
        '_procs_matching(["llama-bench", "llama-server", "watchdog_daemon"])\n'
        'add("swap_label", True, "x")  # compressor_pressure\n'
        '"--prompt", "--gen", "--depths", "--warm-skip", "--ctx-size"\n')
    good = {
        "two_pass": good_two_pass,
        "watchdog": "LAUNCH_SWAP_KILL = 2048.0\nSWAP_KILL = 3072.0\ncompressor_pressure\n",
        "server_window": "import compressor_pressure as cp\n",
        "harness": "_BENCH_DEFAULTS = dict(prompt=2048, gen=128, depths='512', reps=3,\n                       warm_skip=64, ctx_size=0, batch=5632, ubatch=5632)\n",
        "matrix": '. "${REPO}/scripts/check/budget_gate.sh"\n',
        "commit_bench": "--prompt 2048 --gen 128 --depths 512 --warm-skip 64 --ctx-size 0\n",
    }
    rows = check_all(good)
    chk("全綠：符合規格的樣本全 PASS", all(o for _, o, _ in rows))
    chk("樣本檢查項數 >= 8", len(rows) >= 8)

    bad = dict(good)
    bad["server_window"] = "# 沒接壓縮機閘\n"
    rows = check_all(bad)
    chk("閘沒走 compressor_pressure ⇒ 紅",
        any("起跑閘" in n and not o for n, o, _ in rows))

    bad = dict(good)
    bad["two_pass"] = good_two_pass.replace('add("swap_label", True, "x")',
                                            'add("swap_level", True, "x")')
    rows = check_all(bad)
    chk("swap 存量重新當閘（swap_level 回歸）⇒ 紅",
        any("stock" in n and not o for n, o, _ in rows))

    bad = dict(good)
    bad["watchdog"] = good["watchdog"] + "if s > args.max_swap_mb: bad.append(1)\n"
    rows = check_all(bad)
    chk("watchdog 重新用 > max_swap_mb 拒跑 ⇒ 紅",
        any("stock" in n and not o for n, o, _ in rows))

    bad = dict(good)
    bad["two_pass"] = good_two_pass.replace("--reps\", type=int, default=3", "--reps\", type=int, default=1")
    rows = check_all(bad)
    chk("reps=1 ⇒ 紅", any(n.startswith("reps 預設") and not o for n, o, _ in rows))

    bad = dict(good)
    bad["two_pass"] = good_two_pass.replace(", \"watchdog_daemon\"", "")
    rows = check_all(bad)
    chk("缺 watchdog_daemon ⇒ 紅", any("看門狗偵測" in n and not o for n, o, _ in rows))

    rows = check_all(good, {"gated": ["scripts/check/llama_bench_matrix.py"], "ungated": []})
    chk("launcher 全數已接 ⇒ 綠（掃描）", all(o for n, o, _ in rows if "launcher" in n))

    rows = check_all(good, {"gated": [], "ungated": [("scripts/check/new_tool.py", "BENCH=… ⇒ 執行")]})
    chk("新 launcher 未接 budget_gate ⇒ 紅（掃描）",
        any("launcher 已接" in n and not o for n, o, _ in rows))

    rows = check_all(good, {"gated": [], "ungated": [], "stale_exempt": ["scripts/check/gone.sh"]})
    chk("豁免指向已刪檔 ⇒ 紅", any("豁免" in n and not o for n, o, _ in rows))

    bad = dict(good)
    bad["two_pass"] = good_two_pass.replace("8589934592", "3221225472")
    rows = check_all(bad)
    chk("pool 被縮 ⇒ 紅", any("pool =" in n and not o for n, o, _ in rows))

    for n, c in ok:
        print(f"  [{'PASS' if c else 'FAIL'}] {n}")
    print(f"\n  selftest: {sum(1 for _, c in ok if c)}/{len(ok)}")
    return 0 if all(c for _, c in ok) else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--root", default=str(ROOT))
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    return run(Path(args.root))


if __name__ == "__main__":
    sys.exit(main())
