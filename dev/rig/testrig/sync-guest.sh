#!/usr/bin/env bash
# sync-guest.sh — push the working repo into the booted guest VM at /opt/vibeos/os
# AND replicate what the image build (iso/build-img.sh + hooks/0200-kairos-system,
# 0100-nvidia-cuda) would apply, so a synced guest boots the same chain a freshly
# built image does. Keep this in step with build-img.sh + the hooks (R63 status note):
#   * full iso/config/includes.chroot overlay (etc + usr + home): units, drop-ins
#     (getty@tty1 retry limits), sbin scripts, kernel hooks, /etc/kernel/install.conf
#   * stale files a built image never has: the old shared kairos-bootlog-failure.service
#   * exec bits on /usr/local/sbin/kairos-* and the kernel hooks; the boot screen painter
#     (scripts/splash/install.sh: binary, initramfs hook/scripts, unit) + update-initramfs
#   * systemd-tmpfiles --create (/run/kairos, /var/log/kairos) + /var/log/journal
#   * agetty --autologin --skip-login drop-in for getty@tty1
#   * enable every unit the build enables (bootlog{,-early,-emergency,-xfail.path},
#     splash, nvidia-load); disable nvidia-persistenced (kairos-gpu-setup
#     starts it when a device is bound); the failure capture is a per-trigger
#     template pulled in by OnFailure=, never enabled
#   * loader: `default kairos.conf`, ONLY the kAIrOS entries (the Debian BLS entry +
#     <machine-id>/ tree kernel-install wrote are removed), silent cmdline with
#     console=tty1 LAST, kernel + initrd on the ESP refreshed by kairos-esp-sync
#   * ESP fstab entry gets nofail + x-systemd.device-timeout
#   * fonts-noto-color-emoji + fonts-noto-core (best effort over the slow link,
#     KAIROS_SYNC_NO_APT=1 skips)
#   * python3-xlib (kernel/wm.py, the window layer [R68] — in the package list now,
#     not in older images) + ~/.config/openbox/rc.xml ownership check
# Skipped on purpose (slow, needs guest apt): pipewire/bluez packages — audio and
# bluetooth then report available:false.
# Idempotent. Run inside the testrig container while the VM is up: sync-guest.sh [ssh_port]
# Paths (container side): KAIROS_REPO=/workspace/repo, KAIROS_ENGINES=/workspace/engines (a built engine set:
# bin/ + engines/ + lib/, see docs/how-to/BUILDING.md), KAIROS_SSH_KEY=<this dir>/id_kairos.
set -euo pipefail
PORT=${1:-2222}
REPO=${KAIROS_REPO:-/workspace/repo}
# key-built images accept only testrig/id_kairos; pre-R66 images take the password [R66]
SSH_KEY=${KAIROS_SSH_KEY:-$(dirname "$0")/id_kairos}
SSH="sshpass -p ${KAIROS_ROOT_PW:-kairos} ssh -p $PORT -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR"
[ -f "$SSH_KEY" ] && SSH="$SSH -i $SSH_KEY -o IdentitiesOnly=yes"
SSH="$SSH root@127.0.0.1"
log(){ printf '\033[1;36m[sync-guest]\033[0m %s\n' "$*"; }

log "1/8 sync kernel/shell/manifests/scripts/docs -> guest /opt/vibeos/os"
# [R88] the runtime is exactly these five directories (iso/build-iso.sh stages the same set): edit backups and bytecode
# never go, and anything else in the guest's /opt/vibeos/os — the tests/ an older image carries — is removed
OS_RUNTIME="kernel shell manifests scripts docs"
tar -C "$REPO" --exclude=__pycache__ --exclude='*.pyc' --exclude='*.bak' --exclude='*.bak-*' --exclude='*.bak[0-9]*' \
    -czf - $OS_RUNTIME \
  | $SSH 'tar -C /opt/vibeos/os -xzf - && echo synced'
$SSH "cd /opt/vibeos/os && for e in *; do case \" $OS_RUNTIME \" in *\" \$e \"*) ;; *) rm -rf -- \"\$e\" && echo \"removed stale /opt/vibeos/os/\$e (not part of the runtime)\" ;; esac; done"

