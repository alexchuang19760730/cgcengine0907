# The carrier is pinned now. E still does not repeat — and the reason is no longer the sampler.

**Date** 2026-09-26 · carrier `Nail-Qwen3.6-35B-A3B-MTP-UD-IQ3_XXS-denseIQ4X`, MTP on k=1/2/3, pool 8 GiB,
cell `-p 512 -n 128 -d 0 -r 1`, `--warm-skip 64`, cooldown-gated ABBA (`--cooldown 420`), seed 20260926.
Engine: `libllama-bench-impl.dylib b99669fb0fbbf77dd3f1284e3c4903f7`, `libllama-common.0.dylib 87fab59072416216f8016bdf7a139f5`.

## 0. One line

Two of the three sampling axes on the bench carrier were broken and are now fixed **and self-witnessing**
(the seed, and the sampling-distribution parity). The third finding is negative and more important:
**E still does not repeat across rounds**, and the divergence is visible inside the *first* generation
call from an identical history — i.e. it is the **forward pass**, not the RNG. That is the same defect the
M1/M2/M3 oracle gate has been reporting all week as `M1 6/9`, every failing row a `ctx=MTP` row.

## 1. What was pinned

| axis | before | now | witness (engine's own line) |
|---|---|---|---|
| sampler seed | `LLAMA_DEFAULT_SEED` → a fresh `std::random_device` draw **per process** (`llama-sampler.cpp:340-350`) | `CGC_SERVER_SEED` → `sampling.seed`, plus `std::srand` before the prompt/warm/timed calls | `[CGC seed] sampler_seed=20260926 pinned=1` |
| sampling distribution | the struct defaults (`temp 0.80 / top_p 0.95 / top_k 40`) | the profile's own argv (`--temp 0.4 --top-p 0.8 --top-k 0`), renamed into the env names llama-bench reads | `[CGC seed] … temp=0.40 top_p=0.80 top_k=0` |
| C-RNG start token | `std::rand()%n_vocab`, never seeded | `std::srand(seed)` once, before the fills | same `[CGC seed]` line (pinned=1) |

Code: `tools/llama-bench/llama-bench.cpp` (the `[CGC 2026-09-26 carrier pin]` block in `bench_spec_setup`),
`scripts/check/llama_bench_matrix.py::sampling_env()`, `scripts/check/spec_cost_curve.py --seed`.
Fail-closed gates added to `spec_cost_curve.py`: `seed` (the engine must say it pinned the requested seed)
and `sampling_parity` (the engine must have built the sampler the profile's argv specifies). Self-tests:
`spec_cost_curve.py --self-test` **67/67**, `m_keff_analysis.py --selftest` **24/24** (each with fixtures that
must fail).

Why fixed seed and not greedy: temp 0 collapses the sampler to argmax, which *changes* the accept rate
(a sharper target puts more mass on the draft token). E has to describe the **delivery** distribution, so
the carrier keeps temp 0.4 / top-p 0.8 and removes only the draw.

## 2. Pass 1 — seed only: pinned, and still a draw

`--ks 0,1,2,3 --rounds 2 --warm-skip 64 --cooldown 420 --seed 20260926`. All six k>0 arms reported
`seed_applied=True` (`pinned=1`, engine seed == requested). E still disagreed with itself:

| k | r1 E (rounds) | r2 E (rounds) |
|---|---|---|
| 1 | 1.600 (40) | 1.882 (34) |
| 2 | 1.730 (37) | 1.641 (39) |
| 3 | 1.684 (38) | 1.391 (46) |

And the same pass exposed the second defect, from the engine's own witness line:
**`temp=0.80 top_p=0.95 top_k=40`** — the struct defaults, while the server runs 0.4 / 0.8 / 0.

## 3. The parity defect, mechanically

`run_server.sh` passes sampling in its **argv** (`ARG --temp 0.4`, `ARG --top-k 0`, `ARG --top-p 0.8`, with
**no** matching `ENV CGC_SERVER_TEMP=…`), and `llama_bench_matrix.forward_argv()` drops those flags because
they are not in its allowlist — llama-bench has no `--temp` flag, it reads the **env** names. So the parity
block in `bench_spec_setup` (`getenv("CGC_SERVER_TEMP")`, …) had never once fired on this profile: its
premise ("reading the same env names the launcher uses makes the two carriers comparable") was false in this
tree, and no artifact could tell, because nothing printed what the sampler was built with. Now something does.

`lbm.sampling_env(argv)` translates the argv back into the env names (last `--temp` wins — the dump carries
`--temp 0` and `--temp 0.4`, and the server's parser takes the last). Measured effect on E, same cell, same
seed, seed-only → seed+parity: k=3 E 1.684 → **2.065**, k=2 1.730 → 1.828, and pass-2's parity gate PASSes on
all six arms with `temp=0.40 top_p=0.80 top_k=0`. (Direction is expected: a sharper target accepts more.)
**These are not quotable** — see §5 — but the sign and the witness are.

## 4. Pass 2 — seed + parity: still a draw, and now it is locatable

| k | r1 E (rounds) | r2 E (rounds) | mean_draft |
|---|---|---|---|
| 1 | **1.641** (39) | **1.362** (47) | 1.0 / 1.0 |
| 2 | 1.641 (39) | 1.829 (35) | 1.795 / 1.771 |
| 3 | 2.065 (31) | 1.488 (43) | 2.387 / 2.395 |

`k=1` is the clean case and it is decisive: `draft_hist = {1: 39}` and `{1: 47}` — the draft chain always
produced exactly one token, there is no variable draft depth to blame, and **zero** `llama_decode` errors in
either arm. Same config, same seed, same distribution ⇒ `E = 1 + accepted/rounds` gives accept **0.641** vs
**0.362**.

Row-by-row on that pair (`round_segments`, warm call then timed call):

```
segments  r1: [48, 39]   r2: [50, 47]
segment 0 (the UNTIMED --warm-skip call): first divergence at round 3
  r1: (n_done=2, n_past=2, draft=1) -> (2, 2, 1)     # 1 token committed
  r2: (n_done=2, n_past=2, draft=1) -> (4, 4, 1)     # 2 tokens committed
```

Two processes agree for three rounds and then **disagree on whether the proposed draft token was accepted,
from an identical history**. The accept step compares the draft token against the target's *sampled* token,
and both the RNG and the distribution are now pinned and witnessed, so the difference has to be in the draft
token or the target logits: **the forward pass is not deterministic across processes.** That is exactly the
oracle gate's `M1 6/9` with every failure in a `ctx=MTP` row — previously filed as an oracle nuisance, now
identified as the reason E (and therefore m, and therefore every S built on them) is a draw.

## 5. Labels, and what may not be quoted

- **Thermal**: all 8 arms launched `NOMINAL` (the cooldown gate held; it waited 0 s each time — the box was
  already Nominal).
- **Swap at launch, per arm (MiB)**: 4343 (k0 r1) → 4500 → 5030 → 5261 → 5394 → 5393 → 5490 → 5450 (k0 r2).
  Every arm is **above the 2048 MiB start line** of the measurement contract, and the sweep now says so in
  its own artifact (`mem.launch/end/worst`, and a per-arm warning line).
- Consequently: **no `t/s` and no `ms/step` from this pass may be quoted.** E is a counter ratio
  (`n_gen / rounds`) and is unaffected by swap, which is exactly why the reproducibility question could be
  asked at all in this regime — that is the one thing this pass is good for, and it is the thing it answers.
- `m_keff_analysis.py` refuses the pass (`quotable: False`) for two independent reasons: E is non-monotone in
  k, and E does not repeat at a pinned carrier. Its numbers (F=0.506, m=0.2085, r2=0.68 on 6 points) are
  printed for continuity only.

## 6. What changed in the plan

Before this pass, "E is a draw" was a property of the *instrument*, so the fix looked like carrier plumbing
and the m/k ranking looked one more measurement away. Now: the carrier is pinned and certified, and E is
still a draw because the **engine's MTP path does not produce the same tokens twice**. So:

1. **The M1/M2/M3 oracle gate is on the critical path** for the m/E table, not a formality before a commit.
   Until the `ctx=MTP` rows are bit-identical, every m is an average over draws and its sign is not stable —
   which is precisely what the two 2026-09-26 passes showed (`m≈0, F=0.77` vs `m=0.46, F=0.14`).
2. The gate that now exists makes that visible instead of silent: a future pass whose rounds disagree is
   refused by name, and a carrier that never received the seed or the distribution is refused by name.
3. The delivery carrier is still a draw: `run_server.sh` has no `--seed`, so the **server's** token stream
   is still `LLAMA_DEFAULT_SEED`. Bench-side pinning makes the measurement reproducible; it does not make
   the product reproducible, and the two must not be confused in a report.

## 7. Not done / boundaries

- The oracle gate was **not** run on this binary in this round (it is the next ticket, not skipped work).
- The residual `spec draft: llama_decode[0] returned -1` remains: 13 times in pass-1 k3 r1 vs 20 in k3 r2 —
  the same non-determinism seen from the draft-loop side. Left untouched; fixing it is the engine work §6
  asks for.
- `--warm-skip`'s untimed call is no longer a gate. It is a different slice of one stream over a different
  context, so it can disagree while the carrier is perfect; `E_warm_delta` is still reported, and a
  deliberately wild fixture proves it does **not** void an arm whose E repeats across rounds.
- Files (nothing committed): `tools/llama-bench/llama-bench.cpp` + rebuilt `libllama-bench-impl.dylib`,
  `scripts/check/llama_bench_matrix.py`, `scripts/check/spec_cost_curve.py`,
  `scripts/check/m_keff_analysis.py`, `Backup/rerun/seed_abba.sh`, this file.
  Artifacts: `/tmp/seed_abba/pass1_noparity/` (seed only) and `/tmp/seed_abba/` (seed + parity).
