#!/usr/bin/env python3
"""kAIrOS — parallel LLM requests [parallel-2026-10-06]: the GPU scheduler (kernel/gpusched.py),
the `parallel` manifest key (ModelManager.llm_slots / llama_serve_args) and the kernel end to end.

Standalone: `python3 tests/test_llm_parallel.py` (run-tests.sh runs it after the suite). Part A
(scheduler semantics) needs only asyncio; parts B (kernel units) and C (a kernel booted against
this file's own mock llama-server + the suite's mock sd-cli, ports 1343/8087) need aiohttp — in
the testrig container. The embedded mock (`--mock-llama`, exec'd by the kernel as llama-server)
honours `[hold Ns]` in a prompt, counts in-flight requests against its -np and logs every launch
and request: the proof that the kernel sends N requests at once and never N+1.
Writes tests/results-llm-parallel.json.
"""
import asyncio
import contextlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RESULTS = {}
KPORT, LPORT = 1343, 8087          # the suite uses 1338-1340 / 8081-8083, 8085, 8090 (kiwix); in-process: 8094-8096
BASE = f"http://127.0.0.1:{KPORT}"


# ----------------------------------------------------------------------------- the mock engine
def mock_llama_main(argv):
    """An OpenAI-compatible llama-server stand-in with the evidence this test needs.
    Logs under $PAR_MOCK_LOG: launches.log (time + argv), requests.log (one JSON line per
    request: t0, t1, inflight at start, stream, prompt head), inflight.max. `[hold Ns]` in the
    last user message delays the answer (non-stream) or paces the stream over N seconds."""
    from aiohttp import web
    port, np = 8081, 1
    for i, a in enumerate(argv):
        if a == "--port":
            port = int(argv[i + 1])
        if a in ("-np", "--parallel"):
            np = int(argv[i + 1])
    logdir = Path(os.environ.get("PAR_MOCK_LOG", "/tmp"))
    logdir.mkdir(parents=True, exist_ok=True)
    with open(logdir / "launches.log", "a") as f:
        f.write(json.dumps({"t": time.time(), "argv": argv}) + "\n")
    # a real engine listens only after the model is loaded: PAR_MOCK_START_DELAY seconds before
    # binding, so a holder that sends before /health answered hits a closed port (rule b)
    time.sleep(float(os.environ.get("PAR_MOCK_START_DELAY", "0")))
    state = {"inflight": 0, "max": 0}

    def reply(payload):
        msgs = payload["messages"]
        system = msgs[0]["content"] if msgs and msgs[0].get("role") == "system" else ""
        last = msgs[-1].get("content")
        last = last if isinstance(last, str) else json.dumps(last)[:200]
        if "vibeOS kernel" in system or "HTML document" in system:
            return ("<!DOCTYPE html>\n<html><head><style>body{background:#0a0a14;color:#eee}</style></head>"
                    "<body><h1>parallel mock app</h1><div id=\"n\">0</div>"
                    "<button onclick=\"document.getElementById('n').textContent='clicked'\">go</button>"
                    "</body></html>")
        if system.startswith("You are the kAIrOS app designer"):
            return "PURPOSE: parallel mock spec. FEATURES: one button. LAYOUT: one panel."
        return (f"mock answer to: {last[:60]!r} — a complete sentence of filler so the prompt expander's "
                f"twenty-character floor is met.")

    async def chat(request):
        payload = await request.json()
        msgs = payload.get("messages", [])
        last = msgs[-1].get("content") if msgs else ""
        last = last if isinstance(last, str) else ""
        m = re.search(r"\[hold (\d+(?:\.\d+)?)s\]", last)
        hold = float(m.group(1)) if m else 0.0
        if str(port) == os.environ.get("VIBEOS_TRANSLATE_PORT"):
            hold = 0.0                        # the R71 fast MT hop (english_text) answers at once: holds are for the GPU server
        rec = {"t0": time.time(), "inflight_at_start": state["inflight"], "stream": bool(payload.get("stream")),
               "prompt": last[:60], "np": np}
        state["inflight"] += 1
        state["max"] = max(state["max"], state["inflight"])
        (logdir / "inflight.max").write_text(str(state["max"]))
        if state["inflight"] > np:
            with open(logdir / "overflow.log", "a") as f:
                f.write(json.dumps({"t": time.time(), "inflight": state["inflight"], "np": np}) + "\n")
        try:
            text = reply(payload)
            if not payload.get("stream"):
                await asyncio.sleep(hold)
                return web.json_response({"choices": [{"message": {"content": text}}],
                                          "usage": {"completion_tokens": len(text.split())}})
            resp = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
            await resp.prepare(request)
            chunks = [text[i:i + 32] for i in range(0, len(text), 32)]
            pace = hold / len(chunks) if hold else 0.01
            for c in chunks:
                await resp.write(f"data: {json.dumps({'choices': [{'delta': {'content': c}}]})}\n\n".encode())
                await asyncio.sleep(pace)
            await resp.write(b"data: [DONE]\n\n")
            await resp.write_eof()
            return resp
        finally:
            state["inflight"] -= 1
            rec["t1"] = time.time()
            with open(logdir / "requests.log", "a") as f:
                f.write(json.dumps(rec) + "\n")

    async def on_startup(_app):              # bound.log: from here on the port answers — a request
        with open(logdir / "bound.log", "a") as f:   # logged before this time was sent into the gap
            f.write(json.dumps({"t": time.time(), "port": port}) + "\n")

    app = web.Application()
    app.on_startup.append(on_startup)
    app.router.add_get("/health", lambda r: web.json_response({"status": "ok"}))
    app.router.add_post("/v1/chat/completions", chat)
    web.run_app(app, host="127.0.0.1", port=port, print=None)


# ----------------------------------------------------------------------------- the runner
def record(name, reqs, status, detail=""):
    RESULTS[name] = {"reqs": reqs, "status": status, "detail": detail[:300]}
    mark = {"pass": "✓", "fail": "✗", "skip": "~"}[status]
    print(f"  {mark} {name} [{', '.join(reqs)}] {detail[:140]}", flush=True)


class SkipTest(Exception):
    pass


TESTS = []


def run_test(name, reqs):
    def deco(fn):
        async def wrapper(ctx):
            try:
                detail = await asyncio.wait_for(fn(ctx), timeout=120)
                record(name, reqs, "pass", detail or "")
            except SkipTest as e:
                record(name, reqs, "skip", str(e))
            except Exception as e:
                record(name, reqs, "fail", f"{type(e).__name__}: {e}")
        wrapper._name = name
        TESTS.append(wrapper)
        return wrapper
    return deco


def sched_module():
    sys.path.insert(0, str(REPO / "kernel"))
    import gpusched
    return gpusched


# ============================================================================= A. the scheduler
@run_test("sched_concurrency_limit", ["R72"])
async def t_sched_limit(_):
    """slots=2: of 6 shared holders on one key at most 2 run at once; all complete; nothing stays held."""
    s = sched_module().GpuScheduler()
    peak, running, done = [0], [0], []

    async def hold(i):
        async with s.shared("llm", 2):
            running[0] += 1
            peak[0] = max(peak[0], running[0])
            assert running[0] <= s.shared_holders <= 2 and s.shared_key == "llm", (running[0], s.shared_holders)
            await asyncio.sleep(0.03)
            running[0] -= 1
            done.append(i)
    await asyncio.gather(*(hold(i) for i in range(6)))
    assert peak[0] == 2, peak
    assert sorted(done) == list(range(6)) and not s.locked() and s.waiting == 0 and s.shared_key is None
    return f"6 holders, peak concurrency {peak[0]} (slots 2), released clean"


@run_test("sched_fifo_no_starvation", ["R72"])
async def t_sched_fifo(_):
    """A steady stream of same-key requests keeps arriving (always a free slot soon); an exclusive
    request and a request for another key queued in between are served in arrival order —
    before every shared request that arrived after them — and the exclusive waits only for the
    running pair to drain, never for the stream to end."""
    s = sched_module().GpuScheduler()
    order, submitted = [], []
    t0 = time.monotonic()

    async def shared(name, key):
        submitted.append(name)
        async with s.shared(key, 2):
            order.append(name)
            await asyncio.sleep(0.04)

    async def excl(name):
        submitted.append(name)
        async with s.exclusive():
            order.append((name, time.monotonic() - t0))
            await asyncio.sleep(0.02)

    tasks = []
    for i in range(20):                       # a new "llm" request every 10 ms for 200 ms
        tasks.append(asyncio.create_task(shared(f"llm{i}", "llm")))
        if i == 4:
            tasks.append(asyncio.create_task(excl("IMG")))
        if i == 5:
            tasks.append(asyncio.create_task(shared("VIS", "vision")))
        await asyncio.sleep(0.01)
    await asyncio.gather(*tasks)
    names = [o[0] if isinstance(o, tuple) else o for o in order]
    img_i, vis_i = names.index("IMG"), names.index("VIS")
    after_img = [n for n in submitted[submitted.index("IMG") + 1:] if n.startswith("llm")]
    for n in after_img:
        assert names.index(n) > img_i, f"{n} (submitted after IMG) ran before it: {names}"
    after_vis = [n for n in submitted[submitted.index("VIS") + 1:] if n.startswith("llm")]
    for n in after_vis:
        assert names.index(n) > vis_i, f"{n} (submitted after VIS) ran before it: {names}"
    assert vis_i > img_i
    img_t = [o for o in order if isinstance(o, tuple)][0][1]
    assert img_t < 0.05 + 0.08 + 0.3, f"IMG waited {img_t:.3f} s — starved behind the stream"
    assert not s.locked() and s.waiting == 0
    return f"20 llm + IMG + VIS: IMG ran at #{img_i} after {img_t * 1000:.0f} ms, VIS at #{vis_i}, FIFO held"


@run_test("sched_consecutive_same_key_start_together", ["R72"])
async def t_sched_group_start(_):
    """Queue [exclusive, llm, llm, llm, vision] behind a running llm pair (slots 4): once the
    exclusive leaves, the three llm requests start TOGETHER; vision waits for them to drain."""
    s = sched_module().GpuScheduler()
    gate = asyncio.Event()
    probe = {"after_excl": None, "vision_saw": None}

    async def runner(key, hold=0.03):
        async with s.shared(key, 4):
            if key == "vision":
                probe["vision_saw"] = (s.shared_holders, s.shared_key)
            if hold is None:
                await gate.wait()
            else:
                await asyncio.sleep(hold)

    async def excl():
        async with s.exclusive():
            await asyncio.sleep(0.01)
        await asyncio.sleep(0)            # let the dispatch's grants run
        probe["after_excl"] = (s.shared_holders, s.shared_key)

    pair = [asyncio.create_task(runner("llm", None)), asyncio.create_task(runner("llm", None))]
    await asyncio.sleep(0.005)
    tasks = [asyncio.create_task(excl())] + [asyncio.create_task(runner("llm")) for _ in range(3)] \
        + [asyncio.create_task(runner("vision"))]
    await asyncio.sleep(0.005)
    assert s.shared_holders == 2 and s.waiting == 5
    gate.set()
    await asyncio.gather(*pair, *tasks)
    assert probe["after_excl"] == (3, "llm"), probe
    assert probe["vision_saw"] == (1, "vision"), probe
    return f"3 llm started together after the exclusive ({probe['after_excl']}), vision alone after the drain"


