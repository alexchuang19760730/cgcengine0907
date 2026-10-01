#!/usr/bin/env python3
"""Did each MEASURED rep actually run the draft chain? Reads `CGC-BENCH-ACCEPT` lines.

WHY THIS EXISTS (measured 2026-09-28, two independent arms)
----------------------------------------------------------
`k3_pair_cert.sh` verified k at the level of the ARM: the record's `spec_draft_n_max` read back
as the requested k, so the flag had reached llama-bench. That check passed on both arms of pilot
pair r1 -- and both arms were not k=3 arms anyway:

    arm r1.b (k=3, `delivery` cell)          mean_len   t/s
      rep1 warm_skip   drafted=97             2.31
      rep1 timed       drafted=48             4.00    13.07
      rep2 warm_skip   drafted=0              1.00
      rep2 timed       drafted=3              1.05     5.24
      rep3 warm_skip   drafted=0              1.00
      rep3 timed       drafted=0              1.00     4.91

    arm (rerun, started NOMINAL, 117/124 readings NOMINAL)
      rep1 warm_skip   drafted=82             2.23
      rep1 timed       drafted=90             2.02     9.80
      rep2 warm_skip   drafted=0              1.00
      rep2 timed       drafted=0              1.00     8.84
      rep3 warm_skip   drafted=0              1.00
      rep3 timed       drafted=0              1.00     8.79

After the FIRST measured rep the chain stops drafting entirely and never recovers (264x
`llama_decode[0] returned -1`). So `avg_ts` for a "k=3" arm is a blend of one speculative rep and
two PLAIN DECODE reps, and a k=2 vs k=3 comparison is a comparison of two blends. The second run
also settles the attribution: it STARTED at NOMINAL and stayed there (117/124), and the chain still
died -- so this is not a thermal effect, and `wait_nominal` cannot protect against it.

THE THRESHOLD, AND WHY IT IS 1.5. `mean_len` is emitted per phase. Observed alive: 2.02, 2.23,
4.00. Observed dead: 1.0000, 1.0469. 1.5 sits in the gap and is not fitted to it -- it is the
smallest value that says "this phase accepted a draft at least every other round", which is the
weakest thing a speculative phase can do and still be a speculative phase. It is a CLI default,
printed in the verdict, so a reader can disagree with the number instead of with a conclusion.

`--min-mean-len` applies to `phase=timed` blocks only (the warm_skip blocks are not measured), and
a log with NO `CGC-BENCH-ACCEPT` lines is UNREADABLE, never a pass -- the same rule
`mtp_keff_check.py` applies to an unarmed SPECDBG (that tool reads the round-level detail and needs
`LLAMA_BENCH_SPEC_DBG`; this one reads what every normal arm already writes, which is why it can
gate runs that were never instrumented for it).

    python3 scripts/check/draft_liveness.py --log-dir /tmp/kb/logs/r1_b_k3 --expect-timed 3
    python3 scripts/check/draft_liveness.py --log /tmp/harness_bench/x.stderr.log
    python3 scripts/check/draft_liveness.py --selftest

AUDIT MODE (`--audit DIR`, repeatable) classifies a whole corpus of existing logs, because "was
this k arm actually a k arm" is a question about the PAST as well as about the next run. It
reports, per log, the requested k, the per-timed-rep `mean_len`, and a verdict; `DEAD` means at
least one measured rep ran with the draft chain off. Logs that predate `CGC-BENCH-ACCEPT` come back
as `UNREADABLE` or `SPECDBG-only`, never as a pass -- a log that cannot answer is not a log that
answered yes.
"""
from __future__ import annotations

import argparse
import collections
import glob
import os
import re
import sys

# `CGC-BENCH-ACCEPT phase=timed rounds=16 drafted=48 acc_drafts=48 mean_len=4.0000 \
#  draft_ratio=1.00000 gen_tokens=64 n_gen=64`  (fields after mean_len are tolerated)
ACCEPT_RE = re.compile(
    r"CGC-BENCH-ACCEPT\s+phase=(?P<phase>\w+)\s+rounds=(?P<rounds>\d+)\s+drafted=(?P<drafted>\d+)"
    r"\s+acc_drafts=(?P<acc>\d+)\s+mean_len=(?P<mean_len>[\d.]+)\s+draft_ratio=(?P<ratio>[\d.]+)")

