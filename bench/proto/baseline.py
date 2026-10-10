#!/usr/bin/env python3
"""Candidate 1 (baseline): the unmodified web shell in Chromium kiosk windows, one per
monitor exactly as .xinitrc launches them, driven through the shell's OWN functions
over CDP (openApp, restoreWin, the `windows` map). Same line protocol as wmproto.py;
`toscreen` answers UNSUPPORTED — that is the measurement."""
import json
import os
import shlex
import subprocess
import sys
import time

from Xlib import display

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cdp import Browser  # noqa: E402
from common import CHROME, CHROME_WRAP, EXTRA_FLAGS, KERNEL, XINITRC_FLAGS, chrome_env, monitors, now_ms, say  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = open(os.environ.get("BENCH_LOG", "/tmp/bench-baseline.log"), "ab", buffering=0)
OPENBOX_CMD = os.environ.get("BENCH_OPENBOX_CMD_BASELINE", os.environ.get("BENCH_OPENBOX_CMD", "openbox"))
JS_FIND = "(() => { const w = [...windows.values()].find((w) => w.appId === %s); if (!w) throw new Error('no window ' + %s); return w; })()"


class Baseline:
    def __init__(self):
        self.d = display.Display()
        self.rects = monitors(self.d)
        self.browsers = []          # Browser (screen 0 has CDP) / Popen
        self.wm = None
        self.session = None
        self.b0 = None
        self.m0 = None

    def kiosk_cmd(self, n, x, y, w, h):
        prof = f"/tmp/kiosk-{n}"
        subprocess.run(["rm", "-rf", prof])
        return CHROME_WRAP + [CHROME] + XINITRC_FLAGS + EXTRA_FLAGS + [f"--user-data-dir={prof}",
                f"--window-position={x},{y}", f"--window-size={w},{h}", "--start-fullscreen",
                f"--app={KERNEL}/?screen={n}&x={x}&y={y}&w={w}&h={h}"]

    def start(self):
        # openbox-session as in .xinitrc: its only job is stacking; product config (decorated)
        self.wm = subprocess.Popen(shlex.split(OPENBOX_CMD), stdout=LOG, stderr=LOG, env=chrome_env())
        time.sleep(0.5)
        for n, (x, y, w, h) in enumerate(self.rects):
            cmd = self.kiosk_cmd(n, x, y, w, h)
            if n == 0:
                self.b0 = Browser(cmd, env=chrome_env(), log=LOG)
                self.browsers.append(self.b0)
            else:
                self.browsers.append(subprocess.Popen(cmd, stdout=LOG, stderr=LOG, env=chrome_env()))
        self.session = self.b0.attach(f"{KERNEL}/?screen=0")
        t0 = time.monotonic()
        while time.monotonic() - t0 < 60:
            try:
                if self.b0.evaluate(self.session, "typeof openApp === 'function' && !document.querySelector('#desktop').classList.contains('hidden')", False):
                    break
            except Exception:
                pass
            time.sleep(0.05)
        self.m0 = self.b0.metrics(self.session)
        say(f"READY {now_ms()}")

    def ev(self, js, await_promise=True):
        return self.b0.evaluate(self.session, js, await_promise)

    def open(self, app, x, y, w, h):
        js = f"""(async () => {{
            openApp({json.dumps(app)}, {json.dumps(app)});
            for (let i = 0; i < 4000; i++) {{
                const w = [...windows.values()].find((w) => w.appId === {json.dumps(app)} && w.iframe);
                if (w) {{
                    Object.assign(w.el.style, {{ left: '{x}px', top: '{y}px', width: '{w}px', height: '{h}px' }});
                    const r = w.el.getBoundingClientRect();
                    return {{ t: Date.now(), x: r.x, y: r.y, w: r.width, h: r.height, id: w.id }};
                }}
                await new Promise((r) => setTimeout(r, 5));
            }}
            throw new Error('open timed out');
        }})()"""
        return self.ev(js)

    def op(self, app, op, args):
        w = JS_FIND % (json.dumps(app), json.dumps(app))
        if op == "move":
            js = f"(() => {{ const w = {w}; w.el.style.left = '{args[0]}px'; w.el.style.top = '{args[1]}px'; const r = w.el.getBoundingClientRect(); return [r.x, r.y, r.width, r.height, !w.el.classList.contains('minimized')]; }})()"
        elif op == "resize":
            js = f"(() => {{ const w = {w}; w.el.style.width = '{args[0]}px'; w.el.style.height = '{args[1]}px'; const r = w.el.getBoundingClientRect(); return [r.x, r.y, r.width, r.height, !w.el.classList.contains('minimized')]; }})()"
        elif op in ("raise", "restore"):
            js = f"(() => {{ const w = {w}; restoreWin(w); return [w.el.style.zIndex, w.el.classList.contains('focused')]; }})()"
        elif op == "minimize":
            js = f"(() => {{ const w = {w}; w.el.querySelector('.win-min').click(); return [w.el.classList.contains('minimized'), document.querySelectorAll('#taskbar .task-chip').length]; }})()"
        elif op == "maximize":
            js = f"(() => {{ const w = {w}; w.el.querySelector('.win-max').click(); const r = w.el.getBoundingClientRect(); return [r.x, r.y, r.width, r.height, w.el.classList.contains('maximized')]; }})()"
        elif op == "close":
            js = f"(() => {{ const w = {w}; w.el.querySelector('.win-close').click(); return [!windows.has(w.id)]; }})()"
        else:
            return None
        return self.ev(js, False)

    def geom(self, app):
        js = f"(() => {{ const w = {JS_FIND % (json.dumps(app), json.dumps(app))}; const r = w.el.getBoundingClientRect(); return [r.x, r.y, r.width, r.height, !w.el.classList.contains('minimized')]; }})()"
        return self.ev(js, False)

    def windows(self):
        return self.ev("[...windows.values()].map((w) => ({ id: w.id, title: w.title, app: w.appId, kind: w.appId ? 'app' : 'other', minimized: w.el.classList.contains('minimized'), active: w.el.classList.contains('focused') }))", False)

    def jsprof(self, reset=False):
        m = self.b0.metrics(self.session)
        out = {k: m.get(k, 0) - self.m0.get(k, 0) for k in ("TaskDuration", "ScriptDuration", "LayoutDuration", "RecalcStyleDuration")}
        out["JSHeapUsedSize"] = m.get("JSHeapUsedSize")
        if reset:
            self.m0 = m
        return out

    def pids(self):
        out = [self.b0.proc.pid] + [b.pid for b in self.browsers[1:]]
        if self.wm:
            out.append(self.wm.pid)
        return out

    def stop(self):
        for b in self.browsers[1:]:
            b.terminate()
        self.b0.close()
        if self.wm:
            self.wm.terminate()
        if "docker" in OPENBOX_CMD:
            subprocess.run(["docker", "exec", "kairos-bench-host", "sh", "-c", "kill $(pidof openbox) 2>/dev/null"], timeout=10)
        time.sleep(1)
        subprocess.run(["pkill", "-f", "--", "--user-data-dir=/tmp/kiosk-"])


