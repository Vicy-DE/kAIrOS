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

# never listed — they ARE the desktop: the kiosk Chromium (one per monitor [R52], WM_CLASS
# "chromium"/"Chromium", also its --app helper windows), the boot-animation player, the
# cursor hider, the testrig's VNC publisher
DESKTOP_CLASSES = ("chrom", "mpv", "unclutter", "x11vnc")
LISTED_TYPES = {"_NET_WM_WINDOW_TYPE_NORMAL", "_NET_WM_WINDOW_TYPE_DIALOG"}
GAME_CLASSES = ("pygame", "python", "sdl", "main.py")   # SDL derives WM_CLASS from argv[0]: `python3 main.py`
OPS = ("raise", "activate", "restore", "minimize", "close", "move", "resize", "place")


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
    """[(x, y, w, h, primary)] from RandR 1.5 (the heads openbox consumes); [] without RandR"""
    from Xlib.ext import randr
    return [(m.x, m.y, m.width_in_pixels, m.height_in_pixels, bool(m.primary))
            for m in randr.get_monitors(root, True).monitors]


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

    def monitors(self):
        """[(x, y, w, h)] primary first, then left-to-right / top-to-bottom — the
        numbering .xinitrc gives the kiosks (?screen=N) [R52]"""
        try:
            ms = _randr_monitors(self.root)
        except Exception as e:
            if self.lost(e):
                raise
            ms = []                      # no RandR 1.5 on this server: the root geometry
        rects = [(x, y, w, h) for _, x, y, w, h in sorted((not p, x, y, w, h) for x, y, w, h, p in ms)]
        if not rects:
            g = self.root.get_geometry()  # only a dead connection can fail here: let it raise
            rects = [(0, 0, g.width, g.height)]
        return rects

    def grab(self, rect):
        """the framebuffer pixels of the root rect (x, y, w, h): XGetImage, ZPixmap — the
        32-bit BGRX rows a depth-24 TrueColor visual gives (what x11vnc / scrot read);
        the kiosk's own screen, for /api/vision "screen" [R70]. A lost connection raises."""
        x, y, w, h = rect
        img = self.root.get_image(x, y, w, h, ZPIXMAP, ALL_PLANES)
        if img.depth not in (24, 32):
            raise WMError(f"unsupported screen depth {img.depth} (need a 24/32-bit TrueColor visual)")
        return bytes(img.data)

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
        rects = self.monitors()
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
        return {"wm": self.wm_name(), "screens": [list(r) for r in rects], "windows": out}


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
                "wm": None, "screens": [], "windows": []}

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
        if not sock.exists():
            return dict(self.unavailable(f"no X display ({sock} missing)", name), crashed=crashed)
        try:
            snap = await self._run(self._snapshot, name)
        except asyncio.TimeoutError:
            return dict(self.unavailable(f"{name} did not answer within {self.OP_TIMEOUT:.0f} s", name), crashed=crashed)
        except WMError as e:
            return dict(self.unavailable(str(e), name), crashed=crashed)
        return dict({"available": True, "display": name, "crashed": crashed}, **snap)

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

    def register_game(self, pid, app, title=None, icon=None):
        self.registry.register(pid, app, title, icon)
        self._notify()

    def forget_game(self, pid):
        self.registry.forget(pid)
        self._notify()

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
        the number .xinitrc gives the kiosks [R52]. -> (data, (x, y, w, h)); WMError
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
        snap = self._guarded(name, lambda c: c.snapshot(self.registry))
        for w in snap["windows"]:           # mapped again by anyone (a WM restart, a client
            if not w["minimized"] and w["pid"] in self.registry.frozen():   # re-map): let it paint
                self._thaw(w["pid"])
            if w["app"] is not None:        # a game the kernel launched: the gate/record know its window
                self.registry.seen(w["pid"], w["id"], not w["minimized"])
        return snap

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
