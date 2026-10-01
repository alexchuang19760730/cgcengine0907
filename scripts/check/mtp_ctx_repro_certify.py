#!/usr/bin/env python3
"""mtp_ctx_repro_certify.py -- the MTP-path reproducibility certificate (per-ctx M1/M2/M3).

WHY THIS EXISTS
---------------
`mean len` / `draft acceptance` (E) is read off the server log, and E is only a *config*
constant if the draft forward it depends on is bit-identical across launches. The oracle
gate compares ONE fresh dump against a PINNED reference -- it answers "did the numerics
move since the reference?", not "are they reproducible now?". Those are different tests,
and the second one is the precondition for the first being meaningful.

So this runs the SAME probe twice, in two INDEPENDENT launches, and compares the two fresh
dumps row-by-row, split by ctx_type. Both launches are the gate's own driver (server launch,
probe, teardown are the gate's, not a hand-built argv), so the only difference between the
arms is that they are two launches.

VERDICT IS PER ctx_type AND FAIL-CLOSED
--------------------------------------
PASS requires ALL of:
  * equal row counts and no common key missing,
  * every common key bit-identical (row_fnv1a64),
  * ctx=MTP is present and every MTP row bit-identical (the object this tool exists for),
  * the two arms' `draft acceptance` lines identical (the E witness, not a tolerance).

A row that is absent on one side is a FAIL, not an exclusion: a shape difference is how the
2026-09-25 k_eff collapse presented ("only 2 of 9 keys common, both arms 'clean'").

USAGE
    python3 scripts/check/mtp_ctx_repro_certify.py --selftest
    python3 scripts/check/mtp_ctx_repro_certify.py --tag repro0926            # long probe
    python3 scripts/check/mtp_ctx_repro_certify.py --tag repro0926 --probe short
"""

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RESULT_DIR = ROOT / "Backup" / "phase_decomp"
GATE = ROOT / "scripts" / "check" / "m123_oracle_gate.py"

# The 884-record long probe's identity (same values as gate_registry.py: the prompt is part of
# the probe's identity -- a different prompt collides on (step, token_idx, ctx_type) while
# describing a different token sequence).
LONG_PROMPT = "請用繁體中文寫一段約三百字的短文，介紹巴黎的歷史、建築與文化，並說明它們之間的關係。"
LONG_MAX_TOKENS = 400
LONG_REF = ROOT / "Backup" / "phase_decomp" / "oracle_long_base2_20260919.jsonl"


def read_dump(path):
    rows = []
    for ln in Path(path).read_text(errors="replace").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            rows.append(json.loads(ln))
        except json.JSONDecodeError:
            continue
    return rows


def key_of(r):
    return (int(r.get("step", 0)), int(r.get("token_idx", 0)), r.get("ctx_type", "DEF"))


def topk(r):
    return sorted(int(t["t"]) for t in (r.get("top") or []))


def compare(rows_a, rows_b):
    """Row-by-row, split by ctx_type. Pure function: the selftest drives this directly."""
    ka = {key_of(r): r for r in rows_a}
    kb = {key_of(r): r for r in rows_b}
    common = sorted(set(ka) & set(kb))
    out = {
        "n_a": len(rows_a), "n_b": len(rows_b), "n_common": len(common),
        "only_a": sorted(set(ka) - set(kb))[:5], "n_only_a": len(set(ka) - set(kb)),
        "n_only_b": len(set(kb) - set(ka)),
        "by_ctx": {},
    }
    for ctx in ("DEF", "MTP"):
        ks = [k for k in common if k[2] == ctx]
        m1 = sum(1 for k in ks if ka[k]["row_fnv1a64"] == kb[k]["row_fnv1a64"])
        m2 = sum(1 for k in ks if ka[k]["argmax_token"] == kb[k]["argmax_token"])
        m3 = sum(1 for k in ks if topk(ka[k]) == topk(kb[k]))
        out["by_ctx"][ctx] = {"n": len(ks), "m1": m1, "m2": m2, "m3": m3}
        # the first divergence, on the key the dump is ordered by
        first = next((k for k in ks if ka[k]["row_fnv1a64"] != kb[k]["row_fnv1a64"]), None)
        if first is not None:
            out["by_ctx"][ctx]["first_diff"] = {
                "key": list(first),
                "hash_a": ka[first]["row_fnv1a64"], "hash_b": kb[first]["row_fnv1a64"],
                "argmax_a": ka[first]["argmax_token"], "argmax_b": kb[first]["argmax_token"],
            }
    return out


