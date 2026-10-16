#!/usr/bin/env bash
# The kAIrOS desktop in a browser window on this machine, for clicking through the OS without a stick.
#   bash dev/rig/desk-window.sh          start (or restart) the kernel on the GPU + a Firefox window
#   bash dev/rig/desk-window.sh ui       start a UI-only kernel (no GPU, no models) + window
#   bash dev/rig/desk-window.sh server   headless: the UI-only kernel, NO window — prints the URL (= ui --no-window;
#                                        for a server or a box without Firefox/a display)
#   bash dev/rig/desk-window.sh start --no-window   the GPU kernel without the window (also: ui --no-window)
#   bash dev/rig/desk-window.sh window   only reopen the window
#   bash dev/rig/desk-window.sh stop     stop the kernel (and free the GPU lock)
# The kernel binds 127.0.0.1 only (the root API is not for the network, R66): from another machine use an SSH
# tunnel, `ssh -L 1380:127.0.0.1:1380 <this host>`, then open http://127.0.0.1:1380/ there.
# start: a built CUDA engine set (KAIROS_ENGINES: bin/ with ENGINES.json + lib/) + the real weights from
# KAIROS_MODELS, laid out as on the stick (manifests/tier-8gb.json: files found by basename anywhere under
# KAIROS_MODELS), the GPU held under KAIROS_GPU_LOCK for as long as the kernel runs (systemd user unit
# kairos-desk-gpu) — other GPU jobs wait until `stop`. If the lock is busy, start waits.
# ui / server: empty placeholder models, no GPU, no engine binaries; AI actions answer a clean JSON error
# (503 "the LLM engine is not installed …") — enough for the shell.
# Both serve the LIVE repo's shell + kernel read-only (Ctrl+Shift+R after a shell edit; start again after a
# kernel edit). Desktop state (settings, apps, files) lives in KAIROS_HOME/desk-test/vd and survives
# restarts; bin/ and models/ there are rebuilt every start.
# Network host + 127.0.0.1 bind: the browser is a loopback peer, so guarded routes (settings, wallpaper,
# store) work like in the kiosk. Host networking also means the engine ports are the host's: the kernel's
# llama/sd/translate ports are moved to 1808x so they cannot collide with other services on 808x.
# Needs: docker with the NVIDIA container toolkit (--gpus all) for `start`; the chromium kernel-test image
# (bash dev/docker/build.sh chromium); Firefox on the host (not for `server` / --no-window); systemd --user
# (`start` and the window).
# KAIROS_DESK_NAME (default kairos-desk) names the container and, with -gpu, the GPU unit — set it to run a
# second desk next to another one (with its own KAIROS_DESK_PORT).
set -euo pipefail
. "$(dirname "$0")/env.sh"
K=$KAIROS_HOME
REPO=$REPO_DIR
ENG=$KAIROS_ENGINES
MODELS=$KAIROS_MODELS
VD=$K/desk-test/vd
LOCK=$KAIROS_GPU_LOCK
CT=${KAIROS_DESK_NAME:-kairos-desk}
UNIT=$CT-gpu
IMG=${KAIROS_DESK_IMAGE:-}   # = kernel-test + Chromium: the kernel runtime-checks new apps like on the stick
if [ -z "$IMG" ]; then
  for c in kairos-test-chromium:local kairos-fix-chromium:local; do
    docker image inspect "$c" >/dev/null 2>&1 && { IMG=$c; break; }
  done
  [ -n "$IMG" ] || { echo "no chromium kernel-test image — build it:  bash dev/docker/build.sh chromium" >&2; exit 1; }
fi
PORT=${KAIROS_DESK_PORT:-1380}
URL=http://127.0.0.1:$PORT/
LLAMA_PORT=${KAIROS_DESK_LLAMA_PORT:-18081}
SD_PORT=${KAIROS_DESK_SD_PORT:-18082}
TRANSLATE_PORT=${KAIROS_DESK_TRANSLATE_PORT:-18085}
# a snap Firefox can only write under ~/snap/firefox/common; a deb/flatpak one anywhere
if [ -d "$HOME/snap/firefox/common" ]; then PROFILE=${KAIROS_DESK_PROFILE:-$HOME/snap/firefox/common/kairos-desk-profile}
else PROFILE=${KAIROS_DESK_PROFILE:-$HOME/.cache/kairos-desk-profile}; fi

