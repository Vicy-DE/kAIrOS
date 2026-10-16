#!/usr/bin/env bash
# scripts/fetch-licence-sources.sh — the source tarballs the GPL / AGPL dictionaries' licences require [R87]
# (docs/PUBLIC-IMAGES-2026-10-16.md; LICENCES-PUBLIC §4.4: a FreeDict .dictd archive is a build product of FreeDict's
# TEI source — shipping it means shipping the Corresponding Source too).
#
#   bash scripts/fetch-licence-sources.sh --root ROOT        fetch into ROOT/usr/share/doc/kairos/sources/freedict/
#   bash scripts/fetch-licence-sources.sh --dest DIR         fetch into DIR
#   … --check                                                only verify what is there (exit 1 when incomplete)
#   … --base-url URL                                         another mirror (default https://download.freedict.org/dictionaries)
#   … --list FILE                                            another list ("pair version bytes sha512" per line; the tests)
#
# 8 tarballs, 36 004 656 B in all; each is sha512-checked against FreeDict's own database (freedict-database.json,
# read 2026-10-16) before it is moved into place; a file already there with the right sha512 is kept (idempotent).
# Run by the image build (network needed), never by the OS. Writes SHA512SUMS + README.txt next to the tarballs.
set -euo pipefail

BASE=https://download.freedict.org/dictionaries
DEST=""; ROOT=""; CHECK=0; LIST=""
while [ $# -gt 0 ]; do
  case "$1" in
    --root) ROOT=${2:?}; shift 2 ;;
    --dest) DEST=${2:?}; shift 2 ;;
    --check) CHECK=1; shift ;;
    --base-url) BASE=${2:?}; shift 2 ;;
    --list) LIST=${2:?}; shift 2 ;;
    -h|--help) sed -n '2,17p' "$0"; exit 0 ;;
    *) echo "unknown argument $1 (see --help)" >&2; exit 2 ;;
  esac
done
if [ -z "$DEST" ]; then
  [ -n "$ROOT" ] || { echo "give --root ROOT (the mounted image) or --dest DIR" >&2; exit 2; }
  [ "$(cd "$ROOT" && pwd -P)" != / ] || { echo "refusing --root / — give the MOUNTED image root" >&2; exit 2; }
  DEST="$ROOT/usr/share/doc/kairos/sources/freedict"
fi

# pair version bytes sha512 — FreeDict's database, platform "src"
SOURCES='
deu-eng 1.9-fd1 18183480 31c7143ae53074a5f357b80f4a6ccce8fa26f77a237476edca1353d5783293a0a8aef3a47f087de3e0c38f5aa7064196b7347886e1049116dc2964c6532397bc
eng-deu 1.9-fd1 16742600 e44d3a3697d2bfe93cadef70284355bdd9d41cae818722d26e9eda8e4268ce63c37cee4d623c4be8028467fc212d49b388fdb395c0a253d285aed1bd424f80a9
deu-ita 0.2.1 101700 210d8e2b4ed8bc6d3de10336d2fad5431fc39dd63e0f857a75eb759e69d5f81a5f5f97db555a56818b4ec5ae2259821723ecaea5850abc870cb02b87c2330baf
ita-deu 0.2.1 100400 f5139162a94b28624194ce7b908fe30e0c193f85392736e6eb120f9032a0b10228275ee8d1030f0c5c90a517d903a440871b1648ba4316a327640b9f06763cb5
eng-fra 0.1.6 212948 7b7decb42f36617958303b8640616acc06e82aecd955e78949b67e083a7dbbdc4c668fee9cfcda20f219897767f64daabc942b3c3c01ed1f5e8d6ee56262e8a7
fra-eng 0.4.1 214980 bf234cfc9aedea3e322ee9bc518f6b1addfcc4a96c6b9c42d2bd1de765ae2011b057888134c9c4361422d4632e289455c92c6c429fcfab566a95cdc49ac2a2fb
spa-eng 0.3.1 121624 bc8dc9c09b0d4584d1cbaa9c3e75183431ad5cd9a42b31685f0392413a6755504909c35c5ccae3bb8eb66e3b8623df93982c8d5a331dcf516c08bfa5028ce67c
spa-deu 0.1 326924 8e81e22d4dcec46c0aa21aed20a9ed796e18647a31a6e7d5e9b172ff13c5b295fbf3bf04b8ebcd1cb47659c3a6c5909a9f011a81d1f2f01079732c53516d4a07
'
[ -z "$LIST" ] || SOURCES=$(grep -vE '^[[:space:]]*(#|$)' "$LIST")

mkdir -p "$DEST"
BAD=0; GOT=0; KEPT=0
SUMS=""
while read -r pair ver size sha; do
  [ -n "$pair" ] || continue
  name="freedict-$pair-$ver.src.tar.xz"
  f="$DEST/$name"
  SUMS+="$sha  $name"$'\n'
  if [ -f "$f" ] && [ "$(stat -c %s "$f")" = "$size" ] && [ "$(sha512sum "$f" | cut -d' ' -f1)" = "$sha" ]; then
    KEPT=$((KEPT + 1)); continue
  fi
  if [ "$CHECK" = 1 ]; then echo "MISSING or wrong: $name"; BAD=$((BAD + 1)); continue; fi
  tmp="$f.part.$$"   # per run: two runs never write the same temp file
  if ! curl -fsSL --retry 3 --connect-timeout 20 -o "$tmp" "$BASE/$pair/$ver/$name"; then
    echo "FAILED: $name (download)"; rm -f "$tmp"; BAD=$((BAD + 1)); continue
  fi
  if [ "$(stat -c %s "$tmp")" != "$size" ] || [ "$(sha512sum "$tmp" | cut -d' ' -f1)" != "$sha" ]; then
    echo "FAILED: $name (size or sha512 differ from FreeDict's database)"; rm -f "$tmp"; BAD=$((BAD + 1)); continue
  fi
  mv -f "$tmp" "$f"; chmod 0644 "$f"; GOT=$((GOT + 1)); echo "ok: $name ($size B, sha512 verified)"
done <<<"$SOURCES"

if [ "$CHECK" != 1 ]; then
  printf '%s' "$SUMS" > "$DEST/SHA512SUMS"
  cat > "$DEST/README.txt" <<'EOF'
FreeDict source tarballs — the Corresponding Source of the GPL / AGPL dictionaries in /opt/vibeos/models/dict/
(see /usr/share/doc/kairos/model-licences/dictionaries/ for the licences, ATTRIBUTION.txt and SOURCE.txt).
Downloaded from https://download.freedict.org/dictionaries/<pair>/<version>/ and checked against FreeDict's
sha512 (SHA512SUMS). deu-eng / eng-deu are built from the Ding data (BEOLINGUS, in /opt/vibeos/models/dict/)
with ding2tei-haskell: https://github.com/freedict/tools/tree/master/importers/ding2tei
EOF
fi
echo "freedict sources in $DEST: $GOT fetched, $KEPT already there, $BAD missing or bad"
[ "$BAD" = 0 ]
