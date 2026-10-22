#!/usr/bin/env bash
# Build a flashable raw disk image of kAIrOS:
#   - GPT, EFI System Partition (FAT32) with systemd-boot
#   - ext4 root that AUTO-RESIZES to fill the target disk on first boot
#   - models baked in (offline-ready), NVIDIA/SSH/WiFi/kiosk from the verified rootfs
# Output: /srv/kairos-out/kairos-hwtest-4070.img.gz   (gunzip + dd/Etcher to a >= 64 GB stick:
#         the raw image is ~51 GiB — a "32 GB" stick cannot hold it, docs/FIXES-2026-09-29.md N3)
#
# Reuses the rootfs from the verified ISO squashfs (no rebuild). Run as root in WSL.
set -euo pipefail
exec > >(tee -a /opt/vibeos/img-build.log) 2>&1
echo "=== build-img start $(date) ==="

REPO=$(cd "$(dirname "$0")/.." && pwd)
ISO=/srv/kairos-out/kairos-hwtest-4070.iso
ROOTFS=/srv/kairos-rootfs
MODELS=/opt/vibeos/models
KNOW=/opt/vibeos/knowledge
OUT=/srv/kairos-out/kairos-hwtest-4070.img
MNT=/mnt/kimg

for t in unsquashfs sgdisk mkfs.vfat mkfs.ext4 rsync losetup; do command -v $t >/dev/null || \
  { apt-get update -qq; DEBIAN_FRONTEND=noninteractive apt-get install -y -qq squashfs-tools gdisk dosfstools e2fsprogs rsync util-linux; break; }; done

echo "[1/9] unpack verified rootfs from ISO squashfs"
if [ ! -d "$ROOTFS/etc" ]; then
    IM=$(mktemp -d); mount -o loop,ro "$ISO" "$IM"
    SQ=$(find "$IM" -name filesystem.squashfs | head -1)
    rm -rf "$ROOTFS"; unsquashfs -d "$ROOTFS" "$SQ"
    umount "$IM"; rmdir "$IM"
fi
echo "  rootfs: $(du -sh "$ROOTFS" | cut -f1)"

echo "[2/9] size the image (content + slack)"
# the baked model set = exactly the manifest's non-addon files [R47]: add-ons (extra image
# models, video, music, vision, the fast LLM) are downloaded in-OS and must never ride along
# just because the build host has them (docs/FIXES-2026-09-28.md A3) — EXCEPT the bake-flagged
# ones ("bake": true on an add-on section or on one file of it: the translation `fast` model +
# the dictionaries, 1.43 GB, docs/IMAGE-BAKE-ADDONS-2026-10-07.md): the list carries them and
# the two FATAL gates below (90/91) demand them like any baked file
BAKE_LIST=$(mktemp)
python3 "$REPO/scripts/check_tier_present.py" "$REPO/manifests/tier-8gb.json" "$MODELS" --list > "$BAKE_LIST"
python3 "$REPO/scripts/check_tier_present.py" "$REPO/manifests/tier-8gb.json" "$MODELS" \
  || { echo "FATAL: the 8gb baked set is incomplete under $MODELS"; exit 90; }
RB=$(du -sb "$ROOTFS" | cut -f1)
# sized as it will be COPIED: symlinks resolved, a dangling link fails here (du without -L sized a
# symlinked models dir as 516 bytes — the re-verification's symlink risk, docs/FIXES-2026-09-29.md)
MB=$(python3 "$REPO/scripts/check_tier_present.py" "$REPO/manifests/tier-8gb.json" "$MODELS" --bytes) \
  || { echo "FATAL: a baked model is missing or a dangling symlink under $MODELS"; exit 91; }
KB=$( [ -d "$KNOW" ] && du -sbL "$KNOW" | cut -f1 || echo 0 )   # -L: a symlinked ZIM sized as its target
echo "  baked models: $(( MB / 1048576 )) MiB ($(wc -l < "$BAKE_LIST") files) + knowledge $(( KB / 1048576 )) MiB + rootfs $(( RB / 1048576 )) MiB"
ROOT_MIB=$(( (RB + MB + KB) / 1048576 + 1800 ))   # +1.8GB slack
IMG_MIB=$(( ROOT_MIB + 600 ))                      # +ESP/headroom
echo "  root=${ROOT_MIB}MiB image=${IMG_MIB}MiB"
rm -f "$OUT"; truncate -s "${IMG_MIB}MiB" "$OUT"

