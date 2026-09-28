#!/usr/bin/env python3
"""MTP-on promotion gate v2 -- PAIRED design + 3% reproducibility, with the frozen
MTP-off baseline as a window-class anchor (not as the comparison precision).

Why two levels (measured 2026-09-27, this box):
  * UNPAIRED single readings drift far more than 3%: the same frozen-shape off cell read
    11.61 t/s at 10:44 and 10.97 t/s at 10:59 (-5.5% in 15 min, swap +1407 MiB during the
    run). `paired_ab.py`'s own header records the same structure from 2026-09-18: six
    UNPAIRED interleaved arms of one engine spanned 7.03-10.80 t/s (1.54x) while reps
    INSIDE one arm agreed to 0.6-2%.
  * So the 3% is a PROPERTY OF THE PAIRING, and it is enforced on the pair's own repeats,
    not on the distance to the frozen value. The frozen value only answers "is this the
    same class of window at all" (anchor band).

Rules (all fail-closed, every refusal carries its reason):
  1. candidate cell == baseline cell on the 15 strict dims
  2. session stamp present
  3. PAIRED design declared ("ABBA"/"interleaved"/"paired") AND off.reps / on.reps >= 2
  4. within-side repeatability <= 3% on BOTH sides (same threshold as SPEED_ACCEPTANCE_GATE G5)
  5. optional `null` (both slots identical) spread <= 3% -- the instrument's own noise floor
  6. median(on.reps) / median(off.reps) >= 1.0  (a candidate slower than its own off control
     cannot be promoted)
  7. median(off.reps) inside the frozen anchor band (window-class check)
  8. authoritative bench cell green, or a declared `cell_exception`

Two things this gate does NOT conflate (B1, docs/MTP_CALIBER_REDEFINE_CHARTER_2026-09-28.md):
  * rules 1-8 establish PAIR COMPARABILITY: both readings saw the same class of window.
  * they do NOT establish that both readings compute the SAME FUNCTION. With MTP on,
    `cparams.n_rs_seq` goes 0 -> draft.n_max > 0 (src/llama.cpp/src/models/delta-net-base.cpp:494),
    which swaps the recurrent path to a K-slot rollback -- the reduction width changes
    (gdn 30->60, conv_input 30->150), so the very first decode already differs. Two functions.
  * so `verdict: ELIGIBLE` means "the pair is comparable". It does NOT license "on is X% of off".
    The machine-readable flag is `comparability.interpretable_speedup`; when
    `output_function.identical` is false it is forced false, and the number may then only be
    cited as "t/s under the MTP-on output function".

Frozen baseline: scripts/check/mtp_off_baseline.json
  tg 11.61 t/s / pp 275.74 t/s, prod-new (MTP off), cell p2048 n128 d512 r3 warm-skip 64.
Docs: docs/ACCEPT_LEVERS_AND_DIRTY_BOX_PAIR_2026-09-26.md §12 (+ §12.1).

Candidate JSON:
{
  "tag": "mtp-on-...",
  "session": "2026-09-27T11:20+0800",
  "cell": { ...15 strict dims... },
  "spec": "draft-mtp",
  "cell_ok": true | false,
  "cell_exception": "declared reason (only consulted when cell_ok is false)",
  "design": "ABBA",
  "null": {"reps": [11.00, 10.95], "artifact": "path"},   # optional noise floor
  "off": {"reps": [11.2, 10.9, 11.1], "pp_t_s": 291.8, "artifact": "path"},
  "on":  {"reps": [12.3, 12.1, 12.2], "pp_t_s": 290.0, "artifact": "path"}
}
Exit code 0 only when ELIGIBLE.
"""
import argparse
import hashlib
import json
import statistics as st
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASELINE_PATH = HERE / "mtp_off_baseline.json"

STRICT = ("model", "ngl", "load_mode", "threads", "batch", "ubatch",
          "prompt", "gen", "depths", "reps", "warm_skip", "ctx_size",
          "fixed_fill_seed")

PAIR_THRESHOLD = 0.03  # SPEED_ACCEPTANCE_GATE_2026-09-26.md G5: 配對門檻 3%
PAIRED_DESIGNS = ("ABBA", "abba", "interleaved", "paired")

# B1 -- output-function identity. MTP off is the baseline function; anything that turns on
# speculative decoding pushes `cparams.n_rs_seq` off zero and changes the recurrent path.
# Fail-closed: an unrecognised non-empty spec is returned verbatim, which never equals
# "MTP-off" and therefore reads as "not the same function".
OFF_SPECS = ("", "none", "off", "none (mtp off)", "-")
ON_SPEC_MARKERS = ("mtp", "draft", "spec")


