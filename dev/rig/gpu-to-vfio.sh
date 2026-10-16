#!/usr/bin/env bash
# gpu-to-vfio.sh — hand the NVIDIA GPU (VGA+audio) to vfio-pci for a VM, live.
# Works without a reboot only when the host display runs on another GPU (the reference laptop's
# AMD iGPU); it fails if a host process still holds /dev/nvidia* (close it and retry).
# Needs root (sudo). PCI functions: KAIROS_GPU_PCI / KAIROS_GPU_AUDIO_PCI (dev/rig/.env, env.sh defaults).
set -uo pipefail
. "$(dirname "$0")/env.sh"
S(){ if [ "$(id -u)" = 0 ]; then "$@"; else sudo "$@"; fi; }
# nvidia-persistenced is the OS-side holder of nvidia.ko, not a user of the GPU: stop it first
S systemctl stop nvidia-persistenced 2>/dev/null || true
# nvidia-powerd (Dynamic Boost, laptops) is the same kind of OS-side holder: it keeps /dev/nvidia0 open
# as root, invisible to user-level checks (2026-09-29) — gpu-to-host.sh starts it again
S systemctl stop nvidia-powerd 2>/dev/null || true
if S fuser /dev/nvidia0 >/dev/null 2>&1; then
  echo "GPU busy on the host:"; S fuser -v /dev/nvidia0 2>&1 | tail -n +2; exit 1
fi
S rmmod nvidia_drm nvidia_modeset nvidia_uvm nvidia 2>/dev/null || true
S modprobe vfio-pci
for dev in "$KAIROS_GPU_PCI" "$KAIROS_GPU_AUDIO_PCI"; do
  S sh -c "echo $dev > /sys/bus/pci/devices/$dev/driver/unbind" 2>/dev/null
  S sh -c "echo vfio-pci > /sys/bus/pci/devices/$dev/driver_override"
  S sh -c "echo $dev > /sys/bus/pci/drivers_probe"
done
sleep 1
lspci -nnk -d 10de: | grep -q "vfio-pci" && ls /dev/vfio/ | grep -qE '^[0-9]+$' \
  && echo "GPU -> vfio-pci (group $(ls /dev/vfio | grep -E '^[0-9]+$' | head -1)) — a VM can take it" \
  || { echo "rebind to vfio failed:"; lspci -nnk -d 10de: | grep "driver in use"; exit 1; }
