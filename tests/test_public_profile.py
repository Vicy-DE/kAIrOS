#!/usr/bin/env python3
"""kAIrOS R87 — the public images (docs/PUBLIC-IMAGES-2026-10-16.md): the build-time profile, the first-boot downloads,
Muse with the user's own civitai key, the licences, the image prep script.

Standalone: `python3 tests/test_public_profile.py` (run-tests.sh runs it after the suite). Writes
tests/results-public-profile.json (not for a KAIROS_TEST_ONLY subset).
* unit: kernel/public.py's Profile on the REAL tier manifests (private = the very object, untouched; public without Muse
  = Z-Image-Turbo with Muse's prompt rules; public with Muse on disk = the tier's own sections);
* the PRIVATE kernel in a subprocess (no profile file): the manifest it serves == the tier JSON, no Muse alias, no
  downloads — the owner's images as today;
* the PUBLIC kernel in-process (a private VIBEOS_DIR, KAIROS_PROFILE=public, a copy of tier-8gb + the overlay whose
  URLs point at a local mock server, the suite's mock sd-cli): the default resolves to Z-Image, the menubar names it,
  picture ✨ works on its pictures; every feature says "… needs a download / is downloading" while its file is missing
  (never a traceback); the first-boot download with aria2c from the mock — paused, offline, resumed with Range,
  sha256-checked (a wrong file is refused); the civitai-key flow against a mock civitai + a mock CDN on another port
  (the key only ever reaches civitai's origin; never a log, a response or a file but its 0600 store);
* the Licences view (API + traversal) and the panel in a headless Chromium (About → Licences, the ⬇ chip, the
  first-start dialog, the key field) — skips without Chromium;
* scripts/public-image-prep.sh on planted-secrets trees (verify fails on each, prepare fixes them, a second prepare
  changes nothing, unfixable leaks stay FAIL) and scripts/fetch-licence-sources.sh against the mock server.
No test leaves loopback: aria2c and the kernel only ever talk to 127.0.0.1.
"""
import asyncio
import contextlib
import hashlib
import io
import json
import os
import re
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RESULTS = {}
SHOTS = os.environ.get("KAIROS_SHOTS", "")
KEY = "kAIrOStestKEY0123456789abcdefXYZ"           # a fake civitai key: must never leave its store
WRONG_KEY = "wrongKEY0123456789abcdefghijklmn"
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
                    detail = asyncio.run(asyncio.wait_for(detail, timeout=300))
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


def overlay():
    return json.loads((REPO / "manifests" / "profile-public.json").read_text(encoding="utf-8"))


