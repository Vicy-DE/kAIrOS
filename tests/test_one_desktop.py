#!/usr/bin/env python3
"""kAIrOS R90 — one desktop across all monitors (docs/ONE-DESKTOP-2026-10-19.md).

Standalone: `python3 tests/test_one_desktop.py` (run-tests.sh runs it after the suite). Writes
tests/results-one-desktop.json (not for a KAIROS_TEST_ONLY subset).
* no X needed: the monitor math (kernel/monitors.py and shell/monitors.js on the same layouts — the JS through node or
  QuickJS, skipped without both), the window rules (placement, the title-bar clamp out of dead areas, snap zones,
  maximise), the layout-change path (relocation), the display API on mock-xrandr layouts (2 and 3 monitors of different
  sizes and offsets, stacked, mirrored), the X side's hot-plug plan, .xinitrc's ONE launch line (a fake chromium) and the
  boot frame per monitor, rc.xml + the policy, the window layer's monitor list;
* the e2e test runs on a REAL X server (Xvfb 4480x1440 + openbox with the kiosk's rc.xml + the X side
  kairos-desktop.py watch + the kiosk Chromium launched with .xinitrc's flags) on the REAL shell served by the kernel's
  routes in-process; Xvfb has one output and ignores `xrandr --setmonitor`, so the kernel's RandR view is replaced by the
  card run's two monitors (1920x1080 primary + 2560x1440 right of it) and a hot-plug is a change of that view. Real
  pointer input through XTEST, the page read over the DevTools protocol. Skips without Xvfb / openbox / chromium /
  python3-xlib (bash dev/docker/build.sh x11 -> kairos-fix-x11:local). KAIROS_SHOTS=<dir> saves X screenshots.
"""
import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "kernel"))
sys.path.insert(0, str(REPO / "tests"))
import monitors as monmath  # noqa: E402

RESULTS = {}
SHOTS = os.environ.get("KAIROS_SHOTS", "")
INC = REPO / "iso" / "config" / "includes.chroot"
XINITRC = INC / "home" / "kairos" / ".xinitrc"
RC_XML = INC / "home" / "kairos" / ".config" / "openbox" / "rc.xml"
DESKTOP_PY = REPO / "scripts" / "desktop" / "kairos-desktop.py"
MOCK_XRANDR = REPO / "tests" / "mock-xrandr"
CARD = [("eDP", 0, 0, 1920, 1080, True), ("DisplayPort-0", 1920, 0, 2560, 1440, False)]


def record(name, reqs, status, detail=""):
    RESULTS[name] = {"reqs": reqs, "status": status, "detail": detail[:300]}
    mark = {"pass": "✓", "fail": "✗", "skip": "~"}[status]
    print(f"  {mark} {name} [{', '.join(reqs)}] {detail[:200]}", flush=True)


class SkipTest(Exception):
    pass


TESTS = []


def test(name, reqs, group=None):
    def deco(fn):
        fn._name, fn._reqs, fn._group = name, reqs, group
        TESTS.append(fn)
        return fn
    return deco


async def run_one(fn, ctx):
    try:
        detail = fn(ctx)
        if asyncio.iscoroutine(detail):
            detail = await detail
        record(fn._name, fn._reqs, "pass", detail or "")
        return True
    except SkipTest as e:
        record(fn._name, fn._reqs, "skip", str(e))
    except Exception as e:
        import traceback
        tb = traceback.extract_tb(e.__traceback__)
        where = next((f.lineno for f in reversed(tb) if f.filename == __file__), tb[-1].lineno)
        record(fn._name, fn._reqs, "fail", f"{type(e).__name__}: {str(e)[:600]} @ {where}")
        traceback.print_exc()
    return False


# ----------------------------------------------------------------------------- shell/monitors.js through node / QuickJS
def js(code):
    """run `code` after shell/monitors.js (KMon) in node or QuickJS; the code prints ONE JSON line -> its value"""
    eng = shutil.which("node") or shutil.which("qjs")
    if not eng:
        raise SkipTest("needs node or qjs (QuickJS) for shell/monitors.js")
    src = (REPO / "shell" / "monitors.js").read_text() + "\n;\n" + code
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
        f.write(src)
    try:
        r = subprocess.run([eng, f.name], capture_output=True, text=True, timeout=30)
    finally:
        os.unlink(f.name)
    assert r.returncode == 0, (eng, r.stderr[-800:])
    return json.loads(r.stdout.strip().splitlines()[-1])


def jsmons(rects):
    return json.dumps([{"id": i, "x": x, "y": y, "w": w, "h": h, "primary": p} for i, x, y, w, h, p in rects])


LAYOUTS = {
    # the card run: a 1080p panel + a 1440p monitor right of it -> a 1920x360 dead area under the panel
    "card": CARD,
    # three monitors of different sizes and offsets
    "three": [("eDP", 0, 360, 1920, 1080, True), ("DisplayPort-0", 1920, 0, 2560, 1440, False),
              ("DisplayPort-1", 4480, 0, 1280, 1024, False)],
    # stacked: the 1440p monitor above the panel, the panel (primary) offset 320
    "stacked": [("DisplayPort-0", 0, 0, 2560, 1440, False), ("eDP", 320, 1440, 1920, 1080, True)],
    # mirrored (same origin, different sizes): ONE monitor, the area both show
    "mirror": [("eDP", 0, 0, 1920, 1080, False), ("DisplayPort-0", 0, 0, 2560, 1440, True)],
    # no primary reported: the top-left one becomes it
    "noprimary": [("B", 1920, 0, 1920, 1080, False), ("A", 0, 0, 1920, 1080, False)],
    "single": [("eDP", 0, 0, 1920, 1080, True)],
}


