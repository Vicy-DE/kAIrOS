#!/usr/bin/env bash
# vm.sh — boot the kAIrOS hardware-test image in QEMU/KVM inside the testrig
# container (dev/rig), optionally with the real NVIDIA GPU passed through via VFIO.
#
# The pristine .img is NEVER touched: all writes go to a qcow2 overlay whose
# virtual size is larger than the backing image, so the guest's first-boot
# auto-resize (growpart+resize2fs) gets room to prove itself.
#
# Usage (inside the container — from the host: cd dev/rig && docker compose run --rm dev|vm \
#        /workspace/repo/dev/rig/testrig/vm.sh <mode>; the baked /rig/vm.sh copy may be stale):
#   vm.sh test    # headless boot -> SSH checks (autoresize/kairos/GPU) -> report
#   vm.sh run     # interactive: serial console on stdio + VNC on :5900
#   vm.sh fresh   # discard the overlay (next boot is a factory-fresh first boot)
#   vm.sh smoke   # like test, plus a real image-generation smoke on the GPU
#
# Env overrides (container paths; /images = KAIROS_HOME of dev/rig/.env):
#   KAIROS_IMG=/images/kairos-hwtest-4070.img   pristine backing image (raw, gunzipped build-img.sh output)
#   KAIROS_OVERLAY=/images/testrig-overlay.qcow2 the qcow2 overlay all writes go to
#   KAIROS_SSH_KEY=<this dir>/id_kairos         root key for the guest (generated here if missing — its
#                                               .pub must be in iso/secrets/authorized_keys of the image)
#   KAIROS_GPU_PCI=0000:01:00.0 KAIROS_GPU_AUDIO_PCI=0000:01:00.1  the vfio-bound GPU (VGA + audio function)
#   VM_MEM=16G  VM_CPUS=8  VM_DISK=60G          guest sizing (VM_DISK: the overlay is never made smaller than the image)
#   VM_GPU=auto|on|off                          VFIO passthrough (auto: on if /dev/vfio has a group)
#   VM_TIMEOUT=420                              seconds to wait for SSH
set -uo pipefail

IMG=${KAIROS_IMG:-/images/kairos-hwtest-4070.img}
OVERLAY=${KAIROS_OVERLAY:-/images/testrig-overlay.qcow2}
MEM=${VM_MEM:-16G}
CPUS=${VM_CPUS:-8}
DISK=${VM_DISK:-60G}
GPU=${VM_GPU:-auto}
TIMEOUT=${VM_TIMEOUT:-420}
SSH_PORT=2222
NV_VGA=${KAIROS_GPU_PCI:-0000:01:00.0}
NV_AUD=${KAIROS_GPU_AUDIO_PCI:-0000:01:00.1}
CONSOLE=/images/testrig-console.log
MODE=${1:-test}

log(){ printf '\033[1;36m[testrig]\033[0m %s\n' "$*"; }
fail(){ printf '\033[1;31m[testrig] FAIL:\033[0m %s\n' "$*" >&2; }
# guest SSH [R66]: images built with iso/secrets/authorized_keys accept only the key
# (KAIROS_SSH_KEY, default <this dir>/id_kairos — make-key.sh / host-setup.sh generate it, and so does
# this script on first use); pre-R66 images still take the password, so sshpass stays as the
# fallback. Both in one call. The key is gitignored and never part of the repository.
SSH_KEY=${KAIROS_SSH_KEY:-$(cd "$(dirname "$0")" && pwd)/id_kairos}
if [ ! -f "$SSH_KEY" ] && command -v ssh-keygen >/dev/null 2>&1; then
  if ssh-keygen -q -t ed25519 -N "" -C "kairos-testrig" -f "$SSH_KEY" 2>/dev/null; then
    log "generated the rig's SSH key $SSH_KEY — an image only accepts it when $SSH_KEY.pub was in iso/secrets/authorized_keys at build time"
  fi
fi
SSH_OPTS=(-p "$SSH_PORT" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null
          -o ConnectTimeout=5 -o LogLevel=ERROR)
