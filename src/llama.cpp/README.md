# llama.cpp — CGC Engine Branch (Expert Cache Optimization)

![llama](https://raw.githubusercontent.com/ggml-org/llama.brand/refs/heads/master/cover/llama-cpp/cover-llama-cpp-dark.svg)

<div align="center">

<b>MoE Expert Cache 推理优化 — 16GB M4 Max 上 25+ tok/s</b>

[![CGC Expert Cache White Paper](https://img.shields.io/badge/📖-Expert_Cache_White_Paper-blue)](docs/CGC_ExpertCache_WhitePaper.html)
[![Branch: devserver](https://img.shields.io/badge/branch-devserver-orange)](https://github.com/alexchuang19760730/cgcengine0907/tree/devserver)
[![Model: Qwen3.6-35B-A3B](https://img.shields.io/badge/model-Qwen3.6--35B--A3B-green)](https://github.com/alexchuang19760730/cgcengine0907)

**当前生产配置性能（16GB M4 Max, 8GB pool）**

| 指标 | 值 |
|---|---|
| decode_tps（中位数） | **25.87** |
| decode_tps（最佳） | **26.19** |
| draft_accept_pct | ~96% |
| 质量（qa-zh / longform-zh / coding） | 1.0 / 0.882 / 1.0 |

</div>

## 关于本分支

本分支是 [llama.cpp](https://github.com/ggml-org/llama.cpp) 的 CGC Engine 优化分支，专注于 **MoE (Mixture of Experts) 模型的专家缓存与推理加速**，目标是在资源受限的边缘设备（如 16GB M4 Max MacBook）上运行大参数 MoE 模型时，通过智能缓存、预取和计算融合，实现高吞吐推理。

**核心设计理念**：A3B 模型每 token 仅激活 top-8/256 专家（约 3B 参数），但跨 token 的累计 working set 远大于此。Expert Cache 池的大小根据 routing mass 分布动态调整，而非加载全部 35B 参数。

## 核心特性

### 1. L4 Expert Cache Pool（生产）
GPU 端专家权重缓存，按 slot 管理，配合 LRU 淘汰。通过 `CGC_SERVER_EXPERT_CACHE_BYTES` 配置池大小（生产配置：8GB = 8589934592）。

### 2. DBUF — Double Buffer Step-Ahead Refill（生产）
在 per-layer GPU-idle hook 窗口，将当前 step 的 ZERO-mapped cold union 成员加入 prefetch 队列，让 next step 找到它们 resident。
- `CGC_DBUF=1` 启用
- `CGC_DBUF_CAP=24` 队列容量上限

### 3. SPAC — Spatial Activated Estimator（生产）
EMA 效用估计器，per-(layer, expert) EMA utility，预测 next active expert，用于 prefetch 目标选择和 EMA-biased 淘汰。
- `CGC_SPAC=1` 启用
- `CGC_SPAC_ALPHA=0.75` EMA 衰减系数（已调优，细扫描最佳值）
- `CGC_SPAC_K=8` 每次 refresh 预取的 top-K expert 数量
- `CGC_SPAC_REFRESH` 刷新间隔（token 数）

### 4. P0 Down Projection Batch Combine（实验性，默认 OFF）
参考 Perplexity Lily 推理引擎，将 8 个 per-expert down GEMV + 7 次 add 合并为 1 个 kernel，减少 kernel launch 开销。
- `CGC_DOWN_COMBINE=1` 启用（**默认 OFF**，未设置时与基线 byte-identical）
- 仅支持 decode（n_tokens==1）、Q3_K down weights
- **测试结果**：23.29 t/s 中位数 / 25.09 最佳（vs baseline 25.87），质量 3/3 = 1.0

### 5. MTP Speculative Decoding（生产）
Multi-Token Prediction 投机解码，draft_accept ~96%，显著提升有效吞吐。
- **注意**：MTP 生产配置必须设置 `CGC_NO_PREFETCH=1`（0000 防护），因为 MTP+OA_ASYNC + 背景 pool fill 会导致 mid-decode 崩溃。

## 快速开始（devserver 分支）

本仓库是 **flashkv-devserver** 项目的子模块（`src/llama.cpp/`），所有操作应在项目根目录 `flashkv-devserver/` 下进行。

### 目录结构

```
flashkv-devserver/
├── src/llama.cpp/          # 本仓库（llama.cpp CGC Engine 分支）
│   └── build/bin/llama-server  # 构建产物
├── scripts/
│   ├── run_server.sh        # 生产 server 启动脚本
│   └── check/
│       └── replay_server_profile.py  # 基准测试脚本
└── models/gguf/
    └── Nail-Qwen3.6-35B-A3B-MTP-UD-IQ3_XXS.gguf  # 默认 MTP 模型
```

### 构建

```bash
cd flashkv-devserver/src/llama.cpp
cmake -B build -DGGML_METAL=ON -DCMAKE_BUILD_TYPE=Release
cmake --build build --target llama-server -j8
```

### 生产配置启动（MTP + Expert Cache）

在项目根目录 `flashkv-devserver/` 下运行：

```bash
# 默认 MTP 生产配置（8GB pool + DBUF + SPAC α=0.75）
CGC_SERVER_EXPERT_CACHE_BYTES=8589934592 \
CGC_DBUF=1 \
CGC_SPAC=1 \
CGC_SPAC_ALPHA=0.75 \
CGC_NO_PREFETCH=1 \
./scripts/run_server.sh
```

**注意**：`run_server.sh` 使用 `env "${SERVER_ENV[@]}" "$BIN"` 启动，**不继承**外部 shell 环境变量。每个 `CGC_*` 变量必须显式添加到脚本内的 `SERVER_ENV` 数组才能传递给 server 进程。

### 运行时 Profile 切换

`run_server.sh` 支持多种预配置运行时：

```bash
# 非 MTP 基线（~8 t/s，用于对比）
CGC_SERVER_RUNTIME_PROFILE=non-mtp ./scripts/run_server.sh

# 明确切回 MTP 生产配置
CGC_SERVER_RUNTIME_PROFILE=mtp ./scripts/run_server.sh

# 16GB 机器 OOM 保命模式
CGC_SERVER_OOM_SAFE=1 ./scripts/run_server.sh

# 换端口
CGC_SERVER_PORT=9931 ./scripts/run_server.sh

# 外挂模型目录
CGC_SERVER_MODEL_ROOT=/path/to/models/gguf ./scripts/run_server.sh
```

### 启用 P0 Down-Combine（实验性，默认 OFF）

```bash
CGC_SERVER_EXPERT_CACHE_BYTES=8589934592 \
CGC_DBUF=1 \
CGC_SPAC=1 \
CGC_SPAC_ALPHA=0.75 \
CGC_NO_PREFETCH=1 \
CGC_DOWN_COMBINE=1 \
./scripts/run_server.sh
```

### 运行基准测试（3 Profile 质量 + 速度评估）

```bash
cd flashkv-devserver

# Coding profile（代码生成，质量最敏感）
python3 scripts/check/replay_server_profile.py \
  --profile coding \
  --base-url http://127.0.0.1:8080/v1 \
  --model test \
  --max-tokens 512 \
  --seed 42 \
  --runs 3 \
  --warmup

# 中文问答 profile
python3 scripts/check/replay_server_profile.py --profile qa-zh ...

# 中文长文 profile
python3 scripts/check/replay_server_profile.py --profile longform-zh ...
```

## 质量与速度测试方法

### 3 Profile 质量评估

| Profile | Baseline 质量 | 内容类型 |
|---|---|---|
| `qa-zh` | 1.0 | 中文问答（事实性、准确性、完整性） |
| `longform-zh` | 0.882 | 中文长文生成（连贯性、结构、深度） |
| `coding` | 1.0 | 代码生成（语法正确性、逻辑正确性、可运行性） |

**质量判定标准**：任何优化必须保持 3 个 profile 的质量不低于 baseline。

### 严格 A/B 测试协议

为排除机器状态干扰，验证优化效果时使用严格的 A/B 交替测试协议：
1. 相同 prompt、相同 max_tokens（如 coding 固定 512 tokens）
2. 交替执行（A = 无优化, B = 有优化, A, B...）
3. 固定 Seed=42
4. 对比 steady decode tps（去掉前 50 tokens 的 warmup）
5. 每次测试前 kill 残留 llama-server，确保干净启动
6. 关闭其他应用，释放内存，减少机器波动

### 速度指标定义

| 指标 | 定义 | 目标 |
|---|---|---|
| `decode_tps` | decode 阶段 tokens/second（去掉 prefill） | ≥ 25.0 |
| `draft_accept_pct` | MTP speculative decoding 的 draft token 接受率 | ≥ 90% |
| `completion_tokens` | 实际生成的 token 数 | = max-tokens（无提前停止） |
| `decode_tps_degenerate` | 是否为退化输出（循环/重复） | 必须为 False |

## 已弃用特性（不要使用）

| Option | 弃用原因 |
|---|---|
| `CGC_DBUF2` | 完整双缓冲 active/scratch 指针原子切换，6GB/8GB 反而慢 40-48% |
| `CGC_FAST_WAIT` | 等待 in-flight fill 而非 ZERO-mapping，decode 慢 29-48% |
| `CGC_MMV_FUSE` | GLU 融合（gate+up+GLU），平均 15.94 t/s（vs 基线 22.07），draft accept 暴跌 |
| `CGC_RN_ROUTING` | Renorm routing（排除非 resident expert），质量 0.3 counting loop，NOOP bisect 确认 exclusion 是杀手 |
| `CGC_WCOLD_EN` | Warm cold expert 处理，与 renorm 配合使用，已随 renorm 弃用 |

## 文档

- 📖 **[CGC Expert Cache 白皮书](docs/CGC_ExpertCache_WhitePaper.html)** — 完整架构概述、文件清单、所有 CGC_* option 详解、测试方法、性能里程碑、P0 深度剖析、未来方向
- 🔧 **[Decode 版本里程碑](docs/CGC_Step23_RenormRouting_AB_2026-09-06.html)** — 24 小时内关键版本的速度/质量/稳定性里程碑说明

## 未来方向

### 短期（1-2 周）
- P0 Down-Combine 并行度优化（当前 513 threadgroups vs 原始 4104），目标稳定 25+ t/s
- P1：MoE routing 移到 GPU，减少 CPU-GPU 同步（参考 Lily），预期 +5-15%
- P3：批量同步优化，减少 kernel launch 之间的 barrier 开销

### 中期（1-2 月）
- 鸿蒙系统移植：所有 CGC_* option 设计为可配置，便于跨平台迁移
- 32GB 硬件验证：更大 pool（128-192 slots/layer）下的质量-速度平衡点
- Gated DeltaNet 层 Apple GPU 专项优化（如果模型包含），预期 +10-20%

### 长期
- 多模型 MoT (Model-on-Transfer) 端云协同：云端 DeepSeek V4 Flash 负责 Prefill+Speculative Draft，端侧 Qwen3.6-35B-A3B 仅执行 Verify
- KV Translation 跨模型张量对齐技术
- CGC Engine 商业化：仓储 AGV 与具身智能场景的边缘推理部署

---

# 原始 llama.cpp 文档

以下为上游 llama.cpp 原始文档，保留供参考。

## Quick start

A few options to get `llama.cpp` installed on your machine:

- Visit https://llama.app and follow the instructions
- Run with Docker - see our [Docker documentation](docs/docker.md)
- Download pre-built binaries from the [releases page](https://github.com/ggml-org/llama.cpp/releases)
- Build from source by cloning this repository - check out [our build guide](docs/build.md)

Once installed:

```sh
# Download and run a model directly from Hugging Face
llama cli -hf ggml-org/Qwen3.5-0.8B-GGUF

# Launch OpenAI-compatible API server
llama serve -hf ggml-org/Qwen3.5-0.8B-GGUF
```

<table align="center">
    <tr>
        <td align="center" width=50%>
            <img width="1310" height="888" alt="VLM session with `llama cli`" src="https://github.com/user-attachments/assets/88726b48-1713-48aa-a525-95a02e78afc4" />
            <i>VLM session with <b>llama cli</b></i>
        </td>
        <td align="center">
            <img width="1392" height="958" alt="Built-in web UI against `llama serve` running Qwen 3.6" src="https://github.com/user-attachments/assets/b402f972-2e32-4def-8771-8d849f08cf2e" />
            <i>Built-in web UI against <b>llama serve</b></i>
        </td>
    </tr>
<table>

## Description

The main goal of `llama.cpp` is to enable LLM (and VLM) inference with minimal setup and state-of-the-art performance on
a wide range of hardware - locally and in the cloud.

- Plain C/C++ implementation without any dependencies
- Apple silicon is a first-class citizen - optimized via ARM NEON, Accelerate and Metal frameworks
- AVX, AVX2, AVX512 and AMX support for x86 architectures
- RVV, ZVFH, ZFH, ZICBOP and ZIHINTPAUSE support for RISC-V architectures
- 1.5-bit, 2-bit, 3-bit, 4-bit, 5-bit, 6-bit, and 8-bit integer quantization for faster inference and reduced memory use
- Custom CUDA kernels for running LLMs on NVIDIA GPUs (support for AMD GPUs via HIP and Moore Threads GPUs via MUSA)
- Vulkan and SYCL backend support
- CPU+GPU hybrid inference to partially accelerate models larger than the total VRAM capacity

The `llama.cpp` project is build on top of the [ggml](https://github.com/ggml-org/ggml) library.

## Supported backends

| Backend | Target devices |
| --- | --- |
| [BLAS](docs/build.md#blas-build) | All |
| [BLIS](docs/backend/BLIS.md) | All |
| [CANN](docs/build.md#cann) | Ascend NPU |
| [CUDA](docs/build.md#cuda) | Nvidia GPU |
| [HIP](docs/build.md#hip) | AMD GPU |
| [Hexagon [In Progress]](docs/backend/snapdragon/README.md) | Snapdragon |
| [IBM zDNN](docs/backend/zDNN.md) | IBM Z & LinuxONE |
| [MUSA](docs/build.md#musa) | Moore Threads GPU |
| [Metal](docs/build.md#metal-build) | Apple Silicon |
| [OpenCL](docs/backend/OPENCL.md) | Adreno GPU |
| [OpenVINO [In Progress]](docs/backend/OPENVINO.md) | Intel CPUs, GPUs, and NPUs |
| [RPC](https://github.com/ggml-org/llama.cpp/tree/master/tools/rpc) | All |
| [SYCL](docs/backend/SYCL.md) | Intel GPU |
| [VirtGPU](docs/backend/VirtGPU.md) | VirtGPU APIR |
| [Vulkan](docs/build.md#vulkan) | GPU |
| [WebGPU](docs/build.md#webgpu) | All |
| [ZenDNN](docs/build.md#zendnn) | AMD CPU |

## Documentation

#### Tools

- [cli](tools/cli/README.md)
- [completion](tools/completion/README.md)
- [server](tools/server/README.md)
- [GBNF grammars](grammars/README.md)

#### Development

- [How to build](docs/build.md)
- [Running on Docker](docs/docker.md)
- [Build on Android](docs/android.md)
- [Multi-GPU usage](docs/multi-gpu.md)
- [Performance troubleshooting](docs/development/token_generation_performance_tips.md)
- [GGML tips & tricks](https://github.com/ggml-org/llama.cpp/wiki/GGML-Tips-&-Tricks)
- [XCFramework](docs/xcframework.md)
- [Completions](docs/completions.md)
- [Models](docs/models.md)

## Contributing

- Contributors can open PRs
- Collaborators will be invited based on contributions
- Maintainers can push to branches in the `llama.cpp` repo and merge PRs into the `master` branch
- Any help with managing issues, PRs and projects is very appreciated!
- Read the [CONTRIBUTING.md](CONTRIBUTING.md) for more information

## Acknowledgements

- [yhirose/cpp-httplib](https://github.com/yhirose/cpp-httplib) - Single-header HTTP server, used by `llama-server` - MIT license
- [stb-image](https://github.com/nothings/stb) - Single-header image format decoder, used by multimodal subsystem - Public domain
- [nlohmann/json](https://github.com/nlohmann/json) - Single-header JSON library, used by various tools/examples - MIT License
- [miniaudio.h](https://github.com/mackron/miniaudio) - Single-header audio format decoder, used by multimodal subsystem - Public domain
- [subprocess.h](https://github.com/sheredom/subprocess.h) - Single-header process launching solution for C and C++ - Public domain
