#!/usr/bin/env python3
"""kAIrOS — forge quality [R77, docs/FORGE-POLISH-2026-10-07.md]: the language benchmark's findings
applied to the app and game forge — the game static gate (vendored pyflakes), the headless
play-test, 6 game attempts with the identical-resubmission guard, main.py never broken after a
failed evolution, the app runtime check (headless Chromium), 4 app repair rounds that keep the
best candidate, and the fallback when an improvement regresses.

Standalone: `python3 tests/test_forge_quality.py` (run-tests.sh runs it after the suite). Part A
(pure: pyflakes gate, feedback/guards, the Chromium output parser) needs nothing; part B needs
pygame (the play-test) or Chromium (the real runtime check) and skips without them; part C boots a
kernel of this repo on :1351 against tests/mock_forge_llama.py (scripted forge answers) in a
private VIBEOS_DIR — needs aiohttp. Writes tests/results-forge-quality.json.
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
import appcheck   # noqa: E402
import gamecheck  # noqa: E402

RESULTS = {}
# ports: the suite 1338-1340 / 8081-8083, 8085, 8090; test_llm_parallel 1343, 8087-8089, 8094-8096;
# test_image_lora 1346/1347, 8091-8093, 8097-8100
KPORT, LPORT, SDPORT, TRPORT = 1351, 8111, 8112, 8113
BASE = f"http://127.0.0.1:{KPORT}"


def record(name, reqs, status, detail=""):
    RESULTS[name] = {"reqs": reqs, "status": status, "detail": detail[:300]}
    mark = {"pass": "✓", "fail": "✗", "skip": "~"}[status]
    print(f"  {mark} {name} [{', '.join(reqs)}] {detail[:160]}", flush=True)


class SkipTest(Exception):
    pass


TESTS = []


def run_test(name, reqs, needs=None):
    """needs: None = pure, "http" = the :KPORT kernel (aiohttp)"""
    def deco(fn):
        async def wrapper(ctx):
            if needs == "http" and not ctx.get("aiohttp"):
                record(name, reqs, "skip", "aiohttp not installed")
                return
            try:
                detail = await asyncio.wait_for(fn(ctx), timeout=240)
                record(name, reqs, "pass", detail or "")
            except SkipTest as e:
                record(name, reqs, "skip", str(e))
            except Exception as e:
                record(name, reqs, "fail", f"{type(e).__name__}: {e}")
        wrapper._name = name
        TESTS.append(wrapper)
        return wrapper
    return deco


def have_pygame():
    r = subprocess.run([sys.executable, "-c", "import pygame"], capture_output=True,
                       env=dict(os.environ, PYGAME_HIDE_SUPPORT_PROMPT="1"))
    return r.returncode == 0


GAME_HEAD = "import pygame, sys\npygame.init()\nscreen = pygame.display.set_mode((640, 360))\nclock = pygame.time.Clock()\n"
GAME_LOOP = """while True:
    for e in pygame.event.get():
        if e.type == pygame.QUIT:
            pygame.quit(); sys.exit()
%BODY%
    screen.fill((0, 0, 0))
    pygame.display.flip()
    clock.tick(60)
