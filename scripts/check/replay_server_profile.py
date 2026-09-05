#!/usr/bin/env python3
"""
replay_server_profile.py — 應用層 replay benchmark + 質量/速度/內存指標

設計 (2026-09-05):
  - 三個 profile (qa-zh / longform-zh / coding) 一次跑完 (--all-profiles)
  - 每個 profile 收集三個面向:
      1. 質量 (quality): 啟發式 keyword / length / syntax 檢查 (0.0-1.0 score)
      2. 速度 (speed):  prefill t/s + decode t/s (從 llama-server timings)
      3. 內存 (memory): llama-server RSS (psutil, 沒裝 fallback 到 ps -o rss= -p PID)
  - 輸出結構化 JSON 到 --bench-output,供 precommit hook 比較

跟舊版差異:
  - 加 --all-profiles / --bench-output / --reference / --server-pid
  - 加 psutil RSS 監控 (before / peak / after)
  - 加啟發式 quality score (從 --reference 讀 rules)
  - 加 aggregated metrics 摘要 (3 profile 平均 / 中位數)
"""
import argparse
import json
import os
import subprocess
import sys
import time
from urllib import request as urlrequest


LONGFORM_STOP = ["<|end|>", "<|output|>", "<|user|>"]


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def http_json(url, payload, timeout):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urlrequest.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlrequest.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ---------------------------------------------------------------------------
# Payload
# ---------------------------------------------------------------------------

def build_payload(profile, model, max_tokens):
    if profile == "qa-zh":
        return {
            "model": model,
            "messages": [
                {"role": "user", "content": "巴黎是哪個國家的首都？請只用一句中文回答。"},
            ],
            "temperature": 0,
            "max_tokens": max_tokens or 24,
            "chat_template_kwargs": {
                "assistant_prefill": "答:巴黎",
            },
            "stop": ["。", "<|end|>", "<|output|>", "<|user|>"],
        }

    if profile == "longform-zh":
        return {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": "Answer directly, after thinking. Lead with the answer, then only what it needs to be correct and usable. Keep the final answer lean. Use plain prose. Never drop correctness.",
                },
                {"role": "user", "content": "為什麼巴黎會成為法國的政治與文化中心？請用一段中文說明。"},
            ],
            "temperature": 0,
            "max_tokens": max_tokens or 220,
            "chat_template_kwargs": {
                "assistant_prefill": "答:巴黎之所以成為法國的政治與文化中心,主要是因為它從12世紀起就是法國王國的首都,並且匯聚了",
            },
            "stop": LONGFORM_STOP,
        }

    if profile == "coding":
        return {
            "model": model,
            "messages": [
                {"role": "user", "content": "寫一個 Python function，計算費氏數列第 n 項。"},
            ],
            "temperature": 0,
            "max_tokens": max_tokens or 512,
            "chat_template_kwargs": {
                "assistant_prefill": "```python\n",
            },
            "stop": ["```", "<|end|>", "<|output|>", "<|user|>"],
        }

    raise ValueError(f"unsupported profile: {profile}")


# ---------------------------------------------------------------------------
# 內存監控 (psutil + ps fallback)
# ---------------------------------------------------------------------------

def get_rss_mb(pid):
    """讀 PID 的 RSS (MB),用 psutil (如果裝) 或 ps -o rss=。失敗回 None。"""
    if pid is None or pid <= 0:
        return None
    # 1) psutil
    try:
        import psutil  # type: ignore
        p = psutil.Process(pid)
        return p.memory_info().rss / (1024.0 * 1024.0)
    except Exception:
        pass
    # 2) ps fallback (macOS / Linux 都有 ps)
    try:
        out = subprocess.run(
            ["ps", "-o", "rss=", "-p", str(pid)],
            capture_output=True, text=True, timeout=2,
        )
        if out.returncode == 0 and out.stdout.strip():
            return int(out.stdout.strip()) / 1024.0  # KB → MB
    except Exception:
        pass
    return None


