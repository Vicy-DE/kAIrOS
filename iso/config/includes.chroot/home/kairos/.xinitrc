#!/bin/sh
# kAIrOS kiosk [R52 multiscreen]: openbox + one fullscreen Chromium per display.
# The user must never see Linux [R28]: the boot screen painter (scripts/splash/, started
# in the initramfs) drew the thick kAIrOS logo + loading bar on the framebuffer until X
# took the display; X keeps that last frame (-background none) and the kiosk opens a LOCAL
# boot page with the same logo and bar that continues the progress seamlessly and hands
# over to the shell the moment the kernel answers [R73] — never a Chromium error page.
# breadcrumbs (stdout -> /var/log/kairos/x.log, read by kairos-bootlog): where a session that
# never reached its kiosk stopped — on the hybrid laptop X took 92 s to start and then never
# answered this script's first X calls, and x.log said nothing (stick run 2026-10-06)
echo "xinitrc: started (pid $$, uptime $(cut -d' ' -f1 /proc/uptime 2>/dev/null)s)"
xset -dpms; xset s off; xset s noblank
# [R28/R73] no solid root colour here: X was started with -background none, so the root
# still shows the painter's last frame (logo + bar) — the modesetting driver copied the
# framebuffer into its screen — until the kiosk's boot page covers it. A solid colour
# would be a visible step between the two.
# -root: hide the idle pointer over the bare root too (the painter's frame while Chromium starts)
unclutter -idle 2 -root &
# openbox-session reads ~/.config/openbox/rc.xml (shipped: no decorations, no root
# menu, no key bindings [R28/R68]) — it is the stacking layer under the kiosk and
# the window manager the kernel drives over EWMH for game windows (kernel/wm.py)
openbox-session &
echo "xinitrc: X answers (xset done), openbox launched"
# [R28/D-R68-5] openbox puts an X11 arrow on the bare root (X shows no cursor only until
# the first client defines one; X runs WITHOUT -nocursor, which would hide every pointer
# for good — .bash_profile): replace it with an invisible one — a 1x1 bitmap
# with an empty mask — once now and once after openbox has set its own. Only visible
# when no kiosk covers the root; unclutter still hides the pointer over the kiosk.
# Harmless when xsetroot (x11-xserver-utils) or the file is missing.
BLANK=${XDG_RUNTIME_DIR:-/tmp}/kairos-blank-cursor.xbm
printf '#define b_width 1\n#define b_height 1\n#define b_x_hot 0\n#define b_y_hot 0\nstatic unsigned char b_bits[] = { 0x00 };\n' > "$BLANK" 2>/dev/null
root_cursor_off() { [ -f "$BLANK" ] && xsetroot -cursor "$BLANK" "$BLANK" 2>/dev/null; }
root_cursor_off; ( sleep 3; root_cursor_off ) &

# ---- extend every connected display, left-to-right (no mirroring) ----
# kairos-display-apply re-applies the saved layout from the display API; if it is
# not present or has no saved layout, fall back to a plain auto-extend.
layout_extend() {
    prev=""
    for o in $(xrandr --query 2>/dev/null | awk '/ connected/{print $1}'); do
        if [ -z "$prev" ]; then
            xrandr --output "$o" --auto --primary 2>/dev/null
        else
            xrandr --output "$o" --auto --right-of "$prev" 2>/dev/null
        fi
        prev="$o"
    done
}
if [ -x /usr/local/sbin/kairos-display-apply ]; then
    /usr/local/sbin/kairos-display-apply || layout_extend
else
    layout_extend
fi
# breadcrumbs for the boot log [R51] (stdout -> /var/log/kairos/x.log)
echo "xinitrc: displays: $(xrandr --query 2>/dev/null | grep -E ' connected' | tr '\n' ';')"

# the boot page [R73]: the kiosk opens at once — NOT after the kernel answers — on the
# local shell/boot-wait.html, which shows the same thick logo + loading bar as the
# framebuffer painter, continues its progress (the painter's last value, the expected
# boot time and the uptime it was taken at go in the URL), polls the kernel and replaces
# itself with http://localhost:1337/ the moment /api/status answers. Chromium needs
# seconds to start on a USB stick: those seconds show the painter's frame, not a blank.
# Without the page (a broken tree) the kiosk opens the shell directly as before.
kernel_up() { curl -fsS http://localhost:1337/api/status >/dev/null 2>&1; }
KIOSK_URL="http://localhost:1337/"
BOOT_QS=""
BOOTPAGE=/opt/vibeos/os/shell/boot-wait.html
if [ -f "$BOOTPAGE" ]; then
    KIOSK_URL="file://$BOOTPAGE"
    E=$(cat /run/kairos-splash/expected 2>/dev/null); case "$E" in ''|*[!0-9.]*) E=120 ;; esac
    P=$(cat /run/kairos-splash/progress 2>/dev/null); case "$P" in ''|*[!0-9.]*) P=0.6 ;; esac
    BOOT_QS="&boot=1&up=$(cut -d' ' -f1 /proc/uptime 2>/dev/null)&now=$(date +%s%3N 2>/dev/null)&expected=$E&p=$P"
    echo "xinitrc: kiosk opens the boot page (progress $P of expected ${E}s), then the shell"