# The engine's draft-chain failure. Counted, not parsed: it is the correlate of a dead phase, and a
# number is what lets a reader see that a phase died rather than merely under-performed.
FAIL_RE = re.compile(r"returned -1")

MIN_MEAN_LEN = 1.5

# The threshold CANNOT be applied to a measurement whose requested k is unknown, and this is not
# pedantry: a k=1 arm's mean_len IS 1 + a (measured 1.47 for a = 0.46667), which sits in the same
# band as a k=3 arm whose chain was suppressed to k_eff=1. Applying 1.5 to a k=1 arm calls a
# working experiment dead; applying 1.0 to a k=3 arm would miss it. So: k known -> the threshold;
# k unknown -> the reading is REPORTED as LOW-UNK-K and NOT judged. Observed in the corpus:
# 106 of 178 server-side flags sit in 1.05..1.50, i.e. exactly where a legitimate k=1 arm lives.
MIN_MEAN_LEN_K1 = 1.0

# --- audit-mode readers ---------------------------------------------------------------------
# "Was MTP even on" has to be answerable from logs that predate the ACCEPT lines, so it is asked in
# four independent ways; a log that shows none of them is not an MTP arm (and is reported as such
# rather than as a failure -- most of the corpus is MTP-off and a false DEAD would be worse than no
# reading).
MTP_RE = re.compile(r"draft-mtp enabled|SPECDBG round|CGC-BENCH-ACCEPT|spec\s+draft:|draft acceptance\s*=")
REQ_RE = re.compile(r"draft n_max=(\d+)|--spec-draft-n-max[= ](\d+)|"
                    r"CGC_SERVER_MTP_N_MAX[= ](\d+)")
SPECDBG_RE = re.compile(r"SPECDBG round: n_done=(-?\d+) n_past=(-?\d+) draft=(\d+)")

# The SERVER path's own liveness line, one per measured request:
#   `draft acceptance = 0.45000 (   54 accepted /   120 generated), mean len =  2.35`
# It is the same datum as a `phase=timed` ACCEPT block (mean_len = 1 + accepted/rounds, and
# `generated` here is rounds-with-drafts rather than rounds) but it exists for a much older and
# much larger corpus: 1610 `*.log` files under Backup carry it. Without this reader the audit can
# only classify bench logs, and the 09-23/09-26 server-era k work would come back UNREADABLE --
# which would have looked like "no contamination found" when it was really "nothing read".
SRV_RE = re.compile(r"draft acceptance\s*=\s*([\d.]+)\s*\(\s*(\d+)\s+accepted\s*/\s*(\d+)\s+"
                    r"generated\s*\)\s*,\s*mean len\s*=\s*([\d.]+)")

# build trees carry copies of everything and would drown the corpus in duplicates
SKIP_DIRS = ("/build/", "/node_modules/", "/.git/", "/__pycache__/", "/.venv/")


def parse_blocks(text: str) -> list[dict]:
    """Every CGC-BENCH-ACCEPT line in log order -- order is the rep order, which is the point."""
    out = []
    for line in text.splitlines():
        m = ACCEPT_RE.search(line)
        if not m:
            continue
        out.append({"phase": m.group("phase"), "rounds": int(m.group("rounds")),
                    "drafted": int(m.group("drafted")), "acc": int(m.group("acc")),
                    "mean_len": float(m.group("mean_len")), "ratio": float(m.group("ratio"))})
    return out


def timed_blocks(blocks) -> list[dict]:
    return [b for b in blocks if b["phase"] == "timed"]


def n_failures(text: str) -> int:
    return sum(1 for line in text.splitlines() if FAIL_RE.search(line))


def requested_k(text: str):
    """The k the run ASKED for, or None. None is "not stated in this log" -- never a guess."""
    m = REQ_RE.search(text)
    return int(m.group(1) or m.group(2) or m.group(3)) if m else None


def min_mean_len_for(req) -> float:
    """k=1 cannot reach 1.5 (its ceiling is 1 + accept < 2, measured 1.47); only "no draft at all"
    is a defect there. Any other k uses the threshold."""
    return MIN_MEAN_LEN_K1 + 1e-9 if req == 1 else MIN_MEAN_LEN