def output_function_of(spec, declared=None):
    """Normalise a spec field (or an explicit `output_function`) to 'MTP-off' / 'MTP-on'.

    `declared` wins when present -- an explicit `output_function` in the candidate JSON is the
    honest way to state it, and it is what new candidates should carry. Otherwise fall back to
    the `spec` string, which is all that the 09-27 and earlier candidates recorded.
    """
    if declared:
        s = str(declared).strip().lower()
        if "off" in s:
            return "MTP-off"
        if "on" in s or "mtp" in s:
            return "MTP-on"
        return str(declared)
    s = "" if spec is None else str(spec).strip().lower()
    if s in OFF_SPECS:
        return "MTP-off"
    if any(m in s for m in ON_SPEC_MARKERS):
        return "MTP-on"
    return "MTP-off" if not s else str(spec)


def load_baseline():
    raw = BASELINE_PATH.read_bytes()
    return json.loads(raw.decode()), hashlib.md5(raw).hexdigest()


def spread(reps):
    """(max-min)/median over the repeats -- the pair's own noise, as a fraction."""
    med = st.median(reps)
    return (max(reps) - min(reps)) / med if med else float("inf")


def side_reps(side):
    reps = side.get("reps")
    if not isinstance(reps, list) or len(reps) < 2:
        return None
    try:
        return [float(r) for r in reps]
    except (TypeError, ValueError):
        return None


