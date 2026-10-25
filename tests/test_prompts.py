#!/usr/bin/env python3
"""kAIrOS R92/R93 — the prompt registry, its overrides, the ✎ Prompts panel's API, the sync script and the songwriter
(docs/PROMPTS-2026-10-24.md).

Standalone: `python3 tests/test_prompts.py` (run-tests.sh runs it after the suite). Writes tests/results-prompts.json.
* registry: every string constant of kernel/prompts.py is registered with its line, a note and a kind; the compositions
  equal base + part; the shipped texts are what they were (TRANSLATE_PROMPT = the R89 benchmark body);
* kernel (in-process, a PRIVATE VIBEOS_DIR, the mock llama-server on :8171, the mock ace engines, placeholder model
  files): overrides live through POST /api/prompts/<name> — the file, the module attribute, the rebuilt compositions,
  EXPANDERS / LANGUAGE_PROMPTS, the /system copies, the NEXT request's system message — reset, reload, parts refused;
  the dock prompts (DOCK_SETTINGS from shell/index.html, substituted in /api/app);
* the songwriter: POST /api/lyrics — temperature 1.2 / top_k 80 / json_schema on the wire, the plan fields, the pins,
  the tag repairs, the retry, the cap, the minors block; then /api/music with the plan (the mock ace request);
* the shell wiring (static; node --check when node exists) and scripts/prompt-sync.py (export / diff / push --dir /
  pull / push --kernel / reset --kernel / bake on a copy of the tree / html).
"""
import asyncio
import html as _html
import importlib.machinery
import importlib.util
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
RESULTS = {}
LLAMA_PORT = 8171                 # ports in use: test_kernel 1376/8141-8143, picture_edit 8151, imaging 8161, …
TIERS = ("8gb", "16gb", "24gb", "32gb")
sys.path.insert(0, str(REPO / "tests"))


def record(name, reqs, status, detail=""):
    RESULTS[name] = {"reqs": reqs, "status": status, "detail": detail[:300]}
    mark = {"pass": "✓", "fail": "✗", "skip": "~"}[status]
    print(f"  {mark} {name} [{', '.join(reqs)}] {detail[:170]}", flush=True)


class SkipTest(Exception):
    pass


def test(name, reqs):
    def deco(fn):
        async def wrapper(ctx):
            try:
                detail = fn(ctx)
                if asyncio.iscoroutine(detail):
                    detail = await asyncio.wait_for(detail, timeout=300)
                record(name, reqs, "pass", detail or "")
            except SkipTest as e:
                record(name, reqs, "skip", str(e))
            except Exception as e:
                import traceback
                tb = traceback.extract_tb(e.__traceback__)
                where = next((f.lineno for f in reversed(tb) if f.filename == __file__), tb[-1].lineno if tb else 0)
                record(name, reqs, "fail", f"{type(e).__name__}: {str(e)[:600]} @ {where}")
                traceback.print_exc()
        wrapper._name, wrapper._reqs, wrapper._test = name, reqs, True
        return wrapper
    return deco


def manifest(t="8gb"):
    return json.loads((REPO / "manifests" / f"tier-{t}.json").read_text(encoding="utf-8"))


