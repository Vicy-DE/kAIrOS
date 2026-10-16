#!/bin/sh
# dist-clean.sh — runs INSIDE the guest right before flatten. Turns the lived-in
# test VM back into a pristine, first-boot-ready appliance for distribution.
set -e
echo "[dist-clean] stopping services that hold state"
systemctl stop kairos kairos-bootanim 2>/dev/null || true

echo "[dist-clean] wiping runtime cache (apps, web pages, generated media, bootanim state)"
rm -rf /opt/vibeos/cache/* 2>/dev/null || true
rm -f  /opt/vibeos/active-session* 2>/dev/null || true

echo "[dist-clean] removing test drop-ins"
rm -f /etc/systemd/system/kairos.service.d/offline-test.conf 2>/dev/null || true

echo "[dist-clean] apt + logs"
apt-get clean 2>/dev/null || true
rm -rf /var/lib/apt/lists/* /var/cache/apt/archives/*.deb 2>/dev/null || true
journalctl --rotate 2>/dev/null || true
journalctl --vacuum-time=1s 2>/dev/null || true
find /var/log -type f -exec truncate -s 0 {} + 2>/dev/null || true

echo "[dist-clean] first-boot markers: regen ssh keys + grow root on the target stick"
rm -f /var/lib/kairos-resized /var/lib/kairos-firstboot-done 2>/dev/null || true
rm -f /etc/ssh/ssh_host_* 2>/dev/null || true

echo "[dist-clean] unique machine-id per flashed device (systemd regenerates on boot)"
: > /etc/machine-id
rm -f /var/lib/dbus/machine-id 2>/dev/null || true
ln -sf /etc/machine-id /var/lib/dbus/machine-id 2>/dev/null || true

echo "[dist-clean] shell history + tmp"
rm -f /root/.bash_history /home/kairos/.bash_history 2>/dev/null || true
rm -rf /tmp/* /var/tmp/* 2>/dev/null || true

echo "[dist-clean] active tier marker"
echo 8gb > /opt/vibeos/active-tier

sync
echo "[dist-clean] done — ready to flatten"
