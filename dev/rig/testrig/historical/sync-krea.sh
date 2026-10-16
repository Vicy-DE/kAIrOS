#!/usr/bin/env bash
# HISTORICAL (dev/rig/testrig/historical): kept as a record of how earlier images were provisioned;
# it needs artefacts that are not in this repository (see the header below). Paths are env-overridable:
# KAIROS_REPO (repo in the container), KAIROS_SSH_KEY, KAIROS_FIXES (dir with the binary fix bundles).
# sync-krea.sh — provision a BOOTED guest VM with the Krea 2 image stack (R34):
#   * sd-cli rebuilt from sd.cpp >= 2026-06 (Krea2 support), self-contained bundle
#     -> becomes /opt/vibeos/bin/sd-cli; the shipped engine stays as sd-cli-classic
#   * Krea-2-Turbo Q4_K_M + Qwen3-VL-4B encoder + Wan 2.1 VAE -> /opt/vibeos/models/image
#   * current repo (kernel/shell/manifests/docs — R34-R37 features)
# Run inside the testrig container while the VM is up: sync-krea.sh [ssh_port]
# Idempotent: skips model files that already have the right size.
set -euo pipefail
PORT=${1:-2222}
REPO=${KAIROS_REPO:-/workspace/repo}
FIX=${KAIROS_FIXES:-/images/fixes}/sd-krea
MODELS=${KAIROS_MODELS_DIR:-/workspace/models}/image
SSH="sshpass -p kairos ssh -p $PORT -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR root@127.0.0.1"
log(){ printf '\033[1;35m[sync-krea]\033[0m %s\n' "$*"; }

[ -x "$FIX/sd-cli.bin" ] || { echo "engine bundle missing: $FIX (run build-sd-krea.sh)"; exit 1; }

log "1/4 Krea-2-capable sd-cli engine -> guest (old engine kept as sd-cli-classic)"
$SSH 'mkdir -p /opt/vibeos/engines'   # never || a second tar onto a consumed stream
tar -C "$(dirname "$FIX")" -czf - "$(basename "$FIX")" | $SSH 'tar -C /opt/vibeos/engines -xzf -'
$SSH 'set -e
test -x /opt/vibeos/engines/sd-krea/sd-cli.bin
cd /opt/vibeos/bin
if [ ! -e sd-cli-classic ] && [ -e sd-cli ] && [ ! -L sd-cli ]; then mv sd-cli sd-cli-classic; fi
ln -sf ../engines/sd-krea/run-sd-cli.sh sd-cli
echo "engine: $(readlink sd-cli) ($(du -sh /opt/vibeos/engines/sd-krea | cut -f1))"'

log "2/4 Krea 2 model files -> guest /opt/vibeos/models/image (skips present files)"
for f in Krea-2-Turbo-Q4_K_M.gguf Qwen3VL-4B-Instruct-Q4_K_M.gguf wan_2.1_vae.safetensors; do
  [ -f "$MODELS/$f" ] || { echo "missing on host: $MODELS/$f"; exit 1; }
  SZ=$(stat -c%s "$MODELS/$f")
  HAVE=$($SSH "stat -c%s /opt/vibeos/models/image/$f 2>/dev/null || echo 0")
  if [ "$HAVE" = "$SZ" ]; then log "  $f already in guest ($SZ bytes)"; continue; fi
  log "  pushing $f ($((SZ / 1024 / 1024)) MB)…"
  $SSH "cat > /opt/vibeos/models/image/$f" < "$MODELS/$f"
done

log "3/4 repo sync (kernel/shell/manifests/docs with R34-R37)"
tar -C "$REPO" -czf - kernel shell manifests scripts docs \
  | $SSH 'tar -C /opt/vibeos/os -xzf - && echo synced'

log "4/4 restart kAIrOS + status"
$SSH 'systemctl restart kairos && sleep 4 && curl -sf http://127.0.0.1:1337/api/status | head -c 400'
echo
log "done — POST /api/image now renders with Krea 2 Turbo"
