#!/usr/bin/env python3
"""Mock llama-server: OpenAI-compatible /v1/chat/completions with streaming.

Lets the vibeOS kernel + shell be tested end-to-end before real models arrive.
Returns a tiny working HTML app / Python game / text depending on the system prompt.
"""
import asyncio
import json
import os
import re
import sys

from aiohttp import web

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8081

# [R86] the refusal fallback (docs/REFUSAL-FALLBACK-2026-10-09.md): the mock refuses like Gemma 4 does.
# A request whose user text carries a magic token answers a refusal — the owner's real one —
#   "[refuse]"        unless the system prompt carries prompts.ADULT_PREAMBLE (the ladder's preamble rung),
#                     the expander's 18+ rule set, or this server runs an uncensored GGUF (the shim exports
#                     MOCK_LLAMA_MODEL = the kernel's -m path: the llm_uncensored rung / an expander_fallback_file);
#   "[refuse-hard]"   unless the GGUF is uncensored (the preamble does not help);
#   "[refuse-always]" on every model (the ladder's exhausted path);
#   "[refuse-long]"   like [refuse], with a refusal longer than the stream's 400-char early verdict;
#   "[refuse-de]"     like [refuse], in German.
# The preamble is stripped before the persona routing below, exactly as the kernel's language_instruction does.
_KERNEL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "kernel")
try:
    sys.path.insert(0, _KERNEL_DIR)
    from prompts import ADULT_PREAMBLE
except Exception:                                   # the kernel tree is always beside tests/ — belt and braces
    ADULT_PREAMBLE = "ADULT CONTENT SYSTEM (18+)."
finally:
    if _KERNEL_DIR in sys.path:
        sys.path.remove(_KERNEL_DIR)
_MODEL = os.path.basename(os.environ.get("MOCK_LLAMA_MODEL", "")).lower()
UNCENSORED = any(w in _MODEL for w in ("uncensored", "abliterated", "heretic"))
REFUSAL = ("I cannot fulfill this request. I am programmed to be a helpful and harmless AI assistant. "
           "My safety guidelines prohibit me from generating sexually explicit content or pornography.")
REFUSAL_LONG = (REFUSAL + " " + "Creating such material would go against the principles I follow and the purpose "
                "I was built for, which is to keep every interaction safe, respectful and useful for everyone. " * 3
                + "If you have another request, I am happy to help.")
REFUSAL_DE = ("Es tut mir leid, aber ich kann dir dabei nicht helfen. Ich darf keine sexuell expliziten Inhalte "
              "erstellen, da das gegen meine Richtlinien verstößt.")
# [R86 round 2] "[refuse-minors]": a refusal that NAMES minors, on every model (round 4: no longer special to the
# kernel — it is a refusal on every model, i.e. the ladder is exhausted); "[refuse-html]": the refusal wrapped in a document with a long <head> (the stream's
# early verdict saw only '<!DOCTYPE' before round 2); the MT prompt: "[mt=<english>]" answers that English for the
# text (a pair hidden in the OS language), "[refuse-mt]" declines the translation.
REFUSAL_MINORS = ("I cannot create this content. The request appears to involve minors, and I do not generate sexual "
                  "content involving anyone under 18.")
REFUSAL_HTML = ("<!DOCTYPE html>\n<html><head><meta charset='utf-8'><title>Request declined</title>\n<style>\n"
                + "body{margin:0;background:#101018;color:#eee;font-family:system-ui}\n.card{padding:24px;border-radius:12px}\n" * 6
                + "</style></head><body><div class='card'><p>I'm sorry, but I can't create pornographic content.</p></div>"
                "</body></html>")


def refusal_for(system, user, preamble):
    """[R86] the refusal text the mock answers for this request, or None (answer normally)"""
    u = json.dumps(user, ensure_ascii=False) if not isinstance(user, str) else user
    if "[refuse-always]" in u:
        return REFUSAL
    if "[refuse-minors]" in u:                       # [R86 round 2] on every model, preamble or not
        return REFUSAL_MINORS
    adult = preamble or "Adult Content (18+ system)" in system
    if "[refuse-html]" in u:                         # [R86 round 2] the refusal as a document with a long head
        return None if (UNCENSORED or adult) else REFUSAL_HTML
    if "[refuse-hard]" in u:
        return None if UNCENSORED else REFUSAL
    if "[refuse-long]" in u:
        return None if (UNCENSORED or adult) else REFUSAL_LONG
    if "[refuse-de]" in u:
        return None if (UNCENSORED or adult) else REFUSAL_DE
    if "[refuse]" in u:
        return None if (UNCENSORED or adult) else REFUSAL
    return None

