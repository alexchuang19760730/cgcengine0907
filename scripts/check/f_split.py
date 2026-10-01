#!/usr/bin/env python3
"""f_split.py -- where a spec round's time goes, layer by layer, and (when the pair is certifiable)
where the per-round surcharge F goes.

THE QUANTITY
------------
`docs/M_PER_TOKEN_AFTER_KEFF_2026-09-26.md` replaced the linear `m` model with

    cost(k) = 1 + F + m*k_eff          F ~ 0.77 plain steps, m ~ -0.03 (r2 ~ 0.007)

i.e. entering a spec round costs a plain step plus a fixed **F ~ 82 ms**, and the batch width is
nearly free. F is a per-ROUND quantity and it had never been attributed. This tool attributes the
round's own cost per layer, and attributes F when the pair can carry it.

TWO POPULATIONS, NOT ONE (this is the correction that made the first version refuse)
-----------------------------------------------------------------------------------
The instrument (`ggml-backend.cpp`, `dp_step % 8 == 0 || dp_step == 1 || dp_ntok > 1`) emits

  * every graph with ntok > 1 (the verify graphs), and
  * ONE IN EIGHT graphs with ntok == 1 (the draft forwards, and the 2-segment sampler graphs).

So an average taken over the emitted steps is **not** an average over rounds: it over-represents
verify graphs by construction. The step indices are consecutive graph indices, so the graphs the
emit rule hides can be counted exactly ( `max_idx - min_idx + 1 - n_emitted` ), and their cost is
estimated from the emitted ntok==1 sample. That reconstruction is a *named* quantity in the output
(`hidden_ntok1`) -- it is the largest single blind spot in this instrument and it is never folded
into a bucket silently.

TWO CLAIMS, TWO GATES
---------------------
  (1) WITHIN-LAUNCH: the round's per-layer profile (hook / barrier / device / dispatch), from the ON
      arm alone. Drift-immune, published whenever the per-graph closure holds.
  (2) CROSS-ARM: `F = per-round ON - per-step OFF`, attributed as `d_l = on_round_l - off_l`.
      Published only if the two launches were in the same regime -- checked on the arms' *common
      shape* (the prefill graph, present in both) and on the sign of F. A surcharge is positive; a
      negative F is a regime difference wearing an effect's clothes.

COLUMNS
-------
`CGC-DECPROF all:` rows, positional order wait, cb, submit (a previous analysis read group(2) as `cb`
and printed values an order of magnitude too big -- see decode_layer_cb.py), plus the GPU tail with
CGC_GPU_TIMING: `union` (device span), `gap` (device idle inside it), `gpu` (device busy).

    dispatch = d(submit)              submitting the layer's segments
    hook     = d(cb)                  expert-cache top-k hook: fills, slot ops, gather bookkeeping
    barrier  = d(wait - union - gap)  host sync inside the eval window that is NOT device time
    device   = d(union)               the layer's device span
    nonlayer = the step total minus the layer rows (embedding / head / sampler segments)

USAGE
    python3 scripts/check/f_split.py --selftest
    python3 scripts/check/f_split.py --on on.log --off off.log --spec-n-max 3 --rounds-on 39
"""

from __future__ import annotations

import argparse
import collections
import json
import re
import statistics
import sys
import time
from pathlib import Path

# A step header. wait/cb/submit are the step's own additive channels; the GPU tail is optional.
STEP = re.compile(
    r"CGC-DECPROF: step=(?P<idx>\d+) segs=(?P<segs>\d+) layers=(?P<layers>\d+) "
    r"total=(?P<total>[\d.]+) ms \| wait=(?P<wait>[\d.]+) \(\d+%\) cb=(?P<cb>[\d.]+) \(\d+%\) "
    r"submit=(?P<submit>[\d.]+) \(\d+%\) ntok=(?P<ntok>\d+)")
LAYER = re.compile(
    r"CGC-DECPROF all: L(?P<layer>\d+) wait=(?P<wait>[\d.]+) cb=(?P<cb>[\d.]+) "
    r"submit=(?P<submit>[\d.]+) ms gpu=(?P<gpu>[\d.]+) union=(?P<union>[\d.]+) gap=(?P<gap>[\d.]+)")
LAYER_NOGPU = re.compile(
    r"CGC-DECPROF all: L(?P<layer>\d+) wait=(?P<wait>[\d.]+) cb=(?P<cb>[\d.]+) "
    r"submit=(?P<submit>[\d.]+) ms")

