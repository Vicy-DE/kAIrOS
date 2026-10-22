#!/usr/bin/env bash
# kAIrOS verification: boots the kernel against mock engines, runs the full test
# suite (tests/test_kernel.py), then regenerates docs/PROGRESS.md.
# Usage: run-tests.sh   (needs python3-aiohttp — run it inside the testrig container:
#   cd pod && docker compose run --rm --entrypoint bash dev -lc 'cd /images/repo/vibeOS-local && bash tests/run-tests.sh')
set -uo pipefail
REPO=$(cd "$(dirname "$0")/.." && pwd)
TESTDIR=/tmp/kairos-test
PORT=1338

echo "== setting up mock environment at $TESTDIR =="
rm -rf "$TESTDIR"
mkdir -p $TESTDIR/bin $TESTDIR/cache $TESTDIR/models $TESTDIR/games
chmod +x "$REPO/tests/mock-llama-server" "$REPO/tests/mock-sd-cli" 2>/dev/null || true
ln -sf "$REPO/tests/mock-llama-server" $TESTDIR/bin/llama-server
ln -sf "$REPO/tests/mock-sd-cli" $TESTDIR/bin/sd-cli
# sd-server (multi-image sessions) is NOT linked by default: the suite proves the sd-cli
# fallback (the proven 2026-09-14 set has no server) and links tests/mock-sd-server itself
# for the session tests (docs/INTEGRATION-2026-09-28.md §4)
chmod +x "$REPO/tests/mock-sd-server" 2>/dev/null || true
export MOCK_SDSERVER_LOG=$TESTDIR/sd-server.log
# [R79] the sd-server idle window (8 s on the OS) is off for the suite: its tests pin one session per
# call; tests/test_imaging_workflow.py proves the window itself
export VIBEOS_SD_SESSION_IDLE=0
# music engine [R31]: acestep.cpp builds ace-lm + ace-synth (request-JSON driven), no ace-cli
chmod +x "$REPO/tests/mock-ace-lm" "$REPO/tests/mock-ace-synth" 2>/dev/null || true
ln -sf "$REPO/tests/mock-ace-lm" $TESTDIR/bin/ace-lm
ln -sf "$REPO/tests/mock-ace-synth" $TESTDIR/bin/ace-synth
# the engine stamp the kernel pairs with the manifests' "engines" requirement [R3/R47]: the
# mocks get an EXPLICIT stamp dated now (same writer as the build) — a missing stamp is a
# refused set, never a silently skipped check
python3 "$REPO/scripts/engine-stamp.py" --mock --out $TESTDIR/bin/ENGINES.json
# hardware API mocks: nmcli + a fake sysfs (battery/backlight) [R38]
chmod +x "$REPO/tests/mock-nmcli" 2>/dev/null || true
ln -sf "$REPO/tests/mock-nmcli" $TESTDIR/bin/nmcli
# display / datetime / bluetooth mocks [R53-R55]
chmod +x "$REPO/tests/mock-xrandr" "$REPO/tests/mock-timedatectl" "$REPO/tests/mock-bluetoothctl" 2>/dev/null || true
ln -sf "$REPO/tests/mock-xrandr" $TESTDIR/bin/xrandr
ln -sf "$REPO/tests/mock-timedatectl" $TESTDIR/bin/timedatectl
ln -sf "$REPO/tests/mock-bluetoothctl" $TESTDIR/bin/bluetoothctl
export MOCK_XRANDR_LOG=$TESTDIR/xrandr-args.log
export KAIROS_KIOSK_RELOAD=$TESTDIR/kiosk-reload
# WiFi / Bluetooth / airplane [R78]: the mocks keep their state next to the kernel (mock-nmcli.json,
# mock-bt.json under $VIBEOS_DIR), rfkill works on the fake sysfs below, the sign-in flags and the
# NetworkManager conf dirs stay inside $TESTDIR (tests/test_wifi_bt.py builds its own worlds)
chmod +x "$REPO/tests/mock-rfkill" 2>/dev/null || true
ln -sf "$REPO/tests/mock-rfkill" $TESTDIR/bin/rfkill
export KAIROS_OPEN_URL=$TESTDIR/open-url KAIROS_CLOSE_URL=$TESTDIR/close-url KAIROS_NM_CONF_DIRS=$TESTDIR/nm-conf.d
mkdir -p $TESTDIR/sys/class/power_supply/BAT0 $TESTDIR/sys/class/power_supply/AC \
         $TESTDIR/sys/class/backlight/mock_panel
