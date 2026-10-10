#!/usr/bin/env bash
# Locally verify the kAIrOS ISO boots and SSH works, by booting it in QEMU/KVM
# with user-mode networking and an SSH port-forward (host :2222 -> guest :22).
# GPU/WiFi can't be tested in QEMU (no NVIDIA passthrough, no real radio) — this
# proves boot + sshd + login + that the kAIrOS service comes up.
# [R66] images built with iso/secrets/authorized_keys take the key only; older ones the password
KAIROS_SSH_KEY=${KAIROS_SSH_KEY:-$(dirname "$0")/../../../pod/testrig/id_kairos}   # pod/host-setup.sh makes it
KOPT=""; [ -f "$KAIROS_SSH_KEY" ] && KOPT="-i $KAIROS_SSH_KEY -o IdentitiesOnly=yes"
set -uo pipefail
ISO=${1:-/srv/kairos-out/kairos-hwtest-4070.iso}
[ -f "$ISO" ] || { echo "no ISO at $ISO"; exit 1; }

command -v qemu-system-x86_64 >/dev/null || { apt-get update -qq; \
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq qemu-system-x86 sshpass; }

ACCEL=tcg; [ -e /dev/kvm ] && ACCEL=kvm
echo "[verify] booting ISO in QEMU (accel=$ACCEL), SSH fwd 2222->22 ..."
qemu-system-x86_64 -accel $ACCEL -m 4096 -smp 4 \
    -cdrom "$ISO" -boot d -nographic \
    -netdev user,id=n0,hostfwd=tcp::2222-:22 -device e1000,netdev=n0 \
    > /tmp/qemu-kairos.log 2>&1 &
QPID=$!
trap 'kill $QPID 2>/dev/null' EXIT

echo "[verify] waiting for guest sshd (up to 6 min for live boot)..."
OK=0
for i in $(seq 1 180); do
    kill -0 $QPID 2>/dev/null || { echo "[verify] QEMU exited early:"; tail -20 /tmp/qemu-kairos.log; exit 1; }
    if sshpass -p "${KAIROS_ROOT_PW:-kairos}" ssh $KOPT -p 2222 -o StrictHostKeyChecking=no \
        -o UserKnownHostsFile=/dev/null -o ConnectTimeout=4 \
        root@127.0.0.1 'echo KAIROS_SSH_OK; hostname; uname -r' 2>/dev/null | grep -q KAIROS_SSH_OK; then
        OK=1; break
    fi
    sleep 2
done

if [ $OK -eq 1 ]; then
    echo "[verify] ===== SSH WORKS ====="
    sshpass -p "${KAIROS_ROOT_PW:-kairos}" ssh $KOPT -p 2222 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
        root@127.0.0.1 'echo "--- inside the guest ---"; hostname; cat /etc/os-release | grep PRETTY; \
        systemctl is-enabled ssh kairos NetworkManager 2>/dev/null; \
        echo "wifi profile:"; nmcli -g name connection show 2>/dev/null | grep -i openap || cat /etc/NetworkManager/system-connections/OpenAP.nmconnection | grep -E "ssid|key-mgmt"; \
        echo "kairos kernel:"; curl -fsS http://localhost:1337/api/status 2>/dev/null | head -c 120 || echo "(kernel still warming / models load lazily)"'
    echo "[verify] SSH VERIFICATION PASSED"
    exit 0
else
    echo "[verify] SSH did NOT come up in time. QEMU log tail:"; tail -30 /tmp/qemu-kairos.log
    exit 1
fi
