#!/usr/bin/env python3
"""kAIrOS v7.5 desktop + web package (docs/V75-DESKTOP-WEB-2026-10-07.md): the pointer [R28/R51],
the clock's system menu [R74], imaginary-web consistency [R75], game exits at the end of a session,
the EFI partition as an automount [R51].

Standalone: `python3 tests/test_desktop_web.py` (run-tests.sh runs it after the suite). Writes
tests/results-desktop-web.json (not for a KAIROS_TEST_ONLY subset).
* pointer / clock-menu statics, ESP fstab + boot-log scripts: no dependencies (sh, awk);
* web + game tests import kernel/server.py in-process (aiohttp needed — the testrig container),
  with a private WEB_DIR and a mocked MM.chat that captures every page prompt;
* desktop_clock_menu_browser drives the REAL shell page in a headless Chromium over the DevTools
  protocol (node 22+ for its WebSocket) against a mock kernel that records /api/hw — nothing ever
  powers off. It skips without Chromium + node; run it in the Playwright image:
    docker run --rm -v <repo>:/w:ro -e KAIROS_TEST_ONLY=desktop_clock_menu_browser \
      -e KAIROS_UI_SHOT=/out/clock-menu.png -v <out>:/out mcr.microsoft.com/playwright:v1.62.1-noble \
      python3 /w/tests/test_desktop_web.py
"""
import asyncio
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
INC = REPO / "iso" / "config" / "includes.chroot"
RESULTS = {}


def record(name, reqs, status, detail=""):
    RESULTS[name] = {"reqs": reqs, "status": status, "detail": detail[:300]}
    mark = {"pass": "✓", "fail": "✗", "skip": "~"}[status]
    print(f"  {mark} {name} [{', '.join(reqs)}] {detail[:140]}", flush=True)


class SkipTest(Exception):
    pass


def test(name, reqs):
    def deco(fn):
        def wrapper(ctx):
            try:
                detail = fn(ctx)
                if asyncio.iscoroutine(detail):
                    detail = asyncio.run(detail)
                record(name, reqs, "pass", detail or "")
            except SkipTest as e:
                record(name, reqs, "skip", str(e))
            except Exception as e:
                import traceback
                tb = traceback.extract_tb(e.__traceback__)
                where = next((f.lineno for f in reversed(tb) if f.filename == __file__), tb[-1].lineno)
                record(name, reqs, "fail", f"{type(e).__name__}: {e} @ {where}")
        wrapper._name, wrapper._reqs, wrapper._test = name, reqs, True
        return wrapper
    return deco


# ----------------------------------------------------------------------------- the kernel, in-process
_K = {}


def kernel():
    """kernel/server.py imported once, with a private VIBEOS_DIR when none is set (standalone run)"""
    if "server" in _K:
        return _K["server"]
    try:
        import aiohttp  # noqa: F401
    except ImportError:
        raise SkipTest("aiohttp not installed (run in the testrig container)")
    if not os.environ.get("VIBEOS_DIR"):
        d = tempfile.mkdtemp(prefix="kairos-dw-")
        os.environ.update(VIBEOS_DIR=d, VIBEOS_MANIFEST=str(REPO / "manifests" / "tier-8gb.json"),
                          VIBEOS_FORCE_OFFLINE="1", KAIROS_LOG_DIR=str(Path(d) / "log"))
    sys.path.insert(0, str(REPO / "kernel"))
    import server
    _K["server"] = server
    return server


