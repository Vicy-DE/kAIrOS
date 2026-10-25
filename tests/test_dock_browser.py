#!/usr/bin/env python3
"""kAIrOS R81 (the dock), R83 (the built-in browser; addresses from the desktop search) and R84 (✨ improve a website)
— docs/DOCK-BROWSER-2026-10-15.md.

Standalone: `python3 tests/test_dock_browser.py` (run-tests.sh runs it after the suite). Writes
tests/results-dock-browser.json (not for a KAIROS_TEST_ONLY subset).
* kernel tests: kernel/server.py imported in-process with a PRIVATE VIBEOS_DIR (always — never the suite kernel's), the
  LLM mocked at MM.chat / MM.chat_stream (every prompt captured), the painter mocked at render_images;
* e2e tests: the REAL shell (index.html + shell.js + browser.js) served by the real kernel routes (server.add_routes) in
  a headless Chromium driven over the DevTools protocol from Python (aiohttp's WebSocket — no node needed). They skip
  without Chromium; kairos-fix-chromium:local has one. KAIROS_SHOTS=<dir> saves the owner's screenshots there.
"""
import asyncio
import base64
import glob
import json
import os
import re
import shutil
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


def record(name, reqs, status, detail=""):
    RESULTS[name] = {"reqs": reqs, "status": status, "detail": detail[:300]}
    mark = {"pass": "✓", "fail": "✗", "skip": "~"}[status]
    print(f"  {mark} {name} [{', '.join(reqs)}] {detail[:160]}", flush=True)


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
                record(name, reqs, "fail", f"{type(e).__name__}: {str(e)[:600]} @ {where}")
                traceback.print_exc()
        wrapper._name, wrapper._reqs, wrapper._test = name, reqs, True
        return wrapper
    return deco


# ----------------------------------------------------------------------------- the kernel, in-process, private dir
_K = {}


def kernel():
    if "server" in _K:
        return _K["server"]
    try:
        import aiohttp  # noqa: F401
    except ImportError:
        raise SkipTest("aiohttp not installed (run in the testrig container)")
    d = tempfile.mkdtemp(prefix="kairos-db-")
    os.environ.update(VIBEOS_DIR=d, VIBEOS_MANIFEST=str(REPO / "manifests" / "tier-8gb.json"),
                      VIBEOS_MANIFESTS=str(REPO / "manifests"), VIBEOS_SHELL=str(REPO / "shell"),
                      VIBEOS_FORCE_OFFLINE="1", KAIROS_LOG_DIR=str(Path(d) / "log"),
                      VIBEOS_TOKEN_FILE=str(Path(d) / "token"))
    sys.path.insert(0, str(REPO / "kernel"))
    import server
    server.engine_missing = lambda binary: None          # the LLM is mocked below; no engine binaries here
    _K["server"] = server
    return server


def png(w, h, rgb=(128, 128, 128), rgb2=None):
    """a real w×h RGB PNG; with rgb2 a diagonal gradient (the seeded app icons)"""
    rows = []
    for y in range(h):
        row = bytearray(b"\x00")
        for x in range(w):
            if rgb2:
                t = (x + y) / max(1, w + h - 2)
                row += bytes(int(a + (b - a) * t) for a, b in zip(rgb, rgb2))
            else:
                row += bytes(rgb)
        rows.append(bytes(row))
    chunk = lambda t, dd: struct.pack(">I", len(dd)) + t + dd + struct.pack(">I", zlib.crc32(t + dd) & 0xffffffff)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"".join(rows), 1)) + chunk(b"IEND", b""))


def site_page(address, title=None, extra="", big_link=None, pictures=(), big=True):
    """an imaginary-web page as the page writer would answer it: header + logo + nav (the R75 identity), a big link in
    the top-left corner (the e2e tests click it inside the sandboxed frame), optional /genimg/ pictures"""
    site = address.split("/")[0].split("?")[0]
    brand = site.split(".")[0].replace("-", " ").title()
    title = title or f"{brand} | {address}"
    pics = "".join(f'<img alt="{p}" src="/genimg/{p}" style="width:280px;height:180px;object-fit:cover;border-radius:12px">'
                   for p in pictures)
    return (f"<!DOCTYPE html><html><head><meta charset='utf-8'><title>{title}</title><style>"
            ":root{--bg:#101826;--ink:#eef3ff;--accent:#ffb347}body{margin:0;background:var(--bg);color:var(--ink);"
            "font-family:Georgia,serif}header{display:flex;gap:24px;align-items:center;padding:18px 28px;"
            "border-bottom:2px solid var(--accent)}.logo{font-weight:800;font-size:24px;color:#ffb347}"
            "nav a{color:#9fd3ff;margin-right:16px}#big{position:fixed;left:0;top:0;width:180px;height:64px;"
            "display:block;background:#ffb347;color:#101826;font:700 14px system-ui;padding:8px}"
            f"main{{padding:{90 if big else 28}px 28px 28px}}</style></head><body>"
            + (f"<a id='big' href='{big_link or f'/web/{site}/about'}'>BIG LINK</a>" if big else "") +
            f"<header><div class='logo'>{brand}</div><nav><a href='/web/{site}'>Home</a><a href='/web/{site}/about'>About</a>"
            f"<a href='/web/{site}/menu'>Menu</a><a href='/web/{site}/contact'>Contact</a></nav></header>"
            f"<main><h1>{title}</h1><p>Welcome to {brand}, the finest establishment on this side of the imaginary web — "
            f"fresh every morning since 1987.</p>{pics}{extra}</main></body></html>")


REFUSAL = ("I cannot fulfill this request. I am programmed to be a helpful and harmless AI assistant. My safety "
           "guidelines prohibit me from generating sexually explicit content or pornography.")


