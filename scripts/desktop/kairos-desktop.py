#!/usr/bin/env python3
"""kAIrOS one desktop across all monitors [R90] — the X side, run by .xinitrc as the kiosk user.

The desktop is ONE kiosk Chromium window (WM_CLASS class "kairos-desktop", an --app window without a frame) that
covers the whole X screen = every monitor. Chromium's --kiosk / fullscreen covers one monitor only, and Chromium
maps a window of the screen's size 1 px short, so this keeps the window exactly at (0, 0, root w, root h):

  kairos-desktop.py monitors [--hint | --union | --rects]
      the monitor list (kernel/monitors.py: mirrors = one, primary first): --hint = the kiosk URL's ?mon= start hint,
      --union = "W H" of the X screen, --rects = one "WxH+X+Y" per monitor (the boot frame on every monitor)
  kairos-desktop.py providers [--bind]
      [R91] the RandR 1.4 providers on one line; --bind makes every unbound output sink (an NVIDIA GPU screen,
      NVIDIA-G0, when kairos-hdmi-prime started this session with it) an output source of the main provider first —
      its HDMI outputs are then ordinary outputs of the one desktop
  kairos-desktop.py watch
      long-running: (0) [R91] the same binding at start and after every RandR change;
      (1) the kiosk window — after it maps, after a WM or the browser moved/resized/fullscreened it,
      after the X screen changed size — is put back to (0, 0, root w, root h), undecorated, not fullscreen, not
      maximized; (2) hot-plug: when the set of CONNECTED outputs changes (a monitor plugged / unplugged), the layout
      is applied: the saved one (/opt/vibeos/cache/display.json, the display API's) when it names exactly the
      connected outputs, else extend — the outputs already on keep their order and mode, a new one goes to the
      right, an unplugged one goes off, a primary always exists. X then resizes the screen, (1) follows, and the
      shell re-lays out from the kernel's monitor list (/api/windows/stream). Nothing is relaunched.

Breadcrumbs on stdout (.xinitrc sends them to /var/log/kairos/x.log). python3-xlib + xrandr (both in the image).
"""
import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "kernel"))
import monitors as monmath  # noqa: E402

KIOSK_CLASS = "kairos-desktop"
BACKGROUND = 0x0A0A14        # the boot screen's tone: what X shows where Chromium has not painted yet (not its grey)
STATE = Path(os.environ.get("VIBEOS_DIR", "/opt/vibeos")) / "cache" / "display.json"
SETTLE = 0.25                # s: RandR / WM events come in bursts — act once the burst is over


LOG_STREAM = None            # stdout (.xinitrc sends it to x.log); the `providers` command logs to stderr instead


def log(msg):
    print(f"kairos-desktop: {msg}", file=LOG_STREAM or sys.stdout, flush=True)


def xrandr(args):
    try:
        r = subprocess.run(["xrandr", *args], capture_output=True, text=True, timeout=25)
        return r.returncode, r.stdout + r.stderr
    except (OSError, subprocess.TimeoutExpired) as e:
        return -1, str(e)


def outputs():
    rc, out = xrandr(["--query"])
    return monmath.parse_xrandr(out) if rc == 0 else []


def monitor_list(disp=None):
    """RandR 1.5 monitors (what openbox and the kernel see), else the xrandr outputs"""
    try:
        if disp is None:
            from Xlib import display
            disp = display.Display()
        from Xlib.ext import randr
        from Xlib.protocol import request
        root = disp.screen().root
        rects = []
        for i, m in enumerate(randr.get_monitors(root, True).monitors):
            try:
                name = request.GetAtomName(display=root.display, atom=m.name).name
            except Exception:
                name = f"monitor-{i}"
            rects.append((name, m.x, m.y, m.width_in_pixels, m.height_in_pixels, bool(m.primary)))
        mons = monmath.from_rects(rects)
        if mons:
            return mons
    except Exception:
        pass
    return monmath.from_outputs(outputs())


def cmd_monitors(argv):
    mons = monitor_list()
    if not mons:
        return 1
    if "--hint" in argv:
        print(monmath.hint(mons))
    elif "--union" in argv:
        u = monmath.union(mons)
        print(u["x"] + u["w"], u["y"] + u["h"])
    elif "--rects" in argv:
        for m in mons:
            print(f"{m['w']}x{m['h']}+{m['x']}+{m['y']}")
    else:
        print(json.dumps({"monitors": mons, "desktop": monmath.union(mons), "dead": monmath.dead_areas(mons)}))
    return 0


