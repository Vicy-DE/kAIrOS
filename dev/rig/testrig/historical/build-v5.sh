#!/usr/bin/env bash
# HISTORICAL (dev/rig/testrig/historical): kept as a record of how earlier images were provisioned;
# it needs artefacts that are not in this repository (see the header below). Paths are env-overridable:
# KAIROS_REPO (repo in the container), KAIROS_SSH_KEY, KAIROS_FIXES (dir with the binary fix bundles).
# build-v5.sh — provision a BOOTED guest into the v5 shape [R46-R50]:
#   * trim add-on image models (keep only the default Krea 2) -> shrink < 58 GiB
#   * install installer + driver packages (gdisk/dosfstools/rsync/systemd-boot,
#     firmware-intel-graphics, iw, rfkill)
#   * deploy kairos-install + the boot_vga-aware gpu-setup
#   * final repo sync
# Run inside the testrig container while the VM is up: build-v5.sh [ssh_port]
set -euo pipefail
PORT=${1:-2222}
REPO=${KAIROS_REPO:-/workspace/repo}
# key-built images accept only testrig/id_kairos; pre-R66 images take the password [R66]
SSH_KEY=${KAIROS_SSH_KEY:-$(dirname "$0")/../id_kairos}
SSH="sshpass -p ${KAIROS_ROOT_PW:-kairos} ssh -p $PORT -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR"
[ -f "$SSH_KEY" ] && SSH="$SSH -i $SSH_KEY -o IdentitiesOnly=yes"
SSH="$SSH root@127.0.0.1"
log(){ printf '\033[1;32m[build-v5]\033[0m %s\n' "$*"; }

log "1/6 trim add-on image models (keep Krea default + its encoder + VAE)"
$SSH 'cd /opt/vibeos/models/image
du -sh . ; echo "--- removing add-ons ---"
rm -fv Qwen_Image-Q2_K.gguf Qwen2.5-VL-7B-Instruct.Q4_K_S.gguf qwen_image_vae.safetensors \
       Chroma1-HD-Q4_K_S.gguf t5-v1_1-xxl-encoder-Q4_K_M.gguf \
       z_image_turbo-Q4_0.gguf Qwen3-4B-Instruct-2507-Q4_K_M.gguf ae.safetensors
echo "--- kept ---"; ls -la; du -sh .'

log "2/6 repo sync (kernel/shell/manifests/docs/tests with R46-R50)"
tar -C "$REPO" -czf - kernel shell manifests scripts docs tests | $SSH 'tar -C /opt/vibeos/os -xzf - && echo synced'

log "3/6 installer + driver packages"
$SSH 'export DEBIAN_FRONTEND=noninteractive
apt-get update -qq 2>&1 | tail -1 || true
apt-get install -y -qq gdisk dosfstools rsync systemd-boot iw rfkill firmware-intel-graphics 2>&1 | tail -2 || \
  apt-get install -y -qq gdisk dosfstools rsync iw rfkill 2>&1 | tail -2
command -v sgdisk && command -v mkfs.vfat && command -v rsync && command -v bootctl && echo "installer deps OK"'

log "4/6 deploy kairos-install"
tar -C "$REPO/iso/config/includes.chroot" -czf - usr/local/sbin/kairos-install \
  | $SSH 'tar -C / -xzf - && chmod +x /usr/local/sbin/kairos-install && /usr/local/sbin/kairos-install --list >/dev/null && echo "kairos-install ready"'

log "5/6 boot_vga-aware GPU setup"
$SSH 'cat > /usr/local/sbin/kairos-gpu-setup <<"G"
#!/bin/sh
nvidia_boot_vga=0
for d in /sys/bus/pci/devices/*/boot_vga; do
  [ -r "$d" ] || continue
  [ "$(cat "$d" 2>/dev/null)" = "1" ] || continue
  [ "$(cat "$(dirname "$d")/vendor" 2>/dev/null)" = "0x10de" ] && nvidia_boot_vga=1
done
modprobe nvidia 2>/dev/null || exit 0
modprobe nvidia_uvm 2>/dev/null || true
if [ "$nvidia_boot_vga" = "1" ]; then
  modprobe nvidia_modeset 2>/dev/null || true
  modprobe nvidia_drm modeset=1 2>/dev/null || true
fi
exit 0
G
chmod +x /usr/local/sbin/kairos-gpu-setup && echo gpu-setup-updated'

log "6/6 expose the API to the host [R66], restart kAIrOS + verify new endpoints"
$SSH 'sh -s' < "$(dirname "$0")/expose-ui.sh"
$SSH 'systemctl restart kairos && sleep 5
curl -sf http://127.0.0.1:1337/api/models | python3 -c "import json,sys; d=json.load(sys.stdin); print(\"models:\", [(m[\"key\"], m[\"present\"], m[\"addon\"]) for m in d[\"models\"]])"
curl -sf http://127.0.0.1:1337/api/install/targets | head -c 200; echo
df -h / | tail -1'
log "done"