window() {
  if pgrep -u "$(id -u)" -f "firefox.*--profile $PROFILE" >/dev/null; then
    echo "window: already open — press F5 in it ($URL)"; return 0
  fi
  mkdir -p "$PROFILE"
  [ -e "$PROFILE/user.js" ] || cat >"$PROFILE/user.js" <<'EOF'
user_pref("datareporting.policy.firstRunURL", "");
user_pref("datareporting.policy.dataSubmissionPolicyBypassNotification", true);
user_pref("browser.aboutwelcome.enabled", false);
user_pref("browser.shell.checkDefaultBrowser", false);
user_pref("browser.startup.homepage_override.mstone", "ignore");
EOF
  # its own instance + profile: never mixes with the user's browsing; survives this shell
  systemd-run --user --collect --quiet --unit="kairos-desk-window-$(date +%s)" \
    --setenv=WAYLAND_DISPLAY="${WAYLAND_DISPLAY:-wayland-0}" --setenv=DISPLAY="${DISPLAY:-:0}" \
    --setenv=XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}" \
    firefox --new-instance --profile "$PROFILE" --new-window "$URL"
  echo "window: $URL"
}

stop_kernel() {
  systemctl --user stop "$UNIT" >/dev/null 2>&1 || true
  docker rm -f "$CT" >/dev/null 2>&1 || true
}