CHANNELS = ("wait", "cb", "submit", "union", "gap", "gpu")
FULL_MIN_LAYERS = 35       # a full network graph; below this is the 2-segment sampler graph
PRINT_QUANT_MS = 0.01      # the instrument prints two decimals
CLOSURE_ABS_MS = 0.05      # so a 0.71 ms graph is allowed 5 x quantization, not 1%

# ── parse ────────────────────────────────────────────────────────────────────────────────────────


def parse_log(path: Path) -> dict:
    """Steps in graph order, each carrying the per-layer rows emitted beneath it."""
    text = path.read_text(errors="replace")
    hits = list(STEP.finditer(text))
    steps = []
    for i, m in enumerate(hits):
        end = hits[i + 1].start() if i + 1 < len(hits) else len(text)
        body = text[m.end():end]
        rows = []
        for rm in LAYER.finditer(body):
            d = rm.groupdict()
            d["_gpu"] = True
            rows.append(d)
        if not rows:
            for rm in LAYER_NOGPU.finditer(body):
                d = rm.groupdict()
                d["_gpu"] = False
                rows.append(d)
        steps.append({"idx": int(m["idx"]), "segs": int(m["segs"]), "layers": int(m["layers"]),
                      "ntok": int(m["ntok"]), "total": float(m["total"]), "wait": float(m["wait"]),
                      "cb": float(m["cb"]), "submit": float(m["submit"]), "rows": rows})
    return {"path": str(path), "steps": steps}


# ── classify: the two populations + the graphs the emit rule hides ───────────────────────────────


def classify(parsed: dict, k: int, full_min: int) -> dict:
    steps = parsed["steps"]
    prefill = [s for s in steps if s["ntok"] > k + 1]
    dec = [s for s in steps if s["ntok"] <= k + 1]
    full = [s for s in dec if s["layers"] >= full_min]
    trivia = [s for s in dec if s["layers"] < full_min]
    if not dec:
        return {"steps": steps, "prefill": prefill, "full": [], "trivial": trivia,
                "hidden": 0, "hidden_full": 0.0, "sample_ntok1": [], "frac_full": None}
    idx = [s["idx"] for s in dec]
    hidden = (max(idx) - min(idx) + 1) - len(dec)          # every hidden graph has ntok == 1
    sample = [s for s in full if s["ntok"] == 1]
    n1 = len(sample) + len(trivia)
    frac_full = (len(sample) / n1) if n1 else None
    return {"steps": steps, "prefill": prefill, "full": full, "trivial": trivia,
            "hidden": hidden, "hidden_full": (hidden * frac_full) if frac_full is not None else 0.0,
            "sample_ntok1": sample, "frac_full": frac_full,
            "idx_min": min(idx), "idx_max": max(idx)}


def _row_sum(step: dict) -> float:
    return sum(float(r["wait"]) + float(r["cb"]) + float(r["submit"]) for r in step["rows"])


def _layer_sums(steps: list[dict]) -> dict[int, dict[str, float]]:
    acc: dict[int, dict[str, float]] = collections.defaultdict(lambda: collections.defaultdict(float))
    for s in steps:
        for r in s["rows"]:
            l = int(r["layer"])
            for c in CHANNELS:
                acc[l][c] += float(r[c])
    return acc


def round_profile(arm: dict, rounds: float) -> dict | None:
    """Per-round per-layer channels for the ON arm: emitted graphs + the reconstructed hidden ones."""
    if rounds <= 0:
        return None
    sums = _layer_sums(arm["full"])
    sample = arm["sample_ntok1"]
    hf = arm["hidden_full"]
    if hf > 0 and not sample:
        return None                                  # cannot reconstruct: say so instead of assuming
    scale = (hf / len(sample)) if sample else 0.0
    layers = set(sums)
    for s in sample:
        for r in s["rows"]:
            layers.add(int(r["layer"]))
    out = {}
    for l in layers:
        ch = {c: sums.get(l, {}).get(c, 0.0) for c in CHANNELS}
        if scale:
            for s in sample:
                for r in s["rows"]:
                    if int(r["layer"]) == l:
                        for c in CHANNELS:
                            ch[c] += scale * float(r[c])
        out[l] = {c: v / rounds for c, v in ch.items()}
    return out


