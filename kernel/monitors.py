"""kAIrOS one desktop across all monitors [R90] — the monitor math the kernel, the X side and the tests share.

Pure functions, no X, no I/O. A *monitor* is what the user sees as one screen:
  {"id": "eDP", "x": 0, "y": 0, "w": 1920, "h": 1080, "primary": True, "outputs": ["eDP"]}
in X root pixels. Outputs that show the same picture (a mirror: the same origin) are ONE monitor — the area every
one of them shows (the smaller size). Exactly one monitor is primary (the menu bar and the dock live there): the one
X marks primary, else the one at the top-left. Order: primary first, then left-to-right, top-to-bottom — the
numbering /api/vision "screen:N" uses. The desktop (one kiosk window) covers the union = the bounding box; the parts
of the box no monitor shows are *dead areas* (e.g. 1920x360 under a 1080p panel next to a 1440p monitor).
shell/monitors.js is the same math in JS (tests/test_one_desktop.py runs one table through both).
"""
import re

GEOM = re.compile(r"(\d+)x(\d+)\+(-?\d+)\+(-?\d+)")


def parse_xrandr(text):
    """`xrandr --query` -> [{output, connected, primary, resolution, position, rotation, modes, preferred}] (the display
    API's view, R53). The geometry is the CURRENT one (rotation already applied); a connected output that is switched
    off has none. `preferred` = the mode xrandr marks "+" (the monitor's native one), None when none is marked."""
    outputs, cur = [], None
    for line in text.splitlines():
        m = re.match(r"^(\S+) (connected|disconnected)(.*)$", line)
        if m:
            name, state, rest = m.group(1), m.group(2), m.group(3)
            geo = GEOM.search(rest.split("(")[0])
            # the rotation keyword sits between the geometry and the "(normal left inverted right)" capability
            # list — never match inside that list
            head = rest.split("(")[0].split()
            cur = {"output": name, "connected": state == "connected",
                   "primary": "primary" in head,
                   "resolution": (f"{geo.group(1)}x{geo.group(2)}" if geo else None),
                   "position": ([int(geo.group(3)), int(geo.group(4))] if geo else None),
                   "rotation": next((r for r in ("left", "right", "inverted") if r in head), "normal"),
                   "modes": [], "preferred": None}
            outputs.append(cur)
            continue
        m = re.match(r"^\s+(\d+x\d+)\s(.*)$", line)
        if m and cur is not None:
            cur["modes"].append(m.group(1))
            if "+" in m.group(2) and cur["preferred"] is None:
                cur["preferred"] = m.group(1)
    return outputs


def from_rects(rects):
    """[(id, x, y, w, h, primary)] (RandR 1.5 monitors, active outputs, ...) -> the monitor list (see the module doc).
    Zero-sized entries are dropped; entries with the same origin are a mirror and merge into one monitor."""
    groups = {}
    for name, x, y, w, h, primary in rects:
        x, y, w, h = int(x), int(y), int(w), int(h)
        if w <= 0 or h <= 0:
            continue
        g = groups.get((x, y))
        if g is None:
            groups[(x, y)] = {"id": str(name), "x": x, "y": y, "w": w, "h": h, "primary": bool(primary),
                              "outputs": [str(name)]}
        else:                                   # a mirror: the area all of them show
            g["w"], g["h"] = min(g["w"], w), min(g["h"], h)
            g["primary"] = g["primary"] or bool(primary)
            g["outputs"].append(str(name))
            if primary:
                g["id"] = str(name)
    mons = list(groups.values())
    if not mons:
        return []
    prim = [m for m in mons if m["primary"]]
    keep = prim[0] if prim else min(mons, key=lambda m: (m["y"], m["x"]))
    for m in mons:
        m["primary"] = m is keep
    mons.sort(key=lambda m: (not m["primary"], m["x"], m["y"]))
    return mons


def from_outputs(outputs):
    """parse_xrandr() outputs -> monitors: every connected output that shows something"""
    rects = []
    for o in outputs:
        if not o.get("connected") or not o.get("resolution") or not o.get("position"):
            continue
        w, h = (int(v) for v in o["resolution"].split("x"))      # --query's geometry is already rotated
        rects.append((o["output"], o["position"][0], o["position"][1], w, h, o.get("primary")))
    return from_rects(rects)