def sample_rss_during(fn, pid, sample_interval_s=0.5):
    """在 fn() 執行期間,每 sample_interval_s 採樣 RSS,回傳 (result, samples_list)。"""
    samples = []
    before = get_rss_mb(pid)
    if before is not None:
        samples.append(("before", before))
    import threading
    stop = threading.Event()

    def sampler():
        while not stop.is_set():
            rss = get_rss_mb(pid)
            if rss is not None:
                samples.append(("during", rss))
            stop.wait(sample_interval_s)

    t = threading.Thread(target=sampler, daemon=True)
    t.start()
    try:
        result = fn()
    finally:
        stop.set()
        t.join(timeout=2)
    after = get_rss_mb(pid)
    if after is not None:
        samples.append(("after", after))
    return result, samples


def summarize_rss(samples):
    """從 [(phase, rss_mb), ...] 算 peak / avg / delta。"""
    rss_values = [rss for _, rss in samples if rss is not None]
    if not rss_values:
        return {"peak_mb": None, "avg_mb": None, "before_mb": None, "after_mb": None, "delta_mb": None}
    before = next((r for p, r in samples if p == "before"), None)
    after = next((r for p, r in samples if p == "after"), None)
    return {
        "peak_mb": round(max(rss_values), 1),
        "avg_mb": round(sum(rss_values) / len(rss_values), 1),
        "before_mb": round(before, 1) if before is not None else None,
        "after_mb": round(after, 1) if after is not None else None,
        "delta_mb": round((after - before), 1) if (after is not None and before is not None) else None,
        "samples": [[p, round(r, 1)] for p, r in samples],
    }


# ---------------------------------------------------------------------------
# 質量評分 (啟發式, 從 --reference 讀 rules)
# ---------------------------------------------------------------------------

def evaluate_quality(profile, content, finish_reason, reference_rules):
    """根據 reference rules 給 0.0-1.0 score。回傳 (score, checks_list)。"""
    rules = reference_rules.get(profile, {}) if reference_rules else {}
    if not rules:
        return 1.0, [{"check": "no_reference", "result": "skip", "note": "no reference rules provided"}]

    checks = []
    score = 0.0
    weight_sum = 0.0

    # 1) length check
    length_min = rules.get("length_min")
    length_max = rules.get("length_max")
    if length_min is not None or length_max is not None:
        n = len(content or "")
        ok = True
        if length_min is not None and n < length_min:
            ok = False
        if length_max is not None and n > length_max:
            ok = False
        checks.append({"check": "length", "actual": n, "min": length_min, "max": length_max, "result": "pass" if ok else "fail"})
        w = 0.2
        score += (1.0 if ok else 0.0) * w
        weight_sum += w

    # 2) required keyword (any of)
    required_any = rules.get("required_any") or []
    if required_any:
        hit = any(kw in (content or "") for kw in required_any)
        checks.append({"check": "required_any", "keywords": required_any, "result": "pass" if hit else "fail"})
        w = 0.4
        score += (1.0 if hit else 0.0) * w
        weight_sum += w

    # 3) required all
    required_all = rules.get("required_all") or []
    if required_all:
        missing = [kw for kw in required_all if kw not in (content or "")]
        ok = not missing
        checks.append({"check": "required_all", "keywords": required_all, "missing": missing, "result": "pass" if ok else "fail"})
        w = 0.2
        score += (1.0 if ok else 0.0) * w
        weight_sum += w

    # 4) forbidden (any of)
    forbidden = rules.get("forbidden") or []
    if forbidden:
        hit = any(kw in (content or "") for kw in forbidden)
        checks.append({"check": "forbidden", "keywords": forbidden, "result": "pass" if not hit else "fail"})
        w = 0.2
        score += (1.0 if not hit else 0.0) * w
        weight_sum += w

    # 5) finish_reason check
    ok_finish = rules.get("ok_finish_reasons")
    if ok_finish:
        ok = finish_reason in ok_finish
        checks.append({"check": "finish_reason", "actual": finish_reason, "ok": ok_finish, "result": "pass" if ok else "fail"})
        w = 0.2
        score += (1.0 if ok else 0.0) * w
        weight_sum += w

    # 6) loop detection (consecutive identical chars / words)
    loop_max_run = rules.get("loop_max_run", 8)
    if content:
        max_run = 1
        cur_run = 1
        for i in range(1, len(content)):
            if content[i] == content[i-1]:
                cur_run += 1
                if cur_run > max_run:
                    max_run = cur_run
            else:
                cur_run = 1
        ok = max_run <= loop_max_run
        checks.append({"check": "loop_run", "max_run": max_run, "limit": loop_max_run, "result": "pass" if ok else "fail"})
        w = 0.2
        score += (1.0 if ok else 0.0) * w
        weight_sum += w

    if weight_sum == 0:
        return 1.0, checks
    return round(score / weight_sum, 3), checks