echo 73        > $TESTDIR/sys/class/power_supply/BAT0/capacity
echo Charging  > $TESTDIR/sys/class/power_supply/BAT0/status
echo 48000000  > $TESTDIR/sys/class/power_supply/BAT0/energy_full
echo 60000000  > $TESTDIR/sys/class/power_supply/BAT0/energy_full_design
echo 1         > $TESTDIR/sys/class/power_supply/AC/online
echo 128       > $TESTDIR/sys/class/backlight/mock_panel/brightness
echo 255       > $TESTDIR/sys/class/backlight/mock_panel/max_brightness
mkdir -p $TESTDIR/sys/class/bluetooth/hci0          # the Bluetooth controller hw.py looks for [R78]
# [fix 2026-10-22] the GPU the mock engines stand for: a fake NVIDIA driver dir that lists one device
# (/proc/driver/nvidia/gpus/<bus id>), so every kernel of the suite runs as the appliance WITH its GPU (quality
# translation = the loaded LLM [R89]); tests/test_kernel.py takes the device away to prove the no-GPU path
# (quality → the fast translator, "reason": "no GPU") and puts it back. A test that wants no driver sets its own.
mkdir -p "$TESTDIR/nvidia/gpus/0000:01:00.0"
export VIBEOS_NVIDIA_PROC=$TESTDIR/nvidia
for r in "0 bluetooth hci0" "1 wlan phy0"; do set -- $r
    mkdir -p $TESTDIR/sys/class/rfkill/rfkill$1
    echo $2 > $TESTDIR/sys/class/rfkill/rfkill$1/type; echo $3 > $TESTDIR/sys/class/rfkill/rfkill$1/name
    echo 0 > $TESTDIR/sys/class/rfkill/rfkill$1/soft; echo 0 > $TESTDIR/sys/class/rfkill/rfkill$1/hard
done
export VIBEOS_SYSFS=$TESTDIR/sys
# use the real knowledge base if installed (kiwix is CPU-only, cheap)
[ -x /opt/vibeos/bin/kiwix-serve ] && ln -sf /opt/vibeos/bin/kiwix-serve $TESTDIR/bin/kiwix-serve
[ -d /opt/vibeos/knowledge ] && ln -sfn /opt/vibeos/knowledge $TESTDIR/knowledge

export VIBEOS_DIR=$TESTDIR
export VIBEOS_SHELL=$REPO/shell
export VIBEOS_MANIFESTS=$REPO/manifests
export VIBEOS_MANIFEST=$REPO/manifests/tier-8gb.json
# placeholder model files so presence checks (add-on models, tiers) resolve in the
# mock env — the mock sd-cli/ace-lm/ace-synth ignore contents [R47]
python3 - "$TESTDIR/models" "$REPO/manifests" <<'PY'
import json, sys, pathlib
models, mans = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
def files(n, out):
    if isinstance(n, dict):
        # store: knowledge = a ZIM add-on in the knowledge dir, never a models placeholder [R71 P3]
        if "url" in n and "file" in n and n.get("store") != "knowledge": out.add(n["file"])
        for v in n.values(): files(v, out)
seen = set()
for m in mans.glob("tier-*.json"):
    files(json.loads(m.read_text()), seen)
for f in seen:
    p = models / f; p.parent.mkdir(parents=True, exist_ok=True); p.touch()
print(f"  touched {len(seen)} placeholder model files")
PY
export VIBEOS_PORT=$PORT
export VIBEOS_FORCE_OFFLINE=1            # prove the OS works with the cable pulled
# [R66] the suite boots the kernel in the EXPOSED configuration (what the testrig /
# remote monitoring uses) so the container's eth0 address can play the remote attacker;
# the loopback DEFAULT is proven by a second, short-lived kernel inside the test itself.
export VIBEOS_HOST=0.0.0.0
export MOCK_SD_ARGLOG=$TESTDIR/sd-args.log
export MOCK_ACE_ARGLOG=$TESTDIR/ace-args.log
export MOCK_LLAMA_ARGLOG=$TESTDIR/llama-args.log   # every llama-server launch's argv [R70]
export MOCK_LLAMA_MSGLOG=$TESTDIR/llama-msgs.log   # every request's system/user text + port [R71]
export KAIROS_URL=http://127.0.0.1:$PORT

