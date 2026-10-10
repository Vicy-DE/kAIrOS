#!/usr/bin/env bash
# SSH/rsync all kAIrOS resources to another device, to continue work there.
#   transfer-to-device.sh user@host[:/dest]  [--models] [--knowledge] [--image] [--iso] [--engines] [--all]
# Default (no flags): sends only the repo bundle + handoff (small, essential).
# Resumable (rsync --partial). Requires ssh access to the target.
set -euo pipefail
TARGET="${1:?usage: transfer-to-device.sh user@host[:/dest] [--models --knowledge --image --iso --engines --all]}"
shift || true
case "$TARGET" in *:*) HOST="${TARGET%%:*}"; DEST="${TARGET#*:}";; *) HOST="$TARGET"; DEST="~/kairos";; esac

WANT_MODELS=0 WANT_KNOW=0 WANT_IMAGE=0 WANT_ISO=0 WANT_ENG=0
for a in "$@"; do case "$a" in
  --models) WANT_MODELS=1;; --knowledge) WANT_KNOW=1;; --image) WANT_IMAGE=1;;
  --iso) WANT_ISO=1;; --engines) WANT_ENG=1;;
  --all) WANT_MODELS=1; WANT_KNOW=1; WANT_IMAGE=1; WANT_ISO=1; WANT_ENG=1;;
esac; done

BUNDLE=/mnt/b/kairos-transfer
[ -f "$BUNDLE/kairos-repo.tar.gz" ] || { echo "Run scripts/bundle-resources.sh first."; exit 1; }
RS=(rsync -avhP --partial -e ssh)

echo ">> ensuring dest on $HOST: $DEST"
ssh "$HOST" "mkdir -p $DEST"

echo ">> [1] repo bundle + handoff + manifest (essential)"
"${RS[@]}" "$BUNDLE/kairos-repo.tar.gz" "$BUNDLE/SESSION-HANDOFF.md" "$BUNDLE/RESOURCES.txt" "$HOST:$DEST/"

send() { echo ">> $3"; "${RS[@]}" "$1" "$HOST:$DEST/$2"; }
[ $WANT_MODELS -eq 1 ] && send /opt/vibeos/models    models/    "[2] models (~20 GB)"
[ $WANT_KNOW   -eq 1 ] && send /opt/vibeos/knowledge knowledge/ "[3] knowledge ZIM"
[ $WANT_ENG    -eq 1 ] && send /opt/vibeos/bin       bin/       "[4] engines (note: GPU-arch specific)"
[ $WANT_IMAGE  -eq 1 ] && send /mnt/b/kairos-hwtest-4070.img.gz . "[5] disk image (22 GB)"
[ $WANT_ISO    -eq 1 ] && send /srv/kairos-out/kairos-hwtest-4070.iso . "[6] live ISO (2.1 GB)"

cat <<EOF

>> transfer complete -> $HOST:$DEST
On the new device:
  tar xzf kairos-repo.tar.gz && cd vibeOS-local && less docs/SESSION-HANDOFF.md
  # models/knowledge (if sent) -> place at /opt/vibeos/{models,knowledge}; rebuild engines for that GPU.
EOF
