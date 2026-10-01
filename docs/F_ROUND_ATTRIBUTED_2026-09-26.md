# The spec round, attributed per layer — and why the cross-arm F is refused

**Date** 2026-09-26 · carrier Nail IQ3_XXS-denseIQ4X, MTP `--spec-draft-n-max 3` vs `0`, pool 8 GiB,
`-ngl 99 --load-mode none -b/-ub 5632`, cell `-p 512 -n 128 -d 0 -r 1 --warm-skip 64`
· engine `libllama.0.dylib eef62e0ec52cef1b` / `libllama-common.0.dylib 4227947178aa3445`
(built 03:18:55, the k_eff fix is in it) · tool `scripts/check/f_split.py` (self-test 19/19)
· artifacts `/tmp/f_pair/{spec_cost_k3_r1,spec_cost_k0_r1}_p512_n128_d0.stderr.log`, `f_split.json`

## 0. One line

**The round's own cost is attributed (device 66.3%, hook 25.0%, dispatch 3.0%, and the "host sync"
bucket is NOT separable at −27.5%); the cross-arm surcharge F is refused, and the refusal has a
mechanical reason — the two launches' common-shape prefill graph is 10× apart, and F came out
−2.58 ms. The strongest positive number is not F but the round's own width curve: its cost is a
cliff at the 4th token (+73.3 ms, of which +35.7 ms is the expert-cache hook), not a line.**

## 1. What the instrument actually emits (this is where the first version of the reader was wrong)

`ggml-backend.cpp` prints one line per graph window, gated by

```cpp
if (dp_tot > 0 && ((dp_step % 8) == 0 || dp_step == 1 || dp_ntok > 1))
```

`dp_ntok` is the MoE top-k tensor's token count (`dp_ntok = ttopk->ne[1]`), i.e. **each line states
its own width**. The gate means the emitted set is two populations, not one:

* every graph with `ntok > 1` (the verify graphs) — always emitted;
* **one in eight** graphs with `ntok == 1` (the 2-segment sampler graphs) — 7 of 8 are hidden.

Step indices are consecutive graph indices, so the hidden graphs can be *counted* exactly:

| arm | graph windows | full (41-layer) decode | 2-segment graphs | hidden (`ntok==1`) | prefill |
|---|---:|---:|---:|---:|---:|
| ON (k=3) | 94 | 78 | 14 | **114** | 2 |
| OFF (k=0) | 18 | 16 | 0 | 105 | 2 |

That is the instrument's largest blind spot and the reader now names it instead of averaging it
away: `hidden_ntok1 = 114` graphs, ≈1.46 per round, ≈4.5% of the round's time. **Note what they
are:** sampled ones are 0.71–0.88 ms, `segs=2 layers=1`. So in this build the draft chain is *not*
a set of full-model T=1 forwards — each round has **exactly one full graph** (78 full graphs for
77 rounds) whose width is `drafted + 1`. Drafting is fused into that graph; it is not paid as
separate passes.

## 2. Claim 1 — the round, attributed (published: within one launch, no launch drift can enter)

77 verify graphs = 77 rounds; 128 generated tokens ⇒ **1.66 tokens/round** (matches the arm's own
`E = 1.641`). Per round, `row = device + gap + barrier + hook + dispatch` holds by construction;
the layer rows cover 99.99% of the window (nonlayer = 0.02 ms).

| bucket | ms/round | share | what it is |
|---|---:|---:|---|
| device (`union`) | 104.68 | 66.3% | the layer's device span |
| hook (`cb`) | 39.42 | 25.0% | expert-cache top-k hook: fills, slot ops, gather bookkeeping |
| dispatch (`submit`) | 4.77 | 3.0% | submitting the layer's segments |
| barrier (`wait − union − gap`) | **−43.44** | **−27.5%** | **NOT SEPARABLE** |
| round total | 157.79 | | |

**The barrier bucket is not a bucket.** `wait − union − gap` came out negative in aggregate: the
layer-summed device span *exceeds* the host window that nominally contains it by 43 ms/round. Two
mechanical reasons, both stated rather than smoothed: per-layer device spans overlap each other in
wall time (so their sum is not a span), and the device timeline outlives the host's eval window
(async submission). The tool now flags this instead of printing a negative "host sync"; the
per-layer `wait` column is still exact (it closes against `total`), but *host sync* cannot be
recovered as a residual of this formula on this engine.

## 3. Claim 1b — the round's cost is a cliff at the 4th token, and the cliff is the hook

Same launch, full graphs grouped by their own width (`ntok = drafted + 1`):

| `ntok` | n | total ms | ms/token | wait | **cb** | submit | Σ union |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 2 | 12 | 97.14 | 48.57 | 75.83 | 17.35 | 3.96 | 69.66 |
| 3 | 17 | 105.37 | **35.12** | 86.61 | 14.89 | 3.87 | 79.46 |
| 4 | 48 | 178.69 | 44.67 | 123.51 | **50.61** | 4.58 | 114.72 |

