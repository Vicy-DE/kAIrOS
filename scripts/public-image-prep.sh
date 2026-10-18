#!/usr/bin/env bash
# scripts/public-image-prep.sh — make a MOUNTED kAIrOS image root shareable: the public profile, every private bit
# removed [R87, docs/PUBLIC-IMAGES-2026-10-16.md].
#
#   bash scripts/public-image-prep.sh ROOT [--esp ESP] [--patterns FILE]     prepare (then verify)
#   bash scripts/public-image-prep.sh ROOT [--esp ESP] [--patterns FILE] --verify   read-only scan, exit 1 on a finding
#   bash scripts/public-image-prep.sh --scrub DISK [--patterns FILE]          the raw-byte scrub (then the raw verify) [R88]
#   bash scripts/public-image-prep.sh --scrub DISK [--patterns FILE] --verify the raw verify alone (read-only)
#
# ROOT = the image's root file system, mounted (never /). ESP = its EFI partition, mounted (default ROOT/boot/efi when
# that directory exists). The build runs it as root against the mounted image; it changes nothing outside ROOT/ESP and
# needs no network. Idempotent: a second run changes nothing. Prepare = remove/reset, then the verify pass; the exit
# status is the verify's (0 = PASS).
#
# What it does (prepare) / checks (verify):
#   network   NetworkManager system-connections (the build's rendered WiFi, iso/secrets/wifi.env) and its state
#             (leases, seen BSSIDs, secret key), wpa_supplicant configs with a network/psk, DHCP leases, Bluetooth pairings
#   ssh       root's and every user's authorized_keys (iso/secrets/authorized_keys), private keys + known_hosts in their
#             ~/.ssh, the SSH host keys (+ the first-boot flags: kairos-public-firstboot.service makes new keys and a new
#             snakeoil certificate on the first boot), sshd DISABLED (users: systemctl enable --now ssh)
#   identity  /etc/machine-id emptied (systemd's first boot), the random seeds, hostname "kairos"
#   logs      every file under /var/log (journals included), the ESP's KAIROS-LOGS, core dumps, /tmp, /var/tmp
#   history   shell / editor histories of root and every user, root's caches, the kiosk browser profile + caches
#   testrig   the remote-UI exposure drop-in, the rig's VNC unit, the build stamps (/opt/vibeos/HOTFIX: build-host paths),
#             the house rule's *.bak* edit backups (gitignored — never public — but copied into the image by the bundle)
#   kAIrOS    /opt/vibeos/cache empty, the /data partition (settings, first-start flag), generated games and the /system
#             partition's doc copy (rewritten by the kernel at every start) emptied, the
#             civitai key store removed, growroot re-armed, /etc/kairos/profile = public
#   Muse      never in a public image: museByStableYogi* removed (also any file of its size with its sha256)
#   licences  /usr/share/doc/kairos/model-licences/ complete (index.json + every text it names); the FreeDict source
#             tarballs next to the GPL dictionaries (scripts/fetch-licence-sources.sh --root ROOT fetches them)
#   strings   private keys and WiFi PSKs anywhere in machine state; /home/<user> paths other than /home/kairos; the
#             operator's own strings (name, email, hostnames, device serials, home paths — one per line) from
#             --patterns FILE, default iso/secrets/private-patterns.txt (gitignored), and KAIROS_PRIVATE_PATTERNS
#             (comma-separated). Hits inside /opt/vibeos/os (the shipped copy of the public git repository) are
#             reported as WARN — the same text is public on GitHub; everywhere else they FAIL.
#   runtime   [R88] /opt/vibeos/os holds only kernel/ shell/ manifests/ scripts/ docs/ (an older image's tests/ goes)
#
# The raw-byte scrub [R88] (--scrub DISK: run it AFTER the prepare, on the UNMOUNTED disk — a flat image, the build's
# /dev/sdX, a partition): the bytes of what the prepare removed stay on the disk. The v7.5-2 public build found the WiFi
# PSK in /swapfile (fallocated = unwritten extents over old swapped memory pages), in free ext4 blocks and in a free ESP
# cluster after this script said PASS. --scrub zeroes, for every ext4 + FAT partition: the swapfile's pages but its
# header (the header rebuilt as mkswap writes it: same UUID, label, size), every non-zero free ext4 block, every
# non-zero free FAT cluster; then the raw verify: no such block, page or cluster left, and the raw bytes of the whole
# disk hold none of the private strings outside regular files (the patterns file, KAIROS_PRIVATE_PATTERNS, the build's
# iso/secrets/wifi.env SSID + PSK and iso/secrets/authorized_keys key bodies — never printed, named only). The work is
# scripts/public-image-scrub.py (next to this script; needs e2fsprogs). Refuses a mounted disk or an unclean ext4.
set -euo pipefail
shopt -s nullglob dotglob lastpipe     # lastpipe: a pipeline's last stage counts findings in THIS shell

