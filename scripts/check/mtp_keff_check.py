#!/usr/bin/env python3
"""Requested vs EFFECTIVE MTP draft length, per round, from a llama-bench stderr log.

WHY THIS EXISTS
---------------
`MTP_AMORTIZATION_RECHECK_2026-09-25.md` concluded "S = 0.914 => MTP is a net cost in this shape",
from a round that also recorded `SPECDBG round: ... draft=1` on every round -- i.e. the harness was
drafting ONE token while the CLI asked for three. This tool makes that mismatch a red check instead
of a footnote.

CAUSE, CORRECTED 2026-09-26 (docs/KEFF_CAP_2026-09-26.md): this header used to blame the `-1` that
llama-bench passes as the per-call `.n_max`. **That was wrong.** `-1` only means "do not truncate"
to the dispatcher (`common/speculative.cpp:2839-2843`), and `:1820` reads the impl's OWN member
`params.n_max` (from `params.draft.n_max`, i.e. the CLI value -- the bench banner confirms
`draft n_max=3`). The real mechanism: the draft loop's second step re-added the position the shared
KV already held, `llama_decode` refused it (M-RoPE needs `X < Y`, the log shows `X == Y`), and the
loop `break`s after one token. Fixed by making the one-position batch layout the assistant dialect's
(`common/speculative.cpp`, member `one_position_drafts`); the per-run `max(draft)` gate lives in
`spec_cost_curve.py` -- use that for new runs, this reader for existing logs.

WHAT IT READS
  * `SPECDBG round: n_done=%d n_past=%d draft=%zu`   (llama-bench, gated by LLAMA_BENCH_SPEC_DBG)
  * `CGC-MTP-PERF ... calls_draft=... ` + a generated-token count, when the engine's perf line is on
    -- optional; it turns "k rounds saw 1 draft" into "k tokens per draft call".

ABSENCE IS NOT ZERO: a log with no SPECDBG lines means the instrument was not armed, which is
reported as such rather than as "0 rounds, no mismatch".

    python3 scripts/check/mtp_keff_check.py --log /tmp/bench.stderr.log --requested-k 3
    python3 scripts/check/mtp_keff_check.py --selftest
"""
from __future__ import annotations

import argparse
import collections
import re
import sys

ROUND_RE = re.compile(r"SPECDBG round: n_done=(-?\d+) n_past=(-?\d+) draft=(\d+)")
MTPPERF_RE = re.compile(r"CGC-MTP-PERF type=(?P<type>\w+) calls_begin=(?P<b>\d+) "
                        r"calls_draft=(?P<d>\d+) calls_accept=(?P<a>\d+)")
GEN_RE = re.compile(r"gen(?:_erated)?[_ ]tokens=(\d+)")
KEFF_RE = re.compile(r"draft[_ ]n[_ ]max=(\d+)|--spec-draft-n-max[= ](\d+)")


def parse_log(text: str) -> dict:
    """Per-round draft lengths, the MTP perf tallies, and any requested k found in the log."""
    rounds, perf, gen, requested = [], collections.Counter(), None, []
    for line in text.splitlines():
        m = ROUND_RE.search(line)
        if m:
            rounds.append({"n_done": int(m.group(1)), "n_past": int(m.group(2)),
                           "draft": int(m.group(3))})
        m = MTPPERF_RE.search(line)
        if m:
            perf = {"type": m.group("type"), "begin": int(m.group("b")),
                    "draft": int(m.group("d")), "accept": int(m.group("a"))}
        m = GEN_RE.search(line)
        if m:
            gen = int(m.group(1))
        m = KEFF_RE.search(line)
        if m:
            requested.append(int(m.group(1) or m.group(2)))
    return {"rounds": rounds, "hist": collections.Counter(r["draft"] for r in rounds),
            "perf": perf, "gen_tokens": gen, "requested_in_log": sorted(set(requested))}