# ---------------------------------------------------------------------------
# 跑單一 profile
# ---------------------------------------------------------------------------

def run_profile(args, profile, reference_rules):
    payload = build_payload(profile, args.model, args.max_tokens)
    if args.print_payload:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return None

    url = args.base_url.rstrip("/") + "/chat/completions"
    server_pid = args.server_pid

    # 跑 + 同時採樣 RSS
    def _do_request():
        return http_json(url, payload, args.timeout)

    if server_pid > 0:
        resp, rss_samples = sample_rss_during(_do_request, server_pid, args.rss_sample_interval)
    else:
        resp = _do_request()
        rss_samples = []

    content = resp["choices"][0]["message"]["content"]
    finish_reason = resp["choices"][0]["finish_reason"]
    timings = resp.get("timings", {})
    predicted_n = timings.get("predicted_n")
    predicted_ms = timings.get("predicted_ms")
    decode_tps = timings.get("predicted_per_second")
    prompt_tps = timings.get("prompt_per_second")
    draft_n = timings.get("draft_n")
    draft_n_accepted = timings.get("draft_n_accepted")
    accept_pct = None
    if isinstance(draft_n, (int, float)) and draft_n:
        accept_pct = (float(draft_n_accepted or 0) / float(draft_n)) * 100.0

    # 過濾 degenerate decode_tps: 1 token + predicted_ms<5ms 是 stop token 立即觸發,
    # 1000/predicted_ms 會膨脹成 1e6 t/s, 不代表真實 decode 速度 → 設 None
    degenerate_decode = False
    if isinstance(decode_tps, (int, float)) and decode_tps is not None:
        if (predicted_n in (0, 1)) or (isinstance(predicted_ms, (int, float)) and predicted_ms < 5.0):
            degenerate_decode = True
            decode_tps = None

    quality_score, quality_checks = evaluate_quality(profile, content, finish_reason, reference_rules)

    return {
        "profile": profile,
        "finish_reason": finish_reason,
        "quality": {
            "score": quality_score,
            "checks": quality_checks,
        },
        "speed": {
            "prefill_tps": round(prompt_tps, 2) if isinstance(prompt_tps, (int, float)) else None,
            "decode_tps": round(decode_tps, 2) if isinstance(decode_tps, (int, float)) else None,
            "decode_tps_degenerate": degenerate_decode,
            "completion_tokens": predicted_n,
            "draft_accept_pct": round(accept_pct, 2) if accept_pct is not None else None,
        },
        "memory": summarize_rss(rss_samples),
        "timings": {
            "prompt_ms": timings.get("prompt_ms"),
            "predicted_ms": timings.get("predicted_ms"),
            "predicted_n": predicted_n,
            "draft_n": draft_n,
            "draft_n_accepted": draft_n_accepted,
        },
        # raw content 放最後,方便人工 debug
        "content": content,
    }


# ---------------------------------------------------------------------------
# Aggregated 摘要 (3 profile 平均 / 中位數)
# ---------------------------------------------------------------------------