class Env:
    """the mocked LLM + painter + a private web/apps cache on the in-process kernel; every prompt is captured"""

    def __init__(self, s, delay=0.0, paint_delay=0.3):
        self.s, self.delay, self.paint_delay = s, delay, paint_delay
        self.prompts, self.streams, self.pages, self.renders, self.saved = [], [], {}, [], {}
        self.big = True                     # the e2e navigation test clicks the page's BIG LINK
        self.windows_ops = []

    def __enter__(self):
        s, env = self.s, self

        async def chat(system, messages, key="llm", max_tokens=16384, temperature=0.7, sampling=None, gen_info=None):
            user = messages[-1]["content"]
            env.prompts.append({"system": system, "user": user, "key": key})
            m = re.match(r"The browser navigated to: ([^\n]+)", user)
            if "declineme" in user:
                return REFUSAL
            if m:
                if env.delay:
                    await asyncio.sleep(env.delay)
                addr = m.group(1)
                html = env.pages.get(addr) or site_page(addr, big=env.big)
                if "cutme" in addr and isinstance(gen_info, dict):
                    gen_info.update(finish="length", tokens=16000, length=True)
                    return html[:len(html) * 3 // 5]
                return html
            if "self-repair daemon" in (system or ""):
                doc = re.search(r"DOCUMENT HTML:\n(.*)\n\nPROBLEMS TO FIX:", user, re.S)
                return env.repair(doc.group(1) if doc else user)
            return "ok"

        async def chat_stream(system, messages, key="llm", max_tokens=16384, temperature=0.7, gen_info=None):
            user = messages[-1]["content"]
            env.streams.append({"system": system, "user": user, "key": key})
            text = env.evolve(system, user)
            if isinstance(gen_info, dict):
                cut = "[cut]" in user
                gen_info.update(finish="length" if cut else "stop", tokens=900, length=cut)
            for i in range(0, len(text), 64):
                await asyncio.sleep(0.01)
                yield text[i:i + 64]

        async def render(kind, items, adult=False, expand=None):
            env.renders.append([it["prompt"] for it in items])
            await asyncio.sleep(env.paint_delay)
            out = []
            for i, it in enumerate(items):
                Path(it["out"]).parent.mkdir(parents=True, exist_ok=True)
                Path(it["out"]).write_bytes(png(it["w"], it["h"], (40 + 30 * i, 90, 160)))
                out.append({"ok": True, "prompt": it["prompt"], "expanded": False})
            return out

        async def no_facts(*a, **kw):
            return "", None

        async def same(text, settings=None):
            return text
        for name, val in (("english_text", same), ("render_images", render), ("GENIMG_WAIT", 1.0),
                          ("GENIMG_WINDOW", 0.15)):
            self.saved[name] = getattr(s, name)
            setattr(s, name, val)
        self.saved["MM.chat"], s.MM.chat = s.MM.chat, chat
        self.saved["MM.chat_stream"], s.MM.chat_stream = s.MM.chat_stream, chat_stream
        self.saved["KB.context_block"], s.KB.context_block = s.KB.context_block, no_facts
        real_block = s.block_check_primary

        def block(kind, request, key="llm", label=""):
            if "blockme" in str(request):
                raise s.MinorBlockError(s.refusal.BLOCK_MESSAGE)
            return real_block(kind, request, key, label)
        self.saved["block_check_primary"], s.block_check_primary = s.block_check_primary, block
        for d in (s.WEB_JOBS, s.WEB_CUTS, s.WEB_SERVED, s.GENIMG_INFLIGHT, s.GENIMG_FAILED, s.GENIMG_LATE):
            d.clear()
        return self

    def __exit__(self, *exc):
        s = self.s
        s.MM.chat = self.saved.pop("MM.chat")
        s.MM.chat_stream = self.saved.pop("MM.chat_stream")
        s.KB.context_block = self.saved.pop("KB.context_block")
        for name, val in self.saved.items():
            setattr(s, name, val)

    def evolve(self, system, user):
        """the page writer's next version: the previous page with a marked heading (version + instruction)"""
        v = re.search(r"Write version (\d+) of this page", user)
        ins = re.search(r"USER IMPROVEMENT REQUEST: ([^\n]*)", user)
        prev = user.split("HTML:\n", 1)[1].rsplit("\n\nWrite version", 1)[0] if "HTML:\n" in user else "<html></html>"
        if "[regress]" in user:
            return "<!DOCTYPE html><html><head><title>broken</title></head><body><script>let = ;</script>"
        tag = f"v{v.group(1) if v else '?'}"
        what = (ins.group(1).strip() if ins else "no instruction").replace("<", "&lt;")
        prev = re.sub(r"<title>(.*?)</title>", lambda m: f"<title>{re.sub(r' · v[0-9]+$', '', m.group(1))} · {tag}</title>", prev, count=1)
        prev = re.sub(r"<h2 class='improved'>.*?</h2>", "", prev)
        purple = "body{background:#2a1450 !important}" if "purple" in what else ""
        return prev.replace("</style>", purple + "</style>", 1).replace(
            "<main>", f"<main><h2 class='improved' style='color:#c9a7ff'>✨ improved {tag}: {what}</h2>", 1)

    def repair(self, doc):
        return doc if "let = ;" not in doc else doc   # the broken page stays broken (the no-regression path)


def seed_apps(s, ids, recent=None):
    """cached apps in APPS_DIR (meta.json + app.html) with a painted icon each — the dock's user apps"""
    colours = [((255, 120, 80), (120, 40, 200)), ((60, 200, 255), (20, 60, 160)), ((120, 255, 140), (20, 120, 90)),
               ((255, 210, 60), (200, 70, 20)), ((230, 90, 220), (60, 20, 120))]
    now = time.time()
    for i, aid in enumerate(ids):
        d = s.APPS_DIR / aid
        d.mkdir(parents=True, exist_ok=True)
        (d / "app.html").write_text(f"<!DOCTYPE html><html><head><title>{aid}</title></head><body style='background:#123;color:#eee;"
                                    f"font:20px system-ui;padding:30px'><h1>{aid.replace('-', ' ')}</h1><p>a cached app</p></body></html>")
        (d / "meta.json").write_text(json.dumps({"app_id": aid, "prompt": f"a {aid.replace('-', ' ')} app", "version": 1,
                                                 "created": now - 1000 - i, "last_opened": (recent or {}).get(aid, now - i)}))
        icon = s.FILES_DIR / "icons" / f"{aid}.png"
        icon.parent.mkdir(parents=True, exist_ok=True)
        a, b = colours[i % len(colours)]
        icon.write_bytes(png(48, 48, a, b))


# the address table both web_address (kernel) and webAddress (browser.js) must agree on
ADDRESS_TABLE = [
    ("oppenheimer.com", "oppenheimer.com"),
    ("Oppenheimer.com", "oppenheimer.com"),
    ("oppenheimer.com/best-nukes-for-the-cheap", "oppenheimer.com/best-nukes-for-the-cheap"),
    ("www.x.de/y", "www.x.de/y"),
    ("https://example.org/a/b?c=1", "example.org/a/b?c=1"),
    ("http://nebula-cafe.kai/", "nebula-cafe.kai"),
    ("xhamster.com nude slim blonde", "xhamster.com/search?q=nude slim blonde"),
    ("example.com red bikes", "example.com/search?q=red bikes"),
    ("www.x.de/y red bikes", "www.x.de/y?q=red bikes"),
    ("shop.example.com/search?q=a more words", "shop.example.com/search?q=a&q=more words"),
    ("orbital-news.kai", "orbital-news.kai"),
    ("  bakery-berlin.de  ", "bakery-berlin.de"),
    ("www.foo.zz", "www.foo.zz"),
    ("https://foo.zz", "foo.zz"),
    ("node.js tutorial", None),
    ("readme.md", None),
    ("e.g. a pomodoro timer", None),
    ("a pomodoro timer", None),
    ("1.5 kg", None),
    ("localhost:8080", None),
    ("", None),
    ("the moon's best bakery", None),
]


# ============================================================================= kernel tests
@test("db_web_address_table", ["R83", "R23"])
def t_address(ctx):
    """the desktop search's address rule (kernel web_address): a host with http(s)://, www. or a known TLD (.kai
    included) is an address; words after a bare host are a site search (/search?q=), after a path ?q=; everything else
    stays a search"""
    s = kernel()
    bad = [(t, s.web_address(t), want) for t, want in ADDRESS_TABLE if s.web_address(t) != want]
    assert not bad, bad
    assert "kai" in s.WEB_TLDS and "js" not in s.WEB_TLDS and "md" not in s.WEB_TLDS
    return f"{len(ADDRESS_TABLE)} cases (the same table runs through browser.js webAddress in browser_e2e_search_opens_addresses)"


async def _client(s, env=None):
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer
    app = web.Application()
    s.add_routes(app)
    c = TestClient(TestServer(app))
    await c.start_server()
    return c


async def _sse(c, path, body):
    events = []
    async with c.post(path, json=body) as r:
        if r.status != 200:
            return r.status, await r.json()
        buf = b""
        async for chunk in r.content.iter_any():
            buf += chunk
            while b"\n\n" in buf:
                frame, buf = buf.split(b"\n\n", 1)
                ev = d = None
                for ln in frame.decode("utf-8", "ignore").splitlines():
                    if ln.startswith("event: "):
                        ev = ln[7:]
                    elif ln.startswith("data: "):
                        d = json.loads(ln[6:])
                if ev:
                    events.append((ev, d))
    return 200, events


async def _get_page(c, address, extra=""):
    """GET /web/<address> through the loading pages until the page (or its error page) -> (html, report dict)"""
    from urllib.parse import quote
    for _ in range(200):
        async with c.get(f"/web/{quote(address, safe='/?=&')}{extra}") as r:
            html = await r.text()
        rep = json.loads(re.search(r"<script data-kairos-page>\(function\(\)\{var d=(.*?);function post", html).group(1))
        if rep["state"] != "loading":
            return html, rep
        await asyncio.sleep(0.05)
    raise AssertionError(f"{address} never finished")


@test("db_web_page_reports", ["R83", "R57", "R86"])
async def t_reports(ctx):
    """every /web/ document reports itself to the browser (<script data-kairos-page>): loading (elapsed), page (title,
    versions), error kinds declined (R86 ladder exhausted) / blocked (the minors block) / cut (the length limit, shown
    once) — the error page names the kind; the late-picture script stays in every served page; vibe.browse html gets
    no report"""
    s = kernel()
    with Env(s, delay=0.3) as env:
        c = await _client(s)
        try:
            async with c.get("/web/report-test.kai") as r:
                first = await r.text()
            rep = json.loads(re.search(r"var d=(.*?);function post", first).group(1))
            assert rep["state"] == "loading" and rep["address"] == "report-test.kai" and "elapsed" in rep, rep
            html, rep = await _get_page(c, "report-test.kai")
            assert rep == {"address": "report-test.kai", "state": "page"}, rep
            assert html.count("data-kairos-genimg") == 1 and html.count("data-kairos-page") == 1
            assert html.index("data-kairos-page") < html.index("<title>")
            html, rep = await _get_page(c, "declineme.kai")
            assert rep["state"] == "declined" and "every available model" in rep["message"], rep
            assert "the model declined this page" in html and "<a href='/web/home'>home</a>" in html
            html, rep = await _get_page(c, "blockme.kai")
            assert rep["state"] == "blocked" and rep["message"] == s.refusal.BLOCK_MESSAGE and "this page is blocked" in html, rep
            html, rep = await _get_page(c, "cutme.kai")
            assert rep["state"] == "cut" and "cut off" in rep["note"] and "kairos-length-cut" in html, rep
            assert not (s.WEB_DIR / f"{s.web_slug('cutme.kai')}.html").exists()
            async with c.post("/api/browse", json={"address": "report-browse.kai"}) as r:
                b = await r.json()
            assert "data-kairos-page" not in b["html"] and b["html"].count("data-kairos-genimg") == 1
        finally:
            await c.close()
    return "loading/page/declined/blocked/cut reports; error page titles per kind; genimg script kept; vibe.browse unreported"


@test("db_web_improve_versions_revert", ["R84", "R75", "R86", "R77"])
async def t_improve(ctx):
    """POST /api/web/improve streams the page's next version from the previous HTML + the site profile + the
    instruction (WEB_EVOLVE_SYSTEM = BROWSER_SYSTEM with its 18+ page rule); v1 (the page as it was) and v2, v3 are kept,
    /web/<a>?_v=1 serves v1, POST /api/web/version makes v1 the page again (the profile re-anchors: it was the founding
    page); a declined improvement and a blocked one save nothing; a length-cut one is shown once (?_cut=) and not saved;
    a regression is not used (failed-vN kept); the oldest versions go beyond WEB_VERSIONS_KEEP, never the current"""
    s = kernel()
    addr = "bakery-orbit.kai"
    with Env(s) as env:
        c = await _client(s)
        try:
            html1, rep = await _get_page(c, addr)
            prof = s.load_site_profile("bakery-orbit.kai")
            assert prof["brand"] == "Bakery Orbit" and prof["founded_by"] == addr, prof
            st, ev = await _sse(c, "/api/web/improve", {"address": addr, "instruction": "make it purple"})
            names = [e for e, _ in ev]
            assert st == 200 and names[0] == "delta" and names[-1] == "done", names
            done = ev[-1][1]
            assert done["version"] == 2 and done["current"] == 2 and [v["v"] for v in done["versions"]] == [1, 2], done
            p = env.streams[-1]
            assert p["system"] == s.prompts.WEB_EVOLVE_SYSTEM and "ADULT REQUESTS" in p["system"], "the 18+ page rule rides along"
            u = p["user"]
            assert "USER IMPROVEMENT REQUEST: make it purple" in u and "PREVIOUS VERSION (v1) HTML:" in u and "Write version 2" in u, u[:400]
            assert "Site name / brand (use exactly): Bakery Orbit" in u and "Navigation (reuse exactly" in u, u[:1200]
            assert "<title>Bakery Orbit | bakery-orbit.kai</title>" in u, "the previous HTML is in the prompt"
            html2, rep2 = await _get_page(c, addr)
            assert "✨ improved v2: make it purple" in html2 and rep2["version"] == 2 and rep2["versions"] == 2, rep2
            old, repo = await _get_page(c, addr, "?_v=1")
            assert "improved" not in old and repo["version"] == 1 and repo["current"] == 2, repo
            st, ev = await _sse(c, "/api/web/improve", {"address": addr, "instruction": "add a FAQ section"})
            assert ev[-1][0] == "done" and ev[-1][1]["version"] == 3
            assert "PREVIOUS VERSION (v2) HTML:" in env.streams[-1]["user"]
            async with c.get(f"/api/web/versions?address={addr}") as r:
                vs = await r.json()
            assert vs["current"] == 3 and [v["v"] for v in vs["versions"]] == [1, 2, 3], vs
            assert vs["versions"][2]["instruction"] == "add a FAQ section" and vs["versions"][0]["by"] == "generated"
            # the revert: v1 is the page again; nothing deleted; the founding page re-anchors the profile
            async with c.post("/api/web/version", json={"address": addr, "version": 1}) as r:
                rv = await r.json()
            assert rv["current"] == 1 and len(rv["versions"]) == 3, rv
            back, rep = await _get_page(c, addr)
            assert "improved" not in back and rep["version"] == 1, rep
            async with c.post("/api/web/version", json={"address": addr, "version": 9}) as r:
                assert r.status == 404
            # improving from an older version shown in the browser
            st, ev = await _sse(c, "/api/web/improve", {"address": addr, "instruction": "", "from_version": 2})
            assert ev[-1][1]["version"] == 4 and "PREVIOUS VERSION (v2) HTML:" in env.streams[-1]["user"]
            assert "No instruction: improve the page by your own judgement." in env.streams[-1]["user"]
            # declined (every rung refused) and blocked: nothing saved, the error says which
            n_before = len(env.streams)
            mock_stream = s.MM.chat_stream

            async def refusing(system, messages, key="llm", max_tokens=16384, temperature=0.7, gen_info=None):
                env.streams.append({"user": messages[-1]["content"]})
                for i in range(0, len(REFUSAL), 50):
                    yield REFUSAL[i:i + 50]
            s.MM.chat_stream = refusing
            st, ev = await _sse(c, "/api/web/improve", {"address": addr, "instruction": "something"})
            assert ev[-1][0] == "error" and ev[-1][1]["kind"] == "declined" and "every available model" in ev[-1][1]["message"], ev[-3:]
            assert any(e == "refusal" for e, _ in ev), [e for e, _ in ev]
            assert len(env.streams) - n_before >= 2, "the ladder's rung ran"
            s.MM.chat_stream = mock_stream
            st, ev = await _sse(c, "/api/web/improve", {"address": addr, "instruction": "blockme"})
            assert ev[-1][0] == "error" and ev[-1][1]["kind"] == "blocked", ev[-1]
            async with c.get(f"/api/web/versions?address={addr}") as r:
                assert (await r.json())["current"] == 4
        finally:
            await c.close()
    with Env(s) as env:
        c = await _client(s)
        try:
            # cut at the length limit: shown once through ?_cut=, never saved
            st, ev = await _sse(c, "/api/web/improve", {"address": addr, "instruction": "longer [cut]"})
            d = ev[-1][1]
            assert ev[-1][0] == "done" and d["truncated"] and d["cut"] and "cut off" in d["note"], d
            html, rep = await _get_page(c, addr, f"?_cut={d['cut']}")
            assert rep["state"] == "cut" and "kairos-length-cut" in html
            html, rep = await _get_page(c, addr, f"?_cut={d['cut']}")
            assert rep["state"] == "page" and rep["version"] == 4, "the cut view is served once"
            # a regression is not used
            st, ev = await _sse(c, "/api/web/improve", {"address": addr, "instruction": "[regress]"})
            assert ev[-1][0] == "fallback" and ev[-1][1]["version"] == 4, ev[-1]
            assert (s._web_versions_dir(addr) / "failed-v5.html").exists()
            # the limit: oldest first, never the current
            keep, s.WEB_VERSIONS_KEEP = s.WEB_VERSIONS_KEEP, 3
            try:
                async with c.post("/api/web/version", json={"address": addr, "version": 2}) as r:
                    assert (await r.json())["current"] == 2
                st, ev = await _sse(c, "/api/web/improve", {"address": addr, "instruction": "one more"})
                vs = [v["v"] for v in ev[-1][1]["versions"]]
                assert vs == [3, 4, 5] and ev[-1][1]["current"] == 5, vs
                assert not (s._web_versions_dir(addr) / "v1.html").exists()
            finally:
                s.WEB_VERSIONS_KEEP = keep
            # a page that is not cached cannot be improved; a fresh reload of an improved page becomes a version
            async with c.post("/api/web/improve", json={"address": "never-seen.kai"}) as r:
                assert r.status == 404 and "open it first" in (await r.json())["error"]
            await c.get(f"/web/{addr}?_fresh=1")
            html, rep = await _get_page(c, addr)
            assert rep["version"] == 6, rep
        finally:
            await c.close()
    return ("v1→v2 (purple)→v3 (FAQ); prompt = previous HTML + profile + instruction + the 18+ rule; ?_v=1; revert to v1 "
            "(profile re-anchored); from_version 2 → v4; declined/blocked save nothing; cut shown once; regression kept "
            "as failed-v5; keep=3 drops the oldest; _fresh adds v6")


@test("db_search_addresses_system_apps", ["R83", "R81", "R23"])
async def t_search(ctx):
    """the desktop search: an address is the FIRST result ({type: web}); the system apps are found by name ({type:
    sysapp} — Notes, News, Files left the dock); the retired generated browser is neither in /api/apps nor in the
    search, its cache stays on disk"""
    s = kernel()
    seed_apps(s, ["browser", "notes", "pomodoro-timer"])
    with Env(s):
        c = await _client(s)
        try:
            async def q(text):
                async with c.get("/api/desktop-search", params={"q": text}) as r:
                    return (await r.json())["results"]
            r = await q("oppenheimer.com")
            assert r[0]["group"] == "web" and r[0]["action"] == {"type": "web", "address": "oppenheimer.com"}, r[0]
            r = await q("example.com red bikes")
            assert r[0]["action"]["address"] == "example.com/search?q=red bikes", r[0]
            r = await q("node.js tutorial")
            assert all(x["group"] != "web" for x in r)
            r = await q("notes")
            assert any(x["action"] == {"type": "sysapp", "app_id": "notes"} for x in r), r
            assert sum(1 for x in r if x["action"].get("app_id") == "notes") == 1, "no duplicate of the cached notes app"
            r = await q("files")
            assert any(x["action"] == {"type": "sysapp", "app_id": "files"} for x in r)
            r = await q("browser")
            assert any(x["action"] == {"type": "sysapp", "app_id": "browser"} for x in r)
            assert not any(x["action"].get("type") == "app" and x["action"].get("app_id") == "browser" for x in r), r
            async with c.get("/api/apps") as rr:
                ids = [a["app_id"] for a in (await rr.json())["apps"]]
            assert "browser" not in ids and "pomodoro-timer" in ids and "notes" in ids, ids
            assert (s.APPS_DIR / "browser" / "app.html").exists(), "the retired app's cache stays on disk"
        finally:
            await c.close()
    return "address first (web), site search, not for node.js; sysapps notes/files/browser; retired browser hidden, kept on disk"


@test("db_shell_static", ["R81", "R83", "R46", "R13"])
def t_static(ctx):
    """index.html: the dock's pinned catalogue has no Files/Notes/News and no generated browser (data-builtin
    browser instead), the system apps sit in <template id="system-apps">, browser.js loads after shell.js; shell.js has
    the record list, the renderer, the states and no chips for windows with an icon; the bridge refuses /web/ pages"""
    html = (REPO / "shell" / "index.html").read_text()
    dock = html.split('<div id="dock">')[1].split("</div>")[0]
    assert 'data-builtin="browser"' in dock and 'data-app="settings"' in dock
    for gone in ('data-app="files"', 'data-app="notes"', 'data-app="news"', 'data-app="browser"'):
        assert gone not in dock, gone
    tpl = html.split('<template id="system-apps">')[1].split("</template>")[0]
    for sysapp in ("notes", "news", "files"):
        assert f'data-app="{sysapp}" data-prompt="' in tpl, sysapp
    assert html.index('src="/shell/shell.js"') < html.index('src="/shell/browser.js"')
    js = (REPO / "shell" / "shell.js").read_text()
    for needle in ("function windowRecords()", "function renderDock()", "function dockClick(key)", 'classList.toggle("minimized", min)',
                   'classList.toggle("shown"', "if (!dockKeyOf(win)) addTask(win)", 'if (w.kind === "game") continue;',
                   "if (!win || win.br || win.isPage) return;", "function openSysApp(id)", "DOCK_GROW"):
        assert needle in js, needle
    css = (REPO / "shell" / "shell.css").read_text()
    # [R81, owner 2026-10-24] running = 1.33 whether minimized or shown (maximized or not); shown keeps its glow
    assert re.search(r"\.dock-app\.minimized, \.dock-app\.shown \{[^}]*scale\(1\.33\)", css) and ".dock-app.shown {" in css
    assert re.search(r"\.dock-app\.minimized:hover, \.dock-app\.shown:hover \{[^}]*translateY\(-6px\) scale\(1\.42\)", css)
    assert re.search(r"\.dock-app\.shown \{[^}]*box-shadow", css) and re.search(r"\.dock-app\.shown\.active \{[^}]*box-shadow", css)
    bjs = (REPO / "shell" / "browser.js").read_text()
    for needle in ('f.sandbox = "allow-scripts allow-forms"', "kairosPage", "/api/web/improve", "/api/web/version",
                   "function webAddress(text)"):
        assert needle in bjs, needle
    if shutil.which("node"):
        for f in ("shell.js", "browser.js"):
            subprocess.run(["node", "--check", str(REPO / "shell" / f)], check=True, capture_output=True)
    return "pinned catalogue without Files/Notes/News/generated browser; system-apps template; states + chips rule; bridge closed to /web/"


@test("db_r66_sandboxed_caller_not_trusted", ["R66", "R83"])
async def t_sandboxed_headers(ctx):
    """[review v7.5-2 F1] loopback is trusted only for a caller that is not a sandboxed frame (server.sandboxed_caller):
    what a generated page's / app's OWN fetch(), sendBeacon() or form post carries on the kiosk (loopback) — Origin:
    null (its opaque origin) or Sec-Fetch-Site cross-site / same-site — gets 403 at a guarded route and the handler never
    runs (nothing stored, the game route never looks); the shell (same-origin) and curl / kairos-bootlog (neither
    header) pass; the token still authorizes (a sandboxed frame cannot send it: a custom header needs a CORS preflight
    the kernel never answers)"""
    s = kernel()
    c = await _client(s)
    saved = s.API_TOKEN
    s.API_TOKEN = "f1-test-token"          # this test's own token; the module's is restored below
    sandbox = {"Origin": "null", "Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "cors",
               "Content-Type": "text/plain;charset=UTF-8"}               # what the review captured from Chromium 154
    cases = [("sandboxed fetch/beacon", sandbox, 403),
             ("Origin null only", {"Origin": "null"}, 403),
             ("cross-site only", {"Sec-Fetch-Site": "cross-site"}, 403),
             ("same-site", {"Sec-Fetch-Site": "same-site"}, 403),
             ("sandboxed + wrong token", dict(sandbox, **{s.TOKEN_HEADER: "wrong"}), 403),
             ("the shell (same-origin)", {"Origin": f"http://127.0.0.1:{c.server.port}", "Sec-Fetch-Site": "same-origin",
                                          "Content-Type": "application/json"}, 200),
             ("curl/kairos-bootlog (no headers)", {}, 200),
             ("sandboxed + the token", dict(sandbox, **{s.TOKEN_HEADER: "f1-test-token"}), 200)]
    out = []
    try:
        for i, (what, hdrs, want) in enumerate(cases):
            key = f"f1-case-{i}"
            async with c.post("/api/store", data=json.dumps({"key": key, "value": what}), headers=hdrs) as r:
                got = r.status
            stored = key in s._load_store()
            assert got == want and stored == (want == 200), (what, got, "stored" if stored else "not stored")
            out.append(f"{what} {got}")
        async with c.post("/api/game/version", data=json.dumps({"slug": "nosuch", "version": 1}), headers=sandbox) as r:
            assert r.status == 403, f"the guarded game handler ran for a sandboxed caller: {r.status}"
        assert s.sandboxed_caller(type("R", (), {"headers": {"Sec-Fetch-Site": "none"}})()) is False, "a typed URL is not a frame"
    finally:
        s.API_TOKEN = saved
        await c.close()
    return "; ".join(out) + "; /api/game/version sandboxed 403"


# [review v7.5-2 F2] the review's probe 5 address table: read as words (server.address_words: / _ + - are spaces), an
# under-18 age + a sexual term in an address PATH meets the minors block; dates, sizes, page numbers, adult ages and
# slugged age gates do not. Short strings, pure functions — and at the routes the LLM is the mock (0 calls).
ADDR_BLOCK = ["teen.kai/15-year-old nude", "nude.kai/15-year-old", "site.com/15-year-old-nude", "site.com/nude/15yo",
              "site.com/nude_15_year_old", "site.com/search?q=nude+15+year+old", "porn.kai/age/15", "nackt.kai/14-jährige",
              "xxx.kai/teens/15-yo", "porn.kai/ages-12-17"]
ADDR_PASS = ["site.com/2024/10/15/news", "shop.kai/size-16-gb", "kids.kai/15-year-old-birthday-party",
             "porn.kai/milf-45-year-old", "porn.kai/18-year-old", "porn.kai/18_plus_only", "nude.kai/page/15",
             "nude.kai/top_10_beaches", "xxx.kai/categories/teen-18-plus", "tube.kai/videos/12345/hot-wife",
             "porn.kai/age-verification/under-18-not-allowed", "nackt.kai/ab-18", "porn.kai/1080p/15-min",
             "sex.kai/blog/2015/12/17", "sexshop.kai/size_17_cm"]


@test("db_r86_address_path_real_block", ["R86", "R83", "R84"])
async def t_address_block(ctx):
    """[review v7.5-2 F2] the REAL minors block (not the mock's "blockme") on browser addresses, the age in the PATH:
    the review's 10 must-block address forms block and its 15 must-pass forms pass once / _ + - read as spaces; at the
    routes: /web/ (check point 4, the uncensored primary) answers `blocked` with 0 LLM calls and caches nothing, an age
    without a sexual term is a page; website ✨ on 'nude.kai/15-year-old' with no note is `blocked`, 0 calls, no
    version; a censored primary that refuses such an address meets check point 3 (the primary's one call, no rung)"""
    s = kernel()
    hit = lambda a: s.refusal.minor_block(s.address_words(a))[0]
    bad = [("must block", a) for a in ADDR_BLOCK if not hit(a)] + [("must pass", a) for a in ADDR_PASS if hit(a)]
    assert not bad, bad
    with Env(s) as env:
        c = await _client(s)
        try:
            for a in ("teen.kai/15-year-old nude", "site.com/15-year-old-nude", "site.com/nude_15_year_old"):
                html, rep = await _get_page(c, a)
                assert rep["state"] == "blocked" and rep["message"] == s.refusal.BLOCK_MESSAGE, (a, rep)
                assert not (s.WEB_DIR / f"{s.web_slug(a)}.html").exists(), f"{a} cached"
            assert not env.prompts and not env.streams, "a blocked address reaches no model"
            html, rep = await _get_page(c, "kids.kai/15-year-old-birthday-party")
            assert rep["state"] == "page", rep
            addr = "nude.kai/15-year-old"
            (s.WEB_DIR / f"{s.web_slug(addr)}.html").write_text(site_page(addr, big=False))
            st, ev = await _sse(c, "/api/web/improve", {"address": addr, "instruction": ""})
            assert st == 200 and ev and ev[-1][0] == "error" and ev[-1][1]["kind"] == "blocked", (st, ev[-1:])
            assert not env.streams and not (s.load_web_versions(addr) or {}).get("versions"), "no call, no version"
            was = s.MANIFEST["llm"].get("uncensored")
            s.MANIFEST["llm"]["uncensored"] = False           # a censored primary: not checked at the entry
            try:
                n = len(env.prompts)
                html, rep = await _get_page(c, "declineme.kai/15-year-old-nude")    # the mock refuses "declineme"
                assert rep["state"] == "blocked", rep
                assert len(env.prompts) - n == 1, f"check point 3 before any rung: {len(env.prompts) - n} calls"
            finally:
                s.MANIFEST["llm"]["uncensored"] = was
        finally:
            await c.close()
    return (f"{len(ADDR_BLOCK)} block / {len(ADDR_PASS)} pass (address_words); /web/ ×3 blocked, 0 calls, not cached; "
            "age without a term = page; web ✨ blocked, no version; censored primary: check point 3 after 1 call")


# ============================================================================= the real shell in headless Chromium
def find_chrome():
    for cand in [os.environ.get("KAIROS_CHROME")] + sorted(glob.glob("/ms-playwright/chromium-*/chrome-linux*/chrome")) \
            + [shutil.which(n) for n in ("chromium", "chromium-browser", "google-chrome")]:
        if cand and os.path.exists(cand):
            return cand
    return None


class CDP:
    """a page of a headless Chromium over the DevTools protocol (aiohttp WebSocket)"""

    def __init__(self, port):
        self.port, self.mid, self.ws, self.cs = port, 0, None, None

    async def open(self, url, w=1920, h=1080):
        import aiohttp
        self.cs = aiohttp.ClientSession()
        for _ in range(200):
            try:
                async with self.cs.get(f"http://127.0.0.1:{self.port}/json/version") as r:
                    await r.json()
                break
            except Exception:
                await asyncio.sleep(0.1)
        async with self.cs.put(f"http://127.0.0.1:{self.port}/json/new?about:blank") as r:
            target = await r.json()
        self.ws = await self.cs.ws_connect(target["webSocketDebuggerUrl"], max_msg_size=0)
        await self.size(w, h)
        await self.send("Page.navigate", {"url": url})

    async def size(self, w, h):
        await self.send("Emulation.setDeviceMetricsOverride", {"width": w, "height": h, "deviceScaleFactor": 1, "mobile": False})

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
            await asyncio.sleep(0.08)
        raise AssertionError(f"timeout ({timeout} s): {what or expr[:200]}")

    async def center(self, sel):
        return await self.ev(f"(() => {{ const e = document.querySelector({json.dumps(sel)}); if (!e) return null; "
                             "const r = e.getBoundingClientRect(); return [r.x + r.width / 2, r.y + r.height / 2]; })()")

    async def click(self, x, y, button="left"):
        for t in ("mouseMoved", "mousePressed", "mouseReleased"):
            await self.send("Input.dispatchMouseEvent", {"type": t, "x": x, "y": y, "button": button, "clickCount": 1})

    async def click_sel(self, sel, button="left"):
        c = await self.center(sel)
        assert c, f"no element {sel}"
        await self.click(c[0], c[1], button)

    async def away(self):
        """the pointer off the dock (a hovered icon shows its hover size)"""
        await self.send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": 6, "y": 300})

    async def type(self, text):
        await self.send("Input.insertText", {"text": text})

    async def key(self, k):
        codes = {"Enter": (13, "\r"), "Escape": (27, None)}
        vk, text = codes[k]
        down = {"type": "keyDown", "key": k, "code": k, "windowsVirtualKeyCode": vk}
        if text:
            down["text"] = text
        await self.send("Input.dispatchKeyEvent", down)
        await self.send("Input.dispatchKeyEvent", {"type": "keyUp", "key": k, "code": k, "windowsVirtualKeyCode": vk})

    async def shot(self, name, clip=None):
        if not SHOTS:
            return None
        await asyncio.sleep(0.35)
        params = {"format": "png"}
        if clip:                              # [x, y, w, h] in page px: a close-up (the dock's icons)
            params["clip"] = {"x": clip[0], "y": clip[1], "width": clip[2], "height": clip[3], "scale": 1}
        r = await self.send("Page.captureScreenshot", params)
        Path(SHOTS).mkdir(parents=True, exist_ok=True)
        p = Path(SHOTS) / name
        p.write_bytes(base64.b64decode(r["data"]))
        return str(p)

    async def close(self):
        try:
            if self.ws:
                await self.ws.close()
        finally:
            if self.cs:
                await self.cs.close()


class Desk:
    """the kernel's routes (server.add_routes) on a test server + a headless Chromium on the shell"""

    def __init__(self, s, env, w=1920, h=1080, query=""):
        self.s, self.env, self.w, self.h, self.query = s, env, w, h, query
        self.ops = []
        self.posts = []         # [review v7.5-2 F1] every POST /api/…: who sent it (Origin, Sec-Fetch-Site), the answer

    async def __aenter__(self):
        from aiohttp import web
        from aiohttp.test_utils import TestServer
        chrome = find_chrome()
        if not chrome:
            raise SkipTest("needs Chromium (kairos-fix-chromium:local)")
        ops, posts = self.ops, self.posts

        @web.middleware
        async def spy(request, handler):
            if request.path.startswith("/api/windows/") and request.method == "POST":
                ops.append(request.path)
            if request.path.startswith("/api/") and request.method == "POST":
                rec = {"path": request.path, "origin": request.headers.get("Origin"),
                       "site": request.headers.get("Sec-Fetch-Site"), "status": None}
                posts.append(rec)
                try:
                    resp = await handler(request)
                except web.HTTPException as e:
                    rec["status"] = e.status
                    raise
                rec["status"] = resp.status
                return resp
            return await handler(request)
        app = web.Application(middlewares=[spy])
        self.s.add_routes(app)
        self.server = TestServer(app, host="127.0.0.1")
        await self.server.start_server()
        self.base = f"http://127.0.0.1:{self.server.port}"
        self.td = tempfile.mkdtemp(prefix="kairos-cdp-")
        self.port = 9300 + (os.getpid() + int(time.time())) % 600
        self.br = subprocess.Popen([chrome, "--headless=new", "--no-sandbox", "--disable-gpu", "--no-first-run",
                                    "--no-default-browser-check", "--hide-scrollbars", f"--remote-debugging-port={self.port}",
                                    f"--user-data-dir={self.td}/profile", f"--window-size={self.w},{self.h}",
                                    "--font-render-hinting=none", "about:blank"],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.page = CDP(self.port)
        await self.page.open(self.base + "/" + self.query, self.w, self.h)
        await self.page.wait('!!document.querySelector("#desktop") && !document.querySelector("#desktop").classList.contains("hidden")',
                             20, "the desktop")
        return self.page

    async def __aexit__(self, *exc):
        try:
            await self.page.close()
        finally:
            self.br.kill()
            self.br.wait()
            self.s.wm.WM.close_streams()
            await self.server.close()
            shutil.rmtree(self.td, ignore_errors=True)


DOCK_STATE = """JSON.stringify([...document.querySelectorAll('#dock .dock-app')].map((b) => ({
  key: b.dataset.key, cls: [...b.classList].filter((c) => c !== 'dock-app'), win: +b.dataset.windows || 0,
  t: getComputedStyle(b).transform, glow: getComputedStyle(b).boxShadow !== 'none', more: b.dataset.more || null,
  shadow: getComputedStyle(b).boxShadow, top: b.getBoundingClientRect().top, w: b.getBoundingClientRect().width,
  label: b.querySelector('span') ? getComputedStyle(b.querySelector('span')).top : null })))"""
# the dock's CSS transitions (.15 s) finished: a computed transform / box is final only then
DOCK_SETTLED = ("document.getAnimations().filter((a) => a.effect && a.effect.target && a.effect.target.closest && "
                "a.effect.target.closest('#dock')).length === 0")
BRIGHT = "rgba(65, 226, 255, 0.95)"     # the focused icon's glow ring (.shown.active); the plain glow has none


async def dock(page):
    await page.wait(DOCK_SETTLED, 3, "the dock's transitions finished")
    return {d["key"]: d for d in json.loads(await page.ev(DOCK_STATE))}


def dock_state_is(d, key, state):
    """[R81, owner's correction 2026-10-24] closed 1x / no glow; minimized 1.33 / no glow; shown (a window on screen,
    maximized or not) 1.33 + glow; active (the focused one) 1.33 + the brighter glow. Size checked twice: the computed
    transform and the icon's box (52 px x 1.33 = 69.2)"""
    i = d[key]
    cls, t, big = set(i["cls"]), i["t"], abs(i["w"] - 52 * 1.33) <= 0.6
    if state == "closed":
        ok = not cls & {"running", "minimized", "shown", "active"} and t in ("none", "matrix(1, 0, 0, 1, 0, 0)") \
            and abs(i["w"] - 52) <= 0.6 and not i["glow"]
    elif state == "minimized":
        ok = {"running", "minimized"} <= cls and not cls & {"shown", "active"} and t.startswith("matrix(1.33, 0, 0, 1.33") \
            and big and not i["glow"] and i["label"] == "-30px"
    elif state == "shown":
        ok = {"running", "shown"} <= cls and not cls & {"minimized", "active"} and t.startswith("matrix(1.33, 0, 0, 1.33") \
            and big and i["glow"] and BRIGHT not in i["shadow"] and i["label"] == "-30px"
    elif state == "active":
        ok = {"running", "shown", "active"} <= cls and "minimized" not in cls and t.startswith("matrix(1.33, 0, 0, 1.33") \
            and big and i["glow"] and BRIGHT in i["shadow"] and i["label"] == "-30px"
    else:
        raise ValueError(state)
    assert ok, (key, state, i)
    return i


@test("dock_e2e_records_states", ["R81", "R61", "R68", "R52"])
async def t_dock_e2e(ctx):
    """the real shell at 1920×1080: the dock has no Files/Notes/News and the built-in 🌐; the user's apps follow (their
    icons); a click opens an app (1.33 + the bright glow, active), a click on the active one minimizes it (1.33, no glow,
    no chip), a click restores it; built-ins the same, right-click → New window / Close; a game from the X feed gets an
    icon (minimized = 1.33, click → POST raise, no chip) while an X window that is no game keeps its chip; [owner's
    correction 2026-10-24] the four states' computed transform + glow side by side (closed 1x / none, minimized 1.33 /
    none, shown 1.33 / glow, active 1.33 / bright glow) and their hover (running 1.42 - 6 px, closed 1.12 - 8 px); a quick
    viewer keeps its chip; a maximized window: top at the menu bar, bottom above every grown icon (hovered too);
    ⊞ lists Notes/News/Files; 1024 px with 7 running icons: the apps overflow into ⊞ (+N), the dock and every grown icon
    on the screen, none overlapping; "Remove from dock" persists; [R90] one page over two monitors (?mon=): ONE dock,
    on the primary, the X chips on it"""
    s = kernel()
    for d in list(s.APPS_DIR.glob("*")):
        shutil.rmtree(d, ignore_errors=True)
    users = ["pomodoro-timer", "pixel-paint", "weather-orb", "synth-pad", "star-map"]
    seed_apps(s, users + ["notes", "browser"])
    with Env(s) as env:
        desk = Desk(s, env)
        async with desk as page:
            await page.wait("document.querySelectorAll('#dock .dock-app[data-app=\"pomodoro-timer\"]').length === 1", 10, "user apps on the dock")
            d = await dock(page)
            for gone in ("app:notes", "app:news", "app:files", "app:browser"):
                assert gone not in d, (gone, list(d))
            for k in ["menu", "builtin:applauncher", "launch", "builtin:browser", "builtin:imagine", "builtin:motion",
                      "builtin:arcade", "builtin:melody", "app:settings"] + [f"app:{u}" for u in users]:
                assert k in d, (k, list(d))
            keys = list(d)
            assert keys.index("app:settings") < keys.index("app:pomodoro-timer"), keys
            # [fix 2026-10-24] the icon's <img> may still be loading at this instant (R91 suite run 2 read naturalWidth 0
            # once): wait for the load, bounded, then judge the decoded size
            icon = 'document.querySelector(\'#dock [data-app="pomodoro-timer"] img\')'
            await page.wait(f"{icon} && {icon}.complete && {icon}.naturalWidth === 48", 3, "the app's dock icon loaded (48 px)")
            # an app: open (1.33 + the bright glow, active) → minimize (1.33, no glow, no chip) → restore
            dock_state_is(await dock(page), "app:pomodoro-timer", "closed")
            await page.click_sel('#dock [data-app="pomodoro-timer"]')
            await page.wait('windowRecords().some((r) => r.key === "app:pomodoro-timer")', 10, "the app window")
            await page.wait('document.querySelector(\'#dock [data-app="pomodoro-timer"]\').classList.contains("active")', 5)
            await page.away()
            dock_state_is(await dock(page), "app:pomodoro-timer", "active")
            await page.click_sel('#dock [data-app="pomodoro-timer"]')
            await page.wait('document.querySelector(\'#dock [data-app="pomodoro-timer"]\').classList.contains("minimized")', 5)
            await page.away()
            dock_state_is(await dock(page), "app:pomodoro-timer", "minimized")
            assert await page.ev("document.querySelectorAll('#taskbar .task-chip').length") == 0, "no chip for a window with an icon"
            await page.click_sel('#dock [data-app="pomodoro-timer"]')
            await page.wait('!document.querySelector(\'#dock [data-app="pomodoro-timer"]\').classList.contains("minimized")', 5)
            assert await page.ev('windowRecords().find((r) => r.key === "app:pomodoro-timer").active') is True
            # a built-in: open, minimize by click, right-click → New window, Close all
            await page.click_sel('#dock [data-builtin="imagine"]')
            await page.wait('windowRecords().filter((r) => r.key === "builtin:imagine").length === 1', 5)
            await page.click_sel('#dock [data-builtin="imagine"]')
            await page.wait('document.querySelector(\'#dock [data-builtin="imagine"]\').classList.contains("minimized")', 5)
            await page.click_sel('#dock [data-builtin="imagine"]', "right")
            await page.wait('!document.querySelector("#dock-menu").classList.contains("hidden")', 5)
            items = await page.ev('[...document.querySelectorAll("#dock-menu .dm-item")].map((b) => b.textContent)')
            assert items == ["New window", "Close"], items
            await page.click_sel("#dock-menu .dm-item")
            await page.wait('document.querySelector(\'#dock [data-builtin="imagine"]\').dataset.windows === "2"', 5)
            await page.away()
            d = await dock(page)
            assert "shown" in d["builtin:imagine"]["cls"] and d["builtin:imagine"]["win"] == 2, d["builtin:imagine"]
            # the browser (builtin) open + a user app minimized + Imagine shown + a minimized game: the owner's screenshot
            await page.click_sel('#dock [data-app="pixel-paint"]')
            await page.wait('windowRecords().some((r) => r.key === "app:pixel-paint")', 10)
            await page.click_sel('#dock [data-app="pixel-paint"]')
            await page.wait('document.querySelector(\'#dock [data-app="pixel-paint"]\').classList.contains("minimized")', 5)
            await page.ev('renderXWindows([{id: 77, kind: "game", app: "pizza-asteroids", title: "Pizza Asteroids", minimized: true, active: false},'
                          ' {id: 78, kind: "other", app: null, title: "a captive portal sign-in", minimized: false, active: false}]), 1')
            await page.wait('!!document.querySelector(\'#dock [data-game="pizza-asteroids"]\')', 5, "the game's dock icon")
            await page.away()
            await asyncio.sleep(0.3)
            d = await dock(page)
            g = d["game:pizza-asteroids"]
            assert "minimized" in g["cls"] and g["t"].startswith("matrix(1.33"), g
            chips = await page.ev('[...document.querySelectorAll("#taskbar .task-chip")].map((c) => c.dataset.xid || c.dataset.win)')
            assert chips == ["78"], f"only the X window without an icon has a chip: {chips}"
            await page.click_sel('#dock [data-game="pizza-asteroids"]')
            await asyncio.sleep(0.4)
            assert desk.ops == ["/api/windows/77/raise"], f"a click on the game's icon raises it through the kernel: {desk.ops}"
            await page.away()
            shot1 = await page.shot("dock-states-1920.png")
            # [R81, owner's correction 2026-10-24] the four states side by side, each one's computed size and glow:
            # Weather Orb closed, Pixel Paint minimized, Imagine shown (a window on screen, not focused), Pomodoro active
            await page.ev('(() => { restoreWin([...windows.values()].find((w) => w.appId === "pomodoro-timer")); return 1; })()')
            await page.wait('document.querySelector(\'#dock [data-app="pomodoro-timer"]\').classList.contains("active")', 5)
            await page.away()
            d = await dock(page)
            four = {"app:weather-orb": "closed", "app:pixel-paint": "minimized", "builtin:imagine": "shown",
                    "app:pomodoro-timer": "active"}
            for k, st in four.items():
                dock_state_is(d, k, st)
            dock_state_is(d, "game:pizza-asteroids", "minimized")                  # a game's icon alike
            # hover: a shown or active icon lifts like a minimized one (translateY(-6px) scale(1.42)); a closed one as before
            for k, want in (("builtin:imagine", "matrix(1.42, 0, 0, 1.42, 0, -6)"), ("app:pomodoro-timer", "matrix(1.42, 0, 0, 1.42, 0, -6)"),
                            ("app:pixel-paint", "matrix(1.42, 0, 0, 1.42, 0, -6)"), ("app:weather-orb", "matrix(1.12, 0, 0, 1.12, 0, -8)")):
                c = await page.center(f'#dock [data-key="{k}"]')
                await page.send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": c[0], "y": c[1]})
                await page.wait(f"getComputedStyle(document.querySelector('#dock [data-key=\"{k}\"]')).transform === {json.dumps(want)}",
                                3, f"{k} hovered: {want}")
            await page.away()
            d = await dock(page)
            for k, st in four.items():
                dock_state_is(d, k, st)
            shot_four = await page.shot("dock-four-states-1920.png")
            dk = await page.ev("(() => { const r = document.querySelector('#dock').getBoundingClientRect(); return [r.left, r.top, r.width, r.height]; })()")
            shot_four_dock = await page.shot("dock-four-states-1920-closeup.png",
                                             [max(0, dk[0] - 30), dk[1] - 70, dk[2] + 60, min(1080 - (dk[1] - 70), dk[3] + 90)])
            # a quick viewer keeps its chip when minimized
            await page.ev('fileQuick("/data/readme.txt"), 1')
            await page.wait('[...windows.values()].some((w) => w.viewer)', 5)
            await page.ev('minimizeWin([...windows.values()].find((w) => w.viewer)), 1')
            await page.wait('[...document.querySelectorAll("#taskbar .task-chip")].some((c) => c.dataset.win)', 5, "the viewer's chip")
            # a maximized window: its top under the menu bar, its bottom above every grown icon (its own active one, the
            # shown Imagine, the minimized ones) — also above a hovered one (1.42, -6 px); dockRoom() reserves the overhang
            await page.ev('(() => { const w = [...windows.values()].find((w) => w.appId === "pomodoro-timer"); restoreWin(w); w.el.querySelector(".win-max").click(); return 1; })()')
            await page.away()
            dock_state_is(await dock(page), "app:pomodoro-timer", "active")
            box_js = ('(() => { const w = [...windows.values()].find((w) => w.appId === "pomodoro-timer").el.getBoundingClientRect();'
                      ' const tops = [...document.querySelectorAll("#dock .dock-app.running")].map((b) => b.getBoundingClientRect().top);'
                      ' return {top: w.top, bottom: w.bottom, menu: document.querySelector("#menubar").getBoundingClientRect().bottom,'
                      ' room: dockRoom(), dock: document.querySelector("#dock").getBoundingClientRect().top, grown: Math.min(...tops),'
                      ' n: tops.length, own: document.querySelector(\'#dock [data-app="pomodoro-timer"]\').getBoundingClientRect().top}; })()')
            box = await page.ev(box_js)
            assert box["n"] >= 4 and abs(box["top"] - box["menu"]) <= 1 and abs(box["bottom"] - (1080 - box["room"])) <= 1, box
            assert box["grown"] < box["dock"] - 4 and box["grown"] - box["bottom"] >= 0 and box["own"] - box["bottom"] >= 0, \
                f"a grown icon overlaps the maximized window: {box}"
            c = await page.center('#dock [data-app="pomodoro-timer"]')
            await page.send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": c[0], "y": c[1]})
            await page.wait(DOCK_SETTLED, 3)
            hov = await page.ev(box_js)
            assert hov["own"] < box["own"] and hov["own"] - hov["bottom"] >= 0, f"the hovered icon overlaps the maximized window: {hov}"
            await page.away()
            gap = await page.ev('(() => { const w = [...windows.values()].find((w) => w.appId === "pomodoro-timer").el.getBoundingClientRect();'
                                ' const g = document.querySelector(\'#dock [data-game="pizza-asteroids"]\').getBoundingClientRect(); return g.top - w.bottom; })()')
            assert gap >= 0, f"the enlarged icon overlaps the maximized window by {-gap} px"
            # ⊞ Apps: the system apps are there
            await page.click_sel('#dock [data-menu="1"]')
            await page.wait('document.querySelectorAll("#appsmenu-grid .appsmenu-system").length >= 5', 5)
            sysapps = await page.ev('[...document.querySelectorAll("#appsmenu-grid .appsmenu-system")].map((b) => b.dataset.sysapp)')
            assert sysapps[:5] == ["browser", "settings", "notes", "news", "files"] or set(sysapps) >= {"browser", "notes", "news", "files", "settings"}, sysapps
            grid = await page.ev('[...document.querySelectorAll("#appsmenu-grid .appsmenu-item:not(.appsmenu-system) .appsmenu-name")].map((n) => n.textContent)')
            assert "browser" not in grid and "notes" not in grid and "pomodoro timer" in grid, grid
            await page.ev('hideAppsMenu(), 1')
            # remove from dock (persisted in the OS store), then it is back when asked
            await page.click_sel('#dock [data-app="star-map"]', "right")
            await page.wait('!document.querySelector("#dock-menu").classList.contains("hidden")', 5)
            items = await page.ev('[...document.querySelectorAll("#dock-menu .dm-item")].map((b) => b.textContent)')
            assert items == ["Open", "Remove from dock"], items
            await page.ev('[...document.querySelectorAll("#dock-menu .dm-item")].find((b) => b.textContent === "Remove from dock").click(), 1')
            await page.wait('!document.querySelector(\'#dock [data-app="star-map"]\')', 5)
            await asyncio.sleep(0.3)
            stored = s._load_store().get("shell::dock.hidden")
            assert stored == ["star-map"], stored
            # 1024 px: fewer apps fit, the rest are counted on ⊞. [R81, 2026-10-24] with many open apps — every running
            # icon is 1.33 now, the pinned built-ins too: open Motion, Arcade, Melody and Synth Pad as well (7 running
            # icons + the minimized game) — the dock and every grown icon stay on the screen, no two icons overlap
            for sel in ('[data-builtin="motion"]', '[data-builtin="arcade"]', '[data-builtin="melody"]', '[data-app="synth-pad"]'):
                await page.click_sel(f"#dock {sel}")
                await page.wait(f"document.querySelector('#dock {sel}').classList.contains('running')", 10, f"{sel} running")
            for i in range(14):
                seed_apps(s, [f"extra-app-{i:02d}"])
            await page.ev("dockLoadApps(), 1")
            await page.size(1024, 768)
            await asyncio.sleep(0.6)
            await page.away()
            wd = await page.ev("(() => { const r = document.querySelector('#dock').getBoundingClientRect(); return [r.left, r.right, innerWidth]; })()")
            assert wd[0] >= 0 and wd[1] <= wd[2], f"the dock does not fit 1024 px: {wd}"
            d = await dock(page)
            more = d["menu"]["more"]
            assert more and int(more[1:]) >= 10, more
            grown = [k for k, i in d.items() if "running" in i["cls"]]
            assert len(grown) >= 7, grown
            for k in grown:
                dock_state_is(d, k, "minimized" if "minimized" in d[k]["cls"] else "active" if "active" in d[k]["cls"] else "shown")
            boxes = sorted(await page.ev("[...document.querySelectorAll('#dock .dock-app')].map((b) => { const r = b.getBoundingClientRect();"
                                         " return [r.left, r.right, b.dataset.key]; })"))
            assert boxes[0][0] >= 0 and boxes[-1][1] <= wd[2], f"a grown icon sticks out of the 1024 px screen: {boxes[0]} {boxes[-1]}"
            touch = [(a[2], b[2]) for a, b in zip(boxes, boxes[1:]) if a[1] > b[0] + 0.5]
            assert not touch, f"grown icons overlap: {touch}"
            shot2 = await page.shot("dock-1024-overflow.png")
            await page.size(1920, 1080)
    # [R90] one desktop page over two monitors (the kiosk's ?mon= hint; this kernel has no X, so the hint stands): ONE
    # dock, centred on the primary; the X feed's chips are on it (no second page any more)
    with Env(s) as env:
        async with Desk(s, env, 2560, 720, "?mon=eDP:0,0,1280,720,1;DP-1:1280,0,1280,720,0") as page:
            await page.wait("document.querySelectorAll('#dock .dock-app[data-builtin=\"browser\"]').length === 1", 10)
            wd = await page.ev("(() => { const r = document.querySelector('#dock').getBoundingClientRect(); renderXWindows("
                               "[{id: 79, kind: 'other', app: null, title: 'portal', minimized: false, active: false}], []); "
                               "const p = MON.list[0]; return [r.left - p.x, r.right - p.x, MON.list.length, "
                               "document.querySelectorAll('#dock').length, document.querySelectorAll('#taskbar .task-chip').length, "
                               "p.w, p.primary]; })()")
            # positions relative to the primary as the page sees it (the hint is in root px; a headless window may sit off 0,0)
            assert wd[0] >= 0 and wd[1] <= 1280 and abs((wd[0] + wd[1]) / 2 - 640) <= 1 and wd[2:] == [2, 1, 1, 1280, True], wd
    return (f"1920: pinned + {len(users)} user apps, no Files/Notes/News/generated browser; states closed 1x/min 1.33/shown "
            f"1.33+glow/active 1.33+bright, hover 1.42; app active→min (no chip)→restore; builtin New window/Close; game icon "
            f"1.33, X non-game chip only; viewer chip; maximized below the menu bar, above every grown icon (hovered too); "
            f"⊞ system apps; remove from dock persisted; 1024 + {len(grown)} running: fits, ⊞ {more}, no overlap; 2 monitors: "
            f"one dock on the primary + the X chip" + (f"; shots {shot1} {shot_four} {shot_four_dock}" if SHOTS else ""))


@test("browser_e2e_search_opens_addresses", ["R83", "R23"])
async def t_search_e2e(ctx):
    """the desktop search in the real shell: 'oppenheimer.com' + Enter opens the built-in browser on that address (the
    kernel's page, its report: state page + title); 'example.com red bikes' is a site search; a website hit of the
    results (🌐 browse the imaginary web for …) opens in the browser too, in the same window; browser.js webAddress()
    agrees with the kernel's web_address() on the whole table"""
    s = kernel()
    with Env(s) as env:
        env.big = False
        async with Desk(s, env) as page:
            js = await page.ev(f"JSON.stringify({json.dumps([t for t, _ in ADDRESS_TABLE])}.map((t) => webAddress(t)))")
            js = json.loads(js)
            bad = [(t, got, want) for (t, want), got in zip(ADDRESS_TABLE, js) if got != want]
            assert not bad, f"browser.js disagrees with the kernel: {bad}"
            await page.click_sel("#ds-input")
            await page.type("oppenheimer.com")
            await page.wait('document.querySelector("#ds-results .ds-group") && document.querySelector("#ds-results .ds-group").textContent === "web"', 8,
                            "the address as the first result")
            await page.key("Enter")
            await page.wait("[...windows.values()].some((w) => w.br && w.br.report && w.br.report.state === 'page')", 15, "the browser shows the page")
            st = json.loads(await page.ev("JSON.stringify((() => { const w = [...windows.values()].find((w) => w.br); return "
                                          "{addr: w.br.el.addr.value, rep: w.br.report, title: w.title, key: dockKeyOf(w), n: windows.size}; })())"))
            assert st["addr"] == "oppenheimer.com" and st["rep"]["address"] == "oppenheimer.com" and st["key"] == "builtin:browser", st
            assert st["title"].startswith("🌐 Oppenheimer | oppenheimer.com"), st
            d = await dock(page)
            assert {"shown", "active"} <= set(d["builtin:browser"]["cls"]), d["builtin:browser"]
            await page.shot("browser-on-a-site.png")
            # an address with words: the site's search
            await page.ev('dsInput.focus(), dsInput.value = "", 1')       # the browser window covers the search box now
            await page.type("example.com red bikes")
            await page.key("Enter")
            await page.wait("[...windows.values()].find((w) => w.br).br.report && [...windows.values()].find((w) => w.br).br.report.address === 'example.com/search?q=red bikes'", 15)
            assert any(p["user"].startswith("The browser navigated to: example.com/search?q=red bikes") for p in env.prompts)
            # a website hit among the results: the same browser window
            await page.ev('dsInput.focus(), dsInput.value = "", 1')
            await page.type("zzq moon bakery")
            pick = '[...document.querySelectorAll("#ds-results .ds-item")].find((i) => i._action && i._action.type === "page" && i._action.address === "zzq moon bakery")'
            await page.wait(f"!!{pick}", 8, "the website hit for the query")
            await asyncio.sleep(0.3)                                   # no newer keystroke result replaces the list
            hit = await page.ev(f'(() => {{ const i = {pick}; const r = i.getBoundingClientRect(); return [r.x + 40, r.y + r.height / 2]; }})()')
            await page.click(*hit)
            await page.wait("[...windows.values()].find((w) => w.br).br.report && [...windows.values()].find((w) => w.br).br.report.address === 'zzq moon bakery'", 15)
            assert await page.ev("[...windows.values()].filter((w) => w.br).length") == 1, "one browser window, reused"
            hist = await page.ev("[...windows.values()].find((w) => w.br).br.stack.map((e) => e.address)")
            assert hist == ["oppenheimer.com", "example.com/search?q=red bikes", "zzq moon bakery"], hist
    return "webAddress == web_address on the table; Enter on an address → browser (page report, dock glow); site search; website hit → same window"


@test("browser_e2e_navigation_history", ["R83", "R57", "R75"])
async def t_nav_e2e(ctx):
    """the browser's own chrome: a link clicked INSIDE the sandboxed page (real mouse event) loads the next page — the
    loading state shows while the kernel writes it ('leaving', then 'loading' reports, the progress bar), the entry is
    pushed; ← and → walk the entries, ↻ reloads from the cache (no new page prompt), Shift-free; the history panel lists
    the pages; the address bar navigates; declined / blocked / cut pages show their note bar; a picture that arrives
    late appears in the page without a reload (the late-picture script runs in the browser's frame)"""
    s = kernel()
    with Env(s, delay=1.2, paint_delay=2.5) as env:
        env.pages["late-pics.kai"] = site_page("late-pics.kai", pictures=["a lighthouse at dawn"],
                                               extra="<script>setInterval(() => { const i = document.querySelector('img');"
                                                     " parent.postMessage({rep: 'late', w: i.complete ? i.naturalWidth : -1,"
                                                     " src: i.getAttribute('src'), born: performance.timeOrigin}, '*'); }, 200);</script>")
        async with Desk(s, env) as page:
            await page.ev('window.__states = []; const _r = browserReport; browserReport = (w, r) => { window.__states.push(r.state); return _r(w, r); };'
                          ' window.__late = []; addEventListener("message", (e) => { if (e.data && e.data.rep === "late") window.__late.push(e.data); }); 1')
            await page.ev('openBrowser("site.kai"), 1')
            win = "[...windows.values()].find((w) => w.br)"
            await page.wait(f"{win}.br.report && {win}.br.report.state === 'page'", 15, "site.kai")
            states0 = await page.ev("window.__states.slice()")
            assert "loading" in states0, states0
            n_prompts = len(env.prompts)
            box = await page.ev(f"(() => {{ const r = {win}.iframe.getBoundingClientRect(); return [r.x, r.y]; }})()")
            await page.click(box[0] + 60, box[1] + 30)                 # the page's BIG LINK (top-left, inside the frame)
            await page.wait(f"{win}.br.loading === true", 5, "the loading state after the click")
            await page.wait(f"!{win}.br.el.progress.classList.contains('hidden')", 2)
            await page.wait(f"{win}.br.report && {win}.br.report.address === 'site.kai/about'", 15, "the linked page")
            states = await page.ev("window.__states.slice()")
            assert "leaving" in states and states[-1] == "page", states
            nav = json.loads(await page.ev(f"JSON.stringify({{stack: {win}.br.stack.map((e) => e.address), idx: {win}.br.idx,"
                                           f" back: {win}.br.el.back.disabled, fwd: {win}.br.el.fwd.disabled, addr: {win}.br.el.addr.value}})"))
            assert nav == {"stack": ["site.kai", "site.kai/about"], "idx": 1, "back": False, "fwd": True, "addr": "site.kai/about"}, nav
            await page.click_sel(".br-back")
            await page.wait(f"{win}.br.report && {win}.br.report.address === 'site.kai'", 10)
            assert await page.ev(f"{win}.br.el.fwd.disabled") is False
            await page.click_sel(".br-fwd")
            await page.wait(f"{win}.br.report && {win}.br.report.address === 'site.kai/about'", 10)
            n = len(env.prompts)
            await page.click_sel(".br-reload")
            await asyncio.sleep(0.3)
            await page.wait(f"{win}.br.report && {win}.br.report.state === 'page'", 10)
            assert len(env.prompts) == n, "a reload is served from the cache"
            assert len([p for p in env.prompts[n_prompts:] if p["user"].startswith("The browser navigated to: site.kai/about")]) == 1
            await page.click_sel(".br-hist")
            hp = await page.ev('[...document.querySelectorAll(".br-hp-item small")].map((s) => s.textContent)')
            assert hp[:2] == ["site.kai/about", "site.kai"], hp
            await page.click_sel(".br-hist")
            # the address bar
            await page.click_sel(".br-addr")
            await page.type("late-pics.kai")
            await page.key("Enter")
            await page.wait(f"{win}.br.report && {win}.br.report.address === 'late-pics.kai' && {win}.br.report.state === 'page'", 15)
            await page.wait("window.__late.length && window.__late[window.__late.length - 1].w % 2 === 0 && window.__late[window.__late.length - 1].w > 0", 30,
                            "the late picture in the browser's frame")
            late = await page.ev("window.__late.map((l) => [l.w, l.src, l.born])")
            assert any(w > 0 and w % 2 == 1 for w, _, _ in late), f"the pending placeholder first: {late[:12]}"
            assert "_r=" in late[-1][1] and len({b for _, _, b in late}) == 1, "the picture came by the re-ask, no reload"
            # the note bar: declined / blocked / cut
            for addr, state, cls, text in (("declineme.kai", "declined", "br-note-declined", "every available model"),
                                           ("blockme.kai", "blocked", "br-note-blocked", "sexual content involving minors"),
                                           ("cutme.kai", "cut", "br-note-cut", "cut off")):
                await page.ev(f'browserNav({win}, {{address: {json.dumps(addr)}}}), 1')
                await page.wait(f"{win}.br.report && {win}.br.report.address === {json.dumps(addr)} && {win}.br.report.state === {json.dumps(state)}", 20, addr)
                note = json.loads(await page.ev(f"JSON.stringify([{win}.br.el.note.className, {win}.br.el.note.textContent])"))
                assert cls in note[0] and text in note[1], (addr, note)
    return ("link inside the frame → leaving/loading/page reports + progress bar; ←/→/↻ (cache, no prompt); history panel; "
            "address bar; late picture placeholder→picture in the frame, one document; notes declined/blocked/cut")


@test("browser_e2e_improve_revert", ["R84", "R83", "R75"])
async def t_improve_e2e(ctx):
    """✨ in the browser's title bar: the sticky note takes 'make it purple', the page's next version streams in the
    code view and loads (v2, the version strip v1 v2), the prompt carried the previous HTML + the site profile + the
    instruction; ← shows v1 again (?_v=1), → v2; a second ✨ makes v3; a click on v1 in the strip makes it the page
    again (the kernel serves v1, all three kept)"""
    s = kernel()
    with Env(s) as env:
        env.big = False
        async with Desk(s, env) as page:
            await page.ev('openBrowser("sunrise-bakery.kai"), 1')
            win = "[...windows.values()].find((w) => w.br)"
            await page.wait(f"{win}.br.report && {win}.br.report.state === 'page'", 15)
            await page.ev(f'(() => {{ const w = {win}; w.el.querySelector(".win-max").click(); return 1; }})()')
            await page.click_sel(".browser-win .win-improve")
            await page.wait('!!document.querySelector(".browser-win .sticky-note")', 5, "the sticky note")
            await page.click_sel(".browser-win .sticky-text")
            await page.type("make it purple")
            await page.shot("browser-improve-note.png")
            await page.click_sel(".browser-win .sticky-go")
            await page.wait('!!document.querySelector(".br-gen")', 5, "the code view")
            await page.wait(f"{win}.br.report && {win}.br.report.version === 2", 15, "v2 loaded")
            await page.wait('!document.querySelector(".br-gen")', 5)
            st = json.loads(await page.ev(f"JSON.stringify({{title: {win}.br.report.title, strip: [...document.querySelectorAll('.br-ver')].map((b) => b.className.replace('br-ver', '').trim() + ':' + b.textContent), stack: {win}.br.stack}})"))
            assert st["title"].endswith("· v2"), st
            assert st["strip"] == [":v1", "shown current:v2"], st
            u = env.streams[-1]["user"]
            assert "USER IMPROVEMENT REQUEST: make it purple" in u and "Site name / brand (use exactly): Sunrise Bakery" in u and "PREVIOUS VERSION (v1) HTML:" in u
            shot = await page.shot("browser-improved-versions.png")
            await page.click_sel(".br-back")
            await page.wait(f"{win}.br.report && {win}.br.report.version === 1", 10, "← shows v1")
            assert await page.ev("document.querySelector('.br-ver.shown').textContent") == "v1"
            await page.click_sel(".br-fwd")
            await page.wait(f"{win}.br.report && {win}.br.report.version === 2", 10)
            await page.click_sel(".browser-win .win-improve")
            await page.wait('!!document.querySelector(".browser-win .sticky-note")', 5)
            await page.click_sel(".browser-win .sticky-text")
            await page.type("add a FAQ section")
            await page.key("Enter")
            await page.wait(f"{win}.br.report && {win}.br.report.version === 3", 15, "v3")
            await page.wait("document.querySelectorAll('.br-ver').length === 3", 5)
            await page.click_sel('.br-ver[data-v="1"]')
            await page.wait(f"{win}.br.report && {win}.br.report.version === 1 && {win}.br.versions.current === 1", 10, "the revert")
            strip = await page.ev("[...document.querySelectorAll('.br-ver')].map((b) => b.className.replace('br-ver', '').trim() + ':' + b.textContent)")
            assert strip == ["shown current:v1", ":v2", ":v3"], strip
            html = (s.WEB_DIR / f"{s.web_slug('sunrise-bakery.kai')}.html").read_text()
            assert "improved" not in html and len(s.load_web_versions("sunrise-bakery.kai")["versions"]) == 3
    return "✨ note → stream → v2 (strip v1 v2); prompt = previous HTML + profile + instruction; ←v1 →v2; v3; strip click → v1 is the page" \
        + (f"; shot {shot}" if SHOTS else "")


# the review's probe 4 page script: the bridge first, then the kernel directly (a JSON string body = text/plain, a beacon)
F1_PAGE = """<script>
parent.postMessage({vibe:true,id:41,method:"notify",args:["BRIDGE-FROM-WEB-PAGE"]},"*");
parent.postMessage({vibe:true,id:42,method:"store",args:["f1-bridge","reached"]},"*");
fetch("/api/store",{method:"POST",body:JSON.stringify({key:"f1-web-fetch",value:"reached"})}).catch(function(){});
fetch("/api/game/version",{method:"POST",body:JSON.stringify({slug:"nosuch",version:1})}).catch(function(){});
try{navigator.sendBeacon("/api/store",JSON.stringify({key:"f1-web-beacon",value:"reached"}))}catch(e){}
</script>"""


@test("browser_e2e_sandboxed_page_cannot_write", ["R66", "R83"])
async def t_sandbox_e2e(ctx):
    """[review v7.5-2 F1] the review's Chromium probe as a test: a generated /web/ page in the built-in browser (sandboxed,
    opaque origin; the kiosk is loopback) tries the vibe.* bridge and then the kernel directly. The bridge stays closed
    to it (R83: no toast, no bridge store write); its own fetch() POSTs and its sendBeacon() reach the kernel with
    Origin: null + Sec-Fetch-Site cross-site and get 403 — nothing stored, the game handler never runs. An app window's
    frame still reaches the bridge, whose store write (the shell's same-origin POST) gets 200, and so does the shell's
    own POST"""
    s = kernel()
    with Env(s) as env:
        addr = "f1-sandbox.kai"
        (s.WEB_DIR / f"{s.web_slug(addr)}.html").write_text(site_page(addr, extra=F1_PAGE, big=False))
        desk = Desk(s, env)
        async with desk as page:
            # the control: an app-like window (a sandboxed srcdoc frame registered as win.iframe, like every app)
            await page.ev("""(() => { const w = createWindow("f1-control"); const f = document.createElement("iframe");
                f.sandbox = "allow-scripts"; f.srcdoc = "<script>parent.postMessage({vibe:true,id:7,method:'notify',args:['CTL-BRIDGE']},'*');"
                  + "parent.postMessage({vibe:true,id:8,method:'store',args:['f1-ctl','ok']},'*')<\\/script>";
                w.iframe = f; setBody(w, f); return true; })()""")
            await page.wait('[...document.querySelectorAll("#toasts .toast")].some((t) => t.textContent.includes("CTL-BRIDGE"))',
                            10, "the control's toast")
            t0 = time.time()
            while not any(k.endswith("::f1-ctl") for k in s._load_store()) and time.time() - t0 < 10:
                await asyncio.sleep(0.1)
            n0 = len(desk.posts)
            await page.ev(f"openBrowser({json.dumps(addr)}), 1")
            await page.wait('[...windows.values()].some((w) => w.br && w.br.report && w.br.report.state === "page")', 20,
                            "the page's report")
            t0 = time.time()
            while len([r for r in desk.posts[n0:] if r["origin"] == "null"]) < 3 and time.time() - t0 < 10:
                await asyncio.sleep(0.1)
            await asyncio.sleep(1.0)                                      # a bridge toast would be up by now
            shell = await page.ev('fetch("/api/store", {method: "POST", headers: {"Content-Type": "application/json"}, '
                                  'body: JSON.stringify({key: "f1-shell", value: "ok"})}).then((r) => r.status)')
            toasts = await page.ev('[...document.querySelectorAll("#toasts .toast")].map((t) => t.textContent)')
    store = s._load_store()
    framed = [r for r in desk.posts[n0:] if r["origin"] == "null"]
    assert sorted(r["path"] for r in framed) == ["/api/game/version", "/api/store", "/api/store"], framed
    assert all(r["status"] == 403 and r["site"] == "cross-site" for r in framed), framed
    assert not {"f1-web-fetch", "f1-web-beacon"} & set(store), sorted(store)
    assert not any("BRIDGE-FROM-WEB-PAGE" in t for t in toasts) and not any(k.endswith("f1-bridge") for k in store), \
        (toasts, sorted(store))
    ctl = [k for k in store if k.endswith("::f1-ctl")]
    assert ctl and store[ctl[0]] == "ok", f"the app frame's bridge store write: {sorted(store)}"
    assert shell == 200 and store.get("f1-shell") == "ok", (shell, store.get("f1-shell"))
    own = [r for r in desk.posts if r["path"] == "/api/store" and r["origin"] != "null"]
    assert own and all(r["status"] == 200 and r["site"] == "same-origin" for r in own), own
    return (f"page POSTs {', '.join(r['path'] + ' ' + str(r['status']) for r in framed)} (Origin null, cross-site), store "
            f"clean; bridge closed to the page; app frame → bridge store 200; shell's own /api/store ×{len(own)} 200 (same-origin)")


def main():
    tests = [v for v in globals().values() if getattr(v, "_test", False)]
    only = [x.strip() for x in os.environ.get("KAIROS_TEST_ONLY", "").split(",") if x.strip()]
    if only:
        tests = [t for t in tests if any(o in t._name for o in only)]
    print(f"kAIrOS dock + browser tests (R81/R83/R84) — {len(tests)} tests", flush=True)
    for t in tests:
        t({})
    if not only:
        out = {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "tests": RESULTS}
        (REPO / "tests" / "results-dock-browser.json").write_text(json.dumps(out, indent=1))
    fails = [n for n, r in RESULTS.items() if r["status"] == "fail"]
    passes = sum(1 for r in RESULTS.values() if r["status"] == "pass")
    skips = sum(1 for r in RESULTS.values() if r["status"] == "skip")
    print(f"\n{passes} passed, {len(fails)} failed, {skips} skipped -> tests/results-dock-browser.json", flush=True)
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