@run_test("sched_swap_only_after_drain", ["R72"])
async def t_sched_swap(_):
    """A request for another key never joins a running group: it starts when the group's last
    holder has left, becomes the group, and a request for the first key then waits for it."""
    s = sched_module().GpuScheduler()
    seen = {}

    async def hold(name, key, t):
        async with s.shared(key, 4):
            seen[name] = (time.monotonic(), s.shared_key, s.shared_holders)
            await asyncio.sleep(t)
    a = [asyncio.create_task(hold("a1", "llm", 0.05)), asyncio.create_task(hold("a2", "llm", 0.08))]
    await asyncio.sleep(0.01)
    v = asyncio.create_task(hold("v", "vision", 0.05))
    await asyncio.sleep(0.01)
    assert s.shared_key == "llm" and s.waiting == 1 and "v" not in seen
    b = asyncio.create_task(hold("b", "llm", 0.01))
    await asyncio.gather(*a, v, b)
    assert seen["v"][1] == "vision" and seen["v"][2] == 1
    assert seen["v"][0] >= seen["a2"][0] + 0.08 - 0.005, "vision started before the llm group drained"
    assert seen["b"][0] >= seen["v"][0] + 0.05 - 0.005, "llm rejoined before vision drained"
    return "vision waited for both llm holders; the later llm request waited for vision"


@run_test("sched_cancelled_waiter_leaves_queue", ["R72"])
async def t_sched_cancel_waiter(_):
    """Queue [exclusive, llm2] behind one llm holder (slots 2): cancelling the exclusive while it
    waits drops it from the queue and wakes llm2 right away (a free slot, nobody ahead)."""
    s = sched_module().GpuScheduler()
    gate = asyncio.Event()
    started = {}

    async def hold(name):
        async with s.shared("llm", 2):
            started[name] = time.monotonic()
            await gate.wait()

    async def excl():
        async with s.exclusive():
            started["X"] = time.monotonic()
    h1 = asyncio.create_task(hold("h1"))
    await asyncio.sleep(0.005)
    x = asyncio.create_task(excl())
    await asyncio.sleep(0.005)
    h2 = asyncio.create_task(hold("h2"))
    await asyncio.sleep(0.005)
    assert s.waiting == 2 and "h2" not in started
    x.cancel()
    await asyncio.sleep(0.005)
    assert x.cancelled() and "X" not in started and s.waiting == 0, (s.waiting, started)
    assert "h2" in started and s.shared_holders == 2, "llm2 was not woken by the cancellation"
    gate.set()
    await asyncio.gather(h1, h2)
    assert not s.locked()
    return "cancelled exclusive left the queue; the shared request behind it joined at once"


@run_test("sched_release_on_error_and_cancel", ["R72"])
async def t_sched_release(_):
    """A holder that raises, and a holder cancelled mid-hold, both release; the next waiter starts;
    `async with sched:` (asyncio.Lock compatibility) is the exclusive hold."""
    s = sched_module().GpuScheduler()

    async def bad():
        async with s.shared("llm", 1):
            raise RuntimeError("engine died")
    try:
        await bad()
    except RuntimeError:
        pass
    assert not s.locked() and s.shared_holders == 0
    gate = asyncio.Event()

    async def stuck():
        async with s.shared("llm", 1):
            await gate.wait()
    t = asyncio.create_task(stuck())
    await asyncio.sleep(0.005)
    excl_cm = s.exclusive()                        # keep the context manager alive: a dropped one is GC-closed
    nxt = asyncio.create_task(excl_cm.__aenter__())
    await asyncio.sleep(0.005)
    assert s.waiting == 1 and s.locked()
    t.cancel()
    await asyncio.gather(t, return_exceptions=True)
    await asyncio.sleep(0.005)
    assert nxt.done() and s.locked() and s.shared_holders == 0, "the exclusive did not start after the cancel"
    await excl_cm.__aexit__(None, None, None)
    assert not s.locked()
    async with s:                                  # the asyncio.Lock spelling
        assert s.locked() and s.shared_holders == 0
        sh_cm = s.shared("llm", 2)
        w = asyncio.create_task(sh_cm.__aenter__())
        await asyncio.sleep(0.005)
        assert not w.done() and s.waiting == 1
    await w
    assert s.shared_holders == 1
    await sh_cm.__aexit__(None, None, None)
    assert not s.locked() and s.waiting == 0
    return "error → released; cancel mid-hold → released, exclusive started; async-with = exclusive"


@run_test("sched_grant_and_cancel_same_step", ["R72"])
async def t_sched_grant_cancel_race(_):
    """The race: the waiter is granted (its future resolved) and its task cancelled in the same
    loop step. The grant is handed back — nothing stays held, the next request starts."""
    s = sched_module().GpuScheduler()
    ev = asyncio.Event()

    async def h1():
        async with s.shared("llm", 1):
            await ev.wait()

    async def w():
        async with s.shared("llm", 1):
            raise AssertionError("the cancelled waiter must never run its body")
    t1 = asyncio.create_task(h1())
    await asyncio.sleep(0.005)
    tw = asyncio.create_task(w())
    await asyncio.sleep(0.005)
    assert s.waiting == 1
    ev.set()                      # h1 releases on its next step and grants w …
    await asyncio.sleep(0)        # … (h1 runs now) …
    assert tw._fut_waiter is not None and tw._fut_waiter.done() and not tw.done(), "race not reproduced"
    tw.cancel()                   # … and w is cancelled before it ever resumes
    await asyncio.gather(t1, tw, return_exceptions=True)
    assert tw.cancelled(), tw
    assert not s.locked() and s.shared_holders == 0 and s.waiting == 0, "the lost grant was not handed back"
    async with s.shared("llm", 1):
        pass
    return "granted-then-cancelled waiter handed its slot back; scheduler free"


@run_test("sched_drained_hook_on_release_and_handback", ["R72"])
async def t_sched_drained_hook(_):
    """[verify 2026-10-07, finding 1] on_shared_drained fires exactly when a shared release or a
    handed-back grant leaves 0 shared holders, after the dispatch: silent while a granted waiter
    still counts (two holders leave in one step, the first release grants the waiter), once when
    that waiter is cancelled before it runs and hands the grant back, once per plain drain, never
    for an exclusive hold."""
    fired = []
    s = sched_module().GpuScheduler(on_shared_drained=lambda: fired.append(s.shared_holders))
    ev = asyncio.Event()

    async def hold():
        async with s.shared("llm", 2):
            await ev.wait()
    h1, h2 = asyncio.create_task(hold()), asyncio.create_task(hold())
    await asyncio.sleep(0.005)
    w = asyncio.create_task(hold())
    await asyncio.sleep(0.005)
    assert s.shared_holders == 2 and s.waiting == 1 and fired == []
    ev.set()                      # h1 and h2 leave in the same step; h1's release grants w
    await asyncio.sleep(0)
    assert w._fut_waiter is not None and w._fut_waiter.done() and not w.done(), "race not reproduced"
    assert fired == [] and s.shared_holders == 1, f"hook fired while the granted waiter counted: {fired}"
    w.cancel()
    await asyncio.gather(h1, h2, w, return_exceptions=True)
    assert w.cancelled() and not s.locked() and fired == [0], (fired, s.shared_holders)
    async with s.exclusive():
        pass
    assert fired == [0], "fired for an exclusive hold"
    async with s.shared("llm", 1):
        pass
    assert fired == [0, 0]
    return "hook: silent while the granted waiter counted, once on its handback, once per drain, never for exclusive"


def llm_fast_fixture() -> dict:
    """[R89] the `llm_fast` section the 8/16 GB tiers carried until 2026-10-18 (tests/fixture-llm-fast.json): fast mode
    left the shipped manifests, the kernel keeps the mechanism — this file's kernels get it injected"""
    return json.loads((REPO / "tests" / "fixture-llm-fast.json").read_text(encoding="utf-8"))["llm_fast"]


# ============================================================================= B. kernel units
def kernel():
    sys.path.insert(0, str(REPO / "kernel"))
    import server
    return server


@run_test("unit_parallel_argv_absent_is_today", ["R72"])
async def t_argv_today(ctx):
    """Every shipped section without `parallel` (and with parallel 1) gives EXACTLY the old argv."""
    server = kernel()
    checked = 0
    for mf in sorted((REPO / "manifests").glob("tier-*.json")):
        m = json.loads(mf.read_text())
        for key in ("llm", "llm_fast", "python_llm", "translation"):
            spec = m.get(key)
            if not isinstance(spec, dict) or "serve_args" not in spec:
                continue
            spec = {k: v for k, v in spec.items() if k != "parallel"}
            assert server.MM.llama_serve_args(spec) == spec["serve_args"], (mf.name, key)
            assert server.MM.llm_slots(spec) == 1
            assert server.MM.llama_serve_args({**spec, "parallel": 1}) == spec["serve_args"]
            checked += 1
        v = server.vision_llm_spec(m)
        if v:
            v.pop("parallel", None)
            assert server.MM.llama_serve_args(v) == v["serve_args"] and server.MM.llm_slots(v) == 1
            checked += 1
        e = server.expander_llm_spec("image", m)
        assert e["parallel"] == m["image"].get("expander_parallel", server.EXPAND_PARALLEL) \
            and server.EXPAND_SERVE_ARGS == ["-ngl", "99", "-c", "8192", "--jinja"]
        assert server.MM.llama_serve_args({**e, "parallel": 1}) == server.EXPAND_SERVE_ARGS
        checked += 1
    assert checked >= 12, checked
    return f"{checked} sections across the tier manifests: argv unchanged without parallel / with parallel 1"


