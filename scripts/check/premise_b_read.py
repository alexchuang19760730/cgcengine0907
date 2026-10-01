#!/usr/bin/env python3
"""Read the premise-B churn counters out of a server log (zero GPU, no server).

WHAT IT ANSWERS
---------------
G1's S2-vs-S3 decision turns on one quantity: between two steps, does the mapping of an expert id
the consumer actually READS move? `CGC-S1: TABLE-CHURN` counts exactly that, in two places:

  * the teardown line -- `llama_expert_cache: S1 slot-table: publishes=.. consumed_changed=..
    consumed_unchanged_publishes=..` -- is the RATE (how many publishes moved / how many did not)
  * the per-graph line -- `CGC-S1: TABLE-CHURN graph=.. ntok=.. publishes=.. changed_entries=..` --
    is the whole-table count per step, and it carries `ntok` so the population can be split.

WHY IT REFUSES INSTEAD OF GUESSING
----------------------------------
Three ways to get a number that looks like an answer and is not, all seen on this tree:

  1. the knob was off. Then the counters are all zero AND the teardown says
     `not instrumented` -- the source keeps a default 0 and a measured 0 distinguishable on purpose
     (llama-expert-cache.h:346-354), and this tool keeps the same distinction instead of printing 0%.
  2. the gate did not fire on the steps that matter. The gate is
     `cgc_is_decode_graph(n_tokens, cgc_pool_max_tokens())` (widened 2026-09-20; it used to be
     `n_tokens == 1`, which fired on 0 of 128 delivery-shape steps).
  3. the run is too short. 35 publishes is not a sample, so there is a `--min-publishes` floor.

EDGE WORTH KNOWING: `consumed_changed` is a count of PUBLISHES (i.e. of (step, layer) pairs, since
each served layer publishes once per step), not of steps. Divide by the layer count (39 on this
model: 40 layers minus layer 0, which keeps its host leaf) to get steps. The tool prints both.

EDGE THAT BITES (2026-09-28) -- the by-ntok split MIXES CONTEXTS
----------------------------------------------------------------
The `_by_ntok` counter maps are keyed by WIDTH ONLY and carry no `is_draft` guard
(llama-context.cpp:5678-5687 add to them unconditionally), while the per-graph line's SEL-DRIFT
loop DOES skip draft entries (`if (kv.first >= 100000) continue;`, :5604). The draft context and the
main context publish into ONE shared cache, so on an MTP-on run the `ntok=1` row is the DRAFT
context -- not the delivery decode step.

Measured proof, from the 2026-09-20 logs (the only churn-instrumented ones on this tree):

    ntok=1 denominator  612 / 306
    `draft: calls`      612 / 306      <-- equal, exactly, in both runs
    612 + 8160 + 4159 = 12931 ~= publishes=12971   <-- one cache, both contexts

So `ntok=1 0/612 = 0.0%` is a draft measurement. Labelling that row "decode" is how a real number
gets read as an answer to a question nobody measured. This tool now says which context each width
came from, and `delivery_ntok1_measured` is False unless the run was MTP off.

Usage:
    python3 scripts/check/premise_b_read.py Backup/cgc_logs/llama_server_*.log
    python3 scripts/check/premise_b_read.py --newest 3
    python3 scripts/check/premise_b_read.py --self-test
"""
import argparse
import glob
import os
import re
import statistics as st
import sys

TEARDOWN = re.compile(
    r"S1 slot-table: publishes=(\d+) clamped_selected=(\d+) clamped_table=(\d+)"
    r" changed_entries=(\d+) consumed_changed=(\d+) consumed_unchanged_publishes=(\d+)")
TEARDOWN_OLD = re.compile(   # the pre-2026-09-15 shape, no selected-subset split
    r"S1 slot-table: publishes=(\d+) clamped=(\d+) changed_entries=(\d+) unchanged_publishes=(\d+)")
