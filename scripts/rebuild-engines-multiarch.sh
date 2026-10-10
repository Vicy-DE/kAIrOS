#!/usr/bin/env bash
# Rebuild engines multi-arch for the ISO: sm_89 (RTX 4070 / Ada) + sm_120 (this box).
REPO=${KAIROS_REPO:-$(cd "$(dirname "$0")/.." && pwd)}   # the checkout this script lives in
exec >> /opt/vibeos/engines-multiarch.log 2>&1
echo "=== multiarch engine rebuild start $(date) ==="
( while true; do date +%s > /opt/vibeos/eng2-heartbeat; sleep 20; done ) &
HB=$!
trap 'kill $HB 2>/dev/null' EXIT
pkill -f kernel/server.py 2>/dev/null   # free the binaries being relinked
rm -f /opt/vibeos/engines-multiarch.done
export CUDA_ARCH="89;120"
if bash "$REPO/scripts/install-engines.sh"; then
    touch /opt/vibeos/engines-multiarch.done
    echo "=== MULTIARCH ENGINES OK $(date) ==="
    /opt/vibeos/bin/llama-server --version 2>&1 | head -2
else
    echo "=== MULTIARCH ENGINES FAILED rc=$? $(date) ==="
fi
