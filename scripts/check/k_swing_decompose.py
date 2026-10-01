#!/usr/bin/env python3
"""Split a repeated-arm experiment's spread into WITHIN-launch and LAUNCH-level parts.

WHY THIS EXISTS
    `docs/CERTIFIED_WINDOW_K_AB_2026-09-23.md` ends on an unresolved question: identical
    work (bit-identical output, identical engine counters) reads 8.59-12.25 t/s on the
    k=3 arm and 11.21-12.83 on k=2. Two candidate causes were already killed there (the
    gate wait, the pool-event counts), leaving "something about the rate".

    "The rate swings" is not one hypothesis, it is two very different ones:

      (a) every STEP is noisy                 -> nothing about the launch matters;
                                                 more requests per launch is the only fix.
      (b) each LAUNCH is assigned a different baseline, and the steps inside it are
          relatively tight                    -> the launch is the unit that varies, so
                                                 pairing, warmup and repetition behave
                                                 completely differently.

    Those two are separable from data already on disk, because one arm = one launch =
    several requests. This script does that separation. It runs no GPU.

THE ESTIMATOR
    One-way random effects, per group (k=2 / k=3), with m requests in each of n arms:

        MS_within  = SS_within  / (n * (m - 1))     expected  = sigma_within^2
        MS_between = SS_between / (n - 1)           expected  = sigma_within^2 + m * sigma_launch^2

        sigma_launch^2 = max(MS_between - MS_within, 0) / m

    The max(...,0) is a deliberate floor, not a convenience: MS_between < MS_within
    happens by chance and means "no launch-level term was detected", never "a negative
    variance". Reporting 0 is the answer; reporting a signed number invites someone to
    subtract it later.

    F = MS_between / MS_within, df = (n-1, n*(m-1)). This script reports F and refuses to
    call anything a finding below F_CRIT, which is passed in rather than tuned per run.

WHAT IT DOES NOT DO
    It cannot say WHAT the launch-level term is. It only says whether one exists, how
    large, and in which group. Attributing it needs a different instrument (see the doc).

USAGE
    python3 scripts/check/k_swing_decompose.py --selftest
    python3 scripts/check/k_swing_decompose.py --dir Backup/k_abba_2026-09-23/abba \
            --dir Backup/k_abba_2026-09-23/gated --groups k2,k3
"""

import argparse
import glob
import json
import math
import os
import statistics as st
import sys

# df = (4, 10) for the 5-arm / 3-request design this was written for. Passed in, not
# derived per run, so that "is this a finding" cannot be quietly re-tuned after seeing
# the data -- the same discipline as nsg_decide's 5% null-cell gate.
F_CRIT_DEFAULT = 3.48  # F(0.05; 4, 10)


def bench_decode_cells(d):
    """Every decode cell in a `prod_profile.py` record, as a list of (axis_label, row).

    A cell is a decode cell by SHAPE (`n_prompt == 0 and n_depth > 0`), not by its axis
    label, so renaming an axis cannot silently move the cell being compared.
    """
    out = []
    for ax in (d.get("axes") or []):
        if not isinstance(ax, dict):
            continue
        for row in (ax.get("rows") or []):
            if not isinstance(row, dict) or row.get("n_prompt") or not row.get("n_depth"):
                continue
            out.append((str(ax.get("axis") or ""), row))
    return out


BENCH_CELL_PREF = "auto"    # set from --bench-cell; see bench_pick_cell
BENCH_CELL_USED = {}        # path -> (label, n_samples): what was read, so it is never silent


def bench_pick_cell(d):
    """(axis_label, row) for the one decode cell this record means, or None.

    `auto` (default) is deliberately NOT "the first one": it takes the anchor when there
    is exactly one, else the only primary cell. The anchor is the pinned arm --
    `prod_profile.py` calls it `-anchor` for that reason -- and `REF_ARM` is the delivery
    cell, so preferring it is a documented rule rather than a guess between equals. When
    the record does not fit the rule, this returns None and `unusable_reason` says why:
    picking anyway is how an arm moves between the two cells being compared.

    `--bench-cell anchor|primary` pins it explicitly for callers who know which cell they
    registered. Whatever is chosen is echoed in `main` via BENCH_CELL_USED.
    """
    cells = bench_decode_cells(d)
    anchors = [c for c in cells if "anchor" in c[0]]
    primaries = [c for c in cells if "anchor" not in c[0]]
    if BENCH_CELL_PREF == "anchor":
        return anchors[0] if len(anchors) == 1 else None
    if BENCH_CELL_PREF == "primary":
        return primaries[0] if len(primaries) == 1 else None
    if len(anchors) == 1:
        return anchors[0]
    if len(primaries) == 1 and not anchors:
        return primaries[0]
    return None


def bench_rep_ts(d):
    """Per-rep decode t/s from a `prod_profile.py` record, or None.

    WHY A SECOND CALIBER READER EXISTS: the bench caliber is a different SHAPE, not a
    different key. One `prod_profile.py` file is

        {"axes": [{"axis": "decode-delivery",
                    "rows": [{"t/s": .., "±": .., "samples_ts": [r1, r2, r3]}]}]}

    while one `mtp_accept_ab.py` file is `[{"requests": [{"decode_tps": ..}]}]`.
    Reading either with the other's rule yields ZERO usable requests -- and that is
    exactly how this tool would have reported `docs/K3_PAIR_CERT_V2_BENCH_2026-09-23.md`
    (which registers the bench caliber): every arm skipped, "fewer than two pairs",
    rc=0. "n=0" would then have read as "not run yet".

    Which row is the decode cell is decided by SHAPE (`n_prompt == 0 and n_depth > 0`),
    not by the axis label, so a renamed axis cannot silently move the cell being
    compared.

    WHY THE CELL MUST BE PICKED BY A RULE AND NOT BY POSITION: `prod_profile.py` defaults
    `--profile prefill250` and `--ref-arm prod25-stream`, and those are TWO DIFFERENT
    NAMESPACES -- `--profile` is a server profile (run_server.sh accepts exactly
    off|qa-zh|longform-zh|coding|legacy-25plus|prod25|prefill250|prod-new), while
    `--ref-arm` is a `llama_bench_matrix` ARM name (`prod25-stream` = prod25 +
    CGC_PREFILL_STREAM=1 + CGC_GATHER_SLAB_CAP=256). So `prod25-stream` CANNOT be passed
    to `--profile` at all: one record always holds `decode-delivery` (the profile) and,
    unless `--no-ref`, `decode-delivery-anchor` (the ref arm).

    The 12.57 delivery cell that K3_PAIR_CERT_V2 is about is the ANCHOR, and §4's `--no-ref`
    deletes exactly it. That costs the same-record control `prod_profile.py` exists to
    provide ("if the profile and the anchor disagree, the finding is that the delivery
    convention did not reproduce").

    A first draft of this note said the 09-20 record's 9.90 (prefill250) vs 12.57
    (prod25-stream) proved those are different arms. It does not: `prod_profile.py`'s own
    docstring records that the two profiles' knob sets are IDENTICAL for this cell, and the
    9.90 arm is the one whose verdict reads `worst MODERATE` -- a heat-confounded reading,
    not a different arm. Corrected here because the wrong version would have justified a
    rule for the wrong reason. See `bench_pick_cell`.
    """
    picked = bench_pick_cell(d)
    if picked is None:
        return None
    ts = [float(x) for x in (picked[1].get("samples_ts") or [])
          if isinstance(x, (int, float)) and x > 0]
    return ts if len(ts) >= 2 else None


