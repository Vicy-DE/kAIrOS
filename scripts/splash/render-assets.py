#!/usr/bin/env python3
"""kAIrOS boot screen [R73] — render the brand assets the splash painter embeds.

Reproducible: run it on a host with Pillow and the image's own UI font (Noto Sans,
`fonts-noto-core` — the same family Chromium renders the shell's `sans-serif` with on
the appliance). It writes

  shell/splash/logo-phase1.png   the modest "❖ kAIrOS  by mAIer" lockup drawn under the
                                 firmware's own logo (Kubuntu-style BGRT phase)
  shell/splash/logo-thick.png    the heavy wordmark of the loading screen (phase 2)
  scripts/splash/logo_data.h     both, RLE-encoded (straight RGBA), compiled into
                                 scripts/splash/kairos-splash.c
  scripts/splash/assets/preview-*.png   quick look of each logo on the wallpaper tone

Reference canvas is 1920x1080; the painter scales to the real framebuffer. Colours are
the shell's (shell/shell.css): --accent #7c5cff, --accent2 #41e2ff, text #e8e8f2.
"""
import os
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

REPO = Path(__file__).resolve().parent.parent.parent
OUT_SHELL = REPO / "shell" / "splash"
OUT_H = REPO / "scripts" / "splash" / "logo_data.h"
OUT_PREV = REPO / "scripts" / "splash" / "assets"

ACCENT = (0x7C, 0x5C, 0xFF)
ACCENT2 = (0x41, 0xE2, 0xFF)
TEXT = (0xF2, 0xF2, 0xFA)
FONT_DIRS = ["/usr/share/fonts/truetype/noto", "/usr/share/fonts/noto", "/usr/share/fonts/truetype"]


def font(name, size):
    for d in FONT_DIRS:
        p = Path(d) / name
        if p.exists():
            return ImageFont.truetype(str(p), size)
    for p in Path("/usr/share/fonts").rglob(name):
        return ImageFont.truetype(str(p), size)
    sys.exit(f"font {name} not found (apt-get install fonts-noto-core)")


def diamond(draw, cx, cy, s, fill):
    """❖ (BLACK DIAMOND MINUS WHITE X): four rhombi, the X-shaped gap between them."""
    gap = s * 0.11
    half = s / 2 - gap
    for dx, dy in ((0, -s / 2), (s / 2, 0), (0, s / 2), (-s / 2, 0)):
        x, y = cx + dx, cy + dy
        draw.polygon([(x, y - half), (x + half, y), (x, y + half), (x - half, y)], fill=fill)


def text_spaced(img, xy, text, fnt, fill, spacing=0, stroke=0):
    """Draw text with extra letter spacing; returns the advance width."""
    d = ImageDraw.Draw(img)
    x, y = xy
    for ch in text:
        d.text((x, y), ch, font=fnt, fill=fill, stroke_width=stroke, stroke_fill=fill)
        x += fnt.getlength(ch) + spacing
    return x - xy[0] - spacing


def text_width(text, fnt, spacing=0):
    return sum(fnt.getlength(ch) for ch in text) + spacing * (len(text) - 1)


def gradient_text(img, xy, text, fnt, top, bottom, spacing=0, stroke=0):
    """Text filled with a vertical gradient top->bottom (mask + gradient paste)."""
    mask = Image.new("L", img.size, 0)
    md = ImageDraw.Draw(mask)
    x, y = xy
    for ch in text:
        md.text((x, y), ch, font=fnt, fill=255, stroke_width=stroke, stroke_fill=255)
        x += fnt.getlength(ch) + spacing
    bbox = mask.getbbox()
    if not bbox:
        return 0
    grad = Image.new("RGBA", img.size, (0, 0, 0, 0))
    gd = ImageDraw.Draw(grad)
    y0, y1 = bbox[1], bbox[3]
    for yy in range(y0, y1 + 1):
        t = (yy - y0) / max(1, y1 - y0)
        c = tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3)) + (255,)
        gd.line([(bbox[0], yy), (bbox[2], yy)], fill=c)
    img.paste(grad, (0, 0), mask)
    return x - xy[0] - spacing


def crop_alpha(img, pad=2):
    bbox = img.getbbox()
    x0, y0, x1, y1 = bbox
    return img.crop((max(0, x0 - pad), max(0, y0 - pad), min(img.width, x1 + pad), min(img.height, y1 + pad)))


def render_phase1():
    """The modest lockup under the vendor logo: ❖ kAIrOS  by mAIer, ~420 px wide at 1080p."""
    img = Image.new("RGBA", (900, 160), (0, 0, 0, 0))
    bold, light = font("NotoSans-Bold.ttf", 54), font("NotoSans-Regular.ttf", 28)
    d = ImageDraw.Draw(img)
    x = 20
    diamond(d, x + 24, 72, 44, ACCENT + (255,))
    x += 66
    w = text_spaced(img, (x, 30), "kAIrOS", bold, TEXT + (255,), spacing=2)
    x += w + 22
    text_spaced(img, (x, 56), "by mAIer", light, ACCENT2 + (255,), spacing=3)
    return crop_alpha(img)


