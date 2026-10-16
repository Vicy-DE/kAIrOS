#!/usr/bin/env python3
"""kAIrOS R82 — games in windows, with ✨ improve (docs/GAMES-IN-WINDOWS-2026-10-15.md).

Standalone: `python3 tests/test_games_windows.py` (run-tests.sh runs it after the suite). Writes
tests/results-games-windows.json (not for a KAIROS_TEST_ONLY subset).
* gw_frame_bmp, gw_shell_static: no X needed.
* the e2e tests run on a REAL X server: Xvfb + openbox with the kiosk's own rc.xml + a kiosk Chromium (--kiosk, the
  .xinitrc geometry) on the REAL shell (index.html + shell.js + browser.js + games.js) served by the kernel's routes
  (server.add_routes, in-process, a private VIBEOS_DIR; the forge LLM mocked: real pygame games — v1 red, a ✨ version
  asked for "green" green) + real pygame windows; real pointer input through XTEST, the page driven over the
  DevTools protocol, the X side read with python-xlib. They skip without Xvfb / openbox / chromium / python3-xlib /
  pygame — `bash dev/docker/build.sh x11` builds the image that has them (kairos-fix-x11:local).
  KAIROS_SHOTS=<dir> saves X screenshots of the whole display (the game window included).
"""
import asyncio
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
RESULTS = {}
SHOTS = os.environ.get("KAIROS_SHOTS", "")
RC_XML = REPO / "iso" / "config" / "includes.chroot" / "home" / "kairos" / ".config" / "openbox" / "rc.xml"


def record(name, reqs, status, detail=""):
    RESULTS[name] = {"reqs": reqs, "status": status, "detail": detail[:300]}
    mark = {"pass": "✓", "fail": "✗", "skip": "~"}[status]
    print(f"  {mark} {name} [{', '.join(reqs)}] {detail[:200]}", flush=True)


class SkipTest(Exception):
    pass


TESTS = []


def test(name, reqs, group=None):
    """group: tests that share one X session run in one event loop, in order (gw_e2e_*)"""
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


# ----------------------------------------------------------------------------- the kernel, in-process, private dir
_K = {}


def kernel():
    if "server" in _K:
        return _K["server"]
    try:
        import aiohttp  # noqa: F401
    except ImportError:
        raise SkipTest("aiohttp not installed")
    d = tempfile.mkdtemp(prefix="kairos-gw-")
    os.environ.update(VIBEOS_DIR=d, VIBEOS_MANIFEST=str(REPO / "manifests" / "tier-8gb.json"),
                      VIBEOS_MANIFESTS=str(REPO / "manifests"), VIBEOS_SHELL=str(REPO / "shell"),
                      VIBEOS_FORCE_OFFLINE="1", KAIROS_LOG_DIR=str(Path(d) / "log"),
                      VIBEOS_TOKEN_FILE=str(Path(d) / "token"))
    sys.path.insert(0, str(REPO / "kernel"))
    import server
    server.engine_missing = lambda binary: None          # the forge LLM is mocked below
    _K["server"] = server
    return server


GAME = """import pygame, sys, math
pygame.init()
screen = pygame.display.set_mode((960, 540))
pygame.display.set_caption(%CAPTION%)
clock = pygame.time.Clock()
t = 0
while True:
    for e in pygame.event.get():
        if e.type == pygame.QUIT or (e.type == pygame.KEYDOWN and e.key == pygame.K_ESCAPE):
            pygame.quit(); sys.exit()
    t += 1
    screen.fill(%BG%)
    for i in range(6):
        x = int(480 + 380 * math.cos(t / 40 + i))
        y = int(270 + 200 * math.sin(t / 55 + i * 1.7))
        pygame.draw.circle(screen, (235, 235, 245), (x, y), 26)
    pygame.display.flip()
    clock.tick(60)
"""
RED, GREEN = (150, 30, 40), (20, 130, 60)


def game_src(bg, caption):
    return GAME.replace("%BG%", repr(bg)).replace("%CAPTION%", json.dumps(caption))


class Forge:
    """the mocked forge LLM (MM.chat): a real pygame game; a ✨ request with 'green' in it gets the green version"""

    def __init__(self, s):
        self.s, self.prompts, self.saved = s, [], {}

    def __enter__(self):
        env = self

        async def chat(system, messages, key="llm", max_tokens=16384, temperature=0.7, sampling=None, gen_info=None):
            user = messages[-1]["content"]
            env.prompts.append({"system": system, "user": user, "key": key})
            ins = re.search(r"USER IMPROVEMENT REQUEST: ([^\n]*)", user)
            if ins and "green" in ins.group(1):
                return game_src(GREEN, "Neon Bouncer · green")
            return game_src(RED, "Neon Bouncer")
        self.saved["chat"], self.s.MM.chat = self.s.MM.chat, chat
        return self

    def __exit__(self, *exc):
        self.s.MM.chat = self.saved["chat"]


# ----------------------------------------------------------------------------- no-X tests
@test("gw_frame_bmp", ["R82", "R70"])
def t_bmp(ctx):
    """the covered frame's screenshot: the game's BGRX rows as a 32-bit top-down BMP, opaque (the pad byte a real
    Xorg may leave 0 is set to 0xff), sized w*h*4 + 54"""
    s = kernel()
    w, h = 3, 2
    data = bytes([10, 20, 30, 0] * (w * h))
    b = s.frame_bmp(data, w, h)
    assert b[:2] == b"BM" and struct.unpack("<I", b[2:6])[0] == len(b) == 54 + w * h * 4
    hdr = struct.unpack("<IiiHHIIiiII", b[14:54])
    assert hdr[:5] == (40, 3, -2, 1, 32) and hdr[5] == 0, hdr
    assert b[54:58] == bytes([10, 20, 30, 255]) and b[-4:] == bytes([10, 20, 30, 255])
    return f"{len(b)} B: BI_RGB 32 bpp top-down, pad byte opaque"