@test("od_monitor_math_py_js", ["R90", "R53"])
def t_math(ctx):
    """kernel/monitors.py and shell/monitors.js agree on every layout (normalised list, union, dead areas, which
    monitor / nearest for probe points, the ?mon= hint round trip), and the key cases are right: the card run's dead
    area is 1920x360 at (0,1080); three monitors; stacked (primary below, offset); a mirror is ONE monitor of the
    smaller size; no primary -> the top-left"""
    probes = [(10, 10), (100, 1200), (2000, 1200), (4479, 0), (5000, 900), (320, 1500), (-5, -5), (1919, 1079)]
    py = {}
    for k, rects in LAYOUTS.items():
        m = monmath.from_rects(rects)
        py[k] = {"mons": [{kk: v for kk, v in x.items() if kk != "outputs"} for x in m], "union": monmath.union(m),
                 "dead": monmath.dead_areas(m), "at": [monmath.monitor_at(m, x, y) for x, y in probes],
                 "near": [monmath.nearest(m, x, y) for x, y in probes], "hint": monmath.hint(m)}
    code = "const L = " + json.dumps({k: json.loads(jsmons(v)) for k, v in LAYOUTS.items()}) + ";\n" + \
           "const P = " + json.dumps(probes) + ", H = " + json.dumps({k: v["hint"] for k, v in py.items()}) + ";\n" + """
const out = {};
for (const k of Object.keys(L)) {
  const m = KMon.normalize(L[k]);
  out[k] = { mons: m, union: KMon.union(m), dead: KMon.deadAreas(m), at: P.map(([x, y]) => KMon.at(m, x, y)),
             near: P.map(([x, y]) => KMon.nearest(m, x, y)), hint: KMon.parseHint(H[k]) };
}
console.log(JSON.stringify(out));"""
    j = js(code)
    for k in LAYOUTS:
        for key in ("mons", "union", "dead", "at", "near"):
            assert j[k][key] == py[k][key], (k, key, j[k][key], py[k][key])
        assert j[k]["hint"] == py[k]["mons"], (k, "hint round trip", j[k]["hint"], py[k]["mons"])
    assert py["card"]["dead"] == [{"x": 0, "y": 1080, "w": 1920, "h": 360}] and py["card"]["union"] == {"x": 0, "y": 0, "w": 4480, "h": 1440}
    assert py["card"]["at"][:3] == [0, -1, 1], py["card"]["at"]
    assert [m["id"] for m in py["three"]["mons"]] == ["eDP", "DisplayPort-0", "DisplayPort-1"]
    assert {"x": 0, "y": 0, "w": 1920, "h": 360} in py["three"]["dead"] and {"x": 4480, "y": 1024, "w": 1280, "h": 416} in py["three"]["dead"], py["three"]["dead"]
    assert py["stacked"]["mons"][0]["id"] == "eDP" and py["stacked"]["union"] == {"x": 0, "y": 0, "w": 2560, "h": 2520}
    assert py["mirror"]["mons"] == [{"id": "DisplayPort-0", "x": 0, "y": 0, "w": 1920, "h": 1080, "primary": True}], py["mirror"]
    assert py["noprimary"]["mons"][0]["id"] == "A" and py["noprimary"]["mons"][0]["primary"]
    assert py["card"]["hint"] == "eDP:0,0,1920,1080,1;DisplayPort-0:1920,0,2560,1440,0"
    return (f"{len(LAYOUTS)} layouts x (list, union, dead areas, {len(probes)} probes, hint) identical in py and "
            f"{os.path.basename(shutil.which('node') or shutil.which('qjs'))}; card dead area 1920x360@(0,1080); mirror = 1 monitor")


@test("od_window_rules", ["R90"])
def t_rules(ctx):
    """the window rules (shell/monitors.js): a new window lands in the target monitor's work area (the primary's minus
    the menu bar and the dock); a title bar dropped in the dead area under the panel moves up onto it (the shortest
    way; above the dock's band when the dock's room is given), one across the seam of two monitors stays, one under the
    menu bar comes down; a too-wide drop goes into one
    monitor; snap zones are the OUTER edges only (the seam between monitors is a doorway); left/right half and
    maximise fill the window's own monitor, only the primary leaves the dock's room; ofRect picks the title bar's
    monitor; three monitors: the dead strip above the lowered panel; the page origin (the X screen's origin once the page
    spans it, whatever window.screenX says)"""
    code = "const C = " + jsmons(CARD) + ", T = " + jsmons(LAYOUTS["three"]) + ";\n" + """
const c = KMon.normalize(C), t = KMon.normalize(T);
console.log(JSON.stringify({
  placeP: KMon.placeNew(c, 0, 720, 520, 1, 98), placeS: KMon.placeNew(c, 1, 720, 520, 1, 0),
  placeBig: KMon.placeNew(c, 0, 5000, 3000, 0, 98),
  dead: KMon.clampTitle(c, {x: 300, y: 1200, w: 700, h: 400}),
  deadDock: KMon.clampTitle(c, {x: 300, y: 1200, w: 700, h: 400}, 98),
  dockBand: KMon.clampTitle(c, {x: 300, y: 1000, w: 700, h: 400}, 98),
  deadDip: KMon.clampTitle(c, {x: 1500, y: 1070, w: 700, h: 400}),
  seam: KMon.clampTitle(c, {x: 1700, y: 300, w: 700, h: 400}),
  menu: KMon.clampTitle(c, {x: 300, y: 10, w: 700, h: 400}),
  off: KMon.clampTitle(c, {x: 5000, y: 300, w: 700, h: 400}),
  wide: KMon.clampTitle(c, {x: -100, y: 1300, w: 3000, h: 400}),
  threeTop: KMon.clampTitle(t, {x: 300, y: 100, w: 600, h: 400}),
  snaps: [[1919, 500], [1920, 500], [4479, 700], [0, 500], [3000, 0], [500, 0], [1000, 1079], [2500, 1439]].map(([x, y]) => KMon.snapZone(c, x, y)),
  left: KMon.snapRect(c, 0, "left", 98), right: KMon.snapRect(c, 1, "right", 98), maxP: KMon.snapRect(c, 0, "top", 98),
  maxS: KMon.snapRect(c, 1, "top", 98),
  of: [KMon.ofRect(c, {x: 1800, y: 100, w: 400, h: 300}), KMon.ofRect(c, {x: 1500, y: 100, w: 400, h: 300}),
       KMon.ofRect(c, {x: 200, y: 1300, w: 400, h: 100})],
  // the page origin: spanned over the X screen = the origin even with a stale screenX; clamped to monitor 2 = its window
  origin: [KMon.pageOrigin(c, {innerWidth: 4480, innerHeight: 1440, outerWidth: 4480, outerHeight: 1440, screenX: 1920, screenY: 0, devicePixelRatio: 1}),
           KMon.pageOrigin(c, {innerWidth: 2560, innerHeight: 1440, outerWidth: 2560, outerHeight: 1440, screenX: 1920, screenY: 0, devicePixelRatio: 1}),
           KMon.pageOrigin(c, {innerWidth: 1000, innerHeight: 700, outerWidth: 1000, outerHeight: 740, screenX: 50, screenY: 60, devicePixelRatio: 2})],
}));"""
    j = js(code)
    p, s2 = j["placeP"], j["placeS"]
    assert p["x"] >= 0 and p["y"] >= 30 and p["x"] + p["w"] <= 1920 and p["y"] + p["h"] <= 1080 - 98, p
    assert s2["x"] >= 1920 and s2["y"] >= 0 and s2["x"] + s2["w"] <= 4480 and (s2["x"], s2["y"]) == (1920 + 90 + 36, 40 + 32), s2
    assert j["placeBig"] == {"x": 60, "y": 70, "w": 1920 - 60, "h": 1080 - 30 - 98 - 40}, j["placeBig"]
    assert j["dead"] == {"x": 300, "y": 1080 - 34, "moved": True}, j["dead"]
    assert j["deadDip"] == {"x": 1500, "y": 1046, "moved": True}, j["deadDip"]
    # the dock's band at the primary's bottom (98 px) counts as off-limits too: the bar ends above the dock
    assert j["deadDock"] == {"x": 300, "y": 1080 - 98 - 34, "moved": True} and j["dockBand"]["y"] == 1080 - 98 - 34, (j["deadDock"], j["dockBand"])
    assert j["seam"] == {"x": 1700, "y": 300, "moved": False} and j["menu"] == {"x": 300, "y": 30, "moved": True}, (j["seam"], j["menu"])
    assert j["off"]["x"] == 4480 - 700 and j["off"]["y"] == 300, j["off"]
    # 3000 px wide, title bar in the dead area and off the left edge: the nearest place where the bar lies on monitors
    # entirely straddles the seam (0..1920 on the panel, the rest on the 1440p monitor) at the panel's bottom
    assert j["wide"] == {"x": 0, "y": 1046, "moved": True}, j["wide"]
    assert j["threeTop"]["y"] == 360 + 30, j["threeTop"]
    assert [z and z["zone"] for z in j["snaps"]] == [None, None, "right", "left", "top", "top", None, None], j["snaps"]
    assert j["left"] == {"x": 0, "y": 30, "w": 960, "h": 1080 - 30 - 98} and j["right"] == {"x": 1920 + 1280, "y": 0, "w": 1280, "h": 1440}
    assert j["maxP"] == {"x": 0, "y": 30, "w": 1920, "h": 952} and j["maxS"] == {"x": 1920, "y": 0, "w": 2560, "h": 1440}
    assert j["of"] == [1, 0, 0], j["of"]
    assert j["origin"] == [{"x": 0, "y": 0, "s": 1}, {"x": 1920, "y": 0, "s": 1}, {"x": 50, "y": 100, "s": 2}], j["origin"]
    return ("new window in the target's work area; dead-area drop -> y 1046 on the panel, seam kept, menu bar -> y 30, "
            "too wide -> straddles the seam; snap only at outer edges; maximise = own monitor (primary minus 30 + dock)")


