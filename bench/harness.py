#!/usr/bin/env python3
"""kAIrOS desktop-technology benchmark harness — docs/DESKTOP-TECH-EVAL-2026-09.md §6.

  python3 bench/harness.py --candidate baseline|chromium-wm|qtile --env host|vm --runs 3

Runs the §6.1 scenario against one prototype driver (bench/proto/*.py, line protocol on
stdin/stdout), measures M1–M9 and the functional matrix F1–F11 (F12 in the VM), and
writes bench/results/<candidate>-<env>-run<N>.json. Everything is probed on the X
server the harness is attached to ($DISPLAY): pixels via XGetImage, input via XTEST.
stdlib + psutil + python-xlib + Pillow."""
import argparse
import io
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import psutil
from PIL import Image
from Xlib import X, display
from Xlib.ext import xtest

HERE = os.path.dirname(os.path.abspath(__file__))
APPS = os.path.join(HERE, "apps")
BENCH_PORT = int(os.environ.get("BENCH_PORT", "1340"))
BENCH_URL = f"http://127.0.0.1:{BENCH_PORT}"
KERNEL = os.environ.get("BENCH_KERNEL", "http://127.0.0.1:1337")
BAR_H, BORDER = 34, 1                     # .win-bar height + .win border (shell.css / host page)
DOCK_W = 761                              # 12 buttons × 52 + 14 gaps × 6 + 3 seps × (1 + 8) + 2 × 12 padding + 2 border
APP_CYCLE = ["a1", "a2", "a3"]
APP_FILES = {"a1": "canvas-anim.html", "a2": "dom-list.html", "a3": "genimg-page.html",
             "vibe": "vibe-test.html", "f9": "isolation-test.html", "hang": "hang.html"}


def log(msg):
    sys.stderr.write(time.strftime("%H:%M:%S ") + msg + "\n")
    sys.stderr.flush()


def pctl(xs, p):
    if not xs:
        return None
    s = sorted(xs)
    return s[min(len(s) - 1, int(round(p * (len(s) - 1))))]


def summary(xs):
    xs = [x for x in xs if x is not None]
    if not xs:
        return None
    return {"n": len(xs), "p50": statistics.median(xs), "p95": pctl(xs, 0.95), "min": min(xs), "max": max(xs),
            "mean": statistics.fmean(xs)}


# ---------------------------------------------------------------- bench HTTP server (beacons + stub images)
class BenchServer:
    def __init__(self):
        self.marks = []
        self.lock = threading.Lock()
        self.images = {}
        for n in range(1, 7):
            img = Image.new("RGB", (512, 512))
            px = img.load()
            for y in range(512):
                for x in range(512):
                    px[x, y] = ((x * n) & 255, (y * 3 + n * 40) & 255, ((x ^ y) + n * 20) & 255)
            buf = io.BytesIO()
            img.save(buf, "PNG")
            self.images[n] = buf.getvalue()
        srv = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                t = time.time()
                u = urlparse(self.path)
                if u.path == "/mark":
                    q = {k: v[0] for k, v in parse_qs(u.query).items()}
                    q["t_recv"] = t
                    with srv.lock:
                        srv.marks.append(q)
                    self.send_response(200)
                    self.send_header("Content-Type", "image/gif")
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(b"GIF89a\x01\x00\x01\x00\x00\x00\x00;")
                    return
                m = re.fullmatch(r"/img/(\d)\.png", u.path)
                if m and int(m.group(1)) in srv.images:
                    body = srv.images[int(m.group(1))]
                    self.send_response(200)
                    self.send_header("Content-Type", "image/png")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                self.send_response(404)
                self.end_headers()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", BENCH_PORT), H)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def wait_mark(self, app_id, ev, after, timeout=30):
        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout:
            with self.lock:
                for m in self.marks:
                    if m.get("id") == app_id and m.get("ev") == ev and m["t_recv"] >= after:
                        return m
            time.sleep(0.002)
        return None


# ---------------------------------------------------------------- apps into the kernel's cache
def install_apps(vibeos_dir):
    apps_dir = os.path.join(vibeos_dir, "cache", "apps")
    ids = []
    for kind, fname in APP_FILES.items():
        src = open(os.path.join(APPS, fname)).read()
        n = 5 if kind in APP_CYCLE else 1
        for i in range(n):
            aid = f"bench-{kind}-{i}" if kind in APP_CYCLE else f"bench-{kind}"
            d = os.path.join(apps_dir, aid)
            os.makedirs(d, exist_ok=True)
            html = src.replace("{{BENCH}}", BENCH_URL).replace("{{APPID}}", aid)
            open(os.path.join(d, "app.html"), "w").write(html)
            json.dump({"app_id": aid, "prompt": f"bench app {kind} ({fname})", "created": time.time(),
                       "version": 1, "last_opened": time.time(), "icon": f"/files/icons/{aid}.png"},
                      open(os.path.join(d, "meta.json"), "w"))
            ids.append(aid)
    # a placeholder icon per app: the real kernel otherwise paints one with sd.cpp in the
    # background for every app it meets for the first time (GPU load during the run)
    icons = os.path.join(vibeos_dir, "cache", "files", "icons")
    os.makedirs(icons, exist_ok=True)
    for aid in ids:
        ip = os.path.join(icons, aid + ".png")
        if not os.path.exists(ip):
            Image.new("RGB", (64, 64), (60, 40, 120)).save(ip)
    return ids


def uninstall_apps(vibeos_dir):
    apps_dir = os.path.join(vibeos_dir, "cache", "apps")
    for n in os.listdir(apps_dir) if os.path.isdir(apps_dir) else []:
        if n.startswith("bench-"):
            shutil.rmtree(os.path.join(apps_dir, n), ignore_errors=True)


