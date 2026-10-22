#!/bin/sh
# kAIrOS kiosk [R90 one desktop]: openbox + ONE Chromium window over every monitor (the X screen).
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

# ---- ONE kiosk Chromium over every monitor [R90] ----
# The desktop is one page spanning the whole X screen, so windows move between the monitors like
# on any OS. Chromium's --kiosk / fullscreen covers ONE monitor only: the kiosk is an --app window
# (class kairos-desktop) at 0,0 with the X screen's size — openbox's rule for that class (rc.xml)
# keeps it undecorated, in the normal layer (above nothing: games and the sign-in window stay
# above it) and at 0,0; its profile asks for no Chromium title bar (custom_chrome_frame off); the
# policy /etc/chromium/policies/managed/kairos-desktop.json allows no fullscreen (F11 would shrink
# the desktop to one monitor). scripts/desktop/kairos-desktop.py (started below) puts the window
# at exactly 0,0,W,H once it maps (Chromium maps a screen-sized window clamped to ONE monitor, or
# 1 px short on a single one; openbox clamps a per-app <size> to one monitor too — measured) and
# again after every layout change / hot-plug — no relaunch. The page learns the monitors from ?mon= at once
# (kernel/monitors.py hint: id:x,y,w,h,primary;…), then live from the kernel [R90].
# R52's one kiosk per monitor is gone: openbox centred every kiosk on the monitor of the focused
# window / the pointer (the rc.xml "*" rule beat Chromium's position), both kiosks ended up
# fullscreen on ONE monitor and the other kept the boot frame for good — the "frozen loading bar"
# of the 2026-10-16 card run (docs/ONE-DESKTOP-2026-10-19.md).
# [R68/F11] --isolate-sandboxed-iframes + per-document grouping: every generated app
# (a sandboxed srcdoc iframe) gets its own renderer process. Measured without it
# (docs/DESKTOP-TECH-EVAL-2026-09.md §8.9): a `while(true){}` in ONE app froze every
# other app on the desktop in every benchmark run — Chromium isolates sandboxed
# iframes from their parent by default since 127, but groups them per origin, so all
# apps of one kiosk shared one process. Per-document costs one renderer per open app;
# the memory price is measured in the next VM run. Also what lets a closed app's
# memory go back at once: its process simply exits (R68 e).
# [fix 2026-10-22] no --incognito: an incognito window ignores the GTK theme and paints its fixed dark grey
# (#3c3c3c) where no page frame exists yet — at the window's first map and after the span resize, the "grey frame"
# of R90. The profile below is wiped before every launch instead (nothing survives a relaunch, as with incognito)
# and follows the GTK theme, whose view colour (gtk.css) is the boot screen's #0a0a14 (docs/FIXES-2026-10-22.md).
CHROME_FLAGS="--noerrdialogs --disable-infobars --no-first-run \
--disable-translate --check-for-update-interval=31536000 \
--overscroll-history-navigation=0 --disable-pinch \
--isolate-sandboxed-iframes --enable-features=IsolateSandboxedIframes:grouping/per-document"
DESK=/opt/vibeos/os/scripts/desktop/kairos-desktop.py

