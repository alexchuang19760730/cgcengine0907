#!/usr/bin/env python3
"""Is the COMPRESSOR quiet? — the window question that replaces `swap_used < 2048 MiB`.

The launch gates on this box asked about a STOCK (how many MiB are sitting in swap). The variable
that moves t/s is a FLOW (how many MiB/s the memory compressor is moving). Both were measured on
this machine, 2026-09-26:

    idle box, swap 7993 MiB — 3.9x over the old 2048 line   0.00 MiB/s compressions, 0.00 pageouts
    one 3-rep production arm, same box, same config          ~150 MiB/s compressions

Four orders of magnitude apart, and the stock says nothing about which regime you are in. That is
why "wait for swap to fall" was never a mechanism: `vm.swapusage` counts pages pushed out hours ago
and still sitting on disk; the compressor's *rate* is what is happening now. A box with 8 GB of
stale swap and a quiet compressor is a clean box; a box with 2 GB of swap and a busy compressor is
not — and only the second one costs t/s (measured: same config, same launch swap, tg 11.36 vs 8.31,
compressor traffic 32 GB vs 127 GB per arm).

Single source: this module. It reuses `mem_oversub_probe.vm_snapshot`/`delta` for the counters and
`memory_pressure._page_size_kb` for the page size (16 KiB here) rather than parsing `vm_stat` a
third time or hard-coding 4096 — that constant was 4x wrong on this box and the error lands
directly in these rates.

Fail-closed, and "unknown" is its own verdict: a probe that cannot read the counters must not
report "quiet". The threshold is deliberately loose (1 MiB/s against a busy side of ~150 MiB/s);
this gate is here to separate regimes, not to shave decimals.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from mem_oversub_probe import delta, vm_snapshot  # noqa: E402  the counters, one parser

# Calibrated 2026-09-26 from the two measured regimes above (§docstring). Busy = ~150 MiB/s.
COMPRESSIONS_MIB_S = 1.0
PAGEOUTS_MIB_S = 1.0

DEFAULT_SECONDS = 12.0
DEFAULT_INTERVAL = 1.5

# What decides, and what is only carried as a label. The stock is a label on purpose: it is the
# number the old gate refused on, and it is printed with every reading so products can still show
# it, but it never decides.
FLOW_KEYS = (("Compressions", "compressions"), ("Pageouts", "pageouts"))
LABEL_KEYS = (("Swapins", "swapins"), ("Swapouts", "swapouts"), ("Pageins", "pageins"),
              ("Decompressions", "decompressions"))


def page_bytes() -> float:
    """Host page size. Asks the kernel; `memory_pressure` is the repo's single source for it."""
    try:
        from memory_pressure import _page_size_kb
        return _page_size_kb() * 1024.0
    except Exception:  # noqa: BLE001  -- importable in-repo; the fallback is announced, not silent
        import subprocess
        try:
            out = subprocess.run(["sysctl", "-n", "hw.pagesize"],
                                 capture_output=True, text=True, timeout=5)
            return float(out.stdout.strip())
        except Exception:  # noqa: BLE001
            print("[compressor] page size unknown; assuming 16 KiB (Apple Silicon)", file=sys.stderr)
            return 16384.0


def rate_mib_s(series: list[dict], key: str, pbytes: float) -> float | None:
    """Pages/sample -> MiB/s, or None when the series cannot support the claim."""
    d = delta(series, key)
    if d is None:
        return None
    dt = series[-1]["t"] - series[0]["t"]
    if dt <= 0:
        return None
    return d * pbytes / 2 ** 20 / dt


def classify(series: list[dict], pbytes: float, *,
             comp_max: float = COMPRESSIONS_MIB_S,
             pageout_max: float = PAGEOUTS_MIB_S) -> dict:
    """The whole decision, as a pure function of the sampled series (so fixtures can drive it)."""
    out: dict = {
        "samples": len(series), "seconds": (series[-1]["t"] - series[0]["t"]) if len(series) > 1 else 0.0,
        "page_size": pbytes, "swap_used_mib": series[-1]["swap"] if series else None,
        "thresholds": {"compressions_mib_s": comp_max, "pageouts_mib_s": pageout_max},
        "rates_mib_s": {}, "quiet": False, "reason": "",
    }
    if len(series) < 2:
        out["reason"] = "unknown: fewer than 2 samples — cannot measure a rate"
        return out
    flows: dict[str, float] = {}
    for key, name in FLOW_KEYS:
        r = rate_mib_s(series, key, pbytes)
        if r is None:
            out["reason"] = f"unknown: vm_stat has no {key} counter — fail-closed"
            return out
        flows[name] = r
    labels = {}
    for key, name in LABEL_KEYS:
        r = rate_mib_s(series, key, pbytes)
        if r is not None:
            labels[name] = r
    out["rates_mib_s"] = {**flows, **labels}
    busy = []
    if flows["compressions"] > comp_max:
        busy.append(f"compressions {flows['compressions']:.2f} > {comp_max:.2f} MiB/s")
    if flows["pageouts"] > pageout_max:
        busy.append(f"pageouts {flows['pageouts']:.2f} > {pageout_max:.2f} MiB/s")
    out["quiet"] = not busy
    out["reason"] = ("; ".join(busy) if busy else
                     f"quiet (compressions {flows['compressions']:.2f}, "
                     f"pageouts {flows['pageouts']:.2f} MiB/s)")
    return out


def measure(seconds: float = DEFAULT_SECONDS, interval: float = DEFAULT_INTERVAL) -> dict:
    """Sample the box for `seconds` and decide. One sample up front, then `interval` apart."""
    series: list[dict] = []
    t0 = time.time()
    while True:
        t, vm, swap = vm_snapshot()
        series.append({"t": t, "vm": vm, "swap": swap})
        if t - t0 >= seconds:
            break
        time.sleep(interval)
    return classify(series, page_bytes())


