#!/usr/bin/env bash
# host-setup.sh — minimal host preparation for the kAIrOS Docker testrig (dev/rig).
# Everything else (QEMU, OVMF, toolchain, tests) lives INSIDE the Docker image.
#
# What it does (idempotent, additive only, display-safe):
#   1. Docker engine + compose v2 (Ubuntu/Debian archive packages)
#   2. Persistently bind the NVIDIA GPU (KAIROS_GPU_IDS: VGA + audio function, default the
#      reference laptop's RTX 4070 Mobile 10de:2820 + 10de:22bd) to vfio-pci at boot — the
#      other GPU (the AMD iGPU on the reference laptop) keeps the display.
#   3. Put the login user in the docker+kvm groups; generate the rig's SSH key; write
#      dev/rig/.env with the GPU's IOMMU group number for docker-compose.
#
# One REBOOT is required after the first run: the running session (kwin/steam/
# vscode) holds the NVIDIA DRM node, so a live nouveau->vfio rebind would hang.
# After reboot vfio-pci claims the card before anything else can open it.
# (The owner's machine runs the HOST nvidia driver by default and rebinds live with
# gpu-to-vfio.sh / gpu-to-host.sh instead — see docs/how-to/VM-TEST-RIG.md; in that
# case skip step 2 with KAIROS_SKIP_VFIO=1.)
#
# Run:  sudo bash host-setup.sh          # setup
#       sudo bash host-setup.sh status   # report only
set -euo pipefail
. "$(dirname "$0")/env.sh"

NV_VGA=$KAIROS_GPU_PCI
NV_IDS=$KAIROS_GPU_IDS
POD_DIR=$RIG_DIR
LOGIN_USER=${SUDO_USER:-$(id -un)}

log(){ printf '\033[1;36m[host-setup]\033[0m %s\n' "$*"; }
die(){ printf '\033[1;31m[host-setup] ERROR:\033[0m %s\n' "$*" >&2; exit 1; }

vfio_bound(){ [ "$(basename "$(readlink -f /sys/bus/pci/devices/$NV_VGA/driver 2>/dev/null)" 2>/dev/null)" = vfio-pci ]; }
iommu_group(){ basename "$(readlink -f /sys/bus/pci/devices/$NV_VGA/iommu_group)" 2>/dev/null; }

status(){
  echo "── kAIrOS testrig host status ─────────────────────────────────"
  echo "docker            : $(command -v docker >/dev/null && docker --version || echo NOT installed)"
  echo "compose           : $(docker compose version 2>/dev/null || echo NOT installed)"
  echo "/dev/kvm          : $([ -e /dev/kvm ] && echo present || echo MISSING)"
  echo "GPU ($NV_VGA) driver : $(basename "$(readlink -f /sys/bus/pci/devices/$NV_VGA/driver 2>/dev/null)" 2>/dev/null || echo none)"
  echo "GPU iommu group   : $(iommu_group)  (devices: $(ls /sys/kernel/iommu_groups/$(iommu_group)/devices 2>/dev/null | tr '\n' ' '))"
  echo "vfio config       : $([ -f /etc/modprobe.d/kairos-vfio.conf ] && echo written || echo missing)"
  echo "/dev/vfio         : $(ls /dev/vfio 2>/dev/null | tr '\n' ' ' || echo none)"
  echo "rig key           : $([ -f "$POD_DIR/testrig/id_kairos" ] && echo present || echo missing)"
  echo "KAIROS_HOME       : $KAIROS_HOME"
  echo "READY FOR GPU VM  : $(vfio_bound && echo YES || echo 'NO — reboot pending (or use gpu-to-vfio.sh live)')"
  echo "───────────────────────────────────────────────────────────────"
}
[ "${1:-}" = status ] && { status; exit 0; }
[ "$(id -u)" = 0 ] || die "run as root: sudo bash $0"

# ── 1. Docker ────────────────────────────────────────────────────────────────
if ! command -v docker >/dev/null; then
  log "installing docker.io + docker-compose-v2 + qemu-utils…"
  apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq docker.io docker-compose-v2 qemu-utils
