#!/usr/bin/env bash
# Build the bootable kAIrOS live ISO (Debian trixie, amd64) for bare-metal hardware testing.
# Target: laptop with AMD CPU + RTX 4070 (8GB, Ada sm_89), 32GB RAM.
# Bakes in: kernel+firmware, NVIDIA driver+CUDA runtime, SSH (key-only), WiFi (from iso/secrets), kiosk Chromium,
# the kAIrOS stack + multi-arch engines + the 8gb model set + offline Wikipedia.
set -euo pipefail

REPO=$(cd "$(dirname "$0")/.." && pwd)
WORK=/srv/kairos-iso
VIBEOS=/opt/vibeos
OUT=${1:-/srv/kairos-out}

echo "== [0/6] prerequisites =="
command -v lb >/dev/null 2>&1 || { apt-get update -qq; \
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq live-build; }
[ -f "$VIBEOS/engines-multiarch.done" ] || { echo "ERROR: multi-arch engines not built yet (need sm_89)"; exit 1; }
"$REPO/scripts/check_tier_present.py" "$REPO/manifests/tier-8gb.json" "$VIBEOS/models" >/dev/null \
    || { echo "ERROR: 8gb model set incomplete"; exit 1; }

echo "== [1/6] live-build config tree (resumable) =="
# Idempotent: only reconfigure if not already configured (heavy-I/O safety — a WSL
# hiccup mid-build must not nuke the 19GB staging and restart from zero).
mkdir -p "$WORK"; cd "$WORK"
if [ ! -f "$WORK/.lb-configured" ]; then
    lb config \
        --distribution trixie \
        --architectures amd64 \
        --archive-areas "main contrib non-free non-free-firmware" \
        --binary-images iso-hybrid \
        --bootloaders "grub-pc grub-efi" \
        --firmware-chroot true \
        --firmware-binary true \
        --debian-installer none \
        --memtest none \
        --apt-indices false \
        --apt-recommends false \
        --compression gzip \
        --bootappend-live "boot=live components console=ttyS0,115200 console=tty0 quiet" \
        --iso-application "kAIrOS" \
        --iso-volume "kAIrOS"
    touch "$WORK/.lb-configured"
fi