@test("od_layout_change_relocate", ["R90"])
def t_relocate(ctx):
    """the layout-change path (shell/monitors.js relocate + clampTitle, what applyMonitors does to every window): a
    window on an unplugged monitor goes to the primary at the same offset, made to fit; a window on a monitor that
    stays keeps its place; a window on a monitor that MOVED (side by side -> stacked) moves with it; a re-plug puts
    nothing back on its own (no window is lost or hidden)"""
    code = "const C = " + jsmons(CARD) + ", S = " + jsmons(LAYOUTS["stacked"]) + ";\n" + """
const c = KMon.normalize(C), only = KMon.normalize([C[0]]), s = KMon.normalize(S);
const fit = (b, a, r) => { const n = KMon.relocate(b, a, r); const k = KMon.clampTitle(a, n); return Object.assign(n, {x: k.x, y: k.y}); };
console.log(JSON.stringify({
  gone: fit(c, only, {x: 2500, y: 600, w: 800, h: 600}),
  gonebig: fit(c, only, {x: 1920, y: 0, w: 2560, h: 1440}),
  stays: fit(c, only, {x: 100, y: 100, w: 800, h: 600}),
  moved: fit(c, s, {x: 2000, y: 100, w: 800, h: 600}),
  panel: fit(c, s, {x: 100, y: 100, w: 800, h: 600}),
  back: fit(only, c, {x: 580, y: 480, w: 800, h: 600}),
}));"""
    j = js(code)
    g = j["gone"]
    assert g["gone"] and g["to"] == "eDP" and (g["x"], g["y"], g["w"], g["h"]) == (580, 480, 800, 600), g
    gb = j["gonebig"]
    assert gb["x"] == 0 and gb["y"] == 30 and gb["w"] == 1920 and gb["h"] == 1050, gb
    assert (j["stays"]["x"], j["stays"]["y"], j["stays"]["gone"]) == (100, 100, False), j["stays"]
    m = j["moved"]       # DisplayPort-0 moved from (1920,0) to (0,0): the window keeps its offset on it
    assert (m["x"], m["y"], m["to"]) == (80, 100, "DisplayPort-0"), m
    p = j["panel"]       # eDP moved from (0,0) to (320,1440)
    assert (p["x"], p["y"], p["to"]) == (420, 1540, "eDP"), p
    assert (j["back"]["x"], j["back"]["y"]) == (580, 480), j["back"]
    return "unplug -> primary (580,480) same offset, oversized one fitted; stays; moved monitor carries its windows; re-plug moves nothing"


# ----------------------------------------------------------------------------- the display API (R53) on mock layouts
def hw_with(layout=None, query=None):
    import importlib
    hw = importlib.import_module("hw")
    td = Path(tempfile.mkdtemp(prefix="kairos-od-"))
    (td / "bin").mkdir()
    (td / "bin" / "xrandr").symlink_to(MOCK_XRANDR)
    hw._BIN = td / "bin"
    hw.DISPLAY_STATE = td / "cache" / "display.json"
    os.environ["MOCK_XRANDR_LOG"] = str(td / "xrandr.log")
    os.environ.pop("MOCK_XRANDR_QUERY", None)
    os.environ["MOCK_XRANDR_LAYOUT"] = layout or "2"
    if query:
        (td / "query.txt").write_text(query)
        os.environ["MOCK_XRANDR_QUERY"] = str(td / "query.txt")
    return hw, td


@test("od_display_api_monitors", ["R53", "R90"])
async def t_display(ctx):
    """vibe.hw.display.list on mock-xrandr: `monitors` (primary first, mirrors = one) and `desktop` (the X screen the
    one window covers) for the card run's layout, three monitors, stacked, mirrored; arrange "mirror" puts every output
    at ONE mode (the primary's native one when all offer it, else the largest common — not each its own --auto); switching the primary off hands primary to the next monitor;
    no change relaunches the kiosk (no reload flag, "relayout": "live"), the whole layout is saved for the next boot"""
    want = {"2mixed": (2, {"x": 0, "y": 0, "w": 4480, "h": 1440}, "eDP"),
            "3": (3, {"x": 0, "y": 0, "w": 5760, "h": 1440}, "eDP"),
            "stacked": (2, {"x": 0, "y": 0, "w": 2560, "h": 2520}, "eDP"),
            "mirror": (1, {"x": 0, "y": 0, "w": 1920, "h": 1080}, "eDP")}
    for lay, (n, desk, prim) in want.items():
        hw, td = hw_with(lay)
        d = await hw.display_list()
        assert d["available"] and len(d["monitors"]) == n and d["desktop"] == desk, (lay, d["monitors"], d["desktop"])
        assert d["monitors"][0]["primary"] and d["monitors"][0]["id"] == prim and d["multiscreen"] == (n > 1), (lay, d["monitors"])
    hw, td = hw_with("2mixed")
    r = await hw.display_set({"op": "arrange", "mode": "mirror"})
    log = (td / "xrandr.log").read_text().splitlines()[-1].split("\t")
    assert r == {"ok": True, "relayout": "live"}, r
    assert log == ["--output", "eDP", "--mode", "1920x1080", "--primary", "--output", "DisplayPort-0", "--mode", "1920x1080",
                   "--same-as", "eDP"], log
    hw, td = hw_with("2mixed")
    r = await hw.display_set({"output": "eDP", "off": True})
    sets = [ln.split("\t") for ln in (td / "xrandr.log").read_text().splitlines() if "--query" not in ln]
    assert r["ok"] and sets[0] == ["--output", "eDP", "--off", "--output", "DisplayPort-0", "--primary"], sets
    saved = json.loads(hw.DISPLAY_STATE.read_text())["xrandr_args"]
    assert "eDP" in saved and "DisplayPort-0" in saved, saved
    # the VM's virtual outputs both offer modes up to 5120x2160: the mirror takes the primary's native mode
    vm = [{"output": "Virtual-1", "primary": True, "modes": ["1920x1080", "5120x2160", "2560x1440"], "preferred": "1920x1080"},
          {"output": "Virtual-2", "primary": False, "modes": ["2560x1440", "5120x2160", "1920x1080"], "preferred": "2560x1440"}]
    assert hw._common_mode(vm) == "1920x1080" and hw._common_mode([vm[0], dict(vm[1], modes=["2560x1440", "5120x2160"])]) == "5120x2160"
    assert monmath.parse_xrandr(subprocess.run([str(MOCK_XRANDR), "--query"], capture_output=True, text=True,
                                env=dict(os.environ, MOCK_XRANDR_LAYOUT="2mixed", MOCK_XRANDR_QUERY="")).stdout)[1]["preferred"] == "2560x1440"
    src = (REPO / "kernel" / "hw.py").read_text()
    assert "KIOSK_RELOAD" not in src and "_touch_reload" not in src and "reloading_kiosk" not in src, "the display API relaunches nothing"
    return "monitors/desktop for 2mixed 4480x1440, 3 monitors 5760x1440, stacked 2560x2520, mirror = 1; mirror at the common 1920x1080; primary off -> DisplayPort-0 primary; no reload"


