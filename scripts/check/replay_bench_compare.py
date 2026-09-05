#!/usr/bin/env python3
"""
replay_bench_compare.py — 比較兩個 replay benchmark JSON, 判定 commit 是否改善。

設計 (2026-09-05):
  - 讀 current.json (這次 commit 跑的) + baseline.json (上一個 commit 的)
  - 對 9 個指標 (3 profile × 3 aspect: quality / decode_tps / peak_rss_mb)
    算 regression / improvement / neutral
  - 套用 replay_bench_reference.json 的 _compare_thresholds:
      decode_tps_regression_pct=10% / peak_rss_mb_regression_pct=15% / quality_score_regression_abs=0.1
  - 結果: 至少 1 個指標改善 (improvement) 且不超過 1 個指標退化 (regression) → PASS
          否則 → FAIL (precommit hook 拒絕 commit)

用法:
  python3 replay_bench_compare.py \
      --current .replay_bench_current.json \
      --baseline .replay_bench_baseline.json \
      --reference scripts/check/replay_bench_reference.json \
      --report .replay_bench_report.json
  exit code: 0 = pass, 1 = fail, 2 = error
"""
import argparse
import json
import sys


PROFILES = ["qa-zh", "longform-zh", "coding"]


def _load(path):
    with open(path) as f:
        return json.load(f)


def _classify(delta_pct, regression_pct, improvement_pct=2.0):
    """根據變化百分比分類: 'improvement' / 'regression' / 'neutral'。
    - 改善: |delta| > improvement_pct 且方向對 (lower_is_better ? delta < 0 : delta > 0)
    - 退化: |delta| > regression_pct 且方向錯
    - 中性: 其他
    """
    if delta_pct is None:
        return "neutral"
    if abs(delta_pct) < improvement_pct:
        return "neutral"
    # |delta| >= improvement_pct, 看方向
    if delta_pct > 0:
        return "improvement"
    else:
        return "regression"


def compare_metric(baseline_val, current_val, lower_is_better, regression_pct):
    """比較單一指標, 回傳 (delta_abs, delta_pct, status)。"""
    if baseline_val is None or current_val is None:
        return None, None, "neutral"
    delta_abs = current_val - baseline_val
    if baseline_val == 0:
        return delta_abs, None, "neutral"
    delta_pct = (delta_abs / baseline_val) * 100.0
    if lower_is_better:
        # 改善 = 下降, 退化 = 上升
        if delta_abs < 0 and abs(delta_pct) >= 2.0:
            return delta_abs, delta_pct, "improvement"
        if delta_abs > 0 and abs(delta_pct) >= regression_pct:
            return delta_abs, delta_pct, "regression"
        return delta_abs, delta_pct, "neutral"
    else:
        # 改善 = 上升, 退化 = 下降
        if delta_abs > 0 and abs(delta_pct) >= 2.0:
            return delta_abs, delta_pct, "improvement"
        if delta_abs < 0 and abs(delta_pct) >= regression_pct:
            return delta_abs, delta_pct, "regression"
        return delta_abs, delta_pct, "neutral"


