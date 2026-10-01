# The per-token verify cost after the k_eff fix — m is not what was being measured

**Date** 2026-09-26 · carrier Nail IQ3_XXS-denseIQ4X, MTP on, pool 8 GiB, cell `-p 512 -n 128 -d 0`
(llama-bench, `--warm-skip 64`, 4 ks × 3 interleaved rounds) · engine
`libllama=0ed9c88fe0ae4963  libllama-common=87fab59072416216` (the k_eff fix, worktree, uncommitted).

## 0. One line

**The draft-depth cap is gone** (`max(draft) == k` in all 12 runs, counter-level), and with it the
old `m = 0.868` is retracted — not because it was measured badly but because it described a 1-wide
verify batch labelled 3-wide. The replacement is a different shape entirely: a verify round costs
**1 + F + m·k** with **F = 0.77 plain steps and m ≈ 0 (−0.03, r² = 0.007)**, i.e. *the width of the
batch is nearly free and entering the spec path is not*. Consequences, both arithmetic: the
break-even accept rate is **a = F/(1+F) = 0.435** (not `m`), and **k is not a lever** — for the
measured chain (a = 0.264) `S_max = 1/((1−a)(1+F)) = 0.77` at *any* k.

## 1. What ran, and what is new about it

```
python3 scripts/check/spec_cost_curve.py --profile prefill250 --ks 0,1,2,3 --rounds 3 \
        --warm-skip 64 --json Backup/phase_decomp/spec_cost_curve_keff_r2.json
```

| witness | value | why it matters |
|---|---|---|
| `max(draft)` per k | 1 / 2 / 3 | the cap that made every 09-25 number a k_eff=1 reading is gone |
| `draft_hist` k=2 | `{1: 31, 2: 33}` | the chain really reaches depth 2 (pre-fix: `{1: all}`) |
| `draft_hist` k=3 | `{0: 11, 1: 11, 2: 6, 3: 25}` | depth 3 happens; 11 rounds produce nothing (see §6) |
| `k_eff_ok` | 12/12 True | the tool's own gate; the old runs failed it |
| `warm_skip_applied` | 12/12 True | timed window is 64 tokens, not 128 |

**Box**: launched 02:52:25 at `reclaimable 7880 MB`, swap `3796 MiB used / 5120 total`, launcher probe
82% free, zero rival llama processes. Thermal: round-1 arms 0/1/2 launched NOMINAL, every arm from
02:53:36 NOMINAL→MODERATE→HEAVY inside one run, and all of rounds 2–3 launched HEAVY.

## 2. The reading (and its label)

Medians over the 3 rounds; `cost` in units of a plain decode step. The three k=0 arms measured
90.0 / 106.0 / 111.5 ms per step (median 106.04), i.e. the box was slow *and* drifting by ±12% --
which is why every ratio below is paired inside its own round.

| k | k_eff | E | S | cost (E/S) | m=(cost−1)/k_eff | ms/step | MiB/step | thermal |
|---|---|---|---|---|---|---|---|---|
| 1 | 1.00 | 1.730 | 0.933 | 1.854 | **0.854** | 194.5 | 91.0 | NOMINAL/HEAVY |
| 2 | 1.66 | 1.362 | 0.759 | 1.793 | **0.478** | 169.3 | 74.6 | NOMINAL/HEAVY |
| 3 | 1.85 | 1.333 | 0.911 | 1.463 | **0.250** | 177.0 | 62.5 | HEAVY |

**Not quotable, and the reason is not the reader's opinion.** `m_keff_analysis.py` refuses it on two
independent witnesses: 7 of 9 paired rows launched outside NOMINAL, and **E is not monotone in k**
(`E(1)=1.730 > E(2)=1.362 > E(3)=1.333`). The second one is structural, not noise: the committed
token stream is the greedy stream whatever k is, so more draft tokens can only *add* accepted ones —
an E that falls with k is a contamination witness. Two known contributors, both mechanical: the
thermal ramp, and the wasted rounds in §6 (a round with `draft=0` commits 1 token and pays a full
verify step).

