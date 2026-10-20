#!/usr/bin/env python3
"""kAIrOS window layer [R68] — an EWMH client for the kiosk's X display.

The desktop is the web shell (docs/DESKTOP-TECH-EVAL-2026-09.md §9); the only real X
windows next to the kiosk Chromium are the generated pygame games [R7]. This module
lists them and drives openbox over EWMH client messages — raise/activate, minimize
(WM_CHANGE_STATE IconicState: openbox sets _NET_WM_STATE_HIDDEN and unmaps; the game
process itself is then SIGSTOPped, see WindowService._freeze), restore, close
(_NET_CLOSE_WINDOW) and move/resize/place (_NET_MOVERESIZE_WINDOW) — and verifies every
op against the X server, exactly like the bench driver it is lifted from
(bench/proto/wmproto.py, openbox path: 0.94 ms of Python per verified op, 33 MiB). The
shell's taskbar shows one chip per listed window [R61]; GET /api/windows(/stream) +
POST /api/windows/{id}/{op} in kernel/server.py are the HTTP face.

Rules:
  * python-xlib is synchronous: every X call runs on ONE dedicated daemon thread with
    ONE persistent Display; the aiohttp loop only awaits.
  * X restarts on the same :0 in normal operation (the paced X-retry loop, kairos-
    display-apply, a getty@tty1 restart): a dead connection is DETECTED — the readers
    below tell "this window went away" (None) from "the connection is gone" (raised),
    the watcher thread bumps a server generation when its own socket dies — and the
    ops thread reconnects and retries the very call that found out [D-R68-1].
  * a second connection on its own thread turns X events (map/unmap/destroy/property
    changes on the root) into change notifications for the SSE stream — no polling.
  * no X (dev container, test suite, headless, X not up yet) degrades to
    {"available": false, "reason": ...}: the kernel starts and serves without it [R63].
  * the EWMH logic below touches a small python-xlib surface (documented per method) so
    the test suite drives it with a fake Display; importing this module needs no python3-xlib.
"""
import asyncio
import collections
import os
import queue
import signal
import threading
import time

import hw
import monitors as monmath

try:                                    # only the connects need python3-xlib: the logic
    from Xlib.error import XError       # must import and be testable without it
except Exception:                       # ImportError in the dev container / test suite
    XError = None

# python-xlib's X constants, inlined: the logic must import and be testable where
# python3-xlib is absent (dev container, test suite) — only the connects need the module
ANY_PROPERTY_TYPE = 0
CURRENT_TIME = 0
IS_VIEWABLE = 2
ICONIC_STATE = 3
SUBSTRUCTURE_NOTIFY_MASK = 0x80000
SUBSTRUCTURE_REDIRECT_MASK = 0x100000
PROPERTY_CHANGE_MASK = 0x400000
ZPIXMAP, ALL_PLANES = 2, 0xffffffff          # XGetImage: packed pixels, every plane [R70]
EV_DESTROY, EV_UNMAP, EV_MAP, EV_REPARENT, EV_CONFIGURE, EV_PROPERTY = 17, 18, 19, 21, 22, 28
CHANGE_EVENTS = {EV_DESTROY, EV_UNMAP, EV_MAP, EV_REPARENT, EV_CONFIGURE, EV_PROPERTY}
SOURCE_PAGER = 2            # EWMH source indication: a pager/taskbar (WM-trusted) request

# never listed — they ARE the desktop: the kiosk Chromium (ONE window over every monitor [R90], WM_CLASS
# "chromium"/"Chromium", also its --app helper windows), the boot-animation player, the
# cursor hider, the testrig's VNC publisher
DESKTOP_CLASSES = ("chrom", "mpv", "unclutter", "x11vnc")
LISTED_TYPES = {"_NET_WM_WINDOW_TYPE_NORMAL", "_NET_WM_WINDOW_TYPE_DIALOG"}
GAME_CLASSES = ("pygame", "python", "sdl", "main.py")   # SDL derives WM_CLASS from argv[0]: `python3 main.py`
OPS = ("raise", "activate", "restore", "minimize", "close", "move", "resize", "place")
# [R82] the frame <-> game-window pairs: a game runs inside a desktop window frame (a DOM window of the web shell);
# the kernel glues the pygame X window over the frame's body and moves, stacks, covers and minimizes it with the frame
FRAME_OPS = ("place", "follow", "snapshot", "cover", "uncover", "minimize", "restore", "focus", "close", "adopt",
             "release")
FRAME_KEEP = 32                        # frame records kept (ended ones go first)
WM_TRANSIENT_FOR, XA_WINDOW = 68, 33   # predefined atoms (Xatom): no intern round trip
P_MIN_SIZE, P_MAX_SIZE = 16, 32        # WM_NORMAL_HINTS flags


class WMError(Exception):
    pass


def _text(v):
    if v is None:
        return ""
    if isinstance(v, bytes):
        return v.decode("utf-8", "replace")
    return str(v)


def _lost(e, disp=None):
    """Does an exception from a python-xlib call mean the CONNECTION is gone?
    A per-window protocol error (BadWindow/BadDrawable/BadMatch/BadAtom: that window or
    atom went away between two calls) is answered with None by the readers below. The
    connection is lost when python-xlib raises its ConnectionClosedError (every later
    call on a Display whose server died or restarted raises it), when the socket/OS
    error that closed it surfaces, or when python-xlib has already flagged the Display
    closed (protocol.display.Display.socket_error) — those must reach
    WindowService._guarded(), which reconnects. Before this distinction prop() swallowed
    everything and a dead Display answered "no windows" for ever after an X restart on
    the same :0 (VM test 2026-09-11, D-R68-1)."""
    if XError is not None and isinstance(e, XError):
        return False
    if type(e).__name__.startswith("Bad"):          # the test suite's fake raises BadWindow
        return False
    if isinstance(e, (OSError, EOFError)):          # socket.error is OSError since 3.3
        return True
    n = type(e).__name__
    if "Connection" in n or "Closed" in n:
        return True
    return getattr(getattr(disp, "display", None), "socket_error", None) is not None


def _client_message(window, client_type, data):
    """python-xlib event object for send_event (the test suite swaps this seam)"""
    from Xlib import protocol
    return protocol.event.ClientMessage(window=window, client_type=client_type, data=(32, data))


def _randr_monitors(root):
    """[(x, y, w, h, primary, name)] from RandR 1.5 (the heads openbox consumes; one per active output / CRTC);
    [] without RandR. A 5-tuple without the name is accepted everywhere (the tests' fake X)."""
    from Xlib.ext import randr
    from Xlib.protocol import request
    ms = randr.get_monitors(root, True).monitors
    out = []
    for i, m in enumerate(ms):
        try:
            name = request.GetAtomName(display=root.display, atom=m.name).name   # the output's name ("eDP", "DP-1")
        except Exception as e:
            if _lost(e):
                raise
            name = f"monitor-{i}"
        out.append((m.x, m.y, m.width_in_pixels, m.height_in_pixels, bool(m.primary), name))
    return out


def _randr_watch(d, root):
    """[R90] select RandR's screen/CRTC/output change events on root -> the event codes to treat as a change ({} when
    the server has no RandR: the monitor list then only changes with the root window itself)"""
    try:
        from Xlib.ext import randr
        ext = d.query_extension("RANDR")
        if not ext or not ext.present:
            return set()
        root.xrandr_select_input(randr.RRScreenChangeNotifyMask | randr.RRCrtcChangeNotifyMask
                                 | randr.RROutputChangeNotifyMask)
        return {ext.first_event + randr.RRScreenChangeNotify, ext.first_event + randr.RRNotify}
    except Exception:
        return set()


def _proc_name(pid, cache=None):
    """/proc/<pid>/comm of the window's owner (_NET_WM_PID) — the kernel runs as root and
    sees every process; a Chromium --app window's WM_CLASS is derived from its URL
    ("127.0.0.1"/"localhost__"), so the class alone does not identify the kiosk"""
    if not pid:
        return ""
    if cache is not None and pid in cache:
        return cache[pid]
    try:
        with open(f"/proc/{pid}/comm") as f:
            n = f.read().strip()
    except OSError:
        n = ""
    if cache is not None:
        cache[pid] = n
    return n


