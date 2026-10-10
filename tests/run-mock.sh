#!/usr/bin/env bash
# Boot the vibeOS kernel against mock engines (no models / GPU needed).
set -euo pipefail
REPO=$(cd "$(dirname "$0")/.." && pwd)
MOCK=/tmp/vibeos-mock

mkdir -p $MOCK/bin $MOCK/cache $MOCK/models $MOCK/games
chmod +x "$REPO/tests/mock-llama-server" "$REPO/tests/mock-sd-cli"
ln -sf "$REPO/tests/mock-llama-server" $MOCK/bin/llama-server
ln -sf "$REPO/tests/mock-sd-cli" $MOCK/bin/sd-cli
# real knowledge base if it's installed — kiwix is cheap, no GPU involved
[ -x /opt/vibeos/bin/kiwix-serve ] && ln -sf /opt/vibeos/bin/kiwix-serve $MOCK/bin/kiwix-serve
[ -d /opt/vibeos/knowledge ] && ln -sfn /opt/vibeos/knowledge $MOCK/knowledge

export VIBEOS_DIR=$MOCK
export VIBEOS_MANIFEST=$REPO/manifests/tier-16gb.json
export VIBEOS_SHELL=$REPO/shell
echo "[mock] vibeOS on http://localhost:1337 (mock engines)"
exec python3 "$REPO/kernel/server.py"
