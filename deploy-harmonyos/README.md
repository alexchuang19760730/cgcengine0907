# CGC Engine 跨平台部署包

**Version:** v1.0 | **Date:** 2026-09-07 | **基于:** Qwen3.6-35B-A3B MoE Expert Cache

支持平台：**macOS** (Metal) / **Windows** (CUDA/Vulkan) / **Linux** (CUDA/ROCm/Vulkan) / **HarmonyOS** (Vulkan/CPU)

## 快速开始

### macOS (M4 Max, 已验证)
```bash
# 构建 + 同步最新 macOS 二进制
./macos/build-macos.sh

# 基准测试
./benchmark-macos.sh ~/models/Qwen3.6-35B-A3B-UD-IQ3_XXS.gguf

# 运行（生产配置：8GB pool + DBUF + SPAC α=0.75）
CGC_SERVER_EXPERT_CACHE_BYTES=8589934592 CGC_DBUF=1 CGC_SPAC=1 \
CGC_SPAC_ALPHA=0.75 CGC_NO_PREFETCH=1 \
./macos/run-macos.sh -m ~/models/Qwen3.6-35B-A3B-MTP-UD-IQ3_XXS-denseIQ4X.gguf
```

### Windows (CUDA/Vulkan)
```cmd
cd windows
build-windows.bat
llama-server.exe -m ..\..\models\gguf\Qwen3.6-35B-A3B.gguf -c 4096
```
详见 [windows/README.md](windows/README.md)

### Linux (CUDA/ROCm/Vulkan)
```bash
cd linux
./build-linux.sh
./llama-server -m ../../models/gguf/Qwen3.6-35B-A3B.gguf -c 4096
```
详见 [linux/README.md](linux/README.md)

### HarmonyOS PC (Kirin 9030)
```bash
./deploy-to-harmonyos.sh user@device-ip
# 在设备上: ./harmonyos/run.sh -m model.gguf
```

## 文档

| 文档 | 说明 |
|------|------|
| **[Cross_Platform_Porting_Guide.html](Cross_Platform_Porting_Guide.html)** | 🌟 **跨平台移植指导书**（Windows/Mac/Linux/HarmonyOS 完整移植指南） |
| [DEPLOY_GUIDE.md](DEPLOY_GUIDE.md) | 详细部署指南（Mac + HarmonyOS） |
| [macos/README.md](macos/) | macOS 平台部署说明 |
| [windows/README.md](windows/README.md) | Windows 平台部署说明 |
| [linux/README.md](linux/README.md) | Linux 平台部署说明 |
| [harmonyos/README.md](harmonyos/README.md) | HarmonyOS 平台部署说明 |

## 目录结构

```
deploy-harmonyos/
├── Cross_Platform_Porting_Guide.html  # 跨平台移植指导书
├── DEPLOY_GUIDE.md                     # 详细部署指南
├── README.md                            # 本文件
├── benchmark-macos.sh                   # Mac 基准测试
├── deploy-to-harmonyos.sh               # 一键部署到 HarmonyOS
├── run-universal.sh                      # 通用运行脚本
├── src.tar.gz                            # 源代码压缩包
│
├── macos/                                # macOS 部署包（预编译二进制）
│   ├── llama-server                      # server 二进制
│   ├── lib*.dylib                        # 动态库（Metal 后端）
│   ├── build-macos.sh                    # 构建脚本
│   └── run-macos.sh                      # 运行脚本
│
├── windows/                              # Windows 部署包
│   ├── build-windows.bat                 # 构建脚本
│   └── README.md                         # 部署说明
│
├── linux/                                # Linux 部署包
│   ├── build-linux.sh                    # 构建脚本
│   └── README.md                         # 部署说明
│
├── harmonyos/                            # HarmonyOS 部署包
│   ├── build.sh                          # 构建脚本
│   ├── run.sh                            # 运行脚本
│   └── README.md                         # 部署说明
│
└── phone/                                # 手机端部署（HarmonyOS 手机）
```

## 平台支持矩阵

| 平台 | GPU 后端 | Expert Cache | MTP | Down-Combine | 状态 |
|------|---------|-------------|-----|-------------|------|
| macOS | Metal | ✅ 完整 | ✅ 完整 | ✅ 已实现 | 生产验证 |
| Windows | CUDA/Vulkan | ✅ 可移植 | ✅ 可移植 | 🔄 待验证 | 开发中 |
| Linux | CUDA/ROCm/Vulkan | ✅ 可移植 | ✅ 可移植 | 🔄 待验证 | 开发中 |
| HarmonyOS | Vulkan/CPU | ✅ 可移植 | 🔄 待验证 | ❌ 未实现 | 原型阶段 |

## 已验证性能（macOS M4 Max 16GB）

| 配置 | decode_tps（中位数） | 最佳 | draft_accept | 质量 |
|------|---------------------|------|-------------|------|
| 基线（无 Expert Cache） | ~8 | ~10 | N/A | 1.0 |
| 8GB + DBUF + SPAC α=0.75 | **25.87** | **26.19** | ~96% | 1.0/0.882/1.0 |
| + Down-Combine (P0) | 23.29 | 25.09 | 94.25% | 1.0 |

## 核心环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `CGC_SERVER_EXPERT_CACHE_BYTES` | - | Expert Cache Pool 大小（8GB = 8589934592） |
| `CGC_DBUF` | 0 | Double Buffer Step-Ahead Refill |
| `CGC_SPAC` | 0 | EMA Spatial Activated Estimator |
| `CGC_SPAC_ALPHA` | 0.75 | EMA 衰减系数（已调优） |
| `CGC_NO_PREFETCH` | 0 | MTP 安全模式（必须设为 1） |
| `CGC_DOWN_COMBINE` | 0 | P0 Down Projection Batch Combine（实验性） |
| `CGC_DC_NSG` | 2 | Down-Combine threadgroup 大小（调优用） |

## 三方合并策略

跨平台代码整合采用三方合并模型：
- **base**: 共同祖先 (07fdd66)
- **ours**: fork 分支（Vulkan/Windows 适配）
- **theirs**: Mac HEAD（Expert Cache/DBUF/SPAC/Down-Combine）

只针对核心 **5+2 个文件**做逐文件三方合并，避免混入临时文件。详见移植指导书 §3。

> **重要**：合并过程中不要清理构建产物（build/ 目录、*.dylib/*.dll/*.so）。保留构建产物可以随时回滚和验证。
