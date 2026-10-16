#!/usr/bin/env python3
"""kAIrOS R85 — ✨ improve a picture (docs/PICTURE-EDIT-2026-10-15.md): Muse creates, the `image_edit` section (a Qwen
image-edit model, an add-on the media image pre-installs) edits with the user's words, versions are kept.

Standalone: `python3 tests/test_picture_edit.py` (run-tests.sh runs it after the suite). Writes
tests/results-picture-edit.json (not for a KAIROS_TEST_ONLY subset).
* files: the manifests' `image_edit` section (every tier + the 8 GB test variants, byte-stable), the shell wiring;
* kernel (in-process, a PRIVATE VIBEOS_DIR, placeholder model files, the suite's mock sd-server / sd-cli linked into its
  bin/ — sd-server on :8151): the argv (--llm_vision, the section's own schedule, no user tuning), the request's
  reference picture, the idle window, the version chain (v0 = the original, parents, revert, prune), whole-chain
  eviction, the entry checks (paths, the minors block, the add-on missing, non-PNG), the translation hop, the sd floor;
* e2e: the REAL shell (index.html + shell.js + pictures.js) on the real kernel routes in a headless Chromium over CDP
  (tests/test_dock_browser.py's CDP/Desk) — skips without Chromium (kairos-fix-chromium:local / kairos-fix-x11:local
  have one). KAIROS_SHOTS=<dir> saves the screenshots; KAIROS_PE_SOURCE=<png> + KAIROS_PE_EDITS=<png>,<png>,… show real
  pictures (a Muse source and real edits) instead of the generated test pictures.
"""
import asyncio
import base64
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
SHOTS = os.environ.get("KAIROS_SHOTS", "")
SDPORT = 8151                     # ports in use: see tests/test_imaging_workflow.py; test_kernel 1376/8141-8143
TIERS = ("8gb", "16gb", "24gb", "32gb")
VARIANTS = ("coder7b", "qwen35-9b", "gemma4-26b")
sys.path.insert(0, str(REPO / "tests"))


def record(name, reqs, status, detail=""):
    RESULTS[name] = {"reqs": reqs, "status": status, "detail": detail[:300]}
    mark = {"pass": "✓", "fail": "✗", "skip": "~"}[status]
    print(f"  {mark} {name} [{', '.join(reqs)}] {detail[:170]}", flush=True)


class SkipTest(Exception):
    pass


def test(name, reqs):
    def deco(fn):
        def wrapper(ctx):
            try:
                detail = fn(ctx)
                if asyncio.iscoroutine(detail):
                    detail = asyncio.run(asyncio.wait_for(detail, timeout=240))
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


def png(w, h, rgb=(128, 128, 128), rgb2=None):
    import struct
    import zlib
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
    chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"".join(rows), 1)) + chunk(b"IEND", b""))


# ----------------------------------------------------------------------------- the kernel, in-process, private dir
_K = {}


def kernel():
    if "server" in _K:
        return _K["server"]
    try:
        import aiohttp  # noqa: F401
    except ImportError:
        raise SkipTest("aiohttp not installed (run in the testrig container)")
    d = Path(tempfile.mkdtemp(prefix="kairos-pe-"))
    (d / "bin").mkdir()
    os.symlink(REPO / "tests" / "mock-sd-cli", d / "bin" / "sd-cli")
    subprocess.run([sys.executable, str(REPO / "scripts" / "engine-stamp.py"), "--mock", "--out", str(d / "bin" / "ENGINES.json")],
                   check=True, capture_output=True)
    # placeholder model files: every file of every tier (the presence checks), as run-tests.sh does
    seen = set()

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
                      VIBEOS_SD_PORT=str(SDPORT), VIBEOS_SD_SESSION_IDLE="0", VIBEOS_SD_FADVISE="off",
                      MOCK_SDSERVER_LOG=str(d / "sd-server.log"), MOCK_SD_ARGLOG=str(d / "sd-args.log"),
                      MOCK_SDSERVER_LOAD_S="0.3", MOCK_SDSERVER_RENDER_S="0.2")
    sys.path.insert(0, str(REPO / "kernel"))
    import server
    _K["server"], _K["dir"] = server, d
    return server


def link_sd_server(on=True):
    p = _K["dir"] / "bin" / "sd-server"
    if p.is_symlink() or p.exists():
        p.unlink()
    if on:
        os.symlink(REPO / "tests" / "mock-sd-server", p)


def seed_picture(s, name="seed.png", w=1024, h=576, rgb=(30, 60, 120), rgb2=(220, 140, 60)):
    p = s.FILES_DIR / "images" / name
    p.parent.mkdir(parents=True, exist_ok=True)
    src = os.environ.get("KAIROS_PE_SOURCE")
    p.write_bytes(Path(src).read_bytes() if src and name == "seed.png" else png(w, h, rgb, rgb2))
    return f"/files/images/{name}"


def sd_log():
    p = Path(os.environ["MOCK_SDSERVER_LOG"])
    out = []
    if p.exists():
        for ln in p.read_text().splitlines():
            kind, _, rest = ln.partition("\t")
            out.append((kind, rest))
    return out


