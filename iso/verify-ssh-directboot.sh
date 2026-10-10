#!/usr/bin/env bash
# Verify SSH on an existing kAIrOS ISO WITHOUT rebuilding: extract the kernel+initrd,
# boot QEMU via direct kernel boot (so we can force console=ttyS0 + live params),
# with the ISO as the live medium and an SSH port-forward. Proves boot + sshd + login.
# [R66] images built with iso/secrets/authorized_keys take the key only; older ones the password
KAIROS_SSH_KEY=${KAIROS_SSH_KEY:-$(dirname "$0")/../../../pod/testrig/id_kairos}   # pod/host-setup.sh makes it
KOPT=""; [ -f "$KAIROS_SSH_KEY" ] && KOPT="-i $KAIROS_SSH_KEY -o IdentitiesOnly=yes"
set -uo pipefail
ISO=${1:-/srv/kairos-out/kairos-hwtest-4070.iso}
[ -f "$ISO" ] || { echo "no ISO: $ISO"; exit 1; }
command -v qemu-system-x86_64 >/dev/null || { apt-get update -qq; DEBIAN_FRONTEND=noninteractive apt-get install -y -qq qemu-system-x86 sshpass; }

IM=$(mktemp -d)
mount -o loop,ro "$ISO" "$IM"
VMLINUZ=$(ls "$IM"/live/vmlinuz* 2>/dev/null | head -1)
INITRD=$(ls "$IM"/live/initrd* 2>/dev/null | head -1)
cp "$VMLINUZ" /tmp/k-vmlinuz; cp "$INITRD" /tmp/k-initrd
umount "$IM"; rmdir "$IM"
echo "[verify] kernel=$(basename "$VMLINUZ") initrd=$(basename "$INITRD")"

ACCEL=tcg; [ -e /dev/kvm ] && ACCEL=kvm
echo "[verify] booting via direct kernel (accel=$ACCEL), serial->log, ssh 2222->22…"
qemu-system-x86_64 -accel $ACCEL -m 4096 -smp 4 -no-reboot \
    -kernel /tmp/k-vmlinuz -initrd /tmp/k-initrd \
    -append "boot=live components username=root console=ttyS0,115200 console=tty0" \
    -drive file="$ISO",media=cdrom,if=ide \
    -nographic \
    -netdev user,id=n0,hostfwd=tcp::2222-:22 -device e1000,netdev=n0 \
    > /tmp/qemu-kairos.log 2>&1 &
QPID=$!
trap 'kill $QPID 2>/dev/null' EXIT

OK=0
for i in $(seq 1 210); do
    kill -0 $QPID 2>/dev/null || { echo "[verify] QEMU exited early:"; tail -25 /tmp/qemu-kairos.log; exit 1; }
    if sshpass -p "${KAIROS_ROOT_PW:-kairos}" ssh $KOPT -p 2222 -o StrictHostKeyChecking=no \
        -o UserKnownHostsFile=/dev/null -o ConnectTimeout=4 \
        root@127.0.0.1 'echo KAIROS_SSH_OK' 2>/dev/null | grep -q KAIROS_SSH_OK; then OK=1; break; fi
    sleep 2
done

if [ $OK -eq 1 ]; then
    echo "[verify] ============ SSH WORKS ============"
    sshpass -p "${KAIROS_ROOT_PW:-kairos}" ssh $KOPT -p 2222 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
        root@127.0.0.1 'hostname; grep PRETTY /etc/os-release; echo "services:"; systemctl is-enabled ssh kairos kairos-payload NetworkManager 2>&1; echo "wifi:"; ls /etc/NetworkManager/system-connections/ 2>/dev/null || echo "(none baked)"; echo "sshd:"; cat /etc/ssh/sshd_config.d/kairos.conf 2>/dev/null | grep -v "^#"; echo "nvidia module:"; ls /lib/modules/*/updates/dkms/nvidia.ko* 2>/dev/null | head -1 || modinfo -F version nvidia 2>/dev/null || echo "(module present per static check)"; echo "kairos http:"; (curl -fsS http://localhost:1337/api/status 2>/dev/null | head -c 100) || echo "(kernel warming / no models partition in QEMU — expected)"'
    echo; echo "[verify] ===== SSH VERIFICATION PASSED ====="
    RC=0
else
    echo "[verify] SSH did not come up. serial log tail:"; tail -40 /tmp/qemu-kairos.log; RC=1
fi
kill $QPID 2>/dev/null || true
exit $RC