def pool(paths: list, k: int) -> dict:
    """Several logs as one arm (an interleaved pass is several launches but one population).

    Decode indices are translated as a block per log so their internal gaps -- the graphs the emit
    rule hid -- survive, and the blocks are laid end to end so the seam adds no gap either. The
    prefill graphs go to a negative band, because they are classified by `ntok` and must not
    stretch the decode index range (that would count a hidden graph per seam; the self-test pins it).
    """
    steps: list = []
    nxt, free = 0, -1
    for p in paths:
        pr = parse_log(Path(p))
        dec = [s for s in pr["steps"] if s["ntok"] <= k + 1]
        if dec:
            base = min(s["idx"] for s in dec)
            for s in dec:
                steps.append({**s, "idx": s["idx"] - base + nxt})
            nxt = max(s["idx"] for s in steps) + 1
        for s in pr["steps"]:
            if s["ntok"] > k + 1:
                steps.append({**s, "idx": free})
                free -= 1
    return {"path": list(paths), "steps": steps}


def by_width_arm(a_on: dict) -> list[dict]:
    """The ON arm's own full graphs grouped by their own width -- drift-immune, no OFF arm needed.

    `ntok` is the MoE top-k tensor's token count (`ggml-backend.cpp`: `dp_ntok = ttopk->ne[1]`),
    i.e. the graph states its own width. Grouping by it is how the marginal token gets attributed
    inside one launch, where no launch drift can enter.
    """
    groups: dict[int, list[dict]] = collections.defaultdict(list)
    for s in a_on["full"]:
        groups[s["ntok"]].append(s)
    out = []
    for nt in sorted(groups):
        g = groups[nt]
        d = {"ntok": nt, "n": len(g),
             "total_ms": round(statistics.fmean(s["total"] for s in g), 2),
             "per_token_ms": round(statistics.fmean(s["total"] for s in g) / nt, 2),
             "union_sum_ms": round(statistics.fmean(
                 sum(float(r["union"]) for r in s["rows"]) for s in g), 2)}
        for c in ("wait", "cb", "submit"):
            d[f"{c}_ms"] = round(statistics.fmean(s[c] for s in g), 2)
        out.append(d)
    return out


def _buckets(prof: dict[int, dict[str, float]]) -> dict[str, float]:
    b = collections.defaultdict(float)
    for l, ch in prof.items():
        b["hook"] += ch["cb"]
        b["barrier"] += ch["wait"] - ch["union"] - ch["gap"]
        b["dispatch"] += ch["submit"]
        b["device"] += ch["union"]
        b["gap_inside_device"] += ch["gap"]
        b["row"] += ch["wait"] + ch["cb"] + ch["submit"]
    return dict(b)


def _closure(arm: dict) -> float | None:
    worst = None
    for s in arm["steps"]:
        if s["total"] <= 0:
            continue
        got = s["wait"] + s["cb"] + s["submit"]
        msg = abs(got - s["total"])
        if msg <= CLOSURE_ABS_MS:
            continue
        rel = msg / s["total"]
        worst = rel if worst is None else max(worst, rel)
    return worst


# ── split ────────────────────────────────────────────────────────────────────────────────────────