def blob(name: str, size: int) -> bytes:
    """deterministic test content for a model file"""
    seed = hashlib.sha256(name.encode()).digest()
    return (seed * (size // len(seed) + 1))[:size]


def walk_files(node):
    out = []

    def c(n):
        if isinstance(n, dict):
            if "url" in n and "file" in n:
                out.append(n)
            for v in n.values():
                c(v)
    c(node)
    return out


# ----------------------------------------------------------------------------- the mock server (files, civitai, CDN)
class Mock:
    """one origin on 127.0.0.1: files with Range, an optional civitai download route that wants the key and answers
    307 to another origin, a throttle, a log of every request (path, headers)"""

    def __init__(self, port=0):
        self.port, self.files, self.rate, self.log, self.auth, self.httpd, self.stopping = port, {}, None, [], None, None, False

    def start(self):
        mock = self
        self.stopping = False

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def _send(self, code, body=b"", headers=None):
                self.send_response(code)
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(body)

            def do_HEAD(self):
                self.do_GET()

            def do_GET(self):
                mock.log.append({"path": self.path, "headers": dict(self.headers)})
                path, _, query = self.path.partition("?")
                if mock.auth and path == mock.auth["path"]:
                    good = self.headers.get("Authorization") == f"Bearer {mock.auth['key']}" \
                        or f"token={mock.auth['key']}" in query.split("&")
                    if not good:
                        return self._send(401, b'{"error":"Unauthorized","message":"The creator of this asset requires you to be logged in to download it"}',
                                          {"Content-Type": "application/json"})
                    return self._send(307, b"", {"Location": mock.auth["redirect"]})
                data = mock.files.get(path)
                if data is None:
                    return self._send(404, b"not found")
                start, code = 0, 200
                m = re.match(r"bytes=(\d+)-", self.headers.get("Range") or "")
                if m:
                    start = int(m.group(1))
                    if start >= len(data):
                        return self._send(416, b"", {"Content-Range": f"bytes */{len(data)}"})
                    code = 206
                body = data[start:]
                self.send_response(code)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Type", "application/octet-stream")
                if code == 206:
                    self.send_header("Content-Range", f"bytes {start}-{len(data) - 1}/{len(data)}")
                self.end_headers()
                if self.command == "HEAD":
                    return
                step = 65536
                for i in range(0, len(body), step):
                    if mock.stopping:            # the plug is pulled: drop the connection (no keep-alive)
                        self.close_connection = True
                        try:
                            self.connection.shutdown(socket.SHUT_RDWR)
                        except OSError:
                            pass
                        return
                    try:
                        self.wfile.write(body[i:i + step])
                    except (BrokenPipeError, ConnectionResetError):
                        return
                    if mock.rate:
                        time.sleep(step / mock.rate)

        ThreadingHTTPServer.allow_reuse_address = True
        self.httpd = ThreadingHTTPServer(("127.0.0.1", self.port), H)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        return self

    def stop(self):
        self.stopping = True
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None

    def url(self, path):
        return f"http://127.0.0.1:{self.port}{path}"


# ----------------------------------------------------------------------------- the public kernel, in-process
_K = {}
# the files the public test kernel starts with / without (the rest of the essentials download from the mock)
MISSING = ("image/z_image_turbo-Q4_0.gguf", "image/Qwen3-4B-Instruct-2507-Q4_K_M.gguf", "image/ae.safetensors",
           "image/Qwen3VL-4B-Instruct-Q4_K_M.gguf", "wikipedia_en_all_mini_2026-06.zim")
SIZES = {"image/z_image_turbo-Q4_0.gguf": 3_000_000, "image/Qwen3-4B-Instruct-2507-Q4_K_M.gguf": 2_200_000,
         "image/ae.safetensors": 600_000, "image/Qwen3VL-4B-Instruct-Q4_K_M.gguf": 1_500_000,
         "wikipedia_en_all_mini_2026-06.zim": 2_500_000, "image/museByStableYogi_v35Q4Extended.gguf": 3_500_000,
         "image/qwen_image_vae.safetensors": 400_000}


def kernel():
    if "server" in _K:
        return _K["server"]
    try:
        import aiohttp  # noqa: F401
    except ImportError:
        raise SkipTest("aiohttp not installed (run in the testrig container)")
    if not shutil.which("aria2c"):
        raise SkipTest("aria2c not installed (kairos-fix:local has it)")
    d = Path(tempfile.mkdtemp(prefix="kairos-pp-"))
    files_mock = Mock().start()
    cdn = Mock().start()
    civitai = Mock().start()
    _K.update(dir=d, files=files_mock, cdn=cdn, civitai=civitai)
    # the test manifests: tier-8gb + the overlay, every essential / Muse file re-pointed at the mock with its content
    man_dir = d / "manifests"
    man_dir.mkdir()
    m, ov = manifest(), overlay()
    content = {}

    def point(e):
        size = SIZES.get(e["file"], 4096)
        data = blob(e["file"], size)
        content[e["file"]] = data
        e.update(url=files_mock.url("/f/" + e["file"]), size_bytes=size, size_gb=round(size / 1e9, 6),
                 sha256=hashlib.sha256(data).hexdigest())
        e.pop("sha512", None)
    for p in ("llm", "image_classic", "translation", "dictionaries", "image", "image_safe", "vision", "image_edit"):
        for e in walk_files(m[p]):
            point(e)
    for e in walk_files(ov["sections"]):        # the ZIM (vision_encoder copies the tier's image.text_encoder)
        point(e)
    ov["sha256"] = {f: hashlib.sha256(content[f]).hexdigest() for f in ov["sha256"] if f in content}
    mus = m["image"]["diffusion"]                 # Muse: the civitai file (its URL stays civitai's, KAIROS_MUSE_URL = the mock)
    mus["url"] = "https://civitai.com/api/download/models/3258948"
    m["image_safe"]["diffusion"] = dict(mus)
    (man_dir / "tier-8gb.json").write_text(json.dumps(m, indent=1, ensure_ascii=False) + "\n")
    (man_dir / "profile-public.json").write_text(json.dumps(ov, indent=1, ensure_ascii=False) + "\n")
    for f, data in content.items():
        files_mock.files["/f/" + f] = data
    cdn.files["/cdn/muse.gguf"] = content[mus["file"]]
    civitai.auth = {"path": "/api/download/models/3258948", "key": KEY, "redirect": cdn.url("/cdn/muse.gguf?sig=cdn-signature-xyz")}
    # what is on disk at the start: every file but the MISSING ones (and never Muse, never its VAE)
    (d / "bin").mkdir()
    os.symlink(REPO / "tests" / "mock-sd-cli", d / "bin" / "sd-cli")
    subprocess.run([sys.executable, str(REPO / "scripts" / "engine-stamp.py"), "--mock", "--out", str(d / "bin" / "ENGINES.json")],
                   check=True, capture_output=True)
    for f, data in content.items():
        if f in MISSING or f in (mus["file"], "image/qwen_image_vae.safetensors"):
            continue
        dest = (d / "knowledge" if f.endswith(".zim") else d / "models") / f
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
    (d / "knowledge").mkdir(exist_ok=True)
    os.environ.update(VIBEOS_DIR=str(d), VIBEOS_MANIFEST=str(man_dir / "tier-8gb.json"), VIBEOS_MANIFESTS=str(man_dir),
                      VIBEOS_SHELL=str(REPO / "shell"), VIBEOS_FORCE_OFFLINE="1", KAIROS_LOG_DIR=str(d / "log"),
                      VIBEOS_TOKEN_FILE=str(d / "token"), KAIROS_PROFILE="public", KAIROS_SECRETS_DIR=str(d / "secrets"),
                      KAIROS_DL_BACKOFF="0.3,0.6,1", KAIROS_ESSENTIALS_AUTOSTART="0", VIBEOS_SD_PORT="8161",
                      KAIROS_ARIA2_DISK_CACHE="0",
                      KAIROS_MUSE_URL=civitai.url("/api/download/models/3258948"), VIBEOS_SD_SESSION_IDLE="0",
                      MOCK_SD_ARGLOG=str(d / "sd-args.log"))
    os.environ.pop("KAIROS_CIVITAI_AUTH", None)
    sys.path.insert(0, str(REPO / "kernel"))
    import server
    _K["server"] = server
    return server


async def _client(s):
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer
    app = web.Application(middlewares=[s.error_log_middleware, s.engine_missing_middleware])
    s.add_routes(app)
    c = TestClient(TestServer(app))
    await c.start_server()
    return c


async def _json(c, method, path, body=None, status=None, seen=None):
    async with (c.post(path, json=body) if method == "POST" else c.get(path)) as r:
        text = await r.text()
        if seen is not None:
            seen.append(text)
        if status is not None:
            assert r.status == status, (path, r.status, text[:400])
        try:
            return r.status, json.loads(text)
        except ValueError:
            return r.status, text


async def _job(c, jid, timeout=40):
    t0 = time.time()
    while time.time() - t0 < timeout:
        _, j = await _json(c, "GET", f"/api/job/{jid}")
        if j.get("status") in ("done", "error"):
            return j
        await asyncio.sleep(0.1)
    raise AssertionError(f"job {jid} never finished")


async def _until(fn, timeout=40, what=""):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = fn()
        if v:
            return v
        await asyncio.sleep(0.1)
    raise AssertionError(f"timed out waiting for {what}")


def reset_downloads(s):
    s.ESS.paused, s.ESS.task, s.ESS.state = False, None, "idle"
    s.ESS.errors.clear()
    s.ESS.attempts.clear()
    s.ESS._save()


# ============================================================================= files + wiring
@test("pp_static_overlay_and_wiring", ["R87", "R47", "R3"])
def t_static(ctx):
    """the overlay (manifests/profile-public.json) is byte-stable JSON; Muse is never an essential and its only URL is
    civitai's (the user's own key); every essential URL is anonymous (Hugging Face, kiwix, FreeDict, TU Chemnitz) with a
    sha256 (dictionaries: sha256 + sha512); the owner's tier manifests are untouched (Muse still `image`); the shell loads
    models.js after pictures.js, has the ⬇ chip, About → Licences, the bridge's vibe.system.models() handled in both host
    pages, the Settings app's button; the kernel registers the routes guarded where they change the machine"""
    raw = (REPO / "manifests" / "profile-public.json").read_text(encoding="utf-8")
    ov = json.loads(raw)
    assert raw == json.dumps(ov, indent=1, ensure_ascii=False) + "\n", "not byte-stable"
    assert ov["profile"] == "public" and ov["image_default"]["from"] == "image_classic" and ov["image_safe"]["expander"] == "krea"
    assert ov["image_default"]["expander"] == "krea-neutral"
    assert [e["key"] for e in ov["essentials"]] == ["llm", "image", "translation", "dictionaries", "vision_encoder", "knowledge"]
    assert ov["muse"]["auth"]["url"] == "https://civitai.com/api/download/models/3258948" and ov["muse"]["auth"]["mode"] == "header"
    assert any("Krea 2 is licensed under the Krea 2 Community License Agreement. For more information, visit https://krea.ai/krea-2-licensing." in l
               for l in ov["muse"]["licence_summary"]) and any("Stable Yogi" in l for l in ov["muse"]["licence_summary"])
    n, unpinned = 0, []
    for t in ("8gb", "16gb", "24gb", "32gb"):
        m = manifest(t)
        assert m["image"]["name"].startswith("Muse (") and m["image"]["diffusion"]["url"].endswith("/3258948"), t
        for it in ov["essentials"]:
            for p in it["paths"]:
                node = ov["sections"].get(p) if p in ov["sections"] else m
                src = (node or {}).get("from") if p in ov["sections"] else p
                if src:                            # a tier entry (or an overlay section copying one)
                    node = m
                    for part in src.split("."):
                        node = node[part]
                for e in walk_files(node):
                    n += 1
                    assert "civitai" not in e["url"], (t, it["key"], e["file"])
                    assert re.match(r"https://(huggingface\.co|download\.kiwix\.org|download\.freedict\.org|ftp\.tu-chemnitz\.de)/", e["url"]), e["url"]
                    pinned = re.fullmatch(r"[0-9a-f]{64}", e.get("sha256") or ov["sha256"].get(e["file"], ""))
                    if t == "8gb":                 # the public images' tier: every first-boot file is sha256-pinned
                        assert pinned, (t, e["file"])
                    elif not pinned:
                        unpinned.append(f"{t}:{e['file']}")
    html = (REPO / "shell" / "index.html").read_text()
    js = (REPO / "shell" / "shell.js").read_text()
    host = (REPO / "shell" / "bench" / "host.js").read_text()
    mj = (REPO / "shell" / "models.js").read_text()
    assert html.index('src="/shell/pictures.js"') < html.index('src="/shell/models.js"'), "models.js after pictures.js"
    assert 'id="mb-dl"' in html and "A Models button: vibe.system.models()." in html
    bridge = re.search(r"const BRIDGE_SRC = `(.*?)`;\n", js, re.S).group(1)
    assert 'models: (section) => call("panel", section || "downloads")' in bridge
    assert 'if (method === "panel")' in js and 'if (method === "panel")' in host
    assert "kairosModels.open(\"licences\")" in js and "sm-about-lic" in js and "kairosModels.status(s)" in js \
        and 'kairosModels.firstStart(s)' in js
    for needle in ('"/api/muse/key"', '"/api/muse/download"', '"/api/downloads"', "/api/licences", 'input.value = "";',
                   'type: "password"', "Add Muse (needs a free civitai account): paste your API key", "textContent"):
        assert needle in mj, needle
    assert "innerHTML" not in mj, "the panel never writes server strings as HTML"
    src = (REPO / "kernel" / "server.py").read_text()
    for r in ('add_post("/api/downloads", guarded(h_downloads_ctl))', 'add_post("/api/muse/key", guarded(h_muse_key))',
              'add_post("/api/muse/download", guarded(h_muse_download))', 'add_get("/api/licences", h_licences)'):
        assert r in src, r
    # the Settings sheet stays as it was (the dock prompt's budget has ~60 chars of room: the prompt names the call);
    # API.md documents it
    assert "vibe.system.models(section?)" in (REPO / "docs" / "API.md").read_text(encoding="utf-8")
    return (f"overlay byte-stable; {n} essential entries anonymous over 4 tiers (8gb all sha256-pinned; larger tiers unpinned: "
            f"{len(unpinned)}); Muse only via civitai; shell + bridge + routes wired")


@test("pp_profile_unit_real_manifests", ["R87", "R76", "R70"])
def t_unit(ctx):
    """kernel/public.py on the REAL tier manifests: the private profile returns the very same object (byte-identical);
    the public one without Muse makes `image` = the tier's image_classic files + krea-neutral (expander_parallel 2, no
    `addon`), `image_safe` = the same + the SFW `krea`, adds `image_muse` (= the tier's Muse, an add-on with the civitai
    auth + licence summary), `vision_encoder` (vision.weights point at it) and the ZIM entry, fills the missing sha256;
    with Muse's three files on disk (sparse, right size) `image` / `image_safe` are the tier's own and there is no
    `image_muse`; profile_name() reads KAIROS_PROFILE / the file, and anything but "public" is private"""
    if "kernel" not in sys.path[0]:
        sys.path.insert(0, str(REPO / "kernel"))
    try:
        import public
    except ImportError as e:
        raise SkipTest(f"kernel/public.py needs aiohttp ({e})")
    d = Path(tempfile.mkdtemp(prefix="kairos-pp-unit-"))
    try:
        for t in ("8gb", "16gb", "24gb", "32gb"):
            m = manifest(t)
            before = json.dumps(m, sort_keys=True)
            priv = public.Profile(REPO / "manifests", d / "models", d / "knowledge", name="private")
            assert priv.apply(m) is m and json.dumps(m, sort_keys=True) == before, t
            pub = public.Profile(REPO / "manifests", d / "models", d / "knowledge", name="public")
            e = pub.apply(m)
            assert e is not m and json.dumps(m, sort_keys=True) == before, f"{t}: the tier manifest must not change"
            cl = m["image_classic"]
            for k in ("diffusion", "text_encoder", "vae", "gen_args", "max_pixels"):
                assert e["image"][k] == cl[k] and e["image_safe"][k] == cl[k], (t, k)
            assert e["image"]["expander"] == "krea-neutral" and e["image_safe"]["expander"] == "krea" and "addon" not in e["image"]
            assert e["image"]["name"] == cl["name"] and e["image_safe"]["name"].startswith("Z-Image-Turbo SFW (")
            assert e["image_muse"]["diffusion"] == m["image"]["diffusion"] and e["image_muse"]["addon"] is True \
                and e["image_muse"]["auth"]["type"] == "civitai" and e["image_muse"]["licence_summary"]
            assert e["vision"]["weights"] == {"shared_with": "vision_encoder"} and e["vision_encoder"]["file"] == m["image"]["text_encoder"]["file"] \
                and e["vision_encoder"]["url"] == m["image"]["text_encoder"]["url"] and e["vision_encoder"]["addon"] is True, t
            if t == "8gb":
                assert e["image_muse"]["text_encoder"]["sha256"].startswith("66358cb1") and e["vision_encoder"]["sha256"].startswith("66358cb1")
            assert e["image_muse"]["vae"]["sha256"].startswith("a70580f0")
            assert e["knowledge_zim"]["store"] == "knowledge" and e["knowledge_zim"]["sha256"].startswith("1d0f8178")
            # Muse on disk (sparse files of the right size)
            for f in walk_files(m["image"]):
                p = d / "models" / f["file"]
                p.parent.mkdir(parents=True, exist_ok=True)
                with open(p, "wb") as fh:
                    fh.truncate(f["size_bytes"])
            e2 = pub.apply(m)
            nosha = lambda x: json.loads(re.sub(r',\s*"sha256": "[0-9a-f]{64}"', "", json.dumps(x)))   # the overlay only adds pins
            assert nosha(e2["image"]) == nosha(m["image"]) and nosha(e2["image_safe"]) == nosha(m["image_safe"]) \
                and "image_muse" not in e2 and e2["image"]["diffusion"] == m["image"]["diffusion"], t
            assert pub.muse_installed() is True
            shutil.rmtree(d / "models")
            # the essentials of the public profile
            items = pub.essential_items(pub.apply(m))
            assert [i["key"] for i in items] == ["llm", "image", "translation", "dictionaries", "vision_encoder", "knowledge"]
            assert [x["file"] for x in items[1]["entries"]] == [cl["diffusion"]["file"], cl["text_encoder"]["file"], cl["vae"]["file"]]
            assert len(items[3]["entries"]) == 38 and items[2]["entries"][0]["file"].endswith("Index-Translate-2B.Q4_K_M.gguf")
        old = os.environ.pop("KAIROS_PROFILE", None)
        try:
            pf = d / "profile"
            for text, want in (("public\n", "public"), ("PUBLIC", "public"), ("private", "private"), ("publicx", "private"),
                               ("", "private")):
                pf.write_text(text)
                public.PROFILE_FILE = pf
                assert public.profile_name() == want, (text, want)
            public.PROFILE_FILE = d / "missing"
            assert public.profile_name() == "private"
            os.environ["KAIROS_PROFILE"] = "public"
            assert public.profile_name() == "public"
        finally:
            public.PROFILE_FILE = Path("/etc/kairos/profile")
            if old is None:
                os.environ.pop("KAIROS_PROFILE", None)
            else:
                os.environ["KAIROS_PROFILE"] = old
    finally:
        shutil.rmtree(d, ignore_errors=True)
    return "4 tiers: private = same object; public = Z-Image + krea-neutral/krea, image_muse (civitai), vision_encoder, ZIM; Muse on disk = tier's own; profile_name"


@test("pp_private_kernel_unchanged", ["R87", "R76"])
def t_private(ctx):
    """the PRIVATE kernel (no profile file, the real tier-8gb, a subprocess): PROFILE private, MANIFEST == the tier JSON,
    IMAGE_MODEL_KEYS without `muse`, no essentials, no note for any section (ESS.note None), /api/downloads complete with
    no items, /api/muse unavailable, POST /api/muse/key and /api/downloads 409 — the owner's images exactly as before"""
    try:
        import aiohttp  # noqa: F401
    except ImportError:
        raise SkipTest("aiohttp not installed")
    d = Path(tempfile.mkdtemp(prefix="kairos-pp-priv-"))
    code = r"""
import json, os, sys, asyncio
sys.path.insert(0, os.environ["R"] + "/kernel")
import server
from aiohttp.test_utils import TestClient, TestServer
from aiohttp import web
m = json.loads(open(os.environ["R"] + "/manifests/tier-8gb.json").read())
out = {"profile": server.PROFILE.name, "same": server.MANIFEST == m, "muse": "muse" in server.IMAGE_MODEL_KEYS,
       "items": server.ESS.items(), "note": server.ESS.note(server.section_files(server.MANIFEST["image"])),
       "image": server.MANIFEST["image"]["name"], "vision": server.MANIFEST["vision"]["weights"]}
async def main():
    app = web.Application(); server.add_routes(app)
    c = TestClient(TestServer(app)); await c.start_server()
    for path in ("/api/downloads", "/api/muse"):
        async with c.get(path) as r: out[path] = await r.json()
    for path, body in (("/api/muse/key", {"key": "x" * 32}), ("/api/downloads", {"action": "start"})):
        async with c.post(path, json=body) as r: out["POST " + path] = r.status
    async with c.get("/api/models") as r:
        j = await r.json(); out["models_profile"] = j.get("profile"); out["has_downloads"] = "downloads" in j
    await c.close()
asyncio.run(main())
print("RESULT " + json.dumps(out))
"""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("KAIROS_PROFILE", "KAIROS_MUSE", "KAIROS_SECRETS"))}
    env.update(R=str(REPO), VIBEOS_DIR=str(d), VIBEOS_MANIFEST=str(REPO / "manifests" / "tier-8gb.json"),
               VIBEOS_MANIFESTS=str(REPO / "manifests"), VIBEOS_SHELL=str(REPO / "shell"), VIBEOS_FORCE_OFFLINE="1",
               KAIROS_LOG_DIR=str(d / "log"), VIBEOS_TOKEN_FILE=str(d / "token"), KAIROS_PROFILE_FILE=str(d / "no-profile"))
    try:
        r = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=120)
        line = next((l for l in r.stdout.splitlines() if l.startswith("RESULT ")), None)
        assert line, (r.stdout[-800:], r.stderr[-1500:])
        o = json.loads(line[7:])
    finally:
        shutil.rmtree(d, ignore_errors=True)
    assert o["profile"] == "private" and o["same"] is True and o["muse"] is False and o["items"] == [] and o["note"] is None, o
    assert o["image"].startswith("Muse (") and o["vision"] == {"shared_with": "image.text_encoder"}
    assert o["/api/downloads"]["complete"] is True and o["/api/downloads"]["items"] == [] and o["/api/muse"]["available"] is False
    assert o["POST /api/muse/key"] == 409 and o["POST /api/downloads"] == 409 and o["models_profile"] == "private" and not o["has_downloads"]
    return "private: MANIFEST == tier JSON, Muse default, no muse alias, no downloads; key/download routes 409"


