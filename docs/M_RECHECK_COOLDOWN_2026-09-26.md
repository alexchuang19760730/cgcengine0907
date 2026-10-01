# m, re-measured on the fixed engine with `--warm-skip` and repeated interleaved rounds — and the gate it fails

**Date** 2026-09-26 · engine `libllama.0.dylib eef62e0ec52cef1b` / `libllama-common.0.dylib 4227947178aa3445`
(the build that carries the k_eff fix) · carrier Nail IQ3_XXS-denseIQ4X, MTP `--spec-type draft-mtp`
vs plain, pool 8 GiB, `-ngl 99 --load-mode none -b/-ub 5632`
· cell `-p 512 -n 128 -d 0 -r 1 --warm-skip 64` (the cost curve's own cell, i.e. the cell the
0.77-plain-step F anchor was measured in) · artifact `/tmp/m_re3/sweep.json`,
`/tmp/m_re3/analysis.json` · box at start: swap 3.6 GiB used of 5.1 GiB, no rival llama process

## 0. One line

**The pass is clean on every gate the user asked for — `--warm-skip 64` applied 8/8, every arm
launched *and stayed* NOMINAL 8/8, `max_draft == k` 8/8, launch order k0,k1,k2,k3,k3,k2,k1,k0 — and
it still fails, for a reason neither thermal nor memory: E is not reproducible. The same k=2 config
drew E = 0.984 and E = 1.442 (46% apart) with the pool hit rate flat at 42.5% vs 43.4% between them.
So `m` came out 0.46–0.54 with F = 0.14, the previous pass on the same design came out m ≈ 0 with
F = 0.77, and neither may be quoted.**

## 1. Design, and the four gates it passes

```
python3 scripts/check/spec_cost_curve.py --profile prefill250 --ks 0,1,2,3 --rounds 2 \
    --prompt 512 --gen 128 --depth 0 --warm-skip 64 --cooldown 420 \
    --workdir /tmp/m_re3 --json /tmp/m_re3/sweep.json
```

`--cooldown` is new here: it calls the one cooldown loop (`thermal_pressure.wait_nominal`) before
every arm, because the previous 12-arm pass started hot from arm 4 on and left the batch sign of `m`
unrecoverable. The round order is the tool's own ABBA rotation (`ks` on odd rounds, reversed on
even), so every k sits in both an early and a late slot — the fix for the launch-order drift that
the same day's k=0/k=3 pair exposed (its two launches' common prefill graph was 10× apart).

| gate | result |
|---|---|
| `--warm-skip 64` actually took (`n_gen = 128 − 64`) | **8/8 True** |
| thermal at launch **and** worst | **8/8 NOMINAL** (cooldown waited 0 s at every arm — the box was already Nominal) |
| `max_draft == k` (the k_eff fix) | **8/8 True** |
| launch order | k0 k1 k2 k3 k3 k2 k1 k0 |

`--warm-skip` also gives a second generation of the same config inside the same process. That turns
out to be the most informative thing in the pass (§3).

## 2. The numbers, and the two fits that disagree

Paired within round against that round's own k=0:

| k | k_eff | E | S | cost `E/S` | ms/step | MiB/step |
|---:|---:|---:|---:|---:|---:|---:|
| 1 | 1.00 | 1.443 | 0.860 | 1.677 | 129.4 | 72.8 |
| 2 | 1.63 | 1.244 | 0.700 | 1.780 | 134.7 | 76.3 |
| 3 | 2.18 | 1.506 | 0.713 | 2.168 | 164.5 | 104.3 |

```
fit through origin (what the sweep prints):   m = 0.5387   r2 = 0.3596
fit with the per-round term  cost = 1 + F + m*k_eff:   F = 0.1434   m = 0.4568   r2 = 0.3727  (n=6)
```

Compare the earlier pass on the same design: `F = 0.770, m = −0.030, r2 = 0.007`
(`M_PER_TOKEN_AFTER_KEFF_2026-09-26`). **The two passes disagree on which parameter carries the
cost** — near-zero marginal with a large round surcharge, versus a real marginal with a small
round surcharge — and neither r² is respectable (0.36–0.37). A design whose answer flips between
passes is not measuring a property of the engine.

## 3. The reason: E is a draw, not a config constant

`--warm-skip N` runs an untimed generation of the same config in the same process, so each arm
contains **two independent draws of E under one env, one engine, one thermal state**. They disagree:

