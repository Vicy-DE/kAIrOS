#!/usr/bin/env bash
# Highest-signal image test: extract the image's kernel+initrd and DIRECT-BOOT them in
# QEMU (no OVMF/systemd-boot) against the image's own root partition, with serial visible.
# Proves the rootfs/initrd/root-mount/networking work. Then a separate OVMF run proves
# systemd-boot. Tight timeout + full serial so failures are diagnosable.
# [R66] images built with iso/secrets/authorized_keys take the key only; older ones the password
KAIROS_SSH_KEY=${KAIROS_SSH_KEY:-$(dirname "$0")/../../../pod/testrig/id_kairos}   # pod/host-setup.sh makes it
KOPT=""; [ -f "$KAIROS_SSH_KEY" ] && KOPT="-i $KAIROS_SSH_KEY -o IdentitiesOnly=yes"
set -uo pipefail
GZ=${1:-/mnt/b/kairos-hwtest-4070.img.gz}
TEST=/srv/kairos-test.img
[ -f "$TEST" ] || { echo "expanding image…"; gunzip -c "$GZ" > "$TEST"; truncate -s 40G "$TEST"; }

LOOP=$(losetup --find --show -P "$TEST"); sleep 1
PU=$(blkid -s PARTUUID -o value "${LOOP}p2")
E=$(mktemp -d); mount "${LOOP}p1" "$E"
cp "$E"/kairos/vmlinuz /tmp/img-vmlinuz
cp "$E"/kairos/initrd  /tmp/img-initrd
umount "$E"; rmdir "$E"
losetup -d "$LOOP"
echo "[verify] root PARTUUID=$PU; kernel+initrd extracted"

ACCEL=tcg; [ -e /dev/kvm ] && ACCEL=kvm
echo "[verify] direct-kernel boot (accel=$ACCEL), e1000 net, serial visible…"
qemu-system-x86_64 -accel $ACCEL -m 4096 -smp 4 -no-reboot \
  -kernel /tmp/img-vmlinuz -initrd /tmp/img-initrd \
  -append "root=PARTUUID=$PU rw rootwait console=ttyS0,115200 systemd.show_status=1" \
  -drive file="$TEST",format=raw,if=virtio \
  -nographic \
  -netdev user,id=n0,hostfwd=tcp::2224-:22 -device e1000,netdev=n0 \
  > /tmp/qemu-direct.log 2>&1 &
QPID=$!
trap 'kill $QPID 2>/dev/null' EXIT

OK=0
for i in $(seq 1 150); do
  kill -0 $QPID 2>/dev/null || { echo "[verify] QEMU exited:"; tail -30 /tmp/qemu-direct.log; break; }
  if sshpass -p "${KAIROS_ROOT_PW:-kairos}" ssh $KOPT -p 2224 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
       -o ConnectTimeout=4 root@127.0.0.1 'echo OK' 2>/dev/null | grep -q OK; then OK=1; break; fi
  sleep 2
done

if [ $OK -eq 1 ]; then
  echo "[verify] ===== DIRECT BOOT + SSH OK ====="
  sshpass -p "${KAIROS_ROOT_PW:-kairos}" ssh $KOPT -p 2224 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null root@127.0.0.1 '
    echo "root fs: $(findmnt -no SOURCE,FSTYPE /)"; df -h / | tail -1;
    echo "resized: $(test -f /var/lib/kairos-resized && echo yes || echo pending)";
    echo "disk size seen: $(lsblk -dno SIZE /dev/vda 2>/dev/null)";
    echo "services: $(systemctl is-enabled ssh kairos kairos-growroot NetworkManager 2>/dev/null|tr "\n" " ")";
    echo "kairos http: $(curl -fsS http://localhost:1337/api/status 2>/dev/null | head -c 90 || echo warming)"'
  echo "[verify] DIRECT BOOT PASSED"
else
  echo "[verify] no SSH. SERIAL LOG (last 50):"; tail -50 /tmp/qemu-direct.log | tr -d '\000'
fi
kill $QPID 2>/dev/null||true