@run_test("unit_parallel_argv_n_slots", ["R72"])
async def t_argv_n(ctx):
    """parallel N: -c becomes N*C in place, `-np N --no-kv-unified` is appended, nothing else moves;
    --ctx-size works too; junk values and a section without -c fall back to 1 slot."""
    server = kernel()
    spec = {"serve_args": ["-c", "16384", "-fa", "on", "--jinja", "--reasoning", "off"], "parallel": 3}
    out = server.MM.llama_serve_args(spec)
    assert out == ["-c", "49152", "-fa", "on", "--jinja", "--reasoning", "off", "-np", "3", "--no-kv-unified"], out
    assert out.count("-c") == 1 and server.MM.llm_slots(spec) == 3
    spec2 = {"serve_args": ["-ngl", "99", "--ctx-size", "8192", "--jinja"], "parallel": "2"}
    assert server.MM.llama_serve_args(spec2) == ["-ngl", "99", "--ctx-size", "16384", "--jinja", "-np", "2", "--no-kv-unified"]
    for junk in (0, -1, "x", None, 1.0):
        s = {"serve_args": ["-c", "4096"], "parallel": junk}
        assert server.MM.llm_slots(s) == 1 and server.MM.llama_serve_args(s) == ["-c", "4096"], junk
    noctx = {"serve_args": ["-ngl", "99", "--jinja"], "parallel": 4}
    assert server.MM.llm_slots(noctx) == 1 and server.MM.llama_serve_args(noctx) == ["-ngl", "99", "--jinja"]
    return "N*C in place + -np N --no-kv-unified; --ctx-size ok; junk / no -c → 1 slot"


@run_test("unit_parallel_budget_stays_per_request", ["R72", "R43"])
async def t_budget(ctx):
    """llm_ctx() (the prompt budget) keeps reading the manifest's C, not N*C."""
    server = kernel()
    sec = server.MM.llm_spec("llm")[1]
    before = server.llm_ctx()
    old = sec.get("parallel")
    sec["parallel"] = 4
    try:
        assert server.llm_ctx() == before, (server.llm_ctx(), before)
        assert server.MM.llama_serve_args(sec)[server.MM.llama_serve_args(sec).index("-c") + 1] == str(4 * before)
        assert server.prompt_char_budget("x") == max(4000, (before - server.OUT_RESERVE_TOKENS) * server.CHARS_PER_TOKEN - 1)
    finally:
        if old is None:
            sec.pop("parallel", None)
        else:
            sec["parallel"] = old
    return f"llm_ctx() = {before} with parallel 4 (server -c {4 * before})"


@run_test("unit_parallel_expander_vision_keys", ["R72"])
async def t_specs(ctx):
    """The expander takes EXPAND_PARALLEL / the image section's expander_parallel; vision its own
    `parallel`; slots_for() never raises (1 for a key that does not resolve)."""
    server = kernel()
    m = json.loads((REPO / "manifests" / "tier-8gb.json").read_text())
    assert server.expander_llm_spec("image", m)["parallel"] == m["image"]["expander_parallel"] == 2     # shipped [§6]
    assert server.expander_llm_spec("image_classic", m)["parallel"] == server.EXPAND_PARALLEL == 1    # no override
    m2 = json.loads(json.dumps(m))
    m2["image"]["expander_parallel"] = 2
    m2["vision"]["parallel"] = 2
    assert server.expander_llm_spec("image", m2)["parallel"] == 2 and server.MM.llm_slots(server.expander_llm_spec("image", m2)) == 2
    assert server.vision_llm_spec(m2)["parallel"] == 2 and server.MM.llm_slots(server.vision_llm_spec(m2)) == 2
    assert server.vision_llm_spec(m).get("parallel", 1) == 1
    assert server.MM.slots_for("expander:nonsense") == 1          # no such section: resolves to nothing → 1, no raise
    assert server.MM.slots_for("no-such-key") == server.MM.slots_for("llm")   # llm_spec's fallback section, as ever
    assert server.MM.slots_for("llm") == server.MM.llm_slots(server.MM.llm_spec("llm")[1])
    return "expander_parallel / vision.parallel read; slots_for safe"


@run_test("unit_parallel_gpu_validated_values", ["R72"])
async def t_gpu_values(ctx):
    """[GPU validation 2026-10-07, docs/PARALLEL-LLM-2026-10-06.md §5/§6] the shipped values: 8/16 GB
    llm at 2 slots with its host-RAM caps (--cache-ram 4096, --ctx-checkpoints 8; [R89] no llm_fast in any
    tier or test variant any more — the fixture keeps its 2048 cap), the Muse (Krea 2 family) expanders
    (Qwen3VL-4B) at 2 slots in every tier, vision and the other expanders at 1; the derived argv keeps the
    caps and gives every slot the section's full context."""
    server = kernel()
    caps = {"llm": ["--cache-ram", "4096", "--ctx-checkpoints", "8"]}
    n = 0
    for t in ("8gb", "16gb", "24gb", "32gb"):
        m = json.loads((REPO / "manifests" / f"tier-{t}.json").read_text())
        for s in ("image", "image_safe"):          # [R76] image_safe replaced the Kroma v0.2 add-on; both = Muse since 2026-10-10
            assert "Qwen3VL-4B-Instruct" in m[s]["text_encoder"]["file"] and m[s].get("expander_parallel") == 2, (t, s)
            e = server.expander_llm_spec(s, m)
            assert server.MM.llama_serve_args(e) == ["-ngl", "99", "-c", "16384", "--jinja", "-np", "2", "--no-kv-unified"], (t, s)
            n += 1
        for s in ("image_classic", "image_qwen"):
            assert "expander_parallel" not in m[s] and server.expander_llm_spec(s, m)["parallel"] == 1, (t, s)
        v = server.vision_llm_spec(m)
        assert v and server.MM.llm_slots(v) == 1 and "--cache-ram" not in v["serve_args"], t
        if t in ("8gb", "16gb"):
            for key, c in caps.items():
                sec = m[key]
                sa = sec["serve_args"]
                assert sec["parallel"] == 2 and sa[sa.index("--cache-ram"):sa.index("--cache-ram") + 4] == c, (t, key, sa)
                a = server.MM.llama_serve_args(sec)
                assert a[a.index("-c") + 1] == "32768" and a[-3:] == ["-np", "2", "--no-kv-unified"] \
                    and a[a.index("--cache-ram"):a.index("--cache-ram") + 4] == c, (t, key, a)
                n += 1
        else:
            assert m["llm"].get("parallel", 1) == 1, t
        assert "llm_fast" not in m, f"{t}: fast mode dropped [R89]"
    for vf in sorted((REPO / "manifests" / "test").glob("tier-8gb-*.json")):
        mv = json.loads(vf.read_text())
        assert mv["image"].get("expander_parallel") == 2 and "llm_fast" not in mv, vf.name
    fs = llm_fast_fixture()["serve_args"]
    assert llm_fast_fixture()["parallel"] == 2 and fs[fs.index("--cache-ram") + 1] == "2048"
    return f"{n} sections carry the GPU-validated values; derived argv keeps the caps, every slot its full context"


@run_test("unit_parallel_ensure_llama_single_flight", ["R72"])
async def t_single_flight(ctx):
    """Four concurrent ensure_llama() calls for one key start the server ONCE; a different key
    afterwards starts it once more (the swap); gpu_lock is the scheduler and `async with` on it
    is exclusive."""
    server = kernel()
    assert type(server.MM.gpu_lock).__name__ == "GpuScheduler"
    calls = []

    class FakeProc:
        returncode = None

    async def fake_start(key, spec):
        calls.append(key)
        server.MM.llama_ready = False
        await asyncio.sleep(0.05)
        server.MM.llama_proc = FakeProc()
        server.MM.llama_model_key = key
        server.MM.llama_ready = True             # what the real start sets once /health answered
    orig = server.MM._start_llama
    server.MM._start_llama = fake_start
    try:
        server.MM.llama_proc, server.MM.llama_model_key, server.MM.llama_ready = None, None, False
        await asyncio.gather(*(server.MM.ensure_llama("llm") for _ in range(4)))
        assert calls == ["llm"], calls
        await asyncio.gather(*(server.MM.ensure_llama("expander:image") for _ in range(3)))
        assert len(calls) == 2 and calls[1].startswith("expander:"), calls
        await server.MM.ensure_llama("expander:image")
        assert len(calls) == 2
    finally:
        server.MM._start_llama = orig
        server.MM.llama_proc, server.MM.llama_model_key, server.MM.llama_ready = None, None, False
    async with server.MM.gpu_lock:
        assert server.MM.gpu_lock.locked()
        cm = server.MM.gpu_lock.shared("llm", 2)
        w = asyncio.create_task(cm.__aenter__())
        await asyncio.sleep(0.005)
        assert not w.done()
    await w
    await cm.__aexit__(None, None, None)
    assert not server.MM.gpu_lock.locked()
    return f"starts: {calls}; gpu_lock exclusive blocks a shared hold"


def k_inproc_log(name):
    p = Path(os.environ["PAR_MOCK_LOG"]) / name
    return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []


def k_inproc_launches():
    return k_inproc_log("launches.log")


def with_settings(server, **changes):
    """Write settings (the in-process kernel and the :KPORT kernel share the VIBEOS_DIR, so the
    caller restores them); returns the previous values of the changed keys."""
    s = server.load_settings()
    before = {k: s.get(k) for k in changes}
    s.update(changes)
    server.save_settings(s)
    return before


@run_test("unit_parallel_mid_load_wait_no_send_before_health", ["R72"])
async def t_mid_load_wait(ctx):
    """kairos-e3's stick fix under concurrency: the holder that started the server is cancelled
    while it loads (a closed window); the server keeps loading (its process stays). The next
    holder must NOT start a second server (one launch) and must NOT send into the gap ("503
    Loading model" / connection refused): it resumes the wait for /health, then answers. The
    mock binds 0.8 s after its launch (PAR_MOCK_START_DELAY) and logs the moment in bound.log —
    every request it saw came after that."""
    server = kernel()
    MM = server.MM
    await MM.stop_llama(grace=3)
    n0 = len(k_inproc_launches())
    starter = asyncio.create_task(MM.chat("sys", [{"role": "user", "content": "[hold 0s] starter"}], max_tokens=16))
    await asyncio.sleep(0.25)                    # it has spawned the server and sits in wait_llama_health
    assert MM.llama_proc is not None and not MM.llama_ready and MM.gpu_lock.shared_holders == 1
    starter.cancel()
    await asyncio.gather(starter, return_exceptions=True)
    assert starter.cancelled() and MM.gpu_lock.shared_holders == 0 and MM.state == "idle"
    assert MM.llama_proc is not None and MM.llama_proc.returncode is None and not MM.llama_ready, "the loading server must keep going"
    t0 = time.time()
    a, b = await asyncio.gather(MM.chat("sys", [{"role": "user", "content": "[hold 0s] next a"}], max_tokens=16),
                                MM.chat("sys", [{"role": "user", "content": "[hold 0s] next b"}], max_tokens=16))
    waited = time.time() - t0
    assert a.startswith("mock answer") and b.startswith("mock answer")
    la = k_inproc_launches()
    assert len(la) == n0 + 1, f"a second server was started: {[l['argv'][-3:] for l in la[n0:]]}"
    bound = [b_ for b_ in k_inproc_log("bound.log") if b_["t"] >= la[-1]["t"]]
    reqs = [r for r in k_inproc_log("requests.log") if r["prompt"].startswith("[hold 0s] next")]
    assert bound and len(reqs) == 2 and min(r["t0"] for r in reqs) >= bound[-1]["t"], (bound, reqs)
    assert MM.llama_ready and MM.gpu_lock.shared_holders == 0 and MM.state == "idle"
    await MM.stop_llama(grace=3)
    return (f"starter cancelled mid-load; the next pair waited {waited:.2f} s for /health, one launch, "
            f"first request {min(r['t0'] for r in reqs) - bound[-1]['t']:.2f} s after the server bound")


