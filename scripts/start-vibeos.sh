#!/usr/bin/env bash
# Boot vibeOS-local: starts the kernel (which manages llama-server + sd-cli itself).
# Usage: start-vibeos.sh [tier]   (tier defaults to autodetect from VRAM)
set -euo pipefail

VIBEOS=${VIBEOS_DIR:-/opt/vibeos}
REPO=$(cd "$(dirname "$0")/.." && pwd)

# Tier selection lives in the kernel now: explicit arg > previously chosen
# (/opt/vibeos/active-tier) > embedded 8gb starter set. At first boot the OS
# detects VRAM and offers the upgrade itself.
export VIBEOS_DIR=$VIBEOS
export VIBEOS_SHELL=$REPO/shell
export VIBEOS_MANIFESTS=$REPO/manifests
if [ -n "${1:-}" ]; then
    export VIBEOS_MANIFEST=$REPO/manifests/tier-$1.json
fi

echo "[vibeos] kernel starting — open http://localhost:1337 in your browser"
exec python3 "$REPO/kernel/server.py"