log "1b/8 engines: stamped engine set -> guest /opt/vibeos/{bin,lib} (KAIROS_SYNC_ENGINES=0 keeps the image's)"
# Manifests and engines are version-coupled (manifest "engines" <-> bin/ENGINES.json, kernel
# check_engines): the v6 image carries the July engines while step 1 ships today's manifests
# (no --offload-to-cpu: sd.cpp 7f410a3's --auto-fit places Krea 2 itself, an older sd-cli
# OOMs the 8 GB card with a misleading error). Without this step the next VM run would pair
# the two — the kernel now refuses that cleanly, and a "pass" would not be the shipped
# combination anyway. Source: the CUDA set built 2026-09-14 (bin/ symlinks into
# engines/*/build + the staged runtime lib/) at KAIROS_ENGINES. The lib bundle
# mirrors iso/build-iso.sh: the CUDA runtime .so the binaries link (its ldd sweep needs the
# CUDA userland the build chroot has; here the artefact's lib/ is filtered by the same case
# list) + the dlopened ggml backends / llama's own libs from the build trees, cp -L.
ENGINES_SRC=${KAIROS_ENGINES:-/workspace/engines}
if [ "${KAIROS_SYNC_ENGINES:-1}" = 0 ]; then
  log "  skipped (KAIROS_SYNC_ENGINES=0) — the guest keeps its image's engines; expect engines: unstamped/mismatch in /api/status"
