#!/usr/bin/env bash
# Build the kAIrOS engines. Every model runs through pure C++/GGML [R8]:
#   llama.cpp             -> llama-server, llama-cli        (LLM)
#   stable-diffusion.cpp  -> sd-cli                         (image + video)
#   acestep.cpp           -> ace-lm, ace-synth, ace-server  (music [R31])
# Target: Debian 13 (trixie); NVIDIA RTX 4070 laptop (Ada sm_89) and RTX 5070 Ti (Blackwell sm_120).
#
#   ENGINE_BACKEND=cuda   (default) CUDA toolkit + CUDA builds  -> /opt/vibeos/bin
#   ENGINE_BACKEND=vulkan Vulkan builds for the AMD iGPU [R30]  -> /opt/vibeos/bin/vulkan
#                         (static binaries, so the two sets never share a libggml*.so)
# The kernel picks the set at runtime (kernel/server.py engine_backend: VIBEOS_ENGINE
# env, else Vulkan whenever no NVIDIA driver is loaded and bin/vulkan/ exists).
set -euo pipefail

VIBEOS=/opt/vibeos
ENGINES=$VIBEOS/engines
JOBS=$(nproc)
# [R41/R50] native SASS for BOTH targets: sm_89 = the RTX 4070 laptop, sm_120 = the RTX 5070 Ti
# desktop (CMake "89;120" also embeds compute_89/compute_120 PTX). The old default "120"
# shipped engines with no kernel for the 4070 at all, and PTX JIT is no fallback here: the
# image's driver is whatever cuda-drivers resolves to, and a driver older than the toolkit
# that wrote the PTX refuses to JIT it (docs/ENGINES-CUDA-2026-09.md).
CUDA_ARCH="${CUDA_ARCH:-89;120}"
# CUDA 13.x runtimes run on any R580+ driver (minor-version compatibility) — the driver the
# image installs. A 14.x toolkit raises that floor: bump this deliberately, with the hook.
CUDA_MAJOR="${CUDA_MAJOR:-13}"
ENGINE_BACKEND="${ENGINE_BACKEND:-cuda}"
case "$ENGINE_BACKEND" in
    cuda)   BIN=$VIBEOS/bin;        BUILD=build ;;
    vulkan) BIN=$VIBEOS/bin/vulkan; BUILD=build-vulkan ;;
    *) echo "ENGINE_BACKEND must be 'cuda' or 'vulkan' (got '$ENGINE_BACKEND')"; exit 1 ;;
esac
mkdir -p "$BIN" "$ENGINES"

echo "== [1/5] toolchain ($ENGINE_BACKEND) =="
# a fresh trixie chroot/container has no package lists yet — without this every install
# below is "unable to locate" (the Vulkan branch hit it first, then the CUDA branch when
# nvcc was already present and the toolkit block was skipped)
apt-get update -qq
if [ "$ENGINE_BACKEND" = cuda ]; then
    if ! command -v nvcc >/dev/null 2>&1 && [ ! -x /usr/local/cuda/bin/nvcc ]; then
        DEBIAN_FRONTEND=noninteractive apt-get install -y -qq curl ca-certificates
        cd /tmp
        # NVIDIA's Debian 13 repo (was wsl-ubuntu, a leftover of the WSL build PC). trixie's
        # apt accepts its keyring as shipped — no [trusted=yes] rewrite needed (2026-09-14).
        curl -fsSL --speed-limit 2048 --speed-time 60 --retry 5 --retry-all-errors -o cuda-keyring.deb \
             https://developer.download.nvidia.com/compute/cuda/repos/debian13/x86_64/cuda-keyring_1.1-1_all.deb
        dpkg -i cuda-keyring.deb
        apt-get update -qq
        # newest CUDA $CUDA_MAJOR.x compiler in the repo, as "13-4"
        V=$(apt-cache search "^cuda-nvcc-$CUDA_MAJOR-[0-9]+$" | awk '{print $1}' | sed 's/^cuda-nvcc-//' | sort -V | tail -1)
        [ -n "$V" ] || { echo "no cuda-nvcc-$CUDA_MAJOR-* in NVIDIA's debian13 repo"; exit 1; }
        # only what the three ggml builds compile and link: nvcc (+crt/nvvm), cudart + cccl
        # headers, cublas/cublasLt, the libcuda stub (ggml's VMM path links the driver API).
        # 0.93 GB instead of the 3.3 GB cuda-toolkit meta (13.4, measured 2026-09-14) —
        # cusparse/cufft/cusolver/nsight/docs are never used.
        echo "installing CUDA $V (nvcc, cudart, cublas) ..."
        DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
            "cuda-nvcc-$V" "cuda-cudart-dev-$V" "libcublas-dev-$V" "cuda-driver-dev-$V"
    fi
    export PATH=/usr/local/cuda/bin:$PATH
    nvcc --version | tail -1
    # ggml/llama.cpp, sd.cpp and acestep.cpp all take the same CUDA switches
    GGML_FLAGS=(-DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES="$CUDA_ARCH")
    SD_FLAGS=(-DSD_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES="$CUDA_ARCH")
else
    # [R30] Vulkan build deps: loader headers + the glslc shader compiler ggml-vulkan
    # runs at build time. RUNTIME needs only libvulkan1 + mesa-vulkan-drivers (RADV for
    # the Radeon 780M) — both in iso/config/package-lists/kairos.list.chroot.
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq libvulkan-dev glslc spirv-headers glslang-tools
    glslc --version | head -1
    # static + portable (no -march=native): the Vulkan set runs on any AMD/Intel box
    GGML_FLAGS=(-DGGML_VULKAN=ON -DBUILD_SHARED_LIBS=OFF -DGGML_NATIVE=OFF -DGGML_AVX2=ON)
    SD_FLAGS=(-DSD_VULKAN=ON -DSD_BUILD_SHARED_LIBS=OFF -DGGML_NATIVE=OFF -DGGML_AVX2=ON)
