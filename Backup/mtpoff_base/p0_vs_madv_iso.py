"""Attribute the prod-new structural slowdown to P0 (SKIP_READRAW) or P1/P2 (POOL_MADVISE).

Run on 2026-09-25 welded them (the "p012" arm), and the two switches have different, already
written-down fingerprints, so the weld answered neither:
  * P0 removes the second (anonymous CPU) copy of the expert weights at load -> less swap, and
    the pool becomes the only copy -> misses pay real disk I/O.  Fingerprint: reads UP slightly
    (the white paper's ctrl 43857 -> p0 44589, +1.7%).
  * P1/P2 madvise(DONTNEED) the pages a fill will overwrite and the evicted slot's pages.
    Fingerprint: reads can go DOWN while per-miss I/O time goes UP (pages no longer cached).

WHY THIS FILE CHANGED (2026-09-25 22:2x, before the reboot re-run)
Two reasons, both learned the hard way that day:
  1. The first isolation run compared its two arms against `ctrl2` from an EARLIER session.
     That session's own readings had the welded arm faster in one pass and slower in the next
     within 40 minutes, on the same build, cell and thermal label: the session, not the switch,
     dominated.  So the reference arm now runs in THIS session, every arm is cooled alike, and
     the order rotates per pass -- launch order is a first-class covariate here.
  2. `run_server.sh` now arms P0/P1/P2/B_SCHEME BY DEFAULT for prod-new, so "prod-new plain" is
     no longer a control: it is the treatment.  The control is the same profile with the
     switches explicitly disarmed (`!KEY=0`, which the harness gate accepts and records).

So the four arms are the treatment and its three one-knob-removed variants:
  armed    prod-new                              (delivery default: P0 + P1/P2 + B_SCHEME)
  p0only   !CGC_POOL_MADVISE=0                   (armed minus P1/P2)
  m2only   !CGC_EXPERT_SKIP_READRAW=0            (armed minus P0)
  alloff   !all three =0                         (the shape the 11.2-11.6 floor came from)

Decision rule, pre-registered (reference = `armed`, all in-session).  Every pct below is
`100 x (armed - arm) / armed`, so a NEGATIVE pct means that arm is slower than armed; the two
removal arms are read against the two endpoints of the pair (0 and `alloff`'s pct):
  |pct(alloff)| <= 5%                  -> the combination has no measurable effect
  p0only at alloff's pct, m2only at 0  -> the effect is P1/P2's (removing it alone recovers it)
  m2only at alloff's pct, p0only at 0  -> the effect is P0's
  both partway                       -> both contribute; the two pcts are the shares
  neither at either endpoint          -> neither switch alone explains it (report both pcts)
  |pct(alloff)| > 5% and no arm at 0   -> report; do not name a single switch
Two preconditions run first and REFUSE the attribution rather than qualify it: the arms must
start from the same box state (swap_before spread <= 512 MiB) and the launch-order drift must be
smaller than the difference being attributed -- both failed on this box's first 4-arm family
(2026-09-26: arm 1 at swap 0.00M, arms 2+ at ~3.9 GB; the run creates that swap itself).
Spread within an arm is reported: >12% is a mixed regime, and a mixed-regime arm cannot be
compared to anything (MEASUREMENT_CONTRACT §3.3b).  `cross_session_glance_alloff_vs_card_floor`
is printed for information only -- cross-session numbers are not a verdict.

Arms run only after every peer bench AND every peer driver is gone (two drivers interleave
launches and void both), and every launch is preceded by --cool seconds so no arm in this
session starts hotter or colder than its siblings.
"""
import argparse
import json
import os
import re
import statistics as st
import subprocess
import time