def split(on: dict, off: dict, k: int, tol: float, rounds_on: float | None,
          full_min: int = FULL_MIN_LAYERS) -> dict:
    refused: list[str] = []
    a_on, a_off = classify(on, k, full_min), classify(off, k, full_min)
    res: dict = {"ok": False, "refused_by": refused, "spec_n_max": k}

    # ── the within-launch claim (1): the round's own profile, drift-immune
    # The round count is NOT in the log (the bench's `rounds=` is in the sweep summary, and in the
    # logs of 2026-09-26 there are two verify graphs per round). Default to one round per verify
    # graph and require --rounds-on whenever the caller knows better.
    verify_graphs = [s for s in a_on["full"] if s["ntok"] >= 2]
    rounds = rounds_on if rounds_on else float(len(verify_graphs))
    prof = round_profile(a_on, rounds)
    on_prof_ok = prof is not None
    if not on_prof_ok:
        refused.append("ON arm: hidden ntok==1 graphs are all full and none was emitted "
                       "(emit rule is 1-in-8) -- the round cannot be reconstructed from this log")
    if not a_on["full"]:
        refused.append("ON arm has no full decode graphs (CGC_DECODE_PROFILE_ALL missing?)")
    if a_on["hidden"] > 0 and a_on["frac_full"] is None:
        refused.append("ON arm: the emit rule hid ntok==1 graphs and emitted none of them, so the "
                       "round's draft forwards cannot even be classified -- no split possible")
    if not a_off["full"]:
        refused.append("OFF arm has no full decode graphs (CGC_DECODE_PROFILE_ALL missing?)")

    # ── gate: closure, quantization-aware
    for name, arm in (("ON", a_on), ("OFF", a_off)):
        w = _closure(arm)
        if w is not None:
            refused.append(f"{name} arm fails the step closure total=wait+cb+submit (worst {w:.3%})")
    res["closure"] = {"on": _closure(a_on), "off": _closure(a_off)}

    # ── gate: the width really is k+1 (same k_eff gate the cost curve carries, on this carrier)
    on_ntok = sorted({s["ntok"] for s in a_on["full"]})
    if on_ntok and max(on_ntok) != k + 1:
        refused.append(f"ON arm's widest verify graph is ntok={max(on_ntok)}, expected k+1={k+1}")
    off_ntok = sorted({s["ntok"] for s in a_off["full"]})
    if off_ntok and off_ntok != [1]:
        refused.append(f"OFF arm's full graphs have ntok={off_ntok}, expected [1] (plain steps)")

    # ── gate: same regime. The two arms' COMMON shape is the prefill graph, present in both.
    regime = None
    if a_on["prefill"] and a_off["prefill"]:
        po = statistics.fmean(s["total"] for s in a_on["prefill"])
        pf = statistics.fmean(s["total"] for s in a_off["prefill"])
        regime = {"prefill_ms_on": round(po, 2), "prefill_ms_off": round(pf, 2),
                  "ratio_on_over_off": round(po / pf, 3) if pf else None}
        if pf and not (0.5 <= po / pf <= 2.0):
            refused.append(f"the two launches are not in the same regime: their common-shape "
                           f"prefill graph is {po/pf:.1f}x apart (ON {po:.0f} ms vs OFF {pf:.0f} ms) "
                           f"-- F from this pair is a regime difference, not an effect")
    res["regime"] = regime

    # ── claim (2): F and its per-layer attribution
    f_ms = None
    if on_prof_ok and a_off["full"]:
        f_ms = _round_total(a_on, rounds) - statistics.fmean(s["total"] for s in a_off["full"])

    if f_ms is not None and f_ms <= 0:
        refused.append(f"F came out {f_ms:.1f} ms <= 0: a spec round cannot be cheaper than the "
                       f"plain step it replaces, so this pair is measuring the box, not the round")

    # ── the per-round profile, published on its own gate
    res["round"] = None
    if on_prof_ok:
        b = _buckets(prof)
        cov = (b["row"] / rounds) if False else None
        rows_per_round = sum(_row_sum(s) for s in a_on["full"]) / rounds
        if a_on["sample_ntok1"]:
            rows_per_round += (a_on["hidden_full"] / rounds
                               * statistics.fmean(_row_sum(s) for s in a_on["sample_ntok1"]))
        tot_per_round = _round_total(a_on, rounds)
        res["round"] = {"rounds": rounds, "total_ms": round(tot_per_round, 3),
                        "buckets_ms": {kk: round(v, 3) for kk, v in b.items()},
                        "rows_ms": round(rows_per_round, 3),
                        "nonlayer_ms": round(tot_per_round - rows_per_round, 3),
                        "hidden_ntok1": round(a_on["hidden_full"], 2),
                        "hidden_ntok1_share_of_round": round(
                            (a_on["hidden_full"] / rounds
                             * statistics.fmean(s["total"] for s in a_on["sample_ntok1"])
                             / tot_per_round) if a_on["sample_ntok1"] and tot_per_round else 0.0, 4),
                        "per_layer": [{"layer": l, **{c: round(v, 4) for c, v in ch.items()}}
                                      for l, ch in sorted(prof.items())]}

    # ── the ON arm's own width curve: `row = device + gap + barrier + hook + dispatch` holds
    # identically, so a negative barrier is a statement about overlapping device spans, not a bucket
    res["by_width"] = by_width_arm(a_on)
    if res["round"]:
        b = res["round"]["buckets_ms"]
        if b["barrier"] < 0:
            res["round"]["barrier_flag"] = (
                "NOT SEPARABLE: the layer-summed device span exceeds the host window by "
                f"{-b['barrier']:.2f} ms/round, so 'host sync' is not a positive bucket here -- the "
                "per-layer device spans overlap each other and outlive the eval window")
        else:
            res["round"]["barrier_flag"] = ""

    # ── F: the per-layer deltas, and the rows-must-add-up gate
    per_layer, gap = [], None
    if f_ms is not None and a_off["full"]:
        off_prof = {l: {c: v / len(a_off["full"]) for c, v in ch.items()}
                    for l, ch in _layer_sums(a_off["full"]).items()}
        for l in sorted(set(prof) | set(off_prof)):
            o, f = prof.get(l, {}), off_prof.get(l, {})
            d = {c: o.get(c, 0.0) - f.get(c, 0.0) for c in CHANNELS}
            d.update({"layer": l, "hook": d["cb"], "barrier": d["wait"] - d["union"] - d["gap"],
                      "dispatch": d["submit"], "device": d["union"],
                      "row": d["wait"] + d["cb"] + d["submit"]})
            per_layer.append(d)
        b = collections.defaultdict(float)
        for d in per_layer:
            for kk in ("hook", "barrier", "dispatch", "device", "row"):
                b[kk] += d[kk]
        gap = f_ms - b["row"]
        if abs(gap) > tol * abs(f_ms):
            refused.append(f"rows do not add up to F: sum(d_row)={b['row']:.2f} ms vs F={f_ms:.2f} ms "
                           f"(nonlayer {gap:+.2f} ms > tol {tol:.0%})")
        res["buckets_ms"] = {kk: round(v, 3) for kk, v in b.items()}
        res["buckets_share_of_F"] = {kk: (round(v / f_ms, 4) if f_ms else None)
                                     for kk, v in b.items()}
        res["nonlayer_ms"] = round(gap, 3)
        res["per_layer"] = sorted(per_layer, key=lambda d: -d["row"])

    res["F_ms"] = round(f_ms, 3) if f_ms is not None else None
    res["F_in_plain_steps"] = (round(f_ms / statistics.fmean(s["total"] for s in a_off["full"]), 4)
                               if f_ms is not None and a_off["full"] else None)
    res["off_step_ms"] = (round(statistics.fmean(s["total"] for s in a_off["full"]), 3)
                          if a_off["full"] else None)
    res["on_round_ms"] = res["round"]["total_ms"] if res["round"] else None
    res["ok"] = not refused and res["round"] is not None and res["F_ms"] is not None
    res["populations"] = {
        "on": {"graphs": len(a_on["steps"]), "full": len(a_on["full"]),
               "trivial": len(a_on["trivial"]), "hidden_ntok1": a_on["hidden"],
               "hidden_full": round(a_on["hidden_full"], 2),
               "ntok_hist": dict(collections.Counter(s["ntok"] for s in a_on["steps"])),
               "prefill": len(a_on["prefill"])},
        "off": {"graphs": len(a_off["steps"]), "full": len(a_off["full"]),
                "trivial": len(a_off["trivial"]), "hidden_ntok1": a_off["hidden"],
                "ntok_hist": dict(collections.Counter(s["ntok"] for s in a_off["steps"])),
                "prefill": len(a_off["prefill"])}}
    return res


