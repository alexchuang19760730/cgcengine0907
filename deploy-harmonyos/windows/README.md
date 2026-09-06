# CGC Engine - Windows Deployment

## Hardware Requirements
- **CPU**: x86_64 (AVX2 recommended)
- **GPU**: NVIDIA (CUDA) or AMD/Intel (Vulkan)
- **RAM**: 16GB minimum (32GB recommended for 35B models)
- **Disk**: 20GB free space

## Build Instructions

### Prerequisites
1. Visual Studio 2022 (with C++ workload)
2. CMake 3.20+
3. Git
4. CUDA Toolkit 12.x (for NVIDIA GPUs) OR Vulkan SDK (for AMD/Intel)

### Build
```cmd
cd deploy-harmonyos\windows
build-windows.bat
```

### Run
```cmd
llama-server.exe -m ..\..\models\gguf\Qwen3.6-35B-A3B.gguf -c 4096 --host 0.0.0.0 --port 8080
```

## Expert Cache Configuration (Windows)

### Environment Variables
```cmd
REM Expert Cache Pool Size (8GB = 8589934592)
set CGC_SERVER_EXPERT_CACHE_BYTES=8589934592

REM Double Buffer Step-Ahead Refill
set CGC_DBUF=1

REM EMA Spatial Activated Estimator
set CGC_SPAC=1
set CGC_SPAC_ALPHA=0.75

REM MTP Safety (required for MTP config)
set CGC_NO_PREFETCH=1
```

### Expected Performance
| GPU | Model | Quant | Expected t/s |
|-----|-------|-------|-------------|
| RTX 4090 | Qwen3.6-35B-A3B | IQ3_XXS | 40-60 |
| RTX 3090 | Qwen3.6-35B-A3B | IQ3_XXS | 25-35 |
| CPU-only | Qwen3.6-35B-A3B | IQ3_XXS | 3-8 |

## Known Issues
1. CUDA backend requires expert cache pool in GPU memory (not system RAM)
2. Vulkan backend may need `GGML_VULKAN_DEVICE_INDEX` to select correct GPU
3. Windows Defender may slow down initial model loading