def load_module(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


# ----------------------------------------------------------------------------- the kernel, in-process, private dir
_K = {}


def kernel():
    if "server" in _K:
        return _K["server"]
    try:
        import aiohttp  # noqa: F401
    except ImportError:
        raise SkipTest("aiohttp not installed (run in the testrig container)")
    d = Path(tempfile.mkdtemp(prefix="kairos-pr-"))
    (d / "bin").mkdir()
    for mock, name in (("mock-llama-server", "llama-server"), ("mock-sd-cli", "sd-cli"), ("mock-ace-lm", "ace-lm"), ("mock-ace-synth", "ace-synth")):
        os.chmod(REPO / "tests" / mock, 0o755)
        os.symlink(REPO / "tests" / mock, d / "bin" / name)
    subprocess.run([sys.executable, str(REPO / "scripts" / "engine-stamp.py"), "--mock", "--out", str(d / "bin" / "ENGINES.json")],
                   check=True, capture_output=True)
    seen = set()                       # placeholder model files: every file of every tier (the presence checks)

    def files(n):
        if isinstance(n, dict):
            if "url" in n and "file" in n and n.get("store") != "knowledge":
                seen.add(n["file"])
            for v in n.values():
                files(v)
    for t in TIERS:
        files(manifest(t))
    for f in seen:
        p = d / "models" / f
        p.parent.mkdir(parents=True, exist_ok=True)
        p.touch()
    os.environ.update(VIBEOS_DIR=str(d), VIBEOS_MANIFEST=str(REPO / "manifests" / "tier-8gb.json"),
                      VIBEOS_MANIFESTS=str(REPO / "manifests"), VIBEOS_SHELL=str(REPO / "shell"),
                      VIBEOS_FORCE_OFFLINE="1", KAIROS_LOG_DIR=str(d / "log"), VIBEOS_TOKEN_FILE=str(d / "token"),
                      VIBEOS_LLAMA_PORT=str(LLAMA_PORT), KAIROS_PROMPTS_DIR=str(d / "prompts"),
                      MOCK_LLAMA_MSGLOG=str(d / "llama-msgs.log"), MOCK_ACE_ARGLOG=str(d / "ace-args.log"),
                      VIBEOS_SD_SESSION_IDLE="0", VIBEOS_SD_FADVISE="off")
    sys.path.insert(0, str(REPO / "kernel"))
    import server
    _K["server"], _K["dir"] = server, d
    return server


def msgs():
    p = Path(os.environ.get("MOCK_LLAMA_MSGLOG", "/nonexistent"))
    return [json.loads(ln) for ln in p.read_text().splitlines()] if p.exists() else []


async def _client(s):
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer
    app = web.Application()
    s.add_routes(app)
    c = TestClient(TestServer(app))
    await c.start_server()
    return c


async def sse(c, url, body, timeout=240):
    import aiohttp
    events = []
    async with c.post(url, json=body, timeout=aiohttp.ClientTimeout(total=timeout)) as r:
        buf = ""
        async for chunk in r.content.iter_any():
            buf += chunk.decode("utf-8", "ignore")
            while "\n\n" in buf:
                block, buf = buf.split("\n\n", 1)
                ev, data = None, ""
                for line in block.splitlines():
                    if line.startswith("event:"):
                        ev = line[6:].strip()
                    elif line.startswith("data:"):
                        data += line[5:].strip()
                events.append((ev, data))
    return events


async def poll_job(c, jid, timeout=120):
    t0 = time.time()
    while time.time() - t0 < timeout:
        r = await c.get(f"/api/job/{jid}")
        j = await r.json()
        if j.get("status") in ("done", "error"):
            return j
        await asyncio.sleep(0.3)
    raise AssertionError(f"job {jid} did not finish")


# ----------------------------------------------------------------------------- the tests
@test("pr_registry_complete", ["R92"])
def t_registry(ctx):
    src = (REPO / "kernel" / "prompts.py").read_text(encoding="utf-8")
    sys.path.insert(0, str(REPO / "kernel"))
    import prompts as P
    consts = {n for n, v in vars(P).items() if isinstance(v, str) and n.lstrip("_").isupper()
              and n not in ("DEFAULTS", "NOTES", "WHERE", "OVERRIDES_DIR")}
    missing = consts - set(P.names())
    assert not missing, f"not registered: {sorted(missing)}"
    lines = src.splitlines()
    for n in P.names():
        if n in P.EXTERNAL:
            continue
        assert P.NOTES.get(n), f"{n}: no note"
        f, ln = P.WHERE[n].rsplit(":", 1)
        assert f == "kernel/prompts.py" and lines[int(ln) - 1].startswith(f"{n} ="), (n, P.WHERE[n])
    for name, (base, part) in P._DERIVED.items():
        assert P.DEFAULTS[name] == P.DEFAULTS[base] + P.DEFAULTS[part], name
    for n in ("APP_SYSTEM", "GAME_SYSTEM", "ASK_SYSTEM", "BROWSER_SYSTEM", "HTML_FIX_SYSTEM", "SPEC_SYSTEM", "LYRICS_SYSTEM"):
        assert P.editable(n) and P.kind_of(n) == "system", n
    assert P.TRANSLATE_PROMPT == ("Translate the following text into {language}. Only output the translation, "
                                  "without any additional explanation:\n\n{text}")
    L = P.LYRICS_SYSTEM
    assert L.startswith("You are the songwriter of kAIrOS") and L.endswith(P.ADULT_RULE_TEXT)
    for needle in ('"title"', '"caption"', '"lyrics"', '"language"', '"duration"', '"bpm"', '"keyscale"', '"timesignature"',
                   "NEVER a BPM number", "6 to 10 syllables", "(parentheses)", 'No "x2"', "[Verse 1]", "[Chorus]", "20 to 512"):
        assert needle in L, needle
    assert not P.editable("STYLE") and P.kind_of("_KREA_EXPAND_HEAD") == "part" and P.kind_of("BROWSER_NAV_SCRIPT") == "legacy"
    assert P.kind_of("MUSIC_PROMPT_HINT") == "hint" and P.kind_of("TRANSLATE_PROMPT") == "template" and P.kind_of("ADULT_PREAMBLE") == "instruction"
    # the shipped values of the prompts other tests pin are untouched by the refactor
    assert P.APP_REGEN_SYSTEM.startswith(P.APP_SYSTEM) and "iterating on an existing app" in P.APP_REGEN_SYSTEM
    assert P.APP_EVOLVE_SYSTEM.startswith(P.APP_SYSTEM) and "EVOLUTION MODE" in P.APP_EVOLVE_SYSTEM
    assert P.WEB_EVOLVE_SYSTEM.startswith(P.BROWSER_SYSTEM) and "WEBSITE EVOLUTION" in P.WEB_EVOLVE_SYSTEM
    return f"{len(P.names())} names ({sum(P.editable(n) for n in P.names())} editable), every one with a line + note"


@test("pr_overrides_live", ["R92"])
async def t_overrides(ctx):
    s = kernel()
    P = s.prompts
    PDIR = Path(os.environ["KAIROS_PROMPTS_DIR"])
    c = await _client(s)
    try:
        r = await c.get("/api/prompts")
        j = await r.json()
        names = [p["name"] for p in j["prompts"]]
        assert j["count"] >= 50 and "DOCK_SETTINGS" in names and "LYRICS_SYSTEM" in names and j["dir"] == str(PDIR), (j["count"], j["dir"])
        assert j["sampling"]["lyrics"] == {"temperature": 1.2, "top_k": 80} and j["sampling"]["llm"] == manifest()["llm"]["sampling"]
        assert all(k in j["prompts"][0] for k in ("kind", "where", "note", "editable", "overridden", "chars"))
        # set: the file, the attribute, the language-prompt tuple, the /system copy, the NEXT request's system message
        text = "You are the imagination engine of kAIrOS TEST OVERRIDE. Reply plainly."
        r = await c.post("/api/prompts/ASK_SYSTEM", json={"text": text})
        j = await r.json()
        assert r.status == 200 and j["overridden"] and j["text"] == text and j["default"] == P.DEFAULTS["ASK_SYSTEM"], j
        assert (PDIR / "ASK_SYSTEM.txt").read_text(encoding="utf-8") == text and P.ASK_SYSTEM == text and s.LANGUAGE_PROMPTS[2] == text
        r = await c.post("/api/fs", json={"op": "read", "path": "/system/prompts/ASK_SYSTEM.txt"})
        assert (await r.json())["content"] == text
        r = await c.post("/api/fs", json={"op": "read", "path": "/system/prompts/app_system.txt"})
        assert (await r.json())["content"] == P.APP_SYSTEM, "the legacy copy stays"
        n0 = len(msgs())
        r = await c.post("/api/ask", json={"prompt": "hello from the prompts test"})
        assert r.status == 200, await r.text()
        asked = [m for m in msgs()[n0:] if m.get("system", "").startswith("You are the imagination engine of kAIrOS TEST")]
        assert asked, "the override did not reach the model"
        assert asked[-1]["system"].rstrip().endswith(P.UNCENSORED_LIMITS), "the uncensored primary's limits line follows the override"
        # a composition follows its base; the override of the composition wins; reset restores both
        r = await c.post("/api/prompts/APP_SYSTEM", json={"text": "APP TEST " + P.DEFAULTS["APP_SYSTEM"]})
        assert r.status == 200 and P.APP_REGEN_SYSTEM == "APP TEST " + P.DEFAULTS["APP_SYSTEM"] + P._APP_REGEN_TAIL \
            and P.APP_EVOLVE_SYSTEM.endswith(P._APP_EVOLVE_TAIL) and P.APP_EVOLVE_SYSTEM.startswith("APP TEST ")
        info = await (await c.get("/api/prompts/APP_REGEN_SYSTEM")).json()
        assert info["rebuilt"] and not info["overridden"] and info["derived_from"] == ["APP_SYSTEM", "_APP_REGEN_TAIL"], info
        assert s.LANGUAGE_PROMPTS[0].startswith("APP TEST ") and s.language_instruction(P.APP_SYSTEM) == ""   # translation off
        r = await c.post("/api/prompts/APP_REGEN_SYSTEM", json={"text": "OWN REGEN"})
        assert P.APP_REGEN_SYSTEM == "OWN REGEN"
        # EXPANDERS follow the three Krea prompts
        r = await c.post("/api/prompts/KREA_EXPAND_SYSTEM_NEUTRAL", json={"text": "You are an expert prompt engineer for text-to-image models. NEUTRAL TEST"})
        assert r.status == 200 and s.EXPANDERS["krea-neutral"].endswith("NEUTRAL TEST") and s.EXPANDERS["krea"] == P.KREA_EXPAND_SYSTEM
        # refused: a part, an unknown name, a non-string
        assert (await c.post("/api/prompts/STYLE", json={"text": "x"})).status == 400
        assert (await c.post("/api/prompts/_KREA_EXPAND_HEAD", json={"text": "x"})).status == 400
        assert (await c.post("/api/prompts/NO_SUCH_PROMPT", json={"text": "x"})).status == 404
        assert (await c.get("/api/prompts/NO_SUCH_PROMPT")).status == 404
        assert (await c.post("/api/prompts/ASK_SYSTEM", json={"text": 5})).status == 400
        # a text equal to the default is no override; reset removes the file
        r = await c.post("/api/prompts/KREA_EXPAND_SYSTEM_NEUTRAL", json={"text": P.DEFAULTS["KREA_EXPAND_SYSTEM_NEUTRAL"]})
        assert not (await r.json())["overridden"] and not (PDIR / "KREA_EXPAND_SYSTEM_NEUTRAL.txt").exists() \
            and s.EXPANDERS["krea-neutral"] == P.DEFAULTS["KREA_EXPAND_SYSTEM_NEUTRAL"]
        for name in ("ASK_SYSTEM", "APP_REGEN_SYSTEM", "APP_SYSTEM"):
            r = await c.post(f"/api/prompts/{name}", json={"reset": True})
            assert r.status == 200 and not (await r.json())["overridden"] and not (PDIR / f"{name}.txt").exists(), name
        assert P.ASK_SYSTEM == P.DEFAULTS["ASK_SYSTEM"] and P.APP_REGEN_SYSTEM == P.DEFAULTS["APP_REGEN_SYSTEM"] \
            and P.APP_EVOLVE_SYSTEM == P.DEFAULTS["APP_EVOLVE_SYSTEM"] and s.LANGUAGE_PROMPTS[0] == P.DEFAULTS["APP_SYSTEM"]
        r = await c.post("/api/fs", json={"op": "read", "path": "/system/prompts/ASK_SYSTEM.txt"})
        assert (await r.json())["content"] == P.DEFAULTS["ASK_SYSTEM"]
        # reload: files written by hand (an editor's trailing newline dropped), a part skipped, an unknown name reported
        (PDIR / "VISION_SYSTEM.txt").write_text("VISION TEST\n", encoding="utf-8")
        (PDIR / "STYLE.txt").write_text("x", encoding="utf-8")
        (PDIR / "UNKNOWN_THING.txt").write_text("y", encoding="utf-8")
        (PDIR / "WALLPAPER_PROMPT.txt").write_text(P.DEFAULTS["WALLPAPER_PROMPT"], encoding="utf-8")
        r = await c.post("/api/prompts", json={"reload": True})
        j = await r.json()
        assert r.status == 200 and j["loaded"] == ["VISION_SYSTEM"] and j["skipped"] == ["STYLE"] and j["unknown"] == ["UNKNOWN_THING.txt"] \
            and j["same"] == ["WALLPAPER_PROMPT"], j
        assert P.VISION_SYSTEM == "VISION TEST" and P.info("VISION_SYSTEM")["overridden"]
        for f in PDIR.glob("*.txt"):
            f.unlink()
        r = await c.post("/api/prompts", json={"reload": True})
        assert (await r.json())["loaded"] == [] and P.VISION_SYSTEM == P.DEFAULTS["VISION_SYSTEM"] and not P.OVERRIDES
        return "set/reset/reload through the API: file + attribute + compositions + EXPANDERS/LANGUAGE_PROMPTS + /system + the next request"
    finally:
        await c.close()


@test("pr_dock_prompts", ["R92"])
async def t_dock(ctx):
    s = kernel()
    P = s.prompts
    assert s.DOCK_PROMPTS.get("settings") == "DOCK_SETTINGS" and {"notes", "news", "files"} <= set(s.DOCK_PROMPTS), s.DOCK_PROMPTS
    stock = P.DEFAULTS["DOCK_SETTINGS"]
    assert stock.startswith("the kAIrOS system settings app") and "wiktionary_<lang>" in stock, "html-unescaped"
    c = await _client(s)
    try:
        info = await (await c.get("/api/prompts/DOCK_SETTINGS")).json()
        assert info["kind"] == "dock" and info["where"].startswith("shell/index.html:") and info["editable"] and info["text"] == stock, info
        line = int(info["where"].split(":")[1])
        assert 'data-app="settings"' in (REPO / "shell" / "index.html").read_text(encoding="utf-8").splitlines()[line - 1]
        new = "a tiny settings app: one heading 'DOCK OVERRIDE TEST' and the OS name from vibe.settings.get()"
        r = await c.post("/api/prompts/DOCK_SETTINGS", json={"text": new})
        assert r.status == 200 and P.get("DOCK_SETTINGS") == new and (Path(os.environ["KAIROS_PROMPTS_DIR"]) / "DOCK_SETTINGS.txt").read_text() == new
        assert s.dock_prompt("settings", stock) == new and s.dock_prompt("notes", "x") == "x" and s.dock_prompt("", "y") == "y"
        app_dir = s.APPS_DIR / "settings"
        shutil.rmtree(app_dir, ignore_errors=True)
        n0 = len(msgs())
        events = await sse(c, "/api/app", {"prompt": stock, "app_id": "settings"})
        kinds = [e for e, _ in events]
        assert (app_dir / "app.html").exists() and (app_dir / "meta.json").exists(), kinds
        meta = json.loads((app_dir / "meta.json").read_text())
        assert meta["prompt"] == new, meta.get("prompt", "")[:80]
        users = " ".join(m.get("user") or "" for m in msgs()[n0:] if isinstance(m.get("user"), str))
        assert "DOCK OVERRIDE TEST" in users and stock[:60] not in users, "the model got the override, not the shell's text"
        r = await c.post("/api/prompts/DOCK_SETTINGS", json={"reset": True})
        assert P.get("DOCK_SETTINGS") == stock and s.dock_prompt("settings", stock) == stock
        shutil.rmtree(app_dir, ignore_errors=True)
        return f"DOCK_SETTINGS at {info['where']}; the override reached the model and meta.json ({len(events)} events)"
    finally:
        await c.close()


@test("pr_lyrics_route", ["R93"])
async def t_lyrics(ctx):
    s = kernel()
    c = await _client(s)
    try:
        async def ask(body):
            r = await c.post("/api/lyrics", json=body)
            return r.status, await r.json()
        st, j = await ask({"prompt": "a song about the kernel that dreams"})
        assert st == 200, j
        assert j["title"].endswith("(t=1.2 k=80)") and j["duration"] == 180 and j["bpm"] == 118 and j["keyscale"] == "A minor" \
            and j["timesignature"] == "4" and j["language"] == "en" and j["attempts"] == 1 and j["fixes"] == [] \
            and j["sampling"] == {"temperature": 1.2, "top_k": 80} and "[Chorus]" in j["lyrics"] and j["caption"].startswith("mock synthwave"), j
        m = msgs()[-1]
        assert m["temperature"] == 1.2 and m["top_k"] == 80 and m["response_format"] == "json_schema" and m["max_tokens"] == s.LYRICS_MAX_TOKENS, m
        assert m["system"].startswith("You are the songwriter of kAIrOS") and m["system"].rstrip().endswith(s.prompts.UNCENSORED_LIMITS) \
            and m["user"].startswith("SONG: a song about the kernel that dreams") and "MUSIC STYLE" not in m["user"], m["user"]
        assert m["chat_template_kwargs"] == manifest()["llm"].get("chat_template_kwargs"), "thinking stays off"
        # the caller's pins and the style reach the model and win over its plan
        st, j = await ask({"prompt": "ein Lied über Regen", "style": "Schlager, Akkordeon", "language": "de", "duration": 240,
                           "bpm": 90, "keyscale": "f# maj", "timesignature": "3/4"})
        assert st == 200 and j["language"] == "de" and j["duration"] == 240 and j["bpm"] == 90 and j["keyscale"] == "F# major" \
            and j["timesignature"] == "3", j
        m = msgs()[-1]
        for needle in ("MUSIC STYLE: Schlager, Akkordeon", "LENGTH: 240 seconds", "(code de)", "BPM: 90", "KEY: f# maj", "TIME SIGNATURE: 3/4"):
            assert needle in m["user"], (needle, m["user"])
        # the tag repairs: an alias, a repeat marker, a prose bracket line, a long descriptor
        st, j = await ask({"prompt": "repair me [lyrics-badtags]"})
        L = j["lyrics"]
        assert st == 200 and "[Intro - spoken word]" in L and "[Chorus]" in L and "[Hook]" not in L and "x2" not in L \
            and "lights go down" not in L and "[Bridge - soft]" in L and "with piano" not in L and "[Outro]" in L, L
        assert len(j["fixes"]) >= 3 and any("would be sung" in f for f in j["fixes"]), j["fixes"]
        assert "[Verse 1]" in L and L.count("\n\n") <= 1
        # the retry, the exhausted retry, the cap, BPM/key lifted out of the caption
        st, j = await ask({"prompt": "retry me [lyrics-garbage]"})
        assert st == 200 and j["attempts"] == 2, j
        st, j = await ask({"prompt": "hopeless [lyrics-garbage-always]"})
        assert st == 502 and f"{s.LYRICS_ATTEMPTS} attempts" in j["error"], j
        st, j = await ask({"prompt": "an epic [lyrics-long]"})
        assert st == 200 and j["duration"] == s.MUSIC_DURATION_MAX == 512, j
        st, j = await ask({"prompt": "tempo [lyrics-bpm-in-caption]"})
        assert st == 200 and "BPM" not in j["caption"] and "minor" not in j["caption"] and j["bpm"] == 118 and j["keyscale"] == "A minor", j
        # the minors block: the brief at the entry, the written lyrics before they are sung; the 400s
        st, j = await ask({"prompt": "x [lyrics-minors]"})
        assert st == 400 and j["error"] == s.refusal.BLOCK_MESSAGE, j
        st, j = await ask({"prompt": "a 12 year old nude"})
        assert st == 400 and j["error"] == s.refusal.BLOCK_MESSAGE, j
        for body in ({}, {"prompt": "x", "bpm": 999}, {"prompt": "x", "language": "xx"}, {"prompt": "x", "duration": 5},
                     {"prompt": "x", "keyscale": "H dur"}):
            st, j = await ask(body)
            assert st == 400 and "error" in j, (body, st, j)
        st, j = await ask({"style": "only a style, no brief"})
        assert st == 200 and "SONG: a song in the music style below" in msgs()[-1]["user"], j
        # the unit side of the repairs
        fixed, fixes = s.lyrics_tag_fix("[Refrain]\nla la\n[verse 2 - soft]\nda\n[Guitar solo]\n[fade out]\n[whisper]\nhush")
        assert fixed.splitlines()[0] == "[Chorus]" and "[Verse 2 - soft]" in fixed and "[Guitar Solo]" in fixed and "[Fade Out]" in fixed \
            and "[whispered]" in fixed, fixed
        return "temperature 1.2 / top_k 80 / json_schema on the wire; pins, repairs, retry, cap, minors block, 400s"
    finally:
        await c.close()


@test("pr_lyrics_then_music", ["R93", "R31"])
async def t_lyrics_music(ctx):
    s = kernel()
    c = await _client(s)
    try:
        r = await c.post("/api/lyrics", json={"prompt": "a short jingle for the boot screen", "duration": 30})
        j = await r.json()
        assert r.status == 200 and j["duration"] == 30, j
        r = await c.post("/api/music", json={"prompt": j["caption"], "lyrics": j["lyrics"], "duration": j["duration"], "language": j["language"],
                                             "bpm": j["bpm"], "keyscale": j["keyscale"], "timesignature": j["timesignature"]})
        jm = await r.json()
        assert r.status == 200 and jm.get("job"), jm
        rec = await poll_job(c, jm["job"])
        assert rec["status"] == "done", rec
        assert rec["request"] == {"duration": 30, "bpm": 118, "keyscale": "A minor", "timesignature": "4", "vocal_language": "en"}, rec["request"]
        assert str(rec["result"]).startswith("/files/music/")
        return f"the plan reached the music engine: {rec['request']}"
    finally:
        await c.close()


@test("pr_shell_wiring", ["R92", "R93"])
def t_shell(ctx):
    js = (REPO / "shell" / "shell.js").read_text(encoding="utf-8")
    host = (REPO / "shell" / "bench" / "host.js").read_text(encoding="utf-8")
    index = (REPO / "shell" / "index.html").read_text(encoding="utf-8")
    pjs = (REPO / "shell" / "prompts.js").read_text(encoding="utf-8")
    css = (REPO / "shell" / "shell.css").read_text(encoding="utf-8")
    for needle in ('lyrics: (brief, opts) => call("lyrics", brief, opts)', 'if (method === "lyrics")', '"/api/lyrics"',
                   'melody: { title: "Melody — music studio"', "vibe.lyrics(brief", "const isLyrics=", "✍ Write lyrics",
                   'if (name === "prompts")', 'id: "prompts"', 'if (id === "prompts") return openStudio("prompts")', "sm-about-prompts"):
        assert needle in js, needle
    assert 'if (method === "lyrics")' in host and '"/api/lyrics"' in host, "host.js parity"
    melody = js.split('melody: { title: "Melody — music studio"')[1].split("arcade: {")[0]
    literal = melody.split("`", 1)[1].rsplit("`", 1)[0]                  # the template literal's content
    assert "${" not in literal and "`" not in literal, "the studio literal is template-safe"
    assert "vibe.music(p.value,lyrics,dur" in melody and "num(d)||(lyrics?150:60)" in melody
    assert index.index('src="/shell/models.js"') < index.index('src="/shell/prompts.js"')
    for needle in ("const kairosPrompts", '"/api/prompts"', "/api/prompts/", "reset: true", "window.kairosPrompts = kairosPrompts",
                   "textContent", ".value = it.text"):
        assert needle in pjs, needle
    assert "innerHTML" not in pjs, "server strings never as HTML"
    assert ".kp {" in css and ".kp-text" in css
    checks = (REPO / "dev" / "docker" / "run-js-checks.sh").read_text(encoding="utf-8")
    assert "/w/shell/prompts.js" in checks
    for doc, needle in (("docs/API.md", "vibe.lyrics("), ("docs/api/media.md", "vibe.lyrics("), ("docs/API.md", "/api/prompts")):
        assert needle in (REPO / doc).read_text(encoding="utf-8"), (doc, needle)
    node = shutil.which("node")
    if node:
        for f in ("shell/shell.js", "shell/prompts.js", "shell/bench/host.js"):
            subprocess.run([node, "--check", str(REPO / f)], check=True, capture_output=True)
    return "bridge + handlers (shell.js, host.js), Melody's prompt box, the panel, the docs" + (" — node --check OK" if node else " (no node: syntax unchecked here)")


@test("pr_sync_script", ["R92"])
async def t_sync(ctx):
    s = kernel()
    P = s.prompts
    script = REPO / "scripts" / "prompt-sync.py"
    tmp = Path(tempfile.mkdtemp(prefix="kairos-ps-"))
    env = dict(os.environ, KAIROS_HOME=str(tmp / "home"))

    async def run(*args, rc=0):
        proc = await asyncio.create_subprocess_exec(sys.executable, str(script), *args, stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.STDOUT, env=env)
        out, _ = await asyncio.wait_for(proc.communicate(), 120)
        out = out.decode("utf-8", "ignore")
        assert proc.returncode == rc, (args, proc.returncode, out[-1200:])
        return out
    c = await _client(s)
    try:
        url = str(c.make_url("/")).rstrip("/")
        out = await run("list")
        assert "LYRICS_SYSTEM" in out and "DOCK_SETTINGS" in out and "kernel/prompts.py:" in out
        ex = tmp / "ex"
        out = await run("export", str(ex))
        files = sorted(p.stem for p in ex.glob("*.txt"))
        assert files == sorted(P.names()) and (ex / "INDEX.md").exists(), out
        assert (ex / "APP_SYSTEM.txt").read_text(encoding="utf-8") == P.DEFAULTS["APP_SYSTEM"]
        out = await run("diff", str(ex))
        assert "none differs" in out
        (ex / "ASK_SYSTEM.txt").write_text("ASK FROM FILE\n", encoding="utf-8")      # an editor's trailing newline
        (ex / "STYLE.txt").write_text("x", encoding="utf-8")                          # a part: skipped
        out = await run("diff", str(ex), "--full", rc=1)
        assert "edited ASK_SYSTEM" in out and "skipped STYLE" in out and "-You are the imagination engine" in out, out
        ovr = tmp / "ovr"
        await run("push", str(ex), "--dir", str(ovr))
        assert [p.name for p in ovr.glob("*.txt")] == ["ASK_SYSTEM.txt"] and (ovr / "ASK_SYSTEM.txt").read_text() == "ASK FROM FILE"
        await run("pull", "--dir", str(ovr), str(tmp / "pulled"))
        assert (tmp / "pulled" / "ASK_SYSTEM.txt").read_text() == "ASK FROM FILE"
        # live: push to the kernel, list/export/pull from it, reset
        out = await run("push", str(ex), "--kernel", url)
        assert P.ASK_SYSTEM == "ASK FROM FILE" and "set   ASK_SYSTEM: 200" in out, out
        out = await run("list", "--kernel", url)
        assert re.search(r"ASK_SYSTEM\s+\d+ chars\s+kernel/prompts.py:\d+\s+EDITED", out), out
        await run("export", str(tmp / "live"), "--kernel", url)
        assert (tmp / "live" / "ASK_SYSTEM.txt").read_text() == "ASK FROM FILE" and (tmp / "live" / "APP_SYSTEM.txt").read_text() == P.APP_SYSTEM
        await run("pull", "--kernel", url, str(tmp / "pulled2"))
        assert [p.name for p in (tmp / "pulled2").glob("*.txt")] == ["ASK_SYSTEM.txt"]
        (ex / "ASK_SYSTEM.txt").write_text(P.DEFAULTS["ASK_SYSTEM"], encoding="utf-8")
        out = await run("push", str(ex), "--kernel", url)
        assert not P.info("ASK_SYSTEM")["overridden"] and "reset ASK_SYSTEM" in out, out
        await run("push", str(tmp / "pulled"), "--kernel", url)
        assert P.ASK_SYSTEM == "ASK FROM FILE"
        await run("reset", "--all", "--kernel", url)
        assert P.ASK_SYSTEM == P.DEFAULTS["ASK_SYSTEM"] and not P.OVERRIDES
        # bake, on a copy of the tree: a plain prompt, an f-string prompt, a dock prompt, a composition (refused)
        repo = tmp / "repo"
        (repo / "kernel").mkdir(parents=True), (repo / "shell").mkdir()
        shutil.copy2(REPO / "kernel" / "prompts.py", repo / "kernel" / "prompts.py")
        shutil.copy2(REPO / "shell" / "index.html", repo / "shell" / "index.html")
        bk = tmp / "bk"
        bk.mkdir()
        (bk / "APP_SYSTEM.txt").write_text(P.DEFAULTS["APP_SYSTEM"] + " BAKED.", encoding="utf-8")
        (bk / "WALLPAPER_PROMPT.txt").write_text('a wallpaper with "quotes", a backslash \\ and """triple"""', encoding="utf-8")
        (bk / "DOCK_NOTES.txt").write_text('a notes app BAKED <tag> & "quotes" and \'apostrophes\'', encoding="utf-8")
        (bk / "APP_REGEN_SYSTEM.txt").write_text("comp", encoding="utf-8")
        out = await run("bake", str(bk), "--repo", str(repo), "--check", rc=1)
        assert "would bake APP_SYSTEM" in out and "refused APP_REGEN_SYSTEM" in out and "composition" in out, out
        before = (repo / "kernel" / "prompts.py").read_text(encoding="utf-8")
        out = await run("bake", str(bk), "--repo", str(repo), rc=1)
        assert "baked 2 prompt(s) into kernel/prompts.py" in out and "baked 1 dock prompt(s)" in out, out
        new = load_module("baked_prompts", repo / "kernel" / "prompts.py")
        src = (repo / "kernel" / "prompts.py").read_text(encoding="utf-8")
        seg = src.split('APP_SYSTEM = f"""', 1)[1][:7000]
        assert new.APP_SYSTEM == P.DEFAULTS["APP_SYSTEM"] + " BAKED." and "{STYLE}" in seg and "{ADULT_RULE_APP_SHORT}" in seg, "the f-string keeps its parts"
        assert new.APP_REGEN_SYSTEM == new.APP_SYSTEM + new._APP_REGEN_TAIL and new.APP_EVOLVE_SYSTEM.startswith(new.APP_SYSTEM)
        assert new.WALLPAPER_PROMPT == 'a wallpaper with "quotes", a backslash \\ and """triple"""'
        for name in P.names():
            if name not in ("APP_SYSTEM", "WALLPAPER_PROMPT", "APP_REGEN_SYSTEM", "APP_EVOLVE_SYSTEM") and not name.startswith("DOCK_"):
                assert new.DEFAULTS[name] == P.DEFAULTS[name], name
        raw = re.search(r'data-app="notes" data-prompt="([^"]*)"', (repo / "shell" / "index.html").read_text(encoding="utf-8")).group(1)
        assert _html.unescape(raw) == 'a notes app BAKED <tag> & "quotes" and \'apostrophes\'' and "&lt;tag&gt;" in raw and "&quot;" in raw
        baks = sorted(p.name for p in (tmp / "home" / "repo-backups").rglob("*.bak-*"))
        assert any(b.startswith("prompts.py.bak-") for b in baks) and any(b.startswith("index.html.bak-") for b in baks), baks
        assert before != src and before.count("\n") < src.count("\n") + 400
        # the document
        out = await run("html", "-o", str(tmp / "doc.html"))
        doc = (tmp / "doc.html").read_text(encoding="utf-8")
        assert doc.startswith("<!DOCTYPE html>") and "<title>kAIrOS Architecture and Prompts</title>" in doc
        for name in P.names():
            assert f'id="p-{name}"' in doc, name
        assert "LYRICS_SAMPLING" in doc and "prefers-color-scheme: dark" in doc and _html.escape(P.DEFAULTS["ASK_SYSTEM"]) in doc
        out = await run("html", "--fragment", "-o", str(tmp / "frag.html"), "--kernel", url)
        frag = (tmp / "frag.html").read_text(encoding="utf-8")
        assert frag.startswith("<title>") and "<!DOCTYPE" not in frag and "override dir" in frag
        return f"export {len(files)} files, diff, push --dir/--kernel, pull, reset, bake (2 + 1 dock, the composition refused), html"
    finally:
        await c.close()
        shutil.rmtree(tmp, ignore_errors=True)


async def run_all(tests):
    """every test in ONE event loop: the kernel's locks, its llama-server child and the mock live in this loop"""
    ctx = {}
    for t in tests:
        await t(ctx)
    try:
        if "server" in _K:
            await asyncio.wait_for(_K["server"].MM.stop_llama(), 30)
    except Exception as e:
        print(f"  (llama mock stop: {e})")


def main():
    tests = [v for v in globals().values() if getattr(v, "_test", False)]
    print(f"== tests/test_prompts.py: {len(tests)} tests ==", flush=True)
    asyncio.run(run_all(tests))
    if "dir" in _K:
        shutil.rmtree(_K["dir"], ignore_errors=True)
    out = {"ran_at": time.strftime("%Y-%m-%d %H:%M:%S"), "results": RESULTS}
    if not os.environ.get("KAIROS_TEST_ONLY"):
        (REPO / "tests" / "results-prompts.json").write_text(json.dumps(out, indent=1))
    fails = [n for n, r in RESULTS.items() if r["status"] == "fail"]
    passes = sum(1 for r in RESULTS.values() if r["status"] == "pass")
    skips = sum(1 for r in RESULTS.values() if r["status"] == "skip")
    print(f"\n{passes} passed, {len(fails)} failed, {skips} skipped -> tests/results-prompts.json", flush=True)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
