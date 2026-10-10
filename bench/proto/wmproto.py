#!/usr/bin/env python3
"""Candidates 2 (openbox + python-xlib EWMH) and 3a (qtile + its IPC): the Python
window-manager driver — the bench stand-in for the planned kernel/wm.py.

  python3 wmproto.py --wm openbox|qtile

Line protocol on stdin/stdout (docs/DESKTOP-TECH-EVAL-2026-09.md §5); an HTTP side
door on 127.0.0.1:1339 for the shell pages (POST /op/<ref>/<op>, GET /windows,
GET /windows/stream). Window ops go through EWMH client messages (openbox) or
qtile's command IPC (qtile); completion is always verified against the X server.
This process is what `pyprof` reports on (M9); for qtile the probe is also injected
into the qtile process through its `eval`."""
import argparse
import asyncio
import json
import os
import resource
import shlex
import subprocess
import sys
import threading
import time

from Xlib import X, display, protocol
from aiohttp import web

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import CHROME, CHROME_WRAP, EXTRA_FLAGS, KERNEL, XINITRC_FLAGS, chrome_env, monitors, now_ms, say, screen_of  # noqa: E402

os.environ.pop("WAYLAND_DISPLAY", None)      # qtile's IPC client keys its socket on it; we are X11 [host only]
HERE = os.path.dirname(os.path.abspath(__file__))
PROFILE = os.environ.get("BENCH_PROFILE", "/tmp/bench-kiosk")
CTL_PORT = int(os.environ.get("BENCH_CTL_PORT", "1339"))
OPENBOX_CMD = os.environ.get("BENCH_OPENBOX_CMD", f"openbox --config-file {HERE}/openbox-rc.xml")
QTILE_CMD = os.environ.get("BENCH_QTILE_CMD", f"qtile start -c {HERE}/qtile_config.py")
LOG = open(os.environ.get("BENCH_LOG", "/tmp/bench-wmproto.log"), "ab", buffering=0)
DEFAULT_W, DEFAULT_H, DEFAULT_X, DEFAULT_Y = 720, 520, 90, 70        # shell.js createWindow defaults
# BENCH_REVEAL=1: windows are born off-screen and revealed by the driver only after the host
# page reports its first paint — covers Chromium's white first frame (R28) [measured 8.8]
REVEAL = os.environ.get("BENCH_REVEAL", "0") == "1"
BIRTH_X, BIRTH_Y = (-4000, -4000) if REVEAL else (DEFAULT_X, DEFAULT_Y)
MENUBAR_H, DOCK_H = 30, 80


def log(msg):
    LOG.write(f"{time.strftime('%H:%M:%S')} {msg}\n".encode())


