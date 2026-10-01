#!/usr/bin/env python3
"""Does merging preads ACROSS intervening experts pay? (edge0's `gap <= 2` rule, priced offline.)

THE QUESTION
------------
`fill_segments_merged_serial` (`llama-expert-cache.cpp:108`) merges only **exactly contiguous**
runs -- `segs[order[j]].file_offset + segs[order[j]].bytes == segs[order[j+1]].file_offset`. In a
stacked GGUF tensor, expert *e* and expert *e+1* of the same tensor are byte-adjacent, so our rule
already merges **adjacent present experts** (index distance 1) and nothing else.

edge0 (`src/edge0/streaming/layer.py:_build_many`) merges when `gap <= 2`: one pread spanning
`min..max`, throwing away the intervening experts' bytes, to save a syscall ("the extra bytes read
between experts are negligible for small gaps"). They publish an end-to-end number for the switch
that matters there (mmap faults -> preadv, 23.6 -> 17.0 ms/token); they do not publish one for the
merge rule itself, and neither do we.

THE MEASUREMENT, and why it needs no GPU
----------------------------------------
A miss dump (`LLAMA_EXPERT_CACHE_MISS_DUMP`) is one `layer expert` line per demand-miss. Layers are
walked in order inside a step, so **consecutive rows with the same layer are that layer's miss set
in one step** -- which is exactly the set the fill batch merges. So the syscall count and the extra
bytes are a property of an existing capture, not of a new run. The tool is a pure function of the
file and refuses to describe the box: the capture's regime is whatever its own conditions doc says
(pass `--conditions` to have it named in the output).

WHAT IT DOES *NOT* DO
---------------------
It does not invent a syscall cost. It reports the ratio that decides the question:

    X = extra_bytes / syscalls_saved        (bytes paid per syscall saved)

merging wins iff  us_per_syscall > X / rate  -- so the implied threshold is printed in us, and a
verdict is only printed when a *measured* `--us-per-read` is supplied. Anything else would be the
kind of number this repo has already learned to distrust: `F1_CB_MISS_REGRESSION_20260920.conditions.md`
records that the `read shape: ... us/job=` line cannot be quoted, because its numerator and
denominator came from different paths.

USAGE
    python3 scripts/check/merge_gap_ab.py --miss-dump Backup/phase_decomp/miss_dump_mtpon.txt \
        --conditions Backup/phase_decomp/F1_CB_MISS_REGRESSION_20260920.conditions.md
    python3 scripts/check/merge_gap_ab.py --miss-dump <f> --us-per-read <measured>   # verdict
    python3 scripts/check/merge_gap_ab.py --selftest
"""
from __future__ import annotations

import argparse
import os
import statistics as st
import sys
import tempfile
from typing import Callable

MIB = 1048576.0


def read_dump(path: str) -> list[tuple[int, int]]:
    """`layer expert` rows, in file order.

    Raises on a malformed row rather than skipping it: 'this row is not a demand' and 'this row is
    a demand I dropped' must not look the same, and the count is what the answer is made of."""
    rows = []
    with open(path) as fh:
        for n, line in enumerate(fh, 1):
            parts = line.split()
            if not parts:
                continue
            if len(parts) != 2 or not all(p.lstrip("-").isdigit() for p in parts):
                raise ValueError(f"{path}:{n}: not a `layer expert` row: {line.strip()!r}")
            rows.append((int(parts[0]), int(parts[1])))
    return rows


def batches(rows: list[tuple[int, int]]) -> tuple[list[tuple[int, list[int]]], dict]:
    """One layer's miss set per step: consecutive rows sharing a layer.

    The grouping rests on a checkable property of the capture -- that layer ids do not go backwards
    *within* a sweep -- so it is verified rather than assumed, and the evidence travels with it."""
    out, cur_layer, cur = [], None, []
    descents, changes = 0, 0
    for layer, expert in rows:
        if layer != cur_layer:
            if cur:
                out.append((cur_layer, cur))
            if cur_layer is not None:
                changes += 1
                if layer < cur_layer:
                    descents += 1
            cur_layer, cur = layer, []
        cur.append(expert)
    if cur:
        out.append((cur_layer, cur))
    return out, {"n_batches": len(out), "layer_changes": changes, "sweep_descents": descents,
                 "rows": len(rows)}


def runs(experts: list[int], max_gap: int) -> list[tuple[int, int]]:
    """Merge while consecutive present experts are within `max_gap` of each other.

    `max_gap` is the **index distance** between merged experts: 1 is our current rule (adjacent
    experts of one tensor are byte-contiguous), 2 is edge0's. A merged span reads every slice in
    between, needed or not."""
    xs = sorted(set(int(e) for e in experts))
    if not xs:
        return []
    out, a, b = [], xs[0], xs[0]
    for x in xs[1:]:
        if x - b <= max_gap:
            b = x
        else:
            out.append((a, b))
            a = b = x
    out.append((a, b))
    return out


