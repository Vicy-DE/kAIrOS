#!/bin/sh
# esp-automount-fstab.sh [R51, v7.5] — turn the EFI partition's /etc/fstab line of an EXISTING
# kAIrOS system (a v7.3 build guest, before the repo sync) into the automount that
# iso/build-img.sh and kairos-install now write:
#   umask=0077,nofail,x-systemd.automount,x-systemd.idle-timeout=120,x-systemd.device-timeout=45
# (mounted on first access — systemd waits for the device and runs fsck —, unmounted after 120 s
# idle; the boot never waits on it: the old x-systemd.device-timeout=10 gave up on the stick's slow
# udev coldplug: the WARN of the v7.3 stick run). Only the line whose mount point is
# /boot/efi (vfat) changes; its source (UUID=…) and fsck pass stay. Idempotent; the first run keeps
# the original as <fstab>.pre-v75. Run as root in the guest:  sh esp-automount-fstab.sh
# (or: ssh … 'sh -s' < iso/esp-automount-fstab.sh). Optional argument: another fstab (tests).
set -eu
FSTAB=${1:-/etc/fstab}
OPTS=umask=0077,nofail,x-systemd.automount,x-systemd.idle-timeout=120,x-systemd.device-timeout=45
awk -v o="$OPTS" '$1 !~ /^#/ && $2 == "/boot/efi" && $3 == "vfat" { $4 = o } { print }' "$FSTAB" > "$FSTAB.v75"
grep -Eq "^[^#].*[[:space:]]/boot/efi[[:space:]]+vfat[[:space:]]+$OPTS[[:space:]]+0[[:space:]]+[02]\$" "$FSTAB.v75" ||
  { echo "esp-automount-fstab: no /boot/efi vfat line in $FSTAB — unchanged" >&2; rm -f "$FSTAB.v75"; exit 1; }
[ "$(grep -vc /boot/efi "$FSTAB")" = "$(grep -vc /boot/efi "$FSTAB.v75")" ] ||
  { echo "esp-automount-fstab: other lines would change — unchanged" >&2; rm -f "$FSTAB.v75"; exit 1; }
[ -e "$FSTAB.pre-v75" ] || cp -p "$FSTAB" "$FSTAB.pre-v75"      # the first run keeps the original
mv "$FSTAB.v75" "$FSTAB"
[ "$FSTAB" = /etc/fstab ] && command -v systemctl >/dev/null 2>&1 && systemctl daemon-reload
grep /boot/efi "$FSTAB"