@test("od_hotplug_plan", ["R90"])
def t_hotplug(ctx):
    """the X side's hot-plug layout (scripts/desktop/kairos-desktop.py plan_layout): an unplugged monitor that still
    shows a picture goes off and the rest is packed from x 0 with the primary kept; a monitor plugged in goes right of
    the others with its preferred mode; the saved layout (the display API's) is replayed when it names exactly the
    connected outputs; an output the user switched off stays off; the primary unplugged -> the first output is it"""
    import importlib.util
    spec = importlib.util.spec_from_file_location("kdesk", DESKTOP_PY)
    kd = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(kd)
    q = lambda lay: subprocess.run([str(MOCK_XRANDR), "--query"], capture_output=True, text=True,
                                   env=dict(os.environ, MOCK_XRANDR_LAYOUT=lay, MOCK_XRANDR_QUERY="")).stdout
    out = lambda lay: monmath.parse_xrandr(q(lay))
    a, why = kd.plan_layout(out("unplugged"))
    assert why == "extend" and a == ["--output", "eDP", "--mode", "1920x1080", "--pos", "0x0", "--primary",
                                     "--output", "DisplayPort-0", "--off"], a
    a, why = kd.plan_layout(out("plugged"))
    assert a == ["--output", "eDP", "--mode", "1920x1080", "--pos", "0x0", "--primary",
                 "--output", "DisplayPort-0", "--auto", "--right-of", "eDP"], a
    saved = ["--output", "eDP", "--mode", "1920x1080", "--pos", "320x1440", "--rotate", "normal", "--primary",
             "--output", "DisplayPort-0", "--mode", "2560x1440", "--pos", "0x0", "--rotate", "normal"]
    a, why = kd.plan_layout(out("plugged"), saved)
    assert why == "the saved layout" and a == saved, (why, a)
    a, why = kd.plan_layout(out("plugged"), ["--output", "eDP", "--auto", "--output", "DisplayPort-0", "--off", "--output", "HDMI-1", "--auto"])
    assert why == "extend" and "DisplayPort-0" not in a, ("a user-off output stays off", a)
    prim_gone = monmath.parse_xrandr(q("2mixed").replace("eDP connected primary", "eDP disconnected"))
    a, why = kd.plan_layout(prim_gone)
    assert a[:7] == ["--output", "DisplayPort-0", "--mode", "2560x1440", "--pos", "0x0", "--primary"] and a[-3:] == ["--output", "eDP", "--off"], a
    return "unplug -> off + repacked, plug -> right-of with --auto, saved layout replayed on the same set, user-off kept off, primary hand-off"


@test("od_xinitrc_one_kiosk", ["R90", "R73", "R28", "R52"])
def t_xinit(ctx):
    """.xinitrc launches ONE kiosk over the X screen (R52's per-monitor kiosks are gone): an --app window of class
    kairos-desktop at 0,0 with the union's size (4480x1440 for the card run, 5760x1440 for three monitors), never
    --kiosk while the size is known, the ?mon= hint (kernel/monitors.py, via kairos-desktop.py, else the awk fallback on
    xrandr --query) + the boot params on the boot page, a profile without Chromium's title bar; openbox-session without
    arguments; the watcher is started;
    the root cover renders the boot frame per monitor; no ?screen= anywhere"""
    xi = XINITRC.read_text()
    subprocess.run(["sh", "-n", str(XINITRC)], check=True)
    funcs = "\n".join(re.search(rf"^{f}\(\) \{{\n.*?^\}}\n", xi, re.S | re.M).group(0)
                      for f in ("monitor_hint", "desktop_size", "launch_all_kiosks"))
    flags = re.search(r'CHROME_FLAGS="([^"]*)"', xi, re.S).group(0)
    res = {}
    for lay, desk in (("2mixed", (4480, 1440)), ("3", (5760, 1440))):
        for via in ("script", "awk"):
            with tempfile.TemporaryDirectory() as td:
                t = Path(td)
                (t / "bin").mkdir()
                (t / "bin" / "xrandr").symlink_to(MOCK_XRANDR)
                (t / "bin" / "chromium").write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$CHROME_LOG"\n')
                (t / "bin" / "chromium").chmod(0o755)
                (t / "bin" / "xsetroot").write_text("#!/bin/sh\nexit 0\n"); (t / "bin" / "xsetroot").chmod(0o755)
                env = dict(os.environ, PATH=f"{t / 'bin'}:{os.environ.get('PATH', '')}", MOCK_XRANDR_LAYOUT=lay,
                           MOCK_XRANDR_QUERY="", CHROME_LOG=str(t / "chrome.log"), DISPLAY=":4242")
                desk_py = str(DESKTOP_PY) if via == "script" else "/nonexistent/kairos-desktop.py"
                script = (f'{flags}\nDESK={desk_py}\nPTR_DIAG=1\nKIOSK_URL=file:///opt/vibeos/os/shell/boot-wait.html\n'
                          f'BOOT_QS="&boot=1&p=0.6"\n{funcs}\nlaunch_all_kiosks\nwait\necho "PIDS:$KIOSK_PIDS"\n')
                r = subprocess.run(["sh", "-c", script], env=env, capture_output=True, text=True, timeout=60)
                assert r.returncode == 0, r.stderr
                launches = (t / "chrome.log").read_text().splitlines()
                res[(lay, via)] = (launches, r.stdout)
    for (lay, via), (launches, stdout) in res.items():
        assert len(launches) == 1, (lay, via, "ONE kiosk", launches)
        a = launches[0]
        w, h = (4480, 1440) if lay == "2mixed" else (5760, 1440)
        assert "--kiosk" not in a.split() and "--class=kairos-desktop" in a and "--window-position=0,0" in a, a
        # [fix 2026-10-22] no --incognito (it ignores the GTK theme: the #3c3c3c pre-paint frame); a fresh profile instead
        assert f"--window-size={w},{h}" in a and "--user-data-dir=/tmp/kiosk-0" in a and "--incognito" not in a, a
        hint = monmath.hint(monmath.from_outputs(monmath.parse_xrandr(subprocess.run(
            [str(MOCK_XRANDR), "--query"], capture_output=True, text=True,
            env=dict(os.environ, MOCK_XRANDR_LAYOUT=lay, MOCK_XRANDR_QUERY="")).stdout)))
        assert a.endswith(f"--app=file:///opt/vibeos/os/shell/boot-wait.html?mon={hint}&boot=1&p=0.6"), (lay, via, a)
        assert f"one desktop over {w}x{h}: {hint}" in stdout, stdout
    assert "screen=" not in xi and "kiosk_geoms" not in xi and xi.count("chromium --kiosk ") == 1, "R52's per-monitor launch removed"
    # openbox-session takes NO arguments (Debian's wrapper prints its syntax and exits — measured in the VM: no WM)
    code = "\n".join(l for l in xi.splitlines() if not l.lstrip().startswith("#"))
    assert re.search(r"^openbox-session &$", code, re.M) and not re.search(r"openbox-session [^&\n]", code)
    assert '"custom_chrome_frame":false' in xi and "/tmp/kiosk-0/Default/Preferences" in xi
    # [fix 2026-10-22] the profile is wiped before EVERY launch (nothing survives a relaunch, as with incognito) and
    # follows the GTK theme (the pre-paint colour = gtk.css's view background); proven on the launch function's output
    lk = xi[xi.index("launch_all_kiosks() {"):xi.index("# [R73/R28] the painter's picture on the root window")]
    assert lk.index("rm -rf /tmp/kiosk-0\n") < lk.index("mkdir -p /tmp/kiosk-0/Default") < lk.index("chromium $CHROME_FLAGS")
    assert "--incognito" not in flags
    with tempfile.TemporaryDirectory() as td:
        pre = lk[lk.index("rm -rf /tmp/kiosk-0"):lk.index("    if [ \"$W\" -gt 0 ]")].replace("/tmp/kiosk-0", f"{td}/kiosk-0")
        Path(td, "kiosk-0", "Default").mkdir(parents=True)
        Path(td, "kiosk-0", "Default", "Cookies").write_text("x")              # a previous launch's state
        subprocess.run(["sh", "-c", pre], check=True, timeout=10)
        assert sorted(p.name for p in Path(td, "kiosk-0").rglob("*")) == ["Default", "Preferences"]
        prefs = json.loads(Path(td, "kiosk-0", "Default", "Preferences").read_text())
        assert prefs == {"browser": {"custom_chrome_frame": False}, "extensions": {"theme": {"system_theme": 1, "use_system": True}}}, prefs
    assert '( while [ -f "$DESK" ]; do python3 "$DESK" watch; sleep 2; done ) &' in xi and "DESK=/opt/vibeos/os/scripts/desktop/kairos-desktop.py" in xi
    rc = xi[xi.index("root_cover() {"):xi.index("# liveness of the BROWSERS")]
    assert 'python3 "$DESK" monitors --rects' in rc and 'set -- "$@" "$d/frame$i.raw" "$w" "$h" "$x" "$y"' in rc and "root-cover.py \"$@\"" in rc
    import importlib.util
    spec = importlib.util.spec_from_file_location("root_cover", REPO / "scripts" / "splash" / "root-cover.py")
    rcm = importlib.util.module_from_spec(spec); spec.loader.exec_module(rcm)
    assert rcm.frames(["a.raw", "1920", "1080"]) == [("a.raw", 1920, 1080, 0, 0)]
    assert rcm.frames(["a.raw", "1920", "1080", "0", "0", "b.raw", "2560", "1440", "1920", "0"]) == \
        [("a.raw", 1920, 1080, 0, 0), ("b.raw", 2560, 1440, 1920, 0)]
    return "one --app kiosk 4480x1440 / 5760x1440 at 0,0 with ?mon= (script + awk fallback), no --kiosk, watcher started, root cover per monitor"