class Painter:
    """MM.sd_batch mocked: records every job, writes a PNG per job (the next of KAIROS_PE_EDITS when given, else a
    tinted picture of the job's size). The R86 floor is tested through the REAL sd_batch (pe_entry_checks…)"""

    def __init__(self, s, delay=0.2):
        self.s, self.delay, self.jobs, self.saved = s, delay, [], {}
        self.edits = [e for e in os.environ.get("KAIROS_PE_EDITS", "").split(",") if e]

    def __enter__(self):
        s, me = self.s, self

        async def sd_batch(kind, jobs):
            me.jobs.append([dict(j, kind=kind) for j in jobs])
            await asyncio.sleep(me.delay)
            out = []
            for j in jobs:
                n = len(me.jobs)
                data = Path(me.edits[(n - 1) % len(me.edits)]).read_bytes() if me.edits else \
                    png(int(j["w"]), int(j["h"]), (40 + 37 * n % 200, 90, 160), (230, 200 - 25 * n % 180, 90))
                Path(j["out"]).write_bytes(data)
                out.append({"ok": True, "path": j["out"], "seed": 1000 + n, "engine": "sd-server", "error": None})
            return out
        self.saved["sd_batch"], s.MM.sd_batch = s.MM.sd_batch, sd_batch

        async def english(text, settings=None):
            return text.replace("mach den Himmel lila", "make the sky purple")
        self.saved["english_text"], s.english_text = s.english_text, english
        return self

    def __exit__(self, *exc):
        self.s.MM.sd_batch = self.saved["sd_batch"]
        self.s.english_text = self.saved["english_text"]


async def _client(s):
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer
    app = web.Application()
    s.add_routes(app)
    c = TestClient(TestServer(app))
    await c.start_server()
    return c


async def _job(c, jid, timeout=30):
    t0 = time.time()
    while time.time() - t0 < timeout:
        async with c.get(f"/api/job/{jid}") as r:
            j = await r.json()
        if j.get("status") in ("done", "error"):
            return j
        await asyncio.sleep(0.05)
    raise AssertionError(f"job {jid} never finished")


async def _improve(c, picture, instruction="", status=200):
    async with c.post("/api/image/improve", json={"picture": picture, "instruction": instruction}) as r:
        body = await r.json()
        assert r.status == status, (r.status, body)
    return body


# ============================================================================= files
@test("pe_manifest_image_edit", ["R85", "R47", "R3"])
def t_manifest(ctx):
    """every tier carries `image_edit` (the picture editor): an add-on (the media image pre-installs it), a DiT + text
    encoder + VISION tower (mmproj) + VAE under image/ with Hugging Face URLs, byte sizes and sha256, its own schedule
    (no --scheduler; the Turbo's --sigmas when it is one), the CPU-offload flag never, sizes divisible by 64, a longer
    idle window, a licence line and a NOTICE; the JSON stays byte-stable; the three 8 GB test variants equal tier-8gb
    outside `llm`; `image_edit` is no choosable image model (IMAGE_MODEL_KEYS) and Muse stays the creator"""
    import hashlib  # noqa: F401
    m8 = manifest("8gb")
    for t in TIERS:
        raw = (REPO / "manifests" / f"tier-{t}.json").read_text(encoding="utf-8")
        m = json.loads(raw)
        assert raw == json.dumps(m, indent=1, ensure_ascii=False) + "\n", f"tier-{t}: not byte-stable"
        e = m["image_edit"]
        assert e == m8["image_edit"], f"tier-{t}: the picture editor differs from tier-8gb"
        assert e.get("addon") is True and e["name"] and e["licence"] and e.get("notice"), (t, e.get("name"))
        for part in ("diffusion", "text_encoder", "vision", "vae"):
            f = e[part]
            assert f["file"].startswith("image/") and f["url"].startswith("https://huggingface.co/"), (t, part, f)
            assert re.fullmatch(r"[0-9a-f]{64}", f["sha256"]) and f["size_bytes"] > 0 \
                and abs(f["size_gb"] - f["size_bytes"] / 1e9) < 0.01, (t, part, f)
        g = e["gen_args"]
        assert "--offload-to-cpu" not in g and "--scheduler" not in g and "--diffusion-fa" in g and "--vae-tiling" in g, g
        assert int(g[g.index("-W") + 1]) % 64 == 0 and int(g[g.index("-H") + 1]) % 64 == 0 and e["max_pixels"] <= 1048576
        if "--sigmas" in g:     # a custom schedule: --steps = number of sigmas - 1 (the shipped sd.cpp 3f8527a)
            assert int(g[g.index("--steps") + 1]) == len(g[g.index("--sigmas") + 1].split(",")) - 1, g
        assert float(e["session_idle_s"]) >= 60, e["session_idle_s"]
        assert m["image"]["name"].startswith("Muse ("), "Muse creates"
    for name in VARIANTS:
        raw = (REPO / "manifests" / "test" / f"tier-8gb-{name}.json").read_text(encoding="utf-8")
        v = json.loads(raw)
        assert raw == json.dumps(v, indent=1, ensure_ascii=False) + "\n", f"{name}: not byte-stable"
        assert v["image_edit"] == m8["image_edit"], name
    # the chosen set (the 2026-10-15 A/B, docs/PICTURE-EDIT-2026-10-15.md): Qwen-Image-2.1-Turbo Q4_K_M + Qwen3-VL-8B
    e = m8["image_edit"]
    assert e["diffusion"]["file"] == "image/qwen_image_2.1_turbo_Q4_K_M.gguf" \
        and e["diffusion"]["sha256"] == "0f5a11ebbaf3110a16120311454eaac35db13798e0102339e4f2b8fd5be6ba82" \
        and e["text_encoder"]["file"] == "image/Qwen3VL-8B-Instruct-Q4_K_M.gguf" \
        and e["vision"]["file"] == "image/mmproj-Qwen3VL-8B-Instruct-Q8_0.gguf" and e["vae"] == m8["image_qwen"]["vae"], e
    # its licence ships with the image: the Qwen Research licence + the NOTICE (§3a/§3c) in the overlay
    inc = REPO / "iso" / "config" / "includes.chroot"
    notice = inc / e["notice"].lstrip("/")
    assert notice.is_file() and (notice.parent / "LICENSE.txt").is_file(), notice
    assert "Qwen is licensed under the Qwen RESEARCH LICENSE AGREEMENT, Copyright (c) 2026 Hangzhou Tongyi Laboratory " \
        "Technology Co., Ltd. All Rights Reserved." in notice.read_text()
    assert "Qwen RESEARCH LICENSE AGREEMENT" in (notice.parent / "LICENSE.txt").read_text()
    src = (REPO / "kernel" / "server.py").read_text()
    keys = re.search(r"IMAGE_MODEL_KEYS = (\{.*?\})", src, re.S).group(1)
    assert "image_edit" not in keys, "the picture editor is not a creating model"
    e = m8["image_edit"]
    total = sum(e[p]["size_bytes"] for p in ("diffusion", "text_encoder", "vision", "vae") + (("lora",) if "lora" in e else ()))
    return f"4 tiers + 3 variants: {e['name'][:42]} — {total / 1e9:.2f} GB, addon, vision tower, sha256 pins, idle {e['session_idle_s']} s, LICENSE + NOTICE"