def evaluate(cand: dict, base: dict, baseline_md5: str) -> dict:
    reasons, declared, notes = [], [], []

    cell = cand.get("cell") or {}
    want = base["cell"]
    for k in STRICT:
        if cell.get(k) != want[k]:
            reasons.append(f"cell mismatch {k}: got={cell.get(k)!r} want={want[k]!r}")

    if not cand.get("session"):
        reasons.append("no session stamp -- off and on must be one session, or they are two boxes")

    if cand.get("design") not in PAIRED_DESIGNS:
        reasons.append(
            "not a paired design (design missing or not in ABBA/interleaved/paired) -- "
            "two single readings cannot carry a 3% claim; use paired_ab.py / ab_interleave.py")

    off, on = cand.get("off"), cand.get("on")
    off_reps, on_reps = (side_reps(off) if isinstance(off, dict) else None,
                         side_reps(on) if isinstance(on, dict) else None)

    for name, side, reps in (("off", off, off_reps), ("on", on, on_reps)):
        if not isinstance(side, dict):
            reasons.append(f"no {name.upper()} measurement")
            continue
        if side.get("status"):
            reasons.append(f"{name.upper()} has no measurement (status={side['status']}) -- "
                           "the cell must produce a number before promotion talk")
        if not side.get("artifact"):
            reasons.append(f"{name}.artifact missing -- an unrecorded measurement is not evidence")
        if reps is None:
            reasons.append(f"{name}.reps missing (<2 repeats) -- the 3% threshold lives in the repeats")

    nul = cand.get("null")
    if isinstance(nul, dict):
        nul_reps = side_reps(nul)
        if nul_reps is None:
            reasons.append("null.reps missing (<2 repeats) -- a declared noise floor must be repeated")
        else:
            sp = spread(nul_reps)
            notes.append(f"instrument floor: null spread {sp:.2%} (threshold {PAIR_THRESHOLD:.0%})")
            if sp > PAIR_THRESHOLD:
                reasons.append(f"instrument floor {sp:.2%} above the {PAIR_THRESHOLD:.0%} pair "
                               "threshold -- the window cannot resolve the claim")

    for name, reps in (("off", off_reps), ("on", on_reps)):
        if reps is not None:
            sp = spread(reps)
            notes.append(f"{name} repeatability: spread {sp:.2%} over {len(reps)} reps")
            if sp > PAIR_THRESHOLD:
                reasons.append(f"{name} repeats disagree by {sp:.2%} > {PAIR_THRESHOLD:.0%} -- "
                               "pair noise is above the gate; re-run in a quieter window")

    if off_reps is not None and on_reps is not None:
        off_med, on_med = st.median(off_reps), st.median(on_reps)
        ratio = on_med / off_med
        notes.append(f"paired ratio: on/off = {ratio:.3f}x ({on_med:.2f} vs {off_med:.2f} t/s)")
        if ratio < 1.0:
            reasons.append(f"MTP-on is slower than its own paired off control: {ratio:.2f}x "
                           f"({on_med:.2f} vs {off_med:.2f} t/s)")

    if off_reps is not None:
        ref = base["measured"]["tg_t_s"]
        lo, hi = ref * base["band"]["low"], ref * base["band"]["high"]
        off_med = st.median(off_reps)
        notes.append(f"window anchor: off median {off_med:.2f} vs frozen {ref} "
                     f"(band [{lo:.2f}, {hi:.2f}], window-class only)")
        cor = base.get("corridor") or {}
        tg_cor = cor.get("tg_t_s") or {}
        if cor.get("n_accepted"):
            notes.append(
                f"off corridor (window-class, schema {cor.get('schema')}): "
                f"{cor['n_accepted']} accepted same-cell samples "
                f"{tg_cor.get('min')}-{tg_cor.get('max')} t/s, median {tg_cor.get('median')}, "
                f"span {tg_cor.get('spread_pct')}% over {cor.get('window')} -- the frozen point is "
                f"one sample of that corridor, and the corridor is itself too wide to serve as "
                f"`off.reps` (rule 4). Artifact {cor.get('artifact')}")
        if not (lo <= off_med <= hi):
            reasons.append(f"window not comparable: off median {off_med:.2f} outside "
                           f"[{lo:.2f}, {hi:.2f}] (frozen {ref}) -- re-run or declare a re-baseline")

    if cand.get("cell_ok") is not True:
        if cand.get("cell_exception"):
            declared.append(f"cell red, declared exception: {cand['cell_exception']}")
        else:
            reasons.append("authoritative bench cell not green (cell_ok != true) and no declared cell_exception")

    # --- B1: output-function identity, independent of the pair comparability above ---
    base_of = output_function_of(base.get("spec"))
    cand_of = output_function_of(cand.get("spec"), cand.get("output_function"))
    same_of = cand_of == base_of
    if same_of:
        notes.append(f"output function: {cand_of} on both sides -- an on/off ratio is expressible")
    else:
        notes.append(
            f"OUTPUT FUNCTION DIFFERS: candidate={cand_of} baseline={base_of}. MTP on sets "
            "cparams.n_rs_seq>0 (delta-net-base.cpp:494), so the recurrent path is a K-slot "
            "rollback rather than an in-place write-back -- the two sides are different output "
            "functions. The on t/s may be cited as 't/s under the MTP-on output function' and "
            "MUST NOT be written as 'X% of off' or 'X% faster than off', even when the pair is "
            "comparable and on is the faster of the two. "
            "See docs/MTP_CALIBER_REDEFINE_CHARTER_2026-09-28.md B1.")

    return {
        "verdict": "ELIGIBLE" if not reasons else "REFUSED",
        "tag": cand.get("tag"),
        "reasons": reasons,
        "declared": declared,
        "notes": notes,
        "output_function": {"candidate": cand_of, "baseline": base_of, "identical": same_of,
                            "root_cause": None if same_of else
                            "cparams.n_rs_seq 0 -> draft.n_max > 0 "
                            "(src/llama.cpp/src/models/delta-net-base.cpp:494)"},
        "comparability": {
            "paired_comparable": not reasons,
            "interpretable_speedup": (not reasons) and same_of,
            "claim_rule": "on t/s is expressible as a % of off" if same_of else
                          "on t/s only as 'MTP-on output function'; never as a % of off",
        },
        "thresholds": {"pair_pct": PAIR_THRESHOLD, "anchor_band": [base["band"]["low"], base["band"]["high"]]},
        "baseline": {"tg_t_s": base["measured"]["tg_t_s"], "pp_t_s": base["measured"]["pp_t_s"],
                     "frozen": base["frozen"], "md5": baseline_md5,
                     "corridor": None if not (base.get("corridor") or {}).get("n_accepted") else
                     {"n_accepted": base["corridor"]["n_accepted"],
                      "tg_min": (base["corridor"].get("tg_t_s") or {}).get("min"),
                      "tg_max": (base["corridor"].get("tg_t_s") or {}).get("max"),
                      "artifact": base["corridor"].get("artifact")}},
    }