[ -f "$SSH_KEY" ] && SSH_OPTS+=(-i "$SSH_KEY" -o IdentitiesOnly=yes)
gssh(){ sshpass -p "${KAIROS_ROOT_PW:-kairos}" ssh "${SSH_OPTS[@]}" root@127.0.0.1 "$@"; }
# the in-guest opt-in that makes :1337 reachable from the host (baked copy of this
# script lives in /rig; the live tree is under /workspace/repo/dev/rig/testrig)
EXPOSE_UI=${KAIROS_EXPOSE_UI_SH:-$(dirname "$0")/expose-ui.sh}
[ -f "$EXPOSE_UI" ] || EXPOSE_UI=/workspace/repo/dev/rig/testrig/expose-ui.sh

[ "$MODE" = fresh ] && { rm -f "$OVERLAY"; log "overlay discarded — next boot is a fresh first boot"; exit 0; }
[ -f "$IMG" ] || { fail "backing image not found: $IMG (is /images mounted? is the .gz decompressed?)"; exit 1; }
[ -e /dev/kvm ] || { fail "/dev/kvm missing — run the container with --device /dev/kvm"; exit 1; }

# ---- GPU decision -----------------------------------------------------------
VFIO_GROUP=$(ls /dev/vfio 2>/dev/null | grep -E '^[0-9]+$' | head -1 || true)
case "$GPU" in
  auto) [ -n "$VFIO_GROUP" ] && GPU=on || GPU=off ;;
  on)   [ -n "$VFIO_GROUP" ] || { fail "VM_GPU=on but no /dev/vfio group device — host GPU not vfio-bound (reboot pending?)"; exit 1; } ;;
esac
GPU_ARGS=()
if [ "$GPU" = on ]; then
  GPU_ARGS=(-device "vfio-pci,host=$NV_VGA" -device "vfio-pci,host=$NV_AUD")
  log "GPU passthrough: ON (vfio group $VFIO_GROUP, $NV_VGA + $NV_AUD)"
else
  log "GPU passthrough: OFF (boot/service verification only)"
fi

# ---- disk: qcow2 overlay over the pristine raw image ------------------------
if [ ! -f "$OVERLAY" ]; then
  log "creating $DISK overlay over $(basename "$IMG") (pristine image stays untouched)…"
  qemu-img create -f qcow2 -b "$IMG" -F raw -o size="$DISK" "$OVERLAY" >/dev/null
  # never smaller than the image: a shorter virtual disk cuts off the end of the root partition and
  # the guest stops in the initramfs ("PARTUUID=… does not exist") — e.g. the 80.5 GB media image vs 60G
  IMG_BYTES=$(stat -c %s "$IMG")
  OVL_BYTES=$(qemu-img info --output=json "$OVERLAY" | jq -r '."virtual-size"')
  if [ "$OVL_BYTES" -lt "$IMG_BYTES" ] 2>/dev/null; then
    qemu-img resize -q "$OVERLAY" "$IMG_BYTES" \
      && log "VM_DISK=$DISK is smaller than the image ($IMG_BYTES bytes): overlay grown to the image's size"
  fi
fi

# ---- OVMF (UEFI) — the image boots via systemd-boot at the fallback path ----
CODE=$(ls /usr/share/OVMF/OVMF_CODE_4M.fd /usr/share/OVMF/OVMF_CODE.fd 2>/dev/null | head -1)
VARSRC=$(ls /usr/share/OVMF/OVMF_VARS_4M.fd /usr/share/OVMF/OVMF_VARS.fd 2>/dev/null | head -1)
[ -n "$CODE" ] || { fail "OVMF firmware not found in the container"; exit 1; }
VARS=/tmp/OVMF_VARS.testrig.fd; [ -f "$VARS" ] || cp "$VARSRC" "$VARS"