# ---------------------------------------------------------------- X helpers (ops thread)
class XOps:
    def __init__(self):
        self.d = display.Display()
        self.root = self.d.screen().root
        self.atom = {}
        self.rects = monitors(self.d)

    def a(self, name):
        if name not in self.atom:
            self.atom[name] = self.d.intern_atom(name)
        return self.atom[name]

    def win(self, xid):
        return self.d.create_resource_object("window", xid)

    def clients(self):
        p = self.root.get_full_property(self.a("_NET_CLIENT_LIST"), X.AnyPropertyType)
        return list(p.value) if p else []

    def active(self):
        p = self.root.get_full_property(self.a("_NET_ACTIVE_WINDOW"), X.AnyPropertyType)
        return int(p.value[0]) if p and p.value else 0

    def title(self, xid):
        try:
            w = self.win(xid)
            p = w.get_full_property(self.a("_NET_WM_NAME"), X.AnyPropertyType) or \
                w.get_full_property(self.a("WM_NAME"), X.AnyPropertyType)
            v = p.value if p else b""
            return v.decode("utf-8", "replace") if isinstance(v, bytes) else str(v)
        except Exception:
            return ""

    def wm_class(self, xid):
        try:
            c = self.win(xid).get_wm_class()
            return c[0] if c else ""
        except Exception:
            return ""

    def geom(self, xid):
        """(x, y, w, h, mapped) of the CLIENT window in root coordinates"""
        try:
            w = self.win(xid)
            g = w.get_geometry()
            t = w.translate_coords(self.root, 0, 0)
            mapped = w.get_attributes().map_state == X.IsViewable
            return (-t.x, -t.y, g.width, g.height, mapped)
        except Exception:
            return None

    def states(self, xid):
        try:
            p = self.win(xid).get_full_property(self.a("_NET_WM_STATE"), X.AnyPropertyType)
            return {self.d.get_atom_name(x) for x in p.value} if p else set()
        except Exception:
            return set()

    def send(self, xid, msg, data):
        ev = protocol.event.ClientMessage(window=self.win(xid), client_type=self.a(msg), data=(32, data))
        self.root.send_event(ev, event_mask=X.SubstructureRedirectMask | X.SubstructureNotifyMask)
        self.d.flush()

    # ---- EWMH ops (candidate 2)
    def ewmh_moveresize(self, xid, x=None, y=None, w=None, h=None):
        flags = (2 << 12)                       # source = pager (a WM-trusted request)
        data = [flags, 0, 0, 0, 0]
        for i, v in enumerate((x, y, w, h)):
            if v is not None:
                flags |= 1 << (8 + i)
                data[i + 1] = int(v)
        data[0] = flags
        self.send(xid, "_NET_MOVERESIZE_WINDOW", data)

    def ewmh_activate(self, xid):
        self.send(xid, "_NET_ACTIVE_WINDOW", [2, X.CurrentTime, 0, 0, 0])

    def ewmh_close(self, xid):
        self.send(xid, "_NET_CLOSE_WINDOW", [X.CurrentTime, 2, 0, 0, 0])

    def ewmh_iconify(self, xid):
        self.send(xid, "WM_CHANGE_STATE", [3, 0, 0, 0, 0])     # IconicState

    def ewmh_state(self, xid, action, *names):
        """action 0 remove / 1 add / 2 toggle"""
        atoms = [self.a(n) for n in names] + [0, 0]
        self.send(xid, "_NET_WM_STATE", [action, atoms[0], atoms[1], 2, 0])

    def ewmh_drag(self, xid, rx, ry, direction):
        """_NET_WM_MOVERESIZE: the WM takes over the pointer (8 = move, 7 = bottom-right resize)"""
        self.send(xid, "_NET_WM_MOVERESIZE", [int(rx), int(ry), direction, 1, 2])


# ---------------------------------------------------------------- X watcher (own connection, own thread)
class XWatch(threading.Thread):
    """MapNotify timestamps of top-level windows (M2b: map -> first paint). Own X
    connection, own thread, and it ONLY records — python-xlib connections are not
    shareable between threads (the ops Display belongs to the ops thread)."""

    def __init__(self):
        super().__init__(daemon=True)
        self.d = display.Display()
        self.root = self.d.screen().root
        self.root.change_attributes(event_mask=X.SubstructureNotifyMask)
        self.d.flush()
        self.map_time = {}

    def run(self):
        while True:
            ev = self.d.next_event()
            if ev.type == X.MapNotify and not ev.override:
                self.map_time.setdefault(ev.window.id, time.time())


