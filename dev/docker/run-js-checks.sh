#!/usr/bin/env bash
# dev/docker/run-js-checks.sh — the shell's JavaScript checks, in the node:24-alpine image (no node needed
# on the host, none in the kernel-test image either):
#   1. node --check on shell/shell.js, shell/browser.js, shell/games.js, shell/pictures.js and shell/bench/host.js (syntax)
#   2. the app bridge: the BRIDGE_SRC template literal in shell.js is parsed with new Function — a syntax
#      error there breaks EVERY generated app, and node --check on the file does not see inside the literal
#   3. tests/bridge-test.js (the bridge's unit tests: storage shims, vibe.* aliases, unknown-member guard, …)
set -euo pipefail
REPO=$(cd "$(dirname "$0")/../.." && pwd)
IMAGE=${KAIROS_NODE_IMAGE:-node:24-alpine}
run(){ docker run --rm --network none -v "$REPO:/w:ro" "$IMAGE" "$@"; }
echo "== node --check =="
run node --check /w/shell/shell.js && echo "shell/shell.js OK"
run node --check /w/shell/bench/host.js && echo "shell/bench/host.js OK"
run node --check /w/shell/browser.js && echo "shell/browser.js OK"          # [R83] the built-in browser
run node --check /w/shell/games.js && echo "shell/games.js OK"              # [R82] games in windows
run node --check /w/shell/pictures.js && echo "shell/pictures.js OK"        # [R85] ✨ improve a picture
echo "== BRIDGE_SRC parses =="
run node -e '
const fs = require("fs");
const js = fs.readFileSync("/w/shell/shell.js", "utf8");
const m = js.match(/const BRIDGE_SRC = `([\s\S]*?)`;\n/);
if (!m) throw new Error("BRIDGE_SRC literal not found");
const src = m[1].replace(/<\\\/script>/g, "</script>");
const body = src.match(/<script>([\s\S]*?)<\/script>/);
if (!body) throw new Error("no <script> in BRIDGE_SRC");
new Function(body[1]);
console.log("BRIDGE_SRC OK (" + src.length + " chars)");'
echo "== tests/bridge-test.js =="
run node /w/tests/bridge-test.js