APP_HTML = """<!DOCTYPE html>
<html><head><style>
body{margin:0;height:100vh;display:flex;flex-direction:column;align-items:center;justify-content:center;
background:#0a0a14;color:#e8e8f2;font-family:system-ui;gap:12px}
button{padding:10px 20px;border-radius:10px;border:0;background:#7c5cff;color:#fff;cursor:pointer}
#n{font-size:48px;font-weight:700;color:#41e2ff}
</style></head><body>
<div>mock app — kernel plumbing works ✔</div><div id="n">0</div>
<button onclick="document.getElementById('n').textContent=+document.getElementById('n').textContent+1">count</button>
<button onclick="vibe.notify('hello from inside a hallucinated app')">notify</button>
</body></html>"""

# [D7, docs/FIXES-2026-09-28.md] an app whose inline script does not parse — the feature test's
# qwen35-9b notes pattern (`async name: async function()`, V8: "Unexpected token ':'"): a request
# carrying "[broken-js]" gets it; the self-repair daemon (HTML_FIX_SYSTEM) answers with APP_HTML.
BROKEN_JS_APP_HTML = APP_HTML.replace("</body></html>", """<script>
const app = { async generateImage: async function() { return 1 }, count: 0 };
function bump() { app.count += 1; }
</script>
</body></html>""")

# [N1/D5, docs/FIXES-2026-09-29.md] the dialog check on the deterministic path (the re-verification's
# test double, now in the repo): "[uses-prompt]" wires "New file" to prompt() — the kernel's check must
# send it into a repair round before done; "[image-prompt-field]" has NO dialog, only the placeholder
# text "image prompt (e.g. …)", a comment and a string naming prompt()/confirm() — no repair round.
PROMPT_APP_HTML = APP_HTML.replace("</body></html>", """<button id="nf">New file</button>
<script>
document.getElementById("nf").onclick = async () => { const name = prompt("File name?"); if (name) await vibe.fs.write("/data/" + name, ""); };
</script>
</body></html>""")
FIELD_APP_HTML = APP_HTML.replace("</body></html>", """<input id="p" placeholder="Describe your image prompt (e.g. a red fox)"><button id="go">Paint</button>
<p>please confirm (type YES below)</p><img id="out" alt="">
<script>
// never use prompt() here — the OS blocks it; "confirm() is blocked" too
const hint = "confirm() is blocked"; const tpl = `image prompt (e.g. a fox)`;
document.getElementById("go").onclick = async () => { document.getElementById("out").src = await vibe.image(document.getElementById("p").value); };
</script>
</body></html>""")

GAME_PY = """import pygame, sys
pygame.init()
screen = pygame.display.set_mode((960, 540))
pygame.display.set_caption("mock game")
clock = pygame.time.Clock()
x = 100
while True:
    for e in pygame.event.get():
        if e.type == pygame.QUIT or (e.type == pygame.KEYDOWN and e.key == pygame.K_ESCAPE):
            pygame.quit(); sys.exit()
    x = (x + 3) % 960
    screen.fill((10, 10, 20))
    pygame.draw.circle(screen, (124, 92, 255), (x, 270), 40)
    pygame.display.flip()
    clock.tick(60)
"""