@test("pp_public_resolution_and_menubar", ["R87", "R76", "R80"])
async def t_resolution(ctx):
    """the PUBLIC kernel: `image` resolves to Z-Image-Turbo (default, uncensored, adult), `safe` to its SFW section;
    the R80 menubar names it (image_display_name 'Z-Image-Turbo' / 'Z-Image-Turbo SFW'); /api/status says profile
    public, image + image_model = Z-Image, a downloads summary; /api/models lists default (baked, present false while
    its files are missing, the 'needs a download' note), safe, and muse (auth civitai, needs_key); the setting accepts
    'muse' and, Muse not installed, renders with the default"""
    s = kernel()
    assert s.PROFILE.public and s.MANIFEST["image"]["name"] == "Z-Image-Turbo (Q4_0)" and "image_muse" in s.MANIFEST
    for body, want in (({}, "image"), ({"uncensored": True}, "image"), ({"adult": True}, "image"), ({"model": "safe"}, "image_safe"),
                       ({"model": "muse"}, "image"), ({"model": "classic"}, "image")):
        got = s.resolve_image_kind(body)
        assert got == want, (body, got)
    assert s.MM.image_display_name("image") == "Z-Image-Turbo" and s.MM.image_display_name("image_safe") == "Z-Image-Turbo SFW"
    assert s.expander_for("image") == "krea-neutral" and s.expander_for("image", adult=True) == "krea-18plus" \
        and s.expander_for("image_safe") == "krea"
    assert s.validate_setting("image_model", "muse") is None
    c = await _client(s)
    try:
        _, st = await _json(c, "GET", "/api/status", status=200)
        assert st["profile"] == "public" and st["image"] == "Z-Image-Turbo (Q4_0)" and st["image_model"]["name"] == "Z-Image-Turbo (Q4_0)"
        assert st["downloads"]["complete"] is False and st["downloads"]["muse"] == "idle", st["downloads"]
        _, mi = await _json(c, "GET", "/api/models", status=200)
        by = {m["key"]: m for m in mi["models"]}
        assert by["default"]["name"] == "Z-Image-Turbo (Q4_0)" and by["default"]["addon"] is False and by["default"]["present"] is False \
            and "needs a download" in by["default"]["note"], by["default"]
        assert by["muse"]["auth"] == "civitai" and by["muse"]["needs_key"] is True and by["muse"]["present"] is False \
            and by["muse"]["name"].startswith("Muse (") and by["safe"]["name"].startswith("Z-Image-Turbo SFW")
        assert mi["profile"] == "public" and len(mi["downloads"]["items"]) == 6 and mi["muse"]["available"] is True
    finally:
        await c.close()
    return "image -> Z-Image (default/uncensored/adult/muse), safe -> Z-Image SFW; menubar 'Z-Image-Turbo'; models: default + safe + muse(civitai)"


@test("pp_missing_models_say_so", ["R87", "R47", "R70", "R71"])
async def t_missing(ctx):
    """while its files are missing every feature answers a sentence, never a traceback: /api/image + /api/images +
    /api/wallpaper 503 'the picture model needs a download (… GB) — start it in ⚙ Models & licences' (no job, no
    engine call), the sd floor (sd_batch) the same per picture, vision names the vision encoder, /api/search carries a
    knowledge_note, /api/models/download default = the essential (not an add-on 400), muse = 409 civitai; after a
    pause the sentence says 'paused'"""
    s = kernel()
    reset_downloads(s)
    c = await _client(s)
    try:
        for path, body in (("/api/image", {"prompt": "a lighthouse at dusk"}), ("/api/images", {"prompts": ["a", "b"]})):
            st, j = await _json(c, "POST", path, body)
            assert st == 503 and j["missing"] is True and "the picture model needs a download" in j["error"] \
                and "Models & licences" in j["error"] and "job" not in j, (path, st, j)
        st, j = await _json(c, "POST", "/api/wallpaper", {})
        assert st == 503 and "picture model" in j["error"], (st, j)
        res = await s.MM.sd_batch("image", [{"prompt": "x", "out": s.FILES_DIR / "images" / "x.png"}])
        assert res[0]["ok"] is False and res[0]["missing"] is True and "needs a download" in res[0]["error"]
        assert not Path(os.environ["MOCK_SD_ARGLOG"]).exists(), "no engine ran"
        why = s.vision_unavailable_reason()
        assert why and "the vision encoder needs a download" in why, why
        st, j = await _json(c, "POST", "/api/search", {"query": "lighthouse"})
        assert st == 200 and "the offline Wikipedia needs a download" in j.get("knowledge_note", ""), j
        st, j = await _json(c, "POST", "/api/models/download", {"model": "muse"})
        assert st == 409 and j["auth"] == "civitai" and j["needs_key"] is True, j
        s.ESS.paused = True                           # the user's pause: an app's download call never overrides it
        st, j = await _json(c, "POST", "/api/models/download", {"model": "default"})
        assert st == 200 and j["essential"] is True and j["downloading"] is False and "picture model" in j["note"] \
            and "paused" in j["note"] and not s.ESS.running(), j
        st, j = await _json(c, "POST", "/api/image", {"prompt": "a lighthouse"})
        assert st == 503 and "paused" in j["error"], j
    finally:
        reset_downloads(s)
        await c.close()
    return "image/images/wallpaper 503 with the sentence, sd floor, vision, search note, download default/muse"


@test("pp_essentials_download_pause_offline_resume_sha256", ["R87", "R47"])
async def t_download(ctx):
    """the first-boot download from the mock server with aria2c: it starts (status downloading), PAUSES mid-file (aria2
    control file + partial bytes, the picture route says 'paused'), RESUMES (Range from the partial), survives the
    server going away (state waiting with the reason, then a Range request when it is back), refuses a file whose
    sha256 differs (fetched twice, deleted, the item stopped with the reason; the other items finish), and after the
    right file + Retry every item is present; the dictionaries / ZIM hooks ran; /api/image then renders"""
    s = kernel()
    reset_downloads(s)
    fm = _K["files"]
    zim = "/f/wikipedia_en_all_mini_2026-06.zim"
    good_zim = fm.files[zim]
    fm.files[zim] = bytes(b ^ 0x5a for b in good_zim)        # the wrong file first: its sha256 differs
    fm.rate = 1_000_000                                       # 1 MB/s: time to pause and to pull the plug
    fm.log.clear()
    c = await _client(s)
    try:
        st, snap = await _json(c, "POST", "/api/downloads", {"action": "start"}, status=200)
        await _until(lambda: s.ESS.current and s.ESS.current[1] == "image/z_image_turbo-Q4_0.gguf"
                     and s.PROFILE.done_bytes({"file": "image/z_image_turbo-Q4_0.gguf", "size_bytes": SIZES["image/z_image_turbo-Q4_0.gguf"]}) > 1_200_000,
                     30, "the first file under way")
        st, snap = await _json(c, "POST", "/api/downloads", {"action": "pause"}, status=200)
        f = s.MODELS_DIR / "image/z_image_turbo-Q4_0.gguf"
        assert snap["state"] == "paused" and snap["paused"] is True and f.with_name(f.name + ".aria2").exists(), snap
        part = s.PROFILE.done_bytes({"file": "image/z_image_turbo-Q4_0.gguf", "size_bytes": SIZES[str(f.relative_to(s.MODELS_DIR))]})
        assert 0 < part < SIZES["image/z_image_turbo-Q4_0.gguf"], part
        item = next(i for i in snap["items"] if i["key"] == "image")
        assert item["status"] == "paused" and 0 < item["pct"] < 100, item
        st, j = await _json(c, "POST", "/api/image", {"prompt": "a lighthouse"})
        assert st == 503 and "paused" in j["error"]
        assert json.loads((s.CACHE_DIR / "downloads-state.json").read_text())["paused"] is True, "the pause survives a restart"
        n0 = len(fm.log)
        st, snap = await _json(c, "POST", "/api/downloads", {"action": "resume"}, status=200)
        await _until(lambda: any(e["path"].endswith("z_image_turbo-Q4_0.gguf") and re.match(r"bytes=[1-9]\d*-", e["headers"].get("Range", ""))
                                 for e in fm.log[n0:]), 20, "a Range request (resume)")
        # pull the plug while the next file is under way
        await _until(lambda: s.ESS.current and s.ESS.current[1] == "image/Qwen3-4B-Instruct-2507-Q4_K_M.gguf"
                     and s.PROFILE.done_bytes({"file": s.ESS.current[1], "size_bytes": 2_200_000}) > 300_000, 40, "the second file")
        port = fm.port
        fm.stop()
        await _until(lambda: s.ESS.state == "waiting", 40, "the waiting state")
        _, snap = await _json(c, "GET", "/api/downloads", status=200)
        assert snap["state"] == "waiting" and "offline" in snap["reason"], snap
        st, j = await _json(c, "POST", "/api/image", {"prompt": "x"})
        assert st == 503 and "waiting for the network" in j["error"], j
        n1 = len(fm.log)
        fm.port = port
        fm.rate = None
        fm.start()
        await _until(lambda: not s.ESS.running(), 90, "the downloads to end")
        assert any(e["path"].endswith("Qwen3-4B-Instruct-2507-Q4_K_M.gguf") and re.match(r"bytes=[1-9]\d*-", e["headers"].get("Range", ""))
                   for e in fm.log[n1:]), "the interrupted file resumed with Range"
        _, snap = await _json(c, "GET", "/api/downloads", status=200)
        k = {i["key"]: i for i in snap["items"]}
        assert k["knowledge"]["status"] == "error" and "sha256" in k["knowledge"]["error"], k["knowledge"]
        assert sum(1 for e in fm.log if e["path"] == zim and not e["headers"].get("Range")) >= 2, "fetched twice before it gave up"
        assert not (s.KNOWLEDGE_DIR / "wikipedia_en_all_mini_2026-06.zim").exists(), "a wrong file is never kept"
        assert all(k[x]["status"] == "present" for x in ("llm", "image", "translation", "dictionaries", "vision_encoder")), k
        st, j = await _json(c, "POST", "/api/search", {"query": "x"})
        assert "could not be downloaded" in j.get("knowledge_note", ""), j
        fm.files[zim] = good_zim
        await _json(c, "POST", "/api/downloads", {"action": "retry"}, status=200)
        await _until(lambda: not s.ESS.running(), 60, "the retry")
        _, snap = await _json(c, "GET", "/api/downloads", status=200)
        assert snap["complete"] is True and snap["state"] == "done" and snap["pct"] == 100, snap
        for fn in MISSING:
            p = (s.KNOWLEDGE_DIR if fn.endswith(".zim") else s.MODELS_DIR) / fn
            assert hashlib.sha256(p.read_bytes()).hexdigest() == hashlib.sha256(fm.files["/f/" + fn]).hexdigest(), fn
            assert not p.with_name(p.name + ".aria2").exists()
        _, st_ = await _json(c, "GET", "/api/status", status=200)
        assert st_["downloads"]["complete"] is True and st_["image_model"]["name"] == "Z-Image-Turbo (Q4_0)"
        st, j = await _json(c, "POST", "/api/image", {"prompt": "a lighthouse at dusk"}, status=200)
        r = await _job(c, j["job"])
        assert r["status"] == "done" and r["result"].startswith("/files/images/"), r
        args = Path(os.environ["MOCK_SD_ARGLOG"]).read_text()
        assert "z_image_turbo-Q4_0.gguf" in args and "museByStableYogi" not in args, args[-400:]
        log = (s.LOG_DIR / "downloads.log").read_text()
        assert "aria2c image/z_image_turbo-Q4_0.gguf" in log
    finally:
        fm.rate = None
        fm.files[zim] = good_zim
        if fm.httpd is None:
            fm.start()
        if s.ESS.running():
            await s.ESS.pause()
        reset_downloads(s)
        await c.close()
    return "pause (control file, 'paused') -> resume (Range) -> offline (waiting) -> back (Range) -> wrong sha refused x2 -> retry -> complete; Z-Image renders"


