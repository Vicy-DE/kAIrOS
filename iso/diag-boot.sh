#!/usr/bin/env bash
# Diagnose why the image panics on the real hybrid (AMD iGPU + NVIDIA 4070) laptop.
set -uo pipefail
GZ=${1:-/mnt/b/kairos-hwtest-4070.img.gz}
TEST=/srv/kairos-test.img
[ -f "$TEST" ] || { echo "expanding…"; gunzip -c "$GZ" > "$TEST"; }
LOOP=$(losetup --find --show -P "$TEST"); sleep 1
trap 'umount /tmp/e /tmp/r 2>/dev/null||true; losetup -d "$LOOP" 2>/dev/null||true' EXIT
mkdir -p /tmp/e /tmp/r
mount "${LOOP}p1" /tmp/e
mount "${LOOP}p2" /tmp/r

echo "===== kernel cmdline (loader entry) ====="
cat /tmp/e/loader/entries/*.conf | grep options

echo "===== initrd: critical disk/usb drivers present? (reliable: unmkinitramfs) ====="
KI=/tmp/e/kairos/initrd
ID=$(mktemp -d); ( cd "$ID" && unmkinitramfs "$KI" . 2>/dev/null )
for m in usb_storage uas xhci_pci xhci_hcd nvme ahci sd_mod ext4 virtio_blk amdgpu; do
   if find "$ID" -name "$m.ko*" 2>/dev/null | grep -q .; then echo "  ✓ $m"; else echo "  ✗ MISSING $m"; fi
done
rm -rf "$ID"

echo "===== initramfs MODULES policy ====="
grep -h "^MODULES" /tmp/r/etc/initramfs-tools/initramfs.conf /tmp/r/etc/initramfs-tools/conf.d/* 2>/dev/null

echo "===== GPU drivers / firmware ====="
ls /tmp/r/usr/lib/firmware/amdgpu/ >/dev/null 2>&1 && echo "  ✓ amdgpu firmware present ($(ls /tmp/r/usr/lib/firmware/amdgpu/ | wc -l) files)" || echo "  ✗ NO amdgpu firmware (iGPU/display may fail)"
echo "  nvidia autoload: $(cat /tmp/r/etc/modules-load.d/nvidia.conf 2>/dev/null | tr '\n' ' ')"
echo "  nouveau blacklisted: $(grep -rl nouveau /tmp/r/etc/modprobe.d/ 2>/dev/null | head -1 || echo NO)"
ls /tmp/r/lib/modules/*/updates/dkms/nvidia.ko* >/dev/null 2>&1 && echo "  ✓ nvidia.ko built" || echo "  ✗ nvidia.ko missing"

echo "===== initrd has amdgpu (for early KMS console)? ====="
lsinitramfs "$KI" 2>/dev/null | grep -qE "/amdgpu\.ko" && echo "  ✓ amdgpu in initrd" || echo "  - amdgpu not in initrd (ok if loaded later)"
echo "DONE"
