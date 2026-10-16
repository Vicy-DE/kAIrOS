#!/usr/bin/env python3
"""kAIrOS — imaging without model thrash [R79, docs/IMAGING-WORKFLOW-V75-1.md] + the v7.5-1 pointer.

The owner's v7.5 stick run (docs/STICK-RUN-2026-10-07-v75.md): every picture swapped models on the
8 GB GPU (Gemma 4 -> the Qwen3VL-4B prompt expander -> sd -> Gemma), RAM was oversubscribed so every
swap re-read GBs from the stick, prewarm's idle-class read raced Gemma's first load, kernel-written
prompts were expanded a second time, and the software cursor flickered under Chromium's page flips.

Standalone: `python3 tests/test_imaging_workflow.py` (run-tests.sh runs it after the suite).
  A (no dependencies): the cursor conf; kairos-prewarm's order (the default LLM first, best-effort,
    16 MiB blocks, the wait/done flags) + RAM guard + USB-only logic, against PATH wrappers.
  B (in-process kernel import, aiohttp): expander model/key resolution + the setting; expand_prompts
    on MM.chat(key "llm") — one call per prompt, both slots at once; the kernel-written call sites;
    fadvise/mincore on real files; the auto rule; the periodic drops; the bounded prewarm wait.
  C (a kernel of this repo on :1356 with the suite's mock llama-server / sd-cli / sd-server, a 2-s
    sd-server idle window, VIBEOS_SD_FADVISE=on, a private /run/kairos): the first llama-server start
    waits for prewarm; the resident LLM expands (no expander server); wallpaper / app icon / forge
    assets are not expanded while /genimg and user prompts are; the image files leave the page cache,
    the LLM file stays; the idle window joins pictures, closes for any GPU waiter (FIFO, no
    starvation), never joins another model, keeps the state idle; the LLM comes back lazily.
Writes tests/results-imaging-workflow.json (not for a KAIROS_TEST_ONLY subset).
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
INC = REPO / "iso" / "config" / "includes.chroot"
PREWARM = INC / "usr" / "local" / "sbin" / "kairos-prewarm"
RESULTS = {}
# ports: the suite 1338-1340 / 8081-8083, 8085, 8090; test_llm_parallel 1343, 8087-8089, 8094-8096;
# test_image_lora 1346/1347, 8091-8093, 8097-8099; test_forge_quality 1351, 8111-8113
KPORT, LPORT, SDPORT, TRPORT = 1356, 8121, 8122, 8123
BASE = f"http://127.0.0.1:{KPORT}"
IDLE = 2.0                       # the C kernel's sd-server idle window (the OS ships 8 s)
EXPANDER_HEAD = "You are an expert prompt engineer for text-to-image models"


def record(name, reqs, status, detail=""):
    RESULTS[name] = {"reqs": reqs, "status": status, "detail": detail[:300]}
    mark = {"pass": "✓", "fail": "✗", "skip": "~"}[status]
    print(f"  {mark} {name} [{', '.join(reqs)}] {detail[:150]}", flush=True)


class SkipTest(Exception):
    pass


TESTS = []


def run_test(name, reqs, needs=None):
    """needs: None = files only, "kernel" = the in-process import, "http" = the :KPORT kernel"""
    def deco(fn):
        async def wrapper(ctx):
            if needs and not ctx.get("aiohttp"):
                record(name, reqs, "skip", "aiohttp not installed")
                return
            try:
                detail = await asyncio.wait_for(fn(ctx), timeout=240)
                record(name, reqs, "pass", detail or "")
            except SkipTest as e:
                record(name, reqs, "skip", str(e))
            except Exception as e:
                import traceback
                tb = traceback.extract_tb(e.__traceback__)
                where = next((f.lineno for f in reversed(tb) if f.filename == __file__), tb[-1].lineno if tb else 0)
                record(name, reqs, "fail", f"{type(e).__name__}: {e} @ {where}")
        wrapper._name = name
        TESTS.append(wrapper)
        return wrapper
    return deco


def manifest():
    return json.loads((REPO / "manifests" / "tier-8gb.json").read_text(encoding="utf-8"))


def kernel_module():
    sys.path.insert(0, str(REPO / "kernel"))
    import server
    return server


# ============================================================================= A. files only
@run_test("imaging_cursor_hw_on_amdgpu_conf", ["R79", "R28"])
async def t_cursor(ctx):
    """v7.5-1: amdgpu draws the pointer with its hardware cursor plane (v7.5's SWcursor flickered
    under Chromium's KMS page flips, top 1/6 = the menubar clock): no active xorg.conf.d file puts
    amdgpu on a software cursor; the old section ships as 21-…-amdgpu.conf.disabled (X reads only
    *.conf) with the one-line mv to re-enable it; the VMs' modesetting section stays; .xinitrc keeps
    its pointer diagnostics (the R78 agent's file — read, never written here)."""
    xdir = INC / "etc" / "X11" / "xorg.conf.d"
    active = sorted(f.name for f in xdir.iterdir() if f.name.endswith(".conf"))
    for name in active:
        code = "\n".join(l for l in (xdir / name).read_text().splitlines() if not l.lstrip().startswith("#"))
        for sec in re.findall(r'Section "OutputClass"(.*?)EndSection', code, re.S):
            m = re.search(r'MatchDriver\s+"([^"]+)"', sec)
            assert not (m and "amdgpu" in m.group(1).split("|") and "SWcursor" in sec), f"{name}: amdgpu SWcursor active"
    sw = (xdir / "20-kairos-swcursor.conf").read_text()
    code = "\n".join(l for l in sw.splitlines() if not l.lstrip().startswith("#"))
    assert code.count('Section "OutputClass"') == 1 and 'Driver "modesetting"' in code and '"SWcursor" "on"' in code \
        and "virtio_gpu" in code, code
    fb = xdir / "21-kairos-swcursor-amdgpu.conf.disabled"
    t = fb.read_text()
    assert 'MatchDriver "amdgpu"' in t and 'Driver "amdgpu"' in t and 'Option "SWcursor" "on"' in t
    line = f"mv /etc/X11/xorg.conf.d/{fb.name} /etc/X11/xorg.conf.d/21-kairos-swcursor-amdgpu.conf"
    assert line in t and line in sw, "the one-line re-enable is documented in both files"
    xi = (INC / "home" / "kairos" / ".xinitrc").read_text()
    assert "pointer_diag()" in xi and "( sleep 4; pointer_diag ) &" in xi, "the .xinitrc pointer diagnostics stay"
    return f"active confs {active}: no amdgpu SWcursor; fallback {fb.name} + one-line mv; modesetting kept; diag kept"


def _prewarm_world(td: Path, llm_bytes=3_000_000):
    """A fake root: desktop file, engine (lib + llama-server), models (the default LLM, another LLM,
    the encoder), a run dir and PATH wrappers that log dd/ionice/cat in order (+ the wait flag's
    content while dd runs)."""
    for d in ("d", "v/lib", "v/bin", "v/models/llm", "v/models/image", "run", "wbin"):
        (td / d).mkdir(parents=True)
    (td / "d" / "Xorg.so").write_bytes(b"x" * 200_000)
    (td / "v/lib/libcudart.so").write_bytes(b"c" * 300_000)
    (td / "v/bin/llama-server").write_bytes(b"s" * 10_000)
    (td / "v/models/llm/gemma.gguf").write_bytes(b"g" * llm_bytes)
    (td / "v/models/llm/coder.gguf").write_bytes(b"o" * 400_000)
    (td / "v/models/image/Qwen3VL.gguf").write_bytes(b"q" * 500_000)
    log = td / "calls.log"
    for tool in ("dd", "ionice", "cat"):
        real = shutil.which(tool)
        extra = (f'[ -f "{td}/run/prewarm-llm-wait" ] && echo "flag-during-dd $(cat {td}/run/prewarm-llm-wait)" >> "{log}"\n'
                 if tool == "dd" else "")
        (td / "wbin" / tool).write_text(f'#!/bin/sh\necho "{tool} $*" >> "{log}"\n{extra}exec {real} "$@"\n')
        (td / "wbin" / tool).chmod(0o755)
    env = dict(os.environ, PATH=f"{td / 'wbin'}:{os.environ.get('PATH', '/usr/bin:/bin')}",
               KAIROS_PREWARM_LOG=str(td / "prewarm.log"), KAIROS_PREWARM_VIBEOS=str(td / "v"),
               KAIROS_PREWARM_DESKTOP=str(td / "d"), KAIROS_PREWARM_RUN=str(td / "run"),
               KAIROS_PREWARM_MODELS=f"{td}/v/lib {td}/v/bin/llama-server {td}/v/models/llm {td}/v/models/image/Qwen3VL.gguf")
    (td / "v" / "active-tier").write_text("8gb\n")
    (td / "v" / "os" / "manifests").mkdir(parents=True)
    (td / "v" / "os" / "manifests" / "tier-8gb.json").write_text(json.dumps({"llm": {"file": "llm/gemma.gguf"}}))
    return env, log


@run_test("imaging_prewarm_llm_first_best_effort_flags", ["R79", "R41"])
async def t_prewarm_order(ctx):
    """kairos-prewarm [R79]: after the desktop, the DEFAULT LLM (resolved from active-tier ->
    os/manifests/tier-<t>.json, like the kernel) is read FIRST, at best-effort I/O (ionice -c 2 -n 4)
    with 16 MiB dd blocks, then the llama-server engine (lib + binary); the wait flag holds prewarm's
    PID while the LLM is read, the done flag follows it, and only then the rest of phase 2 runs at
    idle I/O — the LLM and engine files are not read twice. Without a resolvable LLM (or one outside
    the phase-2 list) the flags never appear and phase 2 is the old single pass."""
    with tempfile.TemporaryDirectory() as tdn:
        td = Path(tdn)
        env, log = _prewarm_world(td)
        subprocess.run(["sh", str(PREWARM)], env=dict(env, KAIROS_PREWARM_FORCE="1"), check=True, timeout=60)
        calls = log.read_text().splitlines()
        plog = (td / "prewarm.log").read_text()
        gemma = f"{td}/v/models/llm/gemma.gguf"
        i_be = calls.index(next(c for c in calls if c.startswith("ionice -c 2 -n 4")))
        i_dd = calls.index(next(c for c in calls if c.startswith("dd ")))
        i_idle = calls.index(next(c for c in calls if c.startswith("ionice -c 3")))
        assert calls[i_dd] == f"dd if={gemma} of=/dev/null bs=16M", calls[i_dd]
        flag = [c for c in calls if c.startswith("flag-during-dd ")]
        assert flag and flag[0].split()[1].isdigit(), ("the wait flag must hold prewarm's PID during the LLM read", calls)
        desk = calls.index(f"cat {td}/d/Xorg.so")
        eng = calls.index(f"cat {td}/v/lib/libcudart.so")
        rest = [c for c in calls[i_idle:] if c.startswith("cat ")]
        assert desk < i_be < i_dd < eng < i_idle, calls
        assert sorted(rest) == sorted([f"cat {td}/v/models/llm/coder.gguf", f"cat {td}/v/models/image/Qwen3VL.gguf"]), \
            ("phase 2 reads the rest once; the LLM + engine are not read again", rest)
        assert not (td / "run" / "prewarm-llm-wait").exists() and gemma in (td / "run" / "prewarm-llm-done").read_text()
        assert "phase 2a default LLM (best-effort I/O, 16 MiB blocks): gemma.gguf" in plog \
            and "phase 2a llama-server engine (best-effort I/O): 2 files" in plog \
            and "phase 2 engine + LLM (idle I/O): 2 files" in plog, plog
        # an LLM that is not on phase 2's list (e.g. KAIROS_PREWARM_MODELS narrowed) -> no flags, the old pass
        log.unlink()
        (td / "run" / "prewarm-llm-done").unlink()
        narrow = dict(env, KAIROS_PREWARM_FORCE="1", KAIROS_PREWARM_MODELS=f"{td}/v/models/image/Qwen3VL.gguf")
        subprocess.run(["sh", str(PREWARM)], env=narrow, check=True, timeout=60)
        calls = log.read_text().splitlines()
        assert not any(c.startswith("dd ") for c in calls) and not list((td / "run").iterdir()), calls
    return "desktop -> ionice be/4 -> dd if=<default LLM> bs=16M (wait flag = PID) -> engine -> done flag -> ionice idle -> the rest once"


@run_test("imaging_prewarm_ram_guard_and_usb_only", ["R79", "R41"])
async def t_prewarm_guard(ctx):
    """The RAM guard and the USB-only logic survive the reorder: a fixed root disk reads nothing and
    writes no flag; a phase 2 the RAM cannot hold (a 1 TiB sparse LLM) is skipped — no dd, no wait
    flag left behind for the kernel to wait on."""
    with tempfile.TemporaryDirectory() as tdn:
        td = Path(tdn)
        env, log = _prewarm_world(td)
        subprocess.run(["sh", str(PREWARM)], env=dict(env, KAIROS_PREWARM_ROOTDEV="/dev/null"), check=True, timeout=60)
        assert "not on USB" in (td / "prewarm.log").read_text() and not log.exists() and not list((td / "run").iterdir())
        os.truncate(td / "v/models/llm/gemma.gguf", 1 << 40)
        subprocess.run(["sh", str(PREWARM)], env=dict(env, KAIROS_PREWARM_FORCE="1"), check=True, timeout=60)
        calls = log.read_text().splitlines()
        plog = (td / "prewarm.log").read_text()
        assert "phase 2 skipped" in plog and not any(c.startswith("dd ") for c in calls), (plog, calls)
        assert not (td / "run" / "prewarm-llm-wait").exists() and not (td / "run" / "prewarm-llm-done").exists()
    unit = (INC / "etc" / "systemd" / "system" / "kairos-prewarm.service").read_text()
    assert "Type=simple" in unit and "ExecStart=-/usr/local/sbin/kairos-prewarm" in unit and os.access(PREWARM, os.X_OK)
    return "fixed disk: nothing read, no flag; 1 TiB LLM: phase 2 skipped, no dd, no flag; unit unchanged"


# ============================================================================= B. the kernel, in-process
@run_test("imaging_expander_model_resolution_and_setting", ["R79", "R34"], needs="kernel")
async def t_resolution(ctx):
    """image_expander auto|llm|encoder (default auto): auto = the section's "expander_model", else
    llm; expander_key() = "llm" or "expander:<kind>"; the setting is validated; the shipped
    manifests declare no expander_model (= the resident LLM everywhere)."""
    server = ctx["server"]
    assert server.SETTINGS_DEFAULTS["image_expander"] == "auto" and server.EXPANDER_MODES == ("auto", "llm", "encoder")
    assert server.expander_model_for("image", "auto") == "llm" and server.expander_key("image", "llm") == "llm"
    assert server.expander_model_for("image", "encoder") == "encoder" and server.expander_key("image", "encoder") == "expander:image"
    assert server.expander_model_for("image_safe", "llm") == "llm"
    saved = dict(server.MANIFEST["image"])
    try:
        server.MANIFEST["image"]["expander_model"] = "encoder"
        assert server.expander_model_for("image", "auto") == "encoder" and server.expander_model_for("image", "llm") == "llm"
        server.MANIFEST["image"]["expander_model"] = "nonsense"
        assert server.expander_model_for("image", "auto") == "llm"
    finally:
        server.MANIFEST["image"].clear()
        server.MANIFEST["image"].update(saved)
    for v in ("auto", "llm", "encoder"):
        assert server.validate_setting("image_expander", v) is None
    for bad in ("gpu", None, 1, "LLM"):
        assert "image_expander must be one of" in (server.validate_setting("image_expander", bad) or ""), bad
    for t in ("8gb", "16gb", "24gb", "32gb"):
        m = json.loads((REPO / "manifests" / f"tier-{t}.json").read_text())
        assert all("expander_model" not in sec for sec in m.values() if isinstance(sec, dict)), t
    return "auto -> llm (section expander_model honoured), encoder -> expander:<kind>; setting validated; manifests: llm everywhere"


@run_test("imaging_expand_prompts_on_resident_llm", ["R79", "R34", "R72"], needs="kernel")
async def t_expand_llm(ctx):
    """expand_prompts() asks MM.chat(key "llm") — the resident coder LLM — with the section's rule set
    (EXPANDERS, R76: krea-neutral / krea / krea-18plus), EXPAND_SAMPLING and EXPAND_MAX_TOKENS, ONE call
    per prompt, the batch's calls at once on the LLM's 2 slots; image_expander=encoder keeps the
    pre-R79 key expander:<kind>; an LLM failure renders the raw prompt + hint (never lost)."""
    server = ctx["server"]
    calls, live = [], {"n": 0, "max": 0}

    async def fake_chat(system, messages, key="llm", max_tokens=16384, temperature=0.7, sampling=None):
        live["n"] += 1
        live["max"] = max(live["max"], live["n"])
        calls.append({"key": key, "system": system, "user": messages[-1]["content"], "max_tokens": max_tokens,
                      "temperature": temperature, "sampling": sampling})
        try:
            await asyncio.sleep(0.3)
            if "__fail__" in messages[-1]["content"]:
                raise RuntimeError("llama-server: boom")
            return "A long and detailed photograph description of " + messages[-1]["content"] + ", soft light."
        finally:
            live["n"] -= 1
    orig, before = server.MM.chat, server.load_settings().get("image_expander")
    server.MM.chat = fake_chat
    try:
        out = await server.expand_prompts("image", ["a red fox", "a blue bird"])
        assert [c["key"] for c in calls] == ["llm", "llm"] and live["max"] == 2, (calls, live)
        assert all(c["system"] == server.EXPANDERS["krea-neutral"] and c["sampling"] == server.EXPAND_SAMPLING
                   and c["max_tokens"] == server.EXPAND_MAX_TOKENS == 512 and c["temperature"] == 0.7 for c in calls)
        assert all(o["expanded"] and o["expander"] == "krea-neutral" and o["expander_model"] == "llm" for o in out), out
        assert out[0]["prompt"].startswith("A long and detailed photograph description of a red fox")
        calls.clear()
        out = await server.expand_prompts("image_safe", ["a cat"], adult=True)
        assert calls[0]["system"] == server.EXPANDERS["krea-18plus"] and calls[0]["key"] == "llm", calls
        calls.clear()
        out = await server.expand_prompts("image", ["__fail__ a dog"])
        assert out[0]["expanded"] is False and out[0]["prompt"] == "__fail__ a dog" + server.prompts.IMAGE_PROMPT_HINT, out
        calls.clear()
        s = server.load_settings()
        s["image_expander"] = "encoder"
        server.save_settings(s)
        out = await server.expand_prompts("image", ["a red fox"])
        assert [c["key"] for c in calls] == ["expander:image"] and out[0]["expander_model"] == "encoder", calls
        calls.clear()
        out = await server.expand_prompts("image", ["a red fox"], setting="off")
        assert not calls and out[0]["expander"] is None and out[0]["expander_model"] is None
    finally:
        server.MM.chat = orig
        s = server.load_settings()
        s["image_expander"] = before or "auto"
        server.save_settings(s)
    return "key llm, neutral/18+ rules, EXPAND_SAMPLING + 512 tokens, 2 calls in flight; encoder setting -> expander:image; failure -> raw + hint"


@run_test("imaging_kernel_written_prompts_call_sites", ["R79", "R44", "R60", "R36"], needs="kernel")
async def t_call_sites(ctx):
    """The kernel's own prompts never take a second rewrite: the wallpaper's scene and the forge's
    data-gen pictures reach render_images with expand="off", the app icon goes through MM.sd_job
    (-> sd_batch, no expansion at all); /genimg page pictures and the user's /api/image, /api/images
    keep the setting (auto)."""
    src = (REPO / "kernel" / "server.py").read_text()
    wall = src.split("async def h_wallpaper(")[1].split("\nasync def ")[0]
    assert re.search(r"await render_images\(kind, \[\{\"prompt\": scene, [^\n]*\], expand=\"off\"\)", wall), "wallpaper"
    forge = src.split("async def render_generated_media(")[1].split("\nasync def ")[0]
    assert 'await render_images("image", wanted, expand="off")' in forge, "forge assets"
    icon = src.split("async def generate_app_icon(")[1].split("\nasync def ")[0]
    assert "MM.sd_job(" in icon and "render_images(" not in icon and "expand_prompts(" not in icon, "app icon"
    flush = src.split("async def _genimg_flush(")[1].split("\ndef ")[0]
    assert "render_images(kind, [" in flush and "expand=" not in flush, "/genimg keeps the setting"
    for h in ("async def h_image(", "async def h_images("):
        body = src.split(h)[1].split("\nasync def ")[0]
        assert 'expand=body.get("expand")' in body, f"{h} keeps the per-call/settings expansion"
    return "wallpaper + forge: expand=\"off\"; icon: sd_job only; /genimg, /api/image(s): the setting"


@run_test("imaging_fadvise_and_residency_on_real_files", ["R79"], needs="kernel")
async def t_fadvise_real(ctx):
    """fadvise_dontneed() drops a file's cached pages (page_cache_residency: mincore over a read-only
    map, 1.0 after a read -> 0.0), a file it was not given stays resident; a missing file is skipped.
    Skips on a file system that ignores the advice (tmpfs keeps its pages: they are the only copy)."""
    server = ctx["server"]
    with tempfile.TemporaryDirectory() as tdn:
        a, b = Path(tdn) / "dit.gguf", Path(tdn) / "llm.gguf"
        for p in (a, b):
            with open(p, "wb") as f:
                f.write(os.urandom(8 << 20))
                f.flush()
                os.fsync(f.fileno())
            p.read_bytes()
        ra, rb = server.page_cache_residency(a), server.page_cache_residency(b)
        if ra is None or ra < 0.9:
            raise SkipTest(f"residency not measurable here ({ra})")
        n = server.fadvise_dontneed([a, Path(tdn) / "missing.gguf"])
        ra2, rb2 = server.page_cache_residency(a), server.page_cache_residency(b)
        if ra2 is not None and ra2 > 0.5:
            raise SkipTest(f"this file system keeps advised pages (tmpfs?): {ra:.2f} -> {ra2:.2f}")
        assert n == 1 and ra2 < 0.05 and rb2 > 0.9, (n, ra, ra2, rb, rb2)
        ctx["fadvise_works"] = True
    assert server.page_cache_residency("/nonexistent/file") is None
    return f"dropped file {ra:.2f} -> {ra2:.2f}, untouched file {rb2:.2f}; missing file skipped"


@run_test("imaging_drop_policy_auto_and_periodic", ["R79"], needs="kernel")
async def t_drop_policy(ctx):
    """sd_drop_wanted: auto = RAM (MemTotal / cgroup) < LLM + 2 × image files + 6 GiB — on the 8 GB tier
    (the heretic Gemma 13.9 GB, Muse + encoder + VAE 11.1 GB — since 2026-10-10 without a LoRA) a 30 GB machine drops, a
    64 GB one keeps the cache; on/off force it. _drop_start: every SD_FADVISE_EVERY s on exactly the section's
    files (DiT, text encoder, VAE and a LoRA when the section has one), once more at finish(), then the
    residency log line."""
    server = ctx["server"]
    m = manifest()
    sd_keys = [k for k in ("diffusion", "text_encoder", "vae", "lora") if k in m["image"]]
    with tempfile.TemporaryDirectory() as tdn:
        models = Path(tdn)
        sizes = {m["llm"]["file"]: m["llm"]["size_bytes"]}
        for k in sd_keys:
            sizes[m["image"][k]["file"]] = m["image"][k].get("size_bytes") or int(m["image"][k]["size_gb"] * 1e9)
        for f, n in sizes.items():
            p = models / f
            p.parent.mkdir(parents=True, exist_ok=True)
            with open(p, "wb"):
                pass
            os.truncate(p, n)                                  # sparse: the real sizes, no disk used
        saved = (server.MODELS_DIR, server.ram_limit_bytes, server.SD_FADVISE, server.SD_FADVISE_EVERY,
                 server.fadvise_dontneed, server.page_cache_residency)
        calls = []
        try:
            server.MODELS_DIR = models
            mm = server.ModelManager(m)
            files = mm.sd_files("image")
            assert sorted(p.relative_to(models).as_posix() for p in files) == sorted(m["image"][k]["file"] for k in sd_keys), files
            server.SD_FADVISE = "auto"
            server.ram_limit_bytes = lambda: 30 << 30
            assert mm.sd_drop_wanted(files) is True
            server.ram_limit_bytes = lambda: 64 << 30
            assert mm.sd_drop_wanted(files) is False
            server.SD_FADVISE = "on"
            assert mm.sd_drop_wanted(files) is True and mm.sd_drop_wanted([]) is False
            server.SD_FADVISE = "off"
            assert mm.sd_drop_wanted(files) is False
            server.SD_FADVISE, server.SD_FADVISE_EVERY = "on", 0.2
            server.fadvise_dontneed = lambda paths: calls.append((time.monotonic(), sorted(str(p) for p in paths))) or len(paths)
            server.page_cache_residency = lambda p: 0.97 if str(p).endswith(Path(m["llm"]["file"]).name) else 0.01
            import contextlib
            import io
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                finish = mm._drop_start("image")
                await asyncio.sleep(0.75)
                finish()
                await asyncio.sleep(0.3)                      # the loop is gone: no call after finish
            n_after = len(calls)
            await asyncio.sleep(0.3)
            out = buf.getvalue()
        finally:
            (server.MODELS_DIR, server.ram_limit_bytes, server.SD_FADVISE, server.SD_FADVISE_EVERY,
             server.fadvise_dontneed, server.page_cache_residency) = saved
    want = sorted(str(p) for p in files)
    assert 4 <= len(calls) <= 7 and all(c[1] == want for c in calls) and len(calls) == n_after, calls
    gaps = [b[0] - a[0] for a, b in zip(calls, calls[1:-1])]
    assert all(0.1 < g < 0.5 for g in gaps), gaps
    # [Muse 2026-10-10] DiT 8 316 720 160 + encoder 2 497 281 664 + VAE 253 806 246 B = 11.1 GB, no LoRA (v7.5: 4 files, 12.2 GB)
    assert "page cache: image's 3 file(s) (11.1 GB) are dropped while sd reads them" in out, out
    assert "page cache after the image engine: LLM gemma-4-26B-A4B-it-ultra-uncensored-heretic.i1-IQ4_XS.gguf 97% resident, image files 1%" in out, out   # [R86 round 4] the heretic default
    return f"auto: 30 GB drops / 64 GB keeps; on/off; {len(calls)} drops of the 3 files in 0.75 s + final; residency logged"


@run_test("imaging_sd_mmap_opt_in", ["R79"], needs="kernel")
async def t_mmap(ctx):
    """VIBEOS_SD_MMAP=1 (opt-in, off by default): the image sections' sd argv (sd-server and sd-cli,
    _sd_model_args) gains --mmap — the weights stay page cache instead of a pinned copy — and the auto
    drop turns itself off for them (the cache IS sd's copy); video keeps its argv and the drop rule;
    off = the argv every other test pins."""
    server = ctx["server"]
    assert server.SD_MMAP is False and "--mmap" not in server.MM._sd_model_args("image"), "default: no --mmap"
    m = manifest()
    saved = (server.SD_MMAP, server.SD_FADVISE, server.ram_limit_bytes)
    try:
        server.SD_MMAP, server.SD_FADVISE = True, "auto"
        server.ram_limit_bytes = lambda: 4 << 30          # below the rule's 6 GiB floor: auto would drop
        mm = server.ModelManager(m)
        assert server.MM._sd_model_args("image")[-1] == "--mmap" and "--mmap" in mm._sd_model_args("image_safe")
        assert "--mmap" not in mm._sd_model_args("video")
        files = [Path(__file__)]                          # any existing file
        assert mm.sd_drop_wanted(files, "image") is False and mm.sd_drop_wanted(files, "video") is True
        server.SD_FADVISE = "on"
        assert mm.sd_drop_wanted(files, "image") is True, "an explicit on still drops"
    finally:
        server.SD_MMAP, server.SD_FADVISE, server.ram_limit_bytes = saved
    return "off by default; on: --mmap on image argv (not video), auto drop off for image, explicit on still drops"


@run_test("imaging_r80_gpu_status_names", ["R80"], needs="kernel")
async def t_r80_unit(ctx):
    """[R80] ModelManager.gpu_status(): kind none at boot; the menubar names — short_model_name() cuts a
    manifest name at its first ' (' / ' —' (Gemma 4 26B-A4B-it ultra-uncensored heretic — the R86 round-4 default, Muse, Wan2.2-TI2V-5B …), the image session
    adds an applied LoRA (none since Muse: the default = 'Muse', image_safe = 'Muse SFW'); a recorded llama-server that
    is gone reports none, one still loading reports loading; _gpu_clear(kind) only clears that kind."""
    server = ctx["server"]
    m = manifest()
    sn = server.short_model_name
    assert sn(m["llm"]["name"]) == "Gemma 4 26B-A4B-it ultra-uncensored heretic" and sn(m["image"]["name"]) == "Muse" and sn(m["image_safe"]["name"]) == "Muse SFW" \
        and sn(m["video"]["name"]) == "Wan2.2-TI2V-5B" and sn("plain") == "plain" and sn("") == ""
    assert server.lora_short_name("kroma-v0.3-base-lora-rank-384-fro-0985-bf16") == "Kroma"
    mm = server.ModelManager(m)
    assert mm.gpu_status()["kind"] == "none" and mm.gpu_status()["name"] is None
    assert mm.image_display_name("image") == "Muse" and mm.image_display_name("image_safe") == "Muse SFW"

    class _Proc:
        returncode = None
    mm._gpu_set("llm", "llm", m["llm"]["name"])
    assert mm.gpu_status()["kind"] == "none", "no llama-server process: nothing resident"
    mm.llama_proc = _Proc()
    g = mm.gpu_status()
    assert g["kind"] == "llm" and g["name"] == "Gemma 4 26B-A4B-it ultra-uncensored heretic" and g["loading"] is True and g["full_name"] == m["llm"]["name"]
    mm.llama_ready = True
    assert mm.gpu_status()["loading"] is False
    mm._gpu_clear("image")
    assert mm.gpu_status()["kind"] == "llm", "clearing another kind leaves the LLM"
    mm._gpu_set("image", "image", m["image"]["name"], mm.image_display_name("image"))
    assert mm.gpu_status() == {**mm.gpu, "kind": "image", "name": "Muse"} and "loading" not in mm.gpu_status()
    mm._gpu_clear("image")
    assert mm.gpu_status()["kind"] == "none"
    sh = (REPO / "shell" / "shell.js").read_text()
    body = sh.split("async function pollStatus()")[1].split("\n}\n")[0]
    assert "s.gpu" in body and '"no model"' in body and '$("#mb-tier").textContent = `${s.tier} · ${res}`' in body \
        and '$("#mb-state").textContent = s.state' in body, body[:600]
    return "names: Gemma 4 26B-A4B-it / Muse / Muse SFW / Wan2.2-TI2V-5B; dead llama = none, loading flag; menubar uses s.gpu"


@run_test("imaging_first_llama_start_waits_for_prewarm", ["R79", "R41"], needs="kernel")
async def t_prewarm_wait(ctx):
    """_await_prewarm(): the kernel's FIRST llama-server start waits while kairos-prewarm (its PID in
    PREWARM_RUN/prewarm-llm-wait, no prewarm-llm-done yet) still reads the LLM — bounded by
    PREWARM_LLM_WAIT; it ends early when the done flag appears or the PID is gone; later starts never
    wait; no flag = no wait. _start_llama calls it before anything else."""
    server = ctx["server"]
    src = (REPO / "kernel" / "server.py").read_text()
    body = src.split("    async def _start_llama(self, key, spec):")[1]
    assert body.lstrip().startswith("await self._await_prewarm()"), body[:120]
    sleeper = subprocess.Popen(["sleep", "60"])
    saved = (server.PREWARM_RUN, server.PREWARM_LLM_WAIT)
    try:
        with tempfile.TemporaryDirectory() as tdn:
            run = Path(tdn)
            server.PREWARM_RUN, server.PREWARM_LLM_WAIT = run, 1.5
            m = manifest()

            async def timed():
                mm = server.ModelManager(m)
                t0 = time.monotonic()
                await mm._await_prewarm()
                d = time.monotonic() - t0
                t1 = time.monotonic()
                await mm._await_prewarm()                      # a later start of the same kernel
                assert time.monotonic() - t1 < 0.05
                return d
            assert await timed() < 0.05, "no flag: no wait"
            (run / "prewarm-llm-wait").write_text(f"{sleeper.pid}\n")
            d_bound = await timed()
            assert 1.4 <= d_bound < 2.8, f"bounded wait took {d_bound:.2f} s"

            async def done_soon():
                await asyncio.sleep(0.6)
                (run / "prewarm-llm-done").write_text("18:00:00 300.0s /opt/vibeos/models/llm/x.gguf\n")
            server.PREWARM_LLM_WAIT = 10
            task = asyncio.create_task(done_soon())
            d_done = await timed()
            await task
            assert 0.5 <= d_done < 2.0, f"the done flag ended the wait after {d_done:.2f} s"
            (run / "prewarm-llm-done").unlink()
            (run / "prewarm-llm-wait").write_text("999999999\n")      # a PID that is gone
            assert await timed() < 0.05
            (run / "prewarm-llm-wait").write_text("junk\n")
            assert await timed() < 0.05
    finally:
        sleeper.kill()
        sleeper.wait()
        server.PREWARM_RUN, server.PREWARM_LLM_WAIT = saved
    return f"no flag 0 s; live PID: bounded {d_bound:.1f} s (limit 1.5); done flag after 0.6 s -> {d_done:.1f} s; dead/junk PID 0 s; later starts never wait"


# ============================================================================= C. a kernel of its own
class Kernel:
    def __init__(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kairos-r79-"))
        self.proc = None
        self.logf = None
        self.sleeper = None

    def setup(self):
        t = self.tmp
        for d in ("bin", "cache", "models", "games", "manifests", "log", "run"):
            (t / d).mkdir()
        (t / "bin" / "llama-server").symlink_to(REPO / "tests" / "mock-llama-server")
        (t / "bin" / "sd-cli").symlink_to(REPO / "tests" / "mock-sd-cli")
        (t / "bin" / "sd-server").symlink_to(REPO / "tests" / "mock-sd-server")
        subprocess.run([sys.executable, str(REPO / "scripts" / "engine-stamp.py"), "--mock",
                        "--out", str(t / "bin" / "ENGINES.json")], check=True, capture_output=True)
        shutil.copy(REPO / "manifests" / "tier-8gb.json", t / "manifests" / "tier-8gb.json")
        files = set()

        def walk(n):
            if isinstance(n, dict):
                if "url" in n and "file" in n and n.get("store") != "knowledge":
                    files.add(n["file"])
                for v in n.values():
                    walk(v)
        walk(manifest())
        for f in files:
            p = t / "models" / f
            p.parent.mkdir(parents=True, exist_ok=True)
            p.touch()
        # kairos-prewarm still "reading the LLM": a live PID in the wait flag (test C0 ends it)
        self.sleeper = subprocess.Popen(["sleep", "120"])
        (t / "run" / "prewarm-llm-wait").write_text(f"{self.sleeper.pid}\n")
        self.env = {k: v for k, v in os.environ.items() if not k.startswith(("VIBEOS_", "MOCK_", "KAIROS_PREWARM"))
                    and k != "KAIROS_URL"}
        self.env.update(VIBEOS_DIR=str(t), VIBEOS_SHELL=str(REPO / "shell"), VIBEOS_MANIFESTS=str(t / "manifests"),
                        VIBEOS_MANIFEST=str(t / "manifests" / "tier-8gb.json"), VIBEOS_PORT=str(KPORT),
                        VIBEOS_LLAMA_PORT=str(LPORT), VIBEOS_SD_PORT=str(SDPORT), VIBEOS_TRANSLATE_PORT=str(TRPORT),
                        VIBEOS_FORCE_OFFLINE="1", KAIROS_LOG_DIR=str(t / "log"), VIBEOS_TOKEN_FILE=str(t / "token"),
                        VIBEOS_NVIDIA_PROC="/nonexistent", MOCK_SD_ARGLOG=str(t / "sd-args.log"),
                        MOCK_SDSERVER_LOG=str(t / "sd-server.log"), MOCK_SDSERVER_LOAD_S="0.3",
                        MOCK_SDSERVER_RENDER_S="0.2", MOCK_LLAMA_ARGLOG=str(t / "llama-args.log"),
                        MOCK_LLAMA_MSGLOG=str(t / "llama-msgs.log"), PYTHONUNBUFFERED="1",
                        VIBEOS_SD_SESSION_IDLE=str(IDLE), VIBEOS_SD_FADVISE="on",
                        KAIROS_PREWARM_RUN=str(t / "run"), KAIROS_PREWARM_WAIT="20")

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
            assert self.proc.poll() is None, "kernel died at boot: " + self.log_tail()
            await asyncio.sleep(0.5)
        raise AssertionError("kernel never answered: " + self.log_tail())

    def log(self):
        if self.logf:
            self.logf.flush()
        p = self.tmp / "kernel.log"
        return p.read_text(errors="ignore") if p.exists() else ""

    def log_tail(self, n=2500):
        return self.log()[-n:]

    def sd_log(self):
        p = self.tmp / "sd-server.log"
        return [l.split("\t", 1) for l in p.read_text().splitlines()] if p.exists() else []

    def launches(self):
        return [l for l in self.sd_log() if l[0] == "LAUNCH"]

    def reqs(self):
        return [json.loads(l[1]) for l in self.sd_log() if l[0] == "REQ"]

    def llama_launches(self):
        p = self.tmp / "llama-args.log"
        return p.read_text().splitlines() if p.exists() else []

    def msgs(self):
        p = self.tmp / "llama-msgs.log"
        return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []

    def expander_msgs(self):
        return [m for m in self.msgs() if (m.get("system") or "").startswith(EXPANDER_HEAD)]

    def sd_server_running(self):
        """an sd-server of THIS kernel is alive (the kernel execs bin/sd-server -> the mock; its argv
        carries --listen-port SDPORT)"""
        mark = b"--listen-port\x00" + str(SDPORT).encode() + b"\x00"
        for d in Path("/proc").iterdir():
            if d.name.isdigit():
                try:
                    if mark in (d / "cmdline").read_bytes():
                        return True
                except OSError:
                    pass
        return False

    def close(self):
        if self.sleeper and self.sleeper.poll() is None:
            self.sleeper.kill()
            self.sleeper.wait()
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        if self.logf:
            self.logf.close()
        keep = os.environ.get("R79_KEEP_LOGS")
        if keep:
            Path(keep).mkdir(parents=True, exist_ok=True)
            for f in ("kernel.log", "sd-args.log", "sd-server.log", "llama-msgs.log", "llama-args.log"):
                if (self.tmp / f).exists():
                    shutil.copy(self.tmp / f, Path(keep) / f)
        shutil.rmtree(self.tmp, ignore_errors=True)


async def api(ctx, method, path, body=None, timeout=60):
    import aiohttp
    s = ctx["session"]
    async with s.request(method, f"{BASE}{path}", json=body, timeout=aiohttp.ClientTimeout(total=timeout)) as r:
        return r.status, await r.json(content_type=None)


async def job(ctx, path, body, timeout=60):
    st, j = await api(ctx, "POST", path, body)
    assert st == 200 and "job" in j, (st, j)
    t0 = time.time()
    while time.time() - t0 < timeout:
        _, d = await api(ctx, "GET", f"/api/job/{j['job']}")
        if d["status"] in ("done", "error"):
            d["t_done"] = time.time()
            return d
        await asyncio.sleep(0.05)
    raise AssertionError(f"job {j['job']} timed out")


async def ask(ctx, prompt):
    t0 = time.time()
    st, r = await api(ctx, "POST", "/api/ask", {"prompt": prompt}, timeout=90)
    assert st == 200, (st, r)
    return {"t0": t0, "t1": time.time(), "text": r.get("text", "")}


async def settle(ctx):
    """until no sd-server session is open (the window closed, its server gone)"""
    k = ctx["kernel"]
    for _ in range(int((IDLE + 8) / 0.1)):
        if not k.sd_server_running():
            return
        await asyncio.sleep(0.1)
    raise AssertionError("an sd-server session never closed: " + k.log_tail(800))


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


@run_test("imaging_e2e_first_start_waits_for_prewarm", ["R79", "R41"], needs="http")
async def t_c0_prewarm(ctx):
    """The kernel booted while "kairos-prewarm" (a live PID in its run dir's prewarm-llm-wait) still
    reads the LLM: the first LLM request's llama-server start waits until prewarm-llm-done appears
    (here after 1.5 s), the journal says so, and the request is then answered; the next one does not
    wait."""
    k = ctx["kernel"]
    run = k.tmp / "run"

    async def done_later():
        await asyncio.sleep(1.5)
        (run / "prewarm-llm-done").write_text("12:00:00 250.0s llm\n")
    task = asyncio.create_task(done_later())
    r = await ask(ctx, "how warm is the stick?")
    await task
    took = r["t1"] - r["t0"]
    log = k.log()
    assert "kairos-prewarm (pid" in log and "the first llama-server start waits for it" in log \
        and "prewarm wait over after" in log and "the LLM is read" in log, log[-800:]
    assert took >= 1.4, f"the first start did not wait ({took:.2f} s)"
    r2 = await ask(ctx, "and now?")
    assert r2["t1"] - r2["t0"] < 1.0 and log.count("prewarm wait over") == k.log().count("prewarm wait over")
    return f"first ask waited for prewarm-llm-done ({took:.1f} s), second {r2['t1'] - r2['t0']:.2f} s"


@run_test("imaging_e2e_resident_llm_expands_no_expander_server", ["R79", "R34", "R72"], needs="http")
async def t_c1_llm(ctx):
    """A user picture (/api/image, auto) and a batch (/api/images, 2 prompts) are expanded by the
    resident LLM: the expander's system prompt reaches the LLM's server (one request per prompt), no
    llama-server is launched with the Qwen3VL-4B encoder (only the LLM's GGUF), the picture renders
    from the expansion (sd-server REQ), job.expander_model = llm."""
    k = ctx["kernel"]
    await settle(ctx)
    n_exp, n_llama = len(k.expander_msgs()), len(k.llama_launches())
    d = await job(ctx, "/api/image", {"prompt": "a lighthouse at dusk"})
    assert d["status"] == "done" and d["expanded"] is True and d["expander"] == "krea-neutral" \
        and d["expander_model"] == "llm" and d["engine"] == "sd-server", d
    req = k.reqs()[-1]
    assert "expanded by the mock expander for everyone" in req["prompt"] and req["prompt"] == d["prompt"], req
    await settle(ctx)
    d2 = await job(ctx, "/api/images", {"prompts": ["a red kite", "a green kite"]})
    assert d2["status"] == "done" and all(r["expanded"] and r["expander_model"] == "llm" for r in d2["results"]), d2
    ex = k.expander_msgs()[n_exp:]
    assert [m["user"] for m in ex] == ["a lighthouse at dusk", "a red kite", "a green kite"] or \
        sorted(m["user"] for m in ex) == sorted(["a lighthouse at dusk", "a red kite", "a green kite"]), ex
    launched = k.llama_launches()[n_llama:]
    llm_file = Path(manifest()["llm"]["file"]).name
    assert launched and all(llm_file in l and "Qwen3VL" not in l for l in launched), launched
    assert not any("Qwen3VL-4B-Instruct-Q4_K_M.gguf" in l and "--mmproj" not in l for l in k.llama_launches()), \
        "an expander server was launched"
    return f"3 expansions on the LLM's server, {len(launched)} llama launch(es) all {llm_file}, none of the encoder"


@run_test("imaging_e2e_kernel_prompts_not_expanded", ["R79", "R44", "R60", "R36", "R58"], needs="http")
async def t_c2_kernel_prompts(ctx):
    """The wallpaper renders the LLM's scene + hint, the app icon its template, the forge's data-gen
    picture its own text + hint — none of them sends an expander request; a /genimg page picture is
    expanded (auto)."""
    k = ctx["kernel"]
    await settle(ctx)
    hint = ", high quality, sharp detail"
    # the wallpaper (the session log has events from the earlier tests)
    n = len(k.expander_msgs())
    st, w = await api(ctx, "POST", "/api/wallpaper", {})
    assert st == 200 and w.get("job"), w
    t0 = time.time()
    while True:
        _, d = await api(ctx, "GET", f"/api/job/{w['job']}")
        if d["status"] in ("done", "error"):
            break
        assert time.time() - t0 < 60, d
        await asyncio.sleep(0.1)
    assert d["status"] == "done" and d["expanded"] is False and d["prompt"] == w["scene"].strip() + hint, (d, w)
    assert len(k.expander_msgs()) == n, "the wallpaper's scene went through the expander"
    assert any(r["prompt"] == d["prompt"] for r in k.reqs()), "the wallpaper's REQ"
    await settle(ctx)
    # an app + its icon (background, after the app is on screen)
    ev = await app_events(ctx, {"prompt": "a tiny tally counter. Counts things.", "app_id": "r79-icon"})
    assert any(e == "done" for e, _ in ev), ev[-3:]
    t0 = time.time()
    while not (k.tmp / "cache" / "files" / "icons" / "r79-icon.png").exists():
        assert time.time() - t0 < 30, "no icon: " + k.log_tail(600)
        await asyncio.sleep(0.2)
    icon_req = [r for r in k.reqs() if r["prompt"].endswith("no text, no words, sharp, high quality")]
    assert icon_req and icon_req[-1]["prompt"].startswith("a tiny tally counter, app icon,"), icon_req[-1:]
    assert len(k.expander_msgs()) == n, "the icon template went through the expander"
    await settle(ctx)
    # forge assets: the App Launcher forge's data-gen picture
    ev = await app_events(ctx, {"prompt": "a gallery with one forged picture", "app_id": "r79-forge", "assets": True})
    assert any(e == "done" for e, _ in ev), ev[-3:]
    forge_req = [r for r in k.reqs() if r["prompt"].startswith("a glowing plasma orb")]
    assert forge_req and forge_req[-1]["prompt"] == "a glowing plasma orb" + hint, forge_req
    t0 = time.time()                                       # its icon is painted in the background after "done"
    while not (k.tmp / "cache" / "files" / "icons" / "r79-forge.png").exists():
        assert time.time() - t0 < 30, "no forge icon: " + k.log_tail(600)
        await asyncio.sleep(0.2)
    assert len(k.expander_msgs()) == n, "the forge's data-gen text went through the expander"
    await settle(ctx)
    # a page picture keeps the setting: expanded
    async with ctx["session"].get(f"{BASE}/genimg/a%20quiet%20harbour%20at%20night") as r:
        body = await r.read()
    assert body[:4] == b"\x89PNG", body[:40]
    assert [m["user"] for m in k.expander_msgs()[n:]] == ["a quiet harbour at night"], k.expander_msgs()[n:]
    harbour = [r for r in k.reqs() if "quiet harbour" in r["prompt"]]
    assert harbour and "expanded by the mock expander" in harbour[-1]["prompt"], harbour
    await settle(ctx)
    return "wallpaper = scene + hint, icon = template, forge = data-gen + hint (0 expander calls); /genimg expanded (1 call)"


@run_test("imaging_e2e_image_files_leave_page_cache", ["R79"], needs="http")
async def t_c3_cache(ctx):
    """VIBEOS_SD_FADVISE=on: an sd-server session logs the drop of the section's 3 files (Muse DiT,
    encoder, VAE — no LoRA since 2026-10-10) and their residency at the end; the image files (DiT made
    real and read into the cache before the session)
    leave the page cache while the LLM's GGUF (also real and cached) stays resident."""
    k, server = ctx["kernel"], ctx["server"]
    await settle(ctx)
    m = manifest()
    dit, llm = k.tmp / "models" / m["image"]["diffusion"]["file"], k.tmp / "models" / m["llm"]["file"]
    try:
        for p in (dit, llm):
            with open(p, "wb") as f:
                f.write(os.urandom(8 << 20))
                f.flush()
                os.fsync(f.fileno())
            p.read_bytes()
        r0 = (server.page_cache_residency(dit), server.page_cache_residency(llm))
        mark = len(k.log())
        d = await job(ctx, "/api/images", {"prompts": ["a cached orb"], "expand": "off"})
        assert d["status"] == "done", d
        await settle(ctx)
        log = k.log()[mark:]
        assert "page cache: image's 3 file(s)" in log and "are dropped while sd reads them" in log, log[-600:]
        assert "page cache after the image engine: LLM gemma-4-26B-A4B-it-ultra-uncensored-heretic.i1-IQ4_XS.gguf" in log, log[-600:]   # [R86 round 4]
        r1 = (server.page_cache_residency(dit), server.page_cache_residency(llm))
    finally:
        for p in (dit, llm):
            p.write_bytes(b"")
    if not ctx.get("fadvise_works"):
        return f"drop + residency lines logged; residency not asserted here (file system: {r0} -> {r1})"
    assert r0[0] > 0.9 and r0[1] > 0.9 and r1[0] < 0.05 and r1[1] > 0.9, (r0, r1)
    return f"DiT {r0[0]:.2f} -> {r1[0]:.2f} (dropped), LLM {r0[1]:.2f} -> {r1[1]:.2f} (kept); log lines present"


@run_test("imaging_e2e_session_idle_window_joins_and_fifo", ["R79", "R72", "R42"], needs="http")
async def t_c4_window(ctx):
    """The sd-server idle window (2 s here, 8 s on the OS): (a) the first batch's job is answered
    right away (its latency has no idle window in it), a second batch of the same model arriving
    within the window JOINS the session — no second launch, the state is "idle" meanwhile; (b) an LLM
    request closes the window at once ("a GPU waiter", not the timeout) and a picture that arrives
    behind it waits its turn (FIFO: a new session after the answer — no starvation, nothing jumps
    the queue); (c) another model (safe) never joins: its own session; (d) the LLM is not reloaded
    after a session until the next LLM request asks for it."""
    k = ctx["kernel"]
    await settle(ctx)
    n_l, n_r, n_ll = len(k.launches()), len(k.reqs()), len(k.llama_launches())
    t0 = time.time()
    a = await job(ctx, "/api/images", {"prompts": ["window one"], "expand": "off"})
    lat_a = a["t_done"] - t0
    assert a["status"] == "done", a
    # the job is answered while its session is still open: its latency never contains the idle window
    assert k.sd_server_running(), "the session closed right after the batch (or the job waited for the window)"
    st, s1 = await api(ctx, "GET", "/api/status")
    assert s1["state"] == "idle", s1["state"]
    b = await job(ctx, "/api/images", {"prompts": ["window two"], "expand": "off"})
    assert b["status"] == "done" and len(k.launches()) == n_l + 1 and len(k.reqs()) == n_r + 2, (k.launches()[n_l:], k.reqs()[n_r:])
    assert "1 picture(s) joined the open session (no model load)" in k.log()
    assert len(k.llama_launches()) == n_ll, "an image session restarted the LLM"
    # (b) an LLM request closes the window at once; a picture right behind it waits its turn
    mark = len(k.log())
    t_ask = asyncio.create_task(ask(ctx, "a question while the window is open"))
    await asyncio.sleep(0.15)
    t_pic = time.time()
    c = await job(ctx, "/api/images", {"prompts": ["window three"], "expand": "off"})
    r = await t_ask
    log = k.log()[mark:]
    assert "session closed" in log and "(a GPU waiter" in log, log[-600:]
    assert r["t1"] - r["t0"] < IDLE + 1.5, f"the ask waited for the idle window ({r['t1'] - r['t0']:.2f} s)"
    assert c["status"] == "done" and len(k.launches()) == n_l + 2, "the picture behind the ask joined the closing session"
    assert c["t_done"] > r["t1"] - 0.05 and t_pic > r["t0"], ("FIFO: the picture behind the ask ran first", c["t_done"], r["t1"])
    assert len(k.llama_launches()) == n_ll + 1, "the ask needs exactly one LLM start"
    # (c) another model never joins
    await settle(ctx)
    d = await job(ctx, "/api/images", {"prompts": ["window four"], "expand": "off"})
    e = await job(ctx, "/api/images", {"prompts": ["window five"], "expand": "off", "model": "safe"})
    la = k.launches()
    assert d["status"] == e["status"] == "done" and len(la) == n_l + 4, la[n_l:]
    # [Muse 2026-10-10] default and safe share every file and carry no LoRA: two launches (counted above) = two sessions
    assert "--lora-model-dir" not in la[-2][1] and "--lora-model-dir" not in la[-1][1] \
        and "museByStableYogi_v35Q4Extended" in la[-2][1] and "museByStableYogi_v35Q4Extended" in la[-1][1], "safe = its own session, no LoRA"
    await settle(ctx)
    assert "session closed" in k.log() and not k.sd_server_running()
    st, s2 = await api(ctx, "GET", "/api/status")
    assert s2["state"] == "idle"
    return (f"batch 1 {lat_a:.2f} s, batch 2 joined (1 launch); ask closed the window (waiter), picture behind it "
            f"after the answer (new session); safe = own session; LLM started only by the ask")


@run_test("imaging_e2e_menubar_gpu_model", ["R80", "R79"], needs="http")
async def t_c5_gpu(ctx):
    """[R80] /api/status `gpu` follows what is resident: an LLM request -> the LLM's name; an image job ->
    'Muse' while it renders AND through the session's idle window (state "idle" meanwhile);
    the window closed -> none ('no model' in the menubar); the next LLM request -> the LLM's name again;
    the safe model -> 'Muse SFW'."""
    k = ctx["kernel"]
    await settle(ctx)

    async def gpu():
        _, st = await api(ctx, "GET", "/api/status")
        return st["gpu"], st["state"]
    await ask(ctx, "who is on the GPU?")
    g, _ = await gpu()
    assert g["kind"] == "llm" and g["name"] == "Gemma 4 26B-A4B-it ultra-uncensored heretic" and g["loading"] is False, g
    seen = []
    st, j = await api(ctx, "POST", "/api/images", {"prompts": ["a menubar orb"], "expand": "off"})
    assert st == 200, j
    while True:
        g, state = await gpu()
        seen.append((g["kind"], g.get("name"), state))
        _, d = await api(ctx, "GET", f"/api/job/{j['job']}")
        if d["status"] in ("done", "error"):
            break
        await asyncio.sleep(0.05)
    assert d["status"] == "done" and ("image", "Muse", "image") in seen, seen
    assert k.sd_server_running()
    g, state = await gpu()
    assert (g["kind"], g["name"], state) == ("image", "Muse", "idle"), ("the idle window must keep the image name", g, state)
    await settle(ctx)
    for _ in range(30):            # [2026-10-14] settle() sees the server's process gone; the kernel clears the
        g, _ = await gpu()          # GPU record right after its `await proc.wait()` resumes — a few ms later (a race
        if g["kind"] == "none":     # that showed under load, suite-2026-10-14-4): wait for the record, up to 3 s
            break
        await asyncio.sleep(0.1)
    assert g["kind"] == "none" and g["name"] is None, g
    await ask(ctx, "and now?")
    g, _ = await gpu()
    assert g["kind"] == "llm" and g["name"] == "Gemma 4 26B-A4B-it ultra-uncensored heretic", g
    names = set()
    st, j = await api(ctx, "POST", "/api/images", {"prompts": ["a stock orb"], "expand": "off", "model": "safe"})
    while True:
        g, _ = await gpu()
        names.add(g.get("name"))
        _, d = await api(ctx, "GET", f"/api/job/{j['job']}")
        if d["status"] in ("done", "error"):
            break
        await asyncio.sleep(0.05)
    g, _ = await gpu()
    names.add(g.get("name"))
    assert "Muse SFW" in names and "Muse" not in names, names
    await settle(ctx)
    return "LLM -> Gemma 4 26B-A4B-it; image job + idle window -> Muse (state idle in the window); none after; LLM again; safe -> Muse SFW"


# ============================================================================= main
async def main():
    only = [x.strip() for x in os.environ.get("KAIROS_TEST_ONLY", "").split(",") if x.strip()]
    tests = [t for t in TESTS if not only or any(o in t._name for o in only)]
    try:
        import aiohttp
    except ImportError:
        aiohttp = None
    print(f"kAIrOS imaging workflow tests [R79] — {len(tests)} tests" + ("" if aiohttp else " (no aiohttp: file tests only)"),
          flush=True)
    ctx = {"aiohttp": aiohttp}
    k = None
    try:
        if aiohttp:
            k = Kernel()
            k.setup()
            # the in-process kernel shares the private dir (settings, models) — never the C kernel's ports
            os.environ.update({kk: v for kk, v in k.env.items() if kk.startswith(("VIBEOS_", "KAIROS_LOG"))})
            os.environ.update(VIBEOS_LLAMA_PORT="8124", VIBEOS_TRANSLATE_PORT="8125", VIBEOS_SD_PORT="8126",
                              VIBEOS_PORT="1357", VIBEOS_SD_SESSION_IDLE="0", VIBEOS_SD_FADVISE="off")
            ctx["server"] = kernel_module()
            # the in-process tests run first (they patch module globals and restore them)
            for t in [t for t in tests if not t._name.startswith("imaging_e2e_")]:
                await t(ctx)
            k.start()
            ctx["kernel"] = k
            async with aiohttp.ClientSession() as s:
                ctx["session"] = s
                await k.wait(s)
                for t in [t for t in tests if t._name.startswith("imaging_e2e_")]:
                    await t(ctx)
        else:
            for t in tests:
                await t(ctx)
    finally:
        if k:
            if any(r["status"] == "fail" for r in RESULTS.values()):
                print("== kernel log tail ==\n" + k.log_tail(3000), flush=True)
            k.close()
    out = {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "tests": RESULTS}
    if not only:
        (REPO / "tests" / "results-imaging-workflow.json").write_text(json.dumps(out, indent=1))
    fails = [n for n, r in RESULTS.items() if r["status"] == "fail"]
    passes = sum(1 for r in RESULTS.values() if r["status"] == "pass")
    skips = sum(1 for r in RESULTS.values() if r["status"] == "skip")
    print(f"\n{passes} passed, {len(fails)} failed, {skips} skipped -> tests/results-imaging-workflow.json", flush=True)
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    asyncio.run(main())