Note `S` itself is paired within each round against that round's own k=0, which is what cancels the
drift; the E column is *not* paired (there is no paired way to know how many tokens a plain arm
would have committed), and that is where the non-monotonicity shows up.

## 3. The model in circulation is refuted by these three points

`spec_cost_curve.py` fits `cost = 1 + m·k` **through the origin**. Today's points return
`m = 0.434, r² = −1.78` (negative = worse than predicting the mean). Allowing the per-round term:

```
cost(k) = 1 + F + m·k_eff      F = 0.7698   m = −0.0299   r² = 0.0069   (n = 9, 2 params)
```

Two parameters on nine rows, so this is descriptive rather than decisional — but the direction is not
in doubt: **cost is flat in k_eff (1.83 / 1.60 / 1.79 by median-of-ratios; 1.85 / 1.79 / 1.46 by
ratio-of-medians), while k_eff goes 1.00 → 1.85.** `m` therefore *falls* with k in exactly the way a
constant-m model forbids, and the part of the loss that is real is the **per-round surcharge:
0.77 × 106.0 ms ≈ 82 ms**.

This does not contradict the kernel measurement that the MoE gather family scales with T
(`extra_over_first = 0.977`, `MOE_GATHER_BOUND`): that family is 8.1 → 24 ms/step for T=1→3, i.e.
~8–13% of a 180–200 ms step. The gather's T-scaling is real and it is too small to be what the step
is made of. Conflating the family with the step is how "each verify token costs 98% of the first"
became a plan premise.

## 4. k*, and the accept rate as a necessary condition

With `E(k) = Σ_{j≤k} a^j` (constant per-draft accept) and the fitted overhead:

```
S(k) = E(k) / (1 + F + m·k)        S_max (k→∞, m=0) = 1 / ((1−a)(1+F))
break-even: S ≥ 1 ⟺ a ≥ F/(1+F) = 0.435
```

| target | a needed at today's F=0.77 | comment |
|---|---|---|
| S ≥ 1.0 | a ≥ **0.435** | today's chain sits at 0.264 ⇒ no k wins |
| S ≥ 1.5 | a ≥ 0.623 | |
| S ≥ 2.0 | a ≥ 0.717 | |
| S ≥ 2.5 (≈ 25 t/s at 10 t/s) | a ≥ **0.774** | with F untouched |

And the same inequality solved the other way — because accept is the harder variable:

| a reachable | F required for S ≥ 2.5 | |
|---|---|---|
| 0.50 | F ≤ −0.20 | **impossible at any F** |
| 0.60 | F ≤ 0.00 | only as k→∞ and only if the surcharge vanishes |
| 0.70 | F ≤ 0.333 | cut F by 57% |
| 0.75 | F ≤ 0.600 | cut F by 22% |
| 0.80 | F ≤ 1.00 | already satisfied |

So the honest answer to "what is the best k": **the model has no interior optimum while m ≈ 0** — S
increases monotonically with k, and the thing that stops it is that the *chain* decays (E saturates
near 1.7–1.8 for the measured accepts) and that 21% of rounds at k=3 produce zero drafts. The
measured best *point* is k=3 (S = 0.911) but with E non-monotone that ordering is not a finding.

## 5. Sensitivity, per unit and per achievable move

`dS/da` and `dS/dF` at the fitted overhead:

| k | a | dS/da | dS/dF | S |
|---|---|---|---|---|
| 3 | 0.264 | 0.98 | −0.43 | 0.764 |
| 3 | 0.465 | 1.46 | −0.57 | 1.007 |
| 3 | 0.600 | 1.85 | −0.70 | 1.230 |
| 8 | 0.465 | 1.95 | −0.60 | 1.055 |
| 8 | 0.600 | 3.28 | −0.79 | 1.398 |