def compare_benchmarks(current, baseline, reference):
    """對所有 profiles 算 9 個指標的 delta, 回傳 (per_profile_dict, aggregate_status, summary)。
    """
    thresholds = reference.get("_compare_thresholds", {})
    decode_reg = thresholds.get("decode_tps_regression_pct", 10.0)
    prefill_reg = thresholds.get("prefill_tps_regression_pct", 10.0)
    mem_reg = thresholds.get("peak_rss_mb_regression_pct", 15.0)
    qual_reg = thresholds.get("quality_score_regression_abs", 0.1)
    improvement_required = thresholds.get("improvement_required_min_count", 1)
    regression_tolerance = thresholds.get("regression_tolerance_max_count", 1)

    per_profile = {}
    for profile in PROFILES:
        cur_p = current.get("profiles", {}).get(profile)
        base_p = baseline.get("profiles", {}).get(profile)
        if not cur_p or not base_p:
            per_profile[profile] = {"error": "missing profile data"}
            continue

        # 1) quality score
        cur_q = cur_p.get("quality", {}).get("score")
        base_q = base_p.get("quality", {}).get("score")
        q_delta_abs, q_delta_pct, q_status = _classify_quality(base_q, cur_q, qual_reg)

        # 2) decode tps (higher is better)
        cur_d = cur_p.get("speed", {}).get("decode_tps")
        base_d = base_p.get("speed", {}).get("decode_tps")
        d_delta_abs, d_delta_pct, d_status = compare_metric(base_d, cur_d, lower_is_better=False, regression_pct=decode_reg)

        # 3) prefill tps (higher is better)
        cur_pf = cur_p.get("speed", {}).get("prefill_tps")
        base_pf = base_p.get("speed", {}).get("prefill_tps")
        pf_delta_abs, pf_delta_pct, pf_status = compare_metric(base_pf, cur_pf, lower_is_better=False, regression_pct=prefill_reg)

        # 4) peak rss mb (lower is better)
        cur_m = cur_p.get("memory", {}).get("peak_mb")
        base_m = base_p.get("memory", {}).get("peak_mb")
        m_delta_abs, m_delta_pct, m_status = compare_metric(base_m, cur_m, lower_is_better=True, regression_pct=mem_reg)

        per_profile[profile] = {
            "quality": {
                "baseline": base_q, "current": cur_q,
                "delta_abs": q_delta_abs, "delta_pct": None,
                "status": q_status,
            },
            "decode_tps": {
                "baseline": base_d, "current": cur_d,
                "delta_abs": round(d_delta_abs, 2) if d_delta_abs is not None else None,
                "delta_pct": round(d_delta_pct, 2) if d_delta_pct is not None else None,
                "status": d_status,
            },
            "prefill_tps": {
                "baseline": base_pf, "current": cur_pf,
                "delta_abs": round(pf_delta_abs, 2) if pf_delta_abs is not None else None,
                "delta_pct": round(pf_delta_pct, 2) if pf_delta_pct is not None else None,
                "status": pf_status,
            },
            "peak_rss_mb": {
                "baseline": base_m, "current": cur_m,
                "delta_abs": round(m_delta_abs, 1) if m_delta_abs is not None else None,
                "delta_pct": round(m_delta_pct, 2) if m_delta_pct is not None else None,
                "status": m_status,
            },
        }

    # Aggregate verdict
    n_improvement = 0
    n_regression = 0
    n_neutral = 0
    for p, m in per_profile.items():
        for aspect in ("quality", "decode_tps", "prefill_tps", "peak_rss_mb"):
            status = m.get(aspect, {}).get("status", "neutral")
            if status == "improvement":
                n_improvement += 1
            elif status == "regression":
                n_regression += 1
            else:
                n_neutral += 1

    # 加上 aggregate 層級的判斷
    cur_agg = current.get("aggregate", {})
    base_agg = baseline.get("aggregate", {})
    agg_status = {}
    for aspect, lower_is_better, reg in [
        ("decode_tps", False, decode_reg),
        ("prefill_tps", False, prefill_reg),
        ("peak_rss_mb", True, mem_reg),
        ("quality_score", False, qual_reg),  # aggregate quality 是 avg score
    ]:
        cur_v = cur_agg.get(aspect, {}).get("avg")
        base_v = base_agg.get(aspect, {}).get("avg")
        if aspect == "quality_score":
            d, p, s = _classify_quality(base_v, cur_v, qual_reg)
        else:
            d, p, s = compare_metric(base_v, cur_v, lower_is_better=lower_is_better, regression_pct=reg)
        agg_status[aspect] = {"baseline": base_v, "current": cur_v, "status": s, "delta_abs": d}

    if n_regression > regression_tolerance:
        verdict = "FAIL"
        reason = f"too many regressions: {n_regression} > {regression_tolerance} (per-profile)"
    elif n_improvement < improvement_required:
        verdict = "FAIL"
        reason = f"insufficient improvements: {n_improvement} < {improvement_required} (per-profile)"
    else:
        verdict = "PASS"
        reason = f"improvements={n_improvement} regressions={n_regression} neutral={n_neutral}"

    summary = {
        "verdict": verdict,
        "reason": reason,
        "n_improvement": n_improvement,
        "n_regression": n_regression,
        "n_neutral": n_neutral,
        "thresholds": thresholds,
        "aggregate_status": agg_status,
    }
    return per_profile, verdict, summary


def _classify_quality(baseline, current, regression_abs):
    """質量分數比較, 用絕對差 (不是百分比) 因為分數範圍 0-1。"""
    if baseline is None or current is None:
        return None, None, "neutral"
    delta = current - baseline
    if delta >= 0.02:
        return round(delta, 3), None, "improvement"
    if delta <= -regression_abs:
        return round(delta, 3), None, "regression"
    return round(delta, 3), None, "neutral"


def main():
    parser = argparse.ArgumentParser(description="Compare two replay benchmark JSON outputs")
    parser.add_argument("--current", required=True, help="Current bench JSON (this commit)")
    parser.add_argument("--baseline", required=True, help="Baseline bench JSON (previous commit)")
    parser.add_argument("--reference", required=True, help="Reference rules JSON with _compare_thresholds")
    parser.add_argument("--report", default=None, help="Optional: write structured report JSON to this file")
    args = parser.parse_args()

    try:
        current = _load(args.current)
        baseline = _load(args.baseline)
        reference = _load(args.reference)
    except Exception as e:
        print(f"ERROR: failed to load JSON: {e}", file=sys.stderr)
        return 2

    per_profile, verdict, summary = compare_benchmarks(current, baseline, reference)

    report = {
        "verdict": verdict,
        "summary": summary,
        "per_profile": per_profile,
        "current_commit": current.get("commit", "?"),
        "baseline_commit": baseline.get("commit", "?"),
    }
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)

    if args.report:
        try:
            with open(args.report, "w") as f:
                f.write(text)
        except Exception as e:
            print(f"ERROR: failed to write report: {e}", file=sys.stderr)

    # 印 human-readable 摘要到 stderr (給 precommit hook 顯示)
    print(f"\n=== replay benchmark compare ===", file=sys.stderr)
    print(f"verdict: {verdict}", file=sys.stderr)
    print(f"reason:  {summary['reason']}", file=sys.stderr)
    for profile, m in per_profile.items():
        print(f"\n[{profile}]", file=sys.stderr)
        for aspect in ("quality", "decode_tps", "prefill_tps", "peak_rss_mb"):
            d = m.get(aspect, {})
            base = d.get("baseline")
            cur = d.get("current")
            delta = d.get("delta_abs")
            status = d.get("status", "neutral")
            mark = {"improvement": "↑", "regression": "↓", "neutral": "="}.get(status, "?")
            print(f"  {mark} {aspect:14s}  base={base}  cur={cur}  delta={delta}  [{status}]", file=sys.stderr)

    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
