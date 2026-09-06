# CGC Engine - Linux Deployment

## Hardware Requirements
- **CPU**: x86_64 (AVX2 recommended) or ARM64
- **GPU**: NVIDIA (CUDA) / AMD (ROCm or Vulkan) / Intel (Vulkan)
- **RAM**: 16GB minimum (32GB recommended for 35B models)
- **Disk**: 20GB free space
- **OS**: Ubuntu 20.04+, Debian 11+, CentOS 8+, or similar

## Build Instructions

### Prerequisites
```bash
# Ubuntu/Debian
sudo apt-get update
sudo apt-get install -y build-essential cmake git pkg-config

# For CUDA (NVIDIA)
# Download from: https://developer.nvidia.com/cuda-downloads

# For ROCm (AMD)
# Follow: https://rocm.docs.amd.com/

# For Vulkan (通用)
sudo apt-get install -y libvulkan-dev vulkan-tools
```

### Build
```bash
cd deploy-harmonyos/linux
./build-linux.sh
```

### Run
```bash
./llama-server -m ../../models/gguf/Qwen3.6-35B-A3B.gguf \
    -c 4096 --host 0.0.0.0 --port 8080
```

## Expert Cache Configuration (Linux)

### Environment Variables
```bash
# Expert Cache Pool Size (8GB = 8589934592)
export CGC_SERVER_EXPERT_CACHE_BYTES=8589934592

# Double Buffer Step-Ahead Refill
export CGC_DBUF=1

# EMA Spatial Activated Estimator
export CGC_SPAC=1
export CGC_SPAC_ALPHA=0.75

# MTP Safety (required for MTP config)
export CGC_NO_PREFETCH=1
```

### Systemd Service (Production)
```ini
# /etc/systemd/system/cgc-engine.service
[Unit]
Description=CGC Engine Llama Server
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/opt/cgc-engine
Environment="CGC_SERVER_EXPERT_CACHE_BYTES=8589934592"
Environment="CGC_DBUF=1"
Environment="CGC_SPAC=1"
Environment="CGC_SPAC_ALPHA=0.75"
Environment="CGC_NO_PREFETCH=1"
ExecStart=/opt/cgc-engine/llama-server -m /opt/models/Qwen3.6-35B-A3B.gguf -c 4096 --host 0.0.0.0 --port 8080
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

### Expected Performance
| GPU | Model | Quant | Expected t/s |
|-----|-------|-------|-------------|
| A100 80GB | Qwen3.6-35B-A3B | IQ3_XXS | 60-80 |
| RTX 4090 | Qwen3.6-35B-A3B | IQ3_XXS | 40-60 |
| RTX 3090 | Qwen3.6-35B-A3B | IQ3_XXS | 25-35 |
| MI300X | Qwen3.6-35B-A3B | IQ3_XXS | 50-70 |
| CPU-only | Qwen3.6-35B-A3B | IQ3_XXS | 3-8 |

## Docker Deployment
```dockerfile
FROM nvidia/cuda:12.2.0-devel-ubuntu22.04
RUN apt-get update && apt-get install -y cmake git build-essential
COPY . /workspace
WORKDIR /workspace/src/llama.cpp
RUN cmake -B build -DGGML_CUDA=ON -DCMAKE_BUILD_TYPE=Release && \
    cmake --build build --target llama-server -j8
EXPOSE 8080
CMD ["./build/bin/llama-server", "-m", "/models/model.gguf", "-c", "4096", "--host", "0.0.0.0"]
```