def verdict(parsed: dict, requested_k: int | None) -> tuple[str, list[str]]:
    """('OK'|'MISMATCH'|'UNARMED', lines). Rules frozen here so a reading cannot be re-narrated."""
    out = []
    if parsed["perf"].get("draft"):
        # an independent datum: it survives the unarmed case, which is why it is reported first
        dp, dtokens = parsed["perf"]["draft"], parsed["gen_tokens"]
        out.append(f"CGC-MTP-PERF: calls_draft={dp}"
                   + (f", gen_tokens={dtokens} => {dtokens / dp:.2f} tokens per draft call"
                      if dtokens else ", gen_tokens not in this log"))
    if not parsed["rounds"]:
        out.append("no SPECDBG round lines: LLAMA_BENCH_SPEC_DBG was not armed, so this log cannot "
                   "say what the effective draft length was (that is not zero, and it is not a "
                   "pass)")
        return "UNARMED", out
    eff, n = parsed["hist"].most_common(1)[0]
    out.append(f"{len(parsed['rounds'])} rounds, draft histogram "
               f"{dict(sorted(parsed['hist'].items()))} (mode {eff} x{n})")
    req = requested_k if requested_k is not None else (
        parsed["requested_in_log"][0] if parsed["requested_in_log"] else None)
    if req is None:
        out.append("no requested k given and none found in the log: report the effective value only")
    else:
        out.append(f"requested k = {req}, effective mode = {eff}")
    if req is not None and eff != req:
        out.append(f"MISMATCH: the harness drafted {eff} while {req} was requested -- every number "
                   f"from this run is a k_eff={eff} number, and the argv in the artifact still says "
                   f"{req}. Do not quote S/E/a/m from it as if k were {req}.")
        return "MISMATCH", out
    if req is not None:
        out.append(f"OK: effective draft length matches the request ({req}).")
    return "OK", out


def selftest() -> int:
    bad = 0

    def c(name, ok, got=""):
        nonlocal bad
        if not ok:
            bad += 1
        print(f"  {'ok  ' if ok else 'FAIL'}  {name}{'  -> ' + str(got) if got else ''}")

    # the exact line the 09-25 round produced, plus the argv that requested 3
    real = ("llama-bench: cmdline 'llama-bench -m x.gguf --spec-draft-n-max 3'\n"
            "SPECDBG round: n_done=0 n_past=519 draft=1\n"
            "SPECDBG round: n_done=2 n_past=521 draft=1\n")
    p = parse_log(real)
    c("the real line parses", [r["draft"] for r in p["rounds"]] == [1, 1], p["rounds"])
    c("the requested k is recovered from the log", p["requested_in_log"] == [3], p["requested_in_log"])
    st, lines = verdict(p, 3)
    c("requested 3 / effective 1 is a MISMATCH", st == "MISMATCH", lines[-1][:90])
    st, lines = verdict(p, 1)
    c("requested 1 / effective 1 is OK", st == "OK", lines[-1])

    p2 = parse_log("SPECDBG round: n_done=0 n_past=519 draft=3\n"
                   "SPECDBG round: n_done=3 n_past=522 draft=3\n")
    c("effective 3 with request 3 is OK", verdict(p2, 3)[0] == "OK")
    st, lines = verdict(parse_log("nothing here\n"), 3)
    c("an unarmed log is UNARMED, not a pass and not 0 rounds", st == "UNARMED", lines[0][:60])

    p3 = parse_log("CGC-MTP-PERF type=draft calls_begin=1 calls_draft=51 calls_accept=51 gen_tokens=153\n")
    st, lines = verdict(p3, None)
    c("tokens per draft call is reported when the perf line is present",
      any("3.00 tokens per draft call" in l for l in lines), lines)
    c("a perf line alone does not claim a verdict without rounds", st == "UNARMED")

    p4 = parse_log("SPECDBG round: n_done=0 n_past=519 draft=2\n"
                   "SPECDBG round: n_done=2 n_past=521 draft=2\n")
    st, lines = verdict(p4, None)
    c("with no request anywhere it reports the effective value only", st == "OK"
      and any("report the effective value only" in l for l in lines), lines[-1])

    print(f"\nmtp_keff_check selftest: {'OK' if bad == 0 else f'{bad} FAILED'}")
    return 0 if bad == 0 else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", help="llama-bench stderr, or a file containing it")
    ap.add_argument("--requested-k", type=int, default=None,
                    help="what the run asked for; also read from the log when present")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not a.log:
        ap.error("--log is required (or --selftest)")
    parsed = parse_log(open(a.log, errors="replace").read())
    st, lines = verdict(parsed, a.requested_k)
    for l in lines:
        print("  " + l)
    print(f"\n  {st}")
    return {"OK": 0, "MISMATCH": 2, "UNARMED": 3}[st]


if __name__ == "__main__":
    sys.exit(main())