@test("pe_shell_wiring", ["R85", "R81", "R46"])
def t_shell(ctx):
    """the shell: index.html loads pictures.js after games.js and has the menubar ✨ next to ◐ wallpaper; the quick viewer
    becomes the picture viewer (openPicture) and has its ✨ from the start; the Imagine studio posts {kairosPicture} and
    the viewer accepts it from that built-in only; pictures.js talks to /api/image/improve, /api/image/versions,
    /api/image/version and reuses the sticky note + the browser's version-strip look; node --check when node is there"""
    idx = (REPO / "shell" / "index.html").read_text()
    js = (REPO / "shell" / "shell.js").read_text()
    pj = (REPO / "shell" / "pictures.js").read_text()
    assert idx.index("/shell/games.js") < idx.index("/shell/pictures.js"), "pictures.js after shell.js/games.js"
    assert 'id="mb-wallpaper-improve"' in idx and idx.index('id="mb-wallpaper"') < idx.index('id="mb-wallpaper-improve"')
    i = js.index("async function imageQuick")
    q = js[i:js.index("\n}\n", i)]
    assert "improvable: false" not in q and "openPicture(url, { title: prompt, win })" in q, q[:400]
    assert "parent.postMessage({kairosPicture:url,title:what}" in js and "local Muse" in js
    for needle in ("/api/image/improve", "/api/image/versions", "/api/image/version", "stickyPrompt(win)", "br-ver pv-ver",
                   'win.builtin !== "imagine"', "win.viewer = true", "pictureSetWallpaper", "naturalWidth % 2 === 1"):
        assert needle in pj, needle
    node = shutil.which("node")
    if node:
        subprocess.run([node, "--check", str(REPO / "shell" / "pictures.js")], check=True, capture_output=True)
    return "pictures.js loaded after games.js; menubar ✨; quick viewer → viewer; Imagine gallery click; strip + note reused" \
        + ("; node --check ok" if node else "; node --check skipped (no node)")