@test("pp_picture_improve_on_zimage", ["R87", "R85"])
async def t_improve(ctx):
    """picture ✨ on a Z-Image picture: /api/image paints with the public default (the mock sd-cli gets the Z-Image
    files), POST /api/image/improve on that picture runs the editor section (its own Qwen-Image-2.1 DiT, the source as the
    reference picture) and answers v1 of the chain"""
    s = kernel()
    if not all(s.PROFILE.present(e) for e in s.section_files(s.MANIFEST["image"])):
        raise SkipTest("needs the downloaded Z-Image set (pp_essentials_download… must pass first)")
    c = await _client(s)
    try:
        st, j = await _json(c, "POST", "/api/image", {"prompt": "a harbour at dusk"}, status=200)
        r = await _job(c, j["job"])
        assert r["status"] == "done", r
        n0 = len(Path(os.environ["MOCK_SD_ARGLOG"]).read_text().splitlines())
        st, j = await _json(c, "POST", "/api/image/improve", {"picture": r["result"], "instruction": "make the sky purple"}, status=200)
        assert j["model"].startswith("Qwen-Image-2.1 Turbo edit"), j
        r2 = await _job(c, j["job"])
        assert r2["status"] == "done" and "/versions/" in json.dumps(r2["result"]), r2
        new = Path(os.environ["MOCK_SD_ARGLOG"]).read_text().splitlines()[n0:]
        assert new and "qwen_image_2.1_turbo_Q4_K_M.gguf" in new[-1] and "-r" in new[-1].split("\t"), new[-1:]
    finally:
        await c.close()
    return "Z-Image picture -> ✨ improve -> v1 by the Qwen editor (the source as -r)"


@test("pp_licences_view_api", ["R87"])
async def t_licences(ctx):
    """GET /api/licences: every folder of the index with its licence, attribution and texts, `installed` from the files on
    disk (the test kernel: the heretic Gemma, Z-Image, the dictionaries via their prefix … installed; Muse not); every
    text the index names exists in the overlay, the README names every folder; GET /api/licences/<id>/<file> serves the
    text, anything else (traversal, an unknown id, a name the index does not list, the README via its own id) is 404;
    the Krea NOTICE carries the exact §3.1(c) line; every file of the public images' sections belongs to an entry"""
    s = kernel()
    L = REPO / "iso" / "config" / "includes.chroot" / "usr" / "share" / "doc" / "kairos" / "model-licences"
    idx = json.loads((L / "index.json").read_text(encoding="utf-8"))
    readme = (L / "README.txt").read_text(encoding="utf-8")
    for m in idx["models"]:
        assert f"{m['id']}/" in readme, m["id"]
        for t in m["texts"]:
            assert (L / m["id"] / t).is_file() and (L / m["id"] / t).stat().st_size > 200, (m["id"], t)
    assert "Krea 2 is licensed under the Krea 2 Community License Agreement. For more information, visit https://krea.ai/krea-2-licensing." \
        in (L / "krea-2-muse" / "NOTICE.txt").read_text()
    assert "Apache License" in (L / "gemma-4-heretic" / "LICENSE").read_text() \
        and hashlib.sha256((L / "gemma-4-heretic" / "LICENSE").read_bytes()).hexdigest().startswith("cfc7749b")
    assert hashlib.sha256((L / "wan-2.2" / "LICENSE.txt").read_bytes()).hexdigest().startswith("c71d239d")
    assert hashlib.sha256((L / "ace-step-1.5" / "LICENSE").read_bytes()).hexdigest().startswith("05a6bce4")
    assert "GNU AFFERO GENERAL PUBLIC LICENSE" in (L / "dictionaries" / "AGPL-3.0.txt").read_text()
    # coverage: every file of what a public image ships or fetches has an entry (files or prefixes)
    files_of = {f for m in idx["models"] for f in m["files"]}
    prefixes = [p for m in idx["models"] for p in m.get("prefixes", [])]
    m8 = manifest()
    ov = overlay()
    want = [e["file"] for k in ("llm", "image", "image_classic", "vision", "translation", "dictionaries", "video", "music", "image_edit",
                                "llm_fast", "image_chroma", "image_qwen") if k in m8 for e in walk_files(m8[k])] \
        + [e["file"] for e in walk_files(ov["sections"])]          # [R89] llm_fast left the tier (no fast mode)
    uncovered = [f for f in want if f not in files_of and not any(f.startswith(p) for p in prefixes)]
    assert not uncovered, uncovered
    c = await _client(s)
    try:
        _, j = await _json(c, "GET", "/api/licences", status=200)
        by = {m["id"]: m for m in j["models"]}
        zimage_on_disk = any(s.PROFILE.present(e) for e in s.section_files(s.MANIFEST["image_classic"]))
        assert by["gemma-4-heretic"]["installed"] and by["gemma-4-heretic"]["licence"] == "Apache-2.0" \
            and by["z-image-turbo"]["installed"] is zimage_on_disk and by["dictionaries"]["installed"] \
            and by["qwen-image-2.1"]["installed"] and not by["krea-2-muse"]["installed"] and not by["wan-2.2"]["installed"], \
            {k: v["installed"] for k, v in by.items()}
        assert not j["other"] or all(not o["file"].startswith("dict/") for o in j["other"]), j["other"][:3]
        async with c.get("/api/licences/gemma-4-heretic/LICENSE") as r:
            assert r.status == 200 and "Apache License" in await r.text() and r.headers["Content-Type"].startswith("text/plain")
        async with c.get("/api/licences/qwen-image-2.1/NOTICE.txt") as r:
            assert r.status == 200 and "Qwen RESEARCH LICENSE AGREEMENT" in await r.text()
        for bad in ("/api/licences/gemma-4-heretic/..%2F..%2Findex.json", "/api/licences/..%2F..%2F..%2Fetc/passwd",
                    "/api/licences/gemma-4-heretic/README.txt", "/api/licences/nope/LICENSE", "/api/licences/gemma-4-heretic/.hidden",
                    "/api/licences/gemma-4-heretic/index.json"):
            async with c.get(bad) as r:
                assert r.status == 404, (bad, r.status)
    finally:
        await c.close()
    return f"{len(idx['models'])} entries, texts on disk, README complete, Krea line, {len(want)} files covered; API + 6 traversal 404"


@test("pp_panel_e2e_licences_chip_firststart_key", ["R87", "R81"])
async def t_e2e(ctx):
    """the real shell in a headless Chromium on the public kernel: the first start shows the public dialog ('Add Muse
    (needs a free civitai account): paste your API key' + the licence summary) and Not now closes it (setup/done); with a
    file missing the ⬇ chip shows the downloads and opens the panel (its rows); About → Licences opens the panel's
    licences (the heretic Gemma, Apache-2.0) and a text opens; the Muse key typed into the panel leaves the page at once
    (the field empty, the key nowhere in the DOM) and is stored (key_set), Remove key removes it"""
    import test_dock_browser as db
    s = kernel()
    reset_downloads(s)
    for p in (s.VIBEOS_DIR / "fs" / "data" / ".setup-done",):
        p.unlink(missing_ok=True)
    zim = s.KNOWLEDGE_DIR / "wikipedia_en_all_mini_2026-06.zim"
    hold = zim.with_name("held.zim.bak")
    if zim.exists():
        zim.rename(hold)
    try:
        async with db.Desk(s, None) as page:
            await page.wait("!document.querySelector('#setup').classList.contains('hidden')", 15, "the first-start dialog")
            txt = await page.ev("document.querySelector('#setup').textContent")
            assert "Add Muse (needs a free civitai account): paste your API key" in txt and "Stable Yogi" in txt \
                and "the offline Wikipedia" in txt, txt[:600]
            await page.shot("public-first-start.png")
            await page.ev("[...document.querySelectorAll('#setup-actions button')].find((b) => b.textContent === 'Not now').click(), 1")
            await page.wait("document.querySelector('#setup').classList.contains('hidden')", 5, "the dialog closed")
            t0 = time.time()       # [R88 run] done() hides the dialog first, then POSTs /api/setup/done: wait for the file
            while not (s.VIBEOS_DIR / "fs" / "data" / ".setup-done").exists() and time.time() - t0 < 5:
                await asyncio.sleep(0.1)
            assert (s.VIBEOS_DIR / "fs" / "data" / ".setup-done").exists()
            await page.wait("!document.querySelector('#mb-dl').classList.contains('hidden')", 12, "the ⬇ chip")
            chip = await page.ev("document.querySelector('#mb-dl').textContent")
            assert re.match(r"^[⬇⏸⚠] models \d+ %", chip), chip
            await page.click_sel("#mb-dl")
            await page.wait("!!document.querySelector('.km .km-item')", 8, "the panel's downloads")
            rows = await page.ev("[...document.querySelectorAll('.km .km-item')].map((r) => r.dataset.key + '|' + r.textContent)")
            assert any(r.startswith("knowledge|") and "needs a download" in r for r in rows) and any(r.startswith("llm|") and "on disk" in r for r in rows), rows
            await page.shot("public-panel-downloads.png")
            # About → Licences
            await page.click_sel("#mb-clock")
            await page.wait("!document.querySelector('#mb-sysmenu').classList.contains('hidden')", 5)
            await page.click_sel("#mb-about")
            await page.wait("!!document.querySelector('.sm-about-lic')", 5, "About")
            await page.click_sel(".sm-about-lic")
            await page.wait("!!document.querySelector('.km-lic[data-id=\"gemma-4-heretic\"]')", 8, "the licences")
            lic = await page.ev("document.querySelector('.km-lic[data-id=\"gemma-4-heretic\"]').textContent")
            assert "Gemma 4 26B-A4B-it ultra-uncensored heretic" in lic and "Apache-2.0" in lic, lic
            await page.ev("[...document.querySelectorAll('.km-lic[data-id=\"gemma-4-heretic\"] .km-link')].find((b) => b.textContent === 'LICENSE').click(), 1")
            await page.wait("document.querySelector('.km-text') && document.querySelector('.km-text').textContent.includes('Apache License')", 8, "the text")
            await page.shot("public-panel-licences.png")
            # the Muse key: typed, saved, gone from the page
            await page.ev("document.querySelector('.km-muse').scrollIntoView(), 1")
            await page.click_sel(".km-muse .km-key")
            await page.type(KEY)
            await page.ev("[...document.querySelectorAll('.km-muse .km-keyrow button')][0].click(), 1")
            await page.wait("document.querySelector('.km-muse .km-key') && document.querySelector('.km-muse .km-key').value === ''", 5, "the field cleared")
            await _until(lambda: s.MUSE.key_set(), 10, "the key stored")
            await asyncio.sleep(2.5)                       # a panel refresh
            html = await page.ev("document.documentElement.outerHTML")
            assert KEY not in html, "the key must never be in the page"
            await page.shot("public-panel-muse.png")
            await page.ev("[...document.querySelectorAll('.km-muse .km-keyrow button')].find((b) => b.textContent === 'Remove key').click(), 1")
            await _until(lambda: not s.MUSE.key_set(), 10, "the key removed")
    finally:
        if hold.exists():
            hold.rename(zim)
    return "first-start dialog (Muse prompt) -> Not now; ⬇ chip -> panel rows; About -> Licences -> LICENSE text; key cleared + stored + removed"