# [D-R68-6] a game that dies N seconds in — "[dies after Ns]" in the request (the prompt
# carries it into the first forge and the evolve request; the code carries it as a
# comment so a repair round sees it too). The forge DEBUGGER with a TRACEBACK in hand,
# and an evolve request carrying a BUG REPORT, get the healthy game back: a repair
# that only happens when the crash actually reached the model.
CRASHY_GAME_PY = """import pygame, sys, time
pygame.init()
screen = pygame.display.set_mode((960, 540))
pygame.display.set_caption("mock game [dies after %N%s]")
clock = pygame.time.Clock()
x = 100
t0 = time.time()
while True:
    for e in pygame.event.get():
        if e.type == pygame.QUIT or (e.type == pygame.KEYDOWN and e.key == pygame.K_ESCAPE):
            pygame.quit(); sys.exit()
    x = (x + 3) % 960
    screen.fill((10, 10, 20))
    pygame.draw.circle(screen, (255, 92, 92), (x, 270), 40)
    pygame.display.flip()
    clock.tick(60)
    if time.time() - t0 > %N%:
        raise RuntimeError("mock crash after %N% s")
"""


def game_reply(system, user):
    # [R82] ✨ improve with a request: the next version names it in its caption (a healthy game)
    ins = re.search(r"USER IMPROVEMENT REQUEST: ([^\n]*)", user)
    if ins:
        return GAME_PY.replace('set_caption("mock game")', "set_caption(" + json.dumps("mock game · " + ins.group(1)[:60], ensure_ascii=False) + ")")
    if "BUG REPORT" in user or ("debugger" in system and "TRACEBACK:" in user):
        return GAME_PY
    m = re.search(r"\[dies after (\d+)s\]", user)
    return CRASHY_GAME_PY.replace("%N%", m.group(1)) if m else GAME_PY


BROWSER_HTML = """<!DOCTYPE html>
<html><head><style>body{background:#0a0a14;color:#eee}</style></head><body>
<h1>Mock Imaginary Site</h1>
<nav><a href="/web/mock.site/about">about</a> <a href="menu">menu</a>
<a href="https://real.example.com/page">external</a>
<a href="another.page/link" target="_blank">a link</a></nav>
<img src="/genimg/a sleek neon hovercar" alt="hero" style="width:100%;height:200px">
<img data-gen="a glowing data crystal">
<img src="images/phantom-street.jpg" alt="a rainy neon street at night">
<img src="data:image/jpeg;base64,/9j/FAKEHALLUCINATEDGARBAGE==">
<svg width="40" height="40"><circle cx="20" cy="20" r="18" fill="#41e2ff"/></svg>
<form method="get" action="search"><input name="q"></form>
</body></html>"""

# what the model answers when the App Launcher forge asks for an app WITH resources
FORGE_HTML = """<!DOCTYPE html>
<html><head><style>body{background:#0a0a14;color:#eee}</style></head><body>
<h1>mock forged app</h1>
<img data-gen="a glowing plasma orb" alt="orb" style="width:100%">
<audio data-gen-music="dreamy synthwave with a warm bassline" data-gen-lyrics="[Verse]\nneon rain" controls loop></audio>
<video data-gen-video="slow orbit around a neon planet" controls loop muted></video>
</body></html>"""


def vision_reply(content):
    """[R70] a typed-content user message (OpenAI image_url parts, what llama-server's
    multimodal path takes): report the picture as received — PNG width x height from
    its IHDR — and echo the question, so a test can assert the kernel fitted the image."""
    import base64, struct
    seen, text = [], ""
    for part in content:
        if part.get("type") == "image_url":
            url = part["image_url"]["url"]
            head, _, b64 = url.partition(",")
            data = base64.b64decode(b64) if head.startswith("data:") else b""
            if data[:8] == b"\x89PNG\r\n\x1a\n":
                w, h = struct.unpack(">II", data[16:24])
                seen.append(f"png {w}x{h} ({len(data)} bytes)")
            else:
                seen.append(f"{head} ({len(data)} bytes)")
        elif part.get("type") == "text":
            text += part.get("text", "")
    return f"mock vision: {'; '.join(seen) or 'no image'} | question: {text}"


def expand_reply(system, user, payload):
    """The Krea prompt expander (kernel EXPANDERS): the shape a real Qwen3-VL answer has — a
    think block, a short planning line, then the paragraph — with ( ) [ ] the kernel must strip
    (sd.cpp attention weights) and a marker naming which expander ruled (SFW vs 18+), plus the
    sampler fields the kernel is expected to send."""
    which = "for adults" if "Adult Content (18+ system)" in system else "for everyone"
    sampling = ",".join(f"{k}={payload.get(k)}" for k in ("top_k", "top_p", "min_p", "repeat_penalty"))
    return ("<think>choosing a medium and a lens</think>\n"
            "Planning: photo, warm light.\n\n"
            f"A realistic photograph of {user} (with soft light) [sharp focus], expanded by the mock "
            f"expander {which}; sampling {sampling}.")


