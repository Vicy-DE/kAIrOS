#!/usr/bin/env python3
"""Mock llama-server for tests/test_forge_quality.py [R77]: scripted app/game forge answers keyed by
markers in the prompt, so every branch of the forge's check → repair loop is exercised end to end.

    python3 mock_forge_llama.py <port>

Apps (APP_SYSTEM / APP_EVOLVE_SYSTEM via chat_stream, HTML_FIX_SYSTEM "self-repair daemon" via chat):
  [js-3rounds]     the script does not parse; the repair daemon only fixes it in its 3rd reply
  [js-identical]   the script does not parse; the daemon sends the SAME document back unless the
                   kernel's message carries the IDENTICAL note — then it fixes it
  [evolve-regress] v1 healthy; every improvement (and every repair of it) does not parse
  [runtime-click]  parses, but its "Add" button throws a TypeError; fixed once a repair names
                   the runtime error
Games (GAME_SYSTEM "game forge of", GAME_FIX_SYSTEM "game forge debugger"):
  [undefined-name] a NameError in a branch a play-test never reaches; fixed when "undefined name"
                   reaches the debugger
  [crash-on-key]   an IndexError only on the RIGHT key; fixed when the traceback + the input log
                   (KEYDOWN right) reach the debugger
  [identical]      crashes at load; the debugger sends the identical source back unless the
                   IDENTICAL note is in the message
  [never-fixed]    v1 healthy; every evolution and every repair of it crashes at load
"""
import asyncio
import json
import os
import sys

from aiohttp import web

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8101
COUNT = {}

APP_OK = """<!DOCTYPE html>
<html><head><title>forge app %MARK%</title><style>body{background:#0a0a14;color:#eee}</style></head><body>
<h1>forge mock app %MARK%</h1><input id="item" placeholder="item"><button id="add">Add</button><ul id="list"></ul>
<script>
const list = document.getElementById("list");
document.getElementById("add").addEventListener("click", () => {
  const li = document.createElement("li");
  li.textContent = document.getElementById("item").value;
  list.appendChild(li);
});
</script>
</body></html>"""

APP_BROKEN_JS = """<!DOCTYPE html>
<html><head><title>forge app %MARK%</title></head><body>
<h1>broken mock app %MARK%</h1>
<script>
// %NOTE%
const app = { async generateImage: async function() { return 1 }, count: 0 };
</script>
</body></html>"""

APP_RUNTIME_CLICK = """<!DOCTYPE html>
<html><head><title>forge app %MARK%</title></head><body>
<h1>click bug %MARK%</h1><input id="item"><button id="add">Add</button><ul id="list"></ul>
<script>
document.getElementById("add").addEventListener("click", () => {
  const v = document.getElementById("itemz").value;
  document.getElementById("list").textContent = v;
});
</script>
</body></html>"""

GAME_OK = """import pygame, sys
# %MARK%
pygame.init()
screen = pygame.display.set_mode((960, 540))
pygame.display.set_caption("forge mock game %MARK%")
clock = pygame.time.Clock()
x = 100
while True:
    for e in pygame.event.get():
        if e.type == pygame.QUIT or (e.type == pygame.KEYDOWN and e.key == pygame.K_ESCAPE):
            pygame.quit(); sys.exit()
    keys = pygame.key.get_pressed()
    x = (x + (3 if keys[pygame.K_RIGHT] else 1)) % 960
    screen.fill((10, 10, 20))
    pygame.draw.circle(screen, (124, 92, 255), (x, 270), 40)
    pygame.display.flip()
    clock.tick(60)
"""

GAME_UNDEFINED = """import pygame, sys
# %MARK%
pygame.init()
screen = pygame.display.set_mode((960, 540))
clock = pygame.time.Clock()
lives = 3


def game_over():
    print("final score", final_scroe)


while True:
    for e in pygame.event.get():
        if e.type == pygame.QUIT:
            pygame.quit(); sys.exit()
    if lives < 0:
        game_over()
    screen.fill((0, 0, 0))
    pygame.display.flip()
    clock.tick(60)
"""

GAME_KEYCRASH = """import pygame, sys
# %MARK%
pygame.init()
screen = pygame.display.set_mode((960, 540))
clock = pygame.time.Clock()
speeds = [1, 2, 3]
level = 0
while True:
    for e in pygame.event.get():
        if e.type == pygame.QUIT:
            pygame.quit(); sys.exit()
        if e.type == pygame.KEYDOWN and e.key == pygame.K_RIGHT:
            level += 5
            speed = speeds[level]
    screen.fill((0, 0, 0))
    pygame.display.flip()
    clock.tick(60)
"""