# ---------------------------------------------------------------- X probing + input
class XProbe:
    def __init__(self):
        self.d = display.Display()
        self.root = self.d.screen().root
        g = self.root.get_geometry()
        self.W, self.H = g.width, g.height
        from Xlib.ext import randr
        try:
            ms = randr.get_monitors(self.root, True).monitors
            self.rects = sorted((m.x, m.y, m.width_in_pixels, m.height_in_pixels) for m in ms)
        except Exception:
            self.rects = []
        if not self.rects:
            self.rects = [(0, 0, self.W, self.H)]
        self.xserver_pid = None
        for p in psutil.process_iter(["name", "cmdline"]):
            cl = " ".join(p.info["cmdline"] or [])
            if p.info["name"] in ("Xorg", "X", "Xwayland", "Xephyr", "Xvfb") and os.environ.get("DISPLAY", ":0").split(".")[0] in cl.split():
                self.xserver_pid = p.pid
        if self.xserver_pid is None:
            for p in psutil.process_iter(["name"]):
                if p.info["name"] in ("Xorg", "X"):
                    self.xserver_pid = p.pid

    def pixels(self, x, y, w, h):
        img = self.root.get_image(x, y, w, h, X.ZPixmap, 0xffffffff)
        return img.data

    def image(self, x=0, y=0, w=None, h=None):
        w = w or self.W
        h = h or self.H
        return Image.frombytes("RGB", (w, h), self.pixels(x, y, w, h), "raw", "BGRX")

    def shot(self, path, scale=0.5):
        img = self.image()
        if scale != 1:
            img = img.resize((int(img.width * scale), int(img.height * scale)))
        img.save(path, quality=60)

    def dock_probe(self, screen=0):
        """the 🚀 emoji of the second dock button (bright, colourful; the ⊞ of the first
        button is a <button> in UA colour = black on the glass): >= 3 bright pixels"""
        sx, sy, sw, sh = self.rects[screen]
        cx = sx + sw // 2 - DOCK_W // 2 + 1 + 12 + 26 + 58
        cy = sy + sh - 12 - 1 - 8 - 26
        data = self.pixels(cx - 20, cy - 12, 40, 24)
        bright = sum(1 for i in range(0, len(data), 4) if max(data[i], data[i + 1], data[i + 2]) > 150)
        return bright >= 3

    def move(self, x, y):
        xtest.fake_input(self.d, X.MotionNotify, x=x, y=y)
        self.d.sync()

    def button(self, press, button=1):
        xtest.fake_input(self.d, X.ButtonPress if press else X.ButtonRelease, button)
        self.d.sync()

    def click(self, x, y, button=1):
        self.move(x, y)
        self.button(True, button)
        self.button(False, button)

    def key(self, keysym_name):
        from Xlib import XK
        ks = XK.string_to_keysym(keysym_name)
        kc = self.d.keysym_to_keycode(ks)
        xtest.fake_input(self.d, X.KeyPress, kc)
        xtest.fake_input(self.d, X.KeyRelease, kc)
        self.d.sync()

    def type_text(self, text):
        for ch in text:
            self.key({" ": "space"}.get(ch, ch))
            time.sleep(0.02)

    def clients(self):
        p = self.root.get_full_property(self.d.intern_atom("_NET_CLIENT_LIST"), X.AnyPropertyType)
        return list(p.value) if p else []

    def title(self, xid):
        try:
            w = self.d.create_resource_object("window", xid)
            p = w.get_full_property(self.d.intern_atom("_NET_WM_NAME"), X.AnyPropertyType)
            return p.value.decode("utf-8", "replace") if p else ""
        except Exception:
            return ""

    def frame_delta(self, xid):
        """(frame - client) size: >0 means the WM drew decorations around the client"""
        try:
            w = self.d.create_resource_object("window", xid)
            parent = w.query_tree().parent
            if parent.id == self.root.id:
                return (0, 0)
            pg, cg = parent.get_geometry(), w.get_geometry()
            return (pg.width - cg.width, pg.height - cg.height)
        except Exception:
            return None

    def override_windows(self):
        """mapped override-redirect windows (menus, popups) on the root, with their class"""
        out = []
        for c in self.root.query_tree().children:
            try:
                a = c.get_attributes()
                if a.override_redirect and a.map_state == X.IsViewable:
                    g = c.get_geometry()
                    if g.width > 4 and g.height > 4:
                        cls = c.get_wm_class()
                        pidp = c.get_full_property(self.d.intern_atom("_NET_WM_PID"), X.AnyPropertyType)
                        pid = int(pidp.value[0]) if pidp and pidp.value else 0
                        out.append((c.id, g.width, g.height, (cls[1] if cls else "") or "", pid))
            except Exception:
                pass
        return out


# ---------------------------------------------------------------- the prototype driver
class Proto:
    def __init__(self, candidate, env):
        prog = {"baseline": "baseline.py", "chromium-wm": "wmproto.py", "qtile": "wmproto.py"}[candidate]
        cmd = [sys.executable, os.path.join(HERE, "proto", prog)]
        if candidate == "chromium-wm":
            cmd += ["--wm", "openbox"]
        elif candidate == "qtile":
            cmd += ["--wm", "qtile"]
        self.p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1, env=env)
        self.log = []

    def cmd(self, line, timeout=90):
        t0 = time.monotonic()
        self.p.stdin.write(line + "\n")
        self.p.stdin.flush()
        while True:
            reply = self.p.stdout.readline()
            if not reply:
                raise RuntimeError(f"driver died on {line!r}")
            reply = reply.rstrip("\n")
            self.log.append((line, reply, time.monotonic() - t0))
            if reply.startswith("INFO"):
                continue
            return reply

    def ok(self, line):
        r = self.cmd(line)
        return r.startswith("OK"), r

    def geom(self, ref):
        r = self.cmd(f"geom {ref}")
        if r.startswith("GEOM ") and "none" not in r:
            v = list(map(int, r.split()[1:]))
            return {"x": v[0], "y": v[1], "w": v[2], "h": v[3], "mapped": bool(v[4]), "screen": v[5]}
        return None

    def windows(self):
        r = self.cmd("windows")
        return json.loads(r[8:]) if r.startswith("WINDOWS") else []

    def pyprof(self, reset=False):
        r = self.cmd("pyprof reset" if reset else "pyprof")
        return json.loads(r[7:]) if r.startswith("PYPROF") else {"error": r}

    def pids(self):
        r = self.cmd("pids")
        return [int(x) for x in r.split()[1:]] if r.startswith("PIDS") else []


# ---------------------------------------------------------------- memory / process topology
def pss_kb(pid):
    try:
        with open(f"/proc/{pid}/smaps_rollup") as f:
            for line in f:
                if line.startswith("Pss:"):
                    return int(line.split()[1])
    except PermissionError:
        try:    # host: openbox lives in the trixie container (root) — ask from inside
            name = psutil.Process(pid).name()
            out = subprocess.run(["docker", "exec", "kairos-bench-host", "sh", "-c",
                                  f"cat /proc/$(pidof {name} | cut -d' ' -f1)/smaps_rollup"],
                                 capture_output=True, text=True, timeout=5).stdout
            for line in out.splitlines():
                if line.startswith("Pss:"):
                    return int(line.split()[1])
        except Exception:
            return None
    except Exception:
        return None
    return None


def stack_procs(root_pids, xserver_pid):
    procs = {}
    for pid in root_pids + ([xserver_pid] if xserver_pid else []):
        try:
            p = psutil.Process(pid)
            procs[p.pid] = p
            for c in p.children(recursive=True):
                procs[c.pid] = c
        except psutil.Error:
            pass
    return list(procs.values())