def evaluate(bs: list[tuple[int, list[int]]], gaps: list[int],
             slice_bytes: Callable[[int], float], tensor_slices: int = 1) -> dict:
    """Per-gap syscalls / extra slices / extra bytes, summed over the capture.

    `tensor_slices` = how many byte-separated tensors one expert's weights live in. For this GGUF
    it is **3** (`gguf_pool_geometry.py`: `gate=IQ2_S up=IQ2_S down=IQ3_S`; IQ types carry their
    scales inside the tensor), for edge0's own format **9** (weight/scales/biases x gate/up/down).
    It multiplies the SYSCALL count -- each tensor's slices sit at their own file offset and can
    only merge with their own neighbours -- but not `extra_bytes`, which is already in per-expert
    units: the extra slice count is the same in every tensor, and those slices sum to exactly one
    expert's bytes, which is what `slice_bytes()` returns."""
    refs = [(l, sorted(set(e)), slice_bytes(l)) for l, e in bs]
    res = {}
    for g in gaps:
        sysc = extra = needed = 0
        extra_b = 0.0
        for _l, xs, per in refs:
            needed += len(xs)
            rs = runs(xs, g)
            sysc += tensor_slices * len(rs)
            for a, z in rs:
                present = sum(1 for x in xs if a <= x <= z)
                extra += (z - a + 1) - present
                extra_b += ((z - a + 1) - present) * per
        res[g] = {"syscalls": sysc, "needed_slices": needed, "extra_slices": extra,
                  "extra_bytes": extra_b}
    return res


def report(res: dict, base_gap: int, rate_mib_s: float, us_per_read: float | None) -> list[str]:
    lines, base = [], res[base_gap]
    bytes_per_us = rate_mib_s * MIB / 1e6
    for g, r in sorted(res.items()):
        saved = base["syscalls"] - r["syscalls"]
        lines.append(f"  max_gap={g:<2}  syscalls={r['syscalls']:>7}  ({saved:+8} vs current)  "
                     f"extra={r['extra_slices']:>7} slices / {r['extra_bytes'] / MIB:>9.1f} MiB")
        if saved <= 0:
            lines.append("                 (no syscall saved)")
            continue
        x = r["extra_bytes"] / saved
        lines.append(f"                 X = {x:>14,.0f} bytes paid per syscall saved"
                     f"  =>  wins iff a read costs more than {x / bytes_per_us:>8.3f} us"
                     f"   (at {rate_mib_s:.0f} MiB/s)")
        if us_per_read is not None:
            net_ms = (saved * us_per_read - r["extra_bytes"] / bytes_per_us) / 1000.0
            lines.append(f"                 at {us_per_read:.0f} us/read  =>  net {net_ms:>+9.1f} ms "
                         f"over this capture   [{'WORTH IT' if net_ms > 0 else 'NOT worth it'}]")
    return lines


