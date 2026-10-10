#!/bin/sh
# Install the kAIrOS boot screen [R73] into the system this runs in (the image build's
# chroot — hooks/0200 —, a synced testrig guest, or a stick patch). One source for all of
# them, kept in step with the file list in iso/config/includes.chroot (R63 status note).
#   1. the painter binary: the committed static build scripts/splash/kairos-splash (the
#      image has no compiler) — or a fresh build when a compiler is present and the
#      committed binary is missing/older than its source
#   2. exec bits of the initramfs hook + init-top/init-bottom/panic scripts and the ctl
#   3. kairos-splash.service enabled (sysinit.target)
#   4. KAIROS_SPLASH_INITRAMFS=1: update-initramfs -u (the ESP copy follows through the
#      post-update hook -> kairos-esp-sync) — the image build runs its own update-initramfs
# Usage: install.sh [repo-root]   (default: the tree this script lives in)
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=${1:-$(cd "$HERE/../.." && pwd)}
SRC="$HERE/kairos-splash.c"
BIN="$HERE/kairos-splash"
DST=/usr/local/sbin/kairos-splash
if command -v cc >/dev/null 2>&1 && { [ ! -s "$BIN" ] || [ "$SRC" -nt "$BIN" ] || [ "$HERE/logo_data.h" -nt "$BIN" ]; }; then
    sh "$HERE/build.sh" "$BIN"
fi
[ -s "$BIN" ] || { echo "install.sh: no painter binary at $BIN (build it on a host with cc: scripts/splash/build.sh)" >&2; exit 1; }
install -D -m 755 "$BIN" "$DST"
"$DST" --help >/dev/null 2>&1 || { echo "install.sh: $DST does not run here" >&2; exit 1; }
for f in /etc/initramfs-tools/hooks/kairos-splash /etc/initramfs-tools/scripts/init-top/kairos-splash \
         /etc/initramfs-tools/scripts/init-bottom/kairos-splash /etc/initramfs-tools/scripts/panic/kairos-splash \
         /usr/local/sbin/kairos-splash-ctl; do
    [ -f "$f" ] || { echo "install.sh: $f missing (iso/config/includes.chroot not applied?)" >&2; exit 1; }
    chmod 755 "$f"
done
systemctl enable kairos-splash.service >/dev/null 2>&1 || true
if [ "${KAIROS_SPLASH_INITRAMFS:-0}" = 1 ]; then
    update-initramfs -u
fi
echo "kairos-splash installed: $DST ($(wc -c < "$DST") bytes), initramfs hook + scripts, kairos-splash.service enabled"