def _proc_state(pid):
    """the state letter of /proc/<pid>/stat (R S D, T = stopped, Z = zombie), '' when gone"""
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().rsplit(")", 1)[1].split()[0]
    except (OSError, IndexError):
        return ""


def _alive(pid):
    return _proc_state(pid) not in ("", "Z", "X")


def _signal_game(pid, sig):
    """Deliver sig to a game the kernel launched: to its whole process group when it
    leads one (h_game starts games in their own session, so a helper the game spawns
    freezes/thaws/dies with it), to the pid alone otherwise — never to a group the kernel
    itself is in. False when the process is gone or not ours to signal."""
    try:
        pid = int(pid)
        if pid <= 1:
            return False
        if os.getpgid(pid) == pid and pid != os.getpgid(0):
            os.killpg(pid, sig)
        else:
            os.kill(pid, sig)
    except (OSError, ValueError):
        return False
    return True


def is_desktop_window(instance, cls, pid=0, cache=None):
    n = f"{instance} {cls} {_proc_name(pid, cache)}".lower()
    return any(k in n for k in DESKTOP_CLASSES)


def screen_of(rects, x, y):
    for i, (rx, ry, rw, rh) in enumerate(rects):
        if rx <= x < rx + rw and ry <= y < ry + rh:
            return i
    return 0


class GameRegistry:
    """pid -> the app the kernel launched it for [R7]: a game's window carries _NET_WM_PID
    (SDL sets it), so the chip can show the app's title/icon instead of a bare X title.
    Also remembers which of them the kernel has SIGSTOPped (minimized) and under which
    window, so a map by anyone thaws it [D-R68-2]; which window a snapshot last saw for
    the pid and when it first mapped (the launch gate's report, the crash record);
    whether the kernel itself asked the game to end (close escalation, shutdown — its
    exit is then not a crash); and the last few games that died after they were
    reported running, for the desktop to tell the user [D-R68-6]."""

    def __init__(self):
        self._by_pid = {}
        self._crashes = collections.deque(maxlen=5)

    def register(self, pid, app, title=None, icon=None):
        self._by_pid[int(pid)] = {"app": app, "title": title or app, "icon": icon, "t0": time.time(),
                                  "frozen": False, "xid": 0, "win": 0, "mapped_at": 0.0, "killed": False}

    def forget(self, pid):
        self._by_pid.pop(int(pid), None)

    def lookup(self, pid):
        return self._by_pid.get(int(pid)) if pid else None

    def running(self):
        return dict(self._by_pid)

    def frozen(self):
        """pids the kernel has SIGSTOPped"""
        return [p for p, g in self._by_pid.items() if g["frozen"]]

    def frozen_by_xid(self, xid):
        """the SIGSTOPped game whose window is xid, 0 if none"""
        return next((p for p, g in self._by_pid.items() if g["frozen"] and g["xid"] == xid), 0)

    def seen(self, pid, xid, mapped):
        """a snapshot listed window xid for this game [D-R68-6]"""
        g = self._by_pid.get(int(pid))
        if g is not None:
            g["win"] = xid
            if mapped and not g["mapped_at"]:
                g["mapped_at"] = time.time()

    def mark_killed(self, pid):
        """the kernel itself is ending this game (close op escalation, shutdown): its
        exit is not a crash [D-R68-6]"""
        g = self._by_pid.get(int(pid))
        if g is not None:
            g["killed"] = True

    def note_crash(self, rec):
        self._crashes.append(rec)

    def crashes(self):
        """the last few games that died after the kernel reported them running"""
        return list(self._crashes)