def arm_rows(path):
    """[per-request decode t/s] or None. Requests with no t/s are dropped, and an arm with
    fewer than two usable requests cannot contribute a within-arm variance, so it is dropped
    rather than silently counted as one -- but a DROP IS NOT SILENT any more: `main` refuses
    to print a paired sd when a file that named a group could not be read as an arm.

    Two calibers, one arm: see `bench_rep_ts` for why both have to be understood here.
    """
    try:
        d = json.load(open(path))
    except (OSError, ValueError):
        return None
    if isinstance(d, list):
        d = d[0] if d else None
    if not isinstance(d, dict):
        return None
    reqs = [r.get("decode_tps") for r in (d.get("requests") or [])]
    reqs = [float(x) for x in reqs if isinstance(x, (int, float)) and x > 0]
    if len(reqs) >= 2:
        return reqs
    if isinstance(d.get("axes"), list):
        picked = bench_pick_cell(d)
        rr = bench_rep_ts(d)
        if rr:
            # Which arm was read is part of the result, not a detail: the whole reason this
            # picks by a rule is that position would have picked the other arm.
            BENCH_CELL_USED[os.path.basename(path)] = (picked[0], len(rr))
        return rr
    # THIRD CALIBER: what `harness.py bench --json` writes. It is the matrix's own record --
    # a flat list of per-arm dicts, each with `rows[]` straight from llama-bench, plus
    # `contract` / `env` / `spec_draft_n_max` -- NOT a prod_profile `{axes: [...]}` wrapper.
    # The post-2026-09-25 ruling makes this the production entry (the matrix fails closed on
    # any cell that is not the test card's §2.5 block), so this is the shape a certification
    # actually arrives in now.
    rr = matrix_arm_rep_ts(d)
    if rr:
        BENCH_CELL_USED[os.path.basename(path)] = ("matrix rows[] (tg)", len(rr))
    return rr


def matrix_arm_rep_ts(d):
    """Per-rep decode t/s from one `llama_bench_matrix.py` arm record, or None.

    The decode row is the one with `n_gen > 0 and n_depth > 0` -- NOT `n_prompt == 0`.
    Under the §2.5 cell (`-p 2048 -n 128 -d 512`) llama-bench emits a pp row AND a tg row,
    and the tg row carries `n_prompt: 0` of its own; selecting on `n_prompt == 0` would still
    work but for a reason that is an accident of llama-bench's per-row reporting rather than
    a property of the cell. Select on the fields that say "this row generated tokens".
    """
    if not isinstance(d, dict):
        return None
    for row in (d.get("rows") or []):
        if not isinstance(row, dict) or not row.get("n_gen") or not row.get("n_depth"):
            continue
        ts = [float(x) for x in (row.get("samples_ts") or [])
              if isinstance(x, (int, float)) and x > 0]
        return ts if len(ts) >= 2 else None
    return None


def unusable_reason(path):
    """The sentence a silent skip should have printed. "" exactly when `arm_rows` succeeds.

    Kept separate from `arm_rows` so the loader stays a one-line predicate and the
    explanation cannot drift into being a decision. The first two lines are what keeps the
    two from ever disagreeing about the same file -- the failure mode this whole file
    exists to avoid.
    """
    if arm_rows(path):
        return ""
    try:
        d = json.load(open(path))
    except (OSError, ValueError) as e:
        return "unreadable json (%s)" % e
    if isinstance(d, list):
        d = d[0] if d else None
    if not isinstance(d, dict):
        return "not a json object"
    if isinstance(d.get("axes"), list):
        cells = bench_decode_cells(d)
        if not cells:
            if (d.get("axes") or []) and all(not (a or {}).get("rows") for a in d["axes"]):
                # Axes exist but carry no rows: the arm RAN and produced nothing (the child
                # refused). Say that, and point at the record's own evidence -- describing the
                # shape would name something that is not the problem.
                return ("bench caliber: every axis carries zero rows -- the arm produced "
                        "nothing; see `rc` / `stderr_tail` in this same file and the axis's "
                        "--log-dir output")
            return "bench caliber (prod_profile) with no decode cell (n_prompt==0, n_depth>0)"
        picked = bench_pick_cell(d)
        if picked is None:
            return ("bench caliber with decode cells [%s] but --bench-cell %s cannot pick one "
                    "of them -- ambiguous, and guessing would move an arm between the two "
                    "cells being compared"
                    % (", ".join(l for l, _ in cells) or "none", BENCH_CELL_PREF))
        if not picked[1].get("samples_ts"):
            return ("bench caliber (prod_profile) but its decode cell carries no "
                    "samples_ts -- mean+sd of 3 does not recover the 3; re-run with a "
                    "prod_profile.py that passes samples_ts through")
        return "bench decode cell has <2 positive rep samples"
    if isinstance(d.get("requests"), list):
        return "server caliber (mtp_accept_ab) with <2 usable decode_tps"
    if isinstance(d.get("rows"), list):
        for row in d["rows"]:
            if not isinstance(row, dict) or not row.get("n_gen") or not row.get("n_depth"):
                continue
            if not row.get("samples_ts"):
                return ("matrix caliber (harness.py bench / llama_bench_matrix) but its tg row "
                        "carries no samples_ts -- re-run with a matrix that passes it through")
            return "matrix tg row has <2 positive rep samples"
        return ("matrix caliber but no tg row (n_gen>0, n_depth>0) -- if `contract.ok` in this "
                "same file is false, the arm was refused before running")
    return ("neither caliber: no non-empty `requests[]`, no `axes[]`, and no `rows[]` "
            "(server / prod_profile / matrix)")


def group_of(path, groups):
    base = os.path.basename(path).lower()
    hits = [g for g in groups if g.lower() in base]
    if len(hits) != 1:
        return None        # ambiguous or absent: not guessable, and guessing would
    return hits[0]         # silently move an arm between the two cells being compared