GAME_LOADCRASH = """import pygame, sys
# %MARK% %NOTE%
pygame.init()
screen = pygame.display.set_mode((960, 540))
colors = [(1, 2, 3), (4, 5, 6)]
background = colors[7]
while True:
    for e in pygame.event.get():
        if e.type == pygame.QUIT:
            pygame.quit(); sys.exit()
    screen.fill(background)
    pygame.display.flip()
"""


def bump(key):
    COUNT[key] = COUNT.get(key, 0) + 1
    return COUNT[key]


def app_reply(system, user):
    for mark in ("[js-3rounds]", "[js-identical]", "[evolve-regress]", "[runtime-click]"):
        if mark in user:
            break
    else:
        mark = ""
    repair = "self-repair daemon" in system
    if mark == "[js-3rounds]":
        if not repair:
            return APP_BROKEN_JS.replace("%NOTE%", "first").replace("%MARK%", mark)
        n = bump(mark)
        return APP_OK.replace("%MARK%", mark) if n >= 3 else APP_BROKEN_JS.replace("%NOTE%", f"repair {n}").replace("%MARK%", mark)
    if mark == "[js-identical]":
        if repair and "IDENTICAL" in user:
            return APP_OK.replace("%MARK%", mark)
        return APP_BROKEN_JS.replace("%NOTE%", "same").replace("%MARK%", mark)
    if mark == "[evolve-regress]":
        if not repair and "PREVIOUS VERSION" not in user:
            return APP_OK.replace("%MARK%", mark)
        return APP_BROKEN_JS.replace("%NOTE%", f"evolution {bump(mark)}").replace("%MARK%", mark)
    if mark == "[runtime-click]":
        if repair and "runtime JavaScript error" in user:
            return APP_OK.replace("%MARK%", mark)
        return APP_RUNTIME_CLICK.replace("%MARK%", mark)
    return APP_OK.replace("%MARK%", "")


def game_reply(system, user):
    debugger = "debugger" in system
    for mark in ("[undefined-name]", "[crash-on-key]", "[identical]", "[never-fixed]"):
        if mark in user:
            break
    else:
        return GAME_OK.replace("%MARK%", "")
    ok = GAME_OK.replace("%MARK%", mark)
    if mark == "[undefined-name]":
        return ok if debugger and "undefined name" in user else GAME_UNDEFINED.replace("%MARK%", mark)
    if mark == "[crash-on-key]":
        return ok if debugger and "TRACEBACK:" in user and "KEYDOWN right" in user else GAME_KEYCRASH.replace("%MARK%", mark)
    if mark == "[identical]":
        if debugger and "IDENTICAL" in user:
            return ok
        return GAME_LOADCRASH.replace("%MARK%", mark).replace("%NOTE%", "")
    if mark == "[never-fixed]":
        if not debugger and "PREVIOUS VERSION SOURCE" not in user:
            return ok
        return GAME_LOADCRASH.replace("%MARK%", mark).replace("%NOTE%", f"try {bump(mark)}")
    return ok


def pick(payload):
    msgs = payload.get("messages", [])
    system = msgs[0]["content"] if msgs and msgs[0].get("role") == "system" else ""
    user = msgs[-1].get("content") if msgs else ""
    user = user if isinstance(user, str) else json.dumps(user)
    if "app designer" in system:
        return "PURPOSE: mock spec. FEATURES: a list. LAYOUT: one panel. DATA & APIS: none."
    if "game forge" in system:
        return game_reply(system, user)
    if "self-repair daemon" in system or system.startswith("You are the kAIrOS kernel"):
        return app_reply(system, user)
    return "ok"


async def chat(request):
    payload = await request.json()
    text = pick(payload)
    if os.environ.get("MOCK_FORGE_MSGLOG"):
        msgs = payload.get("messages", [])
        with open(os.environ["MOCK_FORGE_MSGLOG"], "a") as f:
            f.write(json.dumps({"system": (msgs[0].get("content") or "")[:80] if msgs else "",
                                "user": msgs[-1].get("content") if msgs and isinstance(msgs[-1].get("content"), str) else "",
                                "temperature": payload.get("temperature"), "reply": text[:200]}) + "\n")
    if not payload.get("stream"):
        return web.json_response({"choices": [{"message": {"content": text}, "finish_reason": "stop"}]})
    resp = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
    await resp.prepare(request)
    for i in range(0, len(text), 64):
        await resp.write(f"data: {json.dumps({'choices': [{'delta': {'content': text[i:i + 64]}}]})}\n\n".encode())
        await asyncio.sleep(0.005)
    await resp.write(b"data: [DONE]\n\n")
    await resp.write_eof()
    return resp


app = web.Application()
app.router.add_get("/health", lambda r: web.json_response({"status": "ok"}))
app.router.add_post("/v1/chat/completions", chat)
web.run_app(app, host="127.0.0.1", port=PORT, print=None)
