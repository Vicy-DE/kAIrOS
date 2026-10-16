#!/usr/bin/env bash
# gpu-to-host.sh — give the NVIDIA GPU back to Linux (nvidia + snd_hda_intel), live.
# Stops any kAIrOS VM container first (it would hold the VFIO group). Needs root (sudo).
# PCI functions: KAIROS_GPU_PCI / KAIROS_GPU_AUDIO_PCI (dev/rig/.env, env.sh defaults).
set -uo pipefail
. "$(dirname "$0")/env.sh"
S(){ if [ "$(id -u)" = 0 ]; then "$@"; else sudo "$@"; fi; }
for c in $(docker ps -q -f name=kairos-user-vm 2>/dev/null); do docker rm -f "$c" >/dev/null; echo "stopped VM container"; done
for D in "$KAIROS_GPU_PCI:nvidia" "$KAIROS_GPU_AUDIO_PCI:snd_hda_intel"; do
  dev=${D%:*}; drv=${D##*:}
  S sh -c "echo $dev > /sys/bus/pci/devices/$dev/driver/unbind" 2>/dev/null
  S sh -c "echo $drv > /sys/bus/pci/devices/$dev/driver_override"
done
S modprobe nvidia; S modprobe nvidia_uvm; S modprobe nvidia_modeset; S modprobe nvidia_drm
for dev in "$KAIROS_GPU_PCI" "$KAIROS_GPU_AUDIO_PCI"; do S sh -c "echo $dev > /sys/bus/pci/drivers_probe"; done
sleep 2
# persistenced keeps a reference on nvidia.ko so nothing can cascade-unload it (see R-AE)
S systemctl start nvidia-persistenced 2>/dev/null || true
S systemctl start nvidia-powerd 2>/dev/null || true   # stopped by gpu-to-vfio.sh (Dynamic Boost)
nvidia-smi --query-gpu=name,memory.used,memory.total --format=csv,noheader 2>/dev/null \
  && echo "GPU -> host nvidia driver" \
  || { echo "rebind to nvidia failed:"; lspci -nnk -d 10de: | grep "driver in use"; exit 1; }