def _betacf(a, b, x):
    """Lentz continued fraction for the incomplete beta (Numerical Recipes form)."""
    tiny, eps, itmax = 1e-30, 3e-12, 300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    if abs(d) < tiny:
        d = tiny
    d = 1.0 / d
    h = d
    for m in range(1, itmax + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        de = d * c
        h *= de
        if abs(de - 1.0) < eps:
            break
    return h


def f_cdf(x, d1, d2):
    """P(F <= x) for the F distribution, via the regularized incomplete beta."""
    if x <= 0.0:
        return 0.0
    a, b = d1 / 2.0, d2 / 2.0
    z = d1 * x / (d1 * x + d2)                   # F -> beta transform
    bt = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
                  + a * math.log(z) + b * math.log1p(-z))
    if z < (a + 1.0) / (a + b + 2.0):
        return bt * _betacf(a, b, z) / a
    return 1.0 - bt * _betacf(b, a, 1.0 - z) / b


def f_ppf(p, d1, d2):
    """Inverse of f_cdf by bisection. Pure stdlib on purpose: this file is a check script."""
    lo, hi = 1e-9, 1e6
    for _ in range(200):
        mid = (lo + hi) / 2.0
        if f_cdf(mid, d1, d2) < p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def launch_sd_upper(d, conf=0.95):
    """Largest launch-level sd consistent with the observed F at `conf`.

    WHY THIS EXISTS: `max(MS_b - MS_w, 0)` reports zero whenever MS_b <= MS_w, and
    "zero" reads like a finding. It is not - it is the point estimate at the edge of
    its own range. Under the model, F_obs / (1 + m*lambda) ~ F(df1, df2) with
    lambda = var_launch / var_within, so inverting the lower conf tail of F gives
    the largest lambda the data cannot rule out. A group whose launch term prints
    0.00% can still be bounded at, say, 10% - and that bound is what prices the arm
    count, because n is computed from the arm-mean scatter which contains it.
    """
    if d.get("F") is None or d.get("m_requests", 0) < 2 or d.get("n_arms", 0) < 2:
        return None
    d1, d2 = d["df"]
    if d1 <= 0 or d2 <= 0 or not math.isfinite(d["F"]):
        return None
    f_lo = f_ppf(1.0 - conf, d1, d2)
    lam = (d["F"] / f_lo - 1.0) / d["m_requests"]
    w = d.get("within_sd_pct")
    if lam <= 0 or w is None:
        return 0.0
    return w * math.sqrt(lam)


def t_two_sided_p(t, df):
    """P(|T| > |t|) for Student t, via the identity |T| > t  <=>  F(1,df) > t^2.

    Reuses f_cdf so this file carries one distribution implementation, not two.
    """
    if df <= 0:
        return None
    return 1.0 - f_cdf(t * t, 1, df)


def t_crit(df, alpha=0.05):
    """Two-sided critical value, by bisection on t_two_sided_p."""
    lo, hi = 0.0, 100.0
    for _ in range(200):
        mid = (lo + hi) / 2.0
        if t_two_sided_p(mid, df) > alpha:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def paired_diffs(by_rep):
    """by_rep: {rep: {"k2": [arm means], "k3": [arm means]}} -> one row per rep.

    A pair is only formed when BOTH sides are present: an unpaired rep would silently
    drop out of the mean and make n look larger than the evidence supports.
    """
    rows = []
    for r in sorted(by_rep):
        a, b = by_rep[r].get("k2"), by_rep[r].get("k3")
        if not a or not b:
            continue
        ma, mb = st.mean(a), st.mean(b)
        rows.append({"rep": r, "a": ma, "b": mb, "diff": ma - mb})
    return rows


def paired_verdict(rows, planned_n, alpha=0.05):
    """Judge a paired difference -- but ONLY at the pre-registered n.

    WHY planned_n IS REQUIRED: the temptation this blocks is the one that already
    happened. Five pairs gave t=2.08 (p=0.11); 'just two more' is exactly the move
    that turns a fixed-n test into optional stopping, and optional stopping does not
    keep the alpha it advertises. So below planned_n this returns a refusal naming
    how many are still owed, and above it says so too -- extra pairs after the test
    are a new experiment, not a better version of this one.
    """
    n = len(rows)
    out = {"n": n, "planned_n": planned_n, "alpha": alpha}
    if n < 2:
        out["class"] = "UNRESOLVED"
        out["why"] = "fewer than two pairs: no variance, no test"
        return out
    diffs = [r["diff"] for r in rows]
    mean_d = st.mean(diffs)
    sd = st.stdev(diffs)
    base = st.mean([r["b"] for r in rows])
    out.update({"mean_diff": mean_d, "sd": sd, "se": sd / (n ** 0.5),
                "mean_diff_pct": 100.0 * mean_d / base if base else None,
                "t": (mean_d / (sd / (n ** 0.5))) if sd > 0 else None,
                "slower_pairs": sum(1 for d in diffs if d > 0),
                "df": n - 1})
    out["crit"] = t_crit(n - 1, alpha)
    if n < planned_n:
        out["class"] = "NOT YET"
        out["why"] = "n=%d < planned %d: testing now would be optional stopping" % (n, planned_n)
        return out
    if n > planned_n:
        out["class"] = "OVERRUN"
        out["why"] = ("n=%d > planned %d: the pre-registered test happened at %d; this is a "
                      "new experiment" % (n, planned_n, planned_n))
        return out
    t = out["t"]
    if t is None:
        out["class"] = "UNRESOLVED"
        out["why"] = "zero spread across pairs"
        return out
    out["class"] = "CERTIFIED" if abs(t) >= out["crit"] else "NOT SEPARATED"
    return out


def _gamma_p(a, x):
    """Regularized lower incomplete gamma P(a,x).

    Series for x < a+1, continued fraction for the complement otherwise. It exists
    because stdlib has no chi-square, and planning a pilot from an ESTIMATED sd needs a
    chi-square quantile (see `paired_sd_upper`).
    """
    if x <= 0.0:
        return 0.0
    lg = math.lgamma(a)
    if x < a + 1.0:
        ap, term, s = a, 1.0 / a, 1.0 / a
        for _ in range(1000):
            ap += 1.0
            term *= x / ap
            s += term
            if abs(term) < abs(s) * 1e-16:
                break
        return s * math.exp(-x + a * math.log(x) - lg)
    tiny = 1e-300
    b = x + 1.0 - a
    c = 1.0 / tiny
    d = 1.0 / (b if b != 0.0 else tiny)
    h = d
    for i in range(1, 1000):
        an = -i * (i - a)
        b += 2.0
        d = an * d + b
        if abs(d) < tiny:
            d = tiny
        c = b + an / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        de = d * c
        h *= de
        if abs(de - 1.0) < 1e-16:
            break
    return 1.0 - math.exp(-x + a * math.log(x) - lg) * h


def chi2_cdf(x, k):
    """P(chi2_k <= x)."""
    return _gamma_p(k / 2.0, x / 2.0)