# the Oppenheimer pages of the stick run (reconstructed from docs/STICK-RUN-2026-10-07-v73.md: title,
# brand and nav as the owner saw them; the cached files stayed on the stick)
LANDING = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>OPPENHEIMER | Global Destabilization Marketplace</title>
<meta name="description" content="The world's most trusted marketplace for surplus strategic weapons: warheads, delivery systems and fallout logistics at unbeatable prices.">
<style>:root{--bg:#0b0c0a;--ink:#e9e4d4;--accent:#ff6a00;--warn:#c8ff00}
body{background:var(--bg);color:var(--ink);font-family:'Courier New',monospace}
header{border-bottom:2px solid var(--accent)} .logo{font-weight:900;letter-spacing:.3em;color:#ff6a00}
nav a{color:#c8ff00;margin:0 12px}""" + "".join(f".x{i}{{margin:{i}px}}" for i in range(400)) + """</style></head>
<body><header><div class="logo">OPPENHEIMER</div>
<nav><a href="/inventory">INVENTORY</a><a href="/bulk-orders">BULK DEALS</a><a href="/shipping">SHIPPING</a>
<a href="/login">SECURE ACCESS</a><a href="/history">HISTORY</a></nav></header>
<main><h1>Best Nukes For The Cheap</h1><h2>Megatons at kilotons prices</h2>
<p>Every warhead in our inventory is certified, decommissioned with paperwork and ready for discreet delivery anywhere on Earth.</p>
<img alt="a row of gleaming warheads in a desert hangar"></main>
<script>console.log("analytics")</script></body></html>"""

BULK = """<!DOCTYPE html><html><head><title>Bulk Deals | OPPENHEIMER</title></head>
<body><header><div class="logo">OPPENHEIMER</div><nav><a href="/inventory">INVENTORY</a></nav></header>
<main><h1>Bulk Deals</h1><p>Pallets of warheads for the discerning superpower.</p></main></body></html>"""


def page_html(title):
    return (f"<!DOCTYPE html><html><head><title>{title}</title></head><body><header><div class='logo'>X</div>"
            f"</header><main><h1>{title}</h1><p>Some page text that is long enough to be a paragraph here.</p></main></body></html>")


class WebEnv:
    """a private WEB_DIR + a mocked LLM for the page engine: every page prompt is captured"""

    def __init__(self, server, pages):
        self.s, self.pages, self.prompts, self.sources = server, pages, [], []
        self.dir = Path(tempfile.mkdtemp(prefix="kairos-web-"))
        self._saved = {}

    def __enter__(self):
        s = self.s

        async def chat(system, messages, **kw):
            user = messages[-1]["content"]
            m = re.match(r"The browser navigated to: ([^\n]+)", user)
            if m:
                self.prompts.append({"address": m.group(1), "user": user, "system": system})
                return self.pages.get(m.group(1)) or page_html(m.group(1))
            return messages[-1]["content"]          # (a repair pass: unchanged)

        async def no_facts(*a, **kw):
            return "", None

        async def same(text, settings=None):
            return text
        real_source = s.site_context_source

        def spy(address, referer=None):
            r = real_source(address, referer)
            self.sources.append({"address": address, "how": r[0], "from": r[1], "link": r[3]})
            return r
        for name, val in (("WEB_DIR", self.dir), ("english_text", same), ("site_context_source", spy)):
            self._saved[name] = getattr(s, name)
            setattr(s, name, val)
        self._saved["MM.chat"], s.MM.chat = s.MM.chat, chat
        self._saved["KB.context_block"], s.KB.context_block = s.KB.context_block, no_facts
        self._saved["WEB_SERVED"] = dict(s.WEB_SERVED)
        s.WEB_SERVED.clear()
        s.WEB_JOBS.clear()
        return self

    def __exit__(self, *exc):
        s = self.s
        s.MM.chat = self._saved.pop("MM.chat")
        s.KB.context_block = self._saved.pop("KB.context_block")
        s.WEB_SERVED.clear()
        s.WEB_SERVED.update(self._saved.pop("WEB_SERVED"))
        for name, val in self._saved.items():
            setattr(s, name, val)
        shutil.rmtree(self.dir, ignore_errors=True)

    def prompt_for(self, address):
        return next(p["user"] for p in reversed(self.prompts) if p["address"] == address)

    def source_for(self, address):
        return next(p for p in reversed(self.sources) if p["address"] == address)

    async def build(self, address, referer=None):
        slug = self.s.web_slug(address)
        await self.s._web_build(address, slug, referer)
        assert (self.dir / f"{slug}.html").exists(), f"{address} was not written: {self.s.WEB_JOBS.get(slug)}"


def assert_site_block(user, *needles):
    assert "SITE CONTEXT" in user and "Do NOT reinterpret" in user, user[:400]
    block = user.split("SITE CONTEXT", 1)[1]
    assert "<style" not in block and ".x399" not in block, "the <style> blob must not be the context"
    for n in needles:
        assert n in block, f"{n!r} missing from the site block:\n{block[:1500]}"
    return block


# ============================================================================= pointer [R28/R51]
@test("desktop_pointer_swcursor_nocursor", ["R28", "R51", "R79"])
def t_pointer(ctx):
    """X draws the pointer: no -nocursor on the X command line (xfixes ANDs every cursor display
    with it — the root cause of three "no pointer" reports); [R79, v7.5-1] amdgpu = the HARDWARE
    cursor plane (v7.5's SWcursor flickered under Chromium's KMS page flips, stick run 2026-10-07):
    no active OutputClass matches amdgpu, the old section ships as a .disabled fallback (X reads only
    *.conf) with its one-line re-enable; the modesetting drivers (VMs, Intel) keep the software
    cursor (Driver line kept, NVIDIA untouched, AutoAddGPU still off); the pointer evidence in x.log
    after the first kiosk launch."""
    prof = (INC / "home" / "kairos" / ".bash_profile").read_text()
    xi = (INC / "home" / "kairos" / ".xinitrc").read_text()
    starts = [l.strip() for l in prof.splitlines() if l.strip().startswith("startx")]
    assert starts == ["startx -- :0 -background none"], starts
    xdir = INC / "etc" / "X11" / "xorg.conf.d"

    def sections(text):
        code = "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))
        return re.findall(r'Section "OutputClass"(.*?)EndSection', code, re.S)

    def matched(s):
        md = re.search(r'MatchDriver\s+"([^"]+)"', s).group(1).split("|")
        drv = re.search(r'^\s*Driver\s+"([^"]+)"', s, re.M)
        assert drv, f"an OutputClass without a Driver line puts NULL into X's driver list: {s}"
        assert re.search(r'Option\s+"SWcursor"\s+"on"', s), s
        assert "modesetting" not in md and not any(d.startswith("nvidia") for d in md), md
        return drv.group(1), md
    secs = sections((xdir / "20-kairos-swcursor.conf").read_text())
    assert len(secs) == 1, secs
    drv, md = matched(secs[0])
    assert drv == "modesetting" and {"i915", "virtio_gpu"} <= set(md) and "amdgpu" not in md, (drv, md)
    for f in xdir.iterdir():                 # what X reads: no active *.conf may put amdgpu on a software cursor
        if f.name.endswith(".conf"):
            for s in sections(f.read_text()):
                m = re.search(r'MatchDriver\s+"([^"]+)"', s)
                assert not (m and "amdgpu" in m.group(1).split("|") and re.search(r'"SWcursor"\s+"on"', s)), f.name
    fb = xdir / "21-kairos-swcursor-amdgpu.conf.disabled"
    fb_text = fb.read_text()
    fsecs = sections(fb_text)
    assert not fb.name.endswith(".conf") and len(fsecs) == 1 and matched(fsecs[0]) == ("amdgpu", ["amdgpu"]), fsecs
    reenable = f"mv /etc/X11/xorg.conf.d/{fb.name} /etc/X11/xorg.conf.d/{fb.name[:-len('.disabled')]}"
    assert reenable in fb_text and reenable in (xdir / "20-kairos-swcursor.conf").read_text(), "the one-line re-enable"
    single = (INC / "etc" / "X11" / "xorg.conf.d" / "10-kairos-single-gpu.conf").read_text()
    assert re.search(r'Option\s+"AutoAddGPU"\s+"off"', single), "AutoAddGPU must stay off"
    subprocess.run(["sh", "-n", str(INC / "home" / "kairos" / ".xinitrc")], check=True)
    assert "unclutter -idle 2 -root &" in xi
    assert xi.index("xsetroot -cursor_name left_ptr") < xi.index('( sleep 4; pointer_diag ) &'), "diag after the arrow"
    assert '[ -n "$PTR_DIAG" ] || { PTR_DIAG=1;' in xi, "once per session, not per relaunch"
    # the diagnostic, run against an Xorg log like the laptop's with the fallback re-enabled (the lines it
    # adds; with the hardware cursor the diag simply lists none — the x.log evidence the stick run checks)
    fn = re.search(r"^pointer_diag\(\) \{.*?^\}", xi, re.S | re.M).group(0)
    with tempfile.TemporaryDirectory() as td:
        log = Path(td) / ".local" / "share" / "xorg" / "Xorg.0.log"
        log.parent.mkdir(parents=True)
        log.write_text("[   111.678] (II) Applying OutputClass \"kAIrOS software cursor (amdgpu)\" options to /dev/dri/card0\n"
                       "[   111.679] (**) AMDGPU(0): Option \"SWcursor\" \"on\"\n"
                       "[   154.166] (II) XINPUT: Adding extended input device \"Video Bus\" (type: KEYBOARD, id 6)\n"
                       "[   154.175] (II) XINPUT: Adding extended input device \"ELAN07C9:00 04F3:3298 Mouse\" (type: MOUSE, id 9)\n"
                       "[   154.180] (II) XINPUT: Adding extended input device \"ELAN07C9:00 04F3:3298 Touchpad\" (type: TOUCHPAD, id 10)\n")
        (Path(td) / "d.sh").write_text(fn + "\npointer_diag\n")
        env = dict(os.environ, HOME=td, PATH="/usr/bin:/bin")      # no xinput -> the Xorg-log branch
        out = subprocess.run(["sh", str(Path(td) / "d.sh")], env=env, capture_output=True, text=True, timeout=20).stdout
    assert "xinitrc: pointer devices (Xorg log): ELAN07C9:00 04F3:3298 Mouse [MOUSE];ELAN07C9:00 04F3:3298 Touchpad [TOUCHPAD];" in out, out
    assert "Video Bus" not in out and 'Option "SWcursor" "on"' in out and 'OutputClass "kAIrOS software cursor (amdgpu)"' in out, out
    return ("startx without -nocursor; amdgpu = hardware cursor (no active SWcursor match, .disabled fallback + "
            "one-line re-enable); modesetting (i915|xe|virtio_gpu|bochs) keeps Driver + SWcursor; diag lines OK")


# ============================================================================= clock menu [R74]
@test("desktop_clock_menu_static", ["R74", "R52", "R66"])
def t_clock_static(ctx):
    html = (REPO / "shell" / "index.html").read_text()
    js = (REPO / "shell" / "shell.js").read_text()
    css = (REPO / "shell" / "shell.css").read_text()
    menubar = re.search(r'<div id="menubar">(.*?)</div>', html, re.S).group(1)
    assert "mb-install" not in menubar, "the installer left the menubar"
    clock = re.search(r'<button [^>]*id="mb-clock"[^>]*>', html, re.S).group(0)
    for a in ('id="mb-clock" translate="no"', 'aria-haspopup="menu"', 'aria-expanded="false"', 'aria-controls="mb-sysmenu"'):
        assert a in clock, a
    menu = re.search(r'<div id="mb-sysmenu"[^>]*>(.*?)\n  </div>', html, re.S)
    assert menu and 'role="menu"' in menu.group(0), "the menu"
    items = re.findall(r'<button class="sm-item[^"]*"[^>]*role="menuitem"[^>]*>(.*?)</button>', menu.group(1))
    labels = [re.sub(r"<[^>]+>[^<]*</span>", "", i) for i in items]
    assert labels == ["Sleep", "Restart…", "Shut down…", "Install kAIrOS on this computer…", "About kAIrOS"], labels
    for op in ("suspend", "reboot", "off"):
        assert f'data-power="{op}"' in menu.group(1)
    assert re.search(r'id="mb-install"', menu.group(1)) and 'class="sm-item hidden" type="button" role="menuitem" id="mb-install"' in menu.group(1)
    # power: /api/hw with confirm:true, uiConfirm for restart + shut down only, Sleep at once
    pa = re.search(r"async function powerAction\(op\) \{.*?\n\}", js, re.S).group(0)
    assert 'fetch("/api/hw"' in pa and 'JSON.stringify({ device: "power", op, confirm: true })' in pa and "apiHeaders()" in pa
    assert "p.ask && !(await uiConfirm(p.ask, p.label))" in pa
    pw = re.search(r"const POWER = \{.*?\n\};", js, re.S).group(0)
    assert "ask:" not in re.search(r"suspend: \{[^}]*\}", pw).group(0) and pw.count("ask:") == 2, pw
    # keyboard + closing
    for needle in ('e.key === "ArrowDown"', 'e.key === "ArrowUp"', 'e.key === "Home"', 'e.key === "End"',
                   'e.key === "Escape") { e.preventDefault(); e.stopPropagation(); closeSysMenu(true); }',
                   'e.key === "Tab") closeSysMenu()', 'document.addEventListener("pointerdown"',
                   '!SYSMENU.contains(e.target) && !CLOCK.contains(e.target)', 'addEventListener("blur", () => closeSysMenu())',
                   'CLOCK.setAttribute("aria-expanded", "true")', 'CLOCK.setAttribute("aria-expanded", "false")'):
        assert needle in js, needle
    ci = re.search(r"async function checkInstallable\(\) \{.*?\n\}", js, re.S).group(0)
    assert '$("#mb-install").classList.remove("hidden")' in ci and '$("#sm-install-sep").classList.remove("hidden")' in ci
    assert '$("#mb-install").addEventListener("click", openInstaller)' in js
    assert re.search(r"html\.screen-secondary #mb-install", css), "the installer stays on screen 0 only [R52]"
    assert "#mb-sysmenu" in css and ".sm-item:hover, .sm-item:focus-visible" in css and "var(--accent)" in css
    if shutil.which("node"):
        subprocess.run(["node", "--check", str(REPO / "shell" / "shell.js")], check=True, capture_output=True)
    return "clock button (aria) + menu Sleep/Restart…/Shut down…/Install…/About; /api/hw confirm:true; keyboard + outside click" \
        + ("; node --check OK" if shutil.which("node") else "")


def _find_chrome():
    for c in [os.environ.get("KAIROS_CHROME")] + sorted(glob.glob("/ms-playwright/chromium-*/chrome-linux*/chrome")) \
            + [shutil.which(n) for n in ("chromium", "chromium-browser", "google-chrome")]:
        if c and os.path.exists(c):
            return c
    return None


CDP_DRIVER = r"""
const [port, base, shot] = process.argv.slice(2);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let t = null;
for (let i = 0; i < 100 && !t; i++) {
  try { t = await (await fetch(`http://127.0.0.1:${port}/json/new?${base}/?screen=0`, { method: "PUT" })).json(); }
  catch { await sleep(100); }
}
const ws = new WebSocket(t.webSocketDebuggerUrl);
let id = 0; const pending = new Map();
ws.onmessage = (m) => { const d = JSON.parse(m.data); if (d.id && pending.has(d.id)) { pending.get(d.id)(d); pending.delete(d.id); } };
await new Promise((r) => (ws.onopen = r));
const send = (method, params = {}) => new Promise((res, rej) => { const i = ++id;
  pending.set(i, (d) => (d.error ? rej(new Error(method + ": " + JSON.stringify(d.error))) : res(d.result)));
  ws.send(JSON.stringify({ id: i, method, params })); });
const ev = async (expr) => { const r = await send("Runtime.evaluate", { expression: expr, awaitPromise: true, returnByValue: true });
  if (r.exceptionDetails) throw new Error(expr + ": " + JSON.stringify(r.exceptionDetails).slice(0, 300)); return r.result.value; };
const waitFor = async (expr, ms = 8000) => { const t0 = Date.now();
  while (Date.now() - t0 < ms) { if (await ev(expr)) return; await sleep(80); } throw new Error("timeout: " + expr); };
const center = (sel) => ev(`(() => { const r = document.querySelector(${JSON.stringify(sel)}).getBoundingClientRect(); return [r.x + r.width / 2, r.y + r.height / 2]; })()`);
const click = async (x, y) => { for (const type of ["mouseMoved", "mousePressed", "mouseReleased"])
  await send("Input.dispatchMouseEvent", { type, x, y, button: "left", clickCount: 1 }); };
const clickSel = async (sel) => { const [x, y] = await center(sel); await click(x, y); };
const KEYS = { Enter: ["Enter", 13, "\r"], Escape: ["Escape", 27], ArrowDown: ["ArrowDown", 40], ArrowUp: ["ArrowUp", 38],
               Home: ["Home", 36], End: ["End", 35], Space: ["Space", 32, " "] };
const key = async (k) => { const [code, vk, text] = KEYS[k]; const kk = k === "Space" ? " " : k;
  await send("Input.dispatchKeyEvent", Object.assign({ type: "keyDown", key: kk, code, windowsVirtualKeyCode: vk }, text ? { text } : {}));
  await send("Input.dispatchKeyEvent", { type: "keyUp", key: kk, code, windowsVirtualKeyCode: vk }); };
const open = `!document.querySelector("#mb-sysmenu").classList.contains("hidden")`;
const focused = `(document.activeElement.id || document.activeElement.textContent.trim())`;
const steps = []; const ok = (step, cond, detail = "") => steps.push({ step, ok: !!cond, detail: String(detail) });
await send("Page.enable"); await send("Runtime.enable");
await waitFor(`!document.querySelector("#desktop").classList.contains("hidden")`, 15000);
await waitFor(`!document.querySelector("#mb-install").classList.contains("hidden")`);
ok("installer not in the menubar, in the menu", await ev(`!document.querySelector("#menubar #mb-install") && !!document.querySelector("#mb-sysmenu #mb-install")`));
await clickSel("#mb-clock"); await waitFor(open);
ok("click opens", await ev(`document.querySelector("#mb-clock").getAttribute("aria-expanded") === "true"`));
ok("first item focused", (await ev(focused)).includes("Sleep"), await ev(focused));
ok("menu under the clock", await ev(`(() => { const m = document.querySelector("#mb-sysmenu").getBoundingClientRect(), c = document.querySelector("#mb-clock").getBoundingClientRect(); return m.top >= c.bottom && Math.abs(m.right - c.right) < 12; })()`));
if (shot) { await sleep(300); const s = await send("Page.captureScreenshot", { format: "png" }); (await import("node:fs")).writeFileSync(shot, Buffer.from(s.data, "base64")); }
await key("Escape"); await sleep(100);
ok("Escape closes, focus back on the clock", !(await ev(open)) && (await ev(focused)) === "mb-clock", await ev(focused));
await key("Enter"); await waitFor(open);
ok("Enter on the clock opens", true);
await key("ArrowDown"); const f1 = await ev(focused);
await key("ArrowUp"); await key("ArrowUp"); const f2 = await ev(focused);
await key("Home"); const f3 = await ev(focused); await key("End"); const f4 = await ev(focused);
ok("arrows/Home/End move + wrap", f1.includes("Restart") && f2 === "mb-about" && f3.includes("Sleep") && f4 === "mb-about", [f1, f2, f3, f4].join(" | "));
await click(300, 500); await sleep(100);
ok("outside click closes", !(await ev(open)) && (await ev(`document.querySelector("#mb-clock").getAttribute("aria-expanded")`)) === "false");
await clickSel("#mb-clock"); await waitFor(open); await clickSel('[data-power="suspend"]'); await sleep(400);
ok("Sleep: no dialog, menu closed", !(await ev(open)) && !(await ev(`!!document.querySelector(".ui-confirm")`)));
await clickSel("#mb-clock"); await waitFor(open); await clickSel('[data-power="off"]');
await waitFor(`!!document.querySelector(".ui-confirm")`);
const q = await ev(`document.querySelector(".ui-confirm .ui-prompt-q").textContent`);
ok("Shut down asks first (OK arming)", q.startsWith("Shut down this computer now?") && (await ev(`document.querySelector(".ui-confirm-ok").disabled`)), q);
await clickSel(".ui-prompt-cancel"); await sleep(300);
ok("Cancel: dialog gone", !(await ev(`!!document.querySelector(".ui-confirm")`)));
await clickSel("#mb-clock"); await waitFor(open); await clickSel('[data-power="reboot"]');
await waitFor(`!!document.querySelector(".ui-confirm")`); await sleep(900);
await clickSel(".ui-confirm-ok"); await sleep(400);
ok("Restart confirmed", !(await ev(`!!document.querySelector(".ui-confirm")`)));
await clickSel("#mb-clock"); await waitFor(open); await clickSel("#mb-install");
await waitFor(`!document.querySelector("#install").classList.contains("hidden")`);
await waitFor(`document.querySelector("#install-disks").textContent.includes("/dev/nvme0n1")`);
ok("Install opens the installer", !(await ev(open)) && (await ev(`document.querySelector("#install-disks").textContent`)).includes("/dev/nvme0n1"));
await clickSel("#install-actions .btn-ghost"); await sleep(200);
ok("installer closes", await ev(`document.querySelector("#install").classList.contains("hidden")`));
await clickSel("#mb-clock"); await waitFor(open); await clickSel("#mb-about");
await waitFor(`!!document.querySelector(".sm-about")`);
const about = await ev(`document.querySelector(".sm-about").textContent`);
ok("About shows tier + brain", about.includes("8gb") && about.includes("Mock Brain"), about.slice(0, 200));
await key("Escape"); await sleep(150);
ok("About closes on Escape", !(await ev(`!!document.querySelector(".sm-about")`)));
console.log(JSON.stringify(steps));
ws.close(); process.exit(0);
"""


@test("desktop_clock_menu_browser", ["R74"])
def t_clock_browser(ctx):
    """the real shell in headless Chromium: mouse + keyboard on the clock menu; /api/hw is a mock
    that records the calls (Sleep at once, Shut down cancelled = no call, Restart confirmed)"""
    chrome, node = _find_chrome(), shutil.which("node")
    if not chrome or not node:
        raise SkipTest("needs Chromium + node 22+ (the Playwright image) — see the module docstring")
    import http.server
    hw_calls = []
    status = {"tier": "8gb", "state": "idle", "llm": "Mock Brain 26B (test)", "online": False, "knowledge": None,
              "image": "Mock Painter", "video": "Mock Motion", "engine": "cuda"}
    shell = REPO / "shell"

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype="application/json"):
            b = body if isinstance(body, bytes) else body.encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def do_GET(self):
            p = self.path.split("?")[0]
            if p == "/":
                return self._send(200, (shell / "index.html").read_bytes(), "text/html")
            if p.startswith("/shell/"):
                f = (shell / p[7:]).resolve()
                if shell in f.parents and f.is_file():
                    ct = {".js": "text/javascript", ".css": "text/css", ".png": "image/png", ".html": "text/html"}.get(f.suffix, "application/octet-stream")
                    return self._send(200, f.read_bytes(), ct)
            api = {"/api/token": {"token": None}, "/api/settings": {}, "/api/status": status,
                   "/api/setup": {"first_boot": False, "tiers": [], "current_tier": "8gb", "recommended_tier": "8gb"},
                   "/api/install/targets": {"available": True, "targets": [{"dev": "/dev/nvme0n1", "size": "512G", "model": "Mock NVMe"}]}}
            if p in api:
                return self._send(200, json.dumps(api[p]))
            return self._send(404, json.dumps({"error": "not mocked"}))

        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length") or 0) or 0)
            if self.path == "/api/hw":
                hw_calls.append(json.loads(body or b"{}"))
                return self._send(200, json.dumps({"ok": True, "detail": "mock — nothing was powered"}))
            return self._send(404, json.dumps({"error": "not mocked"}))

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    with tempfile.TemporaryDirectory() as td:
        port = 9400 + os.getpid() % 500
        drv = Path(td) / "drv.mjs"
        drv.write_text(CDP_DRIVER)
        br = subprocess.Popen([chrome, "--headless", "--no-sandbox", "--disable-gpu", f"--remote-debugging-port={port}",
                               f"--user-data-dir={td}/profile", "--window-size=1280,800", "--no-first-run", "about:blank"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            r = subprocess.run([node, str(drv), str(port), base, os.environ.get("KAIROS_UI_SHOT", "")],
                               capture_output=True, text=True, timeout=120)
        finally:
            br.kill(); br.wait(); srv.shutdown()
    assert r.returncode == 0, (r.stdout[-500:], r.stderr[-1500:])
    steps = json.loads(r.stdout.strip().splitlines()[-1])
    bad = [s for s in steps if not s["ok"]]
    assert not bad, bad
    ops = [c.get("op") for c in hw_calls]
    assert ops == ["suspend", "reboot"], hw_calls
    assert all(c == {"device": "power", "op": c["op"], "confirm": True} for c in hw_calls), hw_calls
    return f"{len(steps)} UI steps OK in {Path(chrome).parent.name}; /api/hw calls {ops} (Shut down cancelled: none)"


# ============================================================================= web consistency [R75]
@test("web_oppenheimer_referer", ["R75", "R57"])
async def t_web_referer(ctx):
    """the stick run's scenario through the real h_web: the deep landing page first (no root page
    ever), then BULK DEALS with the browser's Referer -> the bulk-orders prompt carries the site's
    brand, premise, nav, look, the link text and the header markup — never the <style> blob"""
    s = kernel()
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer
    landing, bulk = "oppenheimer.com/best-nukes-for-the-cheap", "oppenheimer.com/bulk-orders"
    with WebEnv(s, {landing: LANDING}) as env:
        app = web.Application()
        app.router.add_get("/web/{tail:.*}", s.h_web)
        async with TestClient(TestServer(app)) as c:
            async def visit(addr, **headers):
                slug = s.web_slug(addr)
                for _ in range(200):
                    r = await c.get("/web/" + addr, headers=headers)
                    text = await r.text()
                    if (env.dir / f"{slug}.html").exists() and slug not in s.WEB_JOBS \
                            and "every link is a prompt" not in text:          # not the loading page
                        return text
                    await asyncio.sleep(0.05)
                raise AssertionError(f"{addr} never served")
            first = await visit(landing)
            assert "OPPENHEIMER" in first and env.source_for(landing)["how"] is None, env.sources
            s.WEB_SERVED.clear()                      # only the Referer may supply the context now
            await visit(bulk, Referer=f"http://localhost:1337/web/{landing}")
    src = env.source_for(bulk)
    assert src["how"] == "referer" and src["from"] == landing and src["link"] == "BULK DEALS", src
    block = assert_site_block(env.prompt_for(bulk), "OPPENHEIMER", "Global Destabilization Marketplace",
                              "INVENTORY -> /web/oppenheimer.com/inventory", "BULK DEALS -> /web/oppenheimer.com/bulk-orders",
                              "SHIPPING", "SECURE ACCESS", "HISTORY", "surplus strategic weapons", "--accent: #ff6a00",
                              "clicking the link “BULK DEALS”", '<div class="logo">OPPENHEIMER</div>', "Best Nukes For The Cheap")
    assert block.index("Site name / brand (use exactly): OPPENHEIMER") < block.index("Header/navigation markup")
    return f"bulk-orders prompt: brand, premise, nav ×5, palette, link “BULK DEALS”, header markup ({len(block)} chars)"


@test("web_no_referer_fallbacks", ["R75"])
async def t_web_fallbacks(ctx):
    """Chromium sends no Referer from the sandboxed /web/ iframes (measured): the page of the site
    shown last that links here; with nothing shown, a cached page of the site linking here; then
    the root page; then the newest page — and an older cache without a profile gets one"""
    s = kernel()
    landing, bulk = "oppenheimer.com/best-nukes-for-the-cheap", "oppenheimer.com/bulk-orders"
    with WebEnv(s, {landing: LANDING}) as env:
        await env.build(landing)
        s.note_web_served(landing)                    # what h_web does when it serves the page
        await env.build(bulk)                         # no Referer
        a = env.source_for(bulk)
        assert a["how"] == "last shown, links here" and a["from"] == landing and a["link"] == "BULK DEALS", a
        assert_site_block(env.prompt_for(bulk), "OPPENHEIMER", "SECURE ACCESS", "clicking the link “BULK DEALS”")
        s.WEB_SERVED.clear()                          # nothing shown (a vibe.browse app, a kernel restart)
        await env.build("oppenheimer.com/shipping")
        b = env.source_for("oppenheimer.com/shipping")
        assert b["how"] == "cached page linking here" and b["from"] == landing and b["link"] == "SHIPPING", b
        await env.build("oppenheimer.com/careers")      # linked from nowhere: the newest page, the profile
        c = env.source_for("oppenheimer.com/careers")
        assert c["how"] == "newest page of the site" and c["from"] in (bulk, "oppenheimer.com/shipping"), c
        assert_site_block(env.prompt_for("oppenheimer.com/careers"), "OPPENHEIMER", "Global Destabilization Marketplace")
    # an older cache (no profile, no index): the root page, then any page found by its cache name
    with WebEnv(s, {}) as env:
        (env.dir / f"{s.web_slug('acme.io')}.html").write_text(LANDING.replace("OPPENHEIMER", "ACME"))
        await env.build("acme.io/contact")
        d = env.source_for("acme.io/contact")
        assert d["how"] == "root page" and d["from"] == "acme.io", d
        assert_site_block(env.prompt_for("acme.io/contact"), "ACME", "SECURE ACCESS")
    with WebEnv(s, {}) as env:
        (env.dir / f"{s.web_slug('acme.io/products')}.html").write_text(LANDING.replace("OPPENHEIMER", "ACME"))
        await env.build("acme.io/contact")
        e = env.source_for("acme.io/contact")
        assert e["how"] == "newest page of the site" and e["from"] is None, e
        assert_site_block(env.prompt_for("acme.io/contact"), "ACME")
    with WebEnv(s, {}) as env:                         # a site never seen: no site block at all
        await env.build("fresh.example/page")
        assert "SITE CONTEXT" not in env.prompt_for("fresh.example/page")
        await env.build("best nukes for the cheap")    # search queries are not one site by their slug prefix
        await env.build("best nukes")
        assert "SITE CONTEXT" not in env.prompt_for("best nukes"), env.source_for("best nukes")
    return "shown+links (BULK DEALS) -> cached sibling linking here (SHIPPING) -> newest; legacy: root, slug-found page; new site: none"


@test("web_site_profile_written", ["R75"])
async def t_web_profile(ctx):
    s = kernel()
    landing = "oppenheimer.com/best-nukes-for-the-cheap"
    with WebEnv(s, {landing: LANDING, "oppenheimer.com/bulk-orders": BULK}) as env:
        await env.build(landing)
        p = env.dir / f"{s.web_slug('oppenheimer.com')}.site.json"
        prof = json.loads(p.read_text())
        assert prof["brand"] == "OPPENHEIMER" and prof["title"] == "OPPENHEIMER | Global Destabilization Marketplace", prof
        assert [n["text"] for n in prof["nav"]] == ["INVENTORY", "BULK DEALS", "SHIPPING", "SECURE ACCESS", "HISTORY"], prof["nav"]
        assert prof["nav"][1]["href"] == "/web/oppenheimer.com/bulk-orders", prof["nav"]
        assert "surplus strategic weapons" in prof["premise"] and prof["headings"][:2] == ["Best Nukes For The Cheap", "Megatons at kilotons prices"]
        assert prof["palette"]["vars"]["--accent"] == "#ff6a00" and "#ff6a00" in prof["palette"]["colours"], prof["palette"]
        assert prof["fonts"] and "Courier New" in prof["fonts"][0] and prof["founded_by"] == landing
        assert [x["address"] for x in prof["pages"]] == [landing]
        await env.build("oppenheimer.com/bulk-orders")
        prof2 = json.loads(p.read_text())
        assert prof2["brand"] == "OPPENHEIMER" and prof2["founded_by"] == landing and len(prof2["nav"]) == 5, "anchored on the first page"
        assert [x["address"] for x in prof2["pages"]] == [landing, "oppenheimer.com/bulk-orders"]
        assert "Other pages of this site: oppenheimer.com/best-nukes-for-the-cheap" in env.prompt_for("oppenheimer.com/bulk-orders")
        assert not list(env.dir.glob("*.tmp")), "atomic write"
    return f"{p.name}: brand, title, headlines, premise, nav ×5, palette (4 vars, colours), font, pages; anchored on the first page"


@test("web_prompt_budget", ["R75", "R11"])
async def t_web_budget(ctx):
    """the site block is compact and bounded: ≤ SITE_CTX_CAP and ≤ half the prompt budget, the whole
    page prompt ≤ prompt_char_budget — also with a monstrous header and a tiny budget"""
    s = kernel()
    landing = "oppenheimer.com/best-nukes-for-the-cheap"
    huge = LANDING.replace("<nav>", "<nav>" + "".join(f'<a href="/p{i}">ITEM {i}</a>' for i in range(600)), 1)
    real_budget = s.prompt_char_budget
    with WebEnv(s, {landing: huge}) as env:
        await env.build(landing)
        await env.build("oppenheimer.com/bulk-orders")
        full = env.prompt_for("oppenheimer.com/bulk-orders")
        block = full.split("\n\n", 1)[1]
        budget = real_budget(s.prompts.BROWSER_SYSTEM)
        assert len(block) <= s.SITE_CTX_CAP and len(full) <= budget, (len(block), len(full), budget)
        prof = json.loads((env.dir / f"{s.web_slug('oppenheimer.com')}.site.json").read_text())
        assert len(prof["nav"]) == 12, "nav capped"
        try:
            s.prompt_char_budget = lambda system: 4000
            await env.build("oppenheimer.com/shipping")
        finally:
            s.prompt_char_budget = real_budget
        small = env.prompt_for("oppenheimer.com/shipping")
        assert len(small) <= 4000 and len(small.split("\n\n", 1)[1]) <= 2000 and "OPPENHEIMER" in small, len(small)
    return f"block {len(block)} ≤ {s.SITE_CTX_CAP}, prompt {len(full)} ≤ {budget}; tiny budget: prompt {len(small)} ≤ 4000"


@test("web_browse_same_site", ["R75", "R57"])
async def t_web_browse(ctx):
    """vibe.browse (h_browse) uses the same engine: profile written, an optional "from", and the
    page the app was shown last as the source"""
    s = kernel()
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer
    landing = "oppenheimer.com/best-nukes-for-the-cheap"
    with WebEnv(s, {landing: LANDING}) as env:
        app = web.Application()
        app.router.add_post("/api/browse", s.h_browse)
        async with TestClient(TestServer(app)) as c:
            r = await (await c.post("/api/browse", json={"address": landing})).json()
            assert r["cached"] is False and "OPPENHEIMER" in r["html"]
            assert (env.dir / f"{s.web_slug('oppenheimer.com')}.site.json").exists(), "profile after vibe.browse"
            await (await c.post("/api/browse", json={"address": "oppenheimer.com/bulk-orders"})).json()
            assert env.source_for("oppenheimer.com/bulk-orders")["how"] == "last shown, links here"
            s.WEB_SERVED.clear()
            await (await c.post("/api/browse", json={"address": "oppenheimer.com/history", "from": landing})).json()
            h = env.source_for("oppenheimer.com/history")
            assert h["how"] == "referer" and h["link"] == "HISTORY", h
            assert_site_block(env.prompt_for("oppenheimer.com/history"), "OPPENHEIMER", "clicking the link “HISTORY”")
    return "vibe.browse: profile written; last shown page links here; explicit from -> referer path"


@test("web_adult_rule_page_prompt", ["R57", "R86"])
async def t_web_adult_rule(ctx):
    """[SD run 2026-10-13] the page prompt the model gets: BROWSER_SYSTEM with the 18+ rule (keep the brand + theme,
    fictional adults 25+, never minors, never real people, SFW stays SFW) for an adult address AND a SFW one (the rule
    is conditional, the system prompt is the same); the address reaches the model verbatim; an uncensored primary still
    gets the S5 limits line after it"""
    s = kernel()
    P = s.prompts
    adult, sfw = "xhamster.com blonde woman with elven ears in the forest", "bakery-berlin.de - a family bakery"
    with WebEnv(s, {}) as env:
        await env.build(adult)
        await env.build(sfw)
        sys_a = next(p["system"] for p in env.prompts if p["address"] == adult)
        sys_s = next(p["system"] for p in env.prompts if p["address"] == sfw)
        assert P.ADULT_RULE_WEB in sys_a and sys_a == sys_s, "one conditional rule, the same prompt for both"
        assert env.prompt_for(adult).startswith(f"The browser navigated to: {adult}"), env.prompt_for(adult)[:120]
    final = s.uncensored_system(sys_a, "llm")
    assert s.MANIFEST["llm"].get("uncensored") and final.endswith(P.UNCENSORED_LIMITS) and P.ADULT_RULE_WEB in final
    return "BROWSER_SYSTEM carries the 18+ rule for both pages; address verbatim; S5 limits line still last"


# ============================================================================= late pictures [SD run 2026-10-13]
def _png(w, h, shade=128):
    """a real w×h grayscale PNG (what the mock painter writes)"""
    import struct
    import zlib
    raw = b"".join(b"\x00" + bytes([shade]) * w for _ in range(h))
    chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 1)) + chunk(b"IEND", b""))


class LatePictures:
    """kernel/server.py's /genimg with a SLOW mock painter (render_images patched: sleeps `delay` s per batch, then writes
    real PNGs), a short GENIMG_WAIT, a small GENIMG_LIMIT and a private FILES_DIR; every request is counted"""

    def __init__(self, server, wait=0.6, delay=1.5, limit=1, window=0.1):
        self.s, self.wait, self.delay, self.limit, self.window = server, wait, delay, limit, window
        self.dir = Path(tempfile.mkdtemp(prefix="kairos-late-"))
        self.renders, self.fail, self.saved = [], set(), {}

    def __enter__(self):
        s = self

        async def fake_render(kind, items, adult=False, expand=None):
            s.renders.append([it["prompt"] for it in items])
            await asyncio.sleep(s.delay)
            out = []
            for it in items:
                if it["prompt"] in s.fail:
                    out.append({"ok": False, "error": "mock painter failure"})
                    continue
                Path(it["out"]).parent.mkdir(parents=True, exist_ok=True)
                Path(it["out"]).write_bytes(_png(it["w"], it["h"]))
                out.append({"ok": True, "prompt": it["prompt"], "expanded": False})
            return out
        for name, val in (("render_images", fake_render), ("GENIMG_WAIT", self.wait), ("GENIMG_LIMIT", self.limit),
                          ("GENIMG_WINDOW", self.window), ("FILES_DIR", self.dir)):
            self.saved[name] = getattr(self.s, name)
            setattr(self.s, name, val)
        for d in (self.s.GENIMG_INFLIGHT, self.s.GENIMG_FAILED, self.s.GENIMG_LATE):
            d.clear()
        self.s.GENIMG_PENDING.clear()
        return self

    def __exit__(self, *exc):
        for name, val in self.saved.items():
            setattr(self.s, name, val)
        for d in (self.s.GENIMG_INFLIGHT, self.s.GENIMG_FAILED, self.s.GENIMG_LATE):
            d.clear()
        self.s.GENIMG_PENDING.clear()
        shutil.rmtree(self.dir, ignore_errors=True)


def _svg_width(body: bytes):
    m = re.search(rb"<svg[^>]*\swidth='(\d+)'", body)
    return int(m.group(1)) if m else None


def _png_size(body: bytes):
    import struct
    return struct.unpack(">II", body[16:24]) if body[:8] == b"\x89PNG\r\n\x1a\n" else None


@test("web_genimg_late_picture", ["R58", "R57"])
async def t_genimg_late(ctx):
    """the owner's xhamster picture (rendered ~7 min after its request; the request had given up at GENIMG_WAIT and the
    page never asked again): a picture still rendering when the wait ends — or turned away at GENIMG_LIMIT — gets the
    PENDING placeholder (odd width, no-store); a re-ask (?_r=n) is answered at once and queues a turned-away picture
    once a slot is free; the finished picture reaches the re-ask; a failed one gets the FINAL placeholder (even width)
    and no render loop; every served page carries the late-picture script, the one block of the shell's BRIDGE_SRC"""
    import contextlib
    import io
    s = kernel()
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer
    log = io.StringIO()
    with LatePictures(s, wait=0.6, delay=1.5, limit=1) as lp, contextlib.redirect_stdout(log):
        app = web.Application()
        app.router.add_get("/genimg/{tail:.*}", s.h_genimg)
        async with TestClient(TestServer(app)) as c:
            async def get(path):
                t0 = time.time()
                r = await c.get(path)
                return r.status, r.headers.get("Content-Type", ""), r.headers.get("Cache-Control", ""), await r.read(), time.time() - t0

            # 1. the wait runs out before the slow painter is done: the PENDING placeholder, one pixel wider (513)
            st, ct, cc, body, dt = await get("/genimg/a red fox on a cliff")
            assert st == 200 and "svg" in ct and _svg_width(body) == 513 and "no-store" in cc, (st, ct, cc, body[:120])
            assert 0.5 <= dt < 1.4, f"the first request holds GENIMG_WAIT (0.6 s): {dt:.2f}"
            # 2. a re-ask is answered at once (never a held connection) and never starts a second render
            st, ct, cc, body, dt = await get("/genimg/a red fox on a cliff?_r=1")
            assert _svg_width(body) == 513 and dt < 0.4 and lp.renders == [["a red fox on a cliff"]], (dt, lp.renders)
            # 3. the queue is full (GENIMG_LIMIT 1): a second picture is turned away AT ONCE — pending, not queued
            st, ct, cc, body, dt = await get("/genimg/a blue whale?w=512&h=384")
            assert _svg_width(body) == 513 and dt < 0.4 and len(lp.renders) == 1, (dt, lp.renders)
            # 4. the fox is painted: the next re-ask gets the real picture
            for _ in range(60):
                if not s.GENIMG_INFLIGHT:
                    break
                await asyncio.sleep(0.1)
            st, ct, cc, body, dt = await get("/genimg/a red fox on a cliff?_r=2")
            assert st == 200 and _png_size(body) == (512, 512), (st, ct, body[:40])
            # 5. the whale's re-ask finds a free slot: queued now, answered pending at once, then painted
            st, ct, cc, body, dt = await get("/genimg/a blue whale?w=512&h=384&_r=1")
            assert _svg_width(body) == 513 and dt < 0.4, dt
            for _ in range(60):
                if not s.GENIMG_INFLIGHT:
                    break
                await asyncio.sleep(0.1)
            st, ct, cc, body, dt = await get("/genimg/a blue whale?w=512&h=384&_r=2")
            assert _png_size(body) == (512, 384) and lp.renders[-1] == ["a blue whale"], (lp.renders, body[:40])
            # 6. a failed render: the first request's wait ends pending, the re-ask after the failure gets the FINAL
            #    placeholder (even width) and no new render; a fresh page load (no _r) tries again, as before
            lp.fail.add("a broken vase")
            st, ct, cc, body, dt = await get("/genimg/a broken vase")
            assert _svg_width(body) == 513, body[:120]
            for _ in range(60):
                if not s.GENIMG_INFLIGHT:
                    break
                await asyncio.sleep(0.1)
            n = len(lp.renders)
            st, ct, cc, body, dt = await get("/genimg/a broken vase?_r=3")
            assert _svg_width(body) == 512 and dt < 0.4 and len(lp.renders) == n, (body[:120], lp.renders)
            lp.wait = s.GENIMG_WAIT = 3.0
            st, ct, cc, body, dt = await get("/genimg/a broken vase")
            assert _svg_width(body) == 512 and len(lp.renders) == n + 1, "a fresh request renders again; its failure is final"
            # 7. a picture painted within the wait arrives in the first answer, as before (no placeholder at all)
            st, ct, cc, body, dt = await get("/genimg/a quiet lake")
            assert _png_size(body) == (512, 512) and 1.4 <= dt < 3.0, dt
    out = log.getvalue()
    assert "still rendering after 1 s" in out and "queue full (1 pictures in flight)" in out, out[-800:]
    assert out.count("a late picture reached its page") == 2, out[-800:]      # the fox and the whale, once each
    # every served page carries the script — the ONE block of the shell's BRIDGE_SRC (single source)
    js = (REPO / "shell" / "shell.js").read_text()
    bridge = re.search(r"const BRIDGE_SRC = `(.*?)`;\n", js, re.S).group(1).replace("<\\/script>", "</script>")
    block = re.search(r"<script data-kairos-genimg>.*?</script>", bridge, re.S).group(0)
    assert s.genimg_late_script() == block and block in s.bridge_src()
    served = s.no_context_menu("<!DOCTYPE html><html><head><title>x</title></head><body><img src='/genimg/x'></body></html>")
    assert served.count("data-kairos-genimg") == 1 and served.index("data-kairos-genimg") < served.index("<title>")
    assert "naturalWidth % 2 === 1" in block and '"_r"' in block and "isConnected" in block and "3600000" in block
    return ("pending placeholder (513 px, no-store) after the 0.6 s wait; re-ask at once, no second render; queue-full "
            "turned away then queued by the re-ask; both late pictures delivered (2 journal lines); failure final (512 px), "
            "no loop; in-wait picture unchanged; served pages carry the BRIDGE_SRC block")


