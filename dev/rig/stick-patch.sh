#!/usr/bin/env bash
# stick-patch.sh — the pattern for patching a flashed kAIrOS stick in place (files + unit enables,
# with backups on the stick), written for the 2026-10-06 logging patch of the v7 image. Run on the
# development machine with the stick plugged in, BEFORE its first boot or any time later:
#     sudo bash dev/rig/stick-patch.sh [/dev/disk/by-id/<stick>]
# The patch bundle is NOT in the repository: KAIROS_STICK_PATCH (default $KAIROS_HOME/stick-patch-2026-10-06)
# must hold files/<path in the image>… + SHA256SUMS. The `put` lines below are that bundle's file list —
# for a new patch copy this script and replace them. The successor for whole-release hotfixes is the
# hotfix kit (apply / --check / --rollback), see docs/how-to/BUILDING.md.
# What this one changes: the boot-log scripts + units (panic text onto the stick, stall capture, honest
# shutdown verdict, emergency cause in the headline), kairos-growroot.service (failure hook) and the
# kAIrOS kernel (job errors + llama-server/download output into the persistent logs). Every file it
# replaces is first copied to <stick>/var/backups/kairos-logging-patch-2026-10-06/.
set -euo pipefail
. "$(dirname "$0")/env.sh"
find_stick(){
  local part disk
  part=$(readlink -f /dev/disk/by-label/KAIROS_ROOT 2>/dev/null) || return 1
  [ -n "$part" ] || return 1
  disk=$(lsblk -no pkname "$part" 2>/dev/null | head -n 1); [ -n "$disk" ] || return 1
  for p in /dev/disk/by-id/*; do case "$p" in *-part[0-9]*) continue ;; esac
    [ "$(readlink -f "$p")" = "/dev/$disk" ] && { printf '%s\n' "$p"; return 0; }; done
  printf '/dev/%s\n' "$disk"
}
DEV=${1:-${KAIROS_STICK:-$(find_stick || true)}}
B=${KAIROS_STICK_PATCH:-$KAIROS_HOME/stick-patch-2026-10-06}
say(){ printf '\033[1;36m[stick-patch]\033[0m %s\n' "$*"; }
die(){ printf '\033[1;31m[stick-patch] %s\033[0m\n' "$*" >&2; exit 1; }
partdev(){ case "$1" in /dev/disk/by-id/*) printf '%s-part%s\n' "$1" "$2" ;; *[0-9]) printf '%sp%s\n' "$1" "$2" ;; *) printf '%s%s\n' "$1" "$2" ;; esac; }

[ "$(id -u)" = 0 ] || die "run it with sudo"
pgrep -x dd >/dev/null && die "a dd is still running (the flash?) — wait until it has finished"
[ -d "$B/files" ] || die "patch bundle not found: $B (KAIROS_STICK_PATCH)"
{ [ -n "$DEV" ] && [ -e "$DEV" ]; } || [ -n "${STICK_ROOT_DIR:-}" ] || die "stick not found (no KAIROS_ROOT partition plugged in? pass the disk as the first argument)"
(cd "$B/files" && sha256sum -c --quiet ../SHA256SUMS) || die "patch bundle $B is damaged"
if [ -n "${STICK_ROOT_DIR:-}" ]; then   # test mode: an already-mounted / replica root tree
  M=$STICK_ROOT_DIR
else
P=$(partdev "$DEV" 2)
if [ ! -e "$P" ]; then   # right after a dd the kernel may not have re-read the new partition table
  blockdev --rereadpt "$(readlink -f "$DEV")" 2>/dev/null || true; udevadm settle; sleep 1
fi
[ -e "$P" ] || die "partition 2 not visible — unplug + re-plug the stick and run again"
[ "$(blkid -s LABEL -o value "$P")" = KAIROS_ROOT ] || die "partition 2 is not KAIROS_ROOT — wrong stick?"

for m in $(findmnt -rn -S "$(readlink -f "$P")" -o TARGET); do say "unmounting desktop mount $m"; umount "$m"; done
M=$(mktemp -d /tmp/kairos-root.XXXXXX)
mount "$P" "$M"
trap 'umount "$M" 2>/dev/null; rmdir "$M" 2>/dev/null' EXIT
fi
[ -f "$M/opt/vibeos/os/kernel/server.py" ] && [ -f "$M/usr/local/sbin/kairos-bootlog" ] || die "not a kAIrOS root file system"
say "kAIrOS root mounted at $M ($(df -h "$M" | awk 'NR==2{print $4" free"}'))"

BK="$M/var/backups/kairos-logging-patch-2026-10-06"; mkdir -p "$BK"
put(){ # put <path in the image> <mode> <uid:gid>
  local dst=$1
  [ -e "$M$dst" ] && [ ! -e "$BK$dst" ] && { mkdir -p "$(dirname "$BK$dst")"; cp -a "$M$dst" "$BK$dst"; }
  install -D -m "$2" -o "${3%:*}" -g "${3#*:}" "$B/files$dst" "$M$dst"
  say "  $dst ($2 $3)"
}
say "installing"
put /usr/local/sbin/kairos-bootlog                    0775 0:0
put /usr/local/sbin/kairos-bootlog-early              0775 0:0
put /etc/systemd/system/kairos-bootlog-early.service  0644 0:0
put /etc/systemd/system/kairos-bootlog-stall.timer    0644 0:0
put /etc/systemd/system/kairos-bootlog-stall.service  0644 0:0
put /etc/systemd/system/kairos-growroot.service       0644 0:0
put /opt/vibeos/os/kernel/server.py                   0775 1000:1000
# = systemctl enable kairos-bootlog-stall.timer (WantedBy=sysinit.target)
ln -sfn /etc/systemd/system/kairos-bootlog-stall.timer "$M/etc/systemd/system/sysinit.target.wants/kairos-bootlog-stall.timer"
say "  sysinit.target.wants/kairos-bootlog-stall.timer -> enabled"
rm -rf "$M/opt/vibeos/os/kernel/__pycache__"          # the kernel recompiles from the new server.py

say "verifying"
(cd "$B/files" && find . -type f | while read -r f; do cmp -s "$f" "$M/${f#./}" || { echo "MISMATCH ${f#.}"; exit 1; }; done) || die "verification failed"
[ "$(readlink "$M/etc/systemd/system/sysinit.target.wants/kairos-bootlog-stall.timer")" = /etc/systemd/system/kairos-bootlog-stall.timer ] || die "timer not enabled"
printf 'kairos logging patch 2026-10-06 applied %s (bundle %s)\n' "$(date '+%F %T %Z')" "$(sha256sum "$B/SHA256SUMS" | cut -c1-16)" > "$BK/APPLIED"
sync
say "done — all files verified; the stick can boot. (Backups of the replaced files: /var/backups/kairos-logging-patch-2026-10-06 on the stick.)"
