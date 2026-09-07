#!/usr/bin/env python3
"""session_baseline.py — CGC Phase-0.3 session baseline collector.

Post-hoc analyzer that turns one server session's artifacts into a compact,
comparable baseline record:

  python3 scripts/check/session_baseline.py --log <server.log> \\
      [--masscov <masscov_dump.txt>] [--replay <replay_bench.json>] \\
      [--label <free-text>] [--append <history.jsonl>]

Reads:
  --log      server log: cold-guard trips (CGC-COLD-GUARD / -W), per-layer split,
             warm-gate steps, draft accept, decode t/s from print_timing lines.
  --masscov  CGC_MASSCOV_DUMP file: per-layer cur (mass cov) + selcold (count-cold).
  --replay   replay_server_profile.py --bench-output JSON: per-profile score/decode.

Outputs a compact table + one JSON line (appended to --append history if given).
The JSON line is the machine-comparable record; use it to guard drift across
commits (quality gate + count-cold + guard trips + decode variance).
"""
import argparse, glob, json, os, re, statistics, sys

def latest_log(path=None):
    if path:
        return path
    logs = sorted(glob.glob("/Users/alexchuang/Documents/flashkv-devserver/Backup/cgc_logs/llama_server_*.log"),
                  key=os.path.getmtime)
    return logs[-1] if logs else None

def parse_log(path):
    out = {
        "guard_trips": 0,
        "guard_w_trips": 0,
        "guard_by_layer": {},
        "guard_ratios": [],
        "decode_tps": [],
        "prefill_tps": [],
        "draft_accept": [],
        "tasks": 0,
    }
    if not path or not os.path.exists(path):
        return out
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            m = re.search(r"CGC-COLD-GUARD-W: il=(\d+) cold=\d+/\d+ weighted=([\d.]+)%", line)
            if m:
                out["guard_w_trips"] += 1
                out["guard_by_layer"].setdefault(int(m.group(1)), {"w": 0, "c": 0})
                out["guard_by_layer"][int(m.group(1))]["w"] += 1
                out["guard_ratios"].append(float(m.group(2)))
                continue
            m = re.search(r"CGC-COLD-GUARD: il=(\d+) cold=(\d+)/(\d+) \(([\d.]+)%", line)
            if m:
                out["guard_trips"] += 1
                out["guard_by_layer"].setdefault(int(m.group(1)), {"w": 0, "c": 0})
                out["guard_by_layer"][int(m.group(1))]["c"] += 1
                out["guard_ratios"].append(float(m.group(4)))
                continue
            m = re.search(r"draft acceptance = ([\d.]+)", line)
            if m:
                out["draft_accept"].append(float(m.group(1)))
                continue
            m = re.search(r"prompt eval time =\s+([\d.]+) ms /\s+(\d+) tokens", line)
            if m:
                ms, n = float(m.group(1)), int(m.group(2))
                if ms >= 5 and n > 1:
                    out["prefill_tps"].append(n * 1000.0 / ms)
                continue
            m = re.search(r"eval time =\s+([\d.]+) ms /\s+(\d+) tokens", line)
            if m:
                ms, n = float(m.group(1)), int(m.group(2))
                if ms >= 5 and n > 1:
                    out["decode_tps"].append(n * 1000.0 / ms)
                continue
            if re.search(r"task \d+ \|", line):
                out["tasks"] += 1
    return out

def parse_masscov(path):
    """Return per-layer dict {layer: (cur_cov_ratio, selcold_ratio, sel_count)}."""
    out = {}
    if not path or not os.path.exists(path):
        return out
    pat = re.compile(r"layer (\d+) total=([\d.]+) cur=([\d.]+)(?: selcold=([\d.]+) sel=(\d+))?")
    for line in open(path, encoding="utf-8", errors="replace"):
        m = pat.match(line)
        if not m:
            continue
        l = int(m.group(1))
        cur = float(m.group(3))
        sc = float(m.group(4)) if m.group(4) else None
        sel = int(m.group(5)) if m.group(5) else 0
        out[l] = {"cur": cur, "selcold": sc, "sel": sel}
    return out

