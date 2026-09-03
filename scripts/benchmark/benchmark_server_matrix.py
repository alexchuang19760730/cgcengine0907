#!/usr/bin/env python3
import argparse
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib import error as urlerror
from urllib import request as urlrequest


DEFAULT_NGL_DRAFT = ["99,0", "99,99", "8,0"]
DEFAULT_BATCH_UBATCH = ["64,32", "128,64"]
DEFAULT_MTP_N_MAX = [2, 3]


def parse_pairs(values, name):
    pairs = []
    for raw in values:
        left, sep, right = raw.partition(",")
        if sep != ",":
            raise ValueError(f"{name} entry must look like A,B (got {raw!r})")
        pairs.append((int(left), int(right)))
    return pairs


def http_get_json(url, timeout):
    with urlrequest.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def wait_for_health(port, timeout_s):
    deadline = time.time() + timeout_s
    last_error = "not started"
    while time.time() < deadline:
        try:
            payload = http_get_json(f"http://127.0.0.1:{port}/health", timeout=2)
            if payload.get("status") == "ok":
                return
            last_error = json.dumps(payload, ensure_ascii=False)
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
        time.sleep(2)
    raise RuntimeError(f"server on port {port} did not become healthy: {last_error}")


def stop_server(port, proc):
    if proc.poll() is None:
        proc.send_signal(signal.SIGINT)
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
    subprocess.run(
        ["pkill", "-INT", "-f", f"llama-server.*--port {port}"],
        check=False,
        capture_output=True,
        text=True,
    )


def start_server(run_server_path, env, cwd):
    log_handle = tempfile.NamedTemporaryFile(
        mode="w+",
        encoding="utf-8",
        prefix="devserver-matrix-",
        suffix=".log",
        delete=False,
    )
    proc = subprocess.Popen(
        [str(run_server_path)],
        cwd=str(cwd),
        env=env,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        text=True,
    )
    return proc, Path(log_handle.name)


def run_benchmark(benchmark_script, base_url, profile, iterations, timeout, cwd):
    cmd = [
        sys.executable,
        str(benchmark_script),
        "--base-url",
        base_url,
        "--profiles",
        profile,
        "--iterations",
        str(iterations),
        "--timeout",
        str(timeout),
        "--json",
    ]
    proc = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, check=True)
    start = proc.stdout.find("{")
    if start < 0:
        raise RuntimeError(f"benchmark script did not emit JSON: {proc.stdout.strip()}")
    return json.loads(proc.stdout[start:])


def format_case(case):
    return (
        f"mtp_n_max={case['mtp_n_max']} "
        f"ngl={case['ngl']} draft_ngl={case['draft_ngl']} "
        f"batch={case['batch']} ubatch={case['ubatch']}"
    )


def parse_args():
    repo_root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(
        description="Run a fixed longform-zh benchmark matrix across devserver MTP knobs"
    )
    parser.add_argument("--profile", default="longform-zh", choices=["longform-zh"])
    parser.add_argument("--iterations", type=int, default=1)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--startup-timeout", type=int, default=180)
    parser.add_argument("--base-port", type=int, default=18200)
    parser.add_argument("--run-server", default=str(repo_root / "scripts" / "run_server.sh"))
    parser.add_argument(
        "--benchmark-script",
        default=str(repo_root / "scripts" / "benchmark" / "benchmark_server_profiles.py"),
    )
    parser.add_argument("--model-root", default="")
    parser.add_argument("--mtp-n-max", nargs="+", type=int, default=DEFAULT_MTP_N_MAX)
    parser.add_argument("--ngl-draft", nargs="+", default=DEFAULT_NGL_DRAFT)
    parser.add_argument("--batch-ubatch", nargs="+", default=DEFAULT_BATCH_UBATCH)
    parser.add_argument("--accept-floor", type=float, default=90.0)
    parser.add_argument("--max-cases", type=int, default=0)
    parser.add_argument("--json-out", default="")
    return parser.parse_args()


