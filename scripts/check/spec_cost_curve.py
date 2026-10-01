#!/usr/bin/env python3
"""Measure the speculative-decoding cost curve cost(k) = 1 + m*k on the decode axis.

WHY THIS EXISTS
---------------
The MoE-MTP plan (`docs/MOE_MTP_FEASIBILITY_2026-09-18.md`) puts 2-3x on raising the accept
rate. That is only half the model: the achievable speedup is

    S(k) = E(k) / cost(k),      cost(k) = 1 + m*k      (unit = one plain decode step)

where `k` is the number of draft tokens per round and `E` is the expected number of tokens
committed per round. When `m` is large there is NO amortisation: a verify batch of k+1 tokens
costs as much as k+1 separate steps, so `S <= (k+1)/(k+1) = 1` and no accept rate can rescue it.
`m` is therefore the number that decides whether training a better draft head is worth anything,
and it has never been measured -- the 0.21 in the feasibility doc was back-solved from ONE k
(one point cannot separate `E` from `cost`).

HOW IT IS MEASURED (no two-parameter fit needed)
------------------------------------------------
`llama-bench` in this fork runs the speculative path with `--spec-type draft-mtp
--spec-draft-n-max k`. Two things make `E` and `cost` directly observable instead of fitted:

  * `LLAMA_BENCH_SPEC_DBG=1` prints one `SPECDBG round: n_done=.. draft=..` line per verify
    round (llama-bench.cpp:2631). Counting them gives the number of steps actually taken to
    emit `n_gen` tokens, so `E = n_gen / rounds` and the *effective* draft depth is the mean
    `draft=` (not the requested `--spec-draft-n-max`, which the MTP module may cap).
    The warmup generation run uses the NON-speculative path (`test_gen(ctx, 1, ..)`,
    llama-bench.cpp:2926), so the counted rounds are the timed ones and nothing else.
  * The expert cache prints its MTP fast-path counters at teardown
    (llama-expert-cache.cpp:2529), split into verify vs draft. `verify union / verify calls`
    is the size of the expert union the engine actually fetches for one verify batch -- i.e.
    whether the draft tokens' experts really are batched (server path: 18.69 experts/call) or
    whether the instrument silently fell back to one-token-at-a-time (union/calls == 8.00,
    which is exactly what the 09-17 note reports for the bench side).

Then `cost(k) = E(k) / S(k)` with `S(k) = tps(k) / tps(k=0)`, both measured, and
`m = (cost - 1) / k_effective`.

DESIGN (drift, not explanation)
-------------------------------

THE CARRIER IS PINNED, NOT SAMPLED. `E` is `n_gen / rounds`, i.e. 1 + (accepted / rounds), and the
MTP accept step compares the draft token against the TARGET's sampled token. This tool never set
`params.sampling.seed`, which defaults to `LLAMA_DEFAULT_SEED` -- and llama_sampler_init_dist()
resolves that to a fresh `std::random_device` draw per process (llama-sampler.cpp:340-350). So
before 2026-09-26 every launch sampled a different stream and `E` was a draw: one k=2 config gave
E = 0.984 and 1.442 on two reps of the same pass, a spread larger than the k-dependence the sweep
exists to measure. `--seed` now pins it (the engine prints `[CGC seed] sampler_seed=.. pinned=..`,
which `parse_stderr` reads), and the pass is refused unless every k>0 run's own line says the pin
reached it. The E-reproducibility gate then becomes exact rather than statistical: the same
(k, cell, seed) must give the same `E` in every round, because a counter ratio cannot drift.
Single-arm decode noise on this machine is about +/-1.9 t/s, larger than several of the effects
being chased, and no recorded variable (thermal, memory, engine version) orders the arms -- so
this does NOT try to explain the drift, it cancels it:

  * one process per k (llama-bench resolves `--spec-draft-n-max` per invocation, so a single
    call cannot sweep k), and the ks are run in an interleaved ABBA order per round,
  * every ratio is taken **within a round** against that round's own k=0 baseline,
  * the reported figure is the **median over rounds** of those within-round ratios.

Reading rules that apply to every number this prints:
  * llama-bench, not HTTP, and both axes are recorded (the pp row comes free in the same
    invocation and is a control: it must NOT move with k, because k only touches generation).
  * A number is citable only if the thermal sampler recorded NOMINAL at launch and worst.
  * **k_eff == k, checked per run and fail-closed.** `--spec-draft-n-max k` is a request, and this
    tree has produced runs where the engine drafted 1 token per round while the flag said 3
    (2026-09-25: `draft_hist {1: 242}`, mean_draft exactly 1.0 for every round of every rep).
    `E`, `S` and the fitted `m` are only a decomposition of the VERIFY path when the verify batch
    really held k drafts, so a run whose `max(draft) != k` is excluded from the fit and named in
    the verdict. The witnesses are recorded per run (`banner_n_max` = what the bench passed in,
    `impl_n_max` = what the impl bound to, `draft_decode_errors` = the loop breaking out).
    The means to force it: the CELL SHAPE (`--prompt` vs `--depth`, `--cell-extra`).

USAGE
-----
    # the curve, 3 paired rounds, decode axis (-n 128) at depth 0
    python3 scripts/check/spec_cost_curve.py --profile prefill250 --ks 0,1,2,3,5,7 --rounds 3 \
        --json Backup/phase_decomp/spec_cost_curve_<tag>.json

    # see the exact command for one k without running it
    python3 scripts/check/spec_cost_curve.py --ks 3 --rounds 1 --dry-run

    # arithmetic + parsing self-test, no GPU
    python3 scripts/check/spec_cost_curve.py --self-test
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import statistics
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import llama_bench_matrix as lbm  # noqa: E402  (single source of truth for env/argv/batch)
import memory_pressure as mem  # noqa: E402  (single source of truth for the swap/wired state)
import thermal_pressure as thermal  # noqa: E402

ROUND_RE = re.compile(r"SPECDBG round: n_done=(\d+) n_past=(\d+) draft=(\d+)")
VERIFY_RE = re.compile(r"verify: calls=(\d+) union=(\d+) cold=(\d+)")
# The k_eff witnesses. `--spec-draft-n-max k` can be INERT without any visible failure: the run
# completes, `E` stays ~1.5 and `m` comes out large -- and the number is quoted as "verify does not
# amortise". The three lines below are what separate that from a real measurement:
#   * the bench's own banner   -- what the ENGINE CONFIG received (independent of what follows),
#   * the MTP impl ctor line   -- what the impl actually bound itself to (LOG_TRC, needs -v/keep-errors),
#   * "llama_decode[i] returned" -- the draft loop breaks out early when its own step fails, and
#     that print is ERROR level. Since 2026-09-28 llama-bench's default sink FORWARDS ERROR (it
#     used to be a null sink, so this witness was silently absent unless CGC_KEEP_ERRORS=1 or -v);
#     logs older than that need the env, and so do binaries built before it.
BANNER_RE  = re.compile(r"draft n_max=(\d+)")
IMPL_RE    = re.compile(r"spec\s+\S+: - n_max=(-?\d+), n_min=(\d+), p_min=([0-9.]+)")
DERR_RE    = re.compile(r"llama_decode\[(\d+)\] returned (-?\d+)")
DRAFT_RE = re.compile(r"draft: calls=(\d+) union=(\d+) cold=(\d+)")
# Cache teardown counters (printed unconditionally at exit by llama-expert-cache.cpp).
# These are the ONLY place the residency story lives: how many bytes this run re-read,
# how often the pool answered, and how many layers blew through their slot quota.
POOL_READ_RE = re.compile(r"read shape: jobs=(\d+) bytes=(\d+)")
POOL_HIT_RE = re.compile(r"decode/pool \(ensure_slot\+batch\) hits=(\d+)/(\d+) \(([0-9.]+)%\)")
MISS_ATTR_RE = re.compile(r"miss attribution: compulsory=(\d+) capacity=(\d+)")
LAYER_OVER_RE = re.compile(r"layers_distinct_over_slots=(\d+)\s+worst=layer (\d+) distinct=(\d+) slots=(\d+)")
# The carrier pin's witness, printed unconditionally by bench_spec_setup (llama-bench.cpp). `pinned`
# is whether CGC_SERVER_SEED was present; the pair separates "no seed was asked for" from "a seed
# was asked for and this engine ignored it", which is this tree's most repeated defect class.
SEED_RE = re.compile(r"\[CGC seed\] sampler_seed=(\d+) pinned=(\d) temp=([0-9.]+) "
                     r"top_p=([0-9.]+) top_k=(-?\d+)")

# Anything that looks like somebody else's measurement on this machine. Deliberately does NOT
# match on `llama` alone: the 09-18 competitor was `http_duo.py`, whose argv contains no
# `llama` substring at all, so a name-based check gave a false all-clear.
RIVAL_PATTERNS = (
    r"llama-bench",
    r"llama-server",
    r"run_server\.sh",
    r"http_duo\.py",
    r"profile_duo\.py",
    r"decode_sweep\.py",
    r"llama_bench_matrix\.py",
    r"prod_matrix\.py",
)


# ---------------------------------------------------------------------------------------------
# preflight
# ---------------------------------------------------------------------------------------------
def self_and_ancestors() -> set[int]:
    """Our own pid plus every ancestor of it.

    Why ancestry and not a name match: `pgrep -fl PATTERN` matches PATTERN against the WHOLE command
    line, and the line that launches this sweep is a shell whose command naturally names tools. The
    old skip was `"spec_cost_curve" in cmd`, so it missed the launcher the moment the launcher's own
    command mentioned a DIFFERENT rival name -- measured 2026-09-26: a wrapper whose polling command
    contained the literal `llama-bench` (inside its own pgrep argument) was reported as
    "other measurement processes are running" and aborted the sweep. A guard that fires on its own
    caller is worse than no guard: it teaches `--force`, which is how contamination gets in.
    """
    seen: set[int] = set()
    pid = os.getpid()
    while pid and pid not in seen:
        seen.add(pid)
        r = subprocess.run(["ps", "-o", "ppid=", "-p", str(pid)], capture_output=True, text=True)
        try:
            pid = int(r.stdout.strip())
        except ValueError:
            break
    return seen


DETECTOR_SHAPES = ("pgrep", "ps -Ao", "ps ax")   # a poll/detector line is not a measurement


def is_rival(pid: int, cmd: str, skip: set[int]) -> bool:
    """The filter as a pure predicate, so it can be tested without a process table.

    Three independent ways to be excluded: being us/an ancestor (structural), naming this script
    (a sibling that re-invokes us), and being a *detector* rather than a measurement.

    That third rule is measured, not decorative. 2026-09-26: another session's idle watchdog
    (`while [ ! -s .../bench.json ]; do sleep 10; done` whose `pgrep -fl 'harness.py|llama-bench'`
    argument contains the tool names) was reported as "other measurement processes are running" and
    aborted a re-measurement before its first arm. The pattern match was inside the watcher's own
    argument text; the worker it waits for appears under its own name, so nothing is lost by
    ignoring the watcher -- and the alternative (teaching `--force`) is how contamination gets in.
    """
    if pid in skip or "spec_cost_curve" in cmd:
        return False
    return not any(s in cmd for s in DETECTOR_SHAPES)


def rivals() -> list[tuple[int, str]]:
    """Other people's measurement processes: every match that is neither us nor an ancestor of us."""
    out: list[tuple[int, str]] = []
    skip = self_and_ancestors()
    for pat in RIVAL_PATTERNS:
        p = subprocess.run(["pgrep", "-fl", pat], capture_output=True, text=True)
        for line in p.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            pid_s, _, cmd = line.partition(" ")
            try:
                pid = int(pid_s)
            except ValueError:
                continue
            if not is_rival(pid, cmd, skip):
                continue
            out.append((pid, cmd[:110]))
    # de-dup by pid, keep order
    seen: set[int] = set()
    uniq = [(pid, cmd) for pid, cmd in out if not (pid in seen or seen.add(pid))]
    return uniq