# ---- QEMU: modelled to look like a real PC to the guest [R64] ----------------
# q35 chipset, host CPU, SMBIOS/DMI identity, an NVMe system disk, an Intel e1000e
# NIC, a real xHCI USB controller with tablet+keyboard, and HD-audio — so
# dmidecode/lspci/udev see a believable machine, not a bare VM.
QEMU=(qemu-system-x86_64 -name kairos-testrig
  -accel kvm -machine q35 -cpu host -smp "$CPUS" -m "$MEM"
  -smbios type=0,vendor=mAIer,version=kAIrOS-6,date=07/01/2026
  -smbios type=1,manufacturer=mAIer,product="kAIrOS Reference Device",version=v6,serial=KAIROS-0001,family=kAIrOS,uuid=4ea12005-0000-4000-8000-000000000006
  -smbios type=2,manufacturer=mAIer,product="kAIrOS Mainboard",version=1.0
  -smbios type=3,manufacturer=mAIer,version=1.0
  -rtc base=utc,driftfix=slew
  -device qemu-xhci,id=xhci -device usb-tablet,bus=xhci.0 -device usb-kbd,bus=xhci.0
  -audiodev none,id=snd0 -device intel-hda -device hda-duplex,audiodev=snd0)
# test/smoke end on guest poweroff; interactive VMs must survive a guest reboot
[ "$MODE" = run ] || QEMU+=(-no-reboot)
QEMU+=(
  -drive if=pflash,format=raw,unit=0,file="$CODE",readonly=on
  -drive if=pflash,format=raw,unit=1,file="$VARS"
  -drive file="$OVERLAY",format=qcow2,if=none,id=disk0,discard=unmap
  -device nvme,drive=disk0,serial=KAIROS-NVME-001
  -netdev "user,id=n0,hostfwd=tcp::$SSH_PORT-:22,hostfwd=tcp::1337-:1337,hostfwd=tcp::5900-:5900"
  -device e1000e,netdev=n0
  -device virtio-vga
  "${GPU_ARGS[@]}")

if [ "$MODE" = run ]; then
  # The 4070 renders the kAIrOS DESKTOP itself (nvidia X + GPU Chromium); an
  # in-guest x11vnc exposes that framebuffer on :5900 (hostfwd above). QEMU's own
  # VNC on :5901 only shows the firmware/boot console on the basic display — the
  # same split a real desktop has (BIOS on the board, desktop on the dGPU).
  log "real-hardware VM: GPU desktop on VNC :5900 (x11vnc, GPU-rendered), boot console on VNC :5901, SSH -p $SSH_PORT"
  log "(host :1337 needs the in-guest opt-in once SSH is up:  gssh 'sh -s' < $EXPOSE_UI  — run-gpu-vm.sh does it) [R66]"
  exec "${QEMU[@]}" -display vnc=:1 -serial file:"$CONSOLE"
fi

# ---- test / smoke: headless boot + in-guest verification --------------------
log "booting VM headless (console -> $CONSOLE)…"
"${QEMU[@]}" -display vnc=:1 -serial file:"$CONSOLE" -daemonize -pidfile /tmp/qemu.pid
QPID=$(cat /tmp/qemu.pid)
trap 'kill "$QPID" 2>/dev/null' EXIT

log "waiting for SSH (root@127.0.0.1:$SSH_PORT, up to ${TIMEOUT}s)…"
UP=0
for _ in $(seq 1 $((TIMEOUT / 3))); do
  kill -0 "$QPID" 2>/dev/null || { fail "QEMU exited early — console tail:"; tail -30 "$CONSOLE" | tr -d '\000'; exit 1; }
  gssh 'echo OK' 2>/dev/null | grep -q OK && { UP=1; break; }
  sleep 3
done
[ "$UP" = 1 ] || { fail "no SSH after ${TIMEOUT}s — console tail:"; tail -50 "$CONSOLE" | tr -d '\000'; exit 1; }

# [R66] the kernel binds loopback by default: opt this guest in so the host-side
# :1337 forward works (idempotent; only the first boot of an overlay restarts kairos)
if [ -f "$EXPOSE_UI" ]; then
  log "exposing the kAIrOS API to the host (expose-ui.sh -> kairos.service drop-in)…"
  gssh 'sh -s' < "$EXPOSE_UI" 2>/dev/null | sed 's/^/  /' || true
fi

