#!/usr/bin/env bash
# Static verification of a built kAIrOS ISO: mount its squashfs and confirm the
# system is configured (ssh enabled+root login, kairos services, wifi, engines, kiosk).
set -uo pipefail
ISO=${1:-/srv/kairos-out/kairos-hwtest-4070.iso}
IM=$(mktemp -d); SM=$(mktemp -d)
cleanup() { umount "$SM" 2>/dev/null||true; umount "$IM" 2>/dev/null||true; rmdir "$SM" "$IM" 2>/dev/null||true; }
trap cleanup EXIT

mount -o loop,ro "$ISO" "$IM" 2>/dev/null
SQ=$(find "$IM" -name 'filesystem.squashfs' | head -1)
echo "squashfs: ${SQ#$IM}"
mount -o loop,ro "$SQ" "$SM" 2>/dev/null
R="$SM"
ok(){ printf "  ✓ %s\n" "$1"; }; no(){ printf "  ✗ %s\n" "$1"; }

echo "== boot / kernel =="
ls "$IM"/live/vmlinuz* >/dev/null 2>&1 && ok "kernel present on ISO" || no "no kernel"
KVER=$(ls "$R"/lib/modules 2>/dev/null | tr '\n' ' '); echo "  kernel modules: $KVER"

echo "== ssh =="
grep -q '^PermitRootLogin yes' "$R"/etc/ssh/sshd_config && ok "root login enabled" || no "root login not set"
ls "$R"/etc/systemd/system/multi-user.target.wants/ssh.service >/dev/null 2>&1 && ok "ssh.service enabled" || no "ssh not enabled"

echo "== kAIrOS services =="
ls "$R"/etc/systemd/system/multi-user.target.wants/kairos.service >/dev/null 2>&1 && ok "kairos.service enabled" || no "kairos not enabled"
ls "$R"/etc/systemd/system/multi-user.target.wants/kairos-payload.service >/dev/null 2>&1 && ok "kairos-payload.service enabled" || no "payload mount not enabled"

echo "== wifi profile (rendered at build from iso/secrets/wifi.env) [R66] =="
ls "$R"/etc/NetworkManager/system-connections/*.nmconnection >/dev/null 2>&1 \
  && ok "wifi profile: $(basename "$(ls "$R"/etc/NetworkManager/system-connections/*.nmconnection | head -1)")" \
  || no "no wifi profile baked (image joins no network by itself — fine for a consumer build)"
echo "== sshd: key-only root [R66] =="
grep -q '^PermitRootLogin prohibit-password' "$R/etc/ssh/sshd_config.d/kairos.conf" 2>/dev/null \
  && grep -q '^PasswordAuthentication no' "$R/etc/ssh/sshd_config.d/kairos.conf" && ok "sshd: prohibit-password + no password auth" || no "sshd hardening drop-in missing"
[ -s "$R/root/.ssh/authorized_keys" ] && ok "root authorized_keys present" || no "no root authorized_keys — image is not SSH-monitorable"
ls "$R"/etc/systemd/system/multi-user.target.wants/NetworkManager.service >/dev/null 2>&1 && ok "NetworkManager enabled" || no "NM not enabled"

echo "== nvidia / cuda =="
ls "$R"/etc/modules-load.d/nvidia.conf >/dev/null 2>&1 && ok "nvidia modules autoload" || no "nvidia autoload missing"
ls "$R"/lib/modules/*/updates/dkms/nvidia*.ko* >/dev/null 2>&1 && ok "nvidia kernel module built (dkms)" || no "nvidia .ko NOT found — verify on target"
[ -f "$R"/etc/ld.so.conf.d/kairos-cuda.conf ] && ok "cuda runtime lib path configured" || no "cuda lib path missing"

echo "== kAIrOS stack =="
[ -x "$R"/opt/vibeos/bin/llama-server ] && ok "llama-server bundled" || no "llama-server missing"
[ -x "$R"/opt/vibeos/bin/sd-cli ] && ok "sd-cli bundled" || no "sd-cli missing"
ls "$R"/opt/vibeos/lib/libcudart* >/dev/null 2>&1 && ok "cuda runtime .so bundled" || no "cudart not bundled"
[ -f "$R"/opt/vibeos/os/kernel/server.py ] && ok "kernel code present" || no "kernel code missing"
[ -f "$R"/opt/vibeos/manifest.json ] && ok "manifest (tier) present" || no "manifest missing"

echo "== kiosk =="
[ -x "$R"/usr/bin/chromium ] && ok "chromium present" || no "chromium missing"
ls "$R"/etc/systemd/system/getty@tty1.service.d/autologin.conf >/dev/null 2>&1 && ok "tty1 autologin -> kiosk" || no "autologin missing"

echo "== models in squashfs? (should be NO — they ride on KAIROS_DATA) =="
if ls "$R"/opt/vibeos/models/*/*.gguf >/dev/null 2>&1; then no "models ARE in squashfs (lean build expected none)"; else ok "lean: no models in squashfs (correct)"; fi
echo "DONE"