def preflight(force: bool) -> None:
    r = rivals()
    if r and not force:
        sys.stderr.write(
            "[preflight] other measurement processes are running; a cost curve measured "
            "alongside them is not attributable:\n")
        for pid, cmd in r:
            sys.stderr.write(f"  [preflight]   {pid}  {cmd}\n")
        sys.stderr.write("[preflight] aborting. Re-run when the machine is free, or pass "
                         "--force to accept the contamination.\n")
        raise SystemExit(2)
    if r:
        sys.stderr.write(f"[preflight] WARNING: {len(r)} rival process(es) present, --force given\n")


# ---------------------------------------------------------------------------------------------
# one measurement
# ---------------------------------------------------------------------------------------------
def build_cmd(k: int, args, extra_env: dict[str, str]):
    res = lbm.resolve(args.profile, extra_env)
    env, argv, scalars = res["env"], res["server_argv"], res["scalars"]
    # The parity the engine's sampling block ASSUMES but does not get: the server's sampling params
    # are in its ARGV (`ARG --temp 0.4 / --top-p 0.8 / --top-k 0`, with no matching ENV line), and
    # forward_argv drops them because llama-bench has no --temp flag -- it reads the env. Measured
    # 2026-09-26 from the engine's own witness: temp=0.80 top_p=0.95 top_k=40, i.e. the struct
    # defaults, while the server ran 0.4 / 0.8 / 0. The accept step compares the draft against the
    # target's sampled token, so this is an ACCEPT-RATE difference, not a cosmetic one.
    sampled = lbm.sampling_env(argv)
    env = {**env, **sampled}
    fwd = lbm.forward_argv(argv)
    if args.batch and args.ubatch:
        b, ub, why = args.batch, args.ubatch, "cli"
    else:
        b, ub, why = lbm.default_batch(env, scalars)
    cmd = [str(lbm.LLAMA_BENCH)] + fwd + [
        "-b", str(b), "-ub", str(ub),
        "-p", str(args.prompt), "-n", str(args.gen), "-d", str(args.depth),
        "-r", "1", "-o", "json",
    ]
    if getattr(args, "warm_skip", 0):
        # llama-bench runs this as a SEPARATE generation before the timed one
        # (llama-bench.cpp:3414-3427), so stderr carries TWO sets of `SPECDBG round:` lines and
        # `parse_stderr` keeps only the last (the timed) segment.
        cmd += ["--warm-skip", str(args.warm_skip)]
    # `-r 1` on purpose: the SPECDBG round lines are not tagged per rep, so one rep per process
    # is what makes `rounds` attributable to a single measured generation.
    if k > 0:
        cmd += ["--spec-type", "draft-mtp", "--spec-draft-n-max", str(k)]
    # The cell shape moves k_eff (see the docstring), so the shape has to be part of the cell's
    # identity rather than of the caller's memory: a shape-only flag like `--ctx-size 0` goes here.
    if args.cell_extra:
        cmd += shlex.split(args.cell_extra)
    return cmd, env, scalars, (b, ub, why), sampled


def round_segments(text: str) -> list[list[tuple[str, str, str]]]:
    """The round lines of each SEPARATE generation call, in order.

    `--warm-skip N` makes llama-bench run one untimed generation of N tokens and then the timed one
    (llama-bench.cpp:3414-3427), and BOTH emit `SPECDBG round:` lines. They are separable without
    guessing: `n_done` counts tokens within one call, so a new call restarts it. Reading the list as
    one call divides the timed window's tokens by both calls' rounds and yields a mean length no
    round ever had -- which is the whole reason this is a function and not a `findall`.
    """
    segs: list[list[tuple[str, str, str]]] = []
    prev = None
    for r in ROUND_RE.findall(text):
        d = int(r[0])
        if prev is None or d < prev:
            segs.append([])
        segs[-1].append(r)
        prev = d
    return segs


def parse_stderr(text: str, n_gen: int) -> dict:
    segs = round_segments(text)
    rounds = segs[-1] if segs else []          # the last call is the TIMED one
    n_rounds = len(rounds)
    drafts = [int(d) for _, _, d in rounds]
    out = {
        "rounds": n_rounds,
        "mean_draft": round(statistics.fmean(drafts), 3) if drafts else None,
        "max_draft": max(drafts) if drafts else None,
        "last_n_done": int(rounds[-1][0]) if rounds else None,
        "n_gen": n_gen,
        "draft_hist": {str(d): drafts.count(d) for d in sorted(set(drafts))},
        # provenance for the caliber above: 2 segments == a warm-up call was present, and
        # `warm_rounds` is what a whole-list read would have wrongly added to `rounds`.
        "round_segments": len(segs),
        "warm_rounds": sum(len(x) for x in segs[:-1]),
    }
    # The untimed --warm-skip call is kept as a REPORTED number and no longer as a gate. It is a
    # different slice of the same token stream over a different context, so it cannot be expected
    # to agree with the timed one even when the carrier is pinned; what tests reproducibility now
    # is `e_stable`, computed in analyse() across the pass's rounds. Measured 2026-09-26 (k=2):
    # warm E 1.192 vs timed E 0.984 in one arm, and timed E 0.984 vs 1.442 across the two reps.
    warm = segs[:-1]
    if warm and warm[-1]:
        wt = int(warm[-1][-1][0]) + 1          # n_done restarts per call and is 0-based
        wr = sum(len(x) for x in warm)
        out["warm_tokens"] = wt
        out["E_warm"] = round(wt / wr, 4) if wr else None
    # k_eff is the EFFECTIVE draft depth. `max_draft` -- not the mean -- is what the gate below
    # compares to k: a cap shows up as a max, while a mean can be dragged down by sampling.
    b = BANNER_RE.search(text)
    if b:
        out["banner_n_max"] = int(b.group(1))
    i = IMPL_RE.search(text)
    if i:
        out["impl_n_max"], out["impl_n_min"], out["impl_p_min"] = \
            int(i.group(1)), int(i.group(2)), float(i.group(3))
    derr = DERR_RE.findall(text)
    if derr:
        out["draft_decode_errors"] = len(derr)
        out["draft_decode_first"] = f"step {derr[0][0]} -> ret {derr[0][1]}"
    # E = tokens committed per verify round. Only meaningful when the round count is real.
    out["E"] = round(n_gen / n_rounds, 4) if n_rounds else None
    m = VERIFY_RE.search(text)
    if m:
        calls, union, cold = int(m.group(1)), int(m.group(2)), int(m.group(3))
        out["verify_calls"] = calls
        out["verify_union_per_call"] = round(union / calls, 3) if calls else None
        out["verify_cold_pct"] = round(100.0 * cold / union, 2) if union else None
    d = DRAFT_RE.search(text)
    if d:
        calls, union = int(d.group(1)), int(d.group(2))
        out["draft_calls"] = calls
        out["draft_union_per_call"] = round(union / calls, 3) if calls else None
    rd = POOL_READ_RE.search(text)
    if rd:
        out["read_jobs"] = int(rd.group(1))
        out["read_bytes_total"] = int(rd.group(2))
    h = POOL_HIT_RE.search(text)
    if h:
        out["pool_hits"] = int(h.group(1))
        out["pool_lookups"] = int(h.group(2))
        out["pool_hit_pct"] = float(h.group(3))
    ma = MISS_ATTR_RE.search(text)
    if ma:
        out["miss_compulsory"] = int(ma.group(1))
        out["miss_capacity"] = int(ma.group(2))
    lo = LAYER_OVER_RE.search(text)
    if lo:
        out["layers_over_slots"] = int(lo.group(1))
        out["worst_layer_distinct"] = int(lo.group(3))
        out["per_layer_slots"] = int(lo.group(4))  # the quota itself, per layer
    sd = SEED_RE.search(text)
    if sd:
        out["seed_engine"] = int(sd.group(1))
        out["seed_pinned"] = sd.group(2) == "1"
        # What the SAMPLER was actually built with -- the parity witness. Captured here rather than
        # inferred from the argv because the whole point is that the argv is not what the engine read.
        out["sampling_engine"] = {"temp": float(sd.group(3)), "top_p": float(sd.group(4)),
                                  "top_k": int(sd.group(5))}
    return out