# ---------------------------------------------------------------------------------------------- hot-plug layout
def plan_layout(outs, saved_args=None):
    """the xrandr args for the CONNECTED outputs `outs` (parse_xrandr) -> (args, why). The saved layout when it names
    exactly the connected set; else extend: active outputs keep their mode and left-to-right order, newly connected
    ones follow at the right with their preferred mode, connected-but-dark outputs stay as they are only when the
    user switched them off (they are in the saved layout with --off), disconnected outputs that still show a picture
    go off; the primary stays, or the first output becomes it."""
    conn = [o for o in outs if o["connected"]]
    names = {o["output"] for o in conn}
    if saved_args:
        saved_names = {saved_args[i + 1] for i, a in enumerate(saved_args[:-1]) if a == "--output"}
        if saved_names and saved_names == names:
            return list(saved_args), "the saved layout"
    user_off = {saved_args[i + 1] for i, a in enumerate((saved_args or [])[:-2])
                if a == "--output" and saved_args[i + 2] == "--off"}
    stale = [o for o in outs if not o["connected"] and o["resolution"]]
    on = sorted((o for o in conn if o["resolution"]), key=lambda o: (o["position"][0], o["position"][1]))
    new = [o for o in conn if not o["resolution"] and o["output"] not in user_off]
    args, prev = [], None
    primary = next((o["output"] for o in on if o["primary"]), None)
    order = on + new
    if not primary and order:
        primary = order[0]["output"]
    for o in order:
        args += ["--output", o["output"]]
        args += ["--mode", o["resolution"]] if o["resolution"] else ["--auto"]
        args += ["--right-of", prev] if prev else ["--pos", "0x0"]
        if o.get("rotation") and o["rotation"] != "normal":
            args += ["--rotate", o["rotation"]]
        if o["output"] == primary:
            args += ["--primary"]
        prev = o["output"]
    for o in stale:
        args += ["--output", o["output"], "--off"]
    return args, "extend"


def saved_layout():
    try:
        return json.loads(STATE.read_text()).get("xrandr_args") or None
    except Exception:
        return None


# ---------------------------------------------------------------------------------------------- the kiosk window
class Desktop:
    def __init__(self):
        from Xlib import X, display
        self.X = X
        self.d = display.Display()
        self.root = self.d.screen().root
        self.a = {n: self.d.intern_atom(n) for n in (
            "_NET_CLIENT_LIST", "_NET_WM_STATE", "_NET_WM_STATE_FULLSCREEN", "_NET_WM_STATE_MAXIMIZED_VERT",
            "_NET_WM_STATE_MAXIMIZED_HORZ", "_NET_MOVERESIZE_WINDOW")}
        self.connected = None
        self.fixes = 0
        self.painted = 0

    def kiosk(self):
        """the desktop window: the first managed client of class kairos-desktop (a window the same browser opens
        later — Ctrl+N — comes after it in _NET_CLIENT_LIST and is left alone)"""
        p = self.root.get_full_property(self.a["_NET_CLIENT_LIST"], self.X.AnyPropertyType)
        for xid in (p.value if p else []):
            w = self.d.create_resource_object("window", xid)
            try:
                cls = w.get_wm_class()
            except Exception:
                continue
            if cls and cls[1] == KIOSK_CLASS:
                return w
        return None

    def span(self):
        """put the kiosk window at exactly (0, 0, root w, root h) — undo fullscreen / maximized first. -> True when it
        had to be changed"""
        w = self.kiosk()
        if w is None:
            return False
        X = self.X
        try:
            rg = self.root.get_geometry()
            g = w.get_geometry()
            t = w.translate_coords(self.root, 0, 0)
            st = w.get_full_property(self.a["_NET_WM_STATE"], X.AnyPropertyType)
            states = set(st.value) if st else set()
        except Exception:
            return False
        if w.id != self.painted:              # [R28] a new desktop window: X's fill for unpainted parts = the boot tone
            try:
                w.change_attributes(background_pixel=BACKGROUND)
                self.painted = w.id
            except Exception:
                pass
        want = (0, 0, rg.width, rg.height)
        have = (-t.x, -t.y, g.width, g.height)
        bad = {self.a[n] for n in ("_NET_WM_STATE_FULLSCREEN", "_NET_WM_STATE_MAXIMIZED_VERT",
                                   "_NET_WM_STATE_MAXIMIZED_HORZ")} & states
        if have == want and not bad:
            return False
        from Xlib.protocol import event
        for a in bad:                                    # _NET_WM_STATE remove (0), source: pager (2)
            ev = event.ClientMessage(window=w, client_type=self.a["_NET_WM_STATE"],
                                     data=(32, [0, a, 0, 2, 0]))
            self.root.send_event(ev, event_mask=X.SubstructureRedirectMask | X.SubstructureNotifyMask)
        w.configure(x=0, y=0, width=rg.width, height=rg.height)
        self.d.sync()
        self.fixes += 1
        log(f"desktop window {hex(w.id)} {have[2]}x{have[3]}+{have[0]}+{have[1]}"
            f"{' (' + ', '.join(self.d.get_atom_name(a)[14:].lower() for a in bad) + ')' if bad else ''}"
            f" -> {rg.width}x{rg.height}+0+0")
        return True

    def hotplug(self):
        """apply the layout when the set of connected outputs changed since the last look"""
        outs = outputs()
        conn = frozenset(o["output"] for o in outs if o["connected"])
        if self.connected is None:
            self.connected = conn
            return False
        if conn == self.connected or not conn:
            return False
        before = self.connected
        self.connected = conn
        args, why = plan_layout(outs, saved_layout())
        rc, out = xrandr(args)
        log(f"monitors changed ({' '.join(sorted(before)) or '-'} -> {' '.join(sorted(conn))}): {why}: "
            f"xrandr {' '.join(shlex.quote(a) for a in args)} -> rc {rc}{(' ' + out.strip()[-160:]) if rc else ''}")
        return True

    def watch(self):
        from Xlib.ext import randr
        X = self.X
        self.root.change_attributes(event_mask=X.SubstructureNotifyMask | X.StructureNotifyMask)
        ext = self.d.query_extension("RANDR")
        rr = set()
        if ext and ext.present:
            self.root.xrandr_select_input(randr.RRScreenChangeNotifyMask | randr.RRCrtcChangeNotifyMask
                                          | randr.RROutputChangeNotifyMask)
            rr = {ext.first_event + randr.RRScreenChangeNotify, ext.first_event + randr.RRNotify}
        self.d.sync()
        bind_providers()                                 # [R91] an NVIDIA GPU screen's outputs join the X screen first
        self.hotplug()                                   # the connected set at start
        self.span()
        log(f"watching (monitors: {monmath.hint(monitor_list(self.d))})")
        pending_rr = pending_win = False
        while True:
            # wait for the next event, then drain the burst for SETTLE s — a window event is answered at once first:
            # the desktop window is spanned the moment it maps, before Chromium's first frame at the wrong size
            if not (pending_rr or pending_win):
                ev = self.d.next_event()
                pending_rr, pending_win = self.classify(ev, rr, pending_rr, pending_win)
                if pending_win and not pending_rr:
                    self.span()
            t_end = time.monotonic() + SETTLE
            while time.monotonic() < t_end:
                if self.d.pending_events():
                    pending_rr, pending_win = self.classify(self.d.next_event(), rr, pending_rr, pending_win)
                else:
                    time.sleep(0.02)
            if pending_rr:
                bind_providers()                         # [R91] a GPU screen that appeared is bound before its outputs are laid out
                self.hotplug()
            pending_rr = pending_win = False
            self.span()

    @staticmethod
    def classify(ev, rr, pending_rr, pending_win):
        t = ev.type & 0x7f
        if t in rr:
            return True, pending_win
        return pending_rr, True                          # map / configure / property of the root's children