HERE=$(cd "$(dirname "$0")" && pwd -P)
MUSE_SIZE=8316720160
MUSE_SHA=db3eceab8049b5ba7dbebeff297145698c5498d618db15b88fd1c7e0bfa4e6c5
FREEDICT_GPL="deu-eng-1.9-fd1 eng-deu-1.9-fd1 deu-ita-0.2.1 ita-deu-0.2.1 eng-fra-0.1.6 fra-eng-0.4.1 spa-eng-0.3.1 spa-deu-0.1"

die() { echo "public-image-prep: $*" >&2; exit 2; }
usage() { sed -n '2,51p' "$0"; exit 2; }

ROOT=""; ESP=""; MODE=prep; PATTERNS=""; FORCE_TREE=0; SCRUB=""
while [ $# -gt 0 ]; do
  case "$1" in
    --verify) MODE=verify; shift ;;
    --scrub) SCRUB=${2:?--scrub needs the unmounted disk / image}; shift 2 ;;
    --esp) ESP=${2:?--esp needs a directory}; shift 2 ;;
    --patterns) PATTERNS=${2:?--patterns needs a file}; shift 2 ;;
    --tree) FORCE_TREE=1; shift ;;          # a test tree without /etc/os-release (tests/test_public_profile.py)
    -h|--help) usage ;;
    -*) die "unknown option $1" ;;
    *) [ -z "$ROOT" ] || die "one ROOT only"; ROOT=$1; shift ;;
  esac
done
# ------------------------------------------------------------------------------------------------ the raw-byte scrub [R88]
if [ -n "$SCRUB" ]; then
  [ -z "$ROOT" ] || die "--scrub takes the unmounted DISK, no ROOT"
  [ -e "$SCRUB" ] || die "$SCRUB does not exist"
  [ -f "$HERE/public-image-scrub.py" ] || die "public-image-scrub.py is missing next to this script"
  command -v python3 >/dev/null || die "python3 is needed for --scrub"
  [ -n "$PATTERNS" ] || { [ -f "$HERE/../iso/secrets/private-patterns.txt" ] && PATTERNS="$HERE/../iso/secrets/private-patterns.txt"; } || true
  NDIR=$(mktemp -d); chmod 700 "$NDIR"; trap 'rm -rf "$NDIR"' EXIT
  : > "$NDIR/needles"; : > "$NDIR/names"; chmod 600 "$NDIR/needles"
  SHORT=0
  needle() {   # NAME VALUE — a needle shorter than 4 bytes would match everywhere: counted, not searched
    [ -n "$2" ] || return 0
    if [ "${#2}" -lt 4 ]; then SHORT=$((SHORT + 1)); return 0; fi
    printf '%s\n' "$2" >> "$NDIR/needles"; printf '%s\n' "$1" >> "$NDIR/names"
  }
  i=0
  if [ -n "$PATTERNS" ] && [ -f "$PATTERNS" ]; then
    while IFS= read -r l || [ -n "$l" ]; do
      case "$l" in ''|'#'*) continue ;; esac
      [ -n "$(tr -d '[:space:]' <<<"$l")" ] || continue
      i=$((i + 1)); needle "pattern $i" "$l"
    done < "$PATTERNS"
  fi
  if [ -n "${KAIROS_PRIVATE_PATTERNS:-}" ]; then
    while IFS= read -r l; do [ -n "$l" ] && { i=$((i + 1)); needle "pattern $i" "$l"; }; done < <(tr ',' '\n' <<<"$KAIROS_PRIVATE_PATTERNS")
  fi
  WIFI_ENV=${KAIROS_WIFI_ENV:-$HERE/../iso/secrets/wifi.env}
  if [ -f "$WIFI_ENV" ]; then
    for k in KAIROS_WIFI_SSID KAIROS_WIFI_PSK; do
      v=$(sed -n "s/^[[:space:]]*$k=//p" "$WIFI_ENV" | tail -n1 | sed -e 's/^"\(.*\)"$/\1/' -e "s/^'\(.*\)'\$/\1/")
      needle "$(tr 'A-Z_' 'a-z-' <<<"${k#KAIROS_}")" "$v"
    done
  fi
  KEYS=${KAIROS_SSH_PUBKEY:-$HERE/../iso/secrets/authorized_keys}
  if [ -f "$KEYS" ]; then
    j=0
    while read -r _t body _c; do
      case "$body" in AAAA*) j=$((j + 1)); needle "ssh key $j" "${body:0:40}" ;; esac
    done < "$KEYS"
  fi
  echo "raw needles: $(wc -l < "$NDIR/needles") (contents never printed)$([ "$SHORT" = 0 ] || echo "; $SHORT shorter than 4 bytes not searched")"
  rc=0
  python3 -I "$HERE/public-image-scrub.py" "$([ "$MODE" = verify ] && echo verify || echo scrub)" "$SCRUB" \
    --needles "$NDIR/needles" --names "$NDIR/names" || rc=$?
  exit "$rc"