# ---------------------------------------------------------------- the driver
class Driver:
    def __init__(self, wm):
        self.wm = wm
        self.x = XOps()
        self.rects = self.x.rects
        self.procs = []              # (name, Popen)
        self.browser = None
        self.opened = {}             # app_id -> xid
        self.painted = {}            # app_id -> time the host page reported its first paint
        self.saved = {}              # xid -> geometry before minimize (qtile re-float)
        self.maxed = {}              # xid -> geometry before maximize (qtile)
        self.qc = None
        self.watch = XWatch()
        self.watch.start()
        self.rusage0 = resource.getrusage(resource.RUSAGE_SELF)
        self.jitter = []
        self.ops_lock = threading.Lock()
        self.ops = 0
        self.wm_pid = None

    # ---- identity
    def kind(self, title):
        if title.startswith("kairos-app:"):
            return "app"
        if title.startswith("kairos-desk"):
            return "desk"
        if title.startswith(("kairos-dock", "kairos-menubar", "kairos-search")):
            return "strip"
        if title.startswith("kairos-page:"):
            return "page"
        return "other"

    def app_of(self, xid):
        t = self.x.title(xid)
        if t.startswith("kairos-app:"):
            return t.split(":", 1)[1]
        c = self.x.wm_class(xid)         # CLI-adopted --app windows: class derives from the URL, known at map time
        if "_app_" in c:
            return c.split("_app_", 1)[1]
        return None

    def resolve(self, ref):
        """ref: app id | xid (int/hex) | title prefix -> xid"""
        if ref in self.opened and self.opened[ref] in self.x.clients():
            return self.opened[ref]
        try:
            xid = int(ref, 0)
            if xid in self.x.clients():
                return xid
        except ValueError:
            pass
        for xid in self.x.clients():
            t = self.x.title(xid)
            if t == ref or t == "kairos-app:" + ref or t.startswith(ref) or self.app_of(xid) == ref:
                return xid
        return None

    # ---- WM-specific primitives
    def q(self):
        if self.qc is None:
            from libqtile.command.client import InteractiveCommandClient
            self.qc = InteractiveCommandClient()
        return self.qc

    def place(self, xid, x, y, w, h):
        self.ops += 1
        if self.wm == "openbox":
            self.x.ewmh_moveresize(xid, x, y, w, h)
        else:
            self.q().window[xid].place(int(x), int(y), int(w), int(h), 0, None)

    def wait_geom(self, xid, x=None, y=None, w=None, h=None, timeout=2.0):
        t0 = time.monotonic()
        g = None
        while time.monotonic() - t0 < timeout:
            g = self.x.geom(xid)
            if g and all(v is None or int(v) == gv for v, gv in zip((x, y, w, h), g[:4])):
                return g
            time.sleep(0.001)
        return g

    def wait_mapped(self, xid, mapped, timeout=2.0):
        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout:
            g = self.x.geom(xid)
            if g is None:
                return None if mapped else True
            if g[4] == mapped:
                return True
            time.sleep(0.001)
        return False

    def op(self, xid, op, body=None):
        """one window operation, verified; returns (ok, detail)"""
        body = body or {}
        g = self.x.geom(xid)
        if g is None:
            return False, "gone"
        self.ops += 1
        if op == "move":
            x, y = int(body["x"]), int(body["y"])
            if self.wm == "openbox":
                self.x.ewmh_moveresize(xid, x=x, y=y)
            else:   # place(), not set_position_floating(): qtile 0.31's tweak_float regroups and
                    # raises KeyError for an already-floating window that changed screen
                self.q().window[xid].place(x, y, g[2], g[3], 0, None)
            r = self.wait_geom(xid, x=x, y=y)
            return (r[:2] == (x, y)), r
        if op == "resize":
            w, h = int(body["w"]), int(body["h"])
            if self.wm == "openbox":
                self.x.ewmh_moveresize(xid, w=w, h=h)
            else:
                self.q().window[xid].place(g[0], g[1], w, h, 0, None)
            r = self.wait_geom(xid, w=w, h=h)
            return (r[2:4] == (w, h)), r
        if op == "place":
            x, y, w, h = (int(body[k]) for k in "xywh")
            self.place(xid, x, y, w, h)
            r = self.wait_geom(xid, x, y, w, h)
            return (r[:4] == (x, y, w, h)), r
        if op in ("raise", "restore"):
            if self.wm == "openbox":
                self.x.ewmh_activate(xid)
            else:
                if op == "restore" and self.q().window[xid].info().get("minimized"):
                    # qtile: leaving MINIMIZED sets floating=False (tiled, i.e. full-screen in
                    # the Max layout) — re-float and put the saved geometry back
                    self.q().window[xid].toggle_minimize()
                    self.q().window[xid].enable_floating()
                    sg = self.saved.get(xid)
                    if sg:
                        self.q().window[xid].place(sg[0], sg[1], sg[2], sg[3], 0, None)
                self.q().window[xid].focus()
                self.q().window[xid].bring_to_front()
            ok = self.wait_mapped(xid, True)
            t0 = time.monotonic()
            while time.monotonic() - t0 < 1.0 and self.x.active() != xid:
                time.sleep(0.001)
            return ok and self.x.active() == xid, {"active": self.x.active() == xid}
        if op == "minimize":
            self.saved[xid] = g[:4]
            if self.wm == "openbox":
                self.x.ewmh_iconify(xid)
            else:
                if not self.q().window[xid].info().get("minimized"):
                    self.q().window[xid].toggle_minimize()
            return self.wait_mapped(xid, False), None
        if op == "maximize":
            if self.wm == "openbox":
                self.x.ewmh_state(xid, 2, "_NET_WM_STATE_MAXIMIZED_VERT", "_NET_WM_STATE_MAXIMIZED_HORZ")
                time.sleep(0.05)
                return True, self.x.geom(xid)
            # qtile: its maximize is tied to the tiling model (un-maximize -> tiled); the
            # driver toggles between the screen work area and the saved geometry instead
            if xid in self.maxed:
                sg = self.maxed.pop(xid)
                self.q().window[xid].place(sg[0], sg[1], sg[2], sg[3], 0, None)
                r = self.wait_geom(xid, *sg)
            else:
                self.maxed[xid] = g[:4]
                n = screen_of(self.rects, g[0] + g[2] // 2, g[1] + g[3] // 2)
                sx, sy, sw, sh = self.rects[n]
                tgt = (sx, sy + MENUBAR_H, sw, sh - MENUBAR_H - DOCK_H)
                self.q().window[xid].place(*tgt, 0, None)
                r = self.wait_geom(xid, *tgt)
            return True, r
        if op == "close":
            if self.wm == "openbox":
                self.x.ewmh_close(xid)
            else:
                self.q().window[xid].kill()
            t0 = time.monotonic()
            while time.monotonic() - t0 < 3.0 and xid in self.x.clients():
                time.sleep(0.002)
            return xid not in self.x.clients(), None
        if op == "toscreen":
            n = int(body["n"])
            if n >= len(self.rects):
                return False, "no such screen"
            cur = screen_of(self.rects, g[0] + g[2] // 2, g[1] + g[3] // 2)
            rx = g[0] - self.rects[cur][0]
            ry = g[1] - self.rects[cur][1]
            tx, ty = self.rects[n][0] + rx, self.rects[n][1] + ry
            if self.wm == "openbox":
                self.x.ewmh_moveresize(xid, x=tx, y=ty)
            else:
                # qtile numbers screens in Xinerama order (not left-to-right): find its index
                qn = n
                for i in range(len(self.rects)):
                    try:
                        si = self.q().screen[i].info()
                    except Exception:
                        break
                    if (si["x"], si["y"]) == (self.rects[n][0], self.rects[n][1]):
                        qn = i
                self.q().window[xid].toscreen(qn)
                self.q().window[xid].place(tx, ty, g[2], g[3], 0, None)
            r = self.wait_geom(xid, x=tx, y=ty)
            return (r[:2] == (tx, ty)), r
        if op == "above":
            if self.wm == "openbox":
                self.x.ewmh_state(xid, 1, "_NET_WM_STATE_ABOVE")
            else:
                self.q().window[xid].keep_above(True)
            return True, None
        if op == "below":
            if self.wm == "openbox":
                self.x.ewmh_state(xid, 1, "_NET_WM_STATE_BELOW")
            else:
                self.q().window[xid].keep_below(True)
            return True, None
        if op in ("drag", "resize-drag"):
            return self.poll_drag(xid, g, "move" if op == "drag" else "resize"), None
        if op == "painted":
            app = self.app_of(xid)
            if app:
                self.painted[app] = time.time()
            return True, None
        return False, "unknown op " + op

    def poll_drag(self, xid, g, mode):
        """A drag that started in the HTML titlebar: for as long as the button is down the
        page's window holds X's implicit pointer grab, so neither _NET_WM_MOVERESIZE
        (openbox) nor a driver-side XGrabPointer can take the drag over (AlreadyGrabbed,
        verified). The driver follows the pointer by polling it (~250 Hz) and re-places
        the window until the button is released — Python in the loop per step, for both
        WMs; the difference is the control channel (EWMH message vs qtile IPC)."""
        root = self.x.root
        p = root.query_pointer()
        if not (p.mask & X.Button1Mask):
            return False
        dx, dy = (p.root_x - g[0], p.root_y - g[1]) if mode == "move" else (p.root_x - g[2], p.root_y - g[3])
        last = None
        t0 = time.monotonic()
        while time.monotonic() - t0 < 15:
            p = root.query_pointer()
            if not (p.mask & X.Button1Mask):
                break
            cur = (p.root_x, p.root_y)
            if cur != last:
                last = cur
                self.ops += 1
                if mode == "move":
                    nx, ny = p.root_x - dx, p.root_y - dy
                    if self.wm == "openbox":
                        self.x.ewmh_moveresize(xid, x=nx, y=ny)
                    else:
                        self.q().window[xid].place(int(nx), int(ny), g[2], g[3], 0, None)
                else:
                    nw, nh = max(280, p.root_x - dx), max(180, p.root_y - dy)
                    if self.wm == "openbox":
                        self.x.ewmh_moveresize(xid, w=nw, h=nh)
                    else:
                        self.q().window[xid].place(g[0], g[1], int(nw), int(nh), 0, None)
            time.sleep(0.004)
        return True

    # ---- window list (the shell's model of the desktop, pygame windows included)
    def windows(self):
        out = []
        active = self.x.active()
        for xid in self.x.clients():
            g = self.x.geom(xid)
            if not g:
                continue
            t = self.x.title(xid)
            out.append({"xid": xid, "title": t, "kind": self.kind(t), "app": self.app_of(xid),
                        "x": g[0], "y": g[1], "w": g[2], "h": g[3], "minimized": not g[4],
                        "active": xid == active,
                        "screen": screen_of(self.rects, g[0] + g[2] // 2, g[1] + g[3] // 2)})
        return out

    # ---- stack lifecycle
    def chrome_cmd(self, url, position=(BIRTH_X, BIRTH_Y), size=(DEFAULT_W, DEFAULT_H)):
        # --default-background-color: a new top-level window is mapped before its document
        # arrives; without it Chromium paints it white for a frame or two (M2b, R28)
        return CHROME_WRAP + [CHROME] + XINITRC_FLAGS + EXTRA_FLAGS + ["--disable-popup-blocking",
                "--default-background-color=0a0a14",
                f"--user-data-dir={PROFILE}", f"--window-position={position[0]},{position[1]}",
                f"--window-size={size[0]},{size[1]}", f"--app={url}"]

    def start(self):
        subprocess.run(["rm", "-rf", PROFILE])
        cmd = OPENBOX_CMD if self.wm == "openbox" else QTILE_CMD
        wmp = subprocess.Popen(shlex.split(cmd), stdout=LOG, stderr=LOG, env=chrome_env())
        self.procs.append(("wm", wmp))
        t0 = time.monotonic()
        while time.monotonic() - t0 < 20:
            if self.wm_alive():
                break
            time.sleep(0.05)
        else:
            say("ERR window manager did not come up")
        if self.wm == "qtile":          # its IPC socket appears a moment after the WM check
            from libqtile.ipc import find_sockfile
            t0 = time.monotonic()
            while time.monotonic() - t0 < 20 and not os.path.exists(find_sockfile()):
                time.sleep(0.05)
        self.wm_pid = wmp.pid
        if self.wm == "openbox" and "docker" in OPENBOX_CMD:     # host: openbox lives in the trixie container
            try:
                out = subprocess.run(["docker", "exec", "kairos-bench-host", "sh", "-c", "pidof openbox"],
                                     capture_output=True, text=True, timeout=5).stdout.split()
                host_pids = subprocess.run(["pgrep", "-x", "openbox"], capture_output=True, text=True).stdout.split()
                self.wm_pid = int(host_pids[-1]) if host_pids else wmp.pid
            except Exception:
                pass
        # the desktop layer: desk (below) + dock + menubar (above) per monitor. The FIRST
        # Chromium invocation is the browser process; every later window is adopted by it.
        env = chrome_env()
        for i, (x, y, w, h) in enumerate(self.rects):
            q = f"screen={i}&x={x}&y={y}&w={w}&h={h}"
            for name, url, gx, gy, gw, gh in (
                    ("desk", f"{KERNEL}/shell/bench/desk.html?{q}", x, y, w, h),
                    ("menubar", f"{KERNEL}/shell/bench/menubar.html?{q}", x, y, w, MENUBAR_H),
                    ("dock", f"{KERNEL}/shell/bench/dock.html?{q}", x, y + h - DOCK_H, w, DOCK_H)):
                title = f"kairos-{name}:{i}"
                p = subprocess.Popen(self.chrome_cmd(url), stdout=LOG, stderr=LOG, env=env)
                if self.browser is None:
                    self.browser = p
                    self.procs.append(("chromium", p))
                xid = self.wait_title(title, 20)
                if xid is None:
                    say(f"ERR window {title} did not appear")
                    continue
                self.place(xid, gx, gy, gw, gh)
                self.wait_geom(xid, gx, gy, gw, gh)
                self.op(xid, "below" if name == "desk" else "above")
                if name != "desk":      # strips reserve their edge: maximize stays in the work area
                    self.set_strut(xid, top=MENUBAR_H if name == "menubar" else 0,
                                   bottom=DOCK_H if name == "dock" else 0, x0=gx, x1=gx + gw - 1)
        self.install_qtile_probe()
        say(f"READY {now_ms()}")

    def set_strut(self, xid, top=0, bottom=0, x0=0, x1=0):
        """_NET_WM_STRUT_PARTIAL: left right top bottom + the four start/end ranges"""
        vals = [0, 0, top, bottom, 0, 0, 0, 0, x0 if top else 0, x1 if top else 0, x0 if bottom else 0, x1 if bottom else 0]
        try:
            self.x.win(xid).change_property(self.x.a("_NET_WM_STRUT_PARTIAL"), self.x.a("CARDINAL"), 32, vals)
            self.x.win(xid).change_property(self.x.a("_NET_WM_STRUT"), self.x.a("CARDINAL"), 32, vals[:4])
            self.x.d.flush()
        except Exception as e:
            log(f"strut: {e}")

    def wm_alive(self):
        """_NET_SUPPORTING_WM_CHECK on the root outlives a dead WM; its check window does not"""
        try:
            p = self.x.root.get_full_property(self.x.a("_NET_SUPPORTING_WM_CHECK"), X.AnyPropertyType)
            if not p or not p.value:
                return False
            w = self.x.win(int(p.value[0]))
            q = w.get_full_property(self.x.a("_NET_SUPPORTING_WM_CHECK"), X.AnyPropertyType)
            return bool(q and q.value and int(q.value[0]) == int(p.value[0]))
        except Exception:
            return False

    def wait_title(self, title, timeout):
        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout:
            for xid in self.x.clients():
                if self.x.title(xid) == title:
                    return xid
            time.sleep(0.02)
        return None

    def open(self, app, x, y, w, h):
        """CLI adoption: a second invocation against the live profile; the browser opens
        the app window with its own default bounds (a forwarded --window-position/size is
        ignored — verified), the driver places it as soon as it is in the client list."""
        known = set(self.x.clients())
        subprocess.Popen(self.chrome_cmd(f"{KERNEL}/app/{app}"), stdout=LOG, stderr=LOG, env=chrome_env())
        t0 = time.monotonic()
        while time.monotonic() - t0 < 20:
            for xid in self.x.clients():
                if xid in known:
                    continue
                if self.app_of(xid) == app:
                    self.opened[app] = xid
                    if REVEAL:          # keep it off-screen until the host page has painted (<= 2 s)
                        t1 = time.monotonic()
                        while time.monotonic() - t1 < 2.0 and app not in self.painted:
                            time.sleep(0.002)
                    self.place(xid, x, y, w, h)
                    g = self.wait_geom(xid, x, y, w, h)
                    return xid, self.watch.map_time.get(xid, time.time()), g
            time.sleep(0.002)
        return None, None, None

    def stop(self):
        for name, p in reversed(self.procs):
            try:
                p.terminate()
            except Exception:
                pass
        if self.wm == "openbox" and "docker" in OPENBOX_CMD:
            subprocess.run(["docker", "exec", "kairos-bench-host", "sh", "-c", "kill $(pidof openbox) 2>/dev/null"], timeout=10)
        time.sleep(1.5)
        for name, p in self.procs:
            try:
                p.kill()
            except Exception:
                pass
        subprocess.run(["pkill", "-f", "--", f"--user-data-dir={PROFILE}"])

    def pids(self):
        out = []
        if self.browser:
            out.append(self.browser.pid)
        if self.wm_pid:
            out.append(self.wm_pid)
        out.append(os.getpid())
        return out

    # ---- M9: Python overhead
    def pyprof(self, reset=False):
        ru = resource.getrusage(resource.RUSAGE_SELF)
        res = {"driver": {"utime_ms": (ru.ru_utime - self.rusage0.ru_utime) * 1000,
                          "stime_ms": (ru.ru_stime - self.rusage0.ru_stime) * 1000,
                          "ops": self.ops, "pss_kb": pss_kb(os.getpid()),
                          "jitter_us": jitter_stats(self.jitter), "pid": os.getpid()}}
        if self.wm == "qtile":
            try:
                # qtile's eval: an EXPRESSION evaluated inside the manager (`self` = the Qtile object)
                ok, r = self.q().eval(
                    "__import__('json').dumps({'utime_ms': __import__('resource').getrusage(0).ru_utime*1000, "
                    "'stime_ms': __import__('resource').getrusage(0).ru_stime*1000, "
                    "'pid': __import__('os').getpid(), 'jitter': list(getattr(self, '_bench_jitter', []))})")
                q = json.loads(r)
                q0 = getattr(self, "q0", None) or q
                res["qtile"] = {"utime_ms": q["utime_ms"] - q0["utime_ms"], "stime_ms": q["stime_ms"] - q0["stime_ms"],
                                "pid": q["pid"], "pss_kb": pss_kb(q["pid"]), "jitter_us": jitter_stats(q["jitter"])}
                if reset:
                    self.q0 = q
            except Exception as e:
                res["qtile"] = {"error": str(e)}
        if reset:
            self.rusage0 = ru
            self.jitter.clear()
            self.ops = 0
            if self.wm == "qtile":
                try:
                    self.q().eval("getattr(self, '_bench_jitter', []).clear()")
                except Exception:
                    pass
        return res

    def start_jitter_probe(self, loop):
        period = 1 / 120

        async def probe():
            nxt = loop.time() + period
            while True:
                await asyncio.sleep(max(0, nxt - loop.time()))
                late = (loop.time() - nxt) * 1e6
                if len(self.jitter) < 200000:
                    self.jitter.append(late)
                nxt += period
        loop.create_task(probe())

    def install_qtile_probe(self):
        """the same 120 Hz lateness probe inside the qtile process (after it is up)"""
        if self.wm == "qtile":
            # exec'd inside Qtile.eval: `self` is the manager; bind it as a default argument
            # because the coroutine body cannot see exec's local snapshot
            code = ("import asyncio\n"
                    "self._bench_jitter = []\n"
                    "async def _bench_probe(q=self):\n"
                    "    loop = asyncio.get_running_loop(); period = 1/120; nxt = loop.time() + period\n"
                    "    while True:\n"
                    "        await asyncio.sleep(max(0, nxt - loop.time()))\n"
                    "        late = (loop.time() - nxt) * 1e6\n"
                    "        if len(q._bench_jitter) < 200000: q._bench_jitter.append(late)\n"
                    "        nxt += period\n"
                    "asyncio.ensure_future(_bench_probe())\n")
            try:
                ok, r = self.q().eval(code)
                log(f"qtile probe: {ok} {r}")
            except Exception as e:
                log(f"qtile probe failed: {e}")


def pss_kb(pid):
    try:
        with open(f"/proc/{pid}/smaps_rollup") as f:
            for line in f:
                if line.startswith("Pss:"):
                    return int(line.split()[1])
    except Exception:
        return None
    return None


def jitter_stats(samples):
    if not samples:
        return None
    s = sorted(samples)
    q = lambda p: s[min(len(s) - 1, int(p * len(s)))]
    return {"n": len(s), "p50": q(0.5), "p95": q(0.95), "p99": q(0.99), "max": s[-1]}


# ---------------------------------------------------------------- HTTP side door (aiohttp)
CORS = {"Access-Control-Allow-Origin": KERNEL, "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type"}


def make_app(drv, executor):
    app = web.Application()

    async def run(fn, *a):
        return await asyncio.get_running_loop().run_in_executor(executor, fn, *a)

    async def h_op(request):
        if request.method == "OPTIONS":
            return web.Response(headers=CORS)
        ref, op = request.match_info["ref"], request.match_info["op"]
        try:
            body = await request.json()
        except Exception:
            body = {}
        log(f"http op {ref} {op} {body}")
        if op == "painted":         # answered here: the ops thread may be inside open() waiting for it
            drv.painted[ref] = time.time()
            return web.json_response({"ok": True}, headers=CORS)
        xid = await run(drv.resolve, ref)
        if xid is None:
            return web.json_response({"error": "no such window", "ref": ref}, status=404, headers=CORS)
        ok, detail = await run(drv.op, xid, op, body)
        return web.json_response({"ok": ok, "detail": detail}, headers=CORS)

    async def h_windows(request):
        return web.json_response(await run(drv.windows), headers=CORS)

    async def h_stream(request):
        resp = web.StreamResponse(headers=dict(CORS, **{"Content-Type": "text/event-stream", "Cache-Control": "no-cache"}))
        await resp.prepare(request)
        last = None
        try:
            while True:
                cur = json.dumps(await run(drv.windows))
                if cur != last:
                    await resp.write(f"data: {cur}\n\n".encode())
                    last = cur
                await asyncio.sleep(0.25)
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        return resp

    app.router.add_route("*", "/op/{ref}/{op}", h_op)
    app.router.add_get("/windows", h_windows)
    app.router.add_get("/windows/stream", h_stream)
    return app


# ---------------------------------------------------------------- line protocol (main thread)
def serve_stdin(drv, executor):
    def run(fn, *a):
        return executor.submit(fn, *a).result()
    for line in sys.stdin:
        parts = line.strip().split()
        if not parts:
            continue
        cmd, args = parts[0], parts[1:]
        try:
            if cmd == "start":
                run(drv.start)
            elif cmd == "screens":
                say("SCREENS %d %s" % (len(drv.rects), " ".join(",".join(map(str, r)) for r in drv.rects)))
            elif cmd == "pids":
                say("PIDS " + " ".join(map(str, drv.pids())))
            elif cmd == "open":
                app, x, y, w, h = args[0], *map(int, args[1:5])
                xid, tmap, g = run(drv.open, app, x, y, w, h)
                say(f"WIN {app} {int(tmap * 1000)} {xid} {g}" if xid else f"ERR open {app} timed out")
            elif cmd in ("move", "resize", "raise", "minimize", "restore", "close", "maximize", "toscreen"):
                xid = run(drv.resolve, args[0])
                if xid is None:
                    say(f"ERR no window {args[0]}")
                    continue
                body = {}
                if cmd == "move":
                    body = {"x": args[1], "y": args[2]}
                elif cmd == "resize":
                    body = {"w": args[1], "h": args[2]}
                elif cmd == "toscreen":
                    body = {"n": args[1]}
                ok, detail = run(drv.op, xid, cmd, body)
                say(f"OK {now_ms()} {detail}" if ok else f"FAIL {now_ms()} {detail}")
            elif cmd == "geom":
                xid = run(drv.resolve, args[0])
                g = run(drv.x.geom, xid) if xid else None
                say("GEOM %d %d %d %d %d %d" % (g[0], g[1], g[2], g[3], int(g[4]), screen_of(drv.rects, g[0] + g[2] // 2, g[1] + g[3] // 2)) if g else "GEOM none")
            elif cmd == "windows":
                say("WINDOWS " + json.dumps(run(drv.windows)))
            elif cmd == "pyprof":
                say("PYPROF " + json.dumps(run(drv.pyprof, bool(args and args[0] == "reset"))))
            elif cmd == "maptime":
                xid = run(drv.resolve, args[0])
                say(f"MAPTIME {int(drv.watch.map_time.get(xid, 0) * 1000)}")
            elif cmd == "stop":
                run(drv.stop)
                say("BYE")
                break
            else:
                say("ERR unknown command " + cmd)
        except Exception as e:
            log(f"{cmd} {args}: {type(e).__name__}: {e}")
            say(f"ERR {cmd}: {type(e).__name__}: {e}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wm", choices=["openbox", "qtile"], required=True)
    a = ap.parse_args()
    from concurrent.futures import ThreadPoolExecutor
    executor = ThreadPoolExecutor(max_workers=1)          # ONE ops thread: X ops + WM IPC, in order
    drv = executor.submit(Driver, a.wm).result()

    def http_thread():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        drv.start_jitter_probe(loop)
        runner = web.AppRunner(make_app(drv, executor))
        loop.run_until_complete(runner.setup())
        loop.run_until_complete(web.TCPSite(runner, "127.0.0.1", CTL_PORT).start())
        loop.run_forever()
    threading.Thread(target=http_thread, daemon=True).start()
    serve_stdin(drv, executor)


if __name__ == "__main__":
    main()