fi
root_cursor_off                    # openbox is up: its arrow goes until the kiosk is (see below)
# breadcrumb for the boot log [R51]: when the kernel first answered (bounded, in the background)
( i=0
  while [ "$i" -lt "${KAIROS_KERNEL_WAIT:-600}" ] && ! kernel_up; do i=$((i + 1)); sleep 1; done
  if kernel_up; then echo "xinitrc: kernel answered on :1337 after ${i}s"
  else echo "xinitrc: kernel NOT answering on :1337 after ${i}s — the boot page keeps waiting"; fi ) &

# ---- launch one kiosk Chromium per connected monitor, at its geometry ----
# Each instance gets its own profile dir and a ?screen=N&x=&y=&w=&h= hint that
# shell/shell.js reads: screen 0 is the PRIMARY output (dock + first-boot setup +
# machine-level actions live there), the rest are numbered left-to-right / top-
# to-bottom as extra workspaces. Outputs mirroring one geometry share one kiosk.
# [R68/F11] --isolate-sandboxed-iframes + per-document grouping: every generated app
# (a sandboxed srcdoc iframe) gets its own renderer process. Measured without it
# (docs/DESKTOP-TECH-EVAL-2026-09.md §8.9): a `while(true){}` in ONE app froze every
# other app on the desktop in every benchmark run — Chromium isolates sandboxed
# iframes from their parent by default since 127, but groups them per origin, so all
# apps of one kiosk shared one process. Per-document costs one renderer per open app;
# the memory price is measured in the next VM run. Also what lets a closed app's
# memory go back at once: its process simply exits (R68 e).
CHROME_FLAGS="--noerrdialogs --disable-infobars --incognito --no-first-run \
--disable-translate --check-for-update-interval=31536000 \
--overscroll-history-navigation=0 --disable-pinch \
--isolate-sandboxed-iframes --enable-features=IsolateSandboxedIframes:grouping/per-document"

kiosk_geoms() {
    # one "WxH+X+Y" per surface: primary first, then by x, then y. (Plain sort -u
    # ordered the STRINGS: "1024x768+1920+0" < "1920x1080+0+0" made a small
    # external monitor screen 0 and put the dock there.)
    xrandr --query 2>/dev/null | awk '
        / connected/ && match($0, /[0-9]+x[0-9]+\+[0-9]+\+[0-9]+/) {
            g = substr($0, RSTART, RLENGTH)
            if (g in seen) next
            seen[g] = 1
            split(g, a, "+")
            k = ($0 ~ / primary /) ? -1 : a[2] + 0
            print k, a[3] + 0, g
        }' | sort -n -k1,1 -k2,2 | cut -d" " -f3
}

# [R28/R51] pointer evidence for the boot log (x.log; "no pointer" was reported three times):
# the pointer devices X uses and how X draws the sprite — the cursor lines of the Xorg log
# (xorg.conf.d/20-kairos-swcursor.conf's OutputClass + "SWcursor" option). Once per session,
# after the first kiosk launch, in the background; silent where a tool or the log is missing.
PTR_DIAG=""
pointer_diag() {
    XL=$HOME/.local/share/xorg/Xorg.0.log; [ -r "$XL" ] || XL=/var/log/Xorg.0.log
    if command -v xinput >/dev/null 2>&1; then
        echo "xinitrc: pointer devices (xinput): $(xinput list --short 2>/dev/null | grep 'slave  pointer' | sed 's/^[^[:alnum:]]*//; s/[[:space:]]*id=.*//' | tr '\n' ';')"
    else
        echo "xinitrc: pointer devices (Xorg log): $(grep -a 'XINPUT: Adding extended input device' "$XL" 2>/dev/null | grep -a -v 'type: KEYBOARD' | sed 's/.*device "\(.*\)" (type: \([A-Z_]*\).*/\1 [\2]/' | tr '\n' ';')"
    fi
    echo "xinitrc: pointer cursor (Xorg log): $(grep -a -i -E 'swcursor|hw ?cursor|hardware cursor|software cursor|OutputClass "kAIrOS' "$XL" 2>/dev/null | sed 's/^\[ *[0-9.]*\] //' | cut -c1-120 | tr '\n' ';')"
}