# ============================================================================= kernel
@test("pe_kernel_argv_session_window", ["R85", "R79", "R72", "R80"])
async def t_argv(ctx):
    """through the REAL sd_batch with the mock sd-server: one ✨ = an sd-server session started with the edit section's
    DiT, the --llm text encoder, the --llm_vision tower, the VAE and its own schedule (the user's image_steps / cfg
    tuning applies to Muse, never to the editor); the request carries the picture as extra_images (base64 of v0 = the
    copied original) at the picture's size; v1 is written, versions.json says parent 0 + the instruction; the GPU status
    names the editor while it is resident; with the idle window on (2 s, section session_idle_s ≥ 60 → the window is
    the section's), a second ✨ from v1 JOINS the open session (one launch, two requests) and the window closes later"""
    s = kernel()
    link_sd_server(True)
    sec = s.MANIFEST["image_edit"]
    s.save_settings({**s.load_settings(), "image_steps": "30", "image_cfg": "4.5"})
    url = seed_picture(s, "argv.png", 832, 576)
    log0 = len(sd_log())
    c = await _client(s)
    try:
        r = await _improve(c, url, "make the sky purple")
        j = await _job(c, r["job"])
        assert j["status"] == "done", j
        assert j["result"] == f"/files/images/versions/{r['root']}/v1.png" and j["version"] == 1 and j["current"] == 1, j
        log = sd_log()[log0:]
        launches = [x for k, x in log if k == "LAUNCH"]
        reqs = [json.loads(x) for k, x in log if k == "REQ"]
        assert len(launches) == 1 and len(reqs) == 1, log
        argv = launches[0].split("\t")
        d = str(s.MODELS_DIR)
        assert argv[argv.index("--diffusion-model") + 1] == f"{d}/{sec['diffusion']['file']}"
        assert argv[argv.index("--llm") + 1] == f"{d}/{sec['text_encoder']['file']}"
        assert argv[argv.index("--llm_vision") + 1] == f"{d}/{sec['vision']['file']}"
        assert argv[argv.index("--vae") + 1] == f"{d}/{sec['vae']['file']}"
        g = sec["gen_args"]
        assert argv[argv.index("--steps") + 1] == g[g.index("--steps") + 1] != "30", "user tuning must not reach the editor"
        assert argv[argv.index("--cfg-scale") + 1] == g[g.index("--cfg-scale") + 1]
        if "--sigmas" in g:
            assert argv[argv.index("--sigmas") + 1] == g[g.index("--sigmas") + 1]
        if "lora" in sec:
            assert "--lora-model-dir" in argv
        q = reqs[0]
        chain = s.image_chain_dir(r["root"])
        assert q["prompt"] == "make the sky purple" and (q["width"], q["height"]) == (832, 576), q
        assert base64.b64decode(q["extra_images"][0]) == (chain / "v0.png").read_bytes() == (s.FILES_DIR / "images" / "argv.png").read_bytes()
        idx = s.load_image_versions(r["root"])
        assert [v["v"] for v in idx["versions"]] == [0, 1] and idx["versions"][1]["parent"] == 0 \
            and idx["versions"][1]["instruction"] == "make the sky purple" and idx["source"] == url, idx
        # the idle window: on, 2 s base — the section's window is longer
        s.SD_SESSION_IDLE = 2.0
        assert s.MM.sd_idle_s("image_edit") == max(2.0, float(sec["session_idle_s"])) and s.MM.sd_idle_s("image") == 2.0
        sec_idle = sec["session_idle_s"]
        sec["session_idle_s"] = 3                                # the test's window: 3 s (the OS: the manifest's)
        log0 = len(sd_log())
        r1 = await _improve(c, j["result"], "add a moon")
        j1 = await _job(c, r1["job"])
        assert j1["status"] == "done" and j1["version"] == 2 and r1["root"] == r["root"] and r1["from_version"] == 1, j1
        gpu = s.MM.gpu_status()
        assert gpu["kind"] == "image" and gpu["key"] == "image_edit" and gpu["name"] == s.MM.image_display_name("image_edit"), gpu
        r2 = await _improve(c, j1["result"], "")
        j2 = await _job(c, r2["job"])
        assert j2["status"] == "done" and j2["version"] == 3, j2
        log = sd_log()[log0:]
        assert sum(1 for k, _ in log if k == "LAUNCH") == 1 and sum(1 for k, _ in log if k == "REQ") == 2, log
        last = json.loads([x for k, x in log if k == "REQ"][-1])
        assert last["prompt"] == s.IMAGE_EDIT_DEFAULT, "an empty note = improve the quality, keep the content"
        assert base64.b64decode(last["extra_images"][0]) == (chain / "v2.png").read_bytes()
        t0 = time.time()
        while s.MM._sd_open is not None and time.time() - t0 < 10:
            await asyncio.sleep(0.1)
        assert s.MM._sd_open is None and time.time() - t0 > 1.5, "the window stays open ~3 s, then closes"
        while s.MM.gpu_status()["kind"] != "none" and time.time() - t0 < 20:    # the server stops, then R80 clears
            await asyncio.sleep(0.1)
        assert s.MM.gpu_status()["kind"] == "none", s.MM.gpu_status()
        sec["session_idle_s"] = sec_idle
    finally:
        s.SD_SESSION_IDLE = 0.0
        s.save_settings({k: v for k, v in s.load_settings().items() if k not in ("image_steps", "image_cfg")})
        await c.close()
    return "argv = DiT + --llm + --llm_vision + VAE + own schedule (no user tuning); extra_images = v0 at 832×576; v1 parent 0; " \
        "2nd/3rd ✨ joined the open session (1 launch, 2 requests); empty note = the default instruction; window closed"


@test("pe_kernel_sdcli_fallback_ref", ["R85", "R4"])
async def t_sdcli(ctx):
    """without bin/sd-server (the proven 2026-09-14 set), the edit is one sd-cli run with the same model flags + -r <the
    reference version> and the prompt"""
    s = kernel()
    link_sd_server(False)
    url = seed_picture(s, "cli.png", 512, 512)
    arg = Path(os.environ["MOCK_SD_ARGLOG"])
    n0 = len(arg.read_text().splitlines()) if arg.exists() else 0
    c = await _client(s)
    try:
        r = await _improve(c, url, "turn it into a watercolour")
        j = await _job(c, r["job"])
        assert j["status"] == "done" and j["engine"] == "sd-cli", j
        lines = arg.read_text().splitlines()[n0:]
        assert len(lines) == 1, lines
        a = lines[0].split("\t")
        assert a[a.index("-r") + 1] == str(s.image_chain_dir(r["root"]) / "v0.png") and "--llm_vision" in a
        assert a[a.index("-p") + 1] == "turn it into a watercolour" and (a[a.index("-W") + 1], a[a.index("-H") + 1]) == ("512", "512")
    finally:
        link_sd_server(True)
        await c.close()
    return "sd-cli: -r <chain>/v0.png, --llm_vision, the prompt, 512×512"