if python3 -c 'import aiohttp' 2>/dev/null; then
    echo "== booting kernel (mock engines, forced offline) =="
    python3 "$REPO/kernel/server.py" > $TESTDIR/kernel.log 2>&1 &
    KPID=$!
    trap 'kill $KPID 2>/dev/null' EXIT

    for i in $(seq 1 60); do
        curl -fsS "$KAIROS_URL/api/status" >/dev/null 2>&1 && break
        if ! kill -0 $KPID 2>/dev/null; then
            echo "KERNEL DIED AT BOOT:"; tail -20 $TESTDIR/kernel.log; exit 2
        fi
        sleep 0.5
    done
else
    echo "== aiohttp not installed — skipping kernel boot, unit subset only =="
    KPID=""
fi

# the JS syntax gate [D7, docs/FIXES-2026-09-28.md] needs a real parser: trixie's quickjs (qjsc) or node.
# Without one the two gate tests skip — run the suite in a trixie image with quickjs (kairos-fix:local).
echo "== js syntax gate parser: $(command -v qjsc || command -v node || echo "NONE (gate tests skip)") =="
echo "== running test suite =="
python3 "$REPO/tests/test_kernel.py"
RC=$?

# parallel LLM requests [R72]: the GPU scheduler, the `parallel` manifest key and a kernel of its
# own (:1343, this file's mock llama-server with -np accounting) — standalone, after the suite
echo "== running tests/test_llm_parallel.py =="
python3 "$REPO/tests/test_llm_parallel.py" || RC=1

# the boot screen [R73]: the framebuffer painter (built with cc or the committed static binary),
# BGRT/layout/progress maths, the fb handover, signals, the initramfs/unit/.xinitrc contracts
echo "== running tests/test_splash.py =="
python3 "$REPO/tests/test_splash.py" || RC=1

# v7.5 desktop + web [R74 clock menu, R75 web consistency, pointer, ESP automount, game exits at the
# session end]: in-process kernel with a private WEB_DIR; the Chromium clock-menu test skips here
# (run it in the Playwright image — docs/V75-DESKTOP-WEB-2026-10-07.md)
echo "== running tests/test_desktop_web.py =="
python3 "$REPO/tests/test_desktop_web.py" || RC=1

# the default image model [R76 → Muse, 2026-10-10]: Muse v3.5 Extended Q4_0, no LoRA (v7.5: Krea 2 + the Kroma
# v0.3 LoRA) — manifest pins, the bake list, sd-cli/sd-server argv + the /sdapi/v1/loras contract (the kernel's
# LoRA mechanism on a test-only `lora` entry), key resolution, the neutral expander (own kernel :1346)
echo "== running tests/test_image_lora.py =="
python3 "$REPO/tests/test_image_lora.py" || RC=1

# forge quality [R77]: the game static gate (vendored pyflakes), the headless play-test, the identical-
# resubmission guard, main.py never broken by a failed evolution, the app runtime check (its Chromium
# part skips without a chromium), 4 app repair rounds, the regression fallback (own kernel :1351)
echo "== running tests/test_forge_quality.py =="
python3 "$REPO/tests/test_forge_quality.py" || RC=1

# imaging without model thrash [R79]: the resident LLM expands (no expander server), kernel-written
# prompts are not re-expanded, the image files' page cache is dropped, kairos-prewarm's LLM-first read +
# the kernel's bounded first-start wait, the sd-server idle window (FIFO intact), the cursor conf
echo "== running tests/test_imaging_workflow.py =="
python3 "$REPO/tests/test_imaging_workflow.py" || RC=1