KIOSK_PIDS=""
launch_all_kiosks() {
    geoms=$(kiosk_geoms)
    [ -z "$geoms" ] && geoms="0x0+0+0"   # headless / unknown -> one plain kiosk
    echo "xinitrc: launching kiosks for: $(echo $geoms)"
    # runtime flags: /etc/kairos/chromium.flags (persistent) and /run/kairos/chromium.flags
    # (this boot), flags separated by spaces/newlines, # comments. Read on EVERY launch,
    # so `touch /run/kairos/kiosk-reload` picks up an edit — CHROME_FLAGS above is a
    # variable of this shell and fixed for the life of the X session (VM test 2026-09-11).
    extra=""
    for f in /etc/kairos/chromium.flags /run/kairos/chromium.flags; do
        [ -r "$f" ] && extra="$extra $(sed 's/#.*//' "$f" | tr '\n' ' ')"
    done
    KIOSK_PIDS=""; n=0
    for g in $geoms; do
        w=${g%%x*}; r=${g#*x}; h=${r%%+*}; r=${r#*+}; x=${r%%+*}; y=${r#*+}
        if [ "$w" = "0" ]; then
            chromium --kiosk $CHROME_FLAGS $extra --user-data-dir="/tmp/kiosk-0" \
                --app="${KIOSK_URL}?screen=0${BOOT_QS}" &
        else
            # kiosk mode here too (not the browser-fullscreen switch this path used before):
            # a browser-fullscreen window shows Chromium's "To exit full screen, press and
            # hold Esc" bubble for seconds over the boot page and the desktop [R28]; kiosk
            # mode never shows it (and Esc does not leave). The window is placed on its
            # monitor first (--window-position), then made fullscreen there.
            chromium --kiosk $CHROME_FLAGS $extra --user-data-dir="/tmp/kiosk-$n" \
                --window-position="$x,$y" --window-size="$w,$h" \
                --app="${KIOSK_URL}?screen=$n&x=$x&y=$y&w=$w&h=$h${BOOT_QS}" &
        fi
        KIOSK_PIDS="$KIOSK_PIDS $!"
        n=$((n + 1))
    done
    echo "xinitrc: kiosk pids:$KIOSK_PIDS"
    # the POINTER: the kiosk's windows inherit the root window's cursor (Chromium sets none of
    # its own for the plain arrow), and the root carried the invisible boot cursor "for good" —
    # no pointer anywhere on the desktop (stick run 2026-10-07). With the kiosk up the root gets
    # a real arrow; unclutter hides it after 2 s without movement (R28 stays: nothing shows idle).
    xsetroot -cursor_name left_ptr 2>/dev/null && echo "xinitrc: pointer: left_ptr on the root (unclutter hides it when idle)"
    [ -n "$PTR_DIAG" ] || { PTR_DIAG=1; ( sleep 4; pointer_diag ) & }
    # a relaunch (display change, browser crash) is not a boot: the page shows the logo
    # without the bar and hands over as soon as the kernel answers
    BOOT_QS="&relaunch=1"
}

# [R73/R28] the painter's picture on the root window until the kiosk draws: X was started
# with -background none, which keeps the framebuffer's last frame only when the driver copies
# it (amdgpu DDX / modesetting WITH glamor — the laptop, per its Xorg.0.log; not the testrig
# VM). Make it explicit and driver-independent: render the current frame as this user
# (kairos-splash --once into a file, same picture and progress as the boot page) and set it
# as the retained root background (scripts/splash/root-cover.py, python-xlib). Runs in the
# background, never holds the kiosk launch; every failure is silent (cosmetic, R63).
root_cover() {
    g=$(kiosk_geoms | head -n 1); [ -n "$g" ] && [ "${g%%x*}" != 0 ] || return 0
    w=${g%%x*}; r=${g#*x}; h=${r%%+*}; r=${r#*+}; x=${r%%+*}; y=${r#*+}
    d=/run/kairos/splash-cover; mkdir -p "$d/sys" 2>/dev/null || return 0
    printf '%s,%s\n' "$w" "$h" > "$d/sys/virtual_size"; echo 32 > "$d/sys/bits_per_pixel"
    echo $((w * 4)) > "$d/sys/stride"; echo rootcover > "$d/sys/name"
    E=$(cat /run/kairos-splash/expected 2>/dev/null); case "$E" in ''|*[!0-9.]*) E=120 ;; esac
    P=$(cat /run/kairos-splash/progress 2>/dev/null); case "$P" in ''|*[!0-9.]*) P=0.6 ;; esac
    KAIROS_FB="$d/frame.raw" KAIROS_FB_SYS="$d/sys" KAIROS_TTY=none KAIROS_SPLASH_LOG=- KAIROS_SPLASH_RUN="$d" KAIROS_BGRT=/nonexistent \
        /usr/local/sbin/kairos-splash --once --no-reexec --phase 2 --progress "$P" --expected "$E" >/dev/null 2>&1 || return 0
    python3 /opt/vibeos/os/scripts/splash/root-cover.py "$d/frame.raw" "$w" "$h" "$x" "$y" >/dev/null 2>&1 \
        && echo "xinitrc: root cover: the boot screen frame on the root window (${w}x${h}+$x+$y, progress $P)"
    return 0
}