async def _cdp(port, url, expr, until, timeout=150, every=0.5):
    """open `url` in the headless Chromium on `port` and evaluate `expr` (a JSON string) every `every` s until
    until(value) is true -> the list of decoded values"""
    import aiohttp
    async with aiohttp.ClientSession() as cs:
        ver = None
        for _ in range(200):
            try:
                async with cs.get(f"http://127.0.0.1:{port}/json/version") as r:
                    ver = await r.json()
                break
            except Exception:
                await asyncio.sleep(0.1)
        assert ver, "Chromium's DevTools endpoint never answered"
        async with cs.put(f"http://127.0.0.1:{port}/json/new?{url}") as r:
            target = await r.json()
        ws = await cs.ws_connect(target["webSocketDebuggerUrl"], max_msg_size=0)
        mid, seen, t0 = 0, [], time.time()
        try:
            while time.time() - t0 < timeout:
                mid += 1
                await ws.send_json({"id": mid, "method": "Runtime.evaluate",
                                    "params": {"expression": expr, "returnByValue": True}})
                while True:
                    msg = await ws.receive_json(timeout=30)
                    if msg.get("id") == mid:
                        break
                v = (msg.get("result") or {}).get("result", {}).get("value")
                if isinstance(v, str):
                    v = json.loads(v)
                    seen.append(v)
                    if until(v):
                        return seen
                await asyncio.sleep(every)
        finally:
            await ws.close()
    raise AssertionError(f"timeout after {timeout} s; last state: {json.dumps(seen[-1] if seen else None)[:1500]}")