ROOT = "/Users/alexchuang/Documents/flashkv-devserver"
DEFAULTS = {
    "armed": "prod-new",
    "p0only": "prod-new:!CGC_POOL_MADVISE=0",
    "m2only": "prod-new:!CGC_EXPERT_SKIP_READRAW=0",
    "alloff": "prod-new:!CGC_EXPERT_SKIP_READRAW=0;!CGC_POOL_MADVISE=0;!CGC_B_SCHEME=0",
}
ORDER = ["armed", "p0only", "m2only", "alloff"]
REFERENCE = "armed"
FLOOR_TG = 11.90       # the card's prod-new floor, for an explicitly cross-session glance only
NEAR = 5.0             # % within which two arms are called the same
# Two preconditions that a 4-arm family on this box fails by construction unless it is designed
# around them. Both were MEASURED on 2026-09-26, first pass: arm 1 started at swap 0.00M and every
# later arm at ~3.9 GB (the run itself creates that swap: 8 GiB anonymous pool + 13.6 GB mmap on a
# 16 GB box), and launch order is the covariate that made same-config arms differ by 17.5%.
START_STATE_TOL_MIB = 512.0   # arms must begin from the same box state to be comparable at all


def busy():
    """True while any peer bench OR any peer driver is alive.

    Watching only for llama-bench is not enough: a peer driver spends most of its wall time in
    the cooldown with no child process, so a bench-only gate lets this driver start its own
    cooldown in parallel and the two then launch into each other. That happened at 20:09 on
    2026-09-25 -- the gate reported idle while p012_aba2 was cooling.
    """
    out = subprocess.run(["pgrep", "-fl", "llama_bench_matrix|llama-bench|p012_aba|p0_vs_madv"],
                         capture_output=True, text=True).stdout.strip()
    me = str(os.getpid())
    return "\n".join(l for l in out.splitlines() if me not in l.split(" ", 1)[0])


def swap_mib():
    out = subprocess.run(["sysctl", "-n", "vm.swapusage"], capture_output=True, text=True).stdout
    return (float(re.search(r"used = ([\d.]+)M", out).group(1)),
            float(re.search(r"total = ([\d.]+)M", out).group(1)))


def identity():
    d = {}
    for rel in ("src/llama.cpp/build/bin/libllama-common.0.dylib",
                "src/llama.cpp/build/bin/llama-bench"):
        p = os.path.join(ROOT, rel)
        if os.path.exists(p):
            d[os.path.basename(rel)] = subprocess.run(["md5", "-q", p], capture_output=True,
                                                       text=True).stdout.strip()
    d["engine_digest"] = d.get("libllama-common.0.dylib", "")[:16]
    return d


def launch(tag, spec, wd, cool):
    jp = os.path.join(wd, f"{tag}.json")
    cmd = ["python3", "scripts/check/harness.py", "bench", "--arm", spec,
           "--workdir", wd, "--json", jp]
    print(f"[cool] {cool:.0f}s before {tag}", flush=True)
    time.sleep(cool)
    sw0, tot0 = swap_mib()
    t0 = time.time()
    with open(os.path.join(wd, f"{tag}.log"), "w") as lg:
        rc = subprocess.run(cmd, cwd=ROOT, stdout=lg, stderr=subprocess.STDOUT).returncode
    sw1, tot1 = swap_mib()
    rec = {"tag": tag, "spec": spec, "rc": rc, "secs": round(time.time() - t0, 1),
           "swap_before_mib": sw0, "swap_after_mib": sw1, "swap_growth_mib": round(sw1 - sw0, 1),
           "swap_total_mib": tot1}
    try:
        arms = json.load(open(jp))
        a = (arms if isinstance(arms, list) else [arms])[0]
    except Exception as e:
        rec["error"] = f"no product: {e}"
        return rec
    tg = next((r for r in (a.get("rows") or [])
               if not r.get("n_prompt")), None) or {}
    pp = next((r for r in (a.get("rows") or []) if r.get("n_prompt")), None) or {}
    c = a.get("cache") or {}
    rec.update({"tg": tg.get("avg_ts"), "samples": tg.get("samples_ts"),
                "pp": pp.get("avg_ts"), "reads": c.get("file_reads"),
                "pread_usec": c.get("pread_usec"), "warm_skip_applied": a.get("warm_skip_applied"),
                "fixed_fill_seed": a.get("fixed_fill_seed"),
                "thermal": (a.get("thermal") or {}).get("worst"),
                "swap_arms": (a.get("base_check") or {}).get("swap_arms"),
                "overrides": (a.get("base_check") or {}).get("overrides"),
                "box_gate": a.get("box_gate"), "sys_before": a.get("sys_before"),
                "sys_after": a.get("sys_after"), "engine_build": a.get("engine_build")})
    return rec