def accept_line(launch_log):
    """The arm's own `draft acceptance = ...` line, resolved through its launch log's [log] path."""
    try:
        txt = Path(launch_log).read_text(errors="replace")
    except OSError:
        return None
    m = re.search(r"\[log\]\s+([^\s（(]+)", txt)
    if not m:
        return None
    srv = Path(m.group(1))
    if not srv.exists():
        return None
    for line in reversed(srv.read_text(errors="replace").splitlines()):
        if "draft acceptance" in line:
            return line.split("draft acceptance", 1)[1].strip()
    return None


def verdict(cmp_doc, accept_a, accept_b):
    """Fail-closed. Returns (ok, reasons)."""
    bad = []
    if cmp_doc["n_a"] != cmp_doc["n_b"]:
        bad.append("row counts differ (%d vs %d)" % (cmp_doc["n_a"], cmp_doc["n_b"]))
    if cmp_doc["n_only_a"] or cmp_doc["n_only_b"]:
        bad.append("unpaired rows: only_a=%d only_b=%d" % (cmp_doc["n_only_a"], cmp_doc["n_only_b"]))
    for ctx in ("DEF", "MTP"):
        c = cmp_doc["by_ctx"][ctx]
        if c["n"] == 0:
            bad.append("no %s rows at all" % ctx)
        elif c["m1"] != c["n"]:
            bad.append("%s not bit-identical: %d/%d" % (ctx, c["m1"], c["n"]))
    if accept_a is None or accept_b is None:
        bad.append("accept line unreadable on one side (a=%r b=%r)" % (accept_a, accept_b))
    elif accept_a != accept_b:
        bad.append("E differs: A=%r B=%r" % (accept_a, accept_b))
    return (not bad), bad


def run_arm(tag, ref, dump, profile, prompt, max_tokens):
    env_note = [sys.executable, str(GATE), "--profile", profile, "--ref", str(ref),
                "--probe-prompt", prompt, "--probe-max-tokens", str(max_tokens),
                "--dump", str(dump), "--tag", tag]
    print("+ " + " ".join(env_note[1:]), flush=True)
    t0 = time.time()
    r = subprocess.run([str(a) for a in env_note], cwd=str(ROOT), capture_output=True, text=True)
    print(r.stdout[-1500:] + r.stderr[-800:])
    print("--- arm %s: %.0fs rc=%d" % (tag, time.time() - t0, r.returncode), flush=True)
    return r.returncode


