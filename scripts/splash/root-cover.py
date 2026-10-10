#!/usr/bin/env python3
"""kAIrOS boot screen [R73/R28] — the painter's picture on the X root window.

X is started with `-background none` so that the framebuffer's last frame (the boot
screen's logo + bar) stays on screen until the kiosk draws. That copy is made by the
driver only with glamor (the laptop: amdgpu DDX + glamor — Xorg.0.log "glamor X
acceleration enabled on AMD Radeon 780M"; the testrig VM: modesetting without glamor
shows black). This makes it explicit and driver-independent: .xinitrc renders the current
frame with `kairos-splash --once` (as the kiosk user, into a file) and this script sets it
as the root window's background pixmap, retained after the client exits (like xsetroot),
so whatever gap there is before the kiosk's first paint shows the boot screen, not black.

usage: root-cover.py <raw BGRX frame> <w> <h> [x y]
Cosmetic: any error exits 0 and leaves the root alone [R63]. python-xlib is in the image
(kernel/wm.py); DISPLAY from the environment (.xinitrc).
"""
import sys

BG = 0x0A0A14          # the wallpaper's top tone (shell.css) around a frame smaller than the root


def main():
    raw, w, h = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
    x = int(sys.argv[4]) if len(sys.argv) > 4 else 0
    y = int(sys.argv[5]) if len(sys.argv) > 5 else 0
    from Xlib import X, display
    with open(raw, "rb") as f:
        data = f.read()
    if w <= 0 or h <= 0 or len(data) < w * h * 4:
        return 1
    d = display.Display()
    d.set_close_down_mode(X.RetainPermanent)          # the pixmap outlives this process
    scr = d.screen()
    root, depth = scr.root, scr.root_depth
    if depth not in (24, 32):
        return 1
    rw, rh = scr.width_in_pixels, scr.height_in_pixels
    pix = root.create_pixmap(rw, rh, depth)
    gc = pix.create_gc(foreground=BG, background=BG)
    pix.fill_rectangle(gc, 0, 0, rw, rh)
    band = max(1, 200000 // (w * 4))                  # rows per request: under the 256 KiB default request limit
    for y0 in range(0, h, band):
        n = min(band, h - y0)
        pix.put_image(gc, x, y + y0, w, n, X.ZPixmap, depth, 0, data[y0 * w * 4:(y0 + n) * w * 4])
    root.change_attributes(background_pixmap=pix)
    root.clear_area(0, 0, 0, 0)                       # repaint the root from its new background
    d.sync()
    return 0


if __name__ == "__main__":
    try:
        rc = main()
    except Exception:
        rc = 1
    sys.exit(0 if rc == 0 else 0)                     # never a failed step in the session [R63]
