#!/usr/bin/env bash
# run-gpu-vm.sh [R65] — one command to boot the real-hardware kAIrOS VM with the
# 4070 driving the DESKTOP (GPU-rendered), viewable over VNC at localhost:5900.
# Run on the HOST (needs the 4070 vfio-bound). The GPU-display provisioning runs
# itself once SSH is up. QEMU boot console is on VNC :5901.
set -uo pipefail
HERE=$(cd "$(dirname "$0")/.." && pwd)          # .../dev/rig
IMG=${KAIROS_IMG:-/images/kairos-hwtest-4070.img}
OVL=${KAIROS_OVERLAY:-/images/testrig-overlay-hw.qcow2}
NAME=${VM_NAME:-kairos-user-vm}
# key if the image was built with one, password fallback for pre-R66 images [R66]
SSH_KEY=${KAIROS_SSH_KEY:-/workspace/repo/dev/rig/testrig/id_kairos}   # container path of $HERE/testrig/id_kairos
SSH="sshpass -p ${KAIROS_ROOT_PW:-kairos} ssh -p 2222 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR"
[ -f "$HERE/testrig/id_kairos" ] && SSH="$SSH -i $SSH_KEY -o IdentitiesOnly=yes"
SSH="$SSH root@127.0.0.1"

docker rm -f "$NAME" >/dev/null 2>&1 || true
# the GPU lives on the host nvidia driver by default (boot default flipped 2026-09-12);
# take it for the VM live — fails loudly if a host process still uses it
"$HERE/gpu-to-vfio.sh" || { echo "[run-gpu-vm] GPU not free for passthrough — close whatever uses it (or run dev/rig/gpu-to-host.sh later to return it)"; exit 1; }
echo "[run-gpu-vm] booting real-hardware VM ($NAME)…"
( cd "$HERE" && docker compose run -d --service-ports \
    -e VM_MEM="${VM_MEM:-18G}" -e VM_DISK="${VM_DISK:-110G}" \
    -e KAIROS_IMG="$IMG" -e KAIROS_OVERLAY="$OVL" \
    --name "$NAME" vm /workspace/repo/dev/rig/testrig/vm.sh run ) >/dev/null

echo "[run-gpu-vm] waiting for guest SSH…"
for _ in $(seq 1 90); do
  docker ps -q -f name="$NAME" | grep -q . || { echo "VM exited early"; docker logs "$NAME" 2>&1 | tail -8 | tr -d '\000'; exit 1; }
  docker exec "$NAME" bash -c "$SSH true 2>/dev/null" && break
  sleep 5
done

echo "[run-gpu-vm] exposing the kAIrOS API to the host (:1337 forward; the guest kernel binds loopback by default) [R66]…"
docker exec "$NAME" bash -c "cat /workspace/repo/dev/rig/testrig/expose-ui.sh | $SSH 'sh -s'"

echo "[run-gpu-vm] provisioning the GPU display…"
docker exec "$NAME" bash -c "cat /workspace/repo/dev/rig/testrig/gpu-display-setup.sh | $SSH 'cat >/root/gpu-display-setup.sh && chmod +x /root/gpu-display-setup.sh && /root/gpu-display-setup.sh'"
# the kiosk needs a real VT: let the tty1 autologin bring X up on the GPU
docker exec "$NAME" bash -c "$SSH 'systemctl reset-failed getty@tty1 2>/dev/null; systemctl restart getty@tty1'"
sleep 15
docker exec "$NAME" bash -c "$SSH 'pgrep -x Xorg >/dev/null && echo \"  GPU X: up\"; systemctl restart kairos-x11vnc 2>/dev/null; sleep 2; ss -ltn | grep -q 5900 && echo \"  VNC: serving on :5900\"; nvidia-smi --query-gpu=memory.used --format=csv,noheader | sed \"s/^/  GPU mem: /\"'"
echo "[run-gpu-vm] READY — connect a VNC viewer to localhost:5900 (GPU desktop); boot console on :5901; kAIrOS API on :1337"
echo "[run-gpu-vm] machine-changing API calls from the host need the guest's boot token:  docker exec $NAME bash -c \"$SSH cat /run/kairos/token\"  -> X-Kairos-Token header or http://localhost:1337/?token=…"