echo "[3/9] GPT: ESP(512M, EF00) + root(ext4, 8304 linux-root-x86-64)"
sgdisk -Z "$OUT" >/dev/null
sgdisk -n 1:1MiB:+512MiB -t 1:ef00 -c 1:EFI \
       -n 2:0:0          -t 2:8304 -c 2:KAIROS_ROOT "$OUT"

echo "[4/9] attach loop + format"
LOOP=$(losetup --find --show -P "$OUT")
trap 'umount -R "$MNT" 2>/dev/null||true; losetup -d "$LOOP" 2>/dev/null||true' EXIT
sleep 1
mkfs.vfat -F32 -n EFI "${LOOP}p1" >/dev/null
mkfs.ext4 -q -L KAIROS_ROOT "${LOOP}p2"
ROOT_PARTUUID=$(blkid -s PARTUUID -o value "${LOOP}p2")
ESP_UUID=$(blkid -s UUID -o value "${LOOP}p1")
echo "  root PARTUUID=$ROOT_PARTUUID"

echo "[5/9] populate root from rootfs + models (rate-limited writes)"
mkdir -p "$MNT"; mount "${LOOP}p2" "$MNT"
rsync -aHAX --numeric-ids \
   --exclude='/proc/*' --exclude='/sys/*' --exclude='/dev/*' --exclude='/run/*' \
   --exclude='/tmp/*' --exclude='/opt/vibeos/models/*' --exclude='/opt/vibeos/knowledge/*' \
   "$ROOTFS"/ "$MNT"/