def aggregate(profiles_dict):
    """從 3 個 profile 算 speed / memory 的平均 + 中位數 + min/max,供 precommit hook 看趨勢。"""
    speed_decode = [p["speed"]["decode_tps"] for p in profiles_dict.values() if p["speed"]["decode_tps"] is not None]
    speed_prefill = [p["speed"]["prefill_tps"] for p in profiles_dict.values() if p["speed"]["prefill_tps"] is not None]
    quality_scores = [p["quality"]["score"] for p in profiles_dict.values()]
    mem_peaks = [p["memory"]["peak_mb"] for p in profiles_dict.values() if p["memory"]["peak_mb"] is not None]

    def _stats(vals):
        if not vals:
            return {"avg": None, "median": None, "min": None, "max": None, "n": 0}
        s = sorted(vals)
        n = len(s)
        return {
            "avg": round(sum(s) / n, 2),
            "median": round(s[n // 2] if n % 2 == 1 else (s[n // 2 - 1] + s[n // 2]) / 2, 2),
            "min": min(s),
            "max": max(s),
            "n": n,
        }

    return {
        "decode_tps": _stats(speed_decode),
        "prefill_tps": _stats(speed_prefill),
        "quality_score": _stats(quality_scores),
        "peak_rss_mb": _stats(mem_peaks),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(
        description="Replay fixed profile payloads against llama-server + collect quality/speed/memory metrics"
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8080/v1")
    parser.add_argument("--profile", choices=["qa-zh", "longform-zh", "coding"],
                        help="Single profile. Use --all-profiles to run all three.")
    parser.add_argument("--all-profiles", action="store_true",
                        help="Run qa-zh + longform-zh + coding in sequence, output combined JSON.")
    parser.add_argument("--model", default="test")
    parser.add_argument("--max-tokens", type=int, default=0)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--print-payload", action="store_true")
    parser.add_argument("--bench-output", default=None,
                        help="Write structured JSON to this file (used by precommit hook).")
    parser.add_argument("--reference", default=None,
                        help="JSON file with quality check rules per profile.")
    parser.add_argument("--server-pid", type=int, default=0,
                        help="llama-server PID for RSS sampling (0 = skip memory monitoring).")
    parser.add_argument("--rss-sample-interval", type=float, default=0.5,
                        help="RSS sampling interval in seconds during request.")
    parser.add_argument("--schema-version", type=int, default=1)
    parser.add_argument("--commit", default=os.environ.get("CGC_COMMIT", "unknown"),
                        help="Git commit hash (recorded in bench output, default: CGC_COMMIT env or 'unknown').")
    return parser.parse_args()


def main():
    args = parse_args()
    if not args.profile and not args.all_profiles:
        print("error: must specify --profile or --all-profiles", file=sys.stderr)
        return 2

    reference_rules = {}
    if args.reference:
        try:
            with open(args.reference) as f:
                reference_rules = json.load(f)
        except Exception as e:
            print(f"warning: failed to load reference {args.reference}: {e}", file=sys.stderr)
            reference_rules = {}

    profiles = ["qa-zh", "longform-zh", "coding"] if args.all_profiles else [args.profile]
    results = {}
    for p in profiles:
        print(f"[replay] running {p} ...", file=sys.stderr)
        r = run_profile(args, p, reference_rules)
        if r is not None:
            results[p] = r

    if args.all_profiles:
        output = {
            "schema_version": args.schema_version,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "commit": args.commit,
            "server_pid": args.server_pid,
            "base_url": args.base_url,
            "profiles": results,
            "aggregate": aggregate(results),
        }
    else:
        output = results[profiles[0]]

    text = json.dumps(output, ensure_ascii=False, indent=2)
    print(text)

    if args.bench_output:
        try:
            with open(args.bench_output, "w") as f:
                f.write(text)
            print(f"[replay] wrote bench output to {args.bench_output}", file=sys.stderr)
        except Exception as e:
            print(f"error: failed to write bench output: {e}", file=sys.stderr)
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
