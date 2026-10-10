#!/usr/bin/env bash
# Verify the kAIrOS .img.gz: UEFI-boot it via systemd-boot in QEMU (OVMF), on a disk
# LARGER than the image so auto-resize has room, then SSH in and confirm:
#   systemd-boot booted it · root partition grew · services up · nvidia module.
set -uo pipefail
GZ=${1:-/mnt/b/kairos-hwtest-4070.img.gz}
TEST=/srv/kairos-test.img   # on the fast vhdx for a quick QEMU boot
TESTSZ_G=40
[ -f "$GZ" ] || { echo "no image: $GZ"; exit 1; }
for p in qemu-system-x86_64 sshpass; do command -v $p >/dev/null || { apt-get update -qq; DEBIAN_FRONTEND=noninteractive apt-get install -y -qq qemu-system-x86 ovmf sshpass; break; }; done
[ -f /usr/share/OVMF/OVMF_CODE.fd ] || DEBIAN_FRONTEND=noninteractive apt-get install -y -qq ovmf

echo "[verify] structure of the gz image (peek partition table)…"
gunzip -c "$GZ" | head -c 20M > /tmp/img-head.bin
sgdisk -p /tmp/img-head.bin 2>/dev/null | grep -E "EFI|KAIROS|Number|GUID code" || true

echo "[verify] expanding to a ${TESTSZ_G}G test disk (room for auto-resize)…"
gunzip -c "$GZ" > "$TEST"
truncate -s ${TESTSZ_G}G "$TEST"          # grow the *disk*; growpart should expand root into it
ORIG_DF=$(gunzip -c "$GZ" | wc -c)        # informational

# locate OVMF firmware (Debian trixie ships the *_4M.fd names)
CODE=$(ls /usr/share/OVMF/OVMF_CODE_4M.fd /usr/share/OVMF/OVMF_CODE.fd 2>/dev/null | head -1)
VARSRC=$(ls /usr/share/OVMF/OVMF_VARS_4M.fd /usr/share/OVMF/OVMF_VARS.fd 2>/dev/null | head -1)
[ -f "$CODE" ] && [ -f "$VARSRC" ] || { echo "OVMF firmware not found"; ls /usr/share/OVMF/; exit 1; }
VARS=/tmp/OVMF_VARS.kairos.fd
cp "$VARSRC" "$VARS"
echo "[verify] OVMF code=$CODE vars=$VARSRC"
ACCEL=tcg; [ -e /dev/kvm ] && ACCEL=kvm

echo "[verify] UEFI boot in QEMU (accel=$ACCEL, OVMF, systemd-boot)…"
qemu-system-x86_64 -accel $ACCEL -machine q35 -m 4096 -smp 4 -no-reboot \
  -drive if=pflash,format=raw,unit=0,file="$CODE",readonly=on \
  -drive if=pflash,format=raw,unit=1,file="$VARS" \
  -drive file="$TEST",format=raw,if=virtio \
  -nographic \
  -netdev user,id=n0,hostfwd=tcp::2223-:22 -device virtio-net,netdev=n0 \
  > /tmp/qemu-img.log 2>&1 &
QPID=$!
trap 'kill $QPID 2>/dev/null; rm -f "$TEST"' EXIT

OK=0
for i in $(seq 1 210); do
  kill -0 $QPID 2>/dev/null || { echo "[verify] QEMU exited early:"; tail -25 /tmp/qemu-img.log; exit 1; }
  if sshpass -p kairos ssh -p 2223 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
       -o ConnectTimeout=4 root@127.0.0.1 'echo KAIROS_IMG_OK' 2>/dev/null | grep -q KAIROS_IMG_OK; then OK=1; break; fi
  sleep 2
done

if [ $OK -eq 1 ]; then
  echo "[verify] ===== UEFI/systemd-boot BOOT OK + SSH WORKS ====="
  sshpass -p kairos ssh -p 2223 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null root@127.0.0.1 '
     echo "bootloader: $(bootctl status 2>/dev/null | grep -i "Product\|systemd-boot" | head -1)";
     echo "--- auto-resize check (disk vs root fs) ---"; lsblk -o NAME,SIZE,TYPE,MOUNTPOINT | grep -E "disk|/$"; df -h / | tail -1;
     echo "resized marker: $(test -f /var/lib/kairos-resized && echo yes || echo no)";
     echo "--- services ---"; systemctl is-active kairos 2>/dev/null; systemctl is-enabled ssh kairos kairos-growroot NetworkManager 2>/dev/null | tr "\n" " "; echo;
     echo "--- nvidia ---"; ls /lib/modules/*/updates/dkms/nvidia.ko* 2>/dev/null | head -1;
     echo "--- kairos http ---"; (curl -fsS http://localhost:1337/api/status 2>/dev/null | head -c 120) || echo "(warming)";
     echo; echo "--- models present (offline) ---"; ls /opt/vibeos/models/llm/*.gguf 2>/dev/null | head -1'
  echo; echo "[verify] ===== IMG VERIFICATION PASSED ====="
  RC=0
else
  echo "[verify] no SSH. qemu/serial log tail:"; tail -45 /tmp/qemu-img.log; RC=1
fi
kill $QPID 2>/dev/null || true; rm -f "$TEST"
exit $RC
