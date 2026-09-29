#!/usr/bin/env python3
"""L25-5 的收尾那一趟：**只有盒子真的乾淨才發車**，跑完直接把 m 交出來。

為什麼需要這支 —— `bench` 的盒況閘少一條腿
------------------------------------------------
`harness.py bench` 的盒況閘（`_box_gate`）只看 **thermal ＋ 壓縮機安靜度**。
`server_window.NEED_MB=8000` 那條記憶體線**只用在 `harness.py run`**，擋不到 `bench`。
⇒ 目前沒有任何閘擋得住「可回收不足就開 MTP-on」。代價有實測（k=1 之內、同一支臂）：

    可回收 7703 MB ⇒ ✅ tg 11.74／hit 91.4／wall 95.5 s
    可回收 6376 MB ⇒ ❌ thrash：pageins **+1174 萬**／8 分鐘、swap 8.33 GiB、>480 s 未完

所以本工具把 **7703 MB（已知最低的存活讀數）** 變成閘，而不是把 8000 當門檻 —— 8000 是
harness 自己的保守值，實測存活線比它低。

用法
----
    python3 scripts/check/l255_close.py --check      # 只問「現在能不能跑」，不發車
    python3 scripts/check/l255_close.py --go         # 檢查過了才發車；跑完自動跑 mtp_round_split
    python3 scripts/check/l255_close.py --selftest

判準（本檔的產物）
------------------
`--go` 成功時產物是 `docs/…` 以外的兩樣：`Backup/l255_close_<ts>/` 下的 run.json ＋ 原始 stderr，
以及 `mtp_round_split.py` 對它的輸出（k_eff／E／T_draft／m／v ＋ 五條閘）。
⛔ 本檔**不做**「MTP 加速 X%」的結論：ON/OFF 是不同輸出函數（M2 已 FAIL）。
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
PY = sys.executable

SURVIVAL_MB = 7703.0        # 已知最低的「存活」起跑讀數（k=1，on_k1 2026-09-28）
CRASH_MB = 6376.0           # 已知最高的「崩」起跑讀數（k=1，P4 2026-09-29）
NEED_MB = SURVIVAL_MB       # 本工具用的門檻：實測線，不是 harness 的 8000

ARM = ("prod-new:CGC_SERVER_MTP=1;CGC_SERVER_LAYER_CAPS=40-40:16;"
       "CGC_DRAFT_CTX_ALIGN=1;CGC_DRAFT_SMALL_BATCH=1;CGC_MTP_PERF=1")
CLI = ["--spec-type", "draft-mtp", "--spec-draft-n-max", "1"]
CHARTER = "scripts/check/charters/e-mtp-m-acc-2026-09-29.yaml"

THERMAL_KEY = "com.apple.system.thermalpressure"

# ⚠ 只認「0」。這個鍵的等級會隨負載變動；只有在 0（NOMINAL）時才當乾淨。
_THERMAL_RE = re.compile(r"^\s*\S+\s+(\d+)\s*$")


def thermal_level() -> int | None:
    """`notifyutil -g com.apple.system.thermalpressure` → 等級；讀不到回 None。

    ⛔ 讀不到 ≠ 乾淨。None 必須讓閘門 FAIL，否則「儀器壞了」會靜默變成「可以跑」。
    """
    try:
        out = subprocess.run(["notifyutil", "-g", THERMAL_KEY],
                             capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return None
    for line in out.splitlines():
        m = _THERMAL_RE.match(line)
        if m:
            return int(m.group(1))
    return None


def box_reading(need_mb: float = NEED_MB) -> dict:
    """一個 dict 裝齊四個判據；來源是既有儀器（server_window.decision ＋ notifyutil）。"""
    sys.path.insert(0, str(HERE))
    import server_window as sw  # noqa: E402
    d = sw.decision(need_mb=need_mb)
    return {
        "reclaimable_mb": float(d.get("reclaimable_mb") or 0.0),
        "need_mb": need_mb,
        "memory_ok": float(d.get("reclaimable_mb") or 0.0) >= need_mb,
        "port_held": bool(d.get("port_held")),
        "foreign_llama": bool(d.get("foreign_llama")),
        "compressor_ok": bool(d.get("compressor")),
        "compressor_reason": d.get("compressor_reason") or "",
        "thermal": thermal_level(),
        "launcher_class": d.get("launcher_class"),
        "launcher_free_pct": d.get("launcher_free_pct"),
        "binding": d.get("binding"),
        "agree": d.get("agree"),
    }


def verdict(reading: dict) -> tuple[bool, list[str], list[str]]:
    """(能不能跑, 每一條的說明, 擋下來的條目)。純函式 ⇒ 可被 selftest 餵合成讀數。"""
    terms = [
        ("記憶體", reading["memory_ok"],
         f"可回收 {reading['reclaimable_mb']:.0f} MB vs 存活線 {reading['need_mb']:.0f} MB"
         + (f"（實測崩點 {CRASH_MB:.0f}）" if reading["reclaimable_mb"] < CRASH_MB else "")),
        ("熱", reading["thermal"] == 0,
         f"thermalpressure={reading['thermal']}" + ("（NOMINAL）" if reading["thermal"] == 0
                                                    else "（非 0 ⇒ 只會把時間吹大）" if reading["thermal"] is not None
                                                    else "（讀不到 ⇒ 不當乾淨）")),
        ("壓縮機", reading["compressor_ok"], reading["compressor_reason"] or "quiet"),
        ("埠", not reading["port_held"], "8080 空" if not reading["port_held"] else "8080 被佔"),
        ("別的 llama", not reading["foreign_llama"],
         "無" if not reading["foreign_llama"] else "有 foreign llama 行程"),
    ]
    lines = [f"  {'✅' if ok else '⛔'} {name:10s} {note}" for name, ok, note in terms]
    blocked = [name for name, ok, _ in terms if not ok]
    return (not blocked), lines, blocked


def cmd_check(reading: dict | None = None) -> int:
    r = reading or box_reading()
    ok, lines, blocked = verdict(r)
    print("== L25-5 收尾趟：盒子閘 ==")
    for ln in lines:
        print(ln)
    print(f"  起跑讀數：launcher class={r['launcher_class']} free%={r['launcher_free_pct']} "
          f"binding={r['binding']} agree={r['agree']}")
    if ok:
        print("\nVERDICT: PASS —— 可以發車（`--go`）")
        return 0
    short = max(0.0, r["need_mb"] - r["reclaimable_mb"])
    print(f"\nVERDICT: SHORT —— 擋在 {blocked}；記憶體短 {short:.0f} MB")
    print("   ⛔ 不要用 CGC_WINDOW_OVERRIDE 硬跑：那正是 P4 的 thrash（+1174 萬 pageins／8 分鐘）。")
    return 1


def cmd_go() -> int:
    r = box_reading()
    if cmd_check(r) != 0:
        return 1
    ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    outdir = ROOT / "Backup" / f"l255_close_{ts}"
    outdir.mkdir(parents=True, exist_ok=True)
    run_json = outdir / "run.json"
    cmd = [PY, "scripts/check/harness.py", "bench", "--arm", ARM, *CLI,
           "--charter", CHARTER, "--json", str(run_json),
           "--workdir", str(Path("/tmp") / f"harness_bench_l255_{ts}"),
           "--arm-timeout", "420", "--stall-watch", "120"]
    print("\n== 發車 ==")
    print("   " + " ".join(cmd))
    env = dict(os.environ)
    env.setdefault("PYTHONPATH", "/opt/homebrew/lib/python3.14/site-packages")
    with open(outdir / "stdout.log", "w") as fh:
        rc = subprocess.call(cmd, stdout=fh, stderr=subprocess.STDOUT, env=env, cwd=str(ROOT))
    print(f"   harness rc={rc}；stdout → {outdir / 'stdout.log'}")
    print("\n== 拆分（m 與 v）==")
    return subprocess.call([PY, "scripts/check/mtp_round_split.py", str(run_json)], cwd=str(ROOT))


# ── selftest：餵合成讀數，驗閘門真的會紅 ──────────────────────────────────────
def _reading(mb=8000.0, thermal=0, comp=True, port=False, foreign=False) -> dict:
    return {"reclaimable_mb": mb, "need_mb": NEED_MB, "memory_ok": mb >= NEED_MB,
            "port_held": port, "foreign_llama": foreign, "compressor_ok": comp,
            "compressor_reason": "", "thermal": thermal, "launcher_class": "green",
            "launcher_free_pct": 70, "binding": "harness", "agree": True}


def cmd_selftest() -> int:
    bad = 0

    def chk(name, cond):
        nonlocal bad
        print(f"  {'OK  ' if cond else 'FAIL'} {name}")
        if not cond:
            bad += 1

    chk("存活讀數 7703 ⇒ PASS", verdict(_reading(mb=7703.0))[0])
    chk("7702 ⇒ SHORT（門檻是實測線，不是約數）", not verdict(_reading(mb=7702.0))[0])
    chk("P4 的 6376 ⇒ SHORT", not verdict(_reading(mb=6376.0))[0])
    chk("SHORT 的理由指名『記憶體』", "記憶體" in verdict(_reading(mb=6376.0))[2])
    chk("熱非 0 ⇒ SHORT", not verdict(_reading(thermal=1))[0])
    chk("熱讀不到（None）⇒ SHORT，不當乾淨", not verdict(_reading(thermal=None))[0])
    chk("壓縮機吵 ⇒ SHORT", not verdict(_reading(comp=False))[0])
    chk("埠被佔 ⇒ SHORT", not verdict(_reading(port=True))[0])
    chk("有 foreign llama ⇒ SHORT", not verdict(_reading(foreign=True))[0])
    # ⚠ 這條是這支工具存在的理由：記憶體單獨不過就必須擋住
    chk("只有記憶體不過也擋得住（bench 的閘擋不到這條）",
        not verdict(_reading(mb=6000.0))[0] and verdict(_reading(mb=6000.0))[2] == ["記憶體"])
    chk("熱解析：NOMINAL 行", thermal_line_ok("com.apple.system.thermalpressure 0"))
    chk("熱解析：壞行 ⇒ None", thermal_line_ok("garbage", want=None))
    chk("門檻＝實測存活線不是 8000", NEED_MB == SURVIVAL_MB and NEED_MB < 8000)
    print("\nSELFTEST " + ("OK" if bad == 0 else f"FAILED（{bad}）"))
    return 1 if bad else 0


def thermal_line_ok(text, want=0):
    for line in text.splitlines():
        m = _THERMAL_RE.match(line)
        if m:
            return int(m.group(1)) == want
    return want is None


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true", help="只問盒子能不能跑（0=可以，1=短少）")
    g.add_argument("--go", action="store_true", help="檢查過才發車；跑完自動拆 m/v")
    g.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return cmd_selftest()
    if a.check:
        return cmd_check()
    return cmd_go()


if __name__ == "__main__":
    sys.exit(main())