def _round_total(a_on: dict, rounds: float) -> float:
    tot = sum(s["total"] for s in a_on["full"])
    if a_on["sample_ntok1"]:
        tot += (a_on["hidden_full"] * statistics.fmean(s["total"] for s in a_on["sample_ntok1"]))
    return tot / rounds if rounds else 0.0


def report(res: dict, top: int) -> int:
    if not res["ok"]:
        print("REFUSED -- the split is not certified:", file=sys.stderr)
        for why in res["refused_by"]:
            print(f"  - {why}", file=sys.stderr)
    if res.get("round"):
        r = res["round"]
        print(f"WITHIN-LAUNCH (ON arm, drift-immune): {r['rounds']:.0f} rounds, "
              f"{r['total_ms']:.2f} ms/round")
        b = r["buckets_ms"]
        for kk, label in (("hook", "hook (d(cb): fills + slot ops + gather bookkeeping)"),
                          ("barrier", "barrier (d(wait-union-gap): host sync)"),
                          ("device", "device (d(union): the layer's device span)"),
                          ("dispatch", "dispatch (d(submit))")):
            print(f"  {label:<58s} {b[kk]:8.2f} ms  {b[kk]/r['total_ms']:6.2%}")
        print(f"  {'rows sum (layer-attributable)':<58s} {r['rows_ms']:8.2f} ms  "
              f"{r['rows_ms']/r['total_ms']:6.2%}")
        print(f"  {'nonlayer (embedding / head / sampler segments)':<58s} {r['nonlayer_ms']:8.2f} ms  "
              f"{r['nonlayer_ms']/r['total_ms']:6.2%}")
        print(f"  hidden ntok==1 graphs per round: {r['hidden_ntok1']:.2f} "
              f"({r['hidden_ntok1_share_of_round']:.1%} of the round) <- the emit rule's blind spot")
        if r.get("barrier_flag"):
            print(f"  barrier: {r['barrier_flag']}")
    if res.get("by_width"):
        print("\n  the ON arm's own width curve (one launch, no launch drift):")
        print("    ntok    n   total_ms  per_token_ms   wait     cb  submit   union_sum")
        for d in res["by_width"]:
            print(f"    {d['ntok']:>4} {d['n']:>4} {d['total_ms']:>10.2f} {d['per_token_ms']:>13.2f} "
                  f"{d['wait_ms']:>6.2f} {d['cb_ms']:>6.2f} {d['submit_ms']:>6.2f} "
                  f"{d['union_sum_ms']:>11.2f}")
    if res["F_ms"] is not None:
        print(f"\nCROSS-ARM F : ON {res['on_round_ms']} ms/round  -  OFF {res['off_step_ms']} ms/step "
              f"= {res['F_ms']:.2f} ms  ({res['F_in_plain_steps']} plain steps)")
    if res.get("regime"):
        g = res["regime"]
        print(f"regime witness (common prefill graph): ON {g['prefill_ms_on']} ms vs "
              f"OFF {g['prefill_ms_off']} ms  = {g['ratio_on_over_off']}x")
    if res["ok"] and "buckets_ms" in res:
        b, sh = res["buckets_ms"], res["buckets_share_of_F"]
        print(f"\n  F attributed per layer (rows sum {b['row']:.2f} ms, nonlayer "
              f"{res['nonlayer_ms']:+.2f} ms):")
        for kk, label in (("hook", "hook"), ("barrier", "barrier"), ("device", "device"),
                          ("dispatch", "dispatch")):
            print(f"    {label:<9s} {b[kk]:8.2f} ms  {sh[kk]:6.2%} of F")
        print(f"\n  top {top} layers by d(wait+cb+submit):")
        print("    layer      row     hook   barrier   device  dispatch")
        for d in res["per_layer"][:top]:
            print(f"    L{d['layer']:<4d} {d['row']:8.2f} {d['hook']:8.2f} {d['barrier']:9.2f} "
                  f"{d['device']:8.2f} {d['dispatch']:9.2f}")
    if not res["ok"]:
        print("\n  (cross-arm buckets withheld -- see the refusals above)")
    p = res["populations"]
    for arm in ("on", "off"):
        i = p[arm]
        print(f"  {arm:>3} arm: graphs={i['graphs']} full={i['full']} trivial={i['trivial']} "
              f"hidden_ntok1={i['hidden_ntok1']} prefill={i['prefill']} ntok={i['ntok_hist']}")
    print(f"  closure (worst, quantization-aware): on={res['closure']['on']} off={res['closure']['off']}")
    return 0 if res["ok"] else 2