@test("pe_versions_revert_prune_evict", ["R85", "R67"])
async def t_versions(ctx):
    """the chain: a picture never improved answers itself as v0 (root null); ✨ makes v1 (v0 = a COPY of the original,
    which may be evicted on its own); ✨ from an older version records it as the parent; the revert moves `current`
    only (all files stay), an unknown version is a 404, a revert during an edit of the same chain a 409; opening the
    ORIGINAL again maps to the same chain; pruning keeps v0 + the current; eviction removes whole chains oldest first
    and never a chain an edit is working on"""
    s = kernel()
    url = seed_picture(s, "chain.png", 640, 640)
    c = await _client(s)
    with Painter(s) as P:
        try:
            async with c.get("/api/image/versions", params={"picture": url}) as r:
                v = await r.json()
            assert v["root"] is None and v["current"] == 0 and v["versions"] == [{"v": 0, "url": url, "by": "original"}], v
            r1 = await _improve(c, url, "warmer light")
            j1 = await _job(c, r1["job"])
            root = r1["root"]
            r2 = await _improve(c, j1["result"], "add snow")
            j2 = await _job(c, r2["job"])
            r3 = await _improve(c, j1["result"], "add rain")             # from v1 again: a sibling of v2
            j3 = await _job(c, r3["job"])
            assert (j1["version"], j2["version"], j3["version"]) == (1, 2, 3)
            idx = s.load_image_versions(root)
            assert [(x["v"], x.get("parent")) for x in idx["versions"]] == [(0, None), (1, 0), (2, 1), (3, 1)], idx
            assert [j["ref"].name for b in P.jobs for j in b] == ["v0.png", "v1.png", "v1.png"], P.jobs
            async with c.get("/api/image/versions", params={"picture": url}) as r:     # the original -> the same chain
                v = await r.json()
            assert v["root"] == root and v["current"] == 3 and v["url"].endswith("/v3.png") and len(v["versions"]) == 4, v
            async with c.post("/api/image/version", json={"root": root, "version": 1}) as r:
                rv = await r.json()
                assert r.status == 200 and rv["current"] == 1 and rv["url"].endswith("/v1.png"), rv
            assert all((s.image_chain_dir(root) / f"v{i}.png").is_file() for i in range(4)), "a revert deletes nothing"
            async with c.post("/api/image/version", json={"root": root, "version": 9}) as r:
                assert r.status == 404
            async with c.post("/api/image/version", json={"picture": j2["result"], "version": 0}) as r:
                assert r.status == 200 and (await r.json())["current"] == 0
            P.delay = 1.0
            r4 = await _improve(c, url, "slow one")
            await asyncio.sleep(0.3)
            async with c.post("/api/image/version", json={"root": root, "version": 2}) as r:
                assert r.status == 409, await r.text()
            j4 = await _job(c, r4["job"])
            assert j4["version"] == 4 and j4["current"] == 4
            P.delay = 0.0
            # the original file goes (evicted on its own): the chain keeps working from its copy
            (s.FILES_DIR / "images" / "chain.png").unlink()
            r5 = await _improve(c, j4["result"], "sharper")
            assert (await _job(c, r5["job"]))["version"] == 5
            # prune: keep 4 per chain — v0 and the current always stay
            keep = s.IMAGE_VERSIONS_KEEP
            s.IMAGE_VERSIONS_KEEP = 4
            cur = (await _job(c, (await _improve(c, f"/files/images/versions/{root}/v5.png", "x"))["job"]))["result"]
            idx = s.load_image_versions(root)
            s.IMAGE_VERSIONS_KEEP = keep
            vs = [x["v"] for x in idx["versions"]]
            assert vs == [0, 4, 5, 6] and idx["current"] == 6 and cur.endswith("/v6.png"), vs
            assert sorted(p.name for p in s.image_chain_dir(root).glob("v*.png")) == ["v0.png", "v4.png", "v5.png", "v6.png"]
            # eviction: chains are units; a locked chain stays
            other = seed_picture(s, "other.png", 256, 256)
            ro = (await _improve(c, other, "y"))["root"]
            await asyncio.sleep(0.4)
            old = time.time() - 7200
            for ch in (s.image_chain_dir(root), s.image_chain_dir(ro)):
                for f in ch.iterdir():
                    os.utime(f, (old, old))
                os.utime(ch, (old, old))
            (s.FILES_DIR / "images" / "other.png").unlink()
            lock = s._IMAGE_CHAIN_LOCKS.setdefault(ro, asyncio.Lock())
            await lock.acquire()
            saved = (s.MEDIA_CACHE_MAX, s.DISK_FREE_MIN)
            try:
                s.MEDIA_CACHE_MAX, s.DISK_FREE_MIN = 1, 0
                ev = s.evict_cache("test")
                assert not s.image_chain_dir(root).exists() and s.image_chain_dir(ro).exists(), ev
            finally:
                lock.release()
                s.MEDIA_CACHE_MAX, s.DISK_FREE_MIN = saved
            ev = s.evict_cache("test")       # unlocked now: the second chain goes too (whole)
            assert ev["evicted"] >= 0
            s.MEDIA_CACHE_MAX, s.DISK_FREE_MIN = 1, 0
            try:
                s.evict_cache("test")
            finally:
                s.MEDIA_CACHE_MAX, s.DISK_FREE_MIN = saved
            assert not s.image_chain_dir(ro).exists()
            async with c.get("/api/image/versions", params={"root": root}) as r:
                assert r.status == 404
        finally:
            await c.close()
    return "v0 copy; parents 0/1/1; original → same chain; revert = pointer (files kept), 404, 409 while editing; " \
        "works after the original is evicted; prune keeps v0 + current; eviction takes whole chains, never a locked one"