def series_of(text: str) -> tuple[str | None, list[float]]:
    """Which reader can answer this log, in order of precision, plus its `mean_len` series.

    ONE selection function, used by BOTH the gate (`verdict`) and the corpus audit (`audit_one`).
    Keeping them separate is how `draft_liveness --log <server log>` would say "cannot say" while
    `--audit` said ALIVE -- an instrument that exists but is not on the path, which is the family
    of defect this whole file is about.

      `accept` = one block per TIMED REP (`CGC-BENCH-ACCEPT phase=timed`, the bench corpus)
      `server` = one line per measured REQUEST (`draft acceptance = ... mean len = ...`)
    """
    timed = timed_blocks(parse_blocks(text))
    if timed:
        return "accept", [b["mean_len"] for b in timed]
    srv = [float(m.group(4)) for m in SRV_RE.finditer(text)]
    if srv:
        return "server", srv
    return None, []


def verdict(text: str, *, expect_timed: int | None = None,
            min_mean_len: float = MIN_MEAN_LEN) -> tuple[int, list[str]]:
    """(rc, lines). rc 0 = every measured rep was a live speculative rep. Fail-closed otherwise."""
    out = []
    fails = n_failures(text)
    if fails:
        out.append(f"{fails}x `llama_decode[...] returned -1` in this log")
    src, series = series_of(text)
    req = requested_k(text)
    if src is None:
        out.append("no CGC-BENCH-ACCEPT lines and no `draft acceptance = ... mean len =` lines: "
                   "this log cannot say whether the draft chain ran (that is not a pass, and it is "
                   "not zero)")
        return 2, out
    if src == "accept" and expect_timed is not None and len(series) != expect_timed:
        out.append(f"{len(series)} timed blocks but {expect_timed} reps were requested: the per-rep "
                   f"samples and the phase blocks do not line up, so no rep can be attributed")
        return 2, out
    if src == "server" and req is None and min(series) < MIN_MEAN_LEN:
        out.append(f"LOW-UNK-K: min mean_len = {min(series):.4f} < {MIN_MEAN_LEN}, but this log does "
                   f"not state the requested k, and a legitimate k=1 arm's mean_len is 1 + a "
                   f"(measured 1.47) -- i.e. it lives in this band. Not judged. Give the log a "
                   f"`draft n_max=`/`CGC_SERVER_MTP_N_MAX=` banner to make it judgeable.")
        return 2, out
    min_mean_len = min_mean_len_for(req)
    rows, dead = [], []
    if src == "accept":
        for i, b in enumerate(timed_blocks(parse_blocks(text)), start=1):
            rows.append(f"rep{i}: mean_len={b['mean_len']:.4f} drafted={b['drafted']} "
                        f"rounds={b['rounds']} acc={b['acc']}")
    else:
        rows = [f"req{i}: mean_len={v:.4f}" for i, v in enumerate(series, start=1)]
    for i, v in enumerate(series, start=1):
        if v < min_mean_len:
            dead.append(i)
    unit = "timed phases" if src == "accept" else "measured requests"
    out.append(f"{len(series)} {unit} (source: {src}, requested k={req}, "
               f"threshold mean_len >= {min_mean_len}):")
    out += ["  " + r for r in rows]
    if dead:
        out.append(f"DEAD DRAFT CHAIN on measured rep(s) {dead}: those reps are PLAIN DECODE, not "
                   f"speculative reps. `avg_ts`/'platform_ts' from this arm blend two code paths -- "
                   f"do not quote them as a k result, and do not pair this arm against another k.")
        return 3, out
    out.append("OK: every measured rep ran the draft chain.")
    return 0, out