@run_test("unit_parallel_choice_change_while_queued", ["R72"])
async def t_choice_change_while_queued(ctx):
    """The race the shared hold must survive: a request for "llm" queues behind a full group
    (Gemma, 2 slots); while it waits the user switches llm_model to fast. When a slot frees it
    must NOT start the coder server under the other running Gemma request — it re-resolves,
    re-queues under llm_fast, and starts it only after the Gemma group drained; both Gemma
    answers arrive intact."""
    server = kernel()
    MM = server.MM
    await MM.stop_llama(grace=3)
    n0 = len(k_inproc_launches())
    before = with_settings(server, llm_model="default")
    try:
        a1 = asyncio.create_task(MM.chat("sys", [{"role": "user", "content": "[hold 2s] gemma 1"}], max_tokens=16))
        a2 = asyncio.create_task(MM.chat("sys", [{"role": "user", "content": "[hold 3.5s] gemma 2"}], max_tokens=16))
        for _ in range(100):
            if MM.gpu_lock.shared_holders == 2 and MM.llama_ready:
                break
            await asyncio.sleep(0.05)
        assert MM.gpu_lock.shared_holders == 2 and MM.gpu_lock.shared_key == "llm", (MM.gpu_lock.shared_holders, MM.gpu_lock.shared_key)
        b = asyncio.create_task(MM.chat("sys", [{"role": "user", "content": "[hold 0s] after the switch"}], max_tokens=16))
        await asyncio.sleep(0.1)
        assert MM.gpu_lock.waiting == 1
        with_settings(server, llm_model="fast")      # the choice moves while b is queued
        r1, r2, rb = await asyncio.gather(a1, a2, b)
        assert r1.startswith("mock answer") and r2.startswith("mock answer") and rb.startswith("mock answer")
        la = k_inproc_launches()
        assert len(la) == n0 + 2, [l["argv"][-4:] for l in la[n0:]]
        assert "gemma" in " ".join(la[-2]["argv"]).lower() and la[-2]["argv"][-3:] == ["-np", "2", "--no-kv-unified"]
        assert "Qwen2.5-Coder" in " ".join(la[-1]["argv"]) and "-np" not in la[-1]["argv"], la[-1]["argv"]
        reqs = {r["prompt"]: r for r in k_inproc_log("requests.log")}
        g2 = reqs["[hold 3.5s] gemma 2"]
        assert la[-1]["t"] >= g2["t1"] - 0.05, f"the coder server started {g2['t1'] - la[-1]['t']:.2f} s before gemma 2 ended"
        assert g2["t1"] - g2["t0"] >= 3.4, "gemma 2 was cut short"
        assert MM.llama_model_key == "llm_fast" and MM.gpu_lock.shared_holders == 0 and MM.state == "idle"
    finally:
        with_settings(server, **before)
        await MM.stop_llama(grace=3)
    return "queued request re-resolved to llm_fast, waited for the Gemma pair to drain, both Gemma answers intact"


@run_test("unit_parallel_translation_key_swap_and_unload", ["R72", "R71", "R89"])
async def t_translation_swap_unload(ctx):
    """[R71 under R72] MM.chat(key="translation") is one more swap-path key: ONE slot and the
    section's argv unchanged; TranslationService.reconfigure() unloads the quality model under
    an EXCLUSIVE hold — at once when nothing runs (≤ 1 s), and right after the running
    translation group has drained when one does — never under a holder. [R89] The 8 GB tier's
    quality is the loaded LLM (no such key to swap: "translation" resolves to the coder LLM's
    section); the mechanism stays for the 16 GB tier's dedicated 9B, patched in here."""
    server = kernel()
    MM, TR = server.MM, server.TR
    await MM.stop_llama(grace=3)
    assert server.translation_quality_llm() and MM.llm_spec("translation")[0] == "llm", "8 GB: quality = the loaded LLM"
    n0 = len(k_inproc_launches())
    before = with_settings(server, translation="on", language="de", translation_quality="quality")
    saved_q = MM.manifest["translation"]["quality"]
    q16 = json.loads((REPO / "manifests" / "tier-16gb.json").read_text())["translation"]["quality"]
    MM.manifest["translation"]["quality"] = q16                  # the 16 GB tier's dedicated 9B (unchanged by R89)
    (server.MODELS_DIR / q16["file"]).parent.mkdir(parents=True, exist_ok=True)
    (server.MODELS_DIR / q16["file"]).touch()
    try:
        spec = MM.llm_spec("translation")[1]
        assert MM.llm_slots(spec) == 1 and MM.llama_serve_args(spec) == spec["serve_args"] and "-np" not in spec["serve_args"]
        t = await MM.chat(None, [{"role": "user", "content": "[hold 0s] Guten Morgen"}], key="translation", max_tokens=32, temperature=0)
        assert t.startswith("mock answer") and MM.llama_model_key == "translation"
        la = k_inproc_launches()
        assert len(la) == n0 + 1 and "Index-Translate-9B" in la[-1]["argv"][la[-1]["argv"].index("-m") + 1] \
            and "-np" not in la[-1]["argv"] and la[-1]["argv"][la[-1]["argv"].index("-c") + 1] == "4096", la[-1]["argv"]
        # (1) idle: the unload is immediate
        t0 = time.time()
        await TR.reconfigure({**server.load_settings(), "translation": "off"})
        idle_unload = time.time() - t0
        assert MM.llama_model_key is None and MM.llama_proc is None and idle_unload < 1.0, idle_unload
        # (2) a translation request in flight: reconfigure waits for it, then unloads
        with_settings(server, translation="on")
        holder = asyncio.create_task(MM.chat(None, [{"role": "user", "content": "[hold 2s] Hallo Welt"}],
                                             key="translation", max_tokens=32, temperature=0))
        for _ in range(100):
            if MM.gpu_lock.shared_holders == 1 and MM.llama_ready:
                break
            await asyncio.sleep(0.05)
        assert MM.gpu_lock.shared_holders == 1 and MM.gpu_lock.shared_key == "translation"
        t0 = time.time()
        unload = asyncio.create_task(TR.reconfigure({**server.load_settings(), "translation": "off"}))
        await asyncio.sleep(0.3)
        assert MM.llama_model_key == "translation" and MM.gpu_lock.waiting == 1, "the unload ran under the holder"
        answer = await holder
        t_drained = time.time()
        await unload
        t_unloaded = time.time()
        assert answer.startswith("mock answer") and MM.llama_model_key is None and MM.llama_proc is None
        assert t_unloaded - t_drained < 1.0, f"unload took {t_unloaded - t_drained:.2f} s after the drain"
        assert not MM.gpu_lock.locked() and MM.gpu_lock.waiting == 0 and MM.state == "idle"
    finally:
        MM.manifest["translation"]["quality"] = saved_q
        with_settings(server, **before)
        await TR.stop(grace=3)
        await MM.stop_llama(grace=3)
    return (f"translation key (16 GB's 9B patched in; 8 GB = the LLM [R89]): 1 slot, argv unchanged; idle unload "
            f"{idle_unload:.2f} s; busy unload waited for the holder, then {t_unloaded - t_drained:.2f} s")


@run_test("unit_parallel_chat_stream_release_paths", ["R72", "R68"])      # R68's decision D-R68-3: the aclosing() contract
async def t_stream_release(ctx):
    """The real ModelManager against the mock engine: two chat() calls share the server (2 holders,
    state "llm" until the last leaves); a chat_stream() consumer that breaks, one whose task is
    cancelled and one that raises each release their slot AT ONCE through aclosing() — the
    scheduler is free and state "idle" right after, not when the 5 s stream would have ended."""
    import contextlib
    server = kernel()
    MM = server.MM
    assert MM.llm_slots(MM.llm_spec("llm")[1]) == 2, "the test manifest gives llm 2 slots"
    await MM.stop_llama(grace=3)                 # cold: the pair below arrives while the server is starting
    assert not MM._llama_up("llm")
    n0 = len(k_inproc_launches())
    try:
        seen = []

        async def probe():
            for _ in range(30):
                seen.append((MM.gpu_lock.shared_holders, MM.state))
                await asyncio.sleep(0.1)
        pr = asyncio.create_task(probe())
        t0 = time.time()
        a, b = await asyncio.gather(MM.chat("sys", [{"role": "user", "content": "[hold 2s] pair a"}], max_tokens=16),
                                    MM.chat("sys", [{"role": "user", "content": "[hold 2s] pair b"}], max_tokens=16))
        wall = time.time() - t0
        pr.cancel()
        assert a.startswith("mock answer") and b.startswith("mock answer"), "a holder sent before /health answered?"
        assert (2, "llm") in seen and wall < 5.0, (seen, wall)
        assert len(k_inproc_launches()) == n0 + 1, "the pair started two servers"
        assert MM.gpu_lock.shared_holders == 0 and MM.state == "idle"
        # a consumer that stops after the first delta
        t0 = time.time()
        async with contextlib.aclosing(MM.chat_stream("sys", [{"role": "user", "content": "[hold 5s] s1"}])) as g:
            async for _ in g:
                break
        assert MM.gpu_lock.shared_holders == 0 and MM.state == "idle" and time.time() - t0 < 2.5, "break did not release"
        # a consumer task cancelled mid-stream
        got = []

        async def consume():
            async with contextlib.aclosing(MM.chat_stream("sys", [{"role": "user", "content": "[hold 5s] s2"}])) as g:
                async for d in g:
                    got.append(d)
        task = asyncio.create_task(consume())
        for _ in range(100):
            if got:
                break
            await asyncio.sleep(0.05)
        assert got and MM.gpu_lock.shared_holders == 1 and MM.state == "llm"
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        assert task.cancelled() and MM.gpu_lock.shared_holders == 0 and MM.state == "idle", "cancel did not release"
        # a consumer that raises
        try:
            async with contextlib.aclosing(MM.chat_stream("sys", [{"role": "user", "content": "[hold 5s] s3"}])) as g:
                async for _ in g:
                    raise RuntimeError("consumer failed")
        except RuntimeError:
            pass
        assert MM.gpu_lock.shared_holders == 0 and MM.state == "idle" and not MM.gpu_lock.locked(), "error did not release"
        # an exclusive job can take the GPU right away
        async with MM.gpu_lock:
            assert MM.gpu_lock.locked()
    finally:
        await MM.stop_llama(grace=3)
    return "2 chats shared the server (holders 2, state llm→idle); break/cancel/error each freed the slot at once"


