# Agent Harness Memory

> 由 prime-agent `/refine --global` 自动维护，每次学习循环后更新。

## 系统知识

- CGC engine 使用 MTP (Multi-Token Prediction) 投机解码，目标 25+ tok/s
- Mac M4 端：llama-server 常驻进程 + expert-cache + L4 skip-load
- Windows 端：通过 OpenAI-compatible API 连接 Mac 推理服务
- 模型：Nail-Qwen3.6-35B-A3B-MTP-UD-IQ3_XXS-denseIQ4X.gguf (13GB)

## 命令知识

- `pkill -INT -f llama-server` 停止 Mac 上的推理服务
- `curl -s http://192.168.101.90:8080/health` 检查 Mac 服务状态
- `PYTHONUTF8=1 python script.py` 解决 Windows 中文编码问题

## 调试经验

- Chat template 不对齐会导致模型输出 `</think>` 标签作为 literal text
- MTP draft count 过高 (`--mtp 3`) 在 IQ3_XXS 量化下可能导致重复循环
- Expert cache hit rate 低说明 KV Translation 映射需要校准

---

*上次更新：2026-09-17（初始创建）*