def selftest() -> int:
    base, md5 = load_baseline()
    good_cell = dict(base["cell"])

    def cand(**kw):
        d = {"tag": "t", "session": "s", "cell": dict(good_cell), "spec": "draft-mtp",
             "cell_ok": True, "design": "ABBA",
             "off": {"reps": [11.2, 10.9, 11.1], "pp_t_s": 291.8, "artifact": "x"},
             "on": {"reps": [12.3, 12.1, 12.2], "pp_t_s": 290.0, "artifact": "y"}}
        d.update(kw)
        return d

    cases = [
        ("paired, on faster, clean", cand(), "ELIGIBLE"),
        ("not paired (design missing)", {k: v for k, v in cand().items() if k != "design"}, "REFUSED"),
        ("single readings only", cand(off={"tg_t_s": 11.61, "artifact": "x"},
                                      on={"tg_t_s": 12.0, "artifact": "y"}), "REFUSED"),
        ("pair noise 14% on off side", cand(off={"reps": [12.0, 10.4], "artifact": "x"}), "REFUSED"),
        ("null floor above 3%", cand(null={"reps": [11.6, 11.0], "artifact": "z"}), "REFUSED"),
        ("null floor clean", cand(null={"reps": [11.02, 11.00], "artifact": "z"}), "ELIGIBLE"),
        ("on slower ratio 0.9", cand(on={"reps": [10.0, 9.9], "artifact": "y"}), "REFUSED"),
        ("paired but off outside anchor", cand(off={"reps": [14.0, 13.9], "artifact": "x"}), "REFUSED"),
        ("cell red, no exception", cand(cell_ok=False), "REFUSED"),
        ("cell red, declared", cand(cell_ok=False, cell_exception="box OOM, deferred"), "ELIGIBLE"),
        ("on status only", cand(on={"status": "cell_oom: rc=-6 CGC-METAL-FAIL"}), "REFUSED"),
        ("cell drift", cand(cell={**good_cell, "reps": 1}), "REFUSED"),
    ]
    bad = 0
    for name, c, want in cases:
        got = evaluate(c, base, md5)["verdict"]
        if got != want:
            bad += 1
        print(f"  {'ok ' if got == want else 'FAIL'} {name:34s} -> {got} (want {want})")

    # B1 -- the output-function layer must move INDEPENDENTLY of the verdict: a comparable pair
    # with a different function is still ELIGIBLE, but must not be readable as a speedup.
    # (name, candidate, want_identical, want_candidate_label)
    # The label is asserted, not just `identical`: the fallback returns an unrecognised spec
    # VERBATIM, which is also "not MTP-off", so asserting the boolean alone cannot tell a
    # working marker table from an empty one (verified by mutation -- an equivalent mutant).
    b1_cases = [
        ("spec=draft-mtp vs off baseline", cand(), False, "MTP-on"),
        ("spec=none == off baseline", cand(spec="none"), True, "MTP-off"),
        ("spec missing -> treated as off", {k: v for k, v in cand().items() if k != "spec"},
         True, "MTP-off"),
        ("declared output_function wins over spec", cand(spec="none", output_function="MTP-on"),
         False, "MTP-on"),
        ("off-side function but on slower", cand(spec="none", on={"reps": [10.0, 9.9],
                                                                 "artifact": "y"}),
         True, "MTP-off"),
        ("unknown spec -> fail-closed, never MTP-off", cand(spec="eagle-3"), False, "eagle-3"),
    ]
    for name, c, want_ident, want_label in b1_cases:
        r = evaluate(c, base, md5)
        got_ident = r["output_function"]["identical"]
        got_label = r["output_function"]["candidate"]
        want_speed = want_ident and r["verdict"] == "ELIGIBLE"
        got_speed = r["comparability"]["interpretable_speedup"]
        ok = (got_ident == want_ident) and (got_speed == want_speed) and (got_label == want_label)
        if not ok:
            bad += 1
        print(f"  {'ok ' if ok else 'FAIL'} B1 {name:40s} -> {got_label}/{got_ident} "
              f"speedup={got_speed} (want {want_label}/{want_ident}/{want_speed})")
    print(f"selftest: {'PASS' if bad == 0 else f'{bad} FAIL'} "
          f"({len(cases) + len(b1_cases)} cases)")
    return 0 if bad == 0 else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--candidate", help="candidate JSON (see docstring)")
    ap.add_argument("--json", dest="json_out", help="write the verdict JSON here")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not a.candidate:
        print("need --candidate <file> or --selftest", file=sys.stderr)
        return 2
    base, md5 = load_baseline()
    cand = json.loads(Path(a.candidate).read_text())
    rep = evaluate(cand, base, md5)
    if a.json_out:
        Path(a.json_out).write_text(json.dumps(rep, ensure_ascii=False, indent=2))
    print(f"verdict: {rep['verdict']}")
    of = rep["output_function"]
    print(f"  output function: candidate={of['candidate']} baseline={of['baseline']} "
          f"identical={of['identical']}")
    if not of["identical"]:
        print(f"  NOT INTERPRETABLE AS A SPEEDUP: {rep['comparability']['claim_rule']}")
        if of["root_cause"]:
            print(f"    root cause: {of['root_cause']}")
    for r in rep["reasons"]:
        print(f"  REFUSE: {r}")
    for n in rep["notes"]:
        print(f"  note: {n}")
    for d in rep["declared"]:
        print(f"  declared: {d}")
    print(f"  baseline: tg={rep['baseline']['tg_t_s']} pp={rep['baseline']['pp_t_s']} "
          f"frozen={rep['baseline']['frozen']} md5={rep['baseline']['md5'][:12]}")
    return 0 if rep["verdict"] == "ELIGIBLE" else 1


if __name__ == "__main__":
    sys.exit(main())