def classify(p):
    try:
        cl = p.cmdline()
    except psutil.Error:
        return "gone"
    s = " ".join(cl)
    if "chrom" in (cl[0] if cl else "").lower() or "chrome" in s.split(" ")[0]:
        m = re.search(r"--type=(\S+)", s)
        return "chromium:" + (m.group(1) if m else "browser")
    n = p.name()
    if n in ("Xorg", "X", "Xwayland", "Xephyr"):
        return "xserver"
    if "wmproto" in s or "baseline.py" in s:
        return "driver"
    if "qtile" in s:
        return "qtile"
    if "openbox" in n:
        return "openbox"
    if "pygame" in s:
        return "pygame"
    return n


def mem_sample(root_pids, xserver_pid):
    procs = stack_procs(root_pids, xserver_pid)
    rss = pss = 0
    per = {}
    for p in procs:
        try:
            r = p.memory_info().rss // 1024
        except psutil.Error:
            continue
        ps = pss_kb(p.pid)
        k = classify(p)
        per[k] = per.get(k, 0) + (ps if ps is not None else r)
        rss += r
        pss += ps if ps is not None else r
    return {"pss_mib": pss / 1024, "rss_mib": rss / 1024, "procs": len(procs), "per_kind_mib": {k: v / 1024 for k, v in per.items()}}