@test("od_openbox_policy_contracts", ["R90", "R28", "R82"])
def t_rc(ctx):
    """rc.xml: every window is centred on the POINTER's monitor (not "active" — the rule that put R52's kiosks on one
    monitor); the kairos-desktop rule comes AFTER "*" (openbox merges matching rules in order) — undecorated, normal
    layer (above nothing), never fullscreen / maximized, forced to 0,0; the same class in .xinitrc and the X side;
    the Chromium policy forbids fullscreen (F11 would shrink the desktop to one monitor); GTK's window background (what
    Chromium shows between the boot page and the shell's first frame) is the boot screen's #0a0a14, not Adwaita's white;
    the shell has no R52 code"""
    import xml.dom.minidom as minidom
    dom = minidom.parse(str(RC_XML))
    apps = dom.getElementsByTagName("application")
    classes = [a.getAttribute("class") for a in apps]
    assert classes == ["*", "kairos-desktop"], classes
    star, desk = apps
    txt = lambda el, tag: el.getElementsByTagName(tag)[0].firstChild.nodeValue
    assert txt(star, "monitor") == "mouse", txt(star, "monitor")
    assert txt(desk, "decor") == "no" and txt(desk, "layer") == "normal" and txt(desk, "fullscreen") == "no" \
        and txt(desk, "maximized") == "no" and (txt(desk, "x"), txt(desk, "y")) == ("0", "0")
    assert desk.getElementsByTagName("position")[0].getAttribute("force") == "yes"
    assert "--class=kairos-desktop" in XINITRC.read_text() and 'KIOSK_CLASS = "kairos-desktop"' in DESKTOP_PY.read_text()
    pol = json.loads((INC / "etc" / "chromium" / "policies" / "managed" / "kairos-desktop.json").read_text())
    assert pol == {"FullscreenAllowed": False}, pol
    # [R28] the window's colour between the boot page and the shell's first frame: GTK's window background, made dark
    for v in ("3.0", "4.0"):
        css = (INC / "home" / "kairos" / ".config" / f"gtk-{v}" / "gtk.css").read_text()
        assert re.search(r"window[^{]*\{[^}]*background-color: #0a0a14", css), v
        # [fix 2026-10-22] the web area before a page's first frame: GTK's VIEW background (the kiosk follows the GTK theme)
        assert re.search(r"^\.view, textview[^{]*\{[^}]*background-color: #0a0a14", css, re.M), v
    shell = (REPO / "shell" / "shell.js").read_text() + (REPO / "shell" / "games.js").read_text()
    assert "SCREEN" not in shell and "screen-secondary" not in (REPO / "shell" / "shell.css").read_text()
    html = (REPO / "shell" / "index.html").read_text()
    assert html.index('src="/shell/monitors.js"') < html.index('<div id="boot">') < html.index("function kairosTiles(") \
        < html.index('src="/shell/shell.js"'), "the monitor math and the boot tiles before the first paint"
    bw = (REPO / "shell" / "boot-wait.html").read_text()
    assert '<script src="monitors.js"></script>' in bw and "KMon.parseHint(Q.get(\"mon\"))" in bw
    return "pointer monitor for every window, kiosk rule last (no decor, normal layer, no fullscreen/max, 0,0), FullscreenAllowed=false, no R52 code"