@test("pp_muse_civitai_key_flow_never_leaks", ["R87", "R76", "R66"])
async def t_muse(ctx):
    """Muse with the user's key against a mock civitai (origin A: 401 without the key, 307 to the CDN with it) and a mock
    CDN (origin B): no key → 409 needs_key; a malformed key → 400 (not echoed); a wrong key → the download stops with
    'civitai refused … (HTTP 401)' (no key in it); the right key is stored 0600 in a 0700 dir; no licence OK → 409
    needs_licence; query mode sends ?token= to civitai only; a pause keeps the .part, the resume asks the CDN with Range;
    the VAE comes anonymously; size + sha256 checked; then Muse is the default (image / image_safe = the tier's own, no
    image_muse, the menubar says Muse). The key was sent to origin A only — never to B, never to the anonymous file
    server — and is in no response, no kernel output, no log and no file but its store."""
    s = kernel()
    civ, cdn, fm = _K["civitai"], _K["cdn"], _K["files"]
    civ.log.clear(); cdn.log.clear(); fm.log.clear()
    out = io.StringIO()
    seen = []

    class Tee(io.TextIOBase):
        def write(self, t):
            out.write(t)
            return sys.__stdout__.write(t)
    c = await _client(s)
    try:
        with contextlib.redirect_stdout(Tee()):
            _, m = await _json(c, "GET", "/api/muse", status=200, seen=seen)
            assert m["available"] and not m["installed"] and not m["key_set"] and m["host"] == "127.0.0.1" and m["licence_summary"], m
            st, j = await _json(c, "POST", "/api/muse/download", {"accept_licence": True}, seen=seen)
            assert st == 409 and j["needs_key"] is True, j
            st, j = await _json(c, "POST", "/api/muse/key", {"key": "short key!"}, seen=seen)
            assert st == 400 and "short key" not in json.dumps(j), j
            await _json(c, "POST", "/api/muse/key", {"key": WRONG_KEY}, status=200, seen=seen)
            st, j = await _json(c, "POST", "/api/muse/download", {"accept_licence": True}, status=200, seen=seen)
            await _until(lambda: not s.MUSE.running(), 30, "the refused download")
            _, m = await _json(c, "GET", "/api/muse", status=200, seen=seen)
            assert m["state"] == "error" and "civitai refused the download (HTTP 401)" in m["error"] and WRONG_KEY not in m["error"], m
            st, j = await _json(c, "POST", "/api/muse/key", {"key": KEY}, status=200, seen=seen)
            assert j == {"ok": True, "key_set": True}, j
            kp = Path(os.environ["KAIROS_SECRETS_DIR"]) / "civitai.key"
            assert kp.read_text().strip() == KEY and stat.S_IMODE(kp.stat().st_mode) == 0o600 \
                and stat.S_IMODE(kp.parent.stat().st_mode) == 0o700
            st, j = await _json(c, "POST", "/api/muse/download", {}, seen=seen)
            assert st == 409 and j["needs_licence"] is True and j["licence_summary"], j
            cdn.rate = 600_000
            civ.log.clear(); cdn.log.clear()
            await _json(c, "POST", "/api/muse/download", {"accept_licence": True, "auth_mode": "query"}, status=200, seen=seen)
            part = s.MODELS_DIR / "image" / "museByStableYogi_v35Q4Extended.gguf.part"
            await _until(lambda: part.exists() and part.stat().st_size > 800_000, 30, "Muse under way")
            _, m = await _json(c, "GET", "/api/muse", status=200, seen=seen)
            assert m["state"] == "downloading" and 0 < m["pct"] < 100, m
            await _json(c, "POST", "/api/muse/download", {"action": "pause"}, status=200, seen=seen)
            assert part.exists() and 0 < part.stat().st_size < SIZES["image/museByStableYogi_v35Q4Extended.gguf"]
            assert any(f"token={KEY}" in e["path"] and "Authorization" not in e["headers"] for e in civ.log), civ.log
            cdn.rate = None
            n_cdn = len(cdn.log)
            await _json(c, "POST", "/api/muse/download", {"accept_licence": True, "auth_mode": "header"}, status=200, seen=seen)
            await _until(lambda: not s.MUSE.running(), 60, "Muse")
            _, m = await _json(c, "GET", "/api/muse", status=200, seen=seen)
            assert m["installed"] is True and m["state"] == "installed", m
            assert any(e["headers"].get("Authorization") == f"Bearer {KEY}" for e in civ.log), "header mode"
            assert any(re.match(r"bytes=[1-9]\d*-", e["headers"].get("Range", "")) for e in cdn.log[n_cdn:]), "the resume used Range"
            assert any(e["path"].endswith("qwen_image_vae.safetensors") for e in fm.log), "the VAE came from the anonymous server"
            # Muse is the default now — as on the owner's images
            assert s.MANIFEST["image"]["name"].startswith("Muse (") and s.MANIFEST["image_safe"]["name"].startswith("Muse SFW") \
                and "image_muse" not in s.MANIFEST and s.resolve_image_kind({}) == "image" and s.MM.image_display_name("image") == "Muse"
            _, st_ = await _json(c, "GET", "/api/status", status=200, seen=seen)
            assert st_["image_model"]["name"].startswith("Muse (") and st_["image"].startswith("Muse ("), st_["image_model"]
            muse_file = s.MODELS_DIR / "image" / "museByStableYogi_v35Q4Extended.gguf"
            assert hashlib.sha256(muse_file.read_bytes()).hexdigest() == hashlib.sha256(cdn.files["/cdn/muse.gguf"]).hexdigest()
            st, j = await _json(c, "POST", "/api/muse/download", {"accept_licence": True}, status=200, seen=seen)
            assert j.get("already") is True
        # where the key went (and where not)
        for e in cdn.log + fm.log:
            assert KEY not in json.dumps(e) and WRONG_KEY not in json.dumps(e) and "Authorization" not in e["headers"], e
        assert all(KEY not in t and WRONG_KEY not in t for t in seen), "a response carried the key"
        assert KEY not in out.getvalue() and WRONG_KEY not in out.getvalue(), "the kernel printed the key"
        leaks = []
        for root in (s.VIBEOS_DIR, s.LOG_DIR):
            for p in Path(root).rglob("*"):
                if p.is_file() and p != kp and p.stat().st_size < 50_000_000:
                    b = p.read_bytes()
                    if KEY.encode() in b or WRONG_KEY.encode() in b:
                        leaks.append(str(p))
        assert not leaks, leaks
        await _json(c, "POST", "/api/muse/key", {"key": None}, status=200)
        assert not kp.exists()
    finally:
        cdn.rate = None
        if s.MUSE.running():
            await s.MUSE.pause()
        mf = s.MODELS_DIR / "image" / "museByStableYogi_v35Q4Extended.gguf"
        mf.unlink(missing_ok=True)
        mf.with_name(mf.name + ".part").unlink(missing_ok=True)
        s.PROFILE.refresh(s.MANIFEST)                  # back to the public default for what follows
        s.MUSE.error, s.MUSE.state, s.MUSE.paused = None, "idle", False
        await c.close()
    return "no key 409, bad 400, wrong 401 (no echo), 0600/0700, licence 409, query ?token -> civitai only, pause/Range resume, sha ok, Muse default; key in no log/response/file"


@test("pp_r88_download_sentences_grammar", ["R87"])
def t_grammar(ctx):
    """[R88] the first-boot download sentences agree in number: every essential of profile-public.json × every state —
    "the dictionaries ARE queued / NEED a download … dictionary look-ups work when THEY arrive", "the offline Wikipedia
    IS … offline search WORKS when IT arrives", "translation works", "looking at pictures works", "pictures work"; the
    Essentials.note() sentences come from that one function; no "dictionaries is" left in the kernel"""
    sys.path.insert(0, str(REPO / "kernel"))
    import public as P
    want = {"the language model": (False, True), "the picture model": (False, True), "the translation model": (False, False),
            "the dictionaries": (True, True), "the vision encoder": (False, False), "the offline Wikipedia": (False, False)}
    items = overlay()["essentials"]
    assert {it["label"] for it in items} == set(want), [it["label"] for it in items]
    n = 0
    for it in items:
        many, feat_many = want[it["label"]]
        assert P.plural(it["label"]) is many and P.plural(it["feature"]) is feat_many, (it["label"], it["feature"])
        for st in ("downloading", "verifying", "waiting", "queued", "paused", "missing", "error"):
            t = P.missing_sentence(it["label"], it["feature"], st, 37, "0.3", "checksum mismatch")
            n += 1
            assert t.startswith(it["label"] + " "), t
            head = t[len(it["label"]) + 1:]
            if st == "error":
                assert head.startswith("could not be downloaded (checksum mismatch)"), t
                continue
            verb = head.split()[0]
            assert verb == ({"paused": "need", "missing": "need"}.get(st, "are") if many else
                            {"paused": "needs", "missing": "needs"}.get(st, "is")), t
            if st in ("downloading", "verifying", "waiting", "queued"):
                assert f" — {it['feature']} {'work' if feat_many else 'works'} when " in t + " " or \
                    f"; {it['feature']} {'work' if feat_many else 'works'} when " in t, t
                assert (" when they " in t) is many and (" when it " in t) is not many, t
    for text in ("the dictionaries are queued for download (0.3 GB to go) — dictionary look-ups work when they arrive",
                 "the offline Wikipedia is queued for download (0.3 GB to go) — offline search works when it arrives",
                 "the translation model is downloading (37 %) — translation works when it is done",
                 "the vision encoder is waiting for the network (37 % downloaded) — the download resumes by itself; "
                 "looking at pictures works when it is done",
                 "the picture model is downloading (37 %) — pictures work when it is done",
                 "the dictionaries need a download (0.3 GB) — start it in ⚙ Models & licences"):
        label = next(l for l in want if text.startswith(l + " "))
        it = next(i for i in items if i["label"] == label)
        st = "queued" if "queued" in text else "downloading" if "downloading" in text else "waiting" if "waiting" in text else "missing"
        assert P.missing_sentence(label, it["feature"], st, 37, "0.3") == text, (P.missing_sentence(label, it["feature"], st, 37, "0.3"), text)
    assert P.missing_sentence("the picture model", "", "downloading", 5, "1") == "the picture model is downloading (5 %) — this works when it is done"
    src = (REPO / "kernel" / "public.py").read_text()
    note = src[src.index("    def note(self"):src.index("    # ---- control")]
    assert "return missing_sentence(" in note and "{feat}" not in note and 'f"{label}' not in note, "note() builds no sentence of its own"
    for f in ("public.py", "server.py", "dictionary.py", "knowledge.py"):
        assert not re.search(r"dictionaries (is|isn't|needs|has) ", (REPO / "kernel" / f).read_text()), f
    return f"{len(items)} essentials × 7 states = {n} sentences agree (label verb, feature verb, it/they); note() uses them"


