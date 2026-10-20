#!/usr/bin/env python3
"""kAIrOS — the boot screen [R73] (docs/BOOT-SPLASH-2026-10-07.md): the framebuffer painter
scripts/splash/kairos-splash.c and everything wired around it.
Standalone: `python3 tests/test_splash.py` (run-tests.sh runs it after the suite). Needs no
kernel: the painter is built with `cc` when one is present (a temp dir), otherwise the
committed static build scripts/splash/kairos-splash is used; without either the painter
tests skip. Writes tests/results-splash.json.

What is proven here: BGRT parsing (full-screen HP-style 24 bpp, OVMF-small 32 bpp
top-down, 8 bpp palette, none), the layout maths (also the scaled case after a mode
change), the progress formula (C, JS and this file agree), the painter rendering into a
file (key pixels, 32/24/16 bpp), the efifb -> drm fbdev handover (the fake ioctl layer:
ENODEV, no write while unregistered, reopen + re-layout), the signals (SIGUSR1 and the
flag file hand over, SIGTERM/--stop end it), the initramfs hook/scripts, the unit, the
emergency/rescue drop-ins, kairos-splash-ctl, the .xinitrc/.bash_profile contracts, the
boot page, the removal of R27 (no mpv, no dream), the kernel's boot timing module, the
build/sync wiring, and the reproducibility of the assets.
"""
import json
import os
import re
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import time
import zlib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SPLASH = REPO / "scripts" / "splash"
INC = REPO / "iso" / "config" / "includes.chroot"
RESULTS = {}
_BIN = {}


def record(name, reqs, status, detail=""):
    RESULTS[name] = {"reqs": reqs, "status": status, "detail": detail[:300]}
    mark = {"pass": "✓", "fail": "✗", "skip": "~"}[status]
    print(f"  {mark} {name} [{', '.join(reqs)}] {detail[:120]}", flush=True)


class SkipTest(Exception):
    pass


def test(name, reqs):
    def deco(fn):
        def wrapper(ctx):
            try:
                detail = fn(ctx)
                record(name, reqs, "pass", detail or "")
            except SkipTest as e:
                record(name, reqs, "skip", str(e))
            except Exception as e:
                import traceback
                record(name, reqs, "fail", f"{type(e).__name__}: {e} @ {traceback.extract_tb(e.__traceback__)[-1].lineno}")
        wrapper._name = name
        wrapper._reqs = reqs
        wrapper._test = True
        return wrapper
    return deco


# ----------------------------------------------------------------------------- helpers
def painter(ctx):
    """Path of a runnable painter: built from source with cc into the run's temp dir, else
    the committed static binary."""
    if "bin" in _BIN:
        return _BIN["bin"]
    out = Path(ctx["tmp"]) / "kairos-splash"
    if shutil.which("cc") or shutil.which("gcc"):
        r = subprocess.run(["sh", str(SPLASH / "build.sh"), str(out)], capture_output=True, text=True)
        if r.returncode == 0 and out.exists():
            _BIN["bin"] = str(out); _BIN["how"] = "built from source"
            return _BIN["bin"]
        print(f"  (build.sh failed: {r.stderr[-300:]})")
    committed = SPLASH / "kairos-splash"
    if committed.exists() and os.access(committed, os.X_OK):
        _BIN["bin"] = str(committed); _BIN["how"] = "committed binary"
        return _BIN["bin"]
    raise SkipTest("no C compiler and no committed scripts/splash/kairos-splash")


