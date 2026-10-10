#!/usr/bin/env bash
# Create the kAIrOS hardware-test USB: bootable lean ISO + a KAIROS_DATA partition holding
# the baked 8gb model set (scripts/check_tier_present.py --list: 4 files + 39 bake-flagged add-on files, 25.0 GB) + offline
# knowledge (the Wikipedia ZIM, 12.5 GB). Run in WSL with the stick attached via usbipd (so it
# appears as /dev/sdX). DESTRUCTIVE — but NOTHING is written before the size check passes and
# ERASE is typed: the need (ISO + models + knowledge + overhead, ≈ 40 GB today) is computed from
# the actual bake list and the device is refused when smaller. A "32 GB" stick no longer fits;
# use a 64 GB class stick (N3, docs/FIXES-2026-09-29.md).
#
#   write-usb.sh /dev/sdX [iso] [models_dir]
set -euo pipefail
DEV=${1:?usage: write-usb.sh /dev/sdX [iso] [models_dir]}
ISO=${2:-/srv/kairos-out/kairos-hwtest-4070.iso}
MODELS=${3:-/opt/vibeos/models}
KNOW=/opt/vibeos/knowledge
REPO=$(cd "$(dirname "$0")/.." && pwd)
MANIFEST=$REPO/manifests/tier-8gb.json
LISTER=$REPO/scripts/check_tier_present.py

[ -b "$DEV" ] || { echo "not a block device: $DEV"; exit 1; }
[ -f "$ISO" ] || { echo "no ISO: $ISO"; exit 1; }
for t in sgdisk partprobe mkfs.ext4 rsync; do command -v $t >/dev/null || \
    { apt-get update -qq; DEBIAN_FRONTEND=noninteractive apt-get install -y -qq gdisk parted e2fsprogs rsync; break; }; done

# ---- what goes on the stick, and whether it fits — BEFORE anything is erased [R47/N3] ----
# only the baked set of the 8gb manifest: the non-addon files + the bake-flagged add-ons (the
# translation `fast` model + the dictionaries [R71], docs/IMAGE-BAKE-ADDONS-2026-10-07.md) —
# every other add-on is downloaded in-OS; sizes follow symlinks (a dangling link fails the gate)
BAKE_LIST=$(mktemp)
python3 "$LISTER" "$MANIFEST" "$MODELS" --list > "$BAKE_LIST"
python3 "$LISTER" "$MANIFEST" "$MODELS" || { echo "the 8gb baked set is incomplete under $MODELS (see above) — nothing was written"; rm -f "$BAKE_LIST"; exit 1; }
MODELS_B=$(python3 "$LISTER" "$MANIFEST" "$MODELS" --bytes)
ISO_B=$(stat -Lc%s "$ISO")
KNOW_B=0; for z in "$KNOW"/*.zim; do [ -e "$z" ] && KNOW_B=$(( KNOW_B + $(stat -Lc%s "$z") )); done
DATA_B=$(( MODELS_B + KNOW_B ))
# the ISO image + the data partition with ~5 % ext4 metadata/reserve + 1 GiB for the moved GPT/alignment
NEED_B=$(( ISO_B + DATA_B + DATA_B / 20 + 1073741824 ))
SZ=$(blockdev --getsize64 "$DEV")
gb() { printf "%d.%d" $(( $1 / 1000000000 )) $(( ($1 % 1000000000) / 100000000 )); }   # decimal GB, as sticks are sold
echo "TARGET : $DEV  ($(gb "$SZ") GB)"; lsblk -d -o NAME,SIZE,MODEL,TRAN "$DEV" 2>/dev/null || true
echo "ISO    : $ISO ($(gb "$ISO_B") GB)"
echo "MODELS : $(wc -l < "$BAKE_LIST") baked files of $(basename "$MANIFEST") under $MODELS ($(gb "$MODELS_B") GB)"
echo "KNOW   : $KNOW ($(gb "$KNOW_B") GB)"
echo "NEED   : $(gb "$NEED_B") GB  (ISO + models + knowledge + 5 % filesystem overhead + 1 GiB)"
[ "$SZ" -ge "$NEED_B" ] || { echo "REFUSED: $DEV holds $(gb "$SZ") GB but $(gb "$NEED_B") GB are needed — use a 64 GB class stick. Nothing was written."; rm -f "$BAKE_LIST"; exit 1; }
echo
read -r -p "This ERASES $DEV completely. Type ERASE to continue: " ok
[ "$ok" = "ERASE" ] || { echo "aborted."; rm -f "$BAKE_LIST"; exit 1; }

echo "[1/5] wiping signatures + dd lean ISO (bootable)…"
wipefs -a "$DEV" >/dev/null 2>&1 || true
dd if="$ISO" of="$DEV" bs=4M status=progress oflag=direct conv=fsync
sync; partprobe "$DEV" 2>/dev/null || true; sleep 2

echo "[2/5] moving GPT backup to end of disk + adding KAIROS_DATA partition…"
sgdisk -e "$DEV" 2>/dev/null || true           # relocate backup GPT to true end
ISO_END_MIB=$(( ( ISO_B / 1048576 ) + 8 ))
sgdisk -n 0:${ISO_END_MIB}MiB:0 -c 0:KAIROS_DATA -t 0:8300 "$DEV"
sync; partprobe "$DEV"; sleep 2

# resolve the new partition node (…N or …pN)
PART=$(lsblk -ln -o NAME "$DEV" | tail -1)
PDEV="/dev/$PART"
echo "[3/5] formatting $PDEV ext4 (label KAIROS_DATA)…"
mkfs.ext4 -q -L KAIROS_DATA "$PDEV"

echo "[4/5] copying models + knowledge to the stick…"
MNT=$(mktemp -d)
mount "$PDEV" "$MNT"
mkdir -p "$MNT/models" "$MNT/knowledge"
# exactly the bake list; -L copies a symlinked model as its target, never as a link
rsync -aL --info=progress2 --files-from="$BAKE_LIST" "$MODELS/" "$MNT/models/"; rm -f "$BAKE_LIST"
[ -d "$KNOW" ] && rsync -aL "$KNOW"/*.zim "$MNT/knowledge/" 2>/dev/null || true
sync
umount "$MNT"; rmdir "$MNT"

echo "[5/5] done."
lsblk -o NAME,SIZE,LABEL,FSTYPE "$DEV"
echo "kAIrOS USB ready — boot the 4070 laptop from it (UEFI or BIOS)."