# ============================================================================= the scripts
def _tree(root: Path, licences=True, sources=True):
    """an image root with every private bit the prep script knows, planted"""
    def w(rel, text="", mode=None):
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        if mode:
            os.chmod(p, mode)
        return p
    w("etc/os-release", 'NAME="Debian GNU/Linux"\n')
    w("etc/hostname", "owners-laptop\n")
    w("etc/hosts", "127.0.0.1\tlocalhost\n127.0.1.1\towners-laptop\n")
    w("etc/machine-id", "0123456789abcdef0123456789abcdef\n")
    w("etc/NetworkManager/system-connections/HomeAP.nmconnection", "[wifi]\nssid=HomeAP\n[wifi-security]\npsk=hunter2hunter2\n", 0o600)
    w("etc/wpa_supplicant/wpa_supplicant.conf", 'network={\n ssid="HomeAP"\n psk="hunter2hunter2"\n}\n')
    w("etc/ssh/ssh_host_ed25519_key", "-----BEGIN OPENSSH PRIVATE KEY-----\nAAAA\n-----END OPENSSH PRIVATE KEY-----\n", 0o600)
    w("etc/ssh/ssh_host_ed25519_key.pub", "ssh-ed25519 AAAA host\n")
    w("etc/ssh/sshd_config.d/kairos.conf", "PermitRootLogin prohibit-password\nPasswordAuthentication no\n")
    w("etc/ssl/private/ssl-cert-snakeoil.key", "-----BEGIN PRIVATE KEY-----\nAAAA\n-----END PRIVATE KEY-----\n")
    for link, target in (("etc/systemd/system/multi-user.target.wants/ssh.service", "/lib/systemd/system/ssh.service"),
                         ("etc/systemd/system/sshd.service", "/lib/systemd/system/ssh.service")):
        (root / link).parent.mkdir(parents=True, exist_ok=True)
        os.symlink(target, root / link)
    w("etc/systemd/system/kairos.service.d/expose-ui.conf", "[Service]\nEnvironment=VIBEOS_HOST=0.0.0.0\n")
    w("etc/systemd/system/kairos.service", "[Unit]\nDescription=kAIrOS kernel\n")
    w("root/.ssh/authorized_keys", "ssh-ed25519 AAAAC3Nza owner@rig\n", 0o600)
    w("root/.bash_history", "ssh owner@10.0.0.2\n")
    w("root/.cache/mesa_shader_cache/index", "x")
    w("root/.nv/ComputeCache/index", "x")
    w("home/kairos/.ssh/authorized_keys", "ssh-ed25519 AAAAC3Nza owner@rig\n")
    w("home/kairos/.bash_history", "ls\n")
    w("home/kairos/.xinitrc", "exec chromium --app=http://127.0.0.1:1337 # /home/kairos/.xinitrc\n")
    w("home/kairos/.config/openbox/rc.xml", "<openbox_config/>\n")
    w("home/kairos/.config/chromium/Default/History", "visited")
    w("home/kairos/.local/state/wireplumber/stream-properties", "x")
    w("home/kairos/.Xauthority", "x")
    w("var/lib/systemd/random-seed", "seed")
    w("var/lib/NetworkManager/seen-bssids", "aa:bb:cc:dd:ee:ff")
    w("var/lib/bluetooth/AA:BB:CC:DD:EE:FF/11:22:33:44:55:66/info", "[LinkKey]\nKey=00\n")
    w("var/lib/dhcp/dhclient.wlan0.leases", "lease {}")
    w("var/lib/kairos/secrets/civitai.key", KEY + "\n", 0o600)
    w("var/lib/kairos-firstboot-done", "")
    w("var/lib/kairos-resized", "")
    w("var/log/syslog", "owner logged in\n")
    w("var/log/kairos/kernel.log", "x")
    w("var/log/journal/0123456789abcdef/system.journal", "x")
    w("tmp/stray", "x")
    w("boot/efi/KAIROS-LOGS/latest.txt", "boot on SERIAL-ABC123\n")
    w("boot/efi/loader/random-seed", "seed")
    w("boot/efi/EFI/BOOT/BOOTX64.EFI", "efi")
    w("opt/vibeos/HOTFIX", "built by Jane Owner from /home/janeo/kairos on OWNERS-LAPTOP\n")
    w("opt/vibeos/cache/settings.json", '{"wallpaper": "/files/images/1.png"}')
    w("opt/vibeos/cache/apps/abc/meta.json", "{}")
    w("opt/vibeos/fs/data/.setup-done", "{}")
    w("opt/vibeos/fs/data/settings.json", "{}")
    w("opt/vibeos/games/pong/history/v1.py", "print()")
    w("opt/vibeos/models/llm/gemma.gguf", "weights")
    w("opt/vibeos/models/image/museByStableYogi_v35Q4Extended.gguf", "muse")
    w("opt/vibeos/models/dict/freedict-deu-eng-1.9-fd1.dictd.tar.xz", "dict")
    w("opt/vibeos/os/kernel/server.py", "print('kernel')\n")
    w("opt/vibeos/os/docs/OLD-REPORT.md", "measured on jane@example.org's laptop, /home/janeo/kairos\n")
    w("opt/vibeos/os/kernel/server.py.bak-2026-01-01x", "an edit backup: never in the public repo\n")
    w("usr/local/sbin/kairos-bootlog.bak-2026-01-01y", "x")
    w("opt/vibeos/os/tests/mock-ace-cli", "#!/bin/sh\necho mock\n")             # [R88] an older image's tests/
    w("opt/vibeos/os/tests/test_kernel.py", "print('test')\n")
    if licences:
        shutil.copytree(REPO / "iso/config/includes.chroot/usr/share/doc/kairos/model-licences",
                        root / "usr/share/doc/kairos/model-licences")
    if sources:
        for n in ("deu-eng-1.9-fd1", "eng-deu-1.9-fd1", "deu-ita-0.2.1", "ita-deu-0.2.1", "eng-fra-0.1.6", "fra-eng-0.4.1",
                  "spa-eng-0.3.1", "spa-deu-0.1"):
            w(f"usr/share/doc/kairos/sources/freedict/freedict-{n}.src.tar.xz", "src")