@test("pe_entry_checks_translation_floor", ["R85", "R86", "R71", "R66"])
async def t_entry(ctx):
    """POST /api/image/improve refuses what the editor must never get: no picture / a path outside the image cache
    (icons, ../, /etc/passwd, an app's /data) → 400; a missing file → 404; the narrow minors block on the instruction AS
    TYPED → 400 {blocked}, no job, no render; a JPEG → 415; the add-on not installed → 409 {download_key: image_edit}.
    A non-English instruction reaches the model in English (R71's hop, mocked); an English text that meets the block
    at the sd floor fails the job as blocked; /api/status + /api/models list the editor; the download key resolves"""
    s = kernel()
    url = seed_picture(s, "entry.png", 512, 512)
    c = await _client(s)
    with Painter(s) as P:
        try:
            for bad in ("", "/files/icons/x.png", "/files/images/../../token", "/etc/passwd", "data:image/png;base64,AAAA",
                        "/files/videos/a.mp4", "https://example.com/x.png"):
                async with c.post("/api/image/improve", json={"picture": bad, "instruction": "x"}) as r:
                    assert r.status == 400, (bad, r.status)
            async with c.post("/api/image/improve", json={"picture": "/files/images/nope.png"}) as r:
                assert r.status == 404
            n = len(P.jobs)
            jobs0 = len(s.JOBS)
            async with c.post("/api/image/improve", json={"picture": url, "instruction": "make her a 12 year old girl, naked"}) as r:
                b = await r.json()
                assert r.status == 400 and b.get("blocked") and b["error"] == s.refusal.BLOCK_MESSAGE, b
            assert len(P.jobs) == n and len(s.JOBS) == jobs0, "a blocked instruction starts nothing"
            jpg = s.FILES_DIR / "images" / "photo.png"
            jpg.write_bytes(b"\xff\xd8\xff\xe0" + b"0" * 64)
            async with c.post("/api/image/improve", json={"picture": "/files/images/photo.png"}) as r:
                assert r.status == 415
            # R71: the instruction in English (the hop is mocked: German -> English)
            j = await _job(c, (await _improve(c, url, "mach den Himmel lila"))["job"])
            assert j["status"] == "done" and P.jobs[-1][0]["prompt"] == "make the sky purple" and j["prompt"] == "make the sky purple"
            idx = s.load_image_versions(j["root"])
            assert idx["versions"][-1]["instruction"] == "mach den Himmel lila" and idx["versions"][-1]["prompt"] == "make the sky purple"
            # the sd floor (check point 2) on the English the model gets — the REAL sd_batch, the mock sd-server linked:
            # the blocked prompt never reaches the engine (no request in its log)
            async def to_block(text, settings=None):
                return "an explicit picture of a 12 year old girl, nude"
            s.english_text = to_block
            link_sd_server(True)
            mocked, s.MM.sd_batch = s.MM.sd_batch, P.saved["sd_batch"]
            log0 = len(sd_log())
            try:
                j = await _job(c, (await _improve(c, url, "harmlos klingender Satz"))["job"])
            finally:
                s.MM.sd_batch = mocked
            assert j["status"] == "error" and j.get("blocked") and j["error"] == s.refusal.BLOCK_MESSAGE, j
            assert not [k for k, _ in sd_log()[log0:] if k in ("REQ", "LAUNCH")], "the floor stops it before any engine"
            # the editor not installed: 409 with its download key
            f = s.MODELS_DIR / s.MANIFEST["image_edit"]["vision"]["file"]
            f.rename(f.with_name(f.name + ".away"))
            try:
                async with c.post("/api/image/improve", json={"picture": url, "instruction": "x"}) as r:
                    b = await r.json()
                    assert r.status == 409 and b["download_key"] == "image_edit" and "add-on" in b["error"], b
                async with c.get("/api/models") as r:
                    ex = {e["key"]: e for e in (await r.json())["extras"]}
                assert ex["image_edit"]["present"] is False and ex["image_edit"]["addon"] is True
            finally:
                f.with_name(f.name + ".away").rename(f)
            async with c.get("/api/status") as r:
                st = (await r.json())["image_edit"]
            assert st["available"] and st["download_key"] == "image_edit" and st["name"] == s.MANIFEST["image_edit"]["name"], st
            assert s.addon_section_key("image_edit") == "image_edit"
        finally:
            await c.close()
    return "400 ×7 paths, 404, 400 blocked (no job), 415, 409 + download key; German → English prompt; floor blocks the English; status/models list it"


# [review v7.5-2 F3] the review's probe 5, picture-edit half: an EDIT's words are only half of the request — the picture
# is the other half, which no text check reads — so an explicit under-18 age or word blocks on its own there
# (refusal.minor_age); negations, age gates, adult ages, sizes, times and counts pass. EDIT_COST: two SFW texts that
# name an explicit under-18 age themselves — the accepted cost (integrator, 2026-10-15), pinned so a change shows.
EDIT_BLOCK = ["make her a 15-year-old", "make him 12 years old", "make her underage", "sie soll 14 Jahre alt sein",
              "make her minderjährig", "make the woman a preteen", "15 yo", "aged 16"]
EDIT_COST = ["add a sign: under 16 players free", "make it a 4-year-old photo look"]
EDIT_PASS = ["add 3 candles", "make it 1080p", "set the clock to 12:30", "make the sign say 'under 18 not allowed'",
             "make her 25", "she is in her thirties", "18+ only label", "add a 16 GB label", "make the kids' drawing brighter",
             "make the dress 15 cm shorter", "no minors", "keep everyone an adult (25+)", "remove the 17 inch monitor",
             "Mach das Bild heller", "füge 2 Katzen hinzu", "make her look like she is 30", "turn the 12 roses red"]


