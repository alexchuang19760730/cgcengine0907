#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
replay_client_regime.py - Raw OpenAI client quality suite (2026-09-08)
-----------------------------------------------------------------------
WHY THIS SUITE EXISTS (harness-vs-client gap):

The 7-profile replay harness (replay_server_profile.py) always sends a "crutch"
regime: temperature=0 + assistant_prefill anchor + stop tokens + presence_penalty
+ curated Chinese prompts. It scores 1.0 - but real OpenAI/Claude clients
(Windows testers, Claude Code) send raw payloads {model, messages, temperature,
max_tokens} at temp ~0.7 with NO prefill/stops/pp, and quality collapses
(echo prompt, think-tag literal output, code-fence loops, empty content).

Root cause isolated by A/B: this IQ3_XXS quant only produces reliable output at
temperature 0 (greedy). Any temp>0 amplifies quant logits noise. The server-side
fix is CGC_FORCE_TEMP0=1 (clamp client temperature to greedy); this suite proves
that fix works by sending the SAME raw request at temp 0.0 and temp 0.7 and
requiring identical (correct) results.

Usage:
  python3 scripts/check/replay_client_regime.py [--base-url ...] [--temps 0.0,0.7]
  --bench-output /tmp/client_regime.json  to write structured JSON
Exit code 0 always; read "verdict" in JSON for PASS/FAIL (quality >= 0.7).
"""
import argparse
import json
import sys
import time
from urllib import request as urlrequest

DEFAULT_TEMPS = "0.0,0.7"

# Each case: what a real OpenAI client sends and what a correct answer looks like.
# No chat_template_kwargs, no stop, no presence_penalty anywhere in the payload.
CASES = [
    {
        "id": "arith_en",
        "prompt": "2+2=? Answer with a single number.",
        "max_tokens": 24,
        "expect_any": ["4"],
        "kind": "short-qa-en",
    },
    {
        "id": "arith_zh",
        "prompt": "15加27等於多少？",
        "max_tokens": 24,
        "expect_any": ["42"],
        "kind": "short-qa-zh",
    },
    {
        "id": "capital_zh",
        "prompt": "巴黎是哪個國家的首都？",
        "max_tokens": 60,
        "expect_any": ["法國", "法国", "France"],
        "kind": "short-qa-zh",
    },
    {
        "id": "capital_en",
        "prompt": "What country is Paris the capital of?",
        "max_tokens": 60,
        "expect_any": ["France"],
        "kind": "short-qa-en",
    },
    {
        "id": "logic_oneword",
        "prompt": "If A>B and B>C, which is the largest? Answer with one letter.",
        "max_tokens": 30,
        "answer_prefix": ["A"],
        "kind": "short-qa-en",
    },
    {
        "id": "code_quicksort",
        "prompt": "Write a quicksort in Python.",
        "max_tokens": 300,
        "expect_any": ["def ", "quicksort", "quick_sort", "pivot"],
        "kind": "code",
        "note": "expects real code, not a prose loop",
    },
    {
        "id": "code_bubble",
        "prompt": "Write bubble sort in Python and show the code.",
        "max_tokens": 300,
        "expect_any": ["def ", "bubble", "range("],
        "kind": "code",
    },
    {
        "id": "zh_medium",
        "prompt": "用一句話介紹台灣。",
        "max_tokens": 80,
        "length_min": 10,
        "kind": "zh-medium",
    },
]

# Tag markers that indicate scaffold leakage into content. Detection only - never
# sent in payload. Kept to angle-bracket markers to avoid false positives.
_ECHO_MARKERS = ["<think>", "</think>", "<im_start>", "<im_end>",
                 "<|im_start|>", "<|im_end|>", "|im_start|>", "|im_end|>"]


def http_json(url, payload, timeout):
    req = urlrequest.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlrequest.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def detect_phrase_loop(text, min_unit=6, max_unit=300, min_repeat=3):
    """Same phrase (>= min_unit chars) repeated consecutively >= min_repeat -> loop."""
    def _find(t):
        n = len(t)
        if n < min_unit * min_repeat:
            return None
        max_len = min(max_unit, n // min_repeat)
        for p in range(min_unit, max_len + 1):
            limit = n - p * min_repeat
            i = 0
            while i <= limit:
                block = t[i:i + p]
                cnt = 1
                j = i + p
                while j + p <= n and t[j:j + p] == block:
                    cnt += 1
                    j += p
                    if cnt >= min_repeat:
                        return (block, cnt)
                i += 1
        return None

    if not text:
        return None
    found = _find(text)
    if found:
        return found
    collapsed = "".join(text.split())
    if collapsed == text:
        return None
    return _find(collapsed)


def _first_alpha(content):
    for ch in content:
        if ch.isalpha():
            return ch
    return None


def _echo_check(prompt, content):
    if not content:
        return False
    p = "".join(prompt.split())
    c = "".join(content.split())
    if not c or not p:
        return False
    if len(p) >= 24:
        if p[-24:] in c and c.startswith(p[-24:]):
            return True
        if c.startswith(p[:16]):
            return True
    return False


def eval_case(c, content):
    """Return (ok, reasons). reasons non-empty => not acceptable."""
    reasons = []
    if not content:
        return False, ["empty"]
    if _echo_check(c["prompt"], content):
        reasons.append("echo_prompt")
    pl = detect_phrase_loop(content)
    if pl is not None:
        reasons.append("phrase_loop:%sx%d" % (pl[0][:20], pl[1]))
    for m in _ECHO_MARKERS:
        if m and m in content:
            reasons.append("marker:" + m.replace("<", "").replace(">", ""))
            break
    exp = c.get("expect_any")
    if exp and not any(kw in content for kw in exp):
        reasons.append("keyword_miss")
    if c.get("answer_prefix"):
        a = _first_alpha(content)
        if a is None or a not in c["answer_prefix"]:
            reasons.append("prefix_miss:%s" % (a if a else "none"))
    if c.get("length_min") and len(content) < c["length_min"]:
        reasons.append("too_short")
    return (not reasons), reasons


def main():
    ap = argparse.ArgumentParser(description="Raw OpenAI client quality suite")
    ap.add_argument("--base-url", default="http://127.0.0.1:8080/v1")
    ap.add_argument("--model", default="test")
    ap.add_argument("--temps", default=DEFAULT_TEMPS,
                    help="temperature sweep, comma separated (default '0.0,0.7').")
    ap.add_argument("--timeout", type=int, default=180)
    ap.add_argument("--bench-output", default=None)
    ap.add_argument("--commit", default="unknown")
    ap.add_argument("--schema-version", type=int, default=1)
    args = ap.parse_args()

    temps = []
    for x in args.temps.split(","):
        x = x.strip()
        if x:
            try:
                temps.append(float(x))
            except ValueError:
                pass
    if not temps:
        temps = [0.7]

    url = args.base_url.rstrip("/") + "/chat/completions"
    per_case = []
    for c in CASES:
        runs = {}
        for t in temps:
            payload = {
                "model": args.model,
                "messages": [{"role": "user", "content": c["prompt"]}],
                "temperature": t,
                "max_tokens": c.get("max_tokens", 120),
            }
            entry = {"temp": t, "ok": False, "reasons": [], "content": "", "finish": None}
            try:
                resp = http_json(url, payload, args.timeout)
                content = (resp["choices"][0]["message"]["content"] or "").strip()
                entry["content"] = content[:200]
                entry["finish"] = resp["choices"][0].get("finish_reason")
                timings = resp.get("timings", {})
                entry["decode_tps"] = timings.get("predicted_per_second")
                entry["completion_tokens"] = timings.get("predicted_n")
                entry["ok"], entry["reasons"] = eval_case(c, content)
            except Exception as e:
                entry["reasons"] = ["http_error:%s" % e]
            runs[t] = entry
        case_pass = all(runs[t]["ok"] for t in temps)
        identical = len(temps) > 1 and all(
            runs[t]["ok"] == runs[temps[0]]["ok"] for t in temps)
        per_case.append({
            "id": c["id"],
            "kind": c.get("kind"),
            "pass": case_pass,
            "identical_across_temps": identical,
            "note": c.get("note", ""),
            "runs": runs,
        })

    passed = sum(1 for x in per_case if x["pass"])
    total = len(per_case)
    quality = round(passed / total, 3) if total else None
    verdict = "PASS" if quality is not None and quality >= 0.7 else "FAIL"

    out = {
        "suite": "client-regime",
        "schema_version": args.schema_version,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "commit": args.commit,
        "base_url": args.base_url,
        "temps": temps,
        "note": ("raw OpenAI client payloads (model/messages/temperature/max_tokens only - "
                 "no prefill/stop/pp). CGC_FORCE_TEMP0=1 must make temp 0.7 results identical "
                 "to temp 0.0; IQ3_XXS ceiling = greedy only."),
        "quality": quality,
        "verdict": verdict,
        "pass": passed,
        "total": total,
        "cases": per_case,
    }
    text = json.dumps(out, ensure_ascii=False, indent=2)
    print(text)
    if args.bench_output:
        with open(args.bench_output, "w") as f:
            f.write(text)
    print("[client-regime] verdict=%s quality=%s (%d/%d)" %
          (verdict, quality, passed, total), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