def selftest() -> int:
    bad = 0

    def c(name, ok, got=""):
        nonlocal bad
        if not ok:
            bad += 1
        print(f"  {'ok  ' if ok else 'FAIL'}  {name}{'  -> ' + str(got) if got else ''}")

    rows = [(0, 1), (0, 2), (0, 3), (1, 7), (2, 9), (2, 11), (0, 5), (1, 6), (2, 8)]
    bs, meta = batches(rows)
    c("consecutive same-layer rows form one batch each", meta["n_batches"] == 6, meta)
    c("the sweep restart is visible (one descending layer change)", meta["sweep_descents"] == 1, meta)
    c("batches carry their own layer", bs[0] == (0, [1, 2, 3]) and bs[2] == (2, [9, 11]), bs)
    c("the same layer is two separate batches across two sweeps",
      [b for b in bs if b[0] == 0] == [(0, [1, 2, 3]), (0, [5])], [b for b in bs if b[0] == 0])

    c("gap=1 merges adjacent experts into ONE read", runs([1, 2, 3], 1) == [(1, 3)])
    c("gap=1 does not merge a distance-2 pair", runs([9, 11], 1) == [(9, 9), (11, 11)])
    c("gap=2 does merge it", runs([9, 11], 2) == [(9, 11)])
    c("duplicate experts in a batch are one demand", runs([4, 4, 4], 1) == [(4, 4)])
    c("an empty batch yields no reads", runs([], 2) == [])

    ev = evaluate([(0, [9, 11])], [1, 2], lambda _l: 1024.0)
    c("gap=1 on [9,11]: two syscalls, no extra",
      ev[1]["syscalls"] == 2 and ev[1]["extra_slices"] == 0, ev[1])
    c("gap=2 on [9,11]: one syscall, one extra slice = 1024 B",
      ev[2]["syscalls"] == 1 and ev[2]["extra_slices"] == 1 and ev[2]["extra_bytes"] == 1024.0,
      ev[2])
    ev2 = evaluate([(0, [1, 2, 4, 5])], [1, 2], lambda _l: 1.0)
    c("gap=2 reads across one hole and pays for it",
      ev2[2]["syscalls"] == 1 and ev2[2]["extra_slices"] == 1, ev2[2])
    c("gap=1 pays nothing there and takes two reads",
      ev2[1]["syscalls"] == 2 and ev2[1]["extra_slices"] == 0, ev2[1])
    ev3 = evaluate([(0, [1, 2, 3]), (39, [9, 11])], [1, 2], lambda l: 2.0 if l == 39 else 1.0)
    c("per-layer slice size is used, not a global one",
      abs(ev3[2]["extra_bytes"] - 2.0) < 1e-9, ev3[2])
    ev4 = evaluate([(0, [9, 11])], [2], lambda _l: 1.0, tensor_slices=3)
    c("tensor_slices multiplies syscalls but NOT extra_bytes",
      ev4[2]["syscalls"] == 3 and ev4[2]["extra_bytes"] == 1.0, ev4[2])
    c("tensor_slices=1 is the per-tensor view (what the structural tests assert)",
      evaluate([(0, [9, 11])], [2], lambda _l: 1.0)[2]["syscalls"] == 1)

    no = "\n".join(report(ev2, 1, 823.0, None))
    with_ = "\n".join(report(ev2, 1, 823.0, 50.0))
    c("without --us-per-read: threshold only, no verdict",
      "wins iff a read costs" in no and "WORTH IT" not in no and "NOT worth it" not in no)
    c("with a measured cost the rule fires and names a direction",
      ("WORTH IT" in with_) or ("NOT worth it" in with_))

    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as fh:
        fh.write("0 12\nnot a row\n")
        p = fh.name
    try:
        try:
            read_dump(p)
            c("a malformed row raises", False, "no exception")
        except ValueError:
            c("a malformed row raises instead of being skipped", True)
    finally:
        os.unlink(p)

    print(f"\nmerge_gap_ab selftest: {'OK' if bad == 0 else f'{bad} FAILED'}")
    return 0 if bad == 0 else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--miss-dump", default="", help="LLAMA_EXPERT_CACHE_MISS_DUMP capture")
    ap.add_argument("--per-expert-mib", type=float, default=1.0703,
                    help="per-expert bytes of a TYPICAL trunk layer (Nail: 1.0703; blk.39 1.3906)")
    ap.add_argument("--layer-bytes", action="append", default=[], metavar="L=MIB",
                    help="override one layer's per-expert size, e.g. --layer-bytes 39=1.3906")
    ap.add_argument("--gaps", default="1,2,3,4,8")
    ap.add_argument("--tensor-slices", type=int, default=3,
                    help="byte-separated tensors per expert: 3 for this GGUF (gate/up/down), "
                         "9 for edge0's weight/scales/biases format")
    ap.add_argument("--rate-mib-s", type=float, default=823.0,
                    help="sequential device rate; 823 is this box's measured ceiling")
    ap.add_argument("--us-per-read", type=float, default=None,
                    help="MEASURED cost of one pread round trip; without it only the threshold is "
                         "printed, never a verdict")
    ap.add_argument("--conditions", default="", help="the capture's conditions doc (regime label)")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    if not args.miss_dump:
        raise SystemExit("need --miss-dump or --selftest")

    rows = read_dump(args.miss_dump)
    bs, meta = batches(rows)
    gaps = [int(x) for x in args.gaps.split(",") if x.strip()]
    if 1 not in gaps:
        raise SystemExit("--gaps must contain 1: the current rule is the baseline")

    print(f"capture  {args.miss_dump}")
    print(f"         {meta['rows']} rows, {meta['n_batches']} batches (one layer's miss set in one "
          f"step), {meta['sweep_descents']} sweep descents")
    if meta["sweep_descents"] == 0:
        print("         WARNING: no descending layer transition -- no sweep ever restarted, so "
              "'consecutive same-layer rows' may NOT be one step's miss set. Do not quote.")
    sizes = [len(set(e)) for _l, e in bs]
    print(f"         miss set per layer: median {st.median(sizes):.0f}, mean {st.mean(sizes):.1f}, "
          f"max {max(sizes)}")
    layer_hist = {}
    for l, e in bs:
        layer_hist[l] = layer_hist.get(l, 0) + len(set(e))
    print("         misses by layer: "
          + " ".join(f"{l}:{n}" for l, n in sorted(layer_hist.items())))
    if args.conditions:
        print(f"regime   see {args.conditions}")

    over = {}
    for spec in args.layer_bytes:
        k, v = spec.split("=")
        over[int(k)] = float(v) * MIB
    base_b = args.per_expert_mib * MIB
    unknown = sorted({l for l, _ in bs if l not in over and l > 39})
    if unknown:
        print(f"         note: layer(s) {unknown} carry no geometry override -- they are being "
              f"priced at the trunk value, which is an assumption, not a measurement")
    print(f"\nbytes/slice  {args.per_expert_mib:.4f} MiB (typical trunk)"
          + (f"; overrides {args.layer_bytes}" if args.layer_bytes else ""))
    print(f"read shape  {args.tensor_slices} byte-separated tensors per expert (each merges only "
          f"with its own neighbours)")
    print()
    for line in report(evaluate(bs, gaps, lambda l: over.get(l, base_b), args.tensor_slices), 1,
                       args.rate_mib_s, args.us_per_read):
        print(line)
    if args.us_per_read is None:
        print("\n  no --us-per-read supplied: the question is 'does one read cost more than the "
              "threshold above?', and that has to be measured, not assumed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
