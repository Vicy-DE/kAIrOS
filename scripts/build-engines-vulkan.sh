#!/usr/bin/env bash
# [R30] Reproducible Vulkan engine set for the AMD iGPU (Radeon 780M): llama.cpp +
# stable-diffusion.cpp + acestep.cpp with ggml-vulkan, static, into /opt/vibeos/bin/vulkan/.
# Same shape as rebuild-engines-multiarch.sh (log + heartbeat + done marker, resumable).
# Build deps (apt): libvulkan-dev glslc spirv-headers glslang-tools — installed by
# install-engines.sh. Runtime deps on the image: libvulkan1 mesa-vulkan-drivers
# (iso/config/package-lists/kairos.list.chroot). Needs NO GPU to build.
REPO=${KAIROS_REPO:-$(cd "$(dirname "$0")/.." && pwd)}   # the checkout this script lives in
exec >> /opt/vibeos/engines-vulkan.log 2>&1
echo "=== vulkan engine build start $(date) ==="
( while true; do date +%s > /opt/vibeos/engvk-heartbeat; sleep 20; done ) &
HB=$!
trap 'kill $HB 2>/dev/null' EXIT
rm -f /opt/vibeos/engines-vulkan.done
export ENGINE_BACKEND=vulkan
if bash "$REPO/scripts/install-engines.sh"; then
    touch /opt/vibeos/engines-vulkan.done
    echo "=== VULKAN ENGINES OK $(date) ==="
    /opt/vibeos/bin/vulkan/llama-server --version 2>&1 | head -2
else
    echo "=== VULKAN ENGINES FAILED rc=$? $(date) ==="
fi
