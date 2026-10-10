#!/bin/sh
# Build the kAIrOS boot screen painter [R73]: one static binary, no runtime deps, so the
# initramfs hook can copy_exec it as-is. Run at image build time (hooks/0200) and by the
# test suite. Usage: build.sh [output]   (default: scripts/splash/kairos-splash next to the source)
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
OUT=${1:-$HERE/kairos-splash}
CC=${CC:-cc}
command -v "$CC" >/dev/null 2>&1 || { echo "build.sh: no C compiler ($CC)" >&2; exit 1; }
[ -s "$HERE/logo_data.h" ] || { echo "build.sh: $HERE/logo_data.h missing — run render-assets.py" >&2; exit 1; }
# -static: no libc at run time (the initramfs has none for it); -lm for exp/hypot; size ~1 MB
"$CC" -std=gnu11 -O2 -Wall -Wextra -Wno-unused-parameter -static -s -o "$OUT.tmp" "$HERE/kairos-splash.c" -lm
mv -f "$OUT.tmp" "$OUT"
chmod 755 "$OUT"
echo "built $OUT ($(wc -c < "$OUT") bytes, static)"