def render_thick():
    """The loading screen's heavy wordmark: ❖ above, kAIrOS extra-bold (stroke-thickened
    Noto Sans Bold, "AI" in the accent gradient), "by mAIer" letter-spaced below."""
    img = Image.new("RGBA", (1100, 460), (0, 0, 0, 0))
    big = font("NotoSans-Bold.ttf", 164)
    light = font("NotoSans-Regular.ttf", 34)
    d = ImageDraw.Draw(img)
    stroke = 8
    spacing = 6
    word = "kAIrOS"
    total = text_width(word, big, spacing) + 2 * stroke
    x0 = (img.width - total) / 2 + stroke
    y = 110
    # k · AI · rOS — the AI pair carries the accent gradient
    x = x0
    x += text_spaced(img, (x, y), "k", big, TEXT + (255,), spacing, stroke) + spacing
    x += gradient_text(img, (x, y), "AI", big, ACCENT, ACCENT2, spacing, stroke) + spacing
    text_spaced(img, (x, y), "rOS", big, TEXT + (255,), spacing, stroke)
    diamond(d, img.width / 2, 72, 50, ACCENT + (255,))
    tag = "by mAIer"
    tw = text_width(tag, light, 7)
    text_spaced(img, ((img.width - tw) / 2, 352), tag, light, ACCENT2 + (255,), spacing=7)
    return crop_alpha(img)


def rle(img):
    """Straight RGBA -> (rgb runs, alpha runs): [count,r,g,b]* and [count,a]*, count 1..255.
    Antialiased edges keep the fill's RGB (only alpha varies), so the RGB stream is
    almost all long runs; the alpha stream is short at the edges and long elsewhere."""
    px = list(img.convert("RGBA").getdata())
    rgb, al = [], []
    def push(stream, key, width):
        if stream and stream[-1][1] == key and stream[-1][0] < 255:
            stream[-1][0] += 1
        else:
            stream.append([1, key])
    for r, g, b, a in px:
        if a == 0:
            r = g = b = 0          # transparent pixels: RGB is irrelevant, make them one run
        push(rgb, (r, g, b), 3)
        push(al, a, 1)
    rgb_bytes = bytes(v for n, (r, g, b) in rgb for v in (n, r, g, b))
    a_bytes = bytes(v for n, a in al for v in (n, a))
    return rgb_bytes, a_bytes


def c_array(name, data):
    lines = [f"static const unsigned char {name}[{len(data)}] = {{"]
    for i in range(0, len(data), 24):
        lines.append("  " + ",".join(str(b) for b in data[i:i + 24]) + ",")
    lines.append("};")
    return "\n".join(lines)


def write_header(logos):
    parts = ["/* generated by scripts/splash/render-assets.py — do not edit by hand [R73]",
             " * Straight-alpha RGBA sprites, run-length encoded: RGB runs [count,r,g,b]*, alpha runs [count,a]*. */",
             "#ifndef KAIROS_LOGO_DATA_H", "#define KAIROS_LOGO_DATA_H"]
    for name, img in logos.items():
        rgb, al = rle(img)
        parts.append(f"#define {name}_W {img.width}")
        parts.append(f"#define {name}_H {img.height}")
        parts.append(c_array(f"{name}_RGB", rgb))
        parts.append(c_array(f"{name}_A", al))
    parts.append("#endif")
    OUT_H.write_text("\n".join(parts) + "\n")


def preview(img, name, bg=(0x0A, 0x0A, 0x14)):
    OUT_PREV.mkdir(parents=True, exist_ok=True)
    canvas = Image.new("RGBA", (img.width + 120, img.height + 120), bg + (255,))
    canvas.alpha_composite(img, (60, 60))
    canvas.convert("RGB").save(OUT_PREV / f"preview-{name}.png")


def main():
    OUT_SHELL.mkdir(parents=True, exist_ok=True)
    p1, p2 = render_phase1(), render_thick()
    p1.save(OUT_SHELL / "logo-phase1.png", optimize=True)
    p2.save(OUT_SHELL / "logo-thick.png", optimize=True)
    write_header({"LOGO1": p1, "LOGO2": p2})
    preview(p1, "phase1"); preview(p2, "thick")
    print(f"logo-phase1.png {p1.width}x{p1.height}, logo-thick.png {p2.width}x{p2.height}, "
          f"logo_data.h {OUT_H.stat().st_size // 1024} KiB")


if __name__ == "__main__":
    main()