else
  [ -d "$ENGINES_SRC/bin" ] || { log "ERROR: no engine set at $ENGINES_SRC/bin (KAIROS_ENGINES=<dir with bin/ + engines/ + lib/>)"; exit 1; }
  STAGE=$(mktemp -d /tmp/kairos-engines.XXXXXX); trap 'rm -rf "$STAGE"' EXIT
  mkdir -p "$STAGE/bin" "$STAGE/lib"
  for b in llama-server llama-cli sd-cli sd-server ace-lm ace-synth ace-server; do   # sd-server: multi-image sessions (docs/INTEGRATION-2026-09-28.md), absent in the 2026-09-14 set
    p="$ENGINES_SRC/bin/$b"; { [ -e "$p" ] || [ -L "$p" ]; } || continue
    # the build's symlinks are absolute /opt/vibeos/engines/... (the build container): re-root them
    t=$(readlink "$p" 2>/dev/null || echo "$p"); case "$t" in /opt/vibeos/*) t="$ENGINES_SRC/${t#/opt/vibeos/}" ;; esac
    [ -f "$t" ] || { log "ERROR: $b -> $t is not a file"; exit 1; }
    cp "$t" "$STAGE/bin/$b"
  done
  [ -e "$STAGE/bin/sd-cli" ] && [ -e "$STAGE/bin/llama-server" ] || { log "ERROR: $ENGINES_SRC/bin lacks sd-cli/llama-server"; exit 1; }
  # the stamp: install-engines.sh writes it; an artefact dir stamped after the fact gets the
  # same writer here (scripts/engine-stamp.py reads the engines/* git trees)
  if [ -f "$ENGINES_SRC/bin/ENGINES.json" ]; then cp "$ENGINES_SRC/bin/ENGINES.json" "$STAGE/bin/"
  else python3 "$REPO/scripts/engine-stamp.py" --root "$ENGINES_SRC" --backend cuda --out "$STAGE/bin/ENGINES.json"; fi
  for lib in "$ENGINES_SRC"/lib/*; do
    case "$(basename "$lib")" in
      libcudart*|libcublas*|libcublasLt*|libnvrtc*|libcufft*|libcurand*|libnvJitLink*) cp -L "$lib" "$STAGE/lib/" ;;
    esac
  done
  find "$ENGINES_SRC/engines" \( -name 'libggml*.so*' -o -name 'libllama*.so*' -o -name 'libmtmd*.so*' -o -name '*stable-diffusion*.so*' \) 2>/dev/null \
    | while read -r so; do cp -L "$so" "$STAGE/lib/" 2>/dev/null || true; done
  log "  $(ls "$STAGE/bin" | grep -vc json) binaries + $(ls "$STAGE/lib" | wc -l) libs = $(du -sh "$STAGE" | cut -f1) (gzip -1 over the guest's SSH; cp -L materialises the soname links like build-iso.sh)"
  log "  stamp: $(python3 -c 'import json,sys; s=json.load(open(sys.argv[1])); print(s["backend"], "|", ", ".join("%s %s (%s)" % (k, v["commit"], v["committed"][:10]) for k, v in s["engines"].items()))' "$STAGE/bin/ENGINES.json")"
  # bin + lib only, never "." — the stage root is a mktemp -d (0700) and extracting its "./"
  # entry as root set the guest's /opt/vibeos to 0700: the kiosk user could not read the boot
  # clip or anything else below it (VM test 2026-09-29)
  tar -C "$STAGE" -cf - bin lib | pigz -1 \
    | $SSH 'mkdir -p /opt/vibeos/bin /opt/vibeos/lib && tar -C /opt/vibeos -xzf - && ldconfig 2>/dev/null || true
echo "engines extracted: $(ls /opt/vibeos/bin | tr "\n" " ")"'
  # post-sync check IN the guest (its CUDA userland + driver): every binary resolves, both
  # CLIs answer --version, the stamp is readable. The kernel's own verdict follows in step 8.
  # (--version is informational: the 2026-09-14 CUDA sd-cli prints "version unknown" — its
  # build saw no git metadata — which is exactly why the stamp comes from the source trees.)
  $SSH 'export LD_LIBRARY_PATH=/opt/vibeos/lib; rc=0
for b in llama-server llama-cli sd-cli ace-lm ace-synth ace-server; do
  [ -f /opt/vibeos/bin/$b ] || continue
  MISSING=$(ldd /opt/vibeos/bin/$b 2>&1 | grep "not found" | awk "{print \$1}" | sort -u | tr "\n" " ")
  [ -z "$MISSING" ] || { echo "ERROR: $b: unresolved: $MISSING"; rc=1; }
done
[ $rc = 0 ] && echo "ldd: every engine binary resolves against /opt/vibeos/lib + the driver"
echo "llama-server: $(/opt/vibeos/bin/llama-server --version 2>&1 | head -1)"
echo "sd-cli: $(/opt/vibeos/bin/sd-cli --version 2>&1 | head -1)"
python3 -c "import json; s=json.load(open(\"/opt/vibeos/bin/ENGINES.json\")); print(\"stamp in guest:\", s[\"backend\"], \", \".join(k + \" \" + v[\"commit\"] for k, v in s[\"engines\"].items()))" || rc=1
exit $rc' || { log "ERROR: engine post-sync check failed in the guest"; exit 1; }
fi

log "2/8 full includes.chroot overlay (etc + usr + home) -> guest /, stale units removed"
tar -C "$REPO/iso/config/includes.chroot" -czf - etc usr home \
  | $SSH 'tar -C / --no-same-owner -xzf -
# the repo tree is owned by the operator (uid 1000 = kairos in the guest); a real
# build extracts as root — restore root ownership so the test matches a built image
chown -R root:root /etc/systemd/system /etc/tmpfiles.d /etc/kernel /etc/initramfs /etc/chromium /usr/local/sbin 2>/dev/null || true
chown -R kairos:kairos /home/kairos 2>/dev/null || true
# the failure capture is a per-trigger template now (kairos-bootlog-failure@.service);
# the old shared unit must not linger in the guest (a built image never has it)
rm -f /etc/systemd/system/kairos-bootlog-failure.service
echo "overlay applied: $(ls /usr/local/sbin/kairos-* | tr "\n" " ")"'

log "3/8 build-hook equivalents: exec bits, tmpfiles, journal dir, sudoers, agetty"
$SSH 'set -e
# exec bits — a tar/rsync has dropped one before (203/EXEC, R63)
chmod +x /usr/local/sbin/kairos-bootlog /usr/local/sbin/kairos-bootlog-early /usr/local/sbin/kairos-prewarm \
         /usr/local/sbin/kairos-display-apply /usr/local/sbin/kairos-install \
         /usr/local/sbin/kairos-gpu-setup /usr/local/sbin/kairos-esp-sync \
         /etc/kernel/postinst.d/zz-kairos-esp /etc/initramfs/post-update.d/zz-kairos-esp 2>/dev/null || true
chown -R kairos: /home/kairos 2>/dev/null || true
# tmpfiles: /run/kairos (x-attempts, x-failed, kiosk-reload, token) + /var/log/kairos
systemd-tmpfiles --create /etc/tmpfiles.d/kairos.conf 2>&1 | head -3 || true
mkdir -p /var/log/journal /var/log/kairos
chown kairos: /var/log/kairos 2>/dev/null || true
usermod -aG render kairos 2>/dev/null || true
rm -f /etc/sudoers.d/kairos-bootanim /etc/systemd/system/kairos-bootanim.service /etc/systemd/system/kairos-bootanim-gen.service \
      /etc/systemd/system/multi-user.target.wants/kairos-bootanim.service /etc/systemd/system/multi-user.target.wants/kairos-bootanim-gen.service \
      /opt/vibeos/os/shell/bootanim-default.mp4   # R27 leftovers a built image never has
# the boot screen [R73]: painter binary -> /usr/local/sbin, initramfs hook/scripts exec bits,
# kairos-splash.service, and the initrd rebuilt so it carries the painter (-> ESP via the
# post-update hook; step 5 syncs the ESP again anyway)
KAIROS_SPLASH_INITRAMFS=1 sh /opt/vibeos/os/scripts/splash/install.sh /opt/vibeos/os 2>&1 | tail -3
lsinitramfs /boot/initrd.img-$(uname -r) | grep -q "sbin/kairos-splash$" && echo "initrd carries kairos-splash" || echo "WARN: initrd without kairos-splash"
# tty1 autologin WITH --skip-login (else agetty prints "<host> login: kairos
# (automatic login)" under the boot animation handover) [R28]. The retry limits
# live in the overlay drop-in kairos-xretry.conf next to this one (D8).
mkdir -p /etc/systemd/system/getty@tty1.service.d
cat > /etc/systemd/system/getty@tty1.service.d/autologin.conf <<EOF
[Service]
ExecStart=
ExecStart=-/sbin/agetty --autologin kairos --skip-login --noclear --noissue --nohints %I \$TERM
EOF
: > /etc/issue; : > /etc/issue.net; : > /etc/motd
touch /home/kairos/.hushlogin 2>/dev/null; chown kairos: /home/kairos/.hushlogin 2>/dev/null || true
echo "run: $(ls -ld /run/kairos 2>/dev/null || echo MISSING)"
echo "log: $(ls -ld /var/log/kairos 2>/dev/null || echo MISSING)"
echo "getty drop-ins: $(ls /etc/systemd/system/getty@tty1.service.d/ | tr "\n" " ")"'

log "4/8 enable every unit the build enables, disable nvidia-persistenced, daemon-reload"
$SSH 'systemctl daemon-reload
systemctl enable kairos-splash.service \
                 kairos-bootlog.service kairos-bootlog-early.service \
                 kairos-bootlog-emergency.service kairos-bootlog-xfail.path \
                 kairos-bootlog-stall.timer kairos-prewarm.service nvidia-load.service 2>&1 | tail -2
# [D11] the driver package enables persistenced unconditionally: it fails on every
# machine without an NVIDIA GPU; kairos-gpu-setup starts it once a device is bound
systemctl disable nvidia-persistenced.service 2>/dev/null || true
rm -f /etc/systemd/system/multi-user.target.wants/nvidia-persistenced.service
# kairos-bootlog-failure@.service is pulled in by OnFailure= / the path unit only (no [Install])
for u in multi-user.target.wants/kairos-bootlog.service \
         sysinit.target.wants/kairos-bootlog-early.service \
         emergency.target.wants/kairos-bootlog-emergency.service \
         rescue.target.wants/kairos-bootlog-emergency.service \
         multi-user.target.wants/kairos-bootlog-xfail.path \
         sysinit.target.wants/kairos-bootlog-stall.timer \
         sysinit.target.wants/kairos-prewarm.service \
         sysinit.target.wants/kairos-splash.service \
         multi-user.target.wants/nvidia-load.service; do
  [ -L "/etc/systemd/system/$u" ] || echo "WARN: not enabled: $u"
done
[ -e /etc/systemd/system/multi-user.target.wants/nvidia-persistenced.service ] && echo "WARN: nvidia-persistenced still enabled" || true
[ -e /etc/systemd/system/kairos-bootlog-failure.service ] && echo "WARN: old shared failure unit still present" || true
grep -q "^layout=other" /etc/kernel/install.conf && echo "kernel-install: layout=other" || echo "WARN: /etc/kernel/install.conf lacks layout=other"
systemd-analyze verify nvidia-load.service kairos.service kairos-bootlog-xfail.path "kairos-bootlog-failure@.service" 2>&1 | grep -v "is not executable" || true
echo "units enabled"'

log "5/8 boot config: default kairos.conf, only kAIrOS entries, silent cmdline (console=tty1 LAST), ESP nofail, no menu"
# `default` matches entry IDs = file names INCLUDING .conf: the image's old "default
# kairos" matched nothing, so the Debian BLS entry kernel-install had written sorted
# first and was what really booted (D4). Mirror build-img.sh: fix the default, remove
# every entry that is not kAIrOS's (+ the <machine-id>/ kernel tree), keep the
# cmdline rewrite for the two kAIrOS entries, refresh /kairos/{vmlinuz,initrd}.
$SSH '
ESP=/boot/efi
for e in "$ESP"/loader/entries/*.conf; do
  [ -f "$e" ] || continue
  case "$(basename "$e")" in kairos.conf|kairos-serial.conf) ;; *) echo "removing foreign loader entry $(basename "$e")"; rm -f "$e" ;; esac
done
for d in "$ESP"/*/; do
  d=${d%/}; case "$(basename "$d")" in EFI|kairos|loader|KAIROS-LOGS) ;;
    *) if [ "$(basename "$d" | grep -cE "^[0-9a-f]{32}$")" = 1 ]; then echo "removing kernel-install tree $(basename "$d")"; rm -rf "$d"; fi ;;
  esac