# liveness of the BROWSERS themselves — the PIDs launched above (/usr/bin/chromium
# execs the binary, so $! is the browser process). A `pgrep -f chromium.*--user-
# data-dir` check also matched the zygote/renderer/gpu helpers, which can outlive a
# crashed browser: the supervisor then never relaunched and the screen stayed blank.
# A dead child not yet reaped is a zombie (Z) — kill -0 still succeeds on it.
kiosks_alive() {
    for p in $KIOSK_PIDS; do
        kill -0 "$p" 2>/dev/null || return 1
        case "$(sed 's/.*) //' "/proc/$p/stat" 2>/dev/null | cut -d' ' -f1)" in
            Z|X) return 1 ;;
        esac
    done
    return 0
}

# boot screen -> kiosk handover [R73/R28]: the framebuffer painter (root, started in the
# initramfs) is still alive underneath — X owns the display now, so its frames are
# invisible, and it would re-cover the console if X died. Once the kiosk is launched the
# flag file ends it: it exits WITHOUT touching the VT mode (X owns tty1's mode); the
# kiosk user cannot signal a root process, hence a file in /run/kairos (tmpfiles.d).
# Runs once; later calls are no-ops.
handover_splash() {
    [ -e /run/kairos/splash-handover ] && return 0
    : > /run/kairos/splash-handover 2>/dev/null && echo "xinitrc: boot screen handed over (painter ends, X owns the display)"
}

# [R78] the captive-portal sign-in window: the kernel (root, kernel/hw.py OPEN_URL/CLOSE_URL) asks
# for it with a flag file in /run/kairos (owned by kairos — the same rules as the reload flag);
# HERE (user kairos) the URL opens in a real Chromium --app window with a profile of its own —
# not a kiosk, so it is a normal window above the desktop (Ctrl+W closes it) and the kiosk's
# pkill pattern never matches it — and the flag goes. The close flag, written once the device
# is online (or off the network, or after 15 min), ends that window. Only http(s) URLs open.
OPEN_URL=/run/kairos/open-url
CLOSE_URL=/run/kairos/close-url
signin_window() {
    if [ -f "$OPEN_URL" ]; then
        url=$(head -n 1 "$OPEN_URL" 2>/dev/null)
        rm -f "$OPEN_URL"
        case "$url" in
            http://*|https://*)
                pkill -f "user-data-dir=/tmp/kairos-signin" 2>/dev/null
                chromium --app="$url" --user-data-dir=/tmp/kairos-signin --class=kairos-signin \
                    --no-first-run --noerrdialogs --disable-infobars --window-size=1100,800 >/dev/null 2>&1 &
                echo "xinitrc: sign-in window for $url (pid $!)" ;;
            *) echo "xinitrc: sign-in flag ignored (not an http(s) URL)" ;;
        esac
    fi
    if [ -f "$CLOSE_URL" ]; then
        rm -f "$CLOSE_URL"
        pkill -f "user-data-dir=/tmp/kairos-signin" 2>/dev/null && echo "xinitrc: sign-in window closed"
    fi
    return 0
}

# (re)launch kiosks; restart them all if a BROWSER dies or a display reload is requested.
# The flag is written by the kernel (root, kernel/hw.py KIOSK_RELOAD) and removed
# HERE (user kairos): it lives in /run/kairos (tmpfiles.d, owned by kairos), never
# in sticky /tmp where rm -f of a root-owned file silently fails and the loop
# would relaunch every Chromium every 3 s until reboot [R53].
# A reload relaunches with the SAME CHROME_FLAGS (shell variable); flags added at
# runtime go into the files launch_all_kiosks reads. Anything else in this script
# (openbox, xrandr layout) needs `systemctl restart getty@tty1` = a new X session.
RELOAD=/run/kairos/kiosk-reload
( root_cover ) &
while true; do
    pkill -f "chromium.*--user-data-dir=/tmp/kiosk-" 2>/dev/null
    rm -f "$RELOAD"
    [ -e "$RELOAD" ] && echo "xinitrc: WARNING cannot remove $RELOAD ($(ls -l "$RELOAD" 2>/dev/null))"
    launch_all_kiosks
    handover_splash
    while true; do
        sleep 3
        signin_window                                     # [R78] a portal page asked for / done
        [ -f "$RELOAD" ] && break                         # display layout changed
        kiosks_alive || { echo "xinitrc: a kiosk browser died (pids:$KIOSK_PIDS) — relaunching"; break; }
    done
done