def _prep(root, *args, patterns=None):
    cmd = ["bash", str(REPO / "scripts" / "public-image-prep.sh"), str(root), *args]
    if patterns:
        cmd += ["--patterns", str(patterns)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    return r.returncode, r.stdout + r.stderr


def _listing(root: Path) -> str:
    out = []
    for p in sorted(root.rglob("*")):
        st = p.lstat()
        kind = "l" if p.is_symlink() else "d" if p.is_dir() else "f"
        body = os.readlink(p) if kind == "l" else hashlib.sha256(p.read_bytes()).hexdigest()[:16] if kind == "f" else ""
        out.append(f"{kind} {oct(stat.S_IMODE(st.st_mode))} {p.relative_to(root)} {body}")
    return "\n".join(out)


@test("pp_prep_verify_planted_then_clean_idempotent", ["R87", "R66"])
def t_prep(ctx):
    """scripts/public-image-prep.sh on a planted tree: --verify FAILS on every planted category (WiFi + PSK, NM state,
    wpa, BT pairing, DHCP lease, authorized_keys ×2, the SSH host key + flag, sshd enabled, machine-id, random seeds,
    hostname, /var/log, the ESP's KAIROS-LOGS, histories, root's caches, the browser profile, the testrig drop-in,
    the build stamp, the cache, /data, games, the civitai key, growroot flag, the profile, Muse) and reports the owner's
    strings in the shipped repo text as WARN only; prepare fixes all of it (its own verify: PASS, exit 0) and keeps what
    the OS needs (sshd's key-only policy, the models, the kernel, the kiosk's .xinitrc + openbox, the licences, the log
    dirs); /etc/kairos/profile = public, the first-boot unit enabled, sshd disabled; a SECOND prepare changes nothing
    (identical tree); unfixable leaks (a private key, an owner string in /etc) stay FAIL after prepare; missing licence
    texts / FreeDict sources FAIL"""
    base = Path(tempfile.mkdtemp(prefix="kairos-prep-"))
    try:
        pat = base / "patterns.txt"
        pat.write_text("# the owner's strings\nJane Owner\njane@example.org\nOWNERS-LAPTOP\nSERIAL-ABC123\n")
        root = base / "img"
        _tree(root)
        rc, out = _prep(root, "--verify", patterns=pat)
        fails = [l for l in out.splitlines() if l.startswith("FAIL")]
        assert rc == 1 and "VERIFY: FAIL" in out, out[-600:]
        for needle in ("WiFi / network profile", "NetworkManager state", "wpa_supplicant network config", "Bluetooth pairing",
                       "DHCP leases", "authorized_keys: /root/.ssh", "authorized_keys: /home/kairos/.ssh", "SSH host key",
                       "first-boot done flag", "sshd enabled at boot", "machine-id is set", "random seed: /var/lib/systemd",
                       "random seed: ESP:/loader/random-seed", "hostname: /etc/hostname", "/etc/hosts names another host",
                       "log file(s) under /var/log", "boot logs on the EFI partition", "shell / editor history: /root/.bash_history",
                       "cache / browser profile: /root/.cache", "cache / browser profile: /home/kairos/.config/chromium",
                       "remote-UI exposure", "build stamp with build-host details", "kAIrOS cache", "the /data partition",
                       "generated game", "kAIrOS secrets (a user's civitai API key)", "growroot done flag", "the public profile",
                       "Muse — no redistribution grant", "first-boot unit", "temporary file", "snakeoil",
                       "operator's private string: /opt/vibeos/HOTFIX", "operator's private string: ESP:/KAIROS-LOGS/latest.txt",
                       "a home path other than /home/kairos: /opt/vibeos/HOTFIX", "private key: /etc/ssh/ssh_host_ed25519_key",
                       "WiFi PSK: /etc/NetworkManager/system-connections/HomeAP.nmconnection",
                       "2 edit backup(s) (*.bak*, never in the public repository)",
                       "not part of the runtime (dev / test files): /opt/vibeos/os/tests"):
            assert any(needle in l for l in fails), (needle, "\n".join(fails)[-1500:])
        assert re.search(r"^WARN  operator's private string \(shipped repo text, public on GitHub\): /opt/vibeos/os/docs/OLD-REPORT.md", out, re.M), out
        assert not any("OLD-REPORT" in l for l in fails), "repo text is a warning, not a failure"
        before = _listing(root)
        assert "profile" not in before or "etc/kairos/profile" not in before
        rc, out = _prep(root, patterns=pat)
        assert rc == 0 and "VERIFY: PASS" in out and re.search(r"prepare: \d+ change", out), out[-1500:]
        for keep in ("etc/ssh/sshd_config.d/kairos.conf", "opt/vibeos/models/llm/gemma.gguf", "opt/vibeos/os/kernel/server.py",
                     "home/kairos/.xinitrc", "home/kairos/.config/openbox/rc.xml", "usr/share/doc/kairos/model-licences/index.json",
                     "boot/efi/EFI/BOOT/BOOTX64.EFI", "opt/vibeos/models/dict/freedict-deu-eng-1.9-fd1.dictd.tar.xz",
                     "etc/systemd/system/kairos.service"):
            assert (root / keep).exists(), keep
        for d in ("var/log", "var/log/kairos", "var/log/journal", "boot/efi/KAIROS-LOGS", "opt/vibeos/cache", "opt/vibeos/fs/data"):
            assert (root / d).is_dir() and not any(p.is_file() for p in (root / d).rglob("*")), d
        assert (root / "etc/kairos/profile").read_text() == "public\n" and (root / "etc/hostname").read_text() == "kairos\n"
        assert (root / "etc/machine-id").read_text() == "" and not (root / "etc/systemd/system/multi-user.target.wants/ssh.service").exists() \
            and not (root / "etc/systemd/system/sshd.service").exists()
        unit = (root / "etc/systemd/system/kairos-public-firstboot.service").read_text()
        assert "ssh-keygen -A" in unit and "Before=ssh.service" in unit and "make-ssl-cert" in unit
        assert os.readlink(root / "etc/systemd/system/multi-user.target.wants/kairos-public-firstboot.service") \
            == "/etc/systemd/system/kairos-public-firstboot.service"
        assert "127.0.1.1\tkairos" in (root / "etc/hosts").read_text()
        assert not list((root / "opt/vibeos/models").rglob("museByStableYogi*"))
        assert not list(root.rglob("*.bak-*")) and (root / "opt/vibeos/os/kernel/server.py").exists()
        assert not (root / "opt/vibeos/os/tests").exists() and (root / "opt/vibeos/os/docs").is_dir(), "[R88] tests/ goes, docs stay"
        snap1 = _listing(root)
        rc, out2 = _prep(root, patterns=pat)
        assert rc == 0 and "prepare: 0 change(s)" in out2 and "VERIFY: PASS" in out2, out2[-800:]
        assert _listing(root) == snap1, "a second prepare changed the tree"
        # what prepare cannot fix stays a failure
        (root / "etc/ssl/private/other.key").write_text("-----BEGIN RSA PRIVATE KEY-----\nAAAA\n-----END RSA PRIVATE KEY-----\n")
        (root / "etc/motd").write_text("Welcome to OWNERS-LAPTOP\n")
        rc, out3 = _prep(root, patterns=pat)
        assert rc == 1 and "FAIL  private key: /etc/ssl/private/other.key" in out3 \
            and "FAIL  operator's private string: /etc/motd" in out3, out3[-800:]
        # licence texts / FreeDict sources missing
        root2 = base / "img2"
        _tree(root2, licences=False, sources=False)
        rc, out4 = _prep(root2, patterns=pat)
        assert rc == 1 and "licence files missing" in out4 and "FreeDict source missing" in out4, out4[-800:]
        rc, out5 = _prep(Path("/"), "--verify")
        assert rc == 2 and "refusing" in out5
        rc, out6 = _prep(base, "--verify")
        assert rc == 2 and "does not look like a kAIrOS image root" in out6
    finally:
        shutil.rmtree(base, ignore_errors=True)
    return f"verify: {len(fails)} FAIL lines incl. 37 categories + repo text WARN; prepare -> PASS, kept files; 2nd run 0 changes; unfixable stays FAIL"


@test("pp_names_hosts_and_resolv", ["R87", "R28"])
def t_prep_names(ctx):
    """[fix 2026-10-22] the public prep keeps the host's name local and the resolver NetworkManager's: an image whose
    /etc/hosts is EMPTY (v7.5-3's) and whose /etc/resolv.conf is a baked file ("nameserver 10.0.2.3") fails verify on
    both; prepare adds 127.0.0.1 localhost + 127.0.1.1 kairos + ::1 and links resolv.conf to
    /run/NetworkManager/resolv.conf (then PASS, a second run changes nothing); the overlay's /etc/hosts
    (127.0.1.1 localhost.localdomain) is renamed to kairos with its other lines kept."""
    base = Path(tempfile.mkdtemp(prefix="kairos-names-"))
    try:
        pat = base / "patterns.txt"
        pat.write_text("Jane Owner\nOWNERS-LAPTOP\n")
        root = base / "img"
        _tree(root)
        (root / "etc/hosts").write_text("")
        (root / "etc/resolv.conf").write_text("# Generated by NetworkManager\nnameserver 10.0.2.3\n")
        rc, out = _prep(root, "--verify", patterns=pat)
        assert rc == 1 and "FAIL  /etc/hosts does not name the host (no 127.0.1.1 line): /etc/hosts" in out \
            and "FAIL  resolv.conf is not NetworkManager's /run file (a baked one names the build's resolver): /etc/resolv.conf" in out, out[-1500:]
        rc, out = _prep(root, patterns=pat)
        assert rc == 0 and "VERIFY: PASS" in out and "fixed hostname added to /etc/hosts" in out \
            and "fixed resolv.conf -> /run/NetworkManager/resolv.conf" in out, out[-1500:]
        assert (root / "etc/hosts").read_text() == "127.0.0.1\tlocalhost\n127.0.1.1\tkairos\n::1\tlocalhost ip6-localhost ip6-loopback\n"
        assert os.readlink(root / "etc/resolv.conf") == "/run/NetworkManager/resolv.conf"
        snap = _listing(root)
        rc, out = _prep(root, patterns=pat)
        assert rc == 0 and "prepare: 0 change(s)" in out and _listing(root) == snap, out[-800:]
        # the overlay's hosts file: its 127.0.1.1 line becomes kairos, the rest stays
        root2 = base / "img2"
        _tree(root2)
        overlay = (REPO / "iso/config/includes.chroot/etc/hosts").read_text()
        (root2 / "etc/hosts").write_text(overlay)
        os.symlink("/run/NetworkManager/resolv.conf", root2 / "etc/resolv.conf")
        rc, out = _prep(root2, patterns=pat)
        assert rc == 0 and "VERIFY: PASS" in out and "fixed hostname in /etc/hosts" in out \
            and "hostname added" not in out and "resolv.conf" not in out, out[-1500:]
        assert (root2 / "etc/hosts").read_text() == overlay.replace("127.0.1.1\tlocalhost.localdomain", "127.0.1.1\tkairos")
    finally:
        shutil.rmtree(base, ignore_errors=True)
    return "empty hosts + baked resolv.conf: verify FAILs both, prepare adds the kairos line + the NM link (PASS, idempotent); overlay hosts renamed"


@test("pp_r88_runtime_ships_no_tests", ["R87", "R47"])
def t_runtime_set(ctx):
    """[R88] the shipped OS code is exactly kernel/ shell/ manifests/ scripts/ docs/, in all three places that lay it out:
    iso/build-iso.sh (stages that set into its persistent work tree, first removing anything else — the tests/ an older
    build left — and never copying edit backups or bytecode), dev/rig/testrig/sync-guest.sh (the same set, then removes
    anything else from the guest's /opt/vibeos/os) and the public prep script (removes anything else from the image's
    /opt/vibeos/os); the kernel and the shell read nothing under tests/ at runtime"""
    want = {"kernel", "shell", "manifests", "scripts", "docs"}
    iso = (REPO / "iso" / "build-iso.sh").read_text()
    m = re.search(r'^OS_RUNTIME="([^"]+)"', iso, re.M)
    assert m and set(m.group(1).split()) == want, m and m.group(1)
    rs = re.search(r'^rsync -a --delete .*?"\$INC/os/"$', iso, re.M | re.S).group(0)
    assert set(re.findall(r'"\$REPO/([a-z]+)"', rs)) == want, rs
    for x in ("--delete-excluded", "--exclude __pycache__", "--exclude '*.bak-*'", "--exclude '*.bak'"):
        assert x in rs, x
    loop = iso[iso.index('for e in "$INC/os"/*'):iso.index(rs)]
    assert 'rm -rf "$e"' in loop and '" $OS_RUNTIME "' in loop, loop
    sg = (REPO / "dev" / "rig" / "testrig" / "sync-guest.sh").read_text()
    m = re.search(r'^OS_RUNTIME="([^"]+)"', sg, re.M)
    assert m and set(m.group(1).split()) == want, m and m.group(1)
    assert "-czf - $OS_RUNTIME" in sg and "--exclude='*.bak-*'" in sg and "--exclude=__pycache__" in sg
    assert re.search(r'cd /opt/vibeos/os && for e in \*; do case .*?rm -rf -- ', sg), "sync-guest prunes the guest"
    prep = (REPO / "scripts" / "public-image-prep.sh").read_text()
    m = re.search(r'case "\$\(basename "\$e"\)" in ([a-z|]+)\) ;;', prep)
    assert m and set(m.group(1).split("|")) == want, m and m.group(1)
    code = [REPO / "kernel" / f for f in os.listdir(REPO / "kernel") if f.endswith(".py")] + list((REPO / "shell").rglob("*.js"))
    bad = []
    for f in code:
        for n, line in enumerate(f.read_text().splitlines(), 1):
            ln = line.split("#", 1)[0] if f.suffix == ".py" else line.split("//", 1)[0]
            if re.search(r"""["'/]tests/|/ ?["']tests["']|["']tests["']\s*/""", ln):
                bad.append(f"{f.name}:{n}: {line.strip()[:80]}")
    assert not bad, bad
    return f"build-iso / sync-guest / prep: the same {len(want)} dirs, the rest removed, no *.bak* / __pycache__; kernel + shell ({len(code)} files) read nothing under tests/"


# [R88] the raw-byte scrub: a planted GPT disk image (no root needed: mke2fs -d, debugfs -w, mkfs.vfat -C + mtools, sfdisk
# on a file) with the residue the v7.5-2 build found — old "swapped" pages under the swapfile's UNWRITTEN extents (the
# freed blocks of a secret file, then debugfs fallocate), a deleted NetworkManager profile in a free ext4 block, a deleted
# boot log in a free ESP cluster — plus the owner's string in shipped repo text (a WARN, never a FAIL)
RAW_BUILD = r"""set -eu
cd "$1"
mkdir -p tree/etc/NetworkManager/system-connections tree/opt/vibeos/os/docs tree/opt/vibeos/os/kernel tree/root
echo "/swapfile none swap sw 0 0" > tree/etc/fstab
echo "keep me" > tree/etc/keep.conf
echo "measured on Jane Owner's laptop" > tree/opt/vibeos/os/docs/OLD.md
python3 -c "open('tree/old','wb').write((b'PSK=hunter2hunter2-PLANTED '*200)[:4096]*64)"
mke2fs -q -t ext4 -b 4096 -L KAIROS_ROOT -d tree root.img 64M
debugfs -w -R "rm /old" root.img
truncate -s 262144 sw.tmp
mkswap -U 11111111-2222-3333-4444-555555555555 -L SWAPL sw.tmp >/dev/null 2>&1
head -c 4096 sw.tmp > hdr4k
printf 'write hdr4k swapfile\nfallocate /swapfile 1 63\nsif /swapfile size 262144\nsif /swapfile mode 0100600\n' > c1
debugfs -w -f c1 root.img
printf '[wifi]\nssid=HomeAP-PLANTED\n[wifi-security]\npsk=hunter2hunter2-PLANTED\n' > nm.txt
debugfs -w -R "write nm.txt /etc/NetworkManager/system-connections/Home.nmconnection" root.img
debugfs -w -R "rm /etc/NetworkManager/system-connections/Home.nmconnection" root.img
mkfs.vfat -C -n KAIROSEFI esp.img 34816 >/dev/null
mmd -i esp.img ::EFI ::EFI/BOOT ::KAIROS-LOGS
echo efi > efi.bin
mcopy -i esp.img efi.bin ::EFI/BOOT/BOOTX64.EFI
for i in $(seq 400); do echo "boot log: networks seen HomeAP-PLANTED, owner Jane Owner"; done > log.txt
mcopy -i esp.img log.txt ::KAIROS-LOGS/latest.txt
mdel -i esp.img ::KAIROS-LOGS/latest.txt
E=$(( $(stat -c %s esp.img) / 512 )); R=$(( $(stat -c %s root.img) / 512 ))
truncate -s $(( (2048 + E + R + 2048) * 512 )) disk.img
printf 'label: gpt\nstart=2048, size=%d, type=uefi, name="EFI"\nstart=%d, size=%d, type=linux, name="KAIROS_ROOT"\n' \
  $E $((2048 + E)) $R | sfdisk -q disk.img
dd if=esp.img of=disk.img bs=512 seek=2048 conv=notrunc status=none
dd if=root.img of=disk.img bs=512 seek=$((2048 + E)) conv=notrunc status=none
echo "$((2048 * 512)) $(( (2048 + E) * 512 )) $((E * 512))"
"""
RAW_SECRETS = ("Jane Owner", "HomeAP-PLANTED", "hunter2hunter2-PLANTED")


def _scrub(disk, *args, patterns=None, env=None):
    cmd = ["bash", str(REPO / "scripts" / "public-image-prep.sh"), "--scrub", str(disk), *args]
    if patterns:
        cmd += ["--patterns", str(patterns)]
    hermetic = {"KAIROS_WIFI_ENV": "/nonexistent", "KAIROS_SSH_PUBKEY": "/nonexistent"}   # never the operator's own secrets
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=300,
                       env={**os.environ, "LC_ALL": "C", **hermetic, **(env or {})})
    return r.returncode, r.stdout + r.stderr