# ── self-test: the fixtures that MUST fail are the point ─────────────────────────────────────────
def _step(idx, total, wait, cb, submit, ntok, layers=41, segs=41):
    return (f"CGC-DECPROF: step={idx} segs={segs} layers={layers} total={total:.2f} ms | "
            f"wait={wait:.2f} (90%) cb={cb:.2f} (5%) submit={submit:.2f} (5%) ntok={ntok}\n")


def _layers(cb=0.2, submit=0.1, union=0.6, gap=0.1, gpu=0.5, wait=1.0, n=40):
    return "".join(f"CGC-DECPROF all: L{l} wait={wait:.2f} cb={cb:.2f} submit={submit:.2f} ms "
                   f"gpu={gpu:.2f} union={union:.2f} gap={gap:.2f} sg=6 n=4 st=0.0 en=1.0\n"
                   for l in range(n))


def _graph(idx, ntok, rounds_layers, layer_cb=0.2, layer_wait=1.0, trivial=False):
    if trivial:
        return _step(idx, 0.71, 0.62, 0.01, 0.08, ntok, layers=1, segs=2) + \
               "CGC-DECPROF all: L0 wait=0.62 cb=0.01 submit=0.08 ms gpu=0.1 union=0.1 gap=0.0\n"
    rows_total = 40 * (layer_wait + 0.2 + 0.1)
    return _step(idx, rows_total, 40 * layer_wait, 40 * 0.2, 40 * 0.1, ntok, rounds_layers) + \
           _layers(wait=layer_wait, cb=layer_cb)