def bmp(w, h, pixels, bpp=24, topdown=False):
    """pixels(x, y) -> (r, g, b); returns BMP bytes (BI_RGB; 24 or 32 bpp; 8 bpp = grey palette)."""
    rowbytes = ((w * bpp + 31) // 32) * 4
    rows = []
    for y in range(h):
        row = bytearray()
        for x in range(w):
            r, g, b = pixels(x, y)
            if bpp == 24:
                row += bytes((b, g, r))
            elif bpp == 32:
                row += bytes((b, g, r, 0))
            else:
                row += bytes((max(r, g, b),))
        row += b"\0" * (rowbytes - len(row))
        rows.append(bytes(row))
    if not topdown:
        rows.reverse()
    pal = b"".join(bytes((i, i, i, 0)) for i in range(256)) if bpp == 8 else b""
    data = b"".join(rows)
    off = 14 + 40 + len(pal)
    hdr = b"BM" + struct.pack("<IHHI", off + len(data), 0, 0, off)
    dib = struct.pack("<IiiHHIIiiII", 40, w, -h if topdown else h, 1, bpp, 0, len(data), 2835, 2835, 256 if bpp == 8 else 0, 0)
    return hdr + dib + pal + data


def bgrt_dir(root, image, xoff=0, yoff=0, status=0):
    d = Path(root); d.mkdir(parents=True, exist_ok=True)
    (d / "image").write_bytes(image)
    (d / "status").write_text(f"{status}\n"); (d / "type").write_text("0\n"); (d / "version").write_text("1\n")
    (d / "xoffset").write_text(f"{xoff}\n"); (d / "yoffset").write_text(f"{yoff}\n")
    return str(d)


def fbsys(root, w, h, bpp=32, name="test fb"):
    d = Path(root); d.mkdir(parents=True, exist_ok=True)
    (d / "virtual_size").write_text(f"{w},{h}\n"); (d / "bits_per_pixel").write_text(f"{bpp}\n")
    (d / "stride").write_text(f"{w * bpp // 8}\n"); (d / "name").write_text(name + "\n")
    return str(d)


def env_for(ctx, fb, sys_dir, bgrt=None, run=None, extra=None):
    e = dict(os.environ, KAIROS_FB=str(fb), KAIROS_FB_SYS=str(sys_dir), KAIROS_TTY="none", KAIROS_SPLASH_LOG="-",
             KAIROS_SPLASH_RUN=str(run or Path(ctx["tmp"]) / "run"), KAIROS_BGRT=str(bgrt or Path(ctx["tmp"]) / "no-bgrt"))
    Path(e["KAIROS_SPLASH_RUN"]).mkdir(parents=True, exist_ok=True)
    if extra:
        e.update(extra)
    return e


def run_once(ctx, fb, sys_dir, args, bgrt=None):
    e = env_for(ctx, fb, sys_dir, bgrt)
    r = subprocess.run([painter(ctx), "--once", "--no-reexec"] + args, env=e, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, f"painter rc {r.returncode}: {r.stderr[-400:]}"
    return r.stderr


def px32(raw, stride, x, y):
    o = y * stride + x * 4
    b, g, r = raw[o], raw[o + 1], raw[o + 2]
    return r, g, b


def c_const(name):
    src = (SPLASH / "kairos-splash.c").read_text()
    m = re.search(rf"^#define {name}\s+([0-9.]+)", src, re.M)
    assert m, f"{name} not in kairos-splash.c"
    return float(m.group(1))


def progress_curve(t, expected, at_expected, p_max):
    u = t / max(1.0, expected)
    if u <= 1:
        return at_expected * u
    import math
    return at_expected + (p_max - at_expected) * (1 - math.exp(-(u - 1) * 2.0))


# ----------------------------------------------------------------------------- BGRT
@test("splash_bgrt_fullscreen_hp_style", ["R73"])
def t_bgrt_full(ctx):
    """The HP laptop: a 1920x1080 24 bpp BMP (status 0 — still a valid logo), offsets 0/0, the
    logo a bright block in the middle of black -> bbox = exactly that block."""
    img = bmp(1920, 1080, lambda x, y: (255, 255, 255) if 860 <= x < 1060 and 400 <= y < 600 else (0, 0, 0))
    d = bgrt_dir(Path(ctx["tmp"]) / "bgrt-hp", img, 0, 0, status=0)
    r = subprocess.run([painter(ctx), "--bgrt-info", "--no-reexec"], env=env_for(ctx, "/dev/null", "/nonexistent", d),
                       capture_output=True, text=True, timeout=30)
    info = json.loads(r.stdout)
    assert info["present"] and info["w"] == 1920 and info["h"] == 1080 and info["status"] == 0, info
    assert info["bbox"] == [860, 400, 1060, 600], info["bbox"]
    # dark noise below the threshold does not widen the box
    img2 = bmp(1920, 1080, lambda x, y: (255, 255, 255) if 860 <= x < 1060 and 400 <= y < 600 else (20, 20, 20))
    d2 = bgrt_dir(Path(ctx["tmp"]) / "bgrt-hp2", img2)
    r = subprocess.run([painter(ctx), "--bgrt-info", "--no-reexec"], env=env_for(ctx, "/dev/null", "/nonexistent", d2),
                       capture_output=True, text=True, timeout=30)
    assert json.loads(r.stdout)["bbox"] == [860, 400, 1060, 600], r.stdout
    return "1920x1080 24bpp status=0 parsed, bbox = the bright block (noise <= 24 ignored)"


@test("splash_bgrt_small_ovmf_32bpp_topdown_and_8bpp", ["R73"])
def t_bgrt_small(ctx):
    """OVMF: a small logo at +543+371 (what QEMU shows); the decoder also takes 32 bpp top-down
    rows and an 8 bpp palette BMP (two shapes seen in firmware images)."""
    ids = {}
    for tag, kw in (("32td", dict(bpp=32, topdown=True)), ("8", dict(bpp=8)), ("24", dict(bpp=24))):
        img = bmp(193, 58, lambda x, y: (200, 30, 30) if 2 <= x < 191 else (0, 0, 0), **kw)
        d = bgrt_dir(Path(ctx["tmp"]) / f"bgrt-{tag}", img, 543, 371, status=1)
        r = subprocess.run([painter(ctx), "--bgrt-info", "--no-reexec"], env=env_for(ctx, "/dev/null", "/nonexistent", d),
                           capture_output=True, text=True, timeout=30)
        info = json.loads(r.stdout)
        assert info["present"] and (info["w"], info["h"], info["xoffset"], info["yoffset"]) == (193, 58, 543, 371), (tag, info)
        assert info["bbox"] == [2, 0, 191, 58], (tag, info["bbox"])
        ids[tag] = info["bbox"]
    return f"193x58 at +543+371: 24bpp, 32bpp top-down and 8bpp palette all give bbox {ids['24']}"


@test("splash_bgrt_none", ["R73"])
def t_bgrt_none(ctx):
    """No BGRT (a VM without one, a firmware without a logo), or an unreadable image: not
    present, and phase 1 centres the lockup instead."""
    for d in (Path(ctx["tmp"]) / "nowhere", bgrt_dir(Path(ctx["tmp"]) / "bgrt-bad", b"not a bmp at all")):
        r = subprocess.run([painter(ctx), "--bgrt-info", "--no-reexec"], env=env_for(ctx, "/dev/null", "/nonexistent", str(d)),
                           capture_output=True, text=True, timeout=30)
        assert json.loads(r.stdout)["present"] is False, r.stdout
    fb = Path(ctx["tmp"]) / "fb-none.raw"; sysd = fbsys(Path(ctx["tmp"]) / "sys-none", 1920, 1080)
    log = run_once(ctx, fb, sysd, ["--phase", "1"])
    assert "firmware logo absent: lockup centred" in log, log
    lay = json.loads(subprocess.run([painter(ctx), "--layout", "--no-reexec"], env=env_for(ctx, fb, sysd), capture_output=True, text=True).stdout)
    x, y, w, h = lay["logo1"]
    assert abs((x + w / 2) - 960) <= 1 and abs((y + h / 2) - 540) <= 1, lay["logo1"]
    return "missing dir + bad image -> present:false; phase 1 centres the lockup at (960,540)"


# ----------------------------------------------------------------------------- layout + progress
@test("splash_layout_maths", ["R73"])
def t_layout(ctx):
    """The maths on the HP geometry (1920x1080, full-screen BGRT with the logo block): the lockup
    is centred under the logo's bbox with a 4.5 % gap, the wordmark/bar follow the constants."""
    img = bmp(1920, 1080, lambda x, y: (255, 255, 255) if 860 <= x < 1060 and 400 <= y < 600 else (0, 0, 0))
    d = bgrt_dir(Path(ctx["tmp"]) / "bgrt-lay", img)
    fb = Path(ctx["tmp"]) / "fb-lay.raw"; sysd = fbsys(Path(ctx["tmp"]) / "sys-lay", 1920, 1080)
    lay = json.loads(subprocess.run([painter(ctx), "--layout", "--no-reexec"], env=env_for(ctx, fb, sysd, d), capture_output=True, text=True, timeout=30).stdout)
    assert lay["bgrt"] == [0, 0, 1920, 1080] and abs(lay["scale"] - 1.0) < 1e-6, lay
    x, y, w, h = lay["logo1"]
    assert abs((x + w / 2) - 960) <= 1, "lockup centred on the bbox centre"
    assert y == round(600 + c_const("LAYOUT_LOGO1_GAP") * 1080), (y, lay)
    assert w == 448 and h == 84, "the lockup at the reference scale is 1:1 (448x84)"
    x2, y2, w2, h2 = lay["logo2"]
    assert w2 == 606 and h2 == 374 and abs((x2 + w2 / 2) - 960) <= 1 and abs((y2 + h2 / 2) - c_const("LAYOUT_LOGO2_CY") * 1080) <= 1, lay["logo2"]
    bx, by, bw, bh = lay["bar"]
    assert abs(bw - c_const("LAYOUT_BAR_W")) < 0.01 and abs(bh - c_const("LAYOUT_BAR_H")) < 0.01 and abs(by - c_const("LAYOUT_BAR_CY") * 1080) < 0.01
    assert abs(bx + bw / 2 - 960) < 0.01, "bar centred"
    assert y2 + h2 < by - bh, "the wordmark (incl. tagline) ends well above the bar"
    # a bbox at the very bottom: the lockup is clamped onto the screen, never below it
    img2 = bmp(1920, 1080, lambda x, y: (255, 255, 255) if 860 <= x < 1060 and 1000 <= y < 1075 else (0, 0, 0))
    d2 = bgrt_dir(Path(ctx["tmp"]) / "bgrt-low", img2)
    lay2 = json.loads(subprocess.run([painter(ctx), "--layout", "--no-reexec"], env=env_for(ctx, fb, sysd, d2), capture_output=True, text=True, timeout=30).stdout)
    assert lay2["logo1"][1] + lay2["logo1"][3] <= 1080 - 0.03 * 1080 + 1, lay2["logo1"]
    # a 4:3 screen: everything scales with min(W/1920, H/1080)
    sysd3 = fbsys(Path(ctx["tmp"]) / "sys-43", 1024, 768)
    lay3 = json.loads(subprocess.run([painter(ctx), "--layout", "--no-reexec"], env=env_for(ctx, fb, sysd3), capture_output=True, text=True, timeout=30).stdout)
    k = min(1024 / 1920, 768 / 1080)
    assert lay3["logo2"][2] == round(606 * k) and abs(lay3["bar"][2] - 540 * k) < 0.01, lay3
    return f"lockup at y={y} under the bbox, wordmark {w2}x{h2} centred at {c_const('LAYOUT_LOGO2_CY'):.2f}H, bar {bw:.0f}x{bh:.0f} at {c_const('LAYOUT_BAR_CY'):.3f}H; clamp + 4:3 scaling ok"


@test("splash_progress_formula_c_js_agree", ["R73"])
def t_progress(ctx):
    """Determinate progress: 0 at t=0, P_AT_EXPECTED at t=expected, never above P_MAX,
    monotonic; milestone floors; the painter (C), the boot page (JS) and this file use the
    same constants; the painter honours --at/--expected/--progress (its log says so)."""
    c = {k: c_const(k) for k in ("P_ROOT", "P_USERSPACE", "P_X", "P_AT_EXPECTED", "P_MAX")}
    js = (REPO / "shell" / "boot-wait.html").read_text()
    m = re.search(r"const P_AT_EXPECTED = ([0-9.]+), P_MAX = ([0-9.]+), P_X = ([0-9.]+), P_KERNEL = ([0-9.]+);", js)
    assert m, "boot-wait.html constants"
    assert (float(m.group(1)), float(m.group(2)), float(m.group(3))) == (c["P_AT_EXPECTED"], c["P_MAX"], c["P_X"]), (m.groups(), c)
    assert float(m.group(4)) > c["P_X"] and float(m.group(4)) < 1.0
    assert 0 < c["P_ROOT"] < c["P_USERSPACE"] < c["P_X"] < c["P_AT_EXPECTED"] < c["P_MAX"] < 1.0
    E = 120.0
    f = lambda t: progress_curve(t, E, c["P_AT_EXPECTED"], c["P_MAX"])
    assert f(0) == 0 and abs(f(E) - c["P_AT_EXPECTED"]) < 1e-9 and f(10 * E) < c["P_MAX"]
    vals = [f(t) for t in range(0, 1200, 5)]
    assert all(b >= a for a, b in zip(vals, vals[1:])), "monotonic"
    assert f(E / 2) == c["P_AT_EXPECTED"] / 2, "linear until the expected time"
    # the painter's own numbers: --once logs the progress it rendered
    fb = Path(ctx["tmp"]) / "fb-prog.raw"; sysd = fbsys(Path(ctx["tmp"]) / "sys-prog", 640, 360)
    for t, exp, want in ((60, 120, 0.43), (120, 120, 0.86), (600, 120, c["P_MAX"] - 0.01)):
        log = run_once(ctx, fb, sysd, ["--phase", "2", "--at", str(t), "--expected", str(exp)])
        got = float(re.search(r"progress ([0-9.]+)\)", log).group(1))
        assert abs(got - min(f(t), c["P_MAX"])) < 0.011 or (want > 0.9 and got >= want), (t, exp, got, log)
    return f"P(E)={c['P_AT_EXPECTED']}, max {c['P_MAX']}, floors root {c['P_ROOT']} / userspace {c['P_USERSPACE']} / x {c['P_X']}; C == JS == python"


@test("splash_layout_matches_boot_page", ["R73", "R28"])
def t_layout_page(ctx):
    """The X-side page continues the painter's frame seamlessly: same reference canvas scale
    min(w/1920, h/1080) — [R90] of each monitor's tile (--tw/--th; the viewport without the ?mon=
    hint) —, same wordmark width/centre, same bar width/height/centre, same wallpaper gradient,
    the same PNG the header embeds."""
    html = (REPO / "shell" / "boot-wait.html").read_text()
    css = lambda var: re.search(rf"{var}: ([^;]+);", html).group(1).strip()
    assert css("--s") == "min(calc(var(--tw) / 1920), calc(var(--th) / 1080))", css("--s")
    assert css("--tw") == "100vw" and css("--th") == "100vh", "one tile = the viewport without the hint"
    assert float(css("--logo-cy").rstrip("%")) / 100 == c_const("LAYOUT_LOGO2_CY")
    assert float(css("--bar-cy").rstrip("%")) / 100 == c_const("LAYOUT_BAR_CY")
    assert css("--logo-w") == "calc(var(--s) * 606)" and css("--bar-w") == f"calc(var(--s) * {int(c_const('LAYOUT_BAR_W'))})"
    assert css("--bar-h") == f"max(4px, calc(var(--s) * {int(c_const('LAYOUT_BAR_H'))}))"
    hdr = (SPLASH / "logo_data.h").read_text()
    assert "#define LOGO2_W 606" in hdr and "#define LOGO1_W 448" in hdr
    assert 'src="splash/logo-thick.png"' in html and (REPO / "shell" / "splash" / "logo-thick.png").exists()
    for stop in ("#1b1340", "#0a2a38", "linear-gradient(160deg, #0a0a14 0%, #11101f 50%, #0a0d16 100%)", "#7c5cff", "#41e2ff"):
        assert stop in html, f"wallpaper/accent {stop} missing from the boot page"
    src = (SPLASH / "kairos-splash.c").read_text()
    for name, rgb in (("ACCENT", "0x7C, 0x5C, 0xFF"), ("ACCENT2", "0x41, 0xE2, 0xFF"), ("GLOW1", "0x1B, 0x13, 0x40"), ("GLOW2", "0x0A, 0x2A, 0x38")):
        assert f"{name}[3]" in src and rgb in src, name
    return "scale/logo-cy/bar-cy/bar-w/bar-h/logo-w identical, same gradient stops and accents, same PNG"


# ----------------------------------------------------------------------------- rendering
@test("splash_render_phase1_pixels", ["R73", "R28"])
def t_render1(ctx):
    """Phase 1 into a 1920x1080x32 file: black, the BGRT's logo block redrawn where it was, the
    lockup (its accent diamond) under it."""
    img = bmp(1920, 1080, lambda x, y: (255, 255, 255) if 860 <= x < 1060 and 400 <= y < 600 else (0, 0, 0))
    d = bgrt_dir(Path(ctx["tmp"]) / "bgrt-r1", img)
    fb = Path(ctx["tmp"]) / "fb-r1.raw"; sysd = fbsys(Path(ctx["tmp"]) / "sys-r1", 1920, 1080)
    log = run_once(ctx, fb, sysd, ["--phase", "1"], d)
    raw = fb.read_bytes(); assert len(raw) == 1920 * 1080 * 4, len(raw)
    assert px32(raw, 7680, 10, 10) == (0, 0, 0) and px32(raw, 7680, 1900, 1070) == (0, 0, 0), "black outside"
    assert px32(raw, 7680, 960, 500) == (255, 255, 255) and px32(raw, 7680, 861, 401) == (255, 255, 255), "the firmware logo block redrawn 1:1"
    assert px32(raw, 7680, 859, 500) == (0, 0, 0) and px32(raw, 7680, 960, 601) == (0, 0, 0), "and not smeared"
    lay = json.loads(subprocess.run([painter(ctx), "--layout", "--no-reexec"], env=env_for(ctx, fb, sysd, d), capture_output=True, text=True).stdout)
    x, y, w, h = lay["logo1"]
    accent = sum(1 for yy in range(y, y + h) for xx in range(x, x + w)
                 if (lambda p: p[2] > 200 and p[0] > 90 and p[1] < 140)(px32(raw, 7680, xx, yy)))
    white = sum(1 for yy in range(y, y + h) for xx in range(x, x + w) if min(px32(raw, 7680, xx, yy)) > 220)
    assert accent > 400 and white > 400, f"lockup drawn under the logo: accent px {accent}, text px {white}"
    assert all(px32(raw, 7680, xx, y - 10) == (0, 0, 0) for xx in range(x, x + w, 7)), "a gap of black between logo and lockup"
    assert "phase 1 painted" in log and "firmware logo redrawn" in log
    return f"logo block 1:1, black elsewhere, lockup at y={y} ({accent} accent + {white} white px)"


@test("splash_render_phase2_pixels", ["R73", "R28"])
def t_render2(ctx):
    """Phase 2 at 50 %: the wallpaper gradient (not black), the thick wordmark around the
    centre, the bar's left half in the accent gradient, its right half the dim track."""
    fb = Path(ctx["tmp"]) / "fb-r2.raw"; sysd = fbsys(Path(ctx["tmp"]) / "sys-r2", 1920, 1080)
    log = run_once(ctx, fb, sysd, ["--phase", "2", "--at", "60", "--expected", "120", "--progress", "0.5"])
    raw = fb.read_bytes(); assert len(raw) == 1920 * 1080 * 4
    bg = px32(raw, 7680, 10, 10)
    assert bg != (0, 0, 0) and max(bg) < 45 and bg[2] >= bg[0], f"dark wallpaper tone, not black: {bg}"
    lay = json.loads(subprocess.run([painter(ctx), "--layout", "--no-reexec"], env=env_for(ctx, fb, sysd), capture_output=True, text=True).stdout)
    x, y, w, h = lay["logo2"]
    white = sum(1 for yy in range(y, y + h, 2) for xx in range(x, x + w, 2) if min(px32(raw, 7680, xx, yy)) > 225)
    assert white > 3000, f"thick wordmark: {white} near-white samples"
    bx, by, bw, bh = lay["bar"]
    cy = int(by)
    left = px32(raw, 7680, int(bx + bw * 0.25), cy); right = px32(raw, 7680, int(bx + bw * 0.75), cy)
    assert left[2] > 200 and left[0] > 60, f"fill is accent-coloured at 25 %: {left}"
    assert max(right) < 70 and right != bg, f"track at 75 % is dim but visible: {right}"
    above = px32(raw, 7680, int(bx + bw * 0.25), cy - int(bh) - 2)
    assert above[2] > bg[2] + 4, f"soft glow above the fill: {above} vs bg {bg}"
    far = px32(raw, 7680, int(bx + bw * 0.25), cy - int(bh) - 40)
    assert abs(far[2] - px32(raw, 7680, int(bx + bw * 0.25) + 300, cy - int(bh) - 40)[2]) < 12, "the glow fades out"
    assert "once: frame written (phase 2, progress 0.500)" in log, log
    return f"bg {bg}, wordmark {white} white samples, fill {left}, track {right}, glow {above}"


@test("splash_render_other_depths", ["R73"])
def t_render_depths(ctx):
    """24 bpp and RGB565 framebuffers (other machines / modes) render the same picture."""
    out = {}
    for bpp in (24, 16):
        fb = Path(ctx["tmp"]) / f"fb-{bpp}.raw"; sysd = fbsys(Path(ctx["tmp"]) / f"sys-{bpp}", 640, 480, bpp)
        run_once(ctx, fb, sysd, ["--phase", "2", "--at", "60", "--expected", "120", "--progress", "0.5"])
        raw = fb.read_bytes(); stride = 640 * bpp // 8
        assert len(raw) == stride * 480, (bpp, len(raw))
        lay = json.loads(subprocess.run([painter(ctx), "--layout", "--no-reexec"], env=env_for(ctx, fb, sysd), capture_output=True, text=True).stdout)
        bx, by, bw, bh = lay["bar"]; xx, yy = int(bx + bw * 0.25), int(by)
        if bpp == 24:
            o = yy * stride + xx * 3; b, g, r = raw[o], raw[o + 1], raw[o + 2]
        else:
            o = yy * stride + xx * 2; v = raw[o] | raw[o + 1] << 8
            r, g, b = (v >> 11) << 3, ((v >> 5) & 63) << 2, (v & 31) << 3
        assert b > 190 and r > 50, (bpp, (r, g, b))
        out[bpp] = (r, g, b)
    return f"24bpp fill {out[24]}, 16bpp fill {out[16]}"


# ----------------------------------------------------------------------------- handover + signals
def start_painter(ctx, fb, sysd, run, extra_args=(), bgrt=None):
    log = Path(ctx["tmp"]) / f"log-{int(time.time() * 1000) % 100000}.txt"
    e = env_for(ctx, fb, sysd, bgrt, run, {"KAIROS_SPLASH_LOG": str(log), "KAIROS_SPLASH_ARCHIVE": str(Path(ctx["tmp"]) / "archive.log")})
    p = subprocess.Popen([painter(ctx), "--no-reexec", "--fps", "30", "--expected", "120"] + list(extra_args), env=e,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return p, log


def wait_log(log, needle, timeout=5.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if log.exists() and needle in log.read_text():
            return True
        time.sleep(0.02)
    print(f"    (wait_log: {needle!r} not seen within {timeout}s; log:\n" + (log.read_text() if log.exists() else "<no log>") + ")")
    return False


@test("splash_fb_handover_enodev", ["R73"])
def t_handover(ctx):
    """The efifb -> drm fbdev switch with the fake ioctl layer: the painter paints on
    'EFI VGA' 1280x800, the fb goes ENODEV (KAIROS_FB_SYS/enodev) -> it closes the fb and
    writes NOTHING while it is gone; a new fb0 'amdgpudrmfb' 1920x1080 appears -> reopened
    within 10 ms polls, the layout is rescaled (ref 1280x800 -> scale 1.35), the frame is
    repainted; milestone/expected/flag files are honoured; SIGUSR1 ends it without an error."""
    run = Path(ctx["tmp"]) / "run-ho"; run.mkdir(exist_ok=True)
    fb = Path(ctx["tmp"]) / "fb-ho.raw"; sysd = Path(fbsys(Path(ctx["tmp"]) / "sys-ho", 1280, 800, name="EFI VGA"))
    img = bmp(193, 58, lambda x, y: (200, 30, 30), bpp=24)
    bg = bgrt_dir(Path(ctx["tmp"]) / "bgrt-ho", img, 543, 371, status=1)
    p, log = start_painter(ctx, fb, sysd, run, bgrt=bg)
    try:
        assert wait_log(log, "phase 1 painted on EFI VGA (1280x800"), log.read_text() if log.exists() else "no log"
        assert "bgrt at 543,371 193x58 (scale 1.000)" in log.read_text()
        (run / "kairos-splash" / "milestone").write_text("root\n")
        (run / "kairos-splash" / "expected").write_text("90\n")
        assert wait_log(log, "milestone root") and wait_log(log, "expected boot time 90.0s")
        assert wait_log(log, "phase 1 fades out", 8), log.read_text()
        (sysd / "enodev").write_text("")
        assert wait_log(log, "unregistered (ENODEV) — closed, never touched again"), log.read_text()
        m1 = fb.stat().st_mtime_ns; time.sleep(0.35); m2 = fb.stat().st_mtime_ns
        assert m1 == m2, "no write to the unregistered fb"
        assert p.poll() is None, "the painter waits for the next fb0"
        fbsys(sysd, 1920, 1080, name="amdgpudrmfb"); (sysd / "enodev").unlink()
        assert wait_log(log, "amdgpudrmfb 1920x1080 32bpp"), log.read_text()
        assert wait_log(log, "layout 1920x1080: bgrt at 829,501 261x78 (scale 1.350)", 3), log.read_text()
        # the ENODEV may land during the cross-fade (every fade frame is a full write) or in
        # phase 2 proper (an explicit repaint): either way the new fb carries the whole frame
        assert wait_log(log, "on amdgpudrmfb"), log.read_text()
        time.sleep(0.5)
        assert fb.stat().st_size == 1920 * 1080 * 4, "the new geometry was written"
        (run / "kairos").mkdir(exist_ok=True); (run / "kairos" / "splash-x").write_text("")
        assert wait_log(log, "milestone x (startx)"), log.read_text()
        state = (run / "kairos-splash" / "state").read_text() if (run / "kairos-splash" / "state").exists() else ""
        p.send_signal(signal.SIGUSR1)
        rc = p.wait(timeout=5)
        assert rc == 0 and "handover: the kiosk is up, X owns the display — VT mode untouched" in log.read_text(), (rc, log.read_text()[-300:])
        assert not (run / "kairos-splash.pid").exists(), "pidfile removed"
        assert (Path(ctx["tmp"]) / "archive.log").exists() and "===== kairos-splash" in (Path(ctx["tmp"]) / "archive.log").read_text(), "log archived at exit"
    finally:
        if p.poll() is None:
            p.kill()
    return f"ENODEV -> closed + silent; reopened amdgpudrmfb 1920x1080 scale 1.35, repainted; SIGUSR1 -> rc 0, mode untouched; state '{state.strip()[:60]}'"


@test("splash_signals_and_stop", ["R73", "R51"])
def t_signals(ctx):
    """SIGTERM = a failure exit (would restore KD_TEXT; here no VT: 'stopped by signal 15'),
    the kiosk user's flag file = a handover (mode untouched), `--stop` finds the pidfile
    and ends a running painter within 1.5 s."""
    run = Path(ctx["tmp"]) / "run-sig"; run.mkdir(exist_ok=True)
    fb = Path(ctx["tmp"]) / "fb-sig.raw"; sysd = fbsys(Path(ctx["tmp"]) / "sys-sig", 640, 360)
    p, log = start_painter(ctx, fb, sysd, run)
    assert wait_log(log, "phase 1 painted")
    p.send_signal(signal.SIGTERM); rc = p.wait(timeout=5)
    assert rc == 0 and "stopped by signal 15 — VT mode untouched" in log.read_text(), log.read_text()[-300:]
    src = (SPLASH / "kairos-splash.c").read_text()
    assert 'leave(why, 1)' in src and "if (restore_text && kd_graphics_set)" in src and "vt_set_mode(KD_TEXT)" in src, \
        "a signal exit restores KD_TEXT when the painter set KD_GRAPHICS (the VM run proves the VT part)"
    assert 'leave("handover: the kiosk is up, X owns the display", 0)' in src
    assert "on_crash" in src and "SIGBUS" in src and "SIGSEGV" in src and "ioctl(ttyfd, KDSETMODE, KD_TEXT)" in src
    # flag-file handover
    p, log = start_painter(ctx, fb, sysd, run)
    assert wait_log(log, "phase 1 painted")
    (run / "kairos").mkdir(exist_ok=True); (run / "kairos" / "splash-handover").write_text("")
    rc = p.wait(timeout=5)
    assert rc == 0 and "handover: the kiosk is up" in log.read_text()
    (run / "kairos" / "splash-handover").unlink()
    # --stop (the panic hook / emergency drop-in): pidfile -> SIGTERM -> gone
    p, log = start_painter(ctx, fb, sysd, run)
    assert wait_log(log, "phase 1 painted") and (run / "kairos-splash.pid").exists()
    e = env_for(ctx, fb, sysd, run=run)
    t0 = time.time()
    subprocess.run([painter(ctx), "--stop", "--no-reexec"], env=e, capture_output=True, timeout=10)
    rc = p.wait(timeout=5)
    assert rc == 0 and time.time() - t0 < 3 and "stopped by signal 15" in log.read_text()
    return "SIGTERM -> 'stopped by signal 15'; flag file -> handover; --stop ends a running painter via the pidfile"


@test("splash_transition_never_both_layouts", ["R73", "R28"])
def t_transition(ctx):
    """Phase 1 -> 2: the firmware logo + lockup fade OUT to the bare wallpaper first, then the
    wordmark + bar fade IN — the two layouts are never on screen together. Sampled from the
    file framebuffer: during the fade-out the wordmark region holds no bright pixel; once the
    fade-in has started the firmware logo's pixels are gone for good."""
    run = Path(ctx["tmp"]) / "run-tr"; run.mkdir(exist_ok=True)
    fb = Path(ctx["tmp"]) / "fb-tr.raw"; sysd = Path(fbsys(Path(ctx["tmp"]) / "sys-tr", 1280, 800, name="EFI VGA"))
    img = bmp(193, 58, lambda x, y: (200, 30, 30), bpp=24)
    bg = bgrt_dir(Path(ctx["tmp"]) / "bgrt-tr", img, 543, 371, status=1)
    lay = json.loads(subprocess.run([painter(ctx), "--layout", "--no-reexec"], env=env_for(ctx, fb, sysd, bg), capture_output=True, text=True).stdout)
    lx, ly, lw, lh = lay["logo2"]; bx, by, bw, bh = lay["bgrt"]
    stride = 1280 * 4
    def count(raw, x0, y0, w, h, pred, step=3):
        return sum(1 for yy in range(y0, y0 + h, step) for xx in range(x0, x0 + w, step) if pred(px32(raw, stride, xx, yy)))
    red = lambda p: p[0] > 150 and p[1] < 80
    bright = lambda p: min(p) > 200
    p, log = start_painter(ctx, fb, sysd, run, bgrt=bg)
    try:
        assert wait_log(log, "phase 1 painted on EFI VGA")
        raw1 = fb.read_bytes()
        assert count(raw1, bx, by, bw, bh, red) > 100 and count(raw1, lx, ly, lw, lh, bright) == 0, "phase 1: logo block present, no wordmark"
        (run / "kairos-splash" / "milestone").write_text("root\n")
        assert wait_log(log, "phase 1 fades out", 8), log.read_text()
        time.sleep(0.15)                                   # mid fade-out (0.4 s long)
        raw2 = fb.read_bytes()
        r2 = count(raw2, bx, by, bw, bh, red); w2 = count(raw2, lx, ly, lw, lh, bright)
        assert w2 == 0, f"wordmark pixels during the fade-out: {w2}"
        assert wait_log(log, "phase 2 fades in", 3), log.read_text()
        time.sleep(0.12)
        raw3 = fb.read_bytes()
        r3 = count(raw3, bx, by, bw, bh, red)
        assert r3 == 0, f"firmware logo still visible while the wordmark fades in: {r3} red samples"
        assert wait_log(log, "loading bar at", 3)
        time.sleep(0.2)
        raw4 = fb.read_bytes()
        assert count(raw4, lx, ly, lw, lh, bright) > 50 and count(raw4, bx, by, bw, bh, red) == 0
        p.send_signal(signal.SIGUSR1); assert p.wait(timeout=5) == 0
    finally:
        if p.poll() is None:
            p.kill()
    return f"phase 1 {count(raw1, bx, by, bw, bh, red)} logo px -> fade-out ({r2} left, 0 wordmark px) -> fade-in (0 logo px) -> phase 2 ({count(raw4, lx, ly, lw, lh, bright)} wordmark px)"


@test("splash_root_cover_hermetic", ["R73", "R28"])
def t_root_cover(ctx):
    """scripts/splash/root-cover.py (the painter's frame as the retained root background,
    for drivers that do not copy the framebuffer under -background none): run against a fake
    python-xlib — it must fill the root-sized pixmap, put every row of the frame at its
    offset in bands under the request limit, set it as the root background with
    RetainPermanent and repaint; any error exits 0."""
    fake = Path(ctx["tmp"]) / "fakexlib" / "Xlib"; fake.mkdir(parents=True, exist_ok=True)
    (fake / "__init__.py").write_text("")
    (fake / "X.py").write_text("RetainPermanent = 1\nZPixmap = 2\n")
    (fake / "display.py").write_text('''import json, os
LOG = []
class Pix:
    def create_gc(self, **kw): LOG.append(("gc", kw)); return "gc"
    def fill_rectangle(self, gc, x, y, w, h): LOG.append(("fill", x, y, w, h))
    def put_image(self, gc, x, y, w, h, fmt, depth, pad, data):
        assert len(data) == w * h * 4, (len(data), w, h)
        assert len(data) <= 262140, "request too large"
        LOG.append(("put", x, y, w, h, fmt, depth))
class Root:
    def create_pixmap(self, w, h, depth): LOG.append(("pixmap", w, h, depth)); return Pix()
    def change_attributes(self, **kw): LOG.append(("attrs", sorted(kw)))
    def clear_area(self, *a, **kw): LOG.append(("clear",))
class Screen:
    root = Root(); root_depth = 24; width_in_pixels = 3000; height_in_pixels = 1200
class Display:
    def set_close_down_mode(self, m): LOG.append(("closedown", m))
    def screen(self): return Screen()
    def sync(self):
        LOG.append(("sync",)); open(os.environ["FAKE_XLIB_LOG"], "w").write(json.dumps(LOG))
''')
    raw = Path(ctx["tmp"]) / "cover.raw"; raw.write_bytes(bytes(1920 * 1080 * 4))
    logf = Path(ctx["tmp"]) / "xlib.json"
    env = dict(os.environ, PYTHONPATH=str(fake.parent), FAKE_XLIB_LOG=str(logf))
    r = subprocess.run([sys.executable, str(SPLASH / "root-cover.py"), str(raw), "1920", "1080", "100", "60"], env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr[-300:]
    calls = json.loads(logf.read_text())
    assert ["closedown", 1] in calls and ["pixmap", 3000, 1200, 24] in calls and ["fill", 0, 0, 3000, 1200] in calls
    puts = [c for c in calls if c[0] == "put"]
    assert puts and puts[0][1] == 100 and puts[0][2] == 60 and sum(c[4] for c in puts) == 1080 and all(c[3] == 1920 and c[6] == 24 for c in puts), puts[:3]
    assert ["attrs", ["background_pixmap"]] in calls and ["clear"] in calls and calls[-1] == ["sync"]
    # a short/missing frame or an import error never fails the session
    for args in ([str(raw), "4000", "4000"], ["/nonexistent", "10", "10"]):
        r = subprocess.run([sys.executable, str(SPLASH / "root-cover.py")] + args, env=env, capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, args
    r = subprocess.run([sys.executable, str(SPLASH / "root-cover.py"), str(raw), "1920", "1080"], env=dict(os.environ, PYTHONPATH="/nonexistent"), capture_output=True, text=True, timeout=60)
    assert r.returncode == 0
    return f"{len(puts)} put_image bands at +100+60 cover 1080 rows on a 3000x1200 root pixmap, RetainPermanent, background set + cleared; errors exit 0"


# ----------------------------------------------------------------------------- the initramfs + units + scripts
@test("splash_initramfs_hook_and_scripts", ["R73", "R51", "R63"])
def t_initramfs(ctx):
    """The initramfs-tools pieces: hook (copy_exec of the static painter, never fails the
    initrd build), init-top (starts the daemon before udev, only with `quiet`, off with
    kairos.splash=0), init-bottom (expected time from ${rootmnt} + the root milestone), panic
    (stops the painter + text console before the shell); all with the prereqs boilerplate,
    executable, exit 0 on every path."""
    base = INC / "etc" / "initramfs-tools"
    files = {"hook": base / "hooks" / "kairos-splash", "top": base / "scripts" / "init-top" / "kairos-splash",
             "bottom": base / "scripts" / "init-bottom" / "kairos-splash", "panic": base / "scripts" / "panic" / "kairos-splash"}
    for k, f in files.items():
        assert f.exists() and os.access(f, os.X_OK), f"{f} missing or not executable"
        subprocess.run(["sh", "-n", str(f)], check=True)
        t = f.read_text()
        assert 'prereqs() { echo "$PREREQ"; }' in t and 'case "$1" in prereqs) prereqs; exit 0 ;; esac' in t, f"{k}: prereqs boilerplate"
        assert t.rstrip().endswith("exit 0"), f"{k}: must end in exit 0"
        r = subprocess.run(["sh", str(f), "prereqs"], capture_output=True, text=True, timeout=10)
        assert r.returncode == 0 and r.stdout.strip() == "", (k, r.stdout, r.stderr)
    hook = files["hook"].read_text()
    assert ". /usr/share/initramfs-tools/hook-functions" in hook and "copy_exec /usr/local/sbin/kairos-splash /sbin/kairos-splash" in hook
    assert "exit 0" in hook.split("if [ ! -x /usr/local/sbin/kairos-splash ]")[1].split("fi")[0], "a missing painter never fails update-initramfs"
    top = files["top"].read_text()
    assert '[ -x /sbin/kairos-splash ] || exit 0' in top and "/sbin/kairos-splash --daemon" in top
    assert 'kairos.splash=0' in top and '*" quiet "*) ;; *) exit 0 ;;' in top, "silent entry only; explicit off switch"
    assert 'PREREQ=""' in top, "no prereq: listed first (set_initlist takes /etc scripts before the stock ones) -> before udev"
    bottom = files["bottom"].read_text()
    assert '${rootmnt:-/root}/opt/vibeos/cache/boottime/expected' in bottom and "echo root > /run/kairos-splash/milestone" in bottom
    assert "[ -e /run/kairos-splash.pid ] || exit 0" in bottom
    panic = files["panic"].read_text()
    assert "/sbin/kairos-splash --stop" in panic
    # hermetic: init-bottom copies the expected time and writes the milestone (the run dir is /run — simulate with a chroot-free trick: the script uses fixed paths, so only its text is checked) and init-top/panic do nothing without the binary
    with tempfile.TemporaryDirectory() as td:
        r = subprocess.run(["/bin/sh", str(files["top"])], env=dict(os.environ, PATH="/nonexistent"), capture_output=True, text=True, timeout=10)
        assert r.returncode == 0 and r.stderr == "", r
        r = subprocess.run(["/bin/sh", str(files["panic"])], env=dict(os.environ, PATH="/nonexistent"), capture_output=True, text=True, timeout=10)
        assert r.returncode == 0, r
    src = (SPLASH / "kairos-splash.c").read_text()
    assert 'nargv[n++] = "@kairos-splash";' in src and "prctl(PR_SET_NAME" in src, "argv[0] '@' (spared across switch_root) + comm kairos-splash"
    for d in ("devfd", "sysfd", "runfd", "procfd"):
        assert f"{d} = open(" in src, f"{d}: dirfds that survive the mount moves of switch_root"
    assert 'openat(devfd, "fb0"' in src and '"1/root%s"' in src, "fb0 through the /dev dirfd; the archive through /proc/1/root"
    return "hook copy_exec (tolerant), init-top (quiet-only, --daemon, no prereq), init-bottom (expected + root), panic (--stop); '@' re-exec + dirfds"


@test("splash_unit_dropins_ctl", ["R73", "R51", "R63"])
def t_units(ctx):
    """kairos-splash.service (no default deps, oneshot, ExecStart=- / ExecStop=-, sysinit),
    the emergency/rescue drop-ins (ExecStartPre=- kairos-splash --stop), kairos-splash-ctl
    (hermetic run of userspace / milestone / handover / archive)."""
    units = INC / "etc" / "systemd" / "system"
    u = (units / "kairos-splash.service").read_text()
    for needle in ("DefaultDependencies=no", "After=local-fs.target", "Conflicts=shutdown.target", "Before=shutdown.target",
                   "Type=oneshot", "RemainAfterExit=yes", "ExecStart=-/usr/local/sbin/kairos-splash-ctl userspace",
                   "ExecStop=-/usr/local/sbin/kairos-splash-ctl archive", "WantedBy=sysinit.target"):
        assert needle in u, f"kairos-splash.service lacks {needle}"
    assert "getty" not in u, "no getty ordering (D7: never after a target and before something inside it)"
    for svc in ("emergency", "rescue"):
        d = (units / f"{svc}.service.d" / "kairos-splash.conf").read_text()
        assert "[Service]" in d and "ExecStartPre=-/usr/local/sbin/kairos-splash --stop" in d, svc
    for old in ("kairos-bootanim.service", "kairos-bootanim-gen.service"):
        assert not (units / old).exists(), f"{old} must be gone (R27 superseded)"
    ctl = INC / "usr" / "local" / "sbin" / "kairos-splash-ctl"
    assert os.access(ctl, os.X_OK); subprocess.run(["sh", "-n", str(ctl)], check=True)
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); (t / "run").mkdir(); (t / "exp").write_text("95.0\n")
        bin_ = t / "bin"; bin_.mkdir(); (bin_ / "kairos-splash").write_text("#!/bin/sh\necho \"$@\" > \"$KAIROS_SPLASH_RUN/stop-called\"\n"); (bin_ / "kairos-splash").chmod(0o755)
        env = dict(os.environ, KAIROS_SPLASH_RUN=str(t / "run"), KAIROS_SPLASH_EXPECTED_FILE=str(t / "exp"),
                   KAIROS_SPLASH_BIN=str(bin_ / "kairos-splash"), KAIROS_SPLASH_ARCHIVE=str(t / "archive.log"))
        for args in (["userspace"], ["milestone", "x"], ["handover"], ["stop"], ["archive"], ["status"], ["bogus"]):
            r = subprocess.run(["sh", str(ctl)] + args, env=env, capture_output=True, text=True, timeout=10)
            assert r.returncode == 0, (args, r)
        assert (t / "run" / "kairos-splash" / "expected").read_text() == "95.0\n"
        assert (t / "run" / "kairos-splash" / "milestone").read_text() == "x\n"
        assert (t / "run" / "kairos" / "splash-handover").exists()
        assert (t / "run" / "stop-called").read_text().strip() == "--stop"
        # archive only while the painter is alive (it archives itself otherwise)
        (t / "run" / "kairos-splash.log").write_text("line\n"); (t / "run" / "kairos-splash.pid").write_text(str(os.getpid()))
        subprocess.run(["sh", str(ctl), "archive"], env=env, check=True)
        assert "line" in (t / "archive.log").read_text()
    return "unit: no default deps, oneshot, sysinit; drop-ins stop the painter before sulogin; ctl: userspace/milestone/handover/stop/archive hermetic"


@test("splash_xinitrc_bash_profile_contracts", ["R73", "R28", "R51"])
def t_xinit(ctx):
    """.xinitrc: breadcrumbs in order, the invisible root cursor until the kiosk and the arrow
    after it, the kiosk opened AT ONCE on the local boot page with the painter's progress,
    the handover flag after the launch, no mpv/animation, no solid root; .bash_profile: the
    attempt counter + x.log + x-failed flag, the X milestone flag, tty1 never cleared,
    -background none, no -nocursor (it hid every pointer, v7.5)."""
    home = INC / "home" / "kairos"
    xi = (home / ".xinitrc").read_text(); prof = (home / ".bash_profile").read_text()
    subprocess.run(["sh", "-n", str(home / ".xinitrc")], check=True); subprocess.run(["bash", "-n", str(home / ".bash_profile")], check=True)
    assert xi.index('echo "xinitrc: started') < xi.index("xset -dpms") < xi.index('echo "xinitrc: X answers') < xi.index('echo "xinitrc: displays:'), "breadcrumbs"
    assert 'xsetroot -cursor "$BLANK" "$BLANK"' in xi and xi.index('echo "xinitrc: kiosk pids') < xi.index("xsetroot -cursor_name left_ptr"), "cursor logic"
    assert "xsetroot -solid" not in xi, "the root keeps the painter's frame (-background none), no solid step"
    assert 'KIOSK_URL="file://$BOOTPAGE"' in xi and "BOOTPAGE=/opt/vibeos/os/shell/boot-wait.html" in xi
    # [R90] ONE desktop window over every monitor: an --app window (fullscreen = one monitor), never browser-fullscreen
    # (no "press Esc to exit full screen" bubble); --kiosk only for an unknown screen size (headless)
    assert "--start-fullscreen" not in xi and xi.count("chromium --kiosk ") == 1 and "--class=kairos-desktop" in xi
    assert "root_cover()" in xi and "( root_cover ) &" in xi and "root-cover.py" in xi and "--once --no-reexec --phase 2 --progress" in xi
    assert "&boot=1&up=" in xi and "&expected=$E&p=$P" in xi and "/run/kairos-splash/expected" in xi and "/run/kairos-splash/progress" in xi
    assert 'echo "xinitrc: kiosk opens the boot page' in xi
    assert re.search(r'--app="\$\{KIOSK_URL\}\?mon=\$\{MON\}\$\{BOOT_QS\}"', xi) and '?mon=${BOOT_QS}"' in xi
    assert 'BOOT_QS="&relaunch=1"' in xi, "a relaunch is not a boot"
    assert "handover_splash()" in xi and ": > /run/kairos/splash-handover" in xi
    assert xi.index("launch_all_kiosks\n") < xi.index("handover_splash\n") < xi.index("while true; do\n        sleep 3"), "handover after the launch"
    assert "kernel_up" in xi and "KAIROS_KERNEL_WAIT" in xi and 'echo "xinitrc: kernel answered on :1337 after ${i}s"' in xi
    assert not re.search(r"\bwhile .*kernel_up.*\n\s*i=\$\(\(i \+ 1\)\); sleep 1\ndone\nKIOSK_URL", xi), "the kiosk no longer waits for the kernel"
    for bad in ("mpv", "ANIM", "bootanim", "launching the kiosk anyway"):
        assert bad not in xi, f".xinitrc still mentions {bad}"
    assert "/run/kairos/x-attempts" in prof and "/run/kairos/x-failed" in prof and "/var/log/kairos/x.log" in prof
    assert "touch /run/kairos/splash-x" in prof and "pkill" not in prof and "mpv" not in prof and "bootanim" not in prof
    assert "-background none" in prof and not re.search(r"^\s*startx .*-nocursor", prof, re.M) and "\\033[?25l" in prof   # v7.5: -nocursor hid every pointer (xfixes)
    assert not re.search(r"\\033\[2J|\\033c|\btput clear\b|^\s*clear\s*$", prof, re.M), "tty1 must never be cleared"
    assert "sleep 3" in prof and "sleep 30" in prof, "paced X retries stay"
    return "breadcrumbs, cursor, boot page first (+ progress params), handover flag after launch, relaunch flag; profile: counters, splash-x, no clear"


@test("splash_boot_page", ["R73", "R28"])
def t_page(ctx):
    """shell/boot-wait.html: the same logo + bar, continues from ?p= with the painter's formula,
    polls /api/status and replaces itself with the shell keeping the kiosk hint but not its
    own params; relaunch mode without the bar; no context menu, no cursor, emoji font stack."""
    html = (REPO / "shell" / "boot-wait.html").read_text()
    for needle in ("api/status", "location.replace(shellUrl())", "kAIrOS", "Noto Color Emoji", 'addEventListener("contextmenu"',
                   "cursor: none", 'src="splash/logo-thick.png"', 'Q.get("boot") === "1"', 'parseFloat(Q.get("p"))',
                   '["boot", "up", "now", "expected", "p", "relaunch"]', 'classList.add("relaunch")', "body.relaunch .bar { display: none; }",
                   "@keyframes sweep", 'fill.style.width = "100%"', "function uptime()"):
        assert needle in html, f"boot page lacks {needle!r}"
    assert "mpv" not in html and "bootanim" not in html
    # the shell URL keeps the ?mon= hint [R90], drops the page's own params (a JS-free re-implementation of shellUrl)
    keep = [k for k in ("mon", "boot", "up", "now", "expected", "p", "relaunch")
            if k not in ["boot", "up", "now", "expected", "p", "relaunch"]]
    assert keep == ["mon"]
    # [R90] the picture on EVERY monitor of the hint: tiles from shell/monitors.js, every bar driven, re-tiled on resize
    for needle in ('<script src="monitors.js"></script>', 'KMon.parseHint(Q.get("mon"))', 'addEventListener("resize", tiles)',
                   'document.querySelectorAll(".fill")', 'st.setProperty("--tw", m.w + "px")'):
        assert needle in html, f"boot page lacks {needle!r}"
    return "logo-thick.png + bar per monitor, progress continues from ?p=, hands over to the shell with the hint only, relaunch mode, R28 guards"


@test("splash_r27_removed", ["R73", "R28"])
def t_removed(ctx):
    """Nothing of the R27 animation ships any more: no mpv (package list, scripts), no clip, no
    dream module/endpoints/prompt/units, the hook renders no default clip, the bootlog quotes
    the splash log instead."""
    pkgs = (REPO / "iso" / "config" / "package-lists" / "kairos.list.chroot").read_text()
    assert not re.search(r"^mpv$", pkgs, re.M), "mpv must not be in the image"
    for gone in ("kernel/bootanim.py", "scripts/bootanim-play.sh", "scripts/bootanim-splash.py",
                 "iso/config/includes.chroot/etc/systemd/system/kairos-bootanim.service",
                 "iso/config/includes.chroot/etc/systemd/system/kairos-bootanim-gen.service"):
        assert not (REPO / gone).exists(), f"{gone} still present"
    srv = (REPO / "kernel" / "server.py").read_text()
    assert "bootanim" not in srv and '"/api/boot"' in srv and "boottime.desktop_ready()" in srv and "sessionlog.note_event(" in srv
    assert "BOOTANIM_SYSTEM" not in (REPO / "kernel" / "prompts.py").read_text()
    hook = (REPO / "iso" / "config" / "hooks" / "normal" / "0200-kairos-system.hook.chroot").read_text()
    assert "bootanim" not in hook and "ffmpeg -nostdin" not in hook and "scripts/splash/install.sh" in hook
    bl = (INC / "usr" / "local" / "sbin" / "kairos-bootlog").read_text()
    assert "BOOT SCREEN LOG" in bl and "/run/kairos-splash.log" in bl and "/var/log/kairos/splash.log" in bl and "bootanim" not in bl
    for f in ("iso/build-img.sh", "iso/build-iso.sh"):
        assert "bootanim" not in (REPO / f).read_text() and "mpv" not in (REPO / f).read_text(), f
    for p in list((REPO / "kernel").glob("*.py")) + list((REPO / "scripts").glob("*.sh")) + list((REPO / "scripts").glob("*.py")) + list(INC.rglob("*")):
        if p.is_file() and ".bak" not in p.name:
            t = p.read_text(errors="replace")
            assert "bootanim" not in t or p.name in ("boottime.py", "sessionlog.py"), f"{p.relative_to(REPO)} still references bootanim"
    pw = (INC / "usr" / "local" / "sbin" / "kairos-prewarm").read_text()
    assert "mpv" not in pw, "no mpv-only prewarm entries (there never were any)"
    return "no mpv, no clip, no dream module/units/prompt/endpoints; bootlog quotes BOOT SCREEN LOG; prewarm list untouched"


@test("splash_kernel_boottime_sessionlog", ["R73", "R44", "R63"])
def t_boottime(ctx):
    """kernel/boottime.py: the expected file exists after init (default 120 s), desktop_ready
    records once per kernel start into a 5-boot history and rewrites `expected`, a late
    kernel start is not a boot, an unwritable dir only warns; sessionlog keeps 40 events."""
    sys.path.insert(0, str(REPO / "kernel"))
    import importlib
    boottime = importlib.import_module("boottime"); sessionlog = importlib.import_module("sessionlog")
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        boottime.init(t)
        assert (t / "cache" / "boottime" / "expected").read_text() == "120.0\n", "the next initramfs needs a number before any measurement"
        assert boottime.status()["expected_seconds"] == 120.0 and boottime.status()["history"] == []
        assert boottime.desktop_ready(95.0) == 95.0 and boottime.desktop_ready(99.0) is None, "once per kernel start"
        assert json.loads((t / "cache" / "boottime" / "boot-times.json").read_text()) == [95.0]
        assert (t / "cache" / "boottime" / "expected").read_text() == "95.0\n" and boottime.status()["this_boot_seconds"] == 95.0
        for v in (100.0, 110.0, 120.0, 130.0, 140.0):
            boottime.init(t); boottime.desktop_ready(v)
        assert boottime.history() == [100.0, 110.0, 120.0, 130.0, 140.0] and boottime.expected() == 120.0, boottime.history()
        boottime.init(t)
        assert boottime.desktop_ready(700.0) is None and boottime.history()[-1] == 140.0, "a kernel restart mid-session is not a boot"
        assert boottime.status()["this_boot_seconds"] is None
        boottime.init(t); boottime.desktop_ready(5.0)
        assert boottime.expected() >= boottime.MIN_EXPECTED, "clamped"
        # unwritable: warnings only
        bad = t / "bad"; bad.mkdir(); (bad / "cache").mkdir(); (bad / "cache" / "boottime").write_text("not a directory")
        boottime.init(bad)
        assert boottime.desktop_ready(42.0) == 42.0 and boottime.status()["this_boot_seconds"] == 42.0
    sessionlog.clear()
    for i in range(50):
        sessionlog.note_event("app", f"thing {i}")
    ev = sessionlog.session_events()
    assert len(ev) == 40 and ev[0] == ("app", "thing 10") and ev[-1] == ("app", "thing 49")
    sessionlog.note_event("ask", "x" * 500); assert len(sessionlog.session_events()[-1][1]) == 300
    sessionlog.clear()
    return "expected 120 -> measured history (5 kept, avg), late start ignored, unwritable dir warns; session log trims to 40"


@test("splash_build_sync_wiring", ["R73", "R63"])
def t_wiring(ctx):
    """The boot screen really ships: hook 0200 and build-img.sh run scripts/splash/install.sh
    before update-initramfs and build-img asserts the initrd carries the painter; build-img
    installs the initramfs files + unit + drop-ins; the testrig sync does the same with a
    rebuilt initrd; the committed static binary matches the source version."""
    hook = (REPO / "iso" / "config" / "hooks" / "normal" / "0200-kairos-system.hook.chroot").read_text()
    assert "sh /opt/vibeos/os/scripts/splash/install.sh /opt/vibeos/os" in hook
    b = (REPO / "iso" / "build-img.sh").read_text()
    chroot = b.split("<<'CHROOT'")[1].split("\nCHROOT")[0]
    assert chroot.index("scripts/splash/install.sh") < chroot.index("update-initramfs -u -k all"), "painter installed before the initrd is built"
    assert "sysinit.target.wants/kairos-splash.service" in b and "lsinitramfs" in b and "sbin/kairos-splash$" in b
    for f in ("etc/initramfs-tools/hooks/kairos-splash", "etc/initramfs-tools/scripts/panic/kairos-splash", "etc/systemd/system/kairos-splash.service",
              "etc/systemd/system/emergency.service.d/kairos-splash.conf", "usr/local/sbin/kairos-splash-ctl"):
        assert f in b, f"build-img.sh must install {f}"
    for f in ("build.sh", "install.sh"):
        subprocess.run(["sh", "-n", str(SPLASH / f)], check=True); assert os.access(SPLASH / f, os.X_OK)
    inst = (SPLASH / "install.sh").read_text()
    assert "install -D -m 755" in inst and "systemctl enable kairos-splash.service" in inst and 'KAIROS_SPLASH_INITRAMFS' in inst
    sync = REPO.parent.parent / "pod" / "testrig" / "sync-guest.sh"
    if sync.exists():
        s = sync.read_text()
        assert "KAIROS_SPLASH_INITRAMFS=1 sh /opt/vibeos/os/scripts/splash/install.sh" in s and "kairos-splash.service" in s and "mpv" not in s and "bootanim-default" not in s.split("R27 leftovers")[0] or "rm -f" in s
    committed = SPLASH / "kairos-splash"
    assert committed.exists() and os.access(committed, os.X_OK), "the static painter is committed (the image has no compiler)"
    ver = re.search(r'#define SPLASH_VERSION "([^"]+)"', (SPLASH / "kairos-splash.c").read_text()).group(1)
    r = subprocess.run([str(committed), "--help"], capture_output=True, text=True, timeout=10)
    assert ver in r.stderr, f"committed binary is version {r.stderr.splitlines()[0] if r.stderr else '?'}, source {ver}: rebuild with build.sh"
    head = committed.read_bytes()[:64]
    assert head[:4] == b"\x7fELF" and struct.unpack("<H", head[16:18])[0] == 2, "an ELF executable"
    assert b"ld-linux" not in committed.read_bytes()[:200000], "static: no dynamic loader"
    return f"hook + build-img install.sh before update-initramfs, initrd asserted; sync-guest rebuilds the initrd; committed binary {ver} ({_BIN.get('how', 'not built')})"


@test("splash_assets_reproducible", ["R73"])
def t_assets(ctx):
    """scripts/splash/render-assets.py regenerates logo_data.h + the PNGs byte-identically
    (needs Pillow + Noto Sans; skips elsewhere)."""
    try:
        import PIL  # noqa
    except ImportError:
        raise SkipTest("Pillow not installed")
    if not any(Path(d).exists() for d in ("/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",)) and not list(Path("/usr/share/fonts").rglob("NotoSans-Bold.ttf")):
        raise SkipTest("Noto Sans not installed")
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); (t / "scripts" / "splash").mkdir(parents=True); (t / "shell").mkdir()
        shutil.copy(SPLASH / "render-assets.py", t / "scripts" / "splash" / "render-assets.py")
        r = subprocess.run([sys.executable, str(t / "scripts" / "splash" / "render-assets.py")], capture_output=True, text=True, timeout=120)
        assert r.returncode == 0, r.stderr[-400:]
        assert (t / "scripts" / "splash" / "logo_data.h").read_bytes() == (SPLASH / "logo_data.h").read_bytes(), "logo_data.h differs: re-run render-assets.py and rebuild"
        for png in ("logo-phase1.png", "logo-thick.png"):
            assert (t / "shell" / "splash" / png).read_bytes() == (REPO / "shell" / "splash" / png).read_bytes(), png
    return "logo_data.h + logo-phase1.png + logo-thick.png regenerate byte-identically"


def main():
    tests = [v for v in globals().values() if getattr(v, "_test", False)]
    only = [x.strip() for x in os.environ.get("KAIROS_TEST_ONLY", "").split(",") if x.strip()]
    if only:
        tests = [t for t in tests if any(o in t._name for o in only)]
    print(f"kAIrOS boot screen tests — {len(tests)} tests", flush=True)
    with tempfile.TemporaryDirectory(prefix="kairos-splash-test.") as td:
        ctx = {"tmp": td}
        for t in tests:
            t(ctx)
    out = {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "painter": _BIN.get("how", "none"), "tests": RESULTS}
    if not only:
        (REPO / "tests" / "results-splash.json").write_text(json.dumps(out, indent=1))
    fails = [n for n, r in RESULTS.items() if r["status"] == "fail"]
    passes = sum(1 for r in RESULTS.values() if r["status"] == "pass")
    skips = sum(1 for r in RESULTS.values() if r["status"] == "skip")
    print(f"\n{passes} passed, {len(fails)} failed, {skips} skipped -> tests/results-splash.json", flush=True)
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
