#!/usr/bin/env python3
"""kAIrOS automated test suite.

Every test maps to requirement IDs from docs/REQUIREMENTS.md. Run through
tests/run-tests.sh (which boots the kernel on :1338 against mock engines).
Writes tests/results.json consumed by scripts/update-progress.py.
"""
import asyncio
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

try:
    import aiohttp
except ImportError:  # pre-engine-install: unit subset still runs
    aiohttp = None

BASE = os.environ.get("KAIROS_URL", "http://127.0.0.1:1338")
REPO = Path(__file__).resolve().parent.parent
VDIR = Path(os.environ.get("VIBEOS_DIR", "/tmp/kairos-test"))
RESULTS = {}


def record(name, reqs, status, detail=""):
    RESULTS[name] = {"reqs": reqs, "status": status, "detail": detail[:300]}
    mark = {"pass": "✓", "fail": "✗", "skip": "~"}[status]
    print(f"  {mark} {name} [{', '.join(reqs)}] {detail[:120]}", flush=True)


def run_test(name, reqs):
    def deco(fn):
        async def wrapper(session):
            try:
                detail = await fn(session)
                record(name, reqs, "pass", detail or "")
            except SkipTest as e:
                record(name, reqs, "skip", str(e))
            except Exception as e:
                record(name, reqs, "fail", f"{type(e).__name__}: {e}")
        wrapper._test = True
        wrapper._name = name
        wrapper._reqs = reqs
        return wrapper
    return deco


class SkipTest(Exception):
    pass


async def sse_collect(session, url, payload):
    """POST and parse an SSE stream into a list of (event, data) tuples."""
    events = []
    async with session.post(url, json=payload,
                            timeout=aiohttp.ClientTimeout(total=120)) as r:
        buf = ""
        async for chunk in r.content.iter_any():
            buf += chunk.decode("utf-8", "ignore")
            while "\n\n" in buf:
                frame, buf = buf.split("\n\n", 1)
                ev = data = None
                for line in frame.splitlines():
                    if line.startswith("event: "):
                        ev = line[7:]
                    elif line.startswith("data: "):
                        data = json.loads(line[6:])
                if ev:
                    events.append((ev, data))
    return events


async def poll_job(session, jid, timeout=180):
    t0 = time.time()
    while time.time() - t0 < timeout:
        async with session.get(f"{BASE}/api/job/{jid}") as r:
            j = await r.json()
        if j["status"] == "done":
            return j
        if j["status"] == "error":
            raise AssertionError(f"job error: {j['error']}")
        await asyncio.sleep(1)
    raise AssertionError("job timeout")


# ---------------- unit tests (no HTTP) ----------------

@run_test("unit_prompts_style_mandate", ["R13"])
async def t_prompts(_):
    sys.path.insert(0, str(REPO / "kernel"))
    import prompts
    # the mandate applies to the OS's UI design language (apps/games/pages)…
    ui = [prompts.APP_SYSTEM, prompts.APP_EVOLVE_SYSTEM, prompts.GAME_SYSTEM,
          prompts.GAME_FIX_SYSTEM, prompts.ASK_SYSTEM, prompts.BROWSER_SYSTEM]
    missing = [i for i, t in enumerate(ui) if "modern and futuristic" not in t]
    assert not missing, f"UI prompts missing the mandate: indexes {missing}"
    # …but NOT to media prompts (user request 2026-07-10): what the user asks the
    # image/video/music models to make must not be style-hijacked
    media = [prompts.IMAGE_PROMPT_HINT, prompts.VIDEO_PROMPT_HINT,
             prompts.MUSIC_PROMPT_HINT, prompts.WALLPAPER_PROMPT,
             prompts.WALLPAPER_SYSTEM]
    tainted = [i for i, t in enumerate(media) if "modern and futuristic" in t]
    assert not tainted, f"media prompts still style-forced: indexes {tainted}"
    return f"{len(ui)} UI prompts carry the mandate; {len(media)} media prompts are free of it"


@run_test("unit_validate_html", ["R12"])
async def t_validate(_):
    sys.path.insert(0, str(REPO / "kernel"))
    import server
    good = "<!DOCTYPE html><html><head><script>let a=1;</script></head><body></body></html>"
    assert server.validate_html(good) == []
    bad_cases = ["no doctype here", "<!DOCTYPE html><html><body>truncated",
                 "<!DOCTYPE html><html><script>x</html>",
                 "<!DOCTYPE html><html>```js fence``` </html>",
                 '<!DOCTYPE html><html><img src="https://x.com/a.png"></html>']
    caught = sum(1 for b in bad_cases if server.validate_html(b))
    assert caught == len(bad_cases), f"only {caught}/{len(bad_cases)} defects caught"
    return f"{len(bad_cases)} defect classes detected, clean doc passes"


@run_test("unit_jsfix_await_rewrite", ["R12"])
async def t_jsfix(_):
    """docs/LLM-BENCH-2026-09.md "Shared generation failures": an `await` inside a function
    that is not async, or at the top level of a classic script, killed 11 of 48 benchmark
    apps at parse time. jsfix adds the missing `async` / wraps the script in an async IIFE
    that re-exports the top-level declarations to window (inline onclick handlers keep
    working). Pure Python — no aiohttp needed."""
    sys.path.insert(0, str(REPO / "kernel"))
    import jsfix
    # 1. classification against the shapes found in the corpus
    i = jsfix.scan_js("function resetTimer(){ x = await vibe.load('a'); }\nsetInterval(() => { await tick(); }, 1000);")
    assert not i.top_level_await and i.await_in_sync_fn and len(i.async_inserts) == 2, vars(i)
    i = jsfix.scan_js("let habitList = (await vibe.load('h'))?.list || [];\nfunction addHabit(){}\nconst render = () => {};")
    assert i.top_level_await and not i.await_in_sync_fn and i.names == ["habitList", "addHabit", "render"], vars(i)
    for ok in ("async function init(){ await vibe.load('x'); } init();",
               "btn.onclick = async () => { await vibe.store('a', 1); };",
               "class A { async load(){ await x(); } }",
               "const s = 'await x'; // await y\n/* await z */ const r = /await/;",
               "document.addEventListener('DOMContentLoaded', async () => { await init(); });"):
        i = jsfix.scan_js(ok)
        assert not i.top_level_await and not i.await_in_sync_fn, ok
    # 2. the rewrites: async keyword inserted, TLA wrapped with window re-exports, idempotent
    html = ("<!DOCTYPE html><html><head></head><body><button onclick=\"save()\">s</button>"
            "<script>\nfunction save(){ await vibe.store('k', 1); }\nconst o = { load(){ await x(); } };\n</script>"
            "<script type=\"module\">const a = await b();</script>"
            "<script>\nconst data = await vibe.load('k');\nfunction render(){}\nconst add = () => 1;\n</script>"
            "</body></html>")
    out = jsfix.harden_app_html(html)
    assert "async function save(){ await" in out and "{ async load(){ await" in out, out
    assert "<script type=\"module\">const a = await b();</script>" in out, "module scripts are left alone"
    assert out.count("kairos:tla") == 1 and "(async function(){" in out, out
    assert "w.render=render" in out and "w.add=add" in out and "w.data=data" in out, out
    assert out.endswith("__kairosExport()}).call(window).catch(function(e){setTimeout(function(){throw e})});</script></body></html>"), out[-160:]
    assert jsfix.harden_app_html(out) == out, "not idempotent"
    # line numbers survive: the wrapper lives on the script's first and last line
    assert out.count("\n") == html.count("\n") + 1
    # a script without await is untouched byte for byte
    plain = "<script>function f(){ return 1 }</script>"
    assert jsfix.harden_app_html(plain) == plain
    # 3. the static check the repair round sees (no rewrite can fix a direct API call)
    assert jsfix.js_problems("<script>fetch('/api/status')</script>"), "direct /api fetch must be flagged"
    assert jsfix.js_problems('<script>fetch("http://127.0.0.1:1337/api/weather")</script>')
    assert jsfix.js_problems("<script>vibe.ask('x'); fetch('/genimg/x')</script>") == []
    # 4. [D5] blocked native dialogs: prompt()/confirm() make a feature dead (null/false) — flagged for
    # the repair round (Gemma 4 still wrote prompt("Enter filename") in the 2026-09-28 smoke); alert()
    # only shows the OS notice and passes; an app's own foo.prompt()/promptUser()/`prompt` variable pass
    S = lambda js: f"<script>{js}</script>"
    assert jsfix.dialog_calls(S('const name = prompt("Enter filename:");')) == ["prompt"]
    assert jsfix.dialog_calls(S('if (!window.confirm("Delete?")) return; const n = prompt("y")')) == ["confirm", "prompt"]
    assert jsfix.dialog_calls(S('vibe.image(prompt); this.prompt("x"); ui.confirm(); showConfirm(); let prompt = "a"; alert("s")')) == []
    dlg = jsfix.js_problems('<script>const n = prompt("f");</script>')
    assert len(dlg) == 1 and dlg[0].startswith("the app calls prompt()") and "in-page <input>" in dlg[0], dlg
    assert jsfix.js_problems('<script>alert("saved")</script>') == []
    # 5. [N1, docs/REVERIFY-2026-09-28.md] the check reads executable code only — the re-verification's
    # false positives (each cost a ~4 min whole-document repair round) must all pass …
    for fp in ('<input placeholder="Describe your image prompt (e.g. a red fox)">',
               '<p>please confirm (type YES below)</p>',
               S('// never use prompt() here\nlet x = 1;'),
               S('/* confirm(x) is bad */ const s = "confirm() is blocked"; const t = \'prompt("a")\';'),
               S('vibe.ask("Write an image prompt (short) for a fox")'),
               S('const m = `Describe your image prompt (e.g. a fox)`; const r = /prompt\\(/;'),
               S('const ui = { prompt(q) { return q }, confirm(q) { return true } }; ui.prompt("x"); ui.confirm("y"); this.prompt("z"); promptUser("a"); showConfirm();'),
               S('class Dlg { async confirm(q) { return true } static prompt(q) {} } new Dlg().confirm("x")'),
               S('function prompt(msg) { return "own" } prompt("x");'),
               S('const prompt = (q) => q; prompt("x"); let confirm = 1;'),
               '<script type="application/json">{"a": "prompt(x)"}</script><script type="text/template"><b>confirm(</b></script>',
               '<script src="/x.js">prompt("no")</script>',
               S('const p = document.getElementById("prompt"); items.then(prompt); setTimeout(confirm, 1)'),
               '<div title="confirm(x)" data-x="prompt(y)">confirm(</div>',
               S('x.window.prompt("a"); foo?.confirm("b"); obj.self.prompt("c")')):
        assert jsfix.dialog_calls(fp) == [], f"false positive: {fp[:80]} -> {jsfix.dialog_calls(fp)}"
    # … and the true positives, incl. the forms the old regex missed (self./globalThis./top./window?./window["prompt"])
    for tp, want in ((S('self.prompt("a")'), ["prompt"]), (S('globalThis.confirm("a")'), ["confirm"]),
                     (S('top.prompt("a")'), ["prompt"]), (S('window?.prompt("a")'), ["prompt"]),
                     (S('window["prompt"]("a")'), ["prompt"]), (S('const s = `hi ${prompt("name")}`;'), ["prompt"]),
                     ('<button onclick="if(confirm(\'sure?\')) del()">x</button>', ["confirm"]),
                     ("<button onclick='var n = prompt(&quot;name&quot;); go(n)'>x</button>", ["prompt"]),
                     ('<script type="module">const a = prompt("m");</script>', ["prompt"]),
                     (S('while (confirm("again?")) { tick() }'), ["confirm"]), (S('else if (confirm("x")) {\n }'), ["confirm"]),
                     (S('const answer = window.confirm(`Are you sure you want to delete "${name}"?`);'), ["confirm"]),
                     (S('btn.onclick = async () => { const name = prompt("File name?"); if (name) await vibe.fs.write("/data/" + name, ""); };'), ["prompt"])):
        assert jsfix.dialog_calls(tp) == want, f"missed: {tp[:80]} -> {jsfix.dialog_calls(tp)}"
    assert jsfix.executable_js('<a onclick="x()" onMouseOver=\'y()\'>t</a><script>z()</script>') == \
        [("script", "z()"), ("onclick", "x()"), ("onmouseover", "y()")]
    return "sync-fn async insertion + TLA wrap with window re-exports, idempotent, module scripts untouched; prompt()/confirm() flagged in code only (15 FP forms pass, 13 TP forms caught)"


@run_test("http_app_dialog_check_code_only", ["R12", "R45", "R5"])
async def t_dialog_check_pipeline(s):
    """N1 end to end on the deterministic path (the re-verification's `run_mock_d5.sh`, now in the
    suite): an app that wires "New file" to prompt() gets the D5 repair round before `done` and is
    delivered clean; an image app with NO dialog — only the placeholder "Describe your image prompt
    (e.g. …)", a comment and a string naming prompt()/confirm() — gets NO repair round at all
    (before the fix the identical placeholder cost a ~4 min whole-document rewrite)."""
    sys.path.insert(0, str(REPO / "kernel"))
    import jsfix
    events = await sse_collect(s, f"{BASE}/api/app", {"prompt": "a notes app [uses-prompt]", "app_id": "dlg-uses-prompt"})
    deltas = "".join(d.get("text", "") for ev, d in events if ev == "delta")
    done = [d for ev, d in events if ev == "done"]
    assert done and "self-repair: the app calls prompt()" in deltas, deltas[-500:]
    assert jsfix.dialog_calls(done[0]["html"]) == [] and 'prompt("File name?")' not in done[0]["html"]
    events = await sse_collect(s, f"{BASE}/api/app", {"prompt": "an image studio [image-prompt-field]", "app_id": "dlg-field"})
    deltas = "".join(d.get("text", "") for ev, d in events if ev == "delta")
    done = [d for ev, d in events if ev == "done"]
    assert done, [ev for ev, _ in events]
    assert "self-repair" not in deltas, f"needless repair round: {deltas[-400:]}"
    html = done[0]["html"]
    assert 'placeholder="Describe your image prompt (e.g. a red fox)"' in html and "never use prompt() here" in html, "the app was rewritten"
    assert jsfix.dialog_calls(html) == [] and done[0].get("broken") is None
    return "prompt() app → one D5 repair round → clean; placeholder-only image app → delivered as generated, no repair round"


@run_test("unit_bridge_shims_theme_static", ["R12", "R13", "R66"])
async def t_bridge_static(_):
    """shell.js BRIDGE_SRC (the text every app gets — shell.js, host.js and the kernel's
    /app/<id> page inject the same literal): carries the base theme, the storage shims and
    the vibe.* aliases/guard, without widening the sandbox. The behavioural test runs in
    Node (tests/bridge-test.js, node:24-alpine image — no node here)."""
    js = (REPO / "shell" / "shell.js").read_text()
    m = re.search(r"const BRIDGE_SRC = `(.*?)`;\n", js, re.S)
    assert m, "BRIDGE_SRC literal not found"
    b = m.group(1)
    assert "${" not in b and "`" not in b, "template literal must be inert"
    assert not re.search(r"\\(?!/script>)", b), "no backslash escapes: the kernel copies the raw literal"
    for needle in ('<style data-kairos-theme>', 'color-scheme:dark', ':where(button', ':where(body)',
                   'Object.defineProperty(document, "cookie"', 'define(window, "localStorage"',
                   'define(window, "sessionStorage"', 'define(window, "indexedDB"', '"__localStorage"',
                   'is not part of the kAIrOS API', 'get: (key) => call("load", key)',
                   'no such device on this machine', 'method: "jsError"'):
        assert needle in b, f"bridge lacks {needle!r}"
    style = re.search(r"<style data-kairos-theme>(.*?)</style>", b, re.S).group(1)
    for line in style.strip().splitlines():
        sel = line[:line.index("{")].strip()
        assert re.fullmatch(r":where\((?:[^()]|\([^()]*\))*\)(?:::[\w-]+)?", sel), f"non-zero specificity: {sel}"
    # the sandbox: app iframes exactly as before, nowhere allow-same-origin
    for f in ("shell/shell.js", "shell/bench/host.js"):
        src = (REPO / f).read_text()
        mount = re.search(r"async function mountApp\([^)]*\) \{.*?\n\}", src, re.S).group(0)
        assert 'iframe.sandbox = "allow-scripts allow-pointer-lock"' in mount, f
        assert "allow-same-origin" not in re.sub(r"/\*.*?\*/", "", src, flags=re.S), f
    assert "/api/" not in b.split("<script>", 1)[1], "the bridge never calls the REST API itself"
    # the kernel injects the same text plus the app's localStorage seed
    ks = (REPO / "kernel" / "server.py").read_text()
    assert "storage_seed_script(seed) + bridge_src()" in ks and 'f"{app_id}::__localStorage"' in ks
    return "theme (zero specificity) + storage shims + aliases/guard in BRIDGE_SRC; sandbox unchanged"


@run_test("unit_image_dims_and_seed_script", ["R12", "R58"])
async def t_image_dims(_):
    """/api/image must not fail a job on junk width/height (`prompts.map(vibe.image)` sends
    (prompt, index, array) — 5 of 6 gallery pictures died on int() of a list); the
    /app/<id> page embeds the localStorage seed JSON-escaped."""
    sys.path.insert(0, str(REPO / "kernel"))
    import server
    assert server.image_dims(768, 512) == (768, 512)
    assert server.image_dims("1024", "576.0") == (1024, 576)
    for w, h in ((1, ["a", "b"]), (None, None), (0, 0), ("x", 5), (20000, 512), (2, 3)):
        assert server.image_dims(w, h) is None, (w, h)
    s = server.storage_seed_script({"a": "</script><b>"})
    assert s.startswith("<script>window.__kairosSeed=") and "</script><b>" not in s[8:-9] and s.endswith("</script>")
    assert json.loads(s[len("<script>window.__kairosSeed="):-len("</script>")]) == {"localStorage": {"a": "</script><b>"}}
    assert server.storage_seed_script(None) == "" and server.storage_seed_script({}) == ""
    # validate_html carries the direct-API check
    assert any("fetch('/api/" in p for p in server.validate_html(
        "<!DOCTYPE html><html><head></head><body><script>fetch('/api/status')</script></body></html>"))
    return "junk dims ignored, real dims kept; seed escaped; validate_html flags fetch('/api/…')"


@run_test("unit_strip_thinking_blocks", ["R5", "R12"])
async def t_strip_thinking(_):
    """A hybrid-reasoning LLM (Qwen3.5: thinks by default) must never leak <think> text
    into an app, a page or a game: strip_fences drops the block on the HTML and the
    Python path, validate_html flags a survivor (docs/LLM-BENCH-2026-09.md, Phase 0)."""
    sys.path.insert(0, str(REPO / "kernel"))
    import server
    doc = "<!DOCTYPE html><html><head></head><body>hi</body></html>"
    # the usual shape: a think block, then the document (with or without fences)
    assert server.strip_fences("<think>\nplan the todo app…\n</think>\n\n" + doc, "html") == doc
    assert server.strip_fences("<think>x</think>\n```html\n" + doc + "\n```", "html").strip() == doc
    # python: the game forge compiles what strip_fences returns
    py = "import pygame\nprint(1)\n"
    out = server.strip_fences("<THINK>reasoning</THINK>\n```python\n" + py + "```", "python")
    compile(out, "main.py", "exec")
    assert out.strip() == py.strip(), out
    # two blocks, and a block inside the document body
    assert server.strip_fences("<think>a</think><think>b</think>" + doc, "html") == doc
    inner = doc.replace("hi", "<think>c</think>hi")
    assert server.strip_fences(inner, "html") == doc
    # unterminated (the model ran out of tokens while thinking): the tag goes, the
    # remainder is judged as usual — validate_html reports the missing document
    stump = server.strip_fences("<think>still thinking about", "html")
    assert "<think>" not in stump
    assert server.validate_html(stump), "a think stump must not validate as an app"
    # a survivor inside the document is a defect the self-repair round sees
    assert any("<think>" in p for p in server.validate_html(doc.replace("hi", "<think>z</think>hi")))
    assert server.validate_html(doc) == []
    # thinking-free output is untouched (the baseline model's path)
    assert server.strip_fences(doc, "html") == doc
    return "think blocks stripped on html+python, stump/survivor flagged, plain output untouched"


@run_test("unit_chat_template_kwargs_manifest", ["R3", "R5"])
async def t_chat_template_kwargs(_):
    """A model section's optional `chat_template_kwargs` travels in every
    /v1/chat/completions body (llama-server merges it into the Jinja template:
    {"enable_thinking": false} switches Qwen3.5's default thinking off); without the
    field the body is byte-for-byte what the kernel always sent — so the shipped
    manifests (none carries it) are unaffected."""
    sys.path.insert(0, str(REPO / "kernel"))
    import server
    plain = {"llm": {"name": "x", "file": "llm/x.gguf", "serve_args": ["-c", "4096"]},
             "python_llm": {"shared_with": "llm"}}
    mm = server.ModelManager(plain)
    msgs = [{"role": "user", "content": "Open this app: a todo list"}]
    body = mm.chat_payload("llm", "SYS", msgs, 16384, 0.75, True)
    assert body == {"messages": [{"role": "system", "content": "SYS"}, *msgs],
                    "max_tokens": 16384, "temperature": 0.75, "stream": True}, body
    assert "chat_template_kwargs" not in mm.chat_payload("python_llm", "S", msgs, 100, 0.4, False)
    # the shipped tier manifests: the 8/16 GB brain (Gemma 4, docs/FIXES-2026-09-28.md A1) carries
    # the thinking-off switch, the 24/32 GB coder models carry nothing
    for m in (REPO / "manifests").glob("tier-*.json"):
        spec = json.loads(m.read_text())["llm"]
        if m.name in ("tier-8gb.json", "tier-16gb.json"):
            assert spec["chat_template_kwargs"] == {"enable_thinking": False}, m.name
        else:
            assert "chat_template_kwargs" not in spec, m.name
    think = {"llm": dict(plain["llm"], chat_template_kwargs={"enable_thinking": False}),
             "python_llm": {"shared_with": "llm"}}
    mm2 = server.ModelManager(think)
    for key, stream in (("llm", True), ("python_llm", False)):      # shared_with resolves to llm
        b = mm2.chat_payload(key, "SYS", msgs, 700, 0.7, stream)
        assert b["chat_template_kwargs"] == {"enable_thinking": False}, (key, b)
        assert b["stream"] is stream and b["max_tokens"] == 700
    # llama-server wants an object; an empty one or junk is dropped, not sent
    for junk in ({}, "false", None, 3):
        mm3 = server.ModelManager({"llm": dict(plain["llm"], chat_template_kwargs=junk)})
        assert "chat_template_kwargs" not in mm3.chat_payload("llm", "S", msgs, 1, 0.1, False), junk
    return "field absent -> unchanged body; present -> passed through for llm and python_llm"


@run_test("unit_vfs_android_partitions", ["R16", "R17"])
async def t_vfs(_):
    sys.path.insert(0, str(REPO / "kernel"))
    from vfs import VFS, ReadOnlyError
    tmp = Path("/tmp/kairos-vfs-test")
    shutil.rmtree(tmp, ignore_errors=True)
    fs = VFS(tmp)
    fs.write("/data/a/b.txt", "hello")
    assert fs.read("/data/a/b.txt") == "hello"
    assert any(e["name"] == "a" for e in fs.list("/data"))
    try:
        fs.write("/system/evil.txt", "x")
        raise AssertionError("/system write was allowed")
    except ReadOnlyError:
        pass
    try:
        fs.read("/data/../../etc/passwd")
        raise AssertionError("traversal allowed")
    except PermissionError:
        pass
    fs.delete("/data/a/b.txt")
    fs.write("/data/c.txt", "x")
    fs.format_data()
    assert not (tmp / "fs" / "data" / "c.txt").exists()
    return "write/read/delete OK; /system read-only; traversal blocked; format wipes"


@run_test("unit_no_cloud_ai", ["R2"])
async def t_local(_):
    src = (REPO / "kernel" / "server.py").read_text() + (REPO / "kernel" / "knowledge.py").read_text()
    for vendor in ("anthropic", "openai.com", "googleapis"):
        assert vendor not in src.lower(), f"cloud AI reference found: {vendor}"
    return "kernel talks only to local engines (DDG used for search facts, not generation)"


@run_test("unit_identity", ["R19"])
async def t_name(_):
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    shell = (REPO / "shell" / "index.html").read_text(encoding="utf-8")
    assert "kAIrOS" in readme and "artificial intelligence" in readme
    assert "mAIer" in readme and "kAIrOS" in shell
    assert len("kAIrOS".replace(" ", "")) <= 8
    return "kAIrOS by mAIer present in README + shell; ≤8 letters; AI+OS contained"


@run_test("unit_slim_base_builder", ["R8", "R3"])
async def t_builder(_):
    body = (REPO / "scripts" / "build-image.sh").read_text(encoding="utf-8")
    assert "debootstrap" in body and "minbase" in body
    for t in ("8gb", "16gb", "24gb", "32gb"):
        m = json.loads((REPO / "manifests" / f"tier-{t}.json").read_text())
        assert all(k in m for k in ("llm", "image", "video", "knowledge")), t
    return "minbase builder present; 4 tier manifests complete (integration build = manual)"


# ---------------- HTTP tests against the kernel (mock engines) ----------------

@run_test("http_status_tier", ["R3"])
async def t_status(s):
    async with s.get(f"{BASE}/api/status") as r:
        st = await r.json()
    assert st["tier"] == "8gb" and st["llm"]
    # the compute backend is reported honestly, with the evidence it rests on (D12)
    assert st["engine"] in ("cuda", "vulkan", "cpu") and set(st["engine_info"]) == {"nvidia_driver", "vulkan_set", "forced"}
    if not st["engine_info"]["nvidia_driver"] and not st["engine_info"]["vulkan_set"] and not st["engine_info"]["forced"]:
        assert st["engine"] == "cpu", st
    return f"tier={st['tier']} llm={st['llm']} engine={st['engine']}"


@run_test("http_app_generate", ["R1", "R5"])
async def t_app(s):
    events = await sse_collect(s, f"{BASE}/api/app",
                               {"prompt": "test counter app", "app_id": "test-counter"})
    done = [d for e, d in events if e == "done"]
    assert done and "<!DOCTYPE" in done[0]["html"], f"events: {[e for e, _ in events]}"
    return f"v{done[0].get('version')} generated, {len(done[0]['html'])} bytes"


@run_test("http_app_reopen_is_instant_cached", ["R24", "R35"])
async def t_app_cached(s):
    t0 = time.time()
    events = await sse_collect(s, f"{BASE}/api/app",
                               {"prompt": "test counter app", "app_id": "test-counter"})
    dt = time.time() - t0
    cached = [d for e, d in events if e == "cached"]
    assert cached, f"reopen did not serve the existing version: {[e for e, _ in events]}"
    assert cached[0].get("version") == 1, "version must not bump on a plain reopen"
    assert dt < 3, f"reopen took {dt:.1f}s — must be instant (no LLM)"
    return f"reopen served v{cached[0]['version']} from disk in {dt:.2f}s, no evolution"


@run_test("http_app_host_page_same_sandbox_same_bridge", ["R66", "R5"])
async def t_app_host_page(s):
    """GET /app/{id}: a cached app as a top-level page keeps the opaque-origin sandbox
    and carries the byte-identical bridge the desktop page injects (bench route)."""
    import html as htmlmod
    async with s.get(f"{BASE}/app/test-counter") as r:
        assert r.status == 200, f"HTTP {r.status}"
        page = await r.text()
    m = re.search(r'<iframe sandbox="([^"]*)" srcdoc="(.*?)"></iframe>', page, re.S)
    assert m, "no sandboxed srcdoc iframe in the host page"
    assert m.group(1) == "allow-scripts allow-pointer-lock", f"sandbox changed: {m.group(1)}"
    assert "allow-same-origin" not in m.group(1)
    js = (REPO / "shell" / "shell.js").read_text()
    bridge = re.search(r"const BRIDGE_SRC = `(.*?)`;\n", js, re.S).group(1).replace("<\\/script>", "</script>")
    app = (VDIR / "cache" / "apps" / "test-counter" / "app.html").read_text()
    head = re.search(r"<head[^>]*>", app, re.I)
    expect = app[:head.end()] + bridge + app[head.end():] if head else bridge + app
    assert htmlmod.unescape(m.group(2)) == expect, "srcdoc is not injectBridge(app.html)"
    async with s.get(f"{BASE}/app/no-such-app") as r:
        assert r.status == 404, f"unknown app: HTTP {r.status}"
    async with s.get(f"{BASE}/app/..%2Fx") as r:
        assert r.status == 404, f"path escape: HTTP {r.status}"
    return f"host page {len(page)} bytes, sandbox intact, bridge identical ({bridge.count('call(')} call sites), 404 for unknown"


@run_test("http_app_evolves_on_improve_only", ["R24", "R25", "R35"])
async def t_evolve(s):
    events = await sse_collect(s, f"{BASE}/api/app",
                               {"prompt": "test counter app", "app_id": "test-counter",
                                "evolve": True, "instruction": "add a reset button"})
    done = [d for e, d in events if e == "done"]
    assert done and done[0].get("version", 0) >= 2, "improve did not evolve the app"
    hist = VDIR / "cache" / "apps" / "test-counter" / "history" / "v1.html"
    assert hist.exists(), "previous version not kept as history"
    meta = json.loads((VDIR / "cache" / "apps" / "test-counter" / "meta.json").read_text())
    assert meta.get("prompt") == "test counter app", "prompt not saved for relaunch"
    return f"✨ improve evolved to v{done[0]['version']}; v1 in history; prompt persisted"


@run_test("http_sse_client_disconnect_safe", ["R62"])
async def t_disconnect(s):
    # closing a window mid-generation must NOT crash the kernel with a traceback
    try:
        async with s.post(f"{BASE}/api/app",
                          json={"prompt": "disconnect test app", "app_id": "disco-test"},
                          timeout=aiohttp.ClientTimeout(total=0.4)) as r:
            async for _ in r.content.iter_any():
                break  # read one chunk, then abandon the stream (client "closes window")
    except (asyncio.TimeoutError, aiohttp.ClientError):
        pass
    # the kernel must still be fully alive and serving right after
    for _ in range(10):
        await asyncio.sleep(0.5)
        async with s.get(f"{BASE}/api/status") as r:
            if r.status == 200:
                break
    else:
        raise AssertionError("kernel unhealthy after a client disconnect")
    # and the abandoned generation still completed + saved the app (work not wasted)
    saved = False
    for _ in range(20):
        async with s.get(f"{BASE}/api/apps") as r:
            if any(a["app_id"] == "disco-test" for a in (await r.json())["apps"]):
                saved = True
                break
        await asyncio.sleep(1)
    assert saved, "abandoned generation did not finish/save the app"
    return "client disconnect mid-SSE: no kernel crash, app still generated + saved"


@run_test("http_app_icon_and_menu", ["R60"])
async def t_app_icon(s):
    # generating a NEW app kicks off a background icon render (image model)
    await sse_collect(s, f"{BASE}/api/app",
                      {"prompt": "a tiny icon test app", "app_id": "icon-test"})
    icon_ok = False
    for _ in range(30):
        async with s.get(f"{BASE}/api/apps") as r:
            apps = (await r.json())["apps"]
        me = next((a for a in apps if a["app_id"] == "icon-test"), None)
        assert me is not None, "created app missing from /api/apps (the menu source)"
        if me.get("icon"):
            async with s.get(f"{BASE}{me['icon']}") as g:
                icon_ok = g.status == 200 and (await g.read())[:4] == b"\x89PNG"
            break
        await asyncio.sleep(1)
    assert icon_ok, "app icon was not generated/served for the menu"
    return "app listed in /api/apps with a real generated PNG icon"


@run_test("http_app_forge_generates_resources", ["R36", "R69"])
async def t_forge(s):
    import re
    events = await sse_collect(s, f"{BASE}/api/app",
                               {"prompt": "test forge app", "app_id": "test-forge",
                                "assets": True})
    done = [d for e, d in events if e == "done"]
    assert done, f"forge produced no app: {[e for e, _ in events]}"
    h = done[0]["html"]
    img = re.findall(r'src="(/files/images/app-test-forge-[^"]+)"', h)
    aud = re.findall(r'src="(/files/music/app-test-forge-[^"]+)"', h)
    vid = re.findall(r'src="(/files/videos/app-test-forge-[^"]+)"', h)
    assert img, "data-gen image was not generated for the forged app"
    assert aud, "data-gen-music track was not composed for the forged app"
    assert vid, "data-gen-video clip was not rendered for the forged app"
    for u in img + aud + vid:
        async with s.get(f"{BASE}{u}") as r:
            assert r.status == 200, f"forged resource not served: {u}"
    meta = json.loads((VDIR / "cache" / "apps" / "test-forge" / "meta.json").read_text())
    assert meta.get("assets") is True, "assets flag not remembered for future improves"
    # the forge composes through music_job with the same sung-language default [R69]
    arglog = Path(os.environ.get("MOCK_ACE_ARGLOG", VDIR / "ace-args.log"))
    rq = next(json.loads(l.split("\t", 1)[1]) for l in reversed(arglog.read_text().splitlines())
              if l.startswith("ace-lm-request\t") and "dreamy synthwave" in l)
    assert rq["lyrics"].startswith("[Verse]") and rq["vocal_language"] == "en", rq
    return f"dev cycle forged app + {len(img)} image, {len(aud)} track (sung, en), {len(vid)} clip"


@run_test("http_image_with_random_seed", ["R4", "R22"])
async def t_image(s):
    arglog = Path(os.environ.get("MOCK_SD_ARGLOG", VDIR / "sd-args.log"))
    arglog.unlink(missing_ok=True)
    jobs = []
    for _ in range(2):
        async with s.post(f"{BASE}/api/image", json={"prompt": "test orb"}) as r:
            jobs.append((await r.json())["job"])
    urls = [(await poll_job(s, j))["result"] for j in jobs]
    for u in urls:
        async with s.get(f"{BASE}{u}") as r:
            assert r.status == 200, f"image not served: {u}"
    seeds = []
    for line in arglog.read_text().splitlines():
        args = line.split("\t")
        if "-s" in args:
            seeds.append(args[args.index("-s") + 1])
    assert len(seeds) >= 2 and seeds[-1] != seeds[-2], f"seeds not random: {seeds}"
    last = arglog.read_text().splitlines()[-1]
    assert "museByStableYogi_v35Q4Extended" in last and "--llm" in last, \
        f"image job not routed to Muse (Qwen3-VL encoder): {last[:120]}"
    # an engine crash surfaces as a job error and the engine state returns to idle
    # (it used to stay "image" forever: menu bar + loading pages stuck on busy)
    async with s.post(f"{BASE}/api/image", json={"prompt": "__mockfail__ orb"}) as r:
        bad = (await r.json())["job"]
    try:
        await poll_job(s, bad); raise AssertionError("failing engine reported done")
    except AssertionError as e:
        assert "job error" in str(e) and "sd-cli failed (3)" in str(e), e
    async with s.get(f"{BASE}/api/status") as r:
        st = await r.json()
    assert st["state"] == "idle", f"engine state stuck after an error: {st['state']}"
    return f"2 Krea-2 images generated, seeds differ ({seeds[-2]} vs {seeds[-1]}); engine crash -> error + idle"


@run_test("http_video", ["R6"])
async def t_video(s):
    if not shutil.which("ffmpeg"):
        raise SkipTest("ffmpeg not installed yet (arrives with engine install)")
    async with s.post(f"{BASE}/api/video", json={"prompt": "test clip"}) as r:
        jid = (await r.json())["job"]
    res = await poll_job(s, jid)
    async with s.get(f"{BASE}{res['result']}") as r:
        assert r.status == 200
    return f"mp4 produced and served: {res['result']}"


@run_test("unit_finish_video_drops_intermediate", ["R6"])
async def t_finish_video(_):
    """finish_video() normalizes what sd-cli emitted (a `.mp4.avi` on the 2026-09-13 780M
    run, `.avi`, or frame PNGs) to one h264 mp4 — and must not leave the intermediate
    behind: the mjpeg avi is ~5x the mp4 and the videos cache is evicted by size."""
    if not shutil.which("ffmpeg"):
        raise SkipTest("ffmpeg not installed yet (arrives with engine install)")
    import tempfile
    sys.path.insert(0, str(REPO / "kernel"))
    import server

    async def mk(*args):
        p = await asyncio.create_subprocess_exec("ffmpeg", "-y", "-v", "error", *args,
                                                 stdout=asyncio.subprocess.DEVNULL)
        assert await p.wait() == 0, f"ffmpeg fixture failed: {args}"

    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        # 1. what the real engine wrote: the requested name + ".avi" appended (mjpeg)
        base = t / "job1"
        await mk("-f", "lavfi", "-i", "color=c=red:s=64x64:r=16", "-frames:v", "4",
                 "-c:v", "mjpeg", str(t / "job1.mp4.avi"))
        mp4 = await server.finish_video(base)
        assert mp4 == t / "job1.mp4" and mp4.exists() and mp4.stat().st_size > 0
        assert not (t / "job1.mp4.avi").exists(), "mjpeg intermediate left next to the clip"
        # 2. a frame sequence (older sd.cpp / the suite's mock): assembled, frames removed
        base = t / "job2"
        await mk("-f", "lavfi", "-i", "color=c=blue:s=64x64:r=16", "-frames:v", "4",
                 str(t / "job2_%d.png"))
        assert (t / "job2_1.png").exists()
        mp4 = await server.finish_video(base)
        assert mp4 == t / "job2.mp4" and mp4.exists()
        assert not list(t.glob("job2_*.png")), "frame PNGs left next to the clip"
        # 3. nothing produced -> the clean error h_video reports, not a traceback
        try:
            await server.finish_video(t / "job3"); assert False, "must fail cleanly"
        except RuntimeError as e:
            assert "no output" in str(e), e
        assert sorted(p.name for p in t.iterdir()) == ["job1.mp4", "job2.mp4"], list(t.iterdir())
    return "avi + frame intermediates transcoded to mp4 and removed; empty job -> clean error"


@run_test("unit_video_argv_converges", ["R6"])
async def t_video_argv(_):
    """The shipped Wan 2.2 argv must be the one that converges on every seed, on both backends
    (docs/ENGINES-CUDA-2026-09.md 9.2): with `--video-frames 17` (5 latent frames) the DiT
    collapsed into a colour mosaic on 5 of 8 fixed seeds whatever the cfg / flow-shift /
    steps / negative prompt; with 33 (9 latent frames) 8/8 converge, on the RTX 4070 and the
    Radeon 780M alike. And the VAE decodes on the GPU in 8x8-latent tiles = `--vae-tile-size
    128x128` — sd.cpp master-929 reads the size in image PIXELS (minimum 64): the 2026-09-14
    engine's latent spelling `8x8` failed every /api/video with "VAE tile size must be at
    least 64 image pixels" (FEATURE-TEST D10; 128 px verified: 33 frames in 102.7 s, 5.5 GB).
    The old engine's default tile needed 13 GB (OOM on 8 GB), `--vae-on-cpu` cost 368 of a
    402 s job on the 4070 and 347 of 514 s on the UMA 780M — the same flags are the fast ones
    on CUDA and Vulkan, so sd_backend_args() must pass the video argv through unchanged."""
    import tempfile
    sys.path.insert(0, str(REPO / "kernel"))
    import server
    tiers = {}
    for t in ("tier-8gb", "tier-16gb", "tier-24gb", "tier-32gb"):
        ga = json.loads((REPO / "manifests" / f"{t}.json").read_text())["video"]["gen_args"]
        val = lambda flag: ga[ga.index(flag) + 1]
        frames = int(val("--video-frames"))
        assert frames >= 33 and (frames - 1) % 4 == 0, f"{t}: {frames} frames (need 4n+1 >= 33: 17 collapses on most seeds)"
        w, h = int(val("-W")), int(val("-H"))
        assert w % 16 == 0 and h % 16 == 0 and min(w, h) >= 480, f"{t}: {w}x{h} off the Wan 2.2 latent grid"
        assert "--vae-on-cpu" not in ga, f"{t}: --vae-on-cpu is back (368 s CPU decode per clip)"
        assert "--vae-tiling" in ga and val("--vae-tile-size") == "128x128", \
            f"{t}: GPU VAE needs 128x128-PIXEL tiles (8x8 latent) — `8x8` is refused by sd.cpp master-929 (D10)"
        # the same argv in the three 8 GB test variants (they must stay identical outside `llm`)
        for v in (REPO / "manifests" / "test").glob("tier-8gb-*.json"):
            vg = json.loads(v.read_text())["video"]["gen_args"]
            assert vg[vg.index("--vae-tile-size") + 1] == "128x128", v.name
        assert val("--sampling-method") == "euler" and float(val("--cfg-scale")) == 6.0, t
        assert "--offload-to-cpu" not in ga and "--backend" not in ga
        tiers[t] = ga
    # both backends exec the same video argv: the placement decision is the manifest's
    saved = (server.BIN, server.NVIDIA_PROC, os.environ.pop("VIBEOS_ENGINE", None))
    try:
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            (td / "vulkan").mkdir(); (td / "vulkan" / "llama-server").touch()   # what vulkan_set_present() probes
            server.BIN, server.NVIDIA_PROC = td, td / "no-nvidia-driver"
            assert server.engine_backend() == "vulkan", server.engine_info()
            for t, ga in tiers.items():
                assert server.sd_backend_args(ga) == ga, f"{t}: video argv changed on vulkan"
            server.NVIDIA_PROC.mkdir()
            assert server.engine_backend() == "cuda", server.engine_info()
            for t, ga in tiers.items():
                assert server.sd_backend_args(ga) == ga, f"{t}: video argv changed on cuda"
    finally:
        server.BIN, server.NVIDIA_PROC = saved[0], saved[1]
        if saved[2] is not None:
            os.environ["VIBEOS_ENGINE"] = saved[2]
    return "4 tiers + 3 variants: 33 frames, GPU VAE 128x128 px (8x8 latent) tiles, no --vae-on-cpu; identical argv on cuda and vulkan"


@run_test("http_game_forge", ["R7", "R11"])
async def t_game(s):
    try:
        import pygame  # noqa: F401
    except ImportError:
        raise SkipTest("pygame not installed yet (arrives with engine install)")
    async with s.post(f"{BASE}/api/game", json={"prompt": "test bouncing ball"}) as r:
        jid = (await r.json())["job"]
    res = await poll_job(s, jid)
    assert res["result"]["attempt"] >= 1
    return f"game launched (running={res['result']['running']}, attempt {res['result']['attempt']}); crash-retry loop exercised structurally"


@run_test("http_fs_api", ["R16", "R17"])
async def t_fs(s):
    async def fs(op, **kw):
        async with s.post(f"{BASE}/api/fs", json={"op": op, **kw}) as r:
            return r.status, await r.json()
    st, root = await fs("list", path="/")
    assert {e["name"] for e in root["entries"]} == {"system", "data"}
    await fs("write", path="/data/test/file.txt", content="hi")
    st, rd = await fs("read", path="/data/test/file.txt")
    assert rd["content"] == "hi"
    st, err = await fs("write", path="/system/evil.txt", content="x")
    assert st == 403, "system partition writable!"
    st, _ = await fs("delete", path="/data/test")
    st, _ = await fs("write", path="/data/junk.txt", content="x")
    st, _ = await fs("format", path="/data")
    st, ls = await fs("list", path="/data")
    assert not any(e["name"] == "junk.txt" for e in ls["entries"])
    return "partitions listed; CRUD on /data; /system 403; format wipes"


@run_test("http_settings_api", ["R15"])
async def t_settings(s):
    async with s.post(f"{BASE}/api/settings", json={"key": "accent", "value": "#ff0000"}) as r:
        assert (await r.json())["accent"] == "#ff0000"
    async with s.get(f"{BASE}/api/settings") as r:
        assert (await r.json())["accent"] == "#ff0000"
    async with s.post(f"{BASE}/api/fs", json={"op": "read", "path": "/data/settings.json"}) as r:
        assert "#ff0000" in (await r.json())["content"]
    return "set/get works; stored on /data (factory reset resets it)"


@run_test("http_api_docs", ["R14"])
async def t_docs(s):
    async with s.get(f"{BASE}/api/docs") as r:
        text = await r.text()
    assert "kAIrOS API" in text and "vibe.fs" in text
    return f"{len(text)} bytes of self-documentation served"


@run_test("http_search_offline_mode", ["R9", "R10", "R20"])
async def t_search(s):
    assert os.environ.get("VIBEOS_FORCE_OFFLINE"), "suite must run in forced-offline mode"
    async with s.post(f"{BASE}/api/search", json={"query": "Albert Einstein"}) as r:
        res = await r.json()
    assert res["online"] is False, "kernel believes it is online despite force-offline"
    if not res["results"]:
        raise SkipTest("offline shape OK but no ZIM linked in test env — no kb results")
    return f"offline search returned {len(res['results'])} encyclopedia results"


@run_test("http_browser_imaginary_web", ["R21", "R57"])
async def t_browse(s):
    async with s.post(f"{BASE}/api/browse", json={"address": "test.page/hello"}) as r:
        p1 = await r.json()
    assert "<!DOCTYPE" in p1["html"]
    assert 'href="/web/' in p1["html"], "links not rewritten to the virtual web"
    assert "kairosNavigate" not in p1["html"], "legacy nav script still injected"
    async with s.post(f"{BASE}/api/browse", json={"address": "test.page/hello"}) as r:
        p2 = await r.json()
    assert p2["cached"] is True, "revisit not cached (offline replay broken)"
    return "page AI-generated with real /web/ prompt-links; revisit served from cache"


@run_test("http_virtual_web_loading_flow", ["R57"])
async def t_web(s):
    # 1) cache miss -> an instant LOADING page that meta-refreshes to itself
    async with s.get(f"{BASE}/web/loadtest.site") as r:
        assert r.status == 200
        first = await r.text()
    assert 'http-equiv="refresh"' in first and "_t=1" in first, "no loading redirect chain"
    # 2) the chain converges: within a few refreshes the real site is served
    html = None
    for _ in range(40):
        await asyncio.sleep(1)
        async with s.get(f"{BASE}/web/loadtest.site") as r:
            body = await r.text()
        if 'http-equiv="refresh"' not in body:
            html = body
            break
    assert html and "Mock Imaginary Site" in html, "page never finished building"
    # 3) stability layer: links/forms/images all rewritten to working URLs
    assert 'href="/web/mock.site/about"' in html
    assert 'href="/web/loadtest.site/menu"' in html, "relative link not routed to same site"
    assert 'href="/web/real.example.com/page"' in html, "external link not converted"
    assert "target=" not in html.split("<nav")[1].split("</nav>")[0]
    assert 'action="/web/loadtest.site/search"' in html, "form not routed through /web/"
    import re
    assert "data-gen=" not in html.split("<img")[1] or "/genimg/" in html
    imgs = re.findall(r'<img[^>]*src="([^"]+)"', html)
    assert imgs and all(u.startswith(("/genimg/", "/files/", "data:image/svg")) for u in imgs), \
        f"non-working img srcs survived: {imgs}"
    # 4) sub-page of a cached site gets built too (SITE CONTEXT path)
    async with s.get(f"{BASE}/web/loadtest.site/menu") as r:
        assert r.status == 200  # loading page for the sub-page — generation kicked off
    return "loading→refresh→site flow works; links/forms/imgs rewritten; sub-page spawns"


@run_test("http_genimg_picture_tool", ["R58"])
async def t_genimg(s):
    import re as _re
    async with s.get(f"{BASE}/genimg/a tiny test orb") as r:
        assert r.status == 200
        body = await r.read()
        ctype = r.headers.get("Content-Type", "")
    assert body[:4] == b"\x89PNG" or "svg" in ctype, f"unexpected genimg answer: {ctype}"
    assert body[:4] == b"\x89PNG", "picture was not rendered (placeholder only)"
    t0 = time.time()
    async with s.get(f"{BASE}/genimg/a tiny test orb") as r:
        body2 = await r.read()
    assert body2 == body and time.time() - t0 < 1.5, "genimg cache miss on identical prompt"
    return f"/genimg/ rendered a real PNG ({len(body)} B) and cached it"


@run_test("http_browser_images", ["R21", "R29", "R37", "R58"])
async def t_browser_images(s):
    import re
    async with s.post(f"{BASE}/api/browse", json={"address": "img.page/gallery", "fresh": True}) as r:
        h = (await r.json())["html"]
    gen = re.findall(r'<img[^>]*src="(/genimg/[^"]+)"', h)
    assert len(gen) >= 3, ("data-gen, phantom-path AND base64 imgs must all become "
                           f"on-demand /genimg/ pictures, got {len(gen)}: {gen}")
    assert "FAKEHALLUCINATEDGARBAGE" not in h, "hallucinated base64 not neutralized"
    assert 'src="images/' not in h, "phantom relative img src survived (renders broken)"
    # fetching one of them renders a REAL picture on demand
    async with s.get(f"{BASE}{gen[0]}") as g:
        assert g.status == 200 and (await g.read())[:4] == b"\x89PNG", \
            f"on-demand image not rendered: {gen[0]}"
    # a page cached with broken media must be rescued on the next (cached) visit
    async with s.post(f"{BASE}/api/browse", json={"address": "img.page/gallery"}) as r:
        c = await r.json()
    assert c["cached"] and 'src="images/' not in c["html"]
    return f"{len(gen)} imgs → on-demand /genimg/; first one rendered as real PNG"


@run_test("http_desktop_search_always_finds", ["R23"])
async def t_dsearch(s):
    async with s.get(f"{BASE}/api/desktop-search?q=") as r:
        empty = await r.json()
    assert empty["results"], "empty query found nothing"
    async with s.get(f"{BASE}/api/desktop-search?q=zzqxunfindable") as r:
        weird = await r.json()
    assert weird["results"], "nonsense query found nothing"
    assert any(x["group"] == "create" for x in weird["results"]), "no generative actions"
    sparks = {empty["spark"]}
    for _ in range(5):
        async with s.get(f"{BASE}/api/desktop-search?q=") as r:
            sparks.add((await r.json())["spark"])
    assert len(sparks) > 1, "spark not randomized"
    return f"never empty; create-actions present; {len(sparks)} distinct sparks in 6 calls"


@run_test("http_desktop_search_web_address", ["R83", "R81", "R23"])
async def t_dsearch_address(s):
    """[R83] an address typed into the desktop search is the FIRST result and opens in the built-in browser
    ({type: web}); words after a host are the site's search; [R81] the system apps that left the dock are found by
    name ({type: sysapp}); the retired generated browser app is listed nowhere (its cache stays on disk)"""
    d = VDIR / "cache" / "apps" / "browser"
    d.mkdir(parents=True, exist_ok=True)
    (d / "meta.json").write_text(json.dumps({"app_id": "browser", "prompt": "the kAIrOS web browser", "version": 1}))
    (d / "app.html").write_text("<!DOCTYPE html><html><body>old generated browser</body></html>")
    try:
        async def q(text):
            async with s.get(f"{BASE}/api/desktop-search", params={"q": text}) as r:
                return (await r.json())["results"]
        r = await q("oppenheimer.com")
        assert r[0]["group"] == "web" and r[0]["action"] == {"type": "web", "address": "oppenheimer.com"}, r[0]
        r = await q("www.x.de/y red bikes")
        assert r[0]["action"]["address"] == "www.x.de/y?q=red bikes", r[0]
        assert all(x["group"] != "web" for x in await q("node.js tutorial"))
        r = await q("news")
        assert any(x["action"] == {"type": "sysapp", "app_id": "news"} for x in r), r
        r = await q("browser")
        assert any(x["action"] == {"type": "sysapp", "app_id": "browser"} for x in r)
        assert not any(x["action"].get("type") == "app" and x["action"].get("app_id") == "browser" for x in r), r
        async with s.get(f"{BASE}/api/apps") as rr:
            assert "browser" not in [a["app_id"] for a in (await rr.json())["apps"]]
    finally:
        shutil.rmtree(d, ignore_errors=True)
    return "address first ({type: web}); site search ?q=; node.js stays a search; sysapps by name; the generated browser hidden"


@run_test("http_web_improve_versions_revert", ["R84", "R57", "R86"])
async def t_web_improve(s):
    """[R84] ✨ improve a website through the real kernel + the mock llama-server: POST /api/web/improve streams v2
    (the mock's page writer names the instruction), /web/ serves v2 and reports {version, versions}, ?_v=1 is the page
    as it was, POST /api/web/version makes v1 the page again (nothing deleted); a declined improvement ([refuse-always],
    every rung) and a length-cut one save nothing"""
    import re as _re
    addr = "improve.page/home"
    async with s.post(f"{BASE}/api/browse", json={"address": addr, "fresh": True}) as r:
        assert r.status == 200 and "Mock Imaginary Site" in (await r.json())["html"]
    ev = await sse_collect(s, f"{BASE}/api/web/improve", {"address": addr, "instruction": "make it purple"})
    names = [e for e, _ in ev]
    assert names[0] == "delta" and names[-1] == "done", names
    done = ev[-1][1]
    assert done["version"] == 2 and [v["v"] for v in done["versions"]] == [1, 2] and done["title"] == "Mock Imaginary Site v2", done

    async def page(extra=""):
        async with s.get(f"{BASE}/web/{addr}{extra}") as r:
            html = await r.text()
        return html, json.loads(_re.search(r"<script data-kairos-page>\(function\(\)\{var d=(.*?);function post", html).group(1))
    html, rep = await page()
    assert "improved v2: make it purple" in html and rep["version"] == 2 and rep["versions"] == 2, rep
    html, rep = await page("?_v=1")
    assert "improved" not in html and rep["version"] == 1 and rep["current"] == 2, rep
    async with s.post(f"{BASE}/api/web/version", json={"address": addr, "version": 1}) as r:
        rv = await r.json()
    assert r.status == 200 and rv["current"] == 1 and len(rv["versions"]) == 2, rv
    html, rep = await page()
    assert "improved" not in html and rep["version"] == 1, rep
    ev = await sse_collect(s, f"{BASE}/api/web/improve", {"address": addr, "instruction": "[refuse-always] again"})
    assert ev[-1][0] == "error" and ev[-1][1]["kind"] == "declined" and "every available model" in ev[-1][1]["message"], ev[-1]
    ev = await sse_collect(s, f"{BASE}/api/web/improve", {"address": addr, "instruction": "longer [length-stop]"})
    assert ev[-1][0] == "done" and ev[-1][1]["truncated"] is True and ev[-1][1]["cut"], ev[-1]
    async with s.get(f"{BASE}/api/web/versions", params={"address": addr}) as r:
        vs = await r.json()
    assert vs["current"] == 1 and [v["v"] for v in vs["versions"]] == [1, 2], vs
    return "v2 streamed (instruction in the page); /web/ reports version 2 of 2; ?_v=1; revert to v1; declined + cut save nothing"


@run_test("http_first_boot_setup", ["R18"])
async def t_setup(s):
    async with s.get(f"{BASE}/api/setup") as r:
        st = await r.json()
    assert isinstance(st["vram_mb"], int) and len(st["tiers"]) == 4
    assert st["recommended_tier"] in {"8gb", "16gb", "24gb", "32gb"}
    assert st["current_tier"] == "8gb" and "first_boot" in st
    return f"vram={st['vram_mb']}MB recommended={st['recommended_tier']} tiers listed"




@run_test("http_music", ["R31"])
async def t_music(s):
    arglog = Path(os.environ.get("MOCK_ACE_ARGLOG", VDIR / "ace-args.log"))
    async with s.post(f"{BASE}/api/music", json={"prompt": "test jingle", "duration": 10}) as r:
        jid = (await r.json())["job"]
    res = await poll_job(s, jid)
    async with s.get(f"{BASE}{res['result']}") as r:
        assert r.status == 200
        body = await r.read()
    assert body[:4] == b"RIFF", "result is not a WAV"
    # the engine is driven the way acestep.cpp is really driven (tools/ace-lm.cpp,
    # tools/ace-synth.cpp): two stages, --models <dir> --request <json>, no --prompt/-o,
    # the LM's request0.json feeding synth, the AceRequest carrying model + format
    lines = arglog.read_text().splitlines()
    lm = next(l for l in reversed(lines) if l.startswith("ace-lm\t"))
    sy = next(l for l in reversed(lines) if l.startswith("ace-synth\t"))
    rq = next(l for l in reversed(lines) if l.startswith("ace-lm-request\t"))
    assert "\t--models\t" in lm and lm.endswith("/request.json") and "--prompt" not in lm, lm
    assert "\t--models\t" in sy and sy.endswith("/request0.json") and "-o" not in sy.split("\t"), sy
    assert lm.split("\t")[lm.split("\t").index("--models") + 1].endswith("/music")
    req = json.loads(rq.split("\t", 1)[1])
    assert req["synth_model"] == "acestep-v15-turbo-Q8_0.gguf", req
    assert req["lm_model"].startswith("acestep-5Hz-lm-") and req["output_format"].startswith("wav")
    assert req["caption"].startswith("test jingle") and req["duration"] == 10
    assert req["lyrics"] == "[Instrumental]" and req["vocal_language"] == "unknown", req
    assert not list((VDIR / "cache" / "files" / "music" / ".jobs").glob("*")), "job dir not cleaned"
    return f"ace-lm -> request0.json -> ace-synth (turbo DiT, wav) served: {res['result']}"


@run_test("unit_music_language_default_en", ["R69", "R31"])
async def t_music_language_unit(_):
    """The owner's rule for the sung language [R69]: English unless explicitly specified,
    guessing only on request. music_language() maps the API field to acestep's
    vocal_language; music_job() applies it (the forge calls music_job directly, so the
    default must live there, not only in the HTTP handler)."""
    import tempfile
    sys.path.insert(0, str(REPO / "kernel"))
    import server
    ml = server.music_language
    for absent in (None, "", "   ", "\t"):
        assert ml(absent) == "en", absent
    assert ml("de") == "de" and ml("DE") == "de" and ml(" Ja ") == "ja" and ml("yue") == "yue"
    assert ml("auto") == "" and ml("AUTO") == "", "auto = the engine's own 'LM detects via CoT' state"
    for bad in ("xx", "english", "en-US", "unknown", "zh-CN", 5, True):
        try:
            ml(bad); assert False, f"{bad!r} accepted"
        except ValueError as e:
            assert "auto" in str(e) and "en" in str(e) and "639" in str(e), e
    # the table is acestep's (src/metadata-fsm.h: 50 codes + "unknown"), "unknown" is not a
    # caller value (it is what instrumentals get) — checked against the source when present
    assert len(server.MUSIC_LANGUAGES) == 50 and {"en", "de", "ja", "zh", "yue"} <= server.MUSIC_LANGUAGES
    assert "unknown" not in server.MUSIC_LANGUAGES and "auto" not in server.MUSIC_LANGUAGES
    fsm = next((q for q in (Path("/images/vibeos-vulkan/engines/acestep.cpp/src/metadata-fsm.h"),
                            Path(os.environ.get("KAIROS_HOME", os.path.expanduser("~/kairos")), "vibeos-vulkan/engines/acestep.cpp/src/metadata-fsm.h"))
                if q.exists()), None)
    if fsm:
        src = fsm.read_text()
        m = re.search(r'Language: 51 codes.*?\{(.*?)\}', src, re.S)
        codes = set(re.findall(r'"([a-z]+)"', m.group(1)))
        assert codes == server.MUSIC_LANGUAGES | {"unknown"}, codes ^ server.MUSIC_LANGUAGES
        assert 'base.vocal_language == "unknown"' in src, "the FSM merge rule moved"
    # music_job writes the request JSON with the same rule (stub stages: the JSON stays
    # in the job dir because no audio is produced)
    saved = (server.FILES_DIR, server.CACHE_DIR, server.EVICTABLE_DIRS, server.DISK_FREE_MIN)
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        (t / "music").mkdir()
        server.FILES_DIR = server.CACHE_DIR = t
        server.EVICTABLE_DIRS, server.DISK_FREE_MIN = (t / "music",), 0
        mm = server.ModelManager({"strategy": "keep",
                                  "music": {"cmd": [["true", "{request}"]], "request": {"lm_model": "x"}}})

        async def req_for(**kw):
            out = t / "music" / f"{len(list((t / 'music').iterdir()))}.wav"
            try:
                await mm.music_job("p", out, **kw); assert False, "stub stages produce no audio"
            except RuntimeError as e:
                assert "no audio" in str(e), e
            return json.loads((t / "music" / ".jobs" / out.stem / "request.json").read_text())
        try:
            assert (await req_for(lyrics="la la"))["vocal_language"] == "en", "default"
            assert (await req_for(lyrics="la la", language=None))["vocal_language"] == "en"
            assert (await req_for(lyrics="la la", language=""))["vocal_language"] == "en"
            assert (await req_for(lyrics="la la", language="De"))["vocal_language"] == "de"
            assert (await req_for(lyrics="la la", language="auto"))["vocal_language"] == ""
            # no lyrics: "[Instrumental]" + "unknown" whatever language says — acestep's
            # request parser forces exactly that pairing (src/request.cpp:221-224)
            r = await req_for(lyrics="", language="de")
            assert r["lyrics"] == "[Instrumental]" and r["vocal_language"] == "unknown", r
            r = await req_for(lyrics="  ")
            assert r["lyrics"] == "[Instrumental]" and r["vocal_language"] == "unknown", r
            try:
                await mm.music_job("p", t / "music" / "bad.wav", lyrics="x", language="xx")
                assert False, "garbage accepted"
            except ValueError:
                assert not (t / "music" / ".jobs" / "bad").exists(), "validated before the job dir"
        finally:
            server.FILES_DIR, server.CACHE_DIR, server.EVICTABLE_DIRS, server.DISK_FREE_MIN = saved
    return "absent/empty/null -> en; code lower-cased; auto -> '' (LM guesses); instrumental -> unknown; garbage -> ValueError"


@run_test("http_music_language", ["R69", "R31"])
async def t_music_language_http(s):
    """POST /api/music `language` end to end through the mock acestep, which merges
    vocal_language the way the real ace-lm does (kept when concrete, guessed when
    "" / "unknown", forced "unknown" for [Instrumental]) and logs received -> written."""
    arglog = Path(os.environ.get("MOCK_ACE_ARGLOG", VDIR / "ace-args.log"))

    async def sung(body):
        async with s.post(f"{BASE}/api/music", json={"prompt": "test song", "duration": 10, **body}) as r:
            assert r.status == 200, await r.text()
            jid = (await r.json())["job"]
        await poll_job(s, jid)
        lines = arglog.read_text().splitlines()
        rq = json.loads(next(l for l in reversed(lines) if l.startswith("ace-lm-request\t")).split("\t", 1)[1])
        got, wrote = next(l for l in reversed(lines) if l.startswith("ace-lm-language\t")).split("\t")[1:]
        assert json.loads(got) == rq["vocal_language"], "mock read what the kernel sent"
        return rq["vocal_language"], json.loads(wrote)
    lyr = {"lyrics": "[Verse]\nMorning light is on the road"}
    assert await sung(lyr) == ("en", "en"), "no language -> English, and the LM keeps it"
    assert await sung({**lyr, "language": ""}) == ("en", "en")
    assert await sung({**lyr, "language": None}) == ("en", "en")
    assert await sung({**lyr, "language": "DE"}) == ("de", "de"), "explicit code, lower-cased, kept"
    assert await sung({**lyr, "language": "auto"}) == ("", "zh"), "auto: the engine guesses (old behaviour)"
    assert await sung({"language": "de"}) == ("unknown", "unknown"), "instrumental stays unknown"
    # garbage: 400 naming the accepted codes, no job created, no engine run
    async with s.get(f"{BASE}/api/status") as r:
        jobs_before = len((await r.json())["jobs"])
    lm_before = arglog.read_text().count("ace-lm\t")
    for bad in ("xx", "english", "unknown"):
        async with s.post(f"{BASE}/api/music", json={"prompt": "x", "lyrics": "y", "language": bad}) as r:
            assert r.status == 400, bad
            j = await r.json()
        assert "job" not in j and "auto" in j["error"] and "en" in j["accepted"] and "auto" in j["accepted"], j
        assert "unknown" not in j["accepted"]
    async with s.get(f"{BASE}/api/status") as r:
        assert len((await r.json())["jobs"]) == jobs_before, "a 400 must not create a job"
    assert arglog.read_text().count("ace-lm\t") == lm_before, "a 400 must not run the engine"
    return "en by default, de kept, auto -> engine guess, instrumental unknown, garbage 400 + no job"


@run_test("http_music_engine_missing", ["R31", "R47"])
async def t_music_engine_missing(s):
    """No acestep.cpp binaries (the normal state of a dev box and of every image built
    before the engine was added) must be a clean error, never a FileNotFoundError — since
    2026-10-14 the 503 every generation route answers for a missing engine (was a 400)."""
    link = VDIR / "bin" / "ace-lm"
    target = os.readlink(link)
    link.unlink()
    try:
        async with s.post(f"{BASE}/api/music", json={"prompt": "no engine"}) as r:
            assert r.status == 503, f"expected 503, got {r.status}"
            j = await r.json()
            err = j["error"]
        assert "not installed" in err and "ace-lm" in err and "Traceback" not in err, err
        assert j.get("engine") == "ace-lm" and j.get("missing") is True, j
        async with s.get(f"{BASE}/api/models") as r:
            mu = next(e for e in (await r.json())["extras"] if e["key"] == "music")
        assert mu["addon"] is True and mu["engine"] is False and mu["available"] is False, mu
    finally:
        os.symlink(target, link)
    async with s.get(f"{BASE}/api/models") as r:
        mu = next(e for e in (await r.json())["extras"] if e["key"] == "music")
    assert mu["engine"] is True and mu["available"] is True, mu
    # the 5.6 GB music set is an add-on: never counted towards the baked tier [R47]
    sys.path.insert(0, str(REPO / "kernel"))
    import server
    for t in ("8gb", "16gb", "24gb", "32gb"):
        m = json.loads((REPO / "manifests" / f"tier-{t}.json").read_text())
        assert m["music"].get("addon") is True, f"{t}: music must be an add-on"
        baked = {e["file"] for e in server.manifest_files(m)}
        assert not any(f.startswith("music/") for f in baked), f"{t}: music files leak into the tier set"
    return "engine absent -> 503 'not installed' (no traceback), /api/models reports it; music is an add-on"


@run_test("unit_manifest_cmd_matches_built_engines", ["R31", "R8"])
async def t_manifest_cmd_built(_):
    """The invariant R31 violated for months: every engine a manifest execs must be one
    scripts/install-engines.sh actually builds (it called an 'ace-cli' nothing produced)."""
    inst = (REPO / "scripts" / "install-engines.sh").read_text()
    built = set(re.findall(r'"\$BIN/([\w.-]+)"', inst))
    assert {"llama-server", "sd-cli", "ace-lm", "ace-synth"} <= built, built
    assert "ServeurpersoCom/acestep.cpp" in inst and "--recursive" in inst.split("acestep.cpp", 1)[1], \
        "acestep.cpp must be cloned with its ggml submodule"
    for t in ("8gb", "16gb", "24gb", "32gb"):
        text = (REPO / "manifests" / f"tier-{t}.json").read_text()
        assert "ace-cli" not in text, f"{t}: still references the never-built ace-cli"
        m = json.loads(text)
        for key, sec in m.items():
            if not isinstance(sec, dict) or "cmd" not in sec:
                continue
            stages = sec["cmd"]
            assert stages and all(isinstance(st, list) and st for st in stages), f"{t}.{key}: cmd = list of argv lists"
            for st in stages:
                assert st[0].startswith("{bin}/"), f"{t}.{key}: {st[0]} is not a {{bin}} engine"
                assert st[0][6:] in built, f"{t}.{key}: no build script produces {st[0][6:]}"
                assert "--models" in st and "--request" in st, f"{t}.{key}: acestep.cpp CLI takes --models/--request"
        mu = m["music"]
        base = lambda f: f.split("/")[-1]
        # names in the AceRequest are exact GGUF filenames in {models}/music (registry_find)
        assert mu["request"]["synth_model"] == base(mu["dit"]["file"]) and mu["dit"]["url"].endswith(base(mu["dit"]["file"]))
        assert mu["request"]["lm_model"] == base(mu["lm"]["file"]) and mu["request"]["vae"] == base(mu["vae"]["file"])
        assert "turbo" in mu["dit"]["file"] and mu["request"]["inference_steps"] == 8, f"{t}: turbo DiT = 8 steps"
        # the whole turbo triple, = acestep.cpp's own auto-detect for a turbo DiT (src/pipeline-synth-ops.cpp:
        # turbo 8 / 1.0 / 3.0, base-sft 50 / 1.0 / 1.0); proven on the 780M with exactly these values
        # (docs/ENGINES-VULKAN-2026-09.md 9.5). A switch to base/sft must change `shift` too.
        assert (mu["request"]["guidance_scale"], mu["request"]["shift"]) == (1.0, 3.0), \
            f"{t}: turbo request must be guidance 1.0 / shift 3.0, got {mu['request']}"
        assert all(e["file"].startswith("music/") for e in (mu["dit"], mu["lm"], mu["text_encoder"], mu["vae"])), \
            f"{t}: all four GGUFs must sit in the one dir ace-* scans"
    return f"install-engines.sh builds {sorted(built)}; every manifest cmd execs one of them; turbo DiT + exact names"


def _elf_needed(path: Path) -> set:
    """DT_NEEDED sonames of an ELF64 binary, read from its .dynamic/.dynstr — the suite's
    container is not the binaries' userland, so `ldd` is not an option (no objdump either)."""
    import struct
    with open(path, "rb") as f:
        eh = f.read(64)
        assert eh[:4] == b"\x7fELF" and eh[4] == 2, f"{path}: not ELF64"
        shoff, shentsize, shnum = struct.unpack_from("<Q", eh, 0x28)[0], *struct.unpack_from("<HH", eh, 0x3a)
        f.seek(shoff); secs = [struct.unpack("<IIQQQQIIQQ", f.read(shentsize)[:64]) for _ in range(shnum)]
        dyn = next(s for s in secs if s[1] == 6)                     # SHT_DYNAMIC
        strtab = secs[dyn[6]]                                        # sh_link -> .dynstr
        f.seek(strtab[4]); strs = f.read(strtab[5])
        f.seek(dyn[4]); data = f.read(dyn[5])
    out = set()
    for off in range(0, len(data) - 15, 16):
        tag, val = struct.unpack_from("<qQ", data, off)
        if tag == 1:                                                 # DT_NEEDED
            out.add(strs[val:strs.index(b"\0", val)].decode())
    return out


@run_test("unit_cuda_engine_build", ["R41", "R50", "R31", "R8"])
async def t_cuda_build(_):
    """The CUDA set the image ships by default (docs/ENGINES-CUDA-2026-09.md): the arch default
    must cover the RTX 4070 (sm_89) AND the 5070 Ti (sm_120) — the old "120" built no kernel
    for the laptop; the toolkit comes from NVIDIA's Debian 13 repo, not the WSL leftover, as
    the four packages the builds need rather than the 3.3 GB meta; and the image's runtime
    bundle (iso/build-iso.sh -> /opt/vibeos/lib, kairos.service LD_LIBRARY_PATH) must name
    every CUDA lib every engine binary links, acestep's included — checked against the real
    NEEDED entries when the build artefact sits next to the checkout."""
    inst = (REPO / "scripts" / "install-engines.sh").read_text()
    m = re.search(r'^CUDA_ARCH="\$\{CUDA_ARCH:-([0-9;]+)\}"', inst, re.M)
    assert m, "no CUDA_ARCH default"
    archs = set(m.group(1).split(";"))
    assert {"89", "120"} <= archs, f"CUDA_ARCH default {archs} must cover sm_89 (4070) and sm_120 (5070 Ti)"
    assert "CMAKE_CUDA_ARCHITECTURES=\"$CUDA_ARCH\"" in inst
    assert "repos/debian13/x86_64" in inst and "repos/wsl-ubuntu" not in inst, "toolkit must come from NVIDIA's debian13 repo"
    assert "deb [trusted=yes]" not in inst, "trixie's apt accepts the debian13 keyring — no signature workaround"
    assert not re.search(r"apt-get install[^\n]*cuda-toolkit-", inst), "the full cuda-toolkit meta (3.3 GB) must not be installed"
    for pkg in ("cuda-nvcc-$V", "cuda-cudart-dev-$V", "libcublas-dev-$V", "cuda-driver-dev-$V"):
        assert f'"{pkg}"' in inst, f"{pkg} not installed"
    assert "--speed-limit 2048 --speed-time 60" in inst, "keyring fetch must be stall-safe"
    # acestep's ggml is a patched fork with llama.cpp's sonames: it must never be shared, or the
    # image's one /opt/vibeos/lib gets two libggml-cuda.so.0 and the last copy wins
    ace_cmake = re.search(r"^cmake -S acestep\.cpp .*$", inst, re.M)
    assert ace_cmake and "-DBUILD_SHARED_LIBS=OFF" in ace_cmake.group(0), "acestep stage must be static on every backend"
    assert re.search(r"^apt-get update -qq", inst, re.M) and inst.index("apt-get update -qq") < inst.index("ENGINE_BACKEND\" = cuda"), \
        "apt-get update must precede both toolchain branches"
    hook = (REPO / "iso" / "config" / "hooks" / "normal" / "0100-nvidia-cuda.hook.chroot").read_text()
    assert "repos/debian13/x86_64" in hook and "deb [trusted=yes]" not in hook, "driver hook: debian13 repo, no signature workaround"
    # the runtime bundle: every engine binary is ldd'd, the case list names the CUDA libs
    iso = (REPO / "iso" / "build-iso.sh").read_text()
    bundle = iso[iso.index("bundle the exact CUDA"):iso.index("bundled libs:")]
    assert 'for bin in "$INC/bin/"*' in bundle, "only llama-server and sd-cli were ldd'd — acestep's binaries were not"
    for lib in ("libcudart", "libcublas", "libcublasLt", "libmtmd"):
        assert lib in bundle, f"{lib} missing from the bundle"
    assert "LD_LIBRARY_PATH=/opt/vibeos/lib" in (REPO / "iso" / "config" / "includes.chroot" / "etc" / "systemd" / "system" / "kairos.service").read_text()
    # against the real thing when it is there (the 2026-09-14 build: ~/kairos/vibeos-cuda)
    art = next((p for p in (REPO.parent.parent / "vibeos-cuda" / "bin", Path("/images/vibeos-cuda/bin")) if p.is_dir()), None)
    checked = "artefact not present: static checks only"
    if art:
        def real(b):        # the script's symlinks are absolute /opt/vibeos/... — re-root them under the artefact
            t = os.readlink(art / b) if (art / b).is_symlink() else str(art / b)
            return art.parent / t[len("/opt/vibeos/"):] if t.startswith("/opt/vibeos/") else Path(t)
        bins = [b for b in ("llama-server", "llama-cli", "sd-cli", "ace-lm", "ace-synth", "ace-server")
                if (art / b).is_symlink() and real(b).is_file()]
        assert bins, f"{art}: no engine binaries"
        pattern = re.findall(r"\*(lib[A-Za-z]+)\*", bundle)         # the case list's lib names
        cuda_libs = set()
        for b in bins:
            for so in _elf_needed(real(b)):
                if so.startswith(("libcu", "libnv")) and not so.startswith("libcuda.so"):
                    cuda_libs.add(so)
                    assert any(so.startswith(p) for p in pattern), f"{b} links {so}, not in build-iso.sh's bundle list {pattern}"
        assert {"libcudart.so", "libcublas.so", "libcublasLt.so"} <= {s.rsplit(".so", 1)[0] + ".so" for s in cuda_libs}, cuda_libs
        # no two engine build dirs may ship the same libggml*.so (build-iso.sh copies them all into one dir)
        owners = {}
        for so in (art.parent / "engines").glob("*/build/**/libggml*.so*"):
            owners.setdefault(so.name, set()).add(so.relative_to(art.parent / "engines").parts[0])
        dup = {k: v for k, v in owners.items() if len(v) > 1}
        assert not dup, f"same-soname ggml libs from more than one engine: {dup}"
        checked = f"{len(bins)} binaries link {sorted(cuda_libs)} — all in the bundle list; {len(owners)} libggml*.so, one owner each"
    return f"CUDA_ARCH default {sorted(archs)}; debian13 repo, 4 packages, no meta; {checked}"


@run_test("unit_vulkan_engine_build", ["R30", "R33"])
async def t_vulkan_build(_):
    """R30's iGPU path must be rebuildable (the only artifacts were two prebuilt binaries)
    and actually selected on a machine without an NVIDIA driver."""
    inst = (REPO / "scripts" / "install-engines.sh").read_text()
    assert "GGML_VULKAN=ON" in inst and "SD_VULKAN=ON" in inst and "bin/vulkan" in inst, "no Vulkan build path"
    for dep in ("libvulkan-dev", "glslc"):
        assert dep in inst, f"Vulkan build dep {dep} not installed by the script"
    assert "BUILD_SHARED_LIBS=OFF" in inst, "Vulkan set must be static (no libggml*.so clash with the CUDA set)"
    wrapper = (REPO / "scripts" / "build-engines-vulkan.sh").read_text()
    assert "ENGINE_BACKEND=vulkan" in wrapper and "install-engines.sh" in wrapper
    pkgs = (REPO / "iso" / "config" / "package-lists" / "kairos.list.chroot").read_text()
    for pkg in ("libvulkan1", "mesa-vulkan-drivers", "vulkan-tools", "firmware-amd-graphics"):
        assert pkg in pkgs, f"runtime package {pkg} missing from the image"
    for sc in ("supervisor.sh", "rebuild-engines-multiarch.sh", "build-engines-only.sh", "build-engines-vulkan.sh"):
        assert "/mnt/c/Users" not in (REPO / "scripts" / sc).read_text(), f"{sc} still hard-codes the dead WSL path"
    for sc in ("build-image.sh", "../iso/build-iso.sh"):
        assert "bin/vulkan" in (REPO / "scripts" / sc).read_text(), f"{sc} does not ship the Vulkan set"
    # runtime selection: no NVIDIA driver + a Vulkan set -> vulkan; driver -> cuda; env wins
    import tempfile
    sys.path.insert(0, str(REPO / "kernel"))
    import server
    saved = (server.BIN, server.NVIDIA_PROC, os.environ.pop("VIBEOS_ENGINE", None))
    try:
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            (td / "vulkan").mkdir()
            (td / "vulkan" / "llama-server").touch()
            server.BIN, server.NVIDIA_PROC = td, td / "no-nvidia-driver"
            assert server.engine_backend() == "vulkan"
            assert server.engine_path("llama-server") == td / "vulkan" / "llama-server"
            assert server.engine_path("sd-cli") == td / "sd-cli", "missing Vulkan binary must fall back to bin/"
            assert server.engine_env().get("GGML_VK_VISIBLE_DEVICES") == "0", "RADV+llvmpipe hang guard"
            # UMA: --offload-to-cpu doubles the weights in the one GTT pool -> device lost
            # on the 780M (docs/ENGINES-VULKAN-2026-09.md 4.2); the other flags stay
            sd = ["--steps", "8", "--offload-to-cpu", "--vae-tiling", "-W", "768"]
            assert server.sd_backend_args(sd) == ["--steps", "8", "--vae-tiling", "-W", "768"]
            # the same strip must reach every sd-cli section the kernel runs (sd_job feeds
            # video and the add-ons through it too) and must leave the CPU *placement* flags
            # alone: --vae-on-cpu / --clip-on-cpu move a stage to the CPU backend, they do not
            # duplicate weights — proven on the 780M for Wan 2.2, Chroma, Z-Image and
            # Qwen-Image (docs/ENGINES-VULKAN-2026-09.md 8.2 / 8.3)
            for t in ("tier-8gb", "tier-16gb", "tier-24gb", "tier-32gb"):
                mp = REPO / "manifests" / f"{t}.json"
                if not mp.exists():
                    continue
                for sec, spec in json.loads(mp.read_text()).items():
                    if not (sec == "video" or sec.startswith("image")) or not isinstance(spec, dict):
                        continue
                    ga = spec.get("gen_args", [])
                    vk = server.sd_backend_args(ga)
                    assert "--offload-to-cpu" not in vk, f"{t}/{sec}: UMA double-copy flag survives"
                    assert vk == [a for a in ga if a != "--offload-to-cpu"], f"{t}/{sec}: more than the one flag changed"
            # acestep's ggml fork miscomputes on RADV coopmat (token-salad captions, 35 s
            # for 15 s): the music stages run without it, llama/sd keep it (same doc, 4.3)
            assert server.music_engine_env().get("GGML_VK_DISABLE_COOPMAT") == "1"
            assert "GGML_VK_DISABLE_COOPMAT" not in server.engine_env(), "llama/sd keep coopmat"
            server.NVIDIA_PROC.mkdir()
            assert server.engine_backend() == "cuda"
            # and stripped on CUDA as well: with it, sd.cpp 7f410a3 turns its auto-fit off and
            # Krea 2 on the 8 GB 4070 dies at the compute buffer; without it 35.8 s
            # (docs/ENGINES-CUDA-2026-09.md 5.2). The shipped manifests carry it nowhere.
            assert server.sd_backend_args(sd) == ["--steps", "8", "--vae-tiling", "-W", "768"], "offload-to-cpu must go on CUDA too"
            for t in ("tier-8gb", "tier-16gb", "tier-24gb", "tier-32gb"):
                assert "--offload-to-cpu" not in (REPO / "manifests" / f"{t}.json").read_text(), f"{t}: --offload-to-cpu is back"
            assert "GGML_VK_DISABLE_COOPMAT" not in server.music_engine_env(), "a Vulkan-only switch"
            assert server.engine_path("llama-server") == td / "llama-server"
            # neither an NVIDIA driver nor a Vulkan set: "cpu", never a claimed CUDA (D12)
            server.NVIDIA_PROC.rmdir(); (td / "vulkan" / "llama-server").unlink()
            assert server.engine_backend() == "cpu" and server.engine_path("llama-server") == td / "llama-server"
            info = server.engine_info()
            assert info == {"nvidia_driver": False, "vulkan_set": False, "forced": None}, info
            os.environ["VIBEOS_ENGINE"] = "vulkan"
            assert server.engine_backend() == "vulkan", "VIBEOS_ENGINE override ignored"
            assert server.engine_info()["forced"] == "vulkan"
    finally:
        server.BIN, server.NVIDIA_PROC = saved[0], saved[1]
        os.environ.pop("VIBEOS_ENGINE", None)
        if saved[2] is not None:
            os.environ["VIBEOS_ENGINE"] = saved[2]
    return "Vulkan build (llama/sd/ace, static, bin/vulkan) + runtime packages + cuda/vulkan auto-selection"


@run_test("http_image_uncensored", ["R29"])
async def t_image_uncensored(s):
    arglog = Path(os.environ.get("MOCK_SD_ARGLOG", VDIR / "sd-args.log"))
    async with s.post(f"{BASE}/api/image", json={"prompt": "test", "uncensored": True}) as r:
        jid = (await r.json())["job"]
    await poll_job(s, jid)
    last = arglog.read_text().splitlines()[-1]
    # [R76 → Muse, 2026-10-10] uncensored = the default: Muse v3.5 Extended Q4_0 (a Krea 2 fine-tune), no LoRA
    # (the Kroma v0.3 LoRA and the Kroma v0.2 add-on are retired; docs/IMAGE-MODEL-MUSE-2026-10-10.md)
    assert "museByStableYogi_v35Q4Extended" in last and "--llm" in last and "Qwen3VL-4B" in last and "--lora" not in last, \
        f"uncensored not routed to Muse without a LoRA (Qwen3-VL encoder): {last[:160]}"
    assert "\t--steps\t8\t" in "\t" + last + "\t" and "qwen_image_vae" in last, last[:200]
    argv = last.split("\t")
    prompt = argv[argv.index("-p") + 1]
    # docs/INTEGRATION-2026-09-28.md §3: the 18+ expander (rule 8 = adult content) rewrote the prompt
    assert "expanded by the mock expander for adults" in prompt, f"18+ expander not used: {prompt[:120]}"
    # Chroma1-HD stays reachable under its own key
    async with s.post(f"{BASE}/api/image", json={"prompt": "test", "model": "chroma"}) as r:
        jid = (await r.json())["job"]
    await poll_job(s, jid)
    last = arglog.read_text().splitlines()[-1]
    assert "Chroma1-HD" in last and "--t5xxl" in last and "chroma_use_dit_mask=false" in last, last[:200]
    return "uncensored -> Muse Q4_0, no LoRA (Qwen3-VL encoder, 8 steps) via the 18+ expander; model=chroma -> Chroma1-HD (T5-XXL, --model-args)"


@run_test("http_boot_status", ["R73"])
async def t_boot_status(s):
    """GET /api/boot (the boot screen's numbers) is read-only and well-formed; the R27 dream
    endpoints are gone (404)."""
    async with s.get(f"{BASE}/api/boot") as r:
        assert r.status == 200
        st = await r.json()
    for k in ("this_boot_seconds", "expected_seconds", "default_seconds", "history", "kernel_up_seconds"):
        assert k in st, f"missing field {k}"
    assert st["default_seconds"] == 120.0 and 20 <= st["expected_seconds"] <= 600 and isinstance(st["history"], list)
    async with s.post(f"{BASE}/api/boot", json={}) as r:
        assert r.status == 405, r.status
    for old in ("/api/bootanim", "/api/bootanim/generate", "/api/bootanim/config"):
        async with s.get(f"{BASE}{old}") as r:
            assert r.status == 404, (old, r.status)
    return f"expected {st['expected_seconds']}s (history {st['history']}); the R27 routes are gone"


@run_test("unit_hidden_linux_image", ["R28", "R30", "R33", "R73"])
async def t_hidden_linux(_):
    iso = REPO / "iso"
    b = (iso / "build-img.sh").read_text()
    assert "timeout 0" in b and "loglevel=0" in b and "vt.global_cursor_default=0" in b, "silent boot cmdline missing"
    pkgs = (iso / "config" / "package-lists" / "kairos.list.chroot").read_text()
    for p in ("mesa-vulkan-drivers", "firmware-amd-graphics", "vulkan-tools"):
        assert p in pkgs, f"{p} missing from image package list"
    assert not re.search(r"^mpv$", pkgs, re.M), "mpv left with the R27 animation (R73)"
    hook = (iso / "config" / "hooks" / "normal" / "0200-kairos-system.hook.chroot").read_text()
    assert "gttsize" in hook and "scripts/splash/install.sh" in hook and "hushlogin" in hook, "hide-linux/UMA hook incomplete"
    units = iso / "config" / "includes.chroot" / "etc" / "systemd" / "system"
    assert (units / "kairos-splash.service").exists() and (units / "emergency.service.d" / "kairos-splash.conf").exists() \
        and (units / "rescue.service.d" / "kairos-splash.conf").exists()
    assert not (units / "kairos-bootanim.service").exists(), "the R27 unit is gone"
    return "silent boot + boot screen unit/drop-ins + AMD/Vulkan drivers + UMA GTT in the image config"


@run_test("unit_boot_chain_never_shows_console", ["R73", "R28", "R63"])
async def t_boot_chain(_):
    """Firmware -> kernel -> the boot screen painter (initramfs, framebuffer) -> X -> the boot
    page -> kiosk: no window may show a Linux console, and no link of the chain may fail the
    boot. Static check of the whole chain; the painter itself (rendering, the fb handover,
    signals) is proven hermetically by tests/test_splash.py."""
    import re, subprocess
    iso = REPO / "iso"; inc = iso / "config" / "includes.chroot"
    units = inc / "etc" / "systemd" / "system"; home = inc / "home" / "kairos"
    hook = (iso / "config" / "hooks" / "normal" / "0200-kairos-system.hook.chroot").read_text()
    # 1. kernel + loader: nothing printed, no cursor, no menu
    b = (iso / "build-img.sh").read_text()
    opts = re.search(r"^options\s+(.*)$", b, re.M).group(1)
    for f in ("quiet", "loglevel=0", "systemd.show_status=false", "vt.global_cursor_default=0"):
        assert f in opts.split(), f"kernel cmdline lacks {f}: {opts}"
    assert "timeout 0" in b and "editor no" in b
    # 2. tty1 never prints: agetty autologin without --skip-login prints "login: kairos
    #    (automatic login)"; issue/motd empty; hushlogin
    m = re.search(r"^ExecStart=-/sbin/agetty (.*)$", hook, re.M); assert m, "getty override missing"
    for f in ("--autologin", "--skip-login", "--noissue", "--nohints", "--noclear"):
        assert f in m.group(1), f"agetty lacks {f}"
    assert "hushlogin" in hook and ": > /etc/issue" in hook
    # 3. the painter starts in the initramfs (before the GPU driver), is built into the initrd
    #    at image build time, holds tty1 in graphics mode and is ended only by the kiosk's
    #    handover or a failure path (panic hook / emergency drop-ins)
    itop = inc / "etc" / "initramfs-tools" / "scripts" / "init-top" / "kairos-splash"
    assert itop.exists() and "/sbin/kairos-splash --daemon" in itop.read_text() and 'PREREQ=""' in itop.read_text()
    assert (inc / "etc" / "initramfs-tools" / "hooks" / "kairos-splash").exists() and (inc / "etc" / "initramfs-tools" / "scripts" / "panic" / "kairos-splash").exists()
    chroot = b.split("<<'CHROOT'")[1].split("\nCHROOT")[0]
    assert chroot.index("scripts/splash/install.sh") < chroot.index("update-initramfs -u -k all"), "painter before the initrd build"
    unit = (units / "kairos-splash.service").read_text()
    assert "DefaultDependencies=no" in unit and "ExecStart=-/usr/local/sbin/kairos-splash-ctl userspace" in unit, "the userspace milestone never fails the boot"
    for svc in ("emergency", "rescue"):
        assert "ExecStartPre=-/usr/local/sbin/kairos-splash --stop" in (units / f"{svc}.service.d" / "kairos-splash.conf").read_text()
    src = (REPO / "scripts" / "splash" / "kairos-splash.c").read_text()
    assert "KD_GRAPHICS" in src and 'leave("handover: the kiosk is up, X owns the display", 0)' in src
    # 4. every /opt/vibeos/os path .xinitrc references exists in the repo
    xinit = (home / ".xinitrc").read_text(); prof = (home / ".bash_profile").read_text()
    for path in set(re.findall(r"/opt/vibeos/os/(\S+?)(?:[\"'\s;]|$)", xinit)):
        assert (REPO / path).exists(), f"referenced /opt/vibeos/os/{path} is not in the repo"
    waitpage = (REPO / "shell" / "boot-wait.html").read_text()
    assert "api/status" in waitpage and "location.replace" in waitpage and "kAIrOS" in waitpage and "splash/logo-thick.png" in waitpage
    assert (REPO / "shell" / "splash" / "logo-thick.png").exists()
    # 5. handovers: painter -> X leaves the frame (X -background none, tty1 never cleared, the
    #    X milestone flagged), X -> kiosk opens the boot page AT ONCE and hands the painter over
    #    only after the kiosk launch; no mpv, no animation, no solid root in between
    assert "touch /run/kairos/splash-x" in prof and "-background none" in prof and not re.search(r"^\s*startx .*-nocursor", prof, re.M)   # v7.5: -nocursor hid every pointer
    assert not re.search(r"\\033\[2J|\\033c|\btput clear\b|^\s*clear\s*$", prof, re.M), "tty1 must never be cleared (fbcon repaints)"
    subprocess.run(["bash", "-n", str(home / ".bash_profile")], check=True)
    subprocess.run(["sh", "-n", str(home / ".xinitrc")], check=True)
    assert "xsetroot -solid" not in xinit and "mpv" not in xinit and "mpv" not in prof
    assert 'KIOSK_URL="file://$BOOTPAGE"' in xinit and "&boot=1&up=" in xinit
    assert xinit.index("launch_all_kiosks\n") < xinit.index("handover_splash\n") < xinit.index("while true; do\n        sleep 3")
    assert ": > /run/kairos/splash-handover" in xinit.split("handover_splash()")[1], "the painter is ended only by the handover"
    assert "launching the kiosk anyway" not in xinit
    # 6. the boot log quotes the painter's log
    bl = (inc / "usr" / "local" / "sbin" / "kairos-bootlog").read_text()
    assert "BOOT SCREEN LOG" in bl and "/run/kairos-splash.log" in bl
    return "cmdline silent, agetty --skip-login, painter from the initramfs before udev + in the initrd, KD_GRAPHICS until the kiosk, boot page first, handover after launch, no mpv"


@run_test("unit_bootlog_capture_wired", ["R51", "R63"])
async def t_bootlog(_):
    """Boot diagnostics must actually ship: every unit's exec target exists in the repo
    and is executable, the build enables the capture units (and proves it), the capture
    scripts parse and produce a verdict + json, and no capture unit can dirty the boot."""
    import re, subprocess, tempfile
    iso = REPO / "iso"; inc = iso / "config" / "includes.chroot"
    units = inc / "etc" / "systemd" / "system"
    sbin = inc / "usr" / "local" / "sbin"
    # 1. every ExecStart/Stop/Pre/Post path under the overlay units resolves to a repo file with +x
    checked = 0
    for u in sorted(units.glob("*.service")):
        for m in re.finditer(r"^Exec(?:Start|Stop|StartPre|StartPost)=[-@+!:]*(\S+)", u.read_text(), re.M):
            path = m.group(1)
            if path.startswith("/opt/vibeos/os/"):
                f = REPO / path[len("/opt/vibeos/os/"):]
            elif path.startswith("/usr/local/sbin/"):
                f = sbin / path[len("/usr/local/sbin/"):]
            else:
                continue                                  # /bin/sh, /usr/bin/python3, ... (system)
            assert f.exists(), f"{u.name}: {path} missing from the repo"
            assert os.access(f, os.X_OK) and (f.stat().st_mode & 0o111), f"{u.name}: {path} lost its exec bit (203/EXEC)"
            checked += 1
    assert checked >= 5, checked
    # 2. the failure/emergency/early captures can never fail or slow a boot [R63]
    for name in ("kairos-bootlog.service", "kairos-bootlog-early.service", "kairos-bootlog-failure@.service",
                 "kairos-bootlog-emergency.service", "kairos-bootlog-xfail.path"):
        body = (units / name).read_text()
        assert "DefaultDependencies=no" in body, f"{name}: needs DefaultDependencies=no"
        for line in body.splitlines():
            if line.startswith("ExecStart="):
                assert line.startswith("ExecStart=-"), f"{name}: {line} must be ExecStart=- (never fails the boot)"
    early = (units / "kairos-bootlog-early.service").read_text()
    assert "After=local-fs.target" in early and "Before=basic.target" in early
    assert "WantedBy=emergency.target rescue.target" in (units / "kairos-bootlog-emergency.service").read_text()
    assert "PathExists=/run/kairos/x-failed" in (units / "kairos-bootlog-xfail.path").read_text()
    # the failure capture is a per-trigger template (D10): every trigger names its own instance
    assert not (units / "kairos-bootlog-failure.service").exists(), "the shared failure unit is gone (ambiguous trigger)"
    assert re.search(r"^ExecStart=-/usr/local/sbin/kairos-bootlog failure %i$", (units / "kairos-bootlog-failure@.service").read_text(), re.M)
    assert "OnFailure=kairos-bootlog-failure@%n.service" in (units / "kairos.service").read_text()
    assert "OnFailure=kairos-bootlog-failure@%n.service" in (units / "local-fs.target.d" / "kairos-bootlog.conf").read_text()
    assert re.search(r"^Unit=kairos-bootlog-failure@x11\.service$", (units / "kairos-bootlog-xfail.path").read_text(), re.M)
    tmpf = (inc / "etc" / "tmpfiles.d" / "kairos.conf").read_text()
    assert "/var/log/kairos" in tmpf and "/run/kairos" in tmpf
    prof = (inc / "home" / "kairos" / ".bash_profile").read_text()
    assert "kairos/x.log" in prof and "x-failed" in prof and "startx" in prof, "X attempts must be logged + bounded"
    # 3. the builds enable + verify it (the chroot heredoc MUST be quoted: unquoted, the
    #    build host expanded $d and the whole block — bootlog enable included — was skipped)
    b = (iso / "build-img.sh").read_text()
    assert "<<'CHROOT'" in b and not re.search(r"<<CHROOT\s*$", b, re.M), "build-img chroot heredoc must be quoted"
    assert re.search(r"^systemctl enable [^\n]*kairos-bootlog\.service", b, re.M), "build-img must enable kairos-bootlog"
    assert "kairos-bootlog-early.service" in b and "multi-user.target.wants/kairos-bootlog.service" in b, \
        "build-img must enable the early breadcrumb and assert the enable happened"
    assert "OnFailure=kairos-bootlog-failure@%n.service" in b, "growroot unit needs the failure capture (own instance)"
    hook = (iso / "config" / "hooks" / "normal" / "0200-kairos-system.hook.chroot").read_text()
    assert "kairos-bootlog.service" in hook and "kairos-bootlog-early.service" in hook
    biso = (iso / "build-iso.sh").read_text()
    assert '"$REPO/scripts"' in biso, "build-iso must stage scripts/ (scripts/splash/install.sh runs from /opt/vibeos/os/scripts)"
    # 4. the scripts parse, accept both ESP labels, and really produce verdict + json + breadcrumb
    bl, ble = sbin / "kairos-bootlog", sbin / "kairos-bootlog-early"
    for f in (bl, ble):
        subprocess.run(["sh", "-n", str(f)], check=True)
    subprocess.run(["bash", "-n", str(inc / "home" / "kairos" / ".bash_profile")], check=True)
    body = bl.read_text()
    assert "blkid -L EFI" in body and "blkid -L ESP" in body, "ESP fallback must accept both labels (image: EFI, kairos-install: ESP)"
    with tempfile.TemporaryDirectory() as td:
        esp = Path(td) / "esp"; runf = Path(td) / "run-attempt"
        env = dict(os.environ, KAIROS_BOOTLOG_ESP=str(esp), KAIROS_BOOTLOG_RUN=str(runf))
        logdir = esp / "KAIROS-LOGS"
        for i in range(2):                                   # two "boots": counter + incomplete marker
            runf.unlink(missing_ok=True)
            subprocess.run([str(ble)], env=env, check=True, timeout=30)
        att = (logdir / "attempts.txt").read_text()
        assert "#1  started" in att and "#2  started" in att and "#1  DID NOT COMPLETE" in att, att
        assert runf.read_text().strip() == "2" and (logdir / "early.txt").exists()
        for i in range(20):                                  # rotation cap: 16 captures max (2026-10-06: 8 -> 16)
            (logdir / f"boot-200001{i:02d}-000000.txt").write_text("old")
        subprocess.run([str(bl), "selftest"], env=env, check=True, timeout=170)
        txt = (logdir / "latest.txt").read_text(errors="replace").splitlines()
        assert txt[0].startswith("==================== kAIrOS boot log (selftest)") and "SUMMARY / VERDICT" in txt[1]
        assert txt[2].startswith("VERDICT: ") and txt[2].split()[1] in ("PASS", "WARN", "FAIL"), txt[2]
        j = json.loads((logdir / "latest.json").read_text())
        assert j["schema"] == 1 and j["verdict"] in ("PASS", "WARN", "FAIL") and j["attempt"] == 2
        assert j["reason"] == "selftest" and j["trigger"] == "", "json must carry reason + the failing unit (empty here)"
        for k in ("boot_target", "failed_units", "gpu_driver", "xorg", "kernel_api", "kiosk", "rootfs_grow", "network"):
            assert j["checks"][k]["status"] in ("PASS", "WARN", "FAIL"), k
        assert len(list(logdir.glob("boot-*.txt"))) <= 16, "ESP rotation cap broken"
        assert "#2  selftest log written" in (logdir / "attempts.txt").read_text()
        assert "all PCI devices" in "\n".join(txt) and "KAIROS KERNEL LOG" in "\n".join(txt)
    return (f"{checked} unit exec paths present+executable; capture units boot-safe; build quotes the chroot "
            f"heredoc + enables/asserts bootlog; scripts run: verdict={j['verdict']} {len(j['checks'])} checks, json+breadcrumb ok")




@run_test("unit_appliance_boot_hardening", ["R48", "R51", "R63"])
async def t_boot_hardening(_):
    """The appliance must not die silently on real hardware: emergency prompts reach
    the PANEL (last console= is tty1, serial kept for the testrig), a bad ESP never
    fails local-fs.target (nofail, fsck pass != 1), the initramfs carries every
    storage controller a target laptop may have, and kairos-install fails loudly
    instead of reporting DONE for an unbootable disk."""
    import re, subprocess
    iso = REPO / "iso"; inc = iso / "config" / "includes.chroot"
    b = (iso / "build-img.sh").read_text(); pim = (iso / "patch-img.sh").read_text()
    inst = inc / "usr" / "local" / "sbin" / "kairos-install"; ki = inst.read_text()
    # 1. console order: last console= is the panel; serial still listed (QEMU testrig)
    entries = re.findall(r"^options\s+(.*)$", b, re.M)
    assert len(entries) >= 2, "expected the silent default entry + the serial debug entry"
    cons = [t for t in entries[0].split() if t.startswith("console=")]
    assert cons and cons[-1] == "console=tty1", f"/dev/console must be the panel, got {cons}"
    assert "console=ttyS0,115200" in cons, "serial console must stay available for the testrig"
    assert "quiet" in entries[0].split() and "loglevel=0" in entries[0].split()
    assert "kairos-serial.conf" in b and [t for t in entries[1].split() if t.startswith("console=")][-1].startswith("console=ttyS0")
    # patch-img rewrites ALREADY-BUILT images: its search pattern is the old order
    # (both consoles, panel first), its replacement must equal what build-img emits now
    m = re.search(r"s/(console=[^/]+)/(console=[^/]+)/", pim)
    assert m and m.group(2) in entries[0] and m.group(2).split()[-1] == "console=tty1", \
        f"patch-img sed replacement must be what build-img emits: {m and m.group(0)}"
    assert m.group(1).split() == ["console=tty1", "console=ttyS0,115200"], f"patch-img must match the old order: {m.group(1)}"
    icons = [t for t in re.search(r"^options\s+(.*)$", ki, re.M).group(1).split() if t.startswith("console=")]
    assert icons == ["console=tty1"], f"kairos-install cmdline consoles: {icons}"
    # 2. ESP fstab: nofail + device timeout, fsck pass 2 (root stays pass 1)
    for name, text in (("build-img.sh", b), ("kairos-install", ki)):
        esp = next(l for l in text.splitlines() if "/boot/efi" in l and "vfat" in l)
        opts = esp.split()[3].split(",")
        assert "nofail" in opts and "x-systemd.automount" in opts and "x-systemd.idle-timeout=120" in opts \
            and "x-systemd.device-timeout=45" in opts, f"{name}: {esp}"   # v7.5: mounted on first access
        assert esp.split()[-1] in ("2", "0") and esp.split()[-2] == "0", f"{name}: ESP fsck pass must not be 1: {esp}"
        root = next(l for l in text.splitlines() if " / " in l.replace("  ", " ") and "ext4" in l)
        assert root.split()[-2:] == ["0", "1"], f"{name}: root fsck pass: {root}"
    early = (inc / "etc" / "systemd" / "system" / "kairos-bootlog-early.service").read_text()
    assert re.search(r"^After=.*boot-efi\.mount", early, re.M), "bootlog-early must not race the (nofail) ESP mount"
    bl = (inc / "usr" / "local" / "sbin" / "kairos-bootlog").read_text()
    assert 'mount "$ESP"' in bl and "blkid -L EFI" in bl, "bootlog must still mount / find the ESP itself"
    # 3. initramfs: every storage path a target machine may boot from, in both builders
    for name, text in (("build-img.sh", b), ("patch-img.sh", pim)):
        mods = re.search(r"/etc/initramfs-tools/modules <<M\n(.*?)\nM\n", text, re.S).group(1).split()
        for m in ("vmd", "nvme_core", "nvme", "ahci", "sd_mod", "usb_storage", "uas", "xhci_pci", "sdhci_pci", "mmc_block", "ext4"):
            assert m in mods, f"{name}: initramfs module list lacks {m} (silent unbootable machine)"
    # 4. kairos-install: verifies the loader, copies payload mounts -x skipped, swap non-fatal
    subprocess.run(["bash", "-n", str(inst)], check=True)
    assert re.search(r'EFI/BOOT/BOOTX64\.EFI.*\n.*die ', ki) or "BOOTX64.EFI" in ki.split("for f in")[1], "no bootloader post-condition"
    assert "die(){" in ki and "exit 5" in ki and ki.index("bootloader verified") < ki.index('log "DONE'), "must fail loudly before DONE"
    assert "mountpoint -q \"$d\"" in ki and "/opt/vibeos/models" in ki, "rsync -x skips the payload bind mounts"
    fl = next(l for l in ki.splitlines() if "fallocate -l 8G" in l)
    assert fl.lstrip().startswith("if ") and "SWAPLINE" in ki, "swapfile creation must be non-fatal"
    assert "trap cleanup EXIT" in ki
    return (f"console order {cons} (+serial entry), ESP nofail pass 2 in both, "
            f"initramfs {len(mods)} modules incl. vmd, installer verifies BOOTX64.EFI")


@run_test("unit_hw_deps_and_kiosk_reload", ["R38", "R53", "R55", "R56"])
async def t_hw_deps(_):
    """kernel/hw.py against the image: (a) the kiosk-reload flag (.xinitrc's; [R90] the display
    API no longer relaunches the kiosk — the desktop follows a layout change live) lives in a
    dir the kiosk user owns, the same dir as the kernel's sign-in flags; (b) every binary hw.py shells out to has its package in the image list, plus
    the sound server itself; (c) timed-out children are killed, not leaked; (d) real
    xrandr output parses (rotation) and the FULL layout is persisted."""
    import importlib, re, tempfile, time
    iso = REPO / "iso"; inc = iso / "config" / "includes.chroot"
    src = (REPO / "kernel" / "hw.py").read_text()
    # (a) reload flag: under /run/kairos (the kernel's kiosk flags' dir), tmpfiles gives that dir to kairos
    xinit = (inc / "home" / "kairos" / ".xinitrc").read_text()
    m = re.search(r"^RELOAD=(\S+)$", xinit, re.M)
    flag = m and m.group(1)
    assert flag == "/run/kairos/kiosk-reload", f".xinitrc RELOAD={flag}"
    assert str(Path(re.search(r'KAIROS_OPEN_URL",\s*"([^"]+)"', src).group(1)).parent) == str(Path(flag).parent)
    assert "KIOSK_RELOAD" not in src and "/tmp/kairos-kiosk-reload" not in xinit
    assert 'rm -f "$RELOAD"' in xinit and '[ -f "$RELOAD" ] && break' in xinit
    tmpf = (inc / "etc" / "tmpfiles.d" / "kairos.conf").read_text()
    d = str(Path(flag).parent)
    assert re.search(rf"^d\s+{re.escape(d)}\s+0755\s+kairos\s+kairos", tmpf, re.M), f"{d} must be created owned by kairos"
    assert d != "/tmp", "sticky /tmp: the kiosk user cannot unlink a root-owned flag"
    # (b) every _tool("x") in hw.py -> package in the image (or part of systemd)
    pkgs = (iso / "config" / "package-lists" / "kairos.list.chroot").read_text().split()
    DEPS = {"nmcli": "network-manager", "pactl": "pulseaudio-utils", "amixer": "alsa-utils",
            "xrandr": "x11-xserver-utils", "bluetoothctl": "bluez",
            "timedatectl": None, "systemctl": None,          # None = systemd, always present
            "rfkill": "rfkill", "busctl": None}              # [R78] radio blocks + NM's LastScan
    tools = set(re.findall(r'_tool\("([^"]+)"\)', src))
    assert tools >= {"nmcli", "pactl", "bluetoothctl", "xrandr"}, tools
    for t in sorted(tools):
        assert t in DEPS, f"hw.py calls {t}: add its package to the test map + kairos.list.chroot"
        if DEPS[t]:
            assert DEPS[t] in pkgs, f"{t} needs {DEPS[t]} in the image package list"
    for p in ("pipewire", "pipewire-pulse", "wireplumber", "bluez", "libspa-0.2-bluetooth", "firmware-sof-signed"):
        assert p in pkgs, f"{p} missing: no sound server / bluetooth audio on bare metal"
    hook = (iso / "config" / "hooks" / "normal" / "0200-kairos-system.hook.chroot").read_text()
    assert "pipewire-pulse.socket" in hook and "bluetooth.service" in hook
    assert "PULSE_SERVER" in src, "root kernel must reach the kiosk user's pipewire-pulse"
    # (c) hermetic: a child that outlives the timeout is killed + reaped
    sys.path.insert(0, str(REPO / "kernel")); hw = importlib.import_module("hw")
    procs = []; orig = hw.asyncio.create_subprocess_exec
    async def spy(*a, **k):
        p = await orig(*a, **k); procs.append(p); return p
    hw.asyncio.create_subprocess_exec = spy
    try:
        t0 = time.time(); rc, out = await hw._run(["sleep", "30"], timeout=0.5)
    finally:
        hw.asyncio.create_subprocess_exec = orig
    assert rc == -1 and "timed out" in out and time.time() - t0 < 5, (rc, out)
    assert procs and procs[0].returncode is not None, "timed-out child left running (leak)"
    rc, out = await hw._run(["/nonexistent/tool"]); assert rc == -1
    # (d) real xrandr text: rotation only from the keyword before the "(...)" list
    q = ("Screen 0: minimum 320 x 200, current 3000 x 1920, maximum 16384 x 16384\n"
         "eDP-1 connected primary 1920x1080+0+0 (normal left inverted right) 344mm x 194mm\n"
         "   1920x1080     60.01*+\n"
         "HDMI-1 connected 1080x1920+1920+0 left (normal left inverted right) 527mm x 296mm\n"
         "   1920x1080     60.00*+\n"
         "DP-1 connected (normal left inverted right)\n"
         "DP-2 disconnected (normal left inverted right)\n")
    outs = {o["output"]: o for o in hw._parse_xrandr(q)}
    assert outs["eDP-1"]["rotation"] == "normal" and outs["eDP-1"]["primary"]
    assert outs["HDMI-1"]["rotation"] == "left" and not outs["HDMI-1"]["primary"]
    args = await hw._layout_args(q)
    assert args == ["--output", "eDP-1", "--mode", "1920x1080", "--pos", "0x0", "--rotate", "normal", "--primary",
                    "--output", "HDMI-1", "--mode", "1080x1920", "--pos", "1920x0", "--rotate", "left",
                    "--output", "DP-1", "--off"], args
    with tempfile.TemporaryDirectory() as td:   # pactl env finds the kiosk user's socket
        sock = Path(td) / "1000" / "pulse" / "native"; sock.parent.mkdir(parents=True); sock.touch()
        old = hw.RUN_USER; hw.RUN_USER = Path(td)
        try:
            env = {k: v for k, v in hw._paenv().items() if k == "PULSE_SERVER"}
        finally:
            hw.RUN_USER = old
        assert env.get("PULSE_SERVER") == f"unix:{sock}", env
    return (f"flag {flag} (kairos-owned dir) shared by kernel+.xinitrc; {len(tools)} tools packaged; "
            f"timeout kills child in {time.time() - t0:.1f}s; full layout {len(args)} args")


@run_test("unit_one_desktop_kiosk_supervisor_hint", ["R90", "R52", "R63"])
async def t_multiscreen(_):
    """[R90] ONE kiosk over every monitor (R52's one kiosk per monitor is replaced: tests/test_one_desktop.py covers
    the launch line, the monitor math and the real-X behaviour): the supervisor judges liveness by the launched
    browser PID (a lingering helper with the same cmdline does not mask a dead browser); headless / an unknown screen
    size = one plain --kiosk; the ?mon= hint .xinitrc writes is what shell.js and the boot page read (KMon.parseHint)
    and the boot page forwards it (and only it) to the shell; the machine-level singletons (first-boot setup,
    installer, welcome) exist once. Hermetic sh run of the extracted .xinitrc functions against a fake xrandr +
    fake chromium."""
    import re, subprocess, tempfile
    inc = REPO / "iso" / "config" / "includes.chroot"
    xinit = (inc / "home" / "kairos" / ".xinitrc").read_text()
    subprocess.run(["sh", "-n", str(inc / "home" / "kairos" / ".xinitrc")], check=True)
    funcs = "".join(re.search(rf"^{f}\(\) \{{\n.*?^\}}\n", xinit, re.M | re.S).group(0)
                    for f in ("monitor_hint", "desktop_size", "launch_all_kiosks", "kiosks_alive"))
    assert "kiosks_alive || " in xinit and not re.search(r"pgrep -f .chromium", xinit), \
        "the supervisor must watch the browser PIDs, not a pgrep pattern helpers match too"
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); (t / "bin").mkdir(); log = t / "chromium.log"; mock = t / "xrandr.txt"
        (t / "bin" / "xrandr").write_text(f'#!/bin/sh\ncat "{mock}"\n')
        (t / "bin" / "xsetroot").write_text("#!/bin/sh\nexit 0\n")
        # fake browser: logs its argv, spawns a "zygote" helper carrying the same
        # cmdline (what pgrep -f matched) that OUTLIVES it, then idles like a browser
        (t / "bin" / "chromium").write_text(
            f'#!/bin/sh\necho "$*" >> "{log}"\n"{t}/bin/chromium.helper" --type=zygote "$@" >/dev/null 2>&1 &\n'
            f'echo $! >> "{t}/helpers"\nexec sleep 300 >/dev/null 2>&1\n')
        (t / "bin" / "chromium.helper").write_text('#!/bin/sh\nexec sleep 300 >/dev/null 2>&1\n')
        for f in (t / "bin").iterdir():
            f.chmod(0o755)
        env = dict(os.environ, PATH=f"{t / 'bin'}:/usr/bin:/bin")
        def sh(script):
            return subprocess.run(["sh", "-c", "DESK=/nonexistent\nPTR_DIAG=1\n" + funcs + "\n" + script], env=env,
                                  capture_output=True, text=True, timeout=60)
        Q = ("Screen 0: minimum 320 x 200, current 3840 x 1080, maximum 16384 x 16384\n"
             "eDP-1 connected primary 1920x1080+0+0 (normal left inverted right) 344mm x 194mm\n"
             "   1920x1080     60.01*+  48.00\n")
        pids = []
        try:
            mock.write_text(Q + "HDMI-1 connected 1920x1080+1920+0 (normal)\n")
            r = sh('KIOSK_URL=http://localhost:1337/; CHROME_FLAGS=--flag\nlaunch_all_kiosks >/dev/null\n'
                   'echo "PIDS:$KIOSK_PIDS"\nkiosks_alive && echo ALIVE1 || echo DEAD1\n'
                   'first=${KIOSK_PIDS# }; first=${first%% *}; kill "$first"; sleep 0.5\n'
                   'kiosks_alive && echo ALIVE2 || echo DEAD2\n')
            pids += [int(x) for x in re.search(r"PIDS:(.*)", r.stdout).group(1).split()]
            assert len(pids) == 1 and "ALIVE1" in r.stdout and "DEAD2" in r.stdout, (r.stdout, r.stderr)
            args = log.read_text().splitlines()
            assert len(args) == 1 and "--kiosk" not in args[0].split() and "--class=kairos-desktop" in args[0] \
                and "--window-position=0,0" in args[0] and "--window-size=3840,1080" in args[0] \
                and args[0].endswith("--app=http://localhost:1337/?mon=eDP-1:0,0,1920,1080,1;HDMI-1:1920,0,1920,1080,0"), args
            helpers = [int(x) for x in (t / "helpers").read_text().split()]
            pids += helpers
            for h in helpers:
                os.kill(h, 0)                    # the old pgrep pattern would still "see a kiosk"
            log.unlink()                         # headless / unknown: exactly one plain --kiosk
            mock.write_text("eDP-1 disconnected (normal)\n")
            r = sh('KIOSK_URL=http://localhost:1337/; CHROME_FLAGS=--flag\nlaunch_all_kiosks >/dev/null\necho "PIDS:$KIOSK_PIDS"')
            pids += [int(x) for x in re.search(r"PIDS:(.*)", r.stdout).group(1).split()]
            args = log.read_text().splitlines()
            assert len(args) == 1 and args[0].startswith("--kiosk ") and args[0].endswith("?mon="), args
            pids += [int(x) for x in (t / "helpers").read_text().split()]
        finally:
            for pid in pids:
                try:
                    os.kill(pid, 15)
                except ProcessLookupError:
                    pass
    # the round trip: the hint .xinitrc puts in the URL is what the shell and the boot page read
    assert re.search(r'--app="\$\{KIOSK_URL\}\?mon=\$\{MON\}\$\{BOOT_QS\}"', xinit)
    js = (REPO / "shell" / "shell.js").read_text()
    assert 'KMon.parseHint(new URLSearchParams(location.search).get("mon"))' in js and "SCREEN" not in js
    boot = js.split("(async function boot()")[1]
    assert re.search(r'toast\("❖ welcome[^;]*;\s*checkSetup\(\);\s*checkInstallable\(\);', boot), \
        "first-boot setup + installer + welcome once, on the one desktop page (dialogs centred on the primary)"
    css = (REPO / "shell" / "shell.css").read_text()
    assert "screen-secondary" not in css and "padding: var(--pm-y) var(--pm-r) var(--pm-b) var(--pm-x);" in css
    bw = (REPO / "shell" / "boot-wait.html").read_text()
    assert "location.replace(shellUrl())" in bw and '["boot", "up", "now", "expected", "p", "relaunch"]' in bw, "the boot page forwards the hint (and only the hint)"
    bl = (inc / "usr" / "local" / "sbin" / "kairos-bootlog").read_text()   # diagnostics count by profile dir
    assert "--user-data-dir=/tmp/kiosk-" in bl and "--user-data-dir=/tmp/kiosk-0" in xinit
    return "one kiosk with ?mon=, PID liveness sees a dead browser, headless = plain --kiosk, hint read by shell + boot page"


@run_test("unit_kernel_startup_survives_bad_cache", ["R63"])
async def t_startup_bad_cache(_):
    """kairos.service restarts a crashed kernel (Restart=on-failure, then `failed` after the
    start limit): an unwritable / full cache must never make the kernel crash-loop
    (black screen forever). Hermetic: a regular FILE in the place of
    each directory makes mkdir/write fail for root too; startup-path code must degrade
    with a warning. Plus AST: no module-level filesystem write in any kernel module."""
    import ast, importlib, tempfile
    sys.path.insert(0, str(REPO / "kernel"))
    boottime = importlib.import_module("boottime"); vfs = importlib.import_module("vfs")
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); (t / "cache").mkdir(); (t / "cache" / "boottime").write_text("not a directory")
        saved = (boottime._DIR, dict(boottime._STATE))
        try:
            boottime.init(t)                             # on_startup: mkdir + expected write fail -> warnings, no raise
            assert boottime.desktop_ready(12.5) == 12.5  # boot-times.json unwritable -> still measured
            assert boottime.status()["this_boot_seconds"] == 12.5 and boottime.expected() == boottime.DEFAULT_EXPECTED
        finally:
            boottime._DIR = saved[0]; boottime._STATE.update(saved[1])
        (t / "fs").write_text("not a directory")
        FS = vfs.VFS(t)                                  # module-level in server.py
        assert FS.populate_system({"os-release": "x", "docs/a.md": REPO / "README.md"}) == 2
    src = (REPO / "kernel" / "server.py").read_text()
    assert "WARN startup step" in src and re.search(r"middlewares=\[[^\]]*\bdisk_full_middleware\]", src) \
        and "error_log_middleware" in src, "disk-full + error-log middlewares installed (2026-10-06)"
    assert src.count("_ensure_dir(") >= 3, "module-level cache dirs must go through the guarded helper"
    bare = {}
    for mod in sorted((REPO / "kernel").glob("*.py")):
        tree = ast.parse(mod.read_text())
        hits = [n.lineno for st in tree.body
                if not isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Try))
                for n in ast.walk(st) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr in ("mkdir", "write_text", "write_bytes", "unlink", "makedirs")]
        if hits:
            bare[mod.name] = hits
    assert not bare, f"unguarded module-level filesystem writes (crash-loop on a bad disk): {bare}"
    return "boottime init/history, VFS dirs + /system population degrade with warnings; no bare module-level writes"


@run_test("unit_cache_bound_and_disk_full", ["R67", "R63"])
async def t_cache_bound(_):
    """[R67] generated media is a bounded cache: oldest files go first until the size cap
    and the free-space floor hold, files younger than a minute are never touched; a job
    on a still-full disk fails with ONE clean message; a write that hits ENOSPC answers
    507 JSON through the middleware (other OSErrors pass through untouched)."""
    import errno, tempfile
    sys.path.insert(0, str(REPO / "kernel"))
    import server
    saved = (server.EVICTABLE_DIRS, server.MEDIA_CACHE_MAX, server.DISK_FREE_MIN, server.CACHE_DIR, server.disk_free_bytes)
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); dirs = tuple(t / d for d in ("images", "videos", "music", "web"))
        for d in dirs:
            d.mkdir()
        try:
            server.EVICTABLE_DIRS, server.CACHE_DIR, server.DISK_FREE_MIN = dirs, t, 0
            now = time.time()
            for i in range(10):                          # f0 oldest … f9 newest, 1000 B each
                f = dirs[i % 4] / f"f{i}.png"; f.write_bytes(b"x" * 1000)
                os.utime(f, (now - 3600 * (10 - i),) * 2)
            fresh = dirs[0] / "fresh.png"; fresh.write_bytes(b"x" * 1000)   # a job is writing this
            server.MEDIA_CACHE_MAX = 5000
            r = server.evict_cache("test")
            left = sorted(p.name for d in dirs for p in d.iterdir())
            assert r["evicted"] == 6 and left == ["f6.png", "f7.png", "f8.png", "f9.png", "fresh.png"], (r, left)
            server.ensure_cache_space("image")           # fits now: no error
            server.MEDIA_CACHE_MAX = 500
            r = server.evict_cache("test")
            assert r["evicted"] == 4 and fresh.exists() and sum(1 for d in dirs for _ in d.iterdir()) == 1, r
            server.DISK_FREE_MIN = 10**9; server.disk_free_bytes = lambda path=None: 100   # disk still full
            try:
                server.ensure_cache_space("video"); assert False, "must fail cleanly"
            except RuntimeError as e:
                assert "disk full" in str(e) and "0 MB free" in str(e), e
            assert fresh.exists(), "a file a job may still be writing is never evicted"
            class Req:
                method, path = "POST", "/api/store"
            async def enospc(request):
                raise OSError(errno.ENOSPC, "No space left on device")
            resp = await server.disk_full_middleware(Req(), enospc)
            assert resp.status == 507 and "disk full" in json.loads(resp.text)["error"], resp.text
            async def reset(request):
                raise ConnectionResetError(errno.ECONNRESET, "reset")
            try:
                await server.disk_full_middleware(Req(), reset); assert False
            except ConnectionResetError:
                pass                                     # not a disk problem: untouched
        finally:
            (server.EVICTABLE_DIRS, server.MEDIA_CACHE_MAX, server.DISK_FREE_MIN, server.CACHE_DIR,
             server.disk_free_bytes) = saved
    src = (REPO / "kernel" / "server.py").read_text()
    assert src.count("ensure_cache_space(") >= 3, "every GPU job (sd_job + music_job) must pass the space check"
    assert "data.warning" in (REPO / "shell" / "shell.js").read_text(), "an unsaved app must tell the user why"
    return "oldest-first to cap/floor, fresh file kept, clean disk-full error, 507 JSON middleware"


@run_test("http_hw_api_laptop_setup", ["R38"])
async def t_hw(s):
    async with s.get(f"{BASE}/api/hw") as r:
        hw = await r.json()
    for k in ("wifi", "battery", "brightness", "volume", "memory_mb", "disk_gb"):
        assert k in hw, f"summary missing {k}"
    assert hw["battery"]["percent"] == 73 and hw["battery"]["charging"], hw["battery"]
    assert hw["battery"]["health_percent"] == 80
    assert hw["brightness"]["percent"] == 50, hw["brightness"]
    async def hwp(payload):
        async with s.post(f"{BASE}/api/hw", json=payload) as r:
            return await r.json()
    scan = await hwp({"device": "wifi", "op": "scan"})
    assert [n["ssid"] for n in scan["networks"]][0] == "OpenAP" and scan["networks"][0]["in_use"]
    ok = await hwp({"device": "wifi", "op": "connect", "ssid": "OpenAP", "password": "pw"})
    assert ok["ok"] is True, ok
    bad = await hwp({"device": "wifi", "op": "connect", "ssid": "OpenAP", "password": "wrong"})
    assert bad["ok"] is False and "Secrets" in bad["detail"], bad
    st = await hwp({"device": "wifi", "op": "status"})
    assert st["connected"] and st["ssid"] == "OpenAP"
    guard = await hwp({"device": "power", "op": "off"})
    assert guard["ok"] is False and "confirm" in guard["error"], "power needs confirm guard"
    bri = await hwp({"device": "brightness", "op": "set", "percent": 80})
    assert bri["ok"] and bri["percent"] == 80
    return "wifi scan/connect/status + battery 73% + brightness set + power guard all OK"


@run_test("http_display_datetime_bluetooth", ["R53", "R54", "R55", "R56"])
async def t_hw_devices(s):
    async def hwp(payload):
        async with s.post(f"{BASE}/api/hw", json=payload) as r:
            return await r.json()
    d = await hwp({"device": "display"})
    assert d["available"] and d["count"] == 2 and d["multiscreen"] is True, d
    edp = next(x for x in d["displays"] if x["output"] == "eDP-1")
    assert edp["primary"] and edp["resolution"] == "1920x1080" and "1600x900" in edp["modes"]
    # [R90] a layout change is followed LIVE by the one desktop: nothing relaunches the kiosk
    reload_flag = Path(os.environ["KAIROS_KIOSK_RELOAD"])
    reload_flag.unlink(missing_ok=True)
    r = await hwp({"device": "display", "op": "arrange", "mode": "extend"})
    assert r == {"ok": True, "relayout": "live"} and not reload_flag.exists(), r
    r = await hwp({"device": "display", "op": "set", "output": "HDMI-1",
                   "resolution": "2560x1440", "rotation": "left"})
    assert r["ok"] and not reload_flag.exists(), r
    assert [m["id"] for m in d["monitors"]] == ["eDP-1", "HDMI-1"] and d["desktop"] == {"x": 0, "y": 0, "w": 3840, "h": 1080}, d
    args = Path(os.environ["MOCK_XRANDR_LOG"]).read_text()
    assert "--mode\t2560x1440" in args and "--rotate\tleft" in args, args[-200:]
    # a single-output tweak persists the WHOLE layout (every connected output), so
    # the boot-time replay never leaves the other monitors dark [R53]
    saved = json.loads((VDIR / "cache" / "display.json").read_text())["xrandr_args"]
    assert "eDP-1" in saved and "HDMI-1" in saved and "--primary" in saved, saved
    assert saved.count("--output") == 2 and "left" not in saved, saved   # mock query: nothing rotated
    dt = await hwp({"device": "datetime"})
    assert dt["available"] and dt["timezone"] == "Europe/Berlin" and dt["ntp"] is True
    tz = await hwp({"device": "datetime", "op": "timezones"})
    assert "Asia/Tokyo" in tz["timezones"]
    bt = await hwp({"device": "bluetooth", "op": "status"})
    assert bt["available"] and bt["powered"] and "Mock Headphones" in bt["connected"][0]
    return "display list (+monitors/desktop)/arrange/set (live, no kiosk reload) · datetime tz/ntp · bluetooth status all OK"


@run_test("http_categorized_api_docs", ["R59"])
async def t_api_docs(s):
    async with s.get(f"{BASE}/api/docs") as r:
        alld = await r.text()
    assert "kAIrOS API" in alld and "vibe.fs" in alld and "categories:" in alld
    async with s.get(f"{BASE}/api/docs/display") as r:
        disp = await r.text()
    assert "vibe.hw.display.set" in disp and len(disp) < 4000, "display sheet wrong/too long"
    async with s.get(f"{BASE}/api/docs/nope") as r:
        assert r.status == 404 and "categories" in await r.text()
    sys.path.insert(0, str(REPO / "kernel"))
    import server
    picked = [n for n, _ in server.api_docs_for("an app to change display settings and resolution")]
    assert picked and picked[0] == "display", f"wrong sheet chosen: {picked}"
    picked = [n for n, _ in server.api_docs_for("connect to wifi and pair bluetooth headphones")]
    assert picked[0] == "network", f"wrong sheet chosen: {picked}"
    names = set(server.API_DOCS)
    assert {"core", "media", "display", "network", "system-settings",
            "files-storage", "power-battery", "games-apps"} <= names, names
    return f"{len(names)} short API sheets served + keyword-matched (display→display.md, wifi→network.md)"


@run_test("http_wiki_for_coding_llm", ["R39"])
async def t_wiki(s):
    async with s.get(f"{BASE}/api/wiki") as r:
        idx = await r.json()
    names = {p["name"] for p in idx["pages"]}
    for need in ("wifi-manager", "battery-power", "settings-app", "browser"):
        assert need in names, f"wiki page {need} missing"
    async with s.get(f"{BASE}/api/wiki/wifi-manager") as r:
        page = await r.text()
    assert "vibe.hw.wifi.connect" in page, "wifi recipe lacks the connect API"
    # wiki must reach the coding LLM: a wifi app generation carries the recipe
    sys.path.insert(0, str(REPO / "kernel"))
    import server
    picked = server.wiki_pages_for("a wifi manager to connect to my router")
    assert picked and picked[0][0] == "wifi-manager", f"wrong wiki pick: {picked}"
    # the browser recipe (fixes the fragile-generated-browser bug) must match too
    bpick = server.wiki_pages_for("the kAIrOS web browser with an address bar")
    assert bpick and bpick[0][0] == "browser", f"browser recipe not matched: {bpick}"
    async with s.get(f"{BASE}/api/wiki/browser") as r:
        brec = await r.text()
    assert "getElementById" in brec and ":nth-child" in brec, "browser recipe missing anti-bug guidance"
    async with s.post(f"{BASE}/api/fs", json={"op": "read",
                      "path": "/system/docs/wiki/wifi-manager.md"}) as r:
        assert (await r.json()).get("content"), "wiki not mounted on /system"
    return f"{len(names)} wiki pages served, matched to prompts, mounted read-only on /system"


@run_test("http_app_research_spec_step", ["R40", "R9"])
async def t_spec(s):
    events = await sse_collect(s, f"{BASE}/api/app",
                               {"prompt": "einstein facts app", "app_id": "test-spec"})
    deltas = "".join(d.get("text", "") for e, d in events if e == "delta")
    assert "specification" in deltas, "development step (spec) not announced in stream"
    done = [d for e, d in events if e == "done"]
    assert done, f"no app produced: {[e for e, _ in events]}"
    meta = json.loads((VDIR / "cache" / "apps" / "test-spec" / "meta.json").read_text())
    assert meta.get("spec") and "PURPOSE" in meta["spec"], "enriched spec not persisted"
    assert os.environ.get("VIBEOS_FORCE_OFFLINE"), "suite runs offline — so this proves offline research works"
    return f"offline research → OS-written spec ({len(meta['spec'])} chars) → app coded from it"


@run_test("unit_krea2_image_model", ["R34", "R42"])
async def t_krea(_):
    """2026-09-28 (docs/IMAGE-MODELS-2026-09.md §7, docs/INTEGRATION-2026-09-28.md): Krea 2 on its
    official settings — euler + simple, cfg 1, 8 steps, 1024², the Qwen-Image VAE (Krea's reference
    code; the repo's Wan 2.1 VAE is gone), the Qwen3-VL-4B encoder + Krea's prompt expander;
    image_qwen = Qwen-Image-2.1 (its own VAE, Qwen3-VL-8B, the engine's resolution-dependent
    schedule: NO --scheduler / --flow-shift); Z-Image-Turbo kept at 1024²."""
    for t in ("8gb", "16gb", "24gb", "32gb"):
        m = json.loads((REPO / "manifests" / f"tier-{t}.json").read_text())
        img = m["image"]
        assert img["name"].startswith("Muse (") and "museByStableYogi" in img["diffusion"]["file"], f"{t}: image model is not Muse"
        assert "Qwen3VL-4B" in img["text_encoder"]["file"], f"{t}: Qwen3-VL encoder missing"
        assert img["vae"]["file"] == "image/qwen_image_vae.safetensors", f"{t}: Krea's Qwen-Image VAE missing"
        g = img["gen_args"]
        assert g[g.index("--sampling-method") + 1] == "euler" and g[g.index("--scheduler") + 1] == "simple", \
            f"{t}: official Krea config (euler + simple) missing: {g}"
        assert g[g.index("--cfg-scale") + 1] == "1.0" and g[g.index("--steps") + 1] == "8", g
        assert g[g.index("-W") + 1] == "1024" and g[g.index("-H") + 1] == "1024", f"{t}: Krea 2 renders 1024², got {g}"
        assert "er_sde" not in g and "--offload-to-cpu" not in g, g
        assert int(img.get("max_pixels", 0)) >= 1048576, f"{t}: the 768² (589824) July-engine cap must be gone"
        assert img.get("expander") == "krea-neutral", f"{t}: Krea's prompt expander (the neutral rule set, R76) not declared"
        q = m.get("image_qwen")
        assert q and "qwen_image_2.1" in q["diffusion"]["file"], f"{t}: Qwen-Image-2.1 missing"
        assert "Qwen3VL-8B" in q["text_encoder"]["file"] and "qwen_image_2.1_vae" in q["vae"]["file"], f"{t}: {q}"
        qg = q["gen_args"]
        assert qg[qg.index("--steps") + 1] == "25" and qg[qg.index("--cfg-scale") + 1] == "1.0" and "euler" in qg, qg
        assert "--scheduler" not in qg and "--flow-shift" not in qg and "--offload-to-cpu" not in qg, \
            f"{t}: Qwen-Image-2.1 must use the engine's own flow schedule (no --scheduler/--flow-shift): {qg}"
        assert q.get("addon") is True and "Qwen Research" in q.get("licence", ""), f"{t}: licence note missing"
        c = m["image_classic"]
        assert "z_image_turbo" in c["diffusion"]["file"] and c["gen_args"][c["gen_args"].index("-W") + 1] == "1024", c
        ctx = m["llm"]["serve_args"]
        assert int(ctx[ctx.index("-c") + 1]) >= 16384, f"{t}: llm context < 16k"
    return "all tiers: Muse (euler/simple, 1024², Qwen VAE, expander) + Qwen-Image-2.1 (25 steps, own schedule) + Z-Image 1024² + 16k ctx"


@run_test("http_image_model_settings_and_clamp", ["R42", "R43"])
async def t_models(s):
    arglog = Path(os.environ.get("MOCK_SD_ARGLOG", VDIR / "sd-args.log"))

    async def gen(payload):
        async with s.post(f"{BASE}/api/image", json=payload) as r:
            jid = (await r.json())["job"]
        await poll_job(s, jid)
        return "\t" + arglog.read_text().splitlines()[-1] + "\t"

    async with s.get(f"{BASE}/api/models") as r:
        mi = await r.json()
    keys = {m["key"] for m in mi["models"]}
    assert {"default", "safe", "qwen", "classic", "chroma"} <= keys, f"models missing: {keys}"   # [R76] uncensored = default's alias
    q = next(m for m in mi["models"] if m["key"] == "qwen")
    assert q["defaults"]["steps"] == "25" and q["defaults"]["scheduler"] is None and q["defaults"]["sampler"] == "euler", q["defaults"]
    assert "Qwen-Image-2.1" in q["name"] and q["licence"] and q["files"] and q["download_gb"] > 15, q
    d = next(m for m in mi["models"] if m["key"] == "default")
    assert d["expander"] == "krea-neutral" and q["expander"] is None and mi["expand"]["setting"] == "auto", (d, q, mi["expand"])
    assert mi["sessions"] in ("sd-server", "sd-cli") and "image_expand" in mi["overrides"], mi
    # resolution auto-fit: 1280x720 fits the 1 MP budget of the 2026-09 manifests (snapped to 64);
    # a 2048² request must shrink into it (the wallpaper bug: 1280x720 OOMed the July engine)
    line = await gen({"prompt": "clamp test", "width": 1280, "height": 720})
    assert "\t-W\t1280\t-H\t704\t" in line, f"1280x720 not snapped/kept: {line[-160:]}"
    line = await gen({"prompt": "clamp test", "width": 2048, "height": 2048})
    assert "\t-W\t1024\t-H\t1024\t" in line, f"2048² not clamped to the budget: {line[-160:]}"
    # per-call model override
    line = await gen({"prompt": "classic test", "model": "classic"})
    assert "z_image_turbo" in line and "\t-W\t1024\t" in line, "per-call model override ignored"
    # settings: switch model + override steps
    for k, v in (("image_model", "qwen"), ("image_steps", 12)):
        async with s.post(f"{BASE}/api/settings", json={"key": k, "value": v}) as r:
            await r.json()
    line = await gen({"prompt": "qwen test"})
    assert "qwen_image_2.1-Q8_0" in line and "Qwen3VL-8B" in line and "qwen_image_2.1_vae" in line, \
        f"settings image_model=qwen not honored: {line[-200:]}"
    assert "\t--steps\t12\t" in line and "--scheduler" not in line and "--flow-shift" not in line, \
        f"steps override / Qwen-2.1 schedule wrong: {line[-200:]}"
    # reset to the good defaults
    for k, v in (("image_model", "default"), ("image_steps", None)):
        async with s.post(f"{BASE}/api/settings", json={"key": k, "value": v}) as r:
            await r.json()
    line = await gen({"prompt": "back to default"})
    assert "museByStableYogi_v35Q4Extended" in line and "\t--steps\t8\t" in line and "\t--scheduler\tsimple\t" in line \
        and "\t--sampling-method\teuler\t" in line and "qwen_image_vae" in line, "defaults not restored"
    return "model list (5 keys, expander/licence/files) + per-call/settings switching + step override + 2048²→1024² clamp"


@run_test("http_addon_models_downloadable", ["R47"])
async def t_addons(s):
    async with s.get(f"{BASE}/api/models") as r:
        mi = await r.json()
    by = {m["key"]: m for m in mi["models"]}
    assert by["default"]["addon"] is False, "default model must be baked, not an add-on"
    for k in ("qwen", "classic", "chroma"):              # [R76] uncensored = the baked default now
        assert by[k]["addon"] is True, f"{k} must be a downloadable add-on"
        assert by[k]["download_gb"] > 0, f"{k} missing download size"
    # tier presence must ignore add-ons: unit-check the manifest split
    sys.path.insert(0, str(REPO / "kernel"))
    import server
    m8 = json.loads((REPO / "manifests" / "tier-8gb.json").read_text())
    baked = {e["file"] for e in server.manifest_files(m8)}
    withaddons = {e["file"] for e in server.manifest_files(m8, include_addons=True)}
    qwen_file = "image/qwen_image_2.1-Q8_0.gguf"
    assert qwen_file in withaddons, "add-on file missing when addons included"
    assert qwen_file not in baked, "add-on files leak into the baked tier set"
    # a bad add-on key is rejected
    async with s.post(f"{BASE}/api/models/download", json={"model": "nope"}) as r:
        assert r.status == 400
    return f"default baked; {sum(1 for m in mi['models'] if m['addon'])} add-ons downloadable; tier set excludes add-ons"


def _png_bytes(w, h, rgb=(200, 40, 40)):
    """a valid RGB PNG of a flat colour — no PIL in the suite's containers"""
    import struct, zlib
    def chunk(tag, d):
        return struct.pack(">I", len(d)) + tag + d + struct.pack(">I", zlib.crc32(tag + d) & 0xffffffff)
    raw = (b"\x00" + bytes(rgb) * w) * h
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 1)) + chunk(b"IEND", b""))


def _png_size(data):
    import struct
    assert data[:8] == b"\x89PNG\r\n\x1a\n", f"not a PNG: {data[:16]!r}"
    return struct.unpack(">II", data[16:24])


def _data_url(png):
    import base64
    return "data:image/png;base64," + base64.b64encode(png).decode()


VISION_MMPROJ = "image/mmproj-Qwen3VL-4B-Instruct-Q8_0.gguf"
VISION_WEIGHTS = "image/Qwen3VL-4B-Instruct-Q4_K_M.gguf"


@run_test("unit_vision_manifest_and_argv", ["R70", "R47"])
async def t_vision_manifest(_):
    """The vision add-on rides on the image model's text encoder: in every tier the `vision`
    section is addon:true, its ONLY download is the mmproj projector, its weights are a
    reference to image.text_encoder (the same 2.5 GB Qwen3-VL file — never a second copy),
    tier presence ignores it, and the llama-server argv the kernel builds is
    -m <those weights> --mmproj <projector> with the token cap that bounds the context."""
    sys.path.insert(0, str(REPO / "kernel"))
    import server
    for t in ("8gb", "16gb", "24gb", "32gb"):
        m = json.loads((REPO / "manifests" / f"tier-{t}.json").read_text())
        v = m["vision"]
        assert v.get("addon") is True and v.get("engine") == "llama-server", f"{t}: vision must be a llama-server add-on"
        assert v["weights"] == {"shared_with": "image.text_encoder"} and "file" not in v and "url" not in v, \
            f"{t}: the weights must be a reference to the image model's encoder"
        # 2026-09-28: 8 GB keeps the Q4_K_M encoder (measured identical to Q8_0 for Krea 2), the
        # bigger tiers carry Q8_0 — the projector pairs with either quant of the same model
        weights = VISION_WEIGHTS if t == "8gb" else VISION_WEIGHTS.replace("Q4_K_M", "Q8_0")
        assert m["image"]["text_encoder"]["file"] == weights, f"{t}: the image encoder is not the expected Qwen3-VL file"
        files = server.section_files(v)
        assert [e["file"] for e in files] == [VISION_MMPROJ], f"{t}: the download set must be the projector alone, got {files}"
        assert files[0]["url"] == ("https://huggingface.co/Qwen/Qwen3-VL-4B-Instruct-GGUF/resolve/main/"
                                   "mmproj-Qwen3VL-4B-Instruct-Q8_0.gguf") and 0.4 <= files[0]["size_gb"] <= 0.5, files[0]
        assert VISION_MMPROJ not in {e["file"] for e in server.manifest_files(m)}, f"{t}: the projector leaks into the baked tier set"
        assert VISION_MMPROJ in {e["file"] for e in server.manifest_files(m, include_addons=True)}
        spec = server.vision_llm_spec(m)
        assert spec["file"] == weights, spec
        a = spec["serve_args"]
        assert a[a.index("--mmproj") + 1] == str(server.MODELS_DIR / VISION_MMPROJ) and "{mmproj}" not in a, a
        assert 256 <= int(a[a.index("--image-max-tokens") + 1]) <= 2048 and int(a[a.index("-c") + 1]) >= 4096, a
    key, spec = server.MM.llm_spec("vision")          # the same code path chat's llm/python_llm take
    assert key == "vision" and spec["file"] == VISION_WEIGHTS and "--mmproj" in spec["serve_args"]
    assert server.vision_unavailable_reason() is None, server.vision_unavailable_reason()
    return "4 tiers: vision addon, weights = image.text_encoder by reference, download = mmproj only, argv -m/--mmproj/--image-max-tokens"


@run_test("http_vision_addon_download", ["R70", "R47"])
async def t_vision_addon(s):
    """/api/models lists vision like video/music; download {"key": "vision"} fetches only the
    projector (the shared weights are the image model's); without the projector /api/vision
    is a clean 400 naming the download, never a traceback."""
    sys.path.insert(0, str(REPO / "kernel"))
    import server
    async with s.get(f"{BASE}/api/models") as r:
        vi = next(e for e in (await r.json())["extras"] if e["key"] == "vision")
    assert vi["addon"] is True and vi["present"] is True and vi["available"] is True and vi["kind"] == "vision", vi
    assert 0.4 <= vi["download_gb"] <= 0.5 and "Qwen3-VL" in vi["name"], vi
    async with s.post(f"{BASE}/api/models/download", json={"key": "vision"}) as r:      # "key" accepted
        j = await r.json(); assert r.status == 200 and j.get("already") is True, j
    # what a download would fetch: run_model_download hands download_entries the section's entries
    calls, real = [], server.download_entries
    async def rec(entries, state):
        calls.append([e["file"] for e in entries])
    server.download_entries = rec
    try:
        await server.run_model_download("vision", "vision")
    finally:
        server.download_entries = real
    assert calls == [[VISION_MMPROJ]] and server.MODEL_DL["vision"]["status"] == "done", calls
    # projector absent -> not present, not available, /api/vision 400 with the hint
    mm = VDIR / "models" / VISION_MMPROJ
    mm.unlink()
    try:
        async with s.get(f"{BASE}/api/models") as r:
            vi = next(e for e in (await r.json())["extras"] if e["key"] == "vision")
        assert vi["present"] is False and vi["available"] is False, vi
        async with s.post(f"{BASE}/api/vision", json={"image": _data_url(_png_bytes(8, 8)), "prompt": "?"}) as r:
            assert r.status == 400, r.status
            err = (await r.json())["error"]
        assert "vision add-on" in err and "vibe.models.download('vision')" in err and "Traceback" not in err, err
    finally:
        mm.touch()
    async with s.get(f"{BASE}/api/models") as r:
        vi = next(e for e in (await r.json())["extras"] if e["key"] == "vision")
    assert vi["present"] is True and vi["available"] is True
    return "extras[vision] listed; download = projector only (weights shared); projector missing -> clean 400"


@run_test("http_vision_sees_images", ["R70", "R66"])
async def t_vision_sees(s):
    """POST /api/vision end to end against the mock llama-server (it answers with the PNG it
    received): a data URL, a /files path and a /genimg picture reach the model as
    image_url content parts, fitted to <= 1280 px and never upscaled; the kernel launched
    llama-server with the shared weights + the projector; traversal / size / garbage are
    clean 4xx; "screen" is gated to loopback-or-token [R66]; the bridge + docs carry it."""
    import socket
    from urllib.parse import quote
    sys.path.insert(0, str(REPO / "kernel"))
    import server
    async def vision(image, prompt="what is this?", base=BASE, headers=None):
        async with s.post(f"{base}/api/vision", json={"image": image, "prompt": prompt}, headers=headers or {}) as r:
            return r.status, (await r.json() if r.content_type == "application/json" else {"raw": await r.text()})
    # 1. a big canvas arrives fitted (2000x1500 -> 1280x960), a small one untouched
    st, j = await vision(_data_url(_png_bytes(2000, 1500)), "what colour?")
    assert st == 200 and j["text"].startswith("mock vision: png 1280x960") and "question: what colour?" in j["text"], (st, j)
    st, j = await vision(_data_url(_png_bytes(300, 200)))
    assert st == 200 and "png 300x200" in j["text"], j
    # the launch the kernel made: the image model's encoder as -m, the projector as --mmproj
    launches = [ln for ln in (VDIR / "llama-args.log").read_text().splitlines() if "--mmproj" in ln]
    assert launches, "no llama-server launch with --mmproj logged"
    last = launches[-1]
    assert f"-m {VDIR}/models/{VISION_WEIGHTS}" in last and f"--mmproj {VDIR}/models/{VISION_MMPROJ}" in last \
        and "--image-max-tokens" in last, last
    # 2. kernel-served paths: /files/... and a rendered /genimg/... picture (mock sd-cli: 64x64)
    (VDIR / "cache" / "files" / "images" / "vision-test.png").write_bytes(_png_bytes(300, 200, (0, 90, 200)))
    st, j = await vision("/files/images/vision-test.png", "read it")
    assert st == 200 and "png 300x200" in j["text"], j
    async with s.get(f"{BASE}/genimg/{quote('vision test orb', safe='')}") as r:
        assert r.status == 200 and r.content_type == "image/png", (r.status, r.content_type)
    st, j = await vision("/genimg/vision test orb")
    assert st == 200 and "png 64x64" in j["text"], j
    # 3. traversal + shape: only what /files serves; nothing else on the disk
    tok = VDIR / "cache" / "token"
    link = VDIR / "cache" / "files" / "images" / "escape.png"
    link.unlink(missing_ok=True); link.symlink_to("/etc/hostname")
    try:
        for bad in ("/files/../token", "/files/%2e%2e/token", "/files//etc/hostname", "/etc/hostname",
                    "/files/images/escape.png", "/data/x.png", "http://example.com/a.png", "", 42, None):
            st, j = await vision(bad)
            assert st == 400 and "error" in j and "Traceback" not in json.dumps(j), (bad, st, j)
        assert tok.exists(), "the token file the traversal aimed at must exist for the test to mean anything"
    finally:
        link.unlink(missing_ok=True)
    st, j = await vision("/files/images/does-not-exist.png")
    assert st == 404 and "no such image" in j["error"], (st, j)
    st, j = await vision("/genimg/never rendered picture")
    assert st == 404, (st, j)
    # 4. size + garbage: a clean 400 (or aiohttp's 413 for a body past the 8 MB cap), never a hang
    try:
        server.vision_source("data:image/png;base64," + "A" * (server.VISION_MAX_BYTES * 4 // 3 + 400))
        raise AssertionError("oversize data URL accepted")
    except ValueError as e:
        assert "too large" in str(e)
    st, j = await vision("data:image/png;base64," + "QUFB" * (2 * 1024 * 1024))          # 8 MB body
    assert st in (400, 413), (st, j)
    st, j = await vision(_data_url(b"hello, definitely not an image"))
    assert st == 400 and "not a decodable image" in j["error"], j
    st, j = await vision("data:text/plain;base64,aGk=")
    assert st == 400 and "data:image/" in j["error"], j
    assert server.served_file("/files/images/vision-test.png") == (VDIR / "cache" / "files" / "images" / "vision-test.png").resolve()
    assert server.served_file("/files/../token") is None and server.served_file("/files/") is None
    # 5. "screen": a remote peer without the token is refused BEFORE any capture; loopback (and a
    #    token holder) get through to the capture, which in this X-less container is a clean 400
    ip = socket.gethostbyname(socket.gethostname())
    if not ip.startswith("127."):
        remote = f"http://{ip}:{BASE.rsplit(':', 1)[1]}"
        st, j = await vision("screen", base=remote)
        assert st == 403 and "X-Kairos-Token" in j["error"], (st, j)
        st, j = await vision(_data_url(_png_bytes(20, 20)), base=remote)     # other sources stay open
        assert st == 200 and "png 20x20" in j["text"], (st, j)
        async with s.get(f"{BASE}/api/token") as r:
            token = (await r.json())["token"]
        st, j = await vision("screen", base=remote, headers={"X-Kairos-Token": token})
        assert st == 400 and "cannot capture the screen" in j["error"], (st, j)
        gate = f"remote {ip}: screen 403 without token, 400 (no X) with it, data URL open"
    else:
        gate = "no non-loopback interface: remote gate not exercised here"
    st, j = await vision("screen")
    assert st == 400 and "cannot capture the screen" in j["error"] and "no X display" in j["error"], (st, j)
    st, j = await vision("screen:x")
    assert st == 400, (st, j)
    # 6. the bridge + the docs the coder model gets
    js = (REPO / "shell" / "shell.js").read_text()
    assert 'vision: (image, prompt) => call("vision", image, prompt)' in server.bridge_src() and '"/api/vision"' in js
    media = (REPO / "docs" / "api" / "media.md").read_text()
    assert "vibe.vision(" in media and "vision" in media.split("\n", 1)[0] and "screen" in media
    assert "vibe.vision(" in (REPO / "docs" / "API.md").read_text()
    assert [n for n, _ in server.api_docs_for("an app that reads the text in a screenshot")][0] == "media"
    return f"2000x1500 -> 1280x960, 300x200 kept; /files + /genimg ok; 10 bad paths 400, missing 404; {gate}; bridge + docs"


@run_test("http_vision_ffmpeg_still_images_only", ["R70", "R66"])
async def t_vision_ffmpeg_whitelist(s):
    """fit_image() runs ffmpeg as root on caller-supplied bytes. ffmpeg's playlist / script
    demuxers (hls, dash, concat …) open whatever the payload names — a local-file read or
    SSRF class if any format may be probed. The kernel must pass `-protocol_whitelist pipe`
    and a still-image `-format_whitelist`: an HLS playlist and a concat script naming a
    marker file are a clean 400 whose text never names the marker (ffmpeg's concat message
    "Impossible to open <name>" is the open attempt; with the whitelist the demuxer is never
    selected), the marker's atime does not move, a nut clip ffmpeg decodes without the
    whitelist is refused with it, and png/jpeg/gif/bmp + the screen's rawvideo path still work."""
    if not shutil.which("ffmpeg"):
        raise SkipTest("ffmpeg not installed yet (arrives with engine install)")
    import inspect, tempfile, uuid
    sys.path.insert(0, str(REPO / "kernel"))
    import server
    assert server.VISION_DEMUXERS == "png_pipe,jpeg_pipe,webp_pipe,gif,bmp_pipe"
    src = inspect.getsource(server.fit_image)
    assert '"-protocol_whitelist", "pipe"' in src and '"-format_whitelist"' in src and 'VISION_DEMUXERS' in src, src
    # a payload that names a file only ffmpeg would ever read; "SECRET" must stay unseen
    marker = Path(tempfile.mkdtemp()) / f"kairos-vision-marker-{uuid.uuid4().hex}"
    marker.write_text("SECRET-MARKER-CONTENT\n")
    hls = f"#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:10\n#EXTINF:10,\nfile://{marker}\n#EXT-X-ENDLIST\n"
    concat = f"ffconcat version 1.0\nfile file://{marker}\n"
    concat_plain = f"ffconcat version 1.0\nfile {marker}\n"
    atime0 = marker.stat().st_atime_ns

    async def vision(image):
        async with s.post(f"{BASE}/api/vision", json={"image": image, "prompt": "?"}) as r:
            return r.status, (await r.json() if r.content_type == "application/json" else {"raw": await r.text()})
    for name, payload in (("hls", hls), ("concat", concat), ("concat-plain", concat_plain)):
        st, j = await vision(_data_url(payload.encode()))              # arrives as data:image/png
        assert st == 400 and "not a decodable image" in j.get("error", ""), (name, st, j)
        assert str(marker) not in json.dumps(j) and "SECRET" not in json.dumps(j), (name, j)
        try:
            await server.fit_image(payload.encode())
            raise AssertionError(f"{name}: fit_image accepted a playlist")
        except ValueError as e:
            assert str(marker) not in str(e) and "SECRET" not in str(e), (name, str(e))
    assert marker.stat().st_atime_ns == atime0, "marker file was read (atime moved)"
    assert marker.read_text().startswith("SECRET"), "fixture"
    # the restriction bites: a nut clip ffmpeg decodes from a pipe without the whitelist
    p = await asyncio.create_subprocess_exec("ffmpeg", "-v", "error", "-i", "pipe:0", "-f", "nut", "-c:v", "rawvideo", "pipe:1",
                                             stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE)
    nut, _ = await p.communicate(_png_bytes(8, 8))
    assert p.returncode == 0 and nut, "nut fixture"
    p = await asyncio.create_subprocess_exec("ffmpeg", "-v", "error", "-i", "pipe:0", "-frames:v", "1", "-f", "image2pipe", "-c:v", "png", "pipe:1",
                                             stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE)
    out, _ = await p.communicate(nut)
    assert p.returncode == 0 and out.startswith(b"\x89PNG"), "ffmpeg without the whitelist must decode the nut clip (else the check below proves nothing)"
    try:
        await server.fit_image(nut)
        raise AssertionError("nut clip decoded through the whitelist")
    except ValueError as e:
        assert "not on whitelist" in str(e), str(e)
    # still images and the screen path keep working
    async def conv(*args):
        p = await asyncio.create_subprocess_exec("ffmpeg", "-v", "error", "-i", "pipe:0", *args, "pipe:1",
                                                 stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE)
        out, _ = await p.communicate(_png_bytes(12, 10))
        assert p.returncode == 0 and out, f"fixture {args}"
        return out
    ok = []
    for fmt, blob in (("png", _png_bytes(12, 10)), ("jpeg", await conv("-c:v", "mjpeg", "-f", "image2pipe")),
                      ("gif", await conv("-f", "gif")), ("bmp", await conv("-c:v", "bmp", "-f", "image2pipe"))):
        png = await server.fit_image(blob)
        assert _png_size(png) == (12, 10), (fmt, _png_size(png))
        ok.append(fmt)
    st, j = await vision(_data_url(await conv("-f", "gif")))
    assert st == 200 and "png 12x10" in j["text"], (st, j)
    png = await server.fit_image(bytes(4 * 3 * 2), raw=(3, 2))
    assert _png_size(png) == (3, 2), _png_size(png)
    shutil.rmtree(marker.parent, ignore_errors=True)
    return f"hls/concat -> 400, marker never named or read; nut refused ('not on whitelist'); {'/'.join(ok)} + rawvideo screen path ok"


@run_test("unit_vision_screen_capture", ["R70", "R52", "R68"])
async def t_vision_screen(_):
    """"screen" against the fake X display: the window layer grabs the root with XGetImage —
    every monitor as laid out (the bounding box) or one by its kiosk number — on the ops
    thread, and the kernel's fit turns the BGRX rows into a <= 1280 px PNG with ffmpeg;
    an unknown screen or no X is a clean WMError."""
    import tempfile
    sys.path.insert(0, str(REPO / "kernel"))
    import server
    with tempfile.TemporaryDirectory() as td:
        h = _WMHarness(td); wm = h.wm
        try:
            fx = _FakeX([(0, 0, 1024, 768, True), (1024, 0, 1280, 720, False)]); h.srv = fx
            svc = wm.WindowService()
            data, rect = await svc.screenshot(None)
            assert rect == (0, 0, 2304, 768) and len(data) == 2304 * 768 * 4, (rect, len(data))
            data1, rect1 = await svc.screenshot(1)
            assert rect1 == (1024, 0, 1280, 720) and len(data1) == 1280 * 720 * 4, rect1
            assert fx.grabbed == [(0, 0, 2304, 768), (1024, 0, 1280, 720)], fx.grabbed
            try:
                await svc.screenshot(2); raise AssertionError("screen 2 must not exist")
            except wm.WMError as e:
                assert "no screen 2" in str(e), e
            png = await server.fit_image(data, raw=(2304, 768))
            w, hh = _png_size(png); assert w == 1280 and 424 <= hh <= 428, (w, hh)
            assert _png_size(await server.fit_image(data1, raw=(1280, 720))) == (1280, 720)
            (Path(td) / "X0").unlink()
            try:
                await svc.screenshot(); raise AssertionError("no X socket must be a WMError")
            except wm.WMError as e:
                assert "no X display" in str(e), e
        finally:
            h.restore()
    return "two monitors: all = 2304x768 bbox -> 1280x427 PNG, screen:1 = 1280x720, screen:2 + no X = clean errors"


@run_test("http_install_targets", ["R48"])
async def t_install(s):
    # in the test env the installer script is absent -> available:false (graceful)
    async with s.get(f"{BASE}/api/install/targets") as r:
        info = await r.json()
    assert "available" in info
    # confirm guard: no confirm -> 400, never wipes anything
    async with s.post(f"{BASE}/api/install", json={"target": "/dev/sdz"}) as r:
        assert r.status == 400, "install without confirm must be refused"
    return f"install targets endpoint present (available={info['available']}); confirm-guard enforced"


@run_test("http_bug_report_self_repair", ["R12", "R45"])
async def t_bugreport(s):
    # structured report as the shell files it (incl. a SyntaxError + code excerpt)
    rep = {"type": "SyntaxError", "message": "Unexpected token '}'",
           "line": 42, "col": 7, "stack": "SyntaxError: Unexpected token '}'",
           "excerpt": "    41 |   count += 1;\n>>> 42 |   } }\n    43 |   render();"}
    async with s.post(f"{BASE}/api/app/test-counter/repair", json={"error": rep}) as r:
        assert r.status == 200, await r.text()
        healed = await r.json()
    assert "<!DOCTYPE" in healed["html"], "repair did not return a full document"
    # kernel must format a real bug report (not just str() the dict)
    sys.path.insert(0, str(REPO / "kernel"))
    import server
    txt = server.format_bug_report(rep, "test-counter", "test counter app", 2)
    for need in ("BUG REPORT", "SyntaxError", "line 42", "Stack trace", ">>> 42"):
        assert need in txt, f"bug report missing: {need}"
    # [D4] the bridge's list of invented vibe.* members reaches the repair prompt
    txt2 = server.format_bug_report({**rep, "unknown_api": ["vibe.hw.haptics is not part of the kAIrOS API — vibe.hw has: get, wifi"]},
                                    "test-counter", "p", 2)
    assert "do NOT exist" in txt2 and "vibe.hw.haptics is not part of the kAIrOS API" in txt2, txt2
    assert "do NOT exist" not in txt
    # legacy plain-string errors keep working
    async with s.post(f"{BASE}/api/app/test-counter/repair",
                      json={"error": "boom @line 3"}) as r:
        assert r.status == 200
    return "SyntaxError bug report (type/loc/stack/excerpt) formatted + LLM rework OK"


@run_test("http_wallpaper_paints_the_session", ["R44"])
async def t_wallpaper(s):
    # by now the suite has logged plenty of user input (apps, images, searches…)
    kept_sets = []
    for _ in range(3):
        async with s.post(f"{BASE}/api/wallpaper") as r:
            w = await r.json()
        assert w.get("job") and "scene" in w and isinstance(w.get("kept"), list)
        assert w["kept"], "no session input was logged/kept"
        res = await poll_job(s, w["job"])
        assert res["result"].startswith("/files/images/")
        kept_sets.append(tuple(sorted(w["kept"])))
    assert len(set(kept_sets)) > 1, f"keep/discard selection is not random: {kept_sets}"
    return (f"3 session-painted wallpapers; random keep/discard differs per run "
            f"(e.g. {len(kept_sets[0])} fragments kept)")


@run_test("http_remote_needs_token", ["R66"])
async def t_remote_token(s):
    """The suite's kernel runs EXPOSED (VIBEOS_HOST=0.0.0.0, the testrig shape); this
    container's eth0 address plays the remote attacker, 127.0.0.1 is the kiosk path.
    Remote: reads + generation open, anything that changes the machine 403 without
    the boot token, 200 with it. Kiosk (loopback): works with no token at all."""
    import socket, stat
    ip = socket.gethostbyname(socket.gethostname())
    if ip.startswith("127."):
        raise SkipTest("no non-loopback interface to play the remote peer")
    port = BASE.rsplit(":", 1)[1]
    REMOTE = f"http://{ip}:{port}"
    tok_file = Path("/run/kairos/token") if Path("/run/kairos").is_dir() else VDIR / "cache" / "token"
    async def call(_base, _method, _path, _hdr=None, **json):     # bodies carry "path" themselves
        async with s.request(_method, f"{_base}{_path}", json=json or None, headers=_hdr or {}) as r:
            return r.status, (await r.json() if r.content_type == "application/json" else {})
    # 1. read-only + monitoring stays open remotely
    for m, path, body in (("GET", "/api/status", {}), ("GET", "/api/hw", {}), ("GET", "/api/models", {}),
                          ("GET", "/api/install/status", {}), ("POST", "/api/hw", {"device": "battery"}),
                          ("POST", "/api/hw", {"device": "wifi", "op": "status"}),
                          ("POST", "/api/fs", {"op": "list", "path": "/"})):
        st, j = await call(REMOTE, m, path, **body)
        assert st == 200 and not j.get("error"), f"remote {m} {path} must stay open: {st} {j}"
    # 2. every machine-changing route refuses a tokenless remote peer BEFORE doing anything
    denied = (("/api/install", {"target": "/dev/sdz", "confirm": True}),
              ("/api/hw", {"device": "power", "op": "off", "confirm": True}),
              ("/api/hw", {"device": "wifi", "op": "forget", "ssid": "OpenAP"}),
              ("/api/hw", {"device": "brightness", "op": "set", "percent": 1}),
              ("/api/fs", {"op": "format", "path": "/data"}),
              ("/api/fs", {"op": "write", "path": "/data/evil.txt", "content": "x"}),
              ("/api/settings", {"key": "os_name", "value": "pwned"}),
              ("/api/models/download", {"model": "qwen"}),
              ("/api/setup/upgrade", {"tier": "16gb"}), ("/api/setup/done", {}),
              ("/api/store", {"key": "k", "value": "v"}), ("/api/wallpaper", {}),
              ("/api/game", {"prompt": "pong"}))
    for path, body in denied:
        st, j = await call(REMOTE, "POST", path, **body)
        assert st == 403 and "X-Kairos-Token" in j.get("error", ""), f"remote {path} {body}: {st} {j}"
    st, _ = await call(REMOTE, "GET", "/api/token")
    assert st == 403, "the token must never be handed to a remote peer"
    st, j = await call(REMOTE, "POST", "/api/fs", {"X-Kairos-Token": "0" * 64},
                       op="write", path="/data/evil.txt", content="x")
    assert st == 403, "a wrong token must not authorize"
    # 3. the token: loopback-only endpoint, agrees with the 0640 file the kernel wrote
    st, j = await call(BASE, "GET", "/api/token")
    assert st == 200 and len(j["token"]) == 64, j
    assert tok_file.read_text().strip() == j["token"], f"{tok_file} differs from /api/token"
    assert stat.S_IMODE(tok_file.stat().st_mode) == 0o640, oct(tok_file.stat().st_mode)
    hdr = {"X-Kairos-Token": j["token"]}
    # 4. with the token a remote operator may change the machine …
    st, w = await call(REMOTE, "POST", "/api/fs", hdr, op="write", path="/data/r66/remote.txt", content="ok")
    assert st == 200 and w.get("entry"), w
    st, j2 = await call(REMOTE, "POST", "/api/settings", hdr, key="accent2", value="#123456")
    assert st == 200 and j2["accent2"] == "#123456", j2
    # 5. … and the kiosk path (loopback, no token) keeps working unchanged
    st, rd = await call(BASE, "POST", "/api/fs", op="read", path="/data/r66/remote.txt")
    assert st == 200 and rd["content"] == "ok"
    st, d = await call(BASE, "POST", "/api/fs", op="delete", path="/data/r66")
    assert st == 200 and d.get("ok"), d
    st, j3 = await call(BASE, "POST", "/api/settings", key="accent2", value="#41e2ff")
    assert st == 200 and j3["accent2"] == "#41e2ff"
    st, g = await call(BASE, "POST", "/api/hw", device="power", op="off")   # confirm guard still first
    assert st == 200 and g["ok"] is False and "confirm" in g["error"], g
    return (f"remote {ip}: {len(denied)} machine-changing calls 403, reads open, wrong token 403; "
            f"token 0640 + loopback-only; with token 200; kiosk path token-free")


@run_test("unit_network_hardening_static", ["R66"])
async def t_network_hardening(_):
    """Static: the kernel defaults to loopback, exposure is opt-in (never active in
    kairos.service), sshd is key-only, no WiFi PSK is in the tree. Live: a second kernel
    booted WITHOUT VIBEOS_HOST answers on 127.0.0.1 and refuses the eth0 address."""
    import re, socket, subprocess, tempfile, urllib.request
    iso = REPO / "iso"; inc = iso / "config" / "includes.chroot"
    src = (REPO / "kernel" / "server.py").read_text()
    assert 'os.environ.get("VIBEOS_HOST", "127.0.0.1")' in src and "host=BIND_HOST" in src
    assert 'host="0.0.0.0"' not in src, "hard-coded 0.0.0.0 bind is back"
    unit = (inc / "etc" / "systemd" / "system" / "kairos.service").read_text()
    assert not re.search(r"^Environment=VIBEOS_HOST", unit, re.M), "the appliance unit must not expose the API"
    assert "VIBEOS_HOST=0.0.0.0" in unit, "the opt-in must be documented in the unit"
    hook = (iso / "config" / "hooks" / "normal" / "0200-kairos-system.hook.chroot").read_text()
    assert "PermitRootLogin prohibit-password" in hook and "PasswordAuthentication no" in hook
    assert "PermitRootLogin yes" not in hook and "PasswordAuthentication yes" not in hook
    assert "authorized_keys" in hook and "sshd_config.d/kairos.conf" in hook
    nmc = list(inc.rglob("*.nmconnection"))
    assert not nmc, f"WiFi profile with a PSK baked into the tree: {nmc}"
    leaks = [p for p in iso.rglob("*") if p.is_file() and p.suffix != ".img"
             and "psk=" in p.read_text(errors="ignore") and "secrets" not in p.parts and p.name != "build-iso.sh"
             and ".bak-" not in p.name]          # a *.bak-<date> copy of build-iso.sh is never built into the image
    assert not leaks, f"psk= in {leaks}"
    biso = (iso / "build-iso.sh").read_text()
    assert "/authorized_keys" in biso and "wifi.env" in biso and "KAIROS_EXPOSE_UI" in biso
    assert (iso / "secrets" / ".gitignore").exists() and (iso / "secrets" / "wifi.env.example").exists()
    # classification helper: read ops vs machine-changing ops on POST /api/hw
    sys.path.insert(0, str(REPO / "kernel")); import server
    assert server.hw_op_is_read({"device": "wifi", "op": "scan"}) and server.hw_op_is_read({"device": "battery"})
    assert server.hw_op_is_read({"device": "datetime", "op": "timezones"}) and server.hw_op_is_read({})
    for b in ({"device": "power", "op": "off"}, {"device": "wifi", "op": "connect"},
              {"device": "display", "op": "arrange"}, {"device": "bluetooth", "op": "pair"},
              {"device": "volume", "op": "set"}, {"device": "sound", "op": "set"}):
        assert not server.hw_op_is_read(b), b
    # shell: sends the token header on machine-changing calls, never a bare JSON POST
    js = (REPO / "shell" / "shell.js").read_text()
    assert 'headers: { "Content-Type": "application/json" }' not in js, "shell POST without apiHeaders()"
    assert "/api/token" in js and "X-Kairos-Token" in js
    # live: the DEFAULT bind is loopback (fresh kernel, VIBEOS_HOST unset, own dir + port)
    ip = socket.gethostbyname(socket.gethostname())
    port = 1339
    env = {k: v for k, v in os.environ.items() if k != "VIBEOS_HOST"}
    tmp = tempfile.mkdtemp(prefix="kairos-r66-")
    env.update(VIBEOS_DIR=tmp, VIBEOS_PORT=str(port), VIBEOS_TOKEN_FILE=f"{tmp}/token")
    proc = subprocess.Popen([sys.executable, str(REPO / "kernel" / "server.py")], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        for _ in range(60):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=1).read(); break
            except Exception:
                assert proc.poll() is None, proc.stdout.read().decode(errors="ignore")[-800:]
                await asyncio.sleep(0.5)
        else:
            raise AssertionError("second kernel never answered on 127.0.0.1")
        if not ip.startswith("127."):
            try:
                urllib.request.urlopen(f"http://{ip}:{port}/api/status", timeout=2).read()
                raise AssertionError(f"default bind reachable on {ip}:{port} — must be loopback only")
            except urllib.error.URLError as e:
                assert isinstance(e.reason, (ConnectionRefusedError, OSError)), e
        assert Path(tmp, "token").exists()
    finally:
        proc.kill(); proc.wait()
        shutil.rmtree(tmp, ignore_errors=True)
    return (f"default bind refuses {ip}, answers 127.0.0.1; unit exposure opt-in only; sshd key-only; "
            f"no PSK in tree; shell sends the header")


# ---------------------------------------------------------------------------
# boot-path fixes from the 2026-09-10 VM test (docs/VM-TEST-2026-09-10.md, D1-D13)
# ---------------------------------------------------------------------------

def _shipped_systemd_units():
    """Every unit + drop-in the image ends up with, keyed by unit-relative path:
    the files under includes.chroot AND the heredoc-written ones in build-img.sh /
    patch-img.sh / the chroot hooks (quoted or unquoted delimiters, $MNT prefixes)."""
    import re
    inc = REPO / "iso" / "config" / "includes.chroot" / "etc" / "systemd" / "system"
    units = {}
    for p in sorted(inc.rglob("*")):
        if p.is_file() and (p.suffix in (".service", ".path", ".target", ".mount", ".timer", ".socket") or p.parent.suffix == ".d"):
            units[str(p.relative_to(inc))] = p.read_text()
    for src in ("iso/build-img.sh", "iso/patch-img.sh",
                "iso/config/hooks/normal/0200-kairos-system.hook.chroot", "iso/config/hooks/normal/0100-nvidia-cuda.hook.chroot"):
        t = (REPO / src).read_text()
        for m in re.finditer(r"cat > \"?(?:\$MNT)?(/etc/systemd/system/[^\s\"]+)\"? <<('?)(\w+)'?\n(.*?)\n\3\n", t, re.S):
            body = m.group(4) if m.group(2) else m.group(4).replace("\\$", "$")   # unquoted heredoc: \$ -> $
            units[f"{Path(src).name}:{m.group(1)[len('/etc/systemd/system/'):]}"] = body
    return units


def _parse_unit_deps(text):
    sec, d = None, {"after": [], "before": [], "wants": [], "requires": [], "wantedby": [], "requiredby": [], "dd": True}
    keys = {"After": "after", "Before": "before", "Wants": "wants", "Requires": "requires", "Requisite": "requires",
            "BindsTo": "requires", "WantedBy": "wantedby", "RequiredBy": "requiredby"}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("["):
            sec = line.strip("[]"); continue
        if "=" not in line or sec not in ("Unit", "Install"):
            continue
        k, v = (x.strip() for x in line.split("=", 1))
        if k == "DefaultDependencies":
            d["dd"] = v.lower() in ("yes", "true", "on", "1")
        elif k in keys:
            d[keys[k]] += v.split()
    return d


# the stock Debian units the repo's units order against (from the upstream unit files;
# getty@.service: Before=getty.target; multi-user Wants getty.target; targets have
# default deps, local-fs/network targets do not)
_STOCK_UNITS = {
    "sysinit.target": {"after": ["local-fs.target", "swap.target"], "wants": ["local-fs.target", "swap.target"]},
    "basic.target": {"after": ["sysinit.target", "sockets.target", "paths.target", "slices.target"], "requires": ["sysinit.target"]},
    "multi-user.target": {"after": ["basic.target"], "requires": ["basic.target"], "wants": ["getty.target"]},
    "graphical.target": {"after": ["multi-user.target", "display-manager.service"], "requires": ["multi-user.target"]},
    "getty.target": {"wants": ["getty@tty1.service"]},
    "getty@tty1.service": {"after": ["systemd-user-sessions.service", "plymouth-quit-wait.service", "getty-pre.target", "rc-local.service"],
                           "before": ["getty.target", "rescue.service"]},
    "systemd-user-sessions.service": {"after": ["remote-fs.target", "nss-user-lookup.target", "network.target"]},
    "ssh.service": {"after": ["network.target", "auditd.service"]},
    "local-fs.target": {"after": ["local-fs-pre.target"], "dd": False},
    "network.target": {"dd": False}, "network-online.target": {"after": ["network.target"], "dd": False},
    "systemd-udev-trigger.service": {"before": ["sysinit.target"], "dd": False},
    "boot-efi.mount": {"after": ["local-fs-pre.target"], "dd": False},
    "shutdown.target": {"dd": False}, "emergency.target": {"dd": False}, "rescue.target": {"dd": False},
    "emergency.service": {"dd": False}, "rescue.service": {"dd": False},
}


def _systemd_order_graph(units):
    """{unit: parsed deps} -> {unit: set(units it must start AFTER)} with systemd's
    implicit rules: default-dependency services start after basic.target; a target
    with default deps is ordered after every unit it wants/requires that also has
    default deps, unless that unit is explicitly After= the target (systemd's own
    "don't create loops" guard in unit_add_default_target_dependency)."""
    g = {}
    def add(a, b):
        g.setdefault(a, set()).add(b); g.setdefault(b, set())
    def dd(n):
        u = units.get(n); return True if u is None else u["dd"]
    for n, u in units.items():
        g.setdefault(n, set())
        for x in u["after"]:
            add(n, x)
        for x in u["before"]:
            add(x, n)
        if u["dd"] and n.endswith(".service"):
            add(n, "basic.target"); add(n, "sysinit.target")
        elif u["dd"] and n.endswith((".path", ".timer", ".socket")):
            add(n, "sysinit.target")
    wanted = {}                                            # target -> units it pulls in
    for n, u in units.items():
        if n.endswith(".target"):
            for x in u["wants"] + u["requires"]:
                wanted.setdefault(n, set()).add(x)
        for t in u["wantedby"] + u["requiredby"]:
            wanted.setdefault(t, set()).add(n)
    for t, us in wanted.items():
        if not t.endswith(".target") or not dd(t):
            continue
        for x in us:
            if dd(x) and t not in units.get(x, {"after": []})["after"]:
                add(t, x)
    return g


def _find_order_cycle(g):
    color, stack = {n: 0 for n in g}, []
    def dfs(n):
        color[n] = 1; stack.append(n)
        for m in g.get(n, ()):
            if color.get(m, 0) == 1:
                return stack[stack.index(m):] + [m]
            if color.get(m, 0) == 0:
                c = dfs(m)
                if c:
                    return c
        stack.pop(); color[n] = 2
        return None
    for n in list(g):
        if color[n] == 0:
            c = dfs(n)
            if c:
                return c
    return None


@run_test("unit_systemd_ordering_acyclic", ["R63", "R28"])
async def t_ordering_acyclic(_):
    """D7: the shipped + generated units must form an acyclic start order. nvidia-load
    was After=multi-user.target AND Before=getty@tty1.service (which multi-user contains
    via getty.target): systemd broke the cycle by deleting a random job — on one boot in
    six getty@tty1/start, i.e. no login, no X, black screen, `systemctl --failed` empty.
    Models the order graph (explicit After/Before, default-dependency anchors, the
    implicit target->wanted-unit ordering with systemd's own loop guard) over every
    repo unit + drop-in and the stock units they touch, and fails on ANY cycle; plus
    the literal pattern check the VM test asked for."""
    import re
    raw = _shipped_systemd_units()
    assert len(raw) >= 12 and any(k.startswith("build-img.sh:") for k in raw), sorted(raw)
    units = {}                                             # unit name -> merged deps (drop-ins merged in)
    for key, text in raw.items():
        rel = key.split(":", 1)[1] if ":" in key else key
        name = rel.split("/")[0][:-2] if rel.split("/")[0].endswith(".d") else rel
        d = _parse_unit_deps(text)
        if name in units:
            for k in ("after", "before", "wants", "requires", "wantedby", "requiredby"):
                units[name][k] += d[k]
            if rel.split("/")[0].endswith(".d") and "DefaultDependencies" not in text:
                continue
            units[name]["dd"] = d["dd"]
        else:
            units[name] = d
    for n, s in _STOCK_UNITS.items():
        base = {"after": [], "before": [], "wants": [], "requires": [], "wantedby": [], "requiredby": [], "dd": True}
        base.update(s)
        if n in units:                                     # a drop-in on a stock unit adds to it
            for k in ("after", "before", "wants", "requires"):
                base[k] = base[k] + units[n][k]
        units[n] = base
    cyc = _find_order_cycle(_systemd_order_graph(units))
    assert cyc is None, "systemd ordering cycle (a random job of it gets deleted at boot): " + " -> ".join(cyc)
    # the detector must actually fire: the unit that produced D7, and the same unit fixed
    bad = dict(units); bad["nvidia-load.service"] = {"after": ["multi-user.target"], "before": ["getty@tty1.service"], "wants": [],
                                                     "requires": [], "wantedby": ["multi-user.target"], "requiredby": [], "dd": True}
    c = _find_order_cycle(_systemd_order_graph(bad))
    assert c and "getty@tty1.service" in c and "multi-user.target" in c, f"detector missed the D7 cycle: {c}"
    good = dict(units); good["nvidia-load.service"] = dict(bad["nvidia-load.service"], after=["basic.target"])
    assert _find_order_cycle(_systemd_order_graph(good)) is None
    # literal pattern (VM test): a default-dependency unit both After= a target and
    # WantedBy= that target (or one inside it) is one Before= away from a cycle — only
    # DefaultDependencies=no units may say "after the target that pulls me in"
    # (kairos-bootlog: no implicit target ordering exists for them); and nothing that is
    # Before= getty may be After= anything whose start order contains getty.
    late = {"multi-user.target", "graphical.target"}
    for n, u in units.items():
        if n in _STOCK_UNITS:
            continue
        if u["dd"] and (set(u["after"]) & late) and (set(u["wantedby"]) & late):
            raise AssertionError(f"{n}: After={set(u['after']) & late} with WantedBy={set(u['wantedby']) & late} and default "
                                 f"dependencies — order it after basic.target (or DefaultDependencies=no)")
    g = _systemd_order_graph(units)
    def reaches(a, b, seen=None):
        seen = seen or set()
        return a == b or any(m not in seen and not seen.add(m) and reaches(m, b, seen) for m in g.get(a, ()))
    for n, u in units.items():
        if n in _STOCK_UNITS or not ({"getty@tty1.service", "getty.target"} & set(u["before"])):
            continue
        for x in u["after"]:
            assert not reaches(x, "getty@tty1.service"), f"{n}: Before=getty@tty1 but After={x}, whose start order contains getty"
    nl = units["nvidia-load.service"]
    assert nl["dd"] and "basic.target" in nl["after"] and "getty@tty1.service" in nl["before"] and "multi-user.target" not in nl["after"]
    sp = units["kairos-splash.service"]
    assert not sp["dd"] and "local-fs.target" in sp["after"] and "shutdown.target" in sp["before"] and "getty@tty1.service" not in sp["before"]
    return f"{len(units)} units modelled (incl. {sum(1 for k in raw if ':' in k)} heredoc/drop-in), no cycle; detector proven on the D7 unit"


@run_test("unit_loader_default_single_entry", ["R28", "R48"])
async def t_loader_default(_):
    """D4: the appliance booted the Debian Boot-Loader-Spec entry kernel-install wrote,
    not kairos.conf (`default kairos` matches no entry ID — IDs include .conf — and the
    foreign entry sorted first): the kAIrOS cmdline never took effect. Now: default
    names the entry, only kAIrOS entries exist (asserted by the build and the
    installer), kernel-install is off (layout=other, shipped + installed before the
    chroot's update-initramfs) and kernel upgrades refresh the entry's kernel/initrd
    through /etc/kernel + /etc/initramfs hooks (hermetically exercised)."""
    import re, subprocess, tempfile
    iso = REPO / "iso"; inc = iso / "config" / "includes.chroot"
    b = (iso / "build-img.sh").read_text(); pim = (iso / "patch-img.sh").read_text()
    ki = (inc / "usr" / "local" / "sbin" / "kairos-install").read_text()
    hook = (iso / "config" / "hooks" / "normal" / "0200-kairos-system.hook.chroot").read_text()
    for name, t in (("build-img.sh", b), ("kairos-install", ki)):
        m = re.search(r'loader/loader\.conf" <<(\w+)\n(.*?)\n\1\n', t, re.S); assert m, f"{name}: no loader.conf heredoc"
        assert re.search(r"^default kairos\.conf$", m.group(2), re.M), f"{name}: loader.conf must say `default kairos.conf`: {m.group(2)}"
        assert "timeout 0" in m.group(2) and "editor no" in m.group(2), f"{name}: hidden menu, no editor [R28]"
        assert not re.search(r"^default kairos$", t, re.M), f"{name}: the old non-matching default is back"
    assert "default kairos.conf" in pim and re.search(r"^default kairos$", pim, re.M) is None
    # only the kAIrOS entries: foreign ones + kernel-install's <machine-id>/ tree removed, then asserted
    for name, t in (("build-img.sh", b), ("patch-img.sh", pim)):
        assert "kairos.conf|kairos-serial.conf) ;;" in t and "removing foreign loader entry" in t, name
        assert "^[0-9a-f]{32}$" in t and "removing kernel-install tree" in t, f"{name}: the <machine-id>/ kernel tree must go too"
        assert '[ "$DEF" = kairos.conf ]' in t, f"{name}: must assert the default names an existing entry"
    assert "expected exactly kairos.conf + kairos-serial.conf" in b, "build must assert exactly the two kAIrOS entries"
    written = set(re.findall(r'loader/entries/(kairos[\w-]*\.conf)" <<', b)); assert written == {"kairos.conf", "kairos-serial.conf"}, written
    assert "expected exactly kairos.conf" in ki and 'grep -q \'^default kairos.conf$\'' in ki, "installer: exactly one entry, it is the default"
    assert ki.index("expected exactly kairos.conf") < ki.index('log "DONE'), "installer must assert before DONE"
    # kernel-install never writes entries: layout=other shipped and installed BEFORE the chroot's update-initramfs
    conf = (inc / "etc" / "kernel" / "install.conf").read_text(); assert re.search(r"^layout=other$", conf, re.M)
    for name, t in (("build-img.sh", b), ("patch-img.sh", pim)):
        i = t.index("etc/kernel/install.conf")
        assert i < t.index("<<'CHROOT'") < t.index("update-initramfs -u -k all"), f"{name}: install.conf must be in place before update-initramfs runs"
    assert "grep -q '^layout=other'" in b and "layout=other" in ki, "build asserts it; installer ensures it on the target"
    # kernel upgrades: both hooks present + executable + wired; the hook restores exec bits
    for h in (inc / "etc" / "kernel" / "postinst.d" / "zz-kairos-esp", inc / "etc" / "initramfs" / "post-update.d" / "zz-kairos-esp"):
        assert h.exists() and os.access(h, os.X_OK) and "kairos-esp-sync" in h.read_text() and h.read_text().rstrip().endswith("exit 0"), h
        subprocess.run(["sh", "-n", str(h)], check=True)
    assert "zz-kairos-esp" in hook and "kairos-esp-sync" in hook and "zz-kairos-esp" in b and "zz-kairos-esp" in pim
    es = inc / "usr" / "local" / "sbin" / "kairos-esp-sync"
    assert os.access(es, os.X_OK); subprocess.run(["sh", "-n", str(es)], check=True)
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); esp = t / "esp"; (esp / "loader" / "entries").mkdir(parents=True); (esp / "kairos").mkdir()
        (esp / "kairos" / "vmlinuz").write_text("old"); (esp / "kairos" / "initrd").write_text("old")
        boot = t / "boot"; boot.mkdir(); mods = t / "mods"
        for v in ("6.12.90+deb13.1-amd64", "6.12.99+deb13.2-amd64"):
            (mods / v).mkdir(parents=True)
        (boot / "vmlinuz-6.12.90+deb13.1-amd64").write_text("k90"); (boot / "initrd.img-6.12.90+deb13.1-amd64").write_text("i90")
        (boot / "vmlinuz-6.12.99+deb13.2-amd64").write_text("k99")
        env = dict(os.environ, KAIROS_ESP=str(esp), KAIROS_BOOT=str(boot), KAIROS_MODULES=str(mods))
        def run():
            r = subprocess.run([str(es), "6.12.99+deb13.2-amd64"], env=env, capture_output=True, text=True, timeout=30)
            assert r.returncode == 0 and r.stderr == "", r
            return (esp / "kairos" / "vmlinuz").read_text(), (esp / "kairos" / "initrd").read_text()
        assert run() == ("old", "old"), "no kAIrOS entry on the ESP -> not our layout -> untouched"
        (esp / "loader" / "entries" / "kairos.conf").write_text("title kAIrOS\n")
        assert run() == ("k90", "i90"), "newest COMPLETE kernel (the .99 initrd is not built yet)"
        (boot / "initrd.img-6.12.99+deb13.2-amd64").write_text("i99")
        assert run() == ("k99", "i99") and run() == ("k99", "i99"), "post-update hook refreshes; idempotent"
        assert not list((esp / "kairos").glob("*.new")), "temp copy left behind"
    # the rig's guest sync (outside this repo, checked out beside it) must mirror the loader fix
    sg = REPO.parent.parent / "pod" / "testrig" / "sync-guest.sh"
    mirrored = "not checked (pod/ not beside the repo)"
    if sg.exists():
        t = sg.read_text()
        assert "default kairos.conf" in t and "removing foreign loader entry" in t and "kairos-esp-sync" in t, "sync-guest.sh must mirror the loader fix"
        mirrored = "sync-guest.sh mirrors it"
    return f"default kairos.conf everywhere, exactly the kAIrOS entries asserted, layout=other before update-initramfs, esp-sync hooks work; {mirrored}"


@run_test("unit_image_fonts_emoji", ["R13", "R60"])
async def t_fonts(_):
    """D6: the dock and the Apps menu are emoji; the image shipped no emoji font, so
    10 of 12 dock icons were tofu on the real desktop."""
    import re
    pkgs = (REPO / "iso" / "config" / "package-lists" / "kairos.list.chroot").read_text().split()
    for p in ("fonts-noto-color-emoji", "fonts-noto-core"):
        assert p in pkgs, f"{p} missing from the image package list"
    html = (REPO / "shell" / "index.html").read_text()
    dock = html.split('id="dock"')[1].split("</div>")[0]
    icons = re.findall(r'>([^<\s]{1,3})<span>', dock)
    emoji = [i for i in icons if any(ord(ch) >= 0x2600 for ch in i)]
    assert len(emoji) >= 8, f"the dock is emoji ({emoji}) — if that changes, revisit the fonts"
    css = (REPO / "shell" / "shell.css").read_text()
    fam = [f.strip().strip('"') for f in re.search(r"^html, body \{[^}]*font-family:\s*([^;]+);", css, re.M | re.S).group(1).split(",")]
    assert fam[-1] == "Noto Color Emoji" and "sans-serif" in fam[:-1], f"body font stack must END with Noto Color Emoji (digits!): {fam}"
    assert "Noto Color Emoji" in (REPO / "shell" / "boot-wait.html").read_text()
    return f"{len(emoji)} emoji dock icons; fonts-noto-color-emoji + fonts-noto-core in the image; CSS stack ends with the emoji font"


@run_test("unit_nvidia_persistenced_conditional", ["R63", "R41"])
async def t_persistenced(_):
    """D11: the driver package enables nvidia-persistenced unconditionally — it failed
    on every machine without an NVIDIA GPU (R63 broken on the GPU-off boot) and was the
    slowest unit of a GPU boot. The builds disable it; kairos-gpu-setup (nvidia-load,
    from the repo tree, not a heredoc copy) starts it only once a device is bound, off
    the critical chain. Hermetic run of the script against a fake sysfs + tools."""
    import re, subprocess, tempfile
    iso = REPO / "iso"; inc = iso / "config" / "includes.chroot"
    b = (iso / "build-img.sh").read_text(); pim = (iso / "patch-img.sh").read_text()
    h1 = (iso / "config" / "hooks" / "normal" / "0100-nvidia-cuda.hook.chroot").read_text()
    for name, t in (("build-img.sh", b), ("patch-img.sh", pim), ("0100 hook", h1)):
        assert re.search(r"^systemctl disable nvidia-persistenced\.service", t, re.M), f"{name} must disable nvidia-persistenced"
        assert "multi-user.target.wants/nvidia-persistenced.service" in t, f"{name}: remove the wants link the package left"
    assert "exit 98" in b and b.index("nvidia-persistenced.service\" ]") > b.index("CHROOT\n\n"), "build asserts it after the chroot"
    for p in [*inc.rglob("*"), iso / "build-img.sh", iso / "patch-img.sh", *(iso / "config" / "hooks" / "normal").glob("*")]:
        if p.is_file():
            assert not re.search(r"enable [^\n]*nvidia-persistenced", p.read_text(errors="ignore")), f"{p} enables persistenced"
    gs = inc / "usr" / "local" / "sbin" / "kairos-gpu-setup"; t = gs.read_text()
    assert os.access(gs, os.X_OK) and t.index("modprobe nvidia 2>/dev/null || return 0") < t.index("nvidia-persistenced")
    assert "/bus/pci/drivers/nvidia/0000:*" in t and "systemctl start --no-block nvidia-persistenced.service" in t
    unit = (inc / "etc" / "systemd" / "system" / "nvidia-load.service").read_text()
    assert "ExecStart=-/usr/local/sbin/kairos-gpu-setup" in unit and re.search(r"^TimeoutStartSec=\d+", unit, re.M), "oneshot needs a start timeout"
    for name, tt in (("build-img.sh", b), ("patch-img.sh", pim)):
        assert "INC/usr/local/sbin/kairos-gpu-setup" in tt and "INC/etc/systemd/system/nvidia-load.service" in tt, f"{name}: install both from the repo"
        assert "systemctl enable nvidia-load.service" in tt and "cat > /etc/systemd/system/nvidia-load.service" not in tt, name
    assert "multi-user.target.wants/nvidia-load.service" in b
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); (t / "bin").mkdir(); log = t / "calls"
        for tool in ("modprobe", "systemctl"):
            f = t / "bin" / tool
            f.write_text(f'#!/bin/sh\necho "{tool} $*" >> "{log}"\n[ "{tool}" = modprobe ] && [ "${{KAIROS_TEST_MODPROBE_FAIL:-0}}" = 1 ] && exit 1\nexit 0\n')
            f.chmod(0o755)
        sysd = t / "sys"; dev = sysd / "bus" / "pci" / "devices" / "0000:01:00.0"; dev.mkdir(parents=True)
        (dev / "boot_vga").write_text("1\n"); (dev / "vendor").write_text("0x10de\n")
        env = dict(os.environ, PATH=f"{t / 'bin'}:/usr/bin:/bin", KAIROS_SYS=str(sysd), KAIROS_GPU_SETUP_FOREGROUND="1")
        def run(**kw):
            log.unlink(missing_ok=True)
            r = subprocess.run(["sh", str(gs)], env=dict(env, **kw), capture_output=True, text=True, timeout=30)
            assert r.returncode == 0 and r.stderr == "", r
            return log.read_text() if log.exists() else ""
        calls = run(KAIROS_TEST_MODPROBE_FAIL="1")                   # no NVIDIA module: leave, persistenced untouched
        assert calls.strip() == "modprobe nvidia", calls
        calls = run()                                                # module ok, NVIDIA is boot_vga, nothing bound yet
        assert "modprobe nvidia_drm modeset=1" in calls and "modprobe nvidia_modeset" in calls and "systemctl" not in calls, calls
        (sysd / "bus" / "pci" / "drivers" / "nvidia" / "0000:01:00.0").mkdir(parents=True)
        calls = run()                                                # device bound -> persistenced, off the critical chain
        assert "systemctl start --no-block nvidia-persistenced.service" in calls, calls
        (dev / "vendor").write_text("0x1002\n")                       # AMD is boot_vga -> NVIDIA compute-only
        calls = run()
        assert "nvidia_drm" not in calls and "modprobe nvidia_uvm" in calls and "systemctl start" in calls, calls
    return "persistenced disabled by build-img/patch-img/0100 hook, started by kairos-gpu-setup only with a bound device (4 sysfs cases)"


@run_test("unit_bootlog_verdict_accuracy", ["R51"])
async def t_bootlog_accuracy(_):
    """The verdict must describe the machine (VM test §4.2): X files resolved after the
    wait (D1), no dash `echo` on captured data (D2: the cmdline came out mangled), the
    DKMS taint notice is not a module failure (D3: PASS was impossible on the target),
    `degraded` is not a PASS (D13)."""
    import re, subprocess, tempfile
    inc = REPO / "iso" / "config" / "includes.chroot"; sbin = inc / "usr" / "local" / "sbin"
    bl = (sbin / "kairos-bootlog").read_text(); ble = (sbin / "kairos-bootlog-early").read_text()
    for name, t in (("kairos-bootlog", bl), ("kairos-bootlog-early", ble)):
        bad = [l for l in t.splitlines() if re.search(r'\becho\s+"[^"]*\$', l) or re.search(r"\becho\s+\$", l)]
        assert not bad, f"{name}: dash echo with interpolated data (backslashes get eaten, D2): {bad}"
        assert re.search(r"printf 'cmdline: +%s\\n' \"\$\(cat /proc/cmdline", t), f"{name}: cmdline must be printed with printf"
    assert bl.index("while [ $i -lt 40 ]") < bl.index("XLOG=/var/log/kairos/x.log") < bl.index("XATT=$(cat"), \
        "X log / attempt counter must be resolved AFTER the wait for the kernel + kiosk (D1)"
    prof = (inc / "home" / "kairos" / ".bash_profile").read_text()
    assert 'mv -f "$XLOG" "$XLOG.1"' in prof and "x.log.1" in bl and "THIS boot" in bl, "x.log is per boot; the previous boot's is x.log.1"
    # D3: run the script's own modules pipeline over sample journal lines
    m = re.search(r"MODF=\$\(journalctl[^\n]*\n(.*?tr '\\n' ';')\)", bl, re.S); assert m, "modules pipeline not found"
    with tempfile.TemporaryDirectory() as td:
        sample = Path(td) / "journal.txt"
        sample.write_text("nvidia: loading out-of-tree module taints kernel.\n"
                          "nvidia: module verification failed: signature and/or required key missing - tainting kernel\n"
                          "modprobe: FATAL: Module nvidia_drm not found in directory /lib/modules/6.12.90\n"
                          "amdgpu: disagrees about version of symbol module_layout\n")
        r = subprocess.run(["sh", "-c", f"cat '{sample}' " + m.group(1)], capture_output=True, text=True, check=True)
    assert "verification failed" not in r.stdout and "tainting" not in r.stdout, f"taint notice still counted as a failure: {r.stdout}"
    assert "FATAL" in r.stdout and "disagrees" in r.stdout, f"real load failures must still show: {r.stdout}"
    # D13
    assert re.search(r"running\)\s+check boot_target PASS", bl) and re.search(r"\*\)\s+check boot_target WARN[^\n]*\$SYS", bl), \
        "boot_target: PASS only for `running`, degraded/starting are WARN"
    return "no echo on captured data, X files resolved after the wait, taint notice excluded (pipeline run), degraded -> WARN"


@run_test("unit_bootlog_optional_debug_firmware", ["R51"])
async def t_bootlog_debug_firmware(_):
    """[SD run 2026-10-13] the owner's microSD run was WARN for one line only: iwlwifi's optional debug firmware
    (`firmware: failed to load iwl-debug-yoyo.bin (-2)`, Debian ships none). The script's own firmware block runs here
    (a stub dmesg + check) over the run's REAL dmesg firmware lines: PASS, naming the debug file; a real firmware failure
    next to it still WARNs; the "Direct firmware load … failed" form of a debug file is benign too."""
    import re, subprocess, tempfile
    bl = (REPO / "iso" / "config" / "includes.chroot" / "usr" / "local" / "sbin" / "kairos-bootlog").read_text()
    m = re.search(r"^FWOPT=.*?^else check firmware PASS [^\n]*fi\n", bl, re.S | re.M)
    assert m, "the firmware block (FWOPT= … else check firmware PASS …; fi) not found"
    block = m.group(0)
    owner = ("[    2.680309] [drm] Loading DMUB firmware via PSP: version=0x08004E00\n"
             "[    2.680633] [drm] Found VCN firmware Version ENC: 1.23 DEC: 9 VEP: 0 Revision: 15\n"
             "[    8.705921] systemd[1]: systemd-hibernate-clear.service - Clear Stale Hibernate Storage Info skipped, unmet "
             "condition check ConditionPathExists=/sys/firmware/efi/efivars/HibernateLocation-8cf2644b\n"
             "[    9.795328] iwlwifi 0000:02:00.0: firmware: failed to load iwl-debug-yoyo.bin (-2)\n"
             "[    9.795337] iwlwifi 0000:02:00.0: firmware: failed to load iwl-debug-yoyo.bin (-2)\n"
             "[    9.795346] iwlwifi 0000:02:00.0: firmware: failed to load iwl-debug-yoyo.bin (-2)\n"
             "[    9.795348] iwlwifi 0000:02:00.0: loaded firmware version 89.4d42c933.0 ty-a0-gf-a0-89.ucode op_mode iwlmvm\n")
    cases = {
        "owner_run": (owner, "PASS"),
        "direct_form": ("[ 3.1] iwlwifi 0000:00:14.3: Direct firmware load for iwl-debug-yoyo.bin failed with error -2\n", "PASS"),
        "real_failure_too": (owner + "[ 4.2] amdgpu 0000:05:00.0: firmware: failed to load amdgpu/dcn_3_5_dmcub.bin (-2)\n", "WARN"),
        "real_failure_only": ("[ 4.2] bluetooth hci0: Direct firmware load for intel/ibt-0040-1050.sfi failed with error -2\n", "WARN"),
        "clean": ("[ 2.6] [drm] Loading DMUB firmware via PSP: version=0x08004E00\n", "PASS"),
    }
    got = {}
    with tempfile.TemporaryDirectory() as td:
        for name, (dmesg, want) in cases.items():
            f = Path(td) / f"{name}.txt"
            f.write_text(dmesg)
            sh = (f"dmesg() {{ cat '{f}'; }}\ncheck() {{ printf '%s|%s|%s\\n' \"$1\" \"$2\" \"$3\"; }}\n" + block)
            r = subprocess.run(["sh", "-c", sh], capture_output=True, text=True, timeout=30)
            assert r.returncode == 0 and r.stdout.count("\n") == 1, (name, r.stdout, r.stderr)
            chk, st, detail = r.stdout.strip().split("|", 2)
            got[name] = (st, detail)
            assert chk == "firmware" and st == want, (name, st, detail)
    assert "iwl-debug-yoyo.bin" in got["owner_run"][1] and "benign" in got["owner_run"][1], got["owner_run"]
    assert "dcn_3_5_dmcub" in got["real_failure_too"][1] and "yoyo" not in got["real_failure_too"][1], got["real_failure_too"]
    assert got["clean"][1] == "no firmware load failures in dmesg", got["clean"]
    return (f"the owner's dmesg -> PASS ({got['owner_run'][1][:90]}); Direct-load form PASS; a real failure WARNs "
            f"with or without the debug line; clean PASS")


@run_test("unit_failure_capture_bounded", ["R51", "R63"])
async def t_failure_capture_bounded(_):
    """D10: a crash-looping kernel triggered the failure capture every 3 s forever (86
    captures of ~340 KB + sync onto the FAT32 ESP in 4.5 min, the boot history erased,
    a log truncated by concurrent writers, the unit never `failed`). Now: kairos.service
    retries with backoff and a start limit so a dead kernel ENDS in failed; one handler
    instance per trigger (no ambiguous exit-status propagation); the script caps
    captures per boot and trigger (a trigger that really ended in failed still gets its
    final one), skips instead of writing unlocked, and bounds attempts.txt itself."""
    import math, re, subprocess, tempfile
    inc = REPO / "iso" / "config" / "includes.chroot"; units = inc / "etc" / "systemd" / "system"; sbin = inc / "usr" / "local" / "sbin"
    k = (units / "kairos.service").read_text()
    def val(t, key):
        m = re.search(rf"^{key}=(\S+)", t, re.M); assert m, f"{key} missing"; return m.group(1)
    assert val(k, "Restart") == "on-failure", "a clean exit is a stop, a crash is retried — never Restart=always into an endless hot loop"
    rs, steps, mx = float(val(k, "RestartSec")), int(val(k, "RestartSteps")), float(val(k, "RestartMaxDelaySec"))
    burst, interval = int(val(k, "StartLimitBurst")), float(val(k, "StartLimitIntervalSec"))
    assert rs <= 5 and mx <= 60 and steps >= 1 and burst >= 5 and interval >= 300, (rs, steps, mx, burst, interval)
    # systemd's schedule: restart n waits RestartSec * (Max/RestartSec)^((n-1)/steps), capped at Max
    starts, t = [0.0], 0.0
    for n in range(1, burst + 3):
        t += mx if n > steps else rs * (mx / rs) ** ((n - 1) / steps)
        starts.append(t)
    assert sum(1 for s in starts if s <= interval) > burst, "a permanently crashing kernel must hit the start limit within one interval"
    assert sum(1 for s in starts if s <= 60) >= 5, "a transient failure must still get several quick retries in the first minute"
    assert 120 < starts[burst] < interval, f"`failed` should come after minutes of crashing, not seconds or never: {starts[burst]:.0f}s"
    assert val(k, "OnFailure") == "kairos-bootlog-failure@%n.service"
    tmpl = (units / "kairos-bootlog-failure@.service").read_text()
    assert not re.search(r"^RemainAfterExit=", tmpl, re.M) and "DefaultDependencies=no" in tmpl and "failure %i" in tmpl, \
        "template: no RemainAfterExit (a later failure must capture again), no default deps, passes the trigger"
    assert not (units / "kairos-bootlog-failure.service").exists()
    for f, needle in ((units / "local-fs.target.d" / "kairos-bootlog.conf", "OnFailure=kairos-bootlog-failure@%n.service"),
                      (units / "kairos-bootlog-xfail.path", "Unit=kairos-bootlog-failure@x11.service"),
                      (REPO / "iso" / "build-img.sh", "OnFailure=kairos-bootlog-failure@%n.service")):
        assert needle in f.read_text(), f"{f.name}: {needle}"
    assert not re.search(r"kairos-bootlog-failure\.service", "".join(p.read_text() for p in units.rglob("*") if p.is_file()))
    bl = (sbin / "kairos-bootlog").read_text()
    assert re.search(r"^FAILCAP=\$\{KAIROS_BOOTLOG_FAILCAP:-(\d)\}", bl, re.M) and 2 <= int(re.search(r"FAILCAP:-(\d)", bl).group(1)) <= 4
    assert bl.index("flock -w 90 9") < bl.index('[ "$REASON" = failure ]') < bl.index("STAMP=$(date"), "cap check under the lock, before any ESP write"
    assert re.search(r"flock -w \d+ 9 [^\n]*\|\| \{[^\n]*exit 0; \}", bl), "no lock -> skip, never an unlocked writer"
    assert 'systemctl -q is-failed "$TRIG"' in bl, "a trigger that really ended in failed gets its final capture past the cap"
    assert bl.count('tail -n 40 "$LOGDIR/attempts.txt"') == 1, "attempts.txt is bounded by the late capture itself, not only by -early"
    with tempfile.TemporaryDirectory() as td:
        esp = Path(td) / "esp"; logdir = esp / "KAIROS-LOGS"; logdir.mkdir(parents=True)
        (logdir / "attempts.txt").write_text("".join(f"#{i}  started 2026-01-0{i % 9 + 1}\n" for i in range(1, 61)))
        runf = Path(td) / "attempt"; runf.write_text("60\n")
        # a SETTLED machine (fake systemctl: multi-user reached, running) with the N2 pacing
        # off — the cap alone is under test here; pacing has its own test below
        env = dict(os.environ, KAIROS_BOOTLOG_ESP=str(esp), KAIROS_BOOTLOG_RUN=str(runf),
                   KAIROS_BOOTLOG_FAILCOUNT=str(Path(td) / "failc"), KAIROS_BOOTLOG_FAILCAP="2",
                   KAIROS_BOOTLOG_FAILPACE="0", PATH=f"{_fake_systemctl(Path(td))}:{os.environ['PATH']}")
        # the clock is pinned (fake date): every capture lands in the SAME second — the
        # names must still be distinct (boot-S.txt, boot-S-2.txt) or the cap count below
        # would be met by an overwrite, not by two captures
        env["PATH"] = f"{_fake_date(Path(td), '20260912-070000')}:{env['PATH']}"
        outs = []
        for i in range(3):
            r = subprocess.run([str(sbin / "kairos-bootlog"), "failure", "kairos.service"], env=env, capture_output=True, text=True, timeout=170)
            assert r.returncode == 0, r
            outs.append(r.stderr)
        assert sorted(f.name for f in logdir.glob("boot-*")) == \
            ["boot-20260912-070000-2.json", "boot-20260912-070000-2.txt", "boot-20260912-070000.json", "boot-20260912-070000.txt"], \
            "cap 2 -> exactly 2 captures for 3 failures, and two in one second are two files (.txt/.json paired)"
        assert (logdir / "latest.txt").read_text() == (logdir / "boot-20260912-070000-2.txt").read_text(), "latest.txt is the -2 capture"
        assert "cap (2) reached" in outs[2] and outs[0] == "" == outs[1], outs
        assert (Path(td) / "failc.kairos.service").read_text().strip() == "3"
        att = (logdir / "attempts.txt").read_text().splitlines()
        assert len(att) <= 40 and att[-1].startswith("#60  failure kairos.service log written"), att[-3:]
        j = json.loads((logdir / "latest.json").read_text())
        assert j["reason"] == "failure" and j["trigger"] == "kairos.service" and j["attempt"] == 60
        assert (logdir / "latest.txt").read_text().splitlines()[0].endswith("(failure kairos.service) ====================")
    return (f"Restart=on-failure, {burst} starts/{interval:.0f}s with {rs:.0f}s..{mx:.0f}s backoff -> failed at ~{starts[burst]:.0f}s; "
            f"per-trigger template; capture cap proven (3 failures -> 2 logs), attempts.txt trimmed to <=40")


@run_test("unit_x_retry_chain_consistent", ["R51", "R53", "R63"])
async def t_x_retry_chain(_):
    """D8/D5: with X broken, 5 instant startx failures hit getty@tty1's stock start
    limit (5 in 10 s) before .bash_profile's 6th attempt could flag the loop — the
    path unit never fired, the slow-retry branch was dead code, and X stayed dead until
    reboot. Now the retries are paced (sleep between attempts), the getty drop-in widens
    the limit so only an UNPACED shell trips it, the flag reaches the per-trigger handler,
    and the script consumes the flag before anything else. X is pinned to :0 (a restarted
    kiosk came up as :1 and orphaned every :0 consumer) and hw.py finds the live display."""
    import importlib, re, subprocess, tempfile
    inc = REPO / "iso" / "config" / "includes.chroot"; units = inc / "etc" / "systemd" / "system"
    prof = (inc / "home" / "kairos" / ".bash_profile").read_text()
    subprocess.run(["bash", "-n", str(inc / "home" / "kairos" / ".bash_profile")], check=True)
    flag = int(re.search(r'\[ "\$n" -eq (\d+) \] && touch /run/kairos/x-failed', prof).group(1))
    m = re.search(r'elif \[ "\$n" -le (\d+) \]; then\s*\n\s*sleep (\d+)', prof); assert m, "no paced fast retries"
    fast_upto, fast = int(m.group(1)), int(m.group(2))
    slow = int(re.search(r"x-failed 2>/dev/null\n\s*sleep (\d+)", prof).group(1))
    assert fast_upto == flag - 1 and fast >= 2 and slow >= 20, (flag, fast_upto, fast, slow)
    first_sleep = re.search(r"\n\s+sleep \d+", prof).start()
    assert prof.index('if [ "$n" -eq 1 ]; then') < first_sleep, "the first attempt of a boot never sleeps (boot time)"
    dropin = (units / "getty@tty1.service.d" / "kairos-xretry.conf").read_text()
    interval = int(re.search(r"^StartLimitIntervalSec=(\d+)", dropin, re.M).group(1))
    burst = int(re.search(r"^StartLimitBurst=(\d+)", dropin, re.M).group(1))
    assert dropin.index("[Unit]") < dropin.index("StartLimitIntervalSec"), "start limits are [Unit] settings"
    # an X that dies instantly: login-shell start times under the pacing above
    starts = [0.0]
    for n in range(2, 80):
        starts.append(starts[-1] + (fast if n <= fast_upto else slow))
    def max_starts(window):
        return max(sum(1 for s in starts if t0 <= s <= t0 + window) for t0 in starts)
    assert max_starts(interval) < burst, f"paced loop would trip the drop-in limit: {max_starts(interval)} starts in {interval}s >= {burst}"
    assert max_starts(10) <= 5, "the paced loop must not even trip systemd's stock 5-in-10s default"
    assert flag < burst, "the flagging attempt must come before any limit can trip"
    assert max_starts(60) < 20 and slow >= 20, "an unpaced shell (no sleeps) still trips the widened limit: no hot loop on tty1"
    hook = (REPO / "iso" / "config" / "hooks" / "normal" / "0200-kairos-system.hook.chroot").read_text()
    assert "StartLimit" not in hook, "the hook's autologin drop-in must not override the retry limits"
    # the flag reaches its own handler instance, and the script consumes it before any early exit
    path = (units / "kairos-bootlog-xfail.path").read_text()
    assert "PathExists=/run/kairos/x-failed" in path and re.search(r"^Unit=kairos-bootlog-failure@x11\.service$", path, re.M)
    bl = (inc / "usr" / "local" / "sbin" / "kairos-bootlog").read_text()
    assert bl.index("rm -f /run/kairos/x-failed") < bl.index('mkdir -p "$LOGDIR"') < bl.index("flock"), \
        "the flag must be consumed before any early exit (PathExists is level-triggered)"
    assert "X failed to start ($XATT attempt(s) this boot)" in bl
    # D5: pinned display, stale lock cleared; hw.py discovers the live display
    assert "startx -- :0 -background none" in prof and "/tmp/.X0-lock" in prof and "/tmp/.X11-unix/X0" in prof
    sys.path.insert(0, str(REPO / "kernel")); hw = importlib.import_module("hw")
    with tempfile.TemporaryDirectory() as td:
        old = hw.X11_SOCKETS; hw.X11_SOCKETS = Path(td)
        try:
            assert hw._display() == ":0"
            (Path(td) / "X1").touch(); assert hw._display() == ":1", "X0 gone, X1 alive -> :1"
            (Path(td) / "X0").touch(); assert hw._display() == ":0"
            saved = {k: os.environ.pop(k) for k in ("DISPLAY", "XAUTHORITY") if k in os.environ}   # a desktop host sets both
            try:
                xe = hw._xenv()
                assert xe["DISPLAY"] == ":0" and xe["XAUTHORITY"] == "/home/kairos/.Xauthority", (xe["DISPLAY"], xe["XAUTHORITY"])
            finally:
                os.environ.update(saved)
        finally:
            hw.X11_SOCKETS = old
    return (f"paced retries (sleep {fast}s to attempt {fast_upto}, {slow}s after; flag at #{flag}) vs getty limit {burst}/{interval}s: "
            f"max {max_starts(interval)} starts per window; x11 handler instance; X pinned to :0, hw.py finds :1 fallback")


def _fake_date(where: Path, stamp: str) -> Path:
    """a date(1) whose capture stamp (+%Y%m%d-%H%M%S) is pinned to STAMP; anything else
    goes to the real date — two captures 'in the same second' become deterministic"""
    d = where / "fakedate"; d.mkdir(exist_ok=True)
    real = shutil.which("date")
    (d / "date").write_text(f"""#!/bin/sh
[ "$1" = +%Y%m%d-%H%M%S ] && {{ echo {stamp}; exit 0; }}
exec {real} "$@"
""")
    (d / "date").chmod(0o755)
    return d


def _fake_systemctl(where: Path) -> Path:
    """a systemctl for the bootlog tests whose answers come from the environment:
    FAKE_SYS = is-system-running, FAKE_ACTIVE = active units, FAKE_FAILED = units in failed"""
    d = where / "fakebin"; d.mkdir(exist_ok=True)
    (d / "systemctl").write_text("""#!/bin/sh
sub=""; unit=""
for a in "$@"; do case "$a" in -*) ;; *) if [ -z "$sub" ]; then sub=$a; else unit=$a; fi ;; esac; done
case "$sub" in
  is-system-running) echo "${FAKE_SYS:-running}"; [ "${FAKE_SYS:-running}" = running ] ;;
  is-active) case " ${FAKE_ACTIVE:-graphical.target multi-user.target basic.target sysinit.target} " in *" $unit "*) exit 0 ;; *) exit 3 ;; esac ;;
  is-failed) case " ${FAKE_FAILED:-} " in *" $unit "*) exit 0 ;; *) exit 1 ;; esac ;;
  is-enabled) echo disabled; exit 1 ;;
  *) exit 0 ;;
esac
""")
    (d / "systemctl").chmod(0o755)
    return d


@run_test("unit_bootlog_paced_and_preliminary", ["R51", "R63"])
async def t_bootlog_paced(_):
    """docs/VM-TEST-2026-09-10.md N1-N3. N1: a failure capture while systemd is still
    `starting` says WARN "boot still in progress", never FAIL "boot stopped at". N2: captures
    of one trigger are paced (< 30 s apart -> skipped) and the per-boot budget is spent only
    once multi-user.target is reached (one uncounted early look before), while a trigger that
    ENDED in failed is always captured. N3: .bash_profile re-raises the X-failed flag every
    30th attempt so a long X failure keeps refreshing the stick. Real script, fake systemctl."""
    import re, subprocess, tempfile, time
    inc = REPO / "iso" / "config" / "includes.chroot"
    bl = inc / "usr" / "local" / "sbin" / "kairos-bootlog"
    body = bl.read_text()
    assert re.search(r"^FAILPACE=\$\{KAIROS_BOOTLOG_FAILPACE:-(\d+)\}", body, re.M) and int(re.search(r"FAILPACE:-(\d+)", body).group(1)) >= 20
    assert 'systemctl -q is-active multi-user.target' in body and "boot still in progress" in body
    prof = (inc / "home" / "kairos" / ".bash_profile").read_text()
    m = re.search(r'\[ "\$n" -gt 6 \] && \[ \$\(\(n % (\d+)\)\) -eq 0 \] && touch /run/kairos/x-failed', prof)
    assert m and 20 <= int(m.group(1)) <= 60, "N3: the X loop must re-flag periodically after the 6th attempt"
    every = int(m.group(1))
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); esp = t / "esp"; logdir = esp / "KAIROS-LOGS"; logdir.mkdir(parents=True)
        runf = t / "attempt"; runf.write_text("7\n")
        base = dict(os.environ, KAIROS_BOOTLOG_ESP=str(esp), KAIROS_BOOTLOG_RUN=str(runf),
                    KAIROS_BOOTLOG_FAILCOUNT=str(t / "failc"), KAIROS_BOOTLOG_FAILCAP="2",
                    PATH=f"{_fake_systemctl(t)}:{os.environ['PATH']}")
        def run(**fake):
            env = dict(base, **fake)
            r = subprocess.run([str(bl), "failure", "kairos.service"], env=env, capture_output=True, text=True, timeout=170)
            assert r.returncode == 0, r
            return r.stderr.strip()
        def verdict():
            return json.loads((logdir / "latest.json").read_text())
        logs = lambda: len(list(logdir.glob("boot-*.txt")))
        # N1: 3 s into the boot — basic.target reached, system "starting"
        assert run(FAKE_SYS="starting", FAKE_ACTIVE="basic.target sysinit.target") == ""
        j = verdict(); bt = j["checks"]["boot_target"]
        assert bt["status"] == "WARN" and "boot still in progress" in bt["detail"] and "basic.target" in bt["detail"], bt
        assert "boot stopped" not in (logdir / "latest.txt").read_text(), "N1: a boot in progress is not a stopped boot"
        assert logs() == 1 and (t / "failc.kairos.service.early").exists() and not (t / "failc.kairos.service").exists(), \
            "the early look is written but does not count against the budget"
        # N2 (a): the crash loop fires again 3 s later -> paced, nothing written
        out = run(FAKE_SYS="starting", FAKE_ACTIVE="basic.target sysinit.target")
        assert "paced" in out and logs() == 1, out
        # N2 (b): 40 s later, still before multi-user -> the early look is already there, skipped
        (t / "failc.kairos.service.last").write_text(f"{int(time.time()) - 40}\n")
        out = run(FAKE_SYS="starting", FAKE_ACTIVE="basic.target sysinit.target")
        assert "early look" in out and logs() == 1, out
        # the machine settles (multi-user reached): the budget (cap 2) counts from here
        for i in range(3):
            (t / "failc.kairos.service.last").write_text(f"{int(time.time()) - 40}\n")
            out = run(FAKE_SYS="degraded")
            if i < 2:
                assert out == "" and logs() == 2 + i, (i, out)
            else:
                assert "cap (2) reached" in out and logs() == 3, out
        assert (t / "failc.kairos.service").read_text().strip() == "3"
        j = verdict(); assert j["checks"]["boot_target"]["status"] == "WARN" and "degraded" in j["checks"]["boot_target"]["detail"]
        # a unit that ENDED in failed is captured past the cap and past the pacing (its final state)
        (t / "failc.kairos.service.last").write_text(f"{int(time.time())}\n")
        assert run(FAKE_SYS="degraded", FAKE_FAILED="kairos.service") == "" and logs() == 4
        # the settled healthy verdict is unchanged (D13)
        (t / "failc.kairos.service.last").write_text("0\n"); (t / "failc.kairos.service").write_text("0\n")
        assert run(FAKE_SYS="running") == ""
        assert verdict()["checks"]["boot_target"]["status"] == "PASS"
    return (f"N1 WARN 'boot still in progress' while starting; N2 paced (skip < FAILPACE s), 1 uncounted early look, "
            f"budget from multi-user, failed always captured; N3 X flag re-raised every {every} attempts")



@run_test("unit_bootlog_crash_records", ["R51"])
async def t_bootlog_crash_records(_):
    """A kernel panic's own text reaches the stick (VM fault test 2026-10-06, docs/BOOTLOG-TEST-
    2026-10-06.md): systemd-pstore archives EFI pstore records as <pstore>/<time>/<nr>/
    dmesg-efi_pstore-<time><part><nr> (+ dmesg.txt per dir), and ONE panic can span several
    <time> dirs (Part1 = the end of the log is written first; one VM panic gave 5). The early
    breadcrumb groups the dirs new to this copy (a dir holding a Part1 starts the next crash),
    writes crash-<first>.txt in log order, notes it in attempts.txt, never copies twice; the late
    capture's fallback (--crashes-only) is the same code and its verdict headlines the crash."""
    import subprocess, tempfile
    sb = REPO / "iso" / "config" / "includes.chroot" / "usr" / "local" / "sbin"
    early, late = sb / "kairos-bootlog-early", sb / "kairos-bootlog"
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); ps = t / "pstore"; esp = t / "esp"; logdir = esp / "KAIROS-LOGS"; logdir.mkdir(parents=True)
        def part(d, p, text):
            f = ps / d / "001" / f"dmesg-efi_pstore-{d}{p:02d}001"; f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(f"Panic#1 Part{p}\n{text}\n")
        # crash A spans 3 dirs (Part1+2 | Part3 | Part4, dir 100 also has systemd-pstore's dmesg.txt),
        # crash B 2 dirs (Part1 | Part2)
        part("100", 1, "A end: Kernel panic - not syncing: test A"); part("100", 2, "A middle-2")
        (ps / "100" / "001" / "dmesg.txt").write_text("A middle-2\nA end: Kernel panic - not syncing: test A\n")
        part("101", 3, "A middle-3"); part("102", 4, "A start")
        part("200", 1, "B end: Kernel panic - not syncing: test B"); part("201", 2, "B start")
        env = dict(os.environ, KAIROS_BOOTLOG_ESP=str(esp), KAIROS_BOOTLOG_RUN=str(t / "attempt"),
                   KAIROS_BOOTLOG_PSTORE=str(ps), PATH=f"{_fake_systemctl(t)}:{os.environ['PATH']}")
        subprocess.run(["sh", str(early)], env=env, check=True, timeout=120, capture_output=True)
        crashes = sorted(p.name for p in logdir.glob("crash-*.txt"))
        assert crashes == ["crash-100.txt", "crash-200.txt"], crashes
        a = (logdir / "crash-100.txt").read_text()
        pos = [a.index(x) for x in ("A start", "A middle-3", "A middle-2", "A end")]
        assert pos == sorted(pos), "one crash, in log order across its pstore dirs (oldest part first)"
        assert a.count("A middle-2") == 1, "dmesg.txt is used where it exists — never the same text twice"
        b = (logdir / "crash-200.txt").read_text()
        assert b.index("B start") < b.index("B end") and "test A" not in b, "the second crash is its own file"
        att = (logdir / "attempts.txt").read_text()
        assert "CRASHED" in att and "crash-100.txt" in att and "crash-200.txt" in att, att
        assert sorted((logdir / "pstore-copied.txt").read_text().split()) == ["100", "101", "102", "200", "201"]
        subprocess.run(["sh", str(early), "--crashes-only"], env=env, check=True, timeout=120, capture_output=True)
        assert sorted(p.name for p in logdir.glob("crash-*.txt")) == crashes and (logdir / "crash-100.txt").read_text() == a
        subprocess.run([str(late), "selftest"], env=env, check=True, timeout=170, capture_output=True)
        c = json.loads((logdir / "latest.json").read_text())["checks"]["crash"]
        assert c["status"] == "FAIL" and "crash-100.txt" in c["detail"] and "Kernel panic" in c["detail"], c
    return "one crash per Part1 group across pstore dirs, log order, dmesg.txt once, no re-copy, FAIL crash headline"


@run_test("unit_bootlog_shutdown_and_stall", ["R51"])
async def t_bootlog_shutdown_stall(_):
    """VM fault test 2026-10-06: the shutdown capture of every clean reboot headlined "FAIL xorg:
    X failed to start" (X is already gone when it runs) — at shutdown X/kiosk/kernel/target/network
    are judged by the run (X started once = PASS). A boot that never reaches multi-user.target gets
    a "stalled" capture from kairos-bootlog-stall.timer (5 min) — the late capture is After=multi-user
    and never ran for it; a boot that did reach it is left to the late capture (nothing written)."""
    import subprocess, tempfile
    inc = REPO / "iso" / "config" / "includes.chroot"
    bl = inc / "usr" / "local" / "sbin" / "kairos-bootlog"
    tm = (inc / "etc" / "systemd" / "system" / "kairos-bootlog-stall.timer").read_text()
    sv = (inc / "etc" / "systemd" / "system" / "kairos-bootlog-stall.service").read_text()
    assert "OnBootSec=5min" in tm and "WantedBy=sysinit.target" in tm and "DefaultDependencies=no" in tm
    assert "ExecStart=-/usr/local/sbin/kairos-bootlog stalled" in sv
    for f in ("iso/build-img.sh", "iso/config/hooks/normal/0200-kairos-system.hook.chroot"):
        assert "kairos-bootlog-stall.timer" in (REPO / f).read_text(), f"{f} must enable the stall timer"
    xcnt = Path("/tmp/kairos-x-attempts"); had = xcnt.exists()
    xlog = Path("/tmp/kairos-x.log"); had_log = xlog.exists() or Path("/var/log/kairos/x.log").exists()
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); esp = t / "esp"; logdir = esp / "KAIROS-LOGS"; logdir.mkdir(parents=True)
        (t / "attempt").write_text("3\n")
        env = dict(os.environ, KAIROS_BOOTLOG_ESP=str(esp), KAIROS_BOOTLOG_RUN=str(t / "attempt"),
                   KAIROS_BOOTLOG_PSTORE=str(t / "none"), PATH=f"{_fake_systemctl(t)}:{os.environ['PATH']}")
        try:
            if not had and not Path("/run/kairos/x-attempts").exists():
                xcnt.write_text("1\n")                   # X started once this boot, is gone now
            if not had_log:                              # ... and its session got to the kiosk
                xlog.write_text("xinitrc: started (pid 1)\nxinitrc: displays: eDP connected;\n")
            subprocess.run([str(bl), "shutdown"], env=dict(env, FAKE_SYS="stopping"), check=True, timeout=170, capture_output=True)
            ch = json.loads((logdir / "latest.json").read_text())["checks"]
            for k in ("boot_target", "kiosk", "kernel_api", "network"):
                assert ch[k]["status"] == "PASS", (k, ch[k])
            if not had and not had_log:
                assert ch["xorg"]["status"] == "PASS" and "stopped for shutdown" in ch["xorg"]["detail"], ch["xorg"]
                # stick run 2026-10-06: X started, its session never got past the first X calls
                xlog.write_text("xinitrc: started (pid 1, uptime 70s)\n")
                subprocess.run([str(bl), "shutdown"], env=dict(env, FAKE_SYS="stopping"), check=True, timeout=170, capture_output=True)
                x = json.loads((logdir / "latest.json").read_text())["checks"]["xorg"]
                assert x["status"] == "FAIL" and "never got past the display setup" in x["detail"] \
                    and "xinitrc: started" in x["detail"], x
        finally:
            if not had:
                xcnt.unlink(missing_ok=True)
            if not had_log:
                xlog.unlink(missing_ok=True)
        n = len(list(logdir.glob("boot-*.txt")))
        r = subprocess.run([str(bl), "stalled"], env=env, check=True, timeout=170, capture_output=True)
        assert len(list(logdir.glob("boot-*.txt"))) == n, "multi-user reached: the stall timer leaves it to the late capture"
        subprocess.run([str(bl), "stalled"], env=dict(env, FAKE_SYS="starting", FAKE_ACTIVE="basic.target sysinit.target"),
                       check=True, timeout=170, capture_output=True)
        j = json.loads((logdir / "latest.json").read_text())
        assert j["reason"] == "stalled" and j["checks"]["boot_target"]["status"] == "FAIL" \
            and "STALLED" in j["checks"]["boot_target"]["detail"], j["checks"]["boot_target"]
        assert "stalled log written" in (logdir / "attempts.txt").read_text()
    return "shutdown: X once = PASS, kiosk/kernel/target/network neutral; stalled: FAIL only before multi-user, no file after"



@run_test("unit_hybrid_gpu_x_single_gpu", ["R41", "R50"])
async def t_hybrid_gpu_single(_):
    """Stick run 2026-10-06 (docs/STICK-RUN-2026-10-06.md): on the hybrid AMD+NVIDIA laptop udev's
    coldplug autoloaded nvidia_drm (it carries a PCI alias for every NVIDIA GPU) + nvidia_modeset,
    X added the 4070 as a second GPU screen, failed ("Failed to create pixmap") and never answered
    the session — black screen, no kiosk. Now: the display modules are blacklisted from alias
    autoloading (kairos-gpu-setup still loads them explicitly when NVIDIA drives the monitor), X
    never auto-adds a second GPU, .xinitrc leaves breadcrumbs before its first X call, and the
    default loader entry no longer spawns a getty on a serial port the laptop does not have."""
    inc = REPO / "iso" / "config" / "includes.chroot"
    bl = (inc / "etc" / "modprobe.d" / "kairos-nvidia-hybrid.conf").read_text()
    lines = [l.strip() for l in bl.splitlines() if l.strip() and not l.lstrip().startswith("#")]
    assert lines == ["blacklist nvidia_drm", "blacklist nvidia_modeset"], lines
    xc = (inc / "etc" / "X11" / "xorg.conf.d" / "10-kairos-single-gpu.conf").read_text()
    assert re.search(r'Section\s+"ServerFlags"\s+Option\s+"AutoAddGPU"\s+"off"\s+EndSection', xc), xc
    gs = (inc / "usr" / "local" / "sbin" / "kairos-gpu-setup").read_text()
    assert "modprobe nvidia_drm modeset=1" in gs and 'if [ "$nvidia_boot_vga" = "1" ]' in gs, \
        "an NVIDIA boot GPU still gets its display modules explicitly (a blacklist stops only alias autoloading)"
    xi = (inc / "home" / "kairos" / ".xinitrc").read_text()
    assert xi.index('echo "xinitrc: started') < xi.index("xset -dpms") < xi.index('echo "xinitrc: X answers') \
        < xi.index('echo "xinitrc: displays:'), "breadcrumbs around the first X calls, in order"
    bimg = (REPO / "iso" / "build-img.sh").read_text()
    entry = [l for l in bimg.splitlines() if l.startswith("options root=PARTUUID=$ROOT_PARTUUID rw rootwait quiet")]
    assert len(entry) == 1 and "systemd.getty_auto=0" in entry[0] and entry[0].rstrip().endswith("console=tty1"), entry
    serial = [l for l in bimg.splitlines() if l.startswith("options root=PARTUUID=$ROOT_PARTUUID rw rootwait loglevel=4")]
    assert len(serial) == 1 and "getty_auto" not in serial[0], "the hidden serial-debug entry keeps its serial getty"
    return "nvidia_drm/nvidia_modeset alias-blacklisted (explicit load kept for an NVIDIA boot GPU), AutoAddGPU off, xinitrc breadcrumbs, no serial getty on the default entry"


@run_test("unit_stick_run_boot_llm_pointer", ["R41", "R73", "R28", "R51"])
async def t_stick_run_1007(_):
    """Stick run 2026-10-07 (docs/STICK-RUN-2026-10-07.md): no mouse pointer, a slow boot, no LLM.
    (1) the kiosk inherits the root window's cursor: after the kiosk launch the root gets a real arrow
    (unclutter hides it idle); (2) the compute-only NVIDIA load (49 s off the stick) no longer holds
    getty: kairos-gpu-setup backgrounds it unless NVIDIA drives the monitor; the boot logs sync only
    the EFI partition (a global sync held basic.target 13 s); kairos-prewarm reads the desktop and,
    at idle I/O priority, the LLM ahead — only on a USB root, phase 2 only when RAM holds it;
    (3) the LLM may load for LLAMA_LOAD_TIMEOUT (900 s, Gemma off the stick took 443 s) and a request
    that finds llama-server still loading waits for /health instead of meeting "503 Loading model"."""
    import subprocess, tempfile, time as _t
    inc = REPO / "iso" / "config" / "includes.chroot"
    xi = (inc / "home" / "kairos" / ".xinitrc").read_text()
    assert xi.index('echo "xinitrc: kiosk pids') < xi.index("xsetroot -cursor_name left_ptr"), "arrow once the kiosk is up"
    assert "unclutter -idle" in xi and "goes for good" not in xi
    for f in ("kairos-bootlog", "kairos-bootlog-early"):
        body = (inc / "usr" / "local" / "sbin" / f).read_text()
        assert 'sync -f "$LOGDIR"' in body and not re.search(r"^sync$", body, re.M), f"{f}: ESP-only sync"
    # (2a) kairos-gpu-setup: hybrid = background (the script returns before modprobe runs), NVIDIA boot GPU = synchronous
    gs = inc / "usr" / "local" / "sbin" / "kairos-gpu-setup"
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); (t / "bin").mkdir(); log = t / "calls"
        (t / "bin" / "modprobe").write_text(f'#!/bin/sh\nsleep 1\necho "modprobe $*" >> "{log}"\nexit 0\n')
        (t / "bin" / "systemctl").write_text(f'#!/bin/sh\necho "systemctl $*" >> "{log}"\n')
        for f in (t / "bin").iterdir():
            f.chmod(0o755)
        def tree(name, gpus):
            d0 = t / name
            for i, (vendor, bv) in enumerate(gpus):
                d = d0 / "bus" / "pci" / "devices" / f"0000:0{i}:00.0"; d.mkdir(parents=True)
                (d / "vendor").write_text(vendor + "\n"); (d / "boot_vga").write_text(bv + "\n")
            return d0
        env = dict(os.environ, PATH=f"{t / 'bin'}:/usr/bin:/bin")
        t0 = _t.monotonic()
        r = subprocess.run(["sh", str(gs)], env=dict(env, KAIROS_SYS=str(tree("hybrid", [("0x1002", "1"), ("0x10de", "0")]))),
                           capture_output=True, text=True, timeout=30)
        took = _t.monotonic() - t0
        assert r.returncode == 0 and took < 0.9, f"hybrid: returned after {took:.1f} s — must not wait for the driver"
        for _ in range(50):
            if log.exists() and "modprobe nvidia_uvm" in log.read_text():
                break
            _t.sleep(0.2)
        assert "modprobe nvidia_uvm" in log.read_text() and "nvidia_drm" not in log.read_text(), log.read_text()
        log.unlink()
        r = subprocess.run(["sh", str(gs)], env=dict(env, KAIROS_SYS=str(tree("desk", [("0x10de", "1")]))),
                           capture_output=True, text=True, timeout=30)
        assert r.returncode == 0 and "modprobe nvidia_drm modeset=1" in log.read_text(), "NVIDIA boot GPU: loaded before X (synchronous)"
    # (2b) kairos-prewarm
    pw = inc / "usr" / "local" / "sbin" / "kairos-prewarm"
    unit = (inc / "etc" / "systemd" / "system" / "kairos-prewarm.service").read_text()
    assert os.access(pw, os.X_OK) and "DefaultDependencies=no" in unit and "Type=simple" in unit \
        and "ExecStart=-/usr/local/sbin/kairos-prewarm" in unit and "WantedBy=sysinit.target" in unit
    for f in ("iso/build-img.sh", "iso/config/hooks/normal/0200-kairos-system.hook.chroot"):
        assert "kairos-prewarm.service" in (REPO / f).read_text(), f"{f} must enable kairos-prewarm"
    assert "ionice -c 3" in pw.read_text(), "phase 2 at idle I/O priority"
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); (t / "d").mkdir(); (t / "m").mkdir()
        (t / "d" / "lib.so").write_bytes(b"x" * 300000); (t / "m" / "model.gguf").write_bytes(b"y" * 500000)
        base = dict(os.environ, KAIROS_PREWARM_LOG=str(t / "log"), KAIROS_PREWARM_DESKTOP=str(t / "d"),
                    KAIROS_PREWARM_MODELS=str(t / "m"))
        subprocess.run(["sh", str(pw)], env=dict(base, KAIROS_PREWARM_ROOTDEV="/dev/null"), check=True, timeout=60)
        assert "not on USB" in (t / "log").read_text() and "phase 1" not in (t / "log").read_text(), "fixed disks: nothing read"
        subprocess.run(["sh", str(pw)], env=dict(base, KAIROS_PREWARM_FORCE="1"), check=True, timeout=60)
        lg = (t / "log").read_text()
        assert "phase 1 desktop: 1 files" in lg and "phase 2 engine + LLM (idle I/O): 1 files" in lg, lg
        big = t / "huge"; big.mkdir(); (big / "model.gguf").touch(); os.truncate(big / "model.gguf", 1 << 40)   # 1 TiB, sparse
        subprocess.run(["sh", str(pw)], env=dict(base, KAIROS_PREWARM_FORCE="1", KAIROS_PREWARM_MODELS=str(big)), check=True, timeout=60)
        assert "phase 2 skipped" in (t / "log").read_text(), "a model the RAM cannot hold is never read"
    # (3) the LLM wait: a request that finds llama-server still loading waits for /health
    sys.path.insert(0, str(REPO / "kernel"))
    import server
    from aiohttp import web as _web
    assert server.LLAMA_LOAD_TIMEOUT >= 900
    calls = {"n": 0}
    async def health(_r):
        calls["n"] += 1
        return _web.json_response({"status": "loading"} if calls["n"] < 4 else {"status": "ok"},
                                  status=503 if calls["n"] < 4 else 200)
    app = _web.Application(); app.router.add_get("/health", health)
    runner = _web.AppRunner(app); await runner.setup()
    site = _web.TCPSite(runner, "127.0.0.1", 0); await site.start()
    port = site._server.sockets[0].getsockname()[1]
    class _Proc:                                           # a llama-server that is still loading
        returncode = None
    saved = server.LLAMA_PORT
    try:
        server.LLAMA_PORT = port
        mm = server.ModelManager(server.MANIFEST)
        mm.llama_proc, mm.llama_model_key, mm.llama_ready = _Proc(), "llm", False
        async def no_start(*a, **k):
            raise AssertionError("must wait for the running server, not start a second one")
        mm.stop_llama = no_start
        await asyncio.wait_for(mm.ensure_llama("llm"), timeout=30)
        assert mm.llama_ready and calls["n"] == 4, calls
        await mm.ensure_llama("llm")                       # ready now: returns at once, no further probe
        assert calls["n"] == 4
    finally:
        server.LLAMA_PORT = saved
        await runner.cleanup()
    return ("pointer arrow after the kiosk launch; compute-only NVIDIA in the background (<1 s), NVIDIA boot GPU synchronous; "
            "ESP-only syncs; prewarm USB-only + RAM-guarded; LLM load window 900 s and mid-load requests wait for /health")

@run_test("unit_kernel_logs_job_and_engine_errors", ["R63"])
async def t_kernel_logs_errors(_):
    """The stick is debugged from its persistent logs after a reboot (no SSH on the target,
    2026-10-06): every job that ends leaves one journal line `[kernel] job <id> <kind> error|done
    after <s>: <error>` whichever way the handler sets status/error, once per outcome, and the
    record stays a plain JSON dict for /api/job; engine output goes to LOG_DIR (append, title line,
    rotated) and its tail into the error; LLM start failures, app/web failures and every 5xx are
    printed (llama-server's output is no longer discarded)."""
    import contextlib, io, tempfile
    sys.path.insert(0, str(REPO / "kernel"))
    import server
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        jid = server.new_job("image")
        server.JOBS[jid].update(results=[1])                                   # progress: no line
        server.JOBS[jid].update(status="error", error="sd-cli failed (1):\nCUDA error: out of memory")
        server.JOBS[jid].update(status="error", error="sd-cli failed (1):\nCUDA error: out of memory")
        j2 = server.new_job("images")
        server.JOBS[j2]["status"] = "done"
        server.JOBS[j2]["error"] = "1 of 3 pictures failed"
    out = buf.getvalue()
    assert out.count(f"[kernel] job {jid} image error after") == 1 and "CUDA error: out of memory" in out, out
    assert f"[kernel] job {j2} images done after" in out and "1 of 3 pictures failed" in out, out
    assert json.loads(json.dumps(server.JOBS[jid]))["status"] == "error"
    saved = (server.LOG_DIR, server.ENGINE_LOG_MAX)
    with tempfile.TemporaryDirectory() as td:
        try:
            server.LOG_DIR = Path(td) / "log"
            f = server.open_engine_log("llama-server.log", "llama-server (llm): argv")
            f.write(b"E gguf_init_from_file: failed to open GGUF file\n"); f.close()
            assert "failed to open GGUF" in server.engine_log_tail("llama-server.log")
            assert (server.LOG_DIR / "llama-server.log").read_text().lstrip().startswith("===== ")
            server.ENGINE_LOG_MAX = 10                                         # past the cap -> .1, fresh file
            server.open_engine_log("llama-server.log", "second start").close()
            assert (server.LOG_DIR / "llama-server.log.1").exists() and "GGUF" not in server.engine_log_tail("llama-server.log")
        finally:
            server.LOG_DIR, server.ENGINE_LOG_MAX = saved
    src = (REPO / "kernel" / "server.py").read_text()
    assert "stderr=asyncio.subprocess.DEVNULL)\n        self.llama_model_key" not in src, "llama-server output must not be discarded"
    for needle in ("llama-server exited during startup (code", "[kernel] app {app_id}", "[kernel] web page",
                   "async def error_log_middleware", 'open_engine_log("downloads.log"'):
        assert needle in src, needle
    return "job outcome lines (once, item or update), JSON intact; engine log title/tail/rotation; LLM/app/web/5xx/download printed"


class BadWindow(Exception):
    """python-xlib's per-request error for a window that is gone (an XError subclass there)"""


class ConnectionClosedError(Exception):
    """python-xlib's error for a Display whose server died: every later call raises it"""


class _FakeX:
    """A fake X server + openbox for kernel/wm.py: the python-xlib surface EWMH uses,
    with a WM that answers the client messages the way openbox does (activate maps +
    focuses, WM_CHANGE_STATE iconifies = unmap + _NET_WM_STATE_HIDDEN, close destroys
    unless the client ignores it, _NET_MOVERESIZE_WINDOW applies the geometry).
    die() is an X restart: every call on this connection raises ConnectionClosedError
    from then on (a window that vanished raises BadWindow instead), and next_event()
    — what the watcher thread blocks in — returns by raising it too."""
    ROOT = 1

    def __init__(self, monitors):
        import threading
        self.atoms, self.names = {}, {}
        self.wins, self.order, self.active = {}, [], 0
        self.monitors = monitors
        self.sent, self.killed = [], []
        self.grabbed = []               # XGetImage rects [R70]
        self.dead, self.died = False, threading.Event()
        self.no_wm = False              # openbox not (yet) running: no check window, no client list

    def die(self):
        self.dead = True; self.died.set()
    def _chk(self):
        if self.dead:
            raise ConnectionClosedError("server")
    def randr(self):
        self._chk(); return self.monitors
    def next_event(self):
        self.died.wait(); raise ConnectionClosedError("server")

    # display
    def screen(self):
        self._chk(); return type("S", (), {"root": self.window(self.ROOT)})()
    def intern_atom(self, name):
        self._chk()
        if name not in self.atoms:
            self.atoms[name] = 100 + len(self.atoms); self.names[self.atoms[name]] = name
        return self.atoms[name]
    def get_atom_name(self, a):
        self._chk(); return self.names[a]
    def create_resource_object(self, kind, xid):
        self._chk(); assert kind == "window"; return self.window(xid)
    def flush(self): self._chk()
    def close(self): pass
    def set_error_handler(self, h): pass

    # windows
    def add(self, xid, inst, cls, title, x, y, w, h, pid=0, wtype="_NET_WM_WINDOW_TYPE_NORMAL", ignores_close=False,
            hints=None):
        """hints [R82]: WM_NORMAL_HINTS (min_w, min_h, max_w, max_h) — SDL's fixed-size window has min == max"""
        self.wins[xid] = dict(inst=inst, cls=cls, title=title, x=x, y=y, w=w, h=h, pid=pid, wtype=wtype,
                              mapped=True, states=set(), ignores_close=ignores_close, hints=hints, props={})
        self.order.append(xid)
        if not self.active:
            self.active = xid
    def window(self, xid):
        srv = self
        class W:
            id = xid
            def get_full_property(self, atom, typ):
                srv._chk()
                name = srv.names[atom]
                if xid == srv.ROOT:
                    v = {"_NET_CLIENT_LIST": [] if srv.no_wm else list(srv.order), "_NET_ACTIVE_WINDOW": [srv.active],
                         "_NET_SUPPORTING_WM_CHECK": None if srv.no_wm else [2]}.get(name)
                elif xid == 2:                                                   # openbox's check window
                    v = {"_NET_SUPPORTING_WM_CHECK": [2], "_NET_WM_NAME": b"Openbox"}.get(name)
                else:
                    if xid not in srv.wins: raise BadWindow(xid)
                    d = srv.wins[xid]
                    v = {"_NET_WM_NAME": d["title"].encode(), "_NET_WM_PID": [d["pid"]] if d["pid"] else None,
                         "_NET_WM_WINDOW_TYPE": [srv.intern_atom(d["wtype"])],
                         "_NET_WM_STATE": [srv.intern_atom(s) for s in d["states"]]}.get(name)
                return None if v is None else type("P", (), {"value": v})()
            def get_wm_class(self):
                srv._chk(); d = srv.wins.get(xid); return (d["inst"], d["cls"]) if d else None
            def _win(self):
                srv._chk()
                if xid not in srv.wins: raise BadWindow(xid)
                return srv.wins[xid]
            def get_geometry(self):
                if xid == srv.ROOT:
                    srv._chk(); return type("G", (), {"x": 0, "y": 0, "width": 1920, "height": 1080})()
                d = self._win(); return type("G", (), {"x": 0, "y": 0, "width": d["w"], "height": d["h"]})()
            def translate_coords(self, src, sx, sy):
                d = self._win(); return type("T", (), {"x": -d["x"], "y": -d["y"]})()
            def get_attributes(self):
                d = self._win(); return type("A", (), {"map_state": 2 if d["mapped"] else 0})()
            def send_event(self, ev, event_mask=0):
                srv._chk()
                assert xid == srv.ROOT and event_mask & 0x100000, "EWMH messages go to the root with SubstructureRedirect"
                srv.handle(*ev)
            def change_attributes(self, **kw): srv._chk()
            def change_property(self, atom, typ, fmt, data, mode=0):          # [R82] WM_TRANSIENT_FOR
                self._win()["props"][atom] = (typ, list(data))
            def delete_property(self, atom):                                 # [R82]
                self._win()["props"].pop(atom, None)
            def get_wm_normal_hints(self):                                   # [R82]
                h = self._win().get("hints")
                return None if h is None else type("H", (), {"flags": 16 | 32, "min_width": h[0], "min_height": h[1],
                                                              "max_width": h[2], "max_height": h[3]})()
            def get_image(self, x, y, w, h, fmt, mask):
                """XGetImage of the root: depth-24 BGRX rows, one flat colour [R70]"""
                srv._chk(); assert xid == srv.ROOT and fmt == 2 and mask == 0xffffffff
                srv.grabbed.append((x, y, w, h))
                return type("I", (), {"depth": 24, "data": b"\x30\x20\x10\x00" * (w * h)})()
        W.srv = srv
        return W()

    # the window manager
    def handle(self, target, atom, data):
        name = self.names[atom]
        self.sent.append((target, name, data))
        d = self.wins.get(target)
        if d is None:
            return
        if name == "_NET_ACTIVE_WINDOW":
            d["mapped"] = True; d["states"].discard("_NET_WM_STATE_HIDDEN"); self.active = target
        elif name == "WM_CHANGE_STATE" and data[0] == 3:
            d["mapped"] = False; d["states"].add("_NET_WM_STATE_HIDDEN")
            if self.active == target:
                self.active = next((x for x in self.order if self.wins[x]["mapped"]), 0)
        elif name == "_NET_CLOSE_WINDOW":
            if not d["ignores_close"]:
                self.destroy(target)
        elif name == "_NET_MOVERESIZE_WINDOW":
            flags = data[0]
            assert flags >> 12 == 2, "source indication must be 'pager' or openbox ignores the request"
            for i, k in enumerate("xywh"):
                if flags & (1 << (8 + i)):
                    d[k] = data[i + 1]
    def destroy(self, xid):
        self.wins.pop(xid, None); self.order.remove(xid)
        if self.active == xid:
            self.active = self.order[-1] if self.order else 0
    def kill(self, pid, force=False):
        """SDL turns SIGTERM into a QUIT event: a game ignoring QUIT survives it; SIGKILL ends it"""
        self.killed.append((pid, "SIGKILL" if force else "SIGTERM"))
        if force:
            for xid, d in list(self.wins.items()):
                if d["pid"] == pid:
                    self.destroy(xid)
        return True


@run_test("unit_wm_ewmh_fake_display", ["R68", "R7", "R61"])
async def t_wm_ewmh(_):
    """kernel/wm.py against a fake X server + WM: the kiosk's own Chromium windows and
    non-normal windows are never listed; a pygame window is attributed to the app the
    kernel launched (pid registry) and placed on its monitor; raise/minimize/restore/
    close/move/resize/place are sent as EWMH client messages and verified by read-back;
    a game ignoring the close request is terminated only when the kernel launched it.
    The service layer degrades to available:false without X and validates ids/ops."""
    import importlib, tempfile
    sys.path.insert(0, str(REPO / "kernel")); wm = importlib.import_module("wm"); hw = importlib.import_module("hw")
    fx = _FakeX([(0, 0, 1920, 1080, True), (1920, 0, 1024, 768, False)])
    fx.add(0x101, "chromium", "Chromium", "kAIrOS", 0, 0, 1920, 1080, pid=100)
    fx.add(0x102, "chromium", "Chromium", "kAIrOS", 1920, 0, 1024, 768, pid=100)
    fx.add(0x202, "pygame", "pygame", "Snake", 400, 300, 640, 480, pid=4242)
    fx.add(0x303, "panel", "Panel", "dock", 0, 0, 1920, 30, wtype="_NET_WM_WINDOW_TYPE_DOCK")
    fx.add(0x404, "mpv", "mpv", "bootanim", 0, 0, 1920, 1080)
    fx.add(0x505, "python3", "python3", "Pong", 2000, 100, 300, 200, pid=5151, ignores_close=True)
    fx.add(0x606, "127.0.0.1", "Code", "kAIrOS", 0, 0, 1280, 800, pid=100)   # a --app window: WM_CLASS from its URL
    saved = (wm._client_message, wm._randr_monitors, wm._proc_name)
    wm._client_message = lambda w, a, d: (w.id, a, d)
    wm._randr_monitors = lambda root: fx.monitors
    wm._proc_name = lambda pid, cache=None: {100: "chrome", 4242: "python3", 5151: "python3"}.get(pid, "")
    try:
        reg = wm.GameRegistry(); reg.register(4242, "snake", "snake game")
        c = wm.EWMH(fx)
        assert c.wm_name() == "Openbox"
        snap = c.snapshot(reg)
        assert snap["screens"] == [[0, 0, 1920, 1080], [1920, 0, 1024, 768]], snap["screens"]
        ids = [w["id"] for w in snap["windows"]]
        assert ids == [0x202, 0x505], f"kiosk (by class AND by owning process), dock, mpv must be filtered: {ids}"
        snake, pong = snap["windows"]
        assert snake["app"] == "snake" and snake["kind"] == "game" and snake["title"] == "Snake" and snake["pid"] == 4242
        assert (snake["x"], snake["y"], snake["w"], snake["h"], snake["minimized"], snake["screen"]) == (400, 300, 640, 480, False, 0)
        assert pong["app"] is None and pong["kind"] == "game" and pong["screen"] == 1, pong
        assert snake["active"] is False and fx.active == 0x101
        # ops, each verified by read-back
        ok, d = c.op(0x202, "raise"); assert ok and d["active"] and fx.active == 0x202 and fx.sent[-1][1] == "_NET_ACTIVE_WINDOW"
        ok, d = c.op(0x202, "minimize"); assert ok and d["hidden"] and not fx.wins[0x202]["mapped"] and fx.sent[-1][1:] == ("WM_CHANGE_STATE", [3, 0, 0, 0, 0])
        assert c.snapshot(reg)["windows"][0]["minimized"] is True
        ok, d = c.op(0x202, "restore"); assert ok and fx.wins[0x202]["mapped"] and not d["minimized"]
        ok, d = c.op(0x202, "move", {"x": 10, "y": 20}); assert ok and (d["x"], d["y"], d["w"], d["h"]) == (10, 20, 640, 480)
        ok, d = c.op(0x202, "resize", {"w": 800, "h": 600}); assert ok and (d["w"], d["h"]) == (800, 600) and (d["x"], d["y"]) == (10, 20)
        ok, d = c.op(0x202, "place", {"x": 1, "y": 2, "w": 3, "h": 4}); assert ok and d == {"x": 1, "y": 2, "w": 3, "h": 4}
        ok, d = c.op(0x202, "move", {"x": "nope"}); assert not ok and "integer" in d
        ok, d = c.op(0x999, "raise"); assert not ok and d == "no such window"
        ok, d = c.op(0x202, "explode"); assert not ok and d.startswith("unknown op")
        ok, d = c.op(0x202, "close"); assert ok and 0x202 not in fx.wins and fx.sent[-1][1] == "_NET_CLOSE_WINDOW"
        # a game that ignores WM_DELETE_WINDOW: not the kernel's child -> stays; registered -> SIGTERM
        c.wait = lambda pred, timeout: pred()          # no 2 s real wait in the test
        kill = lambda x, force: bool(reg.lookup(c.pid(x)) and fx.kill(c.pid(x), force))
        ok, d = c.op(0x505, "close", kill=kill)
        assert not ok and 0x505 in fx.wins and not fx.killed, "an unknown process is never killed"
        reg.register(5151, "pong")
        ok, d = c.op(0x505, "close", kill=kill)
        assert ok and d == {"killed": "SIGKILL"} and fx.killed == [(5151, "SIGTERM"), (5151, "SIGKILL")] and 0x505 not in fx.wins
        assert c.snapshot(reg)["windows"] == []
        # service layer without X: the kernel must answer, never block, never raise
        svc = wm.WindowService()
        with tempfile.TemporaryDirectory() as td:
            old = hw.X11_SOCKETS; hw.X11_SOCKETS = Path(td)
            try:
                r = await svc.list()
                assert r["available"] is False and "missing" in r["reason"] and r["windows"] == [] and r["display"].startswith(":"), r
                r = await svc.op("0x202", "raise"); assert r["ok"] is False and r["status"] == 503 and r["available"] is False
                r = await svc.op("zz", "raise"); assert r["status"] == 400 and "bad window id" in r["error"]
                r = await svc.op("1", "explode"); assert r["status"] == 400 and "unknown op" in r["error"]
                got = []
                async def send(snap): got.append(snap)
                task = asyncio.create_task(svc.stream(send, busy=0.2, idle=0.2))
                await asyncio.sleep(0.1)
                assert got and got[0]["available"] is False, got
                svc.register_game(4242, "snake")            # a change wakes the stream: keepalive (list unchanged)
                await asyncio.sleep(0.25)
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                assert None in got and len(got) >= 2, got
            finally:
                hw.X11_SOCKETS = old
    finally:
        wm._client_message, wm._randr_monitors, wm._proc_name = saved
    return ("3 kiosk + dock + mpv windows filtered, game attributed to its app + monitor; raise/minimize/restore/"
            f"move/resize/place/close verified ({len(fx.sent)} EWMH messages); kill only for registered games; no X -> available:false")


@run_test("http_windows_api_gated", ["R68", "R66"])
async def t_windows_api(s):
    """GET /api/windows is read-only and open; the suite's kernel has no X, so it answers
    available:false (the kernel started and serves without X [R63]); the SSE stream's first
    message says the same. POST /api/windows/{id}/{op} is guarded like every mutating route:
    a remote peer gets 403 without the token and passes with it; ids/ops are validated.
    The op plumbing itself is proven against a fake backend in an in-process server."""
    import socket
    from aiohttp import web
    from aiohttp.test_utils import TestServer, TestClient
    async with s.get(f"{BASE}/api/windows") as r:
        j = await r.json()
    assert r.status == 200 and j["available"] is False and j["windows"] == [] and j["reason"], j
    async with s.get(f"{BASE}/api/windows/stream", timeout=aiohttp.ClientTimeout(total=15)) as r:
        assert r.status == 200 and r.content_type == "text/event-stream"
        buf = ""
        while "data: " not in buf or not buf.rstrip().endswith("}"):
            buf += (await r.content.read(512)).decode()
    first = json.loads(buf.split("data: ", 1)[1].split("\n", 1)[0])
    assert "retry:" in buf and first["available"] is False, buf[:200]
    async with s.post(f"{BASE}/api/windows/0x202/raise", json={}) as r:
        j = await r.json()
    assert r.status == 503 and j["ok"] is False and j["available"] is False, (r.status, j)
    for path, st in (("/api/windows/zz/raise", 400), ("/api/windows/7/explode", 400)):
        async with s.post(f"{BASE}{path}", json={}) as r:
            assert r.status == st and (await r.json())["ok"] is False, (path, r.status)
    ip = socket.gethostbyname(socket.gethostname())
    gate = "no non-loopback interface: gate not exercised remotely"
    if not ip.startswith("127."):
        remote = f"http://{ip}:{BASE.rsplit(':', 1)[1]}"
        async with s.post(f"{remote}/api/windows/0x202/close", json={}) as r:
            j = await r.json()
        assert r.status == 403 and "X-Kairos-Token" in j["error"], (r.status, j)
        async with s.get(f"{remote}/api/windows") as r:
            assert r.status == 200 and (await r.json())["available"] is False, "reads stay open remotely"
        async with s.get(f"{BASE}/api/token") as r:
            tok = (await r.json())["token"]
        async with s.post(f"{remote}/api/windows/0x202/close", json={}, headers={"X-Kairos-Token": tok}) as r:
            assert r.status == 503, f"with the token the guard passes (503 = no X, not 403): {r.status}"
        gate = f"remote {ip}: POST 403 without token, 503 with it, GET open"
    # in-process: the same handlers over a fake window service
    sys.path.insert(0, str(REPO / "kernel")); import server, wm

    class FakeWM:
        def __init__(self):
            self.ops = []
            self.snap = {"available": True, "display": ":0", "wm": "Openbox", "screens": [[0, 0, 1920, 1080]],
                         "windows": [{"id": 514, "hex": "0x202", "title": "Snake", "class": "pygame", "instance": "pygame",
                                      "pid": 4242, "app": "snake", "icon": None, "kind": "game", "x": 400, "y": 300,
                                      "w": 640, "h": 480, "minimized": False, "active": True, "screen": 0}]}
        async def list(self):
            return self.snap
        async def op(self, xid, op, body=None):
            self.ops.append((xid, op, body))
            if op not in wm.OPS:
                return {"ok": False, "error": "unknown op", "status": 400}
            if int(str(xid), 0) != 514:
                return {"ok": False, "error": "no such window", "status": 404}
            return {"ok": True, "op": op, "id": 514, "detail": {"active": True}}
        async def stream(self, send, **kw):
            await send(self.snap); await send(None)
            raise ConnectionResetError
    real, fake = wm.WM, FakeWM()
    wm.WM = fake
    try:
        app = web.Application()
        app.router.add_get("/api/windows", server.h_windows)
        app.router.add_get("/api/windows/stream", server.h_windows_stream)
        app.router.add_post("/api/windows/{id}/{op}", server.guarded(server.h_window_op))
        async with TestClient(TestServer(app)) as c:
            r = await c.get("/api/windows"); j = await r.json()
            assert r.status == 200 and j["windows"][0]["app"] == "snake" and j["wm"] == "Openbox"
            r = await c.post("/api/windows/0x202/minimize", json={}); j = await r.json()
            assert r.status == 200 and j["ok"] and j["detail"]["active"] and fake.ops[-1] == ("0x202", "minimize", {})
            r = await c.post("/api/windows/514/move", json={"x": 5, "y": 6}); assert r.status == 200 and fake.ops[-1][2] == {"x": 5, "y": 6}
            r = await c.post("/api/windows/9/close"); j = await r.json(); assert r.status == 404 and j["error"] == "no such window"
            r = await c.post("/api/windows/514/explode", data="not json"); assert r.status == 400
            r = await c.get("/api/windows/stream"); txt = (await r.text())
            assert 'retry: 5000' in txt and '"title": "Snake"' in txt and ": ka" in txt, txt[:300]
    finally:
        wm.WM = real
    return f"no X: available:false on GET + stream, 503 on POST; ids/ops validated; {gate}; fake backend: {len(fake.ops)} ops routed"



def _fake_xlib(server_of):
    """a stand-in `Xlib` package for kernel/wm.py's connects: Xlib.display.Display(name)
    hands out whatever server_of() returns (or raises) — the ops thread and the watcher
    thread both go through it, exactly like python-xlib's Display() would"""
    import types
    pkg, disp = types.ModuleType("Xlib"), types.ModuleType("Xlib.display")
    disp.Display = lambda name=None: server_of()
    pkg.display = disp
    return {"Xlib": pkg, "Xlib.display": disp}


class _WMHarness:
    """kernel/wm.py's WindowService against fake X servers that can die and be replaced
    (an X restart on the same :0): a fake Xlib in sys.modules, an X0 socket file so the
    service believes a display exists, the module seams the fake needs."""

    def __init__(self, td):
        import importlib
        sys.path.insert(0, str(REPO / "kernel"))
        self.wm, self.hw = importlib.import_module("wm"), importlib.import_module("hw")
        self.srv, self.connects = None, 0
        self.mods = _fake_xlib(self.server_of)
        self.saved = (self.wm._client_message, self.wm._randr_monitors, self.wm._proc_name, self.hw.X11_SOCKETS,
                      {k: sys.modules.get(k) for k in self.mods})
        self.wm._client_message = lambda w, a, d: (w.id, a, d)
        self.wm._randr_monitors = lambda root: root.srv.randr()
        self.wm._proc_name = lambda pid, cache=None: "python3"
        self.hw.X11_SOCKETS = Path(td); (Path(td) / "X0").touch()
        sys.modules.update(self.mods)

    def server_of(self):
        self.connects += 1
        if self.srv is None or self.srv.dead:
            raise OSError("Can't connect to display :0")   # python-xlib's DisplayConnectionError
        return self.srv

    def new_server(self, *wins):
        fx = _FakeX([(0, 0, 1024, 768, True)])
        for w in wins:
            fx.add(*w)
        self.srv = fx
        return fx

    def restore(self):
        self.wm._client_message, self.wm._randr_monitors, self.wm._proc_name, self.hw.X11_SOCKETS, mods = self.saved
        for k, v in mods.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


NO_PID = 4194305   # above Linux's maximum pid_max (2^22): never a live process


@run_test("unit_wm_x_restart_recovery", ["R68", "R63"])
async def t_wm_x_restart(_):
    """D-R68-1: X restarts on the same :0 (paced X retry, kairos-display-apply, a getty
    restart) and the kernel's cached Display is dead. The readers now tell a vanished
    window (BadWindow -> None) from a lost connection (ConnectionClosedError -> raised),
    the service drops the Display and retries once, so the request that finds the
    restart already answers from the NEW server; a server generation the watcher bumps
    reconnects proactively; an X that is down answers available:false honestly and the
    next call recovers; a stream pushes the fresh list; close_streams() ends it."""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        h = _WMHarness(td); wm = h.wm
        try:
            svc = wm.WindowService(); svc.registry.register(NO_PID, "snake")
            fx1 = h.new_server((0x202, "pygame", "pygame", "Snake", 32, 114, 960, 540, NO_PID))
            r = await svc.list()
            assert r["available"] and r["wm"] == "Openbox" and [w["id"] for w in r["windows"]] == [0x202] \
                and r["screens"] == [[0, 0, 1024, 768]] and r["windows"][0]["app"] == "snake", r
            # X restarts: the old connection is dead, the new server has other windows
            fx1.die(); fx2 = h.new_server((0x303, "pygame", "pygame", "Pong", 0, 0, 640, 480, NO_PID))
            r = await svc.list()
            assert r["available"] is True and [w["id"] for w in r["windows"]] == [0x303] and r["wm"] == "Openbox", \
                f"the call that discovers the restart must answer from the new server: {r}"
            assert svc._client.d is fx2 and svc.reconnects == 1
            # the same through an op
            fx2.die(); fx3 = h.new_server((0x404, "pygame", "pygame", "Snake", 0, 0, 640, 480, NO_PID))
            r = await svc.op("0x404", "raise")
            assert r["ok"] and r["detail"]["active"] and svc._client.d is fx3 and svc.reconnects == 2, r
            # the watcher saw ITS connection die (server generation): reconnect before the ops socket errors
            fx4 = h.new_server(); svc._xgen += 1
            r = await svc.list()
            assert svc._client.d is fx4 and r["available"] and r["windows"] == [] and svc.reconnects == 2, r
            # a window that vanished between two calls is NOT a connection loss
            fx4.add(0x505, "pygame", "pygame", "Gone", 0, 0, 1, 1, NO_PID)
            r = await svc.op("0x999", "raise"); assert r["status"] == 404 and svc._client.d is fx4, r
            del fx4.wins[0x505]                      # gone, still in _NET_CLIENT_LIST for a moment
            r = await svc.list(); assert r["available"] and r["windows"] == [] and svc._client.d is fx4, r
            # X down with nothing to replace it: honest available:false; the next call recovers
            fx4.die(); h.srv = None
            r = await svc.list(); assert r["available"] is False and "connect" in r["reason"], r
            fx5 = h.new_server((0x606, "pygame", "pygame", "Back", 0, 0, 640, 480, NO_PID))
            r = await svc.list(); assert r["available"] and [w["id"] for w in r["windows"]] == [0x606], r
            # the SSE stream: X dies and comes back -> available:false, then the fresh list
            got = []
            async def send(snap): got.append(snap)
            task = asyncio.create_task(svc.stream(send, busy=0.15, idle=0.15))
            await asyncio.sleep(0.1)
            assert got and got[0]["available"] and got[0]["windows"][0]["id"] == 0x606, got
            xgen = svc._xgen
            fx5.die(); h.srv = None                  # the real watcher thread is in next_event(): it raises now
            await asyncio.sleep(0.4)
            assert any(g is not None and g["available"] is False for g in got), got
            assert svc._xgen > xgen, "the watcher must bump the server generation when its connection dies"
            fx6 = h.new_server((0x707, "pygame", "pygame", "Again", 0, 0, 640, 480, NO_PID))
            await asyncio.sleep(0.5)
            last = [g for g in got if g is not None][-1]
            assert last["available"] and [w["id"] for w in last["windows"]] == [0x707], got[-3:]
            # kernel stop: the stream returns at once, without a client disconnect
            t0 = time.monotonic(); svc.close_streams()
            await asyncio.wait_for(task, 1.0)
            assert time.monotonic() - t0 < 0.5
            fx6.die()
            return (f"restart on :0 -> same call answers from the new server (ops + list, {svc.reconnects} reconnects), "
                    f"generation bump, BadWindow stays benign, down -> available:false -> back, stream pushes the fresh list")
        finally:
            h.restore()


@run_test("unit_wm_minimized_game_frozen", ["R68", "R7", "R61"])
async def t_wm_frozen(_):
    """D-R68-2: a minimized game is SIGSTOPped (a pid the kernel launched, never an unknown
    process), thawed before raise/activate/restore/close and when its window is mapped
    by anyone; the kernel's shutdown thaws + ends every launched game within its budget.
    Real children (sleep) stand in for games; /proc/<pid>/stat shows the state."""
    import subprocess, tempfile
    game = subprocess.Popen(["sleep", "60"], start_new_session=True)   # like h_game: own session
    other = subprocess.Popen(["sleep", "60"])                          # not ours: never signalled
    sibling = subprocess.Popen(["sleep", "60"])                        # ours, but in OUR process group
    procs = (game, other, sibling)
    state = lambda p: wm._proc_state(p.pid)
    with tempfile.TemporaryDirectory() as td:
        h = _WMHarness(td); wm = h.wm
        try:
            fx = h.new_server((0x202, "pygame", "pygame", "Snake", 32, 114, 960, 540, game.pid),
                              (0x303, "pygame", "pygame", "Pong", 0, 0, 640, 480, other.pid),
                              (0x404, "pygame", "pygame", "Tetris", 0, 0, 640, 480, sibling.pid))
            svc = wm.WindowService(); svc.registry.register(game.pid, "snake"); svc.registry.register(sibling.pid, "tetris")
            await asyncio.sleep(0.05); assert state(game) == "S", state(game)
            r = await svc.op(hex(0x202), "minimize"); assert r["ok"] and r["detail"]["hidden"], r
            await asyncio.sleep(0.05)
            assert state(game) == "T" and svc.registry.frozen() == [game.pid] and svc.registry.frozen_by_xid(0x202) == game.pid
            r = await svc.list(); assert r["windows"][0]["minimized"] is True and state(game) == "T", "a snapshot leaves it frozen"
            r = await svc.op("0x202", "raise"); assert r["ok"] and r["detail"]["active"], r
            await asyncio.sleep(0.05); assert state(game) == "S" and not svc.registry.frozen()
            # mapped again by anyone (a re-map, a WM restart): the next snapshot thaws it
            await svc.op("0x202", "minimize"); await asyncio.sleep(0.05); assert state(game) == "T"
            fx.wins[0x202]["mapped"] = True; fx.wins[0x202]["states"].discard("_NET_WM_STATE_HIDDEN")
            await svc.list(); await asyncio.sleep(0.05); assert state(game) == "S", "mapped by anyone -> thawed"
            # close: SIGCONT first (a stopped process cannot answer WM_DELETE), then the request
            await svc.op("0x202", "minimize"); await asyncio.sleep(0.05); assert state(game) == "T"
            r = await svc.op("0x202", "close"); assert r["ok"] and 0x202 not in fx.wins, r
            await asyncio.sleep(0.05); assert state(game) == "S" and game.poll() is None, "close thaws, the WM's request does the rest"
            # an unknown process is never stopped
            r = await svc.op("0x303", "minimize"); assert r["ok"]; await asyncio.sleep(0.05); assert state(other) == "S"
            # a registered game in the kernel's own process group: signalled by pid — we are still running
            assert os.getpgid(sibling.pid) == os.getpgid(0)
            r = await svc.op("0x404", "minimize"); assert r["ok"]; await asyncio.sleep(0.05); assert state(sibling) == "T"
            # kernel stop: frozen games thawed and ended within the budget (SIGTERM ends sleep)
            fx.add(0x202, "pygame", "pygame", "Snake", 0, 0, 1, 1, pid=game.pid)
            await svc.op("0x202", "minimize"); await asyncio.sleep(0.05); assert state(game) == "T"
            t0 = time.monotonic(); r = await svc.shutdown(); dt = time.monotonic() - t0
            assert r == {"games": 2, "killed": []} and dt < 2.0, (r, dt)
            assert game.wait(timeout=2) == -15 and sibling.wait(timeout=2) == -15, "SIGTERM after SIGCONT ends them"
            assert other.poll() is None, "the unknown process outlives the kernel"
            return "minimize -> T, raise/close/re-map -> S, unknown pid untouched, group vs pid signalling, shutdown thaws + ends in %.2f s" % dt
        finally:
            h.restore()
            for p in procs:
                if p.poll() is None:
                    p.kill()
                p.wait()


@run_test("unit_wm_frame_pairing", ["R82", "R68", "R52", "R70"])
async def t_wm_frames(_):
    """[R82] the frame <-> game-window pairs live in kernel/wm.py (D0 REPORT §7), keyed by the desktop frame's id.
    Against the fake X + openbox: a game registered with a frame is paired the moment its window is listed — made
    transient for the kiosk of the frame's screen (openbox keeps a transient above the focused fullscreen kiosk) and
    centred in the frame's body (CSS px -> root px through that kiosk: origin + its width / the page's viewport, so a
    2x screen works); follow (no read-back) and place (verified) move it with the frame; snapshot grabs its pixels and
    says where they lie in the body (CSS px); cover / minimize iconify + SIGSTOP it, uncover / restore thaw + map it —
    held while the other flag is still set — and hand the keyboard back to the window that had it unless the frame is
    focused; adopt puts a running game into a new frame (only one the kernel launched); a game's end is recorded on
    its frame; close ends it and drops the record; every snapshot lists the frames and marks paired windows.
    Covered = out of the kiosk's group + restacked below it + frozen (still mapped: a snapshot must not thaw it);
    minimized = out of the group + iconified + frozen (openbox drops the kiosk's focus when a transient of it is
    iconified — measured in Xvfb + openbox; tests/test_games_windows.py proves the real stacking)."""
    import subprocess, tempfile
    g1 = subprocess.Popen(["sleep", "60"], start_new_session=True)
    g2 = subprocess.Popen(["sleep", "60"], start_new_session=True)
    g3 = subprocess.Popen(["sleep", "60"], start_new_session=True)
    procs = (g1, g2, g3)
    state = lambda p: wm._proc_state(p.pid)
    with tempfile.TemporaryDirectory() as td:
        h = _WMHarness(td); wm = h.wm
        try:
            fx = h.new_server((0x101, "chromium", "Chromium", "kAIrOS", 0, 0, 1920, 1080, 100),
                              (0x102, "chromium", "Chromium", "kAIrOS", 1920, 0, 1280, 720, 101))
            fx.monitors = [(0, 0, 1920, 1080, True), (1920, 0, 1280, 720, False)]
            fx.add(0x202, "pygame", "pygame", "Snake", 700, 400, 960, 540, pid=g1.pid, hints=(960, 540, 960, 540))
            fx.add(0x303, "pygame", "pygame", "Pong", 0, 0, 640, 480, pid=g2.pid, hints=(640, 480, 640, 480))
            fx.add(0x404, "pygame", "pygame", "Tetris", 50, 60, 800, 600, pid=g3.pid, hints=(800, 600, 800, 600))
            fx.add(0x505, "python3", "python3", "Stranger", 10, 10, 300, 200, pid=4194305)
            fx.active = 0x101
            svc = wm.WindowService()
            f1 = {"id": "s0-win1-a", "screen": 0, "x": 100, "y": 80, "w": 1000, "h": 600, "vw": 1920, "vh": 1080}
            svc.register_game(g1.pid, "snake", "Snake", frame=f1)
            svc.register_game(g2.pid, "pong", "Pong", frame={"id": "s1-win1-b", "screen": 1, "x": 10, "y": 20, "w": 500,
                                                             "h": 300, "vw": 640, "vh": 360, "sx": 960, "sy": 0, "dpr": 2})
            svc.register_game(g3.pid, "tetris", "Tetris")                      # launched without a frame
            r = await svc.list()
            snake = next(w for w in r["windows"] if w["id"] == 0x202)
            assert snake["frame"] == "s0-win1-a" and svc.frames["s0-win1-a"]["xid"] == 0x202, snake
            assert fx.wins[0x202]["props"].get(68) == (33, [0x101]), "transient for the kiosk of screen 0"
            assert (fx.wins[0x202]["x"], fx.wins[0x202]["y"]) == (120, 110), "centred in the body: 100+20, 80+30"
            assert (fx.wins[0x303]["props"].get(68), fx.wins[0x303]["x"], fx.wins[0x303]["y"]) == ((33, [0x102]), 2120, 100), \
                "screen 1 at 2x: body root (1940, 40, 1000, 600), the 640x480 game centred"
            assert next(w for w in r["windows"] if w["id"] == 0x404)["frame"] is None
            fr = {f["id"]: f for f in r["frames"]}
            assert fr["s0-win1-a"]["slug"] == "snake" and fr["s0-win1-a"]["pid"] == g1.pid and fr["s1-win1-b"]["screen"] == 1, fr
            # follow (the drag: no read-back) and place (verified)
            f1m = dict(f1, x=300, y=200)
            r = await svc.frame_op("s0-win1-a", "follow", f1m)
            assert r["ok"] and r["detail"]["verified"] is None and (fx.wins[0x202]["x"], fx.wins[0x202]["y"]) == (320, 230), r
            r = await svc.frame_op("s0-win1-a", "place", dict(f1m, w=960, h=540))
            d = r["detail"]
            assert r["ok"] and d["verified"] and (d["x"], d["y"]) == (300, 200) and d["fixed"] is True, r
            assert d["css"] == {"x": 0.0, "y": 0.0, "w": 960.0, "h": 540.0}, d["css"]
            r = await svc.frame_op("s0-win1-a", "place", {"x": 1, "y": 2})
            assert r["status"] == 400 and "x y w h" in r["error"], r
            # the snapshot: the game's own pixels + where they lie in the body
            r = await svc.frame_op("s0-win1-a", "snapshot")
            d = r["detail"]
            assert r["ok"] and len(d["image"]) == 960 * 540 * 4 and d["rect"] == [300, 200, 960, 540] and fx.grabbed[-1] == (300, 200, 960, 540)
            assert d["css"] == {"x": 0.0, "y": 0.0, "w": 960.0, "h": 540.0}, d
            # covered: out of the kiosk's group, restacked under it, frozen — still mapped; the keyboard stays put
            fx.active = 0x101
            r = await svc.frame_op("s0-win1-a", "cover")
            await asyncio.sleep(0.05)
            assert r["ok"] and r["detail"]["lowered"] and r["detail"]["frozen"] and state(g1) == "T", r
            assert fx.wins[0x202]["mapped"] and 68 not in fx.wins[0x202]["props"] and fx.active == 0x101, fx.wins[0x202]
            assert [m for m in fx.sent if m[0] == 0x202][-1][1:] == ("_NET_RESTACK_WINDOW", [2, 0x101, 1, 0, 0])
            await svc.list(); await asyncio.sleep(0.05)
            assert state(g1) == "T", "a snapshot must not thaw a covered (mapped) game"
            assert (await svc.frame_op("s0-win1-a", "focus"))["detail"] == {"held": True}
            r = await svc.frame_op("s0-win1-a", "uncover", {"focus": "keep"})
            await asyncio.sleep(0.05)
            assert r["ok"] and r["detail"]["raised"], r
            assert state(g1) == "S", ("not thawed", state(g1))
            assert fx.active == 0x101, ("the keyboard moved", hex(fx.active))
            assert fx.wins[0x202]["props"].get(68) == (33, [0x101]), fx.wins[0x202]["props"]
            assert any(m[0] == 0x202 and m[1] == "_NET_RESTACK_WINDOW" and m[2][:3] == [2, 0, 0] for m in fx.sent[-3:]), fx.sent[-3:]
            # the game had the keyboard when it was covered: it hands it to the desktop and gets it back ("keep")
            fx.active = 0x202
            await svc.frame_op("s0-win1-a", "cover")
            assert fx.active == 0x101, "the covering desktop gets the keyboard"
            await svc.frame_op("s0-win1-a", "uncover", {"focus": "keep"})
            assert fx.active == 0x202, "and the game gets it back"
            # minimized (the frame's _ / the dock): out of the group, iconified, frozen; back with the keyboard
            fx.active = 0x101
            await svc.frame_op("s0-win1-a", "minimize"); await asyncio.sleep(0.05)
            assert not fx.wins[0x202]["mapped"] and state(g1) == "T" and 68 not in fx.wins[0x202]["props"]
            r = await svc.frame_op("s0-win1-a", "restore", {"focus": True})
            assert r["ok"] and fx.wins[0x202]["mapped"] and fx.active == 0x202 and fx.wins[0x202]["props"].get(68) == (33, [0x101]), r
            # both flags: restore is held while still covered; uncover shows it
            await svc.frame_op("s0-win1-a", "cover"); await svc.frame_op("s0-win1-a", "minimize")
            r = await svc.frame_op("s0-win1-a", "restore", {"focus": True})
            assert r["detail"] == {"held": "covered"} and not fx.wins[0x202]["mapped"], r
            r = await svc.frame_op("s0-win1-a", "uncover", {"focus": True})
            await asyncio.sleep(0.05)
            assert r["ok"] and fx.wins[0x202]["mapped"] and state(g1) == "S", r
            # adopt: a running game into a new frame (a reloaded page); never a stranger
            r = await svc.frame_op("s0-win7-c", "adopt", {"xid": 0x404, "x": 0, "y": 40, "w": 820, "h": 620, "vw": 1920, "vh": 1080})
            assert r["ok"] and svc.frames["s0-win7-c"]["xid"] == 0x404 and svc.registry.lookup(g3.pid)["frame"] == "s0-win7-c", r
            assert (fx.wins[0x404]["x"], fx.wins[0x404]["y"]) == (10, 50)
            r = await svc.frame_op("s0-win8-d", "adopt", {"xid": 0x505, "x": 0, "y": 40, "w": 820, "h": 620, "vw": 1920})
            assert r["status"] == 400 and "not a game the kernel launched" in r["error"], r
            r = await svc.frame_op("s0-win9-e", "adopt", {"xid": 0x404, "x": 0, "y": 40, "w": 820, "h": 620, "vw": 1920})
            assert r["ok"] and "s0-win7-c" not in svc.frames, "a game is in one frame: the old record goes"
            # the end of a game is recorded on its frame; release drops a record
            svc.frame_ended(g2.pid, {"returncode": 1, "after_s": 3.0, "killed": False, "crash": "RuntimeError: boom"})
            e = next(f for f in svc.frames_out() if f["id"] == "s1-win1-b")["ended"]
            assert e["pid"] == g2.pid and e["crash"] == "RuntimeError: boom", e
            assert (await svc.frame_op("s1-win1-b", "release"))["ok"] and "s1-win1-b" not in svc.frames
            # errors
            assert (await svc.frame_op("nope", "cover"))["status"] == 404
            assert (await svc.frame_op("s0-win1-a", "explode"))["status"] == 400
            # close: the game ends (WM close) and the record goes
            r = await svc.frame_op("s0-win1-a", "close")
            assert r["ok"] and 0x202 not in fx.wins and "s0-win1-a" not in svc.frames, r
            # no X: the record still follows the shell, the answer says why
            old = h.hw.X11_SOCKETS; h.hw.X11_SOCKETS = Path(td) / "none"
            try:
                r = await svc.frame_op("s0-win9-e", "cover")
                assert r["status"] == 503 and svc.frames["s0-win9-e"]["covered"] is True, r
            finally:
                h.hw.X11_SOCKETS = old
            return ("paired on listing (transient for its screen's kiosk, centred; screen 1 at 2x), follow/place, "
                    "snapshot 960x540 + CSS offset, cover/uncover (keyboard handed back), minimize/restore, held while "
                    "covered, adopt (strangers refused, one frame per game), ended, release, close, no X -> 503")
        finally:
            h.restore()
            for p in procs:
                if p.poll() is None:
                    try:
                        os.killpg(p.pid, 18)
                    except OSError:
                        pass
                    p.kill()
                p.wait()


@run_test("http_kernel_stops_with_open_streams", ["R68", "R63", "R62"])
async def t_kernel_stop(_):
    """D-R68-3: `systemctl stop kairos` = SIGTERM while a client holds GET /api/windows/stream
    (the kiosk always does) and an app generation is mid-LLM-stream. A second kernel with
    its own mock engine: it must exit 0 within a few seconds (the VM saw 90 s + SIGKILL +
    a false failure capture), log no traceback, tell the app window (`error` event), and
    take its llama-server down with it."""
    import socket, subprocess, tempfile, urllib.request
    port, lport = 1340, 8083
    tmp = Path(tempfile.mkdtemp(prefix="kairos-stop-"))
    for d in ("bin", "models"):          # the suite's mock engines
        (tmp / d).symlink_to(Path(os.environ["VIBEOS_DIR"]) / d)
    env = dict(os.environ, VIBEOS_DIR=str(tmp), VIBEOS_PORT=str(port), VIBEOS_TOKEN_FILE=f"{tmp}/token",
               VIBEOS_LLAMA_PORT=str(lport))
    logf = open(tmp / "kernel.log", "wb")
    proc = subprocess.Popen([sys.executable, str(REPO / "kernel" / "server.py")], env=env, stdout=logf, stderr=subprocess.STDOUT)
    socks = []

    def raw(req):
        s = socket.create_connection(("127.0.0.1", port), timeout=60); s.sendall(req); socks.append(s); return s

    def read_until(s, needle, timeout):
        buf, t0 = b"", time.monotonic()
        s.settimeout(timeout)
        while needle not in buf:
            chunk = s.recv(4096)
            assert chunk, f"stream ended before {needle!r}: {buf[-300:]!r}"
            buf += chunk
            assert time.monotonic() - t0 < timeout, f"no {needle!r} within {timeout} s: {buf[-300:]!r}"
        return buf

    def drain(s):
        buf = b""; s.settimeout(3)
        try:
            while True:
                chunk = s.recv(65536)
                if not chunk:
                    return buf, True
                buf += chunk
        except (socket.timeout, OSError):
            return buf, False

    def log_tail():
        logf.flush(); return (tmp / "kernel.log").read_text(errors="ignore")[-1500:]
    try:
        for _ in range(80):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=1).read(); break
            except Exception:
                assert proc.poll() is None, log_tail()
                await asyncio.sleep(0.5)
        else:
            raise AssertionError("second kernel never answered: " + log_tail())
        # 1. the kiosk's window stream (no X in the suite: available:false, stays open)
        s1 = raw(b"GET /api/windows/stream HTTP/1.1\r\nHost: k\r\n\r\n")
        read_until(s1, b'"available": false', 10)
        # 2. an app generation mid-stream ("[slow]": the mock engine paces the tokens)
        body = json.dumps({"prompt": "[slow] a stopwatch"}).encode()
        s2 = raw(b"POST /api/app HTTP/1.1\r\nHost: k\r\nContent-Type: application/json\r\nContent-Length: "
                 + str(len(body)).encode() + b"\r\n\r\n" + body)
        read_until(s2, b"<!DOCTYPE", 60)         # the app's first token: the LLM stream is in flight
        t0 = time.monotonic(); proc.send_signal(15)
        try:
            rc = proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill(); proc.wait()
            raise AssertionError("kernel did not stop within 20 s of SIGTERM with 2 open streams: " + log_tail())
        dt = time.monotonic() - t0
        assert dt < 5.0, f"stop took {dt:.1f} s: " + log_tail()
        assert rc == 0, f"exit code {rc}: " + log_tail()
        text = (tmp / "kernel.log").read_text(errors="ignore")
        assert "Traceback" not in text, text[-1500:]
        assert "[kernel] shutdown:" in text and "2 stream(s) ended" in text, text[-800:]
        tail2, eof2 = drain(s2); tail1, eof1 = drain(s1)
        assert eof1 and eof2, "both streams must be closed"
        assert b"event: error" in tail2 and b"restarting" in tail2, tail2[-400:]
        for _ in range(20):                      # its engine went with it (stop_llama), no orphan
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{lport}/health", timeout=0.5).read()
                await asyncio.sleep(0.2)
            except Exception:
                break
        else:
            raise AssertionError("mock llama-server still answering after the kernel stopped")
        return f"SIGTERM with 2 open SSE streams -> exit 0 in {dt:.2f} s, no traceback, app told, engine gone"
    finally:
        for s in socks:
            s.close()
        if proc.poll() is None:
            proc.kill(); proc.wait()
        logf.close()
        shutil.rmtree(tmp, ignore_errors=True)


@run_test("unit_r68_fixes_static", ["R68", "R63", "R28"])
async def t_r68_fixes_static(_):
    """The VM test's fixes (docs/VM-TEST-2026-09-10.md, R68 verification): the unit's stop
    is bounded (TimeoutStopSec) and never SIGKILL-timed-out by an SSE holder (shutdown
    hook, shutdown_timeout), games get their own process group, the taskbar strip is
    laid out beside/above the dock and chips ellipsize, the root cursor is blanked, the
    kiosk re-reads runtime flags per launch, the rig's x11vnc unit waits for X."""
    import re, subprocess
    inc = REPO / "iso" / "config" / "includes.chroot"
    unit = (inc / "etc" / "systemd" / "system" / "kairos.service").read_text()
    m = re.search(r"^TimeoutStopSec=(\d+)$", unit, re.M)
    assert m and 12 <= int(m.group(1)) <= 60, "a stop must be bounded well under the 90 s default, above the kernel's ~12 s worst case"
    assert "OnFailure=kairos-bootlog-failure@%n.service" in unit
    srv = (REPO / "kernel" / "server.py").read_text()
    assert "shutdown_timeout=5.0" in srv and "app.on_shutdown.append(shutdown)" in srv
    assert srv.count("LIVE_STREAMS[task]") >= 2, "every SSE handler registers itself"
    assert "wm.WM.close_streams()" in srv and "wm.WM.shutdown()" in srv and "stop_llama(grace=3)" in srv, "the hook ends streams, games, the engine"
    assert "start_new_session=True" in srv.split("async def h_game")[1].split("async def _reap_game")[0]
    wmsrc = (REPO / "kernel" / "wm.py").read_text()
    assert "def _lost(" in wmsrc and "socket_error" in wmsrc and "self._xgen += 1" in wmsrc
    assert "signal.SIGSTOP" in wmsrc and "signal.SIGCONT" in wmsrc and "daemon=True" in wmsrc
    assert not re.search(r"except Exception:\s*#.*BadWindow.*\n\s*return None", wmsrc), "prop() must not swallow a dead connection"
    xinit = (inc / "home" / "kairos" / ".xinitrc").read_text()
    subprocess.run(["sh", "-n", str(inc / "home" / "kairos" / ".xinitrc")], check=True)
    assert 'xsetroot -cursor "$BLANK" "$BLANK"' in xinit and "root_cursor_off" in xinit
    assert "/etc/kairos/chromium.flags" in xinit and "/run/kairos/chromium.flags" in xinit
    assert xinit.count("$CHROME_FLAGS $extra") == 2, "both kiosk launches take the runtime flags"
    css = (REPO / "shell" / "shell.css").read_text(); js = (REPO / "shell" / "shell.js").read_text()
    assert "#taskbar.stacked" in css and re.search(r"\.task-t \{[^}]*min-width: 0", css) and re.search(r"\.task-t \{[^}]*text-overflow: ellipsis", css)
    assert "function layoutTaskbar()" in js and js.count("layoutTaskbar();") >= 3 and 'addEventListener("resize", layoutTaskbar)' in js
    rig = Path(REPO.parent.parent / "pod" / "testrig" / "gpu-display-setup.sh")
    if rig.exists():
        u = re.search(r"kairos-x11vnc.service <<'EOF'\n(.*?)\nEOF", rig.read_text(), re.S).group(1)
        assert "StartLimitBurst=" in u and "RestartSec=5" in u and "/tmp/.X11-unix/X0" in u.split("ExecStartPre=")[1].split("\n")[0]
    return "TimeoutStopSec bounded, shutdown hook + 5 s handler grace, games own group, taskbar layout, root cursor, flag files, rig unit"


@run_test("unit_desktop_hidden_linux_r68_static", ["R68", "R28"])
async def t_desktop_r68_static(_):
    """R68 (b)-(e) statically: the kiosk's openbox config ships decoration-free (no titlebar
    on a game window), with no root/desktop menu and no key bindings, and .xinitrc starts the
    session that reads it; every page the kiosk shows suppresses Chromium's context menu
    (desktop, injected bridge = every app iframe, /web/ pages, the boot-wait page);
    --isolate-sandboxed-iframes (per-document) is on the kiosk's Chromium (F11); python3-xlib
    is in the image; the kiosk user owns its home; the search box no longer floats over
    windows while idle; closing a window runs the release sequence; game chips are fed by SSE."""
    import re, xml.etree.ElementTree as ET
    iso = REPO / "iso"; inc = iso / "config" / "includes.chroot"; home = inc / "home" / "kairos"
    rc = home / ".config" / "openbox" / "rc.xml"
    assert rc.exists(), "the kiosk's openbox rc.xml must ship in the image (R28: a game window had a Linux titlebar)"
    root = ET.parse(rc).getroot(); ns = {"ob": "http://openbox.org/3.4/rc"}
    q = lambda path: root.find(path, ns)
    apps = root.findall("ob:applications/ob:application", ns)
    assert apps and any(a.get("class") == "*" and (a.find("ob:decor", ns).text or "").strip() == "no" for a in apps), "decor must be off for every client"
    assert q("ob:theme/ob:keepBorder").text.strip() == "no"
    menu = q("ob:menu"); assert menu is not None and len(menu) == 0 and not (menu.text or "").strip(), "no root menu"
    for ctx in ("Root", "Desktop"):
        el = next((c for c in root.findall("ob:mouse/ob:context", ns) if c.get("name") == ctx), None)
        assert el is not None and len(el) == 0, f"right/middle-click on the {ctx} must do nothing"
    assert not root.findall("ob:mouse/ob:context[@name='Frame']", ns) and not root.findall("ob:mouse/ob:context[@name='Titlebar']", ns), "no frame/titlebar bindings (Alt-drag of the kiosk)"
    assert len(q("ob:keyboard")) == 0, "no key bindings (Alt-Tab popup, run dialogs)"
    assert q("ob:desktops/ob:number").text.strip() == "1"
    bench = (REPO / "bench" / "proto" / "openbox-rc.xml").read_text()
    assert "<decor>no</decor>" in bench and "<menu></menu>" in bench, "derived from the measured bench config"
    xinit = (home / ".xinitrc").read_text()
    assert re.search(r"^openbox-session &", xinit, re.M) or "--config-file" in xinit, "openbox-session reads ~/.config/openbox/rc.xml"
    flags = re.search(r'^CHROME_FLAGS="(.*?)"', xinit, re.M | re.S).group(1).replace("\\\n", " ").split()
    assert "--isolate-sandboxed-iframes" in flags, "F11: one runaway app froze every other app (measured) — isolate them"
    assert any(f.startswith("--enable-features=") and "IsolateSandboxedIframes:grouping/per-document" in f for f in flags), \
        "per-origin grouping (Chromium's default) keeps all apps of one kiosk in ONE process"
    pkgs = (iso / "config" / "package-lists" / "kairos.list.chroot").read_text()
    assert re.search(r"^python3-xlib$", pkgs, re.M), "kernel/wm.py needs python3-xlib in the image"
    hook = (iso / "config" / "hooks" / "normal" / "0200-kairos-system.hook.chroot").read_text()
    assert re.search(r"^chown -R \$\{KIOSK_USER\}: /home/\$\{KIOSK_USER\}", hook, re.M), "the kiosk user must own ~/.config/openbox/rc.xml"
    # context menu: desktop page, the bridge (every app iframe), /web/ pages, boot-wait
    js = (REPO / "shell" / "shell.js").read_text()
    bridge = re.search(r"const BRIDGE_SRC = `(.*?)`;\n", js, re.S).group(1)
    handler = 'addEventListener("contextmenu", (e) => e.preventDefault(), true)'
    assert handler in bridge, "apps: the injected bridge suppresses the menu inside the sandboxed iframe"
    assert handler in js.replace(bridge, ""), "desktop page: document-level contextmenu handler"
    srv = (REPO / "kernel" / "server.py").read_text()
    assert "NO_CONTEXT_MENU" in srv and srv.count("no_context_menu(html") >= 3 and "{refresh}{NO_CONTEXT_MENU}" in srv, "/web/ + vibe.browse pages"   # [R83] (html, report) on /web/
    assert 'addEventListener("contextmenu"' in (REPO / "shell" / "boot-wait.html").read_text()
    sys.path.insert(0, str(REPO / "kernel")); import server
    out = server.no_context_menu("<!DOCTYPE html><html><head><title>x</title></head><body></body></html>")
    assert out.index("contextmenu") < out.index("<title>") and out.count("<head>") == 1
    assert server.no_context_menu("<p>bare</p>").startswith("<script>")
    # search box: under windows while idle, above them only while focused (§8.9)
    css = (REPO / "shell" / "shell.css").read_text()
    idle = int(re.search(r"#dsearch \{[^}]*z-index: (\d+)", css).group(1))
    focused = int(re.search(r"#dsearch:focus-within \{[^}]*z-index: (\d+)", css).group(1))
    assert idle < 10 <= 55 <= focused, (idle, focused)
    # closeWin release sequence + game chips over SSE
    for needle in ("function closeWin(win)", "function releaseFrame(win)", 'f.src = "about:blank"', 'removeAttribute("srcdoc")',
                   "windows.delete(win.id)", "clearInterval(win.iconTimer)", "win.abort.abort()", "onclick = () => closeWin(win)"):
        assert needle in js, needle
    assert 'new EventSource("/api/windows/stream")' in js and "function renderXWindows" in js and "monitorsFromKernel(d.monitors)" in js
    for op in ("raise", "minimize", "close"):
        assert f'xwinOp(w.id, "{op}")' in js, op
    assert "watchXWindows();" in js and js.index("watchXWindows();") > js.index("(async function boot()")
    return "rc.xml decor off/no menu/no keys + openbox-session; contextmenu on 4 page classes; F11 flags; python3-xlib; home owned; search z-index idle < win; closeWin release; SSE chips"


@run_test("unit_requirements_traceability", ["R26"])
async def t_traceability(_):
    """REQUIREMENTS.md, the test suite and PROGRESS.md must agree [R26]:
    no numbering holes, every test cites a documented id, every documented id gets a
    PROGRESS.md row, and the summary denominator is the real requirement count.
    PROGRESS.md on disk is the PREVIOUS run's output (run-tests.sh regenerates it after
    this suite) — so a newly added requirement fails once, then passes after regeneration."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("upd", REPO / "scripts" / "update-progress.py")
    upd = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(upd)
    req_doc = (REPO / "docs" / "REQUIREMENTS.md").read_text(encoding="utf-8")
    ids, titles, notes = upd.parse_requirements(req_doc)
    assert len(ids) >= 65, f"only {len(ids)} requirements parsed"
    holes = upd.numbering_holes(ids)
    assert not holes, f"REQUIREMENTS.md skips ids: {holes}"
    # every test in this file cites ids the document defines (the R42/R43 class of bug)
    cited = {r for v in globals().values() if getattr(v, "_test", False) for r in v._reqs}
    unknown = sorted(cited - set(ids), key=lambda x: int(x[1:]))
    assert not unknown, f"tests cite undocumented requirement ids: {unknown}"
    # the generator, fed this run's results so far, renders one row per id + honest totals
    lines, c = upd.render(ids, titles, notes, RESULTS, "test")
    rows = {ln.split("|")[1].strip() for ln in lines if ln.startswith("| R")}
    assert rows == set(ids), f"rows != requirements: {rows ^ set(ids)}"
    assert c["total"] == len(ids) and c["impl"] + c["partial"] + c["external"] + c["unclaimed"] == c["total"]
    assert c["tested"] + c["untested"] + c["skip"] + c["fail"] == c["total"]
    summary = next(ln for ln in lines if ln.startswith("**Summary:**"))
    assert f"/{len(ids)} requirements implemented" in summary and "no automated test at all" in summary
    assert "Traceability warnings" not in "\n".join(lines)
    # the on-disk progress document must not have dropped any requirement
    prog = (REPO / "docs" / "PROGRESS.md").read_text(encoding="utf-8")
    disk_rows = {ln.split("|")[1].strip() for ln in prog.splitlines() if ln.startswith("| R")}
    missing = sorted(set(ids) - disk_rows, key=lambda x: int(x[1:]))
    assert not missing, f"docs/PROGRESS.md lacks rows for {missing} (regenerate it)"
    assert f"/{len(ids)} requirements implemented" in prog, "PROGRESS.md summary denominator is stale"
    assert "wsl -d Debian" not in prog, "PROGRESS.md still tells you to run the suite under WSL"
    untested = [r for r in ids if r not in {q for v in RESULTS.values() for q in v["reqs"]}]
    return (f"{len(ids)} requirements, no holes, all cited ids documented, "
            f"{len(untested)} without a test so far this run")



@run_test("unit_game_launch_gate_mapped_window", ["R7", "R11", "R68"])
async def t_game_gate(_):
    """[D-R68-6] POST /api/game says running:true only once the game's window has MAPPED
    on the kiosk display (kernel/wm.py's snapshot, woken by the watcher) and the process
    outlived its first frames; a process that exits first is "exited" at once, one that
    never maps is "no-window" at the bound (an iconified window does not count); with X
    but no EWMH WM, or no X at all (dev container, tests), the old survival probe decides.
    A post-success exit is a crash record only for a real crash — not exit 0, not an end
    the kernel asked for (close escalation / shutdown mark the pid) — and the record
    renders as the bug report the forge gets on the relaunch [R45]. Real children stand
    in for games (sh), the fake X for the display."""
    import tempfile
    sys.path.insert(0, str(REPO / "kernel"))
    import server
    procs = []

    async def child(cmd):
        p = await asyncio.create_subprocess_exec("sh", "-c", cmd, stderr=asyncio.subprocess.PIPE,
                                                 start_new_session=True)
        procs.append(p)
        return p

    async def gate(p, **kw):
        t0 = time.monotonic()
        v, win = await server.game_gate(p, **kw)
        return v, win, time.monotonic() - t0
    with tempfile.TemporaryDirectory() as td:
        h = _WMHarness(td); wm = h.wm
        svc, old_wm = wm.WindowService(), wm.WM
        wm.WM = svc                                    # server.game_gate asks wm.WM
        try:
            fx = h.new_server()
            # 1. the window maps 0.3 s after launch -> "mapped" as soon as a snapshot shows it
            p = await child("sleep 30"); svc.register_game(p.pid, "snake", "Snake")

            async def map_later():
                await asyncio.sleep(0.3)
                fx.add(0x202, "pygame", "pygame", "Snake", 32, 114, 960, 540, pid=p.pid)
                svc._notify()                          # what the watcher thread does on MapNotify
            asyncio.create_task(map_later())
            v, win, dt = await gate(p, map_timeout=5, first_frames=0.2)
            assert v == "mapped" and win["id"] == 0x202 and win["pid"] == p.pid and win["app"] == "snake" and dt < 2.5, (v, win, dt)
            g = svc.registry.lookup(p.pid); assert g["win"] == 0x202 and g["mapped_at"] > 0, g
            # 2. exits (rc 3) before any window -> "exited" promptly, returncode set
            p = await child("sleep 0.3; exit 3"); svc.register_game(p.pid, "pong")
            v, win, dt = await gate(p, map_timeout=5)
            assert v == "exited" and win is None and p.returncode == 3 and dt < 2.0, (v, dt)
            # 3. alive, display answering, nothing maps -> "no-window" at the bound; the process is the caller's
            p = await child("sleep 30"); svc.register_game(p.pid, "tetris")
            v, win, dt = await gate(p, map_timeout=0.6)
            assert v == "no-window" and win is None and 0.5 <= dt < 1.6 and p.returncode is None, (v, dt)
            # 3b. a window that exists but is iconified does not count; mapped -> it does
            fx.add(0x303, "pygame", "pygame", "Tetris", 0, 0, 640, 480, pid=p.pid)
            fx.wins[0x303]["mapped"] = False; fx.wins[0x303]["states"].add("_NET_WM_STATE_HIDDEN")
            v, win, dt = await gate(p, map_timeout=0.6); assert v == "no-window", v
            fx.wins[0x303]["mapped"] = True; fx.wins[0x303]["states"].discard("_NET_WM_STATE_HIDDEN")
            v, win, dt = await gate(p, map_timeout=2, first_frames=0.1); assert v == "mapped" and win["id"] == 0x303, v
            # 4. X up but no EWMH WM keeps _NET_CLIENT_LIST (openbox not up yet): the survival probe decides
            fx.no_wm = True
            p = await child("sleep 30"); v, win, dt = await gate(p, map_timeout=0.3, survival=0.6)
            assert v == "survived" and win is None and 0.5 <= dt < 1.5, (v, dt)
            p = await child("sleep 0.2; exit 1"); v, win, dt = await gate(p, map_timeout=0.3, survival=0.6)
            assert v == "exited" and p.returncode == 1, v
            fx.no_wm = False
            # 5. no X at all (dev container, test suite): the survival probe, promptly
            (Path(td) / "X0").unlink()
            assert svc.x_available() is False
            p = await child("sleep 30"); v, win, dt = await gate(p, survival=0.5)
            assert v == "survived" and 0.4 <= dt < 1.5, (v, dt)
            p = await child("sleep 0.2; exit 2"); v, win, dt = await gate(p, survival=1.0)
            assert v == "exited" and p.returncode == 2 and dt < 0.9, (v, dt)
            # 6. the crash record: exit 0 and kernel-ended exits are not crashes; a real one carries it all
            now = time.time()
            assert server.game_exit_record("g", "G", 1, 1, 1, 0, "bye", now, "mapped", 0x1) is None
            assert server.game_exit_record("g", "G", 1, 1, 1, -9, "", now, "mapped", 0x1, killed=True) is None
            rec = server.game_exit_record("g", "G", 2, 1, 4242, 1, "Traceback (most recent call last):\n  File x\nNameError: name 'foo' is not defined\n",
                                          now - 7.4, "mapped", 0x202)
            assert rec["returncode"] == 1 and rec["signal"] is None and 7.3 <= rec["after_s"] <= 8 and rec["version"] == 2 \
                and rec["summary"].startswith("NameError") and rec["window"] == 0x202 and rec["id"].startswith("4242-"), rec
            rec2 = server.game_exit_record("g", "G", 1, 1, 7, -11, "", now, "survived", None)
            assert rec2["signal"] == "SIGSEGV" and "SIGSEGV" in rec2["summary"], rec2
            txt = server.format_game_crash_report(rec, "g", "a snake game", 2)
            for need in ("BUG REPORT", "g (v2)", "a snake game", "after its window had opened", "7.4 s",
                         "exited with code 1", "NameError: name 'foo'", "Fix the root cause"):
                assert need in txt, f"crash report lacks {need!r}:\n{txt}"
            # 7. the kernel ending a game marks it (shutdown here; the close op's escalation does the same)
            p = await child("sleep 30"); svc.register_game(p.pid, "ending")
            assert svc.registry.lookup(p.pid)["killed"] is False
            await svc.shutdown()
            assert svc.registry.lookup(p.pid)["killed"] is True and await p.wait() == -15
            # 8. the record is pushed with the window list (the desktop toasts it), stderr stays out
            svc.note_crash({k: v for k, v in rec.items() if k != "stderr"})
            r = await svc.list(); assert r["available"] is False and r["crashed"][0]["id"] == rec["id"] and "stderr" not in r["crashed"][0]
            return ("mapped in %.1f s / exited / no-window at the bound / iconified ignored / no WM and no X -> survival "
                    "probe; record: exit 0 + kernel-ended = none, code/signal/summary kept; report formatted; pushed with the list" % dt)
        finally:
            wm.WM = old_wm
            h.restore()
            for p in procs:
                if p.returncode is None:
                    try:
                        p.kill()
                    except ProcessLookupError:
                        pass
                    await p.wait()


@run_test("http_game_crash_after_running_is_recorded", ["R7", "R11", "R45", "R68"])
async def t_game_crash_record(s):
    """[D-R68-6] Against the mock kernel there is no X: the launch gate's survival-probe
    fallback is what runs, and the job says so (gate:"survived"). (1) a game that crashes
    inside the gate is self-repaired with its traceback — the mock forge returns a healthy
    game only when a TRACEBACK reached it — and reported running at attempt 2 (the
    evolve-path shape of the VM evidence); (2) a game that dies AFTER the job reported it
    running is recorded — journal line, games/<slug>/meta.json last_crash, the job's
    result (running:false, ended, crash), GET /api/apps games[], the desktop-search entry
    — and NOT repaired on its own [R35]; (3) the relaunch hands the record to the model
    as a BUG REPORT [R45] (the mock returns the healthy game only then), the new version
    starts with the record cleared, and its clean end (SIGTERM = SDL QUIT = exit 0) is
    an end, not a crash."""
    try:
        import pygame  # noqa: F401
    except ImportError:
        raise SkipTest("pygame not installed yet (arrives with engine install)")
    import signal as _signal

    async def forge(prompt, **extra):
        async with s.post(f"{BASE}/api/game", json=dict({"prompt": prompt}, **extra)) as r:
            jid = (await r.json())["job"]
        return jid, (await poll_job(s, jid, timeout=120))["result"]

    async def job(jid):
        async with s.get(f"{BASE}/api/job/{jid}") as r:
            return await r.json()

    async def wait_result(jid, key, tries=60):
        for _ in range(tries):
            j = await job(jid)
            if j["result"].get(key):
                return j
            await asyncio.sleep(0.25)
        raise AssertionError(f"job {jid}: no {key!r} in its result within the wait: {j}")
    log = lambda: (VDIR / "kernel.log").read_text(errors="replace")
    # (1) crash in the headless play-test [R77] (before any launch) -> repaired with the traceback -> running at attempt 2
    jid1, r1 = await forge("test fragile ball [dies after 2s]")
    assert r1["running"] is True and r1["attempt"] == 2 and r1["gate"] == "survived" and r1["window"] is None \
        and r1["version"] == 1, r1
    assert "dies after" not in (VDIR / "games" / r1["slug"] / "main.py").read_text(), "the repaired source must be the healthy one"
    assert "game crashed (attempt 1, play-test: RuntimeError: mock crash after 2 s), self-repairing" in log()
    repairs = log().count("self-repairing")
    # (2) dies 8 s in: after the 6 s probe reported it running
    jid2, r2 = await forge("test crashy ball [dies after 8s]")
    slug, gdir = r2["slug"], VDIR / "games" / r2["slug"]
    assert r2["running"] is True and r2["attempt"] == 1 and r2["gate"] == "survived" and r2["pid"] > 0, r2
    meta = json.loads((gdir / "meta.json").read_text())
    assert meta["version"] == 1 and meta["gate"] == "survived" and "last_crash" not in meta, meta
    j = await wait_result(jid2, "crash")
    crash, res = j["result"]["crash"], j["result"]
    assert res["running"] is False and res["ended"]["returncode"] == 1 and res["ended"]["killed"] is False, res
    assert crash["returncode"] == 1 and crash["signal"] is None and crash["pid"] == r2["pid"] and crash["slug"] == slug \
        and crash["version"] == 1 and crash["gate"] == "survived" and crash["after_s"] >= 7, crash
    assert "RuntimeError: mock crash after 8 s" in crash["stderr"] and crash["summary"].startswith("RuntimeError"), crash
    meta = json.loads((gdir / "meta.json").read_text())
    assert meta["last_crash"]["id"] == crash["id"] and meta["crashes"] == 1 and meta["prompt"] == "test crashy ball [dies after 8s]", meta
    async with s.get(f"{BASE}/api/apps") as r:
        apps = await r.json()
    g = next(x for x in apps["games"] if x["slug"] == slug)
    assert g["last_crash"]["summary"] == crash["summary"] and g["version"] == 1 and g["launched"] > 0, g
    async with s.get(f"{BASE}/api/desktop-search", params={"q": "crashy"}) as r:
        ds = await r.json()
    hit = next(x for x in ds["results"] if x["group"] == "games" and "crashy" in x["title"])
    assert "crashed after" in hit["sub"] and "RuntimeError" in hit["sub"] and "✨ improve" in hit["sub"], hit
    txt = log()
    assert f"game {slug} v1 (pid {r2['pid']}) crashed after the job reported it running: exit code 1 after" in txt \
        and "no automatic repair" in txt, "journal line missing"
    assert txt.count("self-repairing") == repairs, "a crash after success must not start a repair on its own [R35]"
    # (3) ✨ improve carries the bug report (since R82 a plain launch runs the current version): the healthy game
    # comes back, the record is cleared
    jid3, r3 = await forge("test crashy ball [dies after 8s]", evolve=True)
    assert r3["running"] is True and r3["attempt"] == 1 and r3["version"] == 2 and r3["pid"] != r2["pid"], r3
    assert "dies after" not in (gdir / "main.py").read_text(), "without the BUG REPORT the mock forge returns the crashing game again"
    assert (gdir / "history" / "v1.py").read_text().count("dies after 8s") == 1
    meta = json.loads((gdir / "meta.json").read_text())
    assert "last_crash" not in meta and meta["version"] == 2 and meta["crashes"] == 1, meta
    assert f"game {slug}: v1 crashed last time (RuntimeError: mock crash after 8 s) — the bug report goes to the forge" in log()
    async with s.get(f"{BASE}/api/apps") as r:
        assert next(x for x in (await r.json())["games"] if x["slug"] == slug)["last_crash"] is None
    # a clean end is an end, not a crash: SIGTERM -> SDL QUIT event -> exit 0
    os.kill(r3["pid"], _signal.SIGTERM)
    j = await wait_result(jid3, "ended")
    assert j["result"]["ended"]["returncode"] == 0 and "crash" not in j["result"] and j["result"]["running"] is False, j["result"]
    assert "last_crash" not in json.loads((gdir / "meta.json").read_text())
    assert f"game {slug} (pid {r3['pid']}) ended after" in log()
    return (f"in-gate crash repaired with its traceback (attempt 2); post-success crash {crash['after_s']} s in -> "
            "journal + meta.json + job + /api/apps + desktop search, no auto-repair; ✨ improve got the BUG REPORT, "
            "v2 healthy, record cleared; SIGTERM end = exit 0, no record")


@run_test("http_game_launch_runs_improve_evolves_versions", ["R82", "R7", "R35", "R77", "R86"])
async def t_game_r82(s):
    """[R82] launch = run, ✨ = evolve. Against the suite kernel (mock forge, no X: the survival probe): a new game is
    forged once (forge:true); POST /api/game for an existing game RUNS its current version — no LLM request, no new
    version, main.py untouched; while it runs the same call answers at once (already); ✨ (evolve + instruction) sends
    the USER IMPROVEMENT REQUEST + the previous source to the forge, v2 passes the R77 checks, ends the running v1
    (asked for: no crash record) and takes its place; GET /api/game/versions lists v1/v2 with the instruction;
    POST /api/game/version brings v1 back (v2 kept in history/), restart runs it; the next ✨ builds v3 from v1; the
    minors block runs on the instruction at the entry (400, no LLM request); unknown slugs/versions 404/400; a slug
    the shell sends back is taken verbatim (a 48-char slug may end in "-")."""
    try:
        import pygame  # noqa: F401
    except ImportError:
        raise SkipTest("pygame not installed yet (arrives with engine install)")
    import signal as _signal
    msglog = VDIR / "llama-msgs.log"
    msgs = lambda: [json.loads(ln) for ln in msglog.read_text().splitlines()] if msglog.exists() else []
    prompt = f"r82 neon bouncer {int(time.time()) % 100000}"
    sys.path.insert(0, str(REPO / "kernel")); import server
    slug = server.slugify(prompt)
    gdir = VDIR / "games" / slug
    # the slug the shell sends back is the game's own, verbatim — slugify cuts at 48 chars after stripping, so a slug can
    # end in "-" (the GPU check's ✨ forged a NEW game when the kernel slugified it again); never a path
    cut = server.slugify("neon asteroid miner: steer a small ship with the arrow keys, mine glowing asteroids")
    assert cut.endswith("-") and server.game_slug(cut) == cut and server.slugify(cut) != cut, cut
    assert server.game_slug("../../etc") == server.slugify("../../etc") and "/" not in server.game_slug("a/b")

    async def post(body, timeout=120):
        async with s.post(f"{BASE}/api/game", json=body) as r:
            j = await r.json()
            if r.status != 200:
                return r.status, j
        return 200, dict(j, result=(await poll_job(s, j["job"], timeout=timeout))["result"])
    pids = []
    try:
        st, j = await post({"prompt": prompt})
        r1 = j["result"]
        assert st == 200 and j["forge"] is True and r1["running"] and r1["version"] == 1 and r1["slug"] == slug, j
        pids.append(r1["pid"])
        v1 = (gdir / "main.py").read_text()
        n = len(msgs())
        st, j = await post({"slug": slug})
        assert st == 200 and j["forge"] is False and j["result"]["already"] is True and j["result"]["pid"] == r1["pid"], j
        os.killpg(r1["pid"], _signal.SIGTERM)
        await asyncio.sleep(1.0)
        st, j = await post({"slug": slug})
        r2 = j["result"]
        assert st == 200 and j["forge"] is False and r2["running"] and r2["launched_only"] and r2["version"] == 1 \
            and r2["pid"] != r1["pid"], j
        pids.append(r2["pid"])
        assert len(msgs()) == n, "a launch must not ask the LLM (launch = run the current version)"
        assert (gdir / "main.py").read_text() == v1, "a launch must not change the game"
        assert not (gdir / "history").exists() or not list((gdir / "history").glob("v*.py")), "a launch makes no version"
        # ✨ improve with a request
        st, j = await post({"slug": slug, "evolve": True, "instruction": "make the ball green"})
        r3 = j["result"]
        assert st == 200 and j["forge"] is True and r3["running"] and r3["version"] == 2 and r3["evolved_from"] == 1 \
            and r3["instruction"] == "make the ball green", j
        pids.append(r3["pid"])
        forge = [m for m in msgs()[n:] if "game forge" in m["system"]]
        assert forge and "USER IMPROVEMENT REQUEST: make the ball green" in forge[0]["user"] \
            and "PREVIOUS VERSION SOURCE (v1):" in forge[0]["user"] and "TOP priority" in forge[0]["user"], forge[:1]
        assert "mock game · make the ball green" in (gdir / "main.py").read_text(), (gdir / "main.py").read_text()[:300]
        assert (gdir / "history" / "v1.py").read_text() == v1, "history/v1.py is not v1"
        assert server.wm._proc_state(r2["pid"]) in ("", "Z"), "the running v1 ended before v2 took its place"
        meta = json.loads((gdir / "meta.json").read_text())
        assert meta["version"] == 2 and "last_crash" not in meta and meta["versions"]["2"]["instruction"] == "make the ball green", meta
        async with s.get(f"{BASE}/api/game/versions", params={"slug": slug}) as r:
            vs = await r.json()
        assert vs["current"] == 2 and [v["v"] for v in vs["versions"]] == [1, 2] and vs["versions"][1]["instruction"] == "make the ball green" \
            and vs["versions"][0]["by"] == "forged" and vs["running"] is True, vs
        # the way back: v1 is the game again, v2 kept; restart runs it in place of the running v2
        async with s.post(f"{BASE}/api/game/version", json={"slug": slug, "version": 1}) as r:
            rv = await r.json()
        assert rv["current"] == 1 and [v["v"] for v in rv["versions"]] == [1, 2], rv
        assert (gdir / "main.py").read_text() == v1 and "make the ball green" in (gdir / "history" / "v2.py").read_text()
        st, j = await post({"slug": slug, "restart": True})
        r4 = j["result"]
        assert st == 200 and j["forge"] is False and r4["version"] == 1 and r4["pid"] != r3["pid"], j
        pids.append(r4["pid"])
        assert "last_crash" not in json.loads((gdir / "meta.json").read_text()), "a restart's end is not a crash"
        # the next ✨ builds v3 from v1
        n = len(msgs())
        st, j = await post({"slug": slug, "evolve": True, "instruction": "add a second ball"})
        assert st == 200 and j["result"]["version"] == 3, j
        pids.append(j["result"]["pid"])
        forge = [m for m in msgs()[n:] if "game forge" in m["system"]]
        assert forge and "PREVIOUS VERSION SOURCE (v1):" in forge[0]["user"] and "add a second ball" in forge[0]["user"], \
            [m["user"][:200] for m in forge]
        # errors: the minors block on the instruction (no LLM request), unknown slug / version
        n = len(msgs())
        st, j = await post({"slug": slug, "evolve": True, "instruction": "hentai game with a loli"})
        assert st == 400 and j.get("kind") == "blocked" and len(msgs()) == n, (st, j)
        async with s.post(f"{BASE}/api/game/version", json={"slug": slug, "version": 9}) as r:
            assert r.status == 404, ("version 9", r.status, await r.text())
        async with s.post(f"{BASE}/api/game/version", json={"slug": slug, "version": "x"}) as r:
            assert r.status == 400, ("version x", r.status, await r.text())
        async with s.get(f"{BASE}/api/game/versions", params={"slug": "no-such-game-r82"}) as r:
            assert r.status == 404, ("unknown slug", r.status, await r.text())
        async with s.post(f"{BASE}/api/frames/nope/cover", json={}) as r:
            assert r.status in (404, 503), ("unknown frame", r.status, await r.text())
    finally:
        for pid in pids:                         # SIGTERM = SDL's QUIT: an end, not a crash record
            try:
                os.killpg(pid, _signal.SIGTERM)
            except (ProcessLookupError, PermissionError, TypeError):
                pass
    return ("forged once; a launch ran v1 with 0 LLM requests (already-running answered at once); ✨ 'make the ball "
            "green' -> v2 from the request + v1 source, v1 ended (no crash); versions v1/v2; revert to v1 + restart; "
            "next ✨ -> v3 from v1; minors block on the instruction 400; 404/400 for unknown slug/version")


@run_test("unit_knowledge_base_wikipedia_mini", ["R32"])
async def t_knowledge_base(_):
    """[R32] every tier ships the full-English-Wikipedia intro ZIM (wikipedia_en_all_mini,
    ~12 GB — not the Simple-English one it replaced), the downloader resolves that ZIM
    from the manifest's dir_url + zim_pattern, and the kernel's Knowledge organ serves
    exactly the ZIMs under $VIBEOS_DIR/knowledge through kiwix-serve. Hermetic: a fake
    kiwix-serve records its argv."""
    import importlib, re, subprocess, tempfile
    mans = sorted((REPO / "manifests").glob("tier-*.json"))
    assert len(mans) >= 4, mans
    for m in mans:
        k = json.loads(m.read_text()).get("knowledge") or {}
        assert k.get("zim_pattern", "").startswith("wikipedia_en_all_mini_"), f"{m.name}: {k}"
        assert k.get("dir_url") == "https://download.kiwix.org/zim/wikipedia/", m.name
        assert 10 <= float(k.get("approx_gb", 0)) <= 16, f"{m.name}: approx_gb {k.get('approx_gb')} is not ~12 GB"
        assert "nopic" in k.get("upgrade_note", ""), f"{m.name}: the ~49 GB full-text upgrade must stay documented"
        assert re.match(k["zim_pattern"], "wikipedia_en_all_mini_2026-06.zim"), k["zim_pattern"]
        assert not re.match(k["zim_pattern"], "wikipedia_en_simple_all_mini_2026-06.zim"), "the old Simple English ZIM must not match"
        assert not re.match(k["zim_pattern"], "wikipedia_en_all_nopic_2026-06.zim"), "the pattern selects the mini ZIM only"
    dl = REPO / "scripts" / "download-knowledge.sh"
    subprocess.run(["bash", "-n", str(dl)], check=True)
    body = dl.read_text()
    assert 'k["dir_url"], k["zim_pattern"]' in body and "grep -oE" in body and "tail -1" in body and "/opt/vibeos/knowledge" in body
    assert 'rsync -aL "$KNOW"/*.zim "$MNT/opt/vibeos/knowledge"/' in (REPO / "iso" / "build-img.sh").read_text(), "the image build must stage the ZIM (-L: a symlinked ZIM as its target)"
    sys.path.insert(0, str(REPO / "kernel"))
    knowledge = importlib.import_module("knowledge")
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); (t / "bin").mkdir(); (t / "knowledge").mkdir()
        kb = knowledge.Knowledge(t)
        assert kb.zim_dir == t / "knowledge" and kb.bin == t / "bin" / "kiwix-serve" and kb.has_local is False
        await kb.start_kiwix()
        assert kb.proc is None and kb.books == [], "no ZIM: offline search disabled, nothing launched"
        zim = t / "knowledge" / "wikipedia_en_all_mini_2026-06.zim"; zim.write_bytes(b"ZIM")
        argv = t / "argv"
        (t / "bin" / "kiwix-serve").write_text(f'#!/bin/sh\necho "$@" > "{argv}"\nexec sleep 30\n')
        (t / "bin" / "kiwix-serve").chmod(0o755)
        assert kb.has_local is True
        await kb.start_kiwix()
        try:
            assert kb.books == ["wikipedia_en_all_mini_2026-06"], kb.books
            for _ in range(20):
                if argv.exists():
                    break
                await asyncio.sleep(0.05)
            args = argv.read_text().split()
            assert args[:3] == ["--port", str(knowledge.KIWIX_PORT), "--nodatealiases"] and args[3:] == [str(zim)], args
        finally:
            kb.proc.terminate(); await kb.proc.wait()
    return f"{len(mans)} tiers: wikipedia_en_all_mini ~12 GB; downloader resolves it; Knowledge serves <dir>/knowledge/*.zim via kiwix-serve"


@run_test("unit_gpu_setup_boot_vga_display_logic", ["R41", "R50"])
async def t_gpu_setup(_):
    """[R41/R50] kairos-gpu-setup reads sysfs boot_vga to learn where the monitor is:
    NVIDIA loads for compute always, nvidia-drm modeset only when the NVIDIA card IS
    the boot VGA (an NVIDIA-only desktop; a 5070 Ti next to an Intel Ultra 9 iGPU with
    the monitor on the NVIDIA card); a hybrid laptop (AMD/Intel iGPU is boot VGA) stays
    compute-only; no GPU / no module still exits 0 [R63]; nvidia-persistenced starts
    only once a device is bound (D11). Run against fake sysfs trees with a fake modprobe
    + systemctl; plus the driver facts: nvidia-load.service ordering, the initramfs
    storage modules (vmd, nvme_core) and the Intel firmware/microcode packages."""
    import re, subprocess, tempfile
    inc = REPO / "iso" / "config" / "includes.chroot"
    script = inc / "usr" / "local" / "sbin" / "kairos-gpu-setup"
    subprocess.run(["sh", "-n", str(script)], check=True)
    assert os.access(script, os.X_OK), "kairos-gpu-setup lost its exec bit"
    assert 'SYS=${KAIROS_SYS:-/sys}' in script.read_text() and "boot_vga" in script.read_text()
    AMD, NV, INTEL = "0x1002", "0x10de", "0x8086"
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); (t / "bin").mkdir()
        (t / "bin" / "modprobe").write_text('#!/bin/sh\necho "modprobe $*" >> "$LOG"\n'
                                            '[ "$1" = nvidia ] && [ -n "$NO_NVIDIA" ] && exit 1\nexit 0\n')
        (t / "bin" / "systemctl").write_text('#!/bin/sh\necho "systemctl $*" >> "$LOG"\n')
        for f in (t / "bin").iterdir():
            f.chmod(0o755)

        def run(name, gpus, bound=(), no_nvidia=False):
            sysd, log = t / name, t / f"{name}.log"
            for i, (vendor, boot_vga) in enumerate(gpus):
                d = sysd / "bus" / "pci" / "devices" / f"0000:0{i}:00.0"; d.mkdir(parents=True)
                (d / "vendor").write_text(vendor + "\n"); (d / "boot_vga").write_text(boot_vga + "\n")
            for b in bound:
                (sysd / "bus" / "pci" / "drivers" / "nvidia" / b).mkdir(parents=True)
            env = dict(os.environ, PATH=f"{t / 'bin'}:{os.environ.get('PATH', '/usr/bin:/bin')}",
                       KAIROS_SYS=str(sysd), LOG=str(log), NO_NVIDIA="1" if no_nvidia else "",
                       KAIROS_GPU_SETUP_FOREGROUND="1")
            r = subprocess.run(["sh", str(script)], env=env, capture_output=True, text=True, timeout=30)
            assert r.returncode == 0, f"{name}: exit {r.returncode} — the unit may never fail the boot [R63]: {r.stderr}"
            return log.read_text().splitlines() if log.exists() else []
        modeset = lambda lg: [l for l in lg if "nvidia_drm" in l or "nvidia_modeset" in l]
        # hybrid laptop (this HP: AMD iGPU is the boot VGA) -> NVIDIA compute-only, no muxless panic
        lg = run("laptop", [(AMD, "1"), (NV, "0")])
        assert "modprobe nvidia" in lg and "modprobe nvidia_uvm" in lg and not modeset(lg), lg
        # NVIDIA-only desktop: the RTX is the boot VGA -> it drives the display
        lg = run("desktop", [(NV, "1")])
        assert "modprobe nvidia_modeset" in lg and "modprobe nvidia_drm modeset=1" in lg, lg
        # Intel Ultra 9 iGPU + 5070 Ti with the monitor on the NVIDIA card [R50]
        lg = run("intel-5070", [(INTEL, "0"), (NV, "1")])
        assert "modprobe nvidia_drm modeset=1" in lg, lg
        # the same desktop with the monitor on the Intel iGPU -> compute-only
        lg = run("intel-igpu-display", [(INTEL, "1"), (NV, "0")])
        assert "modprobe nvidia" in lg and not modeset(lg), lg
        # an AMD card that is the boot VGA next to no NVIDIA at all: never modeset, still exit 0
        lg = run("amd-only", [(AMD, "1")], no_nvidia=True); assert lg == ["modprobe nvidia"], lg
        # no GPU / no sysfs devices / no nvidia module: exit 0, nothing else [R63]
        lg = run("nogpu", [], no_nvidia=True); assert lg == ["modprobe nvidia"], lg
        # persistenced only once a device is bound to the driver, off the boot's critical chain
        lg = run("bound", [(AMD, "1"), (NV, "0")], bound=("0000:01:00.0",))
        assert "systemctl start --no-block nvidia-persistenced.service" in lg, lg
        assert not any("persistenced" in l for l in run("unbound", [(AMD, "1"), (NV, "0")]))
    unit = (inc / "etc" / "systemd" / "system" / "nvidia-load.service").read_text()
    directives = [l for l in unit.splitlines() if l and not l.startswith("#")]      # the comments explain D7
    assert "Before=getty@tty1.service" in directives and "ExecStart=-/usr/local/sbin/kairos-gpu-setup" in directives \
        and any(re.match(r"TimeoutStartSec=\d+", l) for l in directives) \
        and not any(l.startswith("After=") and "multi-user.target" in l for l in directives), "nvidia-load.service wiring"
    for b in ("build-img.sh", "patch-img.sh"):
        mods = re.search(r"/etc/initramfs-tools/modules <<M\n(.*?)\nM\n", (REPO / "iso" / b).read_text(), re.S)
        assert mods, f"{b}: no initramfs module list"
        for m in ("vmd", "nvme_core", "nvme", "xhci_pci"):
            assert m in mods.group(1).split(), f"{b}: the initramfs lacks {m} (root on an Intel VMD NVMe is never found)"
    pkgs = [l.strip() for l in (REPO / "iso" / "config" / "package-lists" / "kairos.list.chroot").read_text().splitlines()
            if l.strip() and not l.startswith("#")]
    for p in ("intel-microcode", "amd64-microcode", "firmware-intel-graphics", "firmware-iwlwifi", "iw", "rfkill"):
        assert p in pkgs, f"{p} missing from the image package list"
    return "boot_vga decides: laptop/iGPU-display compute-only, NVIDIA boot VGA (+Intel iGPU) modeset, no GPU exit 0, persistenced when bound; vmd+nvme_core in initramfs; Intel firmware/microcode shipped"


@run_test("http_api_docs_are_llm_only", ["R46"])
async def t_docs_llm_only(s):
    """[R46] the 📖 API Docs dock app is gone — no dock/menu entry in shell/index.html,
    no docs app in shell.js — while the docs themselves stay for the coding LLM: served
    at /api/docs (+ one category), keyword-matched into app generation (api_docs_for),
    and reachable from inside any app through vibe.docs() in the injected bridge."""
    html = (REPO / "shell" / "index.html").read_text()
    assert "📖" not in html and 'data-app="docs"' not in html and "API Docs" not in html, "the docs dock app is back"
    js = (REPO / "shell" / "shell.js").read_text()
    assert not re.search(r'data-app="docs"|title: "API Docs"|📖', js), "no docs app in shell.js either"
    assert "deliberately no dock app" in js
    sys.path.insert(0, str(REPO / "kernel"))
    import server
    assert 'docs: (category) => call("docs", category)' in server.bridge_src(), "vibe.docs() missing from the bridge apps get"
    assert re.search(r'if \(method === "docs"\) \{\s*return await fetch\("/api/docs"', js), "the desktop must answer vibe.docs() from /api/docs"
    async with s.get(f"{BASE}/api/docs") as r:
        assert r.status == 200 and "kAIrOS API" in await r.text()
    async with s.get(f"{BASE}/api/docs/games-apps") as r:
        assert r.status == 200 and "vibe.game" in await r.text()
    assert "games-apps" in [n for n, _ in server.api_docs_for("an arcade launcher for my pygame games")]
    return "no 📖 dock app; /api/docs + /api/docs/{category} served and matched into generation; vibe.docs() in the bridge"


@run_test("http_wifi_api_fully_documented", ["R49"])
async def t_wifi_docs(s):
    """[R49] the WiFi reference is complete AND true: docs/API.md 'WiFi connect — full
    reference' and docs/api/network.md (the short sheet the coding LLM gets) carry
    status/scan/connect/disconnect/forget with examples, the connect flow and the
    error handling; every op they name is one kernel/hw.py implements and POST /api/hw
    routes (status/scan open, connect/disconnect/forget guarded [R66]); the served
    sheet answers a WiFi request; the mock nmcli backs a status + scan round trip."""
    ops = ("status", "scan", "connect", "disconnect", "forget")
    api = (REPO / "docs" / "API.md").read_text()
    parts = api.split("### WiFi connect — full reference", 1)
    assert len(parts) == 2, "API.md lost the WiFi reference section"
    ref = parts[1].split("\n## ", 1)[0]
    for op in ops:
        assert f"vibe.hw.wifi.{op}(" in ref, f"API.md WiFi reference lacks {op}"
    assert ref.count("```js") >= 3 and "recommended connect flow" in ref and "available:false" in ref \
        and "r.detail" in ref and "password" in ref and "open network" in ref, "examples / flow / error handling"
    net = (REPO / "docs" / "api" / "network.md").read_text()
    assert net.startswith("<!-- keywords:") and "wifi" in net.split("-->")[0] and "Flow for a WiFi app" in net
    for op in ops:
        assert f"vibe.hw.wifi.{op}(" in net, f"network.md lacks {op}"
    hwsrc = (REPO / "kernel" / "hw.py").read_text()
    wifi_fn = hwsrc.split("async def wifi(op, body):", 1)[1].split("\nasync def ", 1)[0]
    for op in ops:
        assert f'if op == "{op}":' in wifi_fn, f"hw.py wifi() implements no {op} op"
    assert 'if device == "wifi":' in hwsrc and 'return await wifi(op or "status", body)' in hwsrc
    sys.path.insert(0, str(REPO / "kernel"))
    import server
    assert 'add_post("/api/hw", h_hw)' in (REPO / "kernel" / "server.py").read_text()
    for op in ("status", "scan"):
        assert server.hw_op_is_read({"device": "wifi", "op": op}), f"wifi {op} is a read"
    for op in ("connect", "disconnect", "forget"):
        assert not server.hw_op_is_read({"device": "wifi", "op": op}), f"wifi {op} must be guarded [R66]"
    async with s.get(f"{BASE}/api/docs/network") as r:
        sheet = await r.text()
    assert r.status == 200 and all(f"vibe.hw.wifi.{op}(" in sheet for op in ops)
    assert [n for n, _ in server.api_docs_for("a wifi manager that scans and connects")][0] == "network"
    async with s.post(f"{BASE}/api/hw", json={"device": "wifi", "op": "status"}) as r:
        st = await r.json()
    assert st["available"] and st["connected"] and st["ssid"] == "OpenAP", st
    async with s.post(f"{BASE}/api/hw", json={"device": "wifi", "op": "scan"}) as r:
        sc = await r.json()
    assert sc["networks"][0]["ssid"] == "OpenAP" and sc["networks"][0]["in_use"] and sc["networks"][-1]["security"] == "open", sc
    assert "vibe.hw.wifi.connect" in (REPO / "docs" / "wiki" / "wifi-manager.md").read_text()
    return "status/scan/connect/disconnect/forget documented (API.md + network.md, examples + flow) and implemented + routed; sheet served; status/scan round trip"


@run_test("unit_testrig_real_pc_and_gpu_desktop_traceable", ["R64", "R65"])
async def t_testrig_identity(_):
    """[R64/R65] live OUTSIDE this repository (pod/testrig/, the checkout next to repo/).
    When that checkout is present the suite verifies the files exist, parse, and carry
    the identity the requirements name — q35 + host CPU + SMBIOS (mAIer / kAIrOS
    Reference Device / Mainboard) + NVMe + e1000e + xHCI tablet/keyboard + HD audio
    [R64]; nvidia Xorg pinning (BusID, headless virtual 1080p) + x11vnc on :5900 as a
    unit [R65]; run-gpu-vm.sh ties them together. Skips with a clear message elsewhere
    — the requirement stays traceable, never silently green."""
    import subprocess
    rig = REPO.parent.parent / "pod" / "testrig"
    if not rig.is_dir():
        raise SkipTest(f"pod/testrig is not next to this checkout ({rig}): R64/R65 live outside the repository")
    files = {n: rig / n for n in ("vm.sh", "gpu-display-setup.sh", "run-gpu-vm.sh")}
    for n, f in files.items():
        assert f.exists(), f"{f} missing"
        subprocess.run(["bash", "-n", str(f)], check=True)
    vm = files["vm.sh"].read_text()
    for need in ("-machine q35", "-cpu host", "-smbios type=0,vendor=mAIer", 'product="kAIrOS Reference Device"',
                 'product="kAIrOS Mainboard"', "-device nvme,drive=", "-device e1000e", "-device qemu-xhci",
                 "usb-tablet", "usb-kbd", "-device intel-hda", "hda-duplex"):
        assert need in vm, f"vm.sh lost the real-PC identity: {need}"
    gd = files["gpu-display-setup.sh"].read_text()
    for need in ("modprobe nvidia_drm modeset=1", 'Driver "nvidia"', 'BusID "$PCI"', "AllowEmptyInitialConfiguration",
                 "Virtual 1920 1080", "kairos-x11vnc.service", "x11vnc -display :0", "-rfbport 5900", "-noshm"):
        assert need in gd, f"gpu-display-setup.sh lost: {need}"
    run = files["run-gpu-vm.sh"].read_text()
    assert "vm.sh run" in run and "gpu-display-setup" in run and "5900" in run, "run-gpu-vm.sh must boot the VM, provision the GPU desktop and name the VNC port"
    return f"{rig}: vm.sh q35/SMBIOS/NVMe/e1000e/xHCI/HDA; gpu-display-setup.sh nvidia Xorg pinning + x11vnc :5900 unit; run-gpu-vm.sh wires them"

def _load_engine_stamp_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location("engine_stamp", REPO / "scripts" / "engine-stamp.py")
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod


@run_test("unit_engine_stamp_manifest_pairing", ["R3", "R47", "R41"])
async def t_engine_stamp_pairing(_):
    """Manifests and engines are version-coupled (sd.cpp 7f410a3's --auto-fit took
    --offload-to-cpu out of the manifests; a July sd-cli with them OOMs the 8 GB card,
    docs/ENGINES-CUDA-2026-09.md 5.2). install-engines.sh writes bin/ENGINES.json
    (scripts/engine-stamp.py), every manifest declares the oldest engine commit DATE it was
    validated with, the kernel compares. Stamp shape from fake upstream trees, the rule
    (newer / equal / older / missing / unstamped / unchecked), the four manifests, and — with
    the artefact beside the checkout — the real CUDA set through the same writer."""
    import subprocess, tempfile
    sys.path.insert(0, str(REPO / "kernel"))
    import server
    es = _load_engine_stamp_module()
    if not shutil.which("git"):
        raise SkipTest("git not installed: the stamp writer reads the engines' git trees")
    # 1. the stamp: three fake upstream trees with dated commits and upstream-style tags
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        trees = {"llama.cpp": ("2026-07-01T10:00:00+02:00", "b6000"),
                 "stable-diffusion.cpp": ("2026-07-02T03:00:00+00:00", "master-749-{h}"),
                 "acestep.cpp": ("2026-07-03T00:00:00+00:00", None)}
        for name, (date, tag) in trees.items():
            t = root / "engines" / name; t.mkdir(parents=True); (t / "src.c").write_text("int x;")
            env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
                       GIT_COMMITTER_EMAIL="t@t", GIT_AUTHOR_DATE=date, GIT_COMMITTER_DATE=date)
            g = lambda *a: subprocess.run(["git", "-C", str(t), *a], env=env, capture_output=True, text=True, check=True).stdout.strip()
            g("init", "-q"); g("add", "."); g("commit", "-q", "-m", "x"); h = g("rev-parse", "--short=7", "HEAD")
            if tag:
                g("tag", tag.format(h=h))
        (root / "engines" / "llama.cpp" / "build").mkdir()
        (root / "engines" / "llama.cpp" / "build" / "CMakeCache.txt").write_text("CMAKE_CUDA_ARCHITECTURES:UNINITIALIZED=89;120\n")
        r = subprocess.run([sys.executable, str(REPO / "scripts" / "engine-stamp.py"), "--root", td, "--backend", "cuda"],
                           capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        july = json.loads((root / "bin" / "ENGINES.json").read_text())
        assert july["format"] == 1 and july["backend"] == "cuda" and july["cuda_arch"] == "89;120" and set(july["engines"]) == set(trees), july
        sd = july["engines"]["stable-diffusion.cpp"]
        assert sd["committed"] == "2026-07-02T03:00:00Z" and sd["build"] == 749 and sd["describe"].startswith("master-749-") \
            and sd["binaries"] == ["sd-cli", "sd-server"] and len(sd["commit"]) == 7 and sd["commit_full"].startswith(sd["commit"]), sd
        ll = july["engines"]["llama.cpp"]
        assert ll["committed"] == "2026-07-01T08:00:00Z" and ll["build"] == 6000 and ll["describe"] == "b6000", "UTC + tag build number"
        ac = july["engines"]["acestep.cpp"]
        assert ac["build"] is None and ac["describe"] == ac["commit"] and ac["binaries"] == ["ace-lm", "ace-synth", "ace-server"], "no upstream tags"
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", july["built"])
    mock = es.mock_stamp()
    assert mock["backend"] == "mock" and set(mock["engines"]) == set(trees) and all(e["committed"] == mock["built"] for e in mock["engines"].values())
    # 2. the rule: installed commit date vs the manifest's date
    have = {"commit": "7f410a3", "committed": "2026-09-11T17:41:28Z", "describe": "master-859-7f410a3"}
    assert server.compare_engine("2026-09-11", have)[0], "same day, later hour: ok"
    assert server.compare_engine("2026-09-10", have)[0], "newer: ok"
    assert server.compare_engine("2026-09-11T17:41:28Z", have)[0], "equal instant: ok"
    ok, why = server.compare_engine("2026-09-12", have)
    assert not ok and "OLDER" in why and "7f410a3, 2026-09-11, master-859-7f410a3" in why, why
    ok, why = server.compare_engine("2026-09-11", None)
    assert not ok and "not in the engine stamp" in why, why
    ok, why = server.compare_engine("2026-09-11", {"commit": "x", "committed": "yesterday"})
    assert not ok and "unreadable" in why, why
    # 3. the manifests: all three engines, ISO dates, sd.cpp >= the auto-fit build
    req = None
    for t in ("8gb", "16gb", "24gb", "32gb"):
        m = json.loads((REPO / "manifests" / f"tier-{t}.json").read_text())
        e = m.get("engines")
        assert e and set(e) == {"stable-diffusion.cpp", "llama.cpp", "acestep.cpp"}, f"tier-{t}: engines = {e}"
        assert all(re.fullmatch(r"\d{4}-\d{2}-\d{2}", v) for v in e.values()), f"tier-{t}: dates only: {e}"
        assert e["stable-diffusion.cpp"] >= "2026-09-27" and e["llama.cpp"] >= "2026-09-27", \
            f"tier-{t}: the 2026-09-28 argv (Qwen-Image-2.1, --model-args, sd-server, --reasoning) needs the 2026-09-27 set: {e}"
        req = req or e
        v = server.check_engines(m, july)
        assert v["status"] == "mismatch" and len(v["problems"]) == 3 and all("OLDER" in p for p in v["problems"]), v
        assert server.check_engines(m, mock)["status"] == "ok"
        assert server.check_engines(m, None, Path("/nonexistent/ENGINES.json"))["status"] == "unstamped"
    assert server.check_engines({"tier": "old"}, mock)["status"] == "unchecked", "a manifest without the field is not blocked"
    # 4. the real CUDA sets beside the checkout, through the same writer (into a temp dir — the
    # artefacts' own stamps are root-owned): the 2026-09-27 set (vibeos-cuda-next) must satisfy
    # every tier; the proven 2026-09-14 set (vibeos-cuda) is refused for sd.cpp + llama.cpp only
    # (its acestep is the one the next set carries)
    real = "artefacts not present"
    found = []
    for name, want in (("vibeos-cuda-next", "ok"), ("vibeos-cuda", "mismatch")):
        art = next((p for p in (REPO.parent.parent / name, Path("/images") / name) if (p / "engines").is_dir()), None)
        if not art:
            continue
        with tempfile.TemporaryDirectory() as td:
            r = subprocess.run([sys.executable, str(REPO / "scripts" / "engine-stamp.py"), "--root", str(art), "--backend", "cuda",
                                "--out", f"{td}/ENGINES.json"], capture_output=True, text=True)
            assert r.returncode == 0, r.stderr
            st = json.loads(Path(td, "ENGINES.json").read_text())
        for t in ("8gb", "16gb", "24gb", "32gb"):
            v = server.check_engines(json.loads((REPO / "manifests" / f"tier-{t}.json").read_text()), st)
            assert v["status"] == want, f"tier-{t} vs {name}: {v['status']} {v['problems']}"
            if want == "mismatch":
                named = {p.split(":", 1)[0] for p in v["problems"]}
                assert named == {"stable-diffusion.cpp", "llama.cpp"}, f"{name}: {v['problems']}"
        shipped = (art / "bin" / "ENGINES.json")
        assert not shipped.exists() or json.loads(shipped.read_text())["engines"]["stable-diffusion.cpp"]["commit"] == st["engines"]["stable-diffusion.cpp"]["commit"]
        found.append(f"{name} (" + ", ".join(f"{n} {e['commit']} {e['committed'][:10]}" for n, e in st["engines"].items()) + f") -> {want}")
    if found:
        real = "; ".join(found)
    # 5. the build path carries the stamp; the kernel gates every exec; the suite stamps its mocks
    inst = (REPO / "scripts" / "install-engines.sh").read_text()
    assert "engine-stamp.py" in inst and inst.index("engine-stamp.py") < inst.index("ENGINES INSTALL COMPLETE")
    for sc in ("iso/build-iso.sh", "scripts/build-image.sh"):
        b = (REPO / sc).read_text()
        assert 'ENGINES.json" ] || {' in b and "exit 1" in b[b.index('ENGINES.json" ] ||'):][:200] and 'cp "$VIBEOS/bin/ENGINES.json"' in b, f"{sc} must ship the stamp or fail"
    assert "engine-stamp.py\" --mock" in (REPO / "tests" / "run-tests.sh").read_text()
    src = (REPO / "kernel" / "server.py").read_text()
    for b in ("llama-server", "sd-cli", "ace-lm"):
        assert f'engine_check_or_raise("{b}")' in src, f"no gate before {b} is exec'd"
    assert '"engines": check_engines()' in src and "engine_check_line()" in src, "status field + startup log line"
    return f"stamp shape ok (UTC dates, tag build numbers, none for acestep); rule newer/equal/older/missing/unstamped/unchecked; {req}; {real}"


@run_test("http_engine_mismatch_clean_error", ["R3", "R4", "R31"])
async def t_engine_mismatch(s):
    """The suite's kernel runs on an explicit mock stamp (run-tests.sh): /api/status reports
    ok. Swap in a July stamp under the running kernel: status flips to mismatch naming every
    engine, /api/image ends in ONE clean job error (the mock sd-cli never runs), /api/music
    is a 400 with the reason; with no stamp at all the set is refused as unstamped. Restored,
    the kernel is ok again — the check reads the file per call, no restart."""
    stamp_path = VDIR / "bin" / "ENGINES.json"
    orig = stamp_path.read_text()
    arglog = VDIR / "sd-args.log"
    runs = lambda: arglog.read_text().count("\n") if arglog.exists() else 0
    async def status():
        async with s.get(f"{BASE}/api/status") as r:
            return (await r.json())["engines"]
    async def image_error():
        async with s.post(f"{BASE}/api/image", json={"prompt": "engine pairing test"}) as r:
            jid = (await r.json())["job"]
        for _ in range(30):
            async with s.get(f"{BASE}/api/job/{jid}") as r:
                j = await r.json()
            if j["status"] in ("done", "error"):
                break
            await asyncio.sleep(0.5)
        assert j["status"] == "error", j
        return j["error"]
    e = await status()
    assert e["status"] == "ok" and e["backend"] == "mock" and not e["problems"] \
        and set(e["required"]) == {"stable-diffusion.cpp", "llama.cpp", "acestep.cpp"} and e["stamp"] == str(stamp_path), e
    july = json.loads(orig)
    july["engines"]["stable-diffusion.cpp"].update(commit="b11c95a", committed="2026-07-02T03:00:00Z", describe="master-749-b11c95a", build=749)
    july["engines"]["llama.cpp"].update(commit="1a2b3c4", committed="2026-07-01T10:00:00Z", describe="b5900", build=5900)
    july["engines"]["acestep.cpp"].update(commit="0000000", committed="2026-07-03T00:00:00Z", describe="0000000")
    before = runs()
    try:
        stamp_path.write_text(json.dumps(july))
        e = await status()
        assert e["status"] == "mismatch" and len(e["problems"]) == 3, e
        assert e["problems"][0] == ("stable-diffusion.cpp: installed b11c95a, 2026-07-02, master-749-b11c95a is OLDER than "
                                    "the 2026-09-27 this manifest was validated with"), e["problems"]
        err = await image_error()
        assert err.startswith("engine/manifest mismatch (stable-diffusion.cpp, tier 8gb): stable-diffusion.cpp: installed b11c95a") \
            and "install-engines.sh" in err and "sync-guest.sh" in err and "Traceback" not in err, err
        assert runs() == before, "the mismatched sd-cli must never be exec'd"
        async with s.post(f"{BASE}/api/music", json={"prompt": "engine pairing test"}) as r:
            assert r.status == 400, r.status
            m = await r.json()
        assert m["error"].startswith("engine/manifest mismatch (acestep.cpp, tier 8gb): acestep.cpp: installed 0000000, 2026-07-03 is OLDER"), m
        stamp_path.unlink()
        e = await status()
        assert e["status"] == "unstamped" and e["installed"] == {} and "built before the stamp scheme" in e["problems"][0], e
        err = await image_error()
        assert "ENGINES.json missing" in err and "install-engines.sh" in err, err
    finally:
        stamp_path.write_text(orig)
    e = await status()
    assert e["status"] == "ok", e
    return "mock stamp ok; July stamp -> mismatch (3 engines named), /api/image one clean job error (sd-cli not run), /api/music 400; no stamp -> unstamped; restored -> ok"


@run_test("unit_testrig_sync_guest_ships_engines", ["R64", "R65", "R3"])
async def t_sync_guest_engines(_):
    """pod/testrig/sync-guest.sh step 1 syncs kernel+manifests but never shipped engines, so
    a VM test paired today's manifests with the image's July engines. It now ships a stamped
    set (default the 2026-09-14 CUDA build) over the guest SSH it already uses, prints the
    size, checks ldd/--version/stamp in the guest and gates on the kernel's verdict; vm.sh
    mirrors the verdict as one check line. The embedded python judges are run here through
    sh with the same quoting ssh sees."""
    import subprocess, tempfile
    rig = REPO.parent.parent / "pod" / "testrig"
    if not (rig / "sync-guest.sh").exists():
        raise SkipTest(f"pod/testrig is not next to this checkout ({rig})")
    t = (rig / "sync-guest.sh").read_text()
    step = t[t.index('log "1b/8 engines'):t.index('log "2/8')]
    for need in ("KAIROS_ENGINES:-/images/vibeos-cuda", 'KAIROS_SYNC_ENGINES:-1}" = 0', "ENGINES.json", "engine-stamp.py",
                 "readlink", '/opt/vibeos/*) t="$ENGINES_SRC/${t#/opt/vibeos/}"', "du -sh", "cp -L", "pigz -1", "| $SSH ",
                 "libcudart*|libcublas*|libcublasLt*|libnvrtc*|libcufft*|libcurand*|libnvJitLink*",
                 "libggml*.so*", "libllama*.so*", "libmtmd*.so*", "*stable-diffusion*.so*",
                 "ldd /opt/vibeos/bin/$b", '"not found"', "llama-server --version", "sd-cli --version", "ENGINES.json\\\")",
                 "ERROR: engine post-sync check failed", "exit 1"):
        assert need in step, f"sync-guest.sh engine step lacks: {need}"
    assert "sshpass" not in step, "the transfer must ride the script's own $SSH (sshpass lives in the container, not the host)"
    tail = t[t.index('log "8/8'):]
    assert 'get(\\"engines\\")' in tail and '"engines: ok"*) ;;' in tail and "KAIROS_SYNC_ENGINES" in tail, "step 8 must gate on the kernel verdict"
    vm = (rig / "vm.sh").read_text()
    m = re.search(r'check "engines match manifests" \'(.*?)\'\n', vm, re.S)
    assert m, "vm.sh lacks the engines check line"
    judge_vm = m.group(1).replace("curl -sf http://127.0.0.1:1337/api/status", 'cat "$STATUS"')
    m = re.search(r"ENG=\$\(\$SSH '(.*?)' 2>&1\)", t, re.S)
    assert m, "sync-guest.sh step 8 lacks the ENG= verdict line"
    judge_sg = m.group(1).replace("curl -sf http://127.0.0.1:1337/api/status", 'cat "$STATUS"')
    cases = ((
        {"engines": {"status": "ok", "installed": {"llama.cpp": {"commit": "3057bb6", "committed": "2026-09-12T14:09:46Z"}}, "problems": []}},
        0, "ok - llama.cpp 3057bb6 (2026-09-12)"),
        ({"engines": {"status": "mismatch", "installed": {}, "problems": ["stable-diffusion.cpp: installed b11c95a is OLDER"]}},
         1, "mismatch - stable-diffusion.cpp: installed b11c95a is OLDER"),
        ({"tier": "8gb"}, 1, "no engines field"))
    with tempfile.TemporaryDirectory() as td:
        for st, rc, text in cases:
            p = Path(td, "status.json"); p.write_text(json.dumps(st))
            r = subprocess.run(["sh", "-c", judge_vm], env=dict(os.environ, STATUS=str(p)), capture_output=True, text=True, timeout=30)
            assert r.returncode == rc and text in (r.stdout + r.stderr), f"vm.sh judge on {st}: rc={r.returncode} out={r.stdout!r} err={r.stderr!r}"
            r = subprocess.run(["sh", "-c", judge_sg], env=dict(os.environ, STATUS=str(p)), capture_output=True, text=True, timeout=30)
            assert r.returncode == 0 and r.stdout.startswith("engines: ") and text.split(" - ")[0] in r.stdout, f"sync-guest judge on {st}: {r.stdout!r} {r.stderr!r}"
    return "sync-guest.sh ships bin+lib (build-iso bundle rule) + stamp over $SSH, size printed, guest ldd/--version/stamp check, kernel verdict gate; vm.sh check line; both judges parse via sh"


# ---------------- 2026-09-28 integration: new image models, prompt expansion, multi-image sessions ----------------
# (docs/INTEGRATION-2026-09-28.md)

def _sd_argv_lines():
    arglog = Path(os.environ.get("MOCK_SD_ARGLOG", VDIR / "sd-args.log"))
    return arglog.read_text().splitlines() if arglog.exists() else []


def _sd_prompt(line):
    """the picture's prompt as the kernel built it — without the trailing <lora:…> tag the v7.5
    default adds for sd-cli (pinned on its own in tests/test_image_lora.py [R76])"""
    a = line.split("\t")
    return re.sub(r" <lora:[^<>]+>$", "", a[a.index("-p") + 1])


def _sdserver_log():
    p = Path(os.environ.get("MOCK_SDSERVER_LOG", VDIR / "sd-server.log"))
    return [l.split("\t", 1) for l in p.read_text().splitlines()] if p.exists() else []


class _mock_sd_server:
    """links tests/mock-sd-server as bin/sd-server for the duration of a test — the kernel checks
    the binary per session, so no restart; unlinked in every exit path (the rest of the suite
    proves the sd-cli fallback)"""
    def __init__(self):
        self.link = VDIR / "bin" / "sd-server"
    def __enter__(self):
        self.link.unlink(missing_ok=True)
        self.link.symlink_to(REPO / "tests" / "mock-sd-server")
        return self
    def __exit__(self, *a):
        self.link.unlink(missing_ok=True)


@run_test("unit_manifest_image_sections_2026_09", ["R34", "R42", "R29", "R47", "R3"])
async def t_manifest_2026_09(_):
    """Every tier after the 2026-09-28 integration (v7.5 [R76]: the 18+ route = the default, since
    2026-10-10 Muse Q4_0 without a LoRA, image_uncensored retired, image_safe = the same files), image_chroma keeps Chroma1-HD with sd.cpp's
    chroma_use_dit_mask=false, every new candidate file carries the Hugging Face sha256 + byte
    size, sizes are 1024², the engine floors name the 2026-09-27 set, and the three 8 GB test
    variants differ from tier-8gb.json in the llm section only."""
    hexsha = lambda v: isinstance(v, str) and re.fullmatch(r"[0-9a-f]{64}", v)
    for t in ("8gb", "16gb", "24gb", "32gb"):
        m = json.loads((REPO / "manifests" / f"tier-{t}.json").read_text())
        assert m["engines"] == {"stable-diffusion.cpp": "2026-09-27", "llama.cpp": "2026-09-27", "acestep.cpp": "2026-09-11"}, m["engines"]
        # [R76 → Muse, 2026-10-10] the 18+ route is the default itself: Muse v3.5 Extended Q4_0, no LoRA entry; the Kroma
        # v0.2 add-on is retired — full pins in tests/test_image_lora.py
        assert "image_uncensored" not in m and "lora" not in m["image"] \
            and m["image"]["diffusion"]["sha256"] == "db3eceab8049b5ba7dbebeff297145698c5498d618db15b88fd1c7e0bfa4e6c5" \
            and m["image"]["diffusion"]["size_bytes"] == 8316720160 and m["image_safe"]["diffusion"] == m["image"]["diffusion"], t
        c = m["image_chroma"]
        assert "Chroma1-HD" in c["name"] and c.get("addon") is True and "t5xxl" in c and "expander" not in c, c["name"]
        cg = c["gen_args"]
        assert cg[cg.index("--model-args") + 1] == "chroma_use_dit_mask=false" and "--clip-on-cpu" in cg \
            and cg[cg.index("--cfg-scale") + 1] == "4.0", cg
        q = m["image_qwen"]
        for part, sha, size in (("diffusion", "f8b244b00937f0e444a40dbf7866460871b89b30142594973b6012d1b471dc0a", 7687155744),
                                ("text_encoder", "0d264b3941185d00a74f75c4245521dae088ff1efc90ab8d1754e83f5844adb0", 8709519456),
                                ("vae", "bb21f7473051e1ac368515dd3f2e15cd44d7a11748ee8823e1ddca3e4876b7c9", 675509688)):
            assert q[part]["sha256"] == sha and q[part]["size_bytes"] == size, (t, part, q[part])
            assert abs(q[part]["size_gb"] - size / 1e9) < 0.02, (t, part, q[part]["size_gb"])
        assert hexsha(m["image"]["diffusion"]["sha256"]) and hexsha(m["image_classic"]["diffusion"]["sha256"]) \
            and hexsha(c["diffusion"]["sha256"]), f"{t}: sha256 missing on a baked/add-on DiT"
        if t != "8gb":
            assert m["image"]["text_encoder"]["sha256"] == "054721f478bc5fa6beffb7f38eae575d45298f88cbb8d2f83ef675a727863eb1", t
        # every section the kernel can route to has the files a job needs
        for key in ("image", "image_safe", "image_qwen", "image_classic", "image_chroma"):
            sec = m[key]
            assert "diffusion" in sec and "vae" in sec and ("text_encoder" in sec or "t5xxl" in sec) and sec.get("max_pixels"), (t, key)
            for e in (sec["diffusion"], sec["vae"], sec.get("text_encoder") or sec["t5xxl"]):
                # [Muse 2026-10-10] the Muse DiT (image / image_safe) is the author's civitai download — baked in every
                # tier, so the URL is informational (the kernel's aria2c downloader has no host assumption anyway);
                # every other file stays a Hugging Face LFS URL
                url_ok = e["url"].startswith("https://huggingface.co/") or (
                    key in ("image", "image_safe") and e is sec["diffusion"]
                    and e["url"] == "https://civitai.com/api/download/models/3258948")
                assert e["file"].startswith("image/") and url_ok and e["size_gb"] > 0, (t, key, e)
            assert "--offload-to-cpu" not in sec["gen_args"], (t, key)
        assert "expander" not in m["image_classic"] and "expander" not in m["image_qwen"], f"{t}: only the Krea family expands"
    # the test variants: the 8 GB tier with another brain
    m8 = json.loads((REPO / "manifests" / "tier-8gb.json").read_text())
    names = ("coder7b", "qwen35-9b", "gemma4-26b")
    for name in names:
        p = REPO / "manifests" / "test" / f"tier-8gb-{name}.json"
        assert p.exists(), f"variant missing: {p}"
        v = json.loads(p.read_text())
        assert v["variant"] == name and v["tier"] == "8gb"
        diff = {k for k in set(v) | set(m8) if k not in ("llm", "variant", "variant_comment") and v.get(k) != m8.get(k)}
        assert not diff, f"{name}: differs from tier-8gb.json outside llm: {diff}"
        llm = v["llm"]
        assert llm["file"].startswith("llm/") and llm["url"].startswith("https://huggingface.co/") and llm["size_gb"] > 0
        assert int(llm["serve_args"][llm["serve_args"].index("-c") + 1]) >= 16384
        assert "python_llm" in v and v["python_llm"] == {"shared_with": "llm"}
    return "4 tiers: 18+ = default = Muse Q4_0 (sha256, no LoRA, civitai URL), image_safe = the same files, Chroma via image_chroma (--model-args), Qwen-2.1 sha/sizes, floors 2026-09-27; 3 test variants = tier-8gb + llm"


@run_test("unit_prompt_expansion_helpers", ["R34", "R42"])
async def t_expansion_helpers(_):
    """The expander plumbing without engines: clean_expansion() = expand.py's clean (think block
    dropped, last paragraph kept, ( ) [ ] stripped — sd.cpp attention weights), expander_for()
    per section/setting/adult flag, expander_llm_spec() = the section's own encoder under
    llama-server, the kAIrOS line in the SFW prompt, the adult rule in the 18+ one."""
    sys.path.insert(0, str(REPO / "kernel"))
    import server, prompts
    raw = ("<think>pick a lens</think>\nPlanning: photo.\n\n"
           "A realistic photograph of a red fox (sitting) on a [snowy] cliff at dawn, soft (warm) light.")
    assert server.clean_expansion(raw) == "A realistic photograph of a red fox sitting on a snowy cliff at dawn, soft warm light."
    assert server.clean_expansion("short") == "short" and server.clean_expansion("") == ""
    assert server.clean_expansion("<think>only thinking, never finished") == "only thinking, never finished"
    two = "First paragraph that is long enough to count as a candidate line.\nSecond paragraph, also long enough, wins."
    assert server.clean_expansion(two) == "Second paragraph, also long enough, wins."
    # which expander for which section (the settings value passed explicitly = no /data read)
    # [R76 → Muse] the default (Muse, uncensored) expands with the neutral rules, `safe` (the same DiT) with the SFW ones
    assert server.expander_for("image", setting="auto") == "krea-neutral"
    assert server.expander_for("image", setting="on") == "krea-neutral"
    assert server.expander_for("image", setting="off") is None
    assert server.expander_for("image", adult=True, setting="auto") == "krea-18plus", "an explicit adult request takes the 18+ rules"
    assert server.expander_for("image_safe", setting="auto") == "krea"
    assert server.expander_for("image_safe", adult=True, setting="auto") == "krea-18plus"
    for kind in ("image_classic", "image_qwen", "image_chroma"):
        assert server.expander_for(kind, setting="on") is None, f"{kind} has no expander — 'on' cannot invent one"
    assert server.expander_for("image", setting="junk") in ("krea-neutral", None)      # unknown -> the settings default
    spec = server.expander_llm_spec("image")
    assert spec["file"] == "image/Qwen3VL-4B-Instruct-Q4_K_M.gguf" and "-ngl" in spec["serve_args"] \
        and int(spec["serve_args"][spec["serve_args"].index("-c") + 1]) >= 4096, spec
    key, spec2 = server.MM.llm_spec("expander:image_safe")
    assert key == "expander:image/Qwen3VL-4B-Instruct-Q4_K_M.gguf" and spec2["file"] == spec["file"], \
        "sections sharing the encoder share the loaded server"
    assert server.MM.llm_spec("expander:image")[0] == key
    try:
        server.expander_llm_spec("image_chroma"); raise AssertionError("T5 section accepted as expander host")
    except RuntimeError:
        pass
    assert server.EXPAND_SAMPLING == {"temperature": 0.7, "top_k": 64, "top_p": 0.95, "min_p": 0.05, "repeat_penalty": 1.05}
    assert server.EXPAND_MAX_TOKENS == 512 and server.SETTINGS_DEFAULTS["image_expand"] == "auto"
    sfw, adult = prompts.KREA_EXPAND_SYSTEM, prompts.KREA_EXPAND_SYSTEM_ADULT
    ref = (REPO / "docs" / "bench" / "image-2026-09-27" / "krea-expansion.txt").read_text()
    assert sfw.startswith(ref) and sfw.endswith("prefer a realistic photograph.\n"), "the kAIrOS medium line must be the only addition"
    assert adult == (REPO / "docs" / "bench" / "image-2026-09-27" / "krea-expansion-18plus.txt").read_text()
    assert "Adult Content (18+ system)" in adult and "Assume clothing covers" not in adult and "Assume clothing covers" in sfw
    assert server.EXPANDERS == {"krea": sfw, "krea-neutral": prompts.KREA_EXPAND_SYSTEM_NEUTRAL, "krea-18plus": adult}
    return "clean() strips think/brackets/keeps last paragraph; expander per section/setting/adult; encoder spec shared; prompts = bench copies (+1 line)"


@run_test("http_image_prompt_expansion", ["R34", "R42", "R29", "R79"])
async def t_image_expansion(s):
    """/api/image on a Krea-family model runs the prompt through the expander first (mock
    llama-server answers the expander persona with a think block + brackets): the picture is
    rendered from the CLEANED expansion, IMAGE_PROMPT_HINT is not appended; [R79] by default the
    RESIDENT coder LLM expands (no expander llama-server is launched), image_expander=encoder keeps
    the pre-R79 path (the section's text encoder under llama-server); image_expand=off / a per-call
    expand:false render the typed prompt + hint; a section without an expander (classic)
    never expands; adult:true takes the 18+ rules on the default model."""
    llama_log = Path(os.environ.get("MOCK_LLAMA_ARGLOG", VDIR / "llama-args.log"))

    def enc_launches():
        lines = llama_log.read_text().splitlines() if llama_log.exists() else []
        return [l for l in lines if "Qwen3VL-4B-Instruct-Q4_K_M.gguf" in l and "--mmproj" not in l]

    async def gen(payload):
        async with s.post(f"{BASE}/api/image", json=payload) as r:
            jid = (await r.json())["job"]
        job = await poll_job(s, jid)
        return job, _sd_prompt(_sd_argv_lines()[-1])

    async def setting(v, key="image_expand"):
        async with s.post(f"{BASE}/api/settings", json={"key": key, "value": v}) as r:
            assert r.status == 200, await r.text()

    await setting("auto")
    enc0 = len(enc_launches())
    job, p = await gen({"prompt": "a red fox on a snowy cliff"})
    assert p.startswith("A realistic photograph of a red fox on a snowy cliff") and "expanded by the mock expander for everyone" in p, p
    assert "(" not in p and ")" not in p and "[" not in p and "]" not in p, f"brackets not stripped: {p}"
    assert "<think>" not in p and "Planning:" not in p and not p.endswith("high quality, sharp detail"), p
    assert "top_k=64,top_p=0.95,min_p=0.05,repeat_penalty=1.05" in p, f"expander sampling not sent: {p}"
    assert job["expanded"] is True and job["expander"] == "krea-neutral" and job["prompt"] == p and job["engine"] == "sd-cli", job
    # [R79] the resident LLM wrote it: no expander server, the server that answered is the coder LLM's
    llm_file = Path(json.loads((REPO / "manifests" / "tier-8gb.json").read_text())["llm"]["file"]).name
    assert job["expander_model"] == "llm" and len(enc_launches()) == enc0, ("an expander server was launched", job, enc_launches()[enc0:])
    assert llm_file in llama_log.read_text().splitlines()[-1], llama_log.read_text().splitlines()[-1:]
    # the pre-R79 path, selectable: image_expander=encoder -> the section's text encoder under llama-server
    await setting("encoder", "image_expander")
    job, p = await gen({"prompt": "a red fox on a snowy cliff"})
    assert "expanded by the mock expander for everyone" in p and job["expander_model"] == "encoder", (p, job)
    launches = enc_launches()
    # [R72] the Krea section's expander_parallel (2 since the GPU validation of 2026-10-07): every slot keeps -c 8192
    ex_slots = json.loads((REPO / "manifests" / "tier-8gb.json").read_text())["image"].get("expander_parallel", 1)
    assert len(launches) == enc0 + 1 and f" -c {8192 * ex_slots}" in launches[-1] and "-ngl 99" in launches[-1] \
        and ((f" -np {ex_slots}" in launches[-1] and "--no-kv-unified" in launches[-1]) if ex_slots > 1 else " -np " not in launches[-1]), \
        f"the expander must run the image section's text encoder text-only: {launches[-1:] or llama_log.read_text()[-300:]}"
    await setting("auto", "image_expander")
    # 18+ rules on request
    job, p = await gen({"prompt": "a couple at a lake", "adult": True})
    assert "expanded by the mock expander for adults" in p and job["expander"] == "krea-18plus", (p, job)
    # per-call off
    job, p = await gen({"prompt": "a blue orb", "expand": False})
    assert p == "a blue orb, high quality, sharp detail" and job["expanded"] is False, (p, job)
    # no expander on Z-Image, whatever the setting
    await setting("on")
    job, p = await gen({"prompt": "a green orb", "model": "classic"})
    assert p == "a green orb, high quality, sharp detail" and job["expanded"] is False and job["expander"] is None, (p, job)
    # the setting off
    await setting("off")
    job, p = await gen({"prompt": "a violet orb"})
    assert p == "a violet orb, high quality, sharp detail" and job["expanded"] is False, (p, job)
    await setting("auto")
    async with s.get(f"{BASE}/api/models") as r:
        mi = await r.json()
    assert mi["expand"]["setting"] == "auto" and mi["overrides"]["image_expand"] == "auto"
    assert mi["expand"]["expander"]["setting"] == "auto" and mi["expand"]["expander"]["effective"] == "llm", mi["expand"]   # [R79]
    return ("auto: expanded prompt rendered (no brackets/think/hint) by the resident LLM, no expander server [R79]; "
            "image_expander=encoder -> the encoder under llama-server; adult -> 18+ rules; expand:false / off / classic -> raw + hint")


@run_test("http_images_batch_one_session", ["R42", "R22", "R4"])
async def t_images_batch(s):
    """POST /api/images: N prompts, ONE sd-server session (one LAUNCH with the section's model
    flags and no CLI-only -p/-o, N txt2img requests with distinct seeds at the section's size),
    every prompt expanded before the server starts, N URLs in order; prompt+count; a failed
    picture leaves the others (null + error record); input validation; and, with the server
    unlinked, the same call falls back to one sd-cli process per picture."""
    async def call(payload, ok=True):
        async with s.post(f"{BASE}/api/images", json=payload) as r:
            body = await r.json()
            if not ok:
                return r.status, body
        assert "job" in body, body
        return await poll_job(s, body["job"])

    with _mock_sd_server():
        async with s.get(f"{BASE}/api/status") as r:
            assert (await r.json())["image_sessions"] == "sd-server"
        before = len(_sdserver_log())
        job = await call({"prompts": ["a red orb", "a green orb", "a blue orb"]})
        log = _sdserver_log()[before:]
        launches = [l for k, l in log if k == "LAUNCH"]
        reqs = [json.loads(l) for k, l in log if k == "REQ"]
        assert len(launches) == 1, f"3 pictures must load the model once, got {len(launches)} launches"
        argv = launches[0].split("\t")
        assert "museByStableYogi_v35Q4Extended.gguf" in launches[0] and "--llm" in argv and "qwen_image_vae" in launches[0] \
            and argv[argv.index("--listen-port") + 1] == "8082" and "--listen-ip" in argv and "-v" in argv, argv
        assert "-p" not in argv and "-o" not in argv and "-M" not in argv and "-s" not in argv, f"CLI-only flags on the server: {argv}"
        assert argv[argv.index("--steps") + 1] == "8" and argv[argv.index("--scheduler") + 1] == "simple" \
            and "--diffusion-fa" in argv and "--vae-tiling" in argv and "--offload-to-cpu" not in argv, argv
        assert len(reqs) == 3 and [r["width"] for r in reqs] == [1024] * 3 and [r["height"] for r in reqs] == [1024] * 3, reqs
        assert len({r["seed"] for r in reqs}) == 3 and all(r["seed"] > 0 for r in reqs), f"seeds not fresh per picture: {reqs}"
        assert all("expanded by the mock expander for everyone" in r["prompt"] for r in reqs), reqs
        assert reqs[0]["prompt"].startswith("A realistic photograph of a red orb") and "blue orb" in reqs[2]["prompt"]
        assert job["count"] == 3 and len(job["result"]) == 3 and all(u and u.startswith("/files/images/") for u in job["result"]), job
        assert [r["raw"] for r in job["results"]] == ["a red orb", "a green orb", "a blue orb"] \
            and all(r["expanded"] and r["engine"] == "sd-server" and r["error"] is None for r in job["results"]), job["results"]
        for u in job["result"]:
            async with s.get(f"{BASE}{u}") as r:
                assert r.status == 200 and (await r.read())[:4] == b"\x89PNG", u
        assert job["error"] is None
        # prompt + count, a size (fitted), and a partial failure
        before = len(_sdserver_log())
        job = await call({"prompt": "a __mockfail__ orb", "count": 1, "prompts": ["ok orb", "a __mockfail__ orb", "ok orb 2"],
                          "width": 640, "height": 400, "expand": False})
        log = _sdserver_log()[before:]
        assert sum(1 for k, _ in log if k == "LAUNCH") == 1 and sum(1 for k, _ in log if k == "REQ") == 3
        reqs = [json.loads(l) for k, l in log if k == "REQ"]
        assert reqs[0] == {**reqs[0], "width": 640, "height": 384, "prompt": "ok orb, high quality, sharp detail"}, reqs[0]
        assert job["status"] == "done" and job["result"][0] and job["result"][1] is None and job["result"][2], job["result"]
        assert "1 of 3 pictures failed" in job["error"] and "generate_image returned no results" in job["results"][1]["error"], job
        before = len(_sdserver_log())
        job = await call({"prompt": "twin orb", "count": 2, "expand": "off"})
        assert len(job["result"]) == 2 and sum(1 for k, _ in _sdserver_log()[before:] if k == "LAUNCH") == 1
        # validation
        for bad in ({}, {"prompts": []}, {"prompts": "  "}, {"prompt": "x", "count": 99}):
            st, body = await call(bad, ok=False)
            assert st == 400 and "error" in body, (bad, st, body)
        # all failed -> job error, engine state back to idle
        job_err = None
        async with s.post(f"{BASE}/api/images", json={"prompts": ["__mockfail__ a", "__mockfail__ b"]}) as r:
            jid = (await r.json())["job"]
        try:
            await poll_job(s, jid)
        except AssertionError as e:
            job_err = str(e)
        assert job_err and "generate_image returned no results" in job_err, job_err
        async with s.get(f"{BASE}/api/status") as r:
            assert (await r.json())["state"] == "idle"
    # the server is gone (the proven set): the same API renders one sd-cli process per picture
    async with s.get(f"{BASE}/api/status") as r:
        assert (await r.json())["image_sessions"] == "sd-cli"
    n_cli = len(_sd_argv_lines())
    job = await call({"prompts": ["cli orb one", "cli orb two"], "expand": False})
    lines = _sd_argv_lines()[n_cli:]
    assert len(lines) == 2 and all("museByStableYogi_v35Q4Extended" in l and "-o" in l.split("\t") for l in lines), lines
    seeds = [l.split("\t")[l.split("\t").index("-s") + 1] for l in lines]
    assert seeds[0] != seeds[1] and [_sd_prompt(l) for l in lines] == ["cli orb one, high quality, sharp detail", "cli orb two, high quality, sharp detail"]
    assert len(job["result"]) == 2 and all(job["result"]) and all(r["engine"] == "sd-cli" for r in job["results"]), job
    return "3 prompts = 1 sd-server launch + 3 txt2img (fresh seeds, expanded, 1024²); partial failure kept; validation; without the server: 1 sd-cli per picture"


@run_test("http_genimg_batch_window", ["R58", "R42"])
async def t_genimg_batch(s):
    """/genimg requests arriving within the batching window for the same model and size are one
    sd-server session (a page with three pictures = one model load); a different size is its own
    session; the per-file cache, placeholders and the limit keep working (cached picture served
    instantly, a 5th queued picture gets the SVG placeholder)."""
    import urllib.parse
    with _mock_sd_server():
        before = len(_sdserver_log())
        descs = ["batch orb alpha", "batch orb beta", "batch orb gamma"]
        async def get(desc, q=""):
            async with s.get(f"{BASE}/genimg/{urllib.parse.quote(desc)}{q}") as r:
                return r.headers.get("Content-Type", ""), await r.read()
        answers = await asyncio.gather(*(get(d) for d in descs), get("batch orb delta", "?w=768&h=512"))
        for (ctype, body), d in zip(answers, descs + ["delta"]):
            assert body[:4] == b"\x89PNG", f"{d}: not rendered ({ctype})"
        log = _sdserver_log()[before:]
        launches = [l for k, l in log if k == "LAUNCH"]
        reqs = [json.loads(l) for k, l in log if k == "REQ"]
        assert len(launches) == 2, f"3 same-size pictures + 1 other size = 2 sessions, got {len(launches)}"
        assert len(reqs) == 4 and sorted((r["width"], r["height"]) for r in reqs) == [(512, 512)] * 3 + [(768, 512)], reqs
        assert all("expanded by the mock expander" in r["prompt"] for r in reqs), "genimg pictures expand too"
        # cached: instant, no new session
        t0 = time.time()
        ctype, again = await get(descs[0])
        assert again == answers[0][1] and time.time() - t0 < 1.5 and len(_sdserver_log()) == before + len(log), "cache miss"
    return "3 same-size /genimg within the window -> 1 sd-server session (+1 for the other size); cache hit instant"


@run_test("unit_llm_sampling_merge_and_variants", ["R3", "R5"])
async def t_sampling_merge(_):
    """A manifest llm section's `sampling` (vendor-recommended sampler fields) is merged into
    every chat body — top_p/top_k/presence_penalty… and, when given, temperature over the
    kernel's per-call value; a per-call `sampling` (the expander's) wins over both; absent =
    the byte-identical body of before. The three 8 GB test variants carry the sampling the
    benchmark used, thinking off both ways (--reasoning off + enable_thinking=false) for the
    hybrid models, and gemma leaves -ngl to llama.cpp's --fit."""
    sys.path.insert(0, str(REPO / "kernel"))
    import server
    msgs = [{"role": "user", "content": "hi"}]
    plain = {"llm": {"name": "x", "file": "llm/x.gguf", "serve_args": ["-c", "4096"]}, "python_llm": {"shared_with": "llm"}}
    assert server.ModelManager(plain).chat_payload("llm", "S", msgs, 10, 0.4, False) == \
        {"messages": [{"role": "system", "content": "S"}, *msgs], "max_tokens": 10, "temperature": 0.4, "stream": False}
    samp = {"llm": dict(plain["llm"], sampling={"top_p": 0.8, "top_k": 20, "presence_penalty": 1.5, "min_p": 0.0}),
            "python_llm": {"shared_with": "llm"}}
    b = server.ModelManager(samp).chat_payload("python_llm", "S", msgs, 10, 0.4, True)
    assert b["top_p"] == 0.8 and b["top_k"] == 20 and b["presence_penalty"] == 1.5 and b["min_p"] == 0.0 \
        and b["temperature"] == 0.4 and b["stream"] is True, b
    temp = {"llm": dict(plain["llm"], sampling={"temperature": 1.0, "top_k": 64})}
    b = server.ModelManager(temp).chat_payload("llm", "S", msgs, 10, 0.4, False)
    assert b["temperature"] == 1.0 and b["top_k"] == 64, "a manifest temperature overrides the per-call one"
    b = server.ModelManager(temp).chat_payload("llm", "S", msgs, 10, 0.4, False, sampling={"temperature": 0.7, "top_k": 5})
    assert b["temperature"] == 0.7 and b["top_k"] == 5, "the per-call sampling wins"
    b = server.ModelManager({"llm": dict(plain["llm"], sampling={"messages": "x", "stream": 1, "top_k": 3})}).chat_payload("llm", "S", msgs, 1, 0.1, False)
    assert b["messages"][1] == msgs[0] and b["stream"] is False and b["top_k"] == 3, "messages/stream can never be overridden"
    for junk in ("x", 3, None, []):
        assert "top_k" not in server.ModelManager({"llm": dict(plain["llm"], sampling=junk)}).chat_payload("llm", "S", msgs, 1, 0.1, False)
    # the variants
    V = REPO / "manifests" / "test"
    q = json.loads((V / "tier-8gb-qwen35-9b.json").read_text())["llm"]
    assert q["sampling"] == {"temperature": 0.7, "top_p": 0.8, "top_k": 20, "min_p": 0.0, "presence_penalty": 1.5} \
        and q["chat_template_kwargs"] == {"enable_thinking": False} and q["serve_args"][-2:] == ["--reasoning", "off"] \
        and q["file"] == "llm/Qwen3.5-9B-Q4_K_M.gguf" and q["sha256"] == "03b74727a860a56338e042c4420bb3f04b2fec5734175f4cb9fa853daf52b7e8", q
    g = json.loads((V / "tier-8gb-gemma4-26b.json").read_text())["llm"]
    assert g["sampling"] == {"temperature": 1.0, "top_k": 64, "top_p": 0.95} and g["chat_template_kwargs"] == {"enable_thinking": False} \
        and "--reasoning" in g["serve_args"] and "-ngl" not in g["serve_args"] and "--n-cpu-moe" not in g["serve_args"] \
        and g["file"] == "llm/gemma-4-26B-A4B-it-ultra-uncensored-heretic.i1-IQ4_XS.gguf" and g["size_bytes"] == 13917722144 and g["uncensored"] is True, g   # [R86 round 4] the heretic default
    c = json.loads((V / "tier-8gb-coder7b.json").read_text())["llm"]
    m8 = json.loads((REPO / "manifests" / "tier-8gb.json").read_text())
    # since 2026-09-28 the tier's brain IS the gemma variant; coder7b was its `llm_fast` add-on until R89 (the fixture keeps it)
    assert {k: v for k, v in m8["llm"].items() if k != "comment"} == {k: v for k, v in g.items() if k != "comment"}, "tier-8gb llm != gemma variant"
    fast = llm_fast_fixture()
    assert c["file"] == fast["file"] and c["serve_args"] == fast["serve_args"] \
        and "sampling" not in c and "chat_template_kwargs" not in c, c
    for name in ("qwen35-9b", "gemma4-26b", "coder7b"):
        mm = server.ModelManager(json.loads((V / f"tier-8gb-{name}.json").read_text()))
        b = mm.chat_payload("llm", "S", msgs, 100, 0.75, False)
        assert b["max_tokens"] == 100 and b["messages"][0]["content"] == "S"
        if name != "coder7b":
            assert b["chat_template_kwargs"] == {"enable_thinking": False} and "top_k" in b, (name, b)
    return "sampling merged (temperature override, per-call wins, messages/stream protected); variants: qwen35 0.7/0.8/20/1.5, gemma 1.0/64/0.95 + --fit = tier-8gb llm, coder7b = the former llm_fast (fixture)"


@run_test("unit_engine_stamp_next_set_and_sd_server", ["R3", "R47", "R41"])
async def t_next_set(_):
    """The 2026-09-27 engine set is complete: its stamp (written by scripts/engine-stamp.py)
    lists all three engines with sd-server among sd.cpp's binaries, the bin/ symlinks resolve
    (sd-cli, sd-server, llama-server, ace-lm/ace-synth/ace-server), and the shipping scripts +
    install script carry sd-server; the kernel maps sd-server to the sd.cpp pairing check."""
    sys.path.insert(0, str(REPO / "kernel"))
    import server
    es = _load_engine_stamp_module()
    assert es.ENGINES["stable-diffusion.cpp"] == ["sd-cli", "sd-server"], es.ENGINES
    assert server.ENGINE_OF_BINARY["sd-server"] == "stable-diffusion.cpp"
    inst = (REPO / "scripts" / "install-engines.sh").read_text()
    assert '"$BIN/sd-server"' in inst and "sd-server" in inst.split("[5/5]")[0], "install-engines.sh must link bin/sd-server"
    assert "sd-server" in (REPO / "iso" / "build-iso.sh").read_text().split("[4/6]")[1].split("ENGINES.json")[0]
    assert "sd-server" in (REPO / "scripts" / "build-image.sh").read_text()
    src = (REPO / "kernel" / "server.py").read_text()
    assert 'engine_check_or_raise("sd-server" if server else "sd-cli")' in src, "the session path must be gated like sd-cli"
    assert "/sdapi/v1/txt2img" in src and "--listen-port" in src and "--offload-to-cpu" not in src.split("_sd_server_session")[1].split("def ")[0]
    art = next((p for p in (REPO.parent.parent / "vibeos-cuda-next", Path("/images/vibeos-cuda-next")) if (p / "bin").is_dir()), None)
    if not art:
        raise SkipTest("vibeos-cuda-next is not beside this checkout")
    st = json.loads((art / "bin" / "ENGINES.json").read_text())
    assert set(st["engines"]) == {"llama.cpp", "stable-diffusion.cpp", "acestep.cpp"}, st["engines"].keys()
    assert st["engines"]["stable-diffusion.cpp"]["binaries"] == ["sd-cli", "sd-server"] and st["engines"]["stable-diffusion.cpp"]["build"] == 929
    assert st["engines"]["llama.cpp"]["build"] == 11223 and st["engines"]["acestep.cpp"]["commit"] == "daf7644"
    for t in ("8gb", "16gb", "24gb", "32gb"):
        v = server.check_engines(json.loads((REPO / "manifests" / f"tier-{t}.json").read_text()), st)
        assert v["status"] == "ok", (t, v["problems"])
    def real(b):
        t = os.readlink(art / "bin" / b)
        return art / t[len("/opt/vibeos/"):] if t.startswith("/opt/vibeos/") else Path(t)
    for b in ("sd-cli", "sd-server", "llama-server", "llama-cli", "ace-lm", "ace-synth", "ace-server"):
        assert (art / "bin" / b).is_symlink() and real(b).is_file() and real(b).stat().st_size > 1000, f"{b}: missing in the next set"
    return "next set stamped: sd.cpp 929 (sd-cli+sd-server), llama.cpp b11223, acestep daf7644; satisfies all tiers; 7 binaries resolve; scripts ship sd-server"


# ---------------- fixes 2026-09-28 (docs/FIXES-2026-09-28.md) ----------------

def llm_fast_fixture() -> dict:
    """[R89] the `llm_fast` section the 8/16 GB tiers carried until 2026-10-18 (tests/fixture-llm-fast.json): the shipped
    manifests dropped fast mode, the kernel keeps the mechanism for a manifest that has one — the tests inject this"""
    return json.loads((REPO / "tests" / "fixture-llm-fast.json").read_text(encoding="utf-8"))["llm_fast"]


GEMMA_SHA = "e2f223775fa6e4e2129b7d7802fa7326b8da3fe261e8834baf65b722f9df3742"   # [R86 round 4, 2026-10-10] the heretic i1-IQ4_XS (stock UD-IQ4_XS: babd1e38…)
CODER7B_SHA = "9a961bb225cb2b9fd84b2297df0d53089895c049d7d9dc5f5f8aebbcd3247872"


@run_test("unit_manifests_gemma4_and_fast_mode", ["R3", "R5", "R47", "R89"])
async def t_gemma_fast_manifests(_):
    """A1/A2: the 8 GB and 16 GB tiers serve Gemma 4 26B-A4B-it — since 2026-10-10 the uncensored heretic
    edition (mradermacher i1-IQ4_XS, "uncensored": true; the owner's choice, R86 round 4) — with exactly the
    argv the feature test proved (no -ngl: --fit places the MoE; thinking off both ways; vendor sampling; HF
    sha256 + byte size). [R89, 2026-10-18] fast mode is dropped: no tier and no 8 GB test variant carries the
    `llm_fast` add-on (Qwen2.5-Coder-7B: 11/37 functional items vs Gemma's 26–30) any more; the 24/32 GB tiers
    are untouched; tests/fixture-llm-fast.json pins the former section (url, sha256, size, argv) for the tests
    that keep the kernel's llm_fast mechanism covered; the llm comment names the decision."""
    m8 = json.loads((REPO / "manifests" / "tier-8gb.json").read_text())
    m16 = json.loads((REPO / "manifests" / "tier-16gb.json").read_text())
    for t, m in (("8gb", m8), ("16gb", m16)):
        llm = m["llm"]
        assert llm["file"] == "llm/gemma-4-26B-A4B-it-ultra-uncensored-heretic.i1-IQ4_XS.gguf" and llm["size_bytes"] == 13917722144 \
            and llm["sha256"] == GEMMA_SHA and llm["url"].endswith("/gemma-4-26B-A4B-it-ultra-uncensored-heretic.i1-IQ4_XS.gguf") \
            and llm["url"].startswith("https://huggingface.co/mradermacher/") and llm["uncensored"] is True, (t, llm)   # [R86 round 4]
        assert llm["serve_args"] == ["-c", "16384", "-fa", "on", "--cache-ram", "4096", "--ctx-checkpoints", "8", "--jinja", "--reasoning", "off"], (t, llm["serve_args"])      # host-RAM caps [R72 GPU validation 2026-10-07]
        assert llm["chat_template_kwargs"] == {"enable_thinking": False} and llm["sampling"] == {"temperature": 1.0, "top_k": 64, "top_p": 0.95}, t
        assert "-ngl" not in llm["serve_args"] and "--n-cpu-moe" not in llm["serve_args"] and "addon" not in llm, t
        assert m["python_llm"] == {"shared_with": "llm"}, t
        assert "llm_fast" not in m and "[R89]" in llm["comment"] and "FAST-MODELS-2026-10-17" in llm["comment"], f"{t}: fast mode dropped [R89]"
        assert "Qwen2.5-Coder-7B" not in json.dumps({k: v for k, v in m.items() if k != "llm"}), f"{t}: the coder still listed"
    for t in ("24gb", "32gb"):
        m = json.loads((REPO / "manifests" / f"tier-{t}.json").read_text())
        assert "Qwen3-Coder-30B-A3B" in m["llm"]["name"] and "llm_fast" not in m and "-ngl" in m["llm"]["serve_args"], t
    for v in (REPO / "manifests" / "test").glob("tier-8gb-*.json"):
        assert "llm_fast" not in json.loads(v.read_text()), f"{v.name}: the variants mirror tier-8gb outside llm"
    assert json.loads((REPO / "manifests" / "test" / "tier-8gb-gemma4-26b.json").read_text())["llm"]["file"] == m8["llm"]["file"]
    fast = llm_fast_fixture()
    assert fast["addon"] is True and fast["file"] == "llm/Qwen2.5-Coder-7B-Instruct-Q4_K_M.gguf" \
        and fast["size_bytes"] == 4683073504 and abs(fast["size_gb"] - 4.68) < 0.01 and fast["sha256"] == CODER7B_SHA \
        and fast["url"] == "https://huggingface.co/unsloth/Qwen2.5-Coder-7B-Instruct-GGUF/resolve/main/Qwen2.5-Coder-7B-Instruct-Q4_K_M.gguf" \
        and fast["serve_args"] == ["-ngl", "99", "-c", "16384", "--cache-ram", "2048", "--ctx-checkpoints", "8", "--jinja"] \
        and fast["parallel"] == 2, fast
    return "8/16 GB: Gemma 4 (sha256, --fit, thinking off, 1.0/64/0.95), no llm_fast [R89]; 24/32 GB untouched; variants without it; fixture = the former section"


@run_test("unit_r47_baked_set_and_build_scripts", ["R47", "R3"])
async def t_r47_baked_set(_):
    """A3: the baked (non-addon) set of the 8 GB manifest is 4 files / 24.99 GB (the heretic Gemma 4 13.92
    since 2026-10-10 evening [R86 round 4]; stock Gemma 13.60 before + Muse 8.32 + its encoder 2.50 + VAE
    0.25 since 2026-10-10; v7.5 had 5 files / 25.81 GB with
    Krea 2 7.22 + the Kroma LoRA 2.24, the 2026-09 set 23.57 GB, the old set 14.65 GB) — with the ZIM
    (11.67 GiB), the 8 GiB swapfile and the ~7.1 GiB rootfs measured on the v5 image it stays
    under the 58 GiB limit only because music (5.25 GiB), video, the extra image models, vision
    and the fast LLM are add-ons. Every build path must bake EXACTLY that set: build-image.sh's
    collector skips add-on subtrees, build-img.sh + write-usb.sh copy the list
    scripts/check_tier_present.py --list produces (no whole-directory rsync), and the presence
    gate ignores add-ons. The images also ship quickjs for the JS syntax gate [D7]."""
    import subprocess
    sys.path.insert(0, str(REPO / "kernel"))
    import server
    m8 = json.loads((REPO / "manifests" / "tier-8gb.json").read_text())
    baked = {e["file"]: e for e in server.manifest_files(m8)}
    # [R76 → Muse, 2026-10-10] the Muse Q4_0 DiT (8 316 720 160 B) replaces Krea 2 Turbo Q4_K_M + the Kroma v0.3 LoRA
    # (7 216 993 376 + 2 241 178 336 B); image_safe shares image's files
    assert set(baked) == {"llm/gemma-4-26B-A4B-it-ultra-uncensored-heretic.i1-IQ4_XS.gguf", "image/museByStableYogi_v35Q4Extended.gguf",
                          "image/Qwen3VL-4B-Instruct-Q4_K_M.gguf", "image/qwen_image_vae.safetensors"}, set(baked)
    total = sum(e.get("size_bytes") or int(e["size_gb"] * 1e9) for e in baked.values())
    assert total == 24985530214, total      # [R86 round 4] the heretic 13 917 722 144 (stock Gemma 13 597 177 568: 24 664 985 638)
    for key in ("music", "video", "vision", "image_qwen", "image_classic", "image_chroma", "image_edit"):   # [R85] + the editor
        assert m8[key].get("addon") is True, f"{key} must be an add-on for the image to stay < 58 GiB"
    assert "llm_fast" not in m8 and "file" not in m8["translation"]["quality"], "[R89] no fast coder, quality = the loaded LLM"
    lister = REPO / "scripts" / "check_tier_present.py"
    out = subprocess.run([sys.executable, str(lister), str(REPO / "manifests" / "tier-8gb.json"), "/nonexistent", "--list"],
                         capture_output=True, text=True)
    # [2026-10-07, docs/IMAGE-BAKE-ADDONS-2026-10-07.md] the list = the non-addon set + the bake-flagged add-on files:
    # the translation `fast` model and the 38 dictionary archives ride in the image [R71]; quality = the loaded LLM [R89]
    bake_list = out.stdout.split()
    dict_files = [e["file"] for e in server.section_files(m8["dictionaries"])]
    assert out.returncode == 0 and bake_list == list(baked) + [m8["translation"]["fast"]["file"]] + dict_files \
        and len(bake_list) == 43 and not any("Index-Translate-9B" in f for f in bake_list), out.stdout   # 44 in v7.5 (+ the LoRA)
    bake_total = total + m8["translation"]["fast"]["size_bytes"] + sum(e["size_bytes"] for e in server.section_files(m8["dictionaries"]))
    assert bake_total == 26416157753, bake_total      # [R86 round 4] + 320 544 576 (the heretic); [Muse 2026-10-10] 26 095 613 177; v7.5: 27 237 064 729
    out_all = subprocess.run([sys.executable, str(lister), str(REPO / "manifests" / "tier-8gb.json"), "/nonexistent", "--all", "--list"],
                             capture_output=True, text=True).stdout.split()
    assert "music/acestep-v15-turbo-Q8_0.gguf" in out_all and "llm/Qwen2.5-Coder-7B-Instruct-Q4_K_M.gguf" not in out_all \
        and "translation/Index-Translate-2B.Q4_K_M.gguf" in out_all and "dict/beolingus-de-en.txt.gz" in out_all \
        and not any(f.startswith("wiktionary_") for f in out_all) and not any("Index-Translate-9B" in f for f in out_all) \
        and "image/qwen_image_2.1_turbo_Q4_K_M.gguf" in out_all \
        and len(out_all) == 62, len(out_all)   # 20 (Muse 2026-10-10: − the LoRA; v7.5: 22 = + the LoRA, − Kroma v0.2; R89: − the fast coder) + the translation fast file [R71; R89: the 9B left, quality = the loaded LLM] + 38 dictionary files [P3] + 3 picture-editor files [R85: DiT, encoder, mmproj — its VAE is image_qwen's]; the llm_uncensored add-on left 2026-10-10 (R86 round 4: the default is the heretic); the 7 ZIMs live in knowledge/, never models/
    src = (REPO / "scripts" / "check_tier_present.py").read_text()
    assert 'n.get("addon") and not include_addons' in src
    bi = (REPO / "scripts" / "build-image.sh").read_text()
    assert "quickjs" in bi.split("[3/6]")[0], "build-image.sh must install quickjs"
    img = (REPO / "iso" / "build-img.sh").read_text()
    assert '--files-from="$BAKE_LIST"' in img and 'check_tier_present.py" "$REPO/manifests/tier-8gb.json" "$MODELS" --list' in img \
        and 'rsync -a --bwlimit=120M "$MODELS"/ ' not in img, "build-img.sh must copy the listed baked files only"
    usb = (REPO / "iso" / "write-usb.sh").read_text()
    assert '--files-from="$BAKE_LIST"' in usb and 'rsync -a --info=progress2 "$MODELS/"' not in usb, "write-usb.sh copies the whole models dir"
    pkgs = (REPO / "iso" / "config" / "package-lists" / "kairos.list.chroot").read_text()
    assert re.search(r"^quickjs$", pkgs, re.M), "the live image must ship quickjs (kernel/jscheck.py)"
    # ---- N2 [docs/FIXES-2026-09-29.md]: scripts/build-image.sh's [5/6] collector is a MIRROR of the list on a
    # REUSED build root — the re-verification's dry-run scenario: a root that a pre-fix build filled with every
    # manifest file (24 since the translation add-on [R71], 20 of them add-ons) + a source dir made of symlinks
    # + a leftover nobody names
    import tempfile
    from pathlib import Path as P
    py = re.search(r"\[5/6\].*?<<'PY'\n(.*?)\nPY\n", bi, re.S).group(1)
    assert 'sys.argv[1:6]' in py and '"--list"' in py and "os.walk(dst_dir, topdown=False)" in py and "os.path.realpath(src)" in py
    with tempfile.TemporaryDirectory() as td:
        S = P(td); real, src_dir, root = S / "real", S / "src", S / "root"
        dst = root / "opt" / "vibeos" / "models"
        every = set()
        def _files(n):
            if isinstance(n, dict):
                if "url" in n and "file" in n:
                    every.add(n["file"])
                for v in n.values():
                    _files(v)
        _files(m8)
        assert len(every) == 69, len(every)                     # 4 baked (Muse 2026-10-10: the LoRA entry is gone; v7.5: 5) + 65 add-on files (1 translation file [R71; R89: the 9B left], 38 dictionaries + 7 Wiktionary ZIMs [P3], 3 picture-editor files [R85]; R89: the fast coder left); the llm_uncensored add-on left 2026-10-10 [R86 round 4]
        for f in every:
            (real / f).parent.mkdir(parents=True, exist_ok=True); (real / f).write_bytes(b"x" * (7 * len(f)))
            (src_dir / f).parent.mkdir(parents=True, exist_ok=True); (src_dir / f).symlink_to(real / f)     # symlinked models dir
            (dst / f).parent.mkdir(parents=True, exist_ok=True); (dst / f).write_bytes(b"old")            # what the earlier build baked
        (dst / "llm" / "leftover.gguf").write_bytes(b"stale"); (dst / "music" / "sub").mkdir(); (dst / "music" / "sub" / "x.bin").write_bytes(b"s")
        run = lambda *a: subprocess.run([sys.executable, "-", str(REPO / "manifests" / "tier-8gb.json"), *map(str, a), str(lister)],
                                        input=py, capture_output=True, text=True)
        r = run(src_dir, root, dst)
        assert r.returncode == 0, r.stderr
        left = sorted(str(p.relative_to(dst)) for p in dst.rglob("*") if p.is_file())
        assert left == sorted(bake_list), left                   # the 4 non-addon files + the 39 bake-flagged add-on files
        assert not any(p.is_symlink() for p in dst.rglob("*")), "links must be copied as their targets"
        assert all((dst / f).stat().st_size == (real / f).stat().st_size for f in bake_list), "stale copies must be redone"
        assert r.stdout.count("removing stale") == 28 and "mirrored 43 files" in r.stdout, r.stdout   # 26 unflagged add-on copies (69 − 43; R86 round 4: the llm_uncensored add-on left; R85: + the 3 picture-editor files; R89: − the coder and the 9B) + 2 leftovers
        assert not (dst / "music").exists() and not (dst / "video").exists(), "emptied dirs go too"
        for bad_root, bad_dst in ((S, src_dir), (P("/"), P("/opt/vibeos/models")), (root, root / "opt" / "vibeos" / "models-x")):
            rb = run(src_dir, bad_root, bad_dst)
            assert rb.returncode != 0 and "refusing to mirror" in (rb.stderr + rb.stdout), (bad_dst, rb.stderr)
        # the symlink risk: a dangling link fails the build AND the presence gate, --bytes sizes the targets
        gone = src_dir / "image" / "qwen_image_vae.safetensors"
        gone.unlink(); gone.symlink_to(S / "nowhere")
        rd = run(src_dir, root, dst)
        assert rd.returncode != 0 and "dangling symlink" in (rd.stderr + rd.stdout), rd.stderr
        g = subprocess.run([sys.executable, str(lister), str(REPO / "manifests" / "tier-8gb.json"), str(src_dir)], capture_output=True, text=True)
        assert g.returncode == 1 and "DANGLING image/qwen_image_vae.safetensors" in g.stdout and "INCOMPLETE" in g.stdout, g.stdout
        b = subprocess.run([sys.executable, str(lister), str(REPO / "manifests" / "tier-8gb.json"), str(src_dir), "--bytes"], capture_output=True, text=True)
        assert b.returncode == 1 and b.stdout == "" and "DANGLING" in b.stderr, (b.stdout, b.stderr)
        gone.unlink(); gone.symlink_to(real / "image" / "qwen_image_vae.safetensors")
        b = subprocess.run([sys.executable, str(lister), str(REPO / "manifests" / "tier-8gb.json"), str(src_dir), "--bytes"], capture_output=True, text=True)
        assert b.returncode == 0 and int(b.stdout) == sum((real / f).stat().st_size for f in bake_list) == 7 * sum(map(len, bake_list)), b.stdout
    assert 'check_tier_present.py" "$REPO/manifests/tier-8gb.json" "$MODELS" --bytes' in img and "rsync -aL --bwlimit=120M" in img \
        and 'du -sbc $(cat "$BAKE_LIST")' not in img, "build-img.sh: size the resolved files, copy links as targets"
    assert "rsync -aL --info=progress2" in usb and 'rsync -aL "$KNOW"/*.zim' in usb, "write-usb.sh: rsync -L"
    # ---- N3: write-usb.sh computes the need from the bake list + ZIM + ISO and refuses BEFORE the ERASE prompt
    gate = usb.index('[ "$SZ" -ge "$NEED_B" ]')
    assert gate < usb.index('read -r -p "This ERASES') and usb.index('--bytes') < gate and "NEED_B=$(( ISO_B + DATA_B + DATA_B / 20 + 1073741824 ))" in usb \
        and "30000000000" not in usb and "64 GB class stick" in usb and usb.index("dd if=") > usb.index('read -r -p "This ERASES'), "write-usb.sh size gate"
    for doc in ("iso/flash-img.md", "README.md", "iso/README.md", "iso/build-img.sh"):
        t = (REPO / doc).read_text()
        assert "64 GB" in t and not re.search(r"[≥>]=?\s?32\s?GB (USB )?stick", t), f"{doc} still asks for a 32 GB stick"
    return f"baked set = 4 non-addon files (Muse Q4_0, no LoRA) / {total / 1e9:.2f} GB + 39 bake-flagged add-on files (translation fast + dictionaries) = 43 / {bake_total / 1e9:.2f} GB; music/video/vision + 3 image models stay add-ons (no fast coder, no 9B [R89]); 3 build paths use the list; quickjs shipped; build-image.sh mirrors the list (a reused root holding every manifest file → 43, links resolved, dangling fails); write-usb.sh needs the computed size before erasing; docs say 64 GB"


@run_test("http_llm_fast_mode", ["R3", "R5", "R47", "R89"])
async def t_llm_fast_mode(s):
    """A2 → [R89, 2026-10-18 "Drop fast mode"]: the 8 GB tier has no `llm_fast` add-on any more. /api/models lists ONE
    coder LLM (default = the heretic Gemma) and `modes` ["default"]; llm_model=fast is still a valid value: the
    default keeps serving (no coder launch) and /api/status says why; the 'fast' download is no add-on (400); a bad
    value is a 400. The mechanism stays for a manifest that HAS the section (tests/fixture-llm-fast.json in a
    ModelManager of its own): fast → the llm_fast section, its file absent → the default + the add-on reason,
    llm_options lists both, the download keys fast|llm_fast name the same add-on."""
    sys.path.insert(0, str(REPO / "kernel"))
    import server
    arglog = VDIR / "llama-args.log"
    launches = lambda: [l for l in (arglog.read_text().splitlines() if arglog.exists() else []) if l.strip()]

    async def status():
        async with s.get(f"{BASE}/api/status") as r:
            return await r.json()

    async def set_llm(v):
        async with s.post(f"{BASE}/api/settings", json={"key": "llm_model", "value": v}) as r:
            assert r.status == 200, (v, await r.text())

    async with s.get(f"{BASE}/api/models") as r:
        mi = await r.json()
    by = {m["key"]: m for m in mi["llm"]}
    assert set(by) == {"default"} and by["default"]["addon"] is False and by["default"]["active"] is True \
        and by["default"]["file"] == "llm/gemma-4-26B-A4B-it-ultra-uncensored-heretic.i1-IQ4_XS.gguf", by
    assert mi["llm_setting"]["setting"] == "default" and mi["llm_setting"]["effective"] == "default" \
        and mi["llm_setting"]["modes"] == ["default"] and mi["overrides"]["llm_model"] == "default" \
        and "no separate fast model" in mi["llm_setting"]["note"], mi["llm_setting"]
    try:
        await set_llm("fast")
        st = await status()
        assert st["llm_options"]["setting"] == "fast" and st["llm_options"]["effective"] == "default" \
            and st["llm_options"]["section"] == "llm" and "no separate fast LLM" in st["llm_options"]["fallback"] \
            and "serving the default" in st["llm_options"]["fallback"] and [o["key"] for o in st["llm_options"]["options"]] == ["default"], st["llm_options"]
        n0 = len(launches())
        async with s.post(f"{BASE}/api/ask", json={"prompt": "hi [r89 fast]"}) as r:
            assert r.status == 200, await r.text()
        new = launches()[n0:]
        assert not any("Qwen2.5-Coder" in l for l in new) and all("gemma-4-26B" in l for l in new if "--port 8081" in l), new
        assert (await status())["loaded"] == "llm"
        async with s.post(f"{BASE}/api/models/download", json={"model": "fast"}) as r:
            j = await r.json()
            assert r.status == 400 and "not a downloadable add-on" in j["error"], (r.status, j)
        async with s.post(f"{BASE}/api/settings", json={"key": "llm_model", "value": "turbo"}) as r:
            assert r.status == 400 and "llm_model" in (await r.json())["error"]
    finally:
        await set_llm("default")
    # the mechanism, on a manifest that still has the section (the fixture)
    fast = llm_fast_fixture()
    mm = server.ModelManager({**json.loads((REPO / "manifests" / "tier-8gb.json").read_text()), "llm_fast": fast})
    ff = VDIR / "models" / fast["file"]
    ff.parent.mkdir(parents=True, exist_ok=True)
    had = ff.exists()
    try:
        ff.touch()
        c = mm.llm_choice({"llm_model": "fast"})
        assert c == {"setting": "fast", "effective": "fast", "section": "llm_fast", "fallback": None}, c
        assert [o["key"] for o in mm.llm_options()] == ["default", "fast"]
        with_fast = {"llm_model": "fast"}
        server_settings = server.load_settings
        server.load_settings = lambda: dict(server_settings(), **with_fast)
        try:
            assert mm.llm_spec("llm")[0] == "llm_fast" and mm.llm_spec("llm")[1]["file"] == fast["file"]
        finally:
            server.load_settings = server_settings
        ff.unlink()
        c = mm.llm_choice({"llm_model": "fast"})
        assert c["effective"] == "default" and c["section"] == "llm" and "add-on" in c["fallback"] and "download" in c["fallback"], c
    finally:
        if had:
            ff.touch()
        elif ff.exists():
            ff.unlink()
    assert server.canonical_download_key("llm_fast") == "fast" and server.addon_section_key("fast") == "llm_fast"
    return ("8 GB: models.llm = [default] only, modes [default]; fast → default + reason, no coder launch; 'fast' download 400; "
            "bad value 400; mechanism on the fixture: fast → llm_fast, absent → default + add-on reason")


@run_test("http_settings_validation", ["R15", "R42"])
async def t_settings_validation(s):
    """D8: POST /api/settings validates known keys — a bad value is a 400 naming the rule and
    nothing is stored (the feature test's Settings app posted an accent without a value and the
    OS accent became null); unknown keys stay a free preference store."""
    async with s.get(f"{BASE}/api/settings") as r:
        before = await r.json()
    bad = [({"key": "accent"}, "colour"), ({"key": "accent", "value": None}, "colour"), ({"key": "accent", "value": "blueish"}, "colour"),
           ({"key": "accent2", "value": 123}, "colour"), ({"key": "os_name", "value": ""}, "os_name"),
           ({"key": "os_name", "value": "x" * 41}, "40"), ({"key": "image_model", "value": "dalle"}, "image_model"),
           ({"key": "image_expand", "value": "yes"}, "image_expand"), ({"key": "llm_model", "value": "turbo"}, "llm_model"),
           ({"key": "image_steps", "value": "12"}, "image_steps"), ({"key": "image_steps", "value": 0}, "image_steps"),
           ({"key": "image_cfg", "value": 99}, "image_cfg"), ({"key": "image_sampler", "value": "rm -rf /"}, "image_sampler"),
           ({"key": "wallpaper", "value": "junk"}, "wallpaper"), ({"accent": "#123456", "image_expand": "maybe"}, "image_expand"),
           ({"key": "pref", "value": "x" * 70000}, "KB")]
    for body, needle in bad:
        async with s.post(f"{BASE}/api/settings", json=body) as r:
            j = await r.json()
            assert r.status == 400 and needle in j["error"], (body, r.status, j)
    async with s.get(f"{BASE}/api/settings") as r:
        after = await r.json()
    assert after == before, "a rejected POST must store nothing"
    good = [({"key": "accent", "value": "#ff5cf0"}, "accent", "#ff5cf0"), ({"key": "accent", "value": "rgb(124, 92, 255)"}, "accent", "rgb(124, 92, 255)"),
            ({"key": "accent2", "value": "hotpink"}, "accent2", "hotpink"), ({"key": "image_steps", "value": None}, "image_steps", None),
            ({"key": "image_steps", "value": 12}, "image_steps", 12), ({"key": "image_cfg", "value": 1.5}, "image_cfg", 1.5),
            ({"key": "image_sampler", "value": "er_sde"}, "image_sampler", "er_sde"), ({"key": "wallpaper", "value": None}, "wallpaper", None),
            ({"key": "my_pref", "value": {"a": [1, 2]}}, "my_pref", {"a": [1, 2]}), ({"os_name": "kAIrOS", "image_expand": "off"}, "image_expand", "off")]
    for body, k, v in good:
        async with s.post(f"{BASE}/api/settings", json=body) as r:
            j = await r.json()
            assert r.status == 200 and j[k] == v, (body, r.status, j)
    # restore what other tests expect
    for k, v in (("accent", before["accent"]), ("accent2", before["accent2"]), ("image_steps", None), ("image_cfg", None),
                 ("image_sampler", None), ("image_expand", before.get("image_expand") or "auto"), ("os_name", before["os_name"])):
        async with s.post(f"{BASE}/api/settings", json={"key": k, "value": v}) as r:
            assert r.status == 200
    return f"{len(bad)} bad values → 400 (nothing stored), {len(good)} good ones → 200; unknown keys kept"


@run_test("http_music_addon_absent_hint", ["R31", "R47"])
async def t_music_addon_absent(s):
    """A3: music is a downloadable add-on — with its files absent /api/music is a clean 400 with
    the download hint (what vibe.music() rejects with) and /api/models reports it not present."""
    mdir = VDIR / "models" / "music"
    hidden = mdir.with_name("music.hidden")
    mdir.rename(hidden)
    try:
        async with s.post(f"{BASE}/api/music", json={"prompt": "a jingle", "duration": 5}) as r:
            j = await r.json()
            assert r.status == 400 and "add-on" in j["error"] and "vibe.models.download('music')" in j["error"], (r.status, j)
        async with s.get(f"{BASE}/api/models") as r:
            ex = {e["key"]: e for e in (await r.json())["extras"]}
        assert ex["music"]["addon"] is True and ex["music"]["present"] is False and ex["music"]["available"] is False, ex["music"]
    finally:
        hidden.rename(mdir)
    async with s.get(f"{BASE}/api/models") as r:
        ex = {e["key"]: e for e in (await r.json())["extras"]}
    assert ex["music"]["present"] is True
    return "music absent → 400 with vibe.models.download('music') hint; present again → available"


BROKEN_JS = "<!DOCTYPE html><html><head><script>\nconst app = { async generateImage: async function() { return 1 } };\n</script></head><body>x</body></html>"
MODERN_JS = """<!DOCTYPE html><html><head><script>
<!-- annex B comment
class A { #p = 1; static { A.x = 2 } count = 0; get v() { return this.#p } static async *gen() { yield* [1] } }
const o = { a: { b: null } }; const q = o?.a?.b ?? 'd'; let z = 0; z ??= 5; z ||= 1; z &&= 2;
const big = 1_000_000n; const re = /(?<year>\\d{4})(?<=x)y/du; label: for (const k of [1]) { break label }
async function f() { for await (const x of []) {} await import('./x.js').catch(() => {}); return new.target }
const t = String.raw`a${z}`; const [a = 1, ...rest] = [2, 3]; const { b: { c = 4 } = {} } = {};
function* g() { yield 1 } const arrow = async (x) => x ** 2; document?.getElementById?.('x'); const r2 = /]/; try { throw 1 } catch { }
-->
</script><script type="module">import x from "nowhere"; export const y = 1;</script>
<script type="application/json">{"not": "js"}</script><script src="/x.js"></script></head><body></body></html>"""


@run_test("unit_js_syntax_gate", ["R12", "R45"])
async def t_js_gate(_):
    """D7: kernel/jscheck.py parses every classic inline <script> with a real ES2023 parser
    (QuickJS qjsc; node --check as the fallback) before an app is returned: the feature test's
    `async x: async function()` pattern, top-level await/return and truncated code become
    validate_html() issues with the document line, the parser message and the offending line;
    modern syntax passes; module/JSON/src scripts are skipped; two scripts redeclaring one `let`
    (a SyntaxError in the browser's shared global scope) are caught once concatenated."""
    sys.path.insert(0, str(REPO / "kernel"))
    import jscheck, server
    if not jscheck.enabled():
        raise SkipTest(f"no JS parser here ({jscheck.CHECKER.get('reason')}) — run the suite in the kairos-fix image (quickjs)")
    issues = jscheck.js_syntax_problems(BROKEN_JS)
    assert len(issues) == 1 and "does not parse" in issues[0] and "document line 2" in issues[0] and "SyntaxError" in issues[0] \
        and "async generateImage: async function" in issues[0], issues
    assert any("does not parse" in p for p in server.validate_html(BROKEN_JS)), "validate_html must carry the gate"
    assert jscheck.js_syntax_problems(MODERN_JS) == [], jscheck.js_syntax_problems(MODERN_JS)
    tla = "<!DOCTYPE html><html><head><script>\n\nconst s = await vibe.load('k');\n</script></head><body></body></html>"
    got = jscheck.js_syntax_problems(tla)
    assert len(got) == 1 and "document line 3" in got[0], got
    assert jscheck.js_syntax_problems(server.jsfix.harden_app_html(tla)) == [], "jsfix's await wrapper must parse"
    assert jscheck.js_syntax_problems("<html><script>return 5;</script></html>") and \
        jscheck.js_syntax_problems("<html><script>function f() { let s = \"abc;\n return 1 }</script></html>") and \
        jscheck.js_syntax_problems("<html><script>const x = {\n a: 1,\n};\nf(\n</script></html>")
    two = "<html><script>let x = 1;</script><script>\nconst y = 1;\nlet x = 2;</script></html>"
    got = jscheck.js_syntax_problems(two)
    assert len(got) == 1 and "global scope" in got[0] and "#2" in got[0] and "x (#1, #2)" in got[0] and "y (" not in got[0], got
    assert jscheck.js_syntax_problems("<html><script>let x = 1;</script><script>x = 2; var y = /a/v;</script></html>") == [], "regex flags never reported (V8 v flag)"
    assert jscheck.js_syntax_problems("<html><script>   </script><script>// only a comment</script></html>") == []
    return f"gate on ({jscheck.CHECKER['kind']} {jscheck.CHECKER.get('version') or ''}): broken/TLA/return/truncated caught with document lines; modern syntax + module/json skipped; cross-script let caught"


@run_test("http_app_js_gate_repairs_before_done", ["R12", "R45", "R5"])
async def t_js_gate_pipeline(s):
    """D7 end to end: the model returns an app whose script does not parse ("[broken-js]" makes the
    mock do that); the gate turns it into a self-repair round BEFORE `done`, so the document the
    user gets parses — the shell's runtime self-heal (capped by D1) is no longer the first defence."""
    sys.path.insert(0, str(REPO / "kernel"))
    import jscheck
    if not jscheck.enabled():
        raise SkipTest("no JS parser here — run the suite in the kairos-fix image (quickjs)")
    events = await sse_collect(s, f"{BASE}/api/app", {"prompt": "a gate test app [broken-js]", "app_id": "gate-test-broken"})
    deltas = "".join(d.get("text", "") for ev, d in events if ev == "delta")
    done = [d for ev, d in events if ev == "done"]
    assert done, [ev for ev, _ in events]
    assert "self-repair: JavaScript does not parse" in deltas and "async generateImage: async function" in deltas, deltas[-600:]
    html = done[0]["html"]
    assert "async generateImage: async function" not in html and jscheck.js_syntax_problems(html) == [], html[:400]
    assert done[0].get("broken") is None, done[0].get("broken")
    saved = (VDIR / "cache" / "apps" / "gate-test-broken" / "app.html").read_text()
    assert jscheck.js_syntax_problems(saved) == [], "the saved app must be the repaired one"
    # [N5, docs/FIXES-2026-09-29.md] the repair budget runs out ("[broken-js-unfixable]": the mock's
    # repair daemon keeps the broken line, as Gemma did for the crafted D7 prompt): the app is still
    # delivered (done, saved as the last version) but WITH the gate's verdict, so the shell shows it
    # instead of burning 3 self-heal rounds; the verdict travels with the version everywhere
    events = await sse_collect(s, f"{BASE}/api/app", {"prompt": "a gate test app [broken-js-unfixable]", "app_id": "gate-unfixable"})
    deltas = "".join(d.get("text", "") for ev, d in events if ev == "delta")
    done = [d for ev, d in events if ev == "done"]
    assert done, [ev for ev, _ in events]
    assert "self-repair: JavaScript does not parse" in deltas and "// gate: the JavaScript still does not parse after 4 repair rounds" in deltas \
        and "flagged as broken" in deltas, deltas[-700:]
    b = done[0]["broken"]
    assert b and "does not parse" in b["message"] and b["rounds"] == 4 and b["line"] and b["checker"] == jscheck.CHECKER["kind"] \
        and b["problems"] and "async generateImage: async function" in b["message"], b
    assert "async generateImage: async function" in done[0]["html"], "the last version is delivered, not dropped"
    saved = (VDIR / "cache" / "apps" / "gate-unfixable" / "app.html").read_text()
    assert jscheck.js_syntax_problems(saved), "the last version is kept on disk"
    meta = json.loads((VDIR / "cache" / "apps" / "gate-unfixable" / "meta.json").read_text())
    assert meta.get("broken", {}).get("message") == b["message"], meta.get("broken")
    # reopening = cached, flagged again (no heal loop on a saved-broken app)
    events = await sse_collect(s, f"{BASE}/api/app", {"prompt": "a gate test app [broken-js-unfixable]", "app_id": "gate-unfixable"})
    cached = [d for ev, d in events if ev == "cached"]
    assert cached and cached[0]["broken"] and cached[0]["broken"]["message"] == b["message"], cached and cached[0].get("broken")
    # the shell's "repair once more" → the repair reply says whether the gate still fails
    async with s.post(f"{BASE}/api/app/gate-unfixable/repair",
                      json={"error": {"type": "SyntaxError", "message": "Unexpected token ':'", "line": 12}}) as r:
        rep = await r.json()
    assert rep.get("html") and rep.get("broken") and "does not parse" in rep["broken"]["message"], rep.get("broken")
    # the /app/<id> host page carries the verdict for host.js
    async with s.get(f"{BASE}/app/gate-unfixable") as r:
        page = await r.text()
    assert 'data-broken="{' in page and "does not parse" in page, page[:600]
    # a healthy app: no verdict anywhere
    async with s.post(f"{BASE}/api/app/gate-test-broken/repair",
                      json={"error": {"type": "TypeError", "message": "x is not a function", "line": 3}}) as r:
        rep = await r.json()
    assert rep.get("html") and rep.get("broken") is None, rep.get("broken")
    async with s.get(f"{BASE}/app/gate-test-broken") as r:
        assert 'data-broken=""' in await r.text()
    return "broken script → gate issue in the stream → repaired before done; unfixable → done + broken verdict (stream note, meta, cached, repair reply, /app page)"


@run_test("unit_shell_fixes_static", ["R45", "R12", "R61", "R13"])
async def t_shell_fixes_static(_):
    """D1/D2/D3/D4/D5/D6/D9/D11 as text checks (the behavioural bridge test runs in node,
    tests/bridge-test.js): the self-heal cap in both host pages, the maximized window leaving the
    dock's room, host.js handling every bridge method, unknown vibe.* members undefined + the
    unknown_api hint, the dialog shims, the shell-side confirmations before format/upgrade/power,
    the prompt rules, and the settings wiki documenting the exact hw/setup shapes."""
    js = (REPO / "shell" / "shell.js").read_text()
    host = (REPO / "shell" / "bench" / "host.js").read_text()
    bridge = re.search(r"const BRIDGE_SRC = `(.*?)`;\n", js, re.S).group(1)
    body = bridge.split("<script>", 1)[1]
    # D4 + D5 in the bridge (no backslash / backtick / ${ allowed in the literal)
    assert "const missing = (path, members) =>" in body and "return undefined;" in body and "unknownSeen" in body \
        and 'rep.unknown_api = unknownSeen.slice()' in body, "D4: unknown members undefined + hint"
    assert 'define(window, "alert"' in body and 'define(window, "confirm"' in body and 'define(window, "prompt"' in body \
        and "kairos-dialog-notice" in body, "D5: dialog shims"
    assert not re.search(r"\\(?!/script>)", bridge) and "${" not in bridge and "`" not in bridge
    methods = set(re.findall(r'call\("([a-zA-Z]+)"', body)) | {"jsError"}
    assert {"images", "vision", "fs", "upgrade", "hw"} <= methods
    for name, src in (("shell.js", js), ("host.js", host)):
        h = re.search(r"async function handleBridge\([^)]*\) \{.*?\n\}", src, re.S).group(0)
        handled = set(re.findall(r'method === "([a-zA-Z]+)"', h))
        assert methods <= handled, f"{name} lacks handlers: {sorted(methods - handled)}"      # D3
        fs = h[h.index('method === "fs"'):h.index('method === "settingsGet"')]
        assert 'op === "format"' in fs and fs.index("await uiConfirm(") < fs.index('fetch("/api/fs"'), f"{name}: D9 format"
        up = h[h.index('method === "upgrade"'):h.index('method === "search"')]
        assert up.index("await uiConfirm(") < up.index('fetch("/api/setup/upgrade"'), f"{name}: D9 upgrade"
        hw = h[h.index('method === "hw"'):h.index('method === "wiki"')]
        assert 'device === "power"' in hw and hw.index("await uiConfirm(") < hw.index('fetch("/api/hw"'), f"{name}: D9 power"
        assert "const HEAL_MAX_ROUNDS = 3;" in src and "win.healRounds > HEAL_MAX_ROUNDS) { showHealStop(win, rep); return true; }" in h \
            and "win.healRounds = 0;" in src and "function showHealStop(" in src, f"{name}: D1 cap"
        assert "function uiConfirm(" in src and 'iframe.sandbox = "allow-scripts allow-pointer-lock"' in src, name
        # [N4] the confirmation's OK button is disabled + counting down for CONFIRM_ARM_MS after it appears
        uc = re.search(r"function uiConfirm\(.*?\n\}", src, re.S).group(0)
        assert "const CONFIRM_ARM_MS = 700;" in src and "ok.disabled = true;" in uc and 'ov.dataset.armed = "0"' in uc \
            and uc.index("ok.disabled = true;") < uc.index("document.body.appendChild(ov)") \
            and 'ok.onclick = () => { if (ok.disabled || ov.dataset.armed !== "1") return; done(true); };' in uc \
            and "clearInterval(tick)" in uc and "performance.now()" in uc, f"{name}: N4 arming"
        # [N5] the kernel's JS-gate verdict is applied right after every mount, spends the heal budget and
        # shows the banner — never a repair POST; the repair reply's verdict is honoured too
        assert "function applyGateVerdict(win, b)" in src and "win.healRounds = HEAL_MAX_ROUNDS;" in src \
            and "function brokenReport(b)" in src and "win.gateBroken" in src, f"{name}: N5 helpers"
        assert re.search(r'mountApp\((?:win, )?data\.html\);[\s\S]{0,700}applyGateVerdict\(win, data\.broken\)', src), f"{name}: N5 verdict on done/cached/fallback"
        js_err = h[h.index('method === "jsError"'):]
        assert "applyGateVerdict(win, r.broken)" in js_err and js_err.index("mountApp(") < js_err.index("applyGateVerdict(win, r.broken)"), f"{name}: N5 repair reply"
        assert "delivered with a syntax error" in src, f"{name}: N5 banner title"
    assert 'data-broken="{{BROKEN}}"' in (REPO / "shell" / "bench" / "host.html").read_text() and "document.body.dataset.broken" in host
    assert ".ui-confirm-ok:disabled" in (REPO / "shell" / "shell.css").read_text(), "N4 disabled style"
    mx = js[js.index('querySelector(".win-max").onclick'):js.index('querySelector(".win-improve").onclick')]
    sw = js[js.index("function snapWin(win, zone, i)"):js.index("function unsnapWin(win)")]
    km = (REPO / "shell" / "monitors.js").read_text()
    assert 'snapWin(win, "top")' in mx and "KMon.snapRect(MON.list, mi, zone, dockRoomOn(MON.list[mi]))" in sw \
        and "m.h - MENU - Math.max(0, dockRoom)" in km, "D2 [R90]: maximise = the monitor minus the menu bar and the dock's room"
    assert re.search(r"function dockRoom\(\).*?getBoundingClientRect", js, re.S), "D2: measured from the dock"
    css = (REPO / "shell" / "shell.css").read_text()
    assert ".heal-stop" in css and ".ui-confirm-ok" in css
    # D5/D6 prompt rules on every app prompt + the repair daemon
    import prompts
    for name in ("APP_SYSTEM", "APP_REGEN_SYSTEM", "APP_EVOLVE_SYSTEM"):
        p = getattr(prompts, name)
        assert "NEVER call window.alert(), window.confirm() or window.prompt()" in p and "never a way to ask the USER" in p \
            and "Feature-detect OS features with typeof" in p, name
    assert "never window.alert/confirm/prompt" in prompts.HTML_FIX_SYSTEM and "vibe.ask() is the language model" in prompts.HTML_FIX_SYSTEM
    # D11: the settings recipe names the fields (the LLM rendered "[object Object]" for vram/battery)
    wiki = (REPO / "docs" / "wiki" / "settings-app.md").read_text()
    for need in ("memory_mb", "disk_gb", "health_percent", "vram_mb", "have_gb", "total_gb", "recommended_tier", "llm_model", "[object Object]"):
        assert need in wiki, f"settings-app.md must document {need}"
    api = (REPO / "docs" / "API.md").read_text()
    for need in ("llm_model", "window.prompt", "vibe.fs.format()", "typeof vibe."):
        assert need in api, f"API.md must mention {need}"
    return "D1 cap + D2 dock room + D3 parity + D4 undefined/hint + D5 shims + D6/D5 prompt rules + D9 confirms + D11 shapes documented"


@run_test("unit_n6_docs_reach_the_model", ["R39", "R59", "R43", "R45"])
async def t_n6_docs(_):
    """N6 (docs/REVERIFY-2026-09-28.md): for the dock's Settings prompt the kernel injected only the first
    2400/2800 chars of docs/api/system-settings.md and docs/wiki/settings-app.md, cutting off
    vibe.system.setup()'s vram_mb/total_gb/have_gb and the coder-LLM picker recipe (m.llm/llm_model) —
    the generated app invented info.gpu_vram/tier.name and shipped an empty LLM picker. Now both
    documents carry the shapes first AND the caps admit them whole; the R43 arithmetic (whole blocks
    only when they fit prompt_char_budget) still has room with the 8 GB manifest's -c 16384 — and since the
    2026-10-19 trim (28198 → 23281 of 28278) at least 1000 chars of it stay free (pinned below)."""
    sys.path.insert(0, str(REPO / "kernel"))
    import server, prompts
    dock = re.search(r'data-app="settings" data-prompt="([^"]+)"', (REPO / "shell" / "index.html").read_text()).group(1)
    need =["vibe.system.setup()", "vram_mb", "total_gb", "have_gb", "current_tier", "vibe.hw.get()", "memory_mb",
            "disk_gb", "[object Object]", "m.llm", "llm_model", "vibe.models()", "vibe.models.download(entry.download_key)",   # [R89] no "fast"
            "llm_setting", "fallback", "vibe.system.upgrade", "vibe.fs.format()"]
    api = server.api_docs_for(dock)
    wiki = server.wiki_pages_for(dock)
    assert api and api[0][0] == "system-settings" and wiki and wiki[0][0] == "settings-app", (api, wiki)
    api_text, wiki_text = api[0][1], wiki[0][1]
    assert api_text == server.API_DOCS["system-settings"]["text"], "the API sheet must be injected whole"
    assert wiki_text == server.WIKI["settings-app"]["text"], "the best-matching recipe must be injected whole"
    for n in need:
        assert n in api_text or n in wiki_text, f"{n!r} does not reach the model"
    for n in ("vram_mb", "total_gb", "have_gb", "m.llm", "llm_model", "vibe.hw.get()", "vibe.system.setup()"):
        assert n in api_text and n in wiki_text, f"{n!r} must be in BOTH documents"
    # the essentials sit at the top: even the OLD caps would have carried them now
    assert all(n in api_text[:2400] for n in ("vram_mb", "total_gb", "have_gb", "vibe.hw.get()", "memory_mb", "m.llm", "llm_model")), "API sheet: shapes not first"
    assert all(n in wiki_text[:4200] for n in ("vram_mb", "total_gb", "have_gb", "memory_mb", "m.llm", "llm_model")), "wiki: shapes not first"
    assert "gpu_vram" in wiki_text and "tier.name" in wiki_text, "the invented names are called out as non-existent"
    # the 2026-10-19 trim kept every rule the Settings app needs (somewhere in the pack: dock prompt + sheet + recipe)
    pack = dock + api_text + wiki_text
    for n in ("accent2", "wallpaper", "os_name", "vibe.hw.brightness.set(", "vibe.hw.volume.set(", "vibe.hw.sound.set(",
              "image_model", "image_expand", "image_steps", "licence", "answers adult requests", "m.refusal_fallback.last",
              "cancelled", "two-step", "vibe.system.models()", "available:false", "reason", "Bluetooth", "airplane",
              "wifi-manager recipe", "vibe.hw.datetime", "a bad value rejects", "never whole objects"):
        assert n in pack, f"the Settings pack lost {n!r}"
    # every API sheet fits its cap; secondary wiki pages keep the old cap; the caps are what the functions use
    assert max(len(p["text"]) for p in server.API_DOCS.values()) <= server.API_SHEET_CAP
    assert len(server.WIKI["settings-app"]["text"]) <= server.WIKI_FIRST_CAP and server.WIKI_PAGE_CAP == 2800
    assert len(wiki[1][1]) <= server.WIKI_PAGE_CAP if len(wiki) > 1 else True
    picked = server.wiki_pages_for("a wifi manager to connect to my router")
    assert picked[0][0] == "wifi-manager" and picked[0][1] == server.WIKI["wifi-manager"]["text"]
    # R43: the dock prompt + the OS-written spec + both blocks + facts fit the 8 GB tier's budget
    ctx = server.llm_ctx()
    assert ctx >= 16384, ctx
    budget = server.prompt_char_budget(prompts.APP_SYSTEM)
    api_block = len(prompts.API_HEADER) + sum(len(t) + 20 for _, t in api)
    wiki_block = len(prompts.WIKI_HEADER) + sum(len(t) + 20 for _, t in wiki)
    used = len("Open this app: " + dock) + 2400 + 70 + api_block + wiki_block + 1700
    assert used <= budget, f"dock prompt would overflow: {used} > {budget}"
    # the 2026-10-19 trim freed ~4.9 K (80 → 4997 spare): the pack may not grow back into the last 1000 chars
    assert used <= budget - 1000, f"dock prompt headroom < 1000 chars: {used} > {budget} - 1000 — trim the dock prompt, sheet or recipe first"
    return (f"dock prompt: api sheet {len(api_text)} + recipe {len(wiki_text)} chars injected whole; {used} of {budget} budget chars used "
            f"({budget - used} spare, pinned ≥ 1000; -c {ctx})")


# ---------------- the translation layer [R71] (docs/TRANSLATION-LAYER-2026-10-06.md) ----------------

async def _translation_reset(s):
    """[R71] back to the defaults (off / en / fast) and wait until the fast server is gone."""
    async with s.post(f"{BASE}/api/settings", json={"translation": "off", "language": "en", "translation_quality": "fast"}) as r:
        assert r.status == 200, r.status
    for _ in range(40):
        async with s.get(f"{BASE}/api/translate/status") as r:
            st = await r.json()
        if not st["running"]["fast"]:
            return
        await asyncio.sleep(0.25)


@run_test("unit_translation_manifest_and_specs", ["R71", "R47", "R89"])
async def t_translation_manifest(_):
    """[R71] every tier carries the `translation` add-on: addon:true (tier presence ignores it), a `fast` (2B — CPU:
    -ngl 0 -dev none, parallel slots) GGUF with url / size_bytes / sha256 like the other add-ons, thinking off +
    temperature 0 at the section level. `quality`: [R89] on the 8 GB tier the loaded LLM ({"use": "llm"} — no file,
    no url, no download; the comment names the benchmark), on 16 GB and up still the dedicated 9B Q8_0 on the GPU
    (unchanged: the benchmark left 16 GB open). The kernel's specs, prompt and heuristics resolve; the 8 GB tier's
    "translation" key never resolves to a translation model (no swap)."""
    sys.path.insert(0, str(REPO / "kernel"))
    import server
    for t in ("8gb", "16gb", "24gb", "32gb"):
        m = json.loads((REPO / "manifests" / f"tier-{t}.json").read_text())
        tr = m["translation"]
        assert tr.get("addon") is True and tr.get("engine") == "llama-server", t
        assert tr["languages"] == ["en", "de", "es", "fr", "it", "ja", "ru"], t
        assert tr["chat_template_kwargs"] == {"enable_thinking": False} and tr["sampling"] == {"temperature": 0}, t
        assert "provisional" in tr["comment"].lower() and "benchmark" in tr["comment"].lower(), t
        files = server.section_files(tr)
        want = [tr["fast"]["file"]] + ([] if t == "8gb" else [tr["quality"]["file"]])
        assert [e["file"] for e in files] == want, (t, files)
        for e in files:
            assert e["url"].startswith("https://huggingface.co/IndexTeam/Index-Translate-") and e["url"].endswith(Path(e["file"]).name), e
            assert re.fullmatch(r"[0-9a-f]{64}", e["sha256"]) and e["size_bytes"] > 10**9 \
                and abs(e["size_gb"] - e["size_bytes"] / 1e9) < 0.02, (t, e)
        assert tr["fast"]["file"] == "translation/Index-Translate-2B.Q4_K_M.gguf", t
        fa = tr["fast"]["serve_args"]
        assert fa[fa.index("-ngl") + 1] == "0" and fa[fa.index("-dev") + 1] == "none" and int(fa[fa.index("-np") + 1]) >= 2 \
            and "--jinja" in fa, (t, fa)
        if t == "8gb":                                              # [R89] quality = the loaded LLM
            assert set(tr["quality"]) == {"use", "comment"} and tr["quality"]["use"] == "llm" \
                and "0.8684" in tr["quality"]["comment"] and "FAST-MODELS-2026-10-17" in tr["comment"], tr["quality"]
            assert server.translation_quality_llm(m) and server.translation_llm_spec("quality", m) is None
            assert not any("Index-Translate-9B" in e["file"] for e in server.manifest_files(m, include_addons=True)), "the 9B left the 8 GB tier"
        else:                                                       # unchanged: the dedicated 9B Q8_0
            assert tr["quality"]["file"] == "translation/Index-Translate-9B.Q8_0.gguf" and not server.translation_quality_llm(m), t
            qa = tr["quality"]["serve_args"]
            assert qa[qa.index("-ngl") + 1] == "99" and int(qa[qa.index("-c") + 1]) >= 2048, (t, qa)
        assert not any(e["file"].startswith("translation/") for e in server.manifest_files(m)), f"{t}: the add-on leaks into the baked tier set"
        assert {e["file"] for e in files} <= {e["file"] for e in server.manifest_files(m, include_addons=True)}, t
        for mode in ("fast", "quality"):
            spec = server.translation_llm_spec(mode, m)
            if spec is None:
                assert t == "8gb" and mode == "quality", (t, mode)
                continue
            assert spec["file"] == tr[mode]["file"] and spec["serve_args"] == tr[mode]["serve_args"] \
                and spec["chat_template_kwargs"] == {"enable_thinking": False} and spec["sampling"] == {"temperature": 0}, (t, mode, spec)
    key, spec = server.MM.llm_spec("translation")           # [R89] the 8 GB tier: the coder LLM's section, never a swap
    assert key == "llm" and spec["file"] == server.MANIFEST["llm"]["file"], (key, spec.get("file"))
    m16 = json.loads((REPO / "manifests" / "tier-16gb.json").read_text())
    key, spec = server.ModelManager(m16).llm_spec("translation")   # 16 GB: the dedicated 9B through the swap path, like vision
    assert key == "translation" and spec["file"].endswith("Index-Translate-9B.Q8_0.gguf"), spec
    assert server.LANGUAGES == {"en": "English", "de": "German", "es": "Spanish", "fr": "French",
                                "it": "Italian", "ja": "Japanese", "ru": "Russian"}
    assert "{language}" in server.TRANSLATE_PROMPT and "{text}" in server.TRANSLATE_PROMPT \
        and server.TRANSLATE_PROMPT.startswith("Translate the following text into {language}.")
    assert server.needs_translation("Hello", "de") and not server.needs_translation("12:30 · ◉", "de") \
        and not server.needs_translation("こんにちは", "ja") and server.needs_translation("Hello", "ja") \
        and not server.needs_translation("Привет", "ru") and server.needs_translation("Привет", "de")
    assert server.strip_thinking("<think>x</think>\nHallo") == "Hallo"
    return ("4 tiers: translation addon (fast 2B CPU -ngl 0 -dev none); quality 8 GB = the loaded LLM [R89] (no file, key "
            "'translation' → llm), 16 GB+ the 9B Q8_0 (swap); sha256/size_bytes; specs + prompt + script heuristics")


@run_test("http_translation_settings_validation", ["R71", "R15"])
async def t_translation_settings(s):
    """[R71] the three settings keys are validated like the known keys: a bad value is a 400
    naming the rule and the key, nothing is stored; the defaults are off / en / fast."""
    async with s.get(f"{BASE}/api/settings") as r:
        before = await r.json()
    assert before.get("translation") == "off" and before.get("language") == "en" \
        and before.get("translation_quality") == "fast", before
    bad = [({"key": "translation", "value": "yes"}, "translation"), ({"key": "translation", "value": True}, "translation"),
           ({"key": "translation"}, "translation"), ({"key": "language", "value": "zh"}, "language"),
           ({"key": "language", "value": "German"}, "language"), ({"key": "language", "value": None}, "language"),
           ({"key": "translation_quality", "value": "best"}, "translation_quality"), ({"translation": "on", "language": "xx"}, "language")]
    for body, key in bad:
        async with s.post(f"{BASE}/api/settings", json=body) as r:
            j = await r.json()
            assert r.status == 400 and j["key"] == key and key in j["error"], (body, r.status, j)
    async with s.get(f"{BASE}/api/settings") as r:
        assert await r.json() == before, "a rejected POST must store nothing"
    good = [({"key": "language", "value": "de"}, "language", "de"),
            ({"key": "translation_quality", "value": "quality"}, "translation_quality", "quality"),
            ({"translation": "on", "language": "ja"}, "language", "ja"), ({"key": "translation", "value": "off"}, "translation", "off")]
    for body, k, v in good:
        async with s.post(f"{BASE}/api/settings", json=body) as r:
            j = await r.json()
            assert r.status == 200 and j[k] == v, (body, r.status, j)
    await _translation_reset(s)
    return f"{len(bad)} bad values → 400 naming the key, nothing stored; on/off, the 7 languages, fast/quality accepted; defaults off/en/fast"


@run_test("http_translation_off_is_no_change", ["R71", "R5"])
async def t_translation_off(s):
    """[R71] off (the default), or on with language en: nothing changes — /api/translate is a
    clean 400 naming the setting, no translation server is launched, the coder LLM's prompts are
    byte-identical (the mock logs every request's system text), /api/status says so."""
    sys.path.insert(0, str(REPO / "kernel"))
    import server, prompts
    arglog, msglog = VDIR / "llama-args.log", VDIR / "llama-msgs.log"
    launches = lambda: sum(1 for ln in (arglog.read_text().splitlines() if arglog.exists() else []) if "Index-Translate" in ln)
    msgs = lambda: [json.loads(ln) for ln in (msglog.read_text().splitlines() if msglog.exists() else [])]
    await _translation_reset(s)
    n0, m0 = launches(), len(msgs())
    async with s.post(f"{BASE}/api/translate", json={"text": "Hello"}) as r:
        j = await r.json()
        assert r.status == 400 and "translation is off" in j["error"] \
            and "vibe.settings.set('translation', 'on')" in j["error"], (r.status, j)
    async with s.post(f"{BASE}/api/ask", json={"prompt": "say hi [off-test]"}) as r:
        assert r.status == 200
    new = [m for m in msgs()[m0:] if m["user"] and "[off-test]" in m["user"]]
    # [R86 r4b, S5] the uncensored default (the heretic) carries the one limits line at the END of every system prompt
    assert new and new[-1]["system"] == prompts.ASK_SYSTEM.rstrip() + "\n\n" + prompts.UNCENSORED_LIMITS and new[-1]["port"] == 8081, \
        "ASK_SYSTEM (+ the uncensored default's limits line) must be byte-identical with translation off"
    # in-process: the payload builder adds nothing while off
    assert server.language_instruction(prompts.APP_SYSTEM) == ""
    p = server.MM.chat_payload("llm", prompts.APP_SYSTEM, [{"role": "user", "content": "x"}], 10, 0.5, False)
    assert p["messages"][0] == {"role": "system", "content": prompts.APP_SYSTEM}, p["messages"][0]["content"][-120:]
    # on + English = the same nothing: no server, no prompt change, an explicit call is the identity
    for k, v in (("translation", "on"), ("language", "en")):
        async with s.post(f"{BASE}/api/settings", json={"key": k, "value": v}) as r:
            assert r.status == 200
    assert server.language_instruction(prompts.APP_SYSTEM) == "" and server.translation_active() is False
    # an app's own call with an English source is the identity (a call naming another language is the
    # app's business: the model then starts on demand — not the OS doing anything by itself)
    async with s.post(f"{BASE}/api/translate", json={"texts": ["Hello", "World"], "from": "en"}) as r:
        j = await r.json()
        assert r.status == 200 and j["texts"] == ["Hello", "World"] and j["calls"] == 0 and j["model"] is None, j
    async with s.get(f"{BASE}/api/status") as r:
        st = (await r.json())["translation"]
    assert st["enabled"] is True and st["active"] is False and st["language"] == "en" \
        and st["running"] == {"fast": False, "quality": False}, st
    assert launches() == n0, "no translation server may start while off / English"
    await _translation_reset(s)
    async with s.get(f"{BASE}/api/status") as r:
        assert (await r.json())["translation"]["enabled"] is False
    return "off: /api/translate 400 naming the setting, ASK_SYSTEM unchanged, no launch; on+en: identity, no launch, no prompt change"


@run_test("http_translate_api_cache_and_addon", ["R71", "R47"])
async def t_translate_api(s):
    """[R71] on + German: POST /api/translate — one text, a batch, the persistent cache (a repeat =
    no model call), `to`/`from` explicit, the think block stripped, the 400s; the fast server is
    launched ONCE on the CPU (-ngl 0 -dev none, its own port, the add-on's 2B file) with the
    model-card request shape; /api/models lists the add-on and the download fetches its one file (the
    2B — [R89] quality is the loaded LLM on this tier, nothing to fetch); a quality choice whose LLM file is
    not on disk (a public image still downloading it) falls back to fast; fast absent too = a clean 400
    naming the download; switching off stops the server."""
    sys.path.insert(0, str(REPO / "kernel"))
    import server
    arglog, msglog = VDIR / "llama-args.log", VDIR / "llama-msgs.log"
    tr_lines = lambda: [ln for ln in (arglog.read_text().splitlines() if arglog.exists() else []) if "Index-Translate" in ln]
    mt_calls = lambda: sum(1 for ln in (msglog.read_text().splitlines() if msglog.exists() else []) if json.loads(ln)["port"] == 8085)
    await _translation_reset(s)
    for k, v in (("translation", "on"), ("language", "de")):
        async with s.post(f"{BASE}/api/settings", json={"key": k, "value": v}) as r:
            assert r.status == 200
    n_launch = len(tr_lines())
    async with s.post(f"{BASE}/api/translate", json={"text": "Hello world"}) as r:
        j = await r.json()
    assert r.status == 200 and j["text"] == "[de] Hello world" and j["to"] == "de" and j["from"] is None \
        and j["model"] == "fast" and j["calls"] == 1 and j["cached"] == 0, j
    launch = tr_lines()
    assert len(launch) == n_launch + 1, launch
    last = launch[-1]
    assert f"-m {VDIR}/models/translation/Index-Translate-2B.Q4_K_M.gguf" in last and "--port 8085" in last \
        and "-ngl 0" in last and "-dev none" in last and "-np 4" in last and "--mmproj" not in last, last
    mt = [json.loads(ln) for ln in msglog.read_text().splitlines() if json.loads(ln)["port"] == 8085]
    assert mt and mt[-1]["system"] is None and mt[-1]["chat_template_kwargs"] == {"enable_thinking": False} \
        and mt[-1]["temperature"] == 0 \
        and mt[-1]["user"] == server.TRANSLATE_PROMPT.format(language="German", text="Hello world"), mt[-1]
    # a batch: one cached, one numbers-only (skipped), one new text twice → ONE model call, no second launch
    before = mt_calls()
    async with s.post(f"{BASE}/api/translate", json={"texts": ["Hello world", "Open this app", "12:30", "Open this app"]}) as r:
        j = await r.json()
    assert r.status == 200 and j["texts"] == ["[de] Hello world", "[de] Open this app", "12:30", "[de] Open this app"] \
        and j["cached"] == 1 and j["calls"] == 1, j
    assert mt_calls() == before + 1 and len(tr_lines()) == n_launch + 1, "one call for the one new text, no second launch"
    cache = json.loads((VDIR / "cache" / "translate" / "de.json").read_text())
    assert cache["language"] == "de" and cache["entries"].get("\x1fHello world") == "[de] Hello world", cache
    before = mt_calls()
    async with s.post(f"{BASE}/api/translate", json={"text": "Hello world"}) as r:
        j = await r.json()
    assert j["cached"] == 1 and j["calls"] == 0 and mt_calls() == before, j
    async with s.post(f"{BASE}/api/translate", json={"text": "Guten Morgen", "to": "en", "from": "de"}) as r:
        j = await r.json()
    assert r.status == 200 and j["text"] == "[en] Guten Morgen" and j["to"] == "en" and j["from"] == "de", j
    async with s.post(f"{BASE}/api/translate", json={"text": "x", "to": "en", "from": "en"}) as r:
        assert (await r.json())["calls"] == 0, "from == to is the identity"
    for body in ({"text": "x", "to": "zh"}, {"text": "x", "from": "xx"}, {"texts": "x"}, {"texts": []}, {"texts": [1]},
                 {"text": "x" * 5000}, {"texts": ["x"] * 201}):
        async with s.post(f"{BASE}/api/translate", json=body) as r:
            assert r.status == 400 and "error" in (await r.json()), body
    async with s.get(f"{BASE}/api/translate/status") as r:
        st = await r.json()
    assert st["enabled"] and st["active"] and st["language"] == "de" and st["running"] == {"fast": True, "quality": False} \
        and st["effective"] == "fast" and st["present"] == {"fast": True, "quality": True} and st["cache"]["de"] >= 2 \
        and st["languages"] == ["en", "de", "es", "fr", "it", "ja", "ru"] and st["names"]["ja"] == "Japanese", st
    async with s.get(f"{BASE}/api/models") as r:
        mi = await r.json()
    ex = next(e for e in mi["extras"] if e["key"] == "translation")
    assert ex["addon"] is True and ex["present"] is True and ex["available"] is True and ex["kind"] == "translation" \
        and abs(ex["download_gb"] - 1.3) < 0.05 and ex["missing_gb"] == 0 and "Index-Translate" in ex["name"], ex
    assert mi["translation"]["language"] == "de" and mi["overrides"]["translation"] == "on", mi["translation"]
    async with s.post(f"{BASE}/api/models/download", json={"key": "translation"}) as r:
        j = await r.json(); assert r.status == 200 and j.get("already") is True, j
    calls, real = [], server.download_entries
    async def rec(entries, state):
        calls.append([e["file"] for e in entries])
    server.download_entries = rec
    try:
        await server.run_model_download("translation", "translation")
    finally:
        server.download_entries = real
    assert calls == [["translation/Index-Translate-2B.Q4_K_M.gguf"]], calls          # [R89] no 9B on this tier
    qf = VDIR / "models" / server.MANIFEST["llm"]["file"]          # [R89] the quality translator = the loaded LLM's file
    qh = qf.with_name(qf.name + ".hidden")
    ff = VDIR / "models" / "translation" / "Index-Translate-2B.Q4_K_M.gguf"
    qf.rename(qh)
    try:
        async with s.post(f"{BASE}/api/settings", json={"key": "translation_quality", "value": "quality"}) as r:
            assert r.status == 200
        await asyncio.sleep(0.5)
        async with s.get(f"{BASE}/api/translate/status") as r:
            st = await r.json()
        assert st["quality"] == "quality" and st["effective"] == "fast" and "language model isn't on disk" in st["fallback"] \
            and st["quality_llm"] is True, st
        async with s.post(f"{BASE}/api/translate", json={"text": "Quality please"}) as r:
            j = await r.json()
        assert r.status == 200 and j["text"] == "[de] Quality please" and j["model"] == "fast", j
        ff.unlink()
        async with s.post(f"{BASE}/api/translate", json={"text": "Nothing here"}) as r:
            j = await r.json()
        assert r.status == 400 and "translation add-on" in j["error"] and "vibe.models.download('translation')" in j["error"] \
            and "Traceback" not in j["error"], (r.status, j)
        async with s.get(f"{BASE}/api/models") as r:
            ex = next(e for e in (await r.json())["extras"] if e["key"] == "translation")
        assert ex["present"] is False and ex["available"] is False, ex
    finally:
        if qh.exists():
            qh.rename(qf)
        ff.touch()
        async with s.post(f"{BASE}/api/settings", json={"key": "translation_quality", "value": "fast"}) as r:
            assert r.status == 200
    async with s.post(f"{BASE}/api/settings", json={"key": "translation", "value": "off"}) as r:
        assert r.status == 200
    for _ in range(40):
        await asyncio.sleep(0.25)
        async with s.get(f"{BASE}/api/translate/status") as r:
            st = await r.json()
        if not st["running"]["fast"]:
            break
    assert st["running"]["fast"] is False and st["enabled"] is False, st
    await _translation_reset(s)
    return ("single/batch/cache (de.json; repeat = 0 calls)/to+from/7 bad bodies 400; ONE CPU launch (-ngl 0 -dev none :8085, "
            "no system msg, thinking off, temp 0); extras + download = the 2B only [R89]; quality (LLM file absent)→fast fallback; "
            "absent → 400; off stops the server")


@run_test("http_translation_present_modes_fast_baked", ["R71", "R47", "R89"])
async def t_translation_present_modes(s):
    """[present_modes fix 2026-10-07] image v7.3 bakes only the translation add-on's fast file
    (docs/IMAGE-BAKE-ADDONS-2026-10-07.md) — /api/models (= vibe.models()) must say the layer is there.
    [R89] On the 8 GB tier `quality` is the loaded LLM: extras[translation] = the fast file alone (download_gb 1.3),
    present_modes.quality = the LLM's file on disk, never part of missing_gb or the download. Live kernel: both →
    present, missing 0, the download answers 'already'; fast gone → present (the LLM translates), missing_gb = the 2B,
    the download starts and fetches the 2B only; the LLM file gone → present_modes.quality false, missing 0; neither →
    present false. In-process, the 16 GB tier's dedicated 9B (unchanged by R89): fast only → missing_gb = the 9B,
    section_present() stays "every file" (the download starts, both entries handed over) and the real
    download_entries skips the file on disk and runs aria2c for the 9B only. Every other add-on's `present` is untouched."""
    sys.path.insert(0, str(REPO / "kernel"))
    import server
    sec = server.MANIFEST["translation"]
    ff = VDIR / "models" / sec["fast"]["file"]
    lf = VDIR / "models" / server.MANIFEST["llm"]["file"]
    lh = lf.with_name(lf.name + ".hidden")
    assert ff.exists() and lf.exists() and server.translation_quality_llm(), "the runner touches the placeholders"

    async def entry():
        async with s.get(f"{BASE}/api/models") as r:
            mi = await r.json()
        return mi, next(e for e in mi["extras"] if e["key"] == "translation")

    mi, ex = await entry()
    others = {e["key"]: e["present"] for e in mi["extras"] if e["key"] != "translation"}
    assert ex["present"] is True and ex["present_modes"] == {"fast": True, "quality": True} and ex["missing_gb"] == 0 \
        and abs(ex["download_gb"] - 1.3) < 0.05 and ex["available"] is True, ex
    async with s.post(f"{BASE}/api/models/download", json={"key": "translation"}) as r:
        assert (await r.json()).get("already") is True
    launched, calls = [], []
    real_dl, real_exec = server.download_entries, server.asyncio.create_subprocess_exec
    saved_tr = server.MANIFEST["translation"]

    class Req:
        async def json(self):
            return {"key": "translation"}

    async def rec(entries, state):
        calls.append([e["file"] for e in entries])

    async def download_once():
        server.MODEL_DL.pop("translation", None)          # the in-process state an earlier test left behind
        resp = await server.h_model_download(Req())
        j = json.loads(resp.text)
        for _ in range(40):
            await asyncio.sleep(0.05)                     # the handler's create_task runs on the next yield
            if server.MODEL_DL.get("translation", {}).get("status") == "done":
                break
        return resp, j
    try:
        # fast gone (a public image whose first-boot download is still running): the LLM still translates
        ff.unlink()
        mi, ex = await entry()
        assert ex["present"] is True and ex["present_modes"] == {"fast": False, "quality": True} and ex["available"] is True \
            and ex["missing_gb"] == round(sec["fast"]["size_gb"], 2), ex
        assert {e["key"]: e["present"] for e in mi["extras"] if e["key"] != "translation"} == others, "other add-ons untouched"
        server.download_entries = rec
        resp, j = await download_once()
        assert resp.status == 200 and j.get("started") is True and j["download_gb"] == ex["missing_gb"], j
        assert server.MODEL_DL["translation"]["status"] == "done" and calls == [[sec["fast"]["file"]]], calls
        ff.touch()
        # the LLM's file gone: quality is not there, the add-on itself is complete
        lf.rename(lh)
        mi, ex = await entry()
        assert ex["present"] is True and ex["present_modes"] == {"fast": True, "quality": False} and ex["missing_gb"] == 0, ex
        ff.unlink()
        mi, ex = await entry()
        assert ex["present"] is False and ex["present_modes"] == {"fast": False, "quality": False} and ex["available"] is False, ex
        ff.touch(); lh.rename(lf)
        # the 16 GB tier's dedicated 9B (in-process): present_modes per file, the download fetches what is missing
        m16 = json.loads((REPO / "manifests" / "tier-16gb.json").read_text())
        server.MANIFEST["translation"] = m16["translation"]
        q16 = m16["translation"]["quality"]
        qf = VDIR / "models" / q16["file"]
        assert qf.exists(), "the runner touches every tier's files"
        assert server.translation_presence() == {"present": True, "present_modes": {"fast": True, "quality": True}, "missing_gb": 0}
        qf.unlink()
        assert server.translation_presence() == {"present": True, "present_modes": {"fast": True, "quality": False},
                                                 "missing_gb": round(q16["size_gb"], 2)}
        assert server.section_present(server.MANIFEST["translation"]) is False
        calls.clear()
        resp, j = await download_once()
        assert resp.status == 200 and j.get("started") is True and j["download_gb"] == round(q16["size_gb"], 2), j
        assert calls == [[sec["fast"]["file"], q16["file"]]], calls
        server.download_entries = real_dl

        class Proc:
            returncode = 1

            async def wait(self):
                return 1

        async def fake_exec(*argv, **kw):
            launched.append(list(argv))
            return Proc()
        server.asyncio.create_subprocess_exec = fake_exec
        state = {"current": ""}
        await server.download_entries([sec["fast"]], state)
        assert launched == [] and state["current"] == "", "the present fast file must not be fetched again"
        try:
            await server.download_entries([sec["fast"], q16], state)
            raise AssertionError("the 9B's (failed) aria2c must surface")
        except RuntimeError as e:
            assert "download failed: " + q16["file"] in str(e), e
        assert len(launched) == 1 and launched[0][0] == "aria2c" and q16["url"] in launched[0] \
            and launched[0][launched[0].index("-o") + 1] == Path(q16["file"]).name, launched
        assert not qf.exists(), "a failed aria2c leaves no file"
        qf.touch()
    finally:
        server.download_entries, server.asyncio.create_subprocess_exec = real_dl, real_exec
        server.MANIFEST["translation"] = saved_tr
        server.MODEL_DL.pop("translation", None)
        ff.touch()
        if lh.exists():
            lh.rename(lf)
        q = VDIR / "models" / "translation" / "Index-Translate-9B.Q8_0.gguf"
        q.parent.mkdir(parents=True, exist_ok=True)
        q.touch()
    mi, ex = await entry()
    assert ex["present"] is True and ex["present_modes"] == {"fast": True, "quality": True} and ex["missing_gb"] == 0, ex
    return ("8 GB [R89]: fast + LLM → present, missing 0, 'already'; fast gone → present via the LLM, the download fetches the "
            "2B only; LLM gone → quality false, missing 0; neither → absent. 16 GB 9B: fast only → missing 9.79, both entries "
            "handed over, aria2c for the 9B only; other add-ons unchanged")


@run_test("http_translation_llm_language_and_english_hop", ["R71", "R5", "R42"])
async def t_translation_llm(s):
    """[R71] on + German: the coder LLM's user-facing prompts (vibe.ask here; apps / pages / games
    share the one hook) end with the one LANGUAGE line naming German; the repair / spec / expander /
    vision / wallpaper prompts never get it. P2: an image prompt for a section without an expander
    goes through the MT model into English before the engine sees it, the Krea expander is told to
    write English instead (no MT call), a knowledge query is turned into English first and the
    results come back in the user's language."""
    sys.path.insert(0, str(REPO / "kernel"))
    import server, prompts
    msglog = VDIR / "llama-msgs.log"
    reqs = lambda: [json.loads(ln) for ln in msglog.read_text().splitlines()]
    await _translation_reset(s)
    for k, v in (("translation", "on"), ("language", "de")):
        async with s.post(f"{BASE}/api/settings", json={"key": k, "value": v}) as r:
            assert r.status == 200
    line = prompts.LANGUAGE_INSTRUCTION.format(language="German")
    assert "German" in line and "{" not in line
    for sysm, key in ((prompts.APP_SYSTEM, "llm"), (prompts.APP_EVOLVE_SYSTEM, "llm"), (prompts.APP_REGEN_SYSTEM, "llm"),
                      (prompts.GAME_SYSTEM, "python_llm"), (prompts.BROWSER_SYSTEM, "llm"), (prompts.ASK_SYSTEM, "llm")):
        assert server.language_instruction(sysm, key) == line, key
    for sysm, key in ((prompts.HTML_FIX_SYSTEM, "llm"), (prompts.GAME_FIX_SYSTEM, "python_llm"), (prompts.SPEC_SYSTEM, "llm"),
                      (prompts.KREA_EXPAND_SYSTEM, "expander:image"), (prompts.VISION_SYSTEM, "vision"),
                      (prompts.WALLPAPER_SYSTEM, "llm"), (prompts.APP_SYSTEM, "translation")):
        assert server.language_instruction(sysm, key) == "", (key, sysm[:40])
    p = server.MM.chat_payload("llm", prompts.APP_SYSTEM, [{"role": "user", "content": "x"}], 10, 0.5, False)
    assert p["messages"][0]["content"] == prompts.APP_SYSTEM + line
    # the fast MT server's body (TranslationService.chat: the fast spec — [R89] the key alone resolves to the LLM on 8 GB)
    p = server.MM.chat_payload("translation", None, [{"role": "user", "content": "x"}], 10, 0, False, spec=server.translation_llm_spec("fast"))
    assert p == {"messages": [{"role": "user", "content": "x"}], "max_tokens": 10, "temperature": 0, "stream": False,
                 "chat_template_kwargs": {"enable_thinking": False}}, p
    n = len(reqs())
    async with s.post(f"{BASE}/api/ask", json={"prompt": "say hi [lang-test]"}) as r:
        assert r.status == 200
    new = [m for m in reqs()[n:] if m["port"] == 8081 and m["user"] and "[lang-test]" in m["user"]]
    assert new and new[-1]["system"] == prompts.ASK_SYSTEM.rstrip() + "\n\n" + prompts.UNCENSORED_LIMITS + line, \
        (new[-1]["system"] if new else None)   # [R86 r4b, S5] the limits line sits before the language line
    # P2 (a): classic has no expander → the prompt reaches sd-cli as the MT model's English
    sdlog = Path(os.environ.get("MOCK_SD_ARGLOG", VDIR / "sd-args.log"))
    async with s.post(f"{BASE}/api/image", json={"prompt": "ein roter Fuchs", "model": "classic"}) as r:
        jid = (await r.json())["job"]
    job = await poll_job(s, jid)
    last = sdlog.read_text().splitlines()[-1]
    assert "[en] ein roter Fuchs" in last and job["prompt"].startswith("[en] ein roter Fuchs"), (last[-200:], job.get("prompt"))
    hop = [m for m in reqs() if m["port"] == 8085 and m["user"].endswith("\n\nein roter Fuchs")]
    assert hop and hop[-1]["user"].startswith("Translate the following text into English."), hop
    # P2 (b): the default (Krea) section has an expander → it is told to write English, no MT hop
    n = len(reqs())
    async with s.post(f"{BASE}/api/image", json={"prompt": "eine blaue Katze", "expand": "on"}) as r:
        jid = (await r.json())["job"]
    await poll_job(s, jid)
    exp = [m for m in reqs()[n:] if m["system"] and m["system"].startswith("You are an expert prompt engineer")]
    assert exp and exp[-1]["system"].endswith(prompts.KREA_EXPAND_ENGLISH.rstrip() + "\n\n" + prompts.UNCENSORED_LIMITS), \
        "the expander must carry the English rule (+ the uncensored default's limits line [R86 r4b, S5])"
    assert not [m for m in reqs()[n:] if m["port"] == 8085], "no MT call when the expander does the English"
    # P2 (c): a knowledge query is turned into English first (offline here; the hop is what we can see)
    n = len(reqs())
    async with s.post(f"{BASE}/api/search", json={"query": "Albert Einstein Biographie"}) as r:
        res = await r.json()
    assert res.get("query") == "[en] Albert Einstein Biographie", res
    hop = [m for m in reqs()[n:] if m["port"] == 8085]
    assert hop and hop[0]["user"].startswith("Translate the following text into English."), hop
    # knowledge answers go back in the user's language (the result plumbing, with the model faked in-process)
    real = server.translate_texts
    async def fake(texts, to, frm=None, settings=None, prefer=None):
        return [f"[{to}] {t}" for t in texts], {"model": "fake", "calls": len(texts), "cached": 0, "ms": 0}
    server.translate_texts = fake
    try:
        loc = await server.localize_results({"online": False, "results": [{"title": "Fox", "snippet": "A fox is an animal.", "url": "u"}]})
    finally:
        server.translate_texts = real
    assert loc["results"][0]["title"] == "[de] Fox" and loc["results"][0]["snippet"] == "[de] A fox is an animal." \
        and loc["translated"]["to"] == "de", loc
    await _translation_reset(s)
    assert server.language_instruction(prompts.APP_SYSTEM) == ""
    return ("ASK/APP/GAME/BROWSER prompts + the German line, fix/spec/expander/vision/wallpaper untouched; classic prompt → '[en] …' "
            "via the MT hop; the Krea expander told English (no hop); search query → English, results back in German")


@run_test("unit_translation_shell_bridge_docs_static", ["R71", "R12", "R39", "R59"])
async def t_translation_shell_static(_):
    """[R71] the shell translates its own UI and hands the SAME translator to every app iframe:
    kairosTranslator in shell.js and its verbatim copy inside BRIDGE_SRC (which stays inert — no
    backslash / backtick / dollar-brace), vibe.translate in the bridge, the translate + translateLang
    handlers in both host pages, the untranslatable spots marked, the settings hook; and the docs the
    coder LLM gets for a settings app carry the keys, the picker, the switch, the download and
    vibe.translate — both injected whole."""
    sys.path.insert(0, str(REPO / "kernel"))
    import server
    js = (REPO / "shell" / "shell.js").read_text()
    host = (REPO / "shell" / "bench" / "host.js").read_text()
    bridge = re.search(r"const BRIDGE_SRC = `(.*?)`;\n", js, re.S).group(1)
    assert "${" not in bridge and "`" not in bridge and not re.search(r"\\(?!/script>)", bridge), "bridge literal must stay inert"
    starts = list(re.finditer(r"^( *)function kairosTranslator\(translateBatch, win\) \{$", js, re.M))
    assert len(starts) == 2, f"expected the translator in the shell and in BRIDGE_SRC, found {len(starts)}"
    copies = []
    for m in starts:
        ind = m.group(1)
        end = js.index("\n" + ind + "}\n", m.end())
        copies.append(js[m.start():end + len(ind) + 2])
    norm = lambda t: [ln.strip() for ln in t.strip().splitlines()]
    assert norm(copies[0]) == norm(copies[1]), "the shell's and the bridge's kairosTranslator differ"
    inside = [c for c in copies if c in bridge]                  # BRIDGE_SRC sits above the shell's own copy
    assert len(inside) == 1 and len(copies[0]) > 3000, f"{len(inside)} copies inside BRIDGE_SRC, {len(copies[0])} chars"
    shell_copy = next(c for c in copies if c not in bridge)
    body = bridge.split("<script>", 1)[1]
    for needle in ('translate: (text, to) => call("translate", text, to)', 'call("translateLang")', '"vibeTranslate"',
                   'typeof document.createTreeWalker === "function"', "DOMContentLoaded"):
        assert needle in body, needle
    for name, src in (("shell.js", js), ("host.js", host)):
        h = re.search(r"async function handleBridge\([^)]*\) \{.*?\n\}", src, re.S).group(0)
        assert 'method === "translate"' in h and 'method === "translateLang"' in h and '"/api/translate"' in h, name
    assert "function setTranslation(lang)" in js and "const TRANSLATOR = kairosTranslator(" in js \
        and 'setTranslation(s.translation === "on" && s.language && s.language !== "en" ? s.language : null)' in js \
        and "vibeTranslate: lang" in js and 'class="gen-code" translate="no"' in js
    index = (REPO / "shell" / "index.html").read_text()
    assert 'id="mb-clock" translate="no"' in index and 'id="mb-tier" translate="no"' in index
    for needle in ('getAttribute("translate") === "no"', "isContentEditable", "TEXTAREA: 1, INPUT: 1, CODE: 1, PRE: 1",
                   "data-kairos-orig", "MutationObserver", "WeakMap", "var restore = function ()", "c > 6"):
        assert needle in shell_copy, needle
    # the docs the coder LLM gets for the dock's Settings app
    dock = re.search(r'data-app="settings" data-prompt="([^"]+)"', index).group(1)
    assert "language" in dock and "translation" in dock and "vibe.models.download('translation')" in dock
    api, wiki = server.api_docs_for(dock), server.wiki_pages_for(dock)
    assert api[0][0] == "system-settings" and wiki[0][0] == "settings-app", (api, wiki)
    sheet, recipe = api[0][1], wiki[0][1]
    assert sheet == server.API_DOCS["system-settings"]["text"] and recipe == server.WIKI["settings-app"]["text"], "injected whole"
    for n in ("translation", "language", "translation_quality", "m.translation", "languages", 'vibe.settings.set("language"',
              'vibe.models.download("translation")', "vibe.translate"):
        assert n in sheet and n in recipe, f"{n!r} must reach the model through both documents"
    assert "vibe.translate(" in server.API_DOCS["core"]["text"] and "translate" in server.API_DOCS["core"]["keywords"]
    apidoc = (REPO / "docs" / "API.md").read_text()
    for n in ("### Language & translation", "`translation`", "`translation_quality`", "/api/translate", "vibe.translate(",
              'vibe.models.download("translation")'):
        assert n in apidoc, n
    assert "R71 — " in (REPO / "docs" / "REQUIREMENTS.md").read_text()
    return (f"translator {len(shell_copy)} chars identical in shell + bridge (inert literal); handlers in both host pages; "
            f"settings hook + translate=no spots; settings sheet {len(sheet)} + recipe {len(recipe)} chars carry keys/picker/switch/download/vibe.translate")


# ---------------- R71 P3: dictionaries, Wiktionary add-ons, the two fixes (docs/TRANSLATION-LAYER-2026-10-06.md "P3 + fixes") ----------------

_DICTD_B64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"


def _dictd_b64(n: int) -> str:
    s = ""
    while True:
        s = _DICTD_B64[n % 64] + s
        n //= 64
        if not n:
            return s


def _write_dict_fixtures(models: Path) -> Path:
    """Tiny archives in the REAL formats over the placeholders run-tests.sh touched: a BEOLINGUS
    de-en.txt.gz (`de :: en`, `|` groups, `;` synonyms, {n} [ugs.] annotations) and a FreeDict eng-spa
    dictd .tar.xz (`<pair>.index` with dictd's base64 offsets + a gzip `<pair>.dict.dz`, WikDict-style
    entries with //pron// and <pos>, a definition line, numbered senses)."""
    import gzip, io, tarfile
    d = models / "dict"
    d.mkdir(parents=True, exist_ok=True)
    ding = ("# Version :: fixture\n"
            "Haus {n} | Häuser {pl} | ein Haus bauen :: house | houses | to build a house\n"
            "Hau ab! [ugs.] :: Bugger off!\n"
            "Katze {f} [zool.] :: cat; feline [formal]\n"
            "Apfel {m} | Äpfel {pl} :: apple | apples\n")
    with gzip.open(d / "beolingus-de-en.txt.gz", "wt", encoding="utf-8") as f:
        f.write(ding)
    entries = [("00-database-info", "00-database-info\nEnglish-Spanish fixture\n\nAvailability:\n\n    test\n"),
               ("apple", "apple //ˈapəl// /[ɛːpɐl]/ <n>\nmanzana, poma\nA tree of the genus Malus; especially Malus domestica which is cultivated for its edible fruit.\n"),
               ("apple tree", "apple tree /ˈæpəl tɹiː/ <n>\nmanzano\n"),
               ("house", "house /haʊs/ <n>\n1. casa\nA structure built or serving as an abode of human beings.\n2. hogar\n")]
    data, index = b"", []
    for hw, text in entries:
        b = text.encode("utf-8")
        index.append(f"{hw}\t{_dictd_b64(len(data))}\t{_dictd_b64(len(b))}")
        data += b + b"\n"
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb") as g:
        g.write(data)
    with tarfile.open(d / "freedict-eng-spa-2025.11.23.dictd.tar.xz", "w:xz") as t:
        for name, blob in (("eng-spa/eng-spa.index", "\n".join(sorted(index)).encode() + b"\n"),
                           ("eng-spa/eng-spa.dict.dz", buf.getvalue()), ("eng-spa/COPYING", b"fixture\n")):
            ti = tarfile.TarInfo(name)
            ti.size = len(blob)
            t.addfile(ti, io.BytesIO(blob))
    return d


@run_test("unit_p3_dictionaries_wiktionary_manifests", ["R71", "R47"])
async def t_p3_manifests(_):
    """[R71 P3] every tier (+ the three 8 GB test variants) carries the `dictionaries` add-on (BEOLINGUS +
    the 37 FreeDict pairs that exist among en/de/es/fr/it/ja/ru, url/size/sha256 — and sha512 for FreeDict —
    with a licence per dictionary) and the 7 `wiktionary_<lang>` knowledge add-ons (`store: "knowledge"`,
    one ZIM each); none of them leaks into the baked set; the kernel resolves the download keys, the store
    dir, the 42 lookup sources (reversed only where no forward file exists), `--cache-ram 0` on both
    translation servers; the parsers and the fold read the real formats. Checksums are cross-checked against
    the download queue when it is mounted (/images/pod)."""
    sys.path.insert(0, str(REPO / "kernel"))
    import server, dictionary
    langs3 = ["eng", "deu", "spa", "fra", "ita", "jpn", "rus"]
    expected_pairs = {f"{a}-{b}" for a in langs3 for b in langs3 if a != b} - {"ita-fra", "jpn-ita", "jpn-spa", "spa-jpn", "rus-jpn"}
    assert len(expected_pairs) == 37
    queue = {}
    qf = Path("/images/pod/translation-2026-10-06.queue")
    if qf.exists():
        for ln in qf.read_text().splitlines():
            if ln.strip() and not ln.startswith("#"):
                h, size, url, name, _d = ln.split()
                queue[name] = (h, int(size), url)
    files = sorted((REPO / "manifests").glob("tier-*.json")) + sorted((REPO / "manifests" / "test").glob("tier-8gb-*.json"))
    files = [f for f in files if ".bak" not in f.name]
    assert len(files) == 7, files
    for p in files:
        m = json.loads(p.read_text())
        d = m["dictionaries"]
        assert d.get("addon") is True and d.get("kind") == "dictionary" and d["languages"] == ["en", "de", "es", "fr", "it", "ja", "ru"], p.name
        assert "GPL" in d["comment"] and "CC-BY-SA" in d["comment"] and "sha512" in d["comment"], p.name
        b = d["beolingus"]
        assert b["format"] == "ding" and b["bidirectional"] is True and b["pair"] == "deu-eng" and b["licence"] == "GPL-2+" \
            and b["file"] == "dict/beolingus-de-en.txt.gz" and b["url"].endswith("/de-en.txt.gz") and re.fullmatch(r"[0-9a-f]{64}", b["sha256"]) \
            and b["size_bytes"] == 7804867, (p.name, b)
        assert set(d["freedict"]) == expected_pairs, (p.name, set(d["freedict"]) ^ expected_pairs)
        for pair, e in d["freedict"].items():
            assert e["format"] == "dictd" and e["pair"] == pair and e["version"] and e["licence"] \
                and e["file"] == f"dict/freedict-{pair}-{e['version']}.dictd.tar.xz" \
                and e["url"] == f"https://download.freedict.org/dictionaries/{pair}/{e['version']}/freedict-{pair}-{e['version']}.dictd.tar.xz" \
                and re.fullmatch(r"[0-9a-f]{64}", e["sha256"]) and re.fullmatch(r"[0-9a-f]{128}", e["sha512"]) \
                and e["size_bytes"] > 50000 and abs(e["size_gb"] - e["size_bytes"] / 1e9) < 0.001, (p.name, pair, e)
            if queue:
                qh, qsize, qurl = queue[Path(e["file"]).name]
                assert qh == e["sha512"] and qsize == e["size_bytes"] and qurl == e["url"], (pair, "queue mismatch")
        if queue:
            assert queue["beolingus-de-en.txt.gz"][0] == b["sha256"] and queue["beolingus-de-en.txt.gz"][1] == b["size_bytes"]
        assert len(server.section_files(d)) == 38 and all("store" not in e for e in server.section_files(d)), p.name
        for lang in ("en", "de", "es", "fr", "it", "ja", "ru"):
            w = m[f"wiktionary_{lang}"]
            assert w.get("addon") is True and w.get("kind") == "knowledge" and w.get("store") == "knowledge" and w["language"] == lang, (p.name, lang)
            assert re.fullmatch(rf"wiktionary_{lang}_all_nopic_\d{{4}}-\d{{2}}\.zim", w["file"]) and "/" not in w["file"] \
                and w["url"] == f"https://download.kiwix.org/zim/wiktionary/{w['file']}" and re.fullmatch(r"[0-9a-f]{64}", w["sha256"]) \
                and w["size_bytes"] > 3 * 10**8 and abs(w["size_gb"] - w["size_bytes"] / 1e9) < 0.01 and "CC-BY-SA" in w["comment"], (p.name, lang, w)
            if queue:
                assert queue[w["file"]][0] == w["sha256"] and queue[w["file"]][1] == w["size_bytes"], (lang, "queue mismatch")
            assert server.section_files(w) == [w], lang
        baked = [e["file"] for e in server.manifest_files(m)]
        assert not any(f.startswith("dict/") or f.startswith("wiktionary_") for f in baked), f"{p.name}: an add-on leaks into the baked set"
        every = {e["file"] for e in server.manifest_files(m, include_addons=True)}
        assert sum(1 for f in every if f.startswith("dict/")) == 38 and sum(1 for f in every if f.startswith("wiktionary_")) == 7, p.name
        for mode in ("fast", "quality"):
            if "serve_args" not in m["translation"][mode]:
                assert mode == "quality" and m["translation"][mode].get("use") == "llm", (p.name, mode)   # [R89] the loaded LLM
                continue
            a = m["translation"][mode]["serve_args"]
            assert a[a.index("--cache-ram") + 1] == "0", (p.name, mode, a)
        assert "--cache-ram 0" in m["translation"]["comment"]
    # the kernel: keys, the store dir, the sources
    assert server.knowledge_addon_keys() == ["dictionaries"] + [f"wiktionary_{l}" for l in ("en", "de", "es", "fr", "it", "ja", "ru")]
    assert server.addon_section_key("dictionaries") == "dictionaries" and server.addon_section_key("wiktionary_ja") == "wiktionary_ja" \
        and server.addon_section_key("wiktionary_zz") is None and server.addon_section_key("translation") == "translation"
    assert server.entry_dest({"file": "x.zim", "store": "knowledge"}) == server.KNOWLEDGE_DIR / "x.zim" == server.VIBEOS_DIR / "knowledge" / "x.zim" \
        and server.entry_dest({"file": "dict/a.gz"}) == server.MODELS_DIR / "dict" / "a.gz"
    m8 = json.loads((REPO / "manifests" / "tier-8gb.json").read_text())
    srcs = dictionary.sources_from_section(m8["dictionaries"])
    assert len(srcs) == 42 and len({(s.frm, s.to) for s in srcs}) == 40, len(srcs)
    assert {s.id for s in srcs if s.reverse} == {"beolingus-en-de", "freedict-fra-ita-rev", "freedict-ita-jpn-rev", "freedict-jpn-rus-rev"}, [s.id for s in srcs if s.reverse]
    assert [s.id for s in srcs if (s.frm, s.to) == ("de", "en")] == ["beolingus-de-en", "freedict-deu-eng"], "BEOLINGUS first for de→en"
    assert not any((s.frm, s.to) in (("es", "ja"), ("ja", "es")) for s in srcs)
    assert server.DICT.pairs() == [f"{a}-{b}" for a, b in sorted({(s.frm, s.to) for s in srcs})]
    assert server.DICT.sources()[0].file == "dict/beolingus-de-en.txt.gz" and server.DICT.index_dir == server.CACHE_DIR / "dict"
    # the fold, the parsers, dictd's base64
    f = dictionary.fold
    assert f("Häuser") == "hauser" and f("HAUS") == "haus" == f(" haus ") and f("ёлка") == "елка" and f("がっこう") == "がっこう" \
        and f("apple-tree") == "apple tree" and f("Straße") == "strasse" and f("") == "", [f("Häuser"), f("ёлка"), f("がっこう")]
    assert dictionary.dictd_number("B") == 1 and dictionary.dictd_number("BA") == 64 and dictionary.dictd_number("A") == 0
    forms, pos, tr = dictionary.parse_dictd_entry("apple //ˈapəl// //ˈapɪl// /[ɛːpɐl]/ <n>\nmanzana, poma\nA tree of the genus Malus; especially Malus domestica which is cultivated for its edible fruit.\n")
    assert forms == ["apple"] and pos == "n" and tr == ["manzana, poma"], (forms, pos, tr)
    forms, pos, tr = dictionary.parse_dictd_entry(" [ichi1]  [news1]  [nf07]  猫 /(en)x(ja)y/,  [ichi1]  ねこ /nˈe̞ko̞/,  () ネコ /nˈe̞ko̞/\n1. (noun (common) (futsuumeishi))\ncat\n2. shamisen\n3. {猫車}\n         Note: abbreviationwheelbarrow\n4. geisha\n")
    assert forms == ["猫", "ねこ", "ネコ"] and tr == ["cat", "shamisen", "geisha"], (forms, tr)
    forms, pos, tr = dictionary.parse_dictd_entry("abfallend /ˈapfˌalənt/\nfalling off\n   Synonyms: {sinkend}, {stürzend}\n\n see: {abfallen}, {sinken}\n")
    assert forms == ["abfallend"] and tr == ["falling off"], (forms, tr)
    groups = list(dictionary.parse_ding("# c\nHaus {n} | Häuser {pl} :: house | houses\nKatze {f} :: cat; feline [formal]\n"))
    assert groups == [(["Haus {n}"], ["house"]), (["Häuser {pl}"], ["houses"]), (["Katze {f}"], ["cat", "feline [formal]"])], groups
    return ("7 manifests: dictionaries (BEOLINGUS + 37 FreeDict, sha256+sha512, licences) + 7 wiktionary_<lang> (store: knowledge), "
            f"none baked, --cache-ram 0 on fast+quality; 42 sources / 40 pairs; fold/parsers OK; queue cross-check {'done' if queue else 'skipped (no /images/pod)'}")


@run_test("http_dict_lookup_fixtures_and_addon", ["R71", "R47"])
async def t_dict_lookup(s):
    """[R71 P3] GET /api/dict over tiny real-format fixtures (BEOLINGUS de-en + FreeDict eng-spa): exact,
    folded (case/diacritics), prefix, the reversed BEOLINGUS direction (en→de), `to` from the language
    setting, `from` omitted (every dictionary into `to`; the empty placeholders of the other pairs are
    reported, not fatal), the 400s, the status, the add-on missing → a clean 400 naming the download, the
    download hook (38 files + indexing; a Wiktionary download restarts kiwix) and /api/models' extras."""
    sys.path.insert(0, str(REPO / "kernel"))
    import server
    d = _write_dict_fixtures(VDIR / "models")
    assert (d / "beolingus-de-en.txt.gz").stat().st_size > 100 and (d / "freedict-deu-eng-1.9-fd1.dictd.tar.xz").stat().st_size == 0, "fixtures over placeholders"

    async def dict_get(**q):
        async with s.get(f"{BASE}/api/dict", params={k: str(v) for k, v in q.items()}) as r:
            return r.status, await r.json()
    st, j = await dict_get(word="Haus", **{"from": "de"}, to="en")
    assert st == 200 and j["word"] == "Haus" and j["from"] == "de" and j["to"] == "en" and j["count"] >= 1, j
    e = j["entries"][0]
    assert e["match"] == "exact" and e["headword"] == "Haus" and e["display"] == "Haus {n}" and e["translations"] == ["house"] \
        and e["source"] == "BEOLINGUS de-en (TU Chemnitz)" and e["from"] == "de" and e["to"] == "en", e
    assert [x["id"] for x in j["sources"]] == ["beolingus-de-en"] and j["sources"][0]["entries"] == 7, j["sources"]
    assert j["errors"] and j["errors"][0]["id"] == "freedict-deu-eng" and "unreadable" in j["errors"][0]["error"], j["errors"]
    assert j["matches"] == {"exact": 1, "folded": 0, "prefix": 1} and j["entries"][1]["headword"] == "Häuser" \
        and j["entries"][1]["match"] == "prefix" and j["ms"] < 5000, j   # Häuser (fold "hauser") is a prefix hit of "haus"
    st, j = await dict_get(word="haus", **{"from": "de"}, to="en")
    assert st == 200 and j["entries"][0]["match"] == "folded" and j["entries"][0]["headword"] == "Haus", j
    st, j = await dict_get(word="HÄUSER", **{"from": "de"}, to="en")
    assert st == 200 and j["entries"][0]["match"] == "folded" and j["entries"][0]["translations"] == ["houses"], j
    st, j = await dict_get(word="Hau", **{"from": "de"}, to="en")
    assert st == 200 and j["count"] >= 2 and all(x["match"] == "prefix" for x in j["entries"]) \
        and {x["headword"] for x in j["entries"]} == {"Haus", "Häuser", "Hau ab!"}, j
    st, j = await dict_get(word="Hau", **{"from": "de"}, to="en", limit=1)
    assert st == 200 and j["count"] == 1 and j["limit"] == 1, j
    st, j = await dict_get(word="house", **{"from": "en"}, to="de")
    assert st == 200 and j["entries"][0]["match"] == "exact" and j["entries"][0]["translations"] == ["Haus {n}"] \
        and j["entries"][0]["from"] == "en" and j["sources"][0]["id"] == "beolingus-en-de", j
    st, j = await dict_get(word="feline", **{"from": "en"}, to="de")
    assert st == 200 and j["entries"][0]["display"] == "feline [formal]" and j["entries"][0]["translations"] == ["Katze {f} [zool.]"], j
    st, j = await dict_get(word="apple", **{"from": "en"}, to="es")
    assert st == 200 and j["entries"][0]["match"] == "exact" and j["entries"][0]["headword"] == "apple" and j["entries"][0]["pos"] == "n" \
        and j["entries"][0]["translations"] == ["manzana, poma"] and j["entries"][0]["source"] == "FreeDict eng-spa 2025.11.23" \
        and j["entries"][1]["match"] == "prefix" and j["entries"][1]["headword"] == "apple tree" and j["count"] == 2, j
    st, j = await dict_get(word="House", **{"from": "en"}, to="es")
    assert st == 200 and j["entries"][0]["match"] == "folded" and j["entries"][0]["translations"] == ["casa", "hogar"], j
    # `to` defaults to the language setting (translation stays off — a dictionary needs no model)
    async with s.post(f"{BASE}/api/settings", json={"key": "language", "value": "de"}) as r:
        assert r.status == 200
    try:
        st, j = await dict_get(word="house", **{"from": "en"})
        assert st == 200 and j["to"] == "de" and j["entries"][0]["translations"] == ["Haus {n}"], j
        async with s.get(f"{BASE}/api/translate/status") as r:
            assert (await r.json())["enabled"] is False
    finally:
        await _translation_reset(s)
    # `from` omitted: every dictionary into `to`; the placeholders of the other pairs are errors, not failures
    st, j = await dict_get(word="Haus", to="en")
    assert st == 200 and j["from"] is None and j["entries"][0]["from"] == "de" and j["entries"][0]["translations"] == ["house"] \
        and [x["id"] for x in j["sources"]] == ["beolingus-de-en"] and len(j["errors"]) == 6 \
        and {x["id"] for x in j["errors"]} == {"freedict-deu-eng", "freedict-spa-eng", "freedict-fra-eng", "freedict-ita-eng", "freedict-jpn-eng", "freedict-rus-eng"}, j
    # the 400s
    for q, needle in (({}, "word is required"), ({"word": "x" * 101}, "100 characters"), ({"word": "12:30"}, "letters"),
                      ({"word": "x", "to": "zh"}, "to must be one of"), ({"word": "x", "from": "xx", "to": "en"}, "from must be one of"),
                      ({"word": "x", "from": "en", "to": "en"}, "must differ"), ({"word": "x", "from": "de", "to": "en", "limit": "0"}, "1–50"),
                      ({"word": "x", "from": "de", "to": "en", "limit": "abc"}, "integer"), ({"word": "gato", "from": "es", "to": "ja"}, "no dictionary for es→ja")):
        async with s.get(f"{BASE}/api/dict", params=q) as r:
            j = await r.json()
            assert r.status == 400 and needle in j["error"] and "Traceback" not in j["error"], (q, r.status, j)
    async with s.get(f"{BASE}/api/dict", params={"word": "Katze", "from": "de", "to": "en"}) as r:   # a 502 only when EVERY dictionary is unreadable: not here
        assert r.status == 200 and (await r.json())["entries"][0]["translations"] == ["cat", "feline [formal]"]
    async with s.get(f"{BASE}/api/dict/status") as r:
        stt = await r.json()
    assert stt["present"] is True and stt["addon"] is True and stt["sources"] == 42 and stt["files"] == 38 and stt["indexed"] >= 3 \
        and len(stt["dictionaries"]) == 42 and stt["dictionaries"][0]["id"] == "beolingus-de-en" and stt["dictionaries"][0]["indexed"] is True \
        and stt["dictionaries"][0]["entries"] == 7 and stt["lookups"] >= 10 and "freedict-deu-eng" in stt["broken"], stt
    # the add-on mechanism: already present → no download; the download hook indexes; a Wiktionary download restarts kiwix
    async with s.post(f"{BASE}/api/models/download", json={"key": "dictionaries"}) as r:
        j = await r.json(); assert r.status == 200 and j.get("already") is True, j
    calls, restarts, real_dl, real_rs = [], [], server.download_entries, server.KB.restart_kiwix
    async def rec(entries, state):
        calls.append([e["file"] for e in entries])
    async def rec_restart():
        restarts.append(1)
    server.download_entries, server.KB.restart_kiwix = rec, rec_restart
    try:
        await server.run_model_download("dictionaries", "dictionaries")
        await server.run_model_download("wiktionary_de", "wiktionary_de")
    finally:
        server.download_entries, server.KB.restart_kiwix = real_dl, real_rs
    assert len(calls) == 2 and len(calls[0]) == 38 and calls[0][0] == "dict/beolingus-de-en.txt.gz" and calls[1] == ["wiktionary_de_all_nopic_2026-07.zim"] \
        and restarts == [1] and server.MODEL_DL["dictionaries"]["status"] == "done" and server.MODEL_DL["wiktionary_de"]["status"] == "done", (calls, restarts)
    assert server.DICT.indexed(next(x for x in server.DICT.sources() if x.id == "freedict-eng-spa")) is not None, "the download hook indexes the present files"
    async with s.get(f"{BASE}/api/models") as r:
        mi = await r.json()
    dx = next(e for e in mi["extras"] if e["key"] == "dictionaries")
    assert dx["kind"] == "dictionary" and dx["addon"] is True and dx["present"] is True and dx["available"] is True and len(dx["pairs"]) == 40 \
        and 0.11 <= dx["download_gb"] <= 0.13 and "vibe.dict" in dx["api"], dx
    wx = next(e for e in mi["extras"] if e["key"] == "wiktionary_de")
    assert wx["kind"] == "knowledge" and wx["language"] == "de" and wx["store"] == "knowledge" and wx["present"] is False and wx["serving"] is False \
        and 1.2 <= wx["download_gb"] <= 1.4 and wx["addon"] is True, wx
    assert [e["key"] for e in mi["extras"] if e["key"].startswith("wiktionary_")] == [f"wiktionary_{l}" for l in ("en", "de", "es", "fr", "it", "ja", "ru")]
    # the add-on missing → 400 naming the download, nothing crashes; back again afterwards
    off = d.with_name("dict.off")
    d.rename(off)
    try:
        st, j = await dict_get(word="Haus", **{"from": "de"}, to="en")
        assert st == 400 and "dictionaries add-on" in j["error"] and "vibe.models.download('dictionaries')" in j["error"], (st, j)
        async with s.get(f"{BASE}/api/models") as r:
            dx = next(e for e in (await r.json())["extras"] if e["key"] == "dictionaries")
        assert dx["present"] is False and dx["available"] is False, dx
    finally:
        off.rename(d)
    st, j = await dict_get(word="Haus", **{"from": "de"}, to="en")
    assert st == 200 and j["entries"][0]["translations"] == ["house"]
    return ("BEOLINGUS + FreeDict fixtures: exact/folded/prefix, reversed en→de, to=language, from omitted (6 placeholder errors reported), "
            "9 bad requests 400, status, download hook (38 files + index; wiktionary → kiwix restart), extras, add-on missing → 400")


@run_test("http_translation_dom_batches_fast_and_cache_ram", ["R71", "R72", "R89"])
async def t_translation_dom_fast(s):
    """[R71 P3 fix 3+4] quality selected, both translators present: a UI batch (`dom: true`) goes to the fast CPU
    server (port 8085, launched with --cache-ram 0) and the kernel logs it — unchanged by R89; an explicit call
    without `dom` takes quality, which on the 8 GB tier is [R89] the loaded LLM: the request reaches the LLM's port
    with no system message, no translation server is launched for it, and nothing needs unloading when the choice
    goes back to fast (the fast server stays); off stops it."""
    sys.path.insert(0, str(REPO / "kernel"))
    import server
    arglog, msglog, klog = VDIR / "llama-args.log", VDIR / "llama-msgs.log", VDIR / "kernel.log"
    tr_lines = lambda: [ln for ln in (arglog.read_text().splitlines() if arglog.exists() else []) if "Index-Translate" in ln]
    msgs = lambda: [json.loads(ln) for ln in (msglog.read_text().splitlines() if msglog.exists() else [])]
    await _translation_reset(s)
    async with s.post(f"{BASE}/api/settings", json={"translation": "on", "language": "de", "translation_quality": "quality"}) as r:
        assert r.status == 200
    await asyncio.sleep(0.3)
    async with s.get(f"{BASE}/api/translate/status") as r:
        st = await r.json()
    assert st["quality"] == "quality" and st["effective"] == "quality" and st["fallback"] is None and st["present"] == {"fast": True, "quality": True} \
        and st["quality_llm"] is True and "(the loaded LLM)" in st["model"]["quality"], st
    n0, m0 = len(tr_lines()), len(msgs())
    stamp = f"{time.time():.3f}"                      # fresh texts: a cached answer would never reach a model
    texts = [f"Open this window {stamp}", f"Save the file now {stamp}", f"Close {stamp}"]
    async with s.post(f"{BASE}/api/translate", json={"texts": texts, "dom": True}) as r:
        j = await r.json()
    assert r.status == 200 and j["model"] == "fast" and j["via"] == "mt" and j["texts"] == [f"[de] {t}" for t in texts] and j["calls"] == 3, j
    new = msgs()[m0:]
    assert len(new) == 3 and all(m["port"] == 8085 for m in new), "a UI batch must go to the fast CPU server even with quality selected"
    launch = tr_lines()
    assert len(launch) == n0 + 1 and "Index-Translate-2B" in launch[-1] and "--cache-ram 0" in launch[-1] and "-ngl 0" in launch[-1] \
        and "-dev none" in launch[-1], launch[-1:]
    assert "translation: UI batch of 3 → fast (quality selected; vibe.translate() keeps quality)" in klog.read_text()
    m1, n1 = len(msgs()), len(tr_lines())
    async with s.post(f"{BASE}/api/translate", json={"text": f"An explicit vibe.translate call wants quality {stamp}"}) as r:
        j = await r.json()
    assert r.status == 200 and j["model"] == "quality" and j["via"] == "llm" and j["calls"] == 1 \
        and j["text"] == f"[de] An explicit vibe.translate call wants quality {stamp}", j
    new = msgs()[m1:]
    assert len(new) == 1 and new[0]["port"] == 8081 and new[0]["system"] is None, new
    assert len(tr_lines()) == n1, "[R89] no translation server for quality"
    async with s.get(f"{BASE}/api/status") as r:
        full = await r.json()
    assert full["loaded"] == "llm" and full["translation"]["running"] == {"fast": True, "quality": False}, (full["loaded"], full["translation"]["running"])
    async with s.post(f"{BASE}/api/settings", json={"key": "translation_quality", "value": "fast"}) as r:
        assert r.status == 200
    await asyncio.sleep(0.3)
    async with s.get(f"{BASE}/api/translate/status") as r:
        st = await r.json()
    assert st["running"] == {"fast": True, "quality": False} and st["effective"] == "fast", st
    async with s.get(f"{BASE}/api/status") as r:
        assert (await r.json())["loaded"] == "llm", "the LLM stays: it is the coder, not a translation model"
    await _translation_reset(s)
    async with s.get(f"{BASE}/api/translate/status") as r:
        assert (await r.json())["running"] == {"fast": False, "quality": False}
    return ("quality selected: dom batch → fast (:8085, --cache-ram 0, logged); explicit call → quality = the loaded LLM "
            "(:8081, no system msg, no translation launch) [R89]; → fast keeps the fast server and the LLM; off stops it")


@run_test("http_r89_quality_translation_via_llm", ["R89", "R71", "R72"])
async def t_r89_quality_via_llm(s):
    """[R89] translation_quality=quality on the 8 GB tier: every direction runs on the LOADED LLM (en→de, en→ja, de→en
    here) with exactly the benchmark's body — one user turn = TRANSLATE_PROMPT, no system message, temperature 0,
    thinking off, max_tokens min(2048, 64 + 3·len), and NOT the LLM's vendor sampling (no top_k / top_p, temp 0 not
    1.0) — no translation llama-server is launched for it and /api/status `loaded` stays llm. The fast path is
    unchanged: a UI batch (`dom`) and the kernel's English hop (a search query) still go to the 2B on :8085 with its
    own body; with quality=fast an explicit call does too."""
    sys.path.insert(0, str(REPO / "kernel"))
    import server
    arglog, msglog = VDIR / "llama-args.log", VDIR / "llama-msgs.log"
    launches = lambda: [ln for ln in (arglog.read_text().splitlines() if arglog.exists() else []) if ln.strip()]
    msgs = lambda: [json.loads(ln) for ln in (msglog.read_text().splitlines() if msglog.exists() else [])]
    await _translation_reset(s)
    async with s.post(f"{BASE}/api/settings", json={"translation": "on", "language": "de", "translation_quality": "quality"}) as r:
        assert r.status == 200
    stamp = f"{time.time():.3f}"
    n0, m0 = len(launches()), len(msgs())
    cases = [("en", "de", f"The window opens at noon {stamp}", "German"),
             ("en", "ja", f"Save the picture before you close the app {stamp}", "Japanese"),
             ("de", "en", f"Der Zug fährt um acht Uhr ab {stamp}", "English")]
    for frm, to, text, name in cases:
        async with s.post(f"{BASE}/api/translate", json={"text": text, "to": to, "from": frm}) as r:
            j = await r.json()
        assert r.status == 200 and j["model"] == "quality" and j["via"] == "llm" and j["calls"] == 1 and j["text"] == f"[{to}] {text}", j
    new = msgs()[m0:]
    assert len(new) == 3 and all(m["port"] == 8081 for m in new), [m["port"] for m in new]
    for m, (frm, to, text, name) in zip(new, cases):
        assert m["system"] is None and m["messages"] == 1 and m["user"] == server.TRANSLATE_PROMPT.format(language=name, text=text) \
            and m["temperature"] == 0 and m["chat_template_kwargs"] == {"enable_thinking": False} \
            and m["max_tokens"] == min(2048, 64 + 3 * len(text)) \
            and m["keys"] == ["chat_template_kwargs", "max_tokens", "messages", "stream", "temperature"], m
    assert not any("Index-Translate" in ln for ln in launches()[n0:]), launches()[n0:]
    async with s.get(f"{BASE}/api/status") as r:
        st = await r.json()
    assert st["loaded"] == "llm" and st["gpu"]["kind"] == "llm" and st["translation"]["running"]["quality"] is False \
        and st["translation"]["quality_llm"] is True, (st["loaded"], st["gpu"], st["translation"]["running"])
    # the in-process body builder agrees (the LLM's section: thinking off kept, vendor sampling left out)
    spec = server.MM.llm_spec("llm")[1]
    body = server.MM.chat_payload("llm", None, [{"role": "user", "content": "x"}], 67, 0, False, {"temperature": 0}, spec=spec,
                                  vendor_sampling=False)
    assert body == {"messages": [{"role": "user", "content": "x"}], "max_tokens": 67, "temperature": 0, "stream": False,
                    "chat_template_kwargs": {"enable_thinking": False}}, body
    assert server.MM.chat_payload("llm", None, [{"role": "user", "content": "x"}], 67, 0, False, spec=spec)["top_k"] == 64, \
        "every other request keeps the vendor sampling"
    # the fast path is unchanged
    m1 = len(msgs())
    async with s.post(f"{BASE}/api/translate", json={"texts": [f"Open {stamp}", f"Close {stamp}"], "dom": True}) as r:
        j = await r.json()
    assert r.status == 200 and j["model"] == "fast" and j["via"] == "mt", j
    async with s.post(f"{BASE}/api/search", json={"query": f"Eiffelturm Geschichte {stamp}"}) as r:
        assert r.status == 200
    new = msgs()[m1:]
    assert len(new) >= 3 and all(m["port"] == 8085 and m["system"] is None and m["temperature"] == 0 for m in new) \
        and any(m["user"].startswith("Translate the following text into English.") for m in new), new   # (+ result snippets with a ZIM)
    async with s.post(f"{BASE}/api/settings", json={"key": "translation_quality", "value": "fast"}) as r:
        assert r.status == 200
    m2 = len(msgs())
    async with s.post(f"{BASE}/api/translate", json={"text": f"Good night {stamp}"}) as r:
        j = await r.json()
    assert r.status == 200 and j["model"] == "fast" and j["via"] == "mt" and [m["port"] for m in msgs()[m2:]] == [8085], j
    await _translation_reset(s)
    return ("quality = the loaded LLM: en→de / en→ja / de→en on :8081 with the benchmark's body (no system, temp 0, thinking "
            "off, the kernel's max_tokens, no vendor sampling), no translation launch, loaded = llm; dom batch, English hop "
            "and quality=fast stay on the 2B (:8085)")


@run_test("http_translation_quality_without_gpu_goes_fast", ["R89", "R71", "R72"])
async def t_translation_no_gpu(s):
    """[fix 2026-10-22] translation_quality=quality without a GPU: the fast CPU translator answers, and the answer says
    so ("model": "fast", "via": "mt", "reason": "no GPU"); /api/translate/status shows effective fast, the fallback
    sentence and fallback_reason "no GPU". Both ways the GPU can be missing are driven on the suite's kernel through
    its fake driver dir (VIBEOS_NVIDIA_PROC, tests/run-tests.sh): the device gone from gpus/ (the 4070 off the bus, the
    driver still loaded) and the whole driver gone (engine "cpu"). With the device back nothing changes: quality is
    the loaded LLM again (:8081, no reason). Before: the heretic Gemma on the CPU, 134 s per sentence."""
    import shutil
    nv = Path(os.environ.get("VIBEOS_NVIDIA_PROC", ""))
    dev = nv / "gpus" / "0000:01:00.0"
    if not os.environ.get("VIBEOS_NVIDIA_PROC") or not dev.is_dir():
        raise SkipTest("the suite's fake GPU (VIBEOS_NVIDIA_PROC with gpus/<bus id>, tests/run-tests.sh) is not set up")
    msglog, klog = VDIR / "llama-msgs.log", VDIR / "kernel.log"
    msgs = lambda: [json.loads(ln) for ln in (msglog.read_text().splitlines() if msglog.exists() else [])]
    parked, parked_drv = nv.parent / "nvidia-gpu-parked", nv.parent / "nvidia-driver-parked"
    await _translation_reset(s)
    async with s.post(f"{BASE}/api/settings", json={"translation": "on", "language": "de", "translation_quality": "quality"}) as r:
        assert r.status == 200
    stamp = f"{time.time():.3f}"
    out = []
    try:
        # with the GPU: unchanged (R89)
        async with s.get(f"{BASE}/api/translate/status") as r:
            st = await r.json()
        assert st["effective"] == "quality" and st["fallback"] is None and st["fallback_reason"] is None, st
        m0 = len(msgs())
        async with s.post(f"{BASE}/api/translate", json={"text": f"With the GPU {stamp}"}) as r:
            j = await r.json()
        assert r.status == 200 and j["model"] == "quality" and j["via"] == "llm" and "reason" not in j, j
        assert [m["port"] for m in msgs()[m0:]] == [8081], msgs()[m0:]
        for case in ("device off the bus", "no driver"):
            if case == "device off the bus":
                shutil.move(str(dev), str(parked))                     # the driver stays, its GPU list is empty
            else:
                shutil.move(str(nv), str(parked_drv))                  # /proc/driver/nvidia gone: engine "cpu"
            async with s.get(f"{BASE}/api/status") as r:
                full = await r.json()
            st = full["translation"]
            assert st["quality"] == "quality" and st["effective"] == "fast" and st["fallback_reason"] == "no GPU" \
                and st["fallback"].startswith("no GPU — the quality translator (the loaded LLM) would run on the CPU"), st
            assert full["engine"] == ("cuda" if case == "device off the bus" else "cpu"), full["engine"]
            m0, k0 = len(msgs()), len(klog.read_text())
            t0 = time.monotonic()
            async with s.post(f"{BASE}/api/translate", json={"text": f"Good morning, {case} {stamp}"}) as r:
                j = await r.json()
            took = time.monotonic() - t0
            assert r.status == 200 and j["model"] == "fast" and j["via"] == "mt" and j["reason"] == "no GPU" \
                and j["text"] == f"[de] Good morning, {case} {stamp}" and j["calls"] == 1, j
            new = msgs()[m0:]
            assert [m["port"] for m in new] == [8085] and new[0]["system"] is None, new        # the 2B on the CPU only
            assert "translation: quality → fast (no GPU: the quality translator would run on the CPU) — 1 text(s)" \
                in klog.read_text()[k0:], klog.read_text()[k0:][-600:]
            # a UI batch: fast as always, no reason (nothing was redirected)
            async with s.post(f"{BASE}/api/translate", json={"texts": [f"Open {case} {stamp}"], "dom": True}) as r:
                jd = await r.json()
            assert r.status == 200 and jd["model"] == "fast" and "reason" not in jd, jd
            out.append(f"{case}: fast in {took:.2f} s")
            if case == "device off the bus":
                shutil.move(str(parked), str(dev))
            else:
                shutil.move(str(parked_drv), str(nv))
        # the GPU back: quality = the loaded LLM again, nothing else changed
        async with s.get(f"{BASE}/api/translate/status") as r:
            st = await r.json()
        assert st["effective"] == "quality" and st["fallback_reason"] is None, st
        m0 = len(msgs())
        async with s.post(f"{BASE}/api/translate", json={"text": f"The GPU is back {stamp}"}) as r:
            j = await r.json()
        assert r.status == 200 and j["model"] == "quality" and j["via"] == "llm" and "reason" not in j, j
        assert [m["port"] for m in msgs()[m0:]] == [8081], msgs()[m0:]
    finally:
        if parked_drv.exists() and not nv.exists():
            shutil.move(str(parked_drv), str(nv))
        if parked.exists() and not dev.exists():
            shutil.move(str(parked), str(dev))
        await _translation_reset(s)
    return "with GPU: quality on the LLM (:8081); " + "; ".join(out) + " (:8085, reason 'no GPU', logged); GPU back: the LLM again"


@run_test("unit_translation_quality_gpu_rules", ["R89", "R71", "R30"])
async def t_translation_gpu_rules(_):
    """[fix 2026-10-22] quality_translation_gpu_missing() / translation_choice() in-process: "no GPU" without a CUDA
    device (driver loaded, gpus/ empty), without a driver (engine cpu), with VIBEOS_ENGINE=cuda forced on a machine
    without one, and while the LLM's llama-server was started on the CPU and still runs (it stays there until it
    restarts) — None with a device, on the Vulkan set (the iGPU), and for a CPU-started server of another key (an
    expander). Without the fast file quality stays quality (the slow CPU answer beats none); the 16 GB tier's 9B
    (quality = a translation model) follows the same rule under its own key."""
    import tempfile
    sys.path.insert(0, str(REPO / "kernel"))
    import server
    MM = server.MM

    class Running:                                    # a llama_proc that is alive
        returncode = None
    saved = (server.BIN, server.NVIDIA_PROC, os.environ.pop("VIBEOS_ENGINE", None),
             MM.llama_proc, MM.llama_model_key, MM.llama_on_gpu, MM.manifest["translation"]["quality"])
    q = {"translation": "on", "language": "de", "translation_quality": "quality"}
    try:
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            server.BIN, server.NVIDIA_PROC = td, td / "nvidia"
            (td / "nvidia" / "gpus" / "0000:01:00.0").mkdir(parents=True)
            MM.llama_proc = MM.llama_model_key = MM.llama_on_gpu = None
            assert server.cuda_device_present() and server.llm_gpu_now() and server.quality_translation_gpu_missing() is None
            assert server.translation_choice(q) == {"setting": "quality", "effective": "quality", "fallback": None, "reason": None}
            (td / "nvidia" / "gpus" / "0000:01:00.0").rmdir()          # driver loaded, no device
            assert server.engine_backend() == "cuda" and not server.cuda_device_present() and not server.llm_gpu_now()
            c = server.translation_choice(q)
            assert c["effective"] == "fast" and c["reason"] == "no GPU" and c["setting"] == "quality" and "no GPU" in c["fallback"], c
            (td / "nvidia" / "gpus").rmdir(); (td / "nvidia").rmdir()   # no driver at all
            assert server.engine_backend() == "cpu" and server.quality_translation_gpu_missing() == "no GPU"
            os.environ["VIBEOS_ENGINE"] = "cuda"                       # forced CUDA on a machine without a device
            assert server.engine_backend() == "cuda" and server.quality_translation_gpu_missing() == "no GPU"
            os.environ.pop("VIBEOS_ENGINE")
            (td / "vulkan").mkdir(); (td / "vulkan" / "llama-server").touch()   # the iGPU set: a GPU
            assert server.engine_backend() == "vulkan" and server.quality_translation_gpu_missing() is None
            (td / "vulkan" / "llama-server").unlink(); (td / "vulkan").rmdir()
            # a GPU now, but the LLM's server was started on the CPU and still runs
            (td / "nvidia" / "gpus" / "0000:01:00.0").mkdir(parents=True)
            section = server.quality_llm_section()[0]
            MM.llama_proc, MM.llama_model_key, MM.llama_on_gpu = Running(), section, False
            assert server.quality_translation_gpu_missing() == "no GPU" and server.translation_choice(q)["effective"] == "fast"
            MM.llama_on_gpu = True
            assert server.quality_translation_gpu_missing() is None
            MM.llama_model_key, MM.llama_on_gpu = "expander:image/x.gguf", False
            assert server.quality_translation_gpu_missing() is None, "another key's CPU server says nothing about the LLM"
            MM.llama_proc = MM.llama_model_key = MM.llama_on_gpu = None
            # no fast file: quality stays (slow beats none)
            fast = server.MODELS_DIR / server.translation_llm_spec("fast")["file"]
            (td / "nvidia" / "gpus" / "0000:01:00.0").rmdir()
            parked = fast.with_name(fast.name + ".parked")
            fast.rename(parked)
            try:
                c = server.translation_choice(q)
                assert c["effective"] == "quality" and c["reason"] is None, c
            finally:
                parked.rename(fast)
            # the 16 GB tier's 9B: quality = a translation model, the same rule, its own key
            q16 = json.loads((REPO / "manifests" / "tier-16gb.json").read_text())["translation"]["quality"]
            MM.manifest["translation"]["quality"] = q16
            nine = server.MODELS_DIR / q16["file"]
            made = not nine.exists()
            nine.parent.mkdir(parents=True, exist_ok=True); nine.touch()
            try:
                assert not server.translation_quality_llm()
                c = server.translation_choice(q)
                assert c["effective"] == "fast" and c["reason"] == "no GPU" and "the quality translation model" in c["fallback"], c
                (td / "nvidia" / "gpus" / "0000:01:00.0").mkdir(parents=True)
                MM.llama_proc, MM.llama_model_key, MM.llama_on_gpu = Running(), "translation", False
                assert server.quality_translation_gpu_missing() == "no GPU"
                MM.llama_on_gpu = True
                assert server.translation_choice(q)["effective"] == "quality"
            finally:
                if made:
                    nine.unlink()
    finally:
        server.BIN, server.NVIDIA_PROC = saved[0], saved[1]
        os.environ.pop("VIBEOS_ENGINE", None)
        if saved[2] is not None:
            os.environ["VIBEOS_ENGINE"] = saved[2]
        MM.llama_proc, MM.llama_model_key, MM.llama_on_gpu = saved[3], saved[4], saved[5]
        MM.manifest["translation"]["quality"] = saved[6]
    return ("no device / no driver / forced cuda without one / the LLM started on the CPU → 'no GPU' (fast); device, "
            "Vulkan, another key's CPU server → none; no fast file → quality stays; 16 GB's 9B: same rule, key 'translation'")


@run_test("unit_names_hosts_resolv_overlay", ["R28", "R51", "R78"])
async def t_names_overlay(_):
    """[fix 2026-10-22] X must not wait on DNS for the hostname: the overlay's /etc/hosts names localhost (v4 + v6) and
    the image's hostname localhost.localdomain on 127.0.1.1 (the Debian line scripts/public-image-prep.sh renames);
    /etc/resolv.conf is NetworkManager's /run file — a tmpfiles.d L+ rule (proven with the real systemd-tmpfiles on a
    scratch root: a baked regular file becomes the link) and NetworkManager rc-manager=symlink + hostname-mode=none;
    iso/build-img.sh installs the three files, re-points the hosts line at the image's hostname and links
    resolv.conf (live-build rewrites both files); sync-guest.sh carries the overlay and runs the tmpfiles rules; the
    startx path in .bash_profile/.xinitrc names no host (only :0 and localhost)."""
    import configparser, shutil, subprocess, tempfile
    inc = REPO / "iso" / "config" / "includes.chroot"
    hosts = (inc / "etc" / "hosts").read_text()
    lines = [ln.split() for ln in hosts.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    names = {}
    for ln in lines:
        for n in ln[1:]:
            names.setdefault(n, []).append(ln[0])
    assert names["localhost"] == ["127.0.0.1", "::1"] and names["localhost.localdomain"] == ["127.0.1.1"], names
    assert [ln for ln in lines if ln[0] == "127.0.1.1"] == [["127.0.1.1", "localhost.localdomain"]], lines
    assert {"ip6-localhost", "ip6-loopback", "ip6-allnodes", "ip6-allrouters"} <= set(names), names
    # the public prep's rename regex finds the line (the same pattern as scripts/public-image-prep.sh)
    prep = (REPO / "scripts" / "public-image-prep.sh").read_text()
    assert r"s/^127\.0\.1\.1[[:space:]].*$/127.0.1.1\tkairos/" in prep and "/run/NetworkManager/resolv.conf" in prep
    assert re.sub(r"^127\.0\.1\.1[ \t].*$", "127.0.1.1\tkairos", hosts, flags=re.M).count("127.0.1.1\tkairos") == 1
    # NetworkManager: owns resolv.conf through its /run file, never sets the hostname
    cp = configparser.ConfigParser()
    cp.read_string((inc / "etc" / "NetworkManager" / "conf.d" / "20-kairos-dns.conf").read_text())
    assert dict(cp["main"]) == {"rc-manager": "symlink", "hostname-mode": "none"}, dict(cp["main"])
    tmpf = (inc / "etc" / "tmpfiles.d" / "kairos.conf").read_text()
    rule = [ln for ln in tmpf.splitlines() if "resolv.conf" in ln and not ln.startswith("#")]
    assert rule == ["L+ /etc/resolv.conf - - - - /run/NetworkManager/resolv.conf"], rule
    assert not (inc / "etc" / "resolv.conf").exists() and not (inc / "etc" / "resolv.conf").is_symlink(), \
        "no resolv.conf in the overlay (a tracked link would be read through by make-bundle.sh)"
    tool = shutil.which("systemd-tmpfiles")
    proven = "systemd-tmpfiles absent: rule text only"
    if tool:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "etc").mkdir()
            (root / "etc" / "resolv.conf").write_text("# Generated by NetworkManager\nnameserver 10.0.2.3\n")   # v7.5-3's
            conf = root / "rule.conf"
            conf.write_text(rule[0] + "\n")
            r = subprocess.run([tool, f"--root={root}", "--create", str(conf)], capture_output=True, text=True, timeout=30)
            assert r.returncode == 0 and (root / "etc" / "resolv.conf").is_symlink() \
                and os.readlink(root / "etc" / "resolv.conf") == "/run/NetworkManager/resolv.conf", (r.returncode, r.stderr)
            r = subprocess.run([tool, f"--root={root}", "--create", str(conf)], capture_output=True, text=True, timeout=30)
            assert r.returncode == 0 and os.readlink(root / "etc" / "resolv.conf") == "/run/NetworkManager/resolv.conf", "idempotent"
        proven = "systemd-tmpfiles --root turned a baked resolv.conf into the link (twice: idempotent)"
    # the image build sets both after the chroot step (live-build's chroot_hosts / chroot_resolv rewrite them)
    bi = (REPO / "iso" / "build-img.sh").read_text()
    names_step = bi[bi.index("# --- names [fix 2026-10-22"):bi.index("# --- install systemd-boot")]
    assert bi.index("# --- names [fix 2026-10-22") > bi.index("\nCHROOT\n"), "after the chroot block"
    for x in ('install -D -m 644 "$INC/etc/hosts" "$MNT/etc/hosts"',
              'install -D -m 644 "$INC/etc/NetworkManager/conf.d/20-kairos-dns.conf"',
              'install -D -m 644 "$INC/etc/tmpfiles.d/kairos.conf"', 'rm -f "$MNT/etc/resolv.conf"',
              'ln -s /run/NetworkManager/resolv.conf "$MNT/etc/resolv.conf"', r"127.0.1.1\t$HN", "exit 99"):
        assert x in names_step, x
    # the step's hostname rewrite, run on a scratch root with another hostname
    with tempfile.TemporaryDirectory() as td:
        mnt = Path(td)
        (mnt / "etc").mkdir()
        (mnt / "etc" / "hostname").write_text("kairos-box\n")
        r = subprocess.run(["bash", "-euo", "pipefail", "-c", names_step], capture_output=True, text=True, timeout=30,
                           env=dict(os.environ, INC=str(inc), MNT=str(mnt)))
        assert r.returncode == 0 and "names: /etc/hosts names kairos-box on 127.0.1.1" in r.stdout, (r.returncode, r.stdout, r.stderr)
        h = (mnt / "etc" / "hosts").read_text()
        assert re.findall(r"^127\.0\.1\.1[ \t]+(.*)$", h, re.M) == ["kairos-box"] \
            and os.readlink(mnt / "etc" / "resolv.conf") == "/run/NetworkManager/resolv.conf", h
    # the rig's sync carries the whole overlay (so /etc/hosts + the NM conf) and runs the tmpfiles rules
    sg = (REPO / "dev" / "rig" / "testrig" / "sync-guest.sh").read_text()
    assert 'tar -C "$REPO/iso/config/includes.chroot" -czf - etc usr home' in sg and "systemd-tmpfiles --create /etc/tmpfiles.d/kairos.conf" in sg
    # the startx path: startx itself takes `uname -n` (no lookup) and xauth looks up "<hostname>:0" — now in /etc/hosts;
    # .bash_profile / .xinitrc name no other host than :0 and localhost
    for f in (inc / "home" / "kairos" / ".bash_profile", inc / "home" / "kairos" / ".xinitrc"):
        hostsy = set(re.findall(r"https?://([A-Za-z0-9.-]+)", f.read_text()))
        assert hostsy <= {"localhost"}, (f.name, hostsy)
    assert "startx -- :0 " in (inc / "home" / "kairos" / ".bash_profile").read_text()
    return f"/etc/hosts: localhost v4+v6, localhost.localdomain on 127.0.1.1; resolv.conf: {proven}; NM symlink + no hostname; build-img step ran on a scratch root"


@run_test("unit_r89_docs_and_sheets_static", ["R89", "R39", "R59"])
async def t_r89_docs(_):
    """[R89] the documents say what the kernel does: the requirement, the benchmark's lasting record (no personal paths,
    the decisions), the index line, the translation-layer addendum, API.md; the Settings sheet + recipe the coder LLM
    gets (and the dock's Settings prompt) offer no fast model and label quality with `model.quality` — both still
    injected whole within their caps; the Models panel text and the licence index follow."""
    sys.path.insert(0, str(REPO / "kernel"))
    import server
    d = REPO / "docs"
    assert "- **R89 — " in (d / "REQUIREMENTS.md").read_text(encoding="utf-8")
    fm = (d / "FAST-MODELS-2026-10-17.md").read_text(encoding="utf-8")
    assert "0.8684" in fm and "## 9. Decisions" in fm and "Drop fast mode" in fm and "Translation via Gemma" in fm
    assert not re.search(r"/home/|~/|Bilder", fm), "personal paths in the benchmark record"
    assert "FAST-MODELS-2026-10-17.md" in (d / "README.md").read_text(encoding="utf-8")
    assert "## R89 addendum" in (d / "TRANSLATION-LAYER-2026-10-06.md").read_text(encoding="utf-8")
    api = (d / "API.md").read_text(encoding="utf-8")
    assert "There is no separate fast model" in api and "the loaded LLM" in api and "`via`" in api
    sheet, recipe = server.API_DOCS["system-settings"]["text"], server.WIKI["settings-app"]["text"]
    for doc, name in ((sheet, "sheet"), (recipe, "recipe")):
        assert "model.quality" in doc and "Qwen2.5-Coder" not in doc and 'vibe.models.download("fast")' not in doc, name
        assert "no separate fast model" in doc.lower() or "no fast model" in doc.lower(), name
    assert len(sheet) <= server.API_SHEET_CAP and len(recipe) <= server.WIKI_FIRST_CAP, (len(sheet), len(recipe))
    index = (REPO / "shell" / "index.html").read_text(encoding="utf-8")
    dock = re.search(r'data-app="settings" data-prompt="([^"]+)"', index).group(1)
    assert "fast = the small quick one" not in dock and "no fast mode" in dock and "m.translation.model.quality" in dock, dock[-600:]
    assert "the fast coder" not in (REPO / "shell" / "models.js").read_text(encoding="utf-8")
    lic = json.loads((REPO / "iso" / "config" / "includes.chroot" / "usr" / "share" / "doc" / "kairos" / "model-licences"
                      / "index.json").read_text(encoding="utf-8"))
    by = {m["id"]: m for m in lic["models"]}
    assert "translation/Index-Translate-9B.Q8_0.gguf" in by["index-translate"]["files"] and "former" in by["qwen2.5-coder"]["name"]
    return f"R89 entry, FAST-MODELS record (no personal paths), index line, addendum, API.md; sheet {len(sheet)} / recipe {len(recipe)} chars without a fast model"


@run_test("unit_knowledge_multi_zim_sources", ["R32", "R71"])
async def t_knowledge_multi_zim(_):
    """[R71 P3] the knowledge organ over several ZIMs: Wikipedia stays books[0], every book has a
    kind/lang/label, one search per language group (the OS language's Wiktionary with the words as
    typed, first), hits labelled by source, round-robin merge, grounding facts that name their book,
    restart/stop; kiwix pages and search links parsed; the kernel's native query helper and
    localize_results leaving native-language hits alone. Hermetic (a fake kiwix-serve, canned searches)."""
    sys.path.insert(0, str(REPO / "kernel"))
    import importlib, tempfile
    knowledge = importlib.import_module("knowledge")
    import server
    bi = knowledge.book_info
    assert bi("wiktionary_de_all_nopic_2026-07") == {"name": "wiktionary_de_all_nopic_2026-07", "kind": "wiktionary", "lang": "de", "label": "Wiktionary (de)"}
    assert bi("wikipedia_en_all_mini_2026-06")["label"] == "Wikipedia (en)" and bi("custom")["label"] == "custom" and bi("custom")["lang"] == ""
    page = ('<a href="/content/wiktionary_de_all_nopic_2026-07/Haus-Haus-Verkehr"> Haus-Haus-Verkehr </a>'
            '<a href="/search?pattern=Haus&books.name=x&start=3"> 2 </a><a href="/viewer#wikipedia_en_all_mini_2026-06/A/Apple"><b>Apple</b></a>'
            '<a href="/content/wiktionary_de_all_nopic_2026-07/Haus-Haus-Verkehr">again</a>')
    assert knowledge.Knowledge.parse_search_links(page) == [("wiktionary_de_all_nopic_2026-07/Haus-Haus-Verkehr", "Haus-Haus-Verkehr"),
                                                            ("wikipedia_en_all_mini_2026-06/A/Apple", "Apple")]
    html = "<html><body><p>Worttrennung:</p><p>Bedeutungen:</p><dl><dd>[1] Frucht des Apfelbaums, rund und saftig</dd></dl><p>x</p><script>var a=1;</script></body></html>"
    assert knowledge.Knowledge.page_text(html) == "[1] Frucht des Apfelbaums, rund und saftig"
    assert knowledge.Knowledge.page_text("<p>" + "An encyclopedia paragraph of some length. " * 12 + "</p><li>nav junk item</li>").endswith("length.")
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); (t / "bin").mkdir(); (t / "knowledge").mkdir()
        zims = ["wiktionary_de_all_nopic_2026-07.zim", "wikipedia_en_all_mini_2026-06.zim", "wiktionary_en_all_nopic_2026-08.zim"]
        for z in zims:
            (t / "knowledge" / z).write_bytes(b"ZIM")
        argv = t / "argv"
        (t / "bin" / "kiwix-serve").write_text(f'#!/bin/sh\necho "$@" >> "{argv}"\nexec sleep 30\n')
        (t / "bin" / "kiwix-serve").chmod(0o755)
        kb = knowledge.Knowledge(t)
        await kb.start_kiwix()
        try:
            assert kb.books == ["wikipedia_en_all_mini_2026-06", "wiktionary_de_all_nopic_2026-07", "wiktionary_en_all_nopic_2026-08"], kb.books
            assert kb.info["wiktionary_de_all_nopic_2026-07"]["label"] == "Wiktionary (de)" and kb.source_of("wiktionary_en_all_nopic_2026-08") == \
                {"source": "Wiktionary (en)", "book": "wiktionary_en_all_nopic_2026-08", "lang": "en"}
            for _ in range(40):
                if argv.exists():
                    break
                await asyncio.sleep(0.05)
            args = argv.read_text().split()
            assert args[3:] == [str(t / "knowledge" / z) for z in ("wikipedia_en_all_mini_2026-06.zim", "wiktionary_de_all_nopic_2026-07.zim",
                                                                    "wiktionary_en_all_nopic_2026-08.zim")], args
            WIKI, WDE, WEN = "wikipedia_en_all_mini_2026-06", "wiktionary_de_all_nopic_2026-07", "wiktionary_en_all_nopic_2026-08"
            g = kb.groups("apple", {"lang": "de", "query": "Apfel"})
            assert g == [("Apfel", [WDE]), ("apple", [WIKI]), ("apple", [WEN])], g   # native first, encyclopedia, other dictionaries
            g = kb.groups("apple")
            assert g == [("apple", [WIKI]), ("apple", [WDE]), ("apple", [WEN])], g
            seen_calls = []
            async def fake(query, limit, books=None, title_first=True):
                seen_calls.append((query, list(books)))
                src = kb.source_of(books[0])
                return [{"title": f"{query}-{i}", "snippet": f"snippet {i}", "url": f"kiwix://{books[0]}/{query}{i}", **src} for i in range(limit)]
            kb.kiwix_search = fake
            assert os.environ.get("VIBEOS_FORCE_OFFLINE"), "the suite runs forced offline"
            r = await kb.search("apple", 4, native={"lang": "de", "query": "Apfel"})
            assert r["online"] is False and r["source"] == "offline encyclopedia" and r["sources"] == ["Wiktionary (de)", "Wikipedia (en)", "Wiktionary (en)"], r
            assert [x["source"] for x in r["results"]] == ["Wiktionary (de)", "Wikipedia (en)", "Wiktionary (en)", "Wiktionary (de)"] \
                and [x["title"] for x in r["results"]] == ["Apfel-0", "apple-0", "apple-0", "Apfel-1"] \
                and all(x["book"] and x["lang"] in ("de", "en") and x["url"].startswith("kiwix://") for x in r["results"]), r["results"]
            assert seen_calls == [("Apfel", [WDE]), ("apple", [WIKI]), ("apple", [WEN])], seen_calls
            facts, source = await kb.context_block("apple", 2, native={"lang": "de", "query": "Apfel"})
            assert facts == "- Apfel-0 (Wiktionary (de)): snippet 0\n- apple-0 (Wikipedia (en)): snippet 0" \
                and source == "offline encyclopedia (Wiktionary (de), Wikipedia (en))", (facts, source)
            r = await kb.search("Albert Einstein", 3)
            assert [x["source"] for x in r["results"]] == ["Wikipedia (en)", "Wiktionary (de)", "Wiktionary (en)"] \
                and seen_calls[-3:] == [("Albert Einstein", [WIKI]), ("Albert Einstein", [WDE]), ("Albert Einstein", [WEN])], \
                "without a native query the encyclopedia leads and every group gets the query as typed"
            p0 = kb.proc
            await kb.restart_kiwix()
            for _ in range(40):
                if len(argv.read_text().splitlines()) >= 2:
                    break
                await asyncio.sleep(0.05)
            assert kb.proc is not p0 and kb.proc.returncode is None and kb.books[0] == "wikipedia_en_all_mini_2026-06" \
                and len(argv.read_text().splitlines()) == 2 and p0.returncode is not None, "restart = a new kiwix-serve over the current ZIMs"
            await kb.stop_kiwix()
            assert kb.proc is None and kb.books == [] and kb.info == {} and kb.has_local is True
        finally:
            if kb.proc and kb.proc.returncode is None:
                kb.proc.terminate(); await kb.proc.wait()
    assert server.knowledge_native("Apfel ", {"translation": "on", "language": "de"}) == {"lang": "de", "query": "Apfel"}
    assert server.knowledge_native("Apfel", {"translation": "off", "language": "de"}) is None \
        and server.knowledge_native("x", {"translation": "on", "language": "en"}) is None and server.knowledge_native("  ", {"translation": "on", "language": "de"}) is None
    real = server.translate_texts
    async def fake_tr(texts, to, frm=None, settings=None, prefer=None):
        return [f"[{to}] {t}" for t in texts], {"model": "fast", "calls": len(texts), "cached": 0, "ms": 1}
    server.translate_texts = fake_tr
    try:
        res = await server.localize_results({"results": [{"title": "Apfel", "snippet": "Frucht", "lang": "de", "source": "Wiktionary (de)"},
                                                         {"title": "Apple", "snippet": "fruit", "lang": "en", "source": "Wikipedia (en)"}]},
                                            settings={"translation": "on", "language": "de"})
    finally:
        server.translate_texts = real
    assert res["results"][0]["title"] == "Apfel" and res["results"][0]["snippet"] == "Frucht" and res["results"][1]["title"] == "[de] Apple" \
        and res["results"][1]["snippet"] == "[de] fruit" and res["translated"]["native"] == 1 and res["translated"]["calls"] == 2, res
    return ("3 ZIMs: Wikipedia first, labels, native group first (title hit leads), round-robin merge, facts name their book, "
            "restart/stop; search links + Wiktionary page text parsed; knowledge_native; native hits not re-translated")


@run_test("unit_p3_dict_wiktionary_docs_bridge_static", ["R71", "R39", "R59", "R12"])
async def t_p3_static(_):
    """[R71 P3] vibe.dict in the bridge + the dict handler in both host pages; the DOM translators mark
    their batches (`dom`) in the shell and in the bridge; the docs the coder LLM gets (API.md, core sheet,
    the settings sheet + recipe injected whole for the dock prompt) carry the dictionaries / Wiktionary
    add-ons and vibe.dict; the requirement names P3; the kernel appends --cache-ram when a manifest lacks it."""
    sys.path.insert(0, str(REPO / "kernel"))
    import server
    js = (REPO / "shell" / "shell.js").read_text()
    host = (REPO / "shell" / "bench" / "host.js").read_text()
    bridge = re.search(r"const BRIDGE_SRC = `(.*?)`;\n", js, re.S).group(1)
    assert "${" not in bridge and "`" not in bridge and not re.search(r"\\(?!/script>)", bridge), "bridge literal must stay inert"
    assert 'dict: (word, from, to) => call("dict", word, from, to)' in bridge and 'call("translate", texts, l, "dom")' in bridge
    for name, src in (("shell.js", js), ("host.js", host)):
        h = re.search(r"async function handleBridge\([^)]*\) \{.*?\n\}", src, re.S).group(0)
        assert 'method === "dict"' in h and '"/api/dict?"' in h and 'if (args[2] === "dom") body.dom = true;' in h \
            and 'p.set("from", String(args[1]))' in h and 'p.set("to", String(args[2]))' in h, name
    assert "to: lang, dom: true" in js.split("const TRANSLATOR = kairosTranslator(", 1)[1][:300], "the desktop's own batches are UI batches"
    api = (REPO / "docs" / "API.md").read_text()
    for n in ("`vibe.dict(word, from?, to?)`", "### Dictionaries & Wiktionary", "/api/dict", 'vibe.models.download("dictionaries")',
              'vibe.models.download("wiktionary_de")', "wiktionary_ru", "Wiktionary (de)", "/api/dict/status", "always go through the `fast` model"):
        assert n in api, n
    core = server.API_DOCS["core"]
    assert "vibe.dict(" in core["text"] and "dictionary" in core["keywords"] and "wiktionary" in core["keywords"] and "Wiktionary (de)" in core["text"]
    dock = re.search(r'data-app="settings" data-prompt="([^"]+)"', (REPO / "shell" / "index.html").read_text()).group(1)
    assert "dictionaries" in dock and "wiktionary_" in dock and "m.extras" in dock
    api_docs, wiki = server.api_docs_for(dock), server.wiki_pages_for(dock)
    assert api_docs[0][0] == "system-settings" and wiki[0][0] == "settings-app", (api_docs[0][0], wiki[0][0])
    sheet, recipe = api_docs[0][1], wiki[0][1]
    assert sheet == server.API_DOCS["system-settings"]["text"] and recipe == server.WIKI["settings-app"]["text"], "injected whole"
    for n in ("dictionaries", "wiktionary_", "vibe.dict(", "vibe.models.download("):
        assert n in sheet and n in recipe, f"{n!r} must reach the model through both documents"
    assert len(sheet) <= server.API_SHEET_CAP == 6800 and len(recipe) <= server.WIKI_FIRST_CAP == 11800, (len(sheet), len(recipe))   # 6200 → 6800 [R86]
    req = (REPO / "docs" / "REQUIREMENTS.md").read_text()
    assert "P3 (2026-10-07)" in req and "/api/dict" in req and "wiktionary_" in req and "--cache-ram 0" in req and "dom: true" in req
    log = (REPO / "docs" / "TRANSLATION-LAYER-2026-10-06.md").read_text()
    assert "## P3 + fixes (2026-10-07)" in log
    ts = (REPO / "kernel" / "server.py").read_text().split("class TranslationService", 1)[1].split("async def _end", 1)[0]
    assert 'if "--cache-ram" not in args:' in ts and 'args += ["--cache-ram", "0"]' in ts, "the kernel's own fast argv carries the bound"
    return (f"vibe.dict + dom flag in bridge/shell/host; API.md + core + settings sheet {len(sheet)} / recipe {len(recipe)} chars carry the add-ons; "
            "requirement (h) + log section; kernel appends --cache-ram")


NEEDS_AIOHTTP = ("unit_validate_html", "unit_desktop_hidden_linux_r68_static",       # import kernel/server.py
                 "unit_strip_thinking_blocks", "unit_chat_template_kwargs_manifest",
                 "unit_image_dims_and_seed_script",
                 "unit_engine_stamp_manifest_pairing",
                 "unit_prompt_expansion_helpers", "unit_llm_sampling_merge_and_variants",
                 "unit_engine_stamp_next_set_and_sd_server",
                 "unit_game_launch_gate_mapped_window", "unit_knowledge_base_wikipedia_mini",
                 "unit_r47_baked_set_and_build_scripts", "unit_js_syntax_gate",
                 "unit_translation_manifest_and_specs", "unit_translation_shell_bridge_docs_static",
                 "unit_p3_dictionaries_wiktionary_manifests", "unit_knowledge_multi_zim_sources",      # [R71 P3]
                 "unit_p3_dict_wiktionary_docs_bridge_static", "unit_r89_docs_and_sheets_static")


@run_test("unit_bake_flagged_addons_in_image", ["R47", "R71"])
async def t_bake_flagged_addons(_):
    """docs/IMAGE-BAKE-ADDONS-2026-10-07.md — the owner's "build it in the system": an add-on with
    "bake": true (on the section = every file of it, on one {url,file} entry = that file only) is
    copied into the image although it stays an add-on. Set on the translation section's `fast`
    file and on the `dictionaries` section in all 4 tiers + the 3 test variants, nowhere else.
    check_tier_present.py --list (what every build path bakes) = the non-addon set + exactly those
    files; --all, the knowledge-store ZIMs and the kernel's tier presence are unchanged (a baked
    add-on never becomes a tier requirement); the gate and --bytes fail on a missing baked file
    (build-img.sh exit 90/91)."""
    import subprocess
    import tempfile
    from pathlib import Path
    sys.path.insert(0, str(REPO / "kernel"))
    import server
    lister = REPO / "scripts" / "check_tier_present.py"
    run = lambda man, d, *fl: subprocess.run([sys.executable, str(lister), str(man), str(d), *fl], capture_output=True, text=True)
    paths = sorted((REPO / "manifests").glob("tier-*gb.json")) + sorted((REPO / "manifests" / "test").glob("tier-8gb-*.json"))
    assert len(paths) == 7, paths
    for p in paths:
        m = json.loads(p.read_text())
        tr, dc = m["translation"], m["dictionaries"]
        assert tr.get("addon") is True and dc.get("addon") is True, p.name
        assert tr["fast"].get("bake") is True and "bake" not in tr["quality"] and "bake" not in tr, (p.name, "the fast file only")
        assert dc.get("bake") is True and not any("bake" in v for v in [dc["beolingus"], *dc["freedict"].values()] if isinstance(v, dict)), p.name
        flagged = []

        def _flags(n, path):
            if isinstance(n, dict):
                if n.get("bake"):
                    flagged.append(path)
                for k, v in n.items():
                    _flags(v, f"{path}.{k}" if path else k)
        _flags(m, "")
        assert flagged == ["translation.fast", "dictionaries"], (p.name, flagged)
        non_addon = [e["file"] for e in server.manifest_files(m)]
        dict_files = [e["file"] for e in server.section_files(dc)]
        expected = non_addon + [tr["fast"]["file"]] + dict_files
        got = run(p, "/nonexistent", "--list")
        assert got.returncode == 0 and got.stdout.split() == expected, (p.name, got.stdout)
        assert tr["quality"].get("file", "-") not in expected and not any(f.startswith("wiktionary_") for f in expected) and len(dict_files) == 38, p.name
        for key, sec in m.items():                               # every other add-on section stays a download
            if isinstance(sec, dict) and sec.get("addon") and key not in ("translation", "dictionaries"):
                # a file the add-on shares with a non-addon section (an encoder, the VAE) is baked for the latter's sake
                assert not any(e["file"] in expected for e in server.section_files(sec) if e["file"] not in non_addon), \
                    (p.name, key, "an unflagged add-on leaks")
        every = run(p, "/nonexistent", "--all", "--list").stdout.split()
        assert set(expected) < set(every) and (tr["quality"]["file"] in every if "file" in tr["quality"] else tr["quality"]["use"] == "llm") \
            and not any(f.startswith("wiktionary_") for f in every), p.name   # [R89] the 8 GB shape: quality = the loaded LLM, no file
        assert not any(f.startswith(("translation/", "dict/")) for f in non_addon), (p.name, "a baked add-on became a tier requirement")
    # the flag's semantics on a synthetic manifest: section-level = every file, entry-level = that file, a
    # knowledge-store entry never; the kernel's manifest_files ignores the add-on section either way
    syn = {"llm": {"file": "llm/a.gguf", "url": "u", "size_gb": 1},
           "x": {"addon": True, "bake": True, "p": {"file": "x/p", "url": "u", "size_gb": 1}, "q": {"file": "x/q", "url": "u", "size_gb": 1}},
           "y": {"addon": True, "p": {"file": "y/p", "url": "u", "size_gb": 1, "bake": True}, "q": {"file": "y/q", "url": "u", "size_gb": 1}},
           "z": {"addon": True, "file": "z.zim", "url": "u", "size_gb": 1, "store": "knowledge", "bake": True},
           "w": {"addon": True, "file": "w/w", "url": "u", "size_gb": 1}}
    with tempfile.TemporaryDirectory() as td:
        man = Path(td) / "syn.json"
        man.write_text(json.dumps(syn))
        assert run(man, td, "--list").stdout.split() == ["llm/a.gguf", "x/p", "x/q", "y/p"]
        assert run(man, td, "--all", "--list").stdout.split() == ["llm/a.gguf", "x/p", "x/q", "y/p", "y/q", "w/w"]
        assert [e["file"] for e in server.manifest_files(syn)] == ["llm/a.gguf"], "tier presence must ignore baked add-ons"
        assert {e["file"] for e in server.manifest_files(syn, include_addons=True)} == {"llm/a.gguf", "x/p", "x/q", "y/p", "y/q", "z.zim", "w/w"}
        models = Path(td) / "models"
        for f in ("llm/a.gguf", "x/p", "x/q"):
            (models / f).parent.mkdir(parents=True, exist_ok=True)
            (models / f).write_bytes(b"m" * 11)
        r = run(man, models)                                     # y/p (a baked add-on file) is missing → the gate fails
        assert r.returncode == 1 and "MISSING y/p  0.0GB  (baked add-on)" in r.stdout and "OK      x/p  0.0GB  (baked add-on)" in r.stdout \
            and "INCOMPLETE" in r.stdout and "+3 baked add-on files" in r.stdout and "y/q" not in r.stdout and "w/w" not in r.stdout, r.stdout
        b = run(man, models, "--bytes")
        assert b.returncode == 1 and b.stdout == "" and b.stderr.strip() == "MISSING y/p", (b.stdout, b.stderr)
        (models / "y").mkdir()
        (models / "y" / "p").write_bytes(b"m" * 5)
        r, b = run(man, models), run(man, models, "--bytes")
        assert r.returncode == 0 and "ALL PRESENT" in r.stdout and b.returncode == 0 and int(b.stdout) == 11 * 3 + 5, (r.stdout, b.stdout)
    # the build paths consume the list unchanged and say so; the requirement and the image README name the rule
    for rel in ("iso/build-img.sh", "scripts/build-image.sh", "iso/write-usb.sh", "iso/README.md", "docs/REQUIREMENTS.md"):
        assert "IMAGE-BAKE-ADDONS-2026-10-07" in (REPO / rel).read_text(), f"{rel} does not name the bake rule"
    img = (REPO / "iso" / "build-img.sh").read_text()
    assert img.index('--list > "$BAKE_LIST"') < img.index("exit 90") < img.index("--bytes") < img.index("exit 91") < img.index('--files-from="$BAKE_LIST"'), \
        "build-img.sh: list, gate (90), bytes (91), then the rsync of the list"
    m8 = json.loads((REPO / "manifests" / "tier-8gb.json").read_text())
    extra = m8["translation"]["fast"]["size_bytes"] + sum(e["size_bytes"] for e in server.section_files(m8["dictionaries"]))
    assert extra == 1312164352 + 118463187, extra
    return f"bake flag on translation.fast + dictionaries in 7 manifests, nowhere else; 8gb list +39 files / +{extra / 1e9:.2f} GB (ZIMs + the 16 GB+ 9B stay downloads; 8 GB quality = the LLM [R89]); tier presence unchanged"

# ---------------------------------------------------------------------------
# [2026-10-14] a generation cut at the length limit is never cached (R86's GPU check: a 15 783-token page
# was cut at the 16k context and its 1 KB stump served from the cache from then on), and a missing engine
# binary is a clean 503 JSON error on every generation route (VERIFY 2026-10-10: POST /api/ask answered
# aiohttp's bare 500 for a FileNotFoundError). The mock llama-server cuts a reply on "[length-stop]".
# ---------------------------------------------------------------------------

def _mock_calls(system_start: str, needle: str) -> int:
    """how many requests the mock llama-server got with this persona and `needle` in the user text"""
    log = Path(os.environ.get("MOCK_LLAMA_MSGLOG") or VDIR / "llama-msgs.log")
    n = 0
    try:
        lines = log.read_text(errors="ignore").splitlines()
    except FileNotFoundError:
        return 0
    for line in lines:
        try:
            m = json.loads(line)
        except ValueError:
            continue
        if str(m.get("system") or "").startswith(system_start) and needle in str(m.get("user") or ""):
            n += 1
    return n


@run_test("unit_finish_info_length_limit", ["R43"])
async def t_finish_info(_):
    sys.path.insert(0, str(REPO / "kernel")); import server
    fi = server.finish_info
    assert fi("length", {}, 16384) == {"finish": "length", "tokens": None, "length": True}
    assert fi("stop", {"usage": {"completion_tokens": 16384}}, 16384)["length"] is False    # the server says it finished
    assert fi(None, {"usage": {"completion_tokens": 700}}, 700)["length"] is True            # no finish_reason, at max_tokens
    assert fi(None, {"timings": {"prompt_n": 5000, "predicted_n": 11384}}, 16384, ctx=16384)["length"] is True   # at n_ctx
    assert fi(None, {"timings": {"prompt_n": 50, "predicted_n": 300}}, 16384, ctx=16384)["length"] is False
    assert fi(None, {}, 16384, deltas=12)["length"] is False and fi(None, {}, 10, deltas=10)["length"] is True
    assert server.length_cut({"length": True}) and not server.length_cut(None) and not server.length_cut({})
    note = server.length_note({"tokens": 15783})
    assert "cut off" in note and "15783" in note and "not saved" in note, note
    b = server.length_banner("<html><body class='x'><p>hi</p></body></html>", note)
    assert b.index("<body") < b.index("kairos-length-cut") < b.index("<p>hi"), b
    return "finish_reason length / max_tokens / n_ctx -> cut; stop and short replies -> not; the note + the page banner"


@run_test("http_length_cut_page_served_not_cached", ["R43", "R67", "R75"])
async def t_length_cut_page(s):
    from urllib.parse import quote
    sys.path.insert(0, str(REPO / "kernel")); import server
    address = f"lengthcut-{int(time.time())}.test/[length-stop]"
    page = VDIR / "cache" / "web" / f"{server.web_slug(address)}.html"
    before = _mock_calls("You are the imaginary web", address)
    # /api/browse (vibe.browse): the cut page comes back with the note, is not cached, the next call regenerates.
    # The mock cuts the page; its repair round answers a whole document — a repaired stump is still a cut result.
    for n in (1, 2):
        async with s.post(f"{BASE}/api/browse", json={"address": address}) as r:
            assert r.status == 200, r.status
            j = await r.json()
        assert j["truncated"] is True and j["cached"] is False and "cut off" in j["note"], j
        assert "kairos-length-cut" in j["html"] and "kernel plumbing works" in j["html"], j["html"][:300]
        assert not page.exists(), f"browse {n}: the cut page was cached"
    assert _mock_calls("You are the imaginary web", address) == before + 2, "the second browse did not regenerate"
    assert not server.site_profile_path(server.web_site(address)).exists(), "a cut page wrote the site profile"
    # /web/ (the browser app): the loading page, then the cut page ONCE (with its banner), then a fresh generation
    url = f"{BASE}/web/{quote(address, safe='/')}"
    for t in range(1, 80):
        async with s.get(url + f"?_t={t}") as r:
            body = await r.text()
        if "kairos-length-cut" in body:
            break
        await asyncio.sleep(0.5)
    else:
        raise AssertionError("the cut page was never served on /web/")
    assert not page.exists(), "/web/ cached the cut page"
    async with s.get(url + "?_t=200") as r:
        again = await r.text()
    assert "kairos-length-cut" not in again and "imaginary web is" in again, "the next visit did not regenerate"
    for t in range(201, 280):                          # let that generation finish (it is served once, then gone)
        async with s.get(url + f"?_t={t}") as r:
            if "kairos-length-cut" in await r.text():
                break
        await asyncio.sleep(0.5)
    assert _mock_calls("You are the imaginary web", address) == before + 4
    log = (VDIR / "kernel.log").read_text(errors="ignore")
    assert f"[kernel] length limit: web page '{address}" in log, "no journal line for the cut page"
    return "browse + /web/: served once with the banner, never in cache/web, no site profile, every visit regenerates"


@run_test("http_length_cut_app_served_not_saved", ["R43", "R77", "R60"])
async def t_length_cut_app(s):
    apps = VDIR / "cache" / "apps"
    stamp = int(time.time())
    app_id = f"lengthcut-app-{stamp}"
    for n in (1, 2):                                   # a new app: shown with the note, nothing saved, regenerated
        events = await sse_collect(s, f"{BASE}/api/app", {"prompt": "a notes app [length-stop]", "app_id": app_id})
        kinds = [e for e, _ in events]
        assert "cached" not in kinds and "done" in kinds, f"summon {n}: {kinds}"
        done = next(d for e, d in events if e == "done")
        assert done["truncated"] is True and "cut off" in (done.get("warning") or ""), done
        assert done["html"] and any(e == "delta" and "cut off" in d["text"] for e, d in events), "no cut-off note in the stream"
        assert not (apps / app_id).exists(), f"summon {n}: the cut app was saved"
    async with s.get(f"{BASE}/api/apps") as r:
        assert app_id not in [a["app_id"] for a in (await r.json())["apps"]], "a cut app is listed"
    # a saved app: ✨ improve (evolve) and modify with a cut reply leave app.html, meta.json and history/ alone
    base = f"lengthcut-base-{stamp}"
    events = await sse_collect(s, f"{BASE}/api/app", {"prompt": "a counter app", "app_id": base})
    done = next(d for e, d in events if e == "done")
    assert done["truncated"] is False and (apps / base / "app.html").exists(), done
    for _ in range(60):                                # the icon painter writes meta.json once, in the background
        if "icon" in json.loads((apps / base / "meta.json").read_text()):
            break
        await asyncio.sleep(0.25)
    saved, meta = (apps / base / "app.html").read_text(), (apps / base / "meta.json").read_text()
    events = await sse_collect(s, f"{BASE}/api/app", {"prompt": "a counter app", "app_id": base, "evolve": True,
                                                       "instruction": "add a history panel [length-stop]"})
    done = next(d for e, d in events if e == "done")
    assert done["truncated"] is True and done["version"] == 2, done
    assert (apps / base / "app.html").read_text() == saved, "a cut evolution overwrote app.html"
    assert (apps / base / "meta.json").read_text() == meta, "a cut evolution rewrote meta.json"
    assert not (apps / base / "history").exists(), "a cut evolution wrote history"
    async with s.post(f"{BASE}/api/app/{base}/modify", json={"instruction": "dark theme [length-stop]"}) as r:
        assert r.status == 200
        j = await r.json()
    assert j["truncated"] is True and "cut off" in j["warning"] and j["html"], j
    assert (apps / base / "app.html").read_text() == saved, "a cut modify overwrote the app"
    events = await sse_collect(s, f"{BASE}/api/app", {"prompt": "a counter app", "app_id": base})
    assert events[0][0] == "cached" and events[0][1]["version"] == 1, events[:1]
    # the self-heal route: a repair reply cut at the limit is returned with the note, never written over the app
    healed = f"lengthcut-heal-{stamp}"
    (apps / healed).mkdir(parents=True)
    (apps / healed / "app.html").write_text(saved)
    (apps / healed / "meta.json").write_text(json.dumps({"app_id": healed, "prompt": "a timer [length-stop-all]", "version": 1}))
    async with s.post(f"{BASE}/api/app/{healed}/repair", json={"error": {"type": "TypeError", "message": "x is undefined",
                                                                          "line": 3}}) as r:
        assert r.status == 200
        j = await r.json()
    assert j["truncated"] is True and "cut off" in j["warning"] and j["html"], j
    assert (apps / healed / "app.html").read_text() == saved, "a cut repair overwrote the app"
    log = (VDIR / "kernel.log").read_text(errors="ignore")
    for what in (f"app '{app_id}", f"app '{base}", f"app modify '{base}", f"app repair '{healed}"):
        assert f"[kernel] length limit: {what}" in log, what
    return "new app: done{truncated} twice, never saved/listed; evolve + modify + self-heal repair of a saved app: v1 stays byte-identical"


@run_test("http_length_cut_game_runs_not_installed", ["R43", "R77", "R7"])
async def t_length_cut_game(s):
    try:
        import pygame  # noqa: F401
    except ImportError:
        raise SkipTest("pygame not installed yet (arrives with engine install)")
    import signal
    sys.path.insert(0, str(REPO / "kernel")); import server
    prompt = f"lengthcut ball {int(time.time())} [length-stop]"
    gdir = VDIR / "games" / server.slugify(prompt)
    async with s.post(f"{BASE}/api/game", json={"prompt": prompt}) as r:
        jid = (await r.json())["job"]
    res = (await poll_job(s, jid))["result"]
    assert res.get("truncated") is True and "cut off" in (res.get("warning") or ""), res
    assert not (gdir / "main.py").exists() and not (gdir / "meta.json").exists(), "a cut game was installed"
    async with s.get(f"{BASE}/api/apps") as r:
        assert gdir.name not in [g["slug"] for g in (await r.json())["games"]]
    if res.get("running") and res.get("pid"):
        assert list(gdir.glob(".length-cut-v*.py")), "the cut game does not run from its own file"
        try:
            os.killpg(res["pid"], signal.SIGTERM)
        except ProcessLookupError:
            pass
    for _ in range(40):                                # the reaper removes the run's file with the process
        if not list(gdir.glob(".length-cut-v*.py")):
            break
        await asyncio.sleep(0.25)
    assert not list(gdir.glob(".length-cut-v*.py")), "the cut run's file outlived the game"
    assert not (gdir / "meta.json").exists(), "the cut run's exit wrote a crash record"
    log = (VDIR / "kernel.log").read_text(errors="ignore")
    assert f"[kernel] length limit: game '{gdir.name}" in log
    return f"job result truncated + warning, ran from its own file (running={res.get('running')}), no main.py / meta.json"


@run_test("http_engine_missing_clean_503", ["R67", "R63", "R86"])
async def t_engine_missing(s):
    """A kernel with the engine stamp but no engine binary (a desk / dev box without the engine set):
    every generation route answers 503 {"error": "... is not installed ...", "engine", "missing": true} —
    no job, no traceback, no bare 500 — and a cached app still opens."""
    import subprocess, tempfile, urllib.request
    port, lport, sdport, trport = 1376, 8141, 8142, 8143
    tmp = Path(tempfile.mkdtemp(prefix="kairos-noengine-"))
    (tmp / "bin").mkdir()
    shutil.copy(VDIR / "bin" / "ENGINES.json", tmp / "bin" / "ENGINES.json")     # the stamp only, no binary
    (tmp / "models").symlink_to(VDIR / "models")                                  # the placeholder model files
    env = dict(os.environ, VIBEOS_DIR=str(tmp), VIBEOS_PORT=str(port), VIBEOS_TOKEN_FILE=f"{tmp}/token",
               VIBEOS_LLAMA_PORT=str(lport), VIBEOS_SD_PORT=str(sdport), VIBEOS_TRANSLATE_PORT=str(trport),
               KAIROS_LOG_DIR=str(tmp / "log"))
    env.pop("VIBEOS_HOST", None)
    logf = open(tmp / "kernel.log", "wb")
    proc = subprocess.Popen([sys.executable, str(REPO / "kernel" / "server.py")], env=env, stdout=logf,
                            stderr=subprocess.STDOUT)
    kb = f"http://127.0.0.1:{port}"

    async def post(path, body):
        async with s.post(kb + path, json=body, timeout=aiohttp.ClientTimeout(total=60)) as r:
            return r.status, (await r.json() if "json" in r.headers.get("Content-Type", "") else await r.text())
    try:
        for _ in range(80):
            try:
                urllib.request.urlopen(f"{kb}/api/status", timeout=1).read()
                break
            except Exception:
                assert proc.poll() is None, (tmp / "kernel.log").read_text(errors="ignore")[-800:]
                await asyncio.sleep(0.5)
        else:
            raise AssertionError("the engine-less kernel never answered")
        routes = [("/api/ask", {"prompt": "hello"}, "llama-server"),
                  ("/api/app", {"prompt": "a calculator"}, "llama-server"),
                  ("/api/browse", {"address": "noengine.test"}, "llama-server"),
                  ("/api/game", {"prompt": "pong"}, "llama-server"),
                  ("/api/image", {"prompt": "a fox"}, "sd-cli"),
                  ("/api/images", {"prompts": ["a fox", "a cat"]}, "sd-cli"),
                  ("/api/video", {"prompt": "waves"}, "sd-cli"),
                  ("/api/music", {"prompt": "synthwave"}, "ace-lm")]
        for path, body, eng in routes:
            st, j = await post(path, body)
            assert st == 503, f"{path}: HTTP {st} {str(j)[:300]}"
            assert isinstance(j, dict) and "not installed" in j["error"] and j["engine"] == eng and j["missing"] is True, (path, j)
        async with s.get(f"{kb}/web/noengine.test/page") as r:          # the browser's page: its error page, a 503
            assert r.status == 503 and "not installed" in await r.text(), r.status
        for k, v in (("translation", "on"), ("language", "de")):        # the translation layer's model on demand
            st, _ = await post("/api/settings", {"key": k, "value": v})
            assert st == 200, (k, st)
        st, j = await post("/api/translate", {"text": "Hello world", "to": "de"})
        assert st == 503 and "not installed" in j["error"] and j["engine"] == "llama-server", (st, j)
        async with s.get(f"{kb}/api/status") as r:
            assert not (await r.json())["jobs"], "a 503 created a job"
        # a cached app opens instantly with no engine at all (only a generation needs one)
        adir = tmp / "cache" / "apps" / "cached-noengine"
        adir.mkdir(parents=True)
        (adir / "app.html").write_text("<!DOCTYPE html><html><body><p>cached</p></body></html>")
        (adir / "meta.json").write_text(json.dumps({"app_id": "cached-noengine", "prompt": "cached", "version": 1}))
        events = await sse_collect(s, f"{kb}/api/app", {"prompt": "cached", "app_id": "cached-noengine"})
        assert events and events[0][0] == "cached", events[:2]
        log = (tmp / "kernel.log").read_text(errors="ignore")
        for bad in ("Traceback", "Server got itself in trouble", "FileNotFoundError"):
            assert bad not in log, f"{bad!r} in the kernel log:\n{log[-1500:]}"
        assert "HTTP 503 POST /api/ask" in log, log[-800:]
    finally:
        proc.kill(); proc.wait(); logf.close()
        shutil.rmtree(tmp, ignore_errors=True)
    return "ask/app/browse/game/image/images/video/music/translate -> 503 JSON naming the engine; /web/ error page 503; no job, no traceback; cached app opens"


async def main():
    tests = [v for v in globals().values() if getattr(v, "_test", False)]
    # KAIROS_TEST_ONLY=substr[,substr…] runs a subset while iterating on a fix (results.json is
    # NOT written then — it always reflects a whole-suite run)
    only = [x.strip() for x in os.environ.get("KAIROS_TEST_ONLY", "").split(",") if x.strip()]
    if only:
        tests = [t for t in tests if any(o in t._name for o in only)]
    print(f"kAIrOS test suite — {len(tests)} tests against {BASE}" + (f" (subset: {only})" if only else ""), flush=True)
    if aiohttp is None:
        print("(aiohttp not installed — running unit subset only)", flush=True)
        for t in tests:
            if t._name.startswith("http_") or t._name in NEEDS_AIOHTTP:
                record(t._name, t._reqs, "skip", "aiohttp not installed yet (engine install pending)")
            else:
                await t(None)
    else:
        async with aiohttp.ClientSession() as s:
            for t in tests:
                await t(s)
    out = {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "base": BASE, "tests": RESULTS}
    if not only:
        (REPO / "tests" / "results.json").write_text(json.dumps(out, indent=1))
    fails = [n for n, r in RESULTS.items() if r["status"] == "fail"]
    passes = sum(1 for r in RESULTS.values() if r["status"] == "pass")
    skips = sum(1 for r in RESULTS.values() if r["status"] == "skip")
    print(f"\n{passes} passed, {len(fails)} failed, {skips} skipped -> tests/results.json", flush=True)
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    asyncio.run(main())
