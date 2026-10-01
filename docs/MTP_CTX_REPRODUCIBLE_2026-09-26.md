# The `ctx=MTP` rows are bit-identical across launches — the residual M1 6/9 is a *reference* question

**Date** 2026-09-26 · carrier Nail-Qwen3.6-35B-A3B-MTP IQ3_XXS-denseIQ4X (`qwen35moe`,
`nextn_predict_layers = 1`) · probe = the registry's **long probe** (巴黎 prompt, 400 tok, `prod25`,
MTP on) · engine digest `libllama.0.dylib=c80b2329252e1176` / `libllama-common=87fab59072416216`
/ `libggml-metal=920e4ad5e19c5fd3` / `libggml-base=f6f034b2d2dfcaff` /
`libllama-server-impl=60fb7910a8bb7d38` / `llama-server=054fb22f04a01c5c` · tree `828f4d1c2`

## 0. One line

The MTP path **is** reproducible: two independent launches of the long probe agree on **all 1045
rows, including all 447 `ctx=MTP` rows** (M1 = M2 = M3 = 1045/1045), and `draft acceptance` /
`mean len` (E) is **identical across five launches**. The gate's `M1 6/9` is therefore *not* a
reproducibility failure: it compares the fresh dump against a **pinned reference that predates the
KV-sharing switch** (`65c76b8c7`, 2026-09-25 17:16). The first row that moved is **the first
`ctx=MTP` row**; every trunk (`DEF`) row before it is still bit-identical.

## 1. The two questions that were being conflated

| question | tool | what it can say |
|---|---|---|
| "did the numerics move since the reference?" | `m123_oracle_gate.py` (fresh vs pinned ref) | a *drift* verdict — says nothing about reproducibility |
| "are the numerics reproducible **now**?" | `mtp_ctx_repro_certify.py` (two fresh launches, per `ctx_type`) | the precondition; without it a drift verdict has no baseline to be a drift *from* |

`E` (accept / `mean len`) is read off the server log. It is a config constant **iff** the draft
forward it depends on is launch-invariant — that is the proposition this document tests.

```
python3 scripts/check/mtp_ctx_repro_certify.py --tag 0926long     # runs both arms, compares, refuses on slack
```

The verdict is fail-closed and **per `ctx_type`**: equal row counts, no unpaired key, every common
key bit-identical, `ctx=MTP` present and clean, **and** the two arms' `draft acceptance` line
identical. Self-test 8/8 with five must-fail fixtures (MTP row drifted / DEF row drifted / E differs
while rows agree / an MTP row missing / no MTP rows at all).

## 2. The certificate (long probe, two independent launches)

| bucket | rows | M1 bit-identical | M2 argmax | M3 top-N |
|---|---:|---:|---:|---:|
| `DEF` | 598 | **598/598** | 598/598 | 598/598 |
| `ctx=MTP` | 447 | **447/447** | 447/447 | 447/447 |
| total | 1045 | **1045/1045** | 1045/1045 | 1045/1045 |