@test("pp_r88_raw_scrub_planted_image", ["R87", "R66"])
def t_raw_scrub(ctx):
    """[R88] public-image-prep.sh --scrub on a planted GPT disk image (the residue the v7.5-2 build found): --verify FAILS
    on the swapfile's old pages (unwritten extents over a deleted secret's blocks), the non-zero free ext4 block (the
    deleted WiFi profile), the free ESP clusters (the deleted boot log) and on each needle's raw hits there — the owner's
    string in shipped repo text is only a WARN, no needle is ever printed; --scrub zeroes exactly that (the swap header
    kept: same UUID + label, blkid reads it; e2fsck -fn + fsck.vfat -n clean; kept files intact) and its own raw verify
    PASSES with only the repo-text WARN; the raw bytes then hold no secret outside that file; a second --scrub writes
    nothing; stray bytes in the swap header page are wiped (UUID kept); a needle outside the partitions FAILS the verify
    (never scrubbed: the GPT gaps are left alone); a bare ext4 image works too; an unclean ext4 is refused untouched"""
    need = ("mke2fs", "debugfs", "e2fsck", "mkfs.vfat", "fsck.vfat", "mcopy", "mdel", "mmd", "mtype", "sfdisk", "mkswap", "blkid")
    missing = [t for t in need if not shutil.which(t)]
    if missing:
        raise SkipTest("needs e2fsprogs, dosfstools, mtools, sfdisk: missing " + ", ".join(missing))
    base = Path(tempfile.mkdtemp(prefix="kairos-scrub-"))
    try:
        r = subprocess.run(["bash", "-c", RAW_BUILD, "build", str(base)], capture_output=True, text=True, timeout=120,
                           env={**os.environ, "LC_ALL": "C"})
        assert r.returncode == 0, r.stdout[-500:] + r.stderr[-800:]
        esp_off, root_off, esp_len = map(int, r.stdout.split()[-3:])
        disk = base / "disk.img"
        pat = base / "patterns.txt"
        pat.write_text("# the owner's strings\n" + "\n".join(RAW_SECRETS) + "\n")
        raw = disk.read_bytes()
        assert all(raw.count(x.encode()) >= 1 for x in RAW_SECRETS), "the planted residue is in the raw bytes"
        before = raw
        rc, out = _scrub(disk, "--verify", patterns=pat)
        assert rc == 1 and "RAW VERIFY: FAIL" in out, out
        for needle in ("p2 (KAIROS_ROOT): swapfile /swapfile: 63 page(s) beyond its header hold old data",
                       "p2 (KAIROS_ROOT): ext4: 1 free block(s) hold old data", "p1 (EFI): vfat: ",
                       "raw bytes: pattern 3 in ", "— p2 (KAIROS_ROOT): swapfile /swapfile (old memory pages)",
                       "— p2 (KAIROS_ROOT): a FREE block", "— p1 (EFI): a FREE cluster"):
            assert needle in out, (needle, out)
        assert re.search(r"^WARN  raw bytes: pattern 1 in 1 block\(s\) — p2 \(KAIROS_ROOT\): shipped repo text \(/opt/vibeos/os/…\)$", out, re.M), out
        assert not any(x in out for x in RAW_SECRETS), "a needle was printed"
        assert disk.read_bytes() == before, "--verify writes nothing"
        rc, out = _scrub(disk, patterns=pat)
        assert rc == 0 and "RAW VERIFY: PASS — 0 findings, 1 warning(s)" in out, out
        assert "swapfile /swapfile: 63 old page(s) zeroed, header kept" in out and "ext4: 1 non-zero free block(s) zeroed" in out \
            and re.search(r"vfat \(FAT1[26]\): \d+ non-zero free cluster\(s\) zeroed", out), out
        assert not any(x in out for x in RAW_SECRETS), "a needle was printed"
        raw = disk.read_bytes()
        assert raw.count(b"HomeAP-PLANTED") == 0 and raw.count(b"hunter2hunter2-PLANTED") == 0 and raw.count(b"Jane Owner") == 1, \
            "only the shipped repo text keeps the owner's string"
        spec = f"{disk}?offset={root_off}"
        assert subprocess.run(["e2fsck", "-fn", spec], capture_output=True).returncode == 0
        esp = base / "esp.out"
        esp.write_bytes(raw[esp_off:esp_off + esp_len])
        assert subprocess.run(["fsck.vfat", "-n", str(esp)], capture_output=True).returncode == 0
        assert subprocess.run(["mtype", "-i", str(esp), "::EFI/BOOT/BOOTX64.EFI"], capture_output=True, text=True).stdout == "efi\n"
        assert subprocess.run(["debugfs", "-R", "cat /etc/keep.conf", spec], capture_output=True, text=True).stdout == "keep me\n"
        sw = base / "sw.after"
        subprocess.run(["debugfs", "-R", f"dump /swapfile {sw}", spec], capture_output=True)
        b = subprocess.run(["blkid", "-p", "-o", "export", str(sw)], capture_output=True, text=True).stdout
        assert "TYPE=swap" in b and "UUID=11111111-2222-3333-4444-555555555555" in b and "LABEL=SWAPL" in b, b
        assert sw.stat().st_size == 262144
        snap = disk.read_bytes()
        rc, out = _scrub(disk, patterns=pat)
        assert rc == 0 and "63 old page(s)" not in out and "0 old page(s) zeroed" in out and "ext4: 0 non-zero" in out \
            and "vfat (FAT16): 0 non-zero" in out.replace("FAT12", "FAT16"), out
        assert disk.read_bytes() == snap, "a second scrub writes nothing"
        # stray bytes in the swap header page (its bootbits): wiped, the header rebuilt with the same UUID / label
        hb = int(re.search(r"\((?:0)\):(\d+)", subprocess.run(["debugfs", "-R", "stat /swapfile", spec], capture_output=True,
                                                               text=True).stdout).group(1))
        with open(disk, "r+b") as f:
            f.seek(root_off + hb * 4096 + 100)
            f.write(b"stray old bytes")
        rc, out = _scrub(disk, "--verify", patterns=pat)
        assert rc == 1 and "the header page holds stray bytes" in out, out
        rc, out = _scrub(disk, patterns=pat)
        assert rc == 0 and "header rebuilt (same UUID/label)" in out, out
        subprocess.run(["debugfs", "-R", f"dump /swapfile {sw}", spec], capture_output=True)
        b = subprocess.run(["blkid", "-p", "-o", "export", str(sw)], capture_output=True, text=True).stdout
        assert "UUID=11111111-2222-3333-4444-555555555555" in b and b"stray" not in sw.read_bytes()[:4096], b
        # a needle outside the partitions (the GPT gap): the verify FAILS it, the scrub leaves the gap alone
        with open(disk, "r+b") as f:
            f.seek(600 * 512)
            f.write(b"hunter2hunter2-PLANTED")
        rc, out = _scrub(disk, patterns=pat)
        assert rc == 1 and "raw bytes: pattern 3 in 1 block(s) — outside the partitions (GPT / gaps)" in out, out
        assert disk.read_bytes()[600 * 512:600 * 512 + 22] == b"hunter2hunter2-PLANTED", "gaps are never written"
        # a bare ext4 image (no partition table)
        bare = base / "root.img"
        rc, out = _scrub(bare, patterns=pat)
        assert rc == 0 and "the file system ext4" in out and "63 old page(s) zeroed" in out and "RAW VERIFY: PASS" in out, out
        # refused: an unclean ext4 (nothing written), no such disk, --scrub with a ROOT
        unclean = base / "unclean.img"
        unclean.write_bytes(before)
        subprocess.run(["debugfs", "-w", "-R", "ssv state 0", f"{unclean}?offset={root_off}"], capture_output=True)
        snap = unclean.read_bytes()
        rc, out = _scrub(unclean, patterns=pat)
        assert rc == 2 and "refused" in out and "not clean" in out and unclean.read_bytes() == snap, out
        rc, out = _scrub(base / "nope.img")
        assert rc == 2 and "does not exist" in out, out
        rc, out = _prep(base, "--scrub", str(disk))
        assert rc == 2 and "no ROOT" in out, out
        empty = base / "empty.txt"
        empty.write_text("# nothing\n")
        rc, out = _scrub(disk, "--verify", patterns=empty)     # no needles: the structure only, and a WARN
        assert "WARN  no private patterns: the raw bytes were not searched" in out and "raw needles: 0" in out, out
        # the build's own secrets are needles too: iso/secrets/wifi.env (SSID + PSK) and authorized_keys (the key body)
        d2 = base / "d2.img"
        d2.write_bytes(before)
        wenv, keys = base / "wifi.env", base / "authorized_keys"
        wenv.write_text('KAIROS_WIFI_SSID="HomeAP-PLANTED"\nKAIROS_WIFI_PSK=hunter2hunter2-PLANTED\n')
        keys.write_text("ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIPlantedRigKeyBody0123456789abcdef rig\n")
        rc, out = _scrub(d2, "--verify", patterns=empty, env={"KAIROS_WIFI_ENV": str(wenv), "KAIROS_SSH_PUBKEY": str(keys)})
        assert rc == 1 and "raw needles: 3" in out and "raw bytes: wifi-psk in " in out and "raw bytes: wifi-ssid in " in out, out
        assert not any(x in out for x in RAW_SECRETS) and "PlantedRigKey" not in out, "a needle was printed"
    finally:
        shutil.rmtree(base, ignore_errors=True)
    return ("verify FAILs swap pages / free block / free clusters + raw hits (repo text WARN, no needle printed); scrub -> PASS, "
            "UUID kept, e2fsck + fsck.vfat clean, files intact; 2nd scrub writes nothing; header stray bytes wiped; gap needle "
            "FAILs (gap untouched); bare ext4 ok; unclean ext4 refused untouched")


@test("pp_fetch_licence_sources_mock", ["R87"])
def t_fetch(ctx):
    """scripts/fetch-licence-sources.sh against the mock server (--base-url, --list): fetches + sha512-checks, writes
    SHA512SUMS + README.txt, a second run keeps the files, --check passes; a damaged file fails --check and the next run
    repairs it; a list whose sha512 is wrong fails without leaving a file; the real list carries FreeDict's 8 tarballs
    (36 004 656 B)"""
    if not shutil.which("curl") or not shutil.which("sha512sum"):
        raise SkipTest("curl / sha512sum missing")
    srv = Mock().start()
    base = Path(tempfile.mkdtemp(prefix="kairos-fetch-"))
    try:
        files = {"deu-eng 1.9-fd1": blob("a", 70_000), "spa-deu 0.1": blob("b", 33_000)}
        lines = []
        for k, data in files.items():
            pair, ver = k.split()
            srv.files[f"/d/{pair}/{ver}/freedict-{pair}-{ver}.src.tar.xz"] = data
            lines.append(f"{pair} {ver} {len(data)} {hashlib.sha512(data).hexdigest()}")
        lst = base / "list.txt"
        lst.write_text("\n".join(lines) + "\n")
        dest = base / "out"
        sh = ["bash", str(REPO / "scripts" / "fetch-licence-sources.sh"), "--dest", str(dest), "--base-url", srv.url("/d"), "--list", str(lst)]
        r = subprocess.run(sh, capture_output=True, text=True, timeout=60)
        assert r.returncode == 0 and "2 fetched" in r.stdout, r.stdout + r.stderr
        assert (dest / "SHA512SUMS").read_text().count(".src.tar.xz") == 2 and (dest / "README.txt").exists()
        r = subprocess.run(sh, capture_output=True, text=True, timeout=60)
        assert r.returncode == 0 and "0 fetched, 2 already there" in r.stdout, r.stdout
        assert subprocess.run(sh + ["--check"], capture_output=True, text=True).returncode == 0
        (dest / "freedict-spa-deu-0.1.src.tar.xz").write_bytes(b"damaged")
        assert subprocess.run(sh + ["--check"], capture_output=True, text=True).returncode == 1
        r = subprocess.run(sh, capture_output=True, text=True, timeout=60)
        assert r.returncode == 0 and "1 fetched, 1 already there" in r.stdout, r.stdout
        bad = base / "bad.txt"
        bad.write_text(lines[0].rsplit(" ", 1)[0] + " " + "0" * 128 + "\n")
        dest2 = base / "out2"
        r = subprocess.run(["bash", str(REPO / "scripts" / "fetch-licence-sources.sh"), "--dest", str(dest2), "--base-url", srv.url("/d"),
                            "--list", str(bad)], capture_output=True, text=True, timeout=60)
        assert r.returncode == 1 and "FAILED" in r.stdout and not list(dest2.glob("*.src.tar.xz")) and not list(dest2.glob("*.part*")), r.stdout
        real = (REPO / "scripts" / "fetch-licence-sources.sh").read_text()
        rows = re.findall(r"^([a-z]{3}-[a-z]{3}) (\S+) (\d+) ([0-9a-f]{128})$", real, re.M)
        assert len(rows) == 8 and sum(int(x[2]) for x in rows) == 36_004_656, rows
        assert subprocess.run(["bash", str(REPO / "scripts" / "fetch-licence-sources.sh"), "--root", "/"], capture_output=True).returncode == 2
    finally:
        srv.stop()
        shutil.rmtree(base, ignore_errors=True)
    return "fetch + sha512, idempotent, --check, repair, bad sha refused, 8 real tarballs = 36 004 656 B"


def main():
    tests = [v for v in globals().values() if getattr(v, "_test", False)]
    only = [x.strip() for x in os.environ.get("KAIROS_TEST_ONLY", "").split(",") if x.strip()]
    if only:
        tests = [t for t in tests if any(o in t._name for o in only)]
    print(f"kAIrOS public images tests (R87) — {len(tests)} tests", flush=True)
    for t in tests:
        t({})
    for k in ("files", "cdn", "civitai"):
        if k in _K:
            _K[k].stop()
    if not only:
        out = {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "tests": RESULTS}
        (REPO / "tests" / "results-public-profile.json").write_text(json.dumps(out, indent=1))
    fails = [n for n, r in RESULTS.items() if r["status"] == "fail"]
    passes = sum(1 for r in RESULTS.values() if r["status"] == "pass")
    skips = sum(1 for r in RESULTS.values() if r["status"] == "skip")
    print(f"\n{passes} passed, {len(fails)} failed, {skips} skipped -> tests/results-public-profile.json", flush=True)
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