mkdir -p "$MNT/opt/vibeos/models" "$MNT/opt/vibeos/knowledge" "$MNT/boot/efi"
rsync -aL --bwlimit=120M --files-from="$BAKE_LIST" "$MODELS"/ "$MNT/opt/vibeos/models"/   # -L: a symlinked model is copied as its target
rm -f "$BAKE_LIST"
[ -d "$KNOW" ] && rsync -aL "$KNOW"/*.zim "$MNT/opt/vibeos/knowledge"/ 2>/dev/null || true
mount "${LOOP}p1" "$MNT/boot/efi"
sync

echo "[6/9] fstab (root by PARTUUID, ESP) + auto-grow marker dirs"
cat > "$MNT/etc/fstab" <<EOF
PARTUUID=$ROOT_PARTUUID  /          ext4  defaults,noatime,x-systemd.growfs                 0 1
UUID=$ESP_UUID           /boot/efi  vfat  umask=0077,nofail,x-systemd.automount,x-systemd.idle-timeout=120,x-systemd.device-timeout=45     0 2
EOF
# ESP is nofail + fsck pass 2: a missing/corrupt EFI partition must never fail
# local-fs.target (emergency mode on a console nobody sees) — the boot log's
# writer mounts it itself / finds it by label when fstab could not [R51].
# v7.5: an AUTOMOUNT — mounted on first access (systemd: device + fsck), unmounted after 120 s
# idle (a pulled stick finds a clean FAT). The boot never waits for it: with a 10 s
# device timeout udev's slow coldplug on the stick failed the mount (v7.3 stick run: WARN).

echo "[7/9] auto-resize service (growpart + resize2fs on first boot)"
install -D /dev/stdin "$MNT/usr/local/sbin/kairos-growroot.sh" <<'EOF'
#!/bin/sh
set -e
ROOT=$(findmnt -no SOURCE /)
DISK="/dev/$(lsblk -no PKNAME "$ROOT")"
PART=$(echo "$ROOT" | grep -oE '[0-9]+$')
echo "kairos-growroot: $ROOT on $DISK part $PART"
growpart "$DISK" "$PART" 2>/dev/null || true
resize2fs "$ROOT" 2>/dev/null || true
touch /var/lib/kairos-resized
EOF
chmod +x "$MNT/usr/local/sbin/kairos-growroot.sh"
cat > "$MNT/etc/systemd/system/kairos-growroot.service" <<'EOF'
[Unit]
Description=Grow kAIrOS root to fill the disk on first boot
DefaultDependencies=no
After=local-fs.target
Before=kairos.service kairos-payload.service
ConditionPathExists=!/var/lib/kairos-resized
# a failed grow (bad partition table / resize2fs) writes a log to the stick [R51]
# (own handler instance per trigger, see kairos-bootlog-failure@.service)
OnFailure=kairos-bootlog-failure@%n.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/local/sbin/kairos-growroot.sh

[Install]
WantedBy=multi-user.target
EOF

echo "[8/9] convert live->installed; install systemd-boot from host artifacts"
# Host artifacts (installed on the build host where apt has network). The chroot's apt
# has no working indices, so we DON'T install there — only do offline package *removal*
# (live-boot) + initramfs regen in the chroot, and copy boot/resize binaries from the host.
EFISRC=/usr/lib/systemd/boot/efi/systemd-bootx64.efi
[ -f "$EFISRC" ] || { echo "FATAL: host lacks $EFISRC — run: apt-get install systemd-boot-efi"; exit 92; }
command -v growpart >/dev/null || { echo "FATAL: host lacks growpart — apt-get install cloud-guest-utils"; exit 91; }
install -D /usr/bin/growpart "$MNT/usr/bin/growpart"
install -D /usr/sbin/sgdisk  "$MNT/usr/sbin/sgdisk"
install -D "$EFISRC"         "$MNT/usr/lib/systemd/boot/efi/systemd-bootx64.efi"

# --- boot-path files owned by the repo tree (ONE source: the ISO's includes.chroot,
# this image build and pod/testrig/sync-guest.sh all install the same files, and the
# test suite verifies the real ones — no heredoc copies to drift):
#   kairos-gpu-setup + nvidia-load.service  NVIDIA loaded after basic.target and
#                                           before getty@tty1 (D7: never "after
#                                           multi-user AND before getty" — that was
#                                           an ordering cycle), persistenced started
#                                           only when a device is bound (D11)
#   /etc/kernel/install.conf                layout=other: kernel-install writes no
#                                           foreign loader entries (D4)
#   zz-kairos-esp hooks + kairos-esp-sync   an apt kernel upgrade refreshes the
#                                           kAIrOS entry's kernel + initrd on the ESP
INC="$REPO/iso/config/includes.chroot"
install -D -m 755 "$INC/usr/local/sbin/kairos-gpu-setup"           "$MNT/usr/local/sbin/kairos-gpu-setup"
install -D -m 755 "$INC/usr/local/sbin/kairos-esp-sync"            "$MNT/usr/local/sbin/kairos-esp-sync"
install -D -m 644 "$INC/etc/systemd/system/nvidia-load.service"    "$MNT/etc/systemd/system/nvidia-load.service"
install -D -m 644 "$INC/etc/kernel/install.conf"                   "$MNT/etc/kernel/install.conf"
install -D -m 755 "$INC/etc/kernel/postinst.d/zz-kairos-esp"       "$MNT/etc/kernel/postinst.d/zz-kairos-esp"
install -D -m 755 "$INC/etc/initramfs/post-update.d/zz-kairos-esp" "$MNT/etc/initramfs/post-update.d/zz-kairos-esp"
#   boot screen [R73]: the initramfs hook + init-top/init-bottom/panic scripts, the
#   userspace unit, the emergency/rescue drop-ins and the ctl; the painter binary itself
#   is installed by scripts/splash/install.sh in the chroot step (before update-initramfs)
for f in etc/initramfs-tools/hooks/kairos-splash etc/initramfs-tools/scripts/init-top/kairos-splash \
         etc/initramfs-tools/scripts/init-bottom/kairos-splash etc/initramfs-tools/scripts/panic/kairos-splash \
         usr/local/sbin/kairos-splash-ctl; do
  install -D -m 755 "$INC/$f" "$MNT/$f"
done
for f in etc/systemd/system/kairos-splash.service etc/systemd/system/emergency.service.d/kairos-splash.conf \
         etc/systemd/system/rescue.service.d/kairos-splash.conf; do
  install -D -m 644 "$INC/$f" "$MNT/$f"
done

mount --bind /dev "$MNT/dev"; mount --bind /proc "$MNT/proc"; mount --bind /sys "$MNT/sys"
# QUOTED delimiter: the body runs in the chroot's bash. Unquoted, the build host
# expanded it first — $d (kairos-gpu-setup's loop) hit set -u, the whole block
# died and nothing in it (bootlog, ssh keys, initramfs) ever happened. Nothing in
# the block needs a host variable; assertions below prove it ran.
chroot "$MNT" /bin/bash -e <<'CHROOT'
set -e
systemctl disable kairos-payload.service 2>/dev/null || true
rm -f /etc/systemd/system/multi-user.target.wants/kairos-payload.service
# remove live-only packages (offline; no network needed) so a normal root= initrd is built.
# NOTE: this drops live-config, which used to regenerate SSH host keys at boot — so we
# generate them here (sshd won't start without them) and re-gen on first boot for uniqueness.
dpkg --purge --force-depends live-boot live-boot-initramfs-tools live-config live-config-systemd 2>/dev/null || true
ssh-keygen -A                      # host keys so ssh.service starts
systemctl enable ssh kairos-growroot.service

# --- hybrid-GPU (AMD iGPU primary + NVIDIA 4070 secondary) + USB-boot hardening ---
# Forcing the secondary dGPU's KMS at boot panics muxless laptops whose panel is on the
# iGPU. Let amdgpu drive the display; make the 4070 COMPUTE-ONLY, loaded by a
# non-critical service (nvidia-load: after basic.target, before the kiosk's getty,
# 60 s cap) so a finicky dGPU can never fail or block boot/SSH.
rm -f /etc/modules-load.d/nvidia.conf
# Storage controllers the initramfs must ALWAYS carry: vmd (Intel VMD/RST — on by
# default on many Core Ultra laptops; without it the NVMe is invisible and root=
# is never found), nvme_core, ahci, USB sticks, and eMMC (sdhci_pci/mmc_block).
cat > /etc/initramfs-tools/modules <<M
usb_storage
uas
xhci_pci
xhci_hcd
vmd
nvme_core
nvme
ahci
sd_mod
sdhci_pci
mmc_block
ext4
amdgpu
M
# GPU setup is hardware-aware [R41/R50]: /usr/local/sbin/kairos-gpu-setup (installed
# from the repo above, run by nvidia-load.service after basic.target and before
# getty@tty1) loads NVIDIA for COMPUTE always and drives the DISPLAY only when the
# monitor is on it (boot_vga) — see the script. nvidia-persistenced: the driver
# package enables it unconditionally, so it FAILED on every machine without an
# NVIDIA GPU (R63) and was the slowest unit of a GPU boot; disabled here and
# started by kairos-gpu-setup only once a device is bound to the driver (D11).
systemctl enable nvidia-load.service
systemctl disable nvidia-persistenced.service 2>/dev/null || true
rm -f /etc/systemd/system/multi-user.target.wants/nvidia-persistenced.service
# regenerate host keys once per flashed stick, before sshd starts
cat > /etc/systemd/system/kairos-firstboot.service <<'U'
[Unit]
Description=kAIrOS first-boot (fresh SSH host keys)
Before=ssh.service
ConditionPathExists=!/var/lib/kairos-firstboot-done
[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/bin/sh -c 'rm -f /etc/ssh/ssh_host_* && ssh-keygen -A && touch /var/lib/kairos-firstboot-done'
[Install]
WantedBy=multi-user.target
U
systemctl enable kairos-firstboot.service
# boot diagnostics -> readable EFI partition (KAIROS-LOGS) [R51]: late capture,
# early breadcrumb (attempt counter), emergency capture, X-loop watcher. The
# failure capture is pulled in by OnFailure= only. Loud: a missing unit fails the build.
chmod +x /usr/local/sbin/kairos-bootlog /usr/local/sbin/kairos-bootlog-early /usr/local/sbin/kairos-prewarm
systemctl enable kairos-bootlog.service kairos-bootlog-early.service \
                 kairos-bootlog-emergency.service kairos-bootlog-xfail.path \
                 kairos-bootlog-stall.timer kairos-prewarm.service
mkdir -p /var/log/journal /var/log/kairos   # persistent journal (survives a failed boot)
chown kairos: /var/log/kairos 2>/dev/null || true
# the boot screen painter [R73] goes into /usr/local/sbin BEFORE the initrd is built: its
# initramfs hook copy_execs it (a static binary, committed in scripts/splash/)
sh /opt/vibeos/os/scripts/splash/install.sh /opt/vibeos/os
update-initramfs -u -k all
CHROOT

# prove the chroot block really ran on the target (see the heredoc note above):
# every unit it enables exists as a wants-symlink, the GPU script kept its $d,
# ssh host keys exist. A silent skip here shipped images with no bootlog at all.
W="$MNT/etc/systemd/system"
for u in multi-user.target.wants/kairos-bootlog.service \
         sysinit.target.wants/kairos-bootlog-early.service \
         emergency.target.wants/kairos-bootlog-emergency.service \
         rescue.target.wants/kairos-bootlog-emergency.service \
         multi-user.target.wants/kairos-bootlog-xfail.path \
         sysinit.target.wants/kairos-bootlog-stall.timer \
         sysinit.target.wants/kairos-prewarm.service \
         multi-user.target.wants/kairos-growroot.service \
         multi-user.target.wants/nvidia-load.service \
         multi-user.target.wants/kairos-firstboot.service \
         sysinit.target.wants/kairos-splash.service; do
  [ -L "$W/$u" ] || { echo "FATAL: chroot step did not enable $u (heredoc expanded on the host?)"; exit 94; }
done
# [R73] the boot screen really ships: painter installed, and inside the initrd the hook built
[ -x "$MNT/usr/local/sbin/kairos-splash" ] || { echo "FATAL: /usr/local/sbin/kairos-splash missing (scripts/splash/install.sh)"; exit 99; }
KV=$(ls "$MNT/lib/modules" | sort -V | tail -1)
lsinitramfs "$MNT/boot/initrd.img-$KV" 2>/dev/null | grep -q 'sbin/kairos-splash$' \
  || { echo "FATAL: the initrd carries no kairos-splash (initramfs hook not run?)"; exit 99; }
for f in init-top init-bottom panic; do
  lsinitramfs "$MNT/boot/initrd.img-$KV" 2>/dev/null | grep -q "scripts/$f/kairos-splash$" \
    || { echo "FATAL: the initrd lacks scripts/$f/kairos-splash"; exit 99; }
done
grep -q 'for d in "\$SYS"/bus/pci/devices/\*/boot_vga' "$MNT/usr/local/sbin/kairos-gpu-setup" \
  && grep -q '"\$d"' "$MNT/usr/local/sbin/kairos-gpu-setup" \
  || { echo "FATAL: kairos-gpu-setup lost its \$d expansions (heredoc quoting)"; exit 95; }
