#!/usr/bin/env python3
"""Who is compressing? — per-process attribution of one arm's compressor traffic.

Measured 2026-09-26 (global counters, same box, same config): one production arm moves 18–28 GiB
through the compressor, and the t/s read in that state fell 25% (11.36 → 8.31) at an *unchanged*
launch swap. A global counter cannot say whose pages those are, and the two answers lead to
opposite work:

    our own process      -> a knob on our allocation path (fill buffers, KV, logits) can fix it
    foreign / file cache -> our t/s is hostage to neighbours, and the fix is to stop creating the
                            pressure at all; touching the read path would buy nothing

`footprint --swapped -p PID` is the per-process witness: per region category it prints dirty and
(Swapped) bytes, without root, for our own children. So this tool runs ONE arm, samples vm_stat per
phase, and points footprint at the process that is actually doing the work — the one holding the
RSS, NOT the python parent (14.5 MB, the 2026-09-26 mistake this tool exists to not repeat). It is
captured several times mid-arm rather than once: (Swapped) is a stock, and a compressed page the
process later faulted back in would otherwise be missed.

Gates (fail-closed): a rival llama process refuses the run, and so does a busy compressor — the
shared `compressor_pressure` gate, because that is the variable that actually moves the numbers.
Everything else (swap stock, reclaimable, thermal) is recorded as a label via the shared window
probe, so the artifact answers "in what box state was this taken".
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import compressor_pressure as cp          # noqa: E402  the compressor gate, single source
import server_window as sw                # noqa: E402  window provenance + the gated-launch token
from mem_oversub_probe import guard, vm_snapshot  # noqa: E402  counters, one parser

ARM_CMD = [sys.executable, str(HERE / "llama_bench_matrix.py")]
DEFAULT_CELL = ["--prompt", "2048", "--gen", "128", "--depths", "512", "--reps", "3",
                "--ctx-size", "0", "--warm-skip", "64"]


# ── parsing (pure, so the fixtures can drive it) ─────────────────────────────────────────────
def human_to_mib(s: str) -> float | None:
    """`footprint` sizes: "0 B", "1472 KB", "1500 MB", "1.2 GB"."""
    m = re.fullmatch(r"([0-9.]+)\s*([BKMGTP]?)B?", s.strip())
    if not m:
        return None
    v = float(m.group(1))
    unit = m.group(2)
    return v * {"": 1 / 2 ** 20, "B": 1 / 2 ** 20, "K": 1 / 1024, "M": 1.0,
                "G": 1024.0, "T": 1024.0 ** 2, "P": 1024.0 ** 3}[unit]


def parse_footprint(text: str) -> dict:
    """footprint --swapped output -> per-category dirty/(Swapped) MiB, or a stated refusal.

    Shape (verified on this box):
        Dirty  (Swapped)      Clean  Reclaimable    Regions    Category
        ---        ---        ---          ---        ---    ---
      1500 MB        0 B        0 B          0 B         13    MALLOC_LARGE
    """
    out: dict = {"rows": [], "total_dirty_mib": 0.0, "total_swapped_mib": 0.0, "parse": "ok",
                 "header": None}
    lines = text.splitlines()
    head_i = next((i for i, l in enumerate(lines) if "(Swapped)" in l), None)
    if head_i is None:
        out["parse"] = "refused: no (Swapped) column in footprint output"
        return out
    out["header"] = lines[head_i].strip()
    for line in lines[head_i + 1:]:
        if not line.strip() or set(line.strip()) <= {"-", " "}:
            continue
        cols = re.split(r"\s{2,}", line.strip())
        if len(cols) < 3:
            continue
        dirty, swapped = human_to_mib(cols[0]), human_to_mib(cols[1])
        if dirty is None or swapped is None:
            continue
        out["rows"].append({"category": cols[-1], "dirty_mib": dirty, "swapped_mib": swapped})
        out["total_dirty_mib"] += dirty
        out["total_swapped_mib"] += swapped
    if not out["rows"]:
        out["parse"] = "refused: (Swapped) header found but no parsable rows"
    return out


def census() -> list[dict]:
    """Every process, by RSS. The one that matters is the one holding the memory, not the parent."""
    p = subprocess.run(["ps", "-Ao", "pid=,rss=,args="], capture_output=True, text=True).stdout
    rows = []
    for line in p.splitlines():
        pid, _, rest = line.strip().partition(" ")
        rss, _, args = rest.strip().partition(" ")
        if not pid.isdigit() or not args:
            continue
        if int(pid) == __import__("os").getpid():
            continue
        rows.append({"pid": int(pid), "rss_mib": int(rss) / 1024.0,
                     "name": Path(args.split()[0]).name})
    rows.sort(key=lambda r: -r["rss_mib"])
    return rows


def worker_of(c: list[dict]) -> dict | None:
    """The process doing the arm's work: llama-bench, whichever one holds the RSS."""
    for r in c:
        if r["name"] == "llama-bench":
            return r
    return None


