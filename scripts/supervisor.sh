#!/usr/bin/env bash
# kAIrOS build supervisor — survives session drops.
# Runs engines install + 8gb model downloads with a heartbeat file; everything
# idempotent/resumable, so it can be relaunched any number of times.
exec >> /opt/vibeos/supervisor.log 2>&1
echo "=== supervisor start $(date) ==="
# repo = the checkout this script lives in (the old WSL path is dead)
REPO=${KAIROS_REPO:-$(cd "$(dirname "$0")/.." && pwd)}

# heartbeat for the watchdog
( while true; do date +%s > /opt/vibeos/heartbeat; sleep 20; done ) &
HB=$!
trap 'kill $HB 2>/dev/null' EXIT

# recover from any earlier apt/dpkg kill (stale locks, half-configured packages)
if ! pgrep -x dpkg >/dev/null && ! pgrep -x apt-get >/dev/null; then
    rm -f /var/lib/dpkg/lock-frontend /var/lib/dpkg/lock /var/lib/apt/lists/lock /var/cache/apt/archives/lock
    dpkg --configure -a || true
fi

# model downloads run alongside the engine build (aria2 resumes partials)
if [ ! -f /opt/vibeos/models-8gb.done ]; then
    ( python3 "$REPO/scripts/download-models.py" "$REPO/manifests/tier-8gb.json" /opt/vibeos/models \
        && touch /opt/vibeos/models-8gb.done ) &
    DL=$!
else
    DL=""
fi

if [ ! -f /opt/vibeos/engines.done ]; then
    if bash "$REPO/scripts/install-engines.sh"; then
        touch /opt/vibeos/engines.done
    else
        echo "ENGINE INSTALL FAILED rc=$?"
    fi
fi

[ -n "$DL" ] && wait $DL
echo "=== supervisor finished $(date) ==="
ls -la /opt/vibeos/*.done 2>/dev/null
touch /opt/vibeos/supervisor.done