def main():
    b = Baseline()
    for line in sys.stdin:
        parts = line.strip().split()
        if not parts:
            continue
        cmd, args = parts[0], parts[1:]
        try:
            if cmd == "start":
                b.start()
            elif cmd == "screens":
                say("SCREENS %d %s" % (len(b.rects), " ".join(",".join(map(str, r)) for r in b.rects)))
            elif cmd == "pids":
                say("PIDS " + " ".join(map(str, b.pids())))
            elif cmd == "open":
                r = b.open(args[0], *map(int, args[1:5]))
                say(f"WIN {args[0]} {r['t']} {r['id']} {r}")
            elif cmd == "toscreen":
                say("UNSUPPORTED")
            elif cmd in ("move", "resize", "raise", "minimize", "restore", "close", "maximize"):
                r = b.op(args[0], cmd, args[1:])
                say(f"OK {now_ms()} {json.dumps(r)}")
            elif cmd == "geom":
                g = b.geom(args[0])
                say("GEOM %d %d %d %d %d 0" % (g[0], g[1], g[2], g[3], int(g[4])))
            elif cmd == "windows":
                say("WINDOWS " + json.dumps(b.windows()))
            elif cmd == "pyprof":
                say("PYPROF " + json.dumps({"none": True, "js": b.jsprof(bool(args and args[0] == "reset"))}))
            elif cmd == "eval":
                say("EVAL " + json.dumps(b.ev(" ".join(args))))
            elif cmd == "maptime":
                say("MAPTIME 0")
            elif cmd == "stop":
                b.stop()
                say("BYE")
                break
            else:
                say("ERR unknown command " + cmd)
        except Exception as e:
            say(f"ERR {cmd}: {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