def audit_one(path: str) -> dict:
    """Classify ONE log. Reads a path and returns a dict, so the selftest can pin every verdict."""
    try:
        with open(path, "r", errors="replace") as fh:
            text = fh.read()
    except OSError as e:
        return {"path": path, "verdict": "UNREADABLE-FILE", "why": str(e)}
    req = requested_k(text)
    blocks = parse_blocks(text)
    timed = timed_blocks(blocks)
    drafts = [int(x.group(3)) for x in SPECDBG_RE.finditer(text)]
    srv = [float(x.group(4)) for x in SRV_RE.finditer(text)]   # per measured request
    # Which reader answered, in order of precision. `accept` is per TIMED REP (the measured unit);
    # `server` is per REQUEST; the other two cannot answer the question at all and are labelled so.
    if timed:
        src, series = "accept", [b["mean_len"] for b in timed]
    elif srv:
        src, series = "server", srv
    else:
        src, series = None, []
    rec = {"path": path, "mtp": bool(MTP_RE.search(text)), "req": req,
           "n_accept": len(blocks), "n_timed": len(timed), "src": src,
           "min_mean_len": min(series) if series else None,
           "dead": [i for i, v in enumerate(series, 1) if v < min_mean_len_for(req)],
           "n_srv": len(srv), "specdbg": len(drafts),
           "specdbg_mode": (collections.Counter(drafts).most_common(1)[0] if drafts else None),
           "fails": n_failures(text)}
    if not rec["mtp"]:
        rec["verdict"] = "not-an-mtp-arm"
    elif series and src == "server" and req is None and rec["min_mean_len"] < MIN_MEAN_LEN:
        # not judged, not a pass: see MIN_MEAN_LEN_K1
        rec["verdict"] = "LOW-UNK-K"
    elif series:
        rec["verdict"] = "DEAD" if rec["dead"] else "ALIVE"
    elif blocks:
        rec["verdict"] = "no-timed-blocks"
    elif drafts:
        rec["verdict"] = "SPECDBG-only"
    elif req is not None:
        rec["verdict"] = "UNREADABLE"
    else:
        rec["verdict"] = "no-mtp-evidence"
    return rec


def find_logs(paths, name: str = ".stderr.log") -> list[str]:
    """Every file ending in `name` under each path (a file is taken as-is). Nothing external.

    `name` exists because the scope is a choice that must be STATED: the default covers only the
    llama-bench corpus, and `.log` is what reaches the server-side corpus that carries the same
    datum in a different shape. A scope that silently covers one path is how an audit reports
    "nothing found" about a corpus it never opened.
    """
    out = []
    for p in paths:
        if os.path.isfile(p):
            out.append(p)
            continue
        for root, dirs, files in os.walk(p):
            here = root.replace(os.sep, "/") + "/"
            if any(s in here for s in SKIP_DIRS):
                dirs[:] = []
                continue
            out += [os.path.join(root, f) for f in files if f.endswith(name)]
    return sorted(set(out))


AUDIT_ORDER = ("DEAD", "LOW-UNK-K", "SPECDBG-only", "UNREADABLE", "no-timed-blocks", "ALIVE",
               "not-an-mtp-arm", "no-mtp-evidence", "UNREADABLE-FILE")


def audit_lines(recs: list[dict]) -> list[str]:
    """The corpus table + summary, as lines (returned, not printed, so it can be tested)."""
    out = [f"{'verdict':16s} {'src':6s} {'k':>3s} {'n':>4s} {'min_mean_len':>12s} {'dead':>9s} "
           f"{'specdbg':>7s} {'-1':>5s}  log"]
    for r in sorted(recs, key=lambda x: (AUDIT_ORDER.index(x["verdict"])
                                         if x["verdict"] in AUDIT_ORDER else 99, x["path"])):
        mm = r.get("min_mean_len")
        mode = r.get("specdbg_mode")
        n = r.get("n_timed") or r.get("n_srv") or 0
        out.append(f"{r['verdict']:16s} {str(r.get('src') or '-'):6s} {str(r.get('req') or '-'):>3s} "
                   f"{n:4d} {('%.4f' % mm) if mm is not None else '-':>12s} "
                   f"{str(r.get('dead') or '-'):>9s} "
                   f"{('%d x%d' % mode) if mode else '-':>7s} {r.get('fails', 0):5d}  {r['path']}")
    counts = collections.Counter(r["verdict"] for r in recs)
    out.append("")
    out.append(f"{len(recs)} logs: " + ", ".join(f"{k}={counts[k]}"
                                                   for k in AUDIT_ORDER if counts[k]))
    dead = [r for r in recs if r["verdict"] == "DEAD"]
    if dead:
        out.append(f"{len(dead)} arm(s) ran with the draft chain OFF on at least one measured rep"
                   f" -- their avg_ts/platform_ts blend two code paths:")
        for r in dead:
            out.append(f"  k={r.get('req') or '?'} dead_reps={r['dead']} "
                       f"min_mean_len={r['min_mean_len']:.4f}  {r['path']}")
    return out