fi

[ -n "$ROOT" ] || usage
[ -d "$ROOT" ] || die "$ROOT is not a directory"
ROOT=$(cd "$ROOT" && pwd -P)
[ "$ROOT" != "/" ] || die "refusing to work on / — give the MOUNTED image root"
if [ "$FORCE_TREE" != 1 ] && { [ ! -d "$ROOT/opt/vibeos" ] || [ ! -e "$ROOT/etc/os-release" ]; }; then
  die "$ROOT does not look like a kAIrOS image root (no opt/vibeos or etc/os-release)"
fi
if [ -z "$ESP" ] && [ -d "$ROOT/boot/efi" ]; then ESP="$ROOT/boot/efi"; fi
[ -z "$ESP" ] || ESP=$(cd "$ESP" && pwd -P)
[ -n "$PATTERNS" ] || { [ -f "$HERE/../iso/secrets/private-patterns.txt" ] && PATTERNS="$HERE/../iso/secrets/private-patterns.txt"; } || true

FAILS=0; WARNS=0; CHANGES=0
rel() { local p=$1; if [ -n "$ESP" ] && [ "${p#"$ESP"}" != "$p" ]; then echo "ESP:${p#"$ESP"}"; else echo "${p#"$ROOT"}"; fi; }
fail() { echo "FAIL  $*"; FAILS=$((FAILS + 1)); }
warn() { echo "WARN  $*"; WARNS=$((WARNS + 1)); }
fixed() { echo "fixed $*"; CHANGES=$((CHANGES + 1)); }

# rm_paths DESCRIPTION PATH…: verify -> a FAIL per existing path; prepare -> removed
rm_paths() {
  local desc=$1 p; shift
  for p in "$@"; do
    [ -e "$p" ] || [ -L "$p" ] || continue
    if [ "$MODE" = verify ]; then fail "$desc: $(rel "$p")"; else rm -rf -- "$p"; fixed "$desc: $(rel "$p")"; fi
  done
}

# put_file PATH MODE CONTENT DESCRIPTION: the file must hold exactly CONTENT
put_file() {
  local p=$1 mode=$2 content=$3 desc=$4
  if [ -f "$p" ] && [ ! -L "$p" ] && [ "$(cat "$p")" = "$content" ]; then return 0; fi
  if [ "$MODE" = verify ]; then fail "$desc: $(rel "$p")"; return 0; fi
  mkdir -p "$(dirname "$p")"; rm -f -- "$p"
  printf '%s\n' "$content" > "$p"; chmod "$mode" "$p"
  fixed "$desc: $(rel "$p")"
}