def union(mons):
    """the bounding box {x, y, w, h} of the monitors (the X screen the one desktop covers); None without monitors"""
    if not mons:
        return None
    x0, y0 = min(m["x"] for m in mons), min(m["y"] for m in mons)
    x1, y1 = max(m["x"] + m["w"] for m in mons), max(m["y"] + m["h"] for m in mons)
    return {"x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0}


def dead_areas(mons):
    """the rectangles of the bounding box no monitor shows, as {x, y, w, h} — a grid over every monitor edge, cells
    merged along rows (top-to-bottom, left-to-right): deterministic, the JS twin gives the same list"""
    u = union(mons)
    if not u:
        return []
    xs = sorted({u["x"], u["x"] + u["w"]} | {m["x"] for m in mons} | {m["x"] + m["w"] for m in mons})
    ys = sorted({u["y"], u["y"] + u["h"]} | {m["y"] for m in mons} | {m["y"] + m["h"] for m in mons})
    out = []
    for j in range(len(ys) - 1):
        run = None
        for i in range(len(xs) - 1):
            cx, cy = (xs[i] + xs[i + 1]) / 2, (ys[j] + ys[j + 1]) / 2
            dead = monitor_at(mons, cx, cy) < 0
            if dead and run is not None:
                run["w"] = xs[i + 1] - run["x"]
            elif dead:
                run = {"x": xs[i], "y": ys[j], "w": xs[i + 1] - xs[i], "h": ys[j + 1] - ys[j]}
                out.append(run)
            else:
                run = None
    # merge vertically: the same x span directly under the previous one
    merged = []
    for r in out:
        prev = next((p for p in merged if p["x"] == r["x"] and p["w"] == r["w"] and p["y"] + p["h"] == r["y"]), None)
        if prev:
            prev["h"] += r["h"]
        else:
            merged.append(dict(r))
    return merged


def monitor_at(mons, x, y):
    """index of the monitor that shows root pixel (x, y), -1 for a dead area / outside"""
    for i, m in enumerate(mons):
        if m["x"] <= x < m["x"] + m["w"] and m["y"] <= y < m["y"] + m["h"]:
            return i
    return -1


def nearest(mons, x, y):
    """index of the monitor closest to (x, y) (0 when it is on one): where something in a dead area belongs"""
    best, bi = None, 0
    for i, m in enumerate(mons):
        dx = max(m["x"] - x, 0, x - (m["x"] + m["w"] - 1))
        dy = max(m["y"] - y, 0, y - (m["y"] + m["h"] - 1))
        d = dx * dx + dy * dy
        if best is None or d < best:
            best, bi = d, i
    return bi


def hint(mons):
    """the start hint .xinitrc puts in the kiosk URL (?mon=): id:x,y,w,h,p;… — shell/monitors.js parseHint() reads it"""
    return ";".join(f"{m['id']}:{m['x']},{m['y']},{m['w']},{m['h']},{1 if m['primary'] else 0}" for m in mons)


# ------------------------------------------------------------------------------------- RandR 1.4 providers [R91]
PROVIDER = re.compile(r"^Provider (\d+): id: (0x[0-9a-fA-F]+) cap: (0x[0-9a-fA-F]+)(.*?) crtcs: (\d+) outputs: (\d+) "
                      r"associated providers: (\d+) name:(.*)$")


def parse_providers(text):
    """`xrandr --listproviders` -> [{index, id, caps, crtcs, outputs, associated, name}]: the main screen's GPU and
    every GPU screen (an NVIDIA one is "NVIDIA-G0"). A provider with the "Sink Output" capability can show the main
    provider's picture on its own outputs once it is bound as that provider's output source ("reverse PRIME",
    docs/HDMI-PRIME-2026-10-22.md); `associated` counts the bindings it has."""
    provs = []
    for line in text.splitlines():
        m = PROVIDER.match(line.strip())
        if not m:
            continue
        provs.append({"index": int(m.group(1)), "id": m.group(2),
                      "caps": [c.strip() for c in m.group(4).split(",") if c.strip()],
                      "crtcs": int(m.group(5)), "outputs": int(m.group(6)), "associated": int(m.group(7)),
                      "name": m.group(8).strip()})
    return provs


def provider_bind_plan(provs):
    """[(sink name, source name, sink id, source id)] for every provider that can be an output sink, is not the main
    provider (the first with "Source Output") and is not bound yet — the `xrandr --setprovideroutputsource SINK
    SOURCE` calls that make its outputs part of the X screen. By XID: a provider name may carry spaces."""
    src = next((p for p in provs if "Source Output" in p["caps"]), None)
    if not src:
        return []
    return [(p["name"], src["name"], p["id"], src["id"]) for p in provs
            if p is not src and "Sink Output" in p["caps"] and p["associated"] == 0]


def providers_summary(provs):
    """one line for the x.log breadcrumb (`xinitrc: providers: …`): `NAME bound|unbound (caps); …` — "NVIDIA-G0 bound"
    is what kairos-hdmi-prime takes as proof of an attached NVIDIA GPU screen"""
    return "; ".join(f"{p['name']} {'bound' if p['associated'] else 'unbound'} "
                     f"({', '.join(c.lower().replace(' ', '-') for c in p['caps']) or 'no caps'})" for p in provs) or "none"