# the monitors (primary first) as the ?mon= hint and the X screen's size; without python3-xlib or
# the script: the connected outputs' geometries from xrandr --query
monitor_hint() {
    python3 "$DESK" monitors --hint 2>/dev/null && return 0
    xrandr --query 2>/dev/null | awk '
        / connected/ && match($0, /[0-9]+x[0-9]+\+[0-9]+\+[0-9]+/) {
            split(substr($0, RSTART, RLENGTH), a, /[x+]/)
            printf "%s%s:%s,%s,%s,%s,%s", (n++ ? ";" : ""), $1, a[3], a[4], a[1], a[2], ($0 ~ / primary /) ? 1 : 0
        }'
}
desktop_size() {
    python3 "$DESK" monitors --union 2>/dev/null && return 0
    xrandr --query 2>/dev/null | awk '/^Screen 0:/ { for (i = 1; i < NF; i++) if ($i == "current") { gsub(",", "", $(i+3)); print $(i+1), $(i+3); exit } }'
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
    MON=$(monitor_hint)
    set -- $(desktop_size)
    W=${1:-0}; H=${2:-0}
    echo "xinitrc: one desktop over ${W}x${H}: $MON"
    # runtime flags: /etc/kairos/chromium.flags (persistent) and /run/kairos/chromium.flags
    # (this boot), flags separated by spaces/newlines, # comments. Read on EVERY launch,
    # so `touch /run/kairos/kiosk-reload` picks up an edit — CHROME_FLAGS above is a
    # variable of this shell and fixed for the life of the X session (VM test 2026-09-11).
    extra=""
    for f in /etc/kairos/chromium.flags /run/kairos/chromium.flags; do
        [ -r "$f" ] && extra="$extra $(sed 's/#.*//' "$f" | tr '\n' ' ')"
    done
    # [R90] no Chromium title bar on the --app window: the profile's "use the system title bar"
    # (openbox draws none for this class). ([R28] the window's own colour before Chromium draws is GTK's
    # window background: ~/.config/gtk-{3,4}.0/gtk.css makes it the boot screen's #0a0a14.)
    # [fix 2026-10-22] a FRESH profile for every launch (the kiosk runs without --incognito: nothing — cookies,
    # cache, history, a crashed session's "restore" state — survives a relaunch) with the GTK system theme
    # (extensions.theme.system_theme 1 = GTK; use_system = the older key): the colour Chromium paints where the
    # page has no frame yet is then GTK's view background, #0a0a14 (gtk.css), not the dark theme's grey.
    rm -rf /tmp/kiosk-0
    mkdir -p /tmp/kiosk-0/Default
    printf '{"browser":{"custom_chrome_frame":false},"extensions":{"theme":{"system_theme":1,"use_system":true}}}\n' \
        > /tmp/kiosk-0/Default/Preferences
    if [ "$W" -gt 0 ] 2>/dev/null && [ "$H" -gt 0 ] 2>/dev/null; then
        chromium $CHROME_FLAGS $extra --user-data-dir=/tmp/kiosk-0 --class=kairos-desktop \
            --window-position=0,0 --window-size="$W,$H" \
            --app="${KIOSK_URL}?mon=${MON}${BOOT_QS}" &
    else                                  # headless / unknown screen: one plain kiosk, the desktop as before
        chromium --kiosk $CHROME_FLAGS $extra --user-data-dir=/tmp/kiosk-0 \
            --app="${KIOSK_URL}?mon=${BOOT_QS}" &
    fi
    KIOSK_PIDS=" $!"
    echo "xinitrc: kiosk pids:$KIOSK_PIDS"
    # the POINTER: the kiosk's windows inherit the root window's cursor (Chromium sets none of
    # its own for the plain arrow), and the root carried the invisible boot cursor "for good" —
    # no pointer anywhere on the desktop (stick run 2026-10-07). With the kiosk up the root gets
    # a real arrow; unclutter hides it after 2 s without movement (R28 stays: nothing shows idle).
    xsetroot -cursor_name left_ptr 2>/dev/null && echo "xinitrc: pointer: left_ptr on the root (unclutter hides it when idle)"
    [ -n "$PTR_DIAG" ] || { PTR_DIAG=1; ( sleep 4; pointer_diag ) & }
    # a relaunch (browser crash, kiosk-reload) is not a boot: the page shows the logo
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
    d=/run/kairos/splash-cover; mkdir -p "$d" 2>/dev/null || return 0
    E=$(cat /run/kairos-splash/expected 2>/dev/null); case "$E" in ''|*[!0-9.]*) E=120 ;; esac
    P=$(cat /run/kairos-splash/progress 2>/dev/null); case "$P" in ''|*[!0-9.]*) P=0.6 ;; esac
    # [R90] the frame on EVERY monitor (rendered once per monitor at its size): the boot page then
    # covers them all, and no monitor ever shows a bare root
    set --; i=0
    for g in $(python3 "$DESK" monitors --rects 2>/dev/null || xrandr --query 2>/dev/null | grep -o ' [0-9]*x[0-9]*+[0-9]*+[0-9]*' | head -n 1); do
        w=${g%%x*}; r=${g#*x}; h=${r%%+*}; r=${r#*+}; x=${r%%+*}; y=${r#*+}
        [ "$w" -gt 0 ] 2>/dev/null || continue
        mkdir -p "$d/sys$i" 2>/dev/null || return 0
        printf '%s,%s\n' "$w" "$h" > "$d/sys$i/virtual_size"; echo 32 > "$d/sys$i/bits_per_pixel"
        echo $((w * 4)) > "$d/sys$i/stride"; echo rootcover > "$d/sys$i/name"
        KAIROS_FB="$d/frame$i.raw" KAIROS_FB_SYS="$d/sys$i" KAIROS_TTY=none KAIROS_SPLASH_LOG=- KAIROS_SPLASH_RUN="$d" KAIROS_BGRT=/nonexistent \
            /usr/local/sbin/kairos-splash --once --no-reexec --phase 2 --progress "$P" --expected "$E" >/dev/null 2>&1 || continue
        set -- "$@" "$d/frame$i.raw" "$w" "$h" "$x" "$y"
        i=$((i + 1))
    done
    [ $# -gt 0 ] || return 0
    python3 /opt/vibeos/os/scripts/splash/root-cover.py "$@" >/dev/null 2>&1 \
        && echo "xinitrc: root cover: the boot screen frame on $i monitor(s) (progress $P)"
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
# [R90] the desktop window's keeper + hot-plug (scripts/desktop/kairos-desktop.py watch): it ends
# with X; restarted if it dies earlier (a python error must not leave the desktop unsized)
( while [ -f "$DESK" ]; do python3 "$DESK" watch; sleep 2; done ) &
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
