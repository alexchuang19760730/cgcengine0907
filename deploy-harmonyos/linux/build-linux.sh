#!/bin/bash
# CGC Engine Linux Build Script
# Requirements: GCC 11+, CMake, Git
# GPU Support: CUDA (NVIDIA) or Vulkan (AMD/Intel) or ROCm (AMD)

set -e

echo "=== CGC Engine Linux Build ==="
echo ""

BUILD_DIR="build-linux"
SOURCE_DIR="../../src/llama.cpp"

# Detect GPU backend
if command -v nvcc &> /dev/null; then
    echo "[INFO] CUDA detected, building with GGML_CUDA=ON"
    GPU_FLAGS="-DGGML_CUDA=ON"
elif command -v rocminfo &> /dev/null; then
    echo "[INFO] ROCm detected, building with GGML_HIP=ON"
    GPU_FLAGS="-DGGML_HIP=ON"
else
    echo "[INFO] No GPU detected, building with Vulkan backend"
    GPU_FLAGS="-DGGML_VULKAN=ON"
fi

# Configure
echo "[1/3] Configuring CMake..."
cmake -B "$BUILD_DIR" -S "$SOURCE_DIR" \
    -DCMAKE_BUILD_TYPE=Release \
    $GPU_FLAGS \
    -DBUILD_SHARED_LIBS=ON

# Build
echo "[2/3] Building..."
cmake --build "$BUILD_DIR" --config Release --target llama-server -j$(nproc)

# Copy binaries
echo "[3/3] Copying binaries..."
cp -f "$BUILD_DIR/bin/llama-server" .
cp -f "$BUILD_DIR/bin/lib"*.so . 2>/dev/null || true

echo ""
echo "=== Build Complete ==="
echo "Run: ./llama-server -m model.gguf -c 4096"