class EWMH:
    """The EWMH client — one Display, one thread, no asyncio.
    python-xlib surface used: Display.screen().root, intern_atom, get_atom_name,
    root.get_image (the screen capture [R70]),
    create_resource_object("window", xid), flush; Window.get_full_property, get_wm_class,
    get_geometry, translate_coords, get_attributes; root.send_event, root.get_geometry.
    Every reader answers None/""/[] for a window that went away and RAISES when the
    connection is gone (see _lost) — the service layer reconnects on that."""

    def __init__(self, disp):
        self.d = disp
        self.root = disp.screen().root
        self._atoms = {}
        self._names = {}

    def lost(self, e):
        return _lost(e, self.d)

    # ---- properties
    def atom(self, name):
        a = self._atoms.get(name)
        if a is None:
            a = self._atoms[name] = self.d.intern_atom(name)
        return a

    def atom_name(self, atom):
        n = self._names.get(atom)
        if n is None:
            try:
                n = self.d.get_atom_name(atom)
            except Exception as e:
                if self.lost(e):
                    raise
                n = ""                   # BadAtom
            self._names[atom] = n
        return n

    def win(self, xid):
        return self.d.create_resource_object("window", int(xid))

    def prop(self, w, name):
        try:
            p = w.get_full_property(self.atom(name), ANY_PROPERTY_TYPE)
        except Exception as e:
            if self.lost(e):
                raise                    # dead connection: WindowService reconnects
            return None                  # BadWindow: it went away between two calls
        return p.value if p is not None else None

    def clients(self):
        v = self.prop(self.root, "_NET_CLIENT_LIST")
        return [int(x) for x in v] if v is not None else []

    def active(self):
        v = self.prop(self.root, "_NET_ACTIVE_WINDOW")
        return int(v[0]) if v else 0

    def wm_name(self):
        """the running WM (_NET_SUPPORTING_WM_CHECK, verified on its check window — the
        root property outlives a dead WM); None means no WM: EWMH messages go nowhere"""
        v = self.prop(self.root, "_NET_SUPPORTING_WM_CHECK")
        if not v:
            return None
        w = self.win(int(v[0]))
        back = self.prop(w, "_NET_SUPPORTING_WM_CHECK")
        if not back or int(back[0]) != int(v[0]):
            return None
        return _text(self.prop(w, "_NET_WM_NAME")) or "unknown"

    def title(self, xid):
        w = self.win(xid)
        return _text(self.prop(w, "_NET_WM_NAME")) or _text(self.prop(w, "WM_NAME"))

    def wm_class(self, xid):
        try:
            c = self.win(xid).get_wm_class()
        except Exception as e:
            if self.lost(e):
                raise
            c = None
        return (c[0], c[1]) if c else ("", "")

    def pid(self, xid):
        v = self.prop(self.win(xid), "_NET_WM_PID")
        return int(v[0]) if v else 0

    def window_types(self, xid):
        v = self.prop(self.win(xid), "_NET_WM_WINDOW_TYPE")
        return {self.atom_name(a) for a in v} if v else set()

    def states(self, xid):
        v = self.prop(self.win(xid), "_NET_WM_STATE")
        return {self.atom_name(a) for a in v} if v else set()

    def geom(self, xid):
        """(x, y, w, h, mapped) of the CLIENT window in root coordinates, None if gone"""
        try:
            w = self.win(xid)
            g = w.get_geometry()
            t = w.translate_coords(self.root, 0, 0)
            mapped = w.get_attributes().map_state == IS_VIEWABLE
        except Exception as e:
            if self.lost(e):
                raise
            return None
        return (-t.x, -t.y, g.width, g.height, mapped)

    def monitor_list(self):
        """[R90] the monitors the one desktop spans (kernel/monitors.py: a mirror is one, exactly one primary, primary
        first then left-to-right / top-to-bottom — the numbering of /api/vision "screen:N")"""
        try:
            ms = _randr_monitors(self.root)
        except Exception as e:
            if self.lost(e):
                raise
            ms = []                      # no RandR 1.5 on this server: the root geometry
        mons = monmath.from_rects([(t[5] if len(t) > 5 else f"monitor-{i}", t[0], t[1], t[2], t[3], t[4])
                                   for i, t in enumerate(ms)])
        if not mons:
            g = self.root.get_geometry()  # only a dead connection can fail here: let it raise
            mons = monmath.from_rects([("screen", 0, 0, g.width, g.height, True)])
        return mons

    def monitors(self):
        """[(x, y, w, h)] of monitor_list(), in its order"""
        return [(m["x"], m["y"], m["w"], m["h"]) for m in self.monitor_list()]

    def grab(self, rect):
        """the framebuffer pixels of the root rect (x, y, w, h): XGetImage, ZPixmap — the
        32-bit BGRX rows a depth-24 TrueColor visual gives (what x11vnc / scrot read);
        the kiosk's own screen, for /api/vision "screen" [R70]. A lost connection raises."""
        x, y, w, h = rect
        img = self.root.get_image(x, y, w, h, ZPIXMAP, ALL_PLANES)
        if img.depth not in (24, 32):
            raise WMError(f"unsupported screen depth {img.depth} (need a 24/32-bit TrueColor visual)")
        return bytes(img.data)

    # ---- [R82] what a frame needs to know about its game window and its screen
    def size_fixed(self, xid):
        """does the client ask for a fixed size? SDL's non-resizable window (every generated game: 960x540) sets
        WM_NORMAL_HINTS min == max; a RESIZABLE one sets a minimum only. Unreadable hints count as fixed: a game is
        centred in its frame, never stretched, unless it says it can grow."""
        try:
            h = self.win(xid).get_wm_normal_hints()
        except Exception as e:
            if self.lost(e):
                raise
            return True
        flags = getattr(h, "flags", 0) if h is not None else 0
        if not flags:
            return True
        if flags & P_MAX_SIZE:
            return not (flags & P_MIN_SIZE) or (h.min_width, h.min_height) == (h.max_width, h.max_height)
        return False

    def set_transient(self, xid, parent):
        """WM_TRANSIENT_FOR = parent. The kiosk Chromium is FULLSCREEN, and openbox lifts a FOCUSED fullscreen window
        above every other layer, _NET_WM_STATE_ABOVE included: a click on the desktop hid the game (measured in Xvfb
        + openbox 3.6 with the kiosk's rc.xml, 2026-10-15). A transient stays above its parent, in the parent's
        layer — a game transient for the kiosk of its frame's screen stays over the desktop [R82]."""
        try:
            if parent:
                self.win(xid).change_property(WM_TRANSIENT_FOR, XA_WINDOW, 32, [int(parent)])
            else:                                  # out of the kiosk's group (a covered or minimized game)
                self.win(xid).delete_property(WM_TRANSIENT_FOR)
            self.d.flush()
        except Exception as e:
            if self.lost(e):
                raise
            return False
        return True

    def restack(self, xid, sibling=0, below=False):
        """_NET_RESTACK_WINDOW (source: pager): below/above `sibling` (0 = the bottom/top of its layer)"""
        self.send(xid, "_NET_RESTACK_WINDOW", [SOURCE_PAGER, int(sibling or 0), 1 if below else 0])

    def stacked_above(self, xid, other):
        """is xid above `other` in openbox's stacking order? None when the WM does not say"""
        v = self.prop(self.root, "_NET_CLIENT_LIST_STACKING")
        if not v:
            return None
        order = [int(x) for x in v]                # bottom to top
        if xid not in order or other not in order:
            return None
        return order.index(xid) > order.index(other)

    def kiosk_at(self, x, y, w=None):
        """the Chromium window whose origin is (x, y) root px (±2; the one w px wide first) -> (xid, (x, y, w, h)) or
        (0, None): the page tells where its own window is (window.screenX/Y x devicePixelRatio) — the most direct
        way to its window, independent of how the monitors are reported"""
        best, names = (0, None, -1), {}
        for xid in self.clients():
            inst, cls = self.wm_class(xid)
            if "chrom" not in f"{inst} {cls} {_proc_name(self.pid(xid), names)}".lower():
                continue
            g = self.geom(xid)
            if g is None or not g[4] or abs(g[0] - x) > 2 or abs(g[1] - y) > 2:
                continue
            score = (2 if w and abs(g[2] - w) <= 2 else 1, g[2] * g[3])
            if best[2] == -1 or score > best[2]:
                best = (xid, g[:4], score)
        return best[0], best[1]

    def kiosk_for(self, rect):
        """the kiosk Chromium window that shows the monitor rect (x, y, w, h): the mapped Chromium window covering
        most of it -> (xid, (x, y, w, h)) or (0, None). Its geometry maps the page's CSS pixels to root pixels (no
        browser chrome: the viewport IS the window). [R90] By overlap, not by the window's centre: the one desktop
        window spans every monitor and its centre may lie on another monitor or in a dead area."""
        best, names = (0, None, 0), {}
        for xid in self.clients():
            inst, cls = self.wm_class(xid)
            if "chrom" not in f"{inst} {cls} {_proc_name(self.pid(xid), names)}".lower():
                continue
            g = self.geom(xid)
            if g is None or not g[4]:
                continue
            ow = min(g[0] + g[2], rect[0] + rect[2]) - max(g[0], rect[0])
            oh = min(g[1] + g[3], rect[1] + rect[3]) - max(g[1], rect[1])
            if ow <= 0 or oh <= 0:
                continue
            if ow * oh > best[2]:
                best = (xid, g[:4], ow * oh)
        return best[0], best[1]

    # ---- EWMH client messages
    def send(self, xid, name, data):
        data = list(data) + [0] * (5 - len(data))
        ev = _client_message(self.win(xid), self.atom(name), data[:5])
        self.root.send_event(ev, event_mask=SUBSTRUCTURE_REDIRECT_MASK | SUBSTRUCTURE_NOTIFY_MASK)
        self.d.flush()

    def activate(self, xid):
        self.send(xid, "_NET_ACTIVE_WINDOW", [SOURCE_PAGER, CURRENT_TIME, 0])

    def iconify(self, xid):
        self.send(xid, "WM_CHANGE_STATE", [ICONIC_STATE])

    def close(self, xid):
        self.send(xid, "_NET_CLOSE_WINDOW", [CURRENT_TIME, SOURCE_PAGER])

    def moveresize(self, xid, x=None, y=None, w=None, h=None):
        flags = SOURCE_PAGER << 12          # gravity 0 = the window's own; bits 8-11 = x y w h present
        data = [0, 0, 0, 0, 0]
        for i, v in enumerate((x, y, w, h)):
            if v is not None:
                flags |= 1 << (8 + i)
                data[i + 1] = int(v)
        data[0] = flags
        self.send(xid, "_NET_MOVERESIZE_WINDOW", data)

    # ---- verified operations
    def wait(self, pred, timeout):
        """poll pred() (a read-back from the server) until truthy or timeout; ~1 ms steps"""
        t0 = time.monotonic()
        while True:
            r = pred()
            if r:
                return r
            if time.monotonic() - t0 >= timeout:
                return pred()
            time.sleep(0.001)

    def op(self, xid, op, body=None, kill=None):
        """one window operation, verified against the server -> (ok, detail).
        kill(xid, force) -> bool is the caller's last resort for close (a game that ignores
        WM_DELETE_WINDOW has no titlebar to fall back on: openbox runs decoration-free);
        force=False is SIGTERM, force=True SIGKILL."""
        body = body or {}
        if op not in OPS:
            return False, f"unknown op {op}"
        if xid not in self.clients():
            return False, "no such window"
        g = self.geom(xid)
        if g is None:
            return False, "no such window"
        if op in ("raise", "activate", "restore"):
            self.activate(xid)

            def up():
                gg = self.geom(xid)
                return gg is not None and gg[4] and self.active() == xid
            ok = self.wait(up, 1.0)
            return bool(ok), {"active": self.active() == xid, "minimized": not (self.geom(xid) or (0, 0, 0, 0, False))[4]}
        if op == "minimize":
            self.iconify(xid)

            def down():
                gg = self.geom(xid)
                return gg is None or not gg[4]
            ok = self.wait(down, 2.0)
            return bool(ok), {"minimized": bool(ok), "hidden": "_NET_WM_STATE_HIDDEN" in self.states(xid)}
        if op == "close":
            self.close(xid)
            gone = self.wait(lambda: xid not in self.clients(), 2.0)
            if not gone and kill is not None and kill(xid, False):
                # SDL turns SIGTERM into a QUIT event — a game that ignores the WM's close
                # request ignores that too (measured): escalate to SIGKILL
                gone = self.wait(lambda: xid not in self.clients(), 1.5)
                if not gone and kill(xid, True):
                    gone = self.wait(lambda: xid not in self.clients(), 1.5)
                    return bool(gone), {"killed": "SIGKILL"}
                return bool(gone), {"killed": "SIGTERM"}
            return bool(gone), None
        if op in ("move", "resize", "place"):
            try:
                want = {k: int(body[k]) for k in {"move": "xy", "resize": "wh", "place": "xywh"}[op]}
            except (KeyError, TypeError, ValueError):
                return False, f"{op} needs integer " + " ".join({"move": "xy", "resize": "wh", "place": "xywh"}[op])
            self.moveresize(xid, **want)

            def placed():
                gg = self.geom(xid)
                return gg if gg and all(gg["xywh".index(k)] == v for k, v in want.items()) else None
            r = self.wait(placed, 2.0)
            gg = r or self.geom(xid)
            return bool(r), {"x": gg[0], "y": gg[1], "w": gg[2], "h": gg[3]} if gg else "no such window"
        return False, f"unknown op {op}"

    # ---- the list the shell's taskbar renders
    def snapshot(self, registry=None):
        mons = self.monitor_list()
        rects = [(m["x"], m["y"], m["w"], m["h"]) for m in mons]
        active = self.active()
        out, names = [], {}                 # comm cache for this snapshot only (pids recycle)
        for xid in self.clients():
            inst, cls = self.wm_class(xid)
            pid = self.pid(xid)
            if is_desktop_window(inst, cls, pid, names):
                continue
            types = self.window_types(xid)
            if types and not (types & LISTED_TYPES):
                continue
            g = self.geom(xid)
            if g is None:
                continue
            game = registry.lookup(pid) if registry else None
            kind = "game" if game or any(k in f"{inst} {cls}".lower() for k in GAME_CLASSES) else "other"
            out.append({"id": xid, "hex": hex(xid), "title": self.title(xid), "class": cls, "instance": inst,
                        "pid": pid, "app": game["app"] if game else None,
                        "icon": game["icon"] if game else None, "kind": kind,
                        "x": g[0], "y": g[1], "w": g[2], "h": g[3], "minimized": not g[4],
                        "active": xid == active, "screen": screen_of(rects, g[0] + g[2] // 2, g[1] + g[3] // 2)})
        # [R90] monitors + desktop: the shell's live monitor list (a hot-plug / a layout change pushes a new one)
        return {"wm": self.wm_name(), "screens": [list(r) for r in rects], "monitors": mons,
                "desktop": monmath.union(mons), "windows": out}


class _XThread:
    """ONE daemon thread running the blocking python-xlib calls in order (python-xlib is
    synchronous and not thread-safe; a ThreadPoolExecutor's worker would be joined at
    interpreter exit — a call stuck in a hung X server would then keep the kernel from
    exiting until systemd's SIGKILL, a daemon thread never does [D-R68-3])"""

    def __init__(self, name):
        self.q = queue.Queue()
        self.t = threading.Thread(target=self._run, name=name, daemon=True)
        self.t.start()

    def _run(self):
        while True:
            loop, fut, fn, args = self.q.get()
            try:
                r, ok = fn(*args), True
            except BaseException as e:      # noqa: BLE001 — handed to the awaiting coroutine
                r, ok = e, False
            try:
                loop.call_soon_threadsafe(self._settle, fut, ok, r)
            except RuntimeError:            # loop closed underneath us (kernel exiting)
                pass

    @staticmethod
    def _settle(fut, ok, r):
        if not fut.done():                  # the awaiter may have timed out meanwhile
            (fut.set_result if ok else fut.set_exception)(r)

    def submit(self, loop, fn, *args):
        fut = loop.create_future()
        self.q.put((loop, fut, fn, args))
        return fut


class WindowService:
    """asyncio facade over EWMH: one ops thread with a persistent Display, one watcher
    thread turning X events into notifications for the SSE stream; both reconnect when
    X restarts and both start lazily (a kernel that never meets a kiosk starts no thread)."""
    OP_TIMEOUT = 6.0

    def __init__(self):
        self._x = None                  # _XThread, started by the first X call
        self._client = None             # EWMH — ops thread only
        self._client_name = None
        self._client_gen = 0            # the X server generation the client was made for
        self._xgen = 0                  # bumped by the watcher when ITS connection dies
        self._loop = None
        self._waiters = set()           # asyncio.Event per open stream
        self._watcher = None
        self._closing = False           # kernel stopping: streams return [D-R68-3]
        self.registry = GameRegistry()
        self.frames = {}                # [R82] frame id -> the pair record (see attach_frame)
        self.xerrors = 0
        self.reconnects = 0

    # ---- display discovery (loop thread; cheap, no X traffic)
    def display(self):
        """(display name, its socket path) — hw._display() finds the live X like the
        display API does; the socket check keeps a dev box / test run from ever connecting"""
        name = hw._display()
        n = name.lstrip(":").split(".")[0]
        return name, hw.X11_SOCKETS / f"X{n}"

    def unavailable(self, reason, name=None):
        return {"available": False, "reason": reason, "display": name or self.display()[0],
                "wm": None, "screens": [], "monitors": [], "desktop": None, "windows": []}

    async def _run(self, fn, *args):
        loop = asyncio.get_running_loop()
        self._loop = loop
        if self._x is None:
            self._x = _XThread("kairos-wm")
        return await asyncio.wait_for(self._x.submit(loop, fn, *args), self.OP_TIMEOUT)

    def x_available(self):
        """is there a kiosk X display to ask? (the socket check only — no X traffic)"""
        return self.display()[1].exists()

    async def list(self):
        """the managed windows + "crashed": the last games that died after the kernel
        reported them running [D-R68-6] (part of every stream snapshot: a change pushes)"""
        name, sock = self.display()
        crashed = self.registry.crashes()
        frames = self.frames_out()       # [R82] the pairs: a reloaded kiosk page rebuilds its game frames from them
        if not sock.exists():
            return dict(self.unavailable(f"no X display ({sock} missing)", name), crashed=crashed, frames=frames)
        try:
            snap = await self._run(self._snapshot, name)
        except asyncio.TimeoutError:
            return dict(self.unavailable(f"{name} did not answer within {self.OP_TIMEOUT:.0f} s", name), crashed=crashed,
                        frames=frames)
        except WMError as e:
            return dict(self.unavailable(str(e), name), crashed=crashed, frames=frames)
        return dict({"available": True, "display": name, "crashed": crashed, "frames": self.frames_out()}, **snap)

    async def wait_window(self, pid, timeout, until=None):
        """[D-R68-6] Wait until a MAPPED window owned by pid is listed on the kiosk display
        -> (window, askable): the window as list() reports it, or None after `timeout` s
        or as soon as `until` (a Future — the process exiting) is done; askable=False
        when the display could not be asked at all (no X socket, X not answering, no
        EWMH window manager keeping _NET_CLIENT_LIST) — the caller then falls back to
        its survival probe rather than judging a game by a list nobody maintains.
        Woken by the watcher thread on every map/unmap/client-list change, re-read at
        least every 0.5 s (a window that mapped before the watcher had connected)."""
        loop = asyncio.get_running_loop()
        self._loop = loop
        if not self.x_available():
            return None, False
        self._ensure_watcher()
        ev = asyncio.Event()
        self._waiters.add(ev)
        askable = False
        try:
            t_end = time.monotonic() + timeout
            while True:
                snap = await self.list()
                askable = bool(snap.get("available")) and snap.get("wm") is not None
                for w in snap.get("windows", ()):
                    if w["pid"] == pid and not w["minimized"]:
                        return w, True
                left = t_end - time.monotonic()
                if left <= 0 or (until is not None and until.done()):
                    return None, askable
                ev.clear()
                waiter = loop.create_task(ev.wait())
                await asyncio.wait([waiter] + ([until] if until is not None else []),
                                   timeout=min(left, 0.5), return_when=asyncio.FIRST_COMPLETED)
                if not waiter.done():
                    waiter.cancel()
        finally:
            self._waiters.discard(ev)

    def note_crash(self, rec):
        """[D-R68-6] a game the kernel launched died after it was reported running: kept
        (last 5) in every list()/stream snapshot as "crashed" so the desktop can tell
        the user — the process itself is forgotten by then"""
        self.registry.note_crash(rec)
        self._notify()

    async def op(self, xid, op, body=None):
        name, sock = self.display()
        try:
            xid = int(str(xid), 0)
        except ValueError:
            return {"ok": False, "error": f"bad window id {xid!r}", "status": 400}
        if op not in OPS:
            return {"ok": False, "error": f"unknown op {op} (ops: {', '.join(OPS)})", "status": 400}
        if not sock.exists():
            return dict(self.unavailable(f"no X display ({sock} missing)", name), ok=False, status=503)
        try:
            ok, detail = await self._run(self._op, name, xid, op, body or {})
        except asyncio.TimeoutError:
            return {"ok": False, "error": f"{name} did not answer within {self.OP_TIMEOUT:.0f} s", "status": 504}
        except WMError as e:
            return dict(self.unavailable(str(e), name), ok=False, status=503)
        res = {"ok": bool(ok), "op": op, "id": xid}
        if isinstance(detail, str):
            res["error"] = detail
            res["status"] = 404 if detail == "no such window" else 400
        elif detail is not None:
            res["detail"] = detail
        return res

    def register_game(self, pid, app, title=None, icon=None, frame=None):
        """[R82] `frame` = the desktop frame the game runs in ({id, screen, x, y, w, h, vw, vh}: the frame body's
        rect in the page's CSS pixels + the page's viewport): its window is paired with the frame as soon as it maps"""
        self.registry.register(pid, app, title, icon)
        if frame:
            self.attach_frame(frame, pid, app, title)
        self._notify()

    def forget_game(self, pid):
        self.registry.forget(pid)
        self._notify()

    def running_for(self, app):
        """[R82] the live processes the kernel launched for this app (slug): [(pid, registry entry)]"""
        return [(p, g) for p, g in self.registry.running().items() if g["app"] == app and _alive(p)]

    # ---- [R82] the frame <-> game-window pairs (D0 REPORT §7: in the kernel, not in shell.js, so the pairing survives
    # a move to real browser windows). One record per desktop frame, keyed by the frame's id (the page makes it
    # unique): the frame body's rect in CSS pixels + the page's viewport and screen, the game process and its window,
    # covered (a menu, a toast, a dialog, another window lies over the frame: the shell shows the R70 screenshot and
    # the game is minimized + frozen), minimized (the frame is minimized), ended (the game exited: pid, code, crash).
    @staticmethod
    def _frame_spec(spec):
        """the CSS rect + viewport of a frame body from the shell -> dict, or None when malformed"""
        try:
            css = [int(round(float(spec[k]))) for k in ("x", "y", "w", "h")]
            view = [int(round(float(spec.get("vw") or 0))), int(round(float(spec.get("vh") or 0)))]
            screen = int(spec.get("screen") or 0)
            # where the page's own window is: window.screenX/Y (CSS px) x devicePixelRatio — optional
            at = ([float(spec["sx"]), float(spec["sy"])] if spec.get("sx") is not None and spec.get("sy") is not None
                  else None)
            dpr = float(spec.get("dpr") or 1.0)
            # the browser's chrome around the viewport (CSS px): 0 in the kiosk, a title bar in a plain --app window
            chrome = [max(0.0, min(100.0, float(spec.get("cx") or 0))), max(0.0, min(200.0, float(spec.get("cy") or 0)))]
        except (KeyError, TypeError, ValueError):
            return None
        if css[2] <= 0 or css[3] <= 0 or not 0.25 <= dpr <= 8:
            return None
        return {"css": css, "view": view, "screen": max(0, screen), "at": at, "dpr": dpr, "chrome": chrome}

    def attach_frame(self, spec, pid, app, title=None):
        """the game `pid` (launched for `app`) runs in the frame spec["id"]: a new launch into a frame (first start,
        ✨ improve, a revert) starts shown — not covered, not minimized, nothing ended"""
        fid = str((spec or {}).get("id") or "")[:96]
        geo = self._frame_spec(spec or {})
        if not fid or geo is None:
            return None
        f = self.frames.get(fid) or {"id": fid}
        f.update(geo, slug=app, title=title or app, pid=int(pid), xid=0, covered=False, minimized=False, ended=None,
                 kiosk=0, origin=None, scale=None, fixed=None, placed=None, lowered=False, had_focus=False, t=time.time())
        self.frames[fid] = f
        g = self.registry.lookup(pid)
        if g is not None:
            g["frame"] = fid
        self._prune_frames()
        return f

    def _prune_frames(self):
        if len(self.frames) <= FRAME_KEEP:
            return
        for fid, _ in sorted(self.frames.items(), key=lambda kv: (kv[1].get("ended") is None, kv[1].get("t", 0))):
            if len(self.frames) <= FRAME_KEEP:
                break
            self.frames.pop(fid, None)

    def frames_out(self):
        return [{k: f.get(k) for k in ("id", "screen", "slug", "title", "pid", "xid", "covered", "minimized", "ended")}
                for f in self.frames.values()]

    def frame_of_pid(self, pid):
        g = self.registry.lookup(pid)
        return self.frames.get(g.get("frame")) if g and g.get("frame") else None

    def frame_ended(self, pid, ended):
        """the game of a frame exited (the reaper): the frame says so — {pid, returncode, after_s, killed, crash}"""
        for f in self.frames.values():
            if f.get("pid") == pid:
                f.update(ended=dict(ended, pid=pid), xid=0, t=time.time())
                self._notify()

    async def frame_root(self, spec):
        """[R82] the frame body's rect in ROOT pixels (x, y, w, h) — where a game launched into that frame should map
        (SDL_VIDEO_WINDOW_POS) — or None without X"""
        geo = self._frame_spec(spec or {})
        name, sock = self.display()
        if geo is None or not sock.exists():
            return None
        try:
            return await self._run(self._guarded, name, lambda c: self._body_root(c, dict(geo), refresh=True))
        except (asyncio.TimeoutError, WMError):
            return None

    async def frame_op(self, fid, op, body=None):
        """POST /api/frames/{fid}/{op} -> {ok, op, frame, detail?|error, status?}; `snapshot` adds the image bytes
        under "image" (BGRX rows, see EWMH.grab) with its rect. The record is updated first (here, on the loop
        thread), then the X side runs on the ops thread — without X the record still follows the shell."""
        body = body if isinstance(body, dict) else {}
        fid = str(fid)[:96]
        if op not in FRAME_OPS:
            return {"ok": False, "error": f"unknown op {op} (ops: {', '.join(FRAME_OPS)})", "status": 400}
        f = self.frames.get(fid)
        if op == "adopt":
            geo = self._frame_spec(body)
            try:
                xid = int(str(body.get("xid")), 0)
            except (TypeError, ValueError):
                xid = 0
            if geo is None or not xid:
                return {"ok": False, "error": "adopt needs the window's xid and the frame body's x y w h", "status": 400}
        elif f is None:
            return {"ok": False, "error": "no such frame", "status": 404}
        elif op in ("place", "follow"):
            geo = self._frame_spec(dict(body, screen=body.get("screen", f.get("screen"))))
            if geo is None:
                return {"ok": False, "error": f"{op} needs the frame body's x y w h (CSS px) and vw vh", "status": 400}
            if geo["screen"] != f.get("screen") or geo["at"] != f.get("at") or geo["view"] != f.get("view") \
                    or geo["chrome"] != f.get("chrome"):
                f.update(kiosk=0, origin=None, scale=None)       # another page / window / viewport: find its kiosk again
            f.update(geo)
        elif op == "cover":
            f["covered"] = True
        elif op == "uncover":
            f["covered"] = False
        elif op == "minimize":
            f["minimized"] = True
        elif op == "restore":
            f["minimized"] = False
        elif op == "release":
            self.frames.pop(fid, None)
            g = self.registry.lookup(f.get("pid"))
            if g is not None and g.get("frame") == fid:
                g["frame"] = None
            self._notify()
            return {"ok": True, "op": op, "frame": fid}
        name, sock = self.display()
        if not sock.exists():
            return dict(self.unavailable(f"no X display ({sock} missing)", name), ok=False, op=op, frame=fid, status=503)
        try:
            ok, detail = await self._run(self._frame_x, name, fid, op, body)
        except asyncio.TimeoutError:
            return {"ok": False, "op": op, "frame": fid, "error": f"{name} did not answer within {self.OP_TIMEOUT:.0f} s",
                    "status": 504}
        except WMError as e:
            return dict(self.unavailable(str(e), name), ok=False, op=op, frame=fid, status=503)
        if op == "close" and ok:
            self.frames.pop(fid, None)
        if op != "snapshot":
            self._notify()
        res = {"ok": bool(ok), "op": op, "frame": fid}
        if isinstance(detail, str):
            res["error"] = detail
            res["status"] = 404 if detail.startswith("no ") else 400
        elif detail is not None:
            res["detail"] = detail
        return res

    async def stream(self, send, busy=5.0, idle=30.0):
        """Push the list through send(snapshot) on every change the watcher sees
        (coalesced 100 ms), re-read every `busy` s while windows exist (a game may
        rename its window without an event we watch), every `idle` s otherwise —
        send(None) is the keepalive. Returns when send raises (client gone) or when
        close_streams() was called (kernel stopping)."""
        self._loop = asyncio.get_running_loop()
        self._ensure_watcher()
        ev = asyncio.Event()
        self._waiters.add(ev)
        last = None
        try:
            while not self._closing:
                snap = await self.list()
                if snap != last:
                    await send(snap)
                    last = snap
                else:
                    await send(None)
                try:
                    await asyncio.wait_for(ev.wait(), timeout=busy if snap.get("windows") else idle)
                    if self._closing:
                        break
                    await asyncio.sleep(0.1)          # coalesce the burst one change produces
                    ev.clear()
                except asyncio.TimeoutError:
                    pass
        finally:
            self._waiters.discard(ev)

    def close_streams(self):
        """every open stream() returns; its SSE handler then ends the response [D-R68-3]"""
        self._closing = True
        self._wake()

    async def shutdown(self):
        """Kernel stop [D-R68-3]: end the streams, then thaw + end the games the kernel
        launched. A SIGSTOPped game would sit in the unit's cgroup ignoring systemd's
        SIGTERM until the SIGKILL at TimeoutStopSec — a `timeout` stop and a false
        failure capture on the ESP; a running one dies with the cgroup anyway (and is an
        orphan in a dev/test run). SIGTERM (SDL turns it into a QUIT event: a game may
        save), ≤ 1.5 s, then SIGKILL for one that ignores it — the close op's escalation,
        bounded. Never raises."""
        self.close_streams()
        pids = list(self.registry.running())
        for pid in pids:
            self.registry.mark_killed(pid)           # not a crash when its reaper sees the exit [D-R68-6]
            _signal_game(pid, signal.SIGCONT)
            _signal_game(pid, signal.SIGTERM)
        t0 = time.monotonic()
        while any(_alive(p) for p in pids) and time.monotonic() - t0 < 1.5:
            await asyncio.sleep(0.05)
        killed = [p for p in pids if _alive(p) and _signal_game(p, signal.SIGKILL)]
        return {"games": len(pids), "killed": killed}

    async def screenshot(self, screen=None):
        """The kiosk display as raw BGRX pixels + its root rect [R70]: every monitor as X
        lays them out (the root cropped to the monitors' bounding box) or one monitor by
        its number in the monitor list (primary first) [R90]. -> (data, (x, y, w, h)); WMError
        without X or for an unknown screen; the caller fits/encodes (server.fit_image)."""
        name, sock = self.display()
        if not sock.exists():
            raise WMError(f"no X display ({sock} missing)")
        return await self._run(self._screenshot, name, screen)

    def _screenshot(self, name, screen):
        def run(c):
            rects = c.monitors()
            if screen is None:
                x0, y0 = min(r[0] for r in rects), min(r[1] for r in rects)
                x1, y1 = max(r[0] + r[2] for r in rects), max(r[1] + r[3] for r in rects)
                rect = (x0, y0, x1 - x0, y1 - y0)
            elif 0 <= int(screen) < len(rects):
                rect = tuple(rects[int(screen)])
            else:
                raise WMError(f"no screen {screen} (this display has {len(rects)})")
            return c.grab(rect), rect
        return self._guarded(name, run)

    # ---- ops thread
    def _xauth_env(self, name):
        """python-xlib reads the cookie file from XAUTHORITY; the kernel (root, no X
        session) uses the kiosk user's, exactly like game_env()/hw._xenv() do"""
        env = hw._xenv()
        os.environ.setdefault("XAUTHORITY", env["XAUTHORITY"])
        return name or env["DISPLAY"]

    def _connect(self, name):
        if self._client is not None and (self._client_name != name or self._client_gen != self._xgen):
            self._drop()                # X moved to another display, or the watcher saw it restart
        if self._client is not None:
            return self._client
        try:
            from Xlib import display as xdisplay
        except ImportError as e:
            raise WMError("python3-xlib is not installed") from e
        gen = self._xgen
        try:
            d = xdisplay.Display(self._xauth_env(name))
        except Exception as e:
            raise WMError(f"cannot connect to {name}: {e}") from e
        d.set_error_handler(self._xerror)      # asynchronous BadWindow etc.: counted, never printed
        self._client, self._client_name, self._client_gen = EWMH(d), name, gen
        return self._client

    def _xerror(self, *_):
        self.xerrors += 1

    def _drop(self):
        c, self._client, self._client_name = self._client, None, None
        if c is not None:
            try:
                c.d.close()
            except Exception:
                pass

    def _guarded(self, name, fn):
        """run fn(client) on the ops thread. A dead X (kiosk relaunch, driver reset, X
        restarted on the same :0) surfaces as a ConnectionClosedError / socket error from
        ANY call — the readers let those through (_lost) — so the Display is dropped and
        the call is retried ONCE on a fresh connection: the request that discovers the
        restart already answers from the new server; only a second failure in a row is
        reported [D-R68-1]."""
        for attempt in (1, 2):
            try:
                return fn(self._connect(name))
            except WMError:
                raise
            except Exception as e:
                self._drop()
                if attempt == 2:
                    raise WMError(f"X connection lost: {type(e).__name__}: {e}") from e
                self.reconnects += 1

    def _snapshot(self, name):
        def run(c):
            snap = c.snapshot(self.registry)
            lowered = {f.get("pid") for f in self.frames.values() if f.get("lowered")}   # [R82] covered: mapped, frozen
            for w in snap["windows"]:           # mapped again by anyone (a WM restart, a client
                if not w["minimized"] and w["pid"] in self.registry.frozen() and w["pid"] not in lowered:   # re-map): let it paint
                    self._thaw(w["pid"])
                if w["app"] is not None:        # a game the kernel launched: the gate/record know its window
                    self.registry.seen(w["pid"], w["id"], not w["minimized"])
            self._pair_new(c, snap)             # [R82] a game window that just mapped joins its frame
            return snap
        snap = self._guarded(name, run)
        for w in snap["windows"]:
            f = self.frame_of_pid(w["pid"])
            w["frame"] = f["id"] if f is not None and f.get("xid") == w["id"] else None
        return snap

    # ---- [R82] the frame pairs, X side (ops thread)
    def _pair_new(self, c, snap):
        """every listed window of a launched game whose frame does not hold it yet: pair it (it just mapped — the
        first start, the next version after ✨, a revert); a frame that is covered or minimized meanwhile hides it"""
        for w in snap["windows"]:
            f = self.frame_of_pid(w["pid"])
            if f is None or f.get("xid") == w["id"] or w["minimized"]:
                continue
            self._pair(c, f, w["id"])
            if f.get("covered") or f.get("minimized"):
                self._hide(c, f, "minimize" if f.get("minimized") else "cover")

    def _pair(self, c, f, xid):
        f["xid"] = xid
        self._body_root(c, f, refresh=True)
        if f.get("kiosk"):
            c.set_transient(xid, f["kiosk"])
        return self._place(c, f, wait=False)

    def _body_root(self, c, f, refresh=False):
        """the frame body's rect in root pixels. CSS -> root through the page's own kiosk window: the Chromium window
        at the origin the page reports (window.screenX/Y), else the one covering most of the frame's monitor [R90]
        (its origin; scale = its width / the page's viewport width, i.e. the device pixel ratio); without a kiosk
        window (a test page in a plain window) through the monitor's rect. Cached per frame; refresh re-reads X."""
        if refresh or f.get("origin") is None:
            k, kg = 0, None
            dpr = f.get("dpr") or 1.0
            if f.get("at"):
                vw = f["view"][0] if f.get("view") else 0
                k, kg = c.kiosk_at(round(f["at"][0] * dpr), round(f["at"][1] * dpr), round(vw * dpr) if vw else None)
            rects = c.monitors()
            mon = rects[f["screen"]] if 0 <= f.get("screen", 0) < len(rects) else rects[0]
            if not k:
                k, kg = c.kiosk_for(mon)
            base = kg or tuple(mon)
            cx = (f.get("chrome") or [0, 0])[0]
            vw = f["view"][0] + 2 * cx if f.get("view") and f["view"][0] > 0 else 0
            f.update(kiosk=k, origin=[base[0], base[1]], scale=base[2] / vw if vw else 1.0)
        s, (ox, oy) = f["scale"], f["origin"]
        cx, cy = f.get("chrome") or [0, 0]
        x, y, w, h = f["css"]
        return (ox + round((cx + x) * s), oy + round((cy + y) * s), max(1, round(w * s)), max(1, round(h * s)))

    def _target(self, c, f, xid):
        """where the game window goes: a fixed-size game is centred in the frame body (never above/left of it), a
        resizable one fills it -> (want dict for moveresize, body rect, game geometry)"""
        bx, by, bw, bh = self._body_root(c, f)
        g = c.geom(xid)
        if g is None:
            return None, (bx, by, bw, bh), None
        if f.get("fixed") is None:
            f["fixed"] = c.size_fixed(xid)
        if f["fixed"]:
            return {"x": bx + max(0, (bw - g[2]) // 2), "y": by + max(0, (bh - g[3]) // 2)}, (bx, by, bw, bh), g
        return {"x": bx, "y": by, "w": bw, "h": bh}, (bx, by, bw, bh), g

    def _place(self, c, f, wait):
        xid = f.get("xid")
        if not xid:
            return True, {"pending": True}          # not mapped yet: it is placed when it maps
        want, body, g = self._target(c, f, xid)
        if want is None:
            return False, "no such window"
        c.moveresize(xid, **want)
        got = None
        if wait:
            def placed():
                gg = c.geom(xid)
                return gg if gg and all(gg["xywh".index(k)] == v for k, v in want.items()) else None
            got = c.wait(placed, 1.0)
        gg = got or c.geom(xid) or g
        w, h = want.get("w", g[2]), want.get("h", g[3])
        f["placed"] = [want["x"], want["y"], w, h]
        s = f["scale"] or 1.0
        return (bool(got) if wait else True), {
            "x": want["x"], "y": want["y"], "w": w, "h": h, "body": list(body), "fixed": f["fixed"],
            "verified": bool(got) if wait else None, "now": list(gg[:4]) if gg else None,
            # what the shell needs to size the frame and to lay the screenshot where the game is (CSS px)
            "css": {"x": round((want["x"] - body[0]) / s, 2), "y": round((want["y"] - body[1]) / s, 2),
                    "w": round(w / s, 2), "h": round(h / s, 2)}}

    def _hide(self, c, f, how):
        """cover: the game drops UNDER the desktop + SIGSTOP (D-R68-2's freeze); minimize: iconified + SIGSTOP.
        Either way it leaves the kiosk's transient group FIRST: iconifying a transient of the FOCUSED kiosk makes
        openbox drop the focus (measured 2026-10-15: active -> none, Chromium blurs — the R74 system menu that
        covered the game closed itself at once). A covered game is lowered, not unmapped: openbox restacks it under
        the kiosk without any focus change and the transient hint alone lifts it back (no map, no repaint wait, no
        activation). A game that had the keyboard hands it to the desktop that covers it."""
        xid, pid = f.get("xid"), f.get("pid")
        if not xid:
            return True, {"pending": True}
        g = c.geom(xid)
        if g is None:
            return False, "no such window"
        visible = g[4] and not f.get("lowered")
        if visible:
            f["had_focus"] = c.active() == xid       # uncover "keep" gives it back only if it had it
        c.set_transient(xid, 0)
        if how == "minimize":
            ok, d = (c.op(xid, "minimize") if g[4] else (True, {"minimized": True}))
        else:
            c.restack(xid, f.get("kiosk") or 0, below=True)
            f["lowered"] = True
            under = c.wait(lambda: c.stacked_above(xid, f["kiosk"]) is not True, 0.5) if f.get("kiosk") else True
            ok, d = True, {"lowered": True, "verified": bool(under) if f.get("kiosk") else None}
            if visible and f.get("had_focus") and f.get("kiosk"):
                c.activate(f["kiosk"])               # the keyboard goes to the desktop that covers the game
        if ok and self.registry.lookup(pid):
            self._freeze(pid, xid)
        if not ok:
            return False, d if isinstance(d, str) else "the window did not minimize"
        return True, dict(d if isinstance(d, dict) else {},
                          frozen=bool(self.registry.lookup(pid) and self.registry.lookup(pid).get("frozen")))

    def _show(self, c, f, focus):
        """uncover/restore: thaw first (a stopped game cannot repaint), then openbox's activate (maps + raises).
        It also FOCUSES the game (measured: a map request of an iconic window is an activation in openbox too) —
        unless `focus` says so (true: the user clicked the frame / the dock; "keep": the game gets the keyboard back
        only if it had it when it was covered — a toast that passed over a game in the background must not take the
        keyboard from the user) the window that had the focus gets it back at once."""
        if focus == "keep":
            focus = bool(f.get("had_focus"))
        xid, pid = f.get("xid"), f.get("pid")
        if not xid:
            return True, {"pending": True}
        if f.get("covered") or f.get("minimized"):
            return True, {"held": "covered" if f.get("covered") else "minimized"}
        g = c.geom(xid)
        if g is None:
            return False, "no such window"
        prev = c.active()
        self._thaw(pid)                              # first: a stopped game cannot repaint
        if f.get("kiosk"):
            c.set_transient(xid, f["kiosk"])         # back into the kiosk's group: over the desktop again
        refocused = False
        if not g[4]:                                 # minimized: openbox's activate maps it (and focuses it)
            ok, d = c.op(xid, "restore")
            if ok and not focus and prev and prev != xid and prev in c.clients():
                c.activate(prev)
                refocused = True
        else:                                        # lowered under the desktop: lifted, the focus untouched
            c.restack(xid, 0, below=False)
            up = c.wait(lambda: c.stacked_above(xid, f["kiosk"]) is not False, 0.5) if f.get("kiosk") else True
            ok, d = True, {"raised": True, "verified": bool(up) if f.get("kiosk") else None}
            if focus:
                c.activate(xid)
        f["lowered"] = False
        if ok:
            self._place(c, f, wait=False)            # the frame may have moved while the game was hidden
        if not ok:
            return False, d if isinstance(d, str) else "the window did not come back"
        return True, dict(d if isinstance(d, dict) else {}, refocused=refocused, focus=bool(focus))

    def _frame_x(self, name, fid, op, body):
        def run(c):
            f = self.frames.get(fid)
            if op == "adopt":
                xid = int(str(body.get("xid")), 0)
                if xid not in c.clients():
                    return False, "no such window"
                pid = c.pid(xid)
                g = self.registry.lookup(pid)
                if g is None:
                    return False, "not a game the kernel launched"
                old = g.get("frame")
                if old and old != fid:
                    self.frames.pop(old, None)           # the page that held it is gone (reloaded) or it moves on
                f = self.attach_frame(dict(body, id=fid), pid, g["app"], g.get("title"))
                if f is None:
                    return False, "adopt needs the frame body's x y w h"
                self._thaw(pid)
                gg = c.geom(xid)
                if gg is not None and not gg[4]:
                    c.op(xid, "restore")
                return self._pair(c, f, xid)
            if f is None:
                return False, "no such frame"
            xid = f.get("xid")
            if xid and c.geom(xid) is None:
                f["xid"] = xid = 0
            if op in ("place", "follow"):
                if op == "place":
                    self._body_root(c, f, refresh=True)
                return self._place(c, f, wait=(op == "place"))
            if op == "snapshot":
                if not xid:
                    return False, "no window to capture"
                g = c.geom(xid)
                if not g[4]:
                    return False, "no window to capture (hidden)"
                rg = c.root.get_geometry()
                x0, y0 = max(0, g[0]), max(0, g[1])
                x1, y1 = min(rg.width, g[0] + g[2]), min(rg.height, g[1] + g[3])
                if x1 <= x0 or y1 <= y0:
                    return False, "the window is off screen"
                rect = (x0, y0, x1 - x0, y1 - y0)
                body_root = self._body_root(c, f)
                s = f["scale"] or 1.0
                return True, {"image": c.grab(rect), "rect": list(rect), "body": list(body_root),
                              "css": {"x": round((rect[0] - body_root[0]) / s, 2), "y": round((rect[1] - body_root[1]) / s, 2),
                                      "w": round(rect[2] / s, 2), "h": round(rect[3] / s, 2)}}
            if op in ("cover", "minimize"):
                return self._hide(c, f, op)
            if op in ("uncover", "restore"):
                want = body.get("focus", op == "restore")
                return self._show(c, f, "keep" if want == "keep" else bool(want))
            if op == "focus":
                if not xid:
                    return True, {"pending": True}
                if f.get("covered") or f.get("minimized"):
                    return True, {"held": True}
                self._thaw(f.get("pid"))
                return c.op(xid, "raise")
            if op == "close":
                if not xid:
                    return True, {"pending": True}
                pid = f.get("pid")
                if self.registry.lookup(pid):
                    self._thaw(pid)
                return c.op(xid, "close", kill=lambda x, force: self._kill(c, x, force))
            return False, f"unknown op {op}"
        return self._guarded(name, run)

    def _op(self, name, xid, op, body):
        def run(c):
            pid = c.pid(xid)
            game = self.registry.lookup(pid)
            if game and op != "minimize":
                self._thaw(pid)         # first: a stopped process can neither map/repaint nor answer WM_DELETE
            ok, detail = c.op(xid, op, body, kill=lambda x, force: self._kill(c, x, force))
            if ok and op == "minimize" and game:
                self._freeze(pid, xid)
            return ok, detail
        return self._guarded(name, run)

    def _kill(self, c, xid, force=False):
        """close's last resort: only a game the kernel launched itself (registered pid)"""
        pid = c.pid(xid)
        if not self.registry.lookup(pid):
            return False
        self.registry.mark_killed(pid)               # asked for: not a crash [D-R68-6]
        return _signal_game(pid, signal.SIGKILL if force else signal.SIGTERM)

    def _freeze(self, pid, xid):
        """[D-R68-2] a minimized pygame game keeps its 60 fps loop running — iconified it
        still burnt 6.8 % of a core and 15 % of the GPU on the 4070 (VM test 2026-09-11),
        SDL gets no event the generated code acts on — so the kernel SIGSTOPs it: only a
        pid it launched itself (registry), never an unknown process. Trade-off, accepted
        on purpose: the process is frozen wholesale — its audio stops mid-buffer (silence,
        no fade), sockets and timers stall (a network peer may time out), a title/score it
        would have written waits; all of it resumes on SIGCONT, which restore/raise/
        activate/close send FIRST, as does a map of the window by anyone (watcher +
        snapshot) and the kernel's shutdown."""
        g = self.registry.lookup(pid)
        if g and _signal_game(pid, signal.SIGSTOP):
            g["frozen"], g["xid"] = True, xid

    def _thaw(self, pid):
        g = self.registry.lookup(pid)
        if g:
            _signal_game(pid, signal.SIGCONT)      # harmless for a running process
            g["frozen"], g["xid"] = False, 0

    # ---- watcher thread
    def _ensure_watcher(self):
        if self._watcher is None or not self._watcher.is_alive():
            self._watcher = threading.Thread(target=self._watch, name="kairos-wm-watch", daemon=True)
            self._watcher.start()

    def _watch(self):
        """own X connection, only listening: map/unmap/destroy/configure/reparent of the
        root's children and root property changes (_NET_CLIENT_LIST, _NET_ACTIVE_WINDOW,
        ...) wake the streams at once; every managed client is also watched for its own
        property changes (a pygame window maps as "pygame window" and gets its caption a
        few ms later), rate-limited to 2/s — a game that writes its score into the title
        every frame must not turn into a message storm. Blocks in next_event: zero cost
        while nothing happens. When ITS connection dies X is gone: the server generation
        is bumped so the ops thread reconnects before its next call even if that socket
        has not errored yet, and the streams re-read (available:false until X is back,
        the fresh list the moment this thread has reconnected) [D-R68-1]."""
        while True:
            name, sock = self.display()
            if not sock.exists():
                time.sleep(3)
                continue
            d = None
            try:
                from Xlib import display as xdisplay
                d = xdisplay.Display(self._xauth_env(name))
                d.set_error_handler(self._xerror)
                root = d.screen().root
                root.change_attributes(event_mask=SUBSTRUCTURE_NOTIFY_MASK | PROPERTY_CHANGE_MASK)
                # [R90] RandR: a monitor plugged / unplugged / moved / made primary changes the desktop's monitor list
                # (the shell lays itself out from the snapshot's `monitors`) — often without any window event
                rr_events = _randr_watch(d, root)
                d.flush()
                client_list = d.intern_atom("_NET_CLIENT_LIST")
                watched = set()

                def watch_clients():
                    p = root.get_full_property(client_list, ANY_PROPERTY_TYPE)
                    for xid in (int(x) for x in p.value) if p else ():
                        if xid not in watched:
                            watched.add(xid)
                            d.create_resource_object("window", xid).change_attributes(event_mask=PROPERTY_CHANGE_MASK)
                    d.flush()
                watch_clients()
                self._notify()                          # (re)connected: streams re-read
                last_prop = 0.0
                while True:
                    ev = d.next_event()
                    t = ev.type & 0x7f
                    if t in rr_events:
                        self._notify()
                        continue
                    if t not in CHANGE_EVENTS:
                        continue
                    if t == EV_MAP:                     # mapped again by anyone: a frozen game must paint
                        pid = self.registry.frozen_by_xid(getattr(getattr(ev, "window", None), "id", 0))
                        if pid:
                            self._thaw(pid)
                    if t == EV_PROPERTY and getattr(ev, "window", root).id != root.id:
                        now = time.monotonic()
                        if now - last_prop < 0.5:
                            continue
                        last_prop = now
                    elif t == EV_PROPERTY and ev.atom == client_list:
                        watch_clients()
                    self._notify()
            except Exception:
                self._xgen += 1                         # X went away: the ops thread's Display is stale too
                self._notify()                          # streams show available:false
                time.sleep(2)
            finally:
                if d is not None:
                    try:
                        d.close()
                    except Exception:
                        pass

    def _notify(self):
        loop = self._loop
        if loop is not None and not loop.is_closed():
            try:
                loop.call_soon_threadsafe(self._wake)
            except RuntimeError:                        # loop closed between the check and the call
                pass

    def _wake(self):
        for ev in list(self._waiters):
            ev.set()


WM = WindowService()