done
for ENTRY in "$ESP"/loader/entries/*.conf; do
  [ -f "$ENTRY" ] || continue
  OPTS=$(grep -m1 "^options" "$ENTRY" | sed "s/^options[[:space:]]*//")
  KEEP=$(printf "%s\n" $OPTS | grep -vE "^console=|^quiet$|^loglevel=|^systemd.show_status=|^rd.udev.log_level=|^vt.global_cursor_default=|^systemd.getty_auto=" | tr "\n" " ")
  sed -i "/^options/d" "$ENTRY"
  case "$(basename "$ENTRY")" in
    kairos-serial.conf) printf "options %sloglevel=4 systemd.show_status=true console=tty1 console=ttyS0,115200\n" "$KEEP" >> "$ENTRY" ;;
    *) printf "options %squiet loglevel=0 systemd.show_status=false rd.udev.log_level=3 vt.global_cursor_default=0 systemd.getty_auto=0 console=ttyS0,115200 console=tty1\n" "$KEEP" >> "$ENTRY" ;;
  esac
  echo "$(basename "$ENTRY"): $(grep ^options "$ENTRY")"
done
LC=$ESP/loader/loader.conf
sed -i "s/^default[[:space:]]\+kairos[[:space:]]*$/default kairos.conf/; s/^timeout .*/timeout 0/" "$LC" 2>/dev/null || true
grep -q "^default kairos.conf$" "$LC" || { sed -i "/^default /d" "$LC"; echo "default kairos.conf" >> "$LC"; }
grep -q "^editor no$" "$LC" || echo "editor no" >> "$LC"
echo "loader.conf: $(tr "\n" " " < "$LC")"
echo "entries: $(ls "$ESP"/loader/entries/ | tr "\n" " ")"
# the kAIrOS entry boots /kairos/{vmlinuz,initrd}: bring them in step with the newest
# installed kernel + initrd, exactly as the apt kernel hooks would (kairos-esp-sync)
/usr/local/sbin/kairos-esp-sync || true
# ESP must be nofail: a bad EFI partition must never drop the machine to emergency
if grep -q " /boot/efi " /etc/fstab && ! grep " /boot/efi " /etc/fstab | grep -q nofail; then
  sed -i "/ \/boot\/efi /s/umask=0077/umask=0077,nofail,x-systemd.device-timeout=10/" /etc/fstab
  systemctl daemon-reload