PERGRAPH = re.compile(r"CGC-S1: TABLE-CHURN graph=(\d+) ntok=(\d+) publishes=(\d+) changed_entries=(\d+)")
PERGRAPH_OLD = re.compile(r"CGC-S1: TABLE-CHURN graph=(\d+) publishes=(\d+) changed_entries=(\d+)")
# [CGC 2026-09-28] `(.*)$` only matched when the by-ntok line happened to be the LAST line of the
# file (no re.M, and `.` will not cross a newline) -- which is true of the 2026-09-20 logs and is why
# it went unnoticed, but false for any log that prints anything after it (the ENTRY-granularity line
# is printed right after this one whenever it is populated). `[^\n]*` is the intended scope.
BYNTOK = re.compile(r"S1 churn by ntok \(consumed subset\):([^\n]*)")
TOKEN = re.compile(r"ntok=(\d+) (\d+)/(\d+)=([0-9.]+)%")
# [CGC 2026-09-28] Which CONTEXT a width came from. `draft: calls` lives on the "MTP fast path"
# line; its presence is what separates an MTP-on ntok=1 (draft) from an MTP-off ntok=1 (delivery).
MTPFAST  = re.compile(r"MTP fast path: calls=(\d+)")
MTPDRAFT = re.compile(r"draft: calls=(\d+)")
KIND_LABEL = {
    "draft":  "DRAFT context (MTP on) -- NOT the delivery decode step",
    "mixed":  "MIXED draft + main -- quote as neither",
    "main":   "main context, MTP off -- THIS is the delivery decode step",
    "verify": "main context, MTP verify step",
    "prefill": "cgc_pool_max_tokens(), chunked-prefill block",
    "unknown": "unattributed",
}
DECPROF = re.compile(r"CGC-DECPROF: step=\d+ segs=\d+ layers=(\d+) total=[0-9.]+ ms \| "
                     r"wait=[0-9.]+ \([0-9.]+%\) cb=[0-9.]+ \([0-9.]+%\) submit=[0-9.]+ \([0-9.]+%\) "
                     r"ntok=(\d+)")
N_LAYERS = 39          # served layers that publish: il = 1..39; layer 0 keeps the host leaf


def read(path, min_publishes=100):
    """-> dict. `refuse` is set instead of a rate whenever the number would be unearned."""
    txt = open(path, errors="replace").read()
    m = TEARDOWN.search(txt)
    out = {"path": path, "refuse": None, "shape": "new" if m else "old"}
    if not m:
        m = TEARDOWN_OLD.search(txt)
        if not m:
            out["refuse"] = ("no S1 slot-table teardown line at all -- either the run never "
                             "published (CGC_SLOT_TABLE_GPU off) or it was killed before teardown "
                             "(SIGTERM is not a teardown)")
            return out
        pub, cl, ce, un = (int(x) for x in m.groups())
        cc = cu = None
    else:
        pub, cs, ct, ce, cc, cu = (int(x) for x in m.groups())

    out.update(publishes=pub, changed_entries=ce)
    # A server that died of SIGTERM is not a sample: this repo's rule is that the marker means
    # external hunting (watchdog GGML_ABORT and OOM are different things). It matters HERE more than
    # usual because I killed a server with `pkill -TERM` myself on 2026-09-20 to abort a run whose
    # design turned out to be useless -- and a graceful kill STILL prints the teardown line, so the
    # counters look perfectly normal.
    out["sigterm"] = "Received SIGTERM" in txt
    # per-graph lines carry ntok (only after the 2026-09-20 widening)
    per = [dict(graph=int(a), ntok=int(b), publishes=int(c), changed=int(d))
           for a, b, c, d in PERGRAPH.findall(txt)]
    out["per_graph"] = per
    out["per_graph_has_ntok"] = bool(per)
    if not per:
        per_old = [dict(graph=int(a), ntok=None, publishes=int(b), changed=int(c))
                   for a, b, c in PERGRAPH_OLD.findall(txt)]
        out["per_graph"] = per_old
    # [CGC 2026-09-20 §G1-B] the per-ntok split, when the binary has it. THIS is the line that
    # answers premise B for the delivery configuration: `consumed_total = n_tokens * n_expert_used`,
    # so the single scalar above is a mixture whose weights the reader cannot recover.
    out["by_ntok"] = {}
    mb = BYNTOK.search(txt)
    if mb:
        for nt, ch, tot, pct in TOKEN.findall(mb.group(1)):
            out["by_ntok"][int(nt)] = dict(changed=int(ch), total=int(tot), pct=float(pct))
    # [CGC 2026-09-28] Attribute each width to a context. `draft: calls` is the ONLY in-log evidence
    # that separates an MTP-on ntok=1 (draft) from an MTP-off ntok=1 (delivery), and the counter maps
    # themselves cannot: they are keyed by width, and both contexts feed them.
    out["mtp_fast"] = bool(MTPFAST.search(txt))
    md = MTPDRAFT.search(txt)
    out["draft_calls"] = int(md.group(1)) if md else 0
    out["by_ntok_kind"] = {}
    for nt, d in out["by_ntok"].items():
        if nt >= 8:
            kind = "prefill"
        elif nt == 1:
            if not out["draft_calls"]:
                # no draft calls at all -> this really is the main-context single-token decode
                kind = "main"
            elif d["total"] == out["draft_calls"]:
                kind = "draft"
            elif d["total"] > out["draft_calls"]:
                kind = "mixed"
            else:
                kind = "unknown"
        else:
            kind = "verify"
        out["by_ntok_kind"][nt] = kind
    # The single boolean the S2/S3 gate actually needs: was the delivery decode step (MTP off, main
    # context, one token per step) observed? Anything else is somebody else's measurement.
    out["delivery_ntok1_measured"] = out["by_ntok_kind"].get(1) == "main"
    dec = [int(b) for a, b in DECPROF.findall(txt)]
    out["decprof_ntok"] = dict(sorted({n: dec.count(n) for n in set(dec)}.items())) if dec else {}

    if out["sigterm"]:
        out["refuse"] = ("log carries `Received SIGTERM` -- external kill, so this is not a valid "
                         "sample even though the teardown line printed")
        return out
    if pub == 0:
        out["refuse"] = "publishes=0 -- the S1 GPU table never ran, so nothing published"
        return out
    if cc is None:
        out["refuse"] = ("old teardown shape: consumed-subset churn not instrumented "
                         "(set CGC_S1_TABLE_CHURN=1)")
        return out
    if cc + cu == 0:
        out["refuse"] = ("consumed-subset churn not instrumented: set CGC_S1_TABLE_CHURN=1. "
                         "A default 0 and a measured 0 are NOT the same thing")
        return out
    if cc + cu < min_publishes:
        out["refuse"] = (f"only {cc+cu} instrumented publishes (< {min_publishes}): not a sample")
        return out
    out.update(consumed_changed=cc, consumed_same=cu,
               churn=cc / (cc + cu), coverage=(cc + cu) / pub,
               steps=(cc + cu) / N_LAYERS, steps_moved=cc / N_LAYERS)
    return out