def chi2_ppf(p, k):
    """Inverse of `chi2_cdf`, by bisection -- the same approach the file already uses
    for F and t, so the three inverses agree about how they are found."""
    if not 0.0 < p < 1.0:
        raise ValueError("chi2_ppf: p must be in (0,1), got %r" % p)
    lo, hi = 0.0, 1.0
    while chi2_cdf(hi, k) < p:
        hi *= 2.0
        if hi > 1e6:
            raise ValueError("chi2_ppf: no bracket for p=%r df=%r" % (p, k))
    for _ in range(200):
        mid = (lo + hi) / 2.0
        if chi2_cdf(mid, k) < p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def paired_sd_upper(rows, conf=0.95):
    """One-sided upper confidence bound on the paired sd.

    (n-1)*sd^2/sigma^2 ~ chi2(n-1)  =>  sigma <= sd*sqrt((n-1)/chi2_{1-conf}(n-1)).

    This exists to block planning on the POINT estimate. An sd from a handful of pairs is
    itself noisy, and planning on its low side is how a pilot picks n and then fails to
    separate at that n -- the failure mode that made "7 pairs" a coin flip.
    """
    if len(rows) < 2:
        return None
    sd = st.stdev([r["diff"] for r in rows])
    n = len(rows)
    q = chi2_ppf(1.0 - conf, n - 1)
    return sd * math.sqrt((n - 1) / q) if q > 0 else None


def planned_n_for(diff, sd, alpha=0.05, cap=40):
    """Smallest n at which the paired t clears the critical value FOR THAT n.

    Both sides move with n -- t grows as sqrt(n) while the critical value falls -- so this
    is searched, not solved with the z-approximation. At these sizes the z version is
    optimistic by about one pair, which is the whole margin in question.
    """
    if not sd or sd <= 0 or not diff:
        return None
    for n in range(2, cap + 1):
        if abs(diff) / (sd / math.sqrt(n)) >= t_crit(n - 1, alpha):
            return n
    return None


def decompose(arms):
    """arms: list of per-arm lists. Returns the random-effects split."""
    n = len(arms)
    m = min(len(a) for a in arms)
    arms = [a[:m] for a in arms]                  # equal m keeps the algebra honest
    flat = [x for a in arms for x in a]
    grand = st.mean(flat)
    ss_w = sum(sum((x - st.mean(a)) ** 2 for x in a) for a in arms)
    ss_b = sum(m * (st.mean(a) - grand) ** 2 for a in arms)
    ms_w = ss_w / (n * (m - 1)) if n * (m - 1) > 0 else 0.0
    ms_b = ss_b / (n - 1) if n > 1 else 0.0
    var_launch = max(ms_b - ms_w, 0.0) / m
    f = (ms_b / ms_w) if ms_w > 0 else float("inf")
    out = {
        "n_arms": n, "m_requests": m, "grand_mean": grand,
        "ms_within": ms_w, "ms_between": ms_b,
        "within_sd_pct": 100.0 * (ms_w ** 0.5) / grand if grand else None,
        "launch_sd_pct": 100.0 * (var_launch ** 0.5) / grand if grand else None,
        # what an arm MEAN would scatter by if every arm drew the same baseline
        "expected_arm_mean_sd_pct": 100.0 * ((ms_w / m) ** 0.5) / grand if grand else None,
        "total_arm_mean_sd_pct": 100.0 * ((var_launch + ms_w / m) ** 0.5) / grand if grand else None,
        "F": f, "df": (n - 1, n * (m - 1)),
        "arms": arms,
    }
    out["declines"] = sum(1 for a in arms if all(a[i] > a[i + 1] for i in range(len(a) - 1)))
    out["first_to_last_pct"] = [100.0 * (a[0] - a[-1]) / a[0] for a in arms if a[0]]
    return out


def verdict(d, f_crit):
    """The one sentence this tool exists to produce. Three answers, not two: a group with
    too few arms to resolve anything must say so instead of printing 'no launch term'."""
    if d["n_arms"] < 2 or d["m_requests"] < 2:
        return "UNRESOLVED (need >=2 arms and >=2 requests per arm)"
    if d["F"] >= f_crit:
        return "LAUNCH-LEVEL TERM PRESENT"
    return "no launch-level term detected (arms differ no more than requests do)"


def collect(dirs, groups):
    by = {g: [] for g in groups}
    skipped = []
    for d in dirs:
        for p in sorted(glob.glob(os.path.join(d, "*.json"))):
            g = group_of(p, groups)
            if g is None:
                skipped.append(os.path.basename(p))
                continue
            r = arm_rows(p)
            if r is None:
                skipped.append(os.path.basename(p))
                continue
            by[g].append(r)
    return by, skipped