E in both arms: `0.23266 (104 accepted / 447 generated), mean len = 1.70` — and the same string
appears in **five** independent launches this session (09:21:10, 09:25:56, 09:27:03, and the
certificate's two arms). So on the server carrier E is a function of the config, not a draw.

The same holds one level down, which matters for *why* the rows agree: the two arms' cache counters
are identical as well — `requests=48317 hit=79.5% evictions=9798` and
`MTP fast path: calls=6389 union=115573 cold(ZERO)=0 … draft: calls=446 union=3568 cold=0`. So the
rows are not "equal because the pool happened not to matter": the pool did the same work, byte for
byte, in both launches.

Artifacts: `Backup/phase_decomp/mtp_repro_0926long.json`, `..._A.jsonl`, `..._B.jsonl`,
`Backup/m123_oracle_gate/{summary,oraclecmp,cmp,launch}_cgcMTP_0926long_{A,B}.*`.

## 3. What the pinned references say, and where the difference lives

**(a) Short probe (9 records), this engine:** `M1 6/9`, `M2 9/9`, `M3 6/9`, `config_diffs []`
(`summary_cgcMTP_short_0926.json`). `config_diffs []` matters: the resolved launch env matches what
the reference expects, so "stale config" is not the explanation here. The three differing rows are
exactly the three `ctx=MTP` rows (`[2,0]`, `[3,0]`, `[4,0]`); `row_hash_equal=6` = the 6 `DEF` rows.

**(b) Long probe (884-record reference `oracle_long_base2_20260919.jsonl`, md5 intact):**
only 12/884 hashes match — but that is *trajectory*, not a second numeric change. Per-row, in order:

| key | ctx | A argmax | ref argmax | A hash | ref hash | bit-identical |
|---|---|---|---:|---|---|---|
| 0,0 | DEF | 198 | 198 | `30f3eb924b66…` | `30f3eb924b66…` | **Y** |
| 1,0 | DEF | 109705 | 109705 | `88a7dcfc95ef…` | `88a7dcfc95ef…` | **Y** |
| **2,0** | **MTP** | **109286** | **95845** | `932e9ce9504b…` | `985f566cdaaa…` | **N ← first divergence** |
| 3,0 | MTP | 109462 | 16 | `c74ae90252ed…` | `5cd3ad74cfd8…` | N |
| 4,0 | MTP | 96655 | 22 | `a5d2621529fb…` | `6e9ec061c25a…` | N |
| 5,0..3 | DEF | 95726,114545,107230,121041 | 95726,95883,17,115090 | — | — | N (different trajectory) |

Rows **0–1 are bit-identical**; the **first `ctx=MTP` row is the first divergence, in hash *and*
argmax**. Everything after it is a different token sequence, so its hash mismatch is a property of
greedy chaos, not of the trunk. Note the row counts differ too (1045 now vs 884 then): the same
generation is reached in fewer rounds now because the per-round structure changed (see §4).

## 4. Attribution: the draft's KV, not the trunk

`65c76b8c7` retains `ctx_other` for `LLAMA_CONTEXT_TYPE_MTP`, so for this model
`is_mem_shared = llama_get_ctx_other(ctx_dft) == ctx_tgt` became **true**. What that changes:

* `common/speculative.cpp`: `if (!is_mem_shared) { …catch-up decode of the prompt on ctx_dft… }` —
  with sharing, the draft **stops recomputing its own KV** and **reads the target's KV** for the
  prefix. The draft's attention therefore sees *different K/V values* (the target's, computed by the
  trunk's weights, instead of the draft layer's own) — a ~1-logit-unit shift, confined to the draft.
* Its companion regression — the shared-memory branch re-using **one position** for every draft
  token, so step 2+ was refused by M-RoPE (`X < Y`) and `k_eff` silently collapsed to 1 — is the
  working-tree fix in `docs/KEFF_CAP_2026-09-26.md` (`one_position_drafts` split out of
  `is_mem_shared`). With it, the shared-KV chain is *sequential* again (3 deep).

Evidence chain for the attribution (three independent witnesses):
1. **blame** — the retention block is `65c76b8c7`, 09-25 17:16;
2. **artifact timeline** — the `[2,0,'MTP']` hash matches the reference in every gate run up to
   09-23 11:32 (`pfx-ckpt2`, 9/9) and differs in every run from 09-25 17:09 onward
   (`181c373a72e4a81c`, stable across 6 runs and 3 builds — i.e. deterministic, not noisy);
3. **row order** — the first divergence is the first `ctx=MTP` row while the trunk rows before it
   are bit-identical (`config_diffs []` says the config did not move).

## 5. What §2 does **not** say