@test("gw_shell_static", ["R82", "R81", "R46"])
def t_static(ctx):
    """games.js loads after shell.js + browser.js; the hooks shell.js gives a game frame (focus, move, close, min
    size) and the record list's game kind; the X feed on every screen, chips on screen 0 only; launches go through
    launchGame (search, ⊞ games, the vibe.game bridge, the dock); games.js talks to the frame routes, takes the R70
    screenshot before it hides the game, watches the desktop for covers, offers ✨ and the versions"""
    html = (REPO / "shell" / "index.html").read_text()
    assert html.index('src="/shell/shell.js"') < html.index('src="/shell/browser.js"') < html.index('src="/shell/games.js"')
    js = (REPO / "shell" / "shell.js").read_text()
    for needle in ("if (win.onFocus) win.onFocus(e)", 'if (win.onMove) win.onMove("move")', 'if (win.onMove) win.onMove("end")',
                   "if (win.onClose)", "win.minW || 280", "win.minH || 180", 'kind: w.game ? "game"',
                   'if (win.game) return "game:"', "if (w.frame) continue;", "if (!SCREEN.primary) continue;",
                   "gamesSync(list, frames || [])", 'return await launchGame({ prompt: String(args[0] ?? "") });',
                   "launchGame({ prompt: a.prompt, slug: a.slug })", "launchGame({ slug: g.slug })", "launchGame({ slug: it.app })"):
        assert needle in js, needle
    boot = js.split("(async function boot()")[1]
    assert boot.index("watchXWindows();") > boot.index("} else {"), "the feed starts on every screen"
    g = (REPO / "shell" / "games.js").read_text()
    for needle in ("/api/frames/", '"follow"', '"place"', '"snapshot"', '"cover"', '"uncover"', '"minimize"', '"restore"',
                   '"adopt"', "/api/game/versions", "/api/game/version", "evolve: true", "restart: true", "stickyPrompt(win)",
                   "new MutationObserver(gameCoverSoon)", "sx: screenX", "GAME_STATS"):
        assert needle in g, needle
    assert g.index("await gameSnapshot(win)") < g.index('await frameOp(win, "cover")'), \
        "the screenshot is taken while the game is still on screen"
    if shutil.which("node"):
        for f in ("shell.js", "games.js"):
            subprocess.run(["node", "--check", str(REPO / "shell" / f)], check=True, capture_output=True)
    return "games.js after browser.js; frame hooks + game records; feed on every screen; 4 launch paths; frame routes, snapshot before hide"


# ----------------------------------------------------------------------------- the X session
def need_x():
    missing = [b for b in ("Xvfb", "openbox", "chromium") if not shutil.which(b)]
    try:
        import Xlib  # noqa: F401
    except ImportError:
        missing.append("python3-xlib")
    r = subprocess.run([sys.executable, "-c", "import pygame"], capture_output=True,
                       env=dict(os.environ, PYGAME_HIDE_SUPPORT_PROMPT="1"))
    if r.returncode != 0:
        missing.append("pygame")
    if missing:
        raise SkipTest("needs a real X server: " + ", ".join(missing) + " missing (bash dev/docker/build.sh x11)")


def png_from_bgrx(data, w, h):
    raw = bytearray()
    for y in range(h):
        row = data[y * w * 4:(y + 1) * w * 4]
        rgb = bytearray(w * 3)
        rgb[0::3], rgb[1::3], rgb[2::3] = row[2::4], row[1::4], row[0::4]
        raw += b"\x00" + rgb
    chunk = lambda t, dd: struct.pack(">I", len(dd)) + t + dd + struct.pack(">I", zlib.crc32(t + dd) & 0xffffffff)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(raw), 6)) + chunk(b"IEND", b""))