def fmt(r):
    p = os.path.basename(r["path"])
    if r["refuse"]:
        return f"{p:<44} REFUSE  {r['refuse']}"
    line = (f"{p:<44} churn {r['churn']:>6.1%}  "
            f"({r['consumed_changed']}/{r['consumed_changed']+r['consumed_same']} publishes"
            f" = {r['steps_moved']:.0f}/{r['steps']:.0f} layer-steps)"
            f"  coverage {r['coverage']:>5.1%}")
    if r.get("by_ntok"):
        line += "  by-ntok " + " ".join(f"{nt}:{d['pct']:.0f}%" for nt, d in sorted(r["by_ntok"].items()))
        # the default (non -v) line is the one people read, so the context warning has to be here too
        k1 = r.get("by_ntok_kind", {}).get(1)
        if k1 == "draft":
            line += "  [ntok=1 is DRAFT, not the delivery step]"
        elif k1 == "main":
            line += "  [ntok=1 is the delivery decode step]"
        elif k1:
            line += f"  [ntok=1 is {k1}]"
    if r["decprof_ntok"]:
        line += f"  decprof ntok {r['decprof_ntok']}"
    return line


def full(r):
    print(fmt(r))
    if r["refuse"]:
        return
    print(f"      publishes={r['publishes']}  whole-table changed_entries={r['changed_entries']}"
          f"  ({r['publishes']/N_LAYERS:.0f} graphs x {N_LAYERS} layers)")
    if r.get("by_ntok"):
        print("      ★ churn BY ntok (this is the one to quote):")
        for nt in sorted(r["by_ntok"]):
            d = r["by_ntok"][nt]
            kind = r.get("by_ntok_kind", {}).get(nt, "unknown")
            print(f"          ntok={nt}: {d['pct']:>5.1f}%  ({d['changed']}/{d['total']} publishes)"
                  f"   {KIND_LABEL.get(kind, kind)}")
        # [CGC 2026-09-28] The loud half of the fix. A reader who takes the ntok=1 row as "the
        # delivery step" gets 0.0%, S3 looks free, and the number came from the draft context in an
        # MTP-on run. Say so on the line itself, next to the number it invalidates.
        k1 = r.get("by_ntok_kind", {}).get(1)
        if k1 == "draft":
            print(f"      ✗ the ntok=1 row above is the DRAFT context (its denominator "
                  f"{r['by_ntok'][1]['total']} equals `draft: calls={r['draft_calls']}`), so this "
                  f"run does NOT measure the delivery decode step. MTP off, main context, one "
                  f"token per step -- that is the cell premise B needs, and it is absent here.")
        elif k1 == "mixed":
            print(f"      ✗ the ntok=1 row above mixes draft ({r['draft_calls']} calls) with main "
                  f"({r['by_ntok'][1]['total'] - r['draft_calls']} publishes): it cannot be quoted "
                  f"as either the draft or the delivery step.")
        elif k1 == "main":
            print("      ✓ ntok=1 is the main context with no draft calls in this log: this IS the "
                  "delivery decode step premise B asks about.")
    else:
        print("      (no by-ntok split: binary is pre-2026-09-20, so the rate above is a MIXTURE "
              "of widths and cannot be attributed)")
    pg = r["per_graph"]
    if pg:
        print(f"      per-graph lines: {len(pg)}", end="")
        if r["per_graph_has_ntok"]:
            by = {}
            for g in pg:
                d = by.setdefault(g["ntok"], [0, 0, 0])
                d[0] += 1
                d[1] += g["changed"]
                # THE WEIGHT, not the count. `consumed_total = n_tokens * n_expert_used`, so a
                # ntok=8 chunk contributes 2x the ids of a ntok=4 step and therefore decides twice
                # as much of the rate. A 50:50 step count is a 33:67 rate weighting. Measured
                # 2026-09-20: the first delivery run reported 73.4% while only 19% of that rate was
                # decided by the delivery decode step -- the rest was prefill chunks.
                d[2] += g["ntok"] * 8
            wtot = sum(v[2] for v in by.values()) or 1
            parts = [f"ntok={k}: {v[0]} graphs, whole-table churn {v[1]}, rate weight {v[2]/wtot:.0%}"
                     for k, v in sorted(by.items())]
            print(f"  ->  {', '.join(parts)}")
            print(f"      ⚠ the RATE above is ONE number and is not split by ntok -- these are "
                  f"whole-table counts plus the weight each class carries into it, for attribution")
            print(f"      ⚠ read the weight column: a small step count can still carry most of the "
                  f"rate (ids per publish scale with ntok)")
        else:
            print("  (no ntok field: pre-2026-09-20 binary, so the population cannot be split)")
    else:
        print("      no per-graph lines (the per-graph print happens at the il<=1 boundary)")