# $1 = real|placeholder: bin/ + models/ of the VIBEOS_DIR, rebuilt from scratch
layout() {
  mkdir -p "$VD"/{cache,games,manifests,log,run}
  rm -rf "$VD/bin" "$VD/models"
  mkdir -p "$VD/bin" "$VD/models"
  if [ "$1" = real ]; then
    [ -f "$ENG/bin/ENGINES.json" ] || { echo "no engine stamp at $ENG/bin/ENGINES.json (KAIROS_ENGINES = a built set, docs/how-to/BUILDING.md)" >&2; exit 1; }
    cp "$ENG/bin/ENGINES.json" "$VD/bin/ENGINES.json"
    for b in "$ENG"/bin/*; do
      n=$(basename "$b"); [ "$n" = ENGINES.json ] || ln -s "/opt/vibeos/bin/$n" "$VD/bin/$n"
    done
  else
    python3 "$REPO/scripts/engine-stamp.py" --mock --out "$VD/bin/ENGINES.json"
  fi
  cp "$REPO/manifests/tier-8gb.json" "$VD/manifests/tier-8gb.json"
  python3 - "$VD" "$MODELS" "$1" <<'EOF'
import json, os, sys
from pathlib import Path
vd, models, mode = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
idx = {}
if models.is_dir():
    for root, _, files in os.walk(models):
        for f in files:
            p = Path(root) / f
            if p.stat().st_size > 0:
                idx.setdefault(f, p.relative_to(models))
files, sizes = set(), {}
def walk(n):
    if isinstance(n, dict):
        if isinstance(n.get("file"), str) and n.get("store") != "knowledge":
            files.add(n["file"])
            if isinstance(n.get("size_bytes"), int):
                sizes[n["file"]] = n["size_bytes"]
        for v in n.values():
            walk(v)
    elif isinstance(n, list):
        for v in n:
            walk(v)
walk(json.loads((vd / "manifests" / "tier-8gb.json").read_text()))
missing = []
for f in sorted(files):
    p = vd / "models" / f
    p.parent.mkdir(parents=True, exist_ok=True)
    hit = idx.get(Path(f).name) if mode == "real" else None
    if hit and f in sizes and (models / hit).stat().st_size != sizes[f]:
        hit = None                   # a download still in progress is not installed
    if hit:
        p.symlink_to(Path("/models") / hit)
    else:                            # empty placeholder: "installed", fails if loaded
        p.touch()
        if mode == "real":
            missing.append(f)
print(f"models: {len(files) - len(missing) if mode == 'real' else 0} real, "
      f"{len(missing) if mode == 'real' else len(files)} placeholder"
      + (f" ({', '.join(missing)})" if missing else ""))
EOF
}

RUN_ARGS=(--name "$CT" --network host --user "$(id -u):$(id -g)" --shm-size 1g
  -e VIBEOS_DIR=/vd -e VIBEOS_SHELL=/repo/shell -e VIBEOS_MANIFESTS=/vd/manifests
  -e VIBEOS_MANIFEST=/vd/manifests/tier-8gb.json -e VIBEOS_HOST=127.0.0.1 -e VIBEOS_PORT="$PORT"
  -e VIBEOS_LLAMA_PORT="$LLAMA_PORT" -e VIBEOS_SD_PORT="$SD_PORT" -e VIBEOS_TRANSLATE_PORT="$TRANSLATE_PORT"
  -e VIBEOS_FORCE_OFFLINE=1 -e KAIROS_LOG_DIR=/vd/log -e KAIROS_PREWARM_RUN=/vd/run
  -e VIBEOS_TOKEN_FILE=/vd/token -e LD_LIBRARY_PATH=/opt/vibeos/lib -e PYTHONUNBUFFERED=1 -e HOME=/tmp
  -v "$REPO":/repo:ro -v "$VD":/vd)
[ -d "$ENG" ] && RUN_ARGS+=(-v "$ENG":/opt/vibeos:ro)
[ -d "$MODELS" ] && RUN_ARGS+=(-v "$MODELS":/models:ro)

# the window, or — headless (server, --no-window) — only the address
open_or_print() {
  if [ "$NO_WINDOW" = 1 ]; then
    echo "headless: no window — open $URL in a browser (from another machine: ssh -L $PORT:127.0.0.1:$PORT <this host>)"
  else
    window
  fi
}

wait_up() {
  for _ in $(seq "$1"); do
    curl -fsS -m 2 "${URL}api/status" >/dev/null 2>&1 && return 0
    sleep 1
  done
  return 1
}

MODE=${1:-start}
NO_WINDOW=0
[ $# -gt 0 ] && shift
for a in "$@"; do
  case "$a" in
    --no-window) NO_WINDOW=1 ;;
    *) echo "unknown option: $a (usage: $0 [start|ui|server|window|stop] [--no-window])" >&2; exit 2 ;;
  esac
done
[ "$MODE" = server ] && { MODE=ui; NO_WINDOW=1; }

case "$MODE" in
  stop)
    stop_kernel; echo "stopped $CT (GPU lock released)" ;;
  window)
    window ;;
  ui)
    stop_kernel; layout placeholder
    docker run -d "${RUN_ARGS[@]}" --memory 4g "$IMG" python3 /repo/kernel/server.py >/dev/null
    wait_up 60 || { docker logs --tail 40 "$CT"; exit 1; }
    echo "kernel: $CT (UI only) on $URL (logs: docker logs -f $CT; stop: $0 stop)"; open_or_print ;;
  start)
    stop_kernel; layout real
    mkdir -p "$(dirname "$LOCK")"; touch "$LOCK"
    if ! flock -n "$LOCK" true; then
      echo "GPU lock busy — the kernel starts when it is free:"; fuser -v "$LOCK" 2>&1 | tail -n +2 || true
    fi
    # flock holds the GPU lock exactly as long as the (foreground) container runs
    systemd-run --user --unit="$UNIT" --collect --quiet \
      -p StandardOutput=append:"$VD/log/container.log" -p StandardError=append:"$VD/log/container.log" \
      flock "$LOCK" docker run --rm "${RUN_ARGS[@]}" --gpus all --memory 28g --memory-swap 28g \
        "$IMG" python3 /repo/kernel/server.py
    wait_up 120 || { echo "kernel not up yet — see $VD/log/container.log"; exit 1; }
    echo "kernel: $CT on the GPU at $URL (logs: docker logs -f $CT; stop: $0 stop)"; open_or_print ;;
  *)
    echo "usage: $0 [start|ui|server|window|stop] [--no-window]" >&2; exit 2 ;;
esac