| arm | untimed call E | timed call E | ΔE |
|---|---:|---:|---:|
| k1 r1 | 1.240 | 1.476 | +0.236 |
| k1 r2 | 1.292 | 1.319 | +0.027 |
| k2 r1 | 1.192 | **0.984** | −0.208 |
| k2 r2 | 1.743 | **1.442** | −0.301 |
| k3 r1 | 1.089 | 1.442 | +0.353 |
| k3 r2 | 1.216 | 1.500 | +0.284 |

and rep against rep, same config: **k=2 timed E 0.984 vs 1.442 (46% apart)**, k=1 12%, k=3 4%.
The pool is not the covariate: hit rate is 42.5% (k2 r1) vs 43.4% (k2 r2), 41.1–43.9% across all
spec arms, 92.4–92.9% for both k=0 arms. Thermal was NOMINAL at launch everywhere.

So `a = E − 1`, and therefore `S = E/(1+F+m·k_eff)`, carries a per-launch spread that is **larger
than the k-dependence it is supposed to show**. Every "m" in §2 is a fit of a quantity whose
numerator is one draw per arm. That is also the honest reading of the *previous* pass's non-monotone
E — the same thing, before the witness existed.

**Made permanent:** `spec_cost_curve.py` now records `E_warm` and `marks e_stable` (15% tolerance,
documented as 2× the tightest bound a repeat that agreed to 2% would pass), prints it in the run
line, and `m_keff_analysis.py` refuses the pass when any arm is unstable. Both self-tests carry a
must-fail fixture for it (48/48 and 19/19).

## 4. Best k and the accept sensitivity — at this overhead, and not quotable

```
   accept a    k*    S(k*)   S(k=1)   dS/da@k*   dS/dF@k*
      0.264    1    0.790    0.790      0.6249    -0.4936
      0.465    1    0.915    0.915      0.6249    -0.5721
      0.600    1    1.000    1.000      0.6249    -0.6249
      0.800    2    1.186    1.125      1.2640    -0.5767
```

* **k\* = 1** for every accept rate up to ~0.6, and only reaches 2 at a ≈ 0.8. At today's acceptance
  (a ≈ 0.26–0.47) no k pays for itself: S = 0.79–0.92, i.e. MTP is still net-negative on this carrier.
* **The sensitivity is now on m, not F.** `dS/dF = −0.49 … −0.63` (a small lever, because
  F = 0.14 is already small), while a unit of `m` is worth more than a unit of `F` at these values;
  and because F is small, `dS/da` at k\* is only 0.62 — accept alone tops out near 1.2 even at a = 0.8.

## 5. What this changes: the target moves from F to the width

`m ≈ 0.46–0.54` is not a nuisance parameter — it **agrees with the other line's width curve**
(`F_ROUND_ATTRIBUTED_2026-09-26` §3): that pass measured **+40.8 ms per extra verify token**, and a
**cliff at the 4th token (+73.3 ms, of which +35.7 ms is `cb`, 14.9 → 50.6 ms)**. Both say the extra
draft token is *not* free and the cost is in the expert-cache hook at the widest round, not in a
fixed per-round surcharge.

⇒ Stop attacking F (it is 0.14 here, and it was the wrong parameter when it was 0.77). The width
cost is the same `cb` channel F1/M3 already located; the honest target is *the width cliff*.

## 6. The next step, in order

1. **Fix E's reproducibility at the carrier** — that is now the blocker for every m/S/k claim. Either
   pin the sampling (fixed seed / greedy) so E describes the config, or raise reps to ≥5 so the
   median is over a sample; then rerun this exact cooldown-gated ABBA. The witness in §3 stays as the
   acceptance test: if `e_stable` is False again, the carrier is still not measuring a property.
2. **Then** re-derive k\*: with a stable E the F/m split has a chance of surviving a second pass.

## 7. Files and provenance

* `scripts/check/spec_cost_curve.py` — new `--cooldown`; `E_warm`/`e_stable` witness; the preflight's
  rival filter no longer counts another session's idle `pgrep` watchdog as a rival (measured: it
  aborted this run's first launch; `is_rival` now excludes detector shapes, with a must-fail fixture);
  self-test 48/48.
* `scripts/check/m_keff_analysis.py` — the E-reproducibility refusal; self-test 19/19.
* `Backup/rerun/m_rep_cooldown.sh` — the pass (gitignored; needs `-f` to commit).
* Nothing here is a throughput claim; no t/s is published from these arms.