USERS=("$ROOT/root" "$ROOT"/home/*)

# ------------------------------------------------------------------------------------------------ network
rm_paths "WiFi / network profile" "$ROOT"/etc/NetworkManager/system-connections/*
rm_paths "NetworkManager state (leases, seen BSSIDs, secret key)" "$ROOT"/var/lib/NetworkManager/*
for f in "$ROOT"/etc/wpa_supplicant/*.conf; do
  grep -qE '(^|[[:space:]])(psk|network)[[:space:]]*=' "$f" 2>/dev/null && rm_paths "wpa_supplicant network config" "$f"
done
rm_paths "DHCP leases" "$ROOT"/var/lib/dhcp/*.leases "$ROOT"/var/lib/dhcp/dhclient*.lease* "$ROOT"/var/lib/NetworkManager/*.lease
rm_paths "Bluetooth pairing" "$ROOT"/var/lib/bluetooth/*

# ------------------------------------------------------------------------------------------------ ssh
for h in "${USERS[@]}"; do
  rm_paths "authorized_keys" "$h"/.ssh/authorized_keys*
  rm_paths "SSH private key / known_hosts" "$h"/.ssh/id_* "$h"/.ssh/known_hosts*
done
rm_paths "SSH host key" "$ROOT"/etc/ssh/ssh_host_*
rm_paths "first-boot done flag" "$ROOT"/var/lib/kairos-firstboot-done "$ROOT"/var/lib/kairos-public-firstboot-done
rm_paths "Debian snakeoil key/certificate (a new one is made at the first boot)" \
  "$ROOT"/etc/ssl/private/ssl-cert-snakeoil.key "$ROOT"/etc/ssl/certs/ssl-cert-snakeoil.pem
FB_UNIT='[Unit]
Description=kAIrOS public image: first boot (new SSH host keys and snakeoil certificate)
Before=ssh.service
ConditionPathExists=!/var/lib/kairos-public-firstboot-done
[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/bin/sh -c '"'"'ssh-keygen -A; if [ -x /usr/sbin/make-ssl-cert ]; then /usr/sbin/make-ssl-cert generate-default-snakeoil --force-overwrite || true; fi; touch /var/lib/kairos-public-firstboot-done'"'"'
[Install]
WantedBy=multi-user.target'
put_file "$ROOT/etc/systemd/system/kairos-public-firstboot.service" 0644 "$FB_UNIT" "first-boot unit (new host keys)"
WANT="$ROOT/etc/systemd/system/multi-user.target.wants/kairos-public-firstboot.service"
if [ "$(readlink "$WANT" 2>/dev/null)" != /etc/systemd/system/kairos-public-firstboot.service ]; then
  if [ "$MODE" = verify ]; then fail "first-boot unit not enabled: $(rel "$WANT")"
  else mkdir -p "$(dirname "$WANT")"; rm -f -- "$WANT"; ln -s /etc/systemd/system/kairos-public-firstboot.service "$WANT"
       fixed "first-boot unit enabled: $(rel "$WANT")"; fi
fi
rm_paths "sshd enabled at boot (public images: off — systemctl enable --now ssh)" \
  "$ROOT"/etc/systemd/system/multi-user.target.wants/ssh.service "$ROOT"/etc/systemd/system/sshd.service \
  "$ROOT"/etc/systemd/system/sockets.target.wants/ssh.socket

# ------------------------------------------------------------------------------------------------ identity
if [ -s "$ROOT/etc/machine-id" ] || [ ! -e "$ROOT/etc/machine-id" ]; then
  if [ "$MODE" = verify ]; then fail "machine-id is set (or missing): /etc/machine-id"
  else mkdir -p "$ROOT/etc"; : > "$ROOT/etc/machine-id"; chmod 0444 "$ROOT/etc/machine-id"; fixed "machine-id emptied: /etc/machine-id"; fi
fi
[ -L "$ROOT/var/lib/dbus/machine-id" ] || rm_paths "D-Bus machine-id copy" "$ROOT"/var/lib/dbus/machine-id
rm_paths "random seed" "$ROOT"/var/lib/systemd/random-seed ${ESP:+"$ESP"/loader/random-seed}
put_file "$ROOT/etc/hostname" 0644 "kairos" "hostname"
if [ -f "$ROOT/etc/hosts" ] && grep -qE '^127\.0\.1\.1[[:space:]]' "$ROOT/etc/hosts" \
   && ! grep -qE '^127\.0\.1\.1[[:space:]]+kairos[[:space:]]*$' "$ROOT/etc/hosts"; then
  if [ "$MODE" = verify ]; then fail "/etc/hosts names another host on 127.0.1.1"
  else sed -i -E 's/^127\.0\.1\.1[[:space:]].*$/127.0.1.1\tkairos/' "$ROOT/etc/hosts"; fixed "hostname in /etc/hosts"; fi
fi

# ------------------------------------------------------------------------------------------------ logs
if [ -d "$ROOT/var/log" ]; then
  mapfile -t LOGS < <(find "$ROOT/var/log" -xdev -type f 2>/dev/null)
  if [ "${#LOGS[@]}" -gt 0 ]; then
    if [ "$MODE" = verify ]; then fail "${#LOGS[@]} log file(s) under /var/log (e.g. $(rel "${LOGS[0]}"))"
    else find "$ROOT/var/log" -xdev -type f -delete; fixed "${#LOGS[@]} log file(s) under /var/log"; fi
  fi
  rm_paths "journal directory" "$ROOT"/var/log/journal/*
fi
[ -z "$ESP" ] || rm_paths "boot logs on the EFI partition" "$ESP"/KAIROS-LOGS/*
rm_paths "core dump" "$ROOT"/var/lib/systemd/coredump/*
rm_paths "temporary file" "$ROOT"/tmp/* "$ROOT"/var/tmp/*

# ------------------------------------------------------------------------------------------------ histories, caches
for h in "${USERS[@]}"; do
  rm_paths "shell / editor history" "$h"/.bash_history "$h"/.zsh_history "$h"/.python_history "$h"/.lesshst \
    "$h"/.viminfo "$h"/.wget-hsts "$h"/.sqlite_history "$h"/.node_repl_history "$h"/.mysql_history "$h"/.psql_history \
    "$h"/.local/share/recently-used.xbel "$h"/.xsession-errors*
  rm_paths "cache / browser profile" "$h"/.cache "$h"/.config/chromium "$h"/.local/state "$h"/.local/share/pki \
    "$h"/.Xauthority "$h"/.vnc "$h"/.nv "$h"/.local/share/xorg/*.log*
done

# ------------------------------------------------------------------------------------------------ testrig + build leftovers
rm_paths "remote-UI exposure (testrig drop-in)" "$ROOT"/etc/systemd/system/kairos.service.d/expose-ui.conf
rm_paths "testrig VNC unit" "$ROOT"/etc/systemd/system/kairos-x11vnc.service "$ROOT"/etc/systemd/system/*.wants/kairos-x11vnc.service
rm_paths "build stamp with build-host details" "$ROOT"/opt/vibeos/HOTFIX "$ROOT"/opt/vibeos/hotfix-backup
# the house rule's edit backups (<file>.bak-<date><tag>): gitignored, so NOT part of the public repository — the bundle
# copied 139 of them (5.8 MB) into v7.5-2's /opt/vibeos/os
mapfile -d '' BAKS < <(find "$ROOT/opt/vibeos" "$ROOT/usr/local" "$ROOT/etc/systemd/system" "$ROOT/etc/kairos" "$ROOT/home" \
  "$ROOT/usr/share/doc/kairos" -xdev \( -path "$ROOT/opt/vibeos/models" -o -path "$ROOT/opt/vibeos/knowledge" \) -prune \
  -o -type f \( -name '*.bak' -o -name '*.bak-*' -o -name '*.bak[0-9]*' \) -print0 2>/dev/null)
if [ "${#BAKS[@]}" -gt 0 ]; then
  if [ "$MODE" = verify ]; then fail "${#BAKS[@]} edit backup(s) (*.bak*, never in the public repository), e.g. $(rel "${BAKS[0]}")"
  else rm -f -- "${BAKS[@]}"; fixed "${#BAKS[@]} edit backup(s) (*.bak*), e.g. $(rel "${BAKS[0]}")"; fi
fi

# [R88] the shipped OS code is exactly kernel/ shell/ manifests/ scripts/ docs/ (iso/build-iso.sh, sync-guest.sh): a tests/
# or bench/ an older image carries (v7.5-2: 18 test files from June 2026, one not even in git) is dev material
if [ -d "$ROOT/opt/vibeos/os" ]; then
  for e in "$ROOT"/opt/vibeos/os/*; do
    case "$(basename "$e")" in kernel|shell|manifests|scripts|docs) ;; *) rm_paths "not part of the runtime (dev / test files)" "$e" ;; esac
  done
fi

# ------------------------------------------------------------------------------------------------ kAIrOS state
rm_paths "kAIrOS cache (apps, pictures, settings, downloads state)" "$ROOT"/opt/vibeos/cache/*
rm_paths "the /data partition (user files, settings, first-start flag)" "$ROOT"/opt/vibeos/fs/data/*
rm_paths "generated game" "$ROOT"/opt/vibeos/games/*
rm_paths "the /system partition's copy of the OS docs (the kernel rewrites it at every start)" "$ROOT"/opt/vibeos/fs/system/*
rm_paths "kAIrOS secrets (a user's civitai API key)" "$ROOT"/var/lib/kairos/secrets
rm_paths "growroot done flag (the first boot grows the root to the device)" "$ROOT"/var/lib/kairos-resized
put_file "$ROOT/etc/kairos/profile" 0644 "public" "the public profile"
if [ ! -f "$ROOT/opt/vibeos/PUBLIC-IMAGE" ]; then
  if [ "$MODE" = verify ]; then warn "no /opt/vibeos/PUBLIC-IMAGE stamp"
  elif [ -d "$ROOT/opt/vibeos" ]; then
    printf 'kAIrOS public image — profile public, prepared %s by scripts/public-image-prep.sh (docs/PUBLIC-IMAGES-2026-10-16.md)\n' \
      "$(date -u +%Y-%m-%d)" > "$ROOT/opt/vibeos/PUBLIC-IMAGE"
    fixed "stamp: /opt/vibeos/PUBLIC-IMAGE"
  fi
fi

# ------------------------------------------------------------------------------------------------ Muse: never public
rm_paths "Muse — no redistribution grant, never in a public image" \
  "$ROOT"/opt/vibeos/models/image/museByStableYogi* "$ROOT"/opt/vibeos/models/image-candidates/museByStableYogi*
if [ -d "$ROOT/opt" ]; then
  while IFS= read -r -d '' f; do
    if [ "$(sha256sum "$f" | cut -d' ' -f1)" = "$MUSE_SHA" ]; then rm_paths "Muse under another name" "$f"; fi
  done < <(find "$ROOT/opt" "$ROOT/home" "$ROOT/root" -xdev -type f -size "${MUSE_SIZE}c" -print0 2>/dev/null)
fi

if [ "$MODE" = prep ]; then          # what follows cannot be fixed here — the verify pass below reports it
  echo "prepare: $CHANGES change(s) under $ROOT${ESP:+ and $ESP}"
  echo "next: unmount, then the raw-byte scrub: $0 --scrub <the disk> [--patterns FILE] (what was removed stays in the raw bytes until then)"
  echo "== verify =="
  exec bash "$0" "$ROOT" ${ESP:+--esp "$ESP"} ${PATTERNS:+--patterns "$PATTERNS"} $([ "$FORCE_TREE" = 1 ] && echo --tree) --verify
fi

# ------------------------------------------------------------------------------------------------ licences
LIC="$ROOT/usr/share/doc/kairos/model-licences"
if [ ! -f "$LIC/index.json" ]; then
  fail "licence files missing: /usr/share/doc/kairos/model-licences/index.json (the image build copies iso/config/includes.chroot)"
elif command -v python3 >/dev/null; then
  while IFS= read -r miss; do fail "licence text missing: ${miss#"$ROOT"}"; done < <(python3 -I - "$LIC" <<'PY'
import json, sys
from pathlib import Path
d = Path(sys.argv[1])
for m in json.loads((d / "index.json").read_text(encoding="utf-8")).get("models", []):
    for t in m.get("texts") or []:
        if not (d / m["id"] / t).is_file():
            print(d / m["id"] / t)
PY
)
fi
GPLDICT=0
for n in $FREEDICT_GPL; do [ -e "$ROOT/opt/vibeos/models/dict/freedict-$n.dictd.tar.xz" ] && GPLDICT=1; done
if [ "$GPLDICT" = 1 ]; then
  for n in $FREEDICT_GPL; do
    [ -s "$ROOT/usr/share/doc/kairos/sources/freedict/freedict-$n.src.tar.xz" ] || \
      fail "FreeDict source missing next to the GPL dictionaries: /usr/share/doc/kairos/sources/freedict/freedict-$n.src.tar.xz (scripts/fetch-licence-sources.sh --root ROOT)"
  done
fi

# ------------------------------------------------------------------------------------------------ private strings
SCAN=()
for d in etc root home var/lib usr/local opt/vibeos; do [ -d "$ROOT/$d" ] && SCAN+=("$ROOT/$d"); done
[ -n "$ESP" ] && [ -d "$ESP" ] && SCAN+=("$ESP")
EXCL=(--exclude-dir=models --exclude-dir=knowledge --exclude-dir=lib --exclude-dir=bin --exclude-dir=dpkg --exclude-dir=apt)
in_repo() { case "$1" in "$ROOT/opt/vibeos/os/"*|"$ROOT/opt/vibeos/fs/system/"*) return 0 ;; *) return 1 ;; esac; }
report_hits() {   # DESCRIPTION, file list on stdin
  local desc=$1 f
  while IFS= read -r f; do
    [ -n "$f" ] || continue
    if in_repo "$f"; then warn "$desc (shipped repo text, public on GitHub): $(rel "$f")"; else fail "$desc: $(rel "$f")"; fi
  done
}
if [ "${#SCAN[@]}" -gt 0 ]; then
  grep -rIlE "${EXCL[@]}" -e '-----BEGIN [A-Z ]*PRIVATE KEY-----' "${SCAN[@]}" 2>/dev/null | report_hits "private key" || true
  grep -rIlE "${EXCL[@]}" -e '^[[:space:]]*psk[[:space:]]*=' "${SCAN[@]}" 2>/dev/null | report_hits "WiFi PSK" || true
  # outside comments (a '#' / ';' earlier on the line): Debian's configs carry examples like "/home/user/file.ext"
  grep -rIoE "${EXCL[@]}" -e '^[^#;]*/home/[a-z_][a-z0-9_-]*' "${SCAN[@]}" 2>/dev/null | grep -vE '/home/kairos$' | cut -d: -f1 | sort -u \
    | report_hits "a home path other than /home/kairos" || true
  PATS=$(mktemp); trap 'rm -f "$PATS"' EXIT
  { [ -n "$PATTERNS" ] && [ -f "$PATTERNS" ] && grep -vE '^[[:space:]]*(#|$)' "$PATTERNS"; \
    [ -n "${KAIROS_PRIVATE_PATTERNS:-}" ] && tr ',' '\n' <<<"$KAIROS_PRIVATE_PATTERNS" | grep -v '^[[:space:]]*$'; } > "$PATS" || true
  if [ -s "$PATS" ]; then
    grep -rIlF "${EXCL[@]}" -f "$PATS" "${SCAN[@]}" 2>/dev/null | report_hits "operator's private string" || true
  else
    warn "no private-patterns file (iso/secrets/private-patterns.txt or --patterns): the owner's own strings were not searched"
  fi
fi

if [ "$FAILS" -gt 0 ]; then echo "VERIFY: FAIL — $FAILS finding(s), $WARNS warning(s)"; exit 1; fi
echo "VERIFY: PASS — 0 findings, $WARNS warning(s)"