fi
echo "fstab esp: $(grep " /boot/efi " /etc/fstab)"'

log "6/8 fonts (emoji dock icons, D6) — best effort over the slow link"
if [ "${KAIROS_SYNC_NO_APT:-0}" = 1 ]; then
  log "  skipped (KAIROS_SYNC_NO_APT=1)"
else
  $SSH 'if fc-list 2>/dev/null | grep -qi "Noto Color Emoji"; then echo "fonts present: $(fc-list | grep -ci noto) noto faces"
else
  timeout 900 sh -c "DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends fonts-noto-color-emoji fonts-noto-core" 2>&1 | tail -2 \
    && echo "fonts installed: $(fc-list | grep -ci noto) noto faces" || echo "WARN: font install failed/timed out — dock icons stay tofu until the image is rebuilt"
fi' || true
fi

log "6b/8 python3-xlib for the window layer [R68] + kiosk openbox config"
if [ "${KAIROS_SYNC_NO_APT:-0}" = 1 ]; then
  log "  apt skipped (KAIROS_SYNC_NO_APT=1) — /api/windows answers available:false without python3-xlib"
else
  $SSH 'if python3 -c "import Xlib" 2>/dev/null; then echo "python3-xlib present"
else
  timeout 600 sh -c "DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends python3-xlib" 2>&1 | tail -2 \
    && echo "python3-xlib installed" || echo "WARN: python3-xlib install failed/timed out — /api/windows stays available:false"
