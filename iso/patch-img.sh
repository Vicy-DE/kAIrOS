#!/usr/bin/env bash
# Apply the hybrid-GPU + USB-boot fix to an already-expanded raw image and re-gzip it,
# without re-copying the 19GB models. Operates on /srv/kairos-test.img -> /mnt/b/...img.gz
set -euo pipefail
TEST=/srv/kairos-test.img
GZOUT=/mnt/b/kairos-hwtest-4070.img.gz
REPO=$(cd "$(dirname "$0")/.." && pwd)
INC="$REPO/iso/config/includes.chroot"
[ -f "$TEST" ] || { echo "no $TEST — run diag-boot.sh first (it expands the image)"; exit 1; }
exec > >(tee -a /opt/vibeos/img-patch.log) 2>&1
echo "=== patch-img start $(date) ==="

losetup -D 2>/dev/null || true
LOOP=$(losetup --find --show -P "$TEST"); sleep 1
MNT=/mnt/kpatch; mkdir -p "$MNT"
trap 'umount -R "$MNT" 2>/dev/null||true; losetup -d "$LOOP" 2>/dev/null||true' EXIT
mount "${LOOP}p2" "$MNT"
mount "${LOOP}p1" "$MNT/boot/efi"

echo "[patch] boot-path files from the repo tree (same files build-img.sh installs)"
# kairos-gpu-setup + nvidia-load.service (D7: after basic.target, before getty@tty1 —
# never "after multi-user AND before getty", that was an ordering cycle; D11:
# persistenced only when a device is bound), kernel-install off (D4), ESP refresh hooks
install -D -m 755 "$INC/usr/local/sbin/kairos-gpu-setup"           "$MNT/usr/local/sbin/kairos-gpu-setup"
install -D -m 755 "$INC/usr/local/sbin/kairos-esp-sync"            "$MNT/usr/local/sbin/kairos-esp-sync"
install -D -m 644 "$INC/etc/systemd/system/nvidia-load.service"    "$MNT/etc/systemd/system/nvidia-load.service"
install -D -m 644 "$INC/etc/kernel/install.conf"                   "$MNT/etc/kernel/install.conf"
install -D -m 755 "$INC/etc/kernel/postinst.d/zz-kairos-esp"       "$MNT/etc/kernel/postinst.d/zz-kairos-esp"
install -D -m 755 "$INC/etc/initramfs/post-update.d/zz-kairos-esp" "$MNT/etc/initramfs/post-update.d/zz-kairos-esp"
mount --bind /dev "$MNT/dev"; mount --bind /proc "$MNT/proc"; mount --bind /sys "$MNT/sys"

echo "[patch] hybrid-GPU + USB-boot config in chroot"
chroot "$MNT" /bin/bash -e <<'CHROOT'
set -e
rm -f /etc/modules-load.d/nvidia.conf           # no early dGPU load
cat > /etc/initramfs-tools/modules <<M
usb_storage
uas
xhci_pci
xhci_hcd
vmd
nvme_core
nvme
ahci
sd_mod
sdhci_pci
mmc_block
ext4
amdgpu
M
systemctl enable nvidia-load.service
systemctl disable nvidia-persistenced.service 2>/dev/null || true
rm -f /etc/systemd/system/multi-user.target.wants/nvidia-persistenced.service
update-initramfs -u -k all
CHROOT

echo "[patch] refresh ESP kernel/initrd + drop nvidia-drm.modeset, panel (tty1) as the LAST console"
KVER=$(ls "$MNT/lib/modules" | sort -V | tail -1)
cp "$MNT/boot/initrd.img-$KVER" "$MNT/boot/efi/kairos/initrd"
cp "$MNT/boot/vmlinuz-$KVER"    "$MNT/boot/efi/kairos/vmlinuz"
ENTRY="$MNT/boot/efi/loader/entries/kairos.conf"
# build-img.sh emits "console=tty1 ..." (never tty0): the old tty0 pattern was a
# no-op. The last console= is /dev/console (emergency prompts) — must be the panel.
sed -i 's/ nvidia-drm.modeset=1//; s/console=tty1 console=ttyS0,115200/console=ttyS0,115200 console=tty1/' "$ENTRY"
echo "--- patched entry ---"; grep options "$ENTRY"

echo "[patch] loader: default = kairos.conf (entry IDs include .conf), only kAIrOS entries (D4)"
ESP="$MNT/boot/efi"
sed -i 's/^default[[:space:]]\+kairos[[:space:]]*$/default kairos.conf/' "$ESP/loader/loader.conf"
grep -q '^default kairos.conf$' "$ESP/loader/loader.conf" || echo "default kairos.conf" >> "$ESP/loader/loader.conf"
for e in "$ESP"/loader/entries/*.conf; do
  case "$(basename "$e")" in kairos.conf|kairos-serial.conf) ;; *) echo "  removing foreign loader entry $(basename "$e")"; rm -f "$e" ;; esac
done
for d in "$ESP"/*/; do
  d=${d%/}; case "$(basename "$d")" in EFI|kairos|loader|KAIROS-LOGS) ;;
    *) if [ "$(basename "$d" | grep -cE '^[0-9a-f]{32}$')" = 1 ]; then echo "  removing kernel-install tree $(basename "$d")"; rm -rf "$d"; fi ;;
  esac
done
DEF=$(sed -n 's/^default[[:space:]]*//p' "$ESP/loader/loader.conf")
[ "$DEF" = kairos.conf ] && [ -f "$ESP/loader/entries/kairos.conf" ] || { echo "FATAL: loader default '$DEF' is not kairos.conf"; exit 93; }
echo "--- loader entries: $(ls "$ESP"/loader/entries/ | tr '\n' ' ') default $DEF ---"
sync
umount -R "$MNT"; trap - EXIT; losetup -d "$LOOP"

echo "[patch] re-gzip -> $GZOUT"
rm -f "$GZOUT"
gzip -1 -c "$TEST" > "$GZOUT"
sync
ls -lh "$GZOUT"
echo "=== patch-img done $(date) ==="