else
  log "docker already installed: $(docker --version)"
fi
systemctl enable --now docker >/dev/null 2>&1 || true

# ── 2. VFIO bind for the GPU (persistent) ───────────────────────────────────
if [ "${KAIROS_SKIP_VFIO:-0}" = 1 ]; then
  log "KAIROS_SKIP_VFIO=1: not writing the vfio-at-boot config (rebind live with gpu-to-vfio.sh)"
else
  log "writing vfio-pci bind config for $NV_IDS (the other GPU keeps the display)…"
  cat > /etc/modprobe.d/kairos-vfio.conf <<EOF
# kAIrOS testrig: give the NVIDIA GPU (VGA+audio) to vfio-pci at boot so a
# QEMU VM inside Docker can own it. The display runs on the other GPU.
# vfio-pci claims ONLY these IDs; snd_hda_intel still drives the other audio devices.
options vfio-pci ids=$NV_IDS
softdep nouveau pre: vfio-pci
softdep snd_hda_intel pre: vfio-pci
softdep nvidiafb pre: vfio-pci
EOF
  cat > /etc/modules-load.d/kairos-vfio.conf <<EOF
# kAIrOS testrig: load vfio-pci early (its 'ids=' option claims the NVIDIA GPU)
vfio-pci
EOF
  log "regenerating initramfs (nouveau must lose the race at next boot)…"
  update-initramfs -u
fi

# ── 2b. testrig SSH key [R66] ────────────────────────────────────────────────
# Images built after R66 accept root over SSH with a KEY only (the password is
# public). One keypair for the rig: private key stays here (mounted into the
# container at /workspace/repo/dev/rig/testrig/id_kairos), the .pub goes into
# iso/secrets/authorized_keys before an image build. Both paths are gitignored.
KEY="$POD_DIR/testrig/id_kairos"
if [ ! -f "$KEY" ]; then
  ssh-keygen -q -t ed25519 -N "" -C "kairos-testrig" -f "$KEY"
  chown "$LOGIN_USER": "$KEY" "$KEY.pub" 2>/dev/null || true
  log "testrig SSH key generated: $KEY (.pub -> iso/secrets/authorized_keys for the next image build)"
else
  log "testrig SSH key present: $KEY"
fi
SECRETS="$REPO_DIR/iso/secrets"
if [ -d "$SECRETS" ] && [ ! -f "$SECRETS/authorized_keys" ]; then
  install -m 600 -o "$LOGIN_USER" "$KEY.pub" "$SECRETS/authorized_keys" 2>/dev/null \
    && log "staged $KEY.pub as $SECRETS/authorized_keys" || true
fi

# ── 3. Groups, work dirs + compose env ──────────────────────────────────────
usermod -aG docker,kvm "$LOGIN_USER" 2>/dev/null && log "user $LOGIN_USER added to docker+kvm groups (relogin to apply)"
for d in "$KAIROS_HOME" "$KAIROS_MODELS" "$KAIROS_KNOWLEDGE" "$KAIROS_BIN" "$KAIROS_ENGINES"; do
  [ -d "$d" ] || { mkdir -p "$d" && chown "$LOGIN_USER": "$d" 2>/dev/null || true; }
done
GRP=$(iommu_group)
ENVF="$POD_DIR/.env"
if [ -f "$ENVF" ] && grep -q '^VFIO_GROUP=' "$ENVF"; then
  sed -i "s/^VFIO_GROUP=.*/VFIO_GROUP=$GRP/" "$ENVF"
else
  echo "VFIO_GROUP=$GRP" >> "$ENVF"
fi
chown "$LOGIN_USER": "$ENVF" 2>/dev/null || true
log "dev/rig/.env: VFIO_GROUP=$GRP (other paths: copy .env.example and edit)"

echo; status; echo
if vfio_bound; then
  log "GPU is vfio-bound. Run the GPU test:  cd $POD_DIR && docker compose run --rm vm"
else
  log "NEXT: reboot, then:  cd $POD_DIR && sudo bash host-setup.sh status   (expect READY=YES)"
  log "      then run the GPU boot-test:     docker compose run --rm vm"
fi
