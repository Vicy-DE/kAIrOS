#!/usr/bin/env bash
# Diagnose the .img.gz boot: inspect ESP (systemd-boot files + loader entry) and root,
# then add a serial console to the loader entry for a visible re-test.
set -uo pipefail
GZ=${1:-/mnt/b/kairos-hwtest-4070.img.gz}
TEST=/srv/kairos-test.img
[ -f "$TEST" ] || { echo "re-expanding image…"; gunzip -c "$GZ" > "$TEST"; truncate -s 40G "$TEST"; }

LOOP=$(losetup --find --show -P "$TEST"); sleep 1
trap 'losetup -d "$LOOP" 2>/dev/null||true' EXIT
E=$(mktemp -d); R=$(mktemp -d)
mount "${LOOP}p1" "$E"
echo "===== ESP CONTENTS ====="
find "$E" -maxdepth 3 -type f | sed "s|$E||" | sort
echo "===== loader.conf ====="; cat "$E/loader/loader.conf" 2>/dev/null
echo "===== entry ====="; cat "$E/loader/entries/"*.conf 2>/dev/null
echo "===== fallback boot present? ====="
ls -la "$E/EFI/BOOT/BOOTX64.EFI" 2>/dev/null && echo "  fallback OK" || echo "  ✗ NO /EFI/BOOT/BOOTX64.EFI (firmware won't auto-boot!)"
ls -la "$E/EFI/systemd/systemd-bootx64.efi" 2>/dev/null && echo "  systemd-boot OK" || echo "  ✗ NO systemd-bootx64.efi"

echo "===== add serial console to entry (for visible re-test) ====="
for f in "$E"/loader/entries/*.conf; do
    [ -f "$f" ] || continue
    grep -q ttyS0 "$f" || sed -i 's/console=tty1/console=ttyS0,115200 console=tty1/' "$f"
    echo "--- $f ---"; cat "$f"
done

mount "${LOOP}p2" "$R" 2>/dev/null || true
echo "===== root /boot ====="; ls "$R/boot" 2>/dev/null | head
echo "===== fstab ====="; cat "$R/etc/fstab" 2>/dev/null
echo "===== root= target exists in initrd? (virtio/usb modules) ====="
KIMG=$(ls "$R"/boot/initrd.img-* 2>/dev/null | head -1)
[ -n "$KIMG" ] && lsinitramfs "$KIMG" 2>/dev/null | grep -cE "virtio_blk|virtio_pci|usb_storage|ext4" | xargs echo "  matching driver modules in initrd:"
sync
umount "$E" "$R" 2>/dev/null || true
rmdir "$E" "$R" 2>/dev/null || true
losetup -d "$LOOP"; trap - EXIT
echo "DONE — serial console added; re-run verify-img-qemu.sh (it reuses $TEST)"