def quiet(seconds: float = DEFAULT_SECONDS) -> tuple[bool, str]:
    """(is-quiet, reason). Same shape as `server_window.quiet()`, for the same call sites."""
    try:
        m = measure(seconds=seconds)
    except Exception as e:  # noqa: BLE001  fail-closed: no reading is not a pass
        return False, f"unknown: compressor probe error: {e}"
    return bool(m["quiet"]), m["reason"]


def require(seconds: float = DEFAULT_SECONDS, *, where: str = "launch") -> tuple[bool, str]:
    """Launcher-facing spelling of `quiet()`; the reason names the gate that refused."""
    ok, reason = quiet(seconds=seconds)
    return ok, (reason if ok else f"compressor busy at {where}: {reason}")


# ── selftest ────────────────────────────────────────────────────────────────────────────────
def _series(pairs: list[tuple[str, int]], *, dt: float = 1.0, swap: float = 0.0,
            drop: str | None = None) -> list[dict]:
    """Synthetic series: {(counter, value)} at t = i*dt. `drop` removes a counter (fixture)."""
    out = []
    for i, (key, val) in enumerate(pairs):
        vm = {key: float(val)}
        if drop != key:
            vm = {drop: 0.0, key: float(val)} if drop else vm
        out.append({"t": i * dt, "vm": vm, "swap": swap})
    return out


def selftest() -> int:
    p = 16384.0
    ok = []

    def chk(name: str, cond: bool):
        ok.append((name, bool(cond)))

    idle = [{"t": 0.0, "vm": {"Compressions": 100.0, "Pageouts": 50.0}, "swap": 7993.0},
            {"t": 12.0, "vm": {"Compressions": 100.0, "Pageouts": 50.0}, "swap": 7993.0}]
    r = classify(idle, p)
    chk("idle = quiet", r["quiet"])
    chk("idle reason names the rates", "quiet" in r["reason"])

    # THE fixture this gate exists for: the stock is 3.9x over the old line and must NOT decide.
    big_stock = [{"t": 0.0, "vm": {"Compressions": 0.0, "Pageouts": 0.0}, "swap": 7993.0},
                 {"t": 12.0, "vm": {"Compressions": 0.0, "Pageouts": 0.0}, "swap": 7993.0}]
    r = classify(big_stock, p)
    chk("huge swap stock + zero flow = QUIET (the stock must not decide)", r["quiet"])
    chk("the stock is still carried as a label", r["swap_used_mib"] == 7993.0)

    busy = [{"t": 0.0, "vm": {"Compressions": 0.0, "Pageouts": 0.0}, "swap": 0.0},
            {"t": 2.0, "vm": {"Compressions": int(150 * 2 ** 20 / p * 2), "Pageouts": 0.0}, "swap": 0.0}]
    r = classify(busy, p)
    chk("busy compressions ⇒ NOT quiet (must-fail)", not r["quiet"])
    chk("busy rates are MiB/s not pages", abs(r["rates_mib_s"]["compressions"] - 150.0) < 0.5)

    pageout_only = [{"t": 0.0, "vm": {"Compressions": 0.0, "Pageouts": 0.0}, "swap": 0.0},
                    {"t": 2.0, "vm": {"Compressions": 0.0, "Pageouts": int(3 * 2 ** 20 / p * 2)},
                     "swap": 0.0}]
    r = classify(pageout_only, p)
    chk("pageouts alone ⇒ NOT quiet (must-fail)", not r["quiet"])

    missing = [{"t": 0.0, "vm": {"Pageouts": 0.0}, "swap": 0.0},
               {"t": 12.0, "vm": {"Pageouts": 0.0}, "swap": 0.0}]
    r = classify(missing, p)
    chk("missing Compressions ⇒ NOT quiet, called unknown (fail-closed)",
        not r["quiet"] and "unknown" in r["reason"])

    r = classify(idle[:1], p)
    chk("one sample ⇒ NOT quiet, called unknown (fail-closed)",
        not r["quiet"] and "unknown" in r["reason"])

    r = classify([{"t": 5.0, "vm": {"Compressions": 0.0, "Pageouts": 0.0}, "swap": 0.0},
                  {"t": 5.0, "vm": {"Compressions": 0.0, "Pageouts": 0.0}, "swap": 0.0}], p)
    chk("zero dt ⇒ NOT quiet (no rate can be claimed)", not r["quiet"])

    for n, c in ok:
        print(f"  [{'PASS' if c else 'FAIL'}] {n}")
    print(f"\n  selftest: {sum(1 for _, c in ok if c)}/{len(ok)}")
    return 0 if all(c for _, c in ok) else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--seconds", type=float, default=DEFAULT_SECONDS,
                    help=f"sampling window (default {DEFAULT_SECONDS:g}s)")
    ap.add_argument("--json", default=None, help="write the reading here")
    ap.add_argument("--require", action="store_true",
                    help="exit 1 unless quiet (launcher semantics)")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    m = measure(seconds=a.seconds)
    if a.json:
        Path(a.json).parent.mkdir(parents=True, exist_ok=True)
        Path(a.json).write_text(json.dumps(m, indent=2) + "\n")
    print(("QUIET  " if m["quiet"] else "BUSY   ") + m["reason"] +
          f"   [swap stock {m['swap_used_mib']:.0f} MiB, {m['samples']} samples over "
          f"{m['seconds']:.1f}s]")
    return 0 if m["quiet"] else 1


if __name__ == "__main__":
    sys.exit(main())