def cmd_watch(_argv):
    try:
        Desktop().watch()
    except Exception as e:                               # X went away (the session ends) or no python3-xlib
        log(f"watch ended: {type(e).__name__}: {e}")
        return 1
    return 0


# ---------------------------------------------------------------------------------------- RandR providers [R91]
def providers():
    rc, out = xrandr(["--listproviders"])
    return monmath.parse_providers(out) if rc == 0 else []


def bind_providers(provs=None):
    """RandR 1.4 reverse PRIME (docs/HDMI-PRIME-2026-10-22.md): every provider that can be an output sink, is not the
    main one and is not bound yet — the NVIDIA GPU screen NVIDIA-G0 of a session kairos-hdmi-prime started, whose
    HDMI port a monitor hangs on — becomes an output source of the main provider; its outputs are RandR outputs of
    the one desktop from then on, and plan_layout treats them like any other. -> [(sink, source, rc)]"""
    provs = providers() if provs is None else provs
    done = []
    for sink, src, sink_id, src_id in monmath.provider_bind_plan(provs):
        rc, out = xrandr(["--setprovideroutputsource", sink_id, src_id])
        log(f"provider {sink} -> output source of {src}: xrandr --setprovideroutputsource {sink_id} {src_id} -> rc {rc}"
            f"{(' ' + out.strip()[-120:]) if rc else ''}")
        done.append((sink, src, rc))
    return done


def cmd_providers(argv):
    """the providers, one line on stdout (the x.log breadcrumb); --bind first binds every unbound sink (logged on stderr)"""
    global LOG_STREAM
    LOG_STREAM = sys.stderr
    provs = providers()
    if "--bind" in argv and bind_providers(provs):
        provs = providers()
    print(monmath.providers_summary(provs))
    return 0


def main(argv):
    if not argv or argv[0] not in ("monitors", "watch", "providers"):
        print(__doc__, file=sys.stderr)
        return 2
    return {"monitors": cmd_monitors, "watch": cmd_watch, "providers": cmd_providers}[argv[0]](argv[1:])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
