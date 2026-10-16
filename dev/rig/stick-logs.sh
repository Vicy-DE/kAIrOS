#!/usr/bin/env bash
# stick-logs.sh — run on the development machine AFTER a kAIrOS run from the USB stick (the target
# machine boots the stick; there is no SSH into it — the stick's logs ARE the debugging channel).
# Copies everything the run recorded into
#   $KAIROS_HOME/stick-runs/<YYYYmmdd-HHMMSS>/   with SUMMARY.txt on top
# and changes nothing on the stick: both partitions are mounted READ-ONLY through udisks (no sudo for
# removable media in a desktop session) and unmounted again if this script mounted them. An ext4 journal
# left dirty by a hard power-off is replayed by the kernel on mount — the same recovery kAIrOS's next
# boot would do.
#
#   dev/rig/stick-logs.sh                         # the plugged-in kAIrOS stick (found by its KAIROS_ROOT label)
#   dev/rig/stick-logs.sh /dev/disk/by-id/<stick> # a specific stick (the whole disk, not a partition)
#   STICK_ESP=<dir> STICK_ROOT=<dir> dev/rig/stick-logs.sh   # already-mounted / copied trees (tests)
#   BOOTS=10 dev/rig/stick-logs.sh                # export more boots of the journal
# The first line of SUMMARY.txt's "captures" block is the verdict of the latest boot (PASS/WARN/FAIL) —
# the same KAIROS-LOGS/latest.json the VM test gates on. Paste it into a bug report.
set -u
. "$(dirname "$0")/env.sh"
# auto: the disk that carries the KAIROS_ROOT partition (by-label resolves to the partition; its parent
# device is the stick). KAIROS_STICK (dev/rig/.env) pins one explicitly.
find_stick(){
  local part disk
  part=$(readlink -f /dev/disk/by-label/KAIROS_ROOT 2>/dev/null) || return 1
  [ -n "$part" ] || return 1
  disk=$(lsblk -no pkname "$part" 2>/dev/null | head -n 1)
  [ -n "$disk" ] || return 1
  # prefer the stable by-id path so -part1/-part2 work below
  for p in /dev/disk/by-id/*; do
    case "$p" in *-part[0-9]*) continue ;; esac
    [ "$(readlink -f "$p")" = "/dev/$disk" ] && { printf '%s\n' "$p"; return 0; }
  done
  printf '/dev/%s\n' "$disk"
}
DEV=${1:-${KAIROS_STICK:-$(find_stick || true)}}
OUT=${OUT:-$KAIROS_HOME/stick-runs/$(date +%Y%m%d-%H%M%S)}
BOOTS=${BOOTS:-6}
mkdir -p "$OUT" || exit 1
NOTE="$OUT/NOTES.txt"; : > "$NOTE"
note(){ printf '%s\n' "$*" | tee -a "$NOTE" >&2; }
MOUNTED=()
# partition device names: /dev/disk/by-id/X-partN, /dev/sdXN, /dev/nvme0n1pN
partdev(){ case "$1" in /dev/disk/by-id/*) printf '%s-part%s\n' "$1" "$2" ;; *[0-9]) printf '%sp%s\n' "$1" "$2" ;; *) printf '%s%s\n' "$1" "$2" ;; esac; }

# ---- find + mount (ro) the two partitions: 1 = EFI (vfat, KAIROS-LOGS), 2 = KAIROS_ROOT (ext4)
mnt(){  # mnt <partition device> -> prints its mountpoint (mounting it read-only if needed)
  local part=$1 m
  m=$(findmnt -rn -S "$(readlink -f "$part")" -o TARGET 2>/dev/null | head -n 1)
  if [ -z "$m" ]; then
    udisksctl mount -b "$part" -o ro --no-user-interaction >/dev/null 2>"$OUT/.mount.err" \
      || { note "could not mount $part: $(cat "$OUT/.mount.err")"; return 1; }
    MOUNTED+=("$part")
    m=$(findmnt -rn -S "$(readlink -f "$part")" -o TARGET | head -n 1)
  fi
  printf '%s\n' "$m"
}
cleanup(){ for p in "${MOUNTED[@]}"; do udisksctl unmount -b "$p" --no-user-interaction >/dev/null 2>&1 || note "unmount $p failed — eject the stick from the file manager"; done; rm -f "$OUT/.mount.err"; }
trap cleanup EXIT
ESP=${STICK_ESP:-}; ROOT=${STICK_ROOT:-}
if [ -z "$ESP$ROOT" ]; then
  [ -n "$DEV" ] && [ -e "$DEV" ] || { note "stick not found (no KAIROS_ROOT partition plugged in? pass the disk: $0 /dev/disk/by-id/usb-…; ls -l /dev/disk/by-id/ | grep usb)"; exit 1; }
  ESP=$(mnt "$(partdev "$DEV" 1)") || ESP=""
  ROOT=$(mnt "$(partdev "$DEV" 2)") || ROOT=""
fi
note "stick: ${DEV:-(mounted trees)}  ESP=${ESP:-?}  ROOT=${ROOT:-?}  -> $OUT"

# ---- 1. the EFI partition's boot logs (verdicts, attempts, crash-*.txt)
if [ -n "$ESP" ] && [ -d "$ESP/KAIROS-LOGS" ]; then cp -a "$ESP/KAIROS-LOGS" "$OUT/" 2>>"$NOTE"
else note "no KAIROS-LOGS on the EFI partition"; fi

# ---- 2. the root partition: kAIrOS logs, engine logs, crash archive, the full journal
if [ -n "$ROOT" ]; then
  mkdir -p "$OUT/var-log-kairos" "$OUT/pstore-stick" "$OUT/journal"
  cp -a "$ROOT/var/log/kairos/." "$OUT/var-log-kairos/" 2>>"$NOTE"
  cp -a "$ROOT/var/lib/systemd/pstore/." "$OUT/pstore-stick/" 2>/dev/null
  # the X server's own logs live in the kiosk user's home (rootless X), the config in /etc/X11
  mkdir -p "$OUT/xorg"
  cp -a "$ROOT"/home/kairos/.local/share/xorg/Xorg.*.log* "$ROOT"/var/log/Xorg.*.log* "$OUT/xorg/" 2>/dev/null
  cp -a "$ROOT"/etc/X11/xorg.conf "$ROOT"/etc/X11/xorg.conf.d "$OUT/xorg/" 2>/dev/null
  for f in "$ROOT/opt/vibeos/cache/sd-server.log" "$ROOT/opt/vibeos/active-tier" "$ROOT/data/settings.json"; do
    [ -r "$f" ] && cp -a "$f" "$OUT/" 2>/dev/null
  done
  J="$ROOT/var/log/journal"
  if journalctl -D "$J" --list-boots --no-pager > "$OUT/journal/boots.txt" 2>"$OUT/journal/.err"; then
    # newest BOOTS boots, selected by _BOOT_ID: `-b -N` does not work on a foreign journal
    # directory ("no persistent journal was found" -> it exported nothing, 2026-10-07)
    i=0
    for id in $(awk 'NR>1 && $2 ~ /^[0-9a-f]{32}$/ {print $2}' "$OUT/journal/boots.txt" | tac | head -n "$BOOTS"); do
      journalctl -D "$J" _BOOT_ID="$id" --no-pager -o short-iso-precise > "$OUT/journal/boot-minus$i.txt" 2>/dev/null
      journalctl -D "$J" _BOOT_ID="$id" --no-pager -o short-iso-precise -p warning > "$OUT/journal/boot-minus$i.warn.txt" 2>/dev/null
      [ -s "$OUT/journal/boot-minus$i.txt" ] || note "boot $id: no readable entries (journal file truncated/unclosed? its boot capture on the EFI partition holds the kernel log)"
      i=$((i + 1))
    done
    rm -f "$OUT/journal/.err"
  else
    note "journal not readable as $(id -un): $(head -c 300 "$OUT/journal/.err") — re-run with:"
    note "  sudo journalctl -D '$J' --list-boots ; sudo journalctl -D '$J' -b -0 -o short-iso-precise > $OUT/journal/boot-minus0.txt"
  fi
fi

# ---- 3. this machine's own crash archive: if its OS booted first after a kAIrOS kernel
# crash, ITS systemd-pstore took the panic text out of the firmware NVRAM
if ls -A /var/lib/systemd/pstore 2>/dev/null | grep -q .; then
  mkdir -p "$OUT/pstore-host"; cp -a /var/lib/systemd/pstore/. "$OUT/pstore-host/" 2>/dev/null \
    || note "host /var/lib/systemd/pstore has records but is not readable: sudo cp -a /var/lib/systemd/pstore $OUT/pstore-host"
fi

# ---- SUMMARY: what to read first
{
  printf 'kAIrOS stick run — collected %s from %s\n\n' "$(date '+%Y-%m-%d %H:%M:%S %Z')" "${DEV:-(mounted trees)}"
  echo "== boot attempts (KAIROS-LOGS/attempts.txt, newest last) =="
  tail -n 25 "$OUT/KAIROS-LOGS/attempts.txt" 2>/dev/null || echo "(none)"
  echo; echo "== captures (newest first): reason + verdict =="
  for f in $(ls -1t "$OUT"/KAIROS-LOGS/boot-*.txt 2>/dev/null); do
    printf '%s  %s\n   %s\n' "$(basename "$f")" "$(sed -n 1p "$f" | tr -d '=' | sed 's/^ *//')" "$(sed -n 3p "$f" | cut -c1-200)"
  done
  echo; echo "== kernel crashes (panic text saved via EFI pstore) =="
  CR=$(ls -1d "$OUT"/KAIROS-LOGS/crash-[0-9]*.txt "$OUT"/pstore-stick/* "$OUT"/pstore-host/* 2>/dev/null)
  if [ -n "$CR" ]; then printf '%s\n' "$CR"; for c in "$OUT"/KAIROS-LOGS/crash-[0-9]*.txt; do [ -f "$c" ] && printf '   %s: %s\n' "$(basename "$c")" "$(grep -m 1 -E 'Kernel panic|BUG:|Oops|general protection' "$c" | cut -c1-160)"; done
  else echo "(none)"; fi
  echo; echo "== failed kAIrOS jobs (image/app/video/music/game/page) — [kernel] job … error =="
  for f in $(ls -1r "$OUT"/journal/boot-minus[0-9]*.txt 2>/dev/null); do   # oldest boot first
    case "$f" in *.warn.txt) continue ;; esac
    grep -hE '\[kernel\] job [0-9a-f]+ [a-z_]+ (error after|done after [0-9.]+s: )|\[kernel\] web page .* failed|llama-server (exited|had exited|did not become)|download failed' "$f"
  done 2>/dev/null | tail -n 40 | cut -c1-400
  # what v7.2+ changed and the owner reported (boot time, pointer, LLM load): the numbers to compare
  echo; echo "== measurements: boot time / NVIDIA / LLM load per boot (oldest first), X session, prewarm =="
  for f in $(ls -1r "$OUT"/journal/boot-minus[0-9]*.txt 2>/dev/null); do
    case "$f" in *.warn.txt) continue ;; esac
    printf -- '-- %s\n' "$(basename "$f")"
    grep -hE 'systemd\[1\]: Startup finished|NVRM: loading NVIDIA|Started nvidia-persistenced|\[kernel\] (starting llama-server|llama-server (ready|exited|did not))' "$f" | cut -c1-260
  done 2>/dev/null
  echo "-- X sessions (/var/log/kairos/x.log.1 then x.log: uptime at startx, .xinitrc start, pointer)"
  grep -hE '^===== X attempt|^xinitrc: (started|pointer|kiosk pids|boot animation ends)' "$OUT"/var-log-kairos/x.log.1 "$OUT"/var-log-kairos/x.log 2>/dev/null | cut -c1-160
  echo "-- /var/log/kairos/prewarm.log"
  tail -n 12 "$OUT/var-log-kairos/prewarm.log" 2>/dev/null || echo "(none: image older than v7.2, or the root is not on USB)"
  echo; echo "== journal boots on the stick =="
  cat "$OUT/journal/boots.txt" 2>/dev/null | tail -n 12
  echo; echo "== errors (priority err and worse) of the newest boot =="
  grep -E " (kernel|systemd\[1\]|python3\[[0-9]+\]|Xorg|kairos)[^:]*: .*([Ee]rror|[Ff]ail|FAIL)" "$OUT/journal/boot-minus0.txt" 2>/dev/null | grep -v "ACPI" | tail -n 40 | cut -c1-300
  echo; echo "== notes =="; cat "$NOTE"
} > "$OUT/SUMMARY.txt"
echo "$OUT/SUMMARY.txt"
