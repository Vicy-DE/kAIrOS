#!/usr/bin/env bash
# dev/docker/build.sh — build the kernel-test container image (no GPU, ~1.2 GB).
#
#   bash dev/docker/build.sh              # target kernel-test -> kairos-test:local (+ alias kairos-fix:local)
#   bash dev/docker/build.sh chromium     # target chromium    -> kairos-test-chromium:local (+ alias kairos-fix-chromium:local)
#   bash dev/docker/build.sh x11          # target x11         -> kairos-test-x11:local (+ alias kairos-fix-x11:local) [R82]
#
# The aliases are the names the project's older documents and reports use for the same images.
# Needs network (apt, ~350 MB of Debian packages); on a slow link allow 10-30 minutes.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
TARGET=${1:-kernel-test}
case "$TARGET" in
  kernel-test) TAGS=(-t kairos-test:local -t kairos-fix:local) ;;
  chromium)    TAGS=(-t kairos-test-chromium:local -t kairos-fix-chromium:local) ;;
  x11)         TAGS=(-t kairos-test-x11:local -t kairos-fix-x11:local) ;;
  base|feattest) TAGS=(-t "kairos-test-$TARGET:local") ;;
  *) echo "usage: $0 [kernel-test|chromium|x11|base|feattest]" >&2; exit 2 ;;
esac
exec docker build -f "$HERE/Dockerfile.kernel-test" --target "$TARGET" "${TAGS[@]}" "$HERE"
