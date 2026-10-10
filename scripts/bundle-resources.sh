#!/usr/bin/env bash
# Package kAIrOS resources for transfer to another device.
#  - tars the repo (code/scripts/docs/manifests/tests/iso) -> small bundle
#  - writes RESOURCES.txt: every artifact, its location, size, and sha256 of key files
# Output goes to /mnt/b (roomy) by default. Run as root in WSL.
set -euo pipefail
REPO=$(cd "$(dirname "$0")/.." && pwd)
OUT=${1:-/mnt/b/kairos-transfer}
mkdir -p "$OUT"

echo "[bundle] tarring repo -> $OUT/kairos-repo.tar.gz"
tar -C "$(dirname "$REPO")" \
    --exclude='*/dist/*' --exclude='*/.git/*' --exclude='*/__pycache__/*' \
    -czf "$OUT/kairos-repo.tar.gz" "$(basename "$REPO")"

echo "[bundle] writing manifest -> $OUT/RESOURCES.txt"
{
  echo "kAIrOS resource bundle — generated $(cat /opt/vibeos/img-heartbeat 2>/dev/null || echo '?') (epoch)"
  echo "Build host: WSL Debian 13. See docs/SESSION-HANDOFF.md to resume."
  echo
  echo "== TRANSFERABLE ARTIFACTS =="
  printf "%-46s %-10s %s\n" "PATH" "SIZE" "NOTE"
  list() { local p="$1" n="$2"; [ -e "$p" ] && printf "%-46s %-10s %s\n" "$p" "$(du -sh "$p" 2>/dev/null|cut -f1)" "$n"; }
  list "$OUT/kairos-repo.tar.gz"        "repo: code/scripts/docs/manifests/tests/iso (ESSENTIAL, small)"
  list "/opt/vibeos/models"             "8GB-tier models (reusable across machines)"
  list "/opt/vibeos/knowledge"          "offline Simple-Wikipedia ZIM"
  list "/opt/vibeos/bin"                "compiled engines (GPU-arch specific: sm_89;120)"
  list "/mnt/b/kairos-hwtest-4070.img.gz" "flashable disk image (systemd-boot, autoresize, offline)"
  list "/srv/kairos-out/kairos-hwtest-4070.iso" "live ISO"
  echo
  echo "== CHECKSUMS (key files) =="
  for f in "$OUT/kairos-repo.tar.gz" /mnt/b/kairos-hwtest-4070.img.gz; do
    [ -f "$f" ] && sha256sum "$f"
  done
} > "$OUT/RESOURCES.txt"

cp "$REPO/docs/SESSION-HANDOFF.md" "$OUT/SESSION-HANDOFF.md" 2>/dev/null || true
cat "$OUT/RESOURCES.txt"
echo
echo "[bundle] done -> $OUT"
echo "Next: scripts/transfer-to-device.sh user@host:/dest/path  [--models --image --iso --all]"