def main():
    args = parse_args()
    run_server_path = Path(args.run_server).resolve()
    benchmark_script = Path(args.benchmark_script).resolve()
    server_cwd = run_server_path.parent.parent
    ngl_pairs = parse_pairs(args.ngl_draft, "ngl-draft")
    batch_pairs = parse_pairs(args.batch_ubatch, "batch-ubatch")

    cases = []
    for mtp_n_max in args.mtp_n_max:
        for ngl, draft_ngl in ngl_pairs:
            for batch, ubatch in batch_pairs:
                cases.append(
                    {
                        "mtp_n_max": mtp_n_max,
                        "ngl": ngl,
                        "draft_ngl": draft_ngl,
                        "batch": batch,
                        "ubatch": ubatch,
                    }
                )
    if args.max_cases > 0:
        cases = cases[: args.max_cases]

    results = []
    for idx, case in enumerate(cases):
        port = args.base_port + idx
        env = os.environ.copy()
        env.update(
            {
                "CGC_SERVER_RUNTIME_PROFILE": "mtp",
                "CGC_SERVER_PROFILE": args.profile,
                "CGC_SERVER_HOST": "127.0.0.1",
                "CGC_SERVER_PORT": str(port),
                "CGC_SERVER_REASONING": "off",
                "CGC_SERVER_REASONING_FORMAT": "deepseek",
                "CGC_SERVER_SKIP_CHAT_PARSING": "0",
                "CGC_SERVER_OOM_SAFE": "0",
                "CGC_SERVER_MMV_FUSE": "0",
                "CGC_SERVER_OA_ASYNC": "1",
                "CGC_SERVER_MTP_N_MAX": str(case["mtp_n_max"]),
                "CGC_SERVER_NGL": str(case["ngl"]),
                "CGC_SERVER_DRAFT_NGL": str(case["draft_ngl"]),
                "CGC_SERVER_BATCH": str(case["batch"]),
                "CGC_SERVER_UBATCH": str(case["ubatch"]),
                "N30CACHE_NO_CLEAN": "1",
            }
        )
        if args.model_root:
            env["CGC_SERVER_MODEL_ROOT"] = args.model_root

        proc = None
        launcher_log = None
        started_at = time.time()
        try:
            proc, launcher_log = start_server(run_server_path, env, server_cwd)
            wait_for_health(port, args.startup_timeout)
            bench = run_benchmark(
                benchmark_script,
                f"http://127.0.0.1:{port}/v1",
                args.profile,
                args.iterations,
                args.timeout,
                benchmark_script.parent.parent.parent,
            )
            summary = bench["summary"][0]
            accept_mean = summary.get("draft_accept_pct_mean")
            if accept_mean is None:
                raise RuntimeError("missing draft_accept_pct_mean")
            if float(accept_mean) < float(args.accept_floor):
                raise RuntimeError(
                    f"accept_mean {accept_mean:.2f}% < floor {args.accept_floor:.2f}%"
                )
            result = {
                **case,
                "port": port,
                "elapsed_s": round(time.time() - started_at, 2),
                "launcher_log": str(launcher_log) if launcher_log else "",
                **summary,
            }
            results.append(result)
            print(
                f"[PASS] {format_case(case)} "
                f"decode_mean={summary.get('decode_tps_mean')} "
                f"decode_median={summary.get('decode_tps_median')} "
                f"accept_mean={summary.get('draft_accept_pct_mean')} "
                f"finish={summary.get('finish_reasons')}"
            )
        except Exception as exc:  # noqa: BLE001
            result = {
                **case,
                "port": port,
                "elapsed_s": round(time.time() - started_at, 2),
                "launcher_log": str(launcher_log) if launcher_log else "",
                "error": str(exc),
            }
            results.append(result)
            print(f"[FAIL] {format_case(case)} error={exc}")
        finally:
            if proc is not None:
                stop_server(port, proc)

    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps({"profile": args.profile, "results": results}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    print("")
    print("== matrix summary ==")
    for result in results:
        if "error" in result:
            print(f"{format_case(result)} error={result['error']}")
        else:
            print(
                f"{format_case(result)} "
                f"decode_mean={result.get('decode_tps_mean')} "
                f"decode_median={result.get('decode_tps_median')} "
                f"accept_mean={result.get('draft_accept_pct_mean')} "
                f"finish={result.get('finish_reasons')}"
            )

    return 0 if all("error" not in item for item in results) else 1


if __name__ == "__main__":
    sys.exit(main())