@test("od_tearfree_amdgpu_conf_and_bootlog", ["R90", "R51", "R79"])
def t_tearfree(ctx):
    """[fix 2026-10-24] the v7.5-4 card run's Xorg log: 77x "flip queue failed: Invalid argument" in 28 min with the one
    4480-px window over two CRTCs (0 in 13 h with the per-screen kiosks) — a refused Present flip falls back to a torn
    copy. 22-kairos-tearfree-amdgpu.conf turns the amdgpu DDX's TearFree on (a copy + a flip per CRTC of its own
    buffers) through an OutputClass that matches the amdgpu KERNEL driver only: one section, the Driver line kept, no
    other option, nothing for modesetting / NVIDIA, the other X files unchanged; build-img.sh installs it and the rig
    sync ships the whole etc overlay. The boot log's `pageflip` check RECORDS the TearFree state X applied and the
    refused flips of this session (the real kairos-bootlog on a fake ESP: the card run's lines -> auto / 77, a
    TearFree-on log -> on / 0, no amdgpu screen -> n/a; always PASS: a before -> after record, never a verdict change)"""
    xdir = INC / "etc" / "X11" / "xorg.conf.d"
    tf = xdir / "22-kairos-tearfree-amdgpu.conf"
    code = "\n".join(l for l in tf.read_text().splitlines() if not l.lstrip().startswith("#"))
    secs = re.findall(r'Section "(\w+)"(.*?)EndSection', code, re.S)
    assert len(secs) == 1 and secs[0][0] == "OutputClass", secs
    body = secs[0][1]
    assert re.search(r'MatchDriver\s+"amdgpu"', body) and re.search(r'^\s*Driver\s+"amdgpu"', body, re.M), body
    opts = re.findall(r'Option\s+"(\w+)"\s+"(\w+)"', body)
    assert opts == [("TearFree", "on")], opts
    assert "SWcursor" not in tf.read_text() or "SWcursor" not in body
    for name, needle in (("10-kairos-single-gpu.conf", r'Option\s+"AutoAddGPU"\s+"off"'), ("20-kairos-swcursor.conf", r'MatchDriver\s+"i915\|xe\|virtio_gpu\|bochs\|bochs-drm"')):
        assert re.search(needle, (xdir / name).read_text()), name
    assert "amdgpu" not in re.search(r'MatchDriver\s+"([^"]+)"', (xdir / "20-kairos-swcursor.conf").read_text()).group(1)
    assert 'install -D -m 644 "$INC/etc/X11/xorg.conf.d/22-kairos-tearfree-amdgpu.conf"' in (REPO / "iso" / "build-img.sh").read_text()
    assert 'tar -C "$REPO/iso/config/includes.chroot" -czf - etc usr home' in (REPO / "dev" / "rig" / "testrig" / "sync-guest.sh").read_text()
    assert (REPO / "docs" / "FIXES-2026-10-24.md").exists()
    # the boot log's record (the real script on a fake ESP, reason shutdown = no wait for a kiosk)
    bootlog = INC / "usr" / "local" / "sbin" / "kairos-bootlog"
    subprocess.run(["sh", "-n", str(bootlog)], check=True)
    body = bootlog.read_text()
    assert "XORGLOG=${KAIROS_BOOTLOG_XORGLOG:-}" in body and "TFCONF=${KAIROS_BOOTLOG_TFCONF:-/etc/X11/xorg.conf.d/22-kairos-tearfree-amdgpu.conf}" in body
    assert body.count("check pageflip PASS") == 2 and "check pageflip WARN" not in body and "check pageflip FAIL" not in body, "a record, never a verdict change"
    card = ("[    41.122] (==) AMDGPU(0): TearFree property default: auto\n[    41.122] (II) AMDGPU(0): KMS Pageflipping: enabled\n"
            + "".join(f"[   {66 + i}.301] (WW) AMDGPU(0): flip queue failed: Invalid argument\n[   {66 + i}.301] (WW) AMDGPU(0): Page flip failed: Invalid argument\n"
                      f"[   {66 + i}.301] (EE) AMDGPU(0): present flip failed\n" for i in range(77))
            + "[ 1680.100] (II) AMDGPU(0): EDID vendor \"CMN\", prod id 5678\n")
    on = ('[    28.962] (II) Applying OutputClass "kAIrOS TearFree (amdgpu)" options to /dev/dri/card0\n'
          '[    28.962] (**) AMDGPU(0): Option "TearFree" "on"\n[    41.122] (**) AMDGPU(0): TearFree property default: on\n'
          "[    41.122] (II) AMDGPU(0): KMS Pageflipping: enabled\n[  900.000] (II) AMDGPU(0): EDID vendor \"CMN\", prod id 5678\n")
    vm = "[     7.100] (II) modeset(0): glamor X acceleration enabled on llvmpipe\n[     7.200] (II) modeset(0): Output Virtual-1 connected\n"
    got = {}
    for tag, xorg, conf in (("card", card, tf), ("on", on, tf), ("vm", vm, Path("/nonexistent/22.conf"))):
        with tempfile.TemporaryDirectory() as td:
            t = Path(td); esp = t / "esp"; (t / "x.log").write_text("===== X attempt 1  now  uptime 7s =====\nxinitrc: started (pid 9, uptime 7s)\n"
                                                                    "xinitrc: displays: eDP connected primary 1920x1080+0+0;\n")
            (t / "x-attempts").write_text("1\n"); (t / "Xorg.0.log").write_text(xorg)
            env = dict(os.environ, KAIROS_BOOTLOG_ESP=str(esp), KAIROS_BOOTLOG_RUN=str(t / "run-attempt"), KAIROS_BOOTLOG_XLOG=str(t / "x.log"),
                       KAIROS_BOOTLOG_XCNT=str(t / "x-attempts"), KAIROS_BOOTLOG_PLANNED=str(t / "planned"), KAIROS_BOOTLOG_HDMI=str(t / "none.json"),
                       KAIROS_BOOTLOG_XORGLOG=str(t / "Xorg.0.log"), KAIROS_BOOTLOG_TFCONF=str(conf))
            subprocess.run([str(bootlog), "shutdown"], env=env, check=True, timeout=170, capture_output=True)
            j = json.loads((esp / "KAIROS-LOGS" / "latest.json").read_text())
            txt = (esp / "KAIROS-LOGS" / "latest.txt").read_text(errors="replace")
            got[tag] = (j["checks"]["pageflip"], txt)
    c, txt = got["card"]
    assert c["status"] == "PASS" and c["detail"].startswith("amdgpu TearFree auto (config present); refused page flips (flip queue failed) in this X session's log: 77, log up to uptime 1680s"), c
    assert "refused page flips in" in txt and "77 (flip queue failed), 77 (Page flip failed), 77 (present flip failed)" in txt, txt[-3000:]
    assert "66.301] (WW) AMDGPU(0): flip queue failed" in txt and "142.301] (WW) AMDGPU(0): flip queue failed" in txt, "first + last refused flip in the evidence"
    c, txt = got["on"]
    assert c["status"] == "PASS" and c["detail"].startswith("amdgpu TearFree on (config present); refused page flips (flip queue failed) in this X session's log: 0, log up to uptime 900s"), c
    assert 'OutputClass "kAIrOS TearFree (amdgpu)" options' in txt and 'Option "TearFree" "on"' in txt
    c, txt = got["vm"]
    assert c["status"] == "PASS" and c["detail"].startswith("n/a: no amdgpu X screen in ") and "config absent" in c["detail"], c
    return "22-kairos-tearfree-amdgpu.conf: one OutputClass, amdgpu only, TearFree on; build + sync lists; bootlog pageflip: card log auto/77 (+ first/last line), on/0, VM n/a — always PASS"


