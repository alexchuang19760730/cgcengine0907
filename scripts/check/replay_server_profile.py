#!/usr/bin/env python3
import argparse
import json
import sys
from urllib import request as urlrequest


LONGFORM_STOP = ["<|end|>", "<|output|>", "<|user|>"]


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


def build_payload(profile, model, max_tokens):
    if profile == "qa-zh":
        return {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": "巴黎是哪個國家的首都？請只用一句中文回答。",
                }
            ],
            "temperature": 0,
            "max_tokens": max_tokens or 24,
            # 2026-09-05 CGC fix v5: prefill 包含完整 think scaffold + 答案開頭,讓 model
            # 看到「答:<think>\n\n</think>\n\n」這個已知 token 序列,直接從 prefill 接續給答案。
            # v4 失敗原因: disable_think_scaffold flag 在 chat.cpp 注入 THINK_SEED 在 prefill 之後
            # → model 看到「答:巴黎<think>\n\n</think>\n\n」沉默 emit \n。
            # v5 解法: prefill 內含完整「答:巴黎<think>\n\n</think>\n\n」,model 看到熟悉的 pattern
            # (因為 prefill 等同 model 自己要生成的序列),直接 emit 答案延伸。
            # 2026-09-05 CGC fix v6: prefill 給完整答案 + 句號,讓模型從「句號 + 下一句延伸」接續。
            # 失敗根因: prefill 結尾不管是「就在」「最大」「首都」,MTP draft N-gram 都 match 高頻結尾
            # (「。」「,是」等) → MTP 模式 1-2 token stop。
            # non-MTP 模式 prefill 結尾「盧浮宮就在」model 不知道接什麼,emit 高頻 token `0` 48 次。
            # 唯一穩定解法: prefill 結尾必須是「句號」+「有意義的延伸開頭」,讓模型從延伸開頭接續。
            # 但 qa-zh 短答不適合延伸,索性不強求 qa-zh 在 prefill 路徑可用,改用更簡單的 system prompt 注入。
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
                    # 2026-09-05 CGC fix v4: 加 system prompt 引導 longform 寫長文(對齊 GGUF embedded
                    # ChatML 風格,讓 model 知道要連續陳述多個事實,不要 emit 句號就停)。
                    "content": "Answer directly, after thinking. Lead with the answer, then only what it needs to be correct and usable. Keep the final answer lean. Use plain prose. Never drop correctness.",
                },
                {
                    "role": "user",
                    "content": "為什麼巴黎會成為法國的政治與文化中心？請用一段中文說明。",
                }
            ],
            "temperature": 0,
            "max_tokens": max_tokens or 220,
            # 2026-09-05 CGC fix v4: prefill 給「未完成具體事實」,避免 model 進「因為」loop。
            # 之前測「主要是因為」會讓 model 重複「因為」(220 token 全是「因為」)。
            # 改用「從12世紀起就是法國王國的首都,並且匯聚了」期待 model 列舉盧浮宮、艾菲爾鐵塔等。
            "chat_template_kwargs": {
                "assistant_prefill": "答:巴黎之所以成為法國的政治與文化中心,主要是因為它從12世紀起就是法國王國的首都,並且匯聚了",
            },
            "stop": LONGFORM_STOP,
        }

    if profile == "coding":
        return {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": "寫一個 Python function，計算費氏數列第 n 項。",
                }
            ],
            "temperature": 0,
            "max_tokens": max_tokens or 512,
            # 2026-09-05 CGC fix v4: coding prefill 給「```python\n# 」開頭,讓 model 從 code block 接續。
            "chat_template_kwargs": {
                "assistant_prefill": "```python\n",
            },
            "stop": ["```", "<|end|>", "<|output|>", "<|user|>"],
        }

    raise ValueError(f"unsupported profile: {profile}")


def parse_args():
    parser = argparse.ArgumentParser(description="Replay fixed profile payloads against llama-server")
    parser.add_argument("--base-url", default="http://127.0.0.1:8080/v1")
    parser.add_argument("--profile", choices=["qa-zh", "longform-zh", "coding"], required=True)
    parser.add_argument("--model", default="test")
    parser.add_argument("--max-tokens", type=int, default=0)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--print-payload", action="store_true")
    return parser.parse_args()


def main():
    args = parse_args()
    payload = build_payload(args.profile, args.model, args.max_tokens)
    if args.print_payload:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    url = args.base_url.rstrip("/") + "/chat/completions"
    resp = http_json(url, payload, args.timeout)

    content = resp["choices"][0]["message"]["content"]
    finish_reason = resp["choices"][0]["finish_reason"]
    timings = resp.get("timings", {})
    predicted_n = timings.get("predicted_n")
    decode_tps = timings.get("predicted_per_second")
    prompt_tps = timings.get("prompt_per_second")
    draft_n = timings.get("draft_n")
    draft_n_accepted = timings.get("draft_n_accepted")
    accept_pct = None
    if isinstance(draft_n, (int, float)) and draft_n:
        accept_pct = (float(draft_n_accepted or 0) / float(draft_n)) * 100.0

    print(json.dumps(
        {
            "profile": args.profile,
            "finish_reason": finish_reason,
            "decode_tps": decode_tps,
            "prompt_tps": prompt_tps,
            "completion_tokens": predicted_n,
            "draft_accept_pct": accept_pct,
            "content": content,
            "timings": {
                "prompt_ms": timings.get("prompt_ms"),
                "predicted_ms": timings.get("predicted_ms"),
                "predicted_per_second": decode_tps,
                "prompt_per_second": prompt_tps,
                "predicted_n": predicted_n,
                "draft_n": draft_n,
                "draft_n_accepted": draft_n_accepted,
            },
        },
        ensure_ascii=False,
        indent=2,
    ))
    return 0


if __name__ == "__main__":
    sys.exit(main())