def masscov_agg(mc):
    pooled = [v for v in mc.values() if v["sel"] > 0 and v["selcold"] is not None]
    if not pooled:
        return {}
    w_sel = sum(v["sel"] for v in pooled)
    return {
        "cur_mean": statistics.mean(v["cur"] for v in pooled),
        "selcold_weighted": sum(v["selcold"] * v["sel"] for v in pooled) / w_sel,
        "selcold_min": min(v["selcold"] for v in pooled),
        "selcold_max": max(v["selcold"] for v in pooled),
        "layers_sel": len(pooled),
    }

def parse_replay(path):
    if not path or not os.path.exists(path):
        return {}
    try:
        data = json.load(open(path))
    except Exception:
        return {}
    out = {}
    for name, p in (data.get("profiles") or {}).items():
        q = p.get("quality") or {}
        out[name] = {
            "score": q.get("score"),
            "decode_tps": q.get("decode_tps"),
            "prefill_tps": q.get("prefill_tps"),
            "draft_accept_pct": q.get("draft_accept_pct"),
            "checks": [c.get("check") for c in (q.get("checks") or []) if c.get("result") == "fail"],
        }
    return out

def _stats(vals):
    vals = [v for v in vals if v is not None]
    if not vals:
        return None
    return {
        "n": len(vals),
        "median": round(statistics.median(vals), 2),
        "min": round(min(vals), 2),
        "max": round(max(vals), 2),
        "spread_pct": round((max(vals) - min(vals)) / statistics.median(vals) * 100, 1) if len(vals) > 1 else 0.0,
    }

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", default=None, help="server log path (default: latest in Backup/cgc_logs)")
    ap.add_argument("--masscov", default=None, help="CGC_MASSCOV_DUMP file path")
    ap.add_argument("--replay", default=None, help="replay_server_profile.py --bench-output JSON")
    ap.add_argument("--label", default="", help="free-text label for the record")
    ap.add_argument("--append", default=None, help="JSONL history file to append the record to")
    args = ap.parse_args()

    log_path = latest_log(args.log)
    lg = parse_log(log_path)
    mc = parse_masscov(args.masscov)
    ma = masscov_agg(mc)
    rp = parse_replay(args.replay)

    print("=" * 72)
    print(f"SESSION BASELINE  label={args.label or '(none)'}")
    print(f"  log     : {log_path}")
    if args.masscov:
        print(f"  masscov : {args.masscov}")
    if args.replay:
        print(f"  replay  : {args.replay}")
    print("-" * 72)
    print(f"  cold-guard trips     : {lg['guard_trips']} count-based + {lg['guard_w_trips']} weighted  (tasks={lg['tasks']})")
    if lg["guard_by_layer"]:
        top = sorted(lg["guard_by_layer"].items(), key=lambda kv: -(kv[1]["c"] + kv[1]["w"]))[:5]
        print("  guard by layer (top): " + ", ".join(f"il={l}:{v['c']+v['w']}" for l, v in top))
    d = _stats(lg["decode_tps"])
    p = _stats(lg["prefill_tps"])
    a = _stats(lg["draft_accept"])
    print(f"  decode t/s           : {d}")
    print(f"  prefill t/s          : {p}")
    print(f"  draft accept         : {a}")
    if ma:
        print(f"  mass cov mean        : {ma['cur_mean']*100:.1f}%")
        print(f"  SELCOLD (count-cold) : weighted {ma['selcold_weighted']*100:.1f}%  min {ma['selcold_min']*100:.1f}%  max {ma['selcold_max']*100:.1f}%  ({ma['layers_sel']} layers)")
    if rp:
        for name, r in rp.items():
            print(f"  replay {name:12s}: score={r['score']} decode={r['decode_tps']} prefill={r['prefill_tps']} fails={r['checks']}")

    record = {
        "label": args.label,
        "log": os.path.basename(log_path) if log_path else None,
        "guard_trips": lg["guard_trips"],
        "guard_w_trips": lg["guard_w_trips"],
        "decode_tps": d,
        "prefill_tps": p,
        "draft_accept": a,
        "masscov": ma,
        "replay": rp,
    }
    if args.append:
        with open(args.append, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        print(f"-" * 72)
        print(f"appended record to {args.append}")
    print("=" * 72)

if __name__ == "__main__":
    main()