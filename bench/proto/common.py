"""Shared bits of the bench drivers: the line protocol, Chromium flags, monitors."""
import os
import subprocess
import sys
import time

KERNEL = os.environ.get("BENCH_KERNEL", "http://127.0.0.1:1337")
CHROME = os.environ.get("BENCH_CHROME", "chromium")
# .xinitrc CHROME_FLAGS, verbatim, plus what the bench needs (popup blocking off for the
# real-window candidates' window.open; extra flags e.g. --ozone-platform=x11 on the host)
XINITRC_FLAGS = ["--noerrdialogs", "--disable-infobars", "--incognito", "--no-first-run",
                 "--disable-translate", "--check-for-update-interval=31536000",
                 "--overscroll-history-navigation=0", "--disable-pinch"]
EXTRA_FLAGS = os.environ.get("BENCH_CHROME_EXTRA", "").split()
# VM: the harness runs as root (X access, /proc of every process) but Chromium refuses
# root — BENCH_CHROME_WRAP="runuser -u kairos --" runs the browser as the kiosk user
CHROME_WRAP = os.environ.get("BENCH_CHROME_WRAP", "").split()


def now_ms():
    return int(time.time() * 1000)


def say(line):
    """one reply = one line: a multi-line error (a JS stack from CDP) must never desync the protocol"""
    sys.stdout.write(" | ".join(str(line).splitlines()) + "\n")
    sys.stdout.flush()


def monitors(disp):
    """[(x, y, w, h)] left-to-right, top-to-bottom: RandR 1.5 monitors (the source of the
    Xinerama heads openbox/qtile consume; python-xlib's xinerama reply lacks the fields)"""
    from Xlib.ext import randr
    try:
        ms = randr.get_monitors(disp.screen().root, True).monitors
        rects = sorted((m.x, m.y, m.width_in_pixels, m.height_in_pixels) for m in ms)
    except Exception:
        rects = []
    if not rects:
        g = disp.screen().root.get_geometry()
        rects = [(0, 0, g.width, g.height)]
    return rects


def screen_of(rects, x, y):
    for i, (rx, ry, rw, rh) in enumerate(rects):
        if rx <= x < rx + rw and ry <= y < ry + rh:
            return i
    return 0


def chrome_env():
    env = dict(os.environ)
    env.pop("WAYLAND_DISPLAY", None)      # the X11 candidates must not pick the host's Wayland session
    return env


def spawn(cmd, log=None, env=None):
    return subprocess.Popen(cmd, stdout=log, stderr=log, env=env or chrome_env())
