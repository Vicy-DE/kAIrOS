# Autostart the kiosk on tty1 login (kAIrOS desktop on the laptop screen).
if [ -z "$DISPLAY" ] && [ "$(tty)" = "/dev/tty1" ]; then
    # [R51] X used to be invisible: startx/Xorg/.xinitrc output went to tty1 and
    # was lost, and a dying X made agetty autologin -> startx -> die forever (a
    # silent black screen). Now every attempt is logged to /var/log/kairos/x.log
    # (collected by kairos-bootlog) and counted per boot in /run/kairos (tmpfiles.d).
    # Retries are PACED (D8): getty@tty1 re-runs this shell the moment startx
    # exits, and five instant failures used to hit getty's start-rate limit before
    # the 6th attempt below could flag the loop — 3 s between attempts 2-5, then
    # the loop is flagged (kairos-bootlog-xfail.path writes a log to the stick)
    # and retried every 30 s: a driver may still come up, never a hot loop. The
    # sleeps here and getty@tty1.service.d/kairos-xretry.conf belong together.
    XLOG=/var/log/kairos/x.log; [ -w /var/log/kairos ] || XLOG=/tmp/kairos-x.log
    XCNT=/run/kairos/x-attempts; [ -w /run/kairos ] || XCNT=/tmp/kairos-x-attempts
    n=$(cat "$XCNT" 2>/dev/null || echo 0); n=$((n + 1)); echo "$n" > "$XCNT" 2>/dev/null
    if [ "$n" -eq 1 ]; then
        # one log per boot: the previous boot's is kept as x.log.1, so the boot
        # log's X section can never show an older boot's output (D1)
        [ -f "$XLOG" ] && mv -f "$XLOG" "$XLOG.1" 2>/dev/null
    elif [ "$n" -le 5 ]; then
        sleep 3
    else
        # the loop is flagged at the 6th attempt and again every 30th after it (~15 min
        # apart at this pacing), so an X that keeps failing for hours keeps refreshing
        # the stick's log instead of leaving the one capture from minute 1 (N3); the
        # per-trigger cap in kairos-bootlog still bounds the captures per boot
        [ "$n" -eq 6 ] && touch /run/kairos/x-failed 2>/dev/null
        [ "$n" -gt 6 ] && [ $((n % 30)) -eq 0 ] && touch /run/kairos/x-failed 2>/dev/null
        sleep 30
    fi
    [ "$(stat -c %s "$XLOG" 2>/dev/null || echo 0)" -gt 1000000 ] && : > "$XLOG"   # bounded
    # the boot screen [R73/R28]: the framebuffer painter (scripts/splash/, started in the
    # initramfs, root) keeps drawing the logo + loading bar until the kiosk is up. Tell it
    # X is starting (its bar jumps to the X milestone; the flag lives in /run/kairos so
    # this user can write it), keep tty1's cursor off. tty1 is never cleared or written:
    # X starts with -background none and keeps the painter's last frame on screen until
    # the kiosk's boot page draws — no console, cursor or black flash between the two.
    # The painter is ended by .xinitrc once the kiosk is launched (splash-handover).
    printf '\033[?25l' > /dev/tty1 2>/dev/null
    touch /run/kairos/splash-x 2>/dev/null
    # X is pinned to :0 (D5): a restarted kiosk took the next free display (:1)
    # and orphaned everything that talks to :0 (kernel/hw.py display API, the
    # testrig's x11vnc). The lock of an X that was killed (pid gone) is cleared
    # first, or :0 would refuse to start until reboot.
    if [ -f /tmp/.X0-lock ] && ! kill -0 "$(tr -dc 0-9 < /tmp/.X0-lock 2>/dev/null)" 2>/dev/null; then
        rm -f /tmp/.X0-lock /tmp/.X11-unix/X0 2>/dev/null
    fi
    # NO -nocursor [R28/R51, stick run 2026-10-07 v7.3]: it does not hide the cursor "until a
    # client defines one" — xorg-server 21.1 ANDs EVERY cursor display with it (xfixes/cursor.c
    # CursorDisplayCursor: CursorVisible = CursorVisible && EnableCursor), so the laptop never drew
    # a pointer at all, while XFixes/x11grab (the VM "proofs") still reported the cursor image.
    # X's default already shows no cursor until the first XDefineCursor; .xinitrc keeps the root's
    # cursor invisible until the kiosk is up, then sets the arrow (unclutter hides it when idle).
    {
        printf '===== X attempt %s  %s  uptime %ss =====\n' "$n" "$(date)" "$(cut -d' ' -f1 /proc/uptime)"
        startx -- :0 -background none
        printf '===== X attempt %s exited rc=%s  %s =====\n' "$n" "$?" "$(date)"
    } >> "$XLOG" 2>&1
    exit
fi