def _mk(tmp, name, txt):
    p = Path(tmp) / name
    p.write_text(txt)
    return p


def selftest() -> int:
    import tempfile
    cases = []
    with tempfile.TemporaryDirectory() as td:
        # A pair in the same regime: identical prefill graphs; OFF plain steps at 55.1 ms total
        # (rows 1.0+0.2+0.1 x40 = 52.0 + nonlayer 3.1), ON rounds = 1 verify(4) + 3 hidden drafts.
        pre_on = _graph(1, 512, 41, layer_wait=5.0)
        pre_off = _graph(1, 512, 41, layer_wait=5.0)
        on_txt = pre_on
        off_txt = pre_off
        # ON: rounds at idx 2..(2+4*6-1): 4 drafts (idx%8!=0 -> hidden, idx 8 emitted) + 1 verify
        for r in range(6):
            base = 2 + 4 * r
            on_txt += _graph(base, 1, 40, layer_wait=2.0)          # hidden draft forward
            on_txt += _graph(base + 1, 4, 41, layer_wait=3.0)      # verify, rows 132
        # one emitted draft sample at an idx where %8==0 to make hidden_full countable
        on_txt += _graph(8, 1, 40, layer_wait=2.0)
        for r in range(6):
            off_txt += _graph(2 + r, 1, 41, layer_wait=1.0)
        on_p, off_p = _mk(td, "on.log", on_txt), _mk(td, "off.log", off_txt)
        A = classify(parse_log(on_p), 3, FULL_MIN_LAYERS)
        cases += [
            ("decode graphs are separated from the prefill graph", len(A["prefill"]) == 1),
            ("full and trivial graphs are separated", all(s["layers"] >= 35 for s in A["full"])),
            ("the emit rule's hidden graphs are counted, not ignored", A["hidden"] > 0),
            ("hidden graphs are attributed as full ntok==1 graphs",
             abs(A["hidden_full"] - A["hidden"]) < 1e-9),
        ]
        on, off = parse_log(on_p), parse_log(off_p)
        res = split(on, off, 3, 0.15, None)
        cases += [
            ("a same-regime pair is certified", res["ok"] is True),
            ("the round profile is published", res["round"] is not None),
            ("F equals ON round minus OFF step",
             abs(res["F_ms"] - (res["on_round_ms"] - res["off_step_ms"])) < 1e-6),
            ("buckets sum to F within tol", abs(res["nonlayer_ms"]) <= 0.15 * abs(res["F_ms"])),
            ("hook is d(cb), not d(wait)",
             res["buckets_ms"]["hook"] < res["buckets_ms"]["barrier"]),
            ("the hidden draft forwards are a named share of the round",
             res["round"]["hidden_ntok1_share_of_round"] > 0),
            ("the width curve is reported from the ON arm alone, verify widths only",
             [d["ntok"] for d in res["by_width"] if d["ntok"] >= 2] == [4]),
            ("a negative barrier is flagged, not published as host sync",
             res["round"]["barrier_flag"] == "" or "NOT SEPARABLE" in res["round"]["barrier_flag"]),
        ]

        # (1) a pair whose prefill graphs differ by 18x must refuse, and name the regime
        pre_total = f"{40 * (5.0 + 0.2 + 0.1):.2f}"
        bad_regime = on_txt.replace(f"total={pre_total} ms",
                                    f"total={float(pre_total) * 18:.2f} ms", 1)
        r_br = split(parse_log(_mk(td, "on_reg.log", bad_regime)), off, 3, 0.15, None)
        cases += [("an 18x prefill gap is refused as a regime difference",
                   any("not in the same regime" in w for w in r_br["refused_by"]))]

        # (2) the print-quantization trap: a 0.71 ms graph must NOT fail the closure
        tiny = on_txt + _graph(700, 1, 1, trivial=True)
        r_q = split(parse_log(_mk(td, "on_q.log", tiny)), off, 3, 0.15, None)
        cases += [("a 0.71 ms graph does not break the closure (2-decimal print)",
                   not any("closure" in w for w in r_q["refused_by"]))]

        # (3) a negative F must refuse: a spec round cannot be cheaper than the step it replaces
        slow_off = pre_off + "".join(_graph(2 + r, 1, 41, layer_wait=20.0) for r in range(6))
        r_neg = split(on, parse_log(_mk(td, "off_neg.log", slow_off)), 3, 0.15, None)
        cases += [("a negative F is refused", any("F came out" in w for w in r_neg["refused_by"]))]

        # (4) an ON arm that never reached width k+1 must refuse (the k_eff gate)
        r_w = split(parse_log(_mk(td, "on_w.log", on_txt.replace("ntok=4", "ntok=2"))), off,
                    3, 0.15, None)
        cases += [("an ON arm that never reached k+1 is refused",
                   any("widest verify graph" in w for w in r_w["refused_by"]))]

        # (5) an OFF arm that is not plain must refuse
        r_o = split(on, parse_log(_mk(td, "off_w.log", off_txt.replace("ntok=1", "ntok=3"))),
                    3, 0.15, None)
        cases += [("an OFF arm whose graphs are not plain is refused",
                   any("expected [1]" in w for w in r_o["refused_by"]))]

        # (6) no ntok==1 graph emitted at all -> the hidden graphs cannot even be classified
        no_sample = "".join(_graph(2 + 4 * r + 1, 4, 41, layer_wait=3.0) for r in range(6))
        no_sample = pre_on + no_sample
        r_ns = split(parse_log(_mk(td, "on_ns.log", no_sample)), off, 3, 0.15, None)
        cases += [("an ON arm whose hidden graphs were never sampled is refused",
                   any("cannot even be classified" in w for w in r_ns["refused_by"]))]

        # (8) pooling two launches must not invent a gap at the seam
        h_on = classify(parse_log(on_p), 3, FULL_MIN_LAYERS)["hidden"]
        h_off = classify(parse_log(off_p), 3, FULL_MIN_LAYERS)["hidden"]
        seam = classify(pool([str(on_p), str(off_p)], 3), 3, FULL_MIN_LAYERS)
        cases += [("pooling keeps the step indices contiguous (no invented hidden graphs)",
                   seam["hidden"] == h_on + h_off)]

        # (7) a log without the GPU tail still yields the three host channels
        notail = "\n".join(ln.rsplit(" gpu=", 1)[0] if " gpu=" in ln else ln
                           for ln in off_txt.splitlines())
        p_nt = parse_log(_mk(td, "nt.log", notail))
        cases += [("a log without the GPU tail still parses the host channels",
                   len(classify(p_nt, 3, FULL_MIN_LAYERS)["full"]) > 0
                   and all(r["_gpu"] is False for s in p_nt["steps"] for r in s["rows"]))]
    bad = 0
    for name, ok in cases:
        print(f"  [{'ok' if ok else 'FAIL'}] {name}")
        bad += 0 if ok else 1
    print(f"f_split selftest: {len(cases) - bad}/{len(cases)} passed")
    return 0 if bad == 0 else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--on", action="append",
                    help="spec arm log (MTP on, CGC_DECODE_PROFILE_ALL=1); repeat to pool launches")
    ap.add_argument("--off", action="append", help="plain arm log, same cell and instruments")
    ap.add_argument("--spec-n-max", type=int, default=3)
    ap.add_argument("--rounds-on", type=float, default=None,
                    help="rounds in the ON arm's measured region (the log does not carry it); "
                         "defaults to the number of emitted verify graphs")
    ap.add_argument("--tol", type=float, default=0.15)
    ap.add_argument("--full-min-layers", type=int, default=FULL_MIN_LAYERS)
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--json", default="")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    if not args.on or not args.off:
        print("need --on and --off (or --selftest)", file=sys.stderr)
        return 2
    res = split(pool(args.on, args.spec_n_max), pool(args.off, args.spec_n_max), args.spec_n_max,
                args.tol, args.rounds_on, args.full_min_layers)
    res["create"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    res["on_log"], res["off_log"] = args.on, args.off
    rc = report(res, args.top)
    if args.json:
        Path(args.json).write_text(json.dumps(res, indent=1, ensure_ascii=False) + "\n")
        print(f"wrote {args.json}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