# ----------------------------------------------------------------- verification 2026-10-07 (no engine)
class _FakeResp:
    def __init__(self, ev, stream):
        self.ev, self.stream = ev, stream

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def json(self):
        await self.ev.wait()
        return {"choices": [{"message": {"content": "ok"}}]}

    @property
    def content(self):
        return self._lines()

    async def _lines(self):
        await self.ev.wait()
        yield b'data: {"choices":[{"delta":{"content":"ok"}}]}\n'
        yield b"data: [DONE]\n"


class _FakeHTTP:
    """An aiohttp.ClientSession stand-in for the in-process ModelManager (no engine, no port):
    a request's answer waits on `events[prompt]` (prompt = the last user message), `sent[prompt]`
    keeps the body and the ClientTimeout it was posted with."""
    events, sent = {}, {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def post(self, url, json=None, timeout=None):
        prompt = json["messages"][-1]["content"]
        _FakeHTTP.sent[prompt] = (json, timeout)
        return _FakeResp(_FakeHTTP.events.setdefault(prompt, asyncio.Event()), bool(json.get("stream")))


@contextlib.contextmanager
def fake_http(server):
    """For the block: aiohttp.ClientSession is _FakeHTTP and the engine start is skipped (the
    server counts as up); both restored afterwards."""
    import aiohttp

    async def no_start(key, spec):
        return None
    orig = aiohttp.ClientSession
    aiohttp.ClientSession, server.MM._ensure_llama_resolved = _FakeHTTP, no_start
    _FakeHTTP.events.clear()
    _FakeHTTP.sent.clear()
    try:
        yield _FakeHTTP
    finally:
        aiohttp.ClientSession = orig
        del server.MM._ensure_llama_resolved


def fake_chat(server, name, **kw):
    """One MM.chat task whose answer waits on _FakeHTTP.events[name]."""
    _FakeHTTP.events[name] = asyncio.Event()
    return asyncio.create_task(server.MM.chat("sys", [{"role": "user", "content": name}], max_tokens=8, **kw))


@run_test("unit_parallel_state_idle_after_grant_handback", ["R72"])
async def t_state_grant_handback(ctx):
    """[verify 2026-10-07, finding 1] (1) two holders (2 slots) and one waiter; both holders finish
    in the same loop step — the first release grants the waiter — and the waiter is cancelled
    before it resumes: it hands the grant back through the scheduler alone. Nothing holds the
    GPU, so state must be "idle" (the old per-holder `_llm_left` saw itself + the granted
    waiter and left "llm"). (2) llm_spec() raises in _llm_hold's re-check of a granted waiter:
    that release too goes through the scheduler only — the state must follow it."""
    server = kernel()
    MM = server.MM
    assert MM.llm_slots(MM.llm_spec("llm")[1]) == 2
    with fake_http(server) as http:
        h1, h2 = fake_chat(server, "h1"), fake_chat(server, "h2")
        await asyncio.sleep(0.01)
        assert MM.gpu_lock.shared_holders == 2 and MM.state == "llm"
        h3 = fake_chat(server, "h3")
        await asyncio.sleep(0.01)
        assert MM.gpu_lock.waiting == 1
        http.events["h1"].set()
        http.events["h2"].set()
        await asyncio.sleep(0)                  # h1 runs (its release grants h3), then h2
        assert h3._fut_waiter is not None and h3._fut_waiter.done() and not h3.done(), "race not reproduced"
        h3.cancel()
        await asyncio.gather(h1, h2, h3, return_exceptions=True)
        assert h3.cancelled() and not MM.gpu_lock.locked() and MM.gpu_lock.waiting == 0
        assert MM.state == "idle", f"state {MM.state!r} with nothing held (holders {MM.gpu_lock.shared_holders})"
        # (2) the re-check raises
        r1, r2 = fake_chat(server, "r1"), fake_chat(server, "r2")
        await asyncio.sleep(0.01)
        r3 = fake_chat(server, "r3")
        await asyncio.sleep(0.01)
        assert MM.gpu_lock.shared_holders == 2 and MM.gpu_lock.waiting == 1 and MM.state == "llm"

        def boom(key):
            raise RuntimeError("the manifest changed under the request")
        MM.llm_spec = boom
        try:
            http.events["r1"].set()
            http.events["r2"].set()
            res = await asyncio.gather(r1, r2, r3, return_exceptions=True)
        finally:
            del MM.llm_spec
        assert res[0] == "ok" and res[1] == "ok" and isinstance(res[2], RuntimeError), res
        assert not MM.gpu_lock.locked() and MM.state == "idle", f"state {MM.state!r} after the re-check error"
    return "granted-then-cancelled waiter and a re-check error both left state idle with 0 holders"


@run_test("unit_parallel_payload_from_held_spec", ["R72"])
async def t_payload_from_held_spec(ctx):
    """[verify 2026-10-07, finding 2] The body is built from the section the hold RESOLVED, not
    from the key resolved again at send time: a request held on llm (Gemma: `sampling` and
    `chat_template_kwargs` in tier-8gb.json) whose llm_model flips to fast while its server
    loads is still served by Gemma — its body must carry Gemma's fields; one held on llm_fast
    (neither field) flipped back to default must carry none."""
    server = kernel()
    MM = server.MM
    gem, fast = MM.manifest["llm"], MM.manifest["llm_fast"]
    assert gem.get("sampling") and gem.get("chat_template_kwargs"), "tier-8gb's llm carries both fields"
    assert not fast.get("sampling") and not fast.get("chat_template_kwargs"), "the llm_fast fixture carries neither"
    before = with_settings(server, llm_model="default")
    try:
        with fake_http(server) as http:
            load = asyncio.Event()

            async def slow_start(key, spec):       # the server "loads" until the test says so
                await load.wait()
            MM._ensure_llama_resolved = slow_start
            g = fake_chat(server, "g", temperature=0.3)
            await asyncio.sleep(0.01)
            assert MM.gpu_lock.shared_key == "llm" and MM.state == "llm"
            with_settings(server, llm_model="fast")        # flips while g waits for its (Gemma) server
            load.set()
            http.events["g"].set()
            assert await g == "ok"
            body = http.sent["g"][0]
            assert body.get("chat_template_kwargs") == gem["chat_template_kwargs"] and body.get("top_k") == gem["sampling"]["top_k"] \
                and body["temperature"] == gem["sampling"]["temperature"], body
            load.clear()
            f = fake_chat(server, "f", temperature=0.3)
            await asyncio.sleep(0.01)
            assert MM.gpu_lock.shared_key == "llm_fast"
            with_settings(server, llm_model="default")     # and back, while f waits for the coder's server
            load.set()
            http.events["f"].set()
            assert await f == "ok"
            body = http.sent["f"][0]
            assert "chat_template_kwargs" not in body and "top_k" not in body and body["temperature"] == 0.3, body
            assert not MM.gpu_lock.locked() and MM.state == "idle"
    finally:
        with_settings(server, **before)
    return "held on Gemma, flipped to fast mid-load → Gemma's body; held on the coder, flipped back → the coder's"


@run_test("unit_parallel_ctx_zero_is_one_slot", ["R72"])
async def t_ctx_zero(ctx):
    """[verify 2026-10-07, finding 3] `-c 0` = the model's own context (llama-server): nothing to
    multiply, nothing to split — 1 slot and today's argv, like a section without -c; a negative
    value likewise; a real C keeps N slots."""
    server = kernel()
    MM = server.MM
    for args in (["-ngl", "99", "-c", "0", "--jinja"], ["--ctx-size", "0"], ["-c", "-1"]):
        s = {"serve_args": args, "parallel": 2}
        assert MM.llm_slots(s) == 1 and MM.llama_serve_args(s) == args, (args, MM.llama_serve_args(s))
    s = {"serve_args": ["-c", "4096"], "parallel": 2}
    assert MM.llm_slots(s) == 2 and MM.llama_serve_args(s) == ["-c", "8192", "-np", "2", "--no-kv-unified"]
    assert "-c 0" in server.ModelManager.llm_slots.__doc__
    return "-c 0 / --ctx-size 0 / -c -1 with parallel 2 → 1 slot, argv untouched; -c 4096 → 2 slots"


@run_test("unit_parallel_request_timeout_scales_with_slots", ["R72"])
async def t_request_timeout(ctx):
    """[verify 2026-10-07, finding 4] The limit sent to aiohttp: N × LLM_REQUEST_TIMEOUT total for
    a request on an N-slot section (sharing the compute must not time out a generation that
    would have finished alone), a stream also an inactivity limit of the lone value; a 1-slot
    section keeps exactly the old 1800 s total, and no inactivity limit for a non-stream."""
    server = kernel()
    MM = server.MM
    T = server.LLM_REQUEST_TIMEOUT
    assert T == 1800.0 and MM.llm_slots(MM.llm_spec("llm")[1]) == 2
    one = {"serve_args": ["-c", "4096"]}
    assert (MM._request_timeout(one).total, MM._request_timeout(one).sock_read) == (T, None)
    assert (MM._request_timeout(one, stream=True).total, MM._request_timeout(one, stream=True).sock_read) == (T, T)
    with fake_http(server) as http:
        http.events["c"] = asyncio.Event()
        http.events["c"].set()
        assert await MM.chat("sys", [{"role": "user", "content": "c"}], max_tokens=8) == "ok"
        to = http.sent["c"][1]
        assert (to.total, to.sock_read) == (2 * T, None), to
        http.events["s"] = asyncio.Event()
        http.events["s"].set()
        got = []
        async with contextlib.aclosing(MM.chat_stream("sys", [{"role": "user", "content": "s"}])) as g:
            async for d in g:
                got.append(d)
        to = http.sent["s"][1]
        assert got == ["ok"] and (to.total, to.sock_read) == (2 * T, T), (got, to)
    assert MM.state == "idle"
    return f"chat on 2 slots: total {2 * T:.0f} s; stream: total {2 * T:.0f} s + sock_read {T:.0f} s; 1 slot: {T:.0f} s as before"


@run_test("unit_parallel_docs_no_tier_upgrade_requeue_claim", ["R72"])
async def t_docs_wording(ctx):
    """[verify 2026-10-07, finding 5] rule (i) re-queues on a KEY change (llm ↔ llm_fast); a tier
    upgrade keeps the key "llm", so run_upgrade's exclusive stop covers it — the requirement,
    the design doc's rule (i) and _llm_hold's docstring say so, and none lists the tier upgrade
    among the re-queue triggers."""
    server = kernel()
    req = (REPO / "docs" / "REQUIREMENTS.md").read_text()
    r72 = req[req.index("**R72 — "):]
    r72 = r72[:r72.index("\n\n")] if "\n\n" in r72 else r72
    doc = (REPO / "docs" / "PARALLEL-LLM-2026-10-06.md").read_text()
    rule_i = [l for l in doc.splitlines() if l.startswith("| (i)")]
    assert len(rule_i) == 1, rule_i
    for where, text in (("REQUIREMENTS R72", r72), ("doc rule (i)", rule_i[0]), ("_llm_hold", server.ModelManager._llm_hold.__doc__)):
        assert "run_upgrade" in text, f"{where} does not name run_upgrade's stop"
        assert not re.search(r"\(llm_model[^)]*tier upgrade\)", text), f"{where} still lists the tier upgrade as a re-queue trigger"
    return "R72 text, rule (i) and _llm_hold: a tier upgrade is run_upgrade's exclusive stop, not a re-queue"


@run_test("unit_parallel_reselect_keeps_freshly_loaded_choice", ["R72"])
async def t_reselect_no_double_load(ctx):
    """[verify 2026-10-07, finding 6] A request queued AHEAD of h_settings_set's reselect() task
    re-resolves to the newly chosen model and loads it (one launch); reselect, exclusive behind
    it, must leave that server alone — stopping it loaded the same model twice (7 min from a
    USB stick). The next request finds it running: no third launch."""
    server = kernel()
    MM = server.MM
    await MM.stop_llama(grace=3)
    n0 = len(k_inproc_launches())
    before = with_settings(server, llm_model="default")
    try:
        pair = [asyncio.create_task(MM.chat("sys", [{"role": "user", "content": f"[hold 1.5s] gemma {i}"}], max_tokens=16))
                for i in range(2)]
        for _ in range(100):
            if MM.gpu_lock.shared_holders == 2 and MM.llama_ready:
                break
            await asyncio.sleep(0.05)
        assert MM.llama_model_key == "llm" and MM.gpu_lock.shared_holders == 2
        with_settings(server, llm_model="fast")                       # the choice flips …
        c = asyncio.create_task(MM.chat("sys", [{"role": "user", "content": "[hold 0s] fast c"}], max_tokens=16))
        await asyncio.sleep(0.05)                                     # … c queues under llm_fast …
        assert MM.gpu_lock.waiting == 1
        with_settings(server, llm_model="default")                    # … and the settings call announcing it lands behind c

        class Req:
            async def json(self):
                return {"llm_model": "fast"}
        resp = await server.h_settings_set(Req())
        assert resp.status == 200 and server.load_settings()["llm_model"] == "fast"
        await asyncio.sleep(0.05)
        assert MM.gpu_lock.waiting == 2, "reselect() did not queue behind c"
        res = await asyncio.gather(*pair, c)
        assert all(r.startswith("mock answer") for r in res), res
        for _ in range(100):                                          # reselect's turn comes after c
            if not MM.gpu_lock.locked() and MM.gpu_lock.waiting == 0:
                break
            await asyncio.sleep(0.05)
        la = k_inproc_launches()
        assert len(la) == n0 + 2 and "Qwen2.5-Coder" in " ".join(la[-1]["argv"]), [l["argv"][-6:] for l in la[n0:]]
        assert MM.llama_model_key == "llm_fast" and MM.llama_proc and MM.llama_proc.returncode is None, \
            "reselect() stopped the model c had just loaded"
        r = await MM.chat("sys", [{"role": "user", "content": "[hold 0s] fast d"}], max_tokens=16)
        assert r.startswith("mock answer") and len(k_inproc_launches()) == n0 + 2, "the chosen model was loaded twice"
    finally:
        with_settings(server, **before)
        await MM.stop_llama(grace=3)
    return "c loaded the coder ahead of reselect(); reselect left it running; the next request launched nothing"


# ============================================================================= C. end to end
class Kernel:
    """A kernel of this repo on :KPORT with this file's mock llama-server (-np from the manifest)
    and the suite's mock sd-cli, in its own VIBEOS_DIR; the manifest = tier-8gb.json with
    llm.parallel = 2 and [R89] the former llm_fast section (tests/fixture-llm-fast.json) injected
    without `parallel` (the single-slot case) — the shipped tier has none any more."""

    def __init__(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kairos-par-"))
        self.mock = self.tmp / "mock"
        self.proc = None

    def setup(self):
        t = self.tmp
        for d in ("bin", "cache", "models", "games", "manifests", "log", "mock"):
            (t / d).mkdir()
        wrapper = t / "bin" / "llama-server"
        wrapper.write_text(f"#!/bin/bash\nexec {sys.executable} {Path(__file__).resolve()} --mock-llama \"$@\"\n")
        wrapper.chmod(0o755)
        (t / "bin" / "sd-cli").symlink_to(REPO / "tests" / "mock-sd-cli")
        subprocess.run([sys.executable, str(REPO / "scripts" / "engine-stamp.py"), "--mock",
                        "--out", str(t / "bin" / "ENGINES.json")], check=True, capture_output=True)
        m = json.loads((REPO / "manifests" / "tier-8gb.json").read_text())
        m["llm"]["parallel"] = 2
        assert "llm_fast" not in m, "[R89] the shipped tier has no fast mode"
        m["llm_fast"] = {k: v for k, v in llm_fast_fixture().items() if k != "parallel"}   # the "section without parallel" case
        (t / "manifests" / "tier-8gb.json").write_text(json.dumps(m))
        files = set()

        def walk(n):
            if isinstance(n, dict):
                if "url" in n and "file" in n:
                    files.add(n["file"])
                for v in n.values():
                    walk(v)
        walk(m)
        for f in files:
            p = t / "models" / f
            p.parent.mkdir(parents=True, exist_ok=True)
            p.touch()
        env = {k: v for k, v in os.environ.items() if not k.startswith("VIBEOS_") and k != "KAIROS_URL"}
        env.update(VIBEOS_DIR=str(t), VIBEOS_SHELL=str(REPO / "shell"), VIBEOS_MANIFESTS=str(t / "manifests"),
                   VIBEOS_MANIFEST=str(t / "manifests" / "tier-8gb.json"), VIBEOS_PORT=str(KPORT),
                   VIBEOS_LLAMA_PORT=str(LPORT), VIBEOS_SD_PORT="8088", VIBEOS_TRANSLATE_PORT="8089",
                   VIBEOS_FORCE_OFFLINE="1", KAIROS_LOG_DIR=str(t / "log"), VIBEOS_TOKEN_FILE=str(t / "token"),
                   VIBEOS_NVIDIA_PROC="/nonexistent", MOCK_SD_ARGLOG=str(t / "sd-args.log"),
                   PAR_MOCK_LOG=str(self.mock), PAR_MOCK_START_DELAY="0.8", PYTHONUNBUFFERED="1")
        self.logf = open(t / "kernel.log", "wb")
        self.proc = subprocess.Popen([sys.executable, str(REPO / "kernel" / "server.py")], env=env,
                                     stdout=self.logf, stderr=subprocess.STDOUT)

    async def wait(self, session):
        for _ in range(120):
            try:
                async with session.get(f"{BASE}/api/status", timeout=2) as r:
                    if r.status == 200:
                        return
            except Exception:
                pass
            assert self.proc.poll() is None, "kernel died at boot: " + self.log_tail()
            await asyncio.sleep(0.5)
        raise AssertionError("kernel never answered: " + self.log_tail())

    def log_tail(self, n=1500):
        self.logf.flush()
        return (self.tmp / "kernel.log").read_text(errors="ignore")[-n:]

    def launches(self, port=LPORT):
        """The kernel's llama-server launches on `port` (the R71 fast MT server runs its own
        mock on another port; None = all)."""
        p = self.mock / "launches.log"
        out = [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []
        return [l for l in out if port is None or l["argv"][l["argv"].index("--port") + 1] == str(port)]

    def bound(self, port=LPORT):
        p = self.mock / "bound.log"
        out = [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []
        return [b for b in out if port is None or b["port"] == port]

    def requests(self):
        p = self.mock / "requests.log"
        return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []

    def inflight_max(self):
        p = self.mock / "inflight.max"
        return int(p.read_text()) if p.exists() else 0

    def overflow(self):
        p = self.mock / "overflow.log"
        return p.read_text().splitlines() if p.exists() else []

    def close(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.proc.kill(); self.proc.wait()
        self.logf.close()
        keep = os.environ.get("PAR_KEEP_LOGS")          # evidence dir: kernel.log + the mock's logs
        if keep:
            Path(keep).mkdir(parents=True, exist_ok=True)
            for f in [self.tmp / "kernel.log", *self.mock.rglob("*")]:
                if f.is_file():
                    shutil.copy(f, Path(keep) / f.relative_to(self.tmp).as_posix().replace("/", "_"))
        shutil.rmtree(self.tmp, ignore_errors=True)


async def ask(session, prompt, **kw):
    """One /api/ask; t0/t1 are time.time() — the clock the mock's logs use too."""
    t0 = time.time()
    async with session.post(f"{BASE}/api/ask", json={"prompt": prompt, **kw}, timeout=120) as r:
        body = await r.json()
    return {"t0": t0, "t1": time.time(), "status": r.status, "body": body}


async def status(session):
    async with session.get(f"{BASE}/api/status") as r:
        return await r.json()


async def poll_job(session, jid, timeout=60):
    t0 = time.time()
    while time.time() - t0 < timeout:
        async with session.get(f"{BASE}/api/job/{jid}") as r:
            j = await r.json()
        if j["status"] in ("done", "error"):
            return j, time.time()
        await asyncio.sleep(0.1)
    raise AssertionError(f"job {jid} timed out")


@run_test("e2e_parallel_asks_share_one_server", ["R72"])
async def t_e2e_asks(ctx):
    """4 concurrent /api/ask on an llm section with parallel 2: the kernel launches llama-server
    with `-c 32768 -np 2 --no-kv-unified`, keeps TWO requests in flight (never three), the wall
    is two rounds not four; state is "llm" meanwhile and "idle" with the last one."""
    k, s = ctx["kernel"], ctx["session"]
    n0 = len(k.launches())
    t0 = time.monotonic()
    tasks = [asyncio.create_task(ask(s, f"[hold 2s] question {i}")) for i in range(4)]
    await asyncio.sleep(1.5)
    st = await status(s)
    assert st["state"] == "llm", st["state"]
    res = await asyncio.gather(*tasks)
    wall = time.monotonic() - t0
    for r in res:
        assert r["status"] == 200 and r["body"]["text"].startswith("mock answer to:"), r
    st = await status(s)
    assert st["state"] == "idle" and st["loaded"] == "llm", (st["state"], st["loaded"])
    la = k.launches()
    assert len(la) == n0 + 1, la
    argv = la[-1]["argv"]
    assert argv[argv.index("-c") + 1] == "32768" and argv.count("-c") == 1 and "-np" in argv \
        and argv[argv.index("-np") + 1] == "2" and "--no-kv-unified" in argv and "-fa" in argv and "--jinja" in argv, argv
    reqs = [r for r in k.requests() if r["prompt"].startswith("[hold 2s] question")]
    assert len(reqs) == 4 and k.inflight_max() == 2 and not k.overflow(), (len(reqs), k.inflight_max(), k.overflow())
    assert sum(1 for r in reqs if r["inflight_at_start"] == 1) >= 2, [r["inflight_at_start"] for r in reqs]
    bound = [b for b in k.bound() if b["t"] >= la[-1]["t"]]
    gap = min(r["t0"] for r in reqs) - bound[-1]["t"]
    assert bound and gap >= 0, f"a request reached the mock {-gap:.2f} s before it bound (sent before /health)"
    assert 4.0 <= wall < 8.0, f"4 × 2 s on 2 slots took {wall:.1f} s"
    return (f"4 asks in {wall:.1f} s on 2 slots (in-flight max 2, no overflow); argv -c 32768 -np 2 --no-kv-unified; "
            f"first request {gap:.2f} s after the server bound")


@run_test("e2e_exclusive_job_waits_then_runs_fifo", ["R72"])
async def t_e2e_fifo(ctx):
    """Two 3 s asks hold both slots; an image job (exclusive, mock sd-cli 2 s) arrives, then a
    third ask. The image waits for the pair to drain, runs BEFORE the third ask (FIFO), and the
    third ask runs after it — nobody starves, state follows (llm → image → llm → idle)."""
    k, s = ctx["kernel"], ctx["session"]
    a = [asyncio.create_task(ask(s, f"[hold 3s] pair {i}")) for i in range(2)]
    await asyncio.sleep(0.8)
    async with s.post(f"{BASE}/api/image", json={"prompt": "a red bicycle", "expand": "off"}) as r:
        jid = (await r.json())["job"]
    t_img_sub = time.time()
    await asyncio.sleep(0.3)
    c = asyncio.create_task(ask(s, "[hold 1s] third"))
    states = set()
    for _ in range(25):
        states.add((await status(s))["state"])
        await asyncio.sleep(0.2)
    job, t_img_done = await poll_job(s, jid)
    assert job["status"] == "done" and job["result"].endswith(".png"), job
    ra = await asyncio.gather(*a)
    rc = await c
    t_pair_done = max(r["t1"] for r in ra)
    assert t_img_done >= t_pair_done - 0.3, "the image ran before the llm pair had drained"
    assert rc["t1"] > t_img_done, f"the third ask finished before the image job: FIFO broken ({rc['t1'] - t_img_done:+.2f} s)"
    third = [r for r in k.requests() if r["prompt"].startswith("[hold 1s] third")]
    assert third and third[-1]["inflight_at_start"] == 0 and third[-1]["t0"] >= t_img_sub + 2.0
    assert "llm" in states and "image" in states, states
    assert (await status(s))["state"] == "idle"
    return (f"image waited {t_img_done - t_pair_done:+.1f} s past the pair, ran, third ask after it "
            f"(+{rc['t1'] - t_img_done:.1f} s); states seen {sorted(states)}")


@run_test("e2e_swap_to_expander_after_drain_and_back", ["R72", "R79"])
async def t_e2e_swap(ctx):
    """[R79] By default the RESIDENT llm expands: with both of its slots held, the image job's
    expansion queues as a shared llm request, takes the first freed slot of the SAME server (no
    launch, never a third request in flight), the picture renders, and the llm comes back lazily
    with the next ask (one launch). The pre-R79 path (settings image_expander=encoder) needs another
    model key (the expander): llama-server is restarted for it only after the llm pair has drained,
    with the expander's own argv (the Krea section's expander_parallel: 2 since the GPU validation
    2026-10-07 → -c 2×8192 -np 2 --no-kv-unified, every slot its 8192), the picture renders from
    the expanded prompt, and the next ask brings the 2-slot llm back."""
    k, s = ctx["kernel"], ctx["session"]
    # (1) the default: the expansion shares the running llm
    await ask(s, "[hold 0s] warm")                      # the llm runs (a launch now, if not before)
    n0, q0 = len(k.launches()), len(k.requests())
    a = [asyncio.create_task(ask(s, f"[hold 2s] share {i}")) for i in range(2)]
    await asyncio.sleep(0.8)
    async with s.post(f"{BASE}/api/image", json={"prompt": "a lighthouse", "expand": "on"}) as r:
        jid = (await r.json())["job"]
    job, _ = await poll_job(s, jid)
    await asyncio.gather(*a)
    assert job["status"] == "done" and job.get("expanded") is True and job.get("expander") == "krea-neutral" \
        and job.get("expander_model") == "llm", job
    assert len(k.launches()) == n0, ("the expansion launched a server", [l["argv"] for l in k.launches()[n0:]])
    exp = [r for r in k.requests()[q0:] if r["prompt"].startswith("a lighthouse")]
    assert len(exp) == 1 and exp[0]["np"] == 2 and exp[0]["inflight_at_start"] <= 1 and not k.overflow(), exp
    assert len(k.launches()) == n0, "the llm must come back lazily, not right after the picture"
    r = await ask(s, "[hold 0s] lazy")
    assert r["status"] == 200 and len(k.launches()) == n0 + 1 and "gemma" in " ".join(k.launches()[-1]["argv"]).lower()
    # (2) the encoder path, selectable
    async with s.post(f"{BASE}/api/settings", json={"image_expander": "encoder"}) as r:
        assert r.status == 200, await r.text()
    n0 = len(k.launches())
    a = [asyncio.create_task(ask(s, f"[hold 2s] swap {i}")) for i in range(2)]
    await asyncio.sleep(0.8)
    async with s.post(f"{BASE}/api/image", json={"prompt": "a lighthouse", "expand": "on"}) as r:
        jid = (await r.json())["job"]
    ra = await asyncio.gather(*a)
    t_pair_done = max(r["t1"] for r in ra)
    job, _ = await poll_job(s, jid)
    async with s.post(f"{BASE}/api/settings", json={"image_expander": "auto"}) as r:
        assert r.status == 200
    assert job["status"] == "done" and job.get("expanded") is True and job.get("expander") == "krea-neutral" \
        and job.get("expander_model") == "encoder", job   # [R76, R79]
    la = k.launches()
    assert len(la) == n0 + 1, [l["argv"] for l in la[n0:]]
    ex = la[-1]
    assert ex["t"] >= t_pair_done - 0.3, "the expander's server started before the llm pair drained"
    ex_slots = json.loads((REPO / "manifests" / "tier-8gb.json").read_text())["image"].get("expander_parallel", 1)
    if ex_slots > 1:
        assert ex["argv"][ex["argv"].index("-np") + 1] == str(ex_slots) and "--no-kv-unified" in ex["argv"] \
            and ex["argv"][ex["argv"].index("-c") + 1] == str(8192 * ex_slots), ex["argv"]
    else:
        assert "-np" not in ex["argv"] and ex["argv"][ex["argv"].index("-c") + 1] == "8192" and "--kv-unified" not in " ".join(ex["argv"]), ex["argv"]
    assert "Qwen3VL" in " ".join(ex["argv"])
    r = await ask(s, "[hold 0s] back")
    assert r["status"] == 200
    la = k.launches()
    assert len(la) == n0 + 2 and la[-1]["argv"][la[-1]["argv"].index("-np") + 1] == "2" and "gemma" in " ".join(la[-1]["argv"]).lower(), la[-1]["argv"]
    assert (await status(s))["loaded"] == "llm"
    return ("default: the expansion took a freed slot of the running llm (no launch, ≤ 2 in flight), llm back lazily; "
            "encoder: expander server after the drain with its own argv (per-slot -c 8192), llm (2 slots) back")


@run_test("e2e_parallel_one_section_is_today", ["R72"])
async def t_e2e_today(ctx):
    """Switching to the fast coder LLM (this kernel's injected llm_fast fixture has no `parallel`) launches
    llama-server with the section's argv EXACTLY as before — no -np, no KV flag, -c as written — and back."""
    k, s = ctx["kernel"], ctx["session"]
    fast_args = llm_fast_fixture()["serve_args"]
    async with s.post(f"{BASE}/api/settings", json={"llm_model": "fast"}) as r:
        assert r.status == 200, await r.text()
    await asyncio.sleep(0.3)
    n0 = len(k.launches())
    r = await ask(s, "[hold 0s] fast one")
    assert r["status"] == 200
    la = k.launches()
    assert len(la) == n0 + 1, la[n0:]
    argv = la[-1]["argv"]
    i = argv.index("--port") + 2
    assert argv[i:] == fast_args, (argv[i:], fast_args)
    assert "Qwen2.5-Coder" in argv[argv.index("-m") + 1]
    assert (await status(s))["loaded"] == "llm_fast"
    async with s.post(f"{BASE}/api/settings", json={"llm_model": "default"}) as r:
        assert r.status == 200
    await asyncio.sleep(0.3)
    r = await ask(s, "[hold 0s] default again")
    la = k.launches()
    assert len(la) == n0 + 2 and la[-1]["argv"][-3:] == ["-np", "2", "--no-kv-unified"]
    return f"llm_fast argv == manifest serve_args {fast_args}; default back with 2 slots"


@run_test("e2e_translation_quality_via_llm_shares_slots", ["R72", "R71", "R89"])
async def t_e2e_translation(ctx):
    """[R89 through the real endpoints] translation on (de, quality) on the 8 GB tier: /api/translate runs on the
    LOADED LLM — no translation llama-server is ever launched (no swap, `loaded` stays llm) — and a batch takes
    ONE of its two slots at a time: the batch's requests never overlap each other, so an app generation that
    arrives mid-batch gets the other slot at once — its spec and its stream both start while the batch still
    runs, next to a translation in flight (it never queues behind the whole batch; at worst behind ONE text);
    never more than 2 requests in flight. Switching translation off has nothing to unload."""
    import aiohttp
    k, s = ctx["kernel"], ctx["session"]
    async with s.post(f"{BASE}/api/settings", json={"translation": "on", "language": "de", "translation_quality": "quality"}) as r:
        assert r.status == 200, await r.text()
    r = await ask(s, "[hold 0s] warm up")                # starts the fast MT server (english_text) + the llm
    assert r["status"] == 200, r
    st = await status(s)
    assert st["loaded"] == "llm" and st["translation"]["quality_llm"] is True and st["translation"]["effective"] == "quality", st["translation"]
    n0, r0 = len(k.launches()), len(k.requests())
    stamp = f"{time.time():.3f}"
    texts = [f"[hold 1.2s] batch line {i} {stamp}" for i in range(5)]

    async def batch():
        t0 = time.time()
        async with s.post(f"{BASE}/api/translate", json={"texts": texts}, timeout=aiohttp.ClientTimeout(total=120)) as rr:
            return {"t0": t0, "t1": time.time(), "status": rr.status, "body": await rr.json()}

    async def app():
        t0 = time.time()
        async with s.post(f"{BASE}/api/app", json={"prompt": f"[hold 0s] r89 app while translating {stamp}"},
                          timeout=aiohttp.ClientTimeout(total=120)) as rr:
            body = (await rr.read()).decode("utf-8", "ignore")
        return {"t0": t0, "t1": time.time(), "status": rr.status, "body": body}

    tb = asyncio.create_task(batch())
    await asyncio.sleep(0.7)
    ra = await app()
    rb = await tb
    assert rb["status"] == 200 and rb["body"]["model"] == "quality" and rb["body"]["via"] == "llm" and rb["body"]["calls"] == 5 \
        and all(x.startswith("mock answer to:") for x in rb["body"]["texts"]), rb
    assert ra["status"] == 200 and "<!DOCTYPE" in ra["body"], ra["body"][-300:]
    app_took, batch_took = ra["t1"] - ra["t0"], rb["t1"] - rb["t0"]
    reqs = [q for q in k.requests()[r0:] if q.get("np") == 2]          # the LLM's server (the fast MT mock runs -np 4)
    tr = sorted((q for q in reqs if q["prompt"].startswith("Translate the following text into German")), key=lambda q: q["t0"])
    assert len(tr) == 5, [q["prompt"] for q in reqs]
    overlaps = [(a["t1"], b["t0"]) for a, b in zip(tr, tr[1:]) if b["t0"] < a["t1"] - 0.05]
    assert not overlaps, f"two translations of one batch ran at once: {overlaps}"
    apps = sorted((q for q in reqs if not q["prompt"].startswith("Translate") and q["t0"] >= ra["t0"] - 0.1), key=lambda q: q["t0"])
    assert len(apps) >= 2, [(q["prompt"][:30], q["t0"] - ra["t0"]) for q in apps]      # the spec + the stream
    # the app never queued behind the batch: its first request started at once, both before the batch's last text
    # started, sharing the server with a running translation
    assert apps[0]["t0"] - ra["t0"] < 1.5 and apps[1]["t0"] < tr[-1]["t0"], \
        (round(apps[0]["t0"] - ra["t0"], 2), round(apps[1]["t0"] - tr[-1]["t0"], 2))
    assert any(q["inflight_at_start"] >= 1 for q in apps[:2]), [(q["prompt"][:30], q["inflight_at_start"]) for q in apps[:2]]
    assert k.inflight_max() <= 2 and not k.overflow(), (k.inflight_max(), k.overflow())
    la = k.launches()[n0:]
    assert not any("Index-Translate" in l["argv"][l["argv"].index("-m") + 1] for l in la), [l["argv"][-6:] for l in la]
    for _ in range(100):                                  # the app's icon (an exclusive sd job) may still run
        st = await status(s)
        if st["state"] == "idle":
            break
        await asyncio.sleep(0.1)
    assert st["translation"]["running"]["quality"] is False and st["loaded"] in ("llm", None), (st["loaded"], st["translation"]["running"])
    async with s.post(f"{BASE}/api/settings", json={"translation": "off"}) as r:
        assert r.status == 200
    await asyncio.sleep(0.3)
    r = await ask(s, "[hold 0s] back")
    assert r["status"] == 200 and not any("Index-Translate" in l["argv"][l["argv"].index("-m") + 1] for l in k.launches()[n0:])
    return (f"5-text quality batch on the loaded LLM in {batch_took:.1f} s, one slot at a time (no overlap); an app mid-batch "
            f"started at once on the other slot ({app_took:.1f} s in all); in-flight max {k.inflight_max()}; no translation server launched")


@run_test("e2e_vanished_app_client_keeps_its_slot_r62", ["R72", "R62"])
async def t_e2e_vanished(ctx):
    """[R62] an app generation whose window closes mid-stream keeps generating (the app is saved
    and cached for the next open) — so its slot stays busy until it ends, exactly as the whole
    GPU did before; with 2 slots the OTHER slot still serves. Two vanished streams (6 s) hold
    both slots: a following ask waits for them, the apps land in /api/apps, state ends idle."""
    k, s = ctx["kernel"], ctx["session"]
    socks = []
    for i in range(2):
        body = json.dumps({"prompt": f"[hold 6s] vanishing app {i}"}).encode()
        so = socket.create_connection(("127.0.0.1", KPORT), timeout=30)
        so.sendall(b"POST /api/app HTTP/1.1\r\nHost: k\r\nContent-Type: application/json\r\n"
                   + f"Content-Length: {len(body)}\r\n\r\n".encode() + body)
        socks.append(so)
    buf = [b"", b""]
    t0 = time.time()
    while not all(b"<!DOCTYPE" in b for b in buf):          # the first streamed delta of each
        for i, so in enumerate(socks):
            so.settimeout(0.5)
            try:
                chunk = so.recv(4096)
                assert chunk, f"stream {i} ended early: {buf[i][-200:]!r}"
                buf[i] += chunk
            except socket.timeout:
                pass
        assert time.time() - t0 < 30, [b[-200:] for b in buf]
    t_first = time.time()
    st = await status(s)
    assert st["state"] == "llm", st["state"]
    for so in socks:
        so.close()
    r = await ask(s, "[hold 0s] after the vanish")
    assert r["status"] == 200, r
    assert k.inflight_max() <= 2 and not k.overflow()
    for _ in range(60):                                    # the saved apps (+ their icons: mock sd-cli, exclusive)
        await asyncio.sleep(0.5)
        async with s.get(f"{BASE}/api/apps") as rr:
            apps = await rr.json()
        names = json.dumps(apps)
        if "vanishing-app-0" in names and "vanishing-app-1" in names and (await status(s))["state"] == "idle":
            break
    else:
        raise AssertionError(f"apps not saved / state not idle: {names[:300]}")
    # [R86 r4b, S13, test only] measured from the STREAMS' start — the mock's own request log — not from the first delta the
    # client saw: R86's stream verdict holds the first delta back until it can judge the document (chunk 4 of 8 here), so
    # "the remaining stream after the first delta" sat exactly on the old 3.0 s floor (bimodal 2.3 / 3.0 s, review R4 §7).
    # Each vanished app holds a slot twice (the spec step 6 s, then the stream 6 s), so a bound from the POSTs cannot tell
    # the cases apart either. Kept slots = both vanished streams ran their full 6 s hold, the ask's request started only
    # after one of them ended, and the ask ended ≥ 6 s after the streams began; the R62 defect (a vanished client frees
    # its slot) cuts the streams short and lets the ask in early
    streams = sorted((q for q in k.requests() if q["stream"] and q["t0"] >= t0 - 1.0), key=lambda q: q["t0"])[:2]
    asked = [q for q in k.requests() if q["prompt"].startswith("[hold 0s] after the vanish")]
    assert len(streams) == 2 and all(q["t1"] - q["t0"] >= 5.5 for q in streams), \
        f"the vanished streams were cut short: {[round(q['t1'] - q['t0'], 2) for q in streams]}"
    assert asked and asked[-1]["t0"] >= min(q["t1"] for q in streams) - 0.3, \
        f"the ask's request started {min(q['t1'] for q in streams) - asked[-1]['t0']:.2f} s before a vanished stream ended"
    took = r["t1"] - min(q["t0"] for q in streams)
    assert 5.5 < took < 12.0, f"the ask ended {took:.1f} s after the streams began (expected ≈ the 6 s hold)"
    return (f"both vanished streams ran their full hold; the ask ended {took:.1f} s after the streams began "
            f"({r['t1'] - t_first:.1f} s after the first delta), apps saved, state idle")


# ============================================================================= main
async def main():
    only = [x.strip() for x in os.environ.get("KAIROS_TEST_ONLY", "").split(",") if x.strip()]
    tests = [t for t in TESTS if not only or any(o in t._name for o in only)]
    try:
        import aiohttp
    except ImportError:
        aiohttp = None
    print(f"kAIrOS parallel-LLM tests — {len(tests)} tests" + (" (no aiohttp: scheduler part only)" if not aiohttp else ""), flush=True)
    ctx = {}
    k = None
    try:
        if aiohttp:
            k = Kernel()
            k.setup()
            # part B imports the kernel IN-PROCESS against the same mock env, on ports nothing else
            # uses next to the suite's kernel (8081 llama, 8082 sd, 8083 its 2nd kernel's llama, 8085
            # fast MT, 8090 kiwix-serve [knowledge.KIWIX_PORT] — a mock on a taken port exits 1)
            os.environ.update(VIBEOS_DIR=str(k.tmp), VIBEOS_MANIFEST=str(k.tmp / "manifests" / "tier-8gb.json"),
                              VIBEOS_MANIFESTS=str(k.tmp / "manifests"), VIBEOS_SHELL=str(REPO / "shell"),
                              KAIROS_LOG_DIR=str(k.tmp / "log"), VIBEOS_NVIDIA_PROC="/nonexistent",
                              VIBEOS_LLAMA_PORT="8094", VIBEOS_TRANSLATE_PORT="8095", VIBEOS_SD_PORT="8096",
                              PAR_MOCK_LOG=str(k.mock / "inproc"), PAR_MOCK_START_DELAY="0.8")
            ctx["kernel"] = k
            async with aiohttp.ClientSession() as s:
                ctx["session"] = s
                await k.wait(s)
                for t in tests:
                    await t(ctx)
        else:
            for t in tests:
                if t._name.startswith("sched_"):
                    await t(ctx)
                else:
                    record(t._name, ["R72"], "skip", "aiohttp not installed")
    finally:
        if k:
            if any(r["status"] == "fail" for r in RESULTS.values()):
                print("== kernel log tail ==\n" + k.log_tail(3000), flush=True)
            k.close()
    out = {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "tests": RESULTS}
    if not only:
        (REPO / "tests" / "results-llm-parallel.json").write_text(json.dumps(out, indent=1))
    fails = [n for n, r in RESULTS.items() if r["status"] == "fail"]
    passes = sum(1 for r in RESULTS.values() if r["status"] == "pass")
    skips = sum(1 for r in RESULTS.values() if r["status"] == "skip")
    print(f"\n{passes} passed, {len(fails)} failed, {skips} skipped -> tests/results-llm-parallel.json", flush=True)
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--mock-llama":
        mock_llama_main(sys.argv[2:])
    else:
        asyncio.run(main())