@test("pe_r86_edit_age_alone_blocks", ["R86", "R85"])
async def t_age_alone(ctx):
    """[review v7.5-2 F3] picture ✨: an explicit under-18 age or word in the edit blocks on its own (refusal.minor_age)
    — at the entry (check point 1, as typed: 400 {blocked}, no job, no render) and at the sd floor for the image_edit
    kind (check point 2, the English the editor gets: the job fails blocked before any engine request). The review's
    gap case ("make her a 15-year-old" on an adult picture) blocks; a negation, an age gate and a plain edit render;
    outside a picture edit R86 keeps its pairing rule (minor_block: the age alone is no hit). Probe 5's table: 8 + 2
    (the accepted cost) block, 17 pass"""
    s = kernel()
    r = s.refusal
    bad = ([("must block", t) for t in EDIT_BLOCK + EDIT_COST if not r.minor_age(t)[0]]
           + [("must pass", t) for t in EDIT_PASS if r.minor_age(t)[0]])
    assert not bad, bad
    assert not r.minor_block("make her a 15-year-old")[0], "the age alone is no hit outside a picture edit"
    url = seed_picture(s, "adult-source.png", 512, 512)
    c = await _client(s)
    with Painter(s) as P:
        try:
            n, jobs0 = len(P.jobs), len(s.JOBS)
            for ins in ("make her a 15-year-old", "sie soll 14 Jahre alt sein"):
                async with c.post("/api/image/improve", json={"picture": url, "instruction": ins}) as rr:
                    b = await rr.json()
                    assert rr.status == 400 and b.get("blocked") and b["error"] == r.BLOCK_MESSAGE, (ins, rr.status, b)
            assert len(P.jobs) == n and len(s.JOBS) == jobs0, "a blocked edit starts nothing"
            for ins in ("make the sign say 'under 18 not allowed'", "keep everyone an adult (25+)", "add a red scarf"):
                j = await _job(c, (await _improve(c, url, ins))["job"])
                assert j["status"] == "done", (ins, j)
            assert len(P.jobs) == n + 3, "the three passing edits rendered"
            # check point 2: the hop yields the age alone; the REAL sd_batch (kind image_edit) refuses it, no engine
            async def to_age(text, settings=None):
                return "make her a 15-year-old"
            s.english_text = to_age                  # Painter restores the hop on exit
            link_sd_server(True)
            mocked, s.MM.sd_batch = s.MM.sd_batch, P.saved["sd_batch"]
            log0 = len(sd_log())
            try:
                j = await _job(c, (await _improve(c, url, "harmlos klingender Satz"))["job"])
            finally:
                s.MM.sd_batch = mocked
            assert j["status"] == "error" and j.get("blocked") and j["error"] == r.BLOCK_MESSAGE, j
            assert not [k for k, _ in sd_log()[log0:] if k in ("REQ", "LAUNCH")], "the floor stops it before any engine"
        finally:
            await c.close()
    return (f"minor_age: {len(EDIT_BLOCK)} + {len(EDIT_COST)} (cost) block, {len(EDIT_PASS)} pass; entry 400 blocked ×2 "
            "(no job); negation / age gate / plain edit rendered; sd floor (image_edit) blocks the age alone, no engine request")


