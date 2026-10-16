#!/usr/bin/env bash
# make-key.sh — the rig's SSH key pair for root in the guest [R66], without sudo.
# Images built after R66 accept root over SSH with a KEY only. One key pair for the rig:
#   dev/rig/testrig/id_kairos      private, gitignored, mounted into the container
#   dev/rig/testrig/id_kairos.pub  staged as iso/secrets/authorized_keys for the next image build
# vm.sh generates the same key on first use when it is missing; this script does it on the host
# and stages the .pub. Idempotent.
set -euo pipefail
. "$(dirname "$0")/env.sh"
KEY="$RIG_DIR/testrig/id_kairos"
if [ -f "$KEY" ]; then
  echo "key present: $KEY"
else
  ssh-keygen -q -t ed25519 -N "" -C "kairos-testrig" -f "$KEY"
  echo "key generated: $KEY"
fi
SECRETS="$REPO_DIR/iso/secrets"
if [ -f "$SECRETS/authorized_keys" ]; then
  if grep -qF "$(cut -d' ' -f2 "$KEY.pub")" "$SECRETS/authorized_keys"; then
    echo "staged already: $SECRETS/authorized_keys"
  else
    echo "NOTE: $SECRETS/authorized_keys exists without this key — append $KEY.pub to it if the next image should trust the rig"
  fi
else
  install -m 600 "$KEY.pub" "$SECRETS/authorized_keys"
  echo "staged: $SECRETS/authorized_keys (gitignored; read by iso/build-iso.sh)"
fi
echo "an image accepts this key only when its .pub was in iso/secrets/authorized_keys at build time"