ls "$MNT"/etc/ssh/ssh_host_*_key >/dev/null 2>&1 || { echo "FATAL: no ssh host keys generated in chroot"; exit 96; }
[ -d "$MNT/var/log/journal" ] || { echo "FATAL: /var/log/journal missing (persistent journal)"; exit 97; }
# D11: persistenced must not be pulled in unconditionally (it fails without an NVIDIA GPU)
[ ! -e "$W/multi-user.target.wants/nvidia-persistenced.service" ] \
  || { echo "FATAL: nvidia-persistenced.service is still enabled (fails on every non-NVIDIA machine, R63)"; exit 98; }
# D4: kernel-install must never add loader entries of its own; the kAIrOS refresh hooks are in place
grep -q '^layout=other' "$MNT/etc/kernel/install.conf" \
  || { echo "FATAL: /etc/kernel/install.conf lacks layout=other (kernel-install would write a Debian loader entry)"; exit 99; }
for f in usr/local/sbin/kairos-esp-sync etc/kernel/postinst.d/zz-kairos-esp etc/initramfs/post-update.d/zz-kairos-esp; do
  [ -x "$MNT/$f" ] || { echo "FATAL: $f missing or not executable"; exit 99; }
done
echo "chroot step verified: bootlog/growroot/nvidia-load/firstboot enabled, persistenced not, gpu-setup intact, ssh keys present, kernel-install off"