def rates(series: list[dict], page: int) -> dict:
    """MiB/s per counter between the first and last sample of a phase (None = unreadable)."""
    if len(series) < 2:
        return {}
    dt = series[-1]["t"] - series[0]["t"]
    if dt <= 0:
        return {}
    out = {}
    for k in ("Compressions", "Decompressions", "Swapouts", "Swapins", "Pageins",
              "Pages stored in compressor", "Pages occupied by compressor"):
        if k not in series[0]["vm"] or k not in series[-1]["vm"]:
            continue
        out[k] = (series[-1]["vm"][k] - series[0]["vm"][k]) * page / 2 ** 20 / dt
    return out


def selftest() -> int:
    ok = []

    def chk(n, c):
        ok.append((n, bool(c)))

    real = """\
================================================================================
llama-bench [4242]: 64-bit    Footprint: 7521 MB (16384 bytes per page)
================================================================================

  Dirty  (Swapped)      Clean  Reclaimable    Regions    Category
    ---        ---        ---          ---        ---    ---
  6.9 GB        0 B        0 B          0 B        401    MALLOC_LARGE
  271 MB        0 B        0 B          0 B         31    IOAccelerator
  112 MB        0 B        0 B          0 B          1    page table
   12 MB        0 B        2 MB          0 B         88    __DATA
"""
    r = parse_footprint(real)
    chk("parses footprint rows", r["parse"] == "ok" and len(r["rows"]) == 4)
    chk("sums the (Swapped) column", r["total_swapped_mib"] == 0.0)
    chk("dirty total in MiB", abs(r["total_dirty_mib"] - (6.9 * 1024 + 271 + 112 + 12)) < 1.0)

    r = parse_footprint(real.replace(" 6.9 GB        0 B", " 6.9 GB     1200 MB"))
    chk("a nonzero (Swapped) column is read as bytes, not as quiet",
        abs(r["total_swapped_mib"] - 1200.0) < 0.01)

    r = parse_footprint("Footprint: 1 MB\n  Dirty Clean Regions Category\n  1 MB   0 B   1  x\n")
    chk("no (Swapped) column ⇒ REFUSED (a stock we cannot read is not a zero)",
        r["parse"].startswith("refused"))

    r = parse_footprint("  Dirty  (Swapped)      Clean  Reclaimable    Regions    Category\n"
                        "    ---        ---        ---          ---        ---    ---\n")
    chk("header but no rows ⇒ REFUSED (absence is not zero)", r["parse"].startswith("refused"))

    for s, want in (("0 B", 0.0), ("1472 KB", 1.4375), ("1500 MB", 1500.0), ("1.5 GB", 1536.0)):
        chk(f"size {s} -> {want} MiB", abs(human_to_mib(s) - want) < 1e-6)
    chk("unparsable size -> None", human_to_mib("n/a") is None)

    for n, c in ok:
        print(f"  [{'PASS' if c else 'FAIL'}] {n}")
    print(f"\n  selftest: {sum(1 for _, c in ok if c)}/{len(ok)}")
    return 0 if all(c for _, c in ok) else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--arm", default="prod-new")
    ap.add_argument("--cell", default=" ".join(DEFAULT_CELL),
                    help="llama-bench shape flags (the authoritative cell by default)")
    ap.add_argument("--out", default="", help="artifact directory (required to run an arm)")
    ap.add_argument("--interval", type=float, default=2.5)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    if not a.out:
        ap.error("--out is required to run an arm")

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    others = guard()
    if others:
        print("REFUSED: another llama measurement process is alive:")
        for l in others:
            print("   " + l)
        return 2
    cq_ok, cq_why = cp.require(where="arm_pressure_attrib")
    if not cq_ok:
        print(f"REFUSED: {cq_why}")
        return 2
    sw.record(where="arm_pressure_attrib", gated=True)      # records this box state with the run
    print(f"WINDOW  compressor={cq_why}")

    page = int(cp.page_bytes())
    series: list[dict] = []
    stop = threading.Event()
    captures: list[dict] = []

    def sample():
        while not stop.is_set():
            t, vm, swap = vm_snapshot()
            c = census()
            w = worker_of(c)
            series.append({"t": t, "vm": vm, "swap": swap, "worker_pid": (w or {}).get("pid"),
                           "top": c[:6]})
            # Several mid-arm captures: (Swapped) is a stock, so one sample can miss a page that
            # was compressed and then faulted back in. Take up to 4, ~25 s apart, from the worker.
            if w and (not captures or time.time() - captures[-1]["t"] > 25) and len(captures) < 4:
                fp = subprocess.run(["/usr/bin/footprint", "--swapped", "-p", str(w["pid"])],
                                    capture_output=True, text=True)
                vm_ = subprocess.run(["/usr/bin/vmmap", "-summary", str(w["pid"])],
                                     capture_output=True, text=True)
                (out / f"footprint_{w['pid']}_{len(captures)}.txt").write_text(fp.stdout + fp.stderr)
                (out / f"vmmap_{w['pid']}_{len(captures)}.txt").write_text(vm_.stdout + vm_.stderr)
                cap = {"t": time.time(), "pid": w["pid"], "rss_mib": w["rss_mib"],
                       "parse": parse_footprint(fp.stdout), "footprint_rc": fp.returncode}
                # The control arm for "whose pages": the box's biggest FOREIGN process, captured at
                # the same instant. Without it, "our process carries compressed pages" is an
                # observation; with it, "and the neighbour does not" is a comparison.
                fo = next((r for r in c if r["pid"] != w["pid"]), None)
                if fo:
                    fp2 = subprocess.run(["/usr/bin/footprint", "--swapped", "-p", str(fo["pid"])],
                                         capture_output=True, text=True)
                    (out / f"footprint_foreign_{fo['name']}_{len(captures)}.txt").write_text(
                        fp2.stdout + fp2.stderr)
                    cap["foreign"] = {"name": fo["name"], "pid": fo["pid"], "rss_mib": fo["rss_mib"],
                                      "parse": parse_footprint(fp2.stdout)}
                captures.append(cap)
            stop.wait(a.interval)

    th = threading.Thread(target=sample, daemon=True)
    th.start()

    cmd = ARM_CMD + ["--arms", a.arm] + a.cell.split() + ["--workdir", str(out),
                                                          "--json", str(out / "summary.json")]
    t0 = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True)
    dur = time.time() - t0
    stop.set()
    th.join(timeout=max(5.0, a.interval * 2))
    (out / "arm.log").write_text(proc.stdout + proc.stderr)

    first_worker = next((i for i, s in enumerate(series) if s.get("worker_pid")), None)
    pre = series[:first_worker] if first_worker else series
    live = series[first_worker:] if first_worker else []
    shape = {}
    try:
        sm = json.loads((out / "summary.json").read_text())
        runs = sm if isinstance(sm, list) else (sm.get("runs") or [])
        rows = [r for run in runs for r in (run.get("rows") or [])]
        shape = {("pp" if r.get("n_prompt") else "tg"): r.get("avg_ts") for r in rows}
        shape["build_commit"] = next((r.get("build_commit") for r in rows if r.get("build_commit")),
                                     None)
        shape["spec_type"] = runs[0].get("spec_type") if runs else None
    except Exception as e:  # noqa: BLE001  a missing summary is stated, not guessed
        shape = {"error": f"summary.json unreadable: {e}"}
    report = {
        "arm": a.arm, "cell": a.cell, "seconds": dur, "rc": proc.returncode, "shape": shape,
        "window": {"compressor": cq_why, "provenance": sw.provenance()},
        "rates_mib_s": {"before_worker": rates(pre, page), "with_worker": rates(live, page),
                        "whole_arm": rates(series, page)},
        "captures": captures,
        "top_at_end": series[-1]["top"] if series else [],
        "samples": series,
    }
    (out / "attrib.json").write_text(json.dumps(report, indent=1, default=str) + "\n")

    whole = report["rates_mib_s"]["whole_arm"].get("Compressions", float("nan"))
    print(f"\nARM {a.arm} rc={proc.returncode} in {dur:.0f}s")
    print(f"compressions over the arm: {whole:,.1f} MiB/s")
    for c in report["rates_mib_s"]["with_worker"].items():
        print(f"   with worker  {c[0]:<28} {c[1]:10.1f} MiB/s")
    st = report["rates_mib_s"]["whole_arm"].get("Pages stored in compressor")
    if st is not None:
        print(f"   Pages stored in compressor: {st:+.1f} MiB/s "
              f"({'accumulating' if st > 1 else 'CHURN, not accumulation'})")
    print(f"   shape: pp={shape.get('pp')} tg={shape.get('tg')} build={shape.get('build_commit')} "
          f"spec_type={shape.get('spec_type')}")
    for cap in captures:
        p = cap["parse"]
        if p["parse"] != "ok":
            print(f"   OWN  {cap['pid']} @{cap['rss_mib']:.0f} MB RSS: {p['parse']}")
        else:
            cats = ", ".join(f"{r['category']}={r['swapped_mib']:,.0f}" for r in
                            sorted(p["rows"], key=lambda r: -r["swapped_mib"])[:2])
            print(f"   OWN  {cap['pid']} @{cap['rss_mib']:.0f} MB RSS: footprint "
                  f"{p['total_dirty_mib']:,.0f} MiB, (Swapped) {p['total_swapped_mib']:,.1f} MiB  "
                  f"[{cats}]")
        f = cap.get("foreign")
        if f:
            fp_ = f["parse"]
            met = (f"footprint {fp_['total_dirty_mib']:,.0f} MiB, (Swapped) "
                   f"{fp_['total_swapped_mib']:,.1f} MiB") if fp_["parse"] == "ok" else fp_["parse"]
            print(f"   NEIGHBOUR {f['name']} @{f['rss_mib']:.0f} MB RSS: {met}")
    if series:
        print("   foreign top: " + ", ".join(f"{r['name']}({r['rss_mib']:.0f}MB)"
                                             for r in series[-1]["top"][:4]))
    return 0 if proc.returncode == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