@test("od_wm_monitor_list", ["R90", "R68", "R70"])
def t_wm(ctx):
    """kernel/wm.py: the window layer's monitor list (the shell's live feed) — RandR monitors with their output names,
    a mirror merged, primary first; the snapshot carries `monitors` + `desktop`; kiosk_for() finds the ONE desktop
    window for every monitor by overlap (its centre may lie on another monitor); RandR events are selected"""
    import wm

    class Root:
        def get_geometry(self):
            return type("G", (), {"width": 4480, "height": 1440})()

    class D:
        def screen(self):
            return type("S", (), {"root": Root()})()

    class Fake(wm.EWMH):
        def clients(self):
            return [1, 2]

        def wm_class(self, xid):
            return ("127.0.0.1__", "kairos-desktop") if xid == 1 else ("pygame", "pygame")

        def pid(self, xid):
            return 100 + xid

        def geom(self, xid):
            return (0, 0, 4480, 1440, True) if xid == 1 else (2500, 100, 960, 540, True)

        def active(self):
            return 1

        def wm_name(self):
            return "Openbox"

        def title(self, xid):
            return "t"

        def window_types(self, xid):
            return set()

    saved = (wm._randr_monitors, wm._proc_name)
    wm._randr_monitors = lambda root: [(1920, 0, 2560, 1440, False, "DisplayPort-0"), (0, 0, 1920, 1080, True, "eDP"),
                                       (0, 0, 2560, 1440, False, "HDMI-A-0")]
    wm._proc_name = lambda pid, cache=None: "chromium" if pid == 101 else "python3"
    try:
        c = Fake(D())
        ml = c.monitor_list()
        assert [(m["id"], m["x"], m["w"], m["h"], m["primary"]) for m in ml] == [("eDP", 0, 1920, 1080, True),
                                                                                ("DisplayPort-0", 1920, 2560, 1440, False)], ml
        assert ml[0]["outputs"] == ["eDP", "HDMI-A-0"], "a mirror is one monitor"
        snap = c.snapshot()
        assert snap["monitors"] == ml and snap["desktop"] == {"x": 0, "y": 0, "w": 4480, "h": 1440} and snap["screens"][1] == [1920, 0, 2560, 1440]
        assert snap["windows"][0]["screen"] == 1, snap["windows"]
        for m in ml:
            assert c.kiosk_for((m["x"], m["y"], m["w"], m["h"]))[0] == 1, m
        wm._randr_monitors = lambda root: [(0, 0, 1920, 1080, True)]          # the tests' 5-tuples (no name)
        assert c.monitor_list()[0]["id"] == "monitor-0"
    finally:
        wm._randr_monitors, wm._proc_name = saved
    src = (REPO / "kernel" / "wm.py").read_text()
    assert "rr_events = _randr_watch(d, root)" in src and "RROutputChangeNotifyMask" in src
    return "names + mirror merge + primary first; snapshot monitors/desktop; kiosk_for = the one window on both monitors; RandR watched"


# ----------------------------------------------------------------------------- e2e: a real X server
@test("od_e2e_desktop_spans_monitors", ["R90", "R28", "R73"], group="e2e")
async def t_span(ctx):
    """the kiosk launched with .xinitrc's flags is ONE window at exactly (0, 0, 4480, 1440), class kairos-desktop,
    undecorated, not fullscreen; F11 (which a policy-less Chromium obeys: openbox makes it fullscreen on one monitor)
    is undone by the X side within a second; the page knows both monitors (hint, then the kernel's feed); the menu
    bar, the search, the dock and the taskbar lie on the primary, every monitor has its own wallpaper tile; the boot
    tiles are gone"""
    desk = ctx["desk"]
    x, page = desk.x, desk.pages[0]
    wins = x.chrome_windows()
    assert len(wins) == 1 and wins[0][1][:4] == (0, 0, 4480, 1440), wins
    xid = wins[0][0]
    inst, cls = x.c.wm_class(xid)
    states = x.c.states(xid)
    assert cls == "kairos-desktop" and not any("FULLSCREEN" in s for s in states), (cls, states)
    mons = await page.ev("MON.list.map((m) => [m.id, m.x, m.y, m.w, m.h, m.primary])")
    assert mons == [["eDP", 0, 0, 1920, 1080, True], ["DisplayPort-0", 1920, 0, 2560, 1440, False]], mons
    assert await page.ev("MON.src") == "x", "the kernel's feed confirmed the hint"
    mb, dock, ds = await page.rect("#menubar"), await page.rect("#dock"), await page.rect("#dsearch")
    assert mb == [0, 0, 1920, 30], mb
    assert dock[0] >= 0 and dock[0] + dock[2] <= 1920 and abs(dock[0] + dock[2] / 2 - 960) <= 1 and 1080 - 30 < dock[1] + dock[3] <= 1080 - 8, dock
    assert abs(ds[0] + ds[2] / 2 - 960) <= 1 and ds[1] == 44, ds
    tiles = await page.ev("[...document.querySelectorAll('#wallpaper .wp-tile')].map((t) => { const r = t.getBoundingClientRect(); return [r.left, r.top, r.width, r.height]; })")
    assert tiles == [[0, 0, 1920, 1080], [1920, 0, 2560, 1440]], tiles
    css = await page.ev("['--pm-x','--pm-y','--pm-w','--pm-h','--pm-r','--pm-b'].map((k) => getComputedStyle(document.documentElement).getPropertyValue(k).trim())")
    assert css == ["0px", "0px", "1920px", "1080px", "2560px", "360px"], css
    E2E["shot_desktop"] = x.shot("one-desktop-two-monitors.png")
    # F11: what openbox does to a window that asks for fullscreen — the X side puts the desktop back
    x.click(3000, 700)
    await asyncio.sleep(0.3)
    fixes = x.desk_log.read_text().count("(fullscreen")
    x.key("F11")
    seen, dt = await until(lambda: x.desk_log.read_text().count("(fullscreen") > fixes, 5, 0.05)
    assert seen, ("the X side never saw the fullscreen request", x.desk_log.read_text()[-400:])
    back, _ = await until(lambda: (x.geom(xid) or (0,))[:4] == (0, 0, 4480, 1440)
                          and not any("FULLSCREEN" in s for s in x.c.states(xid)), 5, 0.05)
    assert back, ("F11 left the desktop fullscreen", x.geom(xid), x.c.states(xid), x.desk_log.read_text()[-400:])
    return (f"one window 4480x1440+0+0 class kairos-desktop; menu bar {mb}, dock centred on the primary; wallpaper tiles {tiles}; "
            f"F11 undone in {dt:.2f} s")


async def win_rect(page, wid):
    return await page.ev(f"(() => {{ const w = windows.get('{wid}'); const r = w.el.getBoundingClientRect(); "
                         "return [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)]; })()")


async def drag(x, frm, to, steps=30):
    x.move(*frm)
    await asyncio.sleep(0.08)
    x.button(True)
    await asyncio.sleep(0.05)
    for k in range(1, steps + 1):
        x.move(frm[0] + (to[0] - frm[0]) * k / steps, frm[1] + (to[1] - frm[1]) * k / steps)
        await asyncio.sleep(0.016)
    await asyncio.sleep(0.1)
    x.button(False)
    await asyncio.sleep(0.3)


