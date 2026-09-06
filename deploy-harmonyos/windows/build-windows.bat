@echo off
REM CGC Engine Windows Build Script
REM Requirements: Visual Studio 2022, CMake, Git
REM GPU Support: CUDA (NVIDIA) or Vulkan (AMD/Intel)

echo === CGC Engine Windows Build ===
echo.

REM Set build directory
set BUILD_DIR=build-windows
set SOURCE_DIR=..\..\src\llama.cpp

REM Check for CUDA
where nvcc >nul 2>&1
if %errorlevel%==0 (
    echo [INFO] CUDA detected, building with GGML_CUDA=ON
    set GPU_BACKEND=GGML_CUDA=ON
) else (
    echo [INFO] CUDA not found, building with Vulkan backend
    set GPU_BACKEND=GGML_VULKAN=ON
)

REM Configure
echo [1/3] Configuring CMake...
cmake -B %BUILD_DIR% -S %SOURCE_DIR% ^
    -DCMAKE_BUILD_TYPE=Release ^
    -D%GPU_BACKEND% ^
    -DBUILD_SHARED_LIBS=ON

if %errorlevel% neq 0 (
    echo [ERROR] CMake configuration failed
    exit /b 1
)

REM Build
echo [2/3] Building...
cmake --build %BUILD_DIR% --config Release --target llama-server -j8

if %errorlevel% neq 0 (
    echo [ERROR] Build failed
    exit /b 1
)

REM Copy binaries
echo [3/3] Copying binaries...
copy /Y %BUILD_DIR%\bin\Release\llama-server.exe .
copy /Y %BUILD_DIR%\bin\Release\*.dll .

echo.
echo === Build Complete ===
echo Run: llama-server.exe -m model.gguf -c 4096
