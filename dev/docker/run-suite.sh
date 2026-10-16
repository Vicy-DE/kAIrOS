#!/usr/bin/env bash
# dev/docker/run-suite.sh — level 0 verification: the whole mock-engine test suite (tests/run-tests.sh)
# inside the kernel-test container. No GPU. NO INTERNET, ever (see "Network" below).
#
#   bash dev/docker/run-suite.sh                 # full suite; rewrites tests/results*.json + docs/PROGRESS.md
#   bash dev/docker/run-suite.sh --only          # KAIROS_TEST_ONLY=1: no PROGRESS.md regeneration (iterating)
#   bash dev/docker/run-suite.sh -- bash         # a shell in the same container + mounts instead of the suite
#
# Environment (all optional; the suite passes without any of them):
#   KAIROS_TEST_IMAGE   image to use. Default: kairos-test:local, else kairos-fix:local (the older name)
#   KAIROS_TEST_NET     none (default) | internal — see "Network"
#   KAIROS_MODELS       host dir with real model files  -> /workspace/models    (default: dev/rig/.env, else dev/rig/work/models)
#   KAIROS_KNOWLEDGE    host dir with *.zim              -> /workspace/knowledge (one test skips without a ZIM)
#   KAIROS_BIN          host dir with kiwix-serve        -> /workspace/bin       (same test; CPU only)
#   KAIROS_WORK         scratch dir for the container's /opt/vibeos and the log. Default: dev/work (gitignored)
#
# Network. The kernel under test is the real kernel: in an environment where a manifest file looks absent
# it can start its own downloader (aria2c) and pull gigabytes of model weights from the internet — one
# unguarded run on 2026-10-10 fetched a 4.7 GB GGUF into the container's /tmp. The suite is offline by
# design (VIBEOS_FORCE_OFFLINE=1, every network-dependent path is mocked), so the container never gets a
# route to the outside:
#   KAIROS_TEST_NET=none      (default) `docker run --network none`: no interface but loopback. The R66
#                             trust-boundary tests need a non-loopback address to play the "remote attacker":
#                             http_remote_needs_token then SKIPS and three more run only their loopback half
#                             (the container's hostname is pinned to 127.0.0.1 so they skip cleanly instead
#                             of failing on name resolution). 291 passed / 0 failed / 8 skipped, 2026-10-14.
#   KAIROS_TEST_NET=internal  a Docker network created with --internal (kairos-test-internal): the container
#                             has an eth0 address but no DNS and no route out (verified: curl exit 6), so the
#                             R66 tests run in full and a download attempt still fails at once (292 / 0 / 7, 2026-10-14).
# The three data mounts reproduce the project's standard layout (docs/FIXES-2026-09-28.md) so that no real
# model counts as missing when you do have them; without them the suite still passes.
#
# The suite boots the kernel on the mock engines (tests/mock-*), forced offline, in the EXPOSED
# configuration (VIBEOS_HOST=0.0.0.0, the appliance's monitoring layout). Expect 8-12 min.
set -euo pipefail
REPO=$(cd "$(dirname "$0")/../.." && pwd)
WORK=${KAIROS_WORK:-$REPO/dev/work}
mkdir -p "$WORK"
# the rig's .env (dev/rig/env.sh) is the one place host paths are configured; it only fills what is unset
[ -f "$REPO/dev/rig/env.sh" ] && . "$REPO/dev/rig/env.sh"

ONLY=0; CMD=()
while [ $# -gt 0 ]; do
  case "$1" in
    --only) ONLY=1; shift ;;
    --) shift; CMD=("$@"); break ;;
    -h|--help) sed -n '2,34p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1 (see --help)" >&2; exit 2 ;;
  esac
done

IMAGE=${KAIROS_TEST_IMAGE:-}
if [ -z "$IMAGE" ]; then
  for c in kairos-test:local kairos-fix:local; do
    docker image inspect "$c" >/dev/null 2>&1 && { IMAGE=$c; break; }
  done
fi
[ -n "$IMAGE" ] || { echo "no kernel-test image — build it first:  bash dev/docker/build.sh" >&2; exit 1; }

# /opt/vibeos inside the container: the kernel's default VIBEOS_DIR, and where tests/run-tests.sh looks for
# an installed kiwix-serve (bin/) and ZIM (knowledge/). A writable scratch dir with two symlinks into the
# mounts below.
OPT="$WORK/optvibeos"
mkdir -p "$OPT"
ln -sfn /workspace/bin "$OPT/bin"
ln -sfn /workspace/knowledge "$OPT/knowledge"

MOUNTS=(-v "$REPO:/workspace/repo" -v "$OPT:/opt/vibeos")
# the standard data mounts, read-only; a missing dir is simply not mounted (the suite needs none of them)
[ -d "${KAIROS_MODELS:-/nonexistent}" ]    && MOUNTS+=(-v "$KAIROS_MODELS:/workspace/models:ro")
[ -d "${KAIROS_KNOWLEDGE:-/nonexistent}" ] && MOUNTS+=(-v "$KAIROS_KNOWLEDGE:/workspace/knowledge:ro")
[ -d "${KAIROS_BIN:-/nonexistent}" ]       && MOUNTS+=(-v "$KAIROS_BIN:/workspace/bin:ro")

case "${KAIROS_TEST_NET:-none}" in
  none)
    NET=(--network none --hostname kairos-test --add-host kairos-test:127.0.0.1) ;;
  internal)
    docker network inspect kairos-test-internal >/dev/null 2>&1 \
      || docker network create --internal kairos-test-internal >/dev/null
    NET=(--network kairos-test-internal --hostname kairos-test) ;;
  *) echo "KAIROS_TEST_NET must be none or internal (never a network with a route out)" >&2; exit 2 ;;
esac

ENV=(-e VIBEOS_HOST=0.0.0.0 -e VIBEOS_FORCE_OFFLINE=1 -e SDL_VIDEODRIVER=dummy -e SDL_AUDIODRIVER=dummy -e HOME=/tmp)
[ "$ONLY" = 1 ] && ENV+=(-e KAIROS_TEST_ONLY=1)
# --rm leaves no container behind; the network choice is never the default bridge
DOCKER=(docker run --rm "${NET[@]}" --user "$(id -u):$(id -g)" "${MOUNTS[@]}" "${ENV[@]}" -w /workspace/repo)

if [ ${#CMD[@]} -gt 0 ]; then
  exec "${DOCKER[@]}" -it "$IMAGE" "${CMD[@]}"
fi

LOG="$WORK/suite-$(date +%Y%m%d-%H%M%S).log"
echo "== suite in $IMAGE, network ${KAIROS_TEST_NET:-none}, mounts: ${MOUNTS[*]} (log: $LOG) =="
T0=$(date +%s)
set +e
"${DOCKER[@]}" "$IMAGE" bash -c 'bash tests/run-tests.sh' 2>&1 | tee "$LOG"
RC=${PIPESTATUS[0]}
set -e
echo "== $(( $(date +%s) - T0 )) s, exit $RC =="
echo "== totals per test file =="
grep -E '^[0-9]+ passed, [0-9]+ failed, [0-9]+ skipped' "$LOG" || true
exit "$RC"