def selftest():
    def row(step, tidx, ctx, h, argmax=7, top=(7, 8)):
        return {"step": step, "token_idx": tidx, "ctx_type": ctx, "row_fnv1a64": h,
                "argmax_token": argmax, "top": [{"t": t, "v": 1.0} for t in top]}
    ok = n = 0

    def check(name, cond):
        nonlocal ok, n
        n += 1
        print("  %-66s %s" % (name, "ok" if cond else "FAIL"))
        ok += 1 if cond else 0

    same = [row(0, 0, "DEF", "aaa"), row(1, 0, "DEF", "bbb"),
            row(2, 0, "MTP", "ccc"), row(3, 0, "MTP", "ddd")]
    doc = compare(same, same)
    v, why = verdict(doc, "0.5 (1 accepted / 2 generated), mean len = 1.5",
                     "0.5 (1 accepted / 2 generated), mean len = 1.5")
    check("identical dumps + identical E -> PASS", v and not why)
    check("per-ctx split counts both ctx", doc["by_ctx"]["MTP"]["n"] == 2 and doc["by_ctx"]["DEF"]["n"] == 2)

    # MUST FAIL 1: one MTP row moved (the object of the tool)
    moved = list(same)
    moved[2] = row(2, 0, "MTP", "ccc_drift")
    doc1 = compare(same, moved)
    v1, why1 = verdict(doc1, "0.5 x", "0.5 x")
    check("an MTP row that drifted -> FAIL, and says MTP", (not v1) and "MTP not bit-identical" in why1[0])
    check("the first MTP divergence is located", doc1["by_ctx"]["MTP"]["first_diff"]["key"] == [2, 0, "MTP"])

    # MUST FAIL 2: a DEF row drifted but MTP is clean -> still FAIL (this is not a license to ignore DEF)
    d2 = list(same); d2[0] = row(0, 0, "DEF", "aaa2")
    doc2 = compare(same, d2)
    v2, why2 = verdict(doc2, "0.5 x", "0.5 x")
    check("a DEF row that drifted -> FAIL", (not v2) and any("DEF" in w for w in why2))

    # MUST FAIL 3: E differs while every row is bit-identical
    v3, why3 = verdict(compare(same, same), "0.5 x", "0.6 y")
    check("identical rows but E differs -> FAIL (E is the claim, not a by-product)",
          (not v3) and any("E differs" in w for w in why3))

    # MUST FAIL 4: an MTP row missing on B (the k_eff-collapse shape)
    doc4 = compare(same, [r for r in same if not (r["ctx_type"] == "MTP" and r["step"] == 3)])
    v4, why4 = verdict(doc4, "0.5 x", "0.5 x")
    check("a missing MTP row -> FAIL (absent != excluded)", (not v4) and any("unpaired" in w for w in why4))

    # MUST FAIL 5: zero MTP rows in both (a probe that never drafts answers nothing)
    doc5 = compare([row(0, 0, "DEF", "a")], [row(0, 0, "DEF", "a")])
    v5, why5 = verdict(doc5, "0.5 x", "0.5 x")
    check("no MTP rows -> FAIL", (not v5) and any("no MTP rows" in w for w in why5))

    print("\nselftest: %d/%d" % (ok, n))
    return 0 if ok == n else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--tag", default="")
    ap.add_argument("--profile", default="prod25")
    ap.add_argument("--probe", choices=("long", "short"), default="long")
    ap.add_argument("--prompt", default="")
    ap.add_argument("--max-tokens", type=int, default=0)
    args = ap.parse_args()
    if args.selftest:
        return selftest()

    tag = args.tag or time.strftime("repro_%Y%m%d_%H%M%S")
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    if args.probe == "long":
        prompt, mx, ref0 = (args.prompt or LONG_PROMPT), (args.max_tokens or LONG_MAX_TOKENS), LONG_REF
    else:
        prompt, mx, ref0 = (args.prompt or ""), (args.max_tokens or 0), None  # gate defaults
    dump_a = RESULT_DIR / ("mtp_repro_%s_A.jsonl" % tag)
    dump_b = RESULT_DIR / ("mtp_repro_%s_B.jsonl" % tag)

    rc_a = run_arm("cgcMTP_%s_A" % tag, ref0 or ROOT / "Backup" / "knifeedge_matrix"
                   / "ref_iq3_pool8gb_M2_6144_bitident_v6_nbaware.jsonl", dump_a,
                   args.profile, prompt, mx)
    rc_b = run_arm("cgcMTP_%s_B" % tag, dump_a, dump_b, args.profile, prompt, mx)

    rows_a, rows_b = read_dump(dump_a), read_dump(dump_b)
    doc = compare(rows_a, rows_b)
    acc_a = accept_line(ROOT / "Backup" / "m123_oracle_gate" / ("launch_cgcMTP_%s_A.log" % tag))
    acc_b = accept_line(ROOT / "Backup" / "m123_oracle_gate" / ("launch_cgcMTP_%s_B.log" % tag))
    ok, why = verdict(doc, acc_a, acc_b)

    print("\n=========== MTP ctx reproducibility certificate (tag %s) ===========" % tag)
    print("  rows      : A=%d B=%d common=%d (only_a=%d only_b=%d)"
          % (doc["n_a"], doc["n_b"], doc["n_common"], doc["n_only_a"], doc["n_only_b"]))
    for ctx in ("DEF", "MTP"):
        c = doc["by_ctx"][ctx]
        print("  %-4s M1(bit-identical)=%d/%d  M2(argmax)=%d/%d  M3(topk)=%d/%d"
              % (ctx, c["m1"], c["n"], c["m2"], c["n"], c["m3"], c["n"]))
        if "first_diff" in c:
            fd = c["first_diff"]
            print("       first %s divergence at key=%s  %s -> %s (argmax %s -> %s)"
                  % (ctx, fd["key"], fd["hash_a"][:12], fd["hash_b"][:12],
                     fd["argmax_a"], fd["argmax_b"]))
    print("  E (accept): A=%r" % acc_a)
    print("              B=%r" % acc_b)
    print("  VERDICT   : %s%s" % ("PASS" if ok else "FAIL",
                                   "" if ok else "  -- " + "; ".join(why)))

    summary = RESULT_DIR / ("mtp_repro_%s.json" % tag)
    summary.write_text(json.dumps({"tag": tag, "profile": args.profile, "probe": args.probe,
                                   "prompt_md5_src": prompt[:40], "max_tokens": mx,
                                   "compare": doc, "accept_a": acc_a, "accept_b": acc_b,
                                   "ok": ok, "reasons": why,
                                   "dumps": [str(dump_a), str(dump_b)],
                                   "rc": [rc_a, rc_b],
                                   "ts": time.strftime("%Y-%m-%dT%H:%M:%S")},
                                  ensure_ascii=False, indent=1) + "\n")
    print("  summary   : %s" % summary)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