# --- names [fix 2026-10-22, docs/FIXES-2026-10-22.md]: /etc/hosts names the host (startx's xauth looked
# localhost.localdomain up over DNS: X started 40 s late where DNS is dropped) and /etc/resolv.conf is NetworkManager's
# /run file, never a file baked by the build machine (v7.5-3: "nameserver 10.0.2.3"). live-build's chroot_hosts /
# chroot_resolv steps rewrite both at the end of the ISO build, so the image sets them here, from the overlay, after
# the chroot step (which may still want the build's resolver); tmpfiles.d/kairos.conf re-links resolv.conf at boot.
install -D -m 644 "$INC/etc/hosts" "$MNT/etc/hosts"
install -D -m 644 "$INC/etc/NetworkManager/conf.d/20-kairos-dns.conf" "$MNT/etc/NetworkManager/conf.d/20-kairos-dns.conf"
install -D -m 644 "$INC/etc/tmpfiles.d/kairos.conf" "$MNT/etc/tmpfiles.d/kairos.conf"
HN=$(tr -d '[:space:]' < "$MNT/etc/hostname" 2>/dev/null || true); [ -n "$HN" ] || HN=localhost.localdomain
sed -i -E "s/^127\.0\.1\.1[[:space:]].*$/127.0.1.1\t$HN/" "$MNT/etc/hosts"     # the line names THIS image's hostname
rm -f "$MNT/etc/resolv.conf"
ln -s /run/NetworkManager/resolv.conf "$MNT/etc/resolv.conf"
grep -qxF "$(printf '127.0.1.1\t%s' "$HN")" "$MNT/etc/hosts" \
  || { echo "FATAL: /etc/hosts does not name the image's hostname ($HN)"; exit 99; }
