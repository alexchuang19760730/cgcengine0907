#!/usr/bin/env python3
import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from urllib import request as urlrequest


LONG_SENTS = [
    "The coastal observatory recorded steady winds from the northwest throughout the morning, and the tide charts suggested a calm crossing for the research vessel. ",
    "Historical records indicate that the old lighthouse was rebuilt three times after storms damaged its foundations beyond repair. ",
    "A team of engineers inspected the railway bridge, noting the corrosion on the lower girders and scheduling reinforcement work for the coming season. ",
    "The museum's new exhibition traces the development of printing from wooden blocks to movable type and finally to industrial presses. ",
    "Farmers in the valley reported an unusually abundant harvest, with the grain stores filling earlier than they had in a decade. ",
    "The orchestra opened with a slow movement, and the woodwinds carried the melody while the strings provided a steady harmonic foundation. ",
    "Geologists mapped the ancient riverbed, discovering fossilized shells that suggested the region was once covered by a shallow sea. ",
    "The committee reviewed the proposal for the new library wing, debating the allocation of funds between reading rooms and digital archives. ",
]


def parse_args():
    repo_root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(
        description="Compare the historical 25.706 CLI steady recipe with the current llama-server replay benchmark."
    )
    parser.add_argument(
        "--binary",
        default=str(repo_root / "src" / "llama.cpp" / "build" / "bin" / "llama-speculative-simple"),
    )
    parser.add_argument(
        "--model",
        default="/Users/alexchuang/Documents/flashkv0516/models/gguf/Nail-Qwen3.6-35B-A3B-MTP-UD-IQ3_XXS-denseIQ4X.gguf",
    )
    parser.add_argument("--server-base-url", default="http://127.0.0.1:8098/v1")
    parser.add_argument("--server-profiles", nargs="+", default=["qa-zh", "longform-zh"])
    parser.add_argument("--server-iterations", type=int, default=1)
    parser.add_argument("--server-timeout", type=int, default=180)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--n-predict", type=int, default=1100)
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def build_long_prompt():
    prompt = ""
    idx = 0
    while len(prompt) < 7000:
        prompt += LONG_SENTS[idx % len(LONG_SENTS)]
        idx += 1
    return prompt


def http_get_json(url, timeout):
    with urlrequest.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def ensure_server_is_healthy(base_url):
    health_url = base_url.rstrip("/")
    if health_url.endswith("/v1"):
        health_url = health_url[:-3]
    payload = http_get_json(health_url + "/health", timeout=5)
    if payload.get("status") != "ok":
        raise RuntimeError(f"server unhealthy: {payload}")


def run_server_benchmark(args):
    bench_script = Path(__file__).with_name("benchmark_server_profiles.py")
    cmd = [
        sys.executable,
        str(bench_script),
        "--base-url",
        args.server_base_url,
        "--profiles",
        *args.server_profiles,
        "--iterations",
        str(args.server_iterations),
        "--timeout",
        str(args.server_timeout),
        "--json",
    ]
    proc = subprocess.run(cmd, check=True, capture_output=True, text=True)
    start = proc.stdout.find("{")
    if start < 0:
        raise RuntimeError(f"server benchmark did not emit JSON: {proc.stdout.strip()}")
    return json.loads(proc.stdout[start:])


def parse_last_float(pattern, text):
    matches = re.findall(pattern, text, re.MULTILINE)
    return float(matches[-1]) if matches else None


def parse_last_int(pattern, text):
    matches = re.findall(pattern, text, re.MULTILINE)
    return int(matches[-1]) if matches else None


def run_cli_steady(args):
    prompt = build_long_prompt()
    cmd = [
        args.binary,
        "-m",
        args.model,
        "-n",
        str(args.n_predict),
        "-ngl",
        "99",
        "--no-mmap",
        "-t",
        "8",
        "-s",
        str(args.seed),
        "--ignore-eos",
        "--spec-type",
        "draft-mtp",
        "--spec-draft-n-max",
        "2",
        "-c",
        "3072",
        "-expert-cache",
        "4294967296",
        "--temp",
        "0",
        "-p",
        prompt,
    ]
    env = {
        **dict(os.environ),
        "LLAMA_EXPERT_CACHE_ALLOW_NGL": "1",
        "LLAMA_EXPERT_CACHE_L4_SKIP_LAYER0": "1",
        "LLAMA_EXPERT_CACHE_WORKERS": "8",
        "CGC_WAKE_POLL_US": "15",
        "CGC_PREFETCH_SRC": "hist",
        "CGC_EVICTED_RING": "0",
        "CGC_OA_ASYNC": "1",
        "CGC_N_CB": "8",
        "CGC_GLU_FUSED_DOWN": "1",
        "LLAMA_EXPERT_CACHE_LAYER_CAPS": "40-40:256",
        "CGC_DECODEHIT": "1",
        "CGC_WATCHDOG": "1",
        "CGC_NO_PREFETCH": "1",
        "CGC_VERIFY_DECODE": "1",
        "CGC_DRAFT_DECODE": "1",
        "CGC_WARM_NPAST": "0",
    }
    proc = subprocess.run(cmd, check=True, capture_output=True, text=True, env=env)
    merged = proc.stdout + "\n" + proc.stderr
    return {
        "command": " ".join(cmd),
        "decode_tps": parse_last_float(r"decoded\s+[0-9]+\s+tokens in\s+[0-9.]+\s+seconds,\s+speed:\s*([0-9.]+)\s+t/s", merged),
        "decoded_tokens": parse_last_int(r"decoded\s+([0-9]+)\s+tokens", merged),
        "accept_pct": parse_last_float(r"accept\s*=\s*([0-9.]+)%", merged),
        "n_drafted": parse_last_int(r"n_drafted\s*=\s*([0-9]+)", merged),
        "n_accept": parse_last_int(r"n_accept\s*=\s*([0-9]+)", merged),
        "hit_pct": parse_last_float(r"hit rate\s*([0-9.]+)%", merged),
        "prompt_tps": parse_last_float(r"prompt eval time =\s*[0-9.]+\s*ms\s*/\s*[0-9]+\s*tokens\s*\(\s*[0-9.]+\s*ms per token,\s*([0-9.]+)\s*tokens per second\)", merged),
    }


def main():
    args = parse_args()
    ensure_server_is_healthy(args.server_base_url)
    cli = run_cli_steady(args)
    server = run_server_benchmark(args)

    payload = {
        "cli_steady": cli,
        "server_replay": server,
    }

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    print("== CLI steady (historical 25.706 recipe) ==")
    print(f"decode_tps     : {cli['decode_tps']}")
    print(f"decoded_tokens : {cli['decoded_tokens']}")
    print(f"accept_pct     : {cli['accept_pct']}")
    print(f"hit_pct        : {cli['hit_pct']}")
    print(f"prompt_tps     : {cli['prompt_tps']}")
    print("")
    print("== Current server replay ==")
    for item in server["summary"]:
        print(
            f"{item['profile']}: "
            f"decode_tps_mean={item['decode_tps_mean']} "
            f"prompt_tps_mean={item['prompt_tps_mean']} "
            f"completion_tokens_mean={item['completion_tokens_mean']} "
            f"draft_accept_pct_mean={item['draft_accept_pct_mean']}"
        )
    print("")
    print("== Notes ==")
    print("CLI steady uses the historical long-prompt decode-heavy recipe.")
    print("Server replay uses the current qa-zh / longform-zh fixed payload benchmark.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