log "═══ guest verification ═══"
PASS=0; TOTAL=0
check(){ TOTAL=$((TOTAL+1)); local name=$1; shift
  if OUT=$(gssh "$@" 2>&1); then PASS=$((PASS+1)); printf '  \033[1;32m✔\033[0m %-28s %s\n' "$name" "$(head -1 <<<"$OUT")"
  else printf '  \033[1;31m✘\033[0m %-28s %s\n' "$name" "$(head -1 <<<"$OUT")"; fi }

check "boot (kernel)"        'uname -r'
check "root fs"              'findmnt -no SOURCE,FSTYPE /'
check "auto-resize"          'ROOT=$(findmnt -no SOURCE /); SZ=$(df -BG --output=size / | tail -1 | tr -dc 0-9); echo "${SZ}G"; [ "$SZ" -gt 30 ]'
check "ssh host keys"        'ls /etc/ssh/ssh_host_ed25519_key >/dev/null && echo present'
check "kairos.service"       'systemctl is-active kairos'
check "kairos HTTP :1337"    'curl -sf -o /dev/null -w "HTTP %{http_code}" http://127.0.0.1:1337/'
check "kairos trust boundary" 'B=$(systemctl show -p Environment kairos | grep -o "VIBEOS_HOST=[^ ]*"); T=$( [ -r /run/kairos/token ] && echo "token ok" || echo "no token (pre-R66 kernel)"); echo "bind ${B:-VIBEOS_HOST=127.0.0.1 (default)} · $T"'
# the installed engine set vs the manifests' "engines" requirement (kernel check_engines,
# /api/status "engines"): sync-guest.sh ships a stamped set; the July engines of the v6 image
# alone are "unstamped" and the kernel refuses to exec them — a pass here proves the pairing
check "engines match manifests" 'curl -sf http://127.0.0.1:1337/api/status | python3 -c "import sys, json
e = json.load(sys.stdin).get(\"engines\")
e or sys.exit(\"no engines field: pre-stamp kernel\")
print(e[\"status\"] + \" - \" + (\", \".join(k + \" \" + str(v.get(\"commit\")) + \" (\" + str(v.get(\"committed\"))[:10] + \")\" for k, v in e[\"installed\"].items()) if e[\"status\"] == \"ok\" else \"; \".join(e[\"problems\"])))
sys.exit(0 if e[\"status\"] == \"ok\" else 1)"'
check "nvidia module state"  'if systemctl is-enabled nvidia-load >/dev/null 2>&1; then echo "via nvidia-load.service (hybrid-safe, before getty): $(lsmod | grep -q ^nvidia && echo loaded || echo idle)"; else lsmod | grep -q ^nvidia && echo "loaded (service missing!)" || echo "not loaded"; fi'

# ---- the healthy-machine gate (R63 status note, VM test 2026-09-10): an empty
# `systemctl --failed` was satisfied by a black screen (getty@tty1 job deleted by an
# ordering cycle, D7) and by a kernel crash-looping every 3 s (D10). The criterion is
# the boot log's own verdict plus liveness of the chain getty -> X -> kiosk, no
# ordering cycle in the journal, and the kAIrOS loader entry (its cmdline) in use.
check "no ordering cycle"    'N=$(journalctl -b 0 --no-pager 2>/dev/null | grep -c "ordering cycle"); echo "$N \"ordering cycle\" journal line(s)"; [ "$N" = 0 ]'
check "kAIrOS loader entry"  'grep -q "vt.global_cursor_default=0" /proc/cmdline && echo "kairos.conf booted: $(cut -c1-70 /proc/cmdline)…"'
check "failed units"         'N=$(systemctl --failed --no-legend --no-pager | wc -l); echo "$N failed unit(s) $(systemctl --failed --no-legend --no-pager | awk "{print \$1}" | tr "\n" " ")"; [ "$N" = 0 ]'
check "getty@tty1"           'S=$(systemctl is-active getty@tty1); echo "$S ($(systemctl show -p Result,NRestarts --value getty@tty1 | tr "\n" " "))"; [ "$S" = active ]'
check "Xorg on :0"           'for i in $(seq 1 40); do [ -S /tmp/.X11-unix/X0 ] && pgrep -x Xorg >/dev/null && break; sleep 3; done; [ -S /tmp/.X11-unix/X0 ] && echo "Xorg up on :0 ($(pgrep -a Xorg | head -1 | cut -c1-60)), attempts: $(cat /run/kairos/x-attempts 2>/dev/null)"'
check "kiosk Chromium"       'for i in $(seq 1 60); do N=$(pgrep -fa "chromium.*--user-data-dir=/tmp/kiosk-" | grep -oE "kiosk-[0-9]+" | sort -u | wc -l); [ "$N" -ge 1 ] && break; sleep 3; done; echo "$N kiosk instance(s)"; [ "$N" -ge 1 ]'
# the late capture waits up to 2 min for kernel + kiosk before judging: wait for THIS
# boot's `boot` verdict (attempt number from the early breadcrumb), then require
# PASS (WARN tolerated, FAIL is a failed test); the json is kept beside the console log
check "bootlog verdict"      'A=$(cat /run/kairos-bootlog-attempt 2>/dev/null || echo 0); J=/boot/efi/KAIROS-LOGS/latest.json
  for i in $(seq 1 70); do
    if [ -f "$J" ] && grep -q "\"reason\":\"boot\"" "$J" && grep -q "\"attempt\":$A," "$J"; then
      V=$(sed -n "s/.*\"verdict\":\"\([A-Z]*\)\".*/\1/p" "$J")
      echo "verdict $V (attempt #$A): $(sed -n 3p /boot/efi/KAIROS-LOGS/latest.txt | cut -c1-160)"
      [ "$V" = PASS ] || [ "$V" = WARN ]; exit $?
    fi; sleep 3
  done; echo "no boot verdict for attempt #$A after 210 s ($(ls /boot/efi/KAIROS-LOGS 2>/dev/null | tr "\n" " "))"; exit 1'
gssh 'cat /boot/efi/KAIROS-LOGS/latest.json 2>/dev/null' > "$(dirname "$CONSOLE")/testrig-latest.json" 2>/dev/null || true

if [ "$GPU" = on ]; then
  check "GPU visible (PCI)"  'lspci | grep -i nvidia | head -1'
  log "starting nvidia-load.service (compute-only driver load)…"
  gssh 'systemctl start nvidia-load 2>/dev/null; sleep 3' || true
  check "nvidia driver"      'nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader'
  check "nvidia-persistenced" 'S=$(systemctl is-active nvidia-persistenced); echo "$S (started by kairos-gpu-setup, enabled: $(systemctl is-enabled nvidia-persistenced 2>&1))"; [ "$S" = active ]'
  if [ "$MODE" = smoke ]; then
    log "GPU smoke: real image generation via the kAIrOS kernel (async job, up to 5 min)…"
    check "image generation"  'JID=$(curl -sf -X POST http://127.0.0.1:1337/api/image -H "Content-Type: application/json" -d "{\"prompt\":\"a modern and futuristic test cube\"}" | python3 -c "import sys,json;print(json.load(sys.stdin)[\"job\"])") || exit 1
      for i in $(seq 1 100); do
        S=$(curl -sf http://127.0.0.1:1337/api/job/$JID)
        echo "$S" | grep -q "\"status\": \"done\""  && { echo "image OK: $S"; exit 0; }
        echo "$S" | grep -q "\"status\": \"error\"" && { echo "$S"; exit 1; }
        sleep 3
      done; echo "timeout waiting for image job"; exit 1'
  fi
fi

log "═══ result: $PASS/$TOTAL checks passed ═══"
log "powering off guest…"
gssh 'poweroff' 2>/dev/null || true
for _ in $(seq 1 20); do kill -0 "$QPID" 2>/dev/null || break; sleep 1; done
kill "$QPID" 2>/dev/null || true
[ "$PASS" = "$TOTAL" ] && { log "ALL CHECKS PASSED"; exit 0; } || { fail "some checks failed"; exit 1; }