def mem_median(root_pids, xserver_pid, samples=3, gap=2.0):
    out = []
    for i in range(samples):
        out.append(mem_sample(root_pids, xserver_pid))
        if i < samples - 1:
            time.sleep(gap)
    out.sort(key=lambda s: s["pss_mib"])
    return dict(out[len(out) // 2], samples=[s["pss_mib"] for s in out])


def topology(root_pids, xserver_pid):
    counts = {}
    for p in stack_procs(root_pids, xserver_pid):
        k = classify(p)
        counts[k] = counts.get(k, 0) + 1
    return counts


def nvidia_smi():
    try:
        used = subprocess.run(["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader"],
                              capture_output=True, text=True, timeout=10).stdout.strip()
        apps = subprocess.run(["nvidia-smi", "--query-compute-apps=pid,used_memory", "--format=csv,noheader"],
                              capture_output=True, text=True, timeout=10).stdout.strip()
        return {"memory": used, "compute_apps": apps}
    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------- one run
class Run:
    def __init__(self, a, bench, xp, run_no, outdir):
        self.a, self.bench, self.xp, self.run_no, self.outdir = a, bench, xp, run_no, outdir
        self.real = a.candidate != "baseline"
        self.R = {"candidate": a.candidate, "env": a.env, "run": run_no, "started": time.strftime("%Y-%m-%d %H:%M:%S"),
                  "M1": {}, "M2": {"opens": []}, "M2b": [], "M3": {}, "M3b": {}, "M4": {}, "M5": {}, "M6": {}, "M7": {},
                  "M8": {}, "M9": {}, "F": {}, "notes": [], "screenshots": []}
        self.open_geoms = {}
        self.scene_geoms = {}        # where the scenario put each window (restored after M6/M7)
        self.n_open = 0

    def note(self, s):
        log(s)
        self.R["notes"].append(s)

    def shot(self, tag):
        p = os.path.join(self.outdir, f"shot-{self.a.candidate}-{self.a.env}-r{self.run_no}-{tag}.jpg")
        try:
            self.xp.shot(p)
            self.R["screenshots"].append(os.path.basename(p))
        except Exception as e:
            self.note(f"screenshot {tag} failed: {e}")

    # ---- window geometry helpers (root coordinates of an app's window)
    def cascade(self, i):
        sx, sy, sw, sh = self.xp.rects[0]
        return (sx + 90 + (i % 7) * 36, sy + 70 + (i % 5) * 32, 720, 520)

    def open_app(self, aid, geom=None, label="warm"):
        i = self.n_open
        self.n_open += 1
        x, y, w, h = geom or self.cascade(i)
        t_req = time.time()
        r = self.proto.cmd(f"open {aid} {x} {y} {w} {h}")
        t_win = time.time()
        if not r.startswith("WIN"):
            self.note(f"open {aid} failed: {r}")
            return None
        t_map = int(r.split()[2]) / 1000
        flash = self.flash_probe(x, y, w, h)                             # M2b: white before first paint?
        m = self.bench.wait_mark(aid, "paint", t_req, timeout=30)
        rec = {"app": aid, "label": label, "t_req": t_req, "t_win": t_win, "t_map": t_map,
               "t_paint": m["t_recv"] if m else None,
               "open_ms": (m["t_recv"] - t_req) * 1000 if m else None,
               "win_ms": (t_win - t_req) * 1000, "geom": [x, y, w, h]}
        rec["flash"] = flash
        self.R.setdefault("M2b_white_ms", []).append(flash["white_ms"])
        self.R.setdefault("M2b_white_bar_ms", []).append(flash["white_ms_bar"])
        if self.real and m:
            rec["map_to_paint_ms"] = (m["t_recv"] - t_map) * 1000
            self.R["M2b"].append(rec["map_to_paint_ms"])
        self.R["M2"]["opens"].append(rec)
        self.open_geoms[aid] = (x, y, w, h)
        self.scene_geoms[aid] = (x, y, w, h)
        log(f"open {aid}: {rec['open_ms'] and round(rec['open_ms'], 1)} ms (win {rec['win_ms']:.1f})")
        return rec

    def flash_probe(self, x, y, w, h, ms=300):
        """right after the window is placed / the DOM window mounted: sample the titlebar
        (the host/desktop page's own paint) and the app body (the sandboxed iframe) for
        `ms`; a region sample counts as white when > 1/4 of its 8x8 pixels are ~white"""
        t0 = time.monotonic()
        n = 0
        span = {"bar": [None, None], "body": [None, None]}
        cnt = {"bar": 0, "body": 0}
        while (time.monotonic() - t0) * 1000 < ms:
            regions = {"bar": self.xp.pixels(x + 8, y + 6, 8, 8),
                       "body": self.xp.pixels(x + w // 2 - 4, y + h // 2 - 4, 8, 8) + self.xp.pixels(x + 8, y + BAR_H + 8, 8, 8)}
            n += 1
            now = time.monotonic()
            for k, px in regions.items():
                b = sum(1 for i in range(0, len(px), 4) if px[i] > 235 and px[i + 1] > 235 and px[i + 2] > 235)
                if b > len(px) // 16:
                    cnt[k] += 1
                    span[k][0] = span[k][0] or now
                    span[k][1] = now
            time.sleep(0.002)
        ms_of = lambda k: ((span[k][1] - span[k][0]) * 1000 + 2) if span[k][0] else 0
        return {"samples": n, "white_samples": cnt["body"], "white_ms": ms_of("body"), "white_ms_bar": ms_of("bar"),
                "white_samples_bar": cnt["bar"]}

    def restore_scene(self, apps):
        for aid in apps:
            x, y, w, h = self.scene_geoms[aid]
            self.proto.ok(f"move {aid} {x} {y}")
            self.proto.ok(f"resize {aid} {w} {h}")
            g = self.proto.geom(aid)
            if g:
                self.open_geoms[aid] = (g["x"], g["y"], g["w"], g["h"])

    def body_origin(self, aid):
        g = self.proto.geom(aid)
        return (g["x"] + BORDER, g["y"] + BAR_H + BORDER), g

    def region_changes(self, x, y, w, h, seconds=1.0, hz=100):
        """count distinct frames in a region over `seconds` (does it still animate?)"""
        last = None
        changes = 0
        t0 = time.monotonic()
        while time.monotonic() - t0 < seconds:
            px = self.xp.pixels(x, y, w, h)
            if last is not None and px != last:
                changes += 1
            last = px
            time.sleep(1 / hz)
        return changes

    # ---- metrics
    def m1(self):
        cold = False
        if self.a.drop_caches:      # root only (VM): the plan's cold variant of M1
            rc = subprocess.run("sync; echo 3 > /proc/sys/vm/drop_caches", shell=True).returncode
            cold = rc == 0
            time.sleep(1)
        t0 = time.time()
        r = self.proto.cmd("start", timeout=120)
        t_ready = time.time()
        if not r.startswith("READY"):
            raise RuntimeError("start failed: " + r)
        painted = None
        while time.time() - t0 < 90:
            if self.xp.dock_probe(0):
                painted = time.time()
                break
            time.sleep(0.02)
        self.R["M1"] = {"ready_ms": (t_ready - t0) * 1000, "dock_painted_ms": (painted - t0) * 1000 if painted else None, "cold": cold,
                        "probe": "40x24 patch on the second dock button (🚀 emoji): >=3 pixels with max(R,G,B)>150"}
        log(f"M1: READY {self.R['M1']['ready_ms']:.0f} ms, dock painted {self.R['M1']['dock_painted_ms']} ms")
        self.shot("ready")

    def m3(self, label):
        pids = self.proto.pids()
        s = mem_median(pids, self.xp.xserver_pid, samples=self.a.mem_samples, gap=self.a.mem_gap)
        self.R["M3"][label] = s
        log(f"M3 @{label}: Pss {s['pss_mib']:.0f} MiB (rss {s['rss_mib']:.0f}), {s['procs']} procs")

    def m6(self, apps):
        lat = {"move": [], "resize": [], "raise": []}
        fails = 0
        self.proto.pyprof(reset=True)
        t_start = time.monotonic()
        for i in range(self.a.ops):
            aid = apps[i % len(apps)]
            x, y, w, h = self.open_geoms[aid]
            dx = 400 if (i // len(apps)) % 2 == 0 else -400
            dw = 120 if (i // len(apps)) % 2 == 0 else -120
            for op, line in (("move", f"move {aid} {x + dx} {y}"), ("resize", f"resize {aid} {w + dw} {h + dw // 2}"),
                             ("raise", f"raise {aid}")):
                t0 = time.monotonic()
                ok, r = self.proto.ok(line)
                lat[op].append((time.monotonic() - t0) * 1000)
                if not ok:
                    fails += 1
            self.open_geoms[aid] = (x + dx, y, w + dw, h + dw // 2)
        total = time.monotonic() - t_start
        prof = self.proto.pyprof()
        nops = self.a.ops * 3
        self.R["M6"] = {"ops": nops, "seconds": total, "ops_per_s": nops / total, "fails": fails,
                        "per_op": {k: summary(v) for k, v in lat.items()}, "all": summary(sum(lat.values(), []))}
        m9a = {}
        for proc in ("driver", "qtile"):
            if proc in prof and isinstance(prof[proc], dict) and "utime_ms" in prof[proc]:
                m9a[proc] = {"cpu_ms_per_op": (prof[proc]["utime_ms"] + prof[proc]["stime_ms"]) / nops,
                             "utime_ms": prof[proc]["utime_ms"], "stime_ms": prof[proc]["stime_ms"], "pss_kb": prof[proc].get("pss_kb")}
        if "js" in prof:
            m9a["shell_js"] = {"task_ms_per_op": prof["js"]["TaskDuration"] * 1000 / nops, "metrics_delta": prof["js"],
                               "note": "CDP Performance.getMetrics TaskDuration — NOT like-for-like with Python CPU"}
        self.R["M9"]["M9a"] = m9a
        log(f"M6: {nops} ops in {total:.2f} s = {nops / total:.0f} ops/s, p50 {self.R['M6']['all']['p50']:.2f} ms, fails {fails}; M9a {m9a}")

    def drag(self, aid, steps=60, px=600, hz=120):
        """titlebar drag of one window; probe = 1-px row along the path at >=400 Hz"""
        self.proto.ok(f"raise {aid}")          # later cascaded windows overlap its titlebar
        time.sleep(0.15)
        g = self.proto.geom(aid)
        x, y, w, h = g["x"], g["y"], g["w"], g["h"]
        sx, sy = x + min(200, w // 3), y + BAR_H // 2
        row_y = y + h // 2
        rx0, rw = x + w - 16, px + 32
        rx0 = max(0, rx0)
        rw = min(rw, self.xp.W - rx0)
        frames = []
        stop = threading.Event()

        def probe():
            # own X connection: a python-xlib Display must not be shared between threads
            pd = display.Display()
            proot = pd.screen().root
            last = None
            n = 0
            while not stop.is_set():
                pxs = proot.get_image(rx0, row_y, rw, 1, X.ZPixmap, 0xffffffff).data
                n += 1
                if pxs != last:
                    frames.append(time.monotonic())
                    last = pxs
            frames.append(("samples", n))
            pd.close()
        th = threading.Thread(target=probe, daemon=True)
        self.xp.move(sx, sy)
        time.sleep(0.05)
        th.start()
        t0 = time.monotonic()
        self.xp.button(True)
        time.sleep(0.03)
        step = px / steps
        for i in range(1, steps + 1):
            target = t0 + 0.03 + i / hz
            while time.monotonic() < target:
                pass
            self.xp.move(int(sx + i * step), sy)
        self.xp.button(False)
        time.sleep(0.15)
        stop.set()
        th.join()
        n_samples = frames.pop()[1]
        ts = [f for f in frames if isinstance(f, float)]
        ivs = [(b - a) * 1000 for a, b in zip(ts, ts[1:])]
        g2 = self.proto.geom(aid)
        moved = g2["x"] - x if g2 else None
        self.open_geoms[aid] = (g2["x"], g2["y"], g2["w"], g2["h"]) if g2 else self.open_geoms[aid]
        return {"frames": len(ts), "interval_ms": summary(ivs), "stalls_gt32ms": sum(1 for v in ivs if v > 32),
                "moved_px": moved, "probe_hz": n_samples / max(1e-6, (ts[-1] - ts[0]) if len(ts) > 1 else 0.6),
                "samples": n_samples, "duration_s": steps / hz}

    def m7(self, aid, label):
        self.proto.pyprof(reset=True)
        drags = []
        for i in range(self.a.drags):
            d = self.drag(aid)
            drags.append(d)
            self.restore_scene([aid])          # every drag starts from the scenario geometry
            time.sleep(0.2)
        prof = self.proto.pyprof()
        self.R["M7"][label] = {"drags": drags, "frames_median": statistics.median(d["frames"] for d in drags),
                               "stalls_median": statistics.median(d["stalls_gt32ms"] for d in drags),
                               "interval_p95_median": statistics.median((d["interval_ms"] or {}).get("p95") or 0 for d in drags),
                               "moved_px": [d["moved_px"] for d in drags]}
        jit = {}
        for proc in ("driver", "qtile"):
            if proc in prof and isinstance(prof[proc], dict) and prof[proc].get("jitter_us"):
                jit[proc] = prof[proc]["jitter_us"]
        self.R["M9"].setdefault("M9b", {})[label] = jit
        log(f"M7 @{label}: frames {self.R['M7'][label]['frames_median']}, stalls {self.R['M7'][label]['stalls_median']}, moved {self.R['M7'][label]['moved_px']}; jitter {jit}")

    def m8(self, aid):
        (bx, by), g = self.body_origin(aid)
        cx, cy = bx + 40 + 32, by + 60 + 32
        lat = []
        misses = 0
        self.proto.ok(f"raise {aid}")
        time.sleep(0.3)
        for i in range(self.a.clicks):
            before = self.xp.pixels(cx - 4, cy - 4, 8, 8)
            self.xp.move(cx, cy)
            time.sleep(0.01)
            t0 = time.monotonic()
            self.xp.button(True)
            self.xp.button(False)
            t_photon = None
            n = 0
            while time.monotonic() - t0 < 1.0:
                n += 1
                if self.xp.pixels(cx - 4, cy - 4, 8, 8) != before:
                    t_photon = time.monotonic()
                    break
            if t_photon is None:
                misses += 1
            else:
                lat.append((t_photon - t0) * 1000)
            time.sleep(0.1)
        self.R["M8"] = {"input_to_photon_ms": summary(lat), "misses": misses, "clicks": self.a.clicks,
                        "probe": "8x8 centre of the app's 64x64 #probe patch, polled as fast as XGetImage returns"}
        log(f"M8: p50 {self.R['M8']['input_to_photon_ms'] and round(self.R['M8']['input_to_photon_ms']['p50'], 1)} ms, p95 {self.R['M8']['input_to_photon_ms'] and round(self.R['M8']['input_to_photon_ms']['p95'], 1)}, misses {misses}")

    def m5(self, a1_apps):
        pids = self.proto.pids()
        res = {}
        for cond in ("a1_visible", "a1_minimized"):
            if cond == "a1_minimized":
                for aid in a1_apps:
                    self.proto.ok(f"minimize {aid}")
            else:
                for aid in a1_apps:
                    self.proto.ok(f"raise {aid}")
            time.sleep(2)
            procs = stack_procs(pids, self.xp.xserver_pid)
            c0 = {}
            for p in procs:
                try:
                    c0[p.pid] = sum(p.cpu_times()[:2])
                except psutil.Error:
                    pass
            psutil.cpu_percent(interval=None)
            t0 = time.monotonic()
            sys_pct = psutil.cpu_percent(interval=self.a.m5_seconds)
            dt = time.monotonic() - t0
            used = 0.0
            for p in procs:
                try:
                    used += sum(p.cpu_times()[:2]) - c0.get(p.pid, 0)
                except psutil.Error:
                    pass
            res[cond] = {"seconds": dt, "system_busy_pct": sys_pct, "system_idle_pct": 100 - sys_pct,
                         "stack_cpu_pct_one_core": used / dt * 100, "stack_cpu_pct_all_cores": used / dt * 100 / psutil.cpu_count()}
            log(f"M5 {cond}: system idle {100 - sys_pct:.1f}%, stack {used / dt * 100:.1f}% of one core")
        for aid in a1_apps:
            self.proto.ok(f"restore {aid}")
        self.R["M5"] = res

    # ---- functional checks
    def f_minimize_restore(self, apps):
        ok_all = True
        for aid in apps:
            for _ in range(2):
                ok1, _r = self.proto.ok(f"minimize {aid}")
                g = self.proto.geom(aid)
                ok2, _r2 = self.proto.ok(f"restore {aid}")
                g2 = self.proto.geom(aid)
                ok_all &= ok1 and ok2 and (g is not None and not g["mapped"]) and (g2 is not None and g2["mapped"])
        return ok_all

    def f3(self, aid):
        """minimize -> a chip; clicking the chip restores + focuses"""
        self.proto.ok(f"minimize {aid}")
        time.sleep(0.6)
        wins = self.proto.windows()
        ev = {}
        if self.real:
            sx, sy, sw, sh = self.xp.rects[0]
            # dock strip: taskbar at left 12px, bottom 12px, chips 40px tall; the chip of aid is
            # the first (only) minimized window right now -> first chip
            chip_x, chip_y = sx + 12 + 60, sy + sh - 12 - 20
            ev["chip_region_before"] = None
            self.xp.click(chip_x, chip_y)
            time.sleep(0.8)
            g = self.proto.geom(aid)
            w2 = [w for w in self.proto.windows() if w.get("app") == aid]
            ev["restored"] = bool(g and g["mapped"])
            ev["active"] = bool(w2 and w2[0].get("active"))
            ok = ev["restored"] and ev["active"]
        else:
            r = self.proto.cmd("eval (() => { const c = document.querySelector('#taskbar .task-chip'); if (!c) return 'nochip'; c.click(); return 'clicked'; })()")
            time.sleep(0.3)
            g = self.proto.geom(aid)
            w2 = [w for w in self.proto.windows() if w.get("app") == aid]
            ev["chip"] = r
            ev["restored"] = bool(g and g["mapped"])
            ev["active"] = bool(w2 and w2[0].get("active"))
            ok = "clicked" in r and ev["restored"] and ev["active"]
        ev["minimized_listed"] = any(w.get("app") == aid and w.get("minimized") for w in wins)
        self.R["F"]["F3"] = {"pass": ok and ev["minimized_listed"], "evidence": ev}
        if not (g and g["mapped"]):
            self.proto.ok(f"restore {aid}")

    def f4(self, aid):
        """the four window buttons by synthetic clicks: ✨ (sticky note), _ , ▢, ✕"""
        ev = {}
        self.proto.ok(f"raise {aid}")
        time.sleep(0.3)
        g = self.proto.geom(aid)
        right = g["x"] + g["w"] - BORDER - 6
        by = g["y"] + BORDER + BAR_H - 8          # low in the bar: the shell's search box overlaps the top rows
        # ✨ improve: 4th button from the right
        self.xp.click(right - 30 * 3 - 15, by)
        time.sleep(0.6)
        before = self.xp.pixels(g["x"] + g["w"] - 200, g["y"] + 60, 100, 100)
        yellow = sum(1 for i in range(0, len(before), 4) if before[i + 2] > 200 and before[i + 1] > 200 and before[i] < 190)
        ev["sticky_note_yellow_px"] = yellow
        self.xp.key("Escape")
        time.sleep(0.3)
        # _ minimize: 3rd from the right
        self.xp.click(right - 30 * 2 - 15, by)
        time.sleep(0.5)
        g2 = self.proto.geom(aid)
        ev["minimized_by_button"] = bool(g2 and not g2["mapped"])
        self.proto.ok(f"restore {aid}")
        time.sleep(0.4)
        g = self.proto.geom(aid)
        right = g["x"] + g["w"] - BORDER - 6
        by = g["y"] + BORDER + BAR_H - 8
        # ▢ maximize: 2nd from the right
        self.xp.click(right - 30 - 15, by)
        time.sleep(0.6)
        g3 = self.proto.geom(aid)
        ev["maximized_geom"] = g3
        ev["maximized_by_button"] = bool(g3 and g3["w"] >= self.xp.rects[0][2] - 4)
        # restore geometry (toggle back)
        if g3:
            self.xp.click(g3["x"] + g3["w"] - BORDER - 6 - 30 - 15, g3["y"] + BORDER + BAR_H - 8)
            time.sleep(0.6)
        g4 = self.proto.geom(aid)
        ev["restored_geom"] = g4
        ev["restored_by_button"] = bool(g4 and g and (g4["w"], g4["h"]) == (g["w"], g["h"]))
        if g4 and not ev["restored_by_button"]:          # leave the window as the scenario expects
            self.proto.ok(f"maximize {aid}")
            self.proto.ok(f"move {aid} {g['x']} {g['y']}")
            self.proto.ok(f"resize {aid} {g['w']} {g['h']}")
            g4 = self.proto.geom(aid)
        if g4:
            self.open_geoms[aid] = (g4["x"], g4["y"], g4["w"], g4["h"])
        ok = yellow > 20 and ev["minimized_by_button"] and ev["maximized_by_button"] and ev["restored_by_button"]
        self.R["F"]["F4"] = {"pass": ok, "evidence": ev, "note": "close (✕) verified separately via the close op at the end of the run"}

    def f1(self, aid):
        g0 = self.proto.geom(aid)
        r = self.proto.cmd(f"toscreen {aid} 1")
        ev = {"reply": r[:80], "before": g0}
        if r.startswith("UNSUPPORTED") or len(self.xp.rects) < 2:
            self.R["F"]["F1"] = {"pass": False, "evidence": ev}
            return
        time.sleep(0.5)
        g = self.proto.geom(aid)
        ev["after"] = g
        sx, sy, sw, sh = self.xp.rects[1]
        fully = g and g["x"] >= sx and g["x"] + g["w"] <= sx + sw and g["screen"] == 1
        ch = self.region_changes(g["x"] + 200, g["y"] + BAR_H + 200, 32, 32, seconds=0.8) if g else 0
        ev["fully_on_screen_1"] = bool(fully)
        ev["frames_changed_0.8s"] = ch
        self.shot("f1-screen1")
        self.R["F"]["F1"] = {"pass": bool(fully and ch > 5), "evidence": ev}
        self.proto.cmd(f"toscreen {aid} 0")
        g = self.proto.geom(aid)
        if g:
            self.open_geoms[aid] = (g["x"], g["y"], g["w"], g["h"])

    def f2_f5(self):
        env = dict(os.environ)
        env["SDL_AUDIODRIVER"] = "dummy"
        p = subprocess.Popen([sys.executable, os.path.join(APPS, "pygame_stub.py")], env=env,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        ev = {"pid": p.pid}
        found = None
        t0 = time.monotonic()
        while time.monotonic() - t0 < 15:
            for xid in self.xp.clients():
                if self.xp.title(xid).startswith("kairos-game"):
                    found = xid
                    break
            if found:
                break
            time.sleep(0.1)
        ev["x_window"] = hex(found) if found else None
        ev["frame_delta"] = self.xp.frame_delta(found) if found else None
        time.sleep(1.0)
        wins = self.proto.windows()
        listed = [w for w in wins if str(w.get("title", "")).startswith("kairos-game")]
        ev["in_shell_window_list"] = bool(listed)
        self.shot("pygame")
        ok2 = ok5 = False
        if listed and self.real:
            ref = str(listed[0]["xid"])
            ok_raise, _ = self.proto.ok(f"raise {ref}")
            ok_min, _ = self.proto.ok(f"minimize {ref}")
            time.sleep(0.5)
            chips = [w for w in self.proto.windows() if str(w.get("title", "")).startswith("kairos-game") and w.get("minimized")]
            ev["minimized_listed"] = bool(chips)
            self.shot("pygame-minimized")
            ok_rest, _ = self.proto.ok(f"restore {ref}")
            ok_close, _ = self.proto.ok(f"close {ref}")
            ev.update(raise_ok=ok_raise, minimize_ok=ok_min, restore_ok=ok_rest, close_ok=ok_close)
            ok2 = ok_raise and ok_close
            ok5 = ok_min and bool(chips)
        self.R["F"]["F2"] = {"pass": bool(found) and ok2, "evidence": ev, "visible": bool(found)}
        self.R["F"]["F5"] = {"pass": ok5, "evidence": {"minimized_listed": ev.get("minimized_listed")}}
        try:
            p.terminate()
            p.wait(timeout=3)
        except Exception:
            p.kill()

    def f6(self):
        ev = {}
        if not self.real:
            r = self.proto.cmd("eval (async () => { const i = document.querySelector('#ds-input'); i.value = 'vibe'; i.dispatchEvent(new Event('input')); await new Promise(r => setTimeout(r, 900)); const it = document.querySelector('#ds-results .ds-item'); if (!it) return 'noresult'; const n0 = windows.size; runAction(it._action); await new Promise(r => setTimeout(r, 800)); return 'opened:' + (windows.size - n0) + ' ' + JSON.stringify(it._action); })()")
            ev["result"] = r
            self.R["F"]["F6"] = {"pass": "opened:1" in r, "evidence": ev,
                                 "note": "DOM overlay inside the desktop page; z-index 55 over DOM windows"}
            self.proto.ok("close bench-vibe")          # F8 opens it again and waits for a fresh beacon
            return
        n0 = len(self.proto.windows())
        cmd = os.environ.get("BENCH_CHROME_WRAP", "").split() + [os.environ.get("BENCH_CHROME", "chromium")] + os.environ.get("BENCH_CHROME_EXTRA", "").split() + \
              ["--user-data-dir=" + os.environ.get("BENCH_PROFILE", "/tmp/bench-kiosk"), f"--app={KERNEL}/shell/bench/search.html?screen=0"]
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env={k: v for k, v in os.environ.items() if k != "WAYLAND_DISPLAY"})
        sw = None
        t0 = time.monotonic()
        while time.monotonic() - t0 < 15:
            sw = [w for w in self.proto.windows() if str(w.get("title", "")).startswith("kairos-search")]
            if sw:
                break
            time.sleep(0.1)
        ev["search_window"] = bool(sw)
        if sw:
            self.proto.ok(f"raise {sw[0]['xid']}")
            time.sleep(0.5)
            self.xp.type_text("vibe")
            time.sleep(1.0)
            self.shot("f6-search")
            self.xp.key("Return")
            time.sleep(1.5)
            n1 = len(self.proto.windows())
            ev["windows_before_after"] = [n0, n1]
            ev["opened_app"] = [w["app"] for w in self.proto.windows() if w.get("app") == "bench-vibe"]
            ev["over_app_windows"] = bool(sw[0].get("active"))
            self.proto.ok(f"close {sw[0]['xid']}")
            self.proto.ok("close bench-vibe")
        self.R["F"]["F6"] = {"pass": bool(sw) and ev.get("windows_before_after", [0, 0])[1] > n0 + 1, "evidence": ev,
                             "note": "search moved into its own window (search.html) so it opens over real windows"}

    def f7(self):
        ev = {}
        if not self.real:
            r = self.proto.cmd("eval (async () => { const w = await openPage('bench-town-news'); await new Promise(r => setTimeout(r, 2500)); return [w.iframe.src, w.isPage]; })()")
            ev["result"] = r
            self.R["F"]["F7"] = {"pass": "/web/bench-town-news" in r, "evidence": ev}
            return
        cmd = os.environ.get("BENCH_CHROME_WRAP", "").split() + [os.environ.get("BENCH_CHROME", "chromium")] + os.environ.get("BENCH_CHROME_EXTRA", "").split() + \
              ["--user-data-dir=" + os.environ.get("BENCH_PROFILE", "/tmp/bench-kiosk"), f"--app={KERNEL}/shell/bench/page.html?address=bench-town-news"]
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env={k: v for k, v in os.environ.items() if k != "WAYLAND_DISPLAY"})
        pw = None
        t0 = time.monotonic()
        while time.monotonic() - t0 < 15:
            pw = [w for w in self.proto.windows() if str(w.get("title", "")).startswith("kairos-page:")]
            if pw:
                break
            time.sleep(0.1)
        time.sleep(3)
        ev["page_window"] = pw[0]["title"] if pw else None
        if pw:
            g = self.proto.geom(str(pw[0]["xid"]))
            ch = b""
            if g:   # the imagined page has text somewhere: top-left body corner or the centre
                ch = self.xp.pixels(g["x"] + 20, g["y"] + BAR_H + 20, 200, 120) + \
                     self.xp.pixels(g["x"] + g["w"] // 2 - 100, g["y"] + g["h"] // 2 - 60, 200, 120)
            ev["content_nonuniform"] = len(set(ch[i:i + 4] for i in range(0, len(ch), 4))) > 3
            self.shot("f7-page")
            self.proto.ok(f"close {pw[0]['xid']}")
        self.R["F"]["F7"] = {"pass": bool(pw) and ev.get("content_nonuniform", False), "evidence": ev,
                             "note": "page window keeps the /web/ document in a sandboxed iframe (allow-scripts allow-forms)"}

    def f8_f9_f11(self, a1_ref):
        for key, aid, ev_name, timeout in (("F8", "bench-vibe", "vibe", 120), ("F9", "bench-f9", "f9", 30)):
            t_req = time.time()
            self.open_app(aid, label=key)
            m = self.bench.wait_mark(aid, ev_name, t_req, timeout=timeout)
            if key == "F8":
                res = json.loads(m["res"]) if m and m.get("res") else {}
                self.R["F"]["F8"] = {"pass": bool(res) and all(v == "ok" for v in res.values()), "evidence": res}
            else:
                self.R["F"]["F9"] = {"pass": bool(m and m.get("blocked") == "1"), "evidence": m and {"blocked": m.get("blocked"), "detail": m.get("detail")}}
            log(f"{key}: {self.R['F'][key]}")
            self.proto.ok(f"close {aid}")
        # F11: a runaway app must not freeze the others
        (bx, by), g = self.body_origin(a1_ref)
        self.proto.ok(f"raise {a1_ref}")
        t_req = time.time()
        self.open_app("bench-hang", geom=(g["x"] + 300, g["y"] + 200, 400, 300), label="F11")
        m = self.bench.wait_mark("bench-hang", "hang", t_req, timeout=20)
        time.sleep(0.5)
        ch = self.region_changes(bx + 200, by + 200, 32, 32, seconds=2.0)
        ok_probe = self.region_changes(bx + 200, by + 200, 32, 32, seconds=0.5)
        self.R["F"]["F11"] = {"pass": ch > 10, "evidence": {"hang_beacon": bool(m), "a1_frames_changed_2s": ch, "again_0.5s": ok_probe}}
        log(f"F11: a1 frames changed in 2 s while another app spins: {ch}")
        self.shot("f11")
        ok, r = self.proto.ok("close bench-hang")
        self.R["F"]["F11"]["evidence"]["close_hung_app"] = ok
        if not ok:
            self.note("hung app could not be closed via the shell: " + r)

    def f10(self):
        """no Linux chrome: frame == client for every managed window, no menu on desktop right-click"""
        ev = {"decorated_windows": [], "override_windows_after_rightclick": None}
        for xid in self.xp.clients():
            d = self.xp.frame_delta(xid)
            if d and d != (0, 0):
                ev["decorated_windows"].append({"title": self.xp.title(xid), "frame_minus_client": d})
        sx, sy, sw, sh = self.xp.rects[0]
        gap = (sx + sw - 40, sy + 42)               # top-right of screen 0: below the menubar, no window ever there
        game = (self.R["F"].get("F2") or {}).get("evidence", {}).get("frame_delta")
        if game and tuple(game) != (0, 0):
            ev["decorated_windows"].append({"title": "kairos-game: stub (pygame)", "frame_minus_client": game})
        before = self.xp.override_windows()
        self.xp.click(gap[0], gap[1], button=3)
        time.sleep(0.6)
        after = self.xp.override_windows()
        self.shot("f10-rightclick")
        self.xp.key("Escape")
        time.sleep(0.2)
        new = [w for w in after if w not in before]
        ev["override_windows_after_rightclick"] = new
        # WM chrome (openbox/qtile menus, decorations) is the gate; a Chromium context menu
        # is browser chrome the baseline shows too — reported separately (product gap).
        # Attribution by _NET_WM_PID (Chromium menus carry no WM_CLASS) against the stack tree.
        chrome_pids = {p.pid for p in stack_procs(self.proto.pids(), None) if classify(p).startswith("chromium")}
        ev["wm_menus"] = [w for w in new if w[4] not in chrome_pids and "chrom" not in w[3].lower()]
        ev["browser_context_menu"] = [w for w in new if w[4] in chrome_pids or "chrom" in w[3].lower()]
        ev["screenshots"] = len(self.R["screenshots"])
        self.R["F"]["F10"] = {"pass": not ev["decorated_windows"] and not ev["wm_menus"], "evidence": ev,
                              "browser_context_menu": bool(ev["browser_context_menu"]),
                              "note": "structural check + saved frames; Chromium's own context menu is reported, not gated"}

    def f12(self):
        if self.a.env != "vm":
            self.R["F"]["F12"] = {"pass": None, "evidence": "host: NVIDIA is vfio-bound — not measurable"}
            return
        ev = nvidia_smi()
        try:
            ev["glxinfo"] = subprocess.run(["glxinfo", "-B"], capture_output=True, text=True, timeout=10).stdout[:600]
        except Exception as e:
            ev["glxinfo"] = f"error: {e}"
        renderer = "NVIDIA" in ev.get("glxinfo", "")
        self.R["F"]["F12"] = {"pass": renderer and "MiB" in ev.get("memory", ""), "evidence": ev}

    # ---- the scenario
    def run(self):
        env = dict(os.environ)
        self.proto = Proto(self.a.candidate, env)
        try:
            self.m1()
            self.proto.cmd("screens")
            time.sleep(2)
            self.m3("0")
            # 2. cold open
            self.open_app("bench-a1-0", label="cold")
            self.m3("1")
            self.proto.ok("close bench-a1-0")
            time.sleep(0.5)
            # 3. A1 A2 A3
            apps = ["bench-a1-1", "bench-a2-1", "bench-a3-1"]
            for aid in apps:
                self.open_app(aid)
            time.sleep(1)
            self.m3("3")
            self.shot("3apps")
            # 4. M6 + M9a
            self.m6(apps)
            self.restore_scene(apps)
            # 5. M7 + M9b (3 apps open)
            self.m7("bench-a1-1", "3apps")
            # 6. M8
            self.m8("bench-a2-1")
            # 7. minimize/restore twice, F3, F4
            self.R["F"]["F3a_minimize_restore_x2"] = {"pass": self.f_minimize_restore(apps)}
            self.f3("bench-a1-1")
            self.f4("bench-a1-1")
            # 8. ramp to 10
            order = ["bench-a1-2", "bench-a2-2", "bench-a3-2", "bench-a1-3", "bench-a2-3", "bench-a3-3", "bench-a1-4"]
            for aid in order:
                self.open_app(aid)
                n = 3 + order.index(aid) + 1
                if n == 5:
                    self.m3("5")
            time.sleep(5)
            self.m3("10")
            self.R["M3b"] = topology(self.proto.pids(), self.xp.xserver_pid)
            self.R["M3b"]["one_browser_process"] = self.R["M3b"].get("chromium:browser") == 1
            log(f"M3b: {self.R['M3b']}")
            if self.a.env == "vm":
                self.R["M4"] = {"at_10": nvidia_smi()}
            self.shot("10apps")
            self.m7("bench-a1-1", "10apps")
            self.m5(["bench-a1-1", "bench-a1-2", "bench-a1-3", "bench-a1-4"])
            # 9. pygame
            self.f2_f5()
            # 10./11. functional
            self.f1("bench-a1-1")
            self.f6()
            self.f7()
            self.f8_f9_f11("bench-a1-1")
            self.f10()
            self.f12()
            # close everything through the shell (✕ path)
            closed = 0
            for aid in apps + order:
                ok, _ = self.proto.ok(f"close {aid}")
                closed += ok
            self.R["F"]["close_all"] = {"closed": closed, "of": len(apps + order)}
            time.sleep(1)
            self.m3("after_close")
        finally:
            try:
                self.proto.cmd("stop", timeout=30)
            except Exception as e:
                self.note(f"stop failed: {e}")
            try:
                self.proto.p.wait(timeout=10)
            except Exception:
                self.proto.p.kill()
            self.R["driver_log_tail"] = self.proto.log[-40:]
            self.R["finished"] = time.strftime("%Y-%m-%d %H:%M:%S")
        return self.R


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidate", required=True, choices=["baseline", "chromium-wm", "qtile"])
    ap.add_argument("--env", required=True, choices=["host", "vm"])
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--vibeos", default=os.environ.get("VIBEOS_DIR", "/opt/vibeos"))
    ap.add_argument("--out", default=os.path.join(HERE, "results"))
    ap.add_argument("--ops", type=int, default=100)
    ap.add_argument("--drags", type=int, default=5)
    ap.add_argument("--clicks", type=int, default=50)
    ap.add_argument("--m5-seconds", type=int, default=60)
    ap.add_argument("--mem-samples", type=int, default=3)
    ap.add_argument("--mem-gap", type=float, default=2.0)
    ap.add_argument("--install-only", action="store_true")
    ap.add_argument("--drop-caches", action="store_true", help="cold M1: drop the page cache before start (root)")
    ap.add_argument("--run-offset", type=int, default=0, help="number the runs from offset+1")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    ids = install_apps(a.vibeos)
    log(f"installed {len(ids)} bench apps into {a.vibeos}/cache/apps")
    if a.install_only:
        return
    bench = BenchServer()
    xp = XProbe()
    log(f"X server pid {xp.xserver_pid}, screens {xp.rects}")
    for n in range(a.run_offset + 1, a.run_offset + a.runs + 1):
        log(f"=== {a.candidate} / {a.env} run {n}/{a.run_offset + a.runs}")
        r = Run(a, bench, xp, n, a.out).run()
        path = os.path.join(a.out, f"{a.candidate}-{a.env}-run{n}.json")
        json.dump(r, open(path, "w"), indent=1, default=str)
        log(f"wrote {path}")
        time.sleep(3)


if __name__ == "__main__":
    main()