Per unit, accept is the *more* sensitive knob (×2–4). Per *achievable* move they are comparable: the
accept rate has realistic room of ~+0.3 (0.264 → 0.6), worth +0.47 S at k=3; F has room of −0.77
(to zero), worth +0.60 S at k=3 but requiring the surcharge to disappear entirely. **Neither alone
reaches 2.5×; the table in §4 is the necessary condition, and it says the pair (a≈0.75, F≈0.6) is the
cheapest frontier that does.**

## 6. The 82 ms has a fixable component, and it is already identified

At k=3, **11 of 53 rounds drafted zero tokens** (`draft_hist {0: 11}`) — the same residue documented
in `docs/KEFF_CAP_2026-09-26.md` §5: the first `llama_decode[0]` of the chain returns −1, the loop
breaks, and the round pays a full verify step for one token. Treating those rounds as the plain steps
they effectively are: cost 1.463 → 1.367, S 0.911 → 0.976 (**+7%** at k=3, and it removes a bug rather
than trading anything).

The rest of F is *not attributed* in this report and should not be guessed at: the three candidates
with existing instruments are the pool hook on the verify batch (`verify_union_per_call` = 15.5
experts/call vs 8.0 for plain, at 42–46% hit rate), the per-layer eval barrier already measured at
0.46 ms/layer, and the wider batch's own dispatch. A per-layer split on the *verify* round is the
cheap next reading, and it is the same instrument (`CGC_HOOK_SPLIT` / `CGC-DECPROF all`) that already
produced the decode-side table.

## 7. What it would take to make this quotable

The sweep as built cannot be citable on this box: 12 launches back-to-back on a fanless MBA are HEAVY
from the 4th one on, and E's non-monotonicity follows from the same heat plus the wasted rounds. The
protocol that would fix it, at the cost of wall-clock: **one k per window, NOMINAL required at launch
and at worst, cooling between arms** (the existing `mtp_accept_ab.py` thermal sampler already refuses
otherwise). With 4 ks × 3 rounds that is 12 windows. Nothing in §3–§5 changes sign if the numbers
move by the ±15% the drift suggests; the model choice (F vs m) is a factor-of-3 effect.

## 8. Files, digests, and what is *not* claimed

* `Backup/phase_decomp/spec_cost_curve_keff_r2.json` (sweep, 12 runs, per-run thermal + histograms)
* `Backup/phase_decomp/m_keff_r2_analysis.json` (this reader's output)
* `scripts/check/m_keff_analysis.py` (new; self-test 17/17, including the must-fail fixtures: HEAVY
  launch, capped k, unapplied warm-skip, E falling with k)
* `Backup/rerun/keff_m_sweep.sh`, `Backup/rerun/run_detached.py`
* engine digest **`libllama 0ed9c88fe0ae4963` / `libllama-common 87fab59072416216`** — the pair that
  the gate's `engine_digest()` could not tell apart until today (it hashed a stale
  `libllama.0.0.279.dylib` and omitted `libllama-common`, where `common/speculative.cpp` lives). That
  fix is in `scripts/check/m123_oracle_gate.py` in the same worktree.
* **A parallel session rebuilt `build/bin` at 03:02:50–03:02:59**, i.e. two and a half minutes after
  this sweep ended (03:00:17) and therefore after every launch in it. The current on-disk
  `libllama` is now `ecead1c3a6cf3dd3` (different bytes), while `libllama-common` came back
  **byte-identical** (`87fab59072416216`), and the new `libllama` still exports
  `llama_model_is_assistant_block` — so the k_eff fix is present in the shared build both before and
  after that rebuild. The digest above names the bytes that produced the table; a reader comparing it
  against `md5 build/bin/libllama.0.0.578.dylib` today will get a different value and should know why.

Not claimed: any t/s figure (nothing here is quotable, §2); that F = 0.77 is stable across pool
budgets or profiles (one budget, one profile); that the accept chain is constant-a (it visibly is
not — `E(1)−1 = 0.73` but `E(2)−E(1) < 0` in this window); that the M1/M2/M3 oracle gate passes on
this build (it was about to be run when this measurement took the window — `M1 6/9` on the same patch
earlier today, with the failures shown to be inherited from the pre-patch reference).