# ============================================================================= e2e: the real shell in headless Chromium
@test("pe_e2e_viewer_improve_revert_wallpaper", ["R85", "R81", "R84", "R44"])
async def t_e2e(ctx):
    """the real shell at 1920×1080: a picture opens in the viewer (🖼, ✨ in the title bar, no dock icon); ✨ → the sticky
    note → 'make the sky purple' → the veil, then v1 shows and the strip reads v0 v1 (v1 current + shown); a second ✨
    → v2; a click on v0 in the strip makes the original the picture again (the kernel's current = 0); the quick viewer
    (imageQuick) becomes the viewer with ✨; the Imagine studio's gallery click opens the viewer; the menubar ✨ opens the
    wallpaper with the note, and the improved version becomes the wallpaper setting"""
    import test_dock_browser as db
    s = kernel()
    url = seed_picture(s, "seed.png", 1024, 576)
    with Painter(s, delay=0.6) as P:
        async with db.Desk(s, None) as page:
            await page.ev(f'openPicture({json.dumps(url)}, {{ title: "a harbour at dusk" }}), 1')
            win = "[...windows.values()].find((w) => w.pic && !w.pic.wallpaper)"
            await page.wait(f"{win} && {win}.pic.el.img.naturalWidth > 0", 10, "the picture")
            st = json.loads(await page.ev(f"JSON.stringify({{viewer: {win}.viewer, imp: getComputedStyle({win}.el.querySelector('.win-improve')).display, dock: [...document.querySelectorAll('#dock .dock-app')].map((b) => b.dataset.key)}})"))
            assert st["viewer"] is True and st["imp"] != "none", st
            await page.ev(f'(() => {{ const w = {win}; w.el.style.left = "380px"; w.el.style.top = "70px"; w.el.style.width = "900px"; w.el.style.height = "640px"; return 1; }})()')
            await page.shot("picture-viewer.png")
            await page.click_sel(".picture-win .win-improve")
            await page.wait('!!document.querySelector(".picture-win .sticky-note")', 5, "the sticky note")
            await page.click_sel(".picture-win .sticky-text")
            await page.type("make the sky purple")
            shot_note = await page.shot("picture-improve-note.png")
            await page.click_sel(".picture-win .sticky-go")
            await page.wait('!document.querySelector(".picture-win .pv-veil").classList.contains("hidden")', 5, "the veil")
            await page.shot("picture-improving.png")
            await page.wait(f"{win}.pic.url.endsWith('/v1.png') && {win}.pic.el.veil.classList.contains('hidden')", 15, "v1")
            await page.wait("document.querySelectorAll('.picture-win .pv-ver').length === 2", 5, "the strip")
            strip = await page.ev("[...document.querySelectorAll('.picture-win .pv-ver')].map((b) => b.className.replace('br-ver pv-ver', '').trim() + ':' + b.textContent)")
            assert strip == [":v0", "shown current:v1"], strip
            assert P.jobs[-1][0]["prompt"] == "make the sky purple" and P.jobs[-1][0]["ref"].name == "v0.png"
            await page.wait(f"{win}.pic.el.img.naturalWidth > 0", 5)
            shot_v1 = await page.shot("picture-improved-v1.png")
            await page.click_sel(".picture-win .win-improve")
            await page.wait('!!document.querySelector(".picture-win .sticky-note")', 5)
            await page.click_sel(".picture-win .sticky-text")
            await page.type("add a full moon")
            await page.key("Enter")
            await page.wait(f"{win}.pic.url.endsWith('/v2.png') && document.querySelectorAll('.picture-win .pv-ver').length === 3", 15, "v2")
            assert P.jobs[-1][0]["ref"].name == "v1.png"
            await page.shot("picture-improved-v2.png")
            await page.click_sel('.picture-win .pv-ver[data-v="0"]')
            await page.wait(f"{win}.pic.url.endsWith('/v0.png') && {win}.pic.versions.current === 0", 10, "the revert")
            strip = await page.ev("[...document.querySelectorAll('.picture-win .pv-ver')].map((b) => b.className.replace('br-ver pv-ver', '').trim() + ':' + b.textContent)")
            assert strip == ["shown current:v0", ":v1", ":v2"], strip
            root = await page.ev(f"{win}.pic.root")
            assert s.load_image_versions(root)["current"] == 0
            await page.shot("picture-reverted-v0.png")
            # the quick viewer becomes the viewer (render_images mocked: it paints a gradient)
            saved = s.render_images

            async def paint(kind, items, adult=False, expand=None):
                for it in items:
                    Path(it["out"]).parent.mkdir(parents=True, exist_ok=True)
                    Path(it["out"]).write_bytes(png(int(it["w"] or 512), int(it["h"] or 512), (90, 30, 140), (250, 190, 90)))
                return [{"ok": True, "path": it["out"], "prompt": it["prompt"], "expanded": False, "expander": None,
                         "seed": 7, "engine": "sd-server", "error": None} for it in items]
            s.render_images = paint
            try:
                await page.ev('imageQuick("a lighthouse in a storm"), 1')
                q = "[...windows.values()].find((w) => w.pic && w.title.includes('lighthouse'))"
                await page.wait(f"!!{q} && {q}.pic.el.img.naturalWidth > 0", 15, "the quick viewer's picture")
                assert await page.ev(f"getComputedStyle({q}.el.querySelector('.win-improve')).display") != "none"
                # the Imagine studio: its gallery posts {kairosPicture}; only that built-in may open the viewer (the
                # message is dispatched with the studio's frame as its source — the frame itself is cross-origin)
                im = seed_picture(s, "imagine.png", 768, 768, (200, 60, 90), (20, 40, 120))
                await page.ev('openStudio("imagine"), 1')
                await page.wait("[...windows.values()].some((w) => w.builtin === 'imagine' && w.iframe)", 5)
                post = ("(() => {{ const w = [...windows.values()].find((w) => {who}); window.dispatchEvent(new MessageEvent("
                        "'message', {{data: {{kairosPicture: {u}, title: 'from imagine'}}, source: w.iframe.contentWindow}})); return 1; }})()")
                await page.ev(post.format(who="w.title.includes('lighthouse') === false && w.pic && !w.pic.wallpaper", u=json.dumps(im)).replace(
                    "w.iframe.contentWindow", "window"))     # a message from anything else: ignored
                await asyncio.sleep(0.3)
                assert not await page.ev("[...windows.values()].some((w) => w.title.includes('from imagine'))"), "only the studio"
                await page.ev(post.format(who="w.builtin === 'imagine'", u=json.dumps(im)))
                await page.wait("[...windows.values()].some((w) => w.pic && w.title.includes('from imagine'))", 5, "Imagine → viewer")
            finally:
                s.render_images = saved
            # the wallpaper: ✨ in the menubar
            wp = seed_picture(s, "wall.png", 1024, 576, (10, 40, 70), (120, 200, 230))
            s.save_settings({**s.load_settings(), "wallpaper": wp})
            await page.ev(f"applyWallpaper({json.dumps(wp)}), 1")
            await page.click_sel("#mb-wallpaper-improve")
            ww = "[...windows.values()].find((w) => w.pic && w.pic.wallpaper)"
            await page.wait(f"!!{ww} && !!{ww}.el.querySelector('.sticky-note')", 8, "the wallpaper's note")
            await page.click_sel(".picture-win .sticky-note .sticky-text")
            await page.type("make it a snowy night")
            await page.key("Enter")
            await page.wait(f"{ww}.pic.url.endsWith('/v1.png')", 15, "the wallpaper's v1")
            await page.wait("(document.querySelector('#wallpaper').style.backgroundImage || '').includes('/v1.png')", 8)
            assert s.load_settings()["wallpaper"].endswith("/v1.png")
    return "viewer ✨ → note → veil → v1 (strip v0 v1) → v2 → v0 revert; quick viewer + Imagine open the viewer; wallpaper ✨ → v1 = wallpaper" \
        + (f"; shots {shot_note}, {shot_v1}" if SHOTS else "")


def main():
    tests = [v for v in globals().values() if getattr(v, "_test", False)]
    only = [x.strip() for x in os.environ.get("KAIROS_TEST_ONLY", "").split(",") if x.strip()]
    if only:
        tests = [t for t in tests if any(o in t._name for o in only)]
    print(f"kAIrOS ✨ improve a picture tests (R85) — {len(tests)} tests", flush=True)
    for t in tests:
        t({})
    if not only:
        out = {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "tests": RESULTS}
        (REPO / "tests" / "results-picture-edit.json").write_text(json.dumps(out, indent=1))
    fails = [n for n, r in RESULTS.items() if r["status"] == "fail"]
    passes = sum(1 for r in RESULTS.values() if r["status"] == "pass")
    skips = sum(1 for r in RESULTS.values() if r["status"] == "skip")
    print(f"\n{passes} passed, {len(fails)} failed, {skips} skipped -> tests/results-picture-edit.json", flush=True)
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