def self_test():
    import tempfile
    ok = True

    def expect(tag, got, want):
        nonlocal ok
        good = got == want
        ok &= good
        print(f"  [{'OK' if good else 'FAIL'}] {tag}: {got!r}" + ("" if good else f"  (want {want!r})"))

    def mk(body):
        f = tempfile.NamedTemporaryFile("w", suffix=".log", delete=False)
        f.write(body)
        f.close()
        return f.name

    print("premise_b_read --self-test")

    # 1) a good log: rate computed
    p = mk("llama_expert_cache: S1 slot-table: publishes=1599 clamped_selected=0 clamped_table=1"
           " changed_entries=572 consumed_changed=192 consumed_unchanged_publishes=198\n"
           "CGC-S1: TABLE-CHURN graph=0 ntok=4 publishes=39 changed_entries=100\n"
           "CGC-S1: TABLE-CHURN graph=1 ntok=4 publishes=39 changed_entries=200\n")
    r = read(p)
    expect("a good log yields a rate", round(r["churn"], 4), round(192 / 390, 4))
    expect("steps are publishes/39", round(r["steps"], 2), round(390 / 39, 2))
    expect("ntok split is available", sorted({g["ntok"] for g in r["per_graph"]}), [4])

    # 2) knob off -> must REFUSE, not print 0%
    p = mk("llama_expert_cache: S1 slot-table: publishes=1599 clamped_selected=0 clamped_table=1"
           " changed_entries=0 consumed_changed=0 consumed_unchanged_publishes=0"
           "  (consumed-subset churn not instrumented: set CGC_S1_TABLE_CHURN=1)\n")
    r = read(p)
    expect("knob off refuses", r["refuse"] is not None and "not instrumented" in r["refuse"], True)

    # 3) publishes=0 -> refuse
    p = mk("llama_expert_cache: S1 slot-table: publishes=0 clamped_selected=0 clamped_table=0"
           " changed_entries=0 consumed_changed=0 consumed_unchanged_publishes=0\n")
    expect("publishes=0 refuses", read(p)["refuse"] is not None, True)

    # 4) too short -> refuse (35 publishes is the real 20260918_133556 case)
    p = mk("llama_expert_cache: S1 slot-table: publishes=2635 clamped_selected=0 clamped_table=0"
           " changed_entries=0 consumed_changed=0 consumed_unchanged_publishes=35\n")
    expect("a 35-publish run refuses", read(p)["refuse"] is not None, True)

    # 5) no teardown at all (killed) -> refuse
    p = mk("CGC-S1: TABLE-CHURN graph=0 ntok=4 publishes=39 changed_entries=1\n")
    expect("no teardown refuses", read(p)["refuse"] is not None, True)

    # 5b) SIGTERM -> refuse even though the counters look fine
    p = mk("llama_expert_cache: S1 slot-table: publishes=6595 clamped_selected=0 clamped_table=0"
           " changed_entries=1 consumed_changed=4810 consumed_unchanged_publishes=1745\n"
           "[CGC] Received SIGTERM, shutting down\n")
    r = read(p)
    expect("SIGTERM refuses despite a clean teardown",
           r["refuse"] is not None and "SIGTERM" in r["refuse"], True)

    # 5c) the by-ntok split parses, and its absence is stated rather than silent
    p = mk("llama_expert_cache: S1 slot-table: publishes=6595 clamped_selected=0 clamped_table=0"
           " changed_entries=1 consumed_changed=4810 consumed_unchanged_publishes=1745\n"
           "llama_expert_cache: S1 churn by ntok (consumed subset):  ntok=4 500/1000=50.0%"
           "  ntok=8 100/200=50.0%\n")
    r = read(p)
    expect("by-ntok split parses", r["by_ntok"].get(4, {}).get("pct"), 50.0)
    expect("by-ntok keeps every width", sorted(r["by_ntok"]), [4, 8])
    p2 = mk("llama_expert_cache: S1 slot-table: publishes=6595 clamped_selected=0 clamped_table=0"
            " changed_entries=1 consumed_changed=4810 consumed_unchanged_publishes=1745\n")
    expect("absent by-ntok is empty, not guessed", read(p2)["by_ntok"], {})

    # 5d) [2026-09-28] the ntok=1 row is the DRAFT context on an MTP-on run. Fixture is the real
    # 2026-09-20 shape: ntok=1 denominator == `draft: calls` (612), and the two contexts sum to
    # publishes. Reading that 0.0% as "the delivery step is churn-free" is the bug this catches.
    teardown = ("llama_expert_cache: S1 slot-table: publishes=12971 clamped_selected=0"
                " clamped_table=0 changed_entries=48410 consumed_changed=7150"
                " consumed_unchanged_publishes=5781\n")
    bytok_mtpon = ("llama_expert_cache: S1 churn by ntok (consumed subset):  ntok=1 0/612=0.0%"
                   "  ntok=4 3449/8160=42.3%  ntok=8 3701/4159=89.0%\n")
    fastpath = ("llama_expert_cache: MTP fast path: calls=8672 union=158424 cold(ZERO)=0 (0.0%)"
                "   verify: calls=8060 union=153528 cold=0 (0.0%)"
                "   draft: calls=612 union=4896 cold=0 (0.0%)\n")
    r = read(mk(teardown + bytok_mtpon + fastpath))
    expect("MTP-on: ntok=1 is attributed to the draft", r["by_ntok_kind"][1], "draft")
    expect("MTP-on: ntok=4 is the verify step", r["by_ntok_kind"][4], "verify")
    expect("MTP-on: the delivery step is NOT claimed as measured",
           r["delivery_ntok1_measured"], False)
    expect("MTP-on: draft calls are read", r["draft_calls"], 612)
    # the same counters with NO draft line = an MTP-off run, where ntok=1 really is the delivery step
    r = read(mk(teardown + "llama_expert_cache: S1 churn by ntok (consumed subset):  ntok=1 "
                    "37/306=12.1%  ntok=8 100/200=50.0%\n"))
    expect("MTP-off: ntok=1 is the main-context delivery step", r["by_ntok_kind"][1], "main")
    expect("MTP-off: the delivery step IS claimed as measured", r["delivery_ntok1_measured"], True)
    # and a denomination larger than the draft calls is a mixture, not an attribution
    r = read(mk(teardown + "llama_expert_cache: S1 churn by ntok (consumed subset):  ntok=1 "
                    "5/700=0.7%\n" + fastpath))
    expect("a bucket wider than the draft calls is flagged mixed", r["by_ntok_kind"][1], "mixed")
    expect("mixed is not mistaken for the delivery step", r["delivery_ntok1_measured"], False)

    # 6) old binary shape -> refuse, and say which knob
    p = mk("llama_expert_cache: S1 slot-table: publishes=1014 clamped=114582 changed_entries=1423"
           " unchanged_publishes=2348\n")
    r = read(p)
    expect("old shape refuses and names the knob",
           r["refuse"] is not None and "CGC_S1_TABLE_CHURN" in r["refuse"], True)

    # 7) pre-ntok per-graph lines still parse (the population just cannot be split)
    p = mk("llama_expert_cache: S1 slot-table: publishes=1599 clamped_selected=0 clamped_table=1"
           " changed_entries=572 consumed_changed=192 consumed_unchanged_publishes=198\n"
           "CGC-S1: TABLE-CHURN graph=0 publishes=39 changed_entries=100\n")
    r = read(p)
    expect("pre-ntok per-graph lines parse", r["per_graph_has_ntok"], False)
    expect("...and the rate is still computed", r["refuse"], None)

    print("\nCLI must print something for a good log (the bug this test was added for: main() "
          "discarded fmt()'s return value, so it printed nothing unless -v)")
    import subprocess
    good = mk("llama_expert_cache: S1 slot-table: publishes=1599 clamped_selected=0 clamped_table=1"
              " changed_entries=572 consumed_changed=192 consumed_unchanged_publishes=198\n")
    r0 = subprocess.run([sys.executable, __file__, good], capture_output=True, text=True)
    expect("CLI prints the rate without -v", "49.2%" in r0.stdout, True)
    r1 = subprocess.run([sys.executable, __file__, "-v", good], capture_output=True, text=True)
    expect("CLI prints detail with -v", "whole-table" in r1.stdout, True)
    # and a refusal must reach stdout too, not just exist as a dict field
    bad = mk("llama_expert_cache: S1 slot-table: publishes=1599 clamped_selected=0 clamped_table=1"
             " changed_entries=0 consumed_changed=0 consumed_unchanged_publishes=0\n")
    r2 = subprocess.run([sys.executable, __file__, bad], capture_output=True, text=True)
    expect("CLI prints the refusal", "REFUSE" in r2.stdout, True)

    print(f"  -> {'ALL PASS' if ok else 'FAILURES'}")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("logs", nargs="*")
    ap.add_argument("--newest", type=int, default=0, help="use the N newest server logs instead")
    ap.add_argument("--min-publishes", type=int, default=100)
    ap.add_argument("--verbose", "-v", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()
    if a.self_test:
        return self_test()
    logs = list(a.logs)
    if a.newest:
        logs = sorted(glob.glob("Backup/cgc_logs/llama_server_*.log"),
                      key=os.path.getmtime)[-a.newest:]
    if not logs:
        ap.error("give a log path, or --newest N")
    out = 0
    for p in logs:
        if not os.path.exists(p):
            print(f"{p:<44} MISSING")
            continue
        r = read(p, a.min_publishes)
        # main() used to be `(full if a.verbose else fmt)(r)` -- which DISCARDS fmt()'s return
        # value, so the tool printed nothing at all unless -v was given, and a silent run reads as
        # "no findings" rather than as a missing print. `full` prints and `fmt` returns, and that
        # asymmetry is exactly what invited it; branch explicitly instead of relying on it.
        if a.verbose:
            full(r)
        else:
            print(fmt(r))
        out = out or (1 if r["refuse"] else 0)
    return out


if __name__ == "__main__":
    sys.exit(main())
