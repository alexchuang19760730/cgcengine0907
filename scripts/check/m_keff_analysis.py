#!/usr/bin/env python3
"""m_keff_analysis.py -- read a spec_cost_curve artifact and answer the question the sweep exists for.

WHY A SEPARATE READER
---------------------
`spec_cost_curve.py` reports `m` as a least-squares fit **through the origin** of (k_eff, cost-1).
That model says a verify round costs `1 + m*k` plain steps, i.e. the marginal token is the whole
cost and there is no per-round overhead. On the 2026-09-26 sweep (first one where `k_eff == k`, after
the MTP draft-chain fix) that model is REFUTED BY ITS OWN POINTS:

    k_eff   cost (E/S)      m = (cost-1)/k_eff
    1.00    1.85            0.854
    1.64    1.79            0.482
    1.97    1.46            0.235

`cost` is flat while `k_eff` doubles, so the through-origin fit returns m=0.404 with r2 = -2.74
(negative: worse than predicting the mean, which the tool also prints and which is easy to read past).
The two-parameter form

    cost(k) = 1 + F + m*k

has exactly one degree of freedom on three points, and the points put almost all of the loss in `F`:
a verify round carries a fixed surcharge of about half a plain step, and the width of the batch is
nearly free. That is the opposite of the "each verify token costs 98% of the first" story that has
been driving the draft-head work.

WHAT THIS DOES NOT DO
---------------------
It does not re-measure anything and it does not decide citability by opinion. A reading is quoted
only if: every contributing run applied its declared --warm-skip, every contributing run had
max(draft) == k, and the thermal sampler saw NOMINAL at launch. Otherwise the numbers are printed
with the reason they are not quotable, because the sweep above is thermally HEAVY from run 4 on and
E is non-monotone in k -- which the accept model cannot produce (more draft tokens cannot lower the
expected number committed), so the E medians are contaminated and say so here instead of in a
footnote.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def load_runs(path: Path) -> list[dict]:
    return json.loads(Path(path).read_text())["runs"]


def e_of(run: dict) -> float | None:
    """Tokens committed per verify round for one run. Re-derived from the counters, not trusted."""
    n, rounds = run.get("n_gen"), run.get("rounds")
    if not rounds:
        return None
    return round(n / rounds, 4) if n else None


def per_round(runs: list[dict]) -> list[dict]:
    """One row per (round, k): the paired ratio, the cost and the accept rate, side by side.

    Everything is taken WITHIN a round against that round's own k=0, which is the only pairing this
    box supports -- single-arm drift here is +/-1.9 t/s, larger than the effects being read.
    """
    rows: list[dict] = []
    by_round: dict[int, dict[int, dict]] = {}
    for r in runs:
        by_round.setdefault(r.get("round"), {})[r.get("k")] = r
    for rnd in sorted(by_round):
        base = by_round[rnd].get(0) or {}
        b = base.get("tps_tg")
        for k in sorted(by_round[rnd]):
            if k == 0 or not b:
                continue
            r = by_round[rnd][k]
            if not r.get("tps_tg"):
                continue
            e = e_of(r)
            s = r["tps_tg"] / b
            rows.append({
                "round": rnd, "k": k, "k_eff": r.get("mean_draft"),
                "E": e, "S": round(s, 4),
                "cost": round(e / s, 4) if e and s else None,
                "max_draft": r.get("max_draft"),
                "thermal_launch": ((r.get("thermal") or {}).get("launch") or {}).get("label"),
                "warm_skip_applied": r.get("warm_skip_applied"),
                # The reproducibility witness: the SAME (k, cell, seed) must repeat its E across
                # the pass's rounds. E is a counter ratio, so a pinned carrier makes that equality
                # exact -- no tolerance, and immune to thermal/swap/launch drift.
                "e_stable": r.get("e_stable"),
                "e_rounds": r.get("e_rounds"),
                # The carrier pin, as the engine reported it. E_warm is kept as a reported number:
                # the untimed and timed calls are two slices of one stream over two contexts, so
                # they cannot be expected to agree even when the carrier is pinned.
                "seed": r.get("seed"),
                "seed_engine": r.get("seed_engine"),
                "seed_applied": r.get("seed_applied"),
                # The third axis: a pinned seed is useless if the carrier is sampling a different
                # DISTRIBUTION from the one the profile's argv specifies -- measured 2026-09-26:
                # engine 0.80/0.95/40 vs the server's 0.4/0.8/0, on every k>0 run.
                "sampling_parity": r.get("sampling_parity"),
                "sampling_exported": r.get("sampling_exported"),
                "sampling_engine": r.get("sampling_engine"),
                "E_warm": r.get("E_warm"),
                "E_warm_delta": r.get("E_warm_delta"),
                "draft_hist": r.get("draft_hist"),
                "ms_per_step": r.get("ms_per_step"),
                "read_mib_per_step": r.get("read_mib_per_step"),
            })
    return rows


def fit_two(rows: list[dict]) -> dict:
    """cost = 1 + F + m*k_eff, least squares on (k_eff, cost-1). Reports F, m and r2."""
    pts = [(r["k_eff"], r["cost"] - 1.0) for r in rows if r.get("k_eff") and r.get("cost")]
    n = len(pts)
    if n < 2:
        return {"n_points": n, "verdict": "not enough points for a 2-parameter fit"}
    mx = statistics.fmean(x for x, _ in pts)
    my = statistics.fmean(y for _, y in pts)
    sxx = sum((x - mx) ** 2 for x, _ in pts)
    sxy = sum((x - mx) * (y - my) for x, y in pts)
    m = sxy / sxx if sxx else 0.0
    f = my - m * mx
    ss_res = sum((y - (f + m * x)) ** 2 for x, y in pts)
    ss_tot = sum((y - my) ** 2 for _, y in pts)
    return {"n_points": n, "F": round(f, 4), "m": round(m, 4),
            "r2": round(1.0 - ss_res / ss_tot, 4) if ss_tot else None,
            "through_origin": through_origin(pts)}


def through_origin(pts: list[tuple[float, float]]) -> dict:
    """The fit the sweep itself prints -- kept here so both can be read side by side."""
    sxy = sum(x * y for x, y in pts)
    sxx = sum(x * x for x, y in pts)
    m = sxy / sxx if sxx else 0.0
    ys = [y for _, y in pts]
    ybar = statistics.fmean(ys)
    ss_res = sum((y - m * x) ** 2 for x, y in pts)
    ss_tot = sum((y - ybar) ** 2 for y in ys)
    return {"m": round(m, 4), "r2": round(1.0 - ss_res / ss_tot, 4) if ss_tot else None}


def speedup(k: int, a: float, f: float, m: float) -> float:
    """S(k) = E(k)/cost(k) with a constant per-draft accept rate: E = sum_{j<=k} a^j."""
    e = sum(a ** j for j in range(k + 1))
    return e / (1.0 + f + m * k)


def best_k(a: float, f: float, m: float, kmax: int = 10) -> tuple[int, float, float]:
    """(k*, S(k*), S at k=1) over a small k grid -- this curve has no closed-form optimum."""
    cand = [(k, speedup(k, a, f, m)) for k in range(1, kmax + 1)]
    kbest, sbest = max(cand, key=lambda t: t[1])
    return kbest, round(sbest, 4), round(speedup(1, a, f, m), 4)


def acceptance_sensitivity(a: float, f: float, m: float, k: int, da: float = 0.01) -> float:
    """dS/da at fixed k, by central difference -- because it is what the decision needs."""
    return round((speedup(k, a + da, f, m) - speedup(k, a - da, f, m)) / (2 * da), 4)


def overhead_sensitivity(a: float, f: float, m: float, k: int, df: float = 0.01) -> float:
    return round((speedup(k, a, f + df, m) - speedup(k, a, f - df, m)) / (2 * df), 4)


def quotable(rows: list[dict]) -> tuple[bool, list[str]]:
    """Can any of this be quoted? The three gates, each named when it fires."""
    why = []
    bad_warm = [r for r in rows if r.get("warm_skip_applied") is False]
    if bad_warm:
        why.append(f"warm-skip did not take in {len(bad_warm)} run(s)")
    capped = [r for r in rows if r.get("max_draft") is not None and r.get("max_draft") != r.get("k")]
    if capped:
        why.append(f"max(draft) != k in {len(capped)} run(s)")
    heavy = [r for r in rows if r.get("thermal_launch") != "NOMINAL"]
    if heavy:
        why.append(f"{len(heavy)}/{len(rows)} runs launched outside NOMINAL "
                   f"(thermal: {sorted({r.get('thermal_launch') for r in heavy})})")
    # E must not decrease with k: the commit stream is the greedy stream whatever k is, so more
    # draft tokens can only add accepted ones. A decrease is a contamination witness, not a finding.
    med: dict[int, float] = {}
    for k in sorted({r["k"] for r in rows}):
        vals = [r["E"] for r in rows if r["k"] == k and r.get("E")]
        if vals:
            med[k] = statistics.median(vals)
    ks = sorted(med)
    bad = [(ks[i], med[ks[i]], ks[i + 1], med[ks[i + 1]]) for i in range(len(ks) - 1)
           if med[ks[i + 1]] < med[ks[i]]]
    if bad:
        why.append("E is not monotone in k: " +
                   "; ".join(f"E({a})={x:.3f} > E({b})={y:.3f}" for a, x, b, y in bad))
    # E must also be reproducible. The witness is now ACROSS rounds at a pinned carrier: the same
    # (k, cell, seed) has to give the same E every time, because E = 1 + accepted/rounds is a
    # counter ratio that no amount of thermal, swap or launch drift can move. On 2026-09-26 the
    # same k=2 config drew E=0.984 and E=1.442 (46% apart, pool hit rate flat at 41-44%) -- that
    # spread came from the sampler's seed being a fresh std::random_device draw per process, which
    # is what spec_cost_curve.py --seed pins. A carrier that was left unpinned is refused by name
    # here too, so an old artifact cannot slip a draw past this reader.
    unpinned = [r for r in rows if r.get("seed_applied") is False]
    if unpinned:
        why.append(f"the sampler seed did not reach the carrier in {len(unpinned)} arm(s) "
                   f"(k={unpinned[0].get('k')}, engine seed {unpinned[0].get('seed_engine')}): E is "
                   f"a per-process draw, so no S built on it describes the configuration")
    noparity = [r for r in rows if r.get("sampling_parity") is False]
    if noparity:
        why.append(f"the carrier sampled a different distribution from the profile's argv in "
                   f"{len(noparity)} arm(s): argv {noparity[0].get('sampling_exported')} vs engine "
                   f"{noparity[0].get('sampling_engine')}")
    unstable = [r for r in rows if r.get("e_stable") is False]
    if unstable:
        w = unstable[0]
        why.append(f"E does not repeat at a pinned carrier in {len(unstable)} arm(s): k={w.get('k')} "
                   f"E = {w.get('e_rounds') or w.get('E')}")
    return (not why), why


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default=str(ROOT / "Backup/phase_decomp/spec_cost_curve_keff_r2.json"))
    ap.add_argument("--engine-digest", default="", help="md5 pair of the build that produced it")
    ap.add_argument("--out", default="", help="write the analysis as JSON next to the artifact")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return selftest()

    runs = load_runs(Path(args.json))
    rows = per_round(runs)
    ok, why = quotable(rows)
    fit = fit_two(rows)

    print(f"artifact : {args.json}")
    print(f"runs     : {len(runs)}  ({len(rows)} paired rows)")
    print(f"quotable : {ok}" + ("" if ok else "  <- " + " | ".join(why)))
    print(f"digest   : {args.engine_digest or '(not supplied)'}")
    print()
    print("  k  k_eff    E      S     cost   ms/step  MiB/step  thermal   drafts")
    for k in sorted({r["k"] for r in rows}):
        sub = [r for r in rows if r["k"] == k]
        e = statistics.median([r["E"] for r in sub if r.get("E")])
        keff = statistics.median([r["k_eff"] for r in sub if r.get("k_eff")])
        s = statistics.median([r["S"] for r in sub])
        c = statistics.median([r["cost"] for r in sub if r.get("cost")])
        ms = statistics.median([r["ms_per_step"] for r in sub if r.get("ms_per_step")])
        mib = statistics.median([r["read_mib_per_step"] for r in sub if r.get("read_mib_per_step")])
        th = sorted({r.get("thermal_launch") for r in sub})
        print(f" {k}  {keff:5.2f} {e:6.3f} {s:6.3f} {c:6.3f}  {ms:7.1f}  {mib:8.1f}  "
              f"{','.join(str(t) for t in th):9s} {[r.get('draft_hist') for r in sub][0]}")
    print()
    to = fit.get("through_origin") or {}
    print(f"fit through origin (what the sweep prints): m={to.get('m')}  r2={to.get('r2')}")
    print(f"fit with the per-round term  cost = 1 + F + m*k_eff: F={fit.get('F')} m={fit.get('m')} "
          f"r2={fit.get('r2')}  (n={fit.get('n_points')})")

    f = max(fit.get("F") or 0.0, 0.0)
    m = max(fit.get("m") or 0.0, 0.0)
    print()
    print(f"k* and S at the measured overhead (F={f}, m={m}):")
    print("   accept a    k*    S(k*)   S(k=1)   dS/da@k*   dS/dF@k*")
    for a in (0.264, 0.35, 0.465, 0.60, 0.80):
        kbest, sbest, s1 = best_k(a, f, m)
        print(f"   {a:8.3f}  {kbest:3d}  {sbest:7.3f}  {s1:7.3f}  "
              f"{acceptance_sensitivity(a, f, m, kbest):10.4f}  {overhead_sensitivity(a, f, m, kbest):9.4f}")
    print()
    print("reading: dS/dF is the column that decides. Until F is attacked, no accept rate reaches 25 t/s.")
    if args.out:
        doc = {"artifact": args.json, "engine_digest": args.engine_digest, "quotable": ok,
               "not_quotable_because": why, "fit": fit, "rows": rows}
        Path(args.out).write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n")
        print(f"wrote {args.out}")
    return 0 if ok else 1


# ── self-test: the fixtures must include the shapes that made this reader necessary ────────────
def selftest() -> int:
    def run(rnd, k, tps, rounds, n=64, mean_draft=None, max_draft=None, thermal="NOMINAL",
            warm=True, **extra):
        return {"round": rnd, "k": k, "tps_tg": tps, "rounds": rounds, "n_gen": n,
                "mean_draft": mean_draft, "max_draft": max_draft, "warm_skip_applied": warm,
                "thermal": {"launch": {"label": thermal}}, "draft_hist": {str(max_draft or 1): rounds},
                **extra}

    # The shape that refutes the through-origin model: a round overhead that does not grow with k.
    # E rises with k (as it must), cost stays put, so the origin fit charges all of it to k.
    flat = [run(1, 0, 10.0, 0),
            run(1, 1, 9.104, 38, mean_draft=1.0, max_draft=1),   # E 1.684, cost 1.850
            run(1, 2, 9.610, 36, mean_draft=1.6, max_draft=2),   # E 1.778, cost 1.850
            run(1, 3, 9.990, 35, mean_draft=1.9, max_draft=3)]   # E 1.829, cost 1.830
    rows = per_round(flat)
    fit = fit_two(rows)
    to = fit["through_origin"]
    cases = [
        ("E is re-derived from counters, not read from the artifact", rows[0]["E"] == 1.6842),
        ("S is paired within the round", rows[0]["S"] == 0.9104),
        ("cost = E/S", rows[0]["cost"] == round(1.6842 / 0.9104, 4)),
        ("a flat cost curve gives the origin fit a negative r2", to["r2"] < 0),
        ("... while the 2-parameter fit puts the loss in F", fit["F"] > 0.5),
        ("... and its |m| is far below what the origin fit charges to k",
         abs(fit["m"]) < to["m"]),
        ("quotable when warm-skip took, k_eff matched and thermal is nominal",
         quotable(rows)[0] is True),
        ("at k=1 the break-even accept rate IS the round overhead (S==1 when a==F)",
         speedup(1, 0.5, 0.5, 0.0) == 1.0),
        ("... and below it, speculating loses", speedup(1, 0.3, 0.5, 0.0) < 1.0),
        ("a higher accept rate buys more speedup at the same overhead",
         speedup(4, 0.8, 0.5, 0.05) > speedup(4, 0.3, 0.5, 0.05)),
    ]
    # E falling with k is a contamination witness, not a finding
    nonmono = [run(1, 0, 10.0, 0), run(1, 1, 9.0, 33, mean_draft=1.0, max_draft=1),
               run(1, 2, 7.0, 64, mean_draft=1.5, max_draft=2)]
    ok, why = quotable(per_round(nonmono))
    cases += [
        ("E falling with k is caught", ok is False),
        ("... and named as non-monotone, not as noise", any("monotone" in w for w in why)),
    ]
    heavy = [run(1, 0, 10.0, 0), run(1, 1, 9.0, 32, mean_draft=1.0, max_draft=1, thermal="HEAVY")]
    ok2, why2 = quotable(per_round(heavy))
    cases += [
        ("a HEAVY launch is not quotable", ok2 is False),
        ("an E that does not repeat across rounds is not quotable",
         quotable(per_round([run(1, 0, 10.0, 0),
                             run(1, 1, 9.0, 40, mean_draft=1.0, max_draft=1,
                                 e_stable=False, e_rounds=[1.6, 1.19],
                                 E_warm=1.19, E_warm_delta=0.21)]))[0] is False),
        ("... and a wild warm-vs-timed gap alone is NOT a reason",
         not any("does not repeat" in w for w in
                 quotable(per_round([run(1, 0, 10.0, 0),
                                     run(1, 1, 9.0, 40, mean_draft=1.0, max_draft=1,
                                         e_stable=True, e_rounds=[1.19, 1.19],
                                         E_warm=0.90, E_warm_delta=0.29)]))[1])),
        ("an unpinned carrier is not quotable",
         quotable(per_round([run(1, 0, 10.0, 0),
                             run(1, 1, 9.0, 40, mean_draft=1.0, max_draft=1,
                                 e_stable=True, e_rounds=[1.19, 1.19],
                                 seed=20260926, seed_engine=None, seed_applied=False)]))[0] is False),
        ("... and it is named as the seed, not as noise",
         any("seed did not reach" in w for w in
             quotable(per_round([run(1, 0, 10.0, 0),
                                 run(1, 1, 9.0, 40, mean_draft=1.0, max_draft=1,
                                     e_stable=True, e_rounds=[1.19, 1.19],
                                     seed=20260926, seed_engine=None, seed_applied=False)]))[1])),
        ("a carrier that sampled a different distribution is not quotable",
         quotable(per_round([run(1, 0, 10.0, 0),
                             run(1, 1, 9.0, 40, mean_draft=1.0, max_draft=1,
                                 sampling_parity=False,
                                 sampling_exported={"CGC_SERVER_TEMP": "0.4"},
                                 sampling_engine={"temp": 0.8, "top_p": 0.95, "top_k": 40})]))[0]
         is False),
        ("... and the profile's argv is quoted, not a restatement",
         any("distribution" in w for w in
             quotable(per_round([run(1, 0, 10.0, 0),
                                 run(1, 1, 9.0, 40, mean_draft=1.0, max_draft=1,
                                     sampling_parity=False,
                                     sampling_exported={"CGC_SERVER_TEMP": "0.4"},
                                     sampling_engine={"temp": 0.8, "top_p": 0.95,
                                                      "top_k": 40})]))[1])),
        ("a pinned carrier is not refused for the seed",
         not any("seed" in w for w in
                 quotable(per_round([run(1, 0, 10.0, 0),
                                     run(1, 1, 9.0, 40, mean_draft=1.0, max_draft=1,
                                         e_stable=True, e_rounds=[1.19, 1.19],
                                         seed=20260926, seed_engine=20260926,
                                         seed_applied=True)]))[1])),
        ("... and the thermal label is the reason", any("NOMINAL" in w for w in why2)),
        ("a capped run is not quotable",
         not quotable(per_round([run(1, 0, 10.0, 0),
                                 run(1, 2, 8.0, 64, mean_draft=1.0, max_draft=1)]))[0]),
        ("an unapplied warm-skip is not quotable",
         not quotable(per_round([run(1, 0, 10.0, 0),
                                 run(1, 1, 9.0, 32, mean_draft=1.0, max_draft=1, warm=False)]))[0]),
        ("k=0 alone cannot fit", fit_two(per_round([run(1, 0, 10.0, 0)]))["n_points"] == 0),
    ]
    bad = 0
    for name, okk in cases:
        print(f"  [{'ok' if okk else 'FAIL'}] {name}")
        bad += 0 if okk else 1
    print(f"m_keff_analysis selftest: {len(cases) - bad}/{len(cases)} passed")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
