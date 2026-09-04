# Scripts 目录说明

本目录包含所有项目相关的 Shell 脚本，按功能分类存放。

## 目录结构

```
scripts/
├── install/      # 安装相关脚本
├── check/        # 检查相关脚本
├── benchmark/    # 基准测试脚本
├── sync/         # 同步相关脚本
├── download/     # 下载相关脚本
├── test/         # 测试相关脚本
├── fix/          # 修复相关脚本
└── misc/         # 其他杂项脚本
```

## 各目录说明

### install/
- `install_vllm.sh` - 安装 vLLM
- `install_transformers.sh` - 安装 Transformers
- `install_pytorch.sh` - 安装 PyTorch
- `install_editable.sh` - 可编辑模式安装
- 以及其他安装脚本...

### check/
- `check_server.sh` - 用固定 `qa-zh` / `longform-zh` replay payload 做 llama-server regression smoke check
- `check_server_profiles.py` - 回归检查主体；要求 `finish_reason=stop` 且无模板边界污染
- `check_vllm.sh` - 检查 vLLM 安装
- `check_torch.sh` - 检查 PyTorch
- 以及其他检查脚本...

### benchmark/
- `run_phi3_moe_benchmark.sh` - 运行 Phi-3 MoE 基准测试
- `full_benchmark.sh` - 完整基准测试
- `benchmark_server_profiles.py` - 对当前 server 线上的固定 `qa-zh` / `longform-zh` replay payload 做多轮 benchmark，并汇总 decode / prompt / acceptance 指标
- `benchmark_server_matrix.py` - 固定 `longform-zh` + replay payload，扫 `MTP_N_MAX / NGL / draft_ngl / batch / ubatch` 的 devserver server matrix，并默认要求 `accept_mean >= 90%`
- `compare_cli_steady_vs_server.py` - 用 historical CLI steady recipe 对拍 `legacy-25plus` longform benchmark 线；QA debug 仍建议单独看 8098
- 以及其他基准测试脚本...

### sync/
- `sync_code.sh` - 同步代码
- `deploy_and_run.sh` - 部署并运行
- 以及其他同步脚本...

### download/
- `download_model.sh` - 下载模型
- `download_vllm.sh` - 下载 vLLM
- 以及其他下载脚本...

### test/
- `test_cgc.sh` - 测试 CGC
- `test_import.sh` - 测试导入
- 以及其他测试脚本...

### fix/
- `fix_numpy彻底.sh` - 修复 NumPy 问题
- 以及其他修复脚本...

### misc/
- `clear_cache.sh` - 清除缓存
- `server_install.sh` - 服务器安装
- 以及其他杂项脚本...

## 使用说明

### Devserver 工作流分线

- `legacy-25plus` 是 longform benchmark 标准线：
  - 用来量 `longform-zh` decode / acceptance / TPOT。
  - 建议固定跑在 `8080`，避免被 QA debug 实验污染。
  - canonical benchmark 入口是 `scripts/check/replay_server_profile.py --profile longform-zh`，因为这条线的健康结果允许 `finish_reason=length`。
- `8098` 是 QA debug 隔离线：
  - 用来做 `qa-zh` 的 prompt / thinking contract / parser A/B。
  - 这条线可以继续用 `check_server.sh` 或 `benchmark_server_profiles.py` 看当前 QA 与 longform payload 的行为，但它不是 longform 发布 benchmark 基线。
- 不要把两条线混成同一口径：
  - longform benchmark 结论以 `legacy-25plus` 为准；
  - QA semantic debug 结论以 `8098` 为准。
- `run_server.sh` 现在有明确的 memory guard：
  - `CGC_SERVER_MEMORY_MODE=dev`：如果当前机器不满足该条 server 的基本可运作记忆体要求，直接拒跑。
  - `CGC_SERVER_MEMORY_MODE=prod`：如果 full-MTP heavy line 不满足要求，先自动切到保命配置（`ngl=8`、`draft_ngl=0`、更小 `ctx/batch/ubatch`）。
  - 即使是 `prod`，如果连保命线也不满足，仍会拒跑。
  - 这条 guard 也会把“已有其他 `llama-server` 在跑”算进去，因此不要在同一台机器上双开两条 full-MTP `legacy-25plus` 实验线。

```bash
# 运行安装脚本
bash scripts/install/install_vllm.sh

# 运行基准测试
bash scripts/benchmark/run_phi3_moe_benchmark.sh

# 启动 longform benchmark 线（建议 8080）
CGC_SERVER_PROFILE=legacy-25plus ./scripts/run_server.sh

# 研发版：不满足基本记忆体要求就直接拒跑
CGC_SERVER_MEMORY_MODE=dev CGC_SERVER_PROFILE=legacy-25plus ./scripts/run_server.sh

# 生产版：若 full-MTP 线不满足要求，先自动降到保命配置再启动
CGC_SERVER_MEMORY_MODE=prod CGC_SERVER_PROFILE=legacy-25plus ./scripts/run_server.sh

# 对 legacy-25plus 做 longform benchmark
python3 scripts/check/replay_server_profile.py --base-url http://127.0.0.1:8080/v1 --profile longform-zh

# 对拍 historical CLI steady vs legacy-25plus longform benchmark
python3 scripts/benchmark/compare_cli_steady_vs_server.py --server-base-url http://127.0.0.1:8080/v1

# 启动 QA debug 隔离线（建议 8098）
CGC_SERVER_PORT=8098 CGC_SERVER_PROFILE=qa-zh ./scripts/run_server.sh

# 运行 QA regression smoke check
bash scripts/check/check_server.sh --base-url http://127.0.0.1:8098/v1

# 运行当前 QA debug 线的固定 replay payload benchmark
python3 scripts/benchmark/benchmark_server_profiles.py --base-url http://127.0.0.1:8098/v1 --iterations 3

# 运行 devserver MTP 参数 sweep matrix
python3 scripts/benchmark/benchmark_server_matrix.py --iterations 1

# 同步代码
bash scripts/sync/sync_code.sh
```