fi' || true
fi
$SSH 'f=/home/kairos/.config/openbox/rc.xml
[ -f "$f" ] && echo "openbox rc.xml: $(stat -c "%U:%G %a" "$f") decor=$(grep -c "<decor>no</decor>" "$f")" || echo "WARN: $f missing (overlay step 2 should have delivered it)"'

log "7/8 boot screen check [R73]: painter installed, initrd + ESP carry it"
$SSH 'ls -l /usr/local/sbin/kairos-splash | cut -c1-60; /usr/local/sbin/kairos-splash-ctl status | head -n 1
cmp -s /boot/initrd.img-$(uname -r) /boot/efi/kairos/initrd && echo "ESP initrd in sync" || echo "WARN: ESP initrd differs from /boot"'

log "8/8 expose the API to the host [R66] + restart kairos"
$SSH 'sh -s' < "$(dirname "$0")/expose-ui.sh"
$SSH 'systemctl restart kairos && sleep 6 && curl -sf -o /dev/null -w "kairos /api/status HTTP %{http_code}\n" http://127.0.0.1:1337/api/status'
# the kernel's engine/manifest verdict (/api/status "engines", the same line vm.sh checks):
# with the set shipped above it must be ok; with KAIROS_SYNC_ENGINES=0 it is reported only
ENG=$($SSH 'curl -sf http://127.0.0.1:1337/api/status | python3 -c "import sys, json
e = json.load(sys.stdin).get(\"engines\")
print(\"engines: \" + (e[\"status\"] + \" - \" + (\", \".join(k + \" \" + str(v.get(\"commit\")) + \" (\" + str(v.get(\"committed\"))[:10] + \")\" for k, v in e[\"installed\"].items()) if e[\"status\"] == \"ok\" else \"; \".join(e[\"problems\"])) if e else \"no engines field (pre-stamp kernel)\"))"' 2>&1)
log "  $ENG"
if [ "${KAIROS_SYNC_ENGINES:-1}" != 0 ]; then
  case "$ENG" in "engines: ok"*) ;; *) log "ERROR: the synced engines do not satisfy the synced manifests"; exit 1 ;; esac
fi
log "done — REBOOT the guest so the whole chain runs on the new code (vm.sh test gates on the boot verdict)"