# WiFi / Bluetooth / airplane [R78]: hw.py against the stateful mocks (escapes, real SSID, OWE-TM,
# wrong password without stale profiles, portal sign-in, the pty pairing agent, rfkill, airplane),
# the bridge + host pages, the docs the coder LLM gets, the NM connectivity conf, .xinitrc + bootlog
echo "== running tests/test_wifi_bt.py =="
python3 "$REPO/tests/test_wifi_bt.py" || RC=1

# the refusal fallback [R86]: the detector (EN/DE/FR/ES/IT + false-positive guards), the narrow minors block (round 4) + the
# 25+ check on adult picture prompts [R88], and a
# kernel of its own (:1366) with the mock llama-server's refusal tokens — preamble rung, the llm_uncensored
# swap, the exhausted ladder (nothing cached), cached refusal pages regenerating, the streaming `refusal`
# event, the expander's encoder rung / raw prompt, llm_model=uncensored, the image entry points blocked
echo "== running tests/test_refusal_fallback.py =="
python3 "$REPO/tests/test_refusal_fallback.py" || RC=1

# the dock, the built-in browser, ✨ improve a website [R81/R83/R84]: the kernel's web routes in-process with a mocked
# LLM (a private VIBEOS_DIR), and the REAL shell in a headless Chromium over CDP (skips without Chromium — run it in
# kairos-fix-chromium:local; KAIROS_SHOTS=<dir> saves the screenshots)
echo "== running tests/test_dock_browser.py =="
python3 "$REPO/tests/test_dock_browser.py" || RC=1

# games in windows [R82]: launch = run, ✨ = evolve, the frame <-> game-window pairs; the e2e part needs a REAL X server
# (Xvfb + openbox with the kiosk's rc.xml + a kiosk Chromium + real pygame windows) and skips without one — run the suite
# in kairos-fix-x11:local (bash dev/docker/build.sh x11; KAIROS_TEST_IMAGE=kairos-fix-x11:local); KAIROS_SHOTS=<dir>
echo "== running tests/test_games_windows.py =="
python3 "$REPO/tests/test_games_windows.py" || RC=1

# ✨ improve a picture [R85]: the `image_edit` manifest section, the editor's argv (--llm_vision, its own schedule) and the
# reference picture through the mock sd-server (own port :8151), the version chain + revert + whole-chain eviction, the
# entry checks (the minors block, the add-on missing), the translation hop, the sd floor; the e2e part (the picture viewer,
# the version strip, the quick viewer, Imagine, the wallpaper ✨, an app's vibe.image.improve [R88]) needs Chromium and skips
# without it (KAIROS_SHOTS=<dir>)
echo "== running tests/test_picture_edit.py =="
python3 "$REPO/tests/test_picture_edit.py" || RC=1

# the public images [R87]: the build-time profile (private = untouched, public = Z-Image default), the first-boot downloads
# (aria2c against a local mock: pause, offline, Range resume, sha256), Muse with the user's civitai key (mock civitai + CDN:
# the key reaches civitai's origin only), the licences + the panel (Chromium; skips without), the image prep script and its
# raw-byte scrub on a planted GPT image (needs e2fsprogs / dosfstools / mtools / sfdisk; skips without them)
echo "== running tests/test_public_profile.py =="
python3 "$REPO/tests/test_public_profile.py" || RC=1

# one desktop across all monitors [R90]: the monitor math in kernel/monitors.py AND shell/monitors.js (node or QuickJS),
# the window rules, the layout-change path, the display API on mock-xrandr layouts (2/3 monitors, stacked, mirrored), the
# X side's hot-plug plan, .xinitrc's one launch line; the e2e part needs a REAL X server (Xvfb + openbox + the kiosk
# Chromium + scripts/desktop/kairos-desktop.py) and skips without one — kairos-fix-x11:local; KAIROS_SHOTS=<dir>
echo "== running tests/test_one_desktop.py =="
python3 "$REPO/tests/test_one_desktop.py" || RC=1

if [ -z "${KAIROS_TEST_ONLY:-}" ]; then          # a subset run (iterating on a fix) never rewrites the progress page
    echo "== regenerating docs/PROGRESS.md =="
    python3 "$REPO/scripts/update-progress.py"
fi

[ $RC -ne 0 ] && { echo "== kernel log tail =="; tail -30 $TESTDIR/kernel.log; }
exit $RC
