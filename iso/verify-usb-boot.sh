#!/usr/bin/env bash
# Decisive laptop-scenario test: boot the image as a USB MASS-STORAGE device through
# OVMF (UEFI) + systemd-boot, so the initrd must load xHCI + USB-storage to mount root
# from USB — exactly what the real laptop does. Confirms the panic cause is gone.
# [R66] images built with iso/secrets/authorized_keys take the key only; older ones the password
KAIROS_SSH_KEY=${KAIROS_SSH_KEY:-$(dirname "$0")/../../../pod/testrig/id_kairos}   # pod/host-setup.sh makes it
KOPT=""; [ -f "$KAIROS_SSH_KEY" ] && KOPT="-i $KAIROS_SSH_KEY -o IdentitiesOnly=yes"
set -uo pipefail
GZ=${1:-/mnt/b/kairos-hwtest-4070.img.gz}
TEST=/srv/kairos-usbtest.img
echo "[verify] expanding to a 40G USB test disk…"
gunzip -c "$GZ" > "$TEST"; truncate -s 40G "$TEST"

CODE=$(ls /usr/share/OVMF/OVMF_CODE_4M.fd /usr/share/OVMF/OVMF_CODE.fd 2>/dev/null | head -1)
cp "$(ls /usr/share/OVMF/OVMF_VARS_4M.fd /usr/share/OVMF/OVMF_VARS.fd | head -1)" /tmp/OVMF_VARS.usb.fd
ACCEL=tcg; [ -e /dev/kvm ] && ACCEL=kvm

echo "[verify] UEFI boot, image attached as USB (xhci+usb-storage), accel=$ACCEL…"
qemu-system-x86_64 -accel $ACCEL -machine q35 -m 4096 -smp 4 -no-reboot \
  -drive if=pflash,format=raw,unit=0,file="$CODE",readonly=on \
  -drive if=pflash,format=raw,unit=1,file=/tmp/OVMF_VARS.usb.fd \
  -drive id=usbstick,file="$TEST",format=raw,if=none \
  -device qemu-xhci,id=xhci \
  -device usb-storage,bus=xhci.0,drive=usbstick \
  -nographic \
  -netdev user,id=n0,hostfwd=tcp::2225-:22 -device e1000,netdev=n0 \
  > /tmp/qemu-usb.log 2>&1 &
QPID=$!
trap 'kill $QPID 2>/dev/null; rm -f "$TEST"' EXIT

OK=0
for i in $(seq 1 210); do
  kill -0 $QPID 2>/dev/null || { echo "[verify] QEMU exited:"; tail -25 /tmp/qemu-usb.log|tr -d '\000'; break; }
  if sshpass -p "${KAIROS_ROOT_PW:-kairos}" ssh $KOPT -p 2225 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
       -o ConnectTimeout=4 root@127.0.0.1 'echo OK' 2>/dev/null | grep -q OK; then OK=1; break; fi
  sleep 2
done

if [ $OK -eq 1 ]; then
  echo "[verify] ===== UEFI + USB-BOOT + SSH OK (laptop path) ====="
  sshpass -p "${KAIROS_ROOT_PW:-kairos}" ssh $KOPT -p 2225 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null root@127.0.0.1 '
    echo "root: $(findmnt -no SOURCE,FSTYPE /)  (booted from USB)";
    echo "USB host driver loaded: $(lsmod | grep -oE "^xhci_pci|^xhci_hcd|^usb_storage|^uas" | tr "\n" " ")";
    df -h / | tail -1; echo "resized: $(test -f /var/lib/kairos-resized && echo yes || echo pending)";
    echo "nvidia early-loaded (should be empty at boot): $(lsmod | grep -c nvidia)";
    echo "nvidia-load.service: $(systemctl is-enabled nvidia-load 2>/dev/null)";
    echo "kairos: $(systemctl is-active kairos)"'
  echo "[verify] ===== LAPTOP-PATH VERIFICATION PASSED ====="
  RC=0
else
  echo "[verify] FAILED. serial log (last 50):"; tail -50 /tmp/qemu-usb.log | tr -d '\000'; RC=1
fi
kill $QPID 2>/dev/null||true; rm -f "$TEST"
exit $RC