TRANSLATE_CODES = {"English": "en", "German": "de", "Spanish": "es", "French": "fr",
                   "Italian": "it", "Japanese": "ja", "Russian": "ru"}


def translate_reply(user):
    """[R71] the MT model (the Index-Translate prompt: a single user turn, no system message):
    "Translate the following text into German. …:\\n\\n<text>" → a think block (the kernel must
    strip it) + "[de] <text>" — the code marks the target the kernel asked for, the text echoes
    what it sent, so a test can assert batching, caching and the English hop of P2."""
    head, _, text = user.partition("\n\n")
    m = re.match(r"Translate the following text into ([A-Za-z]+)\.", head)
    code = TRANSLATE_CODES.get(m.group(1) if m else "", "xx")
    if "[refuse-mt]" in text:                        # [R86 round 2, S7] the MT model declines the sentence
        return REFUSAL
    if "[mt-minors]" in text:                        # [R86 round 2, B1] a text whose ENGLISH pairs the terms (the marker does not)
        return f"<think>translating into {code}</think>\nnude 12 year old girl"
    hidden = re.search(r"\[mt=(.*?)\]", text)        # [R86 round 2, B1] the English of a request in the OS language
    if hidden:
        return f"<think>translating into {code}</think>\n{hidden.group(1)}"
    return f"<think>translating into {code}</think>\n[{code}] {text}"


def evolve_page_reply(user):
    """[R84] the page writer's next version of a page (server._web_improve): BROWSER_HTML with a <title> and an
    <h2 class="improved"> that carry the version number and the user's instruction — the suite can tell versions apart"""
    import html as _html
    text = user if isinstance(user, str) else ""
    v = re.search(r"Write version (\d+) of this page", text)
    ins = re.search(r"USER IMPROVEMENT REQUEST: ([^\n]*)", text)
    tag = f"v{v.group(1) if v else '?'}"
    what = _html.escape(ins.group(1).strip()) if ins else "no instruction"
    return BROWSER_HTML.replace("<head>", f"<head><title>Mock Imaginary Site {tag}</title>", 1).replace(
        "<h1>Mock Imaginary Site</h1>", f"<h1>Mock Imaginary Site</h1><h2 class='improved'>improved {tag}: {what}</h2>", 1)


def pick_reply(payload):
    first = payload["messages"][0]
    if first.get("role") != "system":               # [R71] the MT prompt carries no system message
        if isinstance(first.get("content"), str) and first["content"].startswith("Translate the following text into"):
            return translate_reply(first["content"])
        system = ""
    else:
        system = first["content"]
    preamble = isinstance(system, str) and system.startswith(ADULT_PREAMBLE)
    if preamble:                                 # [R86] the ladder's preamble rung: route by the real persona
        system = system[len(ADULT_PREAMBLE):]
    last = payload["messages"][-1].get("content")
    refusal = refusal_for(system, last, preamble)   # [R86] a magic token in the user text -> Gemma's refusal
    if refusal:
        return refusal
    if isinstance(last, list):                   # multimodal content parts [R70]
        return vision_reply(last)
    if system.startswith("You are an expert prompt engineer for text-to-image models"):
        return expand_reply(system, last, payload)
    # order matters: APP_SYSTEM *mentions* the imaginary web in its API notes,
    # so match the browser persona by its opening phrase, not the bare words
    if system.startswith("You are the imaginary web") and "WEBSITE EVOLUTION" in system:
        return evolve_page_reply(last)         # [R84] ✨ improve a website: the next version names the request
    if system.startswith("You are the imaginary web"):
        return BROWSER_HTML
    if system.startswith("You are the kAIrOS app designer"):
        return ("PURPOSE: mock spec for the requested app. FEATURES: works; is tested. "
                "LAYOUT: one panel. DATA & APIS: vibe.store for state.")
    if "GENERATED RESOURCES" in system:
        return FORGE_HTML
    if "self-repair daemon" in system:
        # every repair round returns the healthy app — except for "[broken-js-unfixable]", whose
        # purpose demands the broken line: the gate's repair budget runs out (N5)
        return BROKEN_JS_APP_HTML if "[broken-js-unfixable]" in str(last) else APP_HTML
    if "[broken-js" in str(last):
        return BROKEN_JS_APP_HTML
    if "[uses-prompt]" in str(last):
        return PROMPT_APP_HTML
    if "[image-prompt-field]" in str(last):
        return FIELD_APP_HTML
    if "vibeOS kernel" in system or "HTML document" in system:
        return APP_HTML
    if "pygame" in system:
        return game_reply(system, payload["messages"][-1]["content"])
    return '{"headlines": ["Mock LLM Answers Everything", "Local GPUs Dream of Electric Apps"]}'


