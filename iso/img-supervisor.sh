#!/usr/bin/env bash
# Resumable supervisor for the raw .img.gz build, with heartbeat for the watchdog.
exec >> /opt/vibeos/img-pipeline.log 2>&1
echo "=== img-supervisor start $(date) ==="
( while true; do date +%s > /opt/vibeos/img-heartbeat; sleep 20; done ) &
HB=$!
trap 'kill $HB 2>/dev/null' EXIT
rm -f /opt/vibeos/img-built.done
if bash "${KAIROS_REPO:-$(cd "$(dirname "$0")/.." && pwd)}/iso/build-img.sh"; then
    touch /opt/vibeos/img-built.done
    echo "=== IMG OK $(date) ==="
else
    echo "=== IMG FAILED rc=$? $(date) ==="
fi