class XSession:
    """Xvfb + openbox (the kiosk's rc.xml) + Chromium windows on the shell; a test-side python-xlib connection"""

    def __init__(self, w, h):
        self.w, self.h = w, h
        self.num = 50 + (os.getpid() + int(time.time())) % 400
        while Path(f"/tmp/.X11-unix/X{self.num}").exists():
            self.num += 1
        self.disp = f":{self.num}"
        self.procs, self.browsers, self.td = [], [], tempfile.mkdtemp(prefix="kairos-gwx-")

    def start(self):
        from Xlib import display
        self.procs.append(subprocess.Popen(["Xvfb", self.disp, "-screen", "0", f"{self.w}x{self.h}x24", "-ac", "-nolisten", "tcp"],
                                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        for _ in range(100):
            if Path(f"/tmp/.X11-unix/X{self.num}").exists():
                break
            time.sleep(0.05)
        time.sleep(0.3)
        env = dict(os.environ, DISPLAY=self.disp)
        self.procs.append(subprocess.Popen(["openbox", "--config-file", str(RC_XML)], env=env,
                                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        self.d = display.Display(self.disp)
        sys.path.insert(0, str(REPO / "kernel"))
        import wm
        self.c = wm.EWMH(self.d)
        for _ in range(100):
            if self.c.wm_name():
                break
            time.sleep(0.05)
        assert self.c.wm_name() == "Openbox", self.c.wm_name()

    def chromium(self, url, port, x, y, w, h, kiosk=True):
        if not kiosk:       # an --app window without Chromium's own title bar: its viewport is the window
            prefs = Path(self.td) / f"p{port}" / "Default"
            prefs.mkdir(parents=True, exist_ok=True)
            (prefs / "Preferences").write_text(json.dumps({"browser": {"custom_chrome_frame": False}}))
        args = ["chromium", "--no-sandbox", "--disable-gpu", "--no-first-run", "--noerrdialogs", "--disable-infobars",
                "--disable-dev-shm-usage", "--force-device-scale-factor=1", "--disable-translate", "--hide-scrollbars",
                f"--remote-debugging-port={port}", f"--user-data-dir={self.td}/p{port}",
                f"--window-position={x},{y}", f"--window-size={w},{h}"] + (["--kiosk"] if kiosk else []) + [f"--app={url}"]
        p = subprocess.Popen(args, env=dict(os.environ, DISPLAY=self.disp), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.browsers.append(p)
        return p

    # the X side, read directly (geometry, mapping, pixels, the transient hint)
    def geom(self, xid):
        return self.c.geom(xid)

    def visible(self, xid, kiosk):
        """the game shows over the desktop: mapped and stacked above the page's window (a covered game is lowered
        under it, a minimized one unmapped)"""
        g = self.c.geom(xid)
        return bool(g and g[4] and self.c.stacked_above(xid, kiosk))

    def transient(self, xid):
        try:
            p = self.c.win(xid).get_full_property(68, 0)
        except Exception:
            return None
        return int(p.value[0]) if p and len(p.value) else None

    def pixel(self, x, y):
        img = self.c.root.get_image(x, y, 1, 1, 2, 0xffffffff)
        b = bytes(img.data)
        return (b[2], b[1], b[0])

    def area_color(self, x, y, n=5):
        """the mean colour of an n x n block (a moving circle may pass a single pixel)"""
        img = bytes(self.c.root.get_image(x, y, n, n, 2, 0xffffffff).data)
        px = [(img[i + 2], img[i + 1], img[i]) for i in range(0, len(img), 4)]
        return tuple(sum(p[k] for p in px) // len(px) for k in range(3))

    def chrome_windows(self):
        out = []
        for xid in self.c.clients():
            inst, cls = self.c.wm_class(xid)
            if "chrom" in f"{inst} {cls}".lower():
                out.append((xid, self.c.geom(xid)))
        return out

    def shot(self, name):
        if not SHOTS:
            return None
        data = bytes(self.c.root.get_image(0, 0, self.w, self.h, 2, 0xffffffff).data)
        Path(SHOTS).mkdir(parents=True, exist_ok=True)
        p = Path(SHOTS) / name
        p.write_bytes(png_from_bgrx(data, self.w, self.h))
        return str(p)

    # real input through XTEST
    def move(self, x, y):
        from Xlib import X
        from Xlib.ext import xtest
        xtest.fake_input(self.d, X.MotionNotify, x=int(x), y=int(y))
        self.d.sync()

    def button(self, down, b=1):
        from Xlib import X
        from Xlib.ext import xtest
        xtest.fake_input(self.d, X.ButtonPress if down else X.ButtonRelease, b)
        self.d.sync()

    def click(self, x, y):
        self.move(x, y)
        self.button(True)
        self.button(False)

    def key(self, keysym_name):
        from Xlib import X, XK
        from Xlib.ext import xtest
        code = self.d.keysym_to_keycode(XK.string_to_keysym(keysym_name))
        xtest.fake_input(self.d, X.KeyPress, code)
        xtest.fake_input(self.d, X.KeyRelease, code)
        self.d.sync()

    def stop(self):
        for p in self.browsers + list(reversed(self.procs)):
            if p.poll() is None:
                p.terminate()
                try:
                    p.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    p.kill()
                    p.wait()
        try:
            self.d.close()
        except Exception:
            pass
        shutil.rmtree(self.td, ignore_errors=True)


class CDP:
    """the kiosk page over the DevTools protocol (aiohttp WebSocket)"""

    def __init__(self, port):
        self.port, self.mid, self.ws, self.cs = port, 0, None, None

    async def attach(self, match, timeout=30):
        import aiohttp
        self.cs = aiohttp.ClientSession()
        t0 = time.time()
        while time.time() - t0 < timeout:
            try:
                async with self.cs.get(f"http://127.0.0.1:{self.port}/json/list") as r:
                    targets = await r.json()
                t = next((t for t in targets if t.get("type") == "page" and match in t.get("url", "")), None)
                if t:
                    self.ws = await self.cs.ws_connect(t["webSocketDebuggerUrl"], max_msg_size=0)
                    return self
            except Exception:
                pass
            await asyncio.sleep(0.2)
        raise AssertionError(f"no page {match} on :{self.port}")

    async def send(self, method, params=None):
        self.mid += 1
        mid = self.mid
        await self.ws.send_json({"id": mid, "method": method, "params": params or {}})
        while True:
            msg = await self.ws.receive_json(timeout=60)
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})

    async def ev(self, expr):
        r = await self.send("Runtime.evaluate", {"expression": expr, "awaitPromise": True, "returnByValue": True})
        if r.get("exceptionDetails"):
            raise RuntimeError(f"{expr[:160]}: {json.dumps(r['exceptionDetails'])[:400]}")
        return r.get("result", {}).get("value")

    async def wait(self, expr, timeout=15, what=""):
        t0 = time.time()
        while time.time() - t0 < timeout:
            try:
                if await self.ev(expr):
                    return True
            except RuntimeError:
                pass
            await asyncio.sleep(0.05)
        raise AssertionError(f"timeout ({timeout} s): {what or expr[:200]}")

    async def rect(self, sel):
        return await self.ev(f"(() => {{ const e = document.querySelector({json.dumps(sel)}); if (!e) return null; "
                             "const r = e.getBoundingClientRect(); return [r.left, r.top, r.width, r.height]; })()")

    async def type(self, text):
        await self.send("Input.insertText", {"text": text})

    async def enter(self):
        down = {"type": "keyDown", "key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13, "text": "\r"}
        await self.send("Input.dispatchKeyEvent", down)
        await self.send("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13})

    async def close(self):
        try:
            if self.ws:
                await self.ws.close()
        finally:
            if self.cs:
                await self.cs.close()


class Desk:
    """the kernel's routes on a test server + the X session + one Chromium page per screen"""

    def __init__(self, s, w, h, pages):
        self.s, self.w, self.h, self.pages_spec = s, w, h, pages     # [(screen, x, y, w, h, kiosk)]
        self.pages, self.x = [], None

    async def __aenter__(self):
        from aiohttp import web
        from aiohttp.test_utils import TestServer
        need_x()
        self.x = XSession(self.w, self.h)
        self.x.start()
        self.saved_env = {k: os.environ.get(k) for k in ("KAIROS_DISPLAY", "DISPLAY", "SDL_VIDEODRIVER", "XAUTHORITY")}
        os.environ.update(KAIROS_DISPLAY=self.x.disp, DISPLAY=self.x.disp, SDL_VIDEODRIVER="x11")
        os.environ.pop("XAUTHORITY", None)
        self.s.wm.WM = self.s.wm.WindowService()                     # a fresh window layer on this display
        app = web.Application()
        self.s.add_routes(app)
        self.server = TestServer(app, host="127.0.0.1")
        await self.server.start_server()
        self.base = f"http://127.0.0.1:{self.server.port}"
        port = 9400 + (os.getpid() + int(time.time())) % 400
        for i, (screen, x, y, w, h, kiosk) in enumerate(self.pages_spec):
            q = f"?screen={screen}&x={x}&y={y}&w={w}&h={h}"
            before = {xid for xid, _ in self.x.chrome_windows()}
            self.x.chromium(self.base + "/" + q, port + i, x, y, w, h, kiosk)
            if not kiosk:   # openbox centres a window that asks for no position: put it where .xinitrc's kiosk would be
                for _ in range(200):
                    new = [xid for xid, g in self.x.chrome_windows() if xid not in before and g and g[4] and g[2] > 300]
                    if new:
                        break
                    await asyncio.sleep(0.05)
                assert new, "the page's window did not map"
                self.x.c.moveresize(new[0], x=x, y=y, w=w, h=h)
                await asyncio.sleep(0.3)
            page = await CDP(port + i).attach(f"screen={screen}")
            await page.wait('!!document.querySelector("#desktop") && !document.querySelector("#desktop").classList.contains("hidden")',
                            30, f"the desktop on screen {screen}")
            self.pages.append(page)
        await asyncio.sleep(1.5)                                     # the welcome toast, the dock, the X feed
        return self

    async def __aexit__(self, *exc):
        try:
            for p in self.pages:
                await p.close()
            try:
                await asyncio.wait_for(self.s.wm.WM.shutdown(), 5)
            except Exception:
                pass
            await self.server.close()
        finally:
            self.x.stop()
            for k, v in self.saved_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


async def until(pred, timeout=10.0, step=0.002):
    """poll a synchronous X-side predicate without starving the event loop (the kernel runs on it)"""
    t0 = time.perf_counter()
    while True:
        r = pred()
        if r:
            return r, time.perf_counter() - t0
        if time.perf_counter() - t0 > timeout:
            return None, time.perf_counter() - t0
        await asyncio.sleep(step)


def near(c, ref, tol=40):
    return all(abs(a - b) <= tol for a, b in zip(c, ref))


async def frame_state(page):
    return await page.ev("""(() => { const w = [...GAME_FRAMES.values()][0]; if (!w) return null;
      const b = w.el.querySelector('.win-body').getBoundingClientRect(); const img = w.el.querySelector('.game-shot');
      return {fid: w.game.fid, id: w.id, slug: w.game.slug, version: w.game.version, xid: w.game.xid, pid: w.game.pid,
              state: w.game.state, busy: !!w.game.busy, title: w.el.querySelector('.win-title').textContent,
              body: [b.left, b.top, b.width, b.height], focused: w.el.classList.contains('focused'),
              shot: !img.classList.contains('hidden') && img.naturalWidth > 0 ? [img.naturalWidth, img.naturalHeight] : null,
              note: w.el.querySelector('.game-note').classList.contains('hidden') ? null : w.el.querySelector('.game-note').textContent}; })()""")


def proc_state(pid):
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().rsplit(")", 1)[1].split()[0]
    except (OSError, IndexError, TypeError):
        return ""


# ----------------------------------------------------------------------------- the e2e scenario (one X session)
E2E = {}


@test("gw_e2e_launch_frame_holds_game", ["R82", "R7", "R68", "R28"], group="e2e")
async def t_launch(ctx):
    """a new game from the shell (launchGame, as the search / ⊞ / vibe.game do): its frame shows the forge's progress,
    then the pygame window maps INSIDE the frame's body (X geometry: centred in the 960x540 body), transient for the
    kiosk window; a real click on the desktop (openbox focuses + raises the fullscreen kiosk) leaves the game visible
    above it (X pixel = the game's colour); the dock has the game's icon (glow); launching it again (the search's
    ▶) shows the same frame, no second process"""
    s, desk = ctx["s"], ctx["desk"]
    x, page = desk.x, desk.pages[0]
    t0 = time.perf_counter()
    await page.ev('launchGame({prompt: "neon bouncer"}).then((r) => { window.__r = r; }, (e) => { window.__e = String(e); }), 1')
    await page.wait("!!window.__r || !!window.__e", 90, "the forge + launch")
    assert not await page.ev("window.__e || null"), await page.ev("window.__e")
    t_ready = time.perf_counter() - t0
    r = await page.ev("window.__r")
    assert r["running"] and r["version"] == 1 and r["gate"] == "mapped" and r["window"], r
    fs = await frame_state(page)
    xid = fs["xid"]
    assert xid == r["window"] and fs["slug"] == "neon-bouncer" and fs["title"] == "🕹 neon bouncer · v1", fs
    kiosks = x.chrome_windows()
    ok, _ = await until(lambda: x.visible(xid, kiosks[0][0]), 5)
    assert ok, "the game is not above the desktop"
    g = x.geom(xid)
    bx, by, bw, bh = [round(v) for v in fs["body"]]
    assert (g[2], g[3]) == (960, 540) and bx <= g[0] and by <= g[1] and g[0] + g[2] <= bx + bw and g[1] + g[3] <= by + bh, (g, fs["body"])
    assert (g[0] - bx, g[1] - by) == ((bw - 960) // 2, (bh - 540) // 2), (g, fs["body"])
    assert x.transient(xid) == kiosks[0][0] and kiosks[0][1][:4] == (0, 0, x.w, x.h), (x.transient(xid), kiosks)
    col = x.area_color(g[0] + 10, g[1] + 510)
    assert near(col, RED), col
    # a real click on the desktop: openbox focuses + raises the (fullscreen) kiosk — the game stays above it
    x.click(1700, 950)
    await asyncio.sleep(0.4)
    assert x.c.active() == kiosks[0][0], hex(x.c.active())
    col2 = x.area_color(g[0] + 10, g[1] + 510)
    assert near(col2, RED), f"a desktop click hid the game: {col2}"
    await page.wait('document.querySelector(\'#dock [data-game="neon-bouncer"]\') && '
                    'document.querySelector(\'#dock [data-game="neon-bouncer"]\').classList.contains("shown")', 5, "the dock icon")
    E2E["shot_frame"] = x.shot("game-in-its-frame.png")
    # the search's ▶ for a game whose frame is here: the same frame, the same process
    pid = fs["pid"]
    n = await page.ev("windows.size")
    r2 = await page.ev("launchGame({slug: 'neon-bouncer'})")
    assert r2["already"] and await page.ev("windows.size") == n and (await frame_state(page))["pid"] == pid, r2
    E2E.update(xid=xid, pid=pid, fid=fs["fid"], kiosk=kiosks[0][0])
    return (f"forged + mapped in {t_ready:.1f} s; window {g[2]}x{g[3]} at ({g[0]},{g[1]}) inside body ({bx},{by},{bw},{bh}), "
            f"centred; transient for the kiosk; game colour {col2} above the focused kiosk; dock glow; relaunch = same frame")


@test("gw_e2e_drag_follow", ["R82", "R68"], group="e2e")
async def t_drag(ctx):
    """a REAL drag (XTEST: press on the frame's title, 24 steps of (+14, +7) px every 16 ms, release): the shell posts
    `follow` per step (at most one in flight), the kernel moves the game; per step the time until the X window stands
    where the frame's body is now (the follow lag); the drop is placed + verified; a resize of the frame keeps the
    fixed-size game centred and the frame never gets smaller than the game; ▢ maximize centres it"""
    s, desk = ctx["s"], ctx["desk"]
    x, page, xid = desk.x, desk.pages[0], E2E["xid"]
    fs = await frame_state(page)
    title = await page.ev(f"(() => {{ const r = windows.get('{fs['id']}').el.querySelector('.win-title').getBoundingClientRect(); "
                          "return [r.left + 40, r.top + r.height / 2]; })()")
    g0 = x.geom(xid)
    lags, misses = [], 0
    x.move(*title)
    await asyncio.sleep(0.05)
    x.button(True)
    await asyncio.sleep(0.05)
    dx, dy, steps = 14, 7, 24
    for k in range(1, steps + 1):
        x.move(title[0] + k * dx, title[1] + k * dy)
        want = (g0[0] + k * dx, g0[1] + k * dy)
        ok, dt = await until(lambda: (x.geom(xid) or (0, 0))[:2] == want, 0.5, 0.001)
        if ok:
            lags.append(dt * 1000)
        else:
            misses += 1
        await asyncio.sleep(max(0.0, 0.016 - dt))
    x.button(False)
    final = (g0[0] + steps * dx, g0[1] + steps * dy)
    ok, _ = await until(lambda: (x.geom(xid) or (0, 0))[:2] == final, 2)
    assert ok, (x.geom(xid), final)
    fs = await frame_state(page)
    bx, by = round(fs["body"][0]), round(fs["body"][1])
    assert (x.geom(xid)[0] - bx, x.geom(xid)[1] - by) == ((round(fs["body"][2]) - 960) // 2, (round(fs["body"][3]) - 540) // 2), (x.geom(xid), fs)
    assert misses == 0, f"{misses} steps where the game never reached the frame within 500 ms; lags {lags}"
    lags.sort()
    p50, p95, mx = lags[len(lags) // 2], lags[int(len(lags) * 0.95) - 1], lags[-1]
    # resize: the frame grows by 200 x 100 -> the game stays centred; it cannot get smaller than the game
    await page.ev(f"(() => {{ const w = windows.get('{fs['id']}'); w.el.style.width = (w.el.offsetWidth + 200) + 'px'; "
                  "w.el.style.height = (w.el.offsetHeight + 100) + 'px'; w.onMove('end'); return 1; })()")
    fs2 = await frame_state(page)
    want = (round(fs2["body"][0]) + (round(fs2["body"][2]) - 960) // 2, round(fs2["body"][1]) + (round(fs2["body"][3]) - 540) // 2)
    ok, _ = await until(lambda: (x.geom(xid) or (0, 0))[:2] == want, 2)
    assert ok, (x.geom(xid), want, fs2)
    mins = await page.ev(f"[windows.get('{fs['id']}').minW, windows.get('{fs['id']}').minH]")
    assert mins[0] >= 962 and mins[1] >= 540 + 35, mins
    # ▢ maximize: centred in the big body, then back
    await page.ev(f"windows.get('{fs['id']}').el.querySelector('.win-max').click(), 1")
    fs3 = await frame_state(page)
    want = (round(fs3["body"][0]) + (round(fs3["body"][2]) - 960) // 2, round(fs3["body"][1]) + (round(fs3["body"][3]) - 540) // 2)
    ok, _ = await until(lambda: (x.geom(xid) or (0, 0))[:2] == want, 2)
    assert ok, ("maximized", x.geom(xid), want, fs3)
    await page.ev(f"windows.get('{fs['id']}').el.querySelector('.win-max').click(), 1")
    await asyncio.sleep(0.3)
    E2E["lag"] = {"p50": round(p50, 1), "p95": round(p95, 1), "max": round(mx, 1), "n": len(lags)}
    return (f"follow lag over {len(lags)} real drag steps: p50 {p50:.1f} ms, p95 {p95:.1f} ms, max {mx:.1f} ms "
            f"(pointer event -> X window in place); drop verified; resize keeps it centred, min {mins}; maximize centred")


@test("gw_e2e_cover_screenshot_uncover", ["R82", "R70", "R74", "R68"], group="e2e")
async def t_cover(ctx):
    """what covers the game: the R74 system menu (a real click on the clock), the ⊞ Apps overlay, another window
    above the frame, a toast. Each time the frame shows the game's screenshot (an <img> of the game's own pixels:
    its colour, painted where the game was), the X window drops under the desktop (openbox's stacking order: below
    the kiosk) and the game process is stopped (T); when it is uncovered the window is back on top and the process
    runs; the keyboard stays where it is (the R74 menu stays open — it closes on blur), goes to the game when the
    frame is clicked, back to the desktop after a toast. The cover / uncover latency is from the real input to the
    X window's stacking state."""
    s, desk = ctx["s"], ctx["desk"]
    x, page, xid, pid = desk.x, desk.pages[0], E2E["xid"], E2E["pid"]
    fs = await frame_state(page)
    # the frame to the top-right, under the clock's menu
    await page.ev(f"(() => {{ const w = windows.get('{fs['id']}'); w.el.style.left = (innerWidth - w.el.offsetWidth - 20) + 'px'; "
                  "w.el.style.top = '40px'; w.onMove('end'); return 1; })()")
    await asyncio.sleep(0.5)
    kiosk = E2E["kiosk"]
    shown = lambda: x.visible(xid, kiosk)
    hidden = lambda: not x.visible(xid, kiosk)
    assert shown()
    out = {}
    # 1. the R74 system menu: a real click on the clock
    clock = await page.ev("(() => { const r = document.querySelector('#mb-clock').getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; })()")
    await page.ev("(() => { window.__ev = []; const t = () => Math.round(performance.now()); "
                  "addEventListener('blur', () => __ev.push(['blur', t()])); addEventListener('focus', () => __ev.push(['focus', t()])); "
                  "new MutationObserver(() => __ev.push(['menu', document.querySelector('#mb-sysmenu').className, t()]))"
                  ".observe(document.querySelector('#mb-sysmenu'), {attributes: true}); return 1; })()")
    focus0 = x.c.active()
    t0 = time.perf_counter()
    x.click(*clock)
    ok, dt = await until(hidden, 3)
    E2E["dbg_ev"] = await page.ev("JSON.stringify(window.__ev)")
    assert ok, f"the system menu did not cover the game: page {E2E['dbg_ev']}; stats {await page.ev('JSON.stringify(GAME_STATS.slice(-4))')}"
    out["sysmenu_cover_ms"] = round(dt * 1000, 1)
    try:
        await page.wait("!document.querySelector('#mb-sysmenu').classList.contains('hidden')", 3)
    except AssertionError:
        dbg = await page.ev("JSON.stringify({stats: GAME_STATS.slice(-6), focus: document.hasFocus(), "
                            "menu: document.querySelector('#mb-sysmenu').className, ae: document.activeElement && document.activeElement.className})")
        raise AssertionError(f"the system menu is not open: {dbg}; X active {hex(x.c.active())}, game {hex(xid)}, "
                             f"kiosk {hex(E2E['kiosk'])}, page events {E2E['dbg_ev']}")
    await page.wait("(() => { const w = [...GAME_FRAMES.values()][0]; const i = w.el.querySelector('.game-shot'); return !i.classList.contains('hidden') && i.naturalWidth === 960; })()", 3, "the screenshot")
    await asyncio.sleep(0.1)
    assert proc_state(pid) == "T", proc_state(pid)
    # the screenshot is the game's own picture: its colour, painted by Chromium where the game was
    gx, gy = x.geom(xid)[:2]                   # where the game is (lowered: same geometry) = where its picture is
    col = x.area_color(gx + 10, gy + 510)
    if not near(col, RED):
        x.shot("debug-cover.png") if SHOTS else None
        dbg = await page.ev("""(() => { const w = [...GAME_FRAMES.values()][0]; const i = w.el.querySelector('.game-shot');
          const c = document.createElement('canvas'); c.width = i.naturalWidth; c.height = i.naturalHeight;
          const k = c.getContext('2d'); k.drawImage(i, 0, 0); const d = k.getImageData(10, 510, 1, 1).data;
          const r = i.getBoundingClientRect(); const b = w.el.querySelector('.win-body').getBoundingClientRect();
          return JSON.stringify({px: [...d], img: [r.left, r.top, r.width, r.height], body: [b.left, b.top, b.width, b.height],
                                 style: i.getAttribute('style'), cls: i.className, z: getComputedStyle(i).zIndex}); })()""")
        raise AssertionError(f"the frame's screenshot is not the game: X pixel {col}; page {dbg}")
    menu = await page.rect("#mb-sysmenu")
    E2E["shot_covered"] = x.shot("game-covered-by-the-system-menu.png")
    x.key("Escape")
    t0 = time.perf_counter()
    ok, dt = await until(shown, 3)
    assert ok, "Escape closed the menu, but the game did not come back"
    out["sysmenu_uncover_ms"] = round(dt * 1000, 1)
    await asyncio.sleep(0.15)
    assert proc_state(pid) in ("S", "R"), proc_state(pid)
    assert near(x.area_color(gx + 10, gy + 510), RED)
    # 2. ⊞ Apps (a full-screen overlay)
    await page.ev("showAppsMenu(), 1")
    ok, dt = await until(hidden, 3)
    assert ok, "⊞ Apps did not cover the game"
    out["apps_cover_ms"] = round(dt * 1000, 1)
    games_listed = await page.wait("document.querySelectorAll('#appsmenu-grid .appsmenu-game[data-game=\"neon-bouncer\"]').length === 1", 5, "the game in ⊞")
    await page.ev("hideAppsMenu(), 1")
    ok, dt = await until(shown, 3)
    assert ok, "the game did not come back after ⊞"
    out["apps_uncover_ms"] = round(dt * 1000, 1)
    # 3. another window above the frame; a click on the frame brings it (and the game) back on top
    await page.ev("(() => { const w = openStudio('imagine'); w.el.style.left = (innerWidth - 700) + 'px'; w.el.style.top = '200px'; return 1; })()")
    ok, dt = await until(hidden, 3)
    assert ok, "a window above the frame did not cover the game"
    out["window_cover_ms"] = round(dt * 1000, 1)
    focus_before = x.c.active()
    fs = await frame_state(page)
    title = await page.ev(f"(() => {{ const r = windows.get('{fs['id']}').el.querySelector('.win-title').getBoundingClientRect(); return [r.left + 30, r.top + r.height / 2]; }})()")
    x.click(*title)
    ok, dt = await until(shown, 3)
    assert ok, "a click on the frame did not bring the game back"
    out["window_uncover_ms"] = round(dt * 1000, 1)
    await asyncio.sleep(0.5)
    assert x.c.active() == xid, f"the frame was clicked: the game should have the keyboard ({hex(x.c.active())})"
    await page.ev("[...windows.values()].filter((w) => w.builtin === 'imagine').forEach((w) => closeWin(w)), 1")
    # 4. a toast over the frame (the frame up under the toasts), focus elsewhere: the keyboard is handed back
    await page.ev(f"(() => {{ const w = windows.get('{fs['id']}'); w.el.style.top = '30px'; w.onMove('end'); return 1; }})()")
    await asyncio.sleep(0.3)
    x.click(200, 900)                                   # the desktop has the keyboard now
    await asyncio.sleep(0.3)
    kiosk_focus = x.c.active()
    await page.ev("toast('🔔 a toast over the game frame'), 1")
    ok, dt = await until(hidden, 3)
    assert ok, "a toast over the frame did not cover the game"
    out["toast_cover_ms"] = round(dt * 1000, 1)
    ok, dt = await until(shown, 8)
    assert ok, "the game did not come back when the toast went"
    await asyncio.sleep(0.3)
    assert x.c.active() == kiosk_focus == E2E["kiosk"], f"uncovered behind the user's back: focus {hex(x.c.active())}"
    stats = await page.ev("GAME_STATS.map((s) => [s.to, s.ms, s.ops.join('+')])")
    covers = [m for t, m, o in stats if t == "covered"]
    shows = [m for t, m, o in stats if t == "shown" and "uncover" in o]
    out["shell_cover_ms_median"] = sorted(covers)[len(covers) // 2] if covers else None
    out["shell_uncover_ms_median"] = sorted(shows)[len(shows) // 2] if shows else None
    E2E["cover"] = out
    return "; ".join(f"{k} {v}" for k, v in out.items()) + f"; screenshot = game colour {col}; process T while covered; keyboard back to the kiosk after the toast"


@test("gw_e2e_minimize_restore_dock", ["R82", "R81", "R68"], group="e2e")
async def t_minimize(ctx):
    """_ on the frame (a real click): the frame hides, the game is minimized (unmapped) and frozen (T), its dock icon
    is the R81 1.33; a real click on the dock icon restores frame + game (mapped, running, focused)"""
    s, desk = ctx["s"], ctx["desk"]
    x, page, xid, pid = desk.x, desk.pages[0], E2E["xid"], E2E["pid"]
    fs = await frame_state(page)
    await page.ev(f"(() => {{ const w = windows.get('{fs['id']}'); w.el.style.left = '300px'; w.el.style.top = '120px'; w.onMove('end'); return 1; }})()")
    await asyncio.sleep(0.4)
    btn = await page.ev(f"(() => {{ const r = windows.get('{fs['id']}').el.querySelector('.win-min').getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; }})()")
    t0 = time.perf_counter()
    x.click(*btn)
    ok, dt = await until(lambda: not (x.geom(xid) or (0,) * 5)[4], 3)
    assert ok, "_ did not minimize the game"
    min_ms = dt * 1000
    await asyncio.sleep(0.2)
    assert proc_state(pid) == "T", proc_state(pid)
    await page.wait('document.querySelector(\'#dock [data-game="neon-bouncer"]\').classList.contains("minimized")', 5)
    x.move(10, 500)
    await asyncio.sleep(0.4)
    t = await page.ev('getComputedStyle(document.querySelector(\'#dock [data-game="neon-bouncer"]\')).transform')
    assert t.startswith("matrix(1.33, 0, 0, 1.33"), t
    E2E["shot_minimized"] = x.shot("game-minimized-in-the-dock.png")
    icon = await page.ev('(() => { const r = document.querySelector(\'#dock [data-game="neon-bouncer"]\').getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; })()')
    x.click(*icon)
    ok, dt = await until(lambda: x.visible(xid, E2E["kiosk"]), 3)
    assert ok, "the dock icon did not restore the game"
    restore_ms = dt * 1000
    await asyncio.sleep(0.3)
    assert proc_state(pid) in ("S", "R") and x.c.active() == xid, (proc_state(pid), hex(x.c.active()))
    fs = await frame_state(page)
    assert fs["state"] == "shown" and not await page.ev(f"windows.get('{fs['id']}').el.classList.contains('minimized')")
    return f"_ -> unmapped in {min_ms:.0f} ms, process T, dock icon scale 1.33; dock click -> mapped in {restore_ms:.0f} ms, running, focused"


@test("gw_e2e_improve_versions_close", ["R82", "R77", "R84", "R86"], group="e2e")
async def t_improve(ctx):
    """✨ on the frame (a real click): the sticky note covers the game (screenshot, frozen); the request 'make the
    background green' goes to the forge with the previous source; the next version passes R77's checks, the old
    process ends (no crash) and v2 takes the SAME frame (green, title · v2); v▾ lists v1 + v2, a click on v1 makes v1
    the game again (revert + restart: red); ✕ ends the game (process gone, window gone, frame gone)"""
    s, desk = ctx["s"], ctx["desk"]
    x, page, pid1 = desk.x, desk.pages[0], E2E["pid"]
    forge = ctx["forge"]
    fs = await frame_state(page)
    imp = await page.ev(f"(() => {{ const r = windows.get('{fs['id']}').el.querySelector('.win-improve').getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; }})()")
    x.click(*imp)
    await page.wait(f"!!windows.get('{fs['id']}').el.querySelector('.sticky-note')", 3, "the sticky note")
    ok, _ = await until(lambda: not x.visible(E2E["xid"], E2E["kiosk"]), 3)
    assert ok, "the ✨ note did not cover the game"
    await asyncio.sleep(0.1)
    assert proc_state(pid1) == "T", proc_state(pid1)
    await page.wait("document.activeElement && document.activeElement.classList.contains('sticky-text')", 3)
    n0 = len(forge.prompts)
    await page.type("make the background green")
    await page.enter()
    t0 = time.perf_counter()
    await page.wait(f"(() => {{ const w = windows.get('{fs['id']}'); return w && w.game.version === 2 && !w.game.busy; }})()", 90, "v2")
    t_imp = time.perf_counter() - t0
    fs2 = await frame_state(page)
    xid2 = fs2["xid"]
    assert fs2["id"] == fs["id"] and fs2["title"] == "🕹 neon bouncer · v2" and fs2["pid"] != pid1 and xid2, fs2
    ok, _ = await until(lambda: x.visible(xid2, E2E["kiosk"]), 5)
    assert ok, "v2 is not on top of the desktop"
    g = x.geom(xid2)
    col = x.area_color(g[0] + 10, g[1] + 510)
    assert near(col, GREEN), f"v2 is not the green version: {col}"
    p = [m for m in forge.prompts[n0:] if "USER IMPROVEMENT REQUEST" in m["user"]]
    assert p and "USER IMPROVEMENT REQUEST: make the background green" in p[0]["user"] and "PREVIOUS VERSION SOURCE (v1)" in p[0]["user"]
    assert proc_state(pid1) in ("", "Z"), "v1 ended when v2 took the frame"
    gdir = s.GAMES_DIR / "neon-bouncer"
    meta = json.loads((gdir / "meta.json").read_text())
    assert meta["version"] == 2 and "last_crash" not in meta, meta
    bx, by = round(fs2["body"][0]), round(fs2["body"][1])
    assert bx <= g[0] and by <= g[1], (g, fs2["body"])
    E2E["shot_v2"] = x.shot("game-improved-v2-in-its-frame.png")
    # v▾: the versions; v1 again
    vb = await page.ev(f"(() => {{ const r = windows.get('{fs['id']}').el.querySelector('.game-ver').getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; }})()")
    assert (await page.ev(f"windows.get('{fs['id']}').el.querySelector('.game-ver').textContent")) == "v2▾"
    x.click(*vb)
    await page.wait(f"windows.get('{fs['id']}').el.querySelectorAll('.game-menu-v').length === 2", 5, "the versions menu")
    items = await page.ev(f"[...windows.get('{fs['id']}').el.querySelectorAll('.game-menu-v')].map((b) => b.textContent)")
    assert items[0].startswith("v2 ●") and "make the background green" in items[0] and items[1].startswith("v1"), items
    ok, _ = await until(lambda: not x.visible(xid2, E2E["kiosk"]), 3)
    assert ok, "the versions menu (over the body) did not cover the game"
    E2E["shot_versions"] = x.shot("game-versions-menu.png")
    v1 = await page.ev(f"(() => {{ const r = windows.get('{fs['id']}').el.querySelector('.game-menu-v[data-v=\"1\"]').getBoundingClientRect(); return [r.left + 30, r.top + r.height / 2]; }})()")
    x.click(*v1)
    await page.wait(f"(() => {{ const w = windows.get('{fs['id']}'); return w && w.game.version === 1 && !w.game.busy && w.game.xid; }})()", 30, "v1 again")
    fs3 = await frame_state(page)
    ok, _ = await until(lambda: x.visible(fs3["xid"], E2E["kiosk"]), 5)
    assert ok, "v1 is not on top of the desktop"
    g3 = x.geom(fs3["xid"])
    assert near(x.area_color(g3[0] + 10, g3[1] + 510), RED), "v1 (red) is not back"
    assert json.loads((gdir / "meta.json").read_text())["version"] == 1 and (gdir / "history" / "v2.py").exists()
    # ✕: the game ends, its window and the frame go
    pid3, xid3 = fs3["pid"], fs3["xid"]
    cl = await page.ev(f"(() => {{ const r = windows.get('{fs['id']}').el.querySelector('.win-close').getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; }})()")
    x.click(*cl)
    ok, dt = await until(lambda: x.geom(xid3) is None and proc_state(pid3) in ("", "Z"), 5)
    assert ok, (x.geom(xid3), proc_state(pid3))
    assert await page.ev("GAME_FRAMES.size") == 0 and not await page.ev('!!document.querySelector(\'#dock [data-game="neon-bouncer"]\')')
    meta = json.loads((gdir / "meta.json").read_text())
    assert "last_crash" not in meta, "✕ is an end, not a crash"
    return (f"✨ note covered the game; 'make the background green' -> v2 (green) in the same frame after {t_imp:.1f} s, "
            f"v1 ended without a crash record; v▾ v2●/v1 -> v1 (red) again; ✕ ended it in {dt * 1000:.0f} ms, frame + icon gone")


@test("gw_e2e_second_screen", ["R82", "R52"], group="e2e2")
async def t_screen(ctx):
    """[R52] two pages side by side on one X display (Xvfb has no RandR monitors: two Chromium --app windows at the
    .xinitrc geometries, 1920x1080+0+0 and 1280x1024+1920+0): a game launched from screen 1's page runs in screen 1's
    frame — its X window lies inside that frame's body on the right-hand screen, transient for screen 1's window —
    and its dock icon is on screen 1's dock, not on screen 0's"""
    s, desk = ctx["s"], ctx["desk"]
    x, p0, p1 = desk.x, desk.pages[0], desk.pages[1]
    where = await p1.ev("[screenX, screenY, outerWidth, outerHeight, SCREEN.index, (outerWidth - innerWidth) / 2, "
                        "outerHeight - innerHeight - (outerWidth - innerWidth) / 2]")
    assert where[:5] == [1920, 0, 1280, 1024, 1], (where, [(hex(w), g) for w, g in x.chrome_windows()])
    cx, cy = where[5], where[6]          # Chromium's own title bar of a plain --app window (the kiosk has none)
    await p1.ev('launchGame({prompt: "second screen bouncer"}).then((r) => { window.__r = r; }, (e) => { window.__e = String(e); }), 1')
    await p1.wait("!!window.__r || !!window.__e", 90, "the game on screen 1")
    assert not await p1.ev("window.__e || null"), await p1.ev("window.__e")
    fs = await frame_state(p1)
    xid = fs["xid"]
    win1 = next(w for w, gg in x.chrome_windows() if gg and gg[0] == 1920)
    ok, _ = await until(lambda: x.visible(xid, win1), 5)
    assert ok, ("the game is not above screen 1's page", x.geom(xid), hex(x.transient(xid) or 0), hex(win1),
                [hex(v) for v in (x.c.prop(x.c.root, "_NET_CLIENT_LIST_STACKING") or [])], s.wm.WM.frames_out(),
                {k: s.wm.WM.frames[k].get(k2) for k in s.wm.WM.frames for k2 in ("at", "chrome", "kiosk", "origin", "scale")})
    g = x.geom(xid)
    bx, by, bw, bh = 1920 + round(cx + fs["body"][0]), round(cy + fs["body"][1]), round(fs["body"][2]), round(fs["body"][3])
    assert g[0] >= 1920 and bx <= g[0] and by <= g[1] and g[0] + g[2] <= bx + bw and g[1] + g[3] <= by + bh, (g, fs["body"])
    assert x.transient(xid) == win1, (hex(x.transient(xid) or 0), hex(win1))
    await p1.wait('!!document.querySelector(\'#dock [data-game="second-screen-bouncer"]\')', 5)
    await asyncio.sleep(0.5)
    assert not await p0.ev('!!document.querySelector(\'#dock [data-game="second-screen-bouncer"]\')'), "screen 0's dock shows it"
    assert await p0.ev("GAME_FRAMES.size") == 0 and await p0.ev("document.querySelectorAll('#taskbar .task-chip').length") == 0
    E2E["shot_screen1"] = x.shot("game-on-the-second-screen.png")
    await p1.ev("closeWin([...GAME_FRAMES.values()][0]), 1")
    ok, _ = await until(lambda: x.geom(xid) is None, 5)
    assert ok
    return (f"screen 1's game at ({g[0]},{g[1]}) inside its frame body ({bx},{by},{bw},{bh}) (page chrome {cx},{cy} px), "
            "transient for screen 1's window; icon on screen 1's dock only")


# ----------------------------------------------------------------------------- runner
async def run_group(tests, size, pages):
    s = kernel()
    ctx = {"s": s}
    try:
        need_x()
    except SkipTest as e:
        for t in tests:
            record(t._name, t._reqs, "skip", str(e))
        return
    with Forge(s) as forge:
        ctx["forge"] = forge
        async with Desk(s, size[0], size[1], pages) as desk:
            ctx["desk"] = desk
            ok = True
            for t in tests:
                if not ok:
                    record(t._name, t._reqs, "skip", "an earlier step of this X session failed")
                    continue
                ok = await run_one(t, ctx)


def main():
    only = [x.strip() for x in os.environ.get("KAIROS_TEST_ONLY", "").split(",") if x.strip()]
    tests = [t for t in TESTS if not only or any(o in t._name for o in only)]
    print(f"kAIrOS games in windows (R82) — {len(tests)} tests", flush=True)
    for t in [t for t in tests if t._group is None]:
        asyncio.run(run_one(t, {}))
    e2e = [t for t in tests if t._group == "e2e"]
    if e2e:
        asyncio.run(run_group(e2e, (1920, 1080), [(0, 0, 0, 1920, 1080, True)]))
    e2e2 = [t for t in tests if t._group == "e2e2"]
    if e2e2:
        asyncio.run(run_group(e2e2, (3200, 1080), [(0, 0, 0, 1920, 1080, False), (1, 1920, 0, 1280, 1024, False)]))
    if not only:
        out = {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "tests": RESULTS, "numbers": {k: v for k, v in E2E.items() if k in ("lag", "cover")}}
        (REPO / "tests" / "results-games-windows.json").write_text(json.dumps(out, indent=1, ensure_ascii=False))
    if E2E.get("lag") or E2E.get("cover"):
        print("numbers:", json.dumps({k: v for k, v in E2E.items() if k in ("lag", "cover")}), flush=True)
    fails = [n for n, r in RESULTS.items() if r["status"] == "fail"]
    passes = sum(1 for r in RESULTS.values() if r["status"] == "pass")
    skips = sum(1 for r in RESULTS.values() if r["status"] == "skip")
    print(f"\n{passes} passed, {len(fails)} failed, {skips} skipped -> tests/results-games-windows.json", flush=True)
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