[ "$(readlink "$MNT/etc/resolv.conf")" = /run/NetworkManager/resolv.conf ] \
  || { echo "FATAL: /etc/resolv.conf is not NetworkManager's /run file"; exit 99; }
echo "names: /etc/hosts names $HN on 127.0.1.1; /etc/resolv.conf -> /run/NetworkManager/resolv.conf"

# --- install systemd-boot to the ESP manually (operate on the mounted image) ---
ESP="$MNT/boot/efi"
KVER=$(ls "$MNT/lib/modules" | sort -V | tail -1)
mkdir -p "$ESP/EFI/systemd" "$ESP/EFI/BOOT" "$ESP/kairos" "$ESP/loader/entries"
cp -f "$EFISRC" "$ESP/EFI/systemd/systemd-bootx64.efi"
cp -f "$EFISRC" "$ESP/EFI/BOOT/BOOTX64.EFI"      # removable/fallback path -> boots on any UEFI
cp "$MNT/boot/vmlinuz-$KVER"    "$ESP/kairos/vmlinuz"
cp "$MNT/boot/initrd.img-$KVER" "$ESP/kairos/initrd"
# hide Linux completely (R28): no boot menu, no kernel text, no cursor — the
# firmware logo stays on the screen until the kAIrOS boot screen takes over (R73).
# `default` is matched against entry IDS, i.e. file names INCLUDING .conf: the old
# "default kairos" matched nothing, so systemd-boot took its sort order — and the
# Debian entry kernel-install had written sorted first (D4). "kairos.conf" is
# exact; the entries present are asserted below.
cat > "$ESP/loader/loader.conf" <<L
default kairos.conf
timeout 0
console-mode max
editor no
L
# console order matters: the LAST console= becomes /dev/console, where systemd's
# emergency prompt / fsck failure / "Press Enter for maintenance" go. A laptop has
# no ttyS0 — with it last those went nowhere (black screen, no explanation). The
# panel (tty1) is last; the serial console stays for the QEMU testrig (kernel
# messages still reach every console= listed). For a fully interactive serial
# session pick the hidden "kairos-serial" entry (a keypress during boot opens the
# menu, or in the guest: bootctl set-oneshot kairos-serial.conf && reboot).
cat > "$ESP/loader/entries/kairos.conf" <<E
title   kAIrOS by mAIer
linux   /kairos/vmlinuz
initrd  /kairos/initrd
options root=PARTUUID=$ROOT_PARTUUID rw rootwait quiet loglevel=0 systemd.show_status=false rd.udev.log_level=3 vt.global_cursor_default=0 systemd.getty_auto=0 console=ttyS0,115200 console=tty1
E
cat > "$ESP/loader/entries/kairos-serial.conf" <<E
title   kAIrOS (serial debug console)
linux   /kairos/vmlinuz
initrd  /kairos/initrd
options root=PARTUUID=$ROOT_PARTUUID rw rootwait loglevel=4 systemd.show_status=true console=tty1 console=ttyS0,115200
E
[ -f "$ESP/EFI/BOOT/BOOTX64.EFI" ] || { echo "FATAL: ESP bootloader missing"; exit 93; }
# D4: the ESP carries exactly the two kAIrOS entries. Anything else (the Boot Loader
# Specification entry + <machine-id>/<version>/ tree kernel-install wrote before
# layout=other, or a leftover from the source rootfs) is removed, then asserted:
# a foreign entry sorted first and was what really booted — the kAIrOS cmdline never
# took effect and a keypress showed a Debian menu.
for e in "$ESP"/loader/entries/*.conf; do
  case "$(basename "$e")" in kairos.conf|kairos-serial.conf) ;; *) echo "  removing foreign loader entry $(basename "$e")"; rm -f "$e" ;; esac
done
for d in "$ESP"/*/; do
  d=${d%/}; case "$(basename "$d")" in EFI|kairos|loader|KAIROS-LOGS) ;;
    *) if [ "$(basename "$d" | grep -cE '^[0-9a-f]{32}$')" = 1 ]; then echo "  removing kernel-install tree $(basename "$d")"; rm -rf "$d"; fi ;;
  esac