* **It does not say the change improved accept.** On this probe the pre-switch run (whose server log
  sits at `llama_server_20260919_224952.log`, the run that wrote the 884-record reference) reported
  `0.34127 (129/378), mean len 2.02`; the current engine reports `mean len 1.70`. Different
  trajectories, six days of other engine work between them — register this as a *cell to re-measure*,
  not as a result. What is new is that E can now be attributed at all: it is reproducible.

  * **2026-09-27 補記（量綱查證）**：`E = 1 + k·a` 在兩端都成立、且 `k = 3` 都沒變
    （`1 + 3×0.34127 = 2.024` vs 報告 `2.02`；`1 + 3×0.23266 = 1.698` vs 報告 `1.70`；
    `generated` 分別是 `126×3` 和 `149×3`）⇒ **這一次的差值全部落在 `a` 上，`k` 不是變量**。
    所以「回到 2.02」= **+18.8% 的 `mean len`**，而不是 accept 的 +18.8% —— 後者需要 `a` 從
    `0.233` 漲到 `0.341`（**+46.6%**）。⛔ 別把這兩個寫法混用。
    另注：`0.98 → 0.44` 是 `65c76b8c7` 自己的 commit message 裡的那一對，**不是**這條 2.02/1.70
    的血統（今天的實測是 `0.23266`，比 `0.44` 還低 1.9× ⇒ 中間還有一次未記錄的下降，
    姊妹案件見 `docs/KEFF_CAP_2026-09-26.md`）。全文口徑：`docs/CANDIDATE_SPECS_2026-09-27.md` §1.1b／§1.2b。
* **It says nothing about the bench carrier** (`llama-bench`, sampling on). The "E is a draw"
  observation that started this line of work was taken there; on the server carrier it does not
  reproduce (§2). The bench's RNG-stream identity is a separate, still-open question.
* No throughput claim of any kind: these runs took 93–99 s per arm of which the probe is ~35 s; the
  box was swap-loaded throughout (§6).

## 6. Box / state labels (the certificate is only this clean on this state)

| label | value |
|---|---|
| reclaimable | 9.7–10.2 GiB (gate floor 8 GiB; admitted without a lowered threshold) |
| swap used | 4772 MiB of 6144 (arm boundaries); 5992 of 7168 at 09:19 — **above the 2048 MiB contract line** |
| thermal | `pmset -g therm`: no warning level recorded, at every arm |
| rivals | zero foreign `llama-*` processes at launch (one idle watchdog shell whose *argv* names `llama-bench` — a detector shape, excluded by `is_rival`) |
| pool (long probe) | requests 48317, hits 79.5%, misses 9910 (compulsory 6856 / capacity 3054), evictions 9798, resident 6430.62 MiB |
| **draft fast path** | `calls=446 union=3568 cold(ZERO)=0 (0.0%)`; `verify-strict: refused=0 zero_mapped_selected=0` |

That last row is the one that makes the certificate meaningful: the draft fast path **ZERO-mapped no
expert** in these runs, so the MTP rows are not contaminated by pool warmth on this cell. On a cell
where the draft *does* ZERO-map (`n_fast_draft_cold > 0`), the draft logits *are* a function of
residency by construction — and this tool would then be measuring the pool, not the engine.

## 7. The one decision left (not taken here)

The two pinned references certify the **pre-sharing** draft path, so every future gate read on the
MTP path is an `INVALID COMPARISON` until they are re-baselined:

* short: `m123_oracle_gate.py --write-ref … --ref-note "<why>"` + update `REF_PINS` md5 in the same
  commit (the gate refuses silent drift by design);
* long: `gate_registry.LONG_REF` / `LONG_REF_MD5` (`--verify-ref` exists precisely to catch a silent
  overwrite).

I did **not** touch either pin: re-baselining redefines what "bit-identical" is measured against, and
that is a call about accepting the shared-KV draft as *the* behaviour, not a tooling detail. If it is
taken, the honest form is the one used here — write the reference from one launch, certify it with a
second independent launch (§2), and record the certificate next to it.

## 8. Files

* `scripts/check/mtp_ctx_repro_certify.py` (new; selftest 8/8, five must-fail fixtures)
* `docs/MTP_CTX_REPRODUCIBLE_2026-09-26.md` (this)
* artifacts under `Backup/phase_decomp/` and `Backup/m123_oracle_gate/` (`Backup/` is gitignored)
