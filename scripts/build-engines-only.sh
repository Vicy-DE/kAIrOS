#!/usr/bin/env bash
# Focused, resumable engine build (CUDA already installed). Clean configure.
REPO=${KAIROS_REPO:-$(cd "$(dirname "$0")/.." && pwd)}   # the checkout this script lives in
exec >> /opt/vibeos/engines-build2.log 2>&1
echo "=== engine build start $(date) ==="
( while true; do date +%s > /opt/vibeos/eng-heartbeat; sleep 20; done ) &
HB=$!
trap 'kill $HB 2>/dev/null' EXIT
rm -f /opt/vibeos/engines.done
if bash "$REPO/scripts/install-engines.sh"; then
    touch /opt/vibeos/engines.done
    echo "=== ENGINES OK $(date) ==="
else
    echo "=== ENGINES FAILED rc=$? $(date) ==="
fi