Marginal, 2 → 4: **+40.78 ms per extra token**, split as `d(wait) 23.84`, `d(cb) 16.63`,
`d(union) 22.53`, `d(submit) 0.31` (per token). But the line is not the mechanism:

* **2 → 3: +8.23 ms** (+4.1 ms/token) — the third token is nearly free.
* **3 → 4: +73.32 ms** (+73.3 ms/token), of which **+35.72 ms is `cb` alone** and +36.90 ms is the
  host window; the device span grows only +35.26 ms.

So the "~46–59 ms per draft token" that the plan has been quoting is, on this carrier, **the fourth
token's cliff**, and the largest single component of it is the expert-cache hook (a 3.4× jump,
14.9 → 50.6 ms), i.e. **expert fetches for the extra expert set the 4th token touches** — the same
`cb` channel that F1/M3 attributed before, now visible as a *threshold* rather than a slope.
It is also why the per-token cost has an interior minimum at `ntok = 3` (35.12 ms/token).

## 4. Claim 2 — the cross-arm F is refused, with a witness in the log

`F = per-round ON − per-step OFF = 157.79 − 160.38 = **−2.58 ms** (−0.016 plain steps)`. A spec
round cannot be cheaper than the plain step it replaces, so this pair is measuring the box:

**regime witness (the arms' common shape, the prefill graph):** ON 46 845 ms vs OFF 4 682 ms =
**10.0×**, same shape, same cell, one launch apart. The first ON graph alone is 88 962.9 ms for
512 tokens. Thermal said it too: the k=0 arm launched `NOMINAL` (68 NOMINAL / 13 MODERATE samples),
the k=3 arm launched `MODERATE` (114 MODERATE / 43 HEAVY / 88 NOMINAL).

⇒ The tool exits 2 and withholds the cross-arm buckets. The per-layer tables for both arms are in
`f_split.json` (the split is computed, then refused for publication), which is the point: the
numbers exist, the claim does not.

## 5. Three defects this found in my own reader (all fixed, all with a must-fail fixture)

1. **Prefill contamination.** The first version's mean step included the ntok=512 prefill graphs;
   the ON arm's 88 962.9 ms graph added **+946 ms** to a 94-sample mean and made `F = 457.5 ms`
   (the number it printed). Fix: classify by `ntok`; a decode graph is `ntok <= k + 1`.
2. **The divisor was inferred from row counts.** Layer means divided by `rows / layers_seen`
   (= 3214 / 41 = 78.4) while the step mean used all 94 windows — two populations, one reconciliation.
   Fix: one population drives both sides of the identity.
3. **A false closure red at 1.408%.** It was a 0.71 ms, 2-segment graph and the "error" was 0.01 ms —
   exactly one print quantum. Fix: the closure tolerance is `max(rel, 5 × 0.01 ms)`.

## 6. What this does NOT say, and the open item it leaves

* **No absolute level is quoted from these logs.** The DC windows sum to ~11.6–12.2 s while the
  bench's own `tg` wall time is 10.83 s (128 / 11.82 t/s): the instrument accounts for **8–13% more
  time than the generation took**. That gap is inside the graph windows' bookkeeping, not in the
  comparisons (all width classes are measured the same way), but it means `ms/round` here is a
  *relative* reading until the gap is explained. Open, named, not guessed.
* **The width classes are selected, not assigned.** Rounds land in `ntok = 4` because 3 drafts were
  accepted, i.e. on the easy tokens; the per-token numbers of §3 therefore describe "rounds that
  got long", not "the cost of making a round long". Assigning width (`--spec-draft-n-max` sweep
  inside *one* launch) is the experiment that would separate the two; the k sweep cannot, because
  each k is a different launch.
* `m ≈ 0` from `M_PER_TOKEN_AFTER_KEFF` is not refuted here: that fit is over step *cost vs k* in
  policy units across arms. §3 says the same thing locally — cost grows with width — which is the
  batch-no-amortisation finding, now located in `cb` at the 4th token rather than in the first.

## 7. The next two cheap steps

1. **ABBA in one window** (`--ks 0,3` then `--ks 3,0`, same engine, per-layer instruments armed):
   the only design that gives F a number. The tool's two new gates (regime witness, `F > 0`) are
   already fail-closed, so the pass has a verdict either way.
2. **Label the graph kind in the instrument** (`kind=draft|verify|sampler` on the `CGC-DECPROF`
   line). With the kind printed, the hidden 1-in-8 population stops mattering and the round
   composition needs no reconstruction. That is a 5-line change in `ggml-backend.cpp` and it removes
   this tool's only remaining modelled quantity.

## 8. Provenance

* tool: `scripts/check/f_split.py` — `--selftest` 19/19, including four fixtures that **must** fail
  (10× prefill regime, negative F, width never reaching k+1, hidden graphs never sampled).
* round count is not in the log (the bench's `rounds=` is in the sweep summary); it defaults to the
  number of verify graphs and is overridable with `--rounds-on`.
* box at measurement time: swap used 5.72 GiB of 7.17 GiB, free ~6.5 GB, no rival llama process;
  thermal per §4. Nothing in this document is a throughput claim.