done
ENTRIES=$(ls "$ESP"/loader/entries/*.conf | xargs -n1 basename | sort | tr '\n' ' ')
[ "$ENTRIES" = "kairos-serial.conf kairos.conf " ] || { echo "FATAL: loader entries are '$ENTRIES', expected exactly kairos.conf + kairos-serial.conf"; exit 93; }
DEF=$(sed -n 's/^default[[:space:]]*//p' "$ESP/loader/loader.conf")
[ "$DEF" = kairos.conf ] && [ -f "$ESP/loader/entries/$DEF" ] || { echo "FATAL: loader.conf default '$DEF' is not the kairos.conf entry"; exit 93; }
echo "bootloader + entry installed OK ($(du -h "$ESP/EFI/BOOT/BOOTX64.EFI" | cut -f1)); entries: $ENTRIES; default $DEF"
sync
umount -R "$MNT"
trap - EXIT
losetup -d "$LOOP"

echo "[9/9] gzip -> .img.gz (stream to roomy /mnt/b to spare the vhdx/C:)"
# Output to B: (Windows-accessible, hundreds of GB free) so the raw img + gz never
# coexist on the space-constrained C: vhdx. Fall back to in-place if B: is gone.
GZOUT=/mnt/b/kairos-hwtest-4070.img.gz
if touch /mnt/b/.kw 2>/dev/null; then rm -f /mnt/b/.kw; else GZOUT="${OUT}.gz"; fi
rm -f "$GZOUT"
gzip -1 -c "$OUT" > "$GZOUT"
rm -f "$OUT"          # free the 27GB raw immediately
sync
ls -lh "$GZOUT"
cp -f "$REPO/iso/flash-img.md" "$(dirname "$GZOUT")/kairos-FLASH-README.txt" 2>/dev/null || true
echo "IMG_GZ=$GZOUT"
echo "=== IMG BUILD COMPLETE $(date) ==="
