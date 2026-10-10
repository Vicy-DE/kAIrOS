#!/usr/bin/env bash
# Autonomous ISO pipeline: wait for engines -> build ISO -> verify SSH in QEMU.
# Resumable; heartbeat for the watchdog.
exec >> /opt/vibeos/iso-pipeline.log 2>&1
echo "=== iso pipeline start $(date) ==="
REPO=/mnt/c/Users/Layer/Documents/vibeOS-local
( while true; do date +%s > /opt/vibeos/iso-heartbeat; sleep 20; done ) &
trap 'kill %1 2>/dev/null' EXIT

echo "[iso] waiting for multi-arch engines + tooling…"
for i in $(seq 1 720); do
    [ -f /opt/vibeos/engines-multiarch.done ] && grep -q done /opt/vibeos/lb-install.log 2>/dev/null && break
    sleep 10
done
[ -f /opt/vibeos/engines-multiarch.done ] || { echo "[iso] engines never finished — abort"; exit 1; }
echo "[iso] prerequisites ready."

if [ ! -f /opt/vibeos/iso-built.done ]; then
    echo "[iso] === building ISO (resumable, retrying on transient WSL I/O stalls) ==="
    built=0
    for attempt in 1 2 3 4 5; do
        echo "[iso] build attempt $attempt $(date)"
        if bash "$REPO/iso/build-iso.sh"; then built=1; break; fi
        echo "[iso] attempt $attempt failed; sync + cooldown then resume…"
        sync; sleep 20
    done
    if [ $built -eq 1 ]; then touch /opt/vibeos/iso-built.done; else
        echo "[iso] BUILD FAILED after retries"; exit 1
    fi
fi

echo "[iso] === verifying SSH in QEMU ==="
if bash "$REPO/iso/verify-ssh-qemu.sh"; then
    touch /opt/vibeos/iso-ssh-verified.done
    echo "[iso] SSH VERIFIED"
else
    echo "[iso] SSH verification failed (see log) — ISO still built"
fi
touch /opt/vibeos/iso-pipeline.done
echo "=== iso pipeline finished $(date) ==="