REPORTER_JS = """
function kReport(tag) {
  const born = performance.timeOrigin, probes = {};
  const probe = (u) => { if (!(u in probes)) { probes[u] = -1; const im = new Image(); im.onload = () => { probes[u] = im.naturalWidth; }; im.onerror = () => { probes[u] = 0; }; im.src = u; } return probes[u]; };
  setInterval(() => {
    const out = { rep: tag, born, imgs: [], bgs: [] };
    for (const i of document.querySelectorAll("img")) out.imgs.push({ alt: i.alt, src: i.getAttribute("src"), w: i.complete ? i.naturalWidth : -1 });
    for (const el of document.querySelectorAll("[data-bg]")) {
      const p = getComputedStyle(el).backgroundImage.split('url("')[1];
      const u = p ? p.slice(0, p.indexOf('"')) : "";
      out.bgs.push({ id: el.getAttribute("data-bg"), url: u, w: u ? probe(u) : -1 });
    }
    if (window.parent !== window) parent.postMessage(out, "*"); else window.R[tag] = out;
  }, 250);
}
"""


@test("web_genimg_late_browser", ["R58", "R57", "R60", "R68"])
async def t_genimg_late_browser(ctx):
    """the late-picture script in a REAL headless Chromium, every place a picture can be: a /web/ page (the real h_web,
    a cached page in a sandboxed iframe — an <img> and a CSS background), an app (a sandboxed srcdoc iframe with the
    shell's real BRIDGE_SRC — an <img>, and vibe.browse()'s html from the real h_browse put in with innerHTML: an <img>
    and a <style> background) and the shell's wallpaper helper (applyWallpaper/lateWallpaper lifted from shell.js).
    GENIMG_WAIT 1 s, a 2 s mock painter, GENIMG_LIMIT 2: every picture first shows the pending placeholder (odd width),
    then the real picture arrives with no reload (each frame's timeOrigin unchanged, h_web asked once)"""
    chrome = _find_chrome()
    if not chrome:
        raise SkipTest("needs Chromium (kairos-fix-chromium:local) — the protocol itself: web_genimg_late_picture")
    s = kernel()
    from aiohttp import web
    from aiohttp.test_utils import TestServer
    hits = {"web": 0, "genimg": []}
    page_addr, browse_addr = "late-pictures.kai", "late-browse.kai"
    page = ("<!DOCTYPE html><html><head><meta charset='utf-8'><title>Late pictures</title><style>"
            ".hero{width:240px;height:90px;background:linear-gradient(rgba(0,0,0,.4),rgba(0,0,0,.4)),"
            "url('/genimg/page hero background')}</style></head><body><div class='hero' data-bg='page-bg'></div>"
            "<img alt='page picture' src='/genimg/page picture' style='width:120px'>"
            "<script>" + REPORTER_JS + "kReport('page');</script></body></html>")
    browse = ("<!DOCTYPE html><html><head><meta charset='utf-8'><title>Browse</title><style>.b{width:200px;height:80px;"
              "background:url('/genimg/browse background')}</style></head><body><div class='b' data-bg='browse-bg'></div>"
              "<img alt='browse picture' src='/genimg/browse picture' style='width:100px'></body></html>")
    wall = re.search(r"let wallpaperShown = null;\n.*?\n}\nfunction lateWallpaper\(.*?\n}\n", (REPO / "shell" / "shell.js").read_text(), re.S)
    assert wall, "applyWallpaper / lateWallpaper not found in shell.js"
    with WebEnv(s, {}) as env, LatePictures(s, wait=1.0, delay=2.0, limit=2, window=0.2) as lp:
        env_dir_page = env.dir / f"{s.web_slug(page_addr)}.html"
        env_dir_page.write_text(page)
        (env.dir / f"{s.web_slug(browse_addr)}.html").write_text(browse)

        async def h_web(request):
            hits["web"] += 1
            return await s.h_web(request)

        async def h_genimg(request):
            hits["genimg"].append(request.path_qs)
            return await s.h_genimg(request)
        state = {"top": ""}

        async def h_top(request):
            return web.Response(text=state["top"], content_type="text/html")
        app = web.Application()
        app.router.add_get("/web/{tail:.*}", h_web)
        app.router.add_get("/genimg/{tail:.*}", h_genimg)
        app.router.add_post("/api/browse", s.h_browse)
        app.router.add_get("/t", h_top)
        server = TestServer(app, host="127.0.0.1")
        await server.start_server()
        try:
            base = f"http://127.0.0.1:{server.port}"
            import aiohttp
            async with aiohttp.ClientSession() as cs:
                async with cs.post(base + "/api/browse", json={"address": browse_addr}) as r:
                    browsed = (await r.json())["html"]
            assert browsed.count("data-kairos-genimg") == 1, "vibe.browse html carries the script (inert under innerHTML)"
            app_html = ("<!DOCTYPE html><html><head>" + s.bridge_src() + "<title>app</title></head><body>"
                        "<img alt='app picture' src='/genimg/app picture?w=512&h=384' style='width:100px'>"
                        "<div id='browse'></div><script>" + REPORTER_JS + "kReport('app');"
                        "document.getElementById('browse').innerHTML = " + json.dumps(browsed).replace("</", "<\\/")
                        + ";</script></body></html>")
            state["top"] = ("<!DOCTYPE html><html><head><meta charset='utf-8'></head><body>"
                   "<div id='wallpaper' data-bg='wallpaper' style='width:64px;height:64px'></div>"
                   "<img id='gone' alt='gone' src='/genimg/a picture whose element goes away' style='width:40px'>"
                   f"<iframe sandbox='allow-scripts allow-forms' src='/web/{page_addr}'></iframe>"
                   "<iframe id='app' sandbox='allow-scripts allow-pointer-lock'></iframe><script>"
                   "window.R = {}; addEventListener('message', (e) => { if (e.data && e.data.rep) R[e.data.rep] = e.data; });"
                   "document.getElementById('app').srcdoc = " + json.dumps(app_html).replace("</", "<\\/") + ";"
                   "const $ = (q) => document.querySelector(q);" + wall.group(0) + REPORTER_JS +
                   "applyWallpaper('/genimg/wallpaper picture'); kReport('top');"
                   # removed while pending: its re-ask timer finds it disconnected and stops
                   "const g = $('#gone'); g.addEventListener('load', () => { if (g.naturalWidth % 2) g.remove(); });"
                   "</script></body></html>")
            hist = {}

            def done(v):
                for tag, rep in v.items():
                    for it in rep.get("imgs", []) + rep.get("bgs", []):
                        key = f"{tag}:{it.get('alt') or it.get('id')}"
                        hist.setdefault(key, []).append(it)
                    hist.setdefault(f"born:{tag}", set()).add(rep.get("born"))
                pics = [k for k in hist if not k.startswith("born:")]
                pics = [k for k in pics if k != "top:gone"]  # the removed <img> is checked by its requests
                return len(pics) == 6 and all(hist[k][-1]["w"] == 512 and "_r=" in (hist[k][-1].get("src") or hist[k][-1].get("url") or "")
                                              for k in pics)
            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:   # Chromium may still write its profile
                port = 9900 + os.getpid() % 90
                br = subprocess.Popen([chrome, "--headless=new", "--no-sandbox", "--disable-gpu", "--no-first-run",
                                       "--no-default-browser-check", f"--remote-debugging-port={port}",
                                       f"--user-data-dir={td}/profile", "--window-size=900,700", "about:blank"],
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                t0 = time.time()
                try:
                    await _cdp(port, base + "/t", "JSON.stringify(window.R || {})", done, timeout=150)
                finally:
                    br.kill()
                    br.wait()
                took = time.time() - t0
        finally:
            await server.close()
        page_after = env_dir_page.read_text()
    pics = sorted(k for k in hist if not k.startswith("born:") and k != "top:gone")
    assert pics == ["app:app picture", "app:browse picture", "app:browse-bg", "page:page picture", "page:page-bg",
                    "top:wallpaper"], pics
    for k in pics:                                   # placeholder first (odd width), the picture last
        ws = [it["w"] for it in hist[k]]
        if "src" in hist[k][-1]:                     # an <img>: the element itself showed the placeholder
            assert any(w > 0 and w % 2 == 1 for w in ws), f"{k}: never showed the pending placeholder: {ws[:40]}"
        # a CSS background: the script swaps the URL only after a pending answer (n >= 1) -> the final URL carries _r=
        assert ws[-1] == 512 and "_r=" in (hist[k][-1].get("src") or hist[k][-1].get("url")), (k, ws[-12:], hist[k][-1])
    for tag in ("page", "app", "top"):               # no reload: one document per frame the whole time
        assert len(hist[f"born:{tag}"]) == 1, (tag, hist[f"born:{tag}"])
    assert hits["web"] == 1, f"the /web/ page was requested {hits['web']}× — a reload"
    gone = [q for q in hits["genimg"] if "goes%20away" in q or "goes away" in q]
    assert len(gone) == 1, f"a removed <img> must not be asked again: {gone}"
    assert page_after == page, "the cached page file is untouched (injection at serve time only)"
    per = {}
    for q in hits["genimg"]:
        key = q.split("?")[0]
        per[key] = per.get(key, 0) + 1
    assert max(per.values()) <= 16, f"hammering: {per}"
    return (f"6 pictures (page <img> + CSS bg, app <img>, vibe.browse innerHTML <img> + <style> bg, wallpaper): pending "
            f"placeholder first, then the picture, no reload, cached file untouched; a removed <img> asked once; {took:.0f} s; "
            f"{len(hits['genimg'])} /genimg requests, max {max(per.values())} per picture; {Path(chrome).name}")


# ============================================================================= game exits at session end
def _fake_x(path, answer=True):
    """an X-like unix socket: reads the 12-byte setup and answers 8 bytes (Failed: no auth) or stays silent"""
    async def handle(r, w):
        await r.read(12)
        if answer:
            w.write(b"\x00\x40\x0b\x00\x00\x00\x10\x00")
            await w.drain()
        else:
            await asyncio.sleep(5)
        w.close()
    return asyncio.start_unix_server(handle, path=path)


class _Proc:
    def __init__(self, pid, rc):
        self.pid, self.returncode = pid, rc

    async def wait(self):
        return self.returncode


@test("game_exit_with_the_session", ["R45", "R68"])
async def t_game_session(ctx):
    """stick run 2026-10-07: at power-off X went away and the game died ("XIO: fatal IO error 11 …
    after 17643 requests", exit 1) — recorded as a crash with a bug report. Now: kernel or system
    stopping, or X gone under a mapped game = ended with the session (no record); X still answering
    = the game's own crash (recorded)"""
    s = kernel()
    xio = ('XIO:  fatal IO error 11 (Resource temporarily unavailable) on X server ":0"\n'
           "      after 17643 requests (17643 known processed) with 8 events remaining.\n")
    saved = (s.wm.WM.display, s.system_stopping, s.SHUTTING_DOWN, s.X_GONE_GRACE)
    with tempfile.TemporaryDirectory() as td:
        sock = Path(td) / "X0"
        s.wm.WM.display = lambda: (":0", sock)
        stopping = {"v": False}

        async def fake_stopping():
            return stopping["v"]
        s.system_stopping, s.X_GONE_GRACE = fake_stopping, 1.0
        try:
            # the probe: a live X-like server answers, a dead socket file / none does not
            assert not await s.x_display_answers()
            srv = await _fake_x(str(sock))
            assert await s.x_display_answers()
            assert await s.game_ended_with_session(xio, "mapped") == "", "XIO while X answers: the game's own crash"
            assert await s.game_ended_with_session("Traceback…\nZeroDivisionError", "mapped") == ""
            srv.close(); await srv.wait_closed()
            sock.unlink(missing_ok=True)
            assert "the X server went away (its X connection broke)" == await s.game_ended_with_session(xio, "mapped")
            assert await s.game_ended_with_session("ZeroDivisionError", "mapped") == "the X server went away"
            assert await s.game_ended_with_session(xio, "survived") == "", "no display to ask (dev box): no X judgement"
            silent = await _fake_x(str(sock), answer=False)
            assert not await s.x_display_answers(timeout=0.3), "a server that never answers is gone"
            silent.close(); await silent.wait_closed(); sock.unlink(missing_ok=True)
            stopping["v"] = True
            assert await s.game_ended_with_session("ZeroDivisionError", "survived") == "the system is shutting down"
            stopping["v"] = False
            s.SHUTTING_DOWN = True
            assert await s.game_ended_with_session("", "survived") == "the kernel is stopping"
            s.SHUTTING_DOWN = False
            # _reap_game end to end: X gone -> no crash record, the job says why
            gdir = Path(td) / "snake"
            gdir.mkdir()
            done = asyncio.get_running_loop().create_future(); done.set_result("")
            for jid, stderr, xalive in (("j-session", xio, False), ("j-crash", xio, True)):
                s.JOBS[jid] = {"status": "done", "result": {"slug": "snake", "running": True}}
                srv = await _fake_x(str(sock)) if xalive else None
                ctx_ = dict(jid=jid, slug="snake", gdir=gdir, title="snake", version=1, attempt=1,
                            t0=time.time() - 291.5, gate="mapped", window="0x120000a")
                await s._reap_game(_Proc(990000 + len(jid), 1), done, [stderr], ctx_)
                if srv:
                    srv.close(); await srv.wait_closed(); sock.unlink(missing_ok=True)
            meta = json.loads((gdir / "meta.json").read_text())
            ses, crash = s.JOBS["j-session"]["result"], s.JOBS["j-crash"]["result"]
            assert ses["ended"]["with_session"] == "the X server went away (its X connection broke)" and "crash" not in ses, ses
            assert crash["ended"]["with_session"] is None and crash["crash"]["returncode"] == 1, crash
            assert meta["crashes"] == 1 and "17643 requests" in meta["last_crash"]["stderr"], meta
        finally:
            s.wm.WM.display, s.system_stopping, s.SHUTTING_DOWN, s.X_GONE_GRACE = saved
            for j in ("j-session", "j-crash"):
                s.JOBS.pop(j, None)
    src = (REPO / "kernel" / "server.py").read_text()
    assert re.search(r"async def shutdown\(app\):.*?global SHUTTING_DOWN\n\s+SHUTTING_DOWN = True", src, re.S), "the kernel's stop sets the flag"
    return "X gone/system or kernel stopping = ended with the session (no record); XIO while X answers = crash (recorded)"


# ============================================================================= ESP automount [R51]
@test("esp_automount_fstab_units_scripts", ["R51", "R48"])
def t_esp(ctx):
    """/boot/efi is mounted on first access (no boot-time wait — the v7.3 stick run's WARN), unmounted
    when idle; the boot logs still land: the early breadcrumb triggers the automount (systemd: device
    + fsck), a direct mount only when that failed (also at shutdown), then the label; one ESP source
    per attempts line (the autofs + vfat stack lists two)"""
    OPTS = "umask=0077,nofail,x-systemd.automount,x-systemd.idle-timeout=120,x-systemd.device-timeout=45"
    for f in (REPO / "iso" / "build-img.sh", INC / "usr" / "local" / "sbin" / "kairos-install"):
        line = next(l for l in f.read_text().splitlines() if "/boot/efi" in l and "vfat" in l and not l.lstrip().startswith("#"))
        assert line.split()[3] == OPTS and line.split()[-2:] == ["0", "2"], (f.name, line)
    units = INC / "etc" / "systemd" / "system"
    bl = (units / "kairos-bootlog.service").read_text()
    assert re.search(r"^After=boot-efi\.automount boot-efi\.mount$", bl, re.M), "the shutdown capture runs before the ESP goes"
    early = (units / "kairos-bootlog-early.service").read_text()
    assert re.search(r"^After=local-fs\.target boot-efi\.mount$", early, re.M) and "Before=basic.target" in early
    to = int(re.search(r"^TimeoutStartSec=(\d+)", early, re.M).group(1))
    assert to >= 45 + 30, f"the early unit must outlast the device timeout + fsck: {to}"
    for name in ("kairos-bootlog", "kairos-bootlog-early"):
        body = (INC / "usr" / "local" / "sbin" / name).read_text()
        assert 'esp_vfat() { findmnt -n -o FSTYPE --mountpoint "$ESP" 2>/dev/null | grep -qx vfat; }' in body, name
        assert 'esp_vfat || ls "$ESP/" >/dev/null 2>&1' in body and 'esp_vfat || mount "$ESP" 2>/dev/null ||' in body, name
        assert "blkid -L EFI" in body and 'mountpoint -q "$ESP"' not in body, name
    # the early breadcrumb against stub findmnt/mount/blkid: (a) the automount mounted it, (b) the
    # trigger failed -> direct fstab mount, (c) that failed too -> by label
    script = INC / "usr" / "local" / "sbin" / "kairos-bootlog-early"
    outcomes = {}
    for case in ("automount", "direct", "label"):
        with tempfile.TemporaryDirectory() as td:
            t = Path(td); (t / "bin").mkdir(); esp = t / "esp"; esp.mkdir()
            (t / "bin" / "findmnt").write_text(f"""#!/bin/sh
case "$*" in
  *"-o FSTYPE --mountpoint"*) if [ "{case}" = automount ] || [ -e "{t}/mounted" ]; then printf 'autofs\\nvfat\\n'; else echo autofs; fi ;;
  "-no SOURCE /") echo /dev/sdz2 ;;
  "-no SOURCE {esp}") printf 'systemd-1\\n/dev/sdz1\\n' ;;
  *) exit 1 ;;
esac
""")
            (t / "bin" / "mount").write_text(f"""#!/bin/sh
echo "mount $*" >> "{t}/mount.log"
case "{case}:$*" in direct:{esp}|label:/dev/sdz1*) touch "{t}/mounted"; exit 0 ;; esac
exit 32
""")
            (t / "bin" / "blkid").write_text('#!/bin/sh\n[ "$*" = "-L EFI" ] && echo /dev/sdz1 && exit 0\nexit 2\n')
            for b in (t / "bin").iterdir():
                b.chmod(0o755)
            env = dict(os.environ, PATH=f"{t}/bin:" + os.environ.get("PATH", "/usr/bin:/bin"), KAIROS_BOOTLOG_ESP=str(esp),
                       KAIROS_BOOTLOG_RUN=str(t / "attempt"), KAIROS_BOOTLOG_PSTORE=str(t / "pstore"))
            subprocess.run(["sh", str(script)], env=env, capture_output=True, text=True, timeout=60)
            mlog = (t / "mount.log").read_text() if (t / "mount.log").exists() else ""
            att = (esp / "KAIROS-LOGS" / "attempts.txt").read_text()
            started = [l for l in att.splitlines() if " started " in l]
            assert len(started) == 1 and started[0].endswith("esp /dev/sdz1") and len(att.splitlines()) == 1, att
            outcomes[case] = mlog.strip().replace(str(esp), "$ESP").replace("\n", " ; ") or "(none)"
    assert outcomes["automount"] == "(none)", outcomes
    assert outcomes["direct"] == "mount $ESP", outcomes
    assert outcomes["label"] == "mount $ESP ; mount /dev/sdz1 $ESP", outcomes
    # the build guest's fstab rewrite (v7.5 = v7.3 + sync): idempotent, keeps the original, refuses a stranger
    fix = REPO / "iso" / "esp-automount-fstab.sh"
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "fstab"
        orig = ("PARTUUID=baab6cd6  /          ext4  defaults,noatime,x-systemd.growfs  0 1\n"
                "UUID=6153-4063           /boot/efi  vfat  umask=0077,nofail,x-systemd.device-timeout=10                         0 2\n"
                "/swapfile none swap sw 0 0\n# /boot/efi was on /dev/sda1 during installation\n")
        f.write_text(orig)
        subprocess.run(["sh", str(fix), str(f)], check=True, capture_output=True)
        once = f.read_text()
        subprocess.run(["sh", str(fix), str(f)], check=True, capture_output=True)
        assert f.read_text() == once and (Path(td) / "fstab.pre-v75").read_text() == orig
        assert f"UUID=6153-4063 /boot/efi vfat {OPTS} 0 2" in once.splitlines() and once.count("\n") == orig.count("\n")
        assert "# /boot/efi was on /dev/sda1 during installation" in once and "x-systemd.growfs  0 1" in once
        g = Path(td) / "other"; g.write_text("/dev/sda2 / ext4 defaults 0 1\n")
        assert subprocess.run(["sh", str(fix), str(g)], capture_output=True).returncode == 1 and g.read_text() == "/dev/sda2 / ext4 defaults 0 1\n"
    return f"fstab automount in build+installer; units ordered; early ESP: {outcomes}; guest fstab script idempotent"


def main():
    tests = [v for v in globals().values() if getattr(v, "_test", False)]
    only = [x.strip() for x in os.environ.get("KAIROS_TEST_ONLY", "").split(",") if x.strip()]
    if only:
        tests = [t for t in tests if any(o in t._name for o in only)]
    print(f"kAIrOS v7.5 desktop + web tests — {len(tests)} tests", flush=True)
    for t in tests:
        t({})
    if not only:
        out = {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "tests": RESULTS}
        (REPO / "tests" / "results-desktop-web.json").write_text(json.dumps(out, indent=1))
    fails = [n for n, r in RESULTS.items() if r["status"] == "fail"]
    passes = sum(1 for r in RESULTS.values() if r["status"] == "pass")
    skips = sum(1 for r in RESULTS.values() if r["status"] == "skip")
    print(f"\n{passes} passed, {len(fails)} failed, {skips} skipped -> tests/results-desktop-web.json", flush=True)
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