fi

echo "== [2/5] deps =="
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq git cmake build-essential libcurl4-openssl-dev ffmpeg python3-aiohttp

echo "== [3/5] llama.cpp =="
cd "$ENGINES"
if [ ! -d llama.cpp ]; then git clone --depth 1 https://github.com/ggml-org/llama.cpp; fi
rm -rf "llama.cpp/$BUILD"   # always configure clean — stale caches break partial rebuilds
# LLAMA_BUILD_UI downloads web assets from HF at build time (fails offline / 403);
# kAIrOS only uses the server's HTTP API, so disable the embedded web UI.
cmake -S llama.cpp -B "llama.cpp/$BUILD" "${GGML_FLAGS[@]}" \
      -DLLAMA_CURL=ON -DCMAKE_BUILD_TYPE=Release \
      -DLLAMA_BUILD_UI=OFF -DLLAMA_USE_PREBUILT_UI=OFF \
      -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_EXAMPLES=OFF
cmake --build "llama.cpp/$BUILD" --target llama-server llama-cli -j"$JOBS"
ln -sf "$ENGINES/llama.cpp/$BUILD/bin/llama-server" "$BIN/llama-server"
ln -sf "$ENGINES/llama.cpp/$BUILD/bin/llama-cli"    "$BIN/llama-cli"

echo "== [4/5] stable-diffusion.cpp =="
cd "$ENGINES"
if [ ! -d stable-diffusion.cpp ]; then
    git clone --recursive --depth 1 https://github.com/leejet/stable-diffusion.cpp
fi
rm -rf "stable-diffusion.cpp/$BUILD"
cmake -S stable-diffusion.cpp -B "stable-diffusion.cpp/$BUILD" "${SD_FLAGS[@]}" -DCMAKE_BUILD_TYPE=Release
cmake --build "stable-diffusion.cpp/$BUILD" -j"$JOBS"
SD_BIN=$(find "$ENGINES/stable-diffusion.cpp/$BUILD/bin" -maxdepth 1 -type f \( -name sd -o -name sd-cli \) | head -1)
ln -sf "$SD_BIN" "$BIN/sd-cli"
# sd-server (examples/server, built by sd.cpp's default targets): one loaded model for a whole
# batch of pictures — the kernel's /api/images and /genimg sessions; sd-cli stays the fallback
if [ -f "$ENGINES/stable-diffusion.cpp/$BUILD/bin/sd-server" ]; then
    ln -sf "$ENGINES/stable-diffusion.cpp/$BUILD/bin/sd-server" "$BIN/sd-server"
fi

echo "== [5/5] acestep.cpp [R31] =="
# ACE-Step 1.5 music, MIT, GGML (github.com/ServeurpersoCom/acestep.cpp). Its ggml is a
# submodule (patched fork: GGML_OP_SNAKE etc.) — a non-recursive clone does not build.
# The CLI is request-JSON driven: ace-lm  --models <dir> --request r.json  -> r0.json
#                                 ace-synth --models <dir> --request r0.json -> r00.wav
# (there is no "ace-cli" and no --prompt/--dit/-o flags; see kernel music_job).
cd "$ENGINES"
if [ ! -d acestep.cpp ]; then
    git clone --recursive --shallow-submodules --depth 1 https://github.com/ServeurpersoCom/acestep.cpp
fi
rm -rf "acestep.cpp/$BUILD"
# ALWAYS static: acestep's ggml is a patched FORK with the same sonames as llama.cpp's
# (libggml.so.0, libggml-cuda.so.0 …). Shared, both sets land in the image's one
# /opt/vibeos/lib and the last copy wins — one engine would load the other fork's ggml
# (found on the first CUDA build, 2026-09-14, docs/ENGINES-CUDA-2026-09.md §4).
cmake -S acestep.cpp -B "acestep.cpp/$BUILD" "${GGML_FLAGS[@]}" -DBUILD_SHARED_LIBS=OFF -DCMAKE_BUILD_TYPE=Release
# only the three tools the kernel/server can use — skips quantize/neural-codec/tests
cmake --build "acestep.cpp/$BUILD" --target ace-lm ace-synth ace-server -j"$JOBS"
# acestep's CMake puts executables in the build ROOT (CMAKE_RUNTIME_OUTPUT_DIRECTORY), not bin/
ln -sf "$ENGINES/acestep.cpp/$BUILD/ace-lm"     "$BIN/ace-lm"
ln -sf "$ENGINES/acestep.cpp/$BUILD/ace-synth"  "$BIN/ace-synth"
ln -sf "$ENGINES/acestep.cpp/$BUILD/ace-server" "$BIN/ace-server"

echo "== engine stamp: $BIN/ENGINES.json =="
# what was built (backend, CUDA arch/toolkit, each upstream commit + its date) — the kernel
# refuses manifests validated against a newer set (manifest "engines", kernel check_engines)
# instead of crashing an engine; build-iso.sh / build-image.sh ship the file with the binaries
python3 "$(dirname "$0")/engine-stamp.py" --root "$VIBEOS" --backend "$ENGINE_BACKEND"

echo "== engines installed ($ENGINE_BACKEND -> $BIN) =="
"$BIN/llama-server" --version 2>&1 | head -2 || true
"$BIN/sd-cli" --help 2>&1 | head -3 || true
"$BIN/ace-synth" --help 2>&1 | head -1 || true
echo "ENGINES INSTALL COMPLETE"