def selftest():
    tot = bad = 0

    def check(name, cond):
        nonlocal tot, bad
        tot += 1
        if not cond:
            bad += 1
            print("FAIL: %s" % name)

    # (1) pure within noise, no launch offset: MS_between should land near MS_within and
    # the launch term must floor at zero rather than go negative.
    pure = [[10.0, 11.0, 9.0], [9.5, 10.5, 11.0], [10.0, 10.0, 10.5],
            [11.0, 9.5, 10.0], [9.8, 10.2, 10.0]]
    d0 = decompose(pure)
    check("identical baselines give no launch-level term", d0["launch_sd_pct"] == 0.0)
    check("...and F below 1, since arms differ less than requests do", d0["F"] < 1.0)
    check("...and the within term is what remains", d0["within_sd_pct"] > 0.0)

    # (2) the same within noise plus a per-launch offset of +-20%: the estimator must
    # recover roughly that offset and must clear the F gate.
    import copy
    off = copy.deepcopy(pure)
    for i, a in enumerate(off):
        k = 1.0 + (0.20, -0.20, 0.20, -0.20, 0.20)[i]
        off[i] = [x * k for x in a]
    d1 = decompose(off)
    check("a per-launch offset is detected", d1["launch_sd_pct"] > 15.0)
    check("...and it clears the gate", d1["F"] >= F_CRIT_DEFAULT)
    check("...while the within term stays where it was",
          abs(d1["within_sd_pct"] - d0["within_sd_pct"]) < 1.0)

    # (3) the two are distinguishable by the number this tool prints, which is the whole
    # point: same request-level noise, different launch-level noise.
    check("the two groups differ in launch term but not in within term",
          d1["launch_sd_pct"] - d0["launch_sd_pct"] > 15.0
          and abs(d1["within_sd_pct"] - d0["within_sd_pct"]) < 1.0)

    # (4) an arm mean's total scatter is the quadrature sum -- this is the number that
    # prices "how many launches to certify 3%".
    check("total arm-mean sd is the quadrature sum of launch and within/m",
          abs(d1["total_arm_mean_sd_pct"]
              - ((d1["launch_sd_pct"] ** 2 + d1["expected_arm_mean_sd_pct"] ** 2) ** 0.5)) < 0.01)

    # (5) the floor: MS_between < MS_within must never print a negative variance.
    d2 = decompose([[10.0, 10.1, 9.9], [10.0, 9.9, 10.1]])
    check("a negative MS_between - MS_within floors at zero, never reports negative",
          d2["launch_sd_pct"] == 0.0)

    # (6) refusal paths.
    check("one arm cannot resolve a launch term",
          verdict(decompose([[1.0, 2.0, 3.0]]), F_CRIT_DEFAULT).startswith("UNRESOLVED"))
    check("one request per arm cannot either",
          verdict(decompose([[1.0], [2.0], [3.0]]), F_CRIT_DEFAULT).startswith("UNRESOLVED"))
    check("a detected term is named", verdict(d1, F_CRIT_DEFAULT) == "LAUNCH-LEVEL TERM PRESENT")
    check("an undetected one is named too, and says what it did not find",
          verdict(d0, F_CRIT_DEFAULT).startswith("no launch-level term"))

    # (7) monotone decline counting, used for the "the arm heats itself" read.
    d3 = decompose([[10.0, 9.0, 8.0], [10.0, 9.5, 9.0], [8.0, 9.0, 10.0]])
    check("monotone declines are counted, rises are not", d3["declines"] == 2)
    check("first-to-last drop is signed (a rise prints negative)",
          abs(d3["first_to_last_pct"][2] + 25.0) < 0.01)

    # (8) group assignment must not guess: a file matching no group is skipped, and one
    # matching two is skipped too.
    check("an arm matching no group is skipped, not assigned",
          group_of("/x/r1_a_k9.json", ["k2", "k3"]) is None)
    check("an arm matching two groups is skipped, not assigned to the first",
          group_of("/x/r1_k2_k3.json", ["k2", "k3"]) is None)
    check("an unambiguous arm is assigned", group_of("/x/r1_b_k3.json", ["k2", "k3"]) == "k3")

    # The F quantile is pinned against the two values this whole file already leans
    # on: F_CRIT_DEFAULT is the 95% point of F(4,10), and the k=2 F of 0.62 sits just
    # above the 5% point -- which is exactly why its bound comes out near zero.
    check("F(4,10) 95%% point reproduces the F_CRIT this file already uses",
          abs(f_ppf(0.95, 4, 10) - F_CRIT_DEFAULT) < 0.01)
    # 0.1677 is pinned two ways: by this file's own f_cdf, and by 1/F_0.95(10,4).
    # A first pass had 0.6126 here, from a Simpson integration of the F pdf that was
    # simply wrong -- and it made k=2's bound look like ~0.7% instead of ~10%. The
    # check exists so the number cannot silently drift again.
    check("F(4,10) 5%% point is the reciprocal-side tail used for the bound",
          abs(f_ppf(0.05, 4, 10) - 0.1677) < 0.0005
          and abs(f_ppf(0.05, 4, 10) - 1.0 / f_ppf(0.95, 10, 4)) < 1e-9)
    check("f_cdf is monotone and lands on the quantile it inverted",
          abs(f_cdf(f_ppf(0.95, 4, 10), 4, 10) - 0.95) < 1e-6)

    # A group with arms identical to each other must bound to zero: there is no
    # between-arm variance to be generous about.
    check("no between-arm spread => the upper bound is zero, not a positive guess",
          launch_sd_upper(decompose([[10.0, 10.1, 9.9], [10.0, 9.9, 10.1]])) == 0.0)
    # ...and a group WITH one must bound strictly above its own point estimate,
    # because a point estimate at the edge of the range is not the range.
    dhi = decompose([[10.0, 10.1, 9.9], [13.0, 12.9, 13.1], [10.0, 9.9, 10.1],
                     [13.0, 13.2, 12.8], [11.5, 11.4, 11.6]])
    check("a detected launch term bounds strictly ABOVE its point estimate",
          launch_sd_upper(dhi) > (dhi["launch_sd_pct"] or 0.0))
    check("...and above zero, so '0.00%%' can never be read as 'proven absent'",
          launch_sd_upper(dhi) > 0.0)
    check("too few arms to invert gives None rather than a flattering zero",
          launch_sd_upper({"F": 1.0, "df": (0, 2), "m_requests": 3,
                           "within_sd_pct": 5.0}) is None)

    # t critical values, pinned against the standard table -- an independent anchor,
    # like F_0.95(4,10)=3.478 was for the F side.
    check("t_crit(4) reproduces the two-sided 5% point 2.776", abs(t_crit(4) - 2.776) < 0.002)
    check("t_crit(6) reproduces 2.447", abs(t_crit(6) - 2.447) < 0.002)
    check("t_crit(10) reproduces 2.228", abs(t_crit(10) - 2.228) < 0.002)
    check("the t and F tails agree: |T|>t is F(1,df)>t^2",
          abs(t_two_sided_p(2.776, 4) - 0.05) < 0.001)

    # Optional stopping is the failure mode this mode exists to block, so the refusal
    # is the part under test -- not the arithmetic.
    pr = [{"rep": "r%d" % i, "a": 12.0, "b": 10.3, "diff": 1.7} for i in range(1, 5)]
    check("5 pairs against a planned 7 is a refusal, not a result",
          paired_verdict(pr, 7)["class"] == "NOT YET")
    check("...and it names how many are still owed", "7" in paired_verdict(pr, 7)["why"])
    # spread is deliberately small but NON-zero: exactly zero spread makes the t
    # statistic degenerate, and degenerate is a refusal here, not a perfect result.
    eight = pr + [dict(rep="r%d" % i, a=12.0, b=10.3, diff=1.7 + (i - 6) * 0.01)
                  for i in range(5, 9)]
    check("8 pairs against a planned 8 is judged, neither refused nor called an overrun",
          paired_verdict(eight, 8)["class"] in ("CERTIFIED", "NOT SEPARATED"))
    check("9 pairs against a planned 8 is an overrun, not a better 8",
          paired_verdict(eight + [dict(rep="r9", a=12.0, b=10.3, diff=1.7)], 8)["class"]
          == "OVERRUN")
    check("a steady 1.7 t/s gap over 8 pairs with sd 0 certifies",
          paired_verdict(eight, 8)["class"] == "CERTIFIED")
    check("an unpaired rep cannot inflate n",
          len(paired_diffs({"r1": {"k2": [12.0]}, "r2": {"k2": [12.0], "k3": [10.0]}})) == 1)
    check("one pair is unresolved rather than a test with df=0",
          paired_verdict([{"rep": "r1", "a": 12.0, "b": 10.0, "diff": 2.0}], 1)["class"]
          == "UNRESOLVED")

    # Chi-square quantiles: stdlib has none, and planning a pilot from an ESTIMATED sd
    # needs the upper bound -- sizing on the point estimate is how a pilot picks n and
    # then fails to separate at that n. Anchored against the standard table.
    check("chi2_ppf(0.95, 1) reproduces 3.841", abs(chi2_ppf(0.95, 1) - 3.841) < 0.002)
    check("chi2_ppf(0.95, 3) reproduces 7.815", abs(chi2_ppf(0.95, 3) - 7.815) < 0.002)
    check("chi2_ppf(0.05, 3) reproduces 0.352", abs(chi2_ppf(0.05, 3) - 0.352) < 0.002)
    check("chi2_cdf inverts the quantile it produced",
          abs(chi2_cdf(chi2_ppf(0.95, 7), 7) - 0.95) < 1e-6)
    check("chi2_cdf is 0 at zero and rises past 0.999999",
          chi2_cdf(0.0, 4) == 0.0 and chi2_cdf(1e6, 4) > 0.999999)

    # The sd bound is the number that sizes a pilot, so the property under test is
    # "strictly above the point estimate, and tighter as pairs accumulate".
    p2 = [{"rep": "r1", "a": 12.0, "b": 10.3, "diff": 1.7},
          {"rep": "r2", "a": 12.0, "b": 11.5, "diff": 0.5}]
    sd2 = st.stdev([r["diff"] for r in p2])
    check("a two-pair sd bound sits strictly above the point estimate",
          paired_sd_upper(p2) > sd2)
    check("...and more pairs bound tighter for the same spread",
          paired_sd_upper(eight) / st.stdev([r["diff"] for r in eight])
          < paired_sd_upper(p2) / sd2)
    check("fewer than two pairs gives no bound, not a confident sd",
          paired_sd_upper([p2[0]]) is None)

    # The n this file's own arithmetic implies, pinned so it cannot drift: 1.71 t/s at
    # sd 1.83 needs 7 pairs; the same diff at sd 2.0 needs 8. That one pair of margin is
    # the entire reason the pre-registered n is 8 and not 7.
    check("diff 1.71 at sd 1.83 needs 7 pairs (the '7' that was almost chosen)",
          planned_n_for(1.71, 1.83) == 7)
    check("...but at sd 2.0 the same diff needs 8 -- the sd moves, so buy the margin",
          planned_n_for(1.71, 2.0) == 8)
    check("a degenerate sd sizes nothing rather than 'one pair'",
          planned_n_for(1.71, 0.0) is None)

    # (9) THE SECOND CALIBER. This tool is named in a protocol whose registered caliber is
    # prod_profile (llama-bench), so "reads one caliber and silently skips the other" is not a
    # missing feature -- it is a wrong answer with a 0 in it. Pin both directions.
    import shutil
    import tempfile
    tdir = tempfile.mkdtemp(prefix="kswing_selftest_")
    try:
        bench = {"axes": [
            {"axis": "decode-delivery",
             "rows": [{"t/s": 12.57, "±": 2.26, "n_prompt": 0, "n_gen": 64, "n_depth": 512,
                       "samples_ts": [13.40, 12.20, 12.11]}]}]}
        p_bench = os.path.join(tdir, "r1_a_k2.json")
        json.dump(bench, open(p_bench, "w"))
        check("a bench-caliber arm is read, not skipped", arm_rows(p_bench) == [13.40, 12.20, 12.11])
        check("...and its anchor axis does not shadow the cell",
              arm_rows(p_bench) == bench_rep_ts(bench))
        check("...and it is not reported as unusable", unusable_reason(p_bench) == "")

        # the pre-fix shape: mean and sd kept, the 3 numbers gone
        stripped = json.loads(json.dumps(bench))
        del stripped["axes"][0]["rows"][0]["samples_ts"]
        p_stripped = os.path.join(tdir, "r2_a_k2.json")
        json.dump(stripped, open(p_stripped, "w"))
        check("a bench arm with no per-rep samples is refused, not averaged",
              arm_rows(p_stripped) is None)
        check("...and the reason names samples_ts", "samples_ts" in unusable_reason(p_stripped))

        # A record with two non-anchor decode cells is two different arms (prod_profile
        # defaults profile=prefill250, ref-arm=prod25-stream; measured 9.90 vs 12.57 on the
        # identical shape). Neither reading is wrong -- choosing one is.
        two = {"axes": [{"axis": "decode-delivery",
                         "rows": [{"t/s": 9.90, "n_prompt": 0, "n_depth": 512,
                                   "samples_ts": [10.1, 9.9, 9.7]}]},
                        {"axis": "decode-delivery-2",
                         "rows": [{"t/s": 12.57, "n_prompt": 0, "n_depth": 512,
                                   "samples_ts": [13.4, 12.2, 12.1]}]}]}
        p_two = os.path.join(tdir, "r4_a_k2.json")
        json.dump(two, open(p_two, "w"))
        check("two candidate decode cells are refused, not picked", arm_rows(p_two) is None)
        check("...and the reason says ambiguous", "ambiguous" in unusable_reason(p_two))

        # (11) the anchor rule. `prod_profile.py`'s two namespaces mean the delivery cell
        # (prod25-stream = the ref arm) can ONLY appear as an anchor axis, and the default
        # cell is a different arm (prefill250: 9.90 vs 12.57 on the same shape).
        both = {"axes": [{"axis": "decode-delivery",
                          "rows": [{"t/s": 9.90, "n_prompt": 0, "n_depth": 512,
                                    "samples_ts": [10.1, 9.9, 9.7]}]},
                         {"axis": "decode-delivery-anchor",
                          "rows": [{"t/s": 12.57, "n_prompt": 0, "n_depth": 512,
                                    "samples_ts": [13.4, 12.2, 12.1]}]}]}
        p_both = os.path.join(tdir, "r5_a_k2.json")
        json.dump(both, open(p_both, "w"))
        global BENCH_CELL_PREF
        BENCH_CELL_PREF = "auto"
        check("auto takes the anchor cell, i.e. the 12.57 delivery arm",
              arm_rows(p_both) == [13.4, 12.2, 12.1])
        BENCH_CELL_PREF = "primary"
        check("...and --bench-cell primary takes the other arm instead",
              arm_rows(p_both) == [10.1, 9.9, 9.7])
        BENCH_CELL_PREF = "anchor"
        check("...and --bench-cell anchor still gets the delivery arm",
              arm_rows(p_both) == [13.4, 12.2, 12.1])
        BENCH_CELL_PREF = "auto"
        check("a record with two primaries and no anchor is still refused",
              arm_rows(p_two) is None)
        check("...while an anchor label alone does not create a second candidate",
              arm_rows(p_bench) is not None)

        # axes present, rows empty: the arm ran and produced nothing. The reason must say so
        # rather than describe the shape -- shape is not the problem here.
        empty = {"axes": [{"axis": "decode-delivery", "rows": [], "rc": 1,
                           "stderr_tail": "cell contract FAIL"}]}
        p_empty = os.path.join(tdir, "r6_a_k2.json")
        json.dump(empty, open(p_empty, "w"))
        check("an arm with zero rows is refused", arm_rows(p_empty) is None)
        check("...and the reason says it produced nothing, not that the shape is wrong",
              "produced nothing" in unusable_reason(p_empty))

        # (12) the MATRIX caliber, which is what `harness.py bench --json` writes and therefore
        # what the post-09-25 ruling makes the production entry. A flat list of arm dicts.
        mt = [{"tag": "prod-new", "contract": {"ok": True},
               "rows": [{"avg_ts": 210.1, "n_prompt": 2048, "n_gen": 0, "n_depth": 0,
                         "samples_ts": [213.0, 209.0, 208.4]},
                        {"avg_ts": 12.57, "n_prompt": 0, "n_gen": 128, "n_depth": 512,
                         "samples_ts": [13.4, 12.2, 12.1]}]}]
        p_mt = os.path.join(tdir, "r7_a_k2.json")
        json.dump(mt, open(p_mt, "w"))
        check("a matrix-caliber arm is read", arm_rows(p_mt) == [13.4, 12.2, 12.1])
        check("...and the pp row (n_gen == 0) is not mistaken for the decode row",
              matrix_arm_rep_ts(mt[0]) == [13.4, 12.2, 12.1])
        check("...and it is not reported as unusable", unusable_reason(p_mt) == "")
        mt_refused = [{"tag": "prod-new", "contract": {"ok": False}, "rows": []}]
        p_rt = os.path.join(tdir, "r8_a_k2.json")
        json.dump(mt_refused, open(p_rt, "w"))
        check("an arm refused by the cell contract is refused here too",
              arm_rows(p_rt) is None)
        check("...and the reason points at contract.ok",
              "contract.ok" in unusable_reason(p_rt))

        p_none = os.path.join(tdir, "r3_a_k2.json")
        json.dump({"foo": 1}, open(p_none, "w"))
        check("a file that is neither caliber is refused", arm_rows(p_none) is None)
        check("...and says so instead of blaming the group name",
              "neither caliber" in unusable_reason(p_none))

        # (10) the gate belongs to the df. The bench design (4 arms x 3 reps) is df=(3,8).
        check("F(0.95;3,8) is 4.07, not the (4,10) default 3.48",
              abs(f_ppf(0.95, 3, 8) - 4.066) < 0.01)
        check("...so the default gate is too permissive for the registered bench design",
              F_CRIT_DEFAULT < f_ppf(0.95, 3, 8))
    finally:
        shutil.rmtree(tdir, ignore_errors=True)

    print("selftest: %d/%d passed" % (tot - bad, tot))
    return 1 if bad else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dir", action="append", default=[],
                    help="directory of arm JSONs (repeatable)")
    ap.add_argument("--groups", default="k2,k3",
                    help="comma-separated tokens matched against the filename")
    ap.add_argument("--f-crit", type=float, default=F_CRIT_DEFAULT,
                    help="F gate for declaring a launch-level term (default %.2f = F(0.05;4,10))"
                         % F_CRIT_DEFAULT)
    ap.add_argument("--paired", action="store_true",
                    help="judge the k2-vs-k3 EFFECT (paired by repetition) instead of only "
                         "the noise split. --planned-n is REQUIRED for a verdict.")
    ap.add_argument("--planned-n", type=int, default=0,
                    help="the n fixed BEFORE the run. Below it the verdict refuses; above it "
                         "the verdict calls it a new experiment. 0 = report the numbers, no test.")
    ap.add_argument("--alpha", type=float, default=0.05)
    ap.add_argument("--bench-cell", choices=("auto", "anchor", "primary"), default="auto",
                    help="which decode cell of a prod_profile record is THE arm: auto takes "
                         "the anchor when there is exactly one (that is where the prod25-stream "
                         "delivery cell lives), else the only primary cell")
    ap.add_argument("--sd-only", action="store_true",
                    help="pilot mode: report the paired sd, its 95%% upper bound, and the n those "
                         "imply -- and REFUSE to print t or a verdict. For pre-registering n on a "
                         "caliber whose paired sd is not known yet; sizing n and testing on the "
                         "same pairs is optional stopping with extra steps.")
    ap.add_argument("--target-pct", type=float, default=3.0,
                    help="effect size to price, for the arms-per-group estimate")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    if not args.dir:
        print("nothing to do: pass --dir (or --selftest)")
        return 2

    global BENCH_CELL_PREF
    BENCH_CELL_PREF = args.bench_cell
    groups = [g.strip() for g in args.groups.split(",") if g.strip()]

    # REFUSE BEFORE PRINTING, and before --sd-only / --paired changes anything. A file whose
    # NAME named a group but whose SHAPE could not be read is not an absent arm: swallowing
    # it is how a correctly-named directory of bench-caliber arms produced `fewer than two
    # pairs` with rc=0, where `n=0` is indistinguishable from `not run yet`. Refusing here
    # (rather than inside the paired branch) also stops `UNRESOLVED (need >=2 arms ...)` from
    # being printed first -- a different claim from "this file could not be read".
    unreadable = []
    for dd in args.dir:
        for pp in sorted(glob.glob(os.path.join(dd, "*.json"))):
            if group_of(pp, groups) is None:
                continue
            if not arm_rows(pp):
                unreadable.append((os.path.basename(pp), unusable_reason(pp)))
    if unreadable:
        print("=" * 96)
        print("REFUSING: %d file(s) named a group but could not be read as an arm."
              % len(unreadable))
        for name, why in unreadable:
            print("  %-28s %s" % (name, why))
        print("  `n=0` and `not separated` are different claims; this run makes neither.")
        print("=" * 96)
        return 2

    if BENCH_CELL_USED:
        tally = {}
        for lab, n in BENCH_CELL_USED.values():
            tally[(lab, n)] = tally.get((lab, n), 0) + 1
        print("bench cells read (chosen per record by rule, never by position):")
        for (lab, n), cnt in sorted(tally.items()):
            print("  %-26s %d reps  x%d arm(s)" % (lab, n, cnt))

    by, skipped = collect(args.dir, groups)
    if skipped:
        print("skipped (group not identifiable or <2 usable requests): %s"
              % ", ".join(sorted(set(skipped))))

    print("=" * 96)
    for g in groups:
        arms = by.get(g) or []
        if len(arms) < 2:
            print("%-4s  %s" % (g, verdict({"n_arms": len(arms), "m_requests": 0}, args.f_crit)))
            continue
        d = decompose(arms)
        print("%-4s  n=%d arms x %d requests   grand mean %.2f t/s"
              % (g, d["n_arms"], d["m_requests"], d["grand_mean"]))
        print("      within-arm   sd %5.2f%%   (one request)" % (d["within_sd_pct"] or 0.0))
        ub = launch_sd_upper(d)
        print("      launch-level sd %5.2f%%   F=%.2f df=%s%s"
              % (d["launch_sd_pct"] or 0.0, d["F"], d["df"],
                 "" if ub is None else "   (95%% upper bound %.2f%%)" % ub))
        # The F gate is a quantile OF A DF, and the caller passes one for a design this run
        # may not have. K3_PAIR_CERT_V2's bench section is 4 arms x 3 reps => df=(3,8), whose
        # 95% point is 4.07, not the (4,10) default of 3.48. Gating 4.07 of evidence at 3.48
        # would declare a launch term the design cannot support, so say so out loud.
        if d["df"][0] > 0 and d["df"][1] > 0:
            dfc = f_ppf(0.95, d["df"][0], d["df"][1])
            if abs(dfc - args.f_crit) > 0.005:
                print("      !! F gate %.2f is the 95%% point of a DIFFERENT df; for df=%s it is"
                      " %.2f. The verdict below uses %.2f."
                      % (args.f_crit, d["df"], dfc, args.f_crit))
        print("      an arm MEAN scatters %5.2f%%  (%.2f%% of that is just request noise)"
              % (d["total_arm_mean_sd_pct"] or 0.0, d["expected_arm_mean_sd_pct"] or 0.0))
        tot = d["total_arm_mean_sd_pct"] or 0.0
        if tot > 0:
            # TWO different numbers, and mixing them is how "4 arms" gets believed:
            # n_se drives the STANDARD ERROR down to target; n_ci drives the 95%
            # CONFIDENCE INTERVAL half-width down to target. They differ by 1.96^2.
            n_se = (tot / args.target_pct) ** 2
            n_ci = (1.96 * tot / args.target_pct) ** 2
            print("      => %.0f arms/group for standard error %.1f%%; %.0f for a 95%% CI "
                  "half-width %.1f%%" % (n_se, args.target_pct, n_ci, args.target_pct))
            if ub is not None and ub > 0:
                tot_hi = ((ub ** 2) + (d["expected_arm_mean_sd_pct"] or 0.0) ** 2) ** 0.5
                print("      => worst case at that bound: %.0f (SE) / %.0f (CI)"
                      % ((tot_hi / args.target_pct) ** 2,
                         (1.96 * tot_hi / args.target_pct) ** 2))
        print("      first->last within arm: %s   (monotone decline %d/%d)"
              % (" ".join("%+.0f%%" % x for x in d["first_to_last_pct"]),
                 d["declines"], d["n_arms"]))
        print("      VERDICT: %s" % verdict(d, args.f_crit))
        print("-" * 96)

    if args.paired or args.sd_only:
        # Same loader the noise split uses (arm_rows), so the two modes can never
        # disagree about what one arm's number is.
        by_rep = {}
        for d in args.dir:
            for pp in sorted(glob.glob(os.path.join(d, "*.json"))):
                g = group_of(pp, groups)
                if g is None:
                    continue
                rr = arm_rows(pp)
                if not rr:                      # unreachable: refused up front (see main)
                    continue
                rep = os.path.basename(pp)[:-5].split("_")[0]
                by_rep.setdefault(rep, {}).setdefault(g, []).append(st.mean(rr))
        rows = paired_diffs(by_rep)

        if args.sd_only:
            print("=" * 96)
            print("PAIRED SD PILOT -- t and the verdict are withheld on purpose")
            for r in rows:
                print("  %-8s  %s %7.2f   %s %7.2f   diff %+6.2f t/s"
                      % (r["rep"], groups[0], r["a"], groups[1], r["b"], r["diff"]))
            if len(rows) < 2:
                print("  fewer than two pairs: no sd, no plan")
            else:
                sd = st.stdev([r["diff"] for r in rows])
                hi = paired_sd_upper(rows, 0.95)
                md = st.mean([r["diff"] for r in rows])
                print("-" * 96)
                print("  n=%d pairs   sd %.2f t/s   95%% upper bound %.2f t/s" % (len(rows), sd, hi))
                print("  planning diff %+.2f t/s -- OBSERVED, used only to size n. It is not a "
                      "finding and must not be reported as one." % md)
                for label, s in (("point sd", sd), ("sd upper bound", hi)):
                    N = planned_n_for(md, s, args.alpha)
                    print("  n needed at %-15s: %s" % (label, N if N else "> %d (cap)" % 40))
                print("  => pre-register the LARGER of the two, run to it, and judge once.")
            print("=" * 96)
            return 0

        v = paired_verdict(rows, args.planned_n, args.alpha)
        print("=" * 96)
        print("PAIRED EFFECT  (each row = one ABBA repetition, arm means)")
        for r in rows:
            print("  %-8s  %s %7.2f   %s %7.2f   diff %+6.2f t/s"
                  % (r["rep"], groups[0], r["a"], groups[1], r["b"], r["diff"]))
        if rows:
            print("-" * 96)
            print("  n=%d pairs   mean diff %+.2f t/s (%+.1f%%)   sd %.2f   SE %.2f   t=%.2f  "
                  "crit=%.2f (df=%d, alpha=%.2f)"
                  % (v["n"], v["mean_diff"], v["mean_diff_pct"], v["sd"], v["se"],
                     v["t"], v["crit"], v["df"], v["alpha"]))
            print("  %s slower in %d/%d pairs"
                  % (groups[1], v["slower_pairs"], v["n"]))
            print("  VERDICT: %s%s" % (v["class"], "" if "why" not in v else "  -- " + v["why"]))
            if args.planned_n:
                print("  (pre-registered n=%d; the honest number of MORE pairs to run is "
                      "%d, and the test happens once, at the end)"
                      % (args.planned_n, max(0, args.planned_n - v["n"])))
        else:
            print("  no complete pairs found")
        print("=" * 96)

    # The comparison the doc could not make by eye: which group carries a launch term.
    got = {}
    for g in groups:
        arms = by.get(g) or []
        got[g] = decompose(arms)["launch_sd_pct"] if len(arms) >= 2 else None
    if all(v is not None for v in got.values()) and len(groups) >= 2:
        hi = max(groups, key=lambda g: got[g] or 0.0)
        lo = min(groups, key=lambda g: got[g] or 0.0)
        print("asymmetry: %s carries a launch-level term of %.2f%%, %s carries %.2f%%"
              % (hi, got[hi] or 0.0, lo, got[lo] or 0.0))
        print("  => pairing (AB/BA) can only cancel a term BOTH groups share, so it cannot"
              " remove this one.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