def verdict(arms):
    """Apply the pre-registered rule to THIS session's arms; never to another session's.

    Two preconditions run BEFORE the attribution, because both failures were measured here and
    both produce a confident-looking wrong answer rather than an error:
      * same start state -- swap_before must not differ across arms by more than
        START_STATE_TOL_MIB. Without it, "arm 1 clean vs arms 2-8 at 3.9 GB" reads as a switch
        effect.
      * launch-order drift -- rotation only cancels order if the drift is small next to the
        effect. Estimated from the earliest-position reps vs the latest-position reps; a drift
        as large as the difference being attributed means the attribution is about the box.
    """
    used = [a for a in arms if a.get("tg")]
    by = {}
    for a in used:
        by.setdefault(a["tag"], []).append(a["tg"])
    med = {k: st.median(v) for k, v in by.items()}
    spread = {k: (round(100.0 * (max(v) - min(v)) / st.median(v), 1) if len(v) > 1 else None)
              for k, v in by.items()}
    out = {"median_tg": med, "spread_pct": spread, "reference": REFERENCE}

    # Compare the arms' MEAN start state, not each rep's: what has to be equal is the mix of box
    # states each arm was given. Rotation balances that when every slot is equally dirty, and it
    # cannot when only the first slot is clean -- which is this box: one boot has exactly one
    # clean slot and the run creates ~4 GB of swap before the second arm.
    starts = {k: [a.get("swap_before_mib") for a in used if a["tag"] == k
                  and a.get("swap_before_mib") is not None] for k in by}
    out["start_swap_mib_per_rep"] = starts
    means = {k: round(st.mean(v), 1) for k, v in starts.items() if v}
    out["start_swap_mean_mib"] = means
    if len(means) > 1:
        out["start_state_spread_mib"] = round(max(means.values()) - min(means.values()), 1)
        if out["start_state_spread_mib"] > START_STATE_TOL_MIB:
            out["verdict"] = (f"PRECONDITION FAILED: the arms did not get the same mix of box "
                              f"states (mean swap_before spread "
                              f"{out['start_state_spread_mib']:.0f} MiB > "
                              f"{START_STATE_TOL_MIB:.0f} MiB; {means}) -- this family compares "
                              f"the box, not the switches. One clean slot per boot cannot be "
                              f"rotated away; give each arm its own clean window, or free enough "
                              f"memory that the run does not swap at all")
            return out

    pos = sorted({a["order_index"] for a in used if a.get("order_index") is not None})
    if len(pos) > 1:
        early = [a["tg"] for a in used if a.get("order_index") == pos[0]]
        late = [a["tg"] for a in used if a.get("order_index") == pos[-1]]
        alltg = [a["tg"] for a in used]
        if early and late:
            drift = round(100.0 * (st.mean(early) - st.mean(late)) / st.mean(alltg), 1)
            out["order_drift_pct"] = drift
            out["order_drift_note"] = (f"mean of slot {pos[0]} vs slot {pos[-1]}; crude but it is "
                                       f"the covariate that matters")
            if abs(drift) >= NEAR:
                out["verdict"] = (f"PRECONDITION FAILED: launch-order drift {drift:+.1f}% is as "
                                  f"large as the difference being attributed (>= {NEAR}%)"
                                  f" -- balance the arms across slots (--passes 4) or run one "
                                  f"arm per clean window")
                return out

    if REFERENCE not in med:
        out["verdict"] = "PRECONDITION FAILED: no in-session `armed` reference arm"
        return out
    if "alloff" not in med:
        out["verdict"] = "PRECONDITION FAILED: no `alloff` arm, so the pair has no endpoint"
        return out

    def pct(tag):
        return round(100.0 * (med[REFERENCE] - med[tag]) / med[REFERENCE], 1) if tag in med else None

    out["pct_vs_armed"] = {k: pct(k) for k in ("p0only", "m2only", "alloff")}
    out["pct_means"] = "negative = that arm is slower than armed"
    out["cross_session_glance_alloff_vs_card_floor"] = round(med["alloff"] - FLOOR_TG, 2)
    scale, p0, m2 = pct("alloff"), pct("p0only"), pct("m2only")

    def at0(x):
        return x is not None and abs(x) <= NEAR

    def at_scale(x):
        return x is not None and abs(x - scale) <= NEAR

    if abs(scale) <= NEAR:
        out["verdict"] = (f"the structural combination has no measurable effect in this session "
                          f"(alloff is {scale:+.1f}% vs armed, within ±{NEAR}%)")
    elif p0 is None or m2 is None:
        out["verdict"] = "UNDETERMINED: one of the single-removal arms is missing"
    elif at0(p0) and at_scale(m2):
        out["verdict"] = (f"the effect is P0's: removing P0 alone gives {m2:+.1f}% "
                          f"(= alloff's {scale:+.1f}%), removing P1/P2 gives {p0:+.1f}%")
    elif at0(m2) and at_scale(p0):
        out["verdict"] = (f"the effect is P1/P2's: removing P1/P2 alone gives {p0:+.1f}% "
                          f"(= alloff's {scale:+.1f}%), removing P0 gives {m2:+.1f}%")
    elif at_scale(p0) and at_scale(m2):
        out["verdict"] = (f"both removals duplicate the whole effect ({p0:+.1f}% / {m2:+.1f}% "
                          f"vs alloff {scale:+.1f}%) -- the two switches are not separable here")
    else:
        shares = {k: (round(v / scale, 2) if v is not None else None)
                  for k, v in (("P1/P2", p0), ("P0", m2))}
        out["shares_of_alloff"] = shares
        out["verdict"] = (f"both contribute: alloff {scale:+.1f}%, P1/P2 removal {p0:+.1f}%, "
                          f"P0 removal {m2:+.1f}% (shares {shares})")
    mixed = [k for k, s in spread.items() if s is not None and s > 12.0]
    if mixed:
        out["mixed_regime"] = mixed
        out["verdict"] += " | UNRELIABLE for: " + ",".join(mixed) + " (spread >12%)"
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--passes", type=int, default=2,
                    help="2 passes rotate the arm order so launch order cancels out of the median")
    ap.add_argument("--cool", type=float, default=420.0)
    ap.add_argument("--workdir", default="/tmp/p0iso")
    ap.add_argument("--arms", default=",".join(ORDER))
    args = ap.parse_args()
    order = [t for t in args.arms.split(",") if t]
    os.makedirs(args.workdir, exist_ok=True)

    waited = 0
    while busy() and waited < 3600:
        time.sleep(20)
        waited += 20

    res = {"head": subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                                  capture_output=True, text=True).stdout.strip(),
           "identity": identity(), "cool_s": args.cool, "passes": args.passes,
           "arm_order": order, "specs": {k: DEFAULTS[k] for k in order},
           "waited_for_idle_s": waited,
           "rule": (f"reference={REFERENCE}; a switch costs if removing it from {REFERENCE} "
                    f"recovers more than {NEAR}%"),
           "started": time.strftime("%Y-%m-%d %H:%M:%S"), "arms": []}
    jsum = os.path.join(args.workdir, "summary.json")
    t0 = time.time()
    for p in range(args.passes):
        rot = order[p % len(order):] + order[:p % len(order)]
        print(f"[pass {p + 1}/{args.passes}] order {rot}", flush=True)
        for i, tag in enumerate(rot, 1):
            rec = launch(tag, DEFAULTS[tag], args.workdir, args.cool)
            rec.update({"pass": p, "order_index": i})
            res["arms"].append(rec)
            json.dump(res, open(jsum, "w"), indent=2)
            print(f"[{tag}] rc={rec['rc']} tg={rec.get('tg')} pp={rec.get('pp')} "
                  f"thermal={rec.get('thermal')} reads={rec.get('reads')} "
                  f"pread_us={rec.get('pread_usec')} warm_skip_applied="
                  f"{rec.get('warm_skip_applied')} swap {rec['swap_before_mib']:.0f}->"
                  f"{rec['swap_after_mib']:.0f} ({rec['swap_growth_mib']:+.0f})", flush=True)
    res["verdict"] = verdict(res["arms"])
    res["finished"] = time.strftime("%Y-%m-%d %H:%M:%S")
    res["elapsed_s"] = round(time.time() - t0, 1)
    json.dump(res, open(jsum, "w"), indent=2)
    print("verdict:", json.dumps(res["verdict"], ensure_ascii=False))
    print("done ->", jsum)


if __name__ == "__main__":
    main()