echo "== [2/6] overlay our config (packages, hooks, includes) =="
cp -r "$REPO/iso/config/." "$WORK/config/"
chmod +x "$WORK"/config/hooks/normal/*.hook.chroot

echo "== [2b/6] operator secrets -> build tree only, never the repo [R66] =="
# SSH: root accepts keys only (the hook sets prohibit-password). Stage the operator's
# public key(s) so the hardware can be monitored; without it the image has no remote shell.
SECRETS="$REPO/iso/secrets"
PUBKEY=${KAIROS_SSH_PUBKEY:-$SECRETS/authorized_keys}
INC_CHROOT="$WORK/config/includes.chroot"
if [ -s "$PUBKEY" ]; then
    install -d -m 700 "$INC_CHROOT/root/.ssh"
    install -m 600 "$PUBKEY" "$INC_CHROOT/root/.ssh/authorized_keys"
    echo "  root authorized_keys staged from $PUBKEY"
else
    echo "  WARN: no SSH public key ($PUBKEY) — the image will have NO remote shell (see iso/secrets/README.md)"
fi
# WiFi: the SSID/PSK the test laptop should auto-join. Rendered here, so the PSK lives
# only on the build host and in the image, not in this tree.
WIFI_ENV=${KAIROS_WIFI_ENV:-$SECRETS/wifi.env}
rm -f "$INC_CHROOT"/etc/NetworkManager/system-connections/*.nmconnection
if [ -f "$WIFI_ENV" ]; then
    # shellcheck disable=SC1090
    KAIROS_WIFI_SSID=; KAIROS_WIFI_PSK=; . "$WIFI_ENV"
    [ -n "$KAIROS_WIFI_SSID" ] || { echo "ERROR: $WIFI_ENV sets no KAIROS_WIFI_SSID"; exit 1; }
    install -d -m 755 "$INC_CHROOT/etc/NetworkManager/system-connections"
    NMC="$INC_CHROOT/etc/NetworkManager/system-connections/$KAIROS_WIFI_SSID.nmconnection"
    {
        printf '[connection]\nid=%s\ntype=wifi\nautoconnect=true\nautoconnect-priority=100\n\n' "$KAIROS_WIFI_SSID"
        printf '[wifi]\nmode=infrastructure\nssid=%s\n\n' "$KAIROS_WIFI_SSID"
        if [ -n "$KAIROS_WIFI_PSK" ]; then printf '[wifi-security]\nkey-mgmt=wpa-psk\npsk=%s\n\n' "$KAIROS_WIFI_PSK"; fi
        printf '[ipv4]\nmethod=auto\n\n[ipv6]\nmethod=auto\n'
    } > "$NMC"
    chmod 600 "$NMC"
    echo "  wifi profile '$KAIROS_WIFI_SSID' staged ($( [ -n "$KAIROS_WIFI_PSK" ] && echo wpa-psk || echo open ))"
else
    echo "  no $WIFI_ENV — the image joins no WiFi by itself"
fi
# Network exposure of the kernel API (:1337) is loopback-only by default; hardware-test
# builds that are watched from another device opt in here (kairos.service documents it).
if [ "${KAIROS_EXPOSE_UI:-0}" = 1 ]; then
    install -d "$INC_CHROOT/etc/systemd/system/kairos.service.d"
    printf '[Service]\n# hardware-test build: API reachable from the network (KAIROS_EXPOSE_UI=1 at build) [R66]\nEnvironment=VIBEOS_HOST=0.0.0.0\n' \
        > "$INC_CHROOT/etc/systemd/system/kairos.service.d/expose-ui.conf"
    echo "  kairos API EXPOSED to the network (KAIROS_EXPOSE_UI=1); machine-changing calls still need /run/kairos/token"
else
    echo "  kairos API loopback-only (set KAIROS_EXPOSE_UI=1 for a remotely watched hardware-test build)"
fi

echo "== [3/6] stage kAIrOS OS (code) =="
INC="$WORK/config/includes.chroot/opt/vibeos"
mkdir -p "$INC/os" "$INC/bin" "$INC/lib" "$INC/models" "$INC/knowledge"
# scripts/ carries splash/ (the boot screen painter + install.sh, run by hooks/0200) [R73/R63]
rsync -a --delete "$REPO/kernel" "$REPO/shell" "$REPO/manifests" "$REPO/docs" "$REPO/scripts" "$INC/os/"
cp "$REPO/manifests/tier-8gb.json" "$INC/manifest.json"
echo "8gb" > "$INC/active-tier"

echo "== [4/6] stage engines + CUDA runtime libs =="
for b in llama-server llama-cli sd-cli sd-server kiwix-serve ace-lm ace-synth ace-server; do
    [ -e "$VIBEOS/bin/$b" ] && cp -L "$VIBEOS/bin/$b" "$INC/bin/"
done
# the engine stamp travels with the binaries: without it the kernel treats the set as older
# than every manifest requirement and refuses to exec the engines (check_engines) — so a
# stampless build dir is a build error here, not a broken image later
[ -f "$VIBEOS/bin/ENGINES.json" ] || { echo "no $VIBEOS/bin/ENGINES.json — run scripts/engine-stamp.py --root $VIBEOS (install-engines.sh writes it)"; exit 1; }
cp "$VIBEOS/bin/ENGINES.json" "$INC/bin/"
# Vulkan engine set for the AMD iGPU [R30] (static binaries, no extra libs)
if [ -d "$VIBEOS/bin/vulkan" ]; then mkdir -p "$INC/bin/vulkan"; cp -L "$VIBEOS/bin/vulkan/"* "$INC/bin/vulkan/"; fi
# bundle the exact CUDA/CUBLAS runtime .so the engines link (driver-independent userspace)
# — every engine binary, acestep's included (2026-09-14: ace-lm/ace-synth/ace-server link
# the same cudart/cublas/cublasLt, docs/ENGINES-CUDA-2026-09.md §4)
for bin in "$INC/bin/"*; do
    [ -f "$bin" ] && ldd "$bin" 2>/dev/null | awk '/=> \// {print $3}'
done | sort -u | while read -r lib; do
    case "$lib" in
        *libcuda.so*|*/usr/lib/wsl/*) : ;;                 # driver-provided: skip (target ships it)
        *libcudart*|*libcublas*|*libcublasLt*|*libnvrtc*|*libcufft*|*libcurand*|*libnvJitLink*)
            cp -L "$lib" "$INC/lib/" ;;
    esac
done
# ggml backend .so (dlopened, ldd misses them) + llama's own shared libs: libmtmd is what
# llama-server links for the R70 vision path (build-image.sh had it since 2026-07-02)
find "$VIBEOS/engines" -name 'libggml*.so*' -o -name 'libllama*.so*' -o -name 'libmtmd*.so*' \
     -o -name '*stable-diffusion*.so*' 2>/dev/null | while read -r so; do
    cp -L "$so" "$INC/lib/" 2>/dev/null || true
done
echo "   bundled libs:"; ls "$INC/lib/" | sed 's/^/     /'

echo "== [5/6] LEAN image — models are NOT in the squashfs =="
# The build host's disk is an SMR HDD that stalls (EIO, VM wedge) under the ~40GB of
# writes an all-in-one squashfs needs. So the ISO stays lean (~4GB, a safe write size);
# the 19.7GB models + knowledge go onto a SECOND USB partition (label KAIROS_DATA),
# populated directly by write-usb.sh and mounted at boot by kairos-payload.service.
# End result for the user is identical: boot the stick, everything is there, fully offline.
rm -rf "$INC/models" "$INC/knowledge"   # ensure they never enter the squashfs
echo "  (models delivered via the KAIROS_DATA partition; see iso/write-usb.sh)"

echo "== [6/6] lb build (lean, low-I/O) =="
mkdir -p "$OUT"
lb build 2>&1 | tee "$OUT/build.log"
ISO=$(ls "$WORK"/*.iso 2>/dev/null | head -1)
[ -n "$ISO" ] || { echo "BUILD FAILED — no ISO produced"; exit 1; }
FINAL="$OUT/kairos-hwtest-4070.iso"
mv "$ISO" "$FINAL"
echo "ISO BUILD COMPLETE: $FINAL ($(du -h "$FINAL" | cut -f1))"
sha256sum "$FINAL" | tee "$FINAL.sha256"
