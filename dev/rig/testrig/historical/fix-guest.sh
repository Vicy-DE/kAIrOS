#!/usr/bin/env bash
# HISTORICAL (dev/rig/testrig/historical): kept as a record of how earlier images were provisioned;
# it needs artefacts that are not in this repository (see the header below). Paths are env-overridable:
# KAIROS_REPO (repo in the container), KAIROS_SSH_KEY, KAIROS_FIXES (dir with the binary fix bundles).
# fix-guest.sh — provision a booted kAIrOS guest VM with the two fixes the
# 2026-07-02 GPU passthrough test uncovered (run from inside the testrig
# container while the VM is up, e.g. after `vm.sh fresh` + a boot):
#
#   1. NVIDIA userspace missing from the image: the ISO hook installed the
#      driver with --no-install-recommends, and in the CUDA repo `nvidia-smi`
#      is a transitional dummy -> no nvidia-smi, no libcuda1, no nvidia-modprobe.
#      Without libcuda.so.1 the engines can never touch the GPU; without
#      nvidia-smi the first-boot VRAM tier detection reports 0 MB.
#   2. libmtmd.so.0 missing from /opt/vibeos/lib (scripts/build-image.sh lib
#      glob lacked libmtmd*) -> llama-server/llama-cli cannot start at all.
#      pod/fixes/libmtmd.so.0.0.0 was built from llama.cpp @ 2026-06-13
#      (GGML 0.15.1 — matches the image's engine libs) and is runtime-verified.
#
# Both are also fixed at the SOURCE for future image builds:
#   scripts/build-image.sh (lib glob) and iso/config/hooks/normal/0100-nvidia-cuda.hook.chroot.
#
# Usage: fix-guest.sh [ssh_port]   (default 2222; guest login root/kairos)
set -euo pipefail
PORT=${1:-2222}
MTMD=${MTMD:-${KAIROS_FIXES:-/images/fixes}/libmtmd.so.0.0.0}
# key-built images accept only testrig/id_kairos; pre-R66 images take the password [R66]
SSH_KEY=${KAIROS_SSH_KEY:-$(dirname "$0")/../id_kairos}
SSH_OPTS=(-p "$PORT" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR)
[ -f "$SSH_KEY" ] && SSH_OPTS+=(-i "$SSH_KEY" -o IdentitiesOnly=yes)
g(){ sshpass -p "${KAIROS_ROOT_PW:-kairos}" ssh "${SSH_OPTS[@]}" root@127.0.0.1 "$@"; }
log(){ printf '\033[1;36m[fix-guest]\033[0m %s\n' "$*"; }

log "1/3 nvidia userspace (libcuda1, nvidia-modprobe, nvidia-driver-cuda)…"
g 'DEBIAN_FRONTEND=noninteractive apt-get update -qq >/dev/null 2>&1;
   DEBIAN_FRONTEND=noninteractive apt-get install -y -qq libcuda1 nvidia-modprobe nvidia-driver-cuda 2>&1 | tail -1'
g 'nvidia-smi --query-gpu=name,memory.total --format=csv,noheader' \
  && log "   nvidia-smi OK" || log "   WARN: nvidia-smi failed (no GPU passed through?)"

log "2/3 libmtmd.so.0 -> /opt/vibeos/lib…"
[ -f "$MTMD" ] || { echo "missing $MTMD"; exit 1; }
sshpass -p "${KAIROS_ROOT_PW:-kairos}" scp -P "$PORT" "${SSH_OPTS[@]:2}" \
  "$MTMD" root@127.0.0.1:/opt/vibeos/lib/libmtmd.so.0.0.0
g 'cd /opt/vibeos/lib && ln -sf libmtmd.so.0.0.0 libmtmd.so.0 && ln -sf libmtmd.so.0 libmtmd.so && ldconfig 2>/dev/null || true'
g 'LD_LIBRARY_PATH=/opt/vibeos/lib /opt/vibeos/bin/llama-server --version' \
  && log "   llama-server starts" || { echo "llama-server still broken"; exit 1; }

log "3/3 restart kairos…"
g 'systemctl restart kairos'
log "done — guest fully provisioned (LLM + image gen + VRAM detect on GPU)."