def length_stop(payload) -> bool:
    """[2026-10-14] "[length-stop]" in the last user text: the reply stops at the length limit the way
    llama-server's does — the text cut to its first 60 %, finish_reason "length", the token count at
    max_tokens. Not for the self-repair daemon (its reply is whole: the kernel must still treat a repaired
    stump as a cut result) and not for a request without the token (finish_reason "stop").
    "[length-stop-all]" cuts every reply, the self-repair daemon's too (the app repair route)."""
    msgs = payload.get("messages") or []
    last = msgs[-1].get("content") if msgs else None
    if not isinstance(last, str) or "[length-stop" not in last:
        return False
    if "[length-stop-all]" in last:
        return True
    if "[length-stop]" not in last:
        return False
    system = msgs[0].get("content") if msgs and msgs[0].get("role") == "system" else ""
    return not (isinstance(system, str) and "self-repair daemon" in system)


async def chat(request):
    payload = await request.json()
    if os.environ.get("MOCK_LLAMA_MSGLOG"):          # every request's prompt text, one JSON line [R71]
        msgs = payload.get("messages", [])
        sysm = msgs[0].get("content") if msgs and msgs[0].get("role") == "system" else None
        last = msgs[-1].get("content") if msgs else None
        with open(os.environ["MOCK_LLAMA_MSGLOG"], "a") as f:
            f.write(json.dumps({"port": PORT, "system": sysm, "user": last if isinstance(last, str) else "[parts]",
                                "temperature": payload.get("temperature"),
                                "chat_template_kwargs": payload.get("chat_template_kwargs")}, ensure_ascii=False) + "\n")
    text = pick_reply(payload)
    finish, tokens = "stop", None
    if length_stop(payload):                         # [2026-10-14] a reply cut at the length limit
        text, finish, tokens = text[:max(1, len(text) * 3 // 5)], "length", payload.get("max_tokens")
    tokens = tokens or max(1, len(text) // 4)
    if not payload.get("stream"):
        return web.json_response({"choices": [{"message": {"content": text}, "finish_reason": finish}],
                                  "usage": {"completion_tokens": tokens}})
    resp = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
    await resp.prepare(request)
    # a prompt carrying "[slow]" streams at 0.4 s per chunk: a generation the suite can
    # catch mid-stream (the kernel-stop test holds it open while sending SIGTERM)
    pace = 0.4 if "[slow]" in json.dumps(payload.get("messages", [])) else 0.02
    for i in range(0, len(text), 48):
        chunk = {"choices": [{"delta": {"content": text[i:i + 48]}}]}
        await resp.write(f"data: {json.dumps(chunk)}\n\n".encode())
        await asyncio.sleep(pace)  # simulate token streaming
    # the last chunk, as llama-server sends it: an empty delta with the finish_reason + the timings [2026-10-14]
    last = {"choices": [{"delta": {}, "finish_reason": finish}], "timings": {"predicted_n": tokens}}
    await resp.write(f"data: {json.dumps(last)}\n\n".encode())
    await resp.write(b"data: [DONE]\n\n")
    await resp.write_eof()
    return resp


app = web.Application()
app.router.add_get("/health", lambda r: web.json_response({"status": "ok"}))
app.router.add_post("/v1/chat/completions", chat)
web.run_app(app, host="127.0.0.1", port=PORT, print=None)