"""


def game(body="", head=""):
    return GAME_HEAD + head + GAME_LOOP.replace("%BODY%", body)


# ============================================================================= A. pure
@run_test("unit_forge_pyflakes_static_gate", ["R77", "R7", "R11"])
async def t_static(_):
    """The vendored pyflakes (4.0.2, MIT, LICENSE beside it) blocks what breaks when the line runs —
    the benchmark gate's error classes — and nothing else: an undefined name in a branch no short
    run reaches, a syntax error, an import of a module that is not installed; unused imports, an
    f-string without placeholders and a guarded optional import pass."""
    vend = REPO / "kernel" / "vendor" / "pyflakes"
    lic = (vend / "LICENSE").read_text()
    assert lic.startswith("Copyright 2005-2011 Divmod") and "Permission is hereby granted, free of charge" in lic
    assert (vend / "__init__.py").read_text().strip() == "__version__ = '4.0.2'"
    src = game(head="def game_over():\n    print('final', final_scroe)\n\n")
    p = gamecheck.static_problems(src)
    assert len(p) == 1 and "undefined name 'final_scroe'" in p[0] and p[0].startswith("line 6:") \
        and "print('final', final_scroe)" in p[0], p
    p = gamecheck.static_problems(game(body="        if e.type == pygame.KEYDOWN:\n            x = (1\n"))
    assert len(p) == 1 and "SyntaxError" in p[0], p
    p = gamecheck.static_problems("import numpy_that_is_not_here as np\n" + game())
    assert len(p) == 1 and "not installed: 'numpy_that_is_not_here'" in p[0], p
    ok = ("import os, math, random\ntry:\n    import numpy_maybe\nexcept ImportError:\n    numpy_maybe = None\n"
          + game(head="label = f'neon'\n"))
    assert gamecheck.static_problems(ok) == [], gamecheck.static_problems(ok)
    p = gamecheck.static_problems("def f():\n    return 1\nreturn 2\n")
    assert p and "outside function" in p[0], p
    return "undefined name in an unreached branch, SyntaxError, missing module blocked; unused imports/guarded import pass; pyflakes 4.0.2 + MIT LICENSE vendored"


@run_test("unit_forge_feedback_and_guards", ["R77", "R11"])
async def t_guards(_):
    """Specific feedback + the identical-resubmission guard's building blocks: code identity ignores
    whitespace only; the same error at another line is the same error; the debugger's message names
    the failing line's code, the play-test input, and — for a resubmission — the stronger order.
    The budgets: 6 game attempts, 4 app repair rounds (server.py)."""
    a = "x = 1\n\nprint(x)   \n"
    assert gamecheck.code_key(a) == gamecheck.code_key("x = 1\nprint(x)\n") != gamecheck.code_key("x = 2\nprint(x)\n")
    s1 = gamecheck.error_signature("playtest", 'Traceback…\n  File "main.py", line 12, in <module>\nIndexError: list index out of range')
    s2 = gamecheck.error_signature("playtest", 'Traceback…\n  File "main.py", line 40, in step\nIndexError: list index out of range')
    assert s1 == s2 != gamecheck.error_signature("playtest", "KeyError: 'x'"), (s1, s2)
    info = {"detail": "the game crashed 1.6 s after its window opened", "traceback": "IndexError: list index out of range",
            "input": ["t=1.00s KEYDOWN right", "t=1.35s KEYUP right"]}
    txt = gamecheck.failure_text("playtest", info)
    assert "KEYDOWN right" in txt and "THE INPUT THAT TRIGGERED IT" in txt
    code = "import pygame\nspeeds = [1]\nspeed = speeds[5]\n"
    m = gamecheck.fix_message(code, "playtest", txt, line=3, repeat="identical")
    assert m.startswith("SOURCE:\n" + code) and "TRACEBACK:\n" in m and "THE FAILING LINE 3:\n    speed = speeds[5]" in m \
        and "IDENTICAL" in m and "Do NOT send this source again" in m, m
    assert "SAME error" in gamecheck.fix_message(code, "static", "- line 3: x", repeat="same-error")
    assert "ERROR:\n- line 3" in gamecheck.fix_message(code, "static", "- line 3: x")
    assert "IDENTICAL" in appcheck.repeat_note("identical") and "SAME problem" in appcheck.repeat_note("same-error") \
        and appcheck.repeat_note(None) == ""
    srv = (REPO / "kernel" / "server.py").read_text()
    assert re.search(r"^GAME_ATTEMPTS = 6\b", srv, re.M) and re.search(r"^GATE_REPAIR_ROUNDS = 4\b", srv, re.M)
    body = srv.split("async def h_game(request):")[1].split("\nasync def ")[0]
    assert "for attempt in range(GAME_ATTEMPTS)" in body and "gamecheck.static_problems(code)" in body \
        and "gamecheck.play_test(" in body and body.index("gamecheck.play_test(") < body.index('(gdir / "main.py").write_text(code)'), \
        "main.py must only be written after the static gate and the play-test"
    return "code identity, error signature across lines, input log + failing line + IDENTICAL/SAME notes; 6 attempts / 4 rounds; main.py after both checks"


CHROMIUM_STDERR = """[1:33:1007/163359.030498:ERROR:dbus/bus.cc:405] Failed to connect to the bus: No such file or directory
[1:1:1007/163407.011914:INFO:CONSOLE:4] "Uncaught ReferenceError: foo is not defined", source: about:srcdoc (4)
[12:12:1007/101010.000001:INFO:CONSOLE(431)] "Uncaught (in promise) Error: not found: /data/notes.json", source: about:srcdoc (431)
[1:1:1007/163407.011999:INFO:CONSOLE:9] "[kAIrOS] vibe.hw.haptics is not part of the kAIrOS API (the member is undefined)", source: about:srcdoc (9)
[1:1:1007/163407.012000:INFO:CONSOLE:12] "Uncaught SyntaxError: Unexpected token ':' \\"x\\"", source: about:srcdoc (12)
"""


@run_test("unit_appcheck_chromium_output_parser", ["R77", "R12", "R45"])
async def t_parser(_):
    """The runtime check's readers without a browser: Chromium's stderr console lines (the new
    `CONSOLE:<n>]` and the old `CONSOLE(<n>)]` form; only "Uncaught …" counts), the wrapper's report
    out of --dump-dom (HTML-escaped), line mapping around the injected bridge, dedupe of one error
    reported twice, console.error only with a JS runtime error, a JSON.parse error after the canned
    free-text vibe.ask answer ignored; BRIDGE_SRC read from shell.js exactly as the suite reads it."""
    c = appcheck.parse_console(CHROMIUM_STDERR)
    assert [x["line"] for x in c] == [4, 431, 12] and c[0]["msg"] == "Uncaught ReferenceError: foo is not defined" \
        and c[2]["msg"] == "Uncaught SyntaxError: Unexpected token ':' \"x\"", c
    rep = {"errors": [{"kind": "uncaught", "phase": "interaction", "after": 'clicking button#add "Add"',
                       "msg": "Uncaught TypeError: Cannot read properties of null (reading 'value')", "line": 105},
                      {"kind": "unhandledrejection", "phase": "load", "after": "load", "msg": "not found: /data/notes.json", "line": 0},
                      {"kind": "console.error", "phase": "load", "after": "load", "msg": "could not load the weather", "line": 0},
                      {"kind": "console.error", "phase": "interaction", "after": "x", "msg": "TypeError: a.map is not a function", "line": 0},
                      {"kind": "uncaught", "phase": "interaction", "after": "clicking button", "askAnswered": True,
                       "msg": "Uncaught SyntaxError: Unexpected token 'A', \"A short sa\"... is not valid JSON", "line": 0}],
           "askAnswered": True, "done": True}
    dom = '<html><body><pre id="__kairos_rt">' + json.dumps(rep).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;") + "</pre></body></html>"
    got = appcheck.parse_report(dom)
    assert got == rep, got
    assert appcheck.parse_report("<html><pre id=\"__kairos_rt\"></pre></html>") is None and appcheck.parse_report("") is None
    html = "<!DOCTYPE html>\n<html><head><title>t</title></head><body>\n" + "\n".join(f"<p>{i}</p>" for i in range(3, 11)) \
        + '\n<script>const v = document.getElementById("itemz").value;</script>\n</body></html>'
    doc, first, n = appcheck.inject(html, "<script>\n// bridge\n// lines\n</script>")
    assert first == 2 and n == doc.count("\n") - html.count("\n") and doc.startswith("<!DOCTYPE html>\n<html><head>" + appcheck.HARNESS_JS[:40])
    assert appcheck.map_line(1, first, n) == ("app", 1) and appcheck.map_line(first + 1, first, n) == ("bridge", None) \
        and appcheck.map_line(11 + n, first, n) == ("app", 11)
    probs, errs = appcheck.problems_from(rep, c[1:2], html, first, n)
    joined = "\n".join(probs)
    assert len(probs) == 3, probs
    assert "after clicking button#add \"Add\"" in probs[0] and "TypeError: Cannot read properties of null" in probs[0], probs[0]
    assert "unhandled promise rejection while the app loaded" in probs[1] and "not found: /data/notes.json" in probs[1], probs[1]
    assert "a.map is not a function" in probs[2] and "could not load the weather" not in joined and "is not valid JSON" not in joined
    line_err = {"errors": [{"kind": "uncaught", "phase": "load", "after": "load", "msg": "Uncaught TypeError: x", "line": 11 + n}]}
    p2, _ = appcheck.problems_from(line_err, [], html, first, n)
    assert p2[0].endswith('app line 11: <script>const v = document.getElementById("itemz").value;</script>'), p2
    src = appcheck.bridge_source(REPO / "shell")
    js = (REPO / "shell" / "shell.js").read_text()
    want = re.search(r"const BRIDGE_SRC = `(.*?)`;\n", js, re.S).group(1).replace("<\\/script>", "</script>")
    assert src == want and src.rstrip().endswith("</script>") and "parent.postMessage" in src
    assert appcheck.extract_template("const X = `a${b}`;", "X") is None and appcheck.extract_template("const X = `a\\`b`;", "X") == "a`b"
    return f"stderr (new+old console form, {len(c)} Uncaught), DOM report, line map, dedupe, console.error/JSON filters, BRIDGE_SRC ({len(src)} chars)"


# ============================================================================= B. real engines (pygame / Chromium)
@run_test("unit_forge_playtest_catches_keypress_crash", ["R77", "R7", "R11"])
async def t_playtest(_):
    """The headless play-test (SDL dummy drivers, no window on the display): a game that crashes
    only while the RIGHT key is pressed is caught — the traceback without the harness frames, the
    failing line, the input that triggered it; the same game without the bug passes; a program that
    never opens a window fails; a game that keeps drawing after pygame.quit() on the window's close
    fails "after the QUIT"; a game ignoring QUIT is ended by the kernel and passes."""
    if not have_pygame():
        raise SkipTest("pygame not installed")
    bug = game(body="    if pygame.key.get_pressed()[pygame.K_RIGHT]:\n        speed = [1, 2, 3][9]\n")
    good = game(body="    if pygame.key.get_pressed()[pygame.K_RIGHT]:\n        speed = [1, 2, 3][2]\n")
    afterquit = GAME_HEAD + "while True:\n    for e in pygame.event.get():\n        if e.type == pygame.QUIT:\n            pygame.quit()\n    screen.fill((0, 0, 0))\n    pygame.display.flip()\n    clock.tick(60)\n"
    ignore = GAME_HEAD + "while True:\n    pygame.event.pump()\n    pygame.display.flip()\n    clock.tick(60)\n"
    r_bug, r_good, r_nowin, r_aq, r_ign = await asyncio.gather(
        gamecheck.play_test(bug), gamecheck.play_test(good), gamecheck.play_test("print('hello')\n"),
        gamecheck.play_test(afterquit), gamecheck.play_test(ignore, seconds=3.0))
    assert not r_bug["ok"] and r_bug["verdict"] == "exception" and r_bug["summary"].startswith("IndexError") \
        and r_bug["line"] == 10 and any("KEYDOWN right" in e for e in r_bug["input"]) and not r_bug["after_quit"], r_bug
    assert 'File "main.py", line 10' in r_bug["traceback"] and "gamecheck_harness" not in r_bug["traceback"] \
        and "runpy" not in r_bug["traceback"], r_bug["traceback"]
    assert r_bug["seconds"] < 6, r_bug["seconds"]
    assert r_good["ok"] and r_good["verdict"] == "passed" and r_good["input_events"] > 30, r_good
    assert not r_nowin["ok"] and r_nowin["verdict"] == "no-window", r_nowin
    assert not r_aq["ok"] and r_aq["after_quit"] and "after the window was closed" in r_aq["detail"], r_aq
    assert r_ign["ok"] and r_ign["verdict"] == "timeout", r_ign
    return (f"RIGHT-key crash caught in {r_bug['seconds']} s (line 10, input {r_bug['input'][-1]}); healthy passed "
            f"({r_good['input_events']} events, {r_good['seconds']} s); no-window, after-QUIT crash caught; QUIT ignored = pass")


@run_test("unit_appcheck_runtime_chromium", ["R77", "R12", "R45"])
async def t_chromium(_):
    """The real runtime check (headless Chromium, the shell's bridge, canned vibe.* answers): a
    click-only TypeError is found with the button and the app line; a load-time TypeError and an
    unhandled vibe.fs.read rejection are found; a healthy app (vibe.ask/store/load/notify used) is
    clean. Skips where no Chromium is installed (the suite container)."""
    if not appcheck.find_chromium():
        raise SkipTest("no chromium here (the parser is covered by unit_appcheck_chromium_output_parser)")
    ok = ('<!DOCTYPE html>\n<html><head><title>a</title></head><body><input id="q"><button id="ask">Ask</button><div id="o"></div>'
          '<button onclick="vibe.notify(\'hi\')">notify</button>\n<script>\nlocalStorage.setItem("k", "1");\n'
          'document.getElementById("ask").onclick = async () => { document.getElementById("o").textContent = await vibe.ask("a fact about " + document.getElementById("q").value); };\n'
          'vibe.load("prefs").then((p) => { document.body.dataset.p = JSON.stringify(p || {}); });\n</script></body></html>')
    click = ('<!DOCTYPE html>\n<html><head><title>b</title></head><body>\n<input id="item"><button id="add">Add</button>\n<script>\n'
             'document.getElementById("add").addEventListener("click", () => {\n  const v = document.getElementById("itemz").value;\n});\n</script></body></html>')
    load = ('<!DOCTYPE html>\n<html><head></head><body>\n<script>\nvibe.fs.read("/data/notes.json").then((t) => { document.body.textContent = t; });\n'
            'document.getElementById("missing").addEventListener("click", () => {});\n</script></body></html>')
    shell = REPO / "shell"
    r_ok, r_click, r_load = [await appcheck.check(h, shell_dir=shell, settings={"theme": "dark"}) for h in (ok, click, load)]
    assert r_ok["ran"] and r_ok["problems"] == [] and {"ask", "store", "load", "notify"} <= set(r_ok["calls"]), r_ok
    assert r_click["ran"] and len(r_click["problems"]) == 1 and 'after clicking button#add "Add"' in r_click["problems"][0] \
        and "app line 6: const v = document.getElementById(\"itemz\").value;" in r_click["problems"][0], r_click
    assert r_load["ran"] and len(r_load["problems"]) == 2 and "while the app loaded" in r_load["problems"][0] \
        and any("not found: /data/notes.json" in p for p in r_load["problems"]), r_load
    return f"healthy clean ({r_ok['seconds']} s), click error with button + app line, load error + unhandled rejection ({r_load['seconds']} s)"


# ============================================================================= C. a kernel of its own
class Kernel:
    def __init__(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kairos-forge-"))
        self.proc = None
        self.logf = None

    def setup(self):
        t = self.tmp
        for d in ("bin", "cache", "models", "games", "manifests", "log"):
            (t / d).mkdir()
        shim = t / "bin" / "llama-server"
        shim.write_text('#!/bin/bash\nport=8081\nargs=("$@")\nfor ((i = 0; i < ${#args[@]}; i++)); do\n'
                        '  [ "${args[$i]}" = "--port" ] && port="${args[$((i + 1))]}"\ndone\n'
                        f'exec {sys.executable} {REPO / "tests" / "mock_forge_llama.py"} "$port"\n')
        shim.chmod(0o755)
        (t / "bin" / "sd-cli").symlink_to(REPO / "tests" / "mock-sd-cli")
        subprocess.run([sys.executable, str(REPO / "scripts" / "engine-stamp.py"), "--mock",
                        "--out", str(t / "bin" / "ENGINES.json")], check=True, capture_output=True)
        shutil.copy(REPO / "manifests" / "tier-8gb.json", t / "manifests" / "tier-8gb.json")

        def files(n, out):
            if isinstance(n, dict):
                if "url" in n and "file" in n and n.get("store") != "knowledge":
                    out.add(n["file"])
                for v in n.values():
                    files(v, out)
        seen = set()
        files(json.loads((REPO / "manifests" / "tier-8gb.json").read_text()), seen)
        for f in seen:
            p = t / "models" / f
            p.parent.mkdir(parents=True, exist_ok=True)
            p.touch()
        self.env = {k: v for k, v in os.environ.items() if not k.startswith(("VIBEOS_", "MOCK_")) and k != "KAIROS_URL"}
        self.env.update(VIBEOS_DIR=str(t), VIBEOS_SHELL=str(REPO / "shell"), VIBEOS_MANIFESTS=str(t / "manifests"),
                        VIBEOS_MANIFEST=str(t / "manifests" / "tier-8gb.json"), VIBEOS_PORT=str(KPORT),
                        VIBEOS_LLAMA_PORT=str(LPORT), VIBEOS_SD_PORT=str(SDPORT), VIBEOS_TRANSLATE_PORT=str(TRPORT),
                        VIBEOS_FORCE_OFFLINE="1", KAIROS_LOG_DIR=str(t / "log"), VIBEOS_TOKEN_FILE=str(t / "token"),
                        VIBEOS_NVIDIA_PROC="/nonexistent", MOCK_FORGE_MSGLOG=str(t / "forge-msgs.log"),
                        MOCK_SD_ARGLOG=str(t / "sd-args.log"), SDL_VIDEODRIVER="dummy", SDL_AUDIODRIVER="dummy",
                        PYTHONUNBUFFERED="1")

    def start(self):
        self.logf = open(self.tmp / "kernel.log", "wb")
        self.proc = subprocess.Popen([sys.executable, str(REPO / "kernel" / "server.py")], env=self.env,
                                     stdout=self.logf, stderr=subprocess.STDOUT)

    async def wait(self, session):
        for _ in range(120):
            try:
                async with session.get(f"{BASE}/api/status", timeout=2) as r:
                    if r.status == 200:
                        return
            except Exception:
                pass
            assert self.proc.poll() is None, "kernel died at boot: " + self.log()[-2000:]
            await asyncio.sleep(0.5)
        raise AssertionError("kernel never answered: " + self.log()[-2000:])

    def log(self):
        if self.logf:
            self.logf.flush()
        p = self.tmp / "kernel.log"
        return p.read_text(errors="ignore") if p.exists() else ""

    def msgs(self):
        p = self.tmp / "forge-msgs.log"
        return [json.loads(ln) for ln in p.read_text().splitlines()] if p.exists() else []

    def close(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        if self.logf:
            self.logf.close()
        keep = os.environ.get("FORGE_KEEP_LOGS")
        if keep:
            Path(keep).mkdir(parents=True, exist_ok=True)
            for f in ("kernel.log", "forge-msgs.log"):
                if (self.tmp / f).exists():
                    shutil.copy(self.tmp / f, Path(keep) / f)
        shutil.rmtree(self.tmp, ignore_errors=True)


async def game_job(ctx, prompt, timeout=200):
    s = ctx["session"]
    async with s.post(f"{BASE}/api/game", json={"prompt": prompt}) as r:
        j = await r.json()
    assert "job" in j, j
    t0 = time.time()
    while time.time() - t0 < timeout:
        async with s.get(f"{BASE}/api/job/{j['job']}") as r:
            d = await r.json()
        if d["status"] in ("done", "error"):
            return d
        await asyncio.sleep(0.3)
    raise AssertionError(f"game job timed out: {prompt}")


async def app_events(ctx, body):
    s = ctx["session"]
    events = []
    async with s.post(f"{BASE}/api/app", json=body) as r:
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
    return events


def deltas(events):
    return "".join(d.get("text", "") for ev, d in events if ev == "delta")


def games_dir(ctx):
    return ctx["kernel"].tmp / "games"


def slug_dir(ctx, prompt):
    """games/<slug> — server.slugify's rule"""
    return games_dir(ctx) / (re.sub(r"[^a-z0-9]+", "-", prompt.lower()).strip("-")[:48])


async def stop_game(pid):
    try:
        os.kill(pid, 15)
    except (ProcessLookupError, TypeError):
        pass
    await asyncio.sleep(0.3)


def need_pygame():
    if not have_pygame():
        raise SkipTest("pygame not installed (the forge's play-test and launch need it)")


def need_js_gate(ctx):
    import jscheck
    if not jscheck.enabled():
        raise SkipTest("no JS parser here — run in the kairos-fix image (quickjs)")


@run_test("http_forge_game_static_gate_blocks_undefined_name", ["R77", "R7", "R11"], needs="http")
async def t_http_static(ctx):
    """A NameError the play-test would never reach (game-over branch) is caught by the static gate
    before anything runs; the debugger gets the pyflakes message + the line; attempt 2 runs."""
    need_pygame()
    p = "forge test ball [undefined-name]"
    d = await game_job(ctx, p)
    r = d["result"]
    assert d["status"] == "done" and r["running"] and r["attempt"] == 2 and r["checks"]["static_failures"] == 1 \
        and r["checks"]["playtest"] == "passed", d
    log = ctx["kernel"].log()
    assert "game rejected (attempt 1, static check: line 10: undefined name 'final_scroe')" in log, log[-1500:]
    fix = [m for m in ctx["kernel"].msgs() if m["system"].startswith("You are the kAIrOS game forge debugger")]
    assert fix and "ERROR:\n- line 10: undefined name 'final_scroe'" in fix[0]["user"] \
        and "THE FAILING LINE 10:\n    print(\"final score\", final_scroe)" in fix[0]["user"], fix[:1]
    main = (slug_dir(ctx, p) / "main.py").read_text()
    assert "final_scroe" not in main and gamecheck.static_problems(main) == []
    await stop_game(r.get("pid"))
    return "pyflakes blocked attempt 1 (no launch, no play-test), the fix carried the line, attempt 2 running"


@run_test("http_forge_game_playtest_catches_key_crash", ["R77", "R7", "R11"], needs="http")
async def t_http_playtest(ctx):
    """A crash only on the RIGHT key: the headless play-test finds it before the game reaches the
    display; the debugger gets the traceback AND the input that triggered it; main.py is written
    only once a version passed (the crashing one never reaches disk)."""
    need_pygame()
    p = "forge test runner [crash-on-key]"
    d = await game_job(ctx, p)
    r = d["result"]
    assert d["status"] == "done" and r["running"] and r["attempt"] == 2 and r["checks"]["playtest_failures"] == 1, d
    log = ctx["kernel"].log()
    assert "game crashed (attempt 1, play-test: IndexError: list index out of range), self-repairing" in log, log[-1500:]
    fix = [m for m in ctx["kernel"].msgs() if "[crash-on-key]" in m["user"] and m["system"].startswith("You are the kAIrOS game forge debugger")]
    assert fix and "played headless with synthetic keyboard and mouse input" in fix[0]["user"] \
        and "KEYDOWN right" in fix[0]["user"] and "THE FAILING LINE 14:\n    speed = speeds[level]" in fix[0]["user"], fix[:1]
    main = (slug_dir(ctx, p) / "main.py").read_text()
    assert "speeds[level]" not in main, "the crashing candidate must never become main.py"
    await stop_game(r.get("pid"))
    return "key-only IndexError caught headless at attempt 1 (traceback + KEYDOWN right to the debugger), attempt 2 running"


@run_test("http_forge_game_identical_resubmission_guard", ["R77", "R11"], needs="http")
async def t_http_identical(ctx):
    """The Bonsai pattern: the debugger sends the broken source back unchanged. The kernel does
    not run it again; it re-asks with the IDENTICAL order — and the model
    fixes it then: running at attempt 3 of the 6."""
    need_pygame()
    p = "forge test stubborn [identical]"
    d = await game_job(ctx, p)
    r = d["result"]
    assert d["status"] == "done" and r["running"] and r["attempt"] == 3 and r["checks"]["identical_resubmissions"] == 1 \
        and r["checks"]["playtest_failures"] == 1, d
    log = ctx["kernel"].log()
    assert "attempt 2 is an identical resubmission of a failed source — asking again with a stronger instruction" in log
    fix = [m for m in ctx["kernel"].msgs() if "[identical]" in m["user"] and m["system"].startswith("You are the kAIrOS game forge debugger")]
    # (the temperature bump 0.4 -> 0.7 is overridden by Gemma's manifest sampling, temperature 1.0)
    assert len(fix) == 2 and "IDENTICAL" not in fix[0]["user"] and "IDENTICAL" in fix[1]["user"], \
        [(m["temperature"], "IDENTICAL" in m["user"]) for m in fix]
    await stop_game(r.get("pid"))
    return "identical resubmission detected (not re-run), re-asked with the IDENTICAL order, fixed: running at attempt 3"


@run_test("http_forge_game_failed_evolution_keeps_working_version", ["R77", "R35", "R45", "R7"], needs="http")
async def t_http_evolution(ctx):
    """v1 runs; every candidate of the next evolution crashes. main.py is never touched (it is
    written only for a version that passed), the 6 attempts are spent, the attempt goes to
    history/failed-v2.py (+ .log), meta.json keeps version 1 + failed_evolution, the previous
    version is launched and the job says so honestly (fallback, evolution_failed, message)."""
    need_pygame()
    p = "forge test fragile [never-fixed]"
    d1 = await game_job(ctx, p)
    assert d1["status"] == "done" and d1["result"]["running"] and d1["result"]["version"] == 1, d1
    gdir = slug_dir(ctx, p)
    v1 = (gdir / "main.py").read_text()
    await stop_game(d1["result"]["pid"])
    d2 = await game_job(ctx, p, timeout=230)
    r = d2["result"]
    assert d2["status"] == "done" and r["fallback"] is True and r["running"] and r["version"] == 1, d2
    ev = r["evolution_failed"]
    assert ev["version"] == 2 and ev["attempts"] == 6 and ev["checks"]["playtest_failures"] == 6 \
        and "IndexError" in ev["error"] and ev["file"] == "history/failed-v2.py", ev
    assert "the improvement to v2 failed after 6 attempts" in r["message"] and "running the previous version v1" in r["message"]
    assert (gdir / "main.py").read_text() == v1, "main.py must still be the working v1"
    assert gamecheck.static_problems(v1) == []
    assert "colors[7]" in (gdir / "history" / "failed-v2.py").read_text() and (gdir / "history" / "failed-v2.log").exists()
    assert not (gdir / "history" / "v1.py").exists(), "v1 is still the current version, not history"
    meta = json.loads((gdir / "meta.json").read_text())
    assert meta["version"] == 1 and meta["failed_evolution"]["version"] == 2, meta
    log = ctx["kernel"].log()
    assert "the improvement to v2 failed after 6 attempts" in log
    assert sum(1 for m in ctx["kernel"].msgs() if "[never-fixed]" in m["user"] and m["system"].startswith("You are the kAIrOS game forge debugger")) == 5
    await stop_game(r.get("pid"))
    return "6 failed attempts, main.py = v1 byte-identical, history/failed-v2.py + log, meta v1 + failed_evolution, v1 relaunched (fallback)"


@run_test("http_forge_app_repair_rounds", ["R77", "R12", "R45"], needs="http")
async def t_http_rounds(ctx):
    """An app whose script the model only fixes in its 3rd repair reply: delivered clean (the old
    budget of 2 rounds delivered it flagged broken); every round's verdict is streamed."""
    need_js_gate(ctx)
    ev = await app_events(ctx, {"prompt": "a forge list app [js-3rounds]", "app_id": "forge-3rounds"})
    done = [d for e, d in ev if e == "done"]
    assert done and done[0]["broken"] is None and done[0]["repair_rounds"] == 3, [e for e, _ in ev]
    t = deltas(ev)
    assert "// self-repair round 1: 1 problem(s): JavaScript does not parse" in t and "// self-repair round 3: all checks clean" in t, t[-800:]
    assert "async generateImage" not in done[0]["html"] and done[0]["checks"].startswith("gate clean"), done[0]["checks"]
    return f"fixed in round 3 of {4}: delivered clean ({done[0]['checks']})"


@run_test("http_forge_app_identical_repair_guard", ["R77", "R12", "R11"], needs="http")
async def t_http_app_identical(ctx):
    """The repair daemon sends the broken document back unchanged: not re-checked, re-asked with the
    IDENTICAL note — fixed in round 2."""
    need_js_gate(ctx)
    ev = await app_events(ctx, {"prompt": "a forge stubborn app [js-identical]", "app_id": "forge-identical"})
    done = [d for e, d in ev if e == "done"]
    assert done and done[0]["broken"] is None and done[0]["repair_rounds"] == 2, [e for e, _ in ev]
    assert "// self-repair round 1: the model sent back a document that already failed — asking again, more firmly" in deltas(ev)
    rep = [m for m in ctx["kernel"].msgs() if "[js-identical]" in m["user"] and "self-repair daemon" in m["system"]]
    assert len(rep) == 2 and "IDENTICAL" not in rep[0]["user"] and "IDENTICAL" in rep[1]["user"], \
        [(m["temperature"], "IDENTICAL" in m["user"]) for m in rep]
    return "identical repair reply detected (not re-checked), re-asked with the IDENTICAL order, clean in round 2"


@run_test("http_forge_app_regressing_improvement_falls_back", ["R77", "R24", "R35"], needs="http")
async def t_http_fallback(ctx):
    """✨ improve produces a version that fails the JS gate after all 4 repair rounds while v1 passes:
    v1 stays mounted (the shell's fallback event, failed_version 2), app.html/meta untouched (version
    1), the attempt is kept as history/failed-v2.html and recorded in meta failed_evolutions."""
    need_js_gate(ctx)
    ev1 = await app_events(ctx, {"prompt": "a forge note app [evolve-regress]", "app_id": "forge-regress"})
    d1 = [d for e, d in ev1 if e == "done"]
    assert d1 and d1[0]["version"] == 1 and d1[0]["broken"] is None, [e for e, _ in ev1]
    adir = ctx["kernel"].tmp / "cache" / "apps" / "forge-regress"
    v1 = (adir / "app.html").read_text()
    ev2 = await app_events(ctx, {"prompt": "a forge note app [evolve-regress]", "app_id": "forge-regress",
                                 "evolve": True, "instruction": "add tags"})
    kinds = [e for e, _ in ev2]
    fb = [d for e, d in ev2 if e == "fallback"]
    assert fb and "done" not in kinds, kinds
    assert fb[0]["version"] == 1 and fb[0]["failed_version"] == 2 and "forge mock app" in fb[0]["html"] \
        and "fails the checks the previous version passes" in fb[0]["message"], fb[0]
    assert (adir / "app.html").read_text() == v1, "v1 must stay the app"
    assert "async generateImage" in (adir / "history" / "failed-v2.html").read_text()
    meta = json.loads((adir / "meta.json").read_text())
    assert meta["version"] == 1 and meta["failed_evolutions"][-1]["version"] == 2 and meta["failed_evolutions"][-1]["instruction"] == "add tags", meta
    assert "// no regression: v2 fails the checks the previous version passes" in deltas(ev2)
    return "regressing v2 (4 rounds) not mounted: fallback v1, app.html unchanged, history/failed-v2.html, meta failed_evolutions"


@run_test("http_forge_app_runtime_error_repaired", ["R77", "R12", "R45"], needs="http")
async def t_http_runtime(ctx):
    """An app that parses but throws when its "Add" button is clicked: the runtime check finds it
    before `done`, the repair round gets the error (button + line), the delivered version is clean.
    Skips without Chromium (the suite container)."""
    need_js_gate(ctx)
    if not appcheck.find_chromium():
        raise SkipTest("no chromium here (the runtime check skips; parser covered by the unit test)")
    ev = await app_events(ctx, {"prompt": "a forge click app [runtime-click]", "app_id": "forge-runtime"})
    done = [d for e, d in ev if e == "done"]
    t = deltas(ev)
    assert done and done[0]["repair_rounds"] == 1 and "runtime clean" in done[0]["checks"], (done and done[0].get("checks"), t[-600:])
    assert 'self-repair: runtime JavaScript error (uncaught exception after clicking button#add "Add"' in t, t[-800:]
    assert "itemz" not in done[0]["html"]
    return f"click TypeError found headless, repaired in 1 round, delivered: {done[0]['checks']}"


# ============================================================================= main
async def main():
    only = [x.strip() for x in os.environ.get("KAIROS_TEST_ONLY", "").split(",") if x.strip()]
    tests = [t for t in TESTS if not only or any(o in t._name for o in only)]
    try:
        import aiohttp
    except ImportError:
        aiohttp = None
    print(f"kAIrOS forge quality tests [R77] — {len(tests)} tests" + ("" if aiohttp else " (no aiohttp: pure tests only)")
          + f"; pygame {'yes' if have_pygame() else 'no'}, chromium {appcheck.find_chromium() or 'no'}", flush=True)
    ctx = {"aiohttp": aiohttp}
    k = None
    try:
        if aiohttp and any(t._name.startswith("http_") for t in tests):
            k = Kernel()
            k.setup()
            k.start()
            ctx["kernel"] = k
            async with aiohttp.ClientSession() as s:
                ctx["session"] = s
                await k.wait(s)
                for t in tests:
                    await t(ctx)
        else:
            for t in tests:
                await t(ctx)
    finally:
        if k:
            if any(r["status"] == "fail" for r in RESULTS.values()):
                print("== kernel log tail ==\n" + k.log()[-4000:], flush=True)
            k.close()
    out = {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "tests": RESULTS}
    if not only:
        (REPO / "tests" / "results-forge-quality.json").write_text(json.dumps(out, indent=1))
    fails = [n for n, r in RESULTS.items() if r["status"] == "fail"]
    passes = sum(1 for r in RESULTS.values() if r["status"] == "pass")
    skips = sum(1 for r in RESULTS.values() if r["status"] == "skip")
    print(f"\n{passes} passed, {len(fails)} failed, {skips} skipped -> tests/results-forge-quality.json", flush=True)
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    asyncio.run(main())
