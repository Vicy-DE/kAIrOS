#!/usr/bin/env bash
# Install kiwix-serve + download the tier's offline knowledge ZIM (Wikipedia).
# Usage: download-knowledge.sh <manifest.json> [knowledge_dir]
set -euo pipefail

MANIFEST="${1:?usage: download-knowledge.sh <manifest.json>}"
KDIR="${2:-/opt/vibeos/knowledge}"
BIN=/opt/vibeos/bin
mkdir -p "$KDIR" "$BIN"

echo "== kiwix-serve =="
if [ ! -x "$BIN/kiwix-serve" ]; then
    cd /tmp
    curl -fsSLO https://download.kiwix.org/release/kiwix-tools/kiwix-tools_linux-x86_64.tar.gz
    tar xf kiwix-tools_linux-x86_64.tar.gz
    cp kiwix-tools_linux-x86_64*/kiwix-serve "$BIN/"
    chmod +x "$BIN/kiwix-serve"
fi
"$BIN/kiwix-serve" --version | head -1

echo "== resolving latest ZIM =="
read -r DIR_URL PATTERN <<<"$(python3 - "$MANIFEST" <<'PY'
import json, sys
k = json.load(open(sys.argv[1])).get("knowledge")
if not k:
    sys.exit("manifest has no knowledge section")
print(k["dir_url"], k["zim_pattern"])
PY
)"

ZIM=$(curl -fsSL "$DIR_URL" | grep -oE "$PATTERN" | sort -u | tail -1)
[ -n "$ZIM" ] || { echo "could not resolve ZIM matching $PATTERN"; exit 1; }
echo "latest: $ZIM"

if ls "$KDIR"/*.zim >/dev/null 2>&1; then
    echo "a ZIM already exists in $KDIR — skipping download"
else
    aria2c -x8 -s8 --continue=true --summary-interval=30 --console-log-level=warn \
        --file-allocation=none -d "$KDIR" -o "$ZIM" "$DIR_URL$ZIM"
fi
echo "KNOWLEDGE READY: $KDIR/$ZIM"