@test("od_e2e_windows_across_monitors", ["R90"], group="e2e")
async def t_windows(ctx):
    """normal-OS behaviour with REAL pointer input: a window opened with the pointer on the second monitor opens
    there; dragged by its title bar from the primary to the second monitor it lands there; ▢ maximise fills the second
    monitor (2560x1440 at 1920,0; on the primary it leaves the menu bar and the dock); dropped with its title bar in the
    dead area under the panel it is clamped onto the panel, above the dock; dropped with the pointer at the outer right edge it snaps to
    the right half of the second monitor"""
    desk = ctx["desk"]
    x, page = desk.x, desk.pages[0]
    x.move(3200, 800)
    await asyncio.sleep(0.3)
    w2 = await page.ev("openStudio('imagine').id")
    r = await win_rect(page, w2)
    assert r[0] >= 1920 and r[0] + r[2] <= 4480, ("opened on the pointer's monitor", r)
    x.move(500, 500)
    await asyncio.sleep(0.3)
    w1 = await page.ev("openStudio('melody').id")
    r = await win_rect(page, w1)
    assert r[0] + r[2] <= 1920 and r[1] >= 30, ("opened on the primary", r)
    # drag w1 from the primary to the second monitor
    bar = [r[0] + 60, r[1] + 17]
    await drag(x, bar, (bar[0] + 2200, bar[1] + 300))
    r2 = await win_rect(page, w1)
    assert r2[0] >= 1920 and abs(r2[0] - (r[0] + 2200)) <= 2 and abs(r2[1] - (r[1] + 300)) <= 2, ("dragged to monitor 2", r, r2)
    assert await page.ev(f"winMonitor(windows.get('{w1}'))") == 1
    # maximise on monitor 2, then on the primary
    await page.ev(f"windows.get('{w1}').el.querySelector('.win-max').click(), 1")
    rm = await win_rect(page, w1)
    assert rm == [1920, 0, 2560, 1440], ("maximised on its monitor", rm)
    E2E["shot_max2"] = x.shot("window-maximised-on-monitor-2.png")
    await page.ev(f"windows.get('{w1}').el.querySelector('.win-max').click(), 1")
    assert await win_rect(page, w1) == r2, "▢ again gives the old rect back"
    # drop the title bar in the dead area under the panel (x 300, y 1250): it ends ON the panel
    r = await win_rect(page, w1)
    bar = [r[0] + 60, r[1] + 17]
    await drag(x, bar, (360, 1250))
    rd = await win_rect(page, w1)
    room = await page.ev("dockRoom()")
    assert rd[0] < 1920 and rd[1] == 1080 - room - 34, ("clamped out of the dead area, above the dock", rd, room)
    E2E["shot_clamp"] = x.shot("window-clamped-out-of-the-dead-area.png")
    # snap: the pointer at the outer right edge of monitor 2
    bar = [rd[0] + 60, rd[1] + 17]
    await drag(x, bar, (4479, 700))
    rs = await win_rect(page, w1)
    assert rs == [1920 + 1280, 0, 1280, 1440], ("snapped to the right half of monitor 2", rs)
    await page.ev(f"windows.get('{w1}').el.querySelector('.win-max').click(), 1")    # un-snap
    # the primary: maximise leaves the menu bar and the dock
    await page.ev(f"(() => {{ const w = windows.get('{w2}'); setRect(w, {{x: 200, y: 200, w: 700, h: 500}}); w.el.querySelector('.win-max').click(); return 1; }})()")
    rp = await win_rect(page, w2)
    room = await page.ev("dockRoom()")
    assert rp[:3] == [0, 30, 1920] and rp[3] == 1080 - 30 - room and room > 60, (rp, room)
    await page.ev(f"windows.get('{w2}').el.querySelector('.win-max').click(), 1")
    E2E["w"] = (w1, w2)
    return (f"opened on the pointer's monitor; dragged primary -> monitor 2 {r2}; maximised {rm}; dead-area drop -> {rd}; "
            f"snap right half {rs}; primary maximise {rp} (dock room {room})")


@test("od_e2e_hotplug_no_reload", ["R90", "R53"], group="e2e")
async def t_hot(ctx):
    """a hot-unplug of the second monitor (the kernel's RandR view loses it; its feed pushes the new list): the window
    on it comes to the primary, the one maximised there is maximised on the primary, nothing is reloaded (a marker on
    the page survives, the same windows exist); the menu bar / dock stay; a re-plug brings the second monitor's
    wallpaper back and leaves the windows where they are"""
    desk = ctx["desk"]
    x, page = desk.x, desk.pages[0]
    w1, w2 = E2E["w"]
    await page.ev("window.__marker = 42, 1")
    await page.ev(f"(() => {{ setRect(windows.get('{w1}'), {{x: 2600, y: 400, w: 900, h: 600}}); "
                  f"setRect(windows.get('{w2}'), {{x: 2000, y: 100, w: 700, h: 500}}); "
                  f"snapWin(windows.get('{w2}'), 'top', 1); return 1; }})()")
    assert await win_rect(page, w2) == [1920, 0, 2560, 1440]
    n0 = await page.ev("windows.size")
    desk.set_monitors([CARD[0]])
    await page.wait("MON.list.length === 1", 10, "the unplug reaches the shell")
    r1, r2 = await win_rect(page, w1), await win_rect(page, w2)
    room = await page.ev("dockRoom()")
    assert r1[0] >= 0 and r1[0] + r1[2] <= 1920 and r1[1] >= 30, ("came to the primary", r1)
    assert (r1[0], r1[1]) == (2600 - 1920, 400), r1
    assert r2 == [0, 30, 1920, 1080 - 30 - room], ("maximised on the primary now", r2)
    assert await page.ev("window.__marker") == 42 and await page.ev("windows.size") == n0, "no reload, no lost window"
    tiles = await page.ev("document.querySelectorAll('#wallpaper .wp-tile').length")
    assert tiles == 1 and await page.rect("#menubar") == [0, 0, 1920, 30]
    E2E["shot_unplug"] = x.shot("hot-unplug-windows-on-the-primary.png")
    desk.set_monitors(CARD)
    await page.wait("MON.list.length === 2", 10, "the re-plug reaches the shell")
    assert await win_rect(page, w1) == r1 and await page.ev("document.querySelectorAll('#wallpaper .wp-tile').length") == 2
    assert await page.ev("window.__marker") == 42
    return f"unplug: window -> {r1}, maximised -> {r2}, no reload ({n0} windows, marker kept); re-plug: 2 tiles, windows stay"


# ----------------------------------------------------------------------------- runner
E2E = {}


async def run_e2e(tests):
    try:
        import test_games_windows as gw
        gw.need_x()
    except gw.SkipTest as e:
        for t in tests:
            record(t._name, t._reqs, "skip", str(e))
        return
    except Exception as e:
        for t in tests:
            record(t._name, t._reqs, "skip", f"no X harness: {e}")
        return
    gw.SHOTS = SHOTS
    s = gw.kernel()
    async with gw.Desk(s, 4480, 1440, [(0, 0, 0, 4480, 1440, "desktop")], CARD) as desk:
        global until
        until = gw.until
        ctx = {"s": s, "desk": desk}
        ok = True
        for t in tests:
            if not ok:
                record(t._name, t._reqs, "skip", "an earlier step of this X session failed")
                continue
            ok = await run_one(t, ctx)


def until(*a, **k):          # replaced by test_games_windows.until for the e2e group
    raise RuntimeError("until() is only available in the e2e group")


def main():
    only = [x.strip() for x in os.environ.get("KAIROS_TEST_ONLY", "").split(",") if x.strip()]
    tests = [t for t in TESTS if not only or any(o in t._name for o in only)]
    print(f"kAIrOS one desktop (R90) — {len(tests)} tests", flush=True)
    for t in [t for t in tests if t._group is None]:
        asyncio.run(run_one(t, {}))
    e2e = [t for t in tests if t._group == "e2e"]
    if e2e:
        asyncio.run(run_e2e(e2e))
    if not only:
        out = {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "tests": RESULTS}
        (REPO / "tests" / "results-one-desktop.json").write_text(json.dumps(out, indent=1, ensure_ascii=False))
    fails = [n for n, r in RESULTS.items() if r["status"] == "fail"]
    passes = sum(1 for r in RESULTS.values() if r["status"] == "pass")
    skips = sum(1 for r in RESULTS.values() if r["status"] == "skip")
    print(f"\n{passes} passed, {len(fails)} failed, {skips} skipped -> tests/results-one-desktop.json", flush=True)
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