def run_one(k: int, rnd: int, args, extra_env: dict[str, str],
            budget_label: str | None = None, budget_bytes: int | None = None) -> dict:
    env_extra = dict(extra_env)
    if budget_bytes is not None:
        # The profile pins BUDGET itself (`[ -z "${CGC_SERVER_EXPERT_CACHE_BYTES+x}" ]`
        # in run_server.sh), so the ONLY way to actually move the pool is to set this
        # BEFORE the resolve -- it has to be folded into the CGC_DUMP_ENV call.
        env_extra["CGC_SERVER_EXPERT_CACHE_BYTES"] = str(budget_bytes)
    cmd, env, scalars, (b, ub, why), sampled = build_cmd(k, args, env_extra)
    tag = f"k{k}" if budget_label is None else f"{budget_label}k{k}"
    print(f"  [{tag} r{rnd}] {' '.join(cmd)}", flush=True)
    if args.dry_run:
        return {"k": k, "round": rnd, "cmd": cmd, "dry_run": True}

    run_env = dict(os.environ)
    run_env.update(env)
    run_env["LLAMA_BENCH_SPEC_DBG"] = "1"
    # The carrier pin. Set HERE rather than through `--extra`: resolve() only echoes back the env
    # keys run_server.sh's dump allowlists, so a new key pushed that way would be dropped silently
    # -- and the engine's own `[CGC seed]` line is what makes such a drop detectable instead.
    if getattr(args, "seed", 0):
        run_env["CGC_SERVER_SEED"] = str(int(args.seed))

    # The cooldown gate. Without it, consecutive arms start hot and the thermal sampler records
    # MODERATE/HEAVY at launch -- which the contract already treats as unquotable, so the run is
    # not wrong, it is worthless. 2026-09-26 measured exactly that: a 12-arm pass with no cooldown
    # left 7 of 9 comparable rounds non-NOMINAL and the batch sign of `m` unrecoverable. The wait is
    # the ONE loop in thermal_pressure.wait_nominal, not a fifth private copy.
    cooldown = None
    if getattr(args, "cooldown", 0):
        lv = thermal.level()
        if lv != 0:
            print(f"  [{tag} r{rnd}] cooling: thermal={thermal.label(lv)}", flush=True)
            cooldown = thermal.wait_nominal(timeout_s=float(args.cooldown), poll_s=15.0,
                                            log=lambda m: print(f"    {m}", flush=True))
        else:
            cooldown = {"ok": True, "waited_s": 0.0, "level": 0, "label": "NOMINAL",
                        "n_readings": 1, "timed_out": False}
        if not cooldown["ok"]:
            print(f"  !! [{tag} r{rnd}] cooldown timed out still {cooldown['label']} -- this arm is "
                  f"NOT quotable", flush=True)

    sampler = thermal.Sampler()
    mem_before = mem.stamp()
    if (mem_before.get("swap_used_mb") or 0) > mem.SWAP_START_LIMIT_MB:
        print(f"  !! [{tag} r{rnd}] swap {mem_before['swap_used_mb']:.0f} MiB at launch (> "
              f"{mem.SWAP_START_LIMIT_MB:.0f}): E is a counter ratio and unaffected, but no "
              f"t/s or ms/step from this arm may be quoted", flush=True)
    t0 = time.time()
    with sampler:
        proc = subprocess.run(cmd, cwd=str(lbm.ROOT), env=run_env,
                              capture_output=True, text=True)
    wall = time.time() - t0
    mem_after = mem.stamp()

    workdir = Path(args.workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    stem = workdir / f"spec_cost_{tag}_r{rnd}_p{args.prompt}_n{args.gen}_d{args.depth}"
    # stderr is the ONLY place the round lines and the cache counters land; stdout is the JSON.
    stem.with_suffix(".stderr.log").write_text(proc.stderr, errors="replace")
    stem.with_suffix(".json").write_text(proc.stdout, errors="replace")

    rows = lbm.parse_rows(proc.stdout)
    tg = next((r for r in rows if int(r.get("n_gen") or 0) > 0), None)
    pp = next((r for r in rows if int(r.get("n_gen") or 0) == 0), None)

    # E (tokens per verify round) and `tps_tg` must describe the SAME window, or `ms_per_step` is a
    # quotient of two regimes. `--warm-skip` shrinks that window (llama-bench.cpp:3444) and the tg
    # row is where the engine reports the shrink, so read it there instead of assuming args.gen.
    # Fail-closed: a nominal --warm-skip that did not take leaves the run out of the m fit.
    eff_gen = int(tg["n_gen"]) if tg and tg.get("n_gen") else int(args.gen)
    want_gen = int(args.gen) - int(getattr(args, "warm_skip", 0) or 0)
    # THREE states, not two: None means "not decidable from this run" and must NOT be folded into
    # False. A run with no tg row (OOM, assert, signal) has no timed window to judge, and it is
    # already excluded by `signal_killed`/no-tps; calling it a failed caliber instead would make one
    # crashed arm read as "the whole sweep's --warm-skip did not take" and void every reading in it.
    # Measured 2026-09-26: a Metal OOM in test_prompt (kIOGPUCommandBufferCallbackErrorOutOfMemory,
    # prefill phase) printed exactly that false verdict.
    if tg is None:
        warm_ok = None
    elif not getattr(args, "warm_skip", 0):
        warm_ok = True
    else:
        warm_ok = eff_gen == want_gen
    if warm_ok is False:
        print(f"  !! [{tag} r{rnd}] warm_skip_applied=False: asked --warm-skip {args.warm_skip}, "
              f"but the tg row reports n_gen={eff_gen} (expected {want_gen})", flush=True)

    rec = {
        "k": k, "round": rnd, "wall_s": round(wall, 1), "cooldown": cooldown,
        "batch": b, "ubatch": ub, "batch_why": why,
        "tps_tg": tg["avg_ts"] if tg else None,
        "sd_tg": tg["stddev_ts"] if tg else None,
        "tps_pp": pp["avg_ts"] if pp else None,
        "thermal": sampler.result,
        # The box's memory state, at launch and at exit, from the same reading the launchers' gates
        # use. E is a counter ratio and cannot drift with it; t/s and ms/step can, so the label has
        # to travel inside the artifact rather than in whoever read the console.
        "mem": {"launch": mem_before, "end": mem_after,
                "worst": mem.worst([mem_before, mem_after])},
        "rc": proc.returncode,
    }
    rec.update(parse_stderr(proc.stderr, eff_gen))
    rec["n_gen_requested"] = int(args.gen)
    rec["n_gen_measured"] = eff_gen
    rec["E_warm_delta"] = (round(abs(rec["E"] - rec["E_warm"]), 4)
                           if k > 0 and rec.get("E") and rec.get("E_warm") else None)
    # The carrier pin, judged from what the ENGINE printed rather than from what we exported --
    # because "a CGC_SERVER_* key was set and never reached the engine" is the failure this whole
    # field exists to catch. FOUR states, not two: k=0 never builds a sampler, and "no --seed" is
    # a different defect from "a seed was asked for and dropped". Fail-closed on the latter two.
    rec["seed"] = int(getattr(args, "seed", 0) or 0)
    rec["sampling_exported"] = sampled
    eng_s = rec.get("sampling_engine")
    # The parity gate. `sampled` is what the profile's own argv said the server would use; the
    # engine's witness line is what the bench actually built. They must agree, and this is not
    # hypothetical: before 2026-09-26 they never did (0.4/0.8/0 vs the struct's 0.80/0.95/40).
    if k == 0 or not eng_s or not sampled:
        rec["sampling_parity"] = None
    else:
        rec["sampling_parity"] = all(
            abs(float(sampled[env_k]) - eng_s[key]) < 1e-6
            for env_k, key in (("CGC_SERVER_TEMP", "temp"), ("CGC_SERVER_TOP_P", "top_p"),
                               ("CGC_SERVER_TOP_K", "top_k")) if env_k in sampled)
        if rec["sampling_parity"] is False:
            print(f"  !! [{tag} r{rnd}] sampling_parity=False: the profile says {sampled}, the "
                  f"engine built {eng_s} -- the two carriers are not sampling the same distribution",
                  flush=True)
    if k == 0:
        rec["seed_applied"] = None
    elif not rec["seed"]:
        rec["seed_applied"] = False
        rec["seed_reason"] = ("no --seed given: the sampler kept LLAMA_DEFAULT_SEED, which is a "
                             "fresh draw per process")
    elif not rec.get("seed_pinned"):
        rec["seed_applied"] = False
        rec["seed_reason"] = ("the engine printed no pinned `[CGC seed]` line: CGC_SERVER_SEED "
                             "never reached the carrier")
    elif rec["seed_engine"] != rec["seed"]:
        rec["seed_applied"] = False
        rec["seed_reason"] = (f"asked seed {rec['seed']}, the engine built its sampler from "
                             f"{rec['seed_engine']}")
    else:
        rec["seed_applied"] = True
    if rec["seed_applied"] is False:
        print(f"  !! [{tag} r{rnd}] seed_applied=False: {rec['seed_reason']} -- E is a draw from "
              f"this carrier rather than a property of k, so this arm's S is not attributable",
              flush=True)
    rec["warm_skip"] = int(getattr(args, "warm_skip", 0) or 0)
    rec["warm_skip_applied"] = warm_ok
    rec["budget_label"] = budget_label
    rec["budget_bytes"] = budget_bytes
    # k=0 emits no SPECDBG lines at all (there is no verify round to report), so E is 1 by
    # construction and a step is one generated token. Everything else reads its step count
    # off the round lines -- rounds==0 there is a MISSING measurement, not E=1.
    if k == 0:
        rec["E"] = 1.0
        rec["steps"] = eff_gen
        rec["E_source"] = "plain-gen (no verify round; 1 token per step)"
        rec["k_eff_ok"] = True  # no draft to honour
    else:
        rec["steps"] = int(rec.get("rounds") or 0)
        rec["E_source"] = "SPECDBG rounds" if rec.get("rounds") else "MISSING"
        # ── the gate ──────────────────────────────────────────────────────────────────────────
        # Additive and fail-closed: it does not change how anything is measured, it decides
        # whether the m this run would contribute may be quoted at all. `cost = E/S` and
        # `m = (cost-1)/k_eff` are only a decomposition of the VERIFY path if the verify batch
        # really held k drafts; with k_eff < k the same arithmetic silently describes a different
        # experiment and lands in the fit as if it were the requested k.
        rec["k_eff"] = rec.get("max_draft")
        rec["k_eff_ok"] = rec.get("max_draft") == k
        if not rec["k_eff_ok"]:
            rec["k_eff_reason"] = _keff_reason(rec, k)
            print(f"  !! [{tag} r{rnd}] k_eff={rec['k_eff']} != k={k}: {rec['k_eff_reason']}",
                  flush=True)
    if rec.get("read_bytes_total") and rec.get("steps"):
        rec["read_mib_per_step"] = round(rec["read_bytes_total"] / 2**20 / rec["steps"], 3)
        if rec.get("warm_skip"):
            # The teardown counter is per PROCESS, so it also carries the untimed warm-up's reads
            # while `steps` is the timed window only. Flagged rather than silently mixed.
            rec["read_mib_per_step_scope"] = (
                f"includes the untimed warm-up reads: inflate by ~{int(args.gen)}/{eff_gen}")
    if rec.get("tps_tg") and rec.get("E"):
        rec["ms_per_step"] = round(rec["E"] / rec["tps_tg"] * 1000.0, 2)
    # rc < 0 = killed by a signal. On this machine that is USUALLY another session's cleanup
    # (observed 2026-09-18 20:17: the plain-gen baseline died rc=-9 mid-generation while a
    # neighbouring session was clearing `llama-server|llama-bench` before its own D5 run).
    # A killed run is not a slow run: its JSON is truncated and its round count is short, so
    # folding it into a median would silently bias the curve. Marked, retried, and if every
    # attempt dies, excluded.
    rec["signal_killed"] = proc.returncode < 0
    if proc.returncode != 0:
        sigs = ("failed to decode", "res = -", "error:", "GGML_ASSERT", "abort", "SIGSEGV",
                "SIGABRT", "out of memory", "Unable to")
        lines = [l.strip() for l in proc.stderr.splitlines() if l.strip()]
        rec["error"] = next((l for l in lines if any(s in l for s in sigs)),
                            lines[-1] if lines else "")
        print(f"  !! [{tag} r{rnd}] rc={proc.returncode}: {rec['error']}", flush=True)
    print(f"  [{tag} r{rnd}] tg={rec['tps_tg']} pp={rec['tps_pp']} "
          f"rounds={rec['rounds']} E={rec['E']} mean_draft={rec['mean_draft']} "
          f"max_draft={rec.get('max_draft')} k_eff_ok={rec.get('k_eff_ok')} "
          f"hist={rec.get('draft_hist') or '-'} banner_n_max={rec.get('banner_n_max')} "
          f"v_union/call={rec.get('verify_union_per_call')} "
          f"E_warm={rec.get('E_warm')} seed={rec.get('seed')}/{rec.get('seed_engine')} "
          f"seed_applied={rec.get('seed_applied')} "
          f"swap={mem_before.get('swap_used_mb')}MiB "
          f"{(sampler.result or {}).get('launch', {}).get('label', '-')}", flush=True)
    return rec


def _keff_reason(rec: dict, k: int) -> str:
    """Name WHICH mechanism capped the draft -- the three have different fixes.

    "capped at N" with no decode error is an engine/config bound; a draft-step decode failure is
    the loop breaking out (speculative.cpp:1739 `if (ret != 0) break;`); MISSING is a shape that
    produced no verify round at all (llama-bench.cpp:2964 breaks when 1+draft does not fit n_ctx).
    """
    if rec.get("draft_decode_errors"):
        return (f"the draft loop's own llama_decode failed ({rec['draft_decode_errors']}x, first "
                f"{rec.get('draft_decode_first')}) -> the loop breaks after the token it already had")
    if not rec.get("rounds"):
        return ("no verify round at all: 1+draft did not fit n_ctx (llama-bench bounds n_ctx to "
                "n_prompt+n_gen+n_depth) or the arm never entered the spec loop")
    md = rec.get("max_draft")
    if md is None:
        return "no draft= field in any round line"
    return (f"capped at {md} while the config asked for {k}" +
            (f" (bench banner: n_max={rec['banner_n_max']})" if rec.get("banner_n_max") else ""))


# ---------------------------------------------------------------------------------------------
# analysis
# ---------------------------------------------------------------------------------------------
def solve_a(E: float, k: float) -> float | None:
    """Per-draft-token accept probability from E = 1 + a + a^2 + ... + a^k."""
    if k <= 0 or E <= 1.0:
        return None
    if E >= k + 1:
        return 1.0
    lo, hi = 0.0, 1.0
    for _ in range(80):
        mid = (lo + hi) / 2.0
        s = (1.0 - mid ** (k + 1)) / (1.0 - mid) if mid < 1.0 else k + 1
        if s < E:
            lo = mid
        else:
            hi = mid
    return round((lo + hi) / 2.0, 4)


def fit_m(points: list[tuple[float, float]]) -> dict:
    """Least squares through the origin on (k_eff, cost-1) => m."""
    if not points:
        return {}
    sxy = sum(x * y for x, y in points)
    sxx = sum(x * x for x, y in points)
    m = sxy / sxx if sxx else None
    ys = [y for _, y in points]
    ybar = statistics.fmean(ys)
    ss_res = sum((y - (m * x if m else 0.0)) ** 2 for x, y in points)
    ss_tot = sum((y - ybar) ** 2 for y in ys)
    return {"m_fit": round(m, 4) if m is not None else None,
            "r2": round(1.0 - ss_res / ss_tot, 4) if ss_tot else None,
            "n_points": len(points)}


def analyse(runs: list[dict], ks: list[int], args, budgets: list[tuple[str, int]] | None = None) -> dict:
    killed = [r for r in runs if r.get("signal_killed")]
    labels = [lbl for lbl, _ in (budgets or [])] or [""]
    per_budget: dict[str, dict] = {}
    for lbl in labels:
        blk = _stats_for_label(runs, ks, lbl)
        if blk and blk["per_k"]:
            per_budget[lbl] = blk
    # Back-compat: with no budget axis everything still collapses onto the flat fields,
    # and because these are the SAME dict objects the per-k loop below fills in,
    # the budget view stays in sync.
    lead = labels[0] if labels[0] in per_budget else next(iter(per_budget), None)
    # `lead is not None`, not `if lead`: without --budgets the only label is the sentinel "",
    # which is FALSY. Testing truthiness here made the flat per_k/fit always empty on every
    # no-budget run -- the shape the curve was originally written for. It is why the 2026-09-25
    # artifact's own `analysis` block reads `per_k {} / verdict NO DATA` while its runs carried
    # real numbers: the analysis was never reached, and the m in circulation came from reading
    # the run rows by hand. Self-test check "honoured k does reach the fit" fails without this.
    _blk = per_budget[lead] if lead is not None else None
    per_k = _blk["per_k"] if _blk else {}
    base = _blk.get("baseline") if _blk else None
    paired = _blk["paired"] if _blk else {}


    points: list[tuple[float, float]] = []
    offenders = [r for r in runs if r.get("k", 0) > 0 and not r.get("k_eff_ok")]
    # Same shape as the k_eff gate and for the same reason: a run whose declared caliber did not
    # take is not a worse measurement, it is a different experiment. Named, and kept out of the fit.
    unapplied = [r for r in runs if r.get("warm_skip_applied") is False]
    # The carrier pin, same shape again: a run whose sampler was not built from the declared seed
    # is not a slightly noisier measurement of E -- it is a DIFFERENT E.
    unpinned = [r for r in runs if r.get("seed_applied") is False]
    # And the third axis: fixing the seed does not help if the carrier is not even sampling the
    # DELIVERY distribution. `sampling_parity` compares the profile's own argv against the engine's
    # witness line; before 2026-09-26 they disagreed on every k>0 run (0.4/0.8/0 vs 0.80/0.95/40).
    noparity = [r for r in runs if r.get("sampling_parity") is False]
    # The reproducibility witness, and the one that actually tests the claim. E is a counter ratio
    # (`E = n_gen / rounds`), so it does not move with thermal, swap or launch drift, and the SAME
    # (k, cell, seed) must therefore give the SAME E in every round -- exact equality, not a
    # tolerance. That is what makes an artifact self-certifying instead of merely self-labelled.
    e_groups: dict[tuple, list[dict]] = {}
    for r in runs:
        if r.get("k", 0) > 0 and r.get("E") is not None and not r.get("signal_killed") \
                and r.get("seed_applied") is not False:
            e_groups.setdefault((r["k"], r.get("budget_label") or ""), []).append(r)
    e_unstable: list[dict] = []
    for key in sorted(e_groups):
        g = e_groups[key]
        Es = sorted({r["E"] for r in g})
        for r in g:
            r["e_stable"] = len(Es) == 1
            r["e_rounds"] = [x["E"] for x in g]
            r["e_repro_testable"] = len(g) >= 2
        if len(Es) != 1:
            e_unstable.append({"k": key[0], "budget": key[1] or None, "n": len(g), "E": Es})
    for k in ks:
        if k == 0 or k not in per_k or not base:
            continue
        pk = per_k[k]
        Ss = paired.get(k) or []
        if not Ss:
            continue
        S = statistics.median(Ss)
        pk["S_paired_median"] = round(S, 4)
        pk["S_min"] = round(min(Ss), 4)
        pk["S_max"] = round(max(Ss), 4)
        pk["accept_a"] = solve_a(pk["E"], pk["mean_draft"]) if pk["E"] else None
        if pk["E"] and S > 0:
            cost = pk["E"] / S
            pk["cost"] = round(cost, 4)
            k_eff = pk["mean_draft"] or k
            pk["m"] = round((cost - 1.0) / k_eff, 4)
            # Gated point: a k whose k_eff was capped must not enter the fit. Keeping it would
            # re-introduce exactly the defect this gate exists for -- m describing a k_eff=1 run
            # while being read as the cost of k drafts.
            if pk.get("k_eff_ok") and pk.get("warm_skip_ok", True):
                points.append((k_eff, cost - 1.0))

    fit = fit_m(points)
    gate = {
        "ok": not offenders and not unapplied and not unpinned and not noparity,
        "offenders": [{"k": r["k"], "k_eff": r.get("k_eff"), "round": r.get("round"),
                       "reason": r.get("k_eff_reason"), "hist": r.get("draft_hist"),
                       "banner_n_max": r.get("banner_n_max"), "impl_n_max": r.get("impl_n_max")}
                      for r in offenders],
        "rule": "a run may contribute m only if max(draft) == k for that run",
        "warm_skip": {
            "unapplied": [{"k": r["k"], "round": r.get("round"), "warm_skip": r.get("warm_skip"),
                           "n_gen_requested": r.get("n_gen_requested"),
                           "n_gen_measured": r.get("n_gen_measured"),
                           "rc": r.get("rc"),
                           "note": "no tg row: not decidable" if r.get("tps_tg") is None else "row "
                                   "reports the untrimmed window"} for r in unapplied],
            "unapplied_count": len(unapplied),
            "rule": "a run may contribute m only if its declared --warm-skip actually shrank the "
                    "timed window (tg n_gen == gen - warm_skip)",
        },
        "seed": {
            "unapplied": [{"k": r["k"], "round": r.get("round"), "seed": r.get("seed"),
                           "engine_seed": r.get("seed_engine"),
                           "engine_pinned": r.get("seed_pinned"),
                           "reason": r.get("seed_reason")} for r in unpinned],
            "unapplied_count": len(unpinned),
            "rule": "a k>0 run may contribute E/m only if the engine's own `[CGC seed]` line says "
                    "it built its sampler from the declared --seed",
        },
        "sampling_parity": {
            "offenders": [{"k": r["k"], "round": r.get("round"),
                           "profile_argv": r.get("sampling_exported"),
                           "engine": r.get("sampling_engine")} for r in noparity],
            "offender_count": len(noparity),
            "rule": "a k>0 run may contribute E/m only if the carrier sampled the distribution the "
                    "profile's own argv specifies (the accept rate IS that distribution)",
        },
        "e_repro": {
            "unstable": e_unstable,
            "groups": len(e_groups),
            "rule": "the same (k, cell, seed) must give the same E in every round; E is a counter "
                    "ratio, so at a pinned carrier that equality is exact rather than statistical",
        },
    }
    out = {"per_k": per_k, "fit": fit, "killed": killed, "k_eff_gate": gate}
    if base:
        out["baseline_tps"] = base["tps_tg"]
    if (budgets or []) and len(labels) > 1:
        out["per_budget"] = {lbl: {"per_k": b["per_k"], "fit": b["fit"],
                                   "baseline_tps": (b.get("baseline") or {}).get("tps_tg"),
                                   "per_layer_slots": b.get("per_layer_slots")}
                             for lbl, b in per_budget.items()}
        out["bytes_regression"] = bytes_regression(runs)

    # The verdict. `m` is what decides whether a better draft head can pay for itself.
    m = fit.get("m_fit")
    if offenders:
        first = offenders[0]
        out["verdict"] = (f"INVALID: k_eff != k in {len(offenders)} run(s) -- --spec-draft-n-max "
                          f"{first['k']} was measured with max(draft)={first.get('max_draft')} "
                          f"({first.get('k_eff_reason')}). m/E from these runs describe a different "
                          f"draft depth; they are excluded from the fit and must not be quoted.")
    elif unpinned:
        u = unpinned[0]
        out["verdict"] = (f"INVALID: the sampler seed did not reach the carrier in {len(unpinned)} "
                          f"run(s) -- e.g. k={u['k']} ({u.get('seed_reason')}). With "
                          f"LLAMA_DEFAULT_SEED the token stream is a fresh std::random_device draw "
                          f"per process, so E (and every m derived from it) describes the draw, "
                          f"not the configuration.")
    elif noparity:
        o = noparity[0]
        out["verdict"] = (f"INVALID: the bench carrier did not sample the delivery distribution in "
                          f"{len(noparity)} run(s) -- profile argv {o.get('sampling_exported')} vs "
                          f"engine {o.get('sampling_engine')}. The MTP accept step compares the "
                          f"draft against the target's SAMPLED token, so a different distribution "
                          f"is a different accept rate, i.e. a different experiment.")
    elif e_unstable:
        u = e_unstable[0]
        out["verdict"] = (f"INVALID: E is not reproducible at a pinned carrier ({len(e_unstable)} "
                          f"config(s) disagree across rounds) -- e.g. k={u['k']}: E = {u['E']} over "
                          f"{u['n']} rounds. E is a counter ratio, so at a fixed seed an exact "
                          f"disagreement means something else in the loop is non-deterministic.")
    elif m is None:
        out["verdict"] = "NO DATA"
    elif m >= 0.6:
        out["verdict"] = ("m >= 0.6: verify barely amortises. Raising the accept rate cannot "
                          "deliver 2x -- S <= (k+1)/(1+m*k) stays near 1. Training a draft head "
                          "is not the lever here; the batch path is.")
    elif m <= 0.25:
        out["verdict"] = ("m <= 0.25: verify amortises well. The accept rate IS the lever, so "
                          "the plan's training route is worth its cost.")
    else:
        out["verdict"] = ("m in (0.25, 0.6): both levers matter. Accept rate alone tops out "
                          "around 2x; the rest has to come from pushing m down.")
    return out


# ---------------------------------------------------------------------------------------------
# budget axis helpers
# ---------------------------------------------------------------------------------------------
def _budget_label(tok: str) -> str:
    """GiB token -> short tag used in log/experiment names (8 -> p8g, 6.5 -> p6p5g)."""
    return "p" + str(tok).strip().replace(".", "p") + "g"


def _med(vals: list[float]) -> float | None:
    vals = [v for v in vals if v is not None]
    return round(statistics.median(vals), 3) if vals else None


def _stats_for_label(runs: list[dict], ks: list[int], label: str) -> dict:
    """All paired statistics for one pool budget. Mirrors what analyse() used to do inline."""
    by_round: dict[int, dict[int, dict]] = {}
    for r in runs:
        if r.get("signal_killed"):
            continue  # truncated JSON, short round count
        if (r.get("budget_label") or "") != label:
            continue
        by_round.setdefault(r["round"], {})[r["k"]] = r

    per_k: dict[int, dict] = {}
    for k in ks:
        recs = [by_round[rd][k] for rd in sorted(by_round) if k in by_round[rd]]
        ok = [r for r in recs if r.get("tps_tg")]
        if not ok:
            continue
        # Baseline E is 1 by construction now (set in run_one), so it can be averaged safely.
        per_k[k] = {
            "n": len(ok),
            "tps_tg": round(statistics.median([r["tps_tg"] for r in ok]), 3),
            "tps_pp": round(statistics.median([r["tps_pp"] for r in ok]), 3) if ok[0].get("tps_pp") else None,
            "E": round(statistics.median([r["E"] for r in ok if r.get("E")]), 4) if any(r.get("E") for r in ok) else None,
            "mean_draft": round(statistics.fmean([r["mean_draft"] for r in ok if r.get("mean_draft")]), 3)
                          if any(r.get("mean_draft") for r in ok) else None,
            "max_draft": max([r["max_draft"] for r in ok if r.get("max_draft") is not None], default=None),
            "k_eff_ok": all(r["k_eff_ok"] for r in ok if r["k"] > 0),
            "verify_union_per_call": round(statistics.fmean(
                [r["verify_union_per_call"] for r in ok if r.get("verify_union_per_call")]), 3)
                if any(r.get("verify_union_per_call") for r in ok) else None,
            # residency read-outs -- these are what the budget axis is FOR
            "read_mib_per_step": _med([r.get("read_mib_per_step") for r in ok]),
            "ms_per_step": _med([r.get("ms_per_step") for r in ok]),
            "pool_hit_pct": _med([r.get("pool_hit_pct") for r in ok]),
            "layers_over_slots": _med([r.get("layers_over_slots") for r in ok]),
            "worst_layer_distinct": _med([r.get("worst_layer_distinct") for r in ok]),
            "warm_skip_ok": all(r.get("warm_skip_applied") is not False for r in ok),
            "clean": all(
                ((r.get("thermal") or {}).get("launch", {}) or {}).get("label") == "NOMINAL"
                and ((r.get("thermal") or {}).get("worst", {}) or {}).get("label") == "NOMINAL"
                for r in ok),
        }
    per_layer_slots = next((r.get("per_layer_slots") for r in runs
                            if (r.get("budget_label") or "") == label and r.get("per_layer_slots")), None)

    base = per_k.get(0)
    paired: dict[int, list[float]] = {}
    for rd in sorted(by_round):
        b = by_round[rd].get(0)
        if not b or not b.get("tps_tg"):
            continue
        for k in ks:
            if k == 0:
                continue
            r = by_round[rd].get(k)
            if not r or not r.get("tps_tg") or not r.get("E"):
                continue
            paired.setdefault(k, []).append(r["tps_tg"] / b["tps_tg"])
    return {"per_k": per_k, "paired": paired, "baseline": base,
            "per_layer_slots": per_layer_slots}


def bytes_regression(runs: list[dict]) -> dict:
    """ms/step vs MiB/step across BOTH budgets and all k.

    With one budget these two are collinear with k, so the slope is uninterpretable. Two budgets
    put differing working-set sizes at the same k, which is what makes the slope attributable.
    Residual-by-k is the other half of the answer: if the residual grows with k at equal bytes,
    then something OTHER than re-read traffic scales with the draft depth.
    """
    pts = [(r["read_mib_per_step"], r["ms_per_step"], r["k"], (r.get("budget_label") or ""))
           for r in runs
           if not r.get("signal_killed") and not r.get("dry_run")
           and r.get("read_mib_per_step") and r.get("ms_per_step")]
    if len(pts) < 4:
        return {"n_points": len(pts), "note": "not enough points to fit (<4)"}
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    n = len(xs)
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx if sxx else None
    ic = my - (slope or 0.0) * mx
    ss_tot = sum((y - my) ** 2 for y in ys)
    ss_res = sum((y - (ic + slope * x)) ** 2 for x, y in zip(xs, ys)) if slope else ss_tot
    resid_k: dict[int, float] = {}
    for x, y, k, _lbl in pts:
        resid_k.setdefault(k, []).append(y - (ic + slope * x))
    resid_b: dict[str, float] = {}
    for x, y, _k, lbl in pts:
        resid_b.setdefault(lbl, []).append(y - (ic + slope * x))
    return {
        "n_points": n,
        "slope_ms_per_mib": round(slope, 3) if slope else None,
        "intercept_ms": round(ic, 1),
        "r2": round(1.0 - ss_res / ss_tot, 3) if ss_tot else None,
        "implied_rate_mib_s": round(1000.0 / slope, 0) if slope else None,
        "mean_resid_ms_by_k": {str(k): round(statistics.fmean(v), 1) for k, v in sorted(resid_k.items())},
        "mean_resid_ms_by_budget": {k: round(statistics.fmean(v), 1) for k, v in sorted(resid_b.items())},
        "bytes_range_mib": [round(min(xs), 1), round(max(xs), 1)],
        "k_collinear_note": "if every k appears at only one byte level this fit is still confounded",
    }


def report_budget_axis(runs: list[dict], analysis: dict, args) -> None:
    pb = analysis.get("per_budget") or {}
    if not pb:
        return
    print(f"\n{'=' * 100}\n  budget axis (expert pool)\n{'=' * 100}")
    hdr = (f"{'pool':>6s} {'slots':>6s} {'k':>3s} {'n':>2s} {'E':>6s} {'S':>6s} {'cost':>6s} "
           f"{'m':>7s} {'MiB/step':>9s} {'ms/step':>8s} {'hit%':>6s} {'over':>5s} {'worst':>6s}")
    print(hdr)
    print("-" * len(hdr))
    for lbl in sorted(pb):
        blk = pb[lbl]
        slots = blk.get("per_layer_slots")
        for k in sorted(blk["per_k"]):
            p = blk["per_k"][k]
            print(f"{lbl:>6s} {str(slots or '-'):>6s} {k:>3d} {p['n']:>2d} "
                  f"{str(p.get('E') if p.get('E') is not None else '-'):>6s} "
                  f"{('%6.3f' % p['S_paired_median']) if p.get('S_paired_median') else '     -':>6s} "
                  f"{str(p.get('cost') or '-'):>6s} {str(p.get('m') or '-'):>7s} "
                  f"{str(p.get('read_mib_per_step') or '-'):>9s} "
                  f"{str(p.get('ms_per_step') or '-'):>8s} "
                  f"{str(p.get('pool_hit_pct') or '-'):>6s} "
                  f"{str(p.get('layers_over_slots') or '-'):>5s} "
                  f"{str(p.get('worst_layer_distinct') or '-'):>6s}")
        f = blk.get("fit") or {}
        print(f"       -> baseline t/s {blk.get('baseline_tps')}   m_fit {f.get('m_fit')} "
              f"r2 {f.get('r2')}")
    br = analysis.get("bytes_regression") or {}
    if br.get("n_points", 0) >= 4:
        print("\n  ms/step vs MiB/step across all pools and k:")
        print(f"    n = {br['n_points']}   slope = {br['slope_ms_per_mib']} ms/MiB   "
              f"intercept = {br['intercept_ms']} ms   r2 = {br['r2']}")
        print(f"    implied read rate = {br['implied_rate_mib_s']} MiB/s   "
              f"bytes range = {br['bytes_range_mib']} MiB/step")
        print(f"    mean residual by k      = {br['mean_resid_ms_by_k']}")
        print(f"    mean residual by budget = {br['mean_resid_ms_by_budget']}")
        print("    read: a residual that grows with k at EQUAL bytes means some of m is not")
        print("    re-read traffic (it is the k draft forwards / per-step sync).")
    else:
        print(f"\n  bytes regression skipped: {br.get('note') or br}")

def report(runs: list[dict], analysis: dict, args) -> None:
    print(f"\n{'=' * 100}\n  spec cost curve -- {args.profile}  "
          f"p{args.prompt}/n{args.gen}/d{args.depth}  rounds={args.rounds}  "
          f"seed={getattr(args, 'seed', 0)}\n{'=' * 100}")
    hdr = (f"{'k':>3s} {'n':>2s} {'draft':>6s} {'max':>4s} {'k_eff':>6s} {'E':>6s} {'a':>6s} "
           f"{'t/s tg':>8s} {'t/s pp':>8s} "
           f"{'S':>6s} {'S range':>14s} {'cost':>6s} {'m':>7s} {'v_union':>8s} {'clean':>6s}")
    print(hdr)
    print("-" * len(hdr))
    for k in sorted(analysis["per_k"]):
        p = analysis["per_k"][k]
        rng = f"{p['S_min']:.3f}..{p['S_max']:.3f}" if p.get("S_min") is not None else "-"
        print(f"{k:>3d} {p['n']:>2d} {str(p['mean_draft'] or '-'):>6s} "
              f"{str(p.get('max_draft') if p.get('max_draft') is not None else '-'):>4s} "
              f"{('ok' if p.get('k_eff_ok') else 'CAPPED'):>6s} "
              f"{str(p['E'] if p['E'] is not None else '-'):>6s} "
              f"{str(p.get('accept_a') or '-'):>6s} {p['tps_tg']:>8.2f} "
              f"{('%8.2f' % p['tps_pp']) if p['tps_pp'] else '       -':>8s} "
              f"{('%6.3f' % p['S_paired_median']) if p.get('S_paired_median') else '     -':>6s} "
              f"{rng:>14s} {str(p.get('cost') or '-'):>6s} {str(p.get('m') or '-'):>7s} "
              f"{str(p.get('verify_union_per_call') or '-'):>8s} "
              f"{('yes' if p.get('thermal_clean', p.get('clean')) else 'NO'):>6s}")
    f = analysis.get("fit") or {}
    if analysis.get("killed"):
        print(f"\n  !! {len(analysis['killed'])} run(s) were killed by a signal and were EXCLUDED "
              f"(truncated JSON / short round count):")
        for r in analysis["killed"]:
            print(f"  !!   k={r['k']} round={r['round']} rc={r['rc']} "
                  f"{(r.get('error') or '')[:80]}")
    print(f"\n  baseline (k=0) t/s: {analysis.get('baseline_tps')}")
    g = analysis.get("k_eff_gate") or {"ok": True, "offenders": []}
    print(f"  k_eff gate: {'PASS' if g['ok'] else 'FAIL'}  (rule: {g.get('rule')})")
    for o in g.get("offenders", []):
        print(f"    k={o['k']} r{o['round']}: k_eff={o['k_eff']} hist={o.get('hist')} "
              f"banner_n_max={o.get('banner_n_max')} impl_n_max={o.get('impl_n_max')}\n"
              f"      why: {o.get('reason')}")
    sg = g.get("seed") or {}
    print(f"  seed gate: {'PASS' if sg.get('unapplied_count') == 0 else 'FAIL'}  "
          f"(rule: {sg.get('rule')})")
    for u in sg.get("unapplied", []):
        print(f"    k={u['k']} r{u['round']}: asked {u['seed']}, engine {u['engine_seed']} "
              f"(pinned={u['engine_pinned']}) -- {u['reason']}")
    pg = g.get("sampling_parity") or {}
    print(f"  sampling parity: {'PASS' if pg.get('offender_count') == 0 else 'FAIL'}  "
          f"(rule: {pg.get('rule')})")
    for o in pg.get("offenders", []):
        print(f"    k={o['k']} r{o['round']}: profile argv {o['profile_argv']} vs engine {o['engine']}")
    erg = g.get("e_repro") or {}
    print(f"  E reproducibility: {'PASS' if not erg.get('unstable') else 'FAIL'}  "
          f"({erg.get('groups') or 0} config group(s); rule: {erg.get('rule')})")
    for u in erg.get("unstable", []):
        print(f"    k={u['k']} budget={u['budget'] or '-'}: E = {u['E']} over {u['n']} rounds -- "
              f"a pinned carrier cannot do this")
    # Where E was measured matters for everything E multiplies into. Printed from the artifact's
    # own readings, per arm, so the label cannot be separated from the number.
    print("  box state per arm (swap MiB at launch -> at exit; thermal launch->worst):")
    for r in runs:
        m_ = (r.get("mem") or {})
        sw = (m_.get("launch") or {}).get("swap_used_mb")
        sw2 = (m_.get("end") or {}).get("swap_used_mb")
        th = (r.get("thermal") or {})
        print(f"    k={r['k']} r{r.get('round')}: swap {sw} -> {sw2}   "
              f"thermal {(th.get('launch') or {}).get('label', '-')} -> "
              f"{(th.get('worst') or {}).get('label', '-')}   "
              f"E={r.get('E')} e_stable={r.get('e_stable')}"
              + ("   [swap over the clean line at launch: E stands, t/s does not]"
                 if (sw or 0) > mem.SWAP_START_LIMIT_MB else ""))
    print(f"  fit cost-1 = m * k_eff :  m = {f.get('m_fit')}  r2 = {f.get('r2')}  "
          f"n = {f.get('n_points')}")
    print(f"  VERDICT: {analysis.get('verdict')}")
    print("\n  k=0 row is the plain gen cell (no --spec-type): S and cost are 1 by construction,")
    print("  and its pp row is the control -- k only touches generation, so pp must not move.")


# ---------------------------------------------------------------------------------------------
# self-test (no GPU)
# ---------------------------------------------------------------------------------------------
def self_test() -> int:
    checks: list[tuple[str, bool]] = []

    def chk(name: str, cond: bool) -> None:
        checks.append((name, bool(cond)))

    # 1. round parsing + E
    err = ("SPECDBG round: n_done=1 n_past=0 draft=3\n"
           "SPECDBG round: n_done=5 n_past=4 draft=3\n"
           "SPECDBG round: n_done=8 n_past=7 draft=3\n")
    p = parse_stderr(err, 128)
    chk("rounds counted", p["rounds"] == 3)
    chk("E = n_gen/rounds", abs(p["E"] - round(128 / 3, 4)) < 1e-9)  # E is stored at 4 dp
    chk("mean_draft", p["mean_draft"] == 3.0)

    # 2. warmup must not be counted: the warmup gen is non-spec, so a run with no round lines
    #    must report rounds == 0 rather than silently defaulting E to 1.
    chk("no round lines -> rounds 0, E None", parse_stderr("nothing here\n", 128)["E"] is None)

    # 3. verify/draft union counters
    err2 = ("llama_expert_cache: MTP fast path: calls=900 union=9900 cold(ZERO)=10 (0.1%)   "
            "verify: calls=400 union=7476 cold=8 (0.1%)   draft: calls=500 union=4000 cold=2 (0.1%)\n")
    q = parse_stderr(err2, 128)
    chk("verify union/call", abs(q["verify_union_per_call"] - 18.69) < 0.01)
    chk("draft union/call", abs(q["draft_union_per_call"] - 8.0) < 1e-6)

    # 4. accept probability inversion: a=0.6, k=3 -> E = 1+0.6+0.36+0.216 = 2.176
    a = solve_a(2.176, 3.0)
    chk("solve_a round trip", a is not None and abs(a - 0.6) < 0.002)
    chk("solve_a clamps at E = k+1", solve_a(4.0, 3.0) == 1.0)
    chk("solve_a: E<=1 is not an accept-rate question", solve_a(1.0, 3.0) is None)

    # 5. the amortisation theorem: m=1 (no amortisation) => S <= 1 for ANY accept rate
    worst = max((k + 1) / (1 + 1.0 * k) for k in range(1, 16))  # E is at most k+1
    chk("m=1 caps speedup at 1.0", abs(worst - 1.0) < 1e-9)

    # 6. fit through the origin
    fit = fit_m([(1.0, 0.2), (2.0, 0.4), (3.0, 0.6), (5.0, 1.0)])
    chk("m_fit on exact line", abs(fit["m_fit"] - 0.2) < 1e-6)
    chk("r2 == 1 on exact line", fit["r2"] is not None and abs(fit["r2"] - 1.0) < 1e-6)

    # 7. cost/m arithmetic end to end: E=2.12, S=1.31, k=3 -> cost=1.618, m=0.206
    cost = 2.12 / 1.31
    chk("cost(3) matches the doc back-solve", abs(cost - 1.6183) < 0.001)
    chk("m from that point", abs((cost - 1) / 3 - 0.2061) < 0.001)

    # 8. rival filter must exclude our own argv
    # The old check here was `"spec_cost_curve" in __file__` -- true by construction, so it could not
    # fail, which is exactly why the launcher-vs-filter defect survived it. These two can.
    _skip = self_and_ancestors()
    chk("rival filter skips us and our launcher (ancestry, not a name match)",
        not is_rival(os.getpid(), "python3 scripts/check/spec_cost_curve.py", _skip)
        and not is_rival(os.getppid(), "bash -c ... pgrep -fl llama-bench ...", _skip))
    chk("... and a FOREIGN detector line is not a rival either",
        not is_rival(4244, "/bin/zsh -c ... pgrep -fl 'harness.py|llama-bench' ... ; "
                           "while [ ! -s x ]; do sleep 10; done", _skip)
        and not is_rival(4245, "zsh -c 'ps -Ao rss,command | grep llama'", _skip))
    chk("... but a stranger running another tool IS still a rival",
        is_rival(4242, "llama-bench -m model.gguf -p 2048", _skip)
        and is_rival(4243, "bash scripts/run_server.sh", _skip)
        and is_rival(4246, "python3 scripts/check/prod_matrix.py --profiles p", _skip))

    # 8b. k_eff witnesses, from the three real line shapes (bench banner / impl ctor / draft loop)
    kerr = ("llama-bench: [spec] draft-mtp enabled (draft n_max=3): the gen cell measures the SPECULATIVE path\n"
            "spec common_speculative_impl_draft_mtp: - n_max=3, n_min=0, p_min=0.00, n_embd=2048, backend_sampling=1\n"
            "SPECDBG round: n_done=1 n_past=0 draft=1\n"
            "SPECDBG round: n_done=3 n_past=2 draft=1\n")
    kp = parse_stderr(kerr, 128)
    chk("banner n_max parsed", kp["banner_n_max"] == 3)
    chk("impl n_max parsed", kp.get("impl_n_max") == 3 and kp.get("impl_n_min") == 0)
    chk("draft histogram", kp["draft_hist"] == {"1": 2})
    chk("capped run is named 'capped'", _keff_reason(kp, 3).startswith("capped at 1"))
    # the draft loop's own decode failure is a DIFFERENT mechanism and must not read as a cap
    ferr = kerr + "spec common_speculative_impl_draft_mtp: llama_decode[1] returned -3\n"
    fp = parse_stderr(ferr, 128)
    chk("draft decode failures counted", fp.get("draft_decode_errors") == 1)
    chk("decode failure named as the mechanism", "llama_decode failed" in _keff_reason(fp, 3))
    chk("no verify round named as the mechanism",
        "no verify round" in _keff_reason({"rounds": 0}, 3))

    # 8c. the gate itself: a capped k must not contribute m to the fit
    def _run(k, tps, E, mean_draft, max_draft, ok):
        return {"k": k, "round": 1, "tps_tg": tps, "tps_pp": 100.0, "E": E,
                "mean_draft": mean_draft, "max_draft": max_draft, "k_eff_ok": ok,
                "k_eff_reason": None if ok else "capped at 1 while the config asked for 3",
                "draft_hist": {"1": 20}, "rounds": 20, "thermal": {"launch": {"label": "NOMINAL"},
                                                                  "worst": {"label": "NOMINAL"}},
                "budget_label": None}
    capped = [_run(0, 10.0, 1.0, None, None, True), _run(3, 6.0, 1.6, 1.0, 1, False)]
    a_cap = analyse(capped, [0, 3], argparse.Namespace())
    chk("gate FAILs on a capped run", a_cap["k_eff_gate"]["ok"] is False)
    chk("capped verdict is INVALID", str(a_cap["verdict"]).startswith("INVALID"))
    chk("capped k contributes no fit point", a_cap["fit"].get("m_fit") is None)
    good = [_run(0, 10.0, 1.0, None, None, True), _run(3, 6.0, 2.4, 2.3, 3, True)]
    a_ok = analyse(good, [0, 3], argparse.Namespace())
    chk("gate PASSes when max(draft) == k", a_ok["k_eff_gate"]["ok"] is True)
    chk("honoured k does reach the fit", a_ok["fit"].get("m_fit") is not None)

    # 8d. --warm-skip: the untimed warm-up is a SEPARATE llama-bench call, so its round lines must
    #     not enter `rounds` -- but they must be counted and reported, not silently dropped.
    warm_err = ("SPECDBG round: n_done=3 n_past=2 draft=3\n"
                "SPECDBG round: n_done=64 n_past=63 draft=3\n"
                "SPECDBG round: n_done=3 n_past=66 draft=3\n"
                "SPECDBG round: n_done=6 n_past=69 draft=3\n")
    wp = parse_stderr(warm_err, 6)          # the TIMED window is gen - warm_skip
    chk("warm-up rounds are not the timed window", wp["rounds"] == 2)
    chk("warm-up rounds are counted, not discarded silently",
        wp["warm_rounds"] == 2 and wp["round_segments"] == 2)
    chk("E is tokens per round OF THE TIMED WINDOW", abs(wp["E"] - round(6 / 2, 4)) < 1e-9)
    chk("no warm-up call -> one segment", parse_stderr(err, 128)["round_segments"] == 1)

    # 8e. a declared caliber that did not take is named and kept out of the fit, like a capped k
    def _wrun(applied):
        r = _run(3, 6.0, 2.4, 2.3, 3, True)
        r.update({"warm_skip": 64, "warm_skip_applied": applied, "n_gen_requested": 128,
                  "n_gen_measured": 64 if applied else 128})
        return r
    a_ws_bad = analyse([_run(0, 10.0, 1.0, None, None, True), _wrun(False)], [0, 3],
                       argparse.Namespace())
    chk("nominal-but-absent warm-skip is named",
        a_ws_bad["k_eff_gate"]["warm_skip"]["unapplied_count"] == 1)
    chk("nominal-but-absent warm-skip contributes no fit point",
        a_ws_bad["fit"].get("m_fit") is None)
    a_ws_ok = analyse([_run(0, 10.0, 1.0, None, None, True), _wrun(True)], [0, 3],
                      argparse.Namespace())
    chk("applied warm-skip still reaches the fit", a_ws_ok["fit"].get("m_fit") is not None)
    a_ws_unknown = analyse([_run(0, 10.0, 1.0, None, None, True), _wrun(None)], [0, 3],
                           argparse.Namespace())
    chk("'not decidable' (no tg row) is NOT a caliber failure",
        a_ws_unknown["k_eff_gate"]["warm_skip"]["unapplied_count"] == 0
        and a_ws_unknown["k_eff_gate"]["ok"] is True)

    # 9. cache teardown counters (the residency read-out the budget axis depends on)
    err3 = ("llama_expert_cache: miss attribution: compulsory=5601 capacity=873 (86.5% / 13.5% of 6474)  evictions=6474  layers_distinct_over_slots=15  worst=layer 0 distinct=211 slots=143\n"
            "llama_expert_cache: read shape: jobs=46347 bytes=4305625088 (0.09 MiB/job as one contiguous run)\n"
            "llama_expert_cache: decode/pool (ensure_slot+batch) hits=6156/12630 (48.7%)  gather (ensure) hits=0/0\n")
    q3 = parse_stderr(err3, 128)
    chk("pool read bytes", q3.get("read_bytes_total") == 4305625088)
    chk("pool hit pct", abs((q3.get("pool_hit_pct") or 0) - 48.7) < 1e-6)
    chk("miss compulsory", q3.get("miss_compulsory") == 5601)
    chk("layers over slots", q3.get("layers_over_slots") == 15)
    chk("worst layer distinct", q3.get("worst_layer_distinct") == 211)
    chk("per-layer slots", q3.get("per_layer_slots") == 143)
    chk("no round lines -> no derived per-step", "E" in q3 and q3["rounds"] == 0)

    # 10. budget axis plumbing
    chk("budget label tokens", _budget_label("8") == "p8g" and _budget_label("6.5") == "p6p5g")
    flat = bytes_regression([{"read_mib_per_step": 10.0, "ms_per_step": 100.0, "k": 0}])
    chk("too few points -> skip regression", flat.get("note") is not None)
    line = [{"read_mib_per_step": float(x), "ms_per_step": 50.0 + 2.0 * x, "k": x,
             "budget_label": "p8g" if x % 2 else "p6g"} for x in (10, 20, 30, 40)]
    fit2 = bytes_regression(line)
    chk("regression recovers the slope", abs((fit2["slope_ms_per_mib"] or 0) - 2.0) < 1e-6)
    chk("regression recovers r2=1", abs((fit2["r2"] or 0) - 1.0) < 1e-6)

    # 11. the carrier pin. (i) the engine's own line is what decides, (ii) an arm whose seed never
    #     reached the engine is refused like a capped k, (iii) E must REPEAT exactly across rounds --
    #     which is the part the old within-launch witness could not test.
    seed_err = ("[CGC seed] sampler_seed=20260926 pinned=1 temp=0.40 top_p=0.80 top_k=0\n"
                "SPECDBG round: n_done=3 n_past=2 draft=3\n"
                "SPECDBG round: n_done=6 n_past=5 draft=3\n")
    sp = parse_stderr(seed_err, 6)
    chk("seed witness parsed", sp.get("seed_engine") == 20260926 and sp.get("seed_pinned") is True)
    chk("the witness also pins what the SAMPLER was built with",
        sp.get("sampling_engine") == {"temp": 0.4, "top_p": 0.8, "top_k": 0})

    # The sampling-parity translation. The server's params are in its ARGV and llama-bench reads
    # env names, so a carrier without this ran temp 0.80/top_p 0.95/top_k 40 against a server at
    # 0.4/0.8/0 -- the measured defect this check exists for.
    argv_fixture = ["--temp", "0", "-m", "m.gguf", "--top-k", "0", "--top-p", "0.8",
                    "--temp", "0.4", "-t", "8"]
    senv = lbm.sampling_env(argv_fixture)
    chk("sampling params are translated out of the server argv",
        senv == {"CGC_SERVER_TEMP": "0.4", "CGC_SERVER_TOP_K": "0", "CGC_SERVER_TOP_P": "0.8"})
    chk("... and the LAST --temp wins (the server's parser takes the last)",
        senv["CGC_SERVER_TEMP"] == "0.4")
    chk("a profile with no sampling flags exports nothing", lbm.sampling_env(["-m", "m.gguf"]) == {})
    chk("an unpinned carrier is reported as unpinned",
        parse_stderr(seed_err.replace("pinned=1", "pinned=0"), 6).get("seed_pinned") is False)
    chk("a missing witness is not mistaken for a pin",
        parse_stderr("no witness here\n", 6).get("seed_engine") is None)

    def _srun(applied: bool, E: float, rnd: int = 1) -> dict:
        r = _run(3, 6.0, E, 2.3, 3, True)
        r.update({"round": rnd, "seed": 20260926, "seed_applied": applied,
                  "seed_engine": 20260926 if applied else None, "seed_pinned": bool(applied),
                  "seed_reason": None if applied else "CGC_SERVER_SEED never reached the carrier"})
        return r

    # 12. sampling parity: a run whose carrier sampled a different distribution is refused.
    bad_par = _run(3, 6.0, 2.4, 2.3, 3, True)
    bad_par.update({"sampling_parity": False, "sampling_exported": {"CGC_SERVER_TEMP": "0.4"},
                    "sampling_engine": {"temp": 0.8, "top_p": 0.95, "top_k": 40}})
    a_par = analyse([_run(0, 10.0, 1.0, None, None, True), bad_par], [0, 3], argparse.Namespace())
    chk("a carrier that sampled the wrong distribution is named",
        a_par["k_eff_gate"]["sampling_parity"]["offender_count"] == 1
        and a_par["k_eff_gate"]["ok"] is False)
    chk("... and has its own verdict", str(a_par["verdict"]).startswith("INVALID")
        and "distribution" in str(a_par["verdict"]))
    ok_par = _run(3, 6.0, 2.4, 2.3, 3, True)
    ok_par.update({"sampling_parity": True, "sampling_exported": {"CGC_SERVER_TEMP": "0.4"},
                   "sampling_engine": {"temp": 0.4, "top_p": 0.8, "top_k": 0}})
    v_par_ok = analyse([_run(0, 10.0, 1.0, None, None, True), ok_par], [0, 3],
                       argparse.Namespace())["verdict"]
    chk("a carrier at parity is not refused for it", "distribution" not in str(v_par_ok))

    a_unpin = analyse([_run(0, 10.0, 1.0, None, None, True), _srun(False, 2.4)], [0, 3],
                      argparse.Namespace())
    chk("a run whose seed never reached the engine is named",
        a_unpin["k_eff_gate"]["seed"]["unapplied_count"] == 1)
    chk("unpinned seed FAILs the gate", a_unpin["k_eff_gate"]["ok"] is False)
    chk("unpinned seed has its own verdict", str(a_unpin["verdict"]).startswith("INVALID")
        and "seed" in str(a_unpin["verdict"]))

    rep_runs = [_run(0, 10.0, 1.0, None, None, True), _srun(True, 2.4, 1), _srun(True, 2.4, 2)]
    a_rep = analyse(rep_runs, [0, 3], argparse.Namespace())
    chk("E that repeats at a pinned carrier passes",
        not a_rep["k_eff_gate"]["e_repro"]["unstable"]
        and a_rep["k_eff_gate"]["ok"] is True and a_rep["fit"].get("m_fit") is not None)
    chk("e_stable is recorded per run from its own round group",
        rep_runs[1]["e_stable"] is True and rep_runs[1]["e_repro_testable"] is True)

    drift_runs = [_run(0, 10.0, 1.0, None, None, True), _srun(True, 2.4, 1), _srun(True, 1.9, 2)]
    a_drift = analyse(drift_runs, [0, 3], argparse.Namespace())
    chk("E that does NOT repeat is named",
        len(a_drift["k_eff_gate"]["e_repro"]["unstable"]) == 1
        and drift_runs[1]["e_stable"] is False)
    chk("non-reproducible E is refused", str(a_drift["verdict"]).startswith("INVALID"))

    single_runs = [_run(0, 10.0, 1.0, None, None, True), _srun(True, 2.4, 1)]
    analyse(single_runs, [0, 3], argparse.Namespace())
    chk("a one-round group cannot testify to reproducibility",
        single_runs[1]["e_repro_testable"] is False)

    # The redefinition, stated as a test: the within-launch warm-vs-timed gap is no longer a gate,
    # because a pinned carrier makes those two calls different slices of one stream over different
    # contexts. A wild E_warm_delta must NOT void an arm whose E repeats across rounds.
    ew_runs = [_run(0, 10.0, 1.0, None, None, True), _srun(True, 2.4, 1), _srun(True, 2.4, 2)]
    for r in ew_runs[1:]:
        r.update({"E_warm": 0.9, "E_warm_delta": 1.5})
    a_ew = analyse(ew_runs, [0, 3], argparse.Namespace())
    chk("a wild warm-vs-timed gap no longer voids a reproducible arm",
        a_ew["k_eff_gate"]["ok"] is True and a_ew["fit"].get("m_fit") is not None)

    for name, ok in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    bad = [n for n, ok in checks if not ok]
    print(f"\nself-test: {len(checks) - len(bad)}/{len(checks)} passed")
    return 1 if bad else 0


# ---------------------------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile", default="prefill250",
                    help="run_server.sh profile the env/argv are resolved from")
    ap.add_argument("--extra", default="", help="extra env as K=V;K=V folded into the resolve")
    ap.add_argument("--ks", default="0,1,2,3,5,7",
                    help="draft depths. 0 = plain gen cell (the paired baseline, MANDATORY)")
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--prompt", default="512")
    ap.add_argument("--gen", default=128, type=int)
    ap.add_argument("--seed", type=int, default=20260926,
                    help="sampling seed exported to the carrier as CGC_SERVER_SEED. The engine's\n"
                         "sampler defaults to LLAMA_DEFAULT_SEED = a fresh std::random_device draw per\n"
                         "process (llama-sampler.cpp:340), and the MTP accept step compares the draft\n"
                         "against the target's SAMPLED token -- so without this, E is a draw. Measured\n"
                         "2026-09-26: one k=2 config gave E = 0.984 and 1.442 on two reps. The value is\n"
                         "arbitrary; only its constancy across the arms of a comparison matters.\n"
                         "0 = leave it unpinned (the historical caliber; the gate then refuses the pass).")
    ap.add_argument("--warm-skip", type=int, default=0,
                    help="llama-bench --warm-skip N: N generated tokens run UNTIMED before the clock\n"
                         "starts, so the timed window is gen-N and the pool is warm when it begins.\n"
                         "0 = the historical caliber. The flag is checked, not assumed: the tg row\n"
                         "must report n_gen == gen-N or the run is named and kept out of the fit.")
    ap.add_argument("--depth", default="0")
    ap.add_argument("--cell-extra", default="",
                    help="verbatim extra llama-bench args appended to every spec cell, e.g.\n"
                         "'--ctx-size 0'. Cell-shape flags belong here because the shape (prompt vs\n"
                         "depth) is what has moved k_eff in this tree.")
    ap.add_argument("--batch")
    ap.add_argument("--ubatch")
    ap.add_argument("--workdir", default="/tmp")
    ap.add_argument("--json")
    ap.add_argument("--cooldown", type=float, default=0.0,
                    help="seconds to wait for NOMINAL before each arm (0 = off). Without it, "
                         "consecutive arms start hot and the sampler's launch label is not "
                         "NOMINAL, which the contract already makes unquotable")
    ap.add_argument("--retry", type=int, default=2,
                    help="re-attempt a run that was killed by a SIGNAL (default 2). Only signal "
                         "deaths are retried; a real failure (assert/OOM) is a result.")
    ap.add_argument("--budgets", default="",
                    help="expert pool budgets in GiB, comma separated (e.g. 8,6). Each one becomes a\nsecond axis: with a single budget MiB/step and k are collinear, so the \nms/step-vs-bytes slope is not attributable. Two budgets fix that.")
    ap.add_argument("--force", action="store_true", help="run even if rivals are present")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        return self_test()
    if args.batch and not args.ubatch:
        args.ubatch = args.batch

    ks = [int(x) for x in args.ks.split(",") if x.strip()]
    if 0 not in ks:
        raise SystemExit("--ks must include 0: every ratio is taken against that round's own "
                         "plain-gen baseline, so a curve without it cannot be paired.")
    extra_env = dict(kv.split("=", 1) for kv in args.extra.split(";") if "=" in kv)
    budgets: list[tuple[str, int]] = []
    for tok in args.budgets.split(","):
        tok = tok.strip()
        if not tok:
            continue
        budgets.append((_budget_label(tok), int(float(tok) * 1024**3)))

    if not args.dry_run:
        preflight(args.force)

    runs: list[dict] = []
    for rnd in range(1, args.rounds + 1):
        # ABBA: reverse on odd rounds so a monotone drift hits the ks in the opposite order.
        order = ks if rnd % 2 == 1 else list(reversed(ks))
        print(f"\n--- round {rnd}/{args.rounds} (order {order}) ---", flush=True)
        # k is the outer loop and budget the inner one: that keeps the two budgets for the
        # SAME k adjacent, so their difference is not a drift difference.
        plan = [(k, lbl, by) for k in order for (lbl, by) in budgets] \
            if budgets else [(k, None, None) for k in order]
        for k, lbl, by in plan:
            # Retry on signal death only. A crash with a real diagnostic (assert/OOM) is a
            # RESULT about that k and must not be retried away -- retrying would quietly turn
            # "k=7 does not fit" into "k=7 is fine on the second try".
            for attempt in range(1, args.retry + 2):
                rec = run_one(k, rnd, args, extra_env, lbl, by)
                if not rec.get("signal_killed"):
                    break
                print(f"  !! [{rec['k']} r{rnd}] killed by signal (rc={rec['rc']}); "
                      f"attempt {attempt}/{args.retry + 1}", flush=True)
            runs.append(rec)

    if args.dry_run:
        for r in runs:
            print(r.get("cmd"))
        return 0

    analysis = analyse(runs, ks, args, budgets)
    report(runs, analysis, args)
    report_budget_axis(runs, analysis, args)
    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(json.dumps(
            {"args": {k: v for k, v in vars(args).items()},
             "runs": runs, "analysis": analysis}, ensure_ascii=False, indent=2))
        print(f"\njson -> {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