def _load(paths: list[str]) -> tuple[str, list[str]]:
    """Concatenate every log found. Returns (text, paths) -- the paths are echoed so a reader can
    see WHICH file was read, and an empty read is visible instead of looking like a clean log."""
    text, used = "", []
    for pat in paths:
        for p in sorted(glob.glob(pat)):
            if os.path.isfile(p):
                used.append(p)
                with open(p, "r", errors="replace") as fh:
                    text += fh.read()
    return text, used


def selftest() -> int:
    bad = 0

    def c(name, ok, got=""):
        nonlocal bad
        if not ok:
            bad += 1
        print(f"  {'ok  ' if ok else 'FAIL'}  {name}{'  -> ' + str(got) if got else ''}")

    # The two real arms, verbatim phase blocks (values as emitted).
    live = ("CGC-BENCH-ACCEPT phase=warm_skip rounds=35 drafted=82 acc_drafts=43 mean_len=2.2286 "
            "draft_ratio=0.52439 gen_tokens=65 n_gen=64\n"
            "CGC-BENCH-ACCEPT phase=timed rounds=41 drafted=90 acc_drafts=42 mean_len=2.0244 "
            "draft_ratio=0.46667 gen_tokens=67 n_gen=64\n")
    dead = ("CGC-BENCH-ACCEPT phase=warm_skip rounds=64 drafted=0 acc_drafts=0 mean_len=1.0000 "
            "draft_ratio=0.00000 gen_tokens=64 n_gen=64\n"
            "CGC-BENCH-ACCEPT phase=timed rounds=64 drafted=0 acc_drafts=0 mean_len=1.0000 "
            "draft_ratio=0.00000 gen_tokens=64 n_gen=64\n")
    b = parse_blocks(live)
    c("a real line parses", len(b) == 2 and b[1]["drafted"] == 90, b)
    c("timed filter drops warm_skip", len(timed_blocks(b)) == 1)
    rc, lines = verdict(live, expect_timed=1)
    c("a live timed rep passes", rc == 0, lines[-1])
    rc, lines = verdict(dead, expect_timed=1)
    c("a dead timed rep refuses", rc == 3, lines[-1][:70])
    # The near-miss: r1.b's rep2 drafted 3 tokens over 64 rounds. `drafted > 0` would PASS this,
    # which is why the criterion is mean_len and not "did anything happen".
    near = ("CGC-BENCH-ACCEPT phase=timed rounds=64 drafted=3 acc_drafts=3 mean_len=1.0469 "
            "draft_ratio=1.00000 gen_tokens=67 n_gen=64\n")
    rc, lines = verdict(near, expect_timed=1)
    c("drafted=3/64 rounds still refuses (mean_len 1.047)", rc == 3, lines[-1][:60])
    c("the threshold is stated in the output", any(">= 1.5" in l for l in verdict(near, expect_timed=1)[1]))
    rc, lines = verdict("", expect_timed=1)
    c("no ACCEPT lines is UNREADABLE, not a pass", rc == 2, lines[-1][:60])

    # --log-dir must WALK. Regression this pins: the rep-split session writes one log per launch
    # under `<out>/launches/L0i/`, and a non-recursive glob said "no log matched" about them.
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        for i, ml in ((1, 2.0), (2, 2.4)):
            d = os.path.join(td, f"L{i:02d}")
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, "llama_bench_x_p0_n128_d512_r1.stderr.log"), "w") as fh:
                fh.write("draft n_max=3\n"
                         "CGC-BENCH-ACCEPT phase=warm_skip rounds=35 drafted=80 acc_drafts=40 "
                         "mean_len=2.1000 draft_ratio=0.5 gen_tokens=64 n_gen=64\n"
                         f"CGC-BENCH-ACCEPT phase=timed rounds=38 drafted=82 acc_drafts=40 "
                         f"mean_len={ml:.4f} draft_ratio=0.5 gen_tokens=64 n_gen=64\n")
        rc_n = main(["--log-dir", td, "--expect-timed", "2"])
        c("--log-dir 會走子目錄（session 佈局：<out>/launches/L0i/）", rc_n == 0, f"rc={rc_n}")
    rc, lines = verdict(live, expect_timed=3)
    c("fewer timed blocks than reps refuses", rc == 2, lines[-1][:60])
    # `returned -1` is reported even when it is not (by itself) fatal: it is the correlate.
    c("the -1 count is reported", any("returned -1" in l for l in verdict(
        live + "spec        draft: llama_decode[0] returned -1\n", expect_timed=1)[1]))
    c("an empty glob reads as empty text", _load(["/nonexistent/*.stderr.log"])[0] == "")

    # audit mode: every verdict must be reachable from a fixture, including the false-positive
    # guards (an MTP-off log must NOT be reported as a dead k arm).
    import tempfile
    def _tmp(body, suffix=".stderr.log"):
        with tempfile.NamedTemporaryFile("w", suffix=suffix, delete=False) as fh:
            fh.write(body)
        return fh.name
    banner = "llama-bench: [spec] draft-mtp enabled (draft n_max=3): x\n"
    p1 = _tmp(banner + live + dead)
    a1 = audit_one(p1)
    c("audit: MTP arm + a dead rep -> DEAD", a1["verdict"] == "DEAD", a1)
    c("audit: the dead rep is named and k is recovered", a1["dead"] == [2] and a1["req"] == 3, a1)
    p2 = _tmp(banner + live)
    c("audit: MTP arm, live reps -> ALIVE", audit_one(p2)["verdict"] == "ALIVE")
    p3 = _tmp("llama-bench: no spec here\ntg 12.5 t/s\n")
    c("audit: a plain (MTP-off) log is NOT a k arm", audit_one(p3)["verdict"] == "not-an-mtp-arm")
    p4 = _tmp(banner + "SPECDBG round: n_done=0 n_past=519 draft=1\n" * 4)
    a4 = audit_one(p4)
    c("audit: SPECDBG-but-no-ACCEPT is its own verdict, not a pass",
      a4["verdict"] == "SPECDBG-only" and a4["specdbg_mode"] == (1, 4), a4)
    p5 = _tmp(banner)
    c("audit: MTP arm with no evidence is UNREADABLE, not ALIVE",
      audit_one(p5)["verdict"] == "UNREADABLE")
    # the server-side line, verbatim from k_abba_2026-09-23's driver.log
    srv_live = ("     draft acceptance = 0.45000 (   54 accepted /   120 generated), mean len =  2.35\n"
                "     draft acceptance = 0.67021 (   63 accepted /    94 generated), mean len =  2.97\n")
    p6 = _tmp(srv_live)
    a6 = audit_one(p6)
    c("audit: the server-format line is read", a6["src"] == "server" and a6["n_srv"] == 2, a6)
    c("audit: live server measurements are ALIVE", a6["verdict"] == "ALIVE", a6["min_mean_len"])
    p7 = _tmp("draft acceptance = 0.00000 (    0 accepted /    64 generated), mean len =  1.00\n" * 3)
    c("audit: a server measurement is read at all (k unknown -> not judged)",
      audit_one(p7)["verdict"] == "LOW-UNK-K", audit_one(p7)["min_mean_len"])
    c("verdict: the GATE reads the server shape too (not just the audit)",
      verdict(srv_live)[0] == 0, verdict(srv_live)[1][-1][:60])
    # a server measurement with an UNKNOWN k is not judged: mean_len 1.47 is a legitimate k=1 arm
    # AND the documented k_eff=1 signature, so the number alone cannot pick.
    c("verdict: unknown k + low mean_len is refused as UNJUDGEABLE, not as DEAD",
      verdict(open(p7).read())[0] == 2 and "LOW-UNK-K" in verdict(open(p7).read())[1][-1],
      verdict(open(p7).read())[1][-1][:70])
    p8 = _tmp("CGC_SERVER_MTP_N_MAX=3\n" + "draft acceptance = 0.00000 (    0 accepted /    64 "
                                "generated), mean len =  1.00\n" * 3)
    c("verdict: same reading WITH the requested k is DEAD", verdict(open(p8).read())[0] == 3)
    p9 = _tmp("CGC_SERVER_MTP_N_MAX=1\n" + "draft acceptance = 0.46667 (   7 accepted /   15 "
                                "generated), mean len =  1.47\n" * 2)
    c("verdict: a k=1 arm at mean_len 1.47 is ALIVE, not dead", verdict(open(p9).read())[0] == 0,
      verdict(open(p9).read())[1][-1][:60])
    c("audit: a k=1 server log is ALIVE", audit_one(p9)["verdict"] == "ALIVE", audit_one(p9))
    c("audit: unknown k + low mean_len is LOW-UNK-K", audit_one(p7)["verdict"] == "LOW-UNK-K")
    for p in (p8, p9):
        os.unlink(p)
    c("audit: a bench log that also has server lines prefers the per-rep reader",
      audit_one(_tmp(banner + live + srv_live))["src"] == "accept")
    c("audit: a missing file is reported, not raised",
      audit_one("/nonexistent/x.stderr.log")["verdict"] == "UNREADABLE-FILE")
    lines = audit_lines([a1, audit_one(p3), audit_one(p5)])
    c("audit table names the dead arms", any("their avg_ts" in l for l in lines), lines[-1][:60])
    for p in (p1, p2, p3, p4, p5, p6, p7):
        os.unlink(p)
    return 1 if bad else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", action="append", default=[], help="a log file (repeatable)")
    ap.add_argument("--log-dir", action="append", default=[],
                    help="a directory; every *.stderr.log UNDER it (recursive) is read")
    ap.add_argument("--expect-timed", type=int, default=None,
                    help="how many measured reps the arm was asked for")
    ap.add_argument("--min-mean-len", type=float, default=MIN_MEAN_LEN)
    ap.add_argument("--audit", action="append", default=[],
                    help="walk this path and classify each matching log (repeatable)")
    ap.add_argument("--audit-name", default=".stderr.log",
                    help="file suffix the audit walks for (default: the llama-bench corpus; "
                         "'.log' reaches the server-side corpus)")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        rc = selftest()
        print(f"  selftest: {'FAIL' if rc else 'ok'}")
        return rc
    if a.audit:
        logs = find_logs(a.audit, a.audit_name)
        if not logs:
            print(f"READ FAILED: no *{a.audit_name} under " + ", ".join(a.audit))
            return 2
        print(f"# scope: *{a.audit_name} under " + ", ".join(a.audit) + f"  ({len(logs)} files)")
        recs = [audit_one(l) for l in logs]
        for l in audit_lines(recs):
            print(l)
        # rc is a REPORT code, not a gate: the audit's job is to classify a corpus that has already
        # been run, so a DEAD log is a finding about the past, not a red build.
        return 0
    pats = list(a.log)
    if not pats and not a.log_dir:
        ap.error("give --log or --log-dir")
    text, used = _load(pats)

    # `--log-dir` WALKS (it uses `find_logs`, the same reader the audit uses). It used to expand to
    # a non-recursive `<dir>/*.stderr.log`, which reported "no log matched" about logs that were
    # right there -- measured 2026-09-28 on the first rep-split session, whose layout is
    # `<arm>/launches/L01/<...>.stderr.log`. A directory of logs is a TREE in every layout that
    # groups by arm or session, so the flag's `--help` and its behaviour now agree.
    for d in a.log_dir:
        for p in find_logs([d]):
            if p in used:
                continue
            used.append(p)
            with open(p, "r", errors="replace") as fh:
                text += fh.read()

    if not used:
        scope = ", ".join(pats) if pats else "(no --log)"
        if a.log_dir:
            scope += " ; 這些目錄下（含子目錄）沒有 *.stderr.log: " + ", ".join(a.log_dir)
        print("READ FAILED: no log matched " + scope)
        return 2
    print("read " + ", ".join(used))
    rc, lines = verdict(text, expect_timed=a.expect_timed, min_mean_len=a.min_mean_len)
    for l in lines:
        print("  " + l)
    return rc


if __name__ == "__main__":
    sys.exit(main())
